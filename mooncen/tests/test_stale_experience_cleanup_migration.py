from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "DB"
    / "migrations"
    / "20260805_001_deactivate_stale_experience_rows.sql"
)


def test_stale_experience_cleanup_is_provider_and_title_bounded() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "MUNI_RESERVE_ANSAN_GO_KR_8236CAF0" not in sql
    assert "provider = 'MUNI_WWW_GOYANG_GO_KR_9C1A7354'" in sql
    assert "is_active IS TRUE" in sql
    assert "title = '서비스 접속 대기 중입니다.'" in sql
    assert "LOCK TABLE courses IN SHARE ROW EXCLUSIVE MODE" in sql
    assert "provider_active <> 32" in sql
    assert "exact_active <> 32" in sql
    assert "644148f9aa45f0323a02ee513ced1897" in sql
    assert "ops_course_backup_goyang_wait_20260805" in sql
    assert "removed_at = COALESCE(removed_at, CURRENT_TIMESTAMP)" in sql
    assert "MUNI_WWW_GOYANG_GO_KR_AFE8FBDD" not in sql


def test_cleanup_never_deletes_rows() -> None:
    normalized = " ".join(MIGRATION.read_text(encoding="utf-8").lower().split())

    assert "delete from" not in normalized
    assert normalized.count("update courses") == 1
