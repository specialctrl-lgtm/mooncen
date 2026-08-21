from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup
from psycopg2.extras import Json
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_MunicipalYaml import (
    UIWANG_RESERVE_PROVIDER,
    uiwang_course_location,
    uiwang_detail_location,
    uiwang_physical_location,
)
from DB.db_utils import get_db_cursor
from tools.maintenance.backfill_missing_branch_addresses import (
    clean_text,
    is_usable_address,
    road_address_key,
)


@dataclass(frozen=True)
class CourseLocation:
    course_id: str
    branch_id: str
    current_branch_name: str
    title: str
    venue_name: str
    raw_url: str
    is_active: bool


@dataclass(frozen=True)
class DetailResolution:
    course: CourseLocation
    location_text: str
    target_name: str
    address: str
    venue_address: str


_THREAD_LOCAL = threading.local()


def stable_branch_code(provider: Any, name: Any) -> str:
    provider_text = clean_text(provider).upper()
    name_text = clean_text(name)
    digest = hashlib.sha1(
        "|".join((provider_text, name_text, "")).encode("utf-8")
    ).hexdigest()[:12].upper()
    slug = re.sub(r"[^A-Za-z0-9가-힣]+", "_", name_text).strip("_")[:28]
    return f"{slug}_{digest}"[:50] if slug else f"{provider_text[:32]}_{digest}"[:50]


def facility_name_key(value: Any) -> str:
    text = clean_text(value)
    text = text.replace("주민자치센터", "주민센터")
    return re.sub(r"[^A-Za-z0-9가-힣]", "", text).lower()


def target_names_match(left: Any, right: Any) -> bool:
    left_key = facility_name_key(left)
    right_key = facility_name_key(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True
    suffix = "주민센터"
    return (
        left_key == f"{right_key}{suffix}"
        or right_key == f"{left_key}{suffix}"
    )


def choose_existing_target(
    target_name: str,
    address: str,
    branches: list[dict[str, Any]],
) -> dict[str, Any] | None:
    address_key = road_address_key(address)
    same_address = [
        row
        for row in branches
        if road_address_key(row.get("address")) == address_key
    ]
    exact = [
        row
        for row in same_address
        if facility_name_key(row.get("name")) == facility_name_key(target_name)
    ]
    compatible = [
        row
        for row in same_address
        if target_names_match(row.get("name"), target_name)
    ]
    same_name_missing = [
        row
        for row in branches
        if target_names_match(row.get("name"), target_name)
        and not clean_text(row.get("address"))
    ]
    candidates = exact or compatible or same_name_missing
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda row: (
            road_address_key(row.get("address")) == address_key,
            facility_name_key(row.get("name"))
            == facility_name_key(target_name),
            int(row.get("active_course_count") or 0),
            int(row.get("course_count") or 0),
        ),
    )


def load_missing_courses() -> list[CourseLocation]:
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT
                c.id::text AS course_id,
                b.id::text AS branch_id,
                b.name AS current_branch_name,
                c.title,
                c.venue_name,
                c.raw_url,
                COALESCE(c.is_active, TRUE) AS is_active
            FROM branches b
            JOIN courses c ON c.branch_id = b.id
            WHERE b.provider = %(provider)s
              AND NULLIF(BTRIM(COALESCE(b.address, '')), '') IS NULL
              AND c.raw_url LIKE '%%/eduView.do%%'
            ORDER BY c.is_active DESC, b.name, c.title, c.id
            """,
            {"provider": UIWANG_RESERVE_PROVIDER},
        )
        return [CourseLocation(**dict(row)) for row in cursor.fetchall()]


def _session() -> requests.Session:
    session = getattr(_THREAD_LOCAL, "session", None)
    if session is not None:
        return session
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.4,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
    )
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
            )
        }
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    _THREAD_LOCAL.session = session
    return session


def fetch_location(
    course: CourseLocation,
    timeout: int,
) -> tuple[DetailResolution | None, str]:
    try:
        response = _session().get(course.raw_url, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        return None, f"fetch_failed:{type(exc).__name__}:{status or '-'}"

    soup = BeautifulSoup(response.content, "lxml")
    location_text = uiwang_detail_location(soup)
    physical_branch, physical_address = uiwang_physical_location(
        location_text,
        course.venue_name,
        course.current_branch_name,
    )
    target_name, address, venue_address, _physical = uiwang_course_location(
        course.current_branch_name,
        course.venue_name,
        physical_branch,
        physical_address,
    )
    if not is_usable_address(address):
        reason = (
            "detail_location_missing"
            if not location_text
            else "detail_address_unusable"
        )
        return None, reason
    if not target_name:
        return None, "detail_facility_name_missing"
    return (
        DetailResolution(
            course=course,
            location_text=location_text,
            target_name=target_name,
            address=address,
            venue_address=venue_address,
        ),
        "",
    )


def collect_resolutions(
    courses: list[CourseLocation],
    *,
    workers: int,
    timeout: int,
) -> tuple[list[DetailResolution], list[tuple[CourseLocation, str]]]:
    resolved: list[DetailResolution] = []
    unresolved: list[tuple[CourseLocation, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(fetch_location, course, timeout): course
            for course in courses
        }
        completed = 0
        for future in as_completed(futures):
            course = futures[future]
            try:
                resolution, reason = future.result()
            except Exception as exc:
                resolution = None
                reason = f"unexpected:{type(exc).__name__}"
            if resolution:
                resolved.append(resolution)
            else:
                unresolved.append((course, reason))
            completed += 1
            if completed % 50 == 0 or completed == len(courses):
                print(
                    f"fetched={completed}/{len(courses)} "
                    f"resolved={len(resolved)} unresolved={len(unresolved)}"
                )
    resolved.sort(
        key=lambda row: (
            row.target_name,
            road_address_key(row.address),
            row.course.course_id,
        )
    )
    unresolved.sort(
        key=lambda item: (
            item[1],
            item[0].current_branch_name,
            item[0].course_id,
        )
    )
    return resolved, unresolved


def write_reports(
    output_dir: Path,
    resolutions: list[DetailResolution],
    unresolved: list[tuple[CourseLocation, str]],
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    resolved_path = output_dir / f"uiwang_detail_resolved_{stamp}.csv"
    unresolved_path = output_dir / f"uiwang_detail_unresolved_{stamp}.csv"
    fields = [
        "provider",
        "course_id",
        "branch_id",
        "current_branch_name",
        "title",
        "venue_name",
        "raw_url",
        "location_text",
        "target_name",
        "address",
        "venue_address",
        "is_active",
    ]
    with resolved_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in resolutions:
            writer.writerow(
                {
                    "provider": UIWANG_RESERVE_PROVIDER,
                    "course_id": item.course.course_id,
                    "branch_id": item.course.branch_id,
                    "current_branch_name": item.course.current_branch_name,
                    "title": item.course.title,
                    "venue_name": item.course.venue_name,
                    "raw_url": item.course.raw_url,
                    "location_text": item.location_text,
                    "target_name": item.target_name,
                    "address": item.address,
                    "venue_address": item.venue_address,
                    "is_active": item.course.is_active,
                }
            )

    with unresolved_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "provider",
                "course_id",
                "branch_id",
                "current_branch_name",
                "title",
                "venue_name",
                "raw_url",
                "is_active",
                "reason",
            ],
        )
        writer.writeheader()
        for course, reason in unresolved:
            writer.writerow(
                {
                    "provider": UIWANG_RESERVE_PROVIDER,
                    "course_id": course.course_id,
                    "branch_id": course.branch_id,
                    "current_branch_name": course.current_branch_name,
                    "title": course.title,
                    "venue_name": course.venue_name,
                    "raw_url": course.raw_url,
                    "is_active": course.is_active,
                    "reason": reason,
                }
            )
    return resolved_path, unresolved_path


def _load_current_courses(
    course_ids: list[str],
) -> dict[str, CourseLocation]:
    if not course_ids:
        return {}
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT
                c.id::text AS course_id,
                b.id::text AS branch_id,
                b.name AS current_branch_name,
                c.title,
                c.venue_name,
                c.raw_url,
                COALESCE(c.is_active, TRUE) AS is_active,
                b.provider,
                b.address
            FROM courses c
            JOIN branches b ON b.id = c.branch_id
            WHERE c.id = ANY(%(course_ids)s::uuid[])
            """,
            {"course_ids": course_ids},
        )
        result: dict[str, CourseLocation] = {}
        for row in cursor.fetchall():
            if row["provider"] != UIWANG_RESERVE_PROVIDER:
                continue
            if clean_text(row["address"]):
                continue
            result[row["course_id"]] = CourseLocation(
                course_id=row["course_id"],
                branch_id=row["branch_id"],
                current_branch_name=row["current_branch_name"],
                title=row["title"],
                venue_name=row["venue_name"],
                raw_url=row["raw_url"],
                is_active=row["is_active"],
            )
        return result


def load_audited_report(
    report_path: Path,
) -> tuple[list[DetailResolution], list[str]]:
    with report_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    course_ids = [clean_text(row.get("course_id")) for row in rows]
    current = _load_current_courses(course_ids)
    resolutions: list[DetailResolution] = []
    errors: list[str] = []
    seen: set[str] = set()
    for index, row in enumerate(rows, start=2):
        course_id = clean_text(row.get("course_id"))
        if not course_id or course_id in seen:
            errors.append(f"row {index}: missing or duplicate course_id")
            continue
        seen.add(course_id)
        course = current.get(course_id)
        if not course:
            errors.append(
                f"row {index}: course is missing or no longer on an addressless branch"
            )
            continue
        if clean_text(row.get("provider")) != UIWANG_RESERVE_PROVIDER:
            errors.append(f"row {index}: provider mismatch")
            continue
        if clean_text(row.get("raw_url")) != clean_text(course.raw_url):
            errors.append(f"row {index}: raw_url changed")
            continue
        location_text = clean_text(row.get("location_text"))
        physical_branch, physical_address = uiwang_physical_location(
            location_text,
            course.venue_name,
            course.current_branch_name,
        )
        target_name, address, venue_address, _physical = uiwang_course_location(
            course.current_branch_name,
            course.venue_name,
            physical_branch,
            physical_address,
        )
        if (
            target_name != clean_text(row.get("target_name"))
            or road_address_key(address)
            != road_address_key(row.get("address"))
            or road_address_key(venue_address)
            != road_address_key(row.get("venue_address"))
            or not is_usable_address(address)
        ):
            errors.append(f"row {index}: parsed location no longer matches report")
            continue
        resolutions.append(
            DetailResolution(
                course=course,
                location_text=location_text,
                target_name=target_name,
                address=address,
                venue_address=venue_address,
            )
        )
    return resolutions, errors


def load_provider_branches() -> list[dict[str, Any]]:
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT
                b.id::text,
                b.branch_code,
                b.name,
                b.address,
                b.lat,
                b.lon,
                b.coordinate_source,
                COUNT(c.id) AS course_count,
                COUNT(c.id) FILTER (WHERE c.is_active) AS active_course_count
            FROM branches b
            LEFT JOIN courses c ON c.branch_id = b.id
            WHERE b.provider = %(provider)s
            GROUP BY b.id
            ORDER BY b.name, b.id
            """,
            {"provider": UIWANG_RESERVE_PROVIDER},
        )
        return [dict(row) for row in cursor.fetchall()]


def coordinate_candidates(
    branches: list[dict[str, Any]],
    address: str,
) -> tuple[float | None, float | None, str | None]:
    address_key = road_address_key(address)
    candidates = [
        row
        for row in branches
        if road_address_key(row.get("address")) == address_key
        and row.get("lat") is not None
        and row.get("lon") is not None
    ]
    if not candidates:
        return None, None, None
    selected = max(
        candidates,
        key=lambda row: (
            int(row.get("active_course_count") or 0),
            int(row.get("course_count") or 0),
        ),
    )
    return (
        float(selected["lat"]),
        float(selected["lon"]),
        clean_text(selected.get("coordinate_source")) or "SAME_ADDRESS_BRANCH",
    )


def ensure_target(
    cursor: Any,
    target_name: str,
    address: str,
    sample_url: str,
    branches: list[dict[str, Any]],
) -> str:
    target = choose_existing_target(target_name, address, branches)
    lat, lon, coordinate_source = coordinate_candidates(branches, address)
    metadata = Json(
        {
            "uiwang_detail_location": {
                "source_url": sample_url,
                "checked_at": datetime.now().astimezone().isoformat(),
            }
        }
    )
    if target:
        cursor.execute(
            """
            UPDATE branches
            SET name = %(name)s,
                address = %(address)s,
                address_source = 'OFFICIAL_UIWANG_RESERVATION_DETAIL',
                lat = COALESCE(lat, %(lat)s),
                lon = COALESCE(lon, %(lon)s),
                coordinate_source = COALESCE(
                    coordinate_source,
                    %(coordinate_source)s
                ),
                location_confidence = 100,
                location_verified = TRUE,
                location_checked_at = CURRENT_TIMESTAMP,
                location_query = %(source_url)s,
                region_sido = COALESCE(NULLIF(region_sido, ''), '경기도'),
                region_sigungu = COALESCE(NULLIF(region_sigungu, ''), '의왕시'),
                basic_info = COALESCE(basic_info, '{}'::jsonb) || %(metadata)s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %(id)s::uuid
            RETURNING id::text
            """,
            {
                "id": target["id"],
                "name": target_name,
                "address": address,
                "lat": lat,
                "lon": lon,
                "coordinate_source": coordinate_source,
                "source_url": sample_url,
                "metadata": metadata,
            },
        )
        return str(cursor.fetchone()["id"])

    branch_code = stable_branch_code(UIWANG_RESERVE_PROVIDER, target_name)
    cursor.execute(
        """
        INSERT INTO branches (
            provider,
            branch_code,
            name,
            address,
            website_url,
            address_source,
            lat,
            lon,
            coordinate_source,
            location_confidence,
            location_verified,
            location_checked_at,
            location_query,
            region_sido,
            region_sigungu,
            basic_info
        )
        VALUES (
            %(provider)s,
            %(branch_code)s,
            %(name)s,
            %(address)s,
            %(source_url)s,
            'OFFICIAL_UIWANG_RESERVATION_DETAIL',
            %(lat)s,
            %(lon)s,
            %(coordinate_source)s,
            100,
            TRUE,
            CURRENT_TIMESTAMP,
            %(source_url)s,
            '경기도',
            '의왕시',
            %(metadata)s
        )
        ON CONFLICT (provider, branch_code)
        DO UPDATE SET
            name = EXCLUDED.name,
            address = EXCLUDED.address,
            address_source = EXCLUDED.address_source,
            lat = COALESCE(branches.lat, EXCLUDED.lat),
            lon = COALESCE(branches.lon, EXCLUDED.lon),
            coordinate_source = COALESCE(
                branches.coordinate_source,
                EXCLUDED.coordinate_source
            ),
            location_confidence = 100,
            location_verified = TRUE,
            location_checked_at = CURRENT_TIMESTAMP,
            location_query = EXCLUDED.location_query,
            region_sido = COALESCE(NULLIF(branches.region_sido, ''), '경기도'),
            region_sigungu = COALESCE(NULLIF(branches.region_sigungu, ''), '의왕시'),
            basic_info = COALESCE(branches.basic_info, '{}'::jsonb)
                || EXCLUDED.basic_info,
            updated_at = CURRENT_TIMESTAMP
        RETURNING id::text
        """,
        {
            "provider": UIWANG_RESERVE_PROVIDER,
            "branch_code": branch_code,
            "name": target_name,
            "address": address,
            "source_url": sample_url,
            "lat": lat,
            "lon": lon,
            "coordinate_source": coordinate_source,
            "metadata": metadata,
        },
    )
    return str(cursor.fetchone()["id"])


def apply_resolutions(
    resolutions: list[DetailResolution],
) -> tuple[int, int, int]:
    branches = load_provider_branches()
    grouped: dict[tuple[str, str], list[DetailResolution]] = {}
    for item in resolutions:
        key = facility_name_key(item.target_name), road_address_key(item.address)
        grouped.setdefault(key, []).append(item)

    target_ids: set[str] = set()
    source_branch_ids = sorted(
        {item.course.branch_id for item in resolutions}
    )
    updated_courses = 0
    with get_db_cursor() as cursor:
        for items in grouped.values():
            sample = items[0]
            target_id = ensure_target(
                cursor,
                sample.target_name,
                sample.address,
                sample.course.raw_url,
                branches,
            )
            target_ids.add(target_id)
            for item in items:
                cursor.execute(
                    """
                    UPDATE courses
                    SET branch_id = %(target_id)s::uuid,
                        venue_address = NULLIF(%(venue_address)s, ''),
                        raw_fields = COALESCE(raw_fields, '{}'::jsonb)
                            || %(location_metadata)s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %(course_id)s::uuid
                    """,
                    {
                        "target_id": target_id,
                        "venue_address": item.venue_address,
                        "location_metadata": Json(
                            {
                                "location_text": item.location_text,
                                "location_address_source": (
                                    "OFFICIAL_UIWANG_RESERVATION_DETAIL"
                                ),
                            }
                        ),
                        "course_id": item.course.course_id,
                    },
                )
                updated_courses += cursor.rowcount

        cursor.execute(
            """
            DELETE FROM branches b
            WHERE b.id = ANY(%(source_branch_ids)s::uuid[])
              AND NOT EXISTS (
                    SELECT 1 FROM courses c WHERE c.branch_id = b.id
                  )
            RETURNING b.id
            """,
            {"source_branch_ids": source_branch_ids},
        )
        deleted_sources = cursor.rowcount
    return len(target_ids), updated_courses, deleted_sources


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Recover Uiwang branch addresses from each official reservation "
            "detail page. Network collection is dry-run unless --apply is used."
        )
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--apply-report",
        type=Path,
        help="Validate and apply a previously generated resolved CSV without refetching.",
    )
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "logs" / "branch_address_backfill",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.apply_report:
        resolutions, errors = load_audited_report(args.apply_report)
        if errors:
            print(f"report_validation_errors={len(errors)}")
            for error in errors[:30]:
                print(error)
            return 2
        print(
            f"validated_report={args.apply_report.resolve()} "
            f"resolutions={len(resolutions)}"
        )
        targets, courses, deleted = apply_resolutions(resolutions)
        print(
            f"updated_targets={targets} updated_courses={courses} "
            f"deleted_sources={deleted}"
        )
        return 0

    courses = load_missing_courses()
    print(f"candidate_courses={len(courses)}")
    resolutions, unresolved = collect_resolutions(
        courses,
        workers=args.workers,
        timeout=args.timeout,
    )
    resolved_path, unresolved_path = write_reports(
        args.output_dir,
        resolutions,
        unresolved,
    )
    groups = {
        (facility_name_key(item.target_name), road_address_key(item.address))
        for item in resolutions
    }
    print(
        f"resolved_courses={len(resolutions)} target_groups={len(groups)} "
        f"unresolved_courses={len(unresolved)} apply={args.apply}"
    )
    print(f"resolved_report={resolved_path.resolve()}")
    print(f"unresolved_report={unresolved_path.resolve()}")
    if args.apply:
        targets, updated, deleted = apply_resolutions(resolutions)
        print(
            f"updated_targets={targets} updated_courses={updated} "
            f"deleted_sources={deleted}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
