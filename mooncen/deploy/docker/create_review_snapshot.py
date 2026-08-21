#!/usr/bin/env python3
"""Create a local-only WIP branch containing the current Docker source inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Iterator, Sequence


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploy.docker.verify_clean_source import (  # noqa: E402
    BUILD_INPUT_PATHS,
    CONTROL_INPUT_PATHS,
    MAX_DISPLAY_PATH_CHARS,
    MAX_REPORTED_PATHS,
    REQUIRED_CONTROL_PATHS,
    SourceVerificationError,
    _validated_paths,
    repository_copy_sources,
)


BRANCH_PATTERN = re.compile(r"\Adocker-dev-snapshot-(20[0-9]{6})\Z")
TEMP_DIRECTORY_PATTERN = re.compile(
    r"\Amooncen-docker-review-index-[0-9a-f]{32}\Z"
)
FIXED_SUBJECT = "WIP: local Docker development review snapshot"
FIXED_BODY = "Local review only. Do not push, merge, deploy, or release."
SECRET_PATTERN = (
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"
    r"|(AKIA|ASIA)[A-Z0-9]{16}"
    r"|gh[pousr]_[A-Za-z0-9]{36,}"
    r"|github_pat_[A-Za-z0-9_]{50,}"
    r"|xox[baprs]-[A-Za-z0-9-]{20,}"
    r"|AIza[0-9A-Za-z_-]{35}"
    r"|sk-(proj-)?[A-Za-z0-9_-]{40,}"
    r"|[rs]k_live_[A-Za-z0-9]{20,}"
)
SECRET_FILENAME_PATTERN = re.compile(SECRET_PATTERN)
GIT_TIMEOUT_SECONDS = 60
MAX_DOCKERFILE_BYTES = 256 * 1024
MAX_COMPOSE_BYTES = 512 * 1024
DOCKERFILE_PATHS = (
    "deploy/docker/api.Dockerfile",
    "deploy/docker/frontend.Dockerfile",
    "deploy/docker/postgres.Dockerfile",
)


class SnapshotError(RuntimeError):
    """Raised when a safe local review snapshot cannot be created."""


@dataclass(frozen=True)
class SnapshotResult:
    branch: str
    commit: str
    base_commit: str
    changed_paths: tuple[str, ...]


@dataclass(frozen=True)
class _HeadIdentity:
    commit: str
    symbolic_ref: str | None


@dataclass(frozen=True)
class _TemporaryIndex:
    root: Path
    directory: Path
    index: Path
    device: int
    inode: int


def _git_environment(index: Path | None = None) -> dict[str, str]:
    environment = os.environ.copy()
    for name in tuple(environment):
        if name.startswith("GIT_CONFIG_"):
            environment.pop(name, None)
    for name in (
        "GIT_INDEX_FILE",
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
        "GIT_NAMESPACE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_GLOB_PATHSPECS",
        "GIT_NOGLOB_PATHSPECS",
        "GIT_ICASE_PATHSPECS",
    ):
        environment.pop(name, None)
    environment["GIT_LITERAL_PATHSPECS"] = "1"
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    if index is not None:
        environment["GIT_INDEX_FILE"] = str(index)
    return environment


def _git(
    root: Path,
    arguments: Sequence[str],
    *,
    index: Path | None = None,
    check: bool = True,
    input_data: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            [
                "git",
                "-c",
                f"core.hooksPath={os.devnull}",
                "-c",
                "core.fsmonitor=false",
                *arguments,
            ],
            cwd=root,
            env=_git_environment(index),
            check=False,
            capture_output=True,
            input=input_data,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError) as exc:
        raise SnapshotError("Git could not run for the review snapshot.") from exc
    if check and result.returncode != 0:
        operation = arguments[0] if arguments else "command"
        raise SnapshotError(f"Git review snapshot failed during {operation}.")
    return result


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError as exc:
        raise SnapshotError("Git returned non-UTF-8 metadata.") from exc


def _decode_paths(raw: bytes) -> tuple[str, ...]:
    return tuple(
        item.decode("utf-8", errors="surrogateescape")
        for item in raw.split(b"\0")
        if item
    )


def _head_identity(root: Path) -> _HeadIdentity:
    commit = _decode(
        _git(root, ("rev-parse", "--verify", "HEAD^{commit}")).stdout
    )
    symbolic = _git(root, ("symbolic-ref", "-q", "HEAD"), check=False)
    if symbolic.returncode == 0:
        symbolic_ref: str | None = _decode(symbolic.stdout)
    elif symbolic.returncode == 1:
        symbolic_ref = None
    else:
        raise SnapshotError("The current HEAD identity could not be read safely.")
    return _HeadIdentity(commit=commit, symbolic_ref=symbolic_ref)


def _assert_repository_root(root: Path) -> tuple[Path, _HeadIdentity]:
    try:
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise SnapshotError("The requested repository root does not exist.") from exc
    if not resolved.is_dir():
        raise SnapshotError("The requested repository root is not a directory.")

    inside = _git(
        resolved,
        ("rev-parse", "--is-inside-work-tree"),
        check=False,
    )
    if inside.returncode != 0 or inside.stdout.strip() != b"true":
        raise SnapshotError("Review snapshots require a Git worktree.")
    top_level = Path(
        _decode(_git(resolved, ("rev-parse", "--show-toplevel")).stdout)
    ).resolve()
    if os.path.normcase(str(top_level)) != os.path.normcase(str(resolved)):
        raise SnapshotError("Run the review snapshot tool at the Git worktree root.")

    return resolved, _head_identity(resolved)


def _validated_branch(branch: str) -> str:
    match = BRANCH_PATTERN.fullmatch(branch)
    if match is None:
        raise SnapshotError(
            "Branch must match docker-dev-snapshot-YYYYMMDD using a 2000s date."
        )
    try:
        datetime.strptime(match.group(1), "%Y%m%d")
    except ValueError as exc:
        raise SnapshotError("The snapshot branch contains an invalid calendar date.") from exc
    return branch


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise SnapshotError("The current Git index could not be hashed safely.") from exc
    return digest.hexdigest()


def _current_index(root: Path) -> Path:
    raw = _decode(_git(root, ("rev-parse", "--git-path", "index")).stdout)
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    if path.is_symlink():
        raise SnapshotError("The current Git index must not be a symbolic link.")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise SnapshotError("The current Git index does not exist.") from exc
    if not resolved.is_file() or resolved.is_symlink():
        raise SnapshotError("The current Git index is not a regular file.")
    return resolved


def _safe_delete_temporary_index(temporary: _TemporaryIndex) -> None:
    try:
        root = temporary.root.resolve(strict=True)
        directory = temporary.directory.resolve(strict=True)
        metadata = temporary.directory.lstat()
    except OSError as exc:
        raise SnapshotError("The temporary index directory could not be verified.") from exc
    if (
        directory.parent != root
        or not TEMP_DIRECTORY_PATTERN.fullmatch(directory.name)
        or temporary.directory.is_symlink()
        or metadata.st_dev != temporary.device
        or metadata.st_ino != temporary.inode
    ):
        raise SnapshotError("Refusing to remove an unexpected temporary path.")
    try:
        shutil.rmtree(directory)
    except OSError as exc:
        raise SnapshotError("The temporary index directory could not be removed.") from exc


@contextmanager
def _temporary_index() -> Iterator[Path]:
    try:
        root = Path(tempfile.gettempdir()).resolve(strict=True)
        directory = root / f"mooncen-docker-review-index-{uuid.uuid4().hex}"
        directory.mkdir(mode=0o700, parents=False, exist_ok=False)
        metadata = directory.lstat()
    except OSError as exc:
        raise SnapshotError("A private temporary index directory could not be created.") from exc
    temporary = _TemporaryIndex(
        root=root,
        directory=directory,
        index=directory / "index",
        device=metadata.st_dev,
        inode=metadata.st_ino,
    )
    try:
        yield temporary.index
    finally:
        _safe_delete_temporary_index(temporary)


def _ensure_ref_absent(root: Path, ref: str) -> None:
    result = _git(root, ("show-ref", "--verify", "--quiet", ref), check=False)
    if result.returncode == 0:
        raise SnapshotError("The requested local review branch already exists.")
    if result.returncode != 1:
        raise SnapshotError("The requested branch could not be checked safely.")


def _unchanged(
    root: Path,
    head_identity: _HeadIdentity,
    index: Path,
    digest: str,
) -> bool:
    return _head_identity(root) == head_identity and _file_digest(index) == digest


def _bounded_filename_report(
    paths: Sequence[str],
    *,
    heading: str,
    instruction: str,
) -> str:
    unique = tuple(sorted(set(paths)))
    lines = [f"{heading}: {len(unique)} file(s)."]
    for path in unique[:MAX_REPORTED_PATHS]:
        displayed = SECRET_FILENAME_PATTERN.sub("<redacted-credential-like>", path)
        if len(displayed) > MAX_DISPLAY_PATH_CHARS:
            displayed = f"{displayed[: MAX_DISPLAY_PATH_CHARS - 3]}..."
        lines.append(f"- {json.dumps(displayed, ensure_ascii=True)}")
    omitted = len(unique) - min(len(unique), MAX_REPORTED_PATHS)
    if omitted:
        lines.append(f"- ... {omitted} additional file(s) omitted")
    lines.append(instruction)
    return "\n".join(lines)


def _credential_scan(root: Path, tree: str, paths: Sequence[str]) -> None:
    result = _git(
        root,
        (
            "grep",
            "-a",
            "-l",
            "-z",
            "-E",
            "-e",
            SECRET_PATTERN,
            tree,
            "--",
            *paths,
        ),
        check=False,
    )
    if result.returncode == 1:
        return
    if result.returncode != 0:
        raise SnapshotError("The credential-like material scan could not complete.")
    identifiers = _decode_paths(result.stdout)
    tree_prefix = f"{tree}:"
    filenames = tuple(
        identifier[len(tree_prefix) :]
        if identifier.startswith(tree_prefix)
        else identifier
        for identifier in identifiers
    )
    raise SnapshotError(
        _bounded_filename_report(
            filenames,
            heading="Credential-like material was detected in the snapshot",
            instruction="Review and remove or rotate the material before retrying.",
        )
    )


def _verify_tree_manifest(
    root: Path,
    tree: str,
    required_paths: Sequence[str],
    build_input_paths: Sequence[str],
    input_kinds: dict[str, str],
) -> None:
    monitored_paths = tuple(dict.fromkeys((*required_paths, *build_input_paths)))
    raw_entries = _git(
        root,
        ("ls-tree", "-r", "-z", tree, "--", *monitored_paths),
    ).stdout
    entries: dict[str, tuple[str, str]] = {}
    for raw_entry in raw_entries.split(b"\0"):
        if not raw_entry:
            continue
        metadata, separator, raw_path = raw_entry.partition(b"\t")
        fields = metadata.split()
        if separator != b"\t" or len(fields) != 3:
            raise SnapshotError("Git returned an invalid snapshot tree entry.")
        try:
            mode = fields[0].decode("ascii", errors="strict")
            object_type = fields[1].decode("ascii", errors="strict")
            path = raw_path.decode("utf-8", errors="surrogateescape")
        except UnicodeDecodeError as exc:
            raise SnapshotError("Git returned an invalid snapshot tree entry.") from exc
        entries[path] = (mode, object_type)

    gitlinks = tuple(
        path for path, (mode, _object_type) in entries.items() if mode == "160000"
    )
    if gitlinks:
        raise SnapshotError(
            _bounded_filename_report(
                gitlinks,
                heading="Embedded Git repositories are not valid Docker source inputs",
                instruction="Replace each gitlink with reviewed, tracked source files.",
            )
        )

    symlinks = tuple(
        path for path, (mode, _object_type) in entries.items() if mode == "120000"
    )
    if symlinks:
        raise SnapshotError(
            _bounded_filename_report(
                symlinks,
                heading="Symbolic links are not valid Docker source inputs",
                instruction="Replace each link with reviewed, tracked source files.",
            )
        )

    tree_paths = set(entries)
    regular_modes = {"100644", "100755"}
    missing = {
        path
        for path in required_paths
        if path not in entries
        or entries[path][0] not in regular_modes
        or entries[path][1] != "blob"
    }
    for path in build_input_paths:
        prefix = f"{path.rstrip('/')}/"
        if input_kinds[path] == "file":
            valid = (
                path in entries
                and entries[path][0] in regular_modes
                and entries[path][1] == "blob"
            )
        else:
            valid = path not in entries and any(
                candidate.startswith(prefix) for candidate in tree_paths
            )
        if not valid:
            missing.add(path)
    if missing:
        raise SnapshotError(
            _bounded_filename_report(
                tuple(missing),
                heading="The snapshot tree is missing required Docker paths",
                instruction="Restore every required control and build-input path.",
            )
        )


def _manifest_worktree_kinds(
    root: Path,
    required_paths: Sequence[str],
    build_input_paths: Sequence[str],
) -> dict[str, str]:
    kinds: dict[str, str] = {}
    for path in tuple(dict.fromkeys((*required_paths, *build_input_paths))):
        candidate = root / path
        try:
            metadata = candidate.lstat()
        except OSError as exc:
            raise SnapshotError(
                _bounded_filename_report(
                    (path,),
                    heading="Required Docker paths are missing from the worktree",
                    instruction="Restore every required path before creating a snapshot.",
                )
            ) from exc
        file_attributes = getattr(metadata, "st_file_attributes", 0)
        is_reparse_point = bool(
            file_attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        )
        if stat.S_ISLNK(metadata.st_mode) or is_reparse_point:
            raise SnapshotError(
                _bounded_filename_report(
                    (path,),
                    heading="Required Docker path roots must not be links",
                    instruction="Use reviewed regular files or real directories.",
                )
            )
        if stat.S_ISREG(metadata.st_mode):
            kind = "file"
        elif stat.S_ISDIR(metadata.st_mode):
            kind = "directory"
        else:
            raise SnapshotError(
                _bounded_filename_report(
                    (path,),
                    heading="Required Docker paths have unsupported filesystem types",
                    instruction="Use reviewed regular files or real directories.",
                )
            )
        if path in required_paths and kind != "file":
            raise SnapshotError(
                _bounded_filename_report(
                    (path,),
                    heading="Docker control paths must be regular files",
                    instruction="Replace the path with a reviewed regular file.",
                )
            )
        kinds[path] = kind
    return {path: kinds[path] for path in build_input_paths}


def _verify_index_capture(
    root: Path,
    alternate_index: Path,
    monitored_paths: Sequence[str],
) -> None:
    ignored = _decode_paths(
        _git(
            root,
            (
                "ls-files",
                "--cached",
                "--ignored",
                "--exclude-standard",
                "-z",
                "--",
                *monitored_paths,
            ),
            index=alternate_index,
        ).stdout
    )
    if ignored:
        raise SnapshotError(
            _bounded_filename_report(
                ignored,
                heading="Refusing Git-ignored paths present in the snapshot index",
                instruction="Remove them from the index or update the source boundary.",
            )
        )

    remaining = _decode_paths(
        _git(
            root,
            (
                "ls-files",
                "--others",
                "--exclude-standard",
                "-z",
                "--",
                *monitored_paths,
            ),
            index=alternate_index,
        ).stdout
    )
    if remaining:
        raise SnapshotError(
            _bounded_filename_report(
                remaining,
                heading="Non-ignored Docker inputs were not staged",
                instruction="Resolve the staging race and retry.",
            )
        )

    worktree_difference = _git(
        root,
        ("diff-files", "--quiet", "--", *monitored_paths),
        index=alternate_index,
        check=False,
    )
    if worktree_difference.returncode == 1:
        raise SnapshotError("Docker inputs changed while the snapshot was staged.")
    if worktree_difference.returncode != 0:
        raise SnapshotError("Docker input stability could not be verified.")


def _reject_external_clean_filters(
    root: Path,
    alternate_index: Path,
    monitored_paths: Sequence[str],
) -> None:
    candidate_paths = _candidate_paths(root, alternate_index, monitored_paths)
    if not candidate_paths:
        return
    candidates = b"".join(
        path.encode("utf-8", errors="surrogateescape") + b"\0"
        for path in candidate_paths
    )
    attributes = _git(
        root,
        ("check-attr", "-z", "--stdin", "filter"),
        index=alternate_index,
        input_data=candidates,
    ).stdout.split(b"\0")
    if attributes and not attributes[-1]:
        attributes.pop()
    if len(attributes) % 3:
        raise SnapshotError("Git returned invalid filter-attribute metadata.")
    filtered_paths: list[str] = []
    for offset in range(0, len(attributes), 3):
        raw_path, attribute, value = attributes[offset : offset + 3]
        if attribute != b"filter":
            raise SnapshotError("Git returned invalid filter-attribute metadata.")
        if value not in {b"unspecified", b"unset"}:
            filtered_paths.append(
                raw_path.decode("utf-8", errors="surrogateescape")
            )
    if filtered_paths:
        raise SnapshotError(
            _bounded_filename_report(
                filtered_paths,
                heading="External Git clean filters are disabled for review snapshots",
                instruction="Remove the filter attribute and review canonical bytes first.",
            )
        )


def _candidate_paths(
    root: Path,
    alternate_index: Path,
    monitored_paths: Sequence[str],
) -> tuple[str, ...]:
    return _decode_paths(
        _git(
            root,
            (
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
                "--",
                *monitored_paths,
            ),
            index=alternate_index,
        ).stdout
    )


def _reject_unsafe_candidate_types(
    root: Path,
    alternate_index: Path,
    monitored_paths: Sequence[str],
) -> None:
    unsafe: set[str] = set()
    for relative_path in _candidate_paths(root, alternate_index, monitored_paths):
        current = root
        unsafe_candidate = False
        for part in PurePosixPath(relative_path).parts:
            current /= part
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                break
            except OSError as exc:
                raise SnapshotError("A Docker input type could not be inspected.") from exc
            file_attributes = getattr(metadata, "st_file_attributes", 0)
            is_reparse_point = bool(
                file_attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            )
            if stat.S_ISLNK(metadata.st_mode) or is_reparse_point:
                unsafe.add(current.relative_to(root).as_posix())
                unsafe_candidate = True
                break
            if not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)):
                unsafe.add(current.relative_to(root).as_posix())
                unsafe_candidate = True
                break
        if not unsafe_candidate and current.is_dir() and (current / ".git").exists():
            unsafe.add(current.relative_to(root).as_posix())
    if unsafe:
        raise SnapshotError(
            _bounded_filename_report(
                tuple(unsafe),
                heading="Docker inputs contain links, special files, or embedded repositories",
                instruction="Replace them with reviewed regular files and directories.",
            )
        )


def _changed_paths(root: Path, base_commit: str, index: Path) -> tuple[str, ...]:
    result = _git(
        root,
        (
            "diff",
            "--cached",
            "--name-only",
            "-z",
            base_commit,
            "--",
        ),
        index=index,
    )
    return _decode_paths(result.stdout)


def _path_is_monitored(path: str, monitored_paths: Sequence[str]) -> bool:
    return any(
        path == monitored or path.startswith(f"{monitored.rstrip('/')}/")
        for monitored in monitored_paths
    )


def _verify_staged_dockerfile_sources(
    root: Path,
    tree: str,
    required_paths: Sequence[str],
    monitored_paths: Sequence[str],
) -> None:
    uncovered: list[str] = []
    for dockerfile_path in DOCKERFILE_PATHS:
        if dockerfile_path not in required_paths:
            continue
        blob = _git(root, ("show", f"{tree}:{dockerfile_path}")).stdout
        if len(blob) > MAX_DOCKERFILE_BYTES:
            raise SnapshotError("A staged Dockerfile is too large to inspect safely.")
        try:
            dockerfile = blob.decode("utf-8", errors="strict")
            sources = repository_copy_sources(dockerfile)
            _validated_paths(sources, label="Dockerfile COPY source")
        except (UnicodeDecodeError, SourceVerificationError) as exc:
            raise SnapshotError("A staged Dockerfile could not be parsed safely.") from exc
        uncovered.extend(
            source
            for source in sources
            if not _path_is_monitored(source, monitored_paths)
        )
    if uncovered:
        raise SnapshotError(
            _bounded_filename_report(
                uncovered,
                heading="Staged Dockerfile COPY sources are outside the source manifest",
                instruction=(
                    "Add each literal COPY source to the reviewed build-input manifest."
                ),
            )
        )


def _verify_staged_compose_builds(
    root: Path,
    tree: str,
    required_paths: Sequence[str],
    monitored_paths: Sequence[str],
) -> None:
    if "compose.yaml" not in required_paths:
        return
    blob = _git(root, ("show", f"{tree}:compose.yaml")).stdout
    if len(blob) > MAX_COMPOSE_BYTES:
        raise SnapshotError("The staged Compose file is too large to inspect safely.")
    try:
        import yaml
    except ImportError as exc:
        raise SnapshotError("PyYAML is required to inspect the staged Compose file.") from exc
    try:
        compose = yaml.safe_load(blob.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, ValueError, yaml.YAMLError) as exc:
        raise SnapshotError(
            "The staged Compose build graph could not be parsed safely."
        ) from exc
    if not isinstance(compose, dict) or not isinstance(compose.get("services"), dict):
        raise SnapshotError("The staged Compose services mapping is invalid.")
    if "include" in compose:
        raise SnapshotError("Compose include files are not allowed in this snapshot.")
    if "configs" in compose or "secrets" in compose:
        raise SnapshotError("Compose file-backed configs and secrets are not allowed.")

    expected_builds = {
        "postgres": "deploy/docker/postgres.Dockerfile",
        "migrate": "deploy/docker/api.Dockerfile",
        "api": "deploy/docker/api.Dockerfile",
        "frontend": "deploy/docker/frontend.Dockerfile",
    }
    services = compose["services"]
    if set(services) != set(expected_builds):
        raise SnapshotError("The staged Compose service set is incomplete or unexpected.")
    declared_volumes = compose.get("volumes", {})
    if not isinstance(declared_volumes, dict):
        raise SnapshotError("The staged Compose volume mapping is invalid.")
    for definition in declared_volumes.values():
        if definition is not None and definition != {}:
            raise SnapshotError("External or driver-backed Compose volumes are not allowed.")

    dockerfiles: set[str] = set()
    for service_name, service in services.items():
        if not isinstance(service_name, str) or not isinstance(service, dict):
            raise SnapshotError("The staged Compose service definition is invalid.")
        if any(
            key in service
            for key in ("extends", "env_file", "configs", "secrets", "develop")
        ):
            raise SnapshotError(
                "Compose services may not load external files or development sync paths."
            )
        service_volumes = service.get("volumes", [])
        if not isinstance(service_volumes, list):
            raise SnapshotError("A staged Compose volume definition is invalid.")
        for volume in service_volumes:
            if isinstance(volume, str):
                source, separator, _target = volume.partition(":")
                if not separator or source not in declared_volumes:
                    raise SnapshotError("Compose host bind mounts are not allowed.")
            elif isinstance(volume, dict):
                named_volume = (
                    volume.get("type") == "volume"
                    and volume.get("source") in declared_volumes
                )
                runtime_config = service_name == "frontend" and volume == {
                    "type": "bind",
                    "source": (
                        "${MOONCEN_RUNTIME_CONFIG_FILE:-"
                        "./frontend2/public/runtime-config.js}"
                    ),
                    "target": "/usr/share/nginx/html/runtime-config.js",
                    "read_only": True,
                    "bind": {"create_host_path": False},
                }
                if not named_volume and not runtime_config:
                    raise SnapshotError("Compose host bind mounts are not allowed.")
            else:
                raise SnapshotError("A staged Compose volume definition is invalid.")
        build = service.get("build")
        if build is None:
            raise SnapshotError("Every staged Compose service must use a reviewed build.")
        if isinstance(build, str):
            context = build
            dockerfile = "Dockerfile"
        elif isinstance(build, dict):
            if any(
                key in build
                for key in ("additional_contexts", "dockerfile_inline", "secrets", "ssh")
            ):
                raise SnapshotError(
                    "Compose additional, inline, or external build inputs are not allowed."
                )
            if not set(build).issubset({"context", "dockerfile", "args"}):
                raise SnapshotError("A staged Compose build option is unsupported.")
            context = build.get("context", ".")
            dockerfile = build.get("dockerfile", "Dockerfile")
        else:
            raise SnapshotError("A staged Compose build definition is invalid.")
        if context != "." or not isinstance(dockerfile, str):
            raise SnapshotError("Every Compose build context must be the repository root.")
        try:
            normalized = _validated_paths(
                (dockerfile,), label="Compose Dockerfile"
            )[0]
        except SourceVerificationError as exc:
            raise SnapshotError("A Compose Dockerfile path is unsafe.") from exc
        if (
            normalized not in DOCKERFILE_PATHS
            or normalized not in required_paths
            or not _path_is_monitored(normalized, monitored_paths)
            or normalized != expected_builds[service_name]
        ):
            raise SnapshotError("A Compose Dockerfile is outside the reviewed manifest.")
        dockerfiles.add(normalized)
    if dockerfiles != set(DOCKERFILE_PATHS):
        raise SnapshotError("The staged Compose build graph is incomplete or unexpected.")


def _create_commit(root: Path, tree: str, base_commit: str) -> str:
    identity = _git(root, ("var", "GIT_AUTHOR_IDENT"), check=False)
    if identity.returncode != 0:
        raise SnapshotError("Configure a Git user name and email before creating a snapshot.")
    result = _git(
        root,
        (
            "-c",
            "commit.gpgSign=false",
            "commit-tree",
            tree,
            "-p",
            base_commit,
            "-m",
            FIXED_SUBJECT,
            "-m",
            FIXED_BODY,
        ),
    )
    commit = _decode(result.stdout)
    if not re.fullmatch(r"[0-9a-f]+", commit):
        raise SnapshotError("Git returned an invalid snapshot commit identifier.")
    return commit


def _rollback_snapshot_ref(root: Path, ref: str, commit: str) -> None:
    try:
        rollback = _git(
            root,
            ("update-ref", "--no-deref", "-d", ref, commit),
            check=False,
        )
    except BaseException as exc:
        raise SnapshotError("Exact snapshot-ref rollback failed.") from exc
    if rollback.returncode != 0:
        raise SnapshotError("Exact snapshot-ref rollback failed.")


def create_review_snapshot(
    root: Path,
    branch: str,
    *,
    required_paths: Sequence[str] = REQUIRED_CONTROL_PATHS,
    control_input_paths: Sequence[str] = CONTROL_INPUT_PATHS,
    build_input_paths: Sequence[str] = BUILD_INPUT_PATHS,
) -> SnapshotResult:
    """Create one local WIP ref without changing the caller's index or worktree."""

    branch = _validated_branch(branch)
    root, head_identity = _assert_repository_root(root)
    base_commit = head_identity.commit
    ref = f"refs/heads/{branch}"
    check_ref = _git(root, ("check-ref-format", "--branch", branch), check=False)
    if check_ref.returncode != 0:
        raise SnapshotError("Git rejected the requested snapshot branch name.")
    _ensure_ref_absent(root, ref)

    try:
        required = _validated_paths(required_paths, label="required-control")
        controls = _validated_paths(control_input_paths, label="control-input")
        inputs = _validated_paths(build_input_paths, label="build-input")
    except SourceVerificationError as exc:
        raise SnapshotError("The Docker source manifest is invalid.") from exc
    provenance_inputs = tuple(dict.fromkeys((*controls, *inputs)))
    monitored_paths = tuple(dict.fromkeys((*required, *provenance_inputs)))
    if not monitored_paths:
        raise SnapshotError("The Docker source manifest is empty.")
    input_kinds = _manifest_worktree_kinds(root, required, provenance_inputs)

    current_index = _current_index(root)
    original_index_digest = _file_digest(current_index)

    with _temporary_index() as alternate_index:
        _git(root, ("read-tree", base_commit), index=alternate_index)
        _reject_unsafe_candidate_types(root, alternate_index, monitored_paths)
        _reject_external_clean_filters(root, alternate_index, monitored_paths)
        _git(root, ("add", "-A", "--", *monitored_paths), index=alternate_index)
        _verify_index_capture(root, alternate_index, monitored_paths)

        diff_check = _git(
            root,
            ("diff", "--cached", "--check", base_commit, "--", *monitored_paths),
            index=alternate_index,
            check=False,
        )
        if diff_check.returncode != 0:
            raise SnapshotError("The Docker snapshot failed git diff --check.")

        changed_paths = _changed_paths(root, base_commit, alternate_index)
        if not changed_paths:
            raise SnapshotError("There are no Docker source changes to snapshot.")
        unexpected_paths = tuple(
            path
            for path in changed_paths
            if not _path_is_monitored(path, monitored_paths)
        )
        if unexpected_paths:
            raise SnapshotError(
                _bounded_filename_report(
                    unexpected_paths,
                    heading="The alternate index changed paths outside the Docker manifest",
                    instruction="Discard the attempted snapshot and inspect Git configuration.",
                )
            )
        tree = _decode(_git(root, ("write-tree",), index=alternate_index).stdout)
        _verify_tree_manifest(
            root,
            tree,
            required,
            provenance_inputs,
            input_kinds,
        )
        _verify_staged_dockerfile_sources(
            root,
            tree,
            required,
            monitored_paths,
        )
        _verify_staged_compose_builds(root, tree, required, monitored_paths)
        _credential_scan(root, tree, monitored_paths)
        _verify_index_capture(root, alternate_index, monitored_paths)
        _reject_unsafe_candidate_types(root, alternate_index, monitored_paths)
        if (
            _manifest_worktree_kinds(root, required, provenance_inputs)
            != input_kinds
        ):
            raise SnapshotError("Docker path types changed while staging the snapshot.")

        if not _unchanged(root, head_identity, current_index, original_index_digest):
            raise SnapshotError("HEAD or the current Git index changed during staging.")

    if not _unchanged(root, head_identity, current_index, original_index_digest):
        raise SnapshotError("HEAD or the current Git index changed before commit creation.")
    commit = _create_commit(root, tree, base_commit)
    if not _unchanged(root, head_identity, current_index, original_index_digest):
        raise SnapshotError("HEAD or the current Git index changed before ref creation.")

    object_format = _decode(
        _git(root, ("rev-parse", "--show-object-format")).stdout
    )
    zero_oid = {"sha1": "0" * 40, "sha256": "0" * 64}.get(object_format)
    if zero_oid is None:
        raise SnapshotError("The repository object format is unsupported.")
    update = _git(
        root,
        ("update-ref", "--no-deref", ref, commit, zero_oid),
        check=False,
    )
    if update.returncode != 0:
        raise SnapshotError("Atomic local snapshot branch creation failed.")
    try:
        post_update_unchanged = _unchanged(
            root,
            head_identity,
            current_index,
            original_index_digest,
        )
    except BaseException as exc:
        try:
            _rollback_snapshot_ref(root, ref, commit)
        except SnapshotError as rollback_exc:
            raise SnapshotError(
                "The post-ref integrity check failed, and exact snapshot-ref "
                "rollback failed."
            ) from rollback_exc
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise SnapshotError(
            "The post-ref integrity check failed; the snapshot ref was rolled back."
        ) from exc
    if not post_update_unchanged:
        try:
            _rollback_snapshot_ref(root, ref, commit)
        except SnapshotError as exc:
            raise SnapshotError(
                "HEAD or the current index changed after ref creation, and exact "
                "snapshot-ref rollback failed."
            ) from exc
        raise SnapshotError(
            "HEAD or the current index changed after ref creation; the snapshot ref "
            "was rolled back."
        )

    return SnapshotResult(
        branch=branch,
        commit=commit,
        base_commit=base_commit,
        changed_paths=changed_paths,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument(
        "--branch",
        required=True,
        help="required local branch name: docker-dev-snapshot-YYYYMMDD",
    )
    arguments = parser.parse_args(argv)
    try:
        result = create_review_snapshot(Path.cwd(), arguments.branch)
    except SnapshotError as exc:
        print(f"Docker review snapshot refused: {exc}", file=sys.stderr)
        return 1
    print(
        f"Created local-only WIP branch {json.dumps(result.branch)} at "
        f"{result.commit} with {len(result.changed_paths)} changed path(s)."
    )
    print("Do not push, merge, deploy, or release this branch before human review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
