#!/bin/bash
set -euo pipefail
umask 077
export PATH=/usr/sbin:/usr/bin:/sbin:/bin

die() {
  printf '%s\n' "an2p reviewed runtime install: $*" >&2
  exit 78
}

trusted_entrypoint=/usr/local/sbin/mooncen-an2p-runtime-install
trust_file=/etc/mooncen-an2p/runtime-installer.trust
source_repository=/home/sgm/src/project
bootstrap=/root/mooncen-an2p-bootstrap
pair_root=/opt/mooncen-an2p-runtime
pair_releases=$pair_root/releases
control_alias_root=/opt/mooncen-an2p-control
docker_alias_root=/opt/mooncen-an2p-docker
evidence_root=$docker_alias_root/evidence
state_root=/var/lib/mooncen-an2p-runtime
build_root=$state_root/build
operator_build_root=/var/lib/mooncen-docker-operator/build
publish_journal=$state_root/install-transaction.json
control_finalize_journal=$state_root/control-finalization-transaction.json
ops_rotation_journal=$state_root/ops-rotation-transaction.json
ops_rotation_backup=$state_root/ops-rotation-previous.env
docker_user=mooncen_docker_operator
worker_user=mooncen_deployment_worker
tunnel_user=mooncen_ops_db_tunnel
manager=/usr/local/libexec/mooncen-an2p-runtime-manager
selector=/usr/local/libexec/mooncen-an2p-service-control
readonly -a legacy_user_control_units=(
  mooncen-ops-control-env.service
  mooncen-ops-db-tunnel.service
  mooncen-ops-api.service
  mooncen-deployment-worker.service
  mooncen-docker-dev.service
  mooncen-ops-console.service
  mooncen-status-agent.service
)

[ "$(id -u)" -eq 0 ] || die "run from a root console"
[ "$(hostname -s)" = an2p ] || die "unexpected host"
[ "$(readlink -f -- "$0")" = "$trusted_entrypoint" ] && [ ! -L "$0" ] || \
  die "execute only the reviewed root-installed entrypoint"
[ "$(stat -c '%U:%G:%a' "$trusted_entrypoint")" = root:root:755 ] || \
  die "runtime installer metadata is unsafe"
[ -f "$trust_file" ] && [ ! -L "$trust_file" ] && \
  [ "$(stat -c '%U:%G:%a' "$trust_file")" = root:root:600 ] || \
  die "runtime installer trust envelope is unavailable"

declare -A trust=()
while IFS='=' read -r name value; do
  [[ "$name" =~ ^[A-Z][A-Z0-9_]{1,63}$ ]] && [ -n "$value" ] && \
    [ -z "${trust[$name]+present}" ] || die "invalid trust envelope"
  trust[$name]=$value
done <"$trust_file"
trust_keys=(
  VERSION INSTALLER_SHA256 INTEGRITY_SHA256 CLEAN_SOURCE_SHA256
  PAIR_MANAGER_SHA256 HANDOFF_SHA256 REGISTRAR_SHA256
  EXPECTED_BUILD_POLICY_SHA256
)
[ "${#trust[@]}" -eq "${#trust_keys[@]}" ] || die "trust envelope key set is not exact"
for name in "${trust_keys[@]}"; do
  [ -n "${trust[$name]:-}" ] || die "trust envelope is incomplete: $name"
done
[ "${trust[VERSION]}" = 1 ] || die "unsupported trust envelope version"
for name in "${trust_keys[@]:1}"; do
  [[ "${trust[$name]}" =~ ^[0-9a-f]{64}$ ]] || die "invalid trust digest: $name"
done
[ "$(sha256sum "$trusted_entrypoint" | cut -d' ' -f1)" = \
  "${trust[INSTALLER_SHA256]}" ] || die "runtime installer bytes drifted"

verify_legacy_user_unit_masks() {
  local legacy_home legacy_uid legacy_gid
  legacy_home=$(getent passwd sgm | cut -d: -f6)
  [ "$legacy_home" = /home/sgm ] || die "legacy operator home is unexpected"
  legacy_uid=$(id -u sgm)
  legacy_gid=$(id -g sgm)
  [[ "$legacy_uid" =~ ^[0-9]+$ ]] && [ "$legacy_uid" -gt 0 ] && \
    [[ "$legacy_gid" =~ ^[0-9]+$ ]] && [ "$legacy_gid" -gt 0 ] || \
    die "legacy operator identity is invalid"
  if ! /usr/bin/python3.12 -I - "$legacy_home" "$legacy_uid" "$legacy_gid" 0 0 \
    "${legacy_user_control_units[@]}" <<'PY'
import os
import re
import stat
import sys


def mount_id(descriptor: int) -> int:
    with open(f"/proc/self/fdinfo/{descriptor}", encoding="ascii") as stream:
        values = [
            line.removeprefix("mnt_id:").strip()
            for line in stream
            if line.startswith("mnt_id:")
        ]
    if len(values) != 1 or not values[0].isdecimal():
        raise RuntimeError("directory mount identity is unavailable")
    return int(values[0], 10)


def open_directory(parent: int, name: str) -> int:
    return os.open(
        name,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        dir_fd=parent,
    )


def open_home(home: str) -> int:
    if not home.startswith("/") or home == "/" or os.path.normpath(home) != home:
        raise RuntimeError("legacy home path is not canonical")
    components = home.split("/")[1:]
    if not components or any(
        not component or component in {".", ".."} for component in components
    ):
        raise RuntimeError("legacy home path components are unsafe")
    descriptor = os.open(
        "/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    )
    try:
        for component in components:
            child = open_directory(descriptor, component)
            os.close(descriptor)
            descriptor = child
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def verify_once(
    home: str,
    expected_uid: int,
    expected_gid: int,
    expected_mask_uid: int,
    expected_mask_gid: int,
    units: list[str],
):
    descriptor = open_home(home)
    home_mount = mount_id(descriptor)
    try:
        home_metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(home_metadata.st_mode)
            or home_metadata.st_uid != expected_uid
            or home_metadata.st_gid != expected_gid
        ):
            raise RuntimeError("legacy home metadata is unsafe")
        for component in (".config", "systemd", "user"):
            child = open_directory(descriptor, component)
            os.close(descriptor)
            descriptor = child
            if mount_id(descriptor) != home_mount:
                raise RuntimeError("legacy unit directory crosses a mount boundary")
        directory_metadata = os.fstat(descriptor)
        for unit in units:
            metadata = os.stat(unit, dir_fd=descriptor, follow_symlinks=False)
            if (
                not stat.S_ISLNK(metadata.st_mode)
                or metadata.st_uid != expected_mask_uid
                or metadata.st_gid != expected_mask_gid
                or os.readlink(unit, dir_fd=descriptor) != "/dev/null"
            ):
                raise RuntimeError(f"legacy user unit mask is unsafe: {unit}")
        return (
            directory_metadata.st_dev,
            directory_metadata.st_ino,
            home_mount,
        )
    finally:
        os.close(descriptor)


if len(sys.argv) < 7:
    raise SystemExit("legacy user unit mask verifier arguments are incomplete")
home_path = sys.argv[1]
try:
    owner_uid = int(sys.argv[2], 10)
    owner_gid = int(sys.argv[3], 10)
    mask_uid = int(sys.argv[4], 10)
    mask_gid = int(sys.argv[5], 10)
except ValueError as error:
    raise SystemExit("legacy home identity is invalid") from error
unit_names = sys.argv[6:]
if (
    owner_uid < 0
    or owner_gid < 0
    or mask_uid < 0
    or mask_gid < 0
    or len(unit_names) != 7
    or len(set(unit_names)) != len(unit_names)
    or any(not re.fullmatch(r"[a-z0-9-]+\.service", unit) for unit in unit_names)
):
    raise SystemExit("legacy user unit mask verifier arguments are unsafe")
try:
    first = verify_once(
        home_path, owner_uid, owner_gid, mask_uid, mask_gid, unit_names
    )
    second = verify_once(
        home_path, owner_uid, owner_gid, mask_uid, mask_gid, unit_names
    )
except (OSError, RuntimeError) as error:
    raise SystemExit(str(error)) from error
if first != second:
    raise SystemExit("legacy user unit directory changed during verification")
PY
  then
    die "legacy user unit mask directory is unsafe"
  fi
}

ensure_account() {
  local account=$1 home=$2 record
  if ! record=$(getent passwd "$account"); then
    /usr/sbin/useradd --system --user-group --home-dir "$home" --create-home \
      --shell /usr/sbin/nologin "$account"
    record=$(getent passwd "$account")
  fi
  [ "$(printf '%s' "$record" | cut -d: -f6)" = "$home" ] && \
    [ "$(printf '%s' "$record" | cut -d: -f7)" = /usr/sbin/nologin ] && \
    [ "$(id -gn "$account")" = "$account" ] || die "account metadata drifted: $account"
  /usr/sbin/usermod --password '*' "$account"
}

assert_legacy_host_root_revoked() {
  local legacy_user=sgm legacy_uid privileged_group privileged_gid
  local -a privileged_gids=()
  [ "${SUDO_USER:-root}" = root ] ||
    die "run the reviewed installer from an independent root console"
  id "$legacy_user" >/dev/null 2>&1 || die "legacy operator account is unavailable"
  for privileged_group in docker lxd; do
    getent group "$privileged_group" >/dev/null || continue
    privileged_gid=$(getent group "$privileged_group" | cut -d: -f3)
    [[ "$privileged_gid" =~ ^[0-9]+$ ]] && [ "$privileged_gid" -gt 0 ] || \
      die "privileged group identity is invalid: $privileged_group"
    privileged_gids+=("$privileged_gid")
    ! id -nG "$legacy_user" | tr ' ' '\n' | grep -Fxq "$privileged_group" || \
      die "legacy account regained ${privileged_group} host-root"
  done
  legacy_uid=$(id -u "$legacy_user")
  if [ "${#privileged_gids[@]}" -gt 0 ]; then
    if ! /usr/bin/python3.12 -I - "$legacy_uid" "${privileged_gids[@]}" <<'PY'
import os
import pathlib
import sys
import time


uid = int(sys.argv[1], 10)
gids = frozenset(int(value, 10) for value in sys.argv[2:])
proc = pathlib.Path("/proc")


def identity(pid: int) -> tuple[int, frozenset[int], int, str]:
    status = (proc / str(pid) / "status").read_bytes()
    real_uid = None
    groups = None
    for line in status.splitlines():
        if line.startswith(b"Uid:"):
            fields = line.split()
            if len(fields) != 5:
                raise RuntimeError("process UID record is malformed")
            real_uid = int(fields[1])
        elif line.startswith(b"Groups:"):
            groups = frozenset(int(field) for field in line.split()[1:])
    if real_uid is None or groups is None:
        raise RuntimeError("process credential record is incomplete")
    process_stat = (proc / str(pid) / "stat").read_bytes()
    suffix_at = process_stat.rfind(b") ")
    suffix = process_stat[suffix_at + 2 :].split() if suffix_at >= 0 else []
    if len(suffix) < 20:
        raise RuntimeError("process stat record is malformed")
    state = suffix[0].decode("ascii")
    if len(state) != 1:
        raise RuntimeError("process state record is malformed")
    return real_uid, groups, int(suffix[19]), state


for attempt in range(2):
    offenders = []
    for entry in sorted(
        (entry for entry in os.scandir(proc) if entry.name.isdecimal()),
        key=lambda entry: int(entry.name),
    ):
        pid = int(entry.name)
        descriptor = -1
        try:
            before = identity(pid)
            if (
                before[0] != uid
                or before[3] in {"Z", "X", "x"}
                or not before[1].intersection(gids)
            ):
                continue
            descriptor = os.pidfd_open(pid, 0)
            after = identity(pid)
            if after == before:
                offenders.append(pid)
        except (FileNotFoundError, ProcessLookupError):
            pass
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    if offenders:
        raise SystemExit(
            "active retained privileged process identities: "
            + ",".join(str(pid) for pid in offenders)
        )
    if attempt == 0:
        time.sleep(0.1)
PY
    then
      die "legacy process retained docker/lxd host-root"
    fi
  fi
}

preserve_public_development_while_revoking_host_root() {
  local legacy_home unit source destination

  # The reviewed bootstrap owns the only pre-build session replacement. This
  # entrypoint merely proves that its durable boundary still holds, preserving
  # the healthy public runtime throughout clone, venv, and Docker image build.
  verify_legacy_user_unit_masks
  legacy_home=/home/sgm
  [ -d /var/lib/mooncen-an2p-legacy-user-units ] && \
    [ ! -L /var/lib/mooncen-an2p-legacy-user-units ] && \
    [ "$(stat -c '%U:%G:%a' /var/lib/mooncen-an2p-legacy-user-units)" = \
      root:root:700 ] || die "legacy user unit quarantine is unsafe"
  [ -d /var/lib/mooncen-an2p-legacy-credentials ] && \
    [ ! -L /var/lib/mooncen-an2p-legacy-credentials ] && \
    [ "$(stat -c '%U:%G:%a' /var/lib/mooncen-an2p-legacy-credentials)" = \
      root:root:700 ] || die "legacy credential quarantine is unsafe"
  for unit in "${legacy_user_control_units[@]}"; do
    destination=/etc/systemd/user/$unit
    [ -L "$destination" ] && [ "$(readlink "$destination")" = /dev/null ] || \
      die "global legacy user unit mask is unsafe: $unit"
    ! systemctl --user --machine=sgm@ is-active --quiet "$unit" && \
      ! systemctl --user --machine=sgm@ is-enabled --quiet "$unit" || \
      die "legacy control service is live before build: $unit"
  done
  for relative in cloud-deploy.ssh_config keys/cloud-deploy-ed25519 \
    ops-api.env deployment-worker.env; do
    source=$legacy_home/.config/mooncen-an2p/$relative
    destination=/var/lib/mooncen-an2p-legacy-credentials/${relative//\//_}
    [ ! -e "$source" ] && [ ! -L "$source" ] || \
      die "legacy credential remained readable before build: $relative"
    if [ -e "$destination" ] || [ -L "$destination" ]; then
      [ -f "$destination" ] && [ ! -L "$destination" ] && \
        [ "$(stat -c '%U:%G:%a' "$destination")" = root:root:600 ] || \
        die "legacy credential quarantine residue is unsafe: $relative"
    fi
  done
  verify_legacy_user_unit_masks
  assert_legacy_host_root_revoked
  verify_legacy_user_unit_masks
}

bootstrap_prerequisites() {
  [ "$#" -eq 0 ] || die "bootstrap-prerequisites accepts no arguments"
  if ! /usr/bin/dpkg-query -W -f='${Status}\n' python3.12-venv 2>/dev/null | \
    grep -Fxq 'install ok installed'; then
    /usr/bin/apt-get update
    /usr/bin/apt-get install --yes --no-install-recommends python3.12-venv
  fi
  [ "$(/usr/bin/python3.12 -c 'import sys; print(".".join(map(str,sys.version_info[:2])))')" = 3.12 ] || \
    die "exact Python 3.12 is unavailable"
  probe=$(mktemp -d /var/tmp/mooncen-an2p-venv-probe.XXXXXXXX)
  /usr/bin/python3.12 -m venv --copies "$probe/.venv"
  [ -x "$probe/.venv/bin/python" ] && [ ! -L "$probe/.venv/bin/python" ] || \
    die "Python venv copy mode did not converge"
  rm -rf -- "$probe"
  printf '%s\n' '{"prerequisites_ready":true,"schema_version":1}'
}

prepare_development_bootstrap() {
  local destination=$bootstrap/docker-development.env
  [ "$#" -eq 0 ] || die "prepare-development-bootstrap accepts no arguments"
  if [ ! -e "$bootstrap" ] && [ ! -L "$bootstrap" ]; then
    install -d -o root -g root -m 0700 "$bootstrap"
    sync -f -- "$(dirname "$bootstrap")"
  fi
  [ -d "$bootstrap" ] && [ ! -L "$bootstrap" ] && \
    [ "$(stat -c '%U:%G:%a' "$bootstrap")" = root:root:700 ] || \
    die "root-only bootstrap directory is unsafe"
  /usr/bin/python3.12 -I - "$bootstrap" "$destination" 0 0 <<'PY'
import json
import os
import pathlib
import re
import secrets
import stat
import sys


root, destination = map(pathlib.Path, sys.argv[1:3])
expected_uid, expected_gid = map(int, sys.argv[3:5])
names = (
    "COMPOSE_PROJECT_NAME",
    "MOONCEN_AUTH_SECRET",
    "MOONCEN_CORS_ORIGINS",
    "MOONCEN_DB_API_PASSWORD",
    "MOONCEN_DB_API_USER",
    "MOONCEN_DB_NAME",
    "MOONCEN_DB_PASSWORD",
    "MOONCEN_DB_USER",
    "MOONCEN_OAUTH_REDIRECT_URI",
    "MOONCEN_SITE_URL",
)
fixed = {
    "COMPOSE_PROJECT_NAME": "mooncen-dev",
    "MOONCEN_CORS_ORIGINS": "http://localhost:5174",
    "MOONCEN_DB_API_USER": "mooncen_api_login",
    "MOONCEN_DB_NAME": "mooncen",
    "MOONCEN_DB_USER": "mooncen_admin",
    "MOONCEN_OAUTH_REDIRECT_URI": "http://localhost:5174/",
    "MOONCEN_SITE_URL": "http://localhost:5174",
}
secret_names = (
    "MOONCEN_AUTH_SECRET",
    "MOONCEN_DB_API_PASSWORD",
    "MOONCEN_DB_PASSWORD",
)
secret_pattern = re.compile(r"[A-Za-z0-9_-]{64}")


def canonical(values: dict[str, str]) -> bytes:
    return "".join(f"{name}={values[name]}\n" for name in names).encode("ascii")


def validate(path: pathlib.Path) -> bytes:
    metadata = path.lstat()
    payload = path.read_bytes()
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != expected_uid
        or metadata.st_gid != expected_gid
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or not payload.endswith(b"\n")
        or b"\x00" in payload
        or b"\r" in payload
        or len(payload) > 4096
    ):
        raise SystemExit("development bootstrap metadata is unsafe")
    values: dict[str, str] = {}
    order: list[str] = []
    try:
        lines = payload.decode("ascii").splitlines()
    except UnicodeError as exc:
        raise SystemExit("development bootstrap encoding is invalid") from exc
    for line in lines:
        name, separator, value = line.partition("=")
        if not separator or not value or name in values:
            raise SystemExit("development bootstrap assignment is invalid")
        order.append(name)
        values[name] = value
    if (
        tuple(order) != names
        or any(values.get(name) != value for name, value in fixed.items())
        or any(secret_pattern.fullmatch(values.get(name, "")) is None for name in secret_names)
        or len({values[name] for name in secret_names}) != len(secret_names)
        or payload != canonical(values)
    ):
        raise SystemExit("development bootstrap contract is invalid")
    return payload


created = False
if destination.exists() or destination.is_symlink():
    validate(destination)
else:
    values = dict(fixed)
    for name in secret_names:
        value = secrets.token_urlsafe(48)
        if secret_pattern.fullmatch(value) is None:
            raise SystemExit("development secret generation failed")
        values[name] = value
    if len({values[name] for name in secret_names}) != len(secret_names):
        raise SystemExit("development secrets are not independent")
    payload = canonical(values)
    stage = root / f".{destination.name}.{os.getpid()}.tmp"
    descriptor = -1
    try:
        descriptor = os.open(
            stage,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        os.fchown(descriptor, expected_uid, expected_gid)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        validate(stage)
        if destination.exists() or destination.is_symlink():
            raise SystemExit("development bootstrap destination raced")
        os.replace(stage, destination)
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        created = True
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            stage.unlink()
        except FileNotFoundError:
            pass
    validate(destination)
result = {
    "created": created,
    "path": str(destination),
    "production_inputs_read": False,
    "schema_version": 1,
}
print(
    json.dumps(
        result,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
)
PY
}

prepare_development_bootstrap_locked() {
  install -d -o root -g root -m 0700 "$state_root"
  install -o root -g root -m 0600 /dev/null "$state_root/install.lock" \
    2>/dev/null || true
  [ -f "$state_root/install.lock" ] && [ ! -L "$state_root/install.lock" ] && \
    [ "$(stat -c '%U:%G:%a' "$state_root/install.lock")" = root:root:600 ] || \
    die "runtime installer lock is unsafe"
  exec 7<>"$state_root/install.lock"
  /usr/bin/flock -x 7
  prepare_development_bootstrap "$@"
}

if [ "${1:-}" = bootstrap-prerequisites ]; then
  shift
  bootstrap_prerequisites "$@"
  exit 0
fi

if [ "${1:-}" = prepare-development-bootstrap ]; then
  shift
  prepare_development_bootstrap_locked "$@"
  exit 0
fi

install -d -o root -g root -m 0700 "$state_root"
install -d -o root -g root -m 0700 "$build_root"
install -o root -g root -m 0600 /dev/null "$state_root/install.lock" 2>/dev/null || true
[ -f "$state_root/install.lock" ] && [ ! -L "$state_root/install.lock" ] && \
  [ "$(stat -c '%U:%G:%a' "$state_root/install.lock")" = root:root:600 ] || \
  die "runtime installer lock is unsafe"
exec 8<>"$state_root/install.lock"
/usr/bin/flock -x 8

begin_development_selection_fence() {
  local operation_lock=$state_root/operation.lock
  [ -f "$operation_lock" ] && [ ! -L "$operation_lock" ] && \
    [ "$(stat -c '%U:%G:%a' "$operation_lock")" = root:root:600 ] || \
    die "runtime operation lock is unsafe"
  exec 6<>"$operation_lock"
  /usr/bin/flock -x 6
  /usr/bin/python3.12 -I - 6 "$operation_lock" <<'PY'
import os
import pathlib
import stat
import sys


descriptor = int(sys.argv[1])
path = pathlib.Path(sys.argv[2])
descriptor_metadata = os.fstat(descriptor)
path_metadata = path.lstat()
if (
    path.is_symlink()
    or not stat.S_ISREG(descriptor_metadata.st_mode)
    or not stat.S_ISREG(path_metadata.st_mode)
    or descriptor_metadata.st_uid != 0
    or descriptor_metadata.st_gid != 0
    or stat.S_IMODE(descriptor_metadata.st_mode) != 0o600
    or (descriptor_metadata.st_dev, descriptor_metadata.st_ino)
    != (path_metadata.st_dev, path_metadata.st_ino)
):
    raise SystemExit("runtime operation lock descriptor is unsafe")
PY
  [ ! -e "$state_root/transaction.json" ] && \
    [ ! -L "$state_root/transaction.json" ] || \
    die "runtime pair transaction blocks trusted selection proof"
  export MOONCEN_AN2P_MANAGER_LOCK_FD=6
}

end_development_selection_fence() {
  unset MOONCEN_AN2P_MANAGER_LOCK_FD
  exec 6>&-
}

verify_development_selection_under_fence() {
  local pair=$1 status_json
  [ "${MOONCEN_AN2P_MANAGER_LOCK_FD:-}" = 6 ] || \
    die "development selection proof lacks the transaction fence"
  [ -L "$pair_root/current" ] && \
    [ "$(stat -c '%U:%G' "$pair_root/current")" = root:root ] && \
    [ "$(readlink "$pair_root/current")" = "releases/$pair" ] || \
    die "fenced development pair pointer changed"
  status_json=$("$selector" runtime-status)
  /usr/bin/python3.12 -I -c \
    'import json,sys; value=json.load(sys.stdin); assert value == {"docker_active":True,"docker_enabled":True,"marker":True,"native_active":[],"native_enabled":[],"schema_version":1}' \
    <<<"$status_json" || die "fenced Docker development selection is not exact"
  systemctl is-active --quiet mooncen-docker-dev.service && \
    systemctl is-enabled --quiet mooncen-docker-dev.service || \
    die "fenced Docker development service is not persistent"
  /usr/bin/python3.12 -I \
    "$pair_releases/$pair/control/tools/wait_for_an2p_http.py" \
    http://127.0.0.1:8001/health --timeout 30 >/dev/null
  /usr/bin/python3.12 -I \
    "$pair_releases/$pair/control/tools/wait_for_an2p_http.py" \
    http://127.0.0.1:5174 --timeout 30 >/dev/null
}

emit_finalization_success() {
  local pair=$1
  begin_development_selection_fence
  verify_development_selection_under_fence "$pair"
  printf '%s\n' \
    "{\"active_pair\":\"$pair\",\"control_finalized\":true,\"development_healthy\":true,\"schema_version\":1}"
  end_development_selection_fence
}

emit_rotation_success() {
  local pair=$1
  begin_development_selection_fence
  verify_development_selection_under_fence "$pair"
  printf '%s\n' \
    "{\"active_pair\":\"$pair\",\"ops_rotation_applied\":true,\"schema_version\":1}"
  end_development_selection_fence
}

verify_active_development_pair() {
  local pair=$1 phase=${2:-pending} source_tree status_json
  local -a expiry_option=()
  [[ "$pair" =~ ^runtime-pair\.([0-9a-f]{40})\.([0-9a-f]{40})\.([0-9a-f]{64})$ ]] || \
    die "runtime pair name is invalid"
  source_tree=${BASH_REMATCH[2]}
  [ -x "$manager" ] && [ ! -L "$manager" ] && \
    [ "$(stat -c '%U:%G:%a' "$manager")" = root:root:755 ] && \
    [ "$(sha256sum "$manager" | cut -d' ' -f1)" = "${trust[PAIR_MANAGER_SHA256]}" ] || \
    die "installed runtime manager is unavailable"
  [ -x "$selector" ] && \
    [ ! -L "$selector" ] && \
    [ "$(stat -c '%U:%G:%a' "$selector")" = \
      root:root:755 ] || die "installed development selector is unavailable"
  "$manager" validate "$pair" >/dev/null
  [ -L "$pair_root/current" ] && \
    [ "$(stat -c '%U:%G' "$pair_root/current")" = root:root ] && \
    [ "$(readlink "$pair_root/current")" = "releases/$pair" ] || \
    die "finalization pair is not the exact active pair"
  [ ! -e "$publish_journal" ] && [ ! -L "$publish_journal" ] || \
    die "development installation transaction is incomplete"
  [ -f /etc/mooncen-an2p/docker-development-enabled ] && \
    [ ! -L /etc/mooncen-an2p/docker-development-enabled ] && \
    [ "$(stat -c '%U:%G:%a:%s' /etc/mooncen-an2p/docker-development-enabled)" = \
      root:root:644:0 ] || die "Docker development selection marker is unsafe"
  status_json=$("$selector" runtime-status)
  /usr/bin/python3.12 -I -c \
    'import json,sys; value=json.load(sys.stdin); assert value == {"docker_active":True,"docker_enabled":True,"marker":True,"native_active":[],"native_enabled":[],"schema_version":1}' \
    <<<"$status_json" || die "exclusive Docker development selection is not active"
  systemctl is-active --quiet mooncen-docker-dev.service && \
    systemctl is-enabled --quiet mooncen-docker-dev.service || \
    die "persistent Docker development service is not active"
  if [ "$phase" = pending ]; then
    for unit in mooncen-ops-api.service mooncen-deployment-worker.service \
      mooncen-ops-db-tunnel.service mooncen-ops-status-agent.service; do
      ! systemctl is-active --quiet "$unit" && ! systemctl is-enabled --quiet "$unit" || \
        die "control finalization prerequisite is already live: $unit"
    done
  elif [ "$phase" = completed ]; then
    expiry_option=(--allow-expired-receipt)
  elif [ "$phase" != finalized ] && [ "$phase" != residue ]; then
    die "internal development verification phase is invalid"
  fi
  ensure_account "$docker_user" /var/lib/mooncen-docker-operator
  /usr/sbin/runuser --user "$docker_user" -- /usr/bin/env -i \
    HOME=/var/lib/mooncen-docker-operator PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    LANG=C.UTF-8 LC_ALL=C.UTF-8 DOCKER_HOST=unix:///var/run/docker.sock \
    PYTHONDONTWRITEBYTECODE=1 \
    /usr/bin/python3 -I "$docker_alias_root/current/deploy/an2p/validate_docker_release.py" \
    --system-runtime --reader-group "$docker_user" \
    --project-root "$docker_alias_root/current" \
    --environment-file "$docker_alias_root/current/development.env" \
    --runtime-compose-file "$docker_alias_root/current/compose.yaml" \
    --activation-file "$docker_alias_root/current/activation.json" \
    --require-running "${expiry_option[@]}" >/dev/null || \
    die "fresh running Docker development evidence is unavailable"
  /usr/bin/python3.12 -I \
    "$pair_releases/$pair/control/tools/wait_for_an2p_http.py" \
    http://127.0.0.1:8001/health --timeout 30 >/dev/null
  /usr/bin/python3.12 -I \
    "$pair_releases/$pair/control/tools/wait_for_an2p_http.py" \
    http://127.0.0.1:5174 --timeout 30 >/dev/null
  if [ "$phase" != completed ]; then
    verify_pending_finalization "$pair" "$source_tree"
  fi
}

verify_pending_finalization() {
  local pair=$1 source_tree=$2
  /usr/bin/python3.12 -I - \
    "$state_root/pending-control-finalization.json" \
    "$docker_alias_root/current/activation.json" "$pair" "$source_tree" <<'PY'
import json
import pathlib
import stat
import sys

pending_path, activation_path = map(pathlib.Path, sys.argv[1:3])
pair, source_tree = sys.argv[3:]
metadata = pending_path.lstat()
payload = pending_path.read_bytes()
if (
    pending_path.is_symlink()
    or not stat.S_ISREG(metadata.st_mode)
    or metadata.st_uid != 0
    or metadata.st_gid != 0
    or stat.S_IMODE(metadata.st_mode) != 0o600
):
    raise SystemExit(78)
try:
    pending = json.loads(payload.decode("ascii"))
    activation = json.loads(activation_path.read_text(encoding="ascii"))
except (UnicodeError, ValueError, OSError):
    raise SystemExit(78) from None
expected = {
    "environment": "development",
    "environment_sha256": activation.get("environment_sha256"),
    "pair": pair,
    "receipt_digest": activation.get("receipt_digest"),
    "release_digest": activation.get("release_digest"),
    "schema_version": 1,
    "source_tree": source_tree,
    "target": "an2p-dev",
    "target_identity": activation.get("target_identity"),
}
canonical = (
    json.dumps(
        expected,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    + b"\n"
)
if pending != expected or payload != canonical:
    raise SystemExit(78)
PY
}

preflight_control_bootstrap() {
  local pair=$1 name path binding_path
  binding_path=$state_root/pending-control-finalization.json
  if [ ! -e "$binding_path" ] && [ ! -L "$binding_path" ]; then
    binding_path=$state_root/control-finalizations/$pair.json
  fi
  [ -d "$bootstrap" ] && [ ! -L "$bootstrap" ] && \
    [ "$(stat -c '%U:%G:%a' "$bootstrap")" = root:root:700 ] || \
    die "root-only control bootstrap directory is unavailable"
  for name in control-secrets.env ops-auth-secret ops-api.env deployment-worker.env \
    deploy-ssh_config deploy-id_ed25519 deploy-known_hosts \
    status-ssh_config status-id_ed25519 status-known_hosts \
    db-ssh_config db-id_ed25519 db-known_hosts; do
    path=$bootstrap/$name
    [ -f "$path" ] && [ ! -L "$path" ] && \
      [ "$(stat -c '%U:%G:%a' "$path")" = root:root:600 ] || \
      die "control bootstrap input is unsafe: $name"
  done
  /usr/bin/python3.12 -I - "$bootstrap" "$pair_releases/$pair/control" \
    "$binding_path" <<'PY'
import json
import pathlib
import re
import subprocess
import sys


bootstrap, control, pending_path = map(pathlib.Path, sys.argv[1:])
templates = {
    "deploy-ssh_config": "cloud-container-deploy.ssh_config",
    "status-ssh_config": "cloud-container-status.ssh_config",
    "db-ssh_config": "cloud-ops-db.ssh_config",
}
for destination, template in templates.items():
    if (bootstrap / destination).read_bytes() != (
        control / "deploy/an2p/local" / template
    ).read_bytes():
        raise SystemExit("transport SSH config differs from the immutable pair")
known_hosts = (control / "deploy/an2p/local/cloud-deploy.known_hosts").read_bytes()
for destination in ("deploy-known_hosts", "status-known_hosts", "db-known_hosts"):
    if (bootstrap / destination).read_bytes() != known_hosts:
        raise SystemExit("transport host pin differs from the immutable pair")

public_blobs = []
private_key_comments = {
    "deploy-id_ed25519": "mooncen-an2p-container-deploy-20260819",
    "status-id_ed25519": "mooncen-an2p-container-status-20260819",
    "db-id_ed25519": "mooncen-an2p-ops-db-20260819",
}
for name, expected_comment in private_key_comments.items():
    completed = subprocess.run(
        ("/usr/bin/ssh-keygen", "-y", "-f", str(bootstrap / name)),
        cwd="/",
        env={"HOME": "/root", "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=15,
    )
    fields = completed.stdout.decode("ascii", errors="strict").strip().split()
    if (
        completed.returncode != 0
        or completed.stderr
        or len(fields) != 3
        or fields[0] != "ssh-ed25519"
        or fields[2] != expected_comment
    ):
        raise SystemExit("control-plane private key is not a valid Ed25519 key")
    public_blobs.append(fields[1])
if len(set(public_blobs)) != 3:
    raise SystemExit("control-plane private keys must derive distinct public keys")

pending = json.loads(pending_path.read_text(encoding="ascii"))
expected_identity = pending["target_identity"]
control_order = (
    "DB_API_PASSWORD",
    "DB_API_USER",
    "DB_DEPLOYMENT_WORKER_PASSWORD",
    "DB_DEPLOYMENT_WORKER_USER",
    "DB_NAME",
    "MOONCEN_OPS_LOGIN_ID",
    "MOONCEN_OPS_PASSWORD_HASH",
    "OPS_CONTAINER_DEV_TARGET_IDENTITY",
)
control_payload = (bootstrap / "control-secrets.env").read_text(encoding="utf-8")
control_values = {}
control_names = []
for line in control_payload.splitlines():
    key, separator, value = line.partition("=")
    if not separator or not value or key in control_values:
        raise SystemExit("protected control envelope is invalid")
    control_names.append(key)
    control_values[key] = value
if (
    tuple(control_names) != control_order
    or control_payload != "".join(f"{name}={control_values[name]}\n" for name in control_order)
    or control_values["OPS_CONTAINER_DEV_TARGET_IDENTITY"] != expected_identity
    or control_values["DB_DEPLOYMENT_WORKER_USER"]
    != "mooncen_deployment_worker_login"
):
    raise SystemExit("protected control envelope is not exact or pair-bound")
secret = (bootstrap / "ops-auth-secret").read_bytes()
if re.fullmatch(rb"[A-Za-z0-9_-]{64}\n", secret) is None:
    raise SystemExit("independent Ops authentication secret is invalid")
ops_auth_secret = secret[:-1].decode("ascii")
if ops_auth_secret in control_values.values():
    raise SystemExit("independent Ops authentication secret was reused")
common = (
    ("ENVIRONMENT", "production"),
    ("DB_HOST", "127.0.0.1"),
    ("DB_PORT", "15432"),
    ("DB_NAME", control_values["DB_NAME"]),
    ("DB_SSLMODE", "require"),
    ("DB_CONNECT_TIMEOUT", "5"),
    ("DB_STATEMENT_TIMEOUT_MS", "15000"),
    ("DB_LOCK_TIMEOUT_MS", "3000"),
)
api = (
    *common,
    ("MOONCEN_API_PROFILE", "ops"),
    ("MOONCEN_AUTH_COOKIE_PREFIX", "mooncen_ops"),
    ("MOONCEN_AUTH_COOKIE_SECURE", "false"),
    ("MOONCEN_LOCAL_LOOPBACK_OPS_HTTP", "true"),
    ("DB_OWNER_USER", "mooncen_admin"),
    ("DB_API_USER", control_values["DB_API_USER"]),
    ("DB_API_PASSWORD", control_values["DB_API_PASSWORD"]),
    ("AUTH_SECRET", ops_auth_secret),
    ("MOONCEN_OPS_LOGIN_ID", control_values["MOONCEN_OPS_LOGIN_ID"]),
    ("MOONCEN_OPS_PASSWORD_HASH", control_values["MOONCEN_OPS_PASSWORD_HASH"]),
    ("MOONCEN_OPS_SINGLE_ACCOUNT_ONLY", "true"),
    ("OPS_LOCAL_CRAWLER_RUNTIME_ENABLED", "false"),
    ("OPS_CRAWLER_API_DB_REQUIRED", "false"),
    ("OPS_DEPLOY_REQUIRED_AGENT_HOSTNAME", "an2p"),
    ("OPS_CONTAINER_DEV_TARGET_IDENTITY", expected_identity),
    ("MOONCEN_TRUSTED_HOSTS", "localhost,127.0.0.1,[::1]"),
    ("LOG_LEVEL", "INFO"),
)
worker = (
    *common,
    ("DB_OWNER_USER", "mooncen_admin"),
    ("OPS_DEPLOY_QUEUE_DB_HOST", "127.0.0.1"),
    ("OPS_DEPLOY_QUEUE_DB_PORT", "15432"),
    ("OPS_DEPLOY_QUEUE_DB_NAME", control_values["DB_NAME"]),
    ("OPS_DEPLOY_QUEUE_DB_USER", control_values["DB_DEPLOYMENT_WORKER_USER"]),
    (
        "OPS_DEPLOY_QUEUE_DB_PASSWORD",
        control_values["DB_DEPLOYMENT_WORKER_PASSWORD"],
    ),
    ("OPS_DEPLOY_AGENT_EXCLUSIVE", "true"),
    ("OPS_DEPLOY_REQUIRED_AGENT_HOSTNAME", "an2p"),
    ("OPS_CONTAINER_DEV_TARGET_IDENTITY", expected_identity),
    ("OPS_CONTAINER_RELEASE_ROOT", "/var/lib/mooncen-deployment-worker/releases"),
    ("OPS_LOCAL_CRAWLER_RUNTIME_ENABLED", "false"),
    ("LOG_LEVEL", "INFO"),
)
header = "# Generated locally by prepare_an2p_ops_control.py; never commit.\n"
for environment_name, entries in (("ops-api.env", api), ("deployment-worker.env", worker)):
    expected = header + "".join(f"{name}={value}\n" for name, value in entries)
    if (bootstrap / environment_name).read_text(encoding="utf-8") != expected:
        raise SystemExit("generated control environment is stale or incomplete")
PY
}

stage_registration_access() {
  ensure_account "$worker_user" /var/lib/mooncen-deployment-worker
  ensure_account "$tunnel_user" /var/lib/mooncen-ops-db-tunnel
  worker_gid=$(id -g "$worker_user")
  for account in "$worker_user" "$tunnel_user"; do
    for group in docker lxd; do
      getent group "$group" >/dev/null || continue
      ! id -nG "$account" | tr ' ' '\n' | grep -Fxq "$group" || \
        die "registration account inherited host-root group: $account/$group"
    done
  done
  install -d -o root -g root -m 0755 /etc/mooncen-an2p
  install -o root -g "$worker_user" -m 0640 "$bootstrap/deployment-worker.env" \
    /etc/mooncen-an2p/deployment-worker.env
  install -d -o root -g "$worker_user" -m 0750 /etc/mooncen-an2p/deploy-transport
  for transport_file in deploy-ssh_config:ssh_config deploy-id_ed25519:id_ed25519 \
    deploy-known_hosts:known_hosts; do
    install -o root -g "$worker_user" -m 0640 \
      "$bootstrap/${transport_file%%:*}" \
      "/etc/mooncen-an2p/deploy-transport/${transport_file##*:}"
  done
  install -d -o root -g mooncen_ops_api -m 0750 \
    /etc/mooncen-an2p/status-transport
  for transport_file in status-ssh_config:ssh_config status-id_ed25519:id_ed25519 \
    status-known_hosts:known_hosts; do
    install -o root -g mooncen_ops_api -m 0640 \
      "$bootstrap/${transport_file%%:*}" \
      "/etc/mooncen-an2p/status-transport/${transport_file##*:}"
  done
  install -d -o root -g "$tunnel_user" -m 0750 /etc/mooncen-an2p/db-tunnel
  for transport_file in db-ssh_config:ssh_config db-id_ed25519:id_ed25519 \
    db-known_hosts:known_hosts; do
    install -o root -g "$tunnel_user" -m 0640 \
      "$bootstrap/${transport_file%%:*}" \
      "/etc/mooncen-an2p/db-tunnel/${transport_file##*:}"
  done
  install -d -o root -g "$worker_user" -m 0750 \
    /var/lib/mooncen-deployment-worker/releases
  install -d -o "$worker_user" -g "$worker_user" -m 0700 \
    /var/lib/mooncen-deployment-worker/state
  systemctl enable mooncen-ops-db-tunnel.service
  systemctl restart mooncen-ops-db-tunnel.service
  systemctl is-active --quiet mooncen-ops-db-tunnel.service || \
    die "registration DB tunnel did not become active"
}

expect_exact_transport_denial() {
  local expected_status=$1 label=$2 status
  shift 2
  set +e
  /usr/bin/timeout --foreground --signal=TERM --kill-after=2s 15s \
    "$@" </dev/null >/dev/null 2>&1
  status=$?
  set -e
  [ "$status" -eq "$expected_status" ] || \
    die "$label denial returned $status instead of $expected_status"
}

expect_exact_pty_denial() {
  local label=$1 status
  shift
  set +e
  /usr/bin/timeout --foreground --signal=TERM --kill-after=2s 15s \
    "$@" </dev/null >/dev/null 2>&1
  status=$?
  set -e
  [ "$status" -eq 255 ] || \
    die "$label denial returned $status instead of 255"
}

expect_administrative_forward_denial() {
  local label=$1 stderr_file status valid=false
  shift
  [ -d /run/mooncen-an2p-runtime ] && \
    [ ! -L /run/mooncen-an2p-runtime ] && \
    [ "$(stat -c '%U:%G:%a' /run/mooncen-an2p-runtime)" = root:root:755 ] || \
    die "transport probe runtime directory is unsafe"
  stderr_file=$(mktemp /run/mooncen-an2p-runtime/.forward-denial.XXXXXX)
  [ -f "$stderr_file" ] && [ ! -L "$stderr_file" ] && \
    [ "$(stat -c '%U:%G:%a' "$stderr_file")" = root:root:600 ] || {
      rm -f -- "$stderr_file"
      die "forward denial probe output is unsafe"
    }
  set +e
  /usr/bin/timeout --foreground --signal=TERM --kill-after=2s 15s \
    "$@" </dev/null >/dev/null 2>"$stderr_file"
  status=$?
  set -e
  if [ "$status" -eq 255 ] && [ "$(stat -c '%s' "$stderr_file")" -le 4096 ] && \
    grep -Fq 'administratively prohibited' "$stderr_file"; then
    valid=true
  fi
  rm -f -- "$stderr_file"
  [ "$valid" = true ] || \
    die "$label was not an authoritative administrative forwarding denial"
}

assert_forbidden_probe_listener_absent() {
  [ -z "$(/usr/bin/ss -H -ltn 'sport = :15433')" ] || \
    die "forbidden DB forwarding probe left a loopback listener"
}

verify_control_transport_negative_boundaries() {
  assert_forbidden_probe_listener_absent
  expect_exact_pty_denial "status endpoint PTY" \
    /usr/sbin/runuser --user mooncen_ops_api -- /usr/bin/env -i \
    HOME=/var/lib/mooncen-ops-api LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    /usr/bin/ssh -tt -F /etc/mooncen-an2p/status-transport/ssh_config \
    cloud-container-status \
    '/usr/bin/sudo -n -- /usr/local/libexec/mooncen-container-release status'
  expect_exact_pty_denial "deploy endpoint PTY" \
    /usr/sbin/runuser --user "$worker_user" -- /usr/bin/env -i \
    HOME=/var/lib/mooncen-deployment-worker LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    /usr/bin/ssh -tt -F /etc/mooncen-an2p/deploy-transport/ssh_config \
    cloud-container-deploy \
    '/usr/bin/sudo -n -- /usr/local/libexec/mooncen-container-release status'
  expect_exact_transport_denial 255 "status endpoint SFTP" \
    /usr/sbin/runuser --user mooncen_ops_api -- /usr/bin/env -i \
    HOME=/var/lib/mooncen-ops-api LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    /usr/bin/sftp -b /dev/null \
    -F /etc/mooncen-an2p/status-transport/ssh_config cloud-container-status
  expect_exact_transport_denial 255 "deploy endpoint SFTP" \
    /usr/sbin/runuser --user "$worker_user" -- /usr/bin/env -i \
    HOME=/var/lib/mooncen-deployment-worker LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    /usr/bin/sftp -b /dev/null \
    -F /etc/mooncen-an2p/deploy-transport/ssh_config cloud-container-deploy
  expect_administrative_forward_denial "status endpoint DB forwarding" \
    /usr/sbin/runuser --user mooncen_ops_api -- /usr/bin/env -i \
    HOME=/var/lib/mooncen-ops-api LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    /usr/bin/ssh -F /etc/mooncen-an2p/status-transport/ssh_config \
    -o ExitOnForwardFailure=yes -W 127.0.0.1:5432 cloud-container-status
  expect_administrative_forward_denial "deploy endpoint DB forwarding" \
    /usr/sbin/runuser --user "$worker_user" -- /usr/bin/env -i \
    HOME=/var/lib/mooncen-deployment-worker LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    /usr/bin/ssh -F /etc/mooncen-an2p/deploy-transport/ssh_config \
    -o ExitOnForwardFailure=yes -W 127.0.0.1:5432 cloud-container-deploy
  # -W requests the forbidden direct-tcpip channel without ever publishing a
  # local listener.  The already-live 15432 tunnel proves that this exact DB
  # identity authenticated before the negative PermitOpen check.
  expect_administrative_forward_denial \
    "DB endpoint forbidden 127.0.0.1:22 forward" \
    /usr/sbin/runuser --user "$tunnel_user" -- /usr/bin/env -i \
    HOME=/var/lib/mooncen-ops-db-tunnel LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    /usr/bin/ssh -F /etc/mooncen-an2p/db-tunnel/ssh_config \
    -o ExitOnForwardFailure=yes -W 127.0.0.1:22 cloud-ops-db
  assert_forbidden_probe_listener_absent
}

verify_control_transports() {
  local pair=$1 pending_identity status_identity_json deploy_identity_json \
    status_json deploy_json status binding_path
  binding_path=$state_root/pending-control-finalization.json
  if [ ! -e "$binding_path" ] && [ ! -L "$binding_path" ]; then
    binding_path=$state_root/control-finalizations/$pair.json
  fi
  pending_identity=$(/usr/bin/python3.12 -I -c \
    'import json,sys; print(json.load(open(sys.argv[1], encoding="ascii"))["target_identity"])' \
    "$binding_path")
  [[ "$pending_identity" =~ ^[0-9a-f]{64}$ ]] || \
    die "pending transport target identity is invalid"
  status_identity_json=$(/usr/sbin/runuser --user mooncen_ops_api -- /usr/bin/env -i \
    HOME=/var/lib/mooncen-ops-api LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    /usr/bin/ssh -F /etc/mooncen-an2p/status-transport/ssh_config \
    cloud-container-status \
    '/usr/bin/sudo -n -- /usr/local/libexec/mooncen-container-release target-identity')
  deploy_identity_json=$(/usr/sbin/runuser --user "$worker_user" -- /usr/bin/env -i \
    HOME=/var/lib/mooncen-deployment-worker LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    /usr/bin/ssh -F /etc/mooncen-an2p/deploy-transport/ssh_config \
    cloud-container-deploy \
    '/usr/bin/sudo -n -- /usr/local/libexec/mooncen-container-release target-identity')
  status_json=$(/usr/sbin/runuser --user mooncen_ops_api -- /usr/bin/env -i \
    HOME=/var/lib/mooncen-ops-api LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    /usr/bin/ssh -F /etc/mooncen-an2p/status-transport/ssh_config \
    cloud-container-status \
    '/usr/bin/sudo -n -- /usr/local/libexec/mooncen-container-release status')
  /usr/sbin/runuser --user "$worker_user" -- /usr/bin/env -i \
    HOME=/var/lib/mooncen-deployment-worker LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    /usr/bin/ssh -F /etc/mooncen-an2p/deploy-transport/ssh_config \
    cloud-container-deploy \
    '/usr/bin/test -e /usr/local/libexec/mooncen-container-release' >/dev/null
  deploy_json=$(/usr/sbin/runuser --user "$worker_user" -- /usr/bin/env -i \
    HOME=/var/lib/mooncen-deployment-worker LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    /usr/bin/ssh -F /etc/mooncen-an2p/deploy-transport/ssh_config \
    cloud-container-deploy \
    '/usr/bin/sudo -n -- /usr/local/libexec/mooncen-container-release status')
  /usr/bin/python3.12 -I - "$pending_identity" \
    "$status_identity_json" "$deploy_identity_json" \
    "$status_json" "$deploy_json" <<'PY'
import json
import sys


pending_identity, status_identity_text, deploy_identity_text, status_text, deploy_text = (
    sys.argv[1:]
)


def canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


identity_keys = {"schema_version", "target", "target_identity"}
for label, identity_text in (
    ("status", status_identity_text),
    ("deploy", deploy_identity_text),
):
    if len(identity_text.encode("utf-8")) > 4_096:
        raise SystemExit(f"{label} target identity envelope is oversized")
    identity = json.loads(identity_text)
    if (
        not isinstance(identity, dict)
        or set(identity) != identity_keys
        or identity.get("schema_version") != 1
        or identity.get("target") != "an2p-dev"
        or identity.get("target_identity") != pending_identity
        or identity_text != canonical(identity)
    ):
        raise SystemExit(f"{label} target identity does not match phase 1")

if len(status_text.encode("utf-8")) > 1024 * 1024 or status_text != deploy_text:
    raise SystemExit("split transport status views differ")
value = json.loads(status_text)
if (
    not isinstance(value, dict)
    or set(value)
    != {"native_intent", "schema_version", "state", "transaction", "worker_lease"}
    or value.get("schema_version") != 1
    or status_text != canonical(value)
):
    raise SystemExit("transport status is not canonical controller state")
PY
  verify_control_transport_negative_boundaries
  expect_exact_transport_denial 64 "status endpoint mutation" \
    /usr/sbin/runuser --user mooncen_ops_api -- /usr/bin/env -i \
    HOME=/var/lib/mooncen-ops-api LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    /usr/bin/ssh -F /etc/mooncen-an2p/status-transport/ssh_config \
    cloud-container-status \
    '/usr/bin/sudo -n -- /usr/local/libexec/mooncen-container-release lease-bind 00000000000000000000000000000000 00000000000000000001 00000000000000000000000000000000'
  expect_exact_transport_denial 64 "deploy endpoint shell" \
    /usr/sbin/runuser --user "$worker_user" -- /usr/bin/env -i \
    HOME=/var/lib/mooncen-deployment-worker LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    /usr/bin/ssh -F /etc/mooncen-an2p/deploy-transport/ssh_config \
    cloud-container-deploy id
  expect_exact_transport_denial 255 "DB endpoint remote command" \
    /usr/sbin/runuser --user "$tunnel_user" -- /usr/bin/env -i \
    HOME=/var/lib/mooncen-ops-db-tunnel LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    /usr/bin/ssh -F /etc/mooncen-an2p/db-tunnel/ssh_config \
    cloud-ops-db true
  /usr/bin/python3.12 -I - <<'PY'
import socket


with socket.create_connection(("127.0.0.1", 15432), timeout=5):
    pass
PY
}

publish_control_finalization() {
  local pair=$1 source_tree=$2 registration=$3
  /usr/bin/python3.12 -I - \
    "$state_root/pending-control-finalization.json" \
    "$state_root/control-finalizations/$pair.json" \
    "$pair" "$source_tree" "$registration" <<'PY'
import json
import os
import pathlib
import re
import stat
import sys


pending_path = pathlib.Path(sys.argv[1])
destination = pathlib.Path(sys.argv[2])
pair, source_tree, registration_text = sys.argv[3:]
pending_payload = pending_path.read_bytes()
pending = json.loads(pending_payload.decode("ascii"))
registration = json.loads(registration_text)
expected_registration_keys = {
    "expires_at",
    "receipt_digest",
    "receipt_id",
    "release_digest",
    "release_id",
    "schema_version",
    "source_tree",
    "status",
    "target",
    "target_identity",
}
uuid = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
timestamp = re.compile(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
if (
    set(registration) != expected_registration_keys
    or registration.get("schema_version") != 1
    or registration.get("source_tree") != source_tree
    or registration.get("status") != "passed"
    or registration.get("target") != "an2p-dev"
    or registration.get("receipt_digest") != pending.get("receipt_digest")
    or registration.get("release_digest") != pending.get("release_digest")
    or registration.get("target_identity") != pending.get("target_identity")
    or uuid.fullmatch(str(registration.get("receipt_id", ""))) is None
    or uuid.fullmatch(str(registration.get("release_id", ""))) is None
    or timestamp.fullmatch(str(registration.get("expires_at", ""))) is None
):
    raise SystemExit("registration result does not match pending development evidence")
value = {
    "environment": "development",
    "environment_sha256": pending["environment_sha256"],
    "expires_at": registration["expires_at"],
    "pair": pair,
    "receipt_digest": registration["receipt_digest"],
    "receipt_id": registration["receipt_id"],
    "release_digest": registration["release_digest"],
    "release_id": registration["release_id"],
    "schema_version": 1,
    "source_tree": source_tree,
    "target": "an2p-dev",
    "target_identity": registration["target_identity"],
}
payload = (
    json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    + b"\n"
)
destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
parent_metadata = destination.parent.lstat()
if (
    destination.parent.is_symlink()
    or not stat.S_ISDIR(parent_metadata.st_mode)
    or parent_metadata.st_uid != 0
    or parent_metadata.st_gid != 0
    or stat.S_IMODE(parent_metadata.st_mode) != 0o700
    or destination.exists()
    or destination.is_symlink()
):
    raise SystemExit("control finalization publication path is unsafe")
stage = destination.parent / f".{destination.name}.{os.getpid()}.tmp"
descriptor = os.open(
    stage,
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
    0o600,
)
try:
    os.fchown(descriptor, 0, 0)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "wb", closefd=True) as stream:
        descriptor = -1
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
finally:
    if descriptor >= 0:
        os.close(descriptor)
os.replace(stage, destination)
directory = os.open(
    destination.parent,
    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
)
try:
    os.fsync(directory)
finally:
    os.close(directory)
PY
}

control_finalize_transaction() {
  local action=$1 pair=$2 source_tree=$3 phase=${4:-started} \
    registration_sha=${5:-$(printf '0%.0s' {1..64})}
  /usr/bin/python3.12 -I - "$control_finalize_journal" \
    "$state_root/pending-control-finalization.json" "$action" "$pair" \
    "$source_tree" "$phase" "$registration_sha" <<'PY'
import hashlib
import json
import os
import pathlib
import re
import stat
import sys


journal, pending = map(pathlib.Path, sys.argv[1:3])
action, pair, source_tree, phase, registration_sha = sys.argv[3:]
phase_order = ("started", "tunnel", "handoff", "registered", "authorized", "isolated")
phases = set(phase_order)
hex64 = re.compile(r"[0-9a-f]{64}")


def canonical(value: dict[str, object]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        + b"\n"
    )


def load_journal() -> tuple[dict[str, object], bytes]:
    metadata = journal.lstat()
    payload = journal.read_bytes()
    if (
        journal.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise SystemExit("control finalization transaction metadata is unsafe")
    value = json.loads(payload.decode("ascii"))
    if (
        set(value)
        != {
            "pair",
            "pending_sha256",
            "phase",
            "registration_sha256",
            "schema_version",
            "source_tree",
        }
        or value.get("schema_version") != 1
        or value.get("pair") != pair
        or value.get("source_tree") != source_tree
        or value.get("phase") not in phases
        or hex64.fullmatch(str(value.get("pending_sha256", ""))) is None
        or hex64.fullmatch(str(value.get("registration_sha256", ""))) is None
        or payload != canonical(value)
    ):
        raise SystemExit("control finalization transaction is invalid")
    return value, payload


if action == "inspect":
    _existing, payload = load_journal()
    sys.stdout.buffer.write(payload)
    raise SystemExit(0)
if action == "remove":
    existing, _payload = load_journal()
    if existing["phase"] != phase:
        raise SystemExit("control finalization transaction is not complete")
    journal.unlink()
    directory = os.open(journal.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    raise SystemExit(0)
if action == "resume-update":
    existing, _payload = load_journal()
    if (
        existing["phase"] not in {"authorized", "isolated"}
        or phase != "isolated"
        or registration_sha != existing["registration_sha256"]
        or registration_sha == "0" * 64
    ):
        raise SystemExit("committed control finalization transaction cannot resume")
    value = {**existing, "phase": "isolated"}
elif action not in {"ensure", "update"} or phase not in phases:
    raise SystemExit("control finalization transaction action is invalid")
else:
    pending_metadata = pending.lstat()
    pending_payload = pending.read_bytes()
    if (
        pending.is_symlink()
        or not stat.S_ISREG(pending_metadata.st_mode)
        or pending_metadata.st_uid != 0
        or pending_metadata.st_gid != 0
        or stat.S_IMODE(pending_metadata.st_mode) != 0o600
    ):
        raise SystemExit("pending control finalization metadata is unsafe")
    pending_sha = hashlib.sha256(pending_payload).hexdigest()
    existing = None
    if journal.exists() or journal.is_symlink():
        existing, _payload = load_journal()
        if existing["pending_sha256"] != pending_sha:
            raise SystemExit("control finalization transaction changed pending evidence")
    elif action == "update":
        raise SystemExit("control finalization transaction is unavailable")
    if action == "ensure" and existing is not None:
        raise SystemExit(0)
    if (
        action == "update"
        and phase != "started"
        and existing is not None
        and phase_order.index(phase) < phase_order.index(str(existing["phase"]))
    ):
        raise SystemExit("control finalization transaction phase regressed")
    if (phase in {"registered", "authorized", "isolated"}) != (
        registration_sha != "0" * 64
    ):
        raise SystemExit("control finalization registration digest is invalid")
    value = {
        "pair": pair,
        "pending_sha256": pending_sha,
        "phase": "started" if action == "ensure" else phase,
        "registration_sha256": "0" * 64 if action == "ensure" else registration_sha,
        "schema_version": 1,
        "source_tree": source_tree,
    }
payload = canonical(value)
stage = journal.parent / f".{journal.name}.{os.getpid()}.tmp"
descriptor = os.open(
    stage,
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
    0o600,
)
try:
    os.fchown(descriptor, 0, 0)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "wb", closefd=True) as stream:
        descriptor = -1
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
finally:
    if descriptor >= 0:
        os.close(descriptor)
os.replace(stage, journal)
directory = os.open(journal.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    os.fsync(directory)
finally:
    os.close(directory)
PY
}

quiesce_control_consumers() {
  local pair=$1 unit global_mask
  local -a system_units=(
    mooncen-ops-status-agent.service
    mooncen-deployment-worker.service
    mooncen-ops-api.service
    mooncen-ops-db-tunnel.service
  )
  verify_legacy_user_unit_masks
  systemctl mask --runtime mooncen-ops-api.service
  api_runtime_masked=true
  systemctl disable --now "${system_units[@]}" >/dev/null 2>&1 || true
  systemctl reset-failed "${system_units[@]}" >/dev/null 2>&1 || true
  for unit in "${system_units[@]}"; do
    ! systemctl is-active --quiet "$unit" && ! systemctl is-enabled --quiet "$unit" || \
      die "production consumer did not quiesce: $unit"
  done
  systemctl --user --machine=sgm@ disable --now \
    "${legacy_user_control_units[@]}" >/dev/null 2>&1 || true
  systemctl --global mask "${legacy_user_control_units[@]}" >/dev/null 2>&1 || true
  verify_legacy_user_unit_masks
  for unit in "${legacy_user_control_units[@]}"; do
    global_mask=/etc/systemd/user/$unit
    [ -L "$global_mask" ] && [ "$(readlink "$global_mask")" = /dev/null ] || \
      die "global legacy control mask did not converge: $unit"
    ! systemctl --user --machine=sgm@ is-active --quiet "$unit" && \
      ! systemctl --user --machine=sgm@ is-enabled --quiet "$unit" || \
      die "legacy user control service did not quiesce: $unit"
  done
  verify_legacy_user_unit_masks
}

verify_completed_control_plane() {
  local pair=$1 finalized_status unit api_pid api_restarts status_pid \
    global_mask worker_pid worker_uid worker_gid bootstrap_name installed_name
  local -a control_units=(
    mooncen-ops-db-tunnel.service
    mooncen-ops-api.service
    mooncen-deployment-worker.service
    mooncen-ops-status-agent.service
  )
  local -a boundary_units=(
    mooncen-an2p-runtime-recovery.service
    mooncen-ops-api.socket
    mooncen-ops-api-ipv6.socket
    mooncen-ops-api-ipv6.service
  )
  verify_legacy_user_unit_masks
  verify_active_development_pair "$pair" completed
  finalized_status=$("$manager" control-finalized "$pair")
  /usr/bin/python3.12 -I -c \
    'import json,sys; value=json.load(sys.stdin); assert value.get("finalized") is True and value.get("pair") == sys.argv[1]' \
    "$pair" <<<"$finalized_status" || die "completed control finalization is invalid"
  for bootstrap_name in ops-api.env deployment-worker.env; do
    cmp -s -- "$bootstrap/$bootstrap_name" "/etc/mooncen-an2p/$bootstrap_name" || \
      die "staged control environment is not applied; use the trusted apply-ops-rotation action for a finalized pair"
  done
  for bootstrap_name in \
    deploy-ssh_config:deploy-transport/ssh_config \
    deploy-id_ed25519:deploy-transport/id_ed25519 \
    deploy-known_hosts:deploy-transport/known_hosts \
    status-ssh_config:status-transport/ssh_config \
    status-id_ed25519:status-transport/id_ed25519 \
    status-known_hosts:status-transport/known_hosts \
    db-ssh_config:db-tunnel/ssh_config \
    db-id_ed25519:db-tunnel/id_ed25519 \
    db-known_hosts:db-tunnel/known_hosts; do
    installed_name=${bootstrap_name#*:}
    cmp -s -- "$bootstrap/${bootstrap_name%%:*}" \
      "/etc/mooncen-an2p/$installed_name" || \
      die "completed control transport differs from its exact bootstrap input"
  done
  for unit in "${control_units[@]}" "${boundary_units[@]}"; do
    systemctl is-enabled --quiet "$unit" && systemctl is-active --quiet "$unit" || \
      die "completed control service is not persistently active: $unit"
  done
  api_pid=$(systemctl show --property MainPID --value mooncen-ops-api.service)
  api_restarts=$(systemctl show --property NRestarts --value mooncen-ops-api.service)
  status_pid=$(systemctl show --property MainPID --value mooncen-ops-status-agent.service)
  worker_pid=$(systemctl show --property MainPID --value mooncen-deployment-worker.service)
  [[ "$api_pid" =~ ^[1-9][0-9]*$ ]] && [[ "$status_pid" =~ ^[1-9][0-9]*$ ]] && \
    [[ "$worker_pid" =~ ^[1-9][0-9]*$ ]] && [[ "$api_restarts" =~ ^[0-9]+$ ]] || \
    die "completed control process identity is invalid"
  [ "$(stat -c '%U' "/proc/$api_pid")" = mooncen_ops_api ] && \
    [ "$(stat -c '%U' "/proc/$status_pid")" = mooncen_ops_api ] && \
    [ "$(stat -c '%U' "/proc/$worker_pid")" = mooncen_deployment_worker ] || \
    die "completed control process account is invalid"

  /usr/bin/python3.12 -I \
    "$pair_releases/$pair/control/tools/wait_for_an2p_http.py" \
    http://127.0.0.1:5175/health --timeout 30 >/dev/null
  /usr/bin/python3.12 -I - <<'PY'
import socket


with socket.create_connection(("::1", 5175), timeout=5) as connection:
    connection.sendall(b"GET / HTTP/1.1\r\nHost: localhost:5175\r\nConnection: close\r\n\r\n")
    response = connection.recv(4096)
if not response.startswith(b"HTTP/1.1 308 ") or b"Location: http://127.0.0.1:5175/\r\n" not in response:
    raise SystemExit("IPv6 Ops redirect did not converge")
PY
  worker_uid=$(id -u "$worker_user")
  worker_gid=$(id -g "$worker_user")
  /usr/bin/python3.12 -I - \
    /var/lib/mooncen-deployment-worker/state/heartbeat.json \
    "$worker_pid" "$worker_uid" "$worker_gid" <<'PY'
import json
import os
import pathlib
import stat
import sys
import time


path = pathlib.Path(sys.argv[1])
expected_pid, expected_uid, expected_gid = map(int, sys.argv[2:])


def load() -> float:
    metadata = path.lstat()
    payload = path.read_bytes()
    value = json.loads(payload.decode("ascii"))
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != expected_uid
        or metadata.st_gid != expected_gid
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or set(value) != {"pid", "updated_at"}
        or value.get("pid") != expected_pid
        or isinstance(value.get("updated_at"), bool)
        or not isinstance(value.get("updated_at"), (int, float))
        or not 0 <= time.time() - float(value["updated_at"]) <= 30
        or not pathlib.Path(f"/proc/{expected_pid}").is_dir()
    ):
        raise ValueError
    return float(value["updated_at"])


deadline = time.monotonic() + 30
first = None
while time.monotonic() < deadline:
    try:
        observed = load()
        if first is None:
            first = observed
        elif observed > first:
            raise SystemExit(0)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        pass
    time.sleep(1)
raise SystemExit("deployment worker authoritative heartbeat did not advance")
PY
  /usr/sbin/runuser --user mooncen_ops_api -- /usr/bin/python3.12 -I - \
    /etc/mooncen-an2p/ops-api.env /opt/mooncen-an2p-control/current \
    >/dev/null <<'PY'
import os
import pathlib
import re
import sys


environment_path, control = map(pathlib.Path, sys.argv[1:])
payload = environment_path.read_bytes()
if (
    not payload.endswith(b"\n")
    or b"\x00" in payload
    or b"\r" in payload
    or len(payload) > 64 * 1024
):
    raise SystemExit("Ops API environment encoding is invalid")
lines = payload.decode("utf-8").splitlines()
if not lines or lines[0] != "# Generated locally by prepare_an2p_ops_control.py; never commit.":
    raise SystemExit("Ops API environment provenance is invalid")
values = {}
for line in lines[1:]:
    name, separator, value = line.partition("=")
    if (
        not separator
        or re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", name) is None
        or not value
        or name in values
    ):
        raise SystemExit("Ops API environment entry is invalid")
    values[name] = value
expected = {
    "AUTH_SECRET",
    "DB_API_PASSWORD",
    "DB_API_USER",
    "DB_CONNECT_TIMEOUT",
    "DB_HOST",
    "DB_LOCK_TIMEOUT_MS",
    "DB_NAME",
    "DB_OWNER_USER",
    "DB_PORT",
    "DB_SSLMODE",
    "DB_STATEMENT_TIMEOUT_MS",
    "ENVIRONMENT",
    "LOG_LEVEL",
    "MOONCEN_API_PROFILE",
    "MOONCEN_AUTH_COOKIE_PREFIX",
    "MOONCEN_AUTH_COOKIE_SECURE",
    "MOONCEN_LOCAL_LOOPBACK_OPS_HTTP",
    "MOONCEN_OPS_LOGIN_ID",
    "MOONCEN_OPS_PASSWORD_HASH",
    "MOONCEN_OPS_SINGLE_ACCOUNT_ONLY",
    "MOONCEN_TRUSTED_HOSTS",
    "OPS_CONTAINER_DEV_TARGET_IDENTITY",
    "OPS_CRAWLER_API_DB_REQUIRED",
    "OPS_DEPLOY_REQUIRED_AGENT_HOSTNAME",
    "OPS_LOCAL_CRAWLER_RUNTIME_ENABLED",
}
if set(values) != expected:
    raise SystemExit("Ops API environment field set is invalid")
python = control / ".venv/bin/python"
os.chdir(control)
os.execve(
    python,
    (str(python), "-m", "ops_agent.status_agent", "--once"),
    {
        "HOME": "/var/lib/mooncen-ops-api",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        **values,
    },
)
PY
  [ "$(systemctl show --property MainPID --value mooncen-ops-api.service)" = "$api_pid" ] && \
    [ "$(systemctl show --property NRestarts --value mooncen-ops-api.service)" = "$api_restarts" ] && \
    [ "$(systemctl show --property MainPID --value mooncen-ops-status-agent.service)" = "$status_pid" ] && \
    [ "$(systemctl show --property MainPID --value mooncen-deployment-worker.service)" = "$worker_pid" ] || \
    die "completed control process restarted during readiness proof"
  if /usr/bin/ss -H -ltn 'sport = :8002' | grep -q .; then
    die "retired Ops API port 8002 is still listening"
  fi
  for unit in "${legacy_user_control_units[@]}"; do
    global_mask=/etc/systemd/user/$unit
    [ -L "$global_mask" ] && [ "$(readlink "$global_mask")" = /dev/null ] || \
      die "completed legacy user control mask is unsafe: $unit"
    ! systemctl --user --machine=sgm@ is-active --quiet "$unit" && \
      ! systemctl --user --machine=sgm@ is-enabled --quiet "$unit" || \
      die "completed legacy user control service is live: $unit"
  done
  verify_legacy_user_unit_masks
}

finalize_control() {
  local pair=$1 source_tree status registration registration_sha transaction_json \
    transaction_phase receipt_status finalization_path pending_path \
    finalization_published=false authorization_committed=false \
    api_runtime_masked=false finalized=false
  [[ "$pair" =~ ^runtime-pair\.([0-9a-f]{40})\.([0-9a-f]{40})\.([0-9a-f]{64})$ ]] || \
    die "finalization pair name is invalid"
  source_tree=${BASH_REMATCH[2]}
  pending_path=$state_root/pending-control-finalization.json
  finalization_path=$state_root/control-finalizations/$pair.json

  verify_finalization_receipt() {
    receipt_status=$("$manager" control-finalization-receipt "$pair")
    /usr/bin/python3.12 -I -c \
      'import json,sys; value=json.load(sys.stdin); assert value == {"pair":sys.argv[1],"receipt_valid":True,"schema_version":1}' \
      "$pair" <<<"$receipt_status" || die "control finalization receipt is invalid"
  }

  cleanup_control_finalization() {
    status=$?
    trap - EXIT INT TERM
    if [ "$status" -ne 0 ] && [ "$finalized" = false ]; then
      systemctl disable --now mooncen-ops-status-agent.service \
        mooncen-deployment-worker.service mooncen-ops-api.service \
        mooncen-ops-db-tunnel.service >/dev/null 2>&1 || true
      systemctl reset-failed mooncen-ops-status-agent.service \
        mooncen-deployment-worker.service mooncen-ops-api.service \
        mooncen-ops-db-tunnel.service >/dev/null 2>&1 || true
      systemctl --user --machine=sgm@ disable --now \
        mooncen-status-agent.service >/dev/null 2>&1 || true
      systemctl --global mask mooncen-status-agent.service >/dev/null 2>&1 || true
      if [ "$api_runtime_masked" = true ]; then
        systemctl unmask --runtime mooncen-ops-api.service >/dev/null 2>&1 || true
        systemctl daemon-reload >/dev/null 2>&1 || true
      fi
      if [ "$finalization_published" = true ] && \
        [ "$authorization_committed" = false ]; then
        rm -f -- "$finalization_path"
        sync -f -- "$state_root/control-finalizations" >/dev/null 2>&1 || true
      fi
    fi
    exit "$status"
  }

  if [ ! -e "$pending_path" ] && [ ! -L "$pending_path" ]; then
    verify_active_development_pair "$pair" completed
    verify_finalization_receipt
    if [ -e "$control_finalize_journal" ] || [ -L "$control_finalize_journal" ]; then
      transaction_json=$(control_finalize_transaction inspect "$pair" "$source_tree")
      read -r transaction_phase registration_sha < <(/usr/bin/python3.12 -I -c \
        'import json,sys; value=json.load(sys.stdin); print(value["phase"], value["registration_sha256"])' \
        <<<"$transaction_json")
      [ "$transaction_phase" = authorized ] || [ "$transaction_phase" = isolated ] || \
        die "committed control finalization transaction phase is invalid"
      [[ "$registration_sha" =~ ^[0-9a-f]{64}$ ]] && \
        [ "$registration_sha" != "$(printf '0%.0s' {1..64})" ] || \
        die "committed control finalization registration digest is invalid"
      authorization_committed=true
      trap cleanup_control_finalization EXIT
      trap 'exit 130' INT
      trap 'exit 143' TERM
      assert_legacy_host_root_revoked
      quiesce_control_consumers "$pair"
      preflight_control_bootstrap "$pair"
      stage_registration_access
      verify_control_transports "$pair"
      /bin/bash \
        "$pair_releases/$pair/control/deploy/an2p/install_isolated_control_plane.sh" \
        --pair "$pair"
      api_runtime_masked=false
      control_finalize_transaction resume-update "$pair" "$source_tree" isolated \
        "$registration_sha"
      verify_completed_control_plane "$pair"
      control_finalize_transaction remove "$pair" "$source_tree" isolated \
        "$registration_sha"
      finalized=true
      trap - EXIT INT TERM
    else
      preflight_control_bootstrap "$pair"
      verify_control_transports "$pair"
      verify_completed_control_plane "$pair"
    fi
    emit_finalization_success "$pair"
    return 0
  fi

  verify_active_development_pair "$pair" residue
  control_finalize_transaction ensure "$pair" "$source_tree"
  trap cleanup_control_finalization EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  assert_legacy_host_root_revoked
  # A killed earlier attempt can leave only exact, root-owned residue. Quiesce
  # and prove every production consumer absent before withdrawing authorization
  # and replaying the idempotent registration. Docker is deliberately untouched.
  quiesce_control_consumers "$pair"
  if [ -e "$finalization_path" ] || [ -L "$finalization_path" ]; then
    verify_finalization_receipt
    rm -- "$finalization_path"
    sync -f -- "$state_root/control-finalizations"
  fi
  control_finalize_transaction update "$pair" "$source_tree" started
  verify_active_development_pair "$pair"
  # This is the first private bootstrap read or production network access.
  preflight_control_bootstrap "$pair"
  stage_registration_access
  verify_control_transports "$pair"
  control_finalize_transaction update "$pair" "$source_tree" tunnel
  "$pair_releases/$pair/control/.venv/bin/python" -I \
    "$pair_releases/$pair/control/deploy/an2p/container_evidence_handoff.py" \
    "$source_tree" >/dev/null
  control_finalize_transaction update "$pair" "$source_tree" handoff
  # This is the first production DB mutation. The registrar is idempotent when
  # a client is killed after commit but before receiving canonical output.
  registration=$(/usr/local/libexec/mooncen-register-container-evidence "$source_tree")
  registration_sha=$(printf '%s\n' "$registration" | sha256sum | cut -d' ' -f1)
  control_finalize_transaction update "$pair" "$source_tree" registered \
    "$registration_sha"
  publish_control_finalization "$pair" "$source_tree" "$registration"
  finalization_published=true
  control_finalize_transaction update "$pair" "$source_tree" authorized \
    "$registration_sha"
  # Pending unlink is the authorization commit point. Set the trap fence first
  # so an unlink-success/fsync-failure never deletes a valid durable receipt.
  authorization_committed=true
  rm -- "$pending_path"
  sync -f -- "$state_root"
  /bin/bash \
    "$pair_releases/$pair/control/deploy/an2p/install_isolated_control_plane.sh" \
    --pair "$pair"
  api_runtime_masked=false
  control_finalize_transaction resume-update "$pair" "$source_tree" isolated \
    "$registration_sha"
  verify_completed_control_plane "$pair"
  control_finalize_transaction remove "$pair" "$source_tree" isolated \
    "$registration_sha"
  finalized=true
  trap - EXIT INT TERM
  emit_finalization_success "$pair"
}

ops_rotation_transaction() {
  local action=$1 pair=$2 old_sha=$3 new_sha=$4
  /usr/bin/python3.12 -I - "$ops_rotation_journal" "$ops_rotation_backup" \
    /etc/mooncen-an2p/ops-api.env "$action" "$pair" "$old_sha" "$new_sha" <<'PY'
import hashlib
import json
import os
import pathlib
import re
import stat
import sys


journal, backup, installed = map(pathlib.Path, sys.argv[1:4])
action, pair, old_sha, new_sha = sys.argv[4:]
hex64 = re.compile(r"[0-9a-f]{64}")


def canonical(value: dict[str, object]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))
        .encode("ascii")
        + b"\n"
    )


def safe_payload(path: pathlib.Path, *, mode: int) -> bytes:
    metadata = path.lstat()
    payload = path.read_bytes()
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != mode
    ):
        raise SystemExit("Ops rotation transaction file is unsafe")
    return payload


def fsync_directory(path: pathlib.Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic(path: pathlib.Path, payload: bytes) -> None:
    stage = path.parent / f".{path.name}.{os.getpid()}.tmp"
    descriptor = os.open(
        stage,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    try:
        os.fchown(descriptor, 0, 0)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    os.replace(stage, path)
    fsync_directory(path.parent)


def load() -> dict[str, object]:
    payload = safe_payload(journal, mode=0o600)
    value = json.loads(payload.decode("ascii"))
    if (
        set(value)
        != {"new_sha256", "old_sha256", "pair", "phase", "schema_version"}
        or value.get("schema_version") != 1
        or value.get("pair") != pair
        or value.get("phase") not in {"prepared", "published"}
        or value.get("old_sha256") == value.get("new_sha256")
        or hex64.fullmatch(str(value.get("old_sha256", ""))) is None
        or hex64.fullmatch(str(value.get("new_sha256", ""))) is None
        or (
            action != "peek"
            and (
                value.get("old_sha256") != old_sha
                or value.get("new_sha256") != new_sha
            )
        )
        or payload != canonical(value)
    ):
        raise SystemExit("Ops rotation transaction is invalid")
    if value["phase"] == "prepared":
        backup_payload = safe_payload(backup, mode=0o600)
        if hashlib.sha256(backup_payload).hexdigest() != value["old_sha256"]:
            raise SystemExit("Ops rotation backup changed")
    return value


if action in {"inspect", "peek"}:
    sys.stdout.buffer.write(canonical(load()))
    raise SystemExit(0)
if action == "ensure":
    if journal.exists() or journal.is_symlink():
        load()
        raise SystemExit(0)
    installed_payload = installed.read_bytes()
    if hashlib.sha256(installed_payload).hexdigest() != old_sha:
        raise SystemExit("installed Ops environment changed before rotation")
    if backup.exists() or backup.is_symlink():
        if hashlib.sha256(safe_payload(backup, mode=0o600)).hexdigest() != old_sha:
            raise SystemExit("orphaned Ops rotation backup is invalid")
    else:
        atomic(backup, installed_payload)
    atomic(
        journal,
        canonical(
            {
                "new_sha256": new_sha,
                "old_sha256": old_sha,
                "pair": pair,
                "phase": "prepared",
                "schema_version": 1,
            }
        ),
    )
    raise SystemExit(0)
if action == "published":
    value = load()
    if hashlib.sha256(installed.read_bytes()).hexdigest() != new_sha:
        raise SystemExit("published Ops environment digest changed")
    value["phase"] = "published"
    atomic(journal, canonical(value))
    raise SystemExit(0)
if action == "remove":
    value = load()
    if value["phase"] != "published" or hashlib.sha256(installed.read_bytes()).hexdigest() != new_sha:
        raise SystemExit("Ops rotation is not complete")
    if backup.exists() or backup.is_symlink():
        safe_payload(backup, mode=0o600)
        backup.unlink()
        fsync_directory(backup.parent)
    journal.unlink()
    fsync_directory(journal.parent)
    raise SystemExit(0)
raise SystemExit("Ops rotation transaction action is invalid")
PY
}

apply_ops_rotation() {
  local pair=$1 receipt_status journal_json journal_phase old_sha new_sha \
    current_sha validation old_reference api_stage api_pid rotation_status \
    rotation_started=false api_runtime_masked=false rotation_complete=false
  [[ "$pair" =~ ^runtime-pair\.[0-9a-f]{40}\.[0-9a-f]{40}\.([0-9a-f]{64})$ ]] || \
    die "Ops rotation pair name is invalid"
  [ "${BASH_REMATCH[1]}" = "${trust[EXPECTED_BUILD_POLICY_SHA256]}" ] || \
    die "Ops rotation pair predates the current reviewed authentication policy"
  [ ! -e "$state_root/pending-control-finalization.json" ] && \
    [ ! -L "$state_root/pending-control-finalization.json" ] && \
    [ ! -e "$control_finalize_journal" ] && [ ! -L "$control_finalize_journal" ] || \
    die "Ops rotation requires a completed control finalization"
  verify_active_development_pair "$pair" completed
  receipt_status=$("$manager" control-finalized "$pair")
  /usr/bin/python3.12 -I -c \
    'import json,sys; value=json.load(sys.stdin); assert value == {"finalized":True,"pair":sys.argv[1],"schema_version":1}' \
    "$pair" <<<"$receipt_status" || die "Ops rotation pair is not finalized"
  preflight_control_bootstrap "$pair"

  [ -f /etc/mooncen-an2p/ops-api.env ] && \
    [ ! -L /etc/mooncen-an2p/ops-api.env ] && \
    [ "$(stat -c '%U:%G:%a' /etc/mooncen-an2p/ops-api.env)" = \
      root:mooncen_ops_api:640 ] || die "installed Ops API environment is unsafe"
  [ -f /etc/mooncen-an2p/deployment-worker.env ] && \
    [ ! -L /etc/mooncen-an2p/deployment-worker.env ] && \
    cmp -s -- "$bootstrap/deployment-worker.env" \
      /etc/mooncen-an2p/deployment-worker.env || \
    die "Ops rotation attempted to change the deployment worker environment"
  for rotation_file in \
    deploy-ssh_config:/etc/mooncen-an2p/deploy-transport/ssh_config \
    deploy-id_ed25519:/etc/mooncen-an2p/deploy-transport/id_ed25519 \
    deploy-known_hosts:/etc/mooncen-an2p/deploy-transport/known_hosts \
    status-ssh_config:/etc/mooncen-an2p/status-transport/ssh_config \
    status-id_ed25519:/etc/mooncen-an2p/status-transport/id_ed25519 \
    status-known_hosts:/etc/mooncen-an2p/status-transport/known_hosts \
    db-ssh_config:/etc/mooncen-an2p/db-tunnel/ssh_config \
    db-id_ed25519:/etc/mooncen-an2p/db-tunnel/id_ed25519 \
    db-known_hosts:/etc/mooncen-an2p/db-tunnel/known_hosts; do
    cmp -s -- "$bootstrap/${rotation_file%%:*}" "${rotation_file#*:}" || \
      die "Ops rotation attempted to change a service transport"
  done
  [ -f "$bootstrap/ops-credentials.txt" ] && \
    [ ! -L "$bootstrap/ops-credentials.txt" ] && \
    [ "$(stat -c '%U:%G:%a' "$bootstrap/ops-credentials.txt")" = root:root:600 ] || \
    die "root-only rotated Ops credential is unavailable"

  if [ -e "$ops_rotation_journal" ] || [ -L "$ops_rotation_journal" ]; then
    journal_json=$(ops_rotation_transaction peek "$pair" \
      "$(printf '0%.0s' {1..64})" "$(printf '0%.0s' {1..64})")
    read -r journal_phase old_sha new_sha < <(/usr/bin/python3.12 -I -c \
      'import json,sys; value=json.load(sys.stdin); print(value["phase"], value["old_sha256"], value["new_sha256"])' \
      <<<"$journal_json")
    if [ -e "$ops_rotation_backup" ] || [ -L "$ops_rotation_backup" ]; then
      old_reference=$ops_rotation_backup
    else
      [ "$journal_phase" = published ] || \
        die "prepared Ops rotation lost its rollback bytes"
      old_reference=$bootstrap/ops-api.env
    fi
  else
    journal_phase=
    old_reference=/etc/mooncen-an2p/ops-api.env
  fi

  validation=$(/usr/bin/python3.12 -I - "$old_reference" \
    /etc/mooncen-an2p/ops-api.env "$bootstrap/ops-api.env" \
    "$bootstrap/ops-credentials.txt" <<'PY'
import hashlib
import hmac
import json
import pathlib
import re
import sys


old_path, current_path, new_path, credential_path = map(pathlib.Path, sys.argv[1:])
header = "# Generated locally by prepare_an2p_ops_control.py; never commit."


def environment(path: pathlib.Path) -> tuple[bytes, dict[str, str]]:
    payload = path.read_bytes()
    if not payload.endswith(b"\n") or b"\x00" in payload or b"\r" in payload:
        raise SystemExit("Ops environment encoding is invalid")
    lines = payload.decode("utf-8").splitlines()
    if not lines or lines[0] != header:
        raise SystemExit("Ops environment provenance is invalid")
    values = {}
    for line in lines[1:]:
        name, separator, value = line.partition("=")
        if not separator or not value or name in values:
            raise SystemExit("Ops environment entry is invalid")
        values[name] = value
    return payload, values


old_payload, old = environment(old_path)
current_payload, _current = environment(current_path)
new_payload, new = environment(new_path)
if set(old) != set(new) or {
    name for name in new if old[name] != new[name]
} - {"MOONCEN_OPS_PASSWORD_HASH"}:
    raise SystemExit("Ops rotation changed a non-password API setting")
encoded = new.get("MOONCEN_OPS_PASSWORD_HASH", "")
match = re.fullmatch(
    r"pbkdf2_sha256\$([0-9]{6,7})\$([A-Za-z0-9_-]{16,128})\$([0-9a-f]{64})",
    encoded,
)
credential_lines = credential_path.read_text(encoding="utf-8").splitlines()
if (
    match is None
    or credential_lines[:3]
    != [
        "MoonCen isolated an2p Ops Console",
        "URL: http://127.0.0.1:5175/",
        "Login ID: opsadmin",
    ]
    or len(credential_lines) != 4
    or not credential_lines[3].startswith("Password: ")
    or new.get("MOONCEN_OPS_LOGIN_ID") != "opsadmin"
):
    raise SystemExit("rotated Ops credential is invalid")
password = credential_lines[3].removeprefix("Password: ")
rounds, salt, expected = match.groups()
actual = hashlib.pbkdf2_hmac(
    "sha256",
    password.encode("utf-8"),
    salt.encode("ascii"),
    int(rounds),
).hex()
if not hmac.compare_digest(actual, expected):
    raise SystemExit("rotated Ops credential does not match its verifier")
print(
    json.dumps(
        {
            "current_sha256": hashlib.sha256(current_payload).hexdigest(),
            "new_sha256": hashlib.sha256(new_payload).hexdigest(),
            "old_sha256": hashlib.sha256(old_payload).hexdigest(),
            "schema_version": 1,
        },
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
)
PY
  )
  if [ -z "$journal_phase" ]; then
    read -r current_sha new_sha old_sha < <(/usr/bin/python3.12 -I -c \
      'import json,sys; value=json.load(sys.stdin); print(value["current_sha256"], value["new_sha256"], value["old_sha256"])' \
      <<<"$validation")
  else
    read -r current_sha validation_new validation_old < <(/usr/bin/python3.12 -I -c \
      'import json,sys; value=json.load(sys.stdin); print(value["current_sha256"], value["new_sha256"], value["old_sha256"])' \
      <<<"$validation")
    [ "$validation_new" = "$new_sha" ] || die "staged Ops rotation changed during retry"
    if [ -e "$ops_rotation_backup" ]; then
      [ "$validation_old" = "$old_sha" ] || die "Ops rotation backup changed during retry"
    fi
  fi

  verify_rotated_api() {
    api_pid=$(systemctl show --property MainPID --value mooncen-ops-api.service)
    [[ "$api_pid" =~ ^[1-9][0-9]*$ ]] || die "rotated Ops API process is unavailable"
    /usr/bin/python3.12 -I - "$api_pid" "$bootstrap/ops-api.env" \
      "$bootstrap/ops-credentials.txt" <<'PY'
import json
import pathlib
import sys
import urllib.error
import urllib.request
import uuid


pid = int(sys.argv[1])
environment_path, credential_path = map(pathlib.Path, sys.argv[2:])
prefix = b"MOONCEN_OPS_PASSWORD_HASH="
matches = [
    line[len(prefix) :]
    for line in environment_path.read_bytes().splitlines()
    if line.startswith(prefix)
]
process_environment = {}
for item in pathlib.Path(f"/proc/{pid}/environ").read_bytes().split(b"\x00"):
    if not item:
        continue
    name, separator, value = item.partition(b"=")
    if not separator or name in process_environment:
        raise SystemExit("rotated Ops API process environment is invalid")
    process_environment[name] = value
if len(matches) != 1 or process_environment.get(prefix[:-1]) != matches[0]:
    raise SystemExit("rotated Ops API did not load the new verifier")
lines = credential_path.read_text(encoding="utf-8").splitlines()
login_url = "http://127.0.0.1:5175/api/auth/ops/login"


class RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        return None


opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    RejectRedirects(),
)
request = urllib.request.Request(
    login_url,
    data=json.dumps(
        {"login_id": lines[2].removeprefix("Login ID: "), "password": lines[3].removeprefix("Password: ")},
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("ascii"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    with opener.open(request, timeout=15) as response:
        body = response.read(1024 * 1024 + 1)
        value = json.loads(body)
        user = value.get("user") if isinstance(value, dict) else None
        if (
            response.status != 200
            or response.geturl() != login_url
            or response.headers.get_content_type() != "application/json"
            or response.headers.get("Cache-Control") != "no-store"
            or len(body) > 1024 * 1024
            or set(value) != {"user"}
            or not isinstance(user, dict)
            or set(user) != {"email", "id", "name", "provider"}
            or user.get("email") != "opsadmin@ops.internal"
            or user.get("name") != lines[2].removeprefix("Login ID: ")
            or user.get("provider") != "ops"
            or str(uuid.UUID(str(user.get("id")))) != user.get("id")
        ):
            raise SystemExit("rotated Ops login was not accepted")
except (OSError, ValueError, TypeError, urllib.error.URLError) as exc:
    raise SystemExit("rotated Ops login was not accepted") from exc
PY
  }

  if [ "$old_sha" = "$new_sha" ] && [ -z "$journal_phase" ]; then
    verify_rotated_api
    verify_completed_control_plane "$pair"
    emit_rotation_success "$pair"
    return 0
  fi
  [[ "$old_sha" =~ ^[0-9a-f]{64}$ ]] && [[ "$new_sha" =~ ^[0-9a-f]{64}$ ]] && \
    [ "$old_sha" != "$new_sha" ] || die "Ops rotation digests are invalid"
  if [ -z "$journal_phase" ]; then
    ops_rotation_transaction ensure "$pair" "$old_sha" "$new_sha"
    journal_phase=prepared
  fi

  cleanup_ops_rotation() {
    rotation_status=$?
    trap - EXIT INT TERM
    if [ "$rotation_status" -ne 0 ] && [ "$rotation_complete" = false ] && \
      [ -f "$ops_rotation_backup" ] && [ ! -L "$ops_rotation_backup" ]; then
      set +e
      systemctl mask --runtime mooncen-ops-api.service >/dev/null 2>&1
      systemctl stop mooncen-ops-api.service >/dev/null 2>&1
      api_stage=$(mktemp /etc/mooncen-an2p/.ops-api.env.rotation-restore.XXXXXXXX)
      install -o root -g mooncen_ops_api -m 0640 "$ops_rotation_backup" "$api_stage"
      sync -f -- "$api_stage"
      mv -fT -- "$api_stage" /etc/mooncen-an2p/ops-api.env
      sync -f -- /etc/mooncen-an2p/ops-api.env
      sync -f -- /etc/mooncen-an2p
      systemctl unmask --runtime mooncen-ops-api.service >/dev/null 2>&1
      systemctl daemon-reload >/dev/null 2>&1
      systemctl restart mooncen-ops-api.service >/dev/null 2>&1
      set -e
    elif [ "$api_runtime_masked" = true ]; then
      systemctl unmask --runtime mooncen-ops-api.service >/dev/null 2>&1 || true
      systemctl daemon-reload >/dev/null 2>&1 || true
    fi
    exit "$rotation_status"
  }
  trap cleanup_ops_rotation EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  current_sha=$(sha256sum /etc/mooncen-an2p/ops-api.env | cut -d' ' -f1)
  if [ "$current_sha" = "$old_sha" ]; then
    systemctl mask --runtime mooncen-ops-api.service
    api_runtime_masked=true
    systemctl stop mooncen-ops-api.service
    api_stage=$(mktemp /etc/mooncen-an2p/.ops-api.env.rotation.XXXXXXXX)
    install -o root -g mooncen_ops_api -m 0640 "$bootstrap/ops-api.env" "$api_stage"
    sync -f -- "$api_stage"
    mv -fT -- "$api_stage" /etc/mooncen-an2p/ops-api.env
    api_stage=
    sync -f -- /etc/mooncen-an2p/ops-api.env
    sync -f -- /etc/mooncen-an2p
  elif [ "$current_sha" != "$new_sha" ]; then
    die "installed Ops environment is neither rotation endpoint"
  fi
  ops_rotation_transaction published "$pair" "$old_sha" "$new_sha"
  systemctl unmask --runtime mooncen-ops-api.service
  api_runtime_masked=false
  systemctl daemon-reload
  systemctl restart mooncen-ops-api.service
  "$pair_releases/$pair/control/.venv/bin/python" \
    "$pair_releases/$pair/control/tools/wait_for_an2p_http.py" \
    http://127.0.0.1:5175/health --timeout 90
  verify_rotated_api
  verify_completed_control_plane "$pair"
  ops_rotation_transaction remove "$pair" "$old_sha" "$new_sha"
  rotation_complete=true
  trap - EXIT INT TERM
  emit_rotation_success "$pair"
}

if [ "${1:-}" = rollback ]; then
  [ "$#" -eq 3 ] && [ "$2" = --pair ] || \
    die "usage: $trusted_entrypoint rollback --pair <runtime-pair>"
  [[ "$3" =~ ^runtime-pair\.[0-9a-f]{40}\.[0-9a-f]{40}\.[0-9a-f]{64}$ ]] || \
    die "rollback pair name is invalid"
  [ -x "$manager" ] && [ ! -L "$manager" ] && \
    [ "$(stat -c '%U:%G:%a' "$manager")" = root:root:755 ] || \
    die "installed runtime manager is unavailable"
  "$manager" validate "$3" >/dev/null
  "$manager" activate-retained "$3"
  exit 0
fi

if [ "${1:-}" = finalize-control ]; then
  [ "$#" -eq 3 ] && [ "$2" = --pair ] || \
    die "usage: $trusted_entrypoint finalize-control --pair <runtime-pair>"
  finalize_control "$3"
  exit 0
fi

if [ "${1:-}" = apply-ops-rotation ]; then
  [ "$#" -eq 3 ] && [ "$2" = --pair ] || \
    die "usage: $trusted_entrypoint apply-ops-rotation --pair <runtime-pair>"
  apply_ops_rotation "$3"
  exit 0
fi

[ "$#" -eq 11 ] && [ "$1" = install ] && [ "$2" = --reference ] && \
  [ "$4" = --commit ] && [ "$6" = --base-commit ] && \
  [ "$8" = --source-tree ] && [ "${10}" = --build-policy ] || \
  die "usage: $trusted_entrypoint install --reference <refs/.../32hex> --commit <40hex> --base-commit <40hex> --source-tree <40hex> --build-policy <64hex>"
reference=$3
commit=$5
base_commit=$7
source_tree=$9
build_policy=${11}
pair_name=runtime-pair.$commit.$source_tree.$build_policy
pair_final=$pair_releases/$pair_name
evidence_target=$evidence_root/$source_tree
[[ "$reference" =~ ^refs/mooncen/docker-release-snapshots/[0-9a-f]{32}$ ]] || \
  die "reviewed snapshot reference is invalid"
for value in "$commit" "$base_commit" "$source_tree"; do
  [[ "$value" =~ ^[0-9a-f]{40}$ ]] || die "reviewed Git identity is invalid"
done
[[ "$build_policy" =~ ^[0-9a-f]{64}$ ]] && \
  [ "$build_policy" = "${trust[EXPECTED_BUILD_POLICY_SHA256]}" ] || \
  die "reviewed build policy does not match the root trust envelope"
# No root-only service credential or transport key may be read or installed
# while an old sgm process still retains docker/lxd host-root capability.
preserve_public_development_while_revoking_host_root
/usr/bin/dpkg-query -W -f='${Status}\n' python3.12-venv 2>/dev/null | \
  grep -Fxq 'install ok installed' || die "run bootstrap-prerequisites first"
[ -d "$source_repository/.git" ] && [ ! -L "$source_repository/.git" ] || \
  die "fixed source repository is unavailable"
[ -d "$bootstrap" ] && [ ! -L "$bootstrap" ] && \
  [ "$(stat -c '%U:%G:%a' "$bootstrap")" = root:root:700 ] || \
  die "root-only bootstrap input directory is unavailable"

ensure_account "$docker_user" /var/lib/mooncen-docker-operator
getent group docker >/dev/null || die "Docker must already be installed"
/usr/sbin/usermod --append --groups docker "$docker_user"
if getent group lxd >/dev/null && id -nG "$docker_user" | tr ' ' '\n' | grep -Fxq lxd; then
  /usr/bin/gpasswd --delete "$docker_user" lxd >/dev/null
fi
docker_gid=$(id -g "$docker_user")
install -d -o "$docker_user" -g "$docker_user" -m 0700 /var/lib/mooncen-docker-operator
install -d -o "$docker_user" -g "$docker_user" -m 0700 "$operator_build_root"

run_operator() {
  /usr/sbin/runuser --user "$docker_user" -- /usr/bin/env -i \
    HOME=/var/lib/mooncen-docker-operator PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    LANG=C.UTF-8 LC_ALL=C.UTF-8 DOCKER_HOST=unix:///var/run/docker.sock \
    GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
    GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory \
    GIT_CONFIG_VALUE_0="${control_stage:-$source_repository}" \
    PYTHONDONTWRITEBYTECODE=1 "$@"
}

verify_docker_prerequisites() {
  local package compose_version
  for package in docker.io docker-compose-v2 docker-buildx; do
    /usr/bin/dpkg-query -W -f='${Status}\n' "$package" 2>/dev/null |
      grep -Fxq 'install ok installed' ||
      die "reviewed Docker package is unavailable: $package"
  done
  [ -f /usr/bin/docker ] && [ ! -L /usr/bin/docker ] &&
    [ "$(stat -c '%U:%G:%a' /usr/bin/docker)" = root:root:755 ] ||
    die "Docker CLI metadata is unsafe"
  [ ! -e /etc/docker/daemon.json ] && [ ! -L /etc/docker/daemon.json ] ||
    die "an2p Docker daemon overrides require separate review"
  [ ! -e /etc/systemd/system/docker.service.d ] &&
    [ ! -L /etc/systemd/system/docker.service.d ] ||
    die "an2p Docker service overrides require separate review"
  systemctl is-enabled --quiet docker.service &&
    systemctl is-active --quiet docker.service ||
    die "reviewed Docker daemon is not enabled and active"
  [ -S /var/run/docker.sock ] && [ ! -L /var/run/docker.sock ] &&
    [ "$(stat -c '%U:%G:%a' /var/run/docker.sock)" = root:docker:660 ] ||
    die "Docker socket metadata is unsafe"
  [ "$(run_operator /usr/bin/docker context show)" = default ] ||
    die "Docker operator is not using the local default context"
  [ "$(run_operator /usr/bin/docker info --format '{{.OSType}}/{{.Architecture}}')" = linux/x86_64 ] ||
    die "Docker daemon is not the reviewed local amd64 target"
  compose_version=$(run_operator /usr/bin/docker compose version --short)
  /usr/bin/python3.12 -I - "$compose_version" <<'PY'
import re, sys
match = re.fullmatch(
    r"v?([0-9]+)\.([0-9]+)\.([0-9]+)(?:[+~-][0-9A-Za-z.+:~-]+)?",
    sys.argv[1],
)
if match is None or tuple(map(int, match.groups())) < (2, 35, 0):
    raise SystemExit(78)
PY
  run_operator /usr/bin/docker buildx version >/dev/null
}

finish_pair_install() {
  # Publication is complete before selection.  Preserve it for an exact retry
  # if local activation or health fails; phase 1 never reads a production
  # credential, starts a production tunnel, or mutates the production DB.
  resume_required=true
  /bin/bash "$pair_final/control/deploy/an2p/install_development_runtime.sh" \
    --prepare --pair "$pair_name"
  # Cleanup owns recovery before the child can publish a journal or switch the
  # pointer. SIGKILL/OOM therefore converges immediately rather than waiting
  # for a reboot of the durable recovery service.
  activation_attempted=true
  activation=$({ "$manager" activate-development "$pair_name"; })
  activated=true
  [ "$(/usr/bin/python3.12 -I -c \
    'import json,sys;print(json.load(sys.stdin)["active_pair"])' \
    <<<"$activation")" = "$pair_name" ] ||
    die "runtime pair activation result is invalid"

  activated=false
  activation_attempted=false
}

verify_docker_prerequisites

token=$(/usr/bin/openssl rand -hex 16)
[[ "$token" =~ ^[0-9a-f]{32}$ ]] || die "staging token generation failed"
install -d -o root -g root -m 0755 "$pair_root" "$pair_releases" \
  "$control_alias_root" "$docker_alias_root"
install -d -o root -g "$docker_user" -m 0750 "$evidence_root"

# One-time migration from the retired split control/docker pointers.  No
# runtime is selected yet, the legacy login session is already terminated,
# and a crash after unlinking leaves every new system service fail-closed.
migrate_legacy_alias() {
  local alias_root=$1 runtime_kind=$2 alias_path target legacy_path
  alias_path=$alias_root/current
  if [ ! -e "$alias_path" ] && [ ! -L "$alias_path" ]; then
    return 0
  fi
  [ -L "$alias_path" ] && [ "$(stat -c '%U:%G' "$alias_path")" = root:root ] ||
    die "legacy runtime compatibility alias is unsafe: $runtime_kind"
  target=$(readlink "$alias_path")
  if [ "$target" = ../mooncen-an2p-runtime/current/$runtime_kind ]; then
    return 0
  fi
  [ ! -e "$pair_root/current" ] && [ ! -L "$pair_root/current" ] ||
    die "legacy runtime alias cannot be migrated after pair activation"
  [[ "$target" =~ ^releases/${runtime_kind}-runtime\.[0-9a-f]{40}\.[0-9a-f]{40}\.[0-9a-f]{64}$ ]] ||
    die "legacy runtime alias target is not reviewed: $runtime_kind"
  legacy_path=$alias_root/$target
  [ -d "$legacy_path" ] && [ ! -L "$legacy_path" ] ||
    die "legacy runtime alias target is unavailable: $runtime_kind"
  rm -- "$alias_path"
  sync -f -- "$alias_root"
}
migrate_legacy_alias "$control_alias_root" control
migrate_legacy_alias "$docker_alias_root" docker

pair_stage="$pair_releases/.stage.$token"
[ ! -e "$pair_stage" ] && [ ! -L "$pair_stage" ] || die "pair staging path already exists"
install -d -o root -g root -m 0755 "$pair_stage"
control_stage=$pair_stage/control
source_bundle=$pair_stage/.reviewed-source.bundle
operator_output=$operator_build_root/$token
install -d -o "$docker_user" -g "$docker_user" -m 0700 "$operator_output"
evidence_stage=
pair_published=false
evidence_published=false
resume_required=false
activated=false
activation_attempted=false
previous_pair=
cleanup() {
  status=$?
  trap - EXIT INT TERM
  if [ "$status" -ne 0 ] && [ "$activation_attempted" = true ] && \
    [ -x "$manager" ] && [ -x "$selector" ]; then
    if ! "$manager" recover >/dev/null 2>&1; then
      printf '%s\n' "runtime activation recovery remains pending" >&2
    fi
    [ ! -e "$state_root/transaction.json" ] && \
      [ ! -L "$state_root/transaction.json" ] || \
      printf '%s\n' "runtime activation journal remains pending" >&2
    recovered_selection=$("$selector" runtime-status 2>/dev/null || true)
    recovered_kind=$(/usr/bin/python3.12 -I -c '
import json,sys
try: value=json.load(sys.stdin)
except Exception: raise SystemExit(78)
docker={"docker_active":True,"docker_enabled":True,"marker":True,"native_active":[],"native_enabled":[],"schema_version":1}
native={"docker_active":False,"docker_enabled":False,"marker":False,"native_active":["mooncen-api.service","mooncen-frontend.service"],"native_enabled":["mooncen-api.service","mooncen-frontend.service"],"schema_version":1}
if value == docker: print("docker")
elif value == native: print("native")
else: raise SystemExit(78)
' <<<"$recovered_selection" 2>/dev/null || true)
    if [ "$recovered_kind" = native ]; then
      "$selector" native-select >/dev/null 2>&1 || \
        printf '%s\n' "native development recovery failed" >&2
    elif [ "$recovered_kind" = docker ]; then
      "$selector" docker-select >/dev/null 2>&1 || \
        printf '%s\n' "Docker development recovery failed" >&2
    else
      printf '%s\n' "development selection recovery is incomplete" >&2
    fi
  fi
  if [ -n "${pair_stage:-}" ] && { [ -e "$pair_stage" ] || [ -L "$pair_stage" ]; }; then
    rm -rf -- "$pair_stage"
  fi
  if [ -n "${operator_output:-}" ] && [ -d "$operator_output" ] && [ ! -L "$operator_output" ]; then
    rm -rf -- "$operator_output"
  fi
  if [ -n "${evidence_stage:-}" ] && [ -d "$evidence_stage" ] && [ ! -L "$evidence_stage" ]; then
    rm -rf -- "$evidence_stage"
  fi
  if [ "$status" -ne 0 ] && [ "$resume_required" = false ]; then
    if [ "$pair_published" = true ] && [ -n "$pair_final" ] &&
       [ -d "$pair_final" ] && [ ! -L "$pair_final" ]; then
      rm -rf -- "$pair_final"
      sync -f -- "$pair_releases"
    fi
    if [ "$evidence_published" = true ] && [ -n "$evidence_target" ] &&
       [ -d "$evidence_target" ] && [ ! -L "$evidence_target" ]; then
      rm -rf -- "$evidence_target"
      sync -f -- "$evidence_root"
    fi
    if [ -f "$publish_journal" ] && [ ! -L "$publish_journal" ]; then
      rm -f -- "$publish_journal"
      sync -f -- "$state_root"
    fi
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [ -e "$publish_journal" ] || [ -L "$publish_journal" ]; then
  [ -f "$publish_journal" ] && [ ! -L "$publish_journal" ] &&
    [ "$(stat -c '%U:%G:%a' "$publish_journal")" = root:root:600 ] ||
    die "runtime publication journal is unsafe"
  /usr/bin/python3.12 -I - "$publish_journal" "$pair_name" "$commit" \
    "$source_tree" "$build_policy" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
payload = path.read_bytes()
value = json.loads(payload.decode("ascii"))
expected = {
    "build_policy_sha256": sys.argv[5],
    "commit": sys.argv[3],
    "pair_name": sys.argv[2],
    "schema_version": 1,
    "source_tree": sys.argv[4],
}
canonical = json.dumps(expected, ensure_ascii=True, allow_nan=False, sort_keys=True,
                       separators=(",", ":")).encode("ascii") + b"\n"
if value != expected or payload != canonical:
    raise SystemExit(78)
PY
  if [ -e "$pair_final" ] || [ -L "$pair_final" ]; then
    [ -d "$pair_final" ] && [ ! -L "$pair_final" ] &&
      [ "$(stat -c '%U:%G:%a' "$pair_final")" = root:root:755 ] ||
      die "published runtime pair is unsafe"
    [ -d "$evidence_target" ] && [ ! -L "$evidence_target" ] &&
      [ "$(stat -c '%U:%G:%a' "$evidence_target")" = "root:${docker_user}:750" ] ||
      die "published development evidence is unsafe"
    [ -x "$manager" ] && [ ! -L "$manager" ] &&
      [ "$(stat -c '%U:%G:%a' "$manager")" = root:root:755 ] &&
      [ "$(sha256sum "$manager" | cut -d' ' -f1)" = "${trust[PAIR_MANAGER_SHA256]}" ] ||
      die "installed runtime manager drifted during resume"
    /usr/bin/python3.12 -I - "$pair_final/.pair-receipt.json" "$pair_name" \
      "$commit" "$source_tree" "$build_policy" <<'PY'
import json, pathlib, sys
value = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="ascii"))
if (value.get("pair_name"), value.get("commit"), value.get("source_tree"),
    value.get("build_policy_sha256")) != tuple(sys.argv[2:6]):
    raise SystemExit(78)
PY
    for evidence_name in compose.production.yaml images.tar release.json validation.json; do
      [ -f "$evidence_target/$evidence_name" ] &&
        [ ! -L "$evidence_target/$evidence_name" ] &&
        [ "$(stat -c '%U:%G:%a' "$evidence_target/$evidence_name")" = "root:${docker_user}:640" ] ||
        die "published evidence is incomplete during resume: $evidence_name"
    done
    expected_evidence_entries=$(printf '%s\n' compose.production.yaml images.tar release.json validation.json)
    actual_evidence_entries=$(find "$evidence_target" -mindepth 1 -maxdepth 1 \
      -printf '%f\n' | LC_ALL=C sort)
    [ "$actual_evidence_entries" = "$expected_evidence_entries" ] ||
      die "published evidence has an unexpected file set"
    "$manager" validate "$pair_name" >/dev/null
    pair_published=true
    evidence_published=true
    resume_required=true
    if [ -L "$pair_root/current" ]; then
      current_target=$(readlink "$pair_root/current")
      [[ "$current_target" =~ ^releases/(runtime-pair\.[0-9a-f]{40}\.[0-9a-f]{40}\.[0-9a-f]{64})$ ]] ||
        die "existing pair pointer is unsafe"
      previous_pair=${BASH_REMATCH[1]}
    fi
    rm -rf -- "$pair_stage" "$operator_output"
    pair_stage=
    operator_output=
    finish_pair_install
    trap - EXIT INT TERM
    rm -f -- "$publish_journal"
    sync -f -- "$state_root"
    printf '%s\n' "{\"active_pair\":\"$pair_name\",\"control_finalized\":false,\"development_healthy\":true,\"schema_version\":1,\"source_tree\":\"$source_tree\"}"
    exit 0
  fi
  # Publication is evidence-first. Pair absence proves that no worker handoff
  # or DB registration could have run, so a crash residue is safe to discard.
  if [ -e "$evidence_target" ] || [ -L "$evidence_target" ]; then
    [ -d "$evidence_target" ] && [ ! -L "$evidence_target" ] &&
      [ "$(stat -c '%U:%G:%a' "$evidence_target")" = "root:${docker_user}:750" ] ||
      die "orphaned development evidence is unsafe"
    rm -rf -- "$evidence_target"
    sync -f -- "$evidence_root"
  fi
  rm -f -- "$publish_journal"
  sync -f -- "$state_root"
elif [ -e "$pair_final" ] || [ -L "$pair_final" ] ||
     [ -e "$evidence_target" ] || [ -L "$evidence_target" ]; then
  die "unowned runtime publication residue requires manual review"
fi

env -i HOME=/root PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_NO_REPLACE_OBJECTS=1 \
  /usr/bin/git -C "$source_repository" -c core.hooksPath=/dev/null \
    -c safe.directory="$source_repository" \
    -c safe.directory="$source_repository/.git" \
    bundle create "$source_bundle" "$reference"
[ -f "$source_bundle" ] && [ ! -L "$source_bundle" ] && \
  [ "$(stat -c '%U:%G:%a' "$source_bundle")" = root:root:600 ] || \
  die "reviewed source bundle metadata is unsafe"
env -i HOME=/root PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_NO_REPLACE_OBJECTS=1 \
  /usr/bin/git -c core.hooksPath=/dev/null init --quiet "$control_stage"
env -i HOME=/root PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_NO_REPLACE_OBJECTS=1 \
  /usr/bin/git -C "$control_stage" -c core.hooksPath=/dev/null fetch --quiet \
    --no-tags "$source_bundle" "+${reference}:${reference}"
rm -- "$source_bundle"
sync -f -- "$pair_stage"
resolved_reference=$(env -i HOME=/root PATH=/usr/bin:/bin GIT_CONFIG_NOSYSTEM=1 \
  GIT_CONFIG_GLOBAL=/dev/null GIT_NO_REPLACE_OBJECTS=1 \
  /usr/bin/git -C "$control_stage" rev-parse --verify "$reference^{commit}")
[ "$resolved_reference" = "$commit" ] || die "reviewed snapshot reference changed"
[ "$(/usr/bin/git -C "$control_stage" rev-parse --verify "$commit^{tree}")" = "$source_tree" ] || \
  die "reviewed source tree changed"
[ "$(/usr/bin/git -C "$control_stage" rev-parse --verify "$commit^")" = "$base_commit" ] || \
  die "reviewed snapshot parent changed"
/usr/bin/git -C "$control_stage" -c core.hooksPath=/dev/null checkout --quiet --detach "$commit"
[ -z "$(/usr/bin/git -C "$control_stage" status --porcelain --untracked-files=all)" ] || \
  die "reviewed clone is not clean"

trusted_clone_file() {
  local relative=$1 expected=$2 path=$control_stage/$1
  [ -f "$path" ] && [ ! -L "$path" ] && \
    [ "$(sha256sum "$path" | cut -d' ' -f1)" = "$expected" ] || \
    die "reviewed control byte does not match the root envelope: $relative"
}
trusted_clone_file deploy/an2p/install_runtime_snapshot.sh "${trust[INSTALLER_SHA256]}"
trusted_clone_file deploy/docker/production_runtime_integrity.py "${trust[INTEGRITY_SHA256]}"
trusted_clone_file deploy/docker/verify_clean_source.py "${trust[CLEAN_SOURCE_SHA256]}"
trusted_clone_file deploy/an2p/runtime_pair_manager.py "${trust[PAIR_MANAGER_SHA256]}"
trusted_clone_file deploy/an2p/container_evidence_handoff.py "${trust[HANDOFF_SHA256]}"
trusted_clone_file deploy/an2p/mooncen_register_container_evidence.py "${trust[REGISTRAR_SHA256]}"
[ "$(/usr/bin/python3.12 -I "$control_stage/deploy/docker/production_runtime_integrity.py" \
  policy-digest --source-root "$control_stage")" = "$build_policy" ] || \
  die "reviewed build policy digest changed"
/usr/bin/python3.12 -I "$control_stage/deploy/docker/verify_clean_source.py" >/dev/null

/usr/bin/python3.12 -m venv --copies "$control_stage/.venv"
[ -x "$control_stage/.venv/bin/python" ] && [ ! -L "$control_stage/.venv/bin/python" ] || \
  die "control venv is not an exact copied Python runtime"
"$control_stage/.venv/bin/python" -m pip install --disable-pip-version-check \
  --require-hashes --no-deps --requirement "$control_stage/requirements.lock" >/dev/null
"$control_stage/.venv/bin/python" -I -m pip check >/dev/null

# The reviewed operator is the only non-root account that can reach docker.sock.
# It builds API/frontend once, then validates those exact image bytes.
chgrp -R "$docker_user" "$control_stage"
find "$control_stage" -xdev -type d -exec chmod 0750 {} +
find "$control_stage" -xdev -type f ! -perm /0111 -exec chmod 0640 {} +
find "$control_stage" -xdev -type f -perm /0111 -exec chmod 0750 {} +
release_output=$operator_output/releases
static_output=$operator_output/ops-static
install -d -o "$docker_user" -g "$docker_user" -m 0700 "$release_output" "$static_output"
run_operator "$control_stage/.venv/bin/python" \
  "$control_stage/deploy/docker/build_release_bundle.py" \
  --source-root "$control_stage" --output-root "$release_output" \
  --base-commit "$base_commit" --source-tree "$source_tree" \
  --snapshot-commit "$commit" --platform linux/amd64
release_dir=$release_output/$source_tree
postgres_tag=mooncen/postgres:dev-release-$source_tree
run_operator /usr/bin/docker build --pull --no-cache --platform linux/amd64 \
  --label "kr.mooncen.source_tree=$source_tree" --tag "$postgres_tag" \
  --file "$control_stage/deploy/docker/postgres.Dockerfile" "$control_stage"
target_identity=$(run_operator /usr/bin/env -u DOCKER_HOST \
  "$control_stage/.venv/bin/python" \
  "$control_stage/deploy/docker/smoke.py" --print-development-target-identity)
[[ "$target_identity" =~ ^[0-9a-f]{64}$ ]] || die "development target identity is invalid"
run_operator /usr/bin/env -u DOCKER_HOST "$control_stage/.venv/bin/python" \
  "$control_stage/deploy/docker/smoke.py" \
  --release-directory "$release_dir" --receipt-output "$release_dir/validation.json" \
  --validation-target an2p-dev --target-identity "$target_identity" \
  --platform linux/amd64
run_operator /usr/bin/docker build --pull --no-cache \
  --file "$control_stage/deploy/an2p/ops_console_static.Dockerfile" \
  --output "type=local,dest=$static_output" "$control_stage"

if find "$static_output" -xdev \( -type l -o \( ! -type d -a ! -type f \) \) \
  -print -quit | grep -q .; then
  die "Ops static builder produced an unsafe file type"
fi
install -d -o root -g root -m 0755 "$control_stage/ops-console-dist"
cp -a -- "$static_output/." "$control_stage/ops-console-dist/"

# Preserve only immutable reviewed control bytes; Git metadata is never part
# of the installed execution root.
rm -rf -- "$control_stage/.git"
chown -R root:root "$control_stage"
find "$control_stage" -xdev -type d -exec chmod 0755 {} +
find "$control_stage" -xdev -type f ! -perm /0111 -exec chmod 0644 {} +
find "$control_stage" -xdev -type f -perm /0111 -exec chmod 0755 {} +
[ ! -e "$control_stage/.git" ] && [ ! -L "$control_stage/.git" ] || \
  die "installed control runtime retained Git metadata"

docker_stage=$pair_stage/docker
install -d -o root -g "$docker_user" -m 0750 "$docker_stage"
docker_policy_paths=(
  .dockerignore .gitattributes compose.yaml deploy/__init__.py
  deploy/an2p/__init__.py deploy/an2p/check_docker_environment.py
  deploy/an2p/install_user_services.sh deploy/an2p/mooncen-api.service
  deploy/an2p/mooncen-development-runtime.target deploy/an2p/mooncen-docker-dev.service
  deploy/an2p/mooncen-frontend.service deploy/an2p/mooncen-status-agent.service
  deploy/an2p/validate_docker_release.py deploy/docker/__init__.py
  deploy/docker/native_baseline.py deploy/docker/postgres.Dockerfile
  deploy/docker/production_runtime_integrity.py deploy/docker/release_manifest.py
  deploy/docker/render_runtime_config.py deploy/docker/smoke.py
  deploy/docker/verify_release_bundle.py tools/wait_for_an2p_database.py
  tools/wait_for_an2p_http.py
)
for relative in "${docker_policy_paths[@]}"; do
  source=$control_stage/$relative
  destination=$docker_stage/$relative
  [ -f "$source" ] && [ ! -L "$source" ] || die "Docker runtime input is unsafe: $relative"
  destination_parent=$(dirname "$destination")
  # Top-level inputs live directly in the protected Docker stage.  Do not use
  # install -d on that existing directory: it would overwrite the required
  # root:$docker_user 0750 ownership and make validation fail.
  if [ "$destination_parent" != "$docker_stage" ]; then
    install -d -o root -g root -m 0755 "$destination_parent"
  fi
  install -o root -g root -m 0644 "$source" "$destination"
done

evidence_target=$evidence_root/$source_tree
evidence_stage=$evidence_root/.stage.$token
[ ! -e "$evidence_target" ] && [ ! -L "$evidence_target" ] &&
  [ ! -e "$evidence_stage" ] && [ ! -L "$evidence_stage" ] ||
  die "development evidence publication path is not new"
install -d -o root -g "$docker_user" -m 0750 "$evidence_stage"
for name in compose.production.yaml images.tar release.json validation.json; do
  [ -f "$release_dir/$name" ] && [ ! -L "$release_dir/$name" ] || \
    die "development evidence is incomplete: $name"
  install -o root -g "$docker_user" -m 0640 "$release_dir/$name" \
    "$evidence_stage/$name"
done
expected_evidence_entries=$(printf '%s\n' compose.production.yaml images.tar release.json validation.json)
actual_evidence_entries=$(find "$evidence_stage" -mindepth 1 -maxdepth 1 \
  -printf '%f\n' | LC_ALL=C sort)
[ "$actual_evidence_entries" = "$expected_evidence_entries" ] ||
  die "staged evidence has an unexpected file set"
sync -f -- "$evidence_stage"

docker_env_source=$bootstrap/docker-development.env
[ -f "$docker_env_source" ] && [ ! -L "$docker_env_source" ] && \
  [ "$(stat -c '%U:%G:%a' "$docker_env_source")" = root:root:600 ] || \
  die "root-only Docker environment is unavailable"
# Reuse the same canonical generator validator at the consumption boundary.
# This rejects a root-staged/commented/permissive env before any runtime bytes
# are derived, while preserving an already valid exact value set.
prepare_development_bootstrap >/dev/null
/usr/bin/python3.12 -I - "$docker_env_source" "$docker_stage/development.env" \
  "$release_dir" "$evidence_target" "$postgres_tag" "$docker_gid" <<'PY'
import json, os, pathlib, re, sys
source, destination, release_dir, evidence = map(pathlib.Path, sys.argv[1:5])
postgres_tag, gid = sys.argv[5], int(sys.argv[6])
allowed = {
    "COMPOSE_PROJECT_NAME", "MOONCEN_API_IMAGE", "MOONCEN_API_PORT",
    "MOONCEN_AUTH_SECRET", "MOONCEN_CORS_ORIGINS", "MOONCEN_DB_API_PASSWORD",
    "MOONCEN_DB_API_USER", "MOONCEN_DB_NAME", "MOONCEN_DB_PASSWORD",
    "MOONCEN_DB_USER", "MOONCEN_DEV_RELEASE_DIR", "MOONCEN_FRONTEND_IMAGE",
    "MOONCEN_GOOGLE_OAUTH_CLIENT_ID", "MOONCEN_GOOGLE_OAUTH_CLIENT_SECRET",
    "MOONCEN_KAKAO_MAPS_JAVASCRIPT_KEY", "MOONCEN_NAVER_OAUTH_CLIENT_ID",
    "MOONCEN_NAVER_OAUTH_CLIENT_SECRET", "MOONCEN_OAUTH_REDIRECT_URI",
    "MOONCEN_POSTGRES_IMAGE", "MOONCEN_RUNTIME_CONFIG_FILE", "MOONCEN_SITE_URL",
    "MOONCEN_WEB_PORT",
}
values = {}
for raw in source.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    key, separator, value = line.partition("=")
    if separator != "=" or key not in allowed or key in values or "\x00" in value:
        raise SystemExit(78)
    values[key] = value
release = json.loads((release_dir / "release.json").read_text(encoding="ascii"))
values.update({
    "COMPOSE_PROJECT_NAME": "mooncen-dev",
    "MOONCEN_API_IMAGE": release["images"]["api"]["tag"],
    "MOONCEN_API_PORT": "8001",
    "MOONCEN_DEV_RELEASE_DIR": str(evidence),
    "MOONCEN_FRONTEND_IMAGE": release["images"]["frontend"]["tag"],
    "MOONCEN_POSTGRES_IMAGE": postgres_tag,
    "MOONCEN_RUNTIME_CONFIG_FILE": "/var/lib/mooncen-docker-operator/runtime-config.js",
    "MOONCEN_WEB_PORT": "5174",
})
required = {"MOONCEN_AUTH_SECRET", "MOONCEN_DB_API_PASSWORD", "MOONCEN_DB_PASSWORD"}
if any(not values.get(key) for key in required):
    raise SystemExit(78)
payload = "".join(f"{key}={values[key]}\n" for key in sorted(values)).encode("utf-8")
descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640)
os.fchmod(descriptor, 0o640)
os.fchown(descriptor, 0, gid)
with os.fdopen(descriptor, "wb") as stream:
    stream.write(payload); stream.flush(); os.fsync(stream.fileno())
PY

validation=$("$control_stage/.venv/bin/python" -I \
  "$docker_stage/deploy/an2p/validate_docker_release.py" --system-runtime \
  --reader-group "$docker_user" --project-root "$docker_stage" \
  --environment-file "$docker_stage/development.env" \
  --runtime-compose-file "$docker_stage/compose.yaml" \
  --write-activation-file "$docker_stage/activation.json" \
  --staging-token "$token" --json)
read -r receipt_digest environment_sha <<<"$(/usr/bin/python3.12 -I -c \
  'import json,re,sys;v=json.load(sys.stdin);x=(v["receipt_digest"],v["environment_sha256"]);assert all(re.fullmatch(r"[0-9a-f]{64}",i) for i in x);print(*x)' \
  <<<"$validation")"
"$control_stage/.venv/bin/python" -I \
  "$control_stage/tools/seal_ops_static.py" "$pair_name" \
  --staging-token "$token" >/dev/null
control_inventory=$(/usr/bin/python3.12 -I \
  "$control_stage/deploy/docker/native_baseline.py" --root "$control_stage")
docker_inventory=$(/usr/bin/python3.12 -I \
  "$docker_stage/deploy/docker/native_baseline.py" --root "$docker_stage")
host_layer_sha=$(/usr/bin/python3.12 -I - "$control_stage" <<'PY'
import hashlib, importlib.util, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
path = root / "deploy/an2p/runtime_pair_manager.py"
spec = importlib.util.spec_from_file_location("pair_manager", path)
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
records = []
for label, relative, _installed, mode in module.HOST_LAYER_FILES:
    payload = (root / relative).read_bytes()
    records.append({"label": label, "mode": f"{mode:04o}", "sha256": hashlib.sha256(payload).hexdigest()})
canonical = json.dumps({"files": records}, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n"
print(hashlib.sha256(canonical).hexdigest())
PY
)
/usr/bin/python3.12 -I - "$pair_stage/.pair-receipt.json" "$pair_name" \
  "$commit" "$source_tree" "$build_policy" "$control_inventory" \
  "$docker_inventory" "$environment_sha" "$host_layer_sha" <<'PY'
import hashlib, json, os, pathlib, sys
path = pathlib.Path(sys.argv[1])
keys = ("pair_name","commit","source_tree","build_policy_sha256","control_inventory_sha256","docker_inventory_sha256","environment_sha256","host_layer_sha256")
value = {"schema_version": 1, **dict(zip(keys, sys.argv[2:], strict=True))}
canonical = lambda item: json.dumps(item,ensure_ascii=True,allow_nan=False,sort_keys=True,separators=(",",":")).encode("ascii")+b"\n"
value["receipt_digest"] = hashlib.sha256(canonical(value)).hexdigest()
descriptor = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
os.fchown(descriptor,0,0)
with os.fdopen(descriptor,"wb") as stream:
    stream.write(canonical(value)); stream.flush(); os.fsync(stream.fileno())
PY
sync -f -- "$pair_stage"

# Host glue is an independently reviewed ABI. Existing retained pairs may be
# switched only when they bind the exact same host-layer digest.
previous_pair=
if [ -L "$pair_root/current" ]; then
  current_target=$(readlink "$pair_root/current")
  [[ "$current_target" =~ ^releases/(runtime-pair\.[0-9a-f]{40}\.[0-9a-f]{40}\.[0-9a-f]{64})$ ]] || \
    die "existing pair pointer is unsafe"
  previous_pair=${BASH_REMATCH[1]}
  previous_host=$(/usr/bin/python3.12 -I -c \
    'import json,sys;print(json.load(open(sys.argv[1],encoding="ascii"))["host_layer_sha256"])' \
    "$pair_releases/$previous_pair/.pair-receipt.json")
  [ "$previous_host" = "$host_layer_sha" ] || \
    die "host runtime ABI changed; perform a separately reviewed host maintenance transition"
fi

install -d -o root -g root -m 0755 /usr/local/libexec /etc/systemd/system
host_sources=(
  deploy/an2p/runtime_pair_manager.py deploy/an2p/mooncen_an2p_service_control.py
  deploy/an2p/mooncen_loopback_redirect.py deploy/an2p/mooncen_register_container_evidence.py
  deploy/an2p/mooncen-an2p-runtime-recovery.service
  deploy/an2p/mooncen-deployment-worker.service deploy/an2p/mooncen-docker-dev.service
  deploy/an2p/mooncen-ops-api-ipv6.service deploy/an2p/mooncen-ops-api-ipv6.socket
  deploy/an2p/mooncen-ops-api.service deploy/an2p/mooncen-ops-api.socket
  deploy/an2p/mooncen-ops-status-agent.service
  deploy/an2p/mooncen-ops-db-tunnel.service
)
host_targets=(
  /usr/local/libexec/mooncen-an2p-runtime-manager
  /usr/local/libexec/mooncen-an2p-service-control
  /usr/local/libexec/mooncen-an2p-loopback-redirect
  /usr/local/libexec/mooncen-register-container-evidence
  /etc/systemd/system/mooncen-an2p-runtime-recovery.service
  /etc/systemd/system/mooncen-deployment-worker.service
  /etc/systemd/system/mooncen-docker-dev.service
  /etc/systemd/system/mooncen-ops-api-ipv6.service
  /etc/systemd/system/mooncen-ops-api-ipv6.socket
  /etc/systemd/system/mooncen-ops-api.service
  /etc/systemd/system/mooncen-ops-api.socket
  /etc/systemd/system/mooncen-ops-status-agent.service
  /etc/systemd/system/mooncen-ops-db-tunnel.service
)
if [ -n "$previous_pair" ]; then
  for index in "${!host_sources[@]}"; do
    mode=644
    [[ "${host_targets[$index]}" == /usr/local/libexec/* ]] && mode=755
    [ -f "${host_targets[$index]}" ] && [ ! -L "${host_targets[$index]}" ] &&
      [ "$(stat -c '%U:%G:%a' "${host_targets[$index]}")" = "root:root:${mode}" ] &&
      [ "$(sha256sum "${host_targets[$index]}" | cut -d' ' -f1)" = \
        "$(sha256sum "$control_stage/${host_sources[$index]}" | cut -d' ' -f1)" ] ||
      die "installed host runtime ABI drifted"
  done
else
  # First rollout may install host glue only while no dependent system unit is
  # active or enabled. A crash during these byte copies therefore stays
  # fail-closed; the same reviewed installer can safely overwrite all bytes.
  for target in "${host_targets[@]}"; do
    [[ "$target" == /etc/systemd/system/* ]] || continue
    unit=${target##*/}
    ! systemctl is-active --quiet "$unit" && ! systemctl is-enabled --quiet "$unit" ||
      die "initial host runtime unit is already live: $unit"
  done
  for index in "${!host_sources[@]}"; do
    mode=0644
    [[ "${host_targets[$index]}" == /usr/local/libexec/* ]] && mode=0755
    install -o root -g root -m "$mode" "$control_stage/${host_sources[$index]}" \
      "${host_targets[$index]}"
  done
  systemctl daemon-reload
fi
/usr/bin/python3.12 -I - "$publish_journal" "$pair_name" "$commit" \
  "$source_tree" "$build_policy" <<'PY'
import json, os, pathlib, sys
path = pathlib.Path(sys.argv[1])
value = {
    "build_policy_sha256": sys.argv[5],
    "commit": sys.argv[3],
    "pair_name": sys.argv[2],
    "schema_version": 1,
    "source_tree": sys.argv[4],
}
payload = json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                     separators=(",", ":")).encode("ascii") + b"\n"
descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
os.fchown(descriptor, 0, 0)
with os.fdopen(descriptor, "wb") as stream:
    stream.write(payload); stream.flush(); os.fsync(stream.fileno())
PY
sync -f -- "$state_root"
mv -- "$evidence_stage" "$evidence_target"
evidence_stage=
evidence_published=true
sync -f -- "$evidence_root"
mv -- "$pair_stage" "$pair_final"
pair_stage=
pair_published=true
sync -f -- "$pair_releases"
"$manager" validate "$pair_name" >/dev/null

finish_pair_install
trap - EXIT INT TERM
rm -rf -- "$operator_output"
rm -f -- "$publish_journal"
sync -f -- "$state_root"
printf '%s\n' "{\"active_pair\":\"$pair_name\",\"control_finalized\":false,\"development_healthy\":true,\"schema_version\":1,\"source_tree\":\"$source_tree\"}"
