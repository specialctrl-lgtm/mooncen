from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_canonical_schema_and_migration_define_structured_geocode_queue():
    schema = (ROOT / "DB" / "schema.sql").read_text(encoding="utf-8")
    migration = (
        ROOT / "DB" / "migrations" / "20260806_003_branch_geocode_outcomes.sql"
    ).read_text(encoding="utf-8")

    for column in (
        "geocode_status",
        "geocode_reason_code",
        "geocode_attempt_count",
        "geocode_candidates",
        "geocode_next_retry_at",
        "geocode_last_error",
        "geocode_last_attempt_at",
    ):
        assert column in schema
        assert column in migration
    assert "chk_branch_geocode_status" in schema
    assert "chk_branch_geocode_candidates_array" in migration
    assert "idx_branches_geocode_retry_queue" in schema


def test_legacy_non_kakao_coordinates_are_not_falsely_marked_resolved():
    migration = (
        ROOT / "DB" / "migrations" / "20260806_003_branch_geocode_outcomes.sql"
    ).read_text(encoding="utf-8")

    assert "coordinate_source IN ('KAKAO_LOCAL_ADDRESS', 'KAKAO_LOCAL_KEYWORD')" in migration
    assert "GOOGLE" not in migration
