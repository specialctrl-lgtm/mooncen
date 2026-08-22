"""Build the reviewed, source-only crawler-control release artifact.

The builder deliberately reads blobs from an exact Git commit instead of from
the working tree.  Its output is deterministic and contains only the explicit
control-host allowlist below.  Runtime state, credentials, virtual
environments, crawler outputs, and local deployment configuration can never be
added by a command-line option.
"""

from __future__ import annotations

import argparse
import ast
import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final, Iterable


FORMAT: Final = "mooncen-crawler-control-tree-v1"
ROLE: Final = "crawler-control"
ARCHIVE_NAME: Final = "crawler-control-release.tar.gz"
MANIFEST_NAME: Final = "crawler-control-release.tree"
METADATA_NAME: Final = "crawler-control-release.env"
ACTIVATOR_NAME: Final = "crawler-control-release-activate.sh"
MAX_FILE_BYTES: Final = 16 * 1024 * 1024
MAX_ARCHIVE_INPUT_BYTES: Final = 64 * 1024 * 1024
EXPECTED_SCHEDULED_PROVIDER_COUNT: Final = 42
EXPECTED_CONCRETE_PROVIDER_COUNT: Final = 434
COMMIT_PATTERN: Final = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
SAFE_PATH_PATTERN: Final = re.compile(r"[A-Za-z0-9_./-]+")

# Exact paths only: do not replace this tuple with directory prefixes.  A
# newly committed file is not deployable on gen1db until it is reviewed and
# deliberately added here.
CONTROL_RELEASE_PATHS: Final = (
    "deploy/ubuntu/requirements-crawler-control.lock",
    "config/culture_center_standard_categories.yaml",
    "config/standard_categories.yaml",
    "config/production_crawler_provider_ownership.json",
    "config/production_crawler_providers.yaml",
    "config/production_topology.json",
    "DB/connection_settings.py",
    "DB/course_lifecycle.py",
    "DB/course_upsert_guards.py",
    "DB/crawl_progress.py",
    "DB/crawler_run_log.py",
    "DB/db_utils.py",
    "DB/roles_body.sql",
    "DB/crawler_control_database_marker.sql",
    "DB/crawler_control_migrations/20260810_001_crawler_control_plane.sql",
    "DB/crawler_control_migrations/20260812_001_install_receipt_consumption.sql",
    "DB/crawler_control_migrations/20260812_002_release_action_requests.sql",
    "DB/crawler_control_migrations/20260812_003_crawler_studio.sql",
    "DB/crawler_control_migrations/20260812_004_rollout_worker_snapshots.sql",
    "DB/crawler_control_migrations/20260812_005_attempt_release_generation.sql",
    "DB/crawler_control_migrations/20260812_006_release_operator_approvals.sql",
    "DB/crawler_control_migrations/20260812_007_quality_environment_isolation.sql",
    "DB/staging_control_plane.sql",
    "DB/staging_schema.sql",
    "ops_agent/__init__.py",
    "ops_agent/crawler_control_db.py",
    "ops_agent/crawler_control_finalizer.py",
    "ops_agent/crawler_control_metrics.py",
    "ops_agent/crawler_control_provider_scope.py",
    "ops_agent/crawler_control_recovery.py",
    "ops_agent/crawler_control_scheduler.py",
    "ops_agent/crawler_release_action_worker.py",
    "ops_agent/crawler_release_agent.py",
    "ops_agent/crawler_release_control.py",
    "ops_agent/crawler_release_publisher.py",
    "ops_agent/production_topology.py",
    "service_group.py",
    "utils/course_semantic_eligibility.py",
    "utils/course_title_quality.py",
    "utils/url_security.py",
    "tools/apply_staging_batch.py",
    "tools/approve_crawler_control_batch.py",
    "tools/approve_crawler_release_action.py",
    "tools/bootstrap_crawler_credential_registry.py",
    "tools/bootstrap_distributed_crawler_release.py",
    "tools/build_crawler_control_release.py",
    "tools/crawler_control_backup_attestation.py",
    "tools/enqueue_crawler_canary.py",
    "tools/ensure_crawler_control_schema.py",
    "tools/manage_crawler_release.py",
    "tools/postgres_scram_verifier.py",
    "tools/preflight_distributed_crawler_control.py",
    "tools/promote_latest_staging_batch.py",
    "tools/provision_crawler_service_login.py",
    "tools/run_pinned_staging_apply.py",
    "tools/run_pinned_staging_dry_run.py",
    "tools/standard_category_mapper.py",
    "tools/validate_staging_activation_result.py",
    "deploy/ubuntu/activate_crawler_control_release.sh",
    "deploy/ubuntu/setup_distributed_crawler_control.sh",
    "deploy/ubuntu/systemd/mooncen-crawler-control-finalizer.service",
    "deploy/ubuntu/systemd/mooncen-crawler-control-metrics.service",
    "deploy/ubuntu/systemd/mooncen-crawler-control-metrics.timer",
    "deploy/ubuntu/systemd/mooncen-crawler-control-scheduler.service",
    "deploy/ubuntu/systemd/mooncen-crawler-release-action-worker.service",
    "deploy/ubuntu/systemd/mooncen-crawler-release-publisher.service",
    "deploy/ubuntu/systemd/mooncen-crawler-release-publisher.timer",
)

FORBIDDEN_COMPONENTS: Final = frozenset(
    {
        ".git",
        ".venv",
        "__pycache__",
        "artifacts",
        "dist",
        "logs",
        "node_modules",
        "private",
        "secrets",
        "state",
        "uploads",
    }
)
FORBIDDEN_NAMES: Final = frozenset(
    {
        ".env",
        ".deploy-info",
        "deploy.local.ps1",
        "deploy_servers.json",
    }
)
FORBIDDEN_SUFFIXES: Final = (
    ".age",
    ".crt",
    ".der",
    ".key",
    ".p12",
    ".pfx",
    ".pem",
    ".secret",
)


class ReleaseBuildError(RuntimeError):
    """A release input failed a closed security contract."""


@dataclass(frozen=True)
class GitEntry:
    mode: str
    kind: str
    object_id: str
    path: str


@dataclass(frozen=True)
class ReleaseFile:
    path: str
    mode: int
    content: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


def _run_git(root: Path, *arguments: str, input_bytes: bytes | None = None) -> bytes:
    process = subprocess.run(
        ["git", "-c", "core.quotepath=false", *arguments],
        cwd=root,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if process.returncode:
        raise ReleaseBuildError(
            f"git {arguments[0] if arguments else 'command'} failed while building the reviewed release"
        )
    return process.stdout


def _validate_path(path: str) -> None:
    if not path or not SAFE_PATH_PATTERN.fullmatch(path) or "\\" in path:
        raise ReleaseBuildError(f"release path is not canonical safe UTF-8: {path!r}")
    pure = PurePosixPath(path)
    if pure.is_absolute() or path != pure.as_posix() or any(part in ("", ".", "..") for part in pure.parts):
        raise ReleaseBuildError(f"release path escapes its root: {path!r}")
    lowered_parts = tuple(part.lower() for part in pure.parts)
    lowered_name = pure.name.lower()
    if (
        any(part in FORBIDDEN_COMPONENTS for part in lowered_parts)
        or lowered_name in FORBIDDEN_NAMES
        or lowered_name.endswith(FORBIDDEN_SUFFIXES)
        or lowered_name.startswith(".env.")
    ):
        raise ReleaseBuildError(f"mutable, local, or secret-bearing release path is forbidden: {path}")


def _parse_tree(raw: bytes) -> dict[str, GitEntry]:
    entries: dict[str, GitEntry] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            header, encoded_path = record.split(b"\t", 1)
            mode, kind, object_id = header.decode("ascii").split(" ", 2)
            path = encoded_path.decode("utf-8", "strict")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ReleaseBuildError("Git tree contains a non-canonical entry") from exc
        _validate_path(path)
        if path in entries:
            raise ReleaseBuildError(f"Git tree contains a duplicate path: {path}")
        entries[path] = GitEntry(mode=mode, kind=kind, object_id=object_id, path=path)
    return entries


def _validate_provider_ownership_contract(root: Path) -> None:
    provider_path = root / "config" / "production_crawler_providers.yaml"
    ownership_path = root / "config" / "production_crawler_provider_ownership.json"
    try:
        provider_bytes = provider_path.read_bytes()
        ownership = json.loads(ownership_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseBuildError("reviewed provider ownership inputs are unreadable") from exc
    if not isinstance(ownership, dict) or ownership.get("format") != "mooncen-crawler-provider-ownership-v1":
        raise ReleaseBuildError("reviewed provider ownership format is invalid")
    if ownership.get("providers_manifest_sha256") != hashlib.sha256(provider_bytes).hexdigest():
        raise ReleaseBuildError("provider ownership is not bound to the reviewed provider manifest")
    expected_scopes = ownership.get("scheduled_providers")
    if not isinstance(expected_scopes, dict):
        raise ReleaseBuildError("provider ownership scopes are invalid")

    # Recompute the ownership snapshot using the clean reviewed crawler runtime.
    # The large crawler runtime is a build-time oracle only; it is intentionally
    # absent from the control-host artifact.
    validation_program = r"""
import json
import pathlib
import sys
import yaml

root = pathlib.Path(sys.argv[1]).resolve(strict=True)
sys.path.insert(0, str(root))
from run_crawlers import build_course_provider_owners

provider_path = root / "config" / "production_crawler_providers.yaml"
document = yaml.safe_load(provider_path.read_text(encoding="utf-8"))
providers = document.get("providers") if isinstance(document, dict) else None
if not isinstance(providers, list):
    raise SystemExit(65)
owners = build_course_provider_owners(providers)
scopes = {provider: [] for provider in providers}
for concrete, owner in owners.items():
    scopes[owner].append(concrete)
print(json.dumps({key: sorted(value) for key, value in scopes.items()}, sort_keys=True, separators=(",", ":")))
"""
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-I", "-c", validation_program, str(root)],
        cwd=root,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=120,
    )
    try:
        actual_scopes = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseBuildError("committed crawler runtime did not emit canonical provider ownership") from exc
    if result.returncode or actual_scopes != expected_scopes:
        raise ReleaseBuildError("committed crawler runtime and reviewed provider ownership differ")
    if len(actual_scopes) != EXPECTED_SCHEDULED_PROVIDER_COUNT:
        raise ReleaseBuildError("reviewed scheduled-provider count changed without a builder contract review")
    flattened = [provider for values in actual_scopes.values() for provider in values]
    if (
        len(flattened) != EXPECTED_CONCRETE_PROVIDER_COUNT
        or len(flattened) != len(set(flattened))
    ):
        raise ReleaseBuildError("reviewed concrete-provider count or ownership uniqueness changed")


def _selected_entries(entries: dict[str, GitEntry]) -> tuple[GitEntry, ...]:
    if len(CONTROL_RELEASE_PATHS) != len(set(CONTROL_RELEASE_PATHS)):
        raise ReleaseBuildError("the compiled control release allowlist contains duplicates")
    selected: list[GitEntry] = []
    for path in CONTROL_RELEASE_PATHS:
        _validate_path(path)
        entry = entries.get(path)
        if entry is None:
            raise ReleaseBuildError(f"reviewed control release path is missing from the commit: {path}")
        # 120000 is a symbolic link and 160000 is a gitlink/submodule.  Other
        # object types, executable modes with special bits, and future Git
        # modes fail closed as well.
        if entry.kind != "blob" or entry.mode not in {"100644", "100755"}:
            raise ReleaseBuildError(
                f"symbolic links, submodules, and special Git modes are forbidden: {path} ({entry.mode})"
            )
        selected.append(entry)
    return tuple(sorted(selected, key=lambda item: item.path.encode("utf-8")))


def _manifest(commit: str, release_files: Iterable[ReleaseFile]) -> bytes:
    files = tuple(release_files)
    lines = [
        f"format={FORMAT}",
        f"commit={commit}",
        f"node_role={ROLE}",
        f"file_count={len(files)}",
        "--files--",
    ]
    for item in files:
        lines.append(f"{item.mode:04o}\t{len(item.content)}\t{item.sha256}\t{item.path}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _validate_static_python_dependencies(
    release_files: Iterable[ReleaseFile],
    repository_paths: set[str],
) -> None:
    selected = {item.path: item for item in release_files}
    selected_paths = set(selected)
    for path, item in selected.items():
        if not path.endswith(".py"):
            continue
        try:
            tree = ast.parse(item.content.decode("utf-8"), filename=path)
        except (UnicodeDecodeError, SyntaxError) as exc:
            raise ReleaseBuildError(f"selected Python source cannot be parsed: {path}") from exc
        package_parts = list(PurePosixPath(path).with_suffix("").parts[:-1])
        module_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                module_names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base_parts: list[str] = []
                if node.level:
                    keep = max(0, len(package_parts) - node.level + 1)
                    base_parts.extend(package_parts[:keep])
                if node.module:
                    base_parts.extend(node.module.split("."))
                if base_parts:
                    module_names.add(".".join(base_parts))
                for alias in node.names:
                    if alias.name != "*":
                        module_names.add(".".join([*base_parts, alias.name]))
            elif isinstance(node, ast.Call) and (
                isinstance(node.func, ast.Name) and node.func.id == "__import__"
                or isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "importlib"
                and node.func.attr == "import_module"
            ):
                raise ReleaseBuildError(f"dynamic Python imports are forbidden in the control release: {path}")
        for module_name in module_names:
            module_path = module_name.replace(".", "/")
            candidates = {f"{module_path}.py", f"{module_path}/__init__.py"}
            local_candidates = candidates & repository_paths
            missing = local_candidates - selected_paths
            if missing:
                raise ReleaseBuildError(
                    f"selected control source has an unshipped local dependency: {path} -> {sorted(missing)[0]}"
                )


def _add_tar_file(archive: tarfile.TarFile, item: ReleaseFile) -> None:
    info = tarfile.TarInfo(item.path)
    info.size = len(item.content)
    info.mode = item.mode
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "mooncen"
    info.mtime = 0
    info.type = tarfile.REGTYPE
    archive.addfile(info, io.BytesIO(item.content))


def _write_exclusive(path: Path, content: bytes, mode: int = 0o600) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def build_release(root: Path, commit: str, output_directory: Path) -> dict[str, object]:
    root = root.resolve(strict=True)
    output_directory = output_directory.resolve(strict=True)
    if not root.is_dir() or not output_directory.is_dir():
        raise ReleaseBuildError("repository root and output directory must be regular directories")
    if output_directory.is_symlink():
        raise ReleaseBuildError("release output directory must not be a symbolic link")
    normalized_commit = commit.strip().lower()
    if not COMMIT_PATTERN.fullmatch(normalized_commit):
        raise ReleaseBuildError("commit must be an exact lowercase Git object identifier")

    head = _run_git(root, "rev-parse", "--verify", "HEAD^{commit}").decode("ascii").strip().lower()
    if head != normalized_commit:
        raise ReleaseBuildError("Git HEAD no longer matches the reviewed control release commit")
    status_output = _run_git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if status_output:
        raise ReleaseBuildError("control release requires a clean Git working tree")
    object_kind = _run_git(root, "cat-file", "-t", normalized_commit).decode("ascii").strip()
    if object_kind != "commit":
        raise ReleaseBuildError("reviewed release object is not a Git commit")
    _validate_provider_ownership_contract(root)
    if _run_git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ReleaseBuildError("working tree changed during committed runtime validation")
    if _run_git(root, "rev-parse", "--verify", "HEAD^{commit}").decode("ascii").strip().lower() != normalized_commit:
        raise ReleaseBuildError("Git HEAD changed during committed runtime validation")

    tree = _parse_tree(_run_git(root, "ls-tree", "-r", "-z", "--full-tree", normalized_commit))
    selected = _selected_entries(tree)
    release_files: list[ReleaseFile] = []
    total_bytes = 0
    for entry in selected:
        content = _run_git(root, "cat-file", "blob", entry.object_id)
        if len(content) > MAX_FILE_BYTES:
            raise ReleaseBuildError(f"control release file exceeds the per-file bound: {entry.path}")
        total_bytes += len(content)
        if total_bytes > MAX_ARCHIVE_INPUT_BYTES:
            raise ReleaseBuildError("control release exceeds the bounded uncompressed input size")
        release_files.append(
            ReleaseFile(path=entry.path, mode=0o755 if entry.mode == "100755" else 0o644, content=content)
        )
    _validate_static_python_dependencies(release_files, set(tree))

    manifest = _manifest(normalized_commit, release_files)
    tree_sha256 = hashlib.sha256(manifest).hexdigest()
    manifest_path = output_directory / MANIFEST_NAME
    archive_path = output_directory / ARCHIVE_NAME
    metadata_path = output_directory / METADATA_NAME
    activator_path = output_directory / ACTIVATOR_NAME
    for target in (manifest_path, archive_path, metadata_path, activator_path):
        if target.exists() or target.is_symlink():
            raise ReleaseBuildError(f"release output already exists: {target.name}")

    _write_exclusive(manifest_path, manifest)
    archive_buffer = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=archive_buffer, mtime=0, compresslevel=9) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.GNU_FORMAT) as archive:
            for item in release_files:
                _add_tar_file(archive, item)
            _add_tar_file(
                archive,
                ReleaseFile(path=".mooncen-control-tree.manifest", mode=0o444, content=manifest),
            )
    archive_bytes = archive_buffer.getvalue()
    archive_sha256 = hashlib.sha256(archive_bytes).hexdigest()
    _write_exclusive(archive_path, archive_bytes)

    metadata = (
        f"FORMAT=mooncen-crawler-control-release-v1\n"
        f"DEPLOY_COMMIT={normalized_commit}\n"
        f"DEPLOY_ARCHIVE_SHA256={archive_sha256}\n"
        f"DEPLOY_TREE_SHA256={tree_sha256}\n"
        f"NODE_ROLE={ROLE}\n"
        "TARGET_HOST=gen1db\n"
    ).encode("ascii")
    _write_exclusive(metadata_path, metadata)
    activator_content = next(
        item.content
        for item in release_files
        if item.path == "deploy/ubuntu/activate_crawler_control_release.sh"
    )
    _write_exclusive(activator_path, activator_content)
    try:
        directory_descriptor = os.open(output_directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        directory_descriptor = -1
    if directory_descriptor >= 0:
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)

    return {
        "format": "mooncen-crawler-control-release-build-v1",
        "commit": normalized_commit,
        "node_role": ROLE,
        "file_count": len(release_files),
        "archive": str(archive_path),
        "archive_sha256": archive_sha256,
        "tree_manifest": str(manifest_path),
        "tree_sha256": tree_sha256,
        "metadata": str(metadata_path),
        "activator": str(activator_path),
        "activator_sha256": hashlib.sha256(activator_content).hexdigest(),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    try:
        result = build_release(arguments.repository_root, arguments.commit, arguments.output_directory)
    except (OSError, ReleaseBuildError) as exc:
        raise SystemExit(f"control release build rejected: {exc}") from exc
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
