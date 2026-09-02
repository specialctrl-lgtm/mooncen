from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from DB.db_utils import get_db_cursor
from tools.sample_collect_from_yaml import lotte_mart_detail_fields, session


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill missing LOTTE_MART course images from detail pages.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum courses to scan. 0 means all missing images.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sample", type=int, default=20)
    args = parser.parse_args()

    sql_limit = "LIMIT %(limit)s" if args.limit and args.limit > 0 else ""
    params = {"limit": args.limit}
    http = session()

    with get_db_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT id, title, raw_url
            FROM courses
            WHERE provider = 'LOTTE_MART'
              AND raw_url IS NOT NULL
              AND btrim(raw_url) <> ''
              AND (image_url IS NULL OR btrim(image_url) = '')
            ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST
            {sql_limit}
            """,
            params,
        )
        rows = [dict(row) for row in cursor.fetchall()]

        updates: list[tuple[dict, str]] = []
        failures = 0
        for row in rows:
            try:
                fields = lotte_mart_detail_fields(http, row["raw_url"])
            except requests.RequestException as exc:
                failures += 1
                if len(updates) < args.sample:
                    print(f"- fetch failed | {type(exc).__name__}: {exc} | {row['raw_url']}")
                continue

            image_url = fields.get("image_url") or ""
            if image_url:
                updates.append((row, image_url))

        print(
            f"scanned={len(rows)} image_found={len(updates)} failures={failures} "
            f"dry_run={args.dry_run}"
        )
        for row, image_url in updates[: args.sample]:
            print(f"- {row['title']} -> {image_url}")

        if args.dry_run or not updates:
            return 0

        for row, image_url in updates:
            cursor.execute(
                """
                UPDATE courses
                SET image_url = %(image_url)s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %(id)s
                """,
                {"id": row["id"], "image_url": image_url},
            )
        print(f"updated={len(updates)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
