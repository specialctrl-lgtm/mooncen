from __future__ import annotations

from pathlib import Path

import yaml

from Crawler import Crawler_GeneratedYamlTargets as generated
from Crawler import municipal_seogwipo_eticket as seogwipo
from Crawler import municipal_wanju as wanju


ROOT = Path(__file__).resolve().parents[1]
WANJU_URL = (
    "https://lib.wanju.go.kr/planweb/board/list.9is?"
    "boardUid=ff808081737e5a410173a2472101563d&"
    "contentUid=ff808081727e842d017291a8906600c8"
)


def _yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _targets() -> list[dict]:
    rows: list[dict] = []
    for path in sorted((ROOT / "config" / "crawl_targets").glob("*.yaml")):
        if path.name != "index.yaml":
            rows.extend(_yaml(path).get("targets") or [])
    return rows


def _arg_value(arguments: tuple[str, ...], flag: str) -> int:
    return int(arguments[arguments.index(flag) + 1])


def test_seogwipo_eticket_is_a_live_validated_complete_education_target() -> None:
    rows = [row for row in _targets() if row.get("provider") == seogwipo.SEOGWIPO_ETICKET_PROVIDER]

    assert len(rows) == 1
    row = rows[0]
    assert row["url"] == seogwipo.SEOGWIPO_ETICKET_TARGET_URL
    assert row["crawler_status"] == "ready"
    assert row["collection_category"] == "공공예약"
    assert row["domain_category"] == "교육·강좌"
    assert row["source_group"] == "municipal_reservation"
    assert row["service_group"] == "공공강좌"
    assert row["service_group_policy"] == "locked"
    assert row["full_snapshot_required"] is True
    assert row["ownership_scope"] == "official_seogwipo_eticket_explicit_education_current_future"
    assert seogwipo.SEOGWIPO_ETICKET_LIST_URL in row["ownership_aliases"]
    assert row["last_quality"]["source_total"] == 21
    assert row["last_quality"]["detail_pages"] == 0
    assert row["last_quality"]["detail_api_calls"] == 0
    assert row["last_quality"]["current_education_source_count"] == 1
    assert row["last_quality"]["linked_education_count"] == 0
    assert row["last_quality"]["list_only_closed_education_count"] == 1
    assert row["last_quality"]["ignored_current_non_education_count"] == 5
    assert row["last_quality"]["collected"] == 1
    assert row["last_quality"]["snapshot_complete"] is True


def test_seogwipo_eticket_is_exactly_allowlisted_with_uncapped_output() -> None:
    entries = _yaml(ROOT / "config" / "municipal_integrated_reservation_operational.yaml")["entries"]
    row = next(item for item in entries if item.get("provider") == seogwipo.SEOGWIPO_ETICKET_PROVIDER)

    assert row["target_url"] == seogwipo.SEOGWIPO_ETICKET_TARGET_URL
    assert row["validation_outcome"] == "collected"
    assert row["row_count"] == 1
    assert row["no_current_data"] is False
    assert row["ownership_scope"] == "official_seogwipo_eticket_explicit_education_current_future"
    assert [item["code"] for item in row["municipalities"]] == ["5013000000"]

    arguments = generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[seogwipo.SEOGWIPO_ETICKET_PROVIDER]
    assert "--save-db" in arguments
    assert "--mark-stale" in arguments
    assert "--allow-partial-save" not in arguments
    assert _arg_value(arguments, "--per-target-limit") == 0
    assert _arg_value(arguments, "--max-pages") == 1
    assert _arg_value(arguments, "--detail-limit") == 100


def test_wanju_programme_board_is_promoted_and_home_alias_is_excluded() -> None:
    document = _yaml(ROOT / "config" / "municipal_integrated_reservation_overrides.yaml")
    municipality = next(row for row in document["municipalities"] if row["code"] == "5271000000")
    by_url = {row["url"]: row for row in municipality["candidates"]}

    assert by_url[WANJU_URL]["status"] == "candidate"
    home = next(
        row
        for row in municipality["candidates"]
        if row["url"].startswith("https://lib.wanju.go.kr/index.9is")
    )
    assert home["status"] == "excluded"
    assert home["exclusion_reason"] == "navigation_home_alias_of_canonical_owner"

    targets = [row for row in _targets() if row.get("provider") == wanju.WANJU_PROVIDER]
    assert len(targets) == 1
    assert targets[0]["url"] == WANJU_URL == wanju.WANJU_CANONICAL_URL
    assert targets[0]["crawler_status"] == "ready"
