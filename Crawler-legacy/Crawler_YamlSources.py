from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlparse
from psycopg2.extras import Json

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from DB.course_lifecycle import enrich_course_lifecycle, mark_stale_courses, should_skip_expired_course, utc_now
from DB.course_upsert_guards import coalesce_provider_course_id_by_raw_url, delete_empty_branches_for_provider
from DB.db_utils import get_db_cursor
from data_parser import ScheduleParser, TargetParser, explicit_age_month_range, parse_crawler_target
from target_category_fallback import infer_age_group_from_category
from target_cleaner import extract_target_text
from title_cleaner import clean_course_title
from tools.sample_collect_from_yaml import COLLECTORS
from utils import (
    clean_instructor_name,
    clean_text,
    extract_krw_amount,
    extract_material_fee_amount,
    infer_course_status,
    parse_date,
    setup_logger,
)


logger = setup_logger(__name__, "logs/crawler_yaml_sources.log")


SUPPORTED_PROVIDERS = {
    "HYUNDAI_DEPT",
    "GALLERIA",
    "AK_PLAZA",
    "ELAND_RETAIL",
    "SHINSEGAE_ACADEMY",
    "LOTTE_MART",
}


def parse_date_range(value: str | None) -> tuple[Optional[object], Optional[object]]:
    if not value:
        return None, None

    text = clean_text(value).replace("~", "-").replace("–", "-")
    matches = re.findall(r"\d{4}[./-]\d{1,2}[./-]\d{1,2}", text)
    if len(matches) >= 2:
        return parse_date(matches[0].replace("/", ".").replace("-", ".")), parse_date(matches[1].replace("/", ".").replace("-", "."))
    if len(matches) == 1:
        date = parse_date(matches[0].replace("/", ".").replace("-", "."))
        return date, date
    return None, None


def parse_schedule_dates(value: Any, default_year: Optional[int] = None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        candidates = list(value)
    else:
        text = clean_text(value)
        if not text:
            return []
        full_matches = re.findall(r"\d{4}[./-]\d{1,2}[./-]\d{1,2}", text)
        is_plain_range = (
            len(full_matches) == 2
            and re.search(
                r"\d{4}[./-]\d{1,2}[./-]\d{1,2}\s*(?:~|-|부터|至|～)\s*\d{4}[./-]\d{1,2}[./-]\d{1,2}",
                text,
            )
        )
        if is_plain_range:
            return []
        candidates = full_matches
        remainder = re.sub(r"\d{4}[./-]\d{1,2}[./-]\d{1,2}", " ", text)
        if default_year:
            candidates.extend(f"{default_year}.{month}.{day}" for month, day in re.findall(r"(?<!\d)(\d{1,2})[./](\d{1,2})(?!\d)", remainder))
            candidates.extend(f"{default_year}.{month}.{day}" for month, day in re.findall(r"(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일", remainder))

    dates: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        parsed = parse_date(clean_text(candidate).replace("/", ".").replace("-", "."))
        if not parsed:
            continue
        iso = parsed.isoformat()
        if iso not in seen:
            seen.add(iso)
            dates.append(iso)
    return sorted(dates)


def provider_course_id_from_row(row: dict[str, Any]) -> str:
    existing = clean_text(row.get("provider_course_id"))
    if existing:
        return existing[:100]

    raw_url = clean_text(row.get("raw_url"))
    if raw_url:
        parsed = urlparse(raw_url)
        path_token = parsed.path.rstrip("/").split("/")[-1]
        query_token = parsed.query.replace("&", "_").replace("=", "-")
        candidate = path_token or query_token
        if query_token and path_token:
            candidate = f"{path_token}:{query_token}"
        if candidate:
            return candidate[:100]

    title = clean_text(row.get("title"))
    branch = clean_text(row.get("branch"))
    return f"{branch}:{title}"[:100]


def normalize_status(*values: Any) -> str:
    status = infer_course_status(*values, default="")
    if status:
        return status

    text = clean_text(" ".join(str(value or "") for value in values))
    if any(token in text for token in ("마감", "종료")):
        return "CLOSED"
    if any(token in text for token in ("대기", "wait")):
        return "WAITING"
    if any(token in text for token in ("예정", "scheduled")):
        return "SCHEDULED"
    return "OPEN"


def extract_age_target_from_schedule(value: Any) -> str:
    text = clean_text(value)
    if not text:
        return ""
    patterns = [
        r"\(([^)]*(?:\uac1c\uc6d4|\ub144\uc0dd|\uc138|\ucd08\ub4f1|\uc911\ub4f1|\uace0\ub4f1)[^)]*)\)",
        r"(\d{1,3}\s*[~-]\s*\d{1,3}\s*\uac1c\uc6d4)",
        r"(\d{1,3}\s*\uac1c\uc6d4\s*(?:\uc774\uc0c1|\uc774\ud558|\ubd80\ud130|\uae4c\uc9c0)?)",
        r"(\d{2,4}\s*[~-]\s*\d{2,4}\s*\ub144\uc0dd)",
        r"(\d{1,2}\s*[~-]\s*\d{1,2}\s*\uc138)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return clean_text(match.group(1))
    return ""


def remove_age_target_from_schedule(value: Any, target: str) -> str:
    text = clean_text(value)
    if not text or not target:
        return text
    escaped = re.escape(target)
    text = re.sub(rf"\s*\(\s*{escaped}\s*\)\s*", " ", text)
    text = re.sub(rf"\s*{escaped}\s*", " ", text)
    return clean_text(text)


def month_range_from_target(value: Any) -> tuple[Optional[int], Optional[int]]:
    text = clean_text(value)
    if not text:
        return None, None
    match = re.search(r"(\d{1,3})\s*[~-]\s*(\d{1,3})\s*\uac1c\uc6d4", text)
    if match:
        return int(match.group(1)), int(match.group(2))
    match = re.search(r"(\d{1,3})\s*\uac1c\uc6d4\s*(?:\uc774\uc0c1|\ubd80\ud130)", text)
    if match:
        return int(match.group(1)), None
    match = re.search(r"(\d{1,3})\s*\uac1c\uc6d4\s*(?:\uc774\ud558|\uae4c\uc9c0)", text)
    if match:
        return 0, int(match.group(1))
    match = re.search(r"(\d{1,3})\s*\uac1c\uc6d4", text)
    if match:
        month = int(match.group(1))
        return month, month
    return None, None


class YamlSourceCrawler:
    def __init__(self, provider: str):
        if provider not in SUPPORTED_PROVIDERS:
            raise ValueError(f"Unsupported YAML source provider: {provider}")
        self.provider = provider
        self.target_parser = TargetParser()
        self.schedule_parser = ScheduleParser()

    def save_branch(self, branch_code: str, name: str) -> Optional[str]:
        branch = {
            "provider": self.provider,
            "branch_code": branch_code[:50] or self.provider,
            "name": name[:100] or self.provider,
            "address": "",
            "phone": "",
        }
        with get_db_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO branches (provider, branch_code, name, address, phone)
                VALUES (%(provider)s, %(branch_code)s, %(name)s, %(address)s, %(phone)s)
                ON CONFLICT (provider, branch_code)
                DO UPDATE SET
                    name = EXCLUDED.name,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING id
                """,
                branch,
            )
            return str(cursor.fetchone()["id"])

    def normalize_course(self, row: dict[str, Any], branch_id: str) -> dict[str, Any]:
        raw_title = clean_text(row.get("title")) or "제목 없음"
        title, removed_title_prefix = clean_course_title(raw_title)
        description = clean_text(row.get("description"))
        category_raw = clean_text(row.get("category") or row.get("category_raw") or row.get("course_type")) or None
        schedule_raw_original = clean_text(row.get("schedule_raw")) or None
        schedule_age_target = extract_age_target_from_schedule(schedule_raw_original)
        row_age_target = clean_text(row.get("age"))
        row_age_min_month, row_age_max_month = explicit_age_month_range(row_age_target)
        row_age_is_explicit = row_age_min_month is not None or row_age_max_month is not None
        explicit_target = (
            (row_age_target if row_age_is_explicit else "")
            or clean_text(row.get("target"))
            or extract_target_text(raw_title)
            or row_age_target
            or schedule_age_target
        )

        # Detail descriptions often contain unrelated dates or audience notes.
        # Use only title/target/category for age fields to avoid false age groups.
        target_source = " ".join(
            part
            for part in [raw_title, title, explicit_target or "", row_age_target or "", category_raw or ""]
            if part
        )
        parsed_target = parse_crawler_target(target_source, self.target_parser)
        schedule_min_month, schedule_max_month = explicit_age_month_range(schedule_age_target)
        if row_age_is_explicit:
            parsed_target["min_age"] = row_age_min_month
            parsed_target["max_age"] = row_age_max_month
            parsed_target["age_group"] = parsed_target.get("age_group")
            parsed_target["age_is_explicit"] = True
        elif schedule_min_month is not None or schedule_max_month is not None:
            parsed_target["min_age"] = schedule_min_month
            parsed_target["max_age"] = schedule_max_month
            parsed_target["age_is_explicit"] = True
        if not parsed_target.get("age_group"):
            parsed_target["age_group"] = infer_age_group_from_category(category_raw)
        if parsed_target.get("age_group") == "ADULT" and not parsed_target.get("age_is_explicit"):
            min_age = parsed_target.get("min_age")
            max_age = parsed_target.get("max_age")
            if (min_age is not None and min_age > 150) or (max_age is not None and max_age > 150):
                parsed_target["min_age"] = None
                parsed_target["max_age"] = None

        schedule_raw = remove_age_target_from_schedule(schedule_raw_original, schedule_age_target) or None
        parsed_schedule = self.schedule_parser.parse(schedule_raw or "")
        if parsed_schedule.get("duration_minutes") is not None and parsed_schedule["duration_minutes"] <= 0:
            parsed_schedule["duration_minutes"] = None
        if parsed_schedule.get("time_start") == parsed_schedule.get("time_end"):
            parsed_schedule["time_start"] = None
            parsed_schedule["time_end"] = None

        start_date, end_date = parse_date_range(row.get("period"))
        default_year = start_date.year if start_date else None
        schedule_dates = parse_schedule_dates(
            row.get("schedule_dates")
            or row.get("calendar_dates")
            or row.get("class_dates")
            or row.get("date_list"),
            default_year,
        )
        if not schedule_dates:
            schedule_dates = parse_schedule_dates(row.get("period"), default_year)
        if schedule_dates:
            start_date = parse_date(schedule_dates[0])
            end_date = parse_date(schedule_dates[-1])
        sessions = row.get("sessions") or (len(schedule_dates) if schedule_dates else 0)
        material_fee = row.get("material_fee")
        if not isinstance(material_fee, int):
            material_fee = extract_krw_amount(material_fee)
        if not material_fee:
            material_fee = extract_material_fee_amount(
                row.get("material_fee"),
                row.get("material_note"),
                description,
            )

        course = {
            "branch_id": branch_id,
            "provider": self.provider,
            "provider_course_id": provider_course_id_from_row(row),
            "title": title[:255],
            "title_raw": raw_title[:255],
            "title_prefix_removed": removed_title_prefix or None,
            "instructor": clean_instructor_name(row.get("instructor")),
            "target": explicit_target[:100] if explicit_target else None,
            "category_raw": category_raw[:100] if category_raw else None,
            "fee": extract_krw_amount(row.get("fee")),
            "material_fee": material_fee,
            "sessions": sessions,
            "schedule_raw": schedule_raw,
            "schedule_dates": schedule_dates,
            "start_date": start_date,
            "end_date": end_date,
            "apply_start": None,
            "apply_end": None,
            "status": normalize_status(row.get("status"), raw_title, description),
            "raw_url": clean_text(row.get("raw_url")) or None,
            "description": description or None,
            "image_url": clean_text(row.get("image_url")) or None,
            "target_age_group": parsed_target["age_group"],
            "target_min_age": parsed_target["min_age"],
            "target_max_age": parsed_target["max_age"],
            "target_with_parent": parsed_target["with_parent"],
            "target_tags": parsed_target["tags"],
            "target_age_is_explicit": parsed_target.get("age_is_explicit", False),
            "schedule_days": parsed_schedule["days"],
            "schedule_time_start": parsed_schedule["time_start"],
            "schedule_time_end": parsed_schedule["time_end"],
            "schedule_frequency": parsed_schedule["frequency"],
            "schedule_duration_minutes": parsed_schedule["duration_minutes"],
        }
        enrich_course_lifecycle(course)
        course["schedule_dates"] = Json(schedule_dates) if schedule_dates else None
        return course

    def save_course(self, course: dict[str, Any]) -> bool:
        if should_skip_expired_course(course):
            logger.info("Skipping expired %s course: %s", self.provider, course.get("title"))
            return False
        with get_db_cursor() as cursor:
            coalesce_provider_course_id_by_raw_url(cursor, course, logger)
            cursor.execute(
                """
                INSERT INTO courses (
                    branch_id, provider, provider_course_id, title, title_raw, title_prefix_removed, instructor,
                    target, category_raw, fee, material_fee, sessions, schedule_raw, schedule_dates,
                    start_date, end_date, apply_start, apply_end, status, raw_url,
                    description, image_url,
                    is_active, first_seen_at, last_seen_at, removed_at, content_hash, change_detected_at,
                    target_age_group, target_min_age, target_max_age,
                    target_with_parent, target_tags, target_age_is_explicit,
                    schedule_days, schedule_time_start, schedule_time_end,
                    schedule_frequency, schedule_duration_minutes
                )
                VALUES (
                    %(branch_id)s, %(provider)s, %(provider_course_id)s, %(title)s,
                    %(title_raw)s, %(title_prefix_removed)s,
                    %(instructor)s, %(target)s, %(category_raw)s, %(fee)s,
                    %(material_fee)s, %(sessions)s, %(schedule_raw)s, %(schedule_dates)s, %(start_date)s,
                    %(end_date)s, %(apply_start)s, %(apply_end)s, %(status)s,
                    %(raw_url)s, %(description)s, %(image_url)s,
                    TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL, %(content_hash)s, NULL,
                    %(target_age_group)s, %(target_min_age)s, %(target_max_age)s,
                    %(target_with_parent)s, %(target_tags)s, %(target_age_is_explicit)s,
                    %(schedule_days)s, %(schedule_time_start)s, %(schedule_time_end)s,
                    %(schedule_frequency)s, %(schedule_duration_minutes)s
                )
                ON CONFLICT (provider, provider_course_id)
                DO UPDATE SET
                    branch_id = EXCLUDED.branch_id,
                    title = CASE
                        WHEN COALESCE(courses.ai_title_processed, FALSE)
                         AND courses.title_raw IS NOT DISTINCT FROM EXCLUDED.title_raw
                        THEN courses.title
                        ELSE EXCLUDED.title
                    END,
                    title_raw = EXCLUDED.title_raw,
                    title_prefix_removed = CASE
                        WHEN COALESCE(courses.ai_title_processed, FALSE)
                         AND courses.title_raw IS NOT DISTINCT FROM EXCLUDED.title_raw
                        THEN courses.title_prefix_removed
                        ELSE EXCLUDED.title_prefix_removed
                    END,
                    instructor = EXCLUDED.instructor,
                    target = CASE
                        WHEN %(target_age_is_explicit)s AND EXCLUDED.target IS NOT NULL
                        THEN EXCLUDED.target
                        WHEN COALESCE(courses.ai_title_processed, FALSE)
                         AND courses.title_raw IS NOT DISTINCT FROM EXCLUDED.title_raw
                        THEN courses.target
                        ELSE EXCLUDED.target
                    END,
                    category_raw = EXCLUDED.category_raw,
                    fee = EXCLUDED.fee,
                    material_fee = EXCLUDED.material_fee,
                    sessions = EXCLUDED.sessions,
                    schedule_raw = EXCLUDED.schedule_raw,
                    schedule_dates = EXCLUDED.schedule_dates,
                    start_date = EXCLUDED.start_date,
                    end_date = EXCLUDED.end_date,
                    status = EXCLUDED.status,
                    raw_url = EXCLUDED.raw_url,
                    description = COALESCE(EXCLUDED.description, courses.description),
                    image_url = COALESCE(EXCLUDED.image_url, courses.image_url),
                    is_active = TRUE,
                    last_seen_at = CURRENT_TIMESTAMP,
                    removed_at = NULL,
                    change_detected_at = CASE
                        WHEN courses.content_hash IS DISTINCT FROM EXCLUDED.content_hash THEN CURRENT_TIMESTAMP
                        ELSE courses.change_detected_at
                    END,
                    content_hash = EXCLUDED.content_hash,
                    target_age_group = CASE WHEN %(target_age_is_explicit)s OR COALESCE(courses.target_age_is_explicit, FALSE) THEN EXCLUDED.target_age_group ELSE COALESCE(courses.target_age_group, EXCLUDED.target_age_group) END,
                    target_min_age = CASE WHEN %(target_age_is_explicit)s OR COALESCE(courses.target_age_is_explicit, FALSE) THEN EXCLUDED.target_min_age ELSE COALESCE(courses.target_min_age, EXCLUDED.target_min_age) END,
                    target_max_age = CASE WHEN %(target_age_is_explicit)s OR COALESCE(courses.target_age_is_explicit, FALSE) THEN EXCLUDED.target_max_age ELSE COALESCE(courses.target_max_age, EXCLUDED.target_max_age) END,
                    target_with_parent = CASE WHEN %(target_age_is_explicit)s OR COALESCE(courses.target_age_is_explicit, FALSE) THEN EXCLUDED.target_with_parent ELSE COALESCE(courses.target_with_parent, EXCLUDED.target_with_parent) END,
                    target_tags = CASE WHEN %(target_age_is_explicit)s OR COALESCE(courses.target_age_is_explicit, FALSE) THEN EXCLUDED.target_tags ELSE COALESCE(courses.target_tags, EXCLUDED.target_tags) END,
                    target_age_is_explicit = EXCLUDED.target_age_is_explicit,
                    ai_category = CASE
                        WHEN courses.title_raw IS DISTINCT FROM EXCLUDED.title_raw
                          OR courses.category_raw IS DISTINCT FROM EXCLUDED.category_raw
                          OR COALESCE(EXCLUDED.description, courses.description) IS DISTINCT FROM courses.description
                        THEN NULL
                        ELSE courses.ai_category
                    END,
                    ai_tags = CASE
                        WHEN courses.title_raw IS DISTINCT FROM EXCLUDED.title_raw
                          OR courses.category_raw IS DISTINCT FROM EXCLUDED.category_raw
                          OR COALESCE(EXCLUDED.description, courses.description) IS DISTINCT FROM courses.description
                        THEN NULL
                        ELSE courses.ai_tags
                    END,
                    ai_summary = CASE
                        WHEN courses.title_raw IS DISTINCT FROM EXCLUDED.title_raw
                          OR courses.category_raw IS DISTINCT FROM EXCLUDED.category_raw
                          OR COALESCE(EXCLUDED.description, courses.description) IS DISTINCT FROM courses.description
                        THEN NULL
                        ELSE courses.ai_summary
                    END,
                    is_ai_processed = CASE
                        WHEN courses.title_raw IS DISTINCT FROM EXCLUDED.title_raw
                          OR courses.category_raw IS DISTINCT FROM EXCLUDED.category_raw
                          OR COALESCE(EXCLUDED.description, courses.description) IS DISTINCT FROM courses.description
                        THEN FALSE
                        ELSE courses.is_ai_processed
                    END,
                    ai_title_processed = CASE
                        WHEN courses.title_raw IS DISTINCT FROM EXCLUDED.title_raw
                          OR courses.category_raw IS DISTINCT FROM EXCLUDED.category_raw
                        THEN FALSE
                        ELSE courses.ai_title_processed
                    END,
                    ai_title_confidence = CASE
                        WHEN courses.title_raw IS DISTINCT FROM EXCLUDED.title_raw
                          OR courses.category_raw IS DISTINCT FROM EXCLUDED.category_raw
                        THEN NULL
                        ELSE courses.ai_title_confidence
                    END,
                    ai_title_result = CASE
                        WHEN courses.title_raw IS DISTINCT FROM EXCLUDED.title_raw
                          OR courses.category_raw IS DISTINCT FROM EXCLUDED.category_raw
                        THEN NULL
                        ELSE courses.ai_title_result
                    END,
                    schedule_days = EXCLUDED.schedule_days,
                    schedule_time_start = EXCLUDED.schedule_time_start,
                    schedule_time_end = EXCLUDED.schedule_time_end,
                    schedule_frequency = EXCLUDED.schedule_frequency,
                    schedule_duration_minutes = EXCLUDED.schedule_duration_minutes,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING id
                """,
                course,
            )
        return True

    def run(
        self,
        limit: Optional[int] = None,
        mark_stale: bool = False,
        branch_code: Optional[str] = None,
        branch_name: Optional[str] = None,
    ) -> int:
        collector = COLLECTORS[self.provider]
        collector_limit = limit
        if collector_limit is None:
            collector_limit = 100000 if self.provider in {"AK_PLAZA", "LOTTE_MART", "SHINSEGAE_ACADEMY"} else 1000
        rows, pages, note = collector(collector_limit)
        if note:
            logger.warning("%s collector note: %s", self.provider, note)
        if not rows:
            logger.error("%s collected 0 rows. Skipping DB write and stale cleanup.", self.provider)
            return 0

        if branch_code or branch_name:
            before = len(rows)
            code_filter = clean_text(branch_code)
            name_filter = clean_text(branch_name)
            rows = [
                row
                for row in rows
                if (
                    not code_filter
                    or clean_text(row.get("branch_code") or row.get("store_code") or row.get("branch")) == code_filter
                )
                and (
                    not name_filter
                    or clean_text(row.get("branch")) == name_filter
                )
            ]
            logger.info("%s branch filter applied: %s -> %s rows.", self.provider, before, len(rows))
            if not rows:
                logger.error("%s branch filter matched 0 rows. Skipping DB write.", self.provider)
                return 0

        logger.info("%s collected %s rows from %s pages.", self.provider, len(rows), pages)
        saved = 0
        branch_ids: dict[str, str] = {}
        for row in rows:
            branch_name = clean_text(row.get("branch")) or self.provider
            branch_code = clean_text(row.get("branch_code")) or branch_name
            if self.provider == "GALLERIA":
                branch_code = clean_text(row.get("branch_code")) or branch_name.lower()
            elif self.provider == "AK_PLAZA":
                branch_code = clean_text(row.get("branch_code")) or clean_text(row.get("store_code")) or branch_name or self.provider

            branch_id = branch_ids.get(branch_code)
            if not branch_id:
                branch_id = self.save_branch(branch_code, branch_name)
                if not branch_id:
                    logger.warning("Failed to save branch: %s", branch_name)
                    continue
                branch_ids[branch_code] = branch_id

            course = self.normalize_course(row, branch_id)
            try:
                if self.save_course(course):
                    saved += 1
            except Exception as exc:
                logger.error("Failed to save %s course %s: %s", self.provider, course.get("provider_course_id"), exc)

        if mark_stale and saved > 0 and limit is None:
            stale_count = mark_stale_courses(self.provider, utc_now())
            logger.info("Marked stale %s courses inactive: %s", self.provider, stale_count)

        with get_db_cursor() as cursor:
            delete_empty_branches_for_provider(cursor, self.provider, logger)

        logger.info("%s saved %s/%s rows.", self.provider, saved, len(rows))
        return saved


def main() -> int:
    parser = argparse.ArgumentParser(description="Production DB crawler for YAML-discovered sources")
    parser.add_argument("--provider", required=True, choices=sorted(SUPPORTED_PROVIDERS))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--mark-stale", action="store_true")
    parser.add_argument("--branch-code", help="Only save rows matching this branch/store code")
    parser.add_argument("--branch-name", help="Only save rows matching this branch name")
    args = parser.parse_args()

    started = datetime.now()
    saved = YamlSourceCrawler(args.provider).run(
        limit=args.limit,
        mark_stale=args.mark_stale,
        branch_code=args.branch_code,
        branch_name=args.branch_name,
    )
    elapsed = (datetime.now() - started).total_seconds()
    logger.info("%s completed. saved=%s elapsed=%.1fs", args.provider, saved, elapsed)
    return 0 if saved > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
