from __future__ import annotations

from tools.report_scope_region_coverage import (
    CourseLocation,
    MunicipalityIndex,
    ScopeStats,
    STATUS_CLASSIFICATION_REVIEW,
    STATUS_COLLECTED,
    STATUS_HISTORICAL,
    STATUS_NOT_COLLECTED,
    compact_text,
    resolve_course_municipality,
    scope_status,
)


def build_index() -> MunicipalityIndex:
    return MunicipalityIndex.build(
        [
            {
                "code": "4111000000",
                "sido": "경기도",
                "sigungu": "수원시",
                "full_name": "경기도 수원시",
                "municipality_type": "city",
            },
            {
                "code": "4111300000",
                "sido": "경기도",
                "sigungu": "수원시 권선구",
                "full_name": "경기도 수원시 권선구",
                "municipality_type": "district",
            },
            {
                "code": "1283000000",
                "sido": "전남광주통합특별시",
                "sigungu": "영광군",
                "full_name": "전남광주통합특별시 영광군",
                "municipality_type": "county",
            },
            {
                "code": "1221000000",
                "sido": "전남광주통합특별시",
                "sigungu": "동구",
                "full_name": "전남광주통합특별시 동구",
                "municipality_type": "district",
            },
        ]
    )


def test_region_match_prefers_the_more_specific_district() -> None:
    index = build_index()

    match = index.match_one("경기 수원시 권선구 권선로 1")

    assert match is not None
    assert match.full_name == "경기도 수원시 권선구"


def test_region_match_accepts_pre_merger_province_names() -> None:
    index = build_index()

    county = index.match_one("전라남도 영광군 영광읍")
    district = index.match_one("광주광역시 동구 문화전당로")

    assert county is not None
    assert county.full_name == "전남광주통합특별시 영광군"
    assert district is not None
    assert district.full_name == "전남광주통합특별시 동구"


def test_scope_status_distinguishes_collection_gaps() -> None:
    active = ScopeStats(active_courses={"active"})
    historical = ScopeStats(all_courses={"old"})

    assert scope_status(active) == STATUS_COLLECTED
    assert scope_status(ScopeStats(), classification_candidate_active=3) == (
        STATUS_CLASSIFICATION_REVIEW
    )
    assert scope_status(historical) == STATUS_HISTORICAL
    assert scope_status(ScopeStats()) == STATUS_NOT_COLLECTED


def course_location(branch_name: str, branch_address: str = "") -> CourseLocation:
    return CourseLocation(
        course_id="course",
        provider="TEST_PROVIDER",
        branch_id="branch",
        branch_name=branch_name,
        branch_address=branch_address,
        facility_type="",
        facility_category="",
        venue_name="",
        venue_address="",
        is_active=True,
    )


def test_provider_candidates_disambiguate_a_local_district_name() -> None:
    index = MunicipalityIndex.build(
        [
            {
                "code": "2617000000",
                "sido": "부산광역시",
                "sigungu": "동구",
                "full_name": "부산광역시 동구",
                "municipality_type": "district",
            },
            {
                "code": "3117000000",
                "sido": "울산광역시",
                "sigungu": "동구",
                "full_name": "울산광역시 동구",
                "municipality_type": "district",
            },
        ]
    )

    match = resolve_course_municipality(
        course_location("동구청"),
        index,
        {"TEST_PROVIDER": {"부산광역시 동구"}},
    )

    assert match is not None
    assert match.full_name == "부산광역시 동구"


def test_single_descendant_is_preferred_over_its_parent() -> None:
    index = build_index()

    match = resolve_course_municipality(
        course_location("고등동 주민자치센터"),
        index,
        {"TEST_PROVIDER": {"경기도 수원시", "경기도 수원시 권선구"}},
    )

    assert match is not None
    assert match.full_name == "경기도 수원시 권선구"


def test_exact_branch_override_precedes_provider_fallback() -> None:
    index = build_index()
    district = index.by_full_name["경기도 수원시 권선구"]

    match = resolve_course_municipality(
        course_location("주소 없는 별칭 지점"),
        index,
        {"TEST_PROVIDER": {"경기도 수원시"}},
        {
            (
                "TEST_PROVIDER",
                compact_text("주소 없는 별칭 지점"),
            ): district,
        },
    )

    assert match == district
