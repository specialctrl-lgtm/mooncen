#!/usr/bin/env python3
"""Bind the installed production controller to the reviewed build policy.

The Docker release manifest already carries a digest of every file that can
change build, validation, bootstrap, or production cutover behaviour.  This
module deliberately shares that digest algorithm with the builder and records
the exact root-owned bytes installed on the production host.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 1
SHA256_PATTERN = re.compile(r"\A[0-9a-f]{64}\Z")
DEPLOY_USER_PATTERN = re.compile(r"\A[a-z_][a-z0-9_-]{0,31}\Z")
UTC_TIMESTAMP_PATTERN = re.compile(
    r"\A20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z"
)
MAX_POLICY_FILE_BYTES = 64 * 1024 * 1024
BOOTSTRAP_CONFIG_PATH = Path("/etc/mooncen/container-bootstrap.json")
INSTALLATION_RECEIPT_PATH = Path("/etc/mooncen/container-runtime-installation.json")

# Keep this tuple ordered.  Its canonical records are hashed in this order by
# both the clean builder and the root bootstrap installer.
BUILD_POLICY_PATHS = (
    ".dockerignore",
    ".gitattributes",
    ".github/dependabot.yml",
    "compose.yaml",
    "DB/connection_settings.py",
    "DB/migrations/20260819_001_ops_container_deployment_pipeline.sql",
    "DB/provision_deployment_worker_login.sql",
    "DB/provision_login_roles.sql",
    "DB/roles.sql",
    "DB/roles_body.sql",
    "DB/setup_db.py",
    "backend/ops/schemas.py",
    "backend/ops/service.py",
    "backend/main.py",
    "backend/ops_static.py",
    "backend/routers/auth.py",
    "backend/routers/ops_v2.py",
    "deploy_mooncen.ps1",
    "deploy_ubuntu.ps1",
    "deploy/__init__.py",
    "deploy/an2p/__init__.py",
    "deploy/an2p/check_docker_environment.py",
    "deploy/an2p/container_evidence_handoff.py",
    "deploy/an2p/cloud/mooncen-an2p-deploy-sshd.service",
    "deploy/an2p/cloud/mooncen-an2p-deploy-sshd_config",
    "deploy/an2p/cloud/mooncen_container_ingress.py",
    "deploy/an2p/cloud/mooncen_container_ssh_dispatch.py",
    "deploy/an2p/cloud/provision_cloud_deploy_endpoint.sh",
    "deploy/an2p/bootstrap_runtime_installer.sh",
    "deploy/an2p/host_layer_transition.py",
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
    "deploy/an2p/mooncen-deployment-worker.service",
    "deploy/an2p/mooncen-an2p-runtime-recovery.service",
    "deploy/an2p/mooncen-docker-dev.service",
    "deploy/an2p/mooncen-frontend.service",
    "deploy/an2p/mooncen-ops-api.service",
    "deploy/an2p/mooncen-ops-api.socket",
    "deploy/an2p/mooncen-ops-status-agent.service",
    "deploy/an2p/mooncen-ops-api-ipv6.service",
    "deploy/an2p/mooncen-ops-api-ipv6.socket",
    "deploy/an2p/mooncen-ops-console.service",
    "deploy/an2p/mooncen-ops-db-tunnel.service",
    "deploy/an2p/mooncen-status-agent.service",
    "deploy/an2p/mooncen_loopback_redirect.py",
    "deploy/an2p/mooncen_register_container_evidence.py",
    "deploy/an2p/mooncen_an2p_service_control.py",
    "deploy/an2p/receive_control_bootstrap.py",
    "deploy/an2p/ops_console_static.Dockerfile",
    "deploy/an2p/ops_console_static.Dockerfile.dockerignore",
    "deploy/an2p/runtime_pair_manager.py",
    "deploy/an2p/validate_docker_release.py",
    "deploy/docker/api.Dockerfile",
    "deploy/docker/__init__.py",
    "deploy/docker/frontend.Dockerfile",
    "deploy/docker/compose.production.yaml",
    "deploy/docker/create_review_snapshot.py",
    "deploy/docker/release_manifest.py",
    "deploy/docker/build_release_bundle.py",
    "deploy/docker/bootstrap_production_runtime.py",
    "deploy/docker/install_production_runtime.sh",
    "deploy/docker/mooncen-container-release",
    "deploy/docker/mooncen_container_release.py",
    "deploy/docker/native_baseline.py",
    "deploy/docker/nginx.conf",
    "deploy/docker/production_runtime_integrity.py",
    "deploy/docker/promote_review_snapshot.py",
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
    "docs/docker-production.md",
    "docs/docker-development.md",
    "docs/docker-ops-console.md",
    "deploy/an2p/README.md",
    "docs/an2p-control-plane-architecture.md",
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
    "tests/test_clean_release_deployment.py",
    "tests/test_an2p_container_evidence_handoff.py",
    "tests/test_an2p_host_layer_transition.py",
    "tests/test_an2p_loopback_redirect.py",
    "tests/test_an2p_runtime_installer_contract.py",
    "tests/test_an2p_runtime_installer_recovery.py",
    "tests/test_an2p_runtime_pair_manager.py",
    "tests/test_an2p_runtime_selector.py",
    "tests/test_ai_check_db_roles.py",
    "tests/test_backend_security.py",
    "tests/test_container_runtime_host_contracts.py",
    "tests/test_container_transport_isolation.py",
    "tests/test_deployment_registry_profiles.py",
    "tests/test_deployment_db_roles.py",
    "tests/test_staging_safety_contract.py",
    "tests/test_deployment_worker_recovery.py",
    "tests/test_docker_production_runtime.py",
    "tests/test_export_an2p_control_secrets.py",
    "tests/test_receive_an2p_control_bootstrap.py",
    "tests/test_ops_container_deployment_pipeline.py",
    "tests/test_ops_console_separation.py",
    "tests/test_ops_static_bundle.py",
    "tests/test_production_topology_contract.py",
    "tests/test_ops_deployment_api_gating.py",
    "tests/test_rotate_an2p_ops_password.py",
    "tests/test_remaining_security_contracts.py",
    "tests/test_release_guard_state_machine.py",
    "tools/prepare_an2p_ops_control.py",
    "tools/register_container_deployment_evidence.py",
    "tools/rotate_an2p_ops_password.py",
    "tools/seal_ops_static.py",
    "tools/wait_for_an2p_database.py",
    "tools/wait_for_an2p_http.py",
)

# label, installed path, exact mode.  The receipt contains these content
# digests and validation re-hashes the live files before every new promotion.
INSTALLED_RUNTIME_FILES = (
    ("controller", Path("/usr/local/libexec/mooncen-container-release"), 0o755),
    (
        "package_init",
        Path("/usr/local/libexec/mooncen-container-release-lib/deploy/__init__.py"),
        0o644,
    ),
    (
        "docker_package_init",
        Path(
            "/usr/local/libexec/mooncen-container-release-lib/deploy/docker/__init__.py"
        ),
        0o644,
    ),
    (
        "controller_python",
        Path(
            "/usr/local/libexec/mooncen-container-release-lib/"
            "deploy/docker/mooncen_container_release.py"
        ),
        0o644,
    ),
    (
        "native_baseline_python",
        Path(
            "/usr/local/libexec/mooncen-container-release-lib/"
            "deploy/docker/native_baseline.py"
        ),
        0o644,
    ),
    (
        "integrity_python",
        Path(
            "/usr/local/libexec/mooncen-container-release-lib/"
            "deploy/docker/production_runtime_integrity.py"
        ),
        0o644,
    ),
    (
        "integrity_entrypoint",
        Path("/usr/local/libexec/production_runtime_integrity.py"),
        0o644,
    ),
    (
        "manifest_python",
        Path(
            "/usr/local/libexec/mooncen-container-release-lib/"
            "deploy/docker/release_manifest.py"
        ),
        0o644,
    ),
    (
        "verifier_python",
        Path(
            "/usr/local/libexec/mooncen-container-release-lib/"
            "deploy/docker/verify_release_bundle.py"
        ),
        0o644,
    ),
    (
        "stack_unit",
        Path("/etc/systemd/system/mooncen-container-stack.service"),
        0o644,
    ),
    (
        "guard_unit",
        Path("/etc/systemd/system/mooncen-container-release-guard@.service"),
        0o644,
    ),
    (
        "native_deploy_guard_unit",
        Path("/etc/systemd/system/mooncen-deploy-guard@.service"),
        0o644,
    ),
    (
        "native_runtime_condition",
        Path("/usr/local/libexec/mooncen-native-runtime-condition"),
        0o755,
    ),
    (
        "container_pg_hba_helper",
        Path("/usr/local/libexec/mooncen-configure-container-pg-hba"),
        0o755,
    ),
    (
        "an2p_control_secret_exporter",
        Path("/usr/local/libexec/mooncen-export-an2p-control-secrets"),
        0o755,
    ),
    (
        "native_api_unit",
        Path("/etc/systemd/system/mooncen-api.service"),
        0o644,
    ),
    (
        "native_frontend_unit",
        Path("/etc/systemd/system/mooncen-frontend.service"),
        0o644,
    ),
    (
        "native_ai_worker_unit",
        Path("/etc/systemd/system/mooncen-ai-worker.service"),
        0o644,
    ),
    (
        "container_ssh_dispatch",
        Path("/usr/local/libexec/mooncen-container-ssh-dispatch"),
        0o755,
    ),
    (
        "container_ingress_helper",
        Path("/usr/local/libexec/mooncen-container-ingress"),
        0o755,
    ),
    (
        "container_transport_sshd_config",
        Path("/etc/ssh/mooncen-an2p-deploy-sshd_config"),
        0o644,
    ),
    (
        "container_transport_sshd_unit",
        Path("/etc/systemd/system/mooncen-an2p-deploy-sshd.service"),
        0o644,
    ),
    ("operator_document", Path("/opt/mooncen/docs/docker-production.md"), 0o644),
)

BOOTSTRAP_CONFIG_KEYS = frozenset(
    {
        "schema_version",
        "source_root",
        "build_policy_sha256",
        "deploy_user",
        "deploy_uid",
        "deploy_gid",
    }
)
INSTALLATION_RECEIPT_KEYS = frozenset(
    {"schema_version", "build_policy_sha256", "installed_files", "installed_at"}
)


class RuntimeIntegrityError(RuntimeError):
    """Raised when reviewed or installed control-plane bytes do not match."""


def _canonical_json(value: Mapping[str, Any]) -> bytes:
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
        raise RuntimeIntegrityError("runtime integrity evidence is not canonical JSON") from exc


def _sha256_file(path: Path, *, maximum: int = MAX_POLICY_FILE_BYTES) -> str:
    digest = hashlib.sha256()
    total = 0
    try:
        metadata = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise RuntimeIntegrityError(f"integrity path is unsafe: {path}")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                total += len(chunk)
                if total > maximum:
                    raise RuntimeIntegrityError(f"integrity path is too large: {path}")
                digest.update(chunk)
    except OSError as exc:
        raise RuntimeIntegrityError(f"integrity path cannot be hashed: {path}") from exc
    return digest.hexdigest()


def _safe_source_root(source_root: Path) -> Path:
    try:
        metadata = source_root.lstat()
        resolved = source_root.resolve(strict=True)
    except OSError as exc:
        raise RuntimeIntegrityError("reviewed source root is unavailable") from exc
    if (
        source_root.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or resolved != source_root.absolute()
    ):
        raise RuntimeIntegrityError("reviewed source root is unsafe")
    return resolved


def build_policy_records(source_root: Path) -> list[dict[str, str]]:
    root = _safe_source_root(source_root)
    records: list[dict[str, str]] = []
    for relative in BUILD_POLICY_PATHS:
        path = root / relative
        try:
            metadata = path.lstat()
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise RuntimeIntegrityError(f"build policy path is missing: {relative}") from exc
        if (
            path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or not resolved.is_relative_to(root)
        ):
            raise RuntimeIntegrityError(f"build policy path is unsafe: {relative}")
        records.append({"path": relative, "sha256": _sha256_file(path)})
    return records


def build_policy_digest(source_root: Path) -> str:
    encoded = json.dumps(
        build_policy_records(source_root),
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def require_build_policy(source_root: Path, expected: str) -> str:
    if SHA256_PATTERN.fullmatch(expected) is None:
        raise RuntimeIntegrityError("expected build policy digest is invalid")
    actual = build_policy_digest(source_root)
    if actual != expected:
        raise RuntimeIntegrityError("reviewed build policy digest does not match")
    return actual


def _atomic_write(path: Path, value: Mapping[str, Any], *, mode: int) -> None:
    try:
        parent_metadata = path.parent.lstat()
        if path.parent.is_symlink() or not stat.S_ISDIR(parent_metadata.st_mode):
            raise RuntimeIntegrityError("integrity evidence directory is unsafe")
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise RuntimeIntegrityError("integrity evidence destination is unsafe")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
    except OSError as exc:
        raise RuntimeIntegrityError("integrity evidence cannot be staged") from exc
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = -1
            handle.write(_canonical_json(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise


def create_bootstrap_config(
    *,
    source_root: Path,
    deploy_user: str,
    deploy_uid: int,
    deploy_gid: int,
) -> dict[str, Any]:
    if DEPLOY_USER_PATTERN.fullmatch(deploy_user) is None:
        raise RuntimeIntegrityError("bootstrap deploy user is invalid")
    if type(deploy_uid) is not int or deploy_uid <= 0:
        raise RuntimeIntegrityError("bootstrap deploy uid is invalid")
    if type(deploy_gid) is not int or deploy_gid < 0:
        raise RuntimeIntegrityError("bootstrap deploy gid is invalid")
    root = _safe_source_root(source_root)
    if root != Path("/opt/mooncen"):
        raise RuntimeIntegrityError("bootstrap source root must be /opt/mooncen")
    return {
        "schema_version": SCHEMA_VERSION,
        "source_root": str(root),
        "build_policy_sha256": build_policy_digest(root),
        "deploy_user": deploy_user,
        "deploy_uid": deploy_uid,
        "deploy_gid": deploy_gid,
    }


def _read_private_json(path: Path, *, trusted_uid: int) -> dict[str, Any]:
    try:
        metadata = path.lstat()
        raw = path.read_bytes()
    except OSError as exc:
        raise RuntimeIntegrityError(f"private integrity evidence is unavailable: {path.name}") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != trusted_uid
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or len(raw) > 256 * 1024
    ):
        raise RuntimeIntegrityError(f"private integrity evidence is unsafe: {path.name}")
    try:
        value = json.loads(raw.decode("ascii"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeIntegrityError(f"private integrity evidence is invalid: {path.name}") from exc
    if not isinstance(value, dict) or raw != _canonical_json(value):
        raise RuntimeIntegrityError(f"private integrity evidence is not canonical: {path.name}")
    return value


def load_bootstrap_config(
    path: Path = BOOTSTRAP_CONFIG_PATH, *, trusted_uid: int = 0
) -> dict[str, Any]:
    value = _read_private_json(path, trusted_uid=trusted_uid)
    if frozenset(value) != BOOTSTRAP_CONFIG_KEYS or value.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeIntegrityError("bootstrap configuration fields are invalid")
    if value.get("source_root") != "/opt/mooncen":
        raise RuntimeIntegrityError("bootstrap source root is invalid")
    digest = value.get("build_policy_sha256")
    user = value.get("deploy_user")
    uid = value.get("deploy_uid")
    gid = value.get("deploy_gid")
    if not isinstance(digest, str) or SHA256_PATTERN.fullmatch(digest) is None:
        raise RuntimeIntegrityError("bootstrap build policy digest is invalid")
    if not isinstance(user, str) or DEPLOY_USER_PATTERN.fullmatch(user) is None:
        raise RuntimeIntegrityError("bootstrap deploy user is invalid")
    if type(uid) is not int or uid <= 0 or type(gid) is not int or gid < 0:
        raise RuntimeIntegrityError("bootstrap deploy identity is invalid")
    return dict(value)


def _installed_file_digests(*, trusted_uid: int) -> dict[str, str]:
    result: dict[str, str] = {}
    for label, path, expected_mode in INSTALLED_RUNTIME_FILES:
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise RuntimeIntegrityError(f"installed runtime file is missing: {label}") from exc
        if (
            path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != trusted_uid
            or stat.S_IMODE(metadata.st_mode) != expected_mode
        ):
            raise RuntimeIntegrityError(f"installed runtime file is unsafe: {label}")
        result[label] = _sha256_file(path)
    return result


def create_installation_receipt(
    *, source_root: Path, expected_build_policy: str, trusted_uid: int = 0
) -> dict[str, Any]:
    require_build_policy(source_root, expected_build_policy)
    installed_at = datetime.now(timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "build_policy_sha256": expected_build_policy,
        "installed_files": _installed_file_digests(trusted_uid=trusted_uid),
        "installed_at": installed_at,
    }


def validate_installed_runtime(
    expected_build_policy: str | None,
    *,
    receipt_path: Path = INSTALLATION_RECEIPT_PATH,
    trusted_uid: int = 0,
) -> dict[str, Any]:
    value = _read_private_json(receipt_path, trusted_uid=trusted_uid)
    if (
        frozenset(value) != INSTALLATION_RECEIPT_KEYS
        or value.get("schema_version") != SCHEMA_VERSION
    ):
        raise RuntimeIntegrityError("installation receipt fields are invalid")
    digest = value.get("build_policy_sha256")
    installed_at = value.get("installed_at")
    files = value.get("installed_files")
    expected_labels = {label for label, _, _ in INSTALLED_RUNTIME_FILES}
    if not isinstance(digest, str) or SHA256_PATTERN.fullmatch(digest) is None:
        raise RuntimeIntegrityError("installed controller policy digest is invalid")
    if expected_build_policy is not None and digest != expected_build_policy:
        raise RuntimeIntegrityError("installed controller policy does not match the release")
    if not isinstance(installed_at, str) or UTC_TIMESTAMP_PATTERN.fullmatch(installed_at) is None:
        raise RuntimeIntegrityError("installation receipt timestamp is invalid")
    if not isinstance(files, dict) or set(files) != expected_labels:
        raise RuntimeIntegrityError("installation receipt file inventory is invalid")
    for label, recorded in files.items():
        if not isinstance(recorded, str) or SHA256_PATTERN.fullmatch(recorded) is None:
            raise RuntimeIntegrityError(f"installation receipt digest is invalid: {label}")
    actual = _installed_file_digests(trusted_uid=trusted_uid)
    if files != actual:
        raise RuntimeIntegrityError("installed production controller bytes have drifted")
    return dict(value)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    digest = commands.add_parser("policy-digest")
    digest.add_argument("--source-root", type=Path, required=True)
    verify = commands.add_parser("verify-source")
    verify.add_argument("--source-root", type=Path, required=True)
    verify.add_argument("--expected", required=True)
    config = commands.add_parser("write-bootstrap-config")
    config.add_argument("--source-root", type=Path, required=True)
    config.add_argument("--deploy-user", required=True)
    config.add_argument("--deploy-uid", type=int, required=True)
    config.add_argument("--deploy-gid", type=int, required=True)
    config.add_argument("--output", type=Path, required=True)
    receipt = commands.add_parser("write-installation-receipt")
    receipt.add_argument("--source-root", type=Path, required=True)
    receipt.add_argument("--expected", required=True)
    receipt.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "policy-digest":
            print(build_policy_digest(arguments.source_root))
        elif arguments.command == "verify-source":
            print(require_build_policy(arguments.source_root, arguments.expected))
        elif arguments.command == "write-bootstrap-config":
            value = create_bootstrap_config(
                source_root=arguments.source_root,
                deploy_user=arguments.deploy_user,
                deploy_uid=arguments.deploy_uid,
                deploy_gid=arguments.deploy_gid,
            )
            _atomic_write(arguments.output, value, mode=0o600)
        else:
            value = create_installation_receipt(
                source_root=arguments.source_root,
                expected_build_policy=arguments.expected,
            )
            _atomic_write(arguments.output, value, mode=0o600)
    except (OSError, RuntimeIntegrityError) as exc:
        print(f"production runtime integrity: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
