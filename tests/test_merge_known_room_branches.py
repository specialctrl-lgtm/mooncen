from __future__ import annotations

from tools.maintenance.merge_known_room_branches import (
    MergeRule,
    RULES,
    rule_matches_source_name,
    selected_sources,
    stable_branch_code,
    validate_rules,
)


def test_reviewed_rules_have_valid_addresses_and_unique_targets() -> None:
    assert validate_rules(RULES) == []


def test_source_selectors_are_exact_or_prefix_bounded() -> None:
    rule = MergeRule(
        provider="TEST",
        canonical_name="평생학습관",
        address="경기도 수원시 팔달로 1",
        source_names=("301호",),
        source_prefixes=("평생학습관 >",),
    )

    assert rule_matches_source_name(rule, "301호")
    assert rule_matches_source_name(rule, "평생학습관 > 302호")
    assert not rule_matches_source_name(rule, "1301호")
    assert not rule_matches_source_name(rule, "다른 평생학습관 > 302호")


def test_stable_branch_code_matches_venue_split_identity() -> None:
    code = stable_branch_code("TEST_PROVIDER", "순천시평생학습관")

    assert code.startswith("순천시평생학습관_")
    assert code == stable_branch_code("TEST_PROVIDER", "순천시평생학습관")
    assert code != stable_branch_code("OTHER_PROVIDER", "순천시평생학습관")


def test_selected_source_can_already_have_canonical_address() -> None:
    rule = MergeRule(
        provider="TEST",
        canonical_name="성정평생학습관",
        address="충청남도 천안시 서북구 성정중4길 29",
        source_names=("301",),
    )
    branches = [
        {
            "id": "source",
            "name": "301",
            "address": "충청남도 천안시 서북구 성정중4길 29",
        },
        {
            "id": "wrong",
            "name": "301",
            "address": "충청남도 천안시 서북구 다른로 1",
        },
    ]

    assert [row["id"] for row in selected_sources(rule, branches, None)] == [
        "source"
    ]
