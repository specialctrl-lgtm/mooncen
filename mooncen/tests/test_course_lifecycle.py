from __future__ import annotations

from datetime import date

from DB.course_lifecycle import (
    apply_ended_course_lifecycle,
    effective_course_status,
    enrich_course_lifecycle,
    is_application_period_ended,
    is_course_closed_by_date,
    is_course_period_ended,
    mark_stale_courses,
)


class RecordingCursor:
    def __init__(self, rowcounts: list[int]) -> None:
        self._rowcounts = iter(rowcounts)
        self.rowcount = 0
        self.calls: list[tuple[str, dict]] = []

    def execute(self, query: str, params: dict) -> None:
        self.calls.append((" ".join(query.split()), params))
        self.rowcount = next(self._rowcounts)


def test_course_period_ends_on_the_day_after_end_date() -> None:
    assert is_course_period_ended(
        {"end_date": "2026-07-26"},
        reference_date=date(2026, 7, 27),
    )
    assert not is_course_period_ended(
        {"end_date": "2026-07-27"},
        reference_date=date(2026, 7, 27),
    )
    assert not is_course_period_ended(
        {"start_date": "2026-07-26", "end_date": None},
        reference_date=date(2026, 7, 27),
    )


def test_lifecycle_hash_uses_closed_status_for_ended_course() -> None:
    course = {
        "title": "Ended course",
        "end_date": "2000-01-01",
        "status": "OPEN",
        "reservation_available": True,
    }

    enrich_course_lifecycle(course)

    assert course["status"] == "CLOSED"
    assert course["reservation_available"] is False
    assert len(course["content_hash"]) == 64


def test_application_period_closes_open_status_on_the_following_seoul_day() -> None:
    course = {
        "status": "OPEN",
        "apply_end": "2026-08-07",
        "end_date": "2026-12-31",
    }

    assert is_application_period_ended(course, reference_date=date(2026, 8, 8))
    assert is_course_closed_by_date(course, reference_date=date(2026, 8, 8))
    assert effective_course_status(course, reference_date=date(2026, 8, 8)) == "CLOSED"

    assert not is_application_period_ended(course, reference_date=date(2026, 8, 7))
    assert effective_course_status(course, reference_date=date(2026, 8, 7)) == "OPEN"


def test_application_period_does_not_guess_waitlist_or_scheduled_closure() -> None:
    for status in ("WAITING", "SCHEDULED", "CLOSED"):
        course = {"status": status, "apply_end": "2026-08-07", "end_date": None}

        assert not is_application_period_ended(
            course,
            reference_date=date(2026, 8, 8),
        )
        assert effective_course_status(
            course,
            reference_date=date(2026, 8, 8),
        ) == status


def test_lifecycle_hash_closes_expired_open_application() -> None:
    course = {
        "title": "Registration ended",
        "status": "DEADLINE",
        "apply_end": "2000-01-01",
        "end_date": "2099-12-31",
        "reservation_available": True,
    }

    enrich_course_lifecycle(course)

    assert course["status"] == "CLOSED"
    assert course["reservation_available"] is False


def test_ended_course_lifecycle_closes_then_deactivates() -> None:
    cursor = RecordingCursor([12, 4])

    result = apply_ended_course_lifecycle(
        grace_days=7,
        provider="EMART",
        cursor=cursor,
    )

    assert result == {"closed": 12, "deactivated": 4}
    assert len(cursor.calls) == 2
    close_query, close_params = cursor.calls[0]
    deactivate_query, deactivate_params = cursor.calls[1]
    assert "end_date < (NOW() AT TIME ZONE 'Asia/Seoul')::date" in close_query
    assert "status IN ('OPEN', 'DEADLINE')" in close_query
    assert "apply_end < (NOW() AT TIME ZONE 'Asia/Seoul')::date" in close_query
    assert "reservation_available = FALSE" in close_query
    assert "status IS DISTINCT FROM 'CLOSED'" in close_query
    assert "SET is_active = FALSE, status = 'CLOSED'" in deactivate_query
    assert close_params == {"provider": "EMART"}
    assert deactivate_params == {"grace_days": 7, "provider": "EMART"}


def test_stale_cleanup_can_be_isolated_to_one_source_endpoint() -> None:
    cursor = RecordingCursor([3])

    changed = mark_stale_courses(
        "MUNI_MULTI",
        cutoff="crawl-cutoff",
        cursor=cursor,
        source_endpoint="https://example.go.kr/course/list?category=education",
    )

    assert changed == 3
    query, params = cursor.calls[0]
    assert "source_endpoint = %(source_endpoint)s" in query
    assert params == {
        "provider": "MUNI_MULTI",
        "cutoff": "crawl-cutoff",
        "branch_id": None,
        "source_endpoint": "https://example.go.kr/course/list?category=education",
    }
