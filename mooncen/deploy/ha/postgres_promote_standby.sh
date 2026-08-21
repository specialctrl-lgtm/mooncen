#!/usr/bin/env bash
set -euo pipefail

if ! sudo -u postgres psql -At -c "SELECT pg_is_in_recovery();" | grep -qx t; then
  echo "This PostgreSQL instance is not a standby. Promotion skipped." >&2
  exit 1
fi

sudo -u postgres psql -v ON_ERROR_STOP=1 -c "SELECT pg_promote(wait_seconds => 60);"

if sudo -u postgres psql -At -c "SELECT pg_is_in_recovery();" | grep -qx f; then
  echo "Standby promoted to primary."
else
  echo "Promotion did not complete." >&2
  exit 1
fi
