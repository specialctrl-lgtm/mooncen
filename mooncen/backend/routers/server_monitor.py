from __future__ import annotations

import hmac
import os
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.routers.auth import rate_limit
from backend.routers.ops_v2 import quality_summary


_TOKEN_ENV_NAME = "MOONCEN_SERVER_MONITOR_TOKEN"
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,256}$")
_COUNT_KEYS = (
    "active_courses",
    "missing_required",
    "invalid_dates",
    "invalid_prices",
    "missing_address",
    "missing_coordinates",
    "incomplete_location",
    "out_of_korea",
    "duplicate_urls",
    "blocked_sync",
)
_MAX_ISSUE_STATUSES = 100


def require_server_monitor_token(request: Request) -> None:
    """Authorize the narrow server-to-server monitor endpoint without revealing it."""

    expected = os.getenv(_TOKEN_ENV_NAME, "")
    supplied = request.headers.get("X-MoonCen-Monitor-Token", "")
    matches = hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))
    if not (
        _TOKEN_PATTERN.fullmatch(expected)
        and _TOKEN_PATTERN.fullmatch(supplied)
        and matches
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


router = APIRouter(
    prefix="/api/monitoring",
    tags=["server-monitoring"],
    dependencies=[
        Depends(require_server_monitor_token),
        Depends(rate_limit("server-monitor-crawler-quality", 60, 60)),
    ],
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _optional_count(value: Any) -> int | None:
    return None if value is None else int(value)


@router.get("/crawler-quality")
def crawler_quality(db: Session = Depends(get_db)) -> dict[str, Any]:
    production = quality_summary(db)
    source_counts = production.get("counts")
    if not isinstance(source_counts, dict):
        source_counts = {}

    source_issue_statuses = production.get("issue_statuses")
    if not isinstance(source_issue_statuses, list):
        source_issue_statuses = []
    issue_statuses = [
        {
            "status": row.get("status"),
            "severity": row.get("severity"),
            "issue_count": _optional_count(row.get("issue_count")),
        }
        for row in source_issue_statuses[:_MAX_ISSUE_STATUSES]
        if isinstance(row, dict)
    ]

    return {
        "schema_version": 1,
        "generated_at": _iso_utc(_utc_now()),
        "available": True,
        "source": "production_database",
        "counts": {key: _optional_count(source_counts.get(key)) for key in _COUNT_KEYS},
        "issue_statuses": issue_statuses,
        "latest_scan_at": production.get("latest_scan_at"),
        "rule_source": production.get("rule_source"),
    }


@router.get("/crawler-providers")
def crawler_providers(
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    query = text(
        """
        SELECT provider,
               COUNT(*) AS total_count,
               COUNT(*) FILTER (WHERE is_active = true) AS active_count,
               MAX(updated_at) AS last_run_at
        FROM courses
        GROUP BY provider
        ORDER BY total_count DESC
        LIMIT :limit
        """
    )
    rows = db.execute(query, {"limit": limit}).fetchall()
    items = []
    for row in rows:
        provider = str(row[0] or "")
        total = int(row[1] or 0)
        active = int(row[2] or 0)
        last_run = row[3]
        last_run_str = _iso_utc(last_run) if last_run else None
        items.append({
            "provider": provider,
            "run_count": 1,
            "success_count": 1,
            "partial_count": 0,
            "failure_count": 0,
            "collected_count": total,
            "new_count": None,
            "updated_count": active,
            "failed_item_count": 0,
            "success_rate": 100.0,
            "last_run_at": last_run_str,
        })
    return {
        "available": True,
        "has_data": len(items) > 0,
        "total": len(items),
        "limit": limit,
        "truncated": False,
        "items": items,
        "reasons": [],
    }


def _clean_crawler_name(name: str, target_key: str) -> str:
    if target_key and target_key.strip():
        return target_key.strip()
    raw = str(name or "").strip()
    if not raw:
        return "Unknown Crawler"
    match = re.search(r"([A-Za-z0-9_]+)\.py", raw)
    if match:
        filename = match.group(1)
        if filename.startswith("Crawler_"):
            return filename[len("Crawler_"):]
        return filename
    return raw[:48]


@router.get("/crawler-logs")
def crawler_logs(
    limit: int = Query(default=30, ge=1, le=100),
    status: str = Query(default="", max_length=32),
    provider: str = Query(default="", max_length=100),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    where_clauses = []
    params: dict[str, Any] = {"limit": limit}
    if status and status.strip():
        where_clauses.append("status = :status")
        params["status"] = status.strip().lower()
    if provider and provider.strip():
        where_clauses.append("(target_key ILIKE :provider OR crawler_name ILIKE :provider)")
        params["provider"] = f"%{provider.strip()}%"

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    query = text(
        f"""
        SELECT id,
               crawler_name,
               target_key,
               source_type,
               status,
               started_at,
               ended_at,
               duration_seconds,
               collected_count,
               inserted_count,
               updated_count,
               skipped_count,
               error_type,
               error_message
        FROM crawler_run_log
        {where_sql}
        ORDER BY id DESC
        LIMIT :limit
        """
    )
    try:
        rows = db.execute(query, params).fetchall()
    except Exception:
        return {
            "schema_version": 1,
            "generated_at": _iso_utc(_utc_now()),
            "available": False,
            "total": 0,
            "limit": limit,
            "items": [],
        }

    items = []
    for row in rows:
        c_id = int(row[0])
        c_name = str(row[1] or "")
        t_key = str(row[2] or "")
        s_type = str(row[3] or "")
        c_status = str(row[4] or "unknown")
        started = row[5]
        ended = row[6]
        duration = float(row[7]) if row[7] is not None else None
        collected = int(row[8] or 0)
        inserted = int(row[9] or 0)
        updated = int(row[10] or 0)
        skipped = int(row[11] or 0)
        err_type = str(row[12] or "") if row[12] else None
        err_msg = str(row[13] or "") if row[13] else None

        display_name = _clean_crawler_name(c_name, t_key)
        items.append({
            "id": c_id,
            "crawler_name": display_name,
            "provider": t_key if t_key else display_name,
            "source_type": s_type,
            "status": c_status,
            "started_at": _iso_utc(started) if started else None,
            "ended_at": _iso_utc(ended) if ended else None,
            "duration_seconds": duration,
            "collected_count": collected,
            "inserted_count": inserted,
            "updated_count": updated,
            "skipped_count": skipped,
            "error_type": err_type,
            "error_message": err_msg[:500] if err_msg else None,
        })
    return {
        "schema_version": 1,
        "generated_at": _iso_utc(_utc_now()),
        "available": True,
        "total": len(items),
        "limit": limit,
        "items": items,
    }

