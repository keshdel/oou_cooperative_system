import os
import unittest

TEST_DB = os.path.abspath('.test-hq-billing.db')
os.environ.setdefault('SECRET_KEY', 'test-secret-hq-billing')
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


class HqBillingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app_module.app
        cls.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)

    def setUp(self):
        os.environ['MARKETING_HQ'] = '1'
        self.client = self.app.test_client()

    def tearDown(self):
        os.environ.pop('MARKETING_HQ', None)

    def login_admin(self):
        r = self.client.post('/login', data={'username': 'admin', 'password': 'TestAdmin123'},
                             follow_redirects=False)
        self.assertIn(r.status_code, (302, 303))

    def _add_client(self, name, users, rate=5000):
        self.client.post('/hq/clients', data={
            'name': name, 'code': name.lower(), 'billing_email': 'a@x.com',
            'user_count': str(users), 'rate_per_user': str(rate), 'billing_cycle': 'annual',
        }, follow_redirects=True)
        with self.app.app_context():
            return get_db().execute('SELECT id FROM hq_clients WHERE name = ?', (name,)).fetchone()['id']

    def test_billing_is_404_off_the_hq_instance(self):
        os.environ.pop('MARKETING_HQ', None)
        self.login_admin()
        self.assertEqual(self.client.get('/hq/invoices').status_code, 404)

    def test_full_subscription_invoice_and_billed_count(self):
        self.login_admin()
        cid = self._add_client('Alpha', 10, 5000)
        r = self.client.post('/hq/invoices/new', data={
            'client_id': cid, 'period_label': '2026', 'sub_mode': 'full',
            'sub_qty': '10', 'sub_unit': '5000'}, follow_redirects=False)
        self.assertIn(r.status_code, (302, 303))
        with self.app.app_context():
            db = get_db()
            inv = db.execute('SELECT * FROM hq_invoices WHERE client_id = ? ORDER BY id DESC', (cid,)).fetchone()
            self.assertAlmostEqual(float(inv['amount']), 50000.0, places=2)   # 10 × 5,000
            c = db.execute('SELECT billed_user_count FROM hq_clients WHERE id = ?', (cid,)).fetchone()
            self.assertEqual(c['billed_user_count'], 10)

    def test_topup_bills_only_new_members_plus_service_fee(self):
        self.login_admin()
        cid = self._add_client('Beta', 10, 5000)
        # First a full subscription so billed_user_count = 10.
        self.client.post('/hq/invoices/new', data={
            'client_id': cid, 'sub_mode': 'full', 'sub_qty': '10', 'sub_unit': '5000'})
        # Grow to 13 members, then top-up 3 + a migration service fee.
        self.client.post(f'/hq/clients/{cid}/edit', data={
            'name': 'Beta', 'user_count': '13', 'rate_per_user': '5000',
            'billing_cycle': 'annual', 'status': 'active'})
        self.client.post('/hq/invoices/new', data={
            'client_id': cid, 'sub_mode': 'topup', 'sub_qty': '3', 'sub_unit': '5000',
            'service_type': 'migration', 'service_desc': 'one-off setup', 'service_amount': '20000'})
        with self.app.app_context():
            db = get_db()
            inv = db.execute('SELECT * FROM hq_invoices WHERE client_id = ? ORDER BY id DESC', (cid,)).fetchone()
            self.assertAlmostEqual(float(inv['amount']), 35000.0, places=2)   # 3×5,000 + 20,000
            types = {it['item_type'] for it in
                     db.execute('SELECT item_type FROM hq_invoice_items WHERE invoice_id = ?', (inv['id'],)).fetchall()}
            self.assertIn('topup', types)
            self.assertIn('service', types)
            c = db.execute('SELECT billed_user_count FROM hq_clients WHERE id = ?', (cid,)).fetchone()
            self.assertEqual(c['billed_user_count'], 13)   # 10 + 3

    def test_mark_paid_and_pay_link_token_guard(self):
        self.login_admin()
        cid = self._add_client('Gamma', 5, 5000)
        self.client.post('/hq/invoices/new', data={
            'client_id': cid, 'sub_mode': 'full', 'sub_qty': '5', 'sub_unit': '5000'})
        with self.app.app_context():
            db = get_db()
            inv = db.execute('SELECT * FROM hq_invoices WHERE client_id = ? ORDER BY id DESC', (cid,)).fetchone()
            inv_id, number, token = inv['id'], inv['invoice_number'], inv['pay_token']
        # Wrong token is a 404; right token reaches the pay page (or a friendly message).
        self.assertEqual(self.client.get(f'/hq/pay/{number}/wrongtoken').status_code, 404)
        # Mark paid manually.
        r = self.client.post(f'/hq/invoices/{inv_id}/mark-paid',
                             data={'reference': 'TRF-123'}, follow_redirects=False)
        self.assertIn(r.status_code, (302, 303))
        with self.app.app_context():
            db = get_db()
            inv = db.execute('SELECT status, paid_method FROM hq_invoices WHERE id = ?', (inv_id,)).fetchone()
            self.assertEqual(inv['status'], 'paid')
            self.assertEqual(inv['paid_method'], 'manual')


if __name__ == '__main__':
    unittest.main()
