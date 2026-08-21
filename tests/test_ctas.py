import os
import unittest

TEST_DB = os.path.abspath('.test-ctas.db')
os.environ.setdefault('SECRET_KEY', 'test-secret-ctas')
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
from blueprints.ctas import ctas_enabled  # noqa: E402


class CtasFeatureFlagTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app_module.app
        cls.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)

    def setUp(self):
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db = get_db()
            db.execute("DELETE FROM settings WHERE key = 'ctas_enabled'")
            db.commit()
        os.environ.pop('HQ_SYNC_TOKEN', None)

    def _ctas_accounts(self):
        with self.app.app_context():
            return sorted(r[0] for r in get_db().execute(
                "SELECT code FROM accounts WHERE code IN ('1150', '4150')").fetchall())

    def test_ctas_is_off_by_default(self):
        # The meaningful invariant: the module is inert unless switched on.
        # (GL accounts are seeded only at enable; on a shared test DB another
        # test may already have enabled it, so we assert the flag, not accounts.)
        with self.app.app_context():
            self.assertFalse(ctas_enabled())

    def test_hq_can_enable_ctas_on_request_which_seeds_accounts(self):
        os.environ['HQ_SYNC_TOKEN'] = 'feature-token'
        # Guarded.
        self.assertEqual(self.client.post('/api/hq/set-feature',
                                          json={'feature': 'ctas', 'enabled': True}).status_code, 403)
        # Enable.
        r = self.client.post('/api/hq/set-feature', json={'feature': 'ctas', 'enabled': True},
                             headers={'X-HQ-Token': 'feature-token'})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()['enabled'])
        with self.app.app_context():
            self.assertTrue(ctas_enabled())
        self.assertEqual(self._ctas_accounts(), ['1150', '4150'])   # accounts seeded on enable
        # Disable.
        r = self.client.post('/api/hq/set-feature', json={'feature': 'ctas', 'enabled': False},
                             headers={'X-HQ-Token': 'feature-token'})
        self.assertEqual(r.status_code, 200)
        with self.app.app_context():
            self.assertFalse(ctas_enabled())

    def test_unknown_feature_is_rejected(self):
        os.environ['HQ_SYNC_TOKEN'] = 'feature-token'
        r = self.client.post('/api/hq/set-feature', json={'feature': 'nope', 'enabled': True},
                             headers={'X-HQ-Token': 'feature-token'})
        self.assertEqual(r.status_code, 400)


class CtasEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app_module.app

    def _cycle(self, **over):
        base = {
            'id': 1, 'status': 'closed', 'duration_months': 6, 'monthly_capacity': 1,
            'earliest_payout_month': 2, 'admin_fee_flat': 1500, 'admin_fee_percentage': 0.015,
            'admin_fee_cap': 3000, 'admin_fee_threshold': 1000000,
            'affordability_method': 'savings', 'affordability_ratio': 0.5, 'savings_multiple': 3,
        }
        base.update(over)
        return base

    def test_monthly_deduction_and_admin_fee(self):
        from ctas_engine import monthly_deduction, calculate_admin_fee
        self.assertAlmostEqual(monthly_deduction(300000, 6), 50000.0, places=2)
        c = self._cycle()
        self.assertAlmostEqual(calculate_admin_fee(c, 500000), 1500.0)          # flat below threshold
        self.assertAlmostEqual(calculate_admin_fee(c, 2000000), 3000.0)         # capped percentage

    def test_affordability_savings_basis_default(self):
        from ctas_engine import affordability
        c = self._cycle(affordability_method='savings', savings_multiple=3)
        member = {'total_savings': 100000, 'annual_salary': 0, 'status': 'active'}
        ok, method, _ = affordability(None, member, c, 250000, 5)
        self.assertTrue(ok); self.assertEqual(method, 'savings')                # 250k <= 3x100k
        ok, _, _ = affordability(None, member, c, 400000, 5)
        self.assertFalse(ok)                                                    # 400k > 300k

    def test_affordability_salary_basis(self):
        from ctas_engine import affordability
        c = self._cycle(affordability_method='salary', affordability_ratio=0.5)
        salaried = {'total_savings': 0, 'annual_salary': 1200000, 'status': 'active'}  # 100k/mo, cap 50k
        self.assertTrue(affordability(None, salaried, c, 200000, 5)[0])          # 40k/mo <= 50k
        self.assertFalse(affordability(None, salaried, c, 400000, 5)[0])         # 80k/mo > 50k
        no_salary = {'total_savings': 500000, 'annual_salary': 0, 'status': 'active'}
        self.assertFalse(affordability(None, no_salary, c, 100000, 5)[0])        # no salary on record

    def test_affordability_manual_always_defers(self):
        from ctas_engine import affordability
        c = self._cycle(affordability_method='manual')
        member = {'total_savings': 0, 'annual_salary': 0, 'status': 'active'}
        ok, method, _ = affordability(None, member, c, 999999, 6)
        self.assertTrue(ok); self.assertEqual(method, 'manual')

    def test_ballot_assigns_unique_months_and_is_seed_deterministic(self):
        from ctas_engine import assign_payout_months
        c = self._cycle(duration_months=6, earliest_payout_month=2, monthly_capacity=1)
        ids = [10, 11, 12, 13, 14]                          # 5 members, months 2..6 (5 slots)
        a1 = assign_payout_months(ids, c, seed='abc')
        a2 = assign_payout_months(ids, c, seed='abc')
        self.assertEqual(a1, a2)                            # deterministic per seed
        self.assertEqual(sorted(a1.values()), [2, 3, 4, 5, 6])   # each month once
        self.assertEqual(set(a1.keys()), set(ids))
        with self.assertRaises(ValueError):                # 6 members, only 5 slots
            assign_payout_months(ids + [15], c, seed='abc')

    def test_eligibility_blocks_second_active_subscription(self):
        from database import get_db
        from ctas_engine import check_eligibility
        with self.app.app_context():
            db = get_db()
            db.execute("INSERT INTO members (member_number, first_name, last_name, status, "
                       "total_savings, monthly_savings, date_joined) "
                       "VALUES ('CTAS/EL/1','El','Test','active',300000,5000,'2024-01-01')")
            mid = db.execute("SELECT id FROM members WHERE member_number='CTAS/EL/1'").fetchone()['id']
            db.execute("INSERT INTO ctas_cycles (name, status, duration_months, monthly_capacity, "
                       "earliest_payout_month, affordability_method, savings_multiple) "
                       "VALUES ('EligCycle','open',6,3,2,'savings',3)")
            cid = db.execute("SELECT id FROM ctas_cycles WHERE name='EligCycle'").fetchone()['id']
            member = db.execute("SELECT * FROM members WHERE id=?", (mid,)).fetchone()
            cycle = db.execute("SELECT * FROM ctas_cycles WHERE id=?", (cid,)).fetchone()
            try:
                r = check_eligibility(db, member, cycle, 200000, 5)     # 200k <= 3x300k
                self.assertTrue(r['eligible'], r['reasons'])
                # Give the member an active subscription in another cycle, then re-check.
                db.execute("INSERT INTO ctas_cycles (name, status) VALUES ('Other','open')")
                ocid = db.execute("SELECT id FROM ctas_cycles WHERE name='Other'").fetchone()['id']
                db.execute("INSERT INTO ctas_subscriptions (cycle_id, member_id, target_amount, "
                           "tenure_months, status) VALUES (?,?,100000,5,'enrolled')", (ocid, mid))
                db.commit()
                r2 = check_eligibility(db, member, cycle, 200000, 5)
                self.assertFalse(r2['eligible'])
                self.assertTrue(any('active CTAS subscription' in x for x in r2['reasons']))
            finally:
                db.execute("DELETE FROM ctas_subscriptions WHERE member_id=?", (mid,))
                db.execute("DELETE FROM ctas_cycles WHERE name IN ('EligCycle','Other')")
                db.execute("DELETE FROM members WHERE id=?", (mid,))
                db.commit()


class CtasAdminFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app_module.app
        cls.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)

    def setUp(self):
        from blueprints.ctas import set_ctas_enabled
        self.client = self.app.test_client()
        with self.app.app_context():
            db = get_db()
            set_ctas_enabled(db, True)
            db.commit()
        self.client.post('/login', data={'username': 'admin', 'password': 'TestAdmin123'})

    def tearDown(self):
        with self.app.app_context():
            db = get_db()
            db.execute("DELETE FROM settings WHERE key = 'ctas_enabled'")
            db.commit()

    def test_full_cycle_payout_posts_to_gl(self):
        with self.app.app_context():
            db = get_db()
            db.execute("INSERT INTO members (member_number, first_name, last_name, status, "
                       "total_savings, monthly_savings, date_joined) "
                       "VALUES ('CTAS/F/1','Ada','Flow','active',500000,5000,'2024-01-01')")
            db.commit()
            mid = db.execute("SELECT id FROM members WHERE member_number='CTAS/F/1'").fetchone()['id']
        try:
            self.client.post('/ctas/cycles', data={
                'name': 'FlowCycle', 'duration_months': '4', 'monthly_capacity': '2',
                'earliest_payout_month': '2', 'affordability_method': 'savings',
                'savings_multiple': '3', 'admin_fee_flat': '1500'})
            with self.app.app_context():
                cid = get_db().execute("SELECT id FROM ctas_cycles WHERE name='FlowCycle'").fetchone()['id']
            self.client.post(f'/ctas/cycles/{cid}/transition', data={'to': 'open'})
            self.client.post(f'/ctas/cycles/{cid}/subscriptions',
                             data={'member_id': str(mid), 'target_amount': '300000', 'tenure_months': '4'})
            with self.app.app_context():
                sub = get_db().execute("SELECT * FROM ctas_subscriptions WHERE cycle_id=?", (cid,)).fetchone()
                sid = sub['id']
                self.assertEqual(sub['status'], 'submitted')
                self.assertAlmostEqual(float(sub['monthly_deduction']), 75000.0, places=2)   # 300k/4
                self.assertAlmostEqual(float(sub['admin_fee']), 1500.0, places=2)
            self.client.post(f'/ctas/subscriptions/{sid}/act', data={'action': 'enroll'})
            self.client.post(f'/ctas/cycles/{cid}/transition', data={'to': 'closed'})
            self.client.post(f'/ctas/cycles/{cid}/transition', data={'to': 'ready_for_ballot'})
            self.client.post(f'/ctas/cycles/{cid}/ballot', data={})
            with self.app.app_context():
                sub = get_db().execute("SELECT * FROM ctas_subscriptions WHERE id=?", (sid,)).fetchone()
                self.assertEqual(sub['status'], 'scheduled')
                self.assertIsNotNone(sub['payout_month'])
            self.client.post(f'/ctas/subscriptions/{sid}/payout', data={})
            with self.app.app_context():
                db = get_db()
                sub = db.execute("SELECT status FROM ctas_subscriptions WHERE id=?", (sid,)).fetchone()
                self.assertEqual(sub['status'], 'active_recovery')
                adv = db.execute("SELECT COALESCE(SUM(debit),0)-COALESCE(SUM(credit),0) "
                                 "FROM journal_lines WHERE account_code='1150'").fetchone()[0]
                self.assertAlmostEqual(float(adv), 300000.0, places=2)          # advance receivable
                fee = db.execute("SELECT COALESCE(SUM(credit),0) FROM journal_lines "
                                 "WHERE account_code='4150'").fetchone()[0]
                self.assertAlmostEqual(float(fee), 1500.0, places=2)            # admin fee income
        finally:
            with self.app.app_context():
                db = get_db()
                for r in db.execute("SELECT id FROM journal_entries WHERE source_module='ctas_payout'").fetchall():
                    db.execute("DELETE FROM journal_lines WHERE entry_id=?", (r['id'],))
                    db.execute("DELETE FROM journal_entries WHERE id=?", (r['id'],))
                db.execute("DELETE FROM ctas_subscriptions WHERE member_id=?", (mid,))
                db.execute("DELETE FROM ctas_ballot_runs WHERE cycle_id IN (SELECT id FROM ctas_cycles WHERE name='FlowCycle')")
                db.execute("DELETE FROM ctas_cycles WHERE name='FlowCycle'")
                db.execute("DELETE FROM members WHERE id=?", (mid,))
                db.execute("DELETE FROM settings WHERE key='ctas_enabled'")
                db.commit()


if __name__ == '__main__':
    unittest.main()
