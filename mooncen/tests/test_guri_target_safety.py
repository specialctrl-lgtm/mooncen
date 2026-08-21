from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
GSEEK = "MUNI_GURI_GSEEK_KR_2E5F409F"
RESERVE = "MUNI_WWW_GURI_GO_KR_E0C65498"
PRIVATE = "MUNI_WWW_GURI_GO_KR_8B69DE2C"
STATIC_INFO = "MUNI_WWW_GURI_GO_KR_60EBA72C"
MUNICIPALITY_CODE = "4131000000"
GSEEK_CANDIDATE = "MUNI_IR_C64E21DB4BB2"
RESERVE_CANDIDATE = "MUNI_IR_B293F20BA593"
EXPECTED = {
    GSEEK: (
        "guri_gseek_complete_ranges+sentinel+current_detail",
        73,
        28,
        73,
        "guri_gseek_official_all_current_future",
    ),
    RESERVE: (
        "guri_reserve_complete_source_inventory+pages+sentinels+current_detail",
        276,
        51,
        277,
        "guri_official_reservation_17_sources_current_future",
    ),
}


def load_yaml(relative_path: str) -> dict:
    return yaml.safe_load((ROOT / relative_path).read_text(encoding="utf-8"))


def by_provider(rows: list[dict]) -> dict[str, dict]:
    return {str(row.get("provider") or ""): row for row in rows}


def test_guri_has_two_complete_nonduplicate_education_owners() -> None:
    lifelong = by_provider(load_yaml("config/crawl_targets/lifelong_learning.yaml")["targets"])
    public_rows = load_yaml("config/crawl_targets/public_reservation.yaml")["targets"]
    public = by_provider(public_rows)
    reserve_education = next(
        row
        for row in public_rows
        if row.get("provider") == RESERVE
        and row.get("collection_type") == EXPECTED[RESERVE][0]
    )
    targets = {GSEEK: lifelong[GSEEK], RESERVE: reserve_education}
    for provider, (parser, rows, pages, details, scope) in EXPECTED.items():
        target = targets[provider]
        assert target["crawler_status"] == "ready"
        assert target["collection_type"] == parser
        assert target["domain_category"] == "교육·강좌"
        assert target["service_group"] == "공공강좌"
        assert target["full_snapshot_required"] is True
        assert target["ownership_scope"] == scope
        quality = target["last_quality"]
        assert quality["collected"] == rows
        assert quality["pages"] == pages
        assert quality["detail_pages"] == details
        assert quality["duplicate_count"] == 0
        assert quality["semantic_duplicate_count"] == 0
        assert quality["snapshot_complete"] is True
    assert public[PRIVATE]["crawler_status"] == "excluded_url_shape"
    assert lifelong[STATIC_INFO]["crawler_status"] == "excluded_url_shape"


def test_guri_operational_coverage_and_registry_keep_both_scopes() -> None:
    operational_rows = load_yaml(
        "config/municipal_integrated_reservation_operational.yaml"
    )["entries"]
    operational = by_provider(operational_rows)
    assert PRIVATE not in operational
    assert STATIC_INFO not in operational
    for provider, (parser, rows, _pages, _details, scope) in EXPECTED.items():
        entry = next(
            row
            for row in operational_rows
            if row.get("provider") == provider and row.get("parser") == parser
        )
        assert entry["parser"] == parser
        assert entry["row_count"] == rows
        assert entry["ownership_scope"] == scope

    coverage = load_yaml("config/municipal_integrated_reservation_coverage.yaml")
    municipality = next(
        row for row in coverage["municipalities"] if row["code"] == MUNICIPALITY_CODE
    )
    assert municipality["status"] == "promoted"
    assert set(municipality["owner_providers"]) == set(EXPECTED)
    assert set(municipality["promoted_providers"]) == set(EXPECTED)

    review = load_yaml("config/municipal_integrated_reservation_promotion_review.yaml")
    candidates = {
        row["candidate_id"]: row
        for row in review["candidates"]
        if row["candidate_id"] in {GSEEK_CANDIDATE, RESERVE_CANDIDATE}
    }
    assert set(candidates) == {GSEEK_CANDIDATE, RESERVE_CANDIDATE}
    assert {row["status"] for row in candidates.values()} == {"promoted"}
    assert {row["live_validation"]["row_count"] for row in candidates.values()} == {73, 276}

    registry = load_yaml("config/generated_yaml_crawler_registry.yaml")
    providers = {row["provider"] for row in registry["targets"]}
    assert {GSEEK, RESERVE, PRIVATE, STATIC_INFO}.isdisjoint(providers)
