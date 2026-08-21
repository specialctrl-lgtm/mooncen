from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from DB.db_utils import get_db_cursor


SEOUL = ZoneInfo("Asia/Seoul")


CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS user_favorite_courses (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id UUID REFERENCES courses(id) ON DELETE SET NULL,
    course_url TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT unique_user_favorite_course_url UNIQUE(user_id, course_url)
);

CREATE INDEX IF NOT EXISTS idx_user_favorite_courses_user
    ON user_favorite_courses(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_favorite_courses_course
    ON user_favorite_courses(course_id)
    WHERE course_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS course_alerts (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id UUID REFERENCES courses(id) ON DELETE SET NULL,
    course_url TEXT,
    alert_type TEXT NOT NULL,
    alert_status TEXT NOT NULL DEFAULT 'pending',
    scheduled_at TIMESTAMPTZ,
    sent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_course_alert_type
        CHECK (alert_type IN ('registration_open', 'registration_closing', 'seat_available', 'new_course')),
    CONSTRAINT chk_course_alert_status
        CHECK (alert_status IN ('pending', 'sent', 'skipped', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_course_alerts_user_status
    ON course_alerts(user_id, alert_status, scheduled_at DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_course_alerts_scheduled_pending
    ON course_alerts(scheduled_at)
    WHERE alert_status = 'pending';
CREATE UNIQUE INDEX IF NOT EXISTS ux_course_alerts_user_course_type
    ON course_alerts(user_id, course_url, alert_type)
    WHERE course_url IS NOT NULL AND btrim(course_url) <> '';
"""


def ensure_tables() -> None:
    with get_db_cursor() as cursor:
        cursor.execute(CREATE_TABLES_SQL)


def normalize_course_url(row: dict[str, Any]) -> str:
    for key in ("course_url", "raw_url", "application_url"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    course_id = row.get("course_id")
    if course_id:
        return f"course:{course_id}"
    raise ValueError("favorite row has neither URL nor course_id")


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    if not text:
        return None
    return date.fromisoformat(text[:10])


def alert_types_for_course(row: dict[str, Any], today: date, days: int = 1) -> list[tuple[str, date]]:
    alerts: list[tuple[str, date]] = []
    apply_start = _as_date(row.get("apply_start") or row.get("registration_start_date"))
    apply_end = _as_date(row.get("apply_end") or row.get("registration_end_date"))
    if apply_start:
        delta = (apply_start - today).days
        if 0 <= delta <= days:
            alerts.append(("registration_open", apply_start))
    if apply_end:
        delta = (apply_end - today).days
        if 0 <= delta <= days:
            alerts.append(("registration_closing", apply_end))
    return alerts


def scheduled_at_for(event_date: date, now: datetime) -> datetime:
    if event_date <= now.date():
        return now
    return datetime.combine(event_date, time(hour=9), tzinfo=SEOUL)


def fetch_favorite_courses(limit: int | None = None) -> list[dict[str, Any]]:
    limit_sql = "LIMIT %s" if limit else ""
    params: list[Any] = [limit] if limit else []
    with get_db_cursor() as cursor:
        cursor.execute(
            f"""
            WITH favorite_rows AS (
                SELECT
                    f.user_id,
                    f.course_id,
                    f.course_url,
                    f.created_at
                FROM user_favorite_courses f
                UNION
                SELECT
                    m.user_id,
                    m.course_id,
                    COALESCE(NULLIF(c.raw_url, ''), NULLIF(c.application_url, ''), 'course:' || c.id::text) AS course_url,
                    m.created_at
                FROM user_course_marks m
                JOIN courses c ON c.id = m.course_id
                WHERE m.mark_type = 'favorite'
            )
            SELECT
                fr.user_id,
                fr.course_id,
                fr.course_url,
                c.raw_url,
                c.application_url,
                c.title,
                c.apply_start,
                c.apply_end,
                c.status
            FROM favorite_rows fr
            LEFT JOIN courses c ON c.id = fr.course_id
            ORDER BY fr.created_at DESC NULLS LAST
            {limit_sql}
            """,
            params,
        )
        return [dict(row) for row in cursor.fetchall()]


def insert_alert(row: dict[str, Any], alert_type: str, scheduled_at: datetime, dry_run: bool = False) -> bool:
    if dry_run:
        return True
    course_url = normalize_course_url(row)
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO course_alerts (
                user_id, course_id, course_url, alert_type, alert_status, scheduled_at
            )
            VALUES (%s, %s, %s, %s, 'pending', %s)
            ON CONFLICT (user_id, course_url, alert_type)
                WHERE course_url IS NOT NULL AND btrim(course_url) <> ''
            DO NOTHING
            """,
            (row.get("user_id"), row.get("course_id"), course_url, alert_type, scheduled_at),
        )
        return bool(cursor.rowcount)


def generate_alerts(limit: int | None = None, days: int = 1, dry_run: bool = False) -> dict[str, Any]:
    ensure_tables()
    now = datetime.now(SEOUL)
    today = now.date()
    rows = fetch_favorite_courses(limit)
    counters: Counter[str] = Counter()
    created = 0
    skipped_duplicates = 0
    samples: list[dict[str, Any]] = []

    for row in rows:
        for alert_type, event_date in alert_types_for_course(row, today, days):
            schedule_time = scheduled_at_for(event_date, now)
            inserted = insert_alert(row, alert_type, schedule_time, dry_run=dry_run)
            counters.update([alert_type])
            if inserted:
                created += 1
            else:
                skipped_duplicates += 1
            if len(samples) < 10:
                samples.append(
                    {
                        "user_id": str(row.get("user_id")),
                        "course_id": str(row.get("course_id")) if row.get("course_id") else None,
                        "course_url": normalize_course_url(row),
                        "alert_type": alert_type,
                        "scheduled_at": schedule_time.isoformat(),
                        "inserted": inserted,
                    }
                )

    return {
        "ok": True,
        "dry_run": dry_run,
        "favorites_checked": len(rows),
        "candidate_alerts": sum(counters.values()),
        "created": created,
        "skipped_duplicates": skipped_duplicates,
        "by_type": dict(counters),
        "samples": samples,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate pending alerts for favorite MoonCen courses.")
    parser.add_argument("--limit", type=int, default=None, help="Optional favorite row limit.")
    parser.add_argument("--days", type=int, default=1, help="Lookahead window in days.")
    parser.add_argument("--dry-run", action="store_true", help="Do not write course_alerts rows.")
    args = parser.parse_args()

    result = generate_alerts(limit=args.limit, days=max(0, args.days), dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
