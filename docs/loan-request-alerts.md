# Loan Request Alerts

## The problem

A member submitted a loan request through the app. Nobody on the management
committee knew until the member telephoned the office. The application was
sitting in the database, correctly recorded and completely invisible — which
defeats the point of automating the process.

Two things caused it:

1. **The alert almost never fired.** All three application paths (admin,
   member portal, mobile app) only notified anybody when the loan skipped the
   guarantor stage — that is, only when `guarantors_required` was `0`. The
   bye-laws require 2 guarantors, so in practice the branch never ran.
2. **When it did fire it reached one office, in one channel.** It sent an
   in-app notification to users with the `secretary` role. No email, no push,
   no President, no Treasurer, no exco, and nothing attached.

## How a loan request is handled now

A loan request is treated the way an e-commerce platform treats an order: it is
logged, everyone who can act on it is told at once, the customer gets a
receipt, and if nobody acts the system keeps chasing.

### 1. Instant fan-out on submission

The moment a request is created — from the admin screen, the member portal or
the mobile app — `loan_alerts.notify_loan_submitted()` runs and alerts **every
officer**, whatever stage the loan starts in:

| Office | Role in the system |
|---|---|
| President | `admin` |
| Treasurer | `treasurer` |
| General Secretary | `secretary` |
| Exco members | `exco` |
| Anyone else you name | `loan_alert_extra_emails` setting |

Each of them receives:

* an **in-app notification** (and a **push notification** on the mobile app,
  through the existing `notify()` → Expo path);
* an **email** carrying a summary table (applicant, amount, purpose, tenure,
  monthly repayment, total repayable, savings balance, stage) and a link
  straight to the application;
* the **full application as a PDF attachment**.

The applicant also gets a receipt in-app and by email, so no one has to phone
in to find out whether their request arrived.

### 2. The PDF

`loan_pdf.build_loan_application_pdf(db, loan_id)` renders everything an
officer needs to decide without logging in:

1. applicant identity, membership date, savings balance and eligibility ceiling
2. facility requested — amount, purpose, tenure, rate, method, monthly and
   total repayment, repayment collateral
3. declaration and consents — terms, data processing, credit or HR
   affordability, typed signature, signing time, submitting IP and channel
4. pre-disbursement due-diligence status
5. guarantors and their consent status
6. the exact repayment schedule the member accepted
7. the approval trail so far

It is available on demand too:

* officers: `GET /loans/<id>/application.pdf`
* members (own loans only): `GET /loan-detail/<id>/application.pdf`
* mobile app (own loans only): `GET /api/mobile/v1/loans/<id>/application.pdf`

### 3. Everything is logged

Every alert, reminder, escalation, decision and first view is written to
`loan_request_events` with recipient, role, channel (`inapp` / `email` /
`push` / `system`) and outcome (`sent` / `queued` / `skipped` / `failed`). The
loan detail page shows this as a **Notification Log**, so the cooperative can
always answer "who was told, how, and when?" — and prove it.

The loan row itself carries the counters: `submission_channel`,
`stage_entered_at`, `alert_count`, `first_alert_at`, `last_alert_at`,
`last_reminder_at`, `escalated_at`, `first_response_at`.

### 4. Handover between offices

When a stage is approved, `notify_stage_advanced()` alerts the officer who owns
the **next** stage — again with the PDF attached — and resets the stage clock.
If nobody currently holds that role, the President is alerted instead so the
request cannot stall on an empty office.

### 5. Nothing is allowed to go quiet

`loan_alerts.run_pipeline_sweep(db)` walks every pending request:

* past the **response standard** (`loan_alert_sla_hours`, default 24h) →
  reminder to the officer who owns the stage;
* past the **escalation window** (`loan_alert_escalate_hours`, default 48h) →
  the President and the whole exco are alerted;
* stuck on **guarantor consent** → the silent guarantors are chased and the
  Secretary is told the request is blocked outside the committee's hands.

`loan_alert_reminder_hours` (default 12h) throttles repeat chasing, so the
sweep is safe to run as often as you like.

Run it either way:

```bash
# from a scheduler (cron, Render/Railway job, uptime pinger) — every 30-60 min
curl -fsS -X POST https://your-coop.example.org/tasks/loans/pipeline-sweep \
     -H "X-Task-Token: $TASK_RUNNER_TOKEN"
```

or by pressing **Chase pending requests** on the Loans page, which any admin,
treasurer or secretary can do. Set `TASK_RUNNER_TOKEN` in the environment to
enable the scheduled route.

### 6. The queue is visible

The Loans page shows how many requests are awaiting the committee, how many
have passed the response standard, how long the oldest has waited, and how many
have never been alerted at all.

## Settings

All under **Settings → Loan Settings → Loan Request Alerts**:

| Key | Default | Meaning |
|---|---|---|
| `loan_alert_enabled` | `1` | Master switch for automatic alerts |
| `loan_alert_attach_pdf` | `1` | Attach the application PDF to alert emails |
| `loan_alert_roles` | `admin,treasurer,secretary,exco` | Which offices are alerted |
| `loan_alert_extra_emails` | — | Extra addresses copied on every alert |
| `loan_alert_sla_hours` | `24` | Response standard per stage |
| `loan_alert_reminder_hours` | `12` | Minimum gap between reminders |
| `loan_alert_escalate_hours` | `48` | Age at which the exco is pulled in |
| `app_base_url` | — | Public URL, used for links in emails sent by the sweep |

## Requirements for delivery

Emails only go out when email is configured and enabled (`mail_enabled` plus a
Resend, Brevo or SMTP back-end — see **Settings → Email**). Alerts to officers
without an email address on their user record still land in-app and as push
notifications, and the notification log records the email as `skipped` so the
gap is visible. **Give every officer an email address on their user account.**

Push notifications require the officer to have signed in on the mobile app at
least once (that registers the device token).

## Not covered

Applications a member starts and abandons before submitting are not tracked —
the system only sees a request once it is submitted. Capturing drafts (the
"abandoned cart" equivalent) would need the application form to save partial
state, and is a separate change.
