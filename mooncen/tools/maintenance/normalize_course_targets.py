from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from DB.db_utils import get_db_cursor
from data_parser import TargetParser
from target_cleaner import normalize_target_text


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize course target display text.")
    parser.add_argument("--provider", help="Limit to one provider, for example EMART or HOMEPLUS.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    filters = ["target IS NOT NULL", "(target LIKE '%%년생%%' OR target LIKE '%%대상%%')"]
    params: dict[str, object] = {"limit": args.limit}
    if args.provider:
        filters.append("provider = %(provider)s")
        params["provider"] = args.provider.upper()
    limit_sql = "LIMIT %(limit)s" if args.limit else ""

    with get_db_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT id, provider, provider_course_id, title, title_raw, target, description
            FROM courses
            WHERE {' AND '.join(filters)}
            ORDER BY updated_at DESC NULLS LAST
            {limit_sql}
            """,
            params,
        )
        rows = cursor.fetchall()

    target_parser = TargetParser()
    changed = 0
    for row in rows:
        old_target = row["target"]
        new_target = normalize_target_text(old_target)
        if new_target == old_target:
            continue

        source = " ".join(
            value
            for value in [
                row.get("title") or "",
                row.get("title_raw") or "",
                new_target or "",
                row.get("description") or "",
            ]
            if value
        )
        parsed = target_parser.parse(source)
        changed += 1
        print(
            f"{row['provider']} {row['provider_course_id']}: {old_target!r} -> {new_target!r} "
            f"({parsed['age_group']} {parsed['min_age']}~{parsed['max_age']})"
        )

        if args.dry_run:
            continue

        with get_db_cursor() as cursor:
            cursor.execute(
                """
                UPDATE courses
                SET target = %(target)s,
                    target_age_group = %(target_age_group)s,
                    target_min_age = %(target_min_age)s,
                    target_max_age = %(target_max_age)s,
                    target_with_parent = %(target_with_parent)s,
                    target_tags = %(target_tags)s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = CAST(%(id)s AS uuid)
                """,
                {
                    "id": row["id"],
                    "target": new_target,
                    "target_age_group": parsed["age_group"],
                    "target_min_age": parsed["min_age"],
                    "target_max_age": parsed["max_age"],
                    "target_with_parent": parsed["with_parent"],
                    "target_tags": parsed["tags"],
                },
            )

    print(f"scanned={len(rows)} changed={changed} dry_run={args.dry_run}")


if __name__ == "__main__":
    main()
