from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_backend_supports_every_public_course_sort_mode() -> None:
    source = (ROOT / "backend" / "routers" / "courses.py").read_text(encoding="utf-8")

    assert "price_asc" in source
    assert "price_desc" in source
    assert "models.Course.fee.asc().nullslast()" in source
    assert "models.Course.fee.desc().nullslast()" in source


def test_explicitly_unavailable_courses_are_filtered_before_total_and_pagination() -> None:
    source = (ROOT / "backend" / "routers" / "courses.py").read_text(encoding="utf-8")

    filter_index = source.index("query.filter(_course_not_explicitly_unavailable_filter())")
    pagination_index = source.index("query.add_columns(func.count(models.Course.id).over()")
    assert filter_index < pagination_index


def test_main_course_view_does_not_refilter_the_server_page() -> None:
    source = (ROOT / "frontend2" / "src" / "App.tsx").read_text(encoding="utf-8")

    assert "if (viewMode === 'all') return classItems;" in source
    assert "if (coursePage === 1) return courseResponse.items;" in source
    assert "lastCourseQueryBaseKey" not in source
