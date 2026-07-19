# Running all your clients on one cheap VPS

One small server runs everything: a single Postgres database engine (holding a
**separate database per client**), one small copy of the app per client, and
Caddy, which gives every client automatic HTTPS. Adding a client is one command.

```
                        ┌──────────── your VPS (~$5/mo) ────────────┐
   client1.you.com ──►  │  Caddy ──► app-client1 ──►  coop_client1  │
   client2.you.com ──►  │        └─► app-client2 ──►  coop_client2  │  ◄─ one Postgres,
                        │                             (one DB each)  │     many databases
                        └────────────────────────────────────────────┘
```

Data stays fully separated — each client has its own database. All app copies
run the same code, so updates go out to everyone at once.

---

## First-time setup (about 20 minutes)

### 1. Get a server and a domain
- **VPS:** create an **Ubuntu 24.04** server (Hetzner, DigitalOcean, or Vultr).
  2 GB RAM is comfortable for a handful of clients. Note its **public IP**.
- **Domain:** buy one (e.g. `yourcoop.com`). You'll give each client a
  sub-address like `client1.yourcoop.com`.

### 2. Point the domains at the server
At your domain provider, add an **A record** for each client sub-address,
pointing at the server's IP:
```
client1.yourcoop.com   A   <server IP>
client2.yourcoop.com   A   <server IP>
```
(HTTPS won't be issued until DNS points here, so do this early.)

### 3. Install Docker on the server
SSH into the server, then:
```bash
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/keshdel/oou_cooperative_system/main/deploy/vps/install-docker.sh)"
```
Or clone the repo first and run `sudo deploy/vps/install-docker.sh`.

### 4. Get the code
```bash
git clone https://github.com/keshdel/oou_cooperative_system.git
cd oou_cooperative_system/deploy/vps
```

### 5. Add each client — one command each
```bash
./add-client.sh client1 client1.yourcoop.com
./add-client.sh client2 client2.yourcoop.com
```
That's it. Each command:
- generates that client's secrets and admin password,
- creates their database,
- builds/starts their app copy,
- adds their HTTPS domain (Caddy fetches the certificate automatically).

Open `https://client1.yourcoop.com`, log in as **admin** (password is printed at
the end and saved in `clients/client1.env`), change the password, then import
their members under **Data Migration**.

---

## Everyday tasks

**Add another client later**
```bash
./add-client.sh client3 client3.yourcoop.com
```

**Update all clients to the latest code**
```bash
git pull
docker compose up -d --build
```
The app updates its own database structure on start, safely, without losing data.

**See what's running**
```bash
docker compose ps
```

**Turn on nightly backups** (keeps 14 days, one file per client)
```bash
crontab -e
# add this line (adjust the path):
0 2 * * *  cd /root/oou_cooperative_system/deploy/vps && ./backup.sh >> backups/backup.log 2>&1
```

**Restore one client from a backup**
```bash
gunzip -c backups/coop_client1-YYYYMMDD-HHMMSS.sql.gz | \
  docker compose exec -T postgres psql -U postgres -d coop_client1
```

**Remove a client**
```bash
./remove-client.sh client1            # stop the app, keep the data
./remove-client.sh client1 --drop-db  # also delete the database (permanent)
```

---

## Good to know
- **Secrets never leave the server.** `deploy/vps/.env` and everything in
  `clients/` are gitignored. Keep a copy of each client's admin password in your
  password manager.
- **Email:** set `RESEND_API_KEY` (or Brevo) in the client's `clients/<name>.env`,
  then `docker compose up -d` to apply. Plain SMTP is not needed.
- **Payments:** put each client's own Paystack keys in their `clients/<name>.env`.
- **Sizing:** if the server gets busy, resize it in the provider dashboard — no
  reinstall needed. One 2 GB box handles many small coops.
- **Postgres is private:** it isn't exposed to the internet; only the app
  containers reach it over Docker's internal network.
