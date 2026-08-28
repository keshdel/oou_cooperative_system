"""SMS fallback: number handling, per-cooperative config, opt-out, and the rule
that a member who already has the app is not also texted."""

import os
import unittest

TEST_DB = os.path.abspath('.test-sms.db')
os.environ.setdefault('SECRET_KEY', 'test-secret-sms')
os.environ.setdefault('ADMIN_PASSWORD', 'TestAdmin123')
os.environ.setdefault('FLASK_DEBUG', '1')
os.environ.setdefault('FIELD_ENCRYPTION_KEY', '05SmPJhNFMKwg9NysnBdQjKtqn3VwWDl1IiPIMAg2as=')
os.environ.pop('DATABASE_URL', None)
os.environ['SQLITE_DB_PATH'] = TEST_DB
try:
    os.remove(TEST_DB)
except FileNotFoundError:
    pass

import app as app_module          # noqa: E402
import sms                        # noqa: E402
import utils                      # noqa: E402
from database import get_db       # noqa: E402


class PhoneNumberTests(unittest.TestCase):
    def test_local_forms_become_e164_digits(self):
        for raw in ('08012345678', '0801 234 5678', '+234 801 234 5678',
                    '234-801-2345678', '002348012345678', '8012345678'):
            self.assertEqual(sms.normalise_msisdn(raw), '2348012345678', raw)

    def test_nothing_usable_returns_empty(self):
        self.assertEqual(sms.normalise_msisdn(''), '')
        self.assertEqual(sms.normalise_msisdn('not a number'), '')

    def test_country_code_is_configurable(self):
        # A cooperative outside Nigeria changes one setting.
        self.assertEqual(sms.normalise_msisdn('0712345678', country_code='254'),
                         '254712345678')

    def test_looks_sendable_rejects_rubbish(self):
        self.assertTrue(sms.looks_sendable('2348012345678'))
        self.assertFalse(sms.looks_sendable('234'))
        self.assertFalse(sms.looks_sendable(''))


class _SmsTestCase(unittest.TestCase):
    """Shared plumbing: a configured cooperative and a stubbed provider."""

    @classmethod
    def setUpClass(cls):
        cls.app = app_module.app
        cls.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)

    def setUp(self):
        self.sent = []
        self.result = (True, 'msg-1', '')
        self._real_send = sms.TermiiProvider.send
        sms.TermiiProvider.send = lambda _self, m, t: self._record(m, t)

    def tearDown(self):
        sms.TermiiProvider.send = self._real_send
        with self.app.app_context():
            db = get_db()
            db.execute("DELETE FROM settings WHERE key LIKE 'sms\\_%' ESCAPE '\\'")
            db.execute('DELETE FROM sms_log')
            db.commit()

    def _record(self, msisdn, text):
        self.sent.append((msisdn, text))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def _configure(self, db, **overrides):
        values = {'sms_enabled': '1', 'sms_provider': 'termii',
                  'sms_api_key': 'test-key', 'sms_sender_id': 'TestCoop'}
        values.update(overrides)
        for key, val in values.items():
            db.execute('DELETE FROM settings WHERE key = ?', (key,))
            db.execute('INSERT INTO settings (key, value) VALUES (?, ?)', (key, val))
        db.commit()

    def _last_log(self, db):
        return db.execute('SELECT * FROM sms_log ORDER BY id DESC').fetchone()


class ConfigurationTests(_SmsTestCase):
    def test_off_until_a_cooperative_configures_it(self):
        with self.app.app_context():
            self.assertFalse(sms.sms_enabled(get_db()))

    def test_needs_both_the_switch_and_a_key(self):
        with self.app.app_context():
            db = get_db()
            self._configure(db, sms_api_key='')
            self.assertFalse(sms.sms_enabled(db))
            self._configure(db)
            self.assertTrue(sms.sms_enabled(db))

    def test_disabled_cooperative_sends_nothing(self):
        with self.app.app_context():
            self.assertFalse(sms.send_sms(get_db(), '08012345678', 'hello'))
            self.assertEqual(self.sent, [])

    def test_provider_is_swappable(self):
        cfg = {'sms_provider': 'africastalking', 'sms_api_key': 'k'}
        self.assertIsInstance(sms.get_provider(cfg), sms.AfricasTalkingProvider)
        self.assertIsInstance(sms.get_provider({'sms_api_key': 'k'}), sms.TermiiProvider)


class SendingTests(_SmsTestCase):
    def test_send_records_the_attempt(self):
        with self.app.app_context():
            db = get_db()
            self._configure(db)
            self.assertTrue(sms.send_sms(db, '08012345678',
                                         'Your savings were received', purpose='savings'))
            row = self._last_log(db)
            self.assertEqual(row['status'], 'sent')
            self.assertEqual(row['msisdn'], '2348012345678')
            self.assertEqual(row['purpose'], 'savings')
            self.assertEqual(row['provider_ref'], 'msg-1')

    def test_provider_failure_is_logged_not_raised(self):
        with self.app.app_context():
            db = get_db()
            self._configure(db)
            self.result = RuntimeError('network down')
            self.assertFalse(sms.send_sms(db, '08012345678', 'hello'))
            row = self._last_log(db)
            self.assertEqual(row['status'], 'failed')
            self.assertIn('network down', row['error'] or '')

    def test_unusable_number_is_skipped_before_spending_credit(self):
        with self.app.app_context():
            db = get_db()
            self._configure(db)
            self.assertFalse(sms.send_sms(db, 'rubbish', 'hello'))
            self.assertEqual(self.sent, [])
            self.assertEqual(self._last_log(db)['status'], 'skipped')

    def test_long_message_is_trimmed(self):
        with self.app.app_context():
            db = get_db()
            self._configure(db)
            sms.send_sms(db, '08012345678', 'x' * 900)
            self.assertEqual(len(self.sent[0][1]), 640)

    def test_opted_out_member_is_not_texted(self):
        with self.app.app_context():
            db = get_db()
            self._configure(db)
            db.execute("INSERT INTO members (member_number, first_name, last_name, status, "
                       "date_joined, sms_optout) VALUES "
                       "('SMS/OPTOUT', 'Opted', 'Out', 'active', '2024-01-01', 1)")
            mid = db.execute("SELECT id FROM members WHERE member_number = 'SMS/OPTOUT'"
                             ).fetchone()['id']
            db.commit()

            self.assertFalse(sms.send_sms(db, '08012345678', 'hello', member_id=mid))
            self.assertEqual(self.sent, [])
            self.assertEqual(self._last_log(db)['status'], 'skipped')


class ChannelRoutingTests(_SmsTestCase):
    """Push is free; SMS is not. A member with the app must not be billed for."""

    def _member_with_login(self, db, number, phone='08012345678'):
        # utils.member_for_user links a user to a member by email address.
        email = f'{number.lower().replace("/", "-")}@example.test'
        db.execute("INSERT INTO members (member_number, first_name, last_name, status, "
                   "date_joined, phone, email) VALUES (?, 'Test', 'Member', 'active', "
                   "'2024-01-01', ?, ?)", (number, phone, email))
        mid = db.execute('SELECT id FROM members WHERE member_number = ?',
                         (number,)).fetchone()['id']
        db.execute("INSERT INTO users (username, email, password_hash, role) "
                   "VALUES (?, ?, 'x', 'member')", (number, email))
        uid = db.execute('SELECT id FROM users WHERE username = ?', (number,)).fetchone()['id']
        db.commit()
        return mid, uid

    def setUp(self):
        super().setUp()
        self.calls = []
        self._real_send_sms = sms.send_sms
        sms.send_sms = lambda *a, **k: self.calls.append((a, k)) or True

    def tearDown(self):
        sms.send_sms = self._real_send_sms
        with self.app.app_context():
            db = get_db()
            db.execute("DELETE FROM mobile_devices WHERE push_token = 'ExponentPushToken[test]'")
            db.execute("DELETE FROM users WHERE username LIKE 'SMS/ROUTE%'")
            db.execute("DELETE FROM members WHERE member_number LIKE 'SMS/ROUTE%'")
            db.commit()
        super().tearDown()

    def test_member_with_the_app_is_not_texted(self):
        with self.app.app_context():
            db = get_db()
            self._configure(db)
            mid, uid = self._member_with_login(db, 'SMS/ROUTE1')
            db.execute('INSERT INTO mobile_devices (user_id, member_id, platform, push_token, '
                       'enabled) VALUES (?, ?, ?, ?, 1)',
                       (uid, mid, 'android', 'ExponentPushToken[test]'))
            db.commit()

            utils.notify(db, uid, 'Savings received', 'We got your deposit.')
            self.assertEqual(self.calls, [])

    def test_member_without_the_app_gets_a_text(self):
        with self.app.app_context():
            db = get_db()
            self._configure(db)
            mid, uid = self._member_with_login(db, 'SMS/ROUTE2')

            utils.notify(db, uid, 'Savings received', 'We got your deposit.')

            self.assertEqual(len(self.calls), 1)
            args, kwargs = self.calls[0]
            self.assertEqual(args[1], '08012345678')
            self.assertEqual(args[2], 'Savings received: We got your deposit.')
            self.assertEqual(kwargs['member_id'], mid)
            self.assertEqual(kwargs['purpose'], 'notification')

    def test_no_phone_number_means_no_send(self):
        with self.app.app_context():
            db = get_db()
            self._configure(db)
            _, uid = self._member_with_login(db, 'SMS/ROUTE3', phone='')

            utils.notify(db, uid, 'Savings received', 'We got your deposit.')
            self.assertEqual(self.calls, [])

    def test_notify_survives_an_sms_failure(self):
        # SMS is a courtesy channel; it must not break the in-app notification.
        with self.app.app_context():
            db = get_db()
            self._configure(db)
            _, uid = self._member_with_login(db, 'SMS/ROUTE4')
            sms.send_sms = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom'))

            utils.notify(db, uid, 'Savings received', 'We got your deposit.')
            db.commit()

            row = db.execute('SELECT * FROM notifications WHERE user_id = ? ORDER BY id DESC',
                             (uid,)).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row['title'], 'Savings received')


if __name__ == '__main__':
    unittest.main()
