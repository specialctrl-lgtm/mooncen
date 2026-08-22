from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import sys
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import Json, RealDictCursor, execute_values


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from tools.standard_category_mapper import classify_standard_category
from DB.connection_settings import database_connect_options
from DB.course_lifecycle import apply_ended_course_lifecycle
from DB.course_upsert_guards import (
    coalesce_provider_course_ids_by_raw_url,
    normalize_course_raw_url,
)
from service_group import infer_service_group

CULTURE_PROVIDER_APPLICATION_FALLBACK = {
    "HOMEPLUS",
    "EMART",
    "LOTTE",
    "HYUNDAI_DEPT",
    "GALLERIA",
    "AK_PLAZA",
    "ELAND_RETAIL",
    "SHINSEGAE_ACADEMY",
    "LOTTE_MART",
}
COURSE_UPSERT_CHUNK_SIZE = 250

BRANCH_COLUMNS = [
    "provider",
    "branch_code",
    "name",
    "address",
    "phone",
    "lat",
    "lon",
    "operating_hours",
    "website_url",
    "facility_type",
    "facility_category",
    "facility_source",
    "facility_source_sheet",
    "facility_service_group",
    "facility_collection_category",
    "region_sido",
    "region_sigungu",
    "regular_holiday",
    "admission_fee",
    "basic_info",
    "address_source",
    "coordinate_source",
    "location_confidence",
    "location_verified",
    "location_checked_at",
    "location_query",
    "geocode_status",
    "geocode_reason_code",
    "geocode_attempt_count",
    "geocode_candidates",
    "geocode_next_retry_at",
    "geocode_last_error",
    "geocode_last_attempt_at",
]

COURSE_COLUMNS = [
    "provider",
    "provider_course_id",
    "title",
    "title_raw",
    "title_prefix_removed",
    "instructor",
    "target",
    "category_raw",
    "collection_category",
    "domain_category",
    "standard_category_key",
    "standard_category_label",
    "source_group",
    "operator_type",
    "service_group",
    "collection_type",
    "fee",
    "material_fee",
    "sessions",
    "schedule_raw",
    "schedule_days",
    "schedule_dates",
    "schedule_time_start",
    "schedule_time_end",
    "schedule_frequency",
    "schedule_duration_minutes",
    "start_date",
    "end_date",
    "apply_start",
    "apply_end",
    "apply_period_raw",
    "capacity_total",
    "capacity_current",
    "capacity_remaining",
    "waitlist_total",
    "venue_name",
    "venue_address",
    "application_url",
    "application_type",
    "application_method_raw",
    "reservation_available",
    "discovery_status",
    "program_type",
    "eligibility_raw",
    "raw_fields",
    "status",
    "source_endpoint",
    "raw_url",
    "description",
    "image_url",
    "view_count",
    "is_active",
    "first_seen_at",
    "last_seen_at",
    "removed_at",
    "content_hash",
    "change_detected_at",
    "target_age_group",
    "target_min_age",
    "target_max_age",
    "target_with_parent",
    "target_tags",
    "target_age_is_explicit",
]

CULTURE_CENTER_PROVIDERS = {
    "HOMEPLUS",
    "LOTTE",
    "EMART",
    "HYUNDAI_DEPT",
    "GALLERIA",
    "AK_PLAZA",
    "ELAND_RETAIL",
    "SHINSEGAE_ACADEMY",
    "LOTTE_MART",
}
CULTURE_CENTER_CATEGORY_NAMES = {"문화센터", "문화 센터"}
CULTURE_CENTER_STANDARD_CATEGORY_CONFIG = str(ROOT / "config" / "culture_center_standard_categories.yaml")
PROVIDER_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,49}$")
AGGREGATE_PROVIDER_OWNERS = {
    "EXPERIENCE_TARGETS",
    "MUNICIPAL_RESERVATION_TARGETS",
}
APPLY_ADVISORY_LOCK_ID = 5_565_442_503_298_382

COURSE_UPDATE_COLUMNS = [
    column
    for column in COURSE_COLUMNS
    # Primary-owned counters/identity history must survive staging refreshes.
    if column not in {"provider", "provider_course_id", "first_seen_at", "view_count"}
]


@dataclass
class ApplyStats:
    branches: int = 0
    courses: int = 0
    inserted: int = 0
    updated: int = 0
    closed: int = 0
    ended_closed: int = 0
    ended_deactivated: int = 0
    errors: int = 0


@dataclass(frozen=True)
class CloseSafetyDecision:
    allowed_providers: tuple[str, ...]
    blocked_providers: dict[str, str]
    incoming_counts: dict[str, int]
    active_counts: dict[str, int]


@dataclass(frozen=True)
class BatchProviderOwnership:
    requested_owners: tuple[str, ...]
    successful_owners: tuple[str, ...]
    failed_owners: tuple[str, ...]
    course_provider_owners: dict[str, str]
    successful_course_providers: tuple[str, ...]


def db_config(prefix: str, default_name: str) -> dict[str, Any]:
    host = os.getenv(f"{prefix}_DB_HOST", os.getenv("DB_HOST", "localhost"))
    default_port = "55432" if prefix == "CRAWL_STAGING" else os.getenv("DB_PORT", "5432")
    return {
        "host": host,
        "port": os.getenv(f"{prefix}_DB_PORT", default_port),
        "dbname": os.getenv(f"{prefix}_DB_NAME", default_name),
        "user": os.getenv(f"{prefix}_DB_USER", os.getenv("DB_USER", "mooncen_admin")),
        "password": os.getenv(f"{prefix}_DB_PASSWORD", os.getenv("DB_PASSWORD", "")),
        **database_connect_options(host, f"mooncen-{prefix.lower().replace('_', '-')}"),
    }


def normalize_json(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, list):
        return [normalize_json(item) for item in value]
    if isinstance(value, dict):
        return {key: normalize_json(item) for key, item in value.items()}
    return value


def sql_value(column: str, value: Any) -> Any:
    if column in {"schedule_dates", "raw_fields", "ai_title_result", "basic_info"} and value is not None:
        return Json(normalize_json(value))
    return value


def is_culture_center_course_row(row: dict[str, Any]) -> bool:
    provider = str(row.get("provider") or "").strip().upper()
    if provider in CULTURE_CENTER_PROVIDERS:
        return True
    values = (
        row.get("service_group"),
        row.get("collection_category"),
        row.get("domain_category"),
    )
    return any(str(value or "").strip() in CULTURE_CENTER_CATEGORY_NAMES for value in values)


def standard_category_values(row: dict[str, Any]) -> tuple[str, str]:
    config_path = CULTURE_CENTER_STANDARD_CATEGORY_CONFIG if is_culture_center_course_row(row) else None
    result = classify_standard_category(
        {
            "title": row.get("title"),
            "title_raw": row.get("title_raw"),
            "category_raw": row.get("category_raw"),
            "collection_category": row.get("collection_category"),
            "domain_category": row.get("domain_category"),
            "source_group": row.get("source_group"),
            "program_type": row.get("program_type"),
            "description": row.get("description"),
        },
        config_path,
    )
    return result.key, result.label


def connect(config: dict[str, str]):
    return psycopg2.connect(**config)


def acquire_primary_apply_lock(conn) -> None:
    """Serialize all dry-run/apply workers against the production primary."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_try_advisory_xact_lock(%s)",
            (APPLY_ADVISORY_LOCK_ID,),
        )
        row = cur.fetchone()
    if not row or row[0] is not True:
        raise RuntimeError("another staging apply operation is already running")


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _strict_nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RuntimeError(f"batch metadata {field} must be a non-negative integer")
    return value


def _strict_provider_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list):
        raise RuntimeError(f"batch metadata {field} must be a provider list")
    providers: list[str] = []
    for raw in value:
        provider = str(raw or "").strip().upper()
        if not PROVIDER_NAME_PATTERN.fullmatch(provider):
            raise RuntimeError(f"batch metadata {field} contains an invalid provider")
        providers.append(provider)
    if len(providers) != len(set(providers)):
        raise RuntimeError(f"batch metadata {field} contains duplicate providers")
    return providers


def _is_explicit_upsert_only_collection(result: dict[str, Any]) -> bool:
    """Accept a successful scoped crawl only when lifecycle closure is disabled."""
    if (
        result.get("collection_complete") is not False
        or result.get("close_missing_enabled") is not False
    ):
        return False

    limit = result.get("limit")
    if limit is not None and (
        isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0
    ):
        raise RuntimeError("batch metadata limit must be a positive integer or null")

    scoped = limit is not None
    for field in ("branch_code", "branch_name"):
        value = result.get(field)
        if value is not None and not isinstance(value, str):
            raise RuntimeError(f"batch metadata {field} must be a string or null")
        scoped = scoped or bool(str(value or "").strip())
    return scoped


def validate_batch_provider_ownership(
    batch_status: str,
    result: dict[str, Any],
    loaded_providers: set[str],
    *,
    allow_scoped_upsert: bool = False,
) -> BatchProviderOwnership:
    """Validate scheduled-owner evidence before any primary mutation.

    A failed aggregate can leave valid-looking rows for both successful and
    failed concrete providers. The batch producer records the deterministic
    concrete-provider -> scheduled-owner mapping plus transaction-commit evidence
    so the applier can reject malformed metadata and publish only successful rows.
    """
    status = str(batch_status or "").strip().upper()
    if status not in {"COLLECTED", "FAILED"}:
        raise RuntimeError(f"batch status is not applyable: {status or 'missing'}")

    requested = _strict_provider_list(
        result.get("providers_requested"),
        "providers_requested",
    )
    if not requested:
        raise RuntimeError("batch metadata providers_requested is empty")

    provider_results = result.get("provider_results")
    if not isinstance(provider_results, list):
        raise RuntimeError("batch metadata provider_results must be a list")
    result_success: dict[str, bool] = {}
    for item in provider_results:
        if not isinstance(item, dict):
            raise RuntimeError("batch metadata provider_results contains a non-object")
        provider = str(item.get("provider") or "").strip().upper()
        if not PROVIDER_NAME_PATTERN.fullmatch(provider):
            raise RuntimeError("batch metadata provider_results contains an invalid provider")
        if provider in result_success:
            raise RuntimeError("batch metadata provider_results contains duplicate providers")
        success = item.get("success")
        if not isinstance(success, bool):
            raise RuntimeError("batch metadata provider_results success must be boolean")
        result_success[provider] = success

    requested_set = set(requested)
    if set(result_success) != requested_set:
        raise RuntimeError("batch metadata provider_results does not match providers_requested")

    total = _strict_nonnegative_int(result.get("providers_total"), "providers_total")
    completed = _strict_nonnegative_int(
        result.get("providers_completed"),
        "providers_completed",
    )
    failed = _strict_nonnegative_int(result.get("providers_failed"), "providers_failed")
    successful_owners = {provider for provider, success in result_success.items() if success}
    failed_owners = set(result_success) - successful_owners
    reported_failed = set(
        _strict_provider_list(result.get("failed_providers"), "failed_providers")
    )

    if total != len(requested) or completed != len(successful_owners):
        raise RuntimeError("batch metadata provider completion counters do not match results")
    if failed != len(failed_owners) or completed + failed != total:
        raise RuntimeError("batch metadata provider failure counters do not match results")
    if reported_failed != failed_owners:
        raise RuntimeError("batch metadata failed_providers does not match provider_results")
    if status == "COLLECTED":
        if failed or completed != total:
            raise RuntimeError("COLLECTED batch lacks complete provider evidence")
        if (
            result.get("collection_complete") is not True
            and not (
                allow_scoped_upsert
                and _is_explicit_upsert_only_collection(result)
            )
        ):
            raise RuntimeError("COLLECTED batch lacks complete provider evidence")
    else:
        if failed <= 0 or result.get("collection_complete") is True:
            raise RuntimeError("FAILED batch lacks partial-success provider evidence")

    raw_mapping = result.get("course_provider_owners")
    if not isinstance(raw_mapping, dict) or not raw_mapping:
        raise RuntimeError("batch metadata course_provider_owners is missing")
    owner_mapping: dict[str, str] = {}
    for raw_provider, raw_owner in raw_mapping.items():
        provider = str(raw_provider or "").strip().upper()
        owner = str(raw_owner or "").strip().upper()
        if (
            not PROVIDER_NAME_PATTERN.fullmatch(provider)
            or not PROVIDER_NAME_PATTERN.fullmatch(owner)
            or owner not in requested_set
        ):
            raise RuntimeError("batch metadata course_provider_owners is malformed")
        if provider in owner_mapping and owner_mapping[provider] != owner:
            raise RuntimeError("batch metadata course_provider_owners has conflicting owners")
        owner_mapping[provider] = owner

    for owner in requested_set - AGGREGATE_PROVIDER_OWNERS:
        if owner_mapping.get(owner) != owner:
            raise RuntimeError(
                f"direct provider ownership evidence is missing or changed: {owner}"
            )
    if set(owner_mapping.values()) != requested_set:
        raise RuntimeError(
            "batch metadata course_provider_owners does not cover every scheduled owner"
        )

    raw_concrete_results = result.get("concrete_provider_results", [])
    if not isinstance(raw_concrete_results, list):
        raise RuntimeError("batch metadata concrete_provider_results must be a list")
    concrete_success: dict[str, bool] = {}
    for item in raw_concrete_results:
        if not isinstance(item, dict):
            raise RuntimeError(
                "batch metadata concrete_provider_results contains a non-object"
            )
        provider = str(item.get("provider") or "").strip().upper()
        owner = str(item.get("scheduled_owner") or "").strip().upper()
        success = item.get("success")
        if (
            not PROVIDER_NAME_PATTERN.fullmatch(provider)
            or provider in concrete_success
            or owner not in AGGREGATE_PROVIDER_OWNERS
            or owner_mapping.get(provider) != owner
        ):
            raise RuntimeError(
                "batch metadata concrete_provider_results has invalid ownership"
            )
        if not isinstance(success, bool):
            raise RuntimeError(
                "batch metadata concrete_provider_results success must be boolean"
            )
        targets_total = _strict_nonnegative_int(
            item.get("targets_total"),
            "concrete_provider_results.targets_total",
        )
        targets_succeeded = _strict_nonnegative_int(
            item.get("targets_succeeded"),
            "concrete_provider_results.targets_succeeded",
        )
        _strict_nonnegative_int(
            item.get("collected_courses"),
            "concrete_provider_results.collected_courses",
        )
        _strict_nonnegative_int(
            item.get("saved_courses"),
            "concrete_provider_results.saved_courses",
        )
        if (
            targets_total <= 0
            or targets_succeeded > targets_total
            or (success and targets_succeeded != targets_total)
        ):
            raise RuntimeError(
                "batch metadata concrete_provider_results target counters do not match"
            )
        if owner in successful_owners and not success:
            raise RuntimeError(
                "successful aggregate owner has a failed concrete provider result"
            )
        concrete_success[provider] = success

    concrete_total = _strict_nonnegative_int(
        result.get("concrete_providers_total", 0),
        "concrete_providers_total",
    )
    concrete_completed = _strict_nonnegative_int(
        result.get("concrete_providers_completed", 0),
        "concrete_providers_completed",
    )
    concrete_failed = _strict_nonnegative_int(
        result.get("concrete_providers_failed", 0),
        "concrete_providers_failed",
    )
    successful_concrete = {
        provider for provider, success in concrete_success.items() if success
    }
    if (
        concrete_total != len(concrete_success)
        or concrete_completed != len(successful_concrete)
        or concrete_failed != concrete_total - concrete_completed
    ):
        raise RuntimeError(
            "batch metadata concrete provider counters do not match results"
        )
    if status == "FAILED" and completed <= 0 and not successful_concrete:
        raise RuntimeError("FAILED batch lacks partial-success provider evidence")

    normalized_loaded: set[str] = set()
    for raw_provider in loaded_providers:
        provider = str(raw_provider or "").strip().upper()
        if not PROVIDER_NAME_PATTERN.fullmatch(provider):
            raise RuntimeError("staging rows contain an invalid or missing provider")
        normalized_loaded.add(provider)
    unmapped = normalized_loaded - set(owner_mapping)
    if unmapped:
        raise RuntimeError(
            "staging rows contain providers without scheduled owner evidence: "
            + ",".join(sorted(unmapped))
        )

    successful_course_providers = {
        provider
        for provider, owner in owner_mapping.items()
        if owner in successful_owners
    }
    successful_course_providers.update(successful_concrete)

    return BatchProviderOwnership(
        requested_owners=tuple(sorted(requested_set)),
        successful_owners=tuple(sorted(successful_owners)),
        failed_owners=tuple(sorted(failed_owners)),
        course_provider_owners=dict(sorted(owner_mapping.items())),
        successful_course_providers=tuple(sorted(successful_course_providers)),
    )


def rows_owned_by_successful_providers(
    rows: list[dict[str, Any]],
    ownership: BatchProviderOwnership,
) -> list[dict[str, Any]]:
    successful = set(ownership.successful_course_providers)
    return [
        row
        for row in rows
        if str(row.get("provider") or "").strip().upper() in successful
    ]


def collection_is_complete(batch_status: str, result: dict[str, Any]) -> tuple[bool, str]:
    """Require explicit, machine-verifiable evidence before closing rows."""
    if batch_status != "COLLECTED":
        return False, f"batch_status={batch_status or 'missing'}"
    if result.get("collection_complete") is not True:
        return False, "collection_complete evidence is missing or false"
    completed = safe_int(result.get("providers_completed"), -1)
    total = safe_int(result.get("providers_total"), -1)
    failed = safe_int(result.get("providers_failed"), -1)
    if total <= 0 or completed != total or failed != 0:
        return False, f"provider completion mismatch completed={completed} total={total} failed={failed}"
    if result.get("limit") is not None or result.get("branch_code") or result.get("branch_name"):
        return False, "limited or branch-filtered collection"
    return True, "complete"


def validate_control_plane_promotion_gate(
    result: dict[str, Any],
    *,
    dry_run: bool,
) -> None:
    """Keep held distributed batches inspectable but impossible to apply.

    A dry-run is read-only and is part of the approval workflow.  Every path
    that can mutate the primary database, including an explicitly pinned batch,
    requires the finalizer's sealed promotion eligibility bit.
    """
    if result.get("control_plane") is True and not dry_run:
        if result.get("promotion_eligible") is not True:
            raise RuntimeError("Control-plane batch is held for explicit promotion approval")


def provider_course_counts(courses: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in courses:
        provider = str(row.get("provider") or "").strip()
        if provider:
            counts[provider] = counts.get(provider, 0) + 1
    return counts


def evaluate_close_safety(
    providers: list[str],
    incoming_counts: dict[str, int],
    active_counts: dict[str, int],
    reported_counts: dict[str, Any] | None = None,
    *,
    min_ratio: float = 0.65,
    max_absolute_drop: int = 2000,
    ratio_baseline: int = 20,
) -> CloseSafetyDecision:
    """Block provider closure when collection evidence indicates a sharp drop."""
    allowed: list[str] = []
    blocked: dict[str, str] = {}
    reported_counts = reported_counts or {}
    for provider in providers:
        incoming = max(0, safe_int(incoming_counts.get(provider)))
        active = max(0, safe_int(active_counts.get(provider)))
        reported = safe_int(reported_counts.get(provider), -1)
        if reported < 0:
            blocked[provider] = "batch provider count evidence missing"
            continue
        if reported != incoming:
            blocked[provider] = f"batch count mismatch reported={reported} loaded={incoming}"
            continue
        if incoming <= 0:
            blocked[provider] = "no valid incoming courses"
            continue
        drop = max(active - incoming, 0)
        ratio = incoming / active if active else 1.0
        if active >= ratio_baseline and ratio < min_ratio:
            blocked[provider] = f"collection ratio {ratio:.3f} below {min_ratio:.3f} ({incoming}/{active})"
            continue
        if max_absolute_drop >= 0 and drop > max_absolute_drop:
            blocked[provider] = f"absolute drop {drop} exceeds {max_absolute_drop} ({incoming}/{active})"
            continue
        allowed.append(provider)
    return CloseSafetyDecision(tuple(sorted(allowed)), blocked, dict(incoming_counts), dict(active_counts))


def latest_batch_id(conn) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT crawl_batch_id
            FROM crawl_batches
            WHERE crawl_batch_id IS NOT NULL
              AND btrim(crawl_batch_id) <> ''
              AND status IN ('COLLECTED', 'FAILED')
              AND (
                  result->>'control_plane' IS DISTINCT FROM 'true'
                  OR result->>'promotion_eligible' = 'true'
              )
              AND (
                  status = 'FAILED'
                  OR result->>'collection_complete' = 'true'
              )
              AND COALESCE(total_courses, 0) > 0
              AND jsonb_typeof(result->'course_provider_owners') = 'object'
              AND (
                  result->>'close_missing_enabled' = 'true'
                   OR CASE
                       WHEN result->>'providers_completed' ~ '^[0-9]+$'
                       THEN (result->>'providers_completed')::integer
                       ELSE 0
                   END > 0
                   OR CASE
                       WHEN result->>'concrete_providers_completed' ~ '^[0-9]+$'
                       THEN (result->>'concrete_providers_completed')::integer
                       ELSE 0
                   END > 0
               )
            ORDER BY started_at DESC NULLS LAST, created_at DESC NULLS LAST
            LIMIT 1
            """
        )
        row = cur.fetchone()
    if not row:
        raise RuntimeError("No ready staging batch found in crawl_batches")
    return row[0]


def load_batch_metadata(conn, batch_id: str) -> dict[str, Any]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT status, total_branches, total_courses, valid_courses,
                   invalid_courses, result
            FROM crawl_batches
            WHERE crawl_batch_id = %s
            """,
            (batch_id,),
        )
        row = cur.fetchone()
    if not row:
        return {}
    result = row.get("result")
    return {
        "status": row.get("status") or "",
        "total_branches": int(row.get("total_branches") or 0),
        "total_courses": int(row.get("total_courses") or 0),
        "valid_courses": int(row.get("valid_courses") or 0),
        "invalid_courses": int(row.get("invalid_courses") or 0),
        "result": dict(result) if isinstance(result, dict) else {},
    }


def _selected_control_attempts(batch_id: str, batch_result: dict[str, Any]) -> list[dict[str, Any]] | None:
    if batch_result.get("control_plane") is not True:
        return None
    try:
        canonical_batch_id = str(UUID(batch_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise RuntimeError("Control-plane crawl batch id is invalid") from exc
    if canonical_batch_id != batch_id or batch_result.get("control_batch_id") != batch_id:
        raise RuntimeError("Control-plane crawl batch identity does not match its sealed result")
    raw_attempts = batch_result.get("selected_attempts")
    if not isinstance(raw_attempts, list) or not 1 <= len(raw_attempts) <= 512:
        raise RuntimeError("Control-plane crawl batch has no bounded selected attempt set")
    normalized: list[dict[str, Any]] = []
    seen_attempts: set[str] = set()
    seen_jobs: set[str] = set()
    required_fields = {"attempt_id", "job_id", "attempt_no", "lease_epoch"}
    for raw in raw_attempts:
        if not isinstance(raw, dict) or set(raw) != required_fields:
            raise RuntimeError("Control-plane selected attempt contract is invalid")
        attempt_id = str(raw.get("attempt_id") or "")
        job_id = str(raw.get("job_id") or "")
        try:
            if str(UUID(attempt_id)) != attempt_id or str(UUID(job_id)) != job_id:
                raise ValueError("non-canonical UUID")
        except (ValueError, AttributeError) as exc:
            raise RuntimeError("Control-plane selected attempt UUID is invalid") from exc
        attempt_no = raw.get("attempt_no")
        lease_epoch = raw.get("lease_epoch")
        if (
            isinstance(attempt_no, bool)
            or not isinstance(attempt_no, int)
            or attempt_no <= 0
            or isinstance(lease_epoch, bool)
            or not isinstance(lease_epoch, int)
            or lease_epoch <= 0
            or attempt_id in seen_attempts
            or job_id in seen_jobs
        ):
            raise RuntimeError("Control-plane selected attempt identity is invalid or duplicated")
        seen_attempts.add(attempt_id)
        seen_jobs.add(job_id)
        normalized.append(
            {
                "attempt_id": attempt_id,
                "job_id": job_id,
                "attempt_no": attempt_no,
                "lease_epoch": lease_epoch,
            }
        )
    return normalized


def load_rows(
    conn,
    batch_id: str,
    provider: str = "",
    *,
    batch_result: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    provider_filter = "AND provider = %s" if provider else ""
    params: list[Any] = [batch_id]
    if provider:
        params.append(provider)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        selected_attempts = _selected_control_attempts(batch_id, batch_result or {})
        if selected_attempts is not None:
            cur.execute(
                """
                WITH selected_attempts AS (
                    SELECT *
                    FROM jsonb_to_recordset(%s::jsonb) AS selected(
                        attempt_id uuid,
                        job_id uuid,
                        attempt_no integer,
                        lease_epoch bigint
                    )
                )
                SELECT COUNT(*) AS selected_count
                FROM selected_attempts selected
                JOIN ops_crawler_batch_tasks task
                  ON task.batch_id = %s::uuid
                 AND task.job_id = selected.job_id
                JOIN ops_jobs job
                  ON job.id = selected.job_id
                 AND job.attempt_no = selected.attempt_no
                 AND job.lease_epoch = selected.lease_epoch
                JOIN ops_crawler_task_attempts attempt
                  ON attempt.id = selected.attempt_id
                 AND attempt.job_id = selected.job_id
                 AND attempt.attempt_no = selected.attempt_no
                 AND attempt.lease_epoch = selected.lease_epoch
                 AND attempt.status <> 'running'
                """,
                (Json(selected_attempts), batch_id),
            )
            verification = cur.fetchone() or {}
            selected_count = (
                verification.get("selected_count", 0)
                if hasattr(verification, "get")
                else verification[0]
            )
            if int(selected_count or 0) != len(selected_attempts):
                raise RuntimeError(
                    "Control-plane selected attempts are stale or outside the batch"
                )
            fenced_filter = "AND snapshot.provider = %s" if provider else ""
            fenced_params: list[Any] = [Json(selected_attempts), batch_id]
            if provider:
                fenced_params.append(provider)
            cur.execute(
                f"""
                WITH selected_attempts AS (
                    SELECT *
                    FROM jsonb_to_recordset(%s::jsonb) AS selected(
                        attempt_id uuid,
                        job_id uuid,
                        attempt_no integer,
                        lease_epoch bigint
                    )
                ), latest AS (
                    SELECT DISTINCT ON (
                        snapshot.attempt_id, snapshot.provider, snapshot.branch_code
                    ) snapshot.row_data, snapshot.attempt_id,
                      snapshot.provider, snapshot.branch_code
                    FROM crawl_staging.fenced_branch_snapshots snapshot
                    JOIN selected_attempts selected
                      ON selected.attempt_id = snapshot.attempt_id
                     AND selected.job_id = snapshot.job_id
                     AND selected.attempt_no = snapshot.attempt_no
                     AND selected.lease_epoch = snapshot.lease_epoch
                    WHERE snapshot.crawl_batch_id = %s::uuid {fenced_filter}
                    ORDER BY snapshot.attempt_id, snapshot.provider,
                             snapshot.branch_code, snapshot.snapshot_id DESC
                )
                SELECT row_data
                FROM latest
                ORDER BY provider, branch_code
                """,
                fenced_params,
            )
            branch_snapshot_rows = list(cur.fetchall())
            cur.execute(
                f"""
                WITH selected_attempts AS (
                    SELECT *
                    FROM jsonb_to_recordset(%s::jsonb) AS selected(
                        attempt_id uuid,
                        job_id uuid,
                        attempt_no integer,
                        lease_epoch bigint
                    )
                ), latest AS (
                    SELECT DISTINCT ON (
                        snapshot.attempt_id, snapshot.provider,
                        snapshot.provider_course_id
                    ) snapshot.row_data, snapshot.attempt_id,
                      snapshot.provider, snapshot.provider_course_id
                    FROM crawl_staging.fenced_course_snapshots snapshot
                    JOIN selected_attempts selected
                      ON selected.attempt_id = snapshot.attempt_id
                     AND selected.job_id = snapshot.job_id
                     AND selected.attempt_no = snapshot.attempt_no
                     AND selected.lease_epoch = snapshot.lease_epoch
                    WHERE snapshot.crawl_batch_id = %s::uuid {fenced_filter}
                    ORDER BY snapshot.attempt_id, snapshot.provider,
                             snapshot.provider_course_id, snapshot.snapshot_id DESC
                )
                SELECT row_data
                FROM latest
                ORDER BY provider, provider_course_id
                """,
                fenced_params,
            )
            course_snapshot_rows = list(cur.fetchall())
            branches = []
            for row in branch_snapshot_rows:
                payload = row.get("row_data")
                if not isinstance(payload, dict):
                    raise RuntimeError("Fenced staging branch snapshot payload is invalid")
                branches.append(dict(payload))
            branch_by_id = {
                str(row.get("id")): row
                for row in branches
                if row.get("id") is not None
            }
            courses = []
            for row in course_snapshot_rows:
                payload = row.get("row_data")
                if not isinstance(payload, dict):
                    raise RuntimeError("Fenced staging course snapshot payload is invalid")
                course = dict(payload)
                branch = branch_by_id.get(str(course.get("branch_id")))
                course["branch_provider"] = branch.get("provider") if branch else None
                course["branch_code"] = branch.get("branch_code") if branch else None
                course["branch_name"] = branch.get("name") if branch else None
                courses.append(course)
            return branches, courses

        # Fenced distributed workers publish a per-batch snapshot before the
        # mutable staging row can be claimed by a later batch.  Prefer that
        # immutable selection; legacy one-shot batches continue to use the
        # canonical staging tables until their rollout is complete.
        cur.execute(
            f"""
            SELECT row_data
            FROM crawl_staging.branch_snapshots
            WHERE crawl_batch_id = %s {provider_filter}
            ORDER BY provider, branch_code
            """,
            params,
        )
        branch_snapshot_rows = list(cur.fetchall())
        cur.execute(
            f"""
            SELECT row_data
            FROM crawl_staging.course_snapshots
            WHERE crawl_batch_id = %s {provider_filter}
            ORDER BY provider, provider_course_id
            """,
            params,
        )
        course_snapshot_rows = list(cur.fetchall())
        if branch_snapshot_rows or course_snapshot_rows:
            branches = []
            for row in branch_snapshot_rows:
                payload = row.get("row_data")
                if not isinstance(payload, dict):
                    raise RuntimeError("Staging branch snapshot payload is invalid")
                branches.append(dict(payload))
            branch_by_id = {
                str(row.get("id")): row
                for row in branches
                if row.get("id") is not None
            }
            courses = []
            for row in course_snapshot_rows:
                payload = row.get("row_data")
                if not isinstance(payload, dict):
                    raise RuntimeError("Staging course snapshot payload is invalid")
                course = dict(payload)
                branch = branch_by_id.get(str(course.get("branch_id")))
                course["branch_provider"] = branch.get("provider") if branch else None
                course["branch_code"] = branch.get("branch_code") if branch else None
                course["branch_name"] = branch.get("name") if branch else None
                courses.append(course)
            return branches, courses

        cur.execute(
            f"""
            SELECT *
            FROM branches
            WHERE crawl_batch_id = %s {provider_filter}
            ORDER BY provider, branch_code
            """,
            params,
        )
        branches = [dict(row) for row in cur.fetchall()]
        cur.execute(
            f"""
            SELECT c.*, b.provider AS branch_provider, b.branch_code AS branch_code,
                   b.name AS branch_name
            FROM courses c
            LEFT JOIN branches b ON b.id = c.branch_id
            WHERE c.crawl_batch_id = %s {provider_filter.replace('provider', 'c.provider')}
            ORDER BY c.provider, c.provider_course_id
            """,
            params,
        )
        courses = [dict(row) for row in cur.fetchall()]
    return branches, courses


def staging_selection_fingerprint(
    batch_metadata: dict[str, Any],
    branches: list[dict[str, Any]],
    courses: list[dict[str, Any]],
) -> str:
    payload = normalize_json(
        {
            "batch": batch_metadata,
            "branches": branches,
            "courses": courses,
        }
    )
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def validate_expected_staging_fingerprint(
    staging_fingerprint: str,
    expected_staging_fingerprint: str,
) -> None:
    """Reject a changed pinned selection before any primary mutation."""
    if (
        expected_staging_fingerprint
        and staging_fingerprint != expected_staging_fingerprint
    ):
        raise RuntimeError("staging data changed after the reviewed dry-run")


def validate_rows(courses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    seen_raw_urls: dict[tuple[str, str], tuple[str, str]] = {}
    for row in courses:
        provider = str(row.get("provider") or "").strip()
        course_id = str(row.get("provider_course_id") or "").strip()
        title = str(row.get("title") or "").strip()
        key = (provider, course_id)
        if not provider:
            errors.append(error_row(row, "missing_provider", "provider is required"))
        if not course_id:
            errors.append(error_row(row, "missing_provider_course_id", "provider_course_id is required"))
        if not title:
            errors.append(error_row(row, "missing_title", "title is required"))
        if key in seen:
            errors.append(error_row(row, "duplicate_course", f"duplicate course key {provider}/{course_id}"))
        if provider and course_id:
            seen.add(key)
        canonical_url = normalize_course_raw_url(row.get("raw_url"))
        if provider and canonical_url:
            url_key = (provider, canonical_url)
            previous_key = seen_raw_urls.get(url_key)
            if previous_key and previous_key != key:
                errors.append(
                    error_row(
                        row,
                        "duplicate_raw_url",
                        f"canonical raw_url is already used by {previous_key[0]}/{previous_key[1]}",
                    )
                )
            else:
                seen_raw_urls[url_key] = key
    return errors


def validate_provider_promotion_contract(
    *,
    provider: str,
    batch_metadata: dict[str, Any],
    ownership: BatchProviderOwnership,
    branches: list[dict[str, Any]],
    courses: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    staging_fingerprint: str,
    expected_staging_fingerprint: str = "",
) -> None:
    """Fail closed before a limited exact-owner batch can mutate primary."""
    result = batch_metadata.get("result")
    if (
        not PROVIDER_NAME_PATTERN.fullmatch(provider)
        or str(batch_metadata.get("status") or "").strip().upper() != "COLLECTED"
        or not isinstance(result, dict)
        or not _is_explicit_upsert_only_collection(result)
    ):
        raise RuntimeError("provider promotion requires one limited COLLECTED batch")
    aggregate_owner = provider in AGGREGATE_PROVIDER_OWNERS
    owner_mapping = ownership.course_provider_owners
    if (
        ownership.requested_owners != ownership.successful_owners
        or len(ownership.requested_owners) != 1
        or ownership.failed_owners
        or ownership.requested_owners[0] != provider
        or set(owner_mapping.values()) != {provider}
        or (not aggregate_owner and set(owner_mapping) != {provider})
    ):
        raise RuntimeError("provider promotion ownership is not exact")

    course_providers = {
        str(row.get("provider") or "").strip().upper()
        for row in courses
    }
    branch_providers = {
        str(row.get("provider") or "").strip().upper()
        for row in branches
    }
    expected_row_providers = set(owner_mapping) if aggregate_owner else {provider}
    if (
        errors
        or not courses
        or not course_providers
        or not course_providers <= expected_row_providers
        or not branch_providers <= expected_row_providers
        or (not aggregate_owner and course_providers != {provider})
    ):
        raise RuntimeError("provider promotion rows are invalid or incomplete")

    count = len(courses)
    raw_counts = result.get("provider_course_counts")
    expected_counts = provider_course_counts(courses)
    if not isinstance(raw_counts, dict) or raw_counts != expected_counts:
        raise RuntimeError("provider promotion finalized counts are not exact")
    if (
        sum(
            _strict_nonnegative_int(value, f"provider_course_counts.{key}")
            for key, value in raw_counts.items()
        )
        != count
        or batch_metadata.get("total_courses") != count
        or batch_metadata.get("valid_courses") != count
        or batch_metadata.get("invalid_courses") != 0
        or batch_metadata.get("total_branches") != len(branches)
    ):
        raise RuntimeError("provider promotion finalized counts do not match rows")

    if not re.fullmatch(r"[0-9a-f]{64}", staging_fingerprint):
        raise RuntimeError("provider promotion staging fingerprint is invalid")
    if (
        expected_staging_fingerprint
        and staging_fingerprint != expected_staging_fingerprint
    ):
        raise RuntimeError("provider promotion staging data changed after dry-run")


def error_row(row: dict[str, Any], code: str, message: str) -> dict[str, Any]:
    return {
        "provider": row.get("provider"),
        "provider_course_id": row.get("provider_course_id"),
        "error_code": code,
        "error_message": message,
        "row_data": normalize_json(row),
    }


def ensure_primary_staging(conn) -> None:
    """Fail fast when owner-managed primary metadata migrations are missing."""
    required = (
        "public.crawl_batch_validation_errors",
        "public.crawl_batch_apply_logs",
        "crawl_staging.branch_snapshots",
        "crawl_staging.course_snapshots",
    )
    with conn.cursor() as cur:
        cur.execute(
            "SELECT relation_name, to_regclass(relation_name) "
            "FROM unnest(%s::text[]) AS names(relation_name)",
            (list(required),),
        )
        missing = [name for name, relation in cur.fetchall() if relation is None]
    if missing:
        raise RuntimeError(
            "Primary staging metadata is missing: "
            + ", ".join(missing)
            + ". Run DB/setup_db.py --mode migrate with the migration owner."
        )


def insert_validation_errors(conn, batch_id: str, errors: list[dict[str, Any]]) -> None:
    if not errors:
        return
    rows = [
        (
            batch_id,
            item.get("provider"),
            item.get("provider_course_id"),
            item["error_code"],
            item["error_message"],
            Json(item["row_data"]),
        )
        for item in errors
    ]
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO crawl_batch_validation_errors
                (crawl_batch_id, provider, provider_course_id, error_code, error_message, row_data)
            VALUES %s
            """,
            rows,
        )


def successful_apply_result(
    conn,
    batch_id: str,
    scope_provider: str = "",
) -> dict[str, Any] | None:
    scope_provider = str(scope_provider or "").strip().upper()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT result
            FROM crawl_batch_apply_logs
            WHERE crawl_batch_id = %s
              AND dry_run IS FALSE
              AND (
                  (
                      %s = ''
                      AND status IN ('SUCCESS', 'PARTIAL_SUCCESS')
                      AND COALESCE(result->>'apply_scope_provider', '') = ''
                  )
                  OR
                  (
                      %s <> ''
                      AND status = 'SUCCESS'
                      AND COALESCE(result->>'apply_scope_provider', '') IN ('', %s)
                      AND result->'providers' ? %s
                  )
              )
            ORDER BY finished_at DESC NULLS LAST, id DESC
            LIMIT 1
            """,
            (
                batch_id,
                scope_provider,
                scope_provider,
                scope_provider,
                scope_provider,
            ),
        )
        row = cur.fetchone()
    if not row:
        return None
    result = row[0]
    if not isinstance(result, dict):
        raise RuntimeError("successful staging apply log has invalid result evidence")
    return dict(result)


def upload_snapshots(conn, batch_id: str, branches: list[dict[str, Any]], courses: list[dict[str, Any]]) -> None:
    with conn.cursor() as cur:
        if branches:
            branch_rows = [
                (batch_id, row["provider"], row["branch_code"], Json(normalize_json(row)))
                for row in branches
            ]
            execute_values(
                cur,
                """
                INSERT INTO crawl_staging.branch_snapshots
                    (crawl_batch_id, provider, branch_code, row_data)
                VALUES %s
                ON CONFLICT (crawl_batch_id, provider, branch_code)
                DO UPDATE SET row_data = EXCLUDED.row_data, created_at = now()
                """,
                branch_rows,
            )
        if courses:
            course_rows = [
                (batch_id, row["provider"], row["provider_course_id"], Json(normalize_json(row)))
                for row in courses
            ]
            execute_values(
                cur,
                """
                INSERT INTO crawl_staging.course_snapshots
                    (crawl_batch_id, provider, provider_course_id, row_data)
                VALUES %s
                ON CONFLICT (crawl_batch_id, provider, provider_course_id)
                DO UPDATE SET row_data = EXCLUDED.row_data, created_at = now()
                """,
                course_rows,
            )


def upsert_branches(conn, branches: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    mapping: dict[tuple[str, str], str] = {}
    if not branches:
        return mapping
    values = [tuple(sql_value(column, row.get(column)) for column in BRANCH_COLUMNS) for row in branches]
    update_clause = ", ".join(
        f"{column} = EXCLUDED.{column}"
        for column in BRANCH_COLUMNS
        if column not in {"provider", "branch_code"}
    )
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        execute_values(
            cur,
            f"""
            INSERT INTO branches ({", ".join(BRANCH_COLUMNS)})
            VALUES %s
            ON CONFLICT (provider, branch_code)
            DO UPDATE SET {update_clause}
            RETURNING id, provider, branch_code
            """,
            values,
            page_size=len(values),
        )
        for row in cur.fetchall():
            mapping[(row["provider"], row["branch_code"])] = str(row["id"])
    return mapping


def resolve_branch_ids(conn, keys: set[tuple[str, str]]) -> dict[tuple[str, str], str]:
    valid_keys = [(provider, branch_code) for provider, branch_code in keys if provider and branch_code]
    if not valid_keys:
        return {}
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            CREATE TEMP TABLE tmp_branch_keys(provider TEXT, branch_code TEXT)
            ON COMMIT DROP
            """
        )
        execute_values(
            cur,
            "INSERT INTO tmp_branch_keys(provider, branch_code) VALUES %s",
            valid_keys,
        )
        cur.execute(
            """
            SELECT b.id, b.provider, b.branch_code
            FROM branches b
            JOIN tmp_branch_keys k
              ON k.provider = b.provider
             AND k.branch_code = b.branch_code
            """
        )
        return {(row["provider"], row["branch_code"]): str(row["id"]) for row in cur.fetchall()}


def upsert_courses(conn, courses: list[dict[str, Any]], branch_map: dict[tuple[str, str], str]) -> tuple[int, int]:
    if not courses:
        return 0, 0
    insert_columns = ["branch_id", *COURSE_COLUMNS]
    prepared_courses: list[dict[str, Any]] = []
    with conn.cursor(cursor_factory=RealDictCursor) as guard_cursor:
        for source_row in courses:
            row = source_row
            row["raw_url"] = normalize_course_raw_url(row.get("raw_url")) or None
            if (
                str(row.get("provider") or "").strip().upper() in CULTURE_PROVIDER_APPLICATION_FALLBACK
                and not row.get("application_url")
            ):
                row["application_url"] = row["raw_url"]
            prepared_courses.append(row)
        coalesce_provider_course_ids_by_raw_url(
            guard_cursor,
            prepared_courses,
            execute_values_fn=execute_values,
        )
    values = []
    for row in prepared_courses:
        row["service_group"] = infer_service_group(
            provider=row.get("provider"),
            collection_category=row.get("collection_category"),
            domain_category=row.get("domain_category"),
            source_group=row.get("source_group"),
            operator_type=row.get("operator_type"),
            branch_name=row.get("branch_name"),
            venue_name=row.get("venue_name"),
            raw_url=row.get("raw_url"),
            title=" ".join(
                str(value or "").strip()
                for value in (row.get("title"), row.get("title_raw"))
                if str(value or "").strip()
            ),
            category_raw=row.get("category_raw"),
            program_type=row.get("program_type"),
            service_group=row.get("service_group"),
        )
        standard_key, standard_label = standard_category_values(row)
        row["standard_category_key"] = standard_key
        row["standard_category_label"] = standard_label
        branch_key = (row.get("branch_provider") or row.get("provider"), row.get("branch_code"))
        branch_id = branch_map.get(branch_key) if branch_key[1] else None
        values.append((branch_id, *[sql_value(column, row.get(column)) for column in COURSE_COLUMNS]))
    update_clause = ", ".join(
        (
            "last_seen_at = GREATEST("
            "courses.first_seen_at, courses.last_seen_at, EXCLUDED.last_seen_at"
            ")"
            if column == "last_seen_at"
            else f"{column} = EXCLUDED.{column}"
        )
        for column in ["branch_id", *COURSE_UPDATE_COLUMNS]
    )
    cast_columns = {
        "fee": "fee::numeric",
        "material_fee": "material_fee::integer",
        "sessions": "sessions::integer",
        "schedule_days": "schedule_days::text[]",
        "schedule_dates": "schedule_dates::jsonb",
        "schedule_time_start": "schedule_time_start::time",
        "schedule_time_end": "schedule_time_end::time",
        "schedule_duration_minutes": "schedule_duration_minutes::integer",
        "start_date": "start_date::date",
        "end_date": "end_date::date",
        "apply_start": "apply_start::date",
        "apply_end": "apply_end::date",
        "capacity_total": "capacity_total::integer",
        "capacity_current": "capacity_current::integer",
        "capacity_remaining": "capacity_remaining::integer",
        "waitlist_total": "waitlist_total::integer",
        "reservation_available": "reservation_available::boolean",
        "raw_fields": "raw_fields::jsonb",
        "view_count": "view_count::integer",
        "is_active": "is_active::boolean",
        "first_seen_at": (
            "LEAST(first_seen_at::timestamptz, last_seen_at::timestamptz)"
        ),
        "last_seen_at": (
            "GREATEST(first_seen_at::timestamptz, last_seen_at::timestamptz)"
        ),
        "removed_at": "removed_at::timestamptz",
        "change_detected_at": "change_detected_at::timestamptz",
        "target_min_age": "target_min_age::integer",
        "target_max_age": "target_max_age::integer",
        "target_with_parent": "target_with_parent::boolean",
        "target_tags": "target_tags::text[]",
        "target_age_is_explicit": "target_age_is_explicit::boolean",
    }
    select_columns = ["branch_id::uuid", *[cast_columns.get(column, column) for column in COURSE_COLUMNS]]
    query = f"""
        WITH incoming ({", ".join(insert_columns)}) AS (VALUES %s),
        marked AS (
            SELECT incoming.*,
                   CASE WHEN existing.id IS NULL THEN TRUE ELSE FALSE END AS is_insert
            FROM incoming
            LEFT JOIN courses existing
              ON existing.provider = incoming.provider
             AND existing.provider_course_id = incoming.provider_course_id
        ),
        upserted AS (
            INSERT INTO courses ({", ".join(insert_columns)})
            SELECT {", ".join(select_columns)} FROM marked
            ON CONFLICT (provider, provider_course_id)
            DO UPDATE SET {update_clause}, updated_at = now()
            RETURNING provider, provider_course_id
        )
        SELECT
            COUNT(*) FILTER (WHERE is_insert) AS inserted,
            COUNT(*) FILTER (WHERE NOT is_insert) AS updated
        FROM marked
    """
    inserted = 0
    updated = 0
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        for offset in range(0, len(values), COURSE_UPSERT_CHUNK_SIZE):
            chunk = values[offset : offset + COURSE_UPSERT_CHUNK_SIZE]
            execute_values(
                cur,
                query,
                chunk,
                page_size=len(chunk),
            )
            result = cur.fetchone() or {}
            inserted += int(result.get("inserted") or 0)
            updated += int(result.get("updated") or 0)
    return inserted, updated


def load_active_course_counts(conn, providers: list[str]) -> dict[str, int]:
    if not providers:
        return {}
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT provider, COUNT(*) AS active_count
            FROM courses
            WHERE provider = ANY(%s)
              AND is_active IS TRUE
            GROUP BY provider
            """,
            (providers,),
        )
        return {str(row["provider"]): int(row["active_count"] or 0) for row in cur.fetchall()}


def close_missing_courses(conn, batch_id: str, providers: list[str], courses: list[dict[str, Any]]) -> int:
    if not providers:
        return 0
    seen = [(row["provider"], row["provider_course_id"]) for row in courses]
    source_scopes = sorted(
        {
            (str(row.get("provider") or ""), str(row.get("source_endpoint") or ""))
            for row in courses
            if row.get("provider") and row.get("source_endpoint")
        }
    )
    scoped_providers = {provider for provider, _endpoint in source_scopes}
    unscoped_providers = [provider for provider in providers if provider not in scoped_providers]
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TEMP TABLE tmp_seen_courses(provider TEXT, provider_course_id TEXT)
            ON COMMIT DROP
            """
        )
        cur.execute(
            """
            CREATE TEMP TABLE tmp_close_source_scopes(provider TEXT, source_endpoint TEXT)
            ON COMMIT DROP
            """
        )
        if seen:
            execute_values(
                cur,
                "INSERT INTO tmp_seen_courses(provider, provider_course_id) VALUES %s",
                seen,
            )
        if source_scopes:
            execute_values(
                cur,
                "INSERT INTO tmp_close_source_scopes(provider, source_endpoint) VALUES %s",
                source_scopes,
            )
        cur.execute(
            """
            UPDATE courses c
            SET is_active = FALSE,
                status = 'CLOSED',
                removed_at = COALESCE(c.removed_at, now()),
                updated_at = now()
            WHERE c.provider = ANY(%s)
              AND c.is_active IS TRUE
              AND (
                    c.provider = ANY(%s)
                    OR EXISTS (
                        SELECT 1
                        FROM tmp_close_source_scopes scope
                        WHERE scope.provider = c.provider
                          AND scope.source_endpoint = c.source_endpoint
                    )
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM tmp_seen_courses s
                  WHERE s.provider = c.provider
                    AND s.provider_course_id = c.provider_course_id
              )
            """,
            (providers, unscoped_providers),
        )
        return cur.rowcount


def write_apply_log(
    conn,
    batch_id: str,
    dry_run: bool,
    status: str,
    stats: ApplyStats,
    result: dict[str, Any],
    error_message: str = "",
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO crawl_batch_apply_logs
                (crawl_batch_id, source_host, target_host, dry_run, status,
                 inserted_count, updated_count, closed_count, error_count,
                 finished_at, error_message, result)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now(), %s, %s)
            """,
            (
                batch_id,
                socket.gethostname(),
                os.getenv("PRIMARY_DB_HOST", os.getenv("DB_HOST", "cloud")),
                dry_run,
                status,
                stats.inserted,
                stats.updated,
                stats.closed,
                stats.errors,
                error_message,
                Json(result),
            ),
        )


def persist_dry_run_log(primary_config: dict[str, str], batch_id: str, status: str, stats: ApplyStats, result: dict[str, Any], error_message: str = "") -> None:
    conn = connect(primary_config)
    try:
        conn.autocommit = False
        ensure_primary_staging(conn)
        write_apply_log(conn, batch_id, True, status, stats, result, error_message)
        conn.commit()
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply crawler staging batch to cloud primary")
    parser.add_argument("--batch-id", default="", help="crawl_batch_id to apply. Defaults to latest staging batch.")
    provider_group = parser.add_mutually_exclusive_group()
    provider_group.add_argument("--provider", default="", help="Optional provider filter.")
    provider_group.add_argument(
        "--promote-provider",
        default="",
        help="Fail-closed exact-provider promotion for one limited batch.",
    )
    parser.add_argument(
        "--expected-staging-fingerprint",
        default="",
        help="Required dry-run fingerprint for a pinned non-dry-run apply.",
    )
    parser.add_argument(
        "--require-latest-batch",
        action="store_true",
        help="Require --batch-id to match the normal latest eligible batch selection.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate and calculate changes, then rollback.")
    parser.add_argument("--allow-partial", action="store_true", help="Skip invalid rows instead of failing the batch.")
    parser.add_argument("--force", action="store_true", help="Re-apply even if the batch already has a successful apply log.")
    args = parser.parse_args()

    promotion_provider = args.promote_provider.strip().upper()
    requested_provider = promotion_provider or args.provider.strip().upper()
    expected_staging_fingerprint = args.expected_staging_fingerprint.strip().lower()
    if args.require_latest_batch and not args.batch_id:
        parser.error("--require-latest-batch requires --batch-id")
    if promotion_provider:
        if not args.batch_id:
            parser.error("--promote-provider requires --batch-id")
        if args.allow_partial or args.force:
            parser.error("provider promotion does not allow --allow-partial or --force")
        if args.dry_run and expected_staging_fingerprint:
            parser.error("promotion dry-run does not accept an expected fingerprint")
        if not args.dry_run and not re.fullmatch(
            r"[0-9a-f]{64}",
            expected_staging_fingerprint,
        ):
            parser.error("provider promotion apply requires a SHA-256 dry-run fingerprint")
    elif expected_staging_fingerprint:
        if not args.batch_id:
            parser.error("--expected-staging-fingerprint requires --batch-id")
        if args.dry_run:
            parser.error("dry-run does not accept an expected fingerprint")
        if requested_provider:
            parser.error(
                "full-batch expected fingerprint cannot be combined with --provider"
            )
        if args.allow_partial:
            parser.error(
                "full-batch expected fingerprint does not allow --allow-partial"
            )
        if not re.fullmatch(r"[0-9a-f]{64}", expected_staging_fingerprint):
            parser.error("pinned apply requires a SHA-256 dry-run fingerprint")

    staging_config = db_config("CRAWL_STAGING", os.getenv("CRAWL_STAGING_DB_NAME", "mooncen_staging"))
    primary_config = db_config("PRIMARY", os.getenv("PRIMARY_DB_NAME", "mooncen"))
    staging_conn = connect(staging_config)
    primary_conn = connect(primary_config)
    primary_conn.autocommit = False
    acquire_primary_apply_lock(primary_conn)
    staging_conn.set_session(
        isolation_level="REPEATABLE READ",
        readonly=True,
        autocommit=False,
    )
    stats = ApplyStats()
    try:
        batch_id = args.batch_id or latest_batch_id(staging_conn)
    except RuntimeError as exc:
        if args.batch_id:
            raise
        print(json.dumps({"status": "NO_READY_BATCH", "message": str(exc)}, ensure_ascii=False, indent=2))
        staging_conn.close()
        primary_conn.close()
        return 0

    try:
        if args.require_latest_batch:
            selected_batch_id = latest_batch_id(staging_conn)
            if selected_batch_id != batch_id:
                raise RuntimeError(
                    "pinned batch is no longer the latest eligible staging batch"
                )
        row_provider_filter = (
            ""
            if requested_provider in AGGREGATE_PROVIDER_OWNERS
            else requested_provider
        )
        batch_metadata = load_batch_metadata(staging_conn, batch_id)
        batch_result = batch_metadata.get("result") or {}
        validate_control_plane_promotion_gate(
            batch_result,
            dry_run=bool(args.dry_run),
        )
        raw_branches, raw_courses = load_rows(
            staging_conn,
            batch_id,
            row_provider_filter,
            batch_result=batch_result,
        )
        staging_fingerprint = staging_selection_fingerprint(
            batch_metadata,
            raw_branches,
            raw_courses,
        )
        validate_expected_staging_fingerprint(
            staging_fingerprint,
            expected_staging_fingerprint,
        )
        batch_status = str(batch_metadata.get("status") or "")
        loaded_providers = {
            str(row.get("provider") or "").strip().upper()
            for row in [*raw_branches, *raw_courses]
        }
        ownership = validate_batch_provider_ownership(
            batch_status,
            batch_result,
            loaded_providers,
            allow_scoped_upsert=bool(promotion_provider),
        )
        failed_owners = set(ownership.failed_owners)
        owner_mapping = ownership.course_provider_owners
        if requested_provider:
            requested_owner = (
                requested_provider
                if requested_provider in ownership.requested_owners
                and requested_provider in AGGREGATE_PROVIDER_OWNERS
                else owner_mapping.get(requested_provider)
            )
            if requested_owner is None:
                raise RuntimeError(
                    "provider filter has no scheduled owner evidence in this batch"
                )
            successful_course_providers = set(
                ownership.successful_course_providers
            )
            requested_has_successful_rows = (
                any(
                    owner_mapping.get(provider) == requested_provider
                    for provider in successful_course_providers
                )
                if requested_provider in AGGREGATE_PROVIDER_OWNERS
                else requested_provider in successful_course_providers
            )
            if requested_owner in failed_owners and not requested_has_successful_rows:
                raise RuntimeError(
                    "provider filter belongs to a failed scheduled owner"
                )

        branches = rows_owned_by_successful_providers(raw_branches, ownership)
        courses = rows_owned_by_successful_providers(raw_courses, ownership)
        excluded_failed_branches = len(raw_branches) - len(branches)
        excluded_failed_courses = len(raw_courses) - len(courses)
        stats.branches = len(branches)
        stats.courses = len(courses)
        errors = validate_rows(courses)
        stats.errors = len(errors)
        if promotion_provider:
            validate_provider_promotion_contract(
                provider=promotion_provider,
                batch_metadata=batch_metadata,
                ownership=ownership,
                branches=branches,
                courses=courses,
                errors=errors,
                staging_fingerprint=staging_fingerprint,
                expected_staging_fingerprint=expected_staging_fingerprint,
            )
        providers = sorted({row["provider"] for row in courses if row.get("provider")})
        failed_provider_count = len(failed_owners)
        partial_batch = batch_status == "FAILED"
        close_requested = (
            batch_status == "COLLECTED"
            and batch_result.get("close_missing_enabled") is True
        )
        completion_ok, completion_reason = collection_is_complete(batch_status, batch_result)
        close_missing_enabled = close_requested and completion_ok
        close_blocked: dict[str, str] = {}
        if close_requested and not completion_ok:
            close_blocked["*"] = completion_reason
        if requested_provider:
            close_missing_enabled = False
        if partial_batch:
            # Failed provider batches can still contain good rows from completed providers.
            # Never close missing courses from a partial batch, because a failed provider's
            # absence does not mean its courses disappeared.
            close_missing_enabled = False
        allow_partial_validation = args.allow_partial or partial_batch
        activation_full_apply = bool(expected_staging_fingerprint) and not promotion_provider
        if activation_full_apply and (
            partial_batch
            or not completion_ok
            or failed_owners
            or errors
            or stats.courses <= 0
            or not providers
            or excluded_failed_branches
            or excluded_failed_courses
        ):
            raise RuntimeError(
                "reviewed full-batch apply is no longer complete and mutation-safe"
            )

        ensure_primary_staging(primary_conn)
        previous_apply = (
            successful_apply_result(primary_conn, batch_id, requested_provider)
            if not args.dry_run and not args.force
            else None
        )
        if previous_apply is not None:
            result = {
                "batch_id": batch_id,
                "dry_run": False,
                "providers": providers,
                "apply_scope_provider": requested_provider,
                "promotion_provider": promotion_provider,
                "staging_fingerprint": staging_fingerprint,
                "successful_apply_fingerprint": str(
                    previous_apply.get("staging_fingerprint") or ""
                ),
                "branches": stats.branches,
                "courses": stats.courses,
                "status": "SKIPPED_ALREADY_APPLIED",
            }
            primary_conn.rollback()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        insert_validation_errors(primary_conn, batch_id, errors)
        if errors and not allow_partial_validation:
            result = {
                "batch_id": batch_id,
                "dry_run": args.dry_run,
                "branches": stats.branches,
                "courses": stats.courses,
                "providers": providers,
                "errors": stats.errors,
                "status": "FAILED_VALIDATION",
            }
            if args.dry_run:
                primary_conn.rollback()
                persist_dry_run_log(primary_config, batch_id, "FAILED", stats, result, "validation errors")
            else:
                write_apply_log(primary_conn, batch_id, False, "FAILED", stats, result, "validation errors")
                primary_conn.commit()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 2

        invalid_keys = {
            (str(item.get("provider") or "").strip(), str(item.get("provider_course_id") or "").strip())
            for item in errors
        }
        valid_keys = {
            (row.get("provider"), row.get("provider_course_id"))
            for row in courses
            if row.get("provider") and row.get("provider_course_id") and row.get("title")
            and (str(row.get("provider") or "").strip(), str(row.get("provider_course_id") or "").strip()) not in invalid_keys
        }
        valid_courses = [
            row for row in courses
            if (row.get("provider"), row.get("provider_course_id")) in valid_keys
        ]
        requested_close_providers = (
            sorted({row["provider"] for row in valid_courses if row.get("provider")})
            if close_missing_enabled
            else []
        )
        incoming_counts = provider_course_counts(valid_courses)
        # Capture the baseline before inserts can inflate it. Closure decisions
        # compare this completed collection with the previously active corpus.
        active_counts = load_active_course_counts(primary_conn, requested_close_providers)
        close_decision = evaluate_close_safety(
            requested_close_providers,
            incoming_counts,
            active_counts,
            batch_result.get("provider_course_counts") if isinstance(batch_result.get("provider_course_counts"), dict) else {},
            min_ratio=safe_float(os.getenv("STAGING_CLOSE_MIN_RATIO"), 0.65),
            max_absolute_drop=safe_int(os.getenv("STAGING_CLOSE_MAX_ABSOLUTE_DROP"), 2000),
            ratio_baseline=safe_int(os.getenv("STAGING_CLOSE_RATIO_BASELINE"), 20),
        )
        close_blocked.update(close_decision.blocked_providers)
        close_providers = list(close_decision.allowed_providers)
        if activation_full_apply and close_blocked:
            raise RuntimeError(
                "reviewed full-batch apply failed lifecycle close safety"
            )
        upload_snapshots(primary_conn, batch_id, branches, valid_courses)
        branch_map = upsert_branches(primary_conn, branches)
        branch_keys = {
            (row.get("branch_provider") or row.get("provider"), row.get("branch_code"))
            for row in valid_courses
        }
        branch_map.update(resolve_branch_ids(primary_conn, branch_keys))
        stats.inserted, stats.updated = upsert_courses(primary_conn, valid_courses, branch_map)
        stats.closed = close_missing_courses(primary_conn, batch_id, close_providers, valid_courses)
        with primary_conn.cursor() as lifecycle_cursor:
            lifecycle_result = apply_ended_course_lifecycle(
                grace_days=7,
                cursor=lifecycle_cursor,
            )
        stats.ended_closed = lifecycle_result["closed"]
        stats.ended_deactivated = lifecycle_result["deactivated"]
        # Any skipped validation row or blocked lifecycle closure means the
        # batch was not fully applied, even when all valid upserts succeeded.
        apply_partial = partial_batch or bool(errors) or bool(close_blocked)
        apply_status = "PARTIAL_SUCCESS" if apply_partial else "SUCCESS"
        result = {
            "batch_id": batch_id,
            "dry_run": args.dry_run,
            "providers": providers,
            "apply_scope_provider": requested_provider,
            "promotion_provider": promotion_provider,
            "staging_fingerprint": staging_fingerprint,
            "batch_status": batch_status,
            "scheduled_owners": list(ownership.requested_owners),
            "successful_owners": list(ownership.successful_owners),
            "failed_owners": list(ownership.failed_owners),
            "successful_course_providers": list(
                ownership.successful_course_providers
            ),
            "providers_completed": len(ownership.successful_owners),
            "providers_failed": failed_provider_count,
            "partial_batch": partial_batch,
            "excluded_failed_branches": excluded_failed_branches,
            "excluded_failed_courses": excluded_failed_courses,
            "collection_complete": completion_ok,
            "collection_completion_reason": completion_reason,
            "close_missing_enabled": close_missing_enabled,
            "close_requested_providers": requested_close_providers,
            "closed_providers": close_providers,
            "close_blocked": close_blocked,
            "incoming_provider_counts": incoming_counts,
            "active_provider_counts": active_counts,
            "branches": stats.branches,
            "courses": stats.courses,
            "valid_courses": len(valid_courses),
            "invalid_courses": stats.errors,
            "inserted": stats.inserted,
            "updated": stats.updated,
            "closed": stats.closed,
            "ended_closed": stats.ended_closed,
            "ended_deactivated": stats.ended_deactivated,
            "status": "DRY_RUN" if args.dry_run else apply_status,
        }
        if args.dry_run:
            primary_conn.rollback()
            persist_dry_run_log(primary_config, batch_id, "DRY_RUN", stats, result)
        else:
            if promotion_provider:
                validate_provider_promotion_contract(
                    provider=promotion_provider,
                    batch_metadata=batch_metadata,
                    ownership=ownership,
                    branches=branches,
                    courses=courses,
                    errors=errors,
                    staging_fingerprint=staging_fingerprint,
                    expected_staging_fingerprint=expected_staging_fingerprint,
                )
            write_apply_log(
                primary_conn,
                batch_id,
                False,
                apply_status,
                stats,
                result,
            )
            primary_conn.commit()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        primary_conn.rollback()
        print(json.dumps({"batch_id": batch_id, "status": "FAILED", "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    finally:
        staging_conn.close()
        primary_conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
