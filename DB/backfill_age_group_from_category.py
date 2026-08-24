import os
import sys

from psycopg2.extras import execute_batch

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from DB.db_utils import get_db_cursor
from target_category_fallback import infer_age_group_from_category


def main():
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT id, provider, title, category_raw, target_age_group
            FROM courses
            WHERE target_age_group IS NULL
               OR TRIM(COALESCE(target_age_group, '')) = ''
            ORDER BY provider, category_raw, title
            """
        )
        rows = cursor.fetchall()

        updates = []
        samples = []
        for row in rows:
            age_group = infer_age_group_from_category(row["category_raw"])
            if not age_group:
                continue
            updates.append({"id": row["id"], "target_age_group": age_group})
            if len(samples) < 20:
                samples.append(
                    {
                        "provider": row["provider"],
                        "category_raw": row["category_raw"],
                        "title": row["title"],
                        "target_age_group": age_group,
                    }
                )

        if updates:
            execute_batch(
                cursor,
                """
                UPDATE courses
                SET target_age_group = %(target_age_group)s,
                    updated_at = NOW()
                WHERE id = %(id)s
                """,
                updates,
                page_size=200,
            )

    print(f"scanned={len(rows)} updated={len(updates)}")
    for sample in samples:
        print(
            f"[{sample['provider']}] {sample['category_raw']} -> "
            f"{sample['target_age_group']} | {sample['title']}"
        )


if __name__ == "__main__":
    main()

