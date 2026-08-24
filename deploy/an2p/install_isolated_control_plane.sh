#!/usr/bin/env bash
set -euo pipefail
umask 077

die() {
  printf '%s\n' "an2p isolated control-plane install: $*" >&2
  exit "${2:-78}"
}

[ "$(id -u)" -eq 0 ] || die "run from a root console"
[ "$(hostname -s)" = "an2p" ] || die "unexpected host"

[ "$#" -eq 2 ] && [ "$1" = --pair ] || \
  die "usage: $0 --pair <runtime-pair>" 64
expected_pair=$2
[[ "$expected_pair" =~ ^runtime-pair\.[0-9a-f]{40}\.[0-9a-f]{40}\.[0-9a-f]{64}$ ]] || \
  die "runtime pair name is invalid"

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
bootstrap=/root/mooncen-an2p-bootstrap
control_runtime=/opt/mooncen-an2p-control/current
docker_runtime=/opt/mooncen-an2p-docker/current
operation_lock=/var/lib/mooncen-an2p-runtime/operation.lock
operation_journal=/var/lib/mooncen-an2p-runtime/transaction.json
legacy_user=sgm
api_user=mooncen_ops_api
worker_user=mooncen_deployment_worker
tunnel_user=mooncen_ops_db_tunnel
docker_user=mooncen_docker_operator

# Reuse the byte-identical dirfd helper embedded in the already validated
# phase-one script from this immutable pair.  The loader itself opens the
# regular root-owned source without following its leaf and extracts one exact
# heredoc, so finalization cannot fall back to pathname-based user mutations.
safe_user_path_helper=$script_dir/install_development_runtime.sh
safe_legacy_user_paths() {
  /usr/bin/python3.12 -I -c '
import os
import stat
import sys

path = sys.argv[1]
descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
try:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or metadata.st_size <= 0
        or metadata.st_size > 1024 * 1024
    ):
        raise SystemExit("safe user path helper source is unsafe")
    payload = b""
    while len(payload) <= 1024 * 1024:
        chunk = os.read(descriptor, 65536)
        if not chunk:
            break
        payload += chunk
finally:
    os.close(descriptor)
start = b"  /usr/bin/python3.12 -I - \"$@\" <<\x27PY\x27\n"
end = b"\nPY\n}\n"
if payload.count(start) != 1:
    raise SystemExit("safe user path helper start marker is not unique")
program_and_tail = payload.split(start, 1)[1]
if program_and_tail.count(end) < 1:
    raise SystemExit("safe user path helper end marker is missing")
program = program_and_tail.split(end, 1)[0]
try:
    source = program.decode("utf-8")
except UnicodeError as exc:
    raise SystemExit("safe user path helper encoding is invalid") from exc
sys.argv = [path, *sys.argv[2:]]
exec(compile(source, path, "exec"), {"__name__": "__main__", "__file__": path})
' "$safe_user_path_helper" "$@"
}

[ -d "$bootstrap" ] && [ ! -L "$bootstrap" ] && \
  [ "$(stat -c '%U:%G:%a' "$bootstrap")" = "root:root:700" ] || \
  die "stage fixed bootstrap inputs in root:root mode 0700 $bootstrap"

ensure_system_account() {
  local account=$1 home=$2 record
  if ! record=$(getent passwd "$account"); then
    /usr/sbin/useradd --system --user-group --home-dir "$home" --create-home \
      --shell /usr/sbin/nologin "$account"
    record=$(getent passwd "$account")
  fi
  [ "$(printf '%s' "$record" | cut -d: -f6)" = "$home" ] && \
    [ "$(printf '%s' "$record" | cut -d: -f7)" = /usr/sbin/nologin ] && \
    [ "$(id -gn "$account")" = "$account" ] || \
    die "dedicated account metadata drifted: $account"
  install -d -o "$account" -g "$account" -m 0700 "$home"
  /usr/sbin/usermod --password '*' "$account"
}

ensure_system_account "$api_user" /var/lib/mooncen-ops-api
ensure_system_account "$worker_user" /var/lib/mooncen-deployment-worker
ensure_system_account "$tunnel_user" /var/lib/mooncen-ops-db-tunnel
ensure_system_account "$docker_user" /var/lib/mooncen-docker-operator

getent group docker >/dev/null || die "Docker must already provide the docker group"
/usr/sbin/usermod --append --groups docker "$docker_user"
for account in "$legacy_user" "$api_user" "$worker_user" "$tunnel_user"; do
  for privileged_group in docker lxd; do
    if getent group "$privileged_group" >/dev/null && \
      id -nG "$account" | tr ' ' '\n' | grep -Fxq "$privileged_group"; then
      /usr/bin/gpasswd --delete "$account" "$privileged_group" >/dev/null
    fi
  done
done
if getent group lxd >/dev/null && \
  id -nG "$docker_user" | tr ' ' '\n' | grep -Fxq lxd; then
  /usr/bin/gpasswd --delete "$docker_user" lxd >/dev/null
fi

# Old login processes retain supplementary groups after /etc/group changes.
# Terminate and prove that boundary before installing a single service secret.
legacy_uid=$(id -u "$legacy_user")
legacy_gid=$(id -g "$legacy_user")
legacy_home=$(getent passwd "$legacy_user" | cut -d: -f6)
[ "$legacy_home" = "/home/$legacy_user" ] || \
  die "legacy user home is not canonical"
for privileged_group in docker lxd; do
  getent group "$privileged_group" >/dev/null || continue
  privileged_gid=$(getent group "$privileged_group" | cut -d: -f3)
  for status in /proc/[0-9]*/status; do
    [ -r "$status" ] || continue
    process_uid=$(awk '/^Uid:/ {print $2}' "$status")
    [ "$process_uid" = "$legacy_uid" ] || continue
    if awk -v gid="$privileged_gid" '/^Groups:/ {for (i=2;i<=NF;i++) if ($i==gid) found=1} END {exit !found}' \
      "$status"; then
      die "legacy user session still retains ${privileged_group} host-root" 75
    fi
  done
done

pair_pointer=/opt/mooncen-an2p-runtime/current
[ -L "$pair_pointer" ] && [ "$(stat -c '%U:%G' "$pair_pointer")" = root:root ] || \
  die "immutable runtime pair pointer is missing"
pair_relative=$(readlink "$pair_pointer")
[[ "$pair_relative" =~ ^releases/(runtime-pair\.[0-9a-f]{40}\.[0-9a-f]{40}\.[0-9a-f]{64})$ ]] || \
  die "immutable runtime pair pointer is unsafe"
pair_name=${BASH_REMATCH[1]}
[ "$pair_name" = "$expected_pair" ] || \
  die "active runtime pair does not match finalization authorization"
[ "$(readlink -f -- "${BASH_SOURCE[0]}")" = \
  "/opt/mooncen-an2p-runtime/releases/$pair_name/control/deploy/an2p/install_isolated_control_plane.sh" ] && \
  [ ! -L "${BASH_SOURCE[0]}" ] || die "execute only the exact immutable pair script"
[ -L "$control_runtime" ] && \
  [ "$(readlink "$control_runtime")" = ../mooncen-an2p-runtime/current/control ] && \
  [ "$(stat -c '%U:%G' "$control_runtime")" = root:root ] || \
  die "control runtime compatibility alias is unsafe"
[ -L "$docker_runtime" ] && \
  [ "$(readlink "$docker_runtime")" = ../mooncen-an2p-runtime/current/docker ] && \
  [ "$(stat -c '%U:%G' "$docker_runtime")" = root:root ] || \
  die "Docker runtime compatibility alias is unsafe"
[ -x /usr/local/libexec/mooncen-an2p-runtime-manager ] && \
  [ ! -L /usr/local/libexec/mooncen-an2p-runtime-manager ] && \
  [ "$(stat -c '%U:%G:%a' /usr/local/libexec/mooncen-an2p-runtime-manager)" = root:root:755 ] || \
  die "root-installed runtime manager is unsafe"
/usr/local/libexec/mooncen-an2p-runtime-manager validate "$pair_name" >/dev/null
[ -x "$control_runtime/.venv/bin/python" ] && \
  [ ! -L "$control_runtime/.venv/bin/python" ] || \
  die "copied Python runtime is unavailable"
[ ! -e "$control_runtime/.git" ] && [ ! -L "$control_runtime/.git" ] || \
  die "immutable control runtime contains Git metadata"

private_source() {
  local name=$1 path="$bootstrap/$1"
  [ -f "$path" ] && [ ! -L "$path" ] && \
    [ "$(stat -c '%U:%G:%a' "$path")" = "root:root:600" ] || \
    die "bootstrap input must be root:root mode 0600: $name"
  printf '%s\n' "$path"
}

api_env=$(private_source ops-api.env)
worker_env=$(private_source deployment-worker.env)
deploy_config=$(private_source deploy-ssh_config)
deploy_key=$(private_source deploy-id_ed25519)
deploy_hosts=$(private_source deploy-known_hosts)
status_config=$(private_source status-ssh_config)
status_key=$(private_source status-id_ed25519)
status_hosts=$(private_source status-known_hosts)
db_config=$(private_source db-ssh_config)
db_key=$(private_source db-id_ed25519)
db_hosts=$(private_source db-known_hosts)

[ "$(sha256sum "$deploy_key" | cut -d' ' -f1)" != \
  "$(sha256sum "$status_key" | cut -d' ' -f1)" ] || \
  die "deploy and status private keys must be distinct"
[ "$(sha256sum "$deploy_key" | cut -d' ' -f1)" != \
  "$(sha256sum "$db_key" | cut -d' ' -f1)" ] || \
  die "deploy and DB private keys must be distinct"
[ "$(sha256sum "$status_key" | cut -d' ' -f1)" != \
  "$(sha256sum "$db_key" | cut -d' ' -f1)" ] || \
  die "status and DB private keys must be distinct"

install -d -o root -g root -m 0755 /etc/mooncen-an2p
api_env_destination=/etc/mooncen-an2p/ops-api.env
api_env_stage=$(mktemp /etc/mooncen-an2p/.ops-api.env.new.XXXXXX)
api_env_backup=
api_env_had_previous=false
api_env_published=false
api_env_committed=false
api_cutover_started=false
api_service_masked=false
api_was_active=false
if systemctl is-active --quiet mooncen-ops-api.service; then
  api_was_active=true
fi
if [ -e "$api_env_destination" ] || [ -L "$api_env_destination" ]; then
  [ -f "$api_env_destination" ] && [ ! -L "$api_env_destination" ] && \
    [ "$(stat -c '%U:%G:%a' "$api_env_destination")" = \
      "root:${api_user}:640" ] || \
    die "installed Ops API environment is unsafe"
  api_env_backup=$(mktemp /etc/mooncen-an2p/.ops-api.env.previous.XXXXXX)
  install -o root -g root -m 0600 "$api_env_destination" "$api_env_backup"
  sync -f -- "$api_env_backup"
  api_env_had_previous=true
fi
install -o root -g "$api_user" -m 0640 "$api_env" "$api_env_stage"
sync -f -- "$api_env_stage"

cleanup_api_environment() {
  local status=$?
  trap - EXIT
  set +e
  if [ "$api_env_committed" != true ] && [ "$api_cutover_started" = true ]; then
    if [ "$api_env_published" = true ]; then
      if [ "$api_env_had_previous" = true ]; then
        install -o root -g "$api_user" -m 0640 \
          "$api_env_backup" "$api_env_destination"
        sync -f -- "$api_env_destination"
        sync -f -- /etc/mooncen-an2p
      else
        rm -f -- "$api_env_destination"
        sync -f -- /etc/mooncen-an2p
      fi
    fi
    if [ "$api_service_masked" = true ]; then
      systemctl unmask --runtime mooncen-ops-api.service >/dev/null 2>&1
      api_service_masked=false
    fi
    systemctl daemon-reload >/dev/null 2>&1
    if [ "$api_was_active" = true ] && [ "$api_env_had_previous" = true ]; then
      systemctl reset-failed mooncen-ops-api.service >/dev/null 2>&1
      systemctl restart mooncen-ops-api.service >/dev/null 2>&1
    else
      systemctl stop mooncen-ops-api.service >/dev/null 2>&1
    fi
  elif [ "$api_service_masked" = true ]; then
    systemctl unmask --runtime mooncen-ops-api.service >/dev/null 2>&1
    systemctl daemon-reload >/dev/null 2>&1
  fi
  [ -z "$api_env_stage" ] || rm -f -- "$api_env_stage"
  [ -z "$api_env_backup" ] || rm -f -- "$api_env_backup"
  exit "$status"
}
trap cleanup_api_environment EXIT

install -o root -g "$worker_user" -m 0640 "$worker_env" \
  /etc/mooncen-an2p/deployment-worker.env
install -d -o root -g "$worker_user" -m 0750 \
  /etc/mooncen-an2p/deploy-transport
install -o root -g "$worker_user" -m 0640 "$deploy_config" \
  /etc/mooncen-an2p/deploy-transport/ssh_config
install -o root -g "$worker_user" -m 0640 "$deploy_key" \
  /etc/mooncen-an2p/deploy-transport/id_ed25519
install -o root -g "$worker_user" -m 0640 "$deploy_hosts" \
  /etc/mooncen-an2p/deploy-transport/known_hosts
install -d -o root -g "$api_user" -m 0750 \
  /etc/mooncen-an2p/status-transport
install -o root -g "$api_user" -m 0640 "$status_config" \
  /etc/mooncen-an2p/status-transport/ssh_config
install -o root -g "$api_user" -m 0640 "$status_key" \
  /etc/mooncen-an2p/status-transport/id_ed25519
install -o root -g "$api_user" -m 0640 "$status_hosts" \
  /etc/mooncen-an2p/status-transport/known_hosts
install -d -o root -g "$tunnel_user" -m 0750 /etc/mooncen-an2p/db-tunnel
install -o root -g "$tunnel_user" -m 0640 "$db_config" \
  /etc/mooncen-an2p/db-tunnel/ssh_config
install -o root -g "$tunnel_user" -m 0640 "$db_key" \
  /etc/mooncen-an2p/db-tunnel/id_ed25519
install -o root -g "$tunnel_user" -m 0640 "$db_hosts" \
  /etc/mooncen-an2p/db-tunnel/known_hosts
install -d -o root -g "$worker_user" -m 0750 \
  /var/lib/mooncen-deployment-worker
install -d -o "$worker_user" -g "$worker_user" -m 0700 \
  /var/lib/mooncen-deployment-worker/state
install -d -o root -g "$worker_user" -m 0750 \
  /var/lib/mooncen-deployment-worker/releases
install -d -o "$docker_user" -g "$docker_user" -m 0700 \
  /var/lib/mooncen-docker-operator

install -d -o root -g root -m 0755 /usr/local/libexec /etc/systemd/system
install -o root -g root -m 0755 "$script_dir/mooncen_an2p_service_control.py" \
  /usr/local/libexec/mooncen-an2p-service-control
install -o root -g root -m 0755 "$script_dir/mooncen_loopback_redirect.py" \
  /usr/local/libexec/mooncen-an2p-loopback-redirect
install -o root -g root -m 0755 "$script_dir/runtime_pair_manager.py" \
  /usr/local/libexec/mooncen-an2p-runtime-manager
for unit in mooncen-an2p-runtime-recovery.service \
  mooncen-ops-api.service mooncen-ops-api.socket \
  mooncen-ops-api-ipv6.service mooncen-ops-api-ipv6.socket \
  mooncen-ops-status-agent.service \
  mooncen-deployment-worker.service mooncen-ops-db-tunnel.service \
  mooncen-docker-dev.service; do
  install -o root -g root -m 0644 "$script_dir/$unit" "/etc/systemd/system/$unit"
done

sudoers=/etc/sudoers.d/mooncen-an2p-service-control
sudoers_tmp=$(mktemp /etc/sudoers.d/.mooncen-an2p-service-control.XXXXXX)
cat >"$sudoers_tmp" <<EOF
${legacy_user} ALL=(root) NOPASSWD: /usr/local/libexec/mooncen-an2p-service-control docker-select, /usr/local/libexec/mooncen-an2p-service-control native-select, /usr/local/libexec/mooncen-an2p-service-control docker-reload, /usr/local/libexec/mooncen-an2p-service-control runtime-status, /usr/local/libexec/mooncen-an2p-service-control lxd-db-start, /usr/local/libexec/mooncen-an2p-service-control lxd-db-stop, /usr/local/libexec/mooncen-an2p-service-control lxd-db-status
EOF
chmod 0440 "$sudoers_tmp"
/usr/sbin/visudo -cf "$sudoers_tmp" >/dev/null
install -o root -g root -m 0440 "$sudoers_tmp" "$sudoers"
rm -f -- "$sudoers_tmp"

legacy_units=(
  mooncen-ops-control-env.service
  mooncen-ops-db-tunnel.service
  mooncen-ops-api.service
  mooncen-deployment-worker.service
  mooncen-docker-dev.service
  mooncen-ops-console.service
)
systemctl --user --machine="${legacy_user}@" disable --now \
  "${legacy_units[@]}" >/dev/null 2>&1 || true
systemctl --global mask "${legacy_units[@]}" >/dev/null
for unit in "${legacy_units[@]}"; do
  if systemctl --user --machine="${legacy_user}@" is-active --quiet "$unit"; then
    die "legacy user service is still active: $unit"
  fi
done
# Claim the reviewed same-origin Ops port immediately after the legacy Vite
# proxy is stopped. The socket remains active across API/runtime restarts.
systemctl daemon-reload
systemctl enable --now mooncen-ops-api.socket mooncen-ops-api-ipv6.socket \
  mooncen-ops-api-ipv6.service

# Move superseded service credentials to a root-only recoverable quarantine.
quarantine=/var/lib/mooncen-an2p-legacy-credentials
safe_legacy_user_paths quarantine-credentials \
  "$legacy_home" "$legacy_uid" "$legacy_gid" "$quarantine" 0 0 >/dev/null

for account in "$api_user" "$worker_user" "$tunnel_user"; do
  for privileged_group in docker lxd; do
    getent group "$privileged_group" >/dev/null || continue
    id -nG "$account" | tr ' ' '\n' | grep -Fxq "$privileged_group" && \
      die "service account inherited host-root group ${privileged_group}: $account"
  done
done

[ -f /etc/mooncen-an2p/docker-development-enabled ] && \
  [ ! -L /etc/mooncen-an2p/docker-development-enabled ] && \
  [ "$(stat -c '%U:%G:%a:%s' /etc/mooncen-an2p/docker-development-enabled)" = \
    root:root:644:0 ] || die "finalization requires the healthy Docker development phase"

systemctl daemon-reload
systemctl enable mooncen-ops-db-tunnel.service
systemctl restart mooncen-ops-db-tunnel.service
systemctl is-active --quiet mooncen-ops-db-tunnel.service ||
  die "isolated Ops DB tunnel did not become active after credential rotation"
systemctl enable mooncen-an2p-runtime-recovery.service \
  mooncen-ops-api.socket mooncen-ops-api-ipv6.socket \
  mooncen-ops-api-ipv6.service mooncen-ops-api.service \
  mooncen-deployment-worker.service mooncen-ops-status-agent.service
systemctl start mooncen-an2p-runtime-recovery.service \
  mooncen-ops-api.socket mooncen-ops-api-ipv6.socket \
  mooncen-ops-api-ipv6.service

# Keep both root-owned 5175 sockets bound while preventing a queued request
# from reactivating the API between stop and the credential commit.
api_cutover_started=true
systemctl mask --runtime mooncen-ops-api.service
api_service_masked=true
systemctl stop mooncen-ops-api.service
mv -fT -- "$api_env_stage" "$api_env_destination"
api_env_stage=
api_env_published=true
sync -f -- "$api_env_destination"
sync -f -- /etc/mooncen-an2p
systemctl unmask --runtime mooncen-ops-api.service
api_service_masked=false
systemctl daemon-reload
systemctl restart mooncen-ops-api.service
systemctl is-active --quiet mooncen-ops-api.service || \
  die "isolated Ops API did not become active after credential rotation"
"$control_runtime/.venv/bin/python" \
  "$control_runtime/tools/wait_for_an2p_http.py" \
  http://127.0.0.1:5175/health --timeout 90
api_pid=$(systemctl show --property MainPID --value mooncen-ops-api.service)
[[ "$api_pid" =~ ^[1-9][0-9]*$ ]] && [ -r "/proc/$api_pid/environ" ] || \
  die "isolated Ops API process identity is unavailable"
/usr/bin/python3 -I - "$api_pid" "$api_env_destination" \
  "${api_env_backup:--}" <<'PY'
import pathlib
import sys


def password_hash(path: pathlib.Path) -> bytes | None:
    if str(path) == "-":
        return None
    payload = path.read_bytes()
    if b"\x00" in payload or b"\r" in payload or not payload.endswith(b"\n"):
        raise SystemExit("Ops API environment encoding is invalid")
    prefix = b"MOONCEN_OPS_PASSWORD_HASH="
    matches = [line[len(prefix) :] for line in payload.splitlines() if line.startswith(prefix)]
    if len(matches) != 1 or not matches[0]:
        raise SystemExit("Ops API password hash contract is invalid")
    try:
        algorithm, rounds_text, salt, digest = matches[0].decode("ascii").split("$", 3)
        rounds = int(rounds_text)
    except (UnicodeDecodeError, ValueError):
        raise SystemExit("Ops API password hash contract is invalid") from None
    if (
        algorithm != "pbkdf2_sha256"
        or not 310_000 <= rounds <= 2_000_000
        or not 16 <= len(salt) <= 128
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise SystemExit("Ops API password hash contract is invalid")
    return matches[0]


pid = int(sys.argv[1])
desired = password_hash(pathlib.Path(sys.argv[2]))
previous = password_hash(pathlib.Path(sys.argv[3]))
environment = {}
for item in pathlib.Path(f"/proc/{pid}/environ").read_bytes().split(b"\x00"):
    if not item:
        continue
    name, separator, value = item.partition(b"=")
    if not separator or name in environment:
        raise SystemExit("Ops API process environment is invalid")
    environment[name] = value
loaded = environment.get(b"MOONCEN_OPS_PASSWORD_HASH")
if loaded != desired or (previous is not None and previous != desired and loaded == previous):
    raise SystemExit("Ops API did not load the rotated password hash")
PY
api_env_committed=true
api_cutover_started=false
systemctl enable --now mooncen-deployment-worker.service
systemctl enable --now mooncen-ops-status-agent.service

# Recreate the unprivileged user manager only after retained docker/lxd groups
# are gone, then deterministically converge the non-secret UI/status services
# and the selected development runtime.
user_unit_dir="$legacy_home/.config/systemd/user"
user_runtime_dir="$legacy_home/.local/share/mooncen-an2p"
unit_quarantine=/var/lib/mooncen-an2p-legacy-user-units
safe_legacy_user_paths prepare-user-runtime \
  "$legacy_home" "$legacy_uid" "$legacy_gid" "$unit_quarantine" 0 0 \
  "$script_dir" "$control_runtime/tools" 1 >/dev/null
/usr/bin/loginctl enable-linger "$legacy_user"
systemctl start "user@${legacy_uid}.service"
systemctl --user --machine="${legacy_user}@" daemon-reload
auxiliary_units=(mooncen-docs.service)
native_units=(mooncen-api.service mooncen-frontend.service)
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
[ ! -e "$operation_journal" ] && [ ! -L "$operation_journal" ] || \
  die "runtime pair transaction blocks isolated control convergence"
export MOONCEN_AN2P_MANAGER_LOCK_FD=6
/usr/local/libexec/mooncen-an2p-service-control docker-select >/dev/null
systemctl --user --machine="${legacy_user}@" enable --now \
  mooncen-development-runtime.target "${auxiliary_units[@]}"
for unit in "${auxiliary_units[@]}"; do
  systemctl --user --machine="${legacy_user}@" is-active --quiet "$unit" || \
    die "safe user service failed after privilege revocation: $unit"
done

"$control_runtime/.venv/bin/python" -I \
  "$control_runtime/tools/wait_for_an2p_http.py" \
  http://127.0.0.1:5175/health --timeout 90
/usr/bin/python3 -I - <<'PY'
import socket

with socket.create_connection(("::1", 5175), timeout=5) as connection:
    connection.sendall(b"GET / HTTP/1.1\r\nHost: localhost:5175\r\nConnection: close\r\n\r\n")
    response = connection.recv(4096)
if not response.startswith(b"HTTP/1.1 308 ") or b"Location: http://127.0.0.1:5175/\r\n" not in response:
    raise SystemExit("IPv6 Ops loopback reservation did not converge")
PY
"$control_runtime/.venv/bin/python" -I \
  "$control_runtime/tools/wait_for_an2p_http.py" \
  http://127.0.0.1:8001/health --timeout 240
"$control_runtime/.venv/bin/python" -I \
  "$control_runtime/tools/wait_for_an2p_http.py" \
  http://127.0.0.1:5174 --timeout 240
"$control_runtime/.venv/bin/python" -I \
  "$control_runtime/tools/wait_for_an2p_http.py" \
  http://127.0.0.1:8765 --timeout 90
if /usr/bin/ss -H -ltn 'sport = :8002' | grep -q .; then
  die "retired Ops API port 8002 is still listening"
fi
selection_status=$(/usr/local/libexec/mooncen-an2p-service-control runtime-status)
/usr/bin/python3.12 -I -c \
  'import json,sys; value=json.load(sys.stdin); assert value == {"docker_active":True,"docker_enabled":True,"marker":True,"native_active":[],"native_enabled":[],"schema_version":1}' \
  <<<"$selection_status" || die "isolated control convergence lost Docker selection"
unset MOONCEN_AN2P_MANAGER_LOCK_FD
exec 6>&-

printf '%s\n' "isolated an2p API, worker, DB tunnel, and constrained Docker operator installed"
