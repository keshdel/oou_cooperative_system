"""
CTAS — Cooperative Target Advance Scheme.

A balloted target-advance (ajo/esusu) module: members subscribe to a cycle, a
seeded ballot assigns each a payout month, they receive a lump-sum advance, and
it is recovered via monthly payroll deductions. Money posts to the real
double-entry GL (CTAS Advances Receivable + CTAS Admin Fee Income) and reuses
CoopMS members.

**Optional feature.** Off by default and leaves no footprint (no menu, no
routes, no GL accounts) until a cooperative is activated — on request — either by
the operator from HQ (POST /api/hq/set-feature) or by a super admin here. Gate
every CTAS view with @ctas_required.
"""

from functools import wraps

from flask import Blueprint, abort, flash, redirect, request, url_for
from flask_login import current_user, login_required

from database import get_db
from utils import audit

ctas = Blueprint('ctas', __name__)

CTAS_ADVANCES = '1150'            # asset — advances paid out, recovered via payroll
CTAS_ADMIN_FEE_INCOME = '4150'   # income — admin fee charged on subscription


def ctas_enabled(db=None) -> bool:
    """True if this cooperative has the CTAS add-on switched on."""
    try:
        db = db or get_db()
        row = db.execute("SELECT value FROM settings WHERE key = 'ctas_enabled'").fetchone()
        return bool(row and str(row['value']) == '1')
    except Exception:
        return False


def ctas_ensure_accounts(db) -> None:
    """Seed the two CTAS GL accounts — only when the feature is turned on, so a
    coop that never uses CTAS keeps a clean chart of accounts."""
    for code, name, atype, normal in (
        (CTAS_ADVANCES, 'CTAS Advances Receivable', 'asset', 'debit'),
        (CTAS_ADMIN_FEE_INCOME, 'CTAS Admin Fee Income', 'income', 'credit'),
    ):
        try:
            db.execute(
                "INSERT INTO accounts (code, name, type, normal_balance, parent_code) "
                "VALUES (?, ?, ?, ?, NULL) ON CONFLICT(code) DO NOTHING",
                (code, name, atype, normal))
        except Exception:
            pass


def set_ctas_enabled(db, on: bool) -> None:
    """Turn the CTAS add-on on/off for this cooperative (seeds accounts on-on)."""
    db.execute("DELETE FROM settings WHERE key = 'ctas_enabled'")
    db.execute("INSERT INTO settings (key, value) VALUES ('ctas_enabled', ?)", ('1' if on else '0',))
    if on:
        ctas_ensure_accounts(db)


def ctas_required(f):
    """Feature gate: 404 unless CTAS is enabled here; then require login."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not ctas_enabled():
            abort(404)
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login', next=request.path))
        return f(*args, **kwargs)
    return wrapper


def _is_operator() -> bool:
    return current_user.is_authenticated and getattr(current_user, 'is_super_admin', False)


@ctas.route('/ctas/enable', methods=['POST'])
@login_required
def enable_ctas():
    """Activate CTAS for this cooperative — operator/super-admin only."""
    if not _is_operator():
        abort(403)
    db = get_db()
    set_ctas_enabled(db, True)
    audit(db, 'CTAS_ENABLED', 'ctas', 'Target Advance Scheme enabled')
    db.commit()
    flash('CTAS (Target Advance Scheme) is now enabled.', 'success')
    return redirect(request.referrer or url_for('main.dashboard'))


@ctas.route('/ctas/disable', methods=['POST'])
@login_required
def disable_ctas():
    if not _is_operator():
        abort(403)
    db = get_db()
    set_ctas_enabled(db, False)
    audit(db, 'CTAS_DISABLED', 'ctas', 'Target Advance Scheme disabled')
    db.commit()
    flash('CTAS has been disabled.', 'info')
    return redirect(request.referrer or url_for('main.dashboard'))
