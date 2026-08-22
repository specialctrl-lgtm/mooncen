#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$SCRIPT_DIR/backup_ssh_common.sh"
backup_load_runtime_environment

APP_DIR="${APP_DIR:-/opt/mooncen}"
BACKUP_HOST="${BACKUP_HOST:-wtr-nas}"
BACKUP_USER="${BACKUP_USER:-mooncen_backup}"
BACKUP_ROOT="${BACKUP_ROOT:-/volume2/homes/mooncen_backup/mooncen-backup}"
SERVER_NAME="${SERVER_NAME:-$(hostname -s 2>/dev/null || hostname)}"
BACKUP_FILE="${BACKUP_FILE:-latest}"
RESTORE_CONFIRM="${RESTORE_CONFIRM:-}"
RESTORE_MIN_COURSES="${RESTORE_MIN_COURSES:-1}"
RESTORE_MIN_BRANCHES="${RESTORE_MIN_BRANCHES:-1}"
RESTORE_HEALTH_ATTEMPTS="${RESTORE_HEALTH_ATTEMPTS:-30}"
RESTORE_HEALTH_INTERVAL_SECONDS="${RESTORE_HEALTH_INTERVAL_SECONDS:-2}"
ALLOW_STALE_SIGNED_BACKUP="${ALLOW_STALE_SIGNED_BACKUP:-0}"
WORK_DIR="${WORK_DIR:-}"
REMOTE_DIR="$BACKUP_ROOT/$SERVER_NAME"
SERVICES_STOPPED=0
RESTORE_SUCCEEDED=0
BACKUP_CANDIDATE_CREATED=0
SWAP_ACTIVE=0
SWAP_ROLLED_BACK=0
API_WAS_ACTIVE=0
HEALTH_VERIFIED=0
PROBE_API_STARTED=0
DATABASE_COMMITTED=0
RESTORE_DEPLOYMENT_EXCLUSION_ACQUIRED=0
RESTORE_STAGE_DB=""
RESTORE_OLD_DB=""
DEPLOYMENT_LOCK_DIR=/opt/.mooncen-deploy.lock
RESTORE_DEPLOYMENT_OWNER="live-restore:$$"
MANAGED_UNITS=(
  mooncen-api.service
  mooncen-frontend.service
  mooncen-crawler.service
  mooncen-ai-worker.service
  mooncen-container-stack.service
  mooncen-container-release-guard@.service
  mooncen-crawler-once.service
  mooncen-crawler-browser-smoke.service
  mooncen-crawler-control-finalizer.service
  mooncen-crawler-control-metrics.service
  mooncen-crawler-control-scheduler.service
  mooncen-crawler-pull-worker.service
  mooncen-crawler-release-action-worker.service
  mooncen-crawler-release-agent.service
  mooncen-crawler-release-publisher.service
  mooncen-crawler-release-reporter.service
  mooncen-branch-coordinates.service
  mooncen-functional-test.service
  mooncen-staging-apply.service
  mooncen-staging-apply-dry-run.service
  mooncen-staging-apply@.service
  mooncen-staging-apply-dry-run@.service
  mooncen-ops-bot.service
  mooncen-backup.service
  mooncen-backup-restore-test.service
  mooncen-cloudflare-gate.service
  mooncen-cloudflared-role-guard.service
  mooncen-crawler-watchdog.service
  mooncen-node-metrics.service
  mooncen-crawler.timer
  mooncen-crawler-control-metrics.timer
  mooncen-crawler-release-agent.timer
  mooncen-crawler-release-publisher.timer
  mooncen-crawler-release-reporter.timer
  mooncen-backup.timer
  mooncen-backup-restore-test.timer
  mooncen-functional-test.timer
  mooncen-staging-apply.timer
  mooncen-crawler-watchdog.timer
  mooncen-cloudflare-gate.timer
  mooncen-cloudflared-role-guard.timer
  mooncen-node-metrics.timer
  mooncen-deploy-guard@.service
  nginx.service
  cloudflared.service
)
ACTIVE_UNITS=()
ACTIVE_DAEMONS=()
ACTIVE_INGRESS=()
ACTIVE_TIMERS=()
DEFERRED_LOCK_TIMERS=()
INTERRUPTED_ONESHOTS=()
PRESENT_UNITS=()

if [ "$RESTORE_CONFIRM" != "RESTORE_MOONCEN" ]; then
  echo "Refusing restore. Set RESTORE_CONFIRM=RESTORE_MOONCEN." >&2
  exit 2
fi
if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  echo "Production restore must run as root." >&2
  exit 77
fi

if [ "${BACKUP_PROFILE:-}" = "wtr-nas" ] || [ "${BACKUP_HOST:-}" = "wtr-nas" ]; then
  BACKUP_HOST=wtr-nas
  BACKUP_USER="${BACKUP_USER:-mooncen_backup}"
  if [ -z "${BACKUP_ROOT:-}" ] || [ "$BACKUP_ROOT" = "/volume1/mooncen-backup" ]; then
    BACKUP_ROOT=/volume2/homes/mooncen_backup/mooncen-backup
  fi
  BACKUP_IDENTITY_FILE=/etc/mooncen/backup-ssh-key
fi

REMOTE_DIR="$BACKUP_ROOT/$SERVER_NAME"

backup_acquire_operation_lock
backup_prepare_ssh
backup_validate_remote_path "$REMOTE_DIR"

DB_NAME="${DB_NAME:-mooncen}"
DB_USER="${DB_OWNER_USER:-${DB_USER:-mooncen_admin}}"
BACKUP_AGE_IDENTITY_FILE="${BACKUP_AGE_IDENTITY_FILE:-/etc/mooncen/backup-age-key.txt}"

if [ -z "$WORK_DIR" ]; then
  WORK_DIR="$(mktemp -d /tmp/mooncen-restore.XXXXXX)"
else
  if [ ! -d "$WORK_DIR" ] || [ -L "$WORK_DIR" ] || \
     [ "$(stat -c '%u:%a' -- "$WORK_DIR" 2>/dev/null || true)" != "0:700" ] || \
     [ -n "$(find "$WORK_DIR" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
    echo "Restore WORK_DIR override must be an empty root-owned non-symlink directory with mode 0700." >&2
    exit 64
  fi
fi
WORK_DIR="$(cd "$WORK_DIR" && pwd -P)"
case "$WORK_DIR" in
  /tmp/mooncen-restore.*|/tmp/mooncen-restore-*) ;;
  *) echo "Unsafe WORK_DIR for restore: $WORK_DIR" >&2; exit 64 ;;
esac

database_exists() {
  local database_name="$1"
  sudo -n -u postgres psql -v ON_ERROR_STOP=1 -d postgres -Atqc \
    "SELECT 1 FROM pg_database WHERE datname = '$database_name';" | grep -qx 1
}

assert_no_active_deployment_guard() {
  local guard_inventory guard_unit guard_active_state guard_main_pid

  if ! guard_inventory="$(
    systemctl list-units --all --plain --no-legend \
      'mooncen-deploy-guard@*.service'
  )"; then
    echo "Unable to inspect deployment recovery guard instances." >&2
    return 1
  fi
  while IFS= read -r guard_unit; do
    guard_unit="${guard_unit%%[[:space:]]*}"
    [ -n "$guard_unit" ] || continue
    [ "$guard_unit" != "mooncen-deploy-guard@.service" ] || continue
    if [[ ! "$guard_unit" =~ ^mooncen-deploy-guard@[0-9a-f]{32}\.service$ ]]; then
      echo "Unexpected deployment recovery guard instance: $guard_unit" >&2
      return 1
    fi
    guard_active_state="$(systemctl show --property=ActiveState --value "$guard_unit")"
    guard_main_pid="$(systemctl show --property=MainPID --value "$guard_unit")"
    if [ "$guard_active_state" != "inactive" ] || [ "$guard_main_pid" != "0" ]; then
      echo "Live restore is blocked by deployment recovery guard: $guard_unit" >&2
      return 1
    fi
  done <<< "$guard_inventory"
}

acquire_restore_deployment_exclusion() {
  local marker="$DEPLOYMENT_LOCK_DIR/restore-owner"

  assert_no_active_deployment_guard || return 1
  if [ -e "$DEPLOYMENT_LOCK_DIR" ] || [ -L "$DEPLOYMENT_LOCK_DIR" ]; then
    echo "Live restore is blocked by a deployment or pending deployment recovery." >&2
    return 1
  fi
  if ! mkdir -- "$DEPLOYMENT_LOCK_DIR"; then
    echo "Unable to acquire live-restore deployment exclusion." >&2
    return 1
  fi
  if ! chmod 0700 "$DEPLOYMENT_LOCK_DIR" ||
     ! printf '%s\n' "$RESTORE_DEPLOYMENT_OWNER" > "$marker" ||
     ! chmod 0600 "$marker"; then
    rm -f -- "$marker"
    rmdir -- "$DEPLOYMENT_LOCK_DIR" >/dev/null 2>&1 || true
    echo "Unable to initialize live-restore deployment exclusion." >&2
    return 1
  fi
  if ! assert_no_active_deployment_guard; then
    rm -f -- "$marker"
    rmdir -- "$DEPLOYMENT_LOCK_DIR" >/dev/null 2>&1 || true
    echo "Deployment recovery guard raced with live-restore exclusion." >&2
    return 1
  fi
  RESTORE_DEPLOYMENT_EXCLUSION_ACQUIRED=1
}

release_restore_deployment_exclusion() {
  local marker="$DEPLOYMENT_LOCK_DIR/restore-owner"

  [ "$RESTORE_DEPLOYMENT_EXCLUSION_ACQUIRED" = "1" ] || return 0
  if [ "$DEPLOYMENT_LOCK_DIR" != "/opt/.mooncen-deploy.lock" ] ||
     [ ! -d "$DEPLOYMENT_LOCK_DIR" ] || [ -L "$DEPLOYMENT_LOCK_DIR" ] ||
     [ "$(stat -c '%U:%G:%a' "$DEPLOYMENT_LOCK_DIR" 2>/dev/null || true)" != "root:root:700" ] ||
     [ ! -f "$marker" ] || [ -L "$marker" ] ||
     [ "$(stat -c '%U:%G:%a' "$marker" 2>/dev/null || true)" != "root:root:600" ] ||
     [ "$(cat "$marker" 2>/dev/null || true)" != "$RESTORE_DEPLOYMENT_OWNER" ]; then
    echo "Live-restore deployment exclusion changed unexpectedly; preserving it for review." >&2
    return 1
  fi
  rm -f -- "$marker"
  if ! rmdir -- "$DEPLOYMENT_LOCK_DIR"; then
    echo "Live-restore deployment exclusion contains unexpected state; preserving it for review." >&2
    return 1
  fi
  RESTORE_DEPLOYMENT_EXCLUSION_ACQUIRED=0
}

assert_units_quiesced() {
  local allowed_unit="${1:-}"
  local unit unit_active_state unit_main_pid

  for unit in "${PRESENT_UNITS[@]}"; do
    if [ -n "$allowed_unit" ] && [ "$unit" = "$allowed_unit" ]; then
      continue
    fi
    unit_active_state="$(systemctl show --property=ActiveState --value "$unit")"
    unit_main_pid="$(systemctl show --property=MainPID --value "$unit")"
    if [ "$unit_active_state" != "inactive" ] || [ "$unit_main_pid" != "0" ]; then
      echo "Restore-sensitive unit is not fully quiesced: $unit state=$unit_active_state pid=$unit_main_pid" >&2
      return 1
    fi
  done
}

quiesce_present_units() {
  if [ "${#PRESENT_UNITS[@]}" -gt 0 ]; then
    sudo -n systemctl stop "${PRESENT_UNITS[@]}"
    sudo -n systemctl reset-failed "${PRESENT_UNITS[@]}" >/dev/null 2>&1 || true
  fi
  assert_units_quiesced
}

fence_promoted_database_for_probe() {
  sudo -n -u postgres psql -X -v ON_ERROR_STOP=1 -d postgres <<SQL
REVOKE CONNECT ON DATABASE "$DB_NAME"
  FROM mooncen_api, mooncen_crawler, mooncen_applier, mooncen_ai, mooncen_check, mooncen_readonly;
SQL
}

enable_internal_api_probe_connect() {
  sudo -n -u postgres psql -X -v ON_ERROR_STOP=1 -d postgres -c \
    "GRANT CONNECT ON DATABASE \"$DB_NAME\" TO mooncen_api;"
}

restore_runtime_database_connect() {
  sudo -n -u postgres psql -X -v ON_ERROR_STOP=1 -d postgres <<SQL
GRANT CONNECT ON DATABASE "$DB_NAME"
  TO mooncen_api, mooncen_crawler, mooncen_applier, mooncen_ai, mooncen_check, mooncen_readonly;
SQL
}

verify_promoted_database_contract() {
  local contract_ok api_role_select_ok

  contract_ok="$(sudo -n -u postgres psql -X -v ON_ERROR_STOP=1 -d "$DB_NAME" -Atqc "
    SELECT current_database() = '$DB_NAME'
      AND NOT pg_is_in_recovery()
      AND to_regclass('public.courses') IS NOT NULL
      AND to_regclass('public.branches') IS NOT NULL
      AND to_regclass('public.mooncen_schema_migrations') IS NOT NULL
      AND (SELECT COUNT(*) FROM public.courses) = $BACKUP_RESTORED_COURSE_COUNT
      AND (SELECT COUNT(*) FROM public.branches) = $BACKUP_RESTORED_BRANCH_COUNT
      AND NOT has_database_privilege('mooncen_api', current_database(), 'CONNECT')
      AND NOT has_database_privilege('mooncen_crawler', current_database(), 'CONNECT')
      AND NOT has_database_privilege('mooncen_applier', current_database(), 'CONNECT')
      AND NOT has_database_privilege('mooncen_ai', current_database(), 'CONNECT')
      AND NOT has_database_privilege('mooncen_check', current_database(), 'CONNECT')
      AND NOT has_database_privilege('mooncen_readonly', current_database(), 'CONNECT')
      AND has_table_privilege('mooncen_api', 'public.courses', 'SELECT')
      AND NOT has_table_privilege('mooncen_api', 'public.courses', 'DELETE');
  ")"
  if [ "$contract_ok" != "t" ]; then
    echo "Promoted database failed the post-swap contract probe." >&2
    return 1
  fi
  api_role_select_ok="$(sudo -n -u postgres psql -X -v ON_ERROR_STOP=1 -d "$DB_NAME" -Atqc "
    SET ROLE mooncen_api;
    SELECT EXISTS (SELECT 1 FROM public.courses LIMIT 1);
  " | tail -n 1)"
  if [ "$api_role_select_ok" != "t" ]; then
    echo "Promoted database failed the mooncen_api role read probe." >&2
    return 1
  fi
}

stop_internal_api_probe() {
  if [ "$PROBE_API_STARTED" = "1" ] || systemctl is-active --quiet mooncen-api.service; then
    sudo -n systemctl stop mooncen-api.service
  fi
  PROBE_API_STARTED=0
  assert_units_quiesced
}

run_internal_api_probe() {
  local health_ready=0 health_payload="" listener_lines

  command -v ss >/dev/null 2>&1 || {
    echo "ss from iproute2 is required to verify the internal API listener." >&2
    return 1
  }
  assert_units_quiesced
  if ss -H -ltn 'sport = :8001' | grep -q .; then
    echo "Port 8001 is already occupied before the isolated API probe." >&2
    return 1
  fi
  sudo -n systemctl start mooncen-api.service
  PROBE_API_STARTED=1
  assert_units_quiesced mooncen-api.service
  for ((health_attempt = 1; health_attempt <= RESTORE_HEALTH_ATTEMPTS; health_attempt++)); do
    health_payload="$(curl --fail --silent --show-error --max-time 5 http://127.0.0.1:8001/health 2>/dev/null || true)"
    if printf '%s' "$health_payload" | grep -Eq '"status"[[:space:]]*:[[:space:]]*"ready"'; then
      health_ready=1
      break
    fi
    sleep "$RESTORE_HEALTH_INTERVAL_SECONDS"
  done
  if [ "$health_ready" != "1" ] || ! systemctl is-active --quiet mooncen-api.service; then
    echo "Restored API did not pass its isolated database-backed health check." >&2
    return 1
  fi
  listener_lines="$(ss -H -ltn 'sport = :8001')"
  if [ -z "$listener_lines" ] || ! printf '%s\n' "$listener_lines" | awk '
    NF && $4 !~ /^(127[.]0[.]0[.]1:8001|\[::1\]:8001|::1:8001)$/ {bad=1}
    END {exit bad}
  '; then
    echo "API health probe listener is not restricted to loopback." >&2
    return 1
  fi
  stop_internal_api_probe
}

wait_for_resumed_active_unit() {
  local unit="$1"
  local attempt active_state

  for ((attempt = 1; attempt <= RESTORE_HEALTH_ATTEMPTS; attempt++)); do
    active_state="$(systemctl show --property=ActiveState --value "$unit")"
    if [ "$active_state" = "active" ]; then
      return 0
    fi
    if [ "$active_state" = "failed" ]; then
      return 1
    fi
    sleep "$RESTORE_HEALTH_INTERVAL_SECONDS"
  done
  return 1
}

resume_active_units() {
  local unit

  for unit in "${ACTIVE_DAEMONS[@]}"; do
    sudo -n systemctl start --no-block "$unit"
  done
  for unit in "${ACTIVE_DAEMONS[@]}"; do
    wait_for_resumed_active_unit "$unit" || {
      echo "Previously active daemon failed to resume: $unit" >&2
      return 1
    }
  done
  for unit in "${ACTIVE_INGRESS[@]}"; do
    sudo -n systemctl start --no-block "$unit"
  done
  for unit in "${ACTIVE_INGRESS[@]}"; do
    wait_for_resumed_active_unit "$unit" || {
      echo "Previously active ingress failed to resume: $unit" >&2
      return 1
    }
  done
  for unit in "${INTERRUPTED_ONESHOTS[@]}"; do
    sudo -n systemctl start --no-block "$unit" || {
      echo "Interrupted oneshot could not be requeued: $unit" >&2
      return 1
    }
  done
  for unit in "${ACTIVE_TIMERS[@]}"; do
    sudo -n systemctl start --no-block "$unit"
  done
  for unit in "${ACTIVE_TIMERS[@]}"; do
    wait_for_resumed_active_unit "$unit" || {
      echo "Previously active timer failed to resume: $unit" >&2
      return 1
    }
  done
}

rollback_database_swap() {
  local production_exists=0
  local previous_exists=0
  local candidate_exists=0

  if [ -z "$RESTORE_STAGE_DB" ] || [ -z "$RESTORE_OLD_DB" ]; then
    return 0
  fi
  database_exists "$DB_NAME" && production_exists=1
  database_exists "$RESTORE_OLD_DB" && previous_exists=1
  database_exists "$RESTORE_STAGE_DB" && candidate_exists=1
  if [ "$previous_exists" != "1" ]; then
    if [ "$SWAP_ACTIVE" = "1" ] && [ "$production_exists" = "1" ] && [ "$candidate_exists" = "1" ]; then
      backup_set_database_connections "$DB_NAME" true || return 1
      SWAP_ACTIVE=0
    fi
    return 0
  fi
  if [ "$production_exists" = "1" ] && [ "$candidate_exists" = "0" ]; then
    backup_set_database_connections "$DB_NAME" false || return 1
    backup_terminate_database_sessions "$DB_NAME" || return 1
    if ! sudo -n -u postgres psql -v ON_ERROR_STOP=1 -d postgres -c \
      "ALTER DATABASE \"$DB_NAME\" RENAME TO \"$RESTORE_STAGE_DB\";"; then
      echo "Unable to move the failed restore candidate out of the production database name." >&2
      return 1
    fi
    production_exists=0
    candidate_exists=1
  fi
  if [ "$production_exists" = "1" ] || [ "$candidate_exists" != "1" ]; then
    echo "Automatic database rollback preconditions failed." >&2
    echo "production=$production_exists previous=$previous_exists candidate=$candidate_exists" >&2
    return 1
  fi
  backup_terminate_database_sessions "$RESTORE_OLD_DB" || return 1
  if ! sudo -n -u postgres psql -v ON_ERROR_STOP=1 -d postgres -c \
    "ALTER DATABASE \"$RESTORE_OLD_DB\" RENAME TO \"$DB_NAME\";"; then
    echo "Automatic database rollback could not restore the previous production name." >&2
    return 1
  fi
  backup_set_database_connections "$DB_NAME" true || return 1
  SWAP_ACTIVE=0
  SWAP_ROLLED_BACK=1
  BACKUP_CANDIDATE_CREATED=1
  echo "database_swap_rolled_back=1" >&2
}

cleanup() {
  status=$?
  trap - EXIT
  trap '' HUP INT TERM
  set +e
  if [ "$SWAP_ACTIVE" = "1" ] && [ -n "$RESTORE_OLD_DB" ] && \
     ! database_exists "$RESTORE_OLD_DB" && database_exists "$DB_NAME" && \
     { [ -z "$RESTORE_STAGE_DB" ] || ! database_exists "$RESTORE_STAGE_DB"; }; then
    SWAP_ACTIVE=0
    DATABASE_COMMITTED=1
  fi
  if [ "$DATABASE_COMMITTED" = "1" ] && [ "$RESTORE_SUCCEEDED" != "1" ]; then
    if [ "${#PRESENT_UNITS[@]}" -gt 0 ]; then
      sudo -n systemctl stop "${PRESENT_UNITS[@]}" >/dev/null 2>&1 || true
    fi
    echo "restore_committed_service_resume_failed=1" >&2
    echo "The restored database is committed; rollback is no longer possible. Managed services remain stopped." >&2
  elif [ "$SERVICES_STOPPED" = "1" ] && [ "$RESTORE_SUCCEEDED" != "1" ]; then
    if [ "${#PRESENT_UNITS[@]}" -gt 0 ]; then
      sudo -n systemctl stop "${PRESENT_UNITS[@]}" >/dev/null 2>&1 || true
    fi
    if [ "$SWAP_ACTIVE" = "1" ]; then
      rollback_database_swap || true
    fi
    if [ "$SWAP_ACTIVE" = "0" ] && [ "$BACKUP_CANDIDATE_CREATED" = "1" ] && \
       [ -n "$RESTORE_STAGE_DB" ] && database_exists "$RESTORE_STAGE_DB"; then
      backup_set_database_connections "$RESTORE_STAGE_DB" false >/dev/null 2>&1 || true
      backup_terminate_database_sessions "$RESTORE_STAGE_DB" >/dev/null 2>&1 || true
      sudo -n -u postgres dropdb --force --if-exists "$RESTORE_STAGE_DB" >/dev/null 2>&1 || \
        echo "Unable to remove failed restore candidate database: $RESTORE_STAGE_DB" >&2
    fi
    echo "restore_failed_services_remain_stopped=1" >&2
    echo "Review the restore error and database state before manually restarting MoonCen." >&2
  elif [ "$RESTORE_SUCCEEDED" != "1" ] && [ "$BACKUP_CANDIDATE_CREATED" = "1" ] && \
       [ -n "$RESTORE_STAGE_DB" ] && database_exists "$RESTORE_STAGE_DB"; then
    backup_set_database_connections "$RESTORE_STAGE_DB" false >/dev/null 2>&1 || true
    backup_terminate_database_sessions "$RESTORE_STAGE_DB" >/dev/null 2>&1 || true
    sudo -n -u postgres dropdb --force --if-exists "$RESTORE_STAGE_DB" >/dev/null 2>&1 || \
      echo "Unable to remove failed restore candidate database: $RESTORE_STAGE_DB" >&2
  fi
  rm -rf -- "$WORK_DIR"
  if ! release_restore_deployment_exclusion && [ "$status" -eq 0 ]; then
    status=74
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

if ! acquire_restore_deployment_exclusion; then
  exit 75
fi

if [[ ! "$DB_NAME" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]] || [[ ! "$DB_USER" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]]; then
  echo "Invalid DB_NAME or DB_USER identifier." >&2
  exit 64
fi
if [ "${#DB_NAME}" -gt 40 ]; then
  echo "DB_NAME must be at most 40 characters to leave room for collision-safe restore names." >&2
  exit 64
fi
if [ "${#DB_USER}" -gt 63 ]; then
  echo "DB_USER must be at most 63 characters." >&2
  exit 64
fi
for threshold_name in \
  RESTORE_MIN_COURSES RESTORE_MIN_BRANCHES RESTORE_HEALTH_ATTEMPTS RESTORE_HEALTH_INTERVAL_SECONDS; do
  threshold_value="${!threshold_name}"
  if [[ ! "$threshold_value" =~ ^[0-9]+$ ]]; then
    echo "$threshold_name must be a non-negative integer." >&2
    exit 64
  fi
done
if [ "$RESTORE_HEALTH_ATTEMPTS" -lt 1 ] || [ "$RESTORE_HEALTH_ATTEMPTS" -gt 120 ] || \
   [ "$RESTORE_HEALTH_INTERVAL_SECONDS" -lt 1 ] || [ "$RESTORE_HEALTH_INTERVAL_SECONDS" -gt 30 ]; then
  echo "Restore health retry policy is outside the allowed bounds." >&2
  exit 64
fi
if [ "$RESTORE_MIN_COURSES" -gt 1000000000 ] || [ "$RESTORE_MIN_BRANCHES" -gt 1000000000 ]; then
  echo "Restore row-count minimums must not exceed 1000000000." >&2
  exit 64
fi
if [ "$ALLOW_STALE_SIGNED_BACKUP" != "0" ] && [ "$ALLOW_STALE_SIGNED_BACKUP" != "1" ]; then
  echo "ALLOW_STALE_SIGNED_BACKUP must be 0 or 1." >&2
  exit 64
fi
command -v openssl >/dev/null 2>&1 || { echo "openssl is required for collision-safe restore database names." >&2; exit 69; }
restore_token="$(openssl rand -hex 4)"
if [[ ! "$restore_token" =~ ^[0-9a-f]{8}$ ]]; then
  echo "Unable to generate a safe restore database token." >&2
  exit 70
fi
RESTORE_STAGE_DB="${DB_NAME}_rnew_${restore_token}"
RESTORE_OLD_DB="${DB_NAME}_rold_${restore_token}"
if database_exists "$RESTORE_STAGE_DB" || database_exists "$RESTORE_OLD_DB"; then
  echo "Collision detected for generated restore database names; retry the restore." >&2
  exit 73
fi

if [ "$BACKUP_FILE" = "latest" ]; then
  BACKUP_FILE="$(backup_select_latest_committed_dump "$REMOTE_DIR" "$DB_NAME")"
fi

if [ -z "$BACKUP_FILE" ]; then
  echo "No backup dump found in $REMOTE_DIR/db" >&2
  exit 1
fi

LOCAL_DUMP="$WORK_DIR/restore.dump"
echo "restore_backup=$BACKUP_FILE"
backup_restore_fetch_verified_dump \
  "$REMOTE_DIR" "$BACKUP_FILE" "$WORK_DIR" "$BACKUP_AGE_IDENTITY_FILE" "$LOCAL_DUMP" \
  "$SERVER_NAME" "$DB_NAME" "$ALLOW_STALE_SIGNED_BACKUP"
chown root:postgres "$WORK_DIR" "$LOCAL_DUMP"
chmod 0750 "$WORK_DIR"
chmod 0640 "$LOCAL_DUMP"
backup_preflight_postgres_restore "$LOCAL_DUMP" "$BACKUP_VERIFIED_DB_SIZE_BYTES"

echo "restoring_and_validating_candidate=$RESTORE_STAGE_DB"
backup_restore_database_candidate \
  "$LOCAL_DUMP" "$RESTORE_STAGE_DB" "$DB_USER" "$APP_DIR" \
  "$RESTORE_MIN_COURSES" "$RESTORE_MIN_BRANCHES"
printf 'courses\t%s\nbranches\t%s\nlatest_course_update\t%s\n' \
  "$BACKUP_RESTORED_COURSE_COUNT" "$BACKUP_RESTORED_BRANCH_COUNT" "$BACKUP_RESTORED_LATEST_UPDATE"

if ! database_exists "$DB_NAME"; then
  echo "Production database does not exist; refusing a rename swap: $DB_NAME" >&2
  exit 73
fi

echo "stopping_application_services"
for unit in "${MANAGED_UNITS[@]}"; do
  # Installed guard templates are inventory, not stoppable units. Active
  # deployment guard instances and the shared native deployment lock were
  # rejected before any candidate database or application service changed.
  if [ "$unit" = "mooncen-deploy-guard@.service" ] ||
     [ "$unit" = "mooncen-container-release-guard@.service" ]; then
    continue
  fi
  if systemctl cat "$unit" >/dev/null 2>&1; then
    PRESENT_UNITS+=("$unit")
    unit_active_state="$(systemctl show --property=ActiveState --value "$unit")"
    case "$unit_active_state" in
      active|activating|reloading)
        ACTIVE_UNITS+=("$unit")
        if [ "$unit" = "mooncen-api.service" ]; then
          API_WAS_ACTIVE=1
        fi
        case "$unit" in
          mooncen-backup.timer|mooncen-backup-restore-test.timer)
            DEFERRED_LOCK_TIMERS+=("$unit")
            ;;
          *.timer) ACTIVE_TIMERS+=("$unit") ;;
          nginx.service|cloudflared.service) ACTIVE_INGRESS+=("$unit") ;;
          *.service)
            unit_type="$(systemctl show --property=Type --value "$unit")"
            if [ "$unit_type" = "oneshot" ]; then
              INTERRUPTED_ONESHOTS+=("$unit")
            else
              ACTIVE_DAEMONS+=("$unit")
            fi
            ;;
        esac
        ;;
    esac
  fi
done
classified_active_count=$((${#ACTIVE_DAEMONS[@]} + ${#ACTIVE_INGRESS[@]} + ${#ACTIVE_TIMERS[@]} + ${#DEFERRED_LOCK_TIMERS[@]} + ${#INTERRUPTED_ONESHOTS[@]}))
if [ "$classified_active_count" -ne "${#ACTIVE_UNITS[@]}" ]; then
  echo "Unable to classify every originally active restore-sensitive unit." >&2
  exit 70
fi
SERVICES_STOPPED=1
quiesce_present_units

echo "swapping_database_candidate=$RESTORE_STAGE_DB"
SWAP_ACTIVE=1
backup_set_database_connections "$DB_NAME" false
backup_terminate_database_sessions "$DB_NAME"
backup_set_database_connections "$RESTORE_STAGE_DB" false
backup_terminate_database_sessions "$RESTORE_STAGE_DB"
sudo -n -u postgres psql -v ON_ERROR_STOP=1 -d postgres -c \
  "ALTER DATABASE \"$DB_NAME\" RENAME TO \"$RESTORE_OLD_DB\";"
if ! sudo -n -u postgres psql -v ON_ERROR_STOP=1 -d postgres -c \
  "ALTER DATABASE \"$RESTORE_STAGE_DB\" RENAME TO \"$DB_NAME\";"; then
  echo "Candidate rename failed after preserving the previous database; rolling back." >&2
  rollback_database_swap || true
  exit 73
fi
BACKUP_CANDIDATE_CREATED=0
fence_promoted_database_for_probe
backup_set_database_connections "$DB_NAME" true

echo "verifying_promoted_database"
verify_promoted_database_contract
if [ "$API_WAS_ACTIVE" = "1" ]; then
  echo "probing_internal_api_without_ingress"
  enable_internal_api_probe_connect
  run_internal_api_probe
  fence_promoted_database_for_probe
fi
HEALTH_VERIFIED=1

if [ "$HEALTH_VERIFIED" != "1" ]; then
  echo "Refusing to commit a restore that has not passed every health probe." >&2
  exit 71
fi
backup_set_database_connections "$DB_NAME" false
backup_terminate_database_sessions "$DB_NAME"
assert_units_quiesced
echo "dropping_previous_database=$RESTORE_OLD_DB"
backup_set_database_connections "$RESTORE_OLD_DB" false
backup_terminate_database_sessions "$RESTORE_OLD_DB"
sudo -n -u postgres dropdb --force "$RESTORE_OLD_DB"
DATABASE_COMMITTED=1
SWAP_ACTIVE=0

restore_runtime_database_connect
backup_set_database_connections "$DB_NAME" true

echo "resuming_original_application_units"
if ! resume_active_units; then
  echo "Restored database committed, but one or more previously active units failed to resume." >&2
  exit 71
fi
if [ "${#DEFERRED_LOCK_TIMERS[@]}" -gt 0 ]; then
  echo "releasing_restore_lock_before_backup_timers"
  exec 9>&-
  for unit in "${DEFERRED_LOCK_TIMERS[@]}"; do
    sudo -n systemctl start "$unit"
    wait_for_resumed_active_unit "$unit" || {
      echo "Previously active backup timer failed to resume after lock release: $unit" >&2
      exit 71
    }
  done
fi
RESTORE_SUCCEEDED=1
SERVICES_STOPPED=0

echo "restore_completed"
