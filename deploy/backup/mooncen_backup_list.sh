#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$SCRIPT_DIR/backup_ssh_common.sh"
backup_load_runtime_environment

APP_DIR="${APP_DIR:-/opt/mooncen}"
BACKUP_HOST="${BACKUP_HOST:-wtr-nas}"
BACKUP_USER="${BACKUP_USER:-mooncen_backup}"
BACKUP_ROOT="${BACKUP_ROOT:-/volume2/homes/mooncen_backup/mooncen-backup}"
SERVER_NAME="${SERVER_NAME:-$(hostname -s 2>/dev/null || hostname)}"
if [ "${BACKUP_PROFILE:-}" = "wtr-nas" ] || [ "${BACKUP_HOST:-}" = "wtr-nas" ]; then
  BACKUP_HOST=wtr-nas
  BACKUP_USER="${BACKUP_USER:-mooncen_backup}"
  if [ -z "${BACKUP_ROOT:-}" ] || [ "$BACKUP_ROOT" = "/volume1/mooncen-backup" ]; then
    BACKUP_ROOT=/volume2/homes/mooncen_backup/mooncen-backup
  fi
  BACKUP_IDENTITY_FILE=/etc/mooncen/backup-ssh-key
fi

REMOTE_DIR="$BACKUP_ROOT/$SERVER_NAME"

backup_prepare_ssh
backup_validate_remote_path "$REMOTE_DIR"

echo "remote=$BACKUP_USER@$BACKUP_HOST:$REMOTE_DIR"
$SSH_CMD "$BACKUP_USER@$BACKUP_HOST" "
  set -e
  echo '== database dumps =='
  find '$REMOTE_DIR/db' -maxdepth 1 -type f \( -name '*.dump' -o -name '*.dump.age' \) -printf '%TY-%Tm-%Td %TH:%TM %s %p\n' 2>/dev/null | sort | tail -20
  echo
  echo '== app archives =='
  find '$REMOTE_DIR/app' -maxdepth 1 -type f \( -name '*.tar.gz' -o -name '*.tar.gz.age' \) -printf '%TY-%Tm-%Td %TH:%TM %s %p\n' 2>/dev/null | sort | tail -20
  echo
  echo '== manifests =='
  find '$REMOTE_DIR/manifests' -maxdepth 1 -type f \( -name 'manifest_*.txt' -o -name 'manifest_*.txt.sig' \) -printf '%TY-%Tm-%Td %TH:%TM %s %p\n' 2>/dev/null | sort | tail -40
"
