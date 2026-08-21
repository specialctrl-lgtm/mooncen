from __future__ import annotations

import csv
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
import requests

from tools.maintenance.backfill_missing_branch_addresses import (
    CURATED_BRANCH_LOCATIONS,
    CURATED_BRANCH_PATTERN_LOCATIONS,
    INVALID_ADDRESS_PROVIDER_LOCALITIES,
    KakaoResolver,
    address_matches_locality,
    administrative_center_unit,
    administrative_center_search_name,
    branch_locality,
    broader_municipality_locality,
    choose_unique_source,
    deterministic_resolution,
    embedded_course_address,
    embedded_road_address,
    facility_stem,
    google_query_name,
    has_multiple_venues,
    invalid_address_search_name,
    invalid_external_report_rows,
    is_ambiguous_facility_name,
    is_generic_branch_name,
    is_non_physical_name,
    is_usable_address,
    kakao_query_name,
    locality_label,
    names_overlap,
    naver_query_name,
    place_candidate_score,
    provider_operator_search_name,
    retail_branch_key,
    road_address_key,
    rollback_invalid_external_rows,
    search_query_variants,
    strip_locality_prefix,
    text_conflicts_locality,
    unique_address,
)
from Crawler.Crawler_MunicipalYaml import (
    HOME_PEN_EXPERIENCE_BRANCH_LOCATIONS,
)
from tools.maintenance.split_missing_branches_by_venue import (
    VenueGroup,
    facility_name_without_address,
    load_audited_report_resolutions,
    resolve_group,
    stable_branch_code,
    top_level_comma_parts,
    venue_facility_name,
)


def test_facility_stem_removes_room_details() -> None:
    assert facility_stem("MUNI_TEST", "강일도서관 4층 아름터") == "강일도서관"
    assert facility_stem("MUNI_TEST", "신창면 행정복지센터 다목적실(2층)") == "신창면 행정복지센터"
    assert facility_stem("MUNI_TEST", "평생학습관 2층 나눔실") == "평생학습관"
    assert facility_stem("MUNI_TEST", "군포시청소년수련관") == "군포시청소년수련관"
    assert facility_stem("MUNI_TEST", "화성시청 평생학습과") == "화성시청"
    assert facility_stem("MUNI_TEST", "2층 문화강연실") == ""
    assert facility_stem("MUNI_TEST", "여성가족원(본원)") == "여성가족원 본원"
    assert (
        facility_stem("MUNI_TEST", "재송어린이도서관 2층 시청각실")
        == "재송어린이도서관"
    )


def test_retail_facility_stem_keeps_branch_name() -> None:
    assert facility_stem("LOTTE", "롯데문화센터 잠실점") == "롯데문화센터 잠실점"
    assert kakao_query_name("SHINSEGAE_ACADEMY", "센텀시티") == "신세계백화점 센텀시티"
    assert google_query_name("SHINSEGAE_ACADEMY", "센텀시티") == "신세계백화점 센텀시티"
    assert google_query_name("LOTTE", "롯데문화센터 잠실점") == "롯데백화점 잠실점"
    assert naver_query_name("LOTTE", "롯데문화센터 잠실점") == "롯데문화센터 잠실점"
    assert retail_branch_key("ELAND_RETAIL", "강남패션") == "강남"
    assert retail_branch_key("LOTTE", "롯데문화센터 타임빌라스 수원") == "수원"
    assert (
        retail_branch_key("SHINSEGAE_ACADEMY", "대전신세계 Art&Science")
        == "대전"
    )


def test_kakao_resolver_uses_local_endpoints_and_kakao_sources(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, str], dict[str, object]]] = []

    class Response:
        def __init__(self, payload: dict[str, object]) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self.payload

    def fake_get(url, *, headers, params, timeout):
        assert timeout == 3
        calls.append((url, headers, params))
        if url.endswith("coord2address.json"):
            return Response(
                {
                    "documents": [
                        {
                            "road_address": {
                                "address_name": "서울특별시 강동구 아리수로93길 9-14"
                            }
                        }
                    ]
                }
            )
        if url.endswith("address.json"):
            return Response(
                {
                    "documents": [
                        {
                            "road_address": {
                                "address_name": "서울특별시 강동구 아리수로93길 9-14"
                            },
                            "x": "127.173",
                            "y": "37.565",
                        }
                    ]
                }
            )
        return Response(
            {
                "documents": [
                    {
                        "place_name": "강일도서관",
                        "road_address_name": "서울특별시 강동구 아리수로93길 9-14",
                        "x": "127.173",
                        "y": "37.565",
                    }
                ]
            }
        )

    monkeypatch.setattr(
        "tools.maintenance.backfill_missing_branch_addresses.requests.get",
        fake_get,
    )
    resolver = KakaoResolver(
        "rest-key",
        timeout=3,
        delay=0,
        min_score=82,
        max_requests=3,
    )

    keyword = resolver.place(
        "MUNI_TEST",
        "강일도서관",
        "서울특별시 강동구",
    )
    address = resolver.geocode_address(
        "아리수로93길 9-14",
        "서울특별시 강동구",
    )
    reverse = resolver.reverse(37.565, 127.173)

    assert keyword is not None
    assert keyword.address_source == "KAKAO_LOCAL_KEYWORD"
    assert address is not None
    assert address.coordinate_source == "KAKAO_LOCAL_ADDRESS"
    assert reverse is not None
    assert reverse.address_source == "KAKAO_LOCAL_COORD2ADDRESS"
    assert resolver.requests == 3
    assert all(headers == {"Authorization": "KakaoAK rest-key"} for _, headers, _ in calls)
    assert all("key" not in params for _, _, params in calls)


def test_kakao_address_geocode_rejects_different_street_number(monkeypatch) -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "documents": [
                    {
                        "road_address": {
                            "address_name": "서울특별시 중구 세종대로 999"
                        },
                        "x": "126.9780",
                        "y": "37.5665",
                    }
                ]
            }

    monkeypatch.setattr(
        "tools.maintenance.backfill_missing_branch_addresses.requests.get",
        lambda *_args, **_kwargs: Response(),
    )
    resolver = KakaoResolver(
        "rest-key",
        timeout=3,
        delay=0,
        min_score=82,
        max_requests=3,
    )

    candidate = resolver.geocode_address(
        "서울특별시 중구 세종대로 110",
        "서울특별시 중구",
    )

    assert candidate is None


def test_kakao_resolver_deduplicates_concurrent_requests(monkeypatch) -> None:
    calls = 0

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "documents": [
                    {
                        "place_name": "강일도서관",
                        "road_address_name": "서울특별시 강동구 아리수로93길 9-14",
                        "x": "127.173",
                        "y": "37.565",
                    }
                ]
            }

    def fake_get(url, *, headers, params, timeout):
        nonlocal calls
        calls += 1
        time.sleep(0.02)
        return Response()

    monkeypatch.setattr(
        "tools.maintenance.backfill_missing_branch_addresses.requests.get",
        fake_get,
    )
    resolver = KakaoResolver(
        "rest-key",
        timeout=3,
        delay=0,
        min_score=82,
        max_requests=10,
    )
    with ThreadPoolExecutor(max_workers=8) as executor:
        candidates = list(
            executor.map(
                lambda _index: resolver.place(
                    "MUNI_TEST",
                    "강일도서관",
                    "서울특별시 강동구",
                ),
                range(8),
            )
        )

    assert all(candidate is not None for candidate in candidates)
    assert calls == 1
    assert resolver.requests == 1


@pytest.mark.parametrize("status_code", [401, 403, 429])
def test_kakao_resolver_opens_circuit_on_fatal_status(
    monkeypatch,
    status_code: int,
) -> None:
    calls = 0

    class Response:
        def __init__(self) -> None:
            self.status_code = status_code

        def raise_for_status(self) -> None:
            raise requests.HTTPError(response=self)

    def fake_get(url, *, headers, params, timeout):
        nonlocal calls
        calls += 1
        return Response()

    monkeypatch.setattr(
        "tools.maintenance.backfill_missing_branch_addresses.requests.get",
        fake_get,
    )
    resolver = KakaoResolver(
        "rest-key",
        timeout=3,
        delay=0,
        min_score=82,
        max_requests=1000,
    )

    with ThreadPoolExecutor(max_workers=8) as executor:
        candidates = list(
            executor.map(
                lambda index: resolver.place(
                    "MUNI_TEST",
                    f"place-{index}",
                    "Seoul",
                ),
                range(12),
            )
        )

    assert all(candidate is None for candidate in candidates)
    assert calls == 1
    assert resolver.requests == 1
    assert resolver.blocked_status == status_code


def test_kakao_resolver_enforces_budget_for_concurrent_unique_requests(
    monkeypatch,
) -> None:
    calls = 0
    calls_lock = threading.Lock()

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"documents": []}

    def fake_get(url, *, headers, params, timeout):
        nonlocal calls
        with calls_lock:
            calls += 1
        return Response()

    monkeypatch.setattr(
        "tools.maintenance.backfill_missing_branch_addresses.requests.get",
        fake_get,
    )
    resolver = KakaoResolver(
        "rest-key",
        timeout=3,
        delay=0,
        min_score=82,
        max_requests=3,
    )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(
            executor.map(
                lambda index: resolver.place(
                    "MUNI_TEST",
                    f"place-{index}",
                    "Seoul",
                ),
                range(12),
            )
        )

    assert calls == 3
    assert resolver.requests == 3


def test_kakao_api_key_loader_does_not_fall_back_to_google(monkeypatch) -> None:
    from tools.maintenance import backfill_missing_branch_addresses as module

    monkeypatch.setattr(module, "load_dotenv", lambda *_args, **_kwargs: False)
    monkeypatch.delenv("KAKAO_MAPS_REST_API_KEY", raising=False)
    monkeypatch.delenv("MoonCenKakaoMapsRestApiKey", raising=False)
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "must-not-be-used")

    with pytest.raises(RuntimeError, match="Kakao Maps REST API key is missing"):
        module.load_kakao_api_key()


def test_main_aborts_apply_when_kakao_circuit_is_blocked(
    monkeypatch,
    tmp_path,
) -> None:
    from tools.maintenance import backfill_missing_branch_addresses as module

    args = SimpleNamespace(
        rollback_report=None,
        provider=None,
        active_only=False,
        limit=0,
        repair_invalid_crawler_addresses=False,
        repair_invalid_addresses=False,
        kakao=True,
        google=False,
        naver=False,
        apply=True,
        timeout=3,
        delay=0,
        min_score=82,
        max_kakao_requests=10,
        max_google_requests=None,
        max_naver_requests=10,
        workers=2,
        output_dir=tmp_path,
    )

    class Resolver:
        requests = 1
        blocked_status = 429

        def __init__(self, *_args, **_kwargs) -> None:
            return None

    branch = {
        "id": "branch-id",
        "provider": "MUNI_TEST",
        "branch_code": "test",
        "name": "Test branch",
        "active_courses": 1,
        "website_url": "https://example.test/",
        "address": None,
    }
    monkeypatch.setattr(module, "parse_args", lambda: args)
    monkeypatch.setattr(module, "load_provider_localities", lambda: {})
    monkeypatch.setattr(module, "load_provider_target_names", lambda: {})
    monkeypatch.setattr(
        module,
        "fetch_missing_branches",
        lambda *_args, **_kwargs: [branch],
    )
    monkeypatch.setattr(module, "fetch_address_sources", lambda: [])
    monkeypatch.setattr(module, "deterministic_resolution", lambda *_args: None)
    monkeypatch.setattr(module, "branch_locality", lambda *_args: "Seoul")
    monkeypatch.setattr(module, "load_kakao_api_key", lambda: "rest-key")
    monkeypatch.setattr(module, "KakaoResolver", Resolver)
    monkeypatch.setattr(
        module,
        "external_resolution",
        lambda *_args, **_kwargs: (None, "no_verified_external_match"),
    )
    monkeypatch.setattr(
        module,
        "persist_resolutions",
        lambda *_args, **_kwargs: pytest.fail("partial results must not be applied"),
    )

    assert module.main() == 2


def test_external_report_audit_and_rollback_keep_historical_google_support(
    monkeypatch,
    tmp_path,
) -> None:
    report_path = tmp_path / "resolved.csv"
    fields = [
        "provider",
        "branch_code",
        "name",
        "method",
        "matched_name",
        "address",
        "locality",
    ]
    rows = [
        {
            "provider": "MUNI_TEST",
            "branch_code": "kakao",
            "name": "온라인(ZOOM)",
            "method": "kakao_keyword",
            "matched_name": "다른 장소",
            "address": "서울특별시 강동구 아리수로93길 9-14",
            "locality": "서울특별시 강동구",
        },
        {
            "provider": "MUNI_TEST",
            "branch_code": "google-history",
            "name": "온라인(ZOOM)",
            "method": "google_places",
            "matched_name": "다른 장소",
            "address": "서울특별시 강동구 아리수로93길 9-14",
            "locality": "서울특별시 강동구",
        },
    ]
    with report_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    invalid = invalid_external_report_rows(report_path)
    assert [row["method"] for row in invalid] == [
        "kakao_keyword",
        "google_places",
    ]

    executed: list[dict[str, object]] = []

    class Cursor:
        rowcount = 1

        def execute(self, _query, params) -> None:
            executed.append(dict(params))

        def fetchone(self) -> dict[str, str]:
            return {"id": "branch-id"}

    class CursorContext:
        def __enter__(self) -> Cursor:
            return Cursor()

        def __exit__(self, *_args) -> None:
            return None

    monkeypatch.setattr(
        "tools.maintenance.backfill_missing_branch_addresses.get_db_cursor",
        CursorContext,
    )
    assert rollback_invalid_external_rows(invalid, apply=True) == (2, 2)
    assert executed[0]["source"] == "KAKAO_LOCAL_KEYWORD"
    assert executed[3]["source"] == "GOOGLE_PLACES_TEXT_SEARCH"


def test_non_physical_and_embedded_address_detection() -> None:
    assert is_non_physical_name("온라인(ZOOM)")
    assert has_multiple_venues("증평군립도서관, 메리놀창작소, 어울림")
    assert not has_multiple_venues(
        "해운대인문학도서관 대강당(부산광역시 해운대구 반여로 132, 지하 1층)"
    )
    assert not has_multiple_venues(
        "거제평생학습관 2층 거제대로180, 거제평생학습관2층 1강의실"
    )
    assert not has_multiple_venues(
        "한양서적 반여점(부산광역시 해운대구 반여로 98, 청암빌딩 2층)"
    )
    assert has_multiple_venues("월롱도서관, 문산천")
    assert embedded_road_address("아산시 시민로 247 2층 201호 스튜디오") == "아산시 시민로 247"
    assert (
        road_address_key("경기도 군포시 고산로 663 (산본동) 산본2동행정복지센터")
        == road_address_key("경기도 군포시 고산로 663")
    )
    assert (
        embedded_road_address("미추홀구 염전로 144번길 34 누나동네")
        == "미추홀구 염전로 144번길 34"
    )
    assert (
        embedded_road_address(
            "을지로4가역 근처 정원지원센터(을지로30길 16-8, 2층)"
        )
        == "을지로30길 16-8"
    )
    assert is_usable_address("경기도 아산시 시민로 247")
    assert is_usable_address("울산광역시 북구 신현동 산210-3")
    assert not is_usable_address("가천대학교 글로벌캠퍼스 교육대학원 206호")
    assert (
        strip_locality_prefix("울산광역시 북구 · 북구문화예술회관", "울산광역시 북구")
        == "북구문화예술회관"
    )
    assert (
        strip_locality_prefix(
            "전남광주통합특별시 강진군 / 강진군 평생학습센터",
            "전남광주통합특별시 강진군",
        )
        == "강진군 평생학습센터"
    )
    assert strip_locality_prefix("송현1·2동", "인천광역시 제물포구") == "송현1·2동"
    assert (
        embedded_course_address(
            {
                "name": "산본2동 주민자치회",
                "course_venue_names": [
                        "2층 강의실 15803 경기도 군포시 고산로 663 산본2동 행정복지센터",
                        "3층 체육실 경기도 군포시 고산로 663 산본2동 행정복지센터",
                ]
            },
            "경기도 군포시",
        )
        == "경기도 군포시 고산로 663"
    )
    assert (
        embedded_course_address(
            {
                "name": "동구청",
                "course_venue_names": [
                    "동구 평생학습관 부산광역시 동구 초량중로 38",
                    "동구청 대강당",
                    "외부 공방",
                ],
            },
            "부산광역시 동구",
        )
        == ""
    )
    assert (
        unique_address(
            ["영흥로 251번길 90", "영흥로251번길 90"],
            "인천광역시 옹진군",
        )
        == "인천광역시 옹진군 영흥로 251번길 90"
    )
    assert (
        unique_address(
            ["해운대구 송정중앙로5번길 77"],
            "부산광역시 해운대구",
        )
        == "부산광역시 해운대구 송정중앙로5번길 77"
    )
    assert (
        unique_address(
            ["덕양구 고양시청로 10"],
            "경기도 고양시 덕양구",
        )
        == "경기도 고양시 덕양구 고양시청로 10"
    )
    assert (
        unique_address(
            ["대전 중앙로121번길 10"],
            "충청북도 옥천군",
        )
        == ""
    )
    assert text_conflicts_locality(
        "대전홍명요리학원(대전 중앙로121번길 10)",
        "충청북도 옥천군",
    )


def test_locality_validation_uses_most_specific_municipality() -> None:
    assert address_matches_locality("서울특별시 강동구 아리수로93길 9-14", "서울특별시 강동구")
    assert not address_matches_locality("서울특별시 관악구 남부순환로 1546", "서울특별시 강동구")
    assert not address_matches_locality("대전광역시 동구 동구청로 147", "부산광역시 동구")
    assert address_matches_locality(
        "인천광역시 서구 원당대로 123",
        "인천광역시 검단구",
    )
    assert address_matches_locality(
        "전라남도 나주시 영산포로 1",
        "전남광주통합특별시 나주시",
    )


def test_place_candidate_requires_name_and_locality_match() -> None:
    score = place_candidate_score(
        "강일도서관",
        "서울특별시 강동구",
        "강동구립 강일도서관",
        "서울특별시 강동구 아리수로93길 9-14",
        {"establishment", "library"},
    )
    assert score >= 82
    assert (
        place_candidate_score(
            "롯데문화센터 울산점",
            "",
            "롯데마트문화센터 울산점",
            "울산광역시 남구 삼산로 74",
            {"establishment"},
            "LOTTE",
        )
        == 0
    )
    assert (
        place_candidate_score(
            "신세계백화점 대구신세계",
            "",
            "온기정 신세계백화점 대구점",
            "대구광역시 동구 동부로 149 신세계백화점 8층",
            {"establishment"},
            "SHINSEGAE_ACADEMY",
        )
        == 0
    )
    assert (
        place_candidate_score(
            "홈플러스 작전점",
            "",
            "투썸플레이스 홈플러스 작전점",
            "인천광역시 계양구 계양대로 27",
            {"establishment"},
            "HOMEPLUS",
        )
        == 0
    )
    assert (
        place_candidate_score(
            "강일도서관",
            "서울특별시 강동구",
            "관악중앙도서관",
            "서울특별시 관악구 신림로3길 35",
            {"library"},
        )
        == 0
    )
    assert (
        place_candidate_score(
            "초록향기 작은도서관",
            "서울특별시 강서구",
            "책향기작은도서관",
            "서울특별시 강서구 강서로 1",
            {"library"},
        )
        == 0
    )
    assert (
        place_candidate_score(
            "모현동행정복지센터",
            "전북특별자치도 익산시",
            "마동 행정복지센터",
            "전북특별자치도 익산시 중앙로25길 5",
            {"local_government_office"},
        )
        == 0
    )
    assert (
        place_candidate_score(
            "이천시립서희도서관",
            "경기도 이천시",
            "이천시립도서관",
            "경기도 이천시 설봉로81번길 50",
            {"library"},
        )
        == 0
    )


def test_facility_aliases_and_retail_branches_are_safe() -> None:
    assert names_overlap("골약동 주민자치센터", "골약동 주민센터")
    assert names_overlap("송도2동 주민자치센터", "송도2동 행정복지센터")
    assert names_overlap("진건읍 주민자치센터", "진건주민자치센터")
    assert names_overlap("통영도서관", "통영시립도서관")
    assert names_overlap("화정글샘", "화정글샘도서관")
    assert names_overlap("횡성문화원", "횡성문화원 문화학교")
    assert not names_overlap("금산군 청소년수련관", "금산청소년수련원")
    assert not names_overlap("오산남부종합사회복지관", "오산종합사회복지관")
    assert not names_overlap("여성가족원", "동부여성가족원")
    assert names_overlap("국립고창치유의숲", "국립고창치유의숲 강당")
    assert administrative_center_unit("수동면 주민자치센터") == "수동"
    assert administrative_center_unit("화도수동행정복지센터") == "화도수동"

    score = place_candidate_score(
        "롯데문화센터 잠실점",
        "",
        "롯데백화점 문화센터 잠실점",
        "서울특별시 송파구 올림픽로 240",
        {"establishment"},
        "LOTTE",
    )
    assert score >= 82


def test_branch_locality_prefers_course_metadata() -> None:
    branch = {
        "provider": "MUNI_TEST",
        "course_localities": ["경기도 이천시", "경기도 이천시"],
    }
    assert branch_locality(branch, {"MUNI_TEST": "경기도"}) == "경기도 이천시"
    assert branch_locality(
        {"provider": "MUNI_TEST", "course_localities": ["경기도"]},
        {"MUNI_TEST": "경기도 의왕시"},
    ) == "경기도 의왕시"
    assert branch_locality(
        {
            "provider": "MUNI_TEST",
            "course_localities": [],
            "region_sido": "경기도",
            "region_sigungu": "수원시",
        },
        {},
    ) == "경기도 수원시"
    assert branch_locality(
        {
            "provider": "MUNI_TEST",
            "name": "전라남도 목포시",
            "course_localities": [],
        },
        {},
    ) == "전라남도 목포시"
    assert branch_locality(
        {
            "provider": "EMART",
            "name": "통영점",
            "address": "대한민국 통영시",
            "course_localities": [],
        },
        {},
    ) == "통영시"
    assert locality_label("경기도 부천시") == "경기도 부천시"
    assert locality_label("부천시도서관") == ""
    assert is_generic_branch_name("완주군립도서관(공통)")
    assert is_generic_branch_name("강동구립도서관 통합 4층")
    assert is_generic_branch_name("화성시문화관광재단 도서관사업팀")
    assert is_ambiguous_facility_name("동주민센터")
    assert not is_ambiguous_facility_name("상계8동 주민센터")
    assert administrative_center_search_name("동백2동") == "동백2동 행정복지센터"
    assert administrative_center_search_name("주민센터_상계8동") == "상계8동 주민센터"
    assert administrative_center_search_name("주민센터_상계6,7동") == "상계6,7동 주민센터"
    assert administrative_center_search_name("송현1·2동") == "송현1·2동 주민센터"
    assert administrative_center_search_name("방배본동 자치회관") == "방배본동 주민센터"
    assert administrative_center_search_name("산본2동 주민자치회") == "산본2동 주민센터"
    assert (
        administrative_center_search_name("해운대구 반여1동 주민자치회")
        == "반여1동 주민센터"
    )
    assert (
        administrative_center_search_name(
            "전남광주통합특별시 서구 / 통합행정복지센터 / 화정4동"
        )
        == "화정4동 주민센터"
    )
    assert administrative_center_search_name("자치회관[석관동]") == "석관동 주민센터"
    queries = search_query_variants(
        "강원특별자치도 철원군",
        "철원평생학습관",
    )
    assert queries[:3] == (
        "강원특별자치도 철원군 철원평생학습관",
        "철원군 철원평생학습관",
        "철원평생학습관",
    )
    assert "철원군 평생학습관" in queries
    assert "전라남도교육청나주도서관" in search_query_variants(
        "전남광주통합특별시",
        "전남광주통합특별시교육청나주도서관",
    )
    assert "서울특별시 노원구 상계청소년문화의집" in search_query_variants(
        "서울특별시 노원구",
        "노원구립 상계청소년문화의집",
    )
    assert (
        place_candidate_score(
            "군포시청소년수련관",
            "경기도 군포시",
            "군포시청",
            "경기도 군포시 청백리길 6",
            {"local_government_office"},
        )
        == 0
    )
    assert (
        place_candidate_score(
            "운정청소년센터",
            "경기도 파주시",
            "파주시청소년재단 운정청소년센터",
            "경기도 파주시 와석순환로 415",
            {"establishment"},
        )
        >= 82
    )
    assert (
        place_candidate_score(
            "자양체육관",
            "서울특별시 광진구",
            "자양문화체육센터",
            "서울특별시 광진구 뚝섬로52길 66",
            {"establishment"},
        )
        >= 82
    )
    assert (
        place_candidate_score(
            "다산1동 주민자치센터",
            "경기도 남양주시",
            "다산2동주민센터",
            "경기도 남양주시 다산지금로16번길 75",
            {"local_government_office"},
        )
        == 0
    )


def test_unique_source_rejects_conflicting_addresses() -> None:
    shared = [
        {
            "id": "1",
            "provider": "CULTURE_FACILITY",
            "name": "강일도서관",
            "address": "서울특별시 강동구 아리수로93길 9-14",
            "lat": 37.565,
            "lon": 127.173,
            "location_confidence": 90,
            "location_verified": True,
        },
        {
            "id": "2",
            "provider": "MUNI_LIBRARY",
            "name": "강일도서관 4층",
            "address": "서울특별시 강동구 아리수로93길 9-14",
            "lat": 37.565,
            "lon": 127.173,
            "location_confidence": 80,
            "location_verified": True,
        },
    ]
    assert choose_unique_source(shared, "서울특별시 강동구")["id"] == "1"

    conflicting = [
        *shared,
        {
            "id": "3",
            "provider": "OTHER",
            "name": "강일도서관",
            "address": "서울특별시 강동구 다른로 1",
            "lat": 37.5,
            "lon": 127.1,
            "location_confidence": 90,
            "location_verified": True,
        },
    ]
    assert choose_unique_source(conflicting, "서울특별시 강동구") is None


def test_curated_location_can_correct_provider_locality() -> None:
    resolution = deterministic_resolution(
        {
            "id": "branch-id",
            "provider": "MUNI_EDU_EUMSEONG_GO_KR_DEC266D9",
            "name": "충북혁신도시 공유평생학습관",
            "course_addresses": [],
        },
        "충청북도 음성군",
        {},
        {},
        {},
    )

    assert resolution is not None
    assert resolution.candidate.address == "충청북도 진천군 덕산읍 대하로 203"
    assert resolution.candidate.region_sido == "충청북도"
    assert resolution.candidate.region_sigungu == "진천군"


def test_venue_label_with_embedded_road_is_not_saved_as_course_address() -> None:
    resolution = deterministic_resolution(
        {
            "id": "branch-id",
            "provider": "MUNI_TEST",
            "name": "농성2동 행정복지센터(화정로 314)",
            "course_addresses": [
                "농성2동 행정복지센터(화정로 314)",
            ],
        },
        "광주광역시 서구",
        {},
        {},
        {},
    )

    assert resolution is None


def test_curated_location_matches_notice_suffix_and_current_region_alias() -> None:
    resolution = deterministic_resolution(
        {
            "id": "branch-id",
            "provider": "INCHEON_DISABLED_WELFARE_NOTICE",
            "name": "인천광역시장애인종합복지관 프로그램 이용신청 안내",
            "course_addresses": [],
        },
        "",
        {},
        {},
        {},
    )

    assert resolution is not None
    assert resolution.candidate.address == "인천광역시 연수구 앵고개로 130"
    assert resolution.candidate.region_sigungu == "연수구"


def test_curated_location_uses_official_guri_ecology_address() -> None:
    resolution = deterministic_resolution(
        {
            "id": "branch-id",
            "provider": "MUNI_WWW_GURI_GO_KR_E0C65498",
            "name": "장자호수생태체험관",
            "course_addresses": [],
        },
        "경기도 구리시",
        {},
        {},
        {},
    )

    assert resolution is not None
    assert resolution.candidate.address == "경기도 구리시 장자호수길 76-42"
    assert resolution.candidate.lat == 37.583029


def test_curated_locations_cover_new_library_room_and_music_studio() -> None:
    cases = (
        (
            "MUNI_WWW_GDLIBRARY_OR_KR_7E7ADF81",
            "성내도서관 그림책소행성",
            "서울특별시 강동구",
            "서울특별시 강동구 성안로 106-1",
        ),
        (
            "MUNI_WWW_SONGPA_GO_KR_982793EC",
            "뮤직스튜디오",
            "서울특별시 송파구",
            "서울특별시 송파구 올림픽로 326",
        ),
    )
    for provider, name, locality, expected_address in cases:
        resolution = deterministic_resolution(
            {
                "id": "branch-id",
                "provider": provider,
                "name": name,
                "course_addresses": [],
            },
            locality,
            {},
            {},
            {},
        )

        assert resolution is not None
        assert resolution.method == "curated_location"
        assert resolution.candidate.address == expected_address


def test_curated_locations_cover_geumcheon_osan_and_goesan_branches() -> None:
    cases = (
        (
            "MUNI_GEUMCHEONLIB_SEOUL_KR_E6151FD4",
            "해오름작은도서관",
            "서울특별시 금천구",
            "서울특별시 금천구 시흥대로123길 11, 4층",
        ),
        (
            "MUNI_WWW_OSANEDU_GO_KR_8A50CEDC",
            "하천녹지과",
            "경기도 오산시",
            "경기도 오산시 오산천로 52",
        ),
        (
            "MUNI_WWW_OSANEDU_GO_KR_8A50CEDC",
            "기획예산과",
            "경기도 오산시",
            "경기도 오산시 성호대로 141",
        ),
        (
            "MUNI_WWW_GOESAN_GO_KR_EAE2C3E3",
            "괴산군평생학습관",
            "충청북도 괴산군",
            "충청북도 괴산군 괴산읍 읍내로 184, 괴산군립도서관 3층",
        ),
        (
            "MUNI_WWW_NOWON_KR_FBD1F92A",
            "노원어르신상담센터",
            "서울특별시 노원구",
            (
                "서울특별시 노원구 수락산로 214, "
                "구립수락노인종합복지관 4층"
            ),
        ),
        (
            "MUNI_YEYAK_HSCITY_GO_KR_2DFD650A",
            "화성시민대학",
            "경기도 화성시 효행구",
            "경기도 화성시 효행구 봉담읍 효행로 212 4층",
        ),
    )
    for provider, name, locality, expected_address in cases:
        resolution = deterministic_resolution(
            {
                "id": "branch-id",
                "provider": provider,
                "name": name,
                "course_addresses": [],
            },
            locality,
            {},
            {},
            {},
        )

        assert resolution is not None
        assert resolution.method == "curated_location"
        assert resolution.candidate.address == expected_address


def test_curated_location_can_override_generic_branch_district() -> None:
    resolution = deterministic_resolution(
        {
            "id": "branch-id",
            "provider": "MUNI_LEARNING_SUWON_GO_KR_3AF2DB76",
            "name": "경기도 수원시 권선구",
            "_repair_search_name": "수원시 글로벌 평생학습관",
            "course_addresses": [],
        },
        "경기도 수원시 권선구",
        {},
        {},
        {},
    )

    assert resolution is not None
    assert resolution.method == "curated_location"
    assert resolution.candidate.address == "경기도 수원시 팔달구 월드컵로381번길 2"


def test_curated_pattern_locations_run_before_multiple_venue_rejection() -> None:
    cases = (
        (
            "국립아시아문화전당 극장2",
            "전남광주통합특별시 동구 문화전당로 38",
        ),
        (
            "광주시립미술관 제 3,4 전시실",
            "전남광주통합특별시 북구 하서로 52",
        ),
        (
            "서울 경인미술관, 광주비움박물관",
            "전남광주통합특별시 동구 제봉로 143-1",
        ),
        (
            "전시실",
            "전남광주통합특별시 남구 천변좌로338번길 7",
        ),
    )
    for name, expected_address in cases:
        resolution = deterministic_resolution(
            {
                "id": "branch-id",
                "provider": "MUNI_WWW_GJCF_OR_KR_F9585EF3",
                "name": name,
                "course_addresses": [],
            },
            "광주광역시",
            {},
            {},
            {},
        )

        assert resolution is not None
        assert resolution.method == "curated_pattern_location"
        assert resolution.candidate.address == expected_address


def test_curated_pattern_location_uses_invalid_legacy_address_label() -> None:
    resolution = deterministic_resolution(
        {
            "id": "branch-id",
            "provider": "MUNI_WWW_GJCF_OR_KR_F9585EF3",
            "name": "광주광역시 동구 동명동",
            "address": "박물관 정원",
            "_repair_search_name": "박물관 정원",
            "course_addresses": [],
        },
        "광주광역시",
        {},
        {},
        {},
    )

    assert resolution is not None
    assert resolution.candidate.address == "전남광주통합특별시 북구 하서로 110"


def test_curated_location_catalogs_contain_verified_physical_addresses() -> None:
    locations = (
        *CURATED_BRANCH_LOCATIONS.values(),
        *CURATED_BRANCH_PATTERN_LOCATIONS,
    )
    for location in locations:
        assert is_usable_address(location["address"])
        assert 33 <= float(location["lat"]) <= 39
        assert 124 <= float(location["lon"]) <= 132
        assert str(location["source_url"]).startswith("https://")


def test_curated_operator_location_does_not_overwrite_legacy_course_venue() -> None:
    branch = {
        "id": "branch-id",
        "provider": "MUNI_PAJU_PCY_OR_KR_412053A6",
        "name": "경기도 파주시",
        "course_addresses": [],
    }

    resolution = deterministic_resolution(
        branch,
        "경기도 파주시",
        {},
        {},
        {},
    )

    assert resolution is not None
    assert resolution.candidate.address == "경기도 파주시 문산읍 통일로 1680"
    assert resolution.branch["_skip_course_address_backfill"] is True


def test_invalid_crawler_address_uses_facility_label_not_generic_branch() -> None:
    assert invalid_address_search_name(
        {
            "name": "광주광역시 동구 동명동",
            "address": "빛고을시민문화관 공연장",
        },
        "광주광역시 동구",
    ) == "빛고을시민문화관 공연장"
    assert invalid_address_search_name(
        {
            "name": "경산시 평생학습관",
            "address": "경상북도 경산시",
        },
        "경상북도 경산시",
    ) == "경산시 평생학습관"
    assert invalid_address_search_name(
        {"name": "용운도서관", "address": "[]"},
        "대전광역시 동구",
    ) == "용운도서관"
    assert INVALID_ADDRESS_PROVIDER_LOCALITIES[
        "MUNI_CNC_CACF_OR_KR_7A12B48E"
    ] == "충청남도"
    assert INVALID_ADDRESS_PROVIDER_LOCALITIES[
        "MUNI_HOME_PEN_GO_KR_92635850"
    ] == "부산광역시"
    assert INVALID_ADDRESS_PROVIDER_LOCALITIES[
        "MUNI_WWW_GJCF_OR_KR_F9585EF3"
    ] == "광주광역시"


def test_operator_search_prefers_configured_institution_then_admin_office() -> None:
    names = {
        "MUNI_SUWON": (
            "수원시 글로벌 평생학습관",
            "경기도 수원시",
        ),
        "MUNI_CHEONGJU": (
            "청주시 평생학습관 정규프로그램 중복 별칭",
        ),
    }
    assert provider_operator_search_name(
        {"provider": "MUNI_SUWON", "name": "경기도 수원시"},
        "경기도 수원시",
        names,
    ) == "수원시 글로벌 평생학습관"
    assert provider_operator_search_name(
        {"provider": "MUNI_CHEONGJU", "name": "충청북도 청주시"},
        "충청북도 청주시",
        names,
    ) == "청주시 평생학습관"
    assert provider_operator_search_name(
        {"provider": "MUNI_MOKPO", "name": "전라남도 목포시"},
        "전라남도 목포시",
        {},
    ) == "목포시청"
    assert provider_operator_search_name(
        {
            "provider": "MUNI_SEJONG",
            "name": "세종특별자치시",
        },
        "세종특별자치시",
        {
            "MUNI_SEJONG": (
                "세종특별자치시교육청 평생교육원",
            )
        },
    ) == "세종특별자치시교육청 평생교육원"
    assert (
        broader_municipality_locality("경기도 수원시 권선구")
        == "경기도 수원시"
    )


def test_home_pen_official_location_catalog_is_complete_and_usable() -> None:
    expected_codes = {
        "swai",
        "gaonplay",
        "scinuri",
        "guducklib",
        "gupolib",
        "nambu",
        "ncm",
        "nrmr",
        "bmec",
        "pencit",
        "bansonglib",
        "bmcm",
        "hcce",
        "behm",
        "bukbu",
        "sahalib",
        "seobu",
        "siminlib",
        "yeonsanlib",
        "bnec",
        "bellib",
        "penchi",
        "child",
        "childlike",
        "joonganglib",
        "bicce",
        "penele",
        "schoolsafe",
        "bshc",
        "becs",
        "safetycenter",
        "haeundaelib",
        "dcc",
    }
    assert set(HOME_PEN_EXPERIENCE_BRANCH_LOCATIONS) == expected_codes
    for location in HOME_PEN_EXPERIENCE_BRANCH_LOCATIONS.values():
        assert is_usable_address(location["address"])
        assert 34.0 <= location["lat"] <= 36.0
        assert 128.0 <= location["lon"] <= 130.0
        assert location["source_url"].startswith("https://")


def test_home_pen_official_location_resolves_current_and_legacy_branch_codes() -> None:
    for branch in (
        {
            "id": "current",
            "provider": "MUNI_HOME_PEN_GO_KR_92635850",
            "branch_code": "haeundaelib",
            "name": "해운대도서관",
            "course_addresses": [],
        },
        {
            "id": "legacy",
            "provider": "MUNI_HOME_PEN_GO_KR_92635850",
            "branch_code": "서부교육지원청_12148D08D083",
            "course_branch_codes": ["seobu"],
            "name": "서부교육지원청",
            "course_addresses": [],
        },
    ):
        resolution = deterministic_resolution(
            branch,
            "부산광역시",
            {},
            {},
            {},
        )
        assert resolution is not None
        assert resolution.method == "official_provider_branch_location"
        assert resolution.candidate.verified is True


def test_venue_split_uses_physical_facility_not_room() -> None:
    assert venue_facility_name(
        "MUNI_TEST",
        "해운대인문학도서관 지하1층 배움터2실",
    ) == ("해운대인문학도서관", "")
    assert venue_facility_name(
        "MUNI_TEST",
        "거제1동 행정복지센터 2층 평생학습실",
    ) == ("거제1동 행정복지센터", "")
    assert venue_facility_name("MUNI_TEST", "301") == (
        "",
        "room_only_or_unusable",
    )
    assert venue_facility_name("MUNI_TEST", "온라인(ZOOM)") == (
        "",
        "non_physical_venue",
    )
    assert venue_facility_name(
        "MUNI_RESVE_YONGIN_GO_KR_221336AC",
        (
            "경기도 용인시 기흥구 강남서로 38 "
            "1층 별관 안전체험교실 (구갈동) 지도보기"
        ),
    ) == ("기흥구 꿈이룸 안전체험교실", "")
    assert venue_facility_name(
        "MUNI_RESVE_YONGIN_GO_KR_221336AC",
        (
            "경기도 용인시 수지구 풍덕천로 86 "
            "신월초등학교 5층 안전체험교실 (풍덕천동) 지도보기"
        ),
    ) == ("수지구 꿈이룸 안전체험교실", "")
    assert venue_facility_name(
        "MUNI_TEST",
        "중앙도서관 또는 부곡글고운도서관",
    )[0] == ""
    assert venue_facility_name(
        "MUNI_WWW_HAEUNDAE_GO_KR_E2AD27FA",
        "제2연습실",
    ) == ("해운대문화회관", "")
    assert venue_facility_name(
        "MUNI_TEST",
        "서부학습공간 311호(서동대로 1557)",
    ) == ("서부학습공간", "")
    assert venue_facility_name(
        "MUNI_TEST",
        "경기도 용인시 처인구 원삼면 농촌파크로 80-1 종합체험관 지도보기",
    ) == ("종합체험관", "")
    assert venue_facility_name(
        "MUNI_TEST",
        "경남 고성군 고성읍 성내로 130",
    ) == ("", "address_without_facility_name")
    assert venue_facility_name(
        "MUNI_TEST",
        "거제평생학습관 거제대로 180, 거제평생학습관 2층 2강의실",
    ) == ("거제평생학습관", "")
    assert (
        facility_name_without_address(
            "MUNI_TEST",
            "서초구 강남대로 167 서초정원센터",
        )
        == "서초정원센터"
    )
    assert top_level_comma_parts(
        "포인댄스(마들로11길 73, 7층)"
    ) == ["포인댄스(마들로11길 73, 7층)"]
    assert venue_facility_name(
        "MUNI_RESVE_YONGIN_GO_KR_221336AC",
        "경기도 용인시 처인구 남사읍 처인성로 673 지도보기",
    ) == ("처인성역사교육관", "")
    assert venue_facility_name(
        "MUNI_WWW_SEOGU_GO_KR_E4434123",
        "서구 월평중로 4",
    ) == ("", "address_without_facility_name")
    assert venue_facility_name(
        "MUNI_WWW_GOYANG_GO_KR_AFE8FBDD",
        "고양특례시 문예회관(경기도 고양특례시 덕양구 고양시청로 10)",
    ) == ("고양시문예회관", "")


def test_venue_split_does_not_spread_partial_course_address() -> None:
    group = VenueGroup(
        parent={
            "id": "branch-id",
            "provider": "MUNI_TEST",
            "name": "시 전체",
        },
        facility_name="시 전체",
        courses=[
            {
                "is_active": True,
                "venue_name": "시 전체",
                "venue_address": "경기도 수원시 팔달로 1",
            },
            {
                "is_active": True,
                "venue_name": "시 전체",
                "venue_address": "",
            },
        ],
    )

    resolution, reason = resolve_group(
        group,
        "경기도 수원시",
        {},
        {},
        None,
    )

    assert resolution is None
    assert reason == "no_trusted_database_match"


def test_venue_split_branch_code_matches_writer_empty_address_identity() -> None:
    from Crawler.Crawler_MunicipalYaml import MunicipalDbWriter

    assert stable_branch_code(
        "MUNI_TEST",
        "해운대인문학도서관",
    ) == MunicipalDbWriter("MUNI_TEST").stable_branch_code(
        "해운대인문학도서관",
        "",
    )


def test_venue_split_report_reaudits_course_venue_address(tmp_path) -> None:
    group = VenueGroup(
        parent={
            "provider": "MUNI_TEST",
            "name": "통합 체험교실",
            "region_sido": "경기도",
            "region_sigungu": "용인시",
            "course_localities": ["경기도 용인시"],
        },
        facility_name="기흥구 안전체험교실",
        courses=[
            {
                "is_active": True,
                "venue_name": "기흥구 안전체험교실",
                "venue_address": "경기도 용인시 기흥구 강남서로 38",
            }
        ],
    )
    report = tmp_path / "resolved.csv"
    with report.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "provider",
                "parent_name",
                "facility_name",
                "active_courses",
                "total_courses",
                "method",
                "matched_name",
                "address",
                "lat",
                "lon",
                "confidence",
                "venue_names",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "provider": "MUNI_TEST",
                "parent_name": "통합 체험교실",
                "facility_name": "기흥구 안전체험교실",
                "active_courses": 1,
                "total_courses": 1,
                "method": "course_venue_address",
                "matched_name": "기흥구 안전체험교실",
                "address": "경기도 용인시 기흥구 강남서로 38",
                "lat": "",
                "lon": "",
                "confidence": 90,
                "venue_names": "기흥구 안전체험교실",
            }
        )

    resolutions, errors = load_audited_report_resolutions(
        report,
        [group],
        {},
        82,
    )

    assert errors == []
    assert len(resolutions) == 1
    assert resolutions[0].candidate.address_source == (
        "COURSE_VENUE_ADDRESS_AUDITED_REPORT"
    )
    assert resolutions[0].candidate.lat is None
