#!/usr/bin/env python3
"""Root-only, fail-safe controller for MoonCen production containers.

The controller deliberately exposes no arbitrary Compose project, file, port,
service, image, Docker context, or systemd unit arguments.  A reviewed image
bundle is first exercised on alternate loopback ports.  Only after those
containers are healthy and their immutable image IDs match the manifest does
the controller replace the active native or Docker application runtime.

PostgreSQL, nginx, cloudflared, the crawler hosts, Docker itself, containerd,
and the out-of-band deployment SSH endpoint are outside this controller's
authority.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import select
import signal
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploy.docker.release_manifest import (  # noqa: E402
    IMAGE_ID_PATTERN,
    ManifestError,
    bind_promotion_evidence,
    load_json_evidence,
)
from deploy.docker.production_runtime_integrity import (  # noqa: E402
    RuntimeIntegrityError,
    load_bootstrap_config,
    validate_installed_runtime,
)
from deploy.docker.native_baseline import (  # noqa: E402
    NativeBaselineError,
    inventory_sha256,
)
from deploy.docker.verify_release_bundle import (  # noqa: E402
    MAX_BUNDLE_BYTES,
    VerificationError,
    verify_release_artifacts,
    verify_release_directory,
)


SCHEMA_VERSION = 1
SOURCE_TREE_PATTERN = re.compile(r"\A[0-9a-f]{40}\Z")
RELEASE_DIGEST_PATTERN = re.compile(r"\A[0-9a-f]{64}\Z")
TOKEN_PATTERN = re.compile(r"\A[0-9a-f]{32}\Z")
CLAIM_JOB_PATTERN = re.compile(r"\A[0-9a-f]{32}\Z")
CLAIM_TOKEN_PATTERN = re.compile(r"\A[0-9a-f]{32}\Z")
CONTAINER_ID_PATTERN = re.compile(r"\A[0-9a-f]{64}\Z")
BOOT_ID_PATTERN = re.compile(r"\A[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")
UTC_PATTERN = re.compile(r"\A20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
DEPLOY_INFO_VALUE_PATTERN = re.compile(r"\A[A-Za-z0-9._:-]{1,253}\Z")

ACTIVE_PROJECT = "mooncen-production"
CANDIDATE_PROJECT = "mooncen-production-candidate"
ALLOWED_PROJECTS = frozenset({ACTIVE_PROJECT, CANDIDATE_PROJECT})
ACTIVE_API_PORT = 8001
ACTIVE_FRONTEND_PORT = 5173
CANDIDATE_API_PORT = 18001
CANDIDATE_FRONTEND_PORT = 15173
DEVELOPMENT_RECEIPT_TARGET = "an2p-dev"
DEVELOPMENT_IDENTITY_FILE = Path("/etc/mooncen/an2p-dev-target-identity")

NATIVE_UNITS = (
    "mooncen-api.service",
    "mooncen-frontend.service",
    "mooncen-ai-worker.service",
)
NATIVE_CONTROL_FILES = {
    **{unit: Path("/etc/systemd/system") / unit for unit in NATIVE_UNITS},
    "mooncen-native-runtime-condition": Path(
        "/usr/local/libexec/mooncen-native-runtime-condition"
    ),
}
STACK_UNIT = "mooncen-container-stack.service"
GUARD_UNIT_PREFIX = "mooncen-container-release-guard@"
GUARD_UNIT_SUFFIX = ".service"

API_ENV_FILE = Path("/etc/mooncen/container-api.env")
AI_ENV_FILE = Path("/etc/mooncen/container-ai.env")
MIGRATOR_ENV_FILE = Path("/etc/mooncen/container-migrator.env")
RUNTIME_CONFIG_FILE = Path("/etc/mooncen/container-frontend-runtime-config.js")
INGRESS_ROOT = Path("/var/lib/mooncen-container-ingress")
POSTGRES_SOCKET_DIRECTORY = Path("/var/run/postgresql")
PROTECTED_PATHS = (
    "/api/ops",
    "/api/ops/runtime-metrics",
    "/api/auth/ops",
    "/api/auth/ops/login",
)

TRANSACTION_PHASES = frozenset(
    {
        "prepared",
        "candidate_starting",
        "candidate_verified",
        "cutting_over",
        "active_verifying",
        "committing",
        "rolling_back",
        "rollback_failed",
    }
)
TRANSACTION_OPERATIONS = frozenset({"promote", "rollback", "rollback_native"})
HEALTH_TIMEOUT_SECONDS = 120.0
OWNER_TERMINATION_SECONDS = 10.0
TRANSACTION_DEADLINE_SECONDS = 15 * 60
WORKER_LEASE_DEADLINE_SECONDS = 15 * 60
GUARD_POLL_SECONDS = 5.0
GUARD_ARM_TIMEOUT_SECONDS = 30.0
COMMAND_TIMEOUT_SECONDS = 10 * 60
NATIVE_RELEASE_SENTINEL = "0" * 64


class ContainerReleaseError(RuntimeError):
    """Raised when a release cannot be activated or recovered safely."""


@dataclass(frozen=True)
class RuntimePaths:
    release_root: Path = Path("/opt/mooncen-container-releases")
    state_root: Path = Path("/var/lib/mooncen-container-release")
    runtime_root: Path = Path("/run/mooncen-container-release")
    native_root: Path = Path("/opt/mooncen")
    systemd_root: Path = Path("/etc/systemd/system")
    libexec_root: Path = Path("/usr/local/libexec")

    @property
    def state_file(self) -> Path:
        return self.state_root / "active.json"

    @property
    def transaction_file(self) -> Path:
        return self.state_root / "transaction.json"

    @property
    def lock_file(self) -> Path:
        return self.state_root / "operation.lock"

    @property
    def control_lock_file(self) -> Path:
        return self.state_root / "control.lock"

    @property
    def native_restore_file(self) -> Path:
        return self.runtime_root / "native-restore.json"

    @property
    def native_intent_file(self) -> Path:
        return self.state_root / "native-intent.json"

    @property
    def worker_lease_file(self) -> Path:
        return self.state_root / "worker-lease.json"

    @property
    def native_deploy_info_file(self) -> Path:
        return self.native_root / ".deploy-info"


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str


@dataclass(frozen=True)
class LoadedRelease:
    directory: Path
    manifest: dict[str, Any]
    reference: dict[str, Any]


def _canonical_json(value: Any) -> bytes:
    try:
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
    except (TypeError, ValueError) as exc:
        raise ContainerReleaseError("runtime state is not canonical JSON") from exc


def _state_sha256(value: Mapping[str, Any] | None) -> str:
    """Hash the exact compact controller state, excluding its transport LF."""

    return hashlib.sha256(_canonical_json(value)[:-1]).hexdigest()


def _utc_timestamp(epoch: float | None = None) -> str:
    moment = datetime.fromtimestamp(
        time.time() if epoch is None else epoch,
        tz=timezone.utc,
    ).replace(microsecond=0)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _exact_keys(value: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    actual = frozenset(value)
    if actual != expected:
        raise ContainerReleaseError(f"{label} fields are invalid")


def _required_pattern(value: Any, pattern: re.Pattern[str], label: str) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise ContainerReleaseError(f"{label} is invalid")
    return value


def _generation_argument(value: str) -> int:
    """Parse the fixed-width generation used by the sudoers CAS contract."""

    if re.fullmatch(r"[0-9]{10}", value) is None:
        raise argparse.ArgumentTypeError("expected generation must be ten decimal digits")
    generation = int(value)
    if generation > 1_000_000_000:
        raise argparse.ArgumentTypeError("expected generation is outside the supported range")
    return generation


def _claim_epoch_argument(value: str) -> int:
    """Parse the fixed-width globally monotonic worker fencing epoch."""

    if re.fullmatch(r"[0-9]{20}", value) is None:
        raise argparse.ArgumentTypeError("worker claim epoch must be twenty decimal digits")
    epoch = int(value)
    if not 1 <= epoch <= 9_223_372_036_854_775_807:
        raise argparse.ArgumentTypeError("worker claim epoch is outside the supported range")
    return epoch


def _validate_release_reference(value: Any, *, label: str = "release") -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContainerReleaseError(f"{label} reference is invalid")
    _exact_keys(
        value,
        frozenset({"release_digest", "source_tree", "image_ids"}),
        f"{label} reference",
    )
    image_ids = value.get("image_ids")
    if not isinstance(image_ids, dict) or frozenset(image_ids) != {"api", "frontend"}:
        raise ContainerReleaseError(f"{label} image IDs are invalid")
    normalized_ids = {
        name: _required_pattern(image_ids[name], IMAGE_ID_PATTERN, f"{label} {name} image ID")
        for name in ("api", "frontend")
    }
    if normalized_ids["api"] == normalized_ids["frontend"]:
        raise ContainerReleaseError(f"{label} image IDs must be distinct")
    return {
        "release_digest": _required_pattern(value.get("release_digest"), RELEASE_DIGEST_PATTERN, f"{label} digest"),
        "source_tree": _required_pattern(value.get("source_tree"), SOURCE_TREE_PATTERN, f"{label} source tree"),
        "image_ids": normalized_ids,
    }


def _validate_native_snapshot(value: Any) -> dict[str, dict[str, bool]]:
    if not isinstance(value, dict) or frozenset(value) != frozenset(NATIVE_UNITS):
        raise ContainerReleaseError("native service snapshot is invalid")
    normalized: dict[str, dict[str, bool]] = {}
    for unit in NATIVE_UNITS:
        record = value.get(unit)
        if not isinstance(record, dict):
            raise ContainerReleaseError("native service snapshot is invalid")
        _exact_keys(record, frozenset({"active", "enabled"}), "native unit snapshot")
        if type(record.get("active")) is not bool or type(record.get("enabled")) is not bool:
            raise ContainerReleaseError("native service snapshot is invalid")
        normalized[unit] = {
            "active": record["active"],
            "enabled": record["enabled"],
        }
    return normalized


def _native_fallback_identity(value: Mapping[str, Any]) -> str:
    bound = {
        "schema_version": SCHEMA_VERSION,
        "deploy_commit": value["deploy_commit"],
        "deploy_archive_sha256": value["deploy_archive_sha256"],
        "deploy_info_sha256": value["deploy_info_sha256"],
        "prebuild_sha256": value["prebuild_sha256"],
        "runtime_tree_sha256": value["runtime_tree_sha256"],
        "control_sha256": value["control_sha256"],
        "units": value["units"],
    }
    return hashlib.sha256(_canonical_json(bound)[:-1]).hexdigest()


def _validate_native_fallback(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContainerReleaseError("native fallback evidence is invalid")
    _exact_keys(
        value,
        frozenset(
            {
                "schema_version",
                "identity",
                "deploy_commit",
                "deploy_archive_sha256",
                "deploy_info_sha256",
                "prebuild_sha256",
                "runtime_tree_sha256",
                "control_sha256",
                "units",
            }
        ),
        "native fallback evidence",
    )
    normalized = {
        "schema_version": SCHEMA_VERSION,
        "identity": _required_pattern(
            value.get("identity"), RELEASE_DIGEST_PATTERN, "native fallback identity"
        ),
        "deploy_commit": _required_pattern(
            value.get("deploy_commit"), SOURCE_TREE_PATTERN, "native deploy commit"
        ),
        "deploy_archive_sha256": _required_pattern(
            value.get("deploy_archive_sha256"),
            RELEASE_DIGEST_PATTERN,
            "native deploy archive digest",
        ),
        "deploy_info_sha256": _required_pattern(
            value.get("deploy_info_sha256"),
            RELEASE_DIGEST_PATTERN,
            "native deploy-info digest",
        ),
        "prebuild_sha256": _required_pattern(
            value.get("prebuild_sha256"), RELEASE_DIGEST_PATTERN, "native prebuild digest"
        ),
        "runtime_tree_sha256": _required_pattern(
            value.get("runtime_tree_sha256"), RELEASE_DIGEST_PATTERN, "native runtime tree digest"
        ),
        "control_sha256": {},
        "units": _validate_native_snapshot(value.get("units")),
    }
    control = value.get("control_sha256")
    expected_control_keys = frozenset((*NATIVE_UNITS, "mooncen-native-runtime-condition"))
    if not isinstance(control, dict) or frozenset(control) != expected_control_keys:
        raise ContainerReleaseError("native control digest set is invalid")
    normalized["control_sha256"] = {
        key: _required_pattern(control[key], RELEASE_DIGEST_PATTERN, f"native control digest {key}")
        for key in sorted(expected_control_keys)
    }
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ContainerReleaseError("native fallback schema is unsupported")
    if normalized["identity"] != _native_fallback_identity(normalized):
        raise ContainerReleaseError("native fallback identity does not match its evidence")
    return normalized


def _validate_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContainerReleaseError("active container state is invalid")
    _exact_keys(
        value,
        frozenset(
            {
                "schema_version",
                "generation",
                "mode",
                "active",
                "previous",
                "native_fallback",
                "updated_at",
            }
        ),
        "active container state",
    )
    if value.get("schema_version") != SCHEMA_VERSION or value.get("mode") != "docker":
        raise ContainerReleaseError("active container state schema is unsupported")
    generation = value.get("generation")
    if type(generation) is not int or generation < 1 or generation > 1_000_000_000:
        raise ContainerReleaseError("active container state generation is invalid")
    previous_value = value.get("previous")
    previous = None if previous_value is None else _validate_release_reference(previous_value, label="previous release")
    active = _validate_release_reference(value.get("active"), label="active release")
    if previous is not None and previous["release_digest"] == active["release_digest"]:
        raise ContainerReleaseError("active and previous releases must be distinct")
    updated_at = value.get("updated_at")
    if type(updated_at) is not str or not updated_at.endswith("Z"):
        raise ContainerReleaseError("active container state timestamp is invalid")
    return {
        "schema_version": SCHEMA_VERSION,
        "generation": generation,
        "mode": "docker",
        "active": active,
        "previous": previous,
        "native_fallback": _validate_native_fallback(value.get("native_fallback")),
        "updated_at": updated_at,
    }


def _validate_transaction(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContainerReleaseError("container transaction is invalid")
    _exact_keys(
        value,
        frozenset(
            {
                "schema_version",
                "token",
                "operation",
                "phase",
                "target",
                "previous_state",
                "native_snapshot",
                "candidate_started",
                "cutover_started",
                "owner_pid",
                "owner_start_ticks",
                "owner_boot_id",
                "created_epoch",
                "updated_epoch",
                "deadline_epoch",
            }
        ),
        "container transaction",
    )
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ContainerReleaseError("container transaction schema is unsupported")
    operation = value.get("operation")
    phase = value.get("phase")
    if operation not in TRANSACTION_OPERATIONS or phase not in TRANSACTION_PHASES:
        raise ContainerReleaseError("container transaction operation or phase is invalid")
    target_value = value.get("target")
    target = None if target_value is None else _validate_release_reference(target_value, label="target release")
    if operation == "promote" and target is None:
        raise ContainerReleaseError("promotion transaction has no target release")
    previous_state_value = value.get("previous_state")
    previous_state = None if previous_state_value is None else _validate_state(previous_state_value)
    for field in ("candidate_started", "cutover_started"):
        if type(value.get(field)) is not bool:
            raise ContainerReleaseError(f"container transaction {field} is invalid")
    for field in (
        "owner_pid",
        "owner_start_ticks",
        "created_epoch",
        "updated_epoch",
        "deadline_epoch",
    ):
        item = value.get(field)
        if type(item) is not int or item <= 0:
            raise ContainerReleaseError(f"container transaction {field} is invalid")
    if value["deadline_epoch"] <= value["created_epoch"]:
        raise ContainerReleaseError("container transaction deadline is invalid")
    return {
        "schema_version": SCHEMA_VERSION,
        "token": _required_pattern(value.get("token"), TOKEN_PATTERN, "transaction token"),
        "operation": operation,
        "phase": phase,
        "target": target,
        "previous_state": previous_state,
        "native_snapshot": _validate_native_fallback(value.get("native_snapshot")),
        "candidate_started": value["candidate_started"],
        "cutover_started": value["cutover_started"],
        "owner_pid": value["owner_pid"],
        "owner_start_ticks": value["owner_start_ticks"],
        "owner_boot_id": _required_pattern(value.get("owner_boot_id"), BOOT_ID_PATTERN, "transaction boot ID"),
        "created_epoch": value["created_epoch"],
        "updated_epoch": value["updated_epoch"],
        "deadline_epoch": value["deadline_epoch"],
    }


def _validate_worker_lease(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContainerReleaseError("deployment worker lease is invalid")
    _exact_keys(
        value,
        frozenset(
            {
                "schema_version",
                "job_id",
                "claim_epoch",
                "claim_token_sha256",
                "active",
                "expires_epoch",
            }
        ),
        "deployment worker lease",
    )
    claim_epoch = value.get("claim_epoch")
    expires_epoch = value.get("expires_epoch")
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or type(claim_epoch) is not int
        or not 1 <= claim_epoch <= 9_223_372_036_854_775_807
        or type(expires_epoch) is not int
        or expires_epoch <= 0
        or type(value.get("active")) is not bool
    ):
        raise ContainerReleaseError("deployment worker lease fields are invalid")
    return {
        "schema_version": SCHEMA_VERSION,
        "job_id": _required_pattern(
            value.get("job_id"), CLAIM_JOB_PATTERN, "deployment worker job ID"
        ),
        "claim_epoch": claim_epoch,
        "claim_token_sha256": _required_pattern(
            value.get("claim_token_sha256"),
            RELEASE_DIGEST_PATTERN,
            "deployment worker claim token digest",
        ),
        "active": value["active"],
        "expires_epoch": expires_epoch,
    }


class ContainerReleaseController:
    def __init__(
        self,
        *,
        paths: RuntimePaths | None = None,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
        trusted_uid: int = 0,
        enforce_installation_receipt: bool = True,
    ) -> None:
        self.paths = paths or RuntimePaths()
        self.clock = clock
        self.sleeper = sleeper
        self.trusted_uid = trusted_uid
        self.enforce_installation_receipt = enforce_installation_receipt

    def _require_root(self) -> None:
        if os.geteuid() != 0:
            raise ContainerReleaseError("container release control requires root")

    def _validate_private_directory(self, path: Path, *, create: bool) -> Path:
        if create and not path.exists() and not path.is_symlink():
            path.mkdir(mode=0o700, parents=True, exist_ok=False)
        try:
            metadata = path.lstat()
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise ContainerReleaseError(f"trusted directory is unavailable: {path}") from exc
        if (
            path.is_symlink()
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != self.trusted_uid
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or resolved != path.absolute()
        ):
            raise ContainerReleaseError(f"trusted directory is unsafe: {path}")
        return resolved

    def _ensure_layout(self) -> None:
        self._validate_private_directory(self.paths.release_root, create=True)
        self._validate_private_directory(self.paths.state_root, create=True)

    @contextlib.contextmanager
    def _control_lock(self, *, exclusive: bool = False) -> Iterator[None]:
        """Fence complete controller commands from root-owned code upgrades."""

        self._ensure_layout()
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = -1
        try:
            descriptor = os.open(self.paths.control_lock_file, flags, 0o600)
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != self.trusted_uid
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise ContainerReleaseError("container control lock is unsafe")
            fcntl.flock(descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        except ContainerReleaseError:
            if descriptor >= 0:
                os.close(descriptor)
            raise
        except OSError as exc:
            if descriptor >= 0:
                os.close(descriptor)
            raise ContainerReleaseError("container control lock is unavailable") from exc
        try:
            yield
        finally:
            os.close(descriptor)

    @contextlib.contextmanager
    def _operation_lock(self, *, blocking: bool = True) -> Iterator[None]:
        self._ensure_layout()
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self.paths.lock_file, flags, 0o600)
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != self.trusted_uid
                or stat.S_IMODE(metadata.st_mode) & 0o077
            ):
                raise ContainerReleaseError("container operation lock is unsafe")
            lock_flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
            fcntl.flock(descriptor, lock_flags)
        except BlockingIOError as exc:
            raise ContainerReleaseError("another container operation is active") from exc
        except OSError as exc:
            raise ContainerReleaseError("container operation lock is unavailable") from exc
        try:
            yield
        finally:
            os.close(descriptor)

    def _atomic_write(self, path: Path, value: Mapping[str, Any]) -> None:
        parent = self._validate_private_directory(path.parent, create=False)
        if path.is_symlink():
            raise ContainerReleaseError(f"state output is unsafe: {path.name}")
        temporary: Path | None = None
        descriptor = -1
        try:
            descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
            temporary = Path(name)
            os.fchmod(descriptor, 0o600)
            payload = _canonical_json(value)
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                descriptor = -1
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            temporary = None
            _fsync_directory(parent)
        except OSError as exc:
            raise ContainerReleaseError(f"state could not be written: {path.name}") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _remove_file(self, path: Path) -> None:
        if path.is_symlink():
            raise ContainerReleaseError(f"state path is unsafe: {path.name}")
        try:
            path.unlink(missing_ok=True)
            _fsync_directory(path.parent)
        except OSError as exc:
            raise ContainerReleaseError(f"state could not be removed: {path.name}") from exc

    @contextlib.contextmanager
    def _authorize_native_restore(self, token: str) -> Iterator[None]:
        """Authorize native units only around a stopped-container restore."""

        trusted_token = _required_pattern(token, TOKEN_PATTERN, "transaction token")
        self._validate_private_directory(self.paths.runtime_root, create=True)
        with self._operation_lock():
            transaction = self._read_transaction(required=True)
            assert transaction is not None
            if transaction["token"] != trusted_token:
                raise ContainerReleaseError("native restore transaction token changed")
            self._atomic_write(
                self.paths.native_restore_file,
                {
                    "schema_version": SCHEMA_VERSION,
                    "transaction_token": trusted_token,
                },
            )
        try:
            yield
        finally:
            self._remove_file(self.paths.native_restore_file)

    def _read_json(self, path: Path, *, required: bool) -> Any:
        if not path.exists() and not path.is_symlink():
            if required:
                raise ContainerReleaseError(f"state file is missing: {path.name}")
            return None
        try:
            metadata = path.lstat()
            if (
                path.is_symlink()
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != self.trusted_uid
                or stat.S_IMODE(metadata.st_mode) & 0o077
                or metadata.st_size <= 0
                or metadata.st_size > 1024 * 1024
            ):
                raise ContainerReleaseError(f"state file is unsafe: {path.name}")
            return json.loads(path.read_text(encoding="ascii"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ContainerReleaseError(f"state file is invalid: {path.name}") from exc

    def _read_state(self) -> dict[str, Any] | None:
        value = self._read_json(self.paths.state_file, required=False)
        return None if value is None else _validate_state(value)

    def _write_state(self, state_value: Mapping[str, Any]) -> None:
        state = _validate_state(dict(state_value))
        self._atomic_write(self.paths.state_file, state)

    def _read_transaction(self, *, required: bool = False) -> dict[str, Any] | None:
        value = self._read_json(self.paths.transaction_file, required=required)
        return None if value is None else _validate_transaction(value)

    def _write_transaction(self, transaction: Mapping[str, Any]) -> None:
        trusted = _validate_transaction(dict(transaction))
        self._atomic_write(self.paths.transaction_file, trusted)

    def _read_native_intent(self) -> dict[str, Any] | None:
        value = self._read_json(self.paths.native_intent_file, required=False)
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ContainerReleaseError("native deployment intent is invalid")
        _exact_keys(
            value,
            frozenset({"schema_version", "token"}),
            "native deployment intent",
        )
        if value.get("schema_version") != SCHEMA_VERSION:
            raise ContainerReleaseError("native deployment intent schema is unsupported")
        return {
            "schema_version": SCHEMA_VERSION,
            "token": _required_pattern(value.get("token"), TOKEN_PATTERN, "native deployment intent token"),
        }

    def _read_worker_lease(self) -> dict[str, Any] | None:
        value = self._read_json(self.paths.worker_lease_file, required=False)
        return None if value is None else _validate_worker_lease(value)

    @staticmethod
    def _worker_claim_identity(
        job_id: str,
        claim_epoch: int,
        claim_token: str,
    ) -> tuple[str, int, str]:
        trusted_job = _required_pattern(
            job_id,
            CLAIM_JOB_PATTERN,
            "deployment worker job ID",
        )
        if type(claim_epoch) is not int or not 1 <= claim_epoch <= 9_223_372_036_854_775_807:
            raise ContainerReleaseError("deployment worker claim epoch is invalid")
        trusted_token = _required_pattern(
            claim_token,
            CLAIM_TOKEN_PATTERN,
            "deployment worker claim token",
        )
        return trusted_job, claim_epoch, hashlib.sha256(trusted_token.encode("ascii")).hexdigest()

    def bind_worker_lease(
        self,
        job_id: str,
        claim_epoch: int,
        claim_token: str,
    ) -> dict[str, Any]:
        """Fence every older controller caller and bind one DB claim.

        The CLI surrounds this method with the *exclusive* control lock.  It
        therefore returns only after every command holding an older shared
        control lock has exited.  The monotonically increasing DB sequence is
        retained even after release, preventing an old job from reacquiring
        authority later.
        """

        self._require_root()
        self._ensure_layout()
        trusted_job, trusted_epoch, token_sha256 = self._worker_claim_identity(
            job_id,
            claim_epoch,
            claim_token,
        )
        with self._operation_lock():
            existing = self._read_worker_lease()
            if existing is not None:
                if trusted_epoch < existing["claim_epoch"]:
                    raise ContainerReleaseError("deployment worker claim epoch was fenced")
                if trusted_epoch == existing["claim_epoch"]:
                    if (
                        existing["job_id"] != trusted_job
                        or existing["claim_token_sha256"] != token_sha256
                        or existing["active"] is not True
                    ):
                        raise ContainerReleaseError("deployment worker claim epoch cannot be reused")
            lease = {
                "schema_version": SCHEMA_VERSION,
                "job_id": trusted_job,
                "claim_epoch": trusted_epoch,
                "claim_token_sha256": token_sha256,
                "active": True,
                "expires_epoch": int(self.clock()) + WORKER_LEASE_DEADLINE_SECONDS,
            }
            trusted = _validate_worker_lease(lease)
            self._atomic_write(self.paths.worker_lease_file, trusted)
            return trusted

    def release_worker_lease(
        self,
        job_id: str,
        claim_epoch: int,
        claim_token: str,
    ) -> dict[str, Any]:
        """Wait out the caller's last command and durably revoke its token."""

        self._require_root()
        self._ensure_layout()
        trusted_job, trusted_epoch, token_sha256 = self._worker_claim_identity(
            job_id,
            claim_epoch,
            claim_token,
        )
        with self._operation_lock():
            existing = self._read_worker_lease()
            if (
                existing is None
                or existing["job_id"] != trusted_job
                or existing["claim_epoch"] != trusted_epoch
                or existing["claim_token_sha256"] != token_sha256
            ):
                raise ContainerReleaseError("deployment worker lease ownership changed")
            if existing["active"]:
                existing["active"] = False
                existing["expires_epoch"] = int(self.clock())
                self._atomic_write(
                    self.paths.worker_lease_file,
                    _validate_worker_lease(existing),
                )
            return existing

    def _require_worker_lease(
        self,
        job_id: str | None,
        claim_epoch: int | None,
        claim_token: str | None,
    ) -> None:
        supplied = (job_id, claim_epoch, claim_token)
        if all(value is None for value in supplied):
            # Direct root in-process recovery/tests remain available. Every
            # deploy-account CLI mutation requires all three parser arguments.
            return
        if any(value is None for value in supplied):
            raise ContainerReleaseError("deployment worker lease tuple is incomplete")
        assert job_id is not None and claim_epoch is not None and claim_token is not None
        trusted_job, trusted_epoch, token_sha256 = self._worker_claim_identity(
            job_id,
            claim_epoch,
            claim_token,
        )
        with self._operation_lock():
            existing = self._read_worker_lease()
            if (
                existing is None
                or existing["active"] is not True
                or existing["job_id"] != trusted_job
                or existing["claim_epoch"] != trusted_epoch
                or existing["claim_token_sha256"] != token_sha256
                or existing["expires_epoch"] <= int(self.clock())
            ):
                raise ContainerReleaseError("deployment worker lease is absent, expired, or fenced")
            existing["expires_epoch"] = int(self.clock()) + WORKER_LEASE_DEADLINE_SECONDS
            self._atomic_write(
                self.paths.worker_lease_file,
                _validate_worker_lease(existing),
            )

    def _execute(
        self,
        command: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
        allowed_returncodes: frozenset[int] = frozenset({0}),
        timeout: int = COMMAND_TIMEOUT_SECONDS,
    ) -> CommandResult:
        if not command or command[0] not in {"docker", "systemctl"}:
            raise ContainerReleaseError("runtime command is outside the allowlist")
        if command[0] == "docker" and (
            len(command) < 2 or command[1] not in {"context", "info", "compose", "container"}
        ):
            # Image loading is performed only by verify_release_directory(),
            # which hashes and validates the bundle before invoking Docker.
            raise ContainerReleaseError("Docker command is outside the allowlist")
        clean_environment = {
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "HOME": "/root",
        }
        if environment:
            clean_environment.update(environment)
        for name in (
            "DOCKER_HOST",
            "DOCKER_CONTEXT",
            "DOCKER_TLS",
            "DOCKER_TLS_VERIFY",
            "DOCKER_CERT_PATH",
            "COMPOSE_FILE",
            "COMPOSE_PROJECT_NAME",
            "COMPOSE_PROFILES",
        ):
            clean_environment.pop(name, None)
        try:
            result = subprocess.run(
                list(command),
                env=clean_environment,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ContainerReleaseError(f"runtime command failed to execute: {command[0]}") from exc
        if result.returncode not in allowed_returncodes:
            operation = command[1] if len(command) > 1 else command[0]
            raise ContainerReleaseError(f"runtime command failed: {command[0]} {operation}")
        return CommandResult(result.returncode, result.stdout.strip())

    def _require_local_docker(self) -> None:
        context = self._execute(("docker", "context", "show"), timeout=30).stdout
        if context != "default":
            raise ContainerReleaseError("production control requires the default Docker context")
        endpoint = self._execute(
            (
                "docker",
                "context",
                "inspect",
                "default",
                "--format",
                "{{.Endpoints.docker.Host}}",
            ),
            timeout=30,
        ).stdout
        if endpoint not in {"unix:///var/run/docker.sock", "unix:///run/docker.sock"}:
            raise ContainerReleaseError("production control requires the local Docker socket")
        platform = self._execute(
            ("docker", "info", "--format", "{{.OSType}}/{{.Architecture}}"),
            timeout=30,
        ).stdout
        if platform != "linux/x86_64":
            raise ContainerReleaseError("production control requires a Linux amd64 daemon")
        self._execute(("docker", "compose", "version"), timeout=30)

    def _safe_regular_file(self, path: Path, *, private: bool) -> None:
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise ContainerReleaseError(f"required host input is missing: {path.name}") from exc
        forbidden_mode = 0o077 if private else 0o022
        if (
            path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != self.trusted_uid
            or stat.S_IMODE(metadata.st_mode) & forbidden_mode
        ):
            raise ContainerReleaseError(f"required host input is unsafe: {path.name}")

    def _validate_host_inputs(self) -> None:
        for path in (API_ENV_FILE, AI_ENV_FILE, MIGRATOR_ENV_FILE):
            self._safe_regular_file(path, private=True)
        self._safe_regular_file(DEVELOPMENT_IDENTITY_FILE, private=True)
        self._safe_regular_file(RUNTIME_CONFIG_FILE, private=False)
        try:
            metadata = POSTGRES_SOCKET_DIRECTORY.lstat()
        except OSError as exc:
            raise ContainerReleaseError("PostgreSQL socket directory is unavailable") from exc
        if POSTGRES_SOCKET_DIRECTORY.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
            raise ContainerReleaseError("PostgreSQL socket directory is unsafe")
        self._validate_runtime_installation()

    def _validate_runtime_installation(self) -> None:
        if not self.enforce_installation_receipt:
            return
        try:
            validate_installed_runtime(None, trusted_uid=self.trusted_uid)
        except RuntimeIntegrityError as exc:
            raise ContainerReleaseError("installed production controller bytes have drifted") from exc

    def _development_target_identity(self) -> str:
        self._safe_regular_file(DEVELOPMENT_IDENTITY_FILE, private=True)
        try:
            value = DEVELOPMENT_IDENTITY_FILE.read_text(encoding="ascii").strip().lower()
        except (OSError, UnicodeError) as exc:
            raise ContainerReleaseError("development target identity cannot be read") from exc
        return _required_pattern(
            value,
            RELEASE_DIGEST_PATTERN,
            "development target identity",
        )

    def _release_directory(self, source_tree: str) -> Path:
        tree = _required_pattern(source_tree, SOURCE_TREE_PATTERN, "source tree")
        root = self._validate_private_directory(self.paths.release_root, create=False)
        directory = root / tree
        try:
            metadata = directory.lstat()
            resolved = directory.resolve(strict=True)
        except OSError as exc:
            raise ContainerReleaseError("release directory is unavailable") from exc
        if (
            directory.is_symlink()
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != self.trusted_uid
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or resolved.parent != root
        ):
            raise ContainerReleaseError("release directory is unsafe")
        expected = {
            "release.json",
            "validation.json",
            "images.tar",
            "compose.production.yaml",
        }
        try:
            actual = {entry.name for entry in directory.iterdir()}
        except OSError as exc:
            raise ContainerReleaseError("release directory cannot be inventoried") from exc
        if actual != expected:
            raise ContainerReleaseError("release directory contents do not match the allowlist")
        for name in expected:
            self._safe_regular_file(directory / name, private=name == "images.tar")
        return resolved

    def _load_release(
        self,
        source_tree: str,
        *,
        require_current_receipt: bool,
        load_images: bool = True,
        require_installed_policy: bool = False,
    ) -> LoadedRelease:
        directory = self._release_directory(source_tree)
        try:
            verification = verify_release_directory(directory, load_images=load_images)
            manifest = load_json_evidence(directory / "release.json")
        except (ManifestError, VerificationError, OSError) as exc:
            raise ContainerReleaseError("release bundle verification failed") from exc
        if manifest["source_tree"] != source_tree:
            raise ContainerReleaseError("release directory is not bound to its source tree")
        if require_current_receipt:
            try:
                receipt = load_json_evidence(directory / "validation.json", receipt=True)
                promotion = bind_promotion_evidence(
                    manifest,
                    receipt,
                    now=_utc_timestamp(self.clock()),
                )
            except (ManifestError, OSError) as exc:
                raise ContainerReleaseError("development validation receipt is invalid") from exc
            if promotion.receipt["target"] != DEVELOPMENT_RECEIPT_TARGET:
                raise ContainerReleaseError("release was not validated on the an2p development target")
            if promotion.receipt["target_identity"] != self._development_target_identity():
                raise ContainerReleaseError("development validation target identity is not trusted")
        if require_installed_policy and self.enforce_installation_receipt:
            try:
                validate_installed_runtime(
                    manifest["build_policy_sha256"],
                    trusted_uid=self.trusted_uid,
                )
            except RuntimeIntegrityError as exc:
                raise ContainerReleaseError(
                    "installed production controller does not match the release policy"
                ) from exc
        image_ids = verification.get("image_ids")
        expected_ids = {name: manifest["images"][name]["image_id"] for name in ("api", "frontend")}
        if image_ids != expected_ids:
            raise ContainerReleaseError("verified image IDs differ from the release manifest")
        reference = _validate_release_reference(
            {
                "release_digest": manifest["release_digest"],
                "source_tree": manifest["source_tree"],
                "image_ids": expected_ids,
            }
        )
        return LoadedRelease(directory=directory, manifest=manifest, reference=reference)

    def _compose_environment(
        self,
        release: LoadedRelease,
        *,
        api_port: int,
        frontend_port: int,
    ) -> dict[str, str]:
        if (api_port, frontend_port) not in {
            (ACTIVE_API_PORT, ACTIVE_FRONTEND_PORT),
            (CANDIDATE_API_PORT, CANDIDATE_FRONTEND_PORT),
        }:
            raise ContainerReleaseError("Compose ports are outside the allowlist")
        return {
            "MOONCEN_API_IMAGE": release.manifest["images"]["api"]["tag"],
            "MOONCEN_FRONTEND_IMAGE": release.manifest["images"]["frontend"]["tag"],
            "MOONCEN_API_BIND_PORT": str(api_port),
            "MOONCEN_FRONTEND_BIND_PORT": str(frontend_port),
            "MOONCEN_MIGRATOR_ENV_FILE": str(MIGRATOR_ENV_FILE),
            "MOONCEN_RUNTIME_CONFIG_FILE": str(RUNTIME_CONFIG_FILE),
        }

    def _compose(
        self,
        release: LoadedRelease,
        *,
        project: str,
        api_port: int,
        frontend_port: int,
        arguments: Sequence[str],
        allowed_returncodes: frozenset[int] = frozenset({0}),
        timeout: int = COMMAND_TIMEOUT_SECONDS,
    ) -> CommandResult:
        if project not in ALLOWED_PROJECTS:
            raise ContainerReleaseError("Compose project is outside the allowlist")
        if any(argument in {"build", "pull", "push", "prune", "down", "-v", "--volumes"} for argument in arguments):
            raise ContainerReleaseError("destructive or mutable Compose action is forbidden")
        command = (
            "docker",
            "compose",
            "--project-name",
            project,
            "--file",
            str(release.directory / "compose.production.yaml"),
            *arguments,
        )
        return self._execute(
            command,
            environment=self._compose_environment(
                release,
                api_port=api_port,
                frontend_port=frontend_port,
            ),
            allowed_returncodes=allowed_returncodes,
            timeout=timeout,
        )

    def _verify_migration_ledger(self, release: LoadedRelease) -> None:
        result = self._compose(
            release,
            project=CANDIDATE_PROJECT,
            api_port=CANDIDATE_API_PORT,
            frontend_port=CANDIDATE_FRONTEND_PORT,
            arguments=(
                "--profile",
                "migration",
                "run",
                "--rm",
                "--no-deps",
                "migrate",
                "python",
                "DB/setup_db.py",
                "--mode",
                "plan",
                "--json",
                "--require-current",
            ),
        )
        try:
            plan = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ContainerReleaseError("migration plan did not return canonical JSON") from exc
        expected_fields = frozenset(
            {
                "schema_version",
                "current",
                "pending",
                "expected_count",
                "applied_count",
                "expected_ledger_sha256",
                "applied_ledger_sha256",
            }
        )
        expected_digest = release.manifest["migration_ledger_sha256"]
        if (
            not isinstance(plan, dict)
            or frozenset(plan) != expected_fields
            or plan.get("schema_version") != 1
            or plan.get("current") is not True
            or plan.get("pending") != []
            or type(plan.get("expected_count")) is not int
            or type(plan.get("applied_count")) is not int
            or plan.get("expected_count") <= 0
            or plan.get("applied_count") != plan.get("expected_count")
            or plan.get("expected_ledger_sha256") != expected_digest
            or plan.get("applied_ledger_sha256") != expected_digest
        ):
            raise ContainerReleaseError("production migration ledger does not match the release")

    def _start_candidate(self, release: LoadedRelease) -> None:
        self._cleanup_candidate(release)
        self._compose(
            release,
            project=CANDIDATE_PROJECT,
            api_port=CANDIDATE_API_PORT,
            frontend_port=CANDIDATE_FRONTEND_PORT,
            arguments=("up", "--detach", "--no-build", "api", "frontend"),
        )
        self._wait_for_health(CANDIDATE_API_PORT, CANDIDATE_FRONTEND_PORT)
        self._verify_application_contract(CANDIDATE_API_PORT, CANDIDATE_FRONTEND_PORT)
        self._verify_project_images(
            release,
            project=CANDIDATE_PROJECT,
            api_port=CANDIDATE_API_PORT,
            frontend_port=CANDIDATE_FRONTEND_PORT,
            services=("api", "frontend"),
        )

    def _cleanup_candidate(self, release: LoadedRelease) -> None:
        allowed = frozenset({0, 1})
        self._compose(
            release,
            project=CANDIDATE_PROJECT,
            api_port=CANDIDATE_API_PORT,
            frontend_port=CANDIDATE_FRONTEND_PORT,
            arguments=("stop", "--timeout", "30", "api", "frontend"),
            allowed_returncodes=allowed,
            timeout=90,
        )
        self._compose(
            release,
            project=CANDIDATE_PROJECT,
            api_port=CANDIDATE_API_PORT,
            frontend_port=CANDIDATE_FRONTEND_PORT,
            arguments=("rm", "--force", "--stop", "api", "frontend"),
            allowed_returncodes=allowed,
            timeout=90,
        )

    def _start_active(self, release: LoadedRelease) -> None:
        self._compose(
            release,
            project=ACTIVE_PROJECT,
            api_port=ACTIVE_API_PORT,
            frontend_port=ACTIVE_FRONTEND_PORT,
            arguments=("up", "--detach", "--no-build", "api", "frontend", "ai"),
        )
        self._wait_for_health(ACTIVE_API_PORT, ACTIVE_FRONTEND_PORT)
        self._verify_application_contract(ACTIVE_API_PORT, ACTIVE_FRONTEND_PORT)
        self._verify_project_images(
            release,
            project=ACTIVE_PROJECT,
            api_port=ACTIVE_API_PORT,
            frontend_port=ACTIVE_FRONTEND_PORT,
            services=("api", "frontend", "ai"),
        )

    def _stop_active(self, release: LoadedRelease) -> None:
        self._compose(
            release,
            project=ACTIVE_PROJECT,
            api_port=ACTIVE_API_PORT,
            frontend_port=ACTIVE_FRONTEND_PORT,
            arguments=("stop", "--timeout", "60", "api", "frontend", "ai"),
            allowed_returncodes=frozenset({0, 1}),
            timeout=120,
        )

    def _probe(self, url: str) -> bool:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "mooncen-container-release/1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                response.read(64 * 1024 + 1)
                return response.status == 200
        except (OSError, urllib.error.URLError):
            return False

    def _http_response(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[int, str, bytes]:
        request_headers = {"User-Agent": "mooncen-container-release/1"}
        if headers:
            request_headers.update(headers)
        request = urllib.request.Request(url, headers=request_headers)
        try:
            response = urllib.request.urlopen(request, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        except (OSError, urllib.error.URLError) as exc:
            raise ContainerReleaseError("application contract request failed") from exc
        try:
            payload = response.read(1024 * 1024 + 1)
            content_type = response.headers.get_content_type()
            status = response.status
        finally:
            response.close()
        if len(payload) > 1024 * 1024:
            raise ContainerReleaseError("application contract response is too large")
        return status, content_type, payload

    def _http_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        status, content_type, payload = self._http_response(url, headers=headers)
        if status != 200 or content_type != "application/json":
            raise ContainerReleaseError("application JSON contract returned an invalid response")
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ContainerReleaseError("application JSON contract is malformed") from exc

    def _http_status(self, url: str) -> int:
        status, _content_type, _payload = self._http_response(url)
        return status

    def _verify_application_contract(self, api_port: int, frontend_port: int) -> None:
        if (api_port, frontend_port) not in {
            (ACTIVE_API_PORT, ACTIVE_FRONTEND_PORT),
            (CANDIDATE_API_PORT, CANDIDATE_FRONTEND_PORT),
        }:
            raise ContainerReleaseError("application verification ports are outside the allowlist")
        api_root = self._http_json(f"http://127.0.0.1:{api_port}/")
        if (
            not isinstance(api_root, dict)
            or api_root.get("message") != "Welcome to MoonCen API"
            or api_root.get("profile") != "public"
        ):
            raise ContainerReleaseError("public API root contract is invalid")
        courses = self._http_json(f"http://127.0.0.1:{api_port}/api/courses/?page=1&size=1")
        if (
            not isinstance(courses, dict)
            or not isinstance(courses.get("items"), list)
            or type(courses.get("total")) is not int
        ):
            raise ContainerReleaseError("public course API contract is invalid")
        oauth = self._http_json(f"http://127.0.0.1:{frontend_port}/api/auth/oauth/config")
        if not isinstance(oauth, dict):
            raise ContainerReleaseError("public OAuth configuration contract is invalid")
        for surface_port in (api_port, frontend_port):
            if self._http_status(f"http://127.0.0.1:{surface_port}/api/auth/me") != 401:
                raise ContainerReleaseError("unauthenticated auth contract did not return 401")
            for path in PROTECTED_PATHS:
                if self._http_status(f"http://127.0.0.1:{surface_port}{path}") != 404:
                    raise ContainerReleaseError("protected Ops path is exposed by the public stack")

    def _verify_host_origin(self) -> None:
        health = self._http_json(
            "http://127.0.0.1/health",
            headers={"Host": "mooncen.kr"},
        )
        if not isinstance(health, dict) or health.get("status") != "ready":
            raise ContainerReleaseError("host nginx origin health contract is invalid")

    def _wait_for_health(self, api_port: int, frontend_port: int) -> None:
        urls = (
            f"http://127.0.0.1:{api_port}/health",
            f"http://127.0.0.1:{frontend_port}/_frontend_health",
            f"http://127.0.0.1:{frontend_port}/health",
        )
        deadline = time.monotonic() + HEALTH_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if all(self._probe(url) for url in urls):
                return
            self.sleeper(2.0)
        raise ContainerReleaseError("container health verification timed out")

    def _container_id(
        self,
        release: LoadedRelease,
        *,
        project: str,
        api_port: int,
        frontend_port: int,
        service: str,
    ) -> str:
        result = self._compose(
            release,
            project=project,
            api_port=api_port,
            frontend_port=frontend_port,
            arguments=("ps", "--all", "--quiet", service),
            timeout=30,
        ).stdout
        identifiers = [line.strip().lower() for line in result.splitlines() if line.strip()]
        if len(identifiers) != 1 or CONTAINER_ID_PATTERN.fullmatch(identifiers[0]) is None:
            raise ContainerReleaseError(f"{service} container identity is ambiguous")
        return identifiers[0]

    def _verify_project_images(
        self,
        release: LoadedRelease,
        *,
        project: str,
        api_port: int,
        frontend_port: int,
        services: Sequence[str],
    ) -> None:
        expected = {
            "api": release.reference["image_ids"]["api"],
            "frontend": release.reference["image_ids"]["frontend"],
            "ai": release.reference["image_ids"]["api"],
        }
        if not services or any(service not in expected for service in services):
            raise ContainerReleaseError("container service verification is outside the allowlist")
        for service in services:
            container_id = self._container_id(
                release,
                project=project,
                api_port=api_port,
                frontend_port=frontend_port,
                service=service,
            )
            output = self._execute(
                (
                    "docker",
                    "container",
                    "inspect",
                    "--format",
                    "{{.Image}} {{.State.Running}} {{.RestartCount}}",
                    container_id,
                ),
                timeout=30,
            ).stdout.lower()
            parts = output.split()
            if len(parts) != 3 or parts[0] != expected[service] or parts[1] != "true" or parts[2] != "0":
                raise ContainerReleaseError(f"{service} container does not run the manifest image")

    def _systemctl_result(
        self,
        action: str,
        unit: str,
        *,
        allowed_returncodes: frozenset[int] = frozenset({0}),
    ) -> CommandResult:
        guard_match = (
            unit.startswith(GUARD_UNIT_PREFIX)
            and unit.endswith(GUARD_UNIT_SUFFIX)
            and TOKEN_PATTERN.fullmatch(unit[len(GUARD_UNIT_PREFIX) : -len(GUARD_UNIT_SUFFIX)]) is not None
        )
        if unit not in NATIVE_UNITS and unit != STACK_UNIT and not guard_match:
            raise ContainerReleaseError("systemd unit is outside the allowlist")
        if unit in NATIVE_UNITS:
            allowed_actions = {
                "is-active",
                "is-enabled",
                "start",
                "stop",
                "enable",
                "disable",
            }
        elif unit == STACK_UNIT:
            allowed_actions = {"is-active", "is-enabled", "start", "enable", "disable"}
        else:
            allowed_actions = {"start", "stop", "enable", "disable"}
        if action not in allowed_actions:
            raise ContainerReleaseError("systemd action is outside the allowlist")
        return self._execute(
            ("systemctl", action, unit),
            allowed_returncodes=allowed_returncodes,
            timeout=90,
        )

    def _capture_native_snapshot(self) -> dict[str, dict[str, bool]]:
        snapshot: dict[str, dict[str, bool]] = {}
        for unit in NATIVE_UNITS:
            active = (
                self._systemctl_result("is-active", unit, allowed_returncodes=frozenset({0, 1, 3, 4})).returncode == 0
            )
            enabled = (
                self._systemctl_result("is-enabled", unit, allowed_returncodes=frozenset({0, 1, 3, 4})).returncode == 0
            )
            snapshot[unit] = {"active": active, "enabled": enabled}
        return _validate_native_snapshot(snapshot)

    def _read_native_deploy_info(self) -> dict[str, str]:
        """Parse and hash the exact native provenance file without trusting shell input."""

        root = self.paths.native_root
        path = self.paths.native_deploy_info_file
        try:
            root_metadata = root.lstat()
            metadata = path.lstat()
        except OSError as exc:
            raise ContainerReleaseError("native deploy provenance is unavailable") from exc
        if (
            root.is_symlink()
            or not stat.S_ISDIR(root_metadata.st_mode)
            or path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != root_metadata.st_uid
            or metadata.st_gid != root_metadata.st_gid
            or stat.S_IMODE(metadata.st_mode) != 0o640
            or metadata.st_size <= 0
            or metadata.st_size > 4096
        ):
            raise ContainerReleaseError("native deploy provenance metadata is unsafe")
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ContainerReleaseError("native deploy provenance cannot be read") from exc
        try:
            text_value = raw.decode("ascii")
        except UnicodeDecodeError as exc:
            raise ContainerReleaseError("native deploy provenance is not ASCII") from exc
        keys = (
            "DEPLOY_EPOCH",
            "DEPLOY_UTC",
            "DEPLOY_COMMIT",
            "DEPLOY_ARCHIVE_SHA256",
            "NODE_ROLE",
            "DOMAIN",
            "HOSTNAME",
        )
        lines = text_value.splitlines(keepends=True)
        if len(lines) != len(keys) or any(not line.endswith("\n") for line in lines):
            raise ContainerReleaseError("native deploy provenance line set is invalid")
        parsed: dict[str, str] = {}
        for expected_key, line in zip(keys, lines, strict=True):
            key, separator, value = line[:-1].partition("=")
            if key != expected_key or separator != "=" or not value or "=" in value:
                raise ContainerReleaseError("native deploy provenance fields are invalid")
            parsed[key] = value
        if (
            re.fullmatch(r"[1-9][0-9]{0,15}", parsed["DEPLOY_EPOCH"]) is None
            or UTC_PATTERN.fullmatch(parsed["DEPLOY_UTC"]) is None
            or SOURCE_TREE_PATTERN.fullmatch(parsed["DEPLOY_COMMIT"]) is None
            or RELEASE_DIGEST_PATTERN.fullmatch(parsed["DEPLOY_ARCHIVE_SHA256"]) is None
            or any(
                DEPLOY_INFO_VALUE_PATTERN.fullmatch(parsed[key]) is None
                for key in ("NODE_ROLE", "DOMAIN", "HOSTNAME")
            )
        ):
            raise ContainerReleaseError("native deploy provenance values are invalid")
        return {
            "deploy_commit": parsed["DEPLOY_COMMIT"],
            "deploy_archive_sha256": parsed["DEPLOY_ARCHIVE_SHA256"],
            "deploy_info_sha256": hashlib.sha256(raw).hexdigest(),
        }

    def _native_prebuild_evidence(self, *, expected_commit: str) -> dict[str, str]:
        path = self.paths.native_root / ".mooncen-prebuilt-release"
        try:
            metadata = path.lstat()
            raw = path.read_bytes()
        except OSError as exc:
            raise ContainerReleaseError("native prebuild provenance is unavailable") from exc
        if (
            path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_gid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or not 0 < len(raw) <= 4096
        ):
            raise ContainerReleaseError("native prebuild provenance metadata is unsafe")
        keys = (
            "PREBUILD_VERSION",
            "DEPLOY_COMMIT",
            "REQUIREMENTS_SHA256",
            "PACKAGE_LOCK_SHA256",
            "FRONTEND_ENV_SHA256",
            "FRONTEND_INDEX_SHA256",
            "DEPLOY_ARCHIVE_SHA256",
            "IMMUTABLE_TREE_SHA256",
        )
        try:
            lines = raw.decode("ascii").splitlines(keepends=True)
        except UnicodeDecodeError as exc:
            raise ContainerReleaseError("native prebuild provenance is not ASCII") from exc
        if len(lines) != len(keys) or any(not line.endswith("\n") for line in lines):
            raise ContainerReleaseError("native prebuild provenance line set is invalid")
        parsed: dict[str, str] = {}
        for expected_key, line in zip(keys, lines, strict=True):
            key, separator, value = line[:-1].partition("=")
            if key != expected_key or separator != "=" or not value or "=" in value:
                raise ContainerReleaseError("native prebuild provenance fields are invalid")
            parsed[key] = value
        if (
            parsed["PREBUILD_VERSION"] != "1"
            or parsed["DEPLOY_COMMIT"] != expected_commit
            or any(
                RELEASE_DIGEST_PATTERN.fullmatch(parsed[key]) is None
                for key in keys[2:]
            )
        ):
            raise ContainerReleaseError("native prebuild provenance values are invalid")
        if parsed["DEPLOY_ARCHIVE_SHA256"] != self._read_native_deploy_info()["deploy_archive_sha256"]:
            raise ContainerReleaseError("native prebuild archive provenance is inconsistent")
        runtime_tree_sha256 = self._native_runtime_tree_sha256()
        if parsed["IMMUTABLE_TREE_SHA256"] != runtime_tree_sha256:
            raise ContainerReleaseError("native immutable inventory differs from its attestation")
        return {
            "prebuild_sha256": hashlib.sha256(raw).hexdigest(),
            "runtime_tree_sha256": runtime_tree_sha256,
        }

    def _native_control_sha256(self) -> dict[str, str]:
        paths = {
            **{unit: self.paths.systemd_root / unit for unit in NATIVE_UNITS},
            "mooncen-native-runtime-condition": (
                self.paths.libexec_root / "mooncen-native-runtime-condition"
            ),
        }
        digests: dict[str, str] = {}
        for label, path in paths.items():
            try:
                metadata = path.lstat()
            except OSError as exc:
                raise ContainerReleaseError("native control file is unavailable") from exc
            expected_mode = 0o755 if label == "mooncen-native-runtime-condition" else 0o644
            if (
                path.is_symlink()
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != 0
                or metadata.st_gid != 0
                or stat.S_IMODE(metadata.st_mode) != expected_mode
                or not 0 < metadata.st_size <= 1024 * 1024
            ):
                raise ContainerReleaseError("native control file metadata is unsafe")
            try:
                raw = path.read_bytes()
            except OSError as exc:
                raise ContainerReleaseError("native control file cannot be read") from exc
            if len(raw) != metadata.st_size:
                raise ContainerReleaseError("native control file changed while hashing")
            digests[label] = hashlib.sha256(raw).hexdigest()
        return digests

    def _native_runtime_tree_sha256(self) -> str:
        try:
            return inventory_sha256(self.paths.native_root)
        except NativeBaselineError as exc:
            raise ContainerReleaseError("native immutable inventory cannot be verified") from exc

    def _capture_native_fallback(self) -> dict[str, Any]:
        deploy_info = self._read_native_deploy_info()
        fallback: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "identity": "0" * 64,
            **deploy_info,
            **self._native_prebuild_evidence(expected_commit=deploy_info["deploy_commit"]),
            "control_sha256": self._native_control_sha256(),
            "units": self._capture_native_snapshot(),
        }
        fallback["identity"] = _native_fallback_identity(fallback)
        return _validate_native_fallback(fallback)

    def _verify_native_fallback(self, fallback: Mapping[str, Any]) -> dict[str, Any]:
        trusted = _validate_native_fallback(dict(fallback))
        current = self._read_native_deploy_info()
        if any(current[key] != trusted[key] for key in current):
            raise ContainerReleaseError("native deploy provenance drift blocks fallback")
        prebuild = self._native_prebuild_evidence(expected_commit=current["deploy_commit"])
        if prebuild["prebuild_sha256"] != trusted["prebuild_sha256"]:
            raise ContainerReleaseError("native prebuild provenance drift blocks fallback")
        if prebuild["runtime_tree_sha256"] != trusted["runtime_tree_sha256"]:
            raise ContainerReleaseError("native runtime tree drift blocks fallback")
        if self._native_control_sha256() != trusted["control_sha256"]:
            raise ContainerReleaseError("native control file drift blocks fallback")
        return trusted

    def _assert_native_units_disabled(self) -> None:
        snapshot = self._capture_native_snapshot()
        if any(snapshot[unit]["active"] or snapshot[unit]["enabled"] for unit in NATIVE_UNITS):
            raise ContainerReleaseError("native application unit drift blocks Docker stack convergence")

    def _native_unit_manager_state(self, unit: str) -> tuple[bool, bool, int]:
        if unit not in NATIVE_UNITS:
            raise ContainerReleaseError("systemd unit is outside the allowlist")
        result = self._execute(
            (
                "systemctl",
                "show",
                unit,
                "--property=ActiveState",
                "--property=UnitFileState",
                "--property=MainPID",
                "--no-pager",
            ),
            timeout=30,
        )
        fields: dict[str, str] = {}
        for line in result.stdout.splitlines():
            key, separator, value = line.partition("=")
            if not separator or key in fields:
                raise ContainerReleaseError("native application unit postcondition is invalid")
            fields[key] = value
        if (
            frozenset(fields) != {"ActiveState", "UnitFileState", "MainPID"}
            or fields["ActiveState"] not in {"active", "inactive"}
            or fields["UnitFileState"] not in {"enabled", "disabled"}
            or re.fullmatch(r"0|[1-9][0-9]*", fields["MainPID"]) is None
        ):
            raise ContainerReleaseError("native application unit postcondition is invalid")
        return (
            fields["ActiveState"] == "active",
            fields["UnitFileState"] == "enabled",
            int(fields["MainPID"]),
        )

    def _assert_native_unit_postcondition(
        self,
        unit: str,
        *,
        expected_active: bool,
        expected_enabled: bool,
    ) -> None:
        active, enabled, main_pid = self._native_unit_manager_state(unit)
        if (
            active is not expected_active
            or enabled is not expected_enabled
            or (expected_active and main_pid <= 0)
            or (not expected_active and main_pid != 0)
        ):
            raise ContainerReleaseError(
                "native application unit did not reach its exact saved state"
            )

    def _assert_native_unit_stopped_disabled(self, unit: str) -> None:
        self._assert_native_unit_postcondition(
            unit,
            expected_active=False,
            expected_enabled=False,
        )

    def _stop_native(self) -> None:
        for unit in NATIVE_UNITS:
            self._systemctl_result("stop", unit, allowed_returncodes=frozenset({0, 1, 5}))
            self._systemctl_result("disable", unit, allowed_returncodes=frozenset({0, 1}))
        # systemctl can return accepted idempotency/not-found codes without
        # proving that the old process and boot enablement are gone.  Do not
        # start Compose until the live manager reports all three postconditions.
        for unit in NATIVE_UNITS:
            self._assert_native_unit_stopped_disabled(unit)

    def _restore_native(self, snapshot: Mapping[str, Any]) -> None:
        trusted_fallback = self._verify_native_fallback(snapshot)
        trusted = trusted_fallback["units"]
        for unit in NATIVE_UNITS:
            action = "enable" if trusted[unit]["enabled"] else "disable"
            self._systemctl_result(action, unit, allowed_returncodes=frozenset({0, 1}))
        for unit in NATIVE_UNITS:
            action = "start" if trusted[unit]["active"] else "stop"
            self._systemctl_result(action, unit, allowed_returncodes=frozenset({0, 1, 5}))
        for unit in NATIVE_UNITS:
            self._assert_native_unit_postcondition(
                unit,
                expected_active=trusted[unit]["active"],
                expected_enabled=trusted[unit]["enabled"],
            )

    def _enable_stack(self) -> None:
        self._systemctl_result("enable", STACK_UNIT)

    def _disable_stack(self) -> None:
        self._systemctl_result("disable", STACK_UNIT, allowed_returncodes=frozenset({0, 1}))

    def _arm_stack_supervisor(self) -> None:
        """Make the no-op supervisor active before a transaction is armed.

        Starting the unit while no transaction exists is important: its
        ExecStop can then stop the active Compose project for an explicit
        systemd lifecycle operation, without recursively starting a second
        controller during cutover.
        """

        self._enable_stack()
        active = (
            self._systemctl_result(
                "is-active",
                STACK_UNIT,
                allowed_returncodes=frozenset({0, 1, 3, 4}),
            ).returncode
            == 0
        )
        if not active:
            self._systemctl_result("start", STACK_UNIT)

    def _guard_unit(self, token: str) -> str:
        trusted = _required_pattern(token, TOKEN_PATTERN, "transaction token")
        return f"{GUARD_UNIT_PREFIX}{trusted}{GUARD_UNIT_SUFFIX}"

    def _start_guard(self, token: str) -> None:
        unit = self._guard_unit(token)
        self._systemctl_result("enable", unit)
        try:
            self._systemctl_result("start", unit)
        except Exception:
            self._systemctl_result("disable", unit, allowed_returncodes=frozenset({0, 1}))
            raise

    def _finish_guard(self, token: str, *, stop: bool = True) -> None:
        unit = self._guard_unit(token)
        if stop:
            self._systemctl_result("stop", unit, allowed_returncodes=frozenset({0, 1, 5}))
        self._systemctl_result("disable", unit, allowed_returncodes=frozenset({0, 1}))

    def _boot_id(self) -> str:
        path = Path("/proc/sys/kernel/random/boot_id")
        try:
            value = path.read_text(encoding="ascii").strip().lower()
        except (OSError, UnicodeError) as exc:
            raise ContainerReleaseError("kernel boot identity is unavailable") from exc
        return _required_pattern(value, BOOT_ID_PATTERN, "kernel boot identity")

    def _process_start_ticks(self, pid: int) -> int:
        try:
            data = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
            # The command name is parenthesized and may contain spaces.  Field
            # 22 (starttime) is element 20 after the final right parenthesis.
            fields = data.rsplit(")", 1)[1].strip().split()
            value = int(fields[19])
        except (OSError, UnicodeError, ValueError, IndexError) as exc:
            raise ContainerReleaseError("controller process identity is unavailable") from exc
        if value <= 0:
            raise ContainerReleaseError("controller process identity is invalid")
        return value

    def _owner_alive(self, transaction: Mapping[str, Any]) -> bool:
        if transaction["owner_boot_id"] != self._boot_id():
            return False
        try:
            os.kill(transaction["owner_pid"], 0)
            return self._process_start_ticks(transaction["owner_pid"]) == transaction["owner_start_ticks"]
        except (OSError, ContainerReleaseError):
            return False

    def _wait_for_pidfd_exit(self, descriptor: int, timeout_seconds: float) -> bool:
        poller = select.poll()
        poller.register(descriptor, select.POLLIN | select.POLLHUP | select.POLLERR)
        return bool(poller.poll(max(1, int(timeout_seconds * 1000))))

    def _fence_expired_owner(self, transaction: Mapping[str, Any]) -> None:
        """Terminate only the process identity recorded by the durable journal.

        ``pidfd_open`` pins the kernel process object.  The start-ticks check is
        repeated *after* opening the pidfd, so PID reuse can never cause a
        signal to be delivered to an unrelated process.
        """

        if transaction["owner_boot_id"] != self._boot_id():
            return
        pid = transaction["owner_pid"]
        if pid == os.getpid():
            raise ContainerReleaseError("guard cannot fence its own process")
        if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
            raise ContainerReleaseError("safe pidfd owner fencing is unavailable")
        try:
            descriptor = os.pidfd_open(pid, 0)
        except ProcessLookupError:
            return
        except OSError as exc:
            raise ContainerReleaseError("controller pidfd could not be opened") from exc
        try:
            try:
                current_start_ticks = self._process_start_ticks(pid)
            except ContainerReleaseError:
                return
            if current_start_ticks != transaction["owner_start_ticks"]:
                # The recorded controller is gone and this PID belongs to a
                # different process.  The pinned process is intentionally left
                # untouched; recovery may safely proceed for the old owner.
                return
            try:
                signal.pidfd_send_signal(descriptor, signal.SIGTERM)
            except ProcessLookupError:
                return
            except OSError as exc:
                raise ContainerReleaseError("expired controller could not be terminated") from exc
            if self._wait_for_pidfd_exit(descriptor, OWNER_TERMINATION_SECONDS):
                return
            try:
                signal.pidfd_send_signal(descriptor, signal.SIGKILL)
            except ProcessLookupError:
                return
            except OSError as exc:
                raise ContainerReleaseError("expired controller could not be fenced") from exc
            if not self._wait_for_pidfd_exit(descriptor, OWNER_TERMINATION_SECONDS):
                raise ContainerReleaseError("expired controller did not exit after fencing")
        finally:
            os.close(descriptor)

    def _new_transaction(
        self,
        *,
        operation: str,
        target: Mapping[str, Any] | None,
        previous_state: Mapping[str, Any] | None,
        native_snapshot: Mapping[str, Any],
        token: str | None = None,
    ) -> dict[str, Any]:
        epoch = int(self.clock())
        transaction = {
            "schema_version": SCHEMA_VERSION,
            "token": (
                os.urandom(16).hex() if token is None else _required_pattern(token, TOKEN_PATTERN, "transaction token")
            ),
            "operation": operation,
            "phase": "prepared",
            "target": None if target is None else dict(target),
            "previous_state": None if previous_state is None else dict(previous_state),
            "native_snapshot": dict(native_snapshot),
            "candidate_started": False,
            "cutover_started": False,
            "owner_pid": os.getpid(),
            "owner_start_ticks": self._process_start_ticks(os.getpid()),
            "owner_boot_id": self._boot_id(),
            "created_epoch": epoch,
            "updated_epoch": epoch,
            "deadline_epoch": epoch + TRANSACTION_DEADLINE_SECONDS,
        }
        return _validate_transaction(transaction)

    def _update_transaction(self, token: str, **changes: Any) -> dict[str, Any]:
        with self._operation_lock():
            transaction = self._read_transaction(required=True)
            assert transaction is not None
            if transaction["token"] != token:
                raise ContainerReleaseError("container transaction token changed")
            unknown = set(changes) - {
                "phase",
                "candidate_started",
                "cutover_started",
                "owner_pid",
                "owner_start_ticks",
                "owner_boot_id",
            }
            if unknown:
                raise ContainerReleaseError("container transaction update is outside the allowlist")
            transaction.update(changes)
            transaction["updated_epoch"] = int(self.clock())
            transaction["deadline_epoch"] = transaction["updated_epoch"] + TRANSACTION_DEADLINE_SECONDS
            trusted = _validate_transaction(transaction)
            self._write_transaction(trusted)
            return trusted

    def _create_transaction(
        self,
        *,
        operation: str,
        target: Mapping[str, Any] | None,
        previous_state: Mapping[str, Any] | None,
        native_snapshot: Mapping[str, Any],
        expected_generation: int | None = None,
        expected_active_release_digest: str | None = None,
        expected_previous_release_digest: str | None = None,
        expected_state_sha256: str | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        with self._operation_lock():
            if self._read_transaction() is not None:
                raise ContainerReleaseError("an unfinished container transaction already exists")
            if self._read_native_intent() is not None:
                raise ContainerReleaseError("an active native deployment intent blocks containers")
            # This CAS and the durable journal creation deliberately share the
            # operation-lock critical section.  A pre-status check in the Ops
            # worker is diagnostic only; this check is the cutover authority.
            live_state = self._read_state()
            if live_state != previous_state:
                raise ContainerReleaseError("container runtime state changed before transaction")
            live_generation = 0 if live_state is None else live_state["generation"]
            live_active = NATIVE_RELEASE_SENTINEL if live_state is None else live_state["active"]["release_digest"]
            live_previous = (
                NATIVE_RELEASE_SENTINEL
                if live_state is None or live_state["previous"] is None
                else live_state["previous"]["release_digest"]
            )
            supplied = (
                expected_generation,
                expected_active_release_digest,
                expected_previous_release_digest,
                expected_state_sha256,
            )
            if all(value is None for value in supplied):
                # Direct in-process callers retain compatibility; every
                # privileged CLI path requires all four explicit CAS argv.
                expected_generation = live_generation
                expected_active_release_digest = live_active
                expected_previous_release_digest = live_previous
                expected_state_sha256 = _state_sha256(live_state)
            elif any(value is None for value in supplied):
                raise ContainerReleaseError("container runtime CAS tuple is incomplete")
            if (
                type(expected_generation) is not int
                or not 0 <= expected_generation <= 1_000_000_000
                or expected_generation != live_generation
                or _required_pattern(
                    expected_active_release_digest,
                    RELEASE_DIGEST_PATTERN,
                    "expected active release digest",
                )
                != live_active
                or _required_pattern(
                    expected_previous_release_digest,
                    RELEASE_DIGEST_PATTERN,
                    "expected previous release digest",
                )
                != live_previous
                or _required_pattern(
                    expected_state_sha256,
                    RELEASE_DIGEST_PATTERN,
                    "expected controller state hash",
                )
                != _state_sha256(live_state)
            ):
                raise ContainerReleaseError("container runtime compare-and-swap precondition failed")
            transaction = self._new_transaction(
                operation=operation,
                target=target,
                previous_state=previous_state,
                native_snapshot=native_snapshot,
                token=token,
            )
            self._write_transaction(transaction)
            return transaction

    def _loaded_from_reference(self, reference: Mapping[str, Any]) -> LoadedRelease:
        trusted = _validate_release_reference(dict(reference))
        loaded = self._load_release(
            trusted["source_tree"],
            require_current_receipt=False,
            load_images=False,
        )
        if loaded.reference != trusted:
            raise ContainerReleaseError("stored release reference differs from its bundle")
        return loaded

    def _stop_previous_runtime(self, transaction: Mapping[str, Any]) -> None:
        previous_state = transaction["previous_state"]
        if previous_state is None:
            self._stop_native()
            self._verify_native_fallback(transaction["native_snapshot"])
            return
        current = self._loaded_from_reference(previous_state["active"])
        self._stop_active(current)

    def _new_active_state(
        self,
        *,
        target: Mapping[str, Any],
        previous_state: Mapping[str, Any] | None,
        native_snapshot: Mapping[str, Any],
        rollback: bool,
    ) -> dict[str, Any]:
        trusted_target = _validate_release_reference(dict(target), label="new active release")
        if previous_state is None:
            generation = 1
            previous = None
        else:
            trusted_previous_state = _validate_state(dict(previous_state))
            generation = trusted_previous_state["generation"] + 1
            previous = trusted_previous_state["active"]
            if not rollback and previous["release_digest"] == trusted_target["release_digest"]:
                raise ContainerReleaseError("release is already active")
        return _validate_state(
            {
                "schema_version": SCHEMA_VERSION,
                "generation": generation,
                "mode": "docker",
                "active": trusted_target,
                "previous": previous,
                "native_fallback": dict(native_snapshot),
                "updated_at": _utc_timestamp(self.clock()),
            }
        )

    def _commit_transaction(
        self,
        token: str,
        *,
        new_state: Mapping[str, Any] | None,
    ) -> None:
        with self._operation_lock():
            transaction = self._read_transaction(required=True)
            assert transaction is not None
            if transaction["token"] != token:
                raise ContainerReleaseError("container transaction token changed")
            if new_state is None:
                self._remove_file(self.paths.state_file)
            else:
                self._write_state(new_state)
            self._remove_file(self.paths.transaction_file)
        # The durable commit point is the transaction-file removal.  A stale
        # enabled guard instance is harmless (it immediately exits when no
        # transaction exists), so a systemd cleanup error must not turn a
        # successfully committed release into a false rollback attempt.
        with contextlib.suppress(ContainerReleaseError):
            self._finish_guard(token)

    def _perform_target_switch(
        self,
        transaction: Mapping[str, Any],
        target_release: LoadedRelease,
        *,
        rollback: bool,
    ) -> dict[str, Any]:
        token = transaction["token"]
        self._update_transaction(
            token,
            phase="candidate_starting",
            candidate_started=True,
        )
        self._start_candidate(target_release)
        self._update_transaction(token, phase="candidate_verified")
        self._update_transaction(
            token,
            phase="cutting_over",
            cutover_started=True,
        )
        self._stop_previous_runtime(transaction)
        self._update_transaction(token, phase="active_verifying")
        self._start_active(target_release)
        # The host nginx origin remains outside Compose.  Verify that its live
        # loopback route now reaches the newly active containers before the
        # durable commit point.
        self._verify_host_origin()
        self._update_transaction(token, phase="committing")
        self._enable_stack()
        new_state = self._new_active_state(
            target=target_release.reference,
            previous_state=transaction["previous_state"],
            native_snapshot=transaction["native_snapshot"],
            rollback=rollback,
        )
        self._cleanup_candidate(target_release)
        self._commit_transaction(token, new_state=new_state)
        return new_state

    def _recover_transaction(self, token: str, *, stop_guard: bool = True) -> None:
        transaction = self._update_transaction(
            token,
            phase="rolling_back",
            owner_pid=os.getpid(),
            owner_start_ticks=self._process_start_ticks(os.getpid()),
            owner_boot_id=self._boot_id(),
        )
        try:
            target_release = (
                None if transaction["target"] is None else self._loaded_from_reference(transaction["target"])
            )
            previous_state = transaction["previous_state"]
            previous_release = None if previous_state is None else self._loaded_from_reference(previous_state["active"])
            if transaction["candidate_started"] and target_release is not None:
                self._cleanup_candidate(target_release)
            if transaction["cutover_started"]:
                stop_release = target_release or previous_release
                if stop_release is not None:
                    self._stop_active(stop_release)
                if previous_release is None:
                    with self._authorize_native_restore(token):
                        self._restore_native(transaction["native_snapshot"])
                else:
                    if transaction["operation"] == "rollback_native":
                        with self._authorize_native_restore(token):
                            self._stop_native()
                        self._assert_native_units_disabled()
                    self._start_active(previous_release)
                self._verify_host_origin()
            if previous_release is None:
                self._disable_stack()
            else:
                self._enable_stack()
            with self._operation_lock():
                current = self._read_transaction(required=True)
                assert current is not None
                if current["token"] != token:
                    raise ContainerReleaseError("container transaction token changed")
                if previous_state is None:
                    self._remove_file(self.paths.state_file)
                else:
                    self._write_state(previous_state)
                self._remove_file(self.paths.transaction_file)
            # Recovery is complete once the prior state and absent journal are
            # durable.  Guard-unit cleanup is best effort for the same reason
            # as normal commit cleanup.
            with contextlib.suppress(ContainerReleaseError):
                self._finish_guard(token, stop=stop_guard)
        except Exception:
            with contextlib.suppress(Exception):
                self._update_transaction(
                    token,
                    phase="rollback_failed",
                    owner_pid=os.getpid(),
                    owner_start_ticks=self._process_start_ticks(os.getpid()),
                    owner_boot_id=self._boot_id(),
                )
            raise

    def _copy_ingress_release(self, source_tree: str, destination: Path) -> None:
        """Copy the fixed unprivileged inbox through no-follow file descriptors."""

        config = load_bootstrap_config(trusted_uid=self.trusted_uid)
        deploy_uid = config["deploy_uid"]
        deploy_gid = config["deploy_gid"]
        assert isinstance(deploy_uid, int)
        assert isinstance(deploy_gid, int)
        directory_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        directory_flags |= getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        input_root_fd = input_release_fd = -1
        expected_names = {
            "release.json",
            "validation.json",
            "images.tar",
            "compose.production.yaml",
        }
        maximum_sizes = {
            "release.json": 256 * 1024,
            "validation.json": 256 * 1024,
            "images.tar": MAX_BUNDLE_BYTES,
            "compose.production.yaml": 1024 * 1024,
        }
        try:
            input_root_fd = os.open(INGRESS_ROOT, directory_flags)
            root_metadata = os.fstat(input_root_fd)
            if (
                not stat.S_ISDIR(root_metadata.st_mode)
                or root_metadata.st_uid != deploy_uid
                or root_metadata.st_gid != deploy_gid
                or stat.S_IMODE(root_metadata.st_mode) != 0o700
            ):
                raise ContainerReleaseError("container ingress root is unsafe")
            input_release_fd = os.open(source_tree, directory_flags, dir_fd=input_root_fd)
            release_metadata = os.fstat(input_release_fd)
            if (
                not stat.S_ISDIR(release_metadata.st_mode)
                or release_metadata.st_uid != deploy_uid
                or release_metadata.st_gid != deploy_gid
                or stat.S_IMODE(release_metadata.st_mode) != 0o700
            ):
                raise ContainerReleaseError("container ingress release is unsafe")
            if set(os.listdir(input_release_fd)) != expected_names:
                raise ContainerReleaseError("container ingress contents do not match the allowlist")

            destination.mkdir(mode=0o700, parents=False, exist_ok=False)
            for name in sorted(expected_names):
                source_fd = target_fd = -1
                try:
                    source_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
                    source_flags |= getattr(os, "O_NOFOLLOW", 0)
                    source_fd = os.open(name, source_flags, dir_fd=input_release_fd)
                    metadata = os.fstat(source_fd)
                    if (
                        not stat.S_ISREG(metadata.st_mode)
                        or metadata.st_uid != deploy_uid
                        or metadata.st_gid != deploy_gid
                        or stat.S_IMODE(metadata.st_mode) & 0o022
                        or metadata.st_size <= 0
                        or metadata.st_size > maximum_sizes[name]
                    ):
                        raise ContainerReleaseError(f"container ingress file is unsafe: {name}")
                    mode = 0o600 if name == "images.tar" else 0o644
                    target_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                    target_flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
                    target_fd = os.open(destination / name, target_flags, mode)
                    os.fchmod(target_fd, mode)
                    total = 0
                    while True:
                        chunk = os.read(source_fd, 1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > maximum_sizes[name]:
                            raise ContainerReleaseError(f"container ingress file grew: {name}")
                        view = memoryview(chunk)
                        while view:
                            written = os.write(target_fd, view)
                            view = view[written:]
                    if total <= 0:
                        raise ContainerReleaseError(f"container ingress file is empty: {name}")
                    os.fsync(target_fd)
                except OSError as exc:
                    raise ContainerReleaseError(f"container ingress copy failed: {name}") from exc
                finally:
                    if source_fd >= 0:
                        os.close(source_fd)
                    if target_fd >= 0:
                        os.close(target_fd)
            _fsync_directory(destination)
        except OSError as exc:
            raise ContainerReleaseError("container ingress is unavailable") from exc
        finally:
            if input_release_fd >= 0:
                os.close(input_release_fd)
            if input_root_fd >= 0:
                os.close(input_root_fd)

    @staticmethod
    def _discard_staged_release(directory: Path) -> None:
        if not directory.exists() or directory.is_symlink():
            return
        expected_names = {
            "release.json",
            "validation.json",
            "images.tar",
            "compose.production.yaml",
        }
        try:
            actual = {entry.name for entry in directory.iterdir()}
            if actual <= expected_names:
                for name in actual:
                    (directory / name).unlink()
                directory.rmdir()
        except OSError:
            return

    def _validate_staged_release(self, directory: Path, source_tree: str) -> tuple[dict[str, Any], dict[str, Any]]:
        trusted_directory = self._validate_private_directory(directory, create=False)
        expected_names = {
            "release.json",
            "validation.json",
            "images.tar",
            "compose.production.yaml",
        }
        if {entry.name for entry in trusted_directory.iterdir()} != expected_names:
            raise ContainerReleaseError("staged release contents are not exact")
        for name in expected_names:
            candidate = trusted_directory / name
            metadata = candidate.lstat()
            if (
                candidate.is_symlink()
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != self.trusted_uid
                or stat.S_IMODE(metadata.st_mode) & 0o022
            ):
                raise ContainerReleaseError(f"staged release file is unsafe: {name}")
        verify_release_artifacts(trusted_directory)
        manifest = load_json_evidence(trusted_directory / "release.json")
        receipt = load_json_evidence(trusted_directory / "validation.json", receipt=True)
        if manifest["source_tree"] != source_tree:
            raise ContainerReleaseError("staged release is not bound to its source tree")
        promotion = bind_promotion_evidence(
            manifest,
            receipt,
            now=_utc_timestamp(self.clock()),
        )
        if promotion.receipt["target"] != DEVELOPMENT_RECEIPT_TARGET:
            raise ContainerReleaseError("staged release target is not an2p-dev")
        if promotion.receipt["target_identity"] != self._development_target_identity():
            raise ContainerReleaseError("staged validation identity is not trusted")
        if self.enforce_installation_receipt:
            validate_installed_runtime(
                manifest["build_policy_sha256"],
                trusted_uid=self.trusted_uid,
            )
        return manifest, promotion.receipt

    def stage(
        self,
        source_tree: str,
        job_id: str | None = None,
        claim_epoch: int | None = None,
        claim_token: str | None = None,
    ) -> dict[str, Any]:
        """Install one fixed-inbox release into the private new-only root."""

        self._require_root()
        self._ensure_layout()
        self._require_worker_lease(job_id, claim_epoch, claim_token)
        tree = _required_pattern(source_tree, SOURCE_TREE_PATTERN, "source tree")
        destination = self.paths.release_root / tree
        temporary = self.paths.release_root / f".incoming-{tree}-{os.urandom(8).hex()}"
        with self._operation_lock():
            if self._read_transaction() is not None:
                raise ContainerReleaseError("an active transaction blocks release staging")
            if destination.exists() or destination.is_symlink():
                try:
                    manifest, _receipt = self._validate_staged_release(destination, tree)
                except (ManifestError, RuntimeIntegrityError, VerificationError) as exc:
                    raise ContainerReleaseError("existing release destination is not the exact staged release") from exc
                return {
                    "schema_version": SCHEMA_VERSION,
                    "staged": True,
                    "source_tree": tree,
                    "release_digest": manifest["release_digest"],
                    "bundle_sha256": manifest["bundle_sha256"],
                    "compose_sha256": manifest["compose_sha256"],
                    "image_ids": {service: manifest["images"][service]["image_id"] for service in ("api", "frontend")},
                }
            try:
                self._copy_ingress_release(tree, temporary)
                manifest, _receipt = self._validate_staged_release(temporary, tree)
                os.rename(temporary, destination)
                _fsync_directory(self.paths.release_root)
            except (ManifestError, RuntimeIntegrityError, VerificationError) as exc:
                raise ContainerReleaseError("staged release evidence is invalid") from exc
            finally:
                self._discard_staged_release(temporary)
        return {
            "schema_version": SCHEMA_VERSION,
            "staged": True,
            "source_tree": tree,
            "release_digest": manifest["release_digest"],
            "bundle_sha256": manifest["bundle_sha256"],
            "compose_sha256": manifest["compose_sha256"],
            "image_ids": {service: manifest["images"][service]["image_id"] for service in ("api", "frontend")},
        }

    def load_images(
        self,
        source_tree: str,
        job_id: str | None = None,
        claim_epoch: int | None = None,
        claim_token: str | None = None,
    ) -> dict[str, Any]:
        """Explicitly load and verify image-store state without changing the app runtime."""

        self._require_root()
        self._ensure_layout()
        self._require_worker_lease(job_id, claim_epoch, claim_token)
        if self._read_transaction() is not None:
            raise ContainerReleaseError("an active transaction blocks image loading")
        self._validate_runtime_installation()
        self._require_local_docker()
        release = self._load_release(
            source_tree,
            require_current_receipt=True,
            load_images=True,
            require_installed_policy=True,
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "images_loaded": True,
            **release.reference,
        }

    def preflight(
        self,
        source_tree: str,
        job_id: str | None = None,
        claim_epoch: int | None = None,
        claim_token: str | None = None,
    ) -> dict[str, Any]:
        """Run every read-only cutover gate without loading images or arming state."""

        self._require_root()
        self._ensure_layout()
        self._require_worker_lease(job_id, claim_epoch, claim_token)
        if self._read_transaction() is not None:
            raise ContainerReleaseError("an active transaction blocks production preflight")
        self._require_local_docker()
        self._validate_host_inputs()
        release = self._load_release(
            source_tree,
            require_current_receipt=True,
            load_images=False,
            require_installed_policy=True,
        )
        self._verify_migration_ledger(release)
        self._verify_host_origin()
        return {
            "schema_version": SCHEMA_VERSION,
            "preflight": "passed",
            **release.reference,
            "migration_ledger_sha256": release.manifest["migration_ledger_sha256"],
        }

    def promote(
        self,
        source_tree: str,
        expected_generation: int | None = None,
        expected_active_release_digest: str | None = None,
        expected_previous_release_digest: str | None = None,
        expected_state_sha256: str | None = None,
        job_id: str | None = None,
        claim_epoch: int | None = None,
        claim_token: str | None = None,
    ) -> dict[str, Any]:
        self._require_root()
        self._ensure_layout()
        self._require_worker_lease(job_id, claim_epoch, claim_token)
        self._require_local_docker()
        self._validate_host_inputs()
        release = self._load_release(
            source_tree,
            require_current_receipt=True,
            load_images=True,
            require_installed_policy=True,
        )
        state = self._read_state()
        if state is not None and state["active"]["release_digest"] == release.reference["release_digest"]:
            raise ContainerReleaseError("release is already active")
        # Container promotion is deliberately schema-read-only.  Pending
        # migrations must first be applied by the existing backup-guarded
        # native release path, then the exact same image is revalidated.  Run
        # this before arming either the supervisor or transaction so a pending
        # ledger changes no production runtime state at all.
        self._verify_migration_ledger(release)
        native_snapshot = self._capture_native_fallback() if state is None else state["native_fallback"]
        self._arm_stack_supervisor()
        # Start the durable guard before publishing its journal.  The guard
        # waits briefly for this exact token, closing the SIGKILL gap that
        # would otherwise exist between transaction commit and systemd arm.
        token = os.urandom(16).hex()
        self._start_guard(token)
        try:
            transaction = self._create_transaction(
                operation="promote",
                target=release.reference,
                previous_state=state,
                native_snapshot=native_snapshot,
                expected_generation=expected_generation,
                expected_active_release_digest=expected_active_release_digest,
                expected_previous_release_digest=expected_previous_release_digest,
                expected_state_sha256=expected_state_sha256,
                token=token,
            )
        except Exception:
            with contextlib.suppress(ContainerReleaseError):
                self._finish_guard(token)
            raise
        try:
            if state is None:
                # The durable guard journal is already published. Full-tree
                # hashing is deliberately after that point so a slow 16 GiB
                # baseline check cannot outlive the guard arm timeout.
                self._verify_native_fallback(transaction["native_snapshot"])
            return self._perform_target_switch(
                transaction,
                release,
                rollback=False,
            )
        except Exception as original:
            try:
                self._recover_transaction(token)
            except Exception as recovery:
                raise ContainerReleaseError(
                    "container promotion failed and automatic rollback did not converge"
                ) from recovery
            raise ContainerReleaseError("container promotion failed and was rolled back") from original

    def rollback(
        self,
        expected_generation: int | None = None,
        expected_active_release_digest: str | None = None,
        expected_previous_release_digest: str | None = None,
        expected_state_sha256: str | None = None,
        job_id: str | None = None,
        claim_epoch: int | None = None,
        claim_token: str | None = None,
    ) -> dict[str, Any]:
        self._require_root()
        self._ensure_layout()
        self._require_worker_lease(job_id, claim_epoch, claim_token)
        self._require_local_docker()
        self._validate_host_inputs()
        state = self._read_state()
        if state is None:
            raise ContainerReleaseError("there is no active Docker release to roll back")
        target_reference = state["previous"]
        if target_reference is None:
            raise ContainerReleaseError("container rollback requires a previous Docker release")
        target_release = self._loaded_from_reference(target_reference)
        self._arm_stack_supervisor()
        token = os.urandom(16).hex()
        self._start_guard(token)
        try:
            transaction = self._create_transaction(
                operation="rollback",
                target=target_reference,
                previous_state=state,
                native_snapshot=state["native_fallback"],
                expected_generation=expected_generation,
                expected_active_release_digest=expected_active_release_digest,
                expected_previous_release_digest=expected_previous_release_digest,
                expected_state_sha256=expected_state_sha256,
                token=token,
            )
        except Exception:
            with contextlib.suppress(ContainerReleaseError):
                self._finish_guard(token)
            raise
        try:
            return self._perform_target_switch(
                transaction,
                target_release,
                rollback=True,
            )
        except Exception as original:
            try:
                self._recover_transaction(token)
            except Exception as recovery:
                raise ContainerReleaseError(
                    "container rollback failed and automatic recovery did not converge"
                ) from recovery
            raise ContainerReleaseError("container rollback failed; active release was restored") from original

    def rollback_native(
        self,
        expected_generation: int | None = None,
        expected_active_release_digest: str | None = None,
        expected_previous_release_digest: str | None = None,
        expected_state_sha256: str | None = None,
        job_id: str | None = None,
        claim_epoch: int | None = None,
        claim_token: str | None = None,
    ) -> None:
        """Transition any active Docker generation to its pinned native baseline."""

        self._require_root()
        self._ensure_layout()
        self._require_worker_lease(job_id, claim_epoch, claim_token)
        self._require_local_docker()
        self._validate_host_inputs()
        state = self._read_state()
        if state is None:
            raise ContainerReleaseError("there is no active Docker release for native maintenance")
        self._arm_stack_supervisor()
        token = os.urandom(16).hex()
        self._start_guard(token)
        try:
            transaction = self._create_transaction(
                operation="rollback_native",
                target=None,
                previous_state=state,
                native_snapshot=state["native_fallback"],
                expected_generation=expected_generation,
                expected_active_release_digest=expected_active_release_digest,
                expected_previous_release_digest=expected_previous_release_digest,
                expected_state_sha256=expected_state_sha256,
                token=token,
            )
        except Exception:
            with contextlib.suppress(ContainerReleaseError):
                self._finish_guard(token)
            raise
        try:
            # Publish the guard-owned journal before the bounded full native
            # tree/control-byte verification. No runtime mutation has started.
            self._verify_native_fallback(transaction["native_snapshot"])
            self._update_transaction(
                token,
                phase="cutting_over",
                cutover_started=True,
            )
            current_release = self._loaded_from_reference(state["active"])
            self._stop_active(current_release)
            with self._authorize_native_restore(token):
                self._restore_native(state["native_fallback"])
            self._verify_host_origin()
            self._disable_stack()
            self._update_transaction(token, phase="committing")
            self._commit_transaction(token, new_state=None)
            return None
        except Exception as original:
            try:
                self._recover_transaction(token)
            except Exception as recovery:
                raise ContainerReleaseError(
                    "native maintenance transition failed and automatic recovery did not converge"
                ) from recovery
            raise ContainerReleaseError(
                "native maintenance transition failed; active Docker release was restored"
            ) from original

    def ensure_active(self) -> dict[str, Any] | None:
        self._require_root()
        self._ensure_layout()
        if self._read_transaction() is not None:
            raise ContainerReleaseError("an unfinished transaction must recover before stack startup")
        state = self._read_state()
        if state is None:
            return None
        self._assert_native_units_disabled()
        self._require_local_docker()
        self._validate_host_inputs()
        release = self._loaded_from_reference(state["active"])
        self._start_active(release)
        return state

    def stop_active(self) -> dict[str, Any] | None:
        self._require_root()
        self._ensure_layout()
        if self._read_transaction() is not None:
            raise ContainerReleaseError("an unfinished transaction owns the active stack")
        state = self._read_state()
        if state is None:
            return None
        self._require_local_docker()
        self._validate_host_inputs()
        release = self._loaded_from_reference(state["active"])
        self._stop_active(release)
        return state

    def guard(self, token: str, *, once: bool = False) -> None:
        self._require_root()
        self._ensure_layout()
        trusted_token = _required_pattern(token, TOKEN_PATTERN, "transaction token")
        observed_transaction = False
        arm_deadline = time.monotonic() + GUARD_ARM_TIMEOUT_SECONDS
        while True:
            transaction = self._read_transaction()
            if transaction is None:
                if once or observed_transaction:
                    return
                if time.monotonic() >= arm_deadline:
                    # A controller that died before journal publication left
                    # no runtime state to recover.  Remove the stale enablement
                    # without synchronously stopping this guard process.
                    with contextlib.suppress(ContainerReleaseError):
                        self._finish_guard(trusted_token, stop=False)
                    return
                self.sleeper(GUARD_POLL_SECONDS)
                continue
            if transaction["token"] != trusted_token:
                raise ContainerReleaseError("guard token does not own the transaction")
            observed_transaction = True
            expired = int(self.clock()) > transaction["deadline_epoch"]
            owner_alive = self._owner_alive(transaction)
            if expired and owner_alive:
                self._fence_expired_owner(transaction)
                owner_alive = False
            if not owner_alive:
                # The owner may have completed the durable commit while the
                # guard was opening its pidfd.  Re-read before claiming the
                # journal so a successful commit is never reversed.
                current = self._read_transaction()
                if current is None:
                    return
                if current["token"] != trusted_token:
                    raise ContainerReleaseError("guard transaction changed during fencing")
                self._require_local_docker()
                self._validate_host_inputs()
                # This process is the main process of the guard unit.  It must
                # disable the instance and return naturally instead of asking
                # systemd to synchronously stop itself.
                self._recover_transaction(trusted_token, stop_guard=False)
                return
            if once:
                return
            self.sleeper(GUARD_POLL_SECONDS)

    def status(self) -> dict[str, Any]:
        self._require_root()
        self._ensure_layout()
        # State commit removes the transaction journal under this same lock.
        # Reading both values in one critical section prevents a false
        # (state=null, transaction=null) snapshot during cutover.
        with self._operation_lock():
            return {
                "schema_version": SCHEMA_VERSION,
                "native_intent": self._read_native_intent(),
                "state": self._read_state(),
                "transaction": self._read_transaction(),
                "worker_lease": self._read_worker_lease(),
            }

    def native_begin(self, token: str) -> dict[str, Any]:
        """Durably fence container cutovers before a native deployment starts."""

        self._require_root()
        self._ensure_layout()
        trusted_token = _required_pattern(token, TOKEN_PATTERN, "native deployment token")
        with self._operation_lock():
            if self._read_transaction() is not None or self._read_state() is not None:
                raise ContainerReleaseError("container runtime blocks native deployment intent")
            existing = self._read_native_intent()
            expected = {"schema_version": SCHEMA_VERSION, "token": trusted_token}
            if existing is not None and existing != expected:
                raise ContainerReleaseError("another native deployment intent is active")
            if existing is None:
                self._atomic_write(self.paths.native_intent_file, expected)
            return expected

    def native_end(self, token: str) -> dict[str, Any]:
        """Remove only the caller's exact durable native deployment fence."""

        self._require_root()
        self._ensure_layout()
        trusted_token = _required_pattern(token, TOKEN_PATTERN, "native deployment token")
        with self._operation_lock():
            existing = self._read_native_intent()
            if existing is not None and existing["token"] != trusted_token:
                raise ContainerReleaseError("native deployment intent token changed")
            if existing is not None:
                self._remove_file(self.paths.native_intent_file)
            return {
                "schema_version": SCHEMA_VERSION,
                "ended": True,
                "token": trusted_token,
            }

    def target_identity(self) -> dict[str, Any]:
        """Return the single root-owned development validation identity."""

        self._require_root()
        self._ensure_layout()
        return {
            "schema_version": SCHEMA_VERSION,
            "target": DEVELOPMENT_RECEIPT_TARGET,
            "target_identity": self._development_target_identity(),
        }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    def add_worker_claim(command: argparse.ArgumentParser) -> None:
        command.add_argument("job_id")
        command.add_argument("claim_epoch", type=_claim_epoch_argument)
        command.add_argument("claim_token")

    lease_bind = subcommands.add_parser(
        "lease-bind",
        help="exclusively fence older worker commands and bind one DB claim",
    )
    add_worker_claim(lease_bind)
    lease_release = subcommands.add_parser(
        "lease-release",
        help="exclusively wait for and revoke one DB claim",
    )
    add_worker_claim(lease_release)
    stage = subcommands.add_parser("stage", help="install one fixed-inbox source tree")
    stage.add_argument("source_tree")
    add_worker_claim(stage)
    load_images = subcommands.add_parser("load-images", help="load one staged release into the local image store")
    load_images.add_argument("source_tree")
    add_worker_claim(load_images)
    preflight = subcommands.add_parser("preflight", help="run read-only production gates without cutover")
    preflight.add_argument("source_tree")
    add_worker_claim(preflight)
    promote = subcommands.add_parser("promote", help="promote one validated source tree")
    promote.add_argument("source_tree")
    promote.add_argument("expected_generation", type=_generation_argument)
    promote.add_argument("expected_active_release_digest")
    promote.add_argument("expected_previous_release_digest")
    promote.add_argument("expected_state_sha256")
    add_worker_claim(promote)
    rollback = subcommands.add_parser("rollback", help="roll back to the previous Docker or native runtime")
    rollback.add_argument("expected_generation", type=_generation_argument)
    rollback.add_argument("expected_active_release_digest")
    rollback.add_argument("expected_previous_release_digest")
    rollback.add_argument("expected_state_sha256")
    add_worker_claim(rollback)
    rollback_native = subcommands.add_parser(
        "rollback-native",
        help="transition the active Docker runtime to its pinned native baseline",
    )
    rollback_native.add_argument("expected_generation", type=_generation_argument)
    rollback_native.add_argument("expected_active_release_digest")
    rollback_native.add_argument("expected_previous_release_digest")
    rollback_native.add_argument("expected_state_sha256")
    add_worker_claim(rollback_native)
    subcommands.add_parser("ensure-active", help="converge the recorded active stack on boot")
    subcommands.add_parser("stop-active", help="stop the recorded stack for systemd lifecycle")
    guard = subcommands.add_parser("guard", help="recover an orphaned deployment transaction")
    guard.add_argument("token")
    subcommands.add_parser("status", help="emit canonical container release state")
    native_begin = subcommands.add_parser("native-begin", help="create one durable native deployment intent")
    native_begin.add_argument("token")
    native_end = subcommands.add_parser("native-end", help="remove one exact durable native deployment intent")
    native_end.add_argument("token")
    subcommands.add_parser(
        "target-identity",
        help="emit the root-owned an2p validation target identity",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    controller = ContainerReleaseController()
    try:
        controller._require_root()
        controller._ensure_layout()
        if arguments.command == "status":
            # Status must remain observable while a long cutover holds the
            # shared code-upgrade fence. Its own operation lock gives one
            # atomic state/transaction/intent snapshot without deadlocking a
            # native-restore ExecCondition.
            result: object = controller.status()
        elif arguments.command == "target-identity":
            result = controller.target_identity()
        elif arguments.command in {"lease-bind", "lease-release"}:
            # Exclusive acquisition waits for every older shared mutation
            # command to exit before the durable epoch is rotated/revoked.
            with controller._control_lock(exclusive=True):
                if arguments.command == "lease-bind":
                    result = controller.bind_worker_lease(
                        arguments.job_id,
                        arguments.claim_epoch,
                        arguments.claim_token,
                    )
                else:
                    result = controller.release_worker_lease(
                        arguments.job_id,
                        arguments.claim_epoch,
                        arguments.claim_token,
                    )
        else:
            with controller._control_lock():
                if arguments.command == "stage":
                    result = controller.stage(
                        arguments.source_tree,
                        arguments.job_id,
                        arguments.claim_epoch,
                        arguments.claim_token,
                    )
                elif arguments.command == "load-images":
                    result = controller.load_images(
                        arguments.source_tree,
                        arguments.job_id,
                        arguments.claim_epoch,
                        arguments.claim_token,
                    )
                elif arguments.command == "preflight":
                    result = controller.preflight(
                        arguments.source_tree,
                        arguments.job_id,
                        arguments.claim_epoch,
                        arguments.claim_token,
                    )
                elif arguments.command == "promote":
                    result = controller.promote(
                        arguments.source_tree,
                        arguments.expected_generation,
                        arguments.expected_active_release_digest,
                        arguments.expected_previous_release_digest,
                        arguments.expected_state_sha256,
                        arguments.job_id,
                        arguments.claim_epoch,
                        arguments.claim_token,
                    )
                elif arguments.command == "rollback":
                    result = controller.rollback(
                        arguments.expected_generation,
                        arguments.expected_active_release_digest,
                        arguments.expected_previous_release_digest,
                        arguments.expected_state_sha256,
                        arguments.job_id,
                        arguments.claim_epoch,
                        arguments.claim_token,
                    )
                elif arguments.command == "rollback-native":
                    result = controller.rollback_native(
                        arguments.expected_generation,
                        arguments.expected_active_release_digest,
                        arguments.expected_previous_release_digest,
                        arguments.expected_state_sha256,
                        arguments.job_id,
                        arguments.claim_epoch,
                        arguments.claim_token,
                    )
                elif arguments.command == "ensure-active":
                    result = controller.ensure_active()
                elif arguments.command == "stop-active":
                    result = controller.stop_active()
                elif arguments.command == "guard":
                    controller.guard(arguments.token)
                    result = {"recovered": True}
                elif arguments.command == "native-begin":
                    result = controller.native_begin(arguments.token)
                else:
                    result = controller.native_end(arguments.token)
    except (
        ContainerReleaseError,
        ManifestError,
        RuntimeIntegrityError,
        VerificationError,
        OSError,
    ) as exc:
        print(f"mooncen container release: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            result,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
