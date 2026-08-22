from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
PROVIDER = "MUNI_WWW_GP_GO_KR_FA65C3DB"
CANDIDATE_ID = "MUNI_IR_48B24D15F311"
MUNICIPALITY_CODE = "4182000000"


def load_yaml(relative_path: str) -> dict:
    return yaml.safe_load((ROOT / relative_path).read_text(encoding="utf-8"))


def test_gaepyeong_stale_program_guide_is_disabled_and_manually_excluded() -> None:
    targets = load_yaml("config/crawl_targets/lifelong_learning.yaml")["targets"]
    target = next(row for row in targets if row.get("provider") == PROVIDER)
    assert target["branch"] == "조종면 주민자치센터"
    assert target["crawler_status"] == "no_current_data"
    assert target["collection_type"] == "info_only"
    assert target["blocked_reason"] == "stale_information_only"
    assert "신청 URL" in target["notes"]

    registry_providers = {
        row["provider"] for row in load_yaml("config/generated_yaml_crawler_registry.yaml")["targets"]
    }
    assert PROVIDER not in registry_providers
    assert not (ROOT / "Crawler" / "generated_yaml" / f"{PROVIDER}.py").exists()

    coverage = next(
        row
        for row in load_yaml("config/municipal_integrated_reservation_coverage.yaml")["municipalities"]
        if row.get("code") == MUNICIPALITY_CODE
    )
    assert coverage["status"] == "promoted"
    assert coverage["eligible_candidate_count"] == 1
    assert "MUNI_WWW_GAPLIB_GO_KR_38AFB1BF" in coverage["promoted_providers"]
    assert PROVIDER not in coverage["promoted_providers"]
    assert set(coverage["promoted_providers"]) == set(coverage["owner_providers"])
    assert coverage["exclusion_reasons"]["stale_information_only"] == 1
    assert CANDIDATE_ID not in coverage["review_candidate_ids"]
    evidence = next(
        item
        for item in coverage["evidence"]
        if item.get("kind") == "official_manual_exclusion"
        and item.get("candidate_id") == CANDIDATE_ID
        and item.get("provider") == PROVIDER
    )
    assert evidence["candidate_id"] == CANDIDATE_ID
    assert evidence["provider"] == PROVIDER
    assert evidence["disabled_owner_providers"] == [PROVIDER]
    assert evidence["target_statuses"] == ["no_current_data"]

    review_candidates = load_yaml("config/municipal_integrated_reservation_promotion_review.yaml")["candidates"]
    assert all(row.get("candidate_id") != CANDIDATE_ID for row in review_candidates)
    assert all(row.get("provider") != PROVIDER for row in review_candidates)
