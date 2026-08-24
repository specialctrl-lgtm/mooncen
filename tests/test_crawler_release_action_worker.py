from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ops_agent import crawler_release_action_worker as worker


REQUEST_ID = "11111111-1111-4111-8111-111111111111"
ROLLOUT_ID = "22222222-2222-4222-8222-222222222222"
LEASE_TOKEN = "33333333-3333-4333-8333-333333333333"
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


class FakeCursor:
    def __init__(self, connection: "FakeConnection") -> None:
        self.connection = connection
        self.rows: list[Any] = []
        self.rowcount = 0

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, statement: str, parameters: Any = None) -> None:
        normalized = " ".join(statement.split())
        self.connection.executions.append((normalized, parameters))
        rows, rowcount = self.connection.handler(normalized, parameters)
        self.rows = list(rows)
        self.rowcount = rowcount

    def fetchone(self) -> Any:
        return self.rows.pop(0) if self.rows else None

    def fetchall(self) -> list[Any]:
        rows, self.rows = self.rows, []
        return rows


class FakeConnection:
    def __init__(self, handler) -> None:
        self.handler = handler
        self.executions: list[tuple[str, Any]] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def set_session(self, **_kwargs: Any) -> None:
        return None

    def cursor(self, **_kwargs: Any) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


def _config() -> worker.WorkerConfig:
    return worker.WorkerConfig(
        environment="production",
        owner="release-action@gen1db:123",
        public_root=Path("/var/lib/mooncen-crawler-control/public"),
        max_attempts=5,
        lease_seconds=120,
        heartbeat_seconds=30,
        poll_seconds=5,
    )


def _lease(action: str, payload: dict[str, Any], generation: int = 7) -> worker.ActionLease:
    if action == "build":
        confirmation = f"BUILD {str(payload['source_tree'])[:12]}"
    elif action == "register_artifact":
        confirmation = f"REGISTER {str(payload['artifact_digest'])[:12]}"
    elif action == "create_canary":
        confirmation = (
            f"CANARY {payload['rollout_id']} {generation} "
            f"{str(payload['artifact_digest'])[:12]} "
            f"{str(payload['baseline_digest'])[:12]} "
            f"{worker._worker_set_digest(payload['worker_keys'])}"
        )
    elif action == "advance_rollout":
        targets = payload.get("target_worker_keys", [])
        confirmation = (
            f"ADVANCE {payload['rollout_id']} {generation} {payload['rollout_phase']} "
            f"{worker._worker_set_digest(targets) if targets else 'none'}"
        )
    elif action == "pause_rollout":
        confirmation = f"PAUSE {payload['rollout_id']} {generation}"
    elif action == "rollback_rollout":
        confirmation = f"ROLLBACK {payload['rollout_id']} {generation}"
    else:
        confirmation = f"COMPLETE_ROLLBACK {payload['rollout_id']} {generation}"
    return worker.ActionLease(
        request_id=REQUEST_ID,
        action=action,
        environment="production",
        expected_generation=generation,
        payload=payload,
        confirmation=confirmation,
        idempotency_key="release:action:0001",
        requested_by="88888888-8888-4888-8888-888888888888",
        requester_login="mooncen_crawler_api_production",
        requester_role="admin",
        reason="reviewed crawler release action",
        attempt_count=1,
        reconcile_only=False,
        lease_owner="release-action@gen1db:123",
        lease_token=LEASE_TOKEN,
    )


def test_check_runtime_validates_contract_without_emitting_heartbeat(monkeypatch) -> None:
    connection = FakeConnection(lambda _statement, _parameters: ([[True] * 13], 1))
    if hasattr(worker.os, "geteuid"):
        monkeypatch.setattr(worker.os, "geteuid", lambda: 0)
    monkeypatch.setattr(worker.release_admin, "_secure_directory", lambda *_a, **_k: None)

    worker.check_runtime(_config(), lambda: connection)

    assert connection.closed is True
    assert len(connection.executions) == 1
    assert "crawler_release_approval_contract_is_valid" in connection.executions[0][0]
    assert "SELECT heartbeat_crawler_release_action_consumer" not in connection.executions[0][0]


def test_main_run_entry_checks_contract_then_enters_worker(monkeypatch) -> None:
    runtime_checked = False
    worker_started = False

    def check_runtime(*_args: Any, **_kwargs: Any) -> None:
        nonlocal runtime_checked
        runtime_checked = True

    def run_worker(*_args: Any, **_kwargs: Any) -> int:
        nonlocal worker_started
        worker_started = True
        return 0

    monkeypatch.setattr(worker, "load_config", _config)
    monkeypatch.setattr(worker, "check_runtime", check_runtime)
    monkeypatch.setattr(worker, "run_worker", run_worker)

    assert worker.main([]) == 0
    assert runtime_checked is True
    assert worker_started is True


def test_claim_race_uses_skip_locked_and_only_one_consumer_wins(monkeypatch) -> None:
    state = {"queued": True}
    statements: list[str] = []

    def handler(statement: str, _parameters: Any):
        statements.append(statement)
        if "WITH candidate AS" in statement:
            if not state["queued"]:
                return [], 0
            state["queued"] = False
            return [
                {
                    "id": REQUEST_ID,
                    "action": "pause_rollout",
                    "environment": "production",
                    "expected_generation": 7,
                    "request_payload": {"rollout_id": ROLLOUT_ID},
                    "confirmation": f"PAUSE {ROLLOUT_ID} 7",
                    "idempotency_key": "release:pause:0001",
                    "requested_by": "88888888-8888-4888-8888-888888888888",
                    "requester_login": "mooncen_crawler_api_production",
                    "requester_role": "admin",
                    "reason": "reviewed pause request",
                    "attempt_count": 1,
                        "reconcile_only": False,
                        "approval_receipt_id": "55555555-5555-4555-8555-555555555555",
                        "approval_request_digest": DIGEST_A,
                        "approval_expires_at": "2099-01-01T00:00:00Z",
                }
            ], 1
        raise AssertionError(statement)

    tokens = iter([LEASE_TOKEN, "44444444-4444-4444-8444-444444444444"])
    monkeypatch.setattr(worker, "uuid4", lambda: tokens.__next__())
    first = worker.claim_next(FakeConnection(handler), _config())
    second = worker.claim_next(FakeConnection(handler), _config())

    assert first is not None and first.lease_token == LEASE_TOKEN
    assert first.requester_role == "admin"
    assert first.requester_login == "mooncen_crawler_api_production"
    assert first.idempotency_key == "release:pause:0001"
    assert first.reason == "reviewed pause request"
    assert second is None
    assert all("FOR UPDATE OF request SKIP LOCKED" in statement for statement in statements)
    assert all("ops_crawler_api_bindings" not in statement for statement in statements)
    assert all("CASE WHEN request.reconcile_only THEN 0 ELSE 1 END" in statement for statement in statements)


def test_claimed_row_revalidates_requester_and_idempotency_contract() -> None:
    row = {
        "id": REQUEST_ID,
        "action": "pause_rollout",
        "environment": "production",
        "expected_generation": 7,
        "request_payload": {"rollout_id": ROLLOUT_ID},
        "confirmation": f"PAUSE {ROLLOUT_ID} 7",
        "idempotency_key": "release:pause:0001",
        "requested_by": "88888888-8888-4888-8888-888888888888",
        "requester_login": "mooncen_crawler_api_production",
        "requester_role": "viewer",
        "reason": "reviewed pause request",
        "attempt_count": 1,
        "reconcile_only": False,
        "approval_receipt_id": "55555555-5555-4555-8555-555555555555",
        "approval_request_digest": DIGEST_A,
        "approval_expires_at": "2099-01-01T00:00:00Z",
    }
    with pytest.raises(worker.ReleaseActionWorkerError, match="invalid identity"):
        worker._lease_from_row(row, _config(), LEASE_TOKEN)


def test_renewal_detects_attempt_or_token_lease_loss() -> None:
    def handler(statement: str, _parameters: Any):
        assert "attempt_count = %s" in statement
        assert "lease_token = %s::uuid" in statement
        assert "leased_until > clock_timestamp()" in statement
        return [], 0

    assert worker.renew_lease(
        FakeConnection(handler),
        _lease("pause_rollout", {"rollout_id": ROLLOUT_ID}),
        120,
    ) is False


def test_heartbeat_marks_lease_lost(monkeypatch) -> None:
    monkeypatch.setattr(worker, "renew_lease", lambda *_args, **_kwargs: False)
    connection = FakeConnection(lambda *_args: ([], 0))
    heartbeat = worker.LeaseHeartbeat(
        lambda: connection,
        _lease("pause_rollout", {"rollout_id": ROLLOUT_ID}),
        lease_seconds=1,
        interval_seconds=0.01,
    )
    with heartbeat:
        time.sleep(0.04)
        with pytest.raises(worker.LeaseLostError):
            heartbeat.ensure_owned()
    assert connection.closed is True


def test_success_completion_is_token_fenced_and_idempotent() -> None:
    state: dict[str, Any] = {"status": "leased", "result": None}

    def handler(statement: str, parameters: Any):
        if statement.startswith("UPDATE ops_crawler_release_action_requests"):
            assert "attempt_count = %s" in statement
            assert "lease_token = %s::uuid" in statement
            if state["status"] == "leased":
                state["status"] = "succeeded"
                state["result"] = dict(parameters[0].adapted)
                return [], 1
            return [], 0
        if statement.startswith("SELECT status, result"):
            return [(state["status"], state["result"], None, None)], 1
        raise AssertionError(statement)

    connection = FakeConnection(handler)
    lease = _lease("pause_rollout", {"rollout_id": ROLLOUT_ID})
    result = {"status": "ADVANCED", "generation": 8}

    assert worker.complete_success(connection, lease, result).state == "completed"
    assert worker.complete_success(connection, lease, result).state == "already_completed"


def test_stale_reaper_requeues_final_mutation_for_read_only_reconciliation() -> None:
    def handler(statement: str, parameters: Any):
        assert "FOR UPDATE SKIP LOCKED" in statement
        assert "leased_until <= clock_timestamp()" in statement
        assert parameters == ("production", 50, 5)
        assert "WHEN request.reconcile_only THEN 'reconciliation_required'" in statement
        assert "WHEN request.attempt_count >= %s THEN TRUE" in statement
        return [("queued",), ("reconciliation_required",), ("queued",)], 3

    connection = FakeConnection(handler)
    assert worker.reap_expired(
        connection,
        environment="production",
        max_attempts=5,
    ) == (2, 1)
    assert connection.commits == 1


def test_final_ambiguous_attempt_defers_exactly_one_read_only_reconciliation() -> None:
    lease = replace(
        _lease("pause_rollout", {"rollout_id": ROLLOUT_ID}),
        attempt_count=5,
    )

    def handler(statement: str, parameters: Any):
        assert "SET status = 'queued', reconcile_only = TRUE" in statement
        assert "reconcile_only IS FALSE" in statement
        assert parameters == (REQUEST_ID, 5, 5, lease.lease_owner, LEASE_TOKEN)
        return [], 1

    assert worker.defer_reconciliation_owned(
        FakeConnection(handler), lease, max_attempts=5
    ).state == "completed"

    migration = (
        Path(__file__).resolve().parents[1]
        / "DB"
        / "crawler_control_migrations"
        / "20260812_002_release_action_requests.sql"
    ).read_text(encoding="utf-8")
    assert "NEW.reconcile_only IS TRUE AND OLD.attempt_count >= 5" in migration


def test_unavailable_commit_probe_retries_before_budget_is_exhausted(monkeypatch) -> None:
    lease = _lease("pause_rollout", {"rollout_id": ROLLOUT_ID})
    connections: list[FakeConnection] = []

    def connection_factory() -> FakeConnection:
        connection = FakeConnection(lambda *_args: ([], 0))
        connections.append(connection)
        return connection

    class Heartbeat:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def __enter__(self) -> "Heartbeat":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def ensure_owned(self) -> None:
            return None

    monkeypatch.setattr(worker, "reap_expired", lambda *_args, **_kwargs: (0, 0))
    monkeypatch.setattr(worker, "claim_next", lambda *_args, **_kwargs: lease)
    monkeypatch.setattr(worker, "LeaseHeartbeat", Heartbeat)
    monkeypatch.setattr(
        worker,
        "dispatch_action",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            worker.release_admin.CrawlerReleaseAdminError("commit outcome is unknown")
        ),
    )
    monkeypatch.setattr(
        worker,
        "_probe_committed_action",
        lambda *_args, **_kwargs: ("unavailable", None),
    )
    monkeypatch.setattr(
        worker,
        "complete_failure",
        lambda *_args, **_kwargs: pytest.fail("ambiguous commit was marked failed"),
    )
    retried: list[int] = []

    def retry(_connection: Any, owned: worker.ActionLease, *, max_attempts: int):
        retried.append(max_attempts)
        assert owned == lease
        return worker.Completion("completed")

    monkeypatch.setattr(worker, "retry_owned", retry)

    assert worker.run_once(_config(), connection_factory) == "completed"
    assert retried == [5]
    assert connections and all(connection.closed for connection in connections)


def test_unprovable_reconciliation_never_claims_release_failed() -> None:
    lease = replace(
        _lease("pause_rollout", {"rollout_id": ROLLOUT_ID}),
        attempt_count=5,
        reconcile_only=True,
    )

    def handler(statement: str, parameters: Any):
        assert "status = 'reconciliation_required'" in statement
        assert "status = 'failed'" not in statement
        assert parameters[1:] == (REQUEST_ID, 5, lease.lease_owner, LEASE_TOKEN)
        return [], 1

    result = worker.complete_reconciliation_required(
        FakeConnection(handler), lease, message="operator review is required"
    )
    assert result.state == "completed"


def test_read_only_enrollment_and_baseline_queries_do_not_take_row_locks() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "ops_agent"
        / "crawler_release_action_worker.py"
    ).read_text(encoding="utf-8")
    enrollment = source[source.index("def _load_enrolled_workers"):source.index("def _reconcile_create")]
    baseline = source[source.index("def _verify_canary_baseline"):source.index("def _reconcile_transition")]
    assert "readonly=True" in enrollment and "FOR SHARE" not in enrollment
    assert "readonly=True" in baseline and "FOR SHARE" not in baseline


def test_create_canary_uses_exact_db_workers_and_generation(monkeypatch) -> None:
    lease = _lease(
        "create_canary",
        {
            "artifact_digest": DIGEST_A,
            "baseline_digest": DIGEST_B,
            "rollout_id": ROLLOUT_ID,
            "worker_keys": ["canary", "stable"],
        },
        generation=12,
    )
    enrolled = [
        {
            "worker_key": "canary",
            "agent_id": "55555555-5555-4555-8555-555555555555",
            "hostname": "canary",
            "cohort": "canary",
            "enabled": True,
        },
        {
            "worker_key": "stable",
            "agent_id": "66666666-6666-4666-8666-666666666666",
            "hostname": "stable",
            "cohort": "stable",
            "enabled": True,
        },
    ]
    captured: dict[str, Any] = {}
    monkeypatch.setattr(worker, "_reconcile_create", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker, "_load_enrolled_workers", lambda *_args, **_kwargs: enrolled)
    monkeypatch.setattr(worker, "_verify_canary_baseline", lambda *_args, **_kwargs: None)

    def create_rollout(_connection: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"status": "CREATED", "generation": kwargs["generation"]}

    monkeypatch.setattr(worker.release_admin, "create_rollout", create_rollout)
    result = worker.dispatch_action(object(), lease, public_root=worker.PUBLIC_ROOT)

    assert result == {"status": "CREATED", "generation": 12}
    assert captured["generation"] == 12
    assert captured["workers"] is enrolled
    assert captured["public_root"] == worker.PUBLIC_ROOT


def test_canary_baseline_requires_one_exact_desired_identity_and_healthy_active_reports() -> None:
    enrolled = [
        {
            "worker_key": "canary",
            "agent_id": "55555555-5555-4555-8555-555555555555",
            "enabled": True,
        },
        {
            "worker_key": "stable",
            "agent_id": "66666666-6666-4666-8666-666666666666",
            "enabled": False,
        },
    ]

    def connection(canary_digest: str, stable_digest: str) -> FakeConnection:
        def handler(statement: str, _parameters: Any):
            if "FROM ops_crawler_worker_desired_state desired" in statement:
                return [
                    {
                        "worker_key": "canary",
                        "agent_id": enrolled[0]["agent_id"],
                        "generation": 8,
                        "desired_status": "active",
                        "artifact_digest": canary_digest,
                        "code_version": "v8",
                        "config_revision": "config-8",
                        "agent_healthy": True,
                        "agent_fresh": True,
                    },
                    {
                        "worker_key": "stable",
                        "agent_id": enrolled[1]["agent_id"],
                        "generation": 8,
                        "desired_status": "disabled",
                        "artifact_digest": stable_digest,
                        "code_version": "v8",
                        "config_revision": "config-8",
                        "agent_healthy": False,
                        "agent_fresh": False,
                    },
                ], 2
            if "FROM ops_crawler_release_reports report" in statement:
                return [
                    {
                        "worker_key": "canary",
                        "agent_id": enrolled[0]["agent_id"],
                        "desired_generation": 8,
                        "status": "rolled_back",
                        "artifact_digest": canary_digest,
                        "code_version": "v8",
                        "config_revision": "config-8",
                        "health": {"healthy": True},
                        "report_fresh": True,
                    }
                ], 1
            raise AssertionError(statement)

        return FakeConnection(handler)

    worker._verify_canary_baseline(
        connection(DIGEST_B, DIGEST_B),
        environment="production",
        baseline_digest=DIGEST_B,
        enrolled_workers=enrolled,
    )

    with pytest.raises(worker.ActionRejected) as mismatch:
        worker._verify_canary_baseline(
            connection(DIGEST_B, DIGEST_A),
            environment="production",
            baseline_digest=DIGEST_B,
            enrolled_workers=enrolled,
        )
    assert mismatch.value.code == "baseline_identity_mismatch"


def test_first_canary_rollout_fails_without_installed_baseline_evidence() -> None:
    enrolled = [
        {
            "worker_key": "canary",
            "agent_id": "55555555-5555-4555-8555-555555555555",
            "enabled": True,
        }
    ]

    def handler(statement: str, _parameters: Any):
        if "ops_crawler_worker_desired_state" in statement:
            return [], 0
        if "ops_crawler_release_reports" in statement:
            return [], 0
        raise AssertionError(statement)

    with pytest.raises(worker.ActionRejected) as rejected:
        worker._verify_canary_baseline(
            FakeConnection(handler),
            environment="production",
            baseline_digest=DIGEST_B,
            enrolled_workers=enrolled,
        )
    assert rejected.value.code == "bootstrap_baseline_evidence_unavailable"


def test_canary_baseline_rejects_stale_active_agent_or_report() -> None:
    enrolled = [
        {
            "worker_key": "canary",
            "agent_id": "55555555-5555-4555-8555-555555555555",
            "enabled": True,
        }
    ]

    def connection(*, agent_fresh: bool, report_fresh: bool) -> FakeConnection:
        def handler(statement: str, parameters: Any):
            if "FROM ops_crawler_worker_desired_state desired" in statement:
                assert parameters[0] == worker.BASELINE_FRESH_SECONDS
                return [
                    {
                        "worker_key": "canary",
                        "agent_id": enrolled[0]["agent_id"],
                        "generation": 8,
                        "desired_status": "active",
                        "artifact_digest": DIGEST_B,
                        "code_version": "v8",
                        "config_revision": "config-8",
                        "agent_healthy": True,
                        "agent_fresh": agent_fresh,
                    }
                ], 1
            if "FROM ops_crawler_release_reports report" in statement:
                assert parameters[0] == worker.BASELINE_FRESH_SECONDS
                return [
                    {
                        "worker_key": "canary",
                        "agent_id": enrolled[0]["agent_id"],
                        "desired_generation": 8,
                        "status": "ready",
                        "artifact_digest": DIGEST_B,
                        "code_version": "v8",
                        "config_revision": "config-8",
                        "health": {"healthy": True},
                        "report_fresh": report_fresh,
                    }
                ], 1
            raise AssertionError(statement)

        return FakeConnection(handler)

    for stale in (
        connection(agent_fresh=False, report_fresh=True),
        connection(agent_fresh=True, report_fresh=False),
    ):
        with pytest.raises(worker.ActionRejected) as rejected:
            worker._verify_canary_baseline(
                stale,
                environment="production",
                baseline_digest=DIGEST_B,
                enrolled_workers=enrolled,
                fresh_seconds=worker.BASELINE_FRESH_SECONDS,
            )
        assert rejected.value.code == "baseline_report_unhealthy"


def test_first_rollout_loads_complete_reviewed_fleet_without_desired_state(
    monkeypatch,
) -> None:
    canary = SimpleNamespace(
        worker_key="canary",
        rollout_order=1,
        enabled=True,
        canary=True,
        kernel_hostname="canary-host",
    )
    stable = SimpleNamespace(
        worker_key="stable",
        rollout_order=2,
        enabled=False,
        canary=False,
        kernel_hostname="stable-host",
    )
    monkeypatch.setattr(
        worker,
        "load_production_topology",
        lambda _root: SimpleNamespace(
            crawler_mode="distributed",
            crawler_workers={"canary": canary, "stable": stable},
        ),
    )

    def handler(statement: str, _parameters: Any):
        assert "ops_crawler_worker_desired_state" not in statement
        if "FROM ops_agents agent" in statement:
            return [
                {
                    "agent_id": "55555555-5555-4555-8555-555555555555",
                    "name": "canary distributed crawler",
                    "hostname": "canary-host",
                    "environment": "production",
                    "status": "healthy",
                    "maintenance_mode": False,
                    "capabilities": ["crawler_worker"],
                },
                {
                    "agent_id": "66666666-6666-4666-8666-666666666666",
                    "name": "stable distributed crawler",
                    "hostname": "stable-host",
                    "environment": "production",
                    "status": "unknown",
                    "maintenance_mode": False,
                    "capabilities": ["crawler_worker"],
                },
            ], 2
        if "FROM ops_crawler_agent_bindings" in statement:
            return [
                {
                    "agent_id": agent_id,
                    "environment": "production",
                    "binding_type": binding,
                }
                for agent_id in (
                    "55555555-5555-4555-8555-555555555555",
                    "66666666-6666-4666-8666-666666666666",
                )
                for binding in ("reporter", "worker")
            ], 4
        raise AssertionError(statement)

    connection = FakeConnection(handler)
    enrolled = worker._load_enrolled_workers(
        connection,
        environment="production",
        worker_keys=["stable", "canary"],
    )

    assert [item["worker_key"] for item in enrolled] == ["canary", "stable"]
    assert enrolled[0]["cohort"] == "canary" and enrolled[0]["enabled"] is True
    assert enrolled[1]["cohort"] == "stable" and enrolled[1]["enabled"] is False
    assert connection.rollbacks == 1


def test_canary_requires_complete_fleet_and_enabled_reviewed_canary(monkeypatch) -> None:
    canary = SimpleNamespace(
        worker_key="canary",
        rollout_order=1,
        enabled=False,
        canary=True,
        kernel_hostname="canary-host",
    )
    stable = SimpleNamespace(
        worker_key="stable",
        rollout_order=2,
        enabled=False,
        canary=False,
        kernel_hostname="stable-host",
    )
    topology = SimpleNamespace(
        crawler_mode="distributed",
        crawler_workers={"canary": canary, "stable": stable},
    )
    monkeypatch.setattr(worker, "load_production_topology", lambda _root: topology)

    with pytest.raises(worker.ActionRejected) as incomplete:
        worker._load_enrolled_workers(
            object(), environment="production", worker_keys=["canary"]
        )
    assert incomplete.value.code == "worker_contract_incomplete"

    with pytest.raises(worker.ActionRejected) as unavailable:
        worker._load_enrolled_workers(
            object(), environment="production", worker_keys=["canary", "stable"]
        )
    assert unavailable.value.code == "canary_unavailable"


@pytest.mark.parametrize(
    ("action", "payload", "expected_phase", "expected_targets"),
    [
        (
            "advance_rollout",
            {
                "rollout_id": ROLLOUT_ID,
                "rollout_phase": "rolling",
                "target_worker_keys": ["canary", "stable"],
            },
            "rolling",
            ["canary", "stable"],
        ),
        (
            "advance_rollout",
            {"rollout_id": ROLLOUT_ID, "rollout_phase": "complete"},
            "complete",
            [],
        ),
        ("pause_rollout", {"rollout_id": ROLLOUT_ID}, "paused", []),
        ("rollback_rollout", {"rollout_id": ROLLOUT_ID}, "rollback", []),
        ("complete_rollback", {"rollout_id": ROLLOUT_ID}, "rolled_back", []),
    ],
)
def test_rollout_actions_call_only_fixed_advance_function(
    monkeypatch,
    action: str,
    payload: dict[str, Any],
    expected_phase: str,
    expected_targets: list[str],
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(worker, "_reconcile_transition", lambda *_args, **_kwargs: None)

    def advance(_connection: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"status": "ADVANCED", "phase": kwargs["phase"]}

    monkeypatch.setattr(worker.release_admin, "advance_rollout", advance)
    result = worker.dispatch_action(
        object(),
        _lease(action, payload),
        public_root=worker.PUBLIC_ROOT,
    )

    assert result == {"status": "ADVANCED", "phase": expected_phase}
    assert captured["expected_generation"] == 7
    assert captured["next_generation"] == 8
    assert captured["phase"] == expected_phase
    assert captured["target_workers"] == expected_targets
    assert captured["fresh_seconds"] == worker.BASELINE_FRESH_SECONDS


@pytest.mark.parametrize("action", ["build", "register_artifact"])
def test_builder_and_registration_fail_closed_without_evidence_handoff(action: str) -> None:
    payload = (
        {"source_commit": "a" * 40, "source_tree": "b" * 40, "test_profile": "crawler"}
        if action == "build"
        else {
            "build_request_id": "77777777-7777-4777-8777-777777777777",
            "artifact_digest": DIGEST_A,
            "code_version": "v1",
            "config_revision": "config-v1",
        }
    )
    with pytest.raises(worker.ActionRejected) as rejected:
        worker.dispatch_action(
            object(),
            _lease(action, payload, generation=0),
            public_root=worker.PUBLIC_ROOT,
        )
    assert rejected.value.code == "not_implemented"


def test_dispatch_rejects_persisted_confirmation_substitution(monkeypatch) -> None:
    lease = _lease(
        "advance_rollout",
        {
            "rollout_id": ROLLOUT_ID,
            "rollout_phase": "rolling",
            "target_worker_keys": ["canary", "stable"],
        },
    )
    monkeypatch.setattr(
        worker.release_admin,
        "advance_rollout",
        lambda *_args, **_kwargs: pytest.fail("tampered request reached release admin"),
    )
    with pytest.raises(worker.ActionRejected) as rejected:
        worker.dispatch_action(
            object(),
            replace(lease, confirmation=f"ADVANCE {ROLLOUT_ID} 7 rolling none"),
            public_root=worker.PUBLIC_ROOT,
        )
    assert rejected.value.code == "invalid_confirmation"


def test_committed_rolling_retry_reconciles_only_exact_worker_targets() -> None:
    lease = _lease(
        "advance_rollout",
        {
            "rollout_id": ROLLOUT_ID,
            "rollout_phase": "rolling",
            "target_worker_keys": ["canary", "stable"],
        },
    )

    def connection(stable_digest: str) -> FakeConnection:
        def handler(statement: str, _parameters: Any):
            if "FROM ops_crawler_release_rollouts" in statement:
                return [
                    {
                        "rollout_epoch": 8,
                        "status": "running",
                        "strategy": {"schema_version": 1, "state": "rolling"},
                        "artifact_digest": DIGEST_A,
                        "previous_artifact_digest": DIGEST_B,
                    }
                ], 1
            if "FROM ops_crawler_rollout_worker_snapshots" in statement:
                return [
                    {
                        "worker_key": "canary",
                        "generation": 8,
                        "desired_status": "active",
                        "artifact_digest": DIGEST_A,
                    },
                    {
                        "worker_key": "stable",
                        "generation": 8,
                        "desired_status": "active",
                        "artifact_digest": stable_digest,
                    },
                ], 2
            raise AssertionError(statement)

        return FakeConnection(handler)

    recovered = worker._reconcile_transition(
        connection(DIGEST_A),
        lease,
        rollout_id=ROLLOUT_ID,
        phase="rolling",
        target_workers=["canary", "stable"],
    )
    assert recovered is not None and recovered["recovered"] is True

    with pytest.raises(worker.ActionRejected) as conflict:
        worker._reconcile_transition(
            connection(DIGEST_B),
            lease,
            rollout_id=ROLLOUT_ID,
            phase="rolling",
            target_workers=["canary", "stable"],
        )
    assert conflict.value.code == "rollout_identity_conflict"


def test_worker_has_no_command_or_remote_execution_seam() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "ops_agent"
        / "crawler_release_action_worker.py"
    ).read_text(encoding="utf-8")

    for forbidden in (
        "import subprocess",
        "os.system(",
        "Popen(",
        "shell=True",
        "paramiko",
        "fabric",
    ):
        assert forbidden not in source
    assert "release_admin.create_rollout(" in source
    assert "release_admin.advance_rollout(" in source
    assert "OPS_CRAWLER_RELEASE_PUBLIC_ROOT" in source
    assert str(worker.PUBLIC_ROOT).replace("\\", "/") in source


def test_release_action_service_and_control_release_are_wired_fail_closed() -> None:
    root = Path(__file__).resolve().parents[1]
    unit = (
        root
        / "deploy"
        / "ubuntu"
        / "systemd"
        / "mooncen-crawler-release-action-worker.service"
    ).read_text(encoding="utf-8")
    installer = (
        root / "deploy" / "ubuntu" / "setup_distributed_crawler_control.sh"
    ).read_text(encoding="utf-8")
    builder = (root / "tools" / "build_crawler_control_release.py").read_text(
        encoding="utf-8"
    )

    assert "User=root" in unit and "Group=root" in unit
    assert "EnvironmentFile=/etc/mooncen/crawler-release-admin.env" in unit
    assert "--component release_admin" in unit
    assert "ops_agent.crawler_release_action_worker --check" in unit
    assert "ExecStart=/opt/mooncen/.venv/bin/python -X utf8 -m " in unit
    assert "ReadOnlyPaths=/var/lib/mooncen-crawler-control/public" in unit
    assert "NoNewPrivileges=true" in unit
    assert "CapabilityBoundingSet=" in unit
    assert "ExecStart=/bin/sh" not in unit and "ExecStart=/bin/bash" not in unit
    assert "mooncen-crawler-release-action-worker.service" in installer
    assert '"ops_agent/crawler_release_action_worker.py"' in builder
    assert (
        '"deploy/ubuntu/systemd/mooncen-crawler-release-action-worker.service"'
        in builder
    )
