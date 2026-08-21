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

    def test_net_off_waterfall(self):
        from ctas_engine import net_off_waterfall
        wf = net_off_waterfall(100000, 60000, 30000, 0)
        self.assertEqual((wf['from_savings'], wf['from_shares'], wf['from_other'], wf['write_off']),
                         (60000.0, 30000.0, 0.0, 10000.0))
        wf2 = net_off_waterfall(100000, 60000, 30000, 15000)
        self.assertEqual((wf2['from_other'], wf2['write_off']), (10000.0, 0.0))   # other capped to remainder
        wf3 = net_off_waterfall(50000, 80000, 0, 0)
        self.assertEqual((wf3['from_savings'], wf3['write_off']), (50000.0, 0.0))  # savings cover it

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
            for _ in range(4):   # submitted -> eligible -> finance_reviewed -> approved -> enrolled
                self.client.post(f'/ctas/subscriptions/{sid}/act', data={'action': 'advance'})
            with self.app.app_context():
                self.assertEqual(get_db().execute("SELECT status FROM ctas_subscriptions WHERE id=?",
                                                  (sid,)).fetchone()['status'], 'enrolled')
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


    def _make_recovery_sub(self, mnum, savings=0, shares=0, target=100000, monthly=50000):
        with self.app.app_context():
            db = get_db()
            db.execute("INSERT INTO members (member_number, first_name, last_name, status, total_savings, "
                       "shares_value, monthly_savings, date_joined) VALUES (?, 'R','T','active',?,?,5000,'2024-01-01')",
                       (mnum, savings, shares))
            mid = db.execute("SELECT id FROM members WHERE member_number=?", (mnum,)).fetchone()['id']
            db.execute("INSERT INTO ctas_cycles (name, status, duration_months, monthly_capacity) "
                       "VALUES (?, 'active', 2, 1)", (f'Cyc{mnum}',))
            cid = db.execute("SELECT id FROM ctas_cycles WHERE name=?", (f'Cyc{mnum}',)).fetchone()['id']
            db.execute("INSERT INTO ctas_subscriptions (cycle_id, member_id, target_amount, tenure_months, "
                       "monthly_deduction, status, total_recovered, outstanding, payout_month) "
                       "VALUES (?,?,?,2,?,'active_recovery',0,?,2)", (cid, mid, target, monthly, target))
            db.commit()
            sid = db.execute("SELECT id FROM ctas_subscriptions WHERE cycle_id=?", (cid,)).fetchone()['id']
        return cid, mid, sid

    def _cleanup_sub(self, cid, mid, sid, base_sav=0, base_shr=0):
        with self.app.app_context():
            db = get_db()
            for mod in ('ctas_recovery', 'ctas_exit'):
                for r in db.execute("SELECT id FROM journal_entries WHERE source_module=?", (mod,)).fetchall():
                    db.execute("DELETE FROM journal_lines WHERE entry_id=?", (r['id'],))
                    db.execute("DELETE FROM journal_entries WHERE id=?", (r['id'],))
            db.execute("DELETE FROM ctas_exceptions WHERE subscription_id=?", (sid,))
            db.execute("DELETE FROM ctas_payroll_lines WHERE subscription_id=?", (sid,))
            db.execute("DELETE FROM ctas_payroll_batches WHERE cycle_id=?", (cid,))
            db.execute("DELETE FROM ctas_subscriptions WHERE cycle_id=?", (cid,))
            db.execute("DELETE FROM ctas_cycles WHERE id=?", (cid,))
            db.execute("DELETE FROM members WHERE id=?", (mid,))
            db.execute("DELETE FROM settings WHERE key='ctas_enabled'")
            db.commit()

    def test_missed_deduction_raises_arrears_and_an_exception(self):
        import io
        cid, mid, sid = self._make_recovery_sub('CTAS/AR/1')
        try:
            body = f"subscription_id,actual_amount\n{sid},0\n".encode('utf-8')   # missed
            self.client.post(f'/ctas/cycles/{cid}/payroll/import',
                data={'month': '1', 'file': (io.BytesIO(body), 'm.csv')},
                content_type='multipart/form-data', follow_redirects=True)
            with self.app.app_context():
                db = get_db()
                sub = db.execute("SELECT arrears_amount FROM ctas_subscriptions WHERE id=?", (sid,)).fetchone()
                self.assertAlmostEqual(float(sub['arrears_amount']), 50000.0, places=2)
                ex = db.execute("SELECT COUNT(*) FROM ctas_exceptions WHERE subscription_id=? "
                                "AND case_type='missed_deduction'", (sid,)).fetchone()[0]
                self.assertEqual(ex, 1)
        finally:
            self._cleanup_sub(cid, mid, sid)

    def test_exit_settlement_net_off_waterfall_posts_gl(self):
        # outstanding 100k; savings 40k + shares 20k -> 40k write-off.
        cid, mid, sid = self._make_recovery_sub('CTAS/EX/1', savings=40000, shares=20000)
        try:
            self.client.post(f'/ctas/subscriptions/{sid}/exit',
                             data={'other_recovery': '0', 'reason': 'resignation'}, follow_redirects=True)
            with self.app.app_context():
                db = get_db()
                sub = db.execute("SELECT status, outstanding FROM ctas_subscriptions WHERE id=?", (sid,)).fetchone()
                self.assertEqual(sub['status'], 'completed')
                self.assertAlmostEqual(float(sub['outstanding'] or 0), 0.0, places=2)
                m = db.execute("SELECT total_savings, shares_value FROM members WHERE id=?", (mid,)).fetchone()
                self.assertAlmostEqual(float(m['total_savings']), 0.0, places=2)   # 40k drawn
                self.assertAlmostEqual(float(m['shares_value']), 0.0, places=2)     # 20k drawn

                def gl(acct, col):
                    return db.execute(f"SELECT COALESCE(SUM(jl.{col}),0) FROM journal_lines jl "
                                      "JOIN journal_entries je ON je.id=jl.entry_id "
                                      "WHERE je.source_module='ctas_exit' AND jl.account_code=?", (acct,)).fetchone()[0]
                self.assertAlmostEqual(float(gl('2000', 'debit')), 40000.0, places=2)    # member deposits
                self.assertAlmostEqual(float(gl('3200', 'debit')), 20000.0, places=2)    # share capital
                self.assertAlmostEqual(float(gl('5150', 'debit')), 40000.0, places=2)    # write-off
                self.assertAlmostEqual(float(gl('1150', 'credit')), 100000.0, places=2)  # advance cleared
                ex = db.execute("SELECT COUNT(*) FROM ctas_exceptions WHERE subscription_id=? "
                                "AND case_type='exit_recovery'", (sid,)).fetchone()[0]
                self.assertEqual(ex, 1)
        finally:
            self._cleanup_sub(cid, mid, sid)

    def test_payroll_recovery_posts_gl_is_idempotent_and_completes(self):
        import io
        with self.app.app_context():
            db = get_db()
            db.execute("INSERT INTO members (member_number, first_name, last_name, status, "
                       "total_savings, monthly_savings, date_joined) "
                       "VALUES ('CTAS/R/1','Rec','Test','active',0,5000,'2024-01-01')")
            db.commit()
            mid = db.execute("SELECT id FROM members WHERE member_number='CTAS/R/1'").fetchone()['id']
            db.execute("INSERT INTO ctas_cycles (name, status, duration_months, monthly_capacity) "
                       "VALUES ('RecCycle','active',2,1)")
            cid = db.execute("SELECT id FROM ctas_cycles WHERE name='RecCycle'").fetchone()['id']
            db.execute("INSERT INTO ctas_subscriptions (cycle_id, member_id, target_amount, tenure_months, "
                       "monthly_deduction, status, total_recovered, outstanding, payout_month) "
                       "VALUES (?,?,100000,2,50000,'active_recovery',0,100000,2)", (cid, mid))
            db.commit()
            sid = db.execute("SELECT id FROM ctas_subscriptions WHERE cycle_id=?", (cid,)).fetchone()['id']

        def _import(month, amount):
            body = f"subscription_id,actual_amount\n{sid},{amount}\n".encode('utf-8')
            return self.client.post(f'/ctas/cycles/{cid}/payroll/import',
                data={'month': str(month), 'file': (io.BytesIO(body), f'm{month}.csv')},
                content_type='multipart/form-data', follow_redirects=True)

        try:
            # Export lists the subscription in recovery.
            exp = self.client.get(f'/ctas/cycles/{cid}/payroll/export?month=1')
            self.assertEqual(exp.status_code, 200)
            self.assertIn(str(sid).encode(), exp.data)
            # Month 1: post 50,000.
            _import(1, 50000)
            with self.app.app_context():
                db = get_db()
                sub = db.execute("SELECT * FROM ctas_subscriptions WHERE id=?", (sid,)).fetchone()
                self.assertAlmostEqual(float(sub['total_recovered']), 50000.0, places=2)
                self.assertEqual(sub['status'], 'active_recovery')
                adv_cr = db.execute("SELECT COALESCE(SUM(credit),0) FROM journal_lines "
                                    "WHERE account_code='1150'").fetchone()[0]
                self.assertAlmostEqual(float(adv_cr), 50000.0, places=2)   # advance reduced
            # Re-import month 1: idempotent (no double post).
            _import(1, 50000)
            with self.app.app_context():
                sub = get_db().execute("SELECT total_recovered FROM ctas_subscriptions WHERE id=?", (sid,)).fetchone()
                self.assertAlmostEqual(float(sub['total_recovered']), 50000.0, places=2)
            # Month 2: final 50,000 -> completed.
            _import(2, 50000)
            with self.app.app_context():
                sub = get_db().execute("SELECT * FROM ctas_subscriptions WHERE id=?", (sid,)).fetchone()
                self.assertAlmostEqual(float(sub['total_recovered']), 100000.0, places=2)
                self.assertAlmostEqual(float(sub['outstanding'] or 0), 0.0, places=2)
                self.assertEqual(sub['status'], 'completed')
        finally:
            with self.app.app_context():
                db = get_db()
                for r in db.execute("SELECT id FROM journal_entries WHERE source_module='ctas_recovery'").fetchall():
                    db.execute("DELETE FROM journal_lines WHERE entry_id=?", (r['id'],))
                    db.execute("DELETE FROM journal_entries WHERE id=?", (r['id'],))
                db.execute("DELETE FROM ctas_payroll_lines WHERE subscription_id=?", (sid,))
                db.execute("DELETE FROM ctas_payroll_batches WHERE cycle_id=?", (cid,))
                db.execute("DELETE FROM ctas_subscriptions WHERE cycle_id=?", (cid,))
                db.execute("DELETE FROM ctas_cycles WHERE id=?", (cid,))
                db.execute("DELETE FROM members WHERE id=?", (mid,))
                db.execute("DELETE FROM settings WHERE key='ctas_enabled'")
                db.commit()


class CtasMemberPortalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app_module.app
        cls.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)

    def setUp(self):
        from werkzeug.security import generate_password_hash
        from blueprints.ctas import set_ctas_enabled
        self.member = self.app.test_client()
        self.admin = self.app.test_client()
        with self.app.app_context():
            db = get_db()
            set_ctas_enabled(db, True)
            db.execute("INSERT INTO members (member_number, first_name, last_name, email, status, "
                       "total_savings, monthly_savings, date_joined) "
                       "VALUES ('CTAS/M/1','Mem','Ber','ctasmem@x.com','active',500000,5000,'2024-01-01')")
            db.execute("INSERT INTO users (username, password_hash, role, email, is_active, must_change_password) "
                       "VALUES ('ctasmem', ?, 'member', 'ctasmem@x.com', 1, 0)",
                       (generate_password_hash('TestMember123'),))
            db.execute("INSERT INTO ctas_cycles (name, status, duration_months, monthly_capacity, "
                       "affordability_method, savings_multiple) VALUES ('MemCycle','open',6,2,'savings',3)")
            db.commit()
            self.cid = db.execute("SELECT id FROM ctas_cycles WHERE name='MemCycle'").fetchone()['id']
            self.uid = db.execute("SELECT id FROM users WHERE username='ctasmem'").fetchone()['id']
        self.member.post('/login', data={'username': 'ctasmem', 'password': 'TestMember123'})
        self.admin.post('/login', data={'username': 'admin', 'password': 'TestAdmin123'})

    def tearDown(self):
        with self.app.app_context():
            db = get_db()
            db.execute("DELETE FROM notifications WHERE user_id = ?", (self.uid,))
            db.execute("DELETE FROM ctas_subscriptions WHERE cycle_id = ?", (self.cid,))
            db.execute("DELETE FROM ctas_cycles WHERE id = ?", (self.cid,))
            db.execute("DELETE FROM users WHERE username = 'ctasmem'")
            db.execute("DELETE FROM members WHERE member_number = 'CTAS/M/1'")
            db.execute("DELETE FROM settings WHERE key = 'ctas_enabled'")
            db.commit()

    def test_member_applies_and_is_notified_on_enrol(self):
        self.assertEqual(self.member.get('/my-ctas').status_code, 200)
        # Terms + signature are required.
        blocked = self.member.post('/my-ctas/apply', data={
            'cycle_id': str(self.cid), 'target_amount': '200000', 'tenure_months': '4'},
            follow_redirects=False)
        self.assertIn(blocked.status_code, (302, 303))
        with self.app.app_context():
            self.assertIsNone(get_db().execute("SELECT id FROM ctas_subscriptions WHERE cycle_id=?",
                                               (self.cid,)).fetchone())     # not created without terms
        # Apply properly.
        r = self.member.post('/my-ctas/apply', data={
            'cycle_id': str(self.cid), 'target_amount': '200000', 'tenure_months': '4',
            'terms': '1', 'signature_name': 'Mem Ber'}, follow_redirects=False)
        self.assertIn(r.status_code, (302, 303))
        with self.app.app_context():
            sub = get_db().execute("SELECT * FROM ctas_subscriptions WHERE cycle_id = ?", (self.cid,)).fetchone()
            self.assertIsNotNone(sub)
            self.assertEqual(sub['status'], 'submitted')
            self.assertEqual(sub['terms_accepted'], 1)
            self.assertEqual(sub['signature_name'], 'Mem Ber')
            self.assertAlmostEqual(float(sub['monthly_deduction']), 50000.0, places=2)   # 200k/4
            sid = sub['id']
        # Admin advances through the chain -> member notified at enrolment.
        for _ in range(4):
            self.admin.post(f'/ctas/subscriptions/{sid}/act', data={'action': 'advance'})
        with self.app.app_context():
            db = get_db()
            self.assertEqual(db.execute("SELECT status FROM ctas_subscriptions WHERE id=?", (sid,)).fetchone()['status'], 'enrolled')
            n = db.execute("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND title LIKE '%enrolled%'",
                           (self.uid,)).fetchone()[0]
            self.assertGreaterEqual(n, 1)


if __name__ == '__main__':
    unittest.main()
