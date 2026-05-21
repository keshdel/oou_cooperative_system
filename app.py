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
app.register_blueprint(mobile_api)

# ── Context processor ─────────────────────────────────────────────────────────

@app.context_processor
def utility_processor():
    db = get_db()
    coop_name  = db.execute("SELECT value FROM settings WHERE key = 'coop_name'").fetchone()
    coop_logo  = db.execute("SELECT value FROM settings WHERE key = 'coop_logo'").fetchone()
    coop_short = db.execute("SELECT value FROM settings WHERE key = 'coop_short_name'").fetchone()
    return {
        'now':            datetime.now,
        'coop_name':      coop_name['value']  if coop_name  else 'OOU Cooperative',
        'coop_logo':      coop_logo['value']  if coop_logo  else '',
        'coop_short_name': coop_short['value'] if coop_short else 'Coop',
    }


# ── Before-request hook ───────────────────────────────────────────────────────

@app.before_request
def check_maintenance():
    if current_user.is_authenticated and current_user.role == 'admin':
        return
    maintenance = False  # fetch from settings when needed
    if maintenance and request.endpoint not in ['auth.login', 'static']:
        return render_template('errors/maintenance.html'), 503


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
