from __future__ import annotations

from pathlib import Path

from tools.maintenance import audit_map_visibility as audit


def _row(**overrides):
    row = {
        "provider": "TEST_PROVIDER",
        "name": "Test facility",
        "address": None,
        "lat": None,
        "lon": None,
        "location_is_null": True,
        "location_mismatch": False,
        "location_verified": False,
        "coordinate_source": None,
        "region_sido": None,
        "region_sigungu": None,
        "course_addresses": [],
        "active_courses": 1,
        "active_current_courses": 1,
    }
    row.update(overrides)
    return row


def test_repair_paths_are_non_overlapping_and_prefer_stronger_evidence():
    configured = {"TEST_PROVIDER": "\uacbd\uae30\ub3c4 \uc218\uc6d0\uc2dc"}
    source = _row(
        id="source",
        lat=37.2,
        lon=127.0,
        location_verified=True,
        location_confidence=100,
        coordinate_source="KAKAO_LOCAL_ADDRESS",
    )
    sources = audit.verified_source_index([source])

    assert audit.repair_path(
        _row(address="\uacbd\uae30\ub3c4 \uc218\uc6d0\uc2dc \uc601\ud1b5\ub85c 435"),
        configured_localities=configured,
        verified_sources=sources,
    ) == "kakao_stored_address"
    assert audit.repair_path(
        _row(course_addresses=["\uacbd\uae30\ub3c4 \uc218\uc6d0\uc2dc \uc601\ud1b5\ub85c 435"]),
        configured_localities=configured,
        verified_sources=sources,
    ) == "kakao_unique_course_address"
    assert audit.repair_path(
        _row(region_sido="\uacbd\uae30\ub3c4", region_sigungu="\uc218\uc6d0\uc2dc"),
        configured_localities=configured,
        verified_sources=sources,
    ) == "kakao_stored_region"
    assert audit.repair_path(
        _row(),
        configured_localities=configured,
        verified_sources=sources,
    ) == "kakao_configured_locality"


def test_verified_same_name_path_uses_the_same_fail_closed_matcher_as_the_writer():
    source = _row(
        id="source",
        lat=37.2,
        lon=127.0,
        location_verified=True,
        location_confidence=100,
        coordinate_source="KAKAO_LOCAL_ADDRESS",
    )
    sources = audit.verified_source_index([source])

    assert audit.repair_path(
        _row(),
        configured_localities={},
        verified_sources=sources,
    ) == "verified_same_name_copy"

    conflicting_source = dict(source, address="Busan Haeundae-gu 2")
    conflicting_sources = audit.verified_source_index([conflicting_source])
    assert audit.repair_path(
        _row(address="Seoul A"),
        configured_localities={},
        verified_sources=conflicting_sources,
    ) == "manual_conflicting_same_name_evidence"


def test_visibility_integrity_distinguishes_coordinates_from_postgis_state():
    assert audit.location_visibility_issue(_row()) == "missing_coordinates"
    assert audit.location_visibility_issue(
        _row(lat=37.2, lon=127.0, location_is_null=True)
    ) == "missing_postgis_location"
    assert audit.location_visibility_issue(
        _row(lat=37.2, lon=127.0, location_is_null=False, location_mismatch=True)
    ) == "postgis_coordinate_mismatch"
    assert audit.location_visibility_issue(
        _row(lat=37.2, lon=127.0, location_is_null=False)
    ) == "visible_by_location"


def test_summary_counts_only_current_searchable_status_branches_as_map_contract():
    rows = [
        _row(),
        _row(
            name="Visible",
            lat=37.2,
            lon=127.0,
            location_is_null=False,
            location_verified=True,
            coordinate_source="KAKAO_LOCAL_ADDRESS",
        ),
        _row(name="Closed only", active_current_courses=0),
    ]
    report = audit.summarize(
        rows,
        configured_localities={},
        verified_sources={},
        geocode_queue_schema_ready=True,
    )

    assert report["active_course_branches"] == 3
    assert report["current_searchable_branches"] == 2
    assert report["map_visible_by_location"] == 1
    assert report["map_blocked_by_location"] == 1
    assert report["missing_coordinate_repair_paths"] == {
        "manual_missing_location_evidence": 1
    }


def test_audit_source_is_read_only_and_never_calls_an_external_map_api():
    source = Path(audit.__file__).read_text(encoding="utf-8").upper()

    assert "SET_SESSION(READONLY=TRUE" in source
    assert "UPDATE BRANCHES" not in source
    assert "INSERT INTO" not in source
    assert "DELETE FROM" not in source
    assert "REQUESTS." not in source
