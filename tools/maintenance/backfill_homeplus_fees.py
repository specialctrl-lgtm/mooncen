from __future__ import annotations

import argparse
import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from Crawler.Crawler_Homeplus import HomeplusCrawler
from DB.db_utils import get_db_cursor


def fetch_candidates(limit: int | None) -> list[dict]:
    params: list[object] = []
    limit_sql = ""
    if limit:
        limit_sql = "LIMIT %s"
        params.append(limit)

    with get_db_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT id, title, fee, raw_url
            FROM courses
            WHERE provider = 'HOMEPLUS'
              AND raw_url IS NOT NULL
              AND (
                    fee IS NULL
                 OR fee <= 100
              )
            ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST
            {limit_sql}
            """,
            params,
        )
        return [dict(row) for row in cursor.fetchall()]


def update_fee(course_id: str, fee: int, material_fee: int | None) -> None:
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            UPDATE courses
            SET fee = %s,
                material_fee = COALESCE(%s, material_fee),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (fee, material_fee, course_id),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill HOMEPLUS course fees from detail pages")
    parser.add_argument("--limit", type=int, default=100, help="Maximum courses to inspect")
    parser.add_argument("--delay", type=float, default=0.15, help="Delay between detail requests")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without updating DB")
    args = parser.parse_args()

    crawler = HomeplusCrawler(use_selenium=False)
    candidates = fetch_candidates(args.limit)
    updated = 0
    skipped = 0

    for course in candidates:
        detail = crawler.scrape_course_detail_http(course["raw_url"])
        fee = detail.get("fee")
        if not fee or int(fee) <= 100:
            skipped += 1
            continue

        material_fee = detail.get("material_fee")
        print(f"{course['fee']} -> {fee} | {course['title']} | {course['raw_url']}")
        if not args.dry_run:
            update_fee(course["id"], int(fee), int(material_fee or 0))
        updated += 1
        time.sleep(args.delay)

    print(f"inspected={len(candidates)} updated={updated} skipped={skipped} dry_run={args.dry_run}")


if __name__ == "__main__":
    main()
