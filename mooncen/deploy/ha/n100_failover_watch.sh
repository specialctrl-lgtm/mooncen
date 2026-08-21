#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/mooncen}"
ENABLE_FILE="${ENABLE_FILE:-$APP_DIR/failover/enable_auto_failover}"
STATE_DIR="${STATE_DIR:-$APP_DIR/failover}"
FAIL_COUNT_FILE="${FAIL_COUNT_FILE:-$STATE_DIR/cloud_fail_count}"
FAILOVER_LOG="${FAILOVER_LOG:-$STATE_DIR/failover.log}"
CLOUD_DB_HOST="${CLOUD_DB_HOST:-cloud}"
CLOUD_DB_PORT="${CLOUD_DB_PORT:-5432}"
CLOUD_API_HEALTH_URL="${CLOUD_API_HEALTH_URL:-}"
FAIL_THRESHOLD="${FAIL_THRESHOLD:-5}"
START_WORKERS="${START_WORKERS:-0}"
CRAWLER_CLOUD_DB_OVERRIDE_NAME="${CRAWLER_CLOUD_DB_OVERRIDE_NAME:-10-cloud-primary-db.conf}"

mkdir -p "$STATE_DIR"

log() {
  printf '%s %s\n' "$(date --iso-8601=seconds)" "$*" | tee -a "$FAILOVER_LOG"
}

remove_crawler_cloud_db_overrides() {
  local changed=0
  local unit
  for unit in mooncen-crawler.service mooncen-crawler-once.service mooncen-branch-coordinates.service; do
    local override="/etc/systemd/system/${unit}.d/${CRAWLER_CLOUD_DB_OVERRIDE_NAME}"
    if [ -f "$override" ]; then
      rm -f "$override"
      changed=1
      log "removed crawler cloud DB override: $override"
    fi
  done
  if [ "$changed" -eq 1 ]; then
    systemctl daemon-reload
  fi
}

if [ ! -f "$ENABLE_FILE" ]; then
  rm -f "$FAIL_COUNT_FILE"
  exit 0
fi

if ! command -v pg_isready >/dev/null 2>&1; then
  log "pg_isready not found."
  exit 1
fi

local_role="$(sudo -u postgres psql -Atqc "SELECT CASE WHEN pg_is_in_recovery() THEN 'standby' ELSE 'primary' END;" 2>/dev/null || echo unknown)"
if [ "$local_role" != "standby" ]; then
  log "n100 local DB role is $local_role, not standby. no action."
  exit 0
fi

cloud_ok=0
if pg_isready -h "$CLOUD_DB_HOST" -p "$CLOUD_DB_PORT" -t 3 >/dev/null 2>&1; then
  cloud_ok=1
fi

if [ -n "$CLOUD_API_HEALTH_URL" ] && curl -fsS --max-time 5 "$CLOUD_API_HEALTH_URL" >/dev/null 2>&1; then
  cloud_ok=1
fi

if [ "$cloud_ok" -eq 1 ]; then
  previous_fail_count=0
  if [ -f "$FAIL_COUNT_FILE" ]; then
    previous_fail_count="$(cat "$FAIL_COUNT_FILE" 2>/dev/null || echo 0)"
  fi
  printf '0' > "$FAIL_COUNT_FILE"
  if [ "${previous_fail_count:-0}" != "0" ]; then
    log "cloud healthy. fail count reset from $previous_fail_count. n100 remains standby."
  fi
  exit 0
fi

fail_count=0
if [ -f "$FAIL_COUNT_FILE" ]; then
  fail_count="$(cat "$FAIL_COUNT_FILE" 2>/dev/null || echo 0)"
fi
case "$fail_count" in
  ''|*[!0-9]*) fail_count=0 ;;
esac
fail_count=$((fail_count + 1))
printf '%s' "$fail_count" > "$FAIL_COUNT_FILE"
log "cloud health failed count=$fail_count threshold=$FAIL_THRESHOLD host=$CLOUD_DB_HOST:$CLOUD_DB_PORT"

if [ "$fail_count" -lt "$FAIL_THRESHOLD" ]; then
  exit 0
fi

log "failover threshold reached. promoting n100 PostgreSQL standby."
sudo -u postgres psql -v ON_ERROR_STOP=1 -c "SELECT pg_promote(wait_seconds => 60);"

promoted_role="$(sudo -u postgres psql -Atqc "SELECT CASE WHEN pg_is_in_recovery() THEN 'standby' ELSE 'primary' END;")"
if [ "$promoted_role" != "primary" ]; then
  log "promotion failed. role=$promoted_role"
  exit 1
fi

log "n100 promoted to primary. starting app services."
printf 'primary\n' | sudo tee /etc/mooncen-node-role >/dev/null
remove_crawler_cloud_db_overrides
sudo systemctl enable --now mooncen-api mooncen-frontend
sudo systemctl enable --now cloudflared || true
sudo systemctl enable --now mooncen-cloudflared-role-guard.timer || true
sudo systemctl enable --now mooncen-cloudflare-gate.timer || true

if [ "$START_WORKERS" = "1" ]; then
  log "starting workers because START_WORKERS=1."
  sudo systemctl enable --now mooncen-ai-worker mooncen-crawler || true
fi

printf '0' > "$FAIL_COUNT_FILE"
log "failover to n100 completed."
