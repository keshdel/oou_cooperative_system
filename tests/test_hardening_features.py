import os
import unittest
from io import BytesIO


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
from ledger import ledger_reconciliation  # noqa: E402
from reports_engine import income_statement  # noqa: E402


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

    def test_support_routes_are_disabled_by_default(self):
        for path in ('/setup', '/debug-auth', '/emergency-reset'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 404, path)

    def test_mobile_repayment_is_fail_closed(self):
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


if __name__ == '__main__':
    unittest.main()
