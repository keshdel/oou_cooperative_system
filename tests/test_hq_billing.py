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

    def test_draft_invoice_is_editable(self):
        self.login_admin()
        cid = self._add_client('Editable', 10, 5000)
        self.client.post('/hq/invoices/new', data={
            'client_id': cid, 'sub_mode': 'full', 'sub_qty': '10', 'sub_unit': '5000'})
        with self.app.app_context():
            db = get_db()
            inv_id = db.execute('SELECT id FROM hq_invoices WHERE client_id = ? ORDER BY id DESC',
                                (cid,)).fetchone()['id']
            sub_id = db.execute("SELECT id FROM hq_invoice_items WHERE invoice_id = ? AND item_type = 'subscription'",
                                (inv_id,)).fetchone()['id']
        # Reduce subscription to 8 members, add a support fee of 15,000, edit notes.
        r = self.client.post(f'/hq/invoices/{inv_id}/edit', data={
            'item_id': [str(sub_id)], 'item_desc': ['Annual subscription'],
            'item_qty': ['8'], 'item_unit': ['5000'],
            'new_type': ['support'], 'new_desc': ['1yr'], 'new_amount': ['15000'],
            'period_label': '2026', 'due_date': '', 'notes': 'edited'}, follow_redirects=False)
        self.assertIn(r.status_code, (302, 303))
        with self.app.app_context():
            db = get_db()
            inv = db.execute('SELECT amount, notes FROM hq_invoices WHERE id = ?', (inv_id,)).fetchone()
            self.assertAlmostEqual(float(inv['amount']), 55000.0, places=2)   # 8×5,000 + 15,000
            self.assertEqual(inv['notes'], 'edited')
            billed = db.execute('SELECT billed_user_count FROM hq_clients WHERE id = ?', (cid,)).fetchone()[0]
            self.assertEqual(billed, 8)   # 10 + (8 − 10)
            n_service = db.execute("SELECT COUNT(*) FROM hq_invoice_items WHERE invoice_id = ? AND item_type = 'service'",
                                   (inv_id,)).fetchone()[0]
            self.assertEqual(n_service, 1)

    def test_non_draft_invoice_cannot_be_edited(self):
        self.login_admin()
        cid = self._add_client('Locked', 5, 5000)
        self.client.post('/hq/invoices/new', data={
            'client_id': cid, 'sub_mode': 'full', 'sub_qty': '5', 'sub_unit': '5000'})
        with self.app.app_context():
            inv_id = get_db().execute('SELECT id FROM hq_invoices WHERE client_id = ? ORDER BY id DESC',
                                      (cid,)).fetchone()['id']
        self.client.post(f'/hq/invoices/{inv_id}/mark-paid', data={'reference': 'x'})
        self.client.post(f'/hq/invoices/{inv_id}/edit', data={
            'new_type': ['support'], 'new_desc': ['sneak'], 'new_amount': ['99999'], 'period_label': 'x'})
        with self.app.app_context():
            inv = get_db().execute('SELECT amount, status FROM hq_invoices WHERE id = ?', (inv_id,)).fetchone()
            self.assertEqual(inv['status'], 'paid')
            self.assertAlmostEqual(float(inv['amount']), 25000.0, places=2)   # unchanged

    def test_duplicate_invoice_clones_into_a_new_draft(self):
        self.login_admin()
        cid = self._add_client('Cloney', 70, 5000)
        self.client.post('/hq/invoices/new', data={
            'client_id': cid, 'sub_mode': 'full', 'sub_qty': '70', 'sub_unit': '5000',
            'service_type': 'support', 'service_desc': '1yr', 'service_amount': '100000',
            'period_label': '2025/2026'})
        with self.app.app_context():
            db = get_db()
            src = db.execute('SELECT * FROM hq_invoices WHERE client_id = ? ORDER BY id DESC', (cid,)).fetchone()
            src_id, src_number, src_total = src['id'], src['invoice_number'], float(src['amount'])
        r = self.client.post(f'/hq/invoices/{src_id}/duplicate', follow_redirects=False)
        self.assertIn(r.status_code, (302, 303))
        with self.app.app_context():
            db = get_db()
            copies = db.execute('SELECT * FROM hq_invoices WHERE client_id = ? ORDER BY id DESC', (cid,)).fetchall()
            self.assertEqual(len(copies), 2)
            copy = copies[0]
            self.assertNotEqual(copy['invoice_number'], src_number)   # fresh number
            self.assertNotEqual(copy['pay_token'], src['pay_token'] if 'pay_token' in src.keys() else None)
            self.assertEqual(copy['status'], 'draft')
            self.assertAlmostEqual(float(copy['amount']), src_total, places=2)   # 70×5,000 + 100,000
            src_items = db.execute('SELECT COUNT(*) FROM hq_invoice_items WHERE invoice_id = ?', (src_id,)).fetchone()[0]
            copy_items = db.execute('SELECT COUNT(*) FROM hq_invoice_items WHERE invoice_id = ?', (copy['id'],)).fetchone()[0]
            self.assertEqual(src_items, copy_items)
            self.assertEqual(db.execute('SELECT billed_user_count FROM hq_clients WHERE id = ?', (cid,)).fetchone()[0], 70)

    def test_delete_invoice_releases_billed_users(self):
        self.login_admin()
        cid = self._add_client('Deletable', 12, 5000)
        self.client.post('/hq/invoices/new', data={
            'client_id': cid, 'sub_mode': 'full', 'sub_qty': '12', 'sub_unit': '5000'})
        with self.app.app_context():
            db = get_db()
            inv_id = db.execute('SELECT id FROM hq_invoices WHERE client_id = ? ORDER BY id DESC',
                                (cid,)).fetchone()['id']
            self.assertEqual(db.execute('SELECT billed_user_count FROM hq_clients WHERE id = ?', (cid,)).fetchone()[0], 12)
        r = self.client.post(f'/hq/invoices/{inv_id}/delete', follow_redirects=False)
        self.assertIn(r.status_code, (302, 303))
        with self.app.app_context():
            db = get_db()
            self.assertIsNone(db.execute('SELECT id FROM hq_invoices WHERE id = ?', (inv_id,)).fetchone())
            self.assertEqual(db.execute('SELECT COUNT(*) FROM hq_invoice_items WHERE invoice_id = ?', (inv_id,)).fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT billed_user_count FROM hq_clients WHERE id = ?', (cid,)).fetchone()[0], 0)

    def test_deleting_a_voided_invoice_does_not_double_release(self):
        self.login_admin()
        cid = self._add_client('Voidy', 12, 5000)
        self.client.post('/hq/invoices/new', data={
            'client_id': cid, 'sub_mode': 'full', 'sub_qty': '12', 'sub_unit': '5000'})
        with self.app.app_context():
            inv_id = get_db().execute('SELECT id FROM hq_invoices WHERE client_id = ? ORDER BY id DESC',
                                      (cid,)).fetchone()['id']
        self.client.post(f'/hq/invoices/{inv_id}/void')
        with self.app.app_context():
            self.assertEqual(get_db().execute('SELECT billed_user_count FROM hq_clients WHERE id = ?', (cid,)).fetchone()[0], 0)
        self.client.post(f'/hq/invoices/{inv_id}/delete')
        with self.app.app_context():
            db = get_db()
            self.assertIsNone(db.execute('SELECT id FROM hq_invoices WHERE id = ?', (inv_id,)).fetchone())
            self.assertEqual(db.execute('SELECT billed_user_count FROM hq_clients WHERE id = ?', (cid,)).fetchone()[0], 0)

    def test_member_count_endpoint_is_token_guarded(self):
        os.environ['HQ_SYNC_TOKEN'] = 'sync-secret'
        try:
            with self.app.app_context():
                db = get_db()
                db.execute("INSERT INTO members (member_number, first_name, last_name, email, phone, "
                           "status, monthly_savings, total_savings, date_joined) "
                           "VALUES ('HQ/SYNC/1','A','B','sync@x.com','080','active',0,0,'2024-01-01')")
                db.commit()
            self.assertEqual(self.client.get('/api/hq/member-count').status_code, 403)
            r = self.client.get('/api/hq/member-count', headers={'X-HQ-Token': 'sync-secret'})
            self.assertEqual(r.status_code, 200)
            body = r.get_json()
            self.assertTrue(body['success'])
            self.assertGreaterEqual(body['active_members'], 1)
        finally:
            os.environ.pop('HQ_SYNC_TOKEN', None)

    def test_billing_settings_saved_and_pdf_renders(self):
        self.login_admin()
        self.client.post('/hq/billing-settings', data={
            'hq_business_name': 'Adekail Professional Services',
            'hq_payment_instructions': 'Kuda Bank 3000428469'})
        with self.app.app_context():
            v = get_db().execute("SELECT value FROM settings WHERE key = 'hq_business_name'").fetchone()
            self.assertEqual(v['value'], 'Adekail Professional Services')
        cid = self._add_client('Delta', 4, 5000)
        self.client.post('/hq/invoices/new', data={
            'client_id': cid, 'sub_mode': 'full', 'sub_qty': '4', 'sub_unit': '5000'})
        with self.app.app_context():
            inv_id = get_db().execute('SELECT id FROM hq_invoices WHERE client_id = ? ORDER BY id DESC',
                                      (cid,)).fetchone()['id']
        r = self.client.get(f'/hq/invoices/{inv_id}.pdf')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content_type, 'application/pdf')


if __name__ == '__main__':
    unittest.main()
