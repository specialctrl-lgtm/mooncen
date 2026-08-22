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
STAMP="$(date -u +%Y%m%d_%H%M%S)"
WORK_DIR="${WORK_DIR:-}"
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

if [ -z "$WORK_DIR" ]; then
  WORK_DIR="$(mktemp -d "/tmp/mooncen-backup-${STAMP}.XXXXXX")"
else
  if [ ! -d "$WORK_DIR" ] || [ -L "$WORK_DIR" ] || \
     [ "$(stat -c '%u:%a' -- "$WORK_DIR" 2>/dev/null || true)" != "$(id -u):700" ] || \
     [ -n "$(find "$WORK_DIR" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
    echo "Backup WORK_DIR override must be an empty caller-owned non-symlink directory with mode 0700." >&2
    exit 64
  fi
  WORK_DIR="$(cd "$WORK_DIR" && pwd -P)"
fi
case "$WORK_DIR" in
  /tmp/mooncen-backup-*) ;;
  *) echo "Unsafe WORK_DIR for backup: $WORK_DIR" >&2; exit 64 ;;
esac
cleanup() {
  status=$?
  trap - EXIT
  trap '' HUP INT TERM
  rm -rf -- "$WORK_DIR"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

DB_NAME="${DB_NAME:-mooncen}"
DB_USER="${DB_BACKUP_USER:-}"
DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-5432}"
DB_PASSWORD="${DB_BACKUP_PASSWORD:-}"
BACKUP_AGE_RECIPIENT="${BACKUP_AGE_RECIPIENT:-}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-35}"

if [ -z "$BACKUP_AGE_RECIPIENT" ]; then
  echo "BACKUP_AGE_RECIPIENT is required; refusing to create a plaintext backup." >&2
  exit 78
fi
if [[ ! "$BACKUP_AGE_RECIPIENT" =~ ^age1[0-9a-z]+$ ]]; then
  echo "BACKUP_AGE_RECIPIENT is not a valid age X25519 recipient." >&2
  exit 64
fi
if [[ ! "$DB_NAME" =~ ^[a-z_][a-z0-9_]*$ ]] || \
   [[ ! "$DB_USER" =~ ^[a-z_][a-z0-9_]*$ ]] || [ -z "$DB_PASSWORD" ]; then
  echo "A valid DB_BACKUP_USER and DB_BACKUP_PASSWORD are required." >&2
  exit 78
fi
if [ "${#DB_NAME}" -gt 40 ] || [ "${#DB_USER}" -gt 63 ]; then
  echo "DB_NAME or DB_BACKUP_USER exceeds the supported PostgreSQL identifier length." >&2
  exit 64
fi
command -v age >/dev/null 2>&1 || { echo "age is required for encrypted backups." >&2; exit 69; }
case "$BACKUP_RETENTION_DAYS" in
  ''|*[!0-9]*) echo "BACKUP_RETENTION_DAYS must be a non-negative integer." >&2; exit 64 ;;
esac
backup_validate_manifest_trust
backup_validate_exact_local_file \
  "$BACKUP_MANIFEST_SIGNING_KEY" root mooncen-backup 640 "Backup manifest signing key"
backup_validate_size_policy

DB_SIZE_BYTES="$(PGPASSWORD="$DB_PASSWORD" psql \
  -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
  -v ON_ERROR_STOP=1 -Atqc 'SELECT pg_database_size(current_database());')"
backup_require_uint source_database_size "$DB_SIZE_BYTES" 1048576 "$BACKUP_MAX_SOURCE_DB_BYTES"
backup_require_free_bytes "Backup workspace" "$WORK_DIR" \
  "$((DB_SIZE_BYTES * 2 + BACKUP_MAX_APP_ARCHIVE_BYTES * 2 + BACKUP_MAX_CONFIG_ARCHIVE_BYTES * 4 + BACKUP_LOCAL_MIN_FREE_BYTES))"

echo "checking_remote_backup_path=$BACKUP_USER@$BACKUP_HOST:$REMOTE_DIR"
if ! $SSH_CMD "$BACKUP_USER@$BACKUP_HOST" "mkdir -p '$REMOTE_DIR/db' '$REMOTE_DIR/app' '$REMOTE_DIR/config' '$REMOTE_DIR/manifests'"; then
  cat >&2 <<EOF
backup_remote_path_not_writable=$BACKUP_USER@$BACKUP_HOST:$REMOTE_DIR
Create the NAS directory and grant write permission to $BACKUP_USER, or set BACKUP_ROOT to a writable NAS path.
Expected directory: $BACKUP_ROOT
EOF
  exit 73
fi

mkdir -p "$WORK_DIR/db" "$WORK_DIR/app" "$WORK_DIR/config"

echo "backup_started=$STAMP"
echo "server=$SERVER_NAME"
echo "remote=$BACKUP_USER@$BACKUP_HOST:$REMOTE_DIR"

echo "dumping_database=$DB_NAME"
if ! (
  ulimit -f "$(((BACKUP_MAX_DECRYPTED_DUMP_BYTES + 1023) / 1024))" || exit 65
  export PGPASSWORD="$DB_PASSWORD"
  exec pg_dump \
    -h "$DB_HOST" \
    -p "$DB_PORT" \
    -U "$DB_USER" \
    -d "$DB_NAME" \
    -F c \
    -f "$WORK_DIR/db/${DB_NAME}_${STAMP}.dump"
); then
  rm -f -- "$WORK_DIR/db/${DB_NAME}_${STAMP}.dump"
  backup_fail "Database dump failed or exceeded BACKUP_MAX_DECRYPTED_DUMP_BYTES." 65
fi
backup_validate_local_bounded_file \
  "$WORK_DIR/db/${DB_NAME}_${STAMP}.dump" "$BACKUP_MAX_DECRYPTED_DUMP_BYTES" "Database dump"
DB_SIZE_AFTER_BYTES="$(PGPASSWORD="$DB_PASSWORD" psql \
  -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
  -v ON_ERROR_STOP=1 -Atqc 'SELECT pg_database_size(current_database());')"
backup_require_uint post_dump_source_database_size "$DB_SIZE_AFTER_BYTES" 1048576 "$BACKUP_MAX_SOURCE_DB_BYTES"
if [ "$DB_SIZE_AFTER_BYTES" -gt "$DB_SIZE_BYTES" ]; then
  DB_SIZE_BYTES="$DB_SIZE_AFTER_BYTES"
fi
backup_require_free_bytes "Post-dump backup workspace" "$WORK_DIR" \
  "$((DB_SIZE_BYTES + BACKUP_MAX_APP_ARCHIVE_BYTES * 2 + BACKUP_MAX_CONFIG_ARCHIVE_BYTES * 4 + BACKUP_LOCAL_MIN_FREE_BYTES))"

echo "packing_app=$APP_DIR"
APP_PARENT="$(dirname "$APP_DIR")"
APP_BASENAME="$(basename "$APP_DIR")"
if ! (
  ulimit -f "$(((BACKUP_MAX_APP_ARCHIVE_BYTES + 1023) / 1024))" || exit 65
  exec tar \
  --exclude="$APP_BASENAME/.venv" \
  --exclude="$APP_BASENAME/.env" \
  --exclude="$APP_BASENAME/.env.*" \
  --exclude="*/.env" \
  --exclude="*/.env.*" \
  --exclude="$APP_BASENAME/*.key" \
  --exclude="$APP_BASENAME/*.pem" \
  --exclude="$APP_BASENAME/*.p12" \
  --exclude="$APP_BASENAME/*.pfx" \
  --exclude="$APP_BASENAME/*.dump" \
  --exclude="$APP_BASENAME/.android-tools" \
  --exclude="$APP_BASENAME/.agents" \
  --exclude="$APP_BASENAME/.mooncen-prebuilt-release" \
  --exclude="$APP_BASENAME/backups" \
  --exclude="$APP_BASENAME/tmp_*" \
  --exclude="$APP_BASENAME/.pytest_cache" \
  --exclude="$APP_BASENAME/__pycache__" \
  --exclude="$APP_BASENAME/**/__pycache__" \
  --exclude="$APP_BASENAME/frontend2/node_modules" \
  --exclude="$APP_BASENAME/frontend2/dist" \
  --exclude="$APP_BASENAME/logs" \
  --exclude="$APP_BASENAME/.git" \
  -czf "$WORK_DIR/app/mooncen_app_${STAMP}.tar.gz" \
    -C "$APP_PARENT" "$APP_BASENAME"
); then
  rm -f -- "$WORK_DIR/app/mooncen_app_${STAMP}.tar.gz"
  backup_fail "Application archive failed or exceeded BACKUP_MAX_APP_ARCHIVE_BYTES." 65
fi
backup_validate_local_bounded_file \
  "$WORK_DIR/app/mooncen_app_${STAMP}.tar.gz" "$BACKUP_MAX_APP_ARCHIVE_BYTES" "Application archive"

echo "packing_config"
if [ -d /etc/nginx/sites-available ]; then
  if ! (
    ulimit -f "$(((BACKUP_MAX_CONFIG_ARCHIVE_BYTES + 1023) / 1024))" || exit 65
    exec tar -czf "$WORK_DIR/config/nginx_${STAMP}.tar.gz" \
      /etc/nginx/sites-available /etc/nginx/sites-enabled
  ); then
    rm -f -- "$WORK_DIR/config/nginx_${STAMP}.tar.gz"
    backup_fail "Nginx config archive failed or exceeded its size bound." 65
  fi
  backup_validate_local_bounded_file \
    "$WORK_DIR/config/nginx_${STAMP}.tar.gz" "$BACKUP_MAX_CONFIG_ARCHIVE_BYTES" "Nginx config archive"
fi
if [ -d /etc/systemd/system ]; then
  if ! (
    ulimit -f "$(((BACKUP_MAX_CONFIG_ARCHIVE_BYTES + 1023) / 1024))" || exit 65
    exec tar -czf "$WORK_DIR/config/systemd_${STAMP}.tar.gz" \
      /etc/systemd/system/mooncen-*.service /etc/systemd/system/mooncen-*.timer
  ); then
    rm -f -- "$WORK_DIR/config/systemd_${STAMP}.tar.gz"
    backup_fail "Systemd config archive failed or exceeded its size bound." 65
  fi
  backup_validate_local_bounded_file \
    "$WORK_DIR/config/systemd_${STAMP}.tar.gz" "$BACKUP_MAX_CONFIG_ARCHIVE_BYTES" "Systemd config archive"
fi

echo "encrypting_backup"
mapfile -d '' -t encryption_inputs < <(
  find "$WORK_DIR/db" "$WORK_DIR/app" "$WORK_DIR/config" \
    -type f ! -name '*.age' -print0
)
if [ "${#encryption_inputs[@]}" -eq 0 ]; then
  backup_fail "Backup encryption input snapshot is empty." 65
fi
for file in "${encryption_inputs[@]}"; do
  case "$file" in
    "$WORK_DIR"/db/*) encrypted_limit="$BACKUP_MAX_ENCRYPTED_DUMP_BYTES" ;;
    "$WORK_DIR"/app/*) encrypted_limit="$((BACKUP_MAX_APP_ARCHIVE_BYTES + 1048576))" ;;
    "$WORK_DIR"/config/*) encrypted_limit="$((BACKUP_MAX_CONFIG_ARCHIVE_BYTES + 1048576))" ;;
    *) backup_fail "Unexpected file in backup encryption set." 65 ;;
  esac
  if ! (
    ulimit -f "$(((encrypted_limit + 1023) / 1024))" || exit 65
    exec age -r "$BACKUP_AGE_RECIPIENT" -o "${file}.age" "$file"
  ); then
    rm -f -- "${file}.age"
    backup_fail "Backup encryption failed or exceeded its output size bound." 65
  fi
  backup_validate_local_bounded_file "${file}.age" "$encrypted_limit" "Encrypted backup artifact"
  rm -f -- "$file"
done

cat > "$WORK_DIR/manifest.txt" <<EOF
format=mooncen-backup-manifest-v1
timestamp=$STAMP
server=$SERVER_NAME
app_dir=$APP_DIR
db_name=$DB_NAME
db_host=$DB_HOST
db_port=$DB_PORT
db_size_bytes=$DB_SIZE_BYTES
encrypted_files_sha256:
$(cd "$WORK_DIR" && find db app config -type f -print0 | sort -z | xargs -0 -r sha256sum)
EOF
backup_sign_manifest "$WORK_DIR/manifest.txt"

echo "uploading_to_nas"
backup_scp_dir_contents "$WORK_DIR/db" "$REMOTE_DIR/db"
backup_scp_dir_contents "$WORK_DIR/app" "$REMOTE_DIR/app"
backup_scp_dir_contents "$WORK_DIR/config" "$REMOTE_DIR/config"
backup_scp_file "$WORK_DIR/manifest.txt" "$REMOTE_DIR/manifests/manifest_$STAMP.txt"
backup_scp_file "$WORK_DIR/manifest.txt.sig" "$REMOTE_DIR/manifests/manifest_$STAMP.txt.sig"

echo "applying_retention_days=$BACKUP_RETENTION_DAYS"
$SSH_CMD "$BACKUP_USER@$BACKUP_HOST" \
  "find '$REMOTE_DIR/db' '$REMOTE_DIR/app' '$REMOTE_DIR/config' '$REMOTE_DIR/manifests' -type f -mtime +$BACKUP_RETENTION_DAYS -delete"

echo "uploaded_files"
$SSH_CMD "$BACKUP_USER@$BACKUP_HOST" "find '$REMOTE_DIR' -maxdepth 2 -type f | sort | tail -30"

echo "backup_completed=$STAMP"
