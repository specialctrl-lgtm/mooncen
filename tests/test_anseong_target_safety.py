from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = "MUNI_WWW_ANSEONG_GO_KR_5751E139"
DUPLICATE = "MUNI_WWW_ANSEONG_GO_KR_D789171B"
INFO = "MUNI_WWW_ANSEONG_GO_KR_DED7D163"
CANDIDATE_ID = "MUNI_IR_784E7698A47D"
MUNICIPALITY_CODE = "4155000000"
PARSER = (
    "anseong_all_institutions_personal+group_complete_pages+sentinel+"
    "current_detail+application_round_dedupe"
)
SCOPE = "anseong_official_all_institutions_personal_and_group_current_future"


def load_yaml(relative_path: str) -> dict:
    return yaml.safe_load((ROOT / relative_path).read_text(encoding="utf-8"))


def by_provider(rows: list[dict]) -> dict[str, dict]:
    return {str(row.get("provider") or ""): row for row in rows}


def test_anseong_unfiltered_personal_and_group_target_is_canonical() -> None:
    targets = by_provider(load_yaml("config/crawl_targets/lifelong_learning.yaml")["targets"])
    canonical = targets[CANONICAL]
    assert canonical["crawler_status"] == "ready"
    assert canonical["collection_type"] == PARSER
    assert canonical["domain_category"] == "교육·강좌"
    assert canonical["source_group"] == "municipal_reservation"
    assert canonical["service_group"] == "공공강좌"
    assert canonical["full_snapshot_required"] is True
    assert canonical["ownership_scope"] == SCOPE
    quality = canonical["last_quality"]
    assert quality["source_total"] == 2936
    assert quality["personal_source_total"] == 2933
    assert quality["group_source_total"] == 3
    assert quality["pages"] == 297
    assert quality["current_source_rows"] == 296
    assert quality["detail_pages"] == 296
    assert quality["duplicate_application_rounds"] == 46
    assert quality["current_rows"] == 250
    assert sum(quality["branch_counts"].values()) == 250
    assert quality["duplicate_count"] == 0
    assert quality["snapshot_complete"] is True

    assert targets[DUPLICATE]["crawler_status"] == f"duplicate_url:{CANONICAL}"
    assert targets[DUPLICATE]["duplicate_of"] == CANONICAL
    assert targets[INFO]["crawler_status"] == "excluded_url_shape"


def test_anseong_operational_coverage_and_registry_contract() -> None:
    operational = by_provider(
        load_yaml("config/municipal_integrated_reservation_operational.yaml")["entries"]
    )
    assert DUPLICATE not in operational
    assert INFO not in operational
    entry = operational[CANONICAL]
    assert entry["row_count"] == 250
    assert entry["parser"] == PARSER
    assert entry["ownership_scope"] == SCOPE
    assert entry["superseded_providers"] == [DUPLICATE]

    coverage = load_yaml("config/municipal_integrated_reservation_coverage.yaml")
    municipality = next(
        row for row in coverage["municipalities"] if row["code"] == MUNICIPALITY_CODE
    )
    assert municipality["status"] == "promoted"
    owner_providers = set(municipality["owner_providers"])
    assert CANONICAL in owner_providers
    assert {DUPLICATE, INFO}.isdisjoint(owner_providers)
    assert set(municipality["promoted_providers"]) == owner_providers
    assert municipality["review_candidate_ids"] == [CANDIDATE_ID]

    review = load_yaml("config/municipal_integrated_reservation_promotion_review.yaml")
    candidate = next(
        row for row in review["candidates"] if row["candidate_id"] == CANDIDATE_ID
    )
    assert candidate["status"] == "promoted"
    assert candidate["live_validation"]["row_count"] == 250
    assert candidate["live_validation"]["pages"] == 297
    assert candidate["live_validation"]["detail_pages"] == 296

    registry = load_yaml("config/generated_yaml_crawler_registry.yaml")
    providers = {row["provider"] for row in registry["targets"]}
    assert {CANONICAL, DUPLICATE, INFO}.isdisjoint(providers)
