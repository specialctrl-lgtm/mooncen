from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import run_crawlers as runner
from Crawler import Crawler_GeneratedYamlTargets as generated_targets
from Crawler import Crawler_MunicipalIntegratedReservation as municipal
from Crawler.Crawler_MunicipalYaml import CrawlTarget, MunicipalDbWriter


URL = "https://reserve.example.go.kr/education/list?page=1"
NORMALIZED_URL = "https://reserve.example.go.kr/education/list"


def operational_entry(provider: str = "MUNI_TEST_ALLOWED") -> dict:
    return {
        "provider": provider,
        "normalized_url": NORMALIZED_URL,
        "target_url": URL,
        "action": "schedule_existing",
        "validation_outcome": "collected",
        "validated_at": "2026-07-18T00:30:00+09:00",
        "parser": "test_parser",
        "row_count": 1,
        "no_current_data": False,
        "municipalities": [
            {
                "code": "1111000000",
                "sido": "서울특별시",
                "sigungu": "종로구",
                "full_name": "서울특별시 종로구",
            }
        ],
    }


def working_target(provider: str = "MUNI_TEST_ALLOWED", url: str = URL) -> dict:
    return {
        "provider": provider,
        "url": url,
        "source_group": "lifelong_learning",
        "collection_category": "평생학습",
        "domain_category": "평생학습",
        "service_group": "체험",
        "service_group_policy": "inferred",
        "crawler_status": "ready",
    }


def bind_operational_target(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    municipalities: list[dict],
    *,
    url: str = URL,
) -> None:
    monkeypatch.setattr(
        generated_targets,
        "MUNICIPAL_OPERATIONAL_PROVIDER_NAMES",
        {provider},
    )
    monkeypatch.setattr(
        generated_targets,
        "MUNICIPAL_OPERATIONAL_TARGET_MUNICIPALITIES",
        {
            (provider, generated_targets.normalized_duplicate_url(url)): tuple(
                (row["code"], row["full_name"]) for row in municipalities
            )
        },
    )


def test_operational_manifest_requires_exact_canonical_allowlist(tmp_path: Path) -> None:
    path = tmp_path / "operational.yaml"
    path.write_text(
        yaml.safe_dump({"version": 1, "entries": [operational_entry()]}, sort_keys=False),
        encoding="utf-8",
    )

    entries = municipal.load_operational_entries(path)
    contracts = generated_targets.municipal_operational_target_municipalities(path)
    selected = municipal.select_operational_targets(
        [
            working_target(),
            working_target(provider="MUNI_WRONG_PROVIDER"),
            working_target(url="https://reserve.example.go.kr/education/other"),
        ],
        entries,
        scheduled_providers=set(),
    )

    assert len(selected) == 1
    row = selected[0]
    assert row["provider"] == "MUNI_TEST_ALLOWED"
    assert row["source_group"] == "municipal_reservation"
    assert row["collection_category"] == "공공예약"
    assert row["domain_category"] == "교육·강좌"
    assert row["service_group"] == "공공강좌"
    assert row["service_group_policy"] == "locked"
    assert row["municipality_code"] == "1111000000"
    assert row["municipality_full_name"] == "서울특별시 종로구"

    municipality = operational_entry()["municipalities"][0]
    assert contracts[("MUNI_TEST_ALLOWED", NORMALIZED_URL)] == (
        (municipality["code"], municipality["full_name"]),
    )

    invalid = operational_entry()
    invalid["normalized_url"] = URL
    path.write_text(
        yaml.safe_dump({"version": 1, "entries": [invalid]}, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="normalized_url does not match"):
        municipal.load_operational_entries(path)

    invalid = operational_entry()
    invalid["municipalities"][0]["sido"] = "\ubd80\uc0b0\uad11\uc5ed\uc2dc"
    path.write_text(
        yaml.safe_dump({"version": 1, "entries": [invalid]}, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid or conflicting municipality pair"):
        generated_targets.municipal_operational_target_municipalities(path)

    invalid = operational_entry()
    invalid["municipalities"][0].update(
        {
            "sido": "서울특별시",
            "sigungu": "중구",
            "full_name": "서울특별시 중구",
        }
    )
    path.write_text(
        yaml.safe_dump({"version": 1, "entries": [invalid]}, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid or conflicting municipality pair"):
        generated_targets.municipal_operational_target_municipalities(path)

    conflicting_owner = operational_entry(provider="MUNI_OTHER_OWNER")
    path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "entries": [operational_entry(), conflicting_owner],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="normalized_url has conflicting providers"):
        generated_targets.municipal_operational_target_municipalities(path)

    invalid = operational_entry()
    invalid["action"] = "run_everything"
    path.write_text(
        yaml.safe_dump({"version": 1, "entries": [invalid]}, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="action is not executable"):
        municipal.load_operational_entries(path)


def test_library_operational_target_defaults_to_education() -> None:
    target = {
        **working_target(),
        "name": "강동구립도서관 전체 교육·독서 프로그램",
        "branch": "서울특별시 강동구",
        "source_group": "municipal_reservation",
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
    }

    selected = municipal.select_operational_targets(
        [target],
        [operational_entry()],
        scheduled_providers=set(),
    )

    assert selected[0]["service_group"] == "공공강좌"
    assert selected[0]["service_group_policy"] == "inferred"
    assert selected[0]["source_group"] == "library"
    assert selected[0]["collection_category"] == "도서관"
    assert selected[0]["domain_category"] == "도서관"

    crawl_target = generated_targets.to_crawl_target(selected[0])
    rows = [{"title": "어린이 독서교실", "service_group": "공공강좌"}]
    generated_targets.apply_target_metadata(rows, crawl_target)
    assert rows[0]["service_group"] == "공공강좌"
    assert rows[0]["service_group_policy"] == "inferred"
    assert rows[0]["source_group"] == "library"


def test_no_current_data_is_an_executable_validation_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = operational_entry()
    entry["action"] = "live_validate_new"
    entry["validation_outcome"] = "no_current_data"
    entry["row_count"] = 0
    entry["no_current_data"] = True
    path = tmp_path / "operational.yaml"
    path.write_text(
        yaml.safe_dump({"version": 1, "entries": [entry]}, sort_keys=False),
        encoding="utf-8",
    )

    entries = municipal.load_operational_entries(path)
    target = working_target()
    target["crawler_status"] = "no_current_data"
    loader_kwargs: dict = {}

    def load_targets(*_args, **kwargs):
        loader_kwargs.update(kwargs)
        return [target]

    monkeypatch.setattr(generated_targets, "load_yaml_targets", load_targets)
    monkeypatch.setattr(municipal, "load_operational_entries", lambda _path: entries)

    selected = municipal.load_municipal_targets(path=path, scheduled_providers=set())

    assert selected[0]["municipal_validation_outcome"] == "no_current_data"
    assert loader_kwargs["extra_statuses"] == {"no_current_data"}


def test_operational_parser_metadata_has_a_bounded_audit_length(
    tmp_path: Path,
) -> None:
    path = tmp_path / "operational.yaml"
    entry = operational_entry()
    entry["parser"] = "p" * municipal.MAX_OPERATIONAL_PARSER_LENGTH
    path.write_text(
        yaml.safe_dump({"version": 1, "entries": [entry]}, sort_keys=False),
        encoding="utf-8",
    )
    assert municipal.load_operational_entries(path)[0]["parser"] == entry["parser"]

    entry["parser"] += "x"
    path.write_text(
        yaml.safe_dump({"version": 1, "entries": [entry]}, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="parser must be a non-empty bounded string"):
        municipal.load_operational_entries(path)


def test_operational_validation_evidence_must_be_consistent(tmp_path: Path) -> None:
    entry = operational_entry()
    entry["row_count"] = 0
    path = tmp_path / "operational.yaml"
    path.write_text(
        yaml.safe_dump({"version": 1, "entries": [entry]}, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="collected requires row_count>0"):
        municipal.load_operational_entries(path)


def test_individually_scheduled_provider_is_removed_from_macro(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "CRAWLER_PROVIDERS",
        "MUNI_TEST_ALLOWED MUNICIPAL_RESERVATION_TARGETS HOMEPLUS",
    )
    assert municipal.select_operational_targets(
        [working_target()],
        [operational_entry()],
    ) == []


def test_missing_or_disabled_allowlist_target_fails_closed() -> None:
    with pytest.raises(ValueError, match="missing or disabled working targets"):
        municipal.select_operational_targets(
            [working_target(provider="MUNI_SOMETHING_ELSE")],
            [operational_entry()],
            scheduled_providers=set(),
        )


def test_locked_target_metadata_overwrites_collector_classification() -> None:
    target = CrawlTarget(
        provider="MUNI_TEST_ALLOWED",
        name="통합예약",
        branch="서울특별시 종로구",
        url=URL,
        source="test",
        priority=1,
        region="서울특별시",
        extra={**municipal.LOCKED_METADATA, "municipality_code": "1111000000"},
    )
    rows = [
        {
            "service_group": "체험",
            "source_group": "museum_science",
            "collection_category": "체험",
        }
    ]

    generated_targets.apply_target_metadata(rows, target)

    assert rows[0]["service_group"] == "공공강좌"
    assert rows[0]["source_group"] == "municipal_reservation"
    assert rows[0]["collection_category"] == "공공예약"
    assert rows[0]["service_group_policy"] == "locked"


@pytest.mark.parametrize(
    "provider",
    (
        "MUNI_WWW_GEUMCHEON_GO_KR_237EA1EA",
        "MUNI_WWW_GUMI_GO_KR_51F967B3",
        "MUNI_WWW_SEOGU_GO_KR_E4434123",
        "MUNI_WWW_ANDONG_GO_KR_1430676F",
        "MUNI_LIB_ANDONG_GO_KR_6B34DA7C",
        "MUNI_LIB_ANDONG_GO_KR_F96F2899",
        "MUNI_WWW_GOYANG_GO_KR_AFE8FBDD",
        "MUNI_RESVE_YONGIN_GO_KR_221336AC",
        "MUNI_WWW_HSG_GO_KR_7452F27B",
        "MUNI_HSYOUTHCENTER_HSG_GO_KR_46DEDE77",
        "MUNI_LIB_JEONGSEON_GO_KR_DD359707",
        "SUWON_RESERV_EDUCATION",
    ),
)
def test_reviewed_mixed_catalogues_preserve_explicit_course_level_experience(
    provider: str,
) -> None:
    target = CrawlTarget(
        provider=provider,
        name="교육·체험 통합예약",
        branch="통합예약",
        url=URL,
        source="test",
        priority=1,
        region="서울특별시",
        extra=dict(municipal.LOCKED_METADATA),
    )
    rows = [
        {
            "domain_category": "체험·견학",
            "service_group": "체험",
            "service_group_policy": "locked",
            "source_group": "municipal_reservation",
            "collection_category": "공공예약",
        }
    ]

    generated_targets.apply_target_metadata(rows, target)

    assert rows[0]["domain_category"] == "체험·견학"
    assert rows[0]["service_group"] == "체험"
    assert rows[0]["service_group_policy"] == "locked"


@pytest.mark.parametrize(
    ("domain_category", "service_group"),
    (
        ("교육·강좌", "체험"),
        ("체험·견학", "공공강좌"),
    ),
)
def test_mixed_catalogue_override_requires_the_exact_experience_pair(
    domain_category: str,
    service_group: str,
) -> None:
    target = CrawlTarget(
        provider="SUWON_RESERV_EDUCATION",
        name="수원시 교육·체험 통합예약",
        branch="수원시 통합예약",
        url=URL,
        source="test",
        priority=1,
        region="경기도 수원시",
        extra=dict(municipal.LOCKED_METADATA),
    )
    rows = [
        {
            "domain_category": domain_category,
            "service_group": service_group,
            "service_group_policy": "locked",
            "source_group": "municipal_reservation",
            "collection_category": "공공예약",
        }
    ]

    generated_targets.apply_target_metadata(rows, target)

    assert rows[0]["domain_category"] == "교육·강좌"
    assert rows[0]["service_group"] == "공공강좌"
    assert rows[0]["service_group_policy"] == "locked"


def test_operational_multi_target_trusts_only_original_whitelisted_pairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = CrawlTarget(
        provider="MUNI_RESERVE_INSISEOL_OR_KR_EC4B7776",
        name="인천시설공단 전체 교육",
        branch="인천광역시 남동구",
        url=URL,
        source="test",
        priority=1,
        region="인천광역시",
        extra={
            **municipal.LOCKED_METADATA,
            "municipality_code": "2820000000",
            "municipality_full_name": "인천광역시 남동구",
            "covered_municipalities": [
                {
                    "code": "2820000000",
                    "sido": "인천광역시",
                    "sigungu": "남동구",
                    "full_name": "인천광역시 남동구",
                },
                {
                    "code": "2818500000",
                    "sido": "인천광역시",
                    "sigungu": "연수구",
                    "full_name": "인천광역시 연수구",
                },
            ],
        },
    )
    bind_operational_target(
        monkeypatch,
        target.provider,
        target.extra["covered_municipalities"],
    )
    rows = [
        {
            "service_group": "체험",
            "source_group": "museum_science",
            "collection_category": "체험",
            "municipality_code": "2818500000",
            "municipality_full_name": "인천광역시 연수구",
        },
        {},
        {
            "municipality_code": "2818500000",
            "municipality_full_name": "",
        },
        {
            "municipality_code": "not-a-code",
            "municipality_full_name": "인천광역시 계양구",
        },
    ]

    generated_targets.apply_target_metadata(rows, target)

    assert (
        rows[0]["municipality_code"],
        rows[0]["municipality_full_name"],
    ) == ("2818500000", "인천광역시 연수구")
    assert rows[0]["municipality_region_verified"] is True
    assert "municipality_code" not in rows[1]
    assert "municipality_full_name" not in rows[1]
    assert "municipality_region_verified" not in rows[1]
    assert rows[2]["municipality_code"] == "2818500000"
    assert rows[2]["municipality_full_name"] == ""
    assert rows[2]["municipality_region_verified"] is False
    assert rows[3]["municipality_code"] == "not-a-code"
    assert rows[3]["municipality_region_verified"] is False
    branch = MunicipalDbWriter(target.provider).branch_info_from_row(rows[0])
    assert branch["region_sido"] == "인천광역시"
    assert branch["region_sigungu"] == "연수구"
    assert all(row["service_group"] == "공공강좌" for row in rows)
    assert all(row["source_group"] == "municipal_reservation" for row in rows)
    assert all(row["collection_category"] == "공공예약" for row in rows)


def test_locked_target_metadata_accepts_sejong_single_name_municipality(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = CrawlTarget(
        provider="SEJONG_SJFMC_EDUCATION",
        name="세종시설공단 교육",
        branch="세종특별자치시",
        url=URL,
        source="test",
        priority=1,
        region="세종특별자치시",
        extra={
            **municipal.LOCKED_METADATA,
            "municipality_code": "3611000000",
            "municipality_full_name": "세종특별자치시",
        },
    )
    bind_operational_target(
        monkeypatch,
        target.provider,
        [
            {
                "code": target.extra["municipality_code"],
                "full_name": target.extra["municipality_full_name"],
            }
        ],
    )
    rows = [{}]

    generated_targets.apply_target_metadata(rows, target)

    assert rows[0]["municipality_code"] == "3611000000"
    assert rows[0]["municipality_full_name"] == "세종특별자치시"
    assert rows[0]["municipality_region_verified"] is True
    branch = MunicipalDbWriter(target.provider).branch_info_from_row(rows[0])
    assert branch["region_sido"] == "세종특별자치시"
    assert branch["region_sigungu"] == "세종특별자치시"


def test_operational_single_target_falls_back_only_for_completely_missing_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = "MUNI_TEST_OPERATIONAL_SINGLE"
    target = CrawlTarget(
        provider=provider,
        name="종로구 전체 교육",
        branch="서울특별시 종로구",
        url=URL,
        source="test",
        priority=1,
        region="서울특별시",
        extra={
            **municipal.LOCKED_METADATA,
            "municipality_code": "1111000000",
            "municipality_full_name": "서울특별시 종로구",
            "covered_municipalities": [
                {
                    "code": "1111000000",
                    "sido": "서울특별시",
                    "sigungu": "종로구",
                    "full_name": "서울특별시 종로구",
                }
            ],
        },
    )
    bind_operational_target(
        monkeypatch,
        provider,
        target.extra["covered_municipalities"],
    )
    rows = [
        {"branch": "종로구 교육관"},
        {
            "branch": "종로구 평생학습관",
            "municipality_code": "1111000000",
            "municipality_full_name": "서울특별시 종로구",
        },
        {
            "branch": "부분 코드 행",
            "municipality_code": "1111000000",
            "municipality_full_name": "",
        },
        {
            "branch": "허용 범위 밖 행",
            "municipality_code": "1114000000",
            "municipality_full_name": "서울특별시 중구",
        },
    ]

    generated_targets.apply_target_metadata(rows, target)

    for row in rows[:2]:
        assert row["municipality_region_verified"] is True
        branch = MunicipalDbWriter(provider).branch_info_from_row(row)
        assert branch["region_sido"] == "서울특별시"
        assert branch["region_sigungu"] == "종로구"
    for row in rows[2:]:
        assert "municipality_code" in row
        assert "municipality_full_name" in row
        assert row["municipality_region_verified"] is False
        branch = MunicipalDbWriter(provider).branch_info_from_row(row)
        assert branch["region_sido"] == ""
        assert branch["region_sigungu"] == ""


def test_operational_single_target_preserves_collector_false_veto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = "MUNI_TEST_OPERATIONAL_VETO"
    municipality = operational_entry()["municipalities"][0]
    bind_operational_target(monkeypatch, provider, [municipality])
    target = CrawlTarget(
        provider=provider,
        name="collector veto target",
        branch="collector veto branch",
        url=URL,
        source="test",
        priority=1,
        region="",
        extra=dict(municipal.LOCKED_METADATA),
    )
    rows = [{"municipality_region_verified": False}]

    generated_targets.apply_target_metadata(rows, target)

    assert "municipality_code" not in rows[0]
    assert "municipality_full_name" not in rows[0]
    assert rows[0]["municipality_region_verified"] is False
    branch = MunicipalDbWriter(provider).branch_info_from_row(rows[0])
    assert branch["region_sido"] == ""
    assert branch["region_sigungu"] == ""


def test_operational_multi_target_does_not_verify_representative_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = "MUNI_TEST_OPERATIONAL_MULTI"
    target = CrawlTarget(
        provider=provider,
        name="청주시 전체 교육",
        branch="충청북도 청주시 및 4개 구",
        url=URL,
        source="test",
        priority=1,
        region="충청북도",
        extra={
            **municipal.LOCKED_METADATA,
            "municipality_code": "4311000000",
            "municipality_full_name": "충청북도 청주시",
            "covered_municipalities": [
                {
                    "code": "4311000000",
                    "sido": "충청북도",
                    "sigungu": "청주시",
                    "full_name": "충청북도 청주시",
                },
                {
                    "code": "4311100000",
                    "sido": "충청북도",
                    "sigungu": "청주시 상당구",
                    "full_name": "충청북도 청주시 상당구",
                },
            ],
        },
    )
    bind_operational_target(
        monkeypatch,
        provider,
        target.extra["covered_municipalities"],
    )
    rows = [
        {
            "branch": "상당구청소년수련관",
            "municipality_code": "4311100000",
            "municipality_full_name": "충청북도 청주시 상당구",
        },
        {"branch": "지역 증거 없는 교육관"},
    ]

    generated_targets.apply_target_metadata(rows, target)

    assert rows[0]["municipality_region_verified"] is True
    exact_branch = MunicipalDbWriter(provider).branch_info_from_row(rows[0])
    assert exact_branch["region_sido"] == "충청북도"
    assert exact_branch["region_sigungu"] == "청주시 상당구"
    assert "municipality_code" not in rows[1]
    assert "municipality_full_name" not in rows[1]
    assert "municipality_region_verified" not in rows[1]
    missing_branch = MunicipalDbWriter(provider).branch_info_from_row(rows[1])
    assert missing_branch["region_sido"] == ""
    assert missing_branch["region_sigungu"] == ""


def test_conflicting_operational_target_municipality_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = "MUNI_TEST_OPERATIONAL_INVALID"
    target = CrawlTarget(
        provider=provider,
        name="invalid target",
        branch="서울특별시 종로구",
        url=URL,
        source="test",
        priority=1,
        region="서울특별시",
        extra={
            **municipal.LOCKED_METADATA,
            "municipality_code": "1111000000",
            "municipality_full_name": "서울특별시 종로구",
            "covered_municipalities": [
                {
                    "code": "1111000000",
                    "sido": "부산광역시",
                    "sigungu": "종로구",
                    "full_name": "서울특별시 종로구",
                }
            ],
        },
    )
    bind_operational_target(
        monkeypatch,
        provider,
        [
            {
                "code": "1114000000",
                "sido": "\uc11c\uc6b8\ud2b9\ubcc4\uc2dc",
                "sigungu": "\uc911\uad6c",
                "full_name": "\uc11c\uc6b8\ud2b9\ubcc4\uc2dc \uc911\uad6c",
            }
        ],
    )
    rows = [
        {
            "branch": "종로구 교육관",
            "municipality_code": "1111000000",
            "municipality_full_name": "서울특별시 종로구",
            "municipality_region_verified": True,
        }
    ]

    generated_targets.apply_target_metadata(rows, target)

    assert rows[0]["municipality_code"] == "1111000000"
    assert rows[0]["municipality_region_verified"] is False
    branch = MunicipalDbWriter(provider).branch_info_from_row(rows[0])
    assert branch["region_sido"] == ""
    assert branch["region_sigungu"] == ""


def test_explicit_nonoperational_opt_in_keeps_conflicts_fail_closed() -> None:
    provider = "MUNI_EXPLICIT_SINGLE"
    target = CrawlTarget(
        provider=provider,
        name="explicit target",
        branch="제주특별자치도 서귀포시",
        url=URL,
        source="test",
        priority=1,
        region="제주특별자치도",
        extra={
            **municipal.LOCKED_METADATA,
            "municipality_code": "5013000000",
            "municipality_full_name": "제주특별자치도 서귀포시",
            "municipality_region_verified": True,
        },
    )
    rows = [
        {"branch": "서귀포 교육관"},
        {
            "branch": "다른 지역 교육관",
            "municipality_code": "5011000000",
            "municipality_full_name": "제주특별자치도 제주시",
            "municipality_region_verified": True,
        },
    ]

    generated_targets.apply_target_metadata(rows, target)

    assert rows[0]["municipality_region_verified"] is True
    assert MunicipalDbWriter(provider).branch_info_from_row(rows[0])[
        "region_sigungu"
    ] == "서귀포시"
    assert "municipality_region_verified" not in rows[1]
    conflict_branch = MunicipalDbWriter(provider).branch_info_from_row(rows[1])
    assert conflict_branch["region_sido"] == ""
    assert conflict_branch["region_sigungu"] == ""


def test_exact_operational_url_uses_manifest_when_target_metadata_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = "MUNI_TEST_RAW_OPERATIONAL_TARGET"
    municipality = operational_entry()["municipalities"][0]
    bind_operational_target(monkeypatch, provider, [municipality])
    target = CrawlTarget(
        provider=provider,
        name="raw operational target",
        branch="raw operational branch",
        url=URL,
        source="test",
        priority=1,
        region="",
        extra=dict(municipal.LOCKED_METADATA),
    )
    rows = [{}]

    generated_targets.apply_target_metadata(rows, target)

    assert rows[0]["municipality_code"] == municipality["code"]
    assert rows[0]["municipality_full_name"] == municipality["full_name"]
    assert rows[0]["municipality_region_verified"] is True
    branch = MunicipalDbWriter(provider).branch_info_from_row(rows[0])
    assert branch["region_sido"] == municipality["sido"]
    assert branch["region_sigungu"] == municipality["sigungu"]


def test_nonoperational_provider_sibling_preserves_evidence_without_writer_trust() -> None:
    provider = "MUNI_RESERVE_INSISEOL_OR_KR_EC4B7776"
    assert provider in generated_targets.MUNICIPAL_OPERATIONAL_PROVIDER_NAMES
    assert (
        provider,
        generated_targets.normalized_duplicate_url(URL),
    ) not in generated_targets.MUNICIPAL_OPERATIONAL_TARGET_MUNICIPALITIES
    manifest_pair = next(
        municipalities[0]
        for (manifest_provider, _url), municipalities in (
            generated_targets.MUNICIPAL_OPERATIONAL_TARGET_MUNICIPALITIES.items()
        )
        if manifest_provider == provider
    )
    target = CrawlTarget(
        provider=provider,
        name="non-operational sibling",
        branch="non-operational sibling",
        url=URL,
        source="test",
        priority=1,
        region="",
        extra=dict(municipal.LOCKED_METADATA),
    )
    rows = [
        {
            "municipality_code": manifest_pair[0],
            "municipality_full_name": manifest_pair[1],
            "municipality_region_verified": True,
        }
    ]

    generated_targets.apply_target_metadata(rows, target)

    assert rows[0]["municipality_code"] == manifest_pair[0]
    assert rows[0]["municipality_full_name"] == manifest_pair[1]
    assert rows[0]["municipality_region_verified"] is False
    branch = MunicipalDbWriter(provider).branch_info_from_row(rows[0])
    assert branch["region_sido"] == ""
    assert branch["region_sigungu"] == ""


@pytest.mark.parametrize(
    ("provider", "branch_name", "expected_sido", "expected_sigungu"),
    [
        (
            "MUNI_ETICKET_SEOGWIPO_GO_KR_C87B50AB",
            "서귀포시 건강생활지원센터",
            "제주특별자치도",
            "서귀포시",
        ),
        (
            "MUNI_LIBRARY_DAEGU_GO_KR_89EAABA5",
            "대구2ㆍ28민주운동기념도서관",
            "대구광역시",
            "중구",
        ),
        (
            "MUNI_BIZ_NAMDONG_GO_KR_8423F6B9",
            "남동구평생학습관",
            "인천광역시",
            "남동구",
        ),
    ],
)
def test_verified_single_municipality_collection_rows_persist_branch_region(
    provider: str,
    branch_name: str,
    expected_sido: str,
    expected_sigungu: str,
) -> None:
    configured = municipal.load_municipal_targets(scheduled_providers=set())
    target_item = next(row for row in configured if row["provider"] == provider)
    target = generated_targets.to_crawl_target(target_item)
    rows = [
        {
            "title": "공식 교육 프로그램",
            "branch": branch_name,
            "branch_code": f"{provider}_BRANCH",
        }
    ]

    assert target.extra["municipality_region_verified"] is True
    generated_targets.apply_target_metadata(rows, target)
    branch = MunicipalDbWriter(provider).branch_info_from_row(rows[0])

    assert rows[0]["municipality_region_verified"] is True
    assert rows[0]["municipality_code"] == target.extra["municipality_code"]
    assert rows[0]["municipality_full_name"] == target.extra["municipality_full_name"]
    assert branch["region_sido"] == expected_sido
    assert branch["region_sigungu"] == expected_sigungu


def test_unverified_generated_target_municipality_remains_fail_closed() -> None:
    target = CrawlTarget(
        provider="MUNI_LEGACY",
        name="legacy",
        branch="경기도 수원시",
        url=URL,
        source="test",
        priority=1,
        region="경기도",
        extra={
            "municipality_code": "4111000000",
            "municipality_full_name": "경기도 수원시",
        },
    )
    rows = [{"title": "레거시 강좌", "branch": "수원시 통합예약"}]

    generated_targets.apply_target_metadata(rows, target)
    branch = MunicipalDbWriter(target.provider).branch_info_from_row(rows[0])

    assert "municipality_region_verified" not in rows[0]
    assert branch["region_sido"] == ""
    assert branch["region_sigungu"] == ""


def test_mixed_yeongdo_owner_preserves_inferred_library_row_classification() -> None:
    metadata = municipal.operational_target_metadata(
        {
            "provider": "MUNI_WWW_YEONGDO_GO_KR_33400564",
            "name": "부산광역시 영도구 전체 교육 원장",
            "branch": "부산광역시 영도구",
            "source_group": "municipal_reservation",
        }
    )
    assert metadata["service_group"] == "공공강좌"
    assert metadata["service_group_policy"] == ""

    target = CrawlTarget(
        provider="MUNI_WWW_YEONGDO_GO_KR_33400564",
        name="부산광역시 영도구 전체 교육 원장",
        branch="부산광역시 영도구",
        url="https://www.yeongdo.go.kr/reserve/01785/01791.web",
        source="test",
        priority=1,
        region="부산광역시",
        extra=metadata,
    )
    rows = [
        {
            "title": "독서교실",
            "source_group": "library",
            "collection_category": "도서관",
            "domain_category": "도서관",
            "service_group": "공공강좌",
            "service_group_policy": "inferred",
        },
        {"title": "주민 강좌"},
    ]

    generated_targets.apply_target_metadata(rows, target)

    assert rows[0]["service_group"] == "공공강좌"
    assert rows[0]["source_group"] == "library"
    assert rows[0]["service_group_policy"] == "inferred"
    assert rows[1]["service_group"] == "공공강좌"
    assert rows[1]["source_group"] == "municipal_reservation"


def test_only_promoted_new_targets_are_excluded_from_generated_registry() -> None:
    promoted = {
        "provider": "MUNI_PROMOTED_NEW",
        "url": URL,
        "crawler_status": "partial",
        "source_group": "municipal_reservation",
        "origin": "live_validated",
    }
    promoted_by_file = {
        **promoted,
        "provider": "MUNI_PROMOTED_FILE",
        "origin": "manual",
        "_target_file": "municipal_integrated_reservation.yaml",
    }
    schedule_existing = {
        **promoted,
        "provider": "MUNI_EXISTING_OWNER",
        "source_group": "lifelong_learning",
        "origin": "collected",
        "_target_file": "lifelong_learning.yaml",
    }
    individually_scheduled = {
        **promoted,
        "provider": "MUNI_WWW_DANGJIN_GO_KR_3C378AA6",
        "_target_file": "lifelong_learning.yaml",
    }
    operational_existing = {
        **schedule_existing,
        "provider": "MUNI_WWW_SANGJU_GO_KR_A813366C",
        "source_group": "municipal_reservation",
        "origin": "validated",
    }

    assert generated_targets._is_registry_target(promoted) is False
    assert generated_targets._is_registry_target(promoted_by_file) is False
    assert generated_targets._is_registry_target(schedule_existing) is True
    assert operational_existing["provider"] in generated_targets.MUNICIPAL_OPERATIONAL_PROVIDER_NAMES
    assert generated_targets._is_registry_target(operational_existing) is False
    assert individually_scheduled["provider"] in generated_targets.PRODUCTION_SCHEDULED_PROVIDER_NAMES
    assert generated_targets._is_registry_target(individually_scheduled) is True


def test_static_macro_command_requires_complete_rows_before_stale_cleanup() -> None:
    provider = "MUNICIPAL_RESERVATION_TARGETS"
    assert provider in runner.STATIC_PROVIDER_COMMANDS
    assert provider in runner.PARTIAL_AGGREGATE_PROVIDER_NAMES
    command = runner.build_provider_command(provider, None)
    script_index = next(index for index, value in enumerate(command) if value.endswith(".py"))
    assert Path(command[script_index]).name == "Crawler_MunicipalIntegratedReservation.py"
    tail = command[script_index + 1 :]
    parsed = generated_targets.parse_args(tail)
    assert parsed.save_db is True
    assert parsed.mark_stale is True
    assert parsed.per_target_limit == 0
    assert parsed.allow_partial_save is False
    assert parsed.max_pages == 1500
    assert parsed.detail_limit == 3000
    assert parsed.parallel_workers == 3


def test_example_provider_lists_schedule_the_macro_once() -> None:
    root = Path(runner.PROJECT_ROOT)
    for path in (
        root / ".env.example",
        root / "deploy" / "ubuntu" / "mooncen.env.example",
        root / "deploy" / "ubuntu" / "setup_project.sh",
    ):
        text = path.read_text(encoding="utf-8")
        assert "MUNICIPAL_RESERVATION_TARGETS" in text
        for line in text.splitlines():
            if line.startswith("CRAWLER_PROVIDERS="):
                assert line.split().count("MUNICIPAL_RESERVATION_TARGETS") <= 1


@pytest.mark.parametrize(
    ("provider", "max_pages", "detail_limit"),
    (
        ("MUNI_LIFELONG_MOKPO_GO_KR_0E89BA53", 20, 200),
        ("MUNI_WWW_WANDO_GO_KR_AFCA6FD7", 10, 200),
        ("MUNI_WWW_WANDO_GO_KR_64D0194B", 10, 200),
        ("MUNI_WWW_YC_GO_KR_54558363", 120, 200),
        ("MUNI_LLL_PAJU_GO_KR_F639C571", 100, 500),
        ("MUNI_PAJU_PCY_OR_KR_412053A6", 20, 300),
        ("MUNI_WWW_GCCITY_GO_KR_854A9E81", 700, 600),
        ("MUNI_JUMIN_NYJ_GO_KR_4D92ADDF", 200, 1500),
        ("MUNI_SUGANG_GM_GO_KR_F136DD19", 900, 1000),
        ("MUNI_WWW_SIHEUNG_GO_KR_0A4570AD", 230, 400),
        ("MUNI_SPORTSAPP_SHSI_OR_KR_6239E7D6", 32, 300),
        ("MUNI_WWW_HANAM_GO_KR_553EE539", 240, 650),
        ("MUNI_WWW_HANAM_GO_KR_04578639", 240, 650),
        ("MUNI_ONLINE_HNYOUTH_KR_6F390C33", 240, 650),
        ("MUNI_WWW_HDREAM_OR_KR_064EE411", 240, 650),
        ("MUNI_WWW_HANAMLIB_GO_KR_EE810F0A", 240, 650),
        ("MUNI_WWW_ICHEON_GO_KR_1B4316ED", 450, 500),
        ("MUNI_ICHEON_GSEEK_KR_18B68AC1", 450, 500),
        ("MUNI_WWW_ICHEONLIB_GO_KR_76E3CE6D", 450, 500),
        ("MUNI_WWW_ARTIC_OR_KR_9B6E3C8E", 450, 500),
        ("MUNI_CTM_GUNPO_GO_KR_2ADC8672", 250, 1500),
        ("MUNI_SSO_GUNPO_GO_KR_C6EB5B7F", 250, 1500),
        ("MUNI_WWW_GUNPOCF_OR_KR_72C2BA1D", 250, 1500),
        ("MUNI_WWW_GPMEDIA_OR_KR_6517BB69", 250, 1500),
        ("MUNI_WWW_GUNPOLIB_GO_KR_6657561E", 250, 1500),
        ("MUNI_WWW_GUNPOUC_OR_KR_C6BD9C41", 250, 1500),
        ("MUNI_WWW_GPYF_OR_KR_85203167", 250, 1500),
        ("MUNI_WWW_GUNPO_GO_KR_FE43B335", 250, 1500),
        ("MUNI_WWW_GUNPOYCF_OR_KR_ED267E43", 250, 1500),
        ("MUNI_GJEDU_GSEEK_KR_F929637E", 700, 1200),
        ("MUNI_WWW_GJCITY_GO_KR_CF520672", 700, 1200),
        ("MUNI_LIB_GJCITY_GO_KR_56EBD1BF", 700, 1200),
        ("MUNI_WWW_GJCITY_GO_KR_4BA53CE8", 700, 1200),
        ("MUNI_WWW_GJCITY_GO_KR_5B834C82", 700, 1200),
        ("MUNI_WWW_GJYOUTH_OR_KR_E2AB883F", 700, 1200),
        ("MUNI_YPEDU_GSEEK_KR_41263F0B", 100, 100),
        ("MUNI_WWW_YP21_GO_KR_EA0D7B81", 100, 100),
        ("MUNI_WWW_YP21_GO_KR_632CD45F", 100, 100),
        ("MUNI_WWW_YPLIB_GO_KR_C3854B7C", 100, 100),
        ("MUNI_RESVE_YONGIN_GO_KR_221336AC", 20, 100),
        ("MUNI_JACHI_YONGIN_GO_KR_10340408", 60, 250),
        ("MUNI_JACHI_YONGIN_GO_KR_60025DB9", 70, 800),
        ("MUNI_JACHI_YONGIN_GO_KR_91C5118C", 50, 600),
        ("MUNI_LIB_YONGIN_GO_KR_B7626320", 60, 250),
        ("MUNI_WWW_YICF_OR_KR_B2E137D5", 10, 40),
        ("MUNI_YIYF_OR_KR_F56DFD54", 5, 20),
        ("MUNI_SPORTS_YIYF_OR_KR_206DDBA6", 35, 260),
        ("MUNI_WWW_SAMCHEOK_GO_KR_AEA01740", 20, 300),
        ("MUNI_YOUTH_SAMCHEOK_GO_KR_96E8E691", 10, 100),
        ("MUNI_DGYOUTH_SAMCHEOK_GO_KR_C683FA1B", 10, 100),
        ("MUNI_WDYOUTH_SAMCHEOK_GO_KR_AE04F451", 10, 100),
        ("MUNI_WWW_HSG_GO_KR_7452F27B", 20, 100),
        ("MUNI_LIB_HSG_GO_KR_F84FF98D", 2, 25),
        ("MUNI_LIB_GWE_GO_KR_5CEF7967", 2, 25),
        ("MUNI_HSYOUTHCENTER_HSG_GO_KR_46DEDE77", 5, 50),
        ("MUNI_HS_CULTURE_OR_KR_B2E1E14F", 1, 50),
        ("MUNI_HSG_FAMILYNET_OR_KR_4676E082", 10, 50),
        ("MUNI_WWW_CHEONAN_GO_KR_478DFA4B", 5, 50),
        ("MUNI_WWW_CHEONAN_GO_KR_5BC13FB4", 60, 50),
        ("MUNI_WWW_CHEONAN_GO_KR_7F8F5560", 5, 100),
        ("MUNI_WWW_CHEONAN_GO_KR_C97CA6FD", 5, 100),
        ("MUNI_WWW_CHEONAN_GO_KR_EA8D366B", 3, 100),
        ("MUNI_WWW_CHEONANLIFEEDU_ORG_41183F3B", 5, 50),
        ("MUNI_WWW_XN_2Z1BR4K89DEOA28DJVFZVASSQ98BDZK_KR_81F", 20, 50),
        ("MUNI_WWW_JEJU_GO_KR_2B65844D", 30, 100),
        ("MUNI_WWW_JEJUSI_GO_KR_72D06B44", 40, 100),
        ("MUNI_WWW_JEJUSI_GO_KR_A449522B", 25, 50),
        ("MUNI_WWW_JEJU_GO_KR_6E577892", 100, 50),
        ("MUNI_WWW_JEJU_GO_KR_310502FA", 20, 100),
        ("MUNI_AGRI_JEJU_GO_KR_84F944BE", 35, 50),
        ("MUNI_WWW_JEJUSI_GO_KR_F9643CD9", 25, 50),
        ("MUNI_JJDREAMLIB_OR_KR_1A8AAB7D", 35, 50),
        ("MUNI_WWW_HONGCHEON_GO_KR_F5083BE8", 30, 200),
        ("MUNI_HONGCHEONLIB_GO_KR_17726A2C", 20, 200),
        ("MUNI_LIB_GWE_GO_KR_20A09F24", 20, 200),
        ("MUNI_LIB_JEONGSEON_GO_KR_DD359707", 30, 300),
    ),
)
def test_mokpo_wando_and_yeongcheon_generated_bounds_are_complete(
    provider: str,
    max_pages: int,
    detail_limit: int,
) -> None:
    arguments = generated_targets.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[provider]
    parsed = generated_targets.parse_args(arguments)

    assert parsed.save_db is True
    assert parsed.mark_stale is True
    assert parsed.allow_partial_save is False
    assert parsed.per_target_limit == 0
    assert parsed.max_pages == max_pages
    assert parsed.detail_limit == detail_limit


@pytest.mark.parametrize(
    ("provider", "live_pages", "live_detail_pages"),
    (
        ("MUNI_WWW_JEJU_GO_KR_2B65844D", 23, 63),
        ("MUNI_WWW_JEJUSI_GO_KR_72D06B44", 32, 68),
        ("MUNI_WWW_JEJUSI_GO_KR_A449522B", 20, 9),
        ("MUNI_WWW_JEJU_GO_KR_6E577892", 89, 0),
        ("MUNI_WWW_JEJU_GO_KR_310502FA", 14, 42),
        ("MUNI_AGRI_JEJU_GO_KR_84F944BE", 29, 16),
        ("MUNI_WWW_JEJUSI_GO_KR_F9643CD9", 19, 2),
        ("MUNI_JJDREAMLIB_OR_KR_1A8AAB7D", 27, 13),
    ),
)
def test_jeju_generated_caps_exceed_aggregate_live_usage(
    provider: str,
    live_pages: int,
    live_detail_pages: int,
) -> None:
    parsed = generated_targets.parse_args(
        generated_targets.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[provider]
    )
    # The generated runner treats equality as cap exhaustion.  These bounds
    # therefore must exceed, not merely equal, the latest complete live run.
    assert parsed.max_pages > live_pages
    assert parsed.detail_limit > live_detail_pages


def test_no_selected_operational_targets_is_a_successful_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(municipal, "load_municipal_targets", lambda: [])
    assert municipal.run([]) == 0


def test_real_operational_manifest_selects_recent_busan_atomic_owners_once() -> None:
    targets = municipal.load_municipal_targets(scheduled_providers=set())
    keys = [
        (
            target["provider"],
            generated_targets.normalized_duplicate_url(
                generated_targets.target_url(target)
            ),
        )
        for target in targets
    ]
    assert len(keys) == len(set(keys))
    assert len(targets) >= 195

    namwon_urls = {
        generated_targets.normalized_duplicate_url(
            generated_targets.target_url(target)
        )
        for target in targets
        if target["provider"] == "MUNI_WWW_NAMWON_GO_KR_37D4EA88"
    }
    assert namwon_urls == {
        "https://www.namwon.go.kr/reserve",
        "https://www.namwon.go.kr/reserve/index.do?historyPage=1&menuUid=ff8080818fbe6488018fdcadaa120249",
        "https://www.namwon.go.kr/reserve/index.do?historyPage=1&menuUid=ff80808190963f64019096945f6000b9",
    }

    by_provider = {target["provider"]: target for target in targets}
    expected = {
        "BUSAN_DONGGU_RESERVATION": 155,
        "MUNI_WWW_SAHA_GO_KR_ED7CDFC9": 15,
        "MUNI_LLL_BSGANGSEO_GO_KR_0691B6EB": 18,
        "MUNI_WWW_HAEUNDAE_GO_KR_E2AD27FA": 200,
        "SASANG_RESERVATION": 122,
        "MUNI_WWW_YEONJE_GO_KR_73BA35A2": 132,
        "MUNI_WWW_SUYEONG_GO_KR_41E9DDEB": 117,
        "MUNI_NAM_DAEGU_KR_1E00F39A": 78,
        "MUNI_LLL_SUSEONG_KR_2C82AF9F": 396,
        "MUNI_WWW_YEONSU_GO_KR_CB4C41BB": 580,
        "MUNI_GYLLE_GYEYANG_GO_KR_1630ABDE": 177,
        "MUNI_WWW_GIJANG_GO_KR_592C4B5E": 10,
        "MUNI_WWW_GANGHWA_GO_KR_E1374F0C": 315,
        "MUNI_WWW_ONGJIN_GO_KR_0243B215": 12,
        "MUNI_DDC_GSEEK_KR_97F9673C": 138,
        "MUNI_WWW_OSANEDU_GO_KR_8A50CEDC": 264,
        "MUNI_WWW_CHEONGYANG_GO_KR_25520BA7": 1,
        "MUNI_WWW_CNG_GO_KR_84B93860": 148,
        "MUNI_WWW_LIFELONGGEOJE_KR_D866D2AF": 23,
        "MUNI_YEOJU_GSEEK_KR_1034027F": 60,
        "MUNI_WWW_HONGSEONG_GO_KR_C700BF28": 25,
        "MUNI_HONGCHEONLIB_GO_KR_17726A2C": 15,
        "MUNI_EDU_BUYEO_GO_KR_8DF02931": 37,
        "MUNI_WWW_BUAN_GO_KR_B5BDBAE0": 9,
        "MUNI_LLL_ANSAN_GO_KR_691646BE": 2260,
        "MUNI_WWW_GC_GO_KR_91618000": 35,
        "MUNI_WWW_FUTURECSY_OR_KR_D9EE9C9C": 14,
        "MUNI_WWW_GYEONGJU_GO_KR_ADA8A467": 316,
        "MUNI_WWW_CHILGOK_GO_KR_B19807DD": 41,
        "MUNI_WWW_HC_GO_KR_3C13AEC0": 11,
        "MUNI_WWW_USC_GO_KR_AFF8D61A": 38,
        "MUNI_WWW_JINAN_GO_KR_3DF1AE69": 103,
        "MUNI_WWW_MUJU_GO_KR_953B498D": 18,
        "MUNI_WWW_JANGSU_GO_KR_2100CCEA": 15,
        "MUNI_WWW_JEONGEUP_GO_KR_C8631DF4": 7,
        "MUNI_WWW_GBELIB_KR_04DB1B82": 5,
        "MUNI_WWW_CHEONGDO_GO_KR_0AE7DACF": 11,
        "MUNI_LIB_IMSIL_GO_KR_C73F4E31": 3,
        "MUNI_WWW_BONGHWA_GO_KR_A33FDB5A": 16,
        "MUNI_WWW_ULJIN_GO_KR_3EFF1FF0": 21,
        "MUNI_WWW_GBELIB_KR_D515FD5D": 2,
        "MUNI_ULLEUNGGUN_FAMILYNET_OR_KR_10E2058E": 7,
        "MUNI_WWW_ULLEUNG_GO_KR_765C23CB": 14,
        "MUNI_WWW_GORYEONG_GO_KR_8F708B74": 3,
        "MUNI_WWW_SANGJU_GO_KR_A813366C": 43,
        "MUNI_WWW_GBGS_GO_KR_87106AA0": 228,
        "MUNI_WWW_GBGS_GO_KR_999BABE7": 80,
        "SEOSAN_WELFARE_TOTAL_RESERVATION": 80,
        "MUNI_WWW_YANGSAN_GO_KR_059D4DD1": 13,
        "MUNI_WWW_YANGSAN_GO_KR_DBBB1885": 100,
        "MUNI_EDUCITY_GEOCHANG_GO_KR_3187BF2A": 40,
        "HAMAN_WELFARE_LIFELONG_COURSE": 76,
        "MUNI_WWW_GIMHAE_GO_KR_48CF9E63": 426,
        "MUNI_WWW_GYERYONG_GO_KR_42F86CD2": 20,
        "MUNI_CN_SEOCHEON_GO_KR_096AAB21": 34,
        "MUNI_WWW_WONJU_GO_KR_56B0C690": 378,
        "MUNI_LIB_GWE_GO_KR_5D9C27C1": 8,
        "MUNI_LLL_PAJU_GO_KR_F639C571": 210,
        "MUNI_PAJU_PCY_OR_KR_412053A6": 183,
        "MUNI_WWW_GCCITY_GO_KR_854A9E81": 329,
        "MUNI_JUMIN_NYJ_GO_KR_4D92ADDF": 1037,
        "MUNI_SUGANG_GM_GO_KR_F136DD19": 664,
        "MUNI_WWW_SIHEUNG_GO_KR_0A4570AD": 310,
        "MUNI_SPORTSAPP_SHSI_OR_KR_6239E7D6": 241,
        "MUNI_WWW_HANAM_GO_KR_553EE539": 70,
        "MUNI_WWW_HANAM_GO_KR_04578639": 294,
        "MUNI_ONLINE_HNYOUTH_KR_6F390C33": 8,
        "MUNI_WWW_HDREAM_OR_KR_064EE411": 7,
        "MUNI_WWW_HANAMLIB_GO_KR_EE810F0A": 58,
        "MUNI_WWW_ICHEON_GO_KR_1B4316ED": 40,
        "MUNI_ICHEON_GSEEK_KR_18B68AC1": 215,
        "MUNI_WWW_ICHEONLIB_GO_KR_76E3CE6D": 100,
        "MUNI_WWW_ARTIC_OR_KR_9B6E3C8E": 10,
        "MUNI_CTM_GUNPO_GO_KR_2ADC8672": 155,
        "MUNI_SSO_GUNPO_GO_KR_C6EB5B7F": 13,
        "MUNI_WWW_GUNPOCF_OR_KR_72C2BA1D": 204,
        "MUNI_WWW_GPMEDIA_OR_KR_6517BB69": 6,
        "MUNI_WWW_GUNPOLIB_GO_KR_6657561E": 96,
        "MUNI_WWW_GUNPOUC_OR_KR_C6BD9C41": 5,
        "MUNI_WWW_GPYF_OR_KR_85203167": 130,
        "MUNI_WWW_GUNPO_GO_KR_FE43B335": 0,
        "MUNI_WWW_GUNPOYCF_OR_KR_ED267E43": 3,
    }
    library_providers = {
        "MUNI_HONGCHEONLIB_GO_KR_17726A2C",
        "MUNI_WWW_GBELIB_KR_04DB1B82",
        "MUNI_LIB_IMSIL_GO_KR_C73F4E31",
        "MUNI_WWW_GBELIB_KR_D515FD5D",
        "MUNI_LIB_GWE_GO_KR_5D9C27C1",
        "MUNI_WWW_HANAMLIB_GO_KR_EE810F0A",
        "MUNI_WWW_ICHEONLIB_GO_KR_76E3CE6D",
        "MUNI_WWW_GUNPOLIB_GO_KR_6657561E",
    }
    for provider, validated_rows in expected.items():
        target = by_provider[provider]
        assert target["municipal_validation_row_count"] == validated_rows
        expected_outcome = (
            "no_current_data"
            if provider == "MUNI_WWW_GUNPO_GO_KR_FE43B335"
            else "collected"
        )
        assert target["municipal_validation_outcome"] == expected_outcome
        expected_group = (
            "체험"
            if provider == "MUNI_WWW_HDREAM_OR_KR_064EE411"
            else "공공강좌"
        )
        assert target["service_group"] == expected_group
        expected_policy = "inferred" if provider in library_providers else "locked"
        assert target["service_group_policy"] == expected_policy

    gunwi = by_provider["MUNI_EDU_GWA_GO_KR_08B25674"]
    assert gunwi["municipal_validation_row_count"] == 0
    assert gunwi["municipal_validation_outcome"] == "no_current_data"
    assert gunwi["service_group"] == "공공강좌"
    assert gunwi["service_group_policy"] == "locked"

    seongju = by_provider["MUNI_WWW_SJ_WELFARE_OR_KR_335868A2"]
    assert seongju["municipal_validation_row_count"] == 0
    assert seongju["municipal_validation_outcome"] == "no_current_data"
    assert seongju["service_group"] == "공공강좌"
    assert seongju["service_group_policy"] == "locked"

    sunchang = by_provider["MUNI_WWW_SCEDULIFE_CO_KR_C6522638"]
    assert sunchang["municipal_validation_row_count"] == 1
    assert sunchang["municipal_validation_outcome"] == "collected"
    assert sunchang["service_group"] == "공공강좌"
    assert sunchang["service_group_policy"] == "locked"

    taebaek = by_provider["MUNI_WWW_TAEBAEK_GO_KR_89A80ED6"]
    assert taebaek["municipal_validation_row_count"] == 38
    assert taebaek["municipal_validation_outcome"] == "collected"
    assert taebaek["service_group"] == "공공강좌"
    assert taebaek["service_group_policy"] == "locked"

    for provider, validated_rows, expected_policy in (
        ("MUNI_WWW_GOESAN_GO_KR_EAE2C3E3", 16, "locked"),
        ("MUNI_WWW_GHLIB_GO_KR_AAEB8BF2", 3, "inferred"),
    ):
        refreshed = by_provider[provider]
        assert refreshed["municipal_validation_row_count"] == validated_rows
        assert refreshed["municipal_validation_outcome"] == "collected"
        assert refreshed["service_group"] == "공공강좌"
        assert refreshed["service_group_policy"] == expected_policy

    cheongdo_youth = by_provider["MUNI_WWW_CHEONGDO_GO_KR_4F44CA8E"]
    assert cheongdo_youth["municipal_validation_row_count"] == 0
    assert cheongdo_youth["municipal_validation_outcome"] == "no_current_data"
    assert cheongdo_youth["service_group"] == "공공강좌"
    assert cheongdo_youth["service_group_policy"] == "locked"

    cheongdo_reservation = by_provider["MUNI_WWW_CHEONGDO_GO_KR_9BD015B5"]
    assert cheongdo_reservation["municipal_validation_row_count"] == 46
    assert cheongdo_reservation["municipal_validation_outcome"] == "collected"
    assert cheongdo_reservation["service_group"] == "공공강좌"
    assert cheongdo_reservation["service_group_policy"] == "locked"
