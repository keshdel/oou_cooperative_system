"""
SMS delivery — the fallback channel for members without the mobile app.

Push notifications are free but only reach a member who has installed and signed
into the app. SMS reaches every phone, needs no data connection, and unlike
WhatsApp needs no business verification or message templates — just a provider
account, a registered sender ID and prepaid credit.

Credentials are **per cooperative**, held in that tenant's own settings the same
way its Paystack keys are, so each society uses and pays for its own account.

Adding a provider means adding one class with a `send()` method and listing it in
PROVIDERS; nothing else in the codebase changes.
"""

import json
import logging
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

# Nigeria. Cooperatives elsewhere can change this in settings (sms_country_code).
DEFAULT_COUNTRY_CODE = '234'


# ── Phone numbers ─────────────────────────────────────────────────────────────

def normalise_msisdn(raw, country_code=DEFAULT_COUNTRY_CODE):
    """Turn how people actually write their number into E.164 digits.

        08012345678  ->  2348012345678
        +234 801 234 5678 -> 2348012345678
        234-801-2345678   -> 2348012345678

    Returns '' when there is nothing usable, so callers can skip the send.
    """
    if not raw:
        return ''
    digits = re.sub(r'\D', '', str(raw))
    if not digits:
        return ''
    cc = re.sub(r'\D', '', str(country_code or DEFAULT_COUNTRY_CODE)) or DEFAULT_COUNTRY_CODE

    if digits.startswith('00' + cc):          # 00234...
        digits = digits[2:]
    if digits.startswith(cc):                 # already country-coded
        rest = digits[len(cc):]
        return cc + rest.lstrip('0') if rest else ''
    if digits.startswith('0'):                # local trunk form, 0801...
        return cc + digits.lstrip('0')
    # A bare subscriber number (801...), assume the configured country.
    return cc + digits


def looks_sendable(msisdn):
    """Cheap sanity check so we do not spend credit on obvious rubbish."""
    return bool(msisdn) and 10 <= len(msisdn) <= 15 and msisdn.isdigit()


# ── HTTP ──────────────────────────────────────────────────────────────────────

def _post_json(url, payload, headers=None, timeout=15):
    body = json.dumps(payload).encode('utf-8')
    hdrs = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=body, headers=hdrs, method='POST')
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return json.loads(resp.read() or b'{}')


def _post_form(url, fields, timeout=15):
    body = urllib.parse.urlencode(fields).encode('utf-8')
    req = urllib.request.Request(
        url, data=body, method='POST',
        headers={'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return json.loads(resp.read() or b'{}')


# ── Providers ─────────────────────────────────────────────────────────────────

class TermiiProvider:
    """Termii — Nigerian, billed in naira. Reference implementation."""
    name = 'termii'
    BASE = 'https://api.ng.termii.com/api/sms/send'

    def __init__(self, cfg):
        self.api_key = (cfg.get('sms_api_key') or '').strip()
        self.sender = (cfg.get('sms_sender_id') or '').strip() or 'CoopMS'

    def configured(self):
        return bool(self.api_key)

    def send(self, msisdn, text):
        data = _post_json(self.BASE, {
            'to': msisdn, 'from': self.sender, 'sms': text,
            'type': 'plain', 'channel': 'generic', 'api_key': self.api_key,
        })
        # Termii answers with message_id and a code of 'ok' when accepted.
        ok = bool(data.get('message_id')) or str(data.get('code', '')).lower() == 'ok'
        return ok, (data.get('message_id') or ''), ('' if ok else str(data.get('message') or data))


class AfricasTalkingProvider:
    """Africa's Talking — the usual alternative; proves the adapter is swappable."""
    name = 'africastalking'
    BASE = 'https://api.africastalking.com/version1/messaging'

    def __init__(self, cfg):
        self.api_key = (cfg.get('sms_api_key') or '').strip()
        self.username = (cfg.get('sms_username') or '').strip() or 'sandbox'
        self.sender = (cfg.get('sms_sender_id') or '').strip()

    def configured(self):
        return bool(self.api_key)

    def send(self, msisdn, text):
        fields = {'username': self.username, 'to': '+' + msisdn, 'message': text}
        if self.sender:
            fields['from'] = self.sender
        body = urllib.parse.urlencode(fields).encode('utf-8')
        req = urllib.request.Request(
            self.BASE, data=body, method='POST',
            headers={'Content-Type': 'application/x-www-form-urlencoded',
                     'Accept': 'application/json', 'apiKey': self.api_key})
        with urllib.request.urlopen(req, timeout=15, context=ssl.create_default_context()) as resp:
            data = json.loads(resp.read() or b'{}')
        recipients = (data.get('SMSMessageData') or {}).get('Recipients') or []
        first = recipients[0] if recipients else {}
        ok = str(first.get('status', '')).lower() == 'success'
        return ok, (first.get('messageId') or ''), ('' if ok else str(first.get('status') or data))


PROVIDERS = {
    'termii': TermiiProvider,
    'africastalking': AfricasTalkingProvider,
}


# ── Configuration (per cooperative) ───────────────────────────────────────────

_SETTING_KEYS = ('sms_enabled', 'sms_provider', 'sms_api_key', 'sms_sender_id',
                 'sms_username', 'sms_country_code')


def sms_config(db):
    """This cooperative's SMS settings."""
    cfg = {}
    try:
        rows = db.execute(
            "SELECT key, value FROM settings WHERE key IN (%s)"
            % ','.join('?' for _ in _SETTING_KEYS), _SETTING_KEYS).fetchall()
        cfg = {r['key']: (r['value'] or '') for r in rows}
    except Exception:
        pass
    return cfg


def sms_enabled(db):
    cfg = sms_config(db)
    if str(cfg.get('sms_enabled', '')) != '1':
        return False
    provider = get_provider(cfg)
    return bool(provider and provider.configured())


def get_provider(cfg):
    cls = PROVIDERS.get((cfg.get('sms_provider') or 'termii').strip().lower())
    return cls(cfg) if cls else None


# ── Sending ───────────────────────────────────────────────────────────────────

def send_sms(db, msisdn_raw, text, member_id=None, purpose=''):
    """Send one message. Never raises — SMS is a courtesy channel and must not
    break the operation that triggered it. Returns True when the provider
    accepted the message."""
    try:
        cfg = sms_config(db)
        if str(cfg.get('sms_enabled', '')) != '1':
            return False
        provider = get_provider(cfg)
        if not provider or not provider.configured():
            _log(db, member_id, '', purpose, text, 'skipped', 'SMS is not configured')
            return False

        msisdn = normalise_msisdn(msisdn_raw, cfg.get('sms_country_code'))
        if not looks_sendable(msisdn):
            _log(db, member_id, msisdn, purpose, text, 'skipped', 'No usable phone number')
            return False
        if member_id and _opted_out(db, member_id):
            _log(db, member_id, msisdn, purpose, text, 'skipped', 'Member opted out')
            return False

        body = (text or '').strip()
        if not body:
            return False
        if len(body) > 640:                    # keep the bill sane; ~4 segments
            body = body[:637] + '...'

        ok, ref, err = provider.send(msisdn, body)
        _log(db, member_id, msisdn, purpose, body, 'sent' if ok else 'failed', err, ref)
        return ok
    except Exception as exc:                   # pragma: no cover - network
        log.warning('SMS send failed: %s', exc)
        try:
            _log(db, member_id, '', purpose, text, 'failed', str(exc)[:200])
        except Exception:
            pass
        return False


def _opted_out(db, member_id):
    try:
        row = db.execute('SELECT sms_optout FROM members WHERE id = ?', (member_id,)).fetchone()
        return bool(row and row['sms_optout'])
    except Exception:
        return False


def _log(db, member_id, msisdn, purpose, text, status, error='', provider_ref=''):
    """Record every attempt so a cooperative can see what it paid for."""
    try:
        db.execute(
            "INSERT INTO sms_log (member_id, msisdn, purpose, body, status, error, provider_ref) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (member_id, msisdn, purpose, (text or '')[:400], status, (error or '')[:300], provider_ref))
        db.commit()
    except Exception:
        pass
