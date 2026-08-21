from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.crawler_control_database import get_crawler_control_db
from backend.ops.service import current_environment
from backend.routers.auth import rate_limit, require_ops_viewer
from backend.services.crawler_analytics import build_crawler_analytics, build_crawler_batch_detail


router = APIRouter(
    prefix="/api/ops/crawlers",
    tags=["ops-crawler-analytics"],
    dependencies=[
        Depends(rate_limit("ops-crawler-analytics", 60, 60)),
        Depends(require_ops_viewer),
    ],
)


@router.get("/analytics")
def crawler_analytics(
    environment: Literal["production", "staging", "development"] | None = Query(default=None),
    window_hours: int = Query(default=24, ge=1, le=720),
    provider_limit: int = Query(default=50, ge=1, le=200),
    worker_limit: int = Query(default=100, ge=1, le=500),
    correlation_limit: int = Query(default=25, ge=1, le=100),
    heartbeat_timeout_seconds: int = Query(default=360, ge=30, le=3_600),
    db: Session | None = Depends(get_crawler_control_db),
) -> dict[str, Any]:
    api_environment = current_environment()
    if environment is not None and environment != api_environment:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "crawler_analytics_environment_mismatch",
                "message": "Analytics environment differs from this Ops API environment",
            },
        )
    return build_crawler_analytics(
        db,
        environment=api_environment,
        window_hours=window_hours,
        provider_limit=provider_limit,
        worker_limit=worker_limit,
        correlation_limit=correlation_limit,
        heartbeat_timeout_seconds=heartbeat_timeout_seconds,
    )


@router.get("/analytics/batches/{batch_id}")
def crawler_analytics_batch_detail(
    batch_id: UUID,
    environment: Literal["production", "staging", "development"] | None = Query(default=None),
    task_limit: int = Query(default=100, ge=1, le=200),
    task_offset: int = Query(default=0, ge=0, le=100_000),
    db: Session | None = Depends(get_crawler_control_db),
) -> dict[str, Any]:
    api_environment = current_environment()
    if environment is not None and environment != api_environment:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "crawler_analytics_environment_mismatch",
                "message": "Analytics environment differs from this Ops API environment",
            },
        )
    result = build_crawler_batch_detail(
        db,
        environment=api_environment,
        batch_id=str(batch_id),
        task_limit=task_limit,
        task_offset=task_offset,
    )
    if result.get("available") is True and result.get("item") is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Crawler batch not found")
    return result
