#!/usr/bin/env bash
set -euo pipefail
umask 077

db_name="${DB_NAME:-mooncen}"
db_port="${DB_PORT:-5432}"
output_dir="${1:-/var/tmp/mooncen-gen1db-pre-crawler-$(date -u '+%Y%m%dT%H%M%SZ')}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run backup_gen1db_before_crawler_cutover.sh as root." >&2
  exit 77
fi
if [[ ! "$output_dir" =~ ^/var/tmp/mooncen-gen1db-pre-crawler-[0-9]{8}T[0-9]{6}Z$ ]]; then
  echo "Output directory must use /var/tmp/mooncen-gen1db-pre-crawler-YYYYMMDDTHHMMSSZ." >&2
  exit 64
fi
if [[ ! "$db_name" =~ ^[a-z_][a-z0-9_]*$ ]] || \
   [[ ! "$db_port" =~ ^[0-9]+$ ]] || [ "$db_port" -lt 1 ] || [ "$db_port" -gt 65535 ]; then
  echo "Invalid database name or port." >&2
  exit 64
fi
if [ -e "$output_dir" ]; then
  echo "Refusing to overwrite an existing gen1db backup: $output_dir" >&2
  exit 73
fi

install -d -o postgres -g postgres -m 0700 "$output_dir"
dump_path="$output_dir/${db_name}.dump"

runuser -u postgres -- pg_isready -q -p "$db_port" -d "$db_name"
runuser -u postgres -- pg_dump \
  --format=custom \
  --compress=6 \
  --no-owner \
  --no-privileges \
  --port="$db_port" \
  --dbname="$db_name" \
  --file="$dump_path"
runuser -u postgres -- pg_restore --list "$dump_path" >/dev/null

{
  printf 'captured_at=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  printf 'hostname=%s\n' "$(hostname)"
  printf 'database=localhost:%s/%s\n' "$db_port" "$db_name"
  runuser -u postgres -- psql -X -At -p "$db_port" -d "$db_name" <<'SQL'
SELECT 'courses=' || count(*) FROM courses;
SELECT 'active_courses=' || count(*) FROM courses WHERE is_active;
SELECT 'branches=' || count(*) FROM branches;
SELECT 'providers=' || count(DISTINCT provider) FROM courses;
SELECT 'latest_course_update=' || COALESCE(max(updated_at)::text, '') FROM courses;
SELECT 'latest_migration=' || COALESCE(max(version), '') FROM mooncen_schema_migrations;
SQL
  pg_lsclusters --no-header
} >"$output_dir/inventory.txt"

pg_version="$(pg_lsclusters --no-header | awk -v port="$db_port" '$3 == port {print $1; exit}')"
if [[ "$pg_version" =~ ^[0-9]+$ ]]; then
  hba="/etc/postgresql/$pg_version/main/pg_hba.conf"
  split_conf="/etc/postgresql/$pg_version/main/conf.d/90-mooncen-split.conf"
  for config_file in "$hba" "$split_conf"; do
    if [ -f "$config_file" ] && [ ! -L "$config_file" ]; then
      install -o root -g root -m 0600 "$config_file" "$output_dir/$(basename "$config_file")"
    fi
  done
fi

if [ -f /etc/mooncen/db-root-ca.crt ] && [ ! -L /etc/mooncen/db-root-ca.crt ]; then
  openssl x509 \
    -in /etc/mooncen/db-root-ca.crt \
    -noout \
    -subject \
    -issuer \
    -serial \
    -fingerprint \
    -sha256 >"$output_dir/db-ca-fingerprint.txt"
fi

chown -R root:root "$output_dir"
(
  cd "$output_dir"
  sha256sum -- * >SHA256SUMS
)

echo "gen1db pre-crawler backup ready: $output_dir"
