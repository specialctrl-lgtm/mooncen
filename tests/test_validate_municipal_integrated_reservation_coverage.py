from __future__ import annotations

from collections import Counter
from pathlib import Path

import yaml

from tools.validate_municipal_integrated_reservation_coverage import (
    expand_aggregate_production_providers,
    is_locked_live_validate_target,
    normalized_scope_url,
    validate,
)


def write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def queue_document() -> dict:
    return {
        "version": 1,
        "totals": {"municipalities": 2},
        "municipalities": [
            {"code": "1000000001", "sido": "시험도", "sigungu": "가시", "full_name": "시험도 가시"},
            {"code": "1000000002", "sido": "시험도", "sigungu": "나군", "full_name": "시험도 나군"},
        ],
    }


def production_document(*providers: str) -> dict:
    return {
        "version": 1,
        "captured_at": "2026-07-17",
        "source": "test CRAWLER_PROVIDERS",
        "providers": list(providers),
    }


def production_evidence_document(**provider_counts: int) -> dict:
    return {
        "version": 1,
        "environment": "test",
        "scope": "education",
        "include_inactive": False,
        "checked_at": "2026-07-17T23:20:00+09:00",
        "source": "test exact provider filter",
        "summary": {
            "queried_providers": len(provider_counts),
            "active_courses": sum(provider_counts.values()),
            "providers_with_active_courses": sum(1 for count in provider_counts.values() if count > 0),
        },
        "providers": [
            {"provider": provider, "active_course_count": count}
            for provider, count in provider_counts.items()
        ],
    }


def operational_document(entries: list[dict] | None = None) -> dict:
    rows = list(entries or [])
    return {
        "version": 1,
        "summary": {
            "entries": len(rows),
            "by_action": dict(Counter(row.get("action") for row in rows)),
            "by_outcome": dict(Counter(row.get("validation_outcome") for row in rows)),
        },
        "entries": rows,
    }


def operational_entry(
    provider: str,
    url: str,
    municipality: dict,
    *,
    action: str = "schedule_existing",
    validation_outcome: str = "collected",
    row_count: int = 1,
    no_current_data: bool = False,
) -> dict:
    return {
        "provider": provider,
        "normalized_url": normalized_scope_url(url),
        "target_url": url,
        "action": action,
        "validation_outcome": validation_outcome,
        "validated_at": "2026-07-18T00:30:00+09:00",
        "parser": "test_parser",
        "row_count": row_count,
        "no_current_data": no_current_data,
        "municipalities": [municipality],
    }


def test_live_validation_lock_accepts_only_canonical_education_or_experience() -> None:
    common = {
        "collection_category": "공공예약",
        "service_group_policy": "locked",
    }
    assert is_locked_live_validate_target(
        common | {"domain_category": "교육·강좌", "service_group": "공공강좌"}
    )
    assert is_locked_live_validate_target(
        common | {"domain_category": "체험·견학", "service_group": "체험"}
    )
    assert not is_locked_live_validate_target(
        common | {"domain_category": "체험·견학", "service_group": "공공강좌"}
    )
    assert not is_locked_live_validate_target(
        common
        | {
            "domain_category": "체험·견학",
            "service_group": "체험",
            "service_group_policy": "inferred",
        }
    )


def test_expands_aggregate_schedule_to_concrete_course_providers(tmp_path: Path) -> None:
    operational = tmp_path / "operational.yaml"
    write_yaml(
        operational,
        {
            "version": 1,
            "entries": [{"provider": "MUNI_AGGREGATE_OWNER"}],
        },
    )

    providers, errors = expand_aggregate_production_providers(
        {
            "DIRECT_PROVIDER",
            "EXPERIENCE_TARGETS",
            "MUNICIPAL_RESERVATION_TARGETS",
        },
        target_rows=[
            target("MUNI_AGGREGATE_OWNER", "https://municipal.example/education"),
            {
                **target("MUSEUM_EXPERIENCE", "https://museum.example/programs"),
                "service_group": "체험",
            },
        ],
        operational_path=operational,
    )

    assert errors == []
    assert {
        "DIRECT_PROVIDER",
        "EXPERIENCE_TARGETS",
        "MUNICIPAL_RESERVATION_TARGETS",
        "MUNI_AGGREGATE_OWNER",
        "MUSEUM_EXPERIENCE",
    } <= providers


def municipality(code: str) -> dict:
    return next(row for row in queue_document()["municipalities"] if row["code"] == code)


def target(
    provider: str,
    url: str,
    *,
    municipality_code: str = "",
    municipality_full_name: str = "",
    locked: bool = False,
) -> dict:
    row = {
        "provider": provider,
        "name": provider,
        "branch": municipality_full_name or provider,
        "crawler_status": "ready",
        "url": url,
        "collection_type": "static_html",
    }
    if municipality_code:
        row["municipality_code"] = municipality_code
    if municipality_full_name:
        row["municipality_full_name"] = municipality_full_name
    if locked:
        row.update(
            {
                "collection_category": "공공예약",
                "domain_category": "교육·강좌",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
            }
        )
    return row


def test_validates_exact_existing_owner_and_locked_promoted_target(tmp_path: Path) -> None:
    queue = tmp_path / "queue.yaml"
    coverage = tmp_path / "coverage.yaml"
    target_dir = tmp_path / "targets"
    production = tmp_path / "production.yaml"
    production_evidence = tmp_path / "production_evidence.yaml"
    operational = tmp_path / "operational.yaml"
    write_yaml(queue, queue_document())
    write_yaml(
        target_dir / "targets.yaml",
        {
            "version": 1,
            "targets": [
                target("EXISTING_A", "https://a.example/reserve?page=1"),
                target(
                    "PROMOTED_B",
                    "https://b.example/education",
                    municipality_code="1000000002",
                    municipality_full_name="시험도 나군",
                    locked=True,
                ),
            ],
        },
    )
    write_yaml(production, production_document("EXISTING_A"))
    write_yaml(production_evidence, production_evidence_document(EXISTING_A=3))
    write_yaml(
        operational,
        operational_document(
            [
                operational_entry(
                    "EXISTING_A",
                    "https://a.example/reserve?page=1",
                    municipality("1000000001"),
                )
            ]
        ),
    )
    write_yaml(
        coverage,
        {
            "version": 1,
            "summary": {
                "municipalities": 2,
                "by_status": {"covered_by_existing": 1, "promoted": 1},
            },
            "municipalities": [
                {
                    "code": "1000000001",
                    "full_name": "시험도 가시",
                    "status": "covered_by_existing",
                    "owner_providers": ["EXISTING_A"],
                    "promoted_providers": ["EXISTING_A"],
                    "evidence": [
                        {
                            "kind": "exact_active_url",
                            "ownership_basis": "production_scheduled_active_courses",
                            "provider": "EXISTING_A",
                            "url": "https://a.example/reserve?page=9",
                            "production_active_course_count": 3,
                        }
                    ],
                    "candidate_count": 1,
                    "eligible_candidate_count": 1,
                },
                {
                    "code": "1000000002",
                    "full_name": "시험도 나군",
                    "status": "promoted",
                    "owner_providers": ["PROMOTED_B"],
                    "candidate_count": 1,
                    "eligible_candidate_count": 1,
                },
            ],
        },
    )

    errors, warnings, summary = validate(
        queue_path=queue,
        coverage_path=coverage,
        target_dir=target_dir,
        production_providers_path=production,
        production_evidence_path=production_evidence,
        operational_path=operational,
        expected_municipalities=2,
    )

    assert errors == []
    assert summary["configured_covered_municipalities"] == 2
    assert summary["production_scheduled_covered_municipalities"] == 1
    assert summary["locked_promoted_municipalities"] == 1
    assert any("not production-scheduled" in warning for warning in warnings)


def test_validates_declared_ownership_alias_and_rejects_undeclared_claim(tmp_path: Path) -> None:
    queue = tmp_path / "queue.yaml"
    coverage = tmp_path / "coverage.yaml"
    target_dir = tmp_path / "targets"
    production = tmp_path / "production.yaml"
    production_evidence = tmp_path / "production_evidence.yaml"
    operational = tmp_path / "operational.yaml"
    provider = "MUNI_DOKSEODANG_SD_GO_KR_A8C20229"
    canonical_url = "https://www.sd.go.kr/booking/webEduList2.do?key=4916"
    alias_url = "http://dokseodang.sd.go.kr/"

    write_yaml(queue, queue_document())
    write_yaml(
        target_dir / "targets.yaml",
        {
            "version": 1,
            "targets": [
                {
                    **target(provider, canonical_url),
                    "ownership_aliases": [alias_url],
                }
            ],
        },
    )
    write_yaml(production, production_document(provider))
    write_yaml(production_evidence, production_evidence_document(**{provider: 22}))
    write_yaml(operational, operational_document())
    write_yaml(
        coverage,
        {
            "version": 1,
            "summary": {"municipalities": 2, "by_status": {"covered_by_existing": 1, "no_candidate": 1}},
            "municipalities": [
                {
                    "code": "1000000001",
                    "full_name": municipality("1000000001")["full_name"],
                    "status": "covered_by_existing",
                    "owner_providers": [provider],
                    "candidate_count": 1,
                    "eligible_candidate_count": 1,
                    "evidence": [
                        {
                            "kind": "ownership_alias",
                            "ownership_basis": "production_scheduled_active_courses",
                            "candidate_id": "MUNI_IR_A462D3FEDF14",
                            "provider": provider,
                            "candidate_url": alias_url,
                            "target_url": canonical_url,
                            "normalized_url": normalized_scope_url(alias_url),
                            "ownership_alias": alias_url,
                            "production_active_course_count": 22,
                        }
                    ],
                },
                {
                    "code": "1000000002",
                    "full_name": municipality("1000000002")["full_name"],
                    "status": "no_candidate",
                    "owner_providers": [],
                    "candidate_count": 0,
                    "eligible_candidate_count": 0,
                },
            ],
        },
    )

    errors, _, _ = validate(
        queue_path=queue,
        coverage_path=coverage,
        target_dir=target_dir,
        production_providers_path=production,
        production_evidence_path=production_evidence,
        operational_path=operational,
        expected_municipalities=2,
        require_complete=False,
    )
    assert errors == []

    write_yaml(
        target_dir / "targets.yaml",
        {"version": 1, "targets": [target(provider, canonical_url)]},
    )
    errors, _, _ = validate(
        queue_path=queue,
        coverage_path=coverage,
        target_dir=target_dir,
        production_providers_path=production,
        production_evidence_path=production_evidence,
        operational_path=operational,
        expected_municipalities=2,
        require_complete=False,
    )
    assert any("ownership-alias evidence" in error for error in errors)


def test_rejects_missing_duplicate_and_unknown_municipality_codes(tmp_path: Path) -> None:
    queue = tmp_path / "queue.yaml"
    coverage = tmp_path / "coverage.yaml"
    targets = tmp_path / "targets"
    production = tmp_path / "production.yaml"
    production_evidence = tmp_path / "production_evidence.yaml"
    operational = tmp_path / "operational.yaml"
    write_yaml(queue, queue_document())
    write_yaml(production, production_document())
    write_yaml(production_evidence, production_evidence_document())
    write_yaml(operational, operational_document())
    write_yaml(
        coverage,
        {
            "version": 1,
            "municipalities": [
                {"code": "1000000001", "full_name": "시험도 가시", "status": "no_candidate"},
                {"code": "1000000001", "full_name": "시험도 가시", "status": "no_candidate"},
                {"code": "9999999999", "full_name": "없는 곳", "status": "no_candidate"},
            ],
        },
    )

    errors, _, _ = validate(
        queue_path=queue,
        coverage_path=coverage,
        target_dir=targets,
        production_providers_path=production,
        production_evidence_path=production_evidence,
        operational_path=operational,
        expected_municipalities=2,
        require_complete=False,
    )

    assert any("duplicate municipality codes" in error for error in errors)
    assert any("missing official municipality codes" in error for error in errors)
    assert any("unknown municipality codes" in error for error in errors)


def test_rejects_nonworking_owner_and_nonmatching_url_evidence(tmp_path: Path) -> None:
    queue = tmp_path / "queue.yaml"
    coverage = tmp_path / "coverage.yaml"
    target_dir = tmp_path / "targets"
    production = tmp_path / "production.yaml"
    production_evidence = tmp_path / "production_evidence.yaml"
    operational = tmp_path / "operational.yaml"
    write_yaml(queue, queue_document())
    write_yaml(
        target_dir / "targets.yaml",
        {
            "version": 1,
            "targets": [
                {**target("BLOCKED_A", "https://a.example/reserve"), "crawler_status": "blocked"},
                target("WORKING_B", "https://b.example/reserve"),
            ],
        },
    )
    write_yaml(production, production_document("BLOCKED_A", "WORKING_B"))
    write_yaml(production_evidence, production_evidence_document(BLOCKED_A=1, WORKING_B=1))
    write_yaml(operational, operational_document())
    write_yaml(
        coverage,
        {
            "version": 1,
            "municipalities": [
                {
                    "code": "1000000001",
                    "full_name": "시험도 가시",
                    "status": "covered_by_existing",
                    "owner_providers": ["BLOCKED_A"],
                    "evidence": [{"url": "https://a.example/reserve"}],
                },
                {
                    "code": "1000000002",
                    "full_name": "시험도 나군",
                    "status": "covered_by_existing",
                    "owner_providers": ["WORKING_B"],
                    "evidence": [{"url": "https://wrong.example/reserve"}],
                },
            ],
        },
    )

    errors, _, _ = validate(
        queue_path=queue,
        coverage_path=coverage,
        target_dir=target_dir,
        production_providers_path=production,
        production_evidence_path=production_evidence,
        operational_path=operational,
        expected_municipalities=2,
    )

    assert any("not enabled/working" in error for error in errors)
    assert any("evidence URL does not match" in error for error in errors)


def test_rejects_active_scope_duplicates_and_unlocked_promotion(tmp_path: Path) -> None:
    queue = tmp_path / "queue.yaml"
    coverage = tmp_path / "coverage.yaml"
    target_dir = tmp_path / "targets"
    production = tmp_path / "production.yaml"
    production_evidence = tmp_path / "production_evidence.yaml"
    operational = tmp_path / "operational.yaml"
    write_yaml(queue, queue_document())
    write_yaml(
        target_dir / "targets.yaml",
        {
            "version": 1,
            "targets": [
                target("PROMOTED_A", "https://same.example/list?page=1"),
                target("PROMOTED_B", "https://same.example/list?page=2"),
            ],
        },
    )
    write_yaml(production, production_document("PROMOTED_A", "PROMOTED_B"))
    write_yaml(production_evidence, production_evidence_document(PROMOTED_A=1, PROMOTED_B=1))
    write_yaml(operational, operational_document())
    write_yaml(
        coverage,
        {
            "version": 1,
            "municipalities": [
                {
                    "code": "1000000001",
                    "full_name": "시험도 가시",
                    "status": "promoted",
                    "owner_providers": ["PROMOTED_A"],
                },
                {
                    "code": "1000000002",
                    "full_name": "시험도 나군",
                    "status": "promoted",
                    "owner_providers": ["PROMOTED_B"],
                },
            ],
        },
    )

    errors, _, summary = validate(
        queue_path=queue,
        coverage_path=coverage,
        target_dir=target_dir,
        production_providers_path=production,
        production_evidence_path=production_evidence,
        operational_path=operational,
        expected_municipalities=2,
    )

    assert summary["active_scope_duplicates"] == 1
    assert any("active scope duplicate" in error for error in errors)
    assert sum("lack a locked target or exact operational binding" in error for error in errors) == 2


def test_incomplete_rows_are_warning_only_when_explicitly_allowed(tmp_path: Path) -> None:
    queue = tmp_path / "queue.yaml"
    coverage = tmp_path / "coverage.yaml"
    targets = tmp_path / "targets"
    production = tmp_path / "production.yaml"
    production_evidence = tmp_path / "production_evidence.yaml"
    operational = tmp_path / "operational.yaml"
    write_yaml(queue, queue_document())
    write_yaml(production, production_document())
    write_yaml(production_evidence, production_evidence_document())
    write_yaml(operational, operational_document())
    write_yaml(
        coverage,
        {
            "version": 1,
            "municipalities": [
                {
                    "code": "1000000001",
                    "full_name": "시험도 가시",
                    "status": "review",
                    "review_candidate_ids": ["candidate-a"],
                    "candidate_count": 1,
                    "eligible_candidate_count": 1,
                },
                {
                    "code": "1000000002",
                    "full_name": "시험도 나군",
                    "status": "no_candidate",
                    "candidate_count": 0,
                    "eligible_candidate_count": 0,
                },
            ],
        },
    )

    strict_errors, _, _ = validate(
        queue_path=queue,
        coverage_path=coverage,
        target_dir=targets,
        production_providers_path=production,
        production_evidence_path=production_evidence,
        operational_path=operational,
        expected_municipalities=2,
    )
    relaxed_errors, relaxed_warnings, summary = validate(
        queue_path=queue,
        coverage_path=coverage,
        target_dir=targets,
        production_providers_path=production,
        production_evidence_path=production_evidence,
        operational_path=operational,
        expected_municipalities=2,
        require_complete=False,
    )

    assert any("unresolved municipalities count=2" in error for error in strict_errors)
    assert relaxed_errors == []
    assert summary["unresolved_municipalities"] == 2
    assert any("unresolved municipalities count=2" in warning for warning in relaxed_warnings)


def test_schedule_existing_operational_binding_accepts_legacy_classification(
    tmp_path: Path,
) -> None:
    queue = tmp_path / "queue.yaml"
    coverage = tmp_path / "coverage.yaml"
    target_dir = tmp_path / "targets"
    production = tmp_path / "production.yaml"
    production_evidence = tmp_path / "production_evidence.yaml"
    operational = tmp_path / "operational.yaml"
    url = "https://legacy.example/lecture/list.do?page=3"
    write_yaml(queue, queue_document())
    write_yaml(target_dir / "targets.yaml", {"version": 1, "targets": [target("LEGACY_A", url)]})
    write_yaml(production, production_document())
    write_yaml(production_evidence, production_evidence_document())
    write_yaml(
        operational,
        operational_document([operational_entry("LEGACY_A", url, municipality("1000000001"))]),
    )
    write_yaml(
        coverage,
        {
            "version": 1,
            "municipalities": [
                {
                    "code": "1000000001",
                    "full_name": "시험도 가시",
                    "status": "promoted",
                    "promoted_providers": ["LEGACY_A"],
                },
                {
                    "code": "1000000002",
                    "full_name": "시험도 나군",
                    "status": "no_candidate",
                },
            ],
        },
    )

    errors, _, summary = validate(
        queue_path=queue,
        coverage_path=coverage,
        target_dir=target_dir,
        production_providers_path=production,
        production_evidence_path=production_evidence,
        operational_path=operational,
        expected_municipalities=2,
        require_complete=False,
    )

    assert errors == []
    assert summary["locked_promoted_municipalities"] == 0
    assert summary["operational_promoted_municipalities"] == 1
    assert summary["validated_promoted_municipalities"] == 1


def test_live_validate_new_no_current_data_requires_working_locked_target(
    tmp_path: Path,
) -> None:
    queue = tmp_path / "queue.yaml"
    coverage = tmp_path / "coverage.yaml"
    target_dir = tmp_path / "targets"
    production = tmp_path / "production.yaml"
    production_evidence = tmp_path / "production_evidence.yaml"
    operational = tmp_path / "operational.yaml"
    url = "https://new.example/education/list"
    write_yaml(queue, queue_document())
    no_current_target = target("LIVE_A", url, locked=True)
    no_current_target["crawler_status"] = "no_current_data"
    write_yaml(
        target_dir / "targets.yaml",
        {"version": 1, "targets": [no_current_target]},
    )
    write_yaml(production, production_document())
    write_yaml(production_evidence, production_evidence_document())
    write_yaml(
        operational,
        operational_document(
            [
                operational_entry(
                    "LIVE_A",
                    url,
                    municipality("1000000001"),
                    action="live_validate_new",
                    validation_outcome="no_current_data",
                    row_count=0,
                    no_current_data=True,
                )
            ]
        ),
    )
    write_yaml(
        coverage,
        {
            "version": 1,
            "municipalities": [
                {
                    "code": "1000000001",
                    "full_name": "시험도 가시",
                    "status": "promoted",
                    "promoted_providers": ["LIVE_A"],
                },
                {
                    "code": "1000000002",
                    "full_name": "시험도 나군",
                    "status": "no_candidate",
                },
            ],
        },
    )

    errors, _, summary = validate(
        queue_path=queue,
        coverage_path=coverage,
        target_dir=target_dir,
        production_providers_path=production,
        production_evidence_path=production_evidence,
        operational_path=operational,
        expected_municipalities=2,
        require_complete=False,
    )

    assert errors == []
    assert summary["operational_by_outcome"] == {"no_current_data": 1}
    assert summary["operational_promoted_municipalities"] == 1


def test_rejects_unlocked_live_validation_and_operational_coverage_mismatch(
    tmp_path: Path,
) -> None:
    queue = tmp_path / "queue.yaml"
    coverage = tmp_path / "coverage.yaml"
    target_dir = tmp_path / "targets"
    production = tmp_path / "production.yaml"
    production_evidence = tmp_path / "production_evidence.yaml"
    operational = tmp_path / "operational.yaml"
    url = "https://new.example/education/list"
    legacy_url = "https://legacy.example/lecture/list"
    write_yaml(queue, queue_document())
    write_yaml(
        target_dir / "targets.yaml",
        {
            "version": 1,
            "targets": [target("LIVE_A", url), target("LEGACY_B", legacy_url)],
        },
    )
    write_yaml(production, production_document())
    write_yaml(production_evidence, production_evidence_document())
    write_yaml(
        operational,
        operational_document(
            [
                    operational_entry(
                        "LIVE_A",
                        url,
                        municipality("1000000001"),
                        action="live_validate_new",
                    ),
                    operational_entry("LEGACY_B", legacy_url, municipality("1000000002")),
                ]
            ),
    )
    write_yaml(
        coverage,
        {
            "version": 1,
            "municipalities": [
                {
                    "code": "1000000001",
                    "full_name": "시험도 가시",
                    "status": "promoted",
                    "promoted_providers": ["LIVE_A"],
                },
                {
                    "code": "1000000002",
                    "full_name": "시험도 나군",
                    "status": "review",
                    "review_candidate_ids": ["candidate-b"],
                },
            ],
        },
    )

    errors, _, _ = validate(
        queue_path=queue,
        coverage_path=coverage,
        target_dir=target_dir,
        production_providers_path=production,
        production_evidence_path=production_evidence,
        operational_path=operational,
        expected_municipalities=2,
        require_complete=False,
    )

    assert any("live_validate_new target must lock" in error for error in errors)
    assert any("coverage status must be promoted" in error for error in errors)


def test_rejects_operational_schema_scope_duplicates_and_bad_result_consistency(
    tmp_path: Path,
) -> None:
    queue = tmp_path / "queue.yaml"
    coverage = tmp_path / "coverage.yaml"
    target_dir = tmp_path / "targets"
    production = tmp_path / "production.yaml"
    production_evidence = tmp_path / "production_evidence.yaml"
    operational = tmp_path / "operational.yaml"
    url = "https://duplicate.example/program/list?page=1"
    write_yaml(queue, queue_document())
    write_yaml(
        target_dir / "targets.yaml",
        {
            "version": 1,
            "targets": [
                target("DUPLICATE_A", url),
                target("DUPLICATE_B", "https://duplicate.example/program/list?page=2"),
            ],
        },
    )
    write_yaml(production, production_document())
    write_yaml(production_evidence, production_evidence_document())
    first = operational_entry("DUPLICATE_A", url, municipality("1000000001"))
    first.update(
        {
            "normalized_url": "https://wrong.example/list",
            "action": "unsupported",
            "validation_outcome": "collected",
            "validated_at": "2026-07-18",
            "parser": "",
            "row_count": 0,
            "no_current_data": True,
        }
    )
    first["municipalities"][0] = {**first["municipalities"][0], "full_name": "틀린 이름"}
    second = operational_entry(
        "DUPLICATE_B",
        "https://duplicate.example/program/list?page=2",
        municipality("1000000002"),
    )
    second["normalized_url"] = "https://wrong.example/list"
    write_yaml(operational, operational_document([first, second]))
    write_yaml(
        coverage,
        {
            "version": 1,
            "municipalities": [
                {
                    "code": "1000000001",
                    "full_name": "시험도 가시",
                    "status": "no_candidate",
                },
                {
                    "code": "1000000002",
                    "full_name": "시험도 나군",
                    "status": "no_candidate",
                },
            ],
        },
    )

    errors, _, summary = validate(
        queue_path=queue,
        coverage_path=coverage,
        target_dir=target_dir,
        production_providers_path=production,
        production_evidence_path=production_evidence,
        operational_path=operational,
        expected_municipalities=2,
        require_complete=False,
    )

    assert any("normalized_url mismatch" in error for error in errors)
    assert any("unsupported action" in error for error in errors)
    assert any("collected requires row_count>0" in error for error in errors)
    assert any("validated_at must be" in error for error in errors)
    assert any("parser must be" in error for error in errors)
    assert any("full_name mismatch" in error for error in errors)
    assert any("duplicate URL scope" in error for error in errors)
    assert summary["operational_scope_duplicates"] == 1


def test_manual_exclusion_reason_requires_auditable_official_evidence(tmp_path: Path) -> None:
    queue = tmp_path / "queue.yaml"
    coverage = tmp_path / "coverage.yaml"
    target_dir = tmp_path / "targets"
    production = tmp_path / "production.yaml"
    production_evidence = tmp_path / "production_evidence.yaml"
    operational = tmp_path / "operational.yaml"
    target_dir.mkdir()

    rows = []
    for item in queue_document()["municipalities"]:
        rows.append(
            {
                **item,
                "status": "no_candidate",
                "candidate_count": 0,
                "eligible_candidate_count": 0,
                "excluded_candidate_count": 0,
                "exclusion_reasons": {},
                "owner_providers": [],
                "promoted_providers": [],
                "yaml_owner_providers": [],
                "review_candidate_ids": [],
                "evidence": [],
            }
        )
    rows[0].update(
        {
            "candidate_count": 1,
            "excluded_candidate_count": 1,
            "exclusion_reasons": {"stale_information_only": 1},
        }
    )
    coverage_document = {
        "version": 1,
        "summary": {"municipalities": 2, "by_status": {"no_candidate": 2}},
        "municipalities": rows,
    }
    write_yaml(queue, queue_document())
    write_yaml(coverage, coverage_document)
    write_yaml(production, production_document())
    write_yaml(production_evidence, production_evidence_document())
    write_yaml(operational, operational_document())

    errors, _, _ = validate(
        queue_path=queue,
        coverage_path=coverage,
        target_dir=target_dir,
        production_providers_path=production,
        production_evidence_path=production_evidence,
        operational_path=operational,
        expected_municipalities=2,
        require_complete=False,
    )
    assert any("manual exclusion reasons require official_manual_exclusion evidence" in error for error in errors)

    url = "https://guide.go.kr/course/list.do?key=350"
    rows[0]["evidence"] = [
        {
            "kind": "official_manual_exclusion",
            "ownership_basis": "official_page_manual_verification",
            "candidate_url": url,
            "normalized_url": normalized_scope_url(url),
            "exclusion_reason": "stale_information_only",
            "evidence_urls": [url],
            "evidence_note": "기준일이 오래됐고 신청 URL과 접수상태가 없음",
        }
    ]
    write_yaml(coverage, coverage_document)
    errors, _, _ = validate(
        queue_path=queue,
        coverage_path=coverage,
        target_dir=target_dir,
        production_providers_path=production,
        production_evidence_path=production_evidence,
        operational_path=operational,
        expected_municipalities=2,
        require_complete=False,
    )
    assert errors == []
