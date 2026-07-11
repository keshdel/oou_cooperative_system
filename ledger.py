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

OPERATIONAL_REVENUE_CATEGORIES = {
    'Late Fee',
    'Loan Insurance',
    'Loan Application Fee',
}


def get_accounts(db, active_only=True):
    sql = 'SELECT code, name, type, normal_balance, parent_code, is_active FROM accounts'
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


def post_journal_safe(db, *args, **kwargs):
    """Best-effort post_journal that never raises and cannot poison the caller's
    transaction. Use from within member-facing flows so a ledger problem can't
    break recording a payment. Returns the entry id, or None on failure.

    On PostgreSQL the work is wrapped in a SAVEPOINT so a failed insert does not
    abort the outer transaction (mirrors utils.record_revenue).
    """
    from database import USE_POSTGRES
    if USE_POSTGRES:
        try:
            db.execute('SAVEPOINT sp_journal')
            entry_id = post_journal(db, *args, **kwargs)
            db.execute('RELEASE SAVEPOINT sp_journal')
            return entry_id
        except Exception as exc:
            try:
                db.execute('ROLLBACK TO SAVEPOINT sp_journal')
            except Exception:
                pass
            print(f"[ledger] failed to post journal: {exc}")
            return None
    else:
        try:
            return post_journal(db, *args, **kwargs)
        except Exception as exc:
            print(f"[ledger] failed to post journal: {exc}")
            return None


def _je_exists_ref(db, reference):
    """True if a journal entry already exists for this (non-empty) reference."""
    if not reference:
        return False
    return db.execute(
        'SELECT 1 FROM journal_entries WHERE reference = ?', (reference,)
    ).fetchone() is not None


def _sample_missing_by_ref(db, label, sql, sample_limit):
    rows = db.execute(sql).fetchall()
    missing = []
    for r in rows:
        ref = r['ref']
        if not _je_exists_ref(db, ref):
            missing.append(r)
    return {
        'label': label,
        'total': len(rows),
        'missing': len(missing),
        'samples': missing[:sample_limit],
    }


def ledger_reconciliation(db, sample_limit=10):
    """Return operational records that have not yet been posted to the ledger.

    This is intentionally conservative: a record is considered posted when its
    stable business reference appears on a journal entry. Honorarium has no
    business reference, so it is matched by source_module/source_id.
    """
    sample_limit = max(1, int(sample_limit or 10))
    sections = []

    sections.append(_sample_missing_by_ref(db, 'Savings deposits', '''
        SELECT s.id, s.member_id, m.member_number,
               m.first_name || ' ' || m.last_name AS member_name,
               COALESCE(NULLIF(s.receipt_number, ''), NULLIF(s.reference, ''), 'SAV-' || CAST(s.id AS TEXT)) AS ref,
               s.date, s.amount, s.month
        FROM savings s
        JOIN members m ON m.id = s.member_id
        WHERE COALESCE(s.payment_type, '') != 'dividend'
        ORDER BY s.date DESC, s.id DESC
    ''', sample_limit))

    sections.append(_sample_missing_by_ref(db, 'Loan disbursements', '''
        SELECT l.id, l.member_id, m.member_number,
               m.first_name || ' ' || m.last_name AS member_name,
               l.loan_number AS ref, COALESCE(l.disbursement_date, l.date_applied) AS date,
               l.amount, l.purpose AS month
        FROM loans l
        JOIN members m ON m.id = l.member_id
        WHERE l.status IN ('active', 'completed') AND l.loan_number IS NOT NULL
        ORDER BY COALESCE(l.disbursement_date, l.date_applied) DESC, l.id DESC
    ''', sample_limit))

    sections.append(_sample_missing_by_ref(db, 'Loan repayments', '''
        SELECT r.id, l.member_id, m.member_number,
               m.first_name || ' ' || m.last_name AS member_name,
               COALESCE(NULLIF(r.repayment_number, ''), NULLIF(r.reference, ''), 'REP-' || CAST(r.id AS TEXT)) AS ref,
               r.date, r.amount, l.loan_number AS month
        FROM repayments r
        JOIN loans l ON l.id = r.loan_id
        JOIN members m ON m.id = l.member_id
        ORDER BY r.date DESC, r.id DESC
    ''', sample_limit))

    sections.append(_sample_missing_by_ref(db, 'Expenses', '''
        SELECT id, NULL AS member_id, '' AS member_number, category AS member_name,
               COALESCE(NULLIF(expense_number, ''), 'EXP-' || CAST(id AS TEXT)) AS ref,
               date, amount, payment_method AS month
        FROM expenses
        ORDER BY date DESC, id DESC
    ''', sample_limit))

    sections.append(_sample_missing_by_ref(db, 'Revenue', '''
        SELECT id, NULL AS member_id, '' AS member_number, category AS member_name,
               COALESCE(NULLIF(revenue_number, ''), 'REV-' || CAST(id AS TEXT)) AS ref,
               date, amount, source AS month
        FROM revenue
        WHERE COALESCE(category, '') NOT IN ('Late Fee', 'Loan Insurance', 'Loan Application Fee')
        ORDER BY date DESC, id DESC
    ''', sample_limit))

    sections.append(_sample_missing_by_ref(db, 'Investments', '''
        SELECT id, NULL AS member_id, '' AS member_number, name AS member_name,
               COALESCE(NULLIF(investment_number, ''), 'INV-' || CAST(id AS TEXT)) AS ref,
               date, amount, type AS month
        FROM investments
        ORDER BY date DESC, id DESC
    ''', sample_limit))

    honorarium_rows = db.execute('''
        SELECT id, NULL AS member_id, '' AS member_number,
               COALESCE(recipient_name, '') AS member_name,
               'HON-' || CAST(id AS TEXT) AS ref, date, amount, month
        FROM honorarium
        ORDER BY date DESC, id DESC
    ''').fetchall()
    honorarium_missing = []
    for h in honorarium_rows:
        exists = db.execute(
            "SELECT 1 FROM journal_entries WHERE source_module = 'honorarium' AND source_id = ?",
            (h['id'],)
        ).fetchone()
        if not exists:
            honorarium_missing.append(h)
    sections.append({
        'label': 'Honorarium',
        'total': len(honorarium_rows),
        'missing': len(honorarium_missing),
        'samples': honorarium_missing[:sample_limit],
    })

    total_records = sum(s['total'] for s in sections)
    total_missing = sum(s['missing'] for s in sections)
    posted_entries = db.execute('SELECT COUNT(*) FROM journal_entries').fetchone()[0] or 0
    posted_lines = db.execute('SELECT COUNT(*) FROM journal_lines').fetchone()[0] or 0
    return {
        'sections': sections,
        'total_records': total_records,
        'total_missing': total_missing,
        'posted_entries': posted_entries,
        'posted_lines': posted_lines,
        'complete': total_missing == 0,
    }


def backfill_from_transactions(db, created_by=None):
    """Post journal entries for existing transactions that don't have one yet.

    Idempotent: transactions already in the ledger (by their unique reference,
    or by source for honorarium) are skipped, so this is safe to run more than
    once and safe to run alongside live posting. Does NOT commit.

    Returns the number of journal entries posted.
    """
    from utils import split_repayment
    posted = 0

    # Savings deposits
    for s in db.execute('SELECT * FROM savings').fetchall():
        # Dividend credits are posted as one aggregate entry by the dividend
        # engine — don't double-post them here.
        if (s['payment_type'] or '') == 'dividend':
            continue
        ref = s['receipt_number'] or f"SAV-{s['id']}"
        if _je_exists_ref(db, ref):
            continue
        amount = float(s['amount'] or 0)
        late   = float(s['late_fee'] or 0)
        if amount + late <= 0:
            continue
        lines = [
            {'account': CASH, 'debit': amount + late, 'memo': f"Savings {s['month']}"},
            {'account': MEMBER_DEPOSITS, 'credit': amount, 'memo': f"Member {s['member_id']}"},
        ]
        if late:
            lines.append({'account': FEE_INCOME, 'credit': late, 'memo': 'Late fee'})
        if post_journal_safe(db, f"Savings deposit — {s['month']}", lines,
                             date=s['date'], reference=ref, source_module='savings',
                             source_id=s['member_id'], created_by=created_by):
            posted += 1

    # Loan disbursements (active or completed loans)
    for l in db.execute("SELECT * FROM loans WHERE status IN ('active','completed')").fetchall():
        ref = l['loan_number']
        if _je_exists_ref(db, ref):
            continue
        principal = float(l['amount'] or 0)
        if principal <= 0:
            continue
        ins  = float(l['insurance_premium'] or 0)
        appf = float(l['application_fee'] or 0)
        fees = ins + appf
        disbursed = l['disbursed_amount']
        disbursed = float(disbursed) if disbursed is not None else (principal - fees)
        lines = [{'account': LOANS_RECEIVABLE, 'debit': principal, 'memo': l['loan_number']}]
        if disbursed:
            lines.append({'account': CASH, 'credit': disbursed, 'memo': 'Net disbursed'})
        if fees:
            lines.append({'account': FEE_INCOME, 'credit': fees, 'memo': 'Loan fees'})
        if post_journal_safe(db, f"Loan disbursement — {ref}", lines,
                             date=l['disbursement_date'] or l['date_applied'], reference=ref,
                             source_module='loans', source_id=l['id'], created_by=created_by):
            posted += 1

    # Loan repayments
    for r in db.execute('''SELECT r.*, l.amount AS principal, l.total_repayment, l.loan_number
                           FROM repayments r JOIN loans l ON l.id = r.loan_id''').fetchall():
        ref = r['repayment_number'] or f"REP-{r['id']}"
        if _je_exists_ref(db, ref):
            continue
        amount = float(r['amount'] or 0)
        if amount <= 0:
            continue
        pp = float(r['principal_paid'] or 0)
        ip = float(r['interest_paid'] or 0)
        if pp == 0 and ip == 0:
            pp, ip = split_repayment(amount, r['principal'], r['total_repayment'])
        if post_journal_safe(db, f"Loan repayment — {r['loan_number']}", [
            {'account': CASH, 'debit': amount, 'memo': 'Repayment'},
            {'account': LOANS_RECEIVABLE, 'credit': pp, 'memo': r['loan_number']},
            {'account': LOAN_INTEREST_INCOME, 'credit': ip, 'memo': 'Interest earned'},
        ], date=r['date'], reference=ref, source_module='loans',
           source_id=r['loan_id'], created_by=created_by):
            posted += 1

    # Expenses
    for e in db.execute('SELECT * FROM expenses').fetchall():
        ref = e['expense_number'] or f"EXP-{e['id']}"
        if _je_exists_ref(db, ref):
            continue
        amt = float(e['amount'] or 0)
        if amt <= 0:
            continue
        if post_journal_safe(db, f"Expense — {e['category']}", [
            {'account': OPERATING_EXPENSES, 'debit': amt, 'memo': e['description'] or ''},
            {'account': CASH, 'credit': amt},
        ], date=e['date'], reference=ref, source_module='expenses', created_by=created_by):
            posted += 1

    # Revenue
    for rv in db.execute('SELECT * FROM revenue').fetchall():
        if rv['category'] in OPERATIONAL_REVENUE_CATEGORIES:
            continue
        ref = rv['revenue_number'] or f"REV-{rv['id']}"
        if _je_exists_ref(db, ref):
            continue
        amt = float(rv['amount'] or 0)
        if amt <= 0:
            continue
        if post_journal_safe(db, f"Revenue — {rv['category']}", [
            {'account': CASH, 'debit': amt},
            {'account': FEE_INCOME, 'credit': amt, 'memo': rv['description'] or ''},
        ], date=rv['date'], reference=ref, source_module='revenue', created_by=created_by):
            posted += 1

    # Honorarium (no unique reference — identify by source)
    for h in db.execute('SELECT * FROM honorarium').fetchall():
        exists = db.execute(
            "SELECT 1 FROM journal_entries WHERE source_module = 'honorarium' AND source_id = ?",
            (h['id'],)
        ).fetchone()
        if exists:
            continue
        amt = float(h['amount'] or 0)
        if amt <= 0:
            continue
        if post_journal_safe(db, f"Honorarium — {h['recipient_name'] or ''}", [
            {'account': HONORARIUM, 'debit': amt, 'memo': h['recipient_name'] or ''},
            {'account': CASH, 'credit': amt},
        ], date=h['date'], source_module='honorarium', source_id=h['id'], created_by=created_by):
            posted += 1

    # Investments
    for iv in db.execute('SELECT * FROM investments').fetchall():
        ref = iv['investment_number'] or f"INV-{iv['id']}"
        if _je_exists_ref(db, ref):
            continue
        amt = float(iv['amount'] or 0)
        if amt <= 0:
            continue
        if post_journal_safe(db, f"Investment — {iv['name']}", [
            {'account': INVESTMENTS, 'debit': amt, 'memo': iv['name']},
            {'account': CASH, 'credit': amt},
        ], date=iv['date'], reference=ref, source_module='investments', created_by=created_by):
            posted += 1

    return posted


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
