import argparse
import os
import sys

from psycopg2.extras import execute_batch

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from DB.db_utils import get_db_cursor
from data_parser import TargetParser
from target_cleaner import extract_target_text


def main():
    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument("--provider", help="Only backfill one provider, for example ELAND_RETAIL")
    arg_parser.add_argument("--dry-run", action="store_true", help="Show what would change without updating DB")
    args = arg_parser.parse_args()

    parser = TargetParser()

    with get_db_cursor() as cursor:
        params = {}
        provider_filter = ""
        if args.provider:
            provider_filter = "AND provider = %(provider)s"
            params["provider"] = args.provider

        cursor.execute(
            f"""
            SELECT id, provider, title, title_raw, target
            FROM courses
            WHERE COALESCE(title_raw, title) IS NOT NULL
              {provider_filter}
            ORDER BY provider, id
            """,
            params,
        )
        rows = cursor.fetchall()

        updates = []
        samples = []
        for row in rows:
            source_title = row["title_raw"] or row["title"] or ""
            explicit_target = extract_target_text(source_title)
            if not explicit_target:
                continue

            parsed = parser.parse(f"{explicit_target} {source_title}")
            updates.append(
                {
                    "id": row["id"],
                    "target": explicit_target,
                    "target_age_group": parsed["age_group"],
                    "target_min_age": parsed["min_age"],
                    "target_max_age": parsed["max_age"],
                    "target_with_parent": parsed["with_parent"],
                    "target_tags": parsed["tags"],
                }
            )

            if len(samples) < 20 and row["target"] != explicit_target:
                samples.append(
                    {
                        "provider": row["provider"],
                        "title": source_title,
                        "old_target": row["target"],
                        "new_target": explicit_target,
                    }
                )

        if updates and not args.dry_run:
            execute_batch(
                cursor,
                """
                UPDATE courses
                SET target = %(target)s,
                    target_age_group = %(target_age_group)s,
                    target_min_age = %(target_min_age)s,
                    target_max_age = %(target_max_age)s,
                    target_with_parent = %(target_with_parent)s,
                    target_tags = %(target_tags)s,
                    updated_at = NOW()
                WHERE id = %(id)s
                """,
                updates,
                page_size=200,
            )

    mode = "dry_run" if args.dry_run else "updated"
    print(f"provider={args.provider or 'ALL'} scanned={len(rows)} {mode}={len(updates)}")
    for sample in samples:
        print(
            f"[{sample['provider']}] {sample['title']} | "
            f"{sample['old_target']} -> {sample['new_target']}"
        )


if __name__ == "__main__":
    main()
