from __future__ import annotations

import argparse
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from DB.db_utils import get_db_cursor


PROVIDERS = ("LOTTE", "EMART", "HOMEPLUS")


def pct(value: int, total: int) -> str:
    if not total:
        return "0.0%"
    return f"{value / total * 100:.1f}%"


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize crawler data quality by provider")
    parser.add_argument("--providers", nargs="*", default=list(PROVIDERS))
    parser.add_argument("--limit", type=int, default=5, help="Number of sample rows per provider")
    parser.add_argument("--since", help="Only include rows updated at or after this timestamp")
    parser.add_argument("--include-inactive", action="store_true", help="Include inactive/stale rows")
    args = parser.parse_args()

    provider_filter = "provider = ANY(%s)"
    params = [args.providers]
    if not args.include_inactive:
        provider_filter += " AND is_active IS TRUE"
    if args.since:
        provider_filter += " AND updated_at >= %s"
        params.append(args.since)

    with get_db_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT
                provider,
                COUNT(*) AS total,
                COUNT(title) AS title_count,
                COUNT(raw_url) AS raw_url_count,
                COUNT(schedule_raw) AS schedule_raw_count,
                COUNT(target_age_group) AS target_age_group_count,
                COUNT(schedule_days) FILTER (WHERE COALESCE(array_length(schedule_days, 1), 0) > 0) AS schedule_days_count,
                COUNT(schedule_time_start) AS schedule_time_start_count,
                COUNT(description) AS description_count,
                COUNT(instructor) AS instructor_count,
                COUNT(fee) FILTER (WHERE fee IS NOT NULL AND fee > 0) AS paid_count,
                COUNT(branch_id) AS branch_count
            FROM courses
            WHERE {provider_filter}
            GROUP BY provider
            ORDER BY provider
            """,
            params,
        )
        rows = cursor.fetchall()

        print("Provider quality")
        for row in rows:
            total = row["total"]
            print(f"\n[{row['provider']}] total={total}")
            for key in (
                "title_count",
                "raw_url_count",
                "schedule_raw_count",
                "schedule_days_count",
                "schedule_time_start_count",
                "target_age_group_count",
                "description_count",
                "instructor_count",
                "paid_count",
                "branch_count",
            ):
                print(f"  {key}: {row[key]} ({pct(row[key], total)})")

            cursor.execute(
                f"""
                SELECT title, branch_id, raw_url, schedule_raw, schedule_days, target_age_group, fee, description IS NOT NULL AS has_description
                FROM courses
                WHERE provider = %s
                  {"AND is_active IS TRUE" if not args.include_inactive else ""}
                  {"AND updated_at >= %s" if args.since else ""}
                ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST
                LIMIT %s
                """,
                (row["provider"], args.since, args.limit) if args.since else (row["provider"], args.limit),
            )
            samples = cursor.fetchall()
            print("  samples:")
            for sample in samples:
                print(
                    "   - "
                    f"{sample['title']} | fee={sample['fee']} | days={sample['schedule_days']} | "
                    f"target={sample['target_age_group']} | desc={sample['has_description']}"
                )


if __name__ == "__main__":
    main()
