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

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-this-in-production')
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
        return User(
            user['id'], user['username'], user['password_hash'], user['role'],
            user['email'] if 'email' in user.keys() else ''
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

def _check_billing_status():
    """
    If BILLING_API_KEY and BILLING_PORTAL_URL are set, call the billing portal
    to verify this cooperative's subscription is active.
    Returns True if active (or if billing is not configured), False if suspended.
    Caches the result in app.config for 60 minutes to avoid hitting the API
    on every single request.
    """
    api_key    = os.environ.get('BILLING_API_KEY', '')
    portal_url = os.environ.get('BILLING_PORTAL_URL', '')
    if not api_key or not portal_url:
        return True  # billing not configured — allow access

    import time
    cache_key   = '_billing_cache'
    cache_until = '_billing_cache_until'
    now = time.time()

    if app.config.get(cache_until, 0) > now:
        return app.config.get(cache_key, True)

    try:
        import urllib.request, json as _json
        url  = f'{portal_url.rstrip("/")}/api/status?api_key={api_key}'
        req  = urllib.request.Request(url, headers={'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = _json.loads(resp.read())
        active = data.get('active', True)
    except Exception:
        active = True  # fail open — don't block app if billing portal is unreachable

    app.config[cache_key]   = active
    app.config[cache_until] = now + 3600  # cache for 1 hour
    return active


@app.before_request
def check_maintenance():
    if current_user.is_authenticated and current_user.role == 'admin':
        return
    maintenance = False  # fetch from settings when needed
    if maintenance and request.endpoint not in ['auth.login', 'static']:
        return render_template('errors/maintenance.html'), 503

    # Billing subscription check
    if request.endpoint not in ['auth.login', 'auth.logout', 'static']:
        if not _check_billing_status():
            return render_template('errors/subscription_expired.html'), 402


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
