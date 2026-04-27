import sqlite3
import os
from datetime import datetime
from werkzeug.security import generate_password_hash

DATABASE = 'cooperative.db'

def get_db():
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    return db

def init_db():
    db = get_db()
    
    # Users table
    db.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            full_name TEXT,
            email TEXT,
            phone TEXT,
            is_active INTEGER DEFAULT 1,
            two_factor_secret TEXT,
            last_login TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Members table
    db.execute('''
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
    ''')
    
    # Savings table
    db.execute('''
        CREATE TABLE IF NOT EXISTS savings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            month TEXT NOT NULL,
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
    ''')
    
    # Loans table
    db.execute('''
        CREATE TABLE IF NOT EXISTS loans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            loan_number TEXT UNIQUE,
            member_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            purpose TEXT,
            description TEXT,
            tenure INTEGER,
            interest_rate REAL,
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
    ''')
    
    # Repayments table
    db.execute('''
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
    ''')
    
    # Investments table
    db.execute('''
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
    ''')
    
    # Honorarium table
    db.execute('''
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
    ''')
    
    # Expenses table
    db.execute('''
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
    ''')
    
    # Revenue table
    db.execute('''
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
    ''')
    
    # Settings table
    db.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE NOT NULL,
            value TEXT NOT NULL,
            description TEXT
        )
    ''')
    
    # Notifications table
    db.execute('''
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
    ''')
    
    # Audit log table
    db.execute('''
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
    ''')
    
    # Insert default settings (PostgreSQL‑compatible: ON CONFLICT)
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
        ('entrance_fee', '2000', 'Entrance fee'),
        ('reentry_fee', '5000', 'Re-entry fee'),
        ('loan_application_fee', '1000', 'Loan application fee'),
        ('statement_fee', '500', 'Statement request fee')
    ]
    
    for key, value, desc in default_settings:
        try:
            # Works on both SQLite (3.24+) and PostgreSQL
            db.execute('''
                INSERT INTO settings (key, value, description)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO NOTHING
            ''', (key, value, desc))
        except Exception as e:
            print(f"Error inserting setting {key}: {e}")
    
    # Create default users using ON CONFLICT
    users = [
        ('admin', generate_password_hash('admin123'), 'admin'),
        ('treasurer', generate_password_hash('treasurer123'), 'treasurer'),
        ('secretary', generate_password_hash('secretary123'), 'secretary')
    ]
    
    for username, pwd_hash, role in users:
        try:
            db.execute('''
                INSERT INTO users (username, password_hash, role, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(username) DO NOTHING
            ''', (username, pwd_hash, role, datetime.now()))
        except Exception as e:
            print(f"Error creating user {username}: {e}")
    
    db.commit()
    db.close()
    print("Database initialized successfully with all tables!")

if __name__ == '__main__':
    init_db()