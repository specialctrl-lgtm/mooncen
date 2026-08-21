from __future__ import annotations

import argparse
import os
import sys
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(ROOT)

from DB.db_utils import get_db_cursor
from utils import extract_material_fee_amount


def fetch_candidates(limit: int | None) -> list[dict[str, Any]]:
    sql = """
        SELECT id, provider, title, material_fee, description
        FROM courses
        WHERE COALESCE(material_fee, 0) = 0
          AND description IS NOT NULL
          AND description ~ '(재료비|교재비|재료|교재|준비물)'
        ORDER BY updated_at DESC NULLS LAST, id
    """
    params: tuple[Any, ...] = ()
    if limit:
        sql += " LIMIT %s"
        params = (limit,)

    with get_db_cursor() as cursor:
        cursor.execute(sql, params)
        return list(cursor.fetchall())


def update_material_fee(course_id: str, material_fee: int) -> None:
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            UPDATE courses
            SET material_fee = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (material_fee, course_id),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill material_fee from course description text.")
    parser.add_argument("--dry-run", action="store_true", help="Show candidates without updating DB.")
    parser.add_argument("--limit", type=int, default=None, help="Limit candidate rows.")
    args = parser.parse_args()

    candidates = fetch_candidates(args.limit)
    extracted: list[dict[str, Any]] = []
    skipped = 0

    for row in candidates:
        amount = extract_material_fee_amount(row.get("description"))
        if amount <= 0:
            skipped += 1
            continue

        extracted.append({**row, "extracted_material_fee": amount})
        if not args.dry_run:
            update_material_fee(str(row["id"]), amount)

    mode = "DRY RUN" if args.dry_run else "UPDATED"
    print(f"{mode}: candidates={len(candidates)} extracted={len(extracted)} skipped={skipped}")
    for row in extracted[:20]:
        description = " ".join(str(row.get("description") or "").split())
        print(
            f"- {row['provider']} | {row['extracted_material_fee']:,} | "
            f"{row['title']} | {description[:120]}"
        )


if __name__ == "__main__":
    main()
