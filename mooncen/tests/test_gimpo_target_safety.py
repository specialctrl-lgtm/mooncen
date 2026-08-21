from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = "MUNI_GIMPO_GSEEK_KR_6685FD9C"
ARCHIVE = "MUNI_WWW_GIMPO_GO_KR_83984E5D"
SUBSET = "MUNI_WWW_GIMPO_GO_KR_550D23F1"
SHELL = "MUNI_WWW_GIMPO_GO_KR_6341E241"
CANDIDATE = "MUNI_IR_52E1EF958BC0"
MUNICIPALITY_CODE = "4157000000"
SCOPE = "gimpo_official_branded_gseek_G000003_current_future"


def load_yaml(relative_path: str) -> dict:
    return yaml.safe_load((ROOT / relative_path).read_text(encoding="utf-8"))


def by_provider(rows: list[dict]) -> dict[str, dict]:
    return {str(row.get("provider") or ""): row for row in rows}


def test_gimpo_branded_gseek_is_canonical_and_legacy_is_excluded() -> None:
    municipal = by_provider(
        load_yaml("config/crawl_targets/municipal_integrated_reservation.yaml")["targets"]
    )
    public = by_provider(load_yaml("config/crawl_targets/public_reservation.yaml")["targets"])

    canonical = municipal[CANONICAL]
    assert canonical["crawler_status"] == "ready"
    assert canonical["domain_category"] == "교육·강좌"
    assert canonical["full_snapshot_required"] is True
    assert canonical["ownership_scope"] == SCOPE
    quality = canonical["last_quality"]
    assert quality["source_total"] == 511
    assert quality["pages"] == 58
    assert quality["current_count"] == quality["detail_pages"] == 329
    assert quality["returned_count"] == 328
    assert quality["duplicate_rounds_removed"] == 1
    assert sum(quality["branch_counts"].values()) == 328
    assert quality["branch_count"] == 16
    assert quality["snapshot_complete"] is True

    assert public[ARCHIVE]["crawler_status"] == "no_current_data"
    assert public[ARCHIVE]["last_quality"]["source_total"] == 1773
    assert public[ARCHIVE]["last_quality"]["current_count"] == 0
    assert public[SUBSET]["duplicate_of"] == ARCHIVE


def test_gimpo_operational_coverage_review_and_registry_contract() -> None:
    operational = by_provider(
        load_yaml("config/municipal_integrated_reservation_operational.yaml")["entries"]
    )
    assert ARCHIVE not in operational
    assert SUBSET not in operational
    entry = operational[CANONICAL]
    assert entry["row_count"] == 328
    assert entry["ownership_scope"] == SCOPE
    assert entry["superseded_providers"] == [ARCHIVE, SUBSET]

    coverage = load_yaml("config/municipal_integrated_reservation_coverage.yaml")
    municipality = next(
        row for row in coverage["municipalities"] if row["code"] == MUNICIPALITY_CODE
    )
    assert municipality["status"] == "promoted"
    owner_providers = set(municipality["owner_providers"])
    assert CANONICAL in owner_providers
    assert {ARCHIVE, SUBSET, SHELL}.isdisjoint(owner_providers)
    assert set(municipality["promoted_providers"]) == owner_providers
    assert CANDIDATE in municipality["review_candidate_ids"]

    review = load_yaml("config/municipal_integrated_reservation_promotion_review.yaml")
    candidate = next(row for row in review["candidates"] if row["candidate_id"] == CANDIDATE)
    assert candidate["status"] == "promoted"
    assert candidate["live_validation"]["row_count"] == 328
    assert candidate["live_validation"]["pages"] == 58
    assert candidate["live_validation"]["detail_pages"] == 329

    registry = load_yaml("config/generated_yaml_crawler_registry.yaml")
    providers = {row["provider"] for row in registry["targets"]}
    assert {CANONICAL, ARCHIVE, SUBSET, SHELL}.isdisjoint(providers)
