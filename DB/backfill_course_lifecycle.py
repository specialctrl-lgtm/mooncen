import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from DB.course_lifecycle import enrich_course_lifecycle
from DB.db_utils import get_db_cursor


COURSE_FIELDS = """
    id, title, instructor, target, category_raw, fee, material_fee, sessions,
    schedule_raw, start_date, end_date, apply_start, apply_end, status,
    raw_url, description, image_url
"""


def main():
    with get_db_cursor() as cursor:
        cursor.execute(f"""
            SELECT {COURSE_FIELDS}
            FROM courses
            WHERE content_hash IS NULL
        """)
        rows = cursor.fetchall()

        for row in rows:
            course_data = dict(row)
            enrich_course_lifecycle(course_data)
            cursor.execute(
                """
                UPDATE courses
                SET content_hash = %(content_hash)s,
                    first_seen_at = COALESCE(first_seen_at, created_at, CURRENT_TIMESTAMP),
                    last_seen_at = COALESCE(last_seen_at, updated_at, created_at, CURRENT_TIMESTAMP),
                    is_active = TRUE
                WHERE id = %(id)s
                """,
                course_data,
            )

    print(f"Backfilled lifecycle hashes: {len(rows)}")


if __name__ == "__main__":
    main()
