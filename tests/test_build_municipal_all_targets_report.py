from __future__ import annotations

from decimal import Decimal

from tools import build_municipal_all_targets_report as report


def test_display_fields_prefer_structured_values() -> None:
    fields = report.display_fields(
        {
            "target": "초등학생",
            "fee": Decimal("10000"),
            "start_date": "2026-08-01",
            "end_date": "2026-08-31",
            "venue_name": "문화센터",
            "venue_address": "서울시 테스트구",
            "category_raw": "미술",
            "schedule_raw": "매주 토 10:00~12:00",
            "raw_fields": {},
        }
    )

    assert fields == {
        "target": "초등학생",
        "fee": "10,000원",
        "date": "2026-08-01 ~ 2026-08-31",
        "place": "문화센터 / 서울시 테스트구",
        "category": "미술",
        "time": "매주 토 10:00~12:00",
    }


def test_display_fields_use_explicit_source_omission_markers() -> None:
    fields = report.display_fields({"raw_fields": {}})
    flags = report.omission_flags({"raw_fields": {}}, fields)

    assert fields == {
        "target": "대상 별도 안내",
        "fee": "요금 별도 안내",
        "date": "날짜 별도 안내",
        "place": "장소 별도 안내",
        "category": "분야 별도 안내",
        "time": "시간 별도 안내",
    }
    assert all(flags.values())


def test_free_fee_and_branch_location_are_rendered() -> None:
    fields = report.display_fields(
        {
            "fee": Decimal("0"),
            "branch_name": "도서관",
            "branch_address": "부산시 테스트구",
            "raw_fields": {
                "target": "누구나",
                "period": "상시",
                "schedule": "운영시간 내",
                "category": "독서",
            },
        }
    )

    assert fields["fee"] == "무료"
    assert fields["place"] == "도서관 / 부산시 테스트구"
    assert fields["target"] == "누구나"
