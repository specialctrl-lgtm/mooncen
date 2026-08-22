from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.routing import APIRoute

from backend.ops import region_collection
from backend.main import app
from backend.routers import ops_v2
from backend.ops.region_collection import (
    RegionReference,
    ScopeAggregateRow,
    _all_scope_aggregate_rows,
    _configured_provider_registry,
    _configured_provider_scopes,
    _resolve_aggregate_municipality,
    _scope_target_rows,
    _scope_for_aggregate_row,
    _validate_production_municipality_index,
    build_region_collection_snapshot,
)
from tools.report_scope_region_coverage import (
    MunicipalityIndex,
    load_provider_municipalities,
)


def _aggregate(
    provider: str,
    branch_id: str,
    branch_name: str,
    *,
    active: int,
    total: int,
    observed_at: datetime,
) -> ScopeAggregateRow:
    return ScopeAggregateRow(
        provider=provider,
        branch_id=branch_id,
        branch_name=branch_name,
        branch_address="",
        facility_type="",
        facility_category="",
        venue_name="",
        venue_address="",
        active_data_count=active,
        total_data_count=total,
        latest_collected_at=observed_at if active else None,
        latest_historical_at=observed_at,
    )


def _reference() -> RegionReference:
    index = MunicipalityIndex.build(
        [
            {
                "code": "4111000000",
                "sido": "경기도",
                "sigungu": "수원시",
                "full_name": "경기도 수원시",
                "municipality_type": "city",
            },
            {
                "code": "4111100000",
                "sido": "경기도",
                "sigungu": "수원시 장안구",
                "full_name": "경기도 수원시 장안구",
                "municipality_type": "district",
            },
            {
                "code": "4182000000",
                "sido": "경기도",
                "sigungu": "가평군",
                "full_name": "경기도 가평군",
                "municipality_type": "county",
            },
        ]
    )
    provider_municipalities = {
        "PARENT_EMPTY": {"경기도 수원시"},
        "CHILD_EXPERIENCE": {"경기도 수원시 장안구"},
        "COUNTY_EDUCATION": {"경기도 가평군"},
    }
    return RegionReference(
        index=index,
        provider_municipalities=provider_municipalities,
        location_overrides={},
        configured_by_municipality={
            municipality: tuple(
                sorted(
                    provider
                    for provider, names in provider_municipalities.items()
                    if municipality in names
                )
            )
            for municipality in index.by_full_name
        },
    )


def _classification_row(**overrides):
    values = {
        "provider": "MUNI_TEST",
        "branch_provider": "MUNI_TEST",
        "branch_name": "평생학습관",
        "facility_type": "",
        "facility_category": "",
        "facility_source": None,
        "facility_service_group": "",
        "facility_collection_category": "",
        "education_institution": "",
        "target_name": "",
        "matched_name": "",
        "service_group": "공공강좌",
        "program_type": "교육",
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "ai_category": "",
        "source_group": "municipal_reservation",
        "category_raw": "교육",
        "service_group_policy": "locked",
        "locked_service_group": "공공강좌",
        "raw_domain_category": "교육·강좌",
        "raw_collection_category": "공공예약",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_fast_aggregate_classifier_trusts_locked_education_over_library_name() -> None:
    row = _classification_row(
        branch_name="중앙도서관",
        facility_type="library",
        facility_source="official_facility_registry",
    )

    assert _scope_for_aggregate_row(row) == "education"


def test_fast_aggregate_classifier_keeps_unlocked_institution_as_experience() -> None:
    row = _classification_row(
        branch_name="중앙도서관",
        facility_type="library",
        facility_source="official_facility_registry",
        service_group_policy="",
        locked_service_group="",
    )

    assert _scope_for_aggregate_row(row) == "experience"


def test_fast_aggregate_classifier_keeps_inferred_library_as_experience() -> None:
    row = _classification_row(
        branch_name="중앙도서관",
        facility_type="library",
        facility_source="official_facility_registry",
        service_group_policy="inferred",
        locked_service_group="공공강좌",
    )

    assert _scope_for_aggregate_row(row) == "experience"


def test_fast_aggregate_classifier_does_not_fallback_from_empty_locked_group() -> None:
    row = _classification_row(
        branch_name="중앙도서관",
        facility_type="library",
        facility_source="official_facility_registry",
        service_group_policy="locked",
        locked_service_group="",
    )

    # PostgreSQL COALESCE only falls back for NULL. A present empty JSON value
    # therefore does not lock the top-level public-course service group.
    assert _scope_for_aggregate_row(row) == "experience"


def test_fast_aggregate_classifier_uses_explicit_experience_lock() -> None:
    row = _classification_row(
        service_group="체험",
        program_type="체험",
        domain_category="체험·견학",
        service_group_policy="locked",
        locked_service_group="체험",
        raw_domain_category="체험·견학",
    )

    assert _scope_for_aggregate_row(row) == "experience"


def test_fast_aggregate_classifier_supports_legacy_administrative_branch() -> None:
    row = _classification_row(
        branch_name="반포2동 주민자치센터",
        service_group_policy="",
        locked_service_group="",
    )

    assert _scope_for_aggregate_row(row) == "education"


def test_fast_aggregate_classifier_checks_office_false_fragments_per_column() -> None:
    row = _classification_row(
        branch_name="수원시청",
        facility_type="시청각실",
        service_group_policy="",
        locked_service_group="",
    )

    # The authoritative SQL accepts the clean office name even when a
    # different branch column contains a false-positive fragment.
    assert _scope_for_aggregate_row(row) == "education"


def test_fast_aggregate_classifier_excludes_fixed_culture_provider() -> None:
    row = _classification_row(provider="EMART")

    assert _scope_for_aggregate_row(row) is None


def test_configured_registry_expands_aggregate_owners_and_inherits_file_defaults(
    tmp_path,
    monkeypatch,
) -> None:
    target_dir = tmp_path / "crawl_targets"
    target_dir.mkdir()
    (target_dir / "experience.yaml").write_text(
        """\
version: 1
service_group: 체험
targets:
- provider: EXPERIENCE_CHILD
  name: 생태 체험
  branch: 생태관
  crawler_status: ready
  url: https://example.test/experience
- provider: BLOCKED_CHILD
  name: 박물관 체험
  branch: 박물관
  crawler_status: blocked
  url: https://example.test/blocked
- provider: MUNICIPAL_CHILD
  name: 시청 교육
  branch: 시청
  service_group: 공공강좌
  service_group_policy: locked
  crawler_status: ready
  url: https://example.test/education
- provider: MUNICIPAL_CHILD
  name: 폐기된 박물관 상세
  branch: 박물관
  service_group: 체험
  crawler_status: deprecated
  url: https://example.test/deprecated-experience
""",
        encoding="utf-8",
    )
    production_path = tmp_path / "production.yaml"
    production_path.write_text(
        """\
version: 1
providers:
- EXPERIENCE_TARGETS
- MUNICIPAL_RESERVATION_TARGETS
""",
        encoding="utf-8",
    )
    operational_path = tmp_path / "operational.yaml"
    operational_path.write_text(
        """\
version: 1
entries:
- provider: MUNICIPAL_CHILD
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "backend.ops.region_collection.TARGET_CONFIG_DIR",
        target_dir,
    )
    monkeypatch.setattr(
        "backend.ops.region_collection.PRODUCTION_PROVIDER_FILE",
        production_path,
    )
    monkeypatch.setattr(
        "backend.ops.region_collection.OPERATIONAL_PROVIDER_FILE",
        operational_path,
    )

    rows = _scope_target_rows()
    providers = _configured_provider_registry(rows)
    scopes = _configured_provider_scopes(providers, rows)

    assert providers == frozenset({"EXPERIENCE_CHILD", "MUNICIPAL_CHILD"})
    assert scopes == {
        "EXPERIENCE_CHILD": frozenset({"experience"}),
        "MUNICIPAL_CHILD": frozenset({"education"}),
    }


def test_mixed_ledger_declares_both_ops_scopes_and_rejects_bad_values() -> None:
    provider = "MIXED_MUNICIPAL_LEDGER"
    target = {
        "provider": provider,
        "url": "https://public.example.go.kr/reserve/programs",
        "crawler_status": "ready",
        "municipality_code": "1154500000",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "ops_scopes": ["education", "experience"],
    }

    assert _configured_provider_scopes(frozenset({provider}), [target]) == {
        provider: frozenset({"education", "experience"})
    }

    with pytest.raises(ValueError, match="ops_scopes"):
        _configured_provider_scopes(
            frozenset({provider}),
            [{**target, "ops_scopes": ["education", "unknown"]}],
        )


def test_yongin_reservation_maps_only_exact_venue_districts_and_rolls_up_once() -> None:
    provider = "MUNI_RESVE_YONGIN_GO_KR_221336AC"
    expected = {
        "4146000000": "경기도 용인시",
        "4146100000": "경기도 용인시 처인구",
        "4146300000": "경기도 용인시 기흥구",
        "4146500000": "경기도 용인시 수지구",
    }
    target = next(
        row for row in _scope_target_rows() if row.get("provider") == provider
    )
    assert target["municipality_code"] == "4146000000"
    assert set(target["row_municipality_codes"]) == set(expected) - {"4146000000"}
    assert {
        row["code"] for row in target["covered_municipalities"]
    } == set(expected)

    reference = region_collection._region_reference()
    for scope in ("experience", "education"):
        assert all(
            provider in reference.configured_by_scope[scope][full_name]
            for full_name in expected.values()
        )

    observed_at = datetime(2026, 8, 5, 1, 0, tzinfo=timezone.utc)
    rows = []
    district_counts = {
        "4146100000": 3,
        "4146300000": 2,
        "4146500000": 1,
    }
    for code, count in district_counts.items():
        row = ScopeAggregateRow(
            provider=provider,
            branch_id="YONGIN_RESV_SHARED_PARK_BRANCH",
            branch_name="공원이용프로그램",
            branch_address="",
            facility_type="",
            facility_category="",
            venue_name=f"{expected[code]} 고정 체험장",
            venue_address=f"{expected[code]} 고정 체험장",
            active_data_count=count,
            total_data_count=count,
            latest_collected_at=observed_at,
            latest_historical_at=observed_at,
        )
        assert _resolve_aggregate_municipality(row, reference).code == code
        rows.append(row)

    unresolved = ScopeAggregateRow(
        provider=provider,
        branch_id="YONGIN_RESV_UNRESOLVED",
        branch_name="공원이용프로그램",
        branch_address="",
        facility_type="",
        facility_category="",
        venue_name="용인시 공원",
        venue_address="",
        active_data_count=1,
        total_data_count=1,
        latest_collected_at=observed_at,
        latest_historical_at=observed_at,
    )
    assert _resolve_aggregate_municipality(unresolved, reference) is None

    snapshot = build_region_collection_snapshot(
        object(),
        reference=reference,
        aggregate_rows={"experience": rows, "education": []},
        generated_at=observed_at,
    )
    by_code = {row["code"]: row for row in snapshot["municipalities"]}
    parent = by_code["4146000000"]
    assert parent["experience"]["active_data_count"] == 0
    assert parent["rollup"]["experience"]["active_data_count"] == 6
    assert parent["rollup"]["experience"]["active_provider_count"] == 1
    assert parent["rollup"]["experience"]["active_branch_count"] == 1
    assert snapshot["totals"]["experience"]["active_data_count"] == 6
    assert next(
        row for row in snapshot["sidos"] if row["sido"] == "경기도"
    )["experience"]["active_data_count"] == 6


def test_gwangmyeong_ilms_mixed_owner_configures_and_attributes_experience() -> None:
    provider = "MUNI_SUGANG_GM_GO_KR_F136DD19"
    full_name = "경기도 광명시"
    target = next(
        row for row in _scope_target_rows() if row.get("provider") == provider
    )
    assert target["ops_scopes"] == ["education", "experience"]

    reference = region_collection._region_reference()
    assert provider in reference.configured_by_scope["education"][full_name]
    assert provider in reference.configured_by_scope["experience"][full_name]

    classified = _classification_row(
        provider=provider,
        branch_provider=provider,
        branch_name="도시농업과",
        service_group="체험",
        program_type="체험",
        domain_category="체험·견학",
        category_raw="체험",
        service_group_policy="locked",
        locked_service_group="체험",
        raw_domain_category="체험·견학",
    )
    assert _scope_for_aggregate_row(classified) == "experience"

    observed_at = datetime(2026, 8, 5, 2, 0, tzinfo=timezone.utc)
    aggregate = ScopeAggregateRow(
        provider=provider,
        branch_id="GWANGMYEONG_FIXED_EXPERIENCE",
        branch_name="도시농업과",
        branch_address="",
        facility_type="",
        facility_category="",
        venue_name="광명동굴딸기스마트팜",
        venue_address="경기 광명시 가학로85번길 142",
        active_data_count=2,
        total_data_count=2,
        latest_collected_at=observed_at,
        latest_historical_at=observed_at,
    )
    assert _resolve_aggregate_municipality(aggregate, reference).code == "4121000000"

    snapshot = build_region_collection_snapshot(
        object(),
        reference=reference,
        aggregate_rows={"experience": [aggregate], "education": []},
        generated_at=observed_at,
    )
    municipality = next(
        row for row in snapshot["municipalities"] if row["code"] == "4121000000"
    )
    assert municipality["experience"]["status"] == "collected"
    assert municipality["experience"]["active_data_count"] == 2
    assert [
        row["provider"] for row in municipality["experience"]["providers"]
    ] == [provider]


def test_ops_mapping_does_not_expand_sido_hint_to_every_municipality(tmp_path) -> None:
    index = MunicipalityIndex.build(
        [
            {
                "code": "1111000000",
                "sido": "서울특별시",
                "sigungu": "종로구",
                "full_name": "서울특별시 종로구",
                "municipality_type": "district",
            },
            {
                "code": "1150000000",
                "sido": "서울특별시",
                "sigungu": "강서구",
                "full_name": "서울특별시 강서구",
                "municipality_type": "district",
            },
        ]
    )
    target_dir = tmp_path / "targets"
    target_dir.mkdir()
    (target_dir / "museum.yaml").write_text(
        """\
version: 1
targets:
- provider: NATIONAL_MUSEUM_TEST
  region: 서울특별시
- provider: METRO_RESERVATION_TEST
  region: 서울특별시
  row_municipality_codes:
  - '1150000000'
- provider: LOCAL_NAME_TEST
  name: 강서구 통합예약
  branch: 강서구 통합예약
""",
        encoding="utf-8",
    )

    strict = load_provider_municipalities(
        index,
        target_dir=target_dir,
        coverage_path=tmp_path / "missing-coverage.yaml",
        operational_path=tmp_path / "missing-operational.yaml",
        include_region_fallback=False,
    )
    legacy = load_provider_municipalities(
        index,
        target_dir=target_dir,
        coverage_path=tmp_path / "missing-coverage.yaml",
        operational_path=tmp_path / "missing-operational.yaml",
        include_region_fallback=True,
    )

    assert strict["NATIONAL_MUSEUM_TEST"] == set()
    assert strict["METRO_RESERVATION_TEST"] == {"서울특별시 강서구"}
    assert strict["LOCAL_NAME_TEST"] == {"서울특별시 강서구"}
    assert legacy["NATIONAL_MUSEUM_TEST"] == {
        "서울특별시 종로구",
        "서울특별시 강서구",
    }


def test_national_experience_targets_have_exact_production_municipalities() -> None:
    expected_codes = {
        "BUSAN_NATIONAL_SCIENCE_MUSEUM": {"2671000000"},
        "DAEGU_NATIONAL_SCIENCE_MUSEUM": {"2771000000"},
        "GWANGJU_NATIONAL_SCIENCE_MUSEUM": {"1230000000"},
        "HONAM_BIOLOGICAL_RESOURCES": {"1211000000"},
        "KOREA_NATIONAL_MARITIME_MUSEUM": {"2620000000"},
        "NATIONAL_AVIATION_MUSEUM": {"1150000000"},
        "NATIONAL_FOLK_MUSEUM": {"1111000000"},
        "NATIONAL_GUGAK_CENTER": {"1165000000"},
        "NATIONAL_HANGEUL_MUSEUM": {"1117000000"},
        "NATIONAL_METEOROLOGICAL_MUSEUM": {"1111000000"},
        "NATIONAL_MUSEUM_OF_MODERN_ART": {
            "1111000000",
            "1114000000",
            "4129000000",
            "4311400000",
        },
        "NATIONAL_PALACE_MUSEUM": {"1111000000"},
        "NATIONAL_SCIENCE_MUSEUM": {"3020000000"},
    }
    expected_runtime_branches = {
        ("BUSAN_NATIONAL_SCIENCE_MUSEUM", "국립부산과학관"): "2671000000",
        ("DAEGU_NATIONAL_SCIENCE_MUSEUM", "국립대구과학관"): "2771000000",
        ("GWANGJU_NATIONAL_SCIENCE_MUSEUM", "국립광주과학관"): "1230000000",
        ("HONAM_BIOLOGICAL_RESOURCES", "국립호남권생물자원관"): "1211000000",
        ("KOREA_NATIONAL_MARITIME_MUSEUM", "국립해양박물관"): "2620000000",
        ("NATIONAL_AVIATION_MUSEUM", "국립항공박물관"): "1150000000",
        ("NATIONAL_FOLK_MUSEUM", "국립민속박물관"): "1111000000",
        ("NATIONAL_GUGAK_CENTER", "국립국악원"): "1165000000",
        ("NATIONAL_HANGEUL_MUSEUM", "국립한글박물관"): "1117000000",
        ("NATIONAL_METEOROLOGICAL_MUSEUM", "국립기상박물관"): "1111000000",
        ("NATIONAL_MUSEUM_OF_MODERN_ART", "서울"): "1111000000",
        ("NATIONAL_MUSEUM_OF_MODERN_ART", "덕수궁"): "1114000000",
        ("NATIONAL_MUSEUM_OF_MODERN_ART", "과천"): "4129000000",
        ("NATIONAL_MUSEUM_OF_MODERN_ART", "어린이미술관"): "4129000000",
        ("NATIONAL_MUSEUM_OF_MODERN_ART", "과천 어린이미술관"): "4129000000",
        ("NATIONAL_MUSEUM_OF_MODERN_ART", "청주"): "4311400000",
        ("NATIONAL_PALACE_MUSEUM", "국립고궁박물관"): "1111000000",
        ("NATIONAL_SCIENCE_MUSEUM", "국립중앙과학관"): "3020000000",
    }

    target_rows = _scope_target_rows()
    providers = _configured_provider_registry(target_rows)
    selected_targets = region_collection._selected_provider_targets(
        providers,
        target_rows,
    )
    provider_scopes = _configured_provider_scopes(providers, target_rows)
    reference = region_collection._region_reference()
    names_by_code = {
        municipality.code: municipality.full_name
        for municipality in reference.index.municipalities
    }
    unmapped_target_providers = {
        str(target["provider"])
        for target in reference.unmapped_configured_targets_by_scope["experience"]
    }

    for provider, codes in expected_codes.items():
        assert provider_scopes[provider] == frozenset({"experience"})
        assert len(selected_targets[provider]) == 1
        target = selected_targets[provider][0]
        if len(codes) == 1:
            assert target.get("municipality_code") == next(iter(codes))
        else:
            assert set(target.get("row_municipality_codes") or ()) == codes

        expected_names = {names_by_code[code] for code in codes}
        configured_names = {
            full_name
            for full_name, configured_providers in reference.configured_by_scope[
                "experience"
            ].items()
            if provider in configured_providers
        }
        assert configured_names == expected_names
        assert all(
            provider
            not in reference.configured_by_scope["education"].get(full_name, ())
            for full_name in reference.index.by_full_name
        )
        assert provider not in reference.unmapped_configured_providers
        assert provider not in reference.unmapped_configured_by_scope["experience"]
        assert provider not in reference.unmapped_configured_by_scope["education"]
        assert provider not in unmapped_target_providers

    for (provider, branch), code in expected_runtime_branches.items():
        override = reference.location_overrides[
            (provider, region_collection.compact_text(branch))
        ]
        assert override.code == code


def test_partial_unmapped_configured_target_is_reported_without_url(
    monkeypatch,
) -> None:
    index = MunicipalityIndex.build(
        [
            {
                "code": "1150000000",
                "sido": "서울특별시",
                "sigungu": "강서구",
                "full_name": "서울특별시 강서구",
                "municipality_type": "district",
            }
        ]
    )
    target_rows = [
        {
            "provider": "PARTIAL_PROVIDER",
            "name": "매핑 완료 대상",
            "municipality_full_name": "서울특별시 강서구",
            "service_group": "체험",
            "service_group_policy": "locked",
            "crawler_status": "ready",
            "url": "https://public.example/mapped",
        },
        {
            "provider": "PARTIAL_PROVIDER",
            "name": "부분 미배정 대상",
            "region": "서울특별시",
            "service_group": "체험",
            "service_group_policy": "locked",
            "crawler_status": "ready",
            "url": "https://sensitive.example/private-target",
        },
    ]
    monkeypatch.setattr(
        region_collection,
        "_reference_config_signature",
        lambda: (("test-config", 1, 1),),
    )
    monkeypatch.setattr(region_collection, "load_municipality_index", lambda *_: index)
    monkeypatch.setattr(
        region_collection,
        "_validate_production_municipality_index",
        lambda *_: None,
    )
    monkeypatch.setattr(region_collection, "_scope_target_rows", lambda: target_rows)
    monkeypatch.setattr(
        region_collection,
        "_configured_provider_registry",
        lambda _rows: frozenset({"PARTIAL_PROVIDER"}),
    )
    monkeypatch.setattr(
        region_collection,
        "load_provider_municipalities",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(region_collection, "load_location_overrides", lambda *_: {})
    monkeypatch.setattr(region_collection, "_REFERENCE_SIGNATURE", None)
    monkeypatch.setattr(region_collection, "_REFERENCE_VALUE", None)

    reference = region_collection._region_reference()

    assert reference.configured_by_scope["experience"] == {
        "서울특별시 강서구": ("PARTIAL_PROVIDER",)
    }
    assert reference.unmapped_configured_providers == ()
    targets = reference.unmapped_configured_targets_by_scope["experience"]
    assert len(targets) == 1
    assert targets[0]["provider"] == "PARTIAL_PROVIDER"
    assert str(targets[0]["target_id"]).startswith("derived:")
    assert targets[0]["display_name"] == "부분 미배정 대상"
    assert targets[0]["region_hint"] == "서울특별시"
    assert targets[0]["reason"] == "region_hint_requires_explicit_municipality"
    assert "url" not in targets[0]
    assert "sensitive.example" not in repr(targets[0])

    result = build_region_collection_snapshot(
        object(),
        reference=reference,
        aggregate_rows={"experience": [], "education": []},
    )
    experience = result["totals"]["experience"]
    assert experience["unmapped_configured_target_count"] == 1
    assert experience["unmapped_configured_targets"] == [dict(targets[0])]


def test_national_museum_multi_target_baseline_does_not_hide_three_gaps(
    monkeypatch,
) -> None:
    provider = "NATIONAL_MUSEUM_OF_KOREA"
    mapped_names = [f"테스트도 제{number}구" for number in range(1, 12)]
    index = MunicipalityIndex.build(
        [
            {
                "code": f"{number:010d}",
                "sido": "테스트도",
                "sigungu": f"제{number}구",
                "full_name": municipality_name,
                "municipality_type": "district",
            }
            for number, municipality_name in enumerate(mapped_names, start=1)
        ]
    )
    common = {
        "provider": provider,
        "service_group": "체험",
        "service_group_policy": "locked",
        "crawler_status": "ready",
    }
    target_rows = [
        {
            **common,
            "name": f"국립테스트{number}박물관",
            "municipality_full_name": municipality_name,
            "url": f"https://public.example/museum/{number}",
        }
        for number, municipality_name in enumerate(mapped_names, start=1)
    ]
    target_rows.extend(
        [
            {
                **common,
                "name": "국립중앙박물관",
                "region": "서울특별시",
                "url": "https://sensitive.example/museum/central",
            },
            {
                **common,
                "name": "국립광주박물관",
                "region": "광주광역시",
                "url": "https://sensitive.example/museum/gwangju",
            },
            {
                **common,
                "name": "국립대구박물관",
                "region": "대구광역시",
                "url": "https://sensitive.example/museum/daegu",
            },
        ]
    )
    assert len(target_rows) == 14

    monkeypatch.setattr(
        region_collection,
        "_reference_config_signature",
        lambda: (("national-museum-test", 1, 1),),
    )
    monkeypatch.setattr(region_collection, "load_municipality_index", lambda *_: index)
    monkeypatch.setattr(
        region_collection,
        "_validate_production_municipality_index",
        lambda *_: None,
    )
    monkeypatch.setattr(region_collection, "_scope_target_rows", lambda: target_rows)
    monkeypatch.setattr(
        region_collection,
        "_configured_provider_registry",
        lambda _rows: frozenset({provider}),
    )
    monkeypatch.setattr(
        region_collection,
        "load_provider_municipalities",
        lambda *_args, **_kwargs: {provider: set(mapped_names)},
    )
    monkeypatch.setattr(region_collection, "load_location_overrides", lambda *_: {})
    monkeypatch.setattr(region_collection, "_REFERENCE_SIGNATURE", None)
    monkeypatch.setattr(region_collection, "_REFERENCE_VALUE", None)

    reference = region_collection._region_reference()

    assert set(reference.configured_by_scope["experience"]) == set(mapped_names)
    assert reference.unmapped_configured_providers == ()
    targets = reference.unmapped_configured_targets_by_scope["experience"]
    assert len(targets) == 3
    assert {target["display_name"] for target in targets} == {
        "국립중앙박물관",
        "국립광주박물관",
        "국립대구박물관",
    }
    assert {target["region_hint"] for target in targets} == {
        "서울특별시",
        "광주광역시",
        "대구광역시",
    }
    assert {
        target["reason"] for target in targets
    } == {"region_hint_requires_explicit_municipality"}
    assert all("url" not in target for target in targets)
    assert "sensitive.example" not in repr(targets)


def test_scope_aggregate_keeps_branchless_locked_education_course() -> None:
    observed_at = datetime(2026, 8, 4, 12, 30, tzinfo=timezone.utc)
    values = vars(
        _classification_row(
            branch_provider=None,
            branch_name=None,
            facility_type=None,
            facility_category=None,
            education_institution=None,
            target_name=None,
            matched_name=None,
        )
    ).copy()
    values.update(
        {
            "branch_id": None,
            "branch_address": None,
            "region_sido": None,
            "region_sigungu": None,
            "venue_name": None,
            "venue_address": None,
            "total_data_count": 1,
            "active_data_count": 1,
            "latest_collected_at": observed_at,
            "latest_historical_at": observed_at,
        }
    )
    db_row = SimpleNamespace(**values)

    class FakeAggregateQuery:
        def __init__(self, rows) -> None:
            self.rows = rows
            self.outerjoin_calls = 0
            self.join_calls = 0
            self.filter_calls = 0

        def outerjoin(self, *_args, **_kwargs):
            self.outerjoin_calls += 1
            return self

        def join(self, *_args, **_kwargs):
            self.join_calls += 1
            return self

        def filter(self, *_args, **_kwargs):
            self.filter_calls += 1
            return self

        def group_by(self, *_args, **_kwargs):
            return self

        def yield_per(self, *_args, **_kwargs):
            return self

        def __iter__(self):
            return iter(self.rows)

    class FakeSession:
        def __init__(self) -> None:
            self.aggregate_queries = [
                FakeAggregateQuery(()),
                FakeAggregateQuery((db_row,)),
            ]
            self.issued_queries = []

        def query(self, *_args, **_kwargs):
            query = self.aggregate_queries.pop(0)
            self.issued_queries.append(query)
            return query

    session = FakeSession()
    rows_by_scope = _all_scope_aggregate_rows(session)  # type: ignore[arg-type]

    assert session.aggregate_queries == []
    assert len(session.issued_queries) == 2
    assert all(query.outerjoin_calls == 1 for query in session.issued_queries)
    assert all(query.join_calls == 0 for query in session.issued_queries)
    assert all(query.filter_calls == 1 for query in session.issued_queries)
    assert rows_by_scope["experience"] == []
    assert len(rows_by_scope["education"]) == 1
    assert rows_by_scope["education"][0].branch_id == ""


def test_region_snapshot_keeps_zero_regions_and_rolls_children_into_parent_city() -> None:
    observed_at = datetime(2026, 8, 4, 12, 30, tzinfo=timezone.utc)
    result = build_region_collection_snapshot(
        object(),  # aggregate_rows makes the database session unnecessary in this unit test.
        reference=_reference(),
        aggregate_rows={
            "experience": [
                _aggregate(
                    "CHILD_EXPERIENCE",
                    "branch-child",
                    "수원시 장안구",
                    active=5,
                    total=7,
                    observed_at=observed_at,
                )
            ],
            "education": [
                _aggregate(
                    "COUNTY_EDUCATION",
                    "branch-county",
                    "가평군",
                    active=0,
                    total=2,
                    observed_at=observed_at,
                )
            ],
        },
        generated_at=observed_at,
    )

    assert result["totals"]["municipality_count"] == 3
    by_code = {row["code"]: row for row in result["municipalities"]}
    parent = by_code["4111000000"]
    child = by_code["4111100000"]
    county = by_code["4182000000"]

    assert parent["experience"]["status"] == "connected_empty"
    assert parent["experience"]["active_data_count"] == 0
    assert parent["child_municipality_count"] == 1
    assert parent["rollup"]["experience"]["active_data_count"] == 5
    assert parent["rollup"]["experience"]["active_provider_count"] == 1
    assert child["experience"]["status"] == "collected"
    assert child["experience"]["active_data_count"] == 5
    assert county["education"]["status"] == "historical"
    assert county["education"]["total_data_count"] == 2

    sido = result["sidos"][0]
    assert sido["experience"]["active_provider_count"] == 1
    assert sido["experience"]["active_data_count"] == 5
    assert sido["experience"]["collected_municipality_count"] == 2
    assert result["totals"]["experience"]["active_data_count"] == 5
    assert result["totals"]["experience"]["collected_municipality_count"] == 2
    assert result["totals"]["education"]["active_data_count"] == 0


def test_region_snapshot_reports_unmapped_database_provider_names() -> None:
    observed_at = datetime(2026, 8, 4, 12, 30, tzinfo=timezone.utc)
    result = build_region_collection_snapshot(
        object(),
        reference=_reference(),
        aggregate_rows={
            "experience": [
                _aggregate(
                    "UNMAPPED_HISTORICAL",
                    "branch-historical",
                    "https://sensitive.example/private-target",
                    active=0,
                    total=2,
                    observed_at=observed_at,
                ),
                _aggregate(
                    "UNMAPPED_ACTIVE",
                    "branch-active",
                    "지역 정보 없음",
                    active=3,
                    total=4,
                    observed_at=observed_at,
                ),
                _aggregate(
                    "UNMAPPED_ACTIVE",
                    "branch-active-two",
                    "지역 정보 없음",
                    active=1,
                    total=1,
                    observed_at=observed_at,
                ),
            ],
            "education": [],
        },
        generated_at=observed_at,
    )

    experience = result["totals"]["experience"]
    assert experience["unmapped_active_data_count"] == 4
    assert experience["unmapped_total_data_count"] == 7
    assert experience["unmapped_active_provider_count"] == 1
    assert experience["unmapped_provider_count"] == 2
    assert experience["unmapped_active_provider_names"] == ["UNMAPPED_ACTIVE"]
    assert experience["unmapped_provider_names"] == [
        "UNMAPPED_ACTIVE",
        "UNMAPPED_HISTORICAL",
    ]
    assert "sensitive.example" not in repr(experience)
    assert "branch-historical" not in repr(experience)


def test_production_municipality_index_contract() -> None:
    index = region_collection.load_municipality_index(
        region_collection.MUNICIPALITY_SOURCE_FILE
    )

    _validate_production_municipality_index(index)

    assert len(index.municipalities) == 269
    assert len({row.code for row in index.municipalities}) == 269
    assert len({row.full_name for row in index.municipalities}) == 269
    assert all(
        region_collection.MUNICIPALITY_CODE_PATTERN.fullmatch(row.code)
        for row in index.municipalities
    )
    assert all(
        row.sido and row.sigungu and row.full_name
        for row in index.municipalities
    )
    assert {row.municipality_type for row in index.municipalities} <= {
        "city",
        "county",
        "district",
    }


def test_production_municipality_index_rejects_invalid_entries() -> None:
    source = region_collection.load_municipality_index(
        region_collection.MUNICIPALITY_SOURCE_FILE
    )
    rows = [
        {
            "code": row.code,
            "sido": row.sido,
            "sigungu": row.sigungu,
            "full_name": row.full_name,
            "municipality_type": row.municipality_type,
        }
        for row in source.municipalities
    ]

    invalid_type_rows = [dict(row) for row in rows]
    invalid_type_rows[0]["municipality_type"] = "province"
    with pytest.raises(ValueError, match="invalid municipality type"):
        _validate_production_municipality_index(
            MunicipalityIndex.build(invalid_type_rows)
        )

    duplicate_code_rows = [dict(row) for row in rows]
    duplicate_code_rows[1]["code"] = duplicate_code_rows[0]["code"]
    with pytest.raises(ValueError, match="duplicate code"):
        _validate_production_municipality_index(
            MunicipalityIndex.build(duplicate_code_rows)
        )

    invalid_code_rows = [dict(row) for row in rows]
    invalid_code_rows[0]["code"] = "not-a-code"
    with pytest.raises(ValueError, match="invalid code"):
        _validate_production_municipality_index(
            MunicipalityIndex.build(invalid_code_rows)
        )

    duplicate_name_rows = [dict(row) for row in rows]
    for field_name in ("sido", "sigungu", "full_name"):
        duplicate_name_rows[1][field_name] = duplicate_name_rows[0][field_name]
    with pytest.raises(ValueError, match="duplicate full name"):
        _validate_production_municipality_index(
            MunicipalityIndex.build(duplicate_name_rows)
        )

    invalid_field_rows = [dict(row) for row in rows]
    invalid_field_rows[0]["sigungu"] = ""
    with pytest.raises(ValueError, match="missing field"):
        _validate_production_municipality_index(
            MunicipalityIndex.build(invalid_field_rows)
        )

    invalid_full_name_rows = [dict(row) for row in rows]
    invalid_full_name_rows[0]["full_name"] = "불일치 행정구역명"
    with pytest.raises(ValueError, match="invalid full name"):
        _validate_production_municipality_index(
            MunicipalityIndex.build(invalid_full_name_rows)
        )

    with pytest.raises(ValueError, match="expected 269 entries"):
        _validate_production_municipality_index(
            MunicipalityIndex.build(rows[:-1])
        )


def test_region_snapshot_reports_configured_providers_per_scope() -> None:
    observed_at = datetime(2026, 8, 4, 12, 30, tzinfo=timezone.utc)
    base = _reference()
    municipality_name = "경기도 수원시"
    reference = RegionReference(
        index=base.index,
        provider_municipalities={
            "EXPERIENCE_ONLY": {municipality_name},
            "EDUCATION_ONE": {municipality_name},
            "EDUCATION_TWO": {municipality_name},
        },
        location_overrides={},
        configured_by_municipality={
            municipality_name: (
                "EDUCATION_ONE",
                "EDUCATION_TWO",
                "EXPERIENCE_ONLY",
            )
        },
        configured_by_scope={
            "experience": {municipality_name: ("EXPERIENCE_ONLY",)},
            "education": {
                municipality_name: ("EDUCATION_ONE", "EDUCATION_TWO")
            },
        },
        unmapped_configured_providers=("UNMAPPED_EXPERIENCE",),
        unmapped_configured_by_scope={
            "experience": ("UNMAPPED_EXPERIENCE",),
            "education": (),
        },
    )

    result = build_region_collection_snapshot(
        object(),
        reference=reference,
        aggregate_rows={"experience": [], "education": []},
        generated_at=observed_at,
    )

    municipality = next(
        row for row in result["municipalities"] if row["full_name"] == municipality_name
    )
    assert municipality["configured_provider_count"] == 3
    assert municipality["experience"]["configured_provider_count"] == 1
    assert municipality["experience"]["configured_providers"] == [
        "EXPERIENCE_ONLY"
    ]
    assert municipality["education"]["configured_provider_count"] == 2
    assert municipality["education"]["configured_providers"] == [
        "EDUCATION_ONE",
        "EDUCATION_TWO",
    ]
    assert result["totals"]["experience"]["configured_provider_count"] == 1
    assert result["totals"]["education"]["configured_provider_count"] == 2
    assert result["totals"]["experience"]["unmapped_configured_providers"] == [
        "UNMAPPED_EXPERIENCE"
    ]
    assert result["totals"]["experience"]["unmapped_configured_provider_count"] == 1


def test_region_snapshot_counts_one_provider_once_in_sido_rollup() -> None:
    observed_at = datetime(2026, 8, 4, 12, 30, tzinfo=timezone.utc)
    reference = _reference()
    reference.provider_municipalities["SHARED"] = {
        "경기도 수원시 장안구",
        "경기도 가평군",
    }
    result = build_region_collection_snapshot(
        object(),
        reference=reference,
        aggregate_rows={
            "experience": [
                _aggregate(
                    "SHARED",
                    "branch-one",
                    "수원시 장안구",
                    active=3,
                    total=3,
                    observed_at=observed_at,
                ),
                _aggregate(
                    "SHARED",
                    "branch-two",
                    "가평군",
                    active=4,
                    total=4,
                    observed_at=observed_at,
                ),
            ],
            "education": [],
        },
        generated_at=observed_at,
    )

    assert result["sidos"][0]["experience"]["active_provider_count"] == 1
    assert result["sidos"][0]["experience"]["active_data_count"] == 7


def test_region_snapshot_counts_shared_branch_once_across_providers() -> None:
    observed_at = datetime(2026, 8, 4, 12, 30, tzinfo=timezone.utc)
    reference = _reference()
    reference.provider_municipalities.update(
        {
            "PROVIDER_ONE": {"경기도 수원시 장안구"},
            "PROVIDER_TWO": {"경기도 수원시 장안구"},
        }
    )
    result = build_region_collection_snapshot(
        object(),
        reference=reference,
        aggregate_rows={
            "experience": [
                _aggregate(
                    "PROVIDER_ONE",
                    "shared-branch",
                    "수원시 장안구",
                    active=2,
                    total=2,
                    observed_at=observed_at,
                ),
                _aggregate(
                    "PROVIDER_TWO",
                    "shared-branch",
                    "수원시 장안구",
                    active=3,
                    total=4,
                    observed_at=observed_at,
                ),
            ],
            "education": [],
        },
        generated_at=observed_at,
    )

    child = next(row for row in result["municipalities"] if row["code"] == "4111100000")
    assert child["experience"]["active_data_count"] == 5
    assert child["experience"]["total_data_count"] == 6
    assert child["experience"]["active_branch_count"] == 1
    assert child["experience"]["total_branch_count"] == 1
    assert result["sidos"][0]["experience"]["active_branch_count"] == 1
    assert result["totals"]["experience"]["total_branch_count"] == 1
    assert all(
        provider["active_branch_count"] == 1
        for provider in child["experience"]["providers"]
    )
    assert "_active_branch_ids" not in repr(result)


def test_explicit_branch_region_wins_over_single_provider_fallback() -> None:
    observed_at = datetime(2026, 8, 4, 12, 30, tzinfo=timezone.utc)
    result = build_region_collection_snapshot(
        object(),
        reference=_reference(),
        aggregate_rows={
            "experience": [
                ScopeAggregateRow(
                    provider="PARENT_EMPTY",
                    branch_id="branch-explicit",
                    branch_name="지역명이 없는 기관",
                    branch_address="",
                    facility_type="",
                    facility_category="",
                    venue_name="",
                    venue_address="",
                    active_data_count=2,
                    total_data_count=2,
                    latest_collected_at=observed_at,
                    latest_historical_at=observed_at,
                    region_sido="경기도",
                    region_sigungu="가평군",
                )
            ],
            "education": [],
        },
        generated_at=observed_at,
    )

    by_code = {row["code"]: row for row in result["municipalities"]}
    assert by_code["4111000000"]["experience"]["active_data_count"] == 0
    assert by_code["4182000000"]["experience"]["active_data_count"] == 2


def test_location_required_providers_never_override_exact_row_evidence() -> None:
    municipality_rows = [
        ("경기도", "안산시", "city"),
        ("경기도", "안산시 상록구", "district"),
        ("경기도", "안산시 단원구", "district"),
        ("충청남도", "천안시", "city"),
        ("충청남도", "천안시 서북구", "district"),
        ("충청남도", "천안시 동남구", "district"),
        ("충청남도", "공주시", "city"),
        ("충청남도", "당진시", "city"),
        ("충청남도", "아산시", "city"),
        ("충청남도", "보령시", "city"),
        ("충청남도", "예산군", "county"),
        ("충청남도", "서산시", "city"),
        ("충청남도", "홍성군", "county"),
        ("충청남도", "서천군", "county"),
        ("충청남도", "논산시", "city"),
        ("충청남도", "태안군", "county"),
        ("충청남도", "부여군", "county"),
    ]
    index = MunicipalityIndex.build(
        [
            {
                "code": f"{offset:010d}",
                "sido": sido,
                "sigungu": sigungu,
                "full_name": f"{sido} {sigungu}",
                "municipality_type": municipality_type,
            }
            for offset, (sido, sigungu, municipality_type) in enumerate(
                municipality_rows,
                start=1,
            )
        ]
    )
    reference = RegionReference(
        index=index,
        provider_municipalities={
            "MUNI_RESERVE_ANSAN_GO_KR_8236CAF0": {
                "경기도 안산시 상록구"
            },
            "MUNI_CNC_CACF_OR_KR_7A12B48E": {
                "충청남도 천안시 서북구"
            },
            "NORMAL_SINGLE_PROVIDER": {"경기도 안산시 상록구"},
        },
        location_overrides={},
        configured_by_municipality={},
    )

    def resolved_name(provider: str, location: str) -> str | None:
        municipality = _resolve_aggregate_municipality(
            ScopeAggregateRow(
                provider=provider,
                branch_id=f"branch:{provider}:{location}",
                branch_name="지역 근거 없는 시설",
                branch_address=location,
                facility_type="",
                facility_category="",
                venue_name="",
                venue_address="",
                active_data_count=1,
                total_data_count=1,
                latest_collected_at=None,
                latest_historical_at=None,
            ),
            reference,
        )
        return municipality.full_name if municipality is not None else None

    ansan_distribution = {
        resolved_name(
            "MUNI_RESERVE_ANSAN_GO_KR_8236CAF0",
            "경기도 안산시 상록구 본오로 1",
        ): 7,
        resolved_name(
            "MUNI_RESERVE_ANSAN_GO_KR_8236CAF0",
            "경기도 안산시 단원구 안산천남로 14",
        ): 43,
        resolved_name(
            "MUNI_RESERVE_ANSAN_GO_KR_8236CAF0",
            "경기도 안산시 중앙대로 1",
        ): 56,
    }
    assert ansan_distribution == {
        "경기도 안산시 상록구": 7,
        "경기도 안산시 단원구": 43,
        "경기도 안산시": 56,
    }

    cnc_evidence = [
        ("충청남도 천안시 서북구 성환읍 1", 1),
        ("충청남도 천안시 동남구 성남면 종합휴양지로 185", 15),
        ("충청남도 공주시 고마나루길 5", 6),
        ("충청남도 천안시 문화로 1", 4),
        ("충청남도 당진시 시청1로 1", 4),
        ("충청남도 아산시 시민로 1", 2),
        ("충청남도 보령시 성주산로 1", 2),
        ("충청남도 예산군 예산읍 1", 2),
        ("충청남도 서산시 관아문길 1", 2),
        ("충청남도 홍성군 홍성읍 내포로 164", 1),
        ("충청남도 서천군 서천읍 1", 1),
        ("충청남도 논산시 시민로 1", 1),
        ("충청남도 태안군 태안읍 1", 1),
        ("충청남도 부여군 부여읍 1", 1),
        ("외암민속마을 한옥갤러리", 8),
    ]
    cnc_distribution: dict[str | None, int] = {}
    for location, count in cnc_evidence:
        municipality_name = resolved_name(
            "MUNI_CNC_CACF_OR_KR_7A12B48E",
            location,
        )
        cnc_distribution[municipality_name] = (
            cnc_distribution.get(municipality_name, 0) + count
        )

    assert cnc_distribution["충청남도 천안시 서북구"] == 1
    assert cnc_distribution[None] == 8
    assert sum(
        count
        for municipality_name, count in cnc_distribution.items()
        if municipality_name not in {"충청남도 천안시 서북구", None}
    ) == 42
    assert resolved_name("NORMAL_SINGLE_PROVIDER", "지역 정보 없음") == (
        "경기도 안산시 상록구"
    )


def test_region_coverage_route_is_viewer_protected() -> None:
    routes: list[tuple[str, APIRoute]] = []
    for included in app.routes:
        if isinstance(included, APIRoute):
            routes.append(("", included))
        elif hasattr(included, "original_router") and hasattr(included, "include_context"):
            routes.extend(
                (included.include_context.prefix, route)
                for route in included.original_router.routes
                if isinstance(route, APIRoute)
            )
    _prefix, route = next(
        (prefix, route)
        for prefix, route in routes
        if prefix + route.path == "/api/ops/crawlers/region-coverage"
    )

    dependency_names = {
        getattr(dependency.call, "__name__", "")
        for dependency in route.dependant.dependencies
    }
    assert "require_ops_viewer" in dependency_names


def test_region_coverage_route_separates_cloud_topology_from_observed_db_host(
    monkeypatch,
) -> None:
    refresh_values: list[bool] = []

    def snapshot(_db, *, force_refresh: bool = False):
        refresh_values.append(force_refresh)
        return {"available": True}

    monkeypatch.setattr(ops_v2, "get_region_collection_snapshot", snapshot)
    monkeypatch.setattr(ops_v2, "current_environment", lambda: "production")
    monkeypatch.setattr(
        ops_v2,
        "load_production_topology",
        lambda: SimpleNamespace(
            primary_for=lambda _service: SimpleNamespace(
                node="cloud",
                service_host="cloud",
            )
        ),
    )
    database_url = SimpleNamespace(host="localhost", database="mooncen")
    db = SimpleNamespace(get_bind=lambda: SimpleNamespace(url=database_url))

    result = ops_v2.crawler_region_coverage(refresh=True, db=db)

    assert refresh_values == [True]
    assert result["data_source"] == {
        "environment": "production",
        "is_production": True,
        "production_node": "cloud",
        "production_service_host": "cloud",
        "database_host": "localhost",
        "database_name": "mooncen",
    }


def test_snapshot_cache_rebuilds_only_when_source_revision_changes(monkeypatch) -> None:
    revision = {"value": 1}
    builds: list[int] = []

    monkeypatch.setattr(
        region_collection,
        "_database_revision",
        lambda _session: (revision["value"],),
    )
    monkeypatch.setattr(
        region_collection,
        "_reference_config_signature",
        lambda: (("config", 1, 1),),
    )

    def fake_build(_session):
        builds.append(revision["value"])
        return {"revision": revision["value"]}

    monkeypatch.setattr(
        region_collection,
        "build_region_collection_snapshot",
        fake_build,
    )
    region_collection.clear_region_collection_cache()
    try:
        assert region_collection.get_region_collection_snapshot(object()) == {
            "revision": 1
        }
        region_collection._CACHE_DEADLINE = 0.0
        assert region_collection.get_region_collection_snapshot(object()) == {
            "revision": 1
        }
        revision["value"] = 2
        region_collection._CACHE_DEADLINE = 0.0
        assert region_collection.get_region_collection_snapshot(object()) == {
            "revision": 2
        }
        assert region_collection.get_region_collection_snapshot(
            object(), force_refresh=True
        ) == {"revision": 2}
        assert builds == [1, 2, 2]
    finally:
        region_collection.clear_region_collection_cache()
