import csv
import random
from datetime import datetime, timedelta
from io import StringIO, TextIOWrapper

from flask import Blueprint, render_template, redirect, url_for, request, flash, make_response
from flask_login import login_required, current_user

from database import get_db
from email_service import (send_loan_approval_email, send_loan_rejection_email,
                           send_loan_repayment_email)
from utils import (role_required, audit, notify_member, compute_loan_schedule,
                   PURPOSE_SETTING_KEY, METHOD_LABELS, record_revenue, split_repayment,
                   member_savings_balance)
from ledger import (post_journal_safe, CASH, LOANS_RECEIVABLE, FEE_INCOME,
                    LOAN_INTEREST_INCOME)

loans = Blueprint('loans', __name__)


@loans.route('/loans/download-repayment-template')
@login_required
@role_required('admin', 'treasurer')
def download_repayment_template():
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(['loan_number', 'amount', 'payment_date', 'payment_method', 'receipt_number', 'notes'])
    writer.writerow(['LOAN/20250428/0001', '25000', '2025-04-28', 'transfer', 'RCPT-001', 'First repayment'])
    writer.writerow(['LOAN/20250428/0002', '50000', '2025-04-29', 'cash', '', 'Partial payment'])
    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv'
    response.headers['Content-Disposition'] = 'attachment; filename=loan_repayment_template.csv'
    return response


@loans.route('/loans')
@login_required
@role_required('admin', 'treasurer', 'secretary', 'exco')
def loans_list():
    db = get_db()
    all_loans = db.execute('''
        SELECT l.*, m.first_name || ' ' || m.last_name as member_name
        FROM loans l
        JOIN members m ON l.member_id = m.id
        ORDER BY l.date_applied DESC
    ''').fetchall()
    active_loans = db.execute("SELECT SUM(amount) FROM loans WHERE status = 'active'").fetchone()[0] or 0

    # Compute overdue: active loans where disbursement_date + tenure months < today
    today = datetime.now()
    overdue = []
    for loan in all_loans:
        if loan['status'] != 'active':
            continue
        try:
            disbursed_str = loan['disbursement_date'] or loan['date_applied']
            disbursed = datetime.fromisoformat(str(disbursed_str).replace('Z', '+00:00').split('+')[0])
            due = disbursed + timedelta(days=int(loan['tenure']) * 30)
            if due < today:
                overdue.append(loan)
        except Exception:
            pass

    return render_template('admin/loans.html', loans=all_loans, active_loans=active_loans,
                           overdue_loans=overdue)


@loans.route('/loans/apply', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'treasurer', 'secretary', 'exco')
def apply_loan():
    db = get_db()

    max_tenure_row = db.execute("SELECT value FROM settings WHERE key = 'max_tenure_months'").fetchone()
    max_tenure = int(max_tenure_row['value']) if max_tenure_row else 18

    # Load all interest_* settings in one query
    interest_rows = db.execute("SELECT key, value FROM settings WHERE key LIKE 'interest_%'").fetchall()
    s_raw = {row['key']: row['value'] for row in interest_rows}

    interest_rates = {
        'Regular':        float(s_raw.get('interest_regular',    11)),
        'Housing':        float(s_raw.get('interest_housing',     9)),
        'Emergency':      float(s_raw.get('interest_emergency',  10)),
        'Asset Purchase': float(s_raw.get('interest_asset',      10)),
        'School Fees':    float(s_raw.get('interest_school_fees', 9)),
    }
    interest_methods = {
        'Regular':        s_raw.get('interest_method_regular',    'reducing_annual'),
        'Housing':        s_raw.get('interest_method_housing',    'reducing_annual'),
        'Emergency':      s_raw.get('interest_method_emergency',  'reducing_annual'),
        'Asset Purchase': s_raw.get('interest_method_asset',      'reducing_annual'),
        'School Fees':    s_raw.get('interest_method_school_fees','flat'),
    }

    if request.method == 'POST':
        member_id = request.form.get('member_id')
        amount    = float(request.form.get('amount', 0))
        purpose   = request.form.get('purpose')
        tenure    = int(request.form.get('tenure', 0))

        if not member_id or amount <= 0 or not purpose or tenure <= 0:
            flash('All fields are required and must be valid.', 'danger')
            return redirect(url_for('loans.apply_loan'))

        try:
            member = db.execute('SELECT * FROM members WHERE id = ?', (member_id,)).fetchone()
            if not member:
                flash('Member not found.', 'danger')
                return redirect(url_for('loans.apply_loan'))

            if member['date_joined']:
                try:
                    date_joined = datetime.fromisoformat(member['date_joined'].replace('Z', '+00:00'))
                except ValueError:
                    date_joined = datetime.strptime(member['date_joined'], '%Y-%m-%d %H:%M:%S')
                months_as_member = (datetime.now() - date_joined).days / 30
                if months_as_member < 6:
                    flash('Member must be registered for at least 6 months.', 'danger')
                    return redirect(url_for('members.member_details', member_id=member_id))
            else:
                flash('Member join date is missing. Please contact admin.', 'danger')
                return redirect(url_for('members.member_details', member_id=member_id))

            # Eligibility uses the savings ledger (source of truth), not the
            # cached members.total_savings column, which can drift.
            savings_balance = member_savings_balance(db, member_id)
            if savings_balance < 50000:
                flash(f'Minimum savings of ₦50,000 required (current: ₦{savings_balance:,.2f}).', 'danger')
                return redirect(url_for('members.member_details', member_id=member_id))

            outstanding = db.execute(
                "SELECT id FROM loans WHERE member_id = ? AND status = 'active'", (member_id,)
            ).fetchone()
            if outstanding:
                flash('Member already has an active loan. Please complete it before applying for a new one.', 'danger')
                return redirect(url_for('members.member_details', member_id=member_id))

            max_loan = savings_balance * 2
            if amount > max_loan:
                flash(f'Maximum loan amount is ₦{max_loan:,.2f} (2x savings).', 'danger')
                return redirect(url_for('members.member_details', member_id=member_id))

            # Look up rate and method for the chosen purpose
            interest_rate   = interest_rates.get(purpose, interest_rates.get('Regular', 11))
            interest_method = interest_methods.get(purpose, 'reducing_annual')

            monthly_payment, total_repayment, _ = compute_loan_schedule(
                amount, interest_rate, tenure, interest_method
            )

            loan_number = f"LOAN/{datetime.now().strftime('%Y%m%d')}/{random.randint(1000, 9999)}"

            db.execute('''
                INSERT INTO loans (
                    loan_number, member_id, amount, purpose, tenure, interest_rate,
                    interest_method, total_repayment, balance, status, date_applied
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (loan_number, member_id, amount, purpose, tenure, interest_rate,
                  interest_method, total_repayment, total_repayment, 'pending', datetime.now()))
            db.commit()
            flash('Loan application submitted successfully! Pending approval.', 'success')
            return redirect(url_for('members.member_details', member_id=member_id))

        except Exception as e:
            db.rollback()
            flash(f'Error applying for loan: {str(e)}', 'danger')
            return redirect(url_for('loans.apply_loan'))

    all_members = db.execute(
        "SELECT id, first_name, last_name FROM members WHERE status = 'active'"
    ).fetchall()
    return render_template('admin/apply-loan.html',
                           members=all_members,
                           max_tenure=max_tenure,
                           interest_rates=interest_rates,
                           interest_methods=interest_methods,
                           method_labels=METHOD_LABELS)


@loans.route('/loans/approve/<int:loan_id>', methods=['POST'])
@login_required
@role_required('admin', 'treasurer')
def approve_loan(loan_id):
    db = get_db()
    try:
        loan = db.execute('SELECT * FROM loans WHERE id = ?', (loan_id,)).fetchone()
        if loan and loan['status'] == 'pending':
            insurance = round(loan['amount'] * 0.01, 2)
            application_fee = round(loan['amount'] * 0.01, 2)
            disbursed = round(loan['amount'] - insurance - application_fee, 2)

            db.execute('''
                UPDATE loans SET
                    status = 'active',
                    approved_at = ?,
                    approved_by = ?,
                    insurance_premium = ?,
                    application_fee = ?,
                    disbursed_amount = ?,
                    disbursement_date = ?,
                    first_payment_date = ?
                WHERE id = ?
            ''', (
                datetime.now(), current_user.id, insurance, application_fee,
                disbursed, datetime.now(), datetime.now() + timedelta(days=30), loan_id
            ))

            # Loan fees deducted at disbursement are cooperative income.
            record_revenue(db, 'Loan Insurance', insurance,
                           description=f"Insurance premium on loan {loan['loan_number']}",
                           source=f"Loan {loan['loan_number']}", received_by=current_user.id)
            record_revenue(db, 'Loan Application Fee', application_fee,
                           description=f"Application fee on loan {loan['loan_number']}",
                           source=f"Loan {loan['loan_number']}", received_by=current_user.id)

            # Double-entry disbursement: principal becomes receivable; cash paid
            # out (net of fees); the fees are income.
            post_journal_safe(db, f"Loan disbursement — {loan['loan_number']}", [
                {'account': LOANS_RECEIVABLE, 'debit': loan['amount'], 'memo': loan['loan_number']},
                {'account': CASH, 'credit': disbursed, 'memo': 'Net disbursed'},
                {'account': FEE_INCOME, 'credit': insurance + application_fee, 'memo': 'Loan fees'},
            ], reference=loan['loan_number'], source_module='loans',
               source_id=loan_id, created_by=current_user.id)
            db.commit()
            member = db.execute('SELECT * FROM members WHERE id = ?', (loan['member_id'],)).fetchone()
            if member and member['email']:
                send_loan_approval_email(member['email'], member, loan)
                notify_member(db, member['email'],
                              'Loan Approved',
                              f"Your loan of ₦{loan['amount']:,.2f} has been approved and will be disbursed shortly.",
                              notification_type='success',
                              action_url='/my-loans')
            audit(db, 'APPROVE_LOAN', 'loans',
                  f"Approved loan ID {loan_id} – ₦{loan['amount']:,.2f} for member ID {loan['member_id']}")
            flash('Loan approved successfully!', 'success')
        else:
            flash('Loan not found or already processed', 'danger')
    except Exception as e:
        db.rollback()
        flash(f'Error approving loan: {str(e)}', 'danger')
    return redirect(url_for('loans.loans_list'))


@loans.route('/loans/reject/<int:loan_id>', methods=['POST'])
@login_required
@role_required('admin', 'treasurer')
def reject_loan(loan_id):
    db = get_db()
    try:
        loan = db.execute('SELECT * FROM loans WHERE id = ?', (loan_id,)).fetchone()
        if not loan:
            flash('Loan not found', 'danger')
            return redirect(url_for('loans.loans_list'))

        db.execute("UPDATE loans SET status = 'rejected' WHERE id = ?", (loan_id,))
        db.commit()

        member = db.execute('SELECT * FROM members WHERE id = ?', (loan['member_id'],)).fetchone()
        reason = request.form.get('reason', 'Does not meet our lending criteria.')
        if member and member['email']:
            send_loan_rejection_email(member['email'], member, reason)
            notify_member(db, member['email'],
                          'Loan Application Update',
                          f"Your loan application could not be approved at this time. "
                          f"Reason: {reason}",
                          notification_type='warning',
                          action_url='/my-loans')
        audit(db, 'REJECT_LOAN', 'loans', f"Rejected loan ID {loan_id} – reason: {reason}")
        flash('Loan application rejected', 'info')
    except Exception as e:
        db.rollback()
        flash(f'Error rejecting loan: {str(e)}', 'danger')
    return redirect(url_for('loans.loans_list'))


@loans.route('/loans/bulk-repayments', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'treasurer')
def bulk_loan_repayments():
    if request.method == 'POST':
        if 'file' not in request.files or request.files['file'].filename == '':
            flash('No file selected', 'danger')
            return redirect(request.url)

        file = request.files['file']
        if not file.filename.lower().endswith('.csv'):
            flash('Please upload a CSV file', 'danger')
            return redirect(request.url)

        try:
            stream = TextIOWrapper(file.stream, encoding='utf-8')
            reader = csv.DictReader(stream)

            required = {'loan_number', 'amount', 'payment_date'}
            if not required.issubset(reader.fieldnames or []):
                missing = required - set(reader.fieldnames or [])
                flash(f'Missing columns: {", ".join(missing)}', 'danger')
                return redirect(request.url)

            db = get_db()
            success = 0
            errors = []

            for row_num, row in enumerate(reader, start=2):
                try:
                    loan_number = row.get('loan_number', '').strip()
                    amount = float(row.get('amount', 0))
                    payment_date_str = row.get('payment_date', '').strip()
                    payment_method = row.get('payment_method', 'cash').strip().lower()
                    receipt_number = row.get('receipt_number', '').strip()
                    notes = row.get('notes', '').strip()

                    if not loan_number or amount <= 0:
                        errors.append(f"Row {row_num}: Invalid loan number or amount")
                        continue

                    try:
                        payment_date = datetime.strptime(payment_date_str, '%Y-%m-%d')
                    except ValueError:
                        errors.append(f"Row {row_num}: Invalid date format (use YYYY-MM-DD)")
                        continue

                    loan = db.execute('''
                        SELECT l.*, m.first_name, m.last_name, m.email
                        FROM loans l
                        JOIN members m ON m.id = l.member_id
                        WHERE l.loan_number = ?
                    ''', (loan_number,)).fetchone()
                    if not loan:
                        errors.append(f"Row {row_num}: Loan number {loan_number} not found")
                        continue

                    # Pre-liquidation: if amount >= balance, settle in full
                    is_pre_liquidation = amount >= loan['balance']
                    settled_amount = loan['balance'] if is_pre_liquidation else amount

                    repayment_number = f"REP/{datetime.now().strftime('%Y%m%d')}/{row_num:04d}"
                    repayment_notes = notes
                    if is_pre_liquidation:
                        repayment_notes = ('Pre-liquidation – loan settled in full. ' + (notes or '')).strip()

                    principal_paid, interest_paid = split_repayment(
                        settled_amount, loan['amount'], loan['total_repayment'])

                    db.execute('''
                        INSERT INTO repayments (
                            repayment_number, loan_id, amount, principal_paid, interest_paid,
                            payment_method, receipt_number, notes, date
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (repayment_number, loan['id'], settled_amount, principal_paid, interest_paid,
                          payment_method, receipt_number, repayment_notes, payment_date))

                    new_balance = loan['balance'] - settled_amount
                    status = 'completed' if new_balance <= 0 else 'active'
                    completed_at = payment_date if status == 'completed' else None
                    db.execute(
                        'UPDATE loans SET balance = ?, status = ?, completed_at = ? WHERE id = ?',
                        (new_balance, status, completed_at, loan['id'])
                    )
                    post_journal_safe(db, f"Loan repayment — {loan_number}", [
                        {'account': CASH, 'debit': settled_amount, 'memo': 'Repayment'},
                        {'account': LOANS_RECEIVABLE, 'credit': principal_paid, 'memo': loan_number},
                        {'account': LOAN_INTEREST_INCOME, 'credit': interest_paid, 'memo': 'Interest earned'},
                    ], date=payment_date, reference=repayment_number, source_module='loans',
                       source_id=loan['id'], created_by=current_user.id)
                    if loan['email']:
                        send_loan_repayment_email(
                            loan['email'],
                            {'first_name': loan['first_name'], 'last_name': loan['last_name']},
                            loan,
                            {
                                'repayment_number': repayment_number,
                                'amount': settled_amount,
                                'principal_paid': principal_paid,
                                'interest_paid': interest_paid,
                                'balance': max(new_balance, 0),
                                'date': payment_date.strftime('%Y-%m-%d'),
                            },
                            url_for('portal.my_loans', _external=True),
                        )
                    if is_pre_liquidation:
                        errors.append(
                            f"Row {row_num}: Note — ₦{amount:,.2f} entered but only "
                            f"₦{settled_amount:,.2f} (outstanding balance) was recorded. "
                            f"Loan {loan_number} marked as completed."
                        )
                    success += 1
                except Exception as e:
                    errors.append(f"Row {row_num}: {str(e)}")

            db.commit()
            if errors:
                flash(f'Processed {success} repayments. {len(errors)} errors:', 'warning')
                for err in errors[:5]:
                    flash(err, 'danger')
            else:
                flash(f'Successfully recorded {success} loan repayments!', 'success')

        except Exception as e:
            flash(f'Error processing file: {str(e)}', 'danger')

        return redirect(url_for('loans.loans_list'))

    return render_template('admin/bulk-repayments.html')


@loans.route('/loans/export')
@login_required
@role_required('admin', 'treasurer')
def export_loans():
    db = get_db()
    all_loans = db.execute('''
        SELECT l.loan_number, m.first_name || ' ' || m.last_name AS member_name,
               l.amount, l.balance, l.status, l.date_applied
        FROM loans l
        JOIN members m ON l.member_id = m.id
        ORDER BY l.date_applied DESC
    ''').fetchall()

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(['Loan Number', 'Member Name', 'Original Amount',
                     'Outstanding Balance', 'Status', 'Date Applied'])
    for loan in all_loans:
        writer.writerow([
            loan['loan_number'],
            loan['member_name'],
            f"₦{loan['amount']:,.2f}",
            f"₦{loan['balance']:,.2f}",
            loan['status'],
            loan['date_applied'][:10] if loan['date_applied'] else '',
        ])

    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv'
    response.headers['Content-Disposition'] = 'attachment; filename=loans_export.csv'
    return response


@loans.route('/loans/repay/<int:loan_id>', methods=['POST'])
@login_required
@role_required('admin', 'treasurer')
def repay_loan(loan_id):
    db = get_db()
    try:
        loan = db.execute('SELECT * FROM loans WHERE id = ?', (loan_id,)).fetchone()
        if not loan:
            flash('Loan not found.', 'danger')
            return redirect(url_for('loans.loans_list'))

        if loan['status'] != 'active':
            flash('Only active loans can receive repayments.', 'warning')
            return redirect(url_for('loans.loans_list'))

        amount = float(request.form.get('amount', 0))
        method = request.form.get('method', 'cash')

        if amount <= 0:
            flash('Payment amount must be greater than zero.', 'danger')
            return redirect(url_for('loans.loans_list'))

        # Cap payment at outstanding balance (pre-liquidation)
        is_pre_liq = amount >= loan['balance']
        settled    = loan['balance'] if is_pre_liq else amount

        repayment_number = f"REP/{datetime.now().strftime('%Y%m%d%H%M%S')}/{loan_id}"
        notes = 'Pre-liquidation – loan settled in full.' if is_pre_liq else ''
        principal_paid, interest_paid = split_repayment(
            settled, loan['amount'], loan['total_repayment'])

        db.execute('''
            INSERT INTO repayments
                (repayment_number, loan_id, amount, principal_paid, interest_paid,
                 payment_method, notes, date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (repayment_number, loan_id, settled, principal_paid, interest_paid,
              method, notes, datetime.now()))

        new_balance = loan['balance'] - settled
        new_status  = 'completed' if new_balance <= 0 else 'active'
        completed_at = datetime.now() if new_status == 'completed' else None

        db.execute(
            'UPDATE loans SET balance = ?, status = ?, completed_at = ? WHERE id = ?',
            (new_balance, new_status, completed_at, loan_id)
        )

        # Double-entry: cash in; principal reduces the receivable; interest is income.
        post_journal_safe(db, f"Loan repayment — {loan['loan_number']}", [
            {'account': CASH, 'debit': settled, 'memo': 'Repayment'},
            {'account': LOANS_RECEIVABLE, 'credit': principal_paid, 'memo': loan['loan_number']},
            {'account': LOAN_INTEREST_INCOME, 'credit': interest_paid, 'memo': 'Interest earned'},
        ], reference=repayment_number, source_module='loans',
           source_id=loan_id, created_by=current_user.id)
        db.commit()

        member = db.execute('SELECT * FROM members WHERE id = ?', (loan['member_id'],)).fetchone()
        if member and member['email']:
            send_loan_repayment_email(
                member['email'],
                member,
                loan,
                {
                    'repayment_number': repayment_number,
                    'amount': settled,
                    'principal_paid': principal_paid,
                    'interest_paid': interest_paid,
                    'balance': max(new_balance, 0),
                    'date': datetime.now().strftime('%Y-%m-%d'),
                },
                url_for('portal.my_loans', _external=True),
            )
            notify_member(db, member['email'],
                          'Loan Repayment Recorded',
                          f"A repayment of ₦{settled:,.2f} has been recorded on your loan. "
                          f"Outstanding balance: ₦{new_balance:,.2f}.",
                          notification_type='info',
                          action_url='/my-loans')

        audit(db, 'LOAN_REPAYMENT', 'loans',
              f"Recorded repayment ₦{settled:,.2f} for loan ID {loan_id} – balance now ₦{new_balance:,.2f}")

        if is_pre_liq:
            flash(f'Loan fully settled! ₦{settled:,.2f} recorded.', 'success')
        else:
            flash(f'Repayment of ₦{settled:,.2f} recorded. Balance: ₦{new_balance:,.2f}', 'success')

    except Exception as e:
        db.rollback()
        flash(f'Error recording repayment: {str(e)}', 'danger')

    return redirect(url_for('loans.loans_list'))
