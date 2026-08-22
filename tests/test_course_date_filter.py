from datetime import date

from sqlalchemy.dialects import postgresql

from backend.routers.courses import _course_date_filter, _weekday_label_for_date


def test_weekday_label_for_date_uses_korean_weekday():
    assert _weekday_label_for_date(date(2026, 6, 29)) == "월"
    assert _weekday_label_for_date(date(2026, 6, 30)) == "화"
    assert _weekday_label_for_date(date(2026, 7, 5)) == "일"


def test_course_date_filter_limits_range_courses_to_selected_weekday():
    compiled = _course_date_filter(date(2026, 6, 29)).compile(dialect=postgresql.dialect())
    sql = str(compiled)
    params = compiled.params

    assert "courses.schedule_dates @>" in sql
    assert "courses.start_date <=" in sql
    assert "courses.end_date >=" in sql
    assert "array_length(courses.schedule_days" in sql
    assert "ANY (courses.schedule_days)" in sql
    assert params["schedule_dates_1"] == ["2026-06-29"]
    assert "월" in params.values()
    assert "월요일" in params.values()
    assert "Mon" in params.values()
