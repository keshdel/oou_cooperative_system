"""
ledger.py — double-entry general ledger: posting engine + reporting.

A journal entry is a set of lines whose debits equal its credits. Every
financial event (savings deposit, loan disbursement, repayment, fee, expense…)
posts one balanced entry, and all statements roll up from these entries.

Account codes are the stable interface (see the seeded chart of accounts in
database.py). Posting helpers never commit — they run inside the caller's
transaction so the journal entry is atomic with the operation it records.
"""

import secrets
from datetime import datetime

# Canonical account codes (mirror the seeded chart of accounts)
CASH            = '1000'
LOANS_RECEIVABLE = '1100'
INVESTMENTS     = '1200'
MEMBER_DEPOSITS = '2000'
ACCUM_SURPLUS   = '3000'
STATUTORY_RESERVE = '3100'
SHARE_CAPITAL   = '3200'
LOAN_INTEREST_INCOME = '4000'
FEE_INCOME      = '4100'
INVESTMENT_INCOME = '4200'
OPERATING_EXPENSES = '5000'
HONORARIUM      = '5100'


def get_accounts(db, active_only=True):
    sql = 'SELECT code, name, type, normal_balance FROM accounts'
    if active_only:
        sql += ' WHERE is_active = 1'
    sql += " ORDER BY code"
    return db.execute(sql).fetchall()


def account_exists(db, code):
    return db.execute('SELECT 1 FROM accounts WHERE code = ?', (code,)).fetchone() is not None


def post_journal(db, description, lines, date=None, reference='',
                 source_module='', source_id=None, created_by=None):
    """Post a balanced double-entry journal entry.

    lines: iterable of dicts, e.g.
        [{'account': '1000', 'debit': 5000, 'memo': 'cash in'},
         {'account': '2000', 'credit': 5000}]

    Validates that debits == credits, that no line is both debit and credit,
    and that amounts are non-negative. Does NOT commit. Returns the new
    journal_entries.id, or None if there was nothing to post.
    Raises ValueError on an unbalanced or invalid entry.
    """
    norm = []
    total_debit = total_credit = 0.0
    for ln in lines:
        code   = ln['account']
        debit  = round(float(ln.get('debit', 0) or 0), 2)
        credit = round(float(ln.get('credit', 0) or 0), 2)
        if debit < 0 or credit < 0:
            raise ValueError('journal line debit/credit cannot be negative')
        if debit and credit:
            raise ValueError('a journal line cannot have both a debit and a credit')
        if not debit and not credit:
            continue  # skip zero lines
        norm.append((code, debit, credit, ln.get('memo', '')))
        total_debit  += debit
        total_credit += credit

    if not norm:
        return None
    if round(total_debit, 2) != round(total_credit, 2):
        raise ValueError(
            f'unbalanced journal entry: debits {total_debit:.2f} != credits {total_credit:.2f}'
        )

    entry_number = f"JE-{datetime.now().strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"
    db.execute(
        '''INSERT INTO journal_entries
           (entry_number, date, description, reference, source_module, source_id, created_by, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
        (entry_number, date or datetime.now(), description, reference,
         source_module, source_id, created_by, datetime.now())
    )
    from database import last_insert_id
    entry_id = last_insert_id(db)
    for code, debit, credit, memo in norm:
        db.execute(
            'INSERT INTO journal_lines (entry_id, account_code, debit, credit, memo) VALUES (?, ?, ?, ?, ?)',
            (entry_id, code, debit, credit, memo)
        )
    return entry_id


def trial_balance(db, as_of=None):
    """Return the trial balance as of a date (or all-time).

    Each account's net balance (debits - credits) is placed on the side where
    it is positive, so total debits always equal total credits.
    """
    date_filter = ''
    params = ()
    if as_of:
        date_filter = 'WHERE je.date <= ?'
        params = (f"{as_of} 23:59:59",)

    rows = db.execute(f'''
        SELECT a.code, a.name, a.type,
               COALESCE(SUM(x.debit), 0)  AS d,
               COALESCE(SUM(x.credit), 0) AS c
        FROM accounts a
        LEFT JOIN (
            SELECT jl.account_code, jl.debit, jl.credit
            FROM journal_lines jl
            JOIN journal_entries je ON je.id = jl.entry_id
            {date_filter}
        ) x ON x.account_code = a.code
        WHERE a.is_active = 1
        GROUP BY a.code, a.name, a.type
        ORDER BY a.code
    ''', params).fetchall()

    result = []
    total_debit = total_credit = 0.0
    for r in rows:
        net    = float(r['d']) - float(r['c'])
        debit  = net if net > 0 else 0.0
        credit = -net if net < 0 else 0.0
        total_debit  += debit
        total_credit += credit
        result.append({
            'code': r['code'], 'name': r['name'], 'type': r['type'],
            'debit': round(debit, 2), 'credit': round(credit, 2),
        })
    return {
        'rows': result,
        'total_debit': round(total_debit, 2),
        'total_credit': round(total_credit, 2),
        'balanced': abs(total_debit - total_credit) < 0.01,
    }


def account_balance(db, code, as_of=None):
    """Signed balance (debits - credits) for one account."""
    date_filter = ''
    params = [code]
    if as_of:
        date_filter = 'AND je.date <= ?'
        params.append(f"{as_of} 23:59:59")
    row = db.execute(f'''
        SELECT COALESCE(SUM(jl.debit), 0) - COALESCE(SUM(jl.credit), 0) AS bal
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.entry_id
        WHERE jl.account_code = ? {date_filter}
    ''', tuple(params)).fetchone()
    return float(row['bal'] or 0) if row else 0.0
