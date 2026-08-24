"""
CTAS core engine — framework-agnostic business rules ported from the CTAS Django
services (ballot, eligibility, cycle state machine). Pure functions over the
CoopMS SQLite/Postgres schema; no Flask/request state.

Money is handled by the GL in the blueprint; this module only decides
eligibility, cycle transitions, and the balloted payout order.
"""

import calendar
import random
from datetime import date, datetime, timedelta

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


# Contribution frequencies — a "period" is not always a calendar month.
FREQUENCIES = {'weekly': 52, 'fortnightly': 26, 'monthly': 12}
PERIOD_WORD = {'weekly': 'week', 'fortnightly': 'fortnight', 'monthly': 'month'}


def _row_get(row, key, default=None):
    """Read a key from a sqlite Row or dict, tolerating missing columns."""
    try:
        return row[key] if key in row.keys() else default
    except Exception:
        try:
            return row[key]
        except Exception:
            return default


def cycle_periods(cycle):
    """Number of contribution periods for a cycle: `periods` if set, else the
    legacy monthly `duration_months`."""
    p = int(_row_get(cycle, 'periods', 0) or 0)
    return p if p > 0 else int(_row_get(cycle, 'duration_months', 0) or 0)


def period_word(cycle_or_freq):
    freq = cycle_or_freq if isinstance(cycle_or_freq, str) else (_row_get(cycle_or_freq, 'frequency', 'monthly') or 'monthly')
    return PERIOD_WORD.get(freq, 'period')


def compute_target(contribution_amount, periods):
    return round(_f(contribution_amount) * int(periods or 0), 2)


# ── Contribution schedule (due dates + status ladder) ────────────────────────

# Schedule row states: not yet due -> due -> within grace -> late; or paid/partial.
SCH_PENDING, SCH_DUE, SCH_GRACE, SCH_LATE = 'pending', 'due', 'grace', 'late'
SCH_PAID, SCH_PARTIAL = 'paid', 'partial'


def to_date(value):
    """Coerce a DB value (often a string here) to a date, or None."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()[:10]
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y'):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def add_periods(start, frequency, n):
    """The date n periods after `start` for this frequency (month-end safe)."""
    if n <= 0:
        return start
    if frequency == 'weekly':
        return start + timedelta(days=7 * n)
    if frequency == 'fortnightly':
        return start + timedelta(days=14 * n)
    month_index = start.month - 1 + n            # monthly
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def period_due_date(start_date, frequency, period_number):
    """Period 1 is due on the cycle start date; each later period one step on."""
    start = to_date(start_date)
    if not start:
        return None
    return add_periods(start, frequency or 'monthly', max(0, int(period_number or 1) - 1))


def schedule_status(due_date, expected, paid, grace_days=7, today=None):
    """Where a scheduled contribution stands right now."""
    expected = _f(expected)
    paid = _f(paid)
    if paid >= expected and expected > 0:
        return SCH_PAID
    due = to_date(due_date)
    today = today or date.today()
    if due is None or today < due:
        return SCH_PARTIAL if paid > 0 else SCH_PENDING
    if today <= due + timedelta(days=int(grace_days or 0)):
        return SCH_PARTIAL if paid > 0 else (SCH_DUE if today == due else SCH_GRACE)
    return SCH_PARTIAL if paid > 0 else SCH_LATE


def card_expired(exp_month, exp_year, today=None):
    """True if a saved card's expiry has passed (cards die mid-cycle often)."""
    try:
        month, year = int(exp_month), int(exp_year)
    except (TypeError, ValueError):
        return False               # unknown expiry — let the gateway decide
    today = today or date.today()
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, last_day) < today


def build_schedule(cycle, periods=None):
    """The (period_number, due_date, expected_amount) rows for one subscription."""
    n = int(periods or cycle_periods(cycle) or 0)
    freq = _row_get(cycle, 'frequency', 'monthly') or 'monthly'
    start = _row_get(cycle, 'start_date') or _row_get(cycle, 'created_at')
    amount = _f(_row_get(cycle, 'contribution_amount', 0))
    rows = []
    for p in range(1, n + 1):
        rows.append((p, period_due_date(start, freq, p), amount))
    return rows


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
    duration = cycle_periods(cycle)
    earliest = int(_row_get(cycle, 'earliest_payout_month', 1) or 1)
    cap = int(_row_get(cycle, 'monthly_capacity', 0) or 0)
    payout_periods = max(0, duration - earliest + 1)
    return payout_periods * cap


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


def liquidity_projection(cycle, member_count, payouts_by_period=None,
                         reserve=None, support=None, buffer_amount=None):
    """Period-by-period cash projection for a cycle (spec section 14).

        contributions in  +  reserve  +  approved support  -  payouts out
        =  projected liquidity position

    Every subscribed member contributes every period (including after their own
    payout), so inflow is steady while payouts land on specific periods. Returns
    (rows, summary) where each row carries a green/amber/red status and the
    summary reports the largest amount the cooperative must bridge — the
    "cooperative guarantee" of section 13.
    """
    contribution = _f(_row_get(cycle, 'contribution_amount', 0))
    periods = cycle_periods(cycle)
    target = compute_target(contribution, periods)
    capacity = int(_row_get(cycle, 'monthly_capacity', 1) or 1)
    earliest = int(_row_get(cycle, 'earliest_payout_month', 1) or 1)

    reserve = _f(_row_get(cycle, 'liquidity_reserve', 0)) if reserve is None else _f(reserve)
    support = _f(_row_get(cycle, 'liquidity_support', 0)) if support is None else _f(support)
    if buffer_amount is None:
        buffer_amount = _f(_row_get(cycle, 'liquidity_buffer', 0)) or target   # default: one payout
    else:
        buffer_amount = _f(buffer_amount)

    # Before the ballot we do not know who collects when, so assume the cycle
    # runs at full capacity from the earliest payout position — the worst case.
    if payouts_by_period is None:
        payouts_by_period = {p: capacity for p in range(earliest, periods + 1)}

    members = int(member_count or 0)
    inflow = round(members * contribution, 2)
    balance = round(reserve + support, 2)
    rows = []
    worst = balance
    total_out = 0.0
    for p in range(1, periods + 1):
        payees = int(payouts_by_period.get(p, 0) or 0)
        outflow = round(payees * target, 2)
        total_out = round(total_out + outflow, 2)
        balance = round(balance + inflow - outflow, 2)
        worst = min(worst, balance)
        if balance < 0:
            status = 'red'
        elif balance < buffer_amount:
            status = 'amber'
        else:
            status = 'green'
        rows.append({'period': p, 'payees': payees, 'inflow': inflow,
                     'outflow': outflow, 'balance': balance, 'status': status})

    shortfall = round(max(0.0, -worst), 2)          # extra cash needed beyond reserve+support
    # The gap the cooperative underwrites when the cycle is not fully subscribed
    # (section 13): payouts due in a period versus contributions collected.
    per_period_gap = round(max(0.0, (capacity * target) - inflow), 2)
    return rows, {
        'members': members, 'contribution': contribution, 'target': target,
        'inflow_per_period': inflow, 'reserve': reserve, 'support': support,
        'buffer': buffer_amount, 'total_payouts': total_out,
        'lowest_balance': round(worst, 2), 'shortfall': shortfall,
        'funding_gap_per_payout_period': per_period_gap,
        'status': 'red' if shortfall > 0 else ('amber' if worst < buffer_amount else 'green'),
    }


def priority_fee_for(tiers, position):
    """The fee for an early position, from {position: fee}. 0 if not priced."""
    try:
        return round(float(tiers.get(int(position), 0) or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def assign_payout_months(subscription_ids, cycle, seed, priority=None):
    """Deterministically (by seed) assign each enrolled subscription a unique
    payout position, filling earliest_payout_month..periods with up to
    monthly_capacity slots each. Returns {subscription_id: position}.

    `priority` is {subscription_id: requested_position} for requests the
    cooperative has GRANTED. Those are placed first; if more members want the
    same position than there are slots, a seeded ballot decides between them and
    the unsuccessful ones fall back into the normal ballot (spec section 12).
    """
    earliest = int(_row_get(cycle, 'earliest_payout_month', 1) or 1)
    duration = cycle_periods(cycle)
    cap = int(_row_get(cycle, 'monthly_capacity', 1) or 1)

    slots = []
    for mth in range(earliest, duration + 1):
        slots.extend([mth] * cap)

    ids = list(subscription_ids)
    if len(ids) > len(slots):
        raise ValueError('Not enough payout slots for the enrolled members.')

    rng = random.Random(str(seed))
    assignments = {}

    # 1) Granted priority requests, position by position.
    wanted = {}
    for sub_id, pos in (priority or {}).items():
        if sub_id in ids and pos in slots:
            wanted.setdefault(int(pos), []).append(sub_id)
    for pos in sorted(wanted):
        contenders = sorted(wanted[pos])          # sort first so the seed decides, not dict order
        rng.shuffle(contenders)
        available = slots.count(pos)
        for sub_id in contenders[:available]:
            assignments[sub_id] = pos
            slots.remove(pos)

    # 2) Everyone else (including unsuccessful priority applicants) is balloted.
    remaining = sorted(s for s in ids if s not in assignments)
    rng.shuffle(remaining)
    for i, sub_id in enumerate(remaining):
        assignments[sub_id] = slots[i]
    return assignments
