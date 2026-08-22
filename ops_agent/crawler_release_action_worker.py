"""Privileged, fixed-action consumer for audited crawler release requests.

The Ops API can only append immutable requests.  This process runs on the
central crawler-control host with the narrowly scoped release-admin database
credential and converts those requests into the existing, generation-fenced
release administration calls.  It deliberately has no command execution or
remote transport seam.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import signal
import socket
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final
from uuid import UUID, uuid4

import psycopg2
from psycopg2.extras import Json, RealDictCursor

from ops_agent.crawler_control_db import release_admin_database_config
from ops_agent.production_topology import PROJECT_ROOT, load_production_topology
from tools import manage_crawler_release as release_admin


PUBLIC_ROOT: Final = Path("/var/lib/mooncen-crawler-control/public")
ALLOWED_ACTIONS: Final = frozenset(
    {
        "create_canary",
        "advance_rollout",
        "pause_rollout",
        "rollback_rollout",
        "complete_rollback",
    }
)
ENVIRONMENTS: Final = frozenset({"production", "staging", "development"})
WORKER_KEY: Final = re.compile(r"[a-z][a-z0-9_-]{0,63}")
SHA256: Final = re.compile(r"[0-9a-f]{64}")
COMMIT: Final = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
OWNER_COMPONENT: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}")
IDEMPOTENCY_KEY: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{15,127}")
MAX_ATTEMPTS: Final = 5
LEASE_SECONDS: Final = 120
HEARTBEAT_SECONDS: Final = 30
POLL_SECONDS: Final = 5
BASELINE_FRESH_SECONDS: Final = 360
RELEASE_APPROVAL_FUNCTION_SHA256: Final = {
    "crawler_release_approval_catalog_is_valid": (
        "bc844b15763ef0664853a53765f68243ec365e55b2a3d5fd57fcccf08de97266"
    ),
    "crawler_release_approval_contract_is_valid": (
        "2ae043b55f7dc2aa810a64aa3fea2dbe796128b128c6f34705843091a8aaab71"
    ),
    "crawler_release_action_runtime_is_ready": (
        "1d653749681f1c33751941c50e2541f3732cc35e4b45fda6debc41d3ef8477bf"
    ),
}
REAPER_LIMIT: Final = 50
MAX_RESULT_BYTES: Final = 60_000
RUNNING = True
logger = logging.getLogger(__name__)


class ReleaseActionWorkerError(RuntimeError):
    """A fail-closed worker or request contract error."""


class LeaseLostError(ReleaseActionWorkerError):
    """The queue lease is no longer owned by this attempt."""


class ActionRejected(ReleaseActionWorkerError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = _error_code(code)


@dataclass(frozen=True)
class WorkerConfig:
    environment: str
    owner: str
    public_root: Path = PUBLIC_ROOT
    max_attempts: int = MAX_ATTEMPTS
    lease_seconds: int = LEASE_SECONDS
    heartbeat_seconds: int = HEARTBEAT_SECONDS
    poll_seconds: int = POLL_SECONDS
    baseline_fresh_seconds: int = BASELINE_FRESH_SECONDS


@dataclass(frozen=True)
class ActionLease:
    request_id: str
    action: str
    environment: str
    expected_generation: int
    payload: dict[str, Any]
    confirmation: str
    idempotency_key: str
    requested_by: str
    requester_login: str
    requester_role: str
    reason: str
    attempt_count: int
    reconcile_only: bool
    lease_owner: str
    lease_token: str
    approval_receipt_id: str = ""
    approval_request_digest: str = ""
    approval_expires_at: Any = None


@dataclass(frozen=True)
class Completion:
    state: str

    @property
    def accepted(self) -> bool:
        return self.state in {"completed", "already_completed"}


ConnectionFactory = Callable[[], Any]


def _canonical_uuid(value: Any, *, label: str) -> str:
    cleaned = str(value).strip()
    try:
        parsed = UUID(cleaned)
    except (ValueError, AttributeError) as exc:
        raise ActionRejected("invalid_request", f"{label} is not a canonical UUID") from exc
    if parsed.int == 0 or str(parsed) != cleaned:
        raise ActionRejected("invalid_request", f"{label} is not a canonical UUID")
    return cleaned


def _error_code(value: str) -> str:
    cleaned = str(value).strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", cleaned):
        return "internal_error"
    return cleaned


def _error_message(value: Any) -> str:
    cleaned = " ".join(str(value).replace("\x00", "").split())
    return (cleaned or "crawler release action failed")[:4_000]


def _strict_int(value: Any, *, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ActionRejected("invalid_request", f"{label} is invalid")
    return value


def _bounded_environment_int(
    name: str, default: int, minimum: int, maximum: int
) -> int:
    raw = str(os.getenv(name, str(default))).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ReleaseActionWorkerError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ReleaseActionWorkerError(f"{name} is outside the allowed range")
    return value


def _strict_worker_keys(value: Any, *, required: bool) -> list[str]:
    if not isinstance(value, list) or (required and not value) or len(value) > 200:
        raise ActionRejected("invalid_request", "worker key list is invalid")
    keys: list[str] = []
    for item in value:
        if not isinstance(item, str) or not WORKER_KEY.fullmatch(item):
            raise ActionRejected("invalid_request", "worker key is invalid")
        keys.append(item)
    if len(keys) != len(set(keys)):
        raise ActionRejected("invalid_request", "worker keys are not unique")
    return keys


def _worker_set_digest(worker_keys: Sequence[str]) -> str:
    canonical = "\n".join(sorted(worker_keys)).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()[:12]


def _bounded_text(value: Any, *, label: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ActionRejected("invalid_request", f"{label} is invalid")
    cleaned = value.strip()
    if not cleaned or len(cleaned) > maximum or "\x00" in cleaned:
        raise ActionRejected("invalid_request", f"{label} is invalid")
    return cleaned


def _strict_payload(lease: ActionLease) -> dict[str, Any]:
    payload = lease.payload
    if type(payload) is not dict:
        raise ActionRejected("invalid_request", "request payload must be an object")
    allowed = {
        "build": {"source_commit", "source_tree", "test_profile"},
        "register_artifact": {
            "build_request_id",
            "artifact_digest",
            "code_version",
            "config_revision",
        },
        "create_canary": {
            "artifact_digest",
            "baseline_digest",
            "rollout_id",
            "worker_keys",
        },
        "advance_rollout": {"rollout_id", "rollout_phase", "target_worker_keys"},
        "pause_rollout": {"rollout_id"},
        "rollback_rollout": {"rollout_id"},
        "complete_rollback": {"rollout_id"},
    }.get(lease.action)
    required = allowed
    if lease.action == "advance_rollout":
        required = {"rollout_id", "rollout_phase"}
    if allowed is None or not required.issubset(payload) or not set(payload).issubset(allowed):
        raise ActionRejected("invalid_request", "request payload fields are invalid")

    if lease.action == "build":
        commit = payload["source_commit"]
        tree = payload["source_tree"]
        profile = payload["test_profile"]
        if (
            not isinstance(commit, str)
            or not COMMIT.fullmatch(commit)
            or not isinstance(tree, str)
            or not COMMIT.fullmatch(tree)
            or profile not in {"crawler", "crawler_full"}
            or lease.expected_generation != 0
        ):
            raise ActionRejected("invalid_request", "build evidence identity is invalid")
        normalized = {
            "source_commit": commit,
            "source_tree": tree,
            "test_profile": profile,
        }
        expected_confirmation = f"BUILD {tree[:12]}"
    elif lease.action == "register_artifact":
        build_request_id = _canonical_uuid(
            payload["build_request_id"], label="build request id"
        )
        digest = payload["artifact_digest"]
        if (
            not isinstance(digest, str)
            or not SHA256.fullmatch(digest)
            or lease.expected_generation != 0
        ):
            raise ActionRejected("invalid_request", "artifact evidence identity is invalid")
        normalized = {
            "build_request_id": build_request_id,
            "artifact_digest": digest,
            "code_version": _bounded_text(
                payload["code_version"], label="code version", maximum=200
            ),
            "config_revision": _bounded_text(
                payload["config_revision"], label="config revision", maximum=255
            ),
        }
        expected_confirmation = f"REGISTER {digest[:12]}"
    elif lease.action == "create_canary":
        rollout_id = _canonical_uuid(payload["rollout_id"], label="rollout id")
        target = payload["artifact_digest"]
        baseline = payload["baseline_digest"]
        if (
            not isinstance(target, str)
            or not SHA256.fullmatch(target)
            or not isinstance(baseline, str)
            or not SHA256.fullmatch(baseline)
            or target == baseline
            or lease.expected_generation < 1
        ):
            raise ActionRejected("invalid_request", "canary artifact identity is invalid")
        keys = _strict_worker_keys(payload["worker_keys"], required=True)
        normalized = {
            "rollout_id": rollout_id,
            "artifact_digest": target,
            "baseline_digest": baseline,
            "worker_keys": keys,
        }
        expected_confirmation = (
            f"CANARY {rollout_id} {lease.expected_generation} "
            f"{target[:12]} {baseline[:12]} {_worker_set_digest(keys)}"
        )
    else:
        if lease.expected_generation < 1:
            raise ActionRejected("invalid_request", "rollout generation must be positive")
        rollout_id = _canonical_uuid(payload["rollout_id"], label="rollout id")
        normalized = {"rollout_id": rollout_id}
        if lease.action == "advance_rollout":
            phase = payload["rollout_phase"]
            if phase not in {"rolling", "complete"}:
                raise ActionRejected("invalid_request", "rollout phase is invalid")
            targets = _strict_worker_keys(
                payload.get("target_worker_keys", []), required=phase == "rolling"
            )
            if phase == "complete" and targets:
                raise ActionRejected(
                    "invalid_request", "complete does not accept target workers"
                )
            normalized.update(
                {"rollout_phase": phase, "target_worker_keys": targets}
            )
            expected_confirmation = (
                f"ADVANCE {rollout_id} {lease.expected_generation} {phase} "
                f"{_worker_set_digest(targets) if targets else 'none'}"
            )
        elif lease.action == "pause_rollout":
            expected_confirmation = f"PAUSE {rollout_id} {lease.expected_generation}"
        elif lease.action == "rollback_rollout":
            expected_confirmation = f"ROLLBACK {rollout_id} {lease.expected_generation}"
        else:
            expected_confirmation = (
                f"COMPLETE_ROLLBACK {rollout_id} {lease.expected_generation}"
            )
    if lease.confirmation != expected_confirmation:
        raise ActionRejected(
            "invalid_confirmation",
            "confirmation does not match the exact persisted release action identity",
        )
    return normalized


def _result_json(result: Mapping[str, Any]) -> Json:
    document = dict(result)
    try:
        encoded = json.dumps(
            document,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ReleaseActionWorkerError("release action result is not JSON-safe") from exc
    if not encoded or len(encoded) > MAX_RESULT_BYTES:
        raise ReleaseActionWorkerError("release action result is too large")
    return Json(document)


def _lease_from_row(row: Mapping[str, Any], config: WorkerConfig, token: str) -> ActionLease:
    request_id = _canonical_uuid(row.get("id"), label="request id")
    action = str(row.get("action") or "")
    environment = str(row.get("environment") or "")
    attempt_count = _strict_int(row.get("attempt_count"), label="attempt count", minimum=1)
    expected_generation = _strict_int(
        row.get("expected_generation"), label="expected generation"
    )
    payload = row.get("request_payload")
    idempotency_key = str(row.get("idempotency_key") or "")
    requested_by = _canonical_uuid(row.get("requested_by"), label="requester id")
    requester_login = str(row.get("requester_login") or "")
    requester_role = str(row.get("requester_role") or "")
    reconcile_only = row.get("reconcile_only")
    reason = str(row.get("reason") or "")
    confirmation = str(row.get("confirmation") or "")
    approval_receipt_id = _canonical_uuid(
        row.get("approval_receipt_id"), label="approval receipt id"
    )
    approval_request_digest = str(row.get("approval_request_digest") or "")
    approval_expires_at = row.get("approval_expires_at")
    if (
        action not in ALLOWED_ACTIONS
        or environment != config.environment
        or attempt_count > config.max_attempts
        or type(payload) is not dict
        or not IDEMPOTENCY_KEY.fullmatch(idempotency_key)
        or requester_role != "admin"
        or type(reconcile_only) is not bool
        or not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", requester_login)
        or reason != reason.strip()
        or not 3 <= len(reason) <= 500
        or "\x00" in reason
        or confirmation != confirmation.strip()
        or not 1 <= len(confirmation) <= 180
        or "\x00" in confirmation
        or not SHA256.fullmatch(approval_request_digest)
        or approval_expires_at is None
    ):
        raise ReleaseActionWorkerError("claimed release action has an invalid identity")
    return ActionLease(
        request_id=request_id,
        action=action,
        environment=environment,
        expected_generation=expected_generation,
        payload=dict(payload),
        confirmation=confirmation,
        idempotency_key=idempotency_key,
        requested_by=requested_by,
        requester_login=requester_login,
        requester_role=requester_role,
        reason=reason,
        attempt_count=attempt_count,
        reconcile_only=reconcile_only,
        lease_owner=config.owner,
        lease_token=token,
        approval_receipt_id=approval_receipt_id,
        approval_request_digest=approval_request_digest,
        approval_expires_at=approval_expires_at,
    )


def claim_next(connection: Any, config: WorkerConfig) -> ActionLease | None:
    """Atomically lease one request; concurrent consumers skip the same row."""

    token = str(uuid4())
    connection.set_session(isolation_level="READ COMMITTED", readonly=False, autocommit=False)
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                WITH candidate AS (
                    SELECT request.id
                    FROM ops_crawler_release_action_requests request
                    JOIN ops_crawler_release_action_approvals approval
                      ON approval.request_id = request.id
                     AND approval.environment = request.environment
                     AND approval.request_digest = request.request_digest
                    WHERE request.environment = %s
                      AND request.status = 'queued'
                      AND request.action IN (
                          'create_canary', 'advance_rollout', 'pause_rollout',
                          'rollback_rollout', 'complete_rollback'
                      )
                      AND (
                          (request.started_at IS NULL
                              AND approval.expires_at > clock_timestamp())
                          OR (request.started_at IS NOT NULL
                              AND request.started_at <= approval.expires_at)
                      )
                      AND (
                          request.attempt_count < %s
                          OR (request.reconcile_only IS TRUE
                              AND request.attempt_count = %s)
                      )
                    ORDER BY request.created_at, request.id
                    FOR UPDATE OF request SKIP LOCKED
                    LIMIT 1
                )
                UPDATE ops_crawler_release_action_requests request
                SET status = 'leased',
                    attempt_count = request.attempt_count
                        + CASE WHEN request.reconcile_only THEN 0 ELSE 1 END,
                    lease_owner = %s,
                    lease_token = %s::uuid,
                    leased_until = clock_timestamp() + (%s * interval '1 second'),
                    started_at = COALESCE(request.started_at, clock_timestamp())
                FROM candidate
                JOIN ops_crawler_release_action_approvals approval
                  ON approval.request_id = candidate.id
                WHERE request.id = candidate.id
                  AND request.status = 'queued'
                  AND approval.environment = request.environment
                  AND approval.request_digest = request.request_digest
                  AND request.action IN (
                      'create_canary', 'advance_rollout', 'pause_rollout',
                      'rollback_rollout', 'complete_rollback'
                  )
                  AND (
                      (request.started_at IS NULL
                          AND approval.expires_at > clock_timestamp())
                      OR (request.started_at IS NOT NULL
                          AND request.started_at <= approval.expires_at)
                  )
                  AND (
                      request.attempt_count < %s
                      OR (request.reconcile_only IS TRUE
                          AND request.attempt_count = %s)
                  )
                RETURNING request.id::text, request.action, request.environment,
                          request.expected_generation, request.request_payload,
                          request.confirmation, request.idempotency_key,
                          request.requested_by::text, request.requester_login::text,
                          request.requester_role, request.reason,
                          request.attempt_count, request.reconcile_only,
                          approval.receipt_id::text AS approval_receipt_id,
                          approval.request_digest AS approval_request_digest,
                          approval.expires_at AS approval_expires_at
                """,
                (
                    config.environment,
                    config.max_attempts,
                    config.max_attempts,
                    config.owner,
                    token,
                    config.lease_seconds,
                    config.max_attempts,
                    config.max_attempts,
                ),
            )
            row = cursor.fetchone()
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    if row is None:
        return None
    return _lease_from_row(row, config, token)


def renew_lease(connection: Any, lease: ActionLease, lease_seconds: int) -> bool:
    connection.set_session(readonly=False, autocommit=False)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE ops_crawler_release_action_requests
                SET leased_until = clock_timestamp() + (%s * interval '1 second')
                WHERE id = %s::uuid
                  AND status = 'leased'
                  AND attempt_count = %s
                  AND lease_owner = %s
                  AND lease_token = %s::uuid
                  AND leased_until > clock_timestamp()
                """,
                (
                    lease_seconds,
                    lease.request_id,
                    lease.attempt_count,
                    lease.lease_owner,
                    lease.lease_token,
                ),
            )
            renewed = cursor.rowcount == 1
        connection.commit()
        return renewed
    except Exception:
        connection.rollback()
        raise


def _terminal_state(connection: Any, request_id: str) -> tuple[str, Any, Any, Any] | None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT status, result, error_code, error_message
            FROM ops_crawler_release_action_requests
            WHERE id = %s::uuid
            """,
            (request_id,),
        )
        row = cursor.fetchone()
    return tuple(row) if row is not None else None


def complete_success(
    connection: Any,
    lease: ActionLease,
    result: Mapping[str, Any],
) -> Completion:
    encoded = _result_json(result)
    connection.set_session(readonly=False, autocommit=False)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE ops_crawler_release_action_requests
                SET status = 'succeeded', lease_owner = NULL, lease_token = NULL,
                    leased_until = NULL, result = %s, error_code = NULL,
                    error_message = NULL, reconcile_only = FALSE,
                    finished_at = clock_timestamp()
                WHERE id = %s::uuid
                  AND status = 'leased'
                  AND attempt_count = %s
                  AND lease_owner = %s
                  AND lease_token = %s::uuid
                  AND leased_until > clock_timestamp()
                """,
                (
                    encoded,
                    lease.request_id,
                    lease.attempt_count,
                    lease.lease_owner,
                    lease.lease_token,
                ),
            )
            updated = cursor.rowcount == 1
        if updated:
            connection.commit()
            return Completion("completed")
        terminal = _terminal_state(connection, lease.request_id)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    if terminal and terminal[0] == "succeeded" and terminal[1] == dict(result):
        return Completion("already_completed")
    return Completion("lease_lost")


def complete_failure(
    connection: Any,
    lease: ActionLease,
    *,
    code: str,
    message: str,
) -> Completion:
    safe_code = _error_code(code)
    safe_message = _error_message(message)
    connection.set_session(readonly=False, autocommit=False)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE ops_crawler_release_action_requests
                SET status = 'failed', lease_owner = NULL, lease_token = NULL,
                    leased_until = NULL, result = NULL, error_code = %s,
                    error_message = %s, reconcile_only = FALSE,
                    finished_at = clock_timestamp()
                WHERE id = %s::uuid
                  AND status = 'leased'
                  AND attempt_count = %s
                  AND lease_owner = %s
                  AND lease_token = %s::uuid
                  AND leased_until > clock_timestamp()
                """,
                (
                    safe_code,
                    safe_message,
                    lease.request_id,
                    lease.attempt_count,
                    lease.lease_owner,
                    lease.lease_token,
                ),
            )
            updated = cursor.rowcount == 1
        if updated:
            connection.commit()
            return Completion("completed")
        terminal = _terminal_state(connection, lease.request_id)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    if (
        terminal
        and terminal[0] == "failed"
        and terminal[2] == safe_code
        and terminal[3] == safe_message
    ):
        return Completion("already_completed")
    return Completion("lease_lost")


def retry_owned(
    connection: Any,
    lease: ActionLease,
    *,
    max_attempts: int,
) -> Completion:
    exhausted = lease.attempt_count >= max_attempts
    connection.set_session(readonly=False, autocommit=False)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE ops_crawler_release_action_requests
                SET status = %s,
                    lease_owner = NULL, lease_token = NULL, leased_until = NULL,
                    result = NULL, reconcile_only = FALSE,
                    error_code = CASE WHEN %s THEN 'retry_exhausted' ELSE NULL END,
                    error_message = CASE WHEN %s
                        THEN 'release action retry budget was exhausted' ELSE NULL END,
                    finished_at = CASE WHEN %s THEN clock_timestamp() ELSE NULL END
                WHERE id = %s::uuid
                  AND status = 'leased'
                  AND attempt_count = %s
                  AND lease_owner = %s
                  AND lease_token = %s::uuid
                  AND leased_until > clock_timestamp()
                """,
                (
                    "failed" if exhausted else "queued",
                    exhausted,
                    exhausted,
                    exhausted,
                    lease.request_id,
                    lease.attempt_count,
                    lease.lease_owner,
                    lease.lease_token,
                ),
            )
            updated = cursor.rowcount == 1
        connection.commit()
        return Completion("completed" if updated else "lease_lost")
    except Exception:
        connection.rollback()
        raise


def defer_reconciliation_owned(
    connection: Any,
    lease: ActionLease,
    *,
    max_attempts: int,
) -> Completion:
    """Queue one read-only reconciliation pass after an ambiguous final attempt."""

    connection.set_session(readonly=False, autocommit=False)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE ops_crawler_release_action_requests
                SET status = 'queued', reconcile_only = TRUE,
                    lease_owner = NULL, lease_token = NULL, leased_until = NULL,
                    result = NULL, error_code = NULL, error_message = NULL,
                    finished_at = NULL
                WHERE id = %s::uuid
                  AND status = 'leased'
                  AND attempt_count = %s
                  AND attempt_count >= %s
                  AND reconcile_only IS FALSE
                  AND lease_owner = %s
                  AND lease_token = %s::uuid
                  AND leased_until > clock_timestamp()
                """,
                (
                    lease.request_id,
                    lease.attempt_count,
                    max_attempts,
                    lease.lease_owner,
                    lease.lease_token,
                ),
            )
            updated = cursor.rowcount == 1
        connection.commit()
        return Completion("completed" if updated else "lease_lost")
    except Exception:
        connection.rollback()
        raise


def complete_reconciliation_required(
    connection: Any,
    lease: ActionLease,
    *,
    message: str,
) -> Completion:
    """Finish without claiming success or failure when state cannot be proven."""

    safe_message = _error_message(message)
    connection.set_session(readonly=False, autocommit=False)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE ops_crawler_release_action_requests
                SET status = 'reconciliation_required', reconcile_only = FALSE,
                    lease_owner = NULL, lease_token = NULL, leased_until = NULL,
                    result = NULL, error_code = 'reconciliation_required',
                    error_message = %s, finished_at = clock_timestamp()
                WHERE id = %s::uuid
                  AND status = 'leased'
                  AND attempt_count = %s
                  AND reconcile_only IS TRUE
                  AND lease_owner = %s
                  AND lease_token = %s::uuid
                  AND leased_until > clock_timestamp()
                """,
                (
                    safe_message,
                    lease.request_id,
                    lease.attempt_count,
                    lease.lease_owner,
                    lease.lease_token,
                ),
            )
            updated = cursor.rowcount == 1
        connection.commit()
        return Completion("completed" if updated else "lease_lost")
    except Exception:
        connection.rollback()
        raise


def reap_expired(
    connection: Any,
    *,
    environment: str,
    max_attempts: int,
    limit: int = REAPER_LIMIT,
) -> tuple[int, int]:
    """Requeue expired mutation attempts, then surface unprovable state explicitly."""

    connection.set_session(isolation_level="READ COMMITTED", readonly=False, autocommit=False)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                WITH expired AS (
                    SELECT id
                    FROM ops_crawler_release_action_requests
                    WHERE environment = %s
                      AND status = 'leased'
                      AND leased_until <= clock_timestamp()
                    ORDER BY leased_until, id
                    FOR UPDATE SKIP LOCKED
                    LIMIT %s
                )
                UPDATE ops_crawler_release_action_requests request
                SET status = CASE WHEN request.reconcile_only
                                  THEN 'reconciliation_required' ELSE 'queued' END,
                    lease_owner = NULL, lease_token = NULL, leased_until = NULL,
                    result = NULL,
                    error_code = CASE WHEN request.reconcile_only
                                      THEN 'reconciliation_required' ELSE NULL END,
                    error_message = CASE WHEN request.reconcile_only
                        THEN 'read-only release reconciliation lease expired; operator review is required'
                        ELSE NULL END,
                    finished_at = CASE WHEN request.reconcile_only
                                       THEN clock_timestamp() ELSE NULL END,
                    reconcile_only = CASE
                        WHEN request.reconcile_only THEN FALSE
                        WHEN request.attempt_count >= %s THEN TRUE
                        ELSE FALSE
                    END
                FROM expired
                WHERE request.id = expired.id
                  AND request.status = 'leased'
                  AND request.leased_until <= clock_timestamp()
                RETURNING request.status
                """,
                (
                    environment,
                    limit,
                    max_attempts,
                ),
            )
            statuses = [str(row[0]) for row in cursor.fetchall()]
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return statuses.count("queued"), statuses.count("reconciliation_required")


def _load_enrolled_workers(
    connection: Any,
    *,
    environment: str,
    worker_keys: Sequence[str],
) -> list[dict[str, Any]]:
    """Join the reviewed complete fleet to exact DB enrollment evidence.

    Desired state is intentionally not an input: the first rollout creates it.
    The request must name every reviewed worker (enabled and disabled), while
    the signed topology supplies cohort, enabled state, order and hostname.
    """

    try:
        topology = load_production_topology(PROJECT_ROOT)
    except (OSError, ValueError) as exc:
        raise ActionRejected(
            "worker_contract_unavailable", "reviewed crawler topology is unavailable"
        ) from exc
    reviewed = sorted(
        topology.crawler_workers.values(), key=lambda item: item.rollout_order
    )
    reviewed_keys = [item.worker_key for item in reviewed]
    if environment == "production" and topology.crawler_mode != "distributed":
        raise ActionRejected(
            "worker_contract_not_active",
            "reviewed crawler topology is not in distributed mode",
        )
    if sorted(worker_keys) != sorted(reviewed_keys):
        raise ActionRejected(
            "worker_contract_incomplete",
            "canary request must name the complete reviewed rollout worker fleet",
        )
    enabled_canaries = [item for item in reviewed if item.enabled and item.canary]
    if not enabled_canaries:
        raise ActionRejected(
            "canary_unavailable", "reviewed fleet has no enabled canary worker"
        )

    connection.set_session(isolation_level="SERIALIZABLE", readonly=True, autocommit=False)
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT agent.id::text AS agent_id, agent.name, agent.hostname,
                       agent.environment, agent.status, agent.maintenance_mode,
                       agent.capabilities
                FROM ops_agents agent
                WHERE agent.environment = %s
                  AND agent.capabilities = '["crawler_worker"]'::jsonb
                ORDER BY agent.name, agent.id
                """,
                (environment,),
            )
            rows = [dict(row) for row in cursor.fetchall()]
            agent_ids = [str(row["agent_id"]) for row in rows]
            cursor.execute(
                """
                SELECT agent_id::text, environment, binding_type
                FROM ops_crawler_agent_bindings
                WHERE agent_id = ANY(%s::uuid[])
                ORDER BY agent_id, binding_type
                """,
                (agent_ids,),
            )
            bindings: dict[str, list[str]] = {}
            for row in cursor.fetchall():
                if str(row["environment"]) == environment:
                    bindings.setdefault(str(row["agent_id"]), []).append(
                        str(row["binding_type"])
                    )
        connection.rollback()
    except Exception:
        connection.rollback()
        raise

    expected_names = {f"{item.worker_key} distributed crawler" for item in reviewed}
    if {str(row.get("name")) for row in rows} != expected_names or len(rows) != len(reviewed):
        raise ActionRejected(
            "worker_contract_unavailable",
            "every reviewed worker must have one exact central enrollment identity",
        )
    if len({str(row.get("agent_id")) for row in rows}) != len(rows):
        raise ActionRejected("worker_contract_invalid", "worker agent identities are not unique")
    agents_by_name = {str(row["name"]): row for row in rows}
    workers: list[dict[str, Any]] = []
    for reviewed_worker in reviewed:
        key = reviewed_worker.worker_key
        row = agents_by_name[f"{key} distributed crawler"]
        agent_id = str(row["agent_id"])
        if (
            row.get("name") != f"{key} distributed crawler"
            or row.get("hostname") != reviewed_worker.kernel_hostname
            or row.get("environment") != environment
            or row.get("status") not in {"unknown", "healthy"}
            or row.get("maintenance_mode") is not False
            or row.get("capabilities") != ["crawler_worker"]
            or bindings.get(agent_id) != ["reporter", "worker"]
        ):
            raise ActionRejected(
                "worker_contract_invalid", f"worker {key} is not exactly enrolled"
            )
        workers.append(
            {
                "worker_key": key,
                "agent_id": agent_id,
                "hostname": reviewed_worker.kernel_hostname,
                "cohort": "canary" if reviewed_worker.canary else "stable",
                "enabled": reviewed_worker.enabled,
            }
        )
    return workers


def _reconcile_create(
    connection: Any,
    lease: ActionLease,
    payload: Mapping[str, Any],
    enrolled_workers: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    connection.set_session(readonly=True, autocommit=False)
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT id::text, rollout_epoch, artifact_digest,
                       previous_artifact_digest, status, strategy
                FROM ops_crawler_release_rollouts
                WHERE id = %s::uuid AND environment = %s
                """,
                (payload["rollout_id"], lease.environment),
            )
            rollout = cursor.fetchone()
            if rollout is not None:
                cursor.execute(
                    """
                    SELECT worker_key, agent_id::text, generation,
                           desired_status, cohort, artifact_digest
                    FROM ops_crawler_rollout_worker_snapshots
                    WHERE rollout_id = %s::uuid AND environment = %s
                      AND generation = %s
                    ORDER BY worker_key
                    """,
                    (payload["rollout_id"], lease.environment, lease.expected_generation),
                )
                workers = list(cursor.fetchall())
            else:
                workers = []
        connection.rollback()
    except Exception:
        connection.rollback()
        raise
    if rollout is None:
        return None
    strategy = rollout.get("strategy") if isinstance(rollout, Mapping) else None
    observed = {str(row["worker_key"]): row for row in workers}
    expected = {str(row["worker_key"]): row for row in enrolled_workers}
    canaries = {
        key
        for key, row in expected.items()
        if row["enabled"] is True and row["cohort"] == "canary"
    }
    if (
        int(rollout["rollout_epoch"]) == lease.expected_generation
        and rollout["artifact_digest"] == payload["artifact_digest"]
        and rollout["previous_artifact_digest"] == payload["baseline_digest"]
        and rollout["status"] == "running"
        and type(strategy) is dict
        and strategy.get("schema_version") == 1
        and strategy.get("state") == "canary"
        and set(strategy.get("canary_workers") or []) == canaries
        and set(observed) == set(payload["worker_keys"]) == set(expected)
        and all(
            int(row["generation"]) == lease.expected_generation
            and str(row["agent_id"]) == str(expected[key]["agent_id"])
            and row["desired_status"]
            == ("active" if expected[key]["enabled"] else "disabled")
            and row["cohort"] == expected[key]["cohort"]
            and row["artifact_digest"]
            == (
                payload["artifact_digest"]
                if key in canaries
                else payload["baseline_digest"]
            )
            for key, row in observed.items()
        )
    ):
        return {
            "status": "CREATED",
            "environment": lease.environment,
            "rollout_id": payload["rollout_id"],
            "generation": lease.expected_generation,
            "phase": "canary",
            "canary_workers": sorted(strategy.get("canary_workers") or []),
            "recovered": True,
        }
    raise ActionRejected(
        "rollout_identity_conflict", "the rollout id already has different release evidence"
    )


def _verify_canary_baseline(
    connection: Any,
    *,
    environment: str,
    baseline_digest: str,
    enrolled_workers: Sequence[Mapping[str, Any]],
    fresh_seconds: int = BASELINE_FRESH_SECONDS,
) -> None:
    """Prove that a new rollout baseline is the fleet's exact current state.

    The database has no authoritative worker-local bootstrap evidence before
    the first desired-state generation.  Such a rollout therefore remains
    unavailable until an immutable bootstrap-report handoff is introduced.
    """

    expected = {str(worker["worker_key"]): worker for worker in enrolled_workers}
    connection.set_session(isolation_level="SERIALIZABLE", readonly=True, autocommit=False)
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT desired.worker_key, desired.agent_id::text,
                       desired.generation, desired.desired_status,
                       desired.artifact_digest, desired.code_version,
                       desired.config_revision,
                       agent.status = 'healthy' AS agent_healthy,
                       agent.last_seen_at IS NOT NULL
                           AND agent.last_seen_at >= clock_timestamp()
                               - (%s * interval '1 second') AS agent_fresh
                FROM ops_crawler_worker_desired_state desired
                JOIN ops_agents agent ON agent.id = desired.agent_id
                WHERE desired.environment = %s
                  AND desired.worker_key = ANY(%s)
                ORDER BY desired.worker_key
                """,
                (fresh_seconds, environment, sorted(expected)),
            )
            desired_rows = [dict(row) for row in cursor.fetchall()]
            cursor.execute(
                """
                SELECT DISTINCT ON (report.worker_key)
                       report.worker_key, report.agent_id::text,
                       report.desired_generation, report.status,
                       report.artifact_digest, report.code_version,
                       report.config_revision, report.health,
                       report.reported_at >= clock_timestamp()
                           - (%s * interval '1 second') AS report_fresh
                FROM ops_crawler_release_reports report
                WHERE report.environment = %s
                  AND report.worker_key = ANY(%s)
                ORDER BY report.worker_key, report.reported_at DESC,
                         report.created_at DESC, report.id DESC
                """,
                (fresh_seconds, environment, sorted(expected)),
            )
            reports = {str(row["worker_key"]): dict(row) for row in cursor.fetchall()}
        connection.rollback()
    except Exception:
        connection.rollback()
        raise

    desired = {str(row["worker_key"]): row for row in desired_rows}
    if set(desired) != set(expected):
        raise ActionRejected(
            "bootstrap_baseline_evidence_unavailable",
            "first rollout requires immutable central evidence for every installed worker baseline",
        )
    identities = {
        (
            str(row["artifact_digest"]),
            str(row["code_version"]),
            str(row["config_revision"]),
        )
        for row in desired.values()
    }
    if len(identities) != 1 or next(iter(identities))[0] != baseline_digest:
        raise ActionRejected(
            "baseline_identity_mismatch",
            "requested baseline is not the fleet's single exact desired release identity",
        )
    for key, enrolled in expected.items():
        row = desired[key]
        expected_status = "active" if enrolled["enabled"] else "disabled"
        if (
            str(row["agent_id"]) != str(enrolled["agent_id"])
            or row["desired_status"] != expected_status
        ):
            raise ActionRejected(
                "baseline_identity_mismatch",
                f"worker {key} desired identity differs from the reviewed fleet",
            )
        if expected_status != "active":
            continue
        report = reports.get(key)
        if (
            row["agent_healthy"] is not True
            or row["agent_fresh"] is not True
            or report is None
            or str(report["agent_id"]) != str(enrolled["agent_id"])
            or int(report["desired_generation"]) != int(row["generation"])
            or report["status"] not in {"ready", "rolled_back"}
            or report["artifact_digest"] != row["artifact_digest"]
            or report["code_version"] != row["code_version"]
            or report["config_revision"] != row["config_revision"]
            or type(report["health"]) is not dict
            or report["health"].get("healthy") is not True
            or report["report_fresh"] is not True
        ):
            raise ActionRejected(
                "baseline_report_unhealthy",
                f"worker {key} has no fresh exact healthy baseline report",
            )


def _reconcile_transition(
    connection: Any,
    lease: ActionLease,
    *,
    rollout_id: str,
    phase: str,
    target_workers: Sequence[str],
) -> dict[str, Any] | None:
    connection.set_session(readonly=True, autocommit=False)
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT rollout_epoch, status, strategy, artifact_digest,
                       previous_artifact_digest
                FROM ops_crawler_release_rollouts
                WHERE id = %s::uuid AND environment = %s
                """,
                (rollout_id, lease.environment),
            )
            rollout = cursor.fetchone()
            if rollout is not None:
                cursor.execute(
                    """
                    SELECT worker_key, generation, desired_status,
                           artifact_digest
                    FROM ops_crawler_rollout_worker_snapshots
                    WHERE rollout_id = %s::uuid AND environment = %s
                      AND generation = %s
                    ORDER BY worker_key
                    """,
                    (rollout_id, lease.environment, lease.expected_generation + 1),
                )
                workers = [dict(row) for row in cursor.fetchall()]
            else:
                workers = []
        connection.rollback()
    except Exception:
        connection.rollback()
        raise
    if rollout is None or int(rollout["rollout_epoch"]) == lease.expected_generation:
        return None
    strategy = rollout.get("strategy") if isinstance(rollout, Mapping) else None
    expected_status = {
        "rolling": "running",
        "complete": "success",
        "paused": "paused",
        "rollback": "rolling_back",
        "rolled_back": "rolled_back",
    }[phase]
    expected_strategy = "rollback" if phase == "rolled_back" else phase
    if (
        int(rollout["rollout_epoch"]) == lease.expected_generation + 1
        and rollout["status"] == expected_status
        and type(strategy) is dict
        and strategy.get("schema_version") == 1
        and strategy.get("state") == expected_strategy
    ):
        if not workers or any(
            int(row["generation"]) != lease.expected_generation + 1 for row in workers
        ):
            raise ActionRejected(
                "rollout_generation_conflict",
                "rollout worker generation differs from the recovered transition",
            )
        if phase in {"rolling", "complete", "rollback", "rolled_back"}:
            target_digest = str(rollout["artifact_digest"])
            active = {
                str(row["worker_key"])
                for row in workers
                if row["desired_status"] == "active"
            }
            observed_target = {
                str(row["worker_key"])
                for row in workers
                if row["desired_status"] == "active"
                and row["artifact_digest"] == target_digest
            }
            expected_target = (
                set(target_workers)
                if phase == "rolling"
                else active
                if phase == "complete"
                else set()
            )
            if observed_target != expected_target:
                raise ActionRejected(
                    "rollout_identity_conflict",
                    "recovered rollout targets differ from the requested worker set",
                )
        artifact_set = {
            str(rollout["artifact_digest"]),
            str(rollout["previous_artifact_digest"]),
        }
        if any(str(row["artifact_digest"]) not in artifact_set for row in workers):
            raise ActionRejected(
                "rollout_identity_conflict",
                "recovered rollout worker escaped the artifact set",
            )
        return {
            "status": "ADVANCED",
            "environment": lease.environment,
            "rollout_id": rollout_id,
            "generation": lease.expected_generation + 1,
            "phase": phase,
            "recovered": True,
        }
    raise ActionRejected(
        "rollout_generation_conflict", "rollout generation advanced to different evidence"
    )


def reconcile_action(connection: Any, lease: ActionLease) -> dict[str, Any] | None:
    """Read only: prove whether this request's exact mutation already committed."""

    payload = _strict_payload(lease)
    if lease.action in {"build", "register_artifact"}:
        return None
    if lease.action == "create_canary":
        workers = _load_enrolled_workers(
            connection,
            environment=lease.environment,
            worker_keys=payload["worker_keys"],
        )
        return _reconcile_create(connection, lease, payload, workers)

    rollout_id = payload["rollout_id"]
    if lease.action == "advance_rollout":
        phase = payload["rollout_phase"]
        targets = payload["target_worker_keys"]
    elif lease.action == "pause_rollout":
        phase, targets = "paused", []
    elif lease.action == "rollback_rollout":
        phase, targets = "rollback", []
    elif lease.action == "complete_rollback":
        phase, targets = "rolled_back", []
    else:  # pragma: no cover - strict payload/action validation is authoritative.
        raise ActionRejected("invalid_request", "release action is unsupported")
    return _reconcile_transition(
        connection,
        lease,
        rollout_id=rollout_id,
        phase=phase,
        target_workers=targets,
    )


def _probe_committed_action(
    connection_factory: ConnectionFactory,
    lease: ActionLease,
) -> tuple[str, dict[str, Any] | ActionRejected | None]:
    connection: Any | None = None
    try:
        connection = connection_factory()
        recovered = reconcile_action(connection, lease)
        return ("recovered", recovered) if recovered is not None else ("absent", None)
    except ActionRejected as exc:
        return "conflict", exc
    except Exception:
        logger.exception(
            "Crawler release action reconciliation probe is unavailable request_id=%s",
            lease.request_id,
        )
        return "unavailable", None
    finally:
        if connection is not None:
            connection.close()


def dispatch_action(
    connection: Any,
    lease: ActionLease,
    *,
    public_root: Path,
    baseline_fresh_seconds: int = BASELINE_FRESH_SECONDS,
) -> dict[str, Any]:
    """Dispatch only reviewed Python release functions; never a supplied command."""

    payload = _strict_payload(lease)
    if lease.action in {"build", "register_artifact"}:
        raise ActionRejected(
            "not_implemented",
            "immutable builder and signer evidence handoff is not implemented",
        )
    if lease.action == "create_canary":
        if lease.expected_generation < 1:
            raise ActionRejected("invalid_request", "canary generation must be positive")
        workers = _load_enrolled_workers(
            connection,
            environment=lease.environment,
            worker_keys=payload["worker_keys"],
        )
        recovered = _reconcile_create(connection, lease, payload, workers)
        if recovered is not None:
            return recovered
        _verify_canary_baseline(
            connection,
            environment=lease.environment,
            baseline_digest=payload["baseline_digest"],
            enrolled_workers=workers,
            fresh_seconds=baseline_fresh_seconds,
        )
        return release_admin.create_rollout(
            connection,
            environment=lease.environment,
            rollout_id=payload["rollout_id"],
            generation=lease.expected_generation,
            target_digest=payload["artifact_digest"],
            baseline_digest=payload["baseline_digest"],
            workers=workers,
            public_root=public_root,
        )

    if lease.expected_generation < 1:
        raise ActionRejected("invalid_request", "rollout generation must be positive")
    rollout_id = payload["rollout_id"]
    if lease.action == "advance_rollout":
        phase = payload["rollout_phase"]
        targets = payload["target_worker_keys"]
    elif lease.action == "pause_rollout":
        phase = "paused"
        targets = []
    elif lease.action == "rollback_rollout":
        phase = "rollback"
        targets = []
    elif lease.action == "complete_rollback":
        phase = "rolled_back"
        targets = []
    else:
        raise ActionRejected("invalid_request", "release action is unsupported")
    recovered = _reconcile_transition(
        connection,
        lease,
        rollout_id=rollout_id,
        phase=phase,
        target_workers=targets,
    )
    if recovered is not None:
        return recovered
    return release_admin.advance_rollout(
        connection,
        environment=lease.environment,
        rollout_id=rollout_id,
        expected_generation=lease.expected_generation,
        next_generation=lease.expected_generation + 1,
        phase=phase,
        target_workers=targets,
        public_root=public_root,
        fresh_seconds=baseline_fresh_seconds,
    )


class LeaseHeartbeat:
    def __init__(
        self,
        connection_factory: ConnectionFactory,
        lease: ActionLease,
        *,
        lease_seconds: int,
        interval_seconds: int,
    ) -> None:
        self._connection_factory = connection_factory
        self._lease = lease
        self._lease_seconds = lease_seconds
        self._interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._lost_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def lost(self) -> bool:
        return self._lost_event.is_set()

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval_seconds):
            connection = None
            try:
                connection = self._connection_factory()
                if not renew_lease(connection, self._lease, self._lease_seconds):
                    self._lost_event.set()
                    return
            except Exception:
                logger.exception("Crawler release action heartbeat failed")
                self._lost_event.set()
                return
            finally:
                if connection is not None:
                    connection.close()

    def __enter__(self) -> "LeaseHeartbeat":
        self._thread = threading.Thread(
            target=self._run,
            name="crawler-release-action-heartbeat",
            daemon=True,
        )
        self._thread.start()
        return self

    def ensure_owned(self) -> None:
        if self.lost:
            raise LeaseLostError("crawler release action lease was lost")

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, float(self._interval_seconds) + 1.0))


def _database_contract(connection: Any, environment: str) -> None:
    connection.set_session(readonly=True, autocommit=False)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    EXISTS (
                        SELECT 1 FROM ops_crawler_control_database_marker
                        WHERE singleton IS TRUE
                          AND database_name = current_database()::name
                    ),
                    to_regclass('public.ops_crawler_release_action_requests') IS NOT NULL,
                    to_regclass('public.ops_crawler_release_action_approvals') IS NOT NULL,
                    to_regprocedure(
                        'public.crawler_release_approval_contract_is_valid(text)'
                    ) IS NOT NULL,
                    public.crawler_release_approval_contract_is_valid(%s),
                    has_function_privilege(
                        current_user,
                        'public.heartbeat_crawler_release_action_consumer(text,text)',
                        'EXECUTE'
                    ),
                    pg_has_role(current_user, 'mooncen_crawler_release_admin', 'member'),
                    has_table_privilege(
                        current_user, 'ops_crawler_release_action_requests', 'SELECT'
                    ),
                    has_column_privilege(
                        current_user, 'ops_crawler_release_action_requests',
                        'lease_token', 'UPDATE'
                    ),
                    has_table_privilege(
                        current_user, 'ops_crawler_release_rollouts', 'UPDATE'
                    ),
                    has_table_privilege(
                        current_user, 'ops_crawler_worker_desired_state', 'UPDATE'
                    ),
                    NOT pg_has_role(current_user, 'mooncen_crawler_control', 'member'),
                    NOT pg_has_role(current_user, 'mooncen_crawler_worker', 'member')
                """,
                (environment,),
            )
            row = cursor.fetchone()
        connection.rollback()
    except Exception:
        connection.rollback()
        raise
    if not row or any(value is not True for value in row):
        raise ReleaseActionWorkerError(
            f"release action database contract failed for {environment}"
        )


def heartbeat_runtime(connection: Any, config: WorkerConfig) -> None:
    connection.set_session(readonly=False, autocommit=False)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT procedure.proname, procedure.prosrc
                FROM pg_proc procedure
                JOIN pg_namespace namespace_row
                  ON namespace_row.oid = procedure.pronamespace
                WHERE namespace_row.nspname = 'public'
                  AND procedure.proname = ANY(%s::text[])
                ORDER BY procedure.proname
                """,
                (sorted(RELEASE_APPROVAL_FUNCTION_SHA256),),
            )
            identities = cursor.fetchall()
            if len(identities) != len(RELEASE_APPROVAL_FUNCTION_SHA256) or any(
                hashlib.sha256(
                    str(source)
                    .replace("\r\n", "\n")
                    .replace("\r", "\n")
                    .strip()
                    .encode("utf-8")
                ).hexdigest()
                != RELEASE_APPROVAL_FUNCTION_SHA256.get(str(name))
                for name, source in identities
            ):
                raise ReleaseActionWorkerError(
                    "release approval function identity has drifted"
                )
            cursor.execute(
                "SELECT heartbeat_crawler_release_action_consumer(%s, %s)",
                (config.environment, config.owner),
            )
            row = cursor.fetchone()
        if not row or row[0] is None:
            raise ReleaseActionWorkerError("release action consumer heartbeat failed")
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def load_config() -> WorkerConfig:
    aliases = {"prod": "production", "stage": "staging"}
    raw_environment = str(os.getenv("ENVIRONMENT") or "").strip().lower()
    environment = aliases.get(raw_environment, raw_environment)
    if environment not in ENVIRONMENTS:
        raise ReleaseActionWorkerError("ENVIRONMENT is invalid")
    configured_root = str(
        os.getenv("OPS_CRAWLER_RELEASE_PUBLIC_ROOT", str(PUBLIC_ROOT))
    ).strip()
    if configured_root != str(PUBLIC_ROOT):
        raise ReleaseActionWorkerError("crawler release public root is not the fixed path")
    hostname = socket.gethostname().strip().lower().split(".", 1)[0]
    if not OWNER_COMPONENT.fullmatch(hostname):
        raise ReleaseActionWorkerError("release action worker hostname is invalid")
    owner = f"release-action@{hostname}:{os.getpid()}"
    return WorkerConfig(
        environment=environment,
        owner=owner,
        baseline_fresh_seconds=_bounded_environment_int(
            "OPS_CRAWLER_RELEASE_BASELINE_FRESH_SECONDS", BASELINE_FRESH_SECONDS, 30, 900
        ),
    )


def database_connection() -> Any:
    return psycopg2.connect(**release_admin_database_config())


def check_runtime(config: WorkerConfig, connection_factory: ConnectionFactory) -> None:
    if config.public_root != PUBLIC_ROOT or (
        os.name == "posix" and not config.public_root.is_absolute()
    ):
        raise ReleaseActionWorkerError("release action public root is not canonical")
    if os.name == "posix" and os.geteuid() != 0:
        raise ReleaseActionWorkerError("release action worker must run as root")
    release_admin._secure_directory(config.public_root, label="crawler release public root")
    release_admin._secure_directory(
        config.public_root / "artifacts", label="crawler artifact directory"
    )
    connection = connection_factory()
    try:
        _database_contract(connection, config.environment)
    finally:
        connection.close()


def run_once(
    config: WorkerConfig,
    connection_factory: ConnectionFactory = database_connection,
) -> str:
    queue_connection = connection_factory()
    try:
        requeued, reconciliation_required = reap_expired(
            queue_connection,
            environment=config.environment,
            max_attempts=config.max_attempts,
        )
        if requeued or reconciliation_required:
            logger.warning(
                "Reaped expired crawler release actions requeued=%s reconciliation_required=%s",
                requeued,
                reconciliation_required,
            )
        lease = claim_next(queue_connection, config)
    finally:
        queue_connection.close()
    if lease is None:
        return "idle"

    action_connection = connection_factory()
    completion: Completion | None = None
    try:
        reconciliation_missing = False
        with LeaseHeartbeat(
            connection_factory,
            lease,
            lease_seconds=config.lease_seconds,
            interval_seconds=config.heartbeat_seconds,
        ) as heartbeat:
            heartbeat.ensure_owned()
            if lease.reconcile_only:
                result = reconcile_action(action_connection, lease)
                reconciliation_missing = result is None
            else:
                result = dispatch_action(
                    action_connection,
                    lease,
                    public_root=config.public_root,
                    baseline_fresh_seconds=config.baseline_fresh_seconds,
                )
            heartbeat.ensure_owned()
        completion_connection = connection_factory()
        try:
            if reconciliation_missing:
                completion = complete_reconciliation_required(
                    completion_connection,
                    lease,
                    message=(
                        "read-only reconciliation found no exact committed release state; "
                        "operator review is required"
                    ),
                )
            else:
                assert result is not None
                completion = complete_success(completion_connection, lease, result)
        finally:
            completion_connection.close()
    except ActionRejected as exc:
        completion_connection = connection_factory()
        try:
            completion = complete_failure(
                completion_connection,
                lease,
                code=exc.code,
                message=str(exc),
            )
        finally:
            completion_connection.close()
    except release_admin.CrawlerReleaseAdminError as exc:
        probe_state, probe_value = _probe_committed_action(connection_factory, lease)
        completion_connection = connection_factory()
        try:
            if probe_state == "recovered":
                assert isinstance(probe_value, dict)
                completion = complete_success(completion_connection, lease, probe_value)
            elif probe_state == "conflict":
                assert isinstance(probe_value, ActionRejected)
                completion = complete_failure(
                    completion_connection,
                    lease,
                    code=probe_value.code,
                    message=str(probe_value),
                )
            elif probe_state == "unavailable":
                if lease.attempt_count >= config.max_attempts:
                    completion = defer_reconciliation_owned(
                        completion_connection,
                        lease,
                        max_attempts=config.max_attempts,
                    )
                else:
                    completion = retry_owned(
                        completion_connection,
                        lease,
                        max_attempts=config.max_attempts,
                    )
            else:
                completion = complete_failure(
                    completion_connection,
                    lease,
                    code="release_rejected",
                    message=str(exc),
                )
        finally:
            completion_connection.close()
    except LeaseLostError:
        logger.warning("Crawler release action lost its lease request_id=%s", lease.request_id)
        return "lease_lost"
    except Exception:
        logger.exception("Crawler release action failed transiently request_id=%s", lease.request_id)
        if lease.reconcile_only:
            completion_connection = connection_factory()
            try:
                completion = complete_reconciliation_required(
                    completion_connection,
                    lease,
                    message=(
                        "read-only reconciliation could not prove the committed release state; "
                        "operator review is required"
                    ),
                )
            finally:
                completion_connection.close()
            return completion.state if completion.accepted else "lease_lost"

        probe_state, probe_value = _probe_committed_action(connection_factory, lease)
        retry_connection = connection_factory()
        try:
            if probe_state == "recovered":
                assert isinstance(probe_value, dict)
                completion = complete_success(retry_connection, lease, probe_value)
            elif probe_state == "conflict":
                assert isinstance(probe_value, ActionRejected)
                completion = complete_failure(
                    retry_connection,
                    lease,
                    code=probe_value.code,
                    message=str(probe_value),
                )
            elif probe_state == "unavailable" and lease.attempt_count >= config.max_attempts:
                completion = defer_reconciliation_owned(
                    retry_connection,
                    lease,
                    max_attempts=config.max_attempts,
                )
            else:
                completion = retry_owned(
                    retry_connection,
                    lease,
                    max_attempts=config.max_attempts,
                )
        finally:
            retry_connection.close()
    finally:
        action_connection.close()
    if completion is None or not completion.accepted:
        return "lease_lost"
    return completion.state


def run_worker(
    config: WorkerConfig,
    *,
    once: bool = False,
    connection_factory: ConnectionFactory = database_connection,
) -> int:
    while RUNNING:
        try:
            heartbeat_connection = connection_factory()
            try:
                heartbeat_runtime(heartbeat_connection, config)
            finally:
                heartbeat_connection.close()
            state = run_once(config, connection_factory)
            if state != "idle":
                logger.info("Crawler release action iteration state=%s", state)
        except Exception:
            logger.exception("Crawler release action worker iteration failed")
            if once:
                return 1
        if once:
            return 0
        deadline = time.monotonic() + config.poll_seconds
        while RUNNING and time.monotonic() < deadline:
            time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
    return 0


def _stop(_signum: int, _frame: object) -> None:
    global RUNNING
    RUNNING = False


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Consume audited central crawler release action requests"
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--check", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config()
    if args.check:
        check_runtime(config, database_connection)
        return 0
    check_runtime(config, database_connection)
    return run_worker(config, once=args.once)


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("OPS_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _stop)
    raise SystemExit(main())
