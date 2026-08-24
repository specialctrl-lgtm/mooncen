#!/usr/bin/env python3
"""Verify that Docker build inputs exactly match a reviewed Git commit."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shlex
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Sequence


ROOT = Path(__file__).resolve().parents[2]
MAX_REPORTED_PATHS = 20
MAX_DISPLAY_PATH_CHARS = 240
MAX_DOCKERIGNORE_BYTES = 64 * 1024
MAX_DOCKERIGNORE_PATTERNS = 512

# These files define, exercise, or document the clean-clone Docker contract.
# Requiring them in HEAD prevents a locally complete but unpublishable stack
# from being mistaken for reproducible evidence.
REQUIRED_CONTROL_PATHS = (
    ".dockerignore",
    ".gitattributes",
    ".gitleaks.toml",
    ".github/workflows/docker-development.yml",
    ".github/dependabot.yml",
    ".gitignore",
    "compose.yaml",
    "DB/connection_settings.py",
    "DB/migrations/20260819_001_ops_container_deployment_pipeline.sql",
    "DB/provision_deployment_worker_login.sql",
    "DB/provision_login_roles.sql",
    "DB/roles.sql",
    "DB/roles_body.sql",
    "backend/ops/schemas.py",
    "backend/ops/service.py",
    "backend/main.py",
    "backend/ops_static.py",
    "backend/routers/auth.py",
    "backend/routers/ops_v2.py",
    "deploy/__init__.py",
    "deploy/an2p/__init__.py",
    "deploy/an2p/README.md",
    "deploy/an2p/check_docker_environment.py",
    "deploy/an2p/container_evidence_handoff.py",
    "deploy/an2p/cloud/mooncen-an2p-deploy-sshd.service",
    "deploy/an2p/cloud/mooncen-an2p-deploy-sshd_config",
    "deploy/an2p/cloud/mooncen_container_ingress.py",
    "deploy/an2p/cloud/mooncen_container_ssh_dispatch.py",
    "deploy/an2p/cloud/provision_cloud_deploy_endpoint.sh",
    "deploy/an2p/bootstrap_runtime_installer.sh",
    "deploy/an2p/install_development_runtime.sh",
    "deploy/an2p/install_isolated_control_plane.sh",
    "deploy/an2p/install_runtime_snapshot.sh",
    "deploy/an2p/install_user_services.sh",
    "deploy/an2p/local/cloud-deploy.known_hosts",
    "deploy/an2p/local/cloud-container-deploy.ssh_config",
    "deploy/an2p/local/cloud-container-status.ssh_config",
    "deploy/an2p/local/cloud-ops-db.ssh_config",
    "deploy/an2p/mooncen-api.service",
    "deploy/an2p/mooncen-development-runtime.target",
    "deploy/an2p/mooncen-docker-dev.service",
    "deploy/an2p/mooncen-frontend.service",
    "deploy/an2p/mooncen-deployment-worker.service",
    "deploy/an2p/mooncen-an2p-runtime-recovery.service",
    "deploy/an2p/mooncen-ops-api.service",
    "deploy/an2p/mooncen-ops-api.socket",
    "deploy/an2p/mooncen-ops-status-agent.service",
    "deploy/an2p/mooncen-ops-api-ipv6.service",
    "deploy/an2p/mooncen-ops-api-ipv6.socket",
    "deploy/an2p/mooncen-ops-console.service",
    "deploy/an2p/mooncen-ops-control-env.service",
    "deploy/an2p/mooncen-ops-db-tunnel.service",
    "deploy/an2p/mooncen_register_container_evidence.py",
    "deploy/an2p/mooncen-status-agent.service",
    "deploy/an2p/mooncen_an2p_service_control.py",
    "deploy/an2p/receive_control_bootstrap.py",
    "deploy/an2p/mooncen_loopback_redirect.py",
    "deploy/an2p/ops_console_static.Dockerfile",
    "deploy/an2p/ops_console_static.Dockerfile.dockerignore",
    "deploy/an2p/runtime_pair_manager.py",
    "deploy/an2p/validate_docker_release.py",
    "deploy/docker/api.Dockerfile",
    "deploy/docker/__init__.py",
    "deploy/docker/bootstrap_production_runtime.py",
    "deploy/docker/build_release_bundle.py",
    "deploy/docker/compose.production.yaml",
    "deploy/docker/create_review_snapshot.py",
    "deploy/docker/docker.env.example",
    "deploy/docker/frontend.Dockerfile",
    "deploy/docker/install_production_runtime.sh",
    "deploy/docker/mooncen-container-release",
    "deploy/docker/mooncen_container_release.py",
    "deploy/docker/native_baseline.py",
    "deploy/docker/nginx.conf",
    "deploy/docker/postgres.Dockerfile",
    "deploy/docker/promote_review_snapshot.py",
    "deploy/docker/provision_api_login.py",
    "deploy/docker/production_runtime_integrity.py",
    "deploy/docker/release_manifest.py",
    "deploy/docker/render_runtime_config.py",
    "deploy/docker/smoke.py",
    "deploy/docker/verify_clean_source.py",
    "deploy/docker/verify_release_bundle.py",
    "deploy/ubuntu/install_sudoers.sh",
    "deploy/ubuntu/nginx/mooncen.conf",
    "deploy/ubuntu/configure_container_pg_hba.py",
    "deploy/ubuntu/deploy_from_windows.ps1",
    "deploy/ubuntu/export_an2p_control_secrets.py",
    "deploy/ubuntu/mooncen_release_guard.sh",
    "deploy/ubuntu/mooncen_native_runtime_condition.py",
    "deploy/ubuntu/setup_project.sh",
    "deploy/ubuntu/systemd/mooncen-ai-worker.service",
    "deploy/ubuntu/systemd/mooncen-api.service",
    "deploy/ubuntu/systemd/mooncen-container-release-guard@.service",
    "deploy/ubuntu/systemd/mooncen-container-stack.service",
    "deploy/ubuntu/systemd/mooncen-deploy-guard@.service",
    "deploy/ubuntu/systemd/mooncen-frontend.service",
    "deploy_mooncen.ps1",
    "deploy_ubuntu.ps1",
    "docs/docker-development.md",
    "docs/an2p-control-plane-architecture.md",
    "docs/docker-ops-console.md",
    "docs/docker-production.md",
    "frontend2/public/runtime-config.js",
    "ops-console/.env.example",
    "ops-console/index.html",
    "ops-console/package-lock.json",
    "ops-console/package.json",
    "ops-console/src/App.tsx",
    "ops-console/src/api.ts",
    "ops-console/src/auth.ts",
    "ops-console/src/components/DataTable.tsx",
    "ops-console/src/components/ErrorBoundary.tsx",
    "ops-console/src/components/Layout.tsx",
    "ops-console/src/components/StatusBadge.tsx",
    "ops-console/src/components/Ui.tsx",
    "ops-console/src/context.tsx",
    "ops-console/src/hooks/useJobEventStream.ts",
    "ops-console/src/hooks/useUrlFilters.ts",
    "ops-console/src/main.tsx",
    "ops-console/src/pages/AgentsPage.tsx",
    "ops-console/src/pages/ContentPage.tsx",
    "ops-console/src/pages/CrawlerAnalyticsPage.tsx",
    "ops-console/src/pages/CrawlerImprovementsPage.tsx",
    "ops-console/src/pages/CrawlerReleasesPage.tsx",
    "ops-console/src/pages/CrawlerStudioPage.tsx",
    "ops-console/src/pages/CrawlersPage.tsx",
    "ops-console/src/pages/DashboardPage.tsx",
    "ops-console/src/pages/DeploymentsPage.test.tsx",
    "ops-console/src/pages/DeploymentsPage.tsx",
    "ops-console/src/pages/JobsAuditPage.tsx",
    "ops-console/src/pages/QualityPage.tsx",
    "ops-console/src/pages/RegionCoveragePage.tsx",
    "ops-console/src/pages/ServicesPage.tsx",
    "ops-console/src/pages/SettingsPage.tsx",
    "ops-console/src/styles.css",
    "ops-console/src/types.ts",
    "ops-console/src/utils.ts",
    "ops-console/src/vite-env.d.ts",
    "ops-console/tsconfig.json",
    "ops-console/vite.config.ts",
    "ops_agent/container_deployment.py",
    "ops_agent/deployment_registry.py",
    "ops_agent/deployment_worker.py",
    "ops_agent/production_topology.py",
    "tests/test_an2p_docker_release_selection.py",
    "tests/test_an2p_container_evidence_handoff.py",
    "tests/test_an2p_loopback_redirect.py",
    "tests/test_an2p_runtime_installer_contract.py",
    "tests/test_an2p_runtime_installer_recovery.py",
    "tests/test_an2p_runtime_pair_manager.py",
    "tests/test_an2p_runtime_selector.py",
    "tests/test_ai_check_db_roles.py",
    "tests/test_backend_security.py",
    "tests/test_clean_release_deployment.py",
    "tests/test_container_runtime_host_contracts.py",
    "tests/test_container_transport_isolation.py",
    "tests/test_deployment_db_roles.py",
    "tests/test_staging_safety_contract.py",
    "tests/test_deployment_registry_profiles.py",
    "tests/test_docker_clean_source.py",
    "tests/test_docker_development_contract.py",
    "tests/test_docker_production_contract.py",
    "tests/test_docker_production_runtime.py",
    "tests/test_export_an2p_control_secrets.py",
    "tests/test_receive_an2p_control_bootstrap.py",
    "tests/test_docker_release_builder.py",
    "tests/test_docker_release_bundle_verifier.py",
    "tests/test_docker_release_manifest.py",
    "tests/test_docker_review_promotion.py",
    "tests/test_docker_review_snapshot.py",
    "tests/test_docker_runtime_config.py",
    "tests/test_docker_smoke_runner.py",
    "tests/test_migration_plan.py",
    "tests/test_ops_container_deployment_pipeline.py",
    "tests/test_ops_static_bundle.py",
    "tests/test_ops_console_separation.py",
    "tests/test_ops_deployment_api_gating.py",
    "tests/test_release_guard_state_machine.py",
    "tests/test_rotate_an2p_ops_password.py",
    "tests/test_remaining_security_contracts.py",
    "tests/test_deployment_worker_recovery.py",
    "tests/test_production_topology_contract.py",
    "tools/prepare_an2p_ops_control.py",
    "tools/register_container_deployment_evidence.py",
    "tools/rotate_an2p_ops_password.py",
    "tools/seal_ops_static.py",
    "tools/wait_for_an2p_database.py",
    "tools/wait_for_an2p_http.py",
)

# These reviewed directories contain control-plane dependencies whose exact
# transitive file set is intentionally broader than the individual P0 files
# above.  They are provenance inputs, but are not Docker build-context inputs.
# Generated output and dependencies remain excluded by Git ignore policy.
CONTROL_INPUT_PATHS = (
    "deploy/an2p",
    "deploy/ops-console",
    "ops-console",
)

# Keep this list aligned with every COPY source in the Dockerfiles. Directories
# are intentionally broad because a file anywhere below them changes the build
# context copied into an image, even when that file is not imported at startup.
BUILD_INPUT_PATHS = (
    "requirements.txt",
    "requirements.lock",
    "backend",
    "DB",
    "Crawler",
    "config",
    "ops_agent",
    "tools",
    "utils",
    "ai_processor.py",
    "data_parser.py",
    "description_cleaner.py",
    "run_ai_pipeline.py",
    "run_crawlers.py",
    "service_group.py",
    "target_category_fallback.py",
    "target_cleaner.py",
    "title_cleaner.py",
    "utils.py",
    "frontend2",
    "frontend2/package.json",
    "frontend2/package-lock.json",
    "config/privacy_membership_notice.json",
    "deploy/docker/nginx.conf",
    "deploy/__init__.py",
    "deploy/docker/__init__.py",
    "deploy/docker/provision_api_login.py",
    "deploy/docker/release_manifest.py",
    "deploy/docker/render_runtime_config.py",
    "deploy/docker/verify_release_bundle.py",
)


class SourceVerificationError(RuntimeError):
    """Raised when Git cannot provide trustworthy source provenance."""


def repository_copy_sources(dockerfile: str) -> tuple[str, ...]:
    """Return local COPY sources from one supported Dockerfile."""

    logical_lines: list[str] = []
    pending = ""
    for raw_line in dockerfile.splitlines():
        line = raw_line.strip()
        if re.match(r"#\s*escape\s*=", line, flags=re.IGNORECASE):
            raise SourceVerificationError(
                "Dockerfile escape parser directives are not supported."
            )
        if not line or line.startswith("#"):
            continue
        pending = f"{pending} {line}".strip()
        if pending.endswith("\\"):
            pending = pending[:-1].rstrip()
            continue
        logical_lines.append(pending)
        pending = ""
    if pending:
        raise SourceVerificationError("A Dockerfile has an incomplete continuation.")

    sources: list[str] = []
    known_stages: set[str] = set()
    current_stage_alias: str | None = None
    stage_count = 0
    for line in logical_lines:
        try:
            tokens = shlex.split(line, posix=True)
        except ValueError as exc:
            raise SourceVerificationError("A Dockerfile has invalid shell quoting.") from exc
        if not tokens:
            continue
        directive = tokens[0].upper()
        if directive == "FROM":
            stage_arguments = [
                token for token in tokens[1:] if not token.startswith("--platform=")
            ]
            if len(stage_arguments) not in {1, 3} or (
                len(stage_arguments) == 3
                and stage_arguments[-2].upper() != "AS"
            ):
                raise SourceVerificationError("A Dockerfile FROM is invalid.")
            image = stage_arguments[0]
            if (
                image.lower() != "scratch"
                and image.lower() not in known_stages
                and not re.fullmatch(r"[^@\s]+@sha256:[0-9a-f]{64}", image)
            ):
                raise SourceVerificationError(
                    "Every external Dockerfile FROM image must be SHA-256 pinned."
                )
            current_stage_alias = None
            if len(stage_arguments) == 3:
                alias = stage_arguments[-1].lower()
                if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", alias):
                    raise SourceVerificationError("A Dockerfile stage alias is invalid.")
                if alias in known_stages:
                    raise SourceVerificationError("A Dockerfile stage alias is duplicated.")
                known_stages.add(alias)
                current_stage_alias = alias
            stage_count += 1
            continue
        if directive == "ADD":
            raise SourceVerificationError("Dockerfile ADD is not allowed.")
        if directive == "RUN" and any(
            token.startswith("--mount=") for token in tokens[1:]
        ):
            raise SourceVerificationError("Dockerfile RUN mounts are not allowed.")
        if directive == "ONBUILD" and any(
            token.upper() in {"ADD", "COPY"} for token in tokens[1:2]
        ):
            raise SourceVerificationError("Dockerfile ONBUILD context reads are not allowed.")
        if directive != "COPY":
            continue
        arguments = tokens[1:]
        from_reference: str | None = None
        while arguments and arguments[0].startswith("--"):
            option = arguments.pop(0)
            option_name, separator, value = option.partition("=")
            if option_name not in {"--from", "--chown", "--chmod", "--exclude"}:
                raise SourceVerificationError("A Dockerfile COPY option is unsupported.")
            if option_name == "--from":
                from_reference = value if separator else None
            if not separator:
                if not arguments:
                    raise SourceVerificationError("A Dockerfile COPY option lacks a value.")
                option_value = arguments.pop(0)
                if option_name == "--from":
                    from_reference = option_value
        if from_reference is not None:
            known_reference = (
                from_reference.isascii()
                and from_reference.isdecimal()
                and int(from_reference) < stage_count - 1
            ) or (
                from_reference.lower() in known_stages
                and from_reference.lower() != current_stage_alias
            )
            if not known_reference:
                raise SourceVerificationError(
                    "Dockerfile COPY --from must name an earlier local stage."
                )
            continue
        if line[line.upper().find("COPY") + 4 :].lstrip().startswith("["):
            raise SourceVerificationError("JSON-form Dockerfile COPY is unsupported.")
        if len(arguments) < 2:
            raise SourceVerificationError("A Dockerfile COPY lacks source or destination.")
        for source in arguments[:-1]:
            normalized = source.rstrip("/") or "."
            if any(character in normalized for character in "*?[") or "$" in normalized:
                raise SourceVerificationError("A Dockerfile COPY source is not literal.")
            sources.append(normalized)
    return tuple(dict.fromkeys(sources))


@dataclass(frozen=True)
class GitChange:
    status: str
    path: str
    original_path: str | None = None


@dataclass(frozen=True)
class SourceReport:
    missing_from_worktree: tuple[str, ...] = ()
    missing_from_head: tuple[str, ...] = ()
    changes: tuple[GitChange, ...] = ()

    @property
    def ok(self) -> bool:
        return not (self.missing_from_worktree or self.missing_from_head or self.changes)

    @property
    def issue_count(self) -> int:
        return len(self.missing_from_worktree) + len(self.missing_from_head) + len(self.changes)


def _git(root: Path, arguments: Sequence[str], *, timeout: int = 30) -> bytes:
    try:
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise SourceVerificationError(
            "Docker source root does not exist."
        ) from exc
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
    try:
        result = subprocess.run(
            [
                "git",
                "-c",
                f"core.hooksPath={os.devnull}",
                "-c",
                "core.fsmonitor=false",
                "-c",
                f"safe.directory={resolved}",
                *arguments,
            ],
            cwd=resolved,
            env=environment,
            check=False,
            capture_output=True,
            timeout=timeout,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError) as exc:
        raise SourceVerificationError("Git is required for Docker source verification.") from exc
    if result.returncode != 0:
        operation = arguments[0] if arguments else "command"
        raise SourceVerificationError(f"Git source verification failed during {operation}.")
    return result.stdout


def _decode_paths(raw: bytes) -> tuple[str, ...]:
    return tuple(
        item.decode("utf-8", errors="surrogateescape")
        for item in raw.split(b"\0")
        if item
    )


def _validated_paths(paths: Sequence[str], *, label: str) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in paths:
        path = PurePosixPath(value)
        raw_parts = value.split("/")
        if (
            not value
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in raw_parts)
            or "\\" in value
        ):
            raise SourceVerificationError(f"The {label} manifest contains an unsafe path.")
        normalized.append(path.as_posix())
    if len(normalized) != len(set(normalized)):
        raise SourceVerificationError(f"The {label} manifest contains a duplicate path.")
    return tuple(normalized)


def _assert_repository_root(root: Path) -> Path:
    resolved_root = root.resolve()
    inside = _git(resolved_root, ("rev-parse", "--is-inside-work-tree")).strip()
    if inside != b"true":
        raise SourceVerificationError("Docker source verification requires a Git worktree.")
    top_level_raw = _git(resolved_root, ("rev-parse", "--show-toplevel")).strip()
    try:
        top_level = Path(top_level_raw.decode("utf-8", errors="strict")).resolve()
    except UnicodeDecodeError as exc:
        raise SourceVerificationError("The Git worktree path is not valid UTF-8.") from exc
    if os.path.normcase(str(top_level)) != os.path.normcase(str(resolved_root)):
        raise SourceVerificationError("Docker source verification must run at the Git worktree root.")
    _git(resolved_root, ("rev-parse", "--verify", "HEAD^{commit}"))
    return resolved_root


def _parse_porcelain(raw: bytes) -> tuple[GitChange, ...]:
    records = raw.split(b"\0")
    changes: list[GitChange] = []
    index = 0
    while index < len(records):
        record = records[index]
        if not record:
            break
        if len(record) < 4 or record[2:3] != b" ":
            raise SourceVerificationError("Git returned an unexpected status record.")
        status = record[:2].decode("ascii", errors="strict")
        path = record[3:].decode("utf-8", errors="surrogateescape")
        original_path: str | None = None
        if "R" in status or "C" in status:
            index += 1
            if index >= len(records) or not records[index]:
                raise SourceVerificationError("Git returned an incomplete rename status record.")
            original_path = records[index].decode("utf-8", errors="surrogateescape")
        changes.append(GitChange(status=status, path=path, original_path=original_path))
        index += 1
    return tuple(changes)


def _head_regular_paths(
    root: Path,
    monitored_paths: Sequence[str],
) -> tuple[str, ...]:
    raw = _git(
        root,
        ("ls-tree", "-r", "-z", "HEAD", "--", *monitored_paths),
    )
    paths: list[str] = []
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        metadata, separator, raw_path = entry.partition(b"\t")
        fields = metadata.split()
        if separator != b"\t" or len(fields) != 3:
            raise SourceVerificationError("Git returned an invalid HEAD tree entry.")
        try:
            mode = fields[0].decode("ascii", errors="strict")
            object_type = fields[1].decode("ascii", errors="strict")
            path = raw_path.decode("utf-8", errors="surrogateescape")
        except UnicodeDecodeError as exc:
            raise SourceVerificationError("Git returned an invalid HEAD tree entry.") from exc
        if mode not in {"100644", "100755"} or object_type != "blob":
            raise SourceVerificationError(
                "Docker source paths in HEAD must be regular tracked files."
            )
        paths.append(path)
    return tuple(paths)


def _assert_safe_worktree_types(root: Path, paths: Sequence[str]) -> None:
    for relative_path in paths:
        current = root
        parts = PurePosixPath(relative_path).parts
        for index, part in enumerate(parts):
            current /= part
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                break
            except OSError as exc:
                raise SourceVerificationError(
                    "A Docker source worktree path could not be inspected safely."
                ) from exc
            file_attributes = getattr(metadata, "st_file_attributes", 0)
            is_reparse_point = bool(
                file_attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            )
            is_last = index == len(parts) - 1
            valid_type = (
                stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)
                if is_last
                else stat.S_ISDIR(metadata.st_mode)
            )
            if stat.S_ISLNK(metadata.st_mode) or is_reparse_point or not valid_type:
                raise SourceVerificationError(
                    "Docker source worktree paths must not contain links or special files."
                )


def _load_dockerignore(path: Path) -> tuple[tuple[bool, tuple[str, ...]], ...]:
    try:
        if path.stat().st_size > MAX_DOCKERIGNORE_BYTES:
            raise SourceVerificationError(".dockerignore is too large to verify safely.")
        lines = path.read_text(encoding="utf-8").splitlines()
    except SourceVerificationError:
        raise
    except (OSError, UnicodeError) as exc:
        raise SourceVerificationError(".dockerignore could not be read safely.") from exc

    patterns: list[tuple[bool, tuple[str, ...]]] = []
    for raw_line in lines:
        value = raw_line.strip()
        if not value or value.startswith("#"):
            continue
        negated = value.startswith("!")
        if negated:
            value = value[1:].strip()
        value = value.strip("/")
        if not value or value == ".":
            continue
        if "\\" in value:
            raise SourceVerificationError(".dockerignore contains an unsupported path separator.")
        parts = tuple(value.split("/"))
        if any(part in {"", ".", ".."} for part in parts):
            raise SourceVerificationError(".dockerignore contains an unsafe pattern.")
        if any("**" in part and part != "**" for part in parts):
            raise SourceVerificationError(".dockerignore contains an unsupported ** pattern.")
        patterns.append((negated, parts))
        if len(patterns) > MAX_DOCKERIGNORE_PATTERNS:
            raise SourceVerificationError(".dockerignore contains too many patterns.")
    return tuple(patterns)


def _glob_parts_match(candidate: tuple[str, ...], pattern: tuple[str, ...]) -> bool:
    memo: dict[tuple[int, int], bool] = {}

    def matches(candidate_index: int, pattern_index: int) -> bool:
        key = (candidate_index, pattern_index)
        if key in memo:
            return memo[key]
        if pattern_index == len(pattern):
            result = candidate_index == len(candidate)
        elif pattern[pattern_index] == "**":
            result = matches(candidate_index, pattern_index + 1) or (
                candidate_index < len(candidate)
                and matches(candidate_index + 1, pattern_index)
            )
        else:
            result = candidate_index < len(candidate) and fnmatch.fnmatchcase(
                candidate[candidate_index], pattern[pattern_index]
            ) and matches(candidate_index + 1, pattern_index + 1)
        memo[key] = result
        return result

    return matches(0, 0)


def _docker_ignores(
    relative_path: str,
    patterns: Sequence[tuple[bool, tuple[str, ...]]],
) -> bool:
    path_parts = tuple(PurePosixPath(relative_path).parts)
    ignored = False
    for negated, pattern in patterns:
        matched = any(
            _glob_parts_match(path_parts[:length], pattern)
            for length in range(1, len(path_parts) + 1)
        )
        if matched:
            ignored = not negated
    return ignored


def _git_ignored_but_docker_included(
    root: Path,
    inputs: Sequence[str],
) -> tuple[GitChange, ...]:
    dockerignore = root / ".dockerignore"
    if not dockerignore.is_file():
        return ()
    patterns = _load_dockerignore(dockerignore)
    ignored_paths = _decode_paths(
        _git(
            root,
            (
                "ls-files",
                "--others",
                "--ignored",
                "--exclude-standard",
                "-z",
                "--",
                *inputs,
            ),
        )
    )
    return tuple(
        GitChange(status="!!", path=path)
        for path in ignored_paths
        if not _docker_ignores(path, patterns)
    )


def inspect_source(
    root: Path = ROOT,
    *,
    required_paths: Sequence[str] = REQUIRED_CONTROL_PATHS,
    control_input_paths: Sequence[str] = CONTROL_INPUT_PATHS,
    build_input_paths: Sequence[str] = BUILD_INPUT_PATHS,
) -> SourceReport:
    """Return all provenance violations without reading source-file contents."""

    root = _assert_repository_root(root)
    required = _validated_paths(required_paths, label="required-control")
    controls = _validated_paths(control_input_paths, label="control-input")
    inputs = _validated_paths(build_input_paths, label="build-input")

    expected_paths = tuple(dict.fromkeys((*required, *controls, *inputs)))
    head_paths = set(_head_regular_paths(root, expected_paths))
    _assert_safe_worktree_types(
        root,
        tuple(dict.fromkeys((*expected_paths, *head_paths))),
    )
    missing_from_worktree = tuple(
        sorted(
            path
            for path in expected_paths
            if not (root / Path(*PurePosixPath(path).parts)).exists()
        )
    )
    missing_from_head_set = {path for path in required if path not in head_paths}
    for path in (*controls, *inputs):
        prefix = f"{path.rstrip('/')}/"
        if path not in head_paths and not any(candidate.startswith(prefix) for candidate in head_paths):
            missing_from_head_set.add(path)
    missing_from_head = tuple(sorted(missing_from_head_set))

    monitored_paths = tuple(dict.fromkeys((*inputs, *controls, *required)))
    status_raw = _git(
        root,
        (
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--",
            *monitored_paths,
        ),
    )
    changes = tuple(
        sorted(
            (*_parse_porcelain(status_raw), *_git_ignored_but_docker_included(root, inputs)),
            key=lambda item: (item.path, item.status),
        )
    )
    return SourceReport(
        missing_from_worktree=missing_from_worktree,
        missing_from_head=missing_from_head,
        changes=changes,
    )


def _display_path(path: str) -> str:
    bounded = path
    if len(bounded) > MAX_DISPLAY_PATH_CHARS:
        bounded = f"{bounded[: MAX_DISPLAY_PATH_CHARS - 3]}..."
    return json.dumps(bounded, ensure_ascii=True)


def format_failure(report: SourceReport, *, limit: int = MAX_REPORTED_PATHS) -> str:
    """Format a bounded, filename-only failure suitable for CI and terminals."""

    if limit < 0:
        raise ValueError("limit must not be negative")
    lines = [
        "Docker source is not reproducible from HEAD: "
        f"{report.issue_count} provenance issue(s)."
    ]
    issues: list[tuple[str, str]] = []
    issues.extend(("missing-worktree", path) for path in report.missing_from_worktree)
    issues.extend(("missing-HEAD", path) for path in report.missing_from_head)
    for change in report.changes:
        path = change.path
        if change.original_path is not None:
            path = f"{change.original_path} -> {path}"
        issues.append((f"git-{change.status}", path))
    for label, path in issues[:limit]:
        lines.append(f"- {label}: {_display_path(path)}")
    omitted = len(issues) - min(len(issues), limit)
    if omitted:
        lines.append(f"- ... {omitted} additional issue(s) omitted")
    lines.append("Commit or restore every Docker input, then rerun the verifier.")
    return "\n".join(lines)


def require_clean_source(root: Path = ROOT) -> SourceReport:
    report = inspect_source(root)
    if not report.ok:
        raise SourceVerificationError(format_failure(report))
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    try:
        require_clean_source(ROOT)
    except SourceVerificationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        "Docker source verification passed: required control files and build inputs "
        "match HEAD."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
