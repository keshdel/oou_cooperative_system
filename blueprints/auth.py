import hmac
import os

from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_user, login_required, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from database import get_db
from security import log_audit
from utils import User, is_rate_limited, lockout_seconds_remaining, record_failed_login, clear_login_attempts

auth = Blueprint('auth', __name__)


def _support_routes_enabled():
    return os.environ.get('ENABLE_SUPPORT_ROUTES') == '1'


def _reset_token_is_valid():
    expected_token = os.environ.get('RESET_TOKEN', '')
    provided_token = request.form.get('token') or request.headers.get('X-Reset-Token', '')
    return bool(expected_token and provided_token and hmac.compare_digest(provided_token, expected_token))


@auth.route('/')
def index():
    return render_template('index.html')


@auth.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        ip = request.remote_addr or '0.0.0.0'

        # ── Rate-limit check — show exact time remaining ──────────────────
        if is_rate_limited(ip):
            secs = lockout_seconds_remaining(ip)
            mins = max(1, round(secs / 60))
            flash(
                f'Too many failed login attempts. '
                f'Your account is temporarily locked — please try again in '
                f'<strong>{mins} minute{"s" if mins != 1 else ""}</strong>.',
                'lockout'
            )
            return render_template('login.html')

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
            db.commit()
            if user_obj.must_change_password:
                flash('Welcome! You must set a new password before you can continue.', 'warning')
                return redirect(url_for('portal.change_password'))
            flash('Login successful!', 'success')
            return redirect(url_for('main.dashboard'))
        else:
            record_failed_login(ip)
            remaining_attempts = 5 - len([1 for _ in range(1)])  # recalculate
            log_audit(db, None, username, 'FAILED_LOGIN', 'auth',
                      f'Failed login attempt for username: {username}', ip, ua)
            db.commit()
            # Count how many attempts remain before lockout
            from utils import _recent_attempts
            attempts_so_far = len(_recent_attempts(ip))
            attempts_left   = max(0, 5 - attempts_so_far)
            if attempts_left == 0:
                secs = lockout_seconds_remaining(ip)
                mins = max(1, round(secs / 60))
                flash(
                    f'Too many failed attempts — your login is now locked for '
                    f'<strong>{mins} minute{"s" if mins != 1 else ""}</strong>. '
                    f'Please wait before trying again.',
                    'lockout'
                )
            elif attempts_left <= 2:
                flash(
                    f'Incorrect username or password. '
                    f'<strong>{attempts_left} attempt{"s" if attempts_left != 1 else ""} remaining</strong> '
                    f'before your login is temporarily locked.',
                    'danger'
                )
            else:
                flash('Incorrect username or password. Please try again.', 'danger')

    return render_template('login.html')


@auth.route('/logout')
@login_required
def logout():
    from flask_login import current_user
    db = get_db()
    from utils import audit
    audit(db, 'LOGOUT', 'auth', 'User logged out')
    db.commit()
    logout_user()
    flash('You have been logged out', 'info')
    return redirect(url_for('auth.index'))


@auth.route('/setup')
def setup():
    return '<h2>Not available</h2>', 404


@auth.route('/debug-auth')
def debug_auth():
    return '<h2>Not available</h2>', 404


@auth.route('/emergency-reset', methods=['GET', 'POST'])
def emergency_reset():
    """
    Emergency admin password reset.
    Enable with ENABLE_SUPPORT_ROUTES=1, then send RESET_TOKEN by POST body or
    X-Reset-Token header. After resetting, delete the support variables.
    """
    if not _support_routes_enabled():
        return '<h2>Not available</h2>', 404

    if request.method != 'POST':
        return '<h2>Reset requires POST.</h2>', 405

    if not _reset_token_is_valid():
        return '<h2>Invalid token.</h2>', 403

    new_password = os.environ.get('ADMIN_PASSWORD', '')
    if not new_password:
        return '<h2>Reset not available.</h2>', 400

    try:
        db = get_db()
        db.execute(
            'UPDATE users SET password_hash = ? WHERE username = ?',
            (generate_password_hash(new_password), 'admin')
        )
        db.commit()
        rows = db.execute('SELECT id, username, role FROM users WHERE username = ?', ('admin',)).fetchone()
        if rows:
            return '''
            <h2 style="color:green">&#10003; Admin password reset successfully.</h2>
            <p>Username: <strong>admin</strong></p>
            <p><a href="/login">Go to Login</a></p>
            <hr>
            <p style="color:red"><strong>Security:</strong> Remove support reset variables now.</p>
            ''', 200
        else:
            return '<h2>Admin user not found.</h2><p>No user with username "admin" exists in the database.</p>', 404
    except Exception:
        return '<h2>Reset failed.</h2>', 500
