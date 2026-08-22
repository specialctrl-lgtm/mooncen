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
TEST_DB="${TEST_DB:-mooncen_restore_test_$(date +%Y%m%d_%H%M%S)}"
WORK_DIR="${WORK_DIR:-}"
REMOTE_DIR="$BACKUP_ROOT/$SERVER_NAME"
BACKUP_CANDIDATE_CREATED=0

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
RESTORE_MIN_COURSES="${RESTORE_MIN_COURSES:-1}"
RESTORE_MIN_BRANCHES="${RESTORE_MIN_BRANCHES:-1}"

if [ -z "$WORK_DIR" ]; then
  WORK_DIR="$(mktemp -d /tmp/mooncen-restore-test.XXXXXX)"
else
  if [ ! -d "$WORK_DIR" ] || [ -L "$WORK_DIR" ] || \
     [ "$(stat -c '%u:%a' -- "$WORK_DIR" 2>/dev/null || true)" != "0:700" ] || \
     [ -n "$(find "$WORK_DIR" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
    echo "Restore-test WORK_DIR override must be an empty root-owned non-symlink directory with mode 0700." >&2
    exit 64
  fi
fi
WORK_DIR="$(cd "$WORK_DIR" && pwd -P)"
case "$WORK_DIR" in
  /tmp/mooncen-restore-test.*|/tmp/mooncen-restore-test-*) ;;
  *) echo "Unsafe WORK_DIR for restore test: $WORK_DIR" >&2; exit 64 ;;
esac
cleanup() {
  status=$?
  trap - EXIT
  trap '' HUP INT TERM
  set +e
  if [ "$BACKUP_CANDIDATE_CREATED" = "1" ]; then
    backup_set_database_connections "$TEST_DB" false >/dev/null 2>&1 || true
    backup_terminate_database_sessions "$TEST_DB" >/dev/null 2>&1 || true
    sudo -n -u postgres dropdb --force --if-exists "$TEST_DB" >/dev/null 2>&1 || true
  fi
  rm -rf -- "$WORK_DIR"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  echo "Restore verification must run through the hardened root systemd oneshot." >&2
  exit 77
fi
if [[ ! "$DB_NAME" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]] || \
   [[ ! "$DB_USER" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]] || \
   [[ ! "$TEST_DB" =~ ^mooncen_restore_[a-zA-Z0-9_]+$ ]] || \
   [ "$TEST_DB" = "$DB_NAME" ] || [ "${#TEST_DB}" -gt 63 ]; then
  echo "Invalid TEST_DB identifier." >&2
  exit 64
fi
if [ "${#DB_USER}" -gt 63 ]; then
  echo "DB_USER must be at most 63 characters." >&2
  exit 64
fi
for threshold_name in RESTORE_MIN_COURSES RESTORE_MIN_BRANCHES; do
  threshold_value="${!threshold_name}"
  if [[ ! "$threshold_value" =~ ^[0-9]+$ ]]; then
    echo "$threshold_name must be a non-negative integer." >&2
    exit 64
  fi
done
if [ "$RESTORE_MIN_COURSES" -gt 1000000000 ] || [ "$RESTORE_MIN_BRANCHES" -gt 1000000000 ]; then
  echo "Restore row-count minimums must not exceed 1000000000." >&2
  exit 64
fi

if [ "$BACKUP_FILE" = "latest" ]; then
  BACKUP_FILE="$(backup_select_latest_committed_dump "$REMOTE_DIR" "$DB_NAME")"
fi

if [ -z "$BACKUP_FILE" ]; then
  echo "No backup dump found in $REMOTE_DIR/db" >&2
  exit 1
fi

LOCAL_DUMP="$WORK_DIR/restore_test.dump"
echo "restore_test_backup=$BACKUP_FILE"
backup_restore_fetch_verified_dump \
  "$REMOTE_DIR" "$BACKUP_FILE" "$WORK_DIR" "$BACKUP_AGE_IDENTITY_FILE" "$LOCAL_DUMP" \
  "$SERVER_NAME" "$DB_NAME" 0
chown root:postgres "$WORK_DIR" "$LOCAL_DUMP"
chmod 0750 "$WORK_DIR"
chmod 0640 "$LOCAL_DUMP"
backup_preflight_postgres_restore "$LOCAL_DUMP" "$BACKUP_VERIFIED_DB_SIZE_BYTES"

if sudo -n -u postgres psql -d postgres -Atqc \
  "SELECT 1 FROM pg_database WHERE datname = '$TEST_DB';" | grep -qx 1; then
  echo "Restore test database already exists; refusing to replace it: $TEST_DB" >&2
  exit 73
fi

echo "restoring_and_validating_test_candidate=$TEST_DB"
backup_restore_database_candidate \
  "$LOCAL_DUMP" "$TEST_DB" "$DB_USER" "$APP_DIR" \
  "$RESTORE_MIN_COURSES" "$RESTORE_MIN_BRANCHES"
printf 'courses\t%s\nbranches\t%s\nlatest_course_update\t%s\n' \
  "$BACKUP_RESTORED_COURSE_COUNT" "$BACKUP_RESTORED_BRANCH_COUNT" "$BACKUP_RESTORED_LATEST_UPDATE"

echo "dropping_test_db=$TEST_DB"
sudo -n -u postgres dropdb --force "$TEST_DB"
BACKUP_CANDIDATE_CREATED=0
echo "restore_test_completed"
