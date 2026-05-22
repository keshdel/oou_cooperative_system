"""
OOU Acctg 2005 Alumni CMS - Cooperative Accounting Software
"""
import os
import re
from datetime import datetime

from flask import Flask, render_template, request
from flask_login import LoginManager, current_user

from database import init_db, get_db
from extensions import mail
from utils import User

# ── App factory ──────────────────────────────────────────────────────────────

app = Flask(__name__)

_KNOWN_BAD_KEYS = {
    'change-this-in-production',
    'your-super-secret-key-change-this-in-production-2024',
    'secret',
    'dev',
    '',
}
_secret_key = os.environ.get('SECRET_KEY', '')
if not _secret_key or _secret_key in _KNOWN_BAD_KEYS:
    raise RuntimeError(
        "\n\n  *** STARTUP ABORTED ***\n"
        "  SECRET_KEY is not set or is using a known insecure default.\n"
        "  Generate a secure key and set it as an environment variable:\n\n"
        "      python -c \"import secrets; print(secrets.token_hex(32))\"\n\n"
        "  Then set:  SECRET_KEY=<generated-value>\n"
    )
app.config['SECRET_KEY'] = _secret_key
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10 MB

# Mail
app.config['MAIL_SERVER']         = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT']           = int(os.environ.get('MAIL_PORT', 587))
app.config['MAIL_USE_TLS']        = os.environ.get('MAIL_USE_TLS', 'True') == 'True'
app.config['MAIL_USERNAME']       = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD']       = os.environ.get('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER', 'noreply@cooperative.com')
mail.init_app(app)

# Override mail config from DB if the admin has saved settings via the UI
with app.app_context():
    try:
        from blueprints.admin_panel import _apply_mail_config
        _db = get_db()
        _apply_mail_config(_db, app)
        mail.init_app(app)
    except Exception:
        pass

# ── Login manager ─────────────────────────────────────────────────────────────

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth.login'


@login_manager.user_loader
def load_user(user_id):
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if user:
        keys = user.keys()
        return User(
            user['id'], user['username'], user['password_hash'], user['role'],
            user['email'] if 'email' in keys else '',
            user['must_change_password'] if 'must_change_password' in keys else 0,
        )
    return None


# ── Database ──────────────────────────────────────────────────────────────────

init_db()

# ── Blueprints ────────────────────────────────────────────────────────────────

from blueprints.auth        import auth
from blueprints.main        import main
from blueprints.members     import members
from blueprints.savings     import savings
from blueprints.loans       import loans
from blueprints.investments import investments
from blueprints.reports     import reports
from blueprints.admin_panel import admin_panel
from blueprints.portal      import portal
from blueprints.cards       import cards
from blueprints.migration   import migration
from blueprints.payments_bp import payments_bp
from mobile_api             import mobile_api

app.register_blueprint(auth)
app.register_blueprint(main)
app.register_blueprint(members)
app.register_blueprint(savings)
app.register_blueprint(loans)
app.register_blueprint(investments)
app.register_blueprint(reports)
app.register_blueprint(admin_panel)
app.register_blueprint(portal)
app.register_blueprint(cards)
app.register_blueprint(migration)
app.register_blueprint(payments_bp)
app.register_blueprint(mobile_api)

# ── Context processor ─────────────────────────────────────────────────────────

@app.context_processor
def utility_processor():
    db = get_db()
    coop_name  = db.execute("SELECT value FROM settings WHERE key = 'coop_name'").fetchone()
    coop_logo  = db.execute("SELECT value FROM settings WHERE key = 'coop_logo'").fetchone()
    coop_short = db.execute("SELECT value FROM settings WHERE key = 'coop_short_name'").fetchone()

    unread_count = 0
    if current_user.is_authenticated:
        try:
            row = db.execute(
                'SELECT COUNT(*) FROM notifications WHERE user_id = ? AND is_read = 0',
                (current_user.id,)
            ).fetchone()
            unread_count = row[0] if row else 0
        except Exception:
            pass

    return {
        'now':                      datetime.now,
        'coop_name':                coop_name['value']  if coop_name  else 'OOU Cooperative',
        'coop_logo':                coop_logo['value']  if coop_logo  else '',
        'coop_short_name':          coop_short['value'] if coop_short else 'Coop',
        'unread_notifications_count': unread_count,
    }


# ── Before-request hook ───────────────────────────────────────────────────────

def _get_subscription_expiry():
    """
    Returns the subscription expiry date string (YYYY-MM-DD) or '' if not set.
    Checks the database settings first, then falls back to SUBSCRIPTION_EXPIRY env var.
    """
    try:
        from database import get_db
        db = get_db()
        row = db.execute(
            "SELECT value FROM settings WHERE key = 'subscription_expiry'"
        ).fetchone()
        db.close()
        if row and row['value']:
            return row['value'].strip()
    except Exception:
        pass
    return os.environ.get('SUBSCRIPTION_EXPIRY', '').strip()


def _check_billing_status():
    """
    Returns True if subscription is active (or billing not configured).
    Reads expiry from DB settings first, then SUBSCRIPTION_EXPIRY env var.
    """
    expiry_str = _get_subscription_expiry()
    if not expiry_str:
        return True  # no billing configured — allow access
    try:
        from datetime import datetime as _dt
        expiry = _dt.strptime(expiry_str, '%Y-%m-%d')
        return _dt.now() < expiry
    except Exception:
        return True  # malformed date — fail open


# Endpoints accessible even when subscription is expired
_BILLING_EXEMPT = {
    'auth.login', 'auth.logout', 'static',
    'admin_panel.subscription_page',
    'admin_panel.subscription_callback',
}


@app.before_request
def check_maintenance():
    if current_user.is_authenticated and current_user.role == 'admin':
        return
    maintenance = False
    if maintenance and request.endpoint not in ['auth.login', 'static']:
        return render_template('errors/maintenance.html'), 503

    # Billing subscription check — admin always gets through
    if request.endpoint not in _BILLING_EXEMPT:
        if not _check_billing_status():
            return render_template(
                'errors/subscription_expired.html',
                expiry=_get_subscription_expiry()
            ), 402


# ── Forced password-change gate ───────────────────────────────────────────────

_ALLOWED_WHILE_FORCED = {
    'portal.change_password',
    'auth.logout',
    'static',
    # payment callbacks must be reachable so gateway redirects don't loop
    'payments.payment_callback',
    'payments.paystack_webhook',
    'payments.flutterwave_webhook',
}

@app.before_request
def enforce_password_change():
    """Redirect any user with must_change_password=True to the change-password
    page until they set a new password.  Static assets and the change/logout
    endpoints are always permitted so the page can actually render."""
    if not current_user.is_authenticated:
        return
    if not getattr(current_user, 'must_change_password', False):
        return
    if request.endpoint in _ALLOWED_WHILE_FORCED:
        return
    from flask import redirect, url_for, flash
    flash('You must set a new password before continuing.', 'warning')
    return redirect(url_for('portal.change_password'))


# ── Error handlers ────────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found_error(error):
    return render_template('errors/404.html'), 404


@app.errorhandler(403)
def forbidden_error(error):
    return render_template('errors/403.html'), 403


@app.errorhandler(500)
def internal_error(error):
    db = get_db()
    db.rollback()
    return render_template('errors/500.html'), 500


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
