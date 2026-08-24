from __future__ import annotations

from pathlib import Path

import yaml

from Crawler import Crawler_GeneratedYamlTargets as generated
from Crawler import (
    municipal_dongjak,
    municipal_gangnam,
    municipal_guro,
    municipal_gwanak,
    municipal_seocho,
    municipal_seoul_junggu,
    municipal_seongdong,
    municipal_songpa,
    municipal_suwon_reservation,
)


ROOT = Path(__file__).resolve().parents[1]

EXPECTED = {
    municipal_seoul_junggu.SEOUL_JUNGGU_EDUCATION_PROVIDER: {
        "url": municipal_seoul_junggu.SEOUL_JUNGGU_EDUCATION_URL,
        "rows": 135,
        "max_pages": 20,
        "detail_limit": 500,
    },
    municipal_dongjak.DONGJAK_EDUCATION_PROVIDER: {
        "url": municipal_dongjak.DONGJAK_EDUCATION_URL,
        "rows": 91,
        "max_pages": 100,
        "detail_limit": 200,
    },
    municipal_gwanak.GWANAK_EDUCATION_PROVIDER: {
        "url": municipal_gwanak.GWANAK_EDUCATION_URL,
        "rows": 286,
        "max_pages": 100,
        "detail_limit": 400,
    },
    municipal_seocho.SEOCHO_EDUCATION_PROVIDER: {
        "url": municipal_seocho.SEOCHO_EDUCATION_URL,
        "rows": 716,
        "max_pages": 100,
        "detail_limit": 1000,
    },
    municipal_gangnam.GANGNAM_EDUCATION_PROVIDER: {
        "url": municipal_gangnam.GANGNAM_EDUCATION_URL,
        "rows": 4,
        "max_pages": 20,
        "detail_limit": 500,
    },
    municipal_songpa.SONGPA_EDUCATION_PROVIDER: {
        "url": municipal_songpa.SONGPA_EDUCATION_URL,
        "rows": 309,
        "max_pages": 100,
        "detail_limit": 1000,
    },
    municipal_suwon_reservation.SUWON_PROVIDER: {
        "url": municipal_suwon_reservation.SUWON_URL,
        "rows": 166,
        "operational_rows": 166,
        "municipality_count": 5,
        "max_pages": 10,
        "detail_limit": 300,
    },
}

EXISTING_COMPLETE = {
    municipal_guro.GURO_PROVIDER: {
        "url": municipal_guro.GURO_URL,
        "rows": 140,
        "max_pages": 10,
        "detail_limit": 300,
        "alias": municipal_guro.GURO_RESIDENT_URL,
    },
    municipal_seongdong.SEONGDONG_DOKSEODANG_PROVIDER: {
        "url": municipal_seongdong.SEONGDONG_DOKSEODANG_URL,
        "rows": 1286,
        "target_count": 2,
        "experience_url": municipal_seongdong.SEONGDONG_EXPERIENCE_URL,
        "experience_rows": 17,
        "max_pages": 250,
        "detail_limit": 2000,
        "alias": "https://dokseodang.sd.go.kr/product/list.php?ca_id=10",
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


def test_validated_seoul_sources_are_full_snapshot_locked_targets() -> None:
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


def test_promoted_sources_are_exactly_allowlisted_with_live_row_evidence() -> None:
    document = _yaml(ROOT / "config" / "municipal_integrated_reservation_operational.yaml")
    by_provider = {
        row["provider"]: row
        for row in document["entries"]
        if row.get("provider") in EXPECTED
    }
    assert set(by_provider) == set(EXPECTED)
    for provider, expected in EXPECTED.items():
        row = by_provider[provider]
        assert row["target_url"] == expected["url"]
        assert row["validation_outcome"] == "collected"
        assert row["row_count"] == expected.get("operational_rows", expected["rows"])
        assert row["no_current_data"] is False
        assert len(row["municipalities"]) == expected.get("municipality_count", 1)


def test_promoted_sources_have_uncapped_non_partial_execution_arguments() -> None:
    for provider, expected in EXPECTED.items():
        arguments = generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[provider]
        assert "--save-db" in arguments
        assert "--mark-stale" in arguments
        assert "--allow-partial-save" not in arguments
        assert _arg_value(arguments, "--per-target-limit") == 0
        assert _arg_value(arguments, "--max-pages") == expected["max_pages"]
        assert _arg_value(arguments, "--detail-limit") == expected["detail_limit"]


def test_existing_owners_were_upgraded_to_uncapped_complete_targets() -> None:
    all_rows = _all_targets()
    for provider, expected in EXISTING_COMPLETE.items():
        rows = [row for row in all_rows if row.get("provider") == provider]
        assert len(rows) == expected.get("target_count", 1)
        row = next(row for row in rows if row["url"] == expected["url"])
        assert row["url"] == expected["url"]
        assert row["crawler_status"] == "ready"
        assert row["collection_category"] == "공공예약"
        assert row["domain_category"] == "교육·강좌"
        assert row["source_group"] == "municipal_reservation"
        assert row["service_group"] == "공공강좌"
        assert row["service_group_policy"] == "locked"
        assert row["full_snapshot_required"] is True
        assert row["last_quality"]["collected"] == expected["rows"]
        assert row["last_quality"]["snapshot_complete"] is True
        assert expected["alias"] in row["ownership_aliases"]

        if experience_url := expected.get("experience_url"):
            experience = next(row for row in rows if row["url"] == experience_url)
            assert experience["crawler_status"] == "ready"
            assert experience["collection_category"] == "공공예약"
            assert experience["domain_category"] == "체험·견학"
            assert experience["source_group"] == "municipal_reservation"
            assert experience["service_group"] == "체험"
            assert experience["service_group_policy"] == "locked"
            assert experience["full_snapshot_required"] is True
            assert experience["last_quality"]["collected"] == expected["experience_rows"]
            assert experience["last_quality"]["snapshot_complete"] is True

        arguments = generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[provider]
        assert "--save-db" in arguments
        assert "--mark-stale" in arguments
        assert "--allow-partial-save" not in arguments
        assert _arg_value(arguments, "--per-target-limit") == 0
        assert _arg_value(arguments, "--max-pages") == expected["max_pages"]
        assert _arg_value(arguments, "--detail-limit") == expected["detail_limit"]


def test_replaced_generic_sources_are_not_working_targets() -> None:
    rows = {row["provider"]: row for row in _all_targets()}
    assert rows["MUNI_WWW_DONGJAK_GO_KR_ECE31AE9"]["crawler_status"] == "deprecated"
    assert rows["MUNI_WWW_GANGNAM_GO_KR_6A9410A7"]["crawler_status"] == "deprecated"
    assert rows["MUNI_WWW_SEOCHO_GO_KR_040941A0"]["crawler_status"].startswith(
        "duplicate_url:"
    )
