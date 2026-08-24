from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = "MUNI_LIFELONG_MOKPO_GO_KR_0E89BA53"
DUPLICATE_ROOT = "MUNI_LIFELONG_MOKPO_GO_KR_CBF33D32"
CANDIDATE_ID = "MUNI_IR_3A1DC6680C5F"
MUNICIPALITY_CODE = "1211000000"
PARSER = "mokpo_lifelong_complete_pages+sentinel+current_detail"
SCOPE = "mokpo_official_lifelong_sub222_all_current_future"


def load_yaml(relative_path: str) -> dict:
    return yaml.safe_load((ROOT / relative_path).read_text(encoding="utf-8"))


def by_provider(rows: list[dict]) -> dict[str, dict]:
    return {str(row.get("provider") or ""): row for row in rows}


def test_mokpo_canonical_target_is_complete_and_root_is_disabled_duplicate() -> None:
    document = load_yaml("config/crawl_targets/lifelong_learning.yaml")
    targets = by_provider(document["targets"])
    canonical = targets[CANONICAL]
    duplicate = targets[DUPLICATE_ROOT]

    assert canonical["crawler_status"] == "ready"
    assert canonical["collection_type"] == PARSER
    assert canonical["collection_category"] == "공공예약"
    assert canonical["domain_category"] == "교육·강좌"
    assert canonical["source_group"] == "municipal_reservation"
    assert canonical["service_group"] == "공공강좌"
    assert canonical["service_group_policy"] == "locked"
    assert canonical["full_snapshot_required"] is True
    assert canonical["ownership_scope"] == SCOPE
    assert canonical["ownership_aliases"] == [
        "https://lifelong.mokpo.go.kr/",
        "https://lifelong.mokpo.go.kr/lecture/lecture_list_program.php?me_id=sub220",
    ]
    quality = canonical["last_quality"]
    assert quality["source_total"] == 94
    assert quality["source_pages"] == 7
    assert quality["pages"] == 8
    assert quality["current_rows"] == 94
    assert quality["detail_pages"] == 94
    assert quality["branch_count"] == 23
    assert quality["branches"] == [
        "대성동",
        "동명동",
        "만호동",
        "목원동",
        "부주동",
        "부흥동",
        "북항동",
        "산정동",
        "삼학동",
        "삼향동",
        "상동",
        "신흥동",
        "연동",
        "연산동",
        "옥암동",
        "용당1동",
        "용당2동",
        "용해동",
        "원산동",
        "유달동",
        "이로동",
        "죽교동",
        "하당동",
    ]
    assert quality["online_application_count"] == 0
    assert quality["duplicate_count"] == 0
    assert quality["semantic_duplicate_count"] == 0
    assert quality["snapshot_complete"] is True

    assert duplicate["collection_type"] == "duplicate"
    assert duplicate["crawler_status"] == f"duplicate_url:{CANONICAL}"
    assert duplicate["duplicate_of"] == CANONICAL
    assert duplicate["superseded_by"] == CANONICAL
    assert duplicate["last_quality"]["error_kind"] == f"duplicate_of:{CANONICAL}"


def test_mokpo_operational_and_coverage_contract_have_one_owner() -> None:
    operational = by_provider(
        load_yaml("config/municipal_integrated_reservation_operational.yaml")["entries"]
    )
    entry = operational[CANONICAL]
    assert DUPLICATE_ROOT not in operational
    assert entry["action"] == "schedule_existing"
    assert entry["validation_outcome"] == "collected"
    assert entry["row_count"] == 94
    assert entry["parser"] == PARSER
    assert entry["ownership_scope"] == SCOPE

    coverage = load_yaml("config/municipal_integrated_reservation_coverage.yaml")
    municipality = next(
        row for row in coverage["municipalities"] if row["code"] == MUNICIPALITY_CODE
    )
    assert municipality["status"] == "promoted"
    assert municipality["owner_providers"] == [CANONICAL]
    assert municipality["promoted_providers"] == [CANONICAL]
    assert CANDIDATE_ID in municipality["review_candidate_ids"]
    live = next(
        row
        for row in municipality["evidence"]
        if row.get("kind") == "live_validation" and row.get("provider") == CANONICAL
    )
    assert live["row_count"] == 94
    assert live["pages"] == 8
    assert live["detail_pages"] == 94
    duplicate_exclusion = next(
        row
        for row in municipality["evidence"]
        if row.get("candidate_id") == "MUNI_IR_ADF404AF0327"
    )
    assert duplicate_exclusion["exclusion_reason"] == "duplicate_alias_of_canonical_sub222"

    review = load_yaml("config/municipal_integrated_reservation_promotion_review.yaml")
    candidate = next(
        row for row in review["candidates"] if row["candidate_id"] == CANDIDATE_ID
    )
    assert candidate["status"] == "promoted"
    assert candidate["recommended_action"] == "schedule_existing"
    assert candidate["existing_owner_providers"] == [CANONICAL]

    registry = load_yaml("config/generated_yaml_crawler_registry.yaml")
    registry_providers = {row["provider"] for row in registry["targets"]}
    assert CANONICAL not in registry_providers
    assert DUPLICATE_ROOT not in registry_providers
