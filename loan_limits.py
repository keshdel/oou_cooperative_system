"""Application limits shared by officer, member portal and mobile routes."""
from decimal import Decimal, InvalidOperation

LOAN_TYPES = {
    'Regular': 'regular', 'Housing': 'housing', 'Emergency': 'emergency',
    'Asset Purchase': 'asset', 'School Fees': 'school_fees',
}
DEFAULTS = {'max_loan_amount': '0', **{
    f'max_tenure_{suffix}': '0' for suffix in LOAN_TYPES.values()
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
        if key != 'max_loan_amount':
            minimum = 1 if key == 'max_tenure_months' else 0
            if value != value.to_integral_value() or not minimum <= value <= 60:
                raise ValueError('Loan tenure must be whole months, up to 60.')
        elif value != value.quantize(Decimal('0.01')):
            raise ValueError('Maximum loan amount accepts at most two decimal places.')


def limits(db):
    values = dict(DEFAULTS, max_tenure_months='18')
    for row in db.execute('SELECT key, value FROM settings').fetchall():
        if row['key'] in values:
            values[row['key']] = row['value']
    validate_settings(values)
    general = int(values['max_tenure_months'])
    return {
        'max_amount': float(values['max_loan_amount']),
        'tenures': {name: min(general, int(values[f'max_tenure_{suffix}']) or general)
                    for name, suffix in LOAN_TYPES.items()},
    }


def application_error(db, purpose, amount, tenure):
    policy = limits(db)
    if purpose not in policy['tenures']:
        return 'Select a valid loan type.'
    if not Decimal(str(amount)).is_finite() or amount <= 0 or tenure <= 0:
        return 'Enter a valid positive loan amount and tenure.'
    if policy['max_amount'] and amount > policy['max_amount']:
        return f"Maximum loan amount per application is {policy['max_amount']:,.2f}, regardless of savings."
    maximum = policy['tenures'][purpose]
    if tenure > maximum:
        return f'Maximum tenure for {purpose} is {maximum} months.'
    return None


def eligible_amount(db, savings_balance):
    savings_limit = round(float(savings_balance or 0) * 2, 2)
    cap = limits(db)['max_amount']
    return min(savings_limit, cap) if cap else savings_limit
