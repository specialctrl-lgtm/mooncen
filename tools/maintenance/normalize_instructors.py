from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from DB.db_utils import get_db_cursor
from utils import clean_instructor_name


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize course instructor values to names only.")
    parser.add_argument("--provider", action="append", help="Limit to one provider. Can be passed multiple times.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sample", type=int, default=25)
    args = parser.parse_args()

    params = {}
    provider_where = ""
    if args.provider:
        provider_where = "AND provider = ANY(%(providers)s)"
        params["providers"] = args.provider

    with get_db_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT id, provider, title, instructor
            FROM courses
            WHERE instructor IS NOT NULL
              AND instructor <> ''
              {provider_where}
            ORDER BY provider, updated_at DESC, id
            """,
            params,
        )
        rows = [dict(row) for row in cursor.fetchall()]

        updates = []
        for row in rows:
            cleaned = clean_instructor_name(row.get("instructor"))
            if cleaned != row.get("instructor"):
                updates.append((row, cleaned))

        print(f"scanned={len(rows)} updates={len(updates)} dry_run={args.dry_run}")
        for row, cleaned in updates[: args.sample]:
            print(f"- {row['provider']} | {row['instructor']} -> {cleaned or 'NULL'} | {row['title']}")

        if args.dry_run or not updates:
            return 0

        for row, cleaned in updates:
            cursor.execute(
                """
                UPDATE courses
                SET instructor = %(instructor)s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %(id)s
                """,
                {"id": row["id"], "instructor": cleaned},
            )
        print(f"updated={len(updates)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
