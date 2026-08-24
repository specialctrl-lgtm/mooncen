from __future__ import annotations

import csv
from types import SimpleNamespace

import pytest

from tools.maintenance.backfill_branch_operator_addresses import (
    OperatorQuery,
    branch_administrative_center_name,
    load_report_resolutions,
    official_address_override,
    government_office_name,
    inferred_locality,
    operator_query,
    physical_locality,
    resolve_queries,
)
from tools.maintenance.backfill_missing_branch_addresses import AddressCandidate


def test_physical_locality_restores_current_official_regions() -> None:
    assert (
        physical_locality("전남광주통합특별시 서구")
        == "광주광역시 서구"
    )
    assert (
        physical_locality("전남광주통합특별시 강진군")
        == "전라남도 강진군"
    )
    assert physical_locality("경기도 과천시") == "경기도 과천시"
    assert physical_locality("인천광역시 서구") == "인천광역시 서해구"
    assert physical_locality("전라북도 김제시") == "전북특별자치도 김제시"


def test_government_office_name_uses_most_specific_unit() -> None:
    assert government_office_name("경기도 과천시") == "과천시청"
    assert government_office_name("서울특별시 종로구") == "종로구청"
    assert government_office_name("경기도 수원시 영통구") == "영통구청"


def test_operator_query_prefers_named_administrative_center() -> None:
    query = operator_query(
        {"name": "신안면"},
        "경상남도 산청군",
    )

    assert query is not None
    assert query.target_name == "신안면사무소"
    assert query.scope == "administrative_center"


def test_operator_query_falls_back_to_local_government_office() -> None:
    query = operator_query(
        {"name": "장소 별도 안내"},
        "경기도 광명시",
    )

    assert query is not None
    assert query.target_name == "광명시청"
    assert query.scope == "operator_office"


def test_inferred_locality_uses_branch_name_and_provider_fallbacks() -> None:
    assert inferred_locality(
        {"provider": "TEST", "name": "전라북도 부안군"},
        "",
    ) == "전북특별자치도 부안군"
    assert inferred_locality(
        {
            "provider": "DAEGU_BUKGU_RESERVATION",
            "name": "대구 북구 통합예약",
        },
        "",
    ) == "대구광역시 북구"


def test_administrative_center_name_uses_official_eup_myeon_alias() -> None:
    assert branch_administrative_center_name("신안면") == "신안면사무소"
    assert (
        branch_administrative_center_name("도화2.3동")
        == "도화2·3동 행정복지센터"
    )
    assert (
        branch_administrative_center_name("옥곡면 주민자치센터")
        == "옥곡면사무소"
    )


def test_exact_branch_operator_override_does_not_require_locality() -> None:
    branch = {
        "provider": "AK_PLAZA",
        "name": "AK PLAZA 문화아카데미",
    }

    query = operator_query(branch, "")

    assert query is not None
    assert query.locality == "경기도 평택시"
    assert query.target_name == "AK PLAZA"
    assert query.scope == "organization_office"
    assert official_address_override(
        query,
        branch["provider"],
        branch["name"],
    ) == (
        "경기도 평택시 평택로 51",
        "https://www.akplaza.com/etc/mobile",
    )


def test_exact_branch_operator_override_is_name_scoped() -> None:
    branch = {
        "provider": "ULSAN_EDU_BOOKING",
        "name": "울산광역시교육청 통합예약",
    }

    query = operator_query(branch, "")

    assert query is not None
    assert official_address_override(
        query,
        branch["provider"],
        "학생교육문화회관",
    ) is None


def test_official_override_geocodes_address_without_keyword_search(
    monkeypatch,
) -> None:
    from tools.maintenance import backfill_branch_operator_addresses as module

    query = OperatorQuery(
        locality="Seoul",
        target_name="Official office",
        scope="operator_office",
    )
    branch = {"provider": "MUNI_TEST", "name": "Aggregate branch"}
    calls: list[tuple[str, str]] = []

    class Resolver:
        requests = 1
        blocked_status = None

        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def place(self, _provider, target_name, locality):
            calls.append(("keyword", f"{locality} {target_name}"))
            raise AssertionError("official addresses must not use keyword search")

        def geocode_address(self, address, locality):
            calls.append(("address", address))
            return AddressCandidate(
                address=address,
                lat=37.5665,
                lon=126.9780,
                address_source="KAKAO_LOCAL_ADDRESS",
                coordinate_source="KAKAO_LOCAL_ADDRESS",
                confidence=95,
                verified=True,
                query=address,
            )

    monkeypatch.setattr(module, "KakaoResolver", Resolver)
    monkeypatch.setattr(module, "load_kakao_api_key", lambda: "rest-key")
    monkeypatch.setattr(
        module,
        "official_address_override",
        lambda *_args: ("Seoul official address 1", "https://official.example/"),
    )

    resolved, unresolved, request_count, blocked_status = resolve_queries(
        {query: [branch]},
        timeout=3,
        delay=0,
        min_score=82,
        max_requests=10,
        workers=2,
    )

    assert calls == [("address", "Seoul official address 1")]
    assert unresolved == []
    assert request_count == 1
    assert blocked_status is None
    assert len(resolved) == 1
    candidate = resolved[0].candidate
    assert candidate.address == "Seoul official address 1"
    assert candidate.address_source == "CURATED_OFFICIAL_OPERATOR_OFFICE"
    assert candidate.coordinate_source == "KAKAO_LOCAL_ADDRESS"


def test_resolve_queries_propagates_fatal_kakao_status(monkeypatch) -> None:
    from tools.maintenance import backfill_branch_operator_addresses as module

    query = OperatorQuery(
        locality="Seoul",
        target_name="Office",
        scope="organization_office",
    )
    branch = {"provider": "MUNI_TEST", "name": "Aggregate branch"}

    class Resolver:
        requests = 1
        blocked_status = 429

        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def place(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(module, "KakaoResolver", Resolver)
    monkeypatch.setattr(module, "load_kakao_api_key", lambda: "rest-key")
    monkeypatch.setattr(module, "official_address_override", lambda *_args: None)

    resolved, unresolved, request_count, blocked_status = resolve_queries(
        {query: [branch]},
        timeout=3,
        delay=0,
        min_score=82,
        max_requests=10,
        workers=2,
    )

    assert resolved == []
    assert len(unresolved) == 1
    assert request_count == 1
    assert blocked_status == 429


def test_operator_main_aborts_apply_on_fatal_kakao_status(
    monkeypatch,
    tmp_path,
) -> None:
    from tools.maintenance import backfill_branch_operator_addresses as module

    args = SimpleNamespace(
        provider=None,
        active_only=False,
        apply=True,
        apply_report=None,
        timeout=3,
        delay=0,
        min_score=82,
        max_requests=10,
        workers=2,
        output_dir=tmp_path,
    )
    query = OperatorQuery("Seoul", "Office", "organization_office")
    branch = {"provider": "MUNI_TEST", "name": "Aggregate branch"}
    monkeypatch.setattr(module, "parse_args", lambda: args)
    monkeypatch.setattr(
        module,
        "fetch_missing_branches",
        lambda *_args, **_kwargs: [branch],
    )
    monkeypatch.setattr(module, "load_provider_localities", lambda: {})
    monkeypatch.setattr(module, "branch_locality", lambda *_args: "Seoul")
    monkeypatch.setattr(module, "operator_query", lambda *_args: query)
    monkeypatch.setattr(
        module,
        "resolve_queries",
        lambda *_args, **_kwargs: ([], [], 1, 429),
    )
    monkeypatch.setattr(
        module,
        "write_reports",
        lambda *_args, **_kwargs: pytest.fail("fatal runs must not write reports"),
    )
    monkeypatch.setattr(
        module,
        "apply_resolutions",
        lambda *_args, **_kwargs: pytest.fail("partial results must not be applied"),
    )

    assert module.main() == 2


@pytest.mark.parametrize(
    ("address_source", "coordinate_source", "accepted"),
    [
        ("KAKAO_LOCAL_KEYWORD", "KAKAO_LOCAL_KEYWORD", True),
        ("GOOGLE_PLACES_TEXT_SEARCH", "GOOGLE_PLACES_TEXT_SEARCH", True),
        ("KAKAO_LOCAL_KEYWORD", "GOOGLE_PLACES_TEXT_SEARCH", False),
        ("GOOGLE_PLACES_TEXT_SEARCH", "KAKAO_LOCAL_KEYWORD", False),
    ],
)
def test_operator_report_accepts_current_kakao_and_historical_google_sources(
    tmp_path,
    address_source: str,
    coordinate_source: str,
    accepted: bool,
) -> None:
    branch = {
        "id": "00000000-0000-0000-0000-000000000001",
        "provider": "MUNI_TEST",
        "branch_code": "test",
        "name": "장소 별도 안내",
        "course_localities": ["경기도 광명시"],
    }
    report_path = tmp_path / "operator.csv"
    with report_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "branch_id",
                "scope",
                "locality",
                "target_name",
                "matched_name",
                "address",
                "lat",
                "lon",
                "confidence",
                "address_source",
                "coordinate_source",
                "query",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "branch_id": branch["id"],
                "scope": "operator_office",
                "locality": "경기도 광명시",
                "target_name": "광명시청",
                "matched_name": "광명시청",
                "address": "경기도 광명시 시청로 20",
                "lat": "37.4786",
                "lon": "126.8644",
                "confidence": "100",
                "address_source": address_source,
                "coordinate_source": coordinate_source,
                "query": "경기도 광명시 광명시청",
            }
        )

    resolutions, errors = load_report_resolutions(
        report_path,
        [branch],
        {"MUNI_TEST": "경기도 광명시"},
        82,
    )

    if accepted:
        assert errors == []
        assert len(resolutions) == 1
        assert resolutions[0].candidate.address_source == address_source
    else:
        assert resolutions == []
        assert any("coordinate source changed" in error for error in errors)
