from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from psycopg2.extras import Json


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.municipal_chungju_goodedu import (
    CHUNGJU_GOODEDU_INSTITUTION_LOCATIONS,
    CHUNGJU_GOODEDU_PROVIDER,
    CHUNGJU_GOODEDU_REGULAR_INSTITUTION,
    CHUNGJU_GOODEDU_SPECIAL_INSTITUTION,
    chungju_goodedu_location,
)
from DB.db_utils import get_db_cursor


SOURCE_INSTITUTIONS = (
    CHUNGJU_GOODEDU_REGULAR_INSTITUTION,
    CHUNGJU_GOODEDU_SPECIAL_INSTITUTION,
    *CHUNGJU_GOODEDU_INSTITUTION_LOCATIONS,
)


def load_courses() -> list[dict[str, Any]]:
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT
                c.id::text,
                c.branch_id::text,
                c.provider_course_id,
                c.title,
                c.venue_name,
                c.is_active,
                b.name AS old_branch_name,
                COALESCE(
                    c.raw_fields->'raw_fields'->>'source_institution',
                    c.raw_fields->>'source_institution',
                    ''
                ) AS source_institution
            FROM courses c
            JOIN branches b ON b.id = c.branch_id
            WHERE c.provider = %(provider)s
              AND COALESCE(
                    c.raw_fields->'raw_fields'->>'source_institution',
                    c.raw_fields->>'source_institution',
                    ''
                  ) = ANY(%(institutions)s)
            ORDER BY c.provider_course_id
            """,
            {
                "provider": CHUNGJU_GOODEDU_PROVIDER,
                "institutions": list(SOURCE_INSTITUTIONS),
            },
        )
        return [dict(row) for row in cursor.fetchall()]


def resolve_courses(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    resolved: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for row in rows:
        location = chungju_goodedu_location(
            row.get("source_institution"),
            row.get("title"),
            row.get("venue_name"),
        )
        if not location:
            unresolved.append(row)
            continue
        resolved.append({**row, "location": location})
    return resolved, unresolved


def write_report(
    output_dir: Path,
    resolved: list[dict[str, Any]],
    unresolved: list[dict[str, Any]],
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"chungju_location_split_{stamp}.csv"
    fieldnames = [
        "provider_course_id",
        "title",
        "source_institution",
        "venue_name",
        "old_branch_name",
        "new_branch_name",
        "address",
        "is_active",
        "status",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in resolved:
            location = row["location"]
            writer.writerow(
                {
                    "provider_course_id": row["provider_course_id"],
                    "title": row["title"],
                    "source_institution": row["source_institution"],
                    "venue_name": row["venue_name"],
                    "old_branch_name": row["old_branch_name"],
                    "new_branch_name": location["name"],
                    "address": location["address"],
                    "is_active": row["is_active"],
                    "status": "resolved",
                }
            )
        for row in unresolved:
            writer.writerow(
                {
                    "provider_course_id": row["provider_course_id"],
                    "title": row["title"],
                    "source_institution": row["source_institution"],
                    "venue_name": row["venue_name"],
                    "old_branch_name": row["old_branch_name"],
                    "new_branch_name": "",
                    "address": "",
                    "is_active": row["is_active"],
                    "status": "unresolved",
                }
            )
    return path


def apply_split(rows: list[dict[str, Any]]) -> tuple[int, int, list[str]]:
    branch_ids: dict[str, str] = {}
    old_branch_ids = sorted({row["branch_id"] for row in rows})
    locations = {
        row["location"]["key"]: row["location"]
        for row in rows
    }
    updated_courses = 0
    with get_db_cursor() as cursor:
        for key, location in locations.items():
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
                    'OFFICIAL_CHUNGJU_LOCATION_CATALOG',
                    %(lat)s,
                    %(lon)s,
                    'NAVER_LOCAL_SEARCH_BY_OFFICIAL_ADDRESS',
                    100,
                    TRUE,
                    CURRENT_TIMESTAMP,
                    %(source_url)s,
                    '충청북도',
                    '충주시',
                    %(basic_info)s
                )
                ON CONFLICT (provider, branch_code)
                DO UPDATE SET
                    name = EXCLUDED.name,
                    address = EXCLUDED.address,
                    website_url = EXCLUDED.website_url,
                    address_source = EXCLUDED.address_source,
                    lat = EXCLUDED.lat,
                    lon = EXCLUDED.lon,
                    coordinate_source = EXCLUDED.coordinate_source,
                    location_confidence = EXCLUDED.location_confidence,
                    location_verified = EXCLUDED.location_verified,
                    location_checked_at = EXCLUDED.location_checked_at,
                    location_query = EXCLUDED.location_query,
                    region_sido = EXCLUDED.region_sido,
                    region_sigungu = EXCLUDED.region_sigungu,
                    basic_info = COALESCE(branches.basic_info, '{}'::jsonb)
                        || EXCLUDED.basic_info,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING id::text
                """,
                {
                    "provider": CHUNGJU_GOODEDU_PROVIDER,
                    "branch_code": location["branch_code"],
                    "name": location["name"],
                    "address": location["address"],
                    "source_url": location["source_url"],
                    "lat": location["lat"],
                    "lon": location["lon"],
                    "basic_info": Json(
                        {
                            "location_catalog": {
                                "key": key,
                                "source_url": location["source_url"],
                            }
                        }
                    ),
                },
            )
            branch_ids[key] = str(cursor.fetchone()["id"])

        for row in rows:
            location = row["location"]
            cursor.execute(
                """
                UPDATE courses
                SET branch_id = %(branch_id)s,
                    venue_address = %(address)s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %(course_id)s
                  AND provider = %(provider)s
                """,
                {
                    "branch_id": branch_ids[location["key"]],
                    "address": location["address"],
                    "course_id": row["id"],
                    "provider": CHUNGJU_GOODEDU_PROVIDER,
                },
            )
            updated_courses += cursor.rowcount

        cursor.execute(
            """
            DELETE FROM branches b
            WHERE b.provider = %(provider)s
              AND b.id = ANY(%(old_branch_ids)s::uuid[])
              AND NOT EXISTS (
                    SELECT 1
                    FROM courses c
                    WHERE c.branch_id = b.id
                  )
            RETURNING b.name
            """,
            {
                "provider": CHUNGJU_GOODEDU_PROVIDER,
                "old_branch_ids": old_branch_ids,
            },
        )
        deleted_names = [str(row["name"]) for row in cursor.fetchall()]
    return len(branch_ids), updated_courses, deleted_names


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Split Chungju GoodEdu aggregate/room branches into verified "
            "physical facilities."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist the split. The default is a dry-run.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "logs" / "branch_address_backfill",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    courses = load_courses()
    resolved, unresolved = resolve_courses(courses)
    report_path = write_report(args.output_dir, resolved, unresolved)
    counts = Counter(row["location"]["key"] for row in resolved)
    print(
        f"courses={len(courses)} resolved={len(resolved)} "
        f"unresolved={len(unresolved)} apply={args.apply}"
    )
    print(f"locations={dict(counts)}")
    print(f"report={report_path}")
    if unresolved:
        for row in unresolved[:20]:
            print(
                "unresolved "
                f"id={row['provider_course_id']} "
                f"title={row['title']!r} venue={row['venue_name']!r}"
            )
        return 1
    if not args.apply:
        return 0

    branch_count, course_count, deleted_names = apply_split(resolved)
    print(
        f"upserted_branches={branch_count} "
        f"updated_courses={course_count} "
        f"deleted_empty_branches={len(deleted_names)}"
    )
    if deleted_names:
        print(f"deleted_branch_names={deleted_names}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
