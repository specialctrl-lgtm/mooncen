from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
LIFELONG = "MUNI_WWW_NAJU_GO_KR_406D58D1"
GONGIK = "MUNI_WWW_NAJU_GO_KR_DE1B1AE9"
DUPLICATE = "MUNI_WWW_NAJU_GO_KR_D8842639"
LIFELONG_CANDIDATE = "MUNI_IR_BD30CADFB76E"
GONGIK_CANDIDATE = "MUNI_IR_CA1623E92BC1"
MUNICIPALITY_CODE = "1217000000"
LIFELONG_SCOPE = "naju_official_lifelong_all_institutions_current_future"
GONGIK_SCOPE = "naju_official_gongik_education_current_future"


def load_yaml(relative_path: str) -> dict:
    return yaml.safe_load((ROOT / relative_path).read_text(encoding="utf-8"))


def by_provider(rows: list[dict]) -> dict[str, dict]:
    return {str(row.get("provider") or ""): row for row in rows}


def test_naju_canonical_targets_and_duplicate_partition_contract() -> None:
    municipal = by_provider(
        load_yaml("config/crawl_targets/municipal_integrated_reservation.yaml")["targets"]
    )
    lifelong = by_provider(load_yaml("config/crawl_targets/lifelong_learning.yaml")["targets"])

    canonical = municipal[LIFELONG]
    assert canonical["crawler_status"] == "ready"
    assert canonical["full_snapshot_required"] is True
    assert canonical["ownership_scope"] == LIFELONG_SCOPE
    assert len(canonical["ownership_aliases"]) == 6
    quality = canonical["last_quality"]
    assert quality["source_total"] == 1682
    assert quality["source_rows"] == 1678
    assert quality["hidden_row_count"] == 4
    assert quality["pages"] == 114
    assert quality["current_count"] == quality["detail_pages"] == 55
    assert sum(quality["branch_counts"].values()) == 55
    assert quality["duplicate_count"] == 0
    assert quality["snapshot_complete"] is True

    gongik = lifelong[GONGIK]
    assert gongik["crawler_status"] == "no_current_data"
    assert gongik["full_snapshot_required"] is True
    assert gongik["ownership_scope"] == GONGIK_SCOPE
    assert gongik["last_quality"]["source_total"] == 25
    assert gongik["last_quality"]["pages"] == 4
    assert gongik["last_quality"]["snapshot_complete"] is True

    duplicate = lifelong[DUPLICATE]
    assert duplicate["crawler_status"] == f"duplicate_url:{LIFELONG}"
    assert duplicate["duplicate_of"] == LIFELONG


def test_naju_operational_coverage_review_and_registry_contract() -> None:
    operational = by_provider(
        load_yaml("config/municipal_integrated_reservation_operational.yaml")["entries"]
    )
    assert DUPLICATE not in operational
    assert operational[LIFELONG]["ownership_scope"] == LIFELONG_SCOPE
    assert operational[LIFELONG]["superseded_providers"] == [DUPLICATE]
    assert operational[GONGIK]["ownership_scope"] == GONGIK_SCOPE

    coverage = load_yaml("config/municipal_integrated_reservation_coverage.yaml")
    municipality = next(
        row for row in coverage["municipalities"] if row["code"] == MUNICIPALITY_CODE
    )
    assert municipality["status"] == "promoted"
    assert municipality["owner_providers"] == [LIFELONG, GONGIK]
    assert municipality["promoted_providers"] == [LIFELONG, GONGIK]
    assert {LIFELONG_CANDIDATE, GONGIK_CANDIDATE}.issubset(
        municipality["review_candidate_ids"]
    )

    review = load_yaml("config/municipal_integrated_reservation_promotion_review.yaml")
    candidates = {row["candidate_id"]: row for row in review["candidates"]}
    assert candidates[LIFELONG_CANDIDATE]["status"] == "promoted"
    assert candidates[LIFELONG_CANDIDATE]["live_validation"]["row_count"] == 55
    assert candidates[LIFELONG_CANDIDATE]["live_validation"]["pages"] == 114
    assert candidates[GONGIK_CANDIDATE]["status"] == "promoted"
    assert candidates[GONGIK_CANDIDATE]["live_validation"]["no_current_data"] is True

    registry = load_yaml("config/generated_yaml_crawler_registry.yaml")
    providers = {row["provider"] for row in registry["targets"]}
    assert {LIFELONG, GONGIK, DUPLICATE}.isdisjoint(providers)
