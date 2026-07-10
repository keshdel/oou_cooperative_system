"""
Online Payments Blueprint — OOU Cooperative
Routes:
  POST  /admin/pay/savings              — initiate savings payment
  POST  /admin/pay/loan/<loan_id>       — initiate loan repayment
  GET   /admin/pay/callback/<gateway>   — gateway redirect after payment
  POST  /webhooks/paystack              — Paystack webhook (server-side confirmation)
  POST  /webhooks/flutterwave           — Flutterwave webhook
"""

import json
from datetime import datetime

from flask import (Blueprint, abort, current_app, flash, jsonify,
                   redirect, render_template, request, url_for)
from flask_login import current_user, login_required

from database import get_db
from email_service import send_loan_repayment_email
from payments import get_gateway, generate_reference
from security import log_audit
from utils import audit, member_for_user, split_repayment
from ledger import (post_journal_safe, CASH, MEMBER_DEPOSITS, LOANS_RECEIVABLE,
                    LOAN_INTEREST_INCOME)

payments_bp = Blueprint('payments', __name__)

# URL prefix strategy:
#   member-facing payment initiation & callback → /admin/pay/...
#   gateway webhooks (external URLs)            → /webhooks/...  (no prefix)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _record_payment(db, reference: str) -> bool:
    """
    Idempotent: look up the pending_payments row for *reference*, verify with
    the gateway, and if successful commit the actual savings / repayment record.

    Returns True if payment was newly committed, False if already done or failed.
    """
    row = db.execute(
        'SELECT * FROM pending_payments WHERE reference = ?', (reference,)
    ).fetchone()
    if row is None:
        return False
    if row['status'] == 'completed':
        return False  # already processed (webhook beat the callback, or duplicate call)

    gateway_name = row['gateway']
    gw           = get_gateway(gateway_name)

    # ── Verify with gateway ────────────────────────────────────────────────────
    try:
        if gateway_name == 'paystack':
            resp   = gw.verify(reference)
            ok     = (resp.get('status') is True and
                      resp.get('data', {}).get('status') == 'success')
            gw_ref = resp.get('data', {}).get('id', '')
        else:  # flutterwave — reference is stored in gateway_ref after callback
            gw_ref = row['gateway_ref'] or reference
            resp   = gw.verify(str(gw_ref))
            ok     = (resp.get('status') == 'success' and
                      resp.get('data', {}).get('status') == 'successful')
            gw_ref = resp.get('data', {}).get('id', gw_ref)
    except Exception as exc:
        current_app.logger.error('Payment verify error ref=%s: %s', reference, exc)
        return False

    if not ok:
        db.execute(
            "UPDATE pending_payments SET status = 'failed', gateway_ref = ? WHERE reference = ?",
            (str(gw_ref), reference)
        )
        db.commit()
        return False

    # ── Mark pending row completed ─────────────────────────────────────────────
    db.execute(
        "UPDATE pending_payments SET status = 'completed', gateway_ref = ?, "
        "completed_at = ? WHERE reference = ?",
        (str(gw_ref), datetime.now(), reference)
    )

    # ── Persist the actual financial record ────────────────────────────────────
    ptype     = row['payment_type']
    member_id = row['member_id']
    amount    = row['amount']

    if ptype == 'savings':
        month = row['month']
        # Idempotency: skip if this month's savings already recorded
        exists = db.execute(
            'SELECT id FROM savings WHERE member_id = ? AND month = ?',
            (member_id, month)
        ).fetchone()
        if not exists:
            db.execute(
                '''INSERT INTO savings
                   (member_id, amount, month, payment_type, payment_method, reference, date)
                   VALUES (?, ?, ?, 'monthly', 'online', ?, ?)''',
                (member_id, amount, month, reference, datetime.now())
            )
            db.execute(
                'UPDATE members SET total_savings = total_savings + ? WHERE id = ?',
                (amount, member_id)
            )
            post_journal_safe(db, f'Online savings deposit — {month}', [
                {'account': CASH, 'debit': amount, 'memo': 'Online payment'},
                {'account': MEMBER_DEPOSITS, 'credit': amount, 'memo': f'Member {member_id}'},
            ], reference=reference, source_module='payments', source_id=member_id)

    elif ptype == 'loan_repayment':
        loan_id = row['related_id']
        loan    = db.execute(
            'SELECT * FROM loans WHERE id = ? AND member_id = ?',
            (loan_id, member_id)
        ).fetchone()
        if loan:
            existing_repayment = db.execute(
                'SELECT id FROM repayments WHERE reference = ?', (reference,)
            ).fetchone()
            if existing_repayment:
                db.commit()
                return False

            principal_paid, interest_paid = split_repayment(
                amount, loan['amount'], loan['total_repayment'])
            new_balance = max(loan['balance'] - amount, 0)

            rep_num = f"REP-{reference[-8:].upper()}"
            db.execute(
                '''INSERT INTO repayments
                   (repayment_number, loan_id, amount, principal_paid, interest_paid,
                    payment_method, reference, date)
                   VALUES (?, ?, ?, ?, ?, 'online', ?, ?)''',
                (rep_num, loan_id, amount, principal_paid, interest_paid,
                 reference, datetime.now())
            )
            if new_balance <= 0:
                db.execute(
                    "UPDATE loans SET balance = 0, status = 'completed', "
                    "completed_at = ? WHERE id = ?",
                    (datetime.now(), loan_id)
                )
            else:
                db.execute(
                    'UPDATE loans SET balance = ? WHERE id = ?',
                    (new_balance, loan_id)
                )
            post_journal_safe(db, f"Online loan repayment — {loan['loan_number']}", [
                {'account': CASH, 'debit': amount, 'memo': 'Online payment'},
                {'account': LOANS_RECEIVABLE, 'credit': principal_paid, 'memo': loan['loan_number']},
                {'account': LOAN_INTEREST_INCOME, 'credit': interest_paid, 'memo': 'Interest earned'},
            ], reference=reference, source_module='payments', source_id=loan_id)
            member = db.execute('SELECT * FROM members WHERE id = ?', (member_id,)).fetchone()
            if member and member['email']:
                send_loan_repayment_email(
                    member['email'],
                    member,
                    loan,
                    {
                        'repayment_number': rep_num,
                        'reference': reference,
                        'amount': amount,
                        'principal_paid': principal_paid,
                        'interest_paid': interest_paid,
                        'balance': new_balance,
                        'date': datetime.now().strftime('%Y-%m-%d'),
                    },
                    url_for('portal.my_loans', _external=True),
                )

    db.commit()
    return True


# ─── Initiate savings payment ─────────────────────────────────────────────────

@payments_bp.route('/admin/pay/savings', methods=['POST'])
@login_required
def initiate_savings():
    db     = get_db()
    member = member_for_user(db, current_user.id)
    if not member:
        flash('Member record not found.', 'danger')
        return redirect(url_for('portal.my_savings'))

    try:
        amount = float(request.form['amount'])
        month  = request.form['month']         # expected YYYY-MM
        if amount <= 0:
            raise ValueError('Amount must be positive')
    except (KeyError, ValueError) as exc:
        flash(f'Invalid payment details: {exc}', 'danger')
        return redirect(url_for('portal.my_savings'))

    # Check for duplicate (same month already paid)
    exists = db.execute(
        'SELECT id FROM savings WHERE member_id = ? AND month = ?',
        (member['id'], month)
    ).fetchone()
    if exists:
        flash(f'Savings for {month} have already been recorded.', 'warning')
        return redirect(url_for('portal.my_savings'))

    gateway_name = db.execute(
        "SELECT value FROM settings WHERE key = 'active_gateway'"
    ).fetchone()
    gateway_name = gateway_name['value'] if gateway_name else 'paystack'

    reference    = generate_reference('SAV')
    callback_url = url_for('payments.payment_callback',
                           gateway=gateway_name, _external=True)

    # Persist pending row BEFORE redirecting to gateway
    db.execute(
        '''INSERT INTO pending_payments
           (reference, member_id, payment_type, amount, month, gateway)
           VALUES (?, ?, 'savings', ?, ?, ?)''',
        (reference, member['id'], amount, month, gateway_name)
    )
    db.commit()

    email = member['email'] or current_user.email or ''
    name  = f"{member['first_name']} {member['last_name']}"
    desc  = f"Savings — {month}"

    try:
        gw = get_gateway(gateway_name)
        if gateway_name == 'paystack':
            resp = gw.initialize(email, amount, reference, callback_url,
                                 metadata={'member_id': member['id'], 'month': month})
            if resp.get('status'):
                return redirect(resp['data']['authorization_url'])
        else:  # flutterwave
            resp = gw.initialize(email, amount, reference, callback_url,
                                 name=name, description=desc)
            if resp.get('status') == 'success':
                return redirect(resp['data']['link'])
    except Exception as exc:
        current_app.logger.error('Payment init error: %s', exc)

    flash('Could not connect to payment gateway. Please try again or pay at the office.', 'danger')
    db.execute("UPDATE pending_payments SET status = 'failed' WHERE reference = ?", (reference,))
    db.commit()
    return redirect(url_for('portal.my_savings'))


# ─── Initiate loan repayment ──────────────────────────────────────────────────

@payments_bp.route('/admin/pay/loan/<int:loan_id>', methods=['POST'])
@login_required
def initiate_loan_repayment(loan_id):
    db     = get_db()
    member = member_for_user(db, current_user.id)
    if not member:
        flash('Member record not found.', 'danger')
        return redirect(url_for('portal.my_loans'))

    loan = db.execute(
        "SELECT * FROM loans WHERE id = ? AND member_id = ? AND status = 'active'",
        (loan_id, member['id'])
    ).fetchone()
    if not loan:
        flash('Loan not found or not active.', 'danger')
        return redirect(url_for('portal.my_loans'))

    try:
        amount = float(request.form['amount'])
        if amount <= 0:
            raise ValueError('Amount must be positive')
        if amount > loan['balance']:
            amount = loan['balance']   # cap at outstanding balance
    except (KeyError, ValueError) as exc:
        flash(f'Invalid amount: {exc}', 'danger')
        return redirect(url_for('portal.loan_detail', loan_id=loan_id))

    gateway_name = db.execute(
        "SELECT value FROM settings WHERE key = 'active_gateway'"
    ).fetchone()
    gateway_name = gateway_name['value'] if gateway_name else 'paystack'

    reference    = generate_reference('LOAN')
    callback_url = url_for('payments.payment_callback',
                           gateway=gateway_name, _external=True)

    db.execute(
        '''INSERT INTO pending_payments
           (reference, member_id, payment_type, related_id, amount, gateway)
           VALUES (?, ?, 'loan_repayment', ?, ?, ?)''',
        (reference, member['id'], loan_id, amount, gateway_name)
    )
    db.commit()

    email = member['email'] or current_user.email or ''
    name  = f"{member['first_name']} {member['last_name']}"
    desc  = f"Loan repayment — {loan['loan_number']}"

    try:
        gw = get_gateway(gateway_name)
        if gateway_name == 'paystack':
            resp = gw.initialize(email, amount, reference, callback_url,
                                 metadata={'member_id': member['id'], 'loan_id': loan_id})
            if resp.get('status'):
                return redirect(resp['data']['authorization_url'])
        else:
            resp = gw.initialize(email, amount, reference, callback_url,
                                 name=name, description=desc)
            if resp.get('status') == 'success':
                return redirect(resp['data']['link'])
    except Exception as exc:
        current_app.logger.error('Loan payment init error: %s', exc)

    flash('Could not connect to payment gateway. Please try again or pay at the office.', 'danger')
    db.execute("UPDATE pending_payments SET status = 'failed' WHERE reference = ?", (reference,))
    db.commit()
    return redirect(url_for('portal.loan_detail', loan_id=loan_id))


# ─── Payment callback (gateway redirect) ──────────────────────────────────────

@payments_bp.route('/admin/pay/callback/<gateway>', methods=['GET'])
@login_required
def payment_callback(gateway):
    """
    Gateway redirects the user here after payment attempt.
    Paystack:     ?reference=...&trxref=...
    Flutterwave:  ?tx_ref=...&transaction_id=...&status=...
    """
    if gateway not in {'paystack', 'flutterwave'}:
        abort(404)

    db = get_db()

    if gateway == 'paystack':
        reference = request.args.get('reference') or request.args.get('trxref', '')
    else:  # flutterwave
        reference      = request.args.get('tx_ref', '')
        transaction_id = request.args.get('transaction_id', '')

    if not reference:
        flash('Payment reference missing. Contact support if your account was debited.', 'danger')
        return redirect(url_for('portal.my_savings'))

    row = db.execute(
        'SELECT * FROM pending_payments WHERE reference = ?', (reference,)
    ).fetchone()
    if not row:
        flash('Unknown payment reference.', 'danger')
        return redirect(url_for('portal.my_savings'))

    member = member_for_user(db, current_user.id)
    if not member or row['member_id'] != member['id']:
        abort(403)

    if gateway == 'flutterwave' and transaction_id:
        db.execute(
            'UPDATE pending_payments SET gateway_ref = ? WHERE reference = ? AND member_id = ?',
            (transaction_id, reference, member['id'])
        )
        db.commit()

    success = _record_payment(db, reference)

    if success:
        flash('Payment successful! Your records have been updated.', 'success')
    else:
        status = db.execute(
            'SELECT status FROM pending_payments WHERE reference = ?', (reference,)
        ).fetchone()
        if status and status['status'] == 'completed':
            flash('This payment was already recorded.', 'info')
        else:
            flash('Payment could not be verified. Contact support if you were charged.', 'warning')

    # Redirect to appropriate page
    row = db.execute(
        'SELECT * FROM pending_payments WHERE reference = ?', (reference,)
    ).fetchone()
    if row and row['payment_type'] == 'loan_repayment' and row['related_id']:
        return redirect(url_for('portal.loan_detail', loan_id=row['related_id']))
    return redirect(url_for('portal.my_savings'))


# ─── Paystack Webhook ─────────────────────────────────────────────────────────

@payments_bp.route('/webhooks/paystack', methods=['POST'])
def paystack_webhook():
    payload   = request.get_data()
    signature = request.headers.get('X-Paystack-Signature', '')

    gw = get_gateway('paystack')
    if not gw.validate_webhook(payload, signature):
        abort(400)

    try:
        event = json.loads(payload)
    except ValueError:
        abort(400)

    if event.get('event') == 'charge.success':
        reference = event.get('data', {}).get('reference', '')
        if reference:
            db = get_db()
            _record_payment(db, reference)

    return jsonify({'status': 'ok'}), 200


# ─── Flutterwave Webhook ──────────────────────────────────────────────────────

@payments_bp.route('/webhooks/flutterwave', methods=['POST'])
def flutterwave_webhook():
    signature = request.headers.get('verif-hash', '')

    gw = get_gateway('flutterwave')
    if not gw.validate_webhook(signature):
        abort(400)

    try:
        event = request.get_json(force=True) or {}
    except Exception:
        abort(400)

    if event.get('event') == 'charge.completed':
        data      = event.get('data', {})
        reference = data.get('tx_ref', '')
        gw_id     = str(data.get('id', ''))
        if reference:
            db = get_db()
            if gw_id:
                db.execute(
                    'UPDATE pending_payments SET gateway_ref = ? WHERE reference = ? AND gateway_ref IS NULL',
                    (gw_id, reference)
                )
                db.commit()
            _record_payment(db, reference)

    return jsonify({'status': 'ok'}), 200
