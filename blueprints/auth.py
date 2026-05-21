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
        if is_rate_limited(ip):
            flash('Too many failed attempts. Please wait 15 minutes before trying again.', 'danger')
            return render_template('login.html')

        username = request.form['username']
        password = request.form['password']
        ua       = request.user_agent.string if request.user_agent else ''

        db   = get_db()
        user = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()

        if user and check_password_hash(user['password_hash'], password):
            clear_login_attempts(ip)
            user_obj = User(
                user['id'], user['username'], user['password_hash'], user['role'],
                user['email'] if 'email' in user.keys() else ''
            )
            login_user(user_obj)
            log_audit(db, user['id'], user['username'], 'LOGIN', 'auth', 'User logged in', ip, ua)
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
