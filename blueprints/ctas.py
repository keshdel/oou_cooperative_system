"""
CTAS — Cooperative Target Advance Scheme.

A balloted rotating-contribution (ajo/esusu) module. Members subscribe to a
cycle and contribute a fixed amount every period; a seeded ballot assigns each a
payout position; on that position they collect the full target. Their own pooled
contributions fund part of the payout and the cooperative advances the gap,
which their remaining contributions repay. Money posts to the real double-entry
GL (CTAS Contribution Pool, CTAS Advances Receivable, CTAS Admin Fee Income,
CTAS Write-offs) and reuses CoopMS members.

**Optional feature.** Off by default and leaves no footprint (no menu, no
routes, no GL accounts) until a cooperative is activated — on request — either by
the operator from HQ (POST /api/hq/set-feature) or by a super admin here. Gate
every CTAS view with @ctas_required.
"""

import csv
import secrets
from datetime import datetime
from io import StringIO, TextIOWrapper
from functools import wraps

from flask import (Blueprint, abort, current_app, flash, make_response, redirect,
                   render_template, request, url_for)
from flask_login import current_user, login_required

from database import get_db, last_insert_id
from utils import audit, role_required, member_for_user, notify_member
from ledger import post_journal_safe, get_default_cash_account, MEMBER_DEPOSITS, SHARE_CAPITAL
import ctas_engine as ce

ctas = Blueprint('ctas', __name__)

CTAS_WRITEOFF = '5150'   # expense — outstanding CTAS advance written off on member exit
CTAS_POOL = '2050'       # liability — members' contributions held in the rotating pool


def _notify_member(db, member_id, title, message, action_url='/my-ctas'):
    """Best-effort in-app + push notification to a member by id."""
    row = db.execute('SELECT email FROM members WHERE id = ?', (member_id,)).fetchone()
    if row and row['email']:
        notify_member(db, row['email'], title, message, 'info', action_url)


def _raise_exception(db, subscription_id, case_type, month, amount, description):
    """Log a CTAS exception case (missed deduction, exit recovery, override)."""
    db.execute("INSERT INTO ctas_exceptions (subscription_id, case_type, status, month_number, "
               "amount, description) VALUES (?, ?, 'open', ?, ?, ?)",
               (subscription_id, case_type, month, amount, description))

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
        (CTAS_POOL, 'CTAS Contribution Pool', 'liability', 'credit'),
        (CTAS_ADMIN_FEE_INCOME, 'CTAS Admin Fee Income', 'income', 'credit'),
        (CTAS_WRITEOFF, 'CTAS Write-offs', 'expense', 'debit'),
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
    active_plans = db.execute("SELECT * FROM ctas_plans WHERE status = 'active' ORDER BY name").fetchall()
    return render_template('ctas/cycles.html', cycles=cycles, plans=active_plans, ce=ce)


@ctas.route('/ctas/cycles', methods=['POST'])
@ctas_required
@role_required('admin', 'treasurer')
def new_cycle():
    db = get_db()
    name = (request.form.get('name') or '').strip()
    if not name:
        flash('Cycle name is required.', 'danger')
        return redirect(url_for('ctas.dashboard'))

    # A cycle may be created from a reusable Plan (which supplies the product
    # terms), with the form overriding only the schedule/dates.
    plan = None
    plan_id = request.form.get('plan_id')
    if plan_id:
        plan = db.execute('SELECT * FROM ctas_plans WHERE id = ?', (plan_id,)).fetchone()

    def _num(key, default):
        try:
            return float(request.form.get(key) or default)
        except ValueError:
            return float(default)

    def _pn(key, default):
        if plan is not None and key in plan.keys() and plan[key] is not None:
            try:
                return float(plan[key])
            except (TypeError, ValueError):
                return float(default)
        return _num(key, default)

    frequency = (request.form.get('frequency') or (plan['frequency'] if plan else 'monthly') or 'monthly').strip()
    periods = int(_pn('periods', 12))
    contribution = _pn('contribution_amount', 0)

    db.execute('''INSERT INTO ctas_cycles
            (name, status, start_date, end_date, duration_months, fixed_monthly_amount,
             frequency, periods, contribution_amount, plan_id,
             monthly_capacity, earliest_payout_month, max_participants,
             admin_fee_flat, admin_fee_percentage, admin_fee_cap, admin_fee_threshold,
             ballot_date, affordability_method, affordability_ratio, savings_multiple, created_by)
            VALUES (?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (name, (request.form.get('start_date') or '').strip() or None,
         (request.form.get('end_date') or '').strip() or None,
         periods, contribution,   # duration_months = periods (back-compat), fixed_monthly_amount = contribution
         frequency, periods, contribution, (plan['id'] if plan else None),
         int(_pn('monthly_capacity', 1)), int(_pn('earliest_payout_month', 1)),
         int(_num('max_participants', 0)),
         _pn('admin_fee_flat', 0), _pn('admin_fee_percentage', 0),
         _pn('admin_fee_cap', 0), _pn('admin_fee_threshold', 0),
         (request.form.get('ballot_date') or '').strip() or None,
         (plan['affordability_method'] if plan else (request.form.get('affordability_method') or 'savings')).strip(),
         _pn('affordability_ratio', 0.5), _pn('savings_multiple', 3), current_user.id))
    cycle_id = last_insert_id(db)   # capture before the audit insert changes last-rowid
    audit(db, 'CTAS_CYCLE_CREATE', 'ctas', f"Created cycle {name}")
    db.commit()
    flash(f'Cycle "{name}" created (draft). Open it to start enrolment.', 'success')
    return redirect(url_for('ctas.cycle_detail', cycle_id=cycle_id))


# ── Admin: plans (reusable product definitions) ───────────────────────────────

@ctas.route('/ctas/plans')
@ctas_required
@role_required('admin', 'treasurer')
def plans():
    db = get_db()
    rows = db.execute('''SELECT p.*, (SELECT COUNT(*) FROM ctas_cycles c WHERE c.plan_id = p.id) AS cycle_count
                         FROM ctas_plans p ORDER BY p.status = 'active' DESC, p.name''').fetchall()
    return render_template('ctas/plans.html', plans=rows)


@ctas.route('/ctas/plans', methods=['POST'])
@ctas_required
@role_required('admin', 'treasurer')
def new_plan():
    db = get_db()
    name = (request.form.get('name') or '').strip()
    if not name:
        flash('Plan name is required.', 'danger')
        return redirect(url_for('ctas.plans'))

    def _num(key, default):
        try:
            return float(request.form.get(key) or default)
        except ValueError:
            return float(default)

    frequency = (request.form.get('frequency') or 'monthly').strip()
    periods = int(_num('periods', 12))
    contribution = _num('contribution_amount', 0)
    target = ce.compute_target(contribution, periods)
    db.execute('''INSERT INTO ctas_plans
            (name, description, contribution_amount, frequency, periods, target_amount,
             monthly_capacity, earliest_payout_month, admin_fee_flat, admin_fee_percentage,
             admin_fee_cap, admin_fee_threshold, affordability_method, affordability_ratio,
             savings_multiple, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (name, (request.form.get('description') or '').strip(), contribution, frequency, periods, target,
         int(_num('monthly_capacity', 1)), int(_num('earliest_payout_month', 1)),
         _num('admin_fee_flat', 0), _num('admin_fee_percentage', 0),
         _num('admin_fee_cap', 0), _num('admin_fee_threshold', 0),
         (request.form.get('affordability_method') or 'savings').strip(),
         _num('affordability_ratio', 0.5), _num('savings_multiple', 3), current_user.id))
    audit(db, 'CTAS_PLAN_CREATE', 'ctas',
          f"Created plan {name} ({contribution:g} x {periods} {frequency}) target {target:g}")
    db.commit()
    flash(f'Plan "{name}" created — target ₦{target:,.2f}.', 'success')
    return redirect(url_for('ctas.plans'))


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


def _advert_for_cycle(db, cycle):
    """Build a persuasive, numbers-accurate advert for an open cycle. Uses the
    communications engine's {placeholders}, which are filled per member."""
    contribution = float(cycle['contribution_amount'] or 0)
    periods = ce.cycle_periods(cycle)
    target = ce.compute_target(contribution, periods)
    word = ce.period_word(cycle)
    every = {'weekly': 'every week', 'fortnightly': 'every two weeks',
             'monthly': 'every month'}.get(cycle['frequency'] or 'monthly', f'every {word}')
    coop = (db.execute("SELECT value FROM settings WHERE key = 'coop_name'").fetchone() or {}).get('value', 'your cooperative')
    fee_bits = []
    if float(cycle['admin_fee_flat'] or 0):
        fee_bits.append(f"₦{float(cycle['admin_fee_flat']):,.0f} administrative fee")
    fee_line = (' A one-off ' + ' / '.join(fee_bits) + ' applies.') if fee_bits else ''
    ballot = f"\nBallot date: {cycle['ballot_date']}" if cycle['ballot_date'] else ''
    closes = f"\nEnrolment closes: {cycle['end_date']}" if cycle['end_date'] else ''

    subject = f"Get ₦{target:,.0f} — join {cycle['name']} (Target Advance)"
    body = (
        'Dear {first_name},\n\n'
        f"Saving up for something big takes time — {coop} can help you get there sooner.\n\n"
        f"*{cycle['name']}* is now open. Contribute *₦{contribution:,.0f} {every}* for "
        f"*{periods} {word}s*, and receive your full *₦{target:,.0f}* in a single lump sum on "
        f"your allocated {word} — which could be long before you have finished contributing.\n\n"
        "Why members like it:\n"
        f"• You get the full ₦{target:,.0f} at once — for school fees, rent, business stock, "
        "equipment or a family project.\n"
        "• No interest. You contribute the same amount you receive.\n"
        f"• You do not need to find other contributors — {coop} runs and backs the scheme.\n"
        "• Your payout position is decided by a transparent, recorded ballot — everyone has a fair chance.\n"
        "• Contributions are automatic and every transaction is on your member record.\n\n"
        f"How it works: contribute ₦{contribution:,.0f} {every} → the ballot assigns your payout "
        f"{word} → you collect ₦{target:,.0f} → you keep contributing until the cycle ends."
        f"{fee_line}{ballot}{closes}\n\n"
        'Places are limited and allocated by ballot, so apply before enrolment closes.\n\n'
        'Apply in your member portal: {portal_link}\n\n'
        'Member number: {member_number}\n'
        'Your savings balance: {savings_balance}\n\n'
        'Warm regards,\n'
        f'{coop}'
    )
    return subject, body


@ctas.route('/ctas/cycles/<int:cycle_id>/promote', methods=['GET', 'POST'])
@ctas_required
@role_required('admin', 'treasurer')
def promote_cycle(cycle_id):
    """Advertise an open cycle to members. GET previews the generated advert;
    POST creates a communications campaign (email now, WhatsApp when enabled)."""
    db = get_db()
    cycle = db.execute('SELECT * FROM ctas_cycles WHERE id = ?', (cycle_id,)).fetchone()
    if not cycle:
        abort(404)
    subject, body = _advert_for_cycle(db, cycle)
    if request.method == 'GET':
        return render_template('ctas/promote.html', cycle=cycle, subject=subject, body=body)

    subject = (request.form.get('subject') or subject).strip()
    body = (request.form.get('body') or body).strip()
    channel = (request.form.get('channel') or 'email').strip()
    audience = (request.form.get('audience') or 'active').strip()
    if channel != 'email':
        flash('Only email sending is enabled for now. WhatsApp will send once its '
              'consent/template setup is completed in Communications.', 'warning')
        return redirect(url_for('ctas.promote_cycle', cycle_id=cycle_id))

    from blueprints.communications import _members_for_audience, _process_campaign
    members = _members_for_audience(db, audience, None)
    db.execute('''INSERT INTO communication_campaigns
            (title, audience, channel, subject, body, status, recipient_count, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, 'draft', ?, ?, ?)''',
        (f"CTAS advert — {cycle['name']}", audience, channel, subject, body,
         len(members), current_user.id, datetime.now()))
    campaign_id = last_insert_id(db)
    for m in members:
        db.execute('''INSERT INTO communication_recipients
                (campaign_id, member_id, channel, destination, status)
                VALUES (?, ?, ?, ?, 'pending')''',
            (campaign_id, m['id'], channel, (m['email'] if 'email' in m.keys() else '') or ''))
    audit(db, 'CTAS_PROMOTE', 'ctas',
          f"Advertised cycle {cycle['name']} to {len(members)} member(s) by {channel}")
    db.commit()
    try:
        _process_campaign(campaign_id)
    except Exception as exc:   # pragma: no cover - delivery is best-effort
        current_app.logger.warning('CTAS advert send failed: %s', exc)
    flash(f'Advert queued to {len(members)} member(s). Track delivery under Communications.', 'success')
    return redirect(url_for('communications.campaign_detail', campaign_id=campaign_id))


@ctas.route('/ctas/cycles/<int:cycle_id>/delete', methods=['POST'])
@ctas_required
@role_required('admin', 'treasurer')
def delete_cycle(cycle_id):
    """Delete a cycle and its subscriptions — for clearing test/abandoned cycles.
    Refused once money has moved (any payout or contribution posted to the GL),
    because those journals must stay for audit; void/settle those instead."""
    db = get_db()
    cycle = db.execute('SELECT * FROM ctas_cycles WHERE id = ?', (cycle_id,)).fetchone()
    if not cycle:
        abort(404)
    sub_ids = [r['id'] for r in db.execute(
        'SELECT id FROM ctas_subscriptions WHERE cycle_id = ?', (cycle_id,)).fetchall()]
    if sub_ids:
        placeholders = ','.join('?' for _ in sub_ids)
        posted = db.execute(
            f"SELECT COUNT(*) FROM journal_entries WHERE source_module IN "
            f"('ctas_payout','ctas_contribution','ctas_recovery','ctas_exit') "
            f"AND source_id IN ({placeholders})", sub_ids).fetchone()[0]
        if posted:
            flash('This cycle has money posted to the ledger and cannot be deleted. '
                  'Settle or complete its members instead.', 'danger')
            return redirect(url_for('ctas.cycle_detail', cycle_id=cycle_id))
        db.execute(f"DELETE FROM ctas_exceptions WHERE subscription_id IN ({placeholders})", sub_ids)
        db.execute(f"DELETE FROM ctas_payroll_lines WHERE subscription_id IN ({placeholders})", sub_ids)
    db.execute('DELETE FROM ctas_payroll_batches WHERE cycle_id = ?', (cycle_id,))
    db.execute('DELETE FROM ctas_ballot_runs WHERE cycle_id = ?', (cycle_id,))
    db.execute('DELETE FROM ctas_subscriptions WHERE cycle_id = ?', (cycle_id,))
    db.execute('DELETE FROM ctas_cycles WHERE id = ?', (cycle_id,))
    audit(db, 'CTAS_CYCLE_DELETE', 'ctas',
          f"Deleted cycle {cycle['name']} ({len(sub_ids)} subscription(s))")
    db.commit()
    flash(f'Cycle "{cycle["name"]}" deleted.', 'info')
    return redirect(url_for('ctas.dashboard'))


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

    terms = 1 if request.form.get('terms') else 0
    signature = (request.form.get('signature_name') or '').strip()
    db.execute('''INSERT INTO ctas_subscriptions
            (cycle_id, member_id, target_amount, tenure_months, monthly_deduction, admin_fee,
             status, outstanding, terms_accepted, signature_name, signed_at, created_by)
            VALUES (?, ?, ?, ?, ?, ?, 'submitted', ?, ?, ?, ?, ?)''',
        (cycle_id, member['id'], target, tenure, result['monthly_deduction'],
         result['admin_fee'], target, terms, signature or None,
         datetime.now() if terms else None, current_user.id))
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
    detail = url_for('ctas.cycle_detail', cycle_id=sub['cycle_id'])

    if action == 'reject' and sub['status'] in ce.APPROVAL_ORDER:
        reason = (request.form.get('reason') or '').strip()
        db.execute("UPDATE ctas_subscriptions SET status = 'rejected', rejected_reason = ? WHERE id = ?",
                   (reason, sub_id))
        _notify_member(db, sub['member_id'], 'Target Advance — application declined',
                       'Your target-advance application was not approved.'
                       + (f' Reason: {reason}' if reason else ''))
        flash('Application declined.', 'info')

    elif action == 'back' and sub['status'] in ce.PREV_STAGE:
        db.execute("UPDATE ctas_subscriptions SET status = ? WHERE id = ?",
                   (ce.PREV_STAGE[sub['status']], sub_id))
        flash('Moved back one step.', 'info')

    elif action == 'advance':
        nxt = ce.next_stage(sub['status'])
        if not nxt:
            flash('This application is already fully approved and enrolled.', 'warning')
            return redirect(detail)
        # Re-run eligibility at the eligibility gate and again at enrolment.
        if nxt in (ce.SUB_ELIGIBLE, ce.SUB_ENROLLED):
            cycle = db.execute('SELECT * FROM ctas_cycles WHERE id = ?', (sub['cycle_id'],)).fetchone()
            member = db.execute('SELECT * FROM members WHERE id = ?', (sub['member_id'],)).fetchone()
            res = ce.check_eligibility(db, member, cycle, sub['target_amount'], sub['tenure_months'],
                                       exclude_cycle_id=sub['cycle_id'])
            hard = [r for r in res['reasons'] if 'exceeds' not in r and 'salary' not in r.lower()]
            if hard:
                flash('Cannot advance: ' + ' '.join(hard), 'danger')
                return redirect(detail)
        if nxt == ce.SUB_ELIGIBLE:
            db.execute("UPDATE ctas_subscriptions SET status = ?, eligibility_at = ?, eligibility_by = ? "
                       "WHERE id = ?", (nxt, now, current_user.id, sub_id))
        elif nxt == ce.SUB_FINANCE_REVIEWED:
            db.execute("UPDATE ctas_subscriptions SET status = ?, finance_reviewed_at = ?, "
                       "finance_reviewed_by = ? WHERE id = ?", (nxt, now, current_user.id, sub_id))
        elif nxt == ce.SUB_APPROVED:
            db.execute("UPDATE ctas_subscriptions SET status = ?, approved_at = ?, approved_by = ? "
                       "WHERE id = ?", (nxt, now, current_user.id, sub_id))
            _notify_member(db, sub['member_id'], 'Target Advance — approved',
                           'Your application is approved. You will be enrolled for the ballot.')
        elif nxt == ce.SUB_ENROLLED:
            db.execute("UPDATE ctas_subscriptions SET status = ?, enrolled_at = ? WHERE id = ?",
                       (nxt, now, sub_id))
            _notify_member(db, sub['member_id'], 'Target Advance — enrolled',
                           'You are enrolled. The ballot will assign your payout month.')
        flash(f'Advanced to {nxt.replace("_", " ")}.', 'success')

    else:
        flash('That action is not allowed for this application right now.', 'warning')
        return redirect(detail)

    audit(db, 'CTAS_SUBSCRIPTION_ACT', 'ctas', f"Subscription #{sub_id}: {action}")
    db.commit()
    return redirect(detail)


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
            row = db.execute('SELECT member_id FROM ctas_subscriptions WHERE id = ?', (sub_id,)).fetchone()
            _notify_member(db, row['member_id'], 'Target Advance — ballot result',
                           f'The ballot is complete. Your advance is scheduled for month {month} of the cycle.')
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
    contributed = float(sub['contributed_total'] or 0)
    # Their own pooled contributions cover part of the payout; the co-op advances
    # the rest (recovered from their remaining contributions).
    pool_portion = round(min(contributed, target), 2)
    advance_portion = round(target - pool_portion, 2)
    cash = get_default_cash_account(db)
    lines = []
    if pool_portion > 0:
        lines.append({'account': CTAS_POOL, 'debit': pool_portion, 'memo': "CTAS payout from member's pool"})
    if advance_portion > 0:
        lines.append({'account': CTAS_ADVANCES, 'debit': advance_portion,
                      'memo': f"CTAS advance to {member['first_name']} {member['last_name']}"})
    lines.append({'account': cash, 'credit': round(target - fee, 2), 'memo': 'CTAS payout disbursed'})
    if fee:
        lines.append({'account': CTAS_ADMIN_FEE_INCOME, 'credit': fee, 'memo': 'CTAS admin fee'})
    try:
        post_journal_safe(db, f"CTAS payout - subscription {sub_id}", lines,
                          date=datetime.now(), reference=f"CTAS-PO-{sub_id}",
                          source_module='ctas_payout', source_id=sub_id, created_by=current_user.id)
        db.execute("UPDATE ctas_subscriptions SET status = 'active_recovery', paid_out_at = ?, "
                   "payout_date = ?, advance_balance = ? WHERE id = ?",
                   (datetime.now(), datetime.now().date(), advance_portion, sub_id))
        _notify_member(db, sub['member_id'], 'Target Advance — paid out',
                       f"Your target of ₦{target - fee:,.2f} has been disbursed. Keep contributing "
                       f"₦{float(sub['monthly_deduction'] or 0):,.2f} each period until the cycle completes.")
        audit(db, 'CTAS_PAYOUT', 'ctas',
              f"Paid out ₦{target:,.2f} to {member['first_name']} {member['last_name']} (sub #{sub_id})")
        db.commit()
        flash(f'Paid out ₦{target - fee:,.2f} (net of ₦{fee:,.2f} admin fee). Recovery has started.', 'success')
    except Exception as e:
        db.rollback()
        flash(f'Could not post the payout: {e}', 'danger')
    return redirect(url_for('ctas.cycle_detail', cycle_id=sub['cycle_id']))


# ── Payroll recovery ──────────────────────────────────────────────────────────

@ctas.route('/ctas/cycles/<int:cycle_id>/payroll/export')
@ctas_required
@role_required('admin', 'treasurer')
def payroll_export(cycle_id):
    """Download the contribution schedule for a period. In the rotating pool
    EVERY active member contributes the fixed amount each period — whether or not
    they have already had their payout."""
    db = get_db()
    cycle = db.execute('SELECT * FROM ctas_cycles WHERE id = ?', (cycle_id,)).fetchone()
    if not cycle:
        abort(404)
    try:
        month = max(1, int(request.args.get('month') or 1))
    except ValueError:
        month = 1
    contribution = float(cycle['contribution_amount'] or 0)
    subs = db.execute('''
        SELECT s.id, s.monthly_deduction, s.status, m.member_number, m.employee_id,
               m.first_name || ' ' || m.last_name AS name
        FROM ctas_subscriptions s JOIN members m ON m.id = s.member_id
        WHERE s.cycle_id = ? AND s.status IN ('scheduled', 'active_recovery')
        ORDER BY m.member_number''', (cycle_id,)).fetchall()
    out = StringIO()
    w = csv.writer(out)
    w.writerow(['subscription_id', 'member_number', 'employee_id', 'name', 'status', 'period',
                'expected_amount', 'actual_amount'])
    for s in subs:
        exp = contribution or float(s['monthly_deduction'] or 0)
        w.writerow([s['id'], s['member_number'] or '', s['employee_id'] or '', s['name'],
                    s['status'], month, f"{exp:.2f}", ''])
    db.execute("INSERT INTO ctas_payroll_batches (cycle_id, month_number, kind, created_by) "
               "VALUES (?, ?, 'export', ?)", (cycle_id, month, current_user.id))
    db.commit()
    resp = make_response(out.getvalue())
    resp.headers['Content-Type'] = 'text/csv'
    resp.headers['Content-Disposition'] = f'attachment; filename=ctas_contributions_{cycle_id}_period{month}.csv'
    return resp


@ctas.route('/ctas/cycles/<int:cycle_id>/payroll/import', methods=['POST'])
@ctas_required
@role_required('admin', 'treasurer')
def payroll_import(cycle_id):
    """Import confirmed contributions for a period. In the rotating pool, a
    not-yet-paid member's contribution funds the pool (CR CTAS Pool); a paid-out
    member's contribution repays the co-op's advance (CR CTAS Advances).
    Idempotent per subscription+period."""
    db = get_db()
    cycle = db.execute('SELECT * FROM ctas_cycles WHERE id = ?', (cycle_id,)).fetchone()
    if not cycle:
        abort(404)
    try:
        month = max(1, int(request.form.get('month') or 1))
    except ValueError:
        month = 1
    file = request.files.get('file')
    if not file or not file.filename:
        flash('Choose a contributions CSV to import.', 'danger')
        return redirect(url_for('ctas.cycle_detail', cycle_id=cycle_id))

    ctas_ensure_accounts(db)
    cash = get_default_cash_account(db)
    contribution = float(cycle['contribution_amount'] or 0)
    db.execute("INSERT INTO ctas_payroll_batches (cycle_id, month_number, kind, file_name, processed, created_by) "
               "VALUES (?, ?, 'import', ?, 1, ?)", (cycle_id, month, file.filename, current_user.id))
    batch_id = last_insert_id(db)

    posted = missed = skipped = 0
    try:
        reader = csv.DictReader(TextIOWrapper(file, encoding='utf-8-sig'))
        for row in reader:
            sub = None
            sid = (row.get('subscription_id') or '').strip()
            if sid.isdigit():
                sub = db.execute("SELECT * FROM ctas_subscriptions WHERE id = ? AND cycle_id = ?",
                                 (int(sid), cycle_id)).fetchone()
            if not sub:
                mn = (row.get('member_number') or '').strip()
                if mn:
                    sub = db.execute(
                        "SELECT s.* FROM ctas_subscriptions s JOIN members m ON m.id = s.member_id "
                        "WHERE m.member_number = ? AND s.cycle_id = ? AND s.status IN ('scheduled','active_recovery')",
                        (mn, cycle_id)).fetchone()
            if not sub or sub['status'] not in ('scheduled', 'active_recovery'):
                skipped += 1
                continue
            # Idempotent per subscription+period.
            dup = db.execute(
                "SELECT 1 FROM ctas_payroll_lines l JOIN ctas_payroll_batches b ON b.id = l.batch_id "
                "WHERE l.subscription_id = ? AND b.month_number = ? AND b.kind = 'import' "
                "AND l.status IN ('deducted','partial')", (sub['id'], month)).fetchone()
            if dup:
                skipped += 1
                continue

            expected = contribution or float(sub['monthly_deduction'] or 0)
            try:
                actual = float((row.get('actual_amount') or row.get('amount') or 0) or 0)
            except ValueError:
                actual = 0.0
            if actual <= 0:
                db.execute("INSERT INTO ctas_payroll_lines (batch_id, subscription_id, expected_amount, "
                           "actual_amount, status) VALUES (?, ?, ?, 0, 'missed')",
                           (batch_id, sub['id'], expected))
                db.execute("UPDATE ctas_subscriptions SET arrears_amount = COALESCE(arrears_amount, 0) + ? "
                           "WHERE id = ?", (expected, sub['id']))
                _raise_exception(db, sub['id'], 'missed_deduction', month, expected,
                                 f'Missed contribution (period {month}): ₦{expected:,.2f} not received.')
                _notify_member(db, sub['member_id'], 'Target Advance — missed contribution',
                               f'Your period {month} contribution of ₦{expected:,.2f} was not received. '
                               f'Please pay to avoid arrears.')
                missed += 1
                continue

            shortfall = round(max(0.0, expected - actual), 2)
            over = round(max(0.0, actual - expected), 2)
            new_arrears = round(max(0.0, float(sub['arrears_amount'] or 0) + shortfall - over), 2)
            contributed = round(float(sub['contributed_total'] or 0) + actual, 2)

            if sub['status'] == 'active_recovery':
                # Repays the co-op's advance (cap the GL posting to the balance owed).
                post_amt = round(min(actual, float(sub['advance_balance'] or 0)), 2)
                credit_acct = CTAS_ADVANCES
                new_adv = round(max(0.0, float(sub['advance_balance'] or 0) - post_amt), 2)
                recovered = round(float(sub['total_recovered'] or 0) + post_amt, 2)
                status = 'completed' if new_adv <= 0 else 'active_recovery'
            else:
                # Not yet paid out — funds the pool.
                post_amt = actual
                credit_acct = CTAS_POOL
                new_adv = float(sub['advance_balance'] or 0)
                recovered = float(sub['total_recovered'] or 0)
                status = 'scheduled'

            if post_amt > 0:
                post_journal_safe(
                    db, f"CTAS contribution period {month} - subscription {sub['id']}",
                    [{'account': cash, 'debit': post_amt, 'memo': f'CTAS contribution p{month}'},
                     {'account': credit_acct, 'credit': post_amt, 'memo': f"CTAS contribution sub {sub['id']}"}],
                    date=datetime.now(), reference=f"CTAS-CN-{sub['id']}-M{month}",
                    source_module='ctas_contribution', source_id=sub['id'], created_by=current_user.id)

            db.execute("UPDATE ctas_subscriptions SET contributed_total = ?, advance_balance = ?, "
                       "total_recovered = ?, outstanding = ?, arrears_amount = ?, status = ?, "
                       "completed_at = ? WHERE id = ?",
                       (contributed, new_adv, recovered,
                        max(0.0, round(float(sub['target_amount']) - contributed, 2)), new_arrears, status,
                        datetime.now() if status == 'completed' else None, sub['id']))
            db.execute("INSERT INTO ctas_payroll_lines (batch_id, subscription_id, expected_amount, "
                       "actual_amount, status) VALUES (?, ?, ?, ?, ?)",
                       (batch_id, sub['id'], expected, actual, 'partial' if shortfall > 0 else 'deducted'))
            if shortfall > 0:
                _raise_exception(db, sub['id'], 'missed_deduction', month, shortfall,
                                 f'Partial contribution (period {month}): short by ₦{shortfall:,.2f}.')
                _notify_member(db, sub['member_id'], 'Target Advance — partial contribution',
                               f'Only ₦{actual:,.2f} of your ₦{expected:,.2f} period {month} contribution '
                               f'was received.')
            posted += 1

        audit(db, 'CTAS_CONTRIBUTION_IMPORT', 'ctas',
              f"Cycle {cycle_id} period {month}: posted {posted}, missed {missed}, skipped {skipped}")
        db.commit()
        flash(f'Contributions period {month}: posted {posted}, missed {missed}, skipped {skipped}.', 'success')
    except Exception as e:
        db.rollback()
        flash(f'Could not process the contributions file: {e}', 'danger')
    return redirect(url_for('ctas.cycle_detail', cycle_id=cycle_id))


# ── Member portal ─────────────────────────────────────────────────────────────

@ctas.route('/my-ctas')
@ctas_required
@login_required
def my_ctas():
    db = get_db()
    member = member_for_user(db, current_user.id)
    if not member:
        flash('Your account is not linked to a member record yet.', 'warning')
        return redirect(url_for('portal.member_portal'))
    subs = db.execute('''
        SELECT s.*, c.name AS cycle_name, c.duration_months, c.affordability_method
        FROM ctas_subscriptions s JOIN ctas_cycles c ON c.id = s.cycle_id
        WHERE s.member_id = ? ORDER BY s.applied_at DESC''', (member['id'],)).fetchall()
    open_cycles = db.execute('''
        SELECT * FROM ctas_cycles WHERE status = 'open'
          AND id NOT IN (SELECT cycle_id FROM ctas_subscriptions WHERE member_id = ?)
        ORDER BY name''', (member['id'],)).fetchall()
    has_active = ce.member_has_active_subscription(db, member['id'])
    return render_template('member/my-ctas.html', member=member, subs=subs,
                           open_cycles=open_cycles, has_active=has_active)


@ctas.route('/my-ctas/apply', methods=['POST'])
@ctas_required
@login_required
def my_ctas_apply():
    db = get_db()
    member = member_for_user(db, current_user.id)
    if not member:
        abort(403)
    cycle = db.execute("SELECT * FROM ctas_cycles WHERE id = ? AND status = 'open'",
                       (request.form.get('cycle_id'),)).fetchone()
    if not cycle:
        flash('That cycle is not open for applications.', 'warning')
        return redirect(url_for('ctas.my_ctas'))
    try:
        target = float(request.form.get('target_amount') or 0)
        tenure = int(request.form.get('tenure_months') or 0)
    except ValueError:
        flash('Enter a valid amount and tenure.', 'danger')
        return redirect(url_for('ctas.my_ctas'))

    if not request.form.get('terms'):
        flash('You must accept the scheme terms to apply.', 'danger')
        return redirect(url_for('ctas.my_ctas'))
    signature = (request.form.get('signature_name') or '').strip()
    if not signature:
        flash('Please type your full name as your signature.', 'danger')
        return redirect(url_for('ctas.my_ctas'))

    result = ce.check_eligibility(db, member, cycle, target, tenure)
    hard = [r for r in result['reasons'] if 'exceeds' not in r and 'salary' not in r.lower()]
    if hard:
        flash('Cannot apply: ' + ' '.join(hard), 'danger')
        return redirect(url_for('ctas.my_ctas'))

    db.execute('''INSERT INTO ctas_subscriptions
            (cycle_id, member_id, target_amount, tenure_months, monthly_deduction, admin_fee,
             status, outstanding, terms_accepted, signature_name, signed_at, created_by)
            VALUES (?, ?, ?, ?, ?, ?, 'submitted', ?, 1, ?, ?, ?)''',
        (cycle['id'], member['id'], target, tenure, result['monthly_deduction'],
         result['admin_fee'], target, signature, datetime.now(), current_user.id))
    audit(db, 'CTAS_APPLY', 'ctas', f"Member {member['id']} applied to cycle {cycle['name']} (target {target})")
    db.commit()
    note = '' if result['eligible'] else ' Affordability will be reviewed by the committee.'
    flash('Your target-advance application has been submitted for review.' + note, 'success')
    return redirect(url_for('ctas.my_ctas'))


# ── Exceptions: member exit (net-off waterfall) + arrears dashboard ────────────

@ctas.route('/ctas/subscriptions/<int:sub_id>/exit', methods=['GET', 'POST'])
@ctas_required
@role_required('admin', 'treasurer')
def exit_settle(sub_id):
    """Settle a member's outstanding advance on exit/resignation via the net-off
    waterfall: savings -> share capital -> other recoveries -> write-off."""
    db = get_db()
    sub = db.execute('''SELECT s.*, m.first_name || ' ' || m.last_name AS member_name,
                               m.total_savings, m.shares_value
                        FROM ctas_subscriptions s JOIN members m ON m.id = s.member_id
                        WHERE s.id = ?''', (sub_id,)).fetchone()
    if not sub:
        abort(404)
    if sub['status'] != ce.SUB_ACTIVE_RECOVERY:
        flash('Only a subscription in recovery can be settled on exit.', 'warning')
        return redirect(url_for('ctas.cycle_detail', cycle_id=sub['cycle_id']))
    # Outstanding = the co-op's advance still owed by this paid-out member.
    outstanding = round(float(sub['advance_balance'] or 0), 2)
    savings = float(sub['total_savings'] or 0)
    shares = float(sub['shares_value'] or 0)

    if request.method == 'GET':
        wf = ce.net_off_waterfall(outstanding, savings, shares, 0)
        return render_template('ctas/exit.html', sub=sub, outstanding=outstanding,
                               savings=savings, shares=shares, wf=wf)

    try:
        other = float(request.form.get('other_recovery') or 0)
    except ValueError:
        other = 0.0
    reason = (request.form.get('reason') or '').strip()
    wf = ce.net_off_waterfall(outstanding, savings, shares, other)
    ctas_ensure_accounts(db)
    cash = get_default_cash_account(db)
    try:
        lines = []
        if wf['from_savings'] > 0:
            lines.append({'account': MEMBER_DEPOSITS, 'debit': wf['from_savings'], 'memo': 'CTAS exit: from savings'})
        if wf['from_shares'] > 0:
            lines.append({'account': SHARE_CAPITAL, 'debit': wf['from_shares'], 'memo': 'CTAS exit: from share capital'})
        if wf['from_other'] > 0:
            lines.append({'account': cash, 'debit': wf['from_other'], 'memo': 'CTAS exit: other recoveries'})
        if wf['write_off'] > 0:
            lines.append({'account': CTAS_WRITEOFF, 'debit': wf['write_off'], 'memo': 'CTAS exit: write-off'})
        if outstanding > 0:
            lines.append({'account': CTAS_ADVANCES, 'credit': outstanding,
                          'memo': f'CTAS exit settlement sub {sub_id}'})
        if lines:
            post_journal_safe(db, f"CTAS exit settlement - subscription {sub_id}", lines,
                              date=datetime.now(), reference=f"CTAS-EXIT-{sub_id}",
                              source_module='ctas_exit', source_id=sub_id, created_by=current_user.id)
        db.execute("UPDATE members SET total_savings = COALESCE(total_savings, 0) - ?, "
                   "shares_value = COALESCE(shares_value, 0) - ? WHERE id = ?",
                   (wf['from_savings'], wf['from_shares'], sub['member_id']))
        db.execute("UPDATE ctas_subscriptions SET total_recovered = target_amount, outstanding = 0, "
                   "advance_balance = 0, arrears_amount = 0, status = 'completed', completed_at = ? WHERE id = ?",
                   (datetime.now(), sub_id))
        db.execute("INSERT INTO ctas_exceptions (subscription_id, case_type, status, amount, description, "
                   "resolution_note, resolved_at, resolved_by) "
                   "VALUES (?, 'exit_recovery', 'resolved', ?, ?, ?, ?, ?)",
                   (sub_id, outstanding, f"Member exit; outstanding ₦{outstanding:,.2f}. {reason}",
                    f"savings ₦{wf['from_savings']:,.2f}, shares ₦{wf['from_shares']:,.2f}, "
                    f"other ₦{wf['from_other']:,.2f}, write-off ₦{wf['write_off']:,.2f}",
                    datetime.now(), current_user.id))
        audit(db, 'CTAS_EXIT', 'ctas',
              f"Exit settle sub #{sub_id}: outstanding {outstanding}, write-off {wf['write_off']}")
        _notify_member(db, sub['member_id'], 'Target Advance — settled on exit',
                       f"Your target advance was settled. Outstanding ₦{outstanding:,.2f} recovered from your "
                       f"balances" + (f", ₦{wf['write_off']:,.2f} written off." if wf['write_off'] else "."))
        db.commit()
        flash(f"Exit settled — savings ₦{wf['from_savings']:,.2f}, shares ₦{wf['from_shares']:,.2f}"
              + (f", other ₦{wf['from_other']:,.2f}" if wf['from_other'] else "")
              + (f", written off ₦{wf['write_off']:,.2f}" if wf['write_off'] else "") + ".", 'success')
    except Exception as e:
        db.rollback()
        flash(f'Could not settle the exit: {e}', 'danger')
    return redirect(url_for('ctas.cycle_detail', cycle_id=sub['cycle_id']))


@ctas.route('/ctas/exceptions')
@ctas_required
@role_required('admin', 'treasurer')
def exceptions():
    db = get_db()
    show = (request.args.get('status') or 'open').strip()
    where = "WHERE e.status = 'open'" if show == 'open' else ''
    rows = db.execute(f'''
        SELECT e.*, s.cycle_id, m.first_name || ' ' || m.last_name AS member_name, m.member_number
        FROM ctas_exceptions e
        JOIN ctas_subscriptions s ON s.id = e.subscription_id
        JOIN members m ON m.id = s.member_id
        {where}
        ORDER BY e.created_at DESC''').fetchall()
    return render_template('ctas/exceptions.html', rows=rows, show=show)


@ctas.route('/ctas/exceptions/<int:case_id>/resolve', methods=['POST'])
@ctas_required
@role_required('admin', 'treasurer')
def resolve_exception(case_id):
    db = get_db()
    case = db.execute('SELECT * FROM ctas_exceptions WHERE id = ?', (case_id,)).fetchone()
    if not case:
        abort(404)
    note = (request.form.get('resolution_note') or '').strip()
    db.execute("UPDATE ctas_exceptions SET status = 'resolved', resolution_note = ?, resolved_at = ?, "
               "resolved_by = ? WHERE id = ?", (note, datetime.now(), current_user.id, case_id))
    audit(db, 'CTAS_EXCEPTION_RESOLVE', 'ctas', f"Resolved exception #{case_id}")
    db.commit()
    flash('Exception marked resolved.', 'success')
    return redirect(url_for('ctas.exceptions'))
