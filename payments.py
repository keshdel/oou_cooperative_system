"""
Payment Gateway Module — OOU Cooperative
Supports Paystack and Flutterwave.

Keys are NEVER hardcoded. Priority order for each key:
  1. settings table (admin-managed, allows runtime rotation)
  2. environment variable (set by devops / .env file)
  3. empty string (gateway will raise on first use if key missing)
"""

import hashlib
import hmac
import json
import secrets
import urllib.request
import urllib.error
from datetime import datetime


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _setting(key: str, env_fallback: str = '', default: str = '') -> str:
    """
    Read a setting value.  Preference order:
      1. settings DB  (allows admin to rotate keys without restart)
      2. environment variable
      3. supplied default
    """
    try:
        from database import get_db
        db  = get_db()
        row = db.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
        if row and row['value']:
            return row['value']
    except Exception:
        pass
    return env_fallback or default


def generate_reference(prefix: str = 'PAY') -> str:
    """Generate a unique payment reference like PAY-3f8a1b2c."""
    return f"{prefix}-{secrets.token_hex(8)}"


# ─── Paystack ─────────────────────────────────────────────────────────────────

class PaystackGateway:
    BASE_URL = 'https://api.paystack.co'

    @property
    def _secret(self) -> str:
        return _setting('paystack_secret_key', 'PAYSTACK_SECRET_KEY')

    @property
    def _public(self) -> str:
        return _setting('paystack_public_key', 'PAYSTACK_PUBLIC_KEY')

    def _headers(self) -> dict:
        secret = self._secret
        if not secret:
            raise RuntimeError('Paystack secret key is not configured.')
        return {
            'Authorization': f'Bearer {secret}',
            'Content-Type': 'application/json',
        }

    def _post(self, path: str, payload: dict) -> dict:
        data = json.dumps(payload).encode()
        req  = urllib.request.Request(
            f'{self.BASE_URL}{path}',
            data=data,
            headers=self._headers(),
            method='POST',
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read()
            try:
                return json.loads(body)
            except Exception:
                raise RuntimeError(f'Paystack HTTP {e.code}: {body}') from e

    def _get(self, path: str) -> dict:
        req = urllib.request.Request(
            f'{self.BASE_URL}{path}',
            headers=self._headers(),
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read()
            try:
                return json.loads(body)
            except Exception:
                raise RuntimeError(f'Paystack HTTP {e.code}: {body}') from e

    def initialize(self, email: str, amount_naira: float, reference: str,
                   callback_url: str, metadata: dict | None = None) -> dict:
        """
        Initiate a payment.
        Returns Paystack response; on success, data['authorization_url'] is the
        redirect URL.
        """
        payload = {
            'email':        email,
            'amount':       int(amount_naira * 100),   # kobo
            'reference':    reference,
            'callback_url': callback_url,
        }
        if metadata:
            payload['metadata'] = metadata
        return self._post('/transaction/initialize', payload)

    def verify(self, reference: str) -> dict:
        """Verify a transaction by reference. Returns Paystack response dict."""
        return self._get(f'/transaction/verify/{reference}')

    def validate_webhook(self, payload_bytes: bytes, signature_header: str) -> bool:
        """
        Verify Paystack webhook signature.
        Paystack sends X-Paystack-Signature: HMAC-SHA512(secret, body)
        """
        secret = self._secret
        if not secret:
            return False
        computed = hmac.new(
            secret.encode('utf-8'),
            payload_bytes,
            hashlib.sha512,
        ).hexdigest()
        return hmac.compare_digest(computed, signature_header or '')

    @property
    def public_key(self) -> str:
        return self._public


# ─── Flutterwave ──────────────────────────────────────────────────────────────

class FlutterwaveGateway:
    BASE_URL = 'https://api.flutterwave.com/v3'

    @property
    def _secret(self) -> str:
        return _setting('flutterwave_secret_key', 'FLW_SECRET_KEY')

    @property
    def _public(self) -> str:
        return _setting('flutterwave_public_key', 'FLW_PUBLIC_KEY')

    @property
    def _webhook_hash(self) -> str:
        return _setting('flutterwave_webhook_hash', 'FLW_WEBHOOK_HASH')

    def _headers(self) -> dict:
        secret = self._secret
        if not secret:
            raise RuntimeError('Flutterwave secret key is not configured.')
        return {
            'Authorization': f'Bearer {secret}',
            'Content-Type': 'application/json',
        }

    def _post(self, path: str, payload: dict) -> dict:
        data = json.dumps(payload).encode()
        req  = urllib.request.Request(
            f'{self.BASE_URL}{path}',
            data=data,
            headers=self._headers(),
            method='POST',
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read()
            try:
                return json.loads(body)
            except Exception:
                raise RuntimeError(f'Flutterwave HTTP {e.code}: {body}') from e

    def _get(self, path: str) -> dict:
        req = urllib.request.Request(
            f'{self.BASE_URL}{path}',
            headers=self._headers(),
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read()
            try:
                return json.loads(body)
            except Exception:
                raise RuntimeError(f'Flutterwave HTTP {e.code}: {body}') from e

    def initialize(self, email: str, amount_naira: float, reference: str,
                   redirect_url: str, name: str = '', phone: str = '',
                   description: str = '') -> dict:
        """
        Initiate a Flutterwave payment.
        Returns API response; on success, data['link'] is the hosted payment URL.
        """
        payload = {
            'tx_ref':       reference,
            'amount':       amount_naira,
            'currency':     'NGN',
            'redirect_url': redirect_url,
            'customer': {
                'email':       email,
                'name':        name or email,
                'phonenumber': phone or '',
            },
            'customizations': {
                'title':       'OOU Cooperative',
                'description': description or 'Payment to OOU Cooperative',
            },
        }
        return self._post('/payments', payload)

    def verify(self, transaction_id: str) -> dict:
        """Verify a transaction by Flutterwave transaction ID."""
        return self._get(f'/transactions/{transaction_id}/verify')

    def validate_webhook(self, signature_header: str) -> bool:
        """
        Flutterwave sends verif-hash header set to your configured webhook hash.
        Simply compare header == configured hash.
        """
        expected = self._webhook_hash
        if not expected:
            return False
        return hmac.compare_digest(expected, signature_header or '')

    @property
    def public_key(self) -> str:
        return self._public


# ─── Factory ──────────────────────────────────────────────────────────────────

def get_gateway(gateway_name: str | None = None):
    """
    Return the configured gateway instance.
    gateway_name overrides the DB/env setting.
    """
    if gateway_name is None:
        gateway_name = _setting('active_gateway', 'ACTIVE_PAYMENT_GATEWAY', 'paystack')
    gateway_name = (gateway_name or 'paystack').lower().strip()
    if gateway_name == 'flutterwave':
        return FlutterwaveGateway()
    return PaystackGateway()  # default
