"""The Training Centre: private to the cooperative, open to members, with the
facilitator's material behind its own duty."""

import os
import unittest

TEST_DB = os.path.abspath('.test-training.db')
os.environ.setdefault('SECRET_KEY', 'test-secret-training')
os.environ.setdefault('ADMIN_PASSWORD', 'TestAdmin123')
os.environ.setdefault('FLASK_DEBUG', '1')
os.environ.setdefault('FIELD_ENCRYPTION_KEY', '05SmPJhNFMKwg9NysnBdQjKtqn3VwWDl1IiPIMAg2as=')
os.environ.pop('DATABASE_URL', None)
os.environ['SQLITE_DB_PATH'] = TEST_DB
try:
    os.remove(TEST_DB)
except FileNotFoundError:
    pass

from werkzeug.security import generate_password_hash   # noqa: E402

import app as app_module                               # noqa: E402
from blueprints import training as training_bp         # noqa: E402
from database import get_db                            # noqa: E402


class _TrainingTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = app_module.app
        cls.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        with cls.app.app_context():
            db = get_db()
            for username, role in (('train-member', 'member'), ('train-sec', 'secretary')):
                db.execute('DELETE FROM users WHERE username = ?', (username,))
                db.execute(
                    'INSERT INTO users (username, email, password_hash, role, is_active) '
                    'VALUES (?, ?, ?, ?, 1)',
                    (username, f'{username}@example.test',
                     generate_password_hash('TrainPass123'), role))
            db.commit()

    def _login(self, client, username):
        return client.post('/login', data={'username': username,
                                           'password': 'TrainPass123'},
                           follow_redirects=True)


class AccessTests(_TrainingTestCase):

    def test_the_course_is_not_public(self):
        with self.app.test_client() as client:
            resp = client.get('/training/', follow_redirects=False)
            self.assertEqual(resp.status_code, 302)
            self.assertIn('/login', resp.headers['Location'])

    def test_a_member_can_read_the_outline_and_the_slides(self):
        with self.app.test_client() as client:
            self._login(client, 'train-member')

            outline = client.get('/training/')
            self.assertEqual(outline.status_code, 200)
            body = outline.get_data(as_text=True)
            self.assertIn('What we will cover', body)
            self.assertIn('The work you do every week', body)
            self.assertIn('What happens to the money', body)

            slides = client.get('/training/slides')
            self.assertEqual(slides.status_code, 200)
            self.assertIn('Running our society on the computer',
                          slides.get_data(as_text=True))

    def test_the_facilitator_material_is_not_open_to_members(self):
        with self.app.test_client() as client:
            self._login(client, 'train-member')
            resp = client.get('/training/facilitator-notes', follow_redirects=False)
            self.assertNotEqual(resp.status_code, 200)

    def test_an_officer_can_open_the_facilitator_material(self):
        with self.app.test_client() as client:
            self._login(client, 'train-sec')
            for slug, heading in (('lesson-plan', 'Lesson Plan'),
                                  ('facilitator-notes', 'Facilitator Notes')):
                resp = client.get(f'/training/{slug}')
                self.assertEqual(resp.status_code, 200, slug)
                self.assertIn(heading, resp.get_data(as_text=True))

    def test_an_unknown_document_is_not_found(self):
        with self.app.test_client() as client:
            self._login(client, 'train-sec')
            self.assertEqual(client.get('/training/no-such-document').status_code, 404)

    def test_a_request_cannot_walk_out_of_the_training_folder(self):
        with self.app.test_client() as client:
            self._login(client, 'train-sec')
            for attempt in ('../../app.py', '..%2f..%2fapp.py', 'exco-training-deck.html'):
                resp = client.get(f'/training/{attempt}')
                self.assertIn(resp.status_code, (404, 308), attempt)


class ContentTests(_TrainingTestCase):
    """The outline is what officers read first, so it must stay complete."""

    def test_every_session_of_both_days_is_listed(self):
        days = training_bp.COURSE
        self.assertEqual([d['day'] for d in days], ['Day one', 'Day two'])
        for day in days:
            self.assertGreaterEqual(len(day['sessions']), 6, day['day'])
            for title, detail in day['sessions']:
                self.assertTrue(title and detail)

    def test_the_material_it_offers_actually_exists(self):
        self.assertIsNotNone(training_bp._read(training_bp.DECK_FILE))
        for meta in training_bp.DOCUMENTS.values():
            self.assertIsNotNone(training_bp._read(meta['file']), meta['file'])


if __name__ == '__main__':
    unittest.main()
