from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from typing import Any


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from DB.db_utils import get_db_cursor


FIELD_WEIGHTS = {
    "title": 15,
    "branch_name": 15,
    "url": 15,
    "period": 15,
    "time": 10,
    "price": 5,
    "category": 10,
    "target_age": 10,
    "location": 5,
}


CREATE_QUALITY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS course_quality_score (
    id BIGSERIAL PRIMARY KEY,
    course_id UUID REFERENCES courses(id) ON DELETE CASCADE,
    url TEXT,
    provider TEXT,
    title TEXT,
    total_score INTEGER NOT NULL DEFAULT 0,
    grade TEXT NOT NULL DEFAULT 'bad',
    missing_fields TEXT[] NOT NULL DEFAULT '{}'::text[],
    checked_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_course_quality_score_grade
        CHECK (grade IN ('good', 'warning', 'bad'))
)
"""


CREATE_QUALITY_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_course_quality_score_course_id
    ON course_quality_score(course_id)
    WHERE course_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_course_quality_score_url
    ON course_quality_score(url)
    WHERE url IS NOT NULL AND btrim(url) <> '';
CREATE INDEX IF NOT EXISTS idx_course_quality_score_checked
    ON course_quality_score(checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_course_quality_score_grade
    ON course_quality_score(grade);
CREATE INDEX IF NOT EXISTS idx_course_quality_score_provider_grade
    ON course_quality_score(provider, grade);
"""


ALTER_QUALITY_TABLE_SQL = """
ALTER TABLE course_quality_score ADD COLUMN IF NOT EXISTS course_id UUID REFERENCES courses(id) ON DELETE CASCADE;
ALTER TABLE course_quality_score ADD COLUMN IF NOT EXISTS url TEXT;
ALTER TABLE course_quality_score ADD COLUMN IF NOT EXISTS provider TEXT;
ALTER TABLE course_quality_score ADD COLUMN IF NOT EXISTS title TEXT;
ALTER TABLE course_quality_score ADD COLUMN IF NOT EXISTS total_score INTEGER NOT NULL DEFAULT 0;
ALTER TABLE course_quality_score ADD COLUMN IF NOT EXISTS grade TEXT NOT NULL DEFAULT 'bad';
ALTER TABLE course_quality_score ADD COLUMN IF NOT EXISTS missing_fields TEXT[] NOT NULL DEFAULT '{}'::text[];
ALTER TABLE course_quality_score ADD COLUMN IF NOT EXISTS checked_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE course_quality_score ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP;
"""


def has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def first_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if has_value(value):
            return value
    return None


def grade_for_score(score: int) -> str:
    if score >= 80:
        return "good"
    if score >= 50:
        return "warning"
    return "bad"


def calculate_quality_score(row: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "title": first_value(row, "title"),
        "branch_name": first_value(row, "branch_name", "branch_id"),
        "url": first_value(row, "url", "raw_url", "application_url"),
        "period": first_value(row, "start_date", "end_date", "apply_start", "apply_end", "period", "date"),
        "time": first_value(row, "schedule_raw", "schedule_days", "schedule_time_start", "schedule_time_end", "time"),
        "price": first_value(row, "fee", "material_fee", "price"),
        "category": first_value(row, "domain_category", "collection_category", "category_raw", "ai_category", "category"),
        "target_age": first_value(row, "target_age_group", "target_min_age", "target_max_age", "target"),
        "location": first_value(row, "venue_name", "venue_address", "branch_address", "lat", "lon"),
    }
    missing_fields = [field for field, value in checks.items() if not has_value(value)]
    score = sum(weight for field, weight in FIELD_WEIGHTS.items() if field not in missing_fields)
    return {
        "total_score": int(score),
        "grade": grade_for_score(int(score)),
        "missing_fields": missing_fields,
    }


def ensure_quality_table() -> None:
    with get_db_cursor() as cursor:
        cursor.execute(CREATE_QUALITY_TABLE_SQL)
        cursor.execute(ALTER_QUALITY_TABLE_SQL)
        cursor.execute(CREATE_QUALITY_INDEX_SQL)


def fetch_courses(limit: int, source: str = "", bad_only: bool = False) -> list[dict[str, Any]]:
    where = ["c.is_active IS TRUE"]
    params: list[Any] = []
    if source:
        where.append("c.provider = %s")
        params.append(source)
    if bad_only:
        where.append("(q.grade IS NULL OR q.grade <> 'good')")
    params.append(limit)
    where_sql = " AND ".join(where)
    with get_db_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT
                c.id AS course_id,
                c.provider,
                c.title,
                COALESCE(NULLIF(c.raw_url, ''), NULLIF(c.application_url, '')) AS url,
                c.raw_url,
                c.application_url,
                c.branch_id,
                b.name AS branch_name,
                b.address AS branch_address,
                b.lat,
                b.lon,
                c.start_date,
                c.end_date,
                c.apply_start,
                c.apply_end,
                c.schedule_raw,
                c.schedule_days,
                c.schedule_time_start,
                c.schedule_time_end,
                c.fee,
                c.material_fee,
                c.domain_category,
                c.collection_category,
                c.category_raw,
                c.ai_category,
                c.target,
                c.target_age_group,
                c.target_min_age,
                c.target_max_age,
                c.venue_name,
                c.venue_address
            FROM courses c
            LEFT JOIN branches b ON b.id = c.branch_id
            LEFT JOIN course_quality_score q ON q.course_id = c.id
            WHERE {where_sql}
            ORDER BY c.updated_at DESC NULLS LAST, c.created_at DESC NULLS LAST
            LIMIT %s
            """,
            params,
        )
        return [dict(row) for row in cursor.fetchall()]


def upsert_quality_row(row: dict[str, Any], quality: dict[str, Any]) -> None:
    course_id = row.get("course_id")
    url = first_value(row, "url", "raw_url", "application_url")
    provider = row.get("provider")
    title = row.get("title")
    params = {
        "course_id": course_id,
        "url": url,
        "provider": provider,
        "title": title,
        "total_score": quality["total_score"],
        "grade": quality["grade"],
        "missing_fields": quality["missing_fields"],
    }
    if course_id:
        conflict = "(course_id) WHERE course_id IS NOT NULL"
    else:
        conflict = "(url) WHERE url IS NOT NULL AND btrim(url) <> ''"

    with get_db_cursor() as cursor:
        cursor.execute(
            f"""
            INSERT INTO course_quality_score (
                course_id, url, provider, title, total_score, grade, missing_fields, checked_at
            )
            VALUES (
                %(course_id)s, %(url)s, %(provider)s, %(title)s,
                %(total_score)s, %(grade)s, %(missing_fields)s, CURRENT_TIMESTAMP
            )
            ON CONFLICT {conflict}
            DO UPDATE SET
                url = EXCLUDED.url,
                provider = EXCLUDED.provider,
                title = EXCLUDED.title,
                total_score = EXCLUDED.total_score,
                grade = EXCLUDED.grade,
                missing_fields = EXCLUDED.missing_fields,
                checked_at = CURRENT_TIMESTAMP
            """,
            params,
        )


def calculate_and_store(limit: int, source: str = "", bad_only: bool = False) -> dict[str, Any]:
    ensure_quality_table()
    rows = fetch_courses(limit, source, bad_only)
    grades: Counter[str] = Counter()
    missing: Counter[str] = Counter()
    for row in rows:
        quality = calculate_quality_score(row)
        grades.update([quality["grade"]])
        missing.update(quality["missing_fields"])
        upsert_quality_row(row, quality)
    return {
        "checked": len(rows),
        "grades": dict(grades),
        "missing_fields": dict(missing.most_common()),
        "source": source or "ALL",
        "bad_only": bad_only,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Calculate MoonCen course data quality scores.")
    parser.add_argument("--limit", type=int, default=100, help="Maximum active courses to scan.")
    parser.add_argument("--source", default="", help="Optional provider filter, for example HOMEPLUS.")
    parser.add_argument("--bad-only", action="store_true", help="Only rescan rows that are not currently good.")
    args = parser.parse_args()

    result = calculate_and_store(max(args.limit, 1), args.source.strip().upper(), args.bad_only)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
