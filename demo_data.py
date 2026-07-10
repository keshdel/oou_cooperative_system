"""
demo_data.py — one-click demo dataset for evaluating the system.

Reads the CSV fixtures in ./test_data (the same files used for manual migration
testing), inserts them, and posts everything to the general ledger. Use the
"Load demo data" button in Data Migration, then purge when finished.
"""

import csv
import os
import random
from datetime import datetime

from werkzeug.security import generate_password_hash

DEMO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test_data')


def _read(name):
    with open(os.path.join(DEMO_DIR, name), encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


def _num(prefix, i):
    return f"{prefix}/DEMO/{i:04d}"


def _d(v):
    """Empty/blank date -> None (NULL). PostgreSQL rejects '' in timestamp columns."""
    v = (v or '').strip() if isinstance(v, str) else v
    return v or None


def demo_is_loaded(db):
    return db.execute(
        "SELECT 1 FROM members WHERE member_number = 'OOU/2025/0001'"
    ).fetchone() is not None


def load_demo_data(db, created_by=None):
    """Insert the demo dataset and post it to the ledger. Idempotent: does
    nothing if the demo members already exist. Does NOT commit. Returns a summary."""
    from database import last_insert_id
    import ledger

    if demo_is_loaded(db):
        return {'skipped': True}

    memmap = {}
    for r in _read('1_members.csv'):
        db.execute('''
            INSERT INTO members
                (member_number, first_name, last_name, email, phone, address,
                 occupation, monthly_savings, status, date_joined,
                 nominee_name, nominee_relationship, nominee_phone,
                 bank_name, account_number, account_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (r['member_number'], r['first_name'], r['last_name'], r['email'],
              r['phone'], r.get('address'), r.get('occupation'),
              float(r.get('monthly_savings') or 0), r.get('status') or 'active',
              _d(r.get('date_joined')), r.get('nominee_name'), r.get('nominee_relationship'),
              r.get('nominee_phone'), r.get('bank_name'), r.get('account_number'),
              r.get('account_name')))
        mid = last_insert_id(db)
        memmap[r['member_number']] = mid
        if r.get('email'):
            try:
                db.execute('''INSERT INTO users
                    (username, password_hash, role, full_name, email, must_change_password, created_at)
                    VALUES (?, ?, 'member', ?, ?, 0, ?)''',
                    (r['email'], generate_password_hash(r['member_number']),
                     f"{r['first_name']} {r['last_name']}", r['email'], datetime.now()))
            except Exception:
                pass  # duplicate email — skip the login

    for r in _read('2_savings.csv'):
        mid = memmap.get(r['member_number'])
        if not mid:
            continue
        amt = float(r.get('amount') or 0)
        late = float(r.get('late_fee') or 0)
        db.execute('''INSERT INTO savings
            (member_id, amount, month, payment_type, late_fee, payment_method, receipt_number, date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (mid, amt, r['month'], r.get('payment_type') or 'monthly', late,
             r.get('payment_method') or 'cash', r.get('receipt_number'), _d(r.get('date'))))
        db.execute('UPDATE members SET total_savings = total_savings + ? WHERE id = ?', (amt, mid))

    loanmap = {}
    for r in _read('3_loans.csv'):
        mid = memmap.get(r['member_number'])
        if not mid:
            continue
        db.execute('''INSERT INTO loans
            (loan_number, member_id, amount, purpose, tenure, interest_rate,
             total_repayment, balance, status, date_applied, approved_at,
             disbursement_date, disbursed_amount)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (r['loan_number'], mid, float(r['amount']), r['purpose'],
             int(r.get('tenure') or 12), float(r.get('interest_rate') or 0),
             float(r.get('total_repayment') or 0), float(r.get('balance') or 0),
             r.get('status') or 'active', _d(r.get('date_applied')), _d(r.get('date_approved')),
             _d(r.get('disbursement_date')), float(r.get('disbursed_amount') or 0)))
        loanmap[r['loan_number']] = last_insert_id(db)

    for r in _read('4_repayments.csv'):
        lid = loanmap.get(r['loan_number'])
        if not lid:
            continue
        db.execute('''INSERT INTO repayments
            (loan_id, amount, principal_paid, interest_paid, penalty_paid,
             payment_method, receipt_number, date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (lid, float(r['amount']), float(r.get('principal_paid') or 0),
             float(r.get('interest_paid') or 0), float(r.get('penalty_paid') or 0),
             r.get('payment_method') or 'cash', r.get('receipt_number'), _d(r.get('date'))))

    for i, r in enumerate(_read('5_expenses.csv'), 1):
        db.execute('''INSERT INTO expenses
            (expense_number, category, amount, description, vendor, payment_method, date)
            VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (_num('EXP', i), r['category'], float(r['amount']), r.get('description'),
             r.get('vendor'), r.get('payment_method') or 'cash', _d(r.get('date'))))

    for i, r in enumerate(_read('6_revenue.csv'), 1):
        db.execute('''INSERT INTO revenue
            (revenue_number, category, amount, description, source, date)
            VALUES (?, ?, ?, ?, ?, ?)''',
            (_num('REV', i), r['category'], float(r['amount']), r.get('description'),
             r.get('source'), _d(r.get('date'))))

    for i, r in enumerate(_read('7_investments.csv'), 1):
        db.execute('''INSERT INTO investments
            (investment_number, name, type, amount, institution, interest_rate,
             risk_level, start_date, maturity_date, description, approval_status, date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'approved', ?)''',
            (_num('INV', i), r['name'], r['type'], float(r['amount']), r.get('institution'),
             float(r.get('interest_rate') or 0), r.get('risk_level') or 'medium',
             _d(r.get('start_date')), _d(r.get('maturity_date')), r.get('description'),
             _d(r.get('start_date')) or datetime.now()))

    for r in _read('8_honorarium.csv'):
        db.execute('''INSERT INTO honorarium
            (recipient_name, amount, description, month, date)
            VALUES (?, ?, ?, ?, ?)''',
            (r['recipient_name'], float(r['amount']), r.get('description'),
             r.get('month'), _d(r.get('date'))))

    posted = ledger.backfill_from_transactions(db, created_by=created_by)
    return {
        'skipped': False,
        'members': len(memmap),
        'loans': len(loanmap),
        'journal_entries': posted,
    }
