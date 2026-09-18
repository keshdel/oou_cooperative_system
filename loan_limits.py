"""Application limits shared by officer, member portal and mobile routes."""
from decimal import Decimal, InvalidOperation

LOAN_TYPES = {
    'Regular': 'regular', 'Housing': 'housing', 'Emergency': 'emergency',
    'Asset Purchase': 'asset', 'School Fees': 'school_fees',
}
DEFAULTS = {'max_loan_amount': '0', **{
    f'max_tenure_{suffix}': '0' for suffix in LOAN_TYPES.values()
}, **{
    f'max_loan_amount_{suffix}': '0' for suffix in LOAN_TYPES.values()
}}


def validate_settings(values):
    for key in set(DEFAULTS) | {'max_tenure_months'}:
        if key not in values:
            continue
        try:
            value = Decimal(str(values[key]))
        except (InvalidOperation, ValueError):
            raise ValueError(f'Invalid loan limit: {key}.')
        if not value.is_finite() or value < 0:
            raise ValueError('Loan limits must be finite, non-negative numbers.')
        if not key.startswith('max_loan_amount'):
            minimum = 1 if key == 'max_tenure_months' else 0
            if value != value.to_integral_value() or not minimum <= value <= 60:
                raise ValueError('Loan tenure must be whole months, up to 60.')
        elif value != value.quantize(Decimal('0.01')):
            raise ValueError('Maximum loan amount accepts at most two decimal places.')


def multiplier(db):
    """How many times their savings a member may borrow.

    Read from the 'loan_multiplier' setting so a cooperative can change it;
    defaults to 2, which is what the code used to hard-code.
    """
    try:
        row = db.execute(
            "SELECT value FROM settings WHERE key = 'loan_multiplier'").fetchone()
        value = float(row['value']) if row and row['value'] else 2.0
    except (TypeError, ValueError, AttributeError):
        return 2.0
    return value if value > 0 else 2.0


def limits(db):
    values = dict(DEFAULTS, max_tenure_months='18')
    for row in db.execute('SELECT key, value FROM settings').fetchall():
        if row['key'] in values:
            values[row['key']] = row['value']
    validate_settings(values)
    general = int(values['max_tenure_months'])
    general_cap = float(values['max_loan_amount'])
    amounts = {}
    for name, suffix in LOAN_TYPES.items():
        type_cap = float(values[f'max_loan_amount_{suffix}'])
        caps = [cap for cap in (general_cap, type_cap) if cap > 0]
        amounts[name] = min(caps) if caps else 0
    return {
        'max_amount': float(values['max_loan_amount']),
        'amounts': amounts,
        'multiplier': multiplier(db),
        'tenures': {name: min(general, int(values[f'max_tenure_{suffix}']) or general)
                    for name, suffix in LOAN_TYPES.items()},
    }


def application_error(db, purpose, amount, tenure):
    policy = limits(db)
    if purpose not in policy['tenures']:
        return 'Select a valid loan type.'
    if not Decimal(str(amount)).is_finite() or amount <= 0 or tenure <= 0:
        return 'Enter a valid positive loan amount and tenure.'
    cap = policy.get('amounts', {}).get(purpose, policy['max_amount'])
    if cap and amount > cap:
        return f"Maximum loan amount for {purpose} is {cap:,.2f}, regardless of savings."
    maximum = policy['tenures'][purpose]
    if tenure > maximum:
        return f'Maximum tenure for {purpose} is {maximum} months.'
    return None


def eligible_amount(db, savings_balance, purpose=None):
    """The most this member may borrow — the single answer used everywhere.

    Two ceilings apply and the lower one wins: what their own savings support,
    and the cooperative's absolute cap, which exists so one large loan cannot
    drain the cash other members are relying on. A cap of 0 means no cap.

    Every screen that tells a member what they can borrow must come through
    here. Showing a figure the application would then reject is worse than
    showing nothing.
    """
    savings_limit = round(float(savings_balance or 0) * multiplier(db), 2)
    policy = limits(db)
    if purpose is None:
        return max(row['eligible_amount'] for row in member_limits(db, savings_balance).values())
    cap = policy['amounts'][purpose]
    return min(savings_limit, cap) if cap else savings_limit


def member_limits(db, savings_balance):
    policy = limits(db)
    savings_limit = max(0, round(float(savings_balance or 0) * policy['multiplier'], 2))
    return {name: {
        'max_amount': cap,
        'eligible_amount': min(savings_limit, cap) if cap else savings_limit,
        'max_tenure_months': policy['tenures'][name],
    } for name, cap in policy['amounts'].items()}


def eligibility_note(db, savings_balance):
    """Plain wording for why the figure is what it is, so an officer asked
    'why can they only get this much?' has the answer on screen."""
    cap = limits(db)['max_amount']
    times = multiplier(db)
    times_text = f'{times:g}× savings'
    if cap and round(float(savings_balance or 0) * times, 2) > cap:
        return f'Capped at ₦{cap:,.2f} by the cooperative, which is below {times_text}.'
    return f'{times_text}.'
