#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  sudo ./deploy/ha/postgres_primary_prepare.sh \
    --standby-ip <n100_private_or_public_ip> \
    --replication-password <password> \
    --slot-name mooncen_n100_standby

Run on primary DB server: cloud.

This enables PostgreSQL physical replication access for the n100 standby.
It does not restart PostgreSQL; it reloads configuration after ALTER SYSTEM.
USAGE
}

STANDBY_IP=""
REPLICATION_USER="${REPLICATION_USER:-mooncen_replica}"
REPLICATION_PASSWORD=""
SLOT_NAME="${SLOT_NAME:-mooncen_n100_standby}"

while [ $# -gt 0 ]; do
  case "$1" in
    --standby-ip)
      STANDBY_IP="${2:-}"
      shift 2
      ;;
    --replication-user)
      REPLICATION_USER="${2:-}"
      shift 2
      ;;
    --replication-password)
      REPLICATION_PASSWORD="${2:-}"
      shift 2
      ;;
    --slot-name)
      SLOT_NAME="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [ -z "$STANDBY_IP" ] || [ -z "$REPLICATION_PASSWORD" ]; then
  usage >&2
  exit 2
fi

if ! command -v pg_lsclusters >/dev/null 2>&1; then
  echo "pg_lsclusters not found. This script targets Debian/Ubuntu PostgreSQL packages." >&2
  exit 1
fi

read -r PG_VERSION PG_CLUSTER _PG_PORT _PG_STATUS _PG_OWNER PG_DATA _REST < <(pg_lsclusters --no-header | awk 'NR==1 {print $1, $2, $3, $4, $5, $6}')
if [ -z "${PG_VERSION:-}" ] || [ -z "${PG_CLUSTER:-}" ]; then
  echo "No PostgreSQL cluster found." >&2
  exit 1
fi

PG_HBA="/etc/postgresql/${PG_VERSION}/${PG_CLUSTER}/pg_hba.conf"

sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${REPLICATION_USER}') THEN
    CREATE ROLE ${REPLICATION_USER} WITH REPLICATION LOGIN PASSWORD '${REPLICATION_PASSWORD}';
  ELSE
    ALTER ROLE ${REPLICATION_USER} WITH REPLICATION LOGIN PASSWORD '${REPLICATION_PASSWORD}';
  END IF;
END
\$\$;
ALTER SYSTEM SET listen_addresses = '*';
ALTER SYSTEM SET wal_level = 'replica';
ALTER SYSTEM SET max_wal_senders = '10';
ALTER SYSTEM SET max_replication_slots = '4';
ALTER SYSTEM SET hot_standby = 'on';
SQL

HBA_LINE="host replication ${REPLICATION_USER} ${STANDBY_IP}/32 scram-sha-256"
if ! sudo grep -Fqx "$HBA_LINE" "$PG_HBA"; then
  printf '%s\n' "$HBA_LINE" | sudo tee -a "$PG_HBA" >/dev/null
fi

sudo systemctl reload postgresql

sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
SELECT pg_create_physical_replication_slot('${SLOT_NAME}')
WHERE NOT EXISTS (
  SELECT 1 FROM pg_replication_slots WHERE slot_name = '${SLOT_NAME}'
);
SQL

cat <<EOF
Primary replication prepared.
cluster=${PG_VERSION}/${PG_CLUSTER}
pg_hba=${PG_HBA}
replication_user=${REPLICATION_USER}
slot_name=${SLOT_NAME}
standby_ip=${STANDBY_IP}

If n100 cannot connect, also check:
- cloud firewall allows TCP 5432 from ${STANDBY_IP}
- n100 firewall allows outbound TCP 5432
- cloud PostgreSQL port is reachable from n100
EOF
