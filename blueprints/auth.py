from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_user, login_required, logout_user
from werkzeug.security import check_password_hash

from database import get_db
from security import log_audit
from utils import User, is_rate_limited, record_failed_login, clear_login_attempts

auth = Blueprint('auth', __name__)


@auth.route('/')
def index():
    return render_template('index.html')


@auth.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        ip = request.remote_addr or '0.0.0.0'

        username = request.form['username']
        password = request.form['password']
        ua       = request.user_agent.string if request.user_agent else ''

        db   = get_db()
        user = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()

        if user and check_password_hash(user['password_hash'], password):
            clear_login_attempts(ip)
            keys = user.keys()
            user_obj = User(
                user['id'], user['username'], user['password_hash'], user['role'],
                user['email'] if 'email' in keys else '',
                user['must_change_password'] if 'must_change_password' in keys else 0,
            )
            login_user(user_obj)
            log_audit(db, user['id'], user['username'], 'LOGIN', 'auth', 'User logged in', ip, ua)
            if user_obj.must_change_password:
                flash('Welcome! You must set a new password before you can continue.', 'warning')
                return redirect(url_for('portal.change_password'))
            flash('Login successful!', 'success')
            return redirect(url_for('main.dashboard'))
        else:
            record_failed_login(ip)
            log_audit(db, None, username, 'FAILED_LOGIN', 'auth',
                      f'Failed login attempt for username: {username}', ip, ua)
            flash('Invalid username or password', 'danger')

    return render_template('login.html')


@auth.route('/logout')
@login_required
def logout():
    from flask_login import current_user
    db = get_db()
    from utils import audit
    audit(db, 'LOGOUT', 'auth', 'User logged out')
    logout_user()
    flash('You have been logged out', 'info')
    return redirect(url_for('auth.index'))


@auth.route('/setup')
def setup():
    try:
        import subprocess
        subprocess.run(['python', 'init_settings.py'])
        flash('Setup completed!', 'success')
    except Exception as e:
        flash(f'Setup error: {str(e)}', 'danger')
    return redirect(url_for('main.dashboard'))


@auth.route('/debug-auth')
def debug_auth():
    """Temporary diagnostic endpoint — requires RESET_TOKEN."""
    import os
    from flask import request as req
    expected_token = os.environ.get('RESET_TOKEN', '')
    provided_token = req.args.get('token', '')
    if not expected_token or provided_token != expected_token:
        return '<h2>Not available</h2>', 403

    db   = get_db()
    rows = db.execute('SELECT id, username, role, must_change_password, created_at FROM users').fetchall()
    db_path = os.environ.get('SQLITE_DB_PATH', 'cooperative.db (default)')
    admin_pw = os.environ.get('ADMIN_PASSWORD', '')
    pw_hint  = (admin_pw[:3] + '***') if admin_pw else 'NOT SET'

    lines = [f'<p><b>DB path:</b> {db_path}</p>']
    lines.append(f'<p><b>ADMIN_PASSWORD hint:</b> {pw_hint}</p>')
    lines.append(f'<p><b>Users in DB ({len(rows)}):</b></p><ul>')
    for r in rows:
        lines.append(f'<li>id={r["id"]} username=<b>{r["username"]}</b> role={r["role"]} must_change={r["must_change_password"]} created={r["created_at"]}</li>')
    lines.append('</ul>')
    return ''.join(lines), 200


@auth.route('/emergency-reset')
def emergency_reset():
    """
    Emergency admin password reset.
    Requires ?token=<RESET_TOKEN> in the URL where RESET_TOKEN is an env var
    you set in Railway. After resetting, delete the RESET_TOKEN variable.

    Example:
      Set RESET_TOKEN=my-secret-reset-key in Railway variables
      Visit /emergency-reset?token=my-secret-reset-key
      Admin password is reset to whatever ADMIN_PASSWORD is set to
    """
    import os
    from flask import request as req
    from werkzeug.security import generate_password_hash

    expected_token = os.environ.get('RESET_TOKEN', '')
    provided_token = req.args.get('token', '')

    if not expected_token:
        return '<h2>Reset not available.</h2><p>RESET_TOKEN environment variable is not set.</p>', 403

    if not provided_token or provided_token != expected_token:
        return '<h2>Invalid token.</h2>', 403

    new_password = os.environ.get('ADMIN_PASSWORD', '')
    if not new_password:
        return '<h2>ADMIN_PASSWORD is not set.</h2><p>Set it in Railway variables first.</p>', 400

    try:
        db = get_db()
        db.execute(
            'UPDATE users SET password_hash = ? WHERE username = ?',
            (generate_password_hash(new_password), 'admin')
        )
        db.commit()
        rows = db.execute('SELECT id, username, role FROM users WHERE username = ?', ('admin',)).fetchone()
        if rows:
            return f'''
            <h2 style="color:green">&#10003; Admin password reset successfully.</h2>
            <p>Username: <strong>admin</strong></p>
            <p>Password: <strong>the value you set in ADMIN_PASSWORD</strong></p>
            <p><a href="/login">Go to Login</a></p>
            <hr>
            <p style="color:red"><strong>Security:</strong> Remove RESET_TOKEN from your Railway variables now.</p>
            ''', 200
        else:
            return '<h2>Admin user not found.</h2><p>No user with username "admin" exists in the database.</p>', 404
    except Exception as e:
        return f'<h2>Error</h2><pre>{e}</pre>', 500
