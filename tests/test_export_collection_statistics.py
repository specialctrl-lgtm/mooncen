from __future__ import annotations

import csv
from datetime import date

from tools import export_collection_statistics as report


def _raw_course(**overrides):
    row = {
        "course_id": "course-1",
        "provider": "TEST",
        "branch_name": "테스트 지점",
        "title": "테스트 강좌",
        "target": "성인",
        "fee": 0,
        "start_date": date(2027, 1, 10),
        "end_date": date(2027, 2, 10),
        "schedule_raw": "화 10:00-11:00",
        "apply_start": date(2026, 12, 28),
        "apply_end": date(2027, 1, 5),
        "status": "접수중",
        "program_type": "강좌",
        "service_group": "공공강좌",
        "collection_type": "lecture",
        "category_raw": "인문",
        "venue_name": "문화홀",
        "venue_address": "서울시 테스트로 1",
        "raw_url": "https://example.com/course-1",
        "is_active": True,
    }
    row.update(overrides)
    return row


def test_statistics_use_reception_start_not_class_date() -> None:
    normalized = report.normalize_course(_raw_course())

    statistics = report.build_statistics([normalized])

    assert statistics["yearly"] == [{"reception_year": "2026", "count": 1}]
    assert statistics["monthly"] == [{"reception_month": "2026-12", "count": 1}]
    assert statistics["weekly"] == [
        {
            "iso_week": "2026-W53",
            "week_start": "2026-12-28",
            "week_end": "2027-01-03",
            "count": 1,
        }
    ]


def test_normalized_course_contains_required_six_fields_and_table_type() -> None:
    row = report.normalize_course(_raw_course())

    assert [row[key] for key in ("target", "fee", "date", "place", "category", "time")] == [
        "성인",
        "0",
        "2027-01-10 ~ 2027-02-10",
        "문화홀",
        "인문",
        "화 10:00-11:00",
    ]
    assert row["major_category"] == "교육"
    assert row["table_type"] == "education"
    assert row["reception_start"] == "2026-12-28"


def test_write_report_creates_full_and_monthly_tables(tmp_path) -> None:
    output_dir, summary = report.write_report(
        [_raw_course()],
        output_root=tmp_path,
        include_inactive=False,
        from_date=None,
        to_date=None,
    )

    assert summary["statistics_date_basis"] == "apply_start"
    assert summary["required_six_fields_complete_rows"] == 1
    assert (output_dir / "courses.csv").exists()
    assert (output_dir / "statistics_monthly.csv").exists()
    assert (output_dir / "statistics_monthly_category.csv").exists()
    assert (tmp_path / "latest" / "summary.json").exists()
    with (output_dir / "courses.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["reception_start"] == "2026-12-28"
