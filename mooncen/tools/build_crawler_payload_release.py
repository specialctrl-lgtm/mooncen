"""Build a deterministic, commit-only crawler PAYLOAD release.

The builder reads raw objects from one standalone repository-local Git object
database.  It never reads the index, worktree files, Git attributes, smudge
filters, submodules, alternates, replace refs, grafts, promisor remotes, or lazy
object fetches.  The command accepts no repository or output path: those are
fixed by the isolated builder service.  The only CLI identity is a canonical,
database-issued ticket filename below the fixed inbox root.

Ticket issuance remains unavailable until an independent Studio source
approval contract exists.  Even a successful local build is deliberately
``registration_ready=false`` until source-approval verification, exact worker
manifest enforcement, and an isolated signer handoff are integrated.
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
import stat
import subprocess
import sys
import tarfile
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final, Iterable, Mapping, Sequence

from ops_agent.crawler_builder_evidence import (
    BLOCKED_REGISTRATION_REASONS,
    BUILDER_EVIDENCE_FORMAT,
    BuilderEvidence,
    BuilderEvidenceError,
    BuilderTicket,
    canonical_json_bytes,
    load_ticket_json,
)


FORMAT: Final = "mooncen-crawler-payload-tree-v1"
RELEASE_MANIFEST_NAME: Final = "crawler-release.json"
CONTENT_MANIFEST_NAME: Final = ".mooncen-crawler-payload-tree.json"
ARCHIVE_NAME: Final = "crawler-payload-release.tar.gz"
EVIDENCE_NAME: Final = "crawler-payload-builder-evidence.json"

# These paths are fixed by the isolated builder service; no caller-controlled
# filesystem path is accepted by the CLI.
REPOSITORY_ROOT: Final = Path("/srv/mooncen-crawler-builder/repository")
TICKET_INBOX_ROOT: Final = Path("/var/lib/mooncen-crawler-builder/tickets")
OUTPUT_ROOT: Final = Path("/var/lib/mooncen-crawler-builder/output")

MAX_FILE_BYTES: Final = 32 * 1024 * 1024
MAX_INPUT_BYTES: Final = 512 * 1024 * 1024
MAX_ARCHIVE_BYTES: Final = 512 * 1024 * 1024
MAX_FILES: Final = 30_000
MAX_TICKET_BYTES: Final = 256 * 1024
MAX_GIT_OUTPUT_BYTES: Final = 1024 * 1024 * 1024

COMMIT_PATTERN: Final = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
TICKET_NAME_PATTERN: Final = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}[.]json"
)
SAFE_PATH_PATTERN: Final = re.compile(r"[A-Za-z0-9_./-]+")

# Crawler source is one reviewed code surface.  Prefix selection is safe here
# only because every selected path must also match an extension allowlist and
# the committed path-set digest below.  Adding/removing a crawler source file
# requires an explicit builder contract review and digest update.
CRAWLER_PREFIX: Final = "Crawler/"
CRAWLER_SUFFIXES: Final = (".py", ".yaml")
EXPECTED_CRAWLER_PATH_COUNT: Final = 412
EXPECTED_CRAWLER_PATHS_SHA256: Final = (
    "0dce9bc6a8c3f7f522b464ca178f73e331d0e6349420511d6445120f74411001"
)

# These six inputs are opened by the normal COLLECTED_YAML scheduled path.
# Missing files are otherwise silently skipped by Crawler_MunicipalYaml, so
# presence and module binding are both exercised again from the built archive.
RUNTIME_REQUIRED_DATA_BINDINGS: Final = (
    ("MUNICIPAL_CANDIDATES", "config/municipal_course_candidate_results.yaml"),
    (
        "MUNICIPAL_SUGANG_SPORTS_CANDIDATES",
        "config/municipal_course_candidate_results_sugang_sports.yaml",
    ),
    ("MUNICIPAL_SEARCH_TARGETS", "config/municipal_course_search_targets.yaml"),
    ("NATIONAL_KEYWORD_TARGETS", "config/national_keyword_course_search_targets.yaml"),
    (
        "NATIONAL_INSTITUTION_TARGETS",
        "config/national_institution_course_search_targets.yaml",
    ),
    ("MUSEUM_TARGETS", "config/museum_course_search_targets.yaml"),
)

# Importing these roots reaches the scheduled runner, YAML collector, and the
# worker's supported parser-probe agent_command lazy imports.  The subprocess
# runs against a fresh materialization of the completed tar.gz, not the source
# worktree.
RUNTIME_IMPORT_SMOKE: Final = (
    "run_crawlers",
    "Crawler.Crawler_YamlSources",
    "Crawler.Crawler_MunicipalYaml",
    "ops_agent.crawler_worker",
    "backend.ops.service",
    "tools.parser_probe",
)
RUNTIME_DATA_SMOKE_MODULE: Final = "Crawler.Crawler_MunicipalYaml"

# Exact non-crawler runtime closure used by `/opt/mooncen-crawler/current`.
# Files outside this set cannot enter the payload through a caller option.
RUNTIME_EXACT_PATHS: Final = (
    "data_parser.py",
    "description_cleaner.py",
    "run_crawlers.py",
    "service_group.py",
    "target_category_fallback.py",
    "target_cleaner.py",
    "title_cleaner.py",
    "utils.py",
    "DB/connection_settings.py",
    "DB/course_lifecycle.py",
    "DB/course_upsert_guards.py",
    "DB/crawl_progress.py",
    "DB/crawler_run_log.py",
    "DB/db_utils.py",
    "ops_agent/__init__.py",
    "ops_agent/crawler_outcome.py",
    "ops_agent/crawler_registry.py",
    "ops_agent/crawler_worker.py",
    "backend/ops/__init__.py",
    "backend/ops/service.py",
    "tools/crawler_report.py",
    "tools/ops_redaction.py",
    "tools/parser_probe.py",
    "tools/sample_collect_from_yaml.py",
    "tools/standard_category_mapper.py",
    "utils/__init__.py",
    "utils/course_semantic_eligibility.py",
    "utils/course_title_quality.py",
    "utils/fee_semantics.py",
    "utils/generic_course_eligibility.py",
    "utils/outbound_http.py",
    "utils/seo_quality.py",
    "utils/source_endpoint.py",
    "utils/text_quality.py",
    "utils/url_security.py",
    "config/collected_yaml_crawl_targets.yaml",
    "config/crawl_targets/arboretum_ecology.yaml",
    "config/crawl_targets/arts_culture.yaml",
    "config/crawl_targets/deprecated.yaml",
    "config/crawl_targets/generated_review.yaml",
    "config/crawl_targets/index.yaml",
    "config/crawl_targets/library.yaml",
    "config/crawl_targets/lifelong_learning.yaml",
    "config/crawl_targets/municipal_integrated_reservation.yaml",
    "config/crawl_targets/museum_science.yaml",
    "config/crawl_targets/public_reservation.yaml",
    "config/crawl_targets/retail_culture.yaml",
    "config/crawl_targets/sports_facility.yaml",
    "config/crawl_targets/welfare.yaml",
    "config/crawl_targets/youth.yaml",
    "config/culture_center_standard_categories.yaml",
    "config/facility_registry_crawl_targets.yaml",
    "config/generated_yaml_crawler_registry.yaml",
    "config/http_crawl_target_exceptions.yaml",
    "config/municipal_course_candidate_results.yaml",
    "config/municipal_course_candidate_results_sugang_sports.yaml",
    "config/municipal_course_search_targets.yaml",
    "config/municipal_integrated_reservation_operational.yaml",
    "config/museum_course_search_targets.yaml",
    "config/national_institution_course_search_targets.yaml",
    "config/national_keyword_course_search_targets.yaml",
    "config/production_crawler_providers.yaml",
    "config/public_course_targets.yaml",
    "config/standard_categories.yaml",
    "config/welfare_course_targets.yaml",
)

FORBIDDEN_COMPONENTS: Final = frozenset(
    {
        ".git",
        ".venv",
        "__pycache__",
        "artifacts",
        "credentials",
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
    {".env", ".deploy-info", "deploy.local.ps1", "deploy_servers.json"}
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
FORBIDDEN_GIT_ENVIRONMENT: Final = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_CONFIG",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_DIR",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_OPTIONAL_LOCKS",
        "GIT_REPLACE_REF_BASE",
        "GIT_SHALLOW_FILE",
        "GIT_WORK_TREE",
    }
)


class PayloadBuildError(RuntimeError):
    """The reviewed payload cannot be built under the closed contract."""


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
    object_id: str
    content: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


def _git_environment() -> dict[str, str]:
    leaked = sorted(name for name in os.environ if name.upper() in FORBIDDEN_GIT_ENVIRONMENT)
    if leaked:
        raise PayloadBuildError("builder process contains a forbidden Git environment override")
    path = os.environ.get("PATH", "")
    system_root = os.environ.get("SystemRoot", "")
    environment = {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "HOME": "/nonexistent",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": path,
    }
    if system_root:
        environment["SystemRoot"] = system_root
    return environment


def _run_git(root: Path, *arguments: str, maximum: int = MAX_GIT_OUTPUT_BYTES) -> bytes:
    if any(not isinstance(argument, str) or "\x00" in argument for argument in arguments):
        raise PayloadBuildError("Git argument is invalid")
    command = [
        "git",
        "--no-pager",
        "-c",
        "core.quotepath=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "filter.lfs.required=false",
        *arguments,
    ]
    try:
        process = subprocess.run(
            command,
            cwd=root,
            env=_git_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PayloadBuildError("fixed Git object read could not run") from exc
    if process.returncode or len(process.stdout) > maximum:
        raise PayloadBuildError("fixed Git object read failed or exceeded its bound")
    return process.stdout


def _regular_directory(path: Path, *, label: str) -> Path:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise PayloadBuildError(f"{label} is unavailable") from exc
    if path.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
        raise PayloadBuildError(f"{label} must be a non-symlink directory")
    return path


def _assert_tree_has_no_links(path: Path, *, label: str) -> None:
    for current, directory_names, file_names in os.walk(path, followlinks=False):
        current_path = Path(current)
        for name in (*directory_names, *file_names):
            child = current_path / name
            try:
                metadata = child.lstat()
            except OSError as exc:
                raise PayloadBuildError(f"{label} changed during inspection") from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise PayloadBuildError(f"{label} contains a symbolic link")


def _git_config_pairs(root: Path) -> list[tuple[str, str]]:
    # Read the one repository-local config file without following include.path
    # or includeIf directives into caller/environment-controlled files.
    raw = _run_git(
        root,
        "config",
        "--file",
        ".git/config",
        "--no-includes",
        "--null",
        "--list",
        maximum=1024 * 1024,
    )
    fields = raw.rstrip(b"\0").split(b"\0") if raw else []
    pairs: list[tuple[str, str]] = []
    for field in fields:
        try:
            key, value = field.decode("utf-8", "strict").split("\n", 1)
        except (UnicodeDecodeError, ValueError) as exc:
            raise PayloadBuildError("repository config is not canonical UTF-8") from exc
        pairs.append((key.lower(), value))
    return pairs


def _validate_repository(root: Path) -> Path:
    resolved = root.resolve(strict=True)
    if resolved != root or not root.is_absolute():
        raise PayloadBuildError("repository root is not the fixed canonical path")
    _regular_directory(root, label="repository root")
    git_directory = root / ".git"
    _regular_directory(git_directory, label="repository-local Git directory")
    if (git_directory / "commondir").exists() or (git_directory / "gitdir").exists():
        raise PayloadBuildError("linked Git worktrees are forbidden")
    objects = _regular_directory(git_directory / "objects", label="repository-local object database")
    _assert_tree_has_no_links(objects, label="repository-local object database")
    for forbidden in (
        objects / "info" / "alternates",
        git_directory / "info" / "grafts",
        git_directory / "shallow",
    ):
        if forbidden.exists() or forbidden.is_symlink():
            raise PayloadBuildError("repository uses forbidden alternate, graft, or shallow state")
    replace_directory = git_directory / "refs" / "replace"
    if replace_directory.exists() or replace_directory.is_symlink():
        raise PayloadBuildError("repository replace refs are forbidden")
    if any(objects.rglob("*.promisor")):
        raise PayloadBuildError("repository promisor object packs are forbidden")
    for key, value in _git_config_pairs(root):
        if (
            key.startswith("remote.") and key.endswith(".promisor")
            or key.startswith("remote.") and key.endswith(".partialclonefilter")
            or key.startswith("filter.")
            or key == "extensions.partialclone"
        ):
            raise PayloadBuildError("repository config enables a forbidden object source or format")
        if key == "extensions.objectformat" and value not in {"sha1", "sha256"}:
            raise PayloadBuildError("repository object format is unsupported")
    if _run_git(root, "rev-parse", "--is-bare-repository").strip() != b"false":
        raise PayloadBuildError("builder repository must be a standalone non-bare repository")
    if _run_git(root, "rev-parse", "--git-common-dir").strip().replace(b"\\", b"/") != b".git":
        raise PayloadBuildError("repository common directory is not local")
    if _run_git(root, "rev-parse", "--git-path", "objects").strip().replace(b"\\", b"/") != b".git/objects":
        raise PayloadBuildError("repository object database is not local")
    if _run_git(
        root,
        "for-each-ref",
        "--format=%(refname)",
        "refs/replace",
        maximum=1024,
    ):
        raise PayloadBuildError("repository packed replace refs are forbidden")
    return git_directory


def _validate_path(path: str, *, selected: bool) -> None:
    if not path or "\x00" in path or "\\" in path or len(path.encode("utf-8")) > 512:
        raise PayloadBuildError("Git tree contains an unsafe path")
    try:
        encoded = path.encode("utf-8", "strict")
    except UnicodeError as exc:
        raise PayloadBuildError("Git tree path is not UTF-8") from exc
    pure = PurePosixPath(path)
    if (
        pure.is_absolute()
        or path != pure.as_posix()
        or unicodedata.normalize("NFC", path) != path
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise PayloadBuildError("Git tree contains a non-canonical path")
    if selected:
        if SAFE_PATH_PATTERN.fullmatch(path) is None or encoded.decode("ascii", "strict") != path:
            raise PayloadBuildError("selected payload path must be canonical ASCII")
        lowered_parts = tuple(part.casefold() for part in pure.parts)
        lowered_name = pure.name.casefold()
        if (
            any(part in FORBIDDEN_COMPONENTS for part in lowered_parts)
            or lowered_name in FORBIDDEN_NAMES
            or lowered_name.startswith(".env.")
            or lowered_name.endswith(FORBIDDEN_SUFFIXES)
        ):
            raise PayloadBuildError("selected payload path is secret-bearing or mutable")


def _parse_tree(raw: bytes, *, oid_length: int) -> dict[str, GitEntry]:
    entries: dict[str, GitEntry] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            header, encoded_path = record.split(b"\t", 1)
            mode, kind, object_id = header.decode("ascii").split(" ", 2)
            path = encoded_path.decode("utf-8", "strict")
        except (UnicodeDecodeError, ValueError) as exc:
            raise PayloadBuildError("Git tree entry is not canonical") from exc
        _validate_path(path, selected=False)
        if object_id != object_id.lower() or len(object_id) != oid_length or not re.fullmatch(r"[0-9a-f]+", object_id):
            raise PayloadBuildError("Git tree object id is invalid")
        if path in entries:
            raise PayloadBuildError("Git tree contains a duplicate path")
        entries[path] = GitEntry(mode=mode, kind=kind, object_id=object_id, path=path)
    return entries


def _git_object_id(kind: str, content: bytes, *, algorithm: str) -> str:
    if kind not in {"blob", "commit", "tree"}:
        raise PayloadBuildError("Git object kind is unsupported")
    header = f"{kind} {len(content)}\0".encode("ascii")
    try:
        digest = hashlib.new(algorithm)
    except ValueError as exc:  # pragma: no cover - Python always supports sha1/sha256.
        raise PayloadBuildError("Git object hash algorithm is unavailable") from exc
    digest.update(header)
    digest.update(content)
    return digest.hexdigest()


def _read_and_verify_object(
    root: Path,
    object_id: str,
    kind: str,
    *,
    algorithm: str,
    maximum: int,
) -> bytes:
    observed_kind = _run_git(root, "cat-file", "-t", object_id, maximum=32).decode("ascii").strip()
    if observed_kind != kind:
        raise PayloadBuildError("Git object kind differs from reviewed identity")
    content = _run_git(root, "cat-file", kind, object_id, maximum=maximum)
    if _git_object_id(kind, content, algorithm=algorithm) != object_id:
        raise PayloadBuildError("raw Git object bytes differ from their object id")
    return content


def _selected_entries(entries: Mapping[str, GitEntry]) -> tuple[GitEntry, ...]:
    for entry in entries.values():
        if not entry.path.startswith(CRAWLER_PREFIX):
            continue
        lowered_name = PurePosixPath(entry.path).name.casefold()
        if entry.kind != "blob" or entry.mode not in {"100644", "100755"}:
            raise PayloadBuildError("crawler runtime contains a symlink, gitlink, or special mode")
        if lowered_name.endswith(FORBIDDEN_SUFFIXES) or lowered_name in FORBIDDEN_NAMES:
            raise PayloadBuildError("crawler runtime contains a secret-bearing path")
    crawler_paths = sorted(
        (
            path
            for path in entries
            if path.startswith(CRAWLER_PREFIX)
            and path.endswith(CRAWLER_SUFFIXES)
            and not PurePosixPath(path).name.startswith(".")
        ),
        key=lambda value: value.encode("utf-8"),
    )
    crawler_digest = hashlib.sha256(("\n".join(crawler_paths) + "\n").encode("utf-8")).hexdigest()
    if (
        len(crawler_paths) != EXPECTED_CRAWLER_PATH_COUNT
        or crawler_digest != EXPECTED_CRAWLER_PATHS_SHA256
    ):
        raise PayloadBuildError("committed crawler runtime path set differs from its reviewed allowlist")
    selected_paths = [*RUNTIME_EXACT_PATHS, *crawler_paths]
    if len(selected_paths) != len(set(selected_paths)):
        raise PayloadBuildError("compiled payload allowlist contains duplicate paths")
    selected: list[GitEntry] = []
    casefold_paths: dict[str, str] = {}
    for path in sorted(selected_paths, key=lambda value: value.encode("ascii")):
        _validate_path(path, selected=True)
        collision = casefold_paths.setdefault(path.casefold(), path)
        if collision != path:
            raise PayloadBuildError("payload paths collide on a case-insensitive filesystem")
        entry = entries.get(path)
        if entry is None:
            raise PayloadBuildError(f"reviewed payload path is missing: {path}")
        if entry.kind != "blob" or entry.mode not in {"100644", "100755"}:
            raise PayloadBuildError("symlinks, gitlinks, and special Git modes are forbidden")
        selected.append(entry)
    return tuple(selected)


def _runtime_allowlist_sha256(paths: Iterable[str]) -> str:
    ordered = sorted(paths, key=lambda value: value.encode("ascii"))
    return hashlib.sha256(("\n".join(ordered) + "\n").encode("ascii")).hexdigest()


def _source_revisions_match(ticket: BuilderTicket, files: Mapping[str, ReleaseFile]) -> None:
    for source in ticket.sources:
        item = files.get(source.source_path)
        if item is None:
            raise PayloadBuildError("approved Studio source is outside the payload allowlist")
        if item.sha256 != source.source_sha256:
            raise PayloadBuildError("approved Studio source digest differs from the committed blob")


def _module_tree_paths(module: str) -> tuple[str, str]:
    stem = module.replace(".", "/")
    return f"{stem}.py", f"{stem}/__init__.py"


def _resolved_import_modules(item: ReleaseFile, node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    if not isinstance(node, ast.ImportFrom):
        return ()
    module = node.module or ""
    if node.level:
        path_parts = item.path.removesuffix(".py").split("/")
        package_parts = path_parts if path_parts[-1] == "__init__" else path_parts[:-1]
        if package_parts and package_parts[-1] == "__init__":
            package_parts = package_parts[:-1]
        parent_hops = node.level - 1
        if parent_hops > len(package_parts):
            raise PayloadBuildError("payload Python source has an invalid relative import")
        package_parts = package_parts[: len(package_parts) - parent_hops]
        if module:
            package_parts.extend(module.split("."))
        module = ".".join(package_parts)
    candidates = [module] if module else []
    for alias in node.names:
        if alias.name != "*":
            candidates.append(f"{module}.{alias.name}" if module else alias.name)
    return tuple(candidates)


def _validate_first_party_import_closure(
    files: Mapping[str, ReleaseFile],
    entries: Mapping[str, GitEntry],
) -> None:
    """Reject an archive that omits any importable module from the same tree."""

    selected_paths = set(files)
    tree_paths = set(entries)
    for item in files.values():
        if not item.path.endswith(".py"):
            continue
        try:
            syntax = compile(
                item.content,
                item.path,
                "exec",
                flags=ast.PyCF_ONLY_AST,
                dont_inherit=True,
            )
        except (SyntaxError, UnicodeError, ValueError) as exc:
            raise PayloadBuildError("payload Python source is not parseable") from exc
        for node in ast.walk(syntax):
            for module in _resolved_import_modules(item, node):
                if not module or not all(part.isidentifier() for part in module.split(".")):
                    continue
                matches = set(_module_tree_paths(module)) & tree_paths
                if matches and not matches <= selected_paths:
                    raise PayloadBuildError(
                        "payload omits a first-party Python import from its source tree"
                    )


def _materialize_archive_for_smoke(
    archive_bytes: bytes,
    root: Path,
    expected_files: Sequence[ReleaseFile],
) -> None:
    expected = {item.path: item for item in expected_files}
    try:
        archive = tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz")
    except (OSError, tarfile.TarError) as exc:  # pragma: no cover - built immediately above.
        raise PayloadBuildError("completed payload archive cannot be reopened") from exc
    with archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        if names != sorted(expected, key=lambda value: value.encode("ascii")):
            raise PayloadBuildError("completed payload archive member set differs")
        for member in members:
            item = expected.get(member.name)
            if (
                item is None
                or not member.isfile()
                or member.size != len(item.content)
                or member.mode != item.mode
                or member.uid != 0
                or member.gid != 0
                or member.mtime != 0
            ):
                raise PayloadBuildError("completed payload archive metadata differs")
            handle = archive.extractfile(member)
            if handle is None:
                raise PayloadBuildError("completed payload archive member is unreadable")
            content = handle.read(len(item.content) + 1)
            if content != item.content:
                raise PayloadBuildError("completed payload archive member bytes differ")
            relative = PurePosixPath(member.name)
            destination = root.joinpath(*relative.parts)
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, item.mode)
            try:
                with os.fdopen(descriptor, "wb", closefd=False) as output:
                    output.write(content)
            finally:
                os.close(descriptor)


def _runtime_smoke_environment() -> dict[str, str]:
    environment = {
        "HOME": "/nonexistent",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": os.environ.get("PATH", ""),
    }
    system_root = os.environ.get("SystemRoot", "")
    if system_root:
        environment["SystemRoot"] = system_root
    return environment


def _runtime_smoke_program() -> str:
    contract = json.dumps(
        {
            "modules": list(RUNTIME_IMPORT_SMOKE),
            "data_module": RUNTIME_DATA_SMOKE_MODULE,
            "data_bindings": [list(item) for item in RUNTIME_REQUIRED_DATA_BINDINGS],
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"""
import importlib
import json
import os
import pathlib
import sys

contract = json.loads({contract!r})
root = pathlib.Path.cwd().resolve(strict=True)
interpreter_roots = {{
    pathlib.Path(sys.base_prefix).resolve(strict=True),
    pathlib.Path(sys.prefix).resolve(strict=True),
}}
safe_sys_path = []
for value in sys.path:
    if not value:
        continue
    try:
        candidate = pathlib.Path(value).resolve(strict=True)
    except OSError:
        continue
    if any(base == candidate or base in candidate.parents for base in interpreter_roots):
        safe_sys_path.append(str(candidate))
sys.path[:] = [str(root), *safe_sys_path]
loaded = {{}}
for name in contract["modules"]:
    module = importlib.import_module(name)
    module_path = pathlib.Path(module.__file__).resolve(strict=True)
    if root != module_path and root not in module_path.parents:
        raise RuntimeError("runtime smoke imported a module outside the payload")
    loaded[name] = module
if contract["data_bindings"]:
    data_module = loaded.get(contract["data_module"])
    if data_module is None:
        data_module = importlib.import_module(contract["data_module"])
    for attribute, relative in contract["data_bindings"]:
        expected = root.joinpath(*relative.split("/")).resolve(strict=True)
        observed = pathlib.Path(getattr(data_module, attribute)).resolve(strict=True)
        if observed != expected or not observed.is_file() or observed.stat().st_size <= 0:
            raise RuntimeError("required scheduled crawler data is unavailable")
        with observed.open("rb") as handle:
            if not handle.read(1):
                raise RuntimeError("required scheduled crawler data is empty")
    targets = data_module.load_targets("collected")
    if not isinstance(targets, list) or not targets:
        raise RuntimeError("COLLECTED_YAML target inventory is empty")
    identities = []
    for target in targets:
        identity = tuple(str(getattr(target, field, "")).strip() for field in ("provider", "url", "source"))
        if not all(identity):
            raise RuntimeError("COLLECTED_YAML target inventory has an incomplete identity")
        identities.append(identity)
    if len(identities) != len(set(identities)):
        raise RuntimeError("COLLECTED_YAML target inventory has duplicate identities")
"""


def _validate_archive_runtime(
    archive_bytes: bytes,
    output: Path,
    archive_files: Sequence[ReleaseFile],
) -> None:
    with tempfile.TemporaryDirectory(prefix=".runtime-smoke-", dir=output) as temporary:
        root = Path(temporary).resolve(strict=True)
        _materialize_archive_for_smoke(archive_bytes, root, archive_files)
        command = [sys.executable, "-I", "-B", "-c", _runtime_smoke_program()]
        try:
            process = subprocess.run(
                command,
                cwd=root,
                env=_runtime_smoke_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise PayloadBuildError("isolated payload runtime import smoke could not run") from exc
        if process.returncode:
            raise PayloadBuildError("isolated payload runtime import smoke failed")


def _content_manifest(
    ticket: BuilderTicket,
    *,
    commit_object_sha256: str,
    tree_object_sha256: str,
    runtime_allowlist_sha256: str,
    files: Sequence[ReleaseFile],
) -> bytes:
    document = {
        "format": FORMAT,
        "ticket_digest": ticket.digest,
        "source_commit": ticket.source_commit,
        "source_commit_object_sha256": commit_object_sha256,
        "source_tree": ticket.source_tree,
        "source_tree_object_sha256": tree_object_sha256,
        "runtime_allowlist_sha256": runtime_allowlist_sha256,
        "file_count": len(files),
        "files": [
            {
                "path": item.path,
                "mode": f"{item.mode:04o}",
                "size_bytes": len(item.content),
                "blob_oid": item.object_id,
                "sha256": item.sha256,
            }
            for item in files
        ],
    }
    return canonical_json_bytes(document)


def _release_manifest(ticket: BuilderTicket) -> bytes:
    # Schema v1 is the exact manifest currently enforced by the release agent.
    # Rich member evidence lives in the separate embedded content manifest.
    return canonical_json_bytes(
        {
            "schema_version": 1,
            "code_version": ticket.code_version,
            "config_revision": ticket.config_revision,
        }
    )


def _add_tar_file(archive: tarfile.TarFile, item: ReleaseFile) -> None:
    _validate_path(item.path, selected=True)
    info = tarfile.TarInfo(item.path)
    info.size = len(item.content)
    info.mode = item.mode
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    info.mtime = 0
    info.type = tarfile.REGTYPE
    archive.addfile(info, io.BytesIO(item.content))


def _write_exclusive(path: Path, content: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise PayloadBuildError("payload output already exists")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def build_release(
    repository_root: Path,
    ticket: BuilderTicket,
    output_directory: Path,
) -> dict[str, Any]:
    """Build from an exact commit/tree; worktree dirt never enters the result."""

    _validate_repository(repository_root)
    output = output_directory.resolve(strict=True)
    if output != output_directory or output.is_symlink():
        raise PayloadBuildError("output directory is not canonical")
    _regular_directory(output, label="output directory")
    if any(output.iterdir()):
        raise PayloadBuildError("output directory must be empty")
    commit = ticket.source_commit
    tree_id = ticket.source_tree
    if not COMMIT_PATTERN.fullmatch(commit) or not COMMIT_PATTERN.fullmatch(tree_id):
        raise PayloadBuildError("ticket Git identity is invalid")
    if len(commit) == 40:
        algorithm, oid_length = "sha1", 40
    elif len(commit) == 64:
        algorithm, oid_length = "sha256", 64
    else:  # pragma: no cover - ticket validation owns this fence.
        raise PayloadBuildError("ticket Git object format is invalid")
    repository_algorithm = _run_git(
        repository_root, "rev-parse", "--show-object-format", maximum=32
    ).decode("ascii").strip()
    if repository_algorithm != algorithm:
        raise PayloadBuildError("ticket and repository Git object formats differ")
    commit_bytes = _read_and_verify_object(
        repository_root,
        commit,
        "commit",
        algorithm=algorithm,
        maximum=16 * 1024 * 1024,
    )
    resolved_tree = _run_git(
        repository_root, "rev-parse", "--verify", f"{commit}^{{tree}}", maximum=256
    ).decode("ascii").strip()
    if resolved_tree != tree_id:
        raise PayloadBuildError("commit tree differs from the approved ticket")
    tree_bytes = _read_and_verify_object(
        repository_root,
        tree_id,
        "tree",
        algorithm=algorithm,
        maximum=MAX_GIT_OUTPUT_BYTES,
    )
    entries = _parse_tree(
        _run_git(repository_root, "ls-tree", "-r", "-z", "--full-tree", tree_id),
        oid_length=oid_length,
    )
    selected = _selected_entries(entries)
    files: list[ReleaseFile] = []
    input_size = 0
    for entry in selected:
        content = _read_and_verify_object(
            repository_root,
            entry.object_id,
            "blob",
            algorithm=algorithm,
            maximum=MAX_FILE_BYTES + 1,
        )
        if len(content) > MAX_FILE_BYTES:
            raise PayloadBuildError("payload blob size is outside the reviewed bound")
        input_size += len(content)
        if input_size > MAX_INPUT_BYTES:
            raise PayloadBuildError("payload input exceeds the aggregate size bound")
        files.append(
            ReleaseFile(
                path=entry.path,
                mode=0o755 if entry.mode == "100755" else 0o644,
                object_id=entry.object_id,
                content=content,
            )
        )
    if not 1 <= len(files) <= MAX_FILES - 2:
        raise PayloadBuildError("payload file count is outside the reviewed bound")
    file_map = {item.path: item for item in files}
    _validate_first_party_import_closure(file_map, entries)
    _source_revisions_match(ticket, file_map)
    runtime_allowlist_sha256 = _runtime_allowlist_sha256(file_map)
    commit_object_sha256 = hashlib.sha256(commit_bytes).hexdigest()
    tree_object_sha256 = hashlib.sha256(tree_bytes).hexdigest()
    content_manifest = _content_manifest(
        ticket,
        commit_object_sha256=commit_object_sha256,
        tree_object_sha256=tree_object_sha256,
        runtime_allowlist_sha256=runtime_allowlist_sha256,
        files=files,
    )
    content_manifest_sha256 = hashlib.sha256(content_manifest).hexdigest()
    release_manifest = _release_manifest(ticket)
    generated = [
        ReleaseFile(RELEASE_MANIFEST_NAME, 0o444, "generated", release_manifest),
        ReleaseFile(CONTENT_MANIFEST_NAME, 0o444, "generated", content_manifest),
    ]
    archive_files = sorted([*files, *generated], key=lambda item: item.path.encode("ascii"))
    collision_keys = [item.path.casefold() for item in archive_files]
    if len(collision_keys) != len(set(collision_keys)):
        raise PayloadBuildError("generated and committed payload members collide")
    archive_buffer = io.BytesIO()
    try:
        with gzip.GzipFile(
            filename="", mode="wb", fileobj=archive_buffer, mtime=0, compresslevel=9
        ) as compressed:
            with tarfile.open(
                fileobj=compressed, mode="w", format=tarfile.GNU_FORMAT
            ) as archive:
                for item in archive_files:
                    _add_tar_file(archive, item)
    except (MemoryError, OSError, OverflowError, tarfile.TarError) as exc:
        raise PayloadBuildError("payload archive could not be built within its bound") from exc
    archive_bytes = archive_buffer.getvalue()
    if not 1 <= len(archive_bytes) <= MAX_ARCHIVE_BYTES:
        raise PayloadBuildError("payload archive size is outside the reviewed bound")
    _validate_archive_runtime(archive_bytes, output, archive_files)
    archive_sha256 = hashlib.sha256(archive_bytes).hexdigest()
    evidence_document: dict[str, Any] = {
        "format": BUILDER_EVIDENCE_FORMAT,
        "ticket_id": ticket.ticket_id,
        "ticket_digest": ticket.digest,
        "build_request_id": ticket.build_request_id,
        "environment": ticket.environment,
        "request_digest": ticket.request_digest,
        "source_commit": commit,
        "source_commit_object_sha256": commit_object_sha256,
        "source_tree": tree_id,
        "source_tree_object_sha256": tree_object_sha256,
        "runtime_allowlist_sha256": runtime_allowlist_sha256,
        "code_version": ticket.code_version,
        "config_revision": ticket.config_revision,
        "test_profile": ticket.test_profile,
        "source_approval_receipt_id": ticket.source_approval_receipt_id,
        "source_approval_digest": ticket.source_approval_digest,
        "archive_sha256": archive_sha256,
        "archive_size_bytes": len(archive_bytes),
        "content_manifest_sha256": content_manifest_sha256,
        "content_manifest_size_bytes": len(content_manifest),
        "file_count": len(files),
        "input_size_bytes": input_size,
        "object_count": len(files) + 2,
        "registration_ready": False,
        "blocked_reasons": list(BLOCKED_REGISTRATION_REASONS),
    }
    evidence = BuilderEvidence.parse(evidence_document)
    evidence_bytes = canonical_json_bytes(evidence.document)
    _write_exclusive(output / ARCHIVE_NAME, archive_bytes)
    _write_exclusive(output / EVIDENCE_NAME, evidence_bytes)
    _write_exclusive(output / f"{CONTENT_MANIFEST_NAME}.detached", content_manifest)
    return {
        "format": "mooncen-crawler-payload-build-result-v1",
        "archive_name": ARCHIVE_NAME,
        "archive_sha256": archive_sha256,
        "archive_size_bytes": len(archive_bytes),
        "builder_evidence_name": EVIDENCE_NAME,
        "builder_evidence_sha256": evidence.digest,
        "content_manifest_name": f"{CONTENT_MANIFEST_NAME}.detached",
        "content_manifest_sha256": content_manifest_sha256,
        "file_count": len(files),
        "registration_ready": False,
        "blocked_reasons": list(BLOCKED_REGISTRATION_REASONS),
    }


def _ticket_path(name: str) -> Path:
    if TICKET_NAME_PATTERN.fullmatch(name) is None:
        raise PayloadBuildError("ticket name is invalid")
    root = TICKET_INBOX_ROOT.resolve(strict=True)
    if root != TICKET_INBOX_ROOT:
        raise PayloadBuildError("ticket inbox root is not canonical")
    path = root / name
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise PayloadBuildError("builder ticket is unavailable") from exc
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or not 1 <= metadata.st_size <= MAX_TICKET_BYTES:
        raise PayloadBuildError("builder ticket is not a bounded regular file")
    return path


def _read_ticket(path: Path) -> BuilderTicket:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            data = handle.read(MAX_TICKET_BYTES + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
    ):
        raise PayloadBuildError("builder ticket changed while it was read")
    try:
        return load_ticket_json(data, maximum=MAX_TICKET_BYTES)
    except BuilderEvidenceError as exc:
        raise PayloadBuildError(str(exc)) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticket", required=True, help="Canonical ticket filename; never a path")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        ticket = _read_ticket(_ticket_path(arguments.ticket))
        output = OUTPUT_ROOT / ticket.ticket_id
        output.mkdir(mode=0o700)
        result = build_release(REPOSITORY_ROOT, ticket, output)
    except (OSError, PayloadBuildError) as exc:
        raise SystemExit(f"crawler payload build rejected: {exc}") from exc
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
