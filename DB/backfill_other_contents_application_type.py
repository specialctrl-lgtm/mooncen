from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from DB.db_utils import get_db_cursor


CULTURE_PROVIDERS = (
    "HOMEPLUS",
    "EMART",
    "LOTTE",
    "HYUNDAI_DEPT",
    "GALLERIA",
    "AK_PLAZA",
    "ELAND_RETAIL",
    "SHINSEGAE_ACADEMY",
    "LOTTE_MART",
)


def main() -> None:
    with get_db_cursor() as cur:
        cur.execute(
            """
            SELECT application_type, COUNT(*) AS count
            FROM courses
            WHERE provider <> ALL(%s)
            GROUP BY application_type
            ORDER BY count DESC
            """,
            (list(CULTURE_PROVIDERS),),
        )
        before = cur.fetchall()

        cur.execute(
            """
            UPDATE courses
            SET application_type = 'ONLINE_RESERVATION',
                reservation_available = TRUE,
                discovery_status = COALESCE(NULLIF(discovery_status, ''), 'normalized_facility_reservation'),
                updated_at = NOW()
            WHERE provider <> ALL(%s)
              AND application_type = 'FACILITY_RESERVATION'
            """,
            (list(CULTURE_PROVIDERS),),
        )
        facility_updated = cur.rowcount

        cur.execute(
            """
            UPDATE courses
            SET application_type = 'ONLINE_RESERVATION',
                reservation_available = TRUE,
                discovery_status = COALESCE(NULLIF(discovery_status, ''), 'online_application_text'),
                updated_at = NOW()
            WHERE provider <> ALL(%s)
              AND application_type IS NULL
              AND application_url IS NOT NULL
              AND btrim(application_url) <> ''
              AND COALESCE(application_method_raw, '') ~ '(온라인|인터넷|홈페이지|웹|사이트|수강신청|예약|접수)'
            """,
            (list(CULTURE_PROVIDERS),),
        )
        null_online_updated = cur.rowcount

        cur.execute(
            """
            UPDATE courses
            SET application_type = 'OFFLINE_APPLY',
                reservation_available = FALSE,
                discovery_status = COALESCE(NULLIF(discovery_status, ''), 'reclassified_default_offline'),
                updated_at = NOW()
            WHERE provider <> ALL(%s)
              AND application_type IN ('INFO_ONLY', 'EXTERNAL_NOTICE')
              AND title IS NOT NULL
              AND btrim(title) <> ''
              AND (
                    start_date IS NOT NULL
                 OR end_date IS NOT NULL
                 OR schedule_raw IS NOT NULL AND btrim(schedule_raw) <> ''
                 OR description IS NOT NULL AND btrim(description) <> ''
                 OR target IS NOT NULL AND btrim(target) <> ''
                 OR fee IS NOT NULL
                 OR branch_id IS NOT NULL
              )
            """,
            (list(CULTURE_PROVIDERS),),
        )
        review_updated = cur.rowcount

        cur.execute(
            """
            UPDATE courses
            SET application_type = 'OFFLINE_APPLY',
                reservation_available = FALSE,
                discovery_status = COALESCE(NULLIF(discovery_status, ''), 'course_data_default_offline'),
                updated_at = NOW()
            WHERE provider <> ALL(%s)
              AND application_type IS NULL
              AND title IS NOT NULL
              AND btrim(title) <> ''
              AND (
                    start_date IS NOT NULL
                 OR end_date IS NOT NULL
                 OR schedule_raw IS NOT NULL AND btrim(schedule_raw) <> ''
                 OR description IS NOT NULL AND btrim(description) <> ''
                 OR target IS NOT NULL AND btrim(target) <> ''
                 OR fee IS NOT NULL
                 OR branch_id IS NOT NULL
              )
            """,
            (list(CULTURE_PROVIDERS),),
        )
        null_offline_updated = cur.rowcount

        cur.execute(
            """
            SELECT application_type, COUNT(*) AS count
            FROM courses
            WHERE provider <> ALL(%s)
            GROUP BY application_type
            ORDER BY count DESC
            """,
            (list(CULTURE_PROVIDERS),),
        )
        after = cur.fetchall()

    print("Before:")
    for row in before:
        print(f"  {row['application_type'] or 'NULL'}: {row['count']}")
    print(f"Facility normalized: {facility_updated}")
    print(f"NULL online updated: {null_online_updated}")
    print(f"Review reclassified: {review_updated}")
    print(f"NULL offline updated: {null_offline_updated}")
    print("After:")
    for row in after:
        print(f"  {row['application_type'] or 'NULL'}: {row['count']}")


if __name__ == "__main__":
    main()
