from __future__ import annotations

from uuid import uuid4

import pytest

from ops_agent import crawler_worker


JOB_ID = "11111111-1111-4111-8111-111111111111"
AGENT_ID = "22222222-2222-4222-8222-222222222222"
LEASE_TOKEN = "33333333-3333-4333-8333-333333333333"
BATCH_ID = "44444444-4444-4444-8444-444444444444"
ATTEMPT_ID = "55555555-5555-4555-8555-555555555555"
ROLLOUT_ID = "66666666-6666-4666-8666-666666666666"


def _config(*, environment: str = "production") -> crawler_worker.WorkerConfig:
    return crawler_worker.WorkerConfig(
        environment=environment,
        agent_id=uuid4(),
        poll_interval=1.0,
        command_timeout=300,
        lease_seconds=60,
        code_version="code-v1",
        artifact_digest="sha256:reviewed",
        config_revision="config-v1",
    )


def _job(*, retry_count: int = 0, max_retries: int = 0) -> dict[str, object]:
    return {
        "id": JOB_ID,
        "job_type": "crawler_run",
        "parameters": {
            "provider": "LOTTE",
            "batch_id": BATCH_ID,
            "allowed_output_providers": ["LOTTE"],
            "scheduled_providers": ["LOTTE"],
        },
        "agent_id": AGENT_ID,
        "lease_token": LEASE_TOKEN,
        "lease_epoch": 7,
        "attempt_no": 3,
        "attempt_id": ATTEMPT_ID,
        "retry_count": retry_count,
        "max_retries": max_retries,
        "_lease_seconds": 60,
    }


def _desired() -> dict[str, object]:
    return {
        "rollout_id": ROLLOUT_ID,
        "generation": 7,
        "desired_status": "active",
        "code_version": "code-v1",
        "artifact_digest": "sha256:reviewed",
        "config_revision": "config-v1",
    }


class _Cursor:
    def __init__(self, connection: "_Connection") -> None:
        self.connection = connection
        self.rowcount = 0
        self._result = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, statement: str, parameters=None) -> None:
        self.connection.statements.append((statement, parameters))
        self._result = None
        self.rowcount = 1
        if "SELECT id::text" in statement:
            self._result = self.connection.selected_job
        elif "RETURNING id, status, agent_id::text" in statement:
            self._result = self.connection.claimed_lease
            self.rowcount = 1 if self._result is not None else 0
        elif "INSERT INTO ops_crawler_task_attempts" in statement and "RETURNING id::text" in statement:
            self._result = self.connection.claimed_attempt
            self.rowcount = 1 if self._result is not None else 0
        elif "SELECT 1" in statement and "FROM ops_jobs" in statement and "FOR UPDATE" in statement:
            self._result = (1,) if self.connection.job_update_rowcount == 1 else None
        elif "UPDATE ops_jobs" in statement:
            self.rowcount = self.connection.job_update_rowcount

    def fetchone(self):
        return self._result

    def fetchall(self):
        return []


class _Connection:
    def __init__(self) -> None:
        self.statements: list[tuple[str, object]] = []
        self.commits = 0
        self.rollbacks = 0
        self.job_update_rowcount = 1
        self.selected_job = {
            "id": JOB_ID,
            "job_type": "crawler_run",
            "status": "queued",
            "environment": "production",
            "parameters": {"provider": "LOTTE"},
            "target_key": "LOTTE",
            "retry_count": 0,
            "max_retries": 1,
            "attempt_no": 0,
            "required_code_version": "code-v1",
            "artifact_digest": "sha256:reviewed",
            "config_revision": "config-v1",
        }
        self.claimed_lease = {
            "id": JOB_ID,
            "status": "assigned",
            "agent_id": AGENT_ID,
            "lease_token": LEASE_TOKEN,
            "lease_epoch": 1,
            "leased_until": "later",
            "attempt_no": 1,
        }
        self.claimed_attempt = {
            "id": ATTEMPT_ID,
            "rollout_id": ROLLOUT_ID,
            "release_generation": 7,
        }

    def cursor(self, **_kwargs):
        return _Cursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def test_claim_atomically_issues_fenced_attempt_and_filters_compatibility() -> None:
    connection = _Connection()
    config = _config()
    connection.selected_job["environment"] = config.environment
    connection.claimed_lease["agent_id"] = str(config.agent_id)

    claimed = crawler_worker._claim_job(connection, config, _desired())

    assert claimed is not None
    assert claimed["lease_token"] == LEASE_TOKEN
    assert claimed["lease_epoch"] == 1
    assert claimed["attempt_no"] == 1
    assert claimed["attempt_id"] == ATTEMPT_ID
    assert claimed["rollout_id"] == ROLLOUT_ID
    assert claimed["release_generation"] == 7
    selection, selection_parameters = connection.statements[0]
    assert "FOR UPDATE SKIP LOCKED" in selection
    assert "available_at <= CURRENT_TIMESTAMP" in selection
    assert "cancel_requested_at IS NULL" in selection
    assert "required_code_version = %s" in selection
    assert "artifact_digest = %s" in selection
    assert "config_revision = %s" in selection
    assert selection_parameters[-3:] == ["code-v1", "sha256:reviewed", "config-v1"]
    assignment = connection.statements[1][0]
    assert "lease_epoch = lease_epoch + 1" in assignment
    assert "attempt_no = attempt_no + 1" in assignment
    assert "leased_until = CURRENT_TIMESTAMP + make_interval" in assignment
    assert "INSERT INTO ops_crawler_task_attempts" in connection.statements[2][0]
    attempt_parameters = connection.statements[2][1]
    assert attempt_parameters[-2:] == (ROLLOUT_ID, 7)
    claim_observation = connection.statements[3][0]
    assert "INSERT INTO ops_crawler_task_observations" in claim_observation
    assert "attempt_id, job_id, attempt_no, lease_epoch" in claim_observation
    assert connection.commits == 1
    assert connection.rollbacks == 0


def test_claim_race_rolls_back_without_creating_an_attempt() -> None:
    connection = _Connection()
    config = _config()
    connection.claimed_lease = None

    assert crawler_worker._claim_job(connection, config, _desired()) is None

    statements = "\n".join(statement for statement, _ in connection.statements)
    assert "FOR UPDATE SKIP LOCKED" in statements
    assert "INSERT INTO ops_crawler_task_attempts" not in statements
    assert connection.rollbacks == 1
    assert connection.commits == 0


def test_protected_claim_requires_the_loaded_desired_release_generation() -> None:
    with pytest.raises(RuntimeError, match="requires active central desired state"):
        crawler_worker._claim_job(_Connection(), _config(), None)


def test_production_worker_requires_complete_execution_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "OPS_CRAWLER_CODE_VERSION",
        "OPS_CRAWLER_ARTIFACT_DIGEST",
        "OPS_CRAWLER_CONFIG_REVISION",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="Production crawler worker requires"):
        crawler_worker.worker_compatibility_from_environment("production")

    assert crawler_worker.worker_compatibility_from_environment("development") == ("", "", "")


def test_production_worker_requires_colocated_queue_and_staging_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CRAWL_WRITE_MODE", "staging")
    monkeypatch.setenv("OPS_QUEUE_DB_HOST", "crawler-db.internal")
    monkeypatch.setenv("OPS_QUEUE_DB_PORT", "5432")
    monkeypatch.setenv("OPS_QUEUE_DB_NAME", "mooncen_staging")
    monkeypatch.setenv("CRAWL_STAGING_DB_HOST", "crawler-db.internal")
    monkeypatch.setenv("CRAWL_STAGING_DB_PORT", "5432")
    monkeypatch.setenv("CRAWL_STAGING_DB_NAME", "mooncen_staging")
    monkeypatch.setenv("OPS_CRAWLER_SHARED_DB_HOST", "crawler-db.internal")
    monkeypatch.setenv("OPS_CRAWLER_SHARED_DB_PORT", "5432")
    monkeypatch.setenv("OPS_CRAWLER_SHARED_DB_NAME", "mooncen_staging")
    monkeypatch.setenv("OPS_QUEUE_DB_USER", "queue_role")
    monkeypatch.setenv("OPS_QUEUE_DB_PASSWORD", "shared-secret")
    monkeypatch.setenv("CRAWL_STAGING_DB_USER", "queue_role")
    monkeypatch.setenv("CRAWL_STAGING_DB_PASSWORD", "shared-secret")

    crawler_worker.validate_control_plane_colocation("production")
    crawler_worker.validate_control_plane_colocation("staging")

    monkeypatch.setenv("CRAWL_STAGING_DB_HOST", "different-db.internal")
    with pytest.raises(RuntimeError, match="must match exactly"):
        crawler_worker.validate_control_plane_colocation("production")

    monkeypatch.setenv("CRAWL_STAGING_DB_HOST", "crawler-db.internal")
    monkeypatch.setenv("CRAWL_STAGING_DB_USER", "legacy_writer")
    with pytest.raises(RuntimeError, match="same dedicated crawler worker login"):
        crawler_worker.validate_control_plane_colocation("production")


def test_colocation_gate_is_explicit_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "CRAWL_WRITE_MODE",
        "OPS_QUEUE_DB_HOST",
        "OPS_QUEUE_DB_PORT",
        "OPS_QUEUE_DB_NAME",
        "CRAWL_STAGING_DB_HOST",
        "CRAWL_STAGING_DB_PORT",
        "CRAWL_STAGING_DB_NAME",
        "OPS_CRAWLER_SHARED_DB_HOST",
        "OPS_CRAWLER_SHARED_DB_PORT",
        "OPS_CRAWLER_SHARED_DB_NAME",
        "OPS_QUEUE_DB_USER",
        "OPS_QUEUE_DB_PASSWORD",
        "CRAWL_STAGING_DB_USER",
        "CRAWL_STAGING_DB_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="CRAWL_WRITE_MODE=staging"):
        crawler_worker.validate_control_plane_colocation("production")

    crawler_worker.validate_control_plane_colocation("development")


class _RoleCursor:
    def __init__(self, row) -> None:
        self.row = row

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement: str, params=None) -> None:
        assert "pg_has_role(session_user" in statement
        assert params == ("mooncen_crawler_worker",)

    def fetchone(self):
        return self.row


class _RoleConnection:
    def __init__(self, row) -> None:
        self.row = row

    def cursor(self):
        return _RoleCursor(self.row)


def test_production_worker_requires_dedicated_database_role_membership() -> None:
    crawler_worker.assert_dedicated_worker_database_role(
        _RoleConnection((True, "crawler_worker_1")),
        "production",
    )

    with pytest.raises(RuntimeError, match="mooncen_crawler_worker"):
        crawler_worker.assert_dedicated_worker_database_role(
            _RoleConnection((False, "legacy_crawler")),
            "production",
        )

    crawler_worker.assert_dedicated_worker_database_role(
        _RoleConnection((False, "developer")),
        "development",
    )


def test_production_queue_credentials_do_not_fallback_to_legacy_crawler_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("OPS_QUEUE_DB_HOST", "crawler-db.internal")
    monkeypatch.setenv("OPS_QUEUE_DB_PORT", "5432")
    monkeypatch.setenv("OPS_QUEUE_DB_NAME", "mooncen_staging")
    monkeypatch.delenv("OPS_QUEUE_DB_USER", raising=False)
    monkeypatch.delenv("OPS_QUEUE_DB_PASSWORD", raising=False)
    monkeypatch.setenv("DB_CRAWLER_USER", "legacy_crawler")
    monkeypatch.setenv("DB_CRAWLER_PASSWORD", "legacy-secret")

    with pytest.raises(RuntimeError, match="explicit queue database credentials"):
        crawler_worker.queue_database_config()


def test_stale_heartbeat_token_reports_ownership_loss() -> None:
    connection = _Connection()
    connection.job_update_rowcount = 0

    refresh = crawler_worker._heartbeat(connection, _job(), progress=20)

    assert refresh is crawler_worker.JobLeaseRefresh.OWNERSHIP_LOST
    statement, parameters = connection.statements[0]
    assert "lease_token = %s" in statement
    assert "lease_epoch = %s" in statement
    assert "agent_id = %s" in statement
    assert "leased_until > CURRENT_TIMESTAMP" in statement
    assert parameters[-4:] == (JOB_ID, LEASE_TOKEN, 7, AGENT_ID)
    assert connection.commits == 1


def test_successful_heartbeat_appends_only_a_sampled_composite_observation() -> None:
    connection = _Connection()
    job = _job()

    refresh = crawler_worker._heartbeat(connection, job, progress=20)
    second_refresh = crawler_worker._heartbeat(connection, job, progress=21)

    assert refresh is crawler_worker.JobLeaseRefresh.REFRESHED
    assert second_refresh is crawler_worker.JobLeaseRefresh.REFRESHED
    observation, parameters = connection.statements[1]
    assert "INSERT INTO ops_crawler_task_observations" in observation
    assert "WHERE NOT EXISTS" in observation
    assert "attempt_id = %s" in observation
    assert "job_id = %s" in observation
    assert "attempt_no = %s" in observation
    assert "lease_epoch = %s" in observation
    assert parameters[-1] == crawler_worker.HEARTBEAT_OBSERVATION_INTERVAL_SECONDS
    assert (
        sum(
            "INSERT INTO ops_crawler_task_observations" in statement for statement, _parameters in connection.statements
        )
        == 1
    )


def test_stale_completion_cannot_update_attempt_or_crawler_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection()
    connection.job_update_rowcount = 0
    monkeypatch.setattr(crawler_worker, "_run_counts", lambda *_args: {})
    monkeypatch.setattr(
        crawler_worker,
        "_append_log",
        lambda *_args, **_kwargs: pytest.fail("a stale completion must not publish a log"),
    )

    disposition = crawler_worker._finish_job(
        connection,
        _job(),
        final_status="success",
        return_code=0,
    )

    assert disposition == "ownership_lost"
    statements = "\n".join(statement for statement, _ in connection.statements)
    assert "lease_token = %s" in statements
    assert "lease_epoch = %s" in statements
    # The explicit locking read rejects stale ownership before any mutable
    # runtime or terminal evidence is issued.
    assert "FOR UPDATE" in statements
    assert "UPDATE ops_crawler_task_attempts" not in statements
    assert "INSERT INTO ops_crawler_task_observations" not in statements
    assert "UPDATE ops_crawler_runs" not in statements
    assert "INSERT INTO ops_job_logs" not in statements
    assert connection.rollbacks == 1
    assert connection.commits == 0


def test_transient_failure_requeues_with_backoff_under_same_fence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection()
    monkeypatch.setattr(crawler_worker, "_run_counts", lambda *_args: {})
    monkeypatch.setattr(crawler_worker, "_append_log", lambda *_args, **_kwargs: None)

    disposition = crawler_worker._finish_job(
        connection,
        _job(retry_count=0, max_retries=1),
        final_status="failed",
        return_code=75,
        retryable=True,
        error_code="crawler_lock_contention",
    )

    assert disposition == "retry_scheduled"
    assert "SELECT 1" in connection.statements[0][0]
    assert "FOR UPDATE" in connection.statements[0][0]
    assert "UPDATE ops_crawler_runs" in connection.statements[1][0]
    assert "INSERT INTO ops_job_logs" in connection.statements[2][0]
    attempt_update = connection.statements[3][0]
    assert "UPDATE ops_crawler_task_attempts" in attempt_update
    assert "INSERT INTO ops_crawler_task_observations" in connection.statements[4][0]
    job_update = connection.statements[5][0]
    assert "status = 'queued'" in job_update
    assert "available_at = CURRENT_TIMESTAMP + make_interval" in job_update
    assert "retry_count = retry_count + 1" in job_update
    assert "lease_token = NULL" in job_update
    assert connection.commits == 1


def test_ownership_loss_terminates_child_and_never_finishes_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Process:
        stdout = iter(())
        returncode = None

        def poll(self):
            return self.returncode

    process = Process()
    started: dict[str, object] = {}
    terminated: list[bool] = []

    def popen(*_args, **kwargs):
        started.update(kwargs)
        return process

    def terminate() -> None:
        terminated.append(True)
        process.returncode = -15

    monkeypatch.setattr(
        crawler_worker,
        "build_crawler_execution",
        lambda _parameters: (["crawler"], {}),
    )
    monkeypatch.setattr(crawler_worker, "_mark_running", lambda *_args: True)
    monkeypatch.setattr(crawler_worker, "_append_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(crawler_worker, "_cancellation_requested", lambda *_args: False)
    monkeypatch.setattr(
        crawler_worker,
        "_heartbeat",
        lambda *_args, **_kwargs: crawler_worker.JobLeaseRefresh.OWNERSHIP_LOST,
    )
    monkeypatch.setattr(crawler_worker.subprocess, "Popen", popen)
    monkeypatch.setattr(crawler_worker, "_terminate_active_process", terminate)
    monkeypatch.setattr(
        crawler_worker,
        "_finish_job",
        lambda *_args, **_kwargs: pytest.fail("a fenced child must not publish a result"),
    )

    crawler_worker.execute_job(_Connection(), _job(), _config())

    assert terminated == [True]
    environment = started["env"]
    assert isinstance(environment, dict)
    assert environment["CRAWL_JOB_ID"] == JOB_ID
    assert environment["CRAWL_LEASE_TOKEN"] == LEASE_TOKEN
    assert environment["CRAWL_LEASE_EPOCH"] == "7"
    assert environment["CRAWL_ATTEMPT_NO"] == "3"
    assert environment["CRAWL_REQUIRE_LEASE"] == "1"
    assert environment["CRAWL_BATCH_ID"] == BATCH_ID
    assert environment["CRAWL_DISTRIBUTED_TASK"] == "1"
    assert crawler_worker.ACTIVE_PROCESS is None
