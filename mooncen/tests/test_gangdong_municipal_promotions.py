from __future__ import annotations

from pathlib import Path

import yaml

from Crawler import Crawler_GeneratedYamlTargets as generated
from Crawler import municipal_gangdong as gangdong


ROOT = Path(__file__).resolve().parents[1]

EXPECTED = {
    gangdong.GANGDONG_RESERVE_PROVIDER: {
        "url": gangdong.GANGDONG_RESERVE_URL,
        "rows": 57,
        "max_pages": 100,
        "detail_limit": 200,
    },
    gangdong.GANGDONG_HEALTH_PROVIDER: {
        "url": gangdong.GANGDONG_HEALTH_URL,
        "rows": 4,
        "max_pages": 20,
        "detail_limit": 100,
    },
    gangdong.GANGDONG_LLL_PROVIDER: {
        "url": gangdong.GANGDONG_LLL_URL,
        "rows": 2,
        "max_pages": 20,
        "detail_limit": 100,
    },
    gangdong.GANGDONG_LIBRARY_PROVIDER: {
        "url": gangdong.GANGDONG_LIBRARY_URL,
        "rows": 143,
        "max_pages": 300,
        "detail_limit": 200,
    },
    gangdong.GANGDONG_50PLUS_PROVIDER: {
        "url": gangdong.GANGDONG_50PLUS_URL,
        "rows": 25,
        "max_pages": 30,
        "detail_limit": 100,
    },
    gangdong.GANGDONG_SLC_PROVIDER: {
        "url": gangdong.GANGDONG_SLC_URL,
        "rows": 52,
        "max_pages": 20,
        "detail_limit": 100,
    },
    gangdong.GANGDONG_JUMIN_PROVIDER: {
        "url": gangdong.GANGDONG_JUMIN_URL,
        "rows": 287,
        "max_pages": 100,
        "detail_limit": 500,
    },
}


def _yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _all_targets() -> list[dict]:
    rows: list[dict] = []
    for path in sorted((ROOT / "config" / "crawl_targets").glob("*.yaml")):
        if path.name == "index.yaml":
            continue
        rows.extend(_yaml(path).get("targets") or [])
    return rows


def _arg_value(arguments: tuple[str, ...], flag: str) -> int:
    return int(arguments[arguments.index(flag) + 1])


def test_all_gangdong_sources_are_unique_complete_locked_targets() -> None:
    by_provider: dict[str, list[dict]] = {}
    for row in _all_targets():
        by_provider.setdefault(str(row.get("provider") or ""), []).append(row)

    for provider, expected in EXPECTED.items():
        assert len(by_provider.get(provider, [])) == 1
        row = by_provider[provider][0]
        assert row["url"] == expected["url"]
        assert row["crawler_status"] == "ready"
        assert row["collection_category"] == "공공예약"
        assert row["domain_category"] == "교육·강좌"
        assert row["source_group"] == "municipal_reservation"
        assert row["service_group"] == "공공강좌"
        assert row["service_group_policy"] == "locked"
        assert row["full_snapshot_required"] is True
        assert row["ownership_scope"]
        assert row["last_quality"]["collected"] == expected["rows"]
        assert row["last_quality"]["snapshot_complete"] is True


def test_all_gangdong_sources_are_live_validated_and_allowlisted() -> None:
    entries = _yaml(ROOT / "config" / "municipal_integrated_reservation_operational.yaml")["entries"]
    by_provider = {row["provider"]: row for row in entries if row.get("provider") in EXPECTED}

    assert set(by_provider) == set(EXPECTED)
    for provider, expected in EXPECTED.items():
        row = by_provider[provider]
        assert row["target_url"] == expected["url"]
        assert row["validation_outcome"] == "collected"
        assert row["row_count"] == expected["rows"]
        assert row["no_current_data"] is False
        assert row["ownership_scope"]
        assert [municipality["code"] for municipality in row["municipalities"]] == ["1174000000"]


def test_all_gangdong_sources_have_complete_snapshot_arguments() -> None:
    for provider, expected in EXPECTED.items():
        arguments = generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[provider]
        assert "--save-db" in arguments
        assert "--mark-stale" in arguments
        assert "--allow-partial-save" not in arguments
        assert _arg_value(arguments, "--per-target-limit") == 0
        assert _arg_value(arguments, "--max-pages") == expected["max_pages"]
        assert _arg_value(arguments, "--detail-limit") == expected["detail_limit"]


def test_historical_single_detail_and_information_roots_are_disabled() -> None:
    rows = {row["provider"]: row for row in _all_targets()}
    canonical = gangdong.GANGDONG_LLL_PROVIDER

    assert rows["MUNI_LLL_GANGDONG_GO_KR_CBC06DB3"]["crawler_status"] == f"duplicate_url:{canonical}"
    assert rows["MUNI_LLL_GANGDONG_GO_KR_CBC06DB3"]["duplicate_of"] == canonical
    assert rows["MUNI_LLL_GANGDONG_GO_KR_7E55B142"]["crawler_status"] == f"duplicate_url:{canonical}"


def test_gangdong_manual_exclusions_preserve_fail_closed_decisions() -> None:
    document = _yaml(ROOT / "config" / "municipal_integrated_reservation_overrides.yaml")
    municipality = next(row for row in document["municipalities"] if row["code"] == "1174000000")
    exclusions = {
        row["url"]: row["exclusion_reason"]
        for row in municipality["candidates"]
        if row.get("status") == "excluded"
    }

    assert exclusions["https://sugang.igangdong.or.kr/"] == "missing_explicit_education_period_prelogin"
    assert exclusions["https://www.igangdong.or.kr/"] == "indirect_reservation_wrapper"
    assert exclusions[
        "https://lll.gangdong.go.kr/program/ProgramClassroomView.do?menucode=84&gn_seq=592"
    ] == "included_subcategory_of_canonical"
