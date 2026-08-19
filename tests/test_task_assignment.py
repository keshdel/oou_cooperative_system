"""
Tests for the task assignment module (permissions.py).

Two things must hold at once:

  * a fresh database behaves exactly as the old hard-coded role checks did, so
    upgrading changes nobody's access by accident; and
  * an admin can reassign any duty — to a whole office, or to one named
    officer — and the change takes effect on the very next request.
"""

import os
import unittest

TEST_DB = os.path.abspath('.test-task-assignment.db')
os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-task-assignment')
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
import permissions as perms  # noqa: E402
from database import get_db  # noqa: E402


OFFICER_PASSWORD = 'OfficerPass123'


class TaskAssignmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app_module.app
        cls.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)

    def setUp(self):
        self.client = self.app.test_client()
        with self.app.app_context():
            db = get_db()
            from werkzeug.security import generate_password_hash
            existing = {r['username'] for r in db.execute('SELECT username FROM users').fetchall()}
            for username, role in (('tr_officer', 'treasurer'),
                                   ('sec_officer', 'secretary'),
                                   ('exco_officer', 'exco')):
                if username not in existing:
                    db.execute('INSERT INTO users (username, password_hash, role, email, is_active) '
                               'VALUES (?, ?, ?, ?, 1)',
                               (username, generate_password_hash(OFFICER_PASSWORD), role,
                                f'{username}@coop.test'))
                else:
                    db.execute('UPDATE users SET role = ?, is_active = 1 WHERE username = ?',
                               (role, username))
            # Every test starts from the built-in defaults.
            db.execute('DELETE FROM role_permissions')
            db.execute('DELETE FROM user_permissions')
            db.commit()

    def login(self, username='admin', password='TestAdmin123'):
        response = self.client.post('/login', data={'username': username, 'password': password},
                                    follow_redirects=False)
        self.assertIn(response.status_code, (302, 303), f'{username} could not sign in')

    def logout(self):
        self.client.get('/logout', follow_redirects=False)

    def user_id(self, username):
        with self.app.app_context():
            return get_db().execute('SELECT id FROM users WHERE username = ?',
                                    (username,)).fetchone()['id']

    # ── catalogue integrity ──────────────────────────────────────────────────

    def test_every_catalogued_endpoint_exists_in_the_app(self):
        """A typo in the catalogue would silently leave a view unguarded by
        permissions (it would fall back to its role list)."""
        known = set(self.app.view_functions)
        missing = sorted(e for e in perms.ENDPOINT_PERMISSIONS if e not in known)
        self.assertEqual(missing, [], f'catalogued endpoints that do not exist: {missing}')

    def test_every_role_guarded_view_is_catalogued(self):
        """Any view carrying role_required should be assignable, otherwise an
        admin cannot delegate it."""
        import ast, glob
        uncatalogued = []
        for path in glob.glob('blueprints/*.py'):
            tree = ast.parse(open(path, encoding='utf-8').read())
            blueprint = None
            for node in tree.body:
                if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                        and getattr(node.value.func, 'id', '') == 'Blueprint'):
                    blueprint = ast.literal_eval(node.value.args[0])
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef):
                    continue
                guarded = any(isinstance(d, ast.Call)
                              and getattr(d.func, 'attr', getattr(d.func, 'id', '')) == 'role_required'
                              for d in node.decorator_list)
                if guarded and f'{blueprint}.{node.name}' not in perms.ENDPOINT_PERMISSIONS:
                    uncatalogued.append(f'{blueprint}.{node.name}')
        self.assertEqual(sorted(uncatalogued), [],
                         f'views guarded by role_required but not catalogued: {uncatalogued}')

    def test_defaults_reproduce_the_previous_role_access(self):
        with self.app.app_context():
            db = get_db()
            treasurer = perms.effective_permissions(db, self.user_id('tr_officer'), 'treasurer')
            secretary = perms.effective_permissions(db, self.user_id('sec_officer'), 'secretary')
            exco = perms.effective_permissions(db, self.user_id('exco_officer'), 'exco')
        # Money duties belong to the Treasurer, not the Secretary or Exco.
        self.assertIn('savings.manage', treasurer)
        self.assertIn('accounting.view', treasurer)
        self.assertNotIn('savings.manage', secretary)
        self.assertNotIn('accounting.view', exco)
        # Register duties belong to the Secretary.
        self.assertIn('members.manage', secretary)
        self.assertNotIn('members.manage', treasurer)
        # Nobody but the President may reassign duties.
        for holder in (treasurer, secretary, exco):
            self.assertNotIn('system.permissions', holder)
            self.assertNotIn('system.settings', holder)

    def test_president_and_super_admin_hold_everything(self):
        with self.app.app_context():
            db = get_db()
            president = perms.effective_permissions(db, 1, 'admin')
            zealous_exco = perms.effective_permissions(db, 2, 'exco', is_super_admin=True)
        self.assertEqual(president, set(perms.PERMISSION_KEYS))
        self.assertEqual(zealous_exco, set(perms.PERMISSION_KEYS))

    # ── the request the user made: treasurer needs member details ────────────

    def test_treasurer_can_reach_member_records_by_default(self):
        self.login('tr_officer', OFFICER_PASSWORD)
        listing = self.client.get('/members', follow_redirects=False)
        self.assertEqual(listing.status_code, 200)
        # …but still cannot alter the register.
        blocked = self.client.get('/members/add', follow_redirects=False)
        self.assertIn(blocked.status_code, (302, 303))
        self.assertIn('/dashboard', blocked.headers.get('Location', ''))

    # ── role-level reassignment ──────────────────────────────────────────────

    def test_admin_can_grant_a_duty_to_a_whole_office(self):
        self.login()
        # Give the Exco the savings book duties the Treasurer holds.
        ticked = [f'{role}:{p["key"]}'
                  for p in perms.PERMISSIONS for role in perms.ASSIGNABLE_ROLES
                  if perms.default_allowed(p['key'], role)]
        ticked.append('exco:savings.manage')
        response = self.client.post('/task-assignment/roles', data={'permission': ticked},
                                    follow_redirects=False)
        self.assertIn(response.status_code, (302, 303))
        self.logout()

        self.login('exco_officer', OFFICER_PASSWORD)
        page = self.client.get('/savings/salary-template', follow_redirects=False)
        self.assertEqual(page.status_code, 200)

    def test_admin_can_withdraw_a_duty_from_a_whole_office(self):
        self.login()
        keep = [f'{role}:{p["key"]}' for p in perms.PERMISSIONS for role in perms.ASSIGNABLE_ROLES
                if perms.default_allowed(p['key'], role) and p['key'] != 'savings.manage']
        self.client.post('/task-assignment/roles', data={'permission': keep})
        self.logout()

        self.login('tr_officer', OFFICER_PASSWORD)
        blocked = self.client.get('/savings/salary-template', follow_redirects=False)
        self.assertIn(blocked.status_code, (302, 303))
        self.assertIn('/dashboard', blocked.headers.get('Location', ''))
        # A duty that was left ticked is untouched.
        self.assertEqual(self.client.get('/loans').status_code, 200)

    def test_restore_defaults_undoes_role_changes(self):
        self.login()
        self.client.post('/task-assignment/roles', data={'permission': []})   # strip everything
        with self.app.app_context():
            self.assertFalse(perms.role_allows(get_db(), 'treasurer', 'savings.manage'))
        self.client.post('/task-assignment/reset')
        with self.app.app_context():
            self.assertTrue(perms.role_allows(get_db(), 'treasurer', 'savings.manage'))

    def test_reserved_permission_cannot_be_handed_to_an_officer(self):
        self.login()
        self.client.post('/task-assignment/roles',
                         data={'permission': ['treasurer:system.permissions']})
        with self.app.app_context():
            db = get_db()
            self.assertFalse(perms.role_allows(db, 'treasurer', 'system.permissions'))
        self.logout()
        self.login('tr_officer', OFFICER_PASSWORD)
        blocked = self.client.get('/task-assignment', follow_redirects=False)
        self.assertIn(blocked.status_code, (302, 303))

    # ── per-officer overrides ────────────────────────────────────────────────

    def test_one_officer_can_be_granted_a_duty_their_office_lacks(self):
        secretary_id = self.user_id('sec_officer')
        self.login()
        form = {f'perm__{p["key"]}': 'inherit' for p in perms.PERMISSIONS}
        form['perm__savings.manage'] = 'allow'
        response = self.client.post(f'/task-assignment/officer/{secretary_id}', data=form,
                                    follow_redirects=False)
        self.assertIn(response.status_code, (302, 303))
        self.logout()

        self.login('sec_officer', OFFICER_PASSWORD)
        self.assertEqual(self.client.get('/savings/salary-template').status_code, 200)
        self.logout()

        # Their colleagues in the same office are unaffected.
        with self.app.app_context():
            db = get_db()
            self.assertFalse(perms.role_allows(db, 'secretary', 'savings.manage'))

    def test_one_officer_can_be_denied_a_duty_their_office_holds(self):
        treasurer_id = self.user_id('tr_officer')
        self.login()
        form = {f'perm__{p["key"]}': 'inherit' for p in perms.PERMISSIONS}
        form['perm__loans.repayments'] = 'deny'
        self.client.post(f'/task-assignment/officer/{treasurer_id}', data=form)
        self.logout()

        self.login('tr_officer', OFFICER_PASSWORD)
        blocked = self.client.get('/loans/export', follow_redirects=False)
        self.assertIn(blocked.status_code, (302, 303))
        self.assertEqual(self.client.get('/loans').status_code, 200)

    def test_clearing_overrides_returns_the_officer_to_their_office(self):
        treasurer_id = self.user_id('tr_officer')
        self.login()
        form = {f'perm__{p["key"]}': 'inherit' for p in perms.PERMISSIONS}
        form['perm__loans.repayments'] = 'deny'
        self.client.post(f'/task-assignment/officer/{treasurer_id}', data=form)
        self.client.post(f'/task-assignment/officer/{treasurer_id}/reset')
        with self.app.app_context():
            matrix = perms.permission_matrix(get_db(), treasurer_id, 'treasurer')
        self.assertIsNone(matrix['loans.repayments']['override'])
        self.assertTrue(matrix['loans.repayments']['effective'])

    def test_matrix_reports_role_default_override_and_result(self):
        exco_id = self.user_id('exco_officer')
        with self.app.app_context():
            db = get_db()
            perms.set_user_permission(db, exco_id, 'accounting.view', True)
            db.commit()
            matrix = perms.permission_matrix(db, exco_id, 'exco')
        self.assertFalse(matrix['accounting.view']['role_default'])
        self.assertTrue(matrix['accounting.view']['override'])
        self.assertTrue(matrix['accounting.view']['effective'])
        self.assertFalse(matrix['accounting.admin']['effective'])

    # ── screens ──────────────────────────────────────────────────────────────

    def test_only_the_president_reaches_the_assignment_screens(self):
        self.login('sec_officer', OFFICER_PASSWORD)
        for path in ('/task-assignment', f'/task-assignment/officer/{self.user_id("tr_officer")}'):
            response = self.client.get(path, follow_redirects=False)
            self.assertIn(response.status_code, (302, 303), path)

    def test_assignment_screens_render_for_the_president(self):
        self.login()
        matrix = self.client.get('/task-assignment')
        self.assertEqual(matrix.status_code, 200)
        self.assertIn(b'Task Assignment', matrix.data)
        self.assertIn(b'Record savings and payouts', matrix.data)

        officer = self.client.get(f'/task-assignment/officer/{self.user_id("tr_officer")}')
        self.assertEqual(officer.status_code, 200)
        self.assertIn(b'Follow office', officer.data)

    def test_menu_follows_assigned_duties_not_the_role_name(self):
        self.login('tr_officer', OFFICER_PASSWORD)
        before = self.client.get('/dashboard')
        self.assertIn(b'/trial-balance', before.data)
        self.logout()

        self.login()
        keep = [f'{role}:{p["key"]}' for p in perms.PERMISSIONS for role in perms.ASSIGNABLE_ROLES
                if perms.default_allowed(p['key'], role) and p['key'] != 'accounting.view']
        self.client.post('/task-assignment/roles', data={'permission': keep})
        self.logout()

        self.login('tr_officer', OFFICER_PASSWORD)
        after = self.client.get('/dashboard')
        self.assertNotIn(b'/trial-balance', after.data)

    def test_permission_changes_are_audited(self):
        self.login()
        self.client.post('/task-assignment/roles', data={'permission': ['exco:accounting.view']})
        with self.app.app_context():
            row = get_db().execute(
                "SELECT description FROM audit_log WHERE action = 'UPDATE_ROLE_PERMISSIONS' "
                "ORDER BY id DESC"
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertIn('change', row['description'].lower())


if __name__ == '__main__':
    unittest.main()
