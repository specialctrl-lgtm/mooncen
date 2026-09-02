from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "DB"
    / "crawler_control_migrations"
    / "20260810_001_crawler_control_plane.sql"
)


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _roles_sql() -> str:
    return (ROOT / "DB" / "roles.sql").read_text(encoding="utf-8")


def test_control_migration_is_excluded_from_generic_primary_setup_and_requires_marker():
    assert MIGRATION.parent.name == "crawler_control_migrations"
    assert not (
        ROOT / "DB" / "migrations" / "20260810_001_crawler_control_plane.sql"
    ).exists()
    sql = _sql()
    marker_sql = (ROOT / "DB" / "crawler_control_database_marker.sql").read_text(
        encoding="utf-8"
    )
    assert "requires a preconfirmed database marker" in sql
    assert "ops_crawler_control_database_marker" in marker_sql
    assert "database_name = current_database()" in marker_sql


def test_ops_jobs_has_fenced_claim_retry_and_dlq_contract():
    sql = _sql()

    for declaration in (
        "ADD COLUMN IF NOT EXISTS available_at TIMESTAMPTZ",
        "ADD COLUMN IF NOT EXISTS lease_token UUID",
        "ADD COLUMN IF NOT EXISTS lease_epoch BIGINT",
        "ADD COLUMN IF NOT EXISTS leased_until TIMESTAMPTZ",
        "ADD COLUMN IF NOT EXISTS required_code_version TEXT",
        "ADD COLUMN IF NOT EXISTS artifact_digest TEXT",
        "ADD COLUMN IF NOT EXISTS config_revision TEXT",
        "ADD COLUMN IF NOT EXISTS attempt_no INTEGER",
    ):
        assert declaration in sql

    assert "lease_owner" not in sql
    assert "agent_id IS NOT NULL" in sql
    assert "'dead_lettered'" in sql
    assert "idx_ops_jobs_claim_ready" in sql
    assert "idx_ops_jobs_claim_compatibility" in sql
    assert "ux_ops_jobs_active_lease_token" in sql
    assert "idx_ops_jobs_expired_lease" in sql
    assert "idx_ops_jobs_dead_lettered" in sql
    assert "lease_epoch >= 0 AND attempt_no >= 0" in sql
    assert "status <> 'dead_lettered'" in sql


def test_batch_and_task_mapping_are_central_and_slot_idempotent():
    sql = _sql()

    assert "CREATE TABLE IF NOT EXISTS ops_crawler_batches" in sql
    assert "UNIQUE (environment, scheduled_slot)" in sql
    for column in (
        "expected_task_count INTEGER NOT NULL",
        "code_version TEXT NOT NULL",
        "artifact_digest TEXT NOT NULL",
        "config_revision TEXT NOT NULL",
    ):
        assert column in sql

    assert "CREATE TABLE IF NOT EXISTS ops_crawler_batch_tasks" in sql
    assert "PRIMARY KEY (batch_id, task_key)" in sql
    assert "UNIQUE (job_id)" in sql
    assert "close_missing_eligible BOOLEAN NOT NULL DEFAULT false" in sql
    assert "allowed_output_providers TEXT[] NOT NULL" in sql
    assert "cardinality(allowed_output_providers) BETWEEN 1 AND 4096" in sql
    assert "aggregate crawler tasks require an exact allowed_output_providers backfill" in sql
    assert "shard_index >= 0 AND shard_index < shard_count" in sql


def test_attempts_and_observations_carry_the_full_fence_identity():
    sql = _sql()

    assert "CREATE TABLE IF NOT EXISTS ops_crawler_task_attempts" in sql
    for identity in (
        "job_id UUID NOT NULL",
        "attempt_no INTEGER NOT NULL",
        "lease_epoch BIGINT NOT NULL",
        "lease_token UUID NOT NULL",
        "agent_id UUID NOT NULL",
    ):
        assert identity in sql
    assert "UNIQUE (job_id, attempt_no)" in sql
    assert "UNIQUE (job_id, lease_epoch)" in sql
    assert "UNIQUE (lease_token)" in sql
    assert "worker_code_version TEXT NOT NULL" in sql

    assert "CREATE TABLE IF NOT EXISTS ops_crawler_task_observations" in sql
    assert re.search(
        r"FOREIGN KEY \(attempt_id, job_id, attempt_no, lease_epoch\)\s+"
        r"REFERENCES ops_crawler_task_attempts\(id, job_id, attempt_no, lease_epoch\)",
        sql,
    )
    assert "BEFORE UPDATE OR DELETE ON ops_crawler_task_observations" in sql
    assert "ux_ops_crawler_task_observations_finished_once" in sql
    assert "WHERE observation_kind = 'finished'" in sql
    assert "DROP CONSTRAINT IF EXISTS ux_ops_crawler_task_attempt_identity" in sql
    assert "DROP CONSTRAINT IF EXISTS ux_ops_crawler_task_attempt_epoch" in sql
    assert "DROP CONSTRAINT IF EXISTS ux_ops_crawler_task_attempt_token" in sql
    assert "DROP CONSTRAINT IF EXISTS ux_ops_crawler_task_attempt_observation_fk" in sql
    assert "DROP CONSTRAINT IF EXISTS fk_ops_crawler_task_observation_attempt" in sql
    assert sql.count("ADD CONSTRAINT ux_ops_crawler_task_attempt_identity") == 1
    assert sql.count("ADD CONSTRAINT fk_ops_crawler_task_observation_attempt") == 1


def test_release_artifact_rollout_desired_state_and_reports_are_separate():
    sql = _sql()

    for table in (
        "ops_crawler_release_artifacts",
        "ops_crawler_release_rollouts",
        "ops_crawler_worker_desired_state",
        "ops_crawler_release_reports",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql

    assert "artifact_digest TEXT PRIMARY KEY" in sql
    assert "UNIQUE (environment, rollout_epoch)" in sql
    assert "PRIMARY KEY (environment, worker_key)" in sql
    assert "desired_generation BIGINT NOT NULL" in sql
    assert "artifact_path TEXT NOT NULL" in sql
    assert "artifact_digest ~ '^[0-9a-f]{64}$'" in sql
    assert "size_bytes BIGINT NOT NULL" in sql
    assert "size_bytes BETWEEN 1 AND 536870912" in sql
    assert "artifact_uri" not in sql
    assert "artifact_path !~ '://'" in sql
    assert "signature TEXT" in sql
    assert "key_id TEXT" in sql
    assert "(signature IS NULL AND key_id IS NULL)" in sql
    assert "cohort TEXT NOT NULL DEFAULT 'stable'" in sql
    assert "cohort IN ('canary', 'stable')" in sql
    assert "ux_ops_crawler_release_rollouts_active" in sql
    assert "ux_ops_crawler_release_artifacts_code_version" in sql
    assert "ON ops_crawler_release_artifacts (code_version)" in sql
    assert "BEFORE UPDATE OR DELETE ON ops_crawler_release_artifacts" in sql
    assert "BEFORE UPDATE OR DELETE ON ops_crawler_release_reports" in sql


def test_rollout_worker_history_uses_a_forward_ledgered_migration():
    root = Path(__file__).resolve().parents[1]
    base = _sql()
    forward_path = (
        root
        / "DB"
        / "crawler_control_migrations"
        / "20260812_004_rollout_worker_snapshots.sql"
    )
    forward = forward_path.read_text(encoding="utf-8")
    installer = (root / "tools" / "ensure_crawler_control_schema.py").read_text(
        encoding="utf-8"
    )
    builder = (root / "tools" / "build_crawler_control_release.py").read_text(
        encoding="utf-8"
    )

    assert "CREATE TABLE IF NOT EXISTS ops_crawler_rollout_worker_snapshots" not in base
    assert "CREATE TABLE ops_crawler_rollout_worker_snapshots" in forward
    assert "requires the marked crawler-control database" in forward
    assert "zz_enforce_crawler_rollout_snapshot_commit" in forward
    assert "worker_snapshot_required BOOLEAN" in forward
    assert "SET worker_snapshot_required = FALSE" in forward
    assert "NEW.worker_snapshot_required := TRUE" in forward
    assert "NEW.rollout_epoch > OLD.rollout_epoch" in forward
    assert "IF NEW.worker_snapshot_required IS NOT TRUE" in forward
    assert "BEFORE UPDATE OR DELETE ON ops_crawler_rollout_worker_snapshots" in forward
    assert "ROLLOUT_SNAPSHOT_MIGRATION_VERSION" in installer
    assert "rollout_snapshot_recorded == rollout_snapshot_checksum" in installer
    assert "cursor.execute(rollout_snapshot_migration)" in installer
    assert '"DB/crawler_control_migrations/20260812_004_rollout_worker_snapshots.sql"' in builder


def test_attempt_release_generation_uses_a_forward_ledgered_fence():
    root = Path(__file__).resolve().parents[1]
    base = _sql()
    path = (
        root
        / "DB"
        / "crawler_control_migrations"
        / "20260812_005_attempt_release_generation.sql"
    )
    forward = path.read_text(encoding="utf-8")
    installer = (root / "tools" / "ensure_crawler_control_schema.py").read_text(
        encoding="utf-8"
    )
    builder = (root / "tools" / "build_crawler_control_release.py").read_text(
        encoding="utf-8"
    )
    setup = (root / "deploy" / "ubuntu" / "setup_distributed_crawler_control.sh").read_text(
        encoding="utf-8"
    )

    assert "release_generation BIGINT" not in base
    assert "ADD COLUMN rollout_id UUID" in forward
    assert "ADD COLUMN release_generation BIGINT" in forward
    assert "chk_ops_crawler_task_attempt_release_pair" in forward
    assert "fk_ops_crawler_task_attempt_rollout" in forward
    assert "enforce_crawler_attempt_release_generation_insert" in forward
    assert "snapshot_required IS FALSE" in forward
    assert "NEW.rollout_id := NULL" in forward
    assert "NEW.release_generation := NULL" in forward
    assert "NEW.rollout_id := desired_rollout_id" in forward
    assert "NEW.release_generation := desired_generation" in forward
    assert "ATTEMPT_RELEASE_GENERATION_MIGRATION_VERSION" in installer
    assert "attempt_release_generation_recorded" in installer
    assert "cursor.execute(attempt_release_generation_migration)" in installer
    assert '"DB/crawler_control_migrations/20260812_005_attempt_release_generation.sql"' in builder
    assert "20260812_005_attempt_release_generation.sql" in setup


def test_shared_quality_reads_use_a_forward_staging_only_rls_fence():
    root = Path(__file__).resolve().parents[1]
    path = (
        root
        / "DB"
        / "crawler_control_migrations"
        / "20260812_007_quality_environment_isolation.sql"
    )
    forward = path.read_text(encoding="utf-8")
    preflight = (root / "tools" / "preflight_distributed_crawler_control.py").read_text(
        encoding="utf-8"
    )

    assert "ALTER TABLE course_quality_score FORCE ROW LEVEL SECURITY" in forward
    assert "ALTER TABLE ops_quality_issues FORCE ROW LEVEL SECURITY" in forward
    assert forward.count("AS RESTRICTIVE FOR SELECT") == 2
    assert forward.count("pg_has_role(session_user, 'mooncen_crawler_api', 'member')") == 2
    assert forward.count("CASE") == 2
    assert forward.count("current_crawler_api_environment() = 'staging'") == 2
    assert "QUALITY_ENVIRONMENT_ISOLATION_MIGRATION_FILE.stem" in preflight
    assert '"course_quality_score"' in preflight
    assert '"ops_quality_issues"' in preflight


def test_migration_is_rerunnable_and_does_not_modify_course_staging_writes():
    sql = _sql()

    assert sql.count("CREATE TABLE IF NOT EXISTS") == 10
    assert "CREATE INDEX IF NOT EXISTS" in sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS" in sql
    assert "DROP CONSTRAINT IF EXISTS chk_ops_jobs_status" in sql
    assert sql.count("DROP TRIGGER IF EXISTS") == 3

    active = "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--")
    )
    assert "crawl_staging" not in active
    assert not re.search(r"ALTER\s+TABLE\s+(courses|branches)\b", active, re.IGNORECASE)
    assert "COMMIT;" not in sql
    assert "SET statement_timeout" not in sql
    assert "SET lock_timeout" not in sql


def test_worker_permissions_are_append_or_fenced_update_only():
    sql = _sql()

    assert "REVOKE ALL ON TABLE" in sql
    assert "FROM PUBLIC" in sql
    assert "GRANT SELECT, INSERT ON TABLE ops_crawler_task_attempts\n            TO mooncen_crawler_worker" in sql
    assert "GRANT UPDATE (\n            status, finished_at, exit_code, error_code, error_message, metrics\n        ) ON TABLE ops_crawler_task_attempts TO mooncen_crawler_worker" in sql
    assert "GRANT SELECT, INSERT ON TABLE ops_crawler_task_observations\n            TO mooncen_crawler_worker" in sql
    assert (
        "GRANT SELECT ON TABLE ops_crawler_worker_desired_state\n"
        "            TO mooncen_crawler_worker"
    ) in sql

    crawler_grants = "\n".join(
        match.group(0)
        for match in re.finditer(
            r"GRANT[\s\S]*?TO mooncen_crawler_worker;",
            sql,
        )
    )
    assert "UPDATE ON TABLE ops_crawler_task_observations" not in crawler_grants
    assert "DELETE ON TABLE ops_crawler_task_observations" not in crawler_grants
    assert "UPDATE ON TABLE ops_crawler_release_reports" not in crawler_grants
    assert "DELETE ON TABLE ops_crawler_release_reports" not in crawler_grants
    for private_table in (
        "ops_crawler_batches",
        "ops_crawler_batch_tasks",
        "ops_crawler_release_artifacts",
        "ops_crawler_release_rollouts",
    ):
        assert f"GRANT SELECT ON TABLE {private_table}\n            TO mooncen_crawler_worker" not in sql


def test_finalizer_and_applier_have_separate_seal_and_apply_grants():
    sql = _sql()

    assert "GRANT SELECT ON TABLE ops_jobs TO mooncen_applier" in sql
    assert "GRANT UPDATE (status, started_at, finished_at)" in sql
    assert "GRANT SELECT, INSERT ON TABLE crawl_batches TO mooncen_crawler_finalizer" in sql
    assert "valid_courses, invalid_courses, result, updated_at" in sql
    applier_grants = "\n".join(
        statement
        for statement in sql.split(";")
        if statement.lstrip().startswith("GRANT")
        and statement.rstrip().endswith("TO mooncen_applier")
    )
    assert "INSERT ON TABLE ops_jobs" not in applier_grants
    assert "UPDATE ON TABLE ops_jobs" not in applier_grants


def test_role_convergence_regrants_control_plane_after_global_revoke():
    sql = _roles_sql()

    assert "GRANT SELECT, INSERT, UPDATE ON ops_crawler_batches TO mooncen_api" in sql
    assert "ops_crawler_release_reports TO mooncen_crawler_reporter" in sql
    assert "GRANT UPDATE (status, started_at, finished_at)" in sql
    assert "GRANT SELECT, INSERT, UPDATE ON crawl_batches TO mooncen_crawler" in sql
    assert "CREATE ROLE mooncen_crawler_worker NOLOGIN" in sql
    assert "GRANT mooncen_crawler TO mooncen_crawler_worker" not in sql
    assert "GRANT INSERT, UPDATE ON branches, courses TO mooncen_crawler_worker" in sql
    assert "GRANT INSERT, UPDATE, DELETE ON branches, courses TO mooncen_crawler_worker" not in sql
    assert (
        "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public\n"
        "    TO mooncen_api, mooncen_crawler, mooncen_crawler_worker, mooncen_applier;"
        not in sql
    )
    assert "GRANT USAGE, SELECT ON SEQUENCE crawler_run_log_id_seq" in sql
    assert "GRANT SELECT ON ops_crawler_worker_desired_state\n            TO mooncen_crawler_worker" in sql
    assert "GRANT SELECT, UPDATE ON ops_jobs TO mooncen_crawler_worker" in sql
    assert "GRANT INSERT ON ops_crawler_release_reports\n            TO mooncen_crawler_reporter" in sql
    assert "GRANT SELECT, INSERT ON ops_job_logs TO mooncen_api" in sql
    assert "GRANT SELECT, INSERT, UPDATE ON ops_jobs, ops_job_logs TO mooncen_api" not in sql
    assert "ops_crawler_release_reports TO mooncen_crawler_worker" not in sql
    assert "CREATE ROLE mooncen_crawler_control NOLOGIN" in sql
    assert "ops_crawler_batch_tasks TO mooncen_crawler_control" in sql
    assert "capture_fenced_crawler_snapshot()'" in sql
    assert "reviewed_routine := to_regprocedure" in sql
    assert "ops_crawler_control_database_marker" in sql
    assert "REVOKE ALL PRIVILEGES (%I) ON TABLE" in sql
