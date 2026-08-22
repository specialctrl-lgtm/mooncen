from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from backend.routers.auth import rate_limit, require_ops_viewer
from backend.services.visitor_analytics import get_visitor_summary


router = APIRouter(
    prefix="/api/ops/dashboard",
    tags=["ops-visitor-analytics"],
    dependencies=[
        Depends(rate_limit("ops-visitor-summary", 60, 60)),
        Depends(require_ops_viewer),
    ],
)


@router.get("/visitor-summary")
def visitor_summary(days: int = Query(default=7, ge=1, le=30)) -> dict[str, Any]:
    return get_visitor_summary(days)
