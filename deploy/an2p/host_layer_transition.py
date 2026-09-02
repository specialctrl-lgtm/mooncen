#!/usr/bin/python3.12
"""Perform one reviewed, forward-only an2p host-layer ABI transition.

The active runtime-pair receipt binds the exact installed host helper and unit
bytes.  A new pair with a different host-layer digest therefore cannot be
selected by the ordinary pair manager.  This helper owns the deliberately
separate maintenance boundary: it leaves the old pair untouched until the new
pair and the install publication journal are durable, then removes the pair
pointer, restores the native development runtime, replaces the host layer, and
rolls forward to the new Docker development pair.

There is intentionally no automatic cross-ABI rollback after deactivation.
Every interrupted post-deactivation state retains a root-only journal and a
healthy native development runtime until an exact retry can continue forward.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import fcntl
import grp
import hashlib
import importlib.util
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterator, Sequence


# Loading the reviewed pair manager is used only to obtain its exact host-layer
# manifest. SourceFileLoader otherwise writes __pycache__ into the immutable
# pair after its inventory has been verified, making the next recovery process
# reject the pair that this process just inspected.
sys.dont_write_bytecode = True


PAIR_ROOT = Path("/opt/mooncen-an2p-runtime")
PAIR_RELEASES = PAIR_ROOT / "releases"
PAIR_CURRENT = PAIR_ROOT / "current"
STATE_ROOT = Path("/var/lib/mooncen-an2p-runtime")
TRANSITION_JOURNAL = STATE_ROOT / "host-layer-transition.json"
TRANSITION_LOCK = STATE_ROOT / "host-layer-transition.lock"
INSTALL_LOCK = STATE_ROOT / "install.lock"
INSTALL_LOCK_ENV = "MOONCEN_AN2P_INSTALL_LOCK_FD"
COMMITTED_ROOT = STATE_ROOT / "host-layer-transition-commits"
PAIR_TRANSACTION = STATE_ROOT / "transaction.json"
CONTROL_FINALIZATION_TRANSACTION = STATE_ROOT / "control-finalization-transaction.json"
OPS_ROTATION_TRANSACTION = STATE_ROOT / "ops-rotation-transaction.json"
PENDING_CONTROL_FINALIZATION = STATE_ROOT / "pending-control-finalization.json"

SYSTEM_UNIT_ROOT = Path("/etc/systemd/system")
RECOVERY_UNIT_NAME = "mooncen-an2p-host-transition-recovery.service"
RECOVERY_UNIT = SYSTEM_UNIT_ROOT / RECOVERY_UNIT_NAME
CONTINUATION_UNIT_NAME = "mooncen-an2p-host-transition-continue.service"
CONTINUATION_UNIT = SYSTEM_UNIT_ROOT / CONTINUATION_UNIT_NAME
INSTALLED_HELPER = Path("/usr/local/libexec/mooncen-an2p-host-transition")
INSTALLED_MANAGER = Path("/usr/local/libexec/mooncen-an2p-runtime-manager")
INSTALLED_SELECTOR = Path("/usr/local/libexec/mooncen-an2p-service-control")

SYSTEMCTL = "/bin/systemctl"
PYTHON = "/usr/bin/python3.12"
BASH = "/bin/bash"

ROOT_UID = 0
ROOT_GID = 0
MAX_CONTROL_FILE_SIZE = 2 * 1024 * 1024
MAX_JSON_SIZE = 64 * 1024
HANDOFF_TIMEOUT = 2 * 60 * 60

PAIR_PATTERN = re.compile(r"\Aruntime-pair\.([0-9a-f]{40})\.([0-9a-f]{40})\.([0-9a-f]{64})\Z")
SHA256_PATTERN = re.compile(r"\A[0-9a-f]{64}\Z")
LABEL_PATTERN = re.compile(r"\A[a-z0-9_][a-z0-9_.-]{0,127}\Z")
RELATIVE_PATTERN = re.compile(r"\A[a-zA-Z0-9_.-]+(?:/[a-zA-Z0-9_.-]+)*\Z")

PHASES = ("prepared", "deactivated", "host-installed", "activated")
TRANSITION_KEYS = frozenset(
    {
        "manifest_sha256",
        "phase",
        "previous_host_layer_sha256",
        "previous_pair",
        "previous_receipt_sha256",
        "publish_journal",
        "publish_journal_sha256",
        "schema_version",
        "target_host_layer_sha256",
        "target_pair",
        "target_receipt_sha256",
    }
)
COMMITTED_KEYS = frozenset(
    {
        "helper_sha256",
        "manifest_sha256",
        "previous_host_layer_sha256",
        "previous_pair",
        "previous_receipt_sha256",
        "publish_journal_sha256",
        "schema_version",
        "state",
        "target_host_layer_sha256",
        "target_pair",
        "target_receipt_sha256",
    }
)
PAIR_RECEIPT_KEYS = frozenset(
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
PUBLISH_KEYS = frozenset(
    {
        "build_policy_sha256",
        "commit",
        "host_transition",
        "pair_name",
        "schema_version",
        "source_tree",
        "transition_from_host_layer",
        "transition_from_pair",
    }
)
PENDING_KEYS = frozenset(
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

SOCKET_BOUNDARY_UNITS = (
    "mooncen-ops-api.socket",
    "mooncen-ops-api-ipv6.socket",
    "mooncen-ops-api-ipv6.service",
    "mooncen-an2p-runtime-recovery.service",
)
CONTROL_UNITS = (
    "mooncen-ops-db-tunnel.service",
    "mooncen-deployment-worker.service",
    "mooncen-ops-status-agent.service",
    "mooncen-ops-api.service",
)
HOST_CONSUMER_UNITS = (
    *SOCKET_BOUNDARY_UNITS,
    *CONTROL_UNITS,
    "mooncen-docker-dev.service",
)
NATIVE_UNITS = ("mooncen-api.service", "mooncen-frontend.service")

NATIVE_STATUS = {
    "docker_active": False,
    "docker_enabled": False,
    "marker": False,
    "native_active": list(NATIVE_UNITS),
    "native_enabled": list(NATIVE_UNITS),
    "schema_version": 1,
}
DOCKER_STATUS = {
    "docker_active": True,
    "docker_enabled": True,
    "marker": True,
    "native_active": [],
    "native_enabled": [],
    "schema_version": 1,
}


class TransitionError(RuntimeError):
    """Raised when the host-layer transition cannot safely converge."""


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        del req, fp, code, msg, headers, newurl
        return None


@dataclasses.dataclass(frozen=True)
class ManifestEntry:
    label: str
    relative: str
    installed: Path
    mode: int


@dataclasses.dataclass(frozen=True)
class PairContext:
    name: str
    pair: Path
    control: Path
    docker: Path
    receipt: dict[str, Any]
    receipt_sha256: str
    manifest: tuple[ManifestEntry, ...]
    manifest_sha256: str
    host_layer_sha256: str


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
            raise TransitionError("JSON object contains duplicate keys")
        value[key] = item
    return value


def _safe_directory(path: Path, *, uid: int, gid: int, mode: int) -> Path:
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise TransitionError(f"directory is unavailable: {path}") from exc
    if (
        path.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != uid
        or metadata.st_gid != gid
        or stat.S_IMODE(metadata.st_mode) != mode
    ):
        raise TransitionError(f"directory metadata is unsafe: {path}")
    return resolved


def _safe_file(
    path: Path,
    *,
    uid: int,
    gid: int,
    mode: int,
    maximum: int = MAX_CONTROL_FILE_SIZE,
    allow_empty: bool = False,
) -> bytes:
    try:
        metadata = path.lstat()
        payload = path.read_bytes()
    except OSError as exc:
        raise TransitionError(f"file is unavailable: {path}") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != uid
        or metadata.st_gid != gid
        or stat.S_IMODE(metadata.st_mode) != mode
        or (not allow_empty and not payload)
        or len(payload) > maximum
    ):
        raise TransitionError(f"file metadata is unsafe: {path}")
    return payload


def _load_canonical(
    path: Path,
    *,
    uid: int | None = None,
    gid: int | None = None,
    mode: int = 0o600,
) -> tuple[dict[str, Any], bytes]:
    if uid is None:
        uid = ROOT_UID
    if gid is None:
        gid = ROOT_GID
    payload = _safe_file(
        path,
        uid=uid,
        gid=gid,
        mode=mode,
        maximum=MAX_JSON_SIZE,
    )
    try:
        value = json.loads(payload.decode("ascii"), object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError) as exc:
        raise TransitionError(f"JSON is invalid: {path}") from exc
    if not isinstance(value, dict) or payload != _canonical_json(value):
        raise TransitionError(f"JSON is not canonical: {path}")
    return value, payload


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, payload: bytes, *, mode: int, create: bool = False) -> None:
    _safe_directory(path.parent, uid=ROOT_UID, gid=ROOT_GID, mode=0o700)
    if create and (path.exists() or path.is_symlink()):
        raise TransitionError(f"transaction destination already exists: {path}")
    stage = path.parent / f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    descriptor = os.open(
        stage,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        mode,
    )
    try:
        os.fchown(descriptor, ROOT_UID, ROOT_GID)
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(stage, path)
        _fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with contextlib.suppress(FileNotFoundError):
            stage.unlink()


def _validate_pair_name(name: str) -> re.Match[str]:
    match = PAIR_PATTERN.fullmatch(name)
    if match is None:
        raise TransitionError("runtime pair name is invalid")
    return match


def _operator_gid() -> int:
    try:
        return grp.getgrnam("mooncen_docker_operator").gr_gid
    except KeyError as exc:
        raise TransitionError("Docker operator group is unavailable") from exc


def _inventory(root: Path) -> str:
    script = root / "deploy/docker/native_baseline.py"
    _safe_file(script, uid=ROOT_UID, gid=ROOT_GID, mode=0o644)
    result = _run(
        (PYTHON, "-I", str(script), "--root", str(root)),
        timeout=900,
        check=False,
    )
    digest = result.stdout.decode("ascii", errors="ignore").strip()
    if result.returncode != 0 or result.stderr or SHA256_PATTERN.fullmatch(digest) is None:
        raise TransitionError("runtime inventory could not be verified")
    return digest


def _load_manifest(control: Path) -> tuple[ManifestEntry, ...]:
    manager_source = control / "deploy/an2p/runtime_pair_manager.py"
    _safe_file(manager_source, uid=ROOT_UID, gid=ROOT_GID, mode=0o644)
    module_name = f"mooncen_host_transition_pair_manager_{secrets.token_hex(8)}"
    spec = importlib.util.spec_from_file_location(module_name, manager_source)
    if spec is None or spec.loader is None:
        raise TransitionError("pair manager manifest cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        raw_entries = tuple(module.HOST_LAYER_FILES)
    except Exception as exc:  # reviewed pair code; normalize every load failure
        raise TransitionError("pair manager manifest cannot be loaded") from exc
    if not raw_entries or len(raw_entries) > 64:
        raise TransitionError("host-layer manifest size is invalid")
    entries: list[ManifestEntry] = []
    seen_labels: set[str] = set()
    seen_installed: set[Path] = set()
    for raw in raw_entries:
        if not isinstance(raw, tuple) or len(raw) != 4:
            raise TransitionError("host-layer manifest entry is invalid")
        label, relative, installed, mode = raw
        if (
            not isinstance(label, str)
            or LABEL_PATTERN.fullmatch(label) is None
            or label in seen_labels
            or not isinstance(relative, str)
            or RELATIVE_PATTERN.fullmatch(relative) is None
            or Path(relative).is_absolute()
            or any(part in {"", ".", ".."} for part in Path(relative).parts)
            or not isinstance(installed, Path)
            or not installed.is_absolute()
            or installed in seen_installed
            or type(mode) is not int
            or mode not in {0o644, 0o755}
        ):
            raise TransitionError("host-layer manifest entry is unsafe")
        expected_mode: int | None = None
        if installed.parent == Path("/usr/local/libexec"):
            expected_mode = 0o755
        elif installed.parent == SYSTEM_UNIT_ROOT and installed.name.endswith((".service", ".socket")):
            expected_mode = 0o644
        if expected_mode is None or mode != expected_mode:
            raise TransitionError("host-layer installed destination is outside policy")
        seen_labels.add(label)
        seen_installed.add(installed)
        entries.append(ManifestEntry(label, relative, installed, mode))
    return tuple(entries)


def _manifest_payload(manifest: tuple[ManifestEntry, ...]) -> bytes:
    return _canonical_json(
        {
            "files": [
                {
                    "installed": str(entry.installed),
                    "label": entry.label,
                    "mode": f"{entry.mode:04o}",
                    "relative": entry.relative,
                }
                for entry in manifest
            ]
        }
    )


def _source_host_digest(control: Path, manifest: tuple[ManifestEntry, ...]) -> str:
    records: list[dict[str, str]] = []
    for entry in manifest:
        payload = _safe_file(
            control / entry.relative,
            uid=ROOT_UID,
            gid=ROOT_GID,
            mode=0o644,
        )
        records.append(
            {
                "label": entry.label,
                "mode": f"{entry.mode:04o}",
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return hashlib.sha256(_canonical_json({"files": records})).hexdigest()


def _load_pair(name: str) -> PairContext:
    _validate_pair_name(name)
    pair = _safe_directory(PAIR_RELEASES / name, uid=ROOT_UID, gid=ROOT_GID, mode=0o755)
    control = _safe_directory(pair / "control", uid=ROOT_UID, gid=ROOT_GID, mode=0o755)
    docker = _safe_directory(pair / "docker", uid=ROOT_UID, gid=_operator_gid(), mode=0o750)
    receipt, receipt_payload = _load_canonical(pair / ".pair-receipt.json")
    if (
        frozenset(receipt) != PAIR_RECEIPT_KEYS
        or receipt.get("schema_version") != 1
        or receipt.get("pair_name") != name
        or re.fullmatch(r"[0-9a-f]{40}", str(receipt.get("commit", ""))) is None
        or re.fullmatch(r"[0-9a-f]{40}", str(receipt.get("source_tree", ""))) is None
        or any(
            SHA256_PATTERN.fullmatch(str(receipt.get(key, ""))) is None
            for key in PAIR_RECEIPT_KEYS - {"schema_version", "pair_name", "commit", "source_tree"}
        )
    ):
        raise TransitionError("runtime pair receipt is invalid")
    unsigned = {key: value for key, value in receipt.items() if key != "receipt_digest"}
    if hashlib.sha256(_canonical_json(unsigned)).hexdigest() != receipt["receipt_digest"]:
        raise TransitionError("runtime pair receipt digest is invalid")
    if _inventory(control) != receipt["control_inventory_sha256"]:
        raise TransitionError("control runtime inventory drifted")
    if _inventory(docker) != receipt["docker_inventory_sha256"]:
        raise TransitionError("Docker runtime inventory drifted")
    manifest = _load_manifest(control)
    manifest_sha = hashlib.sha256(_manifest_payload(manifest)).hexdigest()
    host_sha = _source_host_digest(control, manifest)
    if host_sha != receipt["host_layer_sha256"]:
        raise TransitionError("pair host-layer receipt drifted")
    return PairContext(
        name=name,
        pair=pair,
        control=control,
        docker=docker,
        receipt=receipt,
        receipt_sha256=hashlib.sha256(receipt_payload).hexdigest(),
        manifest=manifest,
        manifest_sha256=manifest_sha,
        host_layer_sha256=host_sha,
    )


def _require_matching_manifests(previous: PairContext, target: PairContext) -> None:
    if previous.manifest != target.manifest or (previous.manifest_sha256 != target.manifest_sha256):
        raise TransitionError("host-layer manifest changed; a different reviewed transition schema is required")


def _run(arguments: Sequence[str], *, timeout: int = 300, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    try:
        completed = subprocess.run(
            tuple(arguments),
            cwd="/",
            env={
                "HOME": "/root",
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise TransitionError(f"command could not run: {arguments[0]}") from exc
    if check and completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise TransitionError(f"command failed: {arguments[0]}{': ' + detail if detail else ''}")
    return completed


def _systemctl(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return _run((SYSTEMCTL, *arguments), check=check)


def _unit_active(unit: str) -> bool:
    completed = _systemctl("is-active", unit, check=False)
    state = completed.stdout.decode("ascii", errors="ignore").strip()
    if state == "active" and completed.returncode == 0:
        return True
    if state in {"inactive", "failed"} and completed.returncode != 0:
        return False
    raise TransitionError(f"systemd active state is unsafe: {unit}")


def _unit_enabled(unit: str) -> bool:
    completed = _systemctl("is-enabled", unit, check=False)
    state = completed.stdout.decode("ascii", errors="ignore").strip()
    if state == "enabled" and completed.returncode == 0:
        return True
    if state in {"disabled", "masked", "masked-runtime"} and completed.returncode != 0:
        return False
    raise TransitionError(f"systemd enablement state is unsafe: {unit}")


def _user_unit_active(unit: str) -> bool:
    completed = _systemctl(
        "--user",
        "--machine=sgm@",
        "is-active",
        unit,
        check=False,
    )
    state = completed.stdout.decode("ascii", errors="ignore").strip()
    if state == "active" and completed.returncode == 0:
        return True
    if state in {"inactive", "failed"} and completed.returncode != 0:
        return False
    raise TransitionError(f"user systemd active state is unsafe: {unit}")


def _user_unit_enabled(unit: str) -> bool:
    completed = _systemctl(
        "--user",
        "--machine=sgm@",
        "is-enabled",
        unit,
        check=False,
    )
    state = completed.stdout.decode("ascii", errors="ignore").strip()
    if state == "enabled" and completed.returncode == 0:
        return True
    if state in {"disabled", "masked", "masked-runtime"} and completed.returncode != 0:
        return False
    raise TransitionError(f"user systemd enablement state is unsafe: {unit}")


def _disable_unit(unit: str) -> None:
    _systemctl("disable", "--now", unit)
    _systemctl("reset-failed", unit, check=False)
    if _unit_active(unit) or _unit_enabled(unit):
        raise TransitionError(f"host consumer did not quiesce: {unit}")


def _verify_consumers_quiescent() -> None:
    for unit in HOST_CONSUMER_UNITS:
        if _unit_active(unit) or _unit_enabled(unit):
            raise TransitionError(f"host consumer is live without a pair: {unit}")


def _selector_status() -> dict[str, Any]:
    payload = _run((str(INSTALLED_SELECTOR), "runtime-status")).stdout
    try:
        value = json.loads(payload.decode("ascii"), object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError) as exc:
        raise TransitionError("development selector status is invalid") from exc
    if not isinstance(value, dict) or payload != _canonical_json(value):
        raise TransitionError("development selector status is not canonical")
    return value


def _wait_http(url: str, *, timeout: int = 180) -> None:
    deadline = time.monotonic() + timeout
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _RejectRedirects(),
    )
    while time.monotonic() < deadline:
        try:
            request = urllib.request.Request(url, method="GET")
            with opener.open(request, timeout=3) as response:  # noqa: S310 - fixed loopback URLs only.
                body = response.read(1024 * 1024 + 1)
                content_type = response.headers.get_content_type()
                if (
                    response.status == 200
                    and response.geturl() == url
                    and len(body) <= 1024 * 1024
                    and (
                        (
                            url.endswith("/health")
                            and content_type == "application/json"
                            and body == b'{"status":"ready"}'
                        )
                        or (not url.endswith("/health") and content_type == "text/html" and bool(body))
                    )
                ):
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(1)
    raise TransitionError(f"development health endpoint did not recover: {url}")


def _verify_native_runtime(*, use_selector: bool = True) -> None:
    if use_selector:
        if _selector_status() != NATIVE_STATUS:
            raise TransitionError("native development selector did not converge")
    else:
        for unit in NATIVE_UNITS:
            if not _user_unit_active(unit) or not _user_unit_enabled(unit):
                raise TransitionError("native development user units are not persistent and active")
    _wait_http("http://127.0.0.1:8001/health")
    _wait_http("http://127.0.0.1:5174")


def _current_pair() -> str | None:
    if not PAIR_CURRENT.exists() and not PAIR_CURRENT.is_symlink():
        return None
    try:
        metadata = PAIR_CURRENT.lstat()
        target = os.readlink(PAIR_CURRENT)
    except OSError as exc:
        raise TransitionError("runtime pair pointer is unavailable") from exc
    if (
        not stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != ROOT_UID
        or metadata.st_gid != ROOT_GID
        or not target.startswith("releases/")
        or PAIR_PATTERN.fullmatch(target.removeprefix("releases/")) is None
    ):
        raise TransitionError("runtime pair pointer is unsafe")
    return target.removeprefix("releases/")


def _entry_payload(context: PairContext, entry: ManifestEntry) -> bytes:
    return _safe_file(
        context.control / entry.relative,
        uid=ROOT_UID,
        gid=ROOT_GID,
        mode=0o644,
    )


def _installed_payload(entry: ManifestEntry) -> bytes:
    return _safe_file(
        entry.installed,
        uid=ROOT_UID,
        gid=ROOT_GID,
        mode=entry.mode,
    )


def _installed_host_state(previous: PairContext, target: PairContext) -> str:
    old_count = 0
    target_count = 0
    for old_entry, target_entry in zip(previous.manifest, target.manifest, strict=True):
        if old_entry != target_entry:
            raise TransitionError("host-layer manifests diverged during transition")
        installed = _installed_payload(old_entry)
        old_payload = _entry_payload(previous, old_entry)
        target_payload = _entry_payload(target, target_entry)
        matches_old = installed == old_payload
        matches_target = installed == target_payload
        if not matches_old and not matches_target:
            raise TransitionError(f"installed host byte is neither endpoint: {old_entry.label}")
        old_count += int(matches_old)
        target_count += int(matches_target)
    total = len(previous.manifest)
    if target_count == total:
        return "target"
    if old_count == total:
        return "old"
    return "mixed"


def _atomic_install_entry(source: bytes, entry: ManifestEntry) -> None:
    parent = entry.installed.parent
    expected_parent_mode = 0o755
    _safe_directory(parent, uid=ROOT_UID, gid=ROOT_GID, mode=expected_parent_mode)
    directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    stage_name = f".{entry.installed.name}.host-transition.{os.getpid()}.{secrets.token_hex(8)}"
    descriptor = -1
    try:
        descriptor = os.open(
            stage_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            entry.mode,
            dir_fd=directory,
        )
        os.fchown(descriptor, ROOT_UID, ROOT_GID)
        os.fchmod(descriptor, entry.mode)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(source)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(
            stage_name,
            entry.installed.name,
            src_dir_fd=directory,
            dst_dir_fd=directory,
        )
        os.fsync(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with contextlib.suppress(FileNotFoundError):
            os.unlink(stage_name, dir_fd=directory)
        os.close(directory)


def _install_target_host(previous: PairContext, target: PairContext) -> None:
    for old_entry, target_entry in zip(previous.manifest, target.manifest, strict=True):
        if old_entry != target_entry:
            raise TransitionError("host-layer manifests diverged during install")
        installed = _installed_payload(old_entry)
        old_payload = _entry_payload(previous, old_entry)
        target_payload = _entry_payload(target, target_entry)
        if installed == target_payload:
            continue
        if installed != old_payload:
            raise TransitionError(f"installed host byte is unsafe: {old_entry.label}")
        _atomic_install_entry(target_payload, target_entry)
    if _installed_host_state(previous, target) != "target":
        raise TransitionError("target host layer did not converge")


def _verify_exact_manager(context: PairContext) -> None:
    entries = [entry for entry in context.manifest if entry.label == "runtime_manager"]
    if len(entries) != 1 or entries[0].installed != INSTALLED_MANAGER:
        raise TransitionError("runtime manager manifest identity is invalid")
    if _installed_payload(entries[0]) != _entry_payload(context, entries[0]):
        raise TransitionError("installed runtime manager is not the exact pair manager")


def _verify_exact_selector(context: PairContext) -> None:
    entries = [entry for entry in context.manifest if entry.label == "service_selector"]
    if len(entries) != 1 or entries[0].installed != INSTALLED_SELECTOR:
        raise TransitionError("service selector manifest identity is invalid")
    if _installed_payload(entries[0]) != _entry_payload(context, entries[0]):
        raise TransitionError("installed service selector is not the exact pair selector")


def _run_manager(context: PairContext, *arguments: str) -> dict[str, Any]:
    _verify_exact_manager(context)
    completed = _run((str(INSTALLED_MANAGER), *arguments), timeout=1800)
    try:
        value = json.loads(completed.stdout.decode("ascii"), object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError) as exc:
        raise TransitionError("runtime manager result is invalid") from exc
    if not isinstance(value, dict) or completed.stdout != _canonical_json(value):
        raise TransitionError("runtime manager result is not canonical")
    return value


def _run_selector(context: PairContext, action: str) -> dict[str, Any]:
    if action not in {"native-select", "runtime-status"}:
        raise TransitionError("host transition selector action is outside policy")
    _verify_exact_selector(context)
    completed = _run((str(INSTALLED_SELECTOR), action), timeout=1800)
    if completed.stderr:
        raise TransitionError("development selector emitted an error")
    try:
        value = json.loads(completed.stdout.decode("ascii"), object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError) as exc:
        raise TransitionError("development selector result is invalid") from exc
    if not isinstance(value, dict) or completed.stdout != _canonical_json(value):
        raise TransitionError("development selector result is not canonical")
    return value


def _pair_transaction_exists() -> bool:
    return PAIR_TRANSACTION.exists() or PAIR_TRANSACTION.is_symlink()


def _converge_previous_no_pair(previous: PairContext) -> None:
    if _pair_transaction_exists():
        raise TransitionError("a runtime pair transaction blocks host maintenance")
    for unit in SOCKET_BOUNDARY_UNITS:
        _disable_unit(unit)
    for unit in CONTROL_UNITS:
        _disable_unit(unit)
    _disable_unit("mooncen-docker-dev.service")
    _systemctl("mask", "--runtime", "--now", "mooncen-ops-api.service")
    _systemctl("reset-failed", "mooncen-ops-api.service", check=False)
    if _run_selector(previous, "native-select") != NATIVE_STATUS:
        raise TransitionError("previous selector did not restore native development")
    if _current_pair() is not None:
        raise TransitionError("runtime pair pointer returned during native recovery")
    _verify_consumers_quiescent()
    _verify_native_runtime()


def _deactivate_previous(previous: PairContext) -> None:
    for unit in SOCKET_BOUNDARY_UNITS:
        _disable_unit(unit)
    for unit in CONTROL_UNITS[:-1]:
        _disable_unit(unit)
    # A previous generation may not understand LoadState=masked.  With both
    # listener sockets already down it is safe to expose the disabled unit just
    # long enough for that exact manager to own the deactivation transaction.
    _systemctl("unmask", "--runtime", "mooncen-ops-api.service", check=False)
    _disable_unit("mooncen-ops-api.service")
    try:
        result = _run_manager(previous, "deactivate-initial", previous.name)
    except TransitionError as deactivation_error:
        if _pair_transaction_exists():
            raise TransitionError(
                "previous deactivation failed with a runtime transaction pending"
            ) from deactivation_error
        pointer = _current_pair()
        if pointer is None:
            _converge_previous_no_pair(previous)
            return
        if pointer != previous.name:
            raise TransitionError("previous deactivation failed at an unknown runtime pointer") from deactivation_error
        try:
            restored = _run_manager(
                previous,
                "activate-development",
                previous.name,
            )
            if restored.get("active_pair") != previous.name:
                raise TransitionError("previous development restoration result is invalid")
            if _run_selector(previous, "runtime-status") != DOCKER_STATUS:
                raise TransitionError("previous development restoration did not converge")
            if not _unit_active("mooncen-docker-dev.service") or not _unit_enabled("mooncen-docker-dev.service"):
                raise TransitionError("previous Docker development service was not restored")
            _wait_http("http://127.0.0.1:8001/health")
            _wait_http("http://127.0.0.1:5174")
            _systemctl("mask", "--runtime", "--now", "mooncen-ops-api.service")
        except TransitionError as restoration_error:
            raise TransitionError(
                "previous deactivation failed and development restoration failed"
            ) from restoration_error
        raise TransitionError("previous deactivation failed; previous development was restored") from deactivation_error
    if result.get("deactivated_pair") != previous.name:
        raise TransitionError("previous pair deactivation result is invalid")
    _systemctl("mask", "--runtime", "--now", "mooncen-ops-api.service")
    _systemctl("reset-failed", "mooncen-ops-api.service", check=False)
    if _current_pair() is not None:
        raise TransitionError("previous pair pointer remains after deactivation")
    _verify_consumers_quiescent()
    _verify_native_runtime()


def _quiesce_target_residue(target: PairContext) -> None:
    if _pair_transaction_exists():
        _run_manager(target, "recover")
        if _current_pair() is not None:
            raise TransitionError("target pair transaction did not restore no-pair state")
    for unit in HOST_CONSUMER_UNITS:
        _disable_unit(unit)
    _systemctl("mask", "--runtime", "--now", "mooncen-ops-api.service")
    if _run_selector(target, "runtime-status") != NATIVE_STATUS:
        if _run_selector(target, "native-select") != NATIVE_STATUS:
            raise TransitionError("target selector did not restore native development")
    _verify_consumers_quiescent()
    _verify_native_runtime()


def _validate_target_manager(target: PairContext) -> None:
    result = _run_manager(target, "validate", target.name)
    if result.get("pair") != target.name or result.get("valid") is not True:
        raise TransitionError("target manager validation result is invalid")


def _activate_target(target: PairContext) -> None:
    _validate_target_manager(target)
    prepare = target.control / "deploy/an2p/install_development_runtime.sh"
    _safe_file(prepare, uid=ROOT_UID, gid=ROOT_GID, mode=0o755)
    _run((BASH, str(prepare), "--prepare", "--pair", target.name), timeout=1800)
    result = _run_manager(target, "activate-development", target.name)
    if result.get("active_pair") != target.name:
        raise TransitionError("target activation result is invalid")


def _verify_pending_target(target: PairContext) -> None:
    value, _payload = _load_canonical(PENDING_CONTROL_FINALIZATION)
    if (
        frozenset(value) != PENDING_KEYS
        or value.get("schema_version") != 1
        or value.get("pair") != target.name
        or value.get("source_tree") != target.receipt["source_tree"]
        or value.get("environment") != "development"
        or value.get("target") != "an2p-dev"
    ):
        raise TransitionError("target pending finalization receipt is invalid")


def _verify_target_runtime(previous: PairContext, target: PairContext) -> None:
    if _current_pair() != target.name:
        raise TransitionError("target pair is not active")
    if _installed_host_state(previous, target) != "target":
        raise TransitionError("target pointer does not match the installed host layer")
    if _pair_transaction_exists():
        raise TransitionError("target pair transaction remains incomplete")
    _validate_target_manager(target)
    if _selector_status() != DOCKER_STATUS:
        raise TransitionError("Docker development selector did not converge")
    if not _unit_active("mooncen-docker-dev.service") or not _unit_enabled("mooncen-docker-dev.service"):
        raise TransitionError("Docker development service is not persistent and active")
    for unit in CONTROL_UNITS:
        if _unit_active(unit) or _unit_enabled(unit):
            raise TransitionError(f"unfinalized control consumer is live: {unit}")
    _verify_pending_target(target)
    _wait_http("http://127.0.0.1:8001/health")
    _wait_http("http://127.0.0.1:5174")


def _publish_journal_expected(
    target_pair: str,
    previous_pair: str,
    previous_host_layer: str,
) -> dict[str, Any]:
    match = _validate_pair_name(target_pair)
    _validate_pair_name(previous_pair)
    if SHA256_PATTERN.fullmatch(previous_host_layer) is None:
        raise TransitionError("previous host-layer SHA-256 is invalid")
    return {
        "build_policy_sha256": match.group(3),
        "commit": match.group(1),
        "host_transition": True,
        "pair_name": target_pair,
        "schema_version": 1,
        "source_tree": match.group(2),
        "transition_from_host_layer": previous_host_layer,
        "transition_from_pair": previous_pair,
    }


def _load_publish_journal(
    path: Path,
    target_pair: str,
    previous_pair: str,
    previous_host_layer: str,
) -> tuple[dict[str, Any], bytes]:
    expected_path = STATE_ROOT / "install-transaction.json"
    if not path.is_absolute() or path != expected_path:
        raise TransitionError("install publication journal path is outside policy")
    value, payload = _load_canonical(path)
    if frozenset(value) != PUBLISH_KEYS or value != _publish_journal_expected(
        target_pair,
        previous_pair,
        previous_host_layer,
    ):
        raise TransitionError("install publication journal does not bind the target pair")
    return value, payload


def _recovery_unit_payload() -> bytes:
    lines = (
        "[Unit]",
        "Description=Recover a reviewed MoonCen an2p host-layer transition",
        "After=local-fs.target docker.service network-online.target",
        "Wants=docker.service network-online.target",
        "Before=" + " ".join(HOST_CONSUMER_UNITS),
        "RequiresMountsFor=/opt/mooncen-an2p-runtime /var/lib/mooncen-an2p-runtime",
        "",
        "[Service]",
        "Type=oneshot",
        f"ExecStart=/usr/bin/env -i HOME=/root LANG=C LC_ALL=C PATH=/usr/sbin:/usr/bin:/sbin:/bin PYTHONDONTWRITEBYTECODE=1 {INSTALLED_HELPER} recover --boot-fence",
        f"ExecStartPost={SYSTEMCTL} --no-block start {CONTINUATION_UNIT_NAME}",
        "RemainAfterExit=yes",
        "Restart=no",
        "TimeoutStartSec=infinity",
        "UMask=0077",
        "PrivateTmp=true",
        "ProtectHome=true",
        "ProtectKernelTunables=true",
        "ProtectKernelModules=true",
        "ProtectControlGroups=true",
        "RestrictSUIDSGID=true",
        "LockPersonality=true",
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        "RequiredBy=" + " ".join(HOST_CONSUMER_UNITS),
    )
    return ("\n".join(lines) + "\n").encode("ascii")


def _continuation_unit_payload() -> bytes:
    lines = (
        "[Unit]",
        "Description=Continue a fenced MoonCen an2p host-layer transition",
        f"After={RECOVERY_UNIT_NAME} docker.service network-online.target",
        "Wants=docker.service network-online.target",
        "RequiresMountsFor=/home/sgm /opt/mooncen-an2p-runtime /var/lib/mooncen-an2p-runtime",
        "",
        "[Service]",
        "Type=oneshot",
        f"ExecStart=/usr/bin/env -i HOME=/root LANG=C LC_ALL=C PATH=/usr/sbin:/usr/bin:/sbin:/bin PYTHONDONTWRITEBYTECODE=1 {INSTALLED_HELPER} recover",
        "Restart=no",
        "TimeoutStartSec=infinity",
        "UMask=0077",
        "PrivateTmp=true",
        "ProtectKernelTunables=true",
        "ProtectKernelModules=true",
        "ProtectControlGroups=true",
        "RestrictSUIDSGID=true",
        "LockPersonality=true",
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
    )
    return ("\n".join(lines) + "\n").encode("ascii")


def _install_recovery_unit_file(path: Path, payload: bytes) -> None:
    if path.exists() or path.is_symlink():
        existing = _safe_file(
            path,
            uid=ROOT_UID,
            gid=ROOT_GID,
            mode=0o644,
            maximum=MAX_JSON_SIZE,
        )
        if existing != payload:
            raise TransitionError(f"host transition recovery unit drifted: {path.name}")
        return
    stage = SYSTEM_UNIT_ROOT / f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    descriptor = os.open(
        stage,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o644,
    )
    try:
        os.fchown(descriptor, ROOT_UID, ROOT_GID)
        os.fchmod(descriptor, 0o644)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(stage, path)
        _fsync_directory(SYSTEM_UNIT_ROOT)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with contextlib.suppress(FileNotFoundError):
            stage.unlink()


def _install_recovery_unit_files() -> None:
    _safe_directory(SYSTEM_UNIT_ROOT, uid=ROOT_UID, gid=ROOT_GID, mode=0o755)
    _install_recovery_unit_file(RECOVERY_UNIT, _recovery_unit_payload())
    _install_recovery_unit_file(CONTINUATION_UNIT, _continuation_unit_payload())
    _systemctl("daemon-reload")


def _recovery_enablement_links() -> tuple[Path, ...]:
    return (
        SYSTEM_UNIT_ROOT / "multi-user.target.wants" / RECOVERY_UNIT_NAME,
        *(
            SYSTEM_UNIT_ROOT / f"{unit}.requires" / RECOVERY_UNIT_NAME
            for unit in HOST_CONSUMER_UNITS
        ),
    )


def _verify_recovery_enablement(*, enabled: bool) -> None:
    links = _recovery_enablement_links()
    for link in links:
        try:
            metadata = link.lstat()
        except FileNotFoundError:
            if enabled:
                raise TransitionError(f"host transition recovery dependency is absent: {link.parent.name}")
            continue
        except OSError as exc:
            raise TransitionError("host transition recovery dependency is unavailable") from exc
        if not enabled:
            raise TransitionError(f"host transition recovery dependency remains: {link.parent.name}")
        if (
            not stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != ROOT_UID
            or metadata.st_gid != ROOT_GID
            or os.readlink(link) != str(RECOVERY_UNIT)
        ):
            raise TransitionError(f"host transition recovery dependency is unsafe: {link.parent.name}")
        _safe_directory(link.parent, uid=ROOT_UID, gid=ROOT_GID, mode=0o755)
    # systemctl enable/disable writes symlinks rather than regular files.  The
    # parent-directory fsyncs are the durability boundary before host bytes or
    # the active pair can be changed.
    for parent in {link.parent for link in links if link.parent.exists()}:
        _fsync_directory(parent)
    _fsync_directory(SYSTEM_UNIT_ROOT)


def _publish_recovery_enablement() -> None:
    for link in _recovery_enablement_links():
        try:
            link.parent.mkdir(mode=0o755)
        except FileExistsError:
            pass
        except OSError as exc:
            raise TransitionError("host transition recovery dependency directory is unavailable") from exc
        _safe_directory(link.parent, uid=ROOT_UID, gid=ROOT_GID, mode=0o755)
        try:
            link.symlink_to(RECOVERY_UNIT)
        except FileExistsError:
            pass
        except OSError as exc:
            raise TransitionError("host transition recovery dependency could not be published") from exc
    _verify_recovery_enablement(enabled=True)


def _remove_recovery_enablement() -> None:
    for link in _recovery_enablement_links():
        if not link.exists() and not link.is_symlink():
            continue
        try:
            metadata = link.lstat()
        except OSError as exc:
            raise TransitionError("host transition recovery dependency is unavailable") from exc
        if (
            not stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != ROOT_UID
            or metadata.st_gid != ROOT_GID
            or os.readlink(link) != str(RECOVERY_UNIT)
        ):
            raise TransitionError(f"host transition recovery dependency is unsafe: {link.parent.name}")
        link.unlink()
    _verify_recovery_enablement(enabled=False)


def _enable_recovery_unit() -> None:
    # Publish and fsync the boot graph without reloading the live manager.
    # Loading RequiredBy while this foreground transition runs would make a
    # synchronous TARGET start pull in the inactive fence and wait on this
    # helper's own lock.  A reboot reads these exact durable links before any
    # consumer job is queued.
    _publish_recovery_enablement()


def _install_recovery_unit() -> None:
    _install_recovery_unit_files()
    _enable_recovery_unit()


def _remove_recovery_unit() -> None:
    files = (
        (RECOVERY_UNIT, _recovery_unit_payload()),
        (CONTINUATION_UNIT, _continuation_unit_payload()),
    )
    present: list[Path] = []
    for path, expected in files:
        if not path.exists() and not path.is_symlink():
            continue
        payload = _safe_file(
            path,
            uid=ROOT_UID,
            gid=ROOT_GID,
            mode=0o644,
            maximum=MAX_JSON_SIZE,
        )
        if payload != expected:
            raise TransitionError(f"host transition recovery unit drifted: {path.name}")
        present.append(path)
    links_present = any(
        link.exists() or link.is_symlink()
        for link in _recovery_enablement_links()
    )
    if not present and not links_present:
        return
    _remove_recovery_enablement()
    _systemctl("daemon-reload")
    # RemainAfterExit keeps the successful boot fence active while the
    # continuation enables TARGET consumers.  Remove reverse requirements and
    # reload PID 1 before stopping the fence so those consumers stay live.
    _systemctl("stop", RECOVERY_UNIT_NAME, check=False)
    _systemctl("reset-failed", RECOVERY_UNIT_NAME, check=False)
    for path in present:
        path.unlink()
    _fsync_directory(SYSTEM_UNIT_ROOT)
    _systemctl("daemon-reload")


def _validate_transition_value(value: dict[str, Any]) -> None:
    if (
        frozenset(value) != TRANSITION_KEYS
        or value.get("schema_version") != 1
        or value.get("phase") not in PHASES
        or PAIR_PATTERN.fullmatch(str(value.get("previous_pair", ""))) is None
        or PAIR_PATTERN.fullmatch(str(value.get("target_pair", ""))) is None
        or value.get("previous_pair") == value.get("target_pair")
        or any(
            SHA256_PATTERN.fullmatch(str(value.get(key, ""))) is None
            for key in (
                "manifest_sha256",
                "previous_host_layer_sha256",
                "previous_receipt_sha256",
                "publish_journal_sha256",
                "target_host_layer_sha256",
                "target_receipt_sha256",
            )
        )
        or value.get("previous_host_layer_sha256") == value.get("target_host_layer_sha256")
        or not isinstance(value.get("publish_journal"), str)
    ):
        raise TransitionError("host transition journal schema is invalid")
    publish_path = Path(value["publish_journal"])
    if not publish_path.is_absolute() or publish_path != STATE_ROOT / "install-transaction.json":
        raise TransitionError("host transition publication path is invalid")


def _write_transition(value: dict[str, Any], *, create: bool = False) -> None:
    _validate_transition_value(value)
    _atomic_write(
        TRANSITION_JOURNAL,
        _canonical_json(value),
        mode=0o600,
        create=create,
    )


def _load_transition() -> dict[str, Any] | None:
    if not TRANSITION_JOURNAL.exists() and not TRANSITION_JOURNAL.is_symlink():
        return None
    value, _payload = _load_canonical(TRANSITION_JOURNAL)
    _validate_transition_value(value)
    return value


def _advance(value: dict[str, Any], phase: str) -> dict[str, Any]:
    if phase not in PHASES:
        raise TransitionError("host transition phase is invalid")
    if PHASES.index(phase) < PHASES.index(str(value["phase"])):
        raise TransitionError("host transition phase cannot move backwards")
    if value["phase"] == phase:
        return value
    updated = dict(value)
    updated["phase"] = phase
    _write_transition(updated)
    return updated


def _contexts(value: dict[str, Any]) -> tuple[PairContext, PairContext]:
    previous = _load_pair(str(value["previous_pair"]))
    target = _load_pair(str(value["target_pair"]))
    _require_matching_manifests(previous, target)
    expected = {
        "manifest_sha256": previous.manifest_sha256,
        "previous_host_layer_sha256": previous.host_layer_sha256,
        "previous_receipt_sha256": previous.receipt_sha256,
        "target_host_layer_sha256": target.host_layer_sha256,
        "target_receipt_sha256": target.receipt_sha256,
    }
    if any(value[key] != item for key, item in expected.items()):
        raise TransitionError("host transition endpoints drifted from the journal")
    return previous, target


def _verify_publish_binding(value: dict[str, Any], *, allow_absent: bool) -> None:
    path = Path(str(value["publish_journal"]))
    if not path.exists() and not path.is_symlink():
        if allow_absent:
            return
        raise TransitionError("install publication journal disappeared before commit")
    _publish, payload = _load_publish_journal(
        path,
        str(value["target_pair"]),
        str(value["previous_pair"]),
        str(value["previous_host_layer_sha256"]),
    )
    if hashlib.sha256(payload).hexdigest() != value["publish_journal_sha256"]:
        raise TransitionError("install publication journal bytes drifted")


def _installed_helper_sha256() -> str:
    return hashlib.sha256(
        _safe_file(
            INSTALLED_HELPER,
            uid=ROOT_UID,
            gid=ROOT_GID,
            mode=0o755,
        )
    ).hexdigest()


def _committed_path(target_pair: str) -> Path:
    _validate_pair_name(target_pair)
    return COMMITTED_ROOT / f"{target_pair}.json"


def _committed_value(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "helper_sha256": _installed_helper_sha256(),
        "manifest_sha256": value["manifest_sha256"],
        "previous_host_layer_sha256": value["previous_host_layer_sha256"],
        "previous_pair": value["previous_pair"],
        "previous_receipt_sha256": value["previous_receipt_sha256"],
        "publish_journal_sha256": value["publish_journal_sha256"],
        "schema_version": 1,
        "state": "committed",
        "target_host_layer_sha256": value["target_host_layer_sha256"],
        "target_pair": value["target_pair"],
        "target_receipt_sha256": value["target_receipt_sha256"],
    }


def _validate_committed_value(value: dict[str, Any], *, target_pair: str) -> None:
    if (
        frozenset(value) != COMMITTED_KEYS
        or value.get("schema_version") != 1
        or value.get("state") != "committed"
        or value.get("target_pair") != target_pair
        or PAIR_PATTERN.fullmatch(str(value.get("previous_pair", ""))) is None
        or value.get("previous_pair") == target_pair
        or any(
            SHA256_PATTERN.fullmatch(str(value.get(key, ""))) is None
            for key in COMMITTED_KEYS
            - {
                "previous_pair",
                "schema_version",
                "state",
                "target_pair",
            }
        )
    ):
        raise TransitionError("committed host transition receipt is invalid")


def _load_committed_receipt(target_pair: str) -> dict[str, Any] | None:
    path = _committed_path(target_pair)
    if not COMMITTED_ROOT.exists() and not COMMITTED_ROOT.is_symlink():
        return None
    _safe_directory(COMMITTED_ROOT, uid=ROOT_UID, gid=ROOT_GID, mode=0o700)
    if not path.exists() and not path.is_symlink():
        return None
    value, _payload = _load_canonical(path)
    _validate_committed_value(value, target_pair=target_pair)
    return value


def _ensure_committed_root() -> None:
    try:
        COMMITTED_ROOT.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise TransitionError("committed host transition directory is unavailable") from exc
    _safe_directory(COMMITTED_ROOT, uid=ROOT_UID, gid=ROOT_GID, mode=0o700)
    _fsync_directory(STATE_ROOT)


def _write_committed_receipt(value: dict[str, Any]) -> dict[str, Any]:
    if value.get("phase") != "activated":
        raise TransitionError("host transition cannot commit before activation")
    expected = _committed_value(value)
    _validate_committed_value(expected, target_pair=str(value["target_pair"]))
    _ensure_committed_root()
    path = _committed_path(str(value["target_pair"]))
    existing = _load_committed_receipt(str(value["target_pair"]))
    if existing is not None:
        if existing != expected:
            raise TransitionError("committed host transition receipt drifted")
        return existing
    _atomic_write(path, _canonical_json(expected), mode=0o600, create=True)
    return expected


def _verify_committed_prepare(
    receipt: dict[str, Any],
    *,
    previous_pair: str,
    target_pair: str,
    previous_host_layer: str,
    target_host_layer: str,
) -> dict[str, Any]:
    previous = _load_pair(previous_pair)
    target = _load_pair(target_pair)
    _require_matching_manifests(previous, target)
    # The receipt records the reviewed helper that performed the commit, but a
    # later reviewed bootstrap is allowed once every transition journal and
    # unit residue has gone. The current helper must still be a safe installed
    # executable; its byte identity is not an endpoint property of the already
    # committed pair. Schema 1 is verified by this implementation together
    # with every immutable endpoint and the live target runtime below.
    _installed_helper_sha256()
    expected = {
        "helper_sha256": receipt["helper_sha256"],
        "manifest_sha256": previous.manifest_sha256,
        "previous_host_layer_sha256": previous_host_layer,
        "previous_pair": previous_pair,
        "previous_receipt_sha256": previous.receipt_sha256,
        "publish_journal_sha256": hashlib.sha256(
            _canonical_json(
                _publish_journal_expected(
                    target_pair,
                    previous_pair,
                    previous_host_layer,
                )
            )
        ).hexdigest(),
        "schema_version": 1,
        "state": "committed",
        "target_host_layer_sha256": target_host_layer,
        "target_pair": target_pair,
        "target_receipt_sha256": target.receipt_sha256,
    }
    if (
        receipt != expected
        or previous.host_layer_sha256 != previous_host_layer
        or target.host_layer_sha256 != target_host_layer
        or _current_pair() != target_pair
        or _installed_host_state(previous, target) != "target"
        or _pair_transaction_exists()
    ):
        raise TransitionError("committed host transition no longer matches its endpoints")
    _verify_target_runtime(previous, target)
    _remove_recovery_unit()
    return {
        "active_pair": target_pair,
        "host_transition": "committed",
        "schema_version": 1,
    }


def _remove_file_exact(path: Path, *, expected_sha256: str) -> None:
    payload = _safe_file(
        path,
        uid=ROOT_UID,
        gid=ROOT_GID,
        mode=0o600,
        maximum=MAX_JSON_SIZE,
    )
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise TransitionError(f"transaction file drifted before removal: {path}")
    path.unlink()
    _fsync_directory(path.parent)


def _commit(value: dict[str, Any]) -> None:
    _write_committed_receipt(value)
    publish = Path(str(value["publish_journal"]))
    if publish.exists() or publish.is_symlink():
        _remove_file_exact(
            publish,
            expected_sha256=str(value["publish_journal_sha256"]),
        )
    _remove_file_exact(
        TRANSITION_JOURNAL,
        expected_sha256=hashlib.sha256(_canonical_json(value)).hexdigest(),
    )
    _remove_recovery_unit()


def _converge(value: dict[str, Any]) -> dict[str, Any]:
    previous, target = _contexts(value)
    _verify_publish_binding(value, allow_absent=value["phase"] == "activated")

    while True:
        pointer = _current_pair()
        host_state = _installed_host_state(previous, target)
        phase = str(value["phase"])

        if pointer == previous.name:
            if phase != "prepared" or host_state != "old":
                raise TransitionError("old pair pointer is inconsistent with transition state")
            if _pair_transaction_exists():
                _run_manager(previous, "recover")
                continue
            _deactivate_previous(previous)
            value = _advance(value, "deactivated")
            continue

        if pointer is None:
            if host_state == "old":
                if phase not in {"prepared", "deactivated"}:
                    raise TransitionError("mixed host bytes appear after host installation commit")
                if _pair_transaction_exists():
                    _run_manager(previous, "recover")
                    continue
                _converge_previous_no_pair(previous)
                value = _advance(value, "deactivated")
                _install_target_host(previous, target)
                _systemctl("daemon-reload")
                value = _advance(value, "host-installed")
                continue
            if host_state == "mixed":
                if phase not in {"prepared", "deactivated"}:
                    raise TransitionError("mixed host bytes appear after host installation commit")
                if _pair_transaction_exists():
                    raise TransitionError("a runtime pair transaction crossed host-byte replacement")
                _verify_consumers_quiescent()
                # During a mixed replacement the selector itself may already be
                # the target byte, so prove native state through the sgm user
                # manager instead of executing either endpoint's mutable helper.
                _verify_native_runtime(use_selector=False)
                value = _advance(value, "deactivated")
                _install_target_host(previous, target)
                _systemctl("daemon-reload")
                value = _advance(value, "host-installed")
                continue
            if host_state != "target":
                raise TransitionError("no-pair host state is invalid")
            if phase in {"prepared", "deactivated"}:
                # The final atomic rename can reach disk before the phase and
                # daemon-reload do.  Repeating reload is the only safe proof
                # that systemd has consumed the exact target unit bytes.
                _systemctl("daemon-reload")
                value = _advance(value, "host-installed")
            _quiesce_target_residue(target)
            _activate_target(target)
            _verify_target_runtime(previous, target)
            value = _advance(value, "activated")
            continue

        if pointer == target.name:
            if host_state != "target":
                raise TransitionError("target pointer is bound to mixed host bytes")
            if _pair_transaction_exists():
                _run_manager(target, "recover")
                continue
            # The boot fence deliberately disables every host consumer,
            # including Docker. A crash after the target manager committed its
            # pointer and removed transaction.json therefore leaves a valid
            # TARGET pointer but a deliberately quiesced runtime. Normalize
            # that exact endpoint to native state, then replay the reviewed
            # development activation before declaring it healthy.
            if (
                _selector_status() != DOCKER_STATUS
                or not _unit_active("mooncen-docker-dev.service")
                or not _unit_enabled("mooncen-docker-dev.service")
            ):
                _quiesce_target_residue(target)
                _activate_target(target)
            _verify_target_runtime(previous, target)
            value = _advance(value, "activated")
            _commit(value)
            return {
                "active_pair": target.name,
                "host_transition": "committed",
                "schema_version": 1,
            }

        raise TransitionError("runtime pointer is outside the reviewed transition endpoints")


def _boot_fence_consumers() -> None:
    for unit in HOST_CONSUMER_UNITS:
        # A pending start job ordered after the fence must be replaced by a
        # synchronous stop before any journal or pair byte is inspected.
        _systemctl("disable", "--now", unit, check=False)
        _systemctl("stop", unit, check=False)
        _systemctl("reset-failed", unit, check=False)
    for unit in HOST_CONSUMER_UNITS:
        if _unit_active(unit) or _unit_enabled(unit):
            raise TransitionError(f"boot transition fence did not quiesce: {unit}")


def _converge_boot_fence(value: dict[str, Any]) -> dict[str, Any]:
    previous, target = _contexts(value)
    _verify_publish_binding(value, allow_absent=value["phase"] == "activated")
    # The fence service is still activating here.  Running either pair manager
    # could synchronously start a consumer whose RequiredBy/After dependency is
    # this very service.  Only quiesce and validate; the queued continuation
    # runs full recovery after the fence reaches active (exited).
    _boot_fence_consumers()
    pointer = _current_pair()
    host_state = _installed_host_state(previous, target)
    phase = str(value["phase"])
    valid = (
        (pointer == previous.name and host_state == "old" and phase == "prepared")
        or (
            pointer is None
            and (
                (host_state in {"old", "mixed"} and phase in {"prepared", "deactivated"})
                or host_state == "target"
            )
        )
        or (
            pointer == target.name
            and host_state == "target"
            and phase in {"host-installed", "activated"}
        )
    )
    if not valid:
        raise TransitionError("boot transition pointer, host, and phase are inconsistent")
    _verify_consumers_quiescent()
    return {
        "host_transition": "fenced",
        "schema_version": 1,
        "target_pair": target.name,
    }


@contextlib.contextmanager
def _installer_lock() -> Iterator[None]:
    inherited = os.environ.get(INSTALL_LOCK_ENV)
    close_descriptor = False
    if inherited is not None:
        if re.fullmatch(r"[0-9]+", inherited) is None:
            raise TransitionError("inherited runtime installer lock descriptor is invalid")
        descriptor = int(inherited, 10)
    else:
        try:
            descriptor = os.open(INSTALL_LOCK, os.O_RDWR | os.O_NOFOLLOW)
        except OSError as exc:
            raise TransitionError("runtime installer lock is unavailable") from exc
        close_descriptor = True
    try:
        try:
            descriptor_metadata = os.fstat(descriptor)
            path_metadata = INSTALL_LOCK.lstat()
        except OSError as exc:
            raise TransitionError("runtime installer lock metadata is unavailable") from exc
        if (
            INSTALL_LOCK.is_symlink()
            or not stat.S_ISREG(descriptor_metadata.st_mode)
            or not stat.S_ISREG(path_metadata.st_mode)
            or descriptor_metadata.st_uid != ROOT_UID
            or descriptor_metadata.st_gid != ROOT_GID
            or stat.S_IMODE(descriptor_metadata.st_mode) != 0o600
            or descriptor_metadata.st_nlink != 1
            or (descriptor_metadata.st_dev, descriptor_metadata.st_ino)
            != (path_metadata.st_dev, path_metadata.st_ino)
        ):
            raise TransitionError("runtime installer lock descriptor is unsafe")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        if close_descriptor:
            os.close(descriptor)


@contextlib.contextmanager
def _operation_lock() -> Iterator[None]:
    STATE_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    _safe_directory(STATE_ROOT, uid=ROOT_UID, gid=ROOT_GID, mode=0o700)
    descriptor = os.open(
        TRANSITION_LOCK,
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != ROOT_UID or metadata.st_gid != ROOT_GID:
            raise TransitionError("host transition lock is unsafe")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def prepare(
    *,
    previous_pair: str,
    target_pair: str,
    previous_host_layer: str,
    target_host_layer: str,
    publish_journal: Path,
) -> dict[str, Any]:
    _validate_pair_name(previous_pair)
    _validate_pair_name(target_pair)
    if previous_pair == target_pair:
        raise TransitionError("host transition endpoints must differ")
    for digest in (previous_host_layer, target_host_layer):
        if SHA256_PATTERN.fullmatch(digest) is None:
            raise TransitionError("reviewed host-layer SHA-256 is invalid")
    if previous_host_layer == target_host_layer:
        raise TransitionError("ordinary install must handle an unchanged host layer")

    existing = _load_transition()
    if existing is not None:
        expected_arguments = {
            "previous_host_layer_sha256": previous_host_layer,
            "previous_pair": previous_pair,
            "publish_journal": str(publish_journal),
            "target_host_layer_sha256": target_host_layer,
            "target_pair": target_pair,
        }
        if any(existing[key] != item for key, item in expected_arguments.items()):
            raise TransitionError("a different host transition is already pending")
        _install_recovery_unit()
        return {
            "host_transition": "armed",
            "schema_version": 1,
            "target_pair": target_pair,
        }

    committed = _load_committed_receipt(target_pair)
    if committed is not None:
        return _verify_committed_prepare(
            committed,
            previous_pair=previous_pair,
            target_pair=target_pair,
            previous_host_layer=previous_host_layer,
            target_host_layer=target_host_layer,
        )

    if any(
        path.exists() or path.is_symlink()
        for path in (PAIR_TRANSACTION, CONTROL_FINALIZATION_TRANSACTION, OPS_ROTATION_TRANSACTION)
    ):
        raise TransitionError("another runtime transaction blocks host maintenance")
    previous = _load_pair(previous_pair)
    target = _load_pair(target_pair)
    _require_matching_manifests(previous, target)
    if _current_pair() != previous_pair:
        raise TransitionError("previous pair is not the exact active pair")
    if _installed_host_state(previous, target) != "old":
        raise TransitionError("installed host layer is not the reviewed previous endpoint")
    if previous.host_layer_sha256 != previous_host_layer:
        raise TransitionError("previous host-layer digest does not match its receipt")
    if target.host_layer_sha256 != target_host_layer:
        raise TransitionError("target host-layer digest does not match its receipt")
    _publish, publish_payload = _load_publish_journal(
        publish_journal,
        target_pair,
        previous_pair,
        previous_host_layer,
    )
    value = {
        "manifest_sha256": previous.manifest_sha256,
        "phase": "prepared",
        "previous_host_layer_sha256": previous.host_layer_sha256,
        "previous_pair": previous.name,
        "previous_receipt_sha256": previous.receipt_sha256,
        "publish_journal": str(publish_journal),
        "publish_journal_sha256": hashlib.sha256(publish_payload).hexdigest(),
        "schema_version": 1,
        "target_host_layer_sha256": target.host_layer_sha256,
        "target_pair": target.name,
        "target_receipt_sha256": target.receipt_sha256,
    }
    _install_recovery_unit_files()
    try:
        _write_transition(value, create=True)
    except Exception:
        _remove_recovery_unit()
        raise
    _enable_recovery_unit()
    return {
        "host_transition": "armed",
        "schema_version": 1,
        "target_pair": target_pair,
    }


def _handoff_recovery(
    *,
    previous_pair: str,
    target_pair: str,
    previous_host_layer: str,
    target_host_layer: str,
) -> dict[str, Any]:
    # Queue the same fence used at boot only after the foreground helper has
    # released both locks. If an earlier continuation failed, the oneshot
    # fence intentionally remains active (exited), so starting it again is a
    # no-op and cannot replay ExecStartPost. An exact retry may explicitly
    # restart the continuation only after proving that the fence is active and
    # every ordered consumer remains quiescent.
    fence_active = _unit_active(RECOVERY_UNIT_NAME)
    if fence_active:
        with _installer_lock():
            with _operation_lock():
                transition = _load_transition()
                if transition is None:
                    receipt = _load_committed_receipt(target_pair)
                    if receipt is not None:
                        return _verify_committed_prepare(
                            receipt,
                            previous_pair=previous_pair,
                            target_pair=target_pair,
                            previous_host_layer=previous_host_layer,
                            target_host_layer=target_host_layer,
                        )
                    raise TransitionError("active host transition fence has no journal")
                _verify_consumers_quiescent()
        _systemctl("reset-failed", CONTINUATION_UNIT_NAME, check=False)
        _systemctl("--no-block", "start", CONTINUATION_UNIT_NAME)
    else:
        _systemctl("reset-failed", RECOVERY_UNIT_NAME, check=False)
        _systemctl("reset-failed", CONTINUATION_UNIT_NAME, check=False)
        _systemctl("--no-block", "start", RECOVERY_UNIT_NAME)
    deadline = time.monotonic() + HANDOFF_TIMEOUT
    while time.monotonic() < deadline:
        with _installer_lock():
            with _operation_lock():
                receipt = _load_committed_receipt(target_pair)
                if receipt is not None and _load_transition() is None:
                    return _verify_committed_prepare(
                        receipt,
                        previous_pair=previous_pair,
                        target_pair=target_pair,
                        previous_host_layer=previous_host_layer,
                        target_host_layer=target_host_layer,
                    )
        fence_failed = _systemctl("is-failed", RECOVERY_UNIT_NAME, check=False)
        fence_state = fence_failed.stdout.decode("ascii", errors="ignore").strip()
        if fence_failed.returncode == 0 and fence_state == "failed":
            raise TransitionError("host transition fence failed")
        continuation_failed = _systemctl("is-failed", CONTINUATION_UNIT_NAME, check=False)
        continuation_state = continuation_failed.stdout.decode("ascii", errors="ignore").strip()
        if continuation_failed.returncode == 0 and continuation_state == "failed":
            raise TransitionError("host transition continuation failed")
        time.sleep(1)
    raise TransitionError("host transition continuation timed out")


def recover(*, boot_fence: bool = False) -> dict[str, Any]:
    if boot_fence:
        try:
            TRANSITION_JOURNAL.lstat()
        except FileNotFoundError:
            # An inert stale fence can remain if power is lost after the
            # transition journal is removed.  With no pathname at all there is
            # no transition authority, so do not disturb the live runtime.
            return {"fenced": False, "schema_version": 1}
        except OSError:
            # Metadata failure is not absence.  Fence first, then let exact
            # journal loading report the unsafe residue.
            pass
        # This must precede even transition-journal parsing: corrupt recovery
        # state is not authority to release any ordered host consumer.
        _boot_fence_consumers()
    value = _load_transition()
    if value is None:
        _remove_recovery_unit()
        return {"recovered": False, "schema_version": 1}
    if boot_fence:
        return _converge_boot_fence(value)
    _install_recovery_unit()
    return _converge(value)


def status() -> dict[str, Any]:
    value = _load_transition()
    if value is None:
        return {"active": False, "schema_version": 1}
    previous, target = _contexts(value)
    publish = Path(str(value["publish_journal"]))
    return {
        "active": True,
        "host_state": _installed_host_state(previous, target),
        "phase": value["phase"],
        "pointer": _current_pair(),
        "previous_pair": previous.name,
        "publish_journal_present": publish.exists() or publish.is_symlink(),
        "schema_version": 1,
        "target_pair": target.name,
    }


def _require_root_host() -> None:
    if os.geteuid() != 0:
        raise TransitionError("host transition requires root")
    if os.uname().nodename.split(".", 1)[0] != "an2p":
        raise TransitionError("host transition requires the reviewed an2p host")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("--previous-pair", required=True)
    prepare_parser.add_argument("--target-pair", required=True)
    prepare_parser.add_argument("--previous-host-layer", required=True)
    prepare_parser.add_argument("--target-host-layer", required=True)
    prepare_parser.add_argument("--publish-journal", required=True, type=Path)
    recover_parser = commands.add_parser("recover")
    recover_parser.add_argument("--boot-fence", action="store_true")
    status_parser = commands.add_parser("status")
    status_parser.add_argument("--json", action="store_true", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    _require_root_host()
    handoff = False
    with _installer_lock():
        with _operation_lock():
            if arguments.command == "prepare":
                result = prepare(
                    previous_pair=arguments.previous_pair,
                    target_pair=arguments.target_pair,
                    previous_host_layer=arguments.previous_host_layer,
                    target_host_layer=arguments.target_host_layer,
                    publish_journal=arguments.publish_journal,
                )
                handoff = result.get("host_transition") == "armed"
            elif arguments.command == "recover":
                result = recover(boot_fence=arguments.boot_fence)
            else:
                result = status()
    if handoff:
        result = _handoff_recovery(
            previous_pair=arguments.previous_pair,
            target_pair=arguments.target_pair,
            previous_host_layer=arguments.previous_host_layer,
            target_host_layer=arguments.target_host_layer,
        )
    print(_canonical_json(result).decode("ascii"), end="")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (TransitionError, OSError, subprocess.SubprocessError) as exc:
        print(f"an2p host-layer transition: {exc}", file=sys.stderr)
        raise SystemExit(78) from None
