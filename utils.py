"""
Shared helpers used across all blueprints.
Import from here — never from app.py — to avoid circular imports.
"""

import time
from collections import defaultdict
from functools import wraps
from io import BytesIO

from flask import flash, redirect, url_for, request
from flask_login import UserMixin, current_user
from PIL import Image
from werkzeug.utils import secure_filename

from security import log_audit


# ── User model ───────────────────────────────────────────────────────────────

class User(UserMixin):
    def __init__(self, id, username, password_hash, role, email=''):
        self.id            = id
        self.username      = username
        self.password_hash = password_hash
        self.role          = role
        self.email         = email


# ── Role-based access control ─────────────────────────────────────────────────

def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if current_user.role not in roles:
                flash('Access denied. Insufficient privileges.', 'danger')
                return redirect(url_for('main.dashboard'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


# ── Login rate limiting ───────────────────────────────────────────────────────

_login_attempts: dict = defaultdict(list)
_RATE_WINDOW = 300   # 5 minutes
_RATE_MAX    = 5     # failures before block
_RATE_BLOCK  = 900   # block duration (seconds)


def is_rate_limited(ip: str) -> bool:
    now      = time.time()
    attempts = [t for t in _login_attempts[ip] if now - t < _RATE_WINDOW]
    _login_attempts[ip] = attempts
    return len(attempts) >= _RATE_MAX


def record_failed_login(ip: str) -> None:
    _login_attempts[ip].append(time.time())


def clear_login_attempts(ip: str) -> None:
    _login_attempts.pop(ip, None)


# ── File upload validation ────────────────────────────────────────────────────

_ALLOWED_IMAGE_EXTS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
_MAX_UPLOAD_BYTES   = 5 * 1024 * 1024  # 5 MB


def validate_image(file) -> tuple[bool, str]:
    """Check extension, file size, and real image content via Pillow.
    Seeks stream back to 0 on success so the caller can still save it."""
    filename = secure_filename(file.filename or '')
    if '.' not in filename:
        return False, 'File has no extension.'
    ext = filename.rsplit('.', 1)[1].lower()
    if ext not in _ALLOWED_IMAGE_EXTS:
        return False, f'File type .{ext} is not allowed. Use PNG, JPG, GIF, or WEBP.'
    data = file.read(_MAX_UPLOAD_BYTES + 1)
    if len(data) > _MAX_UPLOAD_BYTES:
        return False, 'File too large. Maximum size is 5 MB.'
    try:
        img = Image.open(BytesIO(data))
        img.verify()
    except Exception:
        return False, 'File does not appear to be a valid image.'
    file.stream.seek(0)
    return True, ''


# ── Audit helper ──────────────────────────────────────────────────────────────

def audit(db, action: str, module: str, description: str, data: str = '') -> None:
    """Write an audit log entry pre-filled from the current request/user."""
    uid   = current_user.id       if not current_user.is_anonymous else None
    uname = current_user.username if not current_user.is_anonymous else 'anonymous'
    ip    = request.remote_addr or ''
    ua    = request.user_agent.string if request.user_agent else ''
    log_audit(db, uid, uname, action, module, description, ip, ua, data)
