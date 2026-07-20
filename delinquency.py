"""
delinquency.py — loan arrears / ageing.

A loan is "delinquent" when the amount the member should have repaid by today
(based on equal monthly instalments) exceeds what they have actually repaid.
We report the shortfall (arrears), how many instalments they are behind, how
many days overdue the oldest missed instalment is, and an ageing bucket.

Pure date/number logic here; the blueprint feeds it loan rows from the DB.
"""
from datetime import datetime
import calendar
import math


def _parse(dt):
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt
    try:
        return datetime.fromisoformat(str(dt).replace('Z', '').split('+')[0].split('.')[0])
    except Exception:
        return None


def _add_months(dt, n):
    """Return dt shifted forward by n whole months, clamping the day."""
    m = dt.month - 1 + n
    y = dt.year + m // 12
    m = m % 12 + 1
    last_day = calendar.monthrange(y, m)[1]
    return dt.replace(year=y, month=m, day=min(dt.day, last_day))


def _bucket(days):
    if days <= 0:
        return 'Current'
    if days <= 30:
        return '1-30'
    if days <= 60:
        return '31-60'
    if days <= 90:
        return '61-90'
    return '90+'


BUCKET_ORDER = ['1-30', '31-60', '61-90', '90+']


def loan_delinquency(loan, as_of=None):
    """Compute arrears/ageing for one active loan row (dict-like).

    Returns a dict with monthly_payment, expected_paid, actual_paid, arrears,
    instalments_behind, days_overdue, bucket, next_due_date. Returns arrears=0
    (bucket 'Current') for anything that isn't a live, dated, positive-tenure
    loan.
    """
    blank = {
        'monthly_payment': 0.0, 'expected_paid': 0.0, 'actual_paid': 0.0,
        'arrears': 0.0, 'instalments_behind': 0, 'days_overdue': 0,
        'bucket': 'Current', 'next_due_date': None,
    }
    as_of = as_of or datetime.now()
    try:
        tenure = int(loan['tenure'] or 0)
    except Exception:
        tenure = 0
    total_repayment = float(loan['total_repayment'] or 0)
    balance = float(loan['balance'] or 0)
    if tenure <= 0 or total_repayment <= 0:
        return blank

    monthly_payment = round(total_repayment / tenure, 2)
    actual_paid = max(0.0, round(total_repayment - balance, 2))

    # Repayment schedule starts at first_payment_date, else one month after
    # disbursement, else one month after application.
    start = _parse(loan['first_payment_date'] if 'first_payment_date' in loan.keys() else None)
    if start is None:
        base = _parse(loan['disbursement_date'] if 'disbursement_date' in loan.keys() else None) \
            or _parse(loan['date_applied'] if 'date_applied' in loan.keys() else None)
        if base is None:
            return blank
        start = _add_months(base, 1)

    if as_of < start:
        return {**blank, 'monthly_payment': monthly_payment,
                'actual_paid': actual_paid, 'next_due_date': start}

    # How many instalments are due on or before today (capped at the tenure)?
    instalments_due = 0
    for k in range(1, tenure + 1):
        if _add_months(start, k - 1) <= as_of:
            instalments_due = k
        else:
            break

    expected_paid = min(total_repayment, round(instalments_due * monthly_payment, 2))
    arrears = round(max(0.0, expected_paid - actual_paid), 2)

    if arrears <= 0.005:
        next_idx = min(instalments_due, tenure - 1)  # 0-based index of next upcoming
        next_due = _add_months(start, instalments_due) if instalments_due < tenure else None
        return {**blank, 'monthly_payment': monthly_payment, 'expected_paid': expected_paid,
                'actual_paid': actual_paid, 'next_due_date': next_due}

    instalments_behind = int(math.ceil(arrears / monthly_payment)) if monthly_payment else 0

    # Oldest unpaid instalment = the first instalment not fully covered by what
    # has actually been paid. Its due date drives days-overdue.
    covered = int(actual_paid // monthly_payment) if monthly_payment else 0
    first_unpaid_idx = covered  # 0-based
    first_unpaid_due = _add_months(start, first_unpaid_idx)
    days_overdue = max(0, (as_of - first_unpaid_due).days)

    next_due = _add_months(start, instalments_due) if instalments_due < tenure else None

    return {
        'monthly_payment': monthly_payment,
        'expected_paid': expected_paid,
        'actual_paid': actual_paid,
        'arrears': arrears,
        'instalments_behind': instalments_behind,
        'days_overdue': days_overdue,
        'bucket': _bucket(days_overdue),
        'next_due_date': next_due,
    }


def portfolio_delinquency(db, as_of=None):
    """Assess every active loan; return delinquent ones + an ageing summary."""
    as_of = as_of or datetime.now()
    rows = db.execute('''
        SELECT l.*, m.first_name || ' ' || m.last_name AS member_name,
               m.member_number
        FROM loans l JOIN members m ON m.id = l.member_id
        WHERE l.status = 'active'
        ORDER BY l.date_applied DESC
    ''').fetchall()

    delinquent = []
    buckets = {b: {'count': 0, 'amount': 0.0} for b in BUCKET_ORDER}
    total_arrears = 0.0

    for r in rows:
        d = loan_delinquency(r, as_of)
        if d['arrears'] <= 0.005:
            continue
        loan = dict(r)
        loan.update(d)
        loan['due_date_str'] = d['next_due_date'].strftime('%Y-%m-%d') if d['next_due_date'] else '—'
        delinquent.append(loan)
        total_arrears += d['arrears']
        b = d['bucket']
        if b in buckets:
            buckets[b]['count'] += 1
            buckets[b]['amount'] += d['arrears']

    delinquent.sort(key=lambda x: x['days_overdue'], reverse=True)
    return {
        'loans': delinquent,
        'count': len(delinquent),
        'total_arrears': round(total_arrears, 2),
        'buckets': buckets,
        'bucket_order': BUCKET_ORDER,
    }
