import json
import os
import time
import unittest
from io import BytesIO
from unittest.mock import patch
from urllib.parse import urlparse

import jwt
from werkzeug.security import check_password_hash, generate_password_hash

TEST_DB = os.path.abspath('.test-hardening-features.db')
os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-hardening-regression')
os.environ.setdefault('ADMIN_PASSWORD', 'TestAdmin123')
os.environ.setdefault('FLASK_DEBUG', '1')
os.environ.setdefault('FIELD_ENCRYPTION_KEY', '05SmPJhNFMKwg9NysnBdQjKtqn3VwWDl1IiPIMAg2as=')
os.environ.pop('DATABASE_URL', None)
os.environ['SQLITE_DB_PATH'] = TEST_DB

try:
    os.remove(TEST_DB)
except FileNotFoundError:
    pass

import app as app_module  # noqa: E402
from database import get_db  # noqa: E402
from crypto import decrypt_field, is_encrypted  # noqa: E402
from ledger import backfill_from_transactions, ledger_reconciliation  # noqa: E402
from mobile_api import JWT_AUDIENCE  # noqa: E402
from reports_engine import income_statement  # noqa: E402
from security import generate_compliant_password, validate_password_strength  # noqa: E402
from utils import clear_login_attempts  # noqa: E402


class HardeningFeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app_module.app
        cls.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)

    def setUp(self):
        self.client = self.app.test_client()

    def login_admin(self):
        response = self.client.post(
            '/login',
            data={'username': 'admin', 'password': 'TestAdmin123'},
            follow_redirects=False,
        )
        self.assertIn(response.status_code, (302, 303))

    def test_idle_session_timeout_logs_user_out_and_audits(self):
        self.login_admin()
        with self.client.session_transaction() as sess:
            sess['last_activity_at'] = time.time() - (self.app.config['IDLE_TIMEOUT_SECONDS'] + 5)

        response = self.client.get('/dashboard', follow_redirects=False)
        self.assertIn(response.status_code, (302, 303))
        self.assertIn('/login', response.headers.get('Location', ''))

        with self.app.app_context():
            db = get_db()
            row = db.execute(
                "SELECT action, module, description FROM audit_log WHERE action = 'SESSION_TIMEOUT' ORDER BY id DESC"
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row['module'], 'auth')
            self.assertIn('inactivity', row['description'].lower())

    def test_authenticated_pages_have_security_headers(self):
        self.login_admin()
        response = self.client.get('/dashboard')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get('X-Frame-Options'), 'DENY')
        self.assertEqual(response.headers.get('X-Content-Type-Options'), 'nosniff')
        self.assertIn('no-store', response.headers.get('Cache-Control', ''))

    def create_member(self):
        with self.app.app_context():
            db = get_db()
            existing = db.execute(
                "SELECT * FROM members WHERE member_number = 'OOU/TEST/0001'"
            ).fetchone()
            if existing:
                return existing['id']
            db.execute('''
                INSERT INTO members
                    (member_number, employee_id, first_name, last_name, email,
                     phone, status, monthly_savings, total_savings, date_joined)
                VALUES
                    ('OOU/TEST/0001', 'EMP001', 'Ada', 'Audit',
                     'ada.audit@example.com', '08000000001', 'active',
                     15000, 0, '2024-01-01')
            ''')
            db.commit()
            return db.execute(
                "SELECT id FROM members WHERE member_number = 'OOU/TEST/0001'"
            ).fetchone()['id']

    def create_member_user(self, member_id, email='ada.audit@example.com'):
        with self.app.app_context():
            db = get_db()
            db.execute('''
                INSERT INTO users (username, password_hash, role, full_name, email, is_active, must_change_password)
                VALUES (?, ?, 'member', 'Ada Audit', ?, 1, 0)
                ON CONFLICT(username) DO UPDATE SET
                    password_hash = excluded.password_hash,
                    is_active = 1,
                    must_change_password = 0
            ''', (email, generate_password_hash('MemberPass1!'), email))
            db.commit()

    def create_non_staff_member(self):
        email = 'non.staff.loan@example.com'
        with self.app.app_context():
            db = get_db()
            existing = db.execute(
                "SELECT * FROM members WHERE member_number = 'OOU/TEST/N001'"
            ).fetchone()
            if existing:
                return existing['id'], email
            db.execute('''
                INSERT INTO members
                    (member_number, employee_id, first_name, last_name, email,
                     phone, status, monthly_savings, total_savings, date_joined)
                VALUES
                    ('OOU/TEST/N001', NULL, 'Nora', 'Nonstaff',
                     ?, '08000000011', 'active', 15000, 0, '2024-01-01')
            ''', (email,))
            db.commit()
            return db.execute(
                "SELECT id FROM members WHERE member_number = 'OOU/TEST/N001'"
            ).fetchone()['id'], email

    def create_guarantor_member(self, number, email, first_name):
        with self.app.app_context():
            db = get_db()
            existing = db.execute('SELECT id FROM members WHERE member_number = ?', (number,)).fetchone()
            if existing:
                return existing['id']
            db.execute('''
                INSERT INTO members
                    (member_number, employee_id, first_name, last_name, email,
                     phone, status, monthly_savings, total_savings, date_joined)
                VALUES (?, ?, ?, 'Guarantor', ?, '08000000991', 'active', 15000, 100000, '2024-01-01')
            ''', (number, number.replace('/', ''), first_name, email))
            db.commit()
            return db.execute('SELECT id FROM members WHERE member_number = ?', (number,)).fetchone()['id']

    def fund_member_savings(self, member_id, amount=100000):
        with self.app.app_context():
            db = get_db()
            receipt = f'SAV/LOANAPP/{member_id}'
            if db.execute('SELECT id FROM savings WHERE receipt_number = ?', (receipt,)).fetchone():
                backfill_from_transactions(db)
                db.commit()
                return
            db.execute('''
                INSERT INTO savings
                    (member_id, amount, month, payment_type, payment_method, receipt_number, date)
                VALUES (?, ?, '2026-07', 'monthly', 'cash', ?, '2026-07-01')
            ''', (member_id, amount, receipt))
            backfill_from_transactions(db)
            db.commit()

    def login_member(self, email='ada.audit@example.com'):
        response = self.client.post(
            '/login',
            data={'username': email, 'password': 'MemberPass1!'},
            follow_redirects=False,
        )
        self.assertIn(response.status_code, (302, 303))

    def test_support_routes_are_disabled_by_default(self):
        for path in ('/setup', '/debug-auth', '/emergency-reset'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 404, path)

    def test_support_diagnostics_remain_disabled_when_flag_enabled(self):
        with patch.dict(os.environ, {'ENABLE_SUPPORT_ROUTES': '1', 'RESET_TOKEN': 'test-reset-token'}):
            for path in ('/setup', '/debug-auth'):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404, path)

    def test_emergency_reset_requires_post_and_non_url_token(self):
        support_env = {
            'ENABLE_SUPPORT_ROUTES': '1',
            'RESET_TOKEN': 'test-reset-token',
            'ADMIN_PASSWORD': 'TestAdmin123',
        }
        with patch.dict(os.environ, support_env):
            get_response = self.client.get('/emergency-reset?token=test-reset-token')
            self.assertEqual(get_response.status_code, 405)

            query_token_response = self.client.post('/emergency-reset?token=test-reset-token')
            self.assertEqual(query_token_response.status_code, 403)

            form_token_response = self.client.post(
                '/emergency-reset',
                data={'token': 'test-reset-token'},
            )
            self.assertEqual(form_token_response.status_code, 200)
            self.assertNotIn(b'TestAdmin123', form_token_response.data)

    def test_mobile_repayment_is_fail_closed(self):
        clear_login_attempts('mobile:127.0.0.1')
        login = self.client.post(
            '/api/mobile/login',
            json={'username': 'admin', 'password': 'TestAdmin123'},
        )
        self.assertEqual(login.status_code, 200)
        token = login.get_json()['token']
        response = self.client.post(
            '/api/mobile/pay',
            json={'amount': 1000},
            headers={'Authorization': f'Bearer {token}'},
        )
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.get_json()['success'])

    def test_mobile_login_is_rate_limited_and_cleared_on_success(self):
        login_key = 'mobile:203.0.113.10'
        clear_login_attempts(login_key)
        environ = {'REMOTE_ADDR': '203.0.113.10'}

        for _ in range(5):
            response = self.client.post(
                '/api/mobile/login',
                json={'username': 'admin', 'password': 'wrong-password'},
                environ_overrides=environ,
            )
            self.assertEqual(response.status_code, 401)

        blocked = self.client.post(
            '/api/mobile/login',
            json={'username': 'admin', 'password': 'TestAdmin123'},
            environ_overrides=environ,
        )
        self.assertEqual(blocked.status_code, 429)

        clear_login_attempts(login_key)
        success = self.client.post(
            '/api/mobile/login',
            json={'username': 'admin', 'password': 'TestAdmin123'},
            environ_overrides=environ,
        )
        self.assertEqual(success.status_code, 200)

    def test_mobile_token_requires_expected_audience(self):
        clear_login_attempts('mobile:127.0.0.1')
        response = self.client.post(
            '/api/mobile/login',
            json={'username': 'admin', 'password': 'TestAdmin123'},
        )
        self.assertEqual(response.status_code, 200)
        token = response.get_json()['token']

        with self.assertRaises(jwt.InvalidAudienceError):
            jwt.decode(token, self.app.config['SECRET_KEY'], algorithms=['HS256'])

        payload = jwt.decode(
            token,
            self.app.config['SECRET_KEY'],
            algorithms=['HS256'],
            audience=JWT_AUDIENCE,
        )
        self.assertEqual(payload['username'], 'admin')

    def test_admin_configured_password_policy_is_enforced_by_helper(self):
        with self.app.app_context():
            db = get_db()
            for key, value in (
                ('password_min_length', '10'),
                ('password_require_upper', '1'),
                ('password_require_lower', '1'),
                ('password_require_number', '1'),
                ('password_require_special', '1'),
            ):
                db.execute(
                    'INSERT INTO settings (key, value, description) VALUES (?, ?, ?) '
                    'ON CONFLICT(key) DO UPDATE SET value = excluded.value',
                    (key, value, f'Test {key}'),
                )
            db.commit()

            ok, errors = validate_password_strength('Password1', db)
            self.assertFalse(ok)
            self.assertIn('special character', ' '.join(errors))

            generated = generate_compliant_password(db)
            ok, errors = validate_password_strength(generated, db)
            self.assertTrue(ok, errors)

    def test_new_member_gets_portal_user_and_onboarding_email(self):
        self.login_admin()
        email = 'new.member.onboarding@example.com'
        with patch('blueprints.members.send_welcome_email') as welcome_email, \
                patch('blueprints.members.send_member_onboarding_email') as onboarding_email:
            response = self.client.post(
                '/members/add',
                data={
                    'first_name': 'New',
                    'last_name': 'Member',
                    'email': email,
                    'phone': '08000000999',
                    'monthly_savings': '12000',
                },
                follow_redirects=False,
            )
        self.assertIn(response.status_code, (302, 303))
        welcome_email.assert_called_once()
        onboarding_email.assert_called_once()

        with self.app.app_context():
            db = get_db()
            user = db.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
            member = db.execute('SELECT * FROM members WHERE email = ?', (email,)).fetchone()
            self.assertIsNotNone(user)
            self.assertIsNotNone(member)
            self.assertEqual(user['role'], 'member')
            self.assertEqual(user['username'], email)
            self.assertEqual(user['must_change_password'], 1)
            setup_url = onboarding_email.call_args.args[3]
            self.assertIn('/setup-password/', setup_url)
            self.assertNotIn('password', setup_url.split('/setup-password/', 1)[-1].lower())

            token = urlparse(setup_url).path.rsplit('/', 1)[-1]
            token_row = db.execute('SELECT * FROM account_setup_tokens WHERE user_id = ?', (user['id'],)).fetchone()
            self.assertIsNotNone(token_row)
            self.assertIsNone(token_row['used_at'])

        setup = self.client.post(
            f'/setup-password/{token}',
            data={'new_password': 'SetupPass1!', 'confirm_password': 'SetupPass1!'},
            follow_redirects=False,
        )
        self.assertIn(setup.status_code, (302, 303))

        with self.app.app_context():
            db = get_db()
            user = db.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
            token_row = db.execute('SELECT * FROM account_setup_tokens WHERE user_id = ?', (user['id'],)).fetchone()
            self.assertEqual(user['must_change_password'], 0)
            self.assertTrue(check_password_hash(user['password_hash'], 'SetupPass1!'))
            self.assertIsNotNone(token_row['used_at'])

        reused = self.client.get(f'/setup-password/{token}', follow_redirects=False)
        self.assertIn(reused.status_code, (302, 303))

    def test_member_loan_application_requires_due_diligence_acknowledgements(self):
        member_id = self.create_member()
        self.create_member_user(member_id)
        self.fund_member_savings(member_id)
        guarantor_1 = self.create_guarantor_member('OOU/TEST/G001', 'g1@example.com', 'Grace')
        guarantor_2 = self.create_guarantor_member('OOU/TEST/G002', 'g2@example.com', 'George')
        self.login_member()

        response = self.client.post(
            '/apply-loan-member',
            data={
                'amount': '50000',
                'purpose': 'Regular',
                'tenure': '6',
                'payment_collateral_type': 'standing_order',
                'guarantors': [str(guarantor_1), str(guarantor_2)],
                'accept_terms': '1',
                'data_processing_consent': '1',
                'repayment_schedule_accepted': '1',
                'signature_name': 'Ada Audit',
            },
            follow_redirects=False,
        )
        self.assertIn(response.status_code, (302, 303))

        with self.app.app_context():
            db = get_db()
            loan = db.execute(
                'SELECT * FROM loans WHERE member_id = ? ORDER BY id DESC',
                (member_id,),
            ).fetchone()
            self.assertIsNone(loan)

    def test_member_loan_application_stores_consent_and_schedule_snapshot(self):
        member_id = self.create_member()
        self.create_member_user(member_id)
        self.fund_member_savings(member_id)
        guarantor_1 = self.create_guarantor_member('OOU/TEST/G003', 'g3@example.com', 'Gina')
        guarantor_2 = self.create_guarantor_member('OOU/TEST/G004', 'g4@example.com', 'Gabriel')
        self.login_member()

        response = self.client.post(
            '/apply-loan-member',
            data={
                'amount': '50000',
                'purpose': 'Regular',
                'tenure': '6',
                'payment_collateral_type': 'standing_order',
                'guarantors': [str(guarantor_1), str(guarantor_2)],
                'hr_affordability_consent': '1',
                'data_processing_consent': '1',
                'repayment_schedule_accepted': '1',
                'accept_terms': '1',
                'signature_name': 'Ada Audit',
            },
            follow_redirects=False,
            environ_overrides={'REMOTE_ADDR': '203.0.113.44'},
        )
        self.assertIn(response.status_code, (302, 303))

        with self.app.app_context():
            db = get_db()
            loan = db.execute(
                'SELECT * FROM loans WHERE member_id = ? ORDER BY id DESC',
                (member_id,),
            ).fetchone()
            self.assertIsNotNone(loan)
            self.assertEqual(loan['terms_accepted'], 1)
            self.assertEqual(loan['data_processing_consent'], 1)
            self.assertEqual(loan['credit_check_consent'], 0)
            self.assertEqual(loan['repayment_schedule_accepted'], 1)
            self.assertEqual(loan['bank_statement_status'], 'not_required')
            self.assertEqual(loan['payment_collateral_type'], 'standing_order')
            self.assertEqual(loan['payment_collateral_status'], 'pending')
            self.assertEqual(loan['consent_ip'], '203.0.113.44')
            self.assertEqual(loan['loan_applicant_type'], 'staff')
            self.assertEqual(loan['hr_affordability_consent'], 1)
            self.assertEqual(loan['hr_affordability_status'], 'pending')

            snapshot = json.loads(loan['repayment_schedule_snapshot'])
            self.assertEqual(snapshot['principal'], 50000)
            self.assertEqual(snapshot['purpose'], 'Regular')
            self.assertEqual(snapshot['tenure'], 6)
            self.assertEqual(len(snapshot['schedule']), 6)

    def test_non_staff_loan_application_still_requires_bank_and_credit_acknowledgements(self):
        member_id, email = self.create_non_staff_member()
        self.create_member_user(member_id, email=email)
        self.fund_member_savings(member_id)
        guarantor_1 = self.create_guarantor_member('OOU/TEST/G005', 'g5@example.com', 'Gideon')
        guarantor_2 = self.create_guarantor_member('OOU/TEST/G006', 'g6@example.com', 'Gloria')
        self.login_member(email=email)

        response = self.client.post(
            '/apply-loan-member',
            data={
                'amount': '50000',
                'purpose': 'Regular',
                'tenure': '6',
                'payment_collateral_type': 'post_dated_cheques',
                'guarantors': [str(guarantor_1), str(guarantor_2)],
                'data_processing_consent': '1',
                'repayment_schedule_accepted': '1',
                'accept_terms': '1',
                'signature_name': 'Nora Nonstaff',
            },
            follow_redirects=False,
        )
        self.assertIn(response.status_code, (302, 303))

        with self.app.app_context():
            db = get_db()
            loan = db.execute(
                'SELECT * FROM loans WHERE member_id = ? ORDER BY id DESC',
                (member_id,),
            ).fetchone()
            self.assertIsNone(loan)

    def test_final_loan_approval_requires_completed_due_diligence(self):
        self.login_admin()
        with self.app.app_context():
            db = get_db()
            db.execute('''
                INSERT INTO members
                    (member_number, employee_id, first_name, last_name, email,
                     phone, status, monthly_savings, total_savings, date_joined)
                VALUES
                    ('OOU/TEST/DUE1', 'EMP-DUE1', 'Dara', 'Due',
                     'dara.due@example.com', '08000000021', 'active',
                     15000, 100000, '2024-01-01')
            ''')
            member_id = db.execute(
                "SELECT id FROM members WHERE member_number = 'OOU/TEST/DUE1'"
            ).fetchone()['id']
            db.execute('''
                INSERT INTO loans
                    (loan_number, member_id, amount, purpose, tenure, interest_rate,
                     interest_method, total_repayment, balance, status, approval_stage,
                     loan_applicant_type, hr_affordability_consent, hr_affordability_status,
                     payment_collateral_type, payment_collateral_status, date_applied)
                VALUES
                    ('LOAN/DUE/0001', ?, 50000, 'Regular', 6, 11,
                     'reducing_annual', 52000, 52000, 'pending', 'president',
                     'staff', 1, 'pending', 'standing_order', 'pending', '2026-07-20')
            ''', (member_id,))
            db.commit()
            loan_id = db.execute(
                "SELECT id FROM loans WHERE loan_number = 'LOAN/DUE/0001'"
            ).fetchone()['id']

        blocked = self.client.post(
            f'/loans/{loan_id}/act',
            data={'action': 'approve'},
            follow_redirects=False,
        )
        self.assertIn(blocked.status_code, (302, 303))

        with self.app.app_context():
            db = get_db()
            loan = db.execute('SELECT * FROM loans WHERE id = ?', (loan_id,)).fetchone()
            self.assertEqual(loan['status'], 'pending')
            self.assertEqual(loan['approval_stage'], 'president')

        verified = self.client.post(
            f'/loans/{loan_id}/due-diligence',
            data={
                'hr_affordability_confirmed': '1',
                'payment_collateral_verified': '1',
                'comment': 'HR confirmed salary deduction capacity.',
            },
            follow_redirects=False,
        )
        self.assertIn(verified.status_code, (302, 303))

        approved = self.client.post(
            f'/loans/{loan_id}/act',
            data={'action': 'approve'},
            follow_redirects=False,
        )
        self.assertIn(approved.status_code, (302, 303))

        with self.app.app_context():
            db = get_db()
            loan = db.execute('SELECT * FROM loans WHERE id = ?', (loan_id,)).fetchone()
            self.assertEqual(loan['hr_affordability_status'], 'confirmed')
            self.assertEqual(loan['payment_collateral_status'], 'verified')
            self.assertEqual(loan['status'], 'active')
            self.assertEqual(loan['approval_stage'], 'approved')
            trail = db.execute(
                "SELECT * FROM loan_approvals WHERE loan_id = ? AND stage = 'due_diligence'",
                (loan_id,),
            ).fetchone()
            self.assertIsNotNone(trail)

            journal = db.execute(
                "SELECT id FROM journal_entries WHERE reference = 'LOAN/DUE/0001'"
            ).fetchone()
            if journal:
                db.execute('DELETE FROM journal_lines WHERE entry_id = ?', (journal['id'],))
                db.execute('DELETE FROM journal_entries WHERE id = ?', (journal['id'],))
            db.execute("DELETE FROM revenue WHERE source = 'Loan LOAN/DUE/0001'")
            db.execute('DELETE FROM loan_approvals WHERE loan_id = ?', (loan_id,))
            db.execute('DELETE FROM loans WHERE id = ?', (loan_id,))
            db.execute('DELETE FROM members WHERE id = ?', (member_id,))
            db.commit()

    def test_admin_can_resend_and_revoke_setup_links(self):
        self.login_admin()
        email = 'resend.setup@example.com'
        with self.app.app_context():
            db = get_db()
            existing = db.execute('SELECT id FROM users WHERE username = ?', (email,)).fetchone()
            if existing:
                user_id = existing['id']
            else:
                db.execute('''
                    INSERT INTO users
                        (username, password_hash, role, full_name, email,
                         is_active, must_change_password, created_at)
                    VALUES (?, ?, 'member', 'Resend Setup', ?, 1, 1, CURRENT_TIMESTAMP)
                ''', (email, generate_password_hash('UnusedPass1!'), email))
                db.commit()
                user_id = db.execute('SELECT id FROM users WHERE username = ?', (email,)).fetchone()['id']
            db.execute('''
                INSERT INTO account_setup_tokens (user_id, token_hash, purpose, expires_at)
                VALUES (?, 'old-token-hash-for-resend-test', 'member_onboarding', '2099-01-01 00:00:00')
            ''', (user_id,))
            db.commit()

        with patch('blueprints.admin_panel.send_member_onboarding_email') as onboarding_email:
            response = self.client.post(f'/api/resend_setup_link/{user_id}', follow_redirects=False)
        self.assertIn(response.status_code, (302, 303))
        onboarding_email.assert_called_once()
        self.assertIn('/setup-password/', onboarding_email.call_args.args[3])

        with self.app.app_context():
            db = get_db()
            rows = db.execute(
                'SELECT * FROM account_setup_tokens WHERE user_id = ? ORDER BY id',
                (user_id,)
            ).fetchall()
            self.assertGreaterEqual(len(rows), 2)
            self.assertIsNotNone(rows[0]['used_at'])
            self.assertIsNone(rows[-1]['used_at'])

        revoke = self.client.post(f'/api/revoke_setup_links/{user_id}', follow_redirects=False)
        self.assertIn(revoke.status_code, (302, 303))
        with self.app.app_context():
            db = get_db()
            open_links = db.execute(
                'SELECT COUNT(*) FROM account_setup_tokens WHERE user_id = ? AND used_at IS NULL',
                (user_id,)
            ).fetchone()[0]
            self.assertEqual(open_links, 0)

    def test_admin_can_bulk_send_pending_setup_links(self):
        self.login_admin()
        users = [
            ('bulk.pending.1@example.com', 'Bulk Pending One', 'bulk.pending.1@example.com', 1, 1),
            ('bulk.pending.2@example.com', 'Bulk Pending Two', 'bulk.pending.2@example.com', 1, 1),
            ('bulk.completed@example.com', 'Bulk Completed', 'bulk.completed@example.com', 1, 0),
            ('bulk.inactive@example.com', 'Bulk Inactive', 'bulk.inactive@example.com', 0, 1),
            ('bulk.noemail@example.com', 'Bulk No Email', '', 1, 1),
        ]
        with self.app.app_context():
            db = get_db()
            for username, full_name, email, is_active, must_change in users:
                db.execute('DELETE FROM account_setup_tokens WHERE user_id IN (SELECT id FROM users WHERE username = ?)', (username,))
                db.execute('DELETE FROM users WHERE username = ?', (username,))
                db.execute('''
                    INSERT INTO users
                        (username, password_hash, role, full_name, email,
                         is_active, must_change_password, created_at)
                    VALUES (?, ?, 'member', ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ''', (
                    username,
                    generate_password_hash('UnusedPass1!'),
                    full_name,
                    email,
                    is_active,
                    must_change,
                ))
            db.commit()

        with patch('blueprints.admin_panel.send_member_onboarding_email') as onboarding_email:
            response = self.client.post('/api/bulk_send_setup_links', follow_redirects=False)

        self.assertIn(response.status_code, (302, 303))
        self.assertEqual(onboarding_email.call_count, 2)
        recipients = {call.args[0] for call in onboarding_email.call_args_list}
        self.assertEqual(recipients, {'bulk.pending.1@example.com', 'bulk.pending.2@example.com'})

        with self.app.app_context():
            db = get_db()
            token_counts = {
                row['username']: row['token_count']
                for row in db.execute('''
                    SELECT u.username, COUNT(t.id) AS token_count
                    FROM users u
                    LEFT JOIN account_setup_tokens t ON t.user_id = u.id AND t.used_at IS NULL
                    WHERE u.username LIKE 'bulk.%@example.com'
                    GROUP BY u.username
                ''').fetchall()
            }
            self.assertEqual(token_counts['bulk.pending.1@example.com'], 1)
            self.assertEqual(token_counts['bulk.pending.2@example.com'], 1)
            self.assertEqual(token_counts['bulk.completed@example.com'], 0)
            self.assertEqual(token_counts['bulk.inactive@example.com'], 0)
            self.assertEqual(token_counts['bulk.noemail@example.com'], 0)

    def test_admin_readiness_endpoint_reports_core_services(self):
        self.login_admin()
        response = self.client.get('/api/readiness')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn(payload['overall'], {'ok', 'warn', 'fail'})
        checks = {check['key']: check for check in payload['checks']}
        for key in ('database', 'email', 'payments', 'backup'):
            self.assertIn(key, checks)
            self.assertIn(checks[key]['status'], {'ok', 'warn', 'fail'})
        self.assertIn('members', checks['database']['meta'])

    def test_salary_upload_posts_savings_journal_and_batch_detail(self):
        self.login_admin()
        member_id = self.create_member()
        csv_body = (
            'member_number,employee_id,email,phone,amount,month,date,receipt_number,notes\n'
            'OOU/TEST/0001,EMP001,ada.audit@example.com,08000000001,15000,2026-07,2026-07-05,,July payroll\n'
        )
        response = self.client.post(
            '/savings/salary-upload',
            data={
                'month': '2026-07',
                'batch_ref': 'SAL-SAV/TEST/0001',
                'file': (BytesIO(csv_body.encode('utf-8')), 'salary.csv'),
            },
            content_type='multipart/form-data',
            follow_redirects=False,
        )
        self.assertIn(response.status_code, (302, 303))
        self.assertIn('/savings/batch/SAL-SAV/TEST/0001', response.headers['Location'])

        with self.app.app_context():
            db = get_db()
            saving = db.execute(
                'SELECT * FROM savings WHERE import_batch = ? AND member_id = ?',
                ('SAL-SAV/TEST/0001', member_id),
            ).fetchone()
            self.assertIsNotNone(saving)
            self.assertEqual(saving['payment_method'], 'salary_deduction')
            self.assertEqual(float(saving['amount']), 15000.0)
            journal = db.execute(
                'SELECT * FROM journal_entries WHERE reference = ?',
                (saving['receipt_number'],),
            ).fetchone()
            self.assertIsNotNone(journal)
            rec = ledger_reconciliation(db)
            savings_section = next(s for s in rec['sections'] if s['label'] == 'Savings deposits')
            self.assertEqual(savings_section['missing'], 0)

        detail = self.client.get('/savings/batch/SAL-SAV/TEST/0001')
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b'SAL-SAV/TEST/0001', detail.data)
        export = self.client.get('/savings/batch/SAL-SAV/TEST/0001/export')
        self.assertEqual(export.status_code, 200)
        self.assertIn(b'posted_to_gl', export.data)

    def test_accounting_exports_journal_and_gl_register_csv(self):
        self.login_admin()
        with self.app.app_context():
            from ledger import CASH, OPERATING_EXPENSES, post_journal
            db = get_db()
            existing = db.execute(
                "SELECT id FROM journal_entries WHERE reference = 'TEST/GL/EXPORT'"
            ).fetchone()
            if not existing:
                post_journal(
                    db,
                    'CSV export smoke test',
                    [
                        {'account': OPERATING_EXPENSES, 'debit': 1234.56, 'memo': 'Export debit'},
                        {'account': CASH, 'credit': 1234.56, 'memo': 'Export credit'},
                    ],
                    date='2026-07-21',
                    reference='TEST/GL/EXPORT',
                    source_module='manual',
                )
                db.commit()

        journal_export = self.client.get('/accounting/journal/export')
        self.assertEqual(journal_export.status_code, 200)
        self.assertIn(b'entry_number,date,description,reference', journal_export.data)
        self.assertIn(b'TEST/GL/EXPORT', journal_export.data)

        gl_export = self.client.get('/accounting/ledger/1000/export')
        self.assertEqual(gl_export.status_code, 200)
        self.assertIn(b'account_code,account_name,account_type,normal_balance', gl_export.data)
        self.assertIn(b'TEST/GL/EXPORT', gl_export.data)

        with self.app.app_context():
            db = get_db()
            journal = db.execute(
                "SELECT id FROM journal_entries WHERE reference = 'TEST/GL/EXPORT'"
            ).fetchone()
            if journal:
                db.execute('DELETE FROM journal_lines WHERE entry_id = ?', (journal['id'],))
                db.execute('DELETE FROM journal_entries WHERE id = ?', (journal['id'],))
                db.commit()

    def test_bank_accounts_position_and_reconciliation_exports(self):
        self.login_admin()
        with open(os.path.join(os.getcwd(), 'blueprints', 'accounting.py'), encoding='utf-8') as f:
            self.assertNotIn("LIKE '%", f.read())
        with self.app.app_context():
            from ledger import OPERATING_EXPENSES, post_journal
            db = get_db()
            db.execute("DELETE FROM accounts WHERE code = '1096'")
            db.execute('''
                INSERT INTO accounts (code, name, type, normal_balance, parent_code, is_active)
                VALUES ('1096', 'Test Reconciliation Bank', 'asset', 'debit', '1000', 1)
            ''')
            entry_ids = []
            entry_ids.append(post_journal(
                db,
                'Opening bank test movement',
                [
                    {'account': '1096', 'debit': 1000, 'memo': 'Opening bank'},
                    {'account': OPERATING_EXPENSES, 'credit': 1000, 'memo': 'Offset'},
                ],
                date='2025-12-31',
                reference='TEST/BANK/OPEN',
                source_module='manual',
            ))
            entry_ids.append(post_journal(
                db,
                'Period bank inflow',
                [
                    {'account': '1096', 'debit': 2500, 'memo': 'Inflow'},
                    {'account': OPERATING_EXPENSES, 'credit': 2500, 'memo': 'Offset'},
                ],
                date='2026-07-10',
                reference='TEST/BANK/IN',
                source_module='manual',
            ))
            entry_ids.append(post_journal(
                db,
                'Period bank outflow',
                [
                    {'account': OPERATING_EXPENSES, 'debit': 400, 'memo': 'Expense'},
                    {'account': '1096', 'credit': 400, 'memo': 'Outflow'},
                ],
                date='2026-07-12',
                reference='TEST/BANK/OUT',
                source_module='manual',
            ))
            db.commit()

        page = self.client.get('/accounting/bank-accounts?from_date=2026-01-01&to_date=2026-07-31')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'Test Reconciliation Bank', page.data)
        self.assertIn(b'Bank Position', page.data)

        csv_page = self.client.get('/accounting/bank-accounts?from_date=2026-01-01&to_date=2026-07-31&format=csv')
        self.assertEqual(csv_page.status_code, 200)
        self.assertIn(b'account_code,account_name,opening_balance,cash_in', csv_page.data)
        self.assertIn(b'1096,Test Reconciliation Bank,1000.00,2500.00,400.00,3100.00', csv_page.data)

        detail = self.client.get(
            '/accounting/bank-accounts/1096?from_date=2026-01-01&to_date=2026-07-31&statement_balance=3100'
        )
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b'Bank Reconciliation', detail.data)
        self.assertIn(b'Variance', detail.data)
        self.assertIn(b'TEST/BANK/IN', detail.data)

        detail_csv = self.client.get(
            '/accounting/bank-accounts/1096?from_date=2026-01-01&to_date=2026-07-31&statement_balance=3100&format=csv'
        )
        self.assertEqual(detail_csv.status_code, 200)
        self.assertIn(b'gl_closing_balance,3100.00', detail_csv.data)
        self.assertIn(b'TEST/BANK/OUT', detail_csv.data)

        cash_header = self.client.get('/accounting/bank-accounts/1000?from_date=2026-01-01&to_date=2026-07-31')
        self.assertEqual(cash_header.status_code, 200)
        self.assertIn(b'Bank Reconciliation', cash_header.data)
        self.assertIn(b'Cash &amp; Bank', cash_header.data)

        with self.app.app_context():
            db = get_db()
            for entry_id in entry_ids:
                db.execute('DELETE FROM journal_lines WHERE entry_id = ?', (entry_id,))
                db.execute('DELETE FROM journal_entries WHERE id = ?', (entry_id,))
            db.execute("DELETE FROM accounts WHERE code = '1096'")
            db.commit()

    def test_admin_can_reclassify_savings_bank_lines_to_detail_bank(self):
        self.login_admin()
        with self.app.app_context():
            from ledger import MEMBER_DEPOSITS, post_journal
            db = get_db()
            db.execute("DELETE FROM accounts WHERE code = '1095'")
            db.execute('''
                INSERT INTO accounts (code, name, type, normal_balance, parent_code, is_active)
                VALUES ('1095', 'Zenith Test Bank', 'asset', 'debit', '1000', 1)
            ''')
            entry_id = post_journal(
                db,
                'Savings posted to header account',
                [
                    {'account': '1000', 'debit': 7500, 'memo': 'Savings cash side'},
                    {'account': MEMBER_DEPOSITS, 'credit': 7500, 'memo': 'Member savings'},
                ],
                date='2026-07-15',
                reference='TEST/SAV/RECLASS',
                source_module='savings_deposit',
            )
            db.commit()

        response = self.client.post(
            '/accounting/bank-accounts/reclassify-savings',
            data={
                'from_account': '1000',
                'to_account': '1095',
                'from_date': '2026-07-01',
                'to_date': '2026-07-31',
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Moved 1 savings bank line', response.data)

        with self.app.app_context():
            db = get_db()
            moved = db.execute('''
                SELECT account_code, debit, credit
                FROM journal_lines
                WHERE entry_id = ? AND debit > 0
            ''', (entry_id,)).fetchone()
            liability = db.execute('''
                SELECT account_code, debit, credit
                FROM journal_lines
                WHERE entry_id = ? AND credit > 0
            ''', (entry_id,)).fetchone()
            self.assertEqual(moved['account_code'], '1095')
            self.assertEqual(liability['account_code'], MEMBER_DEPOSITS)
            db.execute('DELETE FROM journal_lines WHERE entry_id = ?', (entry_id,))
            db.execute('DELETE FROM journal_entries WHERE id = ?', (entry_id,))
            db.execute("DELETE FROM accounts WHERE code = '1095'")
            db.commit()

    def test_admin_can_send_member_email_campaign_with_logs(self):
        member_id = self.create_member()
        self.login_admin()
        composer = self.client.get('/communications/new')
        self.assertEqual(composer.status_code, 200)
        self.assertIn(b'Monthly savings reminder', composer.data)
        self.assertIn(b'Loan repayment reminder', composer.data)

        sent_messages = []

        def fake_send(to, subject, html, text=''):
            sent_messages.append((to, subject, html))
            return True

        with patch('blueprints.communications.send_email', side_effect=fake_send):
            response = self.client.post(
                '/communications/new',
                data={
                    'title': 'Profile reminder',
                    'audience': 'selected',
                    'channel': 'email',
                    'member_ids': [str(member_id)],
                    'subject': 'Hello {first_name}',
                    'body': 'Dear {first_name}, your balance is {savings_balance}. Portal: {portal_link}',
                },
                follow_redirects=True,
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Campaign sent: 1 sent, 0 failed, 0 skipped', response.data)
        self.assertEqual(len(sent_messages), 1)
        self.assertEqual(sent_messages[0][0], 'ada.audit@example.com')
        self.assertIn('Hello Ada', sent_messages[0][1])
        self.assertIn('Dear Ada', sent_messages[0][2])
        self.assertIn('NGN', sent_messages[0][2])
        self.assertIn('CoopMS Member Communication', sent_messages[0][2])
        self.assertIn('Member Portal Notice', sent_messages[0][2])

        with self.app.app_context():
            db = get_db()
            campaign = db.execute(
                "SELECT * FROM communication_campaigns WHERE title = 'Profile reminder'"
            ).fetchone()
            self.assertIsNotNone(campaign)
            self.assertEqual(campaign['sent_count'], 1)
            recipient = db.execute(
                'SELECT * FROM communication_recipients WHERE campaign_id = ?',
                (campaign['id'],),
            ).fetchone()
            self.assertEqual(recipient['status'], 'sent')
            db.execute('DELETE FROM communication_recipients WHERE campaign_id = ?', (campaign['id'],))
            db.execute('DELETE FROM communication_campaigns WHERE id = ?', (campaign['id'],))
            db.commit()

    def test_journal_quick_view_drawer_endpoint_and_register_link(self):
        self.login_admin()
        with self.app.app_context():
            from ledger import CASH, OPERATING_EXPENSES, post_journal
            db = get_db()
            existing = db.execute(
                "SELECT id FROM journal_entries WHERE reference = 'TEST/JOURNAL/DRAWER'"
            ).fetchone()
            if existing:
                entry_id = existing['id']
            else:
                entry_id = post_journal(
                    db,
                    'Drawer quick view smoke test',
                    [
                        {'account': OPERATING_EXPENSES, 'debit': 500, 'memo': 'Drawer debit'},
                        {'account': CASH, 'credit': 500, 'memo': 'Drawer credit'},
                    ],
                    date='2026-07-22',
                    reference='TEST/JOURNAL/DRAWER',
                    source_module='manual',
                )
                db.commit()

        quick_view = self.client.get(f'/accounting/journal/{entry_id}/quick-view')
        self.assertEqual(quick_view.status_code, 200)
        payload = quick_view.get_json()
        self.assertTrue(payload['ok'])
        self.assertIn('TEST/JOURNAL/DRAWER', payload['html'])
        self.assertIn('Debit (left side)', payload['html'])
        self.assertIn('Open full page', payload['html'])

        register = self.client.get('/accounting/journal')
        self.assertEqual(register.status_code, 200)
        self.assertIn(b'data-journal-quick-view', register.data)
        self.assertIn(f'/accounting/journal/{entry_id}/quick-view'.encode(), register.data)

        with self.app.app_context():
            db = get_db()
            db.execute('DELETE FROM journal_lines WHERE entry_id = ?', (entry_id,))
            db.execute('DELETE FROM journal_entries WHERE id = ?', (entry_id,))
            db.commit()

    def test_member_profile_completion_and_certified_badge(self):
        member_id = self.create_member()
        self.create_member_user(member_id)
        self.login_member()

        incomplete = self.client.get('/profile')
        self.assertEqual(incomplete.status_code, 200)
        self.assertIn(b'Profile In Progress', incomplete.data)
        self.assertIn(b'Readiness to transact', incomplete.data)

        response = self.client.post(
            '/edit-profile',
            data={
                'first_name': 'Ada',
                'last_name': 'Audit',
                'email': 'ada.audit@example.com',
                'phone': '08000000001',
                'date_of_birth': '1990-01-02',
                'occupation': 'Accountant',
                'address': '12 Cooperative Road',
                'city': 'Ago-Iwoye',
                'state': 'Ogun',
                'country': 'Nigeria',
                'bank_name': 'Test Bank',
                'account_name': 'Ada Audit',
                'account_number': '1234567890',
                'emergency_contact_name': 'Bola Audit',
                'emergency_contact_phone': '08000000002',
                'nominee_name': 'Tunde Audit',
                'nominee_relationship': 'Brother',
                'nominee_phone': '08000000003',
                'nominee_email': 'tunde.audit@example.com',
                'nominee_address': '13 Cooperative Road',
                'bvn': '12345678901',
                'nin': '10987654321',
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Certified Member', response.data)
        self.assertIn(b'100%', response.data)
        self.assertNotIn(b'1234567890', response.data)
        self.assertIn(b'******7890', response.data)

        with self.app.app_context():
            db = get_db()
            member = db.execute('SELECT * FROM members WHERE id = ?', (member_id,)).fetchone()
            self.assertEqual(member['city'], 'Ago-Iwoye')
            self.assertEqual(member['state'], 'Ogun')
            self.assertTrue(is_encrypted(member['bank_name']))
            self.assertTrue(is_encrypted(member['account_number']))
            self.assertTrue(is_encrypted(member['bvn']))
            self.assertEqual(decrypt_field(member['bank_name']), 'Test Bank')
            self.assertEqual(decrypt_field(member['account_number']), '1234567890')
            self.assertEqual(decrypt_field(member['bvn']), '12345678901')
            user = db.execute('SELECT * FROM users WHERE email = ?', ('ada.audit@example.com',)).fetchone()
            self.assertEqual(user['phone'], '08000000001')

        bad_reveal = self.client.post('/profile/reveal-sensitive', data={'password': 'wrong'})
        self.assertEqual(bad_reveal.status_code, 403)
        reveal = self.client.post('/profile/reveal-sensitive', data={'password': 'MemberPass1!'})
        self.assertEqual(reveal.status_code, 200)
        fields = reveal.get_json()['fields']
        self.assertEqual(fields['account_number'], '1234567890')
        self.assertEqual(fields['bvn'], '12345678901')
        self.assertEqual(fields['nin'], '10987654321')

    def test_staff_user_can_switch_between_admin_and_member_views(self):
        member_id = self.create_member()
        with self.app.app_context():
            db = get_db()
            db.execute(
                "UPDATE users SET email = ?, phone = ? WHERE username = 'admin'",
                ('ada.audit@example.com', '08000000001')
            )
            db.commit()

        self.login_admin()
        admin_page = self.client.get('/dashboard')
        self.assertEqual(admin_page.status_code, 200)
        self.assertIn(b'My Member Portal', admin_page.data)
        self.assertIn(b'Dashboard', admin_page.data)

        member_view = self.client.post('/member/view-as-member', follow_redirects=True)
        self.assertEqual(member_view.status_code, 200)
        self.assertIn(b'My Profile', member_view.data)
        self.assertIn(b'Back to Admin', member_view.data)
        self.assertNotIn(b'Data Migration', member_view.data)

        protected_admin = self.client.get('/members')
        self.assertEqual(protected_admin.status_code, 200)

        admin_view = self.client.post('/member/back-to-admin', follow_redirects=True)
        self.assertEqual(admin_view.status_code, 200)
        self.assertIn(b'Data Migration', admin_view.data)

    def test_chart_of_accounts_creates_detail_account_under_parent(self):
        self.login_admin()
        with self.app.app_context():
            db = get_db()
            db.execute("DELETE FROM accounts WHERE code = '1099'")
            db.commit()

        response = self.client.post(
            '/accounting/accounts/add',
            data={
                'code': '1099',
                'name': 'Test Detail Bank',
                'type': '',
                'normal_balance': '',
                'parent_code': '1000',
            },
            follow_redirects=False,
        )
        self.assertIn(response.status_code, (302, 303))
        with self.app.app_context():
            db = get_db()
            account = db.execute("SELECT * FROM accounts WHERE code = '1099'").fetchone()
            self.assertIsNotNone(account)
            self.assertEqual(account['parent_code'], '1000')
            self.assertEqual(account['type'], 'asset')
            self.assertEqual(account['normal_balance'], 'debit')

            db.execute("DELETE FROM accounts WHERE code = '1099'")
            db.commit()

    def test_savings_post_to_configured_default_cash_detail_account(self):
        self.login_admin()
        member_id = self.create_member()
        with self.app.app_context():
            db = get_db()
            db.execute("DELETE FROM journal_lines WHERE account_code = '1098'")
            db.execute("DELETE FROM accounts WHERE code = '1098'")
            db.execute("DELETE FROM settings WHERE key = 'default_cash_account'")
            db.execute('''
                INSERT INTO accounts (code, name, type, normal_balance, parent_code, is_active)
                VALUES ('1098', 'Test Main Bank', 'asset', 'debit', '1000', 1)
            ''')
            db.execute(
                "INSERT INTO settings (key, value, description) VALUES ('default_cash_account', '1098', 'test')"
            )
            db.commit()

        response = self.client.post('/savings/add', data={
            'member_id': member_id,
            'amount': '5000',
            'month': '2026-07',
            'payment_type': 'monthly',
            'payment_method': 'bank_transfer',
            'notes': 'Default cash account test',
        }, follow_redirects=False)
        self.assertIn(response.status_code, (302, 303))

        with self.app.app_context():
            db = get_db()
            line = db.execute('''
                SELECT jl.*
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.entry_id
                WHERE jl.account_code = '1098'
                  AND je.source_module = 'savings_deposit'
                ORDER BY jl.id DESC
            ''').fetchone()
            self.assertIsNotNone(line)
            self.assertGreater(float(line['debit'] or 0), 0)
            db.execute("DELETE FROM settings WHERE key = 'default_cash_account'")
            db.execute("DELETE FROM journal_lines WHERE account_code = '1098'")
            db.execute("DELETE FROM accounts WHERE code = '1098'")
            db.commit()

    def test_financial_reporting_center_and_control_exports_render(self):
        self.login_admin()
        member_id = self.create_member()
        with self.app.app_context():
            db = get_db()
            db.execute(
                "DELETE FROM savings WHERE receipt_number = 'REPORT/SAV/0001'"
            )
            db.execute('''
                INSERT INTO savings
                    (member_id, amount, month, payment_type, payment_method,
                     receipt_number, date, share_capital)
                VALUES (?, 5000, '2026-07', 'monthly', 'cash',
                        'REPORT/SAV/0001', '2026-07-21', 0)
            ''', (member_id,))
            db.commit()

        page = self.client.get('/reports')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'Financial Reporting', page.data)
        self.assertIn(b'Member Savings Control', page.data)

        for url, marker in (
            ('/reports/cashbook?format=csv', b'Date,Entry #,Description'),
            ('/reports/member-savings-control?format=csv', b'Member #,Member Name,Email'),
            ('/reports/loan-portfolio?format=csv', b'Loan #,Member #,Member Name'),
        ):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertIn(marker, response.data)

    def test_financial_report_uses_legacy_income_fallback(self):
        with self.app.app_context():
            db = get_db()
            db.execute('''
                INSERT INTO revenue (revenue_number, category, amount, description, source, date)
                VALUES ('REV/TEST/0001', 'Other Income', 2500, 'Legacy revenue', 'Test', '2026-07-05')
            ''')
            db.execute('''
                INSERT INTO expenses (expense_number, category, amount, description, date)
                VALUES ('EXP/TEST/0001', 'Office', 700, 'Legacy expense', '2026-07-06')
            ''')
            db.commit()
            inc = income_statement(db, '2026-07-01', '2026-07-31')
            self.assertEqual(inc['total_income'], 2500.0)
            self.assertEqual(inc['total_expenses'], 700.0)
            self.assertEqual(inc['net_surplus'], 1800.0)

    def test_email_service_accepts_flask_mail_env_names(self):
        import email_service

        original_env = os.environ.copy()
        original_smtp = email_service.smtplib.SMTP

        class FakeSMTP:
            sent = []
            started_tls = False

            def __init__(self, host, port, timeout=10):
                self.host = host
                self.port = port
                self.timeout = timeout

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def ehlo(self):
                pass

            def starttls(self, context=None):
                FakeSMTP.started_tls = True

            def login(self, user, password):
                self.user = user
                self.password = password

            def sendmail(self, from_addr, recipients, message):
                FakeSMTP.sent.append((from_addr, recipients, message))

        try:
            for key in (
                'MAIL_ENABLED', 'SMTP_HOST', 'SMTP_PORT', 'SMTP_USER',
                'SMTP_PASS', 'MAIL_FROM', 'RESEND_API_KEY',
            ):
                os.environ.pop(key, None)
            os.environ.update({
                'ENABLE_EMAIL_NOTIFICATIONS': 'true',
                'MAIL_SERVER': 'smtp.example.test',
                'MAIL_PORT': '587',
                'MAIL_USERNAME': 'coop@example.test',
                'MAIL_PASSWORD': 'app-password',
                'MAIL_DEFAULT_SENDER': 'OOU Coop <coop@example.test>',
                'MAIL_USE_TLS': 'true',
            })
            email_service.smtplib.SMTP = FakeSMTP

            ok = email_service.send_email(
                'member@example.test',
                'SMTP compatibility test',
                '<p>Hello</p>',
                'Hello',
            )

            self.assertTrue(ok)
            self.assertTrue(FakeSMTP.started_tls)
            self.assertEqual(len(FakeSMTP.sent), 1)
            self.assertEqual(FakeSMTP.sent[0][0], 'OOU Coop <coop@example.test>')
            self.assertEqual(FakeSMTP.sent[0][1], ['member@example.test'])
        finally:
            os.environ.clear()
            os.environ.update(original_env)
            email_service.smtplib.SMTP = original_smtp

    def test_email_service_falls_back_to_smtp_when_resend_fails(self):
        import email_service

        original_env = os.environ.copy()
        original_resend = email_service._send_via_resend
        original_smtp = email_service.smtplib.SMTP

        class FakeSMTP:
            sent = []

            def __init__(self, host, port, timeout=10):
                self.host = host
                self.port = port
                self.timeout = timeout

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def ehlo(self):
                pass

            def starttls(self, context=None):
                pass

            def login(self, user, password):
                self.user = user
                self.password = password

            def sendmail(self, from_addr, recipients, message):
                FakeSMTP.sent.append((from_addr, recipients, message))

        try:
            for key in (
                'MAIL_ENABLED', 'ENABLE_EMAIL_NOTIFICATIONS', 'SMTP_HOST',
                'SMTP_PORT', 'SMTP_USER', 'SMTP_PASS', 'MAIL_FROM',
                'RESEND_API_KEY',
            ):
                os.environ.pop(key, None)
            os.environ.update({
                'MAIL_ENABLED': '1',
                'RESEND_API_KEY': 're_test_key',
                'SMTP_HOST': 'smtp.example.test',
                'SMTP_PORT': '587',
                'SMTP_USER': 'coop@example.test',
                'SMTP_PASS': 'app-password',
                'MAIL_FROM': 'OOU Coop <coop@example.test>',
                'SMTP_USE_TLS': 'true',
            })
            email_service._send_via_resend = lambda to, subject, html: False
            email_service.smtplib.SMTP = FakeSMTP

            ok = email_service.send_email(
                'member@example.test',
                'Fallback test',
                '<p>Hello</p>',
            )

            self.assertTrue(ok)
            self.assertEqual(len(FakeSMTP.sent), 1)
        finally:
            os.environ.clear()
            os.environ.update(original_env)
            email_service._send_via_resend = original_resend
            email_service.smtplib.SMTP = original_smtp

    def test_email_service_sends_via_brevo_api(self):
        import json
        import email_service

        original_env = os.environ.copy()
        original_urlopen = email_service.urllib.request.urlopen
        captured = {}

        class FakeResponse:
            status = 201

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        def fake_urlopen(request, timeout=15):
            captured['url'] = request.full_url
            captured['timeout'] = timeout
            captured['headers'] = dict(request.header_items())
            captured['payload'] = json.loads(request.data.decode('utf-8'))
            return FakeResponse()

        try:
            for key in (
                'MAIL_ENABLED', 'ENABLE_EMAIL_NOTIFICATIONS', 'RESEND_API_KEY',
                'BREVO_API_KEY', 'SENDINBLUE_API_KEY', 'MAIL_FROM',
                'SMTP_HOST', 'MAIL_SERVER',
            ):
                os.environ.pop(key, None)
            os.environ.update({
                'MAIL_ENABLED': '1',
                'BREVO_API_KEY': 'xkeysib-test',
                'MAIL_FROM': 'OOU Coop <coop@example.test>',
            })
            email_service.urllib.request.urlopen = fake_urlopen

            ok = email_service.send_email(
                'member@example.test',
                'Brevo API test',
                '<p>Hello</p>',
                'Hello',
            )

            self.assertTrue(ok)
            self.assertEqual(captured['url'], 'https://api.brevo.com/v3/smtp/email')
            self.assertEqual(captured['timeout'], 15)
            self.assertEqual(captured['headers']['Api-key'], 'xkeysib-test')
            self.assertEqual(captured['payload']['sender']['email'], 'coop@example.test')
            self.assertEqual(captured['payload']['sender']['name'], 'OOU Coop')
            self.assertEqual(captured['payload']['to'], [{'email': 'member@example.test'}])
            self.assertEqual(captured['payload']['subject'], 'Brevo API test')
            self.assertEqual(captured['payload']['textContent'], 'Hello')
        finally:
            os.environ.clear()
            os.environ.update(original_env)
            email_service.urllib.request.urlopen = original_urlopen

    def test_payment_processing_uses_postgres_row_lock(self):
        from blueprints import payments_bp as payments_module

        original_flag = payments_module.USE_POSTGRES

        class FakeCursor:
            def fetchone(self):
                return {'reference': 'PAY-LOCK', 'status': 'pending'}

        class FakeDb:
            sql = ''
            params = ()

            def execute(self, sql, params=()):
                self.sql = sql
                self.params = params
                return FakeCursor()

        try:
            payments_module.USE_POSTGRES = True
            db = FakeDb()
            row = payments_module._select_pending_payment_for_processing(db, 'PAY-LOCK')
            self.assertEqual(row['reference'], 'PAY-LOCK')
            self.assertIn('FOR UPDATE', db.sql)
            self.assertEqual(db.params, ('PAY-LOCK',))
        finally:
            payments_module.USE_POSTGRES = original_flag

    def test_completed_payment_releases_lock_without_reposting(self):
        from blueprints import payments_bp as payments_module

        class FakeCursor:
            def fetchone(self):
                return {'reference': 'PAY-DONE', 'status': 'completed'}

        class FakeDb:
            rolled_back = False

            def execute(self, sql, params=()):
                return FakeCursor()

            def rollback(self):
                self.rolled_back = True

        db = FakeDb()
        processed = payments_module._record_payment(db, 'PAY-DONE')
        self.assertFalse(processed)
        self.assertTrue(db.rolled_back)

    def test_audit_log_does_not_commit_caller_transaction(self):
        from security import log_audit

        class FakeDb:
            committed = False
            executed = False

            def execute(self, sql, params=()):
                self.executed = True

            def commit(self):
                self.committed = True

        db = FakeDb()
        log_audit(db, 1, 'admin', 'TEST', 'security', 'audit test')
        self.assertTrue(db.executed)
        self.assertFalse(db.committed)

    def test_financial_references_are_unique_when_present(self):
        member_id = self.create_member()
        with self.app.app_context():
            db = get_db()

            db.execute('''
                INSERT INTO savings
                    (member_id, amount, month, payment_type, payment_method,
                     receipt_number, date)
                VALUES (?, 1000, '2026-08', 'monthly', 'cash', 'RCPT/UNIQUE/1', '2026-08-01')
            ''', (member_id,))
            with self.assertRaises(Exception):
                db.execute('''
                    INSERT INTO savings
                        (member_id, amount, month, payment_type, payment_method,
                         receipt_number, date)
                    VALUES (?, 1000, '2026-08', 'monthly', 'cash', 'RCPT/UNIQUE/1', '2026-08-01')
                ''', (member_id,))
            db.rollback()

            loan_number = 'LOAN/UNIQUE/1'
            db.execute('''
                INSERT INTO loans
                    (loan_number, member_id, amount, purpose, tenure, interest_rate,
                     total_repayment, balance, status, date_applied)
                VALUES (?, ?, 10000, 'Regular', 6, 10, 10500, 10500, 'active', '2026-08-01')
            ''', (loan_number, member_id))
            loan_id = db.execute(
                'SELECT id FROM loans WHERE loan_number = ?', (loan_number,)
            ).fetchone()['id']
            db.execute('''
                INSERT INTO repayments
                    (repayment_number, loan_id, amount, reference, date)
                VALUES ('REP/UNIQUE/1', ?, 1000, 'PAY-UNIQUE-1', '2026-08-02')
            ''', (loan_id,))
            with self.assertRaises(Exception):
                db.execute('''
                    INSERT INTO repayments
                        (repayment_number, loan_id, amount, reference, date)
                    VALUES ('REP/UNIQUE/2', ?, 1000, 'PAY-UNIQUE-1', '2026-08-02')
                ''', (loan_id,))
            db.rollback()

            db.execute('''
                INSERT INTO journal_entries
                    (entry_number, date, description, reference)
                VALUES ('JE-UNIQUE-1', '2026-08-03', 'Unique ref test', 'JREF-UNIQUE-1')
            ''')
            with self.assertRaises(Exception):
                db.execute('''
                    INSERT INTO journal_entries
                        (entry_number, date, description, reference)
                    VALUES ('JE-UNIQUE-2', '2026-08-03', 'Unique ref duplicate', 'JREF-UNIQUE-1')
            ''')
            db.rollback()

    def test_operational_fee_revenue_is_not_backfilled_twice(self):
        with self.app.app_context():
            db = get_db()
            db.execute('''
                INSERT INTO revenue
                    (revenue_number, category, amount, description, source, date)
                VALUES
                    ('REV/OPERATIONAL/MEMO/1', 'Late Fee', 500,
                     'Late fee already posted with savings journal', 'Savings', '2026-09-01')
            ''')

            posted = backfill_from_transactions(db, created_by=1)
            duplicate = db.execute(
                "SELECT id FROM journal_entries WHERE reference = 'REV/OPERATIONAL/MEMO/1'"
            ).fetchone()
            rec = ledger_reconciliation(db, sample_limit=1000)
            revenue_section = next(s for s in rec['sections'] if s['label'] == 'Revenue')
            sample_refs = {r['ref'] for r in revenue_section['samples']}

            self.assertIsNone(duplicate)
            self.assertNotIn('REV/OPERATIONAL/MEMO/1', sample_refs)
            self.assertGreaterEqual(posted, 0)
            db.rollback()


if __name__ == '__main__':
    unittest.main()
