from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from DB.course_lifecycle import SEOUL_TIMEZONE
from backend import models
from backend.routers.courses import (
    _serialize_course,
    course_effective_status_filter,
)
from backend.routers.seo_pages import course_offer_json_ld
from backend.routers.user_courses import _build_notification


def _dated_course(*, status: str, apply_end_offset: int, end_date_offset: int = 30):
    today = datetime.now(SEOUL_TIMEZONE).date()
    return models.Course(
        id=uuid4(),
        provider="TEST_PROVIDER",
        provider_course_id="course-1",
        title="Date lifecycle course",
        status=status,
        apply_end=today + timedelta(days=apply_end_offset),
        end_date=today + timedelta(days=end_date_offset),
        reservation_available=True,
        is_active=True,
    )


def test_course_serializer_never_exposes_expired_open_registration() -> None:
    payload = _serialize_course(_dated_course(status="OPEN", apply_end_offset=-1))

    assert payload["status"] == "CLOSED"
    assert payload["status_label"] == "마감"
    assert payload["reservation_available"] is False


def test_course_status_filter_applies_kst_date_closure_to_open_and_closed_views() -> None:
    open_clause = course_effective_status_filter(["OPEN"])
    closed_clause = course_effective_status_filter(["CLOSED"])
    assert open_clause is not None
    assert closed_clause is not None

    open_sql = str(
        open_clause.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    closed_sql = str(
        closed_clause.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    for sql in (open_sql, closed_sql):
        assert "courses.end_date" in sql
        assert "courses.apply_end" in sql
        assert "Asia/Seoul" in sql
        assert "OPEN" in sql
        assert "DEADLINE" in sql
    assert "courses.status = 'CLOSED'" in closed_sql


def test_expired_open_registration_does_not_emit_apply_notification() -> None:
    course = _dated_course(status="OPEN", apply_end_offset=-1)
    mark = SimpleNamespace(course=course, mark_type="favorite")
    today = datetime.now(SEOUL_TIMEZONE).date()

    assert _build_notification(mark, today) == []


def test_expired_open_registration_is_sold_out_in_structured_data() -> None:
    course = _dated_course(status="OPEN", apply_end_offset=-1)

    payload = course_offer_json_ld(course, "https://mooncen.test/courses/course-1")

    assert payload["availability"] == "https://schema.org/SoldOut"
