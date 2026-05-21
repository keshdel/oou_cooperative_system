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


def encrypt_field(value: str) -> str:
    """Encrypt a plaintext string. Returns ciphertext prefixed with 'enc:'.
    Returns the original value unchanged if no encryption key is configured."""
    if not _fernet or not value:
        return value
    return 'enc:' + _fernet.encrypt(value.encode()).decode()


def decrypt_field(value: str) -> str:
    """Decrypt a value produced by encrypt_field.
    Returns the original value unchanged if not encrypted or key is absent."""
    if not _fernet or not value or not value.startswith('enc:'):
        return value
    try:
        return _fernet.decrypt(value[4:].encode()).decode()
    except InvalidToken:
        return value  # return raw if decryption fails (e.g. key rotation)
