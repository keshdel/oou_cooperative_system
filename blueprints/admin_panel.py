import os
import random
from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user
from werkzeug.security import generate_password_hash
from werkzeug.utils import secure_filename

from database import get_db
from utils import role_required, audit, validate_image

admin_panel = Blueprint('admin_panel', __name__)

_DEFAULT_SETTINGS = {
    'mail_enabled':  '0',
    'mail_server':   '',
    'mail_port':     '587',
    'mail_use_tls':  '1',
    'mail_username': '',
    'mail_password': '',
    'mail_sender':   '',
    'coop_name': 'OOU Acctg 2005 Alumni CMS',
    'reg_number': 'CMS/2005/001',
    'address': '',
    'phone': '',
    'email': '',
    'fy_start': '1',
    'currency': 'NGN',
    'date_format': 'Y-m-d',
    'session_timeout': '30',
    'maintenance_mode': '0',
    'min_savings': '5000',
    'savings_due_day': '10',
    'late_fee_percent': '10',
    'min_deposit_period': '90',
    'member_deposit_rate': '9',
    'nonmember_deposit_rate': '7',
    'dividend_rate': '50',
    'min_membership_months': '6',
    'min_savings_for_loan': '50000',
    'loan_multiplier': '2',
    'max_tenure_months': '18',
    'max_interest_rate': '11',
    'insurance_rate': '1',
    'guarantors_required': '2',
    'default_penalty_rate': '20',
    'interest_regular': '11',
    'interest_housing': '9',
    'interest_emergency': '10',
    'interest_asset': '10',
    'entrance_fee': '2000',
    'reentry_fee': '5000',
    'loan_application_fee': '1000',
    'statement_fee': '500',
}


@admin_panel.route('/settings')
@login_required
@role_required('admin')
def settings():
    db = get_db()
    try:
        settings_rows = db.execute('SELECT key, value FROM settings').fetchall()
        settings_dict = {row['key']: row['value'] for row in settings_rows}
        for key, default_value in _DEFAULT_SETTINGS.items():
            settings_dict.setdefault(key, default_value)

        users = db.execute('SELECT id, username, role, created_at FROM users ORDER BY id').fetchall()
        user_list = [
            {
                'id': u['id'],
                'username': u['username'],
                'full_name': u['username'],
                'role': u['role'],
                'last_login': 'Never',
                'status': 'active',
            }
            for u in users
        ]

        audit_logs = db.execute(
            'SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT 100'
        ).fetchall()

        return render_template('admin/settings.html',
                               settings=settings_dict,
                               system_users=user_list,
                               audit_logs=audit_logs,
                               backup_history=[],
                               datetime=datetime)
    except Exception as e:
        flash(f'Error loading settings: {str(e)}', 'danger')
        return render_template('admin/settings.html',
                               settings=_DEFAULT_SETTINGS,
                               system_users=[],
                               audit_logs=[],
                               backup_history=[],
                               datetime=datetime)


@admin_panel.route('/settings/update', methods=['POST'])
@login_required
@role_required('admin')
def update_settings():
    db = get_db()

    if 'coop_logo' in request.files:
        logo = request.files['coop_logo']
        if logo and logo.filename:
            ok, err = validate_image(logo)
            if not ok:
                flash(f'Logo not saved: {err}', 'warning')
                logo = None
        if logo and logo.filename:
            ext = secure_filename(logo.filename).rsplit('.', 1)[1].lower()
            unique_name = f"coop_logo_{int(datetime.now().timestamp())}.{ext}"
            logo_path = os.path.join('static/uploads', unique_name)
            os.makedirs('static/uploads', exist_ok=True)
            logo.save(logo_path)
            existing = db.execute('SELECT id FROM settings WHERE key = "coop_logo"').fetchone()
            if existing:
                db.execute('UPDATE settings SET value = ? WHERE key = "coop_logo"', (logo_path,))
            else:
                db.execute(
                    'INSERT INTO settings (key, value, description) VALUES (?, ?, ?)',
                    ('coop_logo', logo_path, 'Cooperative logo')
                )

    try:
        for key, value in request.form.items():
            if not value:
                continue
            existing = db.execute('SELECT id FROM settings WHERE key = ?', (key,)).fetchone()
            if existing:
                db.execute('UPDATE settings SET value = ? WHERE key = ?', (value, key))
            else:
                db.execute(
                    'INSERT INTO settings (key, value, description) VALUES (?, ?, ?)',
                    (key, value, f'Setting for {key}')
                )
        db.commit()
        audit(db, 'UPDATE_SETTINGS', 'settings', 'System settings updated')
        flash('Settings saved successfully!', 'success')
    except Exception as e:
        db.rollback()
        flash(f'Error saving settings: {str(e)}', 'danger')

    return redirect(url_for('admin_panel.settings'))


@admin_panel.route('/expenses')
@login_required
@role_required('admin', 'treasurer')
def expenses():
    db = get_db()
    all_expenses = db.execute('SELECT * FROM expenses ORDER BY date DESC').fetchall()
    total_expenses = db.execute('SELECT SUM(amount) FROM expenses').fetchone()[0] or 0
    return render_template('admin/expenses.html',
                           expenses=all_expenses,
                           total_expenses=total_expenses)


@admin_panel.route('/expenses/add', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'treasurer')
def add_expense():
    if request.method == 'POST':
        db = get_db()
        try:
            expense_number = f"EXP/{datetime.now().strftime('%Y%m%d')}/{random.randint(1000, 9999)}"
            db.execute('''
                INSERT INTO expenses (
                    expense_number, category, amount, description, vendor,
                    payment_method, date, recorded_by, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                expense_number,
                request.form['category'],
                float(request.form['amount']),
                request.form['description'],
                request.form.get('vendor', ''),
                request.form['payment_method'],
                request.form.get('date', datetime.now()),
                current_user.id,
                request.form.get('notes', ''),
            ))
            db.commit()
            audit(db, 'ADD_EXPENSE', 'expenses',
                  f"Recorded expense {expense_number} – ₦{float(request.form['amount']):,.2f}")
            flash('Expense recorded successfully!', 'success')
        except Exception as e:
            db.rollback()
            flash(f'Error recording expense: {str(e)}', 'danger')
        return redirect(url_for('admin_panel.expenses'))
    return render_template('admin/add-expense.html')


@admin_panel.route('/revenue')
@login_required
@role_required('admin', 'treasurer')
def revenue():
    db = get_db()
    revenues = db.execute('SELECT * FROM revenue ORDER BY date DESC').fetchall()
    total_revenue = db.execute('SELECT SUM(amount) FROM revenue').fetchone()[0] or 0
    return render_template('admin/revenue.html', revenues=revenues, total_revenue=total_revenue)


@admin_panel.route('/revenue/add', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'treasurer')
def add_revenue():
    if request.method == 'POST':
        db = get_db()
        try:
            revenue_number = f"REV/{datetime.now().strftime('%Y%m%d')}/{random.randint(1000, 9999)}"
            db.execute('''
                INSERT INTO revenue (
                    revenue_number, category, amount, description, source,
                    date, received_by, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                revenue_number,
                request.form['category'],
                float(request.form['amount']),
                request.form['description'],
                request.form.get('source', ''),
                request.form.get('date', datetime.now()),
                current_user.id,
                request.form.get('notes', ''),
            ))
            db.commit()
            audit(db, 'ADD_REVENUE', 'revenue',
                  f"Recorded revenue {revenue_number} – ₦{float(request.form['amount']):,.2f}")
            flash('Revenue recorded successfully!', 'success')
        except Exception as e:
            db.rollback()
            flash(f'Error recording revenue: {str(e)}', 'danger')
        return redirect(url_for('admin_panel.revenue'))
    return render_template('admin/add-revenue.html')


@admin_panel.route('/honorarium')
@login_required
@role_required('admin')
def honorarium():
    db = get_db()
    honorariums = db.execute('''
        SELECT h.*, u.username as paid_by_name
        FROM honorarium h
        LEFT JOIN users u ON h.paid_by = u.id
        ORDER BY h.date DESC
    ''').fetchall()
    return render_template('admin/honorarium.html', honorariums=honorariums)


@admin_panel.route('/honorarium/add', methods=['POST'])
@login_required
@role_required('admin')
def add_honorarium():
    db = get_db()
    try:
        db.execute('''
            INSERT INTO honorarium (
                recipient_id, recipient_name, amount, description, month, paid_by
            ) VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            request.form.get('recipient_id'),
            request.form['recipient_name'],
            float(request.form['amount']),
            request.form['description'],
            request.form['month'],
            current_user.id,
        ))
        db.commit()
        flash('Honorarium recorded successfully!', 'success')
    except Exception as e:
        db.rollback()
        flash(f'Error recording honorarium: {str(e)}', 'danger')
    return redirect(url_for('admin_panel.honorarium'))


@admin_panel.route('/api/member/<int:member_id>')
@login_required
def get_member_api(member_id):
    from flask import jsonify
    db = get_db()
    member = db.execute(
        'SELECT id, first_name, last_name, member_number, total_savings FROM members WHERE id = ?',
        (member_id,)
    ).fetchone()
    if member:
        return jsonify({
            'id': member['id'],
            'first_name': member['first_name'],
            'last_name': member['last_name'],
            'member_number': member['member_number'],
            'total_savings': float(member['total_savings'] or 0),
            'max_loan': float(member['total_savings'] or 0) * 2,
        })
    return jsonify({'error': 'Member not found'}), 404


@admin_panel.route('/api/add_user', methods=['POST'])
@login_required
@role_required('admin')
def add_user():
    db = get_db()
    try:
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        role = request.form.get('role', 'member').strip()
        full_name = request.form.get('full_name', username)
        email = request.form.get('email', '')

        if not username or not password:
            flash('Username and password are required', 'danger')
            return redirect(url_for('admin_panel.settings'))

        existing = db.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone()
        if existing:
            flash(f'Username "{username}" already exists', 'danger')
            return redirect(url_for('admin_panel.settings'))

        password_hash = generate_password_hash(password)
        db.execute('''
            INSERT INTO users (username, password_hash, role, full_name, email, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (username, password_hash, role, full_name, email, datetime.now()))
        db.commit()
        flash(f'User "{username}" created successfully!', 'success')
    except Exception as e:
        db.rollback()
        flash(f'Error creating user: {str(e)}', 'danger')
    return redirect(url_for('admin_panel.settings'))


@admin_panel.route('/api/test_db')
@login_required
@role_required('admin')
def test_db():
    from flask import jsonify
    try:
        db = get_db()
        db.execute('SELECT 1').fetchone()
        return jsonify({'success': True, 'message': 'Database connection successful'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@admin_panel.route('/settings/update-mail', methods=['POST'])
@login_required
@role_required('admin')
def update_mail_settings():
    from flask import current_app
    from extensions import mail as mail_ext

    db = get_db()
    try:
        keys = ['mail_enabled', 'mail_server', 'mail_port', 'mail_use_tls',
                'mail_username', 'mail_password', 'mail_sender']

        for key in keys:
            if key == 'mail_password':
                val = request.form.get(key, '').strip()
                if not val:
                    continue  # blank → keep existing password
            elif key == 'mail_use_tls':
                val = '1' if request.form.get(key) else '0'
            elif key == 'mail_enabled':
                val = '1' if request.form.get(key) else '0'
            else:
                val = request.form.get(key, '').strip()

            existing = db.execute('SELECT id FROM settings WHERE key = ?', (key,)).fetchone()
            if existing:
                db.execute('UPDATE settings SET value = ? WHERE key = ?', (val, key))
            else:
                db.execute('INSERT INTO settings (key, value, description) VALUES (?, ?, ?)',
                           (key, val, f'Mail setting: {key}'))

        db.commit()

        # Reconfigure Flask-Mail immediately so the test-mail button works without restart
        _apply_mail_config(db, current_app._get_current_object())
        mail_ext.init_app(current_app._get_current_object())

        audit(db, 'UPDATE_MAIL_SETTINGS', 'settings', 'SMTP mail settings updated')
        flash('Mail settings saved successfully!', 'success')
    except Exception as e:
        db.rollback()
        flash(f'Error saving mail settings: {str(e)}', 'danger')

    return redirect(url_for('admin_panel.settings') + '#mail')


@admin_panel.route('/settings/test-mail', methods=['POST'])
@login_required
@role_required('admin')
def test_mail():
    from flask import jsonify, current_app
    from flask_mail import Message
    from extensions import mail as mail_ext

    recipient = request.form.get('recipient', '').strip()
    if not recipient:
        return jsonify({'success': False, 'error': 'Recipient email is required'})

    db = get_db()
    enabled = db.execute("SELECT value FROM settings WHERE key = 'mail_enabled'").fetchone()
    if not enabled or enabled['value'] != '1':
        return jsonify({'success': False, 'error': 'Mail is disabled. Enable it first and save settings.'})

    try:
        msg = Message(
            subject=f"Test Email from {current_app.config.get('MAIL_DEFAULT_SENDER', 'Cooperative System')}",
            recipients=[recipient],
            html=f"""
            <h2>Test Email</h2>
            <p>This is a test email from your cooperative management system.</p>
            <p>If you received this, your SMTP settings are configured correctly.</p>
            <hr>
            <small>Sent at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</small>
            """,
        )
        mail_ext.send(msg)
        audit(db, 'TEST_MAIL', 'settings', f"Test email sent to {recipient}")
        return jsonify({'success': True, 'message': f'Test email sent to {recipient}'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


def _apply_mail_config(db, app):
    """Read mail settings from the DB and push them into app.config."""
    mappings = {
        'mail_server':   ('MAIL_SERVER',         str),
        'mail_port':     ('MAIL_PORT',            int),
        'mail_use_tls':  ('MAIL_USE_TLS',         lambda v: v == '1'),
        'mail_username': ('MAIL_USERNAME',        str),
        'mail_password': ('MAIL_PASSWORD',        str),
        'mail_sender':   ('MAIL_DEFAULT_SENDER',  str),
    }
    for db_key, (cfg_key, cast) in mappings.items():
        row = db.execute('SELECT value FROM settings WHERE key = ?', (db_key,)).fetchone()
        if row and row['value']:
            app.config[cfg_key] = cast(row['value'])
