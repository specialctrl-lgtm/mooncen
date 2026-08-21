from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
MUNICIPALITY_CODE = "1285000000"
PARSER = "wando_education_complete_pages+sentinel+current_detail"
EXPECTED = {
    "MUNI_WWW_WANDO_GO_KR_AFCA6FD7": {
        "source_total": 81,
        "source_pages": 6,
        "pages": 7,
        "source_branch_count": 1,
        "latest_end_date": "2025-07-20",
        "ownership_scope": "wando_official_lifelong_m490_current_future",
    },
    "MUNI_WWW_WANDO_GO_KR_64D0194B": {
        "source_total": 35,
        "source_pages": 3,
        "pages": 4,
        "source_branch_count": 6,
        "latest_end_date": "2025-12-31",
        "ownership_scope": "wando_official_literacy_m502_current_future",
    },
}


def load_yaml(relative_path: str) -> dict:
    return yaml.safe_load((ROOT / relative_path).read_text(encoding="utf-8"))


def by_provider(rows: list[dict]) -> dict[str, dict]:
    return {str(row.get("provider") or ""): row for row in rows}


def test_wando_empty_snapshots_are_complete_distinct_education_owners() -> None:
    targets = by_provider(
        load_yaml("config/crawl_targets/municipal_integrated_reservation.yaml")["targets"]
    )
    operational = by_provider(
        load_yaml("config/municipal_integrated_reservation_operational.yaml")["entries"]
    )

    assert set(EXPECTED).issubset(targets)
    assert set(EXPECTED).issubset(operational)
    scopes: set[str] = set()
    for provider, expected in EXPECTED.items():
        target = targets[provider]
        assert target["crawler_status"] == "no_current_data"
        assert target["collection_type"] == PARSER
        assert target["collection_category"] == "공공예약"
        assert target["domain_category"] == "교육·강좌"
        assert target["service_group"] == "공공강좌"
        assert target["service_group_policy"] == "locked"
        assert target["full_snapshot_required"] is True
        assert target["municipality_code"] == MUNICIPALITY_CODE
        assert target["ownership_scope"] == expected["ownership_scope"]
        scopes.add(target["ownership_scope"])

        quality = target["last_quality"]
        for key in (
            "source_total",
            "source_pages",
            "pages",
            "source_branch_count",
            "latest_end_date",
        ):
            assert quality[key] == expected[key]
        assert quality["current_rows"] == 0
        assert quality["duplicate_count"] == 0
        assert quality["cross_source_semantic_duplicate_count"] == 0
        assert quality["snapshot_complete"] is True
        assert quality["no_current_data"] is True

        entry = operational[provider]
        assert entry["validation_outcome"] == "no_current_data"
        assert entry["row_count"] == 0
        assert entry["no_current_data"] is True
        assert entry["parser"] == PARSER
        assert entry["ownership_scope"] == expected["ownership_scope"]

    assert len(scopes) == len(EXPECTED)


def test_wando_coverage_and_live_evidence_promote_both_nonduplicate_sources() -> None:
    coverage = load_yaml("config/municipal_integrated_reservation_coverage.yaml")
    municipality = next(
        row for row in coverage["municipalities"] if row["code"] == MUNICIPALITY_CODE
    )

    assert municipality["status"] == "promoted"
    assert set(municipality["owner_providers"]) == set(EXPECTED)
    assert set(municipality["promoted_providers"]) == set(EXPECTED)
    operational_evidence = {
        row["provider"]: row
        for row in municipality["evidence"]
        if row.get("kind") == "operational_allowlist"
    }
    for provider, expected in EXPECTED.items():
        assert operational_evidence[provider]["row_count"] == 0
        assert operational_evidence[provider]["no_current_data"] is True
        assert operational_evidence[provider]["parser"] == PARSER

    registry = load_yaml("config/generated_yaml_crawler_registry.yaml")
    registry_providers = {row["provider"] for row in registry["targets"]}
    assert set(EXPECTED).isdisjoint(registry_providers)
