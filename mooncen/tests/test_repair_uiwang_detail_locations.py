from __future__ import annotations

from Crawler.Crawler_MunicipalYaml import uiwang_physical_branch_code
from tools.maintenance.repair_uiwang_detail_locations import (
    choose_existing_target,
    facility_name_key,
    stable_branch_code,
    target_names_match,
)


PROVIDER = "MUNI_WWW_UIWANG_GO_KR_2A9DF9A4"


def test_uiwang_location_branch_code_matches_crawler_contract() -> None:
    assert stable_branch_code(
        PROVIDER,
        "글로벌도서관",
    ) == uiwang_physical_branch_code(
        "글로벌도서관",
    )


def test_uiwang_target_names_accept_resident_center_alias_only() -> None:
    assert target_names_match("청계동", "청계동 주민센터")
    assert target_names_match("청계동 주민자치센터", "청계동 주민센터")
    assert not target_names_match("청계참고운도서관", "청계동 주민센터")
    assert facility_name_key("청계동 주민자치센터") == "청계동주민센터"


def test_uiwang_existing_target_prefers_same_facility_at_same_address() -> None:
    branches = [
        {
            "id": "dong",
            "name": "청계동",
            "address": "경기도 의왕시 안양판교로 232",
            "active_course_count": 43,
            "course_count": 43,
        },
        {
            "id": "library",
            "name": "청계참고운도서관",
            "address": "경기도 의왕시 안양판교로 232 청계동주민센터 4층",
            "active_course_count": 1,
            "course_count": 1,
        },
    ]

    target = choose_existing_target(
        "청계동 주민센터",
        "경기도 의왕시 안양판교로 232",
        branches,
    )

    assert target is not None
    assert target["id"] == "dong"
