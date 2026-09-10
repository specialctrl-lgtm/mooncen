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
    CrawlerRunRequest,
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
    require_ops_operator,
    require_ops_viewer,
)
from ops_agent.crawler_registry import (
    CrawlerProviderRegistryError,
    resolve_crawler_provider_execution,
    reviewed_crawler_providers,
)
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
    if before["job_type"] in {
        "crawler_run",
        "crawler_retry",
        "deployment",
        "rollback",
    }:
        raise HTTPException(
            status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
            detail="이 작업 유형은 전용 운영 경로에서만 제어할 수 있습니다.",
        )
    if before["status"] not in ACTIVE_JOB_STATUSES:
        raise HTTPException(status_code=409, detail="Job is not active")
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
    if original["job_type"] in {
        "crawler_run",
        "crawler_retry",
        "deployment",
        "rollback",
    }:
        raise HTTPException(
            status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
            detail="이 작업 유형은 전용 운영 경로에서만 제어할 수 있습니다.",
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
