from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from DB.db_utils import get_db_connection
from tools.maintenance.kakao_geocode_branches import (
    configured_locality_parts,
    is_usable_address,
    load_configured_provider_localities,
    normalize_text,
)
from tools.maintenance.propagate_branch_locations import (
    choose_unique_verified_source,
    index_verified_sources,
    location_match_key,
)


CURRENT_MAP_STATUSES = frozenset({"OPEN", "SCHEDULED", "DEADLINE", "WAITING"})


def normalized_name(value: Any) -> str:
    return normalize_text(str(value or ""))


def verified_source_index(
    sources: list[dict[str, Any]],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    return index_verified_sources(sources)


def unique_course_address(row: dict[str, Any]) -> str:
    values = {
        re.sub(r"\s+", " ", str(value or "")).strip()
        for value in row.get("course_addresses") or []
        if str(value or "").strip()
    }
    if len(values) != 1:
        return ""
    value = next(iter(values))
    return value if is_usable_address(value) else ""


def repair_path(
    row: dict[str, Any],
    *,
    configured_localities: dict[str, str],
    verified_sources: dict[tuple[str, str], list[dict[str, Any]]],
) -> str:
    """Classify one missing marker into one non-overlapping safe repair path."""

    if row.get("lat") is not None and row.get("lon") is not None:
        return "coordinates_present"
    if is_usable_address(row.get("address")):
        return "kakao_stored_address"
    if unique_course_address(row):
        return "kakao_unique_course_address"
    stored_locality = " ".join(
        str(row.get(key) or "").strip()
        for key in ("region_sido", "region_sigungu")
        if str(row.get(key) or "").strip()
    )
    if configured_locality_parts(stored_locality) is not None:
        return "kakao_stored_region"
    provider = str(row.get("provider") or "").strip().upper()
    if configured_locality_parts(configured_localities.get(provider)) is not None:
        return "kakao_configured_locality"
    source, reason = choose_unique_verified_source(
        row,
        verified_sources.get(location_match_key(row), []),
    )
    if source is not None:
        return "verified_same_name_copy"
    if reason == "ambiguous_verified_same_name_coordinates":
        return "manual_ambiguous_same_name"
    if reason in {
        "conflicting_verified_source_evidence",
        "conflicting_target_source_evidence",
        "partial_or_existing_target_coordinates",
    }:
        return "manual_conflicting_same_name_evidence"
    return "manual_missing_location_evidence"


def location_visibility_issue(row: dict[str, Any]) -> str:
    if row.get("lat") is None or row.get("lon") is None:
        return "missing_coordinates"
    try:
        lat = float(row["lat"])
        lon = float(row["lon"])
    except (TypeError, ValueError):
        return "invalid_coordinates"
    if not 32.5 <= lat <= 39.5 or not 124.0 <= lon <= 132.5:
        return "outside_kakao_service_area"
    if row.get("location_is_null"):
        return "missing_postgis_location"
    if row.get("location_mismatch"):
        return "postgis_coordinate_mismatch"
    return "visible_by_location"


def summarize(
    rows: list[dict[str, Any]],
    *,
    configured_localities: dict[str, str],
    verified_sources: dict[tuple[str, str], list[dict[str, Any]]],
    geocode_queue_schema_ready: bool,
) -> dict[str, Any]:
    current_rows = [row for row in rows if int(row.get("active_current_courses") or 0) > 0]
    visibility = Counter(location_visibility_issue(row) for row in current_rows)
    blocked = [row for row in current_rows if location_visibility_issue(row) != "visible_by_location"]
    repairs = Counter(
        repair_path(
            row,
            configured_localities=configured_localities,
            verified_sources=verified_sources,
        )
        for row in blocked
        if location_visibility_issue(row) == "missing_coordinates"
    )
    return {
        "schema_version": 1,
        "geocode_queue_schema_ready": geocode_queue_schema_ready,
        "active_course_branches": sum(
            int(row.get("active_courses") or 0) > 0 for row in rows
        ),
        "current_searchable_branches": len(current_rows),
        "map_visible_by_location": visibility["visible_by_location"],
        "map_blocked_by_location": len(blocked),
        "visibility_issues": dict(sorted(visibility.items())),
        "missing_coordinate_repair_paths": dict(sorted(repairs.items())),
        "coordinate_quality": {
            "unverified_with_coordinates": sum(
                row.get("lat") is not None
                and row.get("lon") is not None
                and not bool(row.get("location_verified"))
                for row in current_rows
            ),
            "missing_coordinate_source": sum(
                row.get("lat") is not None
                and row.get("lon") is not None
                and not str(row.get("coordinate_source") or "").strip()
                for row in current_rows
            ),
        },
    }


def fetch_audit_rows(connection) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL statement_timeout = '60s'")
        cursor.execute(
            """
            WITH course_summary AS (
                SELECT c.branch_id,
                       COUNT(*) FILTER (WHERE COALESCE(c.is_active, true)) AS active_courses,
                       COUNT(*) FILTER (
                           WHERE COALESCE(c.is_active, true)
                             AND c.status IN ('OPEN', 'SCHEDULED', 'DEADLINE', 'WAITING')
                       ) AS active_current_courses,
                       ARRAY_REMOVE(
                           ARRAY_AGG(DISTINCT NULLIF(btrim(c.venue_address), ''))
                               FILTER (WHERE COALESCE(c.is_active, true)),
                           NULL
                       ) AS course_addresses
                FROM courses c
                GROUP BY c.branch_id
            )
            SELECT b.id::text, b.provider, b.branch_code, b.name, b.address,
                   b.lat, b.lon, b.location_verified, b.coordinate_source,
                   b.region_sido, b.region_sigungu,
                   cs.active_courses, cs.active_current_courses, cs.course_addresses,
                   b.location IS NULL AS location_is_null,
                   CASE
                       WHEN b.location IS NULL OR b.lat IS NULL OR b.lon IS NULL THEN false
                       ELSE abs(ST_Y(b.location::geometry) - b.lat::double precision) > 0.000001
                         OR abs(ST_X(b.location::geometry) - b.lon::double precision) > 0.000001
                   END AS location_mismatch
            FROM branches b
            JOIN course_summary cs ON cs.branch_id = b.id
            WHERE cs.active_courses > 0
            ORDER BY b.provider, b.name, b.branch_code
            """
        )
        columns = [description.name for description in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        cursor.execute(
            """
            SELECT id::text, provider, branch_code, name, address,
                   lat, lon, location_verified, location_confidence,
                   coordinate_source, region_sido, region_sigungu
            FROM branches
            WHERE lat IS NOT NULL
              AND lon IS NOT NULL
              AND location_verified IS TRUE
            """
        )
        source_columns = [description.name for description in cursor.description]
        sources = [dict(zip(source_columns, row)) for row in cursor.fetchall()]
        cursor.execute(
            """
            SELECT COUNT(*) = 8
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'branches'
              AND column_name IN (
                  'geocode_status', 'geocode_reason_code', 'geocode_attempt_count',
                  'geocode_candidates', 'geocode_next_retry_at', 'geocode_last_error',
                  'geocode_last_attempt_at', 'location_checked_at'
              )
            """
        )
        schema_ready = bool(cursor.fetchone()[0])
    return rows, sources, schema_ready


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only audit of searchable course branches that cannot render on the map."
    )
    parser.add_argument(
        "--details",
        type=int,
        default=0,
        choices=range(0, 501),
        metavar="N",
        help="Include at most N blocked public branch identities (default: summary only).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    connection = get_db_connection()
    try:
        connection.set_session(readonly=True, autocommit=False)
        rows, sources, schema_ready = fetch_audit_rows(connection)
        configured_localities = load_configured_provider_localities()
        source_index = verified_source_index(sources)
        report = summarize(
            rows,
            configured_localities=configured_localities,
            verified_sources=source_index,
            geocode_queue_schema_ready=schema_ready,
        )
        if args.details:
            details = []
            for row in rows:
                issue = location_visibility_issue(row)
                if issue == "visible_by_location":
                    continue
                details.append(
                    {
                        "provider": row.get("provider"),
                        "branch_code": row.get("branch_code"),
                        "name": row.get("name"),
                        "issue": issue,
                        "repair_path": repair_path(
                            row,
                            configured_localities=configured_localities,
                            verified_sources=source_index,
                        ),
                        "active_current_courses": int(
                            row.get("active_current_courses") or 0
                        ),
                    }
                )
                if len(details) >= args.details:
                    break
            report["details"] = details
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0
    finally:
        connection.rollback()
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
