from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from DB.db_utils import get_db_cursor
from ai_processor import AIProcessor
from data_parser import NON_TARGET_AGE_PHRASE_RE
from target_cleaner import extract_target_text
from title_cleaner import clean_course_title


def fetch_candidates(limit: int) -> list[dict]:
    limit_sql = "LIMIT %(limit)s" if limit else ""
    # Broad SQL prefilter; exact matching is done with the shared Python regex.
    pattern = "%0%\uC2DC\uC791%"
    with get_db_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT id, provider, title, title_raw, target, category_raw, schedule_raw,
                   target_age_group, target_min_age, target_max_age,
                   target_with_parent, target_tags, title_prefix_removed,
                   ai_title_result
            FROM courses
            WHERE COALESCE(title_raw, title, '') LIKE %(pattern)s
               OR COALESCE(title, '') LIKE %(pattern)s
               OR COALESCE(target, '') LIKE %(pattern)s
            ORDER BY updated_at DESC NULLS LAST, id
            {limit_sql}
            """,
            {"pattern": pattern, "limit": limit},
        )
        rows = [dict(row) for row in cursor.fetchall()]

    return [
        row
        for row in rows
        if any(
            NON_TARGET_AGE_PHRASE_RE.search(str(row.get(field) or ""))
            for field in ("title", "title_raw", "target")
        )
    ]


def update_row(course_id: str, result: dict, dry_run: bool) -> None:
    if dry_run:
        return
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            UPDATE courses
            SET title = %(title)s,
                target = %(target)s,
                target_age_group = %(target_age_group)s,
                target_min_age = %(target_min_age)s,
                target_max_age = %(target_max_age)s,
                target_with_parent = %(target_with_parent)s,
                target_tags = %(target_tags)s,
                ai_title_result = %(ai_title_result)s::jsonb,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = CAST(%(id)s AS uuid)
            """,
            {
                "id": course_id,
                "title": result.get("title"),
                "target": result.get("target"),
                "target_age_group": result.get("target_age_group"),
                "target_min_age": result.get("target_min_age"),
                "target_max_age": result.get("target_max_age"),
                "target_with_parent": result.get("target_with_parent", False),
                "target_tags": result.get("target_tags") or [],
                "ai_title_result": json.dumps(result.get("ai_title_result") or {}, ensure_ascii=False),
            },
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remove false 0-year-old targets from titles like '0세부터 시작하는 ...'."
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    processor = AIProcessor()
    rows = fetch_candidates(args.limit)
    changed = 0

    for row in rows:
        source_title = row.get("title_raw") or row.get("title") or ""
        rule_title, removed = clean_course_title(source_title)
        rule_target = extract_target_text(source_title) or row.get("target")
        ai_result = row.get("ai_title_result") or {}
        if isinstance(ai_result, str):
            try:
                ai_result = json.loads(ai_result)
            except json.JSONDecodeError:
                ai_result = {}

        normalized = processor._normalize_title_result(ai_result, row, rule_title, rule_target, removed)
        before = (row.get("target"), row.get("target_age_group"), row.get("target_min_age"), row.get("target_max_age"))
        after = (
            normalized.get("target"),
            normalized.get("target_age_group"),
            normalized.get("target_min_age"),
            normalized.get("target_max_age"),
        )
        if before == after and normalized.get("title") == row.get("title"):
            continue

        changed += 1
        print(
            f"{row['id']} {row.get('provider')} {before} -> {after} "
            f"title={normalized.get('title')}"
        )
        update_row(str(row["id"]), normalized, args.dry_run)

    print(json.dumps({"scanned": len(rows), "changed": changed, "dry_run": args.dry_run}, ensure_ascii=False))


if __name__ == "__main__":
    main()
