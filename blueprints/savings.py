import csv
import random
from datetime import datetime
from io import StringIO

from flask import Blueprint, render_template, redirect, url_for, request, flash, make_response
from flask_login import login_required

from database import get_db
from email_service import send_payment_confirmation_email
from utils import role_required, audit

savings = Blueprint('savings', __name__)


@savings.route('/savings')
@login_required
def savings_list():
    db = get_db()
    all_savings = db.execute('''
        SELECT s.*, m.first_name || " " || m.last_name as member_name
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
        # Late fee applies only to monthly/salary savings recorded after the 10th
        if payment_type in ('monthly', 'salary') and today.day > 10:
            late_fee    = amount * 0.10
            total_amount = amount + late_fee
            flash(f'Late payment: 10% fee of ₦{late_fee:,.2f} applied.', 'info')
        else:
            late_fee    = 0
            total_amount = amount

        receipt_number = f"RCPT/{today.strftime('%Y%m%d')}/{random.randint(1000, 9999)}"

        db.execute('''
            INSERT INTO savings
                (member_id, amount, month, payment_type, late_fee,
                 payment_method, receipt_number, notes, date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (member_id, total_amount, month, payment_type, late_fee,
              payment_method, receipt_number, notes, today))

        db.execute('UPDATE members SET total_savings = total_savings + ? WHERE id = ?',
                   (total_amount, member_id))
        db.commit()

        saving_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        member    = db.execute('SELECT * FROM members WHERE id = ?', (member_id,)).fetchone()
        new_saving = db.execute('SELECT * FROM savings WHERE id = ?', (saving_id,)).fetchone()
        if member and member['email']:
            send_payment_confirmation_email(member['email'], member, new_saving)

        audit(db, 'ADD_SAVING', 'savings',
              f"Recorded ₦{amount:,.2f} {payment_type} savings for member ID {member_id}, "
              f"receipt {receipt_number}")
        flash(f'Savings of ₦{amount:,.2f} recorded. Receipt: {receipt_number}', 'success')

    except Exception as e:
        db.rollback()
        flash(f'Error recording savings: {str(e)}', 'danger')

    return redirect(url_for('members.member_details', member_id=member_id))
