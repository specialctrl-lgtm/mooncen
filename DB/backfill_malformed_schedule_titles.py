from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from DB.db_utils import get_db_cursor
from title_cleaner import clean_course_title


MALFORMED_SCHEDULE_RE = re.compile(
    r"^(?:월|화|수|목|금|토|일)\s*<[^>]*\d+\s*회\s*>"
    r"|(?:[01]?\d|2[0-3])(?::[0-5]\d|시(?:\s*[0-5]?\d분?)?)\s*[~-]\s*$"
)


def main() -> int:
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT id, provider, title, title_raw, title_prefix_removed
            FROM courses
            WHERE title IS NOT NULL
            ORDER BY provider, title
            """
        )
        rows = cursor.fetchall()

        changed = 0
        samples: list[tuple[str, str, str, str]] = []
        for row in rows:
            title = row["title"] or ""
            if not MALFORMED_SCHEDULE_RE.search(title):
                continue

            clean_title, removed = clean_course_title(title)
            if clean_title == title:
                continue

            existing_removed = row.get("title_prefix_removed") or ""
            merged_removed = " | ".join(
                dict.fromkeys(part for part in [existing_removed, removed] if part)
            )
            cursor.execute(
                """
                UPDATE courses
                SET title = %(title)s,
                    title_prefix_removed = %(removed)s
                WHERE id = %(id)s
                """,
                {
                    "id": row["id"],
                    "title": clean_title,
                    "removed": merged_removed or None,
                },
            )
            changed += 1
            if len(samples) < 30:
                samples.append((row["provider"], title, clean_title, removed))

    print(f"malformed title rows scanned: {len(rows)}")
    print(f"titles changed: {changed}")
    for provider, before, after, removed in samples:
        print("---")
        print(f"provider: {provider}")
        print(f"before : {before}")
        print(f"after  : {after}")
        print(f"removed: {removed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
