"""
CTAS core engine — framework-agnostic business rules ported from the CTAS Django
services (ballot, eligibility, cycle state machine). Pure functions over the
CoopMS SQLite/Postgres schema; no Flask/request state.

Money is handled by the GL in the blueprint; this module only decides
eligibility, cycle transitions, and the balloted payout order.
"""

import random
from datetime import date, datetime

# Subscription lifecycle (mirrors ctas_subscriptions.status).
SUB_SUBMITTED = 'submitted'
SUB_ELIGIBLE = 'eligible'
SUB_FINANCE_REVIEWED = 'finance_reviewed'
SUB_APPROVED = 'approved'
SUB_ENROLLED = 'enrolled'
SUB_SCHEDULED = 'scheduled'
SUB_PAID_OUT = 'paid_out'
SUB_ACTIVE_RECOVERY = 'active_recovery'
SUB_COMPLETED = 'completed'
SUB_CANCELLED = 'cancelled'
SUB_REJECTED = 'rejected'

# Statuses that still occupy a slot / count as "the member's one active scheme".
ACTIVE_SUB_STATES = (
    SUB_SUBMITTED, SUB_ELIGIBLE, SUB_FINANCE_REVIEWED, SUB_APPROVED,
    SUB_ENROLLED, SUB_SCHEDULED, SUB_PAID_OUT, SUB_ACTIVE_RECOVERY,
)

# The approval chain (each step is a governance gate before the ballot).
APPROVAL_ORDER = [SUB_SUBMITTED, SUB_ELIGIBLE, SUB_FINANCE_REVIEWED, SUB_APPROVED, SUB_ENROLLED]
NEXT_STAGE = {APPROVAL_ORDER[i]: APPROVAL_ORDER[i + 1] for i in range(len(APPROVAL_ORDER) - 1)}
PREV_STAGE = {v: k for k, v in NEXT_STAGE.items()}
STAGE_ACTION_LABEL = {
    SUB_ELIGIBLE: 'Confirm eligibility',
    SUB_FINANCE_REVIEWED: 'Finance review',
    SUB_APPROVED: 'Committee approval',
    SUB_ENROLLED: 'Enrol',
}
# Which timestamp/actor columns each advance stamps.
STAGE_STAMP = {
    SUB_ELIGIBLE: ('eligibility_at', 'eligibility_by'),
    SUB_FINANCE_REVIEWED: ('finance_reviewed_at', 'finance_reviewed_by'),
    SUB_APPROVED: ('committee_approved_at', None),   # approved_at/by columns
    SUB_ENROLLED: ('enrolled_at', None),
}


def next_stage(status):
    return NEXT_STAGE.get(status)

# Cycle lifecycle (mirrors ctas_cycles.status).
CYCLE_DRAFT = 'draft'
CYCLE_OPEN = 'open'
CYCLE_CLOSED = 'closed'
CYCLE_READY_FOR_BALLOT = 'ready_for_ballot'
CYCLE_BALLOTED = 'balloted'
CYCLE_ACTIVE = 'active'
CYCLE_COMPLETED = 'completed'

# Allowed cycle state transitions (from -> {to}).
CYCLE_TRANSITIONS = {
    CYCLE_DRAFT: {CYCLE_OPEN},
    CYCLE_OPEN: {CYCLE_CLOSED},
    CYCLE_CLOSED: {CYCLE_READY_FOR_BALLOT, CYCLE_OPEN},
    CYCLE_READY_FOR_BALLOT: {CYCLE_BALLOTED},
    CYCLE_BALLOTED: {CYCLE_ACTIVE},
    CYCLE_ACTIVE: {CYCLE_COMPLETED},
}


def _f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


# ── Financials ────────────────────────────────────────────────────────────────

def monthly_deduction(target_amount, tenure_months):
    tenure = int(tenure_months or 0)
    if tenure <= 0:
        return 0.0
    return round(_f(target_amount) / tenure, 2)


def calculate_admin_fee(cycle, target_amount):
    """Flat fee below the threshold, else percentage capped at admin_fee_cap."""
    amt = _f(target_amount)
    if amt <= 0:
        return 0.0
    threshold = _f(cycle['admin_fee_threshold'])
    if threshold and amt >= threshold:
        fee = amt * _f(cycle['admin_fee_percentage'])
        cap = _f(cycle['admin_fee_cap'])
        return round(min(fee, cap) if cap else fee, 2)
    return round(_f(cycle['admin_fee_flat']), 2)


# ── Capacity ──────────────────────────────────────────────────────────────────

def total_capacity(cycle):
    duration = int(cycle['duration_months'] or 0)
    earliest = int(cycle['earliest_payout_month'] or 1)
    cap = int(cycle['monthly_capacity'] or 0)
    payout_months = max(0, duration - earliest + 1)
    return payout_months * cap


def participant_count(db, cycle_id):
    placeholders = ','.join('?' for _ in ACTIVE_SUB_STATES)
    return db.execute(
        f"SELECT COUNT(*) FROM ctas_subscriptions WHERE cycle_id = ? AND status IN ({placeholders})",
        (cycle_id, *ACTIVE_SUB_STATES)).fetchone()[0] or 0


def member_has_active_subscription(db, member_id, exclude_cycle_id=None):
    placeholders = ','.join('?' for _ in ACTIVE_SUB_STATES)
    params = [member_id, *ACTIVE_SUB_STATES]
    extra = ''
    if exclude_cycle_id is not None:
        extra = ' AND cycle_id != ?'
        params.append(exclude_cycle_id)
    row = db.execute(
        f"SELECT 1 FROM ctas_subscriptions WHERE member_id = ? AND status IN ({placeholders}){extra} LIMIT 1",
        params).fetchone()
    return row is not None


# ── Affordability (configurable basis) ────────────────────────────────────────

def affordability(db, member, cycle, target_amount, tenure_months):
    """Return (ok, method, message). Method decided by the cycle so a coop that
    isn't salary-based can measure capacity from savings, or defer to committee."""
    method = (cycle['affordability_method'] or 'savings') if 'affordability_method' in cycle.keys() else 'savings'
    deduction = monthly_deduction(target_amount, tenure_months)

    if method == 'manual':
        return True, 'manual', 'Affordability assessed by the committee at approval.'

    if method == 'salary':
        annual = _f(member['annual_salary']) if 'annual_salary' in member.keys() else 0.0
        if annual <= 0:
            return False, 'salary', 'No salary on record — cannot assess salary-based affordability.'
        ratio = _f(cycle['affordability_ratio']) or 0.5
        capacity = round((annual / 12.0) * ratio, 2)
        ok = deduction <= capacity
        return ok, 'salary', (
            f'Monthly deduction ₦{deduction:,.2f} within {ratio:.0%} of monthly salary (₦{capacity:,.2f}).'
            if ok else
            f'Monthly deduction ₦{deduction:,.2f} exceeds {ratio:.0%} of monthly salary (₦{capacity:,.2f}).')

    # default: savings-based exposure cap
    savings = _f(member['total_savings']) if 'total_savings' in member.keys() else 0.0
    multiple = _f(cycle['savings_multiple']) or 3.0
    max_target = round(multiple * savings, 2)
    ok = _f(target_amount) <= max_target
    return ok, 'savings', (
        f'Target ₦{_f(target_amount):,.2f} within {multiple:g}× savings (max ₦{max_target:,.2f}).'
        if ok else
        f'Target ₦{_f(target_amount):,.2f} exceeds {multiple:g}× your savings balance (max ₦{max_target:,.2f}).')


def check_eligibility(db, member, cycle, target_amount, tenure_months, exclude_cycle_id=None):
    """Full eligibility decision. Returns a dict the caller can act on/display.
    `exclude_cycle_id` skips the member's subscription in that cycle when checking
    the one-active-scheme rule (used when re-checking an existing application)."""
    reasons = []
    deduction = monthly_deduction(target_amount, tenure_months)
    fee = calculate_admin_fee(cycle, target_amount)

    if _f(target_amount) <= 0:
        reasons.append('Target amount must be greater than zero.')
    tenure = int(tenure_months or 0)
    if tenure < 2 or tenure > 12:
        reasons.append('Tenure must be between 2 and 12 months.')

    if 'status' in member.keys() and member['status'] != 'active':
        reasons.append('Member is not active.')

    if member_has_active_subscription(db, member['id'], exclude_cycle_id=exclude_cycle_id):
        reasons.append('Member already has an active CTAS subscription.')

    cap = total_capacity(cycle)
    if cap and participant_count(db, cycle['id']) >= cap:
        reasons.append('This cycle is full.')

    aff_ok, method, aff_msg = affordability(db, member, cycle, target_amount, tenure_months)
    if not aff_ok:
        reasons.append(aff_msg)

    return {
        'eligible': not reasons,
        'reasons': reasons,
        'monthly_deduction': deduction,
        'admin_fee': fee,
        'affordability_method': method,
        'affordability_message': aff_msg,
    }


# ── Cycle state machine ───────────────────────────────────────────────────────

def can_transition(from_status, to_status):
    return to_status in CYCLE_TRANSITIONS.get(from_status, set())


def assert_ready_for_ballot(db, cycle):
    """Validate a cycle can move to the ballot. Raises ValueError otherwise."""
    if cycle['status'] != CYCLE_CLOSED:
        raise ValueError('Cycle must be CLOSED before it can go to ballot.')
    enrolled = db.execute(
        "SELECT COUNT(*) FROM ctas_subscriptions WHERE cycle_id = ? AND status = ?",
        (cycle['id'], SUB_ENROLLED)).fetchone()[0] or 0
    if enrolled <= 0:
        raise ValueError('No enrolled members — nothing to ballot.')
    cap = total_capacity(cycle)
    if cap and enrolled > cap:
        raise ValueError('Enrolled members exceed cycle capacity — adjust duration or monthly capacity.')
    return enrolled


# ── Ballot engine ─────────────────────────────────────────────────────────────

def net_off_waterfall(outstanding, savings, shares, other=0.0):
    """Recover an outstanding CTAS balance on member exit in priority order:
    savings balance -> share capital -> other (dividends/terminal benefits, entered
    by the officer) -> write-off. Returns the amount taken from each source."""
    remaining = round(_f(outstanding), 2)
    from_savings = round(min(remaining, _f(savings)), 2)
    remaining = round(remaining - from_savings, 2)
    from_shares = round(min(remaining, _f(shares)), 2)
    remaining = round(remaining - from_shares, 2)
    from_other = round(min(remaining, _f(other)), 2)
    remaining = round(remaining - from_other, 2)
    write_off = round(max(0.0, remaining), 2)
    return {
        'from_savings': from_savings, 'from_shares': from_shares,
        'from_other': from_other, 'write_off': write_off,
        'total': round(from_savings + from_shares + from_other + write_off, 2),
    }


def assign_payout_months(subscription_ids, cycle, seed):
    """Deterministically (by seed) assign each enrolled subscription a unique
    payout month, filling months earliest_payout_month..duration_months with up
    to monthly_capacity slots each. Returns {subscription_id: month}."""
    earliest = int(cycle['earliest_payout_month'] or 1)
    duration = int(cycle['duration_months'] or 0)
    cap = int(cycle['monthly_capacity'] or 1)

    slots = []
    for mth in range(earliest, duration + 1):
        slots.extend([mth] * cap)

    ids = list(subscription_ids)
    if len(ids) > len(slots):
        raise ValueError('Not enough payout slots for the enrolled members.')

    rng = random.Random(str(seed))
    rng.shuffle(ids)
    return {sub_id: slots[i] for i, sub_id in enumerate(ids)}
