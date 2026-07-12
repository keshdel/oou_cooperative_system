"""
Security utilities: audit logging, 2FA helpers.
"""

import pyotp
import hashlib
import secrets
import string
from datetime import datetime, timedelta


DEFAULT_PASSWORD_POLICY = {
    'password_min_length': '8',
    'password_require_upper': '1',
    'password_require_lower': '1',
    'password_require_number': '1',
    'password_require_special': '0',
}


def _setting(db, key, default=''):
    if not db:
        return default
    try:
        row = db.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
        return str(row['value']) if row and row['value'] is not None else default
    except Exception:
        return default


def password_policy(db=None):
    policy = {}
    for key, default in DEFAULT_PASSWORD_POLICY.items():
        policy[key] = _setting(db, key, default)

    try:
        policy['password_min_length'] = str(max(6, min(128, int(policy['password_min_length']))))
    except (TypeError, ValueError):
        policy['password_min_length'] = DEFAULT_PASSWORD_POLICY['password_min_length']
    return policy


def password_policy_description(db=None):
    policy = password_policy(db)
    parts = [f"at least {policy['password_min_length']} characters"]
    if policy['password_require_upper'] == '1':
        parts.append('an uppercase letter')
    if policy['password_require_lower'] == '1':
        parts.append('a lowercase letter')
    if policy['password_require_number'] == '1':
        parts.append('a number')
    if policy['password_require_special'] == '1':
        parts.append('a special character')
    return 'Password must contain ' + ', '.join(parts) + '.'


def validate_password_strength(password, db=None):
    policy = password_policy(db)
    password = password or ''
    errors = []
    min_length = int(policy['password_min_length'])

    if len(password) < min_length:
        errors.append(f'Password must be at least {min_length} characters.')
    if policy['password_require_upper'] == '1' and not any(c.isupper() for c in password):
        errors.append('Password must include an uppercase letter.')
    if policy['password_require_lower'] == '1' and not any(c.islower() for c in password):
        errors.append('Password must include a lowercase letter.')
    if policy['password_require_number'] == '1' and not any(c.isdigit() for c in password):
        errors.append('Password must include a number.')
    if policy['password_require_special'] == '1' and not any(c in string.punctuation for c in password):
        errors.append('Password must include a special character.')

    return not errors, errors


def generate_compliant_password(db=None, length=None):
    policy = password_policy(db)
    min_length = int(policy['password_min_length'])
    target_length = max(length or 14, min_length)
    chars = []
    if policy['password_require_upper'] == '1':
        chars.append(secrets.choice(string.ascii_uppercase))
    if policy['password_require_lower'] == '1':
        chars.append(secrets.choice(string.ascii_lowercase))
    if policy['password_require_number'] == '1':
        chars.append(secrets.choice(string.digits))
    if policy['password_require_special'] == '1':
        chars.append(secrets.choice('!@#$%^&*'))

    alphabet = string.ascii_letters + string.digits + '!@#$%^&*'
    while len(chars) < target_length:
        chars.append(secrets.choice(alphabet))

    secrets.SystemRandom().shuffle(chars)
    return ''.join(chars)


def generate_account_setup_token():
    """Return a plaintext token for email delivery and its database hash."""
    token = secrets.token_urlsafe(32)
    return token, hash_account_setup_token(token)


def hash_account_setup_token(token):
    return hashlib.sha256((token or '').encode('utf-8')).hexdigest()


# ── Audit logging ────────────────────────────────────────────────────────────

def log_audit(db, user_id, username, action, module, description,
              ip_address='', user_agent='', data=''):
    """Insert a row into audit_log. Never raises — audit must not crash main flow."""
    try:
        db.execute(
            '''INSERT INTO audit_log
               (user_id, username, action, module, description, ip_address, user_agent, data)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (user_id, username, action, module, description, ip_address, user_agent, data)
        )
    except Exception as exc:
        print(f"[audit] failed to write log: {exc}")


# ── 2FA helpers ──────────────────────────────────────────────────────────────

class SecurityManager:
    def generate_2fa_secret(self):
        """Return a fresh TOTP secret for a user."""
        return pyotp.random_base32()

    def verify_2fa(self, secret, token):
        """Return True if the TOTP token is valid for the given secret."""
        return pyotp.TOTP(secret).verify(token)

    def generate_backup_codes(self, count=10):
        """Return a list of one-time backup codes (plaintext + sha-256 hash)."""
        codes = []
        for _ in range(count):
            code = secrets.token_hex(4).upper()
            codes.append({
                'code': code,
                'hashed': hashlib.sha256(code.encode()).hexdigest(),
                'used': False,
            })
        return codes

    def get_totp_uri(self, secret, username, issuer='OOU Cooperative'):
        """Return an otpauth:// URI for QR-code provisioning."""
        return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=issuer)
