#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export BACKUP_PROFILE=wtr-nas
export BACKUP_HOST=wtr-nas
export BACKUP_USER="${BACKUP_USER:-mooncen_backup}"
if [ -z "${BACKUP_ROOT:-}" ] || [ "$BACKUP_ROOT" = "/volume1/mooncen-backup" ]; then
  export BACKUP_ROOT=/volume2/homes/mooncen_backup/mooncen-backup
fi
export BACKUP_IDENTITY_FILE=/etc/mooncen/backup-ssh-key
exec /bin/bash "$SCRIPT_DIR/mooncen_backup_to_synology.sh" "$@"
