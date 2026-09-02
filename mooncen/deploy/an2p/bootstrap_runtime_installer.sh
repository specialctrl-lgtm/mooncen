#!/bin/bash
set -euo pipefail
umask 077
export PATH=/usr/sbin:/usr/bin:/sbin:/bin

die() {
  printf '%s\n' "an2p runtime installer bootstrap: $*" >&2
  exit 78
}

[ "$(id -u)" -eq 0 ] || die "run from an independent root console"
[ "${SUDO_USER:-root}" = root ] || die "do not run through an untrusted user session"
[ "$(hostname -s)" = an2p ] || die "unexpected host"
[ "$#" -eq 16 ] && [ "$1" = --installer-sha256 ] &&
  [ "$3" = --integrity-sha256 ] && [ "$5" = --clean-source-sha256 ] &&
  [ "$7" = --pair-manager-sha256 ] && [ "$9" = --handoff-sha256 ] &&
  [ "${11}" = --registrar-sha256 ] &&
  [ "${13}" = --host-transition-sha256 ] &&
  [ "${15}" = --build-policy-sha256 ] ||
  die "expected eight fixed SHA-256 options"

installer_sha=$2
integrity_sha=$4
clean_source_sha=$6
pair_manager_sha=$8
handoff_sha=${10}
registrar_sha=${12}
host_transition_sha=${14}
build_policy_sha=${16}
for digest in "$installer_sha" "$integrity_sha" "$clean_source_sha" \
  "$pair_manager_sha" "$handoff_sha" "$registrar_sha" \
  "$host_transition_sha" "$build_policy_sha"; do
  [[ "$digest" =~ ^[0-9a-f]{64}$ ]] || die "invalid reviewed SHA-256 value"
done

recovery_unit_name=mooncen-an2p-bootstrap-recovery.service
recovery_unit=/etc/systemd/system/$recovery_unit_name
bootstrap_stage=/root/mooncen-an2p-runtime-bootstrap.sh
source_root=/home/sgm/src/project/mooncen
checkout_installer=$source_root/deploy/an2p/install_runtime_snapshot.sh
checkout_host_transition=$source_root/deploy/an2p/host_layer_transition.py
recovery_installer=/var/lib/mooncen-an2p-runtime/reviewed-install-runtime-snapshot.sh
recovery_host_transition=/var/lib/mooncen-an2p-runtime/reviewed-host-layer-transition.py
source_installer=$recovery_installer
source_host_transition=$recovery_host_transition
target_installer=/usr/local/sbin/mooncen-an2p-runtime-install
target_host_transition=/usr/local/libexec/mooncen-an2p-host-transition
trust_directory=/etc/mooncen-an2p
trust_target=$trust_directory/runtime-installer.trust
bootstrap_phase=

verify_native_public_health() {
  /usr/bin/python3 -I - <<'PY'
import time
import urllib.error
import urllib.request


class RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        return None


opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    RejectRedirects(),
)
for url in ("http://127.0.0.1:8001/health", "http://127.0.0.1:5174"):
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        try:
            with opener.open(url, timeout=3) as response:
                body = response.read(1024 * 1024 + 1)
                content_type = response.headers.get_content_type()
                ready = (
                    response.status == 200
                    and response.geturl() == url
                    and len(body) <= 1024 * 1024
                    and (
                        (
                            url.endswith("/health")
                            and content_type == "application/json"
                            and body == b'{"status":"ready"}'
                        )
                        or (
                            not url.endswith("/health")
                            and content_type == "text/html"
                            and bool(body)
                        )
                    )
                )
                if ready:
                    break
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(1)
    else:
        raise SystemExit("native development runtime did not recover")
PY
}

refuse_pending_runtime_transactions() {
  local residue
  for residue in \
    /var/lib/mooncen-an2p-runtime/host-layer-transition.json \
    /var/lib/mooncen-an2p-runtime/install-transaction.json \
    /var/lib/mooncen-an2p-runtime/transaction.json \
    /var/lib/mooncen-an2p-runtime/control-finalization-transaction.json \
    /var/lib/mooncen-an2p-runtime/ops-rotation-transaction.json \
    /var/lib/mooncen-an2p-runtime/ops-rotation-previous.env \
    /etc/systemd/system/mooncen-an2p-host-transition-recovery.service \
    /etc/systemd/system/mooncen-an2p-host-transition-continue.service \
    /etc/systemd/system/multi-user.target.wants/mooncen-an2p-host-transition-recovery.service \
    /etc/systemd/system/mooncen-ops-api.socket.requires/mooncen-an2p-host-transition-recovery.service \
    /etc/systemd/system/mooncen-ops-api-ipv6.socket.requires/mooncen-an2p-host-transition-recovery.service \
    /etc/systemd/system/mooncen-ops-api-ipv6.service.requires/mooncen-an2p-host-transition-recovery.service \
    /etc/systemd/system/mooncen-an2p-runtime-recovery.service.requires/mooncen-an2p-host-transition-recovery.service \
    /etc/systemd/system/mooncen-ops-db-tunnel.service.requires/mooncen-an2p-host-transition-recovery.service \
    /etc/systemd/system/mooncen-deployment-worker.service.requires/mooncen-an2p-host-transition-recovery.service \
    /etc/systemd/system/mooncen-ops-status-agent.service.requires/mooncen-an2p-host-transition-recovery.service \
    /etc/systemd/system/mooncen-ops-api.service.requires/mooncen-an2p-host-transition-recovery.service \
    /etc/systemd/system/mooncen-docker-dev.service.requires/mooncen-an2p-host-transition-recovery.service; do
    [ ! -e "$residue" ] && [ ! -L "$residue" ] ||
      die "pending runtime transaction blocks installer bootstrap: ${residue##*/}"
  done
}

preflight_bootstrap_inputs_and_public_runtime() {
  local journal=/var/lib/mooncen-an2p-runtime/bootstrap-development.json
  local installer_input=$source_installer
  local host_transition_input=$source_host_transition
  # Replacing the installed installer, transition interpreter, or trust
  # envelope while an older transaction owns recovery would strand that
  # transaction behind a different ABI.  The bootstrap journal below is the
  # only recovery state this program may own.
  refuse_pending_runtime_transactions
  if [ "${MOONCEN_AN2P_BOOTSTRAP_RECOVERY:-}" != 1 ]; then
    installer_input=$checkout_installer
    host_transition_input=$checkout_host_transition
  fi
  [ -f "$installer_input" ] && [ ! -L "$installer_input" ] ||
    die "reviewed installer source is unavailable"
  if [ "${MOONCEN_AN2P_BOOTSTRAP_RECOVERY:-}" = 1 ]; then
    [ "$(stat -c '%U:%G:%a' "$installer_input")" = root:root:700 ] ||
      die "root recovery installer source is unsafe"
  fi
  [ "$(sha256sum "$installer_input" | cut -d' ' -f1)" = "$installer_sha" ] ||
    die "reviewed installer source digest mismatch"
  [ -f "$host_transition_input" ] && [ ! -L "$host_transition_input" ] ||
    die "reviewed host transition source is unavailable"
  if [ "${MOONCEN_AN2P_BOOTSTRAP_RECOVERY:-}" = 1 ]; then
    [ "$(stat -c '%U:%G:%a' "$host_transition_input")" = root:root:700 ] ||
      die "root recovery host transition source is unsafe"
  fi
  [ "$(sha256sum "$host_transition_input" | cut -d' ' -f1)" = \
    "$host_transition_sha" ] ||
    die "reviewed host transition source digest mismatch"
  /usr/bin/python3 -I - <<'PY' || die "pidfd process-boundary support is unavailable"
import os
import signal


descriptor = os.pidfd_open(os.getpid(), 0)
try:
    signal.pidfd_send_signal(descriptor, 0)
finally:
    os.close(descriptor)
PY
  if [ -e "$journal" ] || [ -L "$journal" ]; then
    [ -f "$journal" ] && [ ! -L "$journal" ] &&
      [ "$(stat -c '%U:%G:%a' "$journal")" = root:root:600 ] ||
      die "bootstrap development journal residue is unsafe"
    return
  fi
  if systemctl --user --machine=sgm@ is-active --quiet mooncen-api.service &&
    systemctl --user --machine=sgm@ is-enabled --quiet mooncen-api.service &&
    systemctl --user --machine=sgm@ is-active --quiet mooncen-frontend.service &&
    systemctl --user --machine=sgm@ is-enabled --quiet mooncen-frontend.service; then
    [ ! -e /etc/mooncen-an2p/docker-development-enabled ] &&
      [ ! -L /etc/mooncen-an2p/docker-development-enabled ] ||
      die "native development overlaps the Docker selection marker"
    verify_native_public_health || die "native development health preflight failed"
  elif [ -f /etc/mooncen-an2p/docker-development-enabled ] &&
    [ ! -L /etc/mooncen-an2p/docker-development-enabled ] &&
    [ "$(stat -c '%U:%G:%a:%s' /etc/mooncen-an2p/docker-development-enabled)" = \
      root:root:644:0 ] &&
    systemctl is-active --quiet mooncen-docker-dev.service &&
    systemctl is-enabled --quiet mooncen-docker-dev.service; then
    :
  else
    die "public development runtime is not healthy before bootstrap"
  fi
}

stage_reviewed_installer_for_recovery() {
  local state_root=/var/lib/mooncen-an2p-runtime stage transition_stage
  install -d -o root -g root -m 0700 "$state_root"
  if [ -e "$recovery_installer" ] || [ -L "$recovery_installer" ]; then
    [ -f "$recovery_installer" ] && [ ! -L "$recovery_installer" ] &&
      [ "$(stat -c '%U:%G:%a' "$recovery_installer")" = root:root:700 ] &&
      [ "$(sha256sum "$recovery_installer" | cut -d' ' -f1)" = "$installer_sha" ] &&
      cmp -s -- "$checkout_installer" "$recovery_installer" ||
      die "root recovery installer residue is unsafe"
  else
    stage=$(mktemp "$state_root/.reviewed-install-runtime-snapshot.XXXXXXXX")
    install -o root -g root -m 0700 "$checkout_installer" "$stage"
    [ "$(sha256sum "$stage" | cut -d' ' -f1)" = "$installer_sha" ] || {
      rm -f -- "$stage"
      die "root recovery installer stage digest mismatch"
    }
    sync -f -- "$stage"
    if ! ln -- "$stage" "$recovery_installer" 2>/dev/null; then
      rm -f -- "$stage"
      [ -f "$recovery_installer" ] && [ ! -L "$recovery_installer" ] &&
        [ "$(stat -c '%U:%G:%a' "$recovery_installer")" = root:root:700 ] &&
        [ "$(sha256sum "$recovery_installer" | cut -d' ' -f1)" = "$installer_sha" ] &&
        cmp -s -- "$checkout_installer" "$recovery_installer" ||
        die "concurrent root recovery installer stage is unsafe"
    else
      rm -- "$stage"
    fi
  fi

  if [ -e "$recovery_host_transition" ] || [ -L "$recovery_host_transition" ]; then
    [ -f "$recovery_host_transition" ] && [ ! -L "$recovery_host_transition" ] &&
      [ "$(stat -c '%U:%G:%a' "$recovery_host_transition")" = root:root:700 ] &&
      [ "$(sha256sum "$recovery_host_transition" | cut -d' ' -f1)" = \
        "$host_transition_sha" ] &&
      cmp -s -- "$checkout_host_transition" "$recovery_host_transition" ||
      die "root recovery host transition residue is unsafe"
  else
    transition_stage=$(mktemp "$state_root/.reviewed-host-layer-transition.XXXXXXXX")
    install -o root -g root -m 0700 "$checkout_host_transition" "$transition_stage"
    [ "$(sha256sum "$transition_stage" | cut -d' ' -f1)" = \
      "$host_transition_sha" ] || {
      rm -f -- "$transition_stage"
      die "root recovery host transition stage digest mismatch"
    }
    sync -f -- "$transition_stage"
    if ! ln -- "$transition_stage" "$recovery_host_transition" 2>/dev/null; then
      rm -f -- "$transition_stage"
      [ -f "$recovery_host_transition" ] &&
        [ ! -L "$recovery_host_transition" ] &&
        [ "$(stat -c '%U:%G:%a' "$recovery_host_transition")" = root:root:700 ] &&
        [ "$(sha256sum "$recovery_host_transition" | cut -d' ' -f1)" = \
          "$host_transition_sha" ] &&
        cmp -s -- "$checkout_host_transition" "$recovery_host_transition" ||
        die "concurrent root recovery host transition stage is unsafe"
    else
      rm -- "$transition_stage"
    fi
  fi
  sync -f -- "$state_root"
}

install_bootstrap_recovery_unit() {
  local unit_stage unit_state retired_root retired_digest retired_unit
  [ "$(readlink -f -- "$0")" = "$bootstrap_stage" ] && [ ! -L "$0" ] && \
    [ "$(stat -c '%U:%G:%a' "$bootstrap_stage")" = root:root:700 ] || \
    die "execute only the reviewed root bootstrap stage"
  unit_stage=$(mktemp /etc/systemd/system/.mooncen-an2p-bootstrap-recovery.XXXXXXXX)
  {
    printf '%s\n' \
      '[Unit]' \
      'Description=Recover the reviewed an2p bootstrap security boundary' \
      'After=local-fs.target systemd-logind.service network-online.target' \
      'Wants=systemd-logind.service network-online.target' \
      'RequiresMountsFor=/home/sgm /var/lib/mooncen-an2p-runtime' \
      'ConditionFileIsExecutable=/root/mooncen-an2p-runtime-bootstrap.sh' \
      'StartLimitIntervalSec=infinity' \
      'StartLimitBurst=2' \
      'StartLimitAction=none' \
      '' \
      '[Service]' \
      'Type=oneshot'
    printf 'ExecStart=/usr/bin/env -i HOME=/root LANG=C LC_ALL=C PATH=/usr/sbin:/usr/bin:/sbin:/bin MOONCEN_AN2P_BOOTSTRAP_RECOVERY=1 /bin/bash %s' \
      "$bootstrap_stage"
    printf ' --installer-sha256 %s --integrity-sha256 %s' \
      "$installer_sha" "$integrity_sha"
    printf ' --clean-source-sha256 %s --pair-manager-sha256 %s' \
      "$clean_source_sha" "$pair_manager_sha"
    printf ' --handoff-sha256 %s --registrar-sha256 %s' \
      "$handoff_sha" "$registrar_sha"
    printf ' --host-transition-sha256 %s' "$host_transition_sha"
    printf ' --build-policy-sha256 %s\n' "$build_policy_sha"
    printf '%s\n' \
      'Restart=on-abnormal' \
      'RestartSec=30s' \
      'RestartPreventExitStatus=78' \
      'TimeoutStartSec=15min' \
      '' \
      '[Install]' \
      'WantedBy=multi-user.target'
  } >"$unit_stage"
  chown root:root "$unit_stage"
  chmod 0644 "$unit_stage"
  sync -f -- "$unit_stage"
  if [ -e "$recovery_unit" ] || [ -L "$recovery_unit" ]; then
    [ -f "$recovery_unit" ] && [ ! -L "$recovery_unit" ] && \
      [ "$(stat -c '%U:%G:%a' "$recovery_unit")" = root:root:644 ] || \
      die "bootstrap recovery unit residue is unsafe"
    if cmp -s -- "$unit_stage" "$recovery_unit"; then
      rm -- "$unit_stage"
    else
      # The 2026-08-20 incident left a disabled, inactive copy of the unsafe
      # unlimited-restart unit behind. Replace only that inert/no-journal
      # residue and retain its exact bytes for root-only incident review.
      ! systemctl is-active --quiet "$recovery_unit_name" || \
        die "a different bootstrap recovery unit is still active"
      unit_state=$(systemctl is-enabled "$recovery_unit_name" 2>/dev/null || true)
      [ "$unit_state" = disabled ] || \
        die "a different bootstrap recovery unit is not disabled"
      [ ! -e /var/lib/mooncen-an2p-runtime/bootstrap-development.json ] && \
        [ ! -L /var/lib/mooncen-an2p-runtime/bootstrap-development.json ] || \
        die "cannot replace a recovery unit that owns a pending journal"
      retired_root=/var/lib/mooncen-an2p-runtime/retired-bootstrap-units
      install -d -o root -g root -m 0700 "$retired_root"
      retired_digest=$(sha256sum "$recovery_unit" | cut -d' ' -f1)
      retired_unit=$retired_root/$retired_digest.service
      if [ -e "$retired_unit" ] || [ -L "$retired_unit" ]; then
        [ -f "$retired_unit" ] && [ ! -L "$retired_unit" ] && \
          [ "$(stat -c '%U:%G:%a' "$retired_unit")" = root:root:600 ] && \
          cmp -s -- "$recovery_unit" "$retired_unit" || \
          die "retired bootstrap unit archive is unsafe"
        rm -- "$recovery_unit"
      else
        mv -- "$recovery_unit" "$retired_unit"
        chmod 0600 "$retired_unit"
        sync -f -- "$retired_root"
      fi
      mv -- "$unit_stage" "$recovery_unit"
      sync -f -- /etc/systemd/system
    fi
  else
    mv -- "$unit_stage" "$recovery_unit"
    sync -f -- /etc/systemd/system
  fi
  systemctl daemon-reload
  # --now submits the service job to PID 1 in the same command that makes it
  # boot-durable. If this launcher is killed after submission, systemd still
  # owns the journaled recovery instead of waiting for a manual rerun.
  systemctl reset-failed "$recovery_unit_name" >/dev/null 2>&1 || true
  systemctl enable --now "$recovery_unit_name" >/dev/null
}

acquire_outer_launcher_lock_and_reexec() {
  exec /usr/bin/python3 -I - "$0" "$@" <<'PY'
import fcntl
import os
import pathlib
import stat
import sys


class BoundaryError(RuntimeError):
    pass


def mount_id(descriptor: int) -> int:
    fdinfo = os.open(
        f"/proc/self/fdinfo/{descriptor}",
        os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
    )
    try:
        payload = os.read(fdinfo, 65537)
    finally:
        os.close(fdinfo)
    if len(payload) > 65536:
        raise BoundaryError("outer launcher descriptor metadata is oversized")
    for line in payload.splitlines():
        if line.startswith(b"mnt_id:"):
            fields = line.split()
            if len(fields) == 2:
                try:
                    value = int(fields[1], 10)
                except ValueError:
                    break
                if value > 0:
                    return value
    raise BoundaryError("outer launcher mount identity is unavailable")


def validate_directory(metadata: os.stat_result, mode: int, label: str) -> None:
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != mode
    ):
        raise BoundaryError(f"unsafe outer launcher directory: {label}")


def main() -> None:
    # python receives: program name, reviewed bootstrap path, then the exact
    # sixteen option/value shell arguments.
    if os.geteuid() != 0 or len(sys.argv) != 18:
        raise BoundaryError("outer launcher lock requires root and the reviewed arguments")
    script = pathlib.Path(sys.argv[1])
    expected_script = pathlib.Path("/root/mooncen-an2p-runtime-bootstrap.sh")
    script_metadata = script.lstat()
    if (
        script != expected_script
        or script.is_symlink()
        or not stat.S_ISREG(script_metadata.st_mode)
        or script_metadata.st_uid != 0
        or script_metadata.st_gid != 0
        or stat.S_IMODE(script_metadata.st_mode) != 0o700
    ):
        raise BoundaryError("outer launcher is not the reviewed root bootstrap stage")

    var_directory = os.open(
        "/var/lib",
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
    )
    state_directory = -1
    lock_descriptor = -1
    try:
        var_metadata = os.fstat(var_directory)
        if (
            not stat.S_ISDIR(var_metadata.st_mode)
            or var_metadata.st_uid != 0
            or var_metadata.st_gid != 0
            or stat.S_IMODE(var_metadata.st_mode) & 0o022
        ):
            raise BoundaryError("outer launcher /var/lib boundary is unsafe")
        try:
            os.mkdir("mooncen-an2p-runtime", 0o700, dir_fd=var_directory)
            os.fsync(var_directory)
        except FileExistsError:
            pass
        state_directory = os.open(
            "mooncen-an2p-runtime",
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=var_directory,
        )
        validate_directory(os.fstat(state_directory), 0o700, "runtime state")
        if mount_id(state_directory) != mount_id(var_directory):
            raise BoundaryError("outer launcher state directory crosses a mount boundary")
        state_entry = os.stat(
            "mooncen-an2p-runtime",
            dir_fd=var_directory,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISDIR(state_entry.st_mode)
            or (state_entry.st_dev, state_entry.st_ino)
            != (
                os.fstat(state_directory).st_dev,
                os.fstat(state_directory).st_ino,
            )
        ):
            raise BoundaryError("outer launcher state directory inode changed")

        created = False
        try:
            lock_descriptor = os.open(
                "bootstrap-launcher.lock",
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=state_directory,
            )
            created = True
        except FileExistsError:
            lock_descriptor = os.open(
                "bootstrap-launcher.lock",
                os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=state_directory,
            )
        if created:
            os.fchown(lock_descriptor, 0, 0)
            os.fchmod(lock_descriptor, 0o600)
            os.fsync(lock_descriptor)
            os.fsync(state_directory)
        lock_metadata = os.fstat(lock_descriptor)
        if (
            not stat.S_ISREG(lock_metadata.st_mode)
            or lock_metadata.st_uid != 0
            or lock_metadata.st_gid != 0
            or stat.S_IMODE(lock_metadata.st_mode) != 0o600
            or lock_metadata.st_nlink != 1
        ):
            raise BoundaryError("outer launcher lock is unsafe")
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
        lock_entry = os.stat(
            "bootstrap-launcher.lock",
            dir_fd=state_directory,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(lock_entry.st_mode)
            or (lock_entry.st_dev, lock_entry.st_ino)
            != (lock_metadata.st_dev, lock_metadata.st_ino)
        ):
            raise BoundaryError("outer launcher lock inode changed while waiting")
        os.set_inheritable(lock_descriptor, True)
        environment = {
            "HOME": "/root",
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
            "SUDO_USER": "root",
            "MOONCEN_AN2P_BOOTSTRAP_LAUNCHER_LOCK_FD": str(lock_descriptor),
        }
        os.close(state_directory)
        state_directory = -1
        os.close(var_directory)
        var_directory = -1
        os.execve(
            "/bin/bash",
            ["bash", str(expected_script), *sys.argv[2:]],
            environment,
        )
    finally:
        if lock_descriptor >= 0:
            os.close(lock_descriptor)
        if state_directory >= 0:
            os.close(state_directory)
        if var_directory >= 0:
            os.close(var_directory)


try:
    main()
except (BoundaryError, OSError) as error:
    print(f"an2p bootstrap outer launcher boundary: {error}", file=sys.stderr)
    raise SystemExit(78) from error
PY
}

verify_outer_launcher_lock() {
  local descriptor=$1
  /usr/bin/python3 -I - "$descriptor" <<'PY'
import fcntl
import os
import stat
import sys


try:
    descriptor = int(sys.argv[1], 10)
except (IndexError, ValueError) as error:
    raise SystemExit(78) from error
if descriptor < 3:
    raise SystemExit(78)
metadata = os.fstat(descriptor)
path_metadata = os.stat(
    "/var/lib/mooncen-an2p-runtime/bootstrap-launcher.lock",
    follow_symlinks=False,
)
if (
    not stat.S_ISREG(metadata.st_mode)
    or not stat.S_ISREG(path_metadata.st_mode)
    or metadata.st_uid != 0
    or metadata.st_gid != 0
    or stat.S_IMODE(metadata.st_mode) != 0o600
    or metadata.st_nlink != 1
    or (metadata.st_dev, metadata.st_ino)
    != (path_metadata.st_dev, path_metadata.st_ino)
):
    raise SystemExit(78)
try:
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError as error:
    raise SystemExit(78) from error
PY
}

verify_outer_bootstrap_convergence() {
  /usr/bin/python3 -I - "$target_installer" "$installer_sha" \
    "$target_host_transition" "$host_transition_sha" "$trust_target" \
    "$integrity_sha" "$clean_source_sha" "$pair_manager_sha" "$handoff_sha" \
    "$registrar_sha" "$build_policy_sha" <<'PY'
import hashlib
import os
import pathlib
import stat
import sys


def open_exact(path: pathlib.Path, mode: int) -> tuple[int, os.stat_result]:
    metadata = path.lstat()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    opened = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or not stat.S_ISREG(opened.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or opened.st_uid != 0
        or opened.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != mode
        or stat.S_IMODE(opened.st_mode) != mode
        or metadata.st_nlink != 1
        or opened.st_nlink != 1
        or (metadata.st_dev, metadata.st_ino) != (opened.st_dev, opened.st_ino)
    ):
        os.close(descriptor)
        raise SystemExit(78)
    return descriptor, opened


target = pathlib.Path(sys.argv[1])
target_descriptor, _ = open_exact(target, 0o755)
try:
    digest = hashlib.sha256()
    while chunk := os.read(target_descriptor, 1024 * 1024):
        digest.update(chunk)
finally:
    os.close(target_descriptor)
if digest.hexdigest() != sys.argv[2]:
    raise SystemExit(78)

transition = pathlib.Path(sys.argv[3])
transition_descriptor, _ = open_exact(transition, 0o755)
try:
    transition_digest = hashlib.sha256()
    while chunk := os.read(transition_descriptor, 1024 * 1024):
        transition_digest.update(chunk)
finally:
    os.close(transition_descriptor)
if transition_digest.hexdigest() != sys.argv[4]:
    raise SystemExit(78)

trust = pathlib.Path(sys.argv[5])
trust_descriptor, trust_metadata = open_exact(trust, 0o600)
try:
    chunks = []
    remaining = 4097
    while remaining:
        chunk = os.read(trust_descriptor, remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    payload = b"".join(chunks)
finally:
    os.close(trust_descriptor)
if len(payload) > 4096 or len(payload) != trust_metadata.st_size:
    raise SystemExit(78)
expected = (
    "VERSION=1\n"
    f"INSTALLER_SHA256={sys.argv[2]}\n"
    f"INTEGRITY_SHA256={sys.argv[6]}\n"
    f"CLEAN_SOURCE_SHA256={sys.argv[7]}\n"
    f"PAIR_MANAGER_SHA256={sys.argv[8]}\n"
    f"HANDOFF_SHA256={sys.argv[9]}\n"
    f"REGISTRAR_SHA256={sys.argv[10]}\n"
    f"HOST_TRANSITION_SHA256={sys.argv[4]}\n"
    f"EXPECTED_BUILD_POLICY_SHA256={sys.argv[11]}\n"
).encode("ascii")
if payload != expected:
    raise SystemExit(78)

journal = pathlib.Path("/var/lib/mooncen-an2p-runtime/bootstrap-development.json")
try:
    journal.lstat()
except FileNotFoundError:
    pass
else:
    raise SystemExit(78)
PY
}

if [ "${MOONCEN_AN2P_BOOTSTRAP_RECOVERY:-}" != 1 ]; then
  if [ -z "${MOONCEN_AN2P_BOOTSTRAP_LAUNCHER_LOCK_FD:-}" ]; then
    acquire_outer_launcher_lock_and_reexec "$@"
    die "outer launcher lock re-exec returned unexpectedly"
  fi
  [[ "$MOONCEN_AN2P_BOOTSTRAP_LAUNCHER_LOCK_FD" =~ ^[0-9]+$ ]] || \
    die "outer launcher lock descriptor is invalid"
  verify_outer_launcher_lock "$MOONCEN_AN2P_BOOTSTRAP_LAUNCHER_LOCK_FD" || \
    die "outer launcher lock descriptor is unsafe"
  # Fail before creating/enabling any recovery state when the reviewed source
  # or the currently serving public runtime cannot be proven exact and healthy.
  preflight_bootstrap_inputs_and_public_runtime
  # A pre-pair root Docker service can keep serving while this script is
  # read-only, but its retired split alias cannot be reconstructed after the
  # pair manager journals and stops it.  Refuse before installing/enabling the
  # recovery worker (and before revoking any user privilege) so a failed first
  # pair activation never turns that unsupported prestate into an outage.
  if { [ ! -e /opt/mooncen-an2p-runtime/current ] && \
       [ ! -L /opt/mooncen-an2p-runtime/current ]; } && \
     { [ -e /etc/mooncen-an2p/docker-development-enabled ] || \
       [ -L /etc/mooncen-an2p/docker-development-enabled ] || \
       systemctl is-active --quiet mooncen-docker-dev.service || \
       systemctl is-enabled --quiet mooncen-docker-dev.service; }; then
    die "first pair bootstrap requires the reviewed native development runtime; legacy Docker selection was left untouched"
  fi
  # Recovery must not depend on a mutable sgm-owned checkout after host-root
  # revocation begins. Publish the exact reviewed installer into root-only
  # durable state before PID 1 is allowed to arm the recovery worker.
  stage_reviewed_installer_for_recovery
  install_bootstrap_recovery_unit
  # The persistent unit owns every mutation. Only an abnormal termination may
  # restart it, and the lifetime start limit permits at most one automatic
  # retry. Explicit invariant/configuration failures remain stopped for manual
  # inspection; enablement still covers a host reboot at every journal phase.
  verify_outer_bootstrap_convergence || \
    die "durable bootstrap recovery did not converge exactly"
  [ "$(systemctl show "$recovery_unit_name" --property=Result --value)" = success ] && \
    [ "$(systemctl show "$recovery_unit_name" --property=ActiveState --value)" = \
      inactive ] || die "durable bootstrap recovery service did not exit successfully"
  systemctl disable "$recovery_unit_name" >/dev/null || \
    die "durable bootstrap recovery service could not be disabled"
  recovery_enablement=$(systemctl is-enabled "$recovery_unit_name" 2>/dev/null || true)
  [ "$recovery_enablement" = disabled ] || \
    die "durable bootstrap recovery service remains enabled"
  [ -f "$recovery_unit" ] && [ ! -L "$recovery_unit" ] && \
    [ "$(stat -c '%U:%G:%a' "$recovery_unit")" = root:root:644 ] || \
    die "durable bootstrap recovery unit changed before cleanup"
  [ -f "$recovery_installer" ] && [ ! -L "$recovery_installer" ] && \
    [ "$(stat -c '%U:%G:%a' "$recovery_installer")" = root:root:700 ] && \
    [ "$(sha256sum "$recovery_installer" | cut -d' ' -f1)" = "$installer_sha" ] || \
    die "durable bootstrap recovery source changed before cleanup"
  [ -f "$recovery_host_transition" ] &&
    [ ! -L "$recovery_host_transition" ] &&
    [ "$(stat -c '%U:%G:%a' "$recovery_host_transition")" = root:root:700 ] &&
    [ "$(sha256sum "$recovery_host_transition" | cut -d' ' -f1)" = \
      "$host_transition_sha" ] ||
    die "durable bootstrap host transition source changed before cleanup"
  rm -- "$recovery_unit"
  rm -- "$recovery_installer"
  rm -- "$recovery_host_transition"
  systemctl daemon-reload
  sync -f -- /etc/systemd/system
  sync -f -- /var/lib/mooncen-an2p-runtime
  printf '%s\n' '{"installed":true,"recovery_converged":true,"schema_version":1}'
  exit 0
fi

# Serialize a manually retried stage with the boot-enabled recovery service.
install -d -o root -g root -m 0700 /var/lib/mooncen-an2p-runtime
bootstrap_lock=/var/lib/mooncen-an2p-runtime/bootstrap.lock
/usr/bin/python3 -I - "$bootstrap_lock" <<'PY'
import os
import pathlib
import stat
import sys


path = pathlib.Path(sys.argv[1])
try:
    descriptor = os.open(
        path,
        os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
except FileExistsError:
    descriptor = -1
else:
    try:
        os.fchown(descriptor, 0, 0)
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
metadata = path.lstat()
if (
    path.is_symlink()
    or not stat.S_ISREG(metadata.st_mode)
    or metadata.st_uid != 0
    or metadata.st_gid != 0
    or stat.S_IMODE(metadata.st_mode) != 0o600
):
    raise SystemExit(78)
PY
[ -f /var/lib/mooncen-an2p-runtime/bootstrap.lock ] && \
  [ ! -L /var/lib/mooncen-an2p-runtime/bootstrap.lock ] && \
  [ "$(stat -c '%U:%G:%a' /var/lib/mooncen-an2p-runtime/bootstrap.lock)" = \
    root:root:600 ] || die "bootstrap recovery lock is unsafe"
exec 9<>/var/lib/mooncen-an2p-runtime/bootstrap.lock
/usr/bin/flock -x 9
if ! /usr/bin/python3 -I - 9 "$bootstrap_lock" <<'PY'
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
    raise SystemExit(78)
PY
then
  die "bootstrap recovery lock descriptor is unsafe"
fi

# Share the installed ABI lock with every installer and host-transition
# helper.  This recovery worker holds it from the guarded transaction snapshot
# through the atomic installer/helper/trust replacement, closing the pathname
# check-to-replace race.
runtime_install_lock=/var/lib/mooncen-an2p-runtime/install.lock
install -o root -g root -m 0600 /dev/null "$runtime_install_lock" 2>/dev/null || true
[ -f "$runtime_install_lock" ] && [ ! -L "$runtime_install_lock" ] && \
  [ "$(stat -c '%U:%G:%a' "$runtime_install_lock")" = root:root:600 ] || \
  die "runtime installer lock is unsafe during bootstrap"
exec 8<>"$runtime_install_lock"
/usr/bin/flock -x 8
if ! /usr/bin/python3 -I - 8 "$runtime_install_lock" <<'PY'
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
    or descriptor_metadata.st_nlink != 1
    or (descriptor_metadata.st_dev, descriptor_metadata.st_ino)
    != (path_metadata.st_dev, path_metadata.st_ino)
):
    raise SystemExit(78)
PY
then
  die "runtime installer lock descriptor is unsafe during bootstrap"
fi
refuse_pending_runtime_transactions

drain_retained_privileged_processes() {
  local legacy_uid=$1
  shift
  [ "$#" -gt 0 ] || return 0
  /usr/bin/python3 -I - "$legacy_uid" "$@" <<'PY'
import dataclasses
import os
import pathlib
import signal
import sys
import time
from collections.abc import Iterable


class BoundaryError(RuntimeError):
    pass


@dataclasses.dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    start_time: int
    handle: int


class LinuxProcessOps:
    def __init__(self, proc_root: pathlib.Path = pathlib.Path("/proc")) -> None:
        self.proc_root = proc_root

    def _identity(self, pid: int) -> tuple[int, frozenset[int], int, str]:
        directory = self.proc_root / str(pid)
        with (directory / "status").open("rb", buffering=0) as stream:
            status = stream.read(65537)
        if len(status) > 65536:
            raise BoundaryError("process status exceeded the reviewed bound")
        real_uid = None
        groups = None
        for line in status.splitlines():
            if line.startswith(b"Uid:"):
                fields = line.split()
                if len(fields) != 5:
                    raise BoundaryError("process UID record is malformed")
                real_uid = int(fields[1])
            elif line.startswith(b"Groups:"):
                groups = frozenset(int(field) for field in line.split()[1:])
        if real_uid is None or groups is None:
            raise BoundaryError("process credential record is incomplete")
        with (directory / "stat").open("rb", buffering=0) as stream:
            process_stat = stream.read(65537)
        if len(process_stat) > 65536:
            raise BoundaryError("process stat record exceeded the reviewed bound")
        suffix_at = process_stat.rfind(b") ")
        suffix = process_stat[suffix_at + 2 :].split() if suffix_at >= 0 else []
        if len(suffix) < 20:
            raise BoundaryError("process stat record is malformed")
        state = suffix[0].decode("ascii")
        if len(state) != 1:
            raise BoundaryError("process state record is malformed")
        return real_uid, groups, int(suffix[19]), state

    def snapshot(self, uid: int, privileged_gids: frozenset[int]) -> list[ProcessIdentity]:
        identities: list[ProcessIdentity] = []
        try:
            entries = sorted(
                (entry for entry in os.scandir(self.proc_root) if entry.name.isdecimal()),
                key=lambda entry: int(entry.name),
            )
        except OSError as error:
            raise BoundaryError("cannot enumerate process credentials") from error
        for entry in entries:
            pid = int(entry.name)
            handle = -1
            try:
                before = self._identity(pid)
                if (
                    before[0] != uid
                    or before[3] in {"Z", "X", "x"}
                    or not before[1].intersection(privileged_gids)
                ):
                    continue
                handle = os.pidfd_open(pid, 0)
                after = self._identity(pid)
                if after != before:
                    os.close(handle)
                    continue
                identities.append(ProcessIdentity(pid, after[2], handle))
            except (FileNotFoundError, ProcessLookupError):
                if handle >= 0:
                    os.close(handle)
            except PermissionError as error:
                if handle >= 0:
                    os.close(handle)
                raise BoundaryError("cannot inspect a retained privileged process") from error
        return identities

    def still_privileged(
        self,
        identity: ProcessIdentity,
        uid: int,
        privileged_gids: frozenset[int],
    ) -> bool:
        try:
            real_uid, groups, start_time, state = self._identity(identity.pid)
        except (FileNotFoundError, ProcessLookupError):
            return False
        return (
            real_uid == uid
            and start_time == identity.start_time
            and state not in {"Z", "X", "x"}
            and bool(groups.intersection(privileged_gids))
        )

    def send(self, identity: ProcessIdentity, requested_signal: signal.Signals) -> None:
        try:
            signal.pidfd_send_signal(identity.handle, requested_signal)
        except ProcessLookupError:
            return
        except PermissionError as error:
            raise BoundaryError("cannot signal a retained privileged process") from error

    @staticmethod
    def close(identity: ProcessIdentity) -> None:
        os.close(identity.handle)

    @staticmethod
    def sleep(seconds: float) -> None:
        time.sleep(seconds)


def wait_for_clear(
    ops: LinuxProcessOps,
    identities: Iterable[ProcessIdentity],
    uid: int,
    privileged_gids: frozenset[int],
    timeout: float,
) -> list[ProcessIdentity]:
    remaining = list(identities)
    deadline = time.monotonic() + timeout
    while remaining:
        survivors: list[ProcessIdentity] = []
        for identity in remaining:
            if ops.still_privileged(identity, uid, privileged_gids):
                survivors.append(identity)
            else:
                ops.close(identity)
        remaining = survivors
        if not remaining or time.monotonic() >= deadline:
            break
        ops.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
    return remaining


def drain_retained_processes(
    ops: LinuxProcessOps,
    uid: int,
    privileged_gids: frozenset[int],
    *,
    stop_grace: float = 0.25,
    kill_grace: float = 5.0,
    rounds: int = 3,
) -> None:
    for attempt in range(rounds):
        identities = ops.snapshot(uid, privileged_gids)
        if not identities:
            return
        for identity in identities:
            ops.send(identity, signal.SIGSTOP)
        ops.sleep(stop_grace)
        for identity in identities:
            ops.send(identity, signal.SIGKILL)
        identities = wait_for_clear(ops, identities, uid, privileged_gids, kill_grace)
        if identities and attempt + 1 == rounds:
            pids = ",".join(str(identity.pid) for identity in identities)
            for identity in identities:
                ops.close(identity)
            raise BoundaryError(
                f"retained privileged processes did not exit within the bounded drain: {pids}"
            )
        for identity in identities:
            ops.close(identity)
    identities = ops.snapshot(uid, privileged_gids)
    if not identities:
        return
    pids = ",".join(str(identity.pid) for identity in identities)
    for identity in identities:
        ops.close(identity)
    raise BoundaryError(f"retained privileged process drain did not converge: {pids}")


def main() -> int:
    if os.geteuid() != 0 or len(sys.argv) < 3:
        raise BoundaryError("retained process drain requires root, one UID, and one or more GIDs")
    try:
        uid = int(sys.argv[1], 10)
        privileged_gids = frozenset(int(value, 10) for value in sys.argv[2:])
    except ValueError as error:
        raise BoundaryError("retained process drain identity is invalid") from error
    if uid <= 0 or not privileged_gids or any(gid <= 0 for gid in privileged_gids):
        raise BoundaryError("retained process drain identity is outside the reviewed range")
    drain_retained_processes(LinuxProcessOps(), uid, privileged_gids)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BoundaryError as error:
        print(f"an2p bootstrap retained-process boundary: {error}", file=sys.stderr)
        raise SystemExit(78) from error
PY
}

quarantine_legacy_user_state() {
  local legacy_home=$1 legacy_uid=$2 legacy_gid=$3
  local unit_quarantine=$4 credential_quarantine=$5
  /usr/bin/python3 -I - "$legacy_home" "$legacy_uid" "$legacy_gid" \
    "$unit_quarantine" "$credential_quarantine" <<'PY'
import dataclasses
import errno
import os
import pathlib
import stat
import sys
from collections.abc import Callable


ROOT_UID = 0
ROOT_GID = 0
OPEN_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
OPEN_REGULAR = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
LEGACY_UNITS = (
    "mooncen-ops-control-env.service",
    "mooncen-ops-db-tunnel.service",
    "mooncen-ops-api.service",
    "mooncen-deployment-worker.service",
    "mooncen-docker-dev.service",
    "mooncen-ops-console.service",
    "mooncen-status-agent.service",
)
LEGACY_CREDENTIALS = (
    ("config", "cloud-deploy.ssh_config", "cloud-deploy.ssh_config"),
    ("keys", "cloud-deploy-ed25519", "keys_cloud-deploy-ed25519"),
    ("config", "ops-api.env", "ops-api.env"),
    ("config", "deployment-worker.env", "deployment-worker.env"),
)


class BoundaryError(RuntimeError):
    pass


def mount_id(descriptor: int) -> int:
    path = f"/proc/self/fdinfo/{descriptor}"
    fdinfo = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        payload = os.read(fdinfo, 65537)
        if len(payload) > 65536:
            raise BoundaryError("descriptor metadata exceeded the reviewed bound")
    finally:
        os.close(fdinfo)
    for line in payload.splitlines():
        if line.startswith(b"mnt_id:"):
            fields = line.split()
            if len(fields) != 2:
                break
            try:
                value = int(fields[1], 10)
            except ValueError:
                break
            if value > 0:
                return value
    raise BoundaryError("descriptor mount identity is unavailable")


def safe_directory_mode(mode: int) -> bool:
    return (
        mode & 0o700 == 0o700
        and mode & 0o002 == 0
        and mode & 0o7000 == 0
    )


def exact_mode(*expected: int) -> Callable[[int], bool]:
    allowed = frozenset(expected)
    return lambda mode: mode in allowed


@dataclasses.dataclass
class DirectoryRef:
    descriptor: int
    parent: int | None
    name: str | None
    uid: int
    gid: int
    mode_check: Callable[[int], bool]
    label: str
    device: int
    inode: int
    mount: int


class DirectoryTree:
    def __init__(
        self,
        anchor: pathlib.Path,
        uid: int,
        gid: int,
        mode_check: Callable[[int], bool],
        label: str,
    ) -> None:
        self.anchor = anchor
        self.refs: list[DirectoryRef] = []
        descriptor = -1
        try:
            descriptor = os.open(anchor, OPEN_DIRECTORY)
            metadata = os.fstat(descriptor)
            self._validate(metadata, uid, gid, mode_check, label)
            self.refs.append(
                DirectoryRef(
                    descriptor,
                    None,
                    None,
                    uid,
                    gid,
                    mode_check,
                    label,
                    metadata.st_dev,
                    metadata.st_ino,
                    mount_id(descriptor),
                )
            )
            descriptor = -1
            self.recheck()
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    @staticmethod
    def _validate(
        metadata: os.stat_result,
        uid: int,
        gid: int,
        mode_check: Callable[[int], bool],
        label: str,
    ) -> None:
        mode = stat.S_IMODE(metadata.st_mode)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != uid
            or metadata.st_gid != gid
            or not mode_check(mode)
        ):
            raise BoundaryError(f"unsafe directory boundary: {label}")

    @property
    def anchor_mount(self) -> int:
        return self.refs[0].mount

    def open_child(
        self,
        parent: int,
        name: str,
        uid: int,
        gid: int,
        mode_check: Callable[[int], bool],
        label: str,
        *,
        create_mode: int | None = None,
    ) -> int | None:
        if not name or name in {".", ".."} or "/" in name:
            raise BoundaryError(f"invalid directory component: {label}")
        self.recheck()
        parent_descriptor = self.refs[parent].descriptor
        created = False
        try:
            descriptor = os.open(name, OPEN_DIRECTORY, dir_fd=parent_descriptor)
        except FileNotFoundError:
            if create_mode is None:
                return None
            try:
                os.mkdir(name, 0o700, dir_fd=parent_descriptor)
                created = True
            except FileExistsError:
                pass
            descriptor = os.open(name, OPEN_DIRECTORY, dir_fd=parent_descriptor)
        except OSError as error:
            if error.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise BoundaryError(f"symlinked directory boundary: {label}") from error
            raise
        try:
            metadata = os.fstat(descriptor)
            child_mount = mount_id(descriptor)
            if child_mount != self.anchor_mount:
                raise BoundaryError(f"cross-mount directory boundary: {label}")
            mode = stat.S_IMODE(metadata.st_mode)
            # A root:root 0700 directory can only be the residue of this
            # helper's interrupted create. Converge it through the already
            # opened fd; never chown an entry first resolved by pathname.
            root_create_residue = (
                create_mode is not None
                and metadata.st_uid == ROOT_UID
                and metadata.st_gid == ROOT_GID
                and mode == 0o700
            )
            if created or root_create_residue:
                if not root_create_residue:
                    raise BoundaryError(f"unsafe newly created directory: {label}")
                os.fchown(descriptor, uid, gid)
                os.fchmod(descriptor, create_mode)
                metadata = os.fstat(descriptor)
            self._validate(metadata, uid, gid, mode_check, label)
            entry = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
            if (
                not stat.S_ISDIR(entry.st_mode)
                or (entry.st_dev, entry.st_ino) != (metadata.st_dev, metadata.st_ino)
            ):
                raise BoundaryError(f"directory inode changed during open: {label}")
            self.refs.append(
                DirectoryRef(
                    descriptor,
                    parent,
                    name,
                    uid,
                    gid,
                    mode_check,
                    label,
                    metadata.st_dev,
                    metadata.st_ino,
                    child_mount,
                )
            )
            descriptor = -1
            child = len(self.refs) - 1
            self.recheck()
            return child
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def recheck(self) -> None:
        fresh: list[int] = []
        try:
            for index, reference in enumerate(self.refs):
                if reference.parent is None:
                    descriptor = os.open(self.anchor, OPEN_DIRECTORY)
                else:
                    descriptor = os.open(
                        reference.name,
                        OPEN_DIRECTORY,
                        dir_fd=fresh[reference.parent],
                    )
                fresh.append(descriptor)
                metadata = os.fstat(descriptor)
                self._validate(
                    metadata,
                    reference.uid,
                    reference.gid,
                    reference.mode_check,
                    reference.label,
                )
                if (
                    (metadata.st_dev, metadata.st_ino)
                    != (reference.device, reference.inode)
                    or mount_id(descriptor) != reference.mount
                    or reference.mount != self.anchor_mount
                ):
                    raise BoundaryError(
                        f"canonical directory changed during quarantine: {reference.label}"
                    )
                if index == 0 and reference.parent is not None:
                    raise BoundaryError("invalid directory tree anchor")
        except OSError as error:
            if error.errno in {errno.ELOOP, errno.ENOTDIR, errno.ENOENT}:
                raise BoundaryError("canonical directory disappeared during quarantine") from error
            raise
        finally:
            for descriptor in reversed(fresh):
                os.close(descriptor)

    def close(self) -> None:
        for reference in reversed(self.refs):
            os.close(reference.descriptor)
        self.refs.clear()


def lstat_at(directory: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return None


def same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def validate_regular(
    metadata: os.stat_result,
    uid: int,
    gid: int,
    modes: frozenset[int],
    label: str,
) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != uid
        or metadata.st_gid != gid
        or stat.S_IMODE(metadata.st_mode) not in modes
        or metadata.st_nlink != 1
    ):
        raise BoundaryError(f"unsafe regular-file boundary: {label}")


def open_verified_regular(
    tree: DirectoryTree,
    directory_ref: int,
    name: str,
    uid: int,
    gid: int,
    modes: frozenset[int],
    label: str,
) -> int:
    tree.recheck()
    directory = tree.refs[directory_ref].descriptor
    descriptor = os.open(name, OPEN_REGULAR, dir_fd=directory)
    try:
        metadata = os.fstat(descriptor)
        entry = os.stat(name, dir_fd=directory, follow_symlinks=False)
        validate_regular(metadata, uid, gid, modes, label)
        if not same_inode(metadata, entry) or mount_id(descriptor) != tree.anchor_mount:
            raise BoundaryError(f"regular file changed during open: {label}")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def exact_root_mask(directory: int, name: str, metadata: os.stat_result | None) -> bool:
    return bool(
        metadata is not None
        and stat.S_ISLNK(metadata.st_mode)
        and metadata.st_uid == ROOT_UID
        and metadata.st_gid == ROOT_GID
        and os.readlink(name, dir_fd=directory) == "/dev/null"
    )


def converge_unit_destination(
    quarantine: DirectoryTree,
    directory_ref: int,
    name: str,
    legacy_uid: int,
    legacy_gid: int,
) -> None:
    directory = quarantine.refs[directory_ref].descriptor
    metadata = lstat_at(directory, name)
    if metadata is None:
        raise BoundaryError(f"legacy unit quarantine move disappeared: {name}")
    if stat.S_ISREG(metadata.st_mode):
        mode = stat.S_IMODE(metadata.st_mode)
        partial = (
            metadata.st_uid == legacy_uid
            and metadata.st_gid == legacy_gid
            and mode == 0o644
        ) or (
            metadata.st_uid == ROOT_UID
            and metadata.st_gid == ROOT_GID
            and mode in {0o600, 0o644}
        )
        if not partial or metadata.st_nlink != 1:
            raise BoundaryError(f"unsafe legacy unit quarantine residue: {name}")
        descriptor = open_verified_regular(
            quarantine,
            directory_ref,
            name,
            metadata.st_uid,
            metadata.st_gid,
            frozenset({mode}),
            f"unit quarantine {name}",
        )
        try:
            os.fchown(descriptor, ROOT_UID, ROOT_GID)
            os.fchmod(descriptor, 0o600)
            os.fsync(descriptor)
            validate_regular(
                os.fstat(descriptor),
                ROOT_UID,
                ROOT_GID,
                frozenset({0o600}),
                f"unit quarantine {name}",
            )
        finally:
            os.close(descriptor)
    elif stat.S_ISLNK(metadata.st_mode):
        if (metadata.st_uid, metadata.st_gid) not in {
            (legacy_uid, legacy_gid),
            (ROOT_UID, ROOT_GID),
        }:
            raise BoundaryError(f"unsafe legacy unit symlink residue: {name}")
        os.chown(
            name,
            ROOT_UID,
            ROOT_GID,
            dir_fd=directory,
            follow_symlinks=False,
        )
        updated = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if (
            not stat.S_ISLNK(updated.st_mode)
            or updated.st_uid != ROOT_UID
            or updated.st_gid != ROOT_GID
        ):
            raise BoundaryError(f"legacy unit symlink did not converge: {name}")
    else:
        raise BoundaryError(f"unsafe legacy unit quarantine type: {name}")


def quarantine_unit(
    home: DirectoryTree,
    user_units_ref: int,
    quarantine: DirectoryTree,
    quarantine_ref: int,
    name: str,
    legacy_uid: int,
    legacy_gid: int,
) -> None:
    home.recheck()
    quarantine.recheck()
    source_directory = home.refs[user_units_ref].descriptor
    destination_directory = quarantine.refs[quarantine_ref].descriptor
    source = lstat_at(source_directory, name)
    destination = lstat_at(destination_directory, name)
    if exact_root_mask(source_directory, name, source):
        if destination is not None:
            converge_unit_destination(
                quarantine,
                quarantine_ref,
                name,
                legacy_uid,
                legacy_gid,
            )
        return
    if source is not None:
        if destination is not None:
            raise BoundaryError(f"legacy unit quarantine collision: {name}")
        if stat.S_ISREG(source.st_mode):
            descriptor = open_verified_regular(
                home,
                user_units_ref,
                name,
                legacy_uid,
                legacy_gid,
                frozenset({0o644}),
                f"legacy unit {name}",
            )
            os.close(descriptor)
        elif stat.S_ISLNK(source.st_mode):
            if (source.st_uid, source.st_gid) != (legacy_uid, legacy_gid):
                raise BoundaryError(f"unsafe legacy unit symlink: {name}")
        else:
            raise BoundaryError(f"unsafe legacy unit type: {name}")
        home.recheck()
        quarantine.recheck()
        os.rename(
            name,
            name,
            src_dir_fd=source_directory,
            dst_dir_fd=destination_directory,
        )
        home.recheck()
        quarantine.recheck()
        converge_unit_destination(
            quarantine,
            quarantine_ref,
            name,
            legacy_uid,
            legacy_gid,
        )
    elif destination is not None:
        converge_unit_destination(
            quarantine,
            quarantine_ref,
            name,
            legacy_uid,
            legacy_gid,
        )
    try:
        os.symlink("/dev/null", name, dir_fd=source_directory)
    except FileExistsError:
        pass
    mask = lstat_at(source_directory, name)
    if not exact_root_mask(source_directory, name, mask):
        raise BoundaryError(f"legacy user unit mask did not converge: {name}")
    os.fsync(source_directory)
    os.fsync(destination_directory)
    home.recheck()
    quarantine.recheck()


def converge_credential_destination(
    quarantine: DirectoryTree,
    directory_ref: int,
    name: str,
    legacy_uid: int,
    legacy_gid: int,
) -> None:
    directory = quarantine.refs[directory_ref].descriptor
    metadata = lstat_at(directory, name)
    if metadata is None:
        raise BoundaryError(f"legacy credential quarantine move disappeared: {name}")
    partial = (
        metadata.st_uid == legacy_uid
        and metadata.st_gid == legacy_gid
        and stat.S_IMODE(metadata.st_mode) == 0o600
    ) or (
        metadata.st_uid == ROOT_UID
        and metadata.st_gid == ROOT_GID
        and stat.S_IMODE(metadata.st_mode) == 0o600
    )
    if not partial:
        raise BoundaryError(f"unsafe legacy credential quarantine residue: {name}")
    descriptor = open_verified_regular(
        quarantine,
        directory_ref,
        name,
        metadata.st_uid,
        metadata.st_gid,
        frozenset({0o600}),
        f"credential quarantine {name}",
    )
    try:
        os.fchown(descriptor, ROOT_UID, ROOT_GID)
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        validate_regular(
            os.fstat(descriptor),
            ROOT_UID,
            ROOT_GID,
            frozenset({0o600}),
            f"credential quarantine {name}",
        )
    finally:
        os.close(descriptor)


def quarantine_credential(
    home: DirectoryTree,
    source_ref: int | None,
    source_name: str,
    quarantine: DirectoryTree,
    quarantine_ref: int,
    destination_name: str,
    legacy_uid: int,
    legacy_gid: int,
) -> None:
    home.recheck()
    quarantine.recheck()
    source_directory = (
        home.refs[source_ref].descriptor if source_ref is not None else None
    )
    destination_directory = quarantine.refs[quarantine_ref].descriptor
    source = (
        lstat_at(source_directory, source_name)
        if source_directory is not None
        else None
    )
    destination = lstat_at(destination_directory, destination_name)
    if source is not None:
        if destination is not None:
            raise BoundaryError(
                f"legacy credential quarantine collision: {destination_name}"
            )
        descriptor = open_verified_regular(
            home,
            source_ref,
            source_name,
            legacy_uid,
            legacy_gid,
            frozenset({0o600}),
            f"legacy credential {source_name}",
        )
        os.close(descriptor)
        home.recheck()
        quarantine.recheck()
        os.rename(
            source_name,
            destination_name,
            src_dir_fd=source_directory,
            dst_dir_fd=destination_directory,
        )
        home.recheck()
        quarantine.recheck()
        converge_credential_destination(
            quarantine,
            quarantine_ref,
            destination_name,
            legacy_uid,
            legacy_gid,
        )
        os.fsync(source_directory)
        os.fsync(destination_directory)
    elif destination is not None:
        converge_credential_destination(
            quarantine,
            quarantine_ref,
            destination_name,
            legacy_uid,
            legacy_gid,
        )
    home.recheck()
    quarantine.recheck()


def quarantine_state(
    legacy_home: pathlib.Path,
    legacy_uid: int,
    legacy_gid: int,
    unit_quarantine: pathlib.Path,
    credential_quarantine: pathlib.Path,
) -> None:
    home = DirectoryTree(
        legacy_home,
        legacy_uid,
        legacy_gid,
        safe_directory_mode,
        "legacy home",
    )
    quarantine: DirectoryTree | None = None
    try:
        config_ref = home.open_child(
            0,
            ".config",
            legacy_uid,
            legacy_gid,
            safe_directory_mode,
            "legacy config parent",
            create_mode=0o755,
        )
        if config_ref is None:
            raise BoundaryError("legacy config parent was not created")
        systemd_ref = home.open_child(
            config_ref,
            "systemd",
            legacy_uid,
            legacy_gid,
            safe_directory_mode,
            "legacy systemd parent",
            create_mode=0o755,
        )
        if systemd_ref is None:
            raise BoundaryError("legacy systemd parent was not created")
        user_units_ref = home.open_child(
            systemd_ref,
            "user",
            legacy_uid,
            legacy_gid,
            exact_mode(0o755),
            "legacy user unit directory",
            create_mode=0o755,
        )
        if user_units_ref is None:
            raise BoundaryError("legacy user unit directory was not created")

        if unit_quarantine.parent != credential_quarantine.parent:
            raise BoundaryError("legacy quarantine parents differ")
        quarantine = DirectoryTree(
            unit_quarantine.parent,
            ROOT_UID,
            ROOT_GID,
            safe_directory_mode,
            "legacy quarantine parent",
        )
        unit_quarantine_ref = quarantine.open_child(
            0,
            unit_quarantine.name,
            ROOT_UID,
            ROOT_GID,
            exact_mode(0o700),
            "legacy unit quarantine",
            create_mode=0o700,
        )
        credential_quarantine_ref = quarantine.open_child(
            0,
            credential_quarantine.name,
            ROOT_UID,
            ROOT_GID,
            exact_mode(0o700),
            "legacy credential quarantine",
            create_mode=0o700,
        )
        if unit_quarantine_ref is None or credential_quarantine_ref is None:
            raise BoundaryError("legacy quarantine directory was not created")

        for unit in LEGACY_UNITS:
            quarantine_unit(
                home,
                user_units_ref,
                quarantine,
                unit_quarantine_ref,
                unit,
                legacy_uid,
                legacy_gid,
            )

        credential_config_ref = home.open_child(
            config_ref,
            "mooncen-an2p",
            legacy_uid,
            legacy_gid,
            exact_mode(0o700),
            "legacy credential directory",
        )
        credential_keys_ref = None
        if credential_config_ref is not None:
            credential_keys_ref = home.open_child(
                credential_config_ref,
                "keys",
                legacy_uid,
                legacy_gid,
                exact_mode(0o700),
                "legacy credential key directory",
            )
        source_refs = {
            "config": credential_config_ref,
            "keys": credential_keys_ref,
        }
        for source_kind, source_name, destination_name in LEGACY_CREDENTIALS:
            quarantine_credential(
                home,
                source_refs[source_kind],
                source_name,
                quarantine,
                credential_quarantine_ref,
                destination_name,
                legacy_uid,
                legacy_gid,
            )
        home.recheck()
        quarantine.recheck()
    finally:
        if quarantine is not None:
            quarantine.close()
        home.close()


def main() -> int:
    if os.geteuid() != ROOT_UID or len(sys.argv) != 6:
        raise BoundaryError("legacy user quarantine requires root and five arguments")
    legacy_home = pathlib.Path(sys.argv[1])
    unit_quarantine = pathlib.Path(sys.argv[4])
    credential_quarantine = pathlib.Path(sys.argv[5])
    try:
        legacy_uid = int(sys.argv[2], 10)
        legacy_gid = int(sys.argv[3], 10)
    except ValueError as error:
        raise BoundaryError("legacy user identity is invalid") from error
    if (
        legacy_home != pathlib.Path("/home/sgm")
        or legacy_uid <= 0
        or legacy_gid <= 0
        or unit_quarantine
        != pathlib.Path("/var/lib/mooncen-an2p-legacy-user-units")
        or credential_quarantine
        != pathlib.Path("/var/lib/mooncen-an2p-legacy-credentials")
    ):
        raise BoundaryError("legacy user quarantine path is outside the reviewed boundary")
    quarantine_state(
        legacy_home,
        legacy_uid,
        legacy_gid,
        unit_quarantine,
        credential_quarantine,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BoundaryError, OSError) as error:
        print(f"an2p bootstrap user-path boundary: {error}", file=sys.stderr)
        raise SystemExit(78) from error
PY
}

advance_bootstrap_journal() {
  local journal=$1 expected_phase=$2 next_phase=$3
  /usr/bin/python3 -I - "$journal" "$expected_phase" "$next_phase" <<'PY'
import json
import os
import pathlib
import stat
import sys


path = pathlib.Path(sys.argv[1])
expected = sys.argv[2]
next_phase = sys.argv[3]
phases = (
    "prepared",
    "membership_revoked",
    "privileged_processes_drained",
    "native_restored",
    "trust_committed",
)
if expected not in phases or next_phase not in phases:
    raise SystemExit(78)
if phases.index(next_phase) != phases.index(expected) + 1:
    raise SystemExit(78)
metadata = path.lstat()
payload = path.read_bytes()
value = json.loads(payload.decode("ascii"))
canonical = (
    json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))
    .encode("ascii")
    + b"\n"
)
if (
    path.is_symlink()
    or not stat.S_ISREG(metadata.st_mode)
    or metadata.st_uid != 0
    or metadata.st_gid != 0
    or stat.S_IMODE(metadata.st_mode) != 0o600
    or set(value) != {"native_selected", "phase", "schema_version"}
    or value.get("schema_version") != 2
    or type(value.get("native_selected")) is not bool
    or value.get("phase") != expected
    or payload != canonical
):
    raise SystemExit(78)
value["phase"] = next_phase
updated = (
    json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))
    .encode("ascii")
    + b"\n"
)
stage = path.with_name(f".{path.name}.next")
try:
    stage_metadata = stage.lstat()
except FileNotFoundError:
    pass
else:
    if (
        stage.is_symlink()
        or not stat.S_ISREG(stage_metadata.st_mode)
        or stage_metadata.st_uid != 0
        or stage_metadata.st_gid != 0
        or stat.S_IMODE(stage_metadata.st_mode) != 0o600
    ):
        raise SystemExit(78)
    stage.unlink()
descriptor = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
try:
    os.fchown(descriptor, 0, 0)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "wb", closefd=True) as stream:
        descriptor = -1
        stream.write(updated)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(stage, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
finally:
    if descriptor >= 0:
        os.close(descriptor)
    try:
        stage.unlink()
    except FileNotFoundError:
        pass
PY
}

revoke_host_root_without_losing_public_development() {
  local state_root=/var/lib/mooncen-an2p-runtime
  local journal=$state_root/bootstrap-development.json native_selected legacy_user=sgm \
    legacy_home unit_quarantine credential_quarantine unit legacy_uid legacy_gid \
    privileged_group privileged_gid
  local -a privileged_gids=()
  local -a legacy_units=(
    mooncen-ops-control-env.service
    mooncen-ops-db-tunnel.service
    mooncen-ops-api.service
    mooncen-deployment-worker.service
    mooncen-docker-dev.service
    mooncen-ops-console.service
    mooncen-status-agent.service
  )
  install -d -o root -g root -m 0700 "$state_root"
  if [ -e "$journal" ] || [ -L "$journal" ]; then
    IFS=$'\t' read -r native_selected bootstrap_phase < <(
      /usr/bin/python3 -I - "$journal" <<'PY'
import json
import pathlib
import stat
import sys


path = pathlib.Path(sys.argv[1])
metadata = path.lstat()
payload = path.read_bytes()
value = json.loads(payload.decode("ascii"))
canonical = (
    json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))
    .encode("ascii")
    + b"\n"
)
if (
    path.is_symlink()
    or not stat.S_ISREG(metadata.st_mode)
    or metadata.st_uid != 0
    or metadata.st_gid != 0
    or stat.S_IMODE(metadata.st_mode) != 0o600
    or set(value) != {"native_selected", "phase", "schema_version"}
    or value.get("schema_version") != 2
    or type(value.get("native_selected")) is not bool
    or value.get("phase")
    not in {
        "prepared",
        "membership_revoked",
        "privileged_processes_drained",
        "native_restored",
        "trust_committed",
    }
    or payload != canonical
):
    raise SystemExit(78)
print(("true" if value["native_selected"] else "false") + "\t" + value["phase"])
PY
    ) || die "bootstrap development journal is unsafe"
    [ -n "$native_selected" ] && [ -n "$bootstrap_phase" ] || \
      die "bootstrap development journal is incomplete"
  else
    if systemctl --user --machine="${legacy_user}@" is-active --quiet \
      mooncen-api.service && systemctl --user --machine="${legacy_user}@" \
      is-enabled --quiet mooncen-api.service && \
      systemctl --user --machine="${legacy_user}@" is-active --quiet \
      mooncen-frontend.service && systemctl --user --machine="${legacy_user}@" \
      is-enabled --quiet mooncen-frontend.service; then
      native_selected=true
      [ ! -e /etc/mooncen-an2p/docker-development-enabled ] && \
        [ ! -L /etc/mooncen-an2p/docker-development-enabled ] || \
        die "native development overlaps the Docker selection marker"
    elif [ -f /etc/mooncen-an2p/docker-development-enabled ] && \
      [ ! -L /etc/mooncen-an2p/docker-development-enabled ] && \
      [ "$(stat -c '%U:%G:%a:%s' \
        /etc/mooncen-an2p/docker-development-enabled)" = root:root:644:0 ] && \
      systemctl is-active --quiet mooncen-docker-dev.service && \
      systemctl is-enabled --quiet mooncen-docker-dev.service; then
      native_selected=false
    else
      die "public development runtime is not healthy before bootstrap"
    fi
    /usr/bin/python3 -I - "$journal" "$native_selected" <<'PY'
import json
import os
import pathlib
import stat
import sys


path = pathlib.Path(sys.argv[1])
value = {
    "native_selected": sys.argv[2] == "true",
    "phase": "prepared",
    "schema_version": 2,
}
payload = (
    json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))
    .encode("ascii")
    + b"\n"
)
stage = path.with_name(f".{path.name}.create")
try:
    stage_metadata = stage.lstat()
except FileNotFoundError:
    pass
else:
    if (
        stage.is_symlink()
        or not stat.S_ISREG(stage_metadata.st_mode)
        or stage_metadata.st_uid != 0
        or stage_metadata.st_gid != 0
        or stat.S_IMODE(stage_metadata.st_mode) != 0o600
    ):
        raise SystemExit(78)
    stage.unlink()
descriptor = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
try:
    os.fchown(descriptor, 0, 0)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "wb", closefd=True) as stream:
        descriptor = -1
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    if path.exists() or path.is_symlink():
        raise SystemExit(78)
    os.rename(stage, path)
finally:
    if descriptor >= 0:
        os.close(descriptor)
    try:
        stage.unlink()
    except FileNotFoundError:
        pass
directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    os.fsync(directory)
finally:
    os.close(directory)
PY
    bootstrap_phase=prepared
  fi

  id "$legacy_user" >/dev/null 2>&1 || die "legacy operator account is unavailable"
  legacy_home=$(getent passwd "$legacy_user" | cut -d: -f6)
  [ "$legacy_home" = /home/sgm ] || die "legacy operator home is outside the reviewed path"
  legacy_uid=$(id -u "$legacy_user")
  legacy_gid=$(id -g "$legacy_user")
  [[ "$legacy_uid" =~ ^[0-9]+$ ]] && [ "$legacy_uid" -gt 0 ] && \
    [[ "$legacy_gid" =~ ^[0-9]+$ ]] && [ "$legacy_gid" -gt 0 ] || \
    die "legacy operator identity is invalid"
  unit_quarantine=/var/lib/mooncen-an2p-legacy-user-units
  credential_quarantine=/var/lib/mooncen-an2p-legacy-credentials

  # This mask lives under root-controlled /etc/systemd/user. Install it before
  # touching the old process set so no user unit can be activated while the
  # retained host-root boundary is being drained. Do not ask the old user
  # manager to stop anything here: an ExecStop or TERM handler would otherwise
  # run while it still retained docker/lxd.
  systemctl --global mask "${legacy_units[@]}" >/dev/null
  for privileged_group in docker lxd; do
    getent group "$privileged_group" >/dev/null || continue
    privileged_gid=$(getent group "$privileged_group" | cut -d: -f3)
    [[ "$privileged_gid" =~ ^[0-9]+$ ]] && [ "$privileged_gid" -gt 0 ] || \
      die "privileged group identity is invalid: $privileged_group"
    privileged_gids+=("$privileged_gid")
    if id -nG "$legacy_user" | tr ' ' '\n' | grep -Fxq "$privileged_group"; then
      /usr/bin/gpasswd --delete "$legacy_user" "$privileged_group" >/dev/null
    fi
    ! id -nG "$legacy_user" | tr ' ' '\n' | grep -Fxq "$privileged_group" || \
      die "legacy operator membership was not revoked: $privileged_group"
  done
  if [ "$bootstrap_phase" = prepared ]; then
    advance_bootstrap_journal "$journal" prepared membership_revoked || \
      die "bootstrap membership-revoked checkpoint failed"
    bootstrap_phase=membership_revoked
  fi
  /usr/bin/loginctl enable-linger "$legacy_user"
  # Never terminate the account wholesale from this restartable worker. New
  # SSH/VS Code processes created after the NSS membership change have clean
  # supplementary groups and must survive a crash/retry. pidfds bind every
  # signal to the exact old process identity. SIGSTOP prevents an untrusted
  # host-root-capable process from handling TERM or forking during a grace
  # period; a bounded SIGKILL/rescan then removes the /proc PID-reuse race that
  # amplified the 2026-08-20 incident.
  drain_retained_privileged_processes "$legacy_uid" "${privileged_gids[@]}"
  for privileged_group in docker lxd; do
    getent group "$privileged_group" >/dev/null || continue
    ! id -nG "$legacy_user" | tr ' ' '\n' | grep -Fxq "$privileged_group" || \
      die "legacy operator membership returned during process drain: $privileged_group"
  done
  if [ "$bootstrap_phase" = membership_revoked ]; then
    advance_bootstrap_journal "$journal" membership_revoked \
      privileged_processes_drained || \
      die "bootstrap privileged-process checkpoint failed"
    bootstrap_phase=privileged_processes_drained
  fi

  # No root pathname operation below follows a component owned by sgm. The
  # bounded helper opens each component relative to an already verified dirfd,
  # refuses symlinks and mount crossings, reopens the canonical inode chain,
  # and mutates only an opened/moved single-link inode in root quarantine.
  quarantine_legacy_user_state "$legacy_home" "$legacy_uid" "$legacy_gid" \
    "$unit_quarantine" "$credential_quarantine"

  systemctl start "user@${legacy_uid}.service"
  systemctl --user --machine="${legacy_user}@" daemon-reload
  systemctl --user --machine="${legacy_user}@" disable --now \
    "${legacy_units[@]}" >/dev/null 2>&1 || true
  for unit in "${legacy_units[@]}"; do
    ! systemctl --user --machine="${legacy_user}@" is-active --quiet "$unit" && \
      ! systemctl --user --machine="${legacy_user}@" is-enabled --quiet "$unit" || \
      die "legacy control service survived clean-manager quarantine: $unit"
  done
  if [ "$native_selected" = true ]; then
    # Restore only the two services proven active+enabled in the captured
    # legacy prestate. The reviewed target is installed by phase one; making
    # bootstrap recovery depend on that not-yet-installed unit caused the
    # second failure loop in the 2026-08-20 incident.
    for unit in mooncen-api.service mooncen-frontend.service; do
      systemctl --user --machine="${legacy_user}@" is-enabled --quiet "$unit" || \
        die "captured native service is no longer enabled: $unit"
      systemctl --user --machine="${legacy_user}@" start "$unit" || \
        die "captured native service did not restart: $unit"
    done
    verify_native_public_health || die "native development runtime did not recover"
  else
    systemctl is-active --quiet mooncen-docker-dev.service && \
      systemctl is-enabled --quiet mooncen-docker-dev.service || \
      die "Docker development runtime stopped during bootstrap"
  fi
  for unit in "${legacy_units[@]}"; do
    ! systemctl --user --machine="${legacy_user}@" is-active --quiet "$unit" && \
      ! systemctl --user --machine="${legacy_user}@" is-enabled --quiet "$unit" || \
      die "legacy control service restarted during bootstrap: $unit"
  done
  if [ "$bootstrap_phase" = privileged_processes_drained ]; then
    advance_bootstrap_journal "$journal" privileged_processes_drained \
      native_restored || die "bootstrap native-restored checkpoint failed"
    bootstrap_phase=native_restored
  fi
}

preflight_bootstrap_inputs_and_public_runtime
revoke_host_root_without_losing_public_development

[ -f "$source_installer" ] && [ ! -L "$source_installer" ] &&
  [ "$(stat -c '%U:%G:%a' "$source_installer")" = root:root:700 ] ||
  die "root recovery installer source is unavailable or unsafe"
[ "$(sha256sum "$source_installer" | cut -d' ' -f1)" = "$installer_sha" ] ||
  die "root recovery installer source digest mismatch"
[ -f "$source_host_transition" ] && [ ! -L "$source_host_transition" ] &&
  [ "$(stat -c '%U:%G:%a' "$source_host_transition")" = root:root:700 ] ||
  die "root recovery host transition source is unavailable or unsafe"
[ "$(sha256sum "$source_host_transition" | cut -d' ' -f1)" = \
  "$host_transition_sha" ] ||
  die "root recovery host transition source digest mismatch"

install -d -o root -g root -m 0755 /usr/local/sbin /usr/local/libexec
installer_stage=$(mktemp /usr/local/sbin/.mooncen-an2p-runtime-install.XXXXXXXX)
host_transition_stage=$(mktemp /usr/local/libexec/.mooncen-an2p-host-transition.XXXXXXXX)
trust_stage=
cleanup() {
  rm -f -- "$installer_stage"
  rm -f -- "$host_transition_stage"
  [ -z "$trust_stage" ] || rm -f -- "$trust_stage"
}
trap cleanup EXIT
install -o root -g root -m 0755 "$source_installer" "$installer_stage"
[ "$(sha256sum "$installer_stage" | cut -d' ' -f1)" = "$installer_sha" ] ||
  die "staged installer digest mismatch"
sync -f -- "$installer_stage"
install -o root -g root -m 0755 "$source_host_transition" "$host_transition_stage"
[ "$(sha256sum "$host_transition_stage" | cut -d' ' -f1)" = \
  "$host_transition_sha" ] ||
  die "staged host transition digest mismatch"
sync -f -- "$host_transition_stage"

install -d -o root -g root -m 0755 "$trust_directory"
trust_stage=$(mktemp "$trust_directory/.runtime-installer.trust.XXXXXXXX")
{
  printf 'VERSION=1\n'
  printf 'INSTALLER_SHA256=%s\n' "$installer_sha"
  printf 'INTEGRITY_SHA256=%s\n' "$integrity_sha"
  printf 'CLEAN_SOURCE_SHA256=%s\n' "$clean_source_sha"
  printf 'PAIR_MANAGER_SHA256=%s\n' "$pair_manager_sha"
  printf 'HANDOFF_SHA256=%s\n' "$handoff_sha"
  printf 'REGISTRAR_SHA256=%s\n' "$registrar_sha"
  printf 'HOST_TRANSITION_SHA256=%s\n' "$host_transition_sha"
  printf 'EXPECTED_BUILD_POLICY_SHA256=%s\n' "$build_policy_sha"
} >"$trust_stage"
chown root:root "$trust_stage"
chmod 0600 "$trust_stage"
sync -f -- "$trust_stage"

mv -fT -- "$installer_stage" "$target_installer"
installer_stage=
mv -fT -- "$host_transition_stage" "$target_host_transition"
host_transition_stage=
mv -fT -- "$trust_stage" "$trust_target"
trust_stage=
sync -f -- /usr/local/sbin
sync -f -- /usr/local/libexec
sync -f -- "$trust_directory"
[ "$(stat -c '%U:%G:%a' "$target_installer")" = root:root:755 ] &&
  [ "$(sha256sum "$target_installer" | cut -d' ' -f1)" = "$installer_sha" ] &&
  [ "$(stat -c '%U:%G:%a' "$target_host_transition")" = root:root:755 ] &&
  [ "$(sha256sum "$target_host_transition" | cut -d' ' -f1)" = \
    "$host_transition_sha" ] &&
  [ "$(stat -c '%U:%G:%a' "$trust_target")" = root:root:600 ] ||
  die "root installer bootstrap did not converge"

journal=/var/lib/mooncen-an2p-runtime/bootstrap-development.json
if [ "$bootstrap_phase" = native_restored ]; then
  advance_bootstrap_journal "$journal" native_restored trust_committed ||
    die "bootstrap trust-committed checkpoint failed"
  bootstrap_phase=trust_committed
fi
[ "$bootstrap_phase" = trust_committed ] ||
  die "bootstrap reached trust commit from an invalid journal phase"

# All privilege, public-health, quarantine, and trust postconditions are now
# durable. Disable boot recovery only at this final commit point. The launcher
# removes the reviewed unit file after systemctl observes this successful exit.
systemctl disable "$recovery_unit_name" >/dev/null
rm -- "$journal"
sync -f -- /var/lib/mooncen-an2p-runtime

printf '%s\n' '{"installed":true,"schema_version":1}'
