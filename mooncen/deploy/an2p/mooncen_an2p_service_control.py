#!/usr/bin/env python3
"""Fail-safe, fixed runtime selector for the an2p developer host."""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Sequence


DOCKER_UNIT = "mooncen-docker-dev.service"
NATIVE_UNITS = ("mooncen-api.service", "mooncen-frontend.service")
LEGACY_USER = "sgm"
MARKER_DIRECTORY = Path("/etc/mooncen-an2p")
DOCKER_MARKER = MARKER_DIRECTORY / "docker-development-enabled"
SELECTION_LOCK = Path("/run/mooncen-an2p-runtime-selection.lock")
MANAGER_OPERATION_LOCK = Path("/var/lib/mooncen-an2p-runtime/operation.lock")
MANAGER_JOURNAL = Path("/var/lib/mooncen-an2p-runtime/transaction.json")
MANAGER_LOCK_FD_ENV = "MOONCEN_AN2P_MANAGER_LOCK_FD"
SYSTEMCTL = "/bin/systemctl"
LXC = "/usr/sbin/lxc"
HEALTH_URLS = ("http://127.0.0.1:8001/health", "http://127.0.0.1:5174")
SELECT_ACTIONS = frozenset({"docker-select", "native-select"})
LXD_ACTIONS = {
    "lxd-db-start": ("start", "mooncen-dev-db"),
    "lxd-db-stop": ("stop", "mooncen-dev-db"),
    "lxd-db-status": ("list", "mooncen-dev-db", "--format=json"),
}
LXD_CONFIG_KEYS = frozenset({"boot.autostart", "boot.autostart.priority"})
LXD_DEVICE_CONTRACT = {
    "eth0": {"name": "eth0", "network": "lxdbr0", "type": "nic"},
    "postgres": {
        "connect": "tcp:127.0.0.1:5432",
        "listen": "tcp:127.0.0.1:5432",
        "type": "proxy",
    },
    "root": {"path": "/", "pool": "default", "type": "disk"},
}


class ControlError(RuntimeError):
    """Raised when the fixed selector cannot prove a safe outcome."""


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Keep selector health checks on their exact reviewed loopback URL."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        del req, fp, code, msg, headers, newurl
        return None


def _health_response_ready(response: object, expected_url: str) -> bool:
    if response.status != 200 or response.geturl() != expected_url:  # type: ignore[attr-defined]
        return False
    body = response.read(1024 * 1024 + 1)  # type: ignore[attr-defined]
    if len(body) > 1024 * 1024:
        return False
    if expected_url.endswith("/health"):
        return (
            response.headers.get_content_type() == "application/json"  # type: ignore[attr-defined]
            and body == b'{"status":"ready"}'
        )
    return response.headers.get_content_type() == "text/html" and bool(body)  # type: ignore[attr-defined]


def _canonical_json(value: dict[str, object]) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def command_for(arguments: Sequence[str]) -> tuple[str, ...]:
    """Return the exact external argv for non-selection fixed actions."""
    values = tuple(arguments)
    if len(values) != 1:
        raise ControlError("expected exactly one fixed runtime control action")
    action = values[0]
    if action in LXD_ACTIONS:
        return (LXC, *LXD_ACTIONS[action])
    if action == "docker-reload":
        return (SYSTEMCTL, "reload", DOCKER_UNIT)
    if action in SELECT_ACTIONS or action == "runtime-status":
        return ()
    raise ControlError("expected exactly one fixed runtime control action")


def _run(
    argv: Sequence[str], *, check: bool = True
) -> subprocess.CompletedProcess[bytes]:
    completed = subprocess.run(  # noqa: S603 - every caller supplies fixed argv.
        tuple(argv),
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={
            "HOME": "/root",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        },
        timeout=1860,
    )
    if check and completed.returncode != 0:
        raise ControlError("fixed runtime control command failed")
    return completed


def _valid_idmap(value: str) -> bool:
    try:
        records = json.loads(value)
    except (TypeError, ValueError):
        return False
    if not isinstance(records, list) or len(records) != 2:
        return False
    expected = {(True, False), (False, True)}
    actual: set[tuple[bool, bool]] = set()
    for record in records:
        if not isinstance(record, dict):
            return False
        normalized = {str(key).lower(): item for key, item in record.items()}
        if frozenset(normalized) != {
            "hostid",
            "isgid",
            "isuid",
            "maprange",
            "nsid",
        }:
            return False
        if (
            normalized["hostid"] != 1_000_000
            or normalized["nsid"] != 0
            or normalized["maprange"] != 1_000_000_000
            or type(normalized["isuid"]) is not bool
            or type(normalized["isgid"]) is not bool
        ):
            return False
        actual.add((normalized["isuid"], normalized["isgid"]))
    return actual == expected


def _validate_lxd_database() -> None:
    result = _run(
        (LXC, "query", "/1.0/instances/mooncen-dev-db?recursion=1")
    )
    if not result.stdout or len(result.stdout) > 1024 * 1024:
        raise ControlError("LXD database configuration is unavailable")
    try:
        value = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ControlError("LXD database configuration is invalid") from exc
    if (
        not isinstance(value, dict)
        or value.get("name") != "mooncen-dev-db"
        or value.get("type") != "container"
        or value.get("architecture") != "x86_64"
        or value.get("ephemeral") is not False
        or value.get("stateful") is not False
        or value.get("profiles") != ["default"]
        or value.get("expanded_devices") != LXD_DEVICE_CONTRACT
        or not isinstance(value.get("expanded_config"), dict)
    ):
        raise ControlError("LXD database boundary drifted")
    config = value["expanded_config"]
    for key, item in config.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise ControlError("LXD database configuration type drifted")
        if key in LXD_CONFIG_KEYS:
            continue
        if key.startswith("image.") or key.startswith("volatile."):
            continue
        raise ControlError("LXD database configuration contains an unsafe key")
    if (
        config.get("boot.autostart") != "true"
        or config.get("boot.autostart.priority") != "20"
        or config.get("volatile.idmap.base") != "0"
    ):
        raise ControlError("LXD database boot or idmap boundary drifted")
    for key in ("volatile.idmap.current", "volatile.idmap.next"):
        if not _valid_idmap(config.get(key, "")):
            raise ControlError("LXD database idmap boundary drifted")
    last_state_idmap = config.get("volatile.last_state.idmap", "[]")
    if last_state_idmap != "[]" and not _valid_idmap(last_state_idmap):
        raise ControlError("LXD database last-state idmap boundary drifted")
    base_image = config.get("volatile.base_image", "")
    if base_image and re.fullmatch(r"[0-9a-f]{64}", base_image) is None:
        raise ControlError("LXD database image identity drifted")


def _systemctl(
    *arguments: str,
    user: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    prefix = (SYSTEMCTL,)
    if user:
        prefix += ("--user", f"--machine={LEGACY_USER}@")
    return _run((*prefix, *arguments), check=check)


def _unit_flag(unit: str, property_name: str, *, user: bool = False) -> bool:
    load = _systemctl(
        "show",
        unit,
        "--property=LoadState",
        "--value",
        user=user,
    ).stdout
    if load != b"loaded\n":
        raise ControlError("runtime unit is not loaded")
    if property_name == "active":
        payload = _systemctl(
            "show",
            unit,
            "--property=ActiveState",
            "--value",
            user=user,
        ).stdout
        if payload == b"active\n":
            return True
        if payload == b"inactive\n":
            return False
    elif property_name == "enabled":
        payload = _systemctl(
            "show",
            unit,
            "--property=UnitFileState",
            "--value",
            user=user,
        ).stdout
        if payload == b"enabled\n":
            return True
        if payload == b"disabled\n":
            return False
    else:  # pragma: no cover - internal programming error.
        raise AssertionError(property_name)
    raise ControlError("runtime unit state is indeterminate")


def _safe_marker_directory() -> None:
    try:
        metadata = MARKER_DIRECTORY.lstat()
    except OSError as exc:
        raise ControlError("runtime marker directory is unavailable") from exc
    if (
        MARKER_DIRECTORY.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o755
    ):
        raise ControlError("runtime marker directory is unsafe")


def _marker_present() -> bool:
    _safe_marker_directory()
    if not DOCKER_MARKER.exists() and not DOCKER_MARKER.is_symlink():
        return False
    metadata = DOCKER_MARKER.lstat()
    if (
        DOCKER_MARKER.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o644
        or metadata.st_size != 0
    ):
        raise ControlError("runtime marker is unsafe")
    return True


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _set_marker(present: bool) -> None:
    existing = _marker_present()
    if existing == present:
        return
    if present:
        stage = MARKER_DIRECTORY / f".docker-development-enabled.{os.getpid()}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        descriptor = os.open(stage, flags, 0o644)
        try:
            os.fchown(descriptor, 0, 0)
            os.fchmod(descriptor, 0o644)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(stage, DOCKER_MARKER)
    else:
        DOCKER_MARKER.unlink()
    _sync_directory(MARKER_DIRECTORY)
    if _marker_present() != present:
        raise ControlError("runtime marker did not converge")


def _wait_for_health(timeout_seconds: int = 240) -> None:
    deadline = time.monotonic() + timeout_seconds
    pending = set(HEALTH_URLS)
    # The selector can be called from a user-facing sudo wrapper as well as an
    # env-isolated system service.  Never let inherited proxy variables turn a
    # loopback cutover check into a remote/proxy success response.
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _RejectRedirects(),
    )
    while pending and time.monotonic() < deadline:
        for url in tuple(pending):
            try:
                request = urllib.request.Request(url, method="GET")
                with opener.open(  # noqa: S310 - fixed loopback URLs, proxies disabled.
                    request,
                    timeout=3,
                ) as response:
                    if _health_response_ready(response, url):
                        pending.remove(url)
            except (OSError, urllib.error.URLError):
                pass
        if pending:
            time.sleep(1)
    if pending:
        raise ControlError("selected development runtime did not become healthy")


def _native_inactive() -> None:
    for unit in NATIVE_UNITS:
        if _unit_flag(unit, "active", user=True):
            raise ControlError("native development runtime remains active")


def _native_enabled(expected: bool) -> None:
    for unit in NATIVE_UNITS:
        if _unit_flag(unit, "enabled", user=True) != expected:
            raise ControlError("native development runtime enablement drifted")


def _select_docker() -> None:
    # The marker first blocks every future native ExecCondition. Any later
    # failure intentionally converges to downtime, never overlapping ports.
    _set_marker(True)
    _systemctl("disable", "--now", *NATIVE_UNITS, user=True)
    _systemctl("reset-failed", *NATIVE_UNITS, user=True, check=False)
    _native_inactive()
    _native_enabled(False)
    _systemctl("enable", "--now", DOCKER_UNIT)
    if not _unit_flag(DOCKER_UNIT, "enabled") or not _unit_flag(
        DOCKER_UNIT,
        "active",
    ):
        raise ControlError("Docker development runtime did not converge")
    _native_inactive()
    _wait_for_health()


def _select_native() -> None:
    # Docker is proven down before the native-start fence is removed.
    _systemctl("disable", "--now", DOCKER_UNIT)
    _systemctl("reset-failed", DOCKER_UNIT, check=False)
    if _unit_flag(DOCKER_UNIT, "enabled") or _unit_flag(DOCKER_UNIT, "active"):
        raise ControlError("Docker development runtime remains active")
    _set_marker(False)
    _systemctl("enable", "--now", *NATIVE_UNITS, user=True)
    _native_enabled(True)
    for unit in NATIVE_UNITS:
        if not _unit_flag(unit, "active", user=True):
            raise ControlError("native development runtime did not converge")
    if _unit_flag(DOCKER_UNIT, "active"):
        raise ControlError("Docker development runtime restarted unexpectedly")
    _wait_for_health()


def _status() -> dict[str, object]:
    return {
        "docker_active": _unit_flag(DOCKER_UNIT, "active"),
        "docker_enabled": _unit_flag(DOCKER_UNIT, "enabled"),
        "marker": _marker_present(),
        "native_active": [
            unit for unit in NATIVE_UNITS if _unit_flag(unit, "active", user=True)
        ],
        "native_enabled": [
            unit for unit in NATIVE_UNITS if _unit_flag(unit, "enabled", user=True)
        ],
        "schema_version": 1,
    }


def _locked_selection(action: str) -> None:
    manager_descriptor = _manager_operation_fence()
    try:
        descriptor = os.open(
            SELECTION_LOCK,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
        )
        try:
            os.fchown(descriptor, 0, 0)
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            if action == "docker-select":
                _select_docker()
            else:
                _select_native()
        finally:
            os.close(descriptor)
    finally:
        # Never issue LOCK_UN here: an inherited descriptor shares the
        # manager/installer's open file description. Closing only this child
        # copy preserves the parent's transaction fence.
        os.close(manager_descriptor)


def _manager_operation_fence() -> int:
    inherited = os.environ.get(MANAGER_LOCK_FD_ENV)
    if inherited is not None:
        if re.fullmatch(r"[1-9][0-9]*", inherited) is None or int(inherited) < 3:
            raise ControlError("inherited runtime transaction fence is invalid")
        descriptor = int(inherited)
        try:
            metadata = os.fstat(descriptor)
            path_metadata = MANAGER_OPERATION_LOCK.lstat()
        except OSError as exc:
            raise ControlError("inherited runtime transaction fence is invalid") from exc
        if (
            MANAGER_OPERATION_LOCK.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or not stat.S_ISREG(path_metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_gid != os.getegid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or (metadata.st_dev, metadata.st_ino)
            != (path_metadata.st_dev, path_metadata.st_ino)
        ):
            raise ControlError("inherited runtime transaction fence is unsafe")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ControlError("inherited runtime transaction fence is not held") from exc
        return descriptor

    descriptor = os.open(
        MANAGER_OPERATION_LOCK,
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    try:
        os.fchown(descriptor, 0, 0)
        os.fchmod(descriptor, 0o600)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_gid != os.getegid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise ControlError("runtime transaction fence is unsafe")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        if MANAGER_JOURNAL.exists() or MANAGER_JOURNAL.is_symlink():
            raise ControlError("runtime pair transaction blocks manual selection")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _locked_status() -> dict[str, object]:
    descriptor = os.open(
        SELECTION_LOCK,
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    try:
        os.fchown(descriptor, 0, 0)
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_SH)
        return _status()
    finally:
        os.close(descriptor)


def main(argv: Sequence[str] | None = None) -> int:
    if os.geteuid() != 0:
        raise ControlError("an2p service control requires root")
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        raise ControlError("expected exactly one fixed runtime control action")
    action = arguments[0]
    if action in SELECT_ACTIONS:
        _locked_selection(action)
        print(_canonical_json(_locked_status()))
        return 0
    if action == "runtime-status":
        print(_canonical_json(_locked_status()))
        return 0
    if action == "docker-reload":
        status = _locked_status()
        if (
            status["marker"] is not True
            or status["docker_active"] is not True
            or status["native_active"]
        ):
            raise ControlError("Docker reload requires exclusive Docker selection")
    if action in {"lxd-db-start", "lxd-db-status"}:
        _validate_lxd_database()
    command = command_for(arguments)
    completed = _run(command)
    if completed.stdout:
        sys.stdout.buffer.write(completed.stdout)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ControlError, OSError) as exc:
        print(f"an2p service control rejected: {exc}", file=sys.stderr)
        raise SystemExit(64) from None
