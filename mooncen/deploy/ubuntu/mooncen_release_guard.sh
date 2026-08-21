#!/usr/bin/env bash
set -euo pipefail

LOCK_DIR_EXPECTED=/opt/.mooncen-deploy.lock
HISTORY_DIR=/opt/.mooncen-release-history
HEARTBEAT_STALE_SECONDS=180
HISTORY_KEEP=5
MUTABLE_ARTIFACT_MANIFEST_NAME=mutable-artifacts.manifest
MUTABLE_ARTIFACT_BACKUP_NAME=mutable-artifacts
SYSTEMD_UNIT_METADATA_NAME=systemd-unit-metadata
SYSTEMD_DROPIN_METADATA_NAME=systemd-dropin-metadata
BOOTSTRAP_MANIFEST_NAME=bootstrap.env
NATIVE_START_AUTH_DIR=/run/mooncen-native-deploy-start
NATIVE_START_AUTH_PATH=$NATIVE_START_AUTH_DIR/authorization.json
RUNTIME_TRANSITION_ROOT=/var/lib/mooncen-runtime-transition
RUNTIME_TRANSITION_LOCK=$RUNTIME_TRANSITION_ROOT/control.lock
NATIVE_BOOTSTRAP_INTENT=$RUNTIME_TRANSITION_ROOT/native-bootstrap-intent.json

# This guard belongs to the full-stack Web/API/DB deployment path. The
# production crawler runtime is deployed independently on gen1crawler, so a
# cloud release recovery must never inspect, stop, restore, enable, or remove
# crawler-owned units or mutable state discovered on the application host.
is_crawler_runtime_unit_name() {
  case "$1" in
    mooncen-crawler*.service|mooncen-crawler*.timer|\
    mooncen-staging-apply*.service|mooncen-staging-apply*.timer|\
    mooncen-crawler*.service.d|mooncen-crawler*.timer.d|\
    mooncen-staging-apply*.service.d|mooncen-staging-apply*.timer.d|\
    mooncen-branch-coordinates.service|mooncen-branch-coordinates.service.d)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

# The dedicated an2p SSH endpoint is the out-of-band transport used to run
# this guard and the surrounding deployment. It is provisioned independently
# of the application release and must remain untouched across switch/recovery.
is_external_control_plane_unit_name() {
  case "$1" in
    mooncen-an2p-deploy-sshd.service|mooncen-an2p-deploy-sshd.service.d)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

# The container controller owns these units outside the native release
# transaction. A native rollback must preserve them exactly and must never
# stop, replace, enable, disable, or replay them from a legacy active journal.
is_container_runtime_unit_name() {
  case "$1" in
    mooncen-container-stack.service|mooncen-container-stack.service.d|\
    mooncen-container-release-guard@*.service|\
    mooncen-container-release-guard@*.service.d)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

build_mutable_artifact_inventory() {
  MUTABLE_ARTIFACT_IDS=(
    env-api env-frontend env-ai env-container-api env-container-ai
    env-container-migrator config-container-runtime env-bot env-applier
    env-functional-test env-gate env-backup db-root-ca postgres-container-hba
    cloudflared-token node-role backup-tmpfiles sudoers-deploy sudoers-bot
    mooncenctl helper-cloudflared-token helper-ops-service helper-ops-action
    helper-postgres-role helper-container-pg-hba helper-native-runtime-condition
    helper-container-bootstrap helper-container-integrity
    helper-container-controller tree-container-controller
    config-container-bootstrap config-container-identity receipt-container-runtime
    tree-bot tree-backup tree-ha nginx-available
    nginx-enabled nginx-default dir-etc-mooncen dir-etc-cloudflared
    dir-local-libexec dir-var-log-mooncen
  )
  MUTABLE_ARTIFACT_PATHS=(
    /etc/mooncen/api.env
    /etc/mooncen/frontend.env
    /etc/mooncen/ai.env
    /etc/mooncen/container-api.env
    /etc/mooncen/container-ai.env
    /etc/mooncen/container-migrator.env
    /etc/mooncen/container-frontend-runtime-config.js
    /etc/mooncen/bot.env
    /etc/mooncen/applier.env
    /etc/mooncen/functional-test.env
    /etc/mooncen/gate.env
    /etc/mooncen/backup.env
    /etc/mooncen/db-root-ca.crt
    /etc/postgresql/16/main/pg_hba.conf
    /etc/cloudflared/token
    /etc/mooncen-node-role
    /etc/tmpfiles.d/mooncen-backup-restore-lock.conf
    /etc/sudoers.d/mooncen-deploy
    /etc/sudoers.d/mooncen-bot-db-status
    /usr/local/bin/mooncenctl
    /usr/local/libexec/mooncen-cloudflared-token
    /usr/local/libexec/mooncen-ops-service
    /usr/local/libexec/mooncen-ops-service-action.py
    /usr/local/libexec/mooncen-postgres-role
    /usr/local/libexec/mooncen-configure-container-pg-hba
    /usr/local/libexec/mooncen-native-runtime-condition
    /usr/local/libexec/mooncen-container-bootstrap
    /usr/local/libexec/production_runtime_integrity.py
    /usr/local/libexec/mooncen-container-release
    /usr/local/libexec/mooncen-container-release-lib
    /etc/mooncen/container-bootstrap.json
    /etc/mooncen/an2p-dev-target-identity
    /etc/mooncen/container-runtime-installation.json
    /usr/local/libexec/mooncen-bot
    /usr/local/libexec/mooncen-backup
    /usr/local/libexec/mooncen-ha
    /etc/nginx/sites-available/mooncen.conf
    /etc/nginx/sites-enabled/mooncen.conf
    /etc/nginx/sites-enabled/default
    /etc/mooncen
    /etc/cloudflared
    /usr/local/libexec
    /var/log/mooncen
  )
  MUTABLE_ARTIFACT_POLICIES=(
    file file file file file file file file file file file file file file-postgres
    file file file file file file file file file file file file file file
    file tree file file file
    tree tree tree file linkable linkable
    metadata-root metadata-root metadata-root metadata-any
  )
  [ "${#MUTABLE_ARTIFACT_IDS[@]}" -eq "${#MUTABLE_ARTIFACT_PATHS[@]}" ] &&
    [ "${#MUTABLE_ARTIFACT_IDS[@]}" -eq "${#MUTABLE_ARTIFACT_POLICIES[@]}" ] ||
    die "mutable artifact inventory is internally inconsistent"
}

die() {
  echo "mooncen release guard: $*" >&2
  exit 65
}

durability_barrier() {
  local path="$1"
  [ -e "$path" ] || [ -L "$path" ] || die "durability barrier path is missing: $path"
  sync -f -- "$path" || die "durability barrier failed: $path"
}

sync_recovery_filesystems() {
  local path
  for path in /opt /etc /usr/local /var/log; do
    [ -d "$path" ] && [ ! -L "$path" ] || die "recovery filesystem anchor is unsafe: $path"
    durability_barrier "$path"
  done
}

current_boot_id() {
  local boot_id
  [ -f /proc/sys/kernel/random/boot_id ] && [ ! -L /proc/sys/kernel/random/boot_id ] ||
    die "kernel boot identifier is unavailable or unsafe"
  boot_id="$(cat /proc/sys/kernel/random/boot_id)"
  [[ "$boot_id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] ||
    die "kernel boot identifier is invalid"
  printf '%s\n' "$boot_id"
}

validate_token() {
  [[ "$1" =~ ^[0-9a-f]{32}$ ]] || die "invalid deployment token"
}

clear_native_bootstrap_intent() {
  local token="$1" expected
  validate_token "$token"
  if [ ! -e "$RUNTIME_TRANSITION_ROOT" ] && [ ! -L "$RUNTIME_TRANSITION_ROOT" ]; then
    return 0
  fi
  [ -d "$RUNTIME_TRANSITION_ROOT" ] && [ ! -L "$RUNTIME_TRANSITION_ROOT" ] &&
    [ "$(stat -c '%U:%G:%a' "$RUNTIME_TRANSITION_ROOT")" = root:root:700 ] ||
    die "runtime transition directory is unsafe"
  [ -f "$RUNTIME_TRANSITION_LOCK" ] && [ ! -L "$RUNTIME_TRANSITION_LOCK" ] &&
    [ "$(stat -c '%U:%G:%a' "$RUNTIME_TRANSITION_LOCK")" = root:root:600 ] ||
    die "runtime transition lock is unsafe"
  exec 8<>"$RUNTIME_TRANSITION_LOCK"
  flock -x 8
  if [ ! -e "$NATIVE_BOOTSTRAP_INTENT" ] && [ ! -L "$NATIVE_BOOTSTRAP_INTENT" ]; then
    flock -u 8
    exec 8>&-
    return 0
  fi
  [ -f "$NATIVE_BOOTSTRAP_INTENT" ] && [ ! -L "$NATIVE_BOOTSTRAP_INTENT" ] &&
    [ "$(stat -c '%U:%G:%a' "$NATIVE_BOOTSTRAP_INTENT")" = root:root:600 ] ||
    die "native bootstrap intent is unsafe"
  expected="{\"schema_version\":1,\"token\":\"${token}\"}"
  [ "$(cat "$NATIVE_BOOTSTRAP_INTENT")" = "$expected" ] ||
    die "native bootstrap intent token mismatch"
  rm -f -- "$NATIVE_BOOTSTRAP_INTENT"
  durability_barrier "$RUNTIME_TRANSITION_ROOT"
  flock -u 8
  exec 8>&-
}

end_native_intent() {
  local token="$1"
  local controller=/usr/local/libexec/mooncen-container-release
  local output expected
  validate_token "$token"
  if [ ! -e "$controller" ] && [ ! -L "$controller" ]; then
    [ ! -e /etc/mooncen/container-runtime-installation.json ] &&
      [ ! -L /etc/mooncen/container-runtime-installation.json ] &&
      [ ! -e /var/lib/mooncen-container-release ] &&
      [ ! -L /var/lib/mooncen-container-release ] ||
      die "container runtime state exists without its root controller"
    clear_native_bootstrap_intent "$token"
    return 0
  fi
  [ -f "$controller" ] && [ ! -L "$controller" ] &&
    [ "$(stat -c '%U:%G:%a' "$controller")" = root:root:755 ] ||
    die "container controller is unavailable or unsafe"
  output="$("$controller" native-end "$token")" ||
    die "native deployment intent could not be released"
  expected="{\"ended\":true,\"schema_version\":1,\"token\":\"${token}\"}"
  [ "$output" = "$expected" ] && [[ "$output" != *$'\n'* ]] ||
    die "native deployment intent release output is invalid"
}

validate_native_start_authorization() {
  local expected_token="$1"
  validate_token "$expected_token"
  [ -d "$NATIVE_START_AUTH_DIR" ] && [ ! -L "$NATIVE_START_AUTH_DIR" ] &&
    [ "$(stat -c '%U:%G:%a' "$NATIVE_START_AUTH_DIR")" = root:root:700 ] ||
    die "native start authorization directory is unsafe"
  [ -f "$NATIVE_START_AUTH_PATH" ] && [ ! -L "$NATIVE_START_AUTH_PATH" ] &&
    [ "$(stat -c '%U:%G:%a' "$NATIVE_START_AUTH_PATH")" = root:root:600 ] ||
    die "native start authorization is unsafe"
  /usr/bin/python3 -I - "$NATIVE_START_AUTH_PATH" "$expected_token" <<'PY'
import json
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
expected = sys.argv[2]
payload = path.read_bytes()
if not payload or len(payload) > 4096:
    raise SystemExit(65)

def _pairs(pairs):
    result = {}
    for key, item in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = item
    return result

try:
    value = json.loads(payload.decode("ascii"), object_pairs_hook=lambda pairs: _pairs(pairs))
except (UnicodeError, ValueError, TypeError):
    raise SystemExit(65)
if not isinstance(value, dict) or set(value) != {
    "arm_boot_id", "arm_deadline_epoch", "authorization_boot_id",
    "authorization_deadline_epoch", "guard_token", "intent_token", "mode",
    "phase", "schema_version"
}:
    raise SystemExit(65)
canonical = (json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                        separators=(",", ":")) + "\n").encode("ascii")
if payload != canonical or value.get("schema_version") != 1:
    raise SystemExit(65)
if value.get("guard_token") != expected:
    raise SystemExit(65)
if not re.fullmatch(r"[0-9a-f]{32}", str(value.get("intent_token", ""))):
    raise SystemExit(65)
if not re.fullmatch(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    str(value.get("arm_boot_id", "")),
) or not re.fullmatch(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    str(value.get("authorization_boot_id", "")),
):
    raise SystemExit(65)
for key in ("arm_deadline_epoch", "authorization_deadline_epoch"):
    if type(value.get(key)) is not int or value[key] < 1_000_000_000:
        raise SystemExit(65)
if (value.get("mode"), value.get("phase")) not in {
    ("candidate", "activated"),
    ("recovery", "recovering"),
    ("recovery", "recovering_prepared"),
}:
    raise SystemExit(65)
PY
}

clear_native_start_authorization() {
  local expected_token="$1"
  validate_token "$expected_token"
  if [ ! -e "$NATIVE_START_AUTH_DIR" ] && [ ! -L "$NATIVE_START_AUTH_DIR" ]; then
    return 0
  fi
  [ -d "$NATIVE_START_AUTH_DIR" ] && [ ! -L "$NATIVE_START_AUTH_DIR" ] &&
    [ "$(stat -c '%U:%G:%a' "$NATIVE_START_AUTH_DIR")" = root:root:700 ] ||
    die "native start authorization directory is unsafe"
  if [ ! -e "$NATIVE_START_AUTH_PATH" ] && [ ! -L "$NATIVE_START_AUTH_PATH" ]; then
    return 0
  fi
  validate_native_start_authorization "$expected_token"
  rm -f -- "$NATIVE_START_AUTH_PATH"
  durability_barrier "$NATIVE_START_AUTH_DIR"
}

publish_native_start_authorization() {
  local lock_dir="$1" token="$2" mode="$3"
  local expected_phase guard_state temporary authorization_boot_id
  local authorization_deadline_epoch now
  validate_token "$token"
  validate_lock "$lock_dir" "$token"
  load_journal "$lock_dir" "$token"
  case "$mode" in
    candidate) expected_phase=activated ;;
    recovery)
      case "$PHASE" in
        recovering|recovering_prepared) expected_phase="$PHASE" ;;
        *) die "native recovery start authorization phase is unsafe" ;;
      esac
      ;;
    *) die "native start authorization mode is invalid" ;;
  esac
  [ "$PHASE" = "$expected_phase" ] ||
    die "native start authorization phase is unsafe"
  authorization_boot_id="$(current_boot_id)"
  now="$(date +%s)"
  authorization_deadline_epoch=$((now + 120))
  if [ "$mode" = candidate ]; then
    [ "$authorization_boot_id" = "$ARM_BOOT_ID" ] &&
      [ "$now" -lt "$DEADLINE_EPOCH" ] ||
      die "native candidate start authorization is stale"
    if [ "$DEADLINE_EPOCH" -lt "$authorization_deadline_epoch" ]; then
      authorization_deadline_epoch="$DEADLINE_EPOCH"
    fi
  fi
  guard_state="$(systemctl show "mooncen-deploy-guard@${token}.service" \
    -p ActiveState --value)"
  if [ "$mode" = candidate ]; then
    [ "$guard_state" = active ] || die "deployment guard is not active"
  else
    case "$guard_state" in active|activating) ;; *) die "deployment guard is not running" ;; esac
  fi
  if [ -e "$NATIVE_START_AUTH_DIR" ] || [ -L "$NATIVE_START_AUTH_DIR" ]; then
    [ -d "$NATIVE_START_AUTH_DIR" ] && [ ! -L "$NATIVE_START_AUTH_DIR" ] &&
      [ "$(stat -c '%U:%G:%a' "$NATIVE_START_AUTH_DIR")" = root:root:700 ] ||
      die "native start authorization directory is unsafe"
  else
    install -d -o root -g root -m 0700 "$NATIVE_START_AUTH_DIR"
  fi
  clear_native_start_authorization "$token"
  temporary="$NATIVE_START_AUTH_DIR/.authorization.$$.tmp"
  [ ! -e "$temporary" ] && [ ! -L "$temporary" ] ||
    die "native start authorization staging path already exists"
  printf '{"arm_boot_id":"%s","arm_deadline_epoch":%s,"authorization_boot_id":"%s","authorization_deadline_epoch":%s,"guard_token":"%s","intent_token":"%s","mode":"%s","phase":"%s","schema_version":1}\n' \
    "$ARM_BOOT_ID" "$DEADLINE_EPOCH" "$authorization_boot_id" \
    "$authorization_deadline_epoch" "$token" "$NATIVE_INTENT_TOKEN" \
    "$mode" "$PHASE" > "$temporary"
  chown root:root "$temporary"
  chmod 0600 "$temporary"
  durability_barrier "$temporary"
  mv -fT -- "$temporary" "$NATIVE_START_AUTH_PATH"
  durability_barrier "$NATIVE_START_AUTH_DIR"
  validate_native_start_authorization "$token"
}

restore_active_units_authorized() {
  local lock_dir="$1" token="$2" result=0
  publish_native_start_authorization "$lock_dir" "$token" recovery
  ( restore_active_units "$lock_dir" ) || result=$?
  clear_native_start_authorization "$token"
  [ "$result" -eq 0 ] || return "$result"
}

validate_lock() {
  local lock_dir="$1"
  local token="$2"
  [ "$lock_dir" = "$LOCK_DIR_EXPECTED" ] || die "unexpected deployment lock path"
  [ -d "$lock_dir" ] && [ ! -L "$lock_dir" ] || die "deployment lock is missing or unsafe"
  [ "$(stat -c '%U:%G:%a' "$lock_dir")" = "root:root:700" ] ||
    die "deployment lock ownership or mode is unsafe"
  [ -f "$lock_dir/token" ] && [ ! -L "$lock_dir/token" ] ||
    die "deployment lock token is missing or unsafe"
  [ "$(cat "$lock_dir/token")" = "$token" ] || die "deployment lock token mismatch"
}

journal_value() {
  local journal="$1"
  local key="$2"
  awk -F= -v wanted="$key" '
    $1 == wanted { count++; value=substr($0, length(wanted) + 2) }
    END { if (count != 1) exit 1; print value }
  ' "$journal"
}

load_journal() {
  local lock_dir="$1"
  local expected_token="$2"
  JOURNAL="$lock_dir/journal.env"
  [ -f "$JOURNAL" ] && [ ! -L "$JOURNAL" ] || die "deployment journal is missing or unsafe"
  [ "$(stat -c '%U:%G:%a' "$JOURNAL")" = "root:root:600" ] ||
    die "deployment journal ownership or mode is unsafe"
  TOKEN="$(journal_value "$JOURNAL" TOKEN)" || die "deployment journal token is invalid"
  PHASE="$(journal_value "$JOURNAL" PHASE)" || die "deployment journal phase is invalid"
  REMOTE_DIR="$(journal_value "$JOURNAL" REMOTE_DIR)" || die "deployment journal app path is invalid"
  RELEASE_DIR="$(journal_value "$JOURNAL" RELEASE_DIR)" || die "deployment journal release path is invalid"
  PREVIOUS_DIR="$(journal_value "$JOURNAL" PREVIOUS_DIR)" || die "deployment journal previous path is invalid"
  FAILED_DIR="$(journal_value "$JOURNAL" FAILED_DIR)" || die "deployment journal failed path is invalid"
  HEARTBEAT="$(journal_value "$JOURNAL" HEARTBEAT)" || die "deployment journal heartbeat path is invalid"
  EXPECTED_COMMIT="$(journal_value "$JOURNAL" EXPECTED_COMMIT)" || die "deployment journal commit is invalid"
  HAD_ACTIVE="$(journal_value "$JOURNAL" HAD_ACTIVE)" || die "deployment journal active-release state is invalid"
  ARM_BOOT_ID="$(journal_value "$JOURNAL" ARM_BOOT_ID)" || die "deployment journal boot identifier is invalid"
  DEADLINE_EPOCH="$(journal_value "$JOURNAL" DEADLINE_EPOCH)" || die "deployment journal deadline is invalid"
  NATIVE_INTENT_TOKEN="$(journal_value "$JOURNAL" NATIVE_INTENT_TOKEN)" ||
    die "deployment journal native intent is invalid"

  [ "$TOKEN" = "$expected_token" ] || die "deployment journal token mismatch"
  [ "$REMOTE_DIR" = "/opt/mooncen" ] || die "deployment journal app path is unsafe"
  [[ "$RELEASE_DIR" =~ ^/opt/\.mooncen-release-${TOKEN}$ ]] || die "deployment journal release path is unsafe"
  [[ "$PREVIOUS_DIR" =~ ^/opt/\.mooncen-previous-${TOKEN}$ ]] || die "deployment journal previous path is unsafe"
  [[ "$FAILED_DIR" =~ ^/opt/\.mooncen-failed-${TOKEN}$ ]] || die "deployment journal failed path is unsafe"
  [ "$HEARTBEAT" = "/opt/.mooncen-deploy-heartbeat-${TOKEN}" ] || die "deployment heartbeat path is unsafe"
  [[ "$EXPECTED_COMMIT" =~ ^([0-9a-f]{40}|[0-9a-f]{64})$ ]] || die "deployment commit is unsafe"
  [[ "$HAD_ACTIVE" =~ ^[01]$ ]] || die "deployment active-release state is unsafe"
  [[ "$ARM_BOOT_ID" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] ||
    die "deployment journal boot identifier is unsafe"
  [[ "$DEADLINE_EPOCH" =~ ^[0-9]{10,12}$ ]] || die "deployment deadline is unsafe"
  validate_token "$NATIVE_INTENT_TOKEN"
}

set_phase() {
  local lock_dir="$1"
  local token="$2"
  local phase="$3"
  case "$phase" in
    prepared|activating|activated|verified|recovering|recovering_prepared|committing|finalizing_abort|finalizing_recovery|finalizing_commit|committed|aborted|recovered|recovery_failed) ;;
    *) die "invalid deployment phase" ;;
  esac
  validate_lock "$lock_dir" "$token"
  load_journal "$lock_dir" "$token"
  clear_native_start_authorization "$token"
  local temporary="$lock_dir/.journal.$$.tmp"
  awk -F= -v phase="$phase" '
    BEGIN { changed=0 }
    $1 == "PHASE" { print "PHASE=" phase; changed++; next }
    { print }
    END { if (changed != 1) exit 1 }
  ' "$JOURNAL" > "$temporary"
  chown root:root "$temporary"
  chmod 0600 "$temporary"
  durability_barrier "$temporary"
  mv -fT -- "$temporary" "$JOURNAL"
  durability_barrier "$lock_dir"
  PHASE="$phase"
}

history_entry() {
  local token="$1"
  if [ -e "$HISTORY_DIR" ] || [ -L "$HISTORY_DIR" ]; then
    [ -d "$HISTORY_DIR" ] && [ ! -L "$HISTORY_DIR" ] &&
      [ "$(stat -c '%U:%G:%a' "$HISTORY_DIR")" = "root:root:700" ] ||
      die "release history directory is unsafe"
  else
    install -d -o root -g root -m 0700 "$HISTORY_DIR"
  fi
  printf '%s/%s\n' "$HISTORY_DIR" "$token"
}

prepare_history_entry() {
  local token="$1"
  local entry
  entry="$(history_entry "$token")"
  if [ -e "$entry" ] || [ -L "$entry" ]; then
    [ -d "$entry" ] && [ ! -L "$entry" ] &&
      [ "$(stat -c '%U:%G:%a' "$entry")" = "root:root:700" ] ||
      die "release history entry is unsafe"
  else
    install -d -o root -g root -m 0700 "$entry"
  fi
  printf '%s\n' "$entry"
}

preserve_directory() {
  local source="$1"
  local destination="$2"
  if [ ! -e "$source" ] && [ ! -L "$source" ]; then
    if [ -e "$destination" ] || [ -L "$destination" ]; then
      [ -d "$destination" ] && [ ! -L "$destination" ] ||
        die "preserved recovery destination is unsafe: $destination"
    fi
    return 0
  fi
  [ -d "$source" ] && [ ! -L "$source" ] || die "recovery source is not a regular directory: $source"
  [ ! -e "$destination" ] && [ ! -L "$destination" ] || die "recovery destination already exists: $destination"
  mv -- "$source" "$destination"
}

preserve_file() {
  local source="$1"
  local destination="$2"
  if [ ! -e "$source" ] && [ ! -L "$source" ]; then
    [ -f "$destination" ] && [ ! -L "$destination" ] ||
      die "preserved recovery file is unavailable or unsafe: $destination"
    return 0
  fi
  [ -f "$source" ] && [ ! -L "$source" ] ||
    die "recovery source is not a regular file: $source"
  [ ! -e "$destination" ] && [ ! -L "$destination" ] ||
    die "recovery destination already exists: $destination"
  mv -- "$source" "$destination"
}

mutable_tree_digest() {
  local directory="$1"
  LC_ALL=C tar --sort=name --numeric-owner --format=gnu \
    -cf - -C "$directory" . | sha256sum | awk '{print $1}'
}

validate_mutable_source() {
  local path="$1"
  local policy="$2"
  local actual_mode
  if [ ! -e "$path" ] && [ ! -L "$path" ]; then
    return 0
  fi
  case "$policy" in
    file|file-postgres)
      [ -f "$path" ] && [ ! -L "$path" ] || die "managed mutable file path is unsafe: $path"
      ;;
    tree)
      [ -d "$path" ] && [ ! -L "$path" ] || die "managed mutable tree path is unsafe: $path"
      if find "$path" -xdev -type l -print -quit | grep -q . ||
         find "$path" -xdev ! -type d ! -type f -print -quit | grep -q .; then
        die "managed mutable tree contains an unsupported file type: $path"
      fi
      if find "$path" -xdev ! -user root -print -quit | grep -q . ||
         find "$path" -xdev -perm /022 -print -quit | grep -q .; then
        die "managed mutable tree ownership or mode is unsafe: $path"
      fi
      ;;
    linkable)
      if [ -L "$path" ]; then
        local link_target
        link_target="$(readlink -- "$path")"
        [[ "$link_target" =~ ^(/etc/nginx/sites-available/|\.\./sites-available/)[A-Za-z0-9._-]+$ ]] ||
          die "managed nginx symlink target is unsafe: $path"
      else
        [ -f "$path" ] || die "managed link-capable path is unsafe: $path"
      fi
      ;;
    metadata-root|metadata-any)
      [ -d "$path" ] && [ ! -L "$path" ] || die "managed metadata directory is unsafe: $path"
      ;;
    *) die "unknown mutable artifact policy" ;;
  esac
  if [ "$policy" = file-postgres ]; then
    [ "$path" = /etc/postgresql/16/main/pg_hba.conf ] &&
      [ "$(stat -c '%U:%G:%a' -- "$path")" = postgres:postgres:640 ] ||
      die "managed PostgreSQL HBA metadata is unsafe: $path"
  elif [ "$policy" != metadata-any ]; then
    [ "$(stat -c '%u' -- "$path")" = "0" ] || die "managed mutable artifact is not root-owned: $path"
  fi
  if [ ! -L "$path" ]; then
    actual_mode="$(stat -c '%a' -- "$path")"
    (( (8#$actual_mode & 8#022) == 0 )) || die "managed mutable artifact mode is unsafe: $path"
  fi
}

mutable_manifest_record() {
  local manifest="$1"
  local wanted_id="$2"
  awk -F'|' -v wanted="$wanted_id" '
    $1 == wanted { count++; record=$0 }
    END { if (count != 1) exit 1; print record }
  ' "$manifest"
}

systemd_metadata_record() {
  local manifest="$1"
  local wanted_name="$2"
  awk -F'|' -v wanted="$wanted_name" '
    $1 == wanted { count++; record=$0 }
    END { if (count != 1) exit 1; print record }
  ' "$manifest"
}

backup_mutable_artifacts() {
  local lock_dir="$1"
  local backup_dir="$lock_dir/$MUTABLE_ARTIFACT_BACKUP_NAME"
  local manifest="$lock_dir/$MUTABLE_ARTIFACT_MANIFEST_NAME"
  local index id path policy state uid gid mode digest stored
  build_mutable_artifact_inventory
  install -d -o root -g root -m 0700 "$backup_dir"
  install -o root -g root -m 0600 /dev/null "$manifest"
  for index in "${!MUTABLE_ARTIFACT_IDS[@]}"; do
    id="${MUTABLE_ARTIFACT_IDS[$index]}"
    path="${MUTABLE_ARTIFACT_PATHS[$index]}"
    policy="${MUTABLE_ARTIFACT_POLICIES[$index]}"
    validate_mutable_source "$path" "$policy"
    if [ ! -e "$path" ] && [ ! -L "$path" ]; then
      printf '%s|absent|-|-|-|-\n' "$id" >> "$manifest"
      continue
    fi
    uid="$(stat -c '%u' -- "$path")"
    gid="$(stat -c '%g' -- "$path")"
    mode="$(stat -c '%a' -- "$path")"
    if [ "$policy" = metadata-root ] || [ "$policy" = metadata-any ]; then
      # These parent/state directories may contain durable data that is not
      # owned by a release. Snapshot only their existence and metadata.
      printf '%s|metadata|%s|%s|%s|-\n' "$id" "$uid" "$gid" "$mode" >> "$manifest"
      continue
    elif [ -L "$path" ]; then
      state=symlink
      digest="$(readlink -- "$path" | sha256sum | awk '{print $1}')"
    elif [ -d "$path" ]; then
      state=tree
      digest="$(mutable_tree_digest "$path")"
    else
      state=file
      digest="$(sha256sum "$path" | awk '{print $1}')"
    fi
    [[ "$digest" =~ ^[0-9a-f]{64}$ ]] || die "managed mutable artifact digest failed: $id"
    stored="$backup_dir/$id"
    cp -a -- "$path" "$stored"
    printf '%s|%s|%s|%s|%s|%s\n' "$id" "$state" "$uid" "$gid" "$mode" "$digest" >> "$manifest"
  done
}

validate_mutable_artifact_backup() {
  local lock_dir="$1"
  local backup_dir="$lock_dir/$MUTABLE_ARTIFACT_BACKUP_NAME"
  local manifest="$lock_dir/$MUTABLE_ARTIFACT_MANIFEST_NAME"
  local expected_count index id path policy record state uid gid mode digest stored actual
  build_mutable_artifact_inventory
  [ -d "$backup_dir" ] && [ ! -L "$backup_dir" ] || die "mutable artifact backup directory is unsafe"
  [ "$(stat -c '%U:%G:%a' "$backup_dir")" = "root:root:700" ] ||
    die "mutable artifact backup directory ownership or mode is unsafe"
  [ -f "$manifest" ] && [ ! -L "$manifest" ] || die "mutable artifact manifest is unsafe"
  [ "$(stat -c '%U:%G:%a' "$manifest")" = "root:root:600" ] ||
    die "mutable artifact manifest ownership or mode is unsafe"
  expected_count="${#MUTABLE_ARTIFACT_IDS[@]}"
  [ "$(wc -l < "$manifest")" -eq "$expected_count" ] || die "mutable artifact manifest size is invalid"
  for index in "${!MUTABLE_ARTIFACT_IDS[@]}"; do
    id="${MUTABLE_ARTIFACT_IDS[$index]}"
    path="${MUTABLE_ARTIFACT_PATHS[$index]}"
    policy="${MUTABLE_ARTIFACT_POLICIES[$index]}"
    record="$(mutable_manifest_record "$manifest" "$id")" || die "mutable artifact manifest entry is invalid: $id"
    IFS='|' read -r _ state uid gid mode digest <<< "$record"
    stored="$backup_dir/$id"
    case "$state" in
      absent)
        [ "$uid|$gid|$mode|$digest" = "-|-|-|-" ] || die "absent mutable artifact metadata is invalid: $id"
        [ ! -e "$stored" ] && [ ! -L "$stored" ] || die "absent mutable artifact has unexpected backup: $id"
        ;;
      file)
        [ "$policy" = file ] || [ "$policy" = file-postgres ] ||
          [ "$policy" = linkable ] || die "mutable file policy mismatch: $id"
        [ -f "$stored" ] && [ ! -L "$stored" ] || die "mutable file backup is unsafe: $id"
        actual="$(sha256sum "$stored" | awk '{print $1}')"
        ;;
      tree)
        [ "$policy" = tree ] || die "mutable tree policy mismatch: $id"
        validate_mutable_source "$stored" tree
        actual="$(mutable_tree_digest "$stored")"
        ;;
      symlink)
        [ "$policy" = linkable ] && [ -L "$stored" ] || die "mutable symlink backup is unsafe: $id"
        validate_mutable_source "$stored" linkable
        actual="$(readlink -- "$stored" | sha256sum | awk '{print $1}')"
        ;;
      metadata)
        { [ "$policy" = metadata-root ] || [ "$policy" = metadata-any ]; } ||
          die "mutable metadata policy mismatch: $id"
        [ ! -e "$stored" ] && [ ! -L "$stored" ] ||
          die "metadata-only mutable artifact has unexpected backup: $id"
        [[ "$uid" =~ ^[0-9]+$ && "$gid" =~ ^[0-9]+$ && "$mode" =~ ^[0-7]{3,4}$ ]] &&
          [ "$digest" = - ] || die "mutable directory metadata is invalid: $id"
        if [ "$policy" = metadata-root ]; then
          [ "$uid" = 0 ] || die "mutable metadata directory is not root-owned: $id"
        fi
        (( (8#$mode & 8#022) == 0 )) || die "mutable metadata directory mode is unsafe: $id"
        ;;
      *) die "mutable artifact manifest state is invalid: $id" ;;
    esac
    if [ "$state" != absent ] && [ "$state" != metadata ]; then
      [[ "$uid" =~ ^[0-9]+$ && "$gid" =~ ^[0-9]+$ && "$mode" =~ ^[0-7]{3,4}$ && "$digest" =~ ^[0-9a-f]{64}$ ]] ||
        die "mutable artifact metadata is invalid: $id"
      [ "$(stat -c '%u:%g:%a' -- "$stored")" = "$uid:$gid:$mode" ] ||
        die "mutable artifact backup metadata mismatch: $id"
      [ "$actual" = "$digest" ] || die "mutable artifact backup digest mismatch: $id"
    fi
  done
}

validate_restored_mutable_artifacts() {
  local lock_dir="$1"
  local manifest="$lock_dir/$MUTABLE_ARTIFACT_MANIFEST_NAME"
  local index id path policy record state uid gid mode digest actual
  build_mutable_artifact_inventory
  for index in "${!MUTABLE_ARTIFACT_IDS[@]}"; do
    id="${MUTABLE_ARTIFACT_IDS[$index]}"
    path="${MUTABLE_ARTIFACT_PATHS[$index]}"
    policy="${MUTABLE_ARTIFACT_POLICIES[$index]}"
    record="$(mutable_manifest_record "$manifest" "$id")" ||
      die "mutable artifact manifest entry is invalid after restore: $id"
    IFS='|' read -r _ state uid gid mode digest <<< "$record"
    case "$state" in
      absent)
        [ ! -e "$path" ] && [ ! -L "$path" ] ||
          die "restored mutable artifact should be absent: $id"
        continue
        ;;
      metadata)
        [ "$policy" = metadata-root ] || [ "$policy" = metadata-any ] ||
          die "restored mutable directory policy mismatch: $id"
        [ -d "$path" ] && [ ! -L "$path" ] ||
          die "restored mutable metadata directory is unsafe: $id"
        actual=-
        ;;
      file)
        [ -f "$path" ] && [ ! -L "$path" ] ||
          die "restored mutable file is unsafe: $id"
        actual="$(sha256sum "$path" | awk '{print $1}')"
        ;;
      tree)
        [ -d "$path" ] && [ ! -L "$path" ] ||
          die "restored mutable tree is unsafe: $id"
        actual="$(mutable_tree_digest "$path")"
        ;;
      symlink)
        [ -L "$path" ] || die "restored mutable symlink is unsafe: $id"
        actual="$(readlink -- "$path" | sha256sum | awk '{print $1}')"
        ;;
      *) die "restored mutable artifact state is invalid: $id" ;;
    esac
    validate_mutable_source "$path" "$policy"
    [ "$(stat -c '%u:%g:%a' -- "$path")" = "$uid:$gid:$mode" ] ||
      die "restored mutable artifact metadata mismatch: $id"
    [ "$actual" = "$digest" ] || die "restored mutable artifact digest mismatch: $id"
  done
}

reload_restored_postgresql_hba() {
  local -a result=()
  local output
  [ -f /etc/postgresql/16/main/pg_hba.conf ] &&
    [ ! -L /etc/postgresql/16/main/pg_hba.conf ] ||
    die "restored PostgreSQL HBA file is unavailable or unsafe"
  output="$(
    /usr/sbin/runuser -u postgres -- /usr/bin/psql \
      -X --no-password --set=ON_ERROR_STOP=1 -At -d postgres \
      -c "SELECT current_setting('hba_file'); SELECT pg_reload_conf(); SELECT count(*) FROM pg_hba_file_rules WHERE error IS NOT NULL;" \
      2>/dev/null
  )" || die "restored PostgreSQL HBA configuration could not be reloaded"
  mapfile -t result <<< "$output"
  [ "${#result[@]}" -eq 3 ] &&
    [ "${result[0]}" = /etc/postgresql/16/main/pg_hba.conf ] &&
    [ "${result[1]}" = t ] &&
    [ "${result[2]}" = 0 ] ||
    die "restored PostgreSQL HBA configuration is not effective"
}

restored_postgresql_hba_is_managed() {
  local path
  build_mutable_artifact_inventory
  for path in "${MUTABLE_ARTIFACT_PATHS[@]}"; do
    [ "$path" = /etc/postgresql/16/main/pg_hba.conf ] && return 0
  done
  return 1
}

cleanup_mutable_restore_transients() {
  local stage="$1"
  local old="$2"
  local policy="$3"
  local id="$4"
  if [ -e "$old" ] || [ -L "$old" ]; then
    [ "$policy" = tree ] || die "unexpected mutable restore old path: $id"
    validate_mutable_source "$old" tree
    rm -rf -- "$old"
  fi
  if [ -e "$stage" ] || [ -L "$stage" ]; then
    case "$policy" in
      tree)
        validate_mutable_source "$stage" tree
        rm -rf -- "$stage"
        ;;
      file|file-postgres|linkable)
        validate_mutable_source "$stage" "$policy"
        rm -f -- "$stage"
        ;;
      metadata-root|metadata-any)
        validate_mutable_source "$stage" "$policy"
        rmdir -- "$stage" || die "mutable metadata staging directory is not empty: $id"
        ;;
      *) die "unknown mutable restore transient policy" ;;
    esac
  fi
}

restore_mutable_artifacts() {
  local lock_dir="$1"
  local backup_dir="$lock_dir/$MUTABLE_ARTIFACT_BACKUP_NAME"
  local manifest="$lock_dir/$MUTABLE_ARTIFACT_MANIFEST_NAME"
  local index id path policy record state uid gid mode digest stored parent stage old
  validate_mutable_artifact_backup "$lock_dir"
  # Validate every current destination before changing any of them.
  for index in "${!MUTABLE_ARTIFACT_IDS[@]}"; do
    path="${MUTABLE_ARTIFACT_PATHS[$index]}"
    policy="${MUTABLE_ARTIFACT_POLICIES[$index]}"
    validate_mutable_source "$path" "$policy"
  done

  # Re-create metadata-only parents first when they existed at arm time. Their
  # exact ownership and mode are applied after their fixed children are back.
  for index in "${!MUTABLE_ARTIFACT_IDS[@]}"; do
    policy="${MUTABLE_ARTIFACT_POLICIES[$index]}"
    case "$policy" in metadata-root|metadata-any) ;; *) continue ;; esac
    id="${MUTABLE_ARTIFACT_IDS[$index]}"
    path="${MUTABLE_ARTIFACT_PATHS[$index]}"
    record="$(mutable_manifest_record "$manifest" "$id")"
    IFS='|' read -r _ state uid gid mode digest <<< "$record"
    [ "$state" = metadata ] || continue
    if [ ! -e "$path" ] && [ ! -L "$path" ]; then
      parent="$(dirname "$path")"
      [ -d "$parent" ] && [ ! -L "$parent" ] ||
        die "mutable metadata parent directory is unsafe: $id"
      stage="$parent/.mooncen-guard-${TOKEN}-${id}.staged"
      cleanup_mutable_restore_transients "$stage" "$parent/.mooncen-guard-${TOKEN}-${id}.old" "$policy" "$id"
      [ ! -e "$stage" ] && [ ! -L "$stage" ] ||
        die "mutable metadata restore staging path already exists: $id"
      if ! mkdir -- "$stage"; then
        die "mutable metadata restore staging failed: $id"
      fi
      if ! chown "$uid:$gid" "$stage" || ! chmod "$mode" "$stage" || ! mv -T -- "$stage" "$path"; then
        rmdir -- "$stage" >/dev/null 2>&1 || true
        die "mutable metadata directory restore failed: $id"
      fi
    fi
  done

  # Restore only fixed leaf artifacts and exact helper trees in this pass.
  for index in "${!MUTABLE_ARTIFACT_IDS[@]}"; do
    id="${MUTABLE_ARTIFACT_IDS[$index]}"
    path="${MUTABLE_ARTIFACT_PATHS[$index]}"
    policy="${MUTABLE_ARTIFACT_POLICIES[$index]}"
    case "$policy" in metadata-root|metadata-any) continue ;; esac
    record="$(mutable_manifest_record "$manifest" "$id")"
    IFS='|' read -r _ state uid gid mode digest <<< "$record"
    stored="$backup_dir/$id"
    parent="$(dirname "$path")"
    if [ "$state" = absent ] && [ ! -e "$parent" ] && [ ! -L "$parent" ]; then
      [ ! -e "$path" ] && [ ! -L "$path" ] ||
        die "absent mutable artifact exists without its parent: $id"
      # A prior recovery attempt may already have removed an originally absent
      # metadata-only parent. That is the desired state and must be retryable.
      continue
    fi
    [ -d "$parent" ] && [ ! -L "$parent" ] || die "mutable artifact parent directory is unsafe: $id"
    stage="$parent/.mooncen-guard-${TOKEN}-${id}.staged"
    old="$parent/.mooncen-guard-${TOKEN}-${id}.old"
    cleanup_mutable_restore_transients "$stage" "$old" "$policy" "$id"
    [ ! -e "$stage" ] && [ ! -L "$stage" ] && [ ! -e "$old" ] && [ ! -L "$old" ] ||
      die "mutable artifact restore staging path already exists: $id"
    if [ "$state" = absent ]; then
      if [ "$policy" = tree ]; then
        rm -rf -- "$path"
      else
        rm -f -- "$path"
      fi
      continue
    fi
    if ! cp -a -- "$stored" "$stage"; then
      if [ -d "$stage" ] && [ ! -L "$stage" ]; then
        rm -rf -- "$stage"
      else
        rm -f -- "$stage"
      fi
      die "mutable artifact restore staging failed: $id"
    fi
    if [ "$state" = tree ] && [ -d "$path" ] && [ ! -L "$path" ]; then
      if ! mv -T -- "$path" "$old"; then
        rm -rf -- "$stage"
        die "mutable artifact current tree preservation failed: $id"
      fi
      if ! mv -T -- "$stage" "$path"; then
        mv -T -- "$old" "$path" || true
        rm -rf -- "$stage"
        die "mutable artifact tree restore failed: $id"
      fi
      rm -rf -- "$old"
    elif ! mv -fT -- "$stage" "$path"; then
      if [ -d "$stage" ] && [ ! -L "$stage" ]; then
        rm -rf -- "$stage"
      else
        rm -f -- "$stage"
      fi
      die "mutable artifact restore activation failed: $id"
    fi
  done

  # Metadata-only directories are deliberately last. An originally absent
  # directory is removed only if the fixed-child restore left it empty.
  for index in "${!MUTABLE_ARTIFACT_IDS[@]}"; do
    policy="${MUTABLE_ARTIFACT_POLICIES[$index]}"
    case "$policy" in metadata-root|metadata-any) ;; *) continue ;; esac
    id="${MUTABLE_ARTIFACT_IDS[$index]}"
    path="${MUTABLE_ARTIFACT_PATHS[$index]}"
    record="$(mutable_manifest_record "$manifest" "$id")"
    IFS='|' read -r _ state uid gid mode digest <<< "$record"
    if [ "$state" = absent ]; then
      if [ -e "$path" ] || [ -L "$path" ]; then
        [ -d "$path" ] && [ ! -L "$path" ] ||
          die "mutable metadata directory became unsafe: $id"
        rmdir -- "$path" || die "originally absent mutable directory is not empty: $id"
      fi
      continue
    fi
    [ "$state" = metadata ] && [ -d "$path" ] && [ ! -L "$path" ] ||
      die "mutable metadata directory restore state is unsafe: $id"
    chown "$uid:$gid" "$path"
    chmod "$mode" "$path"
  done

  validate_restored_mutable_artifacts "$lock_dir"
  if restored_postgresql_hba_is_managed; then
    reload_restored_postgresql_hba
  fi
  if command -v nginx >/dev/null 2>&1 && systemctl cat nginx.service >/dev/null 2>&1; then
    nginx -t >/dev/null 2>&1 || die "restored nginx configuration is invalid"
  fi
  sync_recovery_filesystems
}

cleanup_mutable_artifact_backup() {
  local lock_dir="$1"
  local backup_dir="$lock_dir/$MUTABLE_ARTIFACT_BACKUP_NAME"
  local manifest="$lock_dir/$MUTABLE_ARTIFACT_MANIFEST_NAME"
  local deleting_dir="$lock_dir/.${MUTABLE_ARTIFACT_BACKUP_NAME}.deleting"
  [ "$backup_dir" = "$LOCK_DIR_EXPECTED/$MUTABLE_ARTIFACT_BACKUP_NAME" ] ||
    die "mutable artifact cleanup path is unsafe"
  [ "$deleting_dir" = "$LOCK_DIR_EXPECTED/.${MUTABLE_ARTIFACT_BACKUP_NAME}.deleting" ] ||
    die "mutable artifact deletion path is unsafe"

  # Cleanup is a resumable state machine. The secret-bearing manifest is
  # moved inside its backup directory before one atomic rename marks the pair
  # as deletion-only; a crash can therefore never leave an ambiguous half.
  if [ -e "$deleting_dir" ] || [ -L "$deleting_dir" ]; then
    [ -d "$deleting_dir" ] && [ ! -L "$deleting_dir" ] &&
      [ "$(stat -c '%U:%G:%a' "$deleting_dir")" = "root:root:700" ] ||
      die "mutable artifact deletion directory is unsafe"
    [ ! -e "$backup_dir" ] && [ ! -L "$backup_dir" ] &&
      [ ! -e "$manifest" ] && [ ! -L "$manifest" ] ||
      die "mutable artifact cleanup state is ambiguous"
    rm -rf -- "$deleting_dir"
    return 0
  fi
  if [ ! -e "$backup_dir" ] && [ ! -L "$backup_dir" ] &&
     [ ! -e "$manifest" ] && [ ! -L "$manifest" ]; then
    return 0
  fi
  if [ -d "$backup_dir" ] && [ ! -L "$backup_dir" ] &&
     [ ! -e "$manifest" ] && [ ! -L "$manifest" ]; then
    [ -f "$backup_dir/$MUTABLE_ARTIFACT_MANIFEST_NAME" ] &&
      [ ! -L "$backup_dir/$MUTABLE_ARTIFACT_MANIFEST_NAME" ] &&
      [ "$(stat -c '%U:%G:%a' "$backup_dir/$MUTABLE_ARTIFACT_MANIFEST_NAME")" = "root:root:600" ] ||
      die "mutable artifact cleanup manifest transition is unsafe"
    mv -T -- "$backup_dir" "$deleting_dir"
    rm -rf -- "$deleting_dir"
    return 0
  fi
  [ -d "$backup_dir" ] && [ ! -L "$backup_dir" ] &&
    [ -f "$manifest" ] && [ ! -L "$manifest" ] ||
    die "mutable artifact cleanup state is incomplete"
  validate_mutable_artifact_backup "$lock_dir"
  [ ! -e "$backup_dir/$MUTABLE_ARTIFACT_MANIFEST_NAME" ] &&
    [ ! -L "$backup_dir/$MUTABLE_ARTIFACT_MANIFEST_NAME" ] ||
    die "mutable artifact cleanup staging manifest already exists"
  mv -- "$manifest" "$backup_dir/$MUTABLE_ARTIFACT_MANIFEST_NAME"
  mv -T -- "$backup_dir" "$deleting_dir"
  rm -rf -- "$deleting_dir"
}

backup_external_active_units() {
  local lock_dir="$1"
  local manifest="$lock_dir/external-active-units"
  local unit_name active_state
  install -o root -g root -m 0600 /dev/null "$manifest"
  for unit_name in nginx.service cloudflared.service; do
    systemctl cat "$unit_name" >/dev/null 2>&1 || continue
    active_state="$(systemctl show "$unit_name" -p ActiveState --value)"
    case "$active_state" in
      active|activating|reloading) printf '%s\n' "$unit_name" >> "$manifest" ;;
    esac
  done
}

disable_guard_unit() {
  local token="$1"
  local unit_stage="/etc/systemd/system/.mooncen-deploy-guard-${token}.staged"
  systemctl disable "mooncen-deploy-guard@${token}.service" >/dev/null 2>&1 || true
  # If recovery restored a baseline that did not contain the guard template,
  # systemctl cannot resolve the instance name and may leave this exact wants
  # link behind. Remove only the validated deployment instance link.
  rm -f -- "/etc/systemd/system/multi-user.target.wants/mooncen-deploy-guard@${token}.service" || true
  if [ -e "$unit_stage" ] || [ -L "$unit_stage" ]; then
    if [ -f "$unit_stage" ] && [ ! -L "$unit_stage" ] && [ "$(stat -c '%u' "$unit_stage")" = 0 ]; then
      rm -f -- "$unit_stage" || true
    else
      echo "mooncen release guard: unsafe deployment unit staging path remains: $unit_stage" >&2
    fi
  fi
  sync -f -- /etc/systemd/system >/dev/null 2>&1 ||
    echo "mooncen release guard: guard unit cleanup durability sync failed" >&2
}

finish_lock() {
  local lock_dir="$1"
  local token="$2"
  local entry="$3"
  local terminal_phase="$4"
  local finalized_lock="$entry/finalized-lock"
  case "$terminal_phase" in committed|aborted|recovered) ;; *) die "invalid terminal deployment phase" ;; esac
  [ "$entry" = "$HISTORY_DIR/$token" ] && [ -d "$entry" ] && [ ! -L "$entry" ] &&
    [ "$(stat -c '%U:%G:%a' "$entry")" = "root:root:700" ] ||
    die "terminal release history entry is unsafe"
  [ ! -e "$finalized_lock" ] && [ ! -L "$finalized_lock" ] ||
    die "finalized deployment lock destination already exists"
  cp -- "$lock_dir/journal.env" "$entry/journal.env"
  chown root:root "$entry/journal.env"
  chmod 0600 "$entry/journal.env"
  if [ -f "$lock_dir/active-units" ] && [ ! -L "$lock_dir/active-units" ]; then
    cp -- "$lock_dir/active-units" "$entry/active-units-before-deploy"
    chown root:root "$entry/active-units-before-deploy"
    chmod 0600 "$entry/active-units-before-deploy"
  fi
  if [ -f "$lock_dir/external-active-units" ] && [ ! -L "$lock_dir/external-active-units" ]; then
    cp -- "$lock_dir/external-active-units" "$entry/external-active-units-before-deploy"
    chown root:root "$entry/external-active-units-before-deploy"
    chmod 0600 "$entry/external-active-units-before-deploy"
  fi
  # Mutable snapshots can contain credentials. Validate and destroy them on
  # every terminal path; they are never copied into release history.
  cleanup_mutable_artifact_backup "$lock_dir"
  if [ ! -e "$lock_dir/systemd-units" ] && [ ! -L "$lock_dir/systemd-units" ] &&
     [ ! -e "$entry/systemd-units-before-deploy" ] &&
     [ ! -L "$entry/systemd-units-before-deploy" ]; then
    die "systemd unit backup disappeared during finalization"
  fi
  preserve_directory "$lock_dir/systemd-units" "$entry/systemd-units-before-deploy"
  preserve_file "$lock_dir/systemd-unit-names" "$entry/systemd-unit-names-before-deploy"
  preserve_file "$lock_dir/systemd-enabled-units" "$entry/systemd-enabled-units-before-deploy"
  preserve_file "$lock_dir/$SYSTEMD_UNIT_METADATA_NAME" "$entry/$SYSTEMD_UNIT_METADATA_NAME-before-deploy"
  preserve_file "$lock_dir/$SYSTEMD_DROPIN_METADATA_NAME" "$entry/$SYSTEMD_DROPIN_METADATA_NAME-before-deploy"
  if [ -d "$lock_dir/systemd-dropins" ] && [ ! -L "$lock_dir/systemd-dropins" ]; then
    if find "$lock_dir/systemd-dropins" -mindepth 1 -print -quit | grep -q .; then
      preserve_directory "$lock_dir/systemd-dropins" "$entry/systemd-dropins-before-deploy"
    else
      rmdir -- "$lock_dir/systemd-dropins"
    fi
  elif [ -e "$entry/systemd-dropins-before-deploy" ] ||
       [ -L "$entry/systemd-dropins-before-deploy" ]; then
    [ -d "$entry/systemd-dropins-before-deploy" ] &&
      [ ! -L "$entry/systemd-dropins-before-deploy" ] ||
      die "preserved systemd drop-in backup is unsafe"
  fi
  set_phase "$lock_dir" "$token" "$terminal_phase"
  cp -- "$lock_dir/journal.env" "$entry/journal.env"
  chown root:root "$entry/journal.env"
  chmod 0600 "$entry/journal.env"
  # Renaming the exact lock directory is the atomic completion boundary. A
  # crash before it is retryable; after it, future deploys see no active lock.
  sync_recovery_filesystems
  # Release the cross-runtime fence only after every application/runtime
  # mutation and its durability barrier reached an exact terminal state. Only
  # idempotent history-lock bookkeeping remains after this point.
  end_native_intent "$NATIVE_INTENT_TOKEN"
  mv -T -- "$lock_dir" "$finalized_lock"
  durability_barrier /opt
  [ -d "$finalized_lock" ] && [ ! -L "$finalized_lock" ] ||
    die "finalized deployment lock is unsafe"
  disable_guard_unit "$token"
  if ! rm -f -- "/opt/.mooncen-deploy-heartbeat-${token}"; then
    echo "mooncen release guard: stale deployment heartbeat cleanup failed" >&2
  fi
  if ! rm -rf -- "$finalized_lock"; then
    echo "mooncen release guard: finalized non-secret lock cleanup remains in $entry" >&2
  fi
}

backup_systemd_units() {
  local lock_dir="$1"
  local backup_dir="$lock_dir/systemd-units"
  local dropin_backup="$lock_dir/systemd-dropins"
  local manifest="$lock_dir/systemd-unit-names"
  local enabled_manifest="$lock_dir/systemd-enabled-units"
  local unit_metadata="$lock_dir/$SYSTEMD_UNIT_METADATA_NAME"
  local dropin_metadata="$lock_dir/$SYSTEMD_DROPIN_METADATA_NAME"
  local installed unit_name uid gid mode digest
  install -d -o root -g root -m 0700 "$backup_dir"
  install -d -o root -g root -m 0700 "$dropin_backup"
  install -o root -g root -m 0600 /dev/null "$manifest"
  install -o root -g root -m 0600 /dev/null "$enabled_manifest"
  install -o root -g root -m 0600 /dev/null "$unit_metadata"
  install -o root -g root -m 0600 /dev/null "$dropin_metadata"
  for installed in \
    /etc/systemd/system/mooncen-*.service \
    /etc/systemd/system/mooncen-*.timer \
    /etc/systemd/system/cloudflared.service; do
    [ -e "$installed" ] || [ -L "$installed" ] || continue
    unit_name="$(basename "$installed")"
    case "$unit_name" in
      mooncen-node-metrics.service|mooncen-node-metrics.timer|mooncen-ops-console.service)
        continue
        ;;
    esac
    if is_external_control_plane_unit_name "$unit_name" ||
       is_container_runtime_unit_name "$unit_name"; then
      continue
    fi
    if is_crawler_runtime_unit_name "$unit_name"; then
      continue
    fi
    [[ "$unit_name" =~ ^mooncen-[A-Za-z0-9_.@-]+\.(service|timer)$ ]] ||
      [ "$unit_name" = "cloudflared.service" ] ||
      die "unsafe installed unit name: $unit_name"
    [ -f "$installed" ] && [ ! -L "$installed" ] ||
      die "installed MoonCen unit is not a regular file: $installed"
    [ "$(stat -c '%u' "$installed")" = 0 ] || die "installed systemd unit is not root-owned: $unit_name"
    mode="$(stat -c '%a' "$installed")"
    (( (8#$mode & 8#022) == 0 )) || die "installed systemd unit mode is unsafe: $unit_name"
    uid="$(stat -c '%u' "$installed")"
    gid="$(stat -c '%g' "$installed")"
    digest="$(sha256sum "$installed" | awk '{print $1}')"
    cp -a -- "$installed" "$backup_dir/$unit_name"
    printf '%s\n' "$unit_name" >> "$manifest"
    printf '%s|%s|%s|%s|%s\n' "$unit_name" "$uid" "$gid" "$mode" "$digest" >> "$unit_metadata"
    if systemctl is-enabled --quiet "$unit_name"; then
      printf '%s\n' "$unit_name" >> "$enabled_manifest"
    fi
  done
  # The vendor cloudflared unit may live below /lib or /usr/lib while no
  # /etc unit exists. Its logical enablement is independent of the exact
  # override file inventory and must survive a failed deployment.
  if systemctl cat cloudflared.service >/dev/null 2>&1 &&
     systemctl is-enabled --quiet cloudflared.service; then
    printf '%s\n' cloudflared.service >> "$enabled_manifest"
  fi
  LC_ALL=C sort -u -o "$manifest" "$manifest"
  LC_ALL=C sort -u -o "$enabled_manifest" "$enabled_manifest"
  LC_ALL=C sort -t'|' -k1,1 -o "$unit_metadata" "$unit_metadata"

  for installed in /etc/systemd/system/mooncen-*.service.d /etc/systemd/system/mooncen-*.timer.d; do
    [ -e "$installed" ] || [ -L "$installed" ] || continue
    unit_name="$(basename "$installed")"
    case "$unit_name" in
      mooncen-node-metrics.service.d|mooncen-node-metrics.timer.d)
        continue
        ;;
    esac
    if is_external_control_plane_unit_name "$unit_name" ||
       is_container_runtime_unit_name "$unit_name"; then
      continue
    fi
    if is_crawler_runtime_unit_name "$unit_name"; then
      continue
    fi
    [[ "$unit_name" =~ ^mooncen-[A-Za-z0-9_.@-]+\.(service|timer)\.d$ ]] ||
      die "unsafe installed systemd drop-in name: $unit_name"
    [ -d "$installed" ] && [ ! -L "$installed" ] ||
      die "installed MoonCen drop-in is not a regular directory: $installed"
    if find "$installed" -xdev -type l -print -quit | grep -q .; then
      die "installed MoonCen drop-in contains a symbolic link: $installed"
    fi
    validate_mutable_source "$installed" tree
    uid="$(stat -c '%u' "$installed")"
    gid="$(stat -c '%g' "$installed")"
    mode="$(stat -c '%a' "$installed")"
    digest="$(mutable_tree_digest "$installed")"
    cp -a -- "$installed" "$dropin_backup/$unit_name"
    printf '%s|%s|%s|%s|%s\n' "$unit_name" "$uid" "$gid" "$mode" "$digest" >> "$dropin_metadata"
  done
  LC_ALL=C sort -t'|' -k1,1 -o "$dropin_metadata" "$dropin_metadata"
  validate_systemd_configuration_backup "$lock_dir"
  durability_barrier "$lock_dir"
}

validate_systemd_configuration_backup() {
  local lock_dir="$1"
  local backup_dir="$lock_dir/systemd-units"
  local dropin_backup="$lock_dir/systemd-dropins"
  local manifest="$lock_dir/systemd-unit-names"
  local enabled_manifest="$lock_dir/systemd-enabled-units"
  local unit_metadata="$lock_dir/$SYSTEMD_UNIT_METADATA_NAME"
  local dropin_metadata="$lock_dir/$SYSTEMD_DROPIN_METADATA_NAME"
  local unit_name record uid gid mode digest stored actual direct_count
  for protected_dir in "$backup_dir" "$dropin_backup"; do
    [ -d "$protected_dir" ] && [ ! -L "$protected_dir" ] &&
      [ "$(stat -c '%U:%G:%a' "$protected_dir")" = "root:root:700" ] ||
      die "systemd backup directory is unsafe"
  done
  for protected_file in "$manifest" "$enabled_manifest" "$unit_metadata" "$dropin_metadata"; do
    [ -f "$protected_file" ] && [ ! -L "$protected_file" ] &&
      [ "$(stat -c '%U:%G:%a' "$protected_file")" = "root:root:600" ] ||
      die "systemd backup manifest is unsafe"
  done
  LC_ALL=C sort -u "$manifest" | cmp -s -- "$manifest" - ||
    die "systemd unit backup manifest is not canonical"
  LC_ALL=C sort -u "$enabled_manifest" | cmp -s -- "$enabled_manifest" - ||
    die "enabled systemd unit manifest is not canonical"
  [ "$(wc -l < "$manifest")" -eq "$(wc -l < "$unit_metadata")" ] ||
    die "systemd unit metadata manifest size is invalid"
  direct_count="$(find "$backup_dir" -mindepth 1 -maxdepth 1 -printf '.' | wc -c)"
  [ "$direct_count" -eq "$(wc -l < "$manifest")" ] ||
    die "systemd unit backup contains an unreviewed entry"
  while IFS= read -r unit_name; do
    [ -n "$unit_name" ] || continue
    ! is_external_control_plane_unit_name "$unit_name" ||
      die "external control-plane unit entered the application baseline: $unit_name"
    ! is_container_runtime_unit_name "$unit_name" ||
      die "container runtime unit entered the native application baseline: $unit_name"
    [[ "$unit_name" =~ ^mooncen-[A-Za-z0-9_.@-]+\.(service|timer)$ ]] ||
      [ "$unit_name" = cloudflared.service ] || die "systemd unit backup manifest contains an unsafe name"
    record="$(systemd_metadata_record "$unit_metadata" "$unit_name")" ||
      die "systemd unit metadata entry is invalid: $unit_name"
    IFS='|' read -r _ uid gid mode digest <<< "$record"
    [[ "$uid" =~ ^[0-9]+$ && "$gid" =~ ^[0-9]+$ && "$mode" =~ ^[0-7]{3,4}$ &&
       "$digest" =~ ^[0-9a-f]{64}$ ]] || die "systemd unit metadata is invalid: $unit_name"
    stored="$backup_dir/$unit_name"
    [ -f "$stored" ] && [ ! -L "$stored" ] || die "systemd unit backup is unsafe: $unit_name"
    [ "$(stat -c '%u:%g:%a' "$stored")" = "$uid:$gid:$mode" ] ||
      die "systemd unit backup metadata mismatch: $unit_name"
    actual="$(sha256sum "$stored" | awk '{print $1}')"
    [ "$actual" = "$digest" ] || die "systemd unit backup digest mismatch: $unit_name"
  done < "$manifest"
  while IFS= read -r unit_name; do
    [ -n "$unit_name" ] || continue
    grep -Fxq -- "$unit_name" "$manifest" ||
      die "systemd unit backup is absent from its name manifest: $unit_name"
    systemd_metadata_record "$unit_metadata" "$unit_name" >/dev/null ||
      die "systemd unit backup metadata is not unique: $unit_name"
  done < <(find "$backup_dir" -mindepth 1 -maxdepth 1 -type f -printf '%f\n' | LC_ALL=C sort)
  while IFS= read -r unit_name; do
    [ -n "$unit_name" ] || continue
    ! is_external_control_plane_unit_name "$unit_name" ||
      die "external control-plane unit entered the enabled application baseline: $unit_name"
    ! is_container_runtime_unit_name "$unit_name" ||
      die "container runtime unit entered the native enabled baseline: $unit_name"
    [[ "$unit_name" =~ ^mooncen-[A-Za-z0-9_.@-]+\.(service|timer)$ ]] ||
      [ "$unit_name" = cloudflared.service ] || die "enabled systemd unit manifest contains an unsafe name"
    grep -Fxq -- "$unit_name" "$manifest" ||
      [ "$unit_name" = cloudflared.service ] ||
      die "enabled systemd unit was not present in the captured baseline: $unit_name"
  done < "$enabled_manifest"

  if find "$dropin_backup" -mindepth 1 -maxdepth 1 ! -type d -print -quit | grep -q .; then
    die "systemd drop-in backup contains an unsupported entry"
  fi
  direct_count="$(find "$dropin_backup" -mindepth 1 -maxdepth 1 -type d -printf '.' | wc -c)"
  [ "$direct_count" -eq "$(wc -l < "$dropin_metadata")" ] ||
    die "systemd drop-in metadata manifest size is invalid"
  while IFS='|' read -r unit_name uid gid mode digest; do
    [ -n "$unit_name" ] || continue
    ! is_external_control_plane_unit_name "$unit_name" ||
      die "external control-plane drop-in entered the application baseline: $unit_name"
    ! is_container_runtime_unit_name "$unit_name" ||
      die "container runtime drop-in entered the native application baseline: $unit_name"
    [[ "$unit_name" =~ ^mooncen-[A-Za-z0-9_.@-]+\.(service|timer)\.d$ ]] ||
      die "systemd drop-in metadata contains an unsafe name"
    [[ "$uid" =~ ^[0-9]+$ && "$gid" =~ ^[0-9]+$ && "$mode" =~ ^[0-7]{3,4}$ &&
       "$digest" =~ ^[0-9a-f]{64}$ ]] || die "systemd drop-in metadata is invalid: $unit_name"
    stored="$dropin_backup/$unit_name"
    validate_mutable_source "$stored" tree
    [ "$(stat -c '%u:%g:%a' "$stored")" = "$uid:$gid:$mode" ] ||
      die "systemd drop-in backup metadata mismatch: $unit_name"
    actual="$(mutable_tree_digest "$stored")"
    [ "$actual" = "$digest" ] || die "systemd drop-in backup digest mismatch: $unit_name"
  done < "$dropin_metadata"
  while IFS= read -r unit_name; do
    [ -n "$unit_name" ] || continue
    systemd_metadata_record "$dropin_metadata" "$unit_name" >/dev/null ||
      die "systemd drop-in backup metadata is not unique: $unit_name"
  done < <(find "$dropin_backup" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | LC_ALL=C sort)
}

restore_systemd_configuration() {
  local lock_dir="$1"
  local backup_dir="$lock_dir/systemd-units"
  local manifest="$lock_dir/systemd-unit-names"
  local dropin_backup="$lock_dir/systemd-dropins"
  local enabled_manifest="$lock_dir/systemd-enabled-units"
  local unit_metadata="$lock_dir/$SYSTEMD_UNIT_METADATA_NAME"
  local dropin_metadata="$lock_dir/$SYSTEMD_DROPIN_METADATA_NAME"
  local installed unit_name backup record uid gid mode digest actual stage parent_mode
  validate_systemd_configuration_backup "$lock_dir"
  [ -d /etc/systemd/system ] && [ ! -L /etc/systemd/system ] &&
    [ "$(stat -c '%u' /etc/systemd/system)" = 0 ] ||
    die "systemd configuration parent is unsafe"
  parent_mode="$(stat -c '%a' /etc/systemd/system)"
  (( (8#$parent_mode & 8#022) == 0 )) ||
    die "systemd configuration parent mode is unsafe"

  for installed in \
    /etc/systemd/system/mooncen-*.service \
    /etc/systemd/system/mooncen-*.timer \
    /etc/systemd/system/cloudflared.service; do
    [ -e "$installed" ] || [ -L "$installed" ] || continue
    unit_name="$(basename "$installed")"
    case "$unit_name" in
      mooncen-node-metrics.service|mooncen-node-metrics.timer)
        continue
        ;;
    esac
    if is_external_control_plane_unit_name "$unit_name" ||
       is_container_runtime_unit_name "$unit_name"; then
      continue
    fi
    if is_crawler_runtime_unit_name "$unit_name"; then
      continue
    fi
    [[ "$unit_name" =~ ^mooncen-[A-Za-z0-9_.@-]+\.(service|timer)$ ]] ||
      [ "$unit_name" = "cloudflared.service" ] ||
      die "unsafe installed unit name during recovery: $unit_name"
    if ! grep -Fxq -- "$unit_name" "$manifest"; then
      systemctl disable "$unit_name" >/dev/null 2>&1 || true
      ! systemctl is-enabled --quiet "$unit_name" ||
        die "new systemd unit remains enabled during recovery: $unit_name"
      rm -f -- "$installed"
    fi
  done
  for backup in "$backup_dir"/*; do
    [ -e "$backup" ] || continue
    unit_name="$(basename "$backup")"
    grep -Fxq -- "$unit_name" "$manifest" || die "systemd backup is absent from its manifest"
    [ -f "$backup" ] && [ ! -L "$backup" ] || die "systemd unit backup is unsafe"
    record="$(systemd_metadata_record "$unit_metadata" "$unit_name")"
    IFS='|' read -r _ uid gid mode digest <<< "$record"
    stage="/etc/systemd/system/.mooncen-guard-${TOKEN}-${unit_name}.staged"
    if [ -e "$stage" ] || [ -L "$stage" ]; then
      [ -f "$stage" ] && [ ! -L "$stage" ] && [ "$(stat -c '%u' "$stage")" = 0 ] ||
        die "systemd unit restore staging file is unsafe: $unit_name"
      rm -f -- "$stage"
    fi
    cp -a -- "$backup" "$stage"
    [ "$(stat -c '%u:%g:%a' "$stage")" = "$uid:$gid:$mode" ] ||
      die "staged systemd unit metadata mismatch: $unit_name"
    actual="$(sha256sum "$stage" | awk '{print $1}')"
    [ "$actual" = "$digest" ] || die "staged systemd unit digest mismatch: $unit_name"
    durability_barrier "$stage"
    mv -fT -- "$stage" "/etc/systemd/system/$unit_name"
    durability_barrier /etc/systemd/system
    [ "$(stat -c '%u:%g:%a' "/etc/systemd/system/$unit_name")" = "$uid:$gid:$mode" ] ||
      die "restored systemd unit metadata mismatch: $unit_name"
    actual="$(sha256sum "/etc/systemd/system/$unit_name" | awk '{print $1}')"
    [ "$actual" = "$digest" ] || die "restored systemd unit digest mismatch: $unit_name"
  done

  for installed in /etc/systemd/system/mooncen-*.service.d /etc/systemd/system/mooncen-*.timer.d; do
    [ -e "$installed" ] || [ -L "$installed" ] || continue
    unit_name="$(basename "$installed")"
    case "$unit_name" in
      mooncen-node-metrics.service.d|mooncen-node-metrics.timer.d)
        continue
        ;;
    esac
    if is_external_control_plane_unit_name "$unit_name" ||
       is_container_runtime_unit_name "$unit_name"; then
      continue
    fi
    if is_crawler_runtime_unit_name "$unit_name"; then
      continue
    fi
    [[ "$unit_name" =~ ^mooncen-[A-Za-z0-9_.@-]+\.(service|timer)\.d$ ]] ||
      die "unsafe systemd drop-in path during recovery: $installed"
    [ -d "$installed" ] && [ ! -L "$installed" ] || die "systemd drop-in path is unsafe"
    rm -rf -- "$installed"
  done
  if [ -d "$dropin_backup" ] && [ ! -L "$dropin_backup" ]; then
    for installed in "$dropin_backup"/*; do
      [ -e "$installed" ] || continue
      unit_name="$(basename "$installed")"
      [[ "$unit_name" =~ ^mooncen-[A-Za-z0-9_.@-]+\.(service|timer)\.d$ ]] ||
        die "unsafe backed-up systemd drop-in name: $unit_name"
      [ -d "$installed" ] && [ ! -L "$installed" ] || die "backed-up systemd drop-in is unsafe"
      record="$(systemd_metadata_record "$dropin_metadata" "$unit_name")"
      IFS='|' read -r _ uid gid mode digest <<< "$record"
      stage="/etc/systemd/system/.mooncen-guard-${TOKEN}-${unit_name}.staged"
      if [ -e "$stage" ] || [ -L "$stage" ]; then
        validate_mutable_source "$stage" tree
        rm -rf -- "$stage"
      fi
      cp -a -- "$installed" "$stage"
      [ "$(stat -c '%u:%g:%a' "$stage")" = "$uid:$gid:$mode" ] ||
        die "staged systemd drop-in metadata mismatch: $unit_name"
      actual="$(mutable_tree_digest "$stage")"
      [ "$actual" = "$digest" ] || die "staged systemd drop-in digest mismatch: $unit_name"
      durability_barrier "$stage"
      mv -T -- "$stage" "/etc/systemd/system/$unit_name"
      durability_barrier /etc/systemd/system
      [ "$(stat -c '%u:%g:%a' "/etc/systemd/system/$unit_name")" = "$uid:$gid:$mode" ] ||
        die "restored systemd drop-in metadata mismatch: $unit_name"
      actual="$(mutable_tree_digest "/etc/systemd/system/$unit_name")"
      [ "$actual" = "$digest" ] || die "restored systemd drop-in digest mismatch: $unit_name"
    done
  fi
  systemctl daemon-reload
  while IFS= read -r unit_name; do
    [ -n "$unit_name" ] || continue
    [[ "$unit_name" =~ ^mooncen-[A-Za-z0-9_.@-]+\.(service|timer)$ ]] ||
      [ "$unit_name" = "cloudflared.service" ] ||
      die "unsafe unit in systemd recovery manifest"
    if grep -Fxq -- "$unit_name" "$enabled_manifest"; then
      systemctl enable "$unit_name" >/dev/null
      systemctl is-enabled --quiet "$unit_name" ||
        die "restored systemd unit is not enabled: $unit_name"
    else
      systemctl disable "$unit_name" >/dev/null 2>&1 || true
      ! systemctl is-enabled --quiet "$unit_name" ||
        die "restored systemd unit remains enabled: $unit_name"
    fi
  done < "$manifest"
  if systemctl cat cloudflared.service >/dev/null 2>&1; then
    if grep -Fxq -- cloudflared.service "$enabled_manifest"; then
      systemctl enable cloudflared.service >/dev/null
      systemctl is-enabled --quiet cloudflared.service ||
        die "restored vendor cloudflared unit is not enabled"
    else
      systemctl disable cloudflared.service >/dev/null 2>&1 || true
      ! systemctl is-enabled --quiet cloudflared.service ||
        die "restored vendor cloudflared unit remains enabled"
    fi
  fi
  durability_barrier /etc/systemd/system
}

prune_history() {
  local protected_entry="${1:-}"
  local -a entries=()
  local item base kept=0
  mapfile -t entries < <(
    find "$HISTORY_DIR" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' |
      sort -nr | cut -d' ' -f2-
  )
  if [ -n "$protected_entry" ]; then
    [ -d "$protected_entry" ] && [ ! -L "$protected_entry" ] ||
      die "protected release history entry is unsafe"
    kept=1
  fi
  for item in "${entries[@]}"; do
    if [ -n "$protected_entry" ] && [ "$item" = "$protected_entry" ]; then
      continue
    fi
    if [ "$kept" -lt "$HISTORY_KEEP" ]; then
      kept=$((kept + 1))
      continue
    fi
    base="$(basename "$item")"
    [[ "$base" =~ ^[0-9a-f]{32}$ ]] || die "unsafe release history entry: $item"
    [ "$item" = "$HISTORY_DIR/$base" ] && [ -d "$item" ] && [ ! -L "$item" ] ||
      die "unsafe release history path: $item"
    rm -rf -- "$item"
  done
}

stop_managed_units() {
  local -a units=() discovered_units=()
  local unit_name main_pid
  mapfile -t discovered_units < <(
    find /etc/systemd/system -maxdepth 1 -type f \
      \( -name 'mooncen-*.service' -o -name 'mooncen-*.timer' \) -printf '%f\n' |
      awk '$1 !~ /^mooncen-deploy-guard@/ &&
           $1 !~ /^mooncen-container-release-guard@/ &&
           $1 !~ /^mooncen-crawler/ &&
           $1 !~ /^mooncen-staging-apply/ &&
           $1 != "mooncen-branch-coordinates.service" &&
           $1 != "mooncen-node-metrics.service" &&
           $1 != "mooncen-node-metrics.timer" {print $1}' |
      LC_ALL=C sort -u
  )
  for unit_name in "${discovered_units[@]}"; do
    if is_crawler_runtime_unit_name "$unit_name" ||
       is_external_control_plane_unit_name "$unit_name" ||
       is_container_runtime_unit_name "$unit_name"; then
      continue
    fi
    units+=("$unit_name")
  done
  for unit_name in nginx.service cloudflared.service; do
    if systemctl cat "$unit_name" >/dev/null 2>&1; then
      units+=("$unit_name")
    fi
  done
  if [ "${#units[@]}" -eq 0 ]; then
    return 0
  fi
  systemctl stop "${units[@]}"
  for unit_name in "${units[@]}"; do
    systemctl is-active --quiet "$unit_name" && die "unit remains active during recovery: $unit_name"
    main_pid="$(systemctl show "$unit_name" -p MainPID --value)"
    [ "${main_pid:-0}" = "0" ] || die "unit still has a process during recovery: $unit_name"
  done
}

restore_active_units() {
  local lock_dir="$1"
  local -a units=() filtered_units=()
  local unit_name
  if [ -f "$lock_dir/active-units" ]; then
    [ ! -L "$lock_dir/active-units" ] || die "active unit journal is unsafe"
    [ "$(stat -c '%U:%G:%a' "$lock_dir/active-units")" = "root:root:600" ] ||
      die "active unit journal ownership or mode is unsafe"
    mapfile -t units < "$lock_dir/active-units"
    filtered_units=()
    for unit_name in "${units[@]}"; do
      [[ "$unit_name" =~ ^mooncen-[A-Za-z0-9_.@-]+\.(service|timer)$ ]] ||
        die "active unit journal contains an unsafe unit"
      [ "$unit_name" != "mooncen-ops-console.service" ] ||
        die "active unit journal contains the retired remote Ops Console"
      if is_crawler_runtime_unit_name "$unit_name"; then
        echo "mooncen release guard: ignoring crawler-owned unit in legacy active journal: $unit_name" >&2
        continue
      fi
      if is_external_control_plane_unit_name "$unit_name"; then
        echo "mooncen release guard: ignoring external control-plane unit in legacy active journal: $unit_name" >&2
        continue
      fi
      if is_container_runtime_unit_name "$unit_name"; then
        echo "mooncen release guard: preserving container runtime unit from legacy active journal: $unit_name" >&2
        continue
      fi
      filtered_units+=("$unit_name")
    done
    units=("${filtered_units[@]}")
    if [ "${#units[@]}" -gt 0 ]; then
      if [ "${MOONCEN_BOOT_RECOVERY:-0}" = 1 ]; then
        systemctl start --no-block "${units[@]}"
      else
        systemctl start "${units[@]}"
        for unit_name in "${units[@]}"; do
          systemctl is-active --quiet "$unit_name" || die "previously active unit did not recover: $unit_name"
        done
      fi
    fi
  fi
  if [ -f "$lock_dir/external-active-units" ]; then
    [ ! -L "$lock_dir/external-active-units" ] &&
      [ "$(stat -c '%U:%G:%a' "$lock_dir/external-active-units")" = "root:root:600" ] ||
      die "external active-unit journal is unsafe"
    mapfile -t units < "$lock_dir/external-active-units"
    for unit_name in "${units[@]}"; do
      case "$unit_name" in nginx.service|cloudflared.service) ;; *) die "external active-unit journal contains an unsafe unit" ;; esac
    done
    if [ "${#units[@]}" -gt 0 ]; then
      if [ "${MOONCEN_BOOT_RECOVERY:-0}" = 1 ]; then
        systemctl start --no-block "${units[@]}"
      else
        systemctl start "${units[@]}"
        for unit_name in "${units[@]}"; do
          systemctl is-active --quiet "$unit_name" || die "previously active external unit did not recover: $unit_name"
        done
      fi
    fi
  fi
}

validate_release_state_path() {
  local path="$1"
  local label="$2"
  if [ -e "$path" ] || [ -L "$path" ]; then
    [ -d "$path" ] && [ ! -L "$path" ] || die "$label release path is unsafe"
  fi
}

validate_candidate_release() {
  local directory="$1"
  local marker="$directory/.mooncen-prebuilt-release"
  local marker_commit marker_mode
  [ -d "$directory" ] && [ ! -L "$directory" ] || die "candidate release directory is unsafe"
  [ -f "$marker" ] && [ ! -L "$marker" ] || die "candidate release marker is missing or unsafe"
  marker_mode="$(stat -c '%a' "$marker")"
  (( (8#$marker_mode & 8#077) == 0 )) || die "candidate release marker mode is unsafe"
  marker_commit="$(awk -F= '$1=="DEPLOY_COMMIT" {count++; value=$2} END {if(count!=1) exit 1; print value}' "$marker")" ||
    die "candidate release marker commit is invalid"
  [ "$marker_commit" = "$EXPECTED_COMMIT" ] || die "candidate release marker commit mismatch"
}

require_history_entry() {
  local token="$1"
  local entry
  entry="$(history_entry "$token")"
  [ -d "$entry" ] && [ ! -L "$entry" ] &&
    [ "$(stat -c '%U:%G:%a' "$entry")" = "root:root:700" ] ||
    die "required release history entry is unavailable or unsafe"
  printf '%s\n' "$entry"
}

resume_release_finalization() {
  local lock_dir="$1"
  local token="$2"
  local phase="$3"
  local entry terminal_phase history_commit
  entry="$(require_history_entry "$token")"
  case "$phase" in
    finalizing_abort|aborted)
      terminal_phase=aborted
      validate_release_state_path "$entry/unactivated" unactivated
      [ -d "$entry/unactivated" ] || die "unactivated release history is unavailable"
      ;;
    finalizing_recovery|recovered)
      terminal_phase=recovered
      validate_candidate_release "$entry/failed"
      ;;
    finalizing_commit|committed)
      terminal_phase=committed
      [ -f "$entry/deploy-info" ] && [ ! -L "$entry/deploy-info" ] &&
        [ "$(stat -c '%U:%G:%a' "$entry/deploy-info")" = "root:root:600" ] ||
        die "committed release history provenance is unsafe"
      history_commit="$(awk -F= '$1=="DEPLOY_COMMIT" {count++; value=$2} END {if(count!=1) exit 1; print value}' "$entry/deploy-info")" ||
        die "committed release history provenance is invalid"
      [ "$history_commit" = "$EXPECTED_COMMIT" ] ||
        die "committed release history provenance mismatch"
      ;;
    *) die "deployment finalization phase is unsupported" ;;
  esac
  # Pruning remains inside the retryable lock boundary. Once finish_lock
  # atomically removes the live lock, no later housekeeping failure may turn a
  # successful commit/recovery into an ambiguous client-side failure.
  prune_history "$entry"
  finish_lock "$lock_dir" "$token" "$entry" "$terminal_phase"
}

converge_failed_release_paths() {
  local entry_path archived_candidate state_name current_state previous_state
  entry_path="$(history_entry "$TOKEN")"
  archived_candidate="$entry_path/failed"
  for state_path in "$REMOTE_DIR" "$RELEASE_DIR" "$PREVIOUS_DIR" "$FAILED_DIR"; do
    validate_release_state_path "$state_path" managed
  done
  if [ -e "$archived_candidate" ] || [ -L "$archived_candidate" ]; then
    validate_candidate_release "$archived_candidate"
  fi

  if [ -d "$PREVIOUS_DIR" ]; then
    if [ -d "$FAILED_DIR" ]; then
      validate_candidate_release "$FAILED_DIR"
      [ ! -e "$REMOTE_DIR" ] && [ ! -L "$REMOTE_DIR" ] &&
        [ ! -e "$RELEASE_DIR" ] && [ ! -L "$RELEASE_DIR" ] ||
        die "recovery path state is ambiguous after candidate preservation"
    elif [ -d "$REMOTE_DIR" ]; then
      [ ! -e "$RELEASE_DIR" ] && [ ! -L "$RELEASE_DIR" ] ||
        die "recovery found both active and unactivated candidates"
      validate_candidate_release "$REMOTE_DIR"
      for state_name in logs failover; do
        current_state="$REMOTE_DIR/$state_name"
        previous_state="$PREVIOUS_DIR/$state_name"
        if [ -e "$current_state" ] || [ -L "$current_state" ]; then
          [ -d "$current_state" ] && [ ! -L "$current_state" ] ||
            die "candidate mutable state directory is unsafe"
          if [ -e "$previous_state" ] || [ -L "$previous_state" ]; then
            [ -d "$previous_state" ] && [ ! -L "$previous_state" ] ||
              die "previous mutable state directory is unsafe"
            rm -rf -- "$previous_state"
          fi
          mv -T -- "$current_state" "$previous_state"
        fi
      done
      mv -T -- "$REMOTE_DIR" "$FAILED_DIR"
    elif [ -d "$RELEASE_DIR" ]; then
      validate_candidate_release "$RELEASE_DIR"
      mv -T -- "$RELEASE_DIR" "$FAILED_DIR"
    else
      die "candidate release disappeared before code rollback"
    fi
    [ ! -e "$REMOTE_DIR" ] && [ ! -L "$REMOTE_DIR" ] ||
      die "application path is not clear for code rollback"
    mv -T -- "$PREVIOUS_DIR" "$REMOTE_DIR"
  elif [ "$HAD_ACTIVE" = 1 ]; then
    [ -d "$REMOTE_DIR" ] && [ ! -L "$REMOTE_DIR" ] ||
      die "previous active release is unavailable during recovery"
    if [ -d "$RELEASE_DIR" ]; then
      [ ! -e "$FAILED_DIR" ] && [ ! -L "$FAILED_DIR" ] ||
        die "recovery found duplicate candidate release paths"
      validate_candidate_release "$RELEASE_DIR"
      mv -T -- "$RELEASE_DIR" "$FAILED_DIR"
    elif [ -d "$FAILED_DIR" ]; then
      validate_candidate_release "$FAILED_DIR"
    elif [ -d "$archived_candidate" ]; then
      validate_candidate_release "$archived_candidate"
    else
      die "failed candidate release is unavailable during recovery"
    fi
  else
    [ ! -e "$PREVIOUS_DIR" ] && [ ! -L "$PREVIOUS_DIR" ] ||
      die "first-deploy recovery found an unexpected previous release"
    if [ -d "$RELEASE_DIR" ]; then
      [ ! -e "$REMOTE_DIR" ] && [ ! -L "$REMOTE_DIR" ] &&
        [ ! -e "$FAILED_DIR" ] && [ ! -L "$FAILED_DIR" ] ||
        die "first-deploy recovery path state is ambiguous"
      validate_candidate_release "$RELEASE_DIR"
      mv -T -- "$RELEASE_DIR" "$FAILED_DIR"
    elif [ -d "$REMOTE_DIR" ]; then
      [ ! -e "$FAILED_DIR" ] && [ ! -L "$FAILED_DIR" ] ||
        die "first-deploy recovery found duplicate candidate paths"
      validate_candidate_release "$REMOTE_DIR"
      mv -T -- "$REMOTE_DIR" "$FAILED_DIR"
    elif [ -d "$FAILED_DIR" ]; then
      validate_candidate_release "$FAILED_DIR"
    elif [ -d "$archived_candidate" ]; then
      validate_candidate_release "$archived_candidate"
    else
      die "first-deploy candidate release is unavailable during recovery"
    fi
    [ ! -e "$REMOTE_DIR" ] && [ ! -L "$REMOTE_DIR" ] ||
      die "first-deploy application path was not removed"
  fi

  [ ! -e "$RELEASE_DIR" ] && [ ! -L "$RELEASE_DIR" ] &&
    [ ! -e "$PREVIOUS_DIR" ] && [ ! -L "$PREVIOUS_DIR" ] ||
    die "release rollback did not converge to its canonical path state"
  if [ "$HAD_ACTIVE" = 1 ]; then
    [ -d "$REMOTE_DIR" ] && [ ! -L "$REMOTE_DIR" ] ||
      die "previous release was not restored"
  else
    [ ! -e "$REMOTE_DIR" ] && [ ! -L "$REMOTE_DIR" ] ||
      die "first-deploy rollback left an application release active"
  fi
}

recover_release() {
  local lock_dir="$1"
  local token="$2"
  [ "$lock_dir" = "$LOCK_DIR_EXPECTED" ] || die "unexpected deployment lock path"
  if [ ! -e "$lock_dir" ] && [ ! -L "$lock_dir" ]; then
    return 0
  fi
  validate_lock "$lock_dir" "$token"
  exec 9>"$lock_dir/operation.lock"
  flock -x 9
  if [ ! -e "$lock_dir" ] && [ ! -L "$lock_dir" ]; then
    return 0
  fi
  validate_lock "$lock_dir" "$token"
  load_journal "$lock_dir" "$token"
  clear_native_start_authorization "$token"

  case "$PHASE" in
    finalizing_abort|finalizing_recovery|finalizing_commit|committed|aborted|recovered)
      resume_release_finalization "$lock_dir" "$token" "$PHASE"
      return 0
      ;;
    committing)
      finalize_commit_release "$lock_dir" "$token"
      return 0
      ;;
    prepared)
      set_phase "$lock_dir" "$token" recovering_prepared
      load_journal "$lock_dir" "$token"
      ;;
    recovering_prepared)
      ;;
    activating|activated|verified)
      set_phase "$lock_dir" "$token" recovering
      load_journal "$lock_dir" "$token"
      ;;
    recovering) ;;
    recovery_failed) die "deployment recovery previously failed" ;;
    *) die "deployment journal has an unsupported recovery phase: $PHASE" ;;
  esac

  if [ "$PHASE" = recovering_prepared ]; then
      local prepared_entry
      restore_systemd_configuration "$lock_dir"
      restore_mutable_artifacts "$lock_dir"
      systemctl daemon-reload
      restore_active_units_authorized "$lock_dir" "$token"
      prepared_entry="$(prepare_history_entry "$token")"
      validate_release_state_path "$RELEASE_DIR" unactivated
      preserve_directory "$RELEASE_DIR" "$prepared_entry/unactivated"
      validate_release_state_path "$prepared_entry/unactivated" unactivated
      [ -d "$prepared_entry/unactivated" ] || die "unactivated release was not preserved"
      sync_recovery_filesystems
      set_phase "$lock_dir" "$token" finalizing_abort
      resume_release_finalization "$lock_dir" "$token" finalizing_abort
      return 0
  fi

  [ -f "$lock_dir/active-units" ] && [ ! -L "$lock_dir/active-units" ] &&
    [ "$(stat -c '%U:%G:%a' "$lock_dir/active-units")" = "root:root:600" ] ||
    die "active unit recovery journal is unavailable or unsafe"
  stop_managed_units
  converge_failed_release_paths
  restore_systemd_configuration "$lock_dir"
  restore_mutable_artifacts "$lock_dir"
  systemctl daemon-reload
  restore_active_units_authorized "$lock_dir" "$token"

  local recovered_entry
  recovered_entry="$(prepare_history_entry "$token")"
  preserve_directory "$FAILED_DIR" "$recovered_entry/failed"
  validate_candidate_release "$recovered_entry/failed"
  sync_recovery_filesystems
  set_phase "$lock_dir" "$token" finalizing_recovery
  resume_release_finalization "$lock_dir" "$token" finalizing_recovery
}

bootstrap_value() {
  local manifest="$1"
  local key="$2"
  journal_value "$manifest" "$key"
}

load_bootstrap() {
  local lock_dir="$1"
  local expected_token="$2"
  BOOTSTRAP_MANIFEST="$lock_dir/$BOOTSTRAP_MANIFEST_NAME"
  [ -f "$BOOTSTRAP_MANIFEST" ] && [ ! -L "$BOOTSTRAP_MANIFEST" ] &&
    [ "$(stat -c '%U:%G:%a' "$BOOTSTRAP_MANIFEST")" = "root:root:600" ] ||
    die "deployment bootstrap manifest is unavailable or unsafe"
  BOOTSTRAP_VERSION="$(bootstrap_value "$BOOTSTRAP_MANIFEST" VERSION)" ||
    die "deployment bootstrap version is invalid"
  BOOTSTRAP_TOKEN="$(bootstrap_value "$BOOTSTRAP_MANIFEST" TOKEN)" ||
    die "deployment bootstrap token is invalid"
  BOOTSTRAP_PHASE="$(bootstrap_value "$BOOTSTRAP_MANIFEST" PHASE)" ||
    die "deployment bootstrap phase is invalid"
  BOOTSTRAP_RELEASE_DIR="$(bootstrap_value "$BOOTSTRAP_MANIFEST" RELEASE_DIR)" ||
    die "deployment bootstrap release path is invalid"
  BOOTSTRAP_DEPLOY_USER="$(bootstrap_value "$BOOTSTRAP_MANIFEST" DEPLOY_USER)" ||
    die "deployment bootstrap deploy user is invalid"
  BOOTSTRAP_BOOT_ID="$(bootstrap_value "$BOOTSTRAP_MANIFEST" BOOT_ID)" ||
    die "deployment bootstrap boot identifier is invalid"
  BOOTSTRAP_DEADLINE_EPOCH="$(bootstrap_value "$BOOTSTRAP_MANIFEST" DEADLINE_EPOCH)" ||
    die "deployment bootstrap deadline is invalid"
  BOOTSTRAP_NATIVE_INTENT_TOKEN="$(bootstrap_value "$BOOTSTRAP_MANIFEST" NATIVE_INTENT_TOKEN)" ||
    die "deployment bootstrap native intent is invalid"
  [ "$BOOTSTRAP_VERSION" = 1 ] || die "unsupported deployment bootstrap version"
  [ "$BOOTSTRAP_TOKEN" = "$expected_token" ] || die "deployment bootstrap token mismatch"
  case "$BOOTSTRAP_PHASE" in watching|finalizing) ;; *) die "deployment bootstrap phase is unsafe" ;; esac
  [ "$BOOTSTRAP_RELEASE_DIR" = "/opt/.mooncen-release-${expected_token}" ] ||
    die "deployment bootstrap release path is unsafe"
  [[ "$BOOTSTRAP_DEPLOY_USER" =~ ^[A-Za-z_][A-Za-z0-9_-]{0,63}$ ]] &&
    id "$BOOTSTRAP_DEPLOY_USER" >/dev/null 2>&1 ||
    die "deployment bootstrap deploy user is unsafe"
  [[ "$BOOTSTRAP_BOOT_ID" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] ||
    die "deployment bootstrap boot identifier is unsafe"
  [[ "$BOOTSTRAP_DEADLINE_EPOCH" =~ ^[0-9]{10,12}$ ]] ||
    die "deployment bootstrap deadline is unsafe"
  validate_token "$BOOTSTRAP_NATIVE_INTENT_TOKEN"
}

set_bootstrap_phase() {
  local lock_dir="$1"
  local token="$2"
  local phase="$3"
  local temporary="$lock_dir/.bootstrap.$$.tmp"
  [ "$phase" = finalizing ] || die "unsupported deployment bootstrap transition"
  validate_lock "$lock_dir" "$token"
  load_bootstrap "$lock_dir" "$token"
  [ ! -e "$temporary" ] && [ ! -L "$temporary" ] ||
    die "deployment bootstrap staging path already exists"
  awk -F= -v phase="$phase" '
    BEGIN { changed=0 }
    $1 == "PHASE" { print "PHASE=" phase; changed++; next }
    { print }
    END { if (changed != 1) exit 1 }
  ' "$BOOTSTRAP_MANIFEST" > "$temporary"
  chown root:root "$temporary"
  chmod 0600 "$temporary"
  durability_barrier "$temporary"
  mv -fT -- "$temporary" "$BOOTSTRAP_MANIFEST"
  durability_barrier "$lock_dir"
  BOOTSTRAP_PHASE="$phase"
}

discard_partial_mutable_snapshot() {
  local lock_dir="$1"
  local partial_dir partial_file
  for partial_dir in \
    "$lock_dir/$MUTABLE_ARTIFACT_BACKUP_NAME" \
    "$lock_dir/.${MUTABLE_ARTIFACT_BACKUP_NAME}.deleting"; do
    if [ -e "$partial_dir" ] || [ -L "$partial_dir" ]; then
      [ -d "$partial_dir" ] && [ ! -L "$partial_dir" ] &&
        [ "$(stat -c '%U:%G:%a' "$partial_dir")" = "root:root:700" ] ||
        die "partial mutable snapshot directory is unsafe"
      rm -rf -- "$partial_dir"
    fi
  done
  partial_file="$lock_dir/$MUTABLE_ARTIFACT_MANIFEST_NAME"
  if [ -e "$partial_file" ] || [ -L "$partial_file" ]; then
    [ -f "$partial_file" ] && [ ! -L "$partial_file" ] &&
      [ "$(stat -c '%U:%G:%a' "$partial_file")" = "root:root:600" ] ||
      die "partial mutable snapshot manifest is unsafe"
    rm -f -- "$partial_file"
  fi
}

bootstrap_guard() {
  [ "$#" -eq 6 ] || die "bootstrap expects six arguments"
  local lock_dir="$1" token="$2" release_dir="$3" deploy_user="$4" deadline_seconds="$5"
  local native_intent_token="$6"
  local temporary="$lock_dir/.bootstrap.$$.tmp"
  local boot_id
  validate_token "$token"
  validate_lock "$lock_dir" "$token"
  [ "$release_dir" = "/opt/.mooncen-release-${token}" ] &&
    [ -d "$release_dir" ] && [ ! -L "$release_dir" ] || die "deployment bootstrap release is unsafe"
  [[ "$deploy_user" =~ ^[A-Za-z_][A-Za-z0-9_-]{0,63}$ ]] && id "$deploy_user" >/dev/null 2>&1 ||
    die "invalid bootstrap deploy user"
  [[ "$deadline_seconds" =~ ^[0-9]+$ ]] && [ "$deadline_seconds" -ge 300 ] &&
    [ "$deadline_seconds" -le 1800 ] || die "invalid deployment bootstrap deadline"
  validate_token "$native_intent_token"
  exec 9>"$lock_dir/operation.lock"
  chown root:root "$lock_dir/operation.lock"
  chmod 0600 "$lock_dir/operation.lock"
  flock -x 9
  validate_lock "$lock_dir" "$token"
  [ ! -e "$lock_dir/journal.env" ] && [ ! -L "$lock_dir/journal.env" ] &&
    [ ! -e "$lock_dir/$BOOTSTRAP_MANIFEST_NAME" ] && [ ! -L "$lock_dir/$BOOTSTRAP_MANIFEST_NAME" ] ||
    die "deployment bootstrap state already exists"
  [ ! -e "$temporary" ] && [ ! -L "$temporary" ] ||
    die "deployment bootstrap staging path already exists"

  cleanup_failed_bootstrap() {
    local status=$?
    trap - EXIT HUP INT TERM
    if [ "$status" -ne 0 ]; then
      rm -rf -- "$lock_dir/systemd-units" "$lock_dir/systemd-dropins"
      rm -f -- "$lock_dir/systemd-unit-names" "$lock_dir/systemd-enabled-units" \
        "$lock_dir/$SYSTEMD_UNIT_METADATA_NAME" "$lock_dir/$SYSTEMD_DROPIN_METADATA_NAME" \
        "$temporary" "$lock_dir/$BOOTSTRAP_MANIFEST_NAME" "$lock_dir/operation.lock"
    fi
    exit "$status"
  }
  trap cleanup_failed_bootstrap EXIT
  trap 'exit 130' HUP INT TERM
  backup_systemd_units "$lock_dir"
  boot_id="$(current_boot_id)"
  {
    printf 'VERSION=1\n'
    printf 'TOKEN=%s\n' "$token"
    printf 'PHASE=watching\n'
    printf 'RELEASE_DIR=%s\n' "$release_dir"
    printf 'DEPLOY_USER=%s\n' "$deploy_user"
    printf 'BOOT_ID=%s\n' "$boot_id"
    printf 'DEADLINE_EPOCH=%s\n' "$(( $(date +%s) + deadline_seconds ))"
    printf 'NATIVE_INTENT_TOKEN=%s\n' "$native_intent_token"
  } > "$temporary"
  chown root:root "$temporary"
  chmod 0600 "$temporary"
  durability_barrier "$temporary"
  mv -fT -- "$temporary" "$lock_dir/$BOOTSTRAP_MANIFEST_NAME"
  durability_barrier "$lock_dir"
  trap - EXIT HUP INT TERM
}

abort_bootstrap_guard() {
  local lock_dir="$1"
  local token="$2"
  local entry finalized_lock result_stage
  [ "$lock_dir" = "$LOCK_DIR_EXPECTED" ] || die "unexpected deployment lock path"
  if [ ! -e "$lock_dir" ] && [ ! -L "$lock_dir" ]; then
    return 0
  fi
  validate_lock "$lock_dir" "$token"
  exec 9>"$lock_dir/operation.lock"
  flock -x 9
  if [ ! -e "$lock_dir" ] && [ ! -L "$lock_dir" ]; then
    return 0
  fi
  validate_lock "$lock_dir" "$token"
  if [ -e "$lock_dir/journal.env" ] || [ -L "$lock_dir/journal.env" ]; then
    # arm won the operation.lock race and atomically published the authoritative
    # journal. Let the caller re-evaluate that state instead of poisoning the
    # ordered boot gate with a false bootstrap-abort failure.
    load_journal "$lock_dir" "$token"
    return 0
  fi
  load_bootstrap "$lock_dir" "$token"
  entry="$(prepare_history_entry "$token")"
  if [ "$BOOTSTRAP_PHASE" = watching ]; then
    TOKEN="$token"
    restore_systemd_configuration "$lock_dir"
    systemctl daemon-reload
    validate_release_state_path "$BOOTSTRAP_RELEASE_DIR" unactivated
    preserve_directory "$BOOTSTRAP_RELEASE_DIR" "$entry/unactivated"
    validate_release_state_path "$entry/unactivated" unactivated
    [ -d "$entry/unactivated" ] || die "bootstrap release was not preserved"
    discard_partial_mutable_snapshot "$lock_dir"
    sync_recovery_filesystems
    set_bootstrap_phase "$lock_dir" "$token" finalizing
  fi
  load_bootstrap "$lock_dir" "$token"
  [ "$BOOTSTRAP_PHASE" = finalizing ] || die "bootstrap abort did not reach finalization"
  validate_release_state_path "$entry/unactivated" unactivated
  [ -d "$entry/unactivated" ] || die "bootstrap abort history is incomplete"
  discard_partial_mutable_snapshot "$lock_dir"
  result_stage="$entry/.bootstrap-result.staged"
  if [ -e "$result_stage" ] || [ -L "$result_stage" ]; then
    [ -f "$result_stage" ] && [ ! -L "$result_stage" ] ||
      die "bootstrap result staging file is unsafe"
    rm -f -- "$result_stage"
  fi
  printf 'PHASE=aborted\nTOKEN=%s\n' "$token" > "$result_stage"
  chown root:root "$result_stage"
  chmod 0600 "$result_stage"
  mv -fT -- "$result_stage" "$entry/bootstrap-result.env"
  prune_history "$entry"
  finalized_lock="$entry/finalized-bootstrap-lock"
  [ ! -e "$finalized_lock" ] && [ ! -L "$finalized_lock" ] ||
    die "finalized bootstrap lock destination already exists"
  sync_recovery_filesystems
  end_native_intent "$BOOTSTRAP_NATIVE_INTENT_TOKEN"
  mv -T -- "$lock_dir" "$finalized_lock"
  durability_barrier /opt
  disable_guard_unit "$token"
  rm -f -- "/opt/.mooncen-deploy-heartbeat-${token}" >/dev/null 2>&1 || true
  if ! rm -rf -- "$finalized_lock"; then
    echo "mooncen release guard: finalized bootstrap cleanup remains in $entry" >&2
  fi
}

arm_guard() {
  [ "$#" -eq 9 ] || die "arm expects nine arguments"
  local lock_dir="$1" token="$2" remote_dir="$3" release_dir="$4"
  local previous_dir="$5" failed_dir="$6" expected_commit="$7" deploy_user="$8"
  local deadline_seconds="$9"
  validate_token "$token"
  validate_lock "$lock_dir" "$token"
  [ "$remote_dir" = "/opt/mooncen" ] || die "unexpected application path"
  [ "$release_dir" = "/opt/.mooncen-release-${token}" ] || die "unexpected release path"
  [ "$previous_dir" = "/opt/.mooncen-previous-${token}" ] || die "unexpected previous path"
  [ "$failed_dir" = "/opt/.mooncen-failed-${token}" ] || die "unexpected failed path"
  [[ "$expected_commit" =~ ^([0-9a-f]{40}|[0-9a-f]{64})$ ]] || die "invalid expected commit"
  [[ "$deploy_user" =~ ^[A-Za-z_][A-Za-z0-9_-]{0,63}$ ]] && id "$deploy_user" >/dev/null 2>&1 ||
    die "invalid deploy user"
  [[ "$deadline_seconds" =~ ^[0-9]+$ ]] && [ "$deadline_seconds" -ge 300 ] && [ "$deadline_seconds" -le 21600 ] ||
    die "invalid deployment guard deadline"
  local temporary=""
  local unit_source="$release_dir/deploy/ubuntu/systemd/mooncen-deploy-guard@.service"
  local unit_target=/etc/systemd/system/mooncen-deploy-guard@.service
  local unit_stage="/etc/systemd/system/.mooncen-deploy-guard-${token}.staged"
  local guard_unit="mooncen-deploy-guard@${token}.service"
  local unit_mode parent_mode attempt guard_active

  cleanup_failed_arm() {
    local status=$?
    trap - EXIT HUP INT TERM
    if [ "$status" -ne 0 ]; then
      if [ -n "$temporary" ] && [ "$temporary" = "$lock_dir/.journal.$$.tmp" ]; then
        rm -f -- "$temporary"
      fi
      # The pre-published bootstrap is now the durable recovery authority.
      # Keep its systemd baseline, any partial secret snapshot, guard script,
      # heartbeat, and (if atomically published) main journal intact so either
      # abort-bootstrap or normal recovery can finish after this process exits.
    fi
    exit "$status"
  }
  trap cleanup_failed_arm EXIT
  trap 'exit 130' HUP INT TERM

  local heartbeat="/opt/.mooncen-deploy-heartbeat-${token}"
  local arm_boot_id
  exec 9>"$lock_dir/operation.lock"
  flock -x 9
  validate_lock "$lock_dir" "$token"
  [ ! -e "$lock_dir/journal.env" ] && [ ! -L "$lock_dir/journal.env" ] ||
    die "deployment journal is already published"
  load_bootstrap "$lock_dir" "$token"
  [ "$BOOTSTRAP_PHASE" = watching ] || die "deployment bootstrap is not armable"
  [ "$BOOTSTRAP_RELEASE_DIR" = "$release_dir" ] || die "deployment bootstrap release mismatch"
  [ "$BOOTSTRAP_DEPLOY_USER" = "$deploy_user" ] || die "deployment bootstrap deploy user mismatch"
  arm_boot_id="$(current_boot_id)"
  [ "$arm_boot_id" = "$BOOTSTRAP_BOOT_ID" ] || die "deployment bootstrap crossed a reboot"
  [ "$(date +%s)" -lt "$BOOTSTRAP_DEADLINE_EPOCH" ] || die "deployment bootstrap expired before arm"
  # The baseline was captured before publishing/enabling the candidate guard
  # template. Revalidate and reuse it; taking a second snapshot here would
  # incorrectly make the new template part of the rollback baseline.
  validate_systemd_configuration_backup "$lock_dir"
  [ -f "$unit_source" ] && [ ! -L "$unit_source" ] ||
    die "deployment guard unit source is unavailable or unsafe"
  unit_mode="$(stat -c '%a' "$unit_source")"
  (( (8#$unit_mode & 8#022) == 0 )) || die "deployment guard unit source mode is unsafe"
  [ -d /etc/systemd/system ] && [ ! -L /etc/systemd/system ] &&
    [ "$(stat -c '%u' /etc/systemd/system)" = 0 ] ||
    die "deployment guard unit parent is unsafe"
  parent_mode="$(stat -c '%a' /etc/systemd/system)"
  (( (8#$parent_mode & 8#022) == 0 )) || die "deployment guard unit parent mode is unsafe"
  if [ -e "$unit_target" ] || [ -L "$unit_target" ]; then
    [ -f "$unit_target" ] && [ ! -L "$unit_target" ] &&
      [ "$(stat -c '%u' "$unit_target")" = 0 ] ||
      die "deployment guard unit target is unsafe"
  fi
  if [ -e "$unit_stage" ] || [ -L "$unit_stage" ]; then
    [ -f "$unit_stage" ] && [ ! -L "$unit_stage" ] &&
      [ "$(stat -c '%u' "$unit_stage")" = 0 ] ||
      die "deployment guard unit staging path is unsafe"
    rm -f -- "$unit_stage"
  fi
  # Publish, persist, enable, and start the watcher while operation.lock is
  # held. bootstrap abort/reclaim cannot restore the baseline between these
  # steps and journal publication.
  install -o root -g root -m 0644 "$unit_source" "$unit_stage"
  durability_barrier "$unit_stage"
  mv -fT -- "$unit_stage" "$unit_target"
  durability_barrier /etc/systemd/system
  systemctl daemon-reload
  systemctl enable "$guard_unit"
  systemctl is-enabled --quiet "$guard_unit" || die "deployment guard unit was not enabled"
  durability_barrier /etc/systemd/system
  systemctl start --no-block "$guard_unit"

  local had_active=0
  if [ -e "$remote_dir" ] || [ -L "$remote_dir" ]; then
    [ -d "$remote_dir" ] && [ ! -L "$remote_dir" ] || die "active application path is unsafe"
    had_active=1
  fi
  backup_mutable_artifacts "$lock_dir"
  validate_mutable_artifact_backup "$lock_dir"
  validate_restored_mutable_artifacts "$lock_dir"
  backup_external_active_units "$lock_dir"
  install -o "$deploy_user" -g "$(id -gn "$deploy_user")" -m 0600 /dev/null "$heartbeat"
  [ "$(date +%s)" -lt "$BOOTSTRAP_DEADLINE_EPOCH" ] || die "deployment bootstrap expired during arm"
  temporary="$lock_dir/.journal.$$.tmp"
  [ ! -e "$temporary" ] && [ ! -L "$temporary" ] ||
    die "deployment journal staging path already exists"
  {
    printf 'VERSION=1\n'
    printf 'TOKEN=%s\n' "$token"
    printf 'PHASE=prepared\n'
    printf 'REMOTE_DIR=%s\n' "$remote_dir"
    printf 'RELEASE_DIR=%s\n' "$release_dir"
    printf 'PREVIOUS_DIR=%s\n' "$previous_dir"
    printf 'FAILED_DIR=%s\n' "$failed_dir"
    printf 'HEARTBEAT=%s\n' "$heartbeat"
    printf 'EXPECTED_COMMIT=%s\n' "$expected_commit"
    printf 'HAD_ACTIVE=%s\n' "$had_active"
    printf 'ARM_BOOT_ID=%s\n' "$arm_boot_id"
    printf 'DEADLINE_EPOCH=%s\n' "$(( $(date +%s) + deadline_seconds ))"
    printf 'NATIVE_INTENT_TOKEN=%s\n' "$BOOTSTRAP_NATIVE_INTENT_TOKEN"
  } > "$temporary"
  chown root:root "$temporary"
  chmod 0600 "$temporary"
  durability_barrier "$temporary"
  mv -fT -- "$temporary" "$lock_dir/journal.env"
  durability_barrier "$lock_dir"
  # journal.env is now the durable recovery authority. Release operation.lock
  # before waiting for systemd: ExecStartPre may be blocked in boot-recover on
  # this same lock at the bootstrap deadline boundary.
  flock -u 9
  exec 9>&-
  guard_active=0
  for ((attempt=0; attempt<30; attempt++)); do
    if systemctl is-active --quiet "$guard_unit"; then
      guard_active=1
      break
    fi
    sleep 1
  done
  [ "$guard_active" = 1 ] || die "deployment guard did not become active after journal publication"
  trap - EXIT HUP INT TERM
}

finalize_commit_release() {
  local lock_dir="$1"
  local token="$2"
  local actual_commit entry provenance_target provenance_stage
  [ "$PHASE" = committing ] || die "commit finalization requires committing phase"
  [ -f "$REMOTE_DIR/.deploy-info" ] && [ ! -L "$REMOTE_DIR/.deploy-info" ] ||
    die "active release provenance is missing or unsafe"
  actual_commit="$(awk -F= '$1=="DEPLOY_COMMIT" {count++; value=$2} END {if(count!=1) exit 1; print value}' "$REMOTE_DIR/.deploy-info")" ||
    die "active release commit is missing"
  [ "$actual_commit" = "$EXPECTED_COMMIT" ] || die "active release commit does not match the journal"
  validate_candidate_release "$REMOTE_DIR"

  entry="$(prepare_history_entry "$token")"
  if [ "$HAD_ACTIVE" = 1 ]; then
    if [ ! -e "$PREVIOUS_DIR" ] && [ ! -L "$PREVIOUS_DIR" ] &&
       [ ! -e "$entry/previous" ] && [ ! -L "$entry/previous" ]; then
      die "previous release disappeared during commit finalization"
    fi
    preserve_directory "$PREVIOUS_DIR" "$entry/previous"
    [ -d "$entry/previous" ] && [ ! -L "$entry/previous" ] ||
      die "committed previous release history is unsafe"
  else
    [ ! -e "$PREVIOUS_DIR" ] && [ ! -L "$PREVIOUS_DIR" ] &&
      [ ! -e "$entry/previous" ] && [ ! -L "$entry/previous" ] ||
      die "first deployment unexpectedly has a previous release"
  fi

  provenance_target="$entry/deploy-info"
  provenance_stage="$entry/.deploy-info.staged"
  if [ -e "$provenance_target" ] || [ -L "$provenance_target" ]; then
    [ -f "$provenance_target" ] && [ ! -L "$provenance_target" ] ||
      die "committed release provenance target is unsafe"
  fi
  if [ -e "$provenance_stage" ] || [ -L "$provenance_stage" ]; then
    [ -f "$provenance_stage" ] && [ ! -L "$provenance_stage" ] &&
      [ "$(stat -c '%U:%G:%a' "$provenance_stage")" = "root:root:600" ] ||
      die "committed release provenance staging file is unsafe"
    rm -f -- "$provenance_stage"
  fi
  install -o root -g root -m 0600 "$REMOTE_DIR/.deploy-info" "$provenance_stage"
  mv -fT -- "$provenance_stage" "$provenance_target"
  set_phase "$lock_dir" "$token" finalizing_commit
  resume_release_finalization "$lock_dir" "$token" finalizing_commit
}

commit_release() {
  local lock_dir="$1"
  local token="$2"
  local preflight_commit
  [ "$lock_dir" = "$LOCK_DIR_EXPECTED" ] || die "unexpected deployment lock path"
  if [ ! -e "$lock_dir" ] && [ ! -L "$lock_dir" ]; then
    return 0
  fi
  validate_lock "$lock_dir" "$token"
  exec 9>"$lock_dir/operation.lock"
  flock -x 9
  if [ ! -e "$lock_dir" ] && [ ! -L "$lock_dir" ]; then
    return 0
  fi
  validate_lock "$lock_dir" "$token"
  load_journal "$lock_dir" "$token"
  case "$PHASE" in
    activated|verified)
      # Keep rollback semantics until every read-only commit precondition has
      # passed. Only then does committing become the durable decision.
      [ -f "$REMOTE_DIR/.deploy-info" ] && [ ! -L "$REMOTE_DIR/.deploy-info" ] ||
        die "active release provenance is missing or unsafe"
      preflight_commit="$(awk -F= '$1=="DEPLOY_COMMIT" {count++; value=$2} END {if(count!=1) exit 1; print value}' "$REMOTE_DIR/.deploy-info")" ||
        die "active release commit is missing"
      [ "$preflight_commit" = "$EXPECTED_COMMIT" ] ||
        die "active release commit does not match the journal"
      validate_candidate_release "$REMOTE_DIR"
      # committing is the irreversible decision: after it recovery must finish
      # the new release instead of restoring the old one. Flush candidate code,
      # systemd state, and all managed mutable mounts before persisting it.
      sync_recovery_filesystems
      set_phase "$lock_dir" "$token" committing
      ;;
    committing)
      ;;
    finalizing_commit|committed)
      resume_release_finalization "$lock_dir" "$token" "$PHASE"
      return 0
      ;;
    *) die "deployment cannot commit from phase $PHASE" ;;
  esac
  load_journal "$lock_dir" "$token"
  finalize_commit_release "$lock_dir" "$token"
}

boot_recover_guard() {
  local lock_dir="$1"
  local token="$2"
  local boot_id now
  [ "$lock_dir" = "$LOCK_DIR_EXPECTED" ] || die "unexpected deployment lock path"
  while true; do
    if [ ! -e "$lock_dir" ] && [ ! -L "$lock_dir" ]; then
      return 0
    fi
    validate_lock "$lock_dir" "$token"
    # Once atomically published, the main journal is always authoritative even
    # though bootstrap.env remains until terminal lock finalization.
    if [ -e "$lock_dir/journal.env" ] || [ -L "$lock_dir/journal.env" ]; then
      load_journal "$lock_dir" "$token"
      boot_id="$(current_boot_id)"
      if [ "$boot_id" != "$ARM_BOOT_ID" ]; then
        echo "mooncen release guard: reboot detected with an unfinished deployment; recovering before service startup" >&2
        MOONCEN_BOOT_RECOVERY=1 recover_release "$lock_dir" "$token"
      fi
      return 0
    fi

    load_bootstrap "$lock_dir" "$token"
    boot_id="$(current_boot_id)"
    now="$(date +%s)"
    if [ "$BOOTSTRAP_PHASE" = finalizing ] ||
       [ "$boot_id" != "$BOOTSTRAP_BOOT_ID" ] ||
       [ "$now" -ge "$BOOTSTRAP_DEADLINE_EPOCH" ]; then
      echo "mooncen release guard: incomplete deployment bootstrap detected; restoring the pre-deploy systemd baseline" >&2
      abort_bootstrap_guard "$lock_dir" "$token"
      # abort-bootstrap may have observed that arm won operation.lock and
      # published journal.env. Loop once more so that journal takes priority
      # (including reboot recovery) instead of releasing the boot gate early.
      continue
    fi
    # On the original boot the candidate deploy process is still allowed to
    # publish the journal. ExecStartPre remains active, preserving all Before=
    # ordering edges, until publication or bootstrap rollback completes.
    sleep 1
  done
}

watch_guard() {
  local lock_dir="$1"
  local token="$2"
  validate_token "$token"
  while true; do
    if [ ! -e "$lock_dir" ] && [ ! -L "$lock_dir" ]; then
      exit 0
    fi
    validate_lock "$lock_dir" "$token"
    load_journal "$lock_dir" "$token"
    if [ "$(current_boot_id)" != "$ARM_BOOT_ID" ]; then
      recover_release "$lock_dir" "$token"
      exit 0
    fi
    case "$PHASE" in
      finalizing_abort|finalizing_recovery|finalizing_commit|committed|aborted|recovered)
        # Give the process holding operation.lock a chance to cross the
        # atomic lock-directory rename. If it did not, resume finalization.
        sleep 1
        if [ -e "$lock_dir" ] || [ -L "$lock_dir" ]; then
          recover_release "$lock_dir" "$token"
        fi
        exit 0
        ;;
      recovery_failed) exit 70 ;;
    esac
    now="$(date +%s)"
    heartbeat_epoch=0
    if [ -f "$HEARTBEAT" ] && [ ! -L "$HEARTBEAT" ]; then
      heartbeat_epoch="$(stat -c '%Y' "$HEARTBEAT")"
    fi
    if [ "$now" -ge "$DEADLINE_EPOCH" ] || [ $((now - heartbeat_epoch)) -gt "$HEARTBEAT_STALE_SECONDS" ]; then
      recover_release "$lock_dir" "$token"
      exit 0
    fi
    sleep 10
  done
}

command="${1:-}"
shift || true
case "$command" in
  bootstrap) bootstrap_guard "$@" ;;
  abort-bootstrap)
    [ "$#" -eq 2 ] || die "abort-bootstrap expects two arguments"
    validate_token "$2"
    abort_bootstrap_guard "$1" "$2"
    ;;
  arm) arm_guard "$@" ;;
  set-phase)
    [ "$#" -eq 3 ] || die "set-phase expects three arguments"
    validate_token "$2"
    set_phase "$1" "$2" "$3"
    ;;
  authorize-start)
    [ "$#" -eq 3 ] || die "authorize-start expects three arguments"
    validate_token "$2"
    publish_native_start_authorization "$1" "$2" "$3"
    ;;
  revoke-start)
    [ "$#" -eq 2 ] || die "revoke-start expects two arguments"
    validate_lock "$1" "$2"
    clear_native_start_authorization "$2"
    ;;
  recover)
    [ "$#" -eq 2 ] || die "recover expects two arguments"
    validate_token "$2"
    recover_release "$1" "$2"
    ;;
  commit)
    [ "$#" -eq 2 ] || die "commit expects two arguments"
    validate_token "$2"
    commit_release "$1" "$2"
    ;;
  boot-recover)
    [ "$#" -eq 2 ] || die "boot-recover expects two arguments"
    validate_token "$2"
    # A failed boot recovery must keep this ordered ExecStartPre alive. The
    # guard unit's Before= edges then prevent candidate services from starting
    # until an operator repairs the protected recovery state.
    if ! ( boot_recover_guard "$1" "$2" ); then
      echo "mooncen release guard: boot recovery failed; managed service startup remains blocked" >&2
      while true; do sleep 300; done
    fi
    ;;
  watch)
    [ "$#" -eq 2 ] || die "watch expects two arguments"
    watch_guard "$1" "$2"
    ;;
  *) die "expected bootstrap, abort-bootstrap, arm, set-phase, authorize-start, revoke-start, recover, commit, boot-recover, or watch" ;;
esac
