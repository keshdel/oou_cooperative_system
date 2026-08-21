"""
CTAS — Cooperative Target Advance Scheme.

A balloted target-advance (ajo/esusu) module: members subscribe to a cycle, a
seeded ballot assigns each a payout month, they receive a lump-sum advance, and
it is recovered via monthly payroll deductions. Money posts to the real
double-entry GL (CTAS Advances Receivable + CTAS Admin Fee Income) and reuses
CoopMS members.

**Optional feature.** Off by default and leaves no footprint (no menu, no
routes, no GL accounts) until a cooperative is activated — on request — either by
the operator from HQ (POST /api/hq/set-feature) or by a super admin here. Gate
every CTAS view with @ctas_required.
"""

import secrets
from datetime import datetime
from functools import wraps

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from database import get_db, last_insert_id
from utils import audit, role_required
from ledger import post_journal_safe, get_default_cash_account
import ctas_engine as ce

ctas = Blueprint('ctas', __name__)

CTAS_ADVANCES = '1150'            # asset — advances paid out, recovered via payroll
CTAS_ADMIN_FEE_INCOME = '4150'   # income — admin fee charged on subscription


def ctas_enabled(db=None) -> bool:
    """True if this cooperative has the CTAS add-on switched on."""
    try:
        db = db or get_db()
        row = db.execute("SELECT value FROM settings WHERE key = 'ctas_enabled'").fetchone()
        return bool(row and str(row['value']) == '1')
    except Exception:
        return False


def ctas_ensure_accounts(db) -> None:
    """Seed the two CTAS GL accounts — only when the feature is turned on, so a
    coop that never uses CTAS keeps a clean chart of accounts."""
    for code, name, atype, normal in (
        (CTAS_ADVANCES, 'CTAS Advances Receivable', 'asset', 'debit'),
        (CTAS_ADMIN_FEE_INCOME, 'CTAS Admin Fee Income', 'income', 'credit'),
    ):
        try:
            db.execute(
                "INSERT INTO accounts (code, name, type, normal_balance, parent_code) "
                "VALUES (?, ?, ?, ?, NULL) ON CONFLICT(code) DO NOTHING",
                (code, name, atype, normal))
        except Exception:
            pass


def set_ctas_enabled(db, on: bool) -> None:
    """Turn the CTAS add-on on/off for this cooperative (seeds accounts on-on)."""
    db.execute("DELETE FROM settings WHERE key = 'ctas_enabled'")
    db.execute("INSERT INTO settings (key, value) VALUES ('ctas_enabled', ?)", ('1' if on else '0',))
    if on:
        ctas_ensure_accounts(db)


def ctas_required(f):
    """Feature gate: 404 unless CTAS is enabled here; then require login."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not ctas_enabled():
            abort(404)
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login', next=request.path))
        return f(*args, **kwargs)
    return wrapper


def _is_operator() -> bool:
    return current_user.is_authenticated and getattr(current_user, 'is_super_admin', False)


@ctas.route('/ctas/enable', methods=['POST'])
@login_required
def enable_ctas():
    """Activate CTAS for this cooperative — operator/super-admin only."""
    if not _is_operator():
        abort(403)
    db = get_db()
    set_ctas_enabled(db, True)
    audit(db, 'CTAS_ENABLED', 'ctas', 'Target Advance Scheme enabled')
    db.commit()
    flash('CTAS (Target Advance Scheme) is now enabled.', 'success')
    return redirect(request.referrer or url_for('main.dashboard'))


@ctas.route('/ctas/disable', methods=['POST'])
@login_required
def disable_ctas():
    if not _is_operator():
        abort(403)
    db = get_db()
    set_ctas_enabled(db, False)
    audit(db, 'CTAS_DISABLED', 'ctas', 'Target Advance Scheme disabled')
    db.commit()
    flash('CTAS has been disabled.', 'info')
    return redirect(request.referrer or url_for('main.dashboard'))


# ── Admin: cycles ─────────────────────────────────────────────────────────────

@ctas.route('/ctas')
@ctas_required
@role_required('admin', 'treasurer')
def dashboard():
    db = get_db()
    cycles = db.execute('''
        SELECT c.*,
               (SELECT COUNT(*) FROM ctas_subscriptions s WHERE s.cycle_id = c.id) AS sub_count,
               (SELECT COUNT(*) FROM ctas_subscriptions s WHERE s.cycle_id = c.id AND s.status = 'enrolled') AS enrolled_count
        FROM ctas_cycles c ORDER BY c.created_at DESC, c.id DESC
    ''').fetchall()
    return render_template('ctas/cycles.html', cycles=cycles)


@ctas.route('/ctas/cycles', methods=['POST'])
@ctas_required
@role_required('admin', 'treasurer')
def new_cycle():
    db = get_db()
    name = (request.form.get('name') or '').strip()
    if not name:
        flash('Cycle name is required.', 'danger')
        return redirect(url_for('ctas.dashboard'))

    def _num(key, default):
        try:
            return float(request.form.get(key) or default)
        except ValueError:
            return float(default)

    db.execute('''INSERT INTO ctas_cycles
            (name, status, start_date, end_date, duration_months, fixed_monthly_amount,
             monthly_capacity, earliest_payout_month, max_participants,
             admin_fee_flat, admin_fee_percentage, admin_fee_cap, admin_fee_threshold,
             ballot_date, affordability_method, affordability_ratio, savings_multiple, created_by)
            VALUES (?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (name, (request.form.get('start_date') or '').strip() or None,
         (request.form.get('end_date') or '').strip() or None,
         int(_num('duration_months', 6)), _num('fixed_monthly_amount', 0),
         int(_num('monthly_capacity', 1)), int(_num('earliest_payout_month', 2)),
         int(_num('max_participants', 0)),
         _num('admin_fee_flat', 0), _num('admin_fee_percentage', 0),
         _num('admin_fee_cap', 0), _num('admin_fee_threshold', 0),
         (request.form.get('ballot_date') or '').strip() or None,
         (request.form.get('affordability_method') or 'savings').strip(),
         _num('affordability_ratio', 0.5), _num('savings_multiple', 3), current_user.id))
    cycle_id = last_insert_id(db)   # capture before the audit insert changes last-rowid
    audit(db, 'CTAS_CYCLE_CREATE', 'ctas', f"Created cycle {name}")
    db.commit()
    flash(f'Cycle "{name}" created (draft). Open it to start enrolment.', 'success')
    return redirect(url_for('ctas.cycle_detail', cycle_id=cycle_id))


@ctas.route('/ctas/cycles/<int:cycle_id>')
@ctas_required
@role_required('admin', 'treasurer')
def cycle_detail(cycle_id):
    db = get_db()
    cycle = db.execute('SELECT * FROM ctas_cycles WHERE id = ?', (cycle_id,)).fetchone()
    if not cycle:
        abort(404)
    subs = db.execute('''
        SELECT s.*, m.first_name || ' ' || m.last_name AS member_name, m.member_number,
               m.total_savings, m.annual_salary
        FROM ctas_subscriptions s JOIN members m ON m.id = s.member_id
        WHERE s.cycle_id = ? ORDER BY s.payout_month, s.id
    ''', (cycle_id,)).fetchall()
    # Live eligibility note for still-submitted rows.
    elig = {}
    for s in subs:
        if s['status'] == ce.SUB_SUBMITTED:
            member = db.execute('SELECT * FROM members WHERE id = ?', (s['member_id'],)).fetchone()
            elig[s['id']] = ce.check_eligibility(db, member, cycle, s['target_amount'], s['tenure_months'])
    # Members available to add (active, not already in this cycle).
    members = db.execute('''
        SELECT id, first_name || ' ' || last_name AS name, member_number, total_savings, annual_salary
        FROM members WHERE status = 'active'
          AND id NOT IN (SELECT member_id FROM ctas_subscriptions WHERE cycle_id = ?)
        ORDER BY first_name''', (cycle_id,)).fetchall()
    summary = {
        'capacity': ce.total_capacity(cycle),
        'participants': ce.participant_count(db, cycle_id),
        'enrolled': sum(1 for s in subs if s['status'] == ce.SUB_ENROLLED),
    }
    return render_template('ctas/cycle_detail.html', cycle=cycle, subs=subs, elig=elig,
                           members=members, summary=summary, ce=ce)


@ctas.route('/ctas/cycles/<int:cycle_id>/transition', methods=['POST'])
@ctas_required
@role_required('admin', 'treasurer')
def cycle_transition(cycle_id):
    db = get_db()
    cycle = db.execute('SELECT * FROM ctas_cycles WHERE id = ?', (cycle_id,)).fetchone()
    if not cycle:
        abort(404)
    to = (request.form.get('to') or '').strip()
    try:
        if not ce.can_transition(cycle['status'], to):
            raise ValueError(f'Cannot move a {cycle["status"]} cycle to {to}.')
        if to == ce.CYCLE_READY_FOR_BALLOT:
            ce.assert_ready_for_ballot(db, cycle)
        db.execute('UPDATE ctas_cycles SET status = ?, updated_at = ? WHERE id = ?',
                   (to, datetime.now(), cycle_id))
        audit(db, 'CTAS_CYCLE_TRANSITION', 'ctas', f"Cycle {cycle['name']}: {cycle['status']} -> {to}")
        db.commit()
        flash(f'Cycle moved to {to.replace("_", " ")}.', 'success')
    except ValueError as e:
        flash(str(e), 'danger')
    return redirect(url_for('ctas.cycle_detail', cycle_id=cycle_id))


# ── Admin: subscriptions ──────────────────────────────────────────────────────

@ctas.route('/ctas/cycles/<int:cycle_id>/subscriptions', methods=['POST'])
@ctas_required
@role_required('admin', 'treasurer')
def add_subscription(cycle_id):
    db = get_db()
    cycle = db.execute('SELECT * FROM ctas_cycles WHERE id = ?', (cycle_id,)).fetchone()
    if not cycle:
        abort(404)
    if cycle['status'] != ce.CYCLE_OPEN:
        flash('Members can only be added while the cycle is open.', 'warning')
        return redirect(url_for('ctas.cycle_detail', cycle_id=cycle_id))
    member = db.execute('SELECT * FROM members WHERE id = ?', (request.form.get('member_id'),)).fetchone()
    if not member:
        flash('Choose a member.', 'danger')
        return redirect(url_for('ctas.cycle_detail', cycle_id=cycle_id))
    try:
        target = float(request.form.get('target_amount') or 0)
        tenure = int(request.form.get('tenure_months') or 0)
    except ValueError:
        flash('Enter a valid target amount and tenure.', 'danger')
        return redirect(url_for('ctas.cycle_detail', cycle_id=cycle_id))

    result = ce.check_eligibility(db, member, cycle, target, tenure)
    # Hard rules (dup subscription, bad numbers, capacity) block; affordability is advisory here.
    hard = [r for r in result['reasons'] if 'exceeds' not in r and 'salary' not in r.lower()]
    if hard:
        flash('Cannot add: ' + ' '.join(hard), 'danger')
        return redirect(url_for('ctas.cycle_detail', cycle_id=cycle_id))

    db.execute('''INSERT INTO ctas_subscriptions
            (cycle_id, member_id, target_amount, tenure_months, monthly_deduction, admin_fee,
             status, outstanding, created_by)
            VALUES (?, ?, ?, ?, ?, ?, 'submitted', ?, ?)''',
        (cycle_id, member['id'], target, tenure, result['monthly_deduction'],
         result['admin_fee'], target, current_user.id))
    audit(db, 'CTAS_SUBSCRIPTION_ADD', 'ctas',
          f"{member['first_name']} {member['last_name']} joined cycle {cycle['name']} (target {target})")
    db.commit()
    note = '' if result['eligible'] else ' (affordability flagged — review before enrolling)'
    flash(f'Subscription added for {member["first_name"]} {member["last_name"]}.{note}', 'success')
    return redirect(url_for('ctas.cycle_detail', cycle_id=cycle_id))


@ctas.route('/ctas/subscriptions/<int:sub_id>/act', methods=['POST'])
@ctas_required
@role_required('admin', 'treasurer')
def subscription_act(sub_id):
    db = get_db()
    sub = db.execute('SELECT * FROM ctas_subscriptions WHERE id = ?', (sub_id,)).fetchone()
    if not sub:
        abort(404)
    action = (request.form.get('action') or '').strip()
    now = datetime.now()
    if action == 'enroll' and sub['status'] in (ce.SUB_SUBMITTED,):
        db.execute("UPDATE ctas_subscriptions SET status = 'enrolled', enrolled_at = ?, "
                   "approved_by = ?, approved_at = ? WHERE id = ?", (now, current_user.id, now, sub_id))
        flash('Member enrolled.', 'success')
    elif action == 'unenroll' and sub['status'] == ce.SUB_ENROLLED:
        db.execute("UPDATE ctas_subscriptions SET status = 'submitted', enrolled_at = NULL WHERE id = ?", (sub_id,))
        flash('Member returned to submitted.', 'info')
    elif action == 'reject' and sub['status'] in (ce.SUB_SUBMITTED, ce.SUB_ENROLLED):
        reason = (request.form.get('reason') or '').strip()
        db.execute("UPDATE ctas_subscriptions SET status = 'rejected', rejected_reason = ? WHERE id = ?",
                   (reason, sub_id))
        flash('Subscription rejected.', 'info')
    else:
        flash('That action is not allowed for this subscription right now.', 'warning')
        return redirect(url_for('ctas.cycle_detail', cycle_id=sub['cycle_id']))
    audit(db, 'CTAS_SUBSCRIPTION_ACT', 'ctas', f"Subscription #{sub_id}: {action}")
    db.commit()
    return redirect(url_for('ctas.cycle_detail', cycle_id=sub['cycle_id']))


# ── Admin: ballot + payout ────────────────────────────────────────────────────

@ctas.route('/ctas/cycles/<int:cycle_id>/ballot', methods=['POST'])
@ctas_required
@role_required('admin', 'treasurer')
def run_ballot(cycle_id):
    db = get_db()
    cycle = db.execute('SELECT * FROM ctas_cycles WHERE id = ?', (cycle_id,)).fetchone()
    if not cycle:
        abort(404)
    try:
        if cycle['status'] != ce.CYCLE_READY_FOR_BALLOT:
            raise ValueError('Move the cycle to "ready for ballot" first.')
        enrolled = db.execute("SELECT id FROM ctas_subscriptions WHERE cycle_id = ? AND status = 'enrolled'",
                              (cycle_id,)).fetchall()
        ids = [r['id'] for r in enrolled]
        seed = secrets.token_hex(8)
        assignments = ce.assign_payout_months(ids, cycle, seed)
        for sub_id, month in assignments.items():
            db.execute("UPDATE ctas_subscriptions SET status = 'scheduled', payout_month = ?, "
                       "ballot_assigned_at = ? WHERE id = ?", (month, datetime.now(), sub_id))
        summary = f"{len(ids)} member(s) assigned payout months (seed {seed})."
        db.execute("INSERT INTO ctas_ballot_runs (cycle_id, seed, summary, executed_by) VALUES (?, ?, ?, ?)",
                   (cycle_id, seed, summary, current_user.id))
        db.execute("UPDATE ctas_cycles SET status = 'balloted', updated_at = ? WHERE id = ?",
                   (datetime.now(), cycle_id))
        audit(db, 'CTAS_BALLOT', 'ctas', f"Cycle {cycle['name']}: {summary}")
        db.commit()
        flash(f'Ballot complete — {summary}', 'success')
    except ValueError as e:
        db.rollback()
        flash(str(e), 'danger')
    return redirect(url_for('ctas.cycle_detail', cycle_id=cycle_id))


@ctas.route('/ctas/subscriptions/<int:sub_id>/payout', methods=['POST'])
@ctas_required
@role_required('admin', 'treasurer')
def payout(sub_id):
    db = get_db()
    sub = db.execute('SELECT * FROM ctas_subscriptions WHERE id = ?', (sub_id,)).fetchone()
    if not sub:
        abort(404)
    if sub['status'] != ce.SUB_SCHEDULED:
        flash('Only a scheduled (balloted) subscription can be paid out.', 'warning')
        return redirect(url_for('ctas.cycle_detail', cycle_id=sub['cycle_id']))
    member = db.execute('SELECT * FROM members WHERE id = ?', (sub['member_id'],)).fetchone()
    ctas_ensure_accounts(db)
    target = float(sub['target_amount'])
    fee = float(sub['admin_fee'] or 0)
    cash = get_default_cash_account(db)
    lines = [
        {'account': CTAS_ADVANCES, 'debit': target, 'memo': f"CTAS advance to {member['first_name']} {member['last_name']}"},
        {'account': cash, 'credit': round(target - fee, 2), 'memo': 'CTAS advance disbursed'},
    ]
    if fee:
        lines.append({'account': CTAS_ADMIN_FEE_INCOME, 'credit': fee, 'memo': 'CTAS admin fee'})
    try:
        post_journal_safe(db, f"CTAS payout - subscription {sub_id}", lines,
                          date=datetime.now(), reference=f"CTAS-PO-{sub_id}",
                          source_module='ctas_payout', source_id=sub_id, created_by=current_user.id)
        db.execute("UPDATE ctas_subscriptions SET status = 'active_recovery', paid_out_at = ?, "
                   "payout_date = ? WHERE id = ?", (datetime.now(), datetime.now().date(), sub_id))
        audit(db, 'CTAS_PAYOUT', 'ctas',
              f"Paid out ₦{target:,.2f} to {member['first_name']} {member['last_name']} (sub #{sub_id})")
        db.commit()
        flash(f'Paid out ₦{target - fee:,.2f} (net of ₦{fee:,.2f} admin fee). Recovery has started.', 'success')
    except Exception as e:
        db.rollback()
        flash(f'Could not post the payout: {e}', 'danger')
    return redirect(url_for('ctas.cycle_detail', cycle_id=sub['cycle_id']))
