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


# ── Loan interest computation ─────────────────────────────────────────────────

# Maps canonical purpose names → settings key suffix
PURPOSE_SETTING_KEY = {
    'Regular':        'regular',
    'Housing':        'housing',
    'Emergency':      'emergency',
    'Asset Purchase': 'asset',
    'School Fees':    'school_fees',
}

METHOD_LABELS = {
    'flat':             'Flat Rate',
    'reducing_monthly': 'Declining Balance (Monthly Rate)',
    'reducing_annual':  'Declining Balance (Annual Rate)',
}


def compute_loan_schedule(principal, rate, tenure, method='reducing_annual'):
    """
    Compute loan repayment schedule using one of three interest methods.

    Args:
        principal : Loan amount (float, ₦)
        rate      : Interest rate as a percentage, e.g. 11 for 11%
        tenure    : Loan duration in months (int)
        method    : One of:
            'flat'             — Flat rate on original principal.
                                 Interest = P × (rate/100) × (tenure/12).
                                 Equal monthly instalments; rate is annual.
            'reducing_monthly' — Declining balance; rate IS the monthly rate
                                 (e.g. 2 means 2% per month).  PMT formula.
            'reducing_annual'  — Declining balance; rate is annual, divided
                                 by 12 for each month.  Standard amortisation.

    Returns:
        (monthly_payment: float, total_repayment: float, schedule: list[dict])
        Each schedule dict has: month, payment, principal, interest, balance
    """
    P = float(principal)
    r_pct = float(rate)
    n = int(tenure)

    if n <= 0 or P <= 0:
        return 0.0, 0.0, []

    if method == 'flat':
        total_interest  = P * (r_pct / 100) * (n / 12)
        total_repayment = P + total_interest
        mp       = total_repayment / n          # equal monthly payment
        prin_pm  = P / n
        int_pm   = total_interest / n

        balance  = P
        schedule = []
        for i in range(1, n + 1):
            balance = round(max(0.0, balance - prin_pm), 2)
            schedule.append({
                'month':     i,
                'payment':   round(mp, 2),
                'principal': round(prin_pm, 2),
                'interest':  round(int_pm, 2),
                'balance':   balance,
            })

    elif method == 'reducing_monthly':
        r = r_pct / 100                         # monthly rate as decimal
        if r > 0:
            mp = P * r * (1 + r) ** n / ((1 + r) ** n - 1)
        else:
            mp = P / n
        total_repayment = mp * n

        balance  = P
        schedule = []
        for i in range(1, n + 1):
            interest_p  = round(balance * r, 2)
            principal_p = round(mp - interest_p, 2)
            balance     = round(max(0.0, balance - principal_p), 2)
            if i == n:
                balance = 0.0
            schedule.append({
                'month':     i,
                'payment':   round(mp, 2),
                'principal': principal_p,
                'interest':  interest_p,
                'balance':   balance,
            })

    else:  # 'reducing_annual' — standard amortisation (default)
        r = (r_pct / 100) / 12                  # monthly rate from annual
        if r > 0:
            mp = P * r * (1 + r) ** n / ((1 + r) ** n - 1)
        else:
            mp = P / n
        total_repayment = mp * n

        balance  = P
        schedule = []
        for i in range(1, n + 1):
            interest_p  = round(balance * r, 2)
            principal_p = round(mp - interest_p, 2)
            balance     = round(max(0.0, balance - principal_p), 2)
            if i == n:
                balance = 0.0
            schedule.append({
                'month':     i,
                'payment':   round(mp, 2),
                'principal': principal_p,
                'interest':  interest_p,
                'balance':   balance,
            })

    return round(mp, 2), round(total_repayment, 2), schedule


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


# ── In-app notification helper ────────────────────────────────────────────────

def notify(db, user_id: int, title: str, message: str,
           notification_type: str = 'info', action_url: str = '') -> None:
    """Insert a notification record for a specific user. Never raises."""
    if not user_id:
        return
    try:
        from datetime import datetime
        db.execute('''
            INSERT INTO notifications
                (user_id, title, message, notification_type, is_read, action_url, created_at)
            VALUES (?, ?, ?, ?, 0, ?, ?)
        ''', (user_id, title, message, notification_type, action_url or '', datetime.now()))
    except Exception:
        pass  # notifications are non-critical — never break the main flow


def notify_member(db, member_email: str, title: str, message: str,
                  notification_type: str = 'info', action_url: str = '') -> None:
    """Find the user account matching member_email and create a notification."""
    if not member_email:
        return
    try:
        user = db.execute('SELECT id FROM users WHERE email = ?', (member_email,)).fetchone()
        if user:
            notify(db, user['id'], title, message, notification_type, action_url)
    except Exception:
        pass


# ── Audit helper ──────────────────────────────────────────────────────────────

def audit(db, action: str, module: str, description: str, data: str = '') -> None:
    """Write an audit log entry pre-filled from the current request/user."""
    uid   = current_user.id       if not current_user.is_anonymous else None
    uname = current_user.username if not current_user.is_anonymous else 'anonymous'
    ip    = request.remote_addr or ''
    ua    = request.user_agent.string if request.user_agent else ''
    log_audit(db, uid, uname, action, module, description, ip, ua, data)
