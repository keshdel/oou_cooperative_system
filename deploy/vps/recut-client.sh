#!/usr/bin/env bash
# Clear everything a client has posted, so the books can be imported again from
# scratch. Member records are kept.
#
# Use this when a first import landed wrong figures. You cannot simply import
# the corrected file on top: importing savings again ADDS to what is already
# there (so balances double), and importing loans again SKIPS any loan number
# that already exists (so the wrong figures just stay). The old rows have to go.
#
#   bash recut-client.sh <slug>          e.g.  bash recut-client.sh smtcoop
#
# REMOVED: savings, loans, loan repayments and the guarantor / approval /
#          request records attached to them; the whole general ledger, which
#          includes the opening-balance entry and any entries posted by hand;
#          dividend declarations and allocations; income, expenses, honorarium
#          and investments.
# KEPT:    members and all their details, member logins, staff logins, settings,
#          the chart of accounts, saved permissions, the audit trail, and CTAS.
#
# Each member's running savings total is reset to zero, because the import
# rebuilds it as the rows go back in. That is a figure, not member details --
# names, member numbers, phone numbers and logins are all left alone.
#
# A backup is taken first and a typed confirmation is required.
set -euo pipefail
cd "$(dirname "$0")"

SLUG="${1:-}"
if [[ -z "$SLUG" ]]; then
  echo "Usage: bash recut-client.sh <slug>    (e.g. bash recut-client.sh smtcoop)" >&2
  exit 1
fi
DB="coop_${SLUG}"

# shellcheck disable=SC1091
[[ -f .env ]] && { set -a; source .env; set +a; }

psql_run() { docker compose exec -T postgres psql -U postgres -d "$DB" "$@"; }

if ! psql_run -tAc 'SELECT 1' >/dev/null 2>&1; then
  echo "Cannot reach database ${DB}. Is the slug right, and is postgres up?" >&2
  exit 1
fi

echo "=== ${DB} — what is there now ==="
psql_run -c "
SELECT 'members (KEPT)' AS record, COUNT(*) FROM members
UNION ALL SELECT 'chart of accounts (KEPT)', COUNT(*) FROM accounts
UNION ALL SELECT 'savings',        COUNT(*) FROM savings
UNION ALL SELECT 'loans',          COUNT(*) FROM loans
UNION ALL SELECT 'repayments',     COUNT(*) FROM repayments
UNION ALL SELECT 'ledger entries', COUNT(*) FROM journal_entries;"

cat <<WARN

About to clear everything posted in ${DB}.

  Removed : savings, loans, loan repayments and the guarantor /
            approval / request records attached to them; the WHOLE
            general ledger, including the opening-balance entry and
            every entry posted by hand; dividends; income, expenses,
            honorarium and investments.
  Kept    : members and their details, all logins, settings, the chart
            of accounts, saved permissions, the audit trail, CTAS.

Each member's savings total is reset to zero and rebuilt by the import.

After this the books are empty, so post the opening balances from the
trial balance before anyone looks at a report.

WARN

read -r -p 'Type RECUT to continue: ' CONFIRM
if [[ "$CONFIRM" != "RECUT" ]]; then
  echo "Cancelled — nothing was changed."
  exit 1
fi

STAMP=$(date +%Y%m%d-%H%M%S)
mkdir -p backups
BACKUP="backups/${DB}-pre-recut-${STAMP}.sql.gz"
docker compose exec -T postgres pg_dump -U postgres "$DB" | gzip > "$BACKUP"
echo "Backup written to ${BACKUP}"

# One transaction: either all of it happens or none of it does. Records
# attached to a loan go before the loan itself, or the database refuses the
# delete. Each table is checked for existence first so a client on an older
# version does not fail the whole run.
psql_run -v ON_ERROR_STOP=1 <<'SQL'
BEGIN;
DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'dividend_allocations', 'dividend_declarations',
        'journal_lines', 'journal_entries',
        'repayments', 'loan_request_events', 'loan_approvals', 'loan_guarantors',
        'loans', 'savings',
        'honorarium', 'expenses', 'revenue', 'investments'
    ] LOOP
        IF to_regclass('public.' || t) IS NOT NULL THEN
            EXECUTE format('DELETE FROM %I', t);
            RAISE NOTICE 'cleared %', t;
        END IF;
    END LOOP;
END $$;

UPDATE members SET total_savings = 0, shares_value = 0;
COMMIT;
SQL

echo
echo "=== ${DB} — after clearing ==="
psql_run -c "
SELECT 'members (KEPT)' AS record, COUNT(*) FROM members
UNION ALL SELECT 'chart of accounts (KEPT)', COUNT(*) FROM accounts
UNION ALL SELECT 'savings',        COUNT(*) FROM savings
UNION ALL SELECT 'loans',          COUNT(*) FROM loans
UNION ALL SELECT 'repayments',     COUNT(*) FROM repayments
UNION ALL SELECT 'ledger entries', COUNT(*) FROM journal_entries;"

cat <<NEXT

Done. Members and logins are untouched. The books are empty.

Next, in the app under Data Migration:
  1. Import Savings      — IMPORT_1_savings.csv
  2. Import Loans        — IMPORT_2_loans.csv
  3. Opening Balances    — the July trial balance
Then Settings > System > Reconcile savings, and spot-check a few
members against Tally before telling anyone.
NEXT
