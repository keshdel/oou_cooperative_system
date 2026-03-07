import sqlite3

# Connect to database
conn = sqlite3.connect('cooperative.db')
cursor = conn.cursor()

# Default settings to insert
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

# Insert settings
for key, value, desc in default_settings:
    try:
        cursor.execute('''
            INSERT OR REPLACE INTO settings (key, value, description)
            VALUES (?, ?, ?)
        ''', (key, value, desc))
        print(f"Added: {key} = {value}")
    except Exception as e:
        print(f"Error adding {key}: {e}")

conn.commit()
conn.close()
print("\n✅ Settings fixed! Restart the application.")