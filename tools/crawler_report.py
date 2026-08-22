from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any, Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from DB.db_utils import get_db_cursor


QUALITY_FIELDS = {
    "title": "title IS NOT NULL AND btrim(title) <> ''",
    "branch": "branch_id IS NOT NULL",
    "raw_url": "raw_url IS NOT NULL AND btrim(raw_url) <> ''",
    "description": "description IS NOT NULL AND btrim(description) <> ''",
    "image": "image_url IS NOT NULL AND btrim(image_url) <> ''",
    "schedule_raw": "schedule_raw IS NOT NULL AND btrim(schedule_raw) <> ''",
    "schedule_days": "COALESCE(array_length(schedule_days, 1), 0) > 0",
    "schedule_time": "schedule_time_start IS NOT NULL",
    "target_age_group": "target_age_group IS NOT NULL",
    "fee": "fee IS NOT NULL",
    "status": "status IS NOT NULL AND btrim(status) <> ''",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def fetch_provider_snapshot(
    provider: str,
    since_iso: Optional[str] = None,
    *,
    course_providers: Optional[list[str]] = None,
) -> dict[str, Any]:
    normalized_course_providers = list(
        dict.fromkeys(
            str(value or "").strip().upper()
            for value in (course_providers or [])
            if str(value or "").strip()
        )
    )
    if normalized_course_providers:
        where = ["provider = ANY(%s)"]
        params: list[Any] = [normalized_course_providers]
    else:
        where = ["provider = %s"]
        params = [provider]
    if since_iso:
        where.append("updated_at >= %s")
        params.append(since_iso)
    where_sql = " AND ".join(where)

    select_parts = [
        "COUNT(*) AS total",
        "COUNT(*) FILTER (WHERE is_active IS TRUE) AS active_total",
        "COUNT(*) FILTER (WHERE is_active IS FALSE) AS inactive_total",
        "COUNT(*) FILTER (WHERE created_at >= %s) AS created_since",
        "COUNT(*) FILTER (WHERE updated_at >= %s) AS updated_since",
    ]
    since_for_counts = since_iso or "1970-01-01T00:00:00+00:00"
    count_params = [since_for_counts, since_for_counts, *params]

    for key, condition in QUALITY_FIELDS.items():
        select_parts.append(f"COUNT(*) FILTER (WHERE {condition}) AS {key}_count")

    with get_db_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT {", ".join(select_parts)}
            FROM courses
            WHERE {where_sql}
            """,
            count_params,
        )
        row = dict(cursor.fetchone())

        total = int(row.get("total") or 0)
        quality = {}
        missing = {}
        for key in QUALITY_FIELDS:
            count = int(row.get(f"{key}_count") or 0)
            quality[key] = {
                "count": count,
                "rate": round((count / total * 100), 1) if total else 0.0,
            }
            missing[key] = max(total - count, 0)

        cursor.execute(
            f"""
            SELECT title, branch_id, raw_url, schedule_raw, target_age_group, image_url, description
            FROM courses
            WHERE {where_sql}
              AND (
                image_url IS NULL OR btrim(image_url) = ''
                OR description IS NULL OR btrim(description) = ''
                OR target_age_group IS NULL
                OR schedule_raw IS NULL OR btrim(schedule_raw) = ''
              )
            ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST
            LIMIT 10
            """,
            params,
        )
        weak_samples = [dict(sample) for sample in cursor.fetchall()]

    return {
        "provider": provider,
        "course_providers": normalized_course_providers or [provider],
        "total": total,
        "active_total": int(row.get("active_total") or 0),
        "inactive_total": int(row.get("inactive_total") or 0),
        "created_since": int(row.get("created_since") or 0),
        "updated_since": int(row.get("updated_since") or 0),
        "quality": quality,
        "missing": missing,
        "weak_samples": weak_samples,
    }


def build_provider_report(
    provider: str,
    started_at: str,
    finished_at: str,
    success: bool,
    exit_code: Optional[int],
    elapsed_seconds: float,
    limit: Optional[int],
) -> dict[str, Any]:
    snapshot = fetch_provider_snapshot(provider, since_iso=started_at)
    return {
        "provider": provider,
        "started_at": started_at,
        "finished_at": finished_at,
        "success": success,
        "exit_code": exit_code,
        "elapsed_seconds": round(elapsed_seconds, 2),
        "limit": limit,
        **snapshot,
    }


def summarize_cycle_report(cycle_report: dict[str, Any]) -> dict[str, Any]:
    providers = cycle_report.get("providers", [])
    total_created = sum(int(provider.get("created_since") or 0) for provider in providers)
    total_updated = sum(int(provider.get("updated_since") or 0) for provider in providers)
    total_active = sum(int(provider.get("active_total") or 0) for provider in providers)
    success_count = sum(1 for provider in providers if provider.get("success"))

    required_rates = []
    for provider in providers:
        quality = provider.get("quality", {})
        for field in ("branch", "raw_url", "description", "image", "schedule_raw", "target_age_group"):
            if field in quality:
                required_rates.append(float(quality[field].get("rate") or 0))

    avg_quality = round(sum(required_rates) / len(required_rates), 1) if required_rates else 0.0
    return {
        "provider_success": f"{success_count}/{len(providers)}",
        "total_created_since_run": total_created,
        "total_updated_since_run": total_updated,
        "total_active_courses": total_active,
        "average_required_quality_rate": avg_quality,
    }


def replace_cycle_report(report_path: str, cycle_report: dict[str, Any]) -> str:
    """Atomically replace a previously allocated cycle report in place."""

    report_dir = os.path.dirname(os.path.abspath(report_path))
    os.makedirs(report_dir, exist_ok=True)
    cycle_report["summary"] = summarize_cycle_report(cycle_report)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=report_dir,
            prefix=".crawler-report-",
            suffix=".tmp",
            delete=False,
        ) as report_file:
            json.dump(cycle_report, report_file, ensure_ascii=False, indent=2, default=str)
            report_file.flush()
            os.fsync(report_file.fileno())
            temporary_path = report_file.name
        os.replace(temporary_path, report_path)
    finally:
        if temporary_path:
            try:
                os.remove(temporary_path)
            except FileNotFoundError:
                pass
    return report_path


def write_cycle_report(cycle_report: dict[str, Any], report_dir: Optional[str] = None) -> str:
    report_dir = report_dir or os.path.join(PROJECT_ROOT, "logs", "crawler_reports")
    os.makedirs(report_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = os.path.join(report_dir, f"crawler_report_{timestamp}_{os.getpid()}.json")
    return replace_cycle_report(path, cycle_report)


def latest_report_path(report_dir: Optional[str] = None) -> Optional[str]:
    report_dir = report_dir or os.path.join(PROJECT_ROOT, "logs", "crawler_reports")
    if not os.path.isdir(report_dir):
        return None
    reports = [
        os.path.join(report_dir, name)
        for name in os.listdir(report_dir)
        if name.startswith("crawler_report_") and name.endswith(".json")
    ]
    return max(reports, key=os.path.getmtime) if reports else None
