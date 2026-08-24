# HQ Billing — invoicing client cooperatives

Operator-side billing. Available only on the HQ instance (`MARKETING_HQ=1`),
where a **Billing** item appears in the menu for administrators.

HQ has its own database, separate from every tenant, so client details live here
rather than being read out of each cooperative.

---

## Clients

**Billing → Clients** is your registry of the cooperatives you bill. Each carries:

- name and **code** — the code is the client's subdomain (`ooucoop`), and is how
  HQ reaches that instance for the actions below
- billing email, phone
- **active users** and **rate per user**, which drive the subscription invoice
- billing cycle and period
- `billed_user_count` — how many users have already been invoiced this period, so
  a top-up bills only the newcomers

**Sync counts** pulls each active client's live active-member count from its own
app, so you are not maintaining the numbers by hand. It needs `HQ_SYNC_TOKEN` set
identically on HQ and every tenant (see `deploy/vps/COMMANDS.txt`), and each
client's code set correctly.

---

## Invoices

An invoice is built from line items:

- **Subscription** — users × rate
- **Top-up** — when members join mid-period, bills only the difference between
  the current and already-billed user counts
- **Service fees** — support, migration, customization, training or other

**Invoice all active clients** generates a full subscription invoice for everyone
in one action.

Each invoice can be:

- **emailed** to the client, branded with your business name and logo, carrying
  the purpose and your notes, a **Pay now** link and the invoice PDF
- **duplicated** into a fresh draft for the next period
- **edited** while it is draft *or* sent — real invoices get disputed, so a sent
  invoice can be corrected and resent. Paid and void invoices are locked.
- **deleted**, which also releases its members back to the client's billed count

### Branding

**Billing → gear icon** sets the business name and the default payment
instructions that appear on every invoice and email. The logo comes from
**Settings → your cooperative logo** on the HQ instance. Set these before sending
anything, or invoices go out titled with the instance name and no logo.

---

## Collecting payment

Two routes, both supported:

- **Online** — the emailed invoice carries a Paystack *Pay now* link. On success
  the callback verifies the payment and marks the invoice paid automatically.
- **Manual** — record a bank transfer with its reference using *Mark paid*.

Paystack keys are set on the HQ instance under **Settings → Online Payment
Gateway**. Test keys are fine to start; the flow is identical.

---

## Suspending and restoring access

A client's whole system can be locked from **Clients**:

- **Suspend** — everyone at that cooperative, administrators included, sees a
  "contact your provider" notice. **Their data is untouched.**
- **Reactivate** — restores access immediately.

Marking an invoice paid **reactivates a suspended client automatically**. If the
tenant cannot be reached at that moment the payment still records, and you can
reactivate from the Clients page.

Both actions call the tenant's token-guarded endpoint, so HQ needs
`HQ_SYNC_TOKEN` deployed as above.

---

## Optional modules

The **CTAS on / off** button beside a client switches the Target Advance module
on for that cooperative — no commands. See `docs/ctas.md`.

---

## Testing

```bash
python -m unittest tests.test_hq_billing
```
