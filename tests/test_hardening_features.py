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


if __name__ == '__main__':
    unittest.main()
