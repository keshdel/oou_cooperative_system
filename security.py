"""
Security utilities: audit logging, 2FA helpers.
"""

import pyotp
import hashlib
import secrets
from datetime import datetime, timedelta


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
