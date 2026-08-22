from contextlib import contextmanager
from datetime import date

from Crawler import Crawler_MunicipalYaml as municipal
from Crawler.Crawler_MunicipalYaml import MunicipalDbWriter, filter_generic_miscollected_rows


def test_normalize_course_uses_program_type_to_override_public_target_group() -> None:
    course = MunicipalDbWriter("MUNI_TEST").normalize_course(
        {
            "title": "가족 생태 체험",
            "category": "문화/체험",
            "program_type": "체험",
            "period": "2026-08-01 ~ 2026-08-01",
            "source_group": "public_reservation",
            "domain_category": "공공예약",
            "service_group": "공공강좌",
            "raw_url": "https://example.go.kr/program/1",
        },
        "branch-id",
    )

    assert course["program_type"] == "체험"
    assert course["service_group"] == "체험"


def test_locked_municipal_batch_stays_in_education_even_for_experience_title() -> None:
    course = MunicipalDbWriter("MUNI_TEST").normalize_course(
        {
            "title": "가족 생태 체험",
            "category": "문화/체험",
            "program_type": "체험",
            "period": "2026-08-01 ~ 2026-08-01",
            "source_group": "municipal_reservation",
            "domain_category": "교육·강좌",
            "service_group": "공공강좌",
            "service_group_policy": "locked",
            "municipality_code": "1111000000",
            "raw_url": "https://example.go.kr/program/1",
        },
        "branch-id",
    )

    assert course["program_type"] == "체험"
    assert course["service_group"] == "공공강좌"


def test_save_branch_inherits_one_exact_facility_registry_location(monkeypatch) -> None:
    class Cursor:
        def __init__(self):
            self.params = []
            self.statements = []

        def execute(self, sql, params=None):
            self.statements.append(sql)
            self.params.append(params)

        def fetchall(self):
            return [{
                "id": "facility-id",
                "address": "서울특별시 종로구 세종대로 1",
                "lat": 37.57,
                "lon": 126.98,
                "location_confidence": 90,
                "location_verified": True,
            }]

        def fetchone(self):
            return {"id": "branch-id"}

    cursor = Cursor()

    @contextmanager
    def fake_db_cursor(*_args, **_kwargs):
        yield cursor

    monkeypatch.setattr(municipal, "get_db_cursor", fake_db_cursor)

    result = MunicipalDbWriter("MUNI_TEST").save_branch("branch", "국립체험박물관")

    assert result == "branch-id"
    assert cursor.params[0]["normalized_name"] == "국립체험박물관"
    written = cursor.params[1]
    assert written["address"] == "서울특별시 종로구 세종대로 1"
    assert written["lat"] == 37.57
    assert written["lon"] == 126.98
    assert written["coordinate_source"] == "FACILITY_REGISTRY_MATCH:facility-id"
    assert written["location_confidence"] == 90
    assert written["location_verified"] is True
    upsert_sql = cursor.statements[1]
    assert "branches.location_verified IS TRUE" in upsert_sql
    assert "COALESCE(EXCLUDED.location_verified, FALSE) IS FALSE" in upsert_sql


def test_save_branch_persists_explicit_region_columns(monkeypatch) -> None:
    class Cursor:
        def __init__(self):
            self.params = []
            self.statements = []

        def execute(self, sql, params=None):
            self.statements.append(sql)
            self.params.append(params)

        def fetchall(self):
            return []

        def fetchone(self):
            return {"id": "branch-id"}

    cursor = Cursor()

    @contextmanager
    def fake_db_cursor(*_args, **_kwargs):
        yield cursor

    monkeypatch.setattr(municipal, "get_db_cursor", fake_db_cursor)

    result = MunicipalDbWriter("MUNI_TEST").save_branch(
        "branch",
        "Branch",
        region_sido="\uc778\ucc9c\uad11\uc5ed\uc2dc",
        region_sigungu="\ub0a8\ub3d9\uad6c",
    )

    assert result == "branch-id"
    written = cursor.params[-1]
    assert written["region_sido"] == "\uc778\ucc9c\uad11\uc5ed\uc2dc"
    assert written["region_sigungu"] == "\ub0a8\ub3d9\uad6c"
    upsert_sql = cursor.statements[-1]
    assert "region_sido = COALESCE" in upsert_sql
    assert "region_sigungu = COALESCE" in upsert_sql


def test_promoted_venue_branch_keeps_explicit_municipality_region() -> None:
    writer = MunicipalDbWriter("MUNI_TEST")
    row = {
        "branch": "\ucda9\uccad\ubd81\ub3c4 \uccad\uc8fc\uc2dc \uc0c1\ub2f9\uad6c",
        "branch_code": "MUNI_TEST_4311100000",
        "venue_name": "\uc0c1\ub2f9\uad6c\uccad\uc18c\ub144\uc218\ub828\uad00",
        "municipality_code": "4311100000",
        "municipality_full_name": "\ucda9\uccad\ubd81\ub3c4 \uccad\uc8fc\uc2dc \uc0c1\ub2f9\uad6c",
        "municipality_region_verified": True,
    }

    writer.normalize_branch_split_row(row)
    branch = writer.branch_info_from_row(row)

    assert row["branch"] == "\uc0c1\ub2f9\uad6c\uccad\uc18c\ub144\uc218\ub828\uad00"
    assert branch["region_sido"] == "\ucda9\uccad\ubd81\ub3c4"
    assert branch["region_sigungu"] == "\uccad\uc8fc\uc2dc \uc0c1\ub2f9\uad6c"


def test_unverified_municipality_text_does_not_set_branch_region() -> None:
    branch = MunicipalDbWriter("MUNI_TEST").branch_info_from_row(
        {
            "branch": "Test operator",
            "municipality_code": "not-a-code",
            "municipality_full_name": "\uc778\ucc9c\uad11\uc5ed\uc2dc \ub0a8\ub3d9\uad6c",
        }
    )

    assert branch["region_sido"] == ""
    assert branch["region_sigungu"] == ""


def test_legacy_valid_municipality_pair_is_not_implicitly_trusted() -> None:
    branch = MunicipalDbWriter("MUNI_LEGACY").branch_info_from_row(
        {
            "branch": "수원시 통합예약",
            "municipality_code": "4111000000",
            "municipality_full_name": "경기도 수원시",
            "venue_address": "경기도 수원시 팔달구 효원로 1",
        }
    )

    assert branch["region_sido"] == ""
    assert branch["region_sigungu"] == ""


def test_explicit_region_precedes_municipality_verification_veto() -> None:
    branch = MunicipalDbWriter("MUNI_TEST").branch_info_from_row(
        {
            "branch": "공식 지역 지정 시설",
            "region_sido": "인천광역시",
            "region_sigungu": "남동구",
            "municipality_code": "2818500000",
            "municipality_full_name": "인천광역시 남동구",
            "municipality_region_verified": False,
        }
    )

    assert branch["region_sido"] == "인천광역시"
    assert branch["region_sigungu"] == "남동구"


def test_sejong_municipality_sets_both_region_levels() -> None:
    branch = MunicipalDbWriter("MUNI_TEST").branch_info_from_row(
        {
            "branch": "세종시설공단",
            "municipality_code": "3611000000",
            "municipality_full_name": "세종특별자치시",
            "municipality_region_verified": True,
        }
    )

    assert branch["region_sido"] == "세종특별자치시"
    assert branch["region_sigungu"] == "세종특별자치시"


def test_branch_phone_extracts_numbers_from_long_contact_notes() -> None:
    branch = MunicipalDbWriter("MUNI_TEST").branch_info_from_row(
        {
            "branch": "성내문화체육센터",
            "contact": (
                "관내 저소득층 학생 우선 접수 안내 및 준비물 공지 "
                "강사 문의 연락처: 010-8325-4304 "
                "강동구청 교육지원과: 02-3425-8854"
            ),
        }
    )

    assert branch["phone"] == "010-8325-4304 / 02-3425-8854"
    assert len(branch["phone"]) <= 50


def test_branch_phone_discards_long_non_phone_free_text() -> None:
    assert municipal.normalize_branch_phone("문의 안내 " * 20) == ""


def test_facility_usage_info_is_cached_by_branch_not_course_url(monkeypatch) -> None:
    calls: list[list[str]] = []

    class UsageInfo:
        operating_hours = "09:00 ~ 18:00"
        regular_holiday = "월요일"
        admission_fee = "무료"

        def has_data(self) -> bool:
            return True

        def as_basic_info(self) -> dict[str, str]:
            return {"source_url": "https://example.com/info"}

    def fake_fetch(urls, **_kwargs):
        calls.append(urls)
        return UsageInfo()

    monkeypatch.setattr(municipal, "fetch_library_usage_info", fake_fetch)
    writer = MunicipalDbWriter("SCIENCE_TEST")
    first_branch = {
        "name": "국립테스트과학관",
        "address": "서울특별시 종로구 1",
        "website_url": "https://example.com/program/1",
    }
    second_branch = {
        "name": "국립테스트과학관",
        "address": "서울특별시 종로구 1",
        "website_url": "https://example.com/program/2",
    }

    writer.enrich_library_usage_info(
        {"raw_url": "https://example.com/program/1"},
        first_branch,
    )
    writer.enrich_library_usage_info(
        {"raw_url": "https://example.com/program/2"},
        second_branch,
    )

    assert len(calls) == 1
    assert second_branch["operating_hours"] == "09:00 ~ 18:00"
    assert second_branch["regular_holiday"] == "월요일"
    assert second_branch["admission_fee"] == "무료"


def test_normalize_course_preserves_explicit_registration_dates() -> None:
    course = MunicipalDbWriter("MUNI_TEST").normalize_course(
        {
            "title": "여름 문화 강좌",
            "period": "2026-08-01 ~ 2026-08-31",
            "schedule_raw": "매주 토요일 10:00",
            "apply_start": "2026-07-01",
            "apply_end": "2026-07-20",
            "raw_url": "https://example.com/course/1",
        },
        "branch-id",
    )

    assert course["apply_start"].isoformat() == "2026-07-01"
    assert course["apply_end"].isoformat() == "2026-07-20"
    assert course["apply_period_raw"] == "2026-07-01 ~ 2026-07-20"


def test_normalize_course_preserves_explicit_dates_for_two_digit_source_period() -> None:
    course = MunicipalDbWriter("MUNI_TEST").normalize_course(
        {
            "title": "날짜 테스트",
            "period": "26.08.01 ~ 26.08.31",
            "start_date": date(2026, 8, 1),
            "end_date": date(2026, 8, 31),
            "raw_url": "https://example.com/course/explicit-dates",
        },
        "branch-id",
    )

    assert course["start_date"] == date(2026, 8, 1)
    assert course["end_date"] == date(2026, 8, 31)


def test_normalize_course_accepts_apply_period_raw_alias() -> None:
    course = MunicipalDbWriter("MUNI_TEST").normalize_course(
        {
            "title": "가을 문화 강좌",
            "period": "2026-09-01 ~ 2026-09-30",
            "apply_period_raw": "2026-08-01 ~ 2026-08-20",
            "raw_url": "https://example.com/course/2",
        },
        "branch-id",
    )

    assert course["apply_start"].isoformat() == "2026-08-01"
    assert course["apply_end"].isoformat() == "2026-08-20"
    assert course["apply_period_raw"] == "2026-08-01 ~ 2026-08-20"


def test_normalize_course_distinguishes_unknown_and_free_fees() -> None:
    writer = MunicipalDbWriter("MUNI_TEST")
    expected_by_source = {
        "": None,
        "문의": None,
        "별도 안내": None,
        "무료": 0,
        "0원": 0,
        "15,000원": 15_000,
        "10000": 10_000,
    }

    for index, (source_fee, expected) in enumerate(expected_by_source.items()):
        course = writer.normalize_course(
            {
                "title": f"요금 의미 테스트 {index}",
                "period": "2026-09-01 ~ 2026-09-30",
                "fee": source_fee,
                "raw_url": f"https://example.com/course/fee-{index}",
            },
            "branch-id",
        )
        assert course["fee"] == expected


def test_normalize_course_preserves_explicit_zero_current_capacity() -> None:
    course = MunicipalDbWriter("MUNI_TEST").normalize_course(
        {
            "title": "정원 테스트",
            "period": "2026-09-01 ~ 2026-09-30",
            "capacity_total": 7,
            "capacity_current": 0,
            "capacity_remaining": 7,
            "raw_url": "https://example.com/course/capacity-zero",
        },
        "branch-id",
    )

    assert course["capacity_total"] == 7
    assert course["capacity_current"] == 0
    assert course["capacity_remaining"] == 7


def test_labeled_place_pair_promotes_broad_branch_to_venue_branch():
    row = {
        "branch": "\ucda9\uccad\ub0a8\ub3c4 \ucc9c\uc548\uc2dc \uc11c\ubd81\uad6c",
        "branch_code": "MUNI_TEST",
        "title": "sample",
        "raw_fields": {"pairs": {"\uc7a5\uc18c": "\ub3c5\ub9bd\uae30\ub150\uad00"}},
    }

    MunicipalDbWriter("MUNI_TEST").normalize_branch_split_row(row)

    assert row["branch"] == "\ub3c5\ub9bd\uae30\ub150\uad00"
    assert row["venue_name"] == "\ub3c5\ub9bd\uae30\ub150\uad00"
    assert row["branch_code"] != "MUNI_TEST"


def test_trailing_venue_in_generic_description_promotes_broad_branch():
    row = {
        "branch": "\ucda9\uccad\ub0a8\ub3c4 \ucc9c\uc548\uc2dc \uc11c\ubd81\uad6c",
        "branch_code": "MUNI_TEST",
        "title": "2026 \ucc9c\uc548K\uceec\ucc98\ubc15\ub78c\ud68c",
        "description": (
            "2026 \ucc9c\uc548K\uceec\ucc98\ubc15\ub78c\ud68c "
            "2026.09.02 ~ 2026.09.06 \ub3c5\ub9bd\uae30\ub150\uad00 3071"
        ),
    }

    MunicipalDbWriter("MUNI_TEST").normalize_branch_split_row(row)

    assert row["branch"] == "\ub3c5\ub9bd\uae30\ub150\uad00"
    assert row["venue_name"] == "\ub3c5\ub9bd\uae30\ub150\uad00"
    assert row["branch_code"] != "MUNI_TEST"


def test_region_only_trailing_text_does_not_replace_branch():
    original_branch = "\ucda9\uccad\ub0a8\ub3c4 \ucc9c\uc548\uc2dc \uc11c\ubd81\uad6c"
    row = {
        "branch": original_branch,
        "branch_code": "MUNI_TEST",
        "title": "sample",
        "description": "sample 2026.07.01 ~ 2026.07.31 \ubcf4\ub839\uc2dc 15",
    }

    MunicipalDbWriter("MUNI_TEST").normalize_branch_split_row(row)

    assert row["branch"] == original_branch
    assert "venue_name" not in row


def test_generic_target_text_is_not_promoted_as_venue():
    original_branch = "\ucda9\uccad\ub0a8\ub3c4 \ucc9c\uc548\uc2dc \uc11c\ubd81\uad6c"
    row = {
        "branch": original_branch,
        "branch_code": "MUNI_TEST",
        "title": "\uad50\uc721/\uccb4\ud5d8",
        "venue_name": "\ub300\uc0c1 \ucd08\ub4f1\ud559\uad50 3-6\ud559\ub144 \uc778\uc6d0 15\uba85",
        "description": (
            "sample \uae30\uac04 2024-10-26 ~ 2024-10-27 "
            "\uc2dc\uac04 14:00 \ub300\uc0c1 \ucd08\ub4f1\ud559\uad50 3-6\ud559\ub144 \uc778\uc6d0 15\uba85"
        ),
        "raw_fields": {
            "parser": "generic_card",
            "pairs": {
                "\ub300\uc0c1": "\ucd08\ub4f1\ud559\uad50 3-6\ud559\ub144",
                "\uc778\uc6d0": "15\uba85",
            },
        },
    }

    MunicipalDbWriter("MUNI_TEST").normalize_branch_split_row(row)

    assert row["branch"] == original_branch
    assert "venue_name" not in row


def test_long_facility_list_is_not_promoted_as_venue():
    original_branch = "\ucda9\uccad\ub0a8\ub3c4 \ucc9c\uc548\uc2dc \uc11c\ubd81\uad6c"
    row = {
        "branch": original_branch,
        "branch_code": "MUNI_TEST",
        "title": "sample",
        "venue_name": (
            "\ub3c5\ub9bd\uae30\ub150\uad00 \ubba4\uc9c0\uc5c4\ud638\ub450 "
            "\ucc9c\uc548\ubc15\ubb3c\uad00 \ucc9c\uc548\uc608\uc220\uc758\uc804\ub2f9 "
            "\uacf5\uc8fc\ubb38\uc608\ud68c\uad00 \ub2f9\uc9c4\ubb38\uc608\uc758\uc804\ub2f9 "
            "\uc11c\uc0b0\uc2dc\ubb38\ud654\ud68c\uad00 \ud0dc\uc548\uad70\ubb38\ud654\uc608\uc220\ud68c\uad00"
        ),
        "raw_fields": {"parser": "generic_table"},
    }

    MunicipalDbWriter("MUNI_TEST").normalize_branch_split_row(row)

    assert row["branch"] == original_branch
    assert "venue_name" not in row


def test_generic_registration_form_row_is_filtered_out():
    rows = [
        {
            "title": "\ucda9\ub0a8\ubb38\ud654\uc608\uc220\uc885\ud569\uc815\ubcf4\uc2dc\uc2a4\ud15c",
            "description": (
                "\ub4f1\ub85d/\uc218\uc815/\uc2e0\uccad\ud558\uae30 "
                "\uc9c0\uc5ed,\uc2dc\uc124\uace0\uc720\ubc88\ud638,\ubb38\ud654\ud589\uc0ac\ubd84\ub958,"
                "\uc138\ubd80\ubd84\ub958,\ud589\uc0ac\uba85,\uc7a5\uc18c,\uc2b9\uc778\uc5ec\ubd80,\ub178\ucd9c\uc5ec\ubd80"
            ),
            "raw_url": "https://cnc.cacf.or.kr/main/html/sub02/0217.html?mode=W",
            "status": "OPEN",
        },
        {
            "title": "2026 \uafc8\ub2e4\ub77d \ubb38\ud654\uc608\uc220\ud559\uad50",
            "description": "2026.07.04 ~ 2026.08.23 \ubba4\uc9c0\uc5c4\ud638\ub450",
            "period": "2026-07-04 ~ 2026-08-23",
            "status": "OPEN",
        },
    ]

    filtered = filter_generic_miscollected_rows(rows)

    assert [row["title"] for row in filtered] == ["2026 \uafc8\ub2e4\ub77d \ubb38\ud654\uc608\uc220\ud559\uad50"]
