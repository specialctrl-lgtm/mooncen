from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from ops_agent import deployment_worker


JOB_ID = "11111111-1111-4111-8111-111111111111"
BASE_COMMIT = "1" * 40
SOURCE_TREE = "2" * 40
SOURCE_COMMIT = "3" * 40
LEASE_TOKEN = "22222222-2222-4222-8222-222222222222"
LEASE_EPOCH = 17


def _leased_job(
    *,
    job_id: str = JOB_ID,
    parameters: dict[str, object] | None = None,
    lease_seconds: int = 300,
) -> dict[str, object]:
    return {
        "id": job_id,
        "agent_id": JOB_ID,
        "lease_token": LEASE_TOKEN,
        "lease_epoch": LEASE_EPOCH,
        "_lease_seconds": lease_seconds,
        "parameters": parameters or {},
    }


def _config(
    *,
    agent: bool = False,
    stale_after_seconds: int = 300,
    container_only: bool = False,
) -> deployment_worker.WorkerConfig:
    return deployment_worker.WorkerConfig(
        environment="production",
        agent_id=UUID(JOB_ID) if agent else None,
        poll_interval=0.5,
        command_timeout=300,
        stale_after_seconds=stale_after_seconds,
        container_only=container_only,
    )


def _install_reviewed_execution_harness(monkeypatch, tmp_path: Path):
    finished: list[dict[str, object]] = []
    released: list[tuple[str, str]] = []
    reviewed = {
        "target": "cloud",
        "target_commit": BASE_COMMIT,
        "target_identity": "4" * 64,
        "skip_workers": False,
        "source_tree": SOURCE_TREE,
    }
    monkeypatch.setattr(deployment_worker, "_mark_running", lambda *_args: True)
    monkeypatch.setattr(
        deployment_worker,
        "_publish_worker_heartbeat_resilient",
        lambda: None,
    )
    monkeypatch.setattr(
        deployment_worker,
        "_touch_deployment_agent_resilient",
        lambda active, _config: active,
    )
    monkeypatch.setattr(
        deployment_worker,
        "_report_log_with_reconnect",
        lambda active, *_args, **_kwargs: active,
    )
    monkeypatch.setattr(
        deployment_worker,
        "_try_reconnect_queue",
        lambda active: active,
    )
    monkeypatch.setattr(
        deployment_worker,
        "_flush_spooled_logs",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(deployment_worker, "_cancellation_requested", lambda *_args: False)
    monkeypatch.setattr(
        deployment_worker,
        "_assert_native_deployment_not_mixed_with_container",
        lambda *_args: True,
    )
    monkeypatch.setattr(deployment_worker, "deployment_readiness", lambda: {"available": True})
    monkeypatch.setattr(
        deployment_worker,
        "validated_parameters",
        lambda *_args, **_kwargs: reviewed,
    )
    monkeypatch.setattr(
        deployment_worker,
        "create_deployment_snapshot_commit",
        lambda **_kwargs: SOURCE_COMMIT,
    )
    monkeypatch.setattr(
        deployment_worker,
        "preserve_deployment_release_reference",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        deployment_worker,
        "_build_deployment_command",
        lambda *_args, **_kwargs: ["deploy"],
    )
    monkeypatch.setattr(deployment_worker, "_record_source_snapshot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        deployment_worker,
        "_finish_job",
        lambda *_args, **kwargs: finished.append(kwargs) or "written",
    )
    monkeypatch.setattr(
        deployment_worker,
        "release_deployment_snapshot_reference",
        lambda reference, commit: released.append((reference, commit)),
    )
    monkeypatch.setattr(deployment_worker, "ACTIVE_PROCESS", None)
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    monkeypatch.setattr(
        deployment_worker,
        "_create_deployment_runtime_directory",
        lambda *_args, **_kwargs: runtime,
    )
    monkeypatch.setattr(
        deployment_worker,
        "_remove_deployment_runtime_directory_resilient",
        lambda *_args, **_kwargs: True,
    )
    return finished, released


def test_cancelled_assignment_is_fenced_before_snapshot_preparation(monkeypatch) -> None:
    connection = object()
    monkeypatch.setattr(deployment_worker, "_mark_running", lambda *_args: False)
    monkeypatch.setattr(
        deployment_worker,
        "deployment_readiness",
        lambda: pytest.fail("a terminal job must not begin deployment preparation"),
    )

    assert (
        deployment_worker.execute_job(
            connection,
            _leased_job(),
            _config(),
        )
        is connection
    )


def test_job_enters_running_preparation_before_readiness(monkeypatch) -> None:
    events: list[str] = []
    finished: list[dict[str, object]] = []
    connection = object()
    monkeypatch.setattr(
        deployment_worker,
        "_mark_running",
        lambda *_args: events.append("running") or True,
    )
    monkeypatch.setattr(
        deployment_worker,
        "deployment_readiness",
        lambda: events.append("readiness") or (_ for _ in ()).throw(ValueError("blocked")),
    )
    monkeypatch.setattr(
        deployment_worker,
        "_publish_worker_heartbeat_resilient",
        lambda: None,
    )
    monkeypatch.setattr(
        deployment_worker,
        "_touch_deployment_agent_resilient",
        lambda active, _config: active,
    )
    monkeypatch.setattr(
        deployment_worker,
        "_heartbeat",
        lambda *_args: deployment_worker.JobLeaseRefresh.REFRESHED,
    )
    monkeypatch.setattr(
        deployment_worker,
        "_report_log_with_reconnect",
        lambda active, *_args, **_kwargs: active,
    )
    monkeypatch.setattr(
        deployment_worker,
        "_cancellation_requested",
        lambda *_args: False,
    )
    monkeypatch.setattr(
        deployment_worker,
        "_finish_job",
        lambda *_args, **kwargs: finished.append(kwargs) or "written",
    )

    deployment_worker.execute_job(
        connection,
        _leased_job(),
        _config(),
    )

    assert events == ["running", "readiness"]
    assert finished[0]["final_status"] == "blocked"


def test_preparation_monitor_refreshes_agent_and_job_and_observes_cancel(
    monkeypatch,
) -> None:
    calls: list[str] = []

    class Connection:
        def close(self) -> None:
            calls.append("close")

    connection = Connection()
    monkeypatch.setattr(deployment_worker, "connect_queue", lambda: connection)
    monkeypatch.setattr(
        deployment_worker,
        "_publish_worker_heartbeat_resilient",
        lambda: calls.append("local"),
    )
    monkeypatch.setattr(
        deployment_worker,
        "_touch_deployment_agent_resilient",
        lambda active, _config: calls.append("agent") or active,
    )
    monkeypatch.setattr(
        deployment_worker,
        "_heartbeat",
        lambda *_args: calls.append("job")
        or deployment_worker.JobLeaseRefresh.REFRESHED,
    )
    monkeypatch.setattr(
        deployment_worker,
        "_cancellation_requested",
        lambda *_args: calls.append("cancel") or True,
    )

    stop, cancelled, ownership_lost, lease_expired, thread = (
        deployment_worker._start_preparation_monitor(
            _config(agent=True),
            _leased_job(lease_seconds=60),
            deployment_worker.JobLeaseTracker(60),
        )
    )
    assert cancelled.wait(timeout=1)
    deployment_worker._stop_preparation_monitor(stop, thread)

    assert not ownership_lost.is_set()
    assert not lease_expired.is_set()
    assert calls[:4] == ["local", "agent", "job", "cancel"]
    assert calls[-1] == "close"


def test_preparation_monitor_fences_a_definitively_lost_job(monkeypatch) -> None:
    calls: list[str] = []

    class Connection:
        def close(self) -> None:
            calls.append("close")

    monkeypatch.setattr(deployment_worker, "connect_queue", Connection)
    monkeypatch.setattr(
        deployment_worker,
        "_publish_worker_heartbeat_resilient",
        lambda: None,
    )
    monkeypatch.setattr(
        deployment_worker,
        "_touch_deployment_agent_resilient",
        lambda active, _config: active,
    )
    monkeypatch.setattr(
        deployment_worker,
        "_heartbeat",
        lambda *_args: deployment_worker.JobLeaseRefresh.OWNERSHIP_LOST,
    )
    monkeypatch.setattr(
        deployment_worker,
        "_cancellation_requested",
        lambda *_args: pytest.fail("a lost job must not be treated as a fresh cancellation query"),
    )

    stop, cancelled, ownership_lost, lease_expired, thread = (
        deployment_worker._start_preparation_monitor(
            _config(agent=True, stale_after_seconds=60),
            _leased_job(lease_seconds=60),
            deployment_worker.JobLeaseTracker(60),
        )
    )
    assert ownership_lost.wait(timeout=1)
    deployment_worker._stop_preparation_monitor(stop, thread)

    assert not cancelled.is_set()
    assert not lease_expired.is_set()
    assert calls == ["close"]


def test_local_job_lease_deadline_precedes_the_shared_database_reaper() -> None:
    minimum = deployment_worker.JobLeaseTracker(60, confirmed_at=10.0)
    default = deployment_worker.JobLeaseTracker(300, confirmed_at=10.0)

    assert minimum.local_deadline_seconds == 30.0
    assert not minimum.expired(now=39.999)
    assert minimum.expired(now=40.0)
    assert default.local_deadline_seconds == 240.0
    assert not default.expired(now=249.999)
    assert default.expired(now=250.0)


class _SqlCursor:
    def __init__(self, connection: "_SqlConnection") -> None:
        self.connection = connection
        self.rowcount = connection.rowcount
        self.statement = ""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, statement: str, parameters=None) -> None:
        self.statement = statement
        self.connection.statements.append((statement, parameters))

    def fetchone(self):
        if "nextval" in self.statement:
            return {"claim_epoch": LEASE_EPOCH + 1}
        if "pg_try_advisory_xact_lock" in self.statement:
            return (True,)
        if "SELECT EXISTS" in self.statement:
            return (False,)
        if "RETURNING status" in self.statement:
            return self.connection.update_result
        if "SELECT status, error_code, result" in self.statement:
            return self.connection.current_result
        if "RETURNING id" in self.statement:
            return self.connection.claim_result
        if "SELECT id::text" in self.statement:
            return self.connection.selected_job
        return None

    def fetchall(self):
        return []


class _SqlConnection:
    def __init__(self) -> None:
        self.rowcount = 1
        self.update_result = ("running",)
        self.current_result = None
        self.claim_result = {
            "id": JOB_ID,
            "job_type": "deployment",
            "status": "assigned",
            "environment": "production",
            "parameters": {},
            "target_key": "cloud",
            "max_retries": 0,
            "agent_id": JOB_ID,
            "lease_token": LEASE_TOKEN,
            "lease_epoch": LEASE_EPOCH,
        }
        self.selected_job = {
            "id": JOB_ID,
            "job_type": "deployment",
            "status": "queued",
            "environment": "production",
            "parameters": {},
            "target_key": "cloud",
            "max_retries": 0,
        }
        self.statements: list[tuple[str, object]] = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, **_kwargs):
        return _SqlCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def test_heartbeat_distinguishes_terminal_ownership_loss_from_db_outage() -> None:
    connection = _SqlConnection()
    connection.rowcount = 0

    assert (
        deployment_worker._heartbeat(connection, _leased_job(), 10)
        is deployment_worker.JobLeaseRefresh.OWNERSHIP_LOST
    )
    assert connection.commits == 1


def test_stale_agent_queued_job_is_eligible_for_atomic_reassignment() -> None:
    connection = _SqlConnection()

    claimed = deployment_worker._claim_job(connection, _config(agent=True))

    assert claimed is not None
    selection, selection_parameters = connection.statements[0]
    assert "NOT EXISTS" in selection
    assert "owner.last_seen_at >= CURRENT_TIMESTAMP - INTERVAL '2 minutes'" in selection
    assert "deployment_queue" in selection
    assert "required_agent_hostname" in selection
    assert selection_parameters == ["production", JOB_ID, "an2p"]
    assignment, assignment_parameters = connection.statements[3]
    assert "lease_epoch = nextval('ops_container_deployment_lease_epoch_seq')" in assignment
    assert "agent_id = %s" in assignment
    assert assignment_parameters[0] == JOB_ID
    assert assignment_parameters[2:] == (300, JOB_ID)
    deployment_transition, transition_parameters = connection.statements[4]
    assert "deployment_status = 'running'" in deployment_transition
    assert transition_parameters == (JOB_ID,)


def test_container_only_worker_claim_refuses_legacy_native_queue() -> None:
    connection = _SqlConnection()

    claimed = deployment_worker._claim_job(
        connection,
        _config(agent=True, container_only=True),
    )

    assert claimed is not None
    selection = connection.statements[0][0]
    assert "parameters->>'deployment_mode' = 'container'" in selection


def test_stale_recovery_is_global_to_environment_not_old_agent() -> None:
    connection = _SqlConnection()

    assert (
        deployment_worker._recover_stale_jobs(
            connection,
            _config(agent=True),
            stale_after_seconds=300,
        )
        == 0
    )

    statement, parameters = connection.statements[0]
    assert "job_type = 'deployment'" in statement
    assert "deployment_mode', 'native') <> 'container'" in statement
    assert "agent_id = %s" not in statement
    assert parameters == ["production", 300]


def test_stale_container_claim_fences_remote_epoch_before_db_owner_transfer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class Cursor:
        statement = ""
        parameters: object = None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, statement: str, parameters=None) -> None:
            self.statement = statement
            self.parameters = parameters
            if "UPDATE ops_jobs" in statement:
                events.append("db-owner-transfer")

        def fetchone(self):
            if "SELECT id::text" in self.statement:
                return {
                    "id": JOB_ID,
                    "job_type": "deployment",
                    "status": "running",
                    "environment": "production",
                    "parameters": {"deployment_mode": "container"},
                    "target_key": "cloud",
                    "max_retries": 0,
                    "agent_id": "33333333-3333-4333-8333-333333333333",
                    "lease_token": "44444444-4444-4444-8444-444444444444",
                    "lease_epoch": 16,
                }
            if "nextval" in self.statement:
                return {"claim_epoch": 17}
            if "RETURNING id::text" in self.statement:
                assert isinstance(self.parameters, tuple)
                return {
                    "id": JOB_ID,
                    "job_type": "deployment",
                    "status": "running",
                    "environment": "production",
                    "parameters": {"deployment_mode": "container"},
                    "target_key": "cloud",
                    "max_retries": 0,
                    "agent_id": self.parameters[0],
                    "lease_token": self.parameters[1],
                    "lease_epoch": self.parameters[2],
                }
            if "SELECT deployment_status" in self.statement:
                return {"deployment_status": "running"}
            return None

    class Connection:
        def __init__(self) -> None:
            self.cursor_value = Cursor()
            self.commits = 0
            self.rollbacks = 0

        def cursor(self, **_kwargs):
            return self.cursor_value

        def commit(self) -> None:
            self.commits += 1

        def rollback(self) -> None:
            self.rollbacks += 1

    def fence(_config, **kwargs):
        assert "db-owner-transfer" not in events
        assert kwargs["lease_epoch"] == 17
        events.append("remote-exclusive-fence")
        return {}

    monkeypatch.setattr(deployment_worker, "_rotate_remote_worker_lease", fence)
    connection = Connection()
    claimed = deployment_worker._claim_stale_container_job(
        connection,
        _config(agent=True),
        stale_after_seconds=300,
    )

    assert claimed is not None
    assert claimed["lease_epoch"] == 17
    assert claimed["_remote_fence_confirmed"] is True
    assert events == ["remote-exclusive-fence", "db-owner-transfer"]
    assert connection.commits == 1
    assert connection.rollbacks == 0


def test_stale_container_claim_keeps_db_owner_when_remote_fence_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _SqlConnection()
    connection.selected_job.update(
        {
            "status": "running",
            "parameters": {"deployment_mode": "container"},
            "agent_id": JOB_ID,
            "lease_token": LEASE_TOKEN,
            "lease_epoch": LEASE_EPOCH,
        }
    )
    monkeypatch.setattr(
        deployment_worker,
        "_rotate_remote_worker_lease",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            deployment_worker.ContainerDeploymentError("offline")
        ),
    )

    assert (
        deployment_worker._claim_stale_container_job(
            connection,
            _config(agent=True),
            stale_after_seconds=300,
        )
        is None
    )
    assert connection.rollbacks == 1
    assert not any("UPDATE ops_jobs" in statement for statement, _ in connection.statements)


def test_fixed_remote_mutation_never_starts_without_fresh_exact_db_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    connection = object()
    started: list[bool] = []
    monkeypatch.setattr(
        deployment_worker,
        "_refresh_job_lease_with_reconnect",
        lambda active, *_args, **_kwargs: (
            active,
            deployment_worker.JobLeaseRefresh.UNAVAILABLE,
        ),
    )
    monkeypatch.setattr(
        deployment_worker.subprocess,
        "Popen",
        lambda *_args, **_kwargs: started.append(True),
    )

    result = deployment_worker._run_fixed_container_command(
        connection,
        _leased_job(),
        _config(agent=True),
        ["fixed-mutation"],
        runtime_directory=runtime,
        progress=50,
        action_started=0.0,
    )

    assert result == (connection, None, "", False, True)
    assert started == []


def test_recovered_previous_is_nonterminal_until_remote_owner_is_fenced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = object()
    job = _leased_job(parameters={"deployment_mode": "container"})
    evidence = SimpleNamespace()
    monkeypatch.setattr(
        deployment_worker,
        "reconcile_container_status",
        lambda *_args: "recovered_previous",
    )
    monkeypatch.setattr(
        deployment_worker,
        "_finish_job_resilient",
        lambda *_args, **_kwargs: pytest.fail(
            "unfenced pre-action state must remain nonterminal"
        ),
    )

    assert deployment_worker._finish_reconciled_container_job(
        connection,
        job,
        evidence,
        {},
        started=0.0,
        return_code=None,
        authoritative_fence=False,
    ) == (connection, False)

    monkeypatch.setattr(
        deployment_worker,
        "_finish_job_resilient",
        lambda active, *_args, **_kwargs: active,
    )
    assert deployment_worker._finish_reconciled_container_job(
        connection,
        job,
        evidence,
        {},
        started=0.0,
        return_code=None,
        authoritative_fence=True,
    ) == (connection, True)


def test_container_worker_runs_fixed_ingress_and_controller_pipeline_before_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    evidence = SimpleNamespace(
        job_id=JOB_ID,
        agent_id=JOB_ID,
        lease_token=LEASE_TOKEN,
        lease_epoch=LEASE_EPOCH,
        action="promote",
        target_name="cloud",
        target_runtime_kind="container",
        native_baseline_identity=None,
        release={},
        release_digest="d" * 64,
        source_tree=SOURCE_TREE,
        approval_id="33333333-3333-4333-8333-333333333333",
        expected_runtime_generation=1,
        expected_controller_state_sha256="e" * 64,
    )
    plan = {
        "prepare": ["ingress-prepare"],
        "uploads": [
            SimpleNamespace(command=(f"ingress-upload-{index}",), name=str(index))
            for index in range(4)
        ],
        "abort": ["ingress-abort"],
    }
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    connection = object()
    reconciled: list[dict[str, object]] = []

    monkeypatch.setattr(deployment_worker, "_mark_running", lambda *_args: True)
    monkeypatch.setattr(
        deployment_worker,
        "load_container_execution_evidence",
        lambda *_args, **_kwargs: evidence,
    )
    monkeypatch.setattr(
        deployment_worker,
        "_configured_container_development_identity",
        lambda: "8" * 64,
    )
    monkeypatch.setattr(
        deployment_worker,
        "read_container_controller_status",
        lambda **_kwargs: {"schema_version": 1},
    )
    monkeypatch.setattr(
        deployment_worker,
        "assert_container_runtime_cas",
        lambda *_args: {},
    )
    monkeypatch.setattr(deployment_worker, "container_release_files", lambda *_args: {})
    monkeypatch.setattr(
        deployment_worker,
        "build_container_ingress_commands",
        lambda *_args, **_kwargs: plan,
    )
    monkeypatch.setattr(
        deployment_worker,
        "build_container_controller_command",
        lambda _evidence, action: [action],
    )
    monkeypatch.setattr(
        deployment_worker,
        "build_container_worker_lease_command",
        lambda *_args: ["lease-bind"],
    )
    monkeypatch.setattr(
        deployment_worker,
        "_create_deployment_runtime_directory",
        lambda *_args: runtime,
    )
    monkeypatch.setattr(
        deployment_worker,
        "_remove_deployment_runtime_directory_resilient",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        deployment_worker,
        "_report_log_with_reconnect",
        lambda active, *_args, **_kwargs: active,
    )
    monkeypatch.setattr(
        deployment_worker,
        "_cleanup_container_ingress",
        lambda _command, _evidence: calls.append("cleanup"),
    )

    def run_command(active, _job_id, _config, command, **_kwargs):
        step = command[0]
        calls.append(step)
        output = "{}\n"
        return active, 0, output, True, False

    monkeypatch.setattr(deployment_worker, "_run_fixed_container_command", run_command)
    monkeypatch.setattr(
        deployment_worker,
        "parse_container_worker_lease_result",
        lambda *_args, **_kwargs: calls.append("parse:lease-bind") or {},
    )
    monkeypatch.setattr(
        deployment_worker,
        "assert_container_worker_lease",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        deployment_worker,
        "parse_container_ingress_result",
        lambda _line, _evidence, action, **_kwargs: calls.append(f"parse:{action}"),
    )
    monkeypatch.setattr(
        deployment_worker,
        "parse_container_pipeline_step_result",
        lambda _line, _evidence, action: calls.append(f"parse:{action}"),
    )
    monkeypatch.setattr(
        deployment_worker,
        "parse_container_action_result",
        lambda *_args: calls.append("parse:promote") or {},
    )

    def reconcile(active, _job, _evidence, **kwargs):
        reconciled.append(kwargs)
        return active, True

    monkeypatch.setattr(deployment_worker, "_reconcile_container_job", reconcile)
    monkeypatch.setattr(
        deployment_worker,
        "_finish_job_resilient",
        lambda *_args, **_kwargs: pytest.fail("status reconciliation must own finalization"),
    )

    returned = deployment_worker._execute_container_job(
        connection,
        _leased_job(
            parameters={"deployment_mode": "container", "action": "promote"}
        ),
        _config(agent=True),
    )

    assert returned is connection
    assert calls[:13] == [
        "lease-bind",
        "parse:lease-bind",
        "cleanup",
        "ingress-prepare",
        "parse:prepare",
        "ingress-upload-0",
        "parse:upload",
        "ingress-upload-1",
        "parse:upload",
        "ingress-upload-2",
        "parse:upload",
        "ingress-upload-3",
        "parse:upload",
    ]
    for action in ("stage", "load-images", "preflight", "promote"):
        assert calls.index(action) < calls.index(f"parse:{action}")
    assert reconciled and reconciled[0]["wait_for_guard"] is True


def test_container_worker_reconciles_instead_of_terminalizing_after_interruption(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    evidence = SimpleNamespace(
        job_id=JOB_ID,
        agent_id=JOB_ID,
        lease_token=LEASE_TOKEN,
        lease_epoch=LEASE_EPOCH,
        action="rollback",
        target_name="cloud",
        target_runtime_kind="container",
        native_baseline_identity=None,
        release={},
        release_digest="d" * 64,
        source_tree=SOURCE_TREE,
        approval_id="33333333-3333-4333-8333-333333333333",
        expected_runtime_generation=1,
        expected_controller_state_sha256="e" * 64,
    )
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    reconciled: list[bool] = []
    monkeypatch.setattr(deployment_worker, "_mark_running", lambda *_args: True)
    monkeypatch.setattr(
        deployment_worker,
        "load_container_execution_evidence",
        lambda *_args, **_kwargs: evidence,
    )
    monkeypatch.setattr(
        deployment_worker,
        "_configured_container_development_identity",
        lambda: "8" * 64,
    )
    monkeypatch.setattr(
        deployment_worker,
        "read_container_controller_status",
        lambda **_kwargs: {"schema_version": 1},
    )
    monkeypatch.setattr(
        deployment_worker,
        "assert_container_runtime_cas",
        lambda *_args: {},
    )
    monkeypatch.setattr(
        deployment_worker,
        "_report_log_with_reconnect",
        lambda active, *_args, **_kwargs: active,
    )
    monkeypatch.setattr(
        deployment_worker,
        "build_container_controller_command",
        lambda *_args: ["rollback"],
    )
    monkeypatch.setattr(
        deployment_worker,
        "build_container_worker_lease_command",
        lambda *_args: ["lease-bind"],
    )
    monkeypatch.setattr(
        deployment_worker,
        "_create_deployment_runtime_directory",
        lambda *_args: runtime,
    )
    monkeypatch.setattr(
        deployment_worker,
        "_remove_deployment_runtime_directory_resilient",
        lambda *_args, **_kwargs: True,
    )
    command_calls = 0

    def run_interrupted(active, *_args, **_kwargs):
        nonlocal command_calls
        command_calls += 1
        if command_calls == 1:
            return active, 0, "{}\n", True, False
        return active, -15, "", True, True

    monkeypatch.setattr(deployment_worker, "_run_fixed_container_command", run_interrupted)
    monkeypatch.setattr(
        deployment_worker,
        "parse_container_worker_lease_result",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        deployment_worker,
        "assert_container_worker_lease",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        deployment_worker,
        "_fence_remote_worker_lease",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        deployment_worker,
        "_reconcile_container_job",
        lambda active, *_args, **_kwargs: (reconciled.append(True) or active, False),
    )
    monkeypatch.setattr(
        deployment_worker,
        "_finish_job_resilient",
        lambda *_args, **_kwargs: pytest.fail("interrupted remote work must remain reconciling"),
    )

    connection = object()
    assert deployment_worker._execute_container_job(
        connection,
        _leased_job(
            parameters={"deployment_mode": "container", "action": "rollback"}
        ),
        _config(agent=True),
    ) is connection
    assert reconciled == [True]


def test_cancelled_job_rejects_late_worker_success(monkeypatch) -> None:
    connection = _SqlConnection()
    connection.update_result = None
    connection.current_result = ("cancelled", "cancelled", {"return_code": None})
    logs: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        deployment_worker,
        "_append_log",
        lambda *args, **kwargs: logs.append((args, kwargs)) or True,
    )

    deployment_worker._finish_job(
        connection,
        _leased_job(parameters={"target": "cloud"}),
        final_status="success",
        return_code=0,
        duration_seconds=3,
    )

    statements = "\n".join(statement for statement, _ in connection.statements)
    assert "status IN ('assigned', 'running')" in statements
    assert "agent_id = %s::uuid" in statements
    assert "lease_token = %s::uuid" in statements
    assert "lease_epoch = %s" in statements
    assert "leased_until > CURRENT_TIMESTAMP" in statements
    assert "UPDATE ops_deployments" not in statements
    assert logs and logs[0][0][3].startswith("A stale worker result was ignored")


def test_timeout_wins_over_conflicting_spooled_success(tmp_path: Path, monkeypatch) -> None:
    job = _leased_job(parameters={"target": "cloud"})
    assert deployment_worker._spool_final_status(
        job,
        final_status="success",
        return_code=0,
        duration_seconds=4,
        root=tmp_path,
    )
    connection = _SqlConnection()
    connection.update_result = None
    connection.current_result = (
        "timed_out",
        "worker_heartbeat_expired",
        {"return_code": None},
    )
    monkeypatch.setattr(deployment_worker, "_append_log", lambda *_args, **_kwargs: True)

    replayed = deployment_worker._replay_pending_final_statuses(
        connection,
        root=tmp_path,
    )

    assert replayed == 1
    assert not list(
        (tmp_path / deployment_worker.DEPLOYMENT_PENDING_FINAL_DIRECTORY).glob("*.json")
    )
    conflicts = list(
        (tmp_path / deployment_worker.DEPLOYMENT_PENDING_FINAL_DIRECTORY).glob("*.conflict")
    )
    assert len(conflicts) == 1
    assert not any("UPDATE ops_deployments" in item[0] for item in connection.statements)


def test_conflicting_final_evidence_is_bounded_non_replayed_and_never_clobbered(
    tmp_path: Path,
    monkeypatch,
) -> None:
    job = _leased_job(parameters={"target": "cloud"})
    connection = _SqlConnection()
    connection.update_result = None
    connection.current_result = (
        "timed_out",
        "worker_heartbeat_expired",
        {"return_code": None},
    )
    monkeypatch.setattr(deployment_worker, "_append_log", lambda *_args, **_kwargs: False)
    assert deployment_worker._spool_final_status(
        job,
        final_status="success",
        return_code=0,
        duration_seconds=4,
        root=tmp_path,
    )

    assert deployment_worker._replay_pending_final_statuses(connection, root=tmp_path) == 1
    directory = tmp_path / deployment_worker.DEPLOYMENT_PENDING_FINAL_DIRECTORY
    original = directory / f"{JOB_ID}.conflict"
    original_bytes = original.read_bytes()
    assert deployment_worker._replay_pending_final_statuses(connection, root=tmp_path) == 0

    assert deployment_worker._spool_final_status(
        job,
        final_status="success",
        return_code=0,
        duration_seconds=5,
        root=tmp_path,
    )
    assert deployment_worker._replay_pending_final_statuses(connection, root=tmp_path) == 1

    assert original.read_bytes() == original_bytes
    assert len(list(directory.glob("*.conflict"))) == 2
    assert not list(directory.glob("*.json"))


def test_direct_conflict_persists_evidence_when_warning_reporting_also_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    job = _leased_job(parameters={"target": "cloud"})
    connection = _SqlConnection()
    connection.update_result = None
    connection.current_result = (
        "timed_out",
        "worker_heartbeat_expired",
        {"return_code": None},
    )
    monkeypatch.setattr(deployment_worker, "_append_log", lambda *_args, **_kwargs: False)
    persist_conflict = deployment_worker._spool_final_conflict_evidence
    monkeypatch.setattr(
        deployment_worker,
        "_spool_final_conflict_evidence",
        lambda *args, **kwargs: persist_conflict(*args, **kwargs, root=tmp_path),
    )

    returned = deployment_worker._finish_job_resilient(
        connection,
        job,
        final_status="success",
        return_code=0,
        duration_seconds=3,
    )

    directory = tmp_path / deployment_worker.DEPLOYMENT_PENDING_FINAL_DIRECTORY
    assert returned is connection
    assert len(list(directory.glob("*.conflict"))) == 1
    assert not list(directory.glob("*.json"))
    assert deployment_worker._replay_pending_final_statuses(connection, root=tmp_path) == 0


def test_idempotent_direct_finalization_does_not_create_conflict_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    connection = _SqlConnection()
    connection.update_result = None
    connection.current_result = ("success", None, {"return_code": 0})
    persist_conflict = deployment_worker._spool_final_conflict_evidence
    persisted: list[bool] = []
    monkeypatch.setattr(
        deployment_worker,
        "_spool_final_conflict_evidence",
        lambda *args, **kwargs: persisted.append(True)
        or persist_conflict(*args, **kwargs, root=tmp_path),
    )

    deployment_worker._finish_job_resilient(
        connection,
        _leased_job(parameters={"target": "cloud"}),
        final_status="success",
        return_code=0,
        duration_seconds=3,
    )

    assert persisted == []


def test_later_successful_replay_does_not_mask_earlier_invalid_pending_record(
    tmp_path: Path,
    monkeypatch,
) -> None:
    directory = (
        tmp_path / deployment_worker.DEPLOYMENT_PENDING_FINAL_DIRECTORY
    )
    directory.mkdir(parents=True, mode=0o700)
    invalid = directory / "22222222-2222-4222-8222-222222222222.json"
    invalid.write_text("{invalid", encoding="utf-8")
    valid_job = _leased_job(
        job_id="33333333-3333-4333-8333-333333333333",
        parameters={"target": "cloud"},
    )
    assert deployment_worker._spool_final_status(
        valid_job,
        final_status="success",
        return_code=0,
        duration_seconds=1,
        root=tmp_path,
    )
    connection = _SqlConnection()
    monkeypatch.setattr(deployment_worker, "_append_log", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(deployment_worker, "_FINAL_STATUS_SPOOL_ERROR_AT", None)

    assert deployment_worker._replay_pending_final_statuses(connection, root=tmp_path) == 1
    assert invalid.exists()
    assert deployment_worker._FINAL_STATUS_SPOOL_ERROR_AT is not None

    invalid.unlink()
    assert deployment_worker._replay_pending_final_statuses(connection, root=tmp_path) == 0
    assert deployment_worker._FINAL_STATUS_SPOOL_ERROR_AT is None


@pytest.mark.parametrize(
    ("return_code", "final_status", "error_code"),
    [
        (65, "blocked", "unsafe_remote_state"),
        (73, "blocked", "lock_busy"),
        (75, "blocked", "recovery_required"),
        (1, "failed", "deployment_process_failed"),
    ],
)
def test_remote_exit_codes_are_classified(
    return_code: int,
    final_status: str,
    error_code: str,
) -> None:
    status, code, detail = deployment_worker._deployment_failure_result(
        return_code,
        "token=very-secret diagnostic",
    )
    assert status == final_status
    assert code == error_code
    assert "very-secret" not in detail


def test_preparation_ownership_loss_never_spawns_the_remote_process(monkeypatch, tmp_path: Path) -> None:
    finished, released = _install_reviewed_execution_harness(monkeypatch, tmp_path)
    lease_refreshes = iter(
        (
            deployment_worker.JobLeaseRefresh.REFRESHED,
            deployment_worker.JobLeaseRefresh.OWNERSHIP_LOST,
        )
    )
    monkeypatch.setattr(
        deployment_worker,
        "_heartbeat",
        lambda *_args: next(lease_refreshes),
    )
    monkeypatch.setattr(
        deployment_worker.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("a fenced preparation must never deploy"),
    )

    deployment_worker.execute_job(
        object(),
        _leased_job(parameters={"target": "cloud"}),
        _config(stale_after_seconds=60),
    )

    assert finished[0]["final_status"] == "timed_out"
    assert finished[0]["error_code"] == "deployment_ownership_lost"
    assert released == [(f"refs/mooncen/deploy-snapshots/{JOB_ID}", SOURCE_COMMIT)]


def test_active_remote_process_is_terminated_on_definitive_ownership_loss(
    monkeypatch,
    tmp_path: Path,
) -> None:
    finished, released = _install_reviewed_execution_harness(monkeypatch, tmp_path)
    lease_refreshes = iter(
        (
            deployment_worker.JobLeaseRefresh.REFRESHED,
            deployment_worker.JobLeaseRefresh.REFRESHED,
            deployment_worker.JobLeaseRefresh.REFRESHED,
            deployment_worker.JobLeaseRefresh.OWNERSHIP_LOST,
        )
    )
    monkeypatch.setattr(
        deployment_worker,
        "_heartbeat",
        lambda *_args: next(lease_refreshes),
    )

    class ActiveProcess:
        stdout = iter(())
        returncode = None

        def poll(self):
            return self.returncode

    process = ActiveProcess()
    terminated: list[bool] = []
    monkeypatch.setattr(
        deployment_worker.subprocess,
        "Popen",
        lambda *_args, **_kwargs: process,
    )

    def terminate() -> None:
        terminated.append(True)
        process.returncode = -15

    monkeypatch.setattr(deployment_worker, "_terminate_active_process", terminate)

    deployment_worker.execute_job(
        object(),
        _leased_job(parameters={"target": "cloud"}),
        _config(stale_after_seconds=60),
    )

    assert terminated
    assert finished[0]["final_status"] == "timed_out"
    assert finished[0]["error_code"] == "deployment_ownership_lost"
    assert finished[0]["return_code"] == -15
    assert released == [(f"refs/mooncen/deploy-snapshots/{JOB_ID}", SOURCE_COMMIT)]


def test_active_remote_process_stops_before_prolonged_db_outage_releases_slot(
    monkeypatch,
    tmp_path: Path,
) -> None:
    finished, _released = _install_reviewed_execution_harness(monkeypatch, tmp_path)
    clock = {"now": 0.0}
    monkeypatch.setattr(deployment_worker.time, "monotonic", lambda: clock["now"])
    lease_refreshes = iter(
        (
            deployment_worker.JobLeaseRefresh.REFRESHED,
            deployment_worker.JobLeaseRefresh.UNAVAILABLE,
            deployment_worker.JobLeaseRefresh.UNAVAILABLE,
        )
    )
    monkeypatch.setattr(
        deployment_worker,
        "_heartbeat",
        lambda *_args: next(lease_refreshes),
    )

    class ActiveProcess:
        stdout = iter(())
        returncode = None

        def poll(self):
            return self.returncode

    process = ActiveProcess()

    def start_process(*_args, **_kwargs):
        clock["now"] = 31.0
        return process

    monkeypatch.setattr(deployment_worker.subprocess, "Popen", start_process)

    def terminate() -> None:
        process.returncode = -15

    monkeypatch.setattr(deployment_worker, "_terminate_active_process", terminate)

    deployment_worker.execute_job(
        object(),
        _leased_job(parameters={"target": "cloud"}),
        _config(stale_after_seconds=60),
    )

    assert process.returncode == -15
    assert finished[0]["final_status"] == "timed_out"
    assert finished[0]["error_code"] == "deployment_lease_expired"


def test_remote_lock_exit_finishes_job_as_blocked(monkeypatch, tmp_path: Path) -> None:
    finished: list[dict[str, object]] = []

    class CompletedProcess:
        stdout = iter(
            (
                "another deployment holds /opt/.mooncen-deploy.lock\n",
                "MOONCEN_DEPLOY_FAILURE error_code=lock_busy remote_exit=73\n",
            )
        )
        returncode = 73

        def poll(self):
            return self.returncode

    connection = object()
    reviewed = {
        "target": "cloud",
        "target_commit": BASE_COMMIT,
        "target_identity": "4" * 64,
        "skip_workers": False,
        "source_tree": SOURCE_TREE,
    }
    monkeypatch.setattr(deployment_worker, "_mark_running", lambda *_args: True)
    monkeypatch.setattr(
        deployment_worker,
        "_publish_worker_heartbeat_resilient",
        lambda: None,
    )
    monkeypatch.setattr(
        deployment_worker,
        "_touch_deployment_agent_resilient",
        lambda active, _config: active,
    )
    monkeypatch.setattr(
        deployment_worker,
        "_report_log_with_reconnect",
        lambda active, *_args, **_kwargs: active,
    )
    monkeypatch.setattr(
        deployment_worker,
        "_heartbeat",
        lambda *_args: deployment_worker.JobLeaseRefresh.REFRESHED,
    )
    monkeypatch.setattr(deployment_worker, "_cancellation_requested", lambda *_args: False)
    monkeypatch.setattr(
        deployment_worker,
        "_assert_native_deployment_not_mixed_with_container",
        lambda *_args: True,
    )
    monkeypatch.setattr(deployment_worker, "deployment_readiness", lambda: {"available": True})
    monkeypatch.setattr(
        deployment_worker,
        "validated_parameters",
        lambda *_args, **_kwargs: reviewed,
    )
    monkeypatch.setattr(
        deployment_worker,
        "create_deployment_snapshot_commit",
        lambda **_kwargs: SOURCE_COMMIT,
    )
    monkeypatch.setattr(
        deployment_worker,
        "preserve_deployment_release_reference",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(deployment_worker, "_build_deployment_command", lambda *_args, **_kwargs: ["deploy"])
    monkeypatch.setattr(deployment_worker, "_record_source_snapshot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        deployment_worker.subprocess,
        "Popen",
        lambda *_args, **_kwargs: CompletedProcess(),
    )
    monkeypatch.setattr(
        deployment_worker,
        "_finish_job",
        lambda *_args, **kwargs: finished.append(kwargs) or "written",
    )
    monkeypatch.setattr(
        deployment_worker,
        "release_deployment_snapshot_reference",
        lambda *_args: None,
    )
    monkeypatch.setattr(deployment_worker, "ACTIVE_PROCESS", None)
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    monkeypatch.setattr(
        deployment_worker,
        "_create_deployment_runtime_directory",
        lambda *_args, **_kwargs: runtime,
    )
    monkeypatch.setattr(
        deployment_worker,
        "_remove_deployment_runtime_directory_resilient",
        lambda *_args, **_kwargs: True,
    )

    deployment_worker.execute_job(
        connection,
        _leased_job(parameters={"target": "cloud"}),
        _config(),
    )

    assert finished[0]["final_status"] == "blocked"
    assert finished[0]["error_code"] == "lock_busy"
    assert finished[0]["return_code"] == 73
    assert "remote_exit=73" in str(finished[0]["detail"])


def test_completed_job_replays_pending_final_before_another_claim(monkeypatch) -> None:
    events: list[str] = []

    class Connection:
        def close(self) -> None:
            events.append("close")

    connection = Connection()
    monotonic = iter((0.0, 1.0, 2.0))
    monkeypatch.setattr(
        deployment_worker,
        "parse_args",
        lambda _argv=None: SimpleNamespace(
            once=True,
            agent_id=UUID(JOB_ID),
            poll_interval=0.5,
        ),
    )
    monkeypatch.setattr(deployment_worker, "normalized_environment", lambda: "production")
    monkeypatch.setattr(
        deployment_worker,
        "container_worker_service_boundary_ready",
        lambda: True,
    )
    monkeypatch.setattr(deployment_worker, "deployment_heartbeat_lease_seconds", lambda: 180)
    monkeypatch.setattr(deployment_worker, "connect_queue", lambda: connection)
    monkeypatch.setattr(deployment_worker.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(deployment_worker.time, "monotonic", lambda: next(monotonic))
    monkeypatch.setattr(deployment_worker, "_touch_deployment_agent", lambda *_args: None)
    monkeypatch.setattr(deployment_worker, "_publish_worker_heartbeat", lambda: None)
    monkeypatch.setattr(deployment_worker, "_clear_worker_heartbeat", lambda: None)
    monkeypatch.setattr(
        deployment_worker,
        "_replay_pending_final_statuses",
        lambda *_args: events.append("replay") or 0,
    )
    monkeypatch.setattr(
        deployment_worker,
        "_recover_stale_jobs",
        lambda *_args, **kwargs: events.append(
            f"recover:{kwargs['stale_after_seconds']}"
        )
        or 0,
    )
    monkeypatch.setattr(
        deployment_worker,
        "_reconcile_stale_container_job",
        lambda active, _config, **_kwargs: (active, False),
    )
    monkeypatch.setattr(
        deployment_worker,
        "_claim_job",
        lambda *_args: events.append("claim") or _leased_job(),
    )
    monkeypatch.setattr(
        deployment_worker,
        "execute_job",
        lambda active, _job, config: events.append(
            f"execute:{config.stale_after_seconds}"
        )
        or active,
    )
    monkeypatch.setattr(deployment_worker, "RUNNING", True)

    assert deployment_worker.main([]) == 0
    assert events[:4] == [
        "replay",
        "claim",
        "execute:180",
        "replay",
    ]


def test_container_worker_refuses_start_outside_isolated_service_boundary(monkeypatch) -> None:
    monkeypatch.setattr(
        deployment_worker,
        "parse_args",
        lambda _argv=None: SimpleNamespace(once=True, agent_id=None, poll_interval=0.5),
    )
    monkeypatch.setattr(
        deployment_worker,
        "container_worker_service_boundary_ready",
        lambda: False,
    )
    monkeypatch.setattr(
        deployment_worker,
        "connect_queue",
        lambda: pytest.fail("an unisolated worker must not receive queue credentials"),
    )

    assert deployment_worker.main([]) == 1


def test_powershell_preserves_guard_exit_codes_for_worker() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (
        root
        / "deploy"
        / "ubuntu"
        / "deploy_from_windows.ps1"
    ).read_text(encoding="utf-8")

    assert '65 { return "unsafe_remote_state" }' in source
    assert '73 { return "lock_busy" }' in source
    assert '75 { return "recovery_required" }' in source
    assert "$remoteScriptExitCode = $LASTEXITCODE" in source
    assert "$script:DeploymentRemoteExitCode = $remoteScriptExitCode" in source
    assert "MOONCEN_DEPLOY_FAILURE error_code=$errorCode remote_exit=$ExitCode" in source
    assert "exit $deploymentFailureExitCode" in source
    for function_name, next_function in (
        ("Invoke-Remote", "Invoke-RemoteWithInput"),
        ("Invoke-RemoteWithInput", "Invoke-RemoteTty"),
        ("Invoke-RemoteTty", "Invoke-RemoteBashScriptTty"),
    ):
        function_block = source.split(f"function {function_name} {{", 1)[1].split(
            f"function {next_function} {{",
            1,
        )[0]
        assert "$remoteExitCode = $LASTEXITCODE" in function_block
        assert "$script:DeploymentRemoteExitCode = $remoteExitCode" in function_block
    crawler_drain = source.split(
        "if ($EnableCrawler -and -not $AllowCrawlerInterruption) {",
        1,
    )[1].split("$remoteDbPassword =", 1)[0]
    assert "try {\n        Invoke-RemoteBashScriptTty $crawlerDrainCheckScript" in crawler_drain
    assert "$crawlerDrainFailureExitCode = $script:DeploymentRemoteExitCode" in crawler_drain
    assert "Write-DeploymentFailureMarker $crawlerDrainFailureExitCode" in crawler_drain
    assert "exit $crawlerDrainFailureExitCode" in crawler_drain
    recovery_catch = source.split(
        '$deploymentFailureExitCode = $script:DeploymentRemoteExitCode\n    if ($remoteGuardArmed)',
        1,
    )[1].split("} finally {", 1)[0]
    assert "$recoveryFailureExitCode = $script:DeploymentRemoteExitCode" in recovery_catch
    assert "$deploymentFailureExitCode = $recoveryFailureExitCode" in recovery_catch
    unlock_catch = source.rsplit("Invoke-RemoteBashScriptTty $unlockScript", 1)[1].split(
        "Write-Warning",
        1,
    )[0]
    assert "$unlockFailureExitCode = $script:DeploymentRemoteExitCode" in unlock_catch
    assert "$unlockFailureExitCode -in @(65, 75)" in unlock_catch
    assert "$deploymentFailureExitCode = $unlockFailureExitCode" in unlock_catch
    assert "$deploymentFailure = $unlockFailure" in unlock_catch
    setup_failure = source.split("$remoteSetupFailureExitCode = $LASTEXITCODE", 1)[1].split(
        "} finally {",
        1,
    )[0]
    assert "Get-DeploymentRemoteErrorCode $remoteSetupFailureExitCode" in setup_failure
    assert "$script:DeploymentRemoteExitCode = $remoteSetupFailureExitCode" in setup_failure
    assert source.rstrip().endswith("exit 0")
    wrapper = (root / "deploy_ubuntu.ps1").read_text(encoding="utf-8")
    assert "$deploymentExitCode = $LASTEXITCODE" in wrapper
    assert wrapper.rstrip().endswith("exit $deploymentExitCode")
    orchestrator = (root / "deploy_mooncen.ps1").read_text(encoding="utf-8")
    for action in ('"deploy" {', '"full-deploy" {'):
        action_block = orchestrator.split(action, 1)[1].split("\n    }", 1)[0]
        assert "& $script" in action_block
        assert "exit $LASTEXITCODE" in action_block
