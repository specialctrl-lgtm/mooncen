#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  sudo ./deploy/ha/postgres_standby_clone.sh \
    --primary-host <cloud_ip_or_host> \
    --replication-password <password> \
    --slot-name mooncen_n100_standby \
    --wipe-data

Run on standby DB server: n100.

This replaces the local PostgreSQL data directory with a base backup from cloud.
The destructive part only runs when --wipe-data is present.
USAGE
}

PRIMARY_HOST=""
PRIMARY_PORT="${PRIMARY_PORT:-5432}"
REPLICATION_USER="${REPLICATION_USER:-mooncen_replica}"
REPLICATION_PASSWORD=""
SLOT_NAME="${SLOT_NAME:-mooncen_n100_standby}"
WIPE_DATA=0

while [ $# -gt 0 ]; do
  case "$1" in
    --primary-host)
      PRIMARY_HOST="${2:-}"
      shift 2
      ;;
    --primary-port)
      PRIMARY_PORT="${2:-}"
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
    --wipe-data)
      WIPE_DATA=1
      shift
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

if [ -z "$PRIMARY_HOST" ] || [ -z "$REPLICATION_PASSWORD" ]; then
  usage >&2
  exit 2
fi

if [ "$WIPE_DATA" -ne 1 ]; then
  echo "Refusing to replace PostgreSQL data directory without --wipe-data." >&2
  exit 3
fi

if ! command -v pg_lsclusters >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y postgresql postgresql-contrib postgis
fi

read -r PG_VERSION PG_CLUSTER _PG_PORT _PG_STATUS _PG_OWNER PG_DATA _REST < <(pg_lsclusters --no-header | awk 'NR==1 {print $1, $2, $3, $4, $5, $6}')
if [ -z "${PG_VERSION:-}" ] || [ -z "${PG_CLUSTER:-}" ] || [ -z "${PG_DATA:-}" ]; then
  echo "No PostgreSQL cluster found." >&2
  exit 1
fi

sudo systemctl stop postgresql
sudo install -d -m 700 -o postgres -g postgres "$PG_DATA"
sudo find "$PG_DATA" -mindepth 1 -maxdepth 1 -exec rm -rf {} +

sudo -u postgres env PGPASSWORD="$REPLICATION_PASSWORD" pg_basebackup \
  -h "$PRIMARY_HOST" \
  -p "$PRIMARY_PORT" \
  -U "$REPLICATION_USER" \
  -D "$PG_DATA" \
  -R \
  -S "$SLOT_NAME" \
  -X stream \
  -P

sudo chown -R postgres:postgres "$PG_DATA"
sudo chmod 700 "$PG_DATA"
sudo systemctl start postgresql

cat <<EOF
Standby clone completed.
cluster=${PG_VERSION}/${PG_CLUSTER}
data_dir=${PG_DATA}
primary=${PRIMARY_HOST}:${PRIMARY_PORT}
slot_name=${SLOT_NAME}

Check replication:
  sudo -u postgres psql -c "SELECT pg_is_in_recovery(), now() - pg_last_xact_replay_timestamp() AS replay_delay;"
EOF
