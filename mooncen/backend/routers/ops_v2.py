from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import socket
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend import models
from backend.database import SessionLocal, get_db
from backend.observability import runtime_metrics
from backend.ops.schemas import (
    ContainerBuildRequest,
    ContainerNativeRollbackRequest,
    ContainerPromotionRequest,
    ContainerRollbackRequest,
    ContainerValidationRequest,
    CrawlerRunRequest,
    DeploymentRequest,
    IssueActionRequest,
    JobActionRequest,
    ParserProbeRequest,
    QualityScanRequest,
)
from backend.ops.region_collection import get_region_collection_snapshot
from backend.ops.service import (
    ACTIVE_JOB_STATUSES,
    TERMINAL_JOB_STATUSES,
    add_job_log,
    append_audit,
    current_environment,
    deployment_heartbeat_lease_seconds,
    enqueue_job,
    local_crawler_runtime_enabled,
    mapped_one,
    mapped_rows,
    require_ops_schema,
    sanitize_for_audit,
    table_exists,
)
from backend.readiness import OPS_API_READINESS_QUERIES, assert_database_ready
from backend.routers.auth import (
    ops_role_for_user,
    rate_limit,
    require_ops_admin,
    require_ops_operator,
    require_ops_viewer,
)
from ops_agent.crawler_registry import (
    CrawlerProviderRegistryError,
    resolve_crawler_provider_execution,
    reviewed_crawler_providers,
)
from ops_agent.container_deployment import (
    ContainerDeploymentError,
    container_runtime_cas,
    container_transport_service_boundary_ready,
    read_container_controller_status,
)
from ops_agent.deployment_registry import deployment_readiness, reviewed_target
from ops_agent.production_topology import load_production_topology
from service_group import (
    CULTURE_CENTER_PROVIDERS,
    LOCAL_GOVERNMENT_EDUCATION_BRANCH_TOKENS,
    LOCAL_GOVERNMENT_EDUCATION_EXCLUDED_FACILITY_TOKENS,
    LOCAL_GOVERNMENT_EDUCATION_OFFICE_TOKEN_RULES,
    PUBLIC_NON_ADMIN_EXPERIENCE_SOURCE_GROUPS,
)
from tools.ops_redaction import redact_text
from tools.standard_category_mapper import (
    MOJIBAKE_HARD_MARKERS,
    MOJIBAKE_SOFT_MARKERS,
    looks_corrupted_category,
)
from utils.url_security import safe_external_http_url


logger = logging.getLogger(__name__)
_OBSERVED_HOST_PATTERN = re.compile(r"^[A-Za-z0-9:][A-Za-z0-9._:-]{0,252}$")

router = APIRouter(
    prefix="/api/ops",
    tags=["ops-v2"],
    dependencies=[
        Depends(rate_limit("ops-api-v2", 240, 60)),
        Depends(require_ops_viewer),
    ],
)


@router.get("/runtime-metrics")
def api_runtime_metrics(
    window_seconds: int = Query(default=900, ge=60, le=86_400),
) -> dict[str, Any]:
    """Return bounded, current-worker API latency and exception telemetry."""

    return runtime_metrics(window_seconds)


_BRANCH_SCOPE_TEXT_SQL = (
    "lower(concat_ws(' ', COALESCE(b.name, ''), "
    "COALESCE(b.facility_type, ''), COALESCE(b.facility_category, ''), "
    "COALESCE(b.basic_info #>> '{education_institution}', ''), "
    "COALESCE(b.basic_info #>> '{operator_address_backfill,target_name}', ''), "
    "COALESCE(b.basic_info #>> '{operator_address_backfill,matched_name}', '')))"
)


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _branch_text_contains_token_sql(
    token: str,
    *,
    false_fragments: tuple[str, ...] = (),
) -> str:
    false_fragment_sql = ""
    if false_fragments:
        patterns = ", ".join(_sql_literal(f"%{fragment.lower()}%") for fragment in false_fragments)
        false_fragment_sql = f" AND NOT ({_BRANCH_SCOPE_TEXT_SQL} LIKE ANY (ARRAY[{patterns}]::text[]))"
    return f"({_BRANCH_SCOPE_TEXT_SQL} LIKE {_sql_literal(f'%{token.lower()}%')}{false_fragment_sql})"


def _branch_text_contains_any_sql(tokens: tuple[str, ...]) -> str:
    patterns = ", ".join(_sql_literal(f"%{token.lower()}%") for token in tokens)
    return f"({_BRANCH_SCOPE_TEXT_SQL} LIKE ANY (ARRAY[{patterns}]::text[]))"


_LOCAL_GOVERNMENT_OFFICE_SQL = " OR ".join(
    [
        *(_branch_text_contains_token_sql(token) for token in LOCAL_GOVERNMENT_EDUCATION_BRANCH_TOKENS),
        *(
            _branch_text_contains_token_sql(
                token,
                false_fragments=tuple(false_fragments),
            )
            for token, false_fragments in LOCAL_GOVERNMENT_EDUCATION_OFFICE_TOKEN_RULES
        ),
        (
            "(btrim(COALESCE(b.basic_info #>> "
            "'{education_institution}', '')) "
            "~ '^[가-힣0-9 ]{1,40}(시|군|구|읍|면|동)$')"
        ),
    ]
)
_EXCLUDED_EDUCATION_FACILITY_SQL = _branch_text_contains_any_sql(LOCAL_GOVERNMENT_EDUCATION_EXCLUDED_FACILITY_TOKENS)
LOCAL_GOVERNMENT_EDUCATION_BRANCH_SQL = (
    f"(({_LOCAL_GOVERNMENT_OFFICE_SQL}) AND NOT ({_EXCLUDED_EDUCATION_FACILITY_SQL}))"
)
_NON_ADMIN_EXPERIENCE_SOURCE_SQL = ", ".join(
    _sql_literal(source_group) for source_group in sorted(PUBLIC_NON_ADMIN_EXPERIENCE_SOURCE_GROUPS)
)
_CULTURE_CENTER_PROVIDER_SQL = ", ".join(_sql_literal(provider) for provider in sorted(CULTURE_CENTER_PROVIDERS))
_CULTURE_CENTER_COURSE_SQL = f"c.provider IN ({_CULTURE_CENTER_PROVIDER_SQL})"
_NON_ADMIN_EXPERIENCE_INSTITUTION_SQL = f"""
(
    c.provider = 'CULTURE_FACILITY'
    OR (
        NOT {LOCAL_GOVERNMENT_EDUCATION_BRANCH_SQL}
        AND (
            c.source_group IN ({_NON_ADMIN_EXPERIENCE_SOURCE_SQL})
            OR b.provider = 'CULTURE_FACILITY'
            OR b.facility_source IS NOT NULL
            OR b.facility_service_group = '체험'
            OR b.facility_collection_category = '체험'
            OR ({_EXCLUDED_EDUCATION_FACILITY_SQL})
        )
    )
)
"""

CONTENT_TYPE_SQL = f"""
CASE
    WHEN {_CULTURE_CENTER_COURSE_SQL} THEN 'culture_center'
    WHEN c.service_group = '체험' THEN 'experience'
    WHEN c.service_group = '공공강좌'
         AND {LOCAL_GOVERNMENT_EDUCATION_BRANCH_SQL}
      THEN 'education'
    WHEN {_NON_ADMIN_EXPERIENCE_INSTITUTION_SQL}
      THEN 'experience'
    ELSE 'unknown'
END
"""

MAJOR_CATEGORY_SQL = f"""
CASE
    WHEN {_CULTURE_CENTER_COURSE_SQL} THEN '문화센터'
    WHEN c.service_group = '체험' THEN '체험'
    WHEN c.service_group = '공공강좌'
         AND {LOCAL_GOVERNMENT_EDUCATION_BRANCH_SQL}
      THEN '교육'
    WHEN {_NON_ADMIN_EXPERIENCE_INSTITUTION_SQL}
      THEN '체험'
    ELSE '기타'
END
"""

_CATEGORY_HARD_DAMAGE_MARKERS = "".join(MOJIBAKE_HARD_MARKERS)
_CATEGORY_SOFT_DAMAGE_MARKERS = "".join(MOJIBAKE_SOFT_MARKERS)


def _deployment_agent(db: Session) -> dict[str, Any] | None:
    if not table_exists(db, "ops_agents"):
        return None
    return mapped_one(
        db.execute(
            text(
                """
                SELECT id::text, name, hostname, status, last_seen_at
                FROM ops_agents
                WHERE environment = :environment
                  AND status = 'healthy'
                  AND last_seen_at >= CURRENT_TIMESTAMP - INTERVAL '2 minutes'
                  AND capabilities @> CAST(:capability AS jsonb)
                  AND (:required_hostname = '' OR hostname = :required_hostname)
                ORDER BY last_seen_at DESC
                LIMIT 1
                """
            ),
            {
                "environment": current_environment(),
                "capability": json.dumps(["deployment_queue"]),
                "required_hostname": os.getenv("OPS_DEPLOY_REQUIRED_AGENT_HOSTNAME", "").strip(),
            },
        )
    )


def _container_deployment_agent(db: Session) -> dict[str, Any] | None:
    if not table_exists(db, "ops_agents"):
        return None
    return mapped_one(
        db.execute(
            text(
                """
                SELECT id::text, name, hostname, status, last_seen_at
                FROM ops_agents
                WHERE environment = :environment
                  AND status = 'healthy'
                  AND last_seen_at >= CURRENT_TIMESTAMP - INTERVAL '2 minutes'
                  AND capabilities @> CAST(:capability AS jsonb)
                  AND hostname = :required_hostname
                ORDER BY last_seen_at DESC
                LIMIT 1
                """
            ),
            {
                "environment": current_environment(),
                "capability": json.dumps(["container_deployment"]),
                "required_hostname": _CONTAINER_EXECUTOR_HOSTNAME,
            },
        )
    )


def _deployment_readiness_payload(db: Session) -> dict[str, Any]:
    readiness = deployment_readiness()
    agent = None
    reasons = list(readiness.get("reasons") or [])
    reasons.append(
        {
            "code": "native_deployment_operator_only",
            "message": (
                "네이티브 배포는 long-lived 서비스 키로 실행하지 않습니다. "
                "an2p의 신뢰된 운영자가 Tailscale 대화형 경로에서만 수행합니다."
            ),
        }
    )
    container_runtime_blocked = False
    if table_exists(db, "ops_container_releases"):
        guard = mapped_one(
            db.execute(
                text(
                    """
                    SELECT
                        (
                            SELECT count(*)
                            FROM ops_deployments deployment
                            WHERE deployment.environment = :environment
                              AND deployment.target_name = :target_name
                              AND deployment.deployment_mode = 'container'
                              AND deployment.deployment_status = 'success'
                        ) AS successful_count,
                        (
                            SELECT count(*)
                            FROM ops_jobs job
                            WHERE job.environment = :environment
                              AND job.target_key = :target_key
                              AND job.job_type = 'deployment'
                              AND job.parameters->>'deployment_mode' = 'container'
                              AND job.status IN ('queued', 'assigned', 'running')
                        ) AS active_count
                    """
                ),
                {
                    "environment": current_environment(),
                    "target_name": _CONTAINER_PRODUCTION_TARGET,
                    "target_key": f"deployment:{_CONTAINER_PRODUCTION_TARGET}",
                },
            )
        ) or {"successful_count": 0, "active_count": 0}
        live_status = read_container_controller_status(timeout_seconds=10)
        active_count = int(guard.get("active_count") or 0)
        successful_count = int(guard.get("successful_count") or 0)
        container_runtime_blocked = bool(
            active_count
            or (
                live_status is not None
                and (
                    live_status.get("state") is not None
                    or live_status.get("transaction") is not None
                    or live_status.get("native_intent") is not None
                )
            )
            or (live_status is None and successful_count)
        )
        if container_runtime_blocked:
            reasons.append(
                {
                    "code": "native_deploy_blocked_by_container_runtime",
                    "message": (
                        "운영 container runtime/transaction 또는 확인 불가능한 container 이력이 "
                        "있어 네이티브 배포를 차단했습니다."
                    ),
                }
            )
    readiness["agent"] = agent
    readiness["can_deploy"] = False
    readiness["reasons"] = reasons
    readiness["deployment_mode"] = "native"
    readiness["display_name"] = "네이티브 배포(레거시)"
    readiness["execution_supported"] = False
    readiness["operator_path"] = "an2p-interactive-tailscale"
    return readiness


_CONTAINER_PIPELINE_TABLES = (
    "ops_container_releases",
    "ops_container_validation_receipts",
    "ops_container_approval_evidence",
    "ops_jobs",
    "ops_deployments",
    "ops_audit_logs",
)
_CONTAINER_PRODUCTION_TARGET = "cloud"
_CONTAINER_EXECUTOR_HOSTNAME = "an2p"


def _container_pipeline_missing_tables(db: Session) -> list[str]:
    return [name for name in _CONTAINER_PIPELINE_TABLES if not table_exists(db, name)]


def _container_latest_release(db: Session) -> dict[str, Any] | None:
    return mapped_one(
        db.execute(
            text(
                """
                SELECT id::text, release_digest, base_commit, source_tree,
                       snapshot_commit, platform, api_image_digest,
                       frontend_image_digest, bundle_sha256, compose_sha256,
                       build_policy_sha256, migration_ledger_sha256,
                       builder_target_identity,
                       builder_hostname, built_at, created_at
                FROM ops_container_releases
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """
            )
        )
    )


def _container_latest_pass_receipt(
    db: Session,
    release_id: str,
    target_identity: str,
) -> dict[str, Any] | None:
    return mapped_one(
        db.execute(
            text(
                """
                SELECT id::text, receipt_digest, release_id::text,
                       release_digest, source_tree, target, target_identity,
                       platform, bundle_sha256, compose_sha256,
                       api_image_digest, frontend_image_digest, checks,
                       status, validated_at, expires_at, created_at
                FROM ops_container_validation_receipts
                WHERE release_id = :release_id
                  AND target = 'an2p-dev'
                  AND target_identity = :target_identity
                  AND status = 'passed'
                  AND expires_at > CURRENT_TIMESTAMP
                ORDER BY validated_at DESC, id DESC
                LIMIT 1
                """
            ),
            {"release_id": release_id, "target_identity": target_identity},
        )
    )


def _container_target_states(db: Session) -> list[dict[str, Any]]:
    return mapped_rows(
        db.execute(
            text(
                """
                SELECT DISTINCT ON (d.target_identity)
                       d.target_environment, d.target_name, d.target_identity,
                       d.target_name AS target,
                       d.id::text AS deployment_id,
                       d.runtime_target_kind,
                       d.runtime_native_baseline_identity,
                       CASE WHEN d.runtime_target_kind = 'container'
                            THEN d.container_release_id::text END AS current_release_id,
                       CASE WHEN d.runtime_target_kind = 'container'
                            THEN d.container_release_digest END AS current_release_digest,
                       current_release.api_image_digest,
                       current_release.frontend_image_digest,
                       d.bundle_sha256,
                       CASE WHEN d.runtime_target_kind = 'container'
                            THEN d.previous_container_release_id::text END AS previous_release_id,
                       CASE WHEN d.runtime_target_kind = 'container'
                            THEN d.previous_container_release_digest END AS previous_release_digest,
                       d.validation_receipt_id::text,
                       d.validation_receipt_digest,
                       d.approval_evidence_id::text,
                       d.deployment_action,
                       d.runtime_generation,
                       d.controller_state_sha256,
                       d.finished_at,
                       d.created_at
                FROM ops_deployments d
                JOIN ops_jobs j ON j.id = d.job_id
                LEFT JOIN ops_container_releases current_release
                  ON current_release.id = d.container_release_id
                LEFT JOIN ops_container_releases previous_release
                  ON previous_release.id = d.previous_container_release_id
                WHERE d.environment = :environment
                  AND d.deployment_mode = 'container'
                  AND d.deployment_status = 'success'
                ORDER BY d.target_identity,
                         COALESCE(d.finished_at, d.created_at) DESC,
                         d.id DESC
                """
            ),
            {"environment": current_environment()},
        )
    )


def _container_active_promotion_approval(
    db: Session,
    *,
    target_identity: str,
    release_digest: str,
    receipt_digest: str,
    expected_runtime_generation: int,
    expected_controller_state_sha256: str,
) -> dict[str, Any] | None:
    return mapped_one(
        db.execute(
            text(
                """
                SELECT approval.id::text, approval.target_environment,
                       approval.target_name, approval.target_identity,
                       approval.expected_runtime_generation,
                       approval.expected_controller_state_sha256,
                       approval.expected_previous_release_digest,
                       approval.release_id::text, approval.release_digest,
                       approval.validation_receipt_id::text,
                       approval.validation_receipt_digest,
                       approval.approved_by::text, approval.approved_at,
                       approval.expires_at
                FROM ops_container_approval_evidence approval
                WHERE approval.action = 'promote'
                  AND approval.target_identity = :target_identity
                  AND approval.release_digest = :release_digest
                  AND approval.validation_receipt_digest = :receipt_digest
                  AND approval.expected_runtime_generation = :expected_runtime_generation
                  AND approval.expected_controller_state_sha256 = :expected_controller_state_sha256
                  AND approval.expires_at > CURRENT_TIMESTAMP
                  AND NOT EXISTS (
                      SELECT 1
                      FROM ops_deployments deployment
                      WHERE deployment.approval_evidence_id = approval.id
                  )
                ORDER BY approval.approved_at DESC, approval.id DESC
                LIMIT 1
                """
            ),
            {
                "target_identity": target_identity,
                "release_digest": release_digest,
                "receipt_digest": receipt_digest,
                "expected_runtime_generation": expected_runtime_generation,
                "expected_controller_state_sha256": expected_controller_state_sha256,
            },
        )
    )


def _configured_container_development_identity() -> str | None:
    value = os.getenv("OPS_CONTAINER_DEV_TARGET_IDENTITY", "").strip().lower()
    return value if re.fullmatch(r"[0-9a-f]{64}", value) else None


def _container_deployment_readiness_payload(db: Session) -> dict[str, Any]:
    missing_tables = _container_pipeline_missing_tables(db)
    registry = deployment_readiness()
    reasons: list[dict[str, str]] = []
    if missing_tables:
        reasons.append(
            {
                "code": "container_evidence_schema_unavailable",
                "message": "Docker 배포 증적 마이그레이션이 적용되지 않았습니다.",
            }
        )
    # Container execution deliberately does not inherit the legacy native
    # PowerShell/full-shell-key readiness gate. The reviewed registry identity
    # and source snapshot remain read-only inputs; the dedicated forced
    # container transports are validated by the API and worker themselves.
    latest_release = None
    validation_receipt = None
    promotion_approval = None
    target_states: list[dict[str, Any]] = []
    development_target_identity = _configured_container_development_identity()
    if development_target_identity is None:
        reasons.append(
            {
                "code": "container_development_identity_unconfigured",
                "message": "an2p-dev canonical target identity가 Ops API에 고정되지 않았습니다.",
            }
        )
    if not missing_tables:
        latest_release = _container_latest_release(db)
        if latest_release is not None and development_target_identity is not None:
            validation_receipt = _container_latest_pass_receipt(
                db,
                str(latest_release["id"]),
                development_target_identity,
            )
        target_states = _container_target_states(db)

    targets = registry.get("targets") or []
    default_target = registry.get("default_target")
    selected_target = next(
        (target for target in targets if isinstance(target, dict) and target.get("name") == default_target),
        None,
    )
    selected_identity = str((selected_target or {}).get("target_identity") or "")
    agent = _container_deployment_agent(db)
    if agent is None or str(agent.get("hostname") or "").strip().lower() != _CONTAINER_EXECUTOR_HOSTNAME:
        agent = None
        reasons.append(
            {
                "code": "container_deployment_agent_offline",
                "message": "an2p Docker deployment worker가 연결되어 있지 않습니다.",
            }
        )
    if (
        not isinstance(selected_target, dict)
        or selected_target.get("name") != _CONTAINER_PRODUCTION_TARGET
        or selected_target.get("environment") != "production"
        or selected_target.get("deploy_profile") != "full-stack"
    ):
        reasons.append(
            {
                "code": "container_production_target_unavailable",
                "message": "고정 production target=cloud registry 계약을 확인할 수 없습니다.",
            }
        )
    selected_state = next(
        (state for state in target_states if state.get("target_identity") == selected_identity),
        None,
    )
    live_runtime_cas: dict[str, Any] | None = None
    remote_claim_fencing_ready = False
    status_boundary_ready = container_transport_service_boundary_ready(profile="status")
    live_status = (
        read_container_controller_status(timeout_seconds=10)
        if status_boundary_ready
        else None
    )
    if not status_boundary_ready:
        reasons.append(
            {
                "code": "container_status_service_boundary_unavailable",
                "message": (
                    "Ops API 전용 계정 또는 status-only SSH 자격 증명 경계가 "
                    "준비되지 않았습니다."
                ),
            }
        )
    elif live_status is None:
        reasons.append(
            {
                "code": "container_controller_status_unavailable",
                "message": "운영 controller 상태를 고정 명령으로 확인할 수 없습니다.",
            }
        )
    else:
        remote_claim_fencing_ready = "worker_lease" in live_status
        if not remote_claim_fencing_ready:
            reasons.append(
                {
                    "code": "container_remote_claim_fencing_unavailable",
                    "message": "운영 controller가 worker claim epoch fence를 지원하지 않습니다.",
                }
            )
        try:
            live_runtime_cas = container_runtime_cas(live_status)
        except ContainerDeploymentError as exc:
            reasons.append(
                {
                    "code": "container_controller_state_not_approvable",
                    "message": str(exc),
                }
            )
    if live_runtime_cas is not None:
        selected_runtime_kind = (
            None if selected_state is None else selected_state.get("runtime_target_kind")
        )
        if selected_state is None or selected_runtime_kind == "native":
            state_matches_database = (
                live_runtime_cas["expected_runtime_generation"] == 0
                and live_runtime_cas["expected_active_release_digest"] is None
                and live_runtime_cas["expected_previous_release_digest"] is None
                and (
                    selected_state is None
                    or live_runtime_cas["expected_controller_state_sha256"]
                    == selected_state.get("controller_state_sha256")
                )
            )
        elif selected_runtime_kind == "container":
            state_matches_database = bool(
                live_runtime_cas["expected_runtime_generation"] == selected_state.get("runtime_generation")
                and live_runtime_cas["expected_controller_state_sha256"]
                == selected_state.get("controller_state_sha256")
                and live_runtime_cas["expected_active_release_digest"] == selected_state.get("current_release_digest")
                and live_runtime_cas["expected_previous_release_digest"]
                == selected_state.get("previous_release_digest")
                and live_runtime_cas["native_baseline_identity"]
                == selected_state.get("runtime_native_baseline_identity")
            )
        else:
            state_matches_database = False
        if not state_matches_database:
            live_runtime_cas = None
            reasons.append(
                {
                    "code": "container_controller_database_state_drift",
                    "message": "운영 controller current/previous 상태가 Ops 증적과 일치하지 않습니다.",
                }
            )
    if selected_identity and latest_release and validation_receipt and live_runtime_cas:
        promotion_approval = _container_active_promotion_approval(
            db,
            target_identity=selected_identity,
            release_digest=str(latest_release["release_digest"]),
            receipt_digest=str(validation_receipt["receipt_digest"]),
            expected_runtime_generation=int(live_runtime_cas["expected_runtime_generation"]),
            expected_controller_state_sha256=str(live_runtime_cas["expected_controller_state_sha256"]),
        )
    promotion_confirmation = None
    if selected_identity and latest_release and validation_receipt and live_runtime_cas:
        promotion_confirmation = (
            f"PROMOTE {selected_identity} {latest_release['release_digest']} "
            f"{validation_receipt['receipt_digest']} "
            f"{live_runtime_cas['expected_runtime_generation']} "
            f"{live_runtime_cas['expected_controller_state_sha256']}"
        )
    rollback_confirmation = None
    if (
        selected_identity
        and selected_state
        and selected_state.get("current_release_digest")
        and selected_state.get("previous_release_digest")
        and live_runtime_cas
    ):
        rollback_confirmation = (
            f"ROLLBACK {selected_identity} "
            f"{selected_state['current_release_digest']} "
            f"{selected_state['previous_release_digest']} "
            f"{live_runtime_cas['expected_runtime_generation']} "
            f"{live_runtime_cas['expected_controller_state_sha256']}"
        )
    native_rollback_confirmation = None
    if (
        selected_identity
        and selected_state
        and selected_state.get("runtime_target_kind") == "container"
        and selected_state.get("current_release_digest")
        and live_runtime_cas
        and live_runtime_cas.get("native_baseline_identity")
    ):
        native_rollback_confirmation = (
            f"ROLLBACK_NATIVE {selected_identity} "
            f"{selected_state['current_release_digest']} "
            f"{live_runtime_cas['native_baseline_identity']} "
            f"{live_runtime_cas['expected_runtime_generation']} "
            f"{live_runtime_cas['expected_controller_state_sha256']}"
        )

    promotion_evidence_ready = bool(selected_identity and latest_release and validation_receipt and live_runtime_cas)
    if (
        promotion_evidence_ready
        and selected_state
        and selected_state.get("current_release_digest") == latest_release.get("release_digest")
    ):
        promotion_evidence_ready = False
        reasons.append(
            {
                "code": "container_release_already_active",
                "message": "최신 검증 릴리스가 이미 운영에서 활성 상태입니다.",
            }
        )
    rollback_evidence_ready = bool(rollback_confirmation)
    native_rollback_evidence_ready = bool(native_rollback_confirmation)
    build_validate_unsupported = {
        "supported": False,
        "can_request": False,
        "blocker_code": "container_build_validate_executor_unavailable",
    }
    executor_supported = bool(
        not missing_tables
        and development_target_identity
        and selected_identity
        and selected_target
        and selected_target.get("name") == _CONTAINER_PRODUCTION_TARGET
        and selected_target.get("environment") == "production"
        and agent
        and status_boundary_ready
        and remote_claim_fencing_ready
        and live_runtime_cas
    )
    can_promote = bool(executor_supported and promotion_evidence_ready)
    can_rollback = bool(executor_supported and rollback_evidence_ready)
    can_rollback_native = bool(executor_supported and native_rollback_evidence_ready)
    return {
        "schema_version": 1,
        "deployment_mode": "container",
        "display_name": "Docker 불변 이미지 배포",
        "available": not missing_tables,
        "executor_supported": executor_supported,
        "remote_claim_fencing_ready": remote_claim_fencing_ready,
        "pipeline_state": "ready" if executor_supported else "blocked",
        "agent": agent,
        "default_target": default_target,
        "targets": targets,
        "development_target": {
            "target": "an2p-dev",
            "target_identity": development_target_identity,
        },
        "latest_release": latest_release,
        "validation_receipt": validation_receipt,
        "promotion_approval": promotion_approval,
        "target_states": target_states,
        "live_runtime_cas": live_runtime_cas,
        "promotion_evidence_ready": promotion_evidence_ready,
        "approval_evidence_ready": promotion_approval is not None,
        "rollback_evidence_ready": rollback_evidence_ready,
        "native_rollback_evidence_ready": native_rollback_evidence_ready,
        "can_promote": can_promote,
        "can_rollback": can_rollback,
        "can_rollback_native": can_rollback_native,
        "actions": {
            "build": {**build_validate_unsupported, "evidence_ready": bool(registry.get("snapshot"))},
            "validate": {**build_validate_unsupported, "evidence_ready": latest_release is not None},
            "promote": {
                "supported": True,
                "can_request": can_promote,
                "blocker_code": None if can_promote else "container_promotion_not_ready",
                "evidence_ready": promotion_evidence_ready,
                "approval_ready": promotion_approval is not None,
                "required_confirmation": promotion_confirmation,
            },
            "rollback": {
                "supported": True,
                "can_request": can_rollback,
                "blocker_code": (
                    None if can_rollback else "container_rollback_not_ready"
                ),
                "evidence_ready": rollback_evidence_ready,
                "required_confirmation": rollback_confirmation,
            },
            "rollback_native": {
                "supported": True,
                "can_request": can_rollback_native,
                "blocker_code": (
                    None
                    if can_rollback_native
                    else "container_native_rollback_not_ready"
                ),
                "evidence_ready": native_rollback_evidence_ready,
                "native_baseline_identity": (
                    None
                    if live_runtime_cas is None
                    else live_runtime_cas.get("native_baseline_identity")
                ),
                "required_confirmation": native_rollback_confirmation,
            },
        },
        "reasons": reasons,
    }


def readable_category_sql(column: str) -> str:
    value = f"btrim(COALESCE({column}, ''))"
    question_count = f"(length({value}) - length(replace({value}, '?', '')))"
    hard_marker_count = (
        f"(length({value}) - length(regexp_replace({value}, '[{_CATEGORY_HARD_DAMAGE_MARKERS}]', '', 'g')))"
    )
    soft_marker_count = (
        f"(length({value}) - length(regexp_replace({value}, '[{_CATEGORY_SOFT_DAMAGE_MARKERS}]', '', 'g')))"
    )
    return f"""
    NULLIF(
        CASE
            WHEN {value} = '' THEN ''
            WHEN {question_count} >= 2 THEN ''
            WHEN {hard_marker_count} >= 1 THEN ''
            WHEN {soft_marker_count} >= 2 THEN ''
            ELSE {value}
        END,
        ''
    )
    """


_CATEGORY_COLUMNS = (
    "c.standard_category_label",
    "c.domain_category",
    "c.collection_category",
    "c.category_raw",
)

CONTENT_CATEGORY_SQL = f"""
COALESCE(
    {readable_category_sql("c.standard_category_label")},
    {readable_category_sql("c.domain_category")},
    {readable_category_sql("c.collection_category")},
    {readable_category_sql("c.category_raw")},
    '미분류'
)
"""

CATEGORY_ENCODING_ISSUE_SQL = (
    "("
    + " OR ".join(
        f"""(
        NULLIF(btrim({column}), '') IS NOT NULL
        AND {readable_category_sql(column)} IS NULL
    )"""
        for column in _CATEGORY_COLUMNS
    )
    + ")"
)

TARGET_PRESENT_SQL = """(
    btrim(COALESCE(c.target, '')) <> ''
    OR btrim(COALESCE(c.target_age_group, '')) <> ''
    OR c.target_min_age IS NOT NULL
    OR c.target_max_age IS NOT NULL
    OR btrim(COALESCE(c.raw_fields->>'target', '')) <> ''
)"""
FEE_PRESENT_SQL = """(
    c.fee IS NOT NULL
    OR upper(btrim(COALESCE(c.raw_fields->>'fee_status', ''))) IN ('FREE', 'PAID')
    OR btrim(
        COALESCE(
            c.raw_fields->>'fee',
            c.raw_fields->>'fee_raw',
            c.raw_fields->>'source_fee',
            ''
        )
    ) ~ '(무료|[0-9])'
)"""
DATE_PRESENT_SQL = """(
    c.start_date IS NOT NULL
    OR c.end_date IS NOT NULL
    OR btrim(COALESCE(c.raw_fields->>'period', c.raw_fields->>'date', '')) <> ''
)"""
PLACE_PRESENT_SQL = """(
    btrim(COALESCE(c.venue_name, '')) <> ''
    OR btrim(COALESCE(c.venue_address, '')) <> ''
    OR c.branch_id IS NOT NULL
    OR btrim(
        COALESCE(
            c.raw_fields->>'venue_name',
            c.raw_fields->>'place',
            c.raw_fields->>'room',
            c.raw_fields->>'location',
            ''
        )
    ) <> ''
)"""
CATEGORY_PRESENT_SQL = f"""(
    ({CONTENT_CATEGORY_SQL}) <> '미분류'
    OR btrim(COALESCE(c.raw_fields->>'category', '')) <> ''
)"""
TIME_PRESENT_SQL = """(
    COALESCE(array_length(c.schedule_days, 1), 0) > 0
    OR btrim(COALESCE(c.schedule_raw, '')) <> ''
    OR btrim(COALESCE(c.raw_fields->>'schedule', c.raw_fields->>'time', '')) <> ''
)"""
CATEGORY_QUALITY_FIELD_SQL = {
    "target": TARGET_PRESENT_SQL,
    "fee": FEE_PRESENT_SQL,
    "date": DATE_PRESENT_SQL,
    "place": PLACE_PRESENT_SQL,
    "category": CATEGORY_PRESENT_SQL,
    "time": TIME_PRESENT_SQL,
}


def _page(
    items: list[dict[str, Any]], *, total: int, limit: int, offset: int, available: bool = True
) -> dict[str, Any]:
    return {
        "available": available,
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def _redact_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for row in rows:
        for key in (
            "error_message",
            "last_error",
            "geocode_last_error",
            "message",
            "stack_trace",
        ):
            if key in row and row[key]:
                row[key] = redact_text(row[key], maximum=4_000)
        for key in (
            "source_url",
            "health_url",
            "grafana_url",
            "website_url",
            "raw_url",
            "application_url",
        ):
            if key in row and row[key]:
                row[key] = safe_external_http_url(row[key]) or None
    return rows


_OPTIONAL_BRANCH_GEOCODE_COLUMNS = {
    "geocode_status": "text",
    "geocode_reason_code": "text",
    "geocode_attempt_count": "integer",
    "geocode_candidates": "jsonb",
    "geocode_next_retry_at": "timestamptz",
    "geocode_last_error": "text",
    "geocode_last_attempt_at": "timestamptz",
}


def _optional_branch_geocode_select(db: Session) -> tuple[str, list[str]]:
    """Build a fixed-whitelist projection that works before and after its migration."""
    rows = mapped_rows(
        db.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'branches'
                  AND column_name = ANY(:column_names)
                """
            ),
            {"column_names": list(_OPTIONAL_BRANCH_GEOCODE_COLUMNS)},
        )
    )
    available = {
        str(row.get("column_name")) for row in rows if row.get("column_name") in _OPTIONAL_BRANCH_GEOCODE_COLUMNS
    }
    projections = [
        (f"b.{column_name}" if column_name in available else f"NULL::{sql_type} AS {column_name}")
        for column_name, sql_type in _OPTIONAL_BRANCH_GEOCODE_COLUMNS.items()
    ]
    return ",\n                       ".join(projections), sorted(available)


def _sanitize_category_metadata(row: dict[str, Any]) -> dict[str, Any]:
    damaged_fields: list[str] = []
    for key in (
        "standard_category_key",
        "standard_category_label",
        "domain_category",
        "collection_category",
        "category_raw",
    ):
        if key in row and looks_corrupted_category(row.get(key)):
            row[key] = None
            damaged_fields.append(key)
    row["category_encoding_issue"] = bool(damaged_fields)
    row["damaged_category_fields"] = damaged_fields
    return row


def _status_rank(value: str) -> int:
    return {
        "healthy": 0,
        "disabled": 1,
        "unknown": 2,
        "warning": 3,
        "critical": 4,
    }.get(value, 2)


def _overall_status(components: list[dict[str, Any]]) -> str:
    required = {"frontend", "backend", "database", "crawler", "ai_worker", "agent"}
    by_type = {str(item.get("type")): str(item.get("status") or "unknown") for item in components}
    if not required.issubset(by_type) or any(by_type[item] == "unknown" for item in required):
        known = [value for value in by_type.values() if value != "unknown"]
        if "critical" in known:
            return "critical"
        if "warning" in known:
            return "warning"
        return "unknown"
    return max(by_type.values(), key=_status_rank)


def _database_status(db: Session) -> tuple[str, float | None]:
    started = time.perf_counter()
    try:
        assert_database_ready(db, queries=OPS_API_READINESS_QUERIES)
    except Exception:
        # PostgreSQL marks the transaction failed after a missing relation or
        # privilege error. Roll it back so the response can still report the
        # database as critical instead of turning the whole dashboard into 500.
        try:
            db.rollback()
        except Exception:
            logger.warning("Unable to roll back failed Ops readiness transaction", exc_info=True)
        return "critical", None
    return "healthy", round((time.perf_counter() - started) * 1_000, 1)


def _database_service_host(db: Session) -> str | None:
    """Return the DB endpoint host without confusing it with the reporting Agent."""
    try:
        bind = db.get_bind()
        host = getattr(getattr(bind, "url", None), "host", None)
    except Exception:
        return None
    if host is None:
        return None
    return str(host).strip() or None


def _safe_observed_runtime_host(value: Any) -> str | None:
    """Return bounded explicit runtime evidence, never a reporter-derived value."""
    normalized = str(value or "").strip().rstrip(".")
    if not _OBSERVED_HOST_PATTERN.fullmatch(normalized):
        return None
    return normalized


def _with_production_placement(item: dict[str, Any] | None) -> dict[str, Any] | None:
    """Annotate the desired production host without rewriting observed provenance.

    The standalone control plane intentionally runs against its local database,
    so its reporting row can have ``environment=development`` and a loopback
    ``service_host``.  Production placement is a separate fact: keep the
    observed endpoint untouched and always add the reviewed topology fields
    when the service has a declared production owner.
    """
    if item is None:
        return item
    # A reporting Agent may observe a remote service (crawler status is read
    # from the database), so its hostname is not proof of the executor host.
    item["observed_runtime_host"] = _safe_observed_runtime_host(item.get("observed_runtime_host"))
    item["runtime_host_verified"] = item["observed_runtime_host"] is not None
    item["runtime_host_evidence_source"] = "explicit_observed_runtime_host" if item["runtime_host_verified"] else None
    # These fields are observations made by a status reporter.  Neither the
    # reporter nor its checked endpoint proves which host executed a crawler.
    item["reporter_is_runtime_evidence"] = False
    item["service_host_is_runtime_evidence"] = False
    try:
        placement = load_production_topology().primary_for(str(item.get("service_type") or item.get("type") or ""))
    except (KeyError, StopIteration, ValueError):
        return item
    # New names state the provenance explicitly.  Keep topology_* as additive
    # compatibility aliases for existing Ops Console clients.
    item["configured_owner_node"] = placement.node
    item["configured_owner_host"] = placement.service_host
    item["configured_owner_role"] = placement.role
    item["topology_node"] = placement.node
    item["topology_host"] = placement.service_host
    item["topology_role"] = placement.role
    return item


def _crawler_runtime_disabled_detail() -> str:
    owner = load_production_topology().primary_for("crawler").service_host
    return f"Local crawler runtime is disabled; run the production one-shot on the configured crawler owner {owner}."


def _registered_component(db: Session, service_type: str) -> dict[str, Any] | None:
    if not table_exists(db, "ops_services"):
        return None
    return _with_production_placement(
        mapped_one(
            db.execute(
                text(
                    """
                SELECT s.service_type AS type, s.service_name AS name, s.service_host,
                       s.status,
                       s.response_time_ms, s.current_version, s.current_commit,
                       s.last_checked_at, s.grafana_url,
                       a.hostname AS reporter_hostname
                FROM ops_services s
                LEFT JOIN ops_agents a ON a.id = s.agent_id
                WHERE s.environment = :environment AND s.service_type = :service_type
                ORDER BY s.last_checked_at DESC NULLS LAST, s.updated_at DESC
                LIMIT 1
                """
                ),
                {"environment": current_environment(), "service_type": service_type},
            )
        )
    )


def _crawler_component(db: Session) -> dict[str, Any]:
    # Generic status agents used to infer this component from crawler_run_log
    # and attach their own hostname.  Such rows describe the reporter and may
    # remain stale in ops_services; they are not executor evidence.  Build the
    # summary directly from run history and keep runtime_host unobserved.
    if not table_exists(db, "crawler_run_log"):
        return dict(
            _with_production_placement(
                {
                    "type": "crawler",
                    "name": "Crawler",
                    "status": "unknown",
                    "last_checked_at": None,
                    "status_observation_source": "crawler_run_log",
                }
            )
            or {}
        )
    latest = mapped_one(
        db.execute(
            text(
                """
                SELECT status, started_at, ended_at, error_message
                FROM crawler_run_log
                ORDER BY started_at DESC
                LIMIT 1
                """
            )
        )
    )
    if not latest:
        return dict(
            _with_production_placement(
                {
                    "type": "crawler",
                    "name": "Crawler",
                    "status": "unknown",
                    "last_checked_at": None,
                    "status_observation_source": "crawler_run_log",
                }
            )
            or {}
        )
    legacy_status = str(latest["status"] or "")
    if legacy_status == "running":
        component_status = "healthy"
    elif legacy_status == "success":
        component_status = "healthy"
    elif legacy_status in {"failed", "stopped"}:
        component_status = "warning"
    else:
        component_status = "unknown"
    return dict(
        _with_production_placement(
            {
                "type": "crawler",
                "name": "Crawler",
                "status": component_status,
                "last_checked_at": latest["started_at"],
                "last_error": redact_text(latest.get("error_message"), maximum=1_000) or None,
                "status_observation_source": "crawler_run_log",
            }
        )
        or {}
    )


@router.get("/session")
def ops_session(user: models.User = Depends(require_ops_viewer)) -> dict[str, Any]:
    return {
        "user": {
            "id": str(user.id),
            "email": user.email,
            "name": user.name,
        },
        "role": ops_role_for_user(user),
        "environment": current_environment(),
    }


@router.get("/dashboard/summary")
def dashboard_summary(db: Session = Depends(get_db)) -> dict[str, Any]:
    database_status, database_latency = _database_status(db)
    components: list[dict[str, Any]] = [
        _with_production_placement(
            {
                "type": "backend",
                "name": "Backend",
                "service_host": os.getenv("OPS_BACKEND_SERVICE_HOST", "").strip() or socket.gethostname(),
                "observed_runtime_host": socket.gethostname(),
                "status": "healthy",
                "response_time_ms": None,
                "last_checked_at": datetime.now(timezone.utc),
            }
        ),
        _with_production_placement(
            {
                "type": "database",
                "name": "Database",
                "service_host": _database_service_host(db),
                "status": database_status,
                "response_time_ms": database_latency,
                "last_checked_at": datetime.now(timezone.utc),
            }
        ),
    ]
    if database_status == "critical":
        return {
            "generated_at": datetime.now(timezone.utc),
            "environment": current_environment(),
            "overall_status": "critical",
            "components": _redact_rows(components),
            "agents": {"connected": 0, "total": 0, "status": "unknown"},
            "latest_deployment": None,
            "grafana_url": safe_external_http_url(os.getenv("MOONCEN_GRAFANA_URL", "")) or None,
        }
    for service_type in ("frontend", "redis", "ai_worker", "agent"):
        component = _registered_component(db, service_type)
        components.append(
            component
            or {
                "type": service_type,
                "name": service_type.replace("_", " ").title(),
                "status": "unknown",
                "last_checked_at": None,
            }
        )
    components.append(_crawler_component(db))

    latest_deployment = None
    if table_exists(db, "ops_deployments"):
        latest_deployment = mapped_one(
            db.execute(
                text(
                    """
                    SELECT id, environment, service_type, target_version,
                           target_commit, deployment_status, started_at, finished_at
                    FROM ops_deployments
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                )
            )
        )

    agent_summary = {"connected": 0, "total": 0, "status": "unknown"}
    if table_exists(db, "ops_agents"):
        row = mapped_one(
            db.execute(
                text(
                    """
                    SELECT COUNT(*) AS total,
                           COUNT(*) FILTER (
                               WHERE status = 'healthy'
                                 AND last_seen_at >= NOW() - INTERVAL '2 minutes'
                           ) AS connected
                    FROM ops_agents
                    WHERE environment = :environment
                      AND status <> 'disabled'
                    """
                ),
                {"environment": current_environment()},
            )
        )
        if row:
            agent_summary = {
                "total": int(row["total"] or 0),
                "connected": int(row["connected"] or 0),
                "status": "unknown"
                if int(row["total"] or 0) == 0
                else ("healthy" if row["total"] == row["connected"] else "warning"),
            }

    return {
        "generated_at": datetime.now(timezone.utc),
        "environment": current_environment(),
        "overall_status": _overall_status(components),
        "components": _redact_rows(components),
        "agents": agent_summary,
        "latest_deployment": latest_deployment,
        "grafana_url": safe_external_http_url(os.getenv("MOONCEN_GRAFANA_URL", "")) or None,
    }


@router.get("/dashboard/collection-summary")
def dashboard_collection_summary(db: Session = Depends(get_db)) -> dict[str, Any]:
    if not table_exists(db, "crawler_run_log"):
        return {
            "available": False,
            "today": {
                "collected": 0,
                "new": 0,
                "updated": 0,
                "failed": 0,
                "deleted_candidates": 0,
                "running": 0,
            },
            "providers": [],
            "last_collection_at": None,
        }
    today = (
        mapped_one(
            db.execute(
                text(
                    """
                SELECT
                    COALESCE(SUM(collected_count), 0) AS collected,
                    COALESCE(SUM(inserted_count), 0) AS new,
                    COALESCE(SUM(updated_count), 0) AS updated,
                    COUNT(*) FILTER (WHERE status IN ('failed', 'stopped')) AS failed,
                    COUNT(*) FILTER (WHERE status = 'running') AS running,
                    MAX(started_at) AS last_collection_at
                FROM crawler_run_log
                WHERE started_at >= (
                    date_trunc('day', NOW() AT TIME ZONE 'Asia/Seoul')
                    AT TIME ZONE 'Asia/Seoul'
                )
                """
                )
            )
        )
        or {}
    )
    providers = mapped_rows(
        db.execute(
            text(
                """
                SELECT COALESCE(NULLIF(target_key, ''), NULLIF(crawler_name, ''), 'unknown') AS provider,
                       COUNT(*) AS run_count,
                       COUNT(*) FILTER (WHERE status = 'success') AS success_count,
                       ROUND(
                           100.0 * COUNT(*) FILTER (WHERE status = 'success')
                           / NULLIF(COUNT(*), 0),
                           1
                       ) AS success_rate,
                       MAX(started_at) AS last_run_at
                FROM crawler_run_log
                WHERE started_at >= NOW() - INTERVAL '24 hours'
                GROUP BY COALESCE(NULLIF(target_key, ''), NULLIF(crawler_name, ''), 'unknown')
                ORDER BY success_rate ASC NULLS FIRST, provider
                LIMIT 50
                """
            )
        )
    )
    deleted_candidates = 0
    if table_exists(db, "ops_crawler_runs"):
        deleted_candidates = int(
            db.execute(
                text(
                    """
                    SELECT COALESCE(SUM(deleted_candidate_count), 0)
                    FROM ops_crawler_runs
                    WHERE created_at >= (
                        date_trunc('day', NOW() AT TIME ZONE 'Asia/Seoul')
                        AT TIME ZONE 'Asia/Seoul'
                    )
                    """
                )
            ).scalar()
            or 0
        )
    last_collection_at = today.pop("last_collection_at", None)
    return {
        "available": True,
        "today": {
            "collected": int(today.get("collected") or 0),
            "new": int(today.get("new") or 0),
            "updated": int(today.get("updated") or 0),
            "failed": int(today.get("failed") or 0),
            "running": int(today.get("running") or 0),
            "deleted_candidates": deleted_candidates,
        },
        "providers": providers,
        "last_collection_at": last_collection_at,
    }


def _quality_counts(db: Session) -> dict[str, int]:
    counts = (
        mapped_one(
            db.execute(
                text(
                    f"""
                SELECT
                    COUNT(*) FILTER (
                        WHERE btrim(COALESCE(c.title, '')) = ''
                           OR c.branch_id IS NULL
                           OR (c.start_date IS NULL AND c.end_date IS NULL)
                           OR (
                               COALESCE(array_length(c.schedule_days, 1), 0) = 0
                               AND btrim(COALESCE(c.schedule_raw, '')) = ''
                           )
                           OR c.fee IS NULL
                           OR btrim(COALESCE(c.raw_url, c.application_url, '')) = ''
                           OR btrim(COALESCE(c.standard_category_key, c.category_raw, '')) = ''
                    ) AS missing_required,
                    COUNT(*) FILTER (
                        WHERE (c.start_date IS NOT NULL AND c.end_date IS NOT NULL AND c.start_date > c.end_date)
                           OR (c.apply_start IS NOT NULL AND c.apply_end IS NOT NULL AND c.apply_start > c.apply_end)
                           OR EXTRACT(YEAR FROM COALESCE(c.start_date, c.end_date, CURRENT_DATE)) NOT BETWEEN 2000 AND 2100
                    ) AS invalid_dates,
                    COUNT(*) FILTER (WHERE c.fee < 0 OR c.fee > 100000000) AS invalid_prices,
                    COUNT(DISTINCT c.branch_id) FILTER (
                        WHERE c.branch_id IS NOT NULL
                          AND (b.address IS NULL OR btrim(b.address) = '')
                    ) AS missing_address,
                    COUNT(DISTINCT c.branch_id) FILTER (
                        WHERE c.branch_id IS NOT NULL
                          AND (b.lat IS NULL OR b.lon IS NULL)
                    ) AS missing_coordinates,
                    COUNT(DISTINCT c.branch_id) FILTER (
                        WHERE c.branch_id IS NOT NULL
                          AND (
                              b.address IS NULL OR btrim(b.address) = ''
                              OR b.lat IS NULL OR b.lon IS NULL
                          )
                    ) AS incomplete_location,
                    COUNT(DISTINCT c.branch_id) FILTER (
                        WHERE b.lat IS NOT NULL AND b.lon IS NOT NULL
                          AND NOT (b.lat BETWEEN 32.0 AND 39.5 AND b.lon BETWEEN 123.0 AND 132.5)
                    ) AS out_of_korea,
                    COUNT(*) FILTER (WHERE duplicate_url.duplicate_count > 1) AS duplicate_urls,
                    COUNT(*) AS active_courses
                FROM courses c
                LEFT JOIN branches b ON b.id = c.branch_id
                LEFT JOIN (
                    SELECT raw_url, COUNT(*) AS duplicate_count
                    FROM courses
                    WHERE is_active = true AND btrim(COALESCE(raw_url, '')) <> ''
                    GROUP BY raw_url
                ) duplicate_url ON duplicate_url.raw_url = c.raw_url
                WHERE c.is_active = true
                  AND {CONTENT_TYPE_SQL} <> 'unknown'
                """
                )
            )
        )
        or {}
    )
    return {key: int(value or 0) for key, value in counts.items()}


@router.get("/dashboard/quality-summary")
@router.get("/quality/summary")
def quality_summary(db: Session = Depends(get_db)) -> dict[str, Any]:
    counts = _quality_counts(db)
    issue_statuses: list[dict[str, Any]] = []
    blocked_sync = 0
    latest_scan_at = None
    if table_exists(db, "ops_quality_issues"):
        issue_statuses = mapped_rows(
            db.execute(
                text(
                    """
                    SELECT status, severity, COUNT(*) AS issue_count
                    FROM ops_quality_issues
                    GROUP BY status, severity
                    ORDER BY status, severity
                    """
                )
            )
        )
        blocked_sync = int(
            db.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM ops_quality_issues
                    WHERE blocked_sync = true AND status IN ('open', 'reviewing')
                    """
                )
            ).scalar()
            or 0
        )
        latest_scan_at = db.execute(text("SELECT MAX(detected_at) FROM ops_quality_issues")).scalar()
    return {
        "available": True,
        "counts": {**counts, "blocked_sync": blocked_sync},
        "issue_statuses": issue_statuses,
        "latest_scan_at": latest_scan_at,
        "rule_source": "production courses/service_group",
    }


@router.get("/dashboard/alerts")
def dashboard_alerts(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    persisted: list[dict[str, Any]] = []
    if table_exists(db, "ops_alerts"):
        persisted = mapped_rows(
            db.execute(
                text(
                    """
                    SELECT id::text, severity, alert_type, title, message,
                           resource_type, resource_id, status, detected_at, metadata
                    FROM ops_alerts
                    WHERE status IN ('open', 'acknowledged')
                    ORDER BY
                        CASE severity WHEN 'critical' THEN 1 WHEN 'warning' THEN 2 ELSE 3 END,
                        detected_at DESC
                    LIMIT :limit
                    """
                ),
                {"limit": limit},
            )
        )
    derived: list[dict[str, Any]] = []
    if table_exists(db, "crawler_run_log"):
        failures = mapped_rows(
            db.execute(
                text(
                    """
                    SELECT id, target_key, crawler_name, error_type, error_message, started_at
                    FROM crawler_run_log
                    WHERE status IN ('failed', 'stopped')
                      AND started_at >= NOW() - INTERVAL '24 hours'
                    ORDER BY started_at DESC
                    LIMIT :limit
                    """
                ),
                {"limit": limit},
            )
        )
        for failure in failures:
            target = failure.get("target_key") or failure.get("crawler_name") or "unknown"
            derived.append(
                {
                    "id": f"crawler-run-log:{failure['id']}",
                    "severity": "warning",
                    "alert_type": "crawler_failure",
                    "title": f"{target} 수집 실패",
                    "message": redact_text(
                        failure.get("error_message") or failure.get("error_type") or "원인 확인 필요", maximum=500
                    ),
                    "resource_type": "crawler_run",
                    "resource_id": f"legacy-{failure['id']}",
                    "status": "open",
                    "detected_at": failure["started_at"],
                    "metadata": {"source": "crawler_run_log"},
                }
            )
    items = sorted(
        [*persisted, *derived],
        key=lambda item: (
            {"critical": 0, "warning": 1, "info": 2}.get(str(item.get("severity")), 3),
            -(item.get("detected_at") or datetime.min.replace(tzinfo=timezone.utc)).timestamp(),
        ),
    )[:limit]
    return {"available": bool(table_exists(db, "ops_alerts") or table_exists(db, "crawler_run_log")), "items": items}


@router.get("/dashboard/recent-jobs")
def dashboard_recent_jobs(
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if not table_exists(db, "ops_jobs"):
        return {"available": False, "items": []}
    items = mapped_rows(
        db.execute(
            text(
                """
                SELECT id::text, job_type, status, environment, target_key,
                       progress, requested_by::text, queued_at, started_at,
                       finished_at, error_code, error_message
                FROM ops_jobs
                ORDER BY created_at DESC
                LIMIT :limit
                """
            ),
            {"limit": limit},
        )
    )
    return {"available": True, "items": _redact_rows(items)}


@router.get("/services")
def services(
    environment: Literal["production", "staging", "development"] | None = Query(default=None),
    service_type: str = Query(default="", max_length=40),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if not table_exists(db, "ops_services"):
        return _page([], total=0, limit=200, offset=0, available=False)
    conditions = ["s.environment = :environment"]
    params: dict[str, Any] = {"environment": environment or current_environment()}
    if service_type:
        conditions.append("s.service_type = :service_type")
        params["service_type"] = service_type
    items = mapped_rows(
        db.execute(
            text(
                f"""
                SELECT s.id::text, s.service_name, s.service_type, s.environment,
                       s.service_host,
                       s.status, s.response_time_ms, s.current_version,
                       s.current_commit, s.last_checked_at, s.last_restarted_at,
                       s.health_url, s.grafana_url, s.last_error,
                       a.name AS agent_name, a.hostname AS reporter_hostname
                FROM ops_services s
                LEFT JOIN ops_agents a ON a.id = s.agent_id
                WHERE {" AND ".join(conditions)}
                ORDER BY s.service_type, s.service_name
                LIMIT 200
                """
            ),
            params,
        )
    )
    items = [dict(_with_production_placement(item) or item) for item in items]
    return _page(_redact_rows(items), total=len(items), limit=200, offset=0)


@router.get("/services/{service_id}")
def service_detail(service_id: UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    require_ops_schema(db, "ops_services")
    item = mapped_one(
        db.execute(
            text(
                """
                SELECT s.id::text, s.service_name, s.service_type, s.environment,
                       s.service_host,
                       s.status, s.response_time_ms, s.current_version,
                       s.current_commit, s.last_checked_at, s.last_restarted_at,
                       s.health_url, s.grafana_url, s.last_error, s.dependencies,
                       a.id::text AS agent_id, a.name AS agent_name,
                       a.hostname AS reporter_hostname, a.ip_address::text,
                       a.status AS agent_status,
                       a.last_seen_at
                FROM ops_services s
                LEFT JOIN ops_agents a ON a.id = s.agent_id
                WHERE s.id = :service_id
                """
            ),
            {"service_id": str(service_id)},
        )
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Service not found")
    return _redact_rows([dict(_with_production_placement(item) or item)])[0]


def _legacy_crawler_rows(
    db: Session,
    fetch_limit: int,
    *,
    provider: str = "",
) -> list[dict[str, Any]]:
    if not table_exists(db, "crawler_run_log"):
        return []
    provider_clause = "WHERE COALESCE(NULLIF(target_key, ''), NULLIF(crawler_name, '')) = :provider" if provider else ""
    parameters: dict[str, Any] = {"limit": fetch_limit}
    if provider:
        parameters["provider"] = provider
    rows = mapped_rows(
        db.execute(
            text(
                f"""
                SELECT ('legacy-' || id::text) AS id,
                       COALESCE(NULLIF(crawler_name, ''), NULLIF(target_key, ''), 'unknown') AS crawler_name,
                       COALESCE(NULLIF(source_type, ''), 'unknown') AS content_type,
                       COALESCE(NULLIF(target_key, ''), NULLIF(crawler_name, '')) AS provider,
                       NULL::text AS branch,
                       NULL::text AS source_url,
                       NULL::text AS current_stage,
                       NULL::text AS agent_id,
                       NULL::text AS job_id,
                       CASE status
                           WHEN 'stopped' THEN 'failed'
                           WHEN 'skipped' THEN 'blocked'
                           ELSE status
                       END AS status,
                       'apply' AS run_mode,
                       collected_count AS total_count,
                       collected_count AS processed_count,
                       GREATEST(collected_count - skipped_count, 0) AS success_count,
                       CASE WHEN status IN ('failed', 'stopped') THEN 1 ELSE 0 END AS failed_count,
                       inserted_count AS new_count,
                       updated_count,
                       0 AS deleted_candidate_count,
                       'standalone' AS trigger,
                       started_at, ended_at AS finished_at, created_at,
                        error_type, error_message,
                        'crawler_run_log' AS source
                FROM crawler_run_log
                {provider_clause}
                ORDER BY started_at DESC
                LIMIT :limit
                """
            ),
            parameters,
        )
    )
    return _redact_rows(rows)


def _ops_crawler_rows(
    db: Session,
    fetch_limit: int,
    *,
    provider: str = "",
) -> list[dict[str, Any]]:
    if not table_exists(db, "ops_crawler_runs"):
        return []
    provider_clause = "WHERE r.provider = :provider" if provider else ""
    parameters: dict[str, Any] = {"limit": fetch_limit}
    if provider:
        parameters["provider"] = provider
    rows = mapped_rows(
        db.execute(
            text(
                f"""
                SELECT r.id::text, r.crawler_name, r.content_type, r.provider,
                       r.branch, r.source_url, r.current_stage,
                       r.agent_id::text, r.job_id::text, r.status, r.run_mode,
                       r.total_count, r.processed_count, r.success_count,
                       r.failed_count, r.new_count, r.updated_count,
                       r.deleted_candidate_count, r.started_at, r.finished_at,
                       r.created_at, j.error_code AS error_type,
                       j.error_message,
                       COALESCE(NULLIF(j.parameters ->> 'trigger', ''), 'manual') AS trigger,
                       'ops_crawler_runs' AS source
                FROM ops_crawler_runs r
                LEFT JOIN ops_jobs j ON j.id = r.job_id
                {provider_clause}
                ORDER BY r.created_at DESC
                LIMIT :limit
                """
            ),
            parameters,
        )
    )
    return _redact_rows(rows)


def _merge_crawler_rows(
    ops_rows: list[dict[str, Any]],
    legacy_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Prefer an Ops run when its child process wrote the same legacy run."""
    unmatched_legacy = list(legacy_rows)
    merged = list(ops_rows)
    for ops_row in merged:
        ops_started = ops_row.get("started_at")
        if not isinstance(ops_started, datetime):
            continue
        provider = str(ops_row.get("provider") or "")
        match_index = next(
            (
                index
                for index, legacy_row in enumerate(unmatched_legacy)
                if str(legacy_row.get("provider") or "") == provider
                and isinstance(legacy_row.get("started_at"), datetime)
                and abs((legacy_row["started_at"] - ops_started).total_seconds()) <= 30
            ),
            None,
        )
        if match_index is None:
            continue
        legacy_row = unmatched_legacy.pop(match_index)
        ops_row["legacy_run_id"] = legacy_row.get("id")
        ops_row["source"] = "ops_crawler_runs+crawler_run_log"
    return [*merged, *unmatched_legacy]


_IMPROVEMENT_RUN_HISTORY_LIMIT = 25
_IMPROVEMENT_FAILURE_STATUSES = frozenset({"failed", "partial_success", "blocked", "cancelled"})
_IMPROVEMENT_PROVIDER_MAX_LENGTH = 100


def _improvement_provider(value: Any) -> str | None:
    provider = str(value or "").strip()
    if not provider or provider.casefold() == "unknown" or len(provider) > _IMPROVEMENT_PROVIDER_MAX_LENGTH:
        return None
    return provider


def _improvement_course_rows(db: Session) -> list[dict[str, Any]]:
    return mapped_rows(
        db.execute(
            text(
                """
                SELECT provider,
                       COUNT(*) FILTER (WHERE is_active = true) AS active_course_count,
                       COUNT(*) FILTER (
                           WHERE is_active = true
                              AND last_seen_at IS NOT NULL
                              AND last_seen_at < CURRENT_TIMESTAMP - INTERVAL '48 hours'
                       ) AS stale_48h_count,
                       COUNT(*) FILTER (
                           WHERE is_active = true
                              AND last_seen_at IS NOT NULL
                              AND last_seen_at < CURRENT_TIMESTAMP - INTERVAL '7 days'
                       ) AS stale_7d_count,
                       COUNT(*) FILTER (
                           WHERE is_active = true
                             AND last_seen_at IS NULL
                       ) AS freshness_unknown_count
                FROM courses
                WHERE btrim(COALESCE(provider, '')) <> ''
                GROUP BY provider
                """
            )
        )
    )


def _improvement_ops_run_rows(
    db: Session,
    *,
    jobs_available: bool,
) -> list[dict[str, Any]]:
    job_join = "LEFT JOIN ops_jobs j ON j.id = r.job_id" if jobs_available else ""
    error_code = "j.error_code" if jobs_available else "NULL::text"
    error_message = "j.error_message" if jobs_available else "NULL::text"
    return mapped_rows(
        db.execute(
            text(
                f"""
                WITH base AS (
                    SELECT r.id::text AS run_id,
                           COALESCE(NULLIF(btrim(r.provider), ''), NULLIF(btrim(r.crawler_name), '')) AS provider,
                           r.status,
                           COALESCE(r.started_at, r.created_at) AS run_at,
                           r.finished_at,
                           {error_code} AS raw_error_code,
                           {error_message} AS raw_error_message
                    FROM ops_crawler_runs r
                    {job_join}
                ), ranked AS (
                    SELECT base.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY provider
                               ORDER BY run_at DESC NULLS LAST, run_id DESC
                           ) AS history_rank,
                           COUNT(*) OVER (PARTITION BY provider) AS source_total_runs,
                           MAX(COALESCE(finished_at, run_at)) FILTER (
                               WHERE status = 'success'
                           ) OVER (PARTITION BY provider) AS source_last_success_at
                    FROM base
                    WHERE btrim(COALESCE(provider, '')) <> ''
                )
                SELECT run_id, provider, status, run_at, raw_error_code,
                       raw_error_message, history_rank, source_total_runs,
                       source_last_success_at, 'ops_crawler_runs' AS run_source
                FROM ranked
                WHERE history_rank <= :history_limit
                ORDER BY provider, history_rank
                """
            ),
            {"history_limit": _IMPROVEMENT_RUN_HISTORY_LIMIT},
        )
    )


def _improvement_legacy_run_rows(db: Session) -> list[dict[str, Any]]:
    return mapped_rows(
        db.execute(
            text(
                """
                WITH base AS (
                    SELECT id::text AS run_id,
                           COALESCE(NULLIF(btrim(target_key), ''), NULLIF(btrim(crawler_name), '')) AS provider,
                           CASE status
                               WHEN 'stopped' THEN 'failed'
                               WHEN 'skipped' THEN 'blocked'
                               ELSE status
                           END AS status,
                           started_at AS run_at,
                           ended_at AS finished_at,
                           error_type AS raw_error_code,
                           error_message AS raw_error_message
                    FROM crawler_run_log
                ), ranked AS (
                    SELECT base.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY provider
                               ORDER BY run_at DESC NULLS LAST, run_id DESC
                           ) AS history_rank,
                           COUNT(*) OVER (PARTITION BY provider) AS source_total_runs,
                           MAX(COALESCE(finished_at, run_at)) FILTER (
                               WHERE status = 'success'
                           ) OVER (PARTITION BY provider) AS source_last_success_at
                    FROM base
                    WHERE btrim(COALESCE(provider, '')) <> ''
                )
                SELECT run_id, provider, status, run_at, raw_error_code,
                       raw_error_message, history_rank, source_total_runs,
                       source_last_success_at, 'crawler_run_log' AS run_source
                FROM ranked
                WHERE history_rank <= :history_limit
                ORDER BY provider, history_rank
                """
            ),
            {"history_limit": _IMPROVEMENT_RUN_HISTORY_LIMIT},
        )
    )


def _improvement_quality_score_rows(
    db: Session,
    *,
    courses_available: bool,
) -> list[dict[str, Any]]:
    active_join = "JOIN courses c ON c.id = q.course_id AND c.is_active = true" if courses_available else ""
    return mapped_rows(
        db.execute(
            text(
                f"""
                SELECT q.provider,
                       ROUND(AVG(q.total_score)::numeric, 2) AS quality_average_score,
                       COUNT(*) FILTER (WHERE q.grade = 'bad') AS quality_bad_count
                FROM course_quality_score q
                {active_join}
                WHERE btrim(COALESCE(q.provider, '')) <> ''
                GROUP BY q.provider
                """
            )
        )
    )


def _improvement_quality_issue_rows(db: Session) -> list[dict[str, Any]]:
    return mapped_rows(
        db.execute(
            text(
                """
                SELECT provider,
                       COUNT(*) FILTER (
                           WHERE status IN ('open', 'reviewing')
                       ) AS active_quality_issue_count
                FROM ops_quality_issues
                WHERE btrim(COALESCE(provider, '')) <> ''
                GROUP BY provider
                """
            )
        )
    )


def _normalized_improvement_error(
    status_value: Any,
    raw_code_value: Any,
    raw_message_value: Any,
) -> tuple[str | None, str | None]:
    status_text = str(status_value or "").strip().casefold()
    code_text = str(raw_code_value or "").strip().casefold()[:120]
    message_text = str(raw_message_value or "").strip().casefold()[:1_500]
    evidence = f"{code_text} {message_text}"
    if status_text not in _IMPROVEMENT_FAILURE_STATUSES:
        return None, None
    if status_text == "partial_success" or any(
        marker in evidence for marker in ("partial_failure", "partial failure", "partial success", "부분 성공")
    ):
        return "partial_failure", "partial_failure"

    category_markers: tuple[tuple[str, tuple[str, ...]], ...] = (
        (
            "collection_limit",
            (
                "collection_limit",
                "page_limit",
                "page limit",
                "page cap",
                "max_pages",
                "budget exhausted",
                "limit reached",
                "수집 한도",
                "페이지 한도",
            ),
        ),
        (
            "source_contract",
            (
                "source_contract",
                "selector_error",
                "selector error",
                "parsing_error",
                "parsing error",
                "validation_error",
                "schema mismatch",
                "contract changed",
                "structure changed",
                "구조 변경",
                "완전성",
            ),
        ),
        (
            "timeout",
            ("timeout", "timed out", "time out", "시간 초과"),
        ),
        (
            "transport",
            (
                "network_error",
                "network error",
                "http_error",
                "http error",
                "transport",
                "connection",
                "socket",
                "dns",
                "tls",
                "ssl",
            ),
        ),
        (
            "scheduler",
            (
                "scheduler",
                "worker unavailable",
                "lease expired",
                "heartbeat",
                "executor unavailable",
                "runtime disabled",
            ),
        ),
    )
    normalized_codes = {
        "collection_limit": "collection_limit_reached",
        "source_contract": "source_contract_changed",
        "timeout": "request_timeout",
        "transport": "transport_failure",
        "scheduler": "scheduler_failure",
    }
    for category, markers in category_markers:
        if any(marker in evidence for marker in markers):
            return category, normalized_codes[category]
    return "unknown", "unknown_failure"


def _improvement_run_evidence(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    valid_rows: list[dict[str, Any]] = []
    source_counts: dict[tuple[str, str], tuple[int, int]] = {}
    for raw_row in rows:
        provider = _improvement_provider(raw_row.get("provider"))
        if provider is None:
            continue
        row = dict(raw_row)
        row["provider"] = provider
        valid_rows.append(row)
        source = str(row.get("run_source") or "")
        key = (provider, source)
        selected, total = source_counts.get(key, (0, 0))
        source_counts[key] = (
            selected + 1,
            max(total, int(row.get("source_total_runs") or 0)),
        )

    ops_times: dict[str, list[datetime]] = defaultdict(list)
    for row in valid_rows:
        if row.get("run_source") == "ops_crawler_runs" and isinstance(row.get("run_at"), datetime):
            ops_times[row["provider"]].append(row["run_at"])

    deduplicated: list[dict[str, Any]] = []
    for row in valid_rows:
        if row.get("run_source") == "crawler_run_log" and isinstance(row.get("run_at"), datetime):
            if any(
                abs((candidate - row["run_at"]).total_seconds()) <= 30
                for candidate in ops_times.get(row["provider"], [])
            ):
                continue
        deduplicated.append(row)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in deduplicated:
        grouped[row["provider"]].append(row)

    evidence: dict[str, dict[str, Any]] = {}
    for provider, provider_rows in grouped.items():
        provider_rows.sort(
            key=lambda row: (
                row.get("run_at") or datetime.min.replace(tzinfo=timezone.utc),
                str(row.get("run_id") or ""),
            ),
            reverse=True,
        )
        latest = provider_rows[0]
        failures = 0
        streak_terminated = False
        for row in provider_rows:
            if str(row.get("status") or "").casefold() in _IMPROVEMENT_FAILURE_STATUSES:
                failures += 1
                continue
            streak_terminated = True
            break
        sources = {source for candidate_provider, source in source_counts if candidate_provider == provider}
        history_complete = all(
            source_counts.get((provider, source), (0, 0))[0] >= source_counts.get((provider, source), (0, 0))[1]
            for source in sources
        )
        streak_exact = streak_terminated or history_complete
        success_times = [
            row.get("source_last_success_at")
            for row in provider_rows
            if isinstance(row.get("source_last_success_at"), datetime)
        ]
        error_category, error_code = _normalized_improvement_error(
            latest.get("status"),
            latest.get("raw_error_code"),
            latest.get("raw_error_message"),
        )
        evidence[provider] = {
            "last_run_status": latest.get("status"),
            "last_run_at": latest.get("run_at"),
            "last_success_at": max(success_times) if success_times else None,
            "consecutive_failures": failures if streak_exact else None,
            "failure_streak_lower_bound": failures,
            "run_history_complete": streak_exact,
            "error_category": error_category,
            "error_code": error_code,
        }
    return evidence


def _improvement_reasons_and_score(metrics: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    reasons: list[dict[str, Any]] = []

    def add(code: str, label: str, points: int) -> None:
        reasons.append({"code": code, "label": label, "points": points})

    failure_count = metrics.get("consecutive_failures")
    failure_lower_bound = int(metrics.get("failure_streak_lower_bound") or 0)
    effective_failures = int(failure_count) if failure_count is not None else failure_lower_bound
    if effective_failures >= 3:
        add("failure_streak_3_plus", "연속 실패가 3회 이상입니다.", 35)
    elif effective_failures >= 1:
        add("recent_failure", "최근 실행이 실패했습니다.", 20)

    no_run_history = bool(metrics.get("no_run_history"))
    if no_run_history:
        add("no_run_history", "등록된 실행 대상의 실행 이력이 없습니다.", 30)

    error_category = metrics.get("error_category")
    error_points = {
        "source_contract": ("source_contract", "원본 구조 또는 수집 계약 변경이 의심됩니다.", 20),
        "collection_limit": ("collection_limit", "수집 한도 도달이 감지되었습니다.", 18),
        "scheduler": ("scheduler", "스케줄러 또는 Worker 상태 확인이 필요합니다.", 16),
        "partial_failure": ("partial_failure", "일부 대상만 수집에 성공했습니다.", 14),
        "timeout": ("timeout", "수집 요청이 시간 초과되었습니다.", 12),
        "transport": ("transport", "원본 사이트 연결 실패가 감지되었습니다.", 12),
        "unknown": ("unknown_error", "분류되지 않은 실행 실패 근거가 있습니다.", 8),
    }
    if error_category in error_points and not no_run_history:
        add(*error_points[error_category])

    stale_7d = metrics.get("stale_7d_count")
    stale_48h = metrics.get("stale_48h_count")
    if stale_7d is not None and int(stale_7d) > 0:
        add("stale_observation_7d", "7일 넘게 다시 관측되지 않은 활성 강좌가 있습니다.", 28)
    elif stale_48h is not None and int(stale_48h) > 0:
        add("stale_observation_48h", "48시간 넘게 다시 관측되지 않은 활성 강좌가 있습니다.", 14)

    average_score = metrics.get("quality_average_score")
    if average_score is not None and float(average_score) < 60:
        add("quality_average_critical", "평균 데이터 품질 점수가 60점 미만입니다.", 18)
    elif average_score is not None and float(average_score) < 80:
        add("quality_average_low", "평균 데이터 품질 점수가 80점 미만입니다.", 10)
    bad_count = metrics.get("quality_bad_count")
    if bad_count is not None and int(bad_count) > 0:
        add("bad_quality_courses", "품질 등급이 bad인 강좌가 있습니다.", 12)
    issue_count = metrics.get("active_quality_issue_count")
    if issue_count is not None and int(issue_count) > 0:
        add("active_quality_issues", "처리되지 않은 품질 이슈가 있습니다.", 12)

    active_count = metrics.get("active_course_count")
    if reasons and active_count is not None:
        if int(active_count) >= 1_000:
            add("large_impact", "영향 가능한 활성 강좌가 1,000개 이상입니다.", 8)
        elif int(active_count) >= 100:
            add("medium_impact", "영향 가능한 활성 강좌가 100개 이상입니다.", 5)
    return reasons, min(100, sum(int(reason["points"]) for reason in reasons))


def _improvement_recommended_action(
    provider: str,
    *,
    error_category: str | None,
    reasons: list[dict[str, Any]],
) -> dict[str, str]:
    query = urlencode({"provider": provider})
    if error_category in {"scheduler", "timeout", "transport", "partial_failure"}:
        return {
            "code": "inspect_runs",
            "label": "실행 이력 확인",
            "href": f"/crawlers?{query}",
        }
    if error_category in {"source_contract", "collection_limit", "unknown"}:
        return {
            "code": "inspect_parser",
            "label": "Parser 근거 확인",
            "href": f"/crawler-studio?{query}",
        }
    if any(
        str(reason.get("code") or "").startswith(("quality_", "bad_quality", "active_quality")) for reason in reasons
    ):
        return {
            "code": "inspect_quality",
            "label": "품질 근거 확인",
            "href": f"/data-quality?{query}",
        }
    return {
        "code": "review_provider",
        "label": "Provider 점검",
        "href": f"/crawler-studio?{query}",
    }


@router.get("/crawlers/improvement-queue")
def crawler_improvement_queue(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Build a bounded, explainable, read-only crawler improvement queue."""

    table_sources = {
        name: table_exists(db, name)
        for name in (
            "courses",
            "ops_crawler_runs",
            "crawler_run_log",
            "course_quality_score",
            "ops_quality_issues",
            "ops_jobs",
        )
    }
    sources = {
        "runs": table_sources["ops_crawler_runs"] or table_sources["crawler_run_log"],
        "freshness": table_sources["courses"],
        "quality_scores": table_sources["course_quality_score"],
        "quality_issues": table_sources["ops_quality_issues"],
    }
    complete = all(sources.values())

    course_rows = _improvement_course_rows(db) if sources["freshness"] else []
    run_rows: list[dict[str, Any]] = []
    if table_sources["ops_crawler_runs"]:
        run_rows.extend(_improvement_ops_run_rows(db, jobs_available=table_sources["ops_jobs"]))
    if table_sources["crawler_run_log"]:
        run_rows.extend(_improvement_legacy_run_rows(db))
    score_rows = (
        _improvement_quality_score_rows(
            db,
            courses_available=sources["freshness"],
        )
        if sources["quality_scores"]
        else []
    )
    issue_rows = _improvement_quality_issue_rows(db) if sources["quality_issues"] else []

    def indexed(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for raw_row in rows:
            provider = _improvement_provider(raw_row.get("provider"))
            if provider is not None:
                result[provider] = dict(raw_row)
        return result

    courses_by_provider = indexed(course_rows)
    scores_by_provider = indexed(score_rows)
    issues_by_provider = indexed(issue_rows)
    runs_by_provider = _improvement_run_evidence(run_rows)
    providers = sorted(
        {
            *courses_by_provider,
            *runs_by_provider,
            *issues_by_provider,
        }
    )
    executable_providers: frozenset[str] = frozenset()
    if sources["runs"]:
        try:
            executable_providers = reviewed_crawler_providers()
        except CrawlerProviderRegistryError:
            pass

    items: list[dict[str, Any]] = []
    for provider in providers:
        course = courses_by_provider.get(provider, {})
        run = runs_by_provider.get(provider, {})
        quality = scores_by_provider.get(provider)
        issue = issues_by_provider.get(provider)
        no_run_history = (
            sources["runs"]
            and provider in executable_providers
            and provider not in runs_by_provider
            and int(course.get("active_course_count") or 0) > 0
        )
        metrics: dict[str, Any] = {
            "active_course_count": (int(course.get("active_course_count") or 0) if sources["freshness"] else None),
            "stale_48h_count": (int(course.get("stale_48h_count") or 0) if sources["freshness"] else None),
            "stale_7d_count": (int(course.get("stale_7d_count") or 0) if sources["freshness"] else None),
            "freshness_unknown_count": (
                int(course.get("freshness_unknown_count") or 0) if sources["freshness"] else None
            ),
            "consecutive_failures": run.get("consecutive_failures"),
            "last_run_status": run.get("last_run_status"),
            "last_run_at": run.get("last_run_at"),
            "last_success_at": run.get("last_success_at"),
            "quality_average_score": (
                float(quality["quality_average_score"])
                if quality is not None and quality.get("quality_average_score") is not None
                else None
            ),
            "quality_bad_count": (int(quality.get("quality_bad_count") or 0) if quality is not None else None),
            "active_quality_issue_count": (
                int(issue.get("active_quality_issue_count") or 0)
                if issue is not None
                else (0 if sources["quality_issues"] else None)
            ),
            "error_category": "scheduler" if no_run_history else run.get("error_category"),
            "error_code": "no_run_history" if no_run_history else run.get("error_code"),
            "failure_streak_lower_bound": run.get("failure_streak_lower_bound", 0),
            "no_run_history": no_run_history,
        }
        reasons, score = _improvement_reasons_and_score(metrics)
        priority = "P0" if score >= 75 else "P1" if score >= 50 else "P2" if score >= 25 else "P3"
        items.append(
            {
                "provider": provider,
                "priority": priority,
                "score": score,
                "evidence_complete": bool(
                    complete
                    and provider in courses_by_provider
                    and provider in runs_by_provider
                    and provider in scores_by_provider
                    and run.get("run_history_complete", False)
                    and metrics["freshness_unknown_count"] == 0
                ),
                **{
                    key: metrics[key]
                    for key in (
                        "active_course_count",
                        "stale_48h_count",
                        "stale_7d_count",
                        "freshness_unknown_count",
                        "consecutive_failures",
                        "last_run_status",
                        "last_run_at",
                        "last_success_at",
                        "quality_average_score",
                        "quality_bad_count",
                        "active_quality_issue_count",
                        "error_category",
                        "error_code",
                    )
                },
                "reasons": reasons,
                "recommended_action": _improvement_recommended_action(
                    provider,
                    error_category=metrics["error_category"],
                    reasons=reasons,
                ),
            }
        )
    items.sort(key=lambda item: (-int(item["score"]), str(item["provider"])))
    total = len(items)
    visible_items = items[:limit]
    return {
        "schema_version": 1,
        "available": bool(sources["freshness"] or sources["runs"] or sources["quality_issues"]),
        "complete": complete,
        "generated_at": datetime.now(timezone.utc),
        "sources": sources,
        "total": total,
        "limit": limit,
        "truncated": total > len(visible_items),
        "items": visible_items,
    }


@router.get("/crawlers")
def crawlers(db: Session = Depends(get_db)) -> dict[str, Any]:
    runs = sorted(
        _merge_crawler_rows(
            _ops_crawler_rows(db, 1_000),
            _legacy_crawler_rows(db, 1_000),
        ),
        key=lambda item: item.get("started_at") or item.get("created_at") or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    course_counts: dict[str, int] = {}
    for row in mapped_rows(
        db.execute(
            text(
                """
                SELECT provider, COUNT(*) AS active_count
                FROM courses
                WHERE is_active = true
                GROUP BY provider
                """
            )
        )
    ):
        course_counts[str(row["provider"])] = int(row["active_count"] or 0)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        provider = str(run.get("provider") or run.get("crawler_name") or "unknown")
        grouped[provider].append(run)
    for provider in course_counts:
        grouped.setdefault(provider, [])

    try:
        executable_providers = reviewed_crawler_providers()
        registry_available = True
    except CrawlerProviderRegistryError:
        executable_providers = frozenset()
        registry_available = False
    runtime_enabled = local_crawler_runtime_enabled()

    items: list[dict[str, Any]] = []
    for provider, provider_runs in grouped.items():
        latest = provider_runs[0] if provider_runs else {}
        consecutive_failures = 0
        for row in provider_runs:
            if row.get("status") in {"failed", "partial_success", "blocked"}:
                consecutive_failures += 1
            else:
                break
        running = next((row for row in provider_runs if row.get("status") in {"queued", "running", "stopping"}), None)
        last_success = next((row for row in provider_runs if row.get("status") == "success"), None)
        items.append(
            {
                "crawler_name": latest.get("crawler_name") or provider,
                "content_type": latest.get("content_type") or "unknown",
                "provider": provider,
                "status": running.get("status") if running else ("idle" if latest else "unknown"),
                "last_run_status": latest.get("status") or "unknown",
                "last_run_trigger": latest.get("trigger") or "unknown",
                "last_run_at": latest.get("started_at") or latest.get("created_at"),
                "last_success_at": (last_success or {}).get("finished_at") or (last_success or {}).get("started_at"),
                "collected_count": int(latest.get("total_count") or 0),
                "new_count": int(latest.get("new_count") or 0),
                "updated_count": int(latest.get("updated_count") or 0),
                "failed_count": int(latest.get("failed_count") or 0),
                "active_course_count": course_counts.get(provider, 0),
                "consecutive_failures": consecutive_failures,
                "latest_run_id": latest.get("id"),
                "can_run": runtime_enabled and provider in executable_providers,
                "run_blocked_reason": (
                    None if provider in executable_providers else "승인된 크롤러 registry에 없는 Provider입니다."
                ),
            }
        )
        if not runtime_enabled:
            items[-1]["run_blocked_reason"] = _crawler_runtime_disabled_detail()
    items.sort(key=lambda item: (-int(item["consecutive_failures"]), str(item["provider"])))
    return {
        "available": bool(runs or course_counts),
        "registry_available": registry_available,
        "items": items,
        "total": len(items),
    }


@router.get("/crawlers/region-coverage")
def crawler_region_coverage(
    refresh: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return experience/education coverage for every canonical municipality.

    The canonical municipality master is merged before collected data, so
    regions without a provider or course remain visible with zero counts.
    """
    snapshot = get_region_collection_snapshot(db, force_refresh=refresh)
    environment = current_environment()
    database_placement = load_production_topology().primary_for("database")
    snapshot["data_source"] = {
        "environment": environment,
        "is_production": environment == "production",
        "production_node": database_placement.node,
        "production_service_host": database_placement.service_host,
        "database_host": _database_service_host(db),
        "database_name": str(getattr(getattr(db.get_bind(), "url", None), "database", "") or ""),
    }
    return snapshot


@router.get("/crawlers/runs")
def crawler_runs(
    run_status: str = Query(default="", alias="status", max_length=32),
    content_type: str = Query(default="", max_length=40),
    provider: str = Query(default="", max_length=100),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    fetch_limit = min(2_000, offset + limit + 500)
    items = _merge_crawler_rows(
        _ops_crawler_rows(db, fetch_limit, provider=provider),
        _legacy_crawler_rows(db, fetch_limit, provider=provider),
    )
    if run_status:
        items = [item for item in items if item.get("status") == run_status]
    if content_type:
        items = [item for item in items if item.get("content_type") == content_type]
    if provider:
        items = [item for item in items if item.get("provider") == provider]
    items.sort(
        key=lambda item: item.get("started_at") or item.get("created_at") or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    total = len(items)
    return _page(items[offset : offset + limit], total=total, limit=limit, offset=offset, available=bool(items))


def _crawler_run_detail(db: Session, run_id: str) -> dict[str, Any] | None:
    if run_id.startswith("legacy-"):
        raw_id = run_id.removeprefix("legacy-")
        if not raw_id.isdigit() or not table_exists(db, "crawler_run_log"):
            return None
        rows = _legacy_crawler_rows(db, 2_000)
        return next((row for row in rows if row["id"] == run_id), None)
    try:
        parsed = UUID(run_id)
    except ValueError:
        return None
    if not table_exists(db, "ops_crawler_runs"):
        return None
    return mapped_one(
        db.execute(
            text(
                """
                SELECT r.id::text, r.crawler_name, r.content_type, r.provider,
                       r.branch, r.source_url, r.current_stage,
                       r.agent_id::text, r.job_id::text, r.status, r.run_mode,
                       r.total_count, r.processed_count, r.success_count,
                       r.failed_count, r.new_count, r.updated_count,
                       r.deleted_candidate_count, r.started_at, r.finished_at,
                       r.created_at, j.progress, j.parameters, j.result,
                       j.error_code AS error_type, j.error_message,
                       COALESCE(NULLIF(j.parameters ->> 'trigger', ''), 'manual') AS trigger,
                       a.name AS agent_name, a.hostname,
                       'ops_crawler_runs' AS source
                FROM ops_crawler_runs r
                LEFT JOIN ops_jobs j ON j.id = r.job_id
                LEFT JOIN ops_agents a ON a.id = r.agent_id
                WHERE r.id = :run_id
                """
            ),
            {"run_id": str(parsed)},
        )
    )


@router.get("/crawlers/runs/{run_id}")
def crawler_run_detail(run_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    item = _crawler_run_detail(db, run_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Crawler run not found")
    return _redact_rows([item])[0]


@router.get("/crawlers/runs/{run_id}/errors")
def crawler_run_errors(
    run_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    detail = _crawler_run_detail(db, run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Crawler run not found")
    if run_id.startswith("legacy-"):
        if not detail.get("error_message") and not detail.get("error_type"):
            return {"available": True, "items": []}
        return {
            "available": True,
            "items": [
                {
                    "id": f"{run_id}-error",
                    "crawler_run_id": run_id,
                    "error_type": detail.get("error_type") or "unknown_error",
                    "provider": detail.get("provider"),
                    "branch": None,
                    "source_url": None,
                    "message": detail.get("error_message") or "No detailed error message was recorded",
                    "stack_trace": None,
                    "screenshot_path": None,
                    "html_path": None,
                    "retry_count": 0,
                    "resolved": False,
                    "created_at": detail.get("started_at"),
                    "source": "crawler_run_log",
                }
            ],
        }
    require_ops_schema(db, "ops_crawler_errors")
    items = mapped_rows(
        db.execute(
            text(
                """
                SELECT id, crawler_run_id::text, error_type, provider, branch,
                       source_url, message, stack_trace, screenshot_path,
                       html_path, retry_count, resolved, created_at
                FROM ops_crawler_errors
                WHERE crawler_run_id = :run_id
                ORDER BY created_at DESC
                LIMIT :limit
                """
            ),
            {"run_id": run_id, "limit": limit},
        )
    )
    return {"available": True, "items": _redact_rows(items)}


@router.post("/crawlers/run", status_code=status.HTTP_202_ACCEPTED)
def run_crawler(
    payload: CrawlerRunRequest,
    request: Request,
    user: models.User = Depends(require_ops_operator),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if not local_crawler_runtime_enabled():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_crawler_runtime_disabled_detail(),
        )
    parameters = payload.model_dump()
    if payload.provider:
        provider = payload.provider.strip().upper()
        try:
            resolve_crawler_provider_execution(provider)
        except CrawlerProviderRegistryError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="승인된 크롤러 registry에서 실행할 수 없는 Provider입니다.",
            ) from exc
        parameters["provider"] = provider
    target = parameters.get("provider") or payload.branch or payload.region or payload.url or payload.content_type
    target_key = f"{payload.scope}:{target or 'all'}"
    job = enqueue_job(
        db,
        job_type="crawler_run",
        requested_by=user.id,
        parameters=parameters,
        target_key=target_key,
        max_retries=payload.max_retries,
    )
    run = mapped_one(
        db.execute(
            text(
                """
                INSERT INTO ops_crawler_runs (
                    crawler_name, content_type, provider, branch, source_url,
                    job_id, status, run_mode
                )
                VALUES (
                    :crawler_name, :content_type, :provider, :branch,
                    :source_url, :job_id, 'queued', :run_mode
                )
                RETURNING id::text, crawler_name, content_type, provider,
                          branch, source_url, job_id::text, status, run_mode,
                          created_at
                """
            ),
            {
                "crawler_name": payload.provider or f"{payload.content_type}:{payload.scope}",
                "content_type": payload.content_type,
                "provider": payload.provider,
                "branch": payload.branch,
                "source_url": payload.url,
                "job_id": str(job["id"]),
                "run_mode": payload.run_mode,
            },
        )
    )
    append_audit(
        db,
        request,
        user_id=user.id,
        action="crawler.run",
        resource_type="crawler_run",
        resource_id=run["id"] if run else None,
        after_data=parameters,
        result="success",
        job_id=job["id"],
    )
    db.commit()
    return {"job": job, "crawler_run": run}


@router.post("/crawlers/parser-probe", status_code=status.HTTP_202_ACCEPTED)
def run_parser_probe(
    payload: ParserProbeRequest,
    request: Request,
    user: models.User = Depends(require_ops_operator),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if current_environment() in {"production", "staging"}:
        # Distributed workers pull from the separate shared staging control DB.
        # There is no authenticated primary-to-control outbox for agent_command
        # jobs yet, so returning 202 here would create work that can never be
        # claimed.  Restore this endpoint only with a pinned target agent and
        # exact desired-release identity on the control side.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "distributed_parser_probe_unavailable",
                "message": "Parser probe routing is unavailable during distributed crawler operation.",
            },
        )
    parameters = {
        "command": "parser_probe",
        "url": payload.url,
        "timeout": payload.timeout,
    }
    job = enqueue_job(
        db,
        job_type="agent_command",
        requested_by=user.id,
        parameters=parameters,
        target_key=f"parser-probe:{payload.url}",
        max_retries=0,
    )
    append_audit(
        db,
        request,
        user_id=user.id,
        action="crawler.parser_probe",
        resource_type="url",
        resource_id=payload.url,
        after_data={"timeout": payload.timeout},
        job_id=job["id"],
    )
    db.commit()
    return {"job": job}


@router.post("/crawlers/runs/{run_id}/stop", status_code=status.HTTP_202_ACCEPTED)
def stop_crawler_run(
    run_id: UUID,
    payload: JobActionRequest,
    request: Request,
    user: models.User = Depends(require_ops_operator),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    require_ops_schema(db, "ops_crawler_runs", "ops_jobs", "ops_audit_logs")
    run = mapped_one(
        db.execute(
            text(
                """
                SELECT r.id::text, r.status, r.job_id::text, j.status AS job_status
                FROM ops_crawler_runs r
                LEFT JOIN ops_jobs j ON j.id = r.job_id
                WHERE r.id = :run_id
                FOR UPDATE OF r
                """
            ),
            {"run_id": str(run_id)},
        )
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Crawler run not found")
    if run["status"] not in {"queued", "running", "stopping"}:
        raise HTTPException(status_code=409, detail="Crawler run is not active")
    if run.get("job_id"):
        db.execute(
            text(
                """
                UPDATE ops_jobs
                SET cancel_requested_at = CURRENT_TIMESTAMP,
                    status = CASE WHEN status = 'queued' THEN 'cancelled' ELSE status END,
                    finished_at = CASE WHEN status = 'queued' THEN CURRENT_TIMESTAMP ELSE finished_at END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :job_id AND status IN ('queued', 'assigned', 'running')
                """
            ),
            {"job_id": run["job_id"]},
        )
        add_job_log(
            db,
            run["job_id"],
            "운영자가 크롤러 중지를 요청했습니다.",
            level="warning",
            metadata={"reason": payload.reason},
        )
    db.execute(
        text(
            """
            UPDATE ops_crawler_runs
            SET status = CASE WHEN status = 'queued' THEN 'cancelled' ELSE 'stopping' END
            WHERE id = :run_id
            """
        ),
        {"run_id": str(run_id)},
    )
    append_audit(
        db,
        request,
        user_id=user.id,
        action="crawler.stop",
        resource_type="crawler_run",
        resource_id=run_id,
        before_data={"status": run["status"]},
        after_data={"status": "stopping", "reason": payload.reason},
        job_id=run.get("job_id"),
    )
    db.commit()
    return {"id": str(run_id), "status": "stopping", "job_id": run.get("job_id")}


@router.post("/crawlers/runs/{run_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_crawler_run(
    run_id: UUID,
    payload: JobActionRequest,
    request: Request,
    user: models.User = Depends(require_ops_operator),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if not local_crawler_runtime_enabled():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_crawler_runtime_disabled_detail(),
        )
    require_ops_schema(db, "ops_crawler_runs", "ops_jobs")
    original = mapped_one(
        db.execute(
            text(
                """
                SELECT r.id::text, r.crawler_name, r.content_type, r.provider,
                       r.branch, r.source_url, r.run_mode, r.status,
                       r.job_id::text, j.parameters, j.max_retries
                FROM ops_crawler_runs r
                LEFT JOIN ops_jobs j ON j.id = r.job_id
                WHERE r.id = :run_id
                """
            ),
            {"run_id": str(run_id)},
        )
    )
    if original is None:
        raise HTTPException(status_code=404, detail="Crawler run not found")
    if original["status"] not in {"failed", "partial_success", "cancelled", "blocked"}:
        raise HTTPException(status_code=409, detail="Only a completed unsuccessful run can be retried")
    parameters = dict(original.get("parameters") or {})
    parameters["retry_of"] = str(run_id)
    if payload.reason:
        parameters["retry_reason"] = payload.reason
    job = enqueue_job(
        db,
        job_type="crawler_retry",
        requested_by=user.id,
        parameters=parameters,
        target_key=f"retry:{run_id}",
        max_retries=int(original.get("max_retries") or 0),
        parent_job_id=original.get("job_id"),
    )
    new_run = mapped_one(
        db.execute(
            text(
                """
                INSERT INTO ops_crawler_runs (
                    crawler_name, content_type, provider, branch, source_url,
                    job_id, status, run_mode
                )
                VALUES (
                    :crawler_name, :content_type, :provider, :branch,
                    :source_url, :job_id, 'queued', :run_mode
                )
                RETURNING id::text, job_id::text, status, created_at
                """
            ),
            {
                **original,
                "job_id": str(job["id"]),
            },
        )
    )
    append_audit(
        db,
        request,
        user_id=user.id,
        action="crawler.retry",
        resource_type="crawler_run",
        resource_id=new_run["id"] if new_run else None,
        before_data={"run_id": str(run_id), "status": original["status"]},
        after_data={"reason": payload.reason},
        job_id=job["id"],
    )
    db.commit()
    return {"job": job, "crawler_run": new_run}


@router.get("/quality/providers")
def quality_providers(
    content_type: str = Query(default="", max_length=40),
    provider: str = Query(default="", max_length=100),
    category: str = Query(default="", max_length=100),
    level: Literal["major", "detail"] = Query(default="major"),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    score_available = table_exists(db, "course_quality_score")
    conditions = ["c.is_active = true", f"{CONTENT_TYPE_SQL} <> 'unknown'"]
    params: dict[str, Any] = {"limit": limit}
    category_sql = MAJOR_CATEGORY_SQL if level == "major" else CONTENT_CATEGORY_SQL
    if content_type:
        conditions.append(f"{CONTENT_TYPE_SQL} = :content_type")
        params["content_type"] = content_type
    if provider:
        conditions.append("c.provider = :provider")
        params["provider"] = provider
    if category:
        conditions.append(f"{category_sql} = :category")
        params["category"] = category
    quality_select = (
        """
        ROUND(AVG(q.total_score), 1) AS average_score,
        COUNT(*) FILTER (WHERE q.grade = 'good') AS good_count,
        COUNT(*) FILTER (WHERE q.grade = 'warning') AS warning_count,
        COUNT(*) FILTER (WHERE q.grade = 'bad') AS bad_count,
        COUNT(*) FILTER (WHERE q.id IS NULL) AS unchecked_count,
        COUNT(*) FILTER (WHERE q.id IS NOT NULL) AS checked_count,
        MAX(q.checked_at) AS last_checked_at
        """
        if score_available
        else """
        NULL::numeric AS average_score,
        0::bigint AS good_count,
        0::bigint AS warning_count,
        0::bigint AS bad_count,
        COUNT(*) AS unchecked_count,
        0::bigint AS checked_count,
        NULL::timestamptz AS last_checked_at
        """
    )
    quality_join = "LEFT JOIN course_quality_score q ON q.course_id = c.id" if score_available else ""
    field_counts = ",\n".join(
        f"COUNT(*) FILTER (WHERE {predicate}) AS {field}_count"
        for field, predicate in CATEGORY_QUALITY_FIELD_SQL.items()
    )
    field_count_sum = " + ".join(
        f"COUNT(*) FILTER (WHERE {predicate})" for predicate in CATEGORY_QUALITY_FIELD_SQL.values()
    )
    all_fields_present = " AND ".join(f"({predicate})" for predicate in CATEGORY_QUALITY_FIELD_SQL.values())
    rows = mapped_rows(
        db.execute(
            text(
                f"""
                SELECT c.provider, {CONTENT_TYPE_SQL} AS content_type,
                       COUNT(*) AS active_count,
                       {field_counts},
                       COUNT(*) FILTER (
                           WHERE {all_fields_present}
                       ) AS complete_count,
                       ROUND(
                           100.0 * ({field_count_sum})
                           / NULLIF(COUNT(*) * {len(CATEGORY_QUALITY_FIELD_SQL)}, 0),
                           1
                       ) AS field_completeness,
                       COUNT(*) FILTER (
                           WHERE {CATEGORY_ENCODING_ISSUE_SQL}
                       ) AS encoding_issue_count,
                       {quality_select},
                       array_remove(
                           array_agg(
                               DISTINCT COALESCE(
                                   b.website_url,
                                   c.raw_url,
                                   c.application_url
                               )
                           ),
                           NULL
                       ) AS provider_urls
                FROM courses c
                LEFT JOIN branches b ON b.id = c.branch_id
                {quality_join}
                WHERE {" AND ".join(conditions)}
                GROUP BY c.provider, {CONTENT_TYPE_SQL}
                ORDER BY field_completeness ASC,
                         encoding_issue_count DESC,
                         active_count DESC,
                         c.provider
                LIMIT :limit
                """
            ),
            params,
        )
    )
    for row in rows:
        row["provider_urls"] = [
            safe_url
            for value in list(row.get("provider_urls") or [])[:10]
            if (safe_url := safe_external_http_url(value))
        ]
    return {"available": True, "items": rows, "total": len(rows)}


def _suggest_gap_parser_family(
    provider: str,
    samples: list[dict[str, Any]],
) -> tuple[str, str]:
    evidence = " ".join(
        str(value or "")
        for row in samples
        for value in (
            row.get("source_url"),
            row.get("current_parser"),
            row.get("discovery_status"),
        )
    ).lower()
    patterns = (
        ("lecture.es", "lecture.es list/detail", "lecture.es 공통 목록·상세 구조가 감지되었습니다."),
        ("webedclctrelist.do", "webEdcLctreList list/detail", "webEdcLctreList 공통 지자체 구조가 감지되었습니다."),
        ("learninglist.do", "learningList list/detail", "learningList 공통 지자체 구조가 감지되었습니다."),
        ("reserve.busan.go.kr", "Busan reservation family", "부산 통합예약 공통 구조가 감지되었습니다."),
    )
    for token, family, reason in patterns:
        if token in evidence:
            return family, reason
    current_parsers = [
        str(row.get("current_parser") or "").strip() for row in samples if str(row.get("current_parser") or "").strip()
    ]
    specific = next(
        (parser for parser in current_parsers if not parser.lower().startswith("generic")),
        "",
    )
    if specific:
        return specific, "현재 전용 parser를 유지하고 누락 필드 selector를 보강하세요."
    if provider.upper().startswith("MUNI_") or ".go.kr" in evidence:
        return "municipal board/list + detail", "지자체 목록에서 상세 페이지를 따라가는 공통 family가 적합합니다."
    return "generic list + detail", "목록·상세 샘플을 기준으로 전용 selector 승격을 검토하세요."


@router.get("/quality/gap-samples")
def quality_gap_samples(
    provider: str = Query(min_length=1, max_length=100),
    content_type: str = Query(default="", max_length=40),
    category: str = Query(default="", max_length=100),
    level: Literal["major", "detail"] = Query(default="major"),
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return provider-local missing-field evidence and a parser-family hint."""

    category_sql = MAJOR_CATEGORY_SQL if level == "major" else CONTENT_CATEGORY_SQL
    conditions = [
        "c.is_active = true",
        "c.provider = :provider",
        f"NOT ({' AND '.join(f'({predicate})' for predicate in CATEGORY_QUALITY_FIELD_SQL.values())})",
    ]
    params: dict[str, Any] = {"provider": provider, "limit": limit}
    if content_type:
        conditions.append(f"{CONTENT_TYPE_SQL} = :content_type")
        params["content_type"] = content_type
    if category:
        conditions.append(f"{category_sql} = :category")
        params["category"] = category
    missing_items = ", ".join(
        f"CASE WHEN NOT ({predicate}) THEN '{field}' END" for field, predicate in CATEGORY_QUALITY_FIELD_SQL.items()
    )
    missing_counts = ",\n".join(
        f"COUNT(*) FILTER (WHERE NOT ({predicate})) OVER () AS missing_{field}_count"
        for field, predicate in CATEGORY_QUALITY_FIELD_SQL.items()
    )
    rows = mapped_rows(
        db.execute(
            text(
                f"""
                SELECT c.id::text, c.provider, c.title, c.status,
                       COALESCE(b.name, c.venue_name) AS branch,
                       array_remove(ARRAY[{missing_items}], NULL) AS missing_fields,
                       COALESCE(
                           c.raw_fields->>'parser',
                           c.discovery_status,
                           c.collection_type
                       ) AS current_parser,
                       c.discovery_status,
                       COALESCE(
                           c.raw_fields->>'source_endpoint',
                           c.raw_fields->>'source_url',
                           c.raw_url,
                           c.application_url,
                           b.website_url
                       ) AS source_url,
                       c.last_seen_at,
                       COUNT(*) OVER () AS total,
                       {missing_counts}
                FROM courses c
                LEFT JOIN branches b ON b.id = c.branch_id
                WHERE {" AND ".join(conditions)}
                ORDER BY cardinality(array_remove(ARRAY[{missing_items}], NULL)) DESC,
                         c.last_seen_at DESC NULLS LAST,
                         c.id
                LIMIT :limit
                """
            ),
            params,
        )
    )
    for row in rows:
        row["source_url"] = safe_external_http_url(row.get("source_url")) or None
        row["missing_fields"] = list(row.get("missing_fields") or [])
    family, reason = _suggest_gap_parser_family(provider, rows)
    first = rows[0] if rows else {}
    counts = {field: int(first.get(f"missing_{field}_count") or 0) for field in CATEGORY_QUALITY_FIELD_SQL}
    total = int(first.get("total") or 0)
    for row in rows:
        row.pop("total", None)
        for field in CATEGORY_QUALITY_FIELD_SQL:
            row.pop(f"missing_{field}_count", None)
    return {
        "available": True,
        "provider": provider,
        "total": total,
        "items": rows,
        "missing_counts": counts,
        "suggested_parser_family": family,
        "suggestion_reason": reason,
    }


@router.get("/quality/categories")
def quality_categories(
    content_type: str = Query(default="", max_length=40),
    category: str = Query(default="", max_length=100),
    level: Literal["major", "detail"] = Query(default="detail"),
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    score_available = table_exists(db, "course_quality_score")
    conditions = ["c.is_active = true", f"{CONTENT_TYPE_SQL} <> 'unknown'"]
    params: dict[str, Any] = {"limit": limit}
    category_sql = MAJOR_CATEGORY_SQL if level == "major" else CONTENT_CATEGORY_SQL
    if content_type:
        conditions.append(f"{CONTENT_TYPE_SQL} = :content_type")
        params["content_type"] = content_type
    if category:
        conditions.append(f"{category_sql} = :category")
        params["category"] = category
    quality_select = (
        """
        ROUND(AVG(q.total_score), 1) AS average_score,
        COUNT(*) FILTER (WHERE q.grade = 'good') AS good_count,
        COUNT(*) FILTER (WHERE q.grade = 'warning') AS warning_count,
        COUNT(*) FILTER (WHERE q.grade = 'bad') AS bad_count,
        COUNT(*) FILTER (WHERE q.id IS NULL) AS unchecked_count,
        COUNT(*) FILTER (WHERE q.id IS NOT NULL) AS checked_count,
        MAX(q.checked_at) AS last_checked_at
        """
        if score_available
        else """
        NULL::numeric AS average_score,
        0::bigint AS good_count,
        0::bigint AS warning_count,
        0::bigint AS bad_count,
        COUNT(*) AS unchecked_count,
        0::bigint AS checked_count,
        NULL::timestamptz AS last_checked_at
        """
    )
    quality_join = "LEFT JOIN course_quality_score q ON q.course_id = c.id" if score_available else ""
    field_counts = ",\n".join(
        f"COUNT(*) FILTER (WHERE {predicate}) AS {field}_count"
        for field, predicate in CATEGORY_QUALITY_FIELD_SQL.items()
    )
    field_count_sum = " + ".join(
        f"COUNT(*) FILTER (WHERE {predicate})" for predicate in CATEGORY_QUALITY_FIELD_SQL.values()
    )
    all_fields_present = " AND ".join(f"({predicate})" for predicate in CATEGORY_QUALITY_FIELD_SQL.values())
    rows = mapped_rows(
        db.execute(
            text(
                f"""
                SELECT {CONTENT_TYPE_SQL} AS content_type,
                       {category_sql} AS category,
                       COUNT(*) AS active_count,
                       COUNT(DISTINCT c.provider) AS provider_count,
                       {field_counts},
                       COUNT(*) FILTER (
                           WHERE {all_fields_present}
                       ) AS complete_count,
                       ROUND(
                           100.0 * ({field_count_sum})
                           / NULLIF(COUNT(*) * {len(CATEGORY_QUALITY_FIELD_SQL)}, 0),
                           1
                       ) AS field_completeness,
                       COUNT(*) FILTER (
                           WHERE {CATEGORY_ENCODING_ISSUE_SQL}
                       ) AS encoding_issue_count,
                       {quality_select}
                FROM courses c
                LEFT JOIN branches b ON b.id = c.branch_id
                {quality_join}
                WHERE {" AND ".join(conditions)}
                GROUP BY {CONTENT_TYPE_SQL}, {category_sql}
                ORDER BY field_completeness ASC,
                         encoding_issue_count DESC,
                         bad_count DESC,
                         active_count DESC,
                         category
                LIMIT :limit
                """
            ),
            params,
        )
    )
    return {"available": True, "items": rows, "total": len(rows)}


@router.get("/quality/issues")
def quality_issues(
    issue_status: str = Query(default="", alias="status", max_length=32),
    severity: str = Query(default="", max_length=32),
    content_type: str = Query(default="", max_length=40),
    provider: str = Query(default="", max_length=100),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if not table_exists(db, "ops_quality_issues"):
        return _page([], total=0, limit=limit, offset=offset, available=False)
    conditions = ["1=1"]
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    for column, value in (
        ("status", issue_status),
        ("severity", severity),
        ("content_type", content_type),
        ("provider", provider),
    ):
        if value:
            conditions.append(f"q.{column} = :{column}")
            params[column] = value
    where_sql = " AND ".join(conditions)
    total = int(
        db.execute(
            text(f"SELECT COUNT(*) FROM ops_quality_issues q WHERE {where_sql}"),
            params,
        ).scalar()
        or 0
    )
    items = mapped_rows(
        db.execute(
            text(
                f"""
                SELECT q.id::text, q.issue_type, q.severity, q.content_type,
                       q.resource_type, q.resource_id, q.provider, q.branch,
                       q.field_name, q.current_value, q.previous_value,
                       q.status, q.assigned_to::text, q.auto_fixable,
                       q.blocked_sync, q.detected_at, q.resolved_by::text,
                       q.resolved_at, q.metadata, q.created_at, q.updated_at,
                       CASE
                           WHEN q.resource_type = 'course'
                           THEN COALESCE(c.application_url, c.raw_url)
                           ELSE NULL
                       END AS source_url
                FROM ops_quality_issues q
                LEFT JOIN courses c
                  ON q.resource_type = 'course' AND c.id::text = q.resource_id
                WHERE {where_sql}
                ORDER BY
                    CASE q.severity WHEN 'critical' THEN 1 WHEN 'warning' THEN 2 ELSE 3 END,
                    q.detected_at DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )
    )
    return _page(_redact_rows(items), total=total, limit=limit, offset=offset)


@router.get("/quality/address-fixes")
def quality_address_fixes(
    provider: str = Query(default="", max_length=100),
    mode: Literal["all", "missing_address", "missing_coordinates", "out_of_korea"] = Query(default="all"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0, le=100_000),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    geocode_select_sql, geocode_fields_available = _optional_branch_geocode_select(db)
    conditions = [
        """
        (
            b.address IS NULL OR btrim(b.address) = ''
            OR b.lat IS NULL OR b.lon IS NULL
            OR (
                b.lat IS NOT NULL AND b.lon IS NOT NULL
                AND NOT (b.lat BETWEEN 32.0 AND 39.5 AND b.lon BETWEEN 123.0 AND 132.5)
            )
        )
        """
    ]
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if provider:
        conditions.append("b.provider = :provider")
        params["provider"] = provider
    if mode == "missing_address":
        conditions.append("(b.address IS NULL OR btrim(b.address) = '')")
    elif mode == "missing_coordinates":
        conditions.append("(b.lat IS NULL OR b.lon IS NULL)")
    elif mode == "out_of_korea":
        conditions.append(
            """
            b.lat IS NOT NULL AND b.lon IS NOT NULL
            AND NOT (b.lat BETWEEN 32.0 AND 39.5 AND b.lon BETWEEN 123.0 AND 132.5)
            """
        )
    where_sql = " AND ".join(f"({condition})" for condition in conditions)
    total = int(db.execute(text(f"SELECT COUNT(*) FROM branches b WHERE {where_sql}"), params).scalar() or 0)
    items = mapped_rows(
        db.execute(
            text(
                f"""
                SELECT b.id::text, b.provider, b.branch_code, b.name,
                       b.address, b.lat, b.lon, b.region_sido,
                       b.region_sigungu, b.address_source,
                       b.coordinate_source, b.location_confidence,
                       b.location_verified, b.location_checked_at,
                       b.location_query, b.website_url, b.updated_at,
                       {geocode_select_sql}
                FROM branches b
                WHERE {where_sql}
                ORDER BY b.provider, b.name
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )
    )
    redacted_items = _redact_rows(items)
    for item in redacted_items:
        item["geocode_candidates"] = sanitize_for_audit(item.get("geocode_candidates"))
    response = _page(redacted_items, total=total, limit=limit, offset=offset)
    response["geocode_fields_available"] = geocode_fields_available
    return response


@router.get("/quality/issues/{issue_id}")
def quality_issue_detail(issue_id: UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    require_ops_schema(db, "ops_quality_issues")
    item = mapped_one(
        db.execute(
            text(
                """
                SELECT q.id::text, q.issue_type, q.severity, q.content_type,
                       q.resource_type, q.resource_id, q.provider, q.branch,
                       q.field_name, q.current_value, q.previous_value,
                       q.status, q.assigned_to::text, q.auto_fixable,
                       q.blocked_sync, q.detected_at, q.resolved_by::text,
                       q.resolved_at, q.metadata, q.created_at, q.updated_at,
                       c.title, COALESCE(c.application_url, c.raw_url) AS source_url
                FROM ops_quality_issues q
                LEFT JOIN courses c
                  ON q.resource_type = 'course' AND c.id::text = q.resource_id
                WHERE q.id = :issue_id
                """
            ),
            {"issue_id": str(issue_id)},
        )
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Quality issue not found")
    return _redact_rows([item])[0]


@router.post("/quality/scan", status_code=status.HTTP_202_ACCEPTED)
def run_quality_scan(
    payload: QualityScanRequest,
    request: Request,
    user: models.User = Depends(require_ops_operator),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    parameters = payload.model_dump()
    target = payload.provider or payload.branch or payload.content_type
    job = enqueue_job(
        db,
        job_type="data_quality_scan",
        requested_by=user.id,
        parameters=parameters,
        target_key=f"quality:{target}",
        max_retries=payload.max_retries,
    )
    append_audit(
        db,
        request,
        user_id=user.id,
        action="quality.scan",
        resource_type="data_quality",
        resource_id=target,
        after_data=parameters,
        job_id=job["id"],
    )
    db.commit()
    return {"job": job}


def _change_quality_issue_status(
    db: Session,
    request: Request,
    user: models.User,
    issue_id: UUID,
    *,
    target_status: Literal["resolved", "ignored"],
    reason: str,
) -> dict[str, Any]:
    require_ops_schema(db, "ops_quality_issues", "ops_audit_logs")
    before = mapped_one(
        db.execute(
            text(
                """
                SELECT id::text, status, issue_type, provider, branch
                FROM ops_quality_issues
                WHERE id = :issue_id
                FOR UPDATE
                """
            ),
            {"issue_id": str(issue_id)},
        )
    )
    if before is None:
        raise HTTPException(status_code=404, detail="Quality issue not found")
    if before["status"] not in {"open", "reviewing"}:
        raise HTTPException(status_code=409, detail="Quality issue is already closed")
    after = mapped_one(
        db.execute(
            text(
                """
                UPDATE ops_quality_issues
                SET status = :target_status,
                    resolved_by = :user_id,
                    resolved_at = CURRENT_TIMESTAMP,
                    metadata = metadata || jsonb_build_object(
                        'resolution_reason', :reason,
                        'resolution_action', :target_status
                    ),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :issue_id
                RETURNING id::text, status, resolved_by::text, resolved_at, updated_at
                """
            ),
            {
                "issue_id": str(issue_id),
                "target_status": target_status,
                "user_id": str(user.id),
                "reason": reason,
            },
        )
    )
    append_audit(
        db,
        request,
        user_id=user.id,
        action=f"quality.{target_status}",
        resource_type="quality_issue",
        resource_id=issue_id,
        before_data=before,
        after_data={**(after or {}), "reason": reason},
    )
    db.commit()
    return after or {}


@router.post("/quality/issues/{issue_id}/resolve")
def resolve_quality_issue(
    issue_id: UUID,
    payload: IssueActionRequest,
    request: Request,
    user: models.User = Depends(require_ops_operator),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _change_quality_issue_status(
        db,
        request,
        user,
        issue_id,
        target_status="resolved",
        reason=payload.reason,
    )


@router.post("/quality/issues/{issue_id}/ignore")
def ignore_quality_issue(
    issue_id: UUID,
    payload: IssueActionRequest,
    request: Request,
    user: models.User = Depends(require_ops_operator),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _change_quality_issue_status(
        db,
        request,
        user,
        issue_id,
        target_status="ignored",
        reason=payload.reason,
    )


@router.get("/jobs")
def jobs(
    job_status: str = Query(default="", alias="status", max_length=32),
    job_type: str = Query(default="", max_length=60),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if not table_exists(db, "ops_jobs"):
        return _page([], total=0, limit=limit, offset=offset, available=False)
    conditions = ["environment = :environment"]
    params: dict[str, Any] = {
        "environment": current_environment(),
        "limit": limit,
        "offset": offset,
    }
    if job_status:
        conditions.append("status = :status")
        params["status"] = job_status
    if job_type:
        conditions.append("job_type = :job_type")
        params["job_type"] = job_type
    where_sql = " AND ".join(conditions)
    total = int(db.execute(text(f"SELECT COUNT(*) FROM ops_jobs WHERE {where_sql}"), params).scalar() or 0)
    items = mapped_rows(
        db.execute(
            text(
                f"""
                SELECT id::text, job_type, status, environment, agent_id::text,
                       service_id::text, parent_job_id::text, requested_by::text,
                       target_key, progress, error_code, error_message,
                       retry_count, max_retries, queued_at, assigned_at,
                       started_at, heartbeat_at, cancel_requested_at,
                       finished_at, created_at, updated_at
                FROM ops_jobs
                WHERE {where_sql}
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )
    )
    return _page(_redact_rows(items), total=total, limit=limit, offset=offset)


@router.get("/jobs/{job_id}")
def job_detail(job_id: UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    require_ops_schema(db, "ops_jobs")
    item = mapped_one(
        db.execute(
            text(
                """
                SELECT j.id::text, j.job_type, j.status, j.environment,
                       j.agent_id::text, a.name AS agent_name, a.hostname,
                       j.service_id::text, s.service_name, j.parent_job_id::text,
                       j.requested_by::text, u.email AS requested_by_email,
                       j.target_key, j.parameters, j.progress, j.result,
                       j.error_code, j.error_message, j.retry_count,
                       j.max_retries, j.queued_at, j.assigned_at, j.started_at,
                       j.heartbeat_at, j.cancel_requested_at, j.finished_at,
                       j.created_at, j.updated_at
                FROM ops_jobs j
                LEFT JOIN ops_agents a ON a.id = j.agent_id
                LEFT JOIN ops_services s ON s.id = j.service_id
                LEFT JOIN users u ON u.id = j.requested_by
                WHERE j.id = :job_id
                """
            ),
            {"job_id": str(job_id)},
        )
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Job not found")
    item["parameters"] = sanitize_for_audit(item.get("parameters") or {})
    item["result"] = sanitize_for_audit(item.get("result")) if item.get("result") is not None else None
    return _redact_rows([item])[0]


@router.get("/jobs/{job_id}/logs")
def job_logs(
    job_id: UUID,
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=2_000),
    tail: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    require_ops_schema(db, "ops_jobs", "ops_job_logs")
    if not db.execute(text("SELECT 1 FROM ops_jobs WHERE id = :job_id"), {"job_id": str(job_id)}).scalar():
        raise HTTPException(status_code=404, detail="Job not found")
    if tail:
        query = """
            SELECT *
            FROM (
                SELECT id, job_id::text, log_level, message, metadata, created_at
                FROM ops_job_logs
                WHERE job_id = :job_id AND id > :after_id
                ORDER BY id DESC
                LIMIT :limit
            ) recent_logs
            ORDER BY id
        """
    else:
        query = """
            SELECT id, job_id::text, log_level, message, metadata, created_at
            FROM ops_job_logs
            WHERE job_id = :job_id AND id > :after_id
            ORDER BY id
            LIMIT :limit
        """
    items = mapped_rows(
        db.execute(
            text(query),
            {"job_id": str(job_id), "after_id": after_id, "limit": limit},
        )
    )
    return {"available": True, "items": _redact_rows(items)}


@router.post("/jobs/{job_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
def cancel_job(
    job_id: UUID,
    payload: JobActionRequest,
    request: Request,
    user: models.User = Depends(require_ops_operator),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    require_ops_schema(
        db,
        "ops_jobs",
        "ops_job_logs",
        "ops_deployments",
        "ops_audit_logs",
    )
    stale_after_seconds = deployment_heartbeat_lease_seconds()
    before = mapped_one(
        db.execute(
            text(
                """
                SELECT id::text, status, job_type, target_key,
                       assigned_at, started_at, heartbeat_at,
                       (
                           SELECT deployment.deployment_mode
                           FROM ops_deployments deployment
                           WHERE deployment.job_id = ops_jobs.id
                       ) AS deployment_mode,
                       CASE
                           WHEN job_type = 'deployment'
                            AND status = 'assigned'
                            AND started_at IS NULL
                            AND COALESCE(
                                heartbeat_at, assigned_at, updated_at, created_at
                            ) < CURRENT_TIMESTAMP - make_interval(secs => :stale_after_seconds)
                           THEN true
                           ELSE false
                       END AS stale_assignment
                FROM ops_jobs
                WHERE id = :job_id
                FOR UPDATE
                """
            ),
            {
                "job_id": str(job_id),
                "stale_after_seconds": stale_after_seconds,
            },
        )
    )
    if before is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if before["status"] not in ACTIVE_JOB_STATUSES:
        raise HTTPException(status_code=409, detail="Job is not active")
    if before.get("deployment_mode") == "container" and before["status"] != "queued":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "container_deployment_cancellation_forbidden",
                "message": (
                    "원격 container controller가 시작된 뒤에는 취소 상태를 추측할 수 없습니다. "
                    "guard/reconciler가 durable remote state를 확정할 때까지 기다려야 합니다."
                ),
            },
        )
    cancel_immediately = before["status"] == "queued" or bool(before["stale_assignment"])
    new_status = "cancelled" if cancel_immediately else before["status"]
    if before["status"] == "queued":
        disposition = "cancelled_queued"
    elif before["stale_assignment"]:
        disposition = "cancelled_stale_assignment"
    else:
        disposition = "cancellation_requested"
    after = mapped_one(
        db.execute(
            text(
                """
                UPDATE ops_jobs
                SET cancel_requested_at = CURRENT_TIMESTAMP,
                    status = :status,
                    finished_at = CASE WHEN :status = 'cancelled' THEN CURRENT_TIMESTAMP ELSE finished_at END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :job_id
                RETURNING id::text, status, cancel_requested_at, finished_at
                """
            ),
            {"job_id": str(job_id), "status": new_status},
        )
    )
    add_job_log(
        db,
        job_id,
        (
            "실행 중인 worker가 없어 작업을 즉시 취소했습니다."
            if cancel_immediately
            else "실행 중인 worker에 작업 취소를 요청했습니다."
        ),
        level="warning",
        metadata={
            "reason": payload.reason,
            "cancellation_disposition": disposition,
            "stale_after_seconds": stale_after_seconds,
        },
    )
    if before["job_type"] == "deployment" and new_status == "cancelled":
        db.execute(
            text(
                """
                UPDATE ops_deployments
                SET deployment_status = 'cancelled',
                    finished_at = CURRENT_TIMESTAMP
                WHERE job_id = :job_id
                  AND deployment_status IN ('queued', 'running')
                """
            ),
            {"job_id": str(job_id)},
        )
    append_audit(
        db,
        request,
        user_id=user.id,
        action="job.cancel",
        resource_type="job",
        resource_id=job_id,
        before_data=before,
        after_data={
            **(after or {}),
            "reason": payload.reason,
            "cancellation_disposition": disposition,
        },
        job_id=job_id,
    )
    db.commit()
    return {
        **(after or {}),
        "terminal": new_status == "cancelled",
        "cancellation_disposition": disposition,
    }


@router.post("/jobs/{job_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_job(
    job_id: UUID,
    payload: JobActionRequest,
    request: Request,
    user: models.User = Depends(require_ops_operator),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    require_ops_schema(db, "ops_jobs")
    original = mapped_one(
        db.execute(
            text(
                """
                SELECT id::text, job_type, status, target_key, parameters,
                       retry_count, max_retries
                FROM ops_jobs
                WHERE id = :job_id
                """
            ),
            {"job_id": str(job_id)},
        )
    )
    if original is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if original["job_type"] in {"crawler_run", "crawler_retry"} and not local_crawler_runtime_enabled():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_crawler_runtime_disabled_detail(),
        )
    if original["job_type"] in {"deployment", "rollback"}:
        raise HTTPException(
            status_code=409,
            detail="배포는 현재 HEAD와 대상을 다시 검토한 뒤 새 작업으로 등록해야 합니다.",
        )
    if original["status"] not in TERMINAL_JOB_STATUSES - {"success"}:
        raise HTTPException(status_code=409, detail="Only an unsuccessful terminal job can be retried")
    parameters = dict(original.get("parameters") or {})
    parameters["retry_of"] = str(job_id)
    if payload.reason:
        parameters["retry_reason"] = payload.reason
    retried = enqueue_job(
        db,
        job_type=original["job_type"],
        requested_by=user.id,
        parameters=parameters,
        target_key=f"retry:{original.get('target_key') or job_id}",
        max_retries=int(original.get("max_retries") or 0),
        parent_job_id=job_id,
    )
    crawler_run = None
    if original["job_type"] in {"crawler_run", "crawler_retry"} and table_exists(db, "ops_crawler_runs"):
        crawler_run = mapped_one(
            db.execute(
                text(
                    """
                    INSERT INTO ops_crawler_runs (
                        crawler_name, content_type, provider, branch, source_url,
                        job_id, status, run_mode
                    )
                    SELECT crawler_name, content_type, provider, branch, source_url,
                           :new_job_id, 'queued', run_mode
                    FROM ops_crawler_runs
                    WHERE job_id = :original_job_id
                    RETURNING id::text, job_id::text, status, created_at
                    """
                ),
                {"new_job_id": str(retried["id"]), "original_job_id": str(job_id)},
            )
        )
        if crawler_run is None:
            db.rollback()
            raise HTTPException(status_code=409, detail="Original crawler run metadata is unavailable")
    append_audit(
        db,
        request,
        user_id=user.id,
        action="job.retry",
        resource_type="job",
        resource_id=retried["id"],
        before_data={"job_id": str(job_id), "status": original["status"]},
        after_data={"reason": payload.reason},
        job_id=retried["id"],
    )
    db.commit()
    return {"job": retried, "crawler_run": crawler_run}


def _sse_payload(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str, ensure_ascii=False)}\n\n"


def _read_job_stream_batch(job_id: str, last_log_id: int) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Read one SSE batch without retaining a pooled connection between polls."""

    with SessionLocal() as poll_db:
        job = mapped_one(
            poll_db.execute(
                text(
                    """
                    SELECT id::text, status, progress, error_code,
                           error_message, updated_at, finished_at
                    FROM ops_jobs
                    WHERE id = :job_id
                    """
                ),
                {"job_id": job_id},
            )
        )
        if job is None:
            return None, []
        logs = mapped_rows(
            poll_db.execute(
                text(
                    """
                    SELECT id, log_level, message, metadata, created_at
                    FROM ops_job_logs
                    WHERE job_id = :job_id AND id > :last_log_id
                    ORDER BY id
                    LIMIT 200
                    """
                ),
                {"job_id": job_id, "last_log_id": last_log_id},
            )
        )
    job["error_message"] = redact_text(job.get("error_message"), maximum=2_000) or None
    return job, _redact_rows(logs)


async def _job_stream_events(job_id: UUID, request: Request):
    last_log_id = 0
    last_signature = ""
    for _ in range(900):
        if await request.is_disconnected():
            break
        try:
            job, logs = await asyncio.to_thread(_read_job_stream_batch, str(job_id), last_log_id)
        except Exception:
            request_id = getattr(request.state, "request_id", None)
            logger.warning(
                "Ops job stream database poll failed request_id=%s job_id=%s",
                request_id,
                job_id,
                exc_info=True,
            )
            payload = {"detail": "Job stream temporarily unavailable"}
            if request_id:
                payload["request_id"] = request_id
            yield _sse_payload("error", payload)
            break
        if job is None:
            yield _sse_payload("error", {"detail": "Job not found"})
            break
        signature = json.dumps(job, default=str, sort_keys=True)
        if signature != last_signature:
            yield _sse_payload("job", job)
            last_signature = signature
        for log in logs:
            last_log_id = max(last_log_id, int(log["id"]))
            yield _sse_payload("log", log)
        if job["status"] in TERMINAL_JOB_STATUSES and len(logs) < 200:
            yield _sse_payload("end", {"status": job["status"]})
            break
        yield ": heartbeat\n\n"
        if len(logs) < 200:
            await asyncio.sleep(2)


def _job_stream_response(job_id: UUID, request: Request, db: Session) -> StreamingResponse:
    require_ops_schema(db, "ops_jobs", "ops_job_logs")
    if not db.execute(text("SELECT 1 FROM ops_jobs WHERE id = :job_id"), {"job_id": str(job_id)}).scalar():
        raise HTTPException(status_code=404, detail="Job not found")
    # FastAPI yield dependencies otherwise remain alive until StreamingResponse
    # completes. Explicitly release the authenticated request's DB connection;
    # each background poll opens and closes its own short-lived session.
    db.close()
    return StreamingResponse(
        _job_stream_events(job_id, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/jobs/{job_id}/stream")
def job_stream(
    job_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> StreamingResponse:
    return _job_stream_response(job_id, request, db)


@router.get("/crawlers/runs/{run_id}/stream")
def crawler_run_stream(
    run_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> StreamingResponse:
    detail = _crawler_run_detail(db, str(run_id))
    if detail is None or not detail.get("job_id"):
        raise HTTPException(status_code=404, detail="Crawler run or related job not found")
    return _job_stream_response(UUID(detail["job_id"]), request, db)


@router.get("/audit-logs")
def audit_logs(
    action: str = Query(default="", max_length=120),
    resource_type: str = Query(default="", max_length=120),
    result: str = Query(default="", max_length=20),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if not table_exists(db, "ops_audit_logs"):
        return _page([], total=0, limit=limit, offset=offset, available=False)
    conditions = ["1=1"]
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    for column, value in (("action", action), ("resource_type", resource_type), ("result", result)):
        if value:
            conditions.append(f"a.{column} = :{column}")
            params[column] = value
    where_sql = " AND ".join(conditions)
    total = int(db.execute(text(f"SELECT COUNT(*) FROM ops_audit_logs a WHERE {where_sql}"), params).scalar() or 0)
    items = mapped_rows(
        db.execute(
            text(
                f"""
                SELECT a.id, a.user_id::text, u.email AS user_email,
                       a.action, a.resource_type, a.resource_id,
                       a.ip_address::text, a.user_agent, a.result,
                       a.job_id::text, a.created_at
                FROM ops_audit_logs a
                LEFT JOIN users u ON u.id = a.user_id
                WHERE {where_sql}
                ORDER BY a.created_at DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )
    )
    return _page(_redact_rows(items), total=total, limit=limit, offset=offset)


@router.get("/audit-logs/{audit_id}")
def audit_log_detail(audit_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    require_ops_schema(db, "ops_audit_logs")
    item = mapped_one(
        db.execute(
            text(
                """
                SELECT a.id, a.user_id::text, u.email AS user_email,
                       a.action, a.resource_type, a.resource_id,
                       a.ip_address::text, a.user_agent,
                       a.before_data, a.after_data, a.result,
                       a.job_id::text, a.created_at
                FROM ops_audit_logs a
                LEFT JOIN users u ON u.id = a.user_id
                WHERE a.id = :audit_id
                """
            ),
            {"audit_id": audit_id},
        )
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Audit log not found")
    item["before_data"] = sanitize_for_audit(item.get("before_data")) if item.get("before_data") is not None else None
    item["after_data"] = sanitize_for_audit(item.get("after_data")) if item.get("after_data") is not None else None
    return _redact_rows([item])[0]


@router.get("/agents")
def agents(db: Session = Depends(get_db)) -> dict[str, Any]:
    if not table_exists(db, "ops_agents"):
        return {"available": False, "items": []}
    items = mapped_rows(
        db.execute(
            text(
                """
                SELECT id::text, name, hostname, environment, os_type,
                       ip_address::text, version, status, capabilities,
                       maintenance_mode, last_seen_at, created_at, updated_at
                FROM ops_agents
                WHERE environment = :environment
                ORDER BY name
                """
            ),
            {"environment": current_environment()},
        )
    )
    return {"available": True, "items": items}


@router.get("/deployments")
def deployments(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if not table_exists(db, "ops_deployments"):
        return _page([], total=0, limit=limit, offset=offset, available=False)
    if table_exists(db, "ops_container_releases"):
        container_projection = """
                       d.deployment_mode, d.deployment_action,
                       d.target_environment, d.target_name, d.target_identity,
                       d.target_runtime_kind, d.native_baseline_identity,
                       d.expected_runtime_generation,
                       d.expected_controller_state_sha256,
                       d.expected_previous_release_digest,
                       d.container_release_id::text,
                       d.container_release_digest,
                       d.previous_container_release_id::text,
                       d.previous_container_release_digest,
                       d.validation_receipt_id::text,
                       d.validation_receipt_digest,
                       d.approval_evidence_id::text,
                       d.api_image_digest, d.frontend_image_digest,
                       d.bundle_sha256, d.runtime_generation,
                       d.activated_release_digest,
                       d.runtime_previous_release_digest,
                       d.controller_state_sha256,
                       d.runtime_target_kind,
                       d.runtime_native_baseline_identity,
        """
    else:
        # Rolling-upgrade compatibility before the additive Docker evidence
        # migration is installed. Every pre-migration row is necessarily native.
        container_projection = """
                       'native'::text AS deployment_mode,
                       'deploy'::text AS deployment_action,
                       NULL::text AS target_environment,
                       NULL::text AS target_name,
                       NULL::text AS target_identity,
                       NULL::text AS target_runtime_kind,
                       NULL::text AS native_baseline_identity,
                       NULL::bigint AS expected_runtime_generation,
                       NULL::text AS expected_controller_state_sha256,
                       NULL::text AS expected_previous_release_digest,
                       NULL::text AS container_release_id,
                       NULL::text AS container_release_digest,
                       NULL::text AS previous_container_release_id,
                       NULL::text AS previous_container_release_digest,
                       NULL::text AS validation_receipt_id,
                       NULL::text AS validation_receipt_digest,
                       NULL::text AS approval_evidence_id,
                       NULL::text AS api_image_digest,
                       NULL::text AS frontend_image_digest,
                       NULL::text AS bundle_sha256,
                       NULL::bigint AS runtime_generation,
                       NULL::text AS activated_release_digest,
                       NULL::text AS runtime_previous_release_digest,
                       NULL::text AS controller_state_sha256,
                       NULL::text AS runtime_target_kind,
                       NULL::text AS runtime_native_baseline_identity,
        """
    params = {"environment": current_environment(), "limit": limit, "offset": offset}
    total = int(
        db.execute(
            text("SELECT COUNT(*) FROM ops_deployments WHERE environment = :environment"),
            params,
        ).scalar()
        or 0
    )
    items = mapped_rows(
        db.execute(
            text(
                """
                SELECT d.id::text, d.job_id::text, d.environment, d.service_type,
                       {container_projection}
                       d.previous_version, d.target_version, d.previous_commit,
                       d.target_commit, d.branch, d.deployment_status,
                       d.requested_by::text, d.started_at, d.finished_at, d.created_at,
                       j.parameters->>'target' AS target,
                       j.status AS job_status, j.progress,
                       j.error_code, j.error_message
                FROM ops_deployments d
                JOIN ops_jobs j ON j.id = d.job_id
                WHERE d.environment = :environment
                ORDER BY d.created_at DESC
                LIMIT :limit OFFSET :offset
                """.format(container_projection=container_projection)
            ),
            params,
        )
    )
    return _page(items, total=total, limit=limit, offset=offset)


@router.get("/deployments/readiness")
def deployment_readiness_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    return _deployment_readiness_payload(db)


@router.post("/deployments", status_code=status.HTTP_202_ACCEPTED)
def create_deployment(
    payload: DeploymentRequest,
    request: Request,
    user: models.User = Depends(require_ops_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    require_ops_schema(
        db,
        "ops_agents",
        "ops_jobs",
        "ops_job_logs",
        "ops_deployments",
        "ops_audit_logs",
    )
    readiness = _deployment_readiness_payload(db)
    snapshot = readiness.get("snapshot") or {}
    target_rows = {str(item.get("name")): item for item in readiness.get("targets") or [] if isinstance(item, dict)}
    target_row = target_rows.get(payload.target)
    if target_row is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="검토된 배포 대상이 아닙니다.",
        )
    if target_row.get("deploy_profile") != "full-stack":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "deployment_target_profile_forbidden",
                "message": "The selected target does not accept full-stack deployments.",
            },
        )
    if not readiness.get("can_deploy"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "deployment_not_ready",
                "message": "현재 개발 스냅샷을 배포할 수 없습니다.",
                "reasons": readiness.get("reasons") or [],
            },
        )
    if not target_row.get("key_ready"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="선택한 대상의 배포 키를 읽을 수 없습니다.",
        )
    if payload.target_commit != snapshot.get("commit"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="화면에서 검토한 commit과 현재 개발 HEAD가 다릅니다.",
        )
    if payload.source_tree != snapshot.get("source_tree"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="화면에서 검토한 개발 스냅샷과 현재 파일 내용이 다릅니다.",
        )

    target = reviewed_target(payload.target)
    environment = current_environment()
    if target.environment != environment:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "deployment_target_environment_mismatch",
                "message": "The selected deployment target belongs to a different environment.",
                "current_environment": environment,
                "target_environment": target.environment,
            },
        )
    if target.deploy_profile != "full-stack":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "deployment_target_profile_forbidden",
                "message": "The reviewed target does not accept full-stack deployments.",
            },
        )
    agent = readiness["agent"]
    parameters = {
        "action": "deploy",
        "target": target.name,
        "target_commit": payload.target_commit,
        "source_tree": payload.source_tree,
        "target_identity": target.identity,
        "service_type": "full",
        "skip_workers": payload.skip_workers,
        "required_agent_hostname": agent["hostname"],
    }
    target_key = f"deployment:{target.name}"
    job = enqueue_job(
        db,
        job_type="deployment",
        requested_by=user.id,
        parameters=parameters,
        target_key=target_key,
        max_retries=0,
    )
    environment = job["environment"]
    db.execute(
        text(
            """
            UPDATE ops_jobs
            SET agent_id = :agent_id, updated_at = CURRENT_TIMESTAMP
            WHERE id = :job_id
            """
        ),
        {"agent_id": agent["id"], "job_id": str(job["id"])},
    )
    job["agent_id"] = agent["id"]
    deployment = mapped_one(
        db.execute(
            text(
                """
                INSERT INTO ops_deployments (
                    job_id, environment, service_type, target_version,
                    target_commit, branch, deployment_status, requested_by
                )
                VALUES (
                    :job_id, :environment, 'full', :target_version,
                    :target_commit, :branch, 'queued', :requested_by
                )
                RETURNING id::text, job_id::text, environment, service_type,
                          target_version, target_commit, branch,
                          deployment_status, requested_by::text, created_at,
                          'native'::text AS deployment_mode,
                          'deploy'::text AS deployment_action
                """
            ),
            {
                "job_id": str(job["id"]),
                "environment": environment,
                "target_version": f"worktree-tree@{payload.source_tree[:12]}",
                "target_commit": payload.source_tree,
                "branch": snapshot.get("branch"),
                "requested_by": str(user.id),
            },
        )
    )
    append_audit(
        db,
        request,
        user_id=user.id,
        action="deployment.create",
        resource_type="deployment",
        resource_id=deployment["id"] if deployment else None,
        after_data={
            "target": target.name,
            "target_commit": payload.target_commit,
            "source_tree": payload.source_tree,
            "branch": snapshot.get("branch"),
            "skip_workers": payload.skip_workers,
        },
        job_id=job["id"],
    )
    db.commit()
    return {"job": job, "deployment": deployment}


@router.get("/deployments/container/readiness")
def container_deployment_readiness_status(
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _container_deployment_readiness_payload(db)


@router.get("/deployments/container/releases")
def container_releases(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if table_exists(db, "ops_container_releases") is False:
        return _page([], total=0, limit=limit, offset=offset, available=False)
    params = {"limit": limit, "offset": offset}
    total = int(db.execute(text("SELECT COUNT(*) FROM ops_container_releases")).scalar() or 0)
    items = mapped_rows(
        db.execute(
            text(
                """
                SELECT id::text, release_digest, base_commit, source_tree,
                       snapshot_commit, platform, api_image_digest,
                       frontend_image_digest, bundle_sha256, compose_sha256,
                       build_policy_sha256, migration_ledger_sha256,
                       builder_target_identity,
                       builder_hostname, built_at, created_at
                FROM ops_container_releases
                ORDER BY created_at DESC, id DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )
    )
    return _page(items, total=total, limit=limit, offset=offset)


@router.get("/deployments/container/releases/{release_id}")
def container_release_detail(
    release_id: UUID,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    require_ops_schema(db, *_CONTAINER_PIPELINE_TABLES)
    release = mapped_one(
        db.execute(
            text(
                """
                SELECT id::text, release_digest, base_commit, source_tree,
                       snapshot_commit, platform, api_image_digest,
                       frontend_image_digest, bundle_sha256, compose_sha256,
                       build_policy_sha256, migration_ledger_sha256,
                       builder_target_identity, builder_hostname,
                       manifest_json, built_by::text,
                       built_at, created_at
                FROM ops_container_releases
                WHERE id = :release_id
                """
            ),
            {"release_id": str(release_id)},
        )
    )
    if release is None:
        raise HTTPException(status_code=404, detail="Container release not found")
    receipts = mapped_rows(
        db.execute(
            text(
                """
                SELECT id::text, receipt_digest, release_id::text,
                       release_digest, source_tree, target, target_identity,
                       platform, bundle_sha256, compose_sha256,
                       api_image_digest, frontend_image_digest, checks,
                       status, validated_at, expires_at, created_at
                FROM ops_container_validation_receipts
                WHERE release_id = :release_id
                ORDER BY created_at DESC, id DESC
                """
            ),
            {"release_id": str(release_id)},
        )
    )
    deployments_for_release = mapped_rows(
        db.execute(
            text(
                """
                SELECT id::text, job_id::text, environment, deployment_mode,
                       deployment_action, target_identity,
                       target_runtime_kind, native_baseline_identity,
                       expected_runtime_generation,
                       expected_controller_state_sha256,
                       expected_previous_release_digest,
                       container_release_id::text, container_release_digest,
                       previous_container_release_id::text,
                       previous_container_release_digest,
                       validation_receipt_id::text, validation_receipt_digest,
                       approval_evidence_id::text, deployment_status,
                       runtime_target_kind, runtime_native_baseline_identity,
                       started_at, finished_at, created_at
                FROM ops_deployments
                WHERE container_release_id = :release_id
                ORDER BY created_at DESC, id DESC
                """
            ),
            {"release_id": str(release_id)},
        )
    )
    release["manifest_json"] = sanitize_for_audit(release.get("manifest_json"))
    release["validation_receipts"] = receipts
    release["deployments"] = deployments_for_release
    return release


@router.get("/deployments/container/validation-receipts")
def container_validation_receipts(
    release_id: UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if table_exists(db, "ops_container_validation_receipts") is False:
        return _page([], total=0, limit=limit, offset=offset, available=False)
    condition = "WHERE release_id = :release_id" if release_id else ""
    params: dict[str, Any] = {
        "release_id": str(release_id) if release_id else None,
        "limit": limit,
        "offset": offset,
    }
    total = int(
        db.execute(
            text(f"SELECT COUNT(*) FROM ops_container_validation_receipts {condition}"),
            params,
        ).scalar()
        or 0
    )
    items = mapped_rows(
        db.execute(
            text(
                f"""
                SELECT id::text, receipt_digest, release_id::text,
                       release_digest, source_tree, target, target_identity,
                       platform, bundle_sha256, compose_sha256,
                       api_image_digest, frontend_image_digest, checks,
                       status, validated_at, expires_at, created_at
                FROM ops_container_validation_receipts
                {condition}
                ORDER BY created_at DESC, id DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )
    )
    return _page(items, total=total, limit=limit, offset=offset)


@router.get("/deployments/container/approvals")
def container_approval_evidence(
    action: Literal["promote", "rollback", "rollback_native"] | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if table_exists(db, "ops_container_approval_evidence") is False:
        return _page([], total=0, limit=limit, offset=offset, available=False)
    conditions = ["target_environment = :environment"]
    params: dict[str, Any] = {
        "environment": current_environment(),
        "action": action,
        "limit": limit,
        "offset": offset,
    }
    if action:
        conditions.append("action = :action")
    where_sql = " AND ".join(conditions)
    total = int(
        db.execute(
            text(f"SELECT COUNT(*) FROM ops_container_approval_evidence WHERE {where_sql}"),
            params,
        ).scalar()
        or 0
    )
    items = mapped_rows(
        db.execute(
            text(
                f"""
                SELECT id::text, action, target_environment, target_identity,
                       target_name, target_runtime_kind,
                       native_baseline_identity,
                       release_id::text, release_digest,
                       current_release_id::text, current_release_digest,
                       expected_runtime_generation,
                       expected_controller_state_sha256,
                       expected_previous_release_digest,
                       validation_receipt_id::text, validation_receipt_digest,
                       typed_confirmation,
                       reason, approved_by::text, approved_at, expires_at,
                       created_at
                FROM ops_container_approval_evidence
                WHERE {where_sql}
                ORDER BY created_at DESC, id DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )
    )
    return _page(items, total=total, limit=limit, offset=offset)


@router.get("/deployments/container/timeline")
def container_deployment_timeline(
    limit: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if _container_pipeline_missing_tables(db):
        return {"available": False, "items": []}
    items = mapped_rows(
        db.execute(
            text(
                """
                SELECT d.id::text, d.job_id::text, d.environment,
                       d.deployment_action, d.target_environment,
                       d.target_name AS target, d.target_identity,
                       d.target_runtime_kind, d.native_baseline_identity,
                       d.expected_runtime_generation,
                       d.expected_controller_state_sha256,
                       d.expected_previous_release_digest,
                       d.container_release_id::text, d.container_release_digest,
                       d.previous_container_release_id::text,
                       d.previous_container_release_digest,
                       d.validation_receipt_id::text, d.validation_receipt_digest,
                       receipt.status AS validation_status,
                       receipt.receipt_digest,
                       d.approval_evidence_id::text,
                       d.api_image_digest, d.frontend_image_digest,
                       d.bundle_sha256, d.runtime_generation,
                       d.activated_release_digest,
                       d.runtime_previous_release_digest,
                       d.controller_state_sha256,
                       d.runtime_target_kind,
                       d.runtime_native_baseline_identity,
                       d.deployment_status, d.started_at, d.finished_at,
                       d.created_at
                FROM ops_deployments d
                JOIN ops_jobs j ON j.id = d.job_id
                LEFT JOIN ops_container_releases current_release
                  ON current_release.id = d.container_release_id
                LEFT JOIN ops_container_validation_receipts receipt
                  ON receipt.id = d.validation_receipt_id
                WHERE d.environment = :environment
                  AND d.deployment_mode = 'container'
                ORDER BY d.created_at DESC, d.id DESC
                LIMIT :limit
                """
            ),
            {"environment": current_environment(), "limit": limit},
        )
    )
    return {"available": True, "items": items}


def _require_exact_reviewed_container_target(
    target_name: str,
    target_identity: str,
    target_environment: str,
) -> None:
    if (
        target_name != _CONTAINER_PRODUCTION_TARGET
        or target_environment != "production"
        or current_environment() != "production"
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "container_target_outside_fixed_profile",
                "message": "Docker 배포는 production control plane의 고정 target=cloud만 허용합니다.",
            },
        )
    try:
        target = reviewed_target(target_name)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "container_target_not_reviewed",
                "message": str(exc),
            },
        ) from exc
    if target.identity != target_identity or target.environment != target_environment:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "container_target_identity_mismatch",
                "message": "검토된 대상 식별자 또는 환경이 요청과 일치하지 않습니다.",
            },
        )
    if target.deploy_profile != "full-stack":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "container_target_profile_forbidden",
                "message": "Docker 배포 대상은 full-stack profile이어야 합니다.",
            },
        )


def _raise_container_build_validate_unavailable(action: str) -> None:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail={
            "code": "container_build_validate_executor_unavailable",
            "message": "Docker build/validate 실행은 아직 Ops Console에 연결되지 않았습니다.",
            "action": action,
            "mutated": False,
        },
    )


@router.post("/deployments/container/actions/build")
def request_container_build(
    payload: ContainerBuildRequest,
    _user: models.User = Depends(require_ops_admin),
    db: Session = Depends(get_db),
) -> None:
    require_ops_schema(db, *_CONTAINER_PIPELINE_TABLES)
    _raise_container_build_validate_unavailable("build")


@router.post("/deployments/container/actions/validate")
def request_container_validation(
    payload: ContainerValidationRequest,
    _user: models.User = Depends(require_ops_admin),
    db: Session = Depends(get_db),
) -> None:
    require_ops_schema(db, *_CONTAINER_PIPELINE_TABLES)
    _raise_container_build_validate_unavailable("validate")


def _container_release_for_transition(
    db: Session,
    release_digest: str,
) -> dict[str, Any]:
    release = mapped_one(
        db.execute(
            text(
                """
                SELECT id::text, release_digest, base_commit, source_tree,
                       snapshot_commit, platform, api_image_digest,
                       frontend_image_digest, bundle_sha256, compose_sha256,
                       manifest_json
                FROM ops_container_releases
                WHERE release_digest = :release_digest
                """
            ),
            {"release_digest": release_digest},
        )
    )
    if release is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "container_release_unavailable",
                "message": "요청한 immutable container release 증적이 없습니다.",
            },
        )
    return release


def _container_current_state_for_target(
    db: Session,
    *,
    target_name: str,
    target_environment: str,
    target_identity: str,
) -> dict[str, Any] | None:
    return mapped_one(
        db.execute(
            text(
                """
                SELECT deployment.id::text,
                       deployment.runtime_target_kind,
                       deployment.runtime_native_baseline_identity,
                       CASE WHEN deployment.runtime_target_kind = 'container'
                            THEN deployment.container_release_id::text END
                           AS current_release_id,
                       CASE WHEN deployment.runtime_target_kind = 'container'
                            THEN deployment.container_release_digest END
                           AS current_release_digest,
                       CASE WHEN deployment.runtime_target_kind = 'container'
                            THEN current_release.source_tree END AS current_source_tree,
                       CASE WHEN deployment.runtime_target_kind = 'container'
                            THEN deployment.previous_container_release_id::text END
                           AS previous_release_id,
                       CASE WHEN deployment.runtime_target_kind = 'container'
                            THEN deployment.previous_container_release_digest END
                           AS previous_release_digest,
                       CASE WHEN deployment.runtime_target_kind = 'container'
                            THEN previous_release.source_tree END AS previous_source_tree,
                       deployment.runtime_generation,
                       deployment.controller_state_sha256
                FROM ops_deployments deployment
                LEFT JOIN ops_container_releases current_release
                  ON current_release.id = deployment.container_release_id
                LEFT JOIN ops_container_releases previous_release
                  ON previous_release.id = deployment.previous_container_release_id
                WHERE deployment.environment = :control_environment
                  AND deployment.target_environment = :target_environment
                  AND deployment.target_name = :target_name
                  AND deployment.target_identity = :target_identity
                  AND deployment.deployment_mode = 'container'
                  AND deployment.deployment_status = 'success'
                ORDER BY COALESCE(deployment.finished_at, deployment.created_at) DESC,
                         deployment.id DESC
                LIMIT 1
                """
            ),
            {
                "control_environment": current_environment(),
                "target_environment": target_environment,
                "target_name": target_name,
                "target_identity": target_identity,
            },
        )
    )


def _live_container_runtime_cas(
    current_state: dict[str, Any] | None,
    *,
    expected_runtime_generation: int | None = None,
    expected_controller_state_sha256: str | None = None,
) -> dict[str, Any]:
    """Bind approval to one fixed status observation and reject DB/runtime drift."""

    if not container_transport_service_boundary_ready(profile="status"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "container_status_service_boundary_unavailable",
                "message": "Ops API 전용 status-only SSH 경계가 준비되지 않았습니다.",
            },
        )
    live_status = read_container_controller_status(timeout_seconds=10)
    if live_status is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "container_controller_status_unavailable",
                "message": "운영 controller 상태를 고정 명령으로 확인할 수 없습니다.",
            },
        )
    try:
        runtime_cas = container_runtime_cas(live_status)
    except ContainerDeploymentError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "container_controller_state_not_approvable",
                "message": str(exc),
            },
        ) from exc

    db_runtime_kind = None if current_state is None else current_state.get("runtime_target_kind")
    db_active = None if current_state is None else current_state.get("current_release_digest")
    db_previous = None if current_state is None else current_state.get("previous_release_digest")
    db_generation = None if current_state is None else current_state.get("runtime_generation")
    db_state_hash = None if current_state is None else current_state.get("controller_state_sha256")
    if current_state is None or db_runtime_kind == "native":
        db_matches = (
            runtime_cas["expected_runtime_generation"] == 0
            and runtime_cas["expected_active_release_digest"] is None
            and runtime_cas["expected_previous_release_digest"] is None
            and (
                current_state is None
                or runtime_cas["expected_controller_state_sha256"] == db_state_hash
            )
        )
    elif db_runtime_kind == "container":
        db_matches = bool(
            runtime_cas["expected_runtime_generation"] == db_generation
            and runtime_cas["expected_controller_state_sha256"] == db_state_hash
            and runtime_cas["expected_active_release_digest"] == db_active
            and runtime_cas["expected_previous_release_digest"] == db_previous
            and runtime_cas["native_baseline_identity"]
            == current_state.get("runtime_native_baseline_identity")
        )
    else:
        db_matches = False
    if not db_matches:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "container_controller_database_state_drift",
                "message": "운영 controller current/previous 상태가 Ops 증적과 일치하지 않습니다.",
            },
        )
    if (
        expected_runtime_generation is not None
        and expected_runtime_generation != runtime_cas["expected_runtime_generation"]
    ) or (
        expected_controller_state_sha256 is not None
        and expected_controller_state_sha256 != runtime_cas["expected_controller_state_sha256"]
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "container_approval_cas_mismatch",
                "message": "검토한 generation/state hash가 현재 운영 상태와 달라졌습니다.",
            },
        )
    return runtime_cas


def _enqueue_container_transition(
    db: Session,
    request: Request,
    user: models.User,
    *,
    action: Literal["promote", "rollback", "rollback_native"],
    target_name: str,
    target_environment: str,
    target_identity: str,
    release: dict[str, Any] | None,
    current_state: dict[str, Any] | None,
    confirmation: str,
    reason: str,
    receipt: dict[str, Any] | None,
    runtime_cas: dict[str, Any],
    target_runtime_kind: Literal["container", "native"] = "container",
    native_baseline_identity: str | None = None,
) -> dict[str, Any]:
    agent = _container_deployment_agent(db)
    if agent is None or str(agent.get("hostname") or "").strip().lower() != _CONTAINER_EXECUTOR_HOSTNAME:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "container_deployment_agent_offline",
                "message": "an2p Docker deployment worker가 연결되어 있지 않습니다.",
            },
        )

    current_release_id = (
        str(current_state["current_release_id"])
        if current_state and current_state.get("current_release_id")
        else None
    )
    current_release_digest = (
        str(current_state["current_release_digest"])
        if current_state and current_state.get("current_release_digest")
        else None
    )
    release_id = str(release["id"]) if release else None
    release_digest = str(release["release_digest"]) if release else None
    expected_runtime_generation = int(runtime_cas["expected_runtime_generation"])
    expected_controller_state_sha256 = str(runtime_cas["expected_controller_state_sha256"])
    expected_previous_release_digest = runtime_cas["expected_previous_release_digest"]
    approval = mapped_one(
        db.execute(
            text(
                """
                INSERT INTO ops_container_approval_evidence (
                    action, target_environment, target_identity, target_name,
                    target_runtime_kind, native_baseline_identity,
                    release_id, release_digest,
                    current_release_id, current_release_digest,
                    expected_runtime_generation,
                    expected_controller_state_sha256,
                    expected_previous_release_digest,
                    validation_receipt_id, validation_receipt_digest,
                    typed_confirmation, reason, approved_by,
                    approved_at, expires_at
                )
                VALUES (
                    :action, :target_environment, :target_identity, :target_name,
                    :target_runtime_kind, :native_baseline_identity,
                    :release_id, :release_digest,
                    :current_release_id, :current_release_digest,
                    :expected_runtime_generation,
                    :expected_controller_state_sha256,
                    :expected_previous_release_digest,
                    :validation_receipt_id, :validation_receipt_digest,
                    :typed_confirmation, :reason, :approved_by,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP + INTERVAL '10 minutes'
                )
                RETURNING id::text, action, target_environment, target_name,
                          target_identity, target_runtime_kind,
                          native_baseline_identity,
                          release_id::text, release_digest,
                          current_release_id::text, current_release_digest,
                          expected_runtime_generation,
                          expected_controller_state_sha256,
                          expected_previous_release_digest,
                          validation_receipt_id::text,
                          validation_receipt_digest, approved_at, expires_at
                """
            ),
            {
                "action": action,
                "target_environment": target_environment,
                "target_identity": target_identity,
                "target_name": target_name,
                "target_runtime_kind": target_runtime_kind,
                "native_baseline_identity": native_baseline_identity,
                "release_id": release_id,
                "release_digest": release_digest,
                "current_release_id": current_release_id,
                "current_release_digest": current_release_digest,
                "expected_runtime_generation": expected_runtime_generation,
                "expected_controller_state_sha256": expected_controller_state_sha256,
                "expected_previous_release_digest": expected_previous_release_digest,
                "validation_receipt_id": str(receipt["id"]) if receipt else None,
                "validation_receipt_digest": (str(receipt["receipt_digest"]) if receipt else None),
                "typed_confirmation": confirmation,
                "reason": reason,
                "approved_by": str(user.id),
            },
        )
    )
    if approval is None:
        raise RuntimeError("Container approval insert did not return a row")

    parameters = {
        "action": action,
        "approval_evidence_id": approval["id"],
        "current_release_digest": current_release_digest,
        "deployment_mode": "container",
        "expected_controller_state_sha256": expected_controller_state_sha256,
        "expected_previous_release_digest": expected_previous_release_digest,
        "expected_runtime_generation": expected_runtime_generation,
        "native_baseline_identity": native_baseline_identity,
        "release_digest": release_digest,
        "required_agent_hostname": _CONTAINER_EXECUTOR_HOSTNAME,
        "service_type": "full",
        "source_tree": release["source_tree"] if release else None,
        "target": target_name,
        "target_environment": target_environment,
        "target_identity": target_identity,
        "target_runtime_kind": target_runtime_kind,
        "validation_receipt_digest": (receipt["receipt_digest"] if receipt else None),
    }
    job = enqueue_job(
        db,
        job_type="deployment",
        requested_by=user.id,
        parameters=parameters,
        target_key=f"deployment:{target_name}",
        max_retries=0,
    )
    db.execute(
        text(
            """
            UPDATE ops_jobs
            SET agent_id = :agent_id, updated_at = CURRENT_TIMESTAMP
            WHERE id = :job_id
            """
        ),
        {"agent_id": agent["id"], "job_id": str(job["id"])},
    )
    job["agent_id"] = agent["id"]

    deployment = mapped_one(
        db.execute(
            text(
                """
                INSERT INTO ops_deployments (
                    job_id, environment, service_type,
                    previous_version, target_version,
                    previous_commit, target_commit, branch,
                    deployment_status, requested_by,
                    deployment_mode, deployment_action,
                    target_environment, target_name, target_identity,
                    target_runtime_kind, native_baseline_identity,
                    expected_runtime_generation,
                    expected_controller_state_sha256,
                    expected_previous_release_digest,
                    container_release_id, container_release_digest,
                    previous_container_release_id,
                    previous_container_release_digest,
                    validation_receipt_id, validation_receipt_digest,
                    approval_evidence_id,
                    api_image_digest, frontend_image_digest, bundle_sha256
                )
                VALUES (
                    :job_id, :environment, 'full',
                    :previous_version, :target_version,
                    :previous_commit, :target_commit, 'container-release',
                    'queued', :requested_by,
                    'container', :deployment_action,
                    :target_environment, :target_name, :target_identity,
                    :target_runtime_kind, :native_baseline_identity,
                    :expected_runtime_generation,
                    :expected_controller_state_sha256,
                    :expected_previous_release_digest,
                    :container_release_id, :container_release_digest,
                    :previous_container_release_id,
                    :previous_container_release_digest,
                    :validation_receipt_id, :validation_receipt_digest,
                    :approval_evidence_id,
                    :api_image_digest, :frontend_image_digest, :bundle_sha256
                )
                RETURNING id::text, job_id::text, environment, service_type,
                          deployment_mode, deployment_action,
                          target_environment, target_name, target_identity,
                          target_runtime_kind, native_baseline_identity,
                          expected_runtime_generation,
                          expected_controller_state_sha256,
                          expected_previous_release_digest,
                          container_release_id::text, container_release_digest,
                          previous_container_release_id::text,
                          previous_container_release_digest,
                          validation_receipt_id::text,
                          validation_receipt_digest,
                          approval_evidence_id::text,
                          deployment_status, requested_by::text, created_at
                """
            ),
            {
                "job_id": str(job["id"]),
                "environment": current_environment(),
                "previous_version": (
                    f"container@{current_release_digest[:12]}"
                    if current_release_digest
                    else (
                        f"native@{str(current_state.get('runtime_native_baseline_identity'))[:12]}"
                        if current_state and current_state.get("runtime_target_kind") == "native"
                        else None
                    )
                ),
                "target_version": (
                    f"container@{release_digest[:12]}"
                    if release_digest
                    else f"native@{str(native_baseline_identity)[:12]}"
                ),
                "previous_commit": (current_state.get("current_source_tree") if current_state else None),
                "target_commit": release["snapshot_commit"] if release else native_baseline_identity,
                "requested_by": str(user.id),
                "deployment_action": action,
                "target_environment": target_environment,
                "target_name": target_name,
                "target_identity": target_identity,
                "target_runtime_kind": target_runtime_kind,
                "native_baseline_identity": native_baseline_identity,
                "expected_runtime_generation": expected_runtime_generation,
                "expected_controller_state_sha256": expected_controller_state_sha256,
                "expected_previous_release_digest": expected_previous_release_digest,
                "container_release_id": release_id,
                "container_release_digest": release_digest,
                "previous_container_release_id": current_release_id,
                "previous_container_release_digest": current_release_digest,
                "validation_receipt_id": str(receipt["id"]) if receipt else None,
                "validation_receipt_digest": (receipt["receipt_digest"] if receipt else None),
                "approval_evidence_id": approval["id"],
                "api_image_digest": release["api_image_digest"] if release else None,
                "frontend_image_digest": release["frontend_image_digest"] if release else None,
                "bundle_sha256": release["bundle_sha256"] if release else None,
            },
        )
    )
    if deployment is None:
        raise RuntimeError("Container deployment insert did not return a row")
    append_audit(
        db,
        request,
        user_id=user.id,
        action=f"container_deployment.{action}",
        resource_type="container_deployment",
        resource_id=deployment["id"],
        after_data={
            "target": target_name,
            "target_environment": target_environment,
            "target_identity": target_identity,
            "target_runtime_kind": target_runtime_kind,
            "native_baseline_identity": native_baseline_identity,
            "release_digest": release_digest,
            "current_release_digest": current_release_digest,
            "validation_receipt_digest": (receipt["receipt_digest"] if receipt else None),
            "approval_evidence_id": approval["id"],
            "expected_runtime_generation": expected_runtime_generation,
            "expected_controller_state_sha256": expected_controller_state_sha256,
            "expected_previous_release_digest": expected_previous_release_digest,
            "approval_expires_at": approval["expires_at"],
            "reason": reason,
        },
        job_id=job["id"],
    )
    db.commit()
    return {"job": job, "deployment": deployment, "approval": approval}


@router.post(
    "/deployments/container/actions/promote",
    status_code=status.HTTP_202_ACCEPTED,
)
def request_container_promotion(
    payload: ContainerPromotionRequest,
    request: Request,
    user: models.User = Depends(require_ops_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    require_ops_schema(db, *_CONTAINER_PIPELINE_TABLES)
    _require_exact_reviewed_container_target(
        payload.target,
        payload.target_identity,
        payload.target_environment,
    )
    development_target_identity = _configured_container_development_identity()
    if development_target_identity is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "container_development_identity_unconfigured",
                "message": "an2p-dev canonical target identity가 고정되지 않았습니다.",
            },
        )
    release = _container_release_for_transition(db, payload.release_digest)
    receipt = mapped_one(
        db.execute(
            text(
                """
                SELECT id::text, receipt_digest, release_id::text,
                       release_digest, source_tree, target, target_identity,
                       status, expires_at, receipt_json
                FROM ops_container_validation_receipts
                WHERE receipt_digest = :receipt_digest
                  AND release_digest = :release_digest
                  AND target = 'an2p-dev'
                  AND target_identity = :development_target_identity
                  AND status = 'passed'
                  AND expires_at > CURRENT_TIMESTAMP
                """
            ),
            {
                "receipt_digest": payload.validation_receipt_digest,
                "release_digest": payload.release_digest,
                "development_target_identity": development_target_identity,
            },
        )
    )
    if receipt is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "container_validation_receipt_not_pass",
                "message": "선택한 릴리스의 유효한 PASS 검증 영수증이 아닙니다.",
            },
        )
    if str(receipt.get("release_id")) != str(release["id"]):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "container_validation_release_mismatch",
                "message": "PASS 영수증의 immutable release identity가 일치하지 않습니다.",
            },
        )
    current_state = _container_current_state_for_target(
        db,
        target_name=payload.target,
        target_environment=payload.target_environment,
        target_identity=payload.target_identity,
    )
    runtime_cas = _live_container_runtime_cas(
        current_state,
        expected_runtime_generation=payload.expected_runtime_generation,
        expected_controller_state_sha256=payload.expected_controller_state_sha256,
    )
    if current_state and current_state.get("current_release_digest") == payload.release_digest:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "container_release_already_active",
                "message": "요청한 container release가 이미 운영에서 활성 상태입니다.",
            },
        )
    return _enqueue_container_transition(
        db,
        request,
        user,
        action="promote",
        target_name=payload.target,
        target_environment=payload.target_environment,
        target_identity=payload.target_identity,
        release=release,
        current_state=current_state,
        confirmation=payload.confirmation,
        reason=payload.reason,
        receipt=receipt,
        runtime_cas=runtime_cas,
    )


@router.post(
    "/deployments/container/actions/rollback",
    status_code=status.HTTP_202_ACCEPTED,
)
def request_container_rollback(
    payload: ContainerRollbackRequest,
    request: Request,
    user: models.User = Depends(require_ops_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    require_ops_schema(db, *_CONTAINER_PIPELINE_TABLES)
    _require_exact_reviewed_container_target(
        payload.target,
        payload.target_identity,
        payload.target_environment,
    )
    current = _container_current_state_for_target(
        db,
        target_name=payload.target,
        target_environment=payload.target_environment,
        target_identity=payload.target_identity,
    )
    runtime_cas = _live_container_runtime_cas(
        current,
        expected_runtime_generation=payload.expected_runtime_generation,
        expected_controller_state_sha256=payload.expected_controller_state_sha256,
    )
    if current is None or current.get("current_release_digest") != payload.current_release_digest:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "container_rollback_state_mismatch",
                "message": "현재/이전 릴리스 포인터가 검토한 롤백 전이와 일치하지 않습니다.",
            },
        )
    if current.get("previous_release_digest") is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "container_rollback_previous_release_unavailable",
                "message": "container 간 rollback 대상이 없습니다. native maintenance 작업을 사용하세요.",
            },
        )
    if current.get("previous_release_digest") != payload.rollback_release_digest:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "container_rollback_state_mismatch",
                "message": "현재/이전 릴리스 포인터가 검토한 롤백 전이와 일치하지 않습니다.",
            },
        )
    rollback_release = _container_release_for_transition(
        db,
        payload.rollback_release_digest,
    )
    if str(rollback_release["id"]) != str(current.get("previous_release_id")):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "container_rollback_release_identity_mismatch",
                "message": "previous pointer가 요청한 immutable release와 일치하지 않습니다.",
            },
        )
    return _enqueue_container_transition(
        db,
        request,
        user,
        action="rollback",
        target_name=payload.target,
        target_environment=payload.target_environment,
        target_identity=payload.target_identity,
        release=rollback_release,
        current_state=current,
        confirmation=payload.confirmation,
        reason=payload.reason,
        receipt=None,
        runtime_cas=runtime_cas,
    )


@router.post(
    "/deployments/container/actions/rollback-native",
    status_code=status.HTTP_202_ACCEPTED,
)
def request_container_native_rollback(
    payload: ContainerNativeRollbackRequest,
    request: Request,
    user: models.User = Depends(require_ops_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Queue one CAS-bound Docker-to-native maintenance transition."""

    require_ops_schema(db, *_CONTAINER_PIPELINE_TABLES)
    _require_exact_reviewed_container_target(
        payload.target,
        payload.target_identity,
        payload.target_environment,
    )
    current = _container_current_state_for_target(
        db,
        target_name=payload.target,
        target_environment=payload.target_environment,
        target_identity=payload.target_identity,
    )
    runtime_cas = _live_container_runtime_cas(
        current,
        expected_runtime_generation=payload.expected_runtime_generation,
        expected_controller_state_sha256=payload.expected_controller_state_sha256,
    )
    if (
        current is None
        or current.get("runtime_target_kind") != "container"
        or current.get("current_release_digest") != payload.current_release_digest
        or runtime_cas.get("expected_active_release_digest") != payload.current_release_digest
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "container_native_rollback_state_mismatch",
                "message": "현재 Docker release가 검토한 native maintenance 전이와 일치하지 않습니다.",
            },
        )
    if runtime_cas.get("native_baseline_identity") != payload.native_baseline_identity:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "container_native_baseline_identity_mismatch",
                "message": "운영 controller의 고정 native baseline identity가 요청과 다릅니다.",
            },
        )
    return _enqueue_container_transition(
        db,
        request,
        user,
        action="rollback_native",
        target_name=payload.target,
        target_environment=payload.target_environment,
        target_identity=payload.target_identity,
        release=None,
        current_state=current,
        confirmation=payload.confirmation,
        reason=payload.reason,
        receipt=None,
        runtime_cas=runtime_cas,
        target_runtime_kind="native",
        native_baseline_identity=payload.native_baseline_identity,
    )


@router.get("/deployments/{deployment_id}")
def deployment_detail(
    deployment_id: UUID,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    require_ops_schema(db, "ops_deployments", "ops_jobs")
    if table_exists(db, "ops_container_releases"):
        container_projection = """
                       d.deployment_mode, d.deployment_action,
                       d.target_environment, d.target_name, d.target_identity,
                       d.target_runtime_kind, d.native_baseline_identity,
                       d.expected_runtime_generation,
                       d.expected_controller_state_sha256,
                       d.expected_previous_release_digest,
                       d.container_release_id::text,
                       d.container_release_digest,
                       d.previous_container_release_id::text,
                       d.previous_container_release_digest,
                       d.validation_receipt_id::text,
                       d.validation_receipt_digest,
                       d.approval_evidence_id::text,
                       d.api_image_digest, d.frontend_image_digest,
                       d.bundle_sha256, d.runtime_generation,
                       d.activated_release_digest,
                       d.runtime_previous_release_digest,
                       d.controller_state_sha256,
                       d.runtime_target_kind,
                       d.runtime_native_baseline_identity,
        """
    else:
        container_projection = """
                       'native'::text AS deployment_mode,
                       'deploy'::text AS deployment_action,
                       NULL::text AS target_environment,
                       NULL::text AS target_name,
                       NULL::text AS target_identity,
                       NULL::text AS target_runtime_kind,
                       NULL::text AS native_baseline_identity,
                       NULL::bigint AS expected_runtime_generation,
                       NULL::text AS expected_controller_state_sha256,
                       NULL::text AS expected_previous_release_digest,
                       NULL::text AS container_release_id,
                       NULL::text AS container_release_digest,
                       NULL::text AS previous_container_release_id,
                       NULL::text AS previous_container_release_digest,
                       NULL::text AS validation_receipt_id,
                       NULL::text AS validation_receipt_digest,
                       NULL::text AS approval_evidence_id,
                       NULL::text AS api_image_digest,
                       NULL::text AS frontend_image_digest,
                       NULL::text AS bundle_sha256,
                       NULL::bigint AS runtime_generation,
                       NULL::text AS activated_release_digest,
                       NULL::text AS runtime_previous_release_digest,
                       NULL::text AS controller_state_sha256,
                       NULL::text AS runtime_target_kind,
                       NULL::text AS runtime_native_baseline_identity,
        """
    item = mapped_one(
        db.execute(
            text(
                """
                SELECT d.id::text, d.job_id::text, d.environment, d.service_type,
                       {container_projection}
                       d.previous_version, d.target_version, d.previous_commit,
                       d.target_commit, d.branch, d.deployment_status,
                       d.health_check_result, d.smoke_test_result,
                       d.requested_by::text, d.started_at, d.finished_at, d.created_at,
                       j.parameters->>'target' AS target,
                       j.status AS job_status, j.progress, j.result,
                       j.error_code, j.error_message, j.cancel_requested_at,
                       j.heartbeat_at
                FROM ops_deployments d
                JOIN ops_jobs j ON j.id = d.job_id
                WHERE d.id = :deployment_id
                  AND d.environment = :environment
                """.format(container_projection=container_projection)
            ),
            {
                "deployment_id": str(deployment_id),
                "environment": current_environment(),
            },
        )
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Deployment not found")
    item["result"] = sanitize_for_audit(item.get("result")) if item.get("result") else None
    return _redact_rows([item])[0]


@router.get("/content")
def content_items(
    content_type: str = Query(default="", max_length=40),
    category: str = Query(default="", max_length=100),
    provider: str = Query(default="", max_length=100),
    query: str = Query(default="", max_length=100),
    state: Literal["active", "inactive", "all"] = Query(default="active"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    conditions = [f"{CONTENT_TYPE_SQL} <> 'unknown'"]
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if content_type:
        conditions.append(f"{CONTENT_TYPE_SQL} = :content_type")
        params["content_type"] = content_type
    if category:
        conditions.append(f"{CONTENT_CATEGORY_SQL} = :category")
        params["category"] = category
    if provider:
        conditions.append("c.provider = :provider")
        params["provider"] = provider
    if query:
        conditions.append(
            f"""
            (
                c.title ILIKE :query
                OR c.provider ILIKE :query
                OR COALESCE(b.name, '') ILIKE :query
                OR {CONTENT_CATEGORY_SQL} ILIKE :query
            )
            """
        )
        params["query"] = f"%{query}%"
    if state == "active":
        conditions.append("c.is_active = true")
    elif state == "inactive":
        conditions.append("c.is_active = false")
    where_sql = " AND ".join(f"({condition})" for condition in conditions)
    total = int(
        db.execute(
            text(
                f"""
                SELECT COUNT(*)
                FROM courses c
                LEFT JOIN branches b ON b.id = c.branch_id
                WHERE {where_sql}
                """
            ),
            params,
        ).scalar()
        or 0
    )
    has_overrides = table_exists(db, "ops_content_overrides")
    override_select = (
        """
        (
            SELECT COUNT(*)
            FROM ops_content_overrides o
            WHERE o.resource_type = 'course' AND o.resource_id = c.id::text
        ) AS override_count
        """
        if has_overrides
        else "0::bigint AS override_count"
    )
    items = mapped_rows(
        db.execute(
            text(
                f"""
                SELECT c.id::text, c.provider, c.provider_course_id,
                       {CONTENT_TYPE_SQL} AS content_type,
                       {CONTENT_CATEGORY_SQL} AS category,
                       {CATEGORY_ENCODING_ISSUE_SQL} AS category_encoding_issue,
                       c.title, b.name AS branch, c.status, c.is_active,
                       c.schedule_raw, c.start_date, c.end_date,
                       c.apply_start, c.apply_end, c.fee,
                       c.application_url, c.raw_url,
                       q.total_score AS quality_score, q.grade AS quality_grade,
                       c.last_seen_at, c.updated_at,
                       {override_select}
                FROM courses c
                LEFT JOIN branches b ON b.id = c.branch_id
                LEFT JOIN course_quality_score q ON q.course_id = c.id
                WHERE {where_sql}
                ORDER BY c.last_seen_at DESC NULLS LAST, c.updated_at DESC, c.id
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )
    )
    return _page(_redact_rows(items), total=total, limit=limit, offset=offset)


@router.get("/content/{course_id}")
def content_detail(course_id: UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    item = mapped_one(
        db.execute(
            text(
                f"""
                SELECT c.id::text, c.provider, c.provider_course_id,
                       {CONTENT_TYPE_SQL} AS content_type,
                       c.title, c.title_raw, c.instructor, c.target,
                       c.category_raw, c.collection_category, c.domain_category,
                       c.standard_category_key, c.standard_category_label,
                       c.service_group, c.collection_type, c.fee, c.material_fee,
                       c.schedule_raw, c.schedule_days, c.schedule_dates,
                       c.start_date, c.end_date, c.apply_start, c.apply_end,
                       c.apply_period_raw, c.capacity_total, c.capacity_current,
                       c.capacity_remaining, c.venue_name, c.venue_address,
                       c.application_url, c.application_type,
                       c.application_method_raw, c.reservation_available,
                       c.status, c.raw_url, c.description, c.image_url,
                       c.is_active, c.first_seen_at, c.last_seen_at, c.removed_at,
                       c.change_detected_at, c.ai_category, c.ai_tags, c.ai_summary,
                       c.target_age_group, c.target_min_age, c.target_max_age,
                       c.target_with_parent, c.target_tags,
                       c.target_age_is_explicit, c.raw_fields,
                       b.id::text AS branch_id, b.name AS branch,
                       b.address AS branch_address, b.lat, b.lon,
                       q.total_score AS quality_score, q.grade AS quality_grade,
                       q.missing_fields, q.checked_at AS quality_checked_at,
                       c.created_at, c.updated_at
                FROM courses c
                LEFT JOIN branches b ON b.id = c.branch_id
                LEFT JOIN course_quality_score q ON q.course_id = c.id
                WHERE c.id = :course_id
                """
            ),
            {"course_id": str(course_id)},
        )
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Content item not found")
    item["raw_fields"] = sanitize_for_audit(item.get("raw_fields"))
    if table_exists(db, "ops_content_overrides"):
        item["overrides"] = mapped_rows(
            db.execute(
                text(
                    """
                    SELECT id::text, field_name, source_value, normalized_value,
                           manual_value, is_locked, updated_by::text,
                           created_at, updated_at
                    FROM ops_content_overrides
                    WHERE resource_type = 'course' AND resource_id = :resource_id
                    ORDER BY field_name
                    """
                ),
                {"resource_id": str(course_id)},
            )
        )
    else:
        item["overrides"] = []
    return _redact_rows([_sanitize_category_metadata(item)])[0]


@router.get("/settings")
def settings_status(
    user: models.User = Depends(require_ops_viewer),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    table_names = (
        "ops_agents",
        "ops_services",
        "ops_jobs",
        "ops_job_logs",
        "ops_audit_logs",
        "ops_crawler_runs",
        "ops_quality_issues",
        "ops_deployments",
    )
    schema = {name: table_exists(db, name) for name in table_names}
    service_counts: dict[str, int] = {}
    if schema["ops_services"]:
        service_counts = {
            str(row["status"]): int(row["count"])
            for row in mapped_rows(
                db.execute(
                    text(
                        """
                        SELECT status, COUNT(*) AS count
                        FROM ops_services
                        WHERE environment = :environment
                        GROUP BY status
                        """
                    ),
                    {"environment": current_environment()},
                )
            )
        }
    agent_counts = {"total": 0, "connected": 0}
    if schema["ops_agents"]:
        row = mapped_one(
            db.execute(
                text(
                    """
                    SELECT COUNT(*) AS total,
                           COUNT(*) FILTER (
                               WHERE status = 'healthy'
                                 AND last_seen_at >= NOW() - INTERVAL '2 minutes'
                           ) AS connected
                    FROM ops_agents
                    WHERE environment = :environment
                      AND status <> 'disabled'
                    """
                ),
                {"environment": current_environment()},
            )
        )
        if row:
            agent_counts = {
                "total": int(row["total"] or 0),
                "connected": int(row["connected"] or 0),
            }
    latest_migration = None
    if table_exists(db, "mooncen_schema_migrations"):
        latest_migration = mapped_one(
            db.execute(
                text(
                    """
                    SELECT version, applied_at
                    FROM mooncen_schema_migrations
                    ORDER BY version DESC
                    LIMIT 1
                    """
                )
            )
        )
    return {
        "environment": current_environment(),
        "auth": {
            "mode": "single_account"
            if os.getenv("MOONCEN_OPS_SINGLE_ACCOUNT_ONLY", "").strip().lower() in {"1", "true", "yes", "on"}
            else "allowlist",
            "role": ops_role_for_user(user),
            "user": user.name,
        },
        "database": {
            "connected": _database_status(db)[0] == "healthy",
            "schema": schema,
            "latest_migration": latest_migration,
        },
        "agents": agent_counts,
        "services": service_counts,
        "refresh_seconds": {
            "dashboard": 30,
            "jobs": 15,
            "quality": 60,
        },
    }
