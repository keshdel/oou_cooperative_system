"""
Data Migration Blueprint
Handles bulk import/export for migrating from the old cooperative app.
All imports use atomic transactions — the whole file succeeds or rolls back.
"""
import csv
import random
from datetime import datetime
from io import StringIO, TextIOWrapper

from flask import Blueprint, render_template, redirect, url_for, request, flash, make_response
from flask_login import login_required

from database import get_db
from utils import role_required, audit

migration = Blueprint('migration', __name__, url_prefix='/migration')

# ── Helper ────────────────────────────────────────────────────────────────────

def _resolve_member(db, row):
    """Return member row from member_number or email column, or None."""
    member_number = (row.get('member_number') or '').strip()
    email = (row.get('email') or '').strip()
    if member_number:
        m = db.execute('SELECT * FROM members WHERE member_number = ?', (member_number,)).fetchone()
        if m:
            return m
    if email:
        m = db.execute('SELECT * FROM members WHERE email = ?', (email,)).fetchone()
        if m:
            return m
    return None


def _ref(prefix):
    return f"{prefix}/{datetime.now().strftime('%Y%m%d')}/{random.randint(1000, 9999)}"


# ── Dashboard ─────────────────────────────────────────────────────────────────

@migration.route('/')
@login_required
@role_required('admin')
def index():
    db = get_db()
    counts = {
        'members':     db.execute('SELECT COUNT(*) FROM members').fetchone()[0],
        'savings':     db.execute('SELECT COUNT(*) FROM savings').fetchone()[0],
        'loans':       db.execute('SELECT COUNT(*) FROM loans').fetchone()[0],
        'repayments':  db.execute('SELECT COUNT(*) FROM repayments').fetchone()[0],
        'expenses':    db.execute('SELECT COUNT(*) FROM expenses').fetchone()[0],
        'revenue':     db.execute('SELECT COUNT(*) FROM revenue').fetchone()[0],
        'investments': db.execute('SELECT COUNT(*) FROM investments').fetchone()[0],
        'honorarium':  db.execute('SELECT COUNT(*) FROM honorarium').fetchone()[0],
    }
    return render_template('admin/migration/index.html', counts=counts)


# ═══════════════════════════════════════════════════════════════════════════════
# MEMBERS
# ═══════════════════════════════════════════════════════════════════════════════

MEMBERS_COLUMNS = [
    'first_name', 'last_name', 'email', 'phone',
    'member_number', 'date_joined', 'monthly_savings',
    'address', 'occupation', 'date_of_birth', 'status',
    'nominee_name', 'nominee_relationship', 'nominee_phone',
    'bank_name', 'account_number', 'account_name',
    'emergency_contact_name', 'emergency_contact_phone',
]

MEMBERS_REQUIRED = {'first_name', 'last_name', 'phone'}


@migration.route('/members', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'secretary')
def import_members():
    if request.method == 'POST':
        f = request.files.get('file')
        if not f or not f.filename:
            flash('No file selected.', 'danger')
            return redirect(request.url)
        if not f.filename.lower().endswith('.csv'):
            flash('Please upload a CSV file.', 'danger')
            return redirect(request.url)

        db = get_db()
        try:
            stream = TextIOWrapper(f.stream, encoding='utf-8-sig')
            reader = csv.DictReader(stream)
            missing_req = MEMBERS_REQUIRED - set(reader.fieldnames or [])
            if missing_req:
                flash(f'Missing required columns: {", ".join(sorted(missing_req))}', 'danger')
                return redirect(request.url)

            success, skipped, errors = 0, 0, []

            for row_num, row in enumerate(reader, start=2):
                try:
                    first_name = row.get('first_name', '').strip()
                    last_name  = row.get('last_name', '').strip()
                    phone      = row.get('phone', '').strip()
                    if not all([first_name, last_name, phone]):
                        errors.append(f"Row {row_num}: first_name, last_name, phone are required.")
                        continue

                    email = row.get('email', '').strip() or None
                    if email:
                        dup = db.execute('SELECT id FROM members WHERE email = ?', (email,)).fetchone()
                        if dup:
                            skipped += 1
                            continue  # silently skip duplicate email

                    member_number = row.get('member_number', '').strip() or None
                    if member_number:
                        dup = db.execute(
                            'SELECT id FROM members WHERE member_number = ?', (member_number,)
                        ).fetchone()
                        if dup:
                            skipped += 1
                            continue

                    date_joined_raw = row.get('date_joined', '').strip()
                    date_joined = None
                    if date_joined_raw:
                        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%d-%m-%Y'):
                            try:
                                date_joined = datetime.strptime(date_joined_raw, fmt)
                                break
                            except ValueError:
                                pass
                        if date_joined is None:
                            errors.append(f"Row {row_num}: unrecognised date_joined '{date_joined_raw}'.")
                            continue

                    monthly_savings_raw = row.get('monthly_savings', '').strip()
                    monthly_savings = float(monthly_savings_raw) if monthly_savings_raw else 5000.0

                    db.execute('''
                        INSERT INTO members (
                            member_number, first_name, last_name, email, phone,
                            address, occupation, date_of_birth, date_joined, monthly_savings,
                            status, nominee_name, nominee_relationship, nominee_phone,
                            bank_name, account_number, account_name,
                            emergency_contact_name, emergency_contact_phone
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        member_number,
                        first_name,
                        last_name,
                        email,
                        phone,
                        row.get('address', '').strip() or None,
                        row.get('occupation', '').strip() or None,
                        row.get('date_of_birth', '').strip() or None,
                        date_joined or datetime.now(),
                        monthly_savings,
                        row.get('status', 'active').strip() or 'active',
                        row.get('nominee_name', '').strip() or None,
                        row.get('nominee_relationship', '').strip() or None,
                        row.get('nominee_phone', '').strip() or None,
                        row.get('bank_name', '').strip() or None,
                        row.get('account_number', '').strip() or None,
                        row.get('account_name', '').strip() or None,
                        row.get('emergency_contact_name', '').strip() or None,
                        row.get('emergency_contact_phone', '').strip() or None,
                    ))

                    # Auto-generate member_number if not supplied
                    if not member_number:
                        new_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
                        mn = f"OOU/{(date_joined or datetime.now()).year}/{new_id:04d}"
                        db.execute('UPDATE members SET member_number = ? WHERE id = ?', (mn, new_id))

                    success += 1
                except Exception as e:
                    errors.append(f"Row {row_num}: {e}")

            db.commit()
            audit(db, 'IMPORT_MEMBERS', 'migration',
                  f"Imported {success} members, skipped {skipped} duplicates, {len(errors)} errors")

        except Exception as e:
            db.rollback()
            flash(f'File processing error: {e}', 'danger')
            return redirect(request.url)

        _flash_result(success, skipped, errors, 'member')
        return redirect(url_for('migration.index'))

    return render_template('admin/migration/import.html',
                           entity='members',
                           title='Import Members',
                           required_cols=sorted(MEMBERS_REQUIRED),
                           optional_cols=[c for c in MEMBERS_COLUMNS if c not in MEMBERS_REQUIRED],
                           template_url=url_for('migration.template_members'),
                           back_url=url_for('migration.index'))


@migration.route('/members/template')
@login_required
@role_required('admin', 'secretary')
def template_members():
    out = StringIO()
    w = csv.writer(out)
    w.writerow(MEMBERS_COLUMNS)
    w.writerow(['John', 'Doe', 'john@example.com', '08012345678',
                'OOU/2024/0001', '2020-01-15', '5000',
                'Lagos', 'Teacher', '1985-06-20', 'active',
                'Mary Doe', 'Spouse', '08099991111',
                'GTBank', '0123456789', 'John Doe',
                'James Doe', '08011112222'])
    w.writerow(['Jane', 'Smith', 'jane@example.com', '08087654321',
                '', '2021-03-01', '10000',
                'Ibadan', 'Engineer', '1990-11-05', 'active',
                '', '', '',
                'Access Bank', '9876543210', 'Jane Smith',
                '', ''])
    return _csv_response(out, 'members_import_template.csv')


# ── Members export ────────────────────────────────────────────────────────────

@migration.route('/members/export')
@login_required
@role_required('admin', 'secretary')
def export_members():
    db = get_db()
    rows = db.execute('''
        SELECT member_number, first_name, last_name, email, phone,
               address, occupation, date_of_birth, date_joined, monthly_savings,
               total_savings, status, nominee_name, nominee_relationship, nominee_phone,
               bank_name, account_number, account_name,
               emergency_contact_name, emergency_contact_phone
        FROM members ORDER BY member_number
    ''').fetchall()
    out = StringIO()
    w = csv.writer(out)
    w.writerow([
        'member_number', 'first_name', 'last_name', 'email', 'phone',
        'address', 'occupation', 'date_of_birth', 'date_joined', 'monthly_savings',
        'total_savings', 'status', 'nominee_name', 'nominee_relationship', 'nominee_phone',
        'bank_name', 'account_number', 'account_name',
        'emergency_contact_name', 'emergency_contact_phone',
    ])
    for r in rows:
        w.writerow(list(r))
    return _csv_response(out, 'members_export.csv')


# ═══════════════════════════════════════════════════════════════════════════════
# SAVINGS
# ═══════════════════════════════════════════════════════════════════════════════

SAVINGS_COLUMNS = [
    'member_number', 'email', 'amount', 'month',
    'late_fee', 'payment_method', 'receipt_number', 'date', 'notes',
]
SAVINGS_REQUIRED = {'amount', 'month'}


@migration.route('/savings', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'treasurer')
def import_savings():
    if request.method == 'POST':
        f = request.files.get('file')
        if not f or not f.filename:
            flash('No file selected.', 'danger')
            return redirect(request.url)
        if not f.filename.lower().endswith('.csv'):
            flash('Please upload a CSV file.', 'danger')
            return redirect(request.url)

        db = get_db()
        try:
            stream = TextIOWrapper(f.stream, encoding='utf-8-sig')
            reader = csv.DictReader(stream)
            fieldnames = set(reader.fieldnames or [])
            if not SAVINGS_REQUIRED.issubset(fieldnames):
                flash(f'Missing columns: {", ".join(sorted(SAVINGS_REQUIRED - fieldnames))}', 'danger')
                return redirect(request.url)
            if 'member_number' not in fieldnames and 'email' not in fieldnames:
                flash('CSV must have at least one of: member_number, email', 'danger')
                return redirect(request.url)

            success, skipped, errors = 0, 0, []

            for row_num, row in enumerate(reader, start=2):
                try:
                    member = _resolve_member(db, row)
                    if not member:
                        errors.append(f"Row {row_num}: member not found "
                                      f"(member_number={row.get('member_number','')!r}, "
                                      f"email={row.get('email','')!r}).")
                        continue

                    amount_raw = row.get('amount', '').strip()
                    if not amount_raw:
                        errors.append(f"Row {row_num}: amount is required.")
                        continue
                    amount = float(amount_raw)

                    month = row.get('month', '').strip()
                    if not month:
                        errors.append(f"Row {row_num}: month is required (format YYYY-MM).")
                        continue

                    # Skip duplicate (same member + same month)
                    dup = db.execute(
                        'SELECT id FROM savings WHERE member_id = ? AND month = ?',
                        (member['id'], month)
                    ).fetchone()
                    if dup:
                        skipped += 1
                        continue

                    late_fee = float(row.get('late_fee', '0').strip() or 0)
                    payment_method = row.get('payment_method', 'cash').strip() or 'cash'
                    receipt_number = row.get('receipt_number', '').strip() or _ref('RCPT')
                    notes = row.get('notes', '').strip() or None

                    date_raw = row.get('date', '').strip()
                    date = _parse_date(date_raw) or datetime.now()

                    db.execute('''
                        INSERT INTO savings
                            (member_id, amount, month, late_fee, payment_method,
                             receipt_number, notes, date)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (member['id'], amount, month, late_fee,
                          payment_method, receipt_number, notes, date))

                    db.execute(
                        'UPDATE members SET total_savings = total_savings + ? WHERE id = ?',
                        (amount, member['id'])
                    )
                    success += 1
                except Exception as e:
                    errors.append(f"Row {row_num}: {e}")

            db.commit()
            audit(db, 'IMPORT_SAVINGS', 'migration',
                  f"Imported {success} savings records, skipped {skipped}, {len(errors)} errors")

        except Exception as e:
            db.rollback()
            flash(f'File processing error: {e}', 'danger')
            return redirect(request.url)

        _flash_result(success, skipped, errors, 'savings record')
        return redirect(url_for('migration.index'))

    return render_template('admin/migration/import.html',
                           entity='savings',
                           title='Import Savings History',
                           required_cols=sorted(SAVINGS_REQUIRED) + ['member_number or email'],
                           optional_cols=[c for c in SAVINGS_COLUMNS
                                          if c not in SAVINGS_REQUIRED
                                          and c not in ('member_number', 'email')],
                           template_url=url_for('migration.template_savings'),
                           back_url=url_for('migration.index'))


@migration.route('/savings/template')
@login_required
@role_required('admin', 'treasurer')
def template_savings():
    out = StringIO()
    w = csv.writer(out)
    w.writerow(SAVINGS_COLUMNS)
    w.writerow(['OOU/2024/0001', 'john@example.com', '5000', '2024-01',
                '0', 'cash', 'RCPT/20240115/0001', '2024-01-10', ''])
    w.writerow(['OOU/2024/0002', 'jane@example.com', '10000', '2024-01',
                '1000', 'transfer', 'RCPT/20240118/0002', '2024-01-18', 'Late payment'])
    return _csv_response(out, 'savings_import_template.csv')


@migration.route('/savings/export')
@login_required
@role_required('admin', 'treasurer')
def export_savings():
    db = get_db()
    rows = db.execute('''
        SELECT m.member_number, m.email, s.amount, s.month, s.late_fee,
               s.payment_method, s.receipt_number, s.date, s.notes
        FROM savings s JOIN members m ON s.member_id = m.id
        ORDER BY m.member_number, s.month
    ''').fetchall()
    out = StringIO()
    w = csv.writer(out)
    w.writerow(['member_number', 'email', 'amount', 'month', 'late_fee',
                'payment_method', 'receipt_number', 'date', 'notes'])
    for r in rows:
        w.writerow(list(r))
    return _csv_response(out, 'savings_export.csv')


# ═══════════════════════════════════════════════════════════════════════════════
# LOANS
# ═══════════════════════════════════════════════════════════════════════════════

LOANS_COLUMNS = [
    'member_number', 'email', 'loan_number', 'amount', 'purpose',
    'tenure', 'interest_rate', 'total_repayment', 'balance', 'status',
    'date_applied', 'date_approved', 'notes',
]
LOANS_REQUIRED = {'amount', 'purpose'}


@migration.route('/loans', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'treasurer')
def import_loans():
    if request.method == 'POST':
        f = request.files.get('file')
        if not f or not f.filename:
            flash('No file selected.', 'danger')
            return redirect(request.url)
        if not f.filename.lower().endswith('.csv'):
            flash('Please upload a CSV file.', 'danger')
            return redirect(request.url)

        db = get_db()
        try:
            stream = TextIOWrapper(f.stream, encoding='utf-8-sig')
            reader = csv.DictReader(stream)
            fieldnames = set(reader.fieldnames or [])
            if not LOANS_REQUIRED.issubset(fieldnames):
                flash(f'Missing columns: {", ".join(sorted(LOANS_REQUIRED - fieldnames))}', 'danger')
                return redirect(request.url)
            if 'member_number' not in fieldnames and 'email' not in fieldnames:
                flash('CSV must have at least one of: member_number, email', 'danger')
                return redirect(request.url)

            success, skipped, errors = 0, 0, []

            for row_num, row in enumerate(reader, start=2):
                try:
                    member = _resolve_member(db, row)
                    if not member:
                        errors.append(f"Row {row_num}: member not found.")
                        continue

                    loan_number = row.get('loan_number', '').strip() or _ref('LOAN')
                    dup = db.execute(
                        'SELECT id FROM loans WHERE loan_number = ?', (loan_number,)
                    ).fetchone()
                    if dup:
                        skipped += 1
                        continue

                    amount = float(row.get('amount', 0))
                    if amount <= 0:
                        errors.append(f"Row {row_num}: amount must be > 0.")
                        continue

                    purpose    = row.get('purpose', '').strip() or 'General'
                    tenure     = int(row.get('tenure', '12').strip() or 12)
                    int_rate   = float(row.get('interest_rate', '11').strip() or 11)
                    total_rep  = float(row.get('total_repayment', '0').strip() or 0) or amount
                    balance    = float(row.get('balance', '0').strip() or 0) or total_rep
                    status     = row.get('status', 'pending').strip() or 'pending'
                    notes      = row.get('notes', '').strip() or None

                    date_applied  = _parse_date(row.get('date_applied', '')) or datetime.now()
                    date_approved = _parse_date(row.get('date_approved', ''))

                    db.execute('''
                        INSERT INTO loans
                            (loan_number, member_id, amount, purpose, tenure, interest_rate,
                             total_repayment, balance, status, notes, date_applied, approved_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (loan_number, member['id'], amount, purpose, tenure, int_rate,
                          total_rep, balance, status, notes, date_applied, date_approved))
                    success += 1
                except Exception as e:
                    errors.append(f"Row {row_num}: {e}")

            db.commit()
            audit(db, 'IMPORT_LOANS', 'migration',
                  f"Imported {success} loans, skipped {skipped}, {len(errors)} errors")

        except Exception as e:
            db.rollback()
            flash(f'File processing error: {e}', 'danger')
            return redirect(request.url)

        _flash_result(success, skipped, errors, 'loan')
        return redirect(url_for('migration.index'))

    return render_template('admin/migration/import.html',
                           entity='loans',
                           title='Import Loan Records',
                           required_cols=sorted(LOANS_REQUIRED) + ['member_number or email'],
                           optional_cols=[c for c in LOANS_COLUMNS
                                          if c not in LOANS_REQUIRED
                                          and c not in ('member_number', 'email')],
                           template_url=url_for('migration.template_loans'),
                           back_url=url_for('migration.index'))


@migration.route('/loans/template')
@login_required
@role_required('admin', 'treasurer')
def template_loans():
    out = StringIO()
    w = csv.writer(out)
    w.writerow(LOANS_COLUMNS)
    w.writerow(['OOU/2024/0001', 'john@example.com', 'LOAN/20240201/0001',
                '200000', 'Regular', '12', '11', '224000', '100000',
                'active', '2024-02-01', '2024-02-15', ''])
    w.writerow(['OOU/2024/0002', 'jane@example.com', '',
                '100000', 'Emergency', '6', '10', '105000', '105000',
                'pending', '2024-03-10', '', 'Awaiting guarantor'])
    return _csv_response(out, 'loans_import_template.csv')


@migration.route('/loans/export')
@login_required
@role_required('admin', 'treasurer')
def export_loans():
    db = get_db()
    rows = db.execute('''
        SELECT m.member_number, m.email, l.loan_number, l.amount, l.purpose,
               l.tenure, l.interest_rate, l.total_repayment, l.balance, l.status,
               l.date_applied, l.approved_at, l.notes
        FROM loans l JOIN members m ON l.member_id = m.id
        ORDER BY l.date_applied DESC
    ''').fetchall()
    out = StringIO()
    w = csv.writer(out)
    w.writerow(['member_number', 'email', 'loan_number', 'amount', 'purpose',
                'tenure', 'interest_rate', 'total_repayment', 'balance', 'status',
                'date_applied', 'date_approved', 'notes'])
    for r in rows:
        w.writerow(list(r))
    return _csv_response(out, 'loans_export.csv')


# ═══════════════════════════════════════════════════════════════════════════════
# EXPENSES
# ═══════════════════════════════════════════════════════════════════════════════

EXPENSES_COLUMNS = ['category', 'amount', 'description', 'vendor',
                    'payment_method', 'date', 'notes']
EXPENSES_REQUIRED = {'category', 'amount'}


@migration.route('/expenses', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'treasurer')
def import_expenses():
    if request.method == 'POST':
        f = request.files.get('file')
        if not f or not f.filename:
            flash('No file selected.', 'danger')
            return redirect(request.url)
        if not f.filename.lower().endswith('.csv'):
            flash('Please upload a CSV file.', 'danger')
            return redirect(request.url)

        db = get_db()
        try:
            stream = TextIOWrapper(f.stream, encoding='utf-8-sig')
            reader = csv.DictReader(stream)
            missing = EXPENSES_REQUIRED - set(reader.fieldnames or [])
            if missing:
                flash(f'Missing columns: {", ".join(sorted(missing))}', 'danger')
                return redirect(request.url)

            success, errors = 0, []

            for row_num, row in enumerate(reader, start=2):
                try:
                    category = row.get('category', '').strip()
                    amount_raw = row.get('amount', '').strip()
                    if not category or not amount_raw:
                        errors.append(f"Row {row_num}: category and amount are required.")
                        continue
                    amount = float(amount_raw)
                    date = _parse_date(row.get('date', '')) or datetime.now()
                    expense_number = _ref('EXP')
                    db.execute('''
                        INSERT INTO expenses
                            (expense_number, category, amount, description, vendor,
                             payment_method, date, notes)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (expense_number, category, amount,
                          row.get('description', '').strip() or None,
                          row.get('vendor', '').strip() or None,
                          row.get('payment_method', 'cash').strip() or 'cash',
                          date,
                          row.get('notes', '').strip() or None))
                    success += 1
                except Exception as e:
                    errors.append(f"Row {row_num}: {e}")

            db.commit()
            audit(db, 'IMPORT_EXPENSES', 'migration',
                  f"Imported {success} expenses, {len(errors)} errors")

        except Exception as e:
            db.rollback()
            flash(f'File processing error: {e}', 'danger')
            return redirect(request.url)

        _flash_result(success, 0, errors, 'expense')
        return redirect(url_for('migration.index'))

    return render_template('admin/migration/import.html',
                           entity='expenses',
                           title='Import Expenses',
                           required_cols=sorted(EXPENSES_REQUIRED),
                           optional_cols=[c for c in EXPENSES_COLUMNS if c not in EXPENSES_REQUIRED],
                           template_url=url_for('migration.template_expenses'),
                           back_url=url_for('migration.index'))


@migration.route('/expenses/template')
@login_required
@role_required('admin', 'treasurer')
def template_expenses():
    out = StringIO()
    w = csv.writer(out)
    w.writerow(EXPENSES_COLUMNS)
    w.writerow(['Stationery', '5000', 'Office supplies', 'Balogun Market',
                'cash', '2024-01-10', ''])
    w.writerow(['Utilities', '15000', 'January electricity bill', 'IBEDC',
                'transfer', '2024-01-20', 'Account: 9876543'])
    return _csv_response(out, 'expenses_import_template.csv')


@migration.route('/expenses/export')
@login_required
@role_required('admin', 'treasurer')
def export_expenses():
    db = get_db()
    rows = db.execute('''
        SELECT expense_number, category, amount, description, vendor,
               payment_method, date, notes
        FROM expenses ORDER BY date DESC
    ''').fetchall()
    out = StringIO()
    w = csv.writer(out)
    w.writerow(['expense_number', 'category', 'amount', 'description',
                'vendor', 'payment_method', 'date', 'notes'])
    for r in rows:
        w.writerow(list(r))
    return _csv_response(out, 'expenses_export.csv')


# ═══════════════════════════════════════════════════════════════════════════════
# REVENUE
# ═══════════════════════════════════════════════════════════════════════════════

REVENUE_COLUMNS = ['category', 'amount', 'description', 'source', 'date', 'notes']
REVENUE_REQUIRED = {'category', 'amount'}


@migration.route('/revenue', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'treasurer')
def import_revenue():
    if request.method == 'POST':
        f = request.files.get('file')
        if not f or not f.filename:
            flash('No file selected.', 'danger')
            return redirect(request.url)
        if not f.filename.lower().endswith('.csv'):
            flash('Please upload a CSV file.', 'danger')
            return redirect(request.url)

        db = get_db()
        try:
            stream = TextIOWrapper(f.stream, encoding='utf-8-sig')
            reader = csv.DictReader(stream)
            missing = REVENUE_REQUIRED - set(reader.fieldnames or [])
            if missing:
                flash(f'Missing columns: {", ".join(sorted(missing))}', 'danger')
                return redirect(request.url)

            success, errors = 0, []
            for row_num, row in enumerate(reader, start=2):
                try:
                    category = row.get('category', '').strip()
                    amount_raw = row.get('amount', '').strip()
                    if not category or not amount_raw:
                        errors.append(f"Row {row_num}: category and amount are required.")
                        continue
                    date = _parse_date(row.get('date', '')) or datetime.now()
                    db.execute('''
                        INSERT INTO revenue
                            (revenue_number, category, amount, description, source, date, notes)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (_ref('REV'), category, float(amount_raw),
                          row.get('description', '').strip() or None,
                          row.get('source', '').strip() or None,
                          date,
                          row.get('notes', '').strip() or None))
                    success += 1
                except Exception as e:
                    errors.append(f"Row {row_num}: {e}")

            db.commit()
            audit(db, 'IMPORT_REVENUE', 'migration',
                  f"Imported {success} revenue records, {len(errors)} errors")

        except Exception as e:
            db.rollback()
            flash(f'File processing error: {e}', 'danger')
            return redirect(request.url)

        _flash_result(success, 0, errors, 'revenue record')
        return redirect(url_for('migration.index'))

    return render_template('admin/migration/import.html',
                           entity='revenue',
                           title='Import Revenue Records',
                           required_cols=sorted(REVENUE_REQUIRED),
                           optional_cols=[c for c in REVENUE_COLUMNS if c not in REVENUE_REQUIRED],
                           template_url=url_for('migration.template_revenue'),
                           back_url=url_for('migration.index'))


@migration.route('/revenue/template')
@login_required
@role_required('admin', 'treasurer')
def template_revenue():
    out = StringIO()
    w = csv.writer(out)
    w.writerow(REVENUE_COLUMNS)
    w.writerow(['Entrance Fee', '2000', 'New member entrance fee', 'Member payments',
                '2024-01-05', ''])
    w.writerow(['Loan Interest', '45000', 'Q1 loan interest income', 'Active loans',
                '2024-03-31', 'Aggregated for quarter'])
    return _csv_response(out, 'revenue_import_template.csv')


@migration.route('/revenue/export')
@login_required
@role_required('admin', 'treasurer')
def export_revenue():
    db = get_db()
    rows = db.execute(
        'SELECT revenue_number, category, amount, description, source, date, notes '
        'FROM revenue ORDER BY date DESC'
    ).fetchall()
    out = StringIO()
    w = csv.writer(out)
    w.writerow(['revenue_number', 'category', 'amount', 'description', 'source', 'date', 'notes'])
    for r in rows:
        w.writerow(list(r))
    return _csv_response(out, 'revenue_export.csv')


# ═══════════════════════════════════════════════════════════════════════════════
# INVESTMENTS
# ═══════════════════════════════════════════════════════════════════════════════

INVESTMENTS_COLUMNS = [
    'name', 'type', 'amount', 'institution', 'interest_rate',
    'risk_level', 'start_date', 'maturity_date', 'description', 'notes',
]
INVESTMENTS_REQUIRED = {'name', 'type', 'amount'}


@migration.route('/investments', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'treasurer')
def import_investments():
    if request.method == 'POST':
        f = request.files.get('file')
        if not f or not f.filename:
            flash('No file selected.', 'danger')
            return redirect(request.url)
        if not f.filename.lower().endswith('.csv'):
            flash('Please upload a CSV file.', 'danger')
            return redirect(request.url)

        db = get_db()
        try:
            stream = TextIOWrapper(f.stream, encoding='utf-8-sig')
            reader = csv.DictReader(stream)
            missing = INVESTMENTS_REQUIRED - set(reader.fieldnames or [])
            if missing:
                flash(f'Missing columns: {", ".join(sorted(missing))}', 'danger')
                return redirect(request.url)

            success, errors = 0, []
            for row_num, row in enumerate(reader, start=2):
                try:
                    name = row.get('name', '').strip()
                    inv_type = row.get('type', '').strip()
                    amount_raw = row.get('amount', '').strip()
                    if not name or not inv_type or not amount_raw:
                        errors.append(f"Row {row_num}: name, type, amount are required.")
                        continue

                    int_rate_raw = row.get('interest_rate', '').strip()
                    int_rate = float(int_rate_raw) if int_rate_raw else None
                    start_date = _parse_date(row.get('start_date', ''))
                    maturity_date = _parse_date(row.get('maturity_date', ''))

                    db.execute('''
                        INSERT INTO investments
                            (investment_number, name, amount, type, institution,
                             interest_rate, risk_level, start_date, maturity_date,
                             description, notes, approval_status, date)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (_ref('INV'), name, float(amount_raw), inv_type,
                          row.get('institution', '').strip() or None,
                          int_rate,
                          row.get('risk_level', 'medium').strip() or 'medium',
                          start_date, maturity_date,
                          row.get('description', '').strip() or None,
                          row.get('notes', '').strip() or None,
                          'approved', datetime.now()))
                    success += 1
                except Exception as e:
                    errors.append(f"Row {row_num}: {e}")

            db.commit()
            audit(db, 'IMPORT_INVESTMENTS', 'migration',
                  f"Imported {success} investments, {len(errors)} errors")

        except Exception as e:
            db.rollback()
            flash(f'File processing error: {e}', 'danger')
            return redirect(request.url)

        _flash_result(success, 0, errors, 'investment')
        return redirect(url_for('migration.index'))

    return render_template('admin/migration/import.html',
                           entity='investments',
                           title='Import Investments',
                           required_cols=sorted(INVESTMENTS_REQUIRED),
                           optional_cols=[c for c in INVESTMENTS_COLUMNS
                                          if c not in INVESTMENTS_REQUIRED],
                           template_url=url_for('migration.template_investments'),
                           back_url=url_for('migration.index'))


@migration.route('/investments/template')
@login_required
@role_required('admin', 'treasurer')
def template_investments():
    out = StringIO()
    w = csv.writer(out)
    w.writerow(INVESTMENTS_COLUMNS)
    w.writerow(['GTBank Fixed Deposit', 'Fixed Deposit', '500000', 'GTBank',
                '9', 'low', '2024-01-01', '2024-07-01',
                '6-month FD at 9%', ''])
    w.writerow(['LASG Bond Series 3', 'Government Bond', '250000', 'Lagos State',
                '11.5', 'low', '2023-06-01', '2025-06-01',
                '2-year bond', 'Held in custody at Stanbic'])
    return _csv_response(out, 'investments_import_template.csv')


@migration.route('/investments/export')
@login_required
@role_required('admin', 'treasurer')
def export_investments():
    db = get_db()
    rows = db.execute('''
        SELECT investment_number, name, type, amount, institution, interest_rate,
               risk_level, start_date, maturity_date, description, approval_status, date
        FROM investments ORDER BY date DESC
    ''').fetchall()
    out = StringIO()
    w = csv.writer(out)
    w.writerow(['investment_number', 'name', 'type', 'amount', 'institution',
                'interest_rate', 'risk_level', 'start_date', 'maturity_date',
                'description', 'approval_status', 'date'])
    for r in rows:
        w.writerow(list(r))
    return _csv_response(out, 'investments_export.csv')


# ═══════════════════════════════════════════════════════════════════════════════
# HONORARIUM
# ═══════════════════════════════════════════════════════════════════════════════

HONORARIUM_COLUMNS = ['recipient_name', 'member_number', 'email',
                      'amount', 'description', 'month', 'date']
HONORARIUM_REQUIRED = {'recipient_name', 'amount', 'month'}


@migration.route('/honorarium', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def import_honorarium():
    if request.method == 'POST':
        f = request.files.get('file')
        if not f or not f.filename:
            flash('No file selected.', 'danger')
            return redirect(request.url)
        if not f.filename.lower().endswith('.csv'):
            flash('Please upload a CSV file.', 'danger')
            return redirect(request.url)

        db = get_db()
        try:
            stream = TextIOWrapper(f.stream, encoding='utf-8-sig')
            reader = csv.DictReader(stream)
            missing = HONORARIUM_REQUIRED - set(reader.fieldnames or [])
            if missing:
                flash(f'Missing columns: {", ".join(sorted(missing))}', 'danger')
                return redirect(request.url)

            success, errors = 0, []
            for row_num, row in enumerate(reader, start=2):
                try:
                    recipient_name = row.get('recipient_name', '').strip()
                    amount_raw = row.get('amount', '').strip()
                    month = row.get('month', '').strip()
                    if not recipient_name or not amount_raw or not month:
                        errors.append(f"Row {row_num}: recipient_name, amount, month required.")
                        continue

                    member = _resolve_member(db, row)
                    recipient_id = member['id'] if member else None
                    date = _parse_date(row.get('date', '')) or datetime.now()

                    db.execute('''
                        INSERT INTO honorarium
                            (recipient_id, recipient_name, amount, description, month, date)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (recipient_id, recipient_name, float(amount_raw),
                          row.get('description', '').strip() or None,
                          month, date))
                    success += 1
                except Exception as e:
                    errors.append(f"Row {row_num}: {e}")

            db.commit()
            audit(db, 'IMPORT_HONORARIUM', 'migration',
                  f"Imported {success} honorarium records, {len(errors)} errors")

        except Exception as e:
            db.rollback()
            flash(f'File processing error: {e}', 'danger')
            return redirect(request.url)

        _flash_result(success, 0, errors, 'honorarium record')
        return redirect(url_for('migration.index'))

    return render_template('admin/migration/import.html',
                           entity='honorarium',
                           title='Import Honorarium Records',
                           required_cols=sorted(HONORARIUM_REQUIRED),
                           optional_cols=[c for c in HONORARIUM_COLUMNS
                                          if c not in HONORARIUM_REQUIRED],
                           template_url=url_for('migration.template_honorarium'),
                           back_url=url_for('migration.index'))


@migration.route('/honorarium/template')
@login_required
@role_required('admin')
def template_honorarium():
    out = StringIO()
    w = csv.writer(out)
    w.writerow(HONORARIUM_COLUMNS)
    w.writerow(['Ade Afolabi', 'OOU/2024/0001', 'ade@example.com',
                '15000', 'Executive allowance', '2024-01', '2024-01-31'])
    w.writerow(['Bola Tinubu', '', 'bola@example.com',
                '10000', 'Secretary allowance', '2024-01', '2024-01-31'])
    return _csv_response(out, 'honorarium_import_template.csv')


@migration.route('/honorarium/export')
@login_required
@role_required('admin')
def export_honorarium():
    db = get_db()
    rows = db.execute('''
        SELECT h.recipient_name, m.member_number, m.email,
               h.amount, h.description, h.month, h.date
        FROM honorarium h
        LEFT JOIN members m ON h.recipient_id = m.id
        ORDER BY h.date DESC
    ''').fetchall()
    out = StringIO()
    w = csv.writer(out)
    w.writerow(['recipient_name', 'member_number', 'email',
                'amount', 'description', 'month', 'date'])
    for r in rows:
        w.writerow(list(r))
    return _csv_response(out, 'honorarium_export.csv')


# ── Shared utilities ──────────────────────────────────────────────────────────

def _parse_date(raw):
    if not raw:
        return None
    raw = raw.strip()
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%d-%m-%Y', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    return None


def _csv_response(out, filename):
    response = make_response(out.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    response.headers['Content-Disposition'] = f'attachment; filename={filename}'
    return response


def _flash_result(success, skipped, errors, entity_name):
    if errors:
        flash(f'Imported {success} {entity_name}(s). '
              f'{skipped} duplicate(s) skipped. '
              f'{len(errors)} error(s):', 'warning')
        for err in errors[:10]:
            flash(err, 'danger')
        if len(errors) > 10:
            flash(f'… and {len(errors) - 10} more errors. Fix the CSV and re-upload.', 'danger')
    elif skipped:
        flash(f'Imported {success} {entity_name}(s). '
              f'{skipped} duplicate(s) were skipped.', 'success')
    else:
        flash(f'Successfully imported {success} {entity_name}(s)!', 'success')
