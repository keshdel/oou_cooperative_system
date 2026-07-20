import csv
import random
from datetime import datetime
from io import StringIO, TextIOWrapper

from flask import Blueprint, render_template, redirect, url_for, request, flash, make_response
from flask_login import login_required, current_user

from database import get_db, last_insert_id
from email_service import send_payment_confirmation_email
from utils import role_required, audit, notify_member, record_revenue, share_capital_split
from ledger import post_journal_safe, CASH, MEMBER_DEPOSITS, FEE_INCOME, SHARE_CAPITAL

savings = Blueprint('savings', __name__)


def _parse_date(raw):
    raw = (raw or '').strip()
    if not raw:
        return datetime.now()
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y'):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(raw.split('.')[0].replace('T', ' '))
    except ValueError:
        return None


def _resolve_member(db, row):
    member_number = (row.get('member_number') or row.get('member_no') or '').strip()
    email = (row.get('email') or '').strip()
    employee_id = (row.get('employee_id') or '').strip()
    phone = (row.get('phone') or '').strip()
    if member_number:
        found = db.execute('SELECT * FROM members WHERE member_number = ?', (member_number,)).fetchone()
        if found:
            return found
    if employee_id:
        found = db.execute('SELECT * FROM members WHERE employee_id = ?', (employee_id,)).fetchone()
        if found:
            return found
    if email:
        found = db.execute('SELECT * FROM members WHERE email = ?', (email,)).fetchone()
        if found:
            return found
    if phone:
        return db.execute('SELECT * FROM members WHERE phone = ?', (phone,)).fetchone()
    return None


def _batch_ref(month):
    suffix = random.randint(1000, 9999)
    return f"SAL-SAV/{month.replace('-', '')}/{datetime.now().strftime('%H%M%S')}/{suffix}"


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
    batches = db.execute('''
        SELECT import_batch, source_file, MIN(date) AS first_date, MAX(date) AS last_date,
               COUNT(*) AS row_count,
               COALESCE(SUM(amount), 0) AS total_amount,
               COALESCE(SUM(late_fee), 0) AS total_late_fee
        FROM savings
        WHERE import_batch IS NOT NULL AND import_batch != ''
        GROUP BY import_batch, source_file
        ORDER BY MAX(date) DESC, import_batch DESC
        LIMIT 10
    ''').fetchall()
    return render_template('admin/savings.html',
                           savings=all_savings,
                           total_savings=total_savings,
                           batches=batches)


def _batch_rows(db, batch_ref):
    return db.execute('''
        SELECT s.*, m.member_number, m.employee_id,
               m.first_name || ' ' || m.last_name AS member_name,
               m.email, m.phone,
               CASE WHEN je.id IS NULL THEN 0 ELSE 1 END AS posted_to_gl,
               je.entry_number AS journal_entry_number,
               je.id AS journal_entry_id
        FROM savings s
        JOIN members m ON m.id = s.member_id
        LEFT JOIN journal_entries je ON je.reference = s.receipt_number
        WHERE s.import_batch = ?
        ORDER BY s.date ASC, s.id ASC
    ''', (batch_ref,)).fetchall()


@savings.route('/savings/batch/<path:batch_ref>')
@login_required
@role_required('admin', 'treasurer', 'secretary', 'exco')
def salary_batch_detail(batch_ref):
    db = get_db()
    rows = _batch_rows(db, batch_ref)
    if not rows:
        flash('Savings batch not found.', 'warning')
        return redirect(url_for('savings.savings_list'))

    summary = {
        'batch_ref': batch_ref,
        'source_file': rows[0]['source_file'] or '',
        'row_count': len(rows),
        'total_amount': sum(float(r['amount'] or 0) for r in rows),
        'total_late_fee': sum(float(r['late_fee'] or 0) for r in rows),
        'posted_count': sum(1 for r in rows if r['posted_to_gl']),
        'first_date': rows[0]['date'],
        'last_date': rows[-1]['date'],
    }
    return render_template('admin/salary-savings-batch.html',
                           batch=summary,
                           rows=rows)


@savings.route('/savings/batch/<path:batch_ref>/export')
@login_required
@role_required('admin', 'treasurer', 'secretary', 'exco')
def salary_batch_export(batch_ref):
    db = get_db()
    rows = _batch_rows(db, batch_ref)
    if not rows:
        flash('Savings batch not found.', 'warning')
        return redirect(url_for('savings.savings_list'))

    out = StringIO()
    writer = csv.writer(out)
    writer.writerow([
        'batch_ref', 'member_number', 'employee_id', 'name', 'email',
        'month', 'date', 'receipt_number', 'amount', 'late_fee',
        'total_paid', 'posted_to_gl', 'journal_entry',
    ])
    for r in rows:
        writer.writerow([
            batch_ref, r['member_number'], r['employee_id'], r['member_name'],
            r['email'], r['month'], str(r['date'])[:10], r['receipt_number'],
            f"{float(r['amount'] or 0):.2f}", f"{float(r['late_fee'] or 0):.2f}",
            f"{float(r['amount'] or 0) + float(r['late_fee'] or 0):.2f}",
            'yes' if r['posted_to_gl'] else 'no', r['journal_entry_number'] or '',
        ])
    response = make_response(out.getvalue())
    response.headers['Content-Type'] = 'text/csv'
    safe_name = batch_ref.replace('/', '_').replace('\\', '_')
    response.headers['Content-Disposition'] = f'attachment; filename=savings_batch_{safe_name}.csv'
    return response


@savings.route('/savings/salary-template')
@login_required
@role_required('admin', 'treasurer')
def download_salary_template():
    out = StringIO()
    writer = csv.writer(out)
    writer.writerow([
        'member_number', 'employee_id', 'email', 'phone', 'amount',
        'month', 'date', 'receipt_number', 'notes',
    ])
    writer.writerow([
        'MEM/2025/0001', 'EMP001', 'member@example.com', '08012345678',
        '15000', datetime.now().strftime('%Y-%m'), datetime.now().strftime('%Y-%m-%d'),
        '', 'July payroll deduction',
    ])
    response = make_response(out.getvalue())
    response.headers['Content-Type'] = 'text/csv'
    response.headers['Content-Disposition'] = 'attachment; filename=salary_savings_template.csv'
    return response


@savings.route('/savings/salary-upload', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'treasurer')
def salary_upload():
    if request.method == 'POST':
        file = request.files.get('file')
        month = request.form.get('month', '').strip()
        batch_ref = request.form.get('batch_ref', '').strip() or _batch_ref(month or datetime.now().strftime('%Y-%m'))
        apply_late_fee = bool(request.form.get('apply_late_fee'))

        if not month:
            flash('Payroll month is required.', 'danger')
            return redirect(request.url)
        if not file or not file.filename:
            flash('No CSV file selected.', 'danger')
            return redirect(request.url)
        if not file.filename.lower().endswith('.csv'):
            flash('Please upload a CSV file.', 'danger')
            return redirect(request.url)

        db = get_db()
        success = 0
        skipped = 0
        errors = []
        try:
            stream = TextIOWrapper(file.stream, encoding='utf-8-sig')
            reader = csv.DictReader(stream)
            fields = set(reader.fieldnames or [])
            if 'amount' not in fields:
                flash('CSV must include an amount column.', 'danger')
                return redirect(request.url)
            if not fields.intersection({'member_number', 'employee_id', 'email', 'phone'}):
                flash('CSV must include at least one member identifier: member_number, employee_id, email, or phone.', 'danger')
                return redirect(request.url)

            for row_num, row in enumerate(reader, start=2):
                try:
                    member = _resolve_member(db, row)
                    if not member:
                        errors.append(f"Row {row_num}: member not found.")
                        continue

                    amount = float((row.get('amount') or '').replace(',', '').strip())
                    if amount <= 0:
                        errors.append(f"Row {row_num}: amount must be greater than zero.")
                        continue

                    payment_date = _parse_date(row.get('date', '')) or datetime.now()
                    row_month = (row.get('month') or month).strip() or month
                    notes = (row.get('notes') or '').strip() or f'Salary deduction batch {batch_ref}'
                    receipt_number = (row.get('receipt_number') or '').strip()
                    if not receipt_number:
                        receipt_number = f"PAYROLL/{row_month.replace('-', '')}/{batch_ref.split('/')[-1]}/{row_num:04d}"

                    exists = db.execute(
                        'SELECT id FROM savings WHERE receipt_number = ? OR (import_batch = ? AND member_id = ? AND month = ?)',
                        (receipt_number, batch_ref, member['id'], row_month),
                    ).fetchone()
                    if exists:
                        skipped += 1
                        continue

                    late_fee = 0.0
                    if apply_late_fee and payment_date.day > 10:
                        late_fee = round(amount * 0.10, 2)

                    deposit_amount, share_amount = share_capital_split(db, amount)

                    db.execute('''
                        INSERT INTO savings
                            (member_id, amount, share_capital, month, payment_type, late_fee,
                             payment_method, receipt_number, notes, date,
                             created_by, import_batch, source_file)
                        VALUES (?, ?, ?, ?, 'salary', ?, 'salary_deduction',
                                ?, ?, ?, ?, ?, ?)
                    ''', (
                        member['id'], deposit_amount, share_amount, row_month, late_fee,
                        receipt_number, notes, payment_date, current_user.id,
                        batch_ref, file.filename,
                    ))
                    sav_id = last_insert_id(db)   # before any revenue/GL INSERT
                    db.execute(
                        'UPDATE members SET total_savings = total_savings + ?, '
                        'shares_value = COALESCE(shares_value, 0) + ? WHERE id = ?',
                        (deposit_amount, share_amount, member['id']),
                    )

                    if late_fee:
                        record_revenue(
                            db, 'Late Fee', late_fee,
                            description=f'Late salary deduction fee for {row_month}',
                            source=f"{member['first_name']} {member['last_name']}",
                            received_by=current_user.id,
                            notes=f'Receipt {receipt_number}; batch {batch_ref}',
                        )

                    lines = [
                        {'account': CASH, 'debit': amount + late_fee, 'memo': f'Salary savings {row_month}'},
                        {'account': MEMBER_DEPOSITS, 'credit': deposit_amount, 'memo': f"Member {member['id']}"},
                    ]
                    if share_amount:
                        lines.append({'account': SHARE_CAPITAL, 'credit': share_amount, 'memo': 'Share capital'})
                    if late_fee:
                        lines.append({'account': FEE_INCOME, 'credit': late_fee, 'memo': 'Late fee'})
                    post_journal_safe(
                        db, f'Salary savings deduction - {row_month}', lines,
                        date=payment_date, reference=receipt_number,
                        source_module='savings_deposit', source_id=sav_id,
                        created_by=current_user.id,
                    )

                    if member['email']:
                        notify_member(
                            db, member['email'], 'Salary Savings Recorded',
                            f"Salary savings of ₦{amount:,.2f} was recorded for {row_month}. "
                            f"Receipt: {receipt_number}.",
                            notification_type='info', action_url='/my-savings',
                        )
                    success += 1
                except Exception as row_error:
                    errors.append(f"Row {row_num}: {row_error}")

            db.commit()
            audit(db, 'IMPORT_SALARY_SAVINGS', 'savings',
                  f"Batch {batch_ref}: imported {success}, skipped {skipped}, errors {len(errors)}")
            flash(f'Batch {batch_ref}: imported {success} salary savings record(s), skipped {skipped}.', 'success')
            for error in errors[:8]:
                flash(error, 'warning')
            if len(errors) > 8:
                flash(f'{len(errors) - 8} additional row error(s) not shown.', 'warning')
            return redirect(url_for('savings.salary_batch_detail', batch_ref=batch_ref))
        except Exception as e:
            db.rollback()
            flash(f'Error processing salary deduction file: {e}', 'danger')
            return redirect(request.url)

    return render_template('admin/salary-savings-upload.html',
                           default_month=datetime.now().strftime('%Y-%m'),
                           default_batch=_batch_ref(datetime.now().strftime('%Y-%m')))


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

        # Allocate a configurable portion of the contribution to share capital.
        deposit_amount, share_amount = share_capital_split(db, amount)

        # savings.amount is the deposit portion; share_capital records the split.
        db.execute('''
            INSERT INTO savings
                (member_id, amount, share_capital, month, payment_type, late_fee,
                 payment_method, receipt_number, notes, date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (member_id, deposit_amount, share_amount, month, payment_type, late_fee,
              payment_method, receipt_number, notes, today))
        sav_id = last_insert_id(db)   # captured before any other INSERT (revenue/GL)

        # Deposits grow by the deposit portion; share capital by the share portion.
        db.execute(
            'UPDATE members SET total_savings = total_savings + ?, '
            'shares_value = COALESCE(shares_value, 0) + ? WHERE id = ?',
            (deposit_amount, share_amount, member_id))

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

        # Double-entry: cash in; deposit liability + share capital up; fee is income.
        _lines = [
            {'account': CASH, 'debit': amount + late_fee, 'memo': f'Savings {month}'},
            {'account': MEMBER_DEPOSITS, 'credit': deposit_amount, 'memo': f'Member {member_id}'},
        ]
        if share_amount:
            _lines.append({'account': SHARE_CAPITAL, 'credit': share_amount, 'memo': 'Share capital'})
        if late_fee:
            _lines.append({'account': FEE_INCOME, 'credit': late_fee, 'memo': 'Late fee'})
        post_journal_safe(db, f'Savings deposit — {month}', _lines,
                          reference=receipt_number, source_module='savings_deposit',
                          source_id=sav_id, created_by=current_user.id)

        db.commit()

        saving_id = sav_id   # the savings row id (captured above, before GL posting)
        member    = db.execute('SELECT * FROM members WHERE id = ?', (member_id,)).fetchone()
        new_saving = db.execute('SELECT * FROM savings WHERE id = ?', (saving_id,)).fetchone()
        share_note = f" (₦{deposit_amount:,.2f} to savings, ₦{share_amount:,.2f} to share capital)" if share_amount else ""
        if member and member['email']:
            send_payment_confirmation_email(member['email'], member, new_saving)
            fee_note = f" (plus ₦{late_fee:,.2f} late fee)" if late_fee else ""
            notify_member(db, member['email'],
                          'Savings Payment Confirmed',
                          f"₦{amount:,.2f} {payment_type} contribution recorded for "
                          f"{month}{share_note}{fee_note}. Receipt: {receipt_number}.",
                          notification_type='info',
                          action_url='/my-savings')

        audit(db, 'ADD_SAVING', 'savings',
              f"Recorded ₦{amount:,.2f} {payment_type} contribution for member ID {member_id}, "
              f"receipt {receipt_number}{share_note}")
        flash(f'Contribution of ₦{amount:,.2f} recorded{share_note}. Receipt: {receipt_number}', 'success')

    except Exception as e:
        db.rollback()
        flash(f'Error recording savings: {str(e)}', 'danger')

    return redirect(url_for('members.member_details', member_id=member_id))
