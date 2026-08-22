#!/usr/bin/env bash
set -euo pipefail
umask 077

output_dir="${1:-/var/tmp/mooncen-n100-migration-$(date -u '+%Y%m%dT%H%M%SZ')}"
staging_port="${STAGING_DB_PORT:-55432}"
staging_db="${STAGING_DB_NAME:-mooncen_staging}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run backup_n100_for_gen1crawler.sh as root." >&2
  exit 77
fi
if [[ ! "$output_dir" =~ ^/var/tmp/mooncen-n100-migration-[0-9]{8}T[0-9]{6}Z$ ]]; then
  echo "Output directory must use /var/tmp/mooncen-n100-migration-YYYYMMDDTHHMMSSZ." >&2
  exit 64
fi
if [[ ! "$staging_port" =~ ^[0-9]+$ ]] || [ "$staging_port" -lt 1 ] || [ "$staging_port" -gt 65535 ]; then
  echo "Invalid staging port." >&2
  exit 64
fi
if [[ ! "$staging_db" =~ ^[a-z_][a-z0-9_]*$ ]]; then
  echo "Invalid staging database name." >&2
  exit 64
fi
if [ -e "$output_dir" ]; then
  echo "Refusing to overwrite an existing migration backup: $output_dir" >&2
  exit 73
fi

install -d -o postgres -g postgres -m 0700 "$output_dir"
dump_path="$output_dir/${staging_db}.dump"

runuser -u postgres -- pg_isready -q -p "$staging_port" -d "$staging_db"
runuser -u postgres -- pg_dump \
  --format=custom \
  --compress=6 \
  --no-owner \
  --no-privileges \
  --port="$staging_port" \
  --dbname="$staging_db" \
  --file="$dump_path"
runuser -u postgres -- pg_restore --list "$dump_path" >/dev/null

{
  printf 'captured_at=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  printf 'hostname=%s\n' "$(hostname)"
  printf 'staging_endpoint=localhost:%s/%s\n' "$staging_port" "$staging_db"
  runuser -u postgres -- psql -X -At -p "$staging_port" -d "$staging_db" <<'SQL'
SELECT 'courses=' || count(*) FROM courses;
SELECT 'active_courses=' || count(*) FROM courses WHERE is_active;
SELECT 'branches=' || count(*) FROM branches;
SELECT 'crawl_batches=' || count(*) FROM crawl_batches;
SELECT 'latest_batch=' || COALESCE(max(crawl_batch_id::text), '') FROM crawl_batches;
SELECT 'latest_course_update=' || COALESCE(max(updated_at)::text, '') FROM courses;
SQL
  pg_lsclusters --no-header
} >"$output_dir/inventory.txt"

systemctl list-unit-files \
  'mooncen-crawler*' \
  'mooncen-staging-apply*' \
  'mooncen-ops-bot*' \
  --no-pager >"$output_dir/unit-files.txt" || true
systemctl list-timers \
  'mooncen-crawler*' \
  'mooncen-staging-apply*' \
  --all \
  --no-pager >"$output_dir/timers.txt" || true
journalctl \
  -u mooncen-crawler.service \
  -u mooncen-crawler-once.service \
  -u mooncen-staging-apply.service \
  -u mooncen-staging-apply-dry-run.service \
  -u mooncen-ops-bot.service \
  --since '2026-07-01' \
  --no-pager >"$output_dir/services.journal" || true

for state_file in \
  /var/lib/mooncen-bot/bot_state.json \
  /opt/mooncen/failover/failover.log \
  /opt/mooncen/failover/cloud_fail_count \
  /opt/mooncen/failover/enable_auto_failover; do
  if [ -f "$state_file" ] && [ ! -L "$state_file" ]; then
    install -o root -g root -m 0600 "$state_file" "$output_dir/$(basename "$state_file")"
  fi
done

for protected_env in /etc/mooncen/crawler.env /etc/mooncen/applier.env /etc/mooncen/bot.env; do
  if [ -f "$protected_env" ] && [ ! -L "$protected_env" ]; then
    stat -c '%n owner=%U:%G mode=%a size=%s' "$protected_env"
    sha256sum "$protected_env"
  fi
done >"$output_dir/protected-env-fingerprints.txt"

chown -R root:root "$output_dir"
(
  cd "$output_dir"
  sha256sum -- * >SHA256SUMS
)

echo "N100 migration backup ready: $output_dir"
