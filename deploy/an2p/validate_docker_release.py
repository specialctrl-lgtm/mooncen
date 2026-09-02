#!/usr/bin/env python3
"""Validate the exact development Docker release selected on an2p."""

from __future__ import annotations

import argparse
import grp
import hashlib
import json
import os
import pwd
import re
import socket
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


SOURCE_TREE_PATTERN = re.compile(r"\A[0-9a-f]{40}\Z")
STAGING_TOKEN_PATTERN = re.compile(r"\A[0-9a-f]{32}\Z")
IMAGE_ID_PATTERN = re.compile(r"\Asha256:[0-9a-f]{64}\Z")
RUNTIME_TARGET_PATTERN = re.compile(
    r"\Adocker-release-runtime\.[0-9a-f]{40}\.[0-9a-f]{64}\."
    r"[0-9a-f]{64}\.[A-Za-z0-9]{8}\Z"
)
SYSTEM_RUNTIME_TARGET_PATTERN = re.compile(
    r"\Adocker-runtime\.[0-9a-f]{40}\.[0-9a-f]{64}\.[0-9a-f]{64}\Z"
)
SYSTEM_RUNTIME_ROOT = Path("/opt/mooncen-an2p-docker")
SYSTEM_EVIDENCE_ROOT = SYSTEM_RUNTIME_ROOT / "evidence"
SYSTEM_PAIR_ROOT = Path("/opt/mooncen-an2p-runtime")
SYSTEM_PAIR_PATTERN = re.compile(
    r"\Aruntime-pair\.[0-9a-f]{40}\.[0-9a-f]{40}\.[0-9a-f]{64}\Z"
)
ENVIRONMENT_KEY_PATTERN = re.compile(r"\A[A-Z][A-Z0-9_]*\Z")
MAX_ENVIRONMENT_BYTES = 64 * 1024
MAX_COMPOSE_BYTES = 512 * 1024
MAX_ACTIVATION_BYTES = 64 * 1024
COMMAND_TIMEOUT_SECONDS = 120
ACTIVATION_SCHEMA_VERSION = 1
ACTIVATION_KEYS = frozenset(
    {
        "schema_version",
        "release_digest",
        "receipt_digest",
        "source_tree",
        "target_identity",
        "postgres_image_id",
        "environment_sha256",
    }
)


class ReleaseSelectionError(RuntimeError):
    """Raised when the selected development release cannot be trusted."""


def _canonical_json(value: dict[str, object]) -> bytes:
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
        raise ReleaseSelectionError("Docker activation evidence is invalid") from exc


def _environment_digest(path: Path) -> str:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ReleaseSelectionError("Docker environment file cannot be hashed") from exc
    if len(payload) > MAX_ENVIRONMENT_BYTES:
        raise ReleaseSelectionError("Docker environment path is unsafe")
    return hashlib.sha256(payload).hexdigest()


def _activation_destination(
    project_root: Path,
    path: Path,
    *,
    system_runtime: bool = False,
    reader_gid: int | None = None,
) -> Path:
    try:
        parent = path.parent.resolve(strict=True)
        parent_metadata = path.parent.lstat()
    except OSError as exc:
        raise ReleaseSelectionError("Docker activation directory cannot be read") from exc
    if system_runtime:
        if (
            reader_gid is None
            or parent != project_root
            or path.name != "activation.json"
            or path.is_symlink()
        ):
            raise ReleaseSelectionError("system Docker activation path is unsafe")
        return path
    if (
        parent != project_root
        or path.parent.is_symlink()
        or not stat.S_ISDIR(parent_metadata.st_mode)
        or parent_metadata.st_uid != os.getuid()
        or stat.S_IMODE(parent_metadata.st_mode) != 0o700
        or path.name != "activation.json"
    ):
        raise ReleaseSelectionError("Docker activation path is unsafe")
    return path


def _validate_activation(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or frozenset(value) != ACTIVATION_KEYS:
        raise ReleaseSelectionError("Docker activation evidence fields are invalid")
    if value.get("schema_version") != ACTIVATION_SCHEMA_VERSION:
        raise ReleaseSelectionError("Docker activation evidence version is invalid")
    patterns = {
        "release_digest": re.compile(r"\A[0-9a-f]{64}\Z"),
        "receipt_digest": re.compile(r"\A[0-9a-f]{64}\Z"),
        "source_tree": SOURCE_TREE_PATTERN,
        "target_identity": re.compile(r"\A[0-9a-f]{64}\Z"),
        "postgres_image_id": IMAGE_ID_PATTERN,
        "environment_sha256": re.compile(r"\A[0-9a-f]{64}\Z"),
    }
    normalized: dict[str, object] = {"schema_version": ACTIVATION_SCHEMA_VERSION}
    for field, pattern in patterns.items():
        field_value = value.get(field)
        if type(field_value) is not str or pattern.fullmatch(field_value) is None:
            raise ReleaseSelectionError("Docker activation evidence is invalid")
        normalized[field] = field_value
    return normalized


def _load_activation(
    project_root: Path,
    path: Path,
    *,
    system_runtime: bool = False,
    reader_gid: int | None = None,
) -> dict[str, object]:
    trusted_path = _activation_destination(
        project_root,
        path,
        system_runtime=system_runtime,
        reader_gid=reader_gid,
    )
    try:
        metadata = trusted_path.lstat()
        payload = trusted_path.read_bytes()
    except OSError as exc:
        raise ReleaseSelectionError("Docker activation evidence cannot be read") from exc
    system_metadata_valid = bool(
        system_runtime
        and reader_gid is not None
        and metadata.st_uid == 0
        and metadata.st_gid == reader_gid
        and stat.S_IMODE(metadata.st_mode) == 0o640
    )
    if (
        trusted_path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or (
            not system_metadata_valid
            and (
                metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
            )
        )
        or len(payload) > MAX_ACTIVATION_BYTES
    ):
        raise ReleaseSelectionError("Docker activation evidence path is unsafe")
    try:
        value = json.loads(payload.decode("ascii", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseSelectionError("Docker activation evidence is not valid JSON") from exc
    normalized = _validate_activation(value)
    if payload != _canonical_json(normalized):
        raise ReleaseSelectionError("Docker activation evidence is not canonical")
    return normalized


def _write_activation(
    project_root: Path,
    path: Path,
    value: dict[str, object],
    *,
    system_runtime: bool = False,
    reader_gid: int | None = None,
) -> None:
    trusted_path = _activation_destination(
        project_root,
        path,
        system_runtime=system_runtime,
        reader_gid=reader_gid,
    )
    normalized = _validate_activation(value)
    if trusted_path.exists() or trusted_path.is_symlink():
        raise ReleaseSelectionError("Docker activation evidence already exists")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(trusted_path, flags, 0o640 if system_runtime else 0o600)
        os.fchmod(descriptor, 0o640 if system_runtime else 0o600)
        if system_runtime:
            if reader_gid is None:
                raise ReleaseSelectionError("system activation reader group is missing")
            os.fchown(descriptor, 0, reader_gid)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(_canonical_json(normalized))
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise ReleaseSelectionError("Docker activation evidence could not be written") from exc


def _private_environment(
    path: Path,
    *,
    system_runtime: bool = False,
    reader_gid: int | None = None,
) -> dict[str, str]:
    try:
        directory = path.parent.lstat()
        resolved_directory = path.parent.resolve(strict=True)
        resolved_directory_metadata = resolved_directory.lstat()
        metadata = path.lstat()
        raw = path.read_bytes()
    except OSError as exc:
        raise ReleaseSelectionError("Docker environment file cannot be read") from exc
    system_metadata_valid = bool(
        system_runtime
        and reader_gid is not None
        and resolved_directory.is_dir()
        and resolved_directory_metadata.st_uid == 0
        and resolved_directory_metadata.st_gid == reader_gid
        and stat.S_IMODE(resolved_directory_metadata.st_mode) == 0o750
        and metadata.st_uid == 0
        and metadata.st_gid == reader_gid
        and stat.S_IMODE(metadata.st_mode) == 0o640
    )
    user_directory_valid = bool(
        not path.parent.is_symlink()
        and stat.S_ISDIR(directory.st_mode)
        and directory.st_uid == os.getuid()
        and stat.S_IMODE(directory.st_mode) == 0o700
    )
    user_file_valid = bool(
        metadata.st_uid == os.getuid()
        and stat.S_IMODE(metadata.st_mode) == 0o600
    )
    if (
        not (system_metadata_valid or (user_directory_valid and user_file_valid))
        or path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or len(raw) > MAX_ENVIRONMENT_BYTES
    ):
        raise ReleaseSelectionError("Docker environment path is unsafe")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ReleaseSelectionError("Docker environment is not valid UTF-8") from exc
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if (
            separator != "="
            or ENVIRONMENT_KEY_PATTERN.fullmatch(key) is None
            or key in values
            or any(character in value for character in "\x00\r\n")
        ):
            raise ReleaseSelectionError("Docker environment has an invalid assignment")
        values[key] = value
    return values


def _command(arguments: Sequence[str], *, root: Path) -> str:
    environment = os.environ.copy()
    for name in (
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_TLS",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
        "COMPOSE_FILE",
        "COMPOSE_PROJECT_NAME",
    ):
        environment.pop(name, None)
    environment["DOCKER_HOST"] = "unix:///var/run/docker.sock"
    try:
        result = subprocess.run(
            list(arguments),
            cwd=root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReleaseSelectionError(f"{arguments[0]} could not run") from exc
    if result.returncode != 0:
        raise ReleaseSelectionError(f"{arguments[0]} release verification failed")
    return result.stdout.strip()


def _release_directory(
    value: str,
    *,
    home: Path,
    system_runtime: bool = False,
    reader_gid: int | None = None,
) -> Path:
    if not value or "\x00" in value:
        raise ReleaseSelectionError("MOONCEN_DEV_RELEASE_DIR is missing")
    candidate = Path(value)
    expected_root = (
        SYSTEM_EVIDENCE_ROOT
        if system_runtime
        else home / ".local" / "share" / "mooncen-docker" / "releases"
    )
    try:
        root = expected_root.resolve(strict=True)
        root_metadata = expected_root.lstat()
        resolved = candidate.resolve(strict=True)
        metadata = candidate.lstat()
    except OSError as exc:
        raise ReleaseSelectionError("Development release directory cannot be read") from exc
    system_metadata_valid = bool(
        system_runtime
        and reader_gid is not None
        and root_metadata.st_uid == 0
        and root_metadata.st_gid == reader_gid
        and stat.S_IMODE(root_metadata.st_mode) == 0o750
        and metadata.st_uid == 0
        and metadata.st_gid == reader_gid
        and stat.S_IMODE(metadata.st_mode) == 0o750
    )
    user_metadata_valid = bool(
        root_metadata.st_uid == os.getuid()
        and stat.S_IMODE(root_metadata.st_mode) == 0o700
        and metadata.st_uid == os.getuid()
        and stat.S_IMODE(metadata.st_mode) == 0o700
    )
    if (
        expected_root.is_symlink()
        or not stat.S_ISDIR(root_metadata.st_mode)
        or candidate.is_symlink()
        or resolved.parent != root
        or not stat.S_ISDIR(metadata.st_mode)
        or not (system_metadata_valid or (not system_runtime and user_metadata_valid))
        or SOURCE_TREE_PATTERN.fullmatch(resolved.name) is None
    ):
        raise ReleaseSelectionError("Development release directory is unsafe")
    return resolved


def _staged_release_directory(
    value: str,
    staging_token: str,
    *,
    reader_gid: int,
    trusted_uid: int = 0,
) -> tuple[Path, str]:
    """Resolve one new system evidence stage while binding its final destination."""
    if STAGING_TOKEN_PATTERN.fullmatch(staging_token) is None:
        raise ReleaseSelectionError("Development release staging token is invalid")
    candidate = Path(value)
    source_tree = candidate.name
    stage = SYSTEM_EVIDENCE_ROOT / f".stage.{staging_token}"
    try:
        root = SYSTEM_EVIDENCE_ROOT.resolve(strict=True)
        root_metadata = SYSTEM_EVIDENCE_ROOT.lstat()
        stage_resolved = stage.resolve(strict=True)
        stage_metadata = stage.lstat()
    except OSError as exc:
        raise ReleaseSelectionError("Staged development release cannot be read") from exc
    if (
        SOURCE_TREE_PATTERN.fullmatch(source_tree) is None
        or candidate != SYSTEM_EVIDENCE_ROOT / source_tree
        or candidate.exists()
        or candidate.is_symlink()
        or SYSTEM_EVIDENCE_ROOT.is_symlink()
        or not stat.S_ISDIR(root_metadata.st_mode)
        or root_metadata.st_uid != trusted_uid
        or root_metadata.st_gid != reader_gid
        or stat.S_IMODE(root_metadata.st_mode) != 0o750
        or stage.is_symlink()
        or not stat.S_ISDIR(stage_metadata.st_mode)
        or stage_metadata.st_uid != trusted_uid
        or stage_metadata.st_gid != reader_gid
        or stat.S_IMODE(stage_metadata.st_mode) != 0o750
        or stage_resolved.parent != root
    ):
        raise ReleaseSelectionError("Staged development release directory is unsafe")
    return stage_resolved, source_tree


def _runtime_compose_file(
    project_root: Path,
    selected: Path | None,
    *,
    system_runtime: bool = False,
) -> Path:
    expected = project_root / "compose.yaml"
    candidate = expected if selected is None else selected
    try:
        expected_metadata = expected.lstat()
        candidate_metadata = candidate.lstat()
        expected_bytes = expected.read_bytes()
        candidate_bytes = candidate.read_bytes()
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ReleaseSelectionError("Development Compose policy cannot be read") from exc
    if (
        expected.is_symlink()
        or not stat.S_ISREG(expected_metadata.st_mode)
        or candidate.is_symlink()
        or not stat.S_ISREG(candidate_metadata.st_mode)
        or len(expected_bytes) > MAX_COMPOSE_BYTES
        or len(candidate_bytes) > MAX_COMPOSE_BYTES
    ):
        raise ReleaseSelectionError("Development Compose policy is unsafe")
    if selected is not None and (
        (
            candidate_metadata.st_uid != (0 if system_runtime else os.getuid())
            or stat.S_IMODE(candidate_metadata.st_mode)
            != (0o644 if system_runtime else 0o600)
        )
        or hashlib.sha256(candidate_bytes).digest() != hashlib.sha256(expected_bytes).digest()
    ):
        raise ReleaseSelectionError("Installed development Compose policy differs from the reviewed source")
    return resolved


def _trusted_project_root(
    project_root: Path,
    *,
    system_runtime: bool = False,
    reader_gid: int | None = None,
) -> tuple[Path, bool]:
    try:
        root = project_root.resolve(strict=True)
        metadata = project_root.lstat()
    except OSError as exc:
        raise ReleaseSelectionError("Project root cannot be read") from exc
    if stat.S_ISLNK(metadata.st_mode):
        try:
            target = os.readlink(project_root)
            parent = project_root.parent.resolve(strict=True)
            parent_metadata = project_root.parent.lstat()
            root_metadata = root.lstat()
        except OSError as exc:
            raise ReleaseSelectionError("Installed project pointer cannot be read") from exc
        if system_runtime:
            expected_parent = SYSTEM_RUNTIME_ROOT.resolve(strict=True)
            pair_current = SYSTEM_PAIR_ROOT / "current"
            try:
                pair_pointer_metadata = pair_current.lstat()
                pair_target = os.readlink(pair_current)
                pair = pair_current.parent / pair_target
                pair_metadata = pair.lstat()
                pair_resolved = pair.resolve(strict=True)
            except OSError as exc:
                raise ReleaseSelectionError(
                    "system runtime pair pointer cannot be read"
                ) from exc
            if (
                reader_gid is None
                or project_root != SYSTEM_RUNTIME_ROOT / "current"
                or parent != expected_parent
                or not stat.S_ISDIR(parent_metadata.st_mode)
                or parent_metadata.st_uid != 0
                or parent_metadata.st_gid != 0
                or stat.S_IMODE(parent_metadata.st_mode) != 0o755
                or target != "../mooncen-an2p-runtime/current/docker"
                or not stat.S_ISLNK(pair_pointer_metadata.st_mode)
                or pair_pointer_metadata.st_uid != 0
                or pair_pointer_metadata.st_gid != 0
                or not pair_target.startswith("releases/")
                or SYSTEM_PAIR_PATTERN.fullmatch(
                    pair_target.removeprefix("releases/")
                )
                is None
                or pair.is_symlink()
                or not stat.S_ISDIR(pair_metadata.st_mode)
                or pair_metadata.st_uid != 0
                or pair_metadata.st_gid != 0
                or stat.S_IMODE(pair_metadata.st_mode) != 0o755
                or root != pair_resolved / "docker"
                or root.is_symlink()
                or not stat.S_ISDIR(root_metadata.st_mode)
                or root_metadata.st_uid != 0
                or root_metadata.st_gid != reader_gid
                or stat.S_IMODE(root_metadata.st_mode) != 0o750
            ):
                raise ReleaseSelectionError("system Docker runtime pointer is unsafe")
            return root, True
        if (
            project_root.name != "docker-release-runtime"
            or "/" in target
            or RUNTIME_TARGET_PATTERN.fullmatch(target) is None
            or project_root.parent.is_symlink()
            or not stat.S_ISDIR(parent_metadata.st_mode)
            or parent_metadata.st_uid != os.getuid()
            or stat.S_IMODE(parent_metadata.st_mode) != 0o700
            or root.parent != parent
            or root.is_symlink()
            or not stat.S_ISDIR(root_metadata.st_mode)
            or root_metadata.st_uid != os.getuid()
            or stat.S_IMODE(root_metadata.st_mode) != 0o700
        ):
            raise ReleaseSelectionError("Installed project pointer is unsafe")
        return root, True
    if system_runtime and (
        reader_gid is None
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != reader_gid
        or stat.S_IMODE(metadata.st_mode) != 0o750
        or root.name != "docker"
        or root.parent.parent != (SYSTEM_PAIR_ROOT / "releases").resolve(strict=True)
        or not (
            SYSTEM_PAIR_PATTERN.fullmatch(root.parent.name) is not None
            or re.fullmatch(r"\.stage\.[0-9a-f]{32}", root.parent.name) is not None
        )
    ):
        raise ReleaseSelectionError("system project root is unsafe")
    if not stat.S_ISDIR(metadata.st_mode) or root != project_root:
        raise ReleaseSelectionError("Project root must be a canonical directory")
    return root, False


def _runtime_pointer_child(
    root: Path,
    selected: Path | None,
    name: str,
    *,
    system_runtime: bool = False,
    reader_gid: int | None = None,
) -> Path:
    if selected is None:
        raise ReleaseSelectionError(f"Installed runtime {name} path is missing")
    try:
        metadata = selected.lstat()
        resolved = selected.resolve(strict=True)
    except OSError as exc:
        raise ReleaseSelectionError(f"Installed runtime {name} path cannot be read") from exc
    expected_mode = 0o640 if name in {"development.env", "activation.json"} else 0o644
    system_metadata_valid = bool(
        system_runtime
        and reader_gid is not None
        and metadata.st_uid == 0
        and metadata.st_gid == (reader_gid if expected_mode == 0o640 else 0)
        and stat.S_IMODE(metadata.st_mode) == expected_mode
    )
    if (
        selected.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or resolved != root / name
        or (system_runtime and not system_metadata_valid)
        or (
            not system_runtime
            and (
                metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) & 0o077
            )
        )
    ):
        raise ReleaseSelectionError(f"Installed runtime {name} path is unsafe")
    return resolved


def _running_image_id(
    *,
    root: Path,
    compose_file: Path,
    environment_file: Path,
    service: str,
) -> str:
    container = _command(
        (
            "docker",
            "compose",
            "--project-name",
            "mooncen-dev",
            "--file",
            str(compose_file),
            "--env-file",
            str(environment_file),
            "ps",
            "--quiet",
            service,
        ),
        root=root,
    )
    if not re.fullmatch(r"[0-9a-f]{12,64}", container):
        raise ReleaseSelectionError(f"{service} development container is not running")
    image_id = _command(
        ("docker", "inspect", "--format", "{{.Image}}", container),
        root=root,
    ).lower()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise ReleaseSelectionError(f"{service} container image ID is invalid")
    return image_id


def _development_postgres_image(
    *,
    root: Path,
    tag: str,
    source_tree: str,
) -> tuple[str, str]:
    expected_tag = f"mooncen/postgres:dev-release-{source_tree}"
    if tag != expected_tag:
        raise ReleaseSelectionError("Development PostgreSQL tag is not bound to the release source tree")
    output = _command(
        (
            "docker",
            "image",
            "inspect",
            "--format",
            '{{.Id}}|{{.Os}}/{{.Architecture}}|{{index .Config.Labels "kr.mooncen.source_tree"}}',
            tag,
        ),
        root=root,
    )
    fields = output.split("|")
    if (
        len(fields) != 3
        or IMAGE_ID_PATTERN.fullmatch(fields[0].lower()) is None
        or fields[1] != "linux/amd64"
        or fields[2] != source_tree
    ):
        raise ReleaseSelectionError("Development PostgreSQL image does not match the reviewed source tree")
    return fields[0].lower(), fields[1]


def validate_selected_release(
    *,
    project_root: Path,
    environment_file: Path,
    require_running: bool,
    require_current_receipt: bool = True,
    runtime_compose_file: Path | None = None,
    activation_file: Path | None = None,
    write_activation_file: Path | None = None,
    system_runtime: bool = False,
    reader_group: str = "mooncen_docker_operator",
    staging_token: str | None = None,
) -> dict[str, object]:
    reader_gid: int | None = None
    if system_runtime:
        try:
            reader_gid = grp.getgrnam(reader_group).gr_gid
        except KeyError as exc:
            raise ReleaseSelectionError("system Docker reader group is unavailable") from exc
    root, uses_runtime_pointer = _trusted_project_root(
        project_root,
        system_runtime=system_runtime,
        reader_gid=reader_gid,
    )
    if uses_runtime_pointer:
        environment_file = _runtime_pointer_child(
            root,
            environment_file,
            "development.env",
            system_runtime=system_runtime,
            reader_gid=reader_gid,
        )
        runtime_compose_file = _runtime_pointer_child(
            root,
            runtime_compose_file,
            "compose.yaml",
            system_runtime=system_runtime,
            reader_gid=reader_gid,
        )
        activation_file = _runtime_pointer_child(
            root,
            activation_file,
            "activation.json",
            system_runtime=system_runtime,
            reader_gid=reader_gid,
        )
        if write_activation_file is not None:
            raise ReleaseSelectionError("Installed runtime activation evidence cannot be replaced")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    compose_file = _runtime_compose_file(
        root,
        runtime_compose_file,
        system_runtime=system_runtime,
    )

    from deploy.docker.release_manifest import (  # noqa: PLC0415
        ManifestError,
        bind_promotion_evidence,
        bind_validation_evidence,
        load_json_evidence,
    )
    from deploy.docker.smoke import (  # noqa: PLC0415
        _development_target_identity,
        _normalized_daemon_platform,
    )
    from deploy.docker.verify_release_bundle import (  # noqa: PLC0415
        VerificationError,
        verify_release_directory,
    )

    values = _private_environment(
        environment_file,
        system_runtime=system_runtime,
        reader_gid=reader_gid,
    )
    environment_sha256 = _environment_digest(environment_file)
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    try:
        home = home.resolve(strict=True)
    except OSError as exc:
        raise ReleaseSelectionError("Operator home directory cannot be read") from exc
    configured_release = values.get("MOONCEN_DEV_RELEASE_DIR", "")
    if staging_token is None:
        release_directory = _release_directory(
            configured_release,
            home=home,
            system_runtime=system_runtime,
            reader_gid=reader_gid,
        )
        configured_source_tree = release_directory.name
    else:
        if (
            not system_runtime
            or reader_gid is None
            or uses_runtime_pointer
            or activation_file is not None
            or write_activation_file is None
            or require_running
            or not require_current_receipt
        ):
            raise ReleaseSelectionError(
                "Staged development evidence is only valid for a fresh system preflight"
            )
        release_directory, configured_source_tree = _staged_release_directory(
            configured_release,
            staging_token,
            reader_gid=reader_gid,
        )
    try:
        release = load_json_evidence(release_directory / "release.json")
        receipt = load_json_evidence(
            release_directory / "validation.json",
            receipt=True,
        )
        if require_current_receipt:
            bound = bind_promotion_evidence(
                release,
                receipt,
                now=datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
        else:
            bound = bind_validation_evidence(release, receipt)
        verified = verify_release_directory(release_directory, load_images=False)
    except (ManifestError, VerificationError) as exc:
        raise ReleaseSelectionError(str(exc)) from exc
    trusted_release = bound.release
    trusted_receipt = bound.receipt
    if configured_source_tree != trusted_release["source_tree"]:
        raise ReleaseSelectionError("Release directory is not bound to the source tree")
    if trusted_receipt["target"] != "an2p-dev":
        raise ReleaseSelectionError("Validation receipt target is not an2p-dev")
    if values.get("MOONCEN_API_IMAGE") != trusted_release["images"]["api"]["tag"]:
        raise ReleaseSelectionError("Development API tag does not match the release")
    if values.get("MOONCEN_FRONTEND_IMAGE") != trusted_release["images"]["frontend"]["tag"]:
        raise ReleaseSelectionError("Development frontend tag does not match the release")
    if verified.get("image_ids") != {
        service: trusted_release["images"][service]["image_id"] for service in ("api", "frontend")
    }:
        raise ReleaseSelectionError("Local Docker image IDs do not match the release")

    postgres_image_id, postgres_platform = _development_postgres_image(
        root=root,
        tag=values.get("MOONCEN_POSTGRES_IMAGE", ""),
        source_tree=trusted_release["source_tree"],
    )

    daemon_platform = _command(
        ("docker", "info", "--format", "{{.OSType}}/{{.Architecture}}"),
        root=root,
    )
    normalized_platform = _normalized_daemon_platform(daemon_platform)
    if normalized_platform != trusted_release["platform"]:
        raise ReleaseSelectionError("Development daemon platform does not match the release")
    if postgres_platform != normalized_platform:
        raise ReleaseSelectionError("Development PostgreSQL image platform does not match the daemon")
    target_identity = _development_target_identity(
        hostname=socket.gethostname(),
        platform=daemon_platform,
    )
    if trusted_receipt["target_identity"] != target_identity:
        raise ReleaseSelectionError("Development target identity changed after validation")

    if require_running:
        expected_running = {
            "postgres": postgres_image_id,
            "api": trusted_release["images"]["api"]["image_id"],
            "frontend": trusted_release["images"]["frontend"]["image_id"],
        }
        for service, expected in expected_running.items():
            actual = _running_image_id(
                root=root,
                compose_file=compose_file,
                environment_file=environment_file,
                service=service,
            )
            if actual != expected:
                raise ReleaseSelectionError(f"Running {service} image ID does not match the release")

    evidence: dict[str, object] = {
        "release_digest": trusted_release["release_digest"],
        "receipt_digest": trusted_receipt["receipt_digest"],
        "source_tree": trusted_release["source_tree"],
        "target_identity": target_identity,
        "postgres_image_id": postgres_image_id,
        "environment_sha256": environment_sha256,
        "running_verified": require_running,
    }
    activation = {
        "schema_version": ACTIVATION_SCHEMA_VERSION,
        "release_digest": evidence["release_digest"],
        "receipt_digest": evidence["receipt_digest"],
        "source_tree": evidence["source_tree"],
        "target_identity": evidence["target_identity"],
        "postgres_image_id": evidence["postgres_image_id"],
        "environment_sha256": evidence["environment_sha256"],
    }
    if activation_file is not None:
        installed = _load_activation(
            root,
            activation_file,
            system_runtime=system_runtime,
            reader_gid=reader_gid,
        )
        if installed != activation:
            raise ReleaseSelectionError("Docker activation evidence does not match the selected release")
    if write_activation_file is not None:
        if activation_file is not None or not require_current_receipt or require_running:
            raise ReleaseSelectionError("Docker activation evidence may only be created by a fresh offline preflight")
        _write_activation(
            root,
            write_activation_file,
            activation,
            system_runtime=system_runtime,
            reader_gid=reader_gid,
        )
    return evidence


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--environment-file", type=Path, required=True)
    parser.add_argument("--require-running", action="store_true")
    parser.add_argument("--runtime-compose-file", type=Path)
    parser.add_argument("--activation-file", type=Path)
    parser.add_argument("--write-activation-file", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--system-runtime", action="store_true")
    parser.add_argument("--reader-group", default="mooncen_docker_operator")
    parser.add_argument("--staging-token")
    parser.add_argument(
        "--allow-expired-receipt",
        action="store_true",
        help=(
            "Allow an already activated exact PASS receipt to converge after its "
            "promotion TTL. Never use this for initial activation or promotion."
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        evidence = validate_selected_release(
            project_root=args.project_root,
            environment_file=args.environment_file,
            require_running=args.require_running,
            require_current_receipt=not args.allow_expired_receipt,
            runtime_compose_file=args.runtime_compose_file,
            activation_file=args.activation_file,
            write_activation_file=args.write_activation_file,
            system_runtime=args.system_runtime,
            reader_group=args.reader_group,
            staging_token=args.staging_token,
        )
    except (OSError, ReleaseSelectionError) as exc:
        print(f"Docker release selection failed: {exc}", file=sys.stderr)
        return 78
    if args.json:
        print(json.dumps(evidence, ensure_ascii=True, sort_keys=True))
    else:
        print(f"Validated an2p Docker release {evidence['source_tree']} ({evidence['release_digest']}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
