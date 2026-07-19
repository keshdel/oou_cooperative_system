#!/usr/bin/env bash
# Remove a client's container and (optionally) its database.
#   ./remove-client.sh <name>            # stops the app, keeps the database
#   ./remove-client.sh <name> --drop-db  # also permanently deletes the database
set -euo pipefail
cd "$(dirname "$0")"

NAME="${1:-}"
DROP_DB="${2:-}"
if [[ -z "$NAME" ]]; then
  echo "Usage: ./remove-client.sh <name> [--drop-db]"
  exit 1
fi

# shellcheck disable=SC1091
[[ -f .env ]] && { set -a; source .env; set +a; }

echo "==> Stopping app-${NAME}"
docker compose stop "app-${NAME}" 2>/dev/null || true
docker compose rm -f "app-${NAME}" 2>/dev/null || true

rm -f "clients/${NAME}.env"
python3 generate.py
docker compose up -d   # reload Caddy without this client

if [[ "$DROP_DB" == "--drop-db" ]]; then
  echo "!! Dropping database coop_${NAME} PERMANENTLY in 5s (Ctrl-C to cancel)"
  sleep 5
  docker compose exec -T postgres dropdb -U postgres --if-exists "coop_${NAME}"
  echo "    database coop_${NAME} dropped"
else
  echo "    database coop_${NAME} kept (re-add the client later to reuse it)."
  echo "    add --drop-db to delete it permanently."
fi
echo "Done."
