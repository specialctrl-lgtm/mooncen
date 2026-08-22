from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = "MUNI_WWW_MICHUHOL_GO_KR_06925037"
DUPLICATE = "MUNI_WWW_MICHUHOL_GO_KR_29D0C0F5"
STATIC_INFO = "MUNI_WWW_MICHUHOL_GO_KR_6AA923E4"
CANDIDATE_ID = "MUNI_IR_5C577FCA351E"
MUNICIPALITY_CODE = "2817700000"
PARSER = "michuhol_complete_pages+sentinel+current_detail"
SCOPE = "michuhol_official_unfiltered_all_education_current_future"


def load_yaml(relative_path: str) -> dict:
    return yaml.safe_load((ROOT / relative_path).read_text(encoding="utf-8"))


def by_provider(rows: list[dict]) -> dict[str, dict]:
    return {str(row.get("provider") or ""): row for row in rows}


def test_michuhol_unfiltered_target_is_the_only_active_owner() -> None:
    public = by_provider(load_yaml("config/crawl_targets/public_reservation.yaml")["targets"])
    lifelong = by_provider(load_yaml("config/crawl_targets/lifelong_learning.yaml")["targets"])
    canonical = public[CANONICAL]
    duplicate = public[DUPLICATE]

    assert canonical["crawler_status"] == "ready"
    assert canonical["collection_type"] == PARSER
    assert canonical["domain_category"] == "교육·강좌"
    assert canonical["source_group"] == "municipal_reservation"
    assert canonical["service_group"] == "공공강좌"
    assert canonical["service_group_policy"] == "locked"
    assert canonical["full_snapshot_required"] is True
    assert canonical["ownership_scope"] == SCOPE
    quality = canonical["last_quality"]
    assert quality["source_total"] == 1594
    assert quality["source_pages"] == 54
    assert quality["pages"] == 55
    assert quality["current_rows"] == 219
    assert quality["detail_pages"] == 219
    assert quality["branch_count"] == 22
    assert sum(quality["application_mode_counts"].values()) == 219
    assert quality["duplicate_count"] == 0
    assert quality["semantic_duplicate_count"] == 0
    assert quality["snapshot_complete"] is True

    assert duplicate["collection_type"] == "duplicate"
    assert duplicate["duplicate_of"] == CANONICAL
    assert duplicate["crawler_status"] == f"duplicate_url:{CANONICAL}"
    assert lifelong[STATIC_INFO]["crawler_status"] == "excluded_url_shape"


def test_michuhol_operational_coverage_and_registry_contract() -> None:
    operational = by_provider(
        load_yaml("config/municipal_integrated_reservation_operational.yaml")["entries"]
    )
    assert DUPLICATE not in operational
    assert STATIC_INFO not in operational
    entry = operational[CANONICAL]
    assert entry["row_count"] == 219
    assert entry["parser"] == PARSER
    assert entry["ownership_scope"] == SCOPE
    assert entry["superseded_providers"] == [DUPLICATE]

    coverage = load_yaml("config/municipal_integrated_reservation_coverage.yaml")
    municipality = next(
        row for row in coverage["municipalities"] if row["code"] == MUNICIPALITY_CODE
    )
    assert municipality["status"] == "promoted"
    assert municipality["owner_providers"] == [CANONICAL]
    assert municipality["promoted_providers"] == [CANONICAL]
    assert CANDIDATE_ID in municipality["review_candidate_ids"]

    review = load_yaml("config/municipal_integrated_reservation_promotion_review.yaml")
    candidate = next(
        row for row in review["candidates"] if row["candidate_id"] == CANDIDATE_ID
    )
    assert candidate["status"] == "promoted"
    assert candidate["live_validation"]["row_count"] == 219
    assert candidate["live_validation"]["pages"] == 55
    assert candidate["live_validation"]["detail_pages"] == 219

    registry = load_yaml("config/generated_yaml_crawler_registry.yaml")
    providers = {row["provider"] for row in registry["targets"]}
    assert {CANONICAL, DUPLICATE, STATIC_INFO}.isdisjoint(providers)
