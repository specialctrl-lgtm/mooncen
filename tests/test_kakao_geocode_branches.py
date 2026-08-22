from __future__ import annotations

import sys
from pathlib import Path

import pytest
import requests

from tools.maintenance import kakao_geocode_branches as geocoder


class JsonResponse:
    def __init__(self, payload, status_code: int = 200, headers=None):
        self.payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            response = requests.Response()
            response.status_code = self.status_code
            response.headers.update(self.headers)
            raise requests.HTTPError(f"status={self.status_code}", response=response)

    def json(self):
        return self.payload


def test_geocoder_never_attempts_runtime_schema_changes():
    source = Path(geocoder.__file__).read_text(encoding="utf-8")

    assert "ALTER TABLE" not in source.upper()


def test_load_api_key_accepts_only_server_side_kakao_key(monkeypatch):
    monkeypatch.setattr(geocoder, "load_dotenv", lambda *_args, **_kwargs: None)
    monkeypatch.delenv("KAKAO_MAPS_REST_API_KEY", raising=False)
    monkeypatch.delenv("MoonCenKakaoMapsRestApiKey", raising=False)
    monkeypatch.setenv("VITE_KAKAO_MAPS_JAVASCRIPT_KEY", "browser-key")

    with pytest.raises(RuntimeError, match="Kakao Maps REST API key is missing"):
        geocoder.load_api_key()

    monkeypatch.setenv("KAKAO_MAPS_REST_API_KEY", "server-key")
    assert geocoder.load_api_key() == "server-key"


def test_existing_address_uses_kakao_address_search_and_maps_xy(monkeypatch):
    calls = []

    def fake_get(url, *, headers, params, timeout):
        calls.append((url, headers, params, timeout))
        return JsonResponse(
            {
                "documents": [
                    {
                        "address_name": "서울 중구 태평로1가 31",
                        "address_type": "ROAD_ADDR",
                        "x": "126.9783882",
                        "y": "37.5666103",
                        "road_address": {"address_name": "서울 중구 세종대로 110"},
                    }
                ]
            }
        )

    monkeypatch.setattr(geocoder.requests, "get", fake_get)
    candidate = geocoder.geocode_branch(
        "server-rest-key",
        "CULTURE_FACILITY",
        "서울도서관",
        "서울특별시 중구 세종대로 110",
        7,
    )

    assert candidate is not None
    assert candidate.lat == pytest.approx(37.5666103)
    assert candidate.lon == pytest.approx(126.9783882)
    assert candidate.formatted_address == "서울 중구 세종대로 110"
    assert candidate.source == "KAKAO_LOCAL_ADDRESS"
    assert calls == [
        (
            geocoder.KAKAO_ADDRESS_SEARCH_URL,
            {"Authorization": "KakaoAK server-rest-key"},
            {
                "query": "서울특별시 중구 세종대로 110",
                "analyze_type": "similar",
                "size": 5,
            },
            7,
        )
    ]
    assert "key" not in calls[0][2]


def test_address_search_rejects_unrelated_similar_result(monkeypatch):
    def fake_get(_url, *, headers, params, timeout):
        assert headers == {"Authorization": "KakaoAK server-rest-key"}
        assert params["query"] == "세종특별자치시 호려울로 42"
        assert timeout == 3
        return JsonResponse(
            {
                "documents": [
                    {
                        "address_type": "ROAD_ADDR",
                        "road_address_name": "부산광역시 해운대구 해운대로 42",
                        "address_name": "부산광역시 해운대구 우동 1",
                        "x": "129.1604",
                        "y": "35.1631",
                    }
                ]
            }
        )

    monkeypatch.setattr(geocoder.requests, "get", fake_get)

    assert (
        geocoder.geocode_branch(
            "server-rest-key",
            "SEJONG_SJFMC_EDUCATION",
            "세종특별자치시 · 보람수영장",
            "세종특별자치시 호려울로 42",
            3,
            address_search_only=True,
        )
        is None
    )


@pytest.mark.parametrize(
    ("requested", "returned", "expected"),
    [
        ("서울특별시 중구 세종대로 110", "서울 중구 세종대로 110", True),
        ("세종특별자치시 연서면 당산로 291", "세종 연서면 당산로 291", True),
        ("세종특별자치시 호려울로 42", "세종특별자치시 호려울로 43", False),
        ("세종특별자치시 호려울로 42", "부산광역시 해운대로 42", False),
    ],
)
def test_address_similarity_requires_same_location_identity(requested, returned, expected):
    assert geocoder.addresses_refer_to_same_location(requested, returned) is expected


def test_missing_address_uses_kakao_keyword_search(monkeypatch):
    calls = []

    def fake_get(url, *, headers, params, timeout):
        calls.append((url, headers, params, timeout))
        return JsonResponse(
            {
                "documents": [
                    {
                        "id": "1234",
                        "place_name": "이마트 성수점",
                        "category_group_code": "MT1",
                        "address_name": "서울 성동구 성수동2가 333-16",
                        "road_address_name": "서울 성동구 뚝섬로 379",
                        "x": "127.0598",
                        "y": "37.5399",
                    }
                ]
            }
        )

    monkeypatch.setattr(geocoder.requests, "get", fake_get)
    monkeypatch.setattr(geocoder.time, "sleep", lambda _seconds: None)
    candidate = geocoder.geocode_branch("server-rest-key", "EMART", "이마트 성수점", None, 9)

    assert candidate is not None
    assert candidate.source == "KAKAO_LOCAL_KEYWORD"
    assert candidate.place_id == "1234"
    assert candidate.matched_name == "이마트 성수점"
    assert candidate.confidence >= 75
    assert calls
    assert len(calls) == 1
    assert all(call[0] == geocoder.KAKAO_KEYWORD_SEARCH_URL for call in calls)
    assert all(call[1] == {"Authorization": "KakaoAK server-rest-key"} for call in calls)
    assert all("key" not in call[2] for call in calls)


def test_address_only_mode_does_not_fall_back_to_keyword_search(monkeypatch):
    calls = []

    def fake_get(url, *, headers, params, timeout):
        calls.append((url, headers, params, timeout))
        return JsonResponse({"documents": []})

    monkeypatch.setattr(geocoder.requests, "get", fake_get)
    monkeypatch.setattr(geocoder.time, "sleep", lambda _seconds: None)

    candidate = geocoder.geocode_branch(
        "server-rest-key",
        "TEST_PROVIDER",
        "테스트 시설",
        "서울특별시 중구 세종대로 110",
        3,
        address_search_only=True,
    )

    assert candidate is None
    assert len(calls) == 1
    assert calls[0][0] == geocoder.KAKAO_ADDRESS_SEARCH_URL


def test_region_keyword_mode_rejects_a_result_from_another_district(monkeypatch):
    calls = []

    def fake_get(url, *, headers, params, timeout):
        calls.append((url, headers, params, timeout))
        return JsonResponse(
            {
                "documents": [
                    {
                        "id": "wrong-district",
                        "place_name": "테스트 시설",
                        "road_address_name": "서울 강동구 아리수로 1",
                        "address_name": "서울 강동구 고덕동 1",
                        "x": "127.1500",
                        "y": "37.5500",
                    },
                    {
                        "id": "correct-district",
                        "place_name": "테스트 시설",
                        "road_address_name": "서울 강남구 테헤란로 1",
                        "address_name": "서울 강남구 역삼동 1",
                        "x": "127.0276",
                        "y": "37.4979",
                    },
                ]
            }
        )

    monkeypatch.setattr(geocoder.requests, "get", fake_get)
    monkeypatch.setattr(geocoder.time, "sleep", lambda _seconds: None)

    diagnostics = {}
    candidate = geocoder.geocode_branch(
        "server-rest-key",
        "TEST_PROVIDER",
        "테스트 시설",
        None,
        3,
        expected_locality="서울특별시 강남구",
        diagnostics=diagnostics,
    )

    assert candidate is not None
    assert candidate.place_id == "correct-district"
    assert candidate.confidence == 85
    assert calls[0][0] == geocoder.KAKAO_KEYWORD_SEARCH_URL
    assert calls[0][2]["query"].startswith("서울특별시 강남구 ")
    assert diagnostics["region_mismatch_count"] == 1
    assert diagnostics["rejected_candidates"][0]["rejection_reason"] == "region_mismatch"


def test_region_keyword_mode_prefers_the_parent_facility_over_a_room_name(monkeypatch):
    calls = []

    def fake_get(url, *, headers, params, timeout):
        calls.append((url, headers, params, timeout))
        return JsonResponse(
            {
                "documents": [
                    {
                        "id": "parent-facility",
                        "place_name": "화천청소년수련관",
                        "road_address_name": "강원 화천군 화천읍 산천어길 79",
                        "address_name": "강원 화천군 화천읍 중리 241-4",
                        "x": "127.7100",
                        "y": "38.1050",
                    }
                ]
            }
        )

    monkeypatch.setattr(geocoder.requests, "get", fake_get)
    monkeypatch.setattr(geocoder.time, "sleep", lambda _seconds: None)

    candidate = geocoder.geocode_branch(
        "server-rest-key",
        "TEST_PROVIDER",
        "화천청소년수련관(3층 강의실)",
        None,
        3,
        expected_locality="강원특별자치도 화천군",
    )

    assert candidate is not None
    assert candidate.confidence == 85
    assert len(calls) == 1
    assert calls[0][2]["query"] == "강원특별자치도 화천군 화천청소년수련관"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("화천청소년수련관(3층 강의실)", "화천청소년수련관"),
        ("고덕1동 주민센터 4층 강당", "고덕1동 주민센터"),
        ("주민센터_상계9동", "상계9동 주민센터"),
    ],
)
def test_region_facility_name_removes_only_room_level_suffixes(name, expected):
    assert geocoder.region_facility_name("TEST_PROVIDER", name) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("고운동 남측 복컴 2층 문화관람실", "고운동 남측 행복누림터"),
        ("고운동 북측 복컴 4층 문화교실", "고운동 북측 행복누림터"),
        ("나성동 나성동 복컴3층 gx룸", "나성동 행복누림터"),
        ("대평동 사랑방2", "대평동 행복누림터"),
        ("다정동 G.X룸", ""),
        ("도담동 3층 바리스타실", "도담동 행복누림터"),
        ("보람동 다목적강당(2층)", "보람동 행복누림터"),
        ("소정면 소정면 행정복지센터 2층 대회의실", "소정면 행정복지센터"),
        ("어진동 어진동 행복누림터 2층 문화1실", "어진동 행복누림터"),
        ("연동면 연동면 행복누림터 체육관", "연동면 행복누림터"),
        ("연서면 연서면사무소 2층 문화사랑방(대첩로 238)", "연서면행정복지센터"),
        ("연서면 봉암출장소 2층", "연서면행정복지센터 봉암출장소"),
        ("부강면 복지회관 3층", "부강면문화복지회관"),
        ("부강면 신협 지하1층", "세종부강신협 본점"),
        ("장군면 장군면 복지회관 1층", "장군면복지회관"),
        ("소담동 다목적홀(1층)", "소담동 행복누림터"),
        ("아름동 아름9실", "아름동 행복누림터"),
        ("전동면 전동면 복컴 3층 다목적강당", "전동면 복합커뮤니티센터"),
        ("전의면 복컴 탁구실", "전의면행복누림터"),
        ("조치원읍 조치원읍 복컴 문화교실1(1층)", "조치원읍 행복누림터"),
        ("종촌동 문화3실(4F)", "종촌동 행복누림터"),
        ("한솔동 정음관 다목적실", "한솔동 행복누림터 정음관"),
        ("한솔동 훈민관 203호(누리실)", "한솔동 행복누림터 훈민관"),
        ("연기면 세종필드 골프연습장", "세종필드골프클럽 골프연습장"),
        ("연기면 연남초등학교", "연남초등학교"),
        ("연서면 갤러리 985(쌍류예술촌길 22)", "갤러리 985"),
        ("전동면 세종생활폐기물종합처리시설", "세종시 생활폐기물 종합처리시설"),
        ("전동면 전동 게이트볼장", "전동면게이트볼장"),
        ("전의면 면사무소 대회의실", "전의면행정복지센터"),
        ("전의면 전의체육공원", "전의생활체육공원"),
    ],
)
def test_sejong_emd_facility_name_preserves_facilities_and_removes_rooms(name, expected):
    assert geocoder.region_facility_name(geocoder.SEJONG_EMD_EDUCATION_PROVIDER, name) == expected


def test_sejong_emd_region_queries_use_one_canonical_facility_query():
    assert geocoder.build_region_queries(
        geocoder.SEJONG_EMD_EDUCATION_PROVIDER,
        "나성동 나성동 복컴3층 gx룸",
        "세종특별자치시",
        "세종특별자치시",
    ) == ["세종특별자치시 나성동 행복누림터"]

    # This remains the generic behavior for every other provider.
    assert geocoder.region_facility_name("TEST_PROVIDER", "다정동 G.X룸") == "다정동 G.X룸"

    assert geocoder.build_region_queries(
        geocoder.SEJONG_EMD_EDUCATION_PROVIDER,
        "연서면행정복지센터 봉암출장소",
        "세종특별자치시",
        "세종특별자치시",
    ) == ["세종특별자치시 연서면행정복지센터 봉암출장소"]


@pytest.mark.parametrize(
    "name",
    [
        "연기면 파크골프장/복지회관1층",
        "나성동 나성동 복컴 2층 문화사랑방1,한솔파크골프장,오가낭뜰근린공원",
        "아름동 아름1실/남세종청소년센터",
        "보람동 세미나실(5층) 또는 보람국민체육센터",
    ],
)
def test_sejong_emd_multiple_physical_venues_fail_closed(name):
    assert geocoder.sejong_emd_facility_name(name) == ""
    assert geocoder.build_queries(geocoder.SEJONG_EMD_EDUCATION_PROVIDER, name) == []


def test_sejong_emd_keyword_result_requires_exact_canonical_place_name(monkeypatch):
    def fake_get(_url, *, headers, params, timeout):
        assert headers == {"Authorization": "KakaoAK server-rest-key"}
        assert params["query"] == "세종특별자치시 나성동 행복누림터"
        assert timeout == 3
        return JsonResponse(
            {
                "documents": [
                    {
                        "id": "wrong-neighbourhood",
                        "place_name": "새롬동 행복누림터",
                        "road_address_name": "세종특별자치시 새롬중앙로 44",
                        "x": "127.2510",
                        "y": "36.4860",
                    },
                    {
                        "id": "exact-place",
                        "place_name": "나성동 행복누림터",
                        "road_address_name": "세종특별자치시 갈매로 280",
                        "x": "127.2643637",
                        "y": "36.4890332",
                    },
                ]
            }
        )

    monkeypatch.setattr(geocoder.requests, "get", fake_get)
    candidate = geocoder.geocode_branch(
        "server-rest-key",
        geocoder.SEJONG_EMD_EDUCATION_PROVIDER,
        "나성동 나성동 복컴3층 gx룸",
        None,
        3,
        expected_locality="세종특별자치시 세종특별자치시",
    )

    assert candidate is not None
    assert candidate.place_id == "exact-place"
    assert candidate.matched_name == "나성동 행복누림터"
    assert candidate.formatted_address == "세종특별자치시 갈매로 280"
    assert candidate.confidence >= 85


def test_sejong_emd_missing_venue_never_reaches_kakao(monkeypatch):
    def unexpected_request(*_args, **_kwargs):
        raise AssertionError("missing Sejong venue reached the Kakao API")

    monkeypatch.setattr(geocoder.requests, "get", unexpected_request)
    name = "전동면 주민자치프로그램"

    assert geocoder.build_queries(geocoder.SEJONG_EMD_EDUCATION_PROVIDER, name) == []
    assert (
        geocoder.build_region_queries(
            geocoder.SEJONG_EMD_EDUCATION_PROVIDER,
            name,
            "세종특별자치시",
            "세종특별자치시",
        )
        == []
    )
    assert (
        geocoder.geocode_branch(
            "server-rest-key",
            geocoder.SEJONG_EMD_EDUCATION_PROVIDER,
            name,
            None,
            3,
            expected_locality="세종특별자치시 세종특별자치시",
        )
        is None
    )


@pytest.mark.parametrize(
    ("address", "sido", "sigungu", "expected"),
    [
        ("서울 강남구 테헤란로 1", "서울특별시", "강남구", True),
        ("서울 강동구 아리수로 1", "서울특별시", "강남구", False),
        ("경기 수원시 팔달구 효원로 1", "경기도", "수원시 팔달구", True),
        ("경기 수원시 영통구 광교로 1", "경기도", "수원시 팔달구", False),
        ("경기 화성시 동탄대로 1", "경기도", "화성시 동탄구", True),
        ("충북 청주시 상당구 상당로 1", "충청북도", "청주시 상당구", True),
    ],
)
def test_address_matches_every_expected_region_token(address, sido, sigungu, expected):
    assert geocoder.address_matches_region(address, sido, sigungu) is expected


@pytest.mark.parametrize(
    ("locality", "expected"),
    [
        ("\uacbd\uae30\ub3c4 \uc218\uc6d0\uc2dc \uc601\ud1b5\uad6c", ("\uacbd\uae30\ub3c4", "\uc218\uc6d0\uc2dc \uc601\ud1b5\uad6c")),
        ("\uc11c\uc6b8\ud2b9\ubcc4\uc2dc \uac15\ub0a8\uad6c", ("\uc11c\uc6b8\ud2b9\ubcc4\uc2dc", "\uac15\ub0a8\uad6c")),
        ("\uacbd\uae30\ub3c4", None),
        ("\uc218\uc6d0\uc2dc \uc601\ud1b5\uad6c", None),
        ("", None),
    ],
)
def test_configured_locality_parts_requires_province_and_municipality(locality, expected):
    assert geocoder.configured_locality_parts(locality) == expected


def test_fetch_targets_address_only_filters_and_prioritizes_known_addresses(monkeypatch):
    executed = []

    class Cursor:
        def execute(self, statement, params):
            executed.append((statement, params))

        def fetchall(self):
            return []

    class CursorContext:
        def __enter__(self):
            return Cursor()

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(geocoder, "get_db_cursor", CursorContext)

    assert (
        geocoder.fetch_targets(
            None,
            update_all=False,
            verify_existing=False,
            limit=None,
            with_active_courses=True,
            address_only=True,
        )
        == []
    )

    statement, _params = executed[0]
    assert "AND address IS NOT NULL AND btrim(address) <> ''" in statement
    assert "ORDER BY CASE" in statement
    assert "EXISTS" in statement

    executed.clear()
    assert (
        geocoder.fetch_targets(
            None,
            update_all=False,
            verify_existing=False,
            limit=None,
            region_keyword_only=True,
        )
        == []
    )
    statement, _params = executed[0]
    assert "AND (address IS NULL OR btrim(address) = '')" in statement
    assert "AND region_sido IS NOT NULL" in statement
    assert "AND region_sigungu IS NOT NULL" in statement
    assert "region_sido, region_sigungu" in statement

    executed.clear()
    assert (
        geocoder.fetch_targets(
            None,
            update_all=False,
            verify_existing=False,
            limit=None,
            address_only=True,
            retry_after_days=30,
        )
        == []
    )
    statement, params = executed[0]
    assert "location_checked_at < now() - make_interval" in statement
    assert "geocode_next_retry_at IS NULL" in statement
    assert params["retry_after_days"] == 30

    executed.clear()
    assert (
        geocoder.fetch_targets(
            None,
            update_all=False,
            verify_existing=True,
            limit=None,
            coordinate_source_prefix="GOOGLE",
        )
        == []
    )
    statement, params = executed[0]
    assert "coordinate_source ILIKE %(coordinate_source_prefix)s" in statement
    assert params["coordinate_source_prefix"] == "GOOGLE%"


def test_fetch_targets_course_address_mode_requires_one_active_unique_address(monkeypatch):
    executed = []

    class Cursor:
        def execute(self, statement, params):
            executed.append((statement, params))

        def fetchall(self):
            return [
                {
                    "id": "branch-id",
                    "provider": "TEST_PROVIDER",
                    "branch_code": "branch-code",
                    "name": "\uc601\ud765\uc218\ubaa9\uc6d0",
                    "address": None,
                    "lat": None,
                    "lon": None,
                    "course_address": "\uacbd\uae30\ub3c4 \uc218\uc6d0\uc2dc \uc601\ud1b5\uad6c \uc601\ud1b5\ub85c 435",
                }
            ]

    class CursorContext:
        def __enter__(self):
            return Cursor()

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(geocoder, "get_db_cursor", CursorContext)

    rows = geocoder.fetch_targets(
        None,
        update_all=False,
        verify_existing=False,
        limit=10,
        with_active_courses=True,
        course_address_only=True,
    )

    assert len(rows) == 1
    assert rows[0]["course_address"].endswith("435")
    statement, _params = executed[0]
    assert "SELECT DISTINCT btrim(ca.venue_address)" in statement
    assert "COALESCE(ca.is_active, true) = true" in statement
    assert "HAVING COUNT(*) = 1" in statement


def test_fetch_targets_configured_locality_filters_before_applying_limit(monkeypatch):
    executed = []

    class Cursor:
        def execute(self, statement, params):
            executed.append((statement, params))

        def fetchall(self):
            return [
                {
                    "id": "ineligible",
                    "provider": "MULTI_REGION_PROVIDER",
                    "branch_code": "one",
                    "name": "one",
                    "address": None,
                    "lat": None,
                    "lon": None,
                },
                {
                    "id": "eligible",
                    "provider": "SUWON_PROVIDER",
                    "branch_code": "two",
                    "name": "two",
                    "address": None,
                    "lat": None,
                    "lon": None,
                },
            ]

    class CursorContext:
        def __enter__(self):
            return Cursor()

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(geocoder, "get_db_cursor", CursorContext)

    rows = geocoder.fetch_targets(
        None,
        update_all=False,
        verify_existing=False,
        limit=1,
        configured_locality_only=True,
        configured_provider_localities={
            "MULTI_REGION_PROVIDER": "\uacbd\uae30\ub3c4",
            "SUWON_PROVIDER": "\uacbd\uae30\ub3c4 \uc218\uc6d0\uc2dc",
        },
    )

    assert [row["id"] for row in rows] == ["eligible"]
    assert rows[0]["inferred_region_sido"] == "\uacbd\uae30\ub3c4"
    assert rows[0]["inferred_region_sigungu"] == "\uc218\uc6d0\uc2dc"
    assert rows[0]["location_locality_source"] == "configured_unique_provider_locality"
    statement, _params = executed[0]
    assert "LIMIT %(limit)s" not in statement
    assert "region_sido IS NULL" in statement


def test_fetch_targets_configured_locality_respects_an_explicit_empty_registry(
    monkeypatch,
):
    class Cursor:
        def execute(self, _statement, _params):
            pass

        def fetchall(self):
            return [
                {
                    "id": "target",
                    "provider": "UNREVIEWED_PROVIDER",
                    "branch_code": "one",
                    "name": "one",
                    "address": None,
                    "lat": None,
                    "lon": None,
                }
            ]

    class CursorContext:
        def __enter__(self):
            return Cursor()

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(geocoder, "get_db_cursor", CursorContext)
    monkeypatch.setattr(
        geocoder,
        "load_configured_provider_localities",
        lambda: pytest.fail("an explicitly supplied empty registry must not be reloaded"),
    )

    rows = geocoder.fetch_targets(
        None,
        update_all=False,
        verify_existing=False,
        limit=10,
        configured_locality_only=True,
        configured_provider_localities={},
    )

    assert rows == []


def test_safe_location_modes_reject_existing_location_options(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["kakao_geocode_branches.py", "--region-keyword-only", "--update-all"],
    )

    with pytest.raises(SystemExit):
        geocoder.parse_args()

    monkeypatch.setattr(
        sys,
        "argv",
        ["kakao_geocode_branches.py", "--course-address-only", "--clear-existing"],
    )
    with pytest.raises(SystemExit):
        geocoder.parse_args()


def test_coordinate_source_prefix_requires_verify_existing(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["kakao_geocode_branches.py", "--coordinate-source-prefix", "GOOGLE"],
    )

    with pytest.raises(SystemExit):
        geocoder.parse_args()

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "kakao_geocode_branches.py",
            "--verify-existing",
            "--coordinate-source-prefix",
            "GOOGLE",
        ],
    )
    assert geocoder.parse_args().coordinate_source_prefix == "GOOGLE"


def test_overlong_keyword_variants_are_removed_before_request():
    supported_name = "a" * geocoder.KAKAO_MAX_QUERY_CHARACTERS
    queries = geocoder.build_queries("UNLISTED_PROVIDER", supported_name)

    assert queries == [supported_name]
    assert all(len(query) <= geocoder.KAKAO_MAX_QUERY_CHARACTERS for query in queries)


def test_overlong_query_does_not_consume_budget_or_make_http_request(monkeypatch):
    budget = geocoder.RequestBudget(1)

    def unexpected_request(*_args, **_kwargs):
        raise AssertionError("overlong Kakao query reached the network")

    monkeypatch.setattr(geocoder.requests, "get", unexpected_request)

    assert (
        geocoder._request_documents(
            "server-rest-key",
            geocoder.KAKAO_KEYWORD_SEARCH_URL,
            {"query": "a" * (geocoder.KAKAO_MAX_QUERY_CHARACTERS + 1)},
            3,
            request_budget=budget,
        )
        == []
    )
    assert budget.used == 0


def test_kakao_request_failure_does_not_expose_rest_key(monkeypatch):
    api_key = "sensitive-kakao-rest-key-that-must-not-leak"

    def fail_request(url, *, headers, params, timeout):
        response = requests.Response()
        response.status_code = 401
        response.url = url
        raise requests.HTTPError(
            f"401 Authorization={headers['Authorization']}",
            response=response,
        )

    monkeypatch.setattr(geocoder.requests, "get", fail_request)

    with pytest.raises(RuntimeError) as error:
        geocoder.geocode_branch(api_key, "EMART", "이마트 성수점", None, 3)

    assert api_key not in str(error.value)
    assert "status=401" in str(error.value)


def test_kakao_quota_error_is_not_retried(monkeypatch):
    calls = []

    def fail_request(url, *, headers, params, timeout):
        calls.append((url, headers, params, timeout))
        return JsonResponse({"msg": "API limit has been exceeded."}, status_code=429)

    monkeypatch.setattr(geocoder.requests, "get", fail_request)

    with pytest.raises(RuntimeError, match="status=429"):
        geocoder._request_documents(
            "server-rest-key",
            geocoder.KAKAO_ADDRESS_SEARCH_URL,
            {"query": "서울 중구 세종대로 110"},
            3,
        )

    assert len(calls) == 1


def test_kakao_server_error_has_one_bounded_retry(monkeypatch):
    calls = []
    sleeps = []

    def request(url, *, headers, params, timeout):
        calls.append((url, headers, params, timeout))
        if len(calls) == 1:
            return JsonResponse({}, status_code=503)
        return JsonResponse({"documents": []})

    monkeypatch.setattr(geocoder.requests, "get", request)
    monkeypatch.setattr(geocoder.time, "sleep", sleeps.append)

    documents = geocoder._request_documents(
        "server-rest-key",
        geocoder.KAKAO_ADDRESS_SEARCH_URL,
        {"query": "서울 중구 세종대로 110"},
        3,
    )

    assert documents == []
    assert len(calls) == 2
    assert sleeps == [1.0]


def test_kakao_retry_after_is_capped(monkeypatch):
    calls = []
    sleeps = []

    def request(url, *, headers, params, timeout):
        calls.append((url, headers, params, timeout))
        if len(calls) == 1:
            return JsonResponse({}, status_code=503, headers={"Retry-After": "999"})
        return JsonResponse({"documents": []})

    monkeypatch.setattr(geocoder.requests, "get", request)
    monkeypatch.setattr(geocoder.time, "sleep", sleeps.append)

    geocoder._request_documents(
        "server-rest-key",
        geocoder.KAKAO_ADDRESS_SEARCH_URL,
        {"query": "서울 중구 세종대로 110"},
        3,
    )

    assert len(calls) == 2
    assert sleeps == [geocoder.KAKAO_MAX_RETRY_DELAY_SECONDS]


def test_request_cache_reuses_success_without_storing_rest_key(monkeypatch):
    calls = []
    cache: geocoder.RequestCache = {}

    def request(url, *, headers, params, timeout):
        calls.append((url, headers, params, timeout))
        return JsonResponse({"documents": [{"x": "126.978", "y": "37.566"}]})

    monkeypatch.setattr(geocoder.requests, "get", request)
    params = {"query": "서울 중구 세종대로 110"}

    first = geocoder._request_documents(
        "first-server-rest-key",
        geocoder.KAKAO_ADDRESS_SEARCH_URL,
        params,
        3,
        cache,
    )
    second = geocoder._request_documents(
        "second-server-rest-key",
        geocoder.KAKAO_ADDRESS_SEARCH_URL,
        params,
        3,
        cache,
    )

    assert first == second
    assert len(calls) == 1
    assert "first-server-rest-key" not in repr(cache)
    assert "second-server-rest-key" not in repr(cache)


def test_request_budget_counts_http_attempts_but_not_cache_hits(monkeypatch):
    calls = []
    cache: geocoder.RequestCache = {}
    budget = geocoder.RequestBudget(1)

    def request(url, *, headers, params, timeout):
        calls.append((url, headers, params, timeout))
        return JsonResponse({"documents": []})

    monkeypatch.setattr(geocoder.requests, "get", request)
    params = {"query": "서울 중구 세종대로 110"}

    assert (
        geocoder._request_documents(
            "server-rest-key",
            geocoder.KAKAO_ADDRESS_SEARCH_URL,
            params,
            3,
            cache,
            budget,
        )
        == []
    )
    assert (
        geocoder._request_documents(
            "server-rest-key",
            geocoder.KAKAO_ADDRESS_SEARCH_URL,
            params,
            3,
            cache,
            budget,
        )
        == []
    )

    with pytest.raises(geocoder.RequestBudgetExceeded, match="used=1 limit=1"):
        geocoder._request_documents(
            "server-rest-key",
            geocoder.KAKAO_ADDRESS_SEARCH_URL,
            {"query": "서울 종로구 종로 1"},
            3,
            cache,
            budget,
        )

    assert budget.used == 1
    assert len(calls) == 1


def test_max_request_default_can_be_set_by_server_environment(monkeypatch):
    monkeypatch.setenv("KAKAO_GEOCODE_MAX_REQUESTS_PER_RUN", "321")
    monkeypatch.setattr(sys, "argv", ["kakao_geocode_branches.py"])

    assert geocoder.parse_args().max_requests == 321


@pytest.mark.parametrize("payload", [None, {}, {"documents": None}])
def test_kakao_response_requires_documents_list(monkeypatch, payload):
    monkeypatch.setattr(
        geocoder.requests,
        "get",
        lambda *_args, **_kwargs: JsonResponse(payload),
    )

    with pytest.raises(RuntimeError, match="documents list"):
        geocoder.geocode_branch("server-rest-key", "EMART", "이마트 성수점", None, 3)


def test_out_of_service_area_coordinate_is_ignored(monkeypatch):
    monkeypatch.setattr(
        geocoder.requests,
        "get",
        lambda *_args, **_kwargs: JsonResponse(
            {
                "documents": [
                    {
                        "id": "outside",
                        "place_name": "이마트 성수점",
                        "address_name": "서울 성동구 성수동2가 333-16",
                        "x": "2.3522",
                        "y": "48.8566",
                    }
                ]
            }
        ),
    )
    monkeypatch.setattr(geocoder.time, "sleep", lambda _seconds: None)

    assert geocoder.geocode_branch("server-rest-key", "EMART", "이마트 성수점", None, 3) is None


def _candidate(confidence: int) -> geocoder.GeocodeCandidate:
    return geocoder.GeocodeCandidate(
        query="test branch",
        formatted_address="대한민국 서울특별시 중구 세종대로 110",
        lat=37.5665,
        lon=126.9780,
        place_id="test-place",
        location_type="ROOFTOP",
        partial_match=False,
        confidence=confidence,
        raw_status="OK",
    )


def test_update_branch_persists_kakao_provenance(monkeypatch):
    writes = []

    class Cursor:
        def execute(self, statement, params):
            writes.append((statement, params))

    class CursorContext:
        def __enter__(self):
            return Cursor()

        def __exit__(self, *_args):
            return False

    candidate = _candidate(95)
    candidate.source = "KAKAO_LOCAL_ADDRESS"
    monkeypatch.setattr(geocoder, "get_db_cursor", CursorContext)

    geocoder.update_branch("branch-id", candidate, True)

    assert len(writes) == 1
    statement, params = writes[0]
    assert "address_source = %(source)s" in statement
    assert "coordinate_source = %(source)s" in statement
    assert params["source"] == "KAKAO_LOCAL_ADDRESS"
    assert "geocode_attempt_count = geocode_attempt_count + 1" in statement
    assert params["status"] == "resolved"
    assert params["reason_code"] == "kakao_verified"
    assert "test-place" in params["candidates_json"]


def test_record_geocode_outcome_persists_reason_retry_and_bounded_evidence(monkeypatch):
    writes = []

    class Cursor:
        def execute(self, statement, params):
            writes.append((statement, params))

    class CursorContext:
        def __enter__(self):
            return Cursor()

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(geocoder, "get_db_cursor", CursorContext)

    geocoder.record_geocode_outcome(
        "branch-id",
        "region_mismatch",
        "candidate_outside_expected_region",
        candidates=[{"address": "서울 강동구 아리수로 1", "rejection_reason": "region_mismatch"}],
        retry_after_days=30,
    )

    statement, params = writes[0]
    assert "geocode_last_attempt_at = now()" in statement
    assert params["status"] == "region_mismatch"
    assert params["reason_code"] == "candidate_outside_expected_region"
    assert params["retry_after_days"] == 30
    assert "서울 강동구" in params["candidates_json"]


def test_record_geocode_outcome_rejects_unbounded_or_unknown_tokens():
    with pytest.raises(ValueError, match="unsupported"):
        geocoder.record_geocode_outcome("branch-id", "invented", "reason")
    with pytest.raises(ValueError, match="machine-readable"):
        geocoder.record_geocode_outcome("branch-id", "no_result", "human readable reason")


@pytest.fixture
def isolated_main(monkeypatch):
    target = {
        "id": "branch-id",
        "provider": "TEST_PROVIDER",
        "branch_code": "test-branch",
        "name": "test branch",
        "address": None,
        "lat": None,
        "lon": None,
    }
    writes: list[tuple[str, geocoder.GeocodeCandidate, bool]] = []

    monkeypatch.setattr(geocoder, "load_api_key", lambda: "test-key")
    monkeypatch.setattr(
        geocoder,
        "fetch_targets",
        lambda provider, update_all, verify_existing, limit, **_kwargs: [target],
    )
    monkeypatch.setattr(geocoder, "print_summary", lambda: None)
    monkeypatch.setattr(geocoder.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(geocoder, "mark_branch_checked", lambda _branch_id: None)
    monkeypatch.setattr(geocoder, "record_geocode_outcome", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        geocoder,
        "update_branch",
        lambda branch_id, candidate, verified: writes.append((branch_id, candidate, verified)),
    )
    return writes


def test_main_does_not_persist_low_confidence_candidate_by_default(monkeypatch, isolated_main, capsys):
    monkeypatch.setattr(geocoder, "geocode_branch", lambda *_args: _candidate(74))
    monkeypatch.setattr(sys, "argv", ["kakao_geocode_branches.py"])

    geocoder.main()

    assert isolated_main == []
    assert "skipped: confidence is below the persistence threshold" in capsys.readouterr().out


def test_main_persists_verified_candidate_by_default(monkeypatch, isolated_main):
    candidate = _candidate(75)
    monkeypatch.setattr(geocoder, "geocode_branch", lambda *_args: candidate)
    monkeypatch.setattr(sys, "argv", ["kakao_geocode_branches.py"])

    geocoder.main()

    assert isolated_main == [("branch-id", candidate, True)]


def test_main_can_explicitly_persist_low_confidence_candidate(monkeypatch, isolated_main):
    candidate = _candidate(74)
    monkeypatch.setattr(geocoder, "geocode_branch", lambda *_args: candidate)
    monkeypatch.setattr(
        sys,
        "argv",
        ["kakao_geocode_branches.py", "--allow-low-confidence"],
    )

    geocoder.main()

    assert isolated_main == [("branch-id", candidate, False)]


def test_main_records_unresolved_branch_outcome_without_persisting_location(
    monkeypatch,
    isolated_main,
):
    outcomes = []
    monkeypatch.setattr(geocoder, "geocode_branch", lambda *_args: None)
    monkeypatch.setattr(
        geocoder,
        "record_geocode_outcome",
        lambda *args, **kwargs: outcomes.append((args, kwargs)),
    )
    monkeypatch.setattr(sys, "argv", ["kakao_geocode_branches.py"])

    geocoder.main()

    assert isolated_main == []
    assert outcomes == [
        (
            ("branch-id", "no_result", "kakao_no_result"),
            {"candidates": None, "retry_after_days": 7},
        )
    ]


def test_main_course_address_mode_uses_only_the_unique_course_address(monkeypatch, capsys):
    calls = []
    target = {
        "id": "branch-id",
        "provider": "TEST_PROVIDER",
        "branch_code": "test-branch",
        "name": "test branch",
        "address": None,
        "lat": None,
        "lon": None,
        "course_address": "\uacbd\uae30\ub3c4 \uc218\uc6d0\uc2dc \uc601\ud1b5\uad6c \uc601\ud1b5\ub85c 435",
    }
    monkeypatch.setattr(geocoder, "load_api_key", lambda: "test-key")
    monkeypatch.setattr(geocoder, "fetch_targets", lambda *_args, **_kwargs: [target])
    monkeypatch.setattr(geocoder, "print_summary", lambda: None)
    monkeypatch.setattr(geocoder.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(geocoder, "geocode_branch", lambda *args: calls.append(args) or None)
    monkeypatch.setattr(sys, "argv", ["kakao_geocode_branches.py", "--course-address-only", "--dry-run"])

    geocoder.main()

    assert calls[0][3] == target["course_address"]
    assert calls[0][7] is True
    assert calls[0][8] is None
    assert "kakao_course_address_no_result" in capsys.readouterr().out


def test_main_configured_locality_mode_passes_a_restricted_locality(monkeypatch, capsys):
    calls = []
    target = {
        "id": "branch-id",
        "provider": "TEST_PROVIDER",
        "branch_code": "test-branch",
        "name": "test branch",
        "address": None,
        "lat": None,
        "lon": None,
        "inferred_region_sido": "\uacbd\uae30\ub3c4",
        "inferred_region_sigungu": "\uc218\uc6d0\uc2dc \uc601\ud1b5\uad6c",
    }
    monkeypatch.setattr(geocoder, "load_api_key", lambda: "test-key")
    monkeypatch.setattr(geocoder, "fetch_targets", lambda *_args, **_kwargs: [target])
    monkeypatch.setattr(geocoder, "print_summary", lambda: None)
    monkeypatch.setattr(geocoder.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(geocoder, "geocode_branch", lambda *args: calls.append(args) or None)
    monkeypatch.setattr(sys, "argv", ["kakao_geocode_branches.py", "--configured-locality-only", "--dry-run"])

    geocoder.main()

    assert calls[0][3] is None
    assert calls[0][7] is False
    assert calls[0][8] == "\uacbd\uae30\ub3c4 \uc218\uc6d0\uc2dc \uc601\ud1b5\uad6c"
    assert "kakao_configured_locality_no_result" in capsys.readouterr().out


def test_dry_run_does_not_mark_unresolved_branch_checked(monkeypatch, isolated_main):
    outcomes = []
    monkeypatch.setattr(geocoder, "geocode_branch", lambda *_args: None)
    monkeypatch.setattr(
        geocoder,
        "record_geocode_outcome",
        lambda *args, **kwargs: outcomes.append((args, kwargs)),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["kakao_geocode_branches.py", "--dry-run"],
    )

    geocoder.main()

    assert isolated_main == []
    assert outcomes == []


def test_main_returns_nonzero_and_records_sanitized_request_failure(monkeypatch, isolated_main):
    outcomes = []
    monkeypatch.setattr(
        geocoder,
        "geocode_branch",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("Kakao Local request failed type=HTTPError status=403")),
    )
    monkeypatch.setattr(
        geocoder,
        "record_geocode_outcome",
        lambda *args, **kwargs: outcomes.append((args, kwargs)),
    )
    monkeypatch.setattr(sys, "argv", ["kakao_geocode_branches.py"])

    assert geocoder.main() == 1
    assert outcomes[0][0] == (
        "branch-id",
        "request_error",
        "kakao_authentication_failed",
    )
    assert outcomes[0][1]["retry_after_days"] == 1


def test_main_budget_exhaustion_is_partial_failure(monkeypatch, isolated_main):
    outcomes = []
    monkeypatch.setattr(
        geocoder,
        "geocode_branch",
        lambda *_args: (_ for _ in ()).throw(geocoder.RequestBudgetExceeded("used=1 limit=1")),
    )
    monkeypatch.setattr(
        geocoder,
        "record_geocode_outcome",
        lambda *args, **kwargs: outcomes.append((args, kwargs)),
    )
    monkeypatch.setattr(sys, "argv", ["kakao_geocode_branches.py"])

    assert geocoder.main() == 3
    assert outcomes[0][0] == (
        "branch-id",
        "quota_exhausted",
        "local_request_budget_exhausted",
    )
