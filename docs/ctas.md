# CTAS — Cooperative Target Advance Scheme

A rotating contribution scheme (*ajo* / *esusu*) run under cooperative governance.
Members contribute a fixed amount every period; a ballot decides who collects the
target amount in which period; the cooperative underwrites any gap.

CTAS is an **optional module**. It is off by default and leaves no trace — no menu,
no routes, no ledger accounts — until a cooperative is switched on.

---

## Enabling it for a cooperative

From HQ: **Billing → Clients → CTAS on** next to the client. That calls the
client's `POST /api/hq/set-feature` (guarded by the shared `HQ_SYNC_TOKEN`), sets
`settings.ctas_enabled` and seeds the ledger accounts.

A super admin on the tenant itself can also use `POST /ctas/enable`.

Switching it on seeds four accounts:

| Code | Account | Type |
|---|---|---|
| `1150` | CTAS Advances Receivable | asset |
| `2050` | CTAS Contribution Pool | liability |
| `4150` | CTAS Admin Fee Income | income |
| `4160` | CTAS Priority Fee Income | income |
| `5150` | CTAS Write-offs | expense |

---

## How the money works

Every subscribed member contributes the fixed amount **every period**, including
after they have been paid.

**Contribution, before that member's payout**

```
DR Cash                        CR CTAS Contribution Pool (2050)
```

**Payout** — the member's own pooled contributions cover part of it; the
cooperative advances the rest.

```
DR CTAS Contribution Pool   (what they have contributed)
DR CTAS Advances Receivable (the balance the cooperative fronts)
    CR Cash                 (target less fees)
    CR CTAS Admin Fee Income
    CR CTAS Priority Fee Income   (only if a priority position was allocated)
```

**Contribution, after their payout** — now repaying the advance.

```
DR Cash                        CR CTAS Advances Receivable (1150)
```

At the end of a healthy cycle both the pool and advances return to zero.

### The cooperative guarantee

In a full cycle, contributions in each period exactly equal one payout. In an
under-subscribed cycle they do not, and the cooperative bridges the difference:

```
8 members × ₦50,000  =  ₦400,000 collected
payout                =  ₦600,000
gap the cooperative funds = ₦200,000 per payout period
```

Under-subscription changes what the cooperative must fund — never the member's
target.

---

## Cycle lifecycle

```
draft → open → closed → ready_for_ballot → balloted → active → completed
```

`closed → ready_for_ballot` is gated on liquidity: a projected shortfall blocks
the transition unless an officer ticks *proceed anyway*, which is audited as
`CTAS_LIQUIDITY_OVERRIDE`.

## Subscription lifecycle

```
submitted → eligible → finance_reviewed → approved → enrolled
          → scheduled → active_recovery → completed
```

Each approval gate has its own duty so cooperatives can separate them:

| Gate | Duty | Default offices |
|---|---|---|
| Confirm eligibility | `ctas.eligibility` | President, Secretary |
| Finance review | `ctas.finance` | President, Treasurer |
| Committee approval | `ctas.approve` | President |
| Enrol, and everything else | `ctas.manage` | President, Treasurer |

All are assignable in **Settings → Task Assignment**. Give one person all three
and the chain behaves as a single approval; split them for four-eyes control.

A member **cannot be enrolled until they have accepted the terms**, which cover
the contribution obligation, recovery method, exit net-off, credit and
affordability checks, and personal-data processing. Paper acceptance can be
recorded by an officer via `POST /ctas/subscriptions/<id>/terms`; the attestation
is audited.

---

## Collecting contributions

**By file** — export the period's schedule, collect, then import the confirmed
amounts. Enrolled, scheduled and in-recovery members all appear, because in the
pool everyone contributes from period one. Imports are idempotent per
subscription and period.

**By saved card** — a member authorises once by paying a contribution; the
provider's reusable token is stored (encrypted at rest — the card number never
reaches this system). A scheduler then charges what is due:

```bash
curl -fsS -X POST https://<client>.cooperativems.com/tasks/ctas/charge-due \
     -H "X-Task-Token: $TASK_RUNNER_TOKEN"
```

Run it daily. A declined card backs off for `retry_days` and, after
`max_charge_attempts`, the mandate is suspended and an exception raised. Expired
cards are detected before charging.

---

## Exceptions

- **Missed or short contribution** → arrears increase, a case is raised, the
  member is notified. An over-payment reduces arrears.
- **Member exit** → `Exit / settle` recovers the outstanding advance in order:
  savings → share capital → other recoveries entered by the officer → write-off.
  Posts to the ledger and closes the subscription.

---

## Screens

| Route | Purpose |
|---|---|
| `/ctas/overview` | Management dashboard: profitability, risk, liquidity |
| `/ctas` | Cycles list |
| `/ctas/cycles/<id>` | One cycle — tabs: Members, Contributions, Payouts, Setup |
| `/ctas/plans` | Reusable product definitions |
| `/ctas/exceptions` | Arrears and exit cases |
| `/my-ctas` | Member: apply, track, manage automatic payment |

The cycle page carries the active tab in `?tab=`, and every action returns to its
own tab.

---

## Not modelled yet

Deliberately absent, and reported as *not tracked* rather than as zero:

- Secured exposure — security deposits, pledged savings, guarantors
- Minimum initial contribution before ballot eligibility
- Risk scoring and a risk-based ballot
- CTAS risk reserve fund
- Late-payment, enrolment and cancellation fees
- Investment income on the CTAS float, and bad-debt provisioning

---

## Testing

```bash
python -m unittest tests.test_ctas
```
