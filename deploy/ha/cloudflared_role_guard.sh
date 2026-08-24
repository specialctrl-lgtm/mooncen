#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/mooncen}"
STATE_DIR="${STATE_DIR:-$APP_DIR/failover}"
ROLE_FILE="${ROLE_FILE:-/etc/mooncen-node-role}"
GUARD_LOG="${GUARD_LOG:-$STATE_DIR/cloudflared_role_guard.log}"
CLOUDFLARED_SERVICE="${CLOUDFLARED_SERVICE:-cloudflared.service}"

mkdir -p "$STATE_DIR"

log() {
  printf '%s %s\n' "$(date --iso-8601=seconds)" "$*" | tee -a "$GUARD_LOG"
}

role="$(cat "$ROLE_FILE" 2>/dev/null || true)"
role="$(printf '%s' "$role" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"

if [ -z "$role" ]; then
  if command -v psql >/dev/null 2>&1; then
    role="$(runuser -u postgres -- psql -Atqc "SELECT CASE WHEN pg_is_in_recovery() THEN 'standby' ELSE 'primary' END;" 2>/dev/null || true)"
  fi
fi

case "$role" in
  primary|active)
    role="primary"
    ;;
  standby|replica|backup)
    role="standby"
    ;;
  *)
    log "unknown role='${role:-empty}'. skip cloudflared guard."
    exit 0
    ;;
esac

if ! systemctl cat "$CLOUDFLARED_SERVICE" >/dev/null 2>&1; then
  log "$CLOUDFLARED_SERVICE is not installed. skip."
  exit 0
fi

if [ "$role" = "standby" ]; then
  if systemctl is-active --quiet "$CLOUDFLARED_SERVICE"; then
    log "standby node detected. stopping $CLOUDFLARED_SERVICE."
    systemctl stop "$CLOUDFLARED_SERVICE"
  fi
  if systemctl is-enabled --quiet "$CLOUDFLARED_SERVICE"; then
    log "standby node detected. disabling $CLOUDFLARED_SERVICE."
    systemctl disable "$CLOUDFLARED_SERVICE" >/dev/null
  fi
  exit 0
fi

if systemctl is-enabled --quiet "$CLOUDFLARED_SERVICE"; then
  if ! systemctl is-active --quiet "$CLOUDFLARED_SERVICE"; then
    log "primary node with enabled tunnel. starting $CLOUDFLARED_SERVICE."
    systemctl start "$CLOUDFLARED_SERVICE"
  fi
else
  log "primary node but $CLOUDFLARED_SERVICE is disabled. install token or enable service manually."
fi
