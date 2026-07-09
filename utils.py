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
    def __init__(self, id, username, password_hash, role, email='',
                 must_change_password=0):
        self.id                  = id
        self.username            = username
        self.password_hash       = password_hash
        self.role                = role
        self.email               = email
        self.must_change_password = bool(must_change_password)


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


STAFF_ROLES = {'admin', 'treasurer', 'secretary', 'exco'}


def is_staff_user() -> bool:
    return getattr(current_user, 'role', None) in STAFF_ROLES


def member_for_user(db, user_id=None):
    """Return the members row linked to a user by matching email, or None.

    This is the single source of truth for the user↔member email link used by
    the member portal, the online-payments blueprint, and the mobile API.

    With no ``user_id`` it uses the current logged-in user's email; with a
    ``user_id`` it looks up that user's email first.
    """
    if user_id is None:
        email = getattr(current_user, 'email', '') or ''
    else:
        urow  = db.execute('SELECT email FROM users WHERE id = ?', (user_id,)).fetchone()
        email = (urow['email'] if urow else '') or ''
    if not email:
        return None
    return db.execute('SELECT * FROM members WHERE email = ?', (email,)).fetchone()


def current_member_id(db):
    """Return the member id linked to the logged-in user by email."""
    member = member_for_user(db)
    return member['id'] if member else None


def can_access_member(db, member_id: int) -> bool:
    """Staff can access any member; members can access only their own profile."""
    if is_staff_user():
        return True
    own_id = current_member_id(db)
    return own_id == member_id


# ── Login rate limiting ───────────────────────────────────────────────────────

_login_attempts: dict = defaultdict(list)
_RATE_WINDOW = 300   # 5 minutes sliding window
_RATE_MAX    = 5     # failures before block
_RATE_BLOCK  = 900   # block duration in seconds (15 min)


def _recent_attempts(ip: str) -> list:
    """Return timestamps of failed attempts still within the block window."""
    now = time.time()
    attempts = [t for t in _login_attempts[ip] if now - t < _RATE_BLOCK]
    _login_attempts[ip] = attempts
    return attempts


def is_rate_limited(ip: str) -> bool:
    return len(_recent_attempts(ip)) >= _RATE_MAX


def lockout_seconds_remaining(ip: str) -> int:
    """Return seconds until the oldest blocking attempt expires (0 if not locked)."""
    attempts = _recent_attempts(ip)
    if len(attempts) < _RATE_MAX:
        return 0
    oldest = min(attempts)
    remaining = int(_RATE_BLOCK - (time.time() - oldest))
    return max(remaining, 0)


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


# ── Financial helpers ─────────────────────────────────────────────────────────

def record_revenue(db, category, amount, description='', source='',
                   received_by=None, notes=''):
    """Insert a revenue (income) row, e.g. for late fees or loan fees.

    Does NOT commit — it runs inside the caller's transaction so the income
    is booked atomically with the operation that generated it. Skips zero /
    non-positive amounts. Never raises (income logging must not break the
    main flow).
    """
    try:
        amount = float(amount or 0)
    except (TypeError, ValueError):
        return
    if amount <= 0:
        return
    import random
    from datetime import datetime
    revenue_number = f"REV/{datetime.now().strftime('%Y%m%d')}/{random.randint(1000, 9999)}"
    try:
        db.execute(
            '''INSERT INTO revenue
               (revenue_number, category, amount, description, source, date, received_by, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (revenue_number, category, amount, description, source,
             datetime.now(), received_by, notes)
        )
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[revenue] failed to record {category} {amount}: {exc}")


def member_savings_balance(db, member_id):
    """Authoritative savings balance from the savings ledger (source of truth).

    Use this for financial decisions such as loan eligibility rather than the
    cached members.total_savings column, which can drift over time.
    """
    row = db.execute(
        'SELECT COALESCE(SUM(amount), 0) FROM savings WHERE member_id = ?',
        (member_id,)
    ).fetchone()
    return float(row[0] or 0) if row else 0.0


def reconcile_member_savings(db, member_id=None):
    """Recompute members.total_savings from the savings ledger.

    Reconciles a single member when member_id is given, otherwise every member.
    Does NOT commit — the caller owns the transaction. Returns the number of
    members whose cached balance was corrected.
    """
    if member_id is not None:
        ids = [member_id]
    else:
        ids = [r['id'] for r in db.execute('SELECT id FROM members').fetchall()]
    corrected = 0
    for mid in ids:
        ledger = member_savings_balance(db, mid)
        cur    = db.execute('SELECT total_savings FROM members WHERE id = ?', (mid,)).fetchone()
        cur_val = float(cur['total_savings'] or 0) if cur else 0.0
        if abs(cur_val - ledger) > 0.005:
            db.execute('UPDATE members SET total_savings = ? WHERE id = ?', (ledger, mid))
            corrected += 1
    return corrected


def split_repayment(amount, principal, total_repayment):
    """Split a loan repayment into (principal_part, interest_part).

    The loan's ``balance`` is stored as total_repayment (principal + all
    interest combined), so a payment must be apportioned. We split each
    payment in the loan's principal:interest ratio. This is a proportional
    (not per-period amortising) allocation, but it reconciles exactly over the
    life of the loan: the principal parts sum to the original principal and the
    interest parts sum to the total interest.

    Returns (principal_part, interest_part), each rounded to 2 dp.
    """
    try:
        amount          = float(amount or 0)
        principal       = float(principal or 0)
        total_repayment = float(total_repayment or 0)
    except (TypeError, ValueError):
        return round(float(amount or 0), 2), 0.0
    if total_repayment <= 0 or amount <= 0:
        return round(amount, 2), 0.0
    interest_total    = max(total_repayment - principal, 0.0)
    interest_fraction = interest_total / total_repayment
    interest_part     = round(amount * interest_fraction, 2)
    principal_part    = round(amount - interest_part, 2)
    return principal_part, interest_part


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
