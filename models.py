"""
Database Models with Relationships
"""

from datetime import datetime, timedelta
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3 as sql
from database import get_db

class User(UserMixin):
    """User model for authentication"""
    
    def __init__(self, id, username, password_hash, role, email=None, 
                 full_name=None, phone=None, is_active=True, 
                 two_factor_secret=None, last_login=None, created_at=None):
        self.id = id
        self.username = username
        self.password_hash = password_hash
        self.role = role
        self.email = email
        self.full_name = full_name
        self.phone = phone
        self.is_active = is_active
        self.two_factor_secret = two_factor_secret
        self.last_login = last_login
        self.created_at = created_at or datetime.now()
    
    @staticmethod
    def get_by_id(user_id):
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
        if user:
            return User(
                id=user['id'],
                username=user['username'],
                password_hash=user['password_hash'],
                role=user['role'],
                email=user.get('email'),
                full_name=user.get('full_name'),
                phone=user.get('phone'),
                is_active=user.get('is_active', True),
                two_factor_secret=user.get('two_factor_secret'),
                last_login=user.get('last_login'),
                created_at=user.get('created_at')
            )
        return None
    
    @staticmethod
    def get_by_username(username):
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        if user:
            return User(
                id=user['id'],
                username=user['username'],
                password_hash=user['password_hash'],
                role=user['role'],
                email=user.get('email'),
                full_name=user.get('full_name'),
                phone=user.get('phone'),
                is_active=user.get('is_active', True),
                two_factor_secret=user.get('two_factor_secret'),
                last_login=user.get('last_login'),
                created_at=user.get('created_at')
            )
        return None
    
    def verify_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def has_role(self, role):
        return self.role == role or self.role == 'admin'
    
    def is_admin(self):
        return self.role == 'admin'
    
    def is_treasurer(self):
        return self.role == 'treasurer'
    
    def is_secretary(self):
        return self.role == 'secretary'

class Member:
    """Member model based on Bye-laws Section 4"""
    
    def __init__(self, id=None, member_number=None, first_name=None, last_name=None,
                 email=None, phone=None, address=None, occupation=None,
                 date_of_birth=None, nominee_name=None, nominee_relationship=None,
                 monthly_savings=5000, total_savings=0, shares=0, shares_value=0,
                 status='active', date_joined=None, photo_path=None,
                 card_number=None, card_status='active', card_issued_date=None,
                 card_expiry_date=None, emergency_contact_name=None,
                 emergency_contact_phone=None, next_of_kin=None,
                 bank_name=None, account_number=None, account_name=None,
                 bvn=None, nin=None):
        
        self.id = id
        self.member_number = member_number or self.generate_member_number()
        self.first_name = first_name
        self.last_name = last_name
        self.email = email
        self.phone = phone
        self.address = address
        self.occupation = occupation
        self.date_of_birth = date_of_birth
        self.nominee_name = nominee_name
        self.nominee_relationship = nominee_relationship
        self.monthly_savings = monthly_savings
        self.total_savings = total_savings
        self.shares = shares
        self.shares_value = shares_value
        self.status = status
        self.date_joined = date_joined or datetime.now()
        self.photo_path = photo_path
        self.card_number = card_number
        self.card_status = card_status
        self.card_issued_date = card_issued_date
        self.card_expiry_date = card_expiry_date or (datetime.now() + timedelta(days=365)).strftime('%Y-%m-%d')
        self.emergency_contact_name = emergency_contact_name
        self.emergency_contact_phone = emergency_contact_phone
        self.next_of_kin = next_of_kin
        self.bank_name = bank_name
        self.account_number = account_number
        self.account_name = account_name
        self.bvn = bvn
        self.nin = nin
    
    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"
    
    @property
    def age(self):
        if self.date_of_birth:
            born = datetime.strptime(self.date_of_birth, '%Y-%m-%d')
            today = datetime.now()
            return today.year - born.year - ((today.month, today.day) < (born.month, born.day))
        return None
    
    @property
    def membership_duration_days(self):
        return (datetime.now() - self.date_joined).days
    
    @property
    def membership_duration_months(self):
        return self.membership_duration_days / 30
    
    @property
    def can_apply_for_loan(self):
        """Check if member qualifies for loan (Bye-laws 9.4.1)"""
        if self.total_savings < 50000:
            return False, f"Minimum savings of ₦50,000 required. Current: ₦{self.total_savings:,.2f}"
        if self.membership_duration_months < 6:
            return False, f"Minimum 6 months membership required. Current: {int(self.membership_duration_months)} months"
        return True, "Qualified"
    
    @property
    def max_loan_amount(self):
        """Calculate maximum loan amount (2x savings)"""
        return self.total_savings * 2
    
    @property
    def outstanding_loans(self):
        db = get_db()
        total = db.execute('''
            SELECT SUM(balance) FROM loans 
            WHERE member_id = ? AND status = 'active'
        ''', (self.id,)).fetchone()[0] or 0
        return total
    
    @property
    def loan_eligibility_amount(self):
        """Calculate remaining loan eligibility"""
        return max(0, self.max_loan_amount - self.outstanding_loans)
    
    @property
    def savings_balance(self):
        db = get_db()
        total = db.execute('''
            SELECT SUM(amount) FROM savings WHERE member_id = ?
        ''', (self.id,)).fetchone()[0] or 0
        return total
    
    @property
    def dividend_earned(self, rate=0.05):
        """Calculate estimated dividend"""
        return self.total_savings * rate
    
    @property
    def card_is_valid(self):
        if not self.card_expiry_date:
            return False
        expiry = datetime.strptime(self.card_expiry_date, '%Y-%m-%d')
        return expiry > datetime.now() and self.card_status == 'active'
    
    @staticmethod
    def generate_member_number():
        """Generate unique member number"""
        import random
        import string
        year = datetime.now().year
        random_chars = ''.join(random.choices(string.digits, k=4))
        return f"OOU/{year}/{random_chars}"
    
    @staticmethod
    def get_by_id(member_id):
        db = get_db()
        member = db.execute('SELECT * FROM members WHERE id = ?', (member_id,)).fetchone()
        if member:
            return Member(**dict(member))
        return None
    
    @staticmethod
    def get_by_member_number(member_number):
        db = get_db()
        member = db.execute('SELECT * FROM members WHERE member_number = ?', (member_number,)).fetchone()
        if member:
            return Member(**dict(member))
        return None
    
    @staticmethod
    def search(query):
        db = get_db()
        members = db.execute('''
            SELECT * FROM members 
            WHERE first_name LIKE ? OR last_name LIKE ? OR email LIKE ? OR member_number LIKE ?
            ORDER BY last_name, first_name
        ''', (f'%{query}%', f'%{query}%', f'%{query}%', f'%{query}%')).fetchall()
        return [Member(**dict(m)) for m in members]
    
    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            'id': self.id,
            'member_number': self.member_number,
            'full_name': self.full_name,
            'first_name': self.first_name,
            'last_name': self.last_name,
            'email': self.email,
            'phone': self.phone,
            'total_savings': float(self.total_savings),
            'max_loan': float(self.max_loan_amount),
            'status': self.status,
            'date_joined': self.date_joined.strftime('%Y-%m-%d') if hasattr(self.date_joined, 'strftime') else self.date_joined
        }

class Savings:
    """Savings model based on Bye-laws Section 8"""
    
    def __init__(self, id=None, member_id=None, amount=0, month=None,
                 late_fee=0, payment_method='cash', reference=None,
                 receipt_number=None, notes=None, created_by=None,
                 date=None, verified_by=None, verified_at=None):
        
        self.id = id
        self.member_id = member_id
        self.amount = amount
        self.month = month
        self.late_fee = late_fee
        self.payment_method = payment_method
        self.reference = reference or self.generate_reference()
        self.receipt_number = receipt_number or self.generate_receipt_number()
        self.notes = notes
        self.created_by = created_by
        self.date = date or datetime.now()
        self.verified_by = verified_by
        self.verified_at = verified_at
    
    @property
    def total_paid(self):
        return self.amount + self.late_fee
    
    @property
    def is_late(self):
        return self.late_fee > 0
    
    @property
    def is_verified(self):
        return self.verified_at is not None
    
    @staticmethod
    def generate_reference():
        import uuid
        return f"SAV-{uuid.uuid4().hex[:8].upper()}"
    
    @staticmethod
    def generate_receipt_number():
        import random
        year = datetime.now().year
        month = datetime.now().month
        random_num = random.randint(1000, 9999)
        return f"RCPT/{year}/{month}/{random_num}"
    
    @staticmethod
    def calculate_late_fee(amount, due_day=10, late_fee_percent=10):
        """Calculate late fee based on payment date"""
        today = datetime.now()
        if today.day > due_day:
            return amount * (late_fee_percent / 100)
        return 0

class Loan:
    """Loan model based on Bye-laws Section 9"""
    
    def __init__(self, id=None, loan_number=None, member_id=None, amount=0,
                 purpose=None, description=None, tenure=12, interest_rate=11,
                 total_repayment=None, balance=None, status='pending',
                 application_fee=0, insurance_premium=0, disbursed_amount=None,
                 disbursement_date=None, first_payment_date=None,
                 next_payment_date=None, approved_by=None, approved_at=None,
                 reviewed_by=None, reviewed_at=None, rejection_reason=None,
                 completed_at=None, defaulted=False, notes=None,
                 date_applied=None):
        
        self.id = id
        self.loan_number = loan_number or self.generate_loan_number()
        self.member_id = member_id
        self.amount = amount
        self.purpose = purpose
        self.description = description
        self.tenure = tenure
        self.interest_rate = interest_rate
        self.total_repayment = total_repayment or self.calculate_total_repayment()
        self.balance = balance or self.total_repayment
        self.status = status
        self.application_fee = application_fee or amount * 0.01
        self.insurance_premium = insurance_premium or amount * 0.01
        self.disbursed_amount = disbursed_amount or (amount - self.application_fee - self.insurance_premium)
        self.disbursement_date = disbursement_date
        self.first_payment_date = first_payment_date
        self.next_payment_date = next_payment_date
        self.approved_by = approved_by
        self.approved_at = approved_at
        self.reviewed_by = reviewed_by
        self.reviewed_at = reviewed_at
        self.rejection_reason = rejection_reason
        self.completed_at = completed_at
        self.defaulted = defaulted
        self.notes = notes
        self.date_applied = date_applied or datetime.now()
    
    @property
    def monthly_payment(self):
        return self.total_repayment / self.tenure
    
    @property
    def total_interest(self):
        return self.total_repayment - self.amount
    
    @property
    def effective_interest_rate(self):
        return (self.total_interest / self.amount) * 100
    
    @property
    def progress_percentage(self):
        if self.status == 'completed':
            return 100
        if self.status == 'active':
            paid = self.total_repayment - self.balance
            return (paid / self.total_repayment) * 100
        return 0
    
    @property
    def payments_made(self):
        db = get_db()
        count = db.execute('''
            SELECT COUNT(*) FROM repayments WHERE loan_id = ?
        ''', (self.id,)).fetchone()[0]
        return count or 0
    
    @property
    def payments_remaining(self):
        return self.tenure - self.payments_made
    
    @property
    def days_since_disbursement(self):
        if self.disbursement_date:
            return (datetime.now() - self.disbursement_date).days
        return None
    
    @property
    def is_overdue(self):
        if self.next_payment_date and self.status == 'active':
            return datetime.now() > self.next_payment_date
        return False
    
    @property
    def days_overdue(self):
        if self.is_overdue and self.next_payment_date:
            return (datetime.now() - self.next_payment_date).days
        return 0
    
    def calculate_total_repayment(self):
        """Calculate total repayment with interest"""
        monthly_interest = (self.interest_rate / 100) / 12
        total_interest = self.amount * monthly_interest * self.tenure
        return self.amount + total_interest
    
    def calculate_amortization_schedule(self):
        """Generate loan amortization schedule"""
        schedule = []
        monthly_payment = self.monthly_payment
        balance = self.amount
        monthly_rate = (self.interest_rate / 100) / 12
        
        for month in range(1, self.tenure + 1):
            interest = balance * monthly_rate
            principal = monthly_payment - interest
            balance -= principal
            
            schedule.append({
                'month': month,
                'payment': monthly_payment,
                'principal': principal,
                'interest': interest,
                'balance': max(0, balance)
            })
        
        return schedule
    
    @staticmethod
    def generate_loan_number():
        import random
        year = datetime.now().year
        random_num = random.randint(10000, 99999)
        return f"LOAN/{year}/{random_num}"
    
    def to_dict(self):
        return {
            'id': self.id,
            'loan_number': self.loan_number,
            'amount': float(self.amount),
            'balance': float(self.balance),
            'monthly_payment': float(self.monthly_payment),
            'status': self.status,
            'progress': self.progress_percentage
        }

class Repayment:
    """Loan repayment model"""
    
    def __init__(self, id=None, repayment_number=None, loan_id=None, amount=0,
                 principal_paid=0, interest_paid=0, penalty_paid=0,
                 payment_method='cash', reference=None, receipt_number=None,
                 transaction_id=None, notes=None, received_by=None,
                 verified_by=None, verified_at=None, date=None):
        
        self.id = id
        self.repayment_number = repayment_number or self.generate_repayment_number()
        self.loan_id = loan_id
        self.amount = amount
        self.principal_paid = principal_paid
        self.interest_paid = interest_paid
        self.penalty_paid = penalty_paid
        self.payment_method = payment_method
        self.reference = reference
        self.receipt_number = receipt_number or self.generate_receipt_number()
        self.transaction_id = transaction_id
        self.notes = notes
        self.received_by = received_by
        self.verified_by = verified_by
        self.verified_at = verified_at
        self.date = date or datetime.now()
    
    @property
    def allocation_summary(self):
        return {
            'principal': float(self.principal_paid),
            'interest': float(self.interest_paid),
            'penalty': float(self.penalty_paid),
            'total': float(self.amount)
        }
    
    @staticmethod
    def generate_repayment_number():
        import random
        year = datetime.now().year
        month = datetime.now().month
        day = datetime.now().day
        random_num = random.randint(1000, 9999)
        return f"PAY/{year}{month:02d}{day:02d}/{random_num}"
    
    @staticmethod
    def generate_receipt_number():
        import random
        year = datetime.now().year
        month = datetime.now().month
        random_num = random.randint(10000, 99999)
        return f"REC/{year}/{month}/{random_num}"

class Investment:
    """Investment model based on Bye-laws 8.8"""
    
    INVESTMENT_TYPES = {
        'fixed_deposit': 'Fixed/Term Deposit',
        'certificate': 'Investment Certificate',
        'government': 'Government Securities',
        'shares': 'Shares in Public Liability Company',
        'real_estate': 'Real Estate',
        'bonds': 'Bonds',
        'treasury_bills': 'Treasury Bills',
        'mutual_funds': 'Mutual Funds',
        'other': 'Other'
    }
    
    def __init__(self, id=None, investment_number=None, name=None, amount=0,
                 investment_type=None, description=None, institution=None,
                 interest_rate=None, return_rate=None, risk_level='medium',
                 start_date=None, maturity_date=None, duration_days=None,
                 expected_return=None, actual_return=None, current_value=None,
                 approval_status='pending', approved_by=None, approved_at=None,
                 documents=None, notes=None, created_by=None, date=None):
        
        self.id = id
        self.investment_number = investment_number or self.generate_investment_number()
        self.name = name
        self.amount = amount
        self.investment_type = investment_type
        self.description = description
        self.institution = institution
        self.interest_rate = interest_rate
        self.return_rate = return_rate
        self.risk_level = risk_level
        self.start_date = start_date or datetime.now()
        self.maturity_date = maturity_date
        self.duration_days = duration_days or self.calculate_duration()
        self.expected_return = expected_return
        self.actual_return = actual_return
        self.current_value = current_value or amount
        self.approval_status = approval_status
        self.approved_by = approved_by
        self.approved_at = approved_at
        self.documents = documents
        self.notes = notes
        self.created_by = created_by
        self.date = date or datetime.now()
    
    @property
    def days_remaining(self):
        if self.maturity_date:
            return (self.maturity_date - datetime.now()).days
        return None
    
    @property
    def is_matured(self):
        if self.maturity_date:
            return datetime.now() >= self.maturity_date
        return False
    
    @property
    def roi(self):
        if self.amount > 0:
            return ((self.current_value - self.amount) / self.amount) * 100
        return 0
    
    @property
    def is_major_investment(self, threshold=100000):
        """Check if investment is major (Bye-laws 8.8.5)"""
        return self.investment_type in ['shares', 'real_estate'] and self.amount > threshold
    
    def calculate_duration(self):
        if self.start_date and self.maturity_date:
            return (self.maturity_date - self.start_date).days
        return None
    
    @staticmethod
    def generate_investment_number():
        import random
        year = datetime.now().year
        random_num = random.randint(1000, 9999)
        return f"INV/{year}/{random_num}"

class Transaction:
    """General transaction model"""
    
    TRANSACTION_TYPES = {
        'savings': 'Savings Deposit',
        'loan_disbursement': 'Loan Disbursement',
        'loan_repayment': 'Loan Repayment',
        'investment': 'Investment',
        'dividend': 'Dividend Payment',
        'fee': 'Fee Payment',
        'penalty': 'Penalty',
        'refund': 'Refund',
        'transfer': 'Transfer'
    }
    
    def __init__(self, id=None, transaction_number=None, transaction_type=None,
                 member_id=None, amount=0, description=None, reference=None,
                 status='completed', payment_method=None, bank_reference=None,
                 receipt_number=None, created_by=None, verified_by=None,
                 verified_at=None, date=None):
        
        self.id = id
        self.transaction_number = transaction_number or self.generate_transaction_number()
        self.transaction_type = transaction_type
        self.member_id = member_id
        self.amount = amount
        self.description = description
        self.reference = reference
        self.status = status
        self.payment_method = payment_method
        self.bank_reference = bank_reference
        self.receipt_number = receipt_number
        self.created_by = created_by
        self.verified_by = verified_by
        self.verified_at = verified_at
        self.date = date or datetime.now()
    
    @staticmethod
    def generate_transaction_number():
        import random
        year = datetime.now().year
        month = datetime.now().month
        day = datetime.now().day
        random_num = random.randint(10000, 99999)
        return f"TXN/{year}{month:02d}{day:02d}/{random_num}"

class Notification:
    """Notification model"""
    
    def __init__(self, id=None, user_id=None, title=None, message=None,
                 notification_type='info', is_read=False, action_url=None,
                 created_at=None, read_at=None):
        
        self.id = id
        self.user_id = user_id
        self.title = title
        self.message = message
        self.notification_type = notification_type
        self.is_read = is_read
        self.action_url = action_url
        self.created_at = created_at or datetime.now()
        self.read_at = read_at
    
    def mark_as_read(self):
        self.is_read = True
        self.read_at = datetime.now()
        db = get_db()
        db.execute('UPDATE notifications SET is_read = 1, read_at = ? WHERE id = ?',
                  (self.read_at, self.id))
        db.commit()

class AuditLog:
    """Audit trail model"""
    
    def __init__(self, id=None, user_id=None, username=None, action=None,
                 module=None, description=None, ip_address=None, user_agent=None,
                 timestamp=None, data=None):
        
        self.id = id
        self.user_id = user_id
        self.username = username
        self.action = action
        self.module = module
        self.description = description
        self.ip_address = ip_address
        self.user_agent = user_agent
        self.timestamp = timestamp or datetime.now()
        self.data = data
    
    @staticmethod
    def log(user_id, username, action, module, description, ip_address, user_agent=None, data=None):
        db = get_db()
        db.execute('''
            INSERT INTO audit_log (user_id, username, action, module, description, ip_address, user_agent, data, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, username, action, module, description, ip_address, user_agent, data, datetime.now()))
        db.commit()