# crypto.py — symmetric encryption for sensitive PII fields (BVN, NIN, etc.)
#
# Requires env var FIELD_ENCRYPTION_KEY (a URL-safe base64-encoded 32-byte key).
# Generate one with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
#
# If the key is absent, encrypt/decrypt are no-ops so the app still starts in
# development without the variable set. Set the key in production.

import os
from cryptography.fernet import Fernet, InvalidToken

_raw_key = os.environ.get('FIELD_ENCRYPTION_KEY', '').encode()
_fernet = Fernet(_raw_key) if _raw_key else None
ENCRYPTED_PREFIX = 'enc:'
SENSITIVE_MEMBER_FIELDS = ('bank_name', 'account_name', 'account_number', 'bvn', 'nin')


def encryption_enabled() -> bool:
    return _fernet is not None


def is_encrypted(value: str) -> bool:
    return bool(value) and str(value).startswith(ENCRYPTED_PREFIX)


def encrypt_field(value: str) -> str:
    """Encrypt a plaintext string. Returns ciphertext prefixed with 'enc:'.
    Returns the original value unchanged if no encryption key is configured."""
    if not _fernet or not value:
        return value
    if is_encrypted(value):
        return value
    return ENCRYPTED_PREFIX + _fernet.encrypt(str(value).encode()).decode()


def decrypt_field(value: str) -> str:
    """Decrypt a value produced by encrypt_field.
    Returns the original value unchanged if not encrypted or key is absent."""
    if not _fernet or not value or not is_encrypted(value):
        return value
    try:
        return _fernet.decrypt(value[len(ENCRYPTED_PREFIX):].encode()).decode()
    except InvalidToken:
        return value  # return raw if decryption fails (e.g. key rotation)


def mask_field(value: str, visible: int = 4) -> str:
    """Return a display-safe masked value without exposing full sensitive data."""
    plaintext = decrypt_field(value or '')
    if not plaintext:
        return ''
    plaintext = str(plaintext)
    if len(plaintext) <= visible:
        return '*' * len(plaintext)
    return ('*' * (len(plaintext) - visible)) + plaintext[-visible:]


def encrypt_member_sensitive_fields(values: dict) -> dict:
    data = dict(values)
    for field in SENSITIVE_MEMBER_FIELDS:
        if field in data:
            data[field] = encrypt_field(data.get(field) or '')
    return data


def decrypt_member_sensitive_fields(values: dict) -> dict:
    data = dict(values)
    for field in SENSITIVE_MEMBER_FIELDS:
        if field in data:
            data[field] = decrypt_field(data.get(field) or '')
    return data


def mask_member_sensitive_fields(values: dict) -> dict:
    data = dict(values)
    for field in SENSITIVE_MEMBER_FIELDS:
        if field in data:
            data[field + '_masked'] = mask_field(data.get(field) or '')
    return data
