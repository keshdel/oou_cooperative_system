# Railway onboarding runbook — one instance + one database per client

Each client gets its **own Railway service + own Postgres database**. Full data
isolation. All clients run the same `main` branch, so pushing an update
redeploys every client.

Do the whole thing once per client. It takes ~10 minutes.

---

## 0. One-time, before the first client

Generate unique secrets for both clients (local machine):

```bash
python deploy/gen_secrets.py client1 client2
```

This writes `deploy/secrets/client1.env` and `client2.env` (gitignored). Each
holds a unique `SECRET_KEY`, `FIELD_ENCRYPTION_KEY`, and admin/role passwords.
You'll copy these into Railway below, then move the files into your password
manager and delete them from disk.

---

## 1. Create the service (per client)

1. Railway dashboard → **New Project** (one project per client keeps billing and
   dashboards cleanly separated). Name it e.g. `coop-client1`.
2. **Deploy from GitHub repo** → `keshdel/oou_cooperative_system` → branch `main`.
3. Railway auto-detects Python (via `runtime.txt` + `requirements.txt`) and starts
   it with the `Procfile` (`gunicorn app:app`). No build config needed.

## 2. Add the database (per client)

4. Inside the project: **+ New → Database → PostgreSQL**.
5. Railway injects `DATABASE_URL` into the service automatically. **Do not set it
   by hand.** The app converts `postgres://` → `postgresql://` itself.

## 3. Set the variables (per client)

6. Service → **Variables** → add from that client's `.env` file:

   | Variable | Value | Required |
   |---|---|---|
   | `SECRET_KEY` | from the file | **Yes** (app won't boot without it) |
   | `ADMIN_PASSWORD` | from the file | **Yes** (creates the admin login on first boot) |
   | `FIELD_ENCRYPTION_KEY` | from the file | Recommended |
   | `TREASURER_PASSWORD` / `SECRETARY_PASSWORD` | from the file | Optional (seeds those logins) |
   | `RESEND_API_KEY` | client's Resend key | For email |
   | `PAYSTACK_SECRET` / `PAYSTACK_PUBLIC` | client's own Paystack keys | Only if they take online payments |
   | `SUBSCRIPTION_EXPIRY` | `YYYY-MM-DD` | Optional billing lock |

   `DATABASE_URL` is already there from step 5 — leave it.

## 4. Go live (per client)

7. Service → **Settings → Networking → Generate Domain** (gives a
   `*.up.railway.app` URL), or **Custom Domain** → `client1.yourdomain.com` and
   add the CNAME Railway shows you at your DNS provider.
8. Deploy finishes → open the URL. On first boot the app **creates all tables and
   the admin user automatically** (`init_db()`), so you land on the login page.

## 5. First-login checklist (per client)

9. Log in as `admin` with the `ADMIN_PASSWORD` you set → **change the password**.
10. **Settings** → set coop name, logo, currency, savings rules
    (incl. `share_capital_pct` if used), interest rates.
11. **Settings → Email** → paste the Resend/Brevo key + verified sender, send a test.
12. **Data Migration** → import members / savings / loans (and **Opening
    Balances** if they're bringing historical figures) using the templates.
13. If you imported historical transactions or opening balances:
    **Accounting → Journal → "Backfill ledger"** once, so the GL-based
    statements (income statement, balance sheet, cash flow) reflect that history.
14. Create the treasurer/secretary/exco logins (or share the seeded ones).

Repeat 1–14 for `client2`.

---

## Updating all clients later

Push to `main` → both services redeploy automatically. `init_db()` is
idempotent (adds any new columns/tables without touching existing data), so
schema changes roll out safely.

## Backups

Each Postgres has its own data. Railway → the Postgres service → **Backups** (or
`pg_dump $DATABASE_URL` on a schedule). Because databases are separate, you can
restore one client without affecting the other.

## Cost note

2 web services + 2 Postgres on Railway is roughly $10–25/mo total depending on
usage (verify current Railway pricing). If that grows uncomfortable as you add
clients, the same "database per client" model runs on a single ~$5/mo VPS with
one Postgres instance holding one database per client — ask and I'll generate
the Docker/nginx setup for that.
