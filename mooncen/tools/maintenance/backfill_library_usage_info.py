from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from psycopg2.extras import Json

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from Crawler.library_usage_info import fetch_library_usage_info
from DB.db_utils import get_db_cursor
from utils import clean_text


FACILITY_USAGE_PATTERNS = [
    "%\ub3c4\uc11c\uad00%",
    "%\uacfc\ud559\uad00%",
    "%\ubc15\ubb3c\uad00%",
    "%\ubbf8\uc220\uad00%",
    "%\ubb38\ud559\uad00%",
    "%\uc218\ubaa9\uc6d0%",
    "%\uc0dd\ud0dc%",
    "%\uc804\uc2dc\uad00%",
    "%\ubb38\ud654\uae30\ubc18\uc2dc\uc124%",
    "%\uccb4\ud5d8%",
]

ROOM_LEVEL_BRANCH_PATTERNS = [
    "%\uac15\uc88c\uc2e4%",
    "%\uac15\uc758\uc2e4%",
    "%\uad50\uc2e4%",
    "%\uc2e4\uc2b5\uc2e4%",
    "%\uc5f0\uc2b5\uc2e4%",
    "%\ub300\ud68c\uc758\uc2e4%",
    "%\uc18c\ud68c\uc758\uc2e4%",
    "%\uc815\ubcf4\ud654\uad50\uc721\uc2e4%",
    "%\uc9c0\ud558%",
    "%\uc0c1\uc2dc%",
    "%\uc2e0\uad00 %",
    "%\uad6c\uad00 %",
    "%\uce35%",
    "%\ud638%",
]

RESERVATION_URL_PATTERN = "(reserve|reservation|lecture|course|sugang|booking|expr|program|yeyak|edu|bbs|board|detail)"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill facility branch usage info from homepage usage guide pages.")
    parser.add_argument("--provider", action="append", help="Provider to process. Can be repeated.")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--timeout", type=int, default=8)
    parser.add_argument("--max-pages", type=int, default=8)
    parser.add_argument("--include-complete", action="store_true", help="Also refresh branches that already have both fields.")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def load_library_branches(providers: list[str] | None, limit: int, include_complete: bool) -> list[dict[str, Any]]:
    provider_filter = ""
    params: dict[str, Any] = {
        "limit": limit,
        "patterns": FACILITY_USAGE_PATTERNS,
        "room_patterns": ROOM_LEVEL_BRANCH_PATTERNS,
        "reservation_url_pattern": RESERVATION_URL_PATTERN,
    }
    if providers:
        provider_filter = "AND b.provider = ANY(%(providers)s)"
        params["providers"] = providers

    missing_filter = ""
    if not include_complete:
        missing_filter = "AND (COALESCE(b.operating_hours, '') = '' OR COALESCE(b.regular_holiday, '') = '' OR COALESCE(b.admission_fee, '') = '')"

    with get_db_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT
                b.id,
                b.provider,
                b.branch_code,
                b.name,
                b.website_url,
                b.operating_hours,
                b.regular_holiday,
                b.admission_fee,
                ARRAY_REMOVE(ARRAY_AGG(DISTINCT c.raw_url) FILTER (WHERE COALESCE(c.raw_url, '') <> ''), NULL) AS raw_urls,
                ARRAY_REMOVE(ARRAY_AGG(DISTINCT c.application_url) FILTER (WHERE COALESCE(c.application_url, '') <> ''), NULL) AS application_urls
            FROM branches b
            LEFT JOIN courses c ON c.branch_id = b.id
            WHERE (
                b.provider = 'CULTURE_FACILITY'
                OR b.facility_source IS NOT NULL
                OR (b.name ILIKE ANY(%(patterns)s) AND NOT b.name ILIKE ANY(%(room_patterns)s))
                OR b.facility_category ILIKE ANY(%(patterns)s)
                OR b.facility_type ILIKE ANY(%(patterns)s)
                OR b.facility_source_sheet ILIKE ANY(%(patterns)s)
                OR (
                    NOT b.name ILIKE ANY(%(room_patterns)s)
                    AND EXISTS (
                    SELECT 1
                    FROM courses lc
                    WHERE lc.branch_id = b.id
                      AND (
                        lc.source_group = 'library'
                        OR lc.source_group = 'museum_science'
                        OR lc.collection_category ILIKE ANY(%(patterns)s)
                        OR lc.domain_category ILIKE ANY(%(patterns)s)
                        OR lc.operator_type ILIKE ANY(%(patterns)s)
                        OR lc.service_group = '\uccb4\ud5d8'
                      )
                    )
                )
            )
            {provider_filter}
            {missing_filter}
            GROUP BY b.id, b.provider, b.branch_code, b.name, b.website_url, b.operating_hours, b.regular_holiday, b.admission_fee
            HAVING COALESCE(b.website_url, '') <> ''
                OR COUNT(c.id) FILTER (WHERE COALESCE(c.raw_url, '') <> '' OR COALESCE(c.application_url, '') <> '') > 0
            ORDER BY
                CASE
                    WHEN (b.provider = 'CULTURE_FACILITY' OR b.facility_source IS NOT NULL) THEN 0
                    WHEN b.name ILIKE ANY(%(patterns)s) AND NOT b.name ILIKE ANY(%(room_patterns)s) THEN 1
                    ELSE 2
                END,
                CASE
                    WHEN COALESCE(b.website_url, '') <> '' AND b.website_url !~* %(reservation_url_pattern)s THEN 0
                    WHEN COALESCE(b.website_url, '') <> '' THEN 1
                    ELSE 2
                END,
                b.updated_at DESC NULLS LAST
            LIMIT %(limit)s
            """,
            params,
        )
        return list(cursor.fetchall())


def update_branch(branch_id: str, info, dry_run: bool) -> bool:
    if not info.has_data():
        return False
    payload = {"facility_usage_info": info.as_basic_info()}
    if dry_run:
        return True
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            UPDATE branches
            SET
                operating_hours = COALESCE(NULLIF(%(operating_hours)s, ''), operating_hours),
                regular_holiday = COALESCE(NULLIF(%(regular_holiday)s, ''), regular_holiday),
                admission_fee = COALESCE(NULLIF(%(admission_fee)s, ''), admission_fee),
                basic_info = COALESCE(basic_info, '{}'::jsonb) || %(basic_info)s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %(branch_id)s
            """,
            {
                "branch_id": branch_id,
                "operating_hours": clean_text(info.operating_hours),
                "regular_holiday": clean_text(info.regular_holiday),
                "admission_fee": clean_text(info.admission_fee),
                "basic_info": Json(payload),
            },
        )
    return True


def run(args: argparse.Namespace) -> int:
    branches = load_library_branches(args.provider, args.limit, args.include_complete)
    updated = 0
    found = 0
    for index, branch in enumerate(branches, start=1):
        urls = [
            clean_text(branch.get("website_url")),
            *[clean_text(url) for url in (branch.get("raw_urls") or [])[:5]],
            *[clean_text(url) for url in (branch.get("application_urls") or [])[:5]],
        ]
        urls = [url for url in urls if url]
        info = fetch_library_usage_info(urls, timeout=args.timeout, max_pages=args.max_pages, branch_name=clean_text(branch.get("name")))
        if info.has_data():
            found += 1
            if update_branch(str(branch["id"]), info, args.dry_run):
                updated += 1
        print(
            json.dumps(
                {
                    "index": index,
                    "provider": branch.get("provider"),
                    "branch": branch.get("name"),
                    "found": info.has_data(),
                    "operating_hours": clean_text(info.operating_hours)[:180],
                    "regular_holiday": clean_text(info.regular_holiday)[:180],
                    "admission_fee": clean_text(info.admission_fee)[:180],
                    "source_url": info.source_url,
                },
                ensure_ascii=False,
            )
        )
    print(json.dumps({"processed": len(branches), "found": found, "updated": updated, "dry_run": args.dry_run}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
