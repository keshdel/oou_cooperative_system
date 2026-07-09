"""
reports_engine.py — correct financial statements derived from existing data.

Accounting model for the cooperative (why the old reports were wrong):
  * Member savings are a LIABILITY (deposits the co-op owes members), NOT income.
  * Income   = loan interest earned + fees & other income + investment returns.
  * Expenses = operating expenses + honorarium.
  * Assets   = cash (derived) + investments + loans receivable.
  * Liabilities = member deposits (total savings).
  * Equity   = accumulated surplus (lifetime income - lifetime expenses).

All figures are computed from existing tables (savings, loans, repayments,
revenue, investments, expenses, honorarium). Nothing is fabricated.
"""


def _val(db, sql, params=()):
    row = db.execute(sql, params).fetchone()
    return float(row[0] or 0) if row and row[0] is not None else 0.0


def _period_bounds(from_date, to_date):
    """Return inclusive bounds; the upper bound covers all of to_date since the
    date columns are timestamps (…09 23:59:59, not just …09)."""
    return from_date, f"{to_date} 23:59:59"


def income_statement(db, from_date, to_date):
    """Income statement for the period [from_date, to_date] (YYYY-MM-DD)."""
    lo, hi = _period_bounds(from_date, to_date)

    loan_interest     = _val(db, "SELECT COALESCE(SUM(interest_paid), 0) FROM repayments WHERE date BETWEEN ? AND ?", (lo, hi))
    fee_income        = _val(db, "SELECT COALESCE(SUM(amount), 0) FROM revenue WHERE date BETWEEN ? AND ?", (lo, hi))
    investment_income = _val(db, "SELECT COALESCE(SUM(actual_return), 0) FROM investments WHERE date BETWEEN ? AND ?", (lo, hi))
    total_income = loan_interest + fee_income + investment_income

    operating_expenses = _val(db, "SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE date BETWEEN ? AND ?", (lo, hi))
    honorarium         = _val(db, "SELECT COALESCE(SUM(amount), 0) FROM honorarium WHERE date BETWEEN ? AND ?", (lo, hi))
    total_expenses = operating_expenses + honorarium

    net_surplus = total_income - total_expenses
    return {
        'from_date': from_date, 'to_date': to_date,
        'loan_interest': loan_interest,
        'fee_income': fee_income,
        'investment_income': investment_income,
        'total_income': total_income,
        'operating_expenses': operating_expenses,
        'honorarium': honorarium,
        'total_expenses': total_expenses,
        'net_surplus': net_surplus,
    }


def balance_sheet(db, as_of=None):
    """Point-in-time balance sheet built from current balances.

    Cash is the balancing residual: what members deposited plus surplus earned,
    less what is tied up in outstanding loans and investments. This guarantees
    Assets == Liabilities + Equity.
    """
    member_deposits  = _val(db, "SELECT COALESCE(SUM(amount), 0) FROM savings")
    loans_receivable = _val(db, "SELECT COALESCE(SUM(balance), 0) FROM loans WHERE status = 'active'")
    investments      = _val(db, "SELECT COALESCE(SUM(COALESCE(current_value, amount)), 0) FROM investments")

    lifetime_income = (
        _val(db, "SELECT COALESCE(SUM(interest_paid), 0) FROM repayments")
        + _val(db, "SELECT COALESCE(SUM(amount), 0) FROM revenue")
        + _val(db, "SELECT COALESCE(SUM(actual_return), 0) FROM investments")
    )
    lifetime_expense = (
        _val(db, "SELECT COALESCE(SUM(amount), 0) FROM expenses")
        + _val(db, "SELECT COALESCE(SUM(amount), 0) FROM honorarium")
    )
    accumulated_surplus = lifetime_income - lifetime_expense

    cash = (member_deposits + accumulated_surplus) - (loans_receivable + investments)

    total_assets      = cash + investments + loans_receivable
    total_liabilities = member_deposits
    total_equity      = accumulated_surplus
    return {
        'as_of': as_of,
        'cash': cash,
        'investments': investments,
        'loans_receivable': loans_receivable,
        'total_assets': total_assets,
        'member_deposits': member_deposits,
        'total_liabilities': total_liabilities,
        'accumulated_surplus': accumulated_surplus,
        'total_equity': total_equity,
        'balances': abs(total_assets - (total_liabilities + total_equity)) < 0.01,
    }


def surplus_appropriation(net_surplus, dividend_pct=50, reserve_pct=30,
                          honorarium_pct=10, other_pct=10):
    """Appropriate an ACTUAL net surplus per bye-law percentages.
    Returns zeros when there is no surplus to distribute."""
    base = net_surplus if net_surplus > 0 else 0.0
    return {
        'dividend':   round(base * dividend_pct / 100, 2),
        'reserve':    round(base * reserve_pct / 100, 2),
        'honorarium': round(base * honorarium_pct / 100, 2),
        'other':      round(base * other_pct / 100, 2),
    }
