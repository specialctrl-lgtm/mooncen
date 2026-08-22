#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 0 ]; then
  echo "mooncen PostgreSQL role helper accepts no arguments" >&2
  exit 64
fi

exec /usr/bin/env -i \
  HOME=/var/lib/postgresql \
  USER=postgres \
  LOGNAME=postgres \
  PATH=/usr/bin:/bin \
  PGHOST=/var/run/postgresql \
  PGPORT=5432 \
  /usr/bin/psql -X --no-password -d postgres -Atqc \
  "SELECT CASE WHEN pg_is_in_recovery() THEN 'standby' ELSE 'primary' END;"
