from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
PROVIDER = "MUNI_WWW_YC_GO_KR_54558363"
CANDIDATE_ID = "MUNI_IR_F88A466ADAFB"
MUNICIPALITY_CODE = "4723000000"


def load_yaml(relative_path: str) -> dict:
    return yaml.safe_load((ROOT / relative_path).read_text(encoding="utf-8"))


def find_row(rows: list[dict], key: str, value: str) -> dict:
    return next(row for row in rows if str(row.get(key) or "") == value)


def test_yeongcheon_complete_target_is_enabled_and_operationally_promoted() -> None:
    target_document = load_yaml("config/crawl_targets/lifelong_learning.yaml")
    target = find_row(target_document["targets"], "provider", PROVIDER)

    assert target["crawler_status"] == "ready"
    assert target["collection_type"] == "yeongcheon_advertised_pages_complete_current_future+detail"
    assert target["service_group"] == "공공강좌"
    assert target["service_group_policy"] == "locked"
    assert target["full_snapshot_required"] is True
    assert target["ownership_scope"] == "yeongcheon_official_education_all_advertised_pages_current_future"
    assert target["last_quality"]["collected"] == 54
    assert target["last_quality"]["source_rows"] == 195
    assert target["last_quality"]["detail_pages"] == 54
    assert target["last_quality"]["branch_count"] == 10
    assert target["last_quality"]["snapshot_complete"] is True
    assert target["last_quality"]["error_kind"] == ""
    notes = " ".join(target["crawler_notes"])
    for marker in ("1,115", "195", "54", "113", "부분 행"):
        assert marker in notes

    registry = load_yaml("config/generated_yaml_crawler_registry.yaml")
    assert PROVIDER not in {row["provider"] for row in registry["targets"]}
    assert not (ROOT / "Crawler/generated_yaml" / f"{PROVIDER}.py").exists()

    production = load_yaml("config/production_crawler_providers.yaml")
    assert PROVIDER not in set(production.get("providers") or [])

    operational = load_yaml("config/municipal_integrated_reservation_operational.yaml")
    operational_row = find_row(operational["entries"], "provider", PROVIDER)
    assert operational_row["action"] == "schedule_existing"
    assert operational_row["validation_outcome"] == "collected"
    assert operational_row["row_count"] == 54
    assert operational_row["ownership_scope"] == target["ownership_scope"]

    review = load_yaml("config/municipal_integrated_reservation_promotion_review.yaml")
    candidate = find_row(review["candidates"], "candidate_id", CANDIDATE_ID)
    assert candidate["status"] == "promoted"
    assert candidate["recommended_action"] == "schedule_existing"
    assert candidate["existing_owner_providers"] == [PROVIDER]
    assert candidate["disabled_owner_providers"] == []
    assert "1,115" in candidate["official_evidence_note"]
    assert "113" in candidate["official_evidence_note"]

    coverage = load_yaml("config/municipal_integrated_reservation_coverage.yaml")
    municipality = find_row(coverage["municipalities"], "code", MUNICIPALITY_CODE)
    assert municipality["status"] == "promoted"
    assert CANDIDATE_ID in municipality["review_candidate_ids"]
    assert PROVIDER in municipality["owner_providers"]
    assert set(municipality["promoted_providers"]) == set(
        municipality["owner_providers"]
    )
    validation = next(
        row
        for row in municipality["evidence"]
        if row.get("kind") == "operational_allowlist" and row.get("provider") == PROVIDER
    )
    assert validation["row_count"] == 54
    assert validation["parser"] == "yeongcheon_advertised_pages_complete_current_future+detail"

    index = load_yaml("config/crawl_targets/index.yaml")
    assert int(index["summary"]["by_status"]["ready"]) >= 1
