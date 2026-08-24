#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/mooncen}"
STATE_DIR="${STATE_DIR:-$APP_DIR/failover}"
DISABLE_FILE="${DISABLE_FILE:-$STATE_DIR/disable_cloudflare_gate}"
FAIL_COUNT_FILE="${FAIL_COUNT_FILE:-$STATE_DIR/cloudflare_gate_fail_count}"
RECOVER_COUNT_FILE="${RECOVER_COUNT_FILE:-$STATE_DIR/cloudflare_gate_recover_count}"
GATE_LOG="${GATE_LOG:-$STATE_DIR/cloudflare_gate.log}"

CLOUDFLARED_SERVICE="${CLOUDFLARED_SERVICE:-cloudflared.service}"
FAIL_THRESHOLD="${CLOUDFLARE_GATE_FAIL_THRESHOLD:-2}"
RECOVER_THRESHOLD="${CLOUDFLARE_GATE_RECOVER_THRESHOLD:-1}"
AUTO_RESTORE="${CLOUDFLARE_GATE_AUTO_RESTORE:-1}"
START_DISABLED_CLOUDFLARED="${CLOUDFLARE_GATE_START_DISABLED:-0}"

REQUIRED_SERVICES="${CLOUDFLARE_GATE_REQUIRED_SERVICES:-postgresql nginx mooncen-api mooncen-frontend}"
API_HEALTH_URL="${CLOUDFLARE_GATE_API_HEALTH_URL:-http://127.0.0.1:8001/health}"
NGINX_HEALTH_URL="${CLOUDFLARE_GATE_NGINX_HEALTH_URL:-http://127.0.0.1/health}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
FRONTEND_URL="${CLOUDFLARE_GATE_FRONTEND_URL:-http://127.0.0.1:${FRONTEND_PORT}}"
HTTP_TIMEOUT="${CLOUDFLARE_GATE_HTTP_TIMEOUT:-3}"
DB_NAME="${DB_NAME:-mooncen}"
CHECK_DB="${CLOUDFLARE_GATE_CHECK_DB:-1}"

mkdir -p "$STATE_DIR"

log() {
  printf '%s %s\n' "$(date --iso-8601=seconds)" "$*" | tee -a "$GATE_LOG"
}

read_counter() {
  local file="$1"
  local value=0
  if [ -f "$file" ]; then
    value="$(cat "$file" 2>/dev/null || echo 0)"
  fi
  case "$value" in
    ''|*[!0-9]*) value=0 ;;
  esac
  printf '%s' "$value"
}

write_counter() {
  printf '%s' "$2" > "$1"
}

if [ -f "$DISABLE_FILE" ]; then
  log "cloudflare gate disabled by $DISABLE_FILE"
  exit 0
fi

reasons=()

for service in $REQUIRED_SERVICES; do
  if ! systemctl is-active --quiet "$service"; then
    reasons+=("service:$service")
  fi
done

if [ "$CHECK_DB" = "1" ]; then
  if command -v pg_isready >/dev/null 2>&1 && ! pg_isready -q -t 3; then
    reasons+=("db:pg_isready")
  fi
  if ! runuser -u postgres -- psql -d "$DB_NAME" -Atqc "SELECT 1;" >/dev/null 2>&1; then
    reasons+=("db:psql:$DB_NAME")
  fi
fi

if [ -n "$API_HEALTH_URL" ] && ! curl -fsS --max-time "$HTTP_TIMEOUT" "$API_HEALTH_URL" >/dev/null 2>&1; then
  reasons+=("api:$API_HEALTH_URL")
fi

if [ -n "$NGINX_HEALTH_URL" ] && ! curl -fsS --max-time "$HTTP_TIMEOUT" "$NGINX_HEALTH_URL" >/dev/null 2>&1; then
  reasons+=("nginx:$NGINX_HEALTH_URL")
fi

if [ -n "$FRONTEND_URL" ] && ! curl -fsSI --max-time "$HTTP_TIMEOUT" "$FRONTEND_URL" >/dev/null 2>&1; then
  reasons+=("frontend:$FRONTEND_URL")
fi

if [ "${#reasons[@]}" -gt 0 ]; then
  fail_count="$(read_counter "$FAIL_COUNT_FILE")"
  fail_count=$((fail_count + 1))
  write_counter "$FAIL_COUNT_FILE" "$fail_count"
  write_counter "$RECOVER_COUNT_FILE" 0
  log "local health failed count=$fail_count threshold=$FAIL_THRESHOLD reasons=${reasons[*]}"

  if [ "$fail_count" -ge "$FAIL_THRESHOLD" ]; then
    if systemctl is-active --quiet "$CLOUDFLARED_SERVICE"; then
      log "stopping $CLOUDFLARED_SERVICE to block external traffic."
      systemctl stop "$CLOUDFLARED_SERVICE"
    else
      log "$CLOUDFLARED_SERVICE already inactive."
    fi
  fi
  exit 0
fi

write_counter "$FAIL_COUNT_FILE" 0
recover_count="$(read_counter "$RECOVER_COUNT_FILE")"
recover_count=$((recover_count + 1))
write_counter "$RECOVER_COUNT_FILE" "$recover_count"
log "local health ok recover_count=$recover_count threshold=$RECOVER_THRESHOLD"

if [ "$AUTO_RESTORE" != "1" ] || [ "$recover_count" -lt "$RECOVER_THRESHOLD" ]; then
  exit 0
fi

if systemctl is-active --quiet "$CLOUDFLARED_SERVICE"; then
  exit 0
fi

enabled_state="$(systemctl is-enabled "$CLOUDFLARED_SERVICE" 2>/dev/null || true)"
if [ "$enabled_state" = "enabled" ] || [ "$START_DISABLED_CLOUDFLARED" = "1" ]; then
  log "starting $CLOUDFLARED_SERVICE after local health recovered."
  systemctl start "$CLOUDFLARED_SERVICE"
else
  log "$CLOUDFLARED_SERVICE is inactive but not enabled. skip auto restore. enabled_state=${enabled_state:-unknown}"
fi
