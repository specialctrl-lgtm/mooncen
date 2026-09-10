from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.ops.service import (
    current_environment,
    mapped_one,
    mapped_rows,
    require_ops_schema,
    sanitize_for_audit,
    table_exists,
)
from backend.routers.auth import rate_limit, require_ops_viewer
from ops_agent.deployment_registry import deployment_readiness
from tools.ops_redaction import redact_text


router = APIRouter(
    prefix="/api/ops/deployments",
    tags=["ops-deployments"],
    dependencies=[
        Depends(rate_limit("ops-api-v2", 240, 60)),
        Depends(require_ops_viewer),
    ],
)


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


def _redact_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for row in rows:
        for key in ("error_message", "message"):
            if row.get(key):
                row[key] = redact_text(str(row[key]))
    return rows


def _readiness_payload() -> dict[str, Any]:
    readiness = deployment_readiness()
    reasons = list(readiness.get("reasons") or [])
    reasons.append(
        {
            "code": "native_deployment_operator_only",
            "message": (
                "네이티브 배포는 Ops Console에서 실행하지 않습니다. "
                "검토된 운영자 배포 경로를 사용하십시오."
            ),
        }
    )
    readiness.update(
        {
            "agent": None,
            "can_deploy": False,
            "reasons": reasons,
            "deployment_mode": "native",
            "display_name": "네이티브 배포",
            "execution_supported": False,
            "operator_path": "external-reviewed-operator",
        }
    )
    return readiness


@router.get("")
def deployments(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if not table_exists(db, "ops_deployments"):
        return _page([], total=0, limit=limit, offset=offset, available=False)
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
                """
            ),
            params,
        )
    )
    return _page(items, total=total, limit=limit, offset=offset)


@router.get("/readiness")
def deployment_readiness_status() -> dict[str, Any]:
    return _readiness_payload()


@router.get("/{deployment_id}")
def deployment_detail(
    deployment_id: UUID,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    require_ops_schema(db, "ops_deployments", "ops_jobs")
    item = mapped_one(
        db.execute(
            text(
                """
                SELECT d.id::text, d.job_id::text, d.environment, d.service_type,
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
                """
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
