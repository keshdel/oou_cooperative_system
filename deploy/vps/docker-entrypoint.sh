#!/bin/sh
# Wait for Postgres to accept connections, then start the app.
# The app runs init_db() on boot, so the database only needs to exist (empty is fine).
set -e

python - <<'PY'
import os, sys, time
import psycopg2
url = os.environ.get('DATABASE_URL', '')
if not url:
    print('No DATABASE_URL set', file=sys.stderr); sys.exit(1)
for attempt in range(60):
    try:
        psycopg2.connect(url).close()
        print('Database is ready.')
        sys.exit(0)
    except Exception as exc:
        print(f'Waiting for database... ({exc})')
        time.sleep(2)
print('Database never became reachable', file=sys.stderr)
sys.exit(1)
PY

# 2 workers x 2 threads is comfortable for a low-traffic coop on a small VPS.
exec gunicorn app:app --workers 2 --threads 2 --timeout 120 --bind 0.0.0.0:8000
