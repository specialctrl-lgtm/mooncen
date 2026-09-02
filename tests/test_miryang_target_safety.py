from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
YEYAK = "MUNI_YEYAK_MIRYANG_GO_KR_0741D829"
LIFELONG = "MUNI_WWW_MIRYANG_GO_KR_F66F2E07"
LIFELONG_SUBSET = "MUNI_WWW_MIRYANG_GO_KR_590AFA4C"
SPORTS = "MUNI_YEYAK_MIRYANG_GO_KR_3800E0A0"
YEYAK_CANDIDATE = "MUNI_IR_DD90BA2CBA8F"
LIFELONG_CANDIDATE = "MUNI_IR_DC48C633A280"
MUNICIPALITY_CODE = "4827000000"
YEYAK_SCOPE = "miryang_official_integrated_all_5_education_catalogues_current_future"
LIFELONG_SCOPE = "miryang_official_lifelong_complete_catalogue_current_future"


def load_yaml(relative_path: str) -> dict:
    return yaml.safe_load((ROOT / relative_path).read_text(encoding="utf-8"))


def by_provider(rows: list[dict]) -> dict[str, dict]:
    return {str(row.get("provider") or ""): row for row in rows}


def test_miryang_two_canonical_catalogues_and_exclusions() -> None:
    public = by_provider(load_yaml("config/crawl_targets/public_reservation.yaml")["targets"])
    lifelong = by_provider(load_yaml("config/crawl_targets/lifelong_learning.yaml")["targets"])

    integrated = public[YEYAK]
    assert integrated["crawler_status"] == "ready"
    assert integrated["domain_category"] == "교육·강좌"
    assert integrated["full_snapshot_required"] is True
    assert integrated["ownership_scope"] == YEYAK_SCOPE
    quality = integrated["last_quality"]
    assert quality["source_total"] == 553
    assert quality["pages"] == 70
    assert quality["current_count"] == quality["detail_pages"] == 39
    assert sum(quality["branch_counts"].values()) == 39
    assert quality["snapshot_complete"] is True

    canonical = lifelong[LIFELONG]
    assert canonical["crawler_status"] == "ready"
    assert canonical["domain_category"] == "교육·강좌"
    assert canonical["full_snapshot_required"] is True
    assert canonical["ownership_scope"] == LIFELONG_SCOPE
    assert canonical["last_quality"]["source_total"] == 220
    assert canonical["last_quality"]["pages"] == 23
    assert canonical["last_quality"]["detail_pages"] == 2
    assert canonical["last_quality"]["snapshot_complete"] is True

    assert lifelong[LIFELONG_SUBSET]["duplicate_of"] == LIFELONG
    assert public[SPORTS]["crawler_status"] == "excluded_url_shape"


def test_miryang_operational_coverage_review_and_registry_contract() -> None:
    operational = by_provider(
        load_yaml("config/municipal_integrated_reservation_operational.yaml")["entries"]
    )
    assert LIFELONG_SUBSET not in operational
    assert SPORTS not in operational
    assert operational[YEYAK]["ownership_scope"] == YEYAK_SCOPE
    assert operational[LIFELONG]["ownership_scope"] == LIFELONG_SCOPE
    assert operational[LIFELONG]["superseded_providers"] == [LIFELONG_SUBSET]

    coverage = load_yaml("config/municipal_integrated_reservation_coverage.yaml")
    municipality = next(
        row for row in coverage["municipalities"] if row["code"] == MUNICIPALITY_CODE
    )
    assert municipality["status"] == "promoted"
    owner_providers = set(municipality["owner_providers"])
    assert {LIFELONG, YEYAK}.issubset(owner_providers)
    assert {LIFELONG_SUBSET, SPORTS}.isdisjoint(owner_providers)
    assert set(municipality["promoted_providers"]) == owner_providers
    assert {YEYAK_CANDIDATE, LIFELONG_CANDIDATE}.issubset(
        municipality["review_candidate_ids"]
    )

    review = load_yaml("config/municipal_integrated_reservation_promotion_review.yaml")
    candidates = {row["candidate_id"]: row for row in review["candidates"]}
    assert candidates[YEYAK_CANDIDATE]["status"] == "promoted"
    assert candidates[YEYAK_CANDIDATE]["live_validation"]["row_count"] == 39
    assert candidates[YEYAK_CANDIDATE]["live_validation"]["pages"] == 70
    assert candidates[LIFELONG_CANDIDATE]["status"] == "promoted"
    assert candidates[LIFELONG_CANDIDATE]["live_validation"]["row_count"] == 2
    assert candidates[LIFELONG_CANDIDATE]["live_validation"]["pages"] == 23

    registry = load_yaml("config/generated_yaml_crawler_registry.yaml")
    providers = {row["provider"] for row in registry["targets"]}
    assert {YEYAK, LIFELONG, LIFELONG_SUBSET, SPORTS}.isdisjoint(providers)
