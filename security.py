"""
Enterprise Security Features
"""

import pyotp
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

class SecurityManager:
    def __init__(self):
        self.failed_attempts = {}
        self.locked_accounts = {}
    
    def generate_2fa_secret(self):
        """Generate TOTP secret for 2FA"""
        return pyotp.random_base32()
    
    def verify_2fa(self, secret, token):
        """Verify 2FA token"""
        totp = pyotp.TOTP(secret)
        return totp.verify(token)
    
    def generate_backup_codes(self, count=10):
        """Generate backup codes for 2FA recovery"""
        codes = []
        for _ in range(count):
            code = secrets.token_hex(4).upper()
            hashed = hashlib.sha256(code.encode()).hexdigest()
            codes.append({
                'code': code,
                'hashed': hashed,
                'used': False
            })
        return codes
    
    def check_rate_limit(self, ip_address, action):
        """Check rate limiting for API calls"""
        key = f"{ip_address}:{action}"
        
        if key in self.failed_attempts:
            attempts, first_attempt = self.failed_attempts[key]
            
            # Reset after 15 minutes
            if datetime.now() - first_attempt > timedelta(minutes=15):
                self.failed_attempts[key] = (1, datetime.now())
                return True
            
            if attempts >= 5:
                return False
            
            self.failed_attempts[key] = (attempts + 1, first_attempt)
        else:
            self.failed_attempts[key] = (1, datetime.now())
        
        return True
    
    def log_audit(self, user_id, action, details, ip_address):
        """Log all actions for audit trail"""
        # Store in database
        db.execute('''
            INSERT INTO audit_log (user_id, action, details, ip_address, timestamp)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, action, details, ip_address, datetime.now()))
        db.commit()
    
    def check_session_timeout(self, user):
        """Check if session has timed out"""
        if user.last_activity:
            inactive = datetime.now() - user.last_activity
            if inactive > timedelta(minutes=30):
                return False
        return True

# Activity Monitor
class ActivityMonitor:
    def __init__(self):
        self.suspicious_patterns = []
    
    def detect_suspicious_activity(self, user_id, action, ip_address):
        """Detect potentially fraudulent activity"""
        alerts = []
        
        # Check for multiple failed logins
        # Check for unusual transaction amounts
        # Check for login from new location
        # Check for rapid transactions
        
        if alerts:
            self.trigger_alert(user_id, alerts, ip_address)
            return False
        
        return True
    
    def trigger_alert(self, user_id, alerts, ip_address):
        """Send security alert"""
        # Send email
        # Send SMS
        # Log to security system
        # Lock account if necessary
        pass