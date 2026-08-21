from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from DB.db_utils import get_db_cursor
from data_parser import ScheduleParser, TargetParser
from utils import clean_text


def extract_age_target_from_schedule(value: object) -> str:
    text = clean_text(value)
    if not text:
        return ""
    patterns = [
        r"\(([^)]*(?:개월|년생|세|초등|중등|고등)[^)]*)\)",
        r"(\d{1,3}\s*[~-]\s*\d{1,3}\s*개월)",
        r"(\d{1,3}\s*개월\s*(?:이상|이하|부터|까지)?)",
        r"(\d{2,4}\s*[~-]\s*\d{2,4}\s*년생)",
        r"(\d{1,2}\s*[~-]\s*\d{1,2}\s*세)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return clean_text(match.group(1))
    return ""


def remove_age_target_from_schedule(value: object, target: str) -> str:
    text = clean_text(value)
    if not text or not target:
        return text
    escaped = re.escape(target)
    text = re.sub(rf"\s*\(\s*{escaped}\s*\)\s*", " ", text)
    text = re.sub(rf"\s*{escaped}\s*", " ", text)
    return clean_text(text)


def month_range_from_target(value: object) -> tuple[int | None, int | None]:
    text = clean_text(value)
    if not text:
        return None, None
    match = re.search(r"(\d{1,3})\s*[~-]\s*(\d{1,3})\s*개월", text)
    if match:
        return int(match.group(1)), int(match.group(2))
    match = re.search(r"(\d{1,3})\s*개월\s*(?:이상|부터)", text)
    if match:
        return int(match.group(1)), None
    match = re.search(r"(\d{1,3})\s*개월\s*(?:이하|까지)", text)
    if match:
        return 0, int(match.group(1))
    match = re.search(r"(\d{1,3})\s*개월", text)
    if match:
        month = int(match.group(1))
        return month, month
    return None, None


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill Galleria age target hidden in schedule_raw.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    target_parser = TargetParser()
    schedule_parser = ScheduleParser()

    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT id, title, schedule_raw, target
            FROM courses
            WHERE provider = 'GALLERIA'
              AND (
                    COALESCE(schedule_raw, '') LIKE '%%개월%%'
                 OR COALESCE(schedule_raw, '') LIKE '%%년생%%'
                 OR COALESCE(schedule_raw, '') LIKE '%%초등%%'
                 OR COALESCE(schedule_raw, '') LIKE '%%중등%%'
                 OR COALESCE(schedule_raw, '') LIKE '%%고등%%'
                 OR COALESCE(target, '') LIKE '%%개월%%'
                 OR COALESCE(target, '') LIKE '%%년생%%'
                 OR COALESCE(target, '') LIKE '%%초등%%'
                 OR COALESCE(target, '') LIKE '%%중등%%'
                 OR COALESCE(target, '') LIKE '%%고등%%'
              )
            ORDER BY updated_at DESC NULLS LAST
            """
        )
        rows = cursor.fetchall()

    changed = 0
    for row in rows:
        age_target = extract_age_target_from_schedule(row["schedule_raw"])
        if not age_target:
            age_target = clean_text(row.get("target"))
        if not age_target:
            continue

        parsed_target = target_parser.parse(age_target)
        min_month, max_month = month_range_from_target(age_target)
        if min_month is not None or max_month is not None:
            parsed_target["min_age"] = min_month
            parsed_target["max_age"] = max_month

        cleaned_schedule = remove_age_target_from_schedule(row["schedule_raw"], age_target)
        parsed_schedule = schedule_parser.parse(cleaned_schedule)
        changed += 1

        print(
            f"{row['id']} target={row.get('target')} -> {age_target} "
            f"age={parsed_target['age_group']} {parsed_target['min_age']}~{parsed_target['max_age']} "
            f"schedule={row['schedule_raw']} -> {cleaned_schedule}"
        )

        if args.dry_run:
            continue

        with get_db_cursor() as cursor:
            cursor.execute(
                """
                UPDATE courses
                SET target = %(target)s,
                    target_age_group = %(target_age_group)s,
                    target_min_age = %(target_min_age)s,
                    target_max_age = %(target_max_age)s,
                    target_with_parent = %(target_with_parent)s,
                    target_tags = %(target_tags)s,
                    schedule_raw = %(schedule_raw)s,
                    schedule_days = %(schedule_days)s,
                    schedule_time_start = %(schedule_time_start)s,
                    schedule_time_end = %(schedule_time_end)s,
                    schedule_frequency = %(schedule_frequency)s,
                    schedule_duration_minutes = %(schedule_duration_minutes)s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = CAST(%(id)s AS uuid)
                """,
                {
                    "id": row["id"],
                    "target": age_target,
                    "target_age_group": parsed_target["age_group"],
                    "target_min_age": parsed_target["min_age"],
                    "target_max_age": parsed_target["max_age"],
                    "target_with_parent": parsed_target["with_parent"],
                    "target_tags": parsed_target["tags"],
                    "schedule_raw": cleaned_schedule,
                    "schedule_days": parsed_schedule["days"],
                    "schedule_time_start": parsed_schedule["time_start"],
                    "schedule_time_end": parsed_schedule["time_end"],
                    "schedule_frequency": parsed_schedule["frequency"],
                    "schedule_duration_minutes": parsed_schedule["duration_minutes"],
                },
            )

    print(f"scanned={len(rows)} changed={changed} dry_run={args.dry_run}")


if __name__ == "__main__":
    main()
