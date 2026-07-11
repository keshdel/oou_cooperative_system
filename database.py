"""
database.py — dual-backend database layer
  • PostgreSQL in production (DATABASE_URL env var set by Railway add-on)
  • SQLite for local development (no DATABASE_URL)

All application code uses the same API:
    db = get_db()
    db.execute(sql, params)   # uses ? placeholders everywhere
    row['column']  or  row[0] # both work
    db.commit() / db.rollback() / db.close()
"""

import os
import re
import secrets
from datetime import datetime
from werkzeug.security import generate_password_hash

# ── Backend detection ──────────────────────────────────────────────────────────

DATABASE_URL = os.environ.get('DATABASE_URL', '')
# Railway injects postgres:// URLs; psycopg2 requires postgresql://
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

USE_POSTGRES = bool(DATABASE_URL and DATABASE_URL.startswith('postgresql'))

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras
else:
    import sqlite3
    _SQLITE_DB = os.environ.get('SQLITE_DB_PATH', 'cooperative.db')


# ── Row wrapper ────────────────────────────────────────────────────────────────

from datetime import date as _date, datetime as _datetime
from decimal import Decimal as _Decimal


def _coerce(v):
    """Make PostgreSQL values match what SQLite (and the app) expect: dates as
    'YYYY-MM-DD[ HH:MM:SS]' strings, and Decimal as float. No-op for values that
    are already strings/floats (SQLite), so it is safe on both backends."""
    if isinstance(v, _datetime):
        return v.strftime('%Y-%m-%d %H:%M:%S')
    if isinstance(v, _date):
        return v.strftime('%Y-%m-%d')
    if isinstance(v, _Decimal):
        return float(v)
    return v


class _DictRow(dict):
    """
    Dict subclass that also supports integer index access (like sqlite3.Row).
    Allows row[0] and row['column'] to both work, so existing code needs
    no changes when switching from SQLite. Values are coerced so PostgreSQL
    dates/decimals look like SQLite's strings/floats.
    """
    def __init__(self, mapping):
        coerced = {k: _coerce(v) for k, v in mapping.items()}
        super().__init__(coerced)
        self._vals = list(coerced.values())

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._vals[key]
        return super().__getitem__(key)

    def keys(self):
        return super().keys()


# ── PostgreSQL cursor wrapper ──────────────────────────────────────────────────

class _PGCursor:
    def __init__(self, cur):
        self._cur = cur

    def fetchone(self):
        row = self._cur.fetchone()
        return _DictRow(row) if row is not None else None

    def fetchall(self):
        return [_DictRow(r) for r in (self._cur.fetchall() or [])]

    def __iter__(self):
        return iter(self.fetchall())


# ── PostgreSQL connection wrapper ──────────────────────────────────────────────

class _PGConn:
    """
    Wraps a psycopg2 connection so it looks like sqlite3 to the rest of the app:
    - Accepts ? placeholders (converts to %s for psycopg2)
    - Returns _DictRow objects that support both dict and index access
    """
    def __init__(self, raw):
        self._conn = raw

    def execute(self, sql, params=()):
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        pg_sql = sql.replace('?', '%s')
        cur.execute(pg_sql, params if params else None)
        return _PGCursor(cur)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    def __del__(self):
        self.close()


# ── Public API ─────────────────────────────────────────────────────────────────

def _sqlite_row_factory(cursor, row):
    """Return SQLite rows as _DictRow, matching the PostgreSQL backend exactly.

    Using sqlite3.Row here caused SQLite-only crashes: sqlite3.Row supports
    row['col'] and row[0] but NOT row.get(), while the PostgreSQL _DictRow
    (a dict subclass) does — so code paths that call .get() on a row (e.g.
    email_service) worked in production but raised on SQLite. Unifying the row
    type removes that dev/prod drift.
    """
    return _DictRow({col[0]: row[idx] for idx, col in enumerate(cursor.description)})


def _open_connection():
    """Open a brand-new raw database connection."""
    if USE_POSTGRES:
        return _PGConn(psycopg2.connect(DATABASE_URL))
    db = sqlite3.connect(_SQLITE_DB)
    db.row_factory = _sqlite_row_factory
    return db


def get_db():
    """Return a database connection (PostgreSQL or SQLite depending on env).

    Inside a Flask request/app context the connection is cached on ``flask.g``
    and reused for the rest of the request, then closed by the
    ``teardown_appcontext`` handler registered in app.py.  This prevents the
    connection leak that occurred when every call opened a fresh connection
    that was never closed (which exhausts PostgreSQL's connection pool).

    Outside an app context (CLI scripts, ``init_db`` at import time) a plain
    connection is returned; the caller is responsible for closing it.
    """
    try:
        from flask import g, has_app_context
        if has_app_context():
            db = getattr(g, '_database', None)
            if db is None:
                db = g._database = _open_connection()
            return db
    except Exception:
        pass
    return _open_connection()


def close_db(exception=None):
    """Close the request-scoped connection, if any. Registered as a Flask
    teardown_appcontext handler in app.py."""
    try:
        from flask import g
        db = getattr(g, '_database', None)
        if db is not None:
            g._database = None
            db.close()
    except Exception:
        pass


def last_insert_id(db):
    """Return the ID generated by the most recent INSERT.

    SQLite  : SELECT last_insert_rowid()
    PostgreSQL: SELECT lastval()  — works after any INSERT into a SERIAL column
    """
    if USE_POSTGRES:
        return db.execute('SELECT lastval()').fetchone()[0]
    return db.execute('SELECT last_insert_rowid()').fetchone()[0]


# ── DDL helpers ────────────────────────────────────────────────────────────────

def _adapt(sql):
    """Convert SQLite DDL to PostgreSQL-compatible DDL."""
    if not USE_POSTGRES:
        return sql
    # AUTOINCREMENT → SERIAL (PostgreSQL sequences)
    sql = sql.replace('INTEGER PRIMARY KEY AUTOINCREMENT', 'SERIAL PRIMARY KEY')
    # SQLite REAL = 8-byte float; PostgreSQL REAL = 4-byte; use DOUBLE PRECISION
    sql = re.sub(r'\bREAL\b', 'DOUBLE PRECISION', sql)
    # SQLite BLOB → PostgreSQL BYTEA
    sql = re.sub(r'\bBLOB\b', 'BYTEA', sql)
    return sql


def _add_col(db, table, column, col_def):
    """
    ALTER TABLE … ADD COLUMN — safe for both databases.
    Uses SAVEPOINTs for PostgreSQL so a duplicate-column error doesn't
    abort the whole transaction.
    """
    if USE_POSTGRES:
        sp = f"sp_{table}_{column}"
        db.execute(f"SAVEPOINT {sp}")
        try:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
            db.execute(f"RELEASE SAVEPOINT {sp}")
        except Exception:
            db.execute(f"ROLLBACK TO SAVEPOINT {sp}")
    else:
        try:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
        except Exception:
            pass  # column already exists


def _exec_ignore(db, sql):
    """Run best-effort DDL that is safe to skip on existing databases."""
    try:
        db.execute(_adapt(sql))
    except Exception as exc:
        print(f"[schema] skipped optional DDL: {exc}")


# ── Schema ─────────────────────────────────────────────────────────────────────

def init_db():
    db = get_db()

    # Users table
    db.execute(_adapt('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            full_name TEXT,
            email TEXT,
            phone TEXT,
            is_active INTEGER DEFAULT 1,
            must_change_password INTEGER DEFAULT 0,
            two_factor_secret TEXT,
            last_login TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    '''))
    _add_col(db, 'users', 'must_change_password', 'INTEGER DEFAULT 0')
    _add_col(db, 'users', 'is_super_admin',       'INTEGER DEFAULT 0')

    # Members table
    db.execute(_adapt('''
        CREATE TABLE IF NOT EXISTS members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_number TEXT UNIQUE,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            email TEXT UNIQUE,
            phone TEXT,
            address TEXT,
            occupation TEXT,
            date_of_birth DATE,
            nominee_name TEXT,
            nominee_relationship TEXT,
            nominee_phone TEXT,
            nominee_email TEXT,
            nominee_address TEXT,
            alt_nominee_name TEXT,
            alt_nominee_relationship TEXT,
            monthly_savings REAL DEFAULT 5000,
            total_savings REAL DEFAULT 0,
            shares INTEGER DEFAULT 0,
            shares_value REAL DEFAULT 0,
            status TEXT DEFAULT 'active',
            date_joined TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            photo_path TEXT,
            card_number TEXT,
            card_status TEXT DEFAULT 'active',
            card_issued_date TIMESTAMP,
            card_expiry_date TIMESTAMP,
            emergency_contact_name TEXT,
            emergency_contact_phone TEXT,
            next_of_kin TEXT,
            bank_name TEXT,
            account_number TEXT,
            account_name TEXT,
            bvn TEXT,
            nin TEXT
        )
    '''))
    _add_col(db, 'members', 'card_token', 'TEXT')
    _add_col(db, 'members', 'card_path',  'TEXT')
    _add_col(db, 'members', 'employee_id', 'TEXT')

    # Savings table
    db.execute(_adapt('''
        CREATE TABLE IF NOT EXISTS savings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            month TEXT NOT NULL,
            payment_type TEXT DEFAULT 'monthly',
            late_fee REAL DEFAULT 0,
            payment_method TEXT DEFAULT 'cash',
            reference TEXT,
            receipt_number TEXT,
            notes TEXT,
            created_by INTEGER,
            date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            verified_by INTEGER,
            verified_at TIMESTAMP,
            FOREIGN KEY (member_id) REFERENCES members (id)
        )
    '''))
    _add_col(db, 'savings', 'payment_type', "TEXT DEFAULT 'monthly'")
    # Portion of a contribution allocated to share capital (audit trail per row)
    _add_col(db, 'savings', 'share_capital', 'REAL DEFAULT 0')
    _add_col(db, 'savings', 'reference', 'TEXT')
    _add_col(db, 'savings', 'receipt_number', 'TEXT')
    _add_col(db, 'savings', 'notes', 'TEXT')
    _add_col(db, 'savings', 'created_by', 'INTEGER')
    _add_col(db, 'savings', 'verified_by', 'INTEGER')
    _add_col(db, 'savings', 'verified_at', 'TIMESTAMP')
    _add_col(db, 'savings', 'import_batch', 'TEXT')
    _add_col(db, 'savings', 'source_file', 'TEXT')

    # Loans table
    db.execute(_adapt('''
        CREATE TABLE IF NOT EXISTS loans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            loan_number TEXT UNIQUE,
            member_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            purpose TEXT,
            description TEXT,
            tenure INTEGER,
            interest_rate REAL,
            interest_method TEXT DEFAULT 'reducing_annual',
            total_repayment REAL,
            balance REAL,
            status TEXT DEFAULT 'pending',
            application_fee REAL DEFAULT 0,
            insurance_premium REAL DEFAULT 0,
            disbursed_amount REAL,
            disbursement_date TIMESTAMP,
            first_payment_date TIMESTAMP,
            next_payment_date TIMESTAMP,
            approved_by INTEGER,
            approved_at TIMESTAMP,
            rejection_reason TEXT,
            completed_at TIMESTAMP,
            defaulted INTEGER DEFAULT 0,
            notes TEXT,
            date_applied TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (member_id) REFERENCES members (id)
        )
    '''))
    _add_col(db, 'loans', 'loan_number', 'TEXT')
    _add_col(db, 'loans', 'interest_method', "TEXT DEFAULT 'reducing_annual'")
    # Loan approval workflow stage: guarantors -> secretary -> treasurer -> president -> approved/rejected
    _add_col(db, 'loans', 'approval_stage', "TEXT DEFAULT 'secretary'")
    # Applicant terms-and-conditions consent (typed-name signature + date)
    _add_col(db, 'loans', 'terms_accepted', 'INTEGER DEFAULT 0')
    _add_col(db, 'loans', 'signature_name', 'TEXT')
    _add_col(db, 'loans', 'signed_at', 'TIMESTAMP')
    _add_col(db, 'loans', 'disbursed_amount', 'REAL')
    _add_col(db, 'loans', 'disbursement_date', 'TIMESTAMP')
    _add_col(db, 'loans', 'first_payment_date', 'TIMESTAMP')
    _add_col(db, 'loans', 'next_payment_date', 'TIMESTAMP')
    _add_col(db, 'loans', 'approved_by', 'INTEGER')
    _add_col(db, 'loans', 'approved_at', 'TIMESTAMP')
    _add_col(db, 'loans', 'rejection_reason', 'TEXT')
    _add_col(db, 'loans', 'completed_at', 'TIMESTAMP')
    _add_col(db, 'loans', 'defaulted', 'INTEGER DEFAULT 0')
    _add_col(db, 'loans', 'notes', 'TEXT')

    # Repayments table
    db.execute(_adapt('''
        CREATE TABLE IF NOT EXISTS repayments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repayment_number TEXT UNIQUE,
            loan_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            principal_paid REAL DEFAULT 0,
            interest_paid REAL DEFAULT 0,
            penalty_paid REAL DEFAULT 0,
            payment_method TEXT DEFAULT 'cash',
            reference TEXT,
            receipt_number TEXT,
            transaction_id TEXT,
            notes TEXT,
            received_by INTEGER,
            verified_by INTEGER,
            verified_at TIMESTAMP,
            date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (loan_id) REFERENCES loans (id)
        )
    '''))
    # Ensure the principal/interest split columns exist on databases created
    # from an older schema (safe no-op if they already exist).
    _add_col(db, 'repayments', 'repayment_number', 'TEXT')
    _add_col(db, 'repayments', 'principal_paid', 'REAL DEFAULT 0')
    _add_col(db, 'repayments', 'interest_paid',  'REAL DEFAULT 0')
    _add_col(db, 'repayments', 'penalty_paid',   'REAL DEFAULT 0')
    _add_col(db, 'repayments', 'reference', 'TEXT')
    _add_col(db, 'repayments', 'receipt_number', 'TEXT')
    _add_col(db, 'repayments', 'transaction_id', 'TEXT')
    _add_col(db, 'repayments', 'notes', 'TEXT')
    _add_col(db, 'repayments', 'received_by', 'INTEGER')
    _add_col(db, 'repayments', 'verified_by', 'INTEGER')
    _add_col(db, 'repayments', 'verified_at', 'TIMESTAMP')

    # Loan guarantors — members who back a loan and must consent
    db.execute(_adapt('''
        CREATE TABLE IF NOT EXISTS loan_guarantors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            loan_id INTEGER NOT NULL,
            member_id INTEGER NOT NULL,
            status TEXT DEFAULT 'pending',
            requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            responded_at TIMESTAMP,
            comment TEXT,
            FOREIGN KEY (loan_id) REFERENCES loans (id),
            FOREIGN KEY (member_id) REFERENCES members (id)
        )
    '''))
    # Loan approval audit trail — one row per stage action
    db.execute(_adapt('''
        CREATE TABLE IF NOT EXISTS loan_approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            loan_id INTEGER NOT NULL,
            stage TEXT NOT NULL,
            action TEXT NOT NULL,
            acted_by INTEGER,
            acted_by_name TEXT,
            acted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            comment TEXT,
            FOREIGN KEY (loan_id) REFERENCES loans (id)
        )
    '''))
    _exec_ignore(db, 'CREATE INDEX IF NOT EXISTS idx_loan_guarantors_loan ON loan_guarantors(loan_id)')
    _exec_ignore(db, 'CREATE INDEX IF NOT EXISTS idx_loan_guarantors_member ON loan_guarantors(member_id)')
    _exec_ignore(db, 'CREATE INDEX IF NOT EXISTS idx_loan_approvals_loan ON loan_approvals(loan_id)')

    # Investments table
    db.execute(_adapt('''
        CREATE TABLE IF NOT EXISTS investments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            investment_number TEXT UNIQUE,
            name TEXT NOT NULL,
            amount REAL NOT NULL,
            type TEXT NOT NULL,
            description TEXT,
            institution TEXT,
            interest_rate REAL,
            return_rate REAL,
            risk_level TEXT DEFAULT 'medium',
            start_date TIMESTAMP,
            maturity_date TIMESTAMP,
            duration_days INTEGER,
            expected_return REAL,
            actual_return REAL,
            current_value REAL,
            approval_status TEXT DEFAULT 'pending',
            approved_by INTEGER,
            approved_at TIMESTAMP,
            documents TEXT,
            notes TEXT,
            created_by INTEGER,
            date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (approved_by) REFERENCES users (id),
            FOREIGN KEY (created_by) REFERENCES users (id)
        )
    '''))

    # Honorarium table
    db.execute(_adapt('''
        CREATE TABLE IF NOT EXISTS honorarium (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipient_id INTEGER,
            recipient_name TEXT,
            amount REAL NOT NULL,
            description TEXT,
            month TEXT,
            date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            paid_by INTEGER,
            FOREIGN KEY (recipient_id) REFERENCES members (id),
            FOREIGN KEY (paid_by) REFERENCES users (id)
        )
    '''))
    _add_col(db, 'honorarium', 'recipient_id', 'INTEGER')
    _add_col(db, 'honorarium', 'recipient_name', 'TEXT')
    _add_col(db, 'honorarium', 'description', 'TEXT')
    _add_col(db, 'honorarium', 'month', 'TEXT')
    _add_col(db, 'honorarium', 'paid_by', 'INTEGER')

    # Expenses table
    db.execute(_adapt('''
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            expense_number TEXT UNIQUE,
            category TEXT NOT NULL,
            amount REAL NOT NULL,
            description TEXT,
            vendor TEXT,
            receipt_number TEXT,
            paid_to TEXT,
            payment_method TEXT,
            date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            approved_by INTEGER,
            recorded_by INTEGER,
            notes TEXT,
            FOREIGN KEY (approved_by) REFERENCES users (id),
            FOREIGN KEY (recorded_by) REFERENCES users (id)
        )
    '''))

    # Revenue table
    db.execute(_adapt('''
        CREATE TABLE IF NOT EXISTS revenue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            revenue_number TEXT UNIQUE,
            category TEXT NOT NULL,
            amount REAL NOT NULL,
            description TEXT,
            source TEXT,
            date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            received_by INTEGER,
            notes TEXT,
            FOREIGN KEY (received_by) REFERENCES users (id)
        )
    '''))

    # Settings table
    db.execute(_adapt('''
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE NOT NULL,
            value TEXT NOT NULL,
            description TEXT
        )
    '''))

    # Notifications table
    db.execute(_adapt('''
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            notification_type TEXT DEFAULT 'info',
            is_read INTEGER DEFAULT 0,
            action_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            read_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    '''))

    # Pending payments table
    db.execute(_adapt('''
        CREATE TABLE IF NOT EXISTS pending_payments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            reference       TEXT UNIQUE NOT NULL,
            member_id       INTEGER NOT NULL,
            payment_type    TEXT NOT NULL,
            related_id      INTEGER,
            amount          REAL NOT NULL,
            month           TEXT,
            gateway         TEXT NOT NULL,
            status          TEXT DEFAULT 'pending',
            gateway_ref     TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at    TIMESTAMP,
            FOREIGN KEY (member_id) REFERENCES members (id)
        )
    '''))

    # Audit log table
    db.execute(_adapt('''
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            action TEXT NOT NULL,
            module TEXT,
            description TEXT,
            ip_address TEXT,
            user_agent TEXT,
            data TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    '''))

    # Events / announcements (AGM, meetings) shown on the members' banner
    db.execute(_adapt('''
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            event_type TEXT DEFAULT 'announcement',
            event_date TIMESTAMP,
            location TEXT,
            meeting_link TEXT,
            description TEXT,
            is_active INTEGER DEFAULT 1,
            created_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    '''))
    # Virtual-meeting link (guard for databases created before this column existed)
    _add_col(db, 'events', 'meeting_link', 'TEXT')
    # Minutes of meeting repository — file stored in the DB so it survives
    # redeploys on platforms with an ephemeral filesystem (e.g. Railway).
    db.execute(_adapt('''
        CREATE TABLE IF NOT EXISTS meeting_minutes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            meeting_type TEXT DEFAULT 'general',
            meeting_date DATE,
            file_name TEXT,
            file_mime TEXT,
            file_data BLOB,
            notes TEXT,
            uploaded_by INTEGER,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    '''))
    _exec_ignore(db, 'CREATE INDEX IF NOT EXISTS idx_events_date ON events(event_date)')

    # Member requests to change their monthly savings amount (staff-approved)
    db.execute(_adapt('''
        CREATE TABLE IF NOT EXISTS savings_change_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id INTEGER NOT NULL,
            current_amount REAL DEFAULT 0,
            requested_amount REAL NOT NULL,
            reason TEXT,
            status TEXT DEFAULT 'pending',
            requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            reviewed_by INTEGER,
            reviewed_by_name TEXT,
            reviewed_at TIMESTAMP,
            review_comment TEXT,
            FOREIGN KEY (member_id) REFERENCES members (id)
        )
    '''))
    _exec_ignore(db, 'CREATE INDEX IF NOT EXISTS idx_savings_change_status ON savings_change_requests(status)')

    # ── Double-entry general ledger ────────────────────────────────────────────
    # Chart of accounts
    db.execute(_adapt('''
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            type TEXT NOT NULL,             -- asset | liability | equity | income | expense
            normal_balance TEXT NOT NULL,   -- debit | credit
            parent_code TEXT,
            is_active INTEGER DEFAULT 1,
            description TEXT
        )
    '''))
    # Journal entry headers
    db.execute(_adapt('''
        CREATE TABLE IF NOT EXISTS journal_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_number TEXT UNIQUE,
            date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            description TEXT,
            reference TEXT,
            source_module TEXT,
            source_id INTEGER,
            created_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    '''))
    # Journal entry lines (debits and credits)
    db.execute(_adapt('''
        CREATE TABLE IF NOT EXISTS journal_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id INTEGER NOT NULL,
            account_code TEXT NOT NULL,
            debit REAL DEFAULT 0,
            credit REAL DEFAULT 0,
            memo TEXT,
            FOREIGN KEY (entry_id) REFERENCES journal_entries (id)
        )
    '''))
    _exec_ignore(db, 'CREATE INDEX IF NOT EXISTS idx_journal_lines_entry ON journal_lines(entry_id)')
    _exec_ignore(db, 'CREATE INDEX IF NOT EXISTS idx_journal_lines_account ON journal_lines(account_code)')
    _exec_ignore(db, 'CREATE INDEX IF NOT EXISTS idx_journal_entries_date ON journal_entries(date)')

    # Dividend declarations (year-end surplus distribution)
    db.execute(_adapt('''
        CREATE TABLE IF NOT EXISTS dividend_declarations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period_from TEXT NOT NULL,
            period_to TEXT NOT NULL,
            net_surplus REAL NOT NULL,
            reserve_amount REAL DEFAULT 0,
            honorarium_amount REAL DEFAULT 0,
            other_amount REAL DEFAULT 0,
            dividend_pool REAL DEFAULT 0,
            patronage_split REAL DEFAULT 0,
            status TEXT DEFAULT 'declared',
            journal_entry_id INTEGER,
            declared_by INTEGER,
            declared_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    '''))
    # Per-member dividend allocations for a declaration
    db.execute(_adapt('''
        CREATE TABLE IF NOT EXISTS dividend_allocations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            declaration_id INTEGER NOT NULL,
            member_id INTEGER NOT NULL,
            savings_base REAL DEFAULT 0,
            patronage_base REAL DEFAULT 0,
            dividend_savings REAL DEFAULT 0,
            dividend_patronage REAL DEFAULT 0,
            total REAL DEFAULT 0,
            FOREIGN KEY (declaration_id) REFERENCES dividend_declarations (id),
            FOREIGN KEY (member_id) REFERENCES members (id)
        )
    '''))
    _exec_ignore(db, 'CREATE INDEX IF NOT EXISTS idx_div_alloc_declaration ON dividend_allocations(declaration_id)')
    _exec_ignore(db, 'CREATE INDEX IF NOT EXISTS idx_div_alloc_member ON dividend_allocations(member_id)')

    # Seed the default cooperative chart of accounts (idempotent)
    default_accounts = [
        ('1000', 'Cash & Bank',                'asset',     'debit',  None),
        ('1100', 'Loans Receivable',           'asset',     'debit',  None),
        ('1200', 'Investments',                'asset',     'debit',  None),
        ('2000', 'Member Deposits (Savings)',  'liability', 'credit', None),
        ('3000', 'Accumulated Surplus',        'equity',    'credit', None),
        ('3100', 'Statutory Reserve',          'equity',    'credit', None),
        ('3200', 'Member Share Capital',       'equity',    'credit', None),
        ('4000', 'Loan Interest Income',       'income',    'credit', None),
        ('4100', 'Fee Income',                 'income',    'credit', None),
        ('4200', 'Investment Income',          'income',    'credit', None),
        ('5000', 'Operating Expenses',         'expense',   'debit',  None),
        ('5100', 'Honorarium',                 'expense',   'debit',  None),
    ]
    for code, name, atype, normal, parent in default_accounts:
        try:
            db.execute('''
                INSERT INTO accounts (code, name, type, normal_balance, parent_code)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(code) DO NOTHING
            ''', (code, name, atype, normal, parent))
        except Exception as e:
            print(f"Error seeding account {code}: {e}")

    # Lookup indexes for the most frequent auth, member, ledger, and payment paths.
    _exec_ignore(db, 'CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)')
    _exec_ignore(db, 'CREATE INDEX IF NOT EXISTS idx_members_email ON members(email)')
    _exec_ignore(db, 'CREATE INDEX IF NOT EXISTS idx_savings_member_month ON savings(member_id, month)')
    _exec_ignore(db, 'CREATE INDEX IF NOT EXISTS idx_loans_member_status ON loans(member_id, status)')
    _exec_ignore(db, 'CREATE INDEX IF NOT EXISTS idx_repayments_loan ON repayments(loan_id)')
    _exec_ignore(db, 'CREATE INDEX IF NOT EXISTS idx_repayments_reference ON repayments(reference)')
    _exec_ignore(db, 'CREATE INDEX IF NOT EXISTS idx_pending_payments_member_status ON pending_payments(member_id, status)')
    _exec_ignore(db, 'CREATE INDEX IF NOT EXISTS idx_notifications_user_read ON notifications(user_id, is_read)')

    # ── Default settings ───────────────────────────────────────────────────────
    default_settings = [
        ('coop_name',       'OOU Acctg 2005 Alumni CMS', 'Cooperative full name'),
        ('coop_short_name', 'OOU Coop',                  'Short name shown in sidebar and reports'),
        ('coop_logo',       '',                           'Logo path relative to static/ (e.g. uploads/logo.png)'),
        ('reg_number', 'CMS/2005/001', 'Registration number'),
        ('address', '', 'Cooperative address'),
        ('phone', '', 'Contact phone'),
        ('email', '', 'Contact email'),
        ('fy_start', '1', 'Financial year start month'),
        ('currency', 'NGN', 'Currency'),
        ('date_format', 'Y-m-d', 'Date format'),
        ('session_timeout', '30', 'Session timeout in minutes'),
        ('maintenance_mode', '0', 'Maintenance mode'),
        ('min_savings', '5000', 'Minimum monthly savings'),
        ('share_capital_pct', '0', 'Percent of each savings contribution allocated to member share capital (0 = off)'),
        ('savings_due_day', '10', 'Savings due day of month'),
        ('late_fee_percent', '10', 'Late fee percentage'),
        ('min_deposit_period', '90', 'Minimum deposit period in days'),
        ('member_deposit_rate', '9', 'Member deposit interest rate'),
        ('nonmember_deposit_rate', '7', 'Non-member deposit interest rate'),
        ('dividend_rate', '50', 'Dividend rate percentage'),
        ('min_membership_months', '6', 'Minimum membership months for loan'),
        ('min_savings_for_loan', '50000', 'Minimum savings for loan'),
        ('loan_multiplier', '2', 'Loan multiplier of savings'),
        ('max_tenure_months', '18', 'Maximum loan tenure'),
        ('max_interest_rate', '11', 'Maximum loan interest rate'),
        ('insurance_rate', '1', 'Loan insurance premium rate'),
        ('guarantors_required', '2', 'Number of guarantors required'),
        ('default_penalty_rate', '20', 'Default penalty rate'),
        ('interest_regular', '11', 'Regular loan interest rate'),
        ('interest_housing', '9', 'Housing loan interest rate'),
        ('interest_emergency', '10', 'Emergency loan interest rate'),
        ('interest_asset', '10', 'Asset loan interest rate'),
        ('interest_school_fees', '9', 'School Fees loan interest rate'),
        ('interest_method_regular', 'reducing_annual', 'Regular loan computation method'),
        ('interest_method_housing', 'reducing_annual', 'Housing loan computation method'),
        ('interest_method_emergency', 'reducing_annual', 'Emergency loan computation method'),
        ('interest_method_asset', 'reducing_annual', 'Asset loan computation method'),
        ('interest_method_school_fees', 'flat', 'School Fees loan computation method'),
        ('entrance_fee', '2000', 'Entrance fee'),
        ('reentry_fee', '5000', 'Re-entry fee'),
        ('loan_application_fee', '1000', 'Loan application fee'),
        ('statement_fee', '500', 'Statement request fee'),
        ('active_gateway',          'paystack',  'Active payment gateway: paystack or flutterwave'),
        ('paystack_public_key',     '',          'Paystack publishable key (pk_...)'),
        ('paystack_secret_key',     '',          'Paystack secret key (sk_...)'),
        ('flutterwave_public_key',  '',          'Flutterwave public key (FLWPUBK_...)'),
        ('flutterwave_secret_key',  '',          'Flutterwave secret key (FLWSECK_...)'),
        ('flutterwave_webhook_hash','',          'Flutterwave webhook verification hash'),
        # ── Subscription billing ──────────────────────────────────────────────
        ('subscription_expiry',       '',      'Subscription expiry date YYYY-MM-DD (blank = no billing)'),
        ('subscription_per_user_fee', '5000', 'Per-member annual subscription fee in Naira'),
        ('subscription_email',        '',      'Billing contact email for payment receipts'),
        # ── Email ─────────────────────────────────────────────────────────────
        ('mail_enabled',   '0',  'Enable outgoing email (1=yes, 0=no)'),
        ('resend_api_key', '',   'Resend API key (re_...) — leave blank to use SMTP instead'),
        ('mail_from',      '',   'Sender address shown in inbox, e.g. "OOU Coop <noreply@yourdomain.com>"'),
        ('smtp_host',      '',   'SMTP server hostname, e.g. smtp-relay.brevo.com or smtp.gmail.com'),
        ('smtp_port',      '587','SMTP port (587 for TLS, 465 for SSL)'),
        ('smtp_user',      '',   'SMTP login username (your email address)'),
        ('smtp_pass',      '',   'SMTP login password or app password'),
    ]

    for key, value, desc in default_settings:
        try:
            db.execute('''
                INSERT INTO settings (key, value, description)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO NOTHING
            ''', (key, value, desc))
        except Exception as e:
            print(f"Error inserting setting {key}: {e}")

    # ── Seed / refresh default staff accounts ─────────────────────────────────
    existing_users = {
        row['username']
        for row in db.execute('SELECT username FROM users').fetchall()
    }

    seed_users = [
        ('admin',     os.environ.get('ADMIN_PASSWORD'),     'admin'),
        ('treasurer', os.environ.get('TREASURER_PASSWORD'), 'treasurer'),
        ('secretary', os.environ.get('SECRETARY_PASSWORD'), 'secretary'),
    ]

    for username, password, role in seed_users:
        if username in existing_users:
            continue
        if not password:
            print(f"  [auth] Skipped creating '{username}': {username.upper()}_PASSWORD is not set.")
            continue

        try:
            db.execute(
                'INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)',
                (username, generate_password_hash(password), role, datetime.now())
            )
            print(f"  [auth] Created user '{username}' with role '{role}'.")
        except Exception as e:
            print(f"Error creating user {username}: {e}")

    backend = 'PostgreSQL' if USE_POSTGRES else 'SQLite'
    print(f"\n{'=' * 60}")
    print(f"  Backend    : {backend}")
    print("  auth       : default users are create-only; passwords are never printed")
    print(f"{'=' * 60}\n")

    db.commit()
    db.close()
    print(f"Database ({backend}) initialised successfully!")


if __name__ == '__main__':
    init_db()
