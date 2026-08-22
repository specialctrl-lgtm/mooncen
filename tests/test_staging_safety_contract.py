from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
import re
from pathlib import Path
from uuid import UUID

import pytest

import tools.apply_staging_batch as apply_staging_batch_module
from DB import setup_db
from run_crawlers import build_course_provider_owners, build_staging_batch_result
from service_group import render_service_group_sql
from tools.apply_staging_batch import (
    APPLY_ADVISORY_LOCK_ID,
    BRANCH_COLUMNS,
    COURSE_UPDATE_COLUMNS,
    acquire_primary_apply_lock,
    collection_is_complete,
    evaluate_close_safety,
    rows_owned_by_successful_providers,
    staging_selection_fingerprint,
    validate_batch_provider_ownership,
    validate_control_plane_promotion_gate,
    validate_provider_promotion_contract,
    validate_rows,
)


ROOT = Path(__file__).resolve().parents[1]


def test_schema_executor_forces_the_migrator_schema_before_unqualified_ddl(monkeypatch):
    calls: list[str] = []

    class FakeCursor:
        def execute(self, sql):
            calls.append(str(sql))

    @contextmanager
    def fake_cursor(*, dict_cursor=True):
        assert dict_cursor is False
        yield FakeCursor()

    monkeypatch.setattr(setup_db, "get_db_cursor", fake_cursor)
    monkeypatch.setattr(setup_db, "read_sql", lambda _filename: "CREATE TABLE users (id int);")

    setup_db.execute_sql("auth_schema.sql")

    assert calls == [
        "SET SESSION search_path = public, pg_catalog",
        "CREATE TABLE users (id int);",
    ]


def test_control_plane_promotion_gate_allows_review_but_blocks_primary_mutation():
    held = {"control_plane": True, "promotion_eligible": False}
    validate_control_plane_promotion_gate(held, dry_run=True)
    with pytest.raises(RuntimeError, match="held for explicit promotion approval"):
        validate_control_plane_promotion_gate(held, dry_run=False)

    validate_control_plane_promotion_gate(
        {"control_plane": True, "promotion_eligible": True},
        dry_run=False,
    )
    validate_control_plane_promotion_gate({}, dry_run=False)


def _active_sql(path: Path) -> str:
    return re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.DOTALL)


def test_service_group_sql_is_generated_from_authoritative_python_rules():
    sql = (ROOT / "DB" / "service_group.sql").read_text(encoding="utf-8")
    assert sql == render_service_group_sql()
    assert "NEW.service_group IS DISTINCT FROM OLD.service_group" in sql
    assert "explicit_input" in sql
    assert "NEW.raw_fields ->> 'service_group_policy'" in sql
    assert "program_type, service_group, raw_fields" in sql


def test_base_and_staging_sql_do_not_execute_embedded_classifiers():
    for name in ("schema.sql", "migrate_current.sql", "staging_schema.sql"):
        active = _active_sql(ROOT / "DB" / name)
        assert "CREATE OR REPLACE FUNCTION mooncen_infer_course_service_group" not in active
        assert "CREATE OR REPLACE FUNCTION mooncen_set_course_service_group" not in active


def test_primary_apply_schema_is_isolated_from_staging_triggers():
    sql = (ROOT / "DB" / "staging_primary_schema.sql").read_text(encoding="utf-8")
    assert "PARTIAL_SUCCESS" in sql
    assert "mooncen_infer_course_service_group" not in sql
    assert "set_current_crawl_batch_id" not in sql
    assert "ALTER TABLE courses" not in sql
    staging_sql = (ROOT / "DB" / "staging_schema.sql").read_text(encoding="utf-8")
    assert "Never apply this file on primary" in staging_sql


def test_staging_schema_migrates_standard_category_columns_for_existing_databases():
    staging_sql = (ROOT / "DB" / "staging_schema.sql").read_text(encoding="utf-8")
    for column in ("standard_category_key", "standard_category_label"):
        assert f"ALTER TABLE courses ADD COLUMN IF NOT EXISTS {column}" in staging_sql
        assert f"CREATE INDEX IF NOT EXISTS idx_courses_{column}" in staging_sql


def test_staging_apply_preserves_canonical_branch_metadata():
    expected = {
        "facility_type",
        "facility_category",
        "facility_source",
        "facility_source_sheet",
        "facility_service_group",
        "facility_collection_category",
        "region_sido",
        "region_sigungu",
        "regular_holiday",
        "admission_fee",
        "basic_info",
        "geocode_status",
        "geocode_reason_code",
        "geocode_attempt_count",
        "geocode_candidates",
        "geocode_next_retry_at",
        "geocode_last_error",
        "geocode_last_attempt_at",
    }
    assert expected <= set(BRANCH_COLUMNS)
    assert "view_count" not in COURSE_UPDATE_COLUMNS


def test_staging_upsert_normalizes_and_never_regresses_seen_timestamps(monkeypatch):
    captured: dict[str, str] = {}

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _traceback):
            return False

        def fetchone(self):
            return {"inserted": 1, "updated": 0}

    class Connection:
        def cursor(self, **_kwargs):
            return Cursor()

    def capture_execute_values(_cursor, query, _values, **_kwargs):
        captured["query"] = " ".join(query.split())

    monkeypatch.setattr(
        apply_staging_batch_module,
        "execute_values",
        capture_execute_values,
    )

    inserted, updated = apply_staging_batch_module.upsert_courses(
        Connection(),
        [
            {
                "provider": "TEST_PROVIDER",
                "provider_course_id": "course-1",
                "title": "시민 요리 강좌",
                "schedule_raw": "2026-08-01 ~ 2026-08-31 매주 월요일 10:00",
                "status": "OPEN",
                "raw_url": None,
            }
        ],
        {},
    )

    assert (inserted, updated) == (1, 0)
    query = captured["query"]
    assert (
        "LEAST(first_seen_at::timestamptz, last_seen_at::timestamptz)"
        in query
    )
    assert (
        "GREATEST(first_seen_at::timestamptz, last_seen_at::timestamptz)"
        in query
    )
    assert (
        "last_seen_at = GREATEST(courses.first_seen_at, "
        "courses.last_seen_at, EXCLUDED.last_seen_at)"
        in query
    )
    assert "first_seen_at = EXCLUDED.first_seen_at" not in query


def test_staging_upsert_keeps_culture_provider_official_application_link(monkeypatch):
    captured: dict[str, object] = {}

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _traceback):
            return False

        def fetchone(self):
            return {"inserted": 1, "updated": 0}

    class Connection:
        def cursor(self, **_kwargs):
            return Cursor()

    def capture_execute_values(_cursor, _query, values, **_kwargs):
        captured["values"] = values

    monkeypatch.setattr(apply_staging_batch_module, "execute_values", capture_execute_values)
    monkeypatch.setattr(
        apply_staging_batch_module,
        "coalesce_provider_course_ids_by_raw_url",
        lambda *_args, **_kwargs: None,
    )

    raw_url = "https://www.cultureclub.emart.com/class/course-1"
    apply_staging_batch_module.upsert_courses(
        Connection(),
        [
            {
                "provider": "EMART",
                "provider_course_id": "course-1",
                "raw_url": raw_url,
                "application_url": None,
            }
        ],
        {},
    )

    values = captured["values"]
    application_url_index = 1 + apply_staging_batch_module.COURSE_COLUMNS.index("application_url")
    assert values[0][application_url_index] == raw_url


def test_staging_upsert_chunks_trigger_work_and_aggregates_counts(monkeypatch):
    chunk_sizes: list[int] = []

    class Cursor:
        current_chunk_size = 0

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _traceback):
            return False

        def fetchone(self):
            return {"inserted": self.current_chunk_size, "updated": 0}

    class Connection:
        def cursor(self, **_kwargs):
            return Cursor()

    def capture_execute_values(cursor, _query, values, **_kwargs):
        cursor.current_chunk_size = len(values)
        chunk_sizes.append(len(values))

    monkeypatch.setattr(
        apply_staging_batch_module,
        "execute_values",
        capture_execute_values,
    )

    course_count = apply_staging_batch_module.COURSE_UPSERT_CHUNK_SIZE * 2 + 1
    courses = [
        {
            "provider": "TEST_PROVIDER",
            "provider_course_id": f"course-{index}",
            "title": f"시민 요리 강좌 {index}",
            "schedule_raw": "2026-08-01 ~ 2026-08-31 매주 월요일 10:00",
            "status": "OPEN",
            "raw_url": None,
        }
        for index in range(course_count)
    ]

    inserted, updated = apply_staging_batch_module.upsert_courses(
        Connection(),
        courses,
        {},
    )

    assert chunk_sizes == [
        apply_staging_batch_module.COURSE_UPSERT_CHUNK_SIZE,
        apply_staging_batch_module.COURSE_UPSERT_CHUNK_SIZE,
        1,
    ]
    assert (inserted, updated) == (course_count, 0)


def test_staging_status_contract_accepts_partial_success():
    sql = (ROOT / "DB" / "staging_schema.sql").read_text(encoding="utf-8")
    assert "'PARTIAL_SUCCESS'" in sql
    migration = (ROOT / "DB" / "migrations" / "20260710_001_staging_integrity.sql").read_text(encoding="utf-8")
    assert "'PARTIAL_SUCCESS'" in migration
    applier = (ROOT / "tools" / "apply_staging_batch.py").read_text(encoding="utf-8")
    assert "partial_batch or bool(errors) or bool(close_blocked)" in applier


def test_staging_apply_enforces_ended_course_lifecycle():
    applier = (ROOT / "tools" / "apply_staging_batch.py").read_text(encoding="utf-8")
    migration = (
        ROOT / "DB" / "migrations" / "20260727_002_close_ended_courses.sql"
    ).read_text(encoding="utf-8")

    assert "apply_ended_course_lifecycle(" in applier
    assert '"ended_closed": stats.ended_closed' in applier
    assert "end_date < (NOW() AT TIME ZONE 'Asia/Seoul')::date" in migration
    assert "status IS DISTINCT FROM 'CLOSED'" in migration


def test_registration_expiry_migration_closes_stale_open_rows_without_deactivation():
    migration = (
        ROOT / "DB" / "migrations" / "20260808_001_close_ended_registrations.sql"
    ).read_text(encoding="utf-8")

    assert "status IN ('OPEN', 'DEADLINE')" in migration
    assert "apply_end < (NOW() AT TIME ZONE 'Asia/Seoul')::date" in migration
    assert "end_date < (NOW() AT TIME ZONE 'Asia/Seoul')::date" in migration
    assert "reservation_available = FALSE" in migration
    assert "SET status = 'CLOSED'" in migration
    assert "is_active = FALSE" not in migration


def test_versioned_integrity_migration_uses_not_valid_and_drops_unknown_defaults():
    sql = (ROOT / "DB" / "migrations" / "20260710_001_staging_integrity.sql").read_text(encoding="utf-8")
    assert sql.count("NOT VALID") >= 8
    assert "ALTER COLUMN fee DROP DEFAULT" in sql
    assert "ALTER COLUMN material_fee DROP DEFAULT" in sql
    assert "ALTER COLUMN schedule_frequency DROP DEFAULT" in sql
    assert "ON DELETE RESTRICT" in sql
    assert "NOT VALID" in sql


def test_current_migration_repairs_reversed_seen_timestamps_before_validation():
    current = (ROOT / "DB" / "migrate_current.sql").read_text(encoding="utf-8")
    setup = (ROOT / "DB" / "setup_db.py").read_text(encoding="utf-8")

    assert "SET first_seen_at = last_seen_at,\n    last_seen_at = first_seen_at" in current
    assert "first_seen_at > last_seen_at" in current
    assert setup.index('execute_sql("migrate_current.sql")') < setup.index(
        "applied = execute_versioned_migrations()"
    )


def test_setup_applies_ops_monitoring_schema_before_versioned_migrations():
    setup = (ROOT / "DB" / "setup_db.py").read_text(encoding="utf-8")
    monitoring = (ROOT / "DB" / "migrate_ops_monitoring.sql").read_text(encoding="utf-8")
    auxiliary_url_guards = (
        ROOT / "DB" / "migrations" / "20260710_012_auxiliary_url_guards.sql"
    ).read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS course_quality_score" in monitoring
    assert "UPDATE course_quality_score" in auxiliary_url_guards
    assert setup.index('execute_sql("migrate_ops_monitoring.sql")') < setup.index(
        "applied = execute_versioned_migrations()"
    )


def test_large_search_rewrites_have_production_sized_statement_timeouts():
    for name in (
        "20260710_007_search_description_ngrams.sql",
        "20260710_008_search_long_description.sql",
    ):
        sql = (ROOT / "DB" / "migrations" / name).read_text(encoding="utf-8")
        assert "SET LOCAL statement_timeout = '30min';" in sql


def test_versioned_migrations_do_not_leak_timeouts_or_commit_runner_transactions():
    for path in sorted((ROOT / "DB" / "migrations").glob("*.sql")):
        migration = path.read_text(encoding="utf-8")
        statements = {
            line.strip().upper()
            for line in migration.splitlines()
            if line.strip() and not line.lstrip().startswith("--")
        }
        assert "BEGIN;" not in statements, path.name
        assert "COMMIT;" not in statements, path.name
        assert "ROLLBACK;" not in statements, path.name
        assert "\nSET statement_timeout" not in f"\n{migration}", path.name
        assert "\nSET lock_timeout" not in f"\n{migration}", path.name


def test_url_guard_and_exact_duplicate_index_cleanup_are_migrated():
    sql = (ROOT / "DB" / "migrations" / "20260710_002_url_and_index_cleanup.sql").read_text(encoding="utf-8")
    assert "ux_courses_provider_raw_url_fingerprint" in sql
    assert "mooncen_raw_url_fingerprint" in sql
    assert "DROP INDEX IF EXISTS idx_courses_provider_lookup" in sql
    assert "DROP INDEX IF EXISTS ix_users_email" in sql


def test_staging_schema_exposes_the_primary_raw_url_fingerprint_contract():
    migration = (
        ROOT / "DB" / "migrations" / "20260710_002_url_and_index_cleanup.sql"
    ).read_text(encoding="utf-8")
    staging = (ROOT / "DB" / "staging_schema.sql").read_text(encoding="utf-8")

    expected_function = """CREATE OR REPLACE FUNCTION mooncen_raw_url_fingerprint(p_url TEXT)
RETURNS TEXT AS $$
    SELECT CASE
        WHEN NULLIF(btrim(p_url), '') IS NULL THEN NULL
        ELSE encode(public.digest(btrim(p_url), 'sha256'::text), 'hex')
    END;
$$ LANGUAGE sql IMMUTABLE PARALLEL SAFE;"""
    assert expected_function in migration
    assert expected_function in staging


def test_runtime_roles_do_not_grant_every_table_to_mutating_services():
    sql = (ROOT / "DB" / "roles.sql").read_text(encoding="utf-8")
    broad_grant = "GRANT SELECT ON ALL TABLES IN SCHEMA public TO mooncen_api"
    assert broad_grant not in sql
    assert "GRANT SELECT ON ALL TABLES IN SCHEMA public TO mooncen_readonly" in sql
    assert "GRANT SELECT, INSERT, UPDATE ON crawl_batches TO mooncen_crawler" in sql
    assert "GRANT USAGE, CREATE ON SCHEMA crawl_staging" not in sql


def test_auth_token_version_is_canonical_and_migrated():
    auth_sql = (ROOT / "DB" / "auth_schema.sql").read_text(encoding="utf-8")
    migration = (ROOT / "DB" / "migrations" / "20260710_004_auth_token_version.sql").read_text(encoding="utf-8")
    for sql in (auth_sql, migration):
        assert "auth_token_version INTEGER NOT NULL DEFAULT 1" in sql
        assert "CHECK (auth_token_version > 0)" in sql
    assert "VALIDATE CONSTRAINT chk_users_auth_token_version_positive" in migration


def test_versioned_migration_runner_skips_recorded_versions_without_postgres(tmp_path, monkeypatch):
    (tmp_path / "001_applied.sql").write_text("SELECT 'old';", encoding="utf-8")
    (tmp_path / "002_pending.sql").write_text("SELECT 'new';", encoding="utf-8")

    class FakeCursor:
        def __init__(self):
            self.calls: list[tuple[str, object]] = []

        def execute(self, sql, params=None):
            self.calls.append((str(sql), params))

        def fetchall(self):
            # NULL models a record created by the pre-checksum runner. It is
            # adopted once, while non-NULL checksum drift is rejected.
            return [("001_applied", None)]

    cursor = FakeCursor()

    class FakeConnection:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0
            self.closed = False

        def cursor(self):
            return cursor

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

        def close(self):
            self.closed = True

    connection = FakeConnection()

    monkeypatch.setattr(setup_db, "MIGRATIONS_DIR", tmp_path)
    monkeypatch.setattr(setup_db, "get_db_connection", lambda: connection)

    assert setup_db.execute_versioned_migrations() == ["002_pending"]
    executed_sql = [sql for sql, _params in cursor.calls]
    assert "SELECT 'old';" not in executed_sql
    assert "SELECT 'new';" in executed_sql
    assert any("mooncen.schema_migrations" in sql for sql in executed_sql)
    assert any("SET checksum" in sql for sql in executed_sql)
    assert any("pg_advisory_lock" in sql for sql in executed_sql)
    assert not any("pg_advisory_xact_lock" in sql for sql in executed_sql)
    assert any("RESET lock_timeout" in sql for sql in executed_sql)
    lock_index = next(index for index, sql in enumerate(executed_sql) if "pg_advisory_lock" in sql)
    assert "statement_timeout = '30s'" in executed_sql[lock_index - 1]
    assert connection.commits == 3
    assert connection.rollbacks == 0
    assert connection.closed is True


def test_versioned_migration_runner_rejects_checksum_drift(tmp_path, monkeypatch):
    (tmp_path / "001_applied.sql").write_text("SELECT 'changed';", encoding="utf-8")

    class FakeCursor:
        def execute(self, _sql, _params=None):
            return None

        def fetchall(self):
            return [("001_applied", "not-the-current-sha256")]

    class FakeConnection:
        def __init__(self):
            self.rollbacks = 0
            self.closed = False

        def cursor(self):
            return FakeCursor()

        def commit(self):
            return None

        def rollback(self):
            self.rollbacks += 1

        def close(self):
            self.closed = True

    connection = FakeConnection()

    monkeypatch.setattr(setup_db, "MIGRATIONS_DIR", tmp_path)
    monkeypatch.setattr(setup_db, "get_db_connection", lambda: connection)

    try:
        setup_db.execute_versioned_migrations()
    except RuntimeError as exc:
        assert "checksum mismatch" in str(exc)
    else:
        raise AssertionError("changed applied migration was accepted")
    assert connection.rollbacks == 1
    assert connection.closed is True


def test_versioned_migration_runner_commits_each_file_with_its_ledger_row(tmp_path, monkeypatch):
    (tmp_path / "001_first.sql").write_text("SELECT 'first';", encoding="utf-8")
    (tmp_path / "002_fails.sql").write_text("SELECT 'fails';", encoding="utf-8")
    events: list[str] = []

    class FakeCursor:
        def execute(self, sql, params=None):
            text = str(sql)
            events.append(f"execute:{text}")
            if text == "SELECT 'fails';":
                raise RuntimeError("migration failed")

        def fetchall(self):
            return []

    class FakeConnection:
        def cursor(self):
            return FakeCursor()

        def commit(self):
            events.append("commit")

        def rollback(self):
            events.append("rollback")

        def close(self):
            events.append("close")

    monkeypatch.setattr(setup_db, "MIGRATIONS_DIR", tmp_path)
    monkeypatch.setattr(setup_db, "get_db_connection", FakeConnection)

    try:
        setup_db.execute_versioned_migrations()
    except RuntimeError as exc:
        assert str(exc) == "migration failed"
    else:
        raise AssertionError("failed migration was accepted")

    first_sql = events.index("execute:SELECT 'first';")
    first_ledger = next(
        index
        for index, event in enumerate(events)
        if index > first_sql and "INSERT INTO mooncen_schema_migrations" in event
    )
    first_commit = events.index("commit", first_ledger)
    failed_sql = events.index("execute:SELECT 'fails';")
    assert first_sql < first_ledger < first_commit < failed_sql
    assert sum("INSERT INTO mooncen_schema_migrations" in event for event in events) == 1
    assert events[-2:] == ["rollback", "close"]


def test_batch_completion_requires_explicit_full_run_evidence():
    result = build_staging_batch_result(
        "COLLECTED",
        {
            "providers_completed": 2,
            "providers_failed": 0,
            "providers_total": 2,
            "limit": None,
            "branch_code": None,
            "branch_name": None,
            "course_provider_owners": {"A": "A", "B": "B"},
        },
        {"A": 10, "B": 20},
    )
    assert result["collection_complete"] is True
    assert collection_is_complete("COLLECTED", result) == (True, "complete")

    limited = build_staging_batch_result("COLLECTED", {**result, "limit": 5}, {"A": 5})
    assert limited["collection_complete"] is False
    assert collection_is_complete("COLLECTED", limited)[0] is False


@pytest.mark.parametrize("locked", [True, False])
def test_primary_apply_uses_one_transaction_advisory_lock(locked: bool):
    executed: list[tuple[str, tuple[int]]] = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params):
            executed.append((sql, params))

        def fetchone(self):
            return (locked,)

    class Connection:
        def cursor(self):
            return Cursor()

    if locked:
        acquire_primary_apply_lock(Connection())
    else:
        with pytest.raises(RuntimeError, match="already running"):
            acquire_primary_apply_lock(Connection())

    assert executed == [
        (
            "SELECT pg_try_advisory_xact_lock(%s)",
            (APPLY_ADVISORY_LOCK_ID,),
        )
    ]


def test_limited_successful_batch_is_accepted_only_for_explicit_upsert_only_apply():
    result = build_staging_batch_result(
        "COLLECTED",
        {
            "providers_requested": ["GOOD"],
            "provider_results": [{"provider": "GOOD", "success": True}],
            "providers_total": 1,
            "providers_completed": 1,
            "providers_failed": 0,
            "failed_providers": [],
            "limit": 5000,
            "branch_code": "",
            "branch_name": "",
            "close_missing_enabled": False,
            "course_provider_owners": {"GOOD": "GOOD"},
        },
        {"GOOD": 8},
    )

    assert result["collection_complete"] is False
    with pytest.raises(RuntimeError, match="complete provider evidence"):
        validate_batch_provider_ownership("COLLECTED", result, {"GOOD"})
    ownership = validate_batch_provider_ownership(
        "COLLECTED",
        result,
        {"GOOD"},
        allow_scoped_upsert=True,
    )
    assert ownership.successful_owners == ("GOOD",)
    assert collection_is_complete("COLLECTED", result)[0] is False

    unsafe = deepcopy(result)
    unsafe["close_missing_enabled"] = True
    with pytest.raises(RuntimeError, match="complete provider evidence"):
        validate_batch_provider_ownership(
            "COLLECTED",
            unsafe,
            {"GOOD"},
            allow_scoped_upsert=True,
        )

    malformed = deepcopy(result)
    malformed["limit"] = 0
    with pytest.raises(RuntimeError, match="positive integer"):
        validate_batch_provider_ownership(
            "COLLECTED",
            malformed,
            {"GOOD"},
            allow_scoped_upsert=True,
        )


def test_exact_provider_promotion_binds_finalized_rows_to_dry_run_fingerprint():
    batch_result = build_staging_batch_result(
        "COLLECTED",
        {
            "providers_requested": ["GOOD"],
            "provider_results": [{"provider": "GOOD", "success": True}],
            "providers_total": 1,
            "providers_completed": 1,
            "providers_failed": 0,
            "failed_providers": [],
            "limit": 5000,
            "branch_code": "",
            "branch_name": "",
            "close_missing_enabled": False,
            "course_provider_owners": {"GOOD": "GOOD"},
        },
        {"GOOD": 1},
    )
    metadata = {
        "status": "COLLECTED",
        "total_branches": 1,
        "total_courses": 1,
        "valid_courses": 1,
        "invalid_courses": 0,
        "result": batch_result,
    }
    branch_id = UUID("11111111-1111-4111-8111-111111111111")
    course_id = UUID("22222222-2222-4222-8222-222222222222")
    branches = [
        {
            "id": branch_id,
            "provider": "GOOD",
            "branch_code": "MAIN",
            "name": "Good",
        }
    ]
    courses = [
        {
            "id": course_id,
            "branch_id": branch_id,
            "provider": "GOOD",
            "provider_course_id": "1",
            "title": "Course",
        }
    ]
    ownership = validate_batch_provider_ownership(
        "COLLECTED",
        batch_result,
        {"GOOD"},
        allow_scoped_upsert=True,
    )
    fingerprint = staging_selection_fingerprint(metadata, branches, courses)
    assert fingerprint == staging_selection_fingerprint(metadata, branches, courses)

    validate_provider_promotion_contract(
        provider="GOOD",
        batch_metadata=metadata,
        ownership=ownership,
        branches=branches,
        courses=courses,
        errors=[],
        staging_fingerprint=fingerprint,
        expected_staging_fingerprint=fingerprint,
    )

    changed_courses = deepcopy(courses)
    changed_courses[0]["title"] = "Changed with the same row count"
    changed_fingerprint = staging_selection_fingerprint(
        metadata,
        branches,
        changed_courses,
    )
    with pytest.raises(RuntimeError, match="changed after dry-run"):
        validate_provider_promotion_contract(
            provider="GOOD",
            batch_metadata=metadata,
            ownership=ownership,
            branches=branches,
            courses=changed_courses,
            errors=[],
            staging_fingerprint=changed_fingerprint,
            expected_staging_fingerprint=fingerprint,
        )

    mismatched_metadata = {**metadata, "total_courses": 2}
    with pytest.raises(RuntimeError, match="finalized counts"):
        validate_provider_promotion_contract(
            provider="GOOD",
            batch_metadata=mismatched_metadata,
            ownership=ownership,
            branches=branches,
            courses=courses,
            errors=[],
            staging_fingerprint=staging_selection_fingerprint(
                mismatched_metadata,
                branches,
                courses,
            ),
        )


def test_exact_aggregate_promotion_accepts_owned_concrete_provider_rows():
    batch_result = build_staging_batch_result(
        "COLLECTED",
        {
            "providers_requested": ["EXPERIENCE_TARGETS"],
            "provider_results": [
                {"provider": "EXPERIENCE_TARGETS", "success": True}
            ],
            "providers_total": 1,
            "providers_completed": 1,
            "providers_failed": 0,
            "failed_providers": [],
            "limit": 5000,
            "branch_code": "",
            "branch_name": "",
            "close_missing_enabled": False,
            "course_provider_owners": {
                "EXPERIENCE_ONE": "EXPERIENCE_TARGETS",
                "EXPERIENCE_TWO": "EXPERIENCE_TARGETS",
            },
        },
        {"EXPERIENCE_ONE": 2, "EXPERIENCE_TWO": 1},
    )
    metadata = {
        "status": "COLLECTED",
        "total_branches": 2,
        "total_courses": 3,
        "valid_courses": 3,
        "invalid_courses": 0,
        "result": batch_result,
    }
    branches = [
        {"provider": "EXPERIENCE_ONE", "branch_code": "ONE"},
        {"provider": "EXPERIENCE_TWO", "branch_code": "TWO"},
    ]
    courses = [
        {
            "provider": "EXPERIENCE_ONE",
            "provider_course_id": "one-1",
            "title": "One 1",
        },
        {
            "provider": "EXPERIENCE_ONE",
            "provider_course_id": "one-2",
            "title": "One 2",
        },
        {
            "provider": "EXPERIENCE_TWO",
            "provider_course_id": "two-1",
            "title": "Two 1",
        },
    ]
    ownership = validate_batch_provider_ownership(
        "COLLECTED",
        batch_result,
        {"EXPERIENCE_ONE", "EXPERIENCE_TWO"},
        allow_scoped_upsert=True,
    )
    fingerprint = staging_selection_fingerprint(metadata, branches, courses)

    validate_provider_promotion_contract(
        provider="EXPERIENCE_TARGETS",
        batch_metadata=metadata,
        ownership=ownership,
        branches=branches,
        courses=courses,
        errors=[],
        staging_fingerprint=fingerprint,
        expected_staging_fingerprint=fingerprint,
    )


def _failed_batch_result() -> dict:
    return {
        "providers_requested": ["GOOD", "FAILED_PROVIDER"],
        "provider_results": [
            {"provider": "GOOD", "success": True},
            {"provider": "FAILED_PROVIDER", "success": False},
        ],
        "providers_total": 2,
        "providers_completed": 1,
        "providers_failed": 1,
        "failed_providers": ["FAILED_PROVIDER"],
        "collection_complete": False,
        "close_missing_enabled": True,
        "course_provider_owners": {
            "GOOD": "GOOD",
            "FAILED_PROVIDER": "FAILED_PROVIDER",
        },
    }


def test_failed_batch_excludes_failed_direct_provider_rows_and_branches():
    ownership = validate_batch_provider_ownership(
        "FAILED",
        _failed_batch_result(),
        {"GOOD", "FAILED_PROVIDER"},
    )
    rows = [
        {"provider": "GOOD", "provider_course_id": "good"},
        {"provider": "FAILED_PROVIDER", "provider_course_id": "partial"},
    ]
    assert rows_owned_by_successful_providers(rows, ownership) == [rows[0]]
    assert ownership.successful_owners == ("GOOD",)
    assert ownership.failed_owners == ("FAILED_PROVIDER",)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("providers_failed", 0),
        ("providers_completed", 2),
        ("failed_providers", []),
        ("provider_results", [{"provider": "GOOD", "success": True}]),
        (
            "provider_results",
            [
                {"provider": "GOOD", "success": True},
                {"provider": "FAILED_PROVIDER", "success": "false"},
            ],
        ),
        ("course_provider_owners", {"GOOD": "GOOD"}),
        (
            "course_provider_owners",
            {"GOOD": "FAILED_PROVIDER", "FAILED_PROVIDER": "FAILED_PROVIDER"},
        ),
    ],
)
def test_failed_batch_metadata_mismatch_fails_closed(field, value):
    result = deepcopy(_failed_batch_result())
    result[field] = value
    with pytest.raises(RuntimeError):
        validate_batch_provider_ownership(
            "FAILED",
            result,
            {"GOOD", "FAILED_PROVIDER"},
        )


def test_failed_aggregate_batch_uses_owner_snapshot():
    result = {
        "providers_requested": [
            "EXPERIENCE_TARGETS",
            "MUNICIPAL_RESERVATION_TARGETS",
        ],
        "provider_results": [
            {"provider": "EXPERIENCE_TARGETS", "success": True},
            {"provider": "MUNICIPAL_RESERVATION_TARGETS", "success": False},
        ],
        "providers_total": 2,
        "providers_completed": 1,
        "providers_failed": 1,
        "failed_providers": ["MUNICIPAL_RESERVATION_TARGETS"],
        "collection_complete": False,
        "close_missing_enabled": True,
        "course_provider_owners": {
            "RURAL_DEVELOPMENT_ADMINISTRATION": "EXPERIENCE_TARGETS",
            "MUNI_BSLIB_JNE_GO_KR_34227E33": "MUNICIPAL_RESERVATION_TARGETS",
        },
    }
    ownership = validate_batch_provider_ownership(
        "FAILED",
        result,
        {
            "RURAL_DEVELOPMENT_ADMINISTRATION",
            "MUNI_BSLIB_JNE_GO_KR_34227E33",
        },
    )
    rows = [
        {"provider": "RURAL_DEVELOPMENT_ADMINISTRATION"},
        {"provider": "MUNI_BSLIB_JNE_GO_KR_34227E33"},
    ]
    assert rows_owned_by_successful_providers(rows, ownership) == [rows[0]]


def test_failed_aggregate_applies_only_committed_concrete_provider_rows():
    result = {
        "providers_requested": ["EXPERIENCE_TARGETS"],
        "provider_results": [
            {"provider": "EXPERIENCE_TARGETS", "success": False}
        ],
        "providers_total": 1,
        "providers_completed": 0,
        "providers_failed": 1,
        "failed_providers": ["EXPERIENCE_TARGETS"],
        "collection_complete": False,
        "close_missing_enabled": True,
        "course_provider_owners": {
            "EXPERIENCE_COMMITTED": "EXPERIENCE_TARGETS",
            "EXPERIENCE_FAILED": "EXPERIENCE_TARGETS",
        },
        "concrete_provider_results": [
            {
                "provider": "EXPERIENCE_COMMITTED",
                "scheduled_owner": "EXPERIENCE_TARGETS",
                "success": True,
                "targets_total": 2,
                "targets_succeeded": 2,
                "collected_courses": 3,
                "saved_courses": 3,
            },
            {
                "provider": "EXPERIENCE_FAILED",
                "scheduled_owner": "EXPERIENCE_TARGETS",
                "success": False,
                "targets_total": 1,
                "targets_succeeded": 0,
                "collected_courses": 0,
                "saved_courses": 0,
            },
        ],
        "concrete_providers_total": 2,
        "concrete_providers_completed": 1,
        "concrete_providers_failed": 1,
    }

    ownership = validate_batch_provider_ownership(
        "FAILED",
        result,
        {"EXPERIENCE_COMMITTED", "EXPERIENCE_FAILED"},
    )
    rows = [
        {"provider": "EXPERIENCE_COMMITTED", "provider_course_id": "safe"},
        {"provider": "EXPERIENCE_FAILED", "provider_course_id": "unsafe"},
    ]

    assert ownership.successful_owners == ()
    assert ownership.failed_owners == ("EXPERIENCE_TARGETS",)
    assert ownership.successful_course_providers == ("EXPERIENCE_COMMITTED",)
    assert rows_owned_by_successful_providers(rows, ownership) == [rows[0]]
    assert collection_is_complete("FAILED", result)[0] is False


def test_failed_direct_provider_cannot_claim_concrete_success():
    result = _failed_batch_result()
    result["concrete_provider_results"] = [
        {
            "provider": "FAILED_PROVIDER",
            "scheduled_owner": "FAILED_PROVIDER",
            "success": True,
            "targets_total": 1,
            "targets_succeeded": 1,
            "collected_courses": 1,
            "saved_courses": 1,
        }
    ]
    result["concrete_providers_total"] = 1
    result["concrete_providers_completed"] = 1
    result["concrete_providers_failed"] = 0

    with pytest.raises(RuntimeError, match="invalid ownership"):
        validate_batch_provider_ownership(
            "FAILED",
            result,
            {"GOOD", "FAILED_PROVIDER"},
        )


def test_failed_batch_missing_loaded_owner_mapping_fails_closed():
    result = _failed_batch_result()
    with pytest.raises(RuntimeError, match="without scheduled owner"):
        validate_batch_provider_ownership(
            "FAILED",
            result,
            {"GOOD", "FAILED_PROVIDER", "UNMAPPED"},
        )


def test_direct_course_provider_owner_snapshot_is_identity_mapping():
    assert build_course_provider_owners(["HOMEPLUS", "EMART"]) == {
        "EMART": "EMART",
        "HOMEPLUS": "HOMEPLUS",
    }


def test_close_safety_blocks_sharp_drop_and_count_mismatch():
    decision = evaluate_close_safety(
        ["GOOD", "DROP", "MISMATCH"],
        {"GOOD": 90, "DROP": 10, "MISMATCH": 80},
        {"GOOD": 100, "DROP": 100, "MISMATCH": 100},
        {"GOOD": 90, "DROP": 10, "MISMATCH": 79},
        min_ratio=0.65,
        max_absolute_drop=2000,
    )
    assert decision.allowed_providers == ("GOOD",)
    assert "below" in decision.blocked_providers["DROP"]
    assert "mismatch" in decision.blocked_providers["MISMATCH"]


def test_close_safety_blocks_large_absolute_drop_even_when_ratio_passes():
    decision = evaluate_close_safety(
        ["BIG"],
        {"BIG": 7000},
        {"BIG": 10000},
        {"BIG": 7000},
        min_ratio=0.65,
        max_absolute_drop=2000,
    )
    assert decision.allowed_providers == ()
    assert "absolute drop" in decision.blocked_providers["BIG"]


def test_close_guard_captures_active_baseline_before_course_upsert():
    source = (ROOT / "tools" / "apply_staging_batch.py").read_text(encoding="utf-8")
    baseline_call = source.index(
        "active_counts = load_active_course_counts(primary_conn, requested_close_providers)",
        source.index("def main()"),
    )
    upsert_call = source.index(
        "stats.inserted, stats.updated = upsert_courses(primary_conn, valid_courses, branch_map)",
        source.index("def main()"),
    )
    assert baseline_call < upsert_call


def test_staging_validation_detects_canonical_raw_url_collision():
    courses = [
        {
            "provider": "TEST",
            "provider_course_id": "one",
            "title": "One",
            "raw_url": "https://example.test/course?a=1",
        },
        {
            "provider": "TEST",
            "provider_course_id": "two",
            "title": "Two",
            "raw_url": "https://example.test/course?a=1&mooncen_course_id=legacy",
        },
    ]
    errors = validate_rows(courses)
    assert [error["error_code"] for error in errors] == ["duplicate_raw_url"]


def test_fresh_schema_uses_null_for_unknown_values():
    sql = (ROOT / "DB" / "schema.sql").read_text(encoding="utf-8")
    assert "fee NUMERIC DEFAULT 0" not in sql
    assert "material_fee INTEGER DEFAULT 0" not in sql
    assert "schedule_frequency VARCHAR(20) DEFAULT 'WEEKLY'" not in sql
    assert "branch_id UUID REFERENCES branches(id) ON DELETE RESTRICT" in sql
    assert "branch_id UUID REFERENCES branches(id) ON DELETE CASCADE" not in sql
