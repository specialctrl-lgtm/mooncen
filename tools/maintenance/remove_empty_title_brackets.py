from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from DB.db_utils import get_db_cursor
from title_cleaner import clean_course_title


EMPTY_BRACKETS_RE = re.compile(r"\s*[\(\[\{（［｛]\s*[\)\]\}）］｝]\s*")


def remove_empty_brackets(value: str | None) -> str:
    text = str(value or "")
    text = EMPTY_BRACKETS_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" -*|,")


def main() -> None:
    parser = argparse.ArgumentParser(description="Remove empty ()/[] from course display titles.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    limit_sql = "LIMIT %(limit)s" if args.limit else ""
    with get_db_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT id, provider, title, title_raw
            FROM courses
            WHERE title ~ '[\\(\\[\\{{（［｛][[:space:]]*[\\)\\]\\}}）］｝]'
               OR title_raw ~ '[\\(\\[\\{{（［｛][[:space:]]*[\\)\\]\\}}）］｝]'
            ORDER BY updated_at DESC NULLS LAST
            {limit_sql}
            """,
            {"limit": args.limit},
        )
        rows = cursor.fetchall()

    changed = 0
    for row in rows:
        current = row["title"] or ""
        cleaned_from_raw = clean_course_title(row["title_raw"] or "")[0] if row.get("title_raw") else current
        cleaned = remove_empty_brackets(cleaned_from_raw or current)
        if not cleaned or cleaned == current:
            continue

        changed += 1
        print(f"[{row['provider']}] {current} -> {cleaned}")
        if args.dry_run:
            continue

        with get_db_cursor() as cursor:
            cursor.execute(
                """
                UPDATE courses
                SET title = %(title)s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = CAST(%(id)s AS uuid)
                """,
                {"id": row["id"], "title": cleaned},
            )

    print(f"scanned={len(rows)} changed={changed} dry_run={args.dry_run}")


if __name__ == "__main__":
    main()
