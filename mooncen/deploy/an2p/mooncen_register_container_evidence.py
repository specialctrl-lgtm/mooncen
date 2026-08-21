#!/usr/bin/env python3
"""Run the Docker evidence registrar through the isolated worker account.

This root-owned entrypoint accepts one 40-hex source tree and derives every
path and argument from the installed immutable control runtime.  It never
accepts a release path, database credential, SSH option, or arbitrary command.
"""

from __future__ import annotations

import json
import os
import pwd
import re
import socket
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence


RUNTIME_RELEASES = Path("/opt/mooncen-an2p-runtime/releases")
RUNUSER = Path("/usr/sbin/runuser")
ENV = Path("/usr/bin/env")
PAIR_MANAGER = Path("/usr/local/libexec/mooncen-an2p-runtime-manager")
WORKER_ACCOUNT = "mooncen_deployment_worker"
WORKER_HOME = Path("/var/lib/mooncen-deployment-worker")
SOURCE_TREE_PATTERN = re.compile(r"\A[0-9a-f]{40}\Z")
RUNTIME_PAIR_PATTERN = re.compile(
    r"\Aruntime-pair\.(?P<commit>[0-9a-f]{40})\."
    r"(?P<source_tree>[0-9a-f]{40})\.(?P<policy>[0-9a-f]{64})\Z"
)
SHA256_PATTERN = re.compile(r"\A[0-9a-f]{64}\Z")
UUID_PATTERN = re.compile(
    r"\A[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
UTC_PATTERN = re.compile(
    r"\A20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z"
)
RESULT_KEYS = frozenset(
    {
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
)
MAX_RESULT_BYTES = 16 * 1024
PAIR_VALIDATION_KEYS = frozenset({"pair", "schema_version", "source_tree", "valid"})


class EvidenceRegistrationError(RuntimeError):
    """Raised when the fixed local registration boundary is unavailable."""


def _root_owned_executable(path: Path) -> Path:
    try:
        metadata = path.stat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise EvidenceRegistrationError(f"required executable is unavailable: {path}") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not os.access(resolved, os.X_OK)
    ):
        raise EvidenceRegistrationError(f"required executable is unsafe: {path}")
    return resolved


def _trusted_root_directory(path: Path, *, uid: int = 0, gid: int = 0) -> Path:
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise EvidenceRegistrationError(f"immutable runtime directory is unavailable: {path}") from exc
    if (
        path.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != uid
        or metadata.st_gid != gid
        or stat.S_IMODE(metadata.st_mode) != 0o755
    ):
        raise EvidenceRegistrationError(f"immutable runtime directory is unsafe: {path}")
    return resolved


def _immutable_control_runtime(
    source_tree: str,
    *,
    releases: Path = RUNTIME_RELEASES,
    trusted_uid: int = 0,
    trusted_gid: int = 0,
) -> tuple[Path, Path]:
    if SOURCE_TREE_PATTERN.fullmatch(source_tree) is None:
        raise EvidenceRegistrationError("source tree must be exactly 40 lowercase hexadecimal characters")
    trusted_releases = _trusted_root_directory(
        releases,
        uid=trusted_uid,
        gid=trusted_gid,
    )
    candidates: list[Path] = []
    try:
        entries = tuple(trusted_releases.iterdir())
    except OSError as exc:
        raise EvidenceRegistrationError("immutable runtime release root cannot be listed") from exc
    for entry in entries:
        match = RUNTIME_PAIR_PATTERN.fullmatch(entry.name)
        if match is None or match.group("source_tree") != source_tree:
            continue
        pair = _trusted_root_directory(entry, uid=trusted_uid, gid=trusted_gid)
        if pair.parent != trusted_releases:
            raise EvidenceRegistrationError("immutable runtime pair escaped its release root")
        candidates.append(pair)
    if len(candidates) != 1:
        raise EvidenceRegistrationError(
            "exactly one immutable runtime pair must match the requested source tree"
        )
    runtime = _trusted_root_directory(
        candidates[0] / "control",
        uid=trusted_uid,
        gid=trusted_gid,
    )

    python_link = runtime / ".venv/bin/python"
    registrar = runtime / "tools/register_container_deployment_evidence.py"
    try:
        python_metadata = python_link.lstat()
        registrar_metadata = registrar.lstat()
        python_executable = python_link.resolve(strict=True)
        python_target_metadata = python_executable.stat()
    except OSError as exc:
        raise EvidenceRegistrationError("immutable registration runtime is incomplete") from exc
    if (
        not (
            stat.S_ISREG(python_metadata.st_mode)
            or stat.S_ISLNK(python_metadata.st_mode)
        )
        or python_metadata.st_uid != trusted_uid
        or (
            stat.S_ISREG(python_metadata.st_mode)
            and stat.S_IMODE(python_metadata.st_mode) & 0o022
        )
        or not stat.S_ISREG(python_target_metadata.st_mode)
        or python_target_metadata.st_uid != trusted_uid
        or stat.S_IMODE(python_target_metadata.st_mode) & 0o022
        or not os.access(python_executable, os.X_OK)
        or registrar.is_symlink()
        or not stat.S_ISREG(registrar_metadata.st_mode)
        or registrar_metadata.st_uid != trusted_uid
        or registrar_metadata.st_gid != trusted_gid
        or stat.S_IMODE(registrar_metadata.st_mode) & 0o022
    ):
        raise EvidenceRegistrationError("immutable registration runtime is unsafe")
    return runtime, python_link


def _registration_command(source_tree: str, runtime: Path, python: Path) -> tuple[str, ...]:
    if SOURCE_TREE_PATTERN.fullmatch(source_tree) is None:
        raise EvidenceRegistrationError("source tree must be exactly 40 lowercase hexadecimal characters")
    try:
        worker = pwd.getpwnam(WORKER_ACCOUNT)
    except KeyError as exc:
        raise EvidenceRegistrationError("isolated deployment worker account is unavailable") from exc
    if worker.pw_gid <= 0 or worker.pw_dir != str(WORKER_HOME):
        raise EvidenceRegistrationError("isolated deployment worker account drifted")
    runuser = _root_owned_executable(RUNUSER)
    environment = _root_owned_executable(ENV)
    return (
        str(runuser),
        "--user",
        WORKER_ACCOUNT,
        "--",
        str(environment),
        "-i",
        f"HOME={WORKER_HOME}",
        "PATH=/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE=1",
        str(python),
        "-m",
        "tools.register_container_deployment_evidence",
        "--source-tree",
        source_tree,
    )


def _validate_runtime_pair(pair_name: str, source_tree: str) -> None:
    if (
        RUNTIME_PAIR_PATTERN.fullmatch(pair_name) is None
        or SOURCE_TREE_PATTERN.fullmatch(source_tree) is None
    ):
        raise EvidenceRegistrationError("runtime pair identity is invalid")
    manager = _root_owned_executable(PAIR_MANAGER)
    try:
        completed = subprocess.run(
            (str(manager), "validate", pair_name),
            cwd="/",
            env={"HOME": "/root", "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=300,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EvidenceRegistrationError("pending runtime pair could not be validated") from exc
    if (
        completed.returncode != 0
        or completed.stderr
        or not completed.stdout.endswith(b"\n")
        or completed.stdout.count(b"\n") != 1
        or len(completed.stdout) > MAX_RESULT_BYTES
    ):
        raise EvidenceRegistrationError("pending runtime pair validation failed")
    try:
        text = completed.stdout.decode("ascii")
        value = json.loads(text, object_pairs_hook=_unique_object)
        canonical = (
            json.dumps(
                value,
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
    except (UnicodeError, ValueError) as exc:
        raise EvidenceRegistrationError("pending runtime pair validation is invalid") from exc
    if (
        text != canonical
        or not isinstance(value, dict)
        or frozenset(value) != PAIR_VALIDATION_KEYS
        or value.get("schema_version") != 1
        or value.get("valid") is not True
        or value.get("pair") != pair_name
        or value.get("source_tree") != source_tree
    ):
        raise EvidenceRegistrationError("pending runtime pair validation does not match the source tree")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise EvidenceRegistrationError("registration result contains duplicate keys")
        value[key] = item
    return value


def parse_registration_result(raw: bytes, source_tree: str) -> dict[str, Any]:
    if not raw or len(raw) > MAX_RESULT_BYTES or b"\x00" in raw:
        raise EvidenceRegistrationError("registration result is empty or oversized")
    try:
        text = raw.decode("ascii")
        value = json.loads(text, object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError) as exc:
        raise EvidenceRegistrationError("registration result is not valid JSON") from exc
    canonical = (
        json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    if text != canonical or not isinstance(value, dict) or frozenset(value) != RESULT_KEYS:
        raise EvidenceRegistrationError("registration result is not canonical or exact")
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["source_tree"] != source_tree
        or value["target"] != "an2p-dev"
        or value["status"] != "passed"
        or UUID_PATTERN.fullmatch(str(value["release_id"])) is None
        or UUID_PATTERN.fullmatch(str(value["receipt_id"])) is None
        or SHA256_PATTERN.fullmatch(str(value["release_digest"])) is None
        or SHA256_PATTERN.fullmatch(str(value["receipt_digest"])) is None
        or SHA256_PATTERN.fullmatch(str(value["target_identity"])) is None
        or UTC_PATTERN.fullmatch(str(value["expires_at"])) is None
    ):
        raise EvidenceRegistrationError("registration result does not match the fixed evidence tuple")
    return value


def register(source_tree: str, *, releases: Path = RUNTIME_RELEASES) -> dict[str, Any]:
    runtime, python = _immutable_control_runtime(source_tree, releases=releases)
    _validate_runtime_pair(runtime.parent.name, source_tree)
    command = _registration_command(source_tree, runtime, python)
    try:
        completed = subprocess.run(
            command,
            cwd=runtime,
            env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EvidenceRegistrationError("isolated evidence registrar could not be executed") from exc
    if completed.returncode != 0 or completed.stderr:
        raise EvidenceRegistrationError(
            f"isolated evidence registrar failed with exit {completed.returncode}"
        )
    return parse_registration_result(completed.stdout, source_tree)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if os.geteuid() != 0:
        raise EvidenceRegistrationError("run from a root console on an2p")
    if socket.gethostname().split(".", 1)[0].lower() != "an2p":
        raise EvidenceRegistrationError("registration entrypoint may run only on an2p")
    if len(arguments) != 1:
        raise EvidenceRegistrationError("usage: mooncen-register-container-evidence <40hex-source-tree>")
    source_tree = arguments[0]
    result = register(source_tree)
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
    try:
        raise SystemExit(main())
    except (EvidenceRegistrationError, OSError, ValueError) as exc:
        print(f"container evidence registration entrypoint failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
