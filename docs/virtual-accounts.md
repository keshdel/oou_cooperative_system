# Member Account Numbers (Dedicated Virtual Accounts)

A permanent NUBAN per member, issued through Paystack. Money transferred to it is
identified by the account it landed in, so nothing is matched by hand.

Off by default (`settings.va_enabled`). No menu entry, no ledger account, and no
webhook handling until a cooperative switches it on.

---

## Why it is built in two steps

Every other payment path in CoopMS starts with a member clicking something: a
`pending_payments` row is written first, the gateway is called, and the reference
ties the answer back to the intention. A bank transfer has none of that. It
arrives unannounced, with no reference we chose and no statement of purpose.

So an inflow is **banked** and then **applied**:

| Step | Posting | Meaning |
|---|---|---|
| Banked, on arrival | Dr Cash and Bank, Cr `2010` | The cooperative holds the member's money |
| Applied, as a decision | Dr `2010`, Cr savings / loan / CTAS | What the money was for |

The first step cannot wait for a human. The cooperative owes the member that
money from the second it lands, and the books must say so even though nobody has
decided what it is for yet. The second step is a judgement, made by rule or by an
officer, and is separately reversible.

Enabling seeds one account:

| Code | Account | Type |
|---|---|---|
| `2010` | Unallocated Member Receipts | liability |

---

## Tables

| Table | Holds |
|---|---|
| `member_virtual_accounts` | one NUBAN per member per provider |
| `virtual_account_receipts` | every inflow, with allocation state |
| `virtual_account_allocations` | what each receipt was applied to |

`members.payment_preference` holds the member's own instruction.

Idempotency is a unique index on `(provider, provider_reference)`. A webhook
redelivery finds the existing receipt, returns `is_new = False`, and the caller
rolls back without touching anything.

---

## Receiving money

Paystack sends `charge.success` with `data.channel == 'dedicated_nuban'` to the
existing `/webhooks/paystack` endpoint — no new URL to register.
`_record_virtual_account_inflow` in `blueprints/payments_bp.py` matches the
member on `metadata.receiver_account_number`, falling back to
`authorization.receiver_bank_account_number` and then the customer code.

The handler never raises. A webhook that 500s is retried, and a retry that fails
the same way just repeats, so anything unexpected is logged and the money is left
for an officer.

An inflow that matches no member is stored `unmatched` — still banked, never
guessed at.

---

## Deciding what it is for

`build_plan(db, member_id, amount)` resolves in this order:

1. **`members.payment_preference`** — `savings`, `loan`, or `ctas`. Wins over
   everything, including the `manual` rule: a member who has said what the money
   is for should not queue for an officer to decide it again.
2. **`settings.va_allocation_rule`** — `savings` (default), `loan_first`, or
   `manual`.
3. **Remainder to savings.** Every path ends this way, so money is never left
   sitting in `2010` because a named target was already satisfied.

A preference that no longer applies (CTAS chosen, cycle over) yields no legs and
falls through to savings.

`apply_plan` refuses a plan totalling more than the receipt's unapplied balance.
Without that, `2010` could go negative — the books claiming money the cooperative
never received.

---

## Applying to CTAS

`_apply_to_ctas` calls `blueprints.ctas._post_contribution` with
`debit_account=VA_UNALLOCATED`. It does **not** reimplement CTAS's rules about
funding the pool versus repaying an advance, or ticking the schedule — that
module owns them.

The only difference from a normal contribution is where the money comes from: it
is already banked and sitting in `2010`, so debiting cash again would count it
twice.

`_post_contribution` can post less than asked (a subscription in recovery cannot
repay more than it owes). The difference is saved rather than left in holding,
and the allocation row is trimmed to what was actually posted.

---

## Reversal

| `source_module` | Reversible from the journal? |
|---|---|
| `va_receipt` | **No.** The money genuinely arrived; only the decision can be undone. |
| `va_savings` | Yes — undoes the savings row and returns the amount to unallocated. |
| `va_loan` | Yes — restores the loan balance and returns the amount to unallocated. |
| `ctas_contribution` | No — corrected from the CTAS cycle page, per that module. |

`_unapply_receipt` in `ledger.py` decrements `allocated_amount` and moves the
receipt back to `unallocated` or `part_allocated`, so the money can be applied
somewhere else.

---

## Gotchas

- **`journal_entries.reference` is UNIQUE and `post_journal_safe` swallows the
  collision.** Receipt and repayment numbers are derived from row ids
  (`VA/{savings.id}`, `VAR/{repayments.id}`), never random digits. CTAS legs pass
  `ref_suffix=f'-VA{alloc_id}'` so a period paid twice does not collide.
- **`account_balance()` returns debits minus credits**, so `2010` reads negative.
  Flip the sign before showing it to anyone.
- **Numbers are never reissued** to a member who already has one — that would
  strand every standing transfer set up against the old number.
- **The queue and the ledger are shown side by side** on the admin page
  precisely so a divergence between `pending_total()` and the `2010` balance is
  visible rather than buried.

---

## Not built

Discussed and deliberately not implemented: NIBSS direct debit (recurring pull
from the member's bank account), USSD charges, and cash-agent collection. Card on
file already exists for CTAS via `charge_authorization`.
