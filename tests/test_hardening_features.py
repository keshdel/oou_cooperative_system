import json
import os
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
os.environ.pop('DATABASE_URL', None)
os.environ['SQLITE_DB_PATH'] = TEST_DB

try:
    os.remove(TEST_DB)
except FileNotFoundError:
    pass

import app as app_module  # noqa: E402
from database import get_db  # noqa: E402
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

    def login_member(self):
        response = self.client.post(
            '/login',
            data={'username': 'ada.audit@example.com', 'password': 'MemberPass1!'},
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
                'bank_statement_ack': '1',
                'data_processing_consent': '1',
                'credit_check_consent': '1',
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
            self.assertEqual(loan['credit_check_consent'], 1)
            self.assertEqual(loan['repayment_schedule_accepted'], 1)
            self.assertEqual(loan['bank_statement_status'], 'requested')
            self.assertEqual(loan['payment_collateral_type'], 'standing_order')
            self.assertEqual(loan['payment_collateral_status'], 'pending')
            self.assertEqual(loan['consent_ip'], '203.0.113.44')

            snapshot = json.loads(loan['repayment_schedule_snapshot'])
            self.assertEqual(snapshot['principal'], 50000)
            self.assertEqual(snapshot['purpose'], 'Regular')
            self.assertEqual(snapshot['tenure'], 6)
            self.assertEqual(len(snapshot['schedule']), 6)

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
