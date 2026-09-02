#!/usr/bin/env python3
"""Atomically select one reviewed an2p control/Docker runtime pair."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
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
from typing import Any, Sequence


PAIR_ROOT = Path("/opt/mooncen-an2p-runtime")
PAIR_RELEASES = PAIR_ROOT / "releases"
PAIR_CURRENT = PAIR_ROOT / "current"
CONTROL_ALIAS_ROOT = Path("/opt/mooncen-an2p-control")
DOCKER_ALIAS_ROOT = Path("/opt/mooncen-an2p-docker")
STATE_ROOT = Path("/var/lib/mooncen-an2p-runtime")
CONTROL_FINALIZATIONS = STATE_ROOT / "control-finalizations"
PENDING_CONTROL_FINALIZATION = STATE_ROOT / "pending-control-finalization.json"
CONTROL_FINALIZATION_TRANSACTION = STATE_ROOT / "control-finalization-transaction.json"
JOURNAL = STATE_ROOT / "transaction.json"
LOCK = STATE_ROOT / "operation.lock"
RUNTIME_ROOT = Path("/run/mooncen-an2p-runtime")
START_AUTHORIZATION = RUNTIME_ROOT / "start-authorization.json"
BOOT_VALIDATION = RUNTIME_ROOT / "boot-validation.json"
MARKER = Path("/etc/mooncen-an2p/docker-development-enabled")
SELECTOR = Path("/usr/local/libexec/mooncen-an2p-service-control")
SYSTEMCTL = "/bin/systemctl"
PYTHON = "/usr/bin/python3.12"
SYSTEM_UNIT_ROOT = Path("/etc/systemd/system")
RUNTIME_SYSTEM_UNIT_ROOT = Path("/run/systemd/system")
PAIR_PATTERN = re.compile(
    r"\Aruntime-pair\.[0-9a-f]{40}\.[0-9a-f]{40}\.[0-9a-f]{64}\Z"
)
SHA256_PATTERN = re.compile(r"\A[0-9a-f]{64}\Z")
SYSTEM_UNITS = {
    "ops_api": "mooncen-ops-api.service",
    "status": "mooncen-ops-status-agent.service",
    "worker": "mooncen-deployment-worker.service",
    "tunnel": "mooncen-ops-db-tunnel.service",
    "docker": "mooncen-docker-dev.service",
}
JOURNAL_KEYS = frozenset(
    {
        "development_only",
        "docker_selected",
        "ops_api_enabled",
        "phase",
        "previous",
        "previous_docker_selected",
        "schema_version",
        "status_enabled",
        "target",
        "tunnel_enabled",
        "worker_enabled",
    }
)
RECEIPT_KEYS = frozenset(
    {
        "build_policy_sha256",
        "commit",
        "control_inventory_sha256",
        "docker_inventory_sha256",
        "environment_sha256",
        "host_layer_sha256",
        "pair_name",
        "receipt_digest",
        "schema_version",
        "source_tree",
    }
)
HOST_LAYER_FILES = (
    (
        "runtime_manager",
        "deploy/an2p/runtime_pair_manager.py",
        Path("/usr/local/libexec/mooncen-an2p-runtime-manager"),
        0o755,
    ),
    (
        "service_selector",
        "deploy/an2p/mooncen_an2p_service_control.py",
        Path("/usr/local/libexec/mooncen-an2p-service-control"),
        0o755,
    ),
    (
        "ipv6_redirect",
        "deploy/an2p/mooncen_loopback_redirect.py",
        Path("/usr/local/libexec/mooncen-an2p-loopback-redirect"),
        0o755,
    ),
    (
        "evidence_registrar",
        "deploy/an2p/mooncen_register_container_evidence.py",
        Path("/usr/local/libexec/mooncen-register-container-evidence"),
        0o755,
    ),
    *tuple(
        (
            f"unit_{name}",
            f"deploy/an2p/{name}",
            Path("/etc/systemd/system") / name,
            0o644,
        )
        for name in (
            "mooncen-an2p-runtime-recovery.service",
            "mooncen-deployment-worker.service",
            "mooncen-docker-dev.service",
            "mooncen-ops-api-ipv6.service",
            "mooncen-ops-api-ipv6.socket",
            "mooncen-ops-api.service",
            "mooncen-ops-status-agent.service",
            "mooncen-ops-api.socket",
            "mooncen-ops-db-tunnel.service",
        )
    ),
)
START_AUTHORIZATION_KEYS = frozenset(
    {
        "boot_id",
        "deadline_epoch",
        "journal_sha256",
        "pair",
        "pid",
        "pid_start_ticks",
        "schema_version",
        "unit",
    }
)
BOOT_VALIDATION_KEYS = frozenset(
    {"boot_id", "pair", "pair_receipt_digest", "schema_version"}
)
CONTROL_FINALIZATION_KEYS = frozenset(
    {
        "environment",
        "environment_sha256",
        "expires_at",
        "pair",
        "receipt_digest",
        "receipt_id",
        "release_digest",
        "release_id",
        "schema_version",
        "source_tree",
        "target",
        "target_identity",
    }
)
PENDING_CONTROL_FINALIZATION_KEYS = frozenset(
    {
        "environment",
        "environment_sha256",
        "pair",
        "receipt_digest",
        "release_digest",
        "schema_version",
        "source_tree",
        "target",
        "target_identity",
    }
)
UUID_PATTERN = re.compile(
    r"\A[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
UTC_PATTERN = re.compile(
    r"\A20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z"
)
_OPERATION_LOCK_DESCRIPTOR: int | None = None


class PairManagerError(RuntimeError):
    """Raised when the runtime pair cannot safely converge."""


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Keep recovery probes on the exact reviewed loopback endpoint."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        del req, fp, code, msg, headers, newurl
        return None


def _health_response_ready(response: object, expected_url: str) -> bool:
    return (
        response.status == 200  # type: ignore[attr-defined]
        and response.geturl() == expected_url  # type: ignore[attr-defined]
        and response.headers.get_content_type() == "application/json"  # type: ignore[attr-defined]
        and response.read(64 * 1024 + 1) == b'{"status":"ready"}'  # type: ignore[attr-defined]
    )


def _canonical_json(value: dict[str, Any]) -> bytes:
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


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise PairManagerError("runtime evidence contains duplicate keys")
        value[key] = item
    return value


def _safe_directory(path: Path, *, uid: int, gid: int, mode: int) -> Path:
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise PairManagerError(f"runtime directory is unavailable: {path}") from exc
    if (
        path.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != uid
        or metadata.st_gid != gid
        or stat.S_IMODE(metadata.st_mode) != mode
    ):
        raise PairManagerError(f"runtime directory is unsafe: {path}")
    return resolved


def _safe_file(path: Path, *, uid: int, gid: int, mode: int) -> bytes:
    try:
        metadata = path.lstat()
        payload = path.read_bytes()
    except OSError as exc:
        raise PairManagerError(f"runtime file is unavailable: {path}") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != uid
        or metadata.st_gid != gid
        or stat.S_IMODE(metadata.st_mode) != mode
        or not payload
        or len(payload) > 1024 * 1024
    ):
        raise PairManagerError(f"runtime file is unsafe: {path}")
    return payload


def _operator_gid() -> int:
    import grp

    try:
        return grp.getgrnam("mooncen_docker_operator").gr_gid
    except KeyError as exc:
        raise PairManagerError("Docker operator group is unavailable") from exc


def _load_canonical(path: Path, *, mode: int, gid: int) -> dict[str, Any]:
    payload = _safe_file(path, uid=0, gid=gid, mode=mode)
    try:
        value = json.loads(payload.decode("ascii"), object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError) as exc:
        raise PairManagerError("runtime evidence is not valid JSON") from exc
    if not isinstance(value, dict) or payload != _canonical_json(value):
        raise PairManagerError("runtime evidence is not canonical JSON")
    return value


def _boot_id() -> str:
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="ascii"
        ).strip()
    except OSError as exc:
        raise PairManagerError("runtime boot identity is unavailable") from exc
    if re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        value,
    ) is None:
        raise PairManagerError("runtime boot identity is invalid")
    return value


def _pid_start_ticks(pid: int) -> int:
    try:
        payload = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except OSError as exc:
        raise PairManagerError("runtime manager process is unavailable") from exc
    closing = payload.rfind(")")
    if closing < 0:
        raise PairManagerError("runtime manager process identity is invalid")
    fields = payload[closing + 2 :].split()
    try:
        ticks = int(fields[19])
    except (IndexError, ValueError) as exc:
        raise PairManagerError("runtime manager process identity is invalid") from exc
    if ticks <= 0:
        raise PairManagerError("runtime manager process identity is invalid")
    return ticks


def _inventory(root: Path) -> str:
    script = root / "deploy/docker/native_baseline.py"
    _safe_file(script, uid=0, gid=0, mode=0o644)
    try:
        result = subprocess.run(
            (PYTHON, "-I", str(script), "--root", str(root)),
            cwd="/",
            env={"HOME": "/root", "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=900,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PairManagerError("runtime inventory could not be computed") from exc
    digest = result.stdout.decode("ascii", errors="ignore").strip()
    if result.returncode != 0 or result.stderr or SHA256_PATTERN.fullmatch(digest) is None:
        raise PairManagerError("runtime inventory could not be verified")
    return digest


def _host_layer_digest(control: Path) -> str:
    records: list[dict[str, Any]] = []
    for label, relative, installed, installed_mode in HOST_LAYER_FILES:
        source_payload = _safe_file(
            control / relative,
            uid=0,
            gid=0,
            mode=0o644,
        )
        installed_payload = _safe_file(
            installed,
            uid=0,
            gid=0,
            mode=installed_mode,
        )
        if source_payload != installed_payload:
            raise PairManagerError(f"installed host runtime drifted: {label}")
        records.append(
            {
                "label": label,
                "mode": f"{installed_mode:04o}",
                "sha256": hashlib.sha256(source_payload).hexdigest(),
            }
        )
    return hashlib.sha256(_canonical_json({"files": records})).hexdigest()


def _validate_pair_structure(name: str) -> tuple[Path, Path, Path, dict[str, Any]]:
    if PAIR_PATTERN.fullmatch(name) is None:
        raise PairManagerError("runtime pair name is invalid")
    pair = _safe_directory(PAIR_RELEASES / name, uid=0, gid=0, mode=0o755)
    control = _safe_directory(pair / "control", uid=0, gid=0, mode=0o755)
    docker = _safe_directory(pair / "docker", uid=0, gid=_operator_gid(), mode=0o750)
    receipt = _load_canonical(pair / ".pair-receipt.json", mode=0o600, gid=0)
    if (
        frozenset(receipt) != RECEIPT_KEYS
        or receipt.get("schema_version") != 1
        or receipt.get("pair_name") != name
        or not all(
            SHA256_PATTERN.fullmatch(str(receipt.get(key, ""))) is not None
            for key in (
                "build_policy_sha256",
                "control_inventory_sha256",
                "docker_inventory_sha256",
                "environment_sha256",
                "host_layer_sha256",
                "receipt_digest",
            )
        )
        or re.fullmatch(r"[0-9a-f]{40}", str(receipt.get("commit", ""))) is None
        or re.fullmatch(r"[0-9a-f]{40}", str(receipt.get("source_tree", ""))) is None
    ):
        raise PairManagerError("runtime pair receipt is invalid")
    unsigned = {key: value for key, value in receipt.items() if key != "receipt_digest"}
    import hashlib

    if hashlib.sha256(_canonical_json(unsigned)).hexdigest() != receipt["receipt_digest"]:
        raise PairManagerError("runtime pair receipt digest is invalid")
    return pair, control, docker, receipt


def validate_pair(name: str) -> tuple[Path, dict[str, Any]]:
    pair, control, docker, receipt = _validate_pair_structure(name)
    if _inventory(control) != receipt["control_inventory_sha256"]:
        raise PairManagerError("control runtime inventory drifted")
    if _inventory(docker) != receipt["docker_inventory_sha256"]:
        raise PairManagerError("Docker runtime inventory drifted")
    if _host_layer_digest(control) != receipt["host_layer_sha256"]:
        raise PairManagerError("installed host runtime receipt drifted")
    return pair, receipt


def _control_finalization_receipt_valid(name: str) -> bool:
    """Validate the exact durable DB-registration receipt, independent of phase."""

    if PAIR_PATTERN.fullmatch(name) is None:
        raise PairManagerError("control finalization pair is invalid")
    path = CONTROL_FINALIZATIONS / f"{name}.json"
    if not path.exists() and not path.is_symlink():
        return False
    value = _load_canonical(path, mode=0o600, gid=0)
    _pair, _control, docker, receipt = _validate_pair_structure(name)
    activation = _load_canonical(
        docker / "activation.json",
        mode=0o640,
        gid=_operator_gid(),
    )
    if (
        frozenset(value) != CONTROL_FINALIZATION_KEYS
        or value.get("schema_version") != 1
        or value.get("pair") != name
        or value.get("source_tree") != receipt["source_tree"]
        or value.get("environment") != "development"
        or value.get("target") != "an2p-dev"
        or value.get("environment_sha256") != activation.get("environment_sha256")
        or value.get("receipt_digest") != activation.get("receipt_digest")
        or value.get("release_digest") != activation.get("release_digest")
        or value.get("target_identity") != activation.get("target_identity")
        or UUID_PATTERN.fullmatch(str(value.get("receipt_id", ""))) is None
        or UUID_PATTERN.fullmatch(str(value.get("release_id", ""))) is None
        or UTC_PATTERN.fullmatch(str(value.get("expires_at", ""))) is None
    ):
        raise PairManagerError("control finalization receipt is invalid")
    return True


def control_finalized(name: str) -> bool:
    """Return whether the pair has committed authority and no pending phase."""

    if PAIR_PATTERN.fullmatch(name) is None:
        raise PairManagerError("control finalization pair is invalid")
    if PENDING_CONTROL_FINALIZATION.exists() or PENDING_CONTROL_FINALIZATION.is_symlink():
        pending = _load_canonical(PENDING_CONTROL_FINALIZATION, mode=0o600, gid=0)
        if frozenset(pending) != PENDING_CONTROL_FINALIZATION_KEYS:
            raise PairManagerError("pending control finalization receipt is invalid")
        if pending.get("pair") == name:
            if pending != _pending_control_value(name):
                raise PairManagerError("pending control finalization receipt drifted")
            return False
    return _control_finalization_receipt_valid(name)


def _control_start_ready(name: str) -> bool:
    """Return whether automatic control-service restoration is fully committed."""

    if not control_finalized(name):
        return False
    try:
        CONTROL_FINALIZATION_TRANSACTION.lstat()
    except FileNotFoundError:
        return True
    except OSError as exc:
        raise PairManagerError(
            "control finalization transaction state is unavailable"
        ) from exc
    return False


def _pending_control_value(name: str) -> dict[str, Any]:
    _pair, _control, docker, receipt = _validate_pair_structure(name)
    activation = _load_canonical(
        docker / "activation.json",
        mode=0o640,
        gid=_operator_gid(),
    )
    required = {
        "environment_sha256",
        "receipt_digest",
        "release_digest",
        "source_tree",
        "target_identity",
    }
    if (
        activation.get("schema_version") != 1
        or not required.issubset(activation)
        or activation.get("source_tree") != receipt["source_tree"]
        or any(
            SHA256_PATTERN.fullmatch(str(activation.get(key, ""))) is None
            for key in required - {"source_tree"}
        )
    ):
        raise PairManagerError("Docker activation receipt is invalid")
    return {
        "environment": "development",
        "environment_sha256": activation["environment_sha256"],
        "pair": name,
        "receipt_digest": activation["receipt_digest"],
        "release_digest": activation["release_digest"],
        "schema_version": 1,
        "source_tree": receipt["source_tree"],
        "target": "an2p-dev",
        "target_identity": activation["target_identity"],
    }


def _write_pending_control_finalization(
    name: str, *, replace_pair: str | None = None
) -> None:
    value = _pending_control_value(name)
    payload = _canonical_json(value)
    if PENDING_CONTROL_FINALIZATION.exists() or PENDING_CONTROL_FINALIZATION.is_symlink():
        existing = _load_canonical(
            PENDING_CONTROL_FINALIZATION,
            mode=0o600,
            gid=0,
        )
        if frozenset(existing) != PENDING_CONTROL_FINALIZATION_KEYS:
            raise PairManagerError("pending control finalization receipt is invalid")
        if existing == value:
            return
        # A fresh immutable PASS receipt may supersede the exact pending
        # receipt of the pair being replaced.  No unrelated pending authority
        # can be overwritten.
        if replace_pair is None or existing != _pending_control_value(replace_pair):
            raise PairManagerError("a different control finalization is already pending")
    stage = STATE_ROOT / f".pending-control-finalization.{os.getpid()}.tmp"
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
    os.replace(stage, PENDING_CONTROL_FINALIZATION)
    _fsync_directory(STATE_ROOT)


def _clear_pending_control_finalization(name: str) -> None:
    if not PENDING_CONTROL_FINALIZATION.exists() and not PENDING_CONTROL_FINALIZATION.is_symlink():
        return
    value = _load_canonical(PENDING_CONTROL_FINALIZATION, mode=0o600, gid=0)
    if (
        frozenset(value) != PENDING_CONTROL_FINALIZATION_KEYS
        or value.get("pair") != name
    ):
        return
    PENDING_CONTROL_FINALIZATION.unlink()
    _fsync_directory(STATE_ROOT)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def ensure_layout() -> None:
    for path, mode in (
        (PAIR_ROOT, 0o755),
        (PAIR_RELEASES, 0o755),
        (CONTROL_ALIAS_ROOT, 0o755),
        (DOCKER_ALIAS_ROOT, 0o755),
        (STATE_ROOT, 0o700),
        (CONTROL_FINALIZATIONS, 0o700),
        (RUNTIME_ROOT, 0o700),
    ):
        if not path.exists() and not path.is_symlink():
            path.mkdir(mode=mode, parents=True)
            os.chown(path, 0, 0)
        _safe_directory(path, uid=0, gid=0, mode=mode)
    aliases = {
        CONTROL_ALIAS_ROOT / "current": "../mooncen-an2p-runtime/current/control",
        DOCKER_ALIAS_ROOT / "current": "../mooncen-an2p-runtime/current/docker",
    }
    for path, target in aliases.items():
        if not path.exists() and not path.is_symlink():
            path.symlink_to(target)
            _fsync_directory(path.parent)
        metadata = path.lstat()
        if (
            not stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_gid != 0
            or os.readlink(path) != target
        ):
            raise PairManagerError("runtime compatibility alias is unsafe")


def current_pair(*, full_validation: bool = True) -> str | None:
    if not PAIR_CURRENT.exists() and not PAIR_CURRENT.is_symlink():
        return None
    metadata = PAIR_CURRENT.lstat()
    target = os.readlink(PAIR_CURRENT)
    if (
        not stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or not target.startswith("releases/")
        or PAIR_PATTERN.fullmatch(target.removeprefix("releases/")) is None
    ):
        raise PairManagerError("runtime pair pointer is unsafe")
    name = target.removeprefix("releases/")
    if full_validation:
        validate_pair(name)
    else:
        _validate_pair_structure(name)
    return name


def _switch_pointer(name: str | None) -> None:
    if name is None:
        if PAIR_CURRENT.exists() or PAIR_CURRENT.is_symlink():
            PAIR_CURRENT.unlink()
            _fsync_directory(PAIR_ROOT)
        _clear_boot_validation()
        return
    _pair, receipt = validate_pair(name)
    stage = PAIR_ROOT / f".current.{os.getpid()}.tmp"
    if stage.exists() or stage.is_symlink():
        raise PairManagerError("runtime pointer staging path already exists")
    stage.symlink_to(f"releases/{name}")
    os.replace(stage, PAIR_CURRENT)
    _fsync_directory(PAIR_ROOT)
    if current_pair() != name:
        raise PairManagerError("runtime pair pointer did not converge")
    _write_boot_validation(name, receipt["receipt_digest"])


def _systemctl(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        (SYSTEMCTL, *arguments),
        cwd="/",
        env={"HOME": "/root", "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=1860,
    )
    if check and result.returncode != 0:
        raise PairManagerError("runtime systemd operation failed")
    return result


def _mask_runtime_ops_api() -> None:
    """Make the socket-activation barrier authoritative before stopping API."""

    api = SYSTEM_UNITS["ops_api"]
    _systemctl("mask", "--runtime", "--now", api)
    # `mask` creates the runtime symlink, but a loaded unit can remain usable
    # until PID 1 reloads.  Stop only after the reload closes that restart race.
    _systemctl("daemon-reload")
    _systemctl("stop", api)
    _systemctl("reset-failed", api, check=False)


def _unmask_runtime_ops_api() -> None:
    """Remove the runtime barrier and make PID 1 load the reviewed unit."""

    _systemctl("unmask", "--runtime", SYSTEM_UNITS["ops_api"])
    _systemctl("daemon-reload")


def _unit_property(unit: str, property_name: str) -> str:
    result = _systemctl("show", unit, f"--property={property_name}", "--value")
    try:
        value = result.stdout.decode("ascii").strip()
    except UnicodeError as exc:
        raise PairManagerError("runtime unit state is invalid") from exc
    if not value or "\n" in value:
        raise PairManagerError("runtime unit state is invalid")
    return value


def _unit_loaded(unit: str) -> bool:
    unit_path = SYSTEM_UNIT_ROOT / unit
    if not unit_path.exists() and not unit_path.is_symlink():
        return False
    try:
        metadata = unit_path.lstat()
    except OSError as exc:
        raise PairManagerError("runtime unit file is unavailable") from exc
    if (
        unit_path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o644
    ):
        raise PairManagerError("runtime unit file is unsafe")
    state = _unit_property(unit, "LoadState")
    if state == "masked" and unit == SYSTEM_UNITS["ops_api"]:
        mask = RUNTIME_SYSTEM_UNIT_ROOT / unit
        try:
            mask_metadata = mask.lstat()
        except OSError as exc:
            raise PairManagerError("runtime API mask is unavailable") from exc
        if (
            not mask.is_symlink()
            or mask_metadata.st_uid != 0
            or mask_metadata.st_gid != 0
            or os.readlink(mask) != "/dev/null"
        ):
            raise PairManagerError("runtime API mask is unsafe")
        return True
    if state != "loaded":
        raise PairManagerError("runtime unit load state is unsafe")
    return True


def _unit_enabled(unit: str) -> bool:
    state = _unit_property(unit, "UnitFileState")
    if state == "masked-runtime" and unit == SYSTEM_UNITS["ops_api"]:
        return False
    if state not in {"enabled", "disabled"}:
        raise PairManagerError("runtime unit enablement is unsafe")
    return state == "enabled"


def _unit_active(unit: str) -> bool:
    state = _unit_property(unit, "ActiveState")
    if state not in {"active", "inactive"}:
        raise PairManagerError("runtime unit activity is unsafe")
    return state == "active"


def _marker_present() -> bool:
    if not MARKER.exists() and not MARKER.is_symlink():
        return False
    metadata = MARKER.lstat()
    if (
        MARKER.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o644
        or metadata.st_size != 0
    ):
        raise PairManagerError("runtime selection marker is unsafe")
    return True


def _selector_status() -> dict[str, Any]:
    completed = _run_selector("runtime-status", timeout=60)
    if (
        completed.returncode != 0
        or completed.stderr
        or not completed.stdout
        or len(completed.stdout) > 4_096
    ):
        raise PairManagerError("development runtime selection is unavailable")
    return _parse_selector_status(completed)


def _run_selector(
    action: str,
    *,
    timeout: int = 1860,
) -> subprocess.CompletedProcess[bytes]:
    environment = {
        "HOME": "/root",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
    }
    pass_fds: tuple[int, ...] = ()
    if action in {"docker-select", "native-select"}:
        descriptor = _OPERATION_LOCK_DESCRIPTOR
        if descriptor is not None:
            environment["MOONCEN_AN2P_MANAGER_LOCK_FD"] = str(descriptor)
            pass_fds = (descriptor,)
    return subprocess.run(
        (str(SELECTOR), action),
        cwd="/",
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=timeout,
        pass_fds=pass_fds,
    )


def _parse_selector_status(completed: subprocess.CompletedProcess[bytes]) -> dict[str, Any]:
    try:
        value = json.loads(completed.stdout.decode("ascii"))
    except (UnicodeError, ValueError) as exc:
        raise PairManagerError("development runtime selection is invalid") from exc
    if (
        not isinstance(value, dict)
        or frozenset(value)
        != {
            "docker_active",
            "docker_enabled",
            "marker",
            "native_active",
            "native_enabled",
            "schema_version",
        }
        or value.get("schema_version") != 1
        or type(value.get("docker_active")) is not bool
        or type(value.get("docker_enabled")) is not bool
        or type(value.get("marker")) is not bool
        or not isinstance(value.get("native_active"), list)
        or not isinstance(value.get("native_enabled"), list)
        or completed.stdout != _canonical_json(value)
    ):
        raise PairManagerError("development runtime selection is invalid")
    docker_selected = value == {
        "docker_active": True,
        "docker_enabled": True,
        "marker": True,
        "native_active": [],
        "native_enabled": [],
        "schema_version": 1,
    }
    native_units = ["mooncen-api.service", "mooncen-frontend.service"]
    native_selected = value == {
        "docker_active": False,
        "docker_enabled": False,
        "marker": False,
        "native_active": native_units,
        "native_enabled": native_units,
        "schema_version": 1,
    }
    if not docker_selected and not native_selected:
        raise PairManagerError("development runtime selection is not healthy and exclusive")
    value["docker_selected"] = docker_selected
    return value


def _snapshot(
    target: str,
    previous: str | None,
    *,
    development_only: bool = False,
) -> dict[str, Any]:
    loaded = {key: _unit_loaded(unit) for key, unit in SYSTEM_UNITS.items()}
    docker_selected = bool(loaded["docker"] and _marker_present())
    previous_docker_selected = docker_selected
    if development_only:
        previous_docker_selected = bool(_selector_status()["docker_selected"])
    ops_api_enabled = bool(
        loaded["ops_api"] and _unit_enabled(SYSTEM_UNITS["ops_api"])
    )
    worker_enabled = bool(
        loaded["worker"] and _unit_enabled(SYSTEM_UNITS["worker"])
    )
    tunnel_enabled = bool(
        loaded["tunnel"] and _unit_enabled(SYSTEM_UNITS["tunnel"])
    )
    status_enabled = bool(
        loaded["status"] and _unit_enabled(SYSTEM_UNITS["status"])
    )
    return {
        "development_only": development_only,
        "docker_selected": True if development_only else docker_selected,
        "ops_api_enabled": False if development_only else ops_api_enabled,
        "phase": "prepared",
        "previous": previous,
        "previous_docker_selected": previous_docker_selected,
        "schema_version": 1,
        "status_enabled": False if development_only else status_enabled,
        "target": target,
        "tunnel_enabled": False if development_only else tunnel_enabled,
        "worker_enabled": False if development_only else worker_enabled,
    }


def _write_journal(value: dict[str, Any]) -> None:
    if frozenset(value) != JOURNAL_KEYS:
        raise PairManagerError("runtime transaction schema is invalid")
    _clear_start_authorization()
    stage = STATE_ROOT / f".transaction.{os.getpid()}.tmp"
    descriptor = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.fchown(descriptor, 0, 0)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(_canonical_json(value))
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    os.replace(stage, JOURNAL)
    _fsync_directory(STATE_ROOT)


def _journal_sha256() -> str:
    payload = _safe_file(JOURNAL, uid=0, gid=0, mode=0o600)
    return hashlib.sha256(payload).hexdigest()


def _clear_boot_validation() -> None:
    if BOOT_VALIDATION.exists() or BOOT_VALIDATION.is_symlink():
        _safe_file(BOOT_VALIDATION, uid=0, gid=0, mode=0o600)
        BOOT_VALIDATION.unlink()
        _fsync_directory(RUNTIME_ROOT)


def _write_boot_validation(pair: str, receipt_digest: str) -> None:
    if PAIR_PATTERN.fullmatch(pair) is None or SHA256_PATTERN.fullmatch(
        receipt_digest
    ) is None:
        raise PairManagerError("runtime boot validation identity is invalid")
    value = {
        "boot_id": _boot_id(),
        "pair": pair,
        "pair_receipt_digest": receipt_digest,
        "schema_version": 1,
    }
    _clear_boot_validation()
    stage = RUNTIME_ROOT / f".boot-validation.{os.getpid()}.tmp"
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
            stream.write(_canonical_json(value))
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    os.replace(stage, BOOT_VALIDATION)
    _fsync_directory(RUNTIME_ROOT)


def _require_boot_validation(pair: str) -> None:
    value = _load_canonical(BOOT_VALIDATION, mode=0o600, gid=0)
    _pair, _control, _docker, receipt = _validate_pair_structure(pair)
    if (
        frozenset(value) != BOOT_VALIDATION_KEYS
        or value.get("schema_version") != 1
        or value.get("boot_id") != _boot_id()
        or value.get("pair") != pair
        or value.get("pair_receipt_digest") != receipt["receipt_digest"]
    ):
        raise PairManagerError("runtime boot validation receipt is invalid")


def _clear_start_authorization() -> None:
    if START_AUTHORIZATION.exists() or START_AUTHORIZATION.is_symlink():
        _safe_file(START_AUTHORIZATION, uid=0, gid=0, mode=0o600)
        START_AUTHORIZATION.unlink()
        _fsync_directory(RUNTIME_ROOT)


def _write_start_authorization(unit: str) -> None:
    if unit not in SYSTEM_UNITS.values():
        raise PairManagerError("runtime start authorization unit is invalid")
    pair = current_pair(full_validation=False)
    if pair is None:
        raise PairManagerError("runtime start authorization has no active pair")
    pid = os.getpid()
    value = {
        "boot_id": _boot_id(),
        "deadline_epoch": int(time.time()) + 300,
        "journal_sha256": _journal_sha256(),
        "pair": pair,
        "pid": pid,
        "pid_start_ticks": _pid_start_ticks(pid),
        "schema_version": 1,
        "unit": unit,
    }
    _clear_start_authorization()
    stage = RUNTIME_ROOT / f".start-authorization.{pid}.tmp"
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
            stream.write(_canonical_json(value))
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    os.replace(stage, START_AUTHORIZATION)
    _fsync_directory(RUNTIME_ROOT)


def gate_service_start(unit: str) -> None:
    """Fail closed while a pair transaction is in flight.

    The manager keeps the listening Ops socket open during a switch. A socket
    request must not restart the old API between the service stop and the
    atomic pair-pointer replacement, so transaction-time starts require a
    short-lived authorization bound to this manager process and journal.
    """

    if unit not in SYSTEM_UNITS.values():
        raise PairManagerError("runtime start gate unit is invalid")
    lock_descriptor = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    lock_metadata = os.fstat(lock_descriptor)
    if (
        not stat.S_ISREG(lock_metadata.st_mode)
        or lock_metadata.st_uid != os.geteuid()
        or lock_metadata.st_gid != os.getegid()
        or stat.S_IMODE(lock_metadata.st_mode) != 0o600
    ):
        os.close(lock_descriptor)
        raise PairManagerError("runtime operation lock is unsafe")
    lock_busy = False
    try:
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_busy = True
        current = current_pair(full_validation=False)
        if current is None:
            raise PairManagerError("runtime start gate has no active pair")
        _require_boot_validation(current)
        if unit in {
            SYSTEM_UNITS["ops_api"],
            SYSTEM_UNITS["status"],
            SYSTEM_UNITS["worker"],
        } and not control_finalized(current):
            raise PairManagerError(
                "runtime control service requires exact finalized registration"
            )
        journal_exists = JOURNAL.exists() or JOURNAL.is_symlink()
        authorization_exists = (
            START_AUTHORIZATION.exists() or START_AUTHORIZATION.is_symlink()
        )
        if not lock_busy:
            if journal_exists or authorization_exists:
                raise PairManagerError("stale runtime transaction evidence exists")
            return
        if not journal_exists or not authorization_exists:
            raise PairManagerError("runtime transaction blocks service start")
        _load_journal(validate_inventories=False)
        value = _load_canonical(START_AUTHORIZATION, mode=0o600, gid=0)
        if (
            frozenset(value) != START_AUTHORIZATION_KEYS
            or value.get("schema_version") != 1
            or value.get("unit") != unit
            or value.get("pair") != current
            or value.get("boot_id") != _boot_id()
            or type(value.get("deadline_epoch")) is not int
            or value["deadline_epoch"] <= int(time.time())
            or value["deadline_epoch"] > int(time.time()) + 300
            or type(value.get("pid")) is not int
            or value["pid"] <= 1
            or type(value.get("pid_start_ticks")) is not int
            or value["pid_start_ticks"] <= 0
            or not isinstance(value.get("journal_sha256"), str)
            or SHA256_PATTERN.fullmatch(value["journal_sha256"]) is None
            or value["journal_sha256"] != _journal_sha256()
        ):
            raise PairManagerError("runtime start authorization is invalid")
        if _pid_start_ticks(value["pid"]) != value["pid_start_ticks"]:
            raise PairManagerError("runtime start authorization owner changed")
    finally:
        os.close(lock_descriptor)


def _load_journal(*, validate_inventories: bool = True) -> dict[str, Any] | None:
    if not JOURNAL.exists() and not JOURNAL.is_symlink():
        return None
    value = _load_canonical(JOURNAL, mode=0o600, gid=0)
    if (
        frozenset(value) != JOURNAL_KEYS
        or value.get("schema_version") != 1
        or value.get("phase") not in {"prepared", "switched"}
        or PAIR_PATTERN.fullmatch(str(value.get("target", ""))) is None
        or (
            value.get("previous") is not None
            and PAIR_PATTERN.fullmatch(str(value["previous"])) is None
        )
        or any(
            type(value.get(key)) is not bool
            for key in (
                "development_only",
                "docker_selected",
                "ops_api_enabled",
                "previous_docker_selected",
                "status_enabled",
                "tunnel_enabled",
                "worker_enabled",
            )
        )
    ):
        raise PairManagerError("runtime transaction is invalid")
    target_validator = validate_pair if validate_inventories else _validate_pair_structure
    target_validator(value["target"])
    if value["previous"] is not None:
        target_validator(value["previous"])
    return value


def _clear_journal() -> None:
    _clear_start_authorization()
    if JOURNAL.exists() or JOURNAL.is_symlink():
        _safe_file(JOURNAL, uid=0, gid=0, mode=0o600)
        JOURNAL.unlink()
        _fsync_directory(STATE_ROOT)


def _stop_units() -> None:
    api = SYSTEM_UNITS["ops_api"]
    if _unit_loaded(api):
        # Publish the barrier before touching any other consumer.  Otherwise a
        # queued connection can restart the API and its required tunnel while
        # the transaction is stopping the remaining units.
        _mask_runtime_ops_api()
        if _unit_active(api):
            raise PairManagerError("runtime unit remains active during switch")
    for unit in (
        SYSTEM_UNITS["docker"],
        SYSTEM_UNITS["status"],
        SYSTEM_UNITS["worker"],
        SYSTEM_UNITS["tunnel"],
    ):
        if _unit_loaded(unit):
            _systemctl("stop", unit)
            _systemctl("reset-failed", unit, check=False)
            if _unit_active(unit):
                raise PairManagerError("runtime unit remains active during switch")


def _wait_http(url: str, timeout: int = 180) -> None:
    deadline = time.monotonic() + timeout
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _RejectRedirects(),
    )
    while time.monotonic() < deadline:
        try:
            request = urllib.request.Request(url, method="GET")
            with opener.open(request, timeout=3) as response:  # noqa: S310 - fixed loopback URL, proxies disabled.
                if _health_response_ready(response, url):
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(1)
    raise PairManagerError("runtime health endpoint did not recover")


def _authorized_systemctl_start(unit: str) -> None:
    _write_start_authorization(unit)
    try:
        _systemctl("start", unit)
        if not _unit_active(unit):
            raise PairManagerError("runtime unit did not restart")
    finally:
        _clear_start_authorization()


def _apply_unit_enablement(snapshot: dict[str, Any]) -> dict[str, Any]:
    effective = dict(snapshot)
    control_fields = (
        "ops_api_enabled",
        "worker_enabled",
        "status_enabled",
        "tunnel_enabled",
    )
    if any(bool(effective[field]) for field in control_fields):
        active = current_pair(full_validation=False)
        if active is None or not _control_start_ready(active):
            for field in control_fields:
                effective[field] = False
    for key, field in (
        ("ops_api", "ops_api_enabled"),
        ("worker", "worker_enabled"),
        ("status", "status_enabled"),
        ("tunnel", "tunnel_enabled"),
    ):
        unit = SYSTEM_UNITS[key]
        if effective[field]:
            if key == "ops_api":
                _unmask_runtime_ops_api()
            _systemctl("enable", unit)
        else:
            if key == "ops_api":
                _mask_runtime_ops_api()
            _systemctl("disable", unit)
    if not effective["docker_selected"]:
        _systemctl("disable", SYSTEM_UNITS["docker"])
    return effective


def _quiesce_control_start_boundary() -> None:
    api = SYSTEM_UNITS["ops_api"]
    _mask_runtime_ops_api()
    _systemctl("disable", api)
    for key in ("status", "worker", "tunnel"):
        unit = SYSTEM_UNITS[key]
        if _unit_loaded(unit):
            _systemctl("disable", "--now", unit)
            _systemctl("reset-failed", unit, check=False)


def _converge_control_start_boundary(name: str) -> None:
    """Keep reserved Ops sockets inert until control authority is committed."""

    if _control_start_ready(name):
        _unmask_runtime_ops_api()
        return
    _quiesce_control_start_boundary()


def _start_units(snapshot: dict[str, Any]) -> None:
    if (
        snapshot["ops_api_enabled"]
        or snapshot["worker_enabled"]
        or snapshot["status_enabled"]
    ) and not snapshot["tunnel_enabled"]:
        raise PairManagerError("control services require the isolated DB tunnel")
    if snapshot["tunnel_enabled"]:
        _systemctl("start", SYSTEM_UNITS["tunnel"])
        if not _unit_active(SYSTEM_UNITS["tunnel"]):
            raise PairManagerError("runtime DB tunnel did not restart")
    if snapshot["ops_api_enabled"]:
        _authorized_systemctl_start(SYSTEM_UNITS["ops_api"])
        _wait_http("http://127.0.0.1:5175/health")
    if snapshot["worker_enabled"]:
        _authorized_systemctl_start(SYSTEM_UNITS["worker"])
    if snapshot["status_enabled"]:
        _authorized_systemctl_start(SYSTEM_UNITS["status"])
    if snapshot["docker_selected"]:
        _write_start_authorization(SYSTEM_UNITS["docker"])
        try:
            result = _run_selector("docker-select")
            if result.returncode != 0 or result.stderr:
                raise PairManagerError("Docker development runtime did not restart")
        finally:
            _clear_start_authorization()


def _select_native() -> None:
    result = _run_selector("native-select")
    if result.returncode != 0 or result.stderr:
        raise PairManagerError("native development runtime did not restart")
    if bool(_selector_status()["docker_selected"]):
        raise PairManagerError("native development runtime did not converge")


def _rollback_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    previous = snapshot["previous"]
    restored = dict(snapshot)
    finalized = previous is not None and _control_start_ready(previous)
    restored.update(
        {
            # A Docker selection without a pair pointer cannot be reconstructed
            # safely.  Initial activation rollback therefore returns to the
            # reviewed native runtime instead of restarting the failed target.
            "docker_selected": (
                snapshot["previous_docker_selected"] if previous is not None else False
            ),
            "ops_api_enabled": finalized,
            "status_enabled": finalized,
            "tunnel_enabled": finalized,
            "worker_enabled": finalized,
        }
    )
    return restored


def _rollback_transaction(snapshot: dict[str, Any]) -> None:
    _stop_units()
    _switch_pointer(snapshot["previous"])
    restored = _rollback_snapshot(snapshot)
    restored = _apply_unit_enablement(restored)
    _start_units(restored)
    if (
        snapshot["development_only"] or snapshot["previous"] is None
    ) and not restored["docker_selected"]:
        _select_native()
    _clear_pending_control_finalization(snapshot["target"])
    previous = snapshot["previous"]
    if previous is not None and not control_finalized(previous):
        _write_pending_control_finalization(previous)
    _clear_journal()


def recover() -> None:
    snapshot = _load_journal()
    if snapshot is None:
        return
    if (
        snapshot["previous"] is None
        and snapshot["phase"] == "switched"
        and not snapshot["development_only"]
    ):
        _switch_pointer(snapshot["target"])
        effective = _apply_unit_enablement(snapshot)
        _start_units(effective)
        if not control_finalized(snapshot["target"]):
            _write_pending_control_finalization(snapshot["target"])
        _clear_journal()
        return
    _rollback_transaction(snapshot)


def recover_boot() -> None:
    """Converge an interrupted switch, committing initial development health."""

    snapshot = _load_journal()
    if snapshot is None:
        active = current_pair()
        if active is None:
            _quiesce_control_start_boundary()
            _clear_boot_validation()
            return
        _pair, _control, _docker, receipt = _validate_pair_structure(active)
        _converge_control_start_boundary(active)
        _write_boot_validation(active, receipt["receipt_digest"])
        return
    _stop_units()
    if snapshot["development_only"]:
        _switch_pointer(snapshot["previous"])
        restored = _rollback_snapshot(snapshot)
        restored = _apply_unit_enablement(restored)
        _start_units(restored)
        if not restored["docker_selected"]:
            _select_native()
        _clear_pending_control_finalization(snapshot["target"])
        previous = snapshot["previous"]
        if previous is not None and not control_finalized(previous):
            _write_pending_control_finalization(previous)
        _clear_journal()
        return
    if (
        snapshot["previous"] is None
        and snapshot["phase"] == "switched"
        and not snapshot["development_only"]
    ):
        _switch_pointer(snapshot["target"])
        _apply_unit_enablement(snapshot)
    else:
        _switch_pointer(snapshot["previous"])
        _apply_unit_enablement(_rollback_snapshot(snapshot))
        _clear_pending_control_finalization(snapshot["target"])
        previous = snapshot["previous"]
        if previous is not None and not control_finalized(previous):
            _write_pending_control_finalization(previous)
    if (
        snapshot["previous"] is None
        and snapshot["phase"] == "switched"
        and not control_finalized(snapshot["target"])
    ):
        _write_pending_control_finalization(snapshot["target"])
    _clear_journal()


def activate(name: str) -> dict[str, Any]:
    validate_pair(name)
    recover()
    previous = current_pair()
    if previous == name:
        return {"active_pair": name, "previous_pair": previous, "schema_version": 1}
    snapshot = _snapshot(name, previous)
    _write_journal(snapshot)
    try:
        _stop_units()
        _switch_pointer(name)
        snapshot["phase"] = "switched"
        _write_journal(snapshot)
        effective = _apply_unit_enablement(snapshot)
        _start_units(effective)
    except Exception:
        try:
            _rollback_transaction(snapshot)
        except Exception as rollback_error:
            raise PairManagerError(
                "runtime switch failed and durable rollback remains incomplete"
            ) from rollback_error
        raise
    _clear_journal()
    return {"active_pair": name, "previous_pair": previous, "schema_version": 1}


def activate_development(name: str) -> dict[str, Any]:
    """Select a pair with Docker only; never carry control services forward."""

    validate_pair(name)
    recover()
    previous = current_pair()
    snapshot = _snapshot(name, previous, development_only=True)
    if previous is None and snapshot["previous_docker_selected"]:
        raise PairManagerError(
            "first pair activation cannot reconstruct a legacy Docker selection"
        )
    _write_journal(snapshot)
    try:
        _stop_units()
        _switch_pointer(name)
        snapshot["phase"] = "switched"
        _write_journal(snapshot)
        effective = _apply_unit_enablement(snapshot)
        _start_units(effective)
        if not bool(_selector_status()["docker_selected"]):
            raise PairManagerError("development activation lost Docker selection")
        # Publish the new pending authority only after the replacement runtime
        # and both health probes have succeeded.  Rollback reconstructs the
        # previous immutable pending receipt if publication or journal commit
        # is interrupted.
        _write_pending_control_finalization(name, replace_pair=previous)
    except Exception:
        try:
            _rollback_transaction(snapshot)
        except Exception as rollback_error:
            raise PairManagerError(
                "development switch failed and durable rollback remains incomplete"
            ) from rollback_error
        raise
    _clear_journal()
    return {
        "active_pair": name,
        "development_only": True,
        "previous_pair": previous,
        "schema_version": 1,
    }


def activate_retained(name: str) -> dict[str, Any]:
    """Select a retained pair and restore only its durable finalized services."""

    validate_pair(name)
    recover()
    previous = current_pair()
    snapshot = _snapshot(name, previous)
    finalized = _control_start_ready(name)
    snapshot.update(
        {
            "docker_selected": True,
            "ops_api_enabled": finalized,
            "status_enabled": finalized,
            "tunnel_enabled": finalized,
            "worker_enabled": finalized,
        }
    )
    _write_journal(snapshot)
    try:
        _stop_units()
        _switch_pointer(name)
        snapshot["phase"] = "switched"
        _write_journal(snapshot)
        effective = _apply_unit_enablement(snapshot)
        _start_units(effective)
    except Exception:
        try:
            _rollback_transaction(snapshot)
        except Exception as rollback_error:
            raise PairManagerError(
                "retained switch failed and durable rollback remains incomplete"
            ) from rollback_error
        raise
    if not finalized:
        _write_pending_control_finalization(name, replace_pair=previous)
    if previous is not None and previous != name:
        _clear_pending_control_finalization(previous)
    _clear_journal()
    return {
        "active_pair": name,
        "control_finalized": finalized,
        "previous_pair": previous,
        "schema_version": 1,
    }


def deactivate_initial(name: str) -> dict[str, Any]:
    """Remove a failed first-install pointer after stopping every dependent."""

    validate_pair(name)
    recover()
    if current_pair() != name:
        raise PairManagerError("initial runtime pair is no longer active")
    _stop_units()
    _switch_pointer(None)
    disabled = {
        "docker_selected": False,
        "ops_api_enabled": False,
        "status_enabled": False,
        "tunnel_enabled": False,
        "worker_enabled": False,
    }
    disabled = _apply_unit_enablement(disabled)
    _start_units(disabled)
    _select_native()
    _clear_pending_control_finalization(name)
    _clear_journal()
    return {"deactivated_pair": name, "schema_version": 1}


def _operation_lock() -> int:
    descriptor = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_gid != 0:
        os.close(descriptor)
        raise PairManagerError("runtime operation lock is unsafe")
    os.fchmod(descriptor, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    return descriptor


def main(argv: Sequence[str] | None = None) -> int:
    global _OPERATION_LOCK_DESCRIPTOR

    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    select = subcommands.add_parser("activate")
    select.add_argument("pair")
    development = subcommands.add_parser("activate-development")
    development.add_argument("pair")
    retained = subcommands.add_parser("activate-retained")
    retained.add_argument("pair")
    deactivate = subcommands.add_parser("deactivate-initial")
    deactivate.add_argument("pair")
    subcommands.add_parser("recover")
    subcommands.add_parser("recover-boot")
    validate = subcommands.add_parser("validate")
    validate.add_argument("pair")
    finalized = subcommands.add_parser("control-finalized")
    finalized.add_argument("pair")
    receipt = subcommands.add_parser("control-finalization-receipt")
    receipt.add_argument("pair")
    gate = subcommands.add_parser("gate-service-start")
    gate.add_argument("unit", choices=tuple(SYSTEM_UNITS.values()))
    arguments = parser.parse_args(argv)
    if os.geteuid() != 0:
        raise PairManagerError("runtime pair manager requires root")
    ensure_layout()
    if arguments.command == "gate-service-start":
        gate_service_start(arguments.unit)
        result = {
            "allowed": True,
            "schema_version": 1,
            "unit": arguments.unit,
        }
        print(_canonical_json(result).decode("ascii"), end="")
        return 0
    descriptor = _operation_lock()
    _OPERATION_LOCK_DESCRIPTOR = descriptor
    try:
        if arguments.command == "recover":
            recover()
            result = {"recovered": True, "schema_version": 1}
        elif arguments.command == "recover-boot":
            recover_boot()
            result = {"boot_recovered": True, "schema_version": 1}
        elif arguments.command == "deactivate-initial":
            result = deactivate_initial(arguments.pair)
        elif arguments.command == "validate":
            _pair, receipt = validate_pair(arguments.pair)
            result = {
                "pair": arguments.pair,
                "schema_version": 1,
                "source_tree": receipt["source_tree"],
                "valid": True,
            }
        elif arguments.command == "control-finalized":
            validate_pair(arguments.pair)
            result = {
                "finalized": control_finalized(arguments.pair),
                "pair": arguments.pair,
                "schema_version": 1,
            }
        elif arguments.command == "control-finalization-receipt":
            validate_pair(arguments.pair)
            result = {
                "pair": arguments.pair,
                "receipt_valid": _control_finalization_receipt_valid(arguments.pair),
                "schema_version": 1,
            }
        elif arguments.command == "activate-development":
            result = activate_development(arguments.pair)
        elif arguments.command == "activate-retained":
            result = activate_retained(arguments.pair)
        else:
            result = activate(arguments.pair)
        print(_canonical_json(result).decode("ascii"), end="")
    finally:
        _OPERATION_LOCK_DESCRIPTOR = None
        os.close(descriptor)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (PairManagerError, OSError, subprocess.SubprocessError) as exc:
        print(f"an2p runtime pair manager: {exc}", file=sys.stderr)
        raise SystemExit(78) from None
