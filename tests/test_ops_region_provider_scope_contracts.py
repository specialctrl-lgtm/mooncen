from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from backend.ops import region_collection
from tools.report_scope_region_coverage import (
    load_municipality_index,
    load_provider_municipalities,
    resolve_target_municipalities,
)


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
TARGET_DIR = CONFIG / "crawl_targets"
EMPTY_PATH = ROOT / ".missing-region-scope-contract.yaml"

EXPECTED_OPERATIONAL_SCOPES = {
    "DAEGU_RESERVATION": {
        "대구광역시 중구",
        "대구광역시 동구",
        "대구광역시 서구",
        "대구광역시 북구",
        "대구광역시 달서구",
    },
    "MUNI_RESVE_YONGIN_GO_KR_221336AC": {
        "경기도 용인시",
        "경기도 용인시 처인구",
        "경기도 용인시 기흥구",
        "경기도 용인시 수지구",
    },
    "MUNI_WWW_CHEONANLIFEEDU_ORG_41183F3B": {
        "충청남도 천안시 서북구"
    },
    "MUNI_WWW_CHEONAN_GO_KR_5BC13FB4": {
        "충청남도 천안시",
        "충청남도 천안시 동남구",
        "충청남도 천안시 서북구",
    },
    "MUNI_WWW_CHEONAN_GO_KR_478DFA4B": {
        "충청남도 천안시",
        "충청남도 천안시 동남구",
        "충청남도 천안시 서북구",
    },
    "MUNI_WWW_CHEONAN_GO_KR_C97CA6FD": {"충청남도 천안시 서북구"},
    "MUNI_WWW_CHEONAN_GO_KR_EA8D366B": {"충청남도 천안시 서북구"},
    "MUNI_WWW_XN_2Z1BR4K89DEOA28DJVFZVASSQ98BDZK_KR_81F": {
        "충청남도 천안시 동남구"
    },
}


def _load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    assert isinstance(value, dict)
    return value


def test_operational_and_config_unions_match_exact_collector_ownership() -> None:
    index = load_municipality_index(
        CONFIG / "municipal_course_search_targets.yaml"
    )
    operational = load_provider_municipalities(
        index,
        target_dir=ROOT / ".missing-target-directory",
        coverage_path=EMPTY_PATH,
        operational_path=(
            CONFIG / "municipal_integrated_reservation_operational.yaml"
        ),
        include_targets=False,
    )
    configured = load_provider_municipalities(
        index,
        target_dir=TARGET_DIR,
        coverage_path=(
            CONFIG / "municipal_integrated_reservation_coverage.yaml"
        ),
        operational_path=(
            CONFIG / "municipal_integrated_reservation_operational.yaml"
        ),
        include_region_fallback=False,
    )

    for provider, expected in EXPECTED_OPERATIONAL_SCOPES.items():
        assert operational[provider] == expected
        assert configured[provider] == expected


def test_daegu_runtime_location_allowlist_is_broader_only_for_exact_experience() -> None:
    index = load_municipality_index(
        CONFIG / "municipal_course_search_targets.yaml"
    )
    runtime = load_provider_municipalities(
        index,
        target_dir=TARGET_DIR,
        coverage_path=(
            CONFIG / "municipal_integrated_reservation_coverage.yaml"
        ),
        operational_path=(
            CONFIG / "municipal_integrated_reservation_operational.yaml"
        ),
        include_region_fallback=False,
        region_fallback_providers=(
            region_collection.REGION_WIDE_LOCATION_FALLBACK_PROVIDERS
        ),
    )
    assert runtime["DAEGU_RESERVATION"] == {
        municipality.full_name
        for municipality in index.municipalities
        if municipality.sido == "대구광역시"
    }

    targets = [
        target
        for target in region_collection._scope_target_rows()
        if target.get("provider") == "DAEGU_RESERVATION"
    ]
    by_url = {region_collection._target_url(target): target for target in targets}
    experience = by_url["https://yeyak.daegu.go.kr/expr/list"]
    education = by_url["https://yeyak.daegu.go.kr/lect/list"]

    assert resolve_target_municipalities(
        experience,
        index,
        include_region_fallback=False,
        region_fallback_providers=(
            region_collection._configured_region_fallback_providers(experience)
        ),
    ) == runtime["DAEGU_RESERVATION"]
    assert resolve_target_municipalities(
        education,
        index,
        include_region_fallback=False,
        region_fallback_providers=(
            region_collection._configured_region_fallback_providers(education)
        ),
    ) == EXPECTED_OPERATIONAL_SCOPES["DAEGU_RESERVATION"]


def test_override_sources_do_not_reintroduce_parent_region_drift() -> None:
    document = _load_yaml(
        CONFIG / "municipal_integrated_reservation_overrides.yaml"
    )
    by_code = {
        str(row.get("code") or ""): row
        for row in document.get("municipalities") or []
    }

    cheoin_candidates = by_code["4146100000"]["candidates"]
    yongin = next(
        row
        for row in cheoin_candidates
        if row.get("provider") == "MUNI_RESVE_YONGIN_GO_KR_221336AC"
    )
    assert yongin["status"] == "excluded"
    assert (
        yongin["exclusion_reason"]
        == "citywide_reservation_rows_use_parent_municipality"
    )

    parent_cheonan_providers = {
        str(row.get("provider") or "")
        for row in by_code["4413000000"]["candidates"]
        if row.get("status") == "candidate"
    }
    assert parent_cheonan_providers.isdisjoint(
        {
            "MUNI_WWW_CHEONANLIFEEDU_ORG_41183F3B",
            "MUNI_WWW_CHEONAN_GO_KR_C97CA6FD",
            "MUNI_WWW_CHEONAN_GO_KR_EA8D366B",
            "MUNI_WWW_XN_2Z1BR4K89DEOA28DJVFZVASSQ98BDZK_KR_81F",
        }
    )


def test_reviewed_mixed_catalogues_are_configured_for_both_ops_tabs() -> None:
    expected = {
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
    }
    targets = region_collection._scope_target_rows()
    by_provider = {
        provider: [
            target
            for target in targets
            if str(target.get("provider") or "").strip().upper() == provider
        ]
        for provider in expected
    }

    for provider, rows in by_provider.items():
        assert rows, provider
        assert any(
            region_collection._target_scopes(row)
            == frozenset({"education", "experience"})
            for row in rows
        ), provider

    seongdong = next(
        target
        for target in targets
        if target.get("provider") == "MUNI_DOKSEODANG_SD_GO_KR_A8C20229"
    )
    assert region_collection._target_scopes(seongdong) == frozenset({"education"})

    hdream = next(
        target
        for target in targets
        if target.get("provider") == "MUNI_WWW_HDREAM_OR_KR_064EE411"
    )
    assert region_collection._target_scopes(hdream) == frozenset({"experience"})
