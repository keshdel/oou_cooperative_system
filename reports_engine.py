"""
reports_engine.py — financial statements derived from the general ledger.

The double-entry ledger (see ledger.py) is the single source of truth. Every
statement here rolls up journal-line balances, so the balance sheet balances by
construction (assets = liabilities + equity) and the income statement reflects
exactly what was posted.

Accounting model:
  * Member savings are a LIABILITY (member deposits), never income.
  * Income   = loan interest + fee income + investment income (credit accounts).
  * Expenses = operating expenses + honorarium (debit accounts).
  * Equity   = share capital + reserves + accumulated surplus (income - expenses).
"""

from ledger import (
    CASH, LOANS_RECEIVABLE, INVESTMENTS, MEMBER_DEPOSITS,
    ACCUM_SURPLUS, STATUTORY_RESERVE, SHARE_CAPITAL,
    LOAN_INTEREST_INCOME, FEE_INCOME, INVESTMENT_INCOME,
    OPERATING_EXPENSES, HONORARIUM,
)


def _period_bounds(from_date, to_date):
    return from_date, f"{to_date} 23:59:59"


def _movement(db, code, lo, hi):
    """Net debit-minus-credit movement on an account within [lo, hi]."""
    row = db.execute('''
        SELECT COALESCE(SUM(jl.debit), 0) - COALESCE(SUM(jl.credit), 0)
        FROM journal_lines jl JOIN journal_entries je ON je.id = jl.entry_id
        WHERE jl.account_code = ? AND je.date BETWEEN ? AND ?
    ''', (code, lo, hi)).fetchone()
    return float(row[0] or 0)


def _balance(db, code, hi=None):
    """Net debit-minus-credit balance on an account up to `hi` (or all-time)."""
    if hi:
        row = db.execute('''
            SELECT COALESCE(SUM(jl.debit), 0) - COALESCE(SUM(jl.credit), 0)
            FROM journal_lines jl JOIN journal_entries je ON je.id = jl.entry_id
            WHERE jl.account_code = ? AND je.date <= ?
        ''', (code, hi)).fetchone()
    else:
        row = db.execute('''
            SELECT COALESCE(SUM(jl.debit), 0) - COALESCE(SUM(jl.credit), 0)
            FROM journal_lines jl WHERE jl.account_code = ?
        ''', (code,)).fetchone()
    return float(row[0] or 0)


def income_statement(db, from_date, to_date):
    """Income statement for [from_date, to_date], from the ledger."""
    lo, hi = _period_bounds(from_date, to_date)
    # Income accounts have a credit normal balance: income = credits - debits = -movement
    loan_interest     = -_movement(db, LOAN_INTEREST_INCOME, lo, hi)
    fee_income        = -_movement(db, FEE_INCOME, lo, hi)
    investment_income = -_movement(db, INVESTMENT_INCOME, lo, hi)
    total_income = loan_interest + fee_income + investment_income

    operating_expenses = _movement(db, OPERATING_EXPENSES, lo, hi)
    honorarium         = _movement(db, HONORARIUM, lo, hi)
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
    """Balance sheet as of a date, from the ledger. Balances by construction."""
    _, hi = _period_bounds(as_of, as_of) if as_of else (None, None)

    cash             = _balance(db, CASH, hi)
    loans_receivable = _balance(db, LOANS_RECEIVABLE, hi)
    investments      = _balance(db, INVESTMENTS, hi)
    total_assets = cash + loans_receivable + investments

    member_deposits = -_balance(db, MEMBER_DEPOSITS, hi)   # liability (credit)
    total_liabilities = member_deposits

    share_capital     = -_balance(db, SHARE_CAPITAL, hi)
    statutory_reserve = -_balance(db, STATUTORY_RESERVE, hi)
    posted_surplus    = -_balance(db, ACCUM_SURPLUS, hi)
    # Income/expense accounts are not closed to equity until period end, so
    # current retained surplus = posted surplus + (income - expenses) to date.
    income_to_date  = -(_balance(db, LOAN_INTEREST_INCOME, hi)
                        + _balance(db, FEE_INCOME, hi)
                        + _balance(db, INVESTMENT_INCOME, hi))
    expense_to_date = _balance(db, OPERATING_EXPENSES, hi) + _balance(db, HONORARIUM, hi)
    accumulated_surplus = posted_surplus + income_to_date - expense_to_date

    total_equity = share_capital + statutory_reserve + accumulated_surplus
    return {
        'as_of': as_of,
        'cash': cash,
        'investments': investments,
        'loans_receivable': loans_receivable,
        'total_assets': total_assets,
        'member_deposits': member_deposits,
        'total_liabilities': total_liabilities,
        'share_capital': share_capital,
        'statutory_reserve': statutory_reserve,
        'accumulated_surplus': accumulated_surplus,
        'total_equity': total_equity,
        'balances': abs(total_assets - (total_liabilities + total_equity)) < 0.01,
    }


# Cash-flow categorisation by the counterpart (contra) account
_FINANCING = {MEMBER_DEPOSITS, SHARE_CAPITAL, STATUTORY_RESERVE, ACCUM_SURPLUS}
_INVESTING = {INVESTMENTS, INVESTMENT_INCOME}


def _cash_category(code):
    if code in _FINANCING:
        return 'financing'
    if code in _INVESTING:
        return 'investing'
    return 'operating'


def cash_flow(db, from_date, to_date):
    """Cash-flow statement for the period, derived from movements on the Cash
    account. Each cash movement is attributed to its counterpart account and
    grouped into operating / investing / financing activities."""
    lo, hi = _period_bounds(from_date, to_date)

    opening = 0.0
    row = db.execute('''
        SELECT COALESCE(SUM(jl.debit), 0) - COALESCE(SUM(jl.credit), 0)
        FROM journal_lines jl JOIN journal_entries je ON je.id = jl.entry_id
        WHERE jl.account_code = ? AND je.date < ?
    ''', (CASH, from_date)).fetchone()
    opening = float(row[0] or 0) if row else 0.0

    # Counterpart movements for every entry that touches cash in the period.
    rows = db.execute('''
        SELECT jl.account_code AS code, a.name AS name,
               COALESCE(SUM(jl.credit), 0) - COALESCE(SUM(jl.debit), 0) AS cash_effect
        FROM journal_lines jl
        JOIN accounts a ON a.code = jl.account_code
        WHERE jl.account_code != ?
          AND jl.entry_id IN (
              SELECT jl2.entry_id FROM journal_lines jl2
              JOIN journal_entries je ON je.id = jl2.entry_id
              WHERE jl2.account_code = ? AND je.date BETWEEN ? AND ?
          )
        GROUP BY jl.account_code, a.name
        ORDER BY jl.account_code
    ''', (CASH, CASH, lo, hi)).fetchall()

    groups = {'operating': [], 'investing': [], 'financing': []}
    subtotals = {'operating': 0.0, 'investing': 0.0, 'financing': 0.0}
    net = 0.0
    for r in rows:
        effect = float(r['cash_effect'] or 0)
        if abs(effect) < 0.005:
            continue
        cat = _cash_category(r['code'])
        groups[cat].append({'account': r['code'], 'name': r['name'], 'amount': round(effect, 2)})
        subtotals[cat] += effect
        net += effect

    return {
        'from_date': from_date, 'to_date': to_date,
        'opening': round(opening, 2),
        'groups': groups,
        'subtotals': {k: round(v, 2) for k, v in subtotals.items()},
        'net_change': round(net, 2),
        'closing': round(opening + net, 2),
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
