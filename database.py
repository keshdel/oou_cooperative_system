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

class _DictRow(dict):
    """
    Dict subclass that also supports integer index access (like sqlite3.Row).
    Allows row[0] and row['column'] to both work, so existing code needs
    no changes when switching from SQLite.
    """
    def __init__(self, mapping):
        super().__init__(mapping)
        self._vals = list(mapping.values())

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

def get_db():
    """Return a database connection (PostgreSQL or SQLite depending on env)."""
    if USE_POSTGRES:
        conn = psycopg2.connect(DATABASE_URL)
        return _PGConn(conn)
    db = sqlite3.connect(_SQLITE_DB)
    db.row_factory = sqlite3.Row
    return db


# ── DDL helpers ────────────────────────────────────────────────────────────────

def _adapt(sql):
    """Convert SQLite DDL to PostgreSQL-compatible DDL."""
    if not USE_POSTGRES:
        return sql
    # AUTOINCREMENT → SERIAL (PostgreSQL sequences)
    sql = sql.replace('INTEGER PRIMARY KEY AUTOINCREMENT', 'SERIAL PRIMARY KEY')
    # SQLite REAL = 8-byte float; PostgreSQL REAL = 4-byte; use DOUBLE PRECISION
    sql = re.sub(r'\bREAL\b', 'DOUBLE PRECISION', sql)
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
    _add_col(db, 'loans', 'interest_method', "TEXT DEFAULT 'reducing_annual'")

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

    # ── Default settings ───────────────────────────────────────────────────────
    default_settings = [
        ('coop_name', 'OOU Acctg 2005 Alumni CMS', 'Cooperative name'),
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
        ('subscription_expiry',  '',       'Subscription expiry date YYYY-MM-DD (blank = no billing)'),
        ('subscription_fee',     '50000',  'Annual subscription fee in Naira'),
        ('subscription_email',   '',       'Billing contact email for payment receipts'),
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

    _DEFAULT_ADMIN_PW = 'OOU2005admin'

    seed_users = [
        ('admin',     os.environ.get('ADMIN_PASSWORD')     or _DEFAULT_ADMIN_PW, 'admin'),
        ('treasurer', os.environ.get('TREASURER_PASSWORD') or 'treasurer2005',   'treasurer'),
        ('secretary', os.environ.get('SECRETARY_PASSWORD') or 'secretary2005',   'secretary'),
    ]

    for username, password, role in seed_users:
        if username in existing_users:
            db.execute(
                'UPDATE users SET password_hash = ? WHERE username = ?',
                (generate_password_hash(password), username)
            )
            print(f"  [auth] Password refreshed for '{username}'.")
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
    print(f"  admin      : {os.environ.get('ADMIN_PASSWORD') or _DEFAULT_ADMIN_PW}")
    print(f"  treasurer  : {os.environ.get('TREASURER_PASSWORD') or 'treasurer2005'}")
    print(f"  secretary  : {os.environ.get('SECRETARY_PASSWORD') or 'secretary2005'}")
    print(f"{'=' * 60}\n")

    db.commit()
    db.close()
    print(f"Database ({backend}) initialised successfully!")


if __name__ == '__main__':
    init_db()
