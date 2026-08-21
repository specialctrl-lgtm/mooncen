"""Validated desired-state contract for distributed crawler releases.

This module deliberately contains no database or network client.  The central
control plane can build the document from its own storage, while workers parse
the exact same bounded contract.  Artifact locations are relative paths: a
control-plane response can never make a worker fetch an arbitrary URL.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any, Iterable, Mapping


DESIRED_STATE_SCHEMA_VERSION = 1
MAX_DESIRED_STATE_BYTES = 256 * 1024
MAX_ARTIFACTS = 32
MAX_WORKERS = 256
MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
MAX_SIGNATURE_BYTES = 64 * 1024

ROLLOUT_STATES = frozenset({"paused", "canary", "rolling", "complete", "rollback"})
WORKER_COHORTS = frozenset({"canary", "stable"})

# Subtask A owns migrations.  This is the runtime compatibility contract only;
# this module intentionally emits no DDL and performs no schema mutation.
EXPECTED_DATABASE_CONTRACT: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "ops_crawler_release_artifacts": (
            "artifact_digest",
            "code_version",
            "config_revision",
            "artifact_path",
            "size_bytes",
            "signature",
            "key_id",
            "metadata",
            "created_at",
        ),
        "ops_crawler_release_rollouts": (
            "id",
            "environment",
            "rollout_epoch",
            "artifact_digest",
            "previous_artifact_digest",
            "status",
            "requested_worker_count",
            "strategy",
            "requested_by",
            "created_at",
            "started_at",
            "finished_at",
        ),
        "ops_crawler_worker_desired_state": (
            "environment",
            "worker_key",
            "agent_id",
            "rollout_id",
            "generation",
            "desired_status",
            "cohort",
            "artifact_digest",
            "code_version",
            "config_revision",
            "not_before",
            "created_at",
            "updated_at",
        ),
        "ops_crawler_release_reports": (
            "id",
            "rollout_id",
            "environment",
            "worker_key",
            "agent_id",
            "desired_generation",
            "status",
            "artifact_digest",
            "code_version",
            "config_revision",
            "health",
            "error_code",
            "error_message",
            "reported_at",
            "created_at",
        ),
    }
)

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_ROLLOUT_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_CONFIG_REVISION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,127}$")
_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ArtifactMetadata:
    code_version: str
    relative_path: str
    sha256: str
    size_bytes: int
    config_revision: str
    signature: str | None = None
    key_id: str | None = None

    @property
    def signed(self) -> bool:
        return self.signature is not None


@dataclass(frozen=True)
class Rollout:
    rollout_id: str
    state: str
    target_version: str
    baseline_version: str
    canary_workers: tuple[str, ...]


@dataclass(frozen=True)
class WorkerDesiredState:
    worker_id: str
    desired_version: str
    config_revision: str
    cohort: str
    enabled: bool


@dataclass(frozen=True)
class DesiredState:
    schema_version: int
    environment: str
    generation: int
    rollout: Rollout
    artifacts: Mapping[str, ArtifactMetadata]
    workers: Mapping[str, WorkerDesiredState]


@dataclass(frozen=True)
class ReconcileDecision:
    action: str
    reason: str
    generation: int
    rollout_id: str
    desired: WorkerDesiredState
    artifact: ArtifactMetadata


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("crawler desired state contains a duplicate field")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError("crawler desired state contains a non-finite number")


def _require_object(
    value: Any,
    *,
    field: str,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError(f"{field} must be an object")
    keys = set(value)
    if required - keys:
        raise ValueError(f"{field} is missing a required field")
    if keys - required - optional:
        raise ValueError(f"{field} contains an unsupported field")
    return value


def _bounded_string(value: Any, pattern: re.Pattern[str], *, field: str) -> str:
    if type(value) is not str or not pattern.fullmatch(value):
        raise ValueError(f"{field} is invalid")
    return value


def _artifact_path(value: Any) -> str:
    if type(value) is not str or len(value) > 240 or "\\" in value or "//" in value:
        raise ValueError("artifact relative_path is invalid")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or len(path.parts) < 2
        or any(part in {"", ".", ".."} or not _PATH_SEGMENT.fullmatch(part) for part in path.parts)
    ):
        raise ValueError("artifact relative_path is invalid")
    if not value.endswith(".tar.gz"):
        raise ValueError("artifact relative_path must identify a .tar.gz release")
    return value


def _signature(value: Any, key_id: Any) -> tuple[str | None, str | None]:
    if value is None and key_id is None:
        return None, None
    if type(value) is not str or type(key_id) is not str:
        raise ValueError("artifact signature and key_id must be supplied together")
    normalized_key = _bounded_string(key_id, _KEY_ID, field="artifact key_id")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("artifact signature is not canonical base64") from exc
    if not decoded or len(decoded) > MAX_SIGNATURE_BYTES:
        raise ValueError("artifact signature size is invalid")
    if base64.b64encode(decoded).decode("ascii") != value:
        raise ValueError("artifact signature is not canonical base64")
    return value, normalized_key


def _parse_artifact(value: Any) -> ArtifactMetadata:
    raw = _require_object(
        value,
        field="artifact",
        required=frozenset({"code_version", "relative_path", "sha256", "size_bytes", "config_revision"}),
        optional=frozenset({"signature", "key_id"}),
    )
    code_version = _bounded_string(raw["code_version"], _VERSION, field="artifact code_version")
    digest = _bounded_string(raw["sha256"], _SHA256, field="artifact sha256")
    size = raw["size_bytes"]
    if type(size) is not int or not 1 <= size <= MAX_ARTIFACT_BYTES:
        raise ValueError("artifact size_bytes is invalid")
    config_revision = _bounded_string(
        raw["config_revision"],
        _CONFIG_REVISION,
        field="artifact config_revision",
    )
    signature, key_id = _signature(raw.get("signature"), raw.get("key_id"))
    return ArtifactMetadata(
        code_version=code_version,
        relative_path=_artifact_path(raw["relative_path"]),
        sha256=digest,
        size_bytes=size,
        config_revision=config_revision,
        signature=signature,
        key_id=key_id,
    )


def _parse_rollout(value: Any) -> Rollout:
    raw = _require_object(
        value,
        field="rollout",
        required=frozenset({"id", "state", "target_version", "baseline_version", "canary_workers"}),
    )
    state = raw["state"]
    if state not in ROLLOUT_STATES:
        raise ValueError("rollout state is invalid")
    canaries = raw["canary_workers"]
    if type(canaries) is not list or len(canaries) > MAX_WORKERS:
        raise ValueError("rollout canary_workers is invalid")
    normalized_canaries = tuple(_bounded_string(item, _IDENTIFIER, field="rollout canary worker") for item in canaries)
    if len(set(normalized_canaries)) != len(normalized_canaries):
        raise ValueError("rollout repeats a canary worker")
    return Rollout(
        rollout_id=_bounded_string(raw["id"], _ROLLOUT_ID, field="rollout id"),
        state=state,
        target_version=_bounded_string(raw["target_version"], _VERSION, field="rollout target_version"),
        baseline_version=_bounded_string(raw["baseline_version"], _VERSION, field="rollout baseline_version"),
        canary_workers=normalized_canaries,
    )


def _parse_worker(value: Any) -> WorkerDesiredState:
    raw = _require_object(
        value,
        field="worker desired state",
        required=frozenset({"worker_id", "desired_version", "config_revision", "cohort", "enabled"}),
    )
    if type(raw["enabled"]) is not bool:
        raise ValueError("worker enabled must be a boolean")
    cohort = raw["cohort"]
    if cohort not in WORKER_COHORTS:
        raise ValueError("worker cohort is invalid")
    return WorkerDesiredState(
        worker_id=_bounded_string(raw["worker_id"], _IDENTIFIER, field="worker id"),
        desired_version=_bounded_string(raw["desired_version"], _VERSION, field="worker desired_version"),
        config_revision=_bounded_string(raw["config_revision"], _CONFIG_REVISION, field="worker config_revision"),
        cohort=cohort,
        enabled=raw["enabled"],
    )


def parse_desired_state(document: bytes | str) -> DesiredState:
    """Parse and validate the bounded central desired-state document."""

    encoded = document.encode("utf-8") if isinstance(document, str) else bytes(document)
    if not encoded or len(encoded) > MAX_DESIRED_STATE_BYTES:
        raise ValueError("crawler desired state size is invalid")
    try:
        payload = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("crawler desired state is invalid JSON") from exc
    except ValueError:
        raise
    raw = _require_object(
        payload,
        field="crawler desired state",
        required=frozenset({"schema_version", "environment", "generation", "rollout", "artifacts", "workers"}),
    )
    if type(raw["schema_version"]) is not int or raw["schema_version"] != DESIRED_STATE_SCHEMA_VERSION:
        raise ValueError("crawler desired state schema_version is unsupported")
    environment = _bounded_string(raw["environment"], _IDENTIFIER, field="environment")
    generation = raw["generation"]
    if type(generation) is not int or not 1 <= generation <= 2**63 - 1:
        raise ValueError("crawler desired state generation is invalid")

    rollout = _parse_rollout(raw["rollout"])
    if rollout.target_version == rollout.baseline_version:
        raise ValueError("rollout target and baseline versions must differ")

    raw_artifacts = raw["artifacts"]
    if type(raw_artifacts) is not list or not 2 <= len(raw_artifacts) <= MAX_ARTIFACTS:
        raise ValueError("crawler desired state artifacts are invalid")
    artifacts: dict[str, ArtifactMetadata] = {}
    artifact_digests: set[str] = set()
    for raw_artifact in raw_artifacts:
        artifact = _parse_artifact(raw_artifact)
        if artifact.code_version in artifacts:
            raise ValueError("crawler desired state repeats an artifact version")
        if artifact.sha256 in artifact_digests:
            raise ValueError("crawler desired state reuses one digest for multiple versions")
        artifacts[artifact.code_version] = artifact
        artifact_digests.add(artifact.sha256)
    for required_version in (rollout.target_version, rollout.baseline_version):
        if required_version not in artifacts:
            raise ValueError("rollout references an unavailable artifact version")

    raw_workers = raw["workers"]
    if type(raw_workers) is not list or not 1 <= len(raw_workers) <= MAX_WORKERS:
        raise ValueError("crawler desired state workers are invalid")
    workers: dict[str, WorkerDesiredState] = {}
    for raw_worker in raw_workers:
        worker = _parse_worker(raw_worker)
        if worker.worker_id in workers:
            raise ValueError("crawler desired state repeats a worker")
        if worker.desired_version not in artifacts:
            raise ValueError("worker references an unavailable artifact version")
        if artifacts[worker.desired_version].config_revision != worker.config_revision:
            raise ValueError("worker config_revision does not match its immutable artifact")
        workers[worker.worker_id] = worker

    enabled = {name: worker for name, worker in workers.items() if worker.enabled}
    canaries = set(rollout.canary_workers)
    if not canaries.issubset(enabled):
        raise ValueError("rollout references an unknown or disabled canary worker")
    if any(enabled[name].cohort != "canary" for name in canaries):
        raise ValueError("rollout canary worker is not in the canary cohort")

    desired_target = {name for name, worker in enabled.items() if worker.desired_version == rollout.target_version}
    invalid_versions = {
        worker.desired_version
        for worker in enabled.values()
        if worker.desired_version not in {rollout.target_version, rollout.baseline_version}
    }
    if invalid_versions:
        raise ValueError("enabled worker desired version is outside the active rollout")
    if rollout.state == "canary":
        if not canaries or desired_target != canaries:
            raise ValueError("canary rollout must target exactly the reviewed canary workers")
    elif rollout.state == "rolling":
        if not canaries.issubset(desired_target):
            raise ValueError("rolling rollout cannot move a canary back to baseline")
    elif rollout.state == "complete" and desired_target != set(enabled):
        raise ValueError("complete rollout must target every enabled worker")
    elif rollout.state == "rollback" and desired_target:
        raise ValueError("rollback state must return every enabled worker to baseline")

    return DesiredState(
        schema_version=DESIRED_STATE_SCHEMA_VERSION,
        environment=environment,
        generation=generation,
        rollout=rollout,
        artifacts=MappingProxyType(artifacts),
        workers=MappingProxyType(workers),
    )


def reconcile_decision(
    state: DesiredState,
    worker_id: str,
    *,
    current_version: str,
    current_digest: str,
    current_config_revision: str,
    last_generation: int,
) -> ReconcileDecision:
    """Return the only action a worker may take for one validated generation."""

    normalized_worker = _bounded_string(worker_id, _IDENTIFIER, field="worker id")
    try:
        desired = state.workers[normalized_worker]
    except KeyError as exc:
        raise ValueError("worker is not present in central desired state") from exc
    if type(last_generation) is not int or last_generation < 0:
        raise ValueError("local generation is invalid")
    if state.generation < last_generation:
        raise ValueError("central desired state generation is older than local state")

    artifact = state.artifacts[desired.desired_version]
    if current_version == artifact.code_version and current_digest not in {"", artifact.sha256}:
        raise ValueError("immutable artifact version has a conflicting digest")
    matches_desired = (
        current_version == artifact.code_version
        and current_digest == artifact.sha256
        and current_config_revision == desired.config_revision
    )

    if not desired.enabled:
        action, reason = "blocked", "worker is disabled in central desired state"
    elif state.rollout.state == "paused":
        action, reason = "blocked", "rollout is paused"
    elif state.generation == last_generation and not matches_desired:
        raise ValueError("a changed desired release requires a newer central generation")
    elif matches_desired:
        action, reason = "noop", "worker already matches desired state"
    else:
        action, reason = "deploy", "worker differs from desired state"
    return ReconcileDecision(
        action=action,
        reason=reason,
        generation=state.generation,
        rollout_id=state.rollout.rollout_id,
        desired=desired,
        artifact=artifact,
    )


def assert_expected_database_contract(table_columns: Mapping[str, Iterable[str]]) -> None:
    """Fail closed when Subtask A's schema is missing a required runtime field."""

    for table, required_columns in EXPECTED_DATABASE_CONTRACT.items():
        observed = {str(column) for column in table_columns.get(table, ())}
        missing = set(required_columns) - observed
        if missing:
            raise ValueError(f"crawler release database contract is incomplete for {table}")


__all__ = [
    "ArtifactMetadata",
    "DESIRED_STATE_SCHEMA_VERSION",
    "DesiredState",
    "EXPECTED_DATABASE_CONTRACT",
    "ReconcileDecision",
    "Rollout",
    "WorkerDesiredState",
    "assert_expected_database_contract",
    "parse_desired_state",
    "reconcile_decision",
]
