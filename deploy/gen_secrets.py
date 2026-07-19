#!/usr/bin/env python
"""
Generate the per-client secrets you paste into each Railway service's Variables.

Usage:
    python deploy/gen_secrets.py client1 client2 [client3 ...]

For each client it writes deploy/secrets/<name>.env with a unique SECRET_KEY,
FIELD_ENCRYPTION_KEY, and a strong ADMIN_PASSWORD. That folder is gitignored,
so the secrets never get committed. Open the file, copy the values into the
client's Railway service, then keep the file somewhere safe (a password manager)
and delete it from disk.

Nothing here is client-specific magic — you can re-run it any time you add a
client. DATABASE_URL is NOT generated: Railway sets it automatically when you
add a Postgres database to the service.
"""
import os
import secrets
import sys

from cryptography.fernet import Fernet

REQUIRED_NOTE = "# Paste these into Railway → <service> → Variables. Keep this file secret."


def strong_password(n=20):
    # URL-safe, no ambiguous quoting; comfortably above the app's policy.
    return secrets.token_urlsafe(n)


def gen_for(name):
    return {
        "SECRET_KEY": secrets.token_hex(32),
        "FIELD_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "ADMIN_PASSWORD": strong_password(),
        # Optional role logins — comment out in Railway if you don't want them.
        "TREASURER_PASSWORD": strong_password(),
        "SECRETARY_PASSWORD": strong_password(),
    }


def main(names):
    if not names:
        print(__doc__)
        sys.exit(1)
    out_dir = os.path.join(os.path.dirname(__file__), "secrets")
    os.makedirs(out_dir, exist_ok=True)
    for name in names:
        path = os.path.join(out_dir, f"{name}.env")
        vals = gen_for(name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# ── {name} ──────────────────────────────────────────────\n")
            f.write(REQUIRED_NOTE + "\n")
            f.write("# DATABASE_URL is set automatically by the Railway Postgres add-on.\n\n")
            for k, v in vals.items():
                f.write(f"{k}={v}\n")
            f.write("\n# Add when ready:\n")
            f.write("# RESEND_API_KEY=...            (email)\n")
            f.write("# PAYSTACK_SECRET=...           (this client's own Paystack)\n")
            f.write("# PAYSTACK_PUBLIC=...\n")
            f.write("# SUBSCRIPTION_EXPIRY=2027-01-01  (optional billing lock)\n")
        print(f"  wrote {path}")
    print("\nDone. Open each file, copy the values into that client's Railway service,")
    print("then store the file in your password manager and delete it from disk.")


if __name__ == "__main__":
    main(sys.argv[1:])
