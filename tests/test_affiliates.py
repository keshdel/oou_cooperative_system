import os
import unittest

TEST_DB = os.path.abspath('.test-affiliates.db')
os.environ.setdefault('SECRET_KEY', 'test-secret-affiliates')
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


class AffiliateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app_module.app
        cls.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)

    def setUp(self):
        os.environ['MARKETING_HQ'] = '1'
        self.client = self.app.test_client()
        # The public lead API allows 8 submissions per IP per 15 minutes. That is
        # right in production and wrong for a test suite that files many
        # enquiries from one address, so the window is cleared per test rather
        # than the limit loosened.
        import blueprints.marketing as mk
        mk._RECENT_SUBMISSIONS.clear()

    def tearDown(self):
        os.environ.pop('MARKETING_HQ', None)

    def login_admin(self):
        r = self.client.post('/login', data={'username': 'admin', 'password': 'TestAdmin123'})
        self.assertIn(r.status_code, (302, 303))

    # ── helpers ──────────────────────────────────────────────────────────────

    def _apply(self, name, email, referrer_code=''):
        self.client.post('/affiliates/apply', data={
            'full_name': name, 'email': email, 'phone': '08000000000',
            'referrer_code': referrer_code}, follow_redirects=True)
        with self.app.app_context():
            return get_db().execute('SELECT * FROM affiliates WHERE email = ?', (email,)).fetchone()

    def _approve(self, aff_id, tier='member', parent_id=''):
        self.client.post(f'/hq/affiliates/{aff_id}/review', data={
            'action': 'approve', 'tier': tier, 'parent_id': str(parent_id)},
            follow_redirects=True)
        with self.app.app_context():
            return get_db().execute('SELECT * FROM affiliates WHERE id = ?', (aff_id,)).fetchone()

    def _accept(self, aff):
        return self.client.post(f"/affiliates/accept/{aff['accept_token']}", data={
            'accept_terms': '1', 'signature_name': aff['full_name']}, follow_redirects=True)

    def _onboard(self, name, email, tier='member', parent_id=''):
        aff = self._apply(name, email)
        aff = self._approve(aff['id'], tier=tier, parent_id=parent_id)
        self._accept(aff)
        with self.app.app_context():
            return get_db().execute('SELECT * FROM affiliates WHERE id = ?', (aff['id'],)).fetchone()

    # ── recruitment ──────────────────────────────────────────────────────────

    def test_an_applicant_is_not_in_the_programme_until_they_accept(self):
        self.login_admin()
        aff = self._apply('Tunde Bakare', 'tunde@example.test')
        self.assertEqual(aff['status'], 'applied')
        self.assertIsNone(aff['code'])           # no code until approved

        aff = self._approve(aff['id'])
        self.assertEqual(aff['status'], 'approved')
        self.assertTrue(aff['code'], 'approval must issue a code')
        self.assertTrue(aff['accept_token'])
        # Approved is not yet attributable — acceptance is the gate.
        from blueprints.affiliates import attributable
        self.assertFalse(attributable(aff))

        self._accept(aff)
        with self.app.app_context():
            aff = get_db().execute('SELECT * FROM affiliates WHERE id = ?', (aff['id'],)).fetchone()
        self.assertEqual(aff['status'], 'active')
        self.assertIsNotNone(aff['accepted_at'])
        self.assertTrue(attributable(aff))

    def test_a_declined_applicant_gets_no_code(self):
        self.login_admin()
        aff = self._apply('Rejected Person', 'rejected@example.test')
        self.client.post(f"/hq/affiliates/{aff['id']}/review", data={
            'action': 'decline', 'reason': 'Failed interview'}, follow_redirects=True)
        with self.app.app_context():
            aff = get_db().execute('SELECT * FROM affiliates WHERE id = ?', (aff['id'],)).fetchone()
        self.assertEqual(aff['status'], 'declined')
        self.assertIsNone(aff['code'])
        self.assertEqual(aff['declined_reason'], 'Failed interview')

    def test_acceptance_link_is_single_use_and_unguessable(self):
        self.login_admin()
        aff = self._onboard('Once Only', 'once@example.test')
        # A second visit reports the existing acceptance rather than re-accepting.
        r = self.client.get(f"/affiliates/accept/{aff['accept_token']}")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Welcome aboard', r.data)
        self.assertEqual(self.client.get('/affiliates/accept/not-a-real-token').status_code, 404)

    # ── attribution ──────────────────────────────────────────────────────────

    def _capture_lead(self, society, code=None):
        payload = {'full_name': 'Sec Gen', 'email': f'{society.lower()}@example.test',
                   'society_name': society, 'consent_accepted': '1'}
        if code is not None:
            payload['affiliate_code'] = code
        r = self.client.post('/api/marketing/leads', json=payload)
        self.assertEqual(r.status_code, 200, f'lead capture refused: {r.data[:200]}')
        with self.app.app_context():
            lead = get_db().execute('SELECT * FROM marketing_leads WHERE society_name = ?',
                                    (society,)).fetchone()
        self.assertIsNotNone(lead, f'no lead row created for {society}')
        return lead

    def test_a_referral_code_on_an_enquiry_credits_the_affiliate(self):
        self.login_admin()
        aff = self._onboard('Grace Eze', 'grace@example.test')
        lead = self._capture_lead('Sunrise Coop', aff['code'])
        self.assertEqual(lead['affiliate_code'], aff['code'])
        self.assertEqual(lead['affiliate_id'], aff['id'])

    def test_codes_are_matched_however_the_cooperative_types_them(self):
        self.login_admin()
        aff = self._onboard('Case Test', 'case@example.test')
        lead = self._capture_lead('Lowercase Coop', aff['code'].lower())
        self.assertEqual(lead['affiliate_id'], aff['id'])
        lead2 = self._capture_lead('Spaced Coop', f" {aff['code'][:4]} {aff['code'][4:]} ")
        self.assertEqual(lead2['affiliate_id'], aff['id'])

    def test_an_unknown_code_is_kept_for_correction_not_dropped(self):
        self.login_admin()
        lead = self._capture_lead('Typo Coop', 'CMA-ZZ9999')
        self.assertEqual(lead['affiliate_code'], 'CMA-ZZ9999')
        self.assertIsNone(lead['affiliate_id'], 'an unknown code must not credit anyone')
        # It surfaces on the attribution screen so it can be fixed.
        r = self.client.get('/hq/affiliates/attribution')
        self.assertIn(b'CMA-ZZ9999', r.data)
        # And an officer can correct it to a real affiliate.
        aff = self._onboard('Fixer Upper', 'fixer@example.test')
        self.client.post(f"/hq/affiliates/leads/{lead['id']}/code",
                         data={'affiliate_code': aff['code']}, follow_redirects=True)
        with self.app.app_context():
            lead = get_db().execute('SELECT * FROM marketing_leads WHERE id = ?',
                                    (lead['id'],)).fetchone()
        self.assertEqual(lead['affiliate_id'], aff['id'])

    def test_a_code_belonging_to_a_suspended_affiliate_credits_nobody(self):
        self.login_admin()
        aff = self._onboard('Suspended Sam', 'sam@example.test')
        self.client.post(f"/hq/affiliates/{aff['id']}/team",
                         data={'action': 'suspend'}, follow_redirects=True)
        lead = self._capture_lead('Late Coop', aff['code'])
        self.assertEqual(lead['affiliate_code'], aff['code'])
        self.assertIsNone(lead['affiliate_id'])

    def test_linking_a_client_to_its_enquiry_carries_the_introduction_through(self):
        from blueprints.hq_billing import setup_paid
        self.login_admin()
        aff = self._onboard('Chioma Obi', 'chioma@example.test')
        lead = self._capture_lead('Bridge Coop', aff['code'])
        self.client.post('/hq/clients', data={
            'name': 'Bridge Coop', 'code': 'bridge', 'billing_email': 'b@x.com',
            'user_count': '150', 'rate_per_user': '5000', 'billing_cycle': 'annual'},
            follow_redirects=True)
        with self.app.app_context():
            cid = get_db().execute("SELECT id FROM hq_clients WHERE name = 'Bridge Coop'").fetchone()['id']
        self.client.post(f'/hq/affiliates/clients/{cid}/link',
                         data={'lead_id': str(lead['id'])}, follow_redirects=True)
        with self.app.app_context():
            db = get_db()
            client = db.execute('SELECT * FROM hq_clients WHERE id = ?', (cid,)).fetchone()
            self.assertEqual(client['lead_id'], lead['id'])
            self.assertEqual(client['affiliate_id'], aff['id'])
            self.assertIsNotNone(client['attributed_at'])

        # Nothing is earned until the setup fee is actually collected.
        self.client.post('/hq/invoices/new', data={
            'client_id': cid, 'sub_mode': 'none', 'setup_amount': '300000'},
            follow_redirects=True)
        with self.app.app_context():
            db = get_db()
            self.assertAlmostEqual(setup_paid(db, cid), 0.0, places=2)
            inv = db.execute('SELECT id FROM hq_invoices WHERE client_id = ?', (cid,)).fetchone()['id']
        self.client.post(f'/hq/invoices/{inv}/mark-paid', data={'paid_method': 'transfer'},
                         follow_redirects=True)
        with self.app.app_context():
            self.assertAlmostEqual(setup_paid(get_db(), cid), 300000.0, places=2)

    def test_first_introduction_wins_when_a_client_is_relinked(self):
        self.login_admin()
        first = self._onboard('First Finder', 'first@example.test')
        second = self._onboard('Second Claimer', 'second@example.test')
        lead_a = self._capture_lead('Contested Coop', first['code'])
        lead_b = self._capture_lead('Contested Coop Two', second['code'])
        self.client.post('/hq/clients', data={
            'name': 'Contested Coop', 'code': 'contested', 'billing_email': 'c@x.com',
            'user_count': '80', 'rate_per_user': '5000', 'billing_cycle': 'annual'},
            follow_redirects=True)
        with self.app.app_context():
            cid = get_db().execute("SELECT id FROM hq_clients WHERE name = 'Contested Coop'").fetchone()['id']
        self.client.post(f'/hq/affiliates/clients/{cid}/link',
                         data={'lead_id': str(lead_a['id'])}, follow_redirects=True)
        self.client.post(f'/hq/affiliates/clients/{cid}/link',
                         data={'lead_id': str(lead_b['id'])}, follow_redirects=True)
        with self.app.app_context():
            client = get_db().execute('SELECT * FROM hq_clients WHERE id = ?', (cid,)).fetchone()
        self.assertEqual(client['affiliate_id'], first['id'],
                         'a later link must not move an existing attribution')

    # ── teams and promotion ──────────────────────────────────────────────────

    def test_a_recruit_joins_their_recruiters_team_lead(self):
        self.login_admin()
        lead = self._onboard('Team Lead', 'lead@example.test', tier='lead')
        member = self._onboard('Team Member', 'member@example.test', parent_id=lead['id'])
        recruit = self._apply('New Recruit', 'recruit@example.test', referrer_code=member['code'])
        self.assertEqual(recruit['recruited_by'], member['id'])
        # The recruiter is not yet a lead, so the recruit sits under their lead.
        self.assertEqual(recruit['parent_id'], lead['id'])

    def test_promotion_needs_five_recruits_and_takes_the_team_along(self):
        from blueprints.affiliates import recruit_count, may_apply_for_promotion
        self.login_admin()
        boss = self._onboard('Old Boss', 'boss@example.test', tier='lead')
        climber = self._onboard('Climber', 'climber@example.test', parent_id=boss['id'])

        for i in range(4):
            r = self._apply(f'Recruit {i}', f'r{i}@example.test', referrer_code=climber['code'])
            self._approve(r['id'], parent_id=boss['id'])
        with self.app.app_context():
            db = get_db()
            self.assertEqual(recruit_count(db, climber['id']), 4)
            c = db.execute('SELECT * FROM affiliates WHERE id = ?', (climber['id'],)).fetchone()
            self.assertFalse(may_apply_for_promotion(db, c), 'four recruits is not enough')

        # Below threshold the promotion is refused.
        self.client.post(f"/hq/affiliates/{climber['id']}/team",
                         data={'action': 'promote'}, follow_redirects=True)
        with self.app.app_context():
            c = get_db().execute('SELECT tier FROM affiliates WHERE id = ?', (climber['id'],)).fetchone()
        self.assertEqual(c['tier'], 'member')

        fifth = self._apply('Recruit 5', 'r5@example.test', referrer_code=climber['code'])
        self._approve(fifth['id'], parent_id=boss['id'])
        self.client.post(f"/hq/affiliates/{climber['id']}/team",
                         data={'action': 'promote'}, follow_redirects=True)
        with self.app.app_context():
            db = get_db()
            c = db.execute('SELECT * FROM affiliates WHERE id = ?', (climber['id'],)).fetchone()
            self.assertEqual(c['tier'], 'lead')
            self.assertIsNone(c['parent_id'], 'a promoted member leaves their former lead')
            self.assertIsNotNone(c['promoted_at'])
            # Their own recruits follow them into the new team.
            moved = db.execute('SELECT COUNT(*) FROM affiliates WHERE parent_id = ?',
                               (climber['id'],)).fetchone()[0]
            self.assertEqual(moved, 5)

    def test_affiliate_pages_are_operator_only(self):
        self.login_admin()
        os.environ.pop('MARKETING_HQ', None)
        self.assertEqual(self.client.get('/hq/affiliates').status_code, 404)
        self.assertEqual(self.client.get('/affiliates/apply').status_code, 404)

    # ── commission ───────────────────────────────────────────────────────────

    def _client_with_setup(self, name, aff, setup=300000, users=200, pay=True):
        """A cooperative introduced by `aff`, billed a setup fee, optionally paid."""
        lead = self._capture_lead(name, aff['code'])
        self.client.post('/hq/clients', data={
            'name': name, 'code': name.lower().replace(' ', ''), 'billing_email': 'x@y.com',
            'user_count': str(users), 'rate_per_user': '5000', 'billing_cycle': 'annual'},
            follow_redirects=True)
        with self.app.app_context():
            cid = get_db().execute('SELECT id FROM hq_clients WHERE name = ?', (name,)).fetchone()['id']
        self.client.post(f'/hq/affiliates/clients/{cid}/link',
                         data={'lead_id': str(lead['id'])}, follow_redirects=True)
        self.client.post('/hq/invoices/new', data={
            'client_id': cid, 'sub_mode': 'none', 'setup_amount': str(setup)},
            follow_redirects=True)
        with self.app.app_context():
            inv = get_db().execute('SELECT id FROM hq_invoices WHERE client_id = ? '
                                   'ORDER BY id DESC', (cid,)).fetchone()['id']
        if pay:
            self.client.post(f'/hq/invoices/{inv}/mark-paid',
                             data={'paid_method': 'transfer'}, follow_redirects=True)
        return cid, inv

    def _commissions(self, invoice_id):
        with self.app.app_context():
            return get_db().execute(
                'SELECT c.*, a.full_name FROM affiliate_commissions c '
                'JOIN affiliates a ON a.id = c.affiliate_id WHERE c.invoice_id = ? '
                'ORDER BY c.role, c.id', (invoice_id,)).fetchall()

    def test_member_and_lead_split_the_pool_out_of_one_setup_fee(self):
        self.login_admin()
        boss = self._onboard('Split Lead', 'splitlead@example.test', tier='lead')
        member = self._onboard('Split Member', 'splitmember@example.test', parent_id=boss['id'])
        _, inv = self._client_with_setup('Split Coop', member, setup=300000)

        rows = self._commissions(inv)
        self.assertEqual(len(rows), 2)
        by_role = {r['role']: r for r in rows}
        # 15% to the member who closed it, 5% to their lead, out of the same 20%.
        self.assertAlmostEqual(float(by_role['direct']['amount']), 45000.0, places=2)
        self.assertEqual(by_role['direct']['affiliate_id'], member['id'])
        self.assertAlmostEqual(float(by_role['override']['amount']), 15000.0, places=2)
        self.assertEqual(by_role['override']['affiliate_id'], boss['id'])
        self.assertEqual(by_role['override']['source_affiliate_id'], member['id'])
        # Total cost to the business is capped at the pool.
        self.assertAlmostEqual(sum(float(r['amount']) for r in rows), 60000.0, places=2)

    def test_a_lead_who_closes_it_himself_takes_the_whole_pool(self):
        from blueprints.affiliates import affiliate_balance
        self.login_admin()
        boss = self._onboard('Solo Lead', 'sololead@example.test', tier='lead')
        _, inv = self._client_with_setup('Solo Coop', boss, setup=300000)
        rows = self._commissions(inv)
        self.assertEqual(len(rows), 1, 'there is nobody above a lead to override')
        self.assertEqual(rows[0]['role'], 'direct')
        self.assertAlmostEqual(float(rows[0]['amount']), 60000.0, places=2)
        with self.app.app_context():
            self.assertAlmostEqual(affiliate_balance(get_db(), boss['id']), 60000.0, places=2)

    def test_a_member_with_no_lead_earns_their_own_rate_and_the_rest_is_kept(self):
        self.login_admin()
        orphan = self._onboard('No Team', 'noteam@example.test')   # no parent
        _, inv = self._client_with_setup('Orphan Coop', orphan, setup=300000)
        rows = self._commissions(inv)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(float(rows[0]['amount']), 45000.0, places=2)
        # The 5% override is simply not paid — it does not roll up to the member.
        self.assertAlmostEqual(sum(float(r['amount']) for r in rows), 45000.0, places=2)

    def test_nothing_is_earned_until_the_setup_fee_is_actually_paid(self):
        self.login_admin()
        aff = self._onboard('Patient Seller', 'patient@example.test')
        cid, inv = self._client_with_setup('Unpaid Coop', aff, setup=300000, pay=False)
        self.assertEqual(len(self._commissions(inv)), 0)
        self.client.post(f'/hq/invoices/{inv}/mark-paid',
                         data={'paid_method': 'transfer'}, follow_redirects=True)
        self.assertEqual(len(self._commissions(inv)), 1)

    def test_commission_follows_cash_so_a_deposit_earns_only_its_part(self):
        from blueprints.affiliates import affiliate_balance
        self.login_admin()
        aff = self._onboard('Chaser', 'chaser@example.test')
        # 100,000 deposit invoice paid; 200,000 balance invoice still outstanding.
        cid, first = self._client_with_setup('Instalment Coop', aff, setup=100000)
        self.client.post('/hq/invoices/new', data={
            'client_id': cid, 'sub_mode': 'none', 'setup_amount': '200000',
            'setup_again': '1'}, follow_redirects=True)
        with self.app.app_context():
            db = get_db()
            self.assertAlmostEqual(affiliate_balance(db, aff['id']), 15000.0, places=2)
            second = db.execute('SELECT id FROM hq_invoices WHERE client_id = ? ORDER BY id DESC',
                                (cid,)).fetchone()['id']
        # Chasing the balance earns the rest.
        self.client.post(f'/hq/invoices/{second}/mark-paid',
                         data={'paid_method': 'transfer'}, follow_redirects=True)
        with self.app.app_context():
            self.assertAlmostEqual(affiliate_balance(get_db(), aff['id']), 45000.0, places=2)

    def test_accrual_is_idempotent_so_a_replayed_payment_cannot_pay_twice(self):
        from blueprints.affiliates import accrue_for_invoice, affiliate_balance
        self.login_admin()
        aff = self._onboard('Once Paid', 'oncepaid@example.test')
        _, inv = self._client_with_setup('Replay Coop', aff, setup=300000)
        with self.app.app_context():
            db = get_db()
            before = affiliate_balance(db, aff['id'])
            # Simulate the gateway replaying its callback.
            accrue_for_invoice(db, inv)
            accrue_for_invoice(db, inv)
            db.commit()
            self.assertAlmostEqual(affiliate_balance(db, aff['id']), before, places=2)
        self.assertEqual(len(self._commissions(inv)), 1)

    def test_only_the_setup_line_earns_commission(self):
        from blueprints.affiliates import affiliate_balance
        self.login_admin()
        aff = self._onboard('Subs Only', 'subsonly@example.test')
        lead = self._capture_lead('Subs Coop', aff['code'])
        self.client.post('/hq/clients', data={
            'name': 'Subs Coop', 'code': 'subscoop', 'billing_email': 'x@y.com',
            'user_count': '100', 'rate_per_user': '5000', 'billing_cycle': 'annual'},
            follow_redirects=True)
        with self.app.app_context():
            cid = get_db().execute("SELECT id FROM hq_clients WHERE name = 'Subs Coop'").fetchone()['id']
        self.client.post(f'/hq/affiliates/clients/{cid}/link',
                         data={'lead_id': str(lead['id'])}, follow_redirects=True)
        # A subscription plus a service fee, and no setup line at all.
        self.client.post('/hq/invoices/new', data={
            'client_id': cid, 'sub_mode': 'full', 'sub_qty': '100', 'sub_unit': '5000',
            'service_type': 'training', 'service_desc': 'onboarding day',
            'service_amount': '50000'}, follow_redirects=True)
        with self.app.app_context():
            inv = get_db().execute('SELECT id FROM hq_invoices WHERE client_id = ?',
                                   (cid,)).fetchone()['id']
        self.client.post(f'/hq/invoices/{inv}/mark-paid',
                         data={'paid_method': 'transfer'}, follow_redirects=True)
        with self.app.app_context():
            self.assertAlmostEqual(affiliate_balance(get_db(), aff['id']), 0.0, places=2)

    def test_an_unattributed_cooperative_earns_nobody_anything(self):
        self.login_admin()
        self.client.post('/hq/clients', data={
            'name': 'Walk In Coop', 'code': 'walkin', 'billing_email': 'x@y.com',
            'user_count': '90', 'rate_per_user': '5000', 'billing_cycle': 'annual'},
            follow_redirects=True)
        with self.app.app_context():
            cid = get_db().execute("SELECT id FROM hq_clients WHERE name = 'Walk In Coop'").fetchone()['id']
        self.client.post('/hq/invoices/new', data={
            'client_id': cid, 'sub_mode': 'none', 'setup_amount': '300000'},
            follow_redirects=True)
        with self.app.app_context():
            inv = get_db().execute('SELECT id FROM hq_invoices WHERE client_id = ?',
                                   (cid,)).fetchone()['id']
        self.client.post(f'/hq/invoices/{inv}/mark-paid',
                         data={'paid_method': 'transfer'}, follow_redirects=True)
        self.assertEqual(len(self._commissions(inv)), 0)

    def test_deleting_a_paid_invoice_claws_the_commission_back(self):
        from blueprints.affiliates import affiliate_balance
        self.login_admin()
        boss = self._onboard('Clawback Lead', 'cblead@example.test', tier='lead')
        member = self._onboard('Clawback Member', 'cbmember@example.test', parent_id=boss['id'])
        _, inv = self._client_with_setup('Refund Coop', member, setup=300000)
        with self.app.app_context():
            db = get_db()
            self.assertAlmostEqual(affiliate_balance(db, member['id']), 45000.0, places=2)
            self.assertAlmostEqual(affiliate_balance(db, boss['id']), 15000.0, places=2)

        self.client.post(f'/hq/invoices/{inv}/delete', follow_redirects=True)
        with self.app.app_context():
            db = get_db()
            # Both the member and the override go back to zero...
            self.assertAlmostEqual(affiliate_balance(db, member['id']), 0.0, places=2)
            self.assertAlmostEqual(affiliate_balance(db, boss['id']), 0.0, places=2)
            # ...but the history stays, as compensating rows, not deletions.
            rows = db.execute('SELECT * FROM affiliate_commissions WHERE invoice_id = ? '
                              'ORDER BY id', (inv,)).fetchall()
            self.assertEqual(len(rows), 4, 'two earnings and two reversals')
            # The originals are marked reversed and stamped...
            originals = [r for r in rows if r['status'] == 'reversed']
            self.assertEqual(len(originals), 2)
            self.assertTrue(all(r['reversed_at'] for r in originals))
            # ...and each is cancelled by its own compensating row.
            clawbacks = [r for r in rows if r['status'] == 'clawback']
            self.assertEqual(len(clawbacks), 2)
            self.assertTrue(all(r['amount'] < 0 and r['reversal_of'] for r in clawbacks))
            self.assertEqual({r['reversal_of'] for r in clawbacks},
                             {r['id'] for r in originals})

    def test_rates_are_configurable_and_only_affect_later_earnings(self):
        from blueprints.affiliates import commission_rates
        self.login_admin()
        aff = self._onboard('Rate Change', 'ratechange@example.test')
        _, first = self._client_with_setup('Old Rate Coop', aff, setup=300000)
        self.assertAlmostEqual(float(self._commissions(first)[0]['amount']), 45000.0, places=2)

        self.client.post('/hq/affiliates/rates', data={
            'affiliate_member_rate': '10', 'affiliate_lead_rate': '5'}, follow_redirects=True)
        with self.app.app_context():
            r = commission_rates(get_db())
            self.assertEqual(r['member'], 10.0)
            self.assertEqual(r['pool'], 15.0, 'the pool is the sum of the two shares')

        _, second = self._client_with_setup('New Rate Coop', aff, setup=300000)
        self.assertAlmostEqual(float(self._commissions(second)[0]['amount']), 30000.0, places=2)
        # The earlier earning is untouched.
        self.assertAlmostEqual(float(self._commissions(first)[0]['amount']), 45000.0, places=2)


if __name__ == '__main__':
    unittest.main()
