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

    def test_ctas_is_off_by_default_with_no_footprint(self):
        with self.app.app_context():
            self.assertFalse(ctas_enabled())
        self.assertEqual(self._ctas_accounts(), [])           # no GL accounts seeded

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


if __name__ == '__main__':
    unittest.main()
