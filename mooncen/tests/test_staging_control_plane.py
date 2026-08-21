from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

import run_crawlers
from tools.apply_staging_batch import load_rows


ROOT = Path(__file__).resolve().parents[1]


def test_staging_control_plane_fences_every_mutation_and_preserves_snapshots() -> None:
    sql = (ROOT / "DB" / "staging_control_plane.sql").read_text(encoding="utf-8")

    for predicate in (
        "lease_token = session_token",
        "lease_epoch = session_epoch",
        "attempt_no = session_attempt",
        "status = 'running'",
        "leased_until > clock_timestamp()",
    ):
        assert predicate in sql
    assert "session_batch <> job_batch" in sql
    assert "escaped its leased provider scope" in sql
    assert "crawl_staging.fenced_branch_snapshots" in sql
    assert "crawl_staging.fenced_course_snapshots" in sql
    assert "task.allowed_output_providers" in sql
    assert "pg_has_role(session_user, 'mooncen_crawler_worker', 'member')" in sql
    assert "IF NOT dedicated_worker THEN" in sql
    assert "fenced crawler writes require an enrolled dedicated worker login" in sql
    assert "attempt_id, job_id, attempt_no, lease_epoch" in sql
    assert "BEFORE UPDATE OR DELETE ON crawl_staging.fenced_course_snapshots" in sql
    assert "SECURITY DEFINER" in sql
    assert "REVOKE ALL ON FUNCTION public.enforce_current_crawler_lease() FROM PUBLIC" in sql


def test_dedicated_worker_login_is_bound_to_its_agent_and_rows() -> None:
    sql = (ROOT / "DB" / "staging_control_plane.sql").read_text(encoding="utf-8")

    assert "'crawler-worker:' || session_user" in sql
    assert "CREATE TRIGGER zz_enforce_crawler_worker_job_transition" in sql
    assert "crawler worker attempted to use another agent lease" in sql
    assert "desired.agent_id = worker_agent" in sql
    assert "desired.desired_status = 'active'" in sql
    assert "desired.artifact_digest = OLD.artifact_digest" in sql
    assert "ALTER TABLE public.ops_jobs ENABLE ROW LEVEL SECURITY" in sql
    assert "CREATE POLICY crawler_worker_job_scope" in sql
    assert "CREATE POLICY crawler_worker_attempt_scope" in sql
    assert "CREATE POLICY crawler_worker_observation_scope" in sql
    assert "CREATE POLICY crawler_worker_release_report_scope" in sql
    assert "CREATE POLICY crawler_worker_job_log_insert_scope" in sql
    assert "CREATE POLICY crawler_worker_run_update_scope" in sql
    assert "CREATE POLICY crawler_nonworker_run_insert_scope" in sql
    assert "public.is_live_crawler_worker_job(ops_job_logs.job_id)" in sql
    assert "public.is_live_crawler_worker_job(ops_crawler_runs.job_id)" in sql
    assert "job.leased_until > clock_timestamp()" in sql
    assert "attempt.lease_token = job.lease_token" in sql
    assert "CREATE TRIGGER zz_enforce_crawler_release_report_timestamp" in sql
    assert "NEW.reported_at := clock_timestamp()" in sql
    assert "NEW.created_at := NEW.reported_at" in sql
    assert "jsonb_typeof(NEW.health->'healthy') IS DISTINCT FROM 'boolean'" in sql
    assert "crawler release report status and health contract differs" in sql
    assert "attempt.agent_id = job.agent_id" in sql
    assert "agent.credential_hint = 'crawler-worker:' || session_user" in sql
    assert "CREATE CONSTRAINT TRIGGER zz_enforce_crawler_worker_active_attempt" in sql
    assert "DEFERRABLE INITIALLY DEFERRED" in sql
    assert "active lease has no matching attempt evidence" in sql
    assert "CREATE TRIGGER zz_enforce_crawler_worker_attempt_insert" in sql
    assert "attempt does not match its active lease" in sql
    assert sql.count("FOR UPDATE OF job, attempt") == 3
    assert "CREATE TRIGGER zz_enforce_crawler_worker_attempt_transition" in sql
    assert "one-time terminal seal" in sql
    assert "CREATE TRIGGER zz_enforce_crawler_worker_observation_insert" in sql
    assert "NEW.observed_at := clock_timestamp()" in sql
    assert "CREATE CONSTRAINT TRIGGER zz_enforce_crawler_worker_terminal_job_commit" in sql
    assert "CREATE CONSTRAINT TRIGGER zz_enforce_crawler_worker_terminal_attempt_commit" in sql
    assert "OLD.provider IS DISTINCT FROM NEW.provider" in sql
    assert "OLD.branch_code IS DISTINCT FROM NEW.branch_code" in sql
    assert "OLD.provider_course_id IS DISTINCT FROM NEW.provider_course_id" in sql
    assert "course escaped its provider branch ownership" in sql


class _SnapshotCursor:
    def __init__(self) -> None:
        self.rows = []
        self.executed: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql: str, _params) -> None:
        self.executed.append(sql)
        if "crawl_staging.branch_snapshots" in sql:
            self.rows = [
                {
                    "row_data": {
                        "id": "branch-1",
                        "provider": "SAFE_PROVIDER",
                        "branch_code": "B1",
                        "name": "Safe branch",
                    }
                }
            ]
        elif "crawl_staging.course_snapshots" in sql:
            self.rows = [
                {
                    "row_data": {
                        "id": "course-row-1",
                        "provider": "SAFE_PROVIDER",
                        "provider_course_id": "C1",
                        "branch_id": "branch-1",
                    }
                }
            ]
        else:
            raise AssertionError("canonical mutable staging tables must not be read when snapshots exist")

    def fetchall(self):
        return self.rows


class _SnapshotConnection:
    def __init__(self) -> None:
        self.cursor_value = _SnapshotCursor()

    def cursor(self, **_kwargs):
        return self.cursor_value


def test_applier_prefers_per_batch_snapshots_over_mutable_staging_rows() -> None:
    connection = _SnapshotConnection()

    branches, courses = load_rows(connection, "batch-1")

    assert branches[0]["branch_code"] == "B1"
    assert courses[0]["branch_provider"] == "SAFE_PROVIDER"
    assert courses[0]["branch_code"] == "B1"
    assert courses[0]["branch_name"] == "Safe branch"
    assert len(connection.cursor_value.executed) == 2


class _FencedSnapshotCursor(_SnapshotCursor):
    one = None

    def execute(self, sql: str, _params) -> None:
        self.executed.append(sql)
        self.one = None
        if "SELECT COUNT(*) AS selected_count" in sql:
            self.rows = []
            self.one = {"selected_count": 1}
        elif "crawl_staging.fenced_branch_snapshots" in sql:
            self.rows = [
                {
                    "row_data": {
                        "id": "branch-2",
                        "provider": "SAFE_PROVIDER",
                        "branch_code": "B2",
                        "name": "Fenced branch",
                    }
                }
            ]
        elif "crawl_staging.fenced_course_snapshots" in sql:
            self.rows = [
                {
                    "row_data": {
                        "id": "course-row-2",
                        "provider": "SAFE_PROVIDER",
                        "provider_course_id": "C2",
                        "branch_id": "branch-2",
                    }
                }
            ]
        else:
            raise AssertionError("control-plane batches must use only attempt-bound snapshots")

    def fetchone(self):
        return self.one


class _FencedSnapshotConnection:
    def __init__(self) -> None:
        self.cursor_value = _FencedSnapshotCursor()

    def cursor(self, **_kwargs):
        return self.cursor_value


def test_applier_selects_only_attempts_sealed_by_the_control_plane() -> None:
    batch_id = "00000000-0000-0000-0000-000000000001"
    connection = _FencedSnapshotConnection()
    result = {
        "control_plane": True,
        "control_batch_id": batch_id,
        "selected_attempts": [
            {
                "attempt_id": "00000000-0000-0000-0000-000000000011",
                "job_id": "00000000-0000-0000-0000-000000000010",
                "attempt_no": 2,
                "lease_epoch": 2,
            }
        ],
    }

    branches, courses = load_rows(
        connection,
        batch_id,
        batch_result=result,
    )

    assert branches[0]["branch_code"] == "B2"
    assert courses[0]["branch_name"] == "Fenced branch"
    assert "ops_crawler_batch_tasks" in connection.cursor_value.executed[0]
    assert all(
        "fenced_" in sql
        for sql in connection.cursor_value.executed[1:]
    )


def test_applier_rejects_control_plane_batch_without_selected_attempts() -> None:
    batch_id = "00000000-0000-0000-0000-000000000001"
    with pytest.raises(RuntimeError, match="selected attempt"):
        load_rows(
            _FencedSnapshotConnection(),
            batch_id,
            batch_result={
                "control_plane": True,
                "control_batch_id": batch_id,
                "selected_attempts": [],
            },
        )


def test_distributed_task_keeps_central_batch_and_requires_staging_fence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_id = str(uuid4())
    monkeypatch.setenv("CRAWL_DISTRIBUTED_TASK", "true")
    monkeypatch.setenv("CRAWL_WRITE_MODE", "staging")
    monkeypatch.setenv("CRAWL_REQUIRE_LEASE", "true")
    monkeypatch.setenv("CRAWL_BATCH_ID", batch_id)
    job_id = str(uuid4())
    monkeypatch.setenv("CRAWL_JOB_ID", job_id)
    monkeypatch.setenv("CRAWL_ATTEMPT_NO", "2")

    assert run_crawlers.distributed_task_batch_id() == batch_id
    assert run_crawlers.distributed_progress_run_id(batch_id) == f"{batch_id}:{job_id}:2"

    monkeypatch.setenv("CRAWL_REQUIRE_LEASE", "false")
    with pytest.raises(ValueError, match="fenced lease"):
        run_crawlers.distributed_task_batch_id()

    source = (ROOT / "run_crawlers.py").read_text(encoding="utf-8")
    assert "crawl_batch_id = distributed_batch_id or make_crawl_batch_id(cycle)" in source
    assert "progress_run_id = (" in source
    assert "run_id=progress_run_id" in source
    assert "if not distributed_batch_id and not begin_staging_batch" in source
    assert "if distributed_batch_id\n                else finish_staging_batch" in source
