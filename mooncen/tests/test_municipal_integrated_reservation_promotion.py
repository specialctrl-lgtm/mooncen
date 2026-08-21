from __future__ import annotations

import sys
import types
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from tools.promote_municipal_integrated_reservation_targets import (
    apply_operational_entries_to_manifests,
    build_manifests,
    hydrate_live_validations_from_operational_entries,
    live_validate_candidates,
    merge_operational_entries,
    merge_promoted_targets,
    normalized_duplicate_url,
    operational_entries,
    preserve_live_validations,
    rebuild_target_index,
    semantic_course_title_rejection_reason,
    sync_live_validation_evidence_to_coverage,
)


def municipality(code: str, full_name: str) -> dict:
    sido, sigungu = full_name.split(" ", 1)
    return {"code": code, "sido": sido, "sigungu": sigungu, "full_name": full_name}


def candidate_row(code: str, full_name: str, url: str | None) -> dict:
    values = []
    if url:
        values.append(
            {
                "status": "candidate",
                "score": 12,
                "title": full_name + " 통합예약",
                "url": url,
                "query": full_name + " 통합예약",
                "query_category_id": "integrated_reservation",
            }
        )
    return {**municipality(code, full_name), "candidates": values}


def target(provider: str, url: str, status: str = "ready") -> dict:
    return {
        "provider": provider,
        "name": provider,
        "branch": provider,
        "url": url,
        "crawler_status": status,
        "priority": 2,
        "source": "test",
        "_target_file": "test.yaml",
        "_target_index": 1,
    }


def test_normalized_url_drops_pagination_but_preserves_semantic_menu_parameters() -> None:
    first = normalized_duplicate_url("https://Example.go.kr/course/list.do?mid=abc&currPage=1&kind=edu")
    second = normalized_duplicate_url("https://example.go.kr/course/list.do?kind=edu&mid=abc&currPage=9")
    other_menu = normalized_duplicate_url("https://example.go.kr/course/list.do?kind=edu&mid=xyz")

    assert first == second
    assert first != other_menu


def test_preserve_live_validations_requires_exact_candidate_identity() -> None:
    identity = {
        "candidate_id": "MUNI_IR_EXACT",
        "provider": "MUNI_EXACT",
        "normalized_url": "https://exact.go.kr/course",
    }
    prior_validation = {
        "checked_at": "2026-07-19T12:00:00+09:00",
        "parser": "exact_parser+detail",
        "row_count": 7,
        "pages": 3,
        "detail_pages": 7,
        "semantic_rejection_reasons": {"navigation_title": 1},
    }
    exact = {**identity, "status": "review"}
    changed_id = {**identity, "candidate_id": "MUNI_IR_CHANGED"}
    changed_provider = {**identity, "provider": "MUNI_CHANGED"}
    changed_url = {**identity, "normalized_url": "https://exact.go.kr/other"}
    review = {"candidates": [exact, changed_id, changed_provider, changed_url]}
    existing = {
        "candidates": [
            {
                **identity,
                "status": "promoted",
                "live_validation": prior_validation,
                "operational_entries": [{"provider": "MUNI_EXACT"}],
            }
        ]
    }

    assert preserve_live_validations(review, existing) == 1

    assert exact["live_validation"] == prior_validation
    assert exact["live_validation"] is not prior_validation
    assert exact["status"] == "review"
    assert "operational_entries" not in exact
    assert all("live_validation" not in row for row in (changed_id, changed_provider, changed_url))
    prior_validation["semantic_rejection_reasons"]["navigation_title"] = 99
    assert exact["live_validation"]["semantic_rejection_reasons"] == {"navigation_title": 1}


def test_preserve_live_validations_rejects_malformed_or_ambiguous_prior_evidence() -> None:
    duplicate_identity = {
        "candidate_id": "MUNI_IR_DUPLICATE",
        "provider": "MUNI_DUPLICATE",
        "normalized_url": "https://duplicate.go.kr/course",
    }
    invalid_identity = {
        "candidate_id": "MUNI_IR_INVALID",
        "provider": "MUNI_INVALID",
        "normalized_url": "https://invalid.go.kr/course",
    }
    review = {"candidates": [dict(duplicate_identity), dict(invalid_identity)]}
    existing = {
        "candidates": [
            None,
            {"candidate_id": "", "provider": "", "normalized_url": "", "live_validation": {}},
            {**invalid_identity, "live_validation": ["not", "a", "mapping"]},
            {**duplicate_identity, "live_validation": {"row_count": 1}},
            {**duplicate_identity, "live_validation": {"row_count": 2}},
        ]
    }

    assert preserve_live_validations(review, existing) == 0
    assert all("live_validation" not in row for row in review["candidates"])


def test_hydrate_live_validation_requires_consistent_operational_and_target_quality() -> None:
    normalized_url = "https://recover.go.kr/course"
    review = {
        "candidates": [
            {
                "candidate_id": "MUNI_IR_RECOVER",
                "provider": "MUNI_RECOVER",
                "normalized_url": normalized_url,
            },
            {
                "candidate_id": "MUNI_IR_KEEP",
                "provider": "MUNI_KEEP",
                "normalized_url": "https://keep.go.kr/course",
                "live_validation": None,
            },
        ]
    }
    entries = [
        {
            "provider": "MUNI_RECOVER",
            "normalized_url": normalized_url,
            "target_url": normalized_url + "?page=9",
            "validated_at": "2026-07-18T09:30:00+09:00",
            "parser": "recover_parser+detail",
            "row_count": 12,
            "no_current_data": False,
        },
        {
            "provider": "MUNI_KEEP",
            "normalized_url": "https://keep.go.kr/course",
            "target_url": "https://keep.go.kr/course",
            "validated_at": "2026-07-18T09:31:00+09:00",
            "parser": "keep_parser",
            "row_count": 1,
            "no_current_data": False,
        },
    ]
    targets = [
        {
            "provider": "MUNI_RECOVER",
            "url": normalized_url + "?page=1",
            "last_quality": {
                "parser": "recover_parser+detail",
                "collected": 12,
                "pages": 4,
                "detail_pages": 12,
                "snapshot_complete": True,
            },
        },
        {
            "provider": "MUNI_KEEP",
            "url": "https://keep.go.kr/course",
            "last_quality": {
                "parser": "keep_parser",
                "collected": 1,
                "no_current_data": False,
            },
        },
    ]

    assert hydrate_live_validations_from_operational_entries(review, entries, targets) == 1
    assert review["candidates"][0]["live_validation"] == {
        "checked_at": "2026-07-18T09:30:00+09:00",
        "parser": "recover_parser+detail",
        "row_count": 12,
        "no_current_data": False,
        "error": "",
        "raw_row_count": 12,
        "semantic_rejected_row_count": 0,
        "semantic_rejection_reasons": {},
        "semantic_rejected_title_samples": [],
        "semantic_quality_passed": True,
        "pages": 4,
        "detail_pages": 12,
    }
    assert review["candidates"][1]["live_validation"] is None


def test_hydrate_live_validation_fails_closed_on_cross_check_mismatches() -> None:
    normalized_url = "https://recover.go.kr/course"
    base_review = {
        "candidates": [
            {
                "candidate_id": "MUNI_IR_RECOVER",
                "provider": "MUNI_RECOVER",
                "normalized_url": normalized_url,
            }
        ]
    }
    base_entry = {
        "provider": "MUNI_RECOVER",
        "normalized_url": normalized_url,
        "target_url": normalized_url,
        "validated_at": "2026-07-18T09:30:00+09:00",
        "parser": "recover_parser",
        "row_count": 12,
        "no_current_data": False,
    }
    base_target = {
        "provider": "MUNI_RECOVER",
        "url": normalized_url,
        "last_quality": {
            "parser": "recover_parser",
            "collected": 12,
            "no_current_data": False,
            "pages": 2,
            "detail_pages": 3,
        },
    }
    mismatch_cases = []
    wrong_parser = deepcopy(base_target)
    wrong_parser["last_quality"]["parser"] = "other_parser"
    mismatch_cases.append(([base_entry], [wrong_parser]))
    wrong_count = deepcopy(base_target)
    wrong_count["last_quality"]["collected"] = 11
    mismatch_cases.append(([base_entry], [wrong_count]))
    wrong_no_current_data = deepcopy(base_target)
    wrong_no_current_data["last_quality"]["no_current_data"] = True
    mismatch_cases.append(([base_entry], [wrong_no_current_data]))
    malformed_no_current_data = deepcopy(base_target)
    malformed_no_current_data["last_quality"]["no_current_data"] = 0
    mismatch_cases.append(([base_entry], [malformed_no_current_data]))
    incomplete_snapshot = deepcopy(base_target)
    incomplete_snapshot["last_quality"]["snapshot_complete"] = False
    mismatch_cases.append(([base_entry], [incomplete_snapshot]))
    wrong_provider = {**base_entry, "provider": "MUNI_OTHER"}
    mismatch_cases.append(([wrong_provider], [base_target]))
    wrong_target_url = {**base_entry, "target_url": "https://recover.go.kr/other"}
    mismatch_cases.append(([wrong_target_url], [base_target]))
    mismatch_cases.append(([base_entry], [base_target, deepcopy(base_target)]))

    for entries, targets in mismatch_cases:
        review = deepcopy(base_review)
        assert hydrate_live_validations_from_operational_entries(review, entries, targets) == 0
        assert "live_validation" not in review["candidates"][0]


def test_sync_live_validation_evidence_to_coverage_upserts_by_candidate_id() -> None:
    candidate_value = "MUNI_IR_SYNC"
    municipality_row = municipality("1000000000", "Test Province Test City")
    stale = {
        "kind": "live_validation",
        "ownership_basis": "live_crawl_probe",
        "candidate_id": candidate_value,
        "provider": "MUNI_SYNC",
        "candidate_url": "https://sync.go.kr/course",
        "normalized_url": "https://sync.go.kr/course",
        "row_count": 1,
    }
    unrelated = {"kind": "official_manual_candidate", "candidate_id": candidate_value}
    coverage = {
        "municipalities": [
            {
                **municipality_row,
                "evidence": [unrelated, stale, deepcopy(stale)],
            }
        ]
    }
    validation = {
        "checked_at": "2026-07-20T10:00:00+09:00",
        "parser": "sync_parser+detail",
        "row_count": 9,
        "no_current_data": False,
        "pages": 2,
        "detail_pages": 9,
    }
    review = {
        "candidates": [
            {
                "candidate_id": candidate_value,
                "provider": "MUNI_SYNC",
                "url": "https://sync.go.kr/course?page=1",
                "normalized_url": "https://sync.go.kr/course",
                "municipalities": [municipality_row, municipality_row],
                "live_validation": validation,
            }
        ]
    }

    assert sync_live_validation_evidence_to_coverage(coverage, review) == 1
    assert sync_live_validation_evidence_to_coverage(coverage, review) == 1

    evidence_rows = coverage["municipalities"][0]["evidence"]
    live_rows = [row for row in evidence_rows if row.get("kind") == "live_validation"]
    assert len(live_rows) == 1
    assert unrelated in evidence_rows
    assert live_rows[0] == {
        "kind": "live_validation",
        "ownership_basis": "live_crawl_probe",
        "candidate_id": candidate_value,
        "provider": "MUNI_SYNC",
        "candidate_url": "https://sync.go.kr/course?page=1",
        "normalized_url": "https://sync.go.kr/course",
        **validation,
    }


def test_only_exact_working_scheduled_owner_with_positive_production_evidence_is_covered() -> None:
    municipalities = [
        municipality("1000000000", "테스트도 기존시"),
        municipality("2000000000", "테스트도 차단군"),
        municipality("3000000000", "테스트도 결과없음구"),
    ]
    candidates = [
        candidate_row("1000000000", "테스트도 기존시", "https://one.go.kr/course?page=7"),
        candidate_row("2000000000", "테스트도 차단군", "https://two.go.kr/course"),
        candidate_row("3000000000", "테스트도 결과없음구", None),
    ]
    targets = [
        target("ACTIVE_OWNER", "https://one.go.kr/course?page=1"),
        target("BLOCKED_OWNER", "https://two.go.kr/course", "blocked"),
    ]

    coverage, review = build_manifests(
        {"municipalities": municipalities},
        {"results": candidates},
        targets,
        {"ACTIVE_OWNER", "BLOCKED_OWNER"},
        {
            "ACTIVE_OWNER": {"provider": "ACTIVE_OWNER", "active_course_count": 3},
            "BLOCKED_OWNER": {"provider": "BLOCKED_OWNER", "active_course_count": 9},
        },
    )

    by_code = {row["code"]: row for row in coverage["municipalities"]}
    assert by_code["1000000000"]["status"] == "covered_by_existing"
    assert by_code["1000000000"]["owner_providers"] == ["ACTIVE_OWNER"]
    assert by_code["2000000000"]["status"] == "review"
    assert by_code["2000000000"]["owner_providers"] == []
    assert any(evidence["kind"] == "ignored_disabled_owner" for evidence in by_code["2000000000"]["evidence"])
    assert by_code["3000000000"]["status"] == "no_candidate"
    assert len(review["candidates"]) == 1
    assert review["candidates"][0]["recommended_action"] == "live_validate_new"


def test_production_evidence_active_urls_do_not_cover_an_unvalidated_sibling_target() -> None:
    experience = municipality("1000000000", "테스트도 체험시")
    education = municipality("2000000000", "테스트도 교육구")
    provider = "SHARED_RESERVATION"
    experience_url = "https://shared.go.kr/expr/list"
    education_url = "https://shared.go.kr/lect/list"

    coverage, review = build_manifests(
        {"municipalities": [experience, education]},
        {
            "results": [
                candidate_row(experience["code"], experience["full_name"], experience_url),
                candidate_row(education["code"], education["full_name"], education_url),
            ]
        },
        [target(provider, experience_url), target(provider, education_url)],
        {provider},
        {
            provider: {
                "provider": provider,
                "active_course_count": 17,
                "active_urls": [experience_url],
            }
        },
    )

    by_code = {row["code"]: row for row in coverage["municipalities"]}
    assert by_code[experience["code"]]["status"] == "covered_by_existing"
    assert by_code[education["code"]]["status"] == "review"
    assert len(review["candidates"]) == 1
    assert review["candidates"][0]["provider"] == provider
    assert review["candidates"][0]["recommended_action"] == "schedule_existing"


def test_yaml_owner_not_scheduled_is_reused_for_review_instead_of_duplicated() -> None:
    muni = municipality("1000000000", "테스트도 검토시")
    coverage, review = build_manifests(
        {"municipalities": [muni]},
        {"results": [candidate_row(muni["code"], muni["full_name"], "https://review.go.kr/course")]},
        [target("EXISTING_UNSCHEDULED", "https://review.go.kr/course")],
        set(),
        {},
    )

    assert coverage["municipalities"][0]["status"] == "review"
    assert coverage["municipalities"][0]["yaml_owner_providers"] == ["EXISTING_UNSCHEDULED"]
    assert review["candidates"][0]["provider"] == "EXISTING_UNSCHEDULED"
    assert review["candidates"][0]["recommended_action"] == "schedule_existing"


def test_explicit_ops_target_mapping_survives_without_search_candidate() -> None:
    mapped = municipality("1000000000", "Test-do Mapped-si")
    blocked = municipality("2000000000", "Test-do Blocked-gun")
    implicit = municipality("3000000000", "Test-do Implicit-gu")
    url = "https://mapped.go.kr/experience/list.do"
    exact_owner = {
        **target("EXACT_MAPPED_OWNER", url),
        "ops_scopes": ["experience"],
        "municipality_code": mapped["code"],
        "row_municipality_codes": [mapped["code"]],
        "covered_municipalities": [mapped],
    }
    blocked_owner = {
        **target("BLOCKED_MAPPED_OWNER", "https://blocked.go.kr/experience", "blocked"),
        "ops_scopes": ["experience"],
        "covered_municipalities": [blocked],
    }
    implicit_owner = {
        **target("IMPLICIT_OWNER", "https://implicit.go.kr/experience"),
        "covered_municipalities": [implicit],
    }

    coverage, review = build_manifests(
        {"municipalities": [mapped, blocked, implicit]},
        {
            "results": [
                candidate_row(row["code"], row["full_name"], None)
                for row in (mapped, blocked, implicit)
            ]
        },
        [exact_owner, blocked_owner, implicit_owner],
        set(),
        {},
    )

    by_code = {row["code"]: row for row in coverage["municipalities"]}
    mapped_row = by_code[mapped["code"]]
    assert mapped_row["status"] == "no_candidate"
    assert mapped_row["owner_providers"] == []
    assert mapped_row["yaml_owner_providers"] == ["EXACT_MAPPED_OWNER"]
    evidence = next(
        item for item in mapped_row["evidence"] if item["kind"] == "exact_active_url"
    )
    assert evidence["provider"] == "EXACT_MAPPED_OWNER"
    assert evidence["candidate_url"] == url
    assert evidence["target_url"] == url
    assert evidence["normalized_url"] == url
    assert evidence["ownership_basis"] == "yaml_working_not_scheduled"
    assert by_code[blocked["code"]]["yaml_owner_providers"] == []
    assert by_code[implicit["code"]]["yaml_owner_providers"] == []
    assert review["candidates"] == []


def test_declared_ownership_alias_is_covered_by_existing_owner_without_review() -> None:
    muni = municipality("1120000000", "Seoul Seongdong-gu")
    candidate_url = "http://dokseodang.sd.go.kr/"
    canonical_url = "https://www.sd.go.kr/booking/webEduList2.do?key=4916"
    provider = "MUNI_DOKSEODANG_SD_GO_KR_A8C20229"
    owner = {
        **target(provider, canonical_url),
        "ownership_aliases": [
            candidate_url,
            "https://dokseodang.sd.go.kr/product/list.php?ca_id=10",
        ],
    }

    coverage, review = build_manifests(
        {"municipalities": [muni]},
        {"results": [candidate_row(muni["code"], muni["full_name"], candidate_url)]},
        [owner],
        {provider},
        {provider: {"provider": provider, "active_course_count": 22}},
    )

    row = coverage["municipalities"][0]
    assert row["status"] == "covered_by_existing"
    assert row["owner_providers"] == [provider]
    assert row["review_candidate_ids"] == []
    assert review["candidates"] == []
    evidence = next(item for item in row["evidence"] if item["kind"] == "ownership_alias")
    assert evidence["candidate_id"] == "MUNI_IR_A462D3FEDF14"
    assert evidence["provider"] == provider
    assert evidence["candidate_url"] == candidate_url
    assert evidence["target_url"] == canonical_url
    assert evidence["ownership_alias"] == candidate_url
    assert evidence["production_active_course_count"] == 22


def test_one_normalized_candidate_is_shared_by_multiple_municipalities() -> None:
    first = municipality("1000000000", "테스트도 첫째시")
    second = municipality("2000000000", "테스트도 둘째군")
    coverage, review = build_manifests(
        {"municipalities": [first, second]},
        {
            "results": [
                candidate_row(first["code"], first["full_name"], "https://shared.go.kr/course?page=1"),
                candidate_row(second["code"], second["full_name"], "https://shared.go.kr/course?page=2"),
            ]
        },
        [],
        set(),
        {},
    )

    assert len(review["candidates"]) == 1
    assert {row["code"] for row in review["candidates"][0]["municipalities"]} == {
        first["code"],
        second["code"],
    }
    review_id = review["candidates"][0]["candidate_id"]
    assert all(row["review_candidate_ids"] == [review_id] for row in coverage["municipalities"])


def test_official_manual_override_adds_review_evidence_without_claiming_production_coverage() -> None:
    muni = municipality("1000000000", "테스트도 신설구")
    coverage, review = build_manifests(
        {"municipalities": [muni]},
        {"results": [candidate_row(muni["code"], muni["full_name"], None)]},
        [],
        set(),
        {},
        {
            "checked_at": "2026-07-17T00:00:00+09:00",
            "source": "official_page_manual_verification",
            "municipalities": [
                {
                    **muni,
                    "candidates": [
                        {
                            "status": "candidate",
                            "score": 100,
                            "title": "공식 교육 목록",
                            "url": "https://new.go.kr/course/list.do",
                            "evidence_urls": ["https://new.go.kr/course/1"],
                            "evidence_note": "현재 강좌 확인",
                        }
                    ],
                }
            ],
        },
    )

    row = coverage["municipalities"][0]
    assert row["status"] == "review"
    assert any(evidence["kind"] == "official_manual_candidate" for evidence in row["evidence"])
    assert review["candidates"][0]["manual_override"] is True
    assert review["candidates"][0]["official_evidence_urls"] == ["https://new.go.kr/course/1"]


def test_official_manual_override_preserves_explicit_provider_without_active_owner() -> None:
    muni = municipality("1000000000", "테스트도 신설구")
    url = "https://new.go.kr/course/list.do"
    _coverage, review = build_manifests(
        {"municipalities": [muni]},
        {"results": [candidate_row(muni["code"], muni["full_name"], None)]},
        [],
        set(),
        {},
        {
            "checked_at": "2026-07-17T00:00:00+09:00",
            "source": "official_page_manual_verification",
            "municipalities": [
                {
                    **muni,
                    "candidates": [
                        {
                            "status": "candidate",
                            "score": 100,
                            "provider": "EXPLICIT_OWNER",
                            "title": "공식 교육 목록",
                            "url": url,
                            "evidence_urls": [url],
                            "evidence_note": "현재 강좌 확인",
                        }
                    ],
                }
            ],
        },
    )

    assert review["candidates"][0]["provider"] == "EXPLICIT_OWNER"
    assert review["candidates"][0]["recommended_action"] == "live_validate_new"


def test_official_manual_overrides_reject_conflicting_providers_for_same_url() -> None:
    first = municipality("1000000000", "테스트도 첫째시")
    second = municipality("2000000000", "테스트도 둘째군")
    url = "https://shared.go.kr/course/list.do"
    overrides = {
        "checked_at": "2026-07-17T00:00:00+09:00",
        "source": "official_page_manual_verification",
        "municipalities": [
            {
                **municipality_row,
                "candidates": [
                    {
                        "status": "candidate",
                        "score": 100,
                        "provider": provider,
                        "title": "공식 교육 목록",
                        "url": url,
                        "evidence_urls": [url],
                        "evidence_note": "현재 강좌 확인",
                    }
                ],
            }
            for municipality_row, provider in ((first, "FIRST_OWNER"), (second, "SECOND_OWNER"))
        ],
    }

    with pytest.raises(ValueError, match="manual candidate providers disagree"):
        build_manifests(
            {"municipalities": [first, second]},
            {
                "results": [
                    candidate_row(first["code"], first["full_name"], None),
                    candidate_row(second["code"], second["full_name"], None),
                ]
            },
            [],
            set(),
            {},
            overrides,
        )


def test_official_manual_override_rejects_active_owner_provider_mismatch() -> None:
    muni = municipality("1000000000", "테스트도 신설구")
    url = "https://new.go.kr/course/list.do"
    overrides = {
        "checked_at": "2026-07-17T00:00:00+09:00",
        "source": "official_page_manual_verification",
        "municipalities": [
            {
                **muni,
                "candidates": [
                    {
                        "status": "candidate",
                        "score": 100,
                        "provider": "EXPLICIT_OWNER",
                        "title": "공식 교육 목록",
                        "url": url,
                        "evidence_urls": [url],
                        "evidence_note": "현재 강좌 확인",
                    }
                ],
            }
        ],
    }

    with pytest.raises(ValueError, match="disagrees with active owner providers"):
        build_manifests(
            {"municipalities": [muni]},
            {"results": [candidate_row(muni["code"], muni["full_name"], None)]},
            [target("ACTIVE_OWNER", url)],
            set(),
            {},
            overrides,
        )


def test_official_manual_exclusion_suppresses_search_candidate_and_disabled_owner() -> None:
    muni = municipality("1000000000", "테스트도 안내군")
    url = "https://guide.go.kr/course/list.do?key=350"
    provider = "STALE_INFORMATION_OWNER"
    coverage, review = build_manifests(
        {"municipalities": [muni]},
        {"results": [candidate_row(muni["code"], muni["full_name"], url)]},
        [target(provider, url, "no_current_data")],
        set(),
        {},
        {
            "checked_at": "2026-07-18T00:00:00+09:00",
            "source": "official_page_manual_verification",
            "municipalities": [
                {
                    **muni,
                    "candidates": [
                        {
                            "status": "excluded",
                            "exclusion_reason": "stale_information_only",
                            "provider": provider,
                            "score": 100,
                            "title": "오래된 프로그램 안내",
                            "url": url,
                            "evidence_urls": [url],
                            "evidence_note": "기준일이 오래됐고 신청 URL과 접수상태가 없음",
                        }
                    ],
                }
            ],
        },
    )

    row = coverage["municipalities"][0]
    assert row["status"] == "no_candidate"
    assert row["candidate_count"] == 2
    assert row["eligible_candidate_count"] == 0
    assert row["excluded_candidate_count"] == 2
    assert row["exclusion_reasons"] == {"stale_information_only": 2}
    assert row["review_candidate_ids"] == []
    evidence = next(item for item in row["evidence"] if item["kind"] == "official_manual_exclusion")
    assert evidence["exclusion_reason"] == "stale_information_only"
    assert evidence["provider"] == provider
    assert evidence["disabled_owner_providers"] == [provider]
    assert evidence["target_statuses"] == ["no_current_data"]
    assert review["candidates"] == []


def test_live_validation_promotes_only_positive_new_target_with_locked_education_metadata(monkeypatch) -> None:
    muni = municipality("1000000000", "테스트도 검증시")
    coverage, review = build_manifests(
        {"municipalities": [muni]},
        {"results": [candidate_row(muni["code"], muni["full_name"], "https://live.go.kr/course")]},
        [],
        set(),
        {},
    )

    fake_module = types.ModuleType("Crawler.Crawler_MunicipalYaml")

    class FakeTarget:
        def __init__(self, **values):
            self.__dict__.update(values)

    def fake_collect(_target, **_kwargs):
        return ([{"title": "검증 강좌", "raw_url": "https://live.go.kr/course/1"}], "test_parser", {"pages": 1})

    fake_module.CrawlTarget = FakeTarget
    fake_module.collect_from_url = fake_collect
    monkeypatch.setitem(sys.modules, "Crawler.Crawler_MunicipalYaml", fake_module)

    promoted, operational = live_validate_candidates(review, coverage, [], limit=1)

    assert len(promoted) == 1
    assert len(operational) == 1
    assert operational[0]["validation_outcome"] == "collected"
    assert operational[0]["row_count"] == 1
    assert promoted[0]["crawler_status"] == "ready"
    assert promoted[0]["collection_type"] == "test_parser"
    assert promoted[0]["collection_category"] == "공공예약"
    assert promoted[0]["domain_category"] == "교육·강좌"
    assert promoted[0]["service_group"] == "공공강좌"
    assert promoted[0]["service_group_policy"] == "locked"
    apply_operational_entries_to_manifests(coverage, review, operational)
    assert coverage["municipalities"][0]["status"] == "promoted"


def test_manual_shared_candidate_validates_all_exact_configured_municipalities(monkeypatch) -> None:
    first = municipality("1000000000", "테스트도 첫째시")
    second = municipality("2000000000", "테스트도 둘째군")
    url = "https://shared.go.kr/course"
    owner = {
        **target("SHARED_OWNER", url),
        "covered_municipalities": [first, second],
    }
    coverage, review = build_manifests(
        {"municipalities": [first, second]},
        {
            "results": [
                candidate_row(first["code"], first["full_name"], url),
                candidate_row(second["code"], second["full_name"], url),
            ]
        },
        [owner],
        set(),
        {},
    )
    review["candidates"][0]["manual_override"] = True

    fake_module = types.ModuleType("Crawler.Crawler_MunicipalYaml")

    class FakeTarget:
        def __init__(self, **values):
            self.__dict__.update(values)

    def fake_collect(_target, **_kwargs):
        return ([{"title": "공유 교육 강좌", "raw_url": url + "/1"}], "test_parser", {"pages": 1})

    fake_module.CrawlTarget = FakeTarget
    fake_module.collect_from_url = fake_collect
    monkeypatch.setitem(sys.modules, "Crawler.Crawler_MunicipalYaml", fake_module)

    _promoted, operational = live_validate_candidates(review, coverage, [owner], limit=1)

    assert len(operational) == 1
    assert {row["code"] for row in operational[0]["municipalities"]} == {
        first["code"],
        second["code"],
    }
    apply_operational_entries_to_manifests(coverage, review, operational)
    assert all(row["status"] == "promoted" for row in coverage["municipalities"])


def test_merge_operational_entries_unions_new_shared_municipalities(tmp_path: Path) -> None:
    first = municipality("1000000000", "테스트도 첫째시")
    second = municipality("2000000000", "테스트도 둘째군")
    path = tmp_path / "operational.yaml"

    def entry(municipalities: list[dict]) -> dict:
        return {
            "provider": "SHARED_OWNER",
            "normalized_url": "https://shared.go.kr/course",
            "target_url": "https://shared.go.kr/course",
            "action": "schedule_existing",
            "validation_outcome": "collected",
            "validated_at": "2026-07-18T00:00:00+09:00",
            "parser": "test_parser",
            "row_count": 2,
            "no_current_data": False,
            "municipalities": municipalities,
        }

    path.write_text(
        yaml.safe_dump({"version": 1, "entries": [entry([first])]}, allow_unicode=True),
        encoding="utf-8",
    )
    merge_operational_entries(path, [entry([first, second])])

    merged = operational_entries(yaml.safe_load(path.read_text(encoding="utf-8")))
    assert {row["code"] for row in merged[0]["municipalities"]} == {
        first["code"],
        second["code"],
    }


def test_semantic_course_title_gate_rejects_navigation_site_and_category_headings() -> None:
    rejected = [
        "조종면",
        "학습장소",
        "접수중 강좌",
        "접수예정 강좌",
        "인문교양",
        "공지사항",
        "교육/강좌",
        "게시물 검색",
        "접수/모집",
        "민원안내",
        "구술 전화 신청민원",
        "성인신청",
        "제물포구청",
        "인천광역시 서해구",
        "교육명/장소",
        "선사체험마을",
        "선사체험마을 신청",
        "디지털저장매체파기신청",
        "영천시 평생학습관 메인",
        "달서구의 기관별/강좌별 강의정보를 자세하게 보고",
        "Energetlc Dangjin 당찬 당진 더 큰 당진 거침없는 도약",
        "시민이 행복한 도시, 새로운 검증시",
        "홈페이지에 오신 것을 환영합니다",
        "서비스 접속 대기 중입니다.",
    ]
    for title in rejected:
        assert semantic_course_title_rejection_reason(title), title

    accepted = [
        "2026 여름방학 초등 코딩교실",
        "[가족] 도자기 화분 만들기",
        "시민이 행복한 삶을 위한 마음챙김 교육",
        "미래를 여는 경제 탐험대(초등학교3~6학년)",
    ]
    for title in accepted:
        assert semantic_course_title_rejection_reason(title) == "", title


def test_semantic_course_title_gate_does_not_mistake_course_suffixes_for_regions() -> None:
    for title in ("탁구", "고고장구", "다같이, 걷기에 반할지도"):
        assert semantic_course_title_rejection_reason(title) == "", title

    assert semantic_course_title_rejection_reason("인천광역시 서해구")


def test_semantic_course_title_gate_keeps_long_real_course_titles() -> None:
    titles = (
        "다원 아트 루프 [리뷰 플레이: 마이 시네마 가면]",
        "[느린학습자 학습단비] [장기과정] (초등3~6학년) 내가 만드는 ‘나’ 사용 설명서 (장소: 화성시평생학습관)",
        "[느린학습자 학습단비] [장기과정] (중고등부) 무대 위, 나를 향해 걷다. 1반 (장소: 화성시평생학습관)",
        "[느린학습자 학습단비] [장기과정] (중고등부) 무대 위, 나를 향해 걷다. 2반 (장소: 화성시평생학습관)",
        "AI시대 스마트폰 기초 & 인공지능-YDP미래평생학습관",
        "(특강) 온라인 책 만들기-북크리에이터-YDP미래평생학습관",
    )
    for title in titles:
        assert semantic_course_title_rejection_reason(title) == "", title


def test_live_validation_does_not_promote_when_semantic_gate_rejects_every_row(monkeypatch) -> None:
    muni = municipality("1000000000", "경기도 검증시")
    coverage, review = build_manifests(
        {"municipalities": [muni]},
        {"results": [candidate_row(muni["code"], muni["full_name"], "https://noise.go.kr/course")]},
        [],
        set(),
        {},
    )
    fake_module = types.ModuleType("Crawler.Crawler_MunicipalYaml")

    class FakeTarget:
        def __init__(self, **values):
            self.__dict__.update(values)

    fake_module.CrawlTarget = FakeTarget
    fake_module.collect_from_url = lambda _target, **_kwargs: (
        [
            {"title": "공지사항", "raw_url": "https://noise.go.kr/course#notice"},
            {"title": "접수중 강좌", "raw_url": "https://noise.go.kr/course#open"},
            {"title": "영천시 평생학습관 메인", "raw_url": "https://noise.go.kr/course#main"},
        ],
        "generic_card",
        {"pages": 1},
    )
    monkeypatch.setitem(sys.modules, "Crawler.Crawler_MunicipalYaml", fake_module)

    promoted, operational = live_validate_candidates(review, coverage, [], limit=1)

    assert promoted == []
    assert operational == []
    candidate = review["candidates"][0]
    assert candidate["status"] == "needs_parser"
    assert candidate["live_validation"]["raw_row_count"] == 3
    assert candidate["live_validation"]["row_count"] == 0
    assert candidate["live_validation"]["semantic_rejected_row_count"] == 3
    assert candidate["live_validation"]["semantic_quality_passed"] is False


def test_live_validation_counts_only_semantically_valid_course_rows(monkeypatch) -> None:
    muni = municipality("1000000000", "경기도 검증시")
    coverage, review = build_manifests(
        {"municipalities": [muni]},
        {"results": [candidate_row(muni["code"], muni["full_name"], "https://mixed.go.kr/course")]},
        [],
        set(),
        {},
    )
    fake_module = types.ModuleType("Crawler.Crawler_MunicipalYaml")

    class FakeTarget:
        def __init__(self, **values):
            self.__dict__.update(values)

    fake_module.CrawlTarget = FakeTarget
    fake_module.collect_from_url = lambda _target, **_kwargs: (
        [
            {"title": "2026 여름방학 초등 코딩교실", "raw_url": "https://mixed.go.kr/course/1"},
            {"title": "게시물 검색", "raw_url": "https://mixed.go.kr/course#search"},
        ],
        "generic_card",
        {"pages": 1},
    )
    monkeypatch.setitem(sys.modules, "Crawler.Crawler_MunicipalYaml", fake_module)

    promoted, operational = live_validate_candidates(review, coverage, [], limit=1)

    assert len(promoted) == len(operational) == 1
    assert operational[0]["row_count"] == 1
    validation = review["candidates"][0]["live_validation"]
    assert validation["raw_row_count"] == 2
    assert validation["row_count"] == 1
    assert validation["semantic_rejected_row_count"] == 1
    assert validation["semantic_quality_passed"] is True


def test_live_validation_records_fail_closed_collection_error(monkeypatch) -> None:
    muni = municipality("1000000000", "경기도 검증시")
    coverage, review = build_manifests(
        {"municipalities": [muni]},
        {"results": [candidate_row(muni["code"], muni["full_name"], "https://broken.go.kr/course")]},
        [],
        set(),
        {},
    )
    fake_module = types.ModuleType("Crawler.Crawler_MunicipalYaml")

    class FakeTarget:
        def __init__(self, **values):
            self.__dict__.update(values)

    fake_module.CrawlTarget = FakeTarget
    fake_module.collect_from_url = lambda _target, **_kwargs: (
        [],
        "test_parser",
        {
            "pages": 25,
            "detail_pages": 0,
            "configured_collection_error": (
                "empty sentinel: UlsanNamguContractError: "
                "post-boundary page is not the official empty sentinel"
            ),
        },
    )
    monkeypatch.setitem(sys.modules, "Crawler.Crawler_MunicipalYaml", fake_module)

    promoted, operational = live_validate_candidates(review, coverage, [], limit=1)

    assert promoted == []
    assert operational == []
    candidate = review["candidates"][0]
    assert candidate["status"] == "needs_parser"
    assert candidate["live_validation"]["error"].startswith("empty sentinel:")
    assert "post-boundary page" in candidate["live_validation"]["error"]


def test_promoted_target_merge_is_idempotent_and_rebuilds_index(tmp_path: Path) -> None:
    target_dir = tmp_path / "crawl_targets"
    target_dir.mkdir()
    target_path = target_dir / "municipal_integrated_reservation.yaml"
    row = {
        "provider": "MUNI_TEST_12345678",
        "name": "테스트",
        "branch": "테스트도 테스트시",
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": "generic_auto_discovery",
        "crawler_status": "partial",
        "priority": 2,
        "url": "https://test.go.kr/course",
        "source": "municipal_integrated_reservation_promotion",
        "origin": "live_validated",
        "municipality_code": "1000000000",
        "municipality_full_name": "테스트도 테스트시",
    }

    assert merge_promoted_targets(target_path, [row], []) is True
    assert merge_promoted_targets(target_path, [row], []) is True
    stale_document = yaml.safe_load(target_path.read_text(encoding="utf-8"))
    stale_document["summary"] = {"targets": 999}
    target_path.write_text(
        yaml.safe_dump(stale_document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    rebuild_target_index(target_dir)

    document = yaml.safe_load(target_path.read_text(encoding="utf-8"))
    index = yaml.safe_load((target_dir / "index.yaml").read_text(encoding="utf-8"))
    assert len(document["targets"]) == 1
    assert document["summary"]["targets"] == 1
    assert index["summary"]["targets"] == 1
    assert index["files"][0]["file"] == target_path.name


def test_promoted_target_merge_allows_canonical_retarget_from_deprecated_provider(
    tmp_path: Path,
) -> None:
    target_path = tmp_path / "municipal_integrated_reservation.yaml"
    promoted = {
        "provider": "MUNI_REUSED_PROVIDER",
        "url": "https://example.go.kr/education/course/list.do",
        "crawler_status": "ready",
        "priority": 2,
    }
    deprecated = {
        "provider": "MUNI_REUSED_PROVIDER",
        "url": "http://example.go.kr/old-home.do",
        "crawler_status": "deprecated",
        "manual_action": "delete",
    }

    assert merge_promoted_targets(target_path, [promoted], [deprecated]) is True
    document = yaml.safe_load(target_path.read_text(encoding="utf-8"))
    assert document["targets"] == [promoted]


def test_promoted_target_merge_still_rejects_active_provider_scope_collision(
    tmp_path: Path,
) -> None:
    target_path = tmp_path / "municipal_integrated_reservation.yaml"
    promoted = {
        "provider": "MUNI_REUSED_PROVIDER",
        "url": "https://example.go.kr/education/course/list.do",
        "crawler_status": "ready",
        "priority": 2,
    }
    active = {
        "provider": "MUNI_REUSED_PROVIDER",
        "url": "https://example.go.kr/another-live-ledger.do",
        "crawler_status": "ready",
    }

    with pytest.raises(ValueError, match="provider collision"):
        merge_promoted_targets(target_path, [promoted], [active])
