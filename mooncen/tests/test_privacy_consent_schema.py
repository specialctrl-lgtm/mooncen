from __future__ import annotations

from fnmatch import fnmatchcase
import hashlib
import json
from pathlib import Path
import re

from tools import sync_production_to_development as production_sync


ROOT = Path(__file__).resolve().parents[1]
NOTICE_PATH = ROOT / "config" / "privacy_membership_notice.json"
AUTH_SCHEMA_PATH = ROOT / "DB" / "auth_schema.sql"
MIGRATION_PATH = ROOT / "DB" / "migrations" / "20260810_001_user_privacy_acceptances.sql"
ROLES_PATH = ROOT / "DB" / "roles.sql"
DOC_PATH = ROOT / "docs" / "privacy-membership-notice-2026-08-10.md"
EXPECTED_VERSION = "2026-08-10"
EXPECTED_HASH = "4c01b656b92713aa35bf24149a12ce45e3e17f856d54beb950da4a69ad6e9000"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _notice() -> dict[str, object]:
    return json.loads(_text(NOTICE_PATH))


def _canonical_notice(notice: dict[str, object]) -> bytes:
    return json.dumps(
        notice,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _embedded_notice_payloads(sql: str) -> list[dict[str, object]]:
    payloads = re.findall(
        r"\$membership_notice\$(.*?)\$membership_notice\$::JSONB",
        sql,
        flags=re.DOTALL,
    )
    return [json.loads(payload) for payload in payloads]


def test_reviewed_notice_version_and_hash_are_deterministic() -> None:
    notice = _notice()

    assert notice["version"] == EXPECTED_VERSION
    assert notice["effective_date"] == EXPECTED_VERSION
    assert hashlib.sha256(_canonical_notice(notice)).hexdigest() == EXPECTED_HASH


def test_fresh_schema_and_migration_seed_the_exact_reviewed_notice() -> None:
    notice = _notice()

    for path in (AUTH_SCHEMA_PATH, MIGRATION_PATH):
        sql = _text(path)
        embedded = _embedded_notice_payloads(sql)

        assert len(embedded) == 2
        assert embedded == [notice, notice]
        assert sql.count(EXPECTED_HASH) == 2
        assert "ON CONFLICT (version) DO NOTHING" in sql
        assert "does not match its reviewed seed" in sql


def test_privacy_tables_have_bounded_values_and_no_historical_backfill() -> None:
    required_fragments = (
        "version VARCHAR(32) PRIMARY KEY",
        "notice_type VARCHAR(32) NOT NULL DEFAULT 'membership'",
        "legal_basis VARCHAR(32) NOT NULL DEFAULT 'consent'",
        "notice_hash CHAR(64) NOT NULL",
        "notice_json JSONB NOT NULL",
        "effective_date DATE NOT NULL",
        "id UUID PRIMARY KEY DEFAULT gen_random_uuid()",
        "user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE",
        "notice_version VARCHAR(32) NOT NULL REFERENCES privacy_notice_versions(version)",
        "acceptance_type VARCHAR(32) NOT NULL DEFAULT 'consent_granted'",
        "acquisition_method IN ('email_signup', 'google_signup', 'naver_signup')",
        "accepted_at TIMESTAMPTZ NOT NULL DEFAULT now()",
        "UNIQUE (user_id, notice_version, acceptance_type)",
        "CREATE INDEX IF NOT EXISTS ix_user_privacy_acceptances_notice_version",
    )

    for path in (AUTH_SCHEMA_PATH, MIGRATION_PATH):
        sql = _text(path)
        assert "CREATE TABLE IF NOT EXISTS privacy_notice_versions" in sql
        assert "CREATE TABLE IF NOT EXISTS user_privacy_acceptances" in sql
        for fragment in required_fragments:
            assert fragment in sql
        assert not re.search(
            r"INSERT\s+INTO\s+user_privacy_acceptances",
            sql,
            flags=re.IGNORECASE,
        )
        assert not re.search(
            r"UPDATE\s+user_privacy_acceptances",
            sql,
            flags=re.IGNORECASE,
        )


def test_api_can_only_read_notices_and_append_server_timed_acceptances() -> None:
    roles = _text(ROLES_PATH)

    assert (
        "privacy_notice_versions, user_privacy_acceptances\n"
        "    TO mooncen_api;"
    ) in roles
    assert (
        "REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON privacy_notice_versions "
        "FROM mooncen_api;"
    ) in roles
    assert (
        "REVOKE UPDATE, DELETE, TRUNCATE ON user_privacy_acceptances FROM mooncen_api;"
    ) in roles
    assert (
        "GRANT INSERT (\n"
        "    user_id, notice_version, acceptance_type, acquisition_method\n"
        ") ON user_privacy_acceptances TO mooncen_api;"
    ) in roles
    assert "accepted_at" not in roles.split("GRANT INSERT (", 1)[1].split(") ON user_privacy_acceptances", 1)[0]
    assert "id" not in roles.split("GRANT INSERT (", 1)[1].split(") ON user_privacy_acceptances", 1)[0].split()
    assert "GRANT SELECT ON ALL TABLES IN SCHEMA public TO mooncen_readonly" in roles


def test_production_sync_excludes_acceptance_rows_via_user_wildcard() -> None:
    patterns = production_sync.DUMP_EXCLUDED_DATA_PATTERNS

    assert "public.user_*" in patterns
    assert any(fnmatchcase("public.user_privacy_acceptances", pattern) for pattern in patterns)
    assert not any(fnmatchcase("public.privacy_notice_versions", pattern) for pattern in patterns)


def test_contract_document_tracks_the_reviewed_notice_without_claiming_backfill() -> None:
    notice = _notice()
    document = _text(DOC_PATH)

    assert EXPECTED_VERSION in document
    assert EXPECTED_HASH in document
    for field in ("title", "purpose", "retention", "refusal", "consent_label"):
        assert str(notice[field]) in document
    for item in notice["items"]:
        assert str(item) in document
    assert "기존 사용자 행에는 동의 기록을 소급 생성하지 않습니다" in document
