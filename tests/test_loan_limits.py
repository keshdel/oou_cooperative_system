import sqlite3
import unittest

from loan_limits import application_error, limits, validate_settings


class LoanLimitTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.execute('CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)')

    def tearDown(self):
        self.db.close()

    def put(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO settings VALUES (?, ?)', (key, str(value)))

    def test_default_preserves_amount_but_enforces_general_tenure(self):
        self.assertIsNone(application_error(self.db, 'Regular', 9000000, 18))
        self.assertIn('18 months', application_error(self.db, 'Regular', 1, 19))

    def test_amount_boundary_and_type_independence(self):
        self.put('max_loan_amount', '250000.50')
        for name in limits(self.db)['tenures']:
            self.assertIsNone(application_error(self.db, name, 250000.50, 12))
            self.assertIn('regardless of savings', application_error(self.db, name, 250000.51, 12))

    def test_type_tenure_cannot_exceed_general(self):
        self.put('max_tenure_school_fees', 6)
        self.put('max_tenure_housing', 60)
        self.assertIsNone(application_error(self.db, 'School Fees', 100, 6))
        self.assertIn('6 months', application_error(self.db, 'School Fees', 100, 7))
        self.assertEqual(limits(self.db)['tenures']['Housing'], 18)
        self.assertEqual(limits(self.db)['tenures']['Regular'], 18)

    def test_invalid_settings_and_nonfinite_applications(self):
        for value in ('nan', 'inf', '-1', 'abc', '0.001'):
            with self.assertRaises(ValueError):
                validate_settings({'max_loan_amount': value})
        for value in ('1.5', '61', '-1', 'nan'):
            with self.assertRaises(ValueError):
                validate_settings({'max_tenure_regular': value})
        with self.assertRaises(ValueError):
            validate_settings({'max_tenure_months': '0'})
        self.assertIsNotNone(application_error(self.db, 'Regular', float('nan'), 1))
        self.assertIsNotNone(application_error(self.db, 'Regular', float('inf'), 1))
