from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from DB.db_utils import get_db_cursor
from data_parser import TargetParser
from target_cleaner import extract_target_text


BAD_HOMEPLUS_TARGET_RE = re.compile(
    r"^\s*\[(?:Kids|Adult|Baby|Toddler|Child|Senior)\]\s*"
    r"\d{1,2}\s*/\s*\d{1,2}(?:\s*\([^)]+\))?.*$",
    re.IGNORECASE,
)


def is_bad_homeplus_target(value: str | None) -> bool:
    target = str(value or "").strip()
    if not target:
        return False
    if BAD_HOMEPLUS_TARGET_RE.match(target):
        return True
    return bool(
        re.search(r"\[(?:Kids|Adult|Baby|Toddler|Child|Senior)\]", target, re.IGNORECASE)
        and re.search(r"\d{1,2}\s*/\s*\d{1,2}", target)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Fix Homeplus open-date labels stored in courses.target.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    limit_sql = "LIMIT %(limit)s" if args.limit else ""
    with get_db_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT id, provider_course_id, title, title_raw, target, description
            FROM courses
            WHERE provider = 'HOMEPLUS'
              AND target IS NOT NULL
              AND target ~* '\\[(Kids|Adult|Baby|Toddler|Child|Senior)\\]'
              AND target ~ '\\d{{1,2}}\\s*/\\s*\\d{{1,2}}'
            ORDER BY updated_at DESC NULLS LAST
            {limit_sql}
            """,
            {"limit": args.limit},
        )
        rows = cursor.fetchall()

    parser_obj = TargetParser()
    changed = 0

    for row in rows:
        old_target = row["target"]
        if not is_bad_homeplus_target(old_target):
            continue

        new_target = extract_target_text(row.get("title_raw") or row.get("title") or "")
        source = " ".join(
            value
            for value in [
                row.get("title") or "",
                row.get("title_raw") or "",
                new_target or "",
                row.get("description") or "",
                old_target or "",
            ]
            if value
        )
        parsed = parser_obj.parse(source)
        changed += 1
        print(
            f"{row['provider_course_id']}: target={old_target!r} -> {new_target!r}, "
            f"age_group={parsed['age_group']}, min={parsed['min_age']}, max={parsed['max_age']}"
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
