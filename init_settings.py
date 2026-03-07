import sqlite3

print("Initializing settings...")

conn = sqlite3.connect('cooperative.db')
cursor = conn.cursor()

# Default settings
settings = [
    ('coop_name', 'OOU Acctg 2005 Alumni CMS'),
    ('reg_number', 'CMS/2005/001'),
    ('address', ''),
    ('phone', ''),
    ('email', ''),
    ('fy_start', '1'),
    ('currency', 'NGN'),
    ('date_format', 'Y-m-d'),
    ('session_timeout', '30'),
    ('maintenance_mode', '0'),
    ('min_savings', '5000'),
    ('savings_due_day', '10'),
    ('late_fee_percent', '10'),
    ('min_deposit_period', '90'),
    ('member_deposit_rate', '9'),
    ('nonmember_deposit_rate', '7'),
    ('dividend_rate', '50'),
    ('min_membership_months', '6'),
    ('min_savings_for_loan', '50000'),
    ('loan_multiplier', '2'),
    ('max_tenure_months', '18'),
    ('max_interest_rate', '11'),
    ('insurance_rate', '1'),
    ('guarantors_required', '2'),
    ('default_penalty_rate', '20'),
    ('interest_regular', '11'),
    ('interest_housing', '9'),
    ('interest_emergency', '10'),
    ('interest_asset', '10'),
    ('entrance_fee', '2000'),
    ('reentry_fee', '5000'),
    ('loan_application_fee', '1000'),
    ('statement_fee', '500')
]

for key, value in settings:
    cursor.execute('''
        INSERT OR REPLACE INTO settings (key, value, description)
        VALUES (?, ?, ?)
    ''', (key, value, f'Setting for {key}'))

conn.commit()
conn.close()
print("✅ Settings initialized successfully!")
print("You can now run the application.")