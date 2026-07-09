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
    'resend_api_key': '',
    'mail_from':     '',
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

_EDITABLE_SETTING_KEYS = set(_DEFAULT_SETTINGS) | {
    'coop_short_name',
    'coop_logo',
    'active_gateway',
    'paystack_public_key',
    'flutterwave_public_key',
    'subscription_expiry',
    'subscription_per_user_fee',
    'subscription_email',
    'interest_method_regular',
    'interest_method_housing',
    'interest_method_emergency',
    'interest_method_asset',
    'interest_method_school_fees',
    'interest_school_fees',
    'support_phone',
    'support_email',
    'office_address',
    'whatsapp_number',
}

_PROTECTED_SETTING_KEYS = {
    'csrf_token',
    'paystack_secret_key',
    'flutterwave_secret_key',
    'flutterwave_webhook_hash',
    'resend_api_key',
    'smtp_pass',
}


def _upsert_setting(db, key, value, description=None):
    existing = db.execute('SELECT id FROM settings WHERE key = ?', (key,)).fetchone()
    if existing:
        db.execute('UPDATE settings SET value = ? WHERE key = ?', (value, key))
    else:
        db.execute(
            'INSERT INTO settings (key, value, description) VALUES (?, ?, ?)',
            (key, value, description or f'Setting for {key}')
        )


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

        users = db.execute(
            'SELECT id, username, full_name, email, role, is_active, last_login, is_super_admin FROM users ORDER BY id'
        ).fetchall()
        # Check if the currently logged-in user is a super admin
        me_row = db.execute('SELECT is_super_admin FROM users WHERE id = ?', (current_user.id,)).fetchone()
        current_is_super = bool(me_row and me_row['is_super_admin'])
        user_list = [
            {
                'id':             u['id'],
                'username':       u['username'],
                'full_name':      u['full_name'] or u['username'],
                'email':          u['email'] or '',
                'role':           u['role'],
                'is_active':      u['is_active'] if u['is_active'] is not None else 1,
                'last_login':     u['last_login'] or 'Never',
                'is_super_admin': bool(u['is_super_admin'] if 'is_super_admin' in u.keys() else 0),
            }
            for u in users
        ]

        audit_logs = db.execute(
            'SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT 100'
        ).fetchall()

        return render_template('admin/settings.html',
                               settings=settings_dict,
                               system_users=user_list,
                               current_is_super=current_is_super,
                               audit_logs=audit_logs,
                               backup_history=[],
                               datetime=datetime)
    except Exception as e:
        flash(f'Error loading settings: {str(e)}', 'danger')
        return render_template('admin/settings.html',
                               settings=_DEFAULT_SETTINGS,
                               system_users=[],
                               current_is_super=False,
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
            # db_path is relative to static/ so url_for('static', filename=db_path) works
            db_path   = f"uploads/{unique_name}"
            disk_path = os.path.join('static', 'uploads', unique_name)
            os.makedirs(os.path.join('static', 'uploads'), exist_ok=True)
            logo.save(disk_path)
            existing = db.execute("SELECT id FROM settings WHERE key = 'coop_logo'").fetchone()
            if existing:
                db.execute("UPDATE settings SET value = ? WHERE key = 'coop_logo'", (db_path,))
            else:
                db.execute(
                    'INSERT INTO settings (key, value, description) VALUES (?, ?, ?)',
                    ('coop_logo', db_path, 'Cooperative logo (path relative to static/)')
                )

    try:
        updated = 0
        ignored = []
        for key, value in request.form.items():
            if key in _PROTECTED_SETTING_KEYS:
                continue
            if key not in _EDITABLE_SETTING_KEYS:
                ignored.append(key)
                continue
            if not value:
                continue
            _upsert_setting(db, key, value)
            updated += 1
        db.commit()
        audit(db, 'UPDATE_SETTINGS', 'settings', f'System settings updated ({updated} keys)')
        if ignored:
            flash(f'Ignored unsupported setting keys: {", ".join(sorted(set(ignored)))}', 'warning')
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
@role_required('admin', 'treasurer', 'secretary', 'exco')
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


@admin_panel.route('/api/edit_user/<int:user_id>', methods=['POST'])
@login_required
@role_required('admin')
def edit_user(user_id):
    db = get_db()
    try:
        full_name = request.form.get('full_name', '').strip()
        email     = request.form.get('email', '').strip()
        role      = request.form.get('role', '').strip()

        if not role:
            flash('Role is required.', 'danger')
            return redirect(url_for('admin_panel.settings') + '#users')

        # Prevent admin from removing their own admin role
        if user_id == current_user.id and role != 'admin':
            flash('You cannot change your own role.', 'danger')
            return redirect(url_for('admin_panel.settings') + '#users')

        # Super-admin protection: only a super admin can edit another super admin
        target = db.execute('SELECT is_super_admin FROM users WHERE id = ?', (user_id,)).fetchone()
        if target and target['is_super_admin']:
            me = db.execute('SELECT is_super_admin FROM users WHERE id = ?', (current_user.id,)).fetchone()
            if not (me and me['is_super_admin']):
                flash('Only a super admin can modify a super admin account.', 'danger')
                return redirect(url_for('admin_panel.settings') + '#users')

        db.execute(
            'UPDATE users SET full_name = ?, email = ?, role = ? WHERE id = ?',
            (full_name, email, role, user_id)
        )
        db.commit()
        audit(db, 'UPDATE', 'users', f'Updated user id={user_id} role={role}')
        flash('User updated successfully.', 'success')
    except Exception as e:
        db.rollback()
        flash(f'Error updating user: {e}', 'danger')
    return redirect(url_for('admin_panel.settings') + '#users')


@admin_panel.route('/api/reset_user_password/<int:user_id>', methods=['POST'])
@login_required
@role_required('admin')
def reset_user_password(user_id):
    db = get_db()
    try:
        new_password = request.form.get('new_password', '').strip()
        force_change  = request.form.get('force_change', '0') == '1'

        if len(new_password) < 6:
            flash('Password must be at least 6 characters.', 'danger')
            return redirect(url_for('admin_panel.settings') + '#users')

        db.execute(
            'UPDATE users SET password_hash = ?, must_change_password = ? WHERE id = ?',
            (generate_password_hash(new_password), 1 if force_change else 0, user_id)
        )
        db.commit()
        user = db.execute('SELECT username FROM users WHERE id = ?', (user_id,)).fetchone()
        uname = user['username'] if user else str(user_id)
        audit(db, 'UPDATE', 'users', f'Admin reset password for user {uname}')
        flash(f'Password for "{uname}" has been reset successfully.', 'success')
    except Exception as e:
        db.rollback()
        flash(f'Error resetting password: {e}', 'danger')
    return redirect(url_for('admin_panel.settings') + '#users')


@admin_panel.route('/api/toggle_super_admin/<int:user_id>', methods=['POST'])
@login_required
@role_required('admin')
def toggle_super_admin(user_id):
    """Grant or revoke super-admin status.  Only a super admin can do this."""
    db = get_db()
    me = db.execute('SELECT is_super_admin FROM users WHERE id = ?', (current_user.id,)).fetchone()
    if not (me and me['is_super_admin']):
        flash('Only a super admin can grant or revoke super admin status.', 'danger')
        return redirect(url_for('admin_panel.settings') + '#users')
    if user_id == current_user.id:
        flash('You cannot revoke your own super admin status.', 'danger')
        return redirect(url_for('admin_panel.settings') + '#users')
    try:
        target = db.execute('SELECT username, is_super_admin FROM users WHERE id = ?', (user_id,)).fetchone()
        if not target:
            flash('User not found.', 'danger')
            return redirect(url_for('admin_panel.settings') + '#users')
        new_val = 0 if target['is_super_admin'] else 1
        db.execute('UPDATE users SET is_super_admin = ? WHERE id = ?', (new_val, user_id))
        db.commit()
        status = 'granted' if new_val else 'revoked'
        audit(db, 'UPDATE', 'users', f'Super admin status {status} for {target["username"]}')
        flash(f'Super admin status {status} for "{target["username"]}".', 'success')
    except Exception as e:
        db.rollback()
        flash(f'Error updating super admin: {e}', 'danger')
    return redirect(url_for('admin_panel.settings') + '#users')


@admin_panel.route('/api/toggle_user/<int:user_id>', methods=['POST'])
@login_required
@role_required('admin')
def toggle_user(user_id):
    if user_id == current_user.id:
        flash('You cannot disable your own account.', 'danger')
        return redirect(url_for('admin_panel.settings') + '#users')
    db = get_db()
    try:
        user = db.execute('SELECT username, is_active FROM users WHERE id = ?', (user_id,)).fetchone()
        if not user:
            flash('User not found.', 'danger')
            return redirect(url_for('admin_panel.settings') + '#users')
        new_status = 0 if user['is_active'] else 1
        db.execute('UPDATE users SET is_active = ? WHERE id = ?', (new_status, user_id))
        db.commit()
        action = 'enabled' if new_status else 'disabled'
        audit(db, 'UPDATE', 'users', f'Admin {action} user {user["username"]}')
        flash(f'User "{user["username"]}" has been {action}.', 'success')
    except Exception as e:
        db.rollback()
        flash(f'Error toggling user: {e}', 'danger')
    return redirect(url_for('admin_panel.settings') + '#users')


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
    """Save email settings (Resend API or SMTP) to the DB."""
    db = get_db()
    try:
        mail_enabled   = '1' if request.form.get('mail_enabled') else '0'
        mail_from      = request.form.get('mail_from', '').strip()
        resend_api_key = request.form.get('resend_api_key', '').strip()
        smtp_host      = request.form.get('smtp_host', '').strip()
        smtp_port      = request.form.get('smtp_port', '587').strip() or '587'
        smtp_user      = request.form.get('smtp_user', '').strip()
        smtp_pass      = request.form.get('smtp_pass', '').strip()

        updates = {
            'mail_enabled': mail_enabled,
            'mail_from':    mail_from,
            'smtp_host':    smtp_host,
            'smtp_port':    smtp_port,
            'smtp_user':    smtp_user,
        }
        if resend_api_key:
            updates['resend_api_key'] = resend_api_key
        if smtp_pass:
            updates['smtp_pass'] = smtp_pass  # blank → keep existing

        for key, val in updates.items():
            _upsert_setting(db, key, val, f'Email setting: {key}')

        db.commit()
        audit(db, 'UPDATE_MAIL_SETTINGS', 'settings', 'Email settings updated')
        flash('Email settings saved successfully!', 'success')
    except Exception as e:
        db.rollback()
        flash(f'Error saving email settings: {str(e)}', 'danger')
    return redirect(url_for('admin_panel.settings') + '#mail')


@admin_panel.route('/settings/test-mail', methods=['POST'])
@login_required
@role_required('admin')
def test_mail():
    from flask import jsonify
    from email_service import send_email

    recipient = request.form.get('recipient', '').strip()
    if not recipient:
        return jsonify({'success': False, 'error': 'Recipient email is required'})

    db = get_db()
    enabled = db.execute("SELECT value FROM settings WHERE key = 'mail_enabled'").fetchone()
    if not enabled or enabled['value'] != '1':
        return jsonify({'success': False,
                        'error': 'Email is disabled. Enable it and save first.'})

    html = (
        '<h2 style="color:#1a3a6c">Test Email</h2>'
        '<p>This is a test email from your OOU Cooperative Management System.</p>'
        '<p>If you received this, your Resend API key is configured correctly.</p>'
        f'<hr><small>Sent at {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</small>'
    )
    ok = send_email(recipient, 'Test Email — OOU Cooperative', html)
    if ok:
        audit(db, 'TEST_MAIL', 'settings', f'Test email sent via Resend to {recipient}')
        return jsonify({'success': True, 'message': f'Test email sent to {recipient}'})
    return jsonify({'success': False,
                    'error': 'Send failed. Check that RESEND_API_KEY is set and the From address uses a verified domain.'})


# ── Subscription billing ──────────────────────────────────────────────────────

@admin_panel.route('/subscription')
@login_required
@role_required('admin', 'treasurer')
def subscription_page():
    from datetime import datetime, timedelta
    db = get_db()
    rows = {r['key']: r['value'] for r in db.execute('SELECT key, value FROM settings').fetchall()}

    expiry_str   = rows.get('subscription_expiry', '').strip()
    per_user_fee = int(rows.get('subscription_per_user_fee', '5000') or 5000)
    coop_email   = rows.get('subscription_email') or rows.get('email', '')
    coop_name    = rows.get('coop_name', 'Cooperative')
    pk           = rows.get('paystack_public_key', '')

    # Count active members to compute per-user fee
    try:
        member_count = db.execute(
            "SELECT COUNT(*) FROM members WHERE status = 'active'"
        ).fetchone()[0] or 0
    except Exception:
        member_count = 0

    # Total fee = active members × per_user_fee (minimum 1 member to avoid ₦0)
    total_fee = max(member_count, 1) * per_user_fee

    expiry_date = None
    days_left   = None
    is_active   = False

    if expiry_str:
        try:
            expiry_date = datetime.strptime(expiry_str, '%Y-%m-%d')
            days_left   = (expiry_date - datetime.now()).days
            is_active   = days_left > 0
        except Exception:
            pass
    else:
        is_active = True  # no billing configured

    # Pre-compute new expiry so the template never needs now() or timedelta
    base = expiry_date if (is_active and expiry_date) else datetime.now()
    new_expiry_display = (base + timedelta(days=365)).strftime('%d %b %Y')

    # Safe days_left — never negative for display
    days_left_safe = max(days_left, 0) if days_left is not None else None

    return render_template(
        'subscription.html',
        expiry_date=expiry_date,
        days_left=days_left,
        days_left_safe=days_left_safe,
        is_active=is_active,
        fee=total_fee,
        per_user_fee=per_user_fee,
        member_count=member_count,
        coop_email=coop_email,
        coop_name=coop_name,
        paystack_public_key=pk,
        new_expiry_display=new_expiry_display,
    )


@admin_panel.route('/subscription/callback')
@login_required
@role_required('admin', 'treasurer')
def subscription_callback():
    """Paystack redirects here after a subscription payment."""
    reference = request.args.get('reference', '').strip()
    if not reference:
        flash('Invalid payment reference.', 'danger')
        return redirect(url_for('admin_panel.subscription_page'))

    # Verify with Paystack
    db  = get_db()
    sk  = (db.execute("SELECT value FROM settings WHERE key='paystack_secret_key'").fetchone() or {}).get('value', '')
    if not sk:
        sk = os.environ.get('PAYSTACK_SECRET_KEY', '')

    verified = False
    amount_paid = 0
    try:
        import urllib.request as _ur, json as _json, ssl as _ssl
        req = _ur.Request(
            f'https://api.paystack.co/transaction/verify/{reference}',
            headers={'Authorization': f'Bearer {sk}', 'Accept': 'application/json'}
        )
        ctx = _ssl.create_default_context()
        with _ur.urlopen(req, context=ctx, timeout=10) as resp:
            data = _json.loads(resp.read())
        if data.get('status') and data['data'].get('status') == 'success':
            verified    = True
            amount_paid = data['data']['amount'] // 100  # kobo → naira
    except Exception as e:
        flash(f'Could not verify payment with Paystack: {e}', 'danger')
        return redirect(url_for('admin_panel.subscription_page'))

    if not verified:
        flash('Payment could not be verified. Please contact support.', 'danger')
        return redirect(url_for('admin_panel.subscription_page'))

    # ── Replay protection: each Paystack reference may only be applied once ──
    already = db.execute(
        "SELECT id FROM audit_log WHERE action = 'SUBSCRIPTION_RENEWED' AND data LIKE ?",
        (f'%{reference}%',)
    ).fetchone()
    if already:
        flash('This payment reference has already been applied to your subscription.', 'info')
        return redirect(url_for('admin_panel.subscription_page'))

    # ── Amount check: the payment must cover the fee actually due ──
    _rows = {r['key']: r['value'] for r in db.execute('SELECT key, value FROM settings').fetchall()}
    _per_user_fee = int(_rows.get('subscription_per_user_fee', '5000') or 5000)
    try:
        _member_count = db.execute(
            "SELECT COUNT(*) FROM members WHERE status = 'active'"
        ).fetchone()[0] or 0
    except Exception:
        _member_count = 0
    _expected_fee = max(_member_count, 1) * _per_user_fee
    if amount_paid < _expected_fee:
        flash(
            f'Payment of ₦{amount_paid:,} is less than the amount due '
            f'(₦{_expected_fee:,}). Subscription was not extended — please contact support.',
            'danger'
        )
        return redirect(url_for('admin_panel.subscription_page'))

    # Extend subscription by 1 year from today (or from current expiry if still active)
    from datetime import datetime, timedelta
    current_str = (db.execute("SELECT value FROM settings WHERE key='subscription_expiry'").fetchone() or {}).get('value', '')
    try:
        current_expiry = datetime.strptime(current_str, '%Y-%m-%d') if current_str else datetime.now()
        base = max(current_expiry, datetime.now())
    except Exception:
        base = datetime.now()

    new_expiry = (base + timedelta(days=365)).strftime('%Y-%m-%d')

    db.execute(
        "UPDATE settings SET value = ? WHERE key = 'subscription_expiry'",
        (new_expiry,)
    )
    # Also log as revenue
    from security import log_audit
    log_audit(db, current_user.id, current_user.username,
              'SUBSCRIPTION_RENEWED', 'billing',
              f'Subscription renewed via Paystack ref {reference}. '
              f'Amount: ₦{amount_paid:,}. New expiry: {new_expiry}',
              request.remote_addr, '')
    db.commit()

    flash(f'✅ Subscription renewed successfully! Active until {new_expiry}.', 'success')
    return redirect(url_for('admin_panel.subscription_page'))

