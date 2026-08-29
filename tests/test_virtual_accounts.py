"""Dedicated virtual accounts: identifying an inflow, banking it before anyone
decides what it is for, applying it, and undoing that decision.

The money invariants matter more than the screens here, so most of these check
the general ledger rather than the HTML.
"""

import os
import unittest

TEST_DB = os.path.abspath('.test-va.db')
os.environ.setdefault('SECRET_KEY', 'test-secret-va')
os.environ.setdefault('ADMIN_PASSWORD', 'TestAdmin123')
os.environ.setdefault('FLASK_DEBUG', '1')
os.environ.setdefault('FIELD_ENCRYPTION_KEY', '05SmPJhNFMKwg9NysnBdQjKtqn3VwWDl1IiPIMAg2as=')
os.environ.pop('DATABASE_URL', None)
os.environ['SQLITE_DB_PATH'] = TEST_DB
try:
    os.remove(TEST_DB)
except FileNotFoundError:
    pass

import app as app_module                       # noqa: E402
import virtual_accounts as va                   # noqa: E402
from database import get_db, last_insert_id     # noqa: E402
from ledger import (LOANS_RECEIVABLE, MEMBER_DEPOSITS, account_balance,   # noqa: E402
                    get_default_cash_account, reverse_journal_entry)


_REF_SEQ = 0


def owed(db, code):
    """What the cooperative owes on a liability account.

    account_balance is debits - credits, so a liability sits negative; flipping
    it here keeps the tests reading the way an officer would think about it.
    """
    return -account_balance(db, code)


class _VaTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app_module.app
        cls.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)

    def setUp(self):
        with self.app.app_context():
            db = get_db()
            va.set_va_enabled(db, True)
            va.set_setting(db, 'va_allocation_rule', 'savings')
            db.commit()

    def tearDown(self):
        with self.app.app_context():
            db = get_db()
            db.execute('DELETE FROM virtual_account_allocations')
            db.execute('DELETE FROM virtual_account_receipts')
            db.execute('DELETE FROM member_virtual_accounts')
            db.execute("DELETE FROM savings WHERE payment_method IN ('virtual_account', 'reversal')")
            db.execute("DELETE FROM repayments WHERE payment_method = 'virtual_account'")
            db.execute("DELETE FROM ctas_schedule WHERE subscription_id IN "
                       "(SELECT id FROM ctas_subscriptions WHERE member_id IN "
                       "(SELECT id FROM members WHERE member_number LIKE 'VA/%'))")
            db.execute("DELETE FROM ctas_subscriptions WHERE member_id IN "
                       "(SELECT id FROM members WHERE member_number LIKE 'VA/%')")
            db.execute("DELETE FROM ctas_cycles WHERE name LIKE 'VA test%'")
            db.execute("DELETE FROM members WHERE member_number LIKE 'VA/%'")
            db.execute("DELETE FROM settings WHERE key LIKE 'va\\_%' ESCAPE '\\'")
            db.commit()

    # ── fixtures ──────────────────────────────────────────────────────────────

    def _member(self, db, number, account_number='9900000001'):
        db.execute("INSERT INTO members (member_number, first_name, last_name, status, "
                   "date_joined, email, total_savings) "
                   "VALUES (?, 'Vee', 'Ay', 'active', '2024-01-01', ?, 0)",
                   (number, f"{number.replace('/', '-').lower()}@example.test"))
        mid = last_insert_id(db)
        db.execute("INSERT INTO member_virtual_accounts (member_id, provider, customer_code, "
                   "account_number, account_name, bank_name, status) "
                   "VALUES (?, 'paystack', ?, ?, 'Vee Ay', 'Wema Bank', 'active')",
                   (mid, f'CUS_{number}', account_number))
        db.commit()
        return mid

    def _loan(self, db, member_id, principal=100000.0, total=110000.0):
        db.execute("INSERT INTO loans (loan_number, member_id, amount, purpose, tenure, "
                   "interest_rate, interest_method, total_repayment, balance, status, "
                   "date_applied) "
                   "VALUES (?, ?, ?, 'Regular', 12, 10, 'reducing_annual', ?, ?, "
                   "'active', '2026-01-01')",
                   (f'LN/VA/{member_id}', member_id, principal, total, total))
        lid = last_insert_id(db)
        db.commit()
        return lid

    def _inflow(self, db, amount, account_number='9900000001', reference=None):
        # journal_entries.reference is unique, so every inflow in the suite needs
        # its own — as it would in life, where the gateway supplies it.
        global _REF_SEQ
        _REF_SEQ += 1
        return va.record_receipt(
            db, provider_reference=reference or f'PSTK-VA-{_REF_SEQ}',
            amount=amount, account_number=account_number,
            sender_name='Vee Ay', sender_bank='GTBank')


# ── Identifying the money ─────────────────────────────────────────────────────

class MatchingTests(_VaTestCase):
    def test_inflow_is_matched_by_the_account_it_landed_in(self):
        with self.app.app_context():
            db = get_db()
            mid = self._member(db, 'VA/1', '9900000123')
            receipt_id, is_new = self._inflow(db, 5000, '9900000123')
            db.commit()

            self.assertTrue(is_new)
            row = db.execute('SELECT * FROM virtual_account_receipts WHERE id = ?',
                             (receipt_id,)).fetchone()
            self.assertEqual(row['member_id'], mid)
            self.assertEqual(row['status'], 'unallocated')

    def test_unknown_account_is_parked_not_guessed(self):
        with self.app.app_context():
            db = get_db()
            self._member(db, 'VA/2', '9900000123')
            receipt_id, _ = self._inflow(db, 5000, '9999999999')
            db.commit()

            row = db.execute('SELECT * FROM virtual_account_receipts WHERE id = ?',
                             (receipt_id,)).fetchone()
            self.assertIsNone(row['member_id'])
            self.assertEqual(row['status'], 'unmatched')

    def test_the_same_transfer_is_never_banked_twice(self):
        # Gateways redeliver webhooks; a repeat must change nothing.
        with self.app.app_context():
            db = get_db()
            self._member(db, 'VA/3', '9900000123')
            first, new_first = self._inflow(db, 5000, '9900000123', reference='DUP-1')
            db.commit()
            second, new_second = self._inflow(db, 5000, '9900000123', reference='DUP-1')
            db.commit()

            self.assertTrue(new_first)
            self.assertFalse(new_second)
            self.assertEqual(first, second)
            count = db.execute('SELECT COUNT(*) AS n FROM virtual_account_receipts').fetchone()
            self.assertEqual(count['n'], 1)


# ── Banking it before anyone decides ──────────────────────────────────────────

class BankingTests(_VaTestCase):
    def test_money_hits_the_books_the_moment_it_arrives(self):
        with self.app.app_context():
            db = get_db()
            self._member(db, 'VA/4', '9900000123')
            va.set_setting(db, 'va_allocation_rule', 'manual')   # nobody has decided yet
            db.commit()

            cash = get_default_cash_account(db)
            before_cash = account_balance(db, cash)
            before_hold = owed(db, va.VA_UNALLOCATED)

            self._inflow(db, 7500, '9900000123')
            db.commit()

            self.assertAlmostEqual(account_balance(db, cash), before_cash + 7500, places=2)
            self.assertAlmostEqual(owed(db, va.VA_UNALLOCATED), before_hold + 7500, places=2)

    def test_unmatched_money_is_still_banked(self):
        # We do not know whose it is, but the cooperative is holding it.
        with self.app.app_context():
            db = get_db()
            before = owed(db, va.VA_UNALLOCATED)
            self._inflow(db, 3000, '9999999999')
            db.commit()
            self.assertAlmostEqual(owed(db, va.VA_UNALLOCATED), before + 3000, places=2)

    def test_the_queue_agrees_with_the_holding_account(self):
        with self.app.app_context():
            db = get_db()
            self._member(db, 'VA/5', '9900000123')
            va.set_setting(db, 'va_allocation_rule', 'manual')
            db.commit()
            before = owed(db, va.VA_UNALLOCATED)

            self._inflow(db, 1000, '9900000123')
            self._inflow(db, 2500, '9999999999')
            db.commit()

            self.assertAlmostEqual(va.pending_total(db),
                                   owed(db, va.VA_UNALLOCATED) - before, places=2)


# ── Applying it ───────────────────────────────────────────────────────────────

class AllocationTests(_VaTestCase):
    def test_savings_rule_moves_it_out_of_holding_into_deposits(self):
        with self.app.app_context():
            db = get_db()
            mid = self._member(db, 'VA/6', '9900000123')
            hold_before = owed(db, va.VA_UNALLOCATED)
            dep_before = owed(db, MEMBER_DEPOSITS)

            receipt_id, _ = self._inflow(db, 5000, '9900000123')
            applied, _ = va.auto_allocate(db, receipt_id)
            db.commit()

            self.assertTrue(applied)
            self.assertAlmostEqual(owed(db, va.VA_UNALLOCATED), hold_before, places=2)
            self.assertAlmostEqual(owed(db, MEMBER_DEPOSITS), dep_before + 5000, places=2)
            member = db.execute('SELECT total_savings FROM members WHERE id = ?',
                                (mid,)).fetchone()
            self.assertAlmostEqual(float(member['total_savings']), 5000, places=2)

    def test_loan_first_rule_clears_the_loan_then_saves_the_rest(self):
        with self.app.app_context():
            db = get_db()
            mid = self._member(db, 'VA/7', '9900000123')
            loan_id = self._loan(db, mid, principal=10000, total=11000)
            va.set_setting(db, 'va_allocation_rule', 'loan_first')
            db.commit()

            receipt_id, _ = self._inflow(db, 15000, '9900000123')
            applied, _ = va.auto_allocate(db, receipt_id)
            db.commit()

            self.assertTrue(applied)
            loan = db.execute('SELECT * FROM loans WHERE id = ?', (loan_id,)).fetchone()
            self.assertEqual(float(loan['balance']), 0)
            self.assertEqual(loan['status'], 'completed')
            member = db.execute('SELECT total_savings FROM members WHERE id = ?',
                                (mid,)).fetchone()
            self.assertAlmostEqual(float(member['total_savings']), 4000, places=2)  # 15000 - 11000

    def test_manual_rule_leaves_the_money_for_an_officer(self):
        with self.app.app_context():
            db = get_db()
            self._member(db, 'VA/8', '9900000123')
            va.set_setting(db, 'va_allocation_rule', 'manual')
            db.commit()

            receipt_id, _ = self._inflow(db, 5000, '9900000123')
            applied, _ = va.auto_allocate(db, receipt_id)
            db.commit()

            self.assertFalse(applied)
            row = db.execute('SELECT status FROM virtual_account_receipts WHERE id = ?',
                             (receipt_id,)).fetchone()
            self.assertEqual(row['status'], 'unallocated')

    def test_cannot_apply_more_than_arrived(self):
        # Otherwise the books would say money was received that never was.
        with self.app.app_context():
            db = get_db()
            mid = self._member(db, 'VA/9', '9900000123')
            va.set_setting(db, 'va_allocation_rule', 'manual')
            db.commit()
            receipt_id, _ = self._inflow(db, 5000, '9900000123')
            db.commit()

            ok, message = va.apply_plan(
                db, receipt_id, [{'target': 'savings', 'loan_id': None, 'amount': 9000}])
            self.assertFalse(ok)
            self.assertIn('left to apply', message)

    def test_partial_application_leaves_the_rest_waiting(self):
        with self.app.app_context():
            db = get_db()
            self._member(db, 'VA/10', '9900000123')
            va.set_setting(db, 'va_allocation_rule', 'manual')
            db.commit()
            receipt_id, _ = self._inflow(db, 5000, '9900000123')

            ok, _ = va.apply_plan(
                db, receipt_id, [{'target': 'savings', 'loan_id': None, 'amount': 2000}])
            db.commit()

            self.assertTrue(ok)
            row = db.execute('SELECT * FROM virtual_account_receipts WHERE id = ?',
                             (receipt_id,)).fetchone()
            self.assertEqual(row['status'], 'part_allocated')
            self.assertAlmostEqual(float(row['allocated_amount']), 2000, places=2)

    def test_unmatched_money_cannot_be_applied(self):
        with self.app.app_context():
            db = get_db()
            receipt_id, _ = self._inflow(db, 5000, '9999999999')
            db.commit()
            ok, message = va.apply_plan(
                db, receipt_id, [{'target': 'savings', 'loan_id': None, 'amount': 5000}])
            self.assertFalse(ok)
            self.assertIn('not matched', message)

    def test_share_capital_split_is_honoured(self):
        with self.app.app_context():
            db = get_db()
            mid = self._member(db, 'VA/11', '9900000123')
            db.execute("DELETE FROM settings WHERE key = 'share_capital_pct'")
            db.execute("INSERT INTO settings (key, value) VALUES ('share_capital_pct', '10')")
            db.commit()
            try:
                receipt_id, _ = self._inflow(db, 10000, '9900000123')
                va.auto_allocate(db, receipt_id)
                db.commit()

                member = db.execute('SELECT * FROM members WHERE id = ?', (mid,)).fetchone()
                self.assertAlmostEqual(float(member['total_savings']), 9000, places=2)
                self.assertAlmostEqual(float(member['shares_value'] or 0), 1000, places=2)
            finally:
                db.execute("DELETE FROM settings WHERE key = 'share_capital_pct'")
                db.execute("INSERT INTO settings (key, value) VALUES ('share_capital_pct', '0')")
                db.commit()


# ── Undoing a decision ────────────────────────────────────────────────────────

class ReversalTests(_VaTestCase):
    def _entry_for(self, db, module, source_id):
        return db.execute(
            'SELECT id FROM journal_entries WHERE source_module = ? AND source_id = ? '
            'ORDER BY id DESC', (module, source_id)).fetchone()

    def test_undoing_a_savings_allocation_returns_it_to_unallocated(self):
        with self.app.app_context():
            db = get_db()
            mid = self._member(db, 'VA/12', '9900000123')
            receipt_id, _ = self._inflow(db, 5000, '9900000123')
            va.auto_allocate(db, receipt_id)
            db.commit()

            alloc = db.execute('SELECT * FROM virtual_account_allocations WHERE receipt_id = ?',
                               (receipt_id,)).fetchone()
            entry = self._entry_for(db, 'va_savings', alloc['id'])
            self.assertIsNotNone(entry)

            reverse_journal_entry(db, entry['id'], reason='Applied to the wrong thing')
            db.commit()

            receipt = db.execute('SELECT * FROM virtual_account_receipts WHERE id = ?',
                                 (receipt_id,)).fetchone()
            self.assertEqual(receipt['status'], 'unallocated')
            self.assertAlmostEqual(float(receipt['allocated_amount']), 0, places=2)

            # The member's savings are undone, but the money is still held.
            member = db.execute('SELECT total_savings FROM members WHERE id = ?',
                                (mid,)).fetchone()
            self.assertAlmostEqual(float(member['total_savings']), 0, places=2)

    def test_money_returned_to_unallocated_can_be_applied_somewhere_else(self):
        with self.app.app_context():
            db = get_db()
            mid = self._member(db, 'VA/13', '9900000123')
            loan_id = self._loan(db, mid, principal=10000, total=11000)
            receipt_id, _ = self._inflow(db, 5000, '9900000123')
            va.auto_allocate(db, receipt_id)          # savings rule
            db.commit()

            alloc = db.execute('SELECT * FROM virtual_account_allocations WHERE receipt_id = ?',
                               (receipt_id,)).fetchone()
            entry = self._entry_for(db, 'va_savings', alloc['id'])
            reverse_journal_entry(db, entry['id'], reason='Should have gone to the loan')
            db.commit()

            ok, _ = va.apply_plan(db, receipt_id,
                                  [{'target': 'loan', 'loan_id': loan_id, 'amount': 5000}])
            db.commit()

            self.assertTrue(ok)
            loan = db.execute('SELECT balance FROM loans WHERE id = ?', (loan_id,)).fetchone()
            self.assertAlmostEqual(float(loan['balance']), 6000, places=2)

    def test_the_arrival_itself_cannot_be_reversed(self):
        # The money really did land in the bank; pretending otherwise would put
        # the ledger out of step with the bank statement.
        from ledger import reversal_support
        can, why = reversal_support('va_receipt')
        self.assertFalse(can)
        self.assertIn('genuinely arrived', why)

    def test_undoing_a_loan_allocation_puts_the_balance_back(self):
        with self.app.app_context():
            db = get_db()
            mid = self._member(db, 'VA/14', '9900000123')
            loan_id = self._loan(db, mid, principal=10000, total=11000)
            va.set_setting(db, 'va_allocation_rule', 'loan_first')
            db.commit()

            receipt_id, _ = self._inflow(db, 4000, '9900000123')
            va.auto_allocate(db, receipt_id)
            db.commit()

            alloc = db.execute("SELECT * FROM virtual_account_allocations "
                               "WHERE receipt_id = ? AND target = 'loan'",
                               (receipt_id,)).fetchone()
            entry = self._entry_for(db, 'va_loan', alloc['id'])
            reverse_journal_entry(db, entry['id'], reason='Wrong loan')
            db.commit()

            loan = db.execute('SELECT balance FROM loans WHERE id = ?', (loan_id,)).fetchone()
            self.assertAlmostEqual(float(loan['balance']), 11000, places=2)
            receipt = db.execute('SELECT status FROM virtual_account_receipts WHERE id = ?',
                                 (receipt_id,)).fetchone()
            self.assertEqual(receipt['status'], 'unallocated')


# ── The webhook ───────────────────────────────────────────────────────────────

class WebhookTests(_VaTestCase):
    def _payload(self, amount_kobo, account_number, reference):
        return {
            'event': 'charge.success',
            'data': {
                'channel': 'dedicated_nuban',
                'reference': reference,
                'amount': amount_kobo,
                'customer': {'customer_code': 'CUS_x'},
                'metadata': {'receiver_account_number': account_number},
                'authorization': {'account_name': 'Vee Ay', 'sender_bank': 'GTBank'},
            },
        }

    def test_a_transfer_webhook_banks_and_applies_the_money(self):
        from blueprints.payments_bp import _record_virtual_account_inflow
        with self.app.app_context():
            db = get_db()
            mid = self._member(db, 'VA/15', '9900000123')

            _record_virtual_account_inflow(
                db, self._payload(500000, '9900000123', 'PSTK-HOOK-1')['data'])

            member = db.execute('SELECT total_savings FROM members WHERE id = ?',
                                (mid,)).fetchone()
            self.assertAlmostEqual(float(member['total_savings']), 5000, places=2)

    def test_a_replayed_webhook_changes_nothing(self):
        from blueprints.payments_bp import _record_virtual_account_inflow
        with self.app.app_context():
            db = get_db()
            mid = self._member(db, 'VA/16', '9900000123')
            payload = self._payload(500000, '9900000123', 'PSTK-HOOK-2')['data']

            _record_virtual_account_inflow(db, payload)
            _record_virtual_account_inflow(db, payload)

            member = db.execute('SELECT total_savings FROM members WHERE id = ?',
                                (mid,)).fetchone()
            self.assertAlmostEqual(float(member['total_savings']), 5000, places=2)
            count = db.execute('SELECT COUNT(*) AS n FROM virtual_account_receipts').fetchone()
            self.assertEqual(count['n'], 1)

    def test_nothing_happens_when_the_feature_is_off(self):
        from blueprints.payments_bp import _record_virtual_account_inflow
        with self.app.app_context():
            db = get_db()
            self._member(db, 'VA/17', '9900000123')
            va.set_va_enabled(db, False)
            db.commit()

            _record_virtual_account_inflow(
                db, self._payload(500000, '9900000123', 'PSTK-HOOK-3')['data'])

            count = db.execute('SELECT COUNT(*) AS n FROM virtual_account_receipts').fetchone()
            self.assertEqual(count['n'], 0)


# ── Provisioning ──────────────────────────────────────────────────────────────

class ProvisioningTests(_VaTestCase):
    def test_a_member_without_an_email_is_refused_with_a_reason(self):
        with self.app.app_context():
            db = get_db()
            db.execute("INSERT INTO members (member_number, first_name, last_name, status, "
                       "date_joined, email) VALUES ('VA/18', 'No', 'Mail', 'active', "
                       "'2024-01-01', '')")
            mid = last_insert_id(db)
            db.commit()

            ok, message = va.provision_account(db, mid)
            self.assertFalse(ok)
            self.assertIn('email', message.lower())

    def test_an_existing_account_is_never_reissued(self):
        # Reissuing would strand every standing transfer set up against the old
        # number.
        with self.app.app_context():
            db = get_db()
            mid = self._member(db, 'VA/19', '9900000555')
            ok, message = va.provision_account(db, mid)
            self.assertTrue(ok)
            self.assertIn('9900000555', message)

    def test_members_without_accounts_excludes_those_who_have_one(self):
        with self.app.app_context():
            db = get_db()
            mid = self._member(db, 'VA/20', '9900000777')
            listed = [m['id'] for m in va.members_without_accounts(db)]
            self.assertNotIn(mid, listed)

# ── The member says what their money is for ───────────────────────────────────

class MemberPreferenceTests(_VaTestCase):
    """A member's own instruction beats the cooperative's blanket rule."""

    def _ctas_cycle(self, db, member_id, contribution=5000.0, periods=2):
        from blueprints.ctas import set_ctas_enabled
        set_ctas_enabled(db, True)
        db.execute("INSERT INTO ctas_cycles (name, status, contribution_amount, "
                   "duration_months, monthly_capacity, grace_days) "
                   "VALUES ('VA test cycle', 'active', ?, ?, 1, 7)",
                   (contribution, periods))
        cid = last_insert_id(db)
        db.execute("INSERT INTO ctas_subscriptions (cycle_id, member_id, target_amount, "
                   "tenure_months, monthly_deduction, status, contributed_total, "
                   "advance_balance, outstanding) "
                   "VALUES (?, ?, ?, ?, ?, 'enrolled', 0, 0, ?)",
                   (cid, member_id, contribution * periods, periods, contribution,
                    contribution * periods))
        sid = last_insert_id(db)
        for period in range(1, periods + 1):
            db.execute("INSERT INTO ctas_schedule (subscription_id, cycle_id, period_number, "
                       "due_date, expected_amount, paid_amount, status) "
                       "VALUES (?, ?, ?, ?, ?, 0, 'due')",
                       (sid, cid, period, '2026-0%d-01' % period, contribution))
        db.commit()
        return cid, sid

    def test_member_choice_overrides_the_cooperative_rule(self):
        with self.app.app_context():
            db = get_db()
            mid = self._member(db, 'VA/21', '9900000123')
            self._loan(db, mid, principal=10000, total=11000)
            va.set_setting(db, 'va_allocation_rule', 'loan_first')   # coop says loan
            va.set_member_preference(db, mid, 'savings')             # member says savings
            db.commit()

            plan = va.build_plan(db, mid, 5000)
            self.assertEqual([p['target'] for p in plan], ['savings'])

    def test_member_can_send_money_to_their_target_advance(self):
        with self.app.app_context():
            db = get_db()
            mid = self._member(db, 'VA/22', '9900000123')
            _, sid = self._ctas_cycle(db, mid, contribution=5000, periods=2)
            va.set_member_preference(db, mid, 'ctas')
            db.commit()

            receipt_id, _ = self._inflow(db, 5000, '9900000123')
            applied, note = va.auto_allocate(db, receipt_id)
            db.commit()

            self.assertTrue(applied)
            self.assertIn('Target Advance', note)
            sub = db.execute('SELECT * FROM ctas_subscriptions WHERE id = ?', (sid,)).fetchone()
            self.assertAlmostEqual(float(sub['contributed_total']), 5000, places=2)
            row = db.execute('SELECT * FROM ctas_schedule WHERE subscription_id = ? '
                             'AND period_number = 1', (sid,)).fetchone()
            self.assertAlmostEqual(float(row['paid_amount']), 5000, places=2)

    def test_the_money_is_not_counted_twice_in_the_ledger(self):
        # It was banked on arrival, so applying it must move it out of holding —
        # not debit cash a second time.
        with self.app.app_context():
            db = get_db()
            mid = self._member(db, 'VA/23', '9900000123')
            self._ctas_cycle(db, mid, contribution=5000, periods=2)
            va.set_member_preference(db, mid, 'ctas')
            db.commit()

            cash = get_default_cash_account(db)
            cash_before = account_balance(db, cash)
            hold_before = owed(db, va.VA_UNALLOCATED)

            receipt_id, _ = self._inflow(db, 5000, '9900000123')
            va.auto_allocate(db, receipt_id)
            db.commit()

            # Cash rose once, by the amount that actually arrived.
            self.assertAlmostEqual(account_balance(db, cash), cash_before + 5000, places=2)
            # Holding is back where it started — in, and straight out again.
            self.assertAlmostEqual(owed(db, va.VA_UNALLOCATED), hold_before, places=2)

    def test_more_than_the_contribution_spills_into_savings(self):
        with self.app.app_context():
            db = get_db()
            mid = self._member(db, 'VA/24', '9900000123')
            self._ctas_cycle(db, mid, contribution=5000, periods=2)
            va.set_member_preference(db, mid, 'ctas')
            db.commit()

            receipt_id, _ = self._inflow(db, 12000, '9900000123')   # two periods + 2000
            va.auto_allocate(db, receipt_id)
            db.commit()

            member = db.execute('SELECT total_savings FROM members WHERE id = ?',
                                (mid,)).fetchone()
            self.assertAlmostEqual(float(member['total_savings']), 2000, places=2)
            receipt = db.execute('SELECT status FROM virtual_account_receipts WHERE id = ?',
                                 (receipt_id,)).fetchone()
            self.assertEqual(receipt['status'], 'allocated')

    def test_choosing_target_advance_without_a_cycle_falls_back_to_savings(self):
        # Never leave money in limbo because a member's choice no longer applies.
        with self.app.app_context():
            db = get_db()
            mid = self._member(db, 'VA/25', '9900000123')
            va.set_member_preference(db, mid, 'ctas')
            db.commit()

            receipt_id, _ = self._inflow(db, 5000, '9900000123')
            applied, _ = va.auto_allocate(db, receipt_id)
            db.commit()

            self.assertTrue(applied)
            member = db.execute('SELECT total_savings FROM members WHERE id = ?',
                                (mid,)).fetchone()
            self.assertAlmostEqual(float(member['total_savings']), 5000, places=2)

    def test_member_choice_beats_the_manual_rule_too(self):
        # Someone who has said what it is for should not wait on an officer.
        with self.app.app_context():
            db = get_db()
            mid = self._member(db, 'VA/26', '9900000123')
            va.set_setting(db, 'va_allocation_rule', 'manual')
            va.set_member_preference(db, mid, 'savings')
            db.commit()

            receipt_id, _ = self._inflow(db, 5000, '9900000123')
            applied, _ = va.auto_allocate(db, receipt_id)
            db.commit()
            self.assertTrue(applied)

    def test_an_unknown_choice_is_rejected(self):
        with self.app.app_context():
            db = get_db()
            mid = self._member(db, 'VA/27', '9900000123')
            self.assertFalse(va.set_member_preference(db, mid, 'crypto'))
            self.assertEqual(va.member_preference(db, mid), '')

    def test_blank_choice_returns_to_the_cooperative_rule(self):
        with self.app.app_context():
            db = get_db()
            mid = self._member(db, 'VA/28', '9900000123')
            self._loan(db, mid, principal=10000, total=11000)
            va.set_setting(db, 'va_allocation_rule', 'loan_first')
            va.set_member_preference(db, mid, 'savings')
            db.commit()
            self.assertEqual([p['target'] for p in va.build_plan(db, mid, 5000)], ['savings'])

            va.set_member_preference(db, mid, '')
            db.commit()
            # The loan owes 11,000, so all 5,000 goes to it and nothing spills.
            self.assertEqual([p['target'] for p in va.build_plan(db, mid, 5000)], ['loan'])
            # Send more than the loan owes and the difference is saved.
            self.assertEqual([p['target'] for p in va.build_plan(db, mid, 15000)],
                             ['loan', 'savings'])
# ── The mobile app ────────────────────────────────────────────────────────────

class MobilePayInTests(_VaTestCase):
    """The member's account number and payment choice, as the app sees them."""

    def setUp(self):
        super().setUp()
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db = get_db()
            db.execute("DELETE FROM users WHERE username LIKE 'va-mobile%'")
            db.commit()
        super().tearDown()

    def _member_with_login(self, db, number='VA/90', account_number='9900000900'):
        mid = self._member(db, number, account_number)
        email = f"{number.replace('/', '-').lower()}@example.test"
        from werkzeug.security import generate_password_hash
        db.execute("INSERT INTO users (username, email, password_hash, role, is_active) "
                   "VALUES (?, ?, ?, 'member', 1)",
                   (f'va-mobile-{number}', email, generate_password_hash('MemberPass123')))
        db.commit()
        return mid, f'va-mobile-{number}'

    def _token(self, username):
        from utils import clear_login_attempts
        clear_login_attempts('mobile:127.0.0.1')
        response = self.client.post('/api/mobile/login',
                                    json={'username': username, 'password': 'MemberPass123'})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()['token']

    def _auth(self, token):
        return {'Authorization': f'Bearer {token}'}

    def test_the_app_is_told_the_members_account_number(self):
        with self.app.app_context():
            db = get_db()
            self._member_with_login(db)
        token = self._token('va-mobile-VA/90')

        payload = self.client.get('/api/mobile/v1/pay-in', headers=self._auth(token)).get_json()
        self.assertTrue(payload['enabled'])
        self.assertEqual(payload['account']['account_number'], '9900000900')
        self.assertEqual(payload['account']['bank_name'], 'Wema Bank')

    def test_the_section_is_hidden_when_the_coop_does_not_issue_numbers(self):
        # enabled:false rather than an error, so the app just omits the card.
        with self.app.app_context():
            db = get_db()
            self._member_with_login(db)
            va.set_va_enabled(db, False)
            db.commit()
        token = self._token('va-mobile-VA/90')

        payload = self.client.get('/api/mobile/v1/pay-in', headers=self._auth(token)).get_json()
        self.assertTrue(payload['success'])
        self.assertFalse(payload['enabled'])
        self.assertIsNone(payload['account'])

    def test_the_member_can_set_their_choice_from_the_app(self):
        with self.app.app_context():
            db = get_db()
            mid, _ = self._member_with_login(db)
        token = self._token('va-mobile-VA/90')

        response = self.client.patch('/api/mobile/v1/pay-in', json={'preference': 'loan'},
                                     headers=self._auth(token))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['preference'], 'loan')

        with self.app.app_context():
            self.assertEqual(va.member_preference(get_db(), mid), 'loan')

    def test_target_advance_is_not_offered_to_a_member_who_is_not_on_a_cycle(self):
        with self.app.app_context():
            db = get_db()
            self._member_with_login(db)
        token = self._token('va-mobile-VA/90')

        payload = self.client.get('/api/mobile/v1/pay-in', headers=self._auth(token)).get_json()
        self.assertNotIn('ctas', [c['key'] for c in payload['choices']])

        refused = self.client.patch('/api/mobile/v1/pay-in', json={'preference': 'ctas'},
                                    headers=self._auth(token))
        self.assertEqual(refused.status_code, 400)

    def test_an_unknown_choice_is_refused(self):
        with self.app.app_context():
            db = get_db()
            self._member_with_login(db)
        token = self._token('va-mobile-VA/90')

        response = self.client.patch('/api/mobile/v1/pay-in', json={'preference': 'crypto'},
                                     headers=self._auth(token))
        self.assertEqual(response.status_code, 400)

    def test_it_needs_a_token(self):
        self.assertIn(self.client.get('/api/mobile/v1/pay-in').status_code, (401, 403))

if __name__ == '__main__':
    unittest.main()
