#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/mooncen}"
TEXTFILE_DIR="${TEXTFILE_DIR:-/var/lib/node_exporter/textfile_collector}"
OUT_FILE="$TEXTFILE_DIR/mooncen.prom"
TMP_FILE="${OUT_FILE}.$$"

mkdir -p "$TEXTFILE_DIR"

escape_label() {
  printf '%s' "${1:-}" | sed 's/\\/\\\\/g; s/"/\\"/g; s/\n/ /g'
}

read_kv() {
  local key="$1"
  local file="$2"
  if [ -r "$file" ]; then
    awk -F= -v key="$key" '$1 == key {print substr($0, length($1) + 2); exit}' "$file"
  fi
}

service_active() {
  local unit="$1"
  local state
  state="$(systemctl is-active "$unit" 2>/dev/null || true)"
  [ "$state" = "active" ] && printf '1' || printf '0'
}

service_enabled() {
  local unit="$1"
  local state
  state="$(systemctl is-enabled "$unit" 2>/dev/null || true)"
  case "$state" in
    enabled|static|generated|linked) printf '1' ;;
    *) printf '0' ;;
  esac
}

service_result_failed() {
  local unit="$1"
  local result state
  result="$(systemctl show "$unit" -p Result --value 2>/dev/null || true)"
  state="$(systemctl is-active "$unit" 2>/dev/null || true)"
  if [ "$state" = "failed" ]; then
    printf '1'
  elif [ -z "$result" ] || [ "$result" = "success" ]; then
    printf '0'
  else
    printf '1'
  fi
}

postgres_in_recovery() {
  local value=""
  if command -v psql >/dev/null 2>&1; then
    if [ "$(id -u)" -eq 0 ] && command -v runuser >/dev/null 2>&1; then
      value="$(runuser -u postgres -- psql -Atqc "SELECT CASE WHEN pg_is_in_recovery() THEN 1 ELSE 0 END;" 2>/dev/null || true)"
    else
      value="$(psql -Atqc "SELECT CASE WHEN pg_is_in_recovery() THEN 1 ELSE 0 END;" 2>/dev/null || true)"
    fi
  fi
  case "$value" in
    0|1) printf '%s' "$value" ;;
    *) printf -- '-1' ;;
  esac
}

crawler_cycle_state_values() {
  local state_file="$APP_DIR/logs/crawler_cycle_state.json"
  if ! command -v python3 >/dev/null 2>&1; then
    printf '0|0|0|unknown|0|0|0|0'
    return
  fi
  python3 -I - "$state_file" <<'PY' 2>/dev/null || printf '0|0|0|unknown|0|0|0|0'
import json
import os
import stat
import sys
from datetime import datetime

path = sys.argv[1]
allowed_outcomes = {"success", "partial_success", "failed", "zero_provider", "running"}


def epoch(value):
    if not isinstance(value, str) or not value or len(value) > 64:
        return 0
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("cycle timestamp has no timezone")
    result = int(parsed.timestamp())
    if result < 0:
        raise ValueError("cycle timestamp is negative")
    return result


def count(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1_000_000:
        raise ValueError("cycle count is invalid")
    return value


info = os.lstat(path)
if not stat.S_ISREG(info.st_mode) or info.st_size <= 0 or info.st_size > 65_536:
    raise ValueError("cycle state file is invalid")
with open(path, "r", encoding="utf-8") as state_file:
    state = json.load(state_file)
if not isinstance(state, dict) or state.get("schema_version") != 1:
    raise ValueError("cycle state schema is invalid")
outcome = state.get("final_outcome")
if outcome not in allowed_outcomes:
    raise ValueError("cycle outcome is invalid")
requested = count(state.get("providers_requested"))
completed = count(state.get("providers_completed"))
failed = count(state.get("providers_failed"))
if completed > requested or failed > requested:
    raise ValueError("cycle provider counts are inconsistent")
zero_provider = 1 if state.get("zero_provider") is True else 0
last_success = epoch(state.get("last_success_at")) if state.get("last_success_at") else 0
last_finished = epoch(state.get("last_completed_at")) if state.get("last_completed_at") else 0
print(
    f"1|{last_success}|{last_finished}|{outcome}|{zero_provider}|"
    f"{requested}|{completed}|{failed}",
    end="",
)
PY
}

crawler_cycle_partial_success() {
  local exit_status
  exit_status="$(systemctl show mooncen-crawler-once.service -p ExecMainStatus --value 2>/dev/null || true)"
  if [ "$exit_status" = "3" ] || [ "$crawler_cycle_outcome" = "partial_success" ]; then
    printf '1'
  else
    printf '0'
  fi
}

crawler_cycle_lock_contention() {
  local exit_status
  exit_status="$(systemctl show mooncen-crawler-once.service -p ExecMainStatus --value 2>/dev/null || true)"
  [ "$exit_status" = "75" ] && printf '1' || printf '0'
}

crawler_cycle_zero_provider() {
  local exit_status
  exit_status="$(systemctl show mooncen-crawler-once.service -p ExecMainStatus --value 2>/dev/null || true)"
  if [ "$exit_status" = "4" ] || [ "$crawler_cycle_zero_provider_state" = "1" ]; then
    printf '1'
  else
    printf '0'
  fi
}

cloud_db_ready() {
  local applier_env=/etc/mooncen/applier.env
  local env_mode env_owner host port
  if [ ! -f "$applier_env" ] || [ -L "$applier_env" ] || [ ! -r "$applier_env" ]; then
    printf '0'
    return
  fi
  env_owner="$(stat -c '%U' "$applier_env" 2>/dev/null || true)"
  env_mode="$(stat -c '%a' "$applier_env" 2>/dev/null || true)"
  if [ "$env_owner" != root ] || [[ ! "$env_mode" =~ ^[0-7]{3,4}$ ]] || (( (8#$env_mode & 8#022) != 0 )); then
    printf '0'
    return
  fi
  host="$(read_kv PRIMARY_DB_HOST "$applier_env")"
  port="$(read_kv PRIMARY_DB_PORT "$applier_env")"
  case "$host" in
    ''|-*|*[!A-Za-z0-9._:-]*) printf '0'; return ;;
  esac
  if [ "${#host}" -gt 253 ]; then
    printf '0'
    return
  fi
  case "$port" in
    ''|*[!0-9]*) printf '0'; return ;;
  esac
  if [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
    printf '0'
    return
  fi
  if command -v pg_isready >/dev/null 2>&1; then
    if pg_isready -h "$host" -p "$port" -t 3 >/dev/null 2>&1; then
      printf '1'
      return
    fi
  fi
  printf '0'
}

now="$(date +%s)"
deploy_info="$APP_DIR/.deploy-info"
deploy_epoch="$(read_kv DEPLOY_EPOCH "$deploy_info")"
deploy_epoch="${deploy_epoch:-0}"
domain="$(read_kv DOMAIN "$deploy_info")"
domain="${domain:-unknown}"
file_role="$(read_kv NODE_ROLE "$deploy_info")"
role_file="${ROLE_FILE:-/etc/mooncen-node-role}"
role="$file_role"
if [ -r "$role_file" ]; then
  role="$(tr -d '[:space:]' < "$role_file")"
fi
case "$role" in
  primary|standby|replica|backup|crawler|crawler-control|crawler-worker) ;;
  *) role="unknown" ;;
esac
if [ "$role" = "crawler" ]; then
  cycle_state_values="$(crawler_cycle_state_values)"
  IFS='|' read -r \
    crawler_cycle_state_valid \
    crawler_cycle_last_success_epoch \
    crawler_cycle_last_finished_epoch \
    crawler_cycle_outcome \
    crawler_cycle_zero_provider_state \
    crawler_cycle_providers_requested \
    crawler_cycle_providers_completed \
    crawler_cycle_providers_failed <<< "$cycle_state_values"
fi

{
  printf '# HELP mooncen_monitoring_collector_timestamp_seconds Last successful MoonCen textfile metric collection time.\n'
  printf '# TYPE mooncen_monitoring_collector_timestamp_seconds gauge\n'
  printf 'mooncen_monitoring_collector_timestamp_seconds %s\n' "$now"
  printf '# HELP mooncen_deploy_timestamp_seconds Last MoonCen application deploy time recorded on this node.\n'
  printf '# TYPE mooncen_deploy_timestamp_seconds gauge\n'
  printf 'mooncen_deploy_timestamp_seconds{role="%s",domain="%s"} %s\n' "$(escape_label "$role")" "$(escape_label "$domain")" "$deploy_epoch"
  printf '# HELP mooncen_node_role Node role from /etc/mooncen-node-role or deploy metadata. Current role is 1, others are 0.\n'
  printf '# TYPE mooncen_node_role gauge\n'
  for candidate in primary standby replica backup crawler crawler-control crawler-worker unknown; do
    if [ "$role" = "$candidate" ]; then value=1; else value=0; fi
    printf 'mooncen_node_role{role="%s"} %s\n' "$candidate" "$value"
  done
  printf '# HELP mooncen_postgres_in_recovery PostgreSQL recovery status, 1 standby, 0 primary, -1 unknown.\n'
  printf '# TYPE mooncen_postgres_in_recovery gauge\n'
  printf 'mooncen_postgres_in_recovery %s\n' "$(postgres_in_recovery)"
  printf '# HELP mooncen_cloud_db_ready Whether this node can reach the configured cloud/primary database.\n'
  printf '# TYPE mooncen_cloud_db_ready gauge\n'
  printf 'mooncen_cloud_db_ready %s\n' "$(cloud_db_ready)"
  if [ "$role" = "crawler" ]; then
    printf '# HELP mooncen_crawler_last_success_timestamp_seconds Last fully successful crawler cycle completion time.\n'
    printf '# TYPE mooncen_crawler_last_success_timestamp_seconds gauge\n'
    printf 'mooncen_crawler_last_success_timestamp_seconds %s\n' "$crawler_cycle_last_success_epoch"
    printf '# HELP mooncen_crawler_cycle_state_valid Whether bounded durable crawler cycle evidence is valid.\n'
    printf '# TYPE mooncen_crawler_cycle_state_valid gauge\n'
    printf 'mooncen_crawler_cycle_state_valid %s\n' "$crawler_cycle_state_valid"
    printf '# HELP mooncen_crawler_cycle_last_completion_timestamp_seconds Latest terminal crawler cycle completion time.\n'
    printf '# TYPE mooncen_crawler_cycle_last_completion_timestamp_seconds gauge\n'
    printf 'mooncen_crawler_cycle_last_completion_timestamp_seconds %s\n' "$crawler_cycle_last_finished_epoch"
    printf '# HELP mooncen_crawler_cycle_outcome Latest durable crawler cycle outcome.\n'
    printf '# TYPE mooncen_crawler_cycle_outcome gauge\n'
    for candidate in success partial_success failed zero_provider running unknown; do
      if [ "$crawler_cycle_outcome" = "$candidate" ]; then value=1; else value=0; fi
      printf 'mooncen_crawler_cycle_outcome{outcome="%s"} %s\n' "$candidate" "$value"
    done
    printf '# HELP mooncen_crawler_cycle_providers_requested Providers requested by the latest crawler cycle.\n'
    printf '# TYPE mooncen_crawler_cycle_providers_requested gauge\n'
    printf 'mooncen_crawler_cycle_providers_requested %s\n' "$crawler_cycle_providers_requested"
    printf '# HELP mooncen_crawler_cycle_providers_completed Providers successfully completed by the latest crawler cycle.\n'
    printf '# TYPE mooncen_crawler_cycle_providers_completed gauge\n'
    printf 'mooncen_crawler_cycle_providers_completed %s\n' "$crawler_cycle_providers_completed"
    printf '# HELP mooncen_crawler_cycle_providers_failed Providers failed by the latest crawler cycle.\n'
    printf '# TYPE mooncen_crawler_cycle_providers_failed gauge\n'
    printf 'mooncen_crawler_cycle_providers_failed %s\n' "$crawler_cycle_providers_failed"
    printf '# HELP mooncen_crawler_cycle_partial_success Whether the latest crawler one-shot completed with reviewed partial-success exit code 3.\n'
    printf '# TYPE mooncen_crawler_cycle_partial_success gauge\n'
    printf 'mooncen_crawler_cycle_partial_success %s\n' "$(crawler_cycle_partial_success)"
    printf '# HELP mooncen_crawler_cycle_skipped_lock_contention Whether the latest crawler one-shot exited 75 for a confirmed active duplicate.\n'
    printf '# TYPE mooncen_crawler_cycle_skipped_lock_contention gauge\n'
    printf 'mooncen_crawler_cycle_skipped_lock_contention %s\n' "$(crawler_cycle_lock_contention)"
    printf '# HELP mooncen_crawler_cycle_zero_provider Whether the latest cycle produced no successful provider evidence.\n'
    printf '# TYPE mooncen_crawler_cycle_zero_provider gauge\n'
    printf 'mooncen_crawler_cycle_zero_provider %s\n' "$(crawler_cycle_zero_provider)"
  fi
  printf '# HELP mooncen_systemd_unit_active Whether a MoonCen related systemd unit is active.\n'
  printf '# TYPE mooncen_systemd_unit_active gauge\n'
  printf '# HELP mooncen_systemd_unit_enabled Whether a MoonCen related systemd unit is enabled/static/generated.\n'
  printf '# TYPE mooncen_systemd_unit_enabled gauge\n'
  printf '# HELP mooncen_systemd_unit_result_failed Whether a MoonCen related systemd unit reports failed/non-success result.\n'
  printf '# TYPE mooncen_systemd_unit_result_failed gauge\n'
  for unit in \
    postgresql.service \
    mooncen-api.service \
    mooncen-frontend.service \
    mooncen-ai-worker.service \
    mooncen-cloudflare-gate.timer \
    mooncen-functional-test.timer \
    mooncen-backup.timer \
    mooncen-ops-bot.service \
    cloudflared.service \
    prometheus-node-exporter.service; do
    escaped_unit="$(escape_label "$unit")"
    printf 'mooncen_systemd_unit_active{unit="%s"} %s\n' "$escaped_unit" "$(service_active "$unit")"
    printf 'mooncen_systemd_unit_enabled{unit="%s"} %s\n' "$escaped_unit" "$(service_enabled "$unit")"
    printf 'mooncen_systemd_unit_result_failed{unit="%s"} %s\n' "$escaped_unit" "$(service_result_failed "$unit")"
  done
  if [ "$role" = "crawler" ]; then
    for unit in \
      mooncen-crawler.service \
      mooncen-crawler.timer \
      mooncen-crawler-once.service \
      mooncen-staging-apply.timer \
      mooncen-staging-apply.service; do
      escaped_unit="$(escape_label "$unit")"
      printf 'mooncen_systemd_unit_active{unit="%s"} %s\n' "$escaped_unit" "$(service_active "$unit")"
      printf 'mooncen_systemd_unit_enabled{unit="%s"} %s\n' "$escaped_unit" "$(service_enabled "$unit")"
      printf 'mooncen_systemd_unit_result_failed{unit="%s"} %s\n' "$escaped_unit" "$(service_result_failed "$unit")"
    done
  fi
  if [ "$role" = "crawler-control" ]; then
    for unit in \
      mooncen-crawler-control-scheduler.service \
      mooncen-crawler-control-finalizer.service \
      mooncen-crawler-release-publisher.timer \
      mooncen-crawler-release-publisher.service \
      mooncen-crawler-control-metrics.timer \
      mooncen-crawler-control-metrics.service \
      mooncen-staging-apply.timer \
      mooncen-staging-apply.service; do
      escaped_unit="$(escape_label "$unit")"
      printf 'mooncen_systemd_unit_active{unit="%s"} %s\n' "$escaped_unit" "$(service_active "$unit")"
      printf 'mooncen_systemd_unit_enabled{unit="%s"} %s\n' "$escaped_unit" "$(service_enabled "$unit")"
      printf 'mooncen_systemd_unit_result_failed{unit="%s"} %s\n' "$escaped_unit" "$(service_result_failed "$unit")"
    done
  fi
  if [ "$role" = "crawler-worker" ]; then
    # Desired fleet state remains pending. Export only the dormant distributed
    # worker units; never mix legacy scheduler/applier evidence into this role.
    for unit in \
      mooncen-crawler-pull-worker.service \
      mooncen-crawler-release-agent.service \
      mooncen-crawler-release-agent.timer \
      mooncen-crawler-release-reporter.service \
      mooncen-crawler-release-reporter.timer; do
      escaped_unit="$(escape_label "$unit")"
      printf 'mooncen_systemd_unit_active{unit="%s"} %s\n' "$escaped_unit" "$(service_active "$unit")"
      printf 'mooncen_systemd_unit_enabled{unit="%s"} %s\n' "$escaped_unit" "$(service_enabled "$unit")"
      printf 'mooncen_systemd_unit_result_failed{unit="%s"} %s\n' "$escaped_unit" "$(service_result_failed "$unit")"
    done
  fi
} > "$TMP_FILE"

chmod 644 "$TMP_FILE"
mv "$TMP_FILE" "$OUT_FILE"
