import hashlib
import json
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Optional

from DB.db_utils import get_db_cursor


SEOUL_TIMEZONE = timezone(timedelta(hours=9))

# Only these statuses claim that the ordinary registration window is still
# open.  WAITING is intentionally excluded: a source can keep a wait-list open
# after its normal application period, and we do not currently store a
# separate wait-list closing date.
APPLICATION_OPEN_STATUSES = frozenset({"OPEN", "DEADLINE"})

HASH_FIELDS = (
    "title",
    "instructor",
    "target",
    "category_raw",
    "fee",
    "material_fee",
    "sessions",
    "schedule_raw",
    "schedule_dates",
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
    "status",
    "raw_url",
    "description",
    "image_url",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def enrich_course_lifecycle(course_data: Dict[str, Any]) -> Dict[str, Any]:
    if is_course_closed_by_date(course_data):
        course_data["status"] = "CLOSED"
        course_data["reservation_available"] = False
    payload = {field: _normalize(course_data.get(field)) for field in HASH_FIELDS}
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    course_data["content_hash"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return course_data


def _as_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d"):
            try:
                return datetime.strptime(text[:10], fmt).date()
            except ValueError:
                continue
    return None


def _course_value(course_data: Any, field: str) -> Any:
    if isinstance(course_data, Mapping):
        return course_data.get(field)
    return getattr(course_data, field, None)


def is_course_period_ended(course_data: Any, reference_date: Optional[date] = None) -> bool:
    """Return True when the education end date is before today.

    Courses ending today are still useful to users, so they are kept until the
    next day. Rows without an end_date are not filtered here because many public
    sources do not expose a reliable period.
    """
    end_date = _as_date(_course_value(course_data, "end_date"))
    if end_date is None:
        return False
    today = reference_date or datetime.now(SEOUL_TIMEZONE).date()
    return end_date < today


def is_application_period_ended(
    course_data: Any,
    reference_date: Optional[date] = None,
) -> bool:
    """Return True when a claimed open registration window has expired.

    Date-only application periods remain open through ``apply_end`` in Seoul
    time and close on the following day.  WAITING and SCHEDULED are not
    inferred from ``apply_end`` because their source semantics differ from the
    ordinary OPEN/DEADLINE registration window.
    """
    status = str(_course_value(course_data, "status") or "").strip().upper()
    if status not in APPLICATION_OPEN_STATUSES:
        return False
    apply_end = _as_date(_course_value(course_data, "apply_end"))
    if apply_end is None:
        return False
    today = reference_date or datetime.now(SEOUL_TIMEZONE).date()
    return apply_end < today


def is_course_closed_by_date(
    course_data: Any,
    reference_date: Optional[date] = None,
) -> bool:
    """Return whether the effective public status must be CLOSED by date."""
    return is_course_period_ended(
        course_data,
        reference_date=reference_date,
    ) or is_application_period_ended(
        course_data,
        reference_date=reference_date,
    )


def effective_course_status(
    course_data: Any,
    reference_date: Optional[date] = None,
) -> Optional[str]:
    """Return a date-safe status for crawler, API, notification, and SEO use."""
    raw_status = _course_value(course_data, "status")
    if is_course_closed_by_date(course_data, reference_date=reference_date):
        return "CLOSED"
    if raw_status is None:
        return None
    return str(raw_status).strip().upper() or None


def should_skip_expired_course(course_data: Dict[str, Any]) -> bool:
    return is_course_period_ended(course_data)


def mark_stale_courses(
    provider: str,
    cutoff: Optional[datetime] = None,
    branch_id: Optional[str] = None,
    cursor: Any = None,
    source_endpoint: Optional[str] = None,
) -> int:
    cutoff = cutoff or utc_now()
    params = {
        "provider": provider,
        "cutoff": cutoff,
        "branch_id": branch_id,
        "source_endpoint": source_endpoint,
    }
    branch_filter = "AND branch_id = %(branch_id)s" if branch_id else ""
    source_filter = "AND source_endpoint = %(source_endpoint)s" if source_endpoint else ""

    def execute(active_cursor: Any) -> int:
        active_cursor.execute(
            f"""
            UPDATE courses
            SET is_active = FALSE,
                status = 'CLOSED',
                removed_at = COALESCE(removed_at, CURRENT_TIMESTAMP),
                updated_at = CURRENT_TIMESTAMP
            WHERE provider = %(provider)s
              AND is_active = TRUE
              AND last_seen_at < %(cutoff)s
              {branch_filter}
              {source_filter}
            """,
            params,
        )
        return active_cursor.rowcount

    if cursor is not None:
        return execute(cursor)
    with get_db_cursor() as managed_cursor:
        return execute(managed_cursor)


def mark_ended_courses_closed(
    provider: Optional[str] = None,
    cursor: Any = None,
) -> int:
    """Close active courses after either their course or application period."""
    provider_filter = "AND provider = %(provider)s" if provider else ""
    params = {"provider": provider}

    def execute(active_cursor: Any) -> int:
        active_cursor.execute(
            f"""
            UPDATE courses
            SET status = 'CLOSED',
                reservation_available = FALSE,
                updated_at = CURRENT_TIMESTAMP
            WHERE is_active IS TRUE
              AND (
                    (
                        end_date IS NOT NULL
                        AND end_date < (NOW() AT TIME ZONE 'Asia/Seoul')::date
                    )
                    OR (
                        status IN ('OPEN', 'DEADLINE')
                        AND apply_end IS NOT NULL
                        AND apply_end < (NOW() AT TIME ZONE 'Asia/Seoul')::date
                    )
              )
              AND (
                    status IS DISTINCT FROM 'CLOSED'
                    OR reservation_available IS DISTINCT FROM FALSE
              )
              {provider_filter}
            """,
            params,
        )
        return active_cursor.rowcount

    if cursor is not None:
        return execute(cursor)
    with get_db_cursor() as managed_cursor:
        return execute(managed_cursor)


def mark_ended_courses_inactive(
    grace_days: int = 7,
    provider: Optional[str] = None,
    cursor: Any = None,
) -> int:
    """Deactivate courses whose education period ended at least grace_days ago."""
    grace_days = max(0, int(grace_days))
    provider_filter = "AND provider = %(provider)s" if provider else ""
    params = {"grace_days": grace_days, "provider": provider}

    def execute(active_cursor: Any) -> int:
        active_cursor.execute(
            f"""
            UPDATE courses
            SET is_active = FALSE,
                status = 'CLOSED',
                removed_at = COALESCE(removed_at, CURRENT_TIMESTAMP),
                updated_at = CURRENT_TIMESTAMP
            WHERE is_active IS TRUE
              AND end_date IS NOT NULL
              AND end_date <= ((NOW() AT TIME ZONE 'Asia/Seoul')::date - %(grace_days)s::int)
              {provider_filter}
            """,
            params,
        )
        return active_cursor.rowcount

    if cursor is not None:
        return execute(cursor)
    with get_db_cursor() as managed_cursor:
        return execute(managed_cursor)


def apply_ended_course_lifecycle(
    grace_days: int = 7,
    provider: Optional[str] = None,
    cursor: Any = None,
) -> Dict[str, int]:
    """Close ended registration/course periods, then deactivate old courses."""

    def execute(active_cursor: Any) -> Dict[str, int]:
        return {
            "closed": mark_ended_courses_closed(provider=provider, cursor=active_cursor),
            "deactivated": mark_ended_courses_inactive(
                grace_days=grace_days,
                provider=provider,
                cursor=active_cursor,
            ),
        }

    if cursor is not None:
        return execute(cursor)
    with get_db_cursor() as managed_cursor:
        return execute(managed_cursor)
