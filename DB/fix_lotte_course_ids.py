from __future__ import annotations

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from DB.db_utils import get_db_cursor


def main() -> None:
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            WITH colon_rows AS (
                SELECT *, replace(provider_course_id, ':', '-') AS normalized_id
                FROM courses
                WHERE provider = 'LOTTE'
                  AND provider_course_id LIKE '%:%:%:%'
            )
            UPDATE courses AS target
            SET
                branch_id = COALESCE(source.branch_id, target.branch_id),
                title = COALESCE(NULLIF(source.title, ''), target.title),
                instructor = COALESCE(source.instructor, target.instructor),
                target = COALESCE(source.target, target.target),
                category_raw = COALESCE(source.category_raw, target.category_raw),
                fee = COALESCE(source.fee, target.fee),
                material_fee = COALESCE(source.material_fee, target.material_fee),
                schedule_raw = COALESCE(source.schedule_raw, target.schedule_raw),
                schedule_days = COALESCE(source.schedule_days, target.schedule_days),
                schedule_time_start = COALESCE(source.schedule_time_start, target.schedule_time_start),
                schedule_time_end = COALESCE(source.schedule_time_end, target.schedule_time_end),
                schedule_frequency = COALESCE(source.schedule_frequency, target.schedule_frequency),
                schedule_duration_minutes = COALESCE(source.schedule_duration_minutes, target.schedule_duration_minutes),
                start_date = COALESCE(source.start_date, target.start_date),
                end_date = COALESCE(source.end_date, target.end_date),
                status = COALESCE(source.status, target.status),
                raw_url = COALESCE(source.raw_url, target.raw_url),
                description = COALESCE(source.description, target.description),
                image_url = COALESCE(source.image_url, target.image_url),
                target_age_group = COALESCE(source.target_age_group, target.target_age_group),
                target_min_age = COALESCE(source.target_min_age, target.target_min_age),
                target_max_age = COALESCE(source.target_max_age, target.target_max_age),
                target_with_parent = COALESCE(source.target_with_parent, target.target_with_parent),
                target_tags = COALESCE(source.target_tags, target.target_tags),
                updated_at = CURRENT_TIMESTAMP
            FROM colon_rows AS source
            WHERE target.provider = 'LOTTE'
              AND target.provider_course_id = source.normalized_id
            """
        )
        merged = cursor.rowcount

        cursor.execute(
            """
            DELETE FROM courses AS source
            USING courses AS target
            WHERE source.provider = 'LOTTE'
              AND target.provider = 'LOTTE'
              AND source.provider_course_id LIKE '%:%:%:%'
              AND target.provider_course_id = replace(source.provider_course_id, ':', '-')
            """
        )
        deleted = cursor.rowcount

        cursor.execute(
            """
            UPDATE courses
            SET provider_course_id = replace(provider_course_id, ':', '-'),
                updated_at = CURRENT_TIMESTAMP
            WHERE provider = 'LOTTE'
              AND provider_course_id LIKE '%:%:%:%'
            """
        )
        normalized = cursor.rowcount

    print(f"merged={merged}, deleted_duplicates={deleted}, normalized={normalized}")


if __name__ == "__main__":
    main()
