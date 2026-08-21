"""
HQ Billing — operator-side invoicing for tenant cooperatives.

Only active on the HQ instance (MARKETING_HQ=1). Lets the CoopMS operator keep a
registry of client cooperatives, generate invoices (per-user subscription,
mid-year top-ups for newly added members, and service fees like support,
migration and customization), and collect payment either online via Paystack or
by recording a manual bank transfer.

Design notes:
  * HQ has its own database, separate from each tenant, so a client's user count
    is stored here (operator-maintained) rather than synced live.
  * `billed_user_count` tracks how many users a client has been invoiced for in
    the current period, so a top-up bills only the delta when members are added.
  * Invoices carry line items (subscription / topup / service), so several
    charges sit on one bill.
  * These routes are guarded by a custom HQ-admin gate (env + admin role), not
    `role_required`, so HQ billing stays out of the per-coop Task Assignment
    catalogue (it is operator-only, not a delegated tenant duty).
"""

import base64
import io
import json
import os
import secrets
import urllib.request
from datetime import datetime, date, timedelta
from functools import wraps

from flask import (Blueprint, abort, current_app, flash, make_response,
                   redirect, render_template, request, url_for)
from flask_login import current_user, login_required

from database import get_db, last_insert_id
from email_service import send_email
from blueprints.marketing import marketing_hq_enabled
from payments import get_gateway, generate_reference
from utils import audit

hq_billing = Blueprint('hq_billing', __name__)

SERVICE_ITEM_TYPES = ('support', 'migration', 'customization', 'training', 'other')


# ── Gate ────────────────────────────────────────────────────────────────────

def _is_hq_admin() -> bool:
    return (current_user.is_authenticated
            and (getattr(current_user, 'role', '') == 'admin'
                 or getattr(current_user, 'is_super_admin', False)))


def hq_admin_required(f):
    """Operator-only: 404 off the HQ instance, login required, admin role."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not marketing_hq_enabled():
            abort(404)
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login', next=request.path))
        if not _is_hq_admin():
            abort(403)
        return f(*args, **kwargs)
    return wrapper


# ── Helpers ─────────────────────────────────────────────────────────────────

def _settings(db):
    return {r['key']: r['value'] for r in db.execute('SELECT key, value FROM settings').fetchall()}


def _operator_name(db):
    s = _settings(db)
    return (s.get('hq_business_name') or s.get('coop_name') or 'CoopMS').strip() or 'CoopMS'


def _billing_brand(db):
    """Invoice branding: business name, logo (data URI) and default payment
    instructions. Falls back to the instance's coop_name / coop_logo so it works
    before the operator fills in HQ-specific billing settings."""
    s = _settings(db)
    return {
        'name': (s.get('hq_business_name') or s.get('coop_name') or 'CoopMS').strip() or 'CoopMS',
        'logo': (s.get('hq_business_logo') or s.get('coop_logo') or '').strip(),
        'pay_instructions': (s.get('hq_payment_instructions') or '').strip(),
    }


def _client_base_url(client):
    """Resolve a client's API base from its code: a full URL as-is, a domain to
    https://<domain>, otherwise https://<code>.cooperativems.com."""
    code = (client['code'] or '').strip().lower()
    if not code:
        return ''
    if code.startswith('http://') or code.startswith('https://'):
        return code.rstrip('/')
    if '.' in code:
        return f'https://{code}'
    return f'https://{code}.cooperativems.com'


def _money(value):
    try:
        return f"{float(value or 0):,.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _next_invoice_number(db):
    year = datetime.now().year
    n = db.execute(
        "SELECT COUNT(*) FROM hq_invoices WHERE invoice_number LIKE ?",
        (f'INV/{year}/%',)).fetchone()[0]
    return f"INV/{year}/{n + 1:04d}"


def _invoice_with_items(db, invoice_id):
    inv = db.execute('''SELECT i.*, c.name AS client_name, c.billing_email, c.code AS client_code,
                               c.phone AS client_phone
                        FROM hq_invoices i JOIN hq_clients c ON c.id = i.client_id
                        WHERE i.id = ?''', (invoice_id,)).fetchone()
    if not inv:
        return None, []
    items = db.execute('SELECT * FROM hq_invoice_items WHERE invoice_id = ? ORDER BY id',
                       (invoice_id,)).fetchall()
    return inv, items


def _recalc_invoice_total(db, invoice_id):
    total = db.execute('SELECT COALESCE(SUM(amount), 0) FROM hq_invoice_items WHERE invoice_id = ?',
                       (invoice_id,)).fetchone()[0] or 0
    db.execute('UPDATE hq_invoices SET amount = ? WHERE id = ?', (round(float(total), 2), invoice_id))
    return round(float(total), 2)


def _release_billed_users(db, invoice):
    """Give back this invoice's subscription/top-up members to the client's
    billed_user_count, so a later top-up recomputes the right delta. Used when an
    invoice is voided or deleted."""
    q = db.execute("SELECT COALESCE(SUM(quantity), 0) FROM hq_invoice_items "
                   "WHERE invoice_id = ? AND item_type IN ('subscription','topup')",
                   (invoice['id'],)).fetchone()[0] or 0
    if q:
        cur = db.execute('SELECT billed_user_count FROM hq_clients WHERE id = ?',
                         (invoice['client_id'],)).fetchone()[0] or 0
        db.execute('UPDATE hq_clients SET billed_user_count = ?, updated_at = ? WHERE id = ?',
                   (max(0, int(cur - q)), datetime.now(), invoice['client_id']))


# ── Clients ───────────────────────────────────────────────────────────────────

@hq_billing.route('/hq/clients')
@hq_admin_required
def clients():
    db = get_db()
    rows = db.execute('''
        SELECT c.*,
               (SELECT COUNT(*) FROM hq_invoices i WHERE i.client_id = c.id) AS invoice_count,
               (SELECT COALESCE(SUM(amount), 0) FROM hq_invoices i
                 WHERE i.client_id = c.id AND i.status != 'void') AS billed_total,
               (SELECT COALESCE(SUM(amount), 0) FROM hq_invoices i
                 WHERE i.client_id = c.id AND i.status = 'paid') AS paid_total
        FROM hq_clients c
        ORDER BY c.status = 'active' DESC, c.name
    ''').fetchall()
    return render_template('hq/clients.html', clients=rows)


@hq_billing.route('/hq/clients', methods=['POST'])
@hq_admin_required
def add_client():
    db = get_db()
    name = (request.form.get('name') or '').strip()
    if not name:
        flash('Client name is required.', 'danger')
        return redirect(url_for('hq_billing.clients'))
    try:
        user_count = max(0, int(request.form.get('user_count') or 0))
    except ValueError:
        user_count = 0
    try:
        rate = float(request.form.get('rate_per_user') or 5000)
    except ValueError:
        rate = 5000.0
    db.execute('''INSERT INTO hq_clients
                    (name, code, billing_email, phone, user_count, billed_user_count,
                     rate_per_user, billing_cycle, period_start, period_end, status, notes)
                  VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, 'active', ?)''',
               (name, (request.form.get('code') or '').strip(),
                (request.form.get('billing_email') or '').strip(),
                (request.form.get('phone') or '').strip(),
                user_count, rate,
                (request.form.get('billing_cycle') or 'annual').strip(),
                (request.form.get('period_start') or '').strip() or None,
                (request.form.get('period_end') or '').strip() or None,
                (request.form.get('notes') or '').strip()))
    audit(db, 'HQ_CLIENT_ADD', 'hq_billing', f"Added client {name}")
    db.commit()
    flash(f'Client "{name}" added.', 'success')
    return redirect(url_for('hq_billing.clients'))


@hq_billing.route('/hq/clients/<int:client_id>/edit', methods=['POST'])
@hq_admin_required
def edit_client(client_id):
    db = get_db()
    c = db.execute('SELECT id FROM hq_clients WHERE id = ?', (client_id,)).fetchone()
    if not c:
        abort(404)
    try:
        user_count = max(0, int(request.form.get('user_count') or 0))
    except ValueError:
        user_count = 0
    try:
        rate = float(request.form.get('rate_per_user') or 5000)
    except ValueError:
        rate = 5000.0
    db.execute('''UPDATE hq_clients SET name = ?, code = ?, billing_email = ?, phone = ?,
                    user_count = ?, rate_per_user = ?, billing_cycle = ?, period_start = ?,
                    period_end = ?, status = ?, notes = ?, updated_at = ?
                  WHERE id = ?''',
               ((request.form.get('name') or '').strip(),
                (request.form.get('code') or '').strip(),
                (request.form.get('billing_email') or '').strip(),
                (request.form.get('phone') or '').strip(),
                user_count, rate,
                (request.form.get('billing_cycle') or 'annual').strip(),
                (request.form.get('period_start') or '').strip() or None,
                (request.form.get('period_end') or '').strip() or None,
                (request.form.get('status') or 'active').strip(),
                (request.form.get('notes') or '').strip(),
                datetime.now(), client_id))
    audit(db, 'HQ_CLIENT_EDIT', 'hq_billing', f"Edited client #{client_id}")
    db.commit()
    flash('Client updated.', 'success')
    return redirect(url_for('hq_billing.clients'))


@hq_billing.route('/hq/billing-settings', methods=['POST'])
@hq_admin_required
def billing_settings():
    """Save invoice branding: business name shown on invoices and the default
    payment instructions (e.g. bank details) appended to every invoice."""
    db = get_db()
    for key in ('hq_business_name', 'hq_payment_instructions'):
        val = (request.form.get(key) or '').strip()
        db.execute('DELETE FROM settings WHERE key = ?', (key,))
        if val:
            db.execute('INSERT INTO settings (key, value) VALUES (?, ?)', (key, val))
    audit(db, 'HQ_BILLING_SETTINGS', 'hq_billing', 'Updated invoice branding')
    db.commit()
    flash('Billing settings saved. The logo comes from Settings → your logo.', 'success')
    return redirect(url_for('hq_billing.invoices'))


@hq_billing.route('/hq/clients/sync-members', methods=['POST'])
@hq_admin_required
def sync_members():
    """Pull each active client's live active-member count from its own app and
    store it as user_count. Each tenant exposes GET /api/hq/member-count guarded
    by the shared HQ_SYNC_TOKEN."""
    db = get_db()
    token = (os.environ.get('HQ_SYNC_TOKEN') or '').strip()
    if not token:
        flash('Set HQ_SYNC_TOKEN in the HQ environment (and on each tenant) to enable syncing.', 'warning')
        return redirect(url_for('hq_billing.clients'))
    clients_ = db.execute("SELECT * FROM hq_clients WHERE status = 'active'").fetchall()
    ok, failed = 0, []
    for c in clients_:
        base = _client_base_url(c)
        if not base:
            failed.append(f"{c['name']} (no code)")
            continue
        try:
            req = urllib.request.Request(
                f'{base}/api/hq/member-count',
                headers={'X-HQ-Token': token, 'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            count = int(data.get('active_members'))
            db.execute('UPDATE hq_clients SET user_count = ?, updated_at = ? WHERE id = ?',
                       (count, datetime.now(), c['id']))
            ok += 1
        except Exception as exc:  # pragma: no cover - network
            current_app.logger.warning('HQ member sync failed for %s: %s', c['name'], exc)
            failed.append(c['name'])
    audit(db, 'HQ_MEMBER_SYNC', 'hq_billing', f"Synced {ok} client(s); {len(failed)} failed")
    db.commit()
    msg = f'Updated user counts for {ok} client(s).'
    if failed:
        msg += f' Could not reach: {", ".join(failed[:8])}.'
    flash(msg, 'success' if ok else 'warning')
    return redirect(url_for('hq_billing.clients'))


# ── Invoices ──────────────────────────────────────────────────────────────────

@hq_billing.route('/hq/invoices')
@hq_admin_required
def invoices():
    db = get_db()
    status = (request.args.get('status') or '').strip()
    where, params = '', []
    if status in ('draft', 'sent', 'paid', 'void'):
        where = 'WHERE i.status = ?'
        params = [status]
    rows = db.execute(f'''
        SELECT i.*, c.name AS client_name
        FROM hq_invoices i JOIN hq_clients c ON c.id = i.client_id
        {where}
        ORDER BY i.issue_date DESC, i.id DESC
    ''', params).fetchall()
    totals = db.execute('''
        SELECT
          COALESCE(SUM(CASE WHEN status != 'void' THEN amount END), 0) AS billed,
          COALESCE(SUM(CASE WHEN status = 'paid' THEN amount END), 0) AS collected,
          COALESCE(SUM(CASE WHEN status IN ('draft','sent') THEN amount END), 0) AS outstanding
        FROM hq_invoices
    ''').fetchone()
    clients_ = db.execute("SELECT * FROM hq_clients WHERE status = 'active' ORDER BY name").fetchall()
    return render_template('hq/invoices.html', invoices=rows, totals=totals,
                           clients=clients_, active_status=status, brand=_billing_brand(db),
                           default_due=(date.today() + timedelta(days=14)).isoformat())


def _create_invoice(db, client, period_label, due_date, notes,
                    sub_mode, sub_qty, sub_unit, service_lines):
    """Build one invoice + its line items. Returns (invoice_id, total).
    sub_mode: 'none' | 'full' | 'topup'. service_lines: list of (type, desc, amount)."""
    invoice_number = _next_invoice_number(db)
    token = secrets.token_urlsafe(16)
    db.execute('''INSERT INTO hq_invoices
                    (invoice_number, client_id, period_label, due_date, amount, status,
                     pay_token, notes, created_by)
                  VALUES (?, ?, ?, ?, 0, 'draft', ?, ?, ?)''',
               (invoice_number, client['id'], period_label, due_date or None,
                token, notes, current_user.id))
    invoice_id = last_insert_id(db)

    if sub_mode in ('full', 'topup') and sub_qty > 0:
        label = ('Annual subscription' if client['billing_cycle'] == 'annual' else 'Subscription')
        desc = (f"{label} — {sub_qty} member(s)" if sub_mode == 'full'
                else f"Additional members (top-up) — {sub_qty} member(s)")
        line_amt = round(sub_qty * sub_unit, 2)
        db.execute('''INSERT INTO hq_invoice_items
                        (invoice_id, item_type, description, quantity, unit_price, amount)
                      VALUES (?, ?, ?, ?, ?, ?)''',
                   (invoice_id, 'subscription' if sub_mode == 'full' else 'topup',
                    desc, sub_qty, sub_unit, line_amt))
        # Advance the billed counter so the next top-up only charges the new delta.
        db.execute('UPDATE hq_clients SET billed_user_count = ?, updated_at = ? WHERE id = ?',
                   (client['billed_user_count'] + sub_qty if sub_mode == 'topup' else sub_qty,
                    datetime.now(), client['id']))

    for (itype, desc, amount) in service_lines:
        db.execute('''INSERT INTO hq_invoice_items
                        (invoice_id, item_type, description, quantity, unit_price, amount)
                      VALUES (?, 'service', ?, 1, ?, ?)''',
                   (invoice_id, f"{itype.title()}: {desc}" if desc else itype.title(),
                    amount, amount))

    total = _recalc_invoice_total(db, invoice_id)
    return invoice_id, total


@hq_billing.route('/hq/invoices/new', methods=['POST'])
@hq_admin_required
def new_invoice():
    db = get_db()
    client = db.execute('SELECT * FROM hq_clients WHERE id = ?',
                        (request.form.get('client_id'),)).fetchone()
    if not client:
        flash('Choose a client to invoice.', 'danger')
        return redirect(url_for('hq_billing.invoices'))

    period_label = (request.form.get('period_label') or '').strip()
    due_date = (request.form.get('due_date') or '').strip() or None
    notes = (request.form.get('notes') or '').strip()
    sub_mode = (request.form.get('sub_mode') or 'none').strip()

    # Subscription / top-up quantity + unit price.
    try:
        sub_unit = float(request.form.get('sub_unit') or client['rate_per_user'] or 0)
    except ValueError:
        sub_unit = float(client['rate_per_user'] or 0)
    if sub_mode == 'full':
        sub_qty = int(request.form.get('sub_qty') or client['user_count'] or 0)
    elif sub_mode == 'topup':
        default_delta = max(0, int(client['user_count'] or 0) - int(client['billed_user_count'] or 0))
        try:
            sub_qty = int(request.form.get('sub_qty') or default_delta)
        except ValueError:
            sub_qty = default_delta
    else:
        sub_qty = 0

    # Service-fee line items (parallel arrays from the form).
    service_lines = []
    types = request.form.getlist('service_type')
    descs = request.form.getlist('service_desc')
    amounts = request.form.getlist('service_amount')
    for i, itype in enumerate(types):
        amt_raw = amounts[i] if i < len(amounts) else ''
        desc = descs[i] if i < len(descs) else ''
        try:
            amt = float(amt_raw)
        except (ValueError, TypeError):
            continue
        if amt <= 0:
            continue
        itype = itype if itype in SERVICE_ITEM_TYPES else 'other'
        service_lines.append((itype, desc.strip(), round(amt, 2)))

    if sub_qty <= 0 and not service_lines:
        flash('Nothing to invoice — add a subscription/top-up or at least one service fee.', 'warning')
        return redirect(url_for('hq_billing.invoices'))

    invoice_id, total = _create_invoice(db, client, period_label, due_date, notes,
                                        sub_mode, sub_qty, sub_unit, service_lines)
    audit(db, 'HQ_INVOICE_CREATE', 'hq_billing',
          f"Invoice for {client['name']}: NGN {_money(total)}")
    db.commit()
    flash(f'Invoice created for {client["name"]} — ₦{_money(total)}.', 'success')
    return redirect(url_for('hq_billing.invoice_detail', invoice_id=invoice_id))


@hq_billing.route('/hq/invoices/bulk-subscription', methods=['POST'])
@hq_admin_required
def bulk_subscription():
    """Generate a full-subscription invoice for every active client at once."""
    db = get_db()
    period_label = (request.form.get('period_label') or '').strip()
    due_date = (request.form.get('due_date') or '').strip() or None
    clients_ = db.execute(
        "SELECT * FROM hq_clients WHERE status = 'active' AND user_count > 0").fetchall()
    made = 0
    for client in clients_:
        _create_invoice(db, client, period_label, due_date, '', 'full',
                        int(client['user_count'] or 0), float(client['rate_per_user'] or 0), [])
        made += 1
    audit(db, 'HQ_INVOICE_BULK', 'hq_billing',
          f"Bulk subscription invoices for {made} client(s), period {period_label}")
    db.commit()
    if made:
        flash(f'Generated {made} subscription invoice(s).', 'success')
    else:
        flash('No active clients with a user count to invoice.', 'warning')
    return redirect(url_for('hq_billing.invoices'))


@hq_billing.route('/hq/invoices/<int:invoice_id>')
@hq_admin_required
def invoice_detail(invoice_id):
    db = get_db()
    inv, items = _invoice_with_items(db, invoice_id)
    if not inv:
        abort(404)
    return render_template('hq/invoice-detail.html', inv=inv, items=items)


@hq_billing.route('/hq/invoices/<int:invoice_id>/edit', methods=['POST'])
@hq_admin_required
def edit_invoice(invoice_id):
    """Edit a DRAFT invoice: change/delete existing line items, edit their qty and
    unit price, add service fees, and edit period/due/notes. Sent, paid or void
    invoices are locked. Subscription/top-up quantity changes adjust the client's
    billed_user_count so future top-ups stay correct."""
    db = get_db()
    inv = db.execute('SELECT * FROM hq_invoices WHERE id = ?', (invoice_id,)).fetchone()
    if not inv:
        abort(404)
    if inv['status'] != 'draft':
        flash('Only draft invoices can be edited.', 'warning')
        return redirect(url_for('hq_billing.invoice_detail', invoice_id=invoice_id))

    def _sub_qty():
        return db.execute("SELECT COALESCE(SUM(quantity), 0) FROM hq_invoice_items "
                          "WHERE invoice_id = ? AND item_type IN ('subscription','topup')",
                          (invoice_id,)).fetchone()[0] or 0
    old_sub = _sub_qty()

    delete_ids = set(request.form.getlist('delete_ids'))
    ids = request.form.getlist('item_id')
    descs = request.form.getlist('item_desc')
    qtys = request.form.getlist('item_qty')
    units = request.form.getlist('item_unit')
    for i, iid in enumerate(ids):
        if iid in delete_ids:
            db.execute('DELETE FROM hq_invoice_items WHERE id = ? AND invoice_id = ?', (iid, invoice_id))
            continue
        try:
            q = float(qtys[i]); u = float(units[i])
        except (ValueError, IndexError):
            continue
        db.execute('UPDATE hq_invoice_items SET description = ?, quantity = ?, unit_price = ?, amount = ? '
                   'WHERE id = ? AND invoice_id = ?',
                   ((descs[i] if i < len(descs) else '').strip(), q, u, round(q * u, 2), iid, invoice_id))

    ntypes = request.form.getlist('new_type')
    ndescs = request.form.getlist('new_desc')
    namounts = request.form.getlist('new_amount')
    for i, itype in enumerate(ntypes):
        try:
            amt = float(namounts[i])
        except (ValueError, IndexError):
            continue
        if amt <= 0:
            continue
        desc = (ndescs[i] if i < len(ndescs) else '').strip()
        itype = itype if itype in SERVICE_ITEM_TYPES else 'other'
        label = f"{itype.title()}: {desc}" if desc else itype.title()
        db.execute("INSERT INTO hq_invoice_items (invoice_id, item_type, description, quantity, unit_price, amount) "
                   "VALUES (?, 'service', ?, 1, ?, ?)", (invoice_id, label, round(amt, 2), round(amt, 2)))

    new_sub = _sub_qty()
    if new_sub != old_sub:
        cur = db.execute('SELECT billed_user_count FROM hq_clients WHERE id = ?',
                         (inv['client_id'],)).fetchone()[0] or 0
        db.execute('UPDATE hq_clients SET billed_user_count = ?, updated_at = ? WHERE id = ?',
                   (max(0, int(cur + (new_sub - old_sub))), datetime.now(), inv['client_id']))

    db.execute('UPDATE hq_invoices SET period_label = ?, due_date = ?, notes = ? WHERE id = ?',
               ((request.form.get('period_label') or '').strip(),
                (request.form.get('due_date') or '').strip() or None,
                (request.form.get('notes') or '').strip(), invoice_id))
    total = _recalc_invoice_total(db, invoice_id)
    audit(db, 'HQ_INVOICE_EDIT', 'hq_billing', f"Edited draft {inv['invoice_number']}: NGN {_money(total)}")
    db.commit()
    flash(f'Invoice updated — new total ₦{_money(total)}.', 'success')
    return redirect(url_for('hq_billing.invoice_detail', invoice_id=invoice_id))


@hq_billing.route('/hq/invoices/<int:invoice_id>/duplicate', methods=['POST'])
@hq_admin_required
def duplicate_invoice(invoice_id):
    """Clone an invoice's line items into a new DRAFT — e.g. reuse last period's
    invoice for the next one. The copy gets a fresh number, pay token and no due
    date; edit the period and details before sending. Subscription/top-up members
    are applied to billed_user_count the same way creating the invoice fresh would."""
    db = get_db()
    src, items = _invoice_with_items(db, invoice_id)
    if not src:
        abort(404)
    number = _next_invoice_number(db)
    db.execute('''INSERT INTO hq_invoices
                    (invoice_number, client_id, period_label, due_date, amount, status,
                     pay_token, notes, created_by)
                  VALUES (?, ?, ?, NULL, 0, 'draft', ?, ?, ?)''',
               (number, src['client_id'], src['period_label'],
                secrets.token_urlsafe(16), src['notes'], current_user.id))
    new_id = last_insert_id(db)
    for it in items:
        db.execute('''INSERT INTO hq_invoice_items
                        (invoice_id, item_type, description, quantity, unit_price, amount)
                      VALUES (?, ?, ?, ?, ?, ?)''',
                   (new_id, it['item_type'], it['description'],
                    it['quantity'], it['unit_price'], it['amount']))
    # Keep billed_user_count consistent with a fresh creation (full sets, top-up adds).
    full_qty = sum(float(it['quantity'] or 0) for it in items if it['item_type'] == 'subscription')
    topup_qty = sum(float(it['quantity'] or 0) for it in items if it['item_type'] == 'topup')
    if full_qty > 0:
        db.execute('UPDATE hq_clients SET billed_user_count = ?, updated_at = ? WHERE id = ?',
                   (int(full_qty + topup_qty), datetime.now(), src['client_id']))
    elif topup_qty > 0:
        cur = db.execute('SELECT billed_user_count FROM hq_clients WHERE id = ?',
                         (src['client_id'],)).fetchone()[0] or 0
        db.execute('UPDATE hq_clients SET billed_user_count = ?, updated_at = ? WHERE id = ?',
                   (int(cur + topup_qty), datetime.now(), src['client_id']))
    total = _recalc_invoice_total(db, new_id)
    audit(db, 'HQ_INVOICE_DUPLICATE', 'hq_billing',
          f"Duplicated {src['invoice_number']} -> {number} (NGN {_money(total)})")
    db.commit()
    flash(f'Created draft {number} from {src["invoice_number"]}. Set the new period and due date, then send.', 'success')
    return redirect(url_for('hq_billing.invoice_detail', invoice_id=new_id))


@hq_billing.route('/hq/invoices/<int:invoice_id>/mark-paid', methods=['POST'])
@hq_admin_required
def mark_paid(invoice_id):
    db = get_db()
    inv = db.execute('SELECT * FROM hq_invoices WHERE id = ?', (invoice_id,)).fetchone()
    if not inv:
        abort(404)
    if inv['status'] == 'paid':
        flash('This invoice is already paid.', 'info')
        return redirect(url_for('hq_billing.invoice_detail', invoice_id=invoice_id))
    ref = (request.form.get('reference') or '').strip() or f'MANUAL-{invoice_id}'
    db.execute('''UPDATE hq_invoices SET status = 'paid', paid_at = ?, paid_method = 'manual',
                    payment_reference = ? WHERE id = ?''', (datetime.now(), ref, invoice_id))
    audit(db, 'HQ_INVOICE_PAID_MANUAL', 'hq_billing',
          f"Invoice {inv['invoice_number']} marked paid (manual, ref {ref})")
    db.commit()
    flash('Invoice marked as paid.', 'success')
    return redirect(url_for('hq_billing.invoice_detail', invoice_id=invoice_id))


@hq_billing.route('/hq/invoices/<int:invoice_id>/void', methods=['POST'])
@hq_admin_required
def void_invoice(invoice_id):
    db = get_db()
    inv = db.execute('SELECT * FROM hq_invoices WHERE id = ?', (invoice_id,)).fetchone()
    if not inv:
        abort(404)
    if inv['status'] == 'paid':
        flash('A paid invoice cannot be voided.', 'danger')
        return redirect(url_for('hq_billing.invoice_detail', invoice_id=invoice_id))
    _release_billed_users(db, inv)
    db.execute("UPDATE hq_invoices SET status = 'void' WHERE id = ?", (invoice_id,))
    audit(db, 'HQ_INVOICE_VOID', 'hq_billing', f"Voided invoice {inv['invoice_number']}")
    db.commit()
    flash('Invoice voided.', 'info')
    return redirect(url_for('hq_billing.invoice_detail', invoice_id=invoice_id))


@hq_billing.route('/hq/invoices/<int:invoice_id>/delete', methods=['POST'])
@hq_admin_required
def delete_invoice(invoice_id):
    """Permanently delete an invoice and its line items. Releases its
    subscription/top-up members back to the client (unless already voided, which
    released them). Irreversible — for correcting mistakes, not record-keeping."""
    db = get_db()
    inv = db.execute('SELECT * FROM hq_invoices WHERE id = ?', (invoice_id,)).fetchone()
    if not inv:
        abort(404)
    if inv['status'] != 'void':
        _release_billed_users(db, inv)
    db.execute('DELETE FROM hq_invoice_items WHERE invoice_id = ?', (invoice_id,))
    db.execute('DELETE FROM hq_invoices WHERE id = ?', (invoice_id,))
    audit(db, 'HQ_INVOICE_DELETE', 'hq_billing',
          f"Deleted invoice {inv['invoice_number']} (was {inv['status']}, NGN {_money(inv['amount'])})")
    db.commit()
    flash(f'Invoice {inv["invoice_number"]} deleted.', 'info')
    return redirect(url_for('hq_billing.invoices'))


@hq_billing.route('/hq/invoices/<int:invoice_id>.pdf')
@hq_admin_required
def invoice_pdf(invoice_id):
    db = get_db()
    inv, items = _invoice_with_items(db, invoice_id)
    if not inv:
        abort(404)
    pdf = _build_invoice_pdf(_billing_brand(db), inv, items)
    resp = make_response(pdf)
    resp.headers['Content-Type'] = 'application/pdf'
    resp.headers['Content-Disposition'] = \
        f'inline; filename=invoice_{inv["invoice_number"].replace("/", "_")}.pdf'
    return resp


@hq_billing.route('/hq/invoices/<int:invoice_id>/send', methods=['POST'])
@hq_admin_required
def send_invoice(invoice_id):
    db = get_db()
    inv, items = _invoice_with_items(db, invoice_id)
    if not inv:
        abort(404)
    if not inv['billing_email']:
        flash('This client has no billing email — add one first.', 'danger')
        return redirect(url_for('hq_billing.invoice_detail', invoice_id=invoice_id))

    pay_url = url_for('hq_billing.pay_invoice', invoice_number=inv['invoice_number'],
                      token=inv['pay_token'], _external=True)
    brand = _billing_brand(db)
    operator = brand['name']
    rows_html = ''.join(
        f"<tr><td style='padding:6px 10px;border-bottom:1px solid #eee'>{it['description']}</td>"
        f"<td style='padding:6px 10px;border-bottom:1px solid #eee;text-align:right'>₦{_money(it['amount'])}</td></tr>"
        for it in items)
    html = f"""
      <h2>Invoice {inv['invoice_number']}</h2>
      <p>Dear {inv['client_name']},</p>
      <p>Please find your invoice from {operator}{' for ' + inv['period_label'] if inv['period_label'] else ''}.</p>
      <table style="border-collapse:collapse;width:100%;max-width:520px">
        {rows_html}
        <tr><td style="padding:8px 10px;font-weight:bold">Total due</td>
            <td style="padding:8px 10px;text-align:right;font-weight:bold">₦{_money(inv['amount'])}</td></tr>
      </table>
      <p>{'Due by ' + str(inv['due_date']) + '.' if inv['due_date'] else ''}</p>
      <p><a href="{pay_url}" style="display:inline-block;background:#082B66;color:#fff;
        padding:12px 22px;border-radius:6px;text-decoration:none;font-weight:bold">Pay now</a></p>
      <p style="color:#666;font-size:13px">Or pay by bank transfer and we will mark this invoice settled.</p>
      {('<p style="color:#444;font-size:13px;white-space:pre-line">' + brand['pay_instructions'] + '</p>') if brand['pay_instructions'] else ''}
    """
    try:
        pdf = _build_invoice_pdf(brand, inv, items)
        attachments = [{'filename': f"invoice_{inv['invoice_number'].replace('/', '_')}.pdf",
                        'content': pdf, 'mimetype': 'application/pdf'}]
    except Exception:  # pragma: no cover - PDF is best-effort; still send the email
        attachments = None
    sent_ok = send_email(inv['billing_email'], f"Invoice {inv['invoice_number']} from {operator}",
                         html, attachments=attachments)
    if sent_ok:
        new_status = 'sent' if inv['status'] == 'draft' else inv['status']
        db.execute('UPDATE hq_invoices SET status = ?, sent_at = ? WHERE id = ?',
                   (new_status, datetime.now(), invoice_id))
        audit(db, 'HQ_INVOICE_SENT', 'hq_billing',
              f"Invoice {inv['invoice_number']} emailed to {inv['billing_email']}")
        db.commit()
        flash(f'Invoice emailed to {inv["billing_email"]} with the PDF attached.', 'success')
    else:
        flash('Could not send the email — check Settings → Email is configured and enabled.', 'danger')
    return redirect(url_for('hq_billing.invoice_detail', invoice_id=invoice_id))


# ── Public payment (client-facing; no login, token-guarded) ───────────────────

@hq_billing.route('/hq/pay/<path:invoice_number>/<token>')
def pay_invoice(invoice_number, token):
    if not marketing_hq_enabled():
        abort(404)
    db = get_db()
    inv = db.execute('''SELECT i.*, c.billing_email FROM hq_invoices i
                        JOIN hq_clients c ON c.id = i.client_id
                        WHERE i.invoice_number = ?''', (invoice_number,)).fetchone()
    if not inv or not inv['pay_token'] or not secrets.compare_digest(token, inv['pay_token']):
        abort(404)
    if inv['status'] == 'paid':
        return render_template('hq/pay-result.html', ok=True, already=True, inv=inv)
    if inv['status'] == 'void':
        abort(404)

    settings = _settings(db)
    if (settings.get('active_gateway') or 'paystack') != 'paystack' or not settings.get('paystack_secret_key'):
        return render_template('hq/pay-result.html', ok=False, already=False, inv=inv,
                               message='Online payment is not configured. Please pay by bank transfer.')

    reference = generate_reference('HQINV')
    callback_url = url_for('hq_billing.pay_callback', _external=True)
    db.execute('UPDATE hq_invoices SET payment_reference = ? WHERE id = ?', (reference, inv['id']))
    db.commit()
    try:
        gw = get_gateway('paystack')
        resp = gw.initialize(inv['billing_email'] or 'billing@coopms.local',
                             float(inv['amount']), reference, callback_url,
                             metadata={'invoice_id': inv['id'], 'invoice_number': invoice_number})
        if resp.get('status'):
            return redirect(resp['data']['authorization_url'])
    except Exception as exc:  # pragma: no cover - network
        current_app.logger.error('HQ invoice pay init error: %s', exc)
    return render_template('hq/pay-result.html', ok=False, already=False, inv=inv,
                           message='Could not reach the payment gateway. Please try again shortly.')


@hq_billing.route('/hq/pay/callback')
def pay_callback():
    if not marketing_hq_enabled():
        abort(404)
    db = get_db()
    reference = (request.args.get('reference') or '').strip()
    inv = db.execute('SELECT * FROM hq_invoices WHERE payment_reference = ?', (reference,)).fetchone()
    if not reference or not inv:
        abort(404)
    if inv['status'] == 'paid':
        return render_template('hq/pay-result.html', ok=True, already=True, inv=inv)
    try:
        resp = get_gateway('paystack').verify(reference)
    except Exception as exc:  # pragma: no cover - network
        current_app.logger.error('HQ invoice verify error: %s', exc)
        return render_template('hq/pay-result.html', ok=False, already=False, inv=inv,
                               message='We could not confirm the payment yet. If you were charged, contact us.')
    ok = bool(resp.get('status')) and (resp.get('data', {}).get('status') == 'success')
    if ok and (resp['data'].get('amount', 0) // 100) >= int(float(inv['amount'])):
        db.execute("UPDATE hq_invoices SET status = 'paid', paid_at = ?, paid_method = 'paystack' "
                   "WHERE id = ?", (datetime.now(), inv['id']))
        audit(db, 'HQ_INVOICE_PAID_ONLINE', 'hq_billing',
              f"Invoice {inv['invoice_number']} paid online (ref {reference})")
        db.commit()
        return render_template('hq/pay-result.html', ok=True, already=False, inv=inv)
    return render_template('hq/pay-result.html', ok=False, already=False, inv=inv,
                           message='Payment was not completed. You have not been charged for a failed attempt.')


# ── PDF ───────────────────────────────────────────────────────────────────────

def _logo_flowable(logo_uri, mm):
    """Turn a data: URI logo into a sized reportlab Image, or None."""
    if not logo_uri or not logo_uri.startswith('data:') or ',' not in logo_uri:
        return None
    try:
        from reportlab.platypus import Image as RLImage
        raw = base64.b64decode(logo_uri.split(',', 1)[1])
        img = RLImage(io.BytesIO(raw))
        max_w = 42 * mm
        if img.imageWidth:
            img.drawWidth = max_w
            img.drawHeight = max_w * (img.imageHeight / float(img.imageWidth))
        return img
    except Exception:
        return None


def _build_invoice_pdf(brand, inv, items) -> bytes:
    """Render a simple, self-contained invoice PDF with the operator's logo and
    business name. Uses 'NGN' (Helvetica has no naira glyph)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle)
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=20 * mm, bottomMargin=20 * mm,
                            leftMargin=18 * mm, rightMargin=18 * mm, title=inv['invoice_number'])
    styles = getSampleStyleSheet()
    h = ParagraphStyle('h', parent=styles['Title'], fontSize=20, alignment=0,
                       textColor=colors.HexColor('#082B66'))
    small = ParagraphStyle('small', parent=styles['Normal'], fontSize=9, textColor=colors.HexColor('#555'))
    story = []
    logo = _logo_flowable(brand.get('logo'), mm)
    if logo is not None:
        logo.hAlign = 'LEFT'
        story += [logo, Spacer(1, 4 * mm)]
    story += [Paragraph(brand.get('name') or 'Invoice', h),
              Paragraph('INVOICE', ParagraphStyle('t', parent=styles['Heading2'])),
              Spacer(1, 6 * mm)]

    meta = [
        [Paragraph('<b>Invoice</b>', small), Paragraph(inv['invoice_number'], small),
         Paragraph('<b>Bill to</b>', small), Paragraph(inv['client_name'] or '', small)],
        [Paragraph('<b>Issue date</b>', small), Paragraph(str(inv['issue_date'])[:10], small),
         Paragraph('<b>Email</b>', small), Paragraph(inv['billing_email'] or '-', small)],
        [Paragraph('<b>Due date</b>', small), Paragraph(str(inv['due_date'] or '-'), small),
         Paragraph('<b>Period</b>', small), Paragraph(inv['period_label'] or '-', small)],
    ]
    mt = Table(meta, colWidths=[24 * mm, 55 * mm, 22 * mm, 53 * mm])
    mt.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'),
                            ('BOTTOMPADDING', (0, 0), (-1, -1), 4)]))
    story += [mt, Spacer(1, 8 * mm)]

    data = [['Description', 'Qty', 'Unit (NGN)', 'Amount (NGN)']]
    for it in items:
        data.append([it['description'] or '',
                     f"{float(it['quantity'] or 0):g}",
                     f"{float(it['unit_price'] or 0):,.2f}",
                     f"{float(it['amount'] or 0):,.2f}"])
    data.append(['', '', 'Total', f"{float(inv['amount'] or 0):,.2f}"])
    t = Table(data, colWidths=[95 * mm, 18 * mm, 20 * mm, 21 * mm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#082B66')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
        ('LINEBELOW', (0, 1), (-1, -2), 0.4, colors.HexColor('#dddddd')),
        ('LINEABOVE', (0, -1), (-1, -1), 0.8, colors.HexColor('#082B66')),
        ('FONTNAME', (2, -1), (-1, -1), 'Helvetica-Bold'),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    story += [t, Spacer(1, 8 * mm)]
    status = (inv['status'] or 'draft').upper()
    story.append(Paragraph(f"Status: <b>{status}</b>", small))
    if inv['notes']:
        story += [Spacer(1, 4 * mm), Paragraph(inv['notes'], small)]
    if brand.get('pay_instructions'):
        story += [Spacer(1, 4 * mm),
                  Paragraph(brand['pay_instructions'].replace('\n', '<br/>'), small)]
    doc.build(story)
    return buf.getvalue()
