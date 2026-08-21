"""Redacted crawler release views and an audited, unprivileged action queue."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from backend import models
from backend.crawler_control_database import get_crawler_control_db
from backend.ops.schemas import CrawlerReleaseActionRequest
from backend.ops.service import append_audit, current_environment, mapped_one, mapped_rows, sanitize_for_audit
from backend.routers.auth import rate_limit, require_ops_admin, require_ops_viewer


router = APIRouter(
    prefix="/api/ops/crawler-control",
    tags=["ops-crawler-control"],
    dependencies=[
        Depends(rate_limit("ops-crawler-control", 180, 60)),
        Depends(require_ops_viewer),
    ],
)

_CONTROL_TABLES = (
    "ops_crawler_release_artifacts",
    "ops_crawler_release_rollouts",
    "ops_crawler_worker_desired_state",
    "ops_crawler_rollout_worker_snapshots",
    "ops_crawler_release_reports",
    "ops_crawler_release_action_requests",
    "ops_crawler_api_bindings",
    "ops_crawler_release_action_approvals",
    "ops_crawler_release_approver_bindings",
    "ops_crawler_release_action_consumers",
    "ops_crawler_release_policy_contract",
    "ops_agents",
    "ops_audit_logs",
)
RELEASE_HEALTH_FRESH_SECONDS = 360
_ACTION_CAPABILITIES = {
    "build": {
        "available": False,
        "reason": "immutable_builder_evidence_handoff_not_implemented",
    },
    "register_artifact": {
        "available": False,
        "reason": "immutable_builder_evidence_handoff_not_implemented",
    },
    "create_canary": {
        "available": False,
        "reason": "independent_operator_approval_not_implemented",
    },
    "advance_rollout": {
        "available": False,
        "reason": "independent_operator_approval_not_implemented",
    },
    "pause_rollout": {
        "available": False,
        "reason": "independent_operator_approval_not_implemented",
    },
    "rollback_rollout": {
        "available": False,
        "reason": "independent_operator_approval_not_implemented",
    },
    "complete_rollback": {
        "available": False,
        "reason": "independent_operator_approval_not_implemented",
    },
}

_ROLLOUT_ACTIONS = frozenset(
    {
        "create_canary",
        "advance_rollout",
        "pause_rollout",
        "rollback_rollout",
        "complete_rollback",
    }
)
_RELEASE_APPROVAL_FUNCTION_SHA256 = {
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


def _action_capabilities(approval_ready: bool) -> dict[str, dict[str, Any]]:
    capabilities = {key: dict(value) for key, value in _ACTION_CAPABILITIES.items()}
    if approval_ready:
        for action in _ROLLOUT_ACTIONS:
            capabilities[action] = {"available": True, "reason": None}
    else:
        for action in _ROLLOUT_ACTIONS:
            capabilities[action] = {
                "available": False,
                "reason": "independent_operator_approval_unavailable",
            }
    return capabilities


def _page(
    items: list[dict[str, Any]],
    *,
    total: int,
    limit: int,
    offset: int,
    available: bool = True,
) -> dict[str, Any]:
    return {
        "available": available,
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def _control_schema_available(db: Session | None) -> bool:
    if db is None:
        return False
    try:
        rows = db.execute(
            text(
                """
                SELECT required.name, to_regclass('public.' || required.name) IS NOT NULL AS present
                FROM unnest(CAST(:required AS text[])) AS required(name)
                """
            ),
            {"required": list(_CONTROL_TABLES) + ["ops_crawler_control_database_marker"]},
        ).all()
        if len(rows) != len(_CONTROL_TABLES) + 1 or any(not bool(row[1]) for row in rows):
            db.rollback()
            return False
        marker = db.execute(
            text(
                """
                SELECT count(*) = 1
                   AND bool_and(singleton IS TRUE)
                   AND bool_and(database_name = current_database()::name)
                FROM ops_crawler_control_database_marker
                """
            )
        ).scalar()
        bound_environment = db.execute(
            text("SELECT current_crawler_api_environment()")
        ).scalar()
        db.rollback()
        return marker is True and bound_environment == current_environment()
    except (SQLAlchemyError, RuntimeError):
        db.rollback()
        return False


def _release_approval_available(db: Session | None) -> bool:
    if db is None:
        return False
    try:
        identities = db.execute(
            text(
                """
                SELECT procedure.proname, procedure.prosrc
                FROM pg_proc procedure
                JOIN pg_namespace namespace_row
                  ON namespace_row.oid = procedure.pronamespace
                WHERE namespace_row.nspname = 'public'
                  AND procedure.proname = ANY(CAST(:names AS text[]))
                ORDER BY procedure.proname
                """
            ),
            {"names": sorted(_RELEASE_APPROVAL_FUNCTION_SHA256)},
        ).all()
        if len(identities) != len(_RELEASE_APPROVAL_FUNCTION_SHA256) or any(
            hashlib.sha256(
                str(source).replace("\r\n", "\n").replace("\r", "\n").strip().encode("utf-8")
            ).hexdigest()
            != _RELEASE_APPROVAL_FUNCTION_SHA256.get(str(name))
            for name, source in identities
        ):
            db.rollback()
            return False
        valid = db.execute(
            text("SELECT crawler_release_action_runtime_is_ready(:environment)"),
            {"environment": current_environment()},
        ).scalar()
        db.rollback()
        return valid is True
    except (SQLAlchemyError, RuntimeError):
        db.rollback()
        return False


def _read_unavailable_page(limit: int, offset: int) -> dict[str, Any]:
    return _page([], total=0, limit=limit, offset=offset, available=False)


def _sanitized_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [sanitize_for_audit(item) for item in items]


@router.get("/summary")
def release_summary(
    db: Session | None = Depends(get_crawler_control_db),
) -> dict[str, Any]:
    if not _control_schema_available(db):
        return {"available": False, "action_capabilities": _action_capabilities(False)}
    assert db is not None
    try:
        summary = mapped_one(
            db.execute(
                text(
                    """
                    SELECT
                        (SELECT count(artifact_digest) FROM ops_crawler_release_artifacts)
                            AS artifact_count,
                        (SELECT count(worker_key) FROM ops_crawler_worker_desired_state
                         WHERE environment = :environment) AS worker_count,
                        (SELECT count(id) FROM ops_crawler_release_action_requests
                         WHERE environment = :environment AND status IN ('queued', 'leased'))
                            AS pending_action_count,
                        (SELECT count(desired.worker_key)
                         FROM ops_crawler_worker_desired_state desired
                         LEFT JOIN LATERAL (
                             SELECT report.rollout_id, report.status,
                                    report.desired_generation, report.artifact_digest,
                                    report.code_version, report.config_revision,
                                    report.health, report.reported_at
                             FROM ops_crawler_release_reports report
                             WHERE report.environment = desired.environment
                               AND report.worker_key = desired.worker_key
                             ORDER BY report.reported_at DESC, report.created_at DESC, report.id DESC
                             LIMIT 1
                         ) latest ON TRUE
                         LEFT JOIN ops_agents agent ON agent.id = desired.agent_id
                         WHERE desired.environment = :environment
                           AND desired.desired_status = 'active'
                           AND (
                               latest.status IS NULL
                               OR latest.status NOT IN ('ready', 'rolled_back')
                               OR latest.rollout_id IS DISTINCT FROM desired.rollout_id
                               OR latest.desired_generation IS DISTINCT FROM desired.generation
                               OR latest.artifact_digest IS DISTINCT FROM desired.artifact_digest
                               OR latest.code_version IS DISTINCT FROM desired.code_version
                               OR latest.config_revision IS DISTINCT FROM desired.config_revision
                               OR latest.health->'healthy' IS DISTINCT FROM 'true'::jsonb
                               OR latest.reported_at < clock_timestamp()
                                   - (:fresh_seconds * interval '1 second')
                               OR agent.status IS DISTINCT FROM 'healthy'
                               OR agent.maintenance_mode IS NOT FALSE
                               OR agent.last_seen_at IS NULL
                               OR agent.last_seen_at < clock_timestamp()
                                   - (:fresh_seconds * interval '1 second')
                           )) AS unhealthy_worker_count
                    """
                ),
                {
                    "environment": current_environment(),
                    "fresh_seconds": RELEASE_HEALTH_FRESH_SECONDS,
                },
            )
        ) or {}
        rollout = mapped_one(
            db.execute(
                text(
                    """
                    SELECT id::text, environment, rollout_epoch, artifact_digest,
                           previous_artifact_digest, status, strategy,
                           requested_worker_count, worker_snapshot_required,
                           created_at, started_at, finished_at
                    FROM ops_crawler_release_rollouts
                    WHERE environment = :environment
                    ORDER BY rollout_epoch DESC
                    LIMIT 1
                    """
                ),
                {"environment": current_environment()},
            )
        )
        db.rollback()
        return {
            "available": True,
            "environment": current_environment(),
            "artifact_count": int(summary.get("artifact_count") or 0),
            "worker_count": int(summary.get("worker_count") or 0),
            "unhealthy_worker_count": int(summary.get("unhealthy_worker_count") or 0),
            "pending_action_count": int(summary.get("pending_action_count") or 0),
            "report_fresh_seconds": RELEASE_HEALTH_FRESH_SECONDS,
            "active_rollout": sanitize_for_audit(rollout) if rollout else None,
            "action_capabilities": _action_capabilities(_release_approval_available(db)),
        }
    except SQLAlchemyError:
        db.rollback()
        return {"available": False, "action_capabilities": _action_capabilities(False)}


@router.get("/artifacts")
def release_artifacts(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    db: Session | None = Depends(get_crawler_control_db),
) -> dict[str, Any]:
    if not _control_schema_available(db):
        return _read_unavailable_page(limit, offset)
    assert db is not None
    try:
        total = int(
            db.execute(text("SELECT count(artifact_digest) FROM ops_crawler_release_artifacts")).scalar()
            or 0
        )
        items = mapped_rows(
            db.execute(
                text(
                    """
                    SELECT artifact_digest, code_version, config_revision,
                           size_bytes, key_id, metadata, created_at
                    FROM ops_crawler_release_artifacts
                    ORDER BY created_at DESC, artifact_digest
                    LIMIT :limit OFFSET :offset
                    """
                ),
                {"limit": limit, "offset": offset},
            )
        )
        db.rollback()
        return _page(_sanitized_items(items), total=total, limit=limit, offset=offset)
    except SQLAlchemyError:
        db.rollback()
        return _read_unavailable_page(limit, offset)


@router.get("/rollouts")
def release_rollouts(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    db: Session | None = Depends(get_crawler_control_db),
) -> dict[str, Any]:
    if not _control_schema_available(db):
        return _read_unavailable_page(limit, offset)
    assert db is not None
    parameters = {"environment": current_environment(), "limit": limit, "offset": offset}
    try:
        total = int(
            db.execute(
                text(
                    "SELECT count(id) FROM ops_crawler_release_rollouts "
                    "WHERE environment = :environment"
                ),
                parameters,
            ).scalar()
            or 0
        )
        items = mapped_rows(
            db.execute(
                text(
                    """
                    SELECT id::text, environment, rollout_epoch, artifact_digest,
                           previous_artifact_digest, status, strategy,
                           requested_worker_count, requested_by::text,
                           worker_snapshot_required,
                           created_at, started_at, finished_at
                    FROM ops_crawler_release_rollouts
                    WHERE environment = :environment
                    ORDER BY rollout_epoch DESC
                    LIMIT :limit OFFSET :offset
                    """
                ),
                parameters,
            )
        )
        db.rollback()
        return _page(_sanitized_items(items), total=total, limit=limit, offset=offset)
    except SQLAlchemyError:
        db.rollback()
        return _read_unavailable_page(limit, offset)


@router.get("/rollouts/{rollout_id}")
def release_rollout_detail(
    rollout_id: UUID,
    db: Session | None = Depends(get_crawler_control_db),
) -> dict[str, Any]:
    if not _control_schema_available(db):
        return {
            "available": False,
            "item": None,
            "workers": [],
            "worker_history_available": None,
            "worker_history_reason": "crawler_control_schema_unavailable",
        }
    assert db is not None
    try:
        item = mapped_one(
            db.execute(
                text(
                    """
                    SELECT id::text, environment, rollout_epoch, artifact_digest,
                           previous_artifact_digest, status, strategy,
                           requested_worker_count, requested_by::text,
                           worker_snapshot_required,
                           created_at, started_at, finished_at
                    FROM ops_crawler_release_rollouts
                    WHERE id = :rollout_id AND environment = :environment
                    """
                ),
                {"rollout_id": str(rollout_id), "environment": current_environment()},
            )
        )
        if item is None:
            db.rollback()
            raise HTTPException(status_code=404, detail="Crawler rollout not found")
        workers = _rollout_worker_rows(db, rollout_id=str(rollout_id))
        expected_workers = int(item.get("requested_worker_count") or 0)
        history_required = item.get("worker_snapshot_required") is True
        worker_history_available = (
            len(workers) == expected_workers if history_required else False
        )
        db.rollback()
        return {
            "available": True,
            "item": sanitize_for_audit(item),
            "workers": _sanitized_items(workers) if worker_history_available else [],
            "worker_history_available": worker_history_available,
            "worker_history_reason": (
                None
                if worker_history_available
                else (
                    "rollout_worker_history_predates_snapshot_contract"
                    if history_required is False
                    else "rollout_worker_history_incomplete"
                )
            ),
        }
    except HTTPException:
        raise
    except SQLAlchemyError:
        db.rollback()
        return {
            "available": False,
            "item": None,
            "workers": [],
            "worker_history_available": None,
            "worker_history_reason": "crawler_control_query_failed",
        }


def _worker_rows(db: Session, *, rollout_id: str | None = None) -> list[dict[str, Any]]:
    rollout_filter = "AND desired.rollout_id = :rollout_id" if rollout_id else ""
    return mapped_rows(
        db.execute(
            text(
                f"""
                SELECT desired.worker_key, desired.agent_id::text,
                       agent.name AS agent_name, agent.hostname, agent.status AS agent_status,
                       agent.maintenance_mode, agent.last_seen_at,
                       desired.rollout_id::text, desired.generation,
                       desired.desired_status, desired.cohort,
                       desired.artifact_digest, desired.code_version,
                       desired.config_revision, desired.not_before, desired.updated_at,
                       latest.status AS report_status,
                       latest.rollout_id::text AS reported_rollout_id,
                       latest.desired_generation AS reported_generation,
                       latest.artifact_digest AS reported_artifact_digest,
                       latest.code_version AS reported_code_version,
                       latest.config_revision AS reported_config_revision,
                       latest.health AS reported_health,
                       latest.error_code, latest.reported_at,
                       (
                           latest.status IN ('ready', 'rolled_back')
                           AND latest.rollout_id = desired.rollout_id
                           AND latest.desired_generation = desired.generation
                           AND latest.artifact_digest = desired.artifact_digest
                           AND latest.code_version = desired.code_version
                           AND latest.config_revision = desired.config_revision
                           AND latest.health->'healthy' = 'true'::jsonb
                           AND latest.reported_at >= clock_timestamp()
                               - (:fresh_seconds * interval '1 second')
                           AND agent.status = 'healthy'
                           AND agent.maintenance_mode IS FALSE
                           AND agent.last_seen_at IS NOT NULL
                           AND agent.last_seen_at >= clock_timestamp()
                               - (:fresh_seconds * interval '1 second')
                       ) AS release_converged
                FROM ops_crawler_worker_desired_state desired
                LEFT JOIN ops_agents agent ON agent.id = desired.agent_id
                LEFT JOIN LATERAL (
                    SELECT report.rollout_id, report.status,
                           report.desired_generation, report.artifact_digest, report.code_version,
                           report.config_revision, report.health,
                           report.error_code, report.reported_at
                    FROM ops_crawler_release_reports report
                    WHERE report.environment = desired.environment
                      AND report.worker_key = desired.worker_key
                    ORDER BY report.reported_at DESC, report.created_at DESC, report.id DESC
                    LIMIT 1
                ) latest ON TRUE
                WHERE desired.environment = :environment
                {rollout_filter}
                ORDER BY desired.cohort, desired.worker_key
                """
            ),
            {
                "environment": current_environment(),
                "rollout_id": rollout_id,
                "fresh_seconds": RELEASE_HEALTH_FRESH_SECONDS,
            },
        )
    )


def _rollout_worker_rows(db: Session, *, rollout_id: str) -> list[dict[str, Any]]:
    """Return the immutable final roster/evidence for one historical rollout."""

    return mapped_rows(
        db.execute(
            text(
                """
                WITH snapshot AS (
                    SELECT DISTINCT ON (history.worker_key)
                           history.environment, history.rollout_id, history.generation,
                           history.worker_key, history.agent_id, history.desired_status,
                           history.cohort, history.artifact_digest, history.code_version,
                           history.config_revision, history.created_at
                    FROM ops_crawler_rollout_worker_snapshots history
                    WHERE history.environment = :environment
                      AND history.rollout_id = :rollout_id
                    ORDER BY history.worker_key, history.generation DESC
                )
                SELECT snapshot.worker_key, snapshot.agent_id::text,
                       agent.name AS agent_name, agent.hostname,
                       agent.status AS agent_status, agent.maintenance_mode,
                       agent.last_seen_at, snapshot.rollout_id::text,
                       snapshot.generation, snapshot.desired_status, snapshot.cohort,
                       snapshot.artifact_digest, snapshot.code_version,
                       snapshot.config_revision,
                       snapshot.created_at AS snapshot_created_at,
                       latest.status AS report_status,
                       latest.rollout_id::text AS reported_rollout_id,
                       latest.desired_generation AS reported_generation,
                       latest.artifact_digest AS reported_artifact_digest,
                       latest.code_version AS reported_code_version,
                       latest.config_revision AS reported_config_revision,
                       latest.health AS reported_health,
                       latest.error_code, latest.reported_at,
                       (
                           latest.status IN ('ready', 'rolled_back')
                           AND latest.rollout_id = snapshot.rollout_id
                           AND latest.desired_generation = snapshot.generation
                           AND latest.artifact_digest = snapshot.artifact_digest
                           AND latest.code_version = snapshot.code_version
                           AND latest.config_revision = snapshot.config_revision
                           AND latest.health->'healthy' = 'true'::jsonb
                       ) AS historical_identity_matched,
                       (current_desired.worker_key IS NOT NULL) AS is_current_desired,
                       (
                           current_desired.worker_key IS NOT NULL
                           AND latest.status IN ('ready', 'rolled_back')
                           AND latest.rollout_id = snapshot.rollout_id
                           AND latest.desired_generation = snapshot.generation
                           AND latest.artifact_digest = snapshot.artifact_digest
                           AND latest.code_version = snapshot.code_version
                           AND latest.config_revision = snapshot.config_revision
                           AND latest.health->'healthy' = 'true'::jsonb
                           AND latest.reported_at >= clock_timestamp()
                               - (:fresh_seconds * interval '1 second')
                           AND agent.status = 'healthy'
                           AND agent.maintenance_mode IS FALSE
                           AND agent.last_seen_at IS NOT NULL
                           AND agent.last_seen_at >= clock_timestamp()
                               - (:fresh_seconds * interval '1 second')
                       ) AS release_converged
                FROM snapshot
                LEFT JOIN ops_agents agent ON agent.id = snapshot.agent_id
                LEFT JOIN ops_crawler_worker_desired_state current_desired
                  ON current_desired.environment = snapshot.environment
                 AND current_desired.worker_key = snapshot.worker_key
                 AND current_desired.agent_id = snapshot.agent_id
                 AND current_desired.rollout_id = snapshot.rollout_id
                 AND current_desired.generation = snapshot.generation
                 AND current_desired.desired_status = snapshot.desired_status
                 AND current_desired.cohort = snapshot.cohort
                 AND current_desired.artifact_digest = snapshot.artifact_digest
                 AND current_desired.code_version = snapshot.code_version
                 AND current_desired.config_revision = snapshot.config_revision
                LEFT JOIN LATERAL (
                    SELECT report.rollout_id, report.status,
                           report.desired_generation, report.artifact_digest,
                           report.code_version, report.config_revision, report.health,
                           report.error_code, report.reported_at
                    FROM ops_crawler_release_reports report
                    WHERE report.environment = snapshot.environment
                      AND report.rollout_id = snapshot.rollout_id
                      AND report.worker_key = snapshot.worker_key
                    ORDER BY report.reported_at DESC, report.created_at DESC, report.id DESC
                    LIMIT 1
                ) latest ON TRUE
                ORDER BY snapshot.cohort, snapshot.worker_key
                """
            ),
            {
                "environment": current_environment(),
                "rollout_id": rollout_id,
                "fresh_seconds": RELEASE_HEALTH_FRESH_SECONDS,
            },
        )
    )


@router.get("/workers")
def release_workers(
    db: Session | None = Depends(get_crawler_control_db),
) -> dict[str, Any]:
    if not _control_schema_available(db):
        return {"available": False, "items": []}
    assert db is not None
    try:
        items = _worker_rows(db)
        db.rollback()
        return {"available": True, "items": _sanitized_items(items)}
    except SQLAlchemyError:
        db.rollback()
        return {"available": False, "items": []}


def _action_select() -> str:
    return """
        SELECT id::text, action, environment, status, idempotency_key,
               expected_generation, request_payload, requested_by::text,
               requester_role, reason, confirmation, request_digest,
               attempt_count, reconcile_only,
               leased_until, result, error_code, error_message,
               created_at, started_at, finished_at, updated_at,
               (SELECT approval.receipt_id::text
                FROM ops_crawler_release_action_approvals approval
                WHERE approval.request_id = ops_crawler_release_action_requests.id)
                   AS approval_receipt_id,
               (SELECT approval.approver_login::text
                FROM ops_crawler_release_action_approvals approval
                WHERE approval.request_id = ops_crawler_release_action_requests.id)
                   AS approval_database_login,
               (SELECT approval.operator_identity
                FROM ops_crawler_release_action_approvals approval
                WHERE approval.request_id = ops_crawler_release_action_requests.id)
                   AS approval_operator_label,
               (SELECT approval.approval_reason
                FROM ops_crawler_release_action_approvals approval
                WHERE approval.request_id = ops_crawler_release_action_requests.id)
                   AS approval_reason,
               (SELECT approval.approved_at
                FROM ops_crawler_release_action_approvals approval
                WHERE approval.request_id = ops_crawler_release_action_requests.id)
                   AS approved_at,
               (SELECT approval.expires_at
                FROM ops_crawler_release_action_approvals approval
                WHERE approval.request_id = ops_crawler_release_action_requests.id)
                   AS approval_expires_at,
               CASE
                   WHEN NOT EXISTS (
                       SELECT 1 FROM ops_crawler_release_action_approvals approval
                       WHERE approval.request_id = ops_crawler_release_action_requests.id
                   ) THEN 'pending'
                   WHEN started_at IS NULL AND EXISTS (
                       SELECT 1 FROM ops_crawler_release_action_approvals approval
                       WHERE approval.request_id = ops_crawler_release_action_requests.id
                         AND approval.expires_at <= clock_timestamp()
                   ) THEN 'expired'
                   ELSE 'approved'
               END AS approval_status
        FROM ops_crawler_release_action_requests
    """


@router.get("/actions")
def release_actions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    db: Session | None = Depends(get_crawler_control_db),
) -> dict[str, Any]:
    if not _control_schema_available(db):
        return _read_unavailable_page(limit, offset)
    assert db is not None
    parameters = {"environment": current_environment(), "limit": limit, "offset": offset}
    try:
        total = int(
            db.execute(
                text(
                    "SELECT count(id) FROM ops_crawler_release_action_requests "
                    "WHERE environment = :environment"
                ),
                parameters,
            ).scalar()
            or 0
        )
        items = mapped_rows(
            db.execute(
                text(
                    _action_select()
                    + " WHERE environment = :environment ORDER BY created_at DESC, id DESC "
                    "LIMIT :limit OFFSET :offset"
                ),
                parameters,
            )
        )
        db.rollback()
        return _page(_sanitized_items(items), total=total, limit=limit, offset=offset)
    except SQLAlchemyError:
        db.rollback()
        return _read_unavailable_page(limit, offset)


@router.get("/actions/{action_id}")
def release_action_detail(
    action_id: UUID,
    db: Session | None = Depends(get_crawler_control_db),
) -> dict[str, Any]:
    if not _control_schema_available(db):
        return {"available": False, "item": None}
    assert db is not None
    try:
        item = mapped_one(
            db.execute(
                text(_action_select() + " WHERE id = :action_id AND environment = :environment"),
                {"action_id": str(action_id), "environment": current_environment()},
            )
        )
        db.rollback()
        if item is None:
            raise HTTPException(status_code=404, detail="Crawler release action not found")
        return {"available": True, "item": sanitize_for_audit(item)}
    except HTTPException:
        raise
    except SQLAlchemyError:
        db.rollback()
        return {"available": False, "item": None}


def _semantic_action_request(payload: CrawlerReleaseActionRequest, requested_by: UUID) -> dict[str, Any]:
    return {
        "action": payload.action,
        "environment": payload.environment,
        "idempotency_key": payload.idempotency_key,
        "expected_generation": payload.expected_generation,
        "request_payload": payload.request_payload(),
        "requested_by": str(requested_by),
        "requester_role": "admin",
        "reason": payload.reason,
        "confirmation": payload.confirmation,
    }


def _same_action_request(existing: dict[str, Any], requested: dict[str, Any]) -> bool:
    return all(existing.get(key) == value for key, value in requested.items())


@router.post("/actions", status_code=status.HTTP_202_ACCEPTED)
def request_release_action(
    payload: CrawlerReleaseActionRequest,
    request: Request,
    user: models.User = Depends(require_ops_admin),
    db: Session | None = Depends(get_crawler_control_db),
) -> dict[str, Any]:
    capability = _action_capabilities(_release_approval_available(db))[payload.action]
    if not capability["available"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": capability["reason"],
                "message": "This release action is not yet safe to execute",
            },
        )
    if payload.environment != current_environment():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "crawler_release_environment_mismatch",
                "message": "Release request environment differs from this Ops API environment",
            },
        )
    if not _control_schema_available(db):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "crawler_control_unavailable",
                "message": "The dedicated crawler control connection or action queue is unavailable",
            },
        )
    assert db is not None
    semantic = _semantic_action_request(payload, user.id)
    try:
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
            {
                "identity": (
                    f"crawler-release-request:{payload.environment}:"
                    f"{user.id}:{payload.idempotency_key}"
                )
            },
        )
        existing = mapped_one(
            db.execute(
                text(
                    _action_select()
                    + " WHERE environment = :environment AND requested_by = :requested_by "
                    "AND idempotency_key = :idempotency_key"
                ),
                semantic,
            )
        )
        if existing is not None:
            if not _same_action_request(existing, semantic):
                db.rollback()
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "crawler_release_idempotency_conflict",
                        "message": "Idempotency key was already used for a different release request",
                    },
                )
            db.rollback()
            return {"available": True, "replayed": True, "item": sanitize_for_audit(existing)}

        item = mapped_one(
            db.execute(
                text(
                    """
                    INSERT INTO ops_crawler_release_action_requests (
                        action, environment, idempotency_key, expected_generation,
                        request_payload, requested_by, requester_role, reason, confirmation
                    ) VALUES (
                        :action, :environment, :idempotency_key, :expected_generation,
                        CAST(:request_payload AS jsonb), :requested_by, :requester_role,
                        :reason, :confirmation
                    )
                    RETURNING id::text, action, environment, status, idempotency_key,
                              expected_generation, request_payload, requested_by::text,
                              requester_role, reason, confirmation, request_digest,
                              attempt_count, reconcile_only,
                              leased_until, result, error_code, error_message,
                              created_at, started_at, finished_at, updated_at,
                              NULL::text AS approval_receipt_id,
                              NULL::text AS approval_database_login,
                              NULL::text AS approval_operator_label,
                              NULL::timestamptz AS approved_at,
                              NULL::timestamptz AS approval_expires_at,
                              'pending'::text AS approval_status
                    """
                ),
                {**semantic, "request_payload": json.dumps(semantic["request_payload"], sort_keys=True)},
            )
        )
        assert item is not None
        append_audit(
            db,
            request,
            user_id=None,
            action="crawler_release_action.request",
            resource_type="crawler_release_action",
            resource_id=item["id"],
            after_data={
                "requester_id": str(user.id),
                "requester_role": "admin",
                "action": payload.action,
                "environment": payload.environment,
                "expected_generation": payload.expected_generation,
                "idempotency_key": payload.idempotency_key,
                "request_payload": payload.request_payload(),
                "reason": payload.reason,
            },
        )
        db.commit()
        return {"available": True, "replayed": False, "item": sanitize_for_audit(item)}
    except HTTPException:
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "crawler_release_request_conflict",
                "message": "Release request conflicted with current control-plane state",
            },
        ) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "crawler_control_unavailable",
                "message": "Crawler release request could not be durably queued",
            },
        ) from exc
