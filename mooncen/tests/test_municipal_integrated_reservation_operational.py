from __future__ import annotations

import argparse
import sys
import types
from pathlib import Path

import pytest
import yaml

import tools.promote_municipal_integrated_reservation_targets as promotion


def municipality(code: str, full_name: str) -> dict:
    sido, sigungu = full_name.split(" ", 1)
    return {"code": code, "sido": sido, "sigungu": sigungu, "full_name": full_name}


def candidate_row(code: str, full_name: str, url: str, *, score: int = 12) -> dict:
    return {
        **municipality(code, full_name),
        "candidates": [
            {
                "status": "candidate",
                "score": score,
                "title": full_name + " 통합예약",
                "url": url,
                "query": full_name + " 통합예약",
                "query_category_id": "integrated_reservation",
            }
        ],
    }


def operational_entry(
    provider: str,
    url: str,
    municipality_row: dict,
    *,
    action: str = "live_validate_new",
    outcome: str = "collected",
) -> dict:
    no_current_data = outcome == "no_current_data"
    return {
        "provider": provider,
        "normalized_url": promotion.normalized_duplicate_url(url),
        "target_url": url,
        "action": action,
        "validation_outcome": outcome,
        "validated_at": "2026-07-18T10:00:00+09:00",
        "parser": "test_parser",
        "row_count": 0 if no_current_data else 2,
        "no_current_data": no_current_data,
        "municipalities": [municipality_row],
    }


def install_fake_collector(monkeypatch, rows: list[dict], meta: dict | None = None, calls: list[str] | None = None) -> None:
    fake_module = types.ModuleType("Crawler.Crawler_MunicipalYaml")

    class FakeTarget:
        def __init__(self, **values):
            self.__dict__.update(values)

    def fake_collect(target, **_kwargs):
        if calls is not None:
            calls.append(target.url)
        return (list(rows), "test_parser", dict(meta or {"pages": 1}))

    fake_module.CrawlTarget = FakeTarget
    fake_module.collect_from_url = fake_collect
    monkeypatch.setitem(sys.modules, "Crawler.Crawler_MunicipalYaml", fake_module)


def test_operational_merge_is_idempotent_and_preserves_first_primary_owner(tmp_path: Path) -> None:
    path = tmp_path / "operational.yaml"
    first = municipality("1000000000", "테스트도 첫시")
    second = municipality("2000000000", "테스트도 둘시")
    url = "https://reserve.test.go.kr/course?page=1"
    original = operational_entry("MUNI_TEST", url, first)

    assert promotion.merge_operational_entries(path, [original]) is True
    assert promotion.merge_operational_entries(path, [original]) is True
    updated = operational_entry("MUNI_TEST", url, second, outcome="no_current_data")
    assert promotion.merge_operational_entries(path, [updated]) is True

    rows = promotion.operational_entries(yaml.safe_load(path.read_text(encoding="utf-8")))
    assert len(rows) == 1
    assert rows[0]["municipalities"] == [first]
    assert rows[0]["validation_outcome"] == "no_current_data"
    assert rows[0]["row_count"] == 0

    conflicting = operational_entry("OTHER_PROVIDER", url, first)
    with pytest.raises(ValueError, match="already owned"):
        promotion.merge_operational_entries(path, [conflicting])


def test_operational_manifest_survives_regeneration_and_does_not_downgrade_existing_coverage() -> None:
    muni = municipality("1000000000", "테스트도 보존시")
    url = "https://reserve.test.go.kr/course"
    entry = operational_entry("MUNI_TEST", url, muni)
    queue = {"municipalities": [muni]}
    candidates = {"results": [candidate_row(muni["code"], muni["full_name"], url)]}

    coverage, review = promotion.build_manifests(
        queue,
        candidates,
        [],
        set(),
        {},
        operational_document={"version": 1, "entries": [entry]},
    )
    row = coverage["municipalities"][0]
    assert row["status"] == "promoted"
    assert row["owner_providers"] == ["MUNI_TEST"]
    assert row["promoted_providers"] == ["MUNI_TEST"]
    assert any(item["kind"] == "operational_allowlist" for item in row["evidence"])
    assert review["candidates"][0]["status"] == "promoted"

    existing_target = {
        "provider": "MUNI_TEST",
        "url": url,
        "crawler_status": "ready",
        "priority": 2,
        "source": "test",
    }
    covered, _ = promotion.build_manifests(
        queue,
        candidates,
        [existing_target],
        {"MUNI_TEST"},
        {"MUNI_TEST": {"provider": "MUNI_TEST", "active_course_count": 3}},
        operational_document={"version": 1, "entries": [entry]},
    )
    assert covered["municipalities"][0]["status"] == "covered_by_existing"
    assert covered["municipalities"][0]["promoted_providers"] == ["MUNI_TEST"]


def test_operational_summary_counts_existing_and_incremental_entries() -> None:
    first = municipality("1000000000", "테스트도 첫시")
    second = municipality("2000000000", "테스트도 둘시")
    first_url = "https://first.test.go.kr/course"
    second_url = "https://second.test.go.kr/course"
    first_entry = operational_entry("MUNI_FIRST", first_url, first)
    second_entry = operational_entry("MUNI_SECOND", second_url, second)
    coverage, review = promotion.build_manifests(
        {"municipalities": [first, second]},
        {
            "results": [
                candidate_row(first["code"], first["full_name"], first_url),
                candidate_row(second["code"], second["full_name"], second_url),
            ]
        },
        [],
        set(),
        {},
        operational_document={"version": 1, "entries": [first_entry]},
    )

    assert coverage["summary"]["operational_entries"] == 1
    promotion.apply_operational_entries_to_manifests(coverage, review, [second_entry])
    assert coverage["summary"]["operational_entries"] == 2


def test_no_current_data_is_promoted_as_working_partial_for_only_selected_primary(monkeypatch) -> None:
    first = municipality("1000000000", "테스트도 첫시")
    second = municipality("2000000000", "테스트도 둘시")
    url = "https://shared.test.go.kr/course"
    coverage, review = promotion.build_manifests(
        {"municipalities": [first, second]},
        {
            "results": [
                candidate_row(first["code"], first["full_name"], url),
                candidate_row(second["code"], second["full_name"], url),
            ]
        },
        [],
        set(),
        {},
    )
    install_fake_collector(monkeypatch, [], {"pages": 1, "no_current_data": True})

    targets, additions = promotion.live_validate_candidates(
        review,
        coverage,
        [],
        municipality_filters={second["code"]},
        limit=1,
    )

    assert len(targets) == len(additions) == 1
    assert targets[0]["crawler_status"] == "partial"
    assert targets[0]["covered_municipalities"] == [second]
    assert targets[0]["last_quality"]["no_current_data"] is True
    assert additions[0]["validation_outcome"] == "no_current_data"
    assert additions[0]["municipalities"] == [second]
    promotion.apply_operational_entries_to_manifests(coverage, review, additions)
    by_code = {row["code"]: row for row in coverage["municipalities"]}
    assert by_code[first["code"]]["status"] == "review"
    assert by_code[second["code"]]["status"] == "promoted"


def test_schedule_existing_writes_only_operational_allowlist(monkeypatch) -> None:
    muni = municipality("1000000000", "테스트도 예약시")
    url = "https://reserve.test.go.kr/course"
    existing = {
        "provider": "EXISTING_OWNER",
        "url": url,
        "crawler_status": "ready",
        "priority": 2,
        "source": "test",
    }
    coverage, review = promotion.build_manifests(
        {"municipalities": [muni]},
        {"results": [candidate_row(muni["code"], muni["full_name"], url)]},
        [existing],
        set(),
        {},
    )
    assert review["candidates"][0]["recommended_action"] == "schedule_existing"
    install_fake_collector(monkeypatch, [{"title": "검증 코딩 강좌", "raw_url": url + "/1"}])

    targets, additions = promotion.live_validate_candidates(review, coverage, [existing], limit=1)

    assert targets == []
    assert len(additions) == 1
    assert additions[0]["action"] == "schedule_existing"
    assert additions[0]["provider"] == "EXISTING_OWNER"


def test_live_selection_is_deterministic_and_candidate_filter_is_exact(monkeypatch) -> None:
    muni = municipality("1000000000", "테스트도 선택시")

    def review_row(candidate_id: str, url: str, *, manual: bool, trust: str, score: int) -> dict:
        return {
            "candidate_id": candidate_id,
            "provider": "MUNI_" + candidate_id,
            "status": "review",
            "recommended_action": "live_validate_new",
            "url": url,
            "normalized_url": promotion.normalized_duplicate_url(url),
            "score": score,
            "title": candidate_id,
            "manual_override": manual,
            "trust_tier": trust,
            "municipalities": [muni],
        }

    rows = [
        review_row("HIGH", "https://high.go.kr/course", manual=False, trust="official_public_domain", score=99),
        review_row("MANUAL", "https://manual.example.com/course", manual=True, trust="other", score=1),
        review_row("OTHER", "https://other.example.com/course", manual=False, trust="other", score=999),
    ]
    coverage = {
        "summary": {},
        "municipalities": [{**muni, "status": "review", "owner_providers": [], "promoted_providers": [], "evidence": []}],
    }
    calls: list[str] = []
    install_fake_collector(monkeypatch, [{"title": "검증 코딩 강좌"}], calls=calls)

    promotion.live_validate_candidates(
        {"summary": {}, "candidates": [dict(row) for row in rows]},
        coverage,
        [],
        limit=1,
    )
    assert calls == ["https://manual.example.com/course"]

    calls.clear()
    promotion.live_validate_candidates(
        {"summary": {}, "candidates": [dict(row) for row in rows]},
        coverage,
        [],
        candidate_filters={"HIGH"},
    )
    assert calls == ["https://high.go.kr/course"]


def test_repeated_explicit_validations_union_covered_municipalities_only(tmp_path: Path) -> None:
    target_path = tmp_path / "municipal_integrated_reservation.yaml"
    first = municipality("1000000000", "테스트도 첫시")
    second = municipality("2000000000", "테스트도 둘시")
    url = "https://shared.test.go.kr/course"

    def target(primary: dict) -> dict:
        return {
            "provider": "MUNI_SHARED",
            "name": "통합예약",
            "branch": primary["full_name"],
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "operator_type": "지자체/공공기관",
            "source_group": "municipal_reservation",
            "service_group": "공공강좌",
            "service_group_policy": "locked",
            "collection_type": "generic_auto_discovery",
            "crawler_status": "partial",
            "priority": 2,
            "url": url,
            "source": "municipal_integrated_reservation_promotion",
            "origin": "live_validated",
            "municipality_code": primary["code"],
            "municipality_full_name": primary["full_name"],
            "covered_municipalities": [primary],
        }

    assert promotion.merge_promoted_targets(target_path, [target(first)], []) is True
    assert promotion.merge_promoted_targets(target_path, [target(second)], []) is True
    document = yaml.safe_load(target_path.read_text(encoding="utf-8"))
    row = document["targets"][0]
    assert row["municipality_code"] == first["code"]
    assert {item["code"] for item in row["covered_municipalities"]} == {first["code"], second["code"]}


def test_no_write_targets_is_a_dry_probe_for_operational_and_target_files(tmp_path: Path, monkeypatch) -> None:
    muni = municipality("1000000000", "테스트도 건조시")
    url = "https://dry.test.go.kr/course"
    queue_path = tmp_path / "queue.yaml"
    candidate_path = tmp_path / "candidates.yaml"
    target_dir = tmp_path / "targets"
    target_dir.mkdir()
    production_path = tmp_path / "production.yaml"
    evidence_path = tmp_path / "evidence.yaml"
    overrides_path = tmp_path / "overrides.yaml"
    coverage_path = tmp_path / "coverage.yaml"
    review_path = tmp_path / "review.yaml"
    operational_path = tmp_path / "operational.yaml"
    target_path = target_dir / "municipal_integrated_reservation.yaml"

    queue_path.write_text(yaml.safe_dump({"municipalities": [muni]}, allow_unicode=True), encoding="utf-8")
    candidate_path.write_text(
        yaml.safe_dump({"results": [candidate_row(muni["code"], muni["full_name"], url)]}, allow_unicode=True),
        encoding="utf-8",
    )
    production_path.write_text("version: 1\nproviders: []\n", encoding="utf-8")
    evidence_path.write_text("version: 1\nproviders: []\n", encoding="utf-8")
    overrides_path.write_text("version: 1\nmunicipalities: []\n", encoding="utf-8")
    initial_operational = "version: 1\nentries: []\n"
    operational_path.write_text(initial_operational, encoding="utf-8")
    install_fake_collector(monkeypatch, [{"title": "검증 코딩 강좌"}])
    monkeypatch.setattr(
        promotion,
        "parse_args",
        lambda: argparse.Namespace(
            queue=str(queue_path),
            candidates=str(candidate_path),
            target_dir=str(target_dir),
            production_providers=str(production_path),
            production_evidence=str(evidence_path),
            overrides=str(overrides_path),
            coverage_out=str(coverage_path),
            review_out=str(review_path),
            operational_out=str(operational_path),
            target_out=str(target_path),
            min_score=8,
            live_validate=True,
            municipality=None,
            candidate_id=None,
            limit=1,
            timeout=1,
            max_pages=1,
            detail_limit=1,
            no_write_targets=True,
        ),
    )

    assert promotion.main() == 0
    assert operational_path.read_text(encoding="utf-8") == initial_operational
    assert not target_path.exists()
    assert coverage_path.exists()
    assert review_path.exists()
