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
    LOANS_RECEIVABLE, INVESTMENTS, MEMBER_DEPOSITS,
    ACCUM_SURPLUS, STATUTORY_RESERVE, SHARE_CAPITAL,
    LOAN_INTEREST_INCOME, FEE_INCOME, INVESTMENT_INCOME,
    OPERATING_EXPENSES, HONORARIUM,
    get_default_cash_account,
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


def _type_accounts(db, atype):
    return db.execute(
        "SELECT code, name FROM accounts WHERE type = ? AND is_active = 1 ORDER BY code",
        (atype,)
    ).fetchall()


def income_statement(db, from_date, to_date):
    """Income statement for [from_date, to_date], aggregated by account type so
    every income/expense account (including ones added later) is included."""
    lo, hi = _period_bounds(from_date, to_date)

    income_lines, total_income = [], 0.0
    for a in _type_accounts(db, 'income'):           # income = credits - debits
        amt = round(-_movement(db, a['code'], lo, hi), 2)
        income_lines.append({'code': a['code'], 'name': a['name'], 'amount': amt})
        total_income += amt

    expense_lines, total_expenses = [], 0.0
    for a in _type_accounts(db, 'expense'):          # expense = debits - credits
        amt = round(_movement(db, a['code'], lo, hi), 2)
        expense_lines.append({'code': a['code'], 'name': a['name'], 'amount': amt})
        total_expenses += amt

    net_surplus = round(total_income - total_expenses, 2)
    if abs(total_income) < 0.005 and abs(total_expenses) < 0.005:
        legacy = _legacy_income_statement(db, from_date, to_date)
        if legacy:
            return legacy
    return {
        'from_date': from_date, 'to_date': to_date,
        'income_lines': income_lines,
        'total_income': round(total_income, 2),
        'expense_lines': expense_lines,
        'total_expenses': round(total_expenses, 2),
        'net_surplus': net_surplus,
    }


def _legacy_income_statement(db, from_date, to_date):
    """Temporary bridge for pre-ledger transactions.

    Phase 3 reports use journal lines. Existing deployments may already contain
    savings fees, loan repayments, revenue, and expenses that were created before
    ledger posting was introduced. Until a backfill is run, show those operational
    totals only when the ledger has no income/expense activity for the period.
    """
    lo, hi = _period_bounds(from_date, to_date)

    def val(sql, params=()):
        row = db.execute(sql, params).fetchone()
        return float(row[0] or 0) if row else 0.0

    loan_interest = val(
        'SELECT COALESCE(SUM(interest_paid), 0) FROM repayments WHERE date BETWEEN ? AND ?',
        (lo, hi),
    )
    fee_income = val(
        'SELECT COALESCE(SUM(late_fee), 0) FROM savings WHERE date BETWEEN ? AND ?',
        (lo, hi),
    ) + val(
        'SELECT COALESCE(SUM(amount), 0) FROM revenue WHERE date BETWEEN ? AND ?',
        (lo, hi),
    )
    investment_income = val(
        'SELECT COALESCE(SUM(actual_return), 0) FROM investments WHERE date BETWEEN ? AND ?',
        (lo, hi),
    )
    operating_expenses = val(
        'SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE date BETWEEN ? AND ?',
        (lo, hi),
    )
    honorarium = val(
        'SELECT COALESCE(SUM(amount), 0) FROM honorarium WHERE date BETWEEN ? AND ?',
        (lo, hi),
    )

    total_income = round(loan_interest + fee_income + investment_income, 2)
    total_expenses = round(operating_expenses + honorarium, 2)
    if abs(total_income) < 0.005 and abs(total_expenses) < 0.005:
        return None

    return {
        'from_date': from_date,
        'to_date': to_date,
        'income_lines': [
            {'code': LOAN_INTEREST_INCOME, 'name': 'Loan Interest Income (legacy unposted)', 'amount': round(loan_interest, 2)},
            {'code': FEE_INCOME, 'name': 'Fee / Other Income (legacy unposted)', 'amount': round(fee_income, 2)},
            {'code': INVESTMENT_INCOME, 'name': 'Investment Income (legacy unposted)', 'amount': round(investment_income, 2)},
        ],
        'total_income': total_income,
        'expense_lines': [
            {'code': OPERATING_EXPENSES, 'name': 'Operating Expenses (legacy unposted)', 'amount': round(operating_expenses, 2)},
            {'code': HONORARIUM, 'name': 'Honorarium (legacy unposted)', 'amount': round(honorarium, 2)},
        ],
        'total_expenses': total_expenses,
        'net_surplus': round(total_income - total_expenses, 2),
    }


def balance_sheet(db, as_of=None):
    """Balance sheet as of a date, aggregated by account type. Balances by
    construction (assets == liabilities + equity) for any set of accounts."""
    _, hi = _period_bounds(as_of, as_of) if as_of else (None, None)

    asset_lines, total_assets = [], 0.0
    for a in _type_accounts(db, 'asset'):            # asset = debits - credits
        bal = round(_balance(db, a['code'], hi), 2)
        asset_lines.append({'code': a['code'], 'name': a['name'], 'amount': bal})
        total_assets += bal

    liability_lines, total_liabilities = [], 0.0
    for a in _type_accounts(db, 'liability'):        # liability = credits - debits
        bal = round(-_balance(db, a['code'], hi), 2)
        liability_lines.append({'code': a['code'], 'name': a['name'], 'amount': bal})
        total_liabilities += bal

    equity_lines, equity_accounts = [], 0.0
    for a in _type_accounts(db, 'equity'):
        bal = round(-_balance(db, a['code'], hi), 2)
        equity_lines.append({'code': a['code'], 'name': a['name'], 'amount': bal})
        equity_accounts += bal

    # Income/expense accounts aren't closed to equity until period end, so the
    # current retained surplus is shown as its own equity line.
    income_td  = -sum(_balance(db, a['code'], hi) for a in _type_accounts(db, 'income'))
    expense_td =  sum(_balance(db, a['code'], hi) for a in _type_accounts(db, 'expense'))
    retained = round(income_td - expense_td, 2)
    equity_lines.append({'code': '', 'name': 'Retained surplus (current)', 'amount': retained})

    total_equity = round(equity_accounts + retained, 2)
    return {
        'as_of': as_of,
        'asset_lines': asset_lines,
        'total_assets': round(total_assets, 2),
        'liability_lines': liability_lines,
        'total_liabilities': round(total_liabilities, 2),
        'equity_lines': equity_lines,
        'total_equity': total_equity,
        'accumulated_surplus': retained,
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
    cash_account = get_default_cash_account(db)

    opening = 0.0
    row = db.execute('''
        SELECT COALESCE(SUM(jl.debit), 0) - COALESCE(SUM(jl.credit), 0)
        FROM journal_lines jl JOIN journal_entries je ON je.id = jl.entry_id
        WHERE jl.account_code = ? AND je.date < ?
    ''', (cash_account, from_date)).fetchone()
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
    ''', (cash_account, cash_account, lo, hi)).fetchall()

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
