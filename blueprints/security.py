"""
Self-service two-factor authentication (TOTP) management for logged-in users.

Login verification lives in blueprints/auth.py; this blueprint is only the
"manage my own 2FA" area: enable, view/regenerate backup codes, and disable.
"""
import pyotp
from flask import (Blueprint, flash, redirect, render_template, request,
                   session, url_for)
from flask_login import current_user, login_required

from database import get_db
from security import (security_manager, totp_qr_svg, enable_user_2fa,
                      disable_user_2fa, regenerate_backup_codes,
                      count_unused_backup_codes, user_2fa_secret,
                      role_requires_2fa, log_audit)

security_bp = Blueprint('security', __name__, url_prefix='/security')

_SETUP_SECRET_KEY = '2fa_setup_secret'
_BACKUP_CODES_KEY = '2fa_backup_codes'


def _user_2fa_enabled(db, user_id) -> bool:
    row = db.execute(
        'SELECT two_factor_enabled FROM users WHERE id = ?', (user_id,)
    ).fetchone()
    return bool(row and row['two_factor_enabled'])


def _audit(db, action, description):
    log_audit(db, current_user.id, current_user.username, action, 'security',
              description, request.remote_addr or '',
              request.user_agent.string if request.user_agent else '')


@security_bp.route('/')
@login_required
def index():
    db = get_db()
    enabled = _user_2fa_enabled(db, current_user.id)
    remaining = count_unused_backup_codes(db, current_user.id) if enabled else 0
    required = role_requires_2fa(getattr(current_user, 'role', ''), db)
    return render_template('security/index.html',
                           two_factor_enabled=enabled,
                           backup_codes_remaining=remaining,
                           two_factor_required=required)


@security_bp.route('/2fa/setup', methods=['GET', 'POST'])
@login_required
def setup_2fa():
    db = get_db()
    if _user_2fa_enabled(db, current_user.id):
        flash('Two-factor authentication is already on for your account.', 'info')
        return redirect(url_for('security.index'))

    # Keep one secret for the whole setup attempt so refreshing the page (or a
    # failed code) doesn't invalidate the QR the user already scanned.
    secret = session.get(_SETUP_SECRET_KEY)
    if not secret:
        secret = security_manager.generate_2fa_secret()
        session[_SETUP_SECRET_KEY] = secret

    if request.method == 'POST':
        code = (request.form.get('code') or '').strip()
        if pyotp.TOTP(secret).verify(code, valid_window=1):
            codes = enable_user_2fa(db, current_user.id, secret)
            _audit(db, 'ENABLE_2FA', 'Two-factor authentication enabled')
            db.commit()
            session.pop(_SETUP_SECRET_KEY, None)
            session[_BACKUP_CODES_KEY] = codes
            flash('Two-factor authentication is now on. Save your backup codes.',
                  'success')
            return redirect(url_for('security.show_backup_codes'))
        flash('That code was not valid. Make sure your device time is correct '
              'and enter the current 6-digit code.', 'danger')

    uri = security_manager.get_totp_uri(secret, current_user.username)
    return render_template('security/setup-2fa.html',
                           secret=secret, qr_svg=totp_qr_svg(uri))


@security_bp.route('/2fa/backup-codes')
@login_required
def show_backup_codes():
    codes = session.pop(_BACKUP_CODES_KEY, None)
    if not codes:
        # Nothing fresh to show — codes are only ever displayed once.
        return redirect(url_for('security.index'))
    return render_template('security/backup-codes.html', codes=codes)


@security_bp.route('/2fa/backup-codes/regenerate', methods=['POST'])
@login_required
def regenerate_backup_codes_route():
    db = get_db()
    if not _user_2fa_enabled(db, current_user.id):
        flash('Turn on two-factor authentication first.', 'warning')
        return redirect(url_for('security.index'))
    codes = regenerate_backup_codes(db, current_user.id)
    _audit(db, 'REGENERATE_2FA_BACKUP_CODES', 'Regenerated 2FA backup codes')
    db.commit()
    session[_BACKUP_CODES_KEY] = codes
    flash('Your old backup codes are now invalid. Save the new ones.', 'success')
    return redirect(url_for('security.show_backup_codes'))


@security_bp.route('/2fa/disable', methods=['POST'])
@login_required
def disable_2fa():
    db = get_db()
    if not _user_2fa_enabled(db, current_user.id):
        return redirect(url_for('security.index'))

    # Enforced staff cannot switch it off; an admin reset is the only way out.
    if role_requires_2fa(getattr(current_user, 'role', ''), db):
        flash('Two-factor authentication is required for your role and cannot '
              'be turned off. Contact an administrator if you are locked out.',
              'danger')
        return redirect(url_for('security.index'))

    # Confirm with a current code so a hijacked session can't silently remove it.
    code = (request.form.get('code') or '').strip()
    secret = user_2fa_secret(db, current_user.id)
    if not (secret and pyotp.TOTP(secret).verify(code, valid_window=1)):
        flash('Enter a current code from your authenticator app to turn off 2FA.',
              'danger')
        return redirect(url_for('security.index'))

    disable_user_2fa(db, current_user.id)
    _audit(db, 'DISABLE_2FA', 'Two-factor authentication disabled')
    db.commit()
    flash('Two-factor authentication has been turned off.', 'success')
    return redirect(url_for('security.index'))
