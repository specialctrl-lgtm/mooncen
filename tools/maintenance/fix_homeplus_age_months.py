from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from DB.db_utils import get_db_cursor
from data_parser import TargetParser, parse_crawler_target
from target_category_fallback import infer_age_group_from_category
from target_cleaner import extract_target_text
from utils import clean_text


def recalc_homeplus_target(row: dict, parser: TargetParser) -> dict:
    raw_title = clean_text(row.get("title_raw")) or clean_text(row.get("title"))
    title = clean_text(row.get("title"))
    existing_target = clean_text(row.get("target"))
    category_raw = clean_text(row.get("category_raw"))
    explicit_target = extract_target_text(raw_title) or extract_target_text(title) or existing_target

    target_source = " ".join(
        part
        for part in [raw_title, title, explicit_target, category_raw]
        if part
    )
    parsed = parse_crawler_target(target_source, parser)
    if not parsed.get("age_group"):
        parsed["age_group"] = infer_age_group_from_category(category_raw)
    if not parsed.get("age_group") and row.get("target_age_group") == "ADULT":
        parsed["age_group"] = "ADULT"

    if parsed.get("age_group") == "ADULT" and not parsed.get("age_is_explicit"):
        parsed["min_age"] = None
        parsed["max_age"] = None

    return {
        "target": explicit_target or None,
        "target_age_group": parsed.get("age_group"),
        "target_min_age": parsed.get("min_age"),
        "target_max_age": parsed.get("max_age"),
        "target_with_parent": parsed.get("with_parent", False),
        "target_tags": parsed.get("tags") or [],
        "target_age_is_explicit": parsed.get("age_is_explicit", False),
    }


def changed(row: dict, next_values: dict) -> bool:
    keys = [
        "target",
        "target_age_group",
        "target_min_age",
        "target_max_age",
        "target_with_parent",
        "target_age_is_explicit",
    ]
    for key in keys:
        if row.get(key) != next_values.get(key):
            return True
    return list(row.get("target_tags") or []) != list(next_values.get("target_tags") or [])


def course_column_exists(cursor, column_name: str) -> bool:
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'courses'
              AND column_name = %s
        )
        """,
        (column_name,),
    )
    row = cursor.fetchone()
    if isinstance(row, dict):
        return bool(row.get("exists"))
    return bool(row[0])


def main() -> int:
    arg_parser = argparse.ArgumentParser(description="Backfill HOMEPLUS target min/max ages as months.")
    arg_parser.add_argument("--dry-run", action="store_true")
    arg_parser.add_argument("--limit", type=int, default=0)
    arg_parser.add_argument("--sample", type=int, default=20)
    args = arg_parser.parse_args()

    parser = TargetParser()
    with get_db_cursor() as cursor:
        has_explicit_column = course_column_exists(cursor, "target_age_is_explicit")
        explicit_select = "target_age_is_explicit" if has_explicit_column else "FALSE AS target_age_is_explicit"
        cursor.execute(
            f"""
            SELECT id, title, title_raw, target, category_raw,
                   target_age_group, target_min_age, target_max_age,
                   target_with_parent, target_tags, {explicit_select}
            FROM courses
            WHERE provider = 'HOMEPLUS'
            ORDER BY updated_at DESC, id
            """
        )
        rows = [dict(row) for row in cursor.fetchall()]

        updates = []
        for row in rows:
            next_values = recalc_homeplus_target(row, parser)
            if changed(row, next_values):
                updates.append((row, next_values))
                if args.limit and len(updates) >= args.limit:
                    break

        print(f"HOMEPLUS scanned={len(rows)} updates={len(updates)} dry_run={args.dry_run}")
        for before, after in updates[: args.sample]:
            print(
                f"- {before['title']} | target {before.get('target')} -> {after.get('target')} | "
                f"group {before.get('target_age_group')} -> {after.get('target_age_group')} | "
                f"age {before.get('target_min_age')}~{before.get('target_max_age')} -> "
                f"{after.get('target_min_age')}~{after.get('target_max_age')}"
            )

        if args.dry_run or not updates:
            return 0

        for before, after in updates:
            assignments = [
                "target = %(target)s",
                "target_age_group = %(target_age_group)s",
                "target_min_age = %(target_min_age)s",
                "target_max_age = %(target_max_age)s",
                "target_with_parent = %(target_with_parent)s",
                "target_tags = %(target_tags)s",
                "updated_at = CURRENT_TIMESTAMP",
            ]
            if has_explicit_column:
                assignments.insert(-1, "target_age_is_explicit = %(target_age_is_explicit)s")

            cursor.execute(
                f"""
                UPDATE courses
                SET {", ".join(assignments)}
                WHERE id = %(id)s
                """,
                {**after, "id": before["id"]},
            )
        print(f"updated={len(updates)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
