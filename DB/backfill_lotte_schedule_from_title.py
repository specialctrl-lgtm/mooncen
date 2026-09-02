from __future__ import annotations

import os
import re
import sys

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from DB.db_utils import get_db_cursor
from data_parser import ScheduleParser


SCHEDULE_PATTERN = re.compile(
    r"[\uC6D4\uD654\uC218\uBAA9\uAE08\uD1A0\uC77C]\s*"
    r"\d{1,2}:\d{2}\s*[~\-]\s*\d{1,2}:\d{2}"
)
SESSIONS_PATTERN = re.compile(r"총\s*(\d+)\s*회")


def main() -> None:
    parser = ScheduleParser()
    updated = 0
    skipped = 0

    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT id, title
            FROM courses
            WHERE provider = 'LOTTE'
              AND (schedule_raw IS NULL OR schedule_raw = '')
            """
        )
        rows = cursor.fetchall()

        for row in rows:
            title = row["title"] or ""
            match = SCHEDULE_PATTERN.search(title)
            if not match:
                skipped += 1
                continue

            schedule_raw = match.group(0)
            parsed = parser.parse(schedule_raw)
            duration_minutes = parsed["duration_minutes"]
            if duration_minutes is not None and duration_minutes <= 0:
                duration_minutes = None

            sessions = None
            sessions_match = SESSIONS_PATTERN.search(title)
            if sessions_match:
                sessions = int(sessions_match.group(1))

            cursor.execute(
                """
                UPDATE courses
                SET schedule_raw = %(schedule_raw)s,
                    schedule_days = %(schedule_days)s,
                    schedule_time_start = %(schedule_time_start)s,
                    schedule_time_end = %(schedule_time_end)s,
                    schedule_frequency = %(schedule_frequency)s,
                    schedule_duration_minutes = %(schedule_duration_minutes)s,
                    sessions = COALESCE(sessions, %(sessions)s),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %(id)s
                """,
                {
                    "id": row["id"],
                    "schedule_raw": schedule_raw,
                    "schedule_days": parsed["days"],
                    "schedule_time_start": parsed["time_start"],
                    "schedule_time_end": parsed["time_end"],
                    "schedule_frequency": parsed["frequency"],
                    "schedule_duration_minutes": duration_minutes,
                    "sessions": sessions,
                },
            )
            updated += 1

    print(f"updated={updated}, skipped={skipped}")


if __name__ == "__main__":
    main()
