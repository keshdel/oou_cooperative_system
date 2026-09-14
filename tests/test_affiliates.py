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
        self.client.post('/api/marketing/leads', json=payload)
        with self.app.app_context():
            return get_db().execute('SELECT * FROM marketing_leads WHERE society_name = ?',
                                    (society,)).fetchone()

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


if __name__ == '__main__':
    unittest.main()
