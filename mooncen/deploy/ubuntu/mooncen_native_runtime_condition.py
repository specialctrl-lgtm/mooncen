#!/usr/bin/env python3
"""Fail closed when a native unit would overlap the container runtime."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Sequence


CONTROLLER = Path("/usr/local/libexec/mooncen-container-release")
STATE_ROOT = Path("/var/lib/mooncen-container-release")
INSTALLATION_RECEIPT = Path("/etc/mooncen/container-runtime-installation.json")
CONTROLLER_LIBRARY = Path("/usr/local/libexec/mooncen-container-release-lib")
NATIVE_RESTORE_AUTHORIZATION = Path(
    "/run/mooncen-container-release/native-restore.json"
)
NATIVE_DEPLOY_LOCK = Path("/opt/.mooncen-deploy.lock")
NATIVE_DEPLOY_AUTHORIZATION_DIRECTORY = Path(
    "/run/mooncen-native-deploy-start"
)
NATIVE_DEPLOY_AUTHORIZATION = (
    NATIVE_DEPLOY_AUTHORIZATION_DIRECTORY / "authorization.json"
)
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
TOKEN_PATTERN = re.compile(r"\A[0-9a-f]{32}\Z")
SHA256_PATTERN = re.compile(r"\A[0-9a-f]{64}\Z")
BOOT_ID_PATTERN = re.compile(
    r"\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z"
)
COMMIT_PATTERN = re.compile(r"\A(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
MAX_STATUS_BYTES = 1024 * 1024
MAX_JOURNAL_BYTES = 64 * 1024

JOURNAL_KEYS = frozenset(
    {
        "ARM_BOOT_ID",
        "DEADLINE_EPOCH",
        "EXPECTED_COMMIT",
        "FAILED_DIR",
        "HAD_ACTIVE",
        "HEARTBEAT",
        "NATIVE_INTENT_TOKEN",
        "PHASE",
        "PREVIOUS_DIR",
        "RELEASE_DIR",
        "REMOTE_DIR",
        "TOKEN",
        "VERSION",
    }
)


class NativeRuntimeConditionError(RuntimeError):
    """Raised when exclusive native-runtime ownership cannot be proved."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise NativeRuntimeConditionError("container status contains duplicate keys")
        value[key] = item
    return value


def _canonical_json(value: dict[str, Any]) -> bytes:
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
        raise NativeRuntimeConditionError("container status is not canonical JSON") from exc


def _parse_canonical_object(payload: bytes, *, label: str) -> dict[str, Any]:
    if not payload or len(payload) > MAX_STATUS_BYTES:
        raise NativeRuntimeConditionError(f"{label} is invalid")
    try:
        value = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                NativeRuntimeConditionError(f"{label} is invalid")
            ),
        )
    except (UnicodeDecodeError, ValueError, TypeError) as exc:
        raise NativeRuntimeConditionError(f"{label} is invalid") from exc
    if not isinstance(value, dict) or _canonical_json(value) != payload:
        raise NativeRuntimeConditionError(f"{label} is not canonical JSON")
    return value


def _safe_root_file(path: Path, mode: int) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise NativeRuntimeConditionError("container runtime control file is unavailable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != mode
    ):
        raise NativeRuntimeConditionError("container runtime control file is unsafe")


def _controller_status() -> dict[str, Any] | None:
    if not CONTROLLER.exists():
        if CONTROLLER.is_symlink():
            raise NativeRuntimeConditionError("container controller path is unsafe")
        for candidate in (STATE_ROOT, INSTALLATION_RECEIPT, CONTROLLER_LIBRARY):
            if candidate.exists() or candidate.is_symlink():
                raise NativeRuntimeConditionError(
                    "container runtime state exists without its root controller"
                )
        return None
    _safe_root_file(CONTROLLER, 0o755)
    try:
        result = subprocess.run(
            (str(CONTROLLER), "status"),
            cwd="/",
            env={
                "HOME": "/root",
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin",
            },
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise NativeRuntimeConditionError("container controller status failed") from exc
    if result.returncode != 0:
        raise NativeRuntimeConditionError("container controller status failed")
    status = _parse_canonical_object(result.stdout, label="container controller status")
    if frozenset(status) != {
        "native_intent",
        "schema_version",
        "state",
        "transaction",
        "worker_lease",
    } or status["schema_version"] != 1:
        raise NativeRuntimeConditionError("container controller status schema is invalid")
    worker_lease = status["worker_lease"]
    if worker_lease is not None:
        if (
            not isinstance(worker_lease, dict)
            or frozenset(worker_lease)
            != {
                "schema_version",
                "job_id",
                "claim_epoch",
                "claim_token_sha256",
                "active",
                "expires_epoch",
            }
            or worker_lease.get("schema_version") != 1
            or not isinstance(worker_lease.get("job_id"), str)
            or TOKEN_PATTERN.fullmatch(worker_lease["job_id"]) is None
            or type(worker_lease.get("claim_epoch")) is not int
            or not 1 <= worker_lease["claim_epoch"] <= 9_223_372_036_854_775_807
            or not isinstance(worker_lease.get("claim_token_sha256"), str)
            or SHA256_PATTERN.fullmatch(worker_lease["claim_token_sha256"]) is None
            or type(worker_lease.get("active")) is not bool
            or type(worker_lease.get("expires_epoch")) is not int
            or worker_lease["expires_epoch"] <= 0
        ):
            raise NativeRuntimeConditionError("container controller worker lease is invalid")
    return status


def _valid_token_object(value: Any, *, key: str) -> str | None:
    if not isinstance(value, dict) or frozenset(value) != {"schema_version", key}:
        return None
    token = value.get(key)
    if value.get("schema_version") != 1 or not isinstance(token, str):
        return None
    return token if TOKEN_PATTERN.fullmatch(token) is not None else None


def _restore_authorization_token() -> str:
    _safe_root_file(NATIVE_RESTORE_AUTHORIZATION, 0o600)
    try:
        payload = NATIVE_RESTORE_AUTHORIZATION.read_bytes()
    except OSError as exc:
        raise NativeRuntimeConditionError(
            "native restore authorization cannot be read"
        ) from exc
    value = _parse_canonical_object(payload, label="native restore authorization")
    token = _valid_token_object(value, key="transaction_token")
    if token is None:
        raise NativeRuntimeConditionError("native restore authorization is invalid")
    try:
        directory = NATIVE_RESTORE_AUTHORIZATION.parent.lstat()
    except OSError as exc:
        raise NativeRuntimeConditionError(
            "native restore authorization directory is unavailable"
        ) from exc
    if (
        NATIVE_RESTORE_AUTHORIZATION.parent.is_symlink()
        or not stat.S_ISDIR(directory.st_mode)
        or directory.st_uid != 0
        or directory.st_gid != 0
        or stat.S_IMODE(directory.st_mode) != 0o700
    ):
        raise NativeRuntimeConditionError(
            "native restore authorization directory is unsafe"
        )
    return token


def _safe_root_directory(path: Path, mode: int, *, label: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise NativeRuntimeConditionError(f"{label} is unavailable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != mode
    ):
        raise NativeRuntimeConditionError(f"{label} is unsafe")


def _read_exact_journal(path: Path) -> dict[str, str]:
    _safe_root_file(path, 0o600)
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise NativeRuntimeConditionError("native deployment journal cannot be read") from exc
    if not payload or len(payload) > MAX_JOURNAL_BYTES or not payload.endswith(b"\n"):
        raise NativeRuntimeConditionError("native deployment journal is invalid")
    try:
        lines = payload.decode("ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise NativeRuntimeConditionError("native deployment journal is invalid") from exc
    values: dict[str, str] = {}
    for line in lines:
        key, separator, value = line.partition("=")
        if (
            not separator
            or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key)
            or key in values
            or "\x00" in value
        ):
            raise NativeRuntimeConditionError("native deployment journal is invalid")
        values[key] = value
    if frozenset(values) != JOURNAL_KEYS:
        raise NativeRuntimeConditionError("native deployment journal schema is invalid")
    token = values["TOKEN"]
    intent_token = values["NATIVE_INTENT_TOKEN"]
    if (
        values["VERSION"] != "1"
        or TOKEN_PATTERN.fullmatch(token) is None
        or TOKEN_PATTERN.fullmatch(intent_token) is None
        or values["REMOTE_DIR"] != "/opt/mooncen"
        or values["RELEASE_DIR"] != f"/opt/.mooncen-release-{token}"
        or values["PREVIOUS_DIR"] != f"/opt/.mooncen-previous-{token}"
        or values["FAILED_DIR"] != f"/opt/.mooncen-failed-{token}"
        or values["HEARTBEAT"] != f"/opt/.mooncen-deploy-heartbeat-{token}"
        or COMMIT_PATTERN.fullmatch(values["EXPECTED_COMMIT"]) is None
        or values["HAD_ACTIVE"] not in {"0", "1"}
        or BOOT_ID_PATTERN.fullmatch(values["ARM_BOOT_ID"]) is None
        or not re.fullmatch(r"[0-9]{10,12}", values["DEADLINE_EPOCH"])
    ):
        raise NativeRuntimeConditionError("native deployment journal is invalid")
    return values


def _guard_is_running(token: str, *, mode: str) -> None:
    try:
        result = subprocess.run(
            (
                "/usr/bin/systemctl",
                "show",
                f"mooncen-deploy-guard@{token}.service",
                "--property=ActiveState",
                "--value",
            ),
            cwd="/",
            env={
                "HOME": "/root",
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin",
            },
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise NativeRuntimeConditionError("native deployment guard status failed") from exc
    allowed_states = {b"active\n"}
    if mode == "recovery":
        # During ordered boot recovery, this check runs from the guard unit's
        # ExecStartPre and the unit is necessarily still activating.
        allowed_states.add(b"activating\n")
    if result.returncode != 0 or result.stdout not in allowed_states:
        raise NativeRuntimeConditionError("native deployment guard is not running")


def _deploy_authorization_token(
    *, expected_intent_token: str | None
) -> tuple[str, str]:
    _safe_root_directory(
        NATIVE_DEPLOY_LOCK,
        0o700,
        label="native deployment lock",
    )
    journal = _read_exact_journal(NATIVE_DEPLOY_LOCK / "journal.env")
    try:
        boot_id = BOOT_ID_PATH.read_text(encoding="ascii").strip()
    except OSError as exc:
        raise NativeRuntimeConditionError("kernel boot identifier is unavailable") from exc
    if BOOT_ID_PATTERN.fullmatch(boot_id) is None:
        raise NativeRuntimeConditionError("kernel boot identifier is invalid")
    lock_token_path = NATIVE_DEPLOY_LOCK / "token"
    _safe_root_file(lock_token_path, 0o600)
    try:
        lock_token_payload = lock_token_path.read_bytes()
    except OSError as exc:
        raise NativeRuntimeConditionError("native deployment lock token cannot be read") from exc
    if lock_token_payload != f'{journal["TOKEN"]}\n'.encode("ascii"):
        raise NativeRuntimeConditionError("native deployment lock token is invalid")

    _safe_root_directory(
        NATIVE_DEPLOY_AUTHORIZATION_DIRECTORY,
        0o700,
        label="native deployment authorization directory",
    )
    _safe_root_file(NATIVE_DEPLOY_AUTHORIZATION, 0o600)
    try:
        payload = NATIVE_DEPLOY_AUTHORIZATION.read_bytes()
    except OSError as exc:
        raise NativeRuntimeConditionError(
            "native deployment authorization cannot be read"
        ) from exc
    authorization = _parse_canonical_object(
        payload,
        label="native deployment authorization",
    )
    if frozenset(authorization) != {
        "arm_boot_id",
        "arm_deadline_epoch",
        "authorization_boot_id",
        "authorization_deadline_epoch",
        "guard_token",
        "intent_token",
        "mode",
        "phase",
        "schema_version",
    }:
        raise NativeRuntimeConditionError("native deployment authorization is invalid")
    guard_token = authorization.get("guard_token")
    intent_token = authorization.get("intent_token")
    mode = authorization.get("mode")
    phase = authorization.get("phase")
    arm_boot_id = authorization.get("arm_boot_id")
    arm_deadline = authorization.get("arm_deadline_epoch")
    authorization_boot_id = authorization.get("authorization_boot_id")
    authorization_deadline = authorization.get("authorization_deadline_epoch")
    now = int(time.time())
    if (
        authorization.get("schema_version") != 1
        or not isinstance(guard_token, str)
        or TOKEN_PATTERN.fullmatch(guard_token) is None
        or not isinstance(intent_token, str)
        or TOKEN_PATTERN.fullmatch(intent_token) is None
        or mode not in {"candidate", "recovery"}
        or not isinstance(phase, str)
        or guard_token != journal["TOKEN"]
        or intent_token != journal["NATIVE_INTENT_TOKEN"]
        or phase != journal["PHASE"]
        or arm_boot_id != journal["ARM_BOOT_ID"]
        or type(arm_deadline) is not int
        or arm_deadline != int(journal["DEADLINE_EPOCH"])
        or type(authorization_deadline) is not int
        or (mode == "candidate" and phase != "activated")
        or (mode == "recovery" and phase not in {"recovering", "recovering_prepared"})
        or (
            expected_intent_token is not None
            and intent_token != expected_intent_token
        )
    ):
        raise NativeRuntimeConditionError("native deployment authorization is invalid")
    if (
        authorization_boot_id != boot_id
        or authorization_deadline <= now
        or authorization_deadline > now + 120
        or (
            mode == "candidate"
            and (boot_id != arm_boot_id or now >= arm_deadline)
        )
    ):
        raise NativeRuntimeConditionError("native deployment authorization is stale")
    _guard_is_running(guard_token, mode=mode)
    return guard_token, intent_token


def assert_native_runtime_allowed() -> None:
    if os.geteuid() != 0:
        raise NativeRuntimeConditionError("native runtime condition must run as root")
    status = _controller_status()
    lock_exists = NATIVE_DEPLOY_LOCK.exists() or NATIVE_DEPLOY_LOCK.is_symlink()
    if status is None:
        if lock_exists:
            _deploy_authorization_token(expected_intent_token=None)
        return

    state = status["state"]
    transaction = status["transaction"]
    native_intent = status["native_intent"]
    worker_lease = status["worker_lease"]
    if state is None and transaction is None:
        # A live remote claim may create a container transaction immediately
        # after this status snapshot.  Deny a *new* native unit start until
        # that claim is released or expires.  rollback-native remains allowed
        # below only through its transaction-bound restore authorization.
        if (
            worker_lease is not None
            and worker_lease["active"] is True
            and worker_lease["expires_epoch"] > int(time.time())
        ):
            raise NativeRuntimeConditionError(
                "active deployment worker lease blocks native runtime start"
            )
        if native_intent is None and not lock_exists:
            return
        intent_token = _valid_token_object(native_intent, key="token")
        if intent_token is None:
            raise NativeRuntimeConditionError("native deployment intent is invalid")
        _deploy_authorization_token(expected_intent_token=intent_token)
        return

    # A controller-owned rollback stops the exact active Compose project
    # before publishing this short-lived token.  It is the only safe reason a
    # native unit may start while a container transaction remains durable.
    if native_intent is not None or not isinstance(transaction, dict):
        raise NativeRuntimeConditionError("container runtime owns the application ports")
    transaction_token = transaction.get("token")
    if (
        not isinstance(transaction_token, str)
        or TOKEN_PATTERN.fullmatch(transaction_token) is None
        or _restore_authorization_token() != transaction_token
    ):
        raise NativeRuntimeConditionError("container runtime owns the application ports")


def main(argv: Sequence[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv:
        print("mooncen native runtime condition: unsupported arguments", file=sys.stderr)
        return 64
    try:
        assert_native_runtime_allowed()
    except (NativeRuntimeConditionError, OSError) as exc:
        print(f"mooncen native runtime condition: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
