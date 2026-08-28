# CoopMS User Manual

Last updated: 2026-07-23

CoopMS is a cooperative enterprise management system for member records, savings, loans, accounting, reporting, member self-service, and audited communications.

## Core Navigation

- Dashboard: high-level cooperative activity and financial position.
- Members: member register, member profiles, savings statements, ID cards, and member lifecycle.
- Savings: contribution records, salary/bulk uploads, savings requests, and member savings history.
- Loans: applications, approvals, due diligence, disbursements, repayments, and loan book monitoring.
- Accounting: chart of accounts, journals, trial balance, bank accounts, reconciliation, dividends, and period close.
- Reports: financial statements, cashbook, GL register, member savings control, and loan portfolio reports.
- Communications: branded email notices for profile updates, savings reminders, loan repayment reminders, balance notices, and general cooperative messages.
- Settings: cooperative identity, mail, users, password policy, loans, savings, payments, support contact, and system readiness.
- Data Migration: import members, historical savings, loans, repayments, investments, revenue, and expenses.

## Member Onboarding

1. Add or import members.
2. Confirm each member has a valid email address.
3. Send setup links individually or use bulk setup links for members who have not completed setup.
4. Members configure password and profile from the setup email.
5. Profile readiness reaches 100 percent when required personal, contact, bank, emergency, and nominee fields are complete.
6. A certified member badge shows when the profile is complete.

## Member Self-Service

Members can log in to:

- View savings balance and full savings history.
- View loans, repayment schedules, and repayment status.
- Generate member statements with opening and closing balances.
- Apply for loans after accepting terms, data processing consent, and repayment schedule.
- Update profile, nominee, contact, and bank details.
- Submit savings change requests.
- Transfer to their own personal account number, credited automatically.
- Choose what their transfers pay for: savings, loan repayment, or Target Advance.
- Switch off text messages while keeping in-app alerts.
- Contact support.

Staff users who also have a member profile can switch into member view and return to admin view from the account menu.

## Savings Operations

- Manual savings are recorded from a member profile.
- Bulk savings can be imported through Data Migration or salary/bulk upload workflows.
- Savings postings update the member subledger and the general ledger.
- The default cash/bank account controls where new savings cash is posted.
- Historical savings posted to a generic Cash and Bank GL can be reclassified to the correct detail bank account without changing member balances.

Important checks:

- Member savings control should agree with the Member Deposits GL control account.
- Bank account detail balances should agree with the cooperative bank statement for the same period.
- Use reversal workflows for corrections rather than deleting posted records.

Correcting a whole upload:

1. Fix the cause first — for example correct the share capital percentage in Settings.
2. Open the batch and use "Reverse this upload", giving a reason. The reason is written to the audit log.
3. Re-upload the corrected file under a new batch reference.
4. Check one member's savings statement to confirm the figures now read correctly.

Reversal cancels each row with an opposite entry rather than deleting it, so member balances and share capital are restored and the ledger stays balanced. Reversed rows are hidden from the Savings Records list, and running the reversal twice is safe.

## Member Account Numbers

Each member can be given a permanent bank account number in their own name (a
dedicated virtual account, issued through Paystack). Money transferred to it is
identified by the account it landed in, so nothing is matched by hand and the
member never has to quote a reference or send proof of payment.

Off by default. Turn it on under Account Numbers, then Settings.

### How an inflow is handled

Money arrives unannounced — no pending record is waiting for it and nothing says
what it is for. So it is handled in two steps:

1. **Banked.** Debit Cash and Bank, credit Unallocated Member Receipts (2010).
   The cooperative owes the member that money from the moment it lands, and the
   books say so immediately.
2. **Applied.** Debit 2010, credit savings, the member's loan, or their Target
   Advance. This is a separate decision and is separately reversible.

Account 2010 is seeded only when the feature is switched on, so a cooperative
that never uses it keeps a clean chart of accounts.

### What a payment is for

Resolution order:

1. **The member's own choice**, set on their My Savings page — savings, loan
   repayment, or Target Advance contribution. This wins, including over the
   "hold it for an officer" rule: someone who has already said what the money is
   for should not queue for a person to decide it again.
2. **The cooperative's fallback rule**, set in Settings — all to savings, clear
   loans first, or hold for an officer.
3. **Anything left over becomes savings**, so a member who sends more than their
   named target needs is saving the difference rather than leaving money in the
   holding account.

A member choice that stops applying — Target Advance selected, then the cycle
ends — falls back to savings rather than stranding the money.

### Two deliberate refusals

- **A member is never guessed.** An inflow that cannot be tied to a member is
  held as `unmatched`, still banked, for an officer to identify.
- **The arrival is never reversed.** The money genuinely landed in the bank, so
  only the decision about it can be undone. Reversing an allocation returns the
  amount to unallocated, ready to be applied elsewhere.

Applying more than arrived is refused outright, which keeps the holding account
from going negative.

### Setup

1. Request Dedicated Virtual Accounts approval in the cooperative's Paystack
   dashboard. This takes a few days and gates everything else.
2. Ensure every active member has an email address — the bank requires one.
3. Confirm the Paystack webhook points at `<site>/webhooks/paystack`.
4. Account Numbers → Settings → status On, choose the fallback rule, save.
5. Create the account numbers for all active members.
6. Test with a small real transfer before telling members.
7. Tell members: their number is on My Savings, where they also choose what
   their transfers pay for.

Account numbers are never reissued to a member who already has one — that would
strand every standing transfer set up against the old number.

### Daily operation

Open Account Numbers and check:

- **Unidentified transfers** — click Identify and choose the member.
- **Waiting to be applied** — apply by rule, or split by hand across Target
  Advance, a loan and savings.
- **The queue against the holding account** — they are two views of the same
  money and should always agree. The page shows a tick when they do.

The menu badge counts payments needing a person; no badge means nothing to do.

See `docs/virtual-accounts.md` for the full technical reference.

### Corrections

- Savings and loan allocations reverse from the journal. The amount returns to
  waiting and can be applied elsewhere.
- Target Advance allocations are corrected from the Target Advance cycle page,
  because that module owns its own contribution schedule.
- Nothing is ever deleted. Both the original and its reversal stay on record.

## Text Messages (SMS)

Members with the mobile app already receive free push notifications. SMS closes
the gap for members without it — and only for them, so the cooperative pays for
far fewer messages than a blanket send.

Credentials are per cooperative: each society opens and funds its own provider
account (Termii or Africa's Talking) and is billed directly. The API key sits in
that tenant's own settings alongside its payment gateway keys and is never
shared.

### Setup

1. Open a provider account in the cooperative's name and fund it. SMS is
   prepaid.
2. Register a sender ID — up to 11 characters, approved by the provider, usually
   within a day or two.
3. Settings → SMS: enable, choose the provider, paste the API key, enter the
   sender ID. Country code defaults to 234.
4. Send a test message to a known phone before enabling it for members.

Leaving the API key field blank on a later save keeps the stored key.

### Operational notes

- Sending never interrupts the operation that triggered it. A failed message is
  logged, not raised, so a savings posting or loan approval always completes.
- Every attempt — sent, failed, or skipped — is recorded with its reason, so a
  cooperative can see what its credit was spent on.
- A member can decline SMS from their own profile; in-app and push alerts
  continue.
- Messages over 160 characters are billed as more than one.

## Loan Operations

Loan application flow:

1. Member or staff selects loan type, amount, and tenure.
2. CoopMS calculates repayment schedule from configured loan settings.
3. Applicant accepts the repayment schedule.
4. Applicant accepts terms and data-processing consent.
5. Loan enters review workflow.

Non-staff cooperative members may require:

- Credit check consent and review.
- Bank statement request/review.
- Post-dated cheques, standing order, or other payment collateral.

Staff cooperative members can use HR affordability confirmation where repayments are salary-deducted. Bank statement and credit check may be marked not required according to policy.

Repayments:

- Manual and bulk repayments update both loan balance and GL.
- Repayment emails can notify members of amount paid and remaining balance when outgoing email is configured.
- Reversals update the loan subledger and GL together.

## Target Advance (CTAS)

An optional module. It stays hidden until the cooperative is switched on, from HQ
under Billing → Clients → CTAS on. See `docs/ctas.md` for the full reference.

What it is:

- Members contribute a fixed amount every period; a ballot decides who receives the target amount in which period.
- A member keeps contributing after they have collected, until the cycle ends.
- Their own contributions fund part of their payout; the cooperative advances the rest and recovers it from their remaining contributions.
- If a cycle is not fully subscribed, the cooperative bridges the gap. This changes what the cooperative must fund, never the member's target.

Running a cycle:

1. Define a plan (contribution × periods = target), then create a cycle from it with real dates.
2. Record the CTAS reserve and the approved cooperative support on the Setup tab.
3. Open enrolment; members apply themselves or are added by an officer.
4. Approve each application through eligibility, finance review and committee approval, then enrol. Members at the same gate can be advanced together.
5. Close enrolment and run the ballot. A projected shortfall blocks this until support is approved or an officer accepts the gap.
6. Each period, export what is due, collect, and import the confirmed amounts.
7. Pay each member on their balloted position.

Important checks:

- A member cannot be enrolled until they have accepted the scheme terms, which include credit checks and personal-data processing. Paper acceptance can be recorded by an officer and is audited.
- The approval gates are separate duties and can be split between officers under Settings → Task Assignment.
- Priority positions are charged only if the position is actually allocated, so no refunds arise.
- The pool, advances and fee income are ordinary ledger accounts — reconcile them like any other.

## Accounting

Chart of accounts:

- Create detail accounts where needed, especially for bank accounts under Cash and Bank.
- Set the default cash/bank account for savings deposits.
- Deactivate old accounts instead of deleting accounts with transaction history.

Journals:

- Debit and credit sides are labelled on the journal entry screen.
- Journal entries must balance before posting.
- Use journal quick view or journal detail to inspect source, reference, debit lines, and credit lines.
- Reversals are used to void posted entries while preserving audit trail.

Period close:

- Set a lock date after monthly review.
- Closed periods should not receive new backdated postings.
- Correct closed periods using controlled reversal and adjustment entries.

## Dividends and Patronage

The Dividends page is used after period review to appropriate net surplus and credit members.

Workflow:

1. Select the surplus period using From and To dates.
2. Enter the split for dividend, reserve, honorarium, and other allocation.
3. Enter patronage split percentage if part of the dividend pool should be allocated by loan-interest patronage.
4. Click Compute.
5. Review surplus appropriation and member dividend schedule.
6. Admin clicks Declare and credit members when approved.
7. Open the declaration detail to export PDF or Excel for committee records and audit files.

Controls:

- Declare dividends only after financial statements, trial balance, and member savings control have been reviewed.
- A declaration posts to member savings and the ledger.
- Keep board or committee approval outside the system before posting.
- Correct mistakes through controlled accounting adjustment rather than editing declaration history.

## Financial Reporting

Reports include:

- Financial Statements: income statement, balance sheet, cash flow, and surplus appropriation.
- Trial Balance: debit and credit balances by account.
- General Ledger Register: exportable journal-line report for external analysis.
- Cashbook: cash/bank movements and running balance.
- Member Savings Control: member-level savings reconciliation.
- Loan Portfolio and Aging: outstanding loans, due dates, repayments, and aging.

Recommended monthly review:

1. Review bank account positions.
2. Reconcile cashbook to bank statement.
3. Check member savings control against Member Deposits GL.
4. Review loan portfolio and aging.
5. Review trial balance and financial statements.
6. Set period lock date after review.

## Communications

The Communications Center sends branded CoopMS emails and logs each delivery attempt.

Available presets:

- Profile update reminder.
- Monthly savings reminder.
- Loan repayment reminder.
- Balance and statement notice.
- General cooperative notice.

Useful merge tags:

- `{first_name}`
- `{last_name}`
- `{full_name}`
- `{member_number}`
- `{savings_balance}`
- `{monthly_savings}`
- `{savings_due_day}`
- `{loan_balance}`
- `{loan_monthly_payment}`
- `{loan_next_payment_date}`
- `{profile_completion}`
- `{portal_link}`

Operational guidance:

- Send to a selected test member before large campaigns.
- Skipped recipients usually have no email address.
- Text messages reach members without the mobile app; see Text Messages (SMS) above.
- WhatsApp should only be enabled after member consent and approved WhatsApp template setup.
- Delivery logs are part of the audit record.

## Email Setup

Outgoing email can use SMTP or a provider API, depending on configuration.

For interim sending without a domain, SMTP through Brevo or Gmail app password is usually easiest. Resend generally requires verified domain setup before production sending.

After changing mail settings:

1. Save settings.
2. Send a test email.
3. Confirm delivery.
4. Send a small member campaign before a full broadcast.

## Data Security and Stability

- Production uses PostgreSQL.
- VPS deployment runs through Docker and Caddy reverse proxy.
- HTTPS is handled at the proxy layer.
- Schema startup is serialized for PostgreSQL so multiple app workers do not race during initialization.
- Sensitive operational changes should be committed and deployed through Git.
- Database backups should be scheduled outside the app and tested by restore.

## Deployment Summary

Typical VPS deployment:

```bash
cd ~/oou_cooperative_system
git pull origin main
cd deploy/vps
docker compose up -d --build
docker compose logs -f app-ooucoop
```

Healthy startup shows Gunicorn listening on port 8000 and PostgreSQL initialization completing without worker crashes.
