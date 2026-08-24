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
from blueprints.ctas import ctas_enabled, _store_token  # noqa: E402


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

    def test_plan_target_and_generic_periods(self):
        from ctas_engine import compute_target, cycle_periods, total_capacity
        self.assertEqual(compute_target(50000, 12), 600000.0)
        self.assertEqual(compute_target(10000, 20), 200000.0)          # weekly plan
        self.assertEqual(cycle_periods({'periods': 20, 'duration_months': 6}), 20)   # periods wins
        self.assertEqual(cycle_periods({'periods': None, 'duration_months': 6}), 6)  # falls back
        # 20 periods, earliest position 2, 1/period -> 19 slots
        self.assertEqual(total_capacity({'periods': 20, 'earliest_payout_month': 2, 'monthly_capacity': 1}), 19)

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

    def test_period_due_dates_by_frequency(self):
        from ctas_engine import period_due_date
        self.assertEqual(str(period_due_date('2026-01-15', 'monthly', 1)), '2026-01-15')
        self.assertEqual(str(period_due_date('2026-01-15', 'monthly', 3)), '2026-03-15')
        self.assertEqual(str(period_due_date('2026-01-15', 'weekly', 3)), '2026-01-29')
        self.assertEqual(str(period_due_date('2026-01-15', 'fortnightly', 3)), '2026-02-12')
        # month-end is clamped, not rolled over
        self.assertEqual(str(period_due_date('2026-01-31', 'monthly', 2)), '2026-02-28')

    def test_schedule_status_ladder(self):
        from datetime import date
        from ctas_engine import schedule_status
        due = '2026-06-10'
        # before the due date
        self.assertEqual(schedule_status(due, 5000, 0, 7, date(2026, 6, 1)), 'pending')
        # on the due date, then within grace, then past grace
        self.assertEqual(schedule_status(due, 5000, 0, 7, date(2026, 6, 10)), 'due')
        self.assertEqual(schedule_status(due, 5000, 0, 7, date(2026, 6, 15)), 'grace')
        self.assertEqual(schedule_status(due, 5000, 0, 7, date(2026, 6, 20)), 'late')
        # paid in full / part
        self.assertEqual(schedule_status(due, 5000, 5000, 7, date(2026, 6, 20)), 'paid')
        self.assertEqual(schedule_status(due, 5000, 2000, 7, date(2026, 6, 20)), 'partial')

    def test_build_schedule_covers_every_period(self):
        from ctas_engine import build_schedule
        rows = build_schedule({'periods': 4, 'duration_months': 4, 'frequency': 'monthly',
                               'start_date': '2026-03-01', 'contribution_amount': 50000})
        self.assertEqual(len(rows), 4)
        self.assertEqual([r[0] for r in rows], [1, 2, 3, 4])
        self.assertEqual(str(rows[0][1]), '2026-03-01')
        self.assertEqual(str(rows[3][1]), '2026-06-01')
        self.assertTrue(all(r[2] == 50000 for r in rows))

    def test_fully_subscribed_cycle_needs_no_cooperative_money(self):
        from ctas_engine import liquidity_projection
        # 12 members x 50,000 monthly, one 600,000 payout per period: inflow
        # exactly matches each payout, so the pool funds itself.
        cyc = {'contribution_amount': 50000, 'periods': 12, 'duration_months': 12,
               'monthly_capacity': 1, 'earliest_payout_month': 1}
        rows, s = liquidity_projection(cyc, member_count=12)
        self.assertEqual(s['target'], 600000.0)
        self.assertEqual(s['funding_gap_per_payout_period'], 0.0)
        self.assertEqual(s['shortfall'], 0.0)
        self.assertTrue(all(r['balance'] >= 0 for r in rows))

    def test_under_subscribed_cycle_shows_the_cooperative_guarantee(self):
        from ctas_engine import liquidity_projection
        # The spec's worked example: only 8 of 12 places taken.
        # 8 x 50,000 = 400,000 in, but the payout is 600,000 -> 200,000 gap.
        cyc = {'contribution_amount': 50000, 'periods': 12, 'duration_months': 12,
               'monthly_capacity': 1, 'earliest_payout_month': 1}
        rows, s = liquidity_projection(cyc, member_count=8)
        self.assertEqual(s['inflow_per_period'], 400000.0)
        self.assertEqual(s['funding_gap_per_payout_period'], 200000.0)
        self.assertGreater(s['shortfall'], 0)                 # co-op must fund it
        self.assertEqual(s['status'], 'red')
        # Approving enough support clears the shortfall.
        rows2, s2 = liquidity_projection(cyc, member_count=8, support=s['shortfall'], buffer_amount=0)
        self.assertEqual(s2['shortfall'], 0.0)
        self.assertNotEqual(s2['status'], 'red')

    def test_projection_uses_the_real_ballot_once_it_has_run(self):
        from ctas_engine import liquidity_projection
        cyc = {'contribution_amount': 50000, 'periods': 4, 'duration_months': 4,
               'monthly_capacity': 1, 'earliest_payout_month': 1}
        # Everyone collects in period 1 -> a big early outflow.
        rows, s = liquidity_projection(cyc, member_count=4, payouts_by_period={1: 4})
        self.assertEqual(rows[0]['payees'], 4)
        self.assertEqual(rows[1]['outflow'], 0)
        self.assertLess(rows[0]['balance'], 0)                # cannot fund it on day one
        self.assertGreater(s['shortfall'], 0)

    def test_granted_priority_gets_the_requested_position(self):
        from ctas_engine import assign_payout_months
        cyc = {'periods': 4, 'duration_months': 4, 'earliest_payout_month': 1, 'monthly_capacity': 1}
        out = assign_payout_months([10, 11, 12, 13], cyc, seed='s', priority={12: 1})
        self.assertEqual(out[12], 1)                              # got what was granted
        self.assertEqual(sorted(out.values()), [1, 2, 3, 4])      # others still balloted

    def test_oversubscribed_priority_is_settled_by_ballot_not_first_come(self):
        from ctas_engine import assign_payout_months
        cyc = {'periods': 4, 'duration_months': 4, 'earliest_payout_month': 1, 'monthly_capacity': 1}
        # Three members all want position 1; only one slot exists.
        out = assign_payout_months([10, 11, 12, 13], cyc, seed='seed-a',
                                   priority={10: 1, 11: 1, 12: 1})
        winners = [s for s, p in out.items() if p == 1]
        self.assertEqual(len(winners), 1)                         # exactly one wins
        self.assertIn(winners[0], (10, 11, 12))
        self.assertEqual(sorted(out.values()), [1, 2, 3, 4])       # everyone still placed
        # Same seed -> same outcome (auditable); a different seed may differ.
        again = assign_payout_months([10, 11, 12, 13], cyc, seed='seed-a',
                                     priority={10: 1, 11: 1, 12: 1})
        self.assertEqual(out, again)

    def test_priority_fee_lookup(self):
        from ctas_engine import priority_fee_for
        tiers = {1: 30000, 2: 25000, 3: 20000}
        self.assertEqual(priority_fee_for(tiers, 1), 30000.0)
        self.assertEqual(priority_fee_for(tiers, 9), 0.0)          # unpriced position

    def test_position_one_is_allowed_so_every_period_has_a_slot(self):
        from ctas_engine import total_capacity, assign_payout_months
        # 12 periods, 1 payout/period, starting at position 1 -> 12 slots for 12 members.
        cyc = {'periods': 12, 'earliest_payout_month': 1, 'monthly_capacity': 1, 'duration_months': 12}
        self.assertEqual(total_capacity(cyc), 12)
        ids = list(range(1, 13))
        assigned = assign_payout_months(ids, cyc, seed='s')
        self.assertEqual(sorted(assigned.values()), list(range(1, 13)))   # positions 1..12 all used

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

    def test_plan_creation_and_cycle_from_plan(self):
        self.client.post('/ctas/plans', data={
            'name': 'P600', 'contribution_amount': '50000', 'frequency': 'monthly',
            'periods': '12', 'monthly_capacity': '2', 'earliest_payout_month': '2',
            'affordability_method': 'savings'})
        try:
            with self.app.app_context():
                plan = get_db().execute("SELECT * FROM ctas_plans WHERE name='P600'").fetchone()
                self.assertIsNotNone(plan)
                self.assertAlmostEqual(float(plan['target_amount']), 600000.0, places=2)   # 50k x 12
                pid = plan['id']
            self.client.post('/ctas/cycles', data={'name': 'P600 Cyc', 'plan_id': str(pid)})
            with self.app.app_context():
                cyc = get_db().execute("SELECT * FROM ctas_cycles WHERE name='P600 Cyc'").fetchone()
                self.assertEqual(cyc['frequency'], 'monthly')
                self.assertEqual(cyc['periods'], 12)
                self.assertAlmostEqual(float(cyc['contribution_amount']), 50000.0, places=2)
                self.assertEqual(cyc['monthly_capacity'], 2)   # inherited from the plan
                self.assertEqual(cyc['plan_id'], pid)
        finally:
            with self.app.app_context():
                db = get_db()
                db.execute("DELETE FROM ctas_cycles WHERE name='P600 Cyc'")
                db.execute("DELETE FROM ctas_plans WHERE name='P600'")
                db.execute("DELETE FROM settings WHERE key='ctas_enabled'")
                db.commit()

    def test_enrolment_builds_schedule_and_contribution_marks_it_paid(self):
        import io
        with self.app.app_context():
            db = get_db()
            db.execute("INSERT INTO members (member_number, first_name, last_name, status, total_savings, "
                       "monthly_savings, date_joined) VALUES ('CTAS/SC/1','Sch','T','active',900000,5000,'2024-01-01')")
            db.commit()
            mid = db.execute("SELECT id FROM members WHERE member_number='CTAS/SC/1'").fetchone()['id']
        self.client.post('/ctas/cycles', data={
            'name': 'SchedCyc', 'contribution_amount': '50000', 'frequency': 'monthly',
            'periods': '4', 'start_date': '2026-03-01', 'grace_days': '7',
            'affordability_method': 'manual'})
        with self.app.app_context():
            cid = get_db().execute("SELECT id FROM ctas_cycles WHERE name='SchedCyc'").fetchone()['id']
        self.client.post(f'/ctas/cycles/{cid}/transition', data={'to': 'open'})
        self.client.post(f'/ctas/cycles/{cid}/subscriptions',
                         data={'member_id': str(mid), 'target_amount': '200000', 'tenure_months': '4'})
        with self.app.app_context():
            sid = get_db().execute("SELECT id FROM ctas_subscriptions WHERE cycle_id=?", (cid,)).fetchone()['id']
        try:
            # No schedule until the member is actually enrolled.
            with self.app.app_context():
                self.assertEqual(get_db().execute(
                    "SELECT COUNT(*) FROM ctas_schedule WHERE subscription_id=?", (sid,)).fetchone()[0], 0)
            for _ in range(4):                      # submitted -> ... -> enrolled
                self.client.post(f'/ctas/subscriptions/{sid}/act', data={'action': 'advance'})
            with self.app.app_context():
                rows = get_db().execute(
                    "SELECT * FROM ctas_schedule WHERE subscription_id=? ORDER BY period_number",
                    (sid,)).fetchall()
                self.assertEqual(len(rows), 4)                       # one row per period
                self.assertEqual(str(rows[0]['due_date'])[:10], '2026-03-01')
                self.assertEqual(str(rows[3]['due_date'])[:10], '2026-06-01')
                self.assertAlmostEqual(float(rows[0]['expected_amount']), 50000.0, places=2)

            # Ballot then contribute period 1 -> that schedule row is paid.
            # One member cannot fund their own 200k payout from 50k/period, so the
            # liquidity gate applies; accept the gap deliberately for this test.
            self.client.post(f'/ctas/cycles/{cid}/transition', data={'to': 'closed'})
            self.client.post(f'/ctas/cycles/{cid}/transition',
                             data={'to': 'ready_for_ballot', 'override_liquidity': '1'})
            self.client.post(f'/ctas/cycles/{cid}/ballot', data={})
            body = f"subscription_id,actual_amount\n{sid},50000\n".encode('utf-8')
            self.client.post(f'/ctas/cycles/{cid}/payroll/import',
                data={'month': '1', 'file': (io.BytesIO(body), 'p1.csv')},
                content_type='multipart/form-data', follow_redirects=True)
            with self.app.app_context():
                r1 = get_db().execute(
                    "SELECT * FROM ctas_schedule WHERE subscription_id=? AND period_number=1",
                    (sid,)).fetchone()
                self.assertAlmostEqual(float(r1['paid_amount']), 50000.0, places=2)
                self.assertEqual(r1['status'], 'paid')
        finally:
            with self.app.app_context():
                db = get_db()
                for r2 in db.execute("SELECT id FROM journal_entries WHERE source_module='ctas_contribution'").fetchall():
                    db.execute("DELETE FROM journal_lines WHERE entry_id=?", (r2['id'],))
                    db.execute("DELETE FROM journal_entries WHERE id=?", (r2['id'],))
                db.execute("DELETE FROM ctas_schedule WHERE cycle_id=?", (cid,))
                db.execute("DELETE FROM ctas_payroll_lines WHERE subscription_id=?", (sid,))
                db.execute("DELETE FROM ctas_payroll_batches WHERE cycle_id=?", (cid,))
                db.execute("DELETE FROM ctas_ballot_runs WHERE cycle_id=?", (cid,))
                db.execute("DELETE FROM ctas_subscriptions WHERE cycle_id=?", (cid,))
                db.execute("DELETE FROM ctas_cycles WHERE id=?", (cid,))
                db.execute("DELETE FROM members WHERE id=?", (mid,))
                db.execute("DELETE FROM settings WHERE key='ctas_enabled'")
                db.commit()

    def test_promote_generates_an_advert_with_real_figures(self):
        self.client.post('/ctas/cycles', data={'name': 'AdCyc', 'contribution_amount': '50000',
                                               'frequency': 'monthly', 'periods': '12'})
        try:
            with self.app.app_context():
                cid = get_db().execute("SELECT id FROM ctas_cycles WHERE name='AdCyc'").fetchone()['id']
            r = self.client.get(f'/ctas/cycles/{cid}/promote')
            self.assertEqual(r.status_code, 200)
            page = r.data.decode('utf-8', 'replace')
            self.assertIn('600,000', page)          # 50k x 12 target
            self.assertIn('50,000', page)           # contribution
            self.assertIn('{first_name}', page)     # per-member placeholder
            self.assertIn('{portal_link}', page)
        finally:
            with self.app.app_context():
                db = get_db()
                db.execute("DELETE FROM ctas_cycles WHERE name='AdCyc'")
                db.execute("DELETE FROM settings WHERE key='ctas_enabled'")
                db.commit()

    def test_liquidity_gate_blocks_ballot_until_support_or_override(self):
        with self.app.app_context():
            db = get_db()
            db.execute("INSERT INTO members (member_number, first_name, last_name, status, total_savings, "
                       "monthly_savings, date_joined) VALUES ('CTAS/LQ/1','Liq','T','active',900000,0,'2024-01-01')")
            db.commit()
            mid = db.execute("SELECT id FROM members WHERE member_number='CTAS/LQ/1'").fetchone()['id']
            # One member, four periods: contributions of 50k cannot fund a 200k payout.
            db.execute("INSERT INTO ctas_cycles (name, status, frequency, periods, duration_months, "
                       "contribution_amount, monthly_capacity, earliest_payout_month) "
                       "VALUES ('LiqCyc','closed','monthly',4,4,50000,1,1)")
            cid = db.execute("SELECT id FROM ctas_cycles WHERE name='LiqCyc'").fetchone()['id']
            db.execute("INSERT INTO ctas_subscriptions (cycle_id, member_id, target_amount, tenure_months, "
                       "monthly_deduction, status) VALUES (?,?,200000,4,50000,'enrolled')", (cid, mid))
            db.commit()
        try:
            # Blocked: projected shortfall, no support approved.
            self.client.post(f'/ctas/cycles/{cid}/transition', data={'to': 'ready_for_ballot'},
                             follow_redirects=True)
            with self.app.app_context():
                self.assertEqual(get_db().execute("SELECT status FROM ctas_cycles WHERE id=?",
                                                  (cid,)).fetchone()['status'], 'closed')
            # Approving support lets it through.
            self.client.post(f'/ctas/cycles/{cid}/liquidity',
                             data={'liquidity_reserve': '0', 'liquidity_support': '1000000',
                                   'liquidity_buffer': '0'})
            self.client.post(f'/ctas/cycles/{cid}/transition', data={'to': 'ready_for_ballot'},
                             follow_redirects=True)
            with self.app.app_context():
                self.assertEqual(get_db().execute("SELECT status FROM ctas_cycles WHERE id=?",
                                                  (cid,)).fetchone()['status'], 'ready_for_ballot')
        finally:
            with self.app.app_context():
                db = get_db()
                db.execute("DELETE FROM ctas_subscriptions WHERE cycle_id=?", (cid,))
                db.execute("DELETE FROM ctas_cycles WHERE id=?", (cid,))
                db.execute("DELETE FROM members WHERE id=?", (mid,))
                db.execute("DELETE FROM settings WHERE key='ctas_enabled'")
                db.commit()

    def test_liquidity_gate_can_be_overridden_deliberately(self):
        with self.app.app_context():
            db = get_db()
            db.execute("INSERT INTO members (member_number, first_name, last_name, status, total_savings, "
                       "monthly_savings, date_joined) VALUES ('CTAS/LQ/2','Liq','T2','active',900000,0,'2024-01-01')")
            db.commit()
            mid = db.execute("SELECT id FROM members WHERE member_number='CTAS/LQ/2'").fetchone()['id']
            db.execute("INSERT INTO ctas_cycles (name, status, frequency, periods, duration_months, "
                       "contribution_amount, monthly_capacity, earliest_payout_month) "
                       "VALUES ('LiqCyc2','closed','monthly',4,4,50000,1,1)")
            cid = db.execute("SELECT id FROM ctas_cycles WHERE name='LiqCyc2'").fetchone()['id']
            db.execute("INSERT INTO ctas_subscriptions (cycle_id, member_id, target_amount, tenure_months, "
                       "monthly_deduction, status) VALUES (?,?,200000,4,50000,'enrolled')", (cid, mid))
            db.commit()
        try:
            self.client.post(f'/ctas/cycles/{cid}/transition',
                             data={'to': 'ready_for_ballot', 'override_liquidity': '1'},
                             follow_redirects=True)
            with self.app.app_context():
                db = get_db()
                self.assertEqual(db.execute("SELECT status FROM ctas_cycles WHERE id=?",
                                            (cid,)).fetchone()['status'], 'ready_for_ballot')
                logged = db.execute("SELECT COUNT(*) FROM audit_log WHERE action = "
                                    "'CTAS_LIQUIDITY_OVERRIDE'").fetchone()[0]
                self.assertGreaterEqual(logged, 1)      # the decision is on record
        finally:
            with self.app.app_context():
                db = get_db()
                db.execute("DELETE FROM ctas_subscriptions WHERE cycle_id=?", (cid,))
                db.execute("DELETE FROM ctas_cycles WHERE id=?", (cid,))
                db.execute("DELETE FROM members WHERE id=?", (mid,))
                db.execute("DELETE FROM settings WHERE key='ctas_enabled'")
                db.commit()

    def test_priority_fee_is_charged_at_payout_to_its_own_income_account(self):
        with self.app.app_context():
            db = get_db()
            db.execute("INSERT INTO members (member_number, first_name, last_name, status, total_savings, "
                       "monthly_savings, date_joined) VALUES ('CTAS/PR/1','Pri','T','active',900000,0,'2024-01-01')")
            db.commit()
            mid = db.execute("SELECT id FROM members WHERE member_number='CTAS/PR/1'").fetchone()['id']
            db.execute("INSERT INTO ctas_cycles (name, status, frequency, periods, duration_months, "
                       "contribution_amount, monthly_capacity, earliest_payout_month, priority_enabled) "
                       "VALUES ('PrioCyc','balloted','monthly',4,4,50000,1,1,1)")
            cid = db.execute("SELECT id FROM ctas_cycles WHERE name='PrioCyc'").fetchone()['id']
            db.execute("INSERT INTO ctas_priority_fees (cycle_id, position, fee) VALUES (?, 1, 30000)", (cid,))
            db.execute("INSERT INTO ctas_subscriptions (cycle_id, member_id, target_amount, tenure_months, "
                       "monthly_deduction, admin_fee, status, contributed_total, advance_balance, "
                       "payout_month, is_priority, priority_fee, priority_status, requested_payout_month) "
                       "VALUES (?,?,200000,4,50000,1500,'scheduled',0,0,1,1,30000,'granted',1)", (cid, mid))
            db.commit()
            sid = db.execute("SELECT id FROM ctas_subscriptions WHERE cycle_id=?", (cid,)).fetchone()['id']
        try:
            self.client.post(f'/ctas/subscriptions/{sid}/payout', data={}, follow_redirects=True)
            with self.app.app_context():
                db = get_db()

                def gl(acct, col):
                    return float(db.execute(
                        f"SELECT COALESCE(SUM(jl.{col}),0) FROM journal_lines jl JOIN journal_entries je "
                        "ON je.id=jl.entry_id WHERE je.source_module='ctas_payout' AND jl.account_code=?",
                        (acct,)).fetchone()[0])
                self.assertAlmostEqual(gl('4160', 'credit'), 30000.0, places=2)   # priority fee income
                self.assertAlmostEqual(gl('4150', 'credit'), 1500.0, places=2)    # admin fee income
                # Member receives the target net of BOTH fees.
                self.assertAlmostEqual(gl('1000', 'credit'), 168500.0, places=2)  # 200k - 1.5k - 30k
        finally:
            with self.app.app_context():
                db = get_db()
                for r in db.execute("SELECT id FROM journal_entries WHERE source_module='ctas_payout'").fetchall():
                    db.execute("DELETE FROM journal_lines WHERE entry_id=?", (r['id'],))
                    db.execute("DELETE FROM journal_entries WHERE id=?", (r['id'],))
                db.execute("DELETE FROM ctas_priority_fees WHERE cycle_id=?", (cid,))
                db.execute("DELETE FROM ctas_subscriptions WHERE cycle_id=?", (cid,))
                db.execute("DELETE FROM ctas_cycles WHERE id=?", (cid,))
                db.execute("DELETE FROM members WHERE id=?", (mid,))
                db.execute("DELETE FROM settings WHERE key='ctas_enabled'")
                db.commit()

    def test_delete_cycle_removes_it_but_is_blocked_once_money_posted(self):
        # A fresh cycle with an application deletes cleanly.
        with self.app.app_context():
            db = get_db()
            db.execute("INSERT INTO members (member_number, first_name, last_name, status, total_savings, "
                       "monthly_savings, date_joined) VALUES ('CTAS/DL/1','Del','T','active',500000,5000,'2024-01-01')")
            db.commit()
            mid = db.execute("SELECT id FROM members WHERE member_number='CTAS/DL/1'").fetchone()['id']
        self.client.post('/ctas/cycles', data={'name': 'DelCyc', 'contribution_amount': '50000',
                                               'frequency': 'monthly', 'periods': '4'})
        with self.app.app_context():
            cid = get_db().execute("SELECT id FROM ctas_cycles WHERE name='DelCyc'").fetchone()['id']
        self.client.post(f'/ctas/cycles/{cid}/transition', data={'to': 'open'})
        self.client.post(f'/ctas/cycles/{cid}/subscriptions',
                         data={'member_id': str(mid), 'target_amount': '200000', 'tenure_months': '4'})
        try:
            r = self.client.post(f'/ctas/cycles/{cid}/delete', follow_redirects=False)
            self.assertIn(r.status_code, (302, 303))
            with self.app.app_context():
                db = get_db()
                self.assertIsNone(db.execute("SELECT id FROM ctas_cycles WHERE id=?", (cid,)).fetchone())
                self.assertEqual(db.execute("SELECT COUNT(*) FROM ctas_subscriptions WHERE cycle_id=?",
                                            (cid,)).fetchone()[0], 0)

            # A cycle with a posted payout is protected.
            with self.app.app_context():
                db = get_db()
                db.execute("INSERT INTO ctas_cycles (name, status, frequency, periods, duration_months, "
                           "contribution_amount, monthly_capacity) VALUES ('PaidCyc','balloted','monthly',4,4,50000,1)")
                cid2 = db.execute("SELECT id FROM ctas_cycles WHERE name='PaidCyc'").fetchone()['id']
                db.execute("INSERT INTO ctas_subscriptions (cycle_id, member_id, target_amount, tenure_months, "
                           "monthly_deduction, status, contributed_total, advance_balance, payout_month) "
                           "VALUES (?,?,200000,4,50000,'scheduled',0,0,2)", (cid2, mid))
                db.commit()
                sid2 = db.execute("SELECT id FROM ctas_subscriptions WHERE cycle_id=?", (cid2,)).fetchone()['id']
            self.client.post(f'/ctas/subscriptions/{sid2}/payout', data={})
            self.client.post(f'/ctas/cycles/{cid2}/delete', follow_redirects=True)
            with self.app.app_context():
                self.assertIsNotNone(get_db().execute("SELECT id FROM ctas_cycles WHERE id=?",
                                                      (cid2,)).fetchone())   # refused
        finally:
            with self.app.app_context():
                db = get_db()
                for r2 in db.execute("SELECT id FROM journal_entries WHERE source_module='ctas_payout'").fetchall():
                    db.execute("DELETE FROM journal_lines WHERE entry_id=?", (r2['id'],))
                    db.execute("DELETE FROM journal_entries WHERE id=?", (r2['id'],))
                db.execute("DELETE FROM ctas_subscriptions WHERE member_id=?", (mid,))
                db.execute("DELETE FROM ctas_cycles WHERE name IN ('DelCyc','PaidCyc')")
                db.execute("DELETE FROM members WHERE id=?", (mid,))
                db.execute("DELETE FROM settings WHERE key='ctas_enabled'")
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
                       "monthly_deduction, status, total_recovered, outstanding, advance_balance, payout_month) "
                       "VALUES (?,?,?,2,?,'active_recovery',0,?,?,2)", (cid, mid, target, monthly, target, target))
            db.commit()
            sid = db.execute("SELECT id FROM ctas_subscriptions WHERE cycle_id=?", (cid,)).fetchone()['id']
        return cid, mid, sid

    def _cleanup_sub(self, cid, mid, sid, base_sav=0, base_shr=0):
        with self.app.app_context():
            db = get_db()
            for mod in ('ctas_recovery', 'ctas_contribution', 'ctas_exit', 'ctas_payout'):
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

    def test_pool_contribution_then_payout_split_then_advance_repay(self):
        import io
        with self.app.app_context():
            db = get_db()
            db.execute("INSERT INTO members (member_number, first_name, last_name, status, total_savings, "
                       "monthly_savings, date_joined) VALUES ('CTAS/PL/1','Pool','T','active',0,5000,'2024-01-01')")
            mid = db.execute("SELECT id FROM members WHERE member_number='CTAS/PL/1'").fetchone()['id']
            db.execute("INSERT INTO ctas_cycles (name, status, frequency, periods, duration_months, "
                       "contribution_amount, monthly_capacity, earliest_payout_month) "
                       "VALUES ('PoolCyc','balloted','monthly',4,4,50000,1,1)")
            cid = db.execute("SELECT id FROM ctas_cycles WHERE name='PoolCyc'").fetchone()['id']
            db.execute("INSERT INTO ctas_subscriptions (cycle_id, member_id, target_amount, tenure_months, "
                       "monthly_deduction, admin_fee, status, contributed_total, advance_balance, payout_month) "
                       "VALUES (?,?,200000,4,50000,0,'scheduled',0,0,2)", (cid, mid))
            db.commit()
            sid = db.execute("SELECT id FROM ctas_subscriptions WHERE cycle_id=?", (cid,)).fetchone()['id']

        def _import(period, amount):
            body = f"subscription_id,actual_amount\n{sid},{amount}\n".encode('utf-8')
            return self.client.post(f'/ctas/cycles/{cid}/payroll/import',
                data={'month': str(period), 'file': (io.BytesIO(body), f'p{period}.csv')},
                content_type='multipart/form-data', follow_redirects=True)

        def gl(mod, acct, col):
            with self.app.app_context():
                return float(get_db().execute(
                    f"SELECT COALESCE(SUM(jl.{col}),0) FROM journal_lines jl JOIN journal_entries je "
                    "ON je.id=jl.entry_id WHERE je.source_module=? AND jl.account_code=?", (mod, acct)).fetchone()[0])
        try:
            # Period 1: scheduled member funds the POOL.
            _import(1, 50000)
            with self.app.app_context():
                sub = get_db().execute("SELECT * FROM ctas_subscriptions WHERE id=?", (sid,)).fetchone()
                self.assertEqual(sub['status'], 'scheduled')
                self.assertAlmostEqual(float(sub['contributed_total']), 50000.0, places=2)
            self.assertAlmostEqual(gl('ctas_contribution', '2050', 'credit'), 50000.0, places=2)  # pool

            # Payout: 50k from pool, 150k advanced.
            self.client.post(f'/ctas/subscriptions/{sid}/payout', data={})
            with self.app.app_context():
                sub = get_db().execute("SELECT * FROM ctas_subscriptions WHERE id=?", (sid,)).fetchone()
                self.assertEqual(sub['status'], 'active_recovery')
                self.assertAlmostEqual(float(sub['advance_balance']), 150000.0, places=2)
            self.assertAlmostEqual(gl('ctas_payout', '2050', 'debit'), 50000.0, places=2)   # pool released
            self.assertAlmostEqual(gl('ctas_payout', '1150', 'debit'), 150000.0, places=2)  # advance

            # Period 2: paid-out member repays the ADVANCE.
            _import(2, 50000)
            with self.app.app_context():
                sub = get_db().execute("SELECT * FROM ctas_subscriptions WHERE id=?", (sid,)).fetchone()
                self.assertAlmostEqual(float(sub['advance_balance']), 100000.0, places=2)
                self.assertAlmostEqual(float(sub['contributed_total']), 100000.0, places=2)
            self.assertAlmostEqual(gl('ctas_contribution', '1150', 'credit'), 50000.0, places=2)  # advance repaid
        finally:
            self._cleanup_sub(cid, mid, sid)

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
                       "monthly_deduction, status, total_recovered, outstanding, advance_balance, payout_month) "
                       "VALUES (?,?,100000,2,50000,'active_recovery',0,100000,100000,2)", (cid, mid))
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
                for r in db.execute("SELECT id FROM journal_entries WHERE source_module IN ('ctas_recovery','ctas_contribution')").fetchall():
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
            db.execute("INSERT INTO ctas_cycles (name, status, frequency, periods, duration_months, "
                       "contribution_amount, monthly_capacity, earliest_payout_month, "
                       "affordability_method, savings_multiple, start_date, grace_days) "
                       "VALUES ('MemCycle','open','monthly',4,4,50000,2,1,'savings',3,'2026-01-01',7)")
            db.commit()
            self.cid = db.execute("SELECT id FROM ctas_cycles WHERE name='MemCycle'").fetchone()['id']
            self.uid = db.execute("SELECT id FROM users WHERE username='ctasmem'").fetchone()['id']
        self.member.post('/login', data={'username': 'ctasmem', 'password': 'TestMember123'})
        self.admin.post('/login', data={'username': 'admin', 'password': 'TestAdmin123'})

    def tearDown(self):
        with self.app.app_context():
            db = get_db()
            db.execute("DELETE FROM notifications WHERE user_id = ?", (self.uid,))
            for r in db.execute("SELECT id FROM ctas_subscriptions WHERE cycle_id = ?", (self.cid,)).fetchall():
                db.execute("DELETE FROM ctas_mandates WHERE subscription_id = ?", (r['id'],))
                db.execute("DELETE FROM ctas_schedule WHERE subscription_id = ?", (r['id'],))
                for j in db.execute("SELECT id FROM journal_entries WHERE source_module = 'ctas_contribution' "
                                    "AND source_id = ?", (r['id'],)).fetchall():
                    db.execute("DELETE FROM journal_lines WHERE entry_id = ?", (j['id'],))
                    db.execute("DELETE FROM journal_entries WHERE id = ?", (j['id'],))
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


    def _enrol_and_schedule(self):
        """Member applies, is enrolled (schedule built) and balloted -> scheduled."""
        self.member.post('/my-ctas/apply', data={
            'cycle_id': str(self.cid), 'target_amount': '200000', 'tenure_months': '4',
            'terms': '1', 'signature_name': 'Mem Ber'})
        with self.app.app_context():
            sid = get_db().execute("SELECT id FROM ctas_subscriptions WHERE cycle_id=?",
                                   (self.cid,)).fetchone()['id']
        for _ in range(4):
            self.admin.post(f'/ctas/subscriptions/{sid}/act', data={'action': 'advance'})
        return sid

    def test_autopay_setup_stores_mandate_and_posts_the_authorising_contribution(self):
        from unittest.mock import patch
        sid = self._enrol_and_schedule()
        with self.app.app_context():
            db = get_db()
            # Paystack keys must look configured for the flow to start.
            for k, v in (('active_gateway', 'paystack'), ('paystack_secret_key', 'sk_test_x')):
                db.execute("DELETE FROM settings WHERE key = ?", (k,))
                db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (k, v))
            db.commit()
        # Consent + signature are required.
        self.member.post(f'/my-ctas/autopay/{sid}', data={'signature_name': 'Mem Ber'})
        with self.app.app_context():
            self.assertIsNone(get_db().execute(
                "SELECT id FROM ctas_mandates WHERE subscription_id=? AND status='active'",
                (sid,)).fetchone())

        class _GW:
            def initialize(self, *a, **k):
                return {'status': True, 'data': {'authorization_url': 'https://pay.test/x'}}
            def verify(self, ref):
                return {'status': True, 'data': {
                    'status': 'success', 'amount': 5000000,      # 50,000.00 in kobo
                    'metadata': {'ctas_period': 1},
                    'authorization': {'authorization_code': 'AUTH_tok123', 'reusable': True,
                                      'last4': '4321', 'card_type': 'visa', 'bank': 'Test Bank',
                                      'exp_month': '12', 'exp_year': '2030'}}}
        with patch('blueprints.ctas.get_gateway', return_value=_GW()):
            r = self.member.post(f'/my-ctas/autopay/{sid}',
                                 data={'signature_name': 'Mem Ber', 'consent': '1'},
                                 follow_redirects=False)
            self.assertIn(r.status_code, (302, 303))
            with self.app.app_context():
                pend = get_db().execute(
                    "SELECT * FROM ctas_mandates WHERE subscription_id=? AND status='pending'",
                    (sid,)).fetchone()
                self.assertIsNotNone(pend)
                self.assertIsNotNone(pend['consented_at'])            # consent recorded
                self.assertEqual(pend['consent_signature'], 'Mem Ber')
                self.assertIsNone(pend['authorization_code'])          # no token until authorised
                ref = pend['setup_reference']
            self.member.get(f'/my-ctas/autopay/callback?reference={ref}', follow_redirects=True)

        with self.app.app_context():
            db = get_db()
            man = db.execute("SELECT * FROM ctas_mandates WHERE subscription_id=?", (sid,)).fetchone()
            from blueprints.ctas import _read_token
            self.assertEqual(man['status'], 'active')
            self.assertEqual(_read_token(man['authorization_code']), 'AUTH_tok123')  # token, decryptable
            self.assertIn('4321', man['masked_label'])                  # masked display
            # The authorising payment posted as a real contribution.
            sub = db.execute("SELECT * FROM ctas_subscriptions WHERE id=?", (sid,)).fetchone()
            self.assertAlmostEqual(float(sub['contributed_total']), 50000.0, places=2)
            row1 = db.execute("SELECT * FROM ctas_schedule WHERE subscription_id=? AND period_number=1",
                              (sid,)).fetchone()
            self.assertEqual(row1['status'], 'paid')

    def test_stored_payment_token_is_encrypted_at_rest(self):
        from blueprints.ctas import _store_token, _read_token
        from crypto import encryption_enabled
        stored = _store_token('AUTH_secret123')
        if encryption_enabled():
            self.assertNotEqual(stored, 'AUTH_secret123')      # not readable in the database
            self.assertNotIn('AUTH_secret123', stored)
        self.assertEqual(_read_token(stored), 'AUTH_secret123')  # still usable for charging

    def test_another_member_cannot_complete_someone_elses_authorisation(self):
        with self.app.app_context():
            db = get_db()
            db.execute("INSERT INTO ctas_subscriptions (cycle_id, member_id, target_amount, tenure_months, "
                       "monthly_deduction, status) VALUES (?, (SELECT id FROM members WHERE "
                       "member_number='CTAS/M/1'), 200000, 4, 50000, 'scheduled')", (self.cid,))
            db.commit()
            sid = db.execute("SELECT id FROM ctas_subscriptions WHERE cycle_id=?", (self.cid,)).fetchone()['id']
            db.execute("INSERT INTO ctas_mandates (member_id, subscription_id, status, setup_reference) "
                       "VALUES ((SELECT id FROM members WHERE member_number='CTAS/M/1'), ?, "
                       "'pending', 'REF-PRIVATE')", (sid,))
            db.commit()
        # The admin is signed in but is not this member — the callback must refuse.
        r = self.admin.get('/my-ctas/autopay/callback?reference=REF-PRIVATE')
        self.assertIn(r.status_code, (302, 403))
        if r.status_code == 403:
            with self.app.app_context():
                man = get_db().execute("SELECT status FROM ctas_mandates WHERE setup_reference='REF-PRIVATE'").fetchone()
                self.assertEqual(man['status'], 'pending')     # not activated by the wrong user

    def test_autopay_cancel_stops_future_charges(self):
        with self.app.app_context():
            db = get_db()
            db.execute("INSERT INTO ctas_subscriptions (cycle_id, member_id, target_amount, tenure_months, "
                       "monthly_deduction, status, contributed_total, advance_balance) "
                       "VALUES (?, (SELECT id FROM members WHERE member_number='CTAS/M/1'), "
                       "200000, 4, 50000, 'scheduled', 0, 0)", (self.cid,))
            db.commit()
            sid = db.execute("SELECT id FROM ctas_subscriptions WHERE cycle_id=?", (self.cid,)).fetchone()['id']
            db.execute("INSERT INTO ctas_mandates (member_id, subscription_id, status, authorization_code, "
                       "masked_label) VALUES ((SELECT id FROM members WHERE member_number='CTAS/M/1'), "
                       "?, 'active', 'AUTH_x', '**** 1111')", (sid,))
            db.commit()
            mid = db.execute("SELECT id FROM ctas_mandates WHERE subscription_id=?", (sid,)).fetchone()['id']
        r = self.member.post(f'/my-ctas/autopay/{mid}/cancel', follow_redirects=False)
        self.assertIn(r.status_code, (302, 303))
        with self.app.app_context():
            man = get_db().execute("SELECT status, cancelled_at FROM ctas_mandates WHERE id=?", (mid,)).fetchone()
            self.assertEqual(man['status'], 'cancelled')
            self.assertIsNotNone(man['cancelled_at'])

    def test_card_expiry_detection(self):
        from datetime import date as _d
        from ctas_engine import card_expired
        self.assertTrue(card_expired('01', '2026', _d(2026, 2, 1)))    # expired last month
        self.assertFalse(card_expired('02', '2026', _d(2026, 2, 28)))  # valid to month end
        self.assertFalse(card_expired('12', '2030', _d(2026, 2, 1)))
        self.assertFalse(card_expired('', '', _d(2026, 2, 1)))         # unknown -> let gateway decide

    def _mandate_fixture(self, exp_month='12', exp_year='2030', attempts=0, retry=None, max_att=3):
        """A scheduled subscription with an active mandate and one due period."""
        with self.app.app_context():
            db = get_db()
            db.execute("UPDATE ctas_cycles SET retry_days = 3, max_charge_attempts = ? WHERE id = ?",
                       (max_att, self.cid))
            db.execute("INSERT INTO ctas_subscriptions (cycle_id, member_id, target_amount, tenure_months, "
                       "monthly_deduction, status, contributed_total, advance_balance) VALUES "
                       "(?, (SELECT id FROM members WHERE member_number='CTAS/M/1'), 200000, 4, 50000, "
                       "'scheduled', 0, 0)", (self.cid,))
            db.commit()
            sid = db.execute("SELECT id FROM ctas_subscriptions WHERE cycle_id=?", (self.cid,)).fetchone()['id']
            db.execute("INSERT INTO ctas_schedule (subscription_id, cycle_id, period_number, due_date, "
                       "expected_amount, paid_amount, status, charge_attempts, next_retry_at) "
                       "VALUES (?, ?, 1, '2026-01-01', 50000, 0, 'late', ?, ?)",
                       (sid, self.cid, attempts, retry))
            db.execute("INSERT INTO ctas_mandates (member_id, subscription_id, status, authorization_code, "
                       "masked_label, email, exp_month, exp_year) VALUES "
                       "((SELECT id FROM members WHERE member_number='CTAS/M/1'), ?, 'active', ?, "
                       "'**** 4321', 'ctasmem@x.com', ?, ?)",
                       (sid, _store_token('AUTH_tok'), exp_month, exp_year))
            db.commit()
            return sid

    def test_failed_charge_backs_off_then_suspends_after_max_attempts(self):
        from unittest.mock import patch
        import os as _os
        sid = self._mandate_fixture(attempts=0, max_att=2)
        _os.environ['TASK_RUNNER_TOKEN'] = 'charge-secret'
        anon = self.app.test_client()
        hdr = {'X-Task-Token': 'charge-secret'}

        class _Decline:
            def charge_authorization(self, *a, **k):
                return {'status': False, 'message': 'Insufficient funds'}
        try:
            with patch('blueprints.ctas.get_gateway', return_value=_Decline()):
                # First failure -> schedules a retry, does NOT suspend.
                r1 = anon.post('/tasks/ctas/charge-due', headers=hdr)
                self.assertEqual(r1.get_json()['failed'], 1)
                self.assertEqual(r1.get_json()['suspended'], 0)
                with self.app.app_context():
                    row = get_db().execute("SELECT * FROM ctas_schedule WHERE subscription_id=?", (sid,)).fetchone()
                    self.assertEqual(row['charge_attempts'], 1)
                    self.assertIsNotNone(row['next_retry_at'])       # backed off
                # Same day: the back-off means it is not retried again.
                r2 = anon.post('/tasks/ctas/charge-due', headers=hdr)
                self.assertEqual(r2.get_json()['failed'], 0)
                # Clear the back-off to simulate the retry day arriving -> hits the cap.
                with self.app.app_context():
                    db = get_db()
                    db.execute("UPDATE ctas_schedule SET next_retry_at = NULL WHERE subscription_id=?", (sid,))
                    db.commit()
                r3 = anon.post('/tasks/ctas/charge-due', headers=hdr)
                self.assertEqual(r3.get_json()['suspended'], 1)
                with self.app.app_context():
                    db = get_db()
                    man = db.execute("SELECT status FROM ctas_mandates WHERE subscription_id=?", (sid,)).fetchone()
                    self.assertEqual(man['status'], 'suspended')     # stopped charging
                    ex = db.execute("SELECT COUNT(*) FROM ctas_exceptions WHERE subscription_id=?",
                                    (sid,)).fetchone()[0]
                    self.assertGreaterEqual(ex, 1)                   # officers have a case to work
        finally:
            _os.environ.pop('TASK_RUNNER_TOKEN', None)

    def test_expired_card_stops_charging_and_tells_the_member(self):
        from unittest.mock import patch
        import os as _os
        sid = self._mandate_fixture(exp_month='01', exp_year='2020')
        _os.environ['TASK_RUNNER_TOKEN'] = 'charge-secret'
        anon = self.app.test_client()

        class _NeverCalled:
            def charge_authorization(self, *a, **k):
                raise AssertionError('an expired card must not be charged')
        try:
            with patch('blueprints.ctas.get_gateway', return_value=_NeverCalled()):
                r = anon.post('/tasks/ctas/charge-due', headers={'X-Task-Token': 'charge-secret'})
                self.assertEqual(r.get_json()['skipped'], 1)
            with self.app.app_context():
                man = get_db().execute("SELECT status FROM ctas_mandates WHERE subscription_id=?",
                                       (sid,)).fetchone()
                self.assertEqual(man['status'], 'expired')
        finally:
            _os.environ.pop('TASK_RUNNER_TOKEN', None)

    def test_successful_charge_posts_contribution_and_clears_retries(self):
        from unittest.mock import patch
        import os as _os
        sid = self._mandate_fixture(attempts=1, retry=None)
        _os.environ['TASK_RUNNER_TOKEN'] = 'charge-secret'
        anon = self.app.test_client()

        class _OK:
            def charge_authorization(self, *a, **k):
                return {'status': True, 'data': {'status': 'success'}}
        try:
            with patch('blueprints.ctas.get_gateway', return_value=_OK()):
                r = anon.post('/tasks/ctas/charge-due', headers={'X-Task-Token': 'charge-secret'})
                self.assertEqual(r.get_json()['charged'], 1)
            with self.app.app_context():
                db = get_db()
                row = db.execute("SELECT * FROM ctas_schedule WHERE subscription_id=?", (sid,)).fetchone()
                self.assertEqual(row['status'], 'paid')
                self.assertIsNone(row['next_retry_at'])              # back-off cleared
                sub = db.execute("SELECT contributed_total FROM ctas_subscriptions WHERE id=?", (sid,)).fetchone()
                self.assertAlmostEqual(float(sub['contributed_total']), 50000.0, places=2)
        finally:
            _os.environ.pop('TASK_RUNNER_TOKEN', None)

    def test_charge_due_endpoint_is_token_guarded(self):
        import os as _os
        _os.environ.pop('TASK_RUNNER_TOKEN', None)
        anon = self.app.test_client()
        self.assertEqual(anon.post('/tasks/ctas/charge-due').status_code, 403)
        _os.environ['TASK_RUNNER_TOKEN'] = 'charge-secret'
        try:
            self.assertEqual(anon.post('/tasks/ctas/charge-due').status_code, 403)   # no token
            ok = anon.post('/tasks/ctas/charge-due', headers={'X-Task-Token': 'charge-secret'})
            self.assertEqual(ok.status_code, 200)
            self.assertTrue(ok.get_json()['success'])
        finally:
            _os.environ.pop('TASK_RUNNER_TOKEN', None)

    def test_mobile_ctas_view_and_apply(self):
        r = self.member.post('/api/mobile/login',
                             json={'username': 'ctasmem', 'password': 'TestMember123'})
        self.assertEqual(r.status_code, 200)
        token = r.get_json()['token']
        h = {'Authorization': f'Bearer {token}'}

        v = self.member.get('/api/mobile/v1/ctas', headers=h)
        self.assertEqual(v.status_code, 200)
        body = v.get_json()
        self.assertTrue(body['enabled'])
        self.assertTrue(any(c['id'] == self.cid for c in body['open_cycles']))

        a = self.member.post('/api/mobile/v1/ctas/apply', headers=h, json={
            'cycle_id': self.cid, 'target_amount': 200000, 'tenure_months': 4,
            'terms_accepted': True, 'signature_name': 'Mem Ber'})
        self.assertEqual(a.status_code, 200)
        self.assertTrue(a.get_json()['success'])
        with self.app.app_context():
            sub = get_db().execute("SELECT * FROM ctas_subscriptions WHERE cycle_id = ?", (self.cid,)).fetchone()
            self.assertIsNotNone(sub)
            self.assertEqual(sub['terms_accepted'], 1)
            self.assertAlmostEqual(float(sub['monthly_deduction']), 50000.0, places=2)

        # Applying without terms is rejected.
        a2 = self.member.post('/api/mobile/v1/ctas/apply', headers=h, json={
            'cycle_id': self.cid, 'target_amount': 100000, 'tenure_months': 4, 'signature_name': 'X'})
        self.assertEqual(a2.status_code, 400)


if __name__ == '__main__':
    unittest.main()
