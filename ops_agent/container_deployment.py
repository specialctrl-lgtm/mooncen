from __future__ import annotations

import grp
import hashlib
import json
import os
import pwd
import re
import stat
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import UUID

from psycopg2.extras import RealDictCursor

from deploy.docker.release_manifest import (
    ManifestError,
    bind_promotion_evidence,
    load_json_evidence,
    validate_release_manifest,
    validate_validation_receipt,
)
from deploy.docker.verify_release_bundle import (
    MAX_BUNDLE_BYTES,
    VerificationError,
    verify_release_artifacts,
)

from .crawler_worker import PROJECT_ROOT, normalized_environment
from .deployment_registry import reviewed_target


SHA256_PATTERN = re.compile(r"\A[0-9a-f]{64}\Z")
SOURCE_TREE_PATTERN = re.compile(r"\A[0-9a-f]{40}\Z")
IMAGE_ID_PATTERN = re.compile(r"\Asha256:[0-9a-f]{64}\Z")
TARGET_PATTERN = re.compile(r"\A[a-z][a-z0-9_-]{0,31}\Z")
UUID_PATTERN = re.compile(
    r"\A[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
UTC_TIMESTAMP_PATTERN = re.compile(r"\A20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
CONTAINER_PARAMETER_KEYS = frozenset(
    {
        "action",
        "approval_evidence_id",
        "current_release_digest",
        "deployment_mode",
        "native_baseline_identity",
        "expected_controller_state_sha256",
        "expected_previous_release_digest",
        "expected_runtime_generation",
        "release_digest",
        "required_agent_hostname",
        "service_type",
        "source_tree",
        "target",
        "target_environment",
        "target_identity",
        "target_runtime_kind",
        "validation_receipt_digest",
    }
)
REFERENCE_KEYS = frozenset({"release_digest", "source_tree", "image_ids"})
STATE_KEYS = frozenset(
    {
        "schema_version",
        "generation",
        "mode",
        "active",
        "previous",
        "native_fallback",
        "updated_at",
    }
)
STATUS_KEYS = frozenset(
    {"native_intent", "schema_version", "state", "transaction", "worker_lease"}
)
NATIVE_INTENT_KEYS = frozenset({"schema_version", "token"})
WORKER_LEASE_KEYS = frozenset(
    {
        "schema_version",
        "job_id",
        "claim_epoch",
        "claim_token_sha256",
        "active",
        "expires_epoch",
    }
)
TRANSACTION_KEYS = frozenset(
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
)
NATIVE_UNITS = frozenset(
    {
        "mooncen-api.service",
        "mooncen-frontend.service",
        "mooncen-ai-worker.service",
    }
)
NATIVE_CONTROL_KEYS = frozenset((*NATIVE_UNITS, "mooncen-native-runtime-condition"))
NATIVE_FALLBACK_KEYS = frozenset(
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
MAX_CONTROLLER_OUTPUT_BYTES = 256 * 1024
FIXED_TARGET_NAME = "cloud"
FIXED_TARGET_ENVIRONMENT = "production"
FIXED_EXECUTOR_HOSTNAME = "an2p"
FIXED_DEPLOY_SSH_TARGET = "cloud-container-deploy"
FIXED_STATUS_SSH_TARGET = "cloud-container-status"
FIXED_REMOTE_CONTROLLER = "/usr/local/libexec/mooncen-container-release"
FIXED_REMOTE_INGRESS_ROOT = "/var/lib/mooncen-container-ingress"
FIXED_REMOTE_INGRESS_HELPER = "/usr/local/libexec/mooncen-container-ingress"
SERVICE_ACCOUNT_BY_TRANSPORT_PROFILE = {
    "deploy": "mooncen_deployment_worker",
    "status": "mooncen_ops_api",
}
NATIVE_RELEASE_SENTINEL = "0" * 64
NULL_STATE_SHA256 = "74234e98afe7498fb5daf1f36ac2d78acc339464f950703b8c019892f982b90b"
INGRESS_FILE_NAMES = (
    "compose.production.yaml",
    "images.tar",
    "release.json",
    "validation.json",
)


class ContainerDeploymentError(ValueError):
    """Raised when queue evidence or controller output is not exact."""


@dataclass(frozen=True)
class ContainerExecutionEvidence:
    job_id: str
    agent_id: str
    lease_token: str
    lease_epoch: int
    deployment_id: str
    action: str
    target_name: str
    target_environment: str
    target_identity: str
    target_runtime_kind: str
    native_baseline_identity: str | None
    approval_id: str
    expected_runtime_generation: int
    expected_controller_state_sha256: str
    expected_active_release_digest: str | None
    expected_previous_release_digest: str | None
    release: dict[str, Any] | None
    receipt: dict[str, Any] | None
    previous_release: dict[str, Any] | None

    @property
    def source_tree(self) -> str:
        if self.release is None:
            raise ContainerDeploymentError("native target has no container source tree")
        return str(self.release["source_tree"])

    @property
    def release_digest(self) -> str:
        if self.release is None:
            raise ContainerDeploymentError("native target has no container release digest")
        return str(self.release["release_digest"])

    @property
    def previous_release_digest(self) -> str | None:
        if self.previous_release is None:
            return None
        return str(self.previous_release["release_digest"])

    @property
    def remote_job_id(self) -> str:
        return UUID(self.job_id).hex

    @property
    def remote_claim_token(self) -> str:
        return UUID(self.lease_token).hex

    @property
    def remote_claim_epoch(self) -> str:
        if not 1 <= self.lease_epoch <= 9_223_372_036_854_775_807:
            raise ContainerDeploymentError("deployment lease epoch is invalid")
        return f"{self.lease_epoch:020d}"


@dataclass(frozen=True)
class ContainerIngressUpload:
    command: tuple[str, ...]
    path: Path
    name: str
    size: int
    sha256: str
    device: int
    inode: int
    uid: int
    gid: int
    mode: int
    mtime_ns: int
    ctime_ns: int


def _required_string(
    value: Any,
    *,
    label: str,
    pattern: re.Pattern[str],
) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise ContainerDeploymentError(f"{label} is invalid")
    return value


def _exact_parameters(parameters: Any) -> dict[str, Any]:
    if not isinstance(parameters, dict) or frozenset(parameters) != CONTAINER_PARAMETER_KEYS:
        raise ContainerDeploymentError("container deployment parameters are not exact")
    action = parameters.get("action")
    if action not in {"promote", "rollback", "rollback_native"}:
        raise ContainerDeploymentError("container deployment action is invalid")
    if parameters.get("deployment_mode") != "container" or parameters.get("service_type") != "full":
        raise ContainerDeploymentError("container deployment mode is invalid")
    if parameters.get("target") != FIXED_TARGET_NAME:
        raise ContainerDeploymentError("container deployment target is not fixed to cloud")
    if parameters.get("target_environment") != FIXED_TARGET_ENVIRONMENT:
        raise ContainerDeploymentError("container deployment environment is invalid")
    if parameters.get("required_agent_hostname") != FIXED_EXECUTOR_HOSTNAME:
        raise ContainerDeploymentError("container deployment executor is not fixed to an2p")
    normalized = dict(parameters)
    normalized["approval_evidence_id"] = _required_string(
        parameters.get("approval_evidence_id"),
        label="approval evidence id",
        pattern=UUID_PATTERN,
    )
    target_runtime_kind = parameters.get("target_runtime_kind")
    native_baseline = parameters.get("native_baseline_identity")
    if action == "rollback_native":
        if target_runtime_kind != "native":
            raise ContainerDeploymentError("native rollback target kind is invalid")
        normalized["native_baseline_identity"] = _required_string(
            native_baseline, label="native baseline identity", pattern=SHA256_PATTERN
        )
        if parameters.get("release_digest") is not None or parameters.get("source_tree") is not None:
            raise ContainerDeploymentError("native rollback contains a container target release")
    else:
        if target_runtime_kind != "container" or native_baseline is not None:
            raise ContainerDeploymentError("container transition target kind is invalid")
        normalized["release_digest"] = _required_string(
            parameters.get("release_digest"), label="release digest", pattern=SHA256_PATTERN
        )
        normalized["source_tree"] = _required_string(
            parameters.get("source_tree"), label="source tree", pattern=SOURCE_TREE_PATTERN
        )
    normalized["target_identity"] = _required_string(
        parameters.get("target_identity"), label="target identity", pattern=SHA256_PATTERN
    )
    generation = parameters.get("expected_runtime_generation")
    if type(generation) is not int or not 0 <= generation <= 1_000_000_000:
        raise ContainerDeploymentError("expected runtime generation is invalid")
    normalized["expected_controller_state_sha256"] = _required_string(
        parameters.get("expected_controller_state_sha256"),
        label="expected controller state hash",
        pattern=SHA256_PATTERN,
    )
    expected_previous = parameters.get("expected_previous_release_digest")
    if expected_previous is not None:
        normalized["expected_previous_release_digest"] = _required_string(
            expected_previous,
            label="expected previous release digest",
            pattern=SHA256_PATTERN,
        )
    current = parameters.get("current_release_digest")
    if current is not None:
        normalized["current_release_digest"] = _required_string(
            current, label="current release digest", pattern=SHA256_PATTERN
        )
    receipt = parameters.get("validation_receipt_digest")
    if action == "promote":
        normalized["validation_receipt_digest"] = _required_string(
            receipt, label="validation receipt digest", pattern=SHA256_PATTERN
        )
    elif receipt is not None:
        raise ContainerDeploymentError("rollback parameters contain a validation receipt")
    if action in {"rollback", "rollback_native"} and current is None:
        raise ContainerDeploymentError("rollback parameters have no current release")
    if action == "rollback" and expected_previous != normalized["release_digest"]:
        raise ContainerDeploymentError("rollback CAS previous release is not the approved target")
    if action == "rollback_native" and generation < 1:
        raise ContainerDeploymentError("native rollback requires an active Docker generation")
    if generation == 0:
        if current is not None or expected_previous is not None:
            raise ContainerDeploymentError("native CAS contains a Docker release pointer")
    elif current is None:
        raise ContainerDeploymentError("Docker CAS has no expected active release")
    return normalized


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_container_execution_evidence(
    connection: Any,
    job: Mapping[str, Any],
    *,
    development_target_identity: str,
    require_fresh: bool = True,
) -> ContainerExecutionEvidence:
    """Re-read and revalidate the complete evidence tuple after queue lease."""

    parameters = _exact_parameters(job.get("parameters"))
    configured_development_identity = _required_string(
        development_target_identity,
        label="configured development target identity",
        pattern=SHA256_PATTERN,
    )
    job_id = _required_string(str(job.get("id") or "").lower(), label="job id", pattern=UUID_PATTERN)
    agent_id = _required_string(
        str(job.get("agent_id") or "").lower(),
        label="deployment agent id",
        pattern=UUID_PATTERN,
    )
    lease_token = _required_string(
        str(job.get("lease_token") or "").lower(),
        label="deployment lease token",
        pattern=UUID_PATTERN,
    )
    lease_epoch = job.get("lease_epoch")
    if type(lease_epoch) is not int or not 1 <= lease_epoch <= 9_223_372_036_854_775_807:
        raise ContainerDeploymentError("deployment lease epoch is invalid")
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute(
            """
            SELECT deployment.id::text AS deployment_id,
                   deployment.deployment_action,
                   deployment.target_environment,
                   deployment.target_name,
                   deployment.target_identity,
                   deployment.target_runtime_kind,
                   deployment.native_baseline_identity,
                   deployment.expected_runtime_generation,
                   deployment.expected_controller_state_sha256,
                   deployment.expected_previous_release_digest,
                   deployment.approval_evidence_id::text,
                   deployment.container_release_id::text,
                   deployment.container_release_digest,
                   deployment.previous_container_release_id::text,
                   deployment.previous_container_release_digest,
                   deployment.validation_receipt_id::text,
                   deployment.validation_receipt_digest,
                   deployment.api_image_digest,
                   deployment.frontend_image_digest,
                   deployment.bundle_sha256,
                   queued_job.agent_id::text AS job_agent_id,
                   queued_job.lease_token::text AS job_lease_token,
                   queued_job.lease_epoch AS job_lease_epoch,
                   queued_job.leased_until > CURRENT_TIMESTAMP AS job_lease_fresh,
                   release.manifest_json,
                   receipt.receipt_json,
                   approval.action AS approval_action,
                   approval.target_environment AS approval_target_environment,
                   approval.target_name AS approval_target_name,
                   approval.target_identity AS approval_target_identity,
                   approval.target_runtime_kind AS approval_target_runtime_kind,
                   approval.native_baseline_identity AS approval_native_baseline_identity,
                   approval.expected_runtime_generation AS approval_expected_runtime_generation,
                   approval.expected_controller_state_sha256 AS approval_expected_controller_state_sha256,
                   approval.expected_previous_release_digest AS approval_expected_previous_release_digest,
                   approval.release_id::text AS approval_release_id,
                   approval.release_digest AS approval_release_digest,
                   approval.current_release_id::text AS approval_current_release_id,
                   approval.current_release_digest AS approval_current_release_digest,
                   approval.validation_receipt_id::text AS approval_receipt_id,
                   approval.validation_receipt_digest AS approval_receipt_digest,
                   approval.expires_at > CURRENT_TIMESTAMP AS approval_fresh,
                   receipt.expires_at > CURRENT_TIMESTAMP AS receipt_fresh,
                   previous_release.manifest_json AS previous_manifest_json,
                   latest.container_release_id::text AS latest_release_id,
                   latest.container_release_digest AS latest_release_digest,
                   (
                       SELECT count(*)
                       FROM ops_deployments consumed
                       WHERE consumed.approval_evidence_id = approval.id
                   ) AS approval_consumption_count,
                   (
                       SELECT count(*)
                       FROM ops_jobs conflicting
                       WHERE conflicting.id <> queued_job.id
                         AND conflicting.environment = queued_job.environment
                         AND conflicting.target_key = queued_job.target_key
                         AND conflicting.job_type = 'deployment'
                         AND COALESCE(
                             conflicting.parameters->>'deployment_mode', 'native'
                         ) <> 'container'
                         AND conflicting.status IN ('queued', 'assigned', 'running')
                   ) AS conflicting_native_job_count
            FROM ops_deployments deployment
            JOIN ops_jobs queued_job ON queued_job.id = deployment.job_id
            LEFT JOIN ops_container_releases release
              ON release.id = deployment.container_release_id
            LEFT JOIN ops_container_releases previous_release
              ON previous_release.id = deployment.previous_container_release_id
            LEFT JOIN ops_container_validation_receipts receipt
              ON receipt.id = deployment.validation_receipt_id
            JOIN ops_container_approval_evidence approval
              ON approval.id = deployment.approval_evidence_id
            LEFT JOIN LATERAL (
                SELECT prior.container_release_id, prior.container_release_digest
                FROM ops_deployments prior
                WHERE prior.environment = deployment.environment
                  AND prior.target_environment = deployment.target_environment
                  AND prior.target_name = deployment.target_name
                  AND prior.target_identity = deployment.target_identity
                  AND prior.deployment_mode = 'container'
                  AND prior.deployment_status = 'success'
                  AND prior.id <> deployment.id
                ORDER BY COALESCE(prior.finished_at, prior.created_at) DESC,
                         prior.id DESC
                LIMIT 1
            ) latest ON true
            WHERE deployment.job_id = %s
              AND deployment.deployment_mode = 'container'
              AND deployment.deployment_status = 'running'
              AND queued_job.status = 'running'
              AND queued_job.environment = %s
              AND queued_job.agent_id = %s::uuid
              AND queued_job.lease_token = %s::uuid
              AND queued_job.lease_epoch = %s
              AND queued_job.leased_until > CURRENT_TIMESTAMP
            FOR SHARE OF deployment, queued_job, approval
            """,
            (
                job_id,
                normalized_environment(),
                agent_id,
                lease_token,
                lease_epoch,
            ),
        )
        row = cursor.fetchone()
    connection.commit()
    if row is None:
        raise ContainerDeploymentError("leased container deployment evidence is unavailable")
    evidence = dict(row)

    exact_pairs = {
        "job_agent_id": agent_id,
        "job_lease_token": lease_token,
        "job_lease_epoch": lease_epoch,
        "deployment_action": parameters["action"],
        "target_environment": parameters["target_environment"],
        "target_name": parameters["target"],
        "target_identity": parameters["target_identity"],
        "target_runtime_kind": parameters["target_runtime_kind"],
        "native_baseline_identity": parameters["native_baseline_identity"],
        "expected_runtime_generation": parameters["expected_runtime_generation"],
        "expected_controller_state_sha256": parameters["expected_controller_state_sha256"],
        "expected_previous_release_digest": parameters["expected_previous_release_digest"],
        "approval_evidence_id": parameters["approval_evidence_id"],
        "container_release_digest": parameters["release_digest"],
        "previous_container_release_digest": parameters["current_release_digest"],
        "validation_receipt_digest": parameters["validation_receipt_digest"],
        "approval_action": parameters["action"],
        "approval_target_environment": parameters["target_environment"],
        "approval_target_name": parameters["target"],
        "approval_target_identity": parameters["target_identity"],
        "approval_target_runtime_kind": parameters["target_runtime_kind"],
        "approval_native_baseline_identity": parameters["native_baseline_identity"],
        "approval_expected_runtime_generation": parameters["expected_runtime_generation"],
        "approval_expected_controller_state_sha256": parameters["expected_controller_state_sha256"],
        "approval_expected_previous_release_digest": parameters["expected_previous_release_digest"],
        "approval_release_id": evidence["container_release_id"],
        "approval_release_digest": parameters["release_digest"],
        "approval_current_release_id": evidence["previous_container_release_id"],
        "approval_current_release_digest": parameters["current_release_digest"],
        "approval_receipt_id": evidence["validation_receipt_id"],
        "approval_receipt_digest": parameters["validation_receipt_digest"],
        "latest_release_id": evidence["previous_container_release_id"],
        "latest_release_digest": parameters["current_release_digest"],
    }
    for field, expected in exact_pairs.items():
        if evidence.get(field) != expected:
            raise ContainerDeploymentError(f"leased evidence field changed: {field}")
    if evidence.get("job_lease_fresh") is not True:
        raise ContainerDeploymentError("deployment lease expired before evidence load")
    if require_fresh and evidence.get("approval_fresh") is not True:
        raise ContainerDeploymentError("container approval expired before execution lease")
    if int(evidence.get("approval_consumption_count") or 0) != 1:
        raise ContainerDeploymentError("container approval was not consumed exactly once")
    if int(evidence.get("conflicting_native_job_count") or 0) != 0:
        raise ContainerDeploymentError("a native deployment for the same target is active")

    release: dict[str, Any] | None = None
    if parameters["target_runtime_kind"] == "container":
        try:
            release = validate_release_manifest(evidence.get("manifest_json"))
        except ManifestError as exc:
            raise ContainerDeploymentError("release manifest failed canonical revalidation") from exc
        if (
            release["release_digest"] != parameters["release_digest"]
            or release["source_tree"] != parameters["source_tree"]
            or release["images"]["api"]["image_id"] != evidence.get("api_image_digest")
            or release["images"]["frontend"]["image_id"] != evidence.get("frontend_image_digest")
            or release["bundle_sha256"] != evidence.get("bundle_sha256")
        ):
            raise ContainerDeploymentError("release manifest differs from deployment columns")
    elif evidence.get("manifest_json") is not None:
        raise ContainerDeploymentError("native target unexpectedly references a container release")

    receipt: dict[str, Any] | None = None
    if parameters["action"] == "promote":
        assert release is not None
        if require_fresh and evidence.get("receipt_fresh") is not True:
            raise ContainerDeploymentError("validation receipt expired before execution lease")
        try:
            receipt = validate_validation_receipt(evidence.get("receipt_json"))
            bound = bind_promotion_evidence(
                release,
                receipt,
                now=_utc_now() if require_fresh else receipt["validated_at"],
            )
        except ManifestError as exc:
            raise ContainerDeploymentError("validation receipt failed canonical revalidation") from exc
        receipt = bound.receipt
        if (
            receipt["target"] != "an2p-dev"
            or receipt["target_identity"] != configured_development_identity
            or receipt["receipt_digest"] != parameters["validation_receipt_digest"]
        ):
            raise ContainerDeploymentError("validation receipt target identity is not exact")
    elif evidence.get("receipt_json") is not None:
        raise ContainerDeploymentError("rollback unexpectedly references validation evidence")

    previous_release: dict[str, Any] | None = None
    if evidence.get("previous_manifest_json") is not None:
        try:
            previous_release = validate_release_manifest(evidence["previous_manifest_json"])
        except ManifestError as exc:
            raise ContainerDeploymentError("previous release failed canonical revalidation") from exc
        if previous_release["release_digest"] != parameters["current_release_digest"]:
            raise ContainerDeploymentError("previous release pointer changed before execution")
    elif parameters["current_release_digest"] is not None:
        raise ContainerDeploymentError("current release evidence is missing")

    target = reviewed_target(parameters["target"], PROJECT_ROOT)
    if (
        normalized_environment() != FIXED_TARGET_ENVIRONMENT
        or target.name != FIXED_TARGET_NAME
        or target.environment != FIXED_TARGET_ENVIRONMENT
        or target.deploy_profile != "full-stack"
        or target.identity != parameters["target_identity"]
    ):
        raise ContainerDeploymentError("reviewed production target changed before lease")

    return ContainerExecutionEvidence(
        job_id=job_id,
        agent_id=agent_id,
        lease_token=lease_token,
        lease_epoch=lease_epoch,
        deployment_id=_required_string(
            str(evidence.get("deployment_id") or "").lower(),
            label="deployment id",
            pattern=UUID_PATTERN,
        ),
        action=str(parameters["action"]),
        target_name=str(parameters["target"]),
        target_environment=str(parameters["target_environment"]),
        target_identity=str(parameters["target_identity"]),
        target_runtime_kind=str(parameters["target_runtime_kind"]),
        native_baseline_identity=(
            None
            if parameters["native_baseline_identity"] is None
            else str(parameters["native_baseline_identity"])
        ),
        approval_id=str(parameters["approval_evidence_id"]),
        expected_runtime_generation=int(parameters["expected_runtime_generation"]),
        expected_controller_state_sha256=str(parameters["expected_controller_state_sha256"]),
        expected_active_release_digest=(
            None if parameters["current_release_digest"] is None else str(parameters["current_release_digest"])
        ),
        expected_previous_release_digest=(
            None
            if parameters["expected_previous_release_digest"] is None
            else str(parameters["expected_previous_release_digest"])
        ),
        release=release,
        receipt=receipt,
        previous_release=previous_release,
    )


def _secure_transport_file(
    path: Path,
    *,
    executable: bool = False,
    trusted_uids: frozenset[int] | None = None,
) -> Path:
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ContainerDeploymentError("pinned container SSH transport is unavailable") from exc
    mode = stat.S_IMODE(metadata.st_mode)
    private_owner_valid = bool(
        (metadata.st_uid == os.geteuid() and metadata.st_gid == os.getegid() and mode == 0o600)
        or (metadata.st_uid == 0 and metadata.st_gid == os.getegid() and mode == 0o640)
    )
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or (
            executable
            and (
                metadata.st_uid not in (trusted_uids or frozenset({os.geteuid()}))
                or mode & 0o022
            )
        )
        or (not executable and not private_owner_valid)
        or (executable and not os.access(path, os.X_OK))
    ):
        raise ContainerDeploymentError("pinned container SSH transport is unsafe")
    return resolved


def default_transport_paths(
    root: Path = PROJECT_ROOT,
    *,
    profile: str = "deploy",
) -> tuple[Path, Path, Path]:
    if profile not in {"deploy", "status"}:
        raise ContainerDeploymentError("container SSH transport profile is invalid")
    profile_root = Path("/etc/mooncen-an2p") / f"{profile}-transport"
    return (
        Path("/usr/bin/ssh"),
        profile_root / "ssh_config",
        profile_root / "id_ed25519",
    )


def container_transport_service_boundary_ready(*, profile: str) -> bool:
    """Prove the local least-privilege account and its private SSH profile."""

    expected_account = SERVICE_ACCOUNT_BY_TRANSPORT_PROFILE.get(profile)
    if expected_account is None or os.name != "posix" or os.geteuid() == 0:
        return False
    try:
        account = pwd.getpwuid(os.geteuid())
        if account.pw_name != expected_account or account.pw_gid != os.getegid():
            return False
        group_ids = {os.getegid(), *os.getgroups()}
        for privileged_group in ("docker", "lxd"):
            try:
                privileged_gid = grp.getgrnam(privileged_group).gr_gid
            except KeyError:
                continue
            if privileged_gid in group_ids:
                return False
        profile_root = Path("/etc/mooncen-an2p") / f"{profile}-transport"
        root_metadata = profile_root.lstat()
        if (
            profile_root.is_symlink()
            or not stat.S_ISDIR(root_metadata.st_mode)
            or root_metadata.st_uid != 0
            or root_metadata.st_gid != os.getegid()
            or stat.S_IMODE(root_metadata.st_mode) != 0o750
        ):
            return False
        _transport_paths(profile=profile)
        _secure_transport_file(profile_root / "known_hosts")
    except (KeyError, OSError, ContainerDeploymentError):
        return False
    return True


def container_release_directory_metadata_ready(metadata: os.stat_result) -> bool:
    """Accept immutable production evidence or a private same-user test fixture."""

    mode = stat.S_IMODE(metadata.st_mode)
    return bool(
        stat.S_ISDIR(metadata.st_mode)
        and (
            (
                metadata.st_uid == 0
                and metadata.st_gid == os.getegid()
                and mode == 0o750
            )
            or (
                metadata.st_uid == os.geteuid()
                and metadata.st_gid == os.getegid()
                and mode == 0o700
            )
        )
    )


def container_release_file_metadata_ready(metadata: os.stat_result) -> bool:
    """Require bundle bytes to be immutable to the deployment worker."""

    mode = stat.S_IMODE(metadata.st_mode)
    return bool(
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_nlink == 1
        and metadata.st_size > 0
        and (
            (
                metadata.st_uid == 0
                and metadata.st_gid == os.getegid()
                and mode == 0o640
            )
            or (
                metadata.st_uid == os.geteuid()
                and metadata.st_gid == os.getegid()
                and mode == 0o600
            )
        )
    )


def _transport_paths(
    *,
    ssh_executable: Path | None = None,
    ssh_config: Path | None = None,
    identity_file: Path | None = None,
    validate_files: bool = True,
    profile: str = "deploy",
) -> tuple[Path, Path, Path]:
    default_ssh, default_config, default_identity = default_transport_paths(profile=profile)
    ssh = ssh_executable or default_ssh
    config = ssh_config or default_config
    identity = identity_file or default_identity
    if validate_files:
        ssh = _secure_transport_file(
            ssh,
            executable=True,
            trusted_uids=frozenset({0, os.geteuid()}),
        )
        config = _secure_transport_file(config)
        identity = _secure_transport_file(identity)
    return ssh, config, identity


def container_execution_prerequisites_ready() -> bool:
    identity = os.getenv("OPS_CONTAINER_DEV_TARGET_IDENTITY", "").strip().lower()
    release_root = os.getenv("OPS_CONTAINER_RELEASE_ROOT", "").strip()
    if SHA256_PATTERN.fullmatch(identity) is None or not release_root:
        return False
    root = Path(release_root)
    if not root.is_absolute():
        return False
    try:
        if not container_transport_service_boundary_ready(profile="deploy"):
            return False
        metadata = root.lstat()
        resolved = root.resolve(strict=True)
    except (OSError, ContainerDeploymentError):
        return False
    return bool(
        not root.is_symlink()
        and resolved.is_dir()
        and container_release_directory_metadata_ready(metadata)
    )


def _ssh_prefix(
    *,
    ssh_executable: Path | None = None,
    ssh_config: Path | None = None,
    identity_file: Path | None = None,
    validate_files: bool = True,
    profile: str = "deploy",
    discard_stdin: bool = True,
) -> list[str]:
    ssh, config, identity = _transport_paths(
        ssh_executable=ssh_executable,
        ssh_config=ssh_config,
        identity_file=identity_file,
        validate_files=validate_files,
        profile=profile,
    )
    command = [
        str(ssh),
        "-F",
        str(config),
        "-i",
        str(identity),
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "UpdateHostKeys=no",
        "-o",
        "PreferredAuthentications=publickey",
        "-o",
        "PasswordAuthentication=no",
        "-o",
        "KbdInteractiveAuthentication=no",
        "-o",
        "NumberOfPasswordPrompts=0",
        "-o",
        "ForwardAgent=no",
        "-o",
        "ForwardX11=no",
    ]
    if discard_stdin:
        command.append("-n")
    command.append(FIXED_DEPLOY_SSH_TARGET if profile == "deploy" else FIXED_STATUS_SSH_TARGET)
    return command


def build_container_controller_command(
    evidence: ContainerExecutionEvidence,
    action: str,
    *,
    ssh_executable: Path | None = None,
    ssh_config: Path | None = None,
    identity_file: Path | None = None,
    validate_files: bool = True,
) -> list[str]:
    if action not in {
        "stage",
        "load-images",
        "preflight",
        "promote",
        "rollback",
        "rollback-native",
    }:
        raise ContainerDeploymentError("unsupported container controller action")
    if action == "rollback" and evidence.action != "rollback":
        raise ContainerDeploymentError("rollback command is not bound to rollback approval")
    if action == "rollback-native" and evidence.action != "rollback_native":
        raise ContainerDeploymentError("native rollback command is not bound to native approval")
    if action not in {"rollback", "rollback-native"} and evidence.action != "promote":
        raise ContainerDeploymentError("release ingress command is not bound to promotion approval")
    action_arguments = [action]
    if action not in {"rollback", "rollback-native"}:
        action_arguments.append(
            _required_string(
                evidence.source_tree,
                label="container source tree",
                pattern=SOURCE_TREE_PATTERN,
            )
        )
    if action in {"promote", "rollback", "rollback-native"}:
        expected_active = evidence.expected_active_release_digest or NATIVE_RELEASE_SENTINEL
        expected_previous = evidence.expected_previous_release_digest or NATIVE_RELEASE_SENTINEL
        action_arguments.extend(
            [
                f"{evidence.expected_runtime_generation:010d}",
                _required_string(
                    expected_active,
                    label="expected active release digest",
                    pattern=SHA256_PATTERN,
                ),
                _required_string(
                    expected_previous,
                    label="expected previous release digest",
                    pattern=SHA256_PATTERN,
                ),
                _required_string(
                    evidence.expected_controller_state_sha256,
                    label="expected controller state hash",
                    pattern=SHA256_PATTERN,
                ),
            ]
        )
    action_arguments.extend(
        [
            evidence.remote_job_id,
            evidence.remote_claim_epoch,
            evidence.remote_claim_token,
        ]
    )
    return [
        *_ssh_prefix(
            ssh_executable=ssh_executable,
            ssh_config=ssh_config,
            identity_file=identity_file,
            validate_files=validate_files,
        ),
        "/usr/bin/sudo",
        "-n",
        "--",
        FIXED_REMOTE_CONTROLLER,
        *action_arguments,
    ]


def build_container_worker_lease_command(
    evidence: ContainerExecutionEvidence,
    action: str,
    *,
    ssh_executable: Path | None = None,
    ssh_config: Path | None = None,
    identity_file: Path | None = None,
    validate_files: bool = True,
) -> list[str]:
    """Build the only two controller commands allowed to rotate worker authority."""

    return build_container_worker_lease_claim_command(
        job_id=evidence.job_id,
        lease_epoch=evidence.lease_epoch,
        lease_token=evidence.lease_token,
        action=action,
        ssh_executable=ssh_executable,
        ssh_config=ssh_config,
        identity_file=identity_file,
        validate_files=validate_files,
    )


def build_container_worker_lease_claim_command(
    *,
    job_id: str,
    lease_epoch: int,
    lease_token: str,
    action: str,
    ssh_executable: Path | None = None,
    ssh_config: Path | None = None,
    identity_file: Path | None = None,
    validate_files: bool = True,
) -> list[str]:
    """Build a fixed fence command before the new DB owner becomes visible."""

    if action not in {"lease-bind", "lease-release"}:
        raise ContainerDeploymentError("unsupported deployment lease action")
    try:
        remote_job_id = UUID(job_id).hex
        remote_claim_token = UUID(lease_token).hex
    except (ValueError, AttributeError) as exc:
        raise ContainerDeploymentError("deployment lease UUID is invalid") from exc
    if type(lease_epoch) is not int or not 1 <= lease_epoch <= 9_223_372_036_854_775_807:
        raise ContainerDeploymentError("deployment lease epoch is invalid")
    return [
        *_ssh_prefix(
            ssh_executable=ssh_executable,
            ssh_config=ssh_config,
            identity_file=identity_file,
            validate_files=validate_files,
        ),
        "/usr/bin/sudo",
        "-n",
        "--",
        FIXED_REMOTE_CONTROLLER,
        action,
        remote_job_id,
        f"{lease_epoch:020d}",
        remote_claim_token,
    ]


def build_container_remote_command(
    evidence: ContainerExecutionEvidence,
    *,
    ssh_executable: Path | None = None,
    ssh_config: Path | None = None,
    identity_file: Path | None = None,
    validate_files: bool = True,
) -> list[str]:
    action = "rollback-native" if evidence.action == "rollback_native" else evidence.action
    return build_container_controller_command(
        evidence,
        action,
        ssh_executable=ssh_executable,
        ssh_config=ssh_config,
        identity_file=identity_file,
        validate_files=validate_files,
    )


def build_container_status_command(
    *,
    ssh_executable: Path | None = None,
    ssh_config: Path | None = None,
    identity_file: Path | None = None,
    validate_files: bool = True,
    transport_profile: str = "status",
) -> list[str]:
    command = _ssh_prefix(
        ssh_executable=ssh_executable,
        ssh_config=ssh_config,
        identity_file=identity_file,
        validate_files=validate_files,
        profile=transport_profile,
    )
    return [
        *command,
        "/usr/bin/sudo",
        "-n",
        "--",
        FIXED_REMOTE_CONTROLLER,
        "status",
    ]


def build_container_presence_command(
    *,
    ssh_executable: Path | None = None,
    ssh_config: Path | None = None,
    identity_file: Path | None = None,
    validate_files: bool = True,
    transport_profile: str = "status",
) -> list[str]:
    return [
        *_ssh_prefix(
            ssh_executable=ssh_executable,
            ssh_config=ssh_config,
            identity_file=identity_file,
            validate_files=validate_files,
            profile=transport_profile,
        ),
        "/usr/bin/test",
        "-e",
        FIXED_REMOTE_CONTROLLER,
    ]


def read_container_controller_presence(
    *, timeout_seconds: int = 15, transport_profile: str = "status"
) -> bool | None:
    """Distinguish a pre-bootstrap absent controller from a corrupt controller."""

    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv and fixed path.
            build_container_presence_command(transport_profile=transport_profile),
            cwd=PROJECT_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout_seconds,
            shell=False,
            env={
                "HOME": str(Path.home()),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
            },
        )
    except (OSError, subprocess.SubprocessError, ContainerDeploymentError):
        return None
    if completed.stdout or completed.stderr:
        return None
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False
    return None


def build_container_native_intent_command(
    action: str,
    token: str,
    *,
    ssh_executable: Path | None = None,
    ssh_config: Path | None = None,
    identity_file: Path | None = None,
    validate_files: bool = True,
) -> list[str]:
    if action not in {"native-begin", "native-end"}:
        raise ContainerDeploymentError("native deployment intent action is invalid")
    trusted_token = _required_string(
        token,
        label="native deployment intent token",
        pattern=re.compile(r"\A[0-9a-f]{32}\Z"),
    )
    return [
        *_ssh_prefix(
            ssh_executable=ssh_executable,
            ssh_config=ssh_config,
            identity_file=identity_file,
            validate_files=validate_files,
        ),
        "/usr/bin/sudo",
        "-n",
        "--",
        FIXED_REMOTE_CONTROLLER,
        action,
        trusted_token,
    ]


def container_release_files(
    evidence: ContainerExecutionEvidence,
    *,
    release_root: Path | None = None,
) -> dict[str, Path]:
    """Resolve and revalidate the fixed local release bundle for ingress."""

    configured = release_root
    if configured is None:
        value = os.getenv("OPS_CONTAINER_RELEASE_ROOT", "").strip()
        if not value:
            raise ContainerDeploymentError("OPS_CONTAINER_RELEASE_ROOT is not configured")
        configured = Path(value)
    if not configured.is_absolute():
        raise ContainerDeploymentError("container release root must be absolute")
    try:
        root_metadata = configured.lstat()
        root = configured.resolve(strict=True)
    except OSError as exc:
        raise ContainerDeploymentError("container release root is unavailable") from exc
    if (
        configured.is_symlink()
        or not container_release_directory_metadata_ready(root_metadata)
    ):
        raise ContainerDeploymentError("container release root is unsafe")
    source_tree = _required_string(
        evidence.source_tree,
        label="container source tree",
        pattern=SOURCE_TREE_PATTERN,
    )
    release_path = root / source_tree
    try:
        release_metadata = release_path.lstat()
        release = release_path.resolve(strict=True)
    except OSError as exc:
        raise ContainerDeploymentError("container release directory is unavailable") from exc
    if (
        release_path.is_symlink()
        or release.parent != root
        or not container_release_directory_metadata_ready(release_metadata)
    ):
        raise ContainerDeploymentError("container release directory is unsafe")
    try:
        entries = {entry.name for entry in release.iterdir()}
    except OSError as exc:
        raise ContainerDeploymentError("container release directory cannot be listed") from exc
    if entries != set(INGRESS_FILE_NAMES):
        raise ContainerDeploymentError("container release directory contents are not exact")

    maximum_sizes = {
        "compose.production.yaml": 1024 * 1024,
        "images.tar": MAX_BUNDLE_BYTES,
        "release.json": 256 * 1024,
        "validation.json": 256 * 1024,
    }
    files: dict[str, Path] = {}
    for name in INGRESS_FILE_NAMES:
        candidate = release / name
        try:
            metadata = candidate.lstat()
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise ContainerDeploymentError(f"container ingress file is unavailable: {name}") from exc
        if (
            candidate.is_symlink()
            or resolved.parent != release
            or not container_release_file_metadata_ready(metadata)
            or metadata.st_size > maximum_sizes[name]
        ):
            raise ContainerDeploymentError(f"container ingress file is unsafe: {name}")
        files[name] = resolved
    try:
        artifact_result = verify_release_artifacts(release)
        local_release = load_json_evidence(files["release.json"])
        local_receipt = load_json_evidence(files["validation.json"], receipt=True)
        bound = bind_promotion_evidence(local_release, local_receipt, now=_utc_now())
    except (ManifestError, VerificationError, OSError) as exc:
        raise ContainerDeploymentError("local container release failed canonical verification") from exc
    if (
        artifact_result.get("release_digest") != evidence.release_digest
        or artifact_result.get("source_tree") != evidence.source_tree
        or local_release != evidence.release
        or evidence.receipt is None
        or bound.receipt != evidence.receipt
    ):
        raise ContainerDeploymentError("local release bundle differs from leased database evidence")
    return files


def _container_ingress_path(evidence: ContainerExecutionEvidence) -> str:
    tree = _required_string(
        evidence.source_tree,
        label="container source tree",
        pattern=SOURCE_TREE_PATTERN,
    )
    return f"{FIXED_REMOTE_INGRESS_ROOT}/{tree}"


def build_container_ingress_commands(
    evidence: ContainerExecutionEvidence,
    *,
    release_files: Mapping[str, Path],
    ssh_executable: Path | None = None,
    ssh_config: Path | None = None,
    identity_file: Path | None = None,
    validate_files: bool = True,
) -> dict[str, Any]:
    """Build a fixed stdin-upload plan without SCP or remote shell paths."""

    if evidence.action != "promote":
        raise ContainerDeploymentError("container ingress is allowed only for promotion")
    if frozenset(release_files) != frozenset(INGRESS_FILE_NAMES):
        raise ContainerDeploymentError("container ingress file mapping is not exact")
    destination = _container_ingress_path(evidence)
    source_tree = evidence.source_tree
    ssh, config, identity = _transport_paths(
        ssh_executable=ssh_executable,
        ssh_config=ssh_config,
        identity_file=identity_file,
        validate_files=validate_files,
        profile="deploy",
    )
    ssh_prefix = _ssh_prefix(
        ssh_executable=ssh,
        ssh_config=config,
        identity_file=identity,
        validate_files=False,
        profile="deploy",
    )
    upload_prefix = _ssh_prefix(
        ssh_executable=ssh,
        ssh_config=config,
        identity_file=identity,
        validate_files=False,
        profile="deploy",
        discard_stdin=False,
    )
    uploads: list[ContainerIngressUpload] = []
    for name in INGRESS_FILE_NAMES:
        local = release_files[name]
        if not isinstance(local, Path) or local.name != name:
            raise ContainerDeploymentError("container ingress local file mapping changed")
        try:
            descriptor = os.open(local, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                before = os.fstat(descriptor)
                digest = hashlib.sha256()
                while True:
                    chunk = os.read(descriptor, 1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                after = os.fstat(descriptor)
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise ContainerDeploymentError("container ingress file cannot be pinned") from exc
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_uid != after.st_uid
            or before.st_gid != after.st_gid
            or stat.S_IMODE(before.st_mode) != stat.S_IMODE(after.st_mode)
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
            or not container_release_file_metadata_ready(before)
        ):
            raise ContainerDeploymentError("container ingress file changed while pinning")
        file_sha256 = digest.hexdigest()
        command = (
            *upload_prefix,
            FIXED_REMOTE_INGRESS_HELPER,
            "upload",
            source_tree,
            name,
            str(before.st_size),
            file_sha256,
        )
        uploads.append(
            ContainerIngressUpload(
                command=command,
                path=local,
                name=name,
                size=before.st_size,
                sha256=file_sha256,
                device=before.st_dev,
                inode=before.st_ino,
                uid=before.st_uid,
                gid=before.st_gid,
                mode=stat.S_IMODE(before.st_mode),
                mtime_ns=before.st_mtime_ns,
                ctime_ns=before.st_ctime_ns,
            )
        )
    return {
        "destination": destination,
        "prepare": [
            *ssh_prefix,
            FIXED_REMOTE_INGRESS_HELPER,
            "prepare",
            source_tree,
        ],
        "uploads": uploads,
        "abort": [
            *ssh_prefix,
            FIXED_REMOTE_INGRESS_HELPER,
            "abort",
            source_tree,
        ],
    }


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ContainerDeploymentError("controller JSON contains a duplicate field")
        value[key] = item
    return value


def _parse_canonical_json(line: str) -> Any:
    if (
        not line
        or len(line.encode("utf-8")) > MAX_CONTROLLER_OUTPUT_BYTES
        or "\x00" in line
        or "\r" in line
        or "\n" in line
    ):
        raise ContainerDeploymentError("controller output is not one bounded JSON line")
    try:
        value = json.loads(line, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ContainerDeploymentError("controller output is invalid JSON") from exc
    try:
        canonical = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ContainerDeploymentError("controller output is not canonical JSON") from exc
    if canonical != line:
        raise ContainerDeploymentError("controller output bytes are not canonical")
    return value


def parse_container_ingress_result(
    line: str,
    evidence: ContainerExecutionEvidence,
    action: str,
    *,
    upload: ContainerIngressUpload | None = None,
) -> dict[str, Any]:
    """Bind one forced ingress-helper response to the leased source tree."""

    value = _parse_canonical_json(line)
    if action == "prepare" and upload is None:
        expected = {
            "prepared": True,
            "schema_version": 1,
            "source_tree": evidence.source_tree,
        }
    elif action == "upload" and upload is not None:
        expected = {
            "name": upload.name,
            "schema_version": 1,
            "sha256": upload.sha256,
            "size": upload.size,
            "source_tree": evidence.source_tree,
            "uploaded": True,
        }
    elif action == "abort" and upload is None:
        expected = {
            "aborted": True,
            "schema_version": 1,
            "source_tree": evidence.source_tree,
        }
    else:
        raise ContainerDeploymentError("container ingress result action is invalid")
    if value != expected:
        raise ContainerDeploymentError("container ingress result differs from the pinned upload")
    return expected


def _validate_native_snapshot(value: Any) -> dict[str, dict[str, bool]]:
    if not isinstance(value, dict) or frozenset(value) != NATIVE_UNITS:
        raise ContainerDeploymentError("controller native snapshot is invalid")
    normalized: dict[str, dict[str, bool]] = {}
    for unit in sorted(NATIVE_UNITS):
        state = value[unit]
        if (
            not isinstance(state, dict)
            or frozenset(state) != {"active", "enabled"}
            or type(state.get("active")) is not bool
            or type(state.get("enabled")) is not bool
        ):
            raise ContainerDeploymentError("controller native unit state is invalid")
        normalized[unit] = dict(state)
    return normalized


def _validate_native_fallback(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or frozenset(value) != NATIVE_FALLBACK_KEYS:
        raise ContainerDeploymentError("controller native fallback fields are invalid")
    control = value.get("control_sha256")
    if not isinstance(control, dict) or frozenset(control) != NATIVE_CONTROL_KEYS:
        raise ContainerDeploymentError("controller native control digest set is invalid")
    normalized = {
        "schema_version": 1,
        "identity": _required_string(
            value.get("identity"), label="native fallback identity", pattern=SHA256_PATTERN
        ),
        "deploy_commit": _required_string(
            value.get("deploy_commit"), label="native deploy commit", pattern=SOURCE_TREE_PATTERN
        ),
        "deploy_archive_sha256": _required_string(
            value.get("deploy_archive_sha256"), label="native archive digest", pattern=SHA256_PATTERN
        ),
        "deploy_info_sha256": _required_string(
            value.get("deploy_info_sha256"), label="native deploy-info digest", pattern=SHA256_PATTERN
        ),
        "prebuild_sha256": _required_string(
            value.get("prebuild_sha256"), label="native prebuild digest", pattern=SHA256_PATTERN
        ),
        "runtime_tree_sha256": _required_string(
            value.get("runtime_tree_sha256"), label="native runtime tree digest", pattern=SHA256_PATTERN
        ),
        "control_sha256": {
            key: _required_string(
                control[key], label=f"native control digest {key}", pattern=SHA256_PATTERN
            )
            for key in sorted(NATIVE_CONTROL_KEYS)
        },
        "units": _validate_native_snapshot(value.get("units")),
    }
    if value.get("schema_version") != 1:
        raise ContainerDeploymentError("controller native fallback schema is invalid")
    identity_bound = {key: normalized[key] for key in NATIVE_FALLBACK_KEYS if key != "identity"}
    canonical = json.dumps(
        identity_bound,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if hashlib.sha256(canonical.encode("ascii")).hexdigest() != normalized["identity"]:
        raise ContainerDeploymentError("controller native fallback identity is invalid")
    return normalized


def _validate_reference(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or frozenset(value) != REFERENCE_KEYS:
        raise ContainerDeploymentError("controller release reference is invalid")
    image_ids = value.get("image_ids")
    if not isinstance(image_ids, dict) or frozenset(image_ids) != {"api", "frontend"}:
        raise ContainerDeploymentError("controller image IDs are invalid")
    normalized_images = {
        service: _required_string(image_ids[service], label=f"controller {service} image", pattern=IMAGE_ID_PATTERN)
        for service in ("api", "frontend")
    }
    if normalized_images["api"] == normalized_images["frontend"]:
        raise ContainerDeploymentError("controller image IDs are not distinct")
    return {
        "release_digest": _required_string(
            value.get("release_digest"), label="controller release digest", pattern=SHA256_PATTERN
        ),
        "source_tree": _required_string(
            value.get("source_tree"), label="controller source tree", pattern=SOURCE_TREE_PATTERN
        ),
        "image_ids": normalized_images,
    }


def _validate_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or frozenset(value) != STATE_KEYS:
        raise ContainerDeploymentError("controller state fields are invalid")
    generation = value.get("generation")
    if (
        value.get("schema_version") != 1
        or value.get("mode") != "docker"
        or type(generation) is not int
        or not 1 <= generation <= 1_000_000_000
    ):
        raise ContainerDeploymentError("controller state schema is invalid")
    previous = value.get("previous")
    return {
        "schema_version": 1,
        "generation": generation,
        "mode": "docker",
        "active": _validate_reference(value.get("active")),
        "previous": None if previous is None else _validate_reference(previous),
        "native_fallback": _validate_native_fallback(value.get("native_fallback")),
        "updated_at": _required_string(
            value.get("updated_at"), label="controller state timestamp", pattern=UTC_TIMESTAMP_PATTERN
        ),
    }


def _expected_reference(release: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "release_digest": release["release_digest"],
        "source_tree": release["source_tree"],
        "image_ids": {service: release["images"][service]["image_id"] for service in ("api", "frontend")},
    }


def parse_container_pipeline_step_result(
    line: str,
    evidence: ContainerExecutionEvidence,
    action: str,
) -> dict[str, Any]:
    """Parse one canonical non-cutover controller result and bind it to DB evidence."""

    value = _parse_canonical_json(line)
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ContainerDeploymentError("container pipeline step result schema is invalid")
    if evidence.release is None:
        raise ContainerDeploymentError("native transition has no release pipeline result")
    reference = _expected_reference(evidence.release)
    common = {
        "release_digest": reference["release_digest"],
        "source_tree": reference["source_tree"],
        "image_ids": reference["image_ids"],
    }
    if action == "stage":
        expected = {
            "schema_version": 1,
            "staged": True,
            **common,
            "bundle_sha256": evidence.release["bundle_sha256"],
            "compose_sha256": evidence.release["compose_sha256"],
        }
    elif action == "load-images":
        expected = {"schema_version": 1, "images_loaded": True, **common}
    elif action == "preflight":
        expected = {
            "schema_version": 1,
            "preflight": "passed",
            **common,
            "migration_ledger_sha256": evidence.release["migration_ledger_sha256"],
        }
    else:
        raise ContainerDeploymentError("unsupported container pipeline result action")
    if value != expected:
        raise ContainerDeploymentError(f"container {action} result differs from leased release evidence")
    return dict(value)


def parse_container_action_result(
    line: str,
    evidence: ContainerExecutionEvidence,
) -> dict[str, Any]:
    parsed = _parse_canonical_json(line)
    if evidence.action == "rollback_native":
        if parsed is not None or evidence.native_baseline_identity is None:
            raise ContainerDeploymentError("native rollback result is not the approved native state")
        return {
            "runtime_generation": 0,
            "activated_release_digest": None,
            "runtime_previous_release_digest": None,
            "controller_state_sha256": NULL_STATE_SHA256,
            "runtime_target_kind": "native",
            "runtime_native_baseline_identity": evidence.native_baseline_identity,
        }
    if evidence.release is None:
        raise ContainerDeploymentError("container transition result has no target release")
    state = _validate_state(parsed)
    expected_active = _expected_reference(evidence.release)
    expected_previous = None if evidence.previous_release is None else _expected_reference(evidence.previous_release)
    if (
        state["generation"] != evidence.expected_runtime_generation + 1
        or state["active"] != expected_active
        or state["previous"] != expected_previous
    ):
        raise ContainerDeploymentError("controller result differs from approved transition")
    return {
        "runtime_generation": state["generation"],
        "activated_release_digest": state["active"]["release_digest"],
        "runtime_previous_release_digest": (state["previous"]["release_digest"] if state["previous"] else None),
        "controller_state_sha256": hashlib.sha256(line.encode("ascii")).hexdigest(),
        "runtime_target_kind": "container",
        "runtime_native_baseline_identity": state["native_fallback"]["identity"],
    }


def _validate_transaction(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or frozenset(value) != TRANSACTION_KEYS:
        raise ContainerDeploymentError("controller transaction fields are invalid")
    if (
        value.get("schema_version") != 1
        or value.get("operation") not in {"promote", "rollback", "rollback_native"}
        or value.get("phase") not in TRANSACTION_PHASES
        or type(value.get("candidate_started")) is not bool
        or type(value.get("cutover_started")) is not bool
    ):
        raise ContainerDeploymentError("controller transaction schema is invalid")
    for field in (
        "owner_pid",
        "owner_start_ticks",
        "created_epoch",
        "updated_epoch",
        "deadline_epoch",
    ):
        if type(value.get(field)) is not int or value[field] <= 0:
            raise ContainerDeploymentError("controller transaction counter is invalid")
    _required_string(value.get("token"), label="controller token", pattern=re.compile(r"\A[0-9a-f]{32}\Z"))
    _required_string(
        value.get("owner_boot_id"),
        label="controller boot id",
        pattern=UUID_PATTERN,
    )
    if value.get("target") is not None:
        _validate_reference(value["target"])
    if value.get("previous_state") is not None:
        _validate_state(value["previous_state"])
    _validate_native_fallback(value.get("native_snapshot"))
    return dict(value)


def _validate_worker_lease(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or frozenset(value) != WORKER_LEASE_KEYS:
        raise ContainerDeploymentError("controller worker lease fields are invalid")
    claim_epoch = value.get("claim_epoch")
    expires_epoch = value.get("expires_epoch")
    if (
        value.get("schema_version") != 1
        or type(claim_epoch) is not int
        or not 1 <= claim_epoch <= 9_223_372_036_854_775_807
        or type(expires_epoch) is not int
        or expires_epoch <= 0
        or type(value.get("active")) is not bool
    ):
        raise ContainerDeploymentError("controller worker lease values are invalid")
    return {
        "schema_version": 1,
        "job_id": _required_string(
            value.get("job_id"),
            label="controller worker job id",
            pattern=re.compile(r"\A[0-9a-f]{32}\Z"),
        ),
        "claim_epoch": claim_epoch,
        "claim_token_sha256": _required_string(
            value.get("claim_token_sha256"),
            label="controller worker claim token digest",
            pattern=SHA256_PATTERN,
        ),
        "active": value["active"],
        "expires_epoch": expires_epoch,
    }


def _assert_worker_lease_identity(
    lease: Mapping[str, Any],
    *,
    job_id: str,
    lease_epoch: int,
    lease_token: str,
    active: bool,
) -> dict[str, Any]:
    try:
        remote_job_id = UUID(job_id).hex
        remote_claim_token = UUID(lease_token).hex
    except (ValueError, AttributeError) as exc:
        raise ContainerDeploymentError("deployment lease UUID is invalid") from exc
    if type(lease_epoch) is not int or not 1 <= lease_epoch <= 9_223_372_036_854_775_807:
        raise ContainerDeploymentError("deployment lease epoch is invalid")
    trusted = _validate_worker_lease(lease)
    expected_digest = hashlib.sha256(remote_claim_token.encode("ascii")).hexdigest()
    if (
        trusted["job_id"] != remote_job_id
        or trusted["claim_epoch"] != lease_epoch
        or trusted["claim_token_sha256"] != expected_digest
        or trusted["active"] is not active
    ):
        raise ContainerDeploymentError("controller worker lease differs from the DB claim")
    return trusted


def _assert_evidence_worker_lease_identity(
    lease: Mapping[str, Any],
    evidence: ContainerExecutionEvidence,
    *,
    active: bool,
) -> dict[str, Any]:
    return _assert_worker_lease_identity(
        lease,
        job_id=evidence.job_id,
        lease_epoch=evidence.lease_epoch,
        lease_token=evidence.lease_token,
        active=active,
    )


def parse_container_worker_lease_result(
    line: str,
    evidence: ContainerExecutionEvidence,
    *,
    active: bool,
) -> dict[str, Any]:
    value = _parse_canonical_json(line)
    if not isinstance(value, dict):
        raise ContainerDeploymentError("controller worker lease result is invalid")
    return _assert_evidence_worker_lease_identity(value, evidence, active=active)


def parse_container_worker_lease_claim_result(
    line: str,
    *,
    job_id: str,
    lease_epoch: int,
    lease_token: str,
    active: bool,
) -> dict[str, Any]:
    value = _parse_canonical_json(line)
    if not isinstance(value, dict):
        raise ContainerDeploymentError("controller worker lease result is invalid")
    return _assert_worker_lease_identity(
        value,
        job_id=job_id,
        lease_epoch=lease_epoch,
        lease_token=lease_token,
        active=active,
    )


def assert_container_worker_lease(
    status_value: Mapping[str, Any],
    evidence: ContainerExecutionEvidence,
    *,
    active: bool = True,
) -> dict[str, Any]:
    lease = status_value.get("worker_lease")
    if not isinstance(lease, Mapping):
        raise ContainerDeploymentError("controller worker lease is absent")
    return _assert_evidence_worker_lease_identity(lease, evidence, active=active)


def assert_container_worker_lease_claim(
    status_value: Mapping[str, Any],
    *,
    job_id: str,
    lease_epoch: int,
    lease_token: str,
    active: bool = True,
) -> dict[str, Any]:
    lease = status_value.get("worker_lease")
    if not isinstance(lease, Mapping):
        raise ContainerDeploymentError("controller worker lease is absent")
    return _assert_worker_lease_identity(
        lease,
        job_id=job_id,
        lease_epoch=lease_epoch,
        lease_token=lease_token,
        active=active,
    )


def parse_container_status(line: str) -> dict[str, Any]:
    value = _parse_canonical_json(line)
    if not isinstance(value, dict) or frozenset(value) != STATUS_KEYS:
        raise ContainerDeploymentError("controller status fields are invalid")
    if value.get("schema_version") != 1:
        raise ContainerDeploymentError("controller status schema is invalid")
    state = None if value.get("state") is None else _validate_state(value["state"])
    transaction = None if value.get("transaction") is None else _validate_transaction(value["transaction"])
    worker_lease_value = value.get("worker_lease")
    worker_lease = (
        None
        if worker_lease_value is None
        else _validate_worker_lease(worker_lease_value)
    )
    native_intent_value = value.get("native_intent")
    native_intent = None
    if native_intent_value is not None:
        if (
            not isinstance(native_intent_value, dict)
            or frozenset(native_intent_value) != NATIVE_INTENT_KEYS
            or native_intent_value.get("schema_version") != 1
        ):
            raise ContainerDeploymentError("native deployment intent fields are invalid")
        native_intent = {
            "schema_version": 1,
            "token": _required_string(
                native_intent_value.get("token"),
                label="native deployment intent token",
                pattern=re.compile(r"\A[0-9a-f]{32}\Z"),
            ),
        }
    return {
        "schema_version": 1,
        "native_intent": native_intent,
        "state": state,
        "transaction": transaction,
        "worker_lease": worker_lease,
    }


def container_runtime_cas(status_value: Mapping[str, Any]) -> dict[str, Any]:
    """Derive the immutable CAS tuple from one strictly parsed controller status."""

    if (
        not isinstance(status_value, dict)
        or frozenset(status_value) != STATUS_KEYS
        or status_value.get("schema_version") != 1
    ):
        raise ContainerDeploymentError("controller status is unavailable for CAS")
    if status_value.get("transaction") is not None:
        raise ContainerDeploymentError("an unfinished controller transaction blocks approval")
    if status_value.get("native_intent") is not None:
        raise ContainerDeploymentError("an active native deployment intent blocks approval")
    raw_state = status_value.get("state")
    state = None if raw_state is None else _validate_state(raw_state)
    canonical_state = json.dumps(
        state,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "expected_runtime_generation": 0 if state is None else state["generation"],
        "expected_controller_state_sha256": hashlib.sha256(canonical_state.encode("ascii")).hexdigest(),
        "expected_active_release_digest": (None if state is None else state["active"]["release_digest"]),
        "expected_previous_release_digest": (
            None if state is None or state["previous"] is None else state["previous"]["release_digest"]
        ),
        "native_baseline_identity": (
            None if state is None else state["native_fallback"]["identity"]
        ),
        "state": state,
    }


def assert_container_runtime_cas(
    status_value: Mapping[str, Any], evidence: ContainerExecutionEvidence
) -> dict[str, Any]:
    """Reject any live runtime drift from the leased append-only approval."""

    observed = container_runtime_cas(status_value)
    expected = {
        "expected_runtime_generation": evidence.expected_runtime_generation,
        "expected_controller_state_sha256": evidence.expected_controller_state_sha256,
        "expected_active_release_digest": evidence.expected_active_release_digest,
        "expected_previous_release_digest": evidence.expected_previous_release_digest,
    }
    if any(observed[key] != value for key, value in expected.items()):
        raise ContainerDeploymentError("controller runtime changed after approval")
    if (
        evidence.action == "rollback_native"
        and observed["native_baseline_identity"] != evidence.native_baseline_identity
    ):
        raise ContainerDeploymentError("native baseline changed after approval")
    return observed


def read_container_controller_status(
    *, timeout_seconds: int = 15, transport_profile: str = "status"
) -> dict[str, Any] | None:
    """Read only the pinned cloud controller state; unavailable is never treated as native."""

    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 60:
        raise ContainerDeploymentError("container status timeout is invalid")
    try:
        command = build_container_status_command(transport_profile=transport_profile)
        completed = subprocess.run(  # noqa: S603 - command is a fixed allowlisted argv.
            command,
            cwd=PROJECT_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout_seconds,
            shell=False,
            env={
                "HOME": str(Path.home()),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
            },
        )
    except (OSError, subprocess.SubprocessError, ContainerDeploymentError):
        return None
    if (
        completed.returncode != 0
        or completed.stderr
        or not completed.stdout.endswith("\n")
        or completed.stdout.count("\n") != 1
        or "\r" in completed.stdout
    ):
        return None
    try:
        return parse_container_status(completed.stdout[:-1])
    except ContainerDeploymentError:
        return None


def reconcile_container_status(
    status_value: Mapping[str, Any],
    evidence: ContainerExecutionEvidence,
) -> str:
    if status_value.get("transaction") is not None or status_value.get("native_intent") is not None:
        return "pending"
    state = status_value.get("state")
    canonical_state = json.dumps(
        state,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    observed_state_sha256 = hashlib.sha256(canonical_state.encode("ascii")).hexdigest()
    if evidence.action == "rollback_native":
        if state is None:
            return "success"
        if observed_state_sha256 == evidence.expected_controller_state_sha256:
            return "recovered_previous"
        return "recovery_required"
    if evidence.release is None:
        return "recovery_required"
    expected_active = _expected_reference(evidence.release)
    expected_previous = None if evidence.previous_release is None else _expected_reference(evidence.previous_release)
    if (
        isinstance(state, dict)
        and state.get("generation") == evidence.expected_runtime_generation + 1
        and state.get("active") == expected_active
        and state.get("previous") == expected_previous
    ):
        return "success"
    if observed_state_sha256 == evidence.expected_controller_state_sha256:
        return "recovered_previous"
    return "recovery_required"
