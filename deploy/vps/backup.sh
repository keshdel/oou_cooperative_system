#!/usr/bin/env bash
# Back up every client database to ./backups/, keeping the last 14 days.
# Run nightly via cron (see README). Each client is dumped separately so you
# can restore one without touching the others.
set -euo pipefail
cd "$(dirname "$0")"

# shellcheck disable=SC1091
[[ -f .env ]] && { set -a; source .env; set +a; }

STAMP=$(date +%Y%m%d-%H%M%S)
OUT="backups"
mkdir -p "$OUT"

# List coop_* databases and dump each.
DBS=$(docker compose exec -T postgres psql -U postgres -tAc \
  "SELECT datname FROM pg_database WHERE datname LIKE 'coop_%'")

for DB in $DBS; do
  DB=$(echo "$DB" | tr -d '[:space:]')
  [[ -z "$DB" ]] && continue
  FILE="${OUT}/${DB}-${STAMP}.sql.gz"
  docker compose exec -T postgres pg_dump -U postgres "$DB" | gzip > "$FILE"
  echo "backed up ${DB} -> ${FILE}"
done

# Keep 14 days of backups.
find "$OUT" -name '*.sql.gz' -type f -mtime +14 -delete
echo "Old backups older than 14 days pruned."

# Optional offsite copy. If an rclone remote is configured (in ./.backup-remote
# or the OFFSITE_REMOTE env var) and rclone is installed, copy the backups there.
# 'copy' is additive — it never deletes offsite files, so an offsite copy
# survives even if the local box is wiped. Credentials live in rclone's own
# config, never here.
OFFSITE="${OFFSITE_REMOTE:-}"
if [ -z "$OFFSITE" ] && [ -f .backup-remote ]; then
  OFFSITE="$(tr -d '[:space:]' < .backup-remote)"
fi
if [ -n "$OFFSITE" ]; then
  if command -v rclone >/dev/null 2>&1; then
    if rclone copy "$OUT" "$OFFSITE" --transfers 4; then
      echo "Offsite copy sent to $OFFSITE"
    else
      echo "WARNING: offsite copy to $OFFSITE failed" >&2
    fi
  else
    echo "WARNING: OFFSITE remote set but rclone is not installed" >&2
  fi
fi

# To restore a database:
#   gunzip -c backups/coop_client1-YYYYMMDD-HHMMSS.sql.gz | \
#     docker compose exec -T postgres psql -U postgres -d coop_client1
