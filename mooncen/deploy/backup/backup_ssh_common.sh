#!/usr/bin/env bash

readonly BACKUP_ENV_FILE=/etc/mooncen/backup.env
readonly BACKUP_DEFAULT_IDENTITY_FILE=/etc/mooncen/backup-ssh-key
readonly BACKUP_SYSTEMD_CREDENTIALS_DIRECTORY="${CREDENTIALS_DIRECTORY:-}"
BACKUP_RUNTIME_ENV_LOADED=0

BACKUP_HOST="${BACKUP_HOST:-wtr-nas}"
BACKUP_USER="${BACKUP_USER:-mooncen_backup}"
BACKUP_ROOT="${BACKUP_ROOT:-/volume2/homes/mooncen_backup/mooncen-backup}"
BACKUP_IDENTITY_FILE="${BACKUP_IDENTITY_FILE:-$BACKUP_DEFAULT_IDENTITY_FILE}"
BACKUP_ALLOW_TAILSCALE_IP="${BACKUP_ALLOW_TAILSCALE_IP:-${BACKUP_ALLOW_TAILSCALE_SSH:-0}}"
BACKUP_SSH_CONFIG="${BACKUP_SSH_CONFIG:-/dev/null}"
BACKUP_PORT="${BACKUP_PORT:-}"
BACKUP_KNOWN_HOSTS_FILE="${BACKUP_KNOWN_HOSTS_FILE:-/etc/mooncen/backup-known-hosts}"
BACKUP_MANIFEST_SIGNING_KEY="${BACKUP_MANIFEST_SIGNING_KEY:-/etc/mooncen/backup-manifest-signing-key}"
BACKUP_MANIFEST_ALLOWED_SIGNERS="${BACKUP_MANIFEST_ALLOWED_SIGNERS:-/etc/mooncen/backup-manifest-allowed-signers}"
BACKUP_MANIFEST_PRINCIPAL="mooncen-backup"
BACKUP_MANIFEST_NAMESPACE="mooncen-backup-manifest-v1"
BACKUP_MAX_ENCRYPTED_DUMP_BYTES="${BACKUP_MAX_ENCRYPTED_DUMP_BYTES:-68719476736}"
BACKUP_MAX_DECRYPTED_DUMP_BYTES="${BACKUP_MAX_DECRYPTED_DUMP_BYTES:-68719476736}"
BACKUP_LOCAL_MIN_FREE_BYTES="${BACKUP_LOCAL_MIN_FREE_BYTES:-1073741824}"
BACKUP_DB_MIN_FREE_BYTES="${BACKUP_DB_MIN_FREE_BYTES:-2147483648}"
BACKUP_RESTORE_EXPANSION_FACTOR="${BACKUP_RESTORE_EXPANSION_FACTOR:-4}"
BACKUP_MAX_AGE_SECONDS="${BACKUP_MAX_AGE_SECONDS:-604800}"
BACKUP_MAX_APP_ARCHIVE_BYTES="${BACKUP_MAX_APP_ARCHIVE_BYTES:-2147483648}"
BACKUP_MAX_CONFIG_ARCHIVE_BYTES="${BACKUP_MAX_CONFIG_ARCHIVE_BYTES:-268435456}"
BACKUP_MAX_SOURCE_DB_BYTES="${BACKUP_MAX_SOURCE_DB_BYTES:-274877906944}"
BACKUP_OPERATION_LOCK_FILE=/run/lock/mooncen-backup-restore.lock

backup_fail() {
  local message="$1"
  local status="${2:-64}"
  echo "$message" >&2
  exit "$status"
}

backup_validate_exact_local_file() {
  local file_path="$1"
  local expected_owner="$2"
  local expected_group="$3"
  local expected_mode="$4"
  local label="$5"
  local actual_contract

  if [[ "$file_path" != /* ]] || [ ! -f "$file_path" ] || [ -L "$file_path" ] || [ ! -r "$file_path" ]; then
    backup_fail "$label must be a readable regular non-symlink file: $file_path" 78
  fi
  actual_contract="$(stat -c '%U:%G:%a' -- "$file_path")"
  if [ "$actual_contract" != "$expected_owner:$expected_group:$expected_mode" ]; then
    backup_fail "$label must have contract $expected_owner:$expected_group:$expected_mode (found $actual_contract)." 78
  fi
}

backup_select_runtime_ssh_identity() {
  local effective_uid="${EUID:-$(id -u)}"
  local credential_dir="$BACKUP_SYSTEMD_CREDENTIALS_DIRECTORY"
  local credential_path=""

  if [ "$effective_uid" -eq 0 ]; then
    case "$credential_dir" in
      /run/credentials/mooncen-backup-restore-test.service|\
      /run/credentials/mooncen-backup-restore-manual.service) ;;
      *)
        backup_fail \
          "Root backup restore operations must receive the NAS identity through systemd LoadCredential." \
          78
        ;;
    esac
    credential_path="$credential_dir/backup-ssh-key"
    backup_validate_exact_local_file \
      "$BACKUP_DEFAULT_IDENTITY_FILE" root mooncen-backup 640 "Canonical backup SSH identity"
    backup_validate_exact_local_file \
      "$credential_path" root root 400 "Backup restore SSH credential"
    if ! cmp -s -- "$BACKUP_DEFAULT_IDENTITY_FILE" "$credential_path"; then
      backup_fail \
        "Backup restore SSH credential does not match the canonical identity." \
        78
    fi
    BACKUP_IDENTITY_FILE="$credential_path"
    return 0
  fi

  if [ "$(id -un)" != "mooncen-backup" ] || \
     [ "$BACKUP_IDENTITY_FILE" != "/etc/mooncen/backup-ssh-key" ]; then
    backup_fail \
      "Backup SSH transport must run as mooncen-backup with the canonical identity path." \
      78
  fi
  backup_validate_exact_local_file \
    "$BACKUP_IDENTITY_FILE" root mooncen-backup 640 "Backup SSH identity"
}

backup_load_runtime_environment() {
  if [ "${BACKUP_RUNTIME_ENV_LOADED:-0}" = "1" ]; then
    return 0
  fi
  backup_validate_exact_local_file \
    "$BACKUP_ENV_FILE" root mooncen-backup 640 "Backup runtime environment"
  set -a
  # shellcheck disable=SC1091
  . "$BACKUP_ENV_FILE"
  set +a
  BACKUP_RUNTIME_ENV_LOADED=1
}

backup_require_uint() {
  local label="$1"
  local value="$2"
  local minimum="$3"
  local maximum="$4"
  if [[ ! "$value" =~ ^[0-9]+$ ]] || [ "$value" -lt "$minimum" ] || [ "$value" -gt "$maximum" ]; then
    backup_fail "$label must be an integer between $minimum and $maximum."
  fi
}

backup_validate_size_policy() {
  backup_require_uint BACKUP_MAX_ENCRYPTED_DUMP_BYTES "$BACKUP_MAX_ENCRYPTED_DUMP_BYTES" 1048576 1099511627776
  backup_require_uint BACKUP_MAX_DECRYPTED_DUMP_BYTES "$BACKUP_MAX_DECRYPTED_DUMP_BYTES" 1048576 1099511627776
  backup_require_uint BACKUP_LOCAL_MIN_FREE_BYTES "$BACKUP_LOCAL_MIN_FREE_BYTES" 0 1099511627776
  backup_require_uint BACKUP_DB_MIN_FREE_BYTES "$BACKUP_DB_MIN_FREE_BYTES" 0 1099511627776
  backup_require_uint BACKUP_RESTORE_EXPANSION_FACTOR "$BACKUP_RESTORE_EXPANSION_FACTOR" 1 16
  backup_require_uint BACKUP_MAX_AGE_SECONDS "$BACKUP_MAX_AGE_SECONDS" 3600 315360000
  backup_require_uint BACKUP_MAX_APP_ARCHIVE_BYTES "$BACKUP_MAX_APP_ARCHIVE_BYTES" 1048576 68719476736
  backup_require_uint BACKUP_MAX_CONFIG_ARCHIVE_BYTES "$BACKUP_MAX_CONFIG_ARCHIVE_BYTES" 1048576 17179869184
  backup_require_uint BACKUP_MAX_SOURCE_DB_BYTES "$BACKUP_MAX_SOURCE_DB_BYTES" 1048576 4398046511104
}

backup_validate_row_count() {
  local label="$1"
  local value="$2"
  if [[ ! "$value" =~ ^[0-9]+$ ]] || [ "${#value}" -gt 18 ]; then
    backup_fail "$label query returned an invalid or unreasonably large row count." 72
  fi
}

backup_acquire_operation_lock() {
  command -v flock >/dev/null 2>&1 || backup_fail "flock is required for backup/restore serialization." 69
  backup_validate_exact_local_file \
    "$BACKUP_OPERATION_LOCK_FILE" root mooncen-backup 660 "Backup operation lock"
  exec 9<>"$BACKUP_OPERATION_LOCK_FILE"
  if ! flock -n 9; then
    backup_fail "Another MoonCen backup or restore operation already holds the exclusive lock." 75
  fi
}

backup_validate_local_bounded_file() {
  local file_path="$1"
  local max_bytes="$2"
  local label="$3"
  local file_size
  if [ ! -f "$file_path" ] || [ -L "$file_path" ]; then
    backup_fail "$label is not a regular file." 65
  fi
  file_size="$(stat -c '%s' -- "$file_path")"
  if [ "$file_size" -le 0 ] || [ "$file_size" -gt "$max_bytes" ]; then
    backup_fail "$label is outside its configured size bound." 65
  fi
}

backup_validate_config() {
  if [[ ! "$BACKUP_USER" =~ ^[A-Za-z_][A-Za-z0-9_-]*$ ]]; then
    echo "Invalid BACKUP_USER." >&2
    exit 64
  fi
  if [[ ! "$BACKUP_HOST" =~ ^[A-Za-z0-9._:-]+$ ]]; then
    echo "Invalid BACKUP_HOST." >&2
    exit 64
  fi
  if [[ ! "${SERVER_NAME:-}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$ ]]; then
    echo "Invalid SERVER_NAME." >&2
    exit 64
  fi
  backup_validate_remote_path "$BACKUP_ROOT"
  case "$BACKUP_PORT" in
    ''|*[!0-9]*) [ -z "$BACKUP_PORT" ] || { echo "Invalid BACKUP_PORT." >&2; exit 64; } ;;
    *) [ "$BACKUP_PORT" -ge 1 ] && [ "$BACKUP_PORT" -le 65535 ] || { echo "Invalid BACKUP_PORT." >&2; exit 64; } ;;
  esac
  if [[ "$BACKUP_KNOWN_HOSTS_FILE" != /* ]]; then
    backup_fail "BACKUP_KNOWN_HOSTS_FILE must be an absolute path."
  fi
}

backup_validate_remote_path() {
  local remote_path="${1:-}"
  if [[ ! "$remote_path" =~ ^/[A-Za-z0-9._/-]+$ ]] || [[ "$remote_path" == *".."* ]]; then
    echo "Unsafe backup remote path." >&2
    exit 64
  fi
}

is_tailscale_ip() {
  local ip="${1:-}"
  local first second _rest
  IFS=. read -r first second _rest <<<"$ip"
  [ "$first" = "100" ] && [ "${second:-0}" -ge 64 ] 2>/dev/null && [ "${second:-0}" -le 127 ] 2>/dev/null
}

backup_resolved_ip() {
  getent ahostsv4 "$BACKUP_HOST" 2>/dev/null | awk 'NR == 1 {print $1}'
}

backup_ssh_guard() {
  local resolved_ip
  resolved_ip="$(backup_resolved_ip || true)"
  if [ -n "$resolved_ip" ] && is_tailscale_ip "$resolved_ip" && [ "$BACKUP_ALLOW_TAILSCALE_IP" != "1" ]; then
    cat >&2 <<EOF
backup_host_resolves_to_tailscale=$BACKUP_HOST/$resolved_ip
Backup uses regular SSH to NAS. This host currently resolves to a Tailscale IP, so it can trigger Tailscale SSH browser authentication.
Set BACKUP_HOST to a NAS LAN/public SSH hostname or IP, or set BACKUP_ALLOW_TAILSCALE_IP=1 to allow normal OpenSSH over the Tailscale private network.
EOF
    exit 64
  fi
}

backup_validate_pinned_known_hosts() {
  local expected_host_token="$BACKUP_HOST"
  if [ -n "$BACKUP_PORT" ] && [ "$BACKUP_PORT" != "22" ]; then
    expected_host_token="[$BACKUP_HOST]:$BACKUP_PORT"
  fi
  backup_validate_exact_local_file \
    "$BACKUP_KNOWN_HOSTS_FILE" root mooncen-backup 640 "Backup pinned known_hosts file"
  if ! awk -v expected_host="$expected_host_token" '
    /^[[:space:]]*($|#)/ {next}
    {
      count++
      if (NF != 3 || $1 != expected_host || $2 != "ssh-ed25519") invalid=1
    }
    END {exit !(count == 1 && invalid != 1)}
  ' "$BACKUP_KNOWN_HOSTS_FILE" || \
     ! ssh-keygen -l -f "$BACKUP_KNOWN_HOSTS_FILE" >/dev/null 2>&1; then
    backup_fail "Pinned known_hosts must contain exactly one literal host Ed25519 key for BACKUP_HOST." 78
  fi
}

backup_ssh_options() {
  local identity_file=""
  if [ -f "$BACKUP_IDENTITY_FILE" ]; then
    identity_file="$BACKUP_IDENTITY_FILE"
  fi
  local options=(
    -F "$BACKUP_SSH_CONFIG"
    -o IdentitiesOnly=yes
    -o BatchMode=yes
    -o PubkeyAuthentication=yes
    -o PreferredAuthentications=publickey
    -o PasswordAuthentication=no
    -o KbdInteractiveAuthentication=no
    -o NumberOfPasswordPrompts=0
    -o StrictHostKeyChecking=yes
    -o "UserKnownHostsFile=$BACKUP_KNOWN_HOSTS_FILE"
    -o GlobalKnownHostsFile=/dev/null
    -o UpdateHostKeys=no
    -o ConnectTimeout=10
    -o LogLevel=ERROR
  )
  if [ -n "$identity_file" ]; then
    options=(-i "$identity_file" "${options[@]}")
  fi
  if [ -n "$BACKUP_PORT" ]; then
    options=(-p "$BACKUP_PORT" "${options[@]}")
  fi
  printf '%q ' "${options[@]}"
}

backup_build_ssh_options_array() {
  local identity_file=""
  if [ -f "$BACKUP_IDENTITY_FILE" ]; then
    identity_file="$BACKUP_IDENTITY_FILE"
  fi
  BACKUP_SSH_OPTIONS=(
    -F "$BACKUP_SSH_CONFIG"
    -o IdentitiesOnly=yes
    -o BatchMode=yes
    -o PubkeyAuthentication=yes
    -o PreferredAuthentications=publickey
    -o PasswordAuthentication=no
    -o KbdInteractiveAuthentication=no
    -o NumberOfPasswordPrompts=0
    -o StrictHostKeyChecking=yes
    -o "UserKnownHostsFile=$BACKUP_KNOWN_HOSTS_FILE"
    -o GlobalKnownHostsFile=/dev/null
    -o UpdateHostKeys=no
    -o ConnectTimeout=10
    -o LogLevel=ERROR
  )
  if [ -n "$identity_file" ]; then
    BACKUP_SSH_OPTIONS=(-i "$identity_file" "${BACKUP_SSH_OPTIONS[@]}")
  fi
  BACKUP_SCP_OPTIONS=("${BACKUP_SSH_OPTIONS[@]}")
  if [ -n "$BACKUP_PORT" ]; then
    BACKUP_SSH_OPTIONS=(-p "$BACKUP_PORT" "${BACKUP_SSH_OPTIONS[@]}")
    BACKUP_SCP_OPTIONS=(-P "$BACKUP_PORT" "${BACKUP_SCP_OPTIONS[@]}")
  fi
}

backup_prepare_ssh() {
  backup_select_runtime_ssh_identity
  backup_validate_config
  backup_validate_pinned_known_hosts
  backup_ssh_guard
  backup_build_ssh_options_array
  SSH_CMD="ssh $(backup_ssh_options)"
  RSYNC_CMD="rsync -avz -e \"$SSH_CMD\""
}

backup_validate_manifest_trust() {
  command -v ssh-keygen >/dev/null 2>&1 || backup_fail "ssh-keygen with -Y signing support is required." 69
  backup_validate_exact_local_file \
    "$BACKUP_MANIFEST_ALLOWED_SIGNERS" root root 644 "Backup manifest allowed_signers file"
  if ! awk '
    /^[[:space:]]*($|#)/ {next}
    {
      count++
      if (NF != 3 || $1 != "mooncen-backup" || $2 != "ssh-ed25519") invalid=1
    }
    END {exit !(count == 1 && invalid != 1)}
  ' "$BACKUP_MANIFEST_ALLOWED_SIGNERS"; then
    backup_fail "Backup manifest allowed_signers must contain exactly one literal mooncen-backup Ed25519 key." 78
  fi
}

backup_sign_manifest() {
  local manifest_path="$1"
  local signature_path="${manifest_path}.sig"

  backup_validate_manifest_trust
  backup_validate_exact_local_file \
    "$BACKUP_MANIFEST_SIGNING_KEY" root mooncen-backup 640 "Backup manifest signing key"
  rm -f -- "$signature_path"
  ssh-keygen -Y sign \
    -f "$BACKUP_MANIFEST_SIGNING_KEY" \
    -n "$BACKUP_MANIFEST_NAMESPACE" \
    "$manifest_path" >/dev/null
  if [ ! -s "$signature_path" ] || [ -L "$signature_path" ]; then
    backup_fail "Backup manifest signature was not created safely." 65
  fi
  backup_verify_manifest "$manifest_path" "$signature_path"
}

backup_verify_manifest() {
  local manifest_path="$1"
  local signature_path="$2"

  backup_validate_manifest_trust
  if [ ! -f "$manifest_path" ] || [ -L "$manifest_path" ] || \
     [ ! -f "$signature_path" ] || [ -L "$signature_path" ]; then
    backup_fail "Backup manifest or signature is not a regular file." 65
  fi
  if ! ssh-keygen -Y verify \
    -f "$BACKUP_MANIFEST_ALLOWED_SIGNERS" \
    -I "$BACKUP_MANIFEST_PRINCIPAL" \
    -n "$BACKUP_MANIFEST_NAMESPACE" \
    -s "$signature_path" < "$manifest_path" >/dev/null; then
    backup_fail "Backup manifest signature verification failed." 65
  fi
}

backup_remote_regular_file_size() {
  local remote_path="$1"
  local size

  backup_validate_remote_path "$remote_path"
  size="$($SSH_CMD "$BACKUP_USER@$BACKUP_HOST" \
    "if [ -f '$remote_path' ] && [ ! -L '$remote_path' ]; then stat -c '%s' '$remote_path'; else exit 66; fi")" || \
    backup_fail "Remote backup file is missing, unreadable, or a symlink: $remote_path" 66
  if [[ ! "$size" =~ ^[0-9]+$ ]]; then
    backup_fail "Remote backup stat returned an invalid size for: $remote_path" 65
  fi
  printf '%s\n' "$size"
}

backup_available_bytes() {
  local target_path="$1"
  local available
  available="$(df -B1 --output=avail -- "$target_path" | awk 'NR == 2 {gsub(/[[:space:]]/, "", $0); print $0}')"
  if [[ ! "$available" =~ ^[0-9]+$ ]]; then
    backup_fail "Unable to determine free disk space for: $target_path" 70
  fi
  printf '%s\n' "$available"
}

backup_require_free_bytes() {
  local label="$1"
  local target_path="$2"
  local required_bytes="$3"
  local available_bytes

  available_bytes="$(backup_available_bytes "$target_path")"
  if [ "$available_bytes" -lt "$required_bytes" ]; then
    backup_fail "$label requires $required_bytes free bytes, but only $available_bytes are available at $target_path." 70
  fi
}

backup_fetch_remote_bounded() {
  local remote_path="$1"
  local local_path="$2"
  local max_bytes="$3"
  local remote_size local_size max_blocks

  remote_size="$(backup_remote_regular_file_size "$remote_path")"
  if [ "$remote_size" -le 0 ] || [ "$remote_size" -gt "$max_bytes" ]; then
    backup_fail "Remote backup file size is outside the allowed bound: $remote_path ($remote_size bytes)." 65
  fi
  max_blocks=$(((remote_size + 1023) / 1024))
  rm -f -- "$local_path"
  if ! (
    ulimit -f "$max_blocks" || exit 65
    backup_scp_from_remote "$remote_path" "$local_path"
  ); then
    rm -f -- "$local_path"
    backup_fail "Bounded backup download failed or exceeded the remote stat size: $remote_path" 65
  fi
  if [ ! -f "$local_path" ] || [ -L "$local_path" ]; then
    backup_fail "Downloaded backup path is not a regular file: $local_path" 65
  fi
  local_size="$(stat -c '%s' -- "$local_path")"
  if [ "$local_size" != "$remote_size" ]; then
    backup_fail "Downloaded backup size does not match remote stat: $remote_path" 65
  fi
  printf '%s\n' "$remote_size"
}

backup_select_latest_committed_dump() {
  local remote_dir="$1"
  local expected_db="$2"
  local signature_path signature_name stamp manifest_path dump_path latest_stamp="" signature_listing
  local committed_signatures=()

  backup_validate_remote_path "$remote_dir"
  if [[ ! "$expected_db" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || [ "${#expected_db}" -gt 40 ]; then
    backup_fail "Expected backup database name is invalid." 64
  fi
  signature_listing="$($SSH_CMD "$BACKUP_USER@$BACKUP_HOST" "
      for signature in '$remote_dir'/manifests/manifest_*.txt.sig; do
        [ -f \"\$signature\" ] && [ ! -L \"\$signature\" ] || continue
        name=\${signature##*/}
        candidate_stamp=\${name#manifest_}
        candidate_stamp=\${candidate_stamp%.txt.sig}
        manifest=\${signature%.sig}
        dump='$remote_dir/db/${expected_db}_'\${candidate_stamp}'.dump.age'
        if [ -f \"\$manifest\" ] && [ ! -L \"\$manifest\" ] && \
           [ -f \"\$dump\" ] && [ ! -L \"\$dump\" ]; then
          printf '%s\n' \"\$signature\"
        fi
      done
    ")" || backup_fail "Unable to list committed backup generations." 66
  if [ -z "$signature_listing" ]; then
    return 0
  fi
  mapfile -t committed_signatures <<<"$signature_listing"
  for signature_path in "${committed_signatures[@]}"; do
    backup_validate_remote_path "$signature_path"
    signature_name="$(basename -- "$signature_path")"
    if [[ ! "$signature_name" =~ ^manifest_([0-9]{8}_[0-9]{6})\.txt\.sig$ ]]; then
      continue
    fi
    stamp="${BASH_REMATCH[1]}"
    if [ "$signature_path" != "$remote_dir/manifests/$signature_name" ]; then
      continue
    fi
    if [ -z "$latest_stamp" ] || [[ "$stamp" > "$latest_stamp" ]]; then
      latest_stamp="$stamp"
    fi
  done
  if [ -z "$latest_stamp" ]; then
    return 0
  fi
  stamp="$latest_stamp"
  signature_path="$remote_dir/manifests/manifest_${stamp}.txt.sig"
  manifest_path="${signature_path%.sig}"
  dump_path="$remote_dir/db/${expected_db}_${stamp}.dump.age"
  backup_remote_regular_file_size "$signature_path" >/dev/null
  backup_remote_regular_file_size "$manifest_path" >/dev/null
  backup_remote_regular_file_size "$dump_path" >/dev/null
  printf '%s\n' "$dump_path"
}

backup_restore_fetch_verified_dump() {
  local remote_dir="$1"
  local backup_file="$2"
  local work_dir="$3"
  local age_identity_file="$4"
  local local_dump="$5"
  local expected_server="$6"
  local expected_db="$7"
  local allow_stale="${8:-0}"
  local source_name stamp stamp_iso manifest_remote signature_remote manifest_local signature_local
  local expected_hash actual_hash remote_size fetched_size required_work_bytes decrypted_size max_blocks
  local backup_epoch now_epoch backup_age_seconds
  local manifest_matches=() manifest_stamps=() manifest_formats=()
  local manifest_servers=() manifest_databases=()
  local manifest_database_sizes=()

  backup_validate_size_policy
  backup_validate_manifest_trust
  backup_validate_exact_local_file "$age_identity_file" root root 600 "Backup age identity"
  backup_validate_remote_path "$remote_dir"
  backup_validate_remote_path "$backup_file"
  source_name="$(basename -- "$backup_file")"
  if [[ ! "$source_name" =~ ^[A-Za-z0-9_-]+_(([0-9]{4})([0-9]{2})([0-9]{2})_([0-9]{2})([0-9]{2})([0-9]{2}))\.dump\.age$ ]]; then
    backup_fail "Only timestamped encrypted .dump.age backups can be restored." 65
  fi
  stamp="${BASH_REMATCH[1]}"
  stamp_iso="${BASH_REMATCH[2]}-${BASH_REMATCH[3]}-${BASH_REMATCH[4]}T${BASH_REMATCH[5]}:${BASH_REMATCH[6]}:${BASH_REMATCH[7]}Z"
  if [ "$allow_stale" != "0" ] && [ "$allow_stale" != "1" ]; then
    backup_fail "The stale-backup override must be 0 or 1."
  fi
  if [ "$source_name" != "${expected_db}_${stamp}.dump.age" ]; then
    backup_fail "Backup dump filename does not match the expected database and signed timestamp." 65
  fi
  if [ "$backup_file" != "$remote_dir/db/$source_name" ]; then
    backup_fail "Backup dump must be a direct child of the configured remote db directory." 65
  fi

  manifest_remote="$remote_dir/manifests/manifest_${stamp}.txt"
  signature_remote="${manifest_remote}.sig"
  manifest_local="$work_dir/manifest_${stamp}.txt"
  signature_local="${manifest_local}.sig"
  backup_fetch_remote_bounded "$manifest_remote" "$manifest_local" 1048576 >/dev/null
  backup_fetch_remote_bounded "$signature_remote" "$signature_local" 65536 >/dev/null
  backup_verify_manifest "$manifest_local" "$signature_local"

  mapfile -t manifest_formats < <(awk -F= '$1 == "format" {print $2}' "$manifest_local")
  mapfile -t manifest_stamps < <(awk -F= '$1 == "timestamp" {print $2}' "$manifest_local")
  mapfile -t manifest_servers < <(awk -F= '$1 == "server" {print substr($0, index($0, "=") + 1)}' "$manifest_local")
  mapfile -t manifest_databases < <(awk -F= '$1 == "db_name" {print substr($0, index($0, "=") + 1)}' "$manifest_local")
  mapfile -t manifest_database_sizes < <(awk -F= '$1 == "db_size_bytes" {print $2}' "$manifest_local")
  if [ "${#manifest_formats[@]}" -ne 1 ] || \
     [ "${manifest_formats[0]}" != "mooncen-backup-manifest-v1" ] || \
     [ "${#manifest_stamps[@]}" -ne 1 ] || [ "${manifest_stamps[0]}" != "$stamp" ] || \
     [ "${#manifest_servers[@]}" -ne 1 ] || [ "${manifest_servers[0]}" != "$expected_server" ] || \
     [ "${#manifest_databases[@]}" -ne 1 ] || [ "${manifest_databases[0]}" != "$expected_db" ] || \
     [ "${#manifest_database_sizes[@]}" -ne 1 ]; then
    backup_fail "Signed manifest context does not match the dump filename, server, or database." 65
  fi
  backup_require_uint signed_manifest_db_size "${manifest_database_sizes[0]}" 1048576 "$BACKUP_MAX_SOURCE_DB_BYTES"
  BACKUP_VERIFIED_DB_SIZE_BYTES="${manifest_database_sizes[0]}"
  backup_epoch="$(date -u -d "$stamp_iso" +%s 2>/dev/null)" || \
    backup_fail "Backup filename contains an invalid UTC timestamp." 65
  now_epoch="$(date -u +%s)"
  if [ "$backup_epoch" -gt "$((now_epoch + 300))" ]; then
    backup_fail "Backup timestamp is unreasonably far in the future." 65
  fi
  backup_age_seconds=$((now_epoch - backup_epoch))
  if [ "$backup_age_seconds" -gt "$BACKUP_MAX_AGE_SECONDS" ] && [ "$allow_stale" != "1" ]; then
    backup_fail "Signed backup is older than BACKUP_MAX_AGE_SECONDS; explicit stale restore approval is required." 65
  fi

  mapfile -t manifest_matches < <(
    awk -v path="db/$source_name" '$2 == path {print $1}' "$manifest_local"
  )
  if [ "${#manifest_matches[@]}" -ne 1 ] || [[ ! "${manifest_matches[0]}" =~ ^[0-9a-f]{64}$ ]]; then
    backup_fail "Signed backup manifest does not contain exactly one valid hash for db/$source_name." 65
  fi
  expected_hash="${manifest_matches[0]}"

  remote_size="$(backup_remote_regular_file_size "$backup_file")"
  if [ "$remote_size" -le 0 ] || [ "$remote_size" -gt "$BACKUP_MAX_ENCRYPTED_DUMP_BYTES" ]; then
    backup_fail "Encrypted dump size is outside BACKUP_MAX_ENCRYPTED_DUMP_BYTES: $remote_size" 65
  fi
  required_work_bytes=$((remote_size * 2 + BACKUP_LOCAL_MIN_FREE_BYTES))
  backup_require_free_bytes "Backup download and decryption" "$work_dir" "$required_work_bytes"

  fetched_size="$(backup_fetch_remote_bounded \
    "$backup_file" "$work_dir/$source_name" "$BACKUP_MAX_ENCRYPTED_DUMP_BYTES")"
  if [ "$fetched_size" != "$remote_size" ]; then
    rm -f -- "$work_dir/$source_name"
    backup_fail "Encrypted dump changed size between remote preflight and bounded download." 65
  fi
  actual_hash="$(sha256sum "$work_dir/$source_name" | awk '{print $1}')"
  if [ "$actual_hash" != "$expected_hash" ]; then
    backup_fail "Encrypted dump hash does not match the signed manifest." 65
  fi

  command -v age >/dev/null 2>&1 || backup_fail "age is required to decrypt backups." 69
  backup_require_free_bytes "Backup decryption" "$work_dir" "$((remote_size + BACKUP_LOCAL_MIN_FREE_BYTES))"
  max_blocks=$(((BACKUP_MAX_DECRYPTED_DUMP_BYTES + 1023) / 1024))
  rm -f -- "$local_dump"
  if ! (
    ulimit -f "$max_blocks" || exit 65
    exec age -d -i "$age_identity_file" -o "$local_dump" "$work_dir/$source_name"
  ); then
    rm -f -- "$local_dump"
    backup_fail "Backup decryption failed or exceeded the configured size bound." 65
  fi
  if [ ! -f "$local_dump" ] || [ -L "$local_dump" ]; then
    backup_fail "Decrypted dump is not a regular file." 65
  fi
  decrypted_size="$(stat -c '%s' -- "$local_dump")"
  if [ "$decrypted_size" -le 0 ] || [ "$decrypted_size" -gt "$BACKUP_MAX_DECRYPTED_DUMP_BYTES" ]; then
    rm -f -- "$local_dump"
    backup_fail "Decrypted dump size is outside BACKUP_MAX_DECRYPTED_DUMP_BYTES." 65
  fi
}

backup_preflight_postgres_restore() {
  local dump_path="$1"
  local source_db_size="$2"
  local dump_size data_directory required_db_bytes dump_estimate_bytes

  if [ ! -f "$dump_path" ] || [ -L "$dump_path" ]; then
    backup_fail "PostgreSQL restore preflight requires a regular dump file." 65
  fi
  dump_size="$(stat -c '%s' -- "$dump_path")"
  if [ "$dump_size" -le 0 ] || [ "$dump_size" -gt "$BACKUP_MAX_DECRYPTED_DUMP_BYTES" ]; then
    backup_fail "PostgreSQL dump exceeds the configured restore bound." 65
  fi
  if ! sudo -n -u postgres pg_restore --list "$dump_path" >/dev/null; then
    backup_fail "pg_restore --list rejected the decrypted dump." 65
  fi
  data_directory="$(sudo -n -u postgres psql -v ON_ERROR_STOP=1 -d postgres -Atqc 'SHOW data_directory;')"
  if [[ "$data_directory" != /* ]] || [ ! -d "$data_directory" ]; then
    backup_fail "PostgreSQL data_directory is not a usable absolute directory." 70
  fi
  backup_require_uint signed_manifest_db_size "$source_db_size" 1 "$BACKUP_MAX_SOURCE_DB_BYTES"
  dump_estimate_bytes=$((dump_size * BACKUP_RESTORE_EXPANSION_FACTOR))
  required_db_bytes="$source_db_size"
  if [ "$dump_estimate_bytes" -gt "$required_db_bytes" ]; then
    required_db_bytes="$dump_estimate_bytes"
  fi
  required_db_bytes=$((required_db_bytes + BACKUP_DB_MIN_FREE_BYTES))
  backup_require_free_bytes "PostgreSQL restore" "$data_directory" "$required_db_bytes"
}

backup_set_database_connections() {
  local database_name="$1"
  local allow_connections="$2"
  sudo -n -u postgres psql -v ON_ERROR_STOP=1 -d postgres -c \
    "ALTER DATABASE \"$database_name\" ALLOW_CONNECTIONS $allow_connections;"
}

backup_terminate_database_sessions() {
  local database_name="$1"
  sudo -n -u postgres psql -v ON_ERROR_STOP=1 -d postgres -Atqc \
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$database_name' AND pid <> pg_backend_pid();" \
    >/dev/null
}

backup_converge_database_ownership() {
  local database_name="$1"
  local owner_role="$2"
  sudo -n -u postgres psql -v ON_ERROR_STOP=1 -d "$database_name" <<SQL
ALTER DATABASE "$database_name" OWNER TO "$owner_role";
ALTER SCHEMA public OWNER TO "$owner_role";
DO \$\$
DECLARE
  item record;
BEGIN
  IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'crawl_staging') THEN
    EXECUTE format('ALTER SCHEMA crawl_staging OWNER TO %I', '$owner_role');
  END IF;
  FOR item IN
    SELECT n.nspname AS schema_name, c.relname AS object_name, c.relkind
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname IN ('public', 'crawl_staging')
      AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
      -- Ownership of a serial/identity sequence is managed through its table.
      -- PostgreSQL rejects ALTER SEQUENCE OWNER for these linked sequences.
      AND (
        c.relkind <> 'S'
        OR NOT EXISTS (
          SELECT 1
          FROM pg_depend sequence_dependency
          WHERE sequence_dependency.classid = 'pg_class'::regclass
            AND sequence_dependency.objid = c.oid
            AND sequence_dependency.refclassid = 'pg_class'::regclass
            AND sequence_dependency.deptype IN ('a', 'i')
        )
      )
      AND NOT EXISTS (
        SELECT 1 FROM pg_depend d
        WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e'
      )
  LOOP
    CASE item.relkind
      WHEN 'S' THEN EXECUTE format('ALTER SEQUENCE %I.%I OWNER TO %I', item.schema_name, item.object_name, '$owner_role');
      WHEN 'v' THEN EXECUTE format('ALTER VIEW %I.%I OWNER TO %I', item.schema_name, item.object_name, '$owner_role');
      WHEN 'm' THEN EXECUTE format('ALTER MATERIALIZED VIEW %I.%I OWNER TO %I', item.schema_name, item.object_name, '$owner_role');
      WHEN 'f' THEN EXECUTE format('ALTER FOREIGN TABLE %I.%I OWNER TO %I', item.schema_name, item.object_name, '$owner_role');
      ELSE EXECUTE format('ALTER TABLE %I.%I OWNER TO %I', item.schema_name, item.object_name, '$owner_role');
    END CASE;
  END LOOP;
  FOR item IN
    SELECT n.nspname AS schema_name, p.proname,
           pg_get_function_identity_arguments(p.oid) AS args
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname IN ('public', 'crawl_staging')
      AND p.prokind IN ('f', 'p', 'a', 'w')
      AND NOT EXISTS (
        SELECT 1 FROM pg_depend d
        WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e'
      )
  LOOP
    EXECUTE format('ALTER ROUTINE %I.%I(%s) OWNER TO %I', item.schema_name, item.proname, item.args, '$owner_role');
  END LOOP;
  FOR item IN
    SELECT n.nspname AS schema_name, t.typname AS type_name, t.typtype
    FROM pg_type t
    JOIN pg_namespace n ON n.oid = t.typnamespace
    WHERE n.nspname IN ('public', 'crawl_staging')
      AND (
        (
          t.typtype IN ('b', 'd', 'e', 'm', 'r')
          AND NOT EXISTS (
            SELECT 1 FROM pg_depend internal_dep
            WHERE internal_dep.classid = 'pg_type'::regclass
              AND internal_dep.objid = t.oid
              AND internal_dep.deptype = 'i'
          )
        )
        OR (
          t.typtype = 'c'
          AND EXISTS (
            SELECT 1 FROM pg_class composite_class
            WHERE composite_class.oid = t.typrelid
              AND composite_class.relkind = 'c'
          )
        )
      )
      AND NOT EXISTS (
        SELECT 1 FROM pg_depend d
        WHERE d.classid = 'pg_type'::regclass AND d.objid = t.oid AND d.deptype = 'e'
      )
  LOOP
    IF item.typtype = 'd' THEN
      EXECUTE format('ALTER DOMAIN %I.%I OWNER TO %I', item.schema_name, item.type_name, '$owner_role');
    ELSE
      EXECUTE format('ALTER TYPE %I.%I OWNER TO %I', item.schema_name, item.type_name, '$owner_role');
    END IF;
  END LOOP;
  FOR item IN SELECT oid FROM pg_largeobject_metadata LOOP
    EXECUTE format('ALTER LARGE OBJECT %s OWNER TO %I', item.oid, '$owner_role');
  END LOOP;
END \$\$;
SQL
}

backup_restore_database_candidate() {
  local dump_path="$1"
  local candidate_db="$2"
  local owner_role="$3"
  local app_dir="$4"
  local minimum_courses="$5"
  local minimum_branches="$6"
  local role_contract_ok

  sudo -n -u postgres psql -v ON_ERROR_STOP=1 -d postgres -c \
    "CREATE DATABASE \"$candidate_db\" WITH OWNER postgres ALLOW_CONNECTIONS false;"
  BACKUP_CANDIDATE_CREATED=1
  backup_set_database_connections "$candidate_db" false
  sudo -n -u postgres psql -v ON_ERROR_STOP=1 -d postgres <<SQL
REVOKE CONNECT ON DATABASE "$candidate_db" FROM PUBLIC;
DO \$\$
DECLARE role_name text;
BEGIN
  FOREACH role_name IN ARRAY ARRAY['mooncen_api','mooncen_crawler','mooncen_applier','mooncen_ai','mooncen_check','mooncen_readonly'] LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
      EXECUTE format('REVOKE CONNECT ON DATABASE %I FROM %I', '$candidate_db', role_name);
    END IF;
  END LOOP;
END \$\$;
SQL
  backup_set_database_connections "$candidate_db" true
  sudo -n -u postgres pg_restore \
    --dbname="$candidate_db" \
    --no-owner \
    --no-privileges \
    --no-tablespaces \
    --single-transaction \
    --exit-on-error \
    "$dump_path"
  backup_converge_database_ownership "$candidate_db" "$owner_role"
  # The release tree is intentionally not traversable by the postgres OS user.
  # Read the role contract as root and stream it into the privileged psql process.
  sudo -n cat "$app_dir/DB/roles.sql" |
    sudo -n -u postgres psql -v ON_ERROR_STOP=1 -d "$candidate_db"
  sudo -n -u postgres psql -v ON_ERROR_STOP=1 -d "$candidate_db" <<SQL
ALTER DEFAULT PRIVILEGES FOR ROLE "$owner_role" IN SCHEMA public
  GRANT SELECT ON TABLES TO mooncen_readonly;
ALTER DEFAULT PRIVILEGES FOR ROLE "$owner_role" IN SCHEMA public
  GRANT SELECT ON SEQUENCES TO mooncen_readonly;
DO \$\$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'crawl_staging') THEN
    EXECUTE format(
      'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA crawl_staging GRANT SELECT ON TABLES TO mooncen_readonly',
      '$owner_role'
    );
    EXECUTE format(
      'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA crawl_staging GRANT SELECT ON SEQUENCES TO mooncen_readonly',
      '$owner_role'
    );
  END IF;
END \$\$;
SQL

  BACKUP_RESTORED_COURSE_COUNT="$(sudo -n -u postgres psql -v ON_ERROR_STOP=1 -d "$candidate_db" -Atqc 'SELECT COUNT(*) FROM courses;')"
  BACKUP_RESTORED_BRANCH_COUNT="$(sudo -n -u postgres psql -v ON_ERROR_STOP=1 -d "$candidate_db" -Atqc 'SELECT COUNT(*) FROM branches;')"
  BACKUP_RESTORED_LATEST_UPDATE="$(sudo -n -u postgres psql -v ON_ERROR_STOP=1 -d "$candidate_db" -Atqc "SELECT COALESCE(MAX(updated_at)::text, '') FROM courses;")"
  backup_validate_row_count courses "$BACKUP_RESTORED_COURSE_COUNT"
  backup_validate_row_count branches "$BACKUP_RESTORED_BRANCH_COUNT"
  if [ "$BACKUP_RESTORED_COURSE_COUNT" -lt "$minimum_courses" ] || \
     [ "$BACKUP_RESTORED_BRANCH_COUNT" -lt "$minimum_branches" ]; then
    backup_fail "Restore validation counts are below the configured minimums." 72
  fi
  role_contract_ok="$(sudo -n -u postgres psql -v ON_ERROR_STOP=1 -d "$candidate_db" -Atqc "
    SELECT
      has_database_privilege('mooncen_api', current_database(), 'CONNECT')
      AND has_database_privilege('mooncen_crawler', current_database(), 'CONNECT')
      AND has_database_privilege('mooncen_check', current_database(), 'CONNECT')
      AND has_table_privilege('mooncen_api', 'public.courses', 'SELECT')
      AND NOT has_table_privilege('mooncen_api', 'public.courses', 'DELETE')
      AND has_table_privilege('mooncen_crawler', 'public.courses', 'INSERT')
      AND NOT has_table_privilege('mooncen_check', 'public.courses', 'UPDATE');
  ")"
  if [ "$role_contract_ok" != "t" ]; then
    backup_fail "Restored database runtime role contract probe failed." 72
  fi
  backup_set_database_connections "$candidate_db" false
  backup_terminate_database_sessions "$candidate_db"
}

backup_scp_file() {
  local source_path="$1"
  local remote_path="$2"
  backup_validate_remote_path "$remote_path"
  scp -O "${BACKUP_SCP_OPTIONS[@]}" "$source_path" "$BACKUP_USER@$BACKUP_HOST:$remote_path"
}

backup_scp_from_remote() {
  local remote_path="$1"
  local local_path="$2"
  backup_validate_remote_path "$remote_path"
  scp -O "${BACKUP_SCP_OPTIONS[@]}" "$BACKUP_USER@$BACKUP_HOST:$remote_path" "$local_path"
}

backup_scp_dir_contents() {
  local source_dir="$1"
  local remote_dir="$2"
  local items=()
  backup_validate_remote_path "$remote_dir"
  shopt -s nullglob dotglob
  items=("$source_dir"/*)
  shopt -u nullglob dotglob
  if [ "${#items[@]}" -eq 0 ]; then
    return 0
  fi
  scp -O -r "${BACKUP_SCP_OPTIONS[@]}" "${items[@]}" "$BACKUP_USER@$BACKUP_HOST:$remote_dir/"
}
