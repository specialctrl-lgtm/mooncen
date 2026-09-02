from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tools.audit_target_collection_coverage import (
    build_facility_coverage_rows,
    build_site_group_rows,
    canonical_site_key,
    canonical_url_key,
    crawler_run_reports,
    derive_collection_status,
    institution_names_overlap,
    is_duplicate_target_alias,
    merge_latest_reports,
    normalized_name,
    operational_validation_reports,
    summarize,
    target_matches_related_scope,
    url_keys_overlap,
)


def test_url_path_matching_covers_a_facility_homepage_and_course_list() -> None:
    homepage = canonical_url_key("http://lib.gwe.go.kr/kanglib/")
    course_list = canonical_url_key(
        "https://lib.gwe.go.kr/kanglib/egf/bp/lecture/list.do?menuId=100"
    )

    assert homepage == "lib.gwe.go.kr/kanglib"
    assert url_keys_overlap(homepage, course_list)
    assert not url_keys_overlap(homepage, "lib.gwe.go.kr/another-library")


def test_normalized_name_ignores_spacing_and_punctuation() -> None:
    assert normalized_name("국립 과천과학관") == normalized_name("국립-과천 과학관")


def test_shared_hosting_site_key_keeps_the_account_boundary() -> None:
    assert canonical_site_key("https://blog.naver.com/example/223") == "blog.naver.com/example"
    assert canonical_site_key("https://www.bcl.go.kr/site/lecture") == "bcl.go.kr"


def test_institution_name_overlap_accepts_a_municipal_prefix_only() -> None:
    assert institution_names_overlap("부천시립꿈빛도서관", "꿈빛도서관")
    assert institution_names_overlap(
        "울산광역시 북구 · 강동바다도서관",
        "강동바다도서관",
    )
    assert not institution_names_overlap("꿈빛도서관", "꿈빛문화센터")
    assert not institution_names_overlap("중앙", "중앙도서관")


def test_same_site_active_branch_suffix_proves_facility_collection() -> None:
    targets = {
        "BCL": {
            "names": {"부천시도서관"},
            "branches": {"경기도 부천시"},
            "urls": {"https://www.bcl.go.kr/site/reservation/lecture/list"},
        }
    }
    rows = build_facility_coverage_rows(
        [
            {
                "provider": "FACILITY",
                "category": "문화기반시설/공공도서관",
                "name": "부천시립꿈빛도서관",
                "url": "http://www.bcl.go.kr",
            }
        ],
        targets,
        {"BCL": {"active_courses": 9}},
        {"BCL": [{"name": "꿈빛도서관", "address": "", "active_courses": 9}]},
    )

    assert rows[0]["coverage_status"] == "collected"
    assert rows[0]["match_method"] == "active_branch_name_fuzzy"
    assert rows[0]["matched_providers"] == "BCL"
    assert rows[0]["site_key"] == "bcl.go.kr"


def test_same_site_without_a_confirmed_branch_is_not_counted_as_collected() -> None:
    targets = {
        "LIB": {
            "names": {"시립도서관"},
            "branches": {"도시"},
            "urls": {"https://library.example.kr/lecture/list"},
        }
    }
    rows = build_facility_coverage_rows(
        [
            {
                "provider": "FACILITY",
                "category": "문화기반시설/공공도서관",
                "name": "새로운도서관",
                "url": "https://library.example.kr/new",
            }
        ],
        targets,
        {"LIB": {"active_courses": 4}},
        {"LIB": [{"name": "중앙도서관", "address": "", "active_courses": 4}]},
    )

    assert rows[0]["coverage_status"] == "site_connected_unverified"
    assert rows[0]["match_method"] == "site_key"


def test_same_branch_name_on_a_different_site_is_not_counted_as_collected() -> None:
    targets = {
        "OTHER_CITY": {
            "names": {"다른시 도서관"},
            "branches": {"다른시"},
            "urls": {"https://other-library.example/lecture/list"},
        }
    }
    rows = build_facility_coverage_rows(
        [
            {
                "provider": "FACILITY",
                "category": "문화기반시설/공공도서관",
                "name": "행복한도서관",
                "url": "https://library.example/happy",
            }
        ],
        targets,
        {"OTHER_CITY": {"active_courses": 4}},
        {
            "OTHER_CITY": [
                {"name": "행복한도서관", "address": "", "active_courses": 4}
            ]
        },
    )

    assert rows[0]["coverage_status"] == "crawler_target_needed"
    assert rows[0]["matched_providers"] == ""


def test_site_group_prioritizes_existing_collector_expansion() -> None:
    groups = build_site_group_rows(
        [
            {
                "site_key": "library.example.kr",
                "coverage_status": "collected",
                "matched_providers": "LIB",
                "category": "문화기반시설/공공도서관",
                "region": "경기 예시시",
                "name": "중앙도서관",
            },
            {
                "site_key": "library.example.kr",
                "coverage_status": "site_connected_unverified",
                "matched_providers": "LIB",
                "category": "문화기반시설/공공도서관",
                "region": "경기 예시시",
                "name": "분관",
            },
        ]
    )

    assert groups[0]["facility_count"] == 2
    assert groups[0]["unresolved_count"] == 1
    assert groups[0]["action"] == "expand_existing_collector"


def test_duplicate_target_aliases_are_not_operational_targets() -> None:
    assert is_duplicate_target_alias({"crawler_status": "duplicate_url:CANONICAL"})
    assert is_duplicate_target_alias({"duplicate_of": "CANONICAL"})
    assert is_duplicate_target_alias({"collection_type": "duplicate"})
    assert not is_duplicate_target_alias({"crawler_status": "ready"})


def test_related_scope_finds_library_targets_stored_in_other_registries() -> None:
    assert target_matches_related_scope(
        {
            "name": "정선교육도서관 전체 교육강좌",
            "source_group": "municipal_reservation",
        }
    )
    assert target_matches_related_scope({"branch": "남원시립김병종미술관"})
    assert not target_matches_related_scope(
        {"name": "시민 평생학습관", "branch": "평생학습관"}
    )


def test_active_provider_with_recent_error_is_reported_as_recent_failure() -> None:
    target = {"registry_statuses": {"ready"}, "last_quality": []}
    stats = {
        "active_courses": 3,
        "latest_seen_at": datetime.now(timezone.utc),
        **{f"{field}_count": 3 for field in ("target", "fee", "date", "place", "category", "time")},
    }
    report = {"success": False, "error": "request timeout"}

    assert (
        derive_collection_status(
            target,
            stats,
            report,
            stale_before=datetime.now(timezone.utc) - timedelta(days=30),
        )
        == "collected_recent_failure"
    )


def test_active_provider_with_a_required_field_gap_is_reported_separately() -> None:
    target = {"registry_statuses": {"ready"}, "last_quality": []}
    stats = {
        "active_courses": 2,
        "latest_seen_at": datetime.now(timezone.utc),
        "target_count": 2,
        "fee_count": 2,
        "date_count": 2,
        "place_count": 2,
        "category_count": 2,
        "time_count": 1,
    }

    assert (
        derive_collection_status(
            target,
            stats,
            None,
            stale_before=datetime.now(timezone.utc) - timedelta(days=30),
        )
        == "collected_field_gap"
    )


def test_successful_dry_run_without_active_rows_is_reported_as_validated() -> None:
    target = {"registry_statuses": {"ready"}, "last_quality": []}
    stats = {"active_courses": 0, "latest_seen_at": None}
    report = {"success": True, "collected": 12, "saved": 0}

    assert (
        derive_collection_status(
            target,
            stats,
            report,
            stale_before=datetime.now(timezone.utc) - timedelta(days=30),
        )
        == "validated_not_persisted"
    )


def test_blocked_provider_with_an_old_error_remains_blocked() -> None:
    target = {"registry_statuses": {"blocked"}, "last_quality": []}
    stats = {"active_courses": 0, "latest_seen_at": None}
    report = {"success": False, "error": "strict TLS request failed"}

    assert (
        derive_collection_status(
            target,
            stats,
            report,
            stale_before=datetime.now(timezone.utc) - timedelta(days=30),
        )
        == "blocked"
    )


def test_operational_validation_is_collection_evidence() -> None:
    reports = operational_validation_reports(
        [
            {
                "provider": "MUNI_TEST",
                "validation_outcome": "collected",
                "validated_at": "2026-07-23T11:00:00+09:00",
                "parser": "test_parser",
                "row_count": 12,
                "no_current_data": False,
            },
            {
                "provider": "MUNI_EMPTY",
                "validation_outcome": "no_current_data",
                "validated_at": "2026-07-23T12:00:00+09:00",
                "parser": "empty_parser",
                "row_count": 0,
                "no_current_data": True,
            },
        ],
        {"MUNI_TEST", "MUNI_EMPTY"},
    )

    assert reports["MUNI_TEST"]["success"] is True
    assert reports["MUNI_TEST"]["collected"] == 12
    assert reports["MUNI_EMPTY"]["no_current_data"] is True


def test_latest_evidence_wins_between_operational_validation_and_run_report() -> None:
    validation = {
        "MUNI_TEST": {
            "success": True,
            "collected": 12,
            "report_generated_at": "2026-07-23T11:00:00+09:00",
        }
    }
    later_failure = {
        "MUNI_TEST": {
            "success": False,
            "error": "later failure",
            "report_generated_at": "2026-07-24T11:00:00+09:00",
        }
    }

    merged = merge_latest_reports(validation, later_failure)

    assert merged["MUNI_TEST"]["success"] is False
    assert merged["MUNI_TEST"]["error"] == "later failure"


def test_crawler_run_report_maps_the_latest_full_provider_failure() -> None:
    reports = crawler_run_reports(
        [
            {
                "id": 10,
                "target_key": "LOTTE",
                "status": "success",
                "started_at": datetime(2026, 7, 26, 8, tzinfo=timezone.utc),
                "ended_at": datetime(2026, 7, 26, 9, tzinfo=timezone.utc),
                "collected_count": 3000,
                "inserted_count": 100,
                "updated_count": 2900,
            },
            {
                "id": 11,
                "target_key": "LOTTE",
                "status": "failed",
                "started_at": datetime(2026, 7, 27, 8, tzinfo=timezone.utc),
                "ended_at": datetime(2026, 7, 27, 9, tzinfo=timezone.utc),
                "collected_count": 3011,
                "inserted_count": 77,
                "updated_count": 2934,
                "error_type": "CalledProcessError",
                "error_message": "exit_code=1",
            },
        ],
        {"LOTTE"},
    )

    assert reports["LOTTE"]["success"] is False
    assert reports["LOTTE"]["collected"] == 3011
    assert reports["LOTTE"]["saved"] == 3011
    assert reports["LOTTE"]["error"] == "CalledProcessError: exit_code=1"
    assert reports["LOTTE"]["report_path"] == "crawler_run_log:11"


def test_crawler_run_report_ignores_branch_scoped_and_aggregate_runs() -> None:
    reports = crawler_run_reports(
        [
            {
                "id": 20,
                "target_key": "EMART|branch_code=100",
                "status": "success",
                "started_at": "2026-07-27T10:00:00+09:00",
                "ended_at": "2026-07-27T10:01:00+09:00",
                "collected_count": 2,
            },
            {
                "id": 21,
                "target_key": "MUNICIPAL_RESERVATION_TARGETS",
                "status": "failed",
                "started_at": "2026-07-27T10:00:00+09:00",
                "ended_at": "2026-07-27T12:00:00+09:00",
            },
        ],
        {"EMART", "MUNI_TEST"},
    )

    assert reports == {}


def test_successful_crawler_run_is_collection_evidence() -> None:
    reports = crawler_run_reports(
        [
            {
                "id": 30,
                "target_key": "HOMEPLUS",
                "source_type": "culture_center",
                "crawler_name": "Crawler_Homeplus.py",
                "status": "success",
                "started_at": "2026-07-27T01:00:00+09:00",
                "ended_at": "2026-07-27T04:31:00+09:00",
                "collected_count": 7859,
                "inserted_count": 100,
                "updated_count": 7759,
            }
        ],
        {"HOMEPLUS"},
    )

    assert reports["HOMEPLUS"]["success"] is True
    assert reports["HOMEPLUS"]["error"] == ""
    assert reports["HOMEPLUS"]["evidence_type"] == "crawler_run_log"


def test_full_crawler_failure_is_not_hidden_by_a_later_branch_update() -> None:
    target = {"registry_statuses": {"ready"}, "last_quality": []}
    stats = {
        "active_courses": 2,
        "latest_seen_at": datetime(2026, 7, 27, 10, 22, tzinfo=timezone.utc),
        **{
            f"{field}_count": 2
            for field in ("target", "fee", "date", "place", "category", "time")
        },
    }
    report = {
        "success": False,
        "error": "CalledProcessError: exit_code=2",
        "evidence_type": "crawler_run_log",
        "report_generated_at": "2026-07-27T01:21:00+00:00",
    }

    assert (
        derive_collection_status(
            target,
            stats,
            report,
            stale_before=datetime.now(timezone.utc) - timedelta(days=30),
        )
        == "collected_recent_failure"
    )


def test_failure_report_older_than_latest_saved_data_is_superseded() -> None:
    latest_seen_at = datetime(2026, 7, 26, 2, 0, tzinfo=timezone(timedelta(hours=9)))
    target = {"registry_statuses": {"ready"}, "last_quality": []}
    stats = {
        "active_courses": 2,
        "latest_seen_at": latest_seen_at,
        **{
            f"{field}_count": 2
            for field in ("target", "fee", "date", "place", "category", "time")
        },
    }
    report = {
        "success": False,
        "error": "old failure",
        "report_generated_at": "2026-07-25T21:00:00",
    }

    assert (
        derive_collection_status(
            target,
            stats,
            report,
            stale_before=latest_seen_at - timedelta(days=30),
        )
        == "collected"
    )


def test_field_gap_summary_excludes_providers_without_active_courses() -> None:
    empty = {
        "collection_status": "no_current_data",
        "active_courses": 0,
        **{
            f"{field}_pct": 0.0
            for field in ("target", "fee", "date", "place", "category", "time")
        },
    }
    partial = {
        "collection_status": "collected_field_gap",
        "active_courses": 2,
        "target_pct": 100.0,
        "fee_pct": 100.0,
        "date_pct": 100.0,
        "place_pct": 100.0,
        "category_pct": 100.0,
        "time_pct": 50.0,
    }

    summary = summarize([empty, partial], [], target_entries=2)

    assert summary["providers_with_field_gaps"] == {
        "target": 0,
        "fee": 0,
        "date": 0,
        "place": 0,
        "category": 0,
        "time": 1,
    }
