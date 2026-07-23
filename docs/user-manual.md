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
