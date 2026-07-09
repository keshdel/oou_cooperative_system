import csv
import random
from datetime import datetime
from io import StringIO

from flask import Blueprint, render_template, redirect, url_for, request, flash, make_response
from flask_login import login_required, current_user

from database import get_db, last_insert_id
from email_service import send_payment_confirmation_email
from utils import role_required, audit, notify_member, record_revenue
from ledger import post_journal_safe, CASH, MEMBER_DEPOSITS, FEE_INCOME

savings = Blueprint('savings', __name__)


@savings.route('/savings')
@login_required
@role_required('admin', 'treasurer', 'secretary', 'exco')
def savings_list():
    db = get_db()
    all_savings = db.execute('''
        SELECT s.*, m.first_name || ' ' || m.last_name as member_name
        FROM savings s
        JOIN members m ON s.member_id = m.id
        ORDER BY s.date DESC
    ''').fetchall()
    total_savings = db.execute('SELECT SUM(amount) FROM savings').fetchone()[0] or 0
    return render_template('admin/savings.html', savings=all_savings, total_savings=total_savings)


@savings.route('/savings/add', methods=['POST'])
@login_required
@role_required('admin', 'treasurer')
def add_saving():
    member_id     = request.form['member_id']
    amount        = float(request.form['amount'])
    month         = request.form['month']
    payment_type  = request.form.get('payment_type', 'monthly').strip() or 'monthly'
    payment_method = request.form.get('payment_method', 'cash').strip() or 'cash'
    notes         = request.form.get('notes', '').strip() or None

    if amount < 5000:
        flash(f'Minimum savings amount is ₦5,000. You entered ₦{amount:,.2f}', 'danger')
        return redirect(url_for('members.member_details', member_id=member_id))

    db = get_db()
    try:
        today = datetime.now()
        # Late fee applies only to monthly/salary savings recorded after the 10th.
        # The fee is cooperative INCOME — it is recorded separately and must NOT
        # inflate the member's savings balance.
        if payment_type in ('monthly', 'salary') and today.day > 10:
            late_fee = round(amount * 0.10, 2)
            flash(f'Late payment: 10% fee of ₦{late_fee:,.2f} applied.', 'info')
        else:
            late_fee = 0

        receipt_number = f"RCPT/{today.strftime('%Y%m%d')}/{random.randint(1000, 9999)}"

        # savings.amount is the member's contribution only (fee tracked separately)
        db.execute('''
            INSERT INTO savings
                (member_id, amount, month, payment_type, late_fee,
                 payment_method, receipt_number, notes, date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (member_id, amount, month, payment_type, late_fee,
              payment_method, receipt_number, notes, today))

        # Member savings balance grows only by the contribution, not the fee.
        db.execute('UPDATE members SET total_savings = total_savings + ? WHERE id = ?',
                   (amount, member_id))

        # Book the late fee as cooperative income.
        if late_fee:
            member_row = db.execute('SELECT first_name, last_name FROM members WHERE id = ?',
                                    (member_id,)).fetchone()
            member_name = (f"{member_row['first_name']} {member_row['last_name']}"
                           if member_row else f"member {member_id}")
            record_revenue(db, 'Late Fee', late_fee,
                           description=f'Late savings fee for {month}',
                           source=member_name, received_by=current_user.id,
                           notes=f'Receipt {receipt_number}')

        # Double-entry: cash in; member-deposit liability up; late fee is income.
        _lines = [
            {'account': CASH, 'debit': amount + late_fee, 'memo': f'Savings {month}'},
            {'account': MEMBER_DEPOSITS, 'credit': amount, 'memo': f'Member {member_id}'},
        ]
        if late_fee:
            _lines.append({'account': FEE_INCOME, 'credit': late_fee, 'memo': 'Late fee'})
        post_journal_safe(db, f'Savings deposit — {month}', _lines,
                          reference=receipt_number, source_module='savings',
                          source_id=member_id, created_by=current_user.id)

        db.commit()

        saving_id = last_insert_id(db)
        member    = db.execute('SELECT * FROM members WHERE id = ?', (member_id,)).fetchone()
        new_saving = db.execute('SELECT * FROM savings WHERE id = ?', (saving_id,)).fetchone()
        if member and member['email']:
            send_payment_confirmation_email(member['email'], member, new_saving)
            fee_note = f" (plus ₦{late_fee:,.2f} late fee)" if late_fee else ""
            notify_member(db, member['email'],
                          'Savings Payment Confirmed',
                          f"₦{amount:,.2f} {payment_type} savings recorded for "
                          f"{month}{fee_note}. Receipt: {receipt_number}.",
                          notification_type='info',
                          action_url='/my-savings')

        audit(db, 'ADD_SAVING', 'savings',
              f"Recorded ₦{amount:,.2f} {payment_type} savings for member ID {member_id}, "
              f"receipt {receipt_number}")
        flash(f'Savings of ₦{amount:,.2f} recorded. Receipt: {receipt_number}', 'success')

    except Exception as e:
        db.rollback()
        flash(f'Error recording savings: {str(e)}', 'danger')

    return redirect(url_for('members.member_details', member_id=member_id))
