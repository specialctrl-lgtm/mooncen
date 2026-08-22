from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from DB import connection_settings
from backend.ops.schemas import DeploymentRequest, JobActionRequest, ParserProbeRequest
from backend.ops import service as ops_service
from backend.routers import ops_v2
from tools.ensure_ops_console_schema import REQUIRED_MIGRATIONS


ROOT = Path(__file__).resolve().parents[1]
COMMIT = "1" * 40
TREE = "2" * 40


class _Result:
    def __init__(self, row=None):
        self.row = row

    def mappings(self):
        return self

    def first(self):
        return self.row


class _TargetReservationDB:
    def __init__(self, active=None):
        self.active = active
        self.calls: list[tuple[str, dict | None]] = []
        self.rollbacks = 0

    def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params))
        if "FROM ops_jobs" in sql:
            return _Result(self.active)
        return _Result()

    def rollback(self):
        self.rollbacks += 1


def test_deployment_heartbeat_lease_uses_one_shared_api_worker_contract(
    monkeypatch,
) -> None:
    monkeypatch.delenv(connection_settings.DEPLOYMENT_HEARTBEAT_LEASE_ENV, raising=False)

    assert connection_settings.deployment_heartbeat_lease_seconds() == 300
    assert (
        ops_service.deployment_heartbeat_lease_seconds
        is connection_settings.deployment_heartbeat_lease_seconds
    )

    for boundary in (60, 3_600):
        monkeypatch.setenv(
            connection_settings.DEPLOYMENT_HEARTBEAT_LEASE_ENV,
            str(boundary),
        )
        assert connection_settings.deployment_heartbeat_lease_seconds() == boundary


@pytest.mark.parametrize("invalid", ("59", "3601", "not-an-integer"))
def test_deployment_heartbeat_lease_rejects_invalid_shared_boundaries(
    monkeypatch,
    invalid: str,
) -> None:
    monkeypatch.setenv(connection_settings.DEPLOYMENT_HEARTBEAT_LEASE_ENV, invalid)

    with pytest.raises(RuntimeError, match="OPS_DEPLOY_STALE_HEARTBEAT_SECONDS"):
        connection_settings.deployment_heartbeat_lease_seconds()


def test_deployment_target_reservation_serializes_and_rejects_existing_active_job() -> None:
    active_id = str(uuid4())
    db = _TargetReservationDB(
        {
            "id": active_id,
            "status": "assigned",
            "environment": "production",
            "target_key": "deployment:cloud",
        }
    )

    with pytest.raises(HTTPException) as raised:
        ops_service.reserve_deployment_target(
            db,  # type: ignore[arg-type]
            environment="production",
            target_key="deployment:cloud",
        )

    assert raised.value.status_code == 409
    assert raised.value.detail == {
        "code": "active_deployment_target",
        "message": "A deployment for this environment and target is already active",
        "active_job_id": active_id,
        "active_status": "assigned",
    }
    assert db.rollbacks == 1
    assert "pg_advisory_xact_lock" in db.calls[0][0]
    active_query, active_params = db.calls[1]
    assert "job_type = 'deployment'" in active_query
    assert "status IN ('queued', 'assigned', 'running')" in active_query
    assert active_params == {
        "environment": "production",
        "target_key": "deployment:cloud",
    }


def test_deployment_target_reservation_keeps_transaction_lock_until_create_commit() -> None:
    db = _TargetReservationDB()

    ops_service.reserve_deployment_target(
        db,  # type: ignore[arg-type]
        environment="production",
        target_key="deployment:cloud",
    )

    assert db.rollbacks == 0
    assert len(db.calls) == 2


def test_deployment_enqueue_captures_one_environment_for_reserve_and_insert(
    monkeypatch,
) -> None:
    environment_reads: list[str] = []

    def changing_environment() -> str:
        value = "production" if not environment_reads else "development"
        environment_reads.append(value)
        return value

    class AtomicDeploymentDB:
        def __init__(self):
            self.calls: list[tuple[str, dict | None]] = []

        def execute(self, statement, params=None):
            sql = str(statement)
            self.calls.append((sql, params))
            if "FROM ops_jobs" in sql:
                return _Result()
            if "INSERT INTO ops_jobs" in sql:
                return _Result(
                    {
                        "id": uuid4(),
                        "job_type": "deployment",
                        "status": "queued",
                        "environment": params["environment"],
                        "target_key": params["target_key"],
                    }
                )
            return _Result()

    db = AtomicDeploymentDB()
    monkeypatch.setattr(ops_service, "require_ops_schema", lambda *_args: None)
    monkeypatch.setattr(ops_service, "current_environment", changing_environment)
    monkeypatch.setattr(ops_service, "add_job_log", lambda *_args, **_kwargs: None)

    job = ops_service.enqueue_job(
        db,  # type: ignore[arg-type]
        job_type="deployment",
        requested_by=uuid4(),
        parameters={"target": "cloud"},
        target_key="deployment:cloud",
        max_retries=0,
    )

    assert environment_reads == ["production"]
    reserve_index = next(
        index
        for index, (sql, _params) in enumerate(db.calls)
        if "pg_advisory_xact_lock" in sql
    )
    insert_index = next(
        index
        for index, (sql, _params) in enumerate(db.calls)
        if "INSERT INTO ops_jobs" in sql
    )
    assert reserve_index < insert_index
    active_params = next(params for sql, params in db.calls if "FROM ops_jobs" in sql)
    insert_params = db.calls[insert_index][1]
    assert active_params["environment"] == "production"
    assert insert_params["environment"] == "production"
    assert job["environment"] == "production"


@pytest.mark.parametrize(
    "target_key",
    (
        " deployment:cloud",
        "deployment:cloud ",
        "deployment:Cloud",
        "deployment:",
        "cloud",
    ),
)
def test_deployment_target_reservation_rejects_noncanonical_keys(
    target_key: str,
) -> None:
    db = _TargetReservationDB()

    with pytest.raises(HTTPException) as raised:
        ops_service.reserve_deployment_target(
            db,  # type: ignore[arg-type]
            environment="production",
            target_key=target_key,
        )

    assert raised.value.status_code == 422
    assert db.calls == []


@pytest.mark.parametrize(
    ("parameters", "target_key"),
    (
        ({}, "deployment:cloud"),
        ({"target": "Cloud"}, "deployment:Cloud"),
        ({"target": "cloud"}, "deployment:other"),
        ({"target": "a" * 33}, f"deployment:{'a' * 33}"),
    ),
)
def test_deployment_enqueue_identity_requires_exact_parameters_target(
    parameters: dict,
    target_key: str,
) -> None:
    with pytest.raises(HTTPException) as raised:
        ops_service.validated_job_target_key(
            "deployment",
            parameters,
            target_key,
        )

    assert raised.value.status_code == 422


def test_non_deployment_target_key_is_preserved_without_silent_truncation(
    monkeypatch,
) -> None:
    prefix = "parser-probe:https://example.test/"
    target_key = prefix + ("a" * (ops_service.JOB_TARGET_KEY_MAX_LENGTH - len(prefix)))
    captured: list[dict] = []

    class InsertDB:
        def execute(self, _statement, params=None):
            captured.append(params)
            return _Result(
                {
                    "id": uuid4(),
                    "job_type": "crawler_run",
                    "status": "queued",
                }
            )

    monkeypatch.setattr(ops_service, "require_ops_schema", lambda *_args: None)
    monkeypatch.setattr(ops_service, "add_job_log", lambda *_args, **_kwargs: None)

    ops_service.enqueue_job(
        InsertDB(),  # type: ignore[arg-type]
        job_type="crawler_run",
        requested_by=uuid4(),
        parameters={"url": "https://example.test/"},
        target_key=target_key,
        max_retries=0,
    )

    assert captured[0]["target_key"] == target_key


def test_long_non_deployment_target_keys_are_deterministically_compacted() -> None:
    shared = "parser-probe:https://example.test/" + ("a" * 600)
    first = ops_service.validated_job_target_key(
        "agent_command",
        {},
        shared + "first-tail",
    )
    repeated = ops_service.validated_job_target_key(
        "agent_command",
        {},
        shared + "first-tail",
    )
    different_tail = ops_service.validated_job_target_key(
        "agent_command",
        {},
        shared + "second-tail",
    )

    assert len(first) == ops_service.JOB_TARGET_KEY_MAX_LENGTH
    assert first == repeated
    assert first != different_tail
    assert first.startswith("parser-probe:https://example.test/")
    assert ops_service.JOB_TARGET_KEY_DIGEST_MARKER in first


def test_maximum_length_parser_probe_url_keeps_a_bounded_queue_identity() -> None:
    url_prefix = "https://example.test/"
    url = url_prefix + ("a" * (4_096 - len(url_prefix)))
    payload = ParserProbeRequest(url=url)

    target_key = ops_service.validated_job_target_key(
        "agent_command",
        payload.model_dump(),
        f"parser-probe:{payload.url}",
    )

    assert len(payload.url) == 4_096
    assert len(target_key) == ops_service.JOB_TARGET_KEY_MAX_LENGTH
    assert target_key.startswith("parser-probe:https://example.test/")


def test_parser_probe_fails_closed_in_distributed_runtime(monkeypatch) -> None:
    monkeypatch.setattr(ops_v2, "current_environment", lambda: "production")

    with pytest.raises(HTTPException) as raised:
        ops_v2.run_parser_probe(
            ParserProbeRequest(url="https://example.test/course"),
            None,  # type: ignore[arg-type]
            SimpleNamespace(id=uuid4()),
            None,  # type: ignore[arg-type]
        )

    assert raised.value.status_code == 503
    assert raised.value.detail["code"] == "distributed_parser_probe_unavailable"


def test_retry_of_legacy_maximum_length_key_is_compacted_not_rejected() -> None:
    original_prefix = "provider:"
    original = original_prefix + (
        "a" * (ops_service.JOB_TARGET_KEY_MAX_LENGTH - len(original_prefix))
    )

    target_key = ops_service.validated_job_target_key(
        "crawler_retry",
        {"retry_of": "00000000-0000-0000-0000-000000000001"},
        f"retry:{original}",
    )

    assert len(target_key) == ops_service.JOB_TARGET_KEY_MAX_LENGTH
    assert target_key.startswith(f"retry:{original_prefix}")
    assert ops_service.JOB_TARGET_KEY_DIGEST_MARKER in target_key


def test_active_deployment_unique_index_conflict_maps_to_target_specific_409(monkeypatch) -> None:
    class ConflictDB:
        rollbacks = 0

        def execute(self, *_args, **_kwargs):
            raise IntegrityError(
                "INSERT",
                {},
                RuntimeError(ops_service.ACTIVE_DEPLOYMENT_TARGET_INDEX),
            )

        def rollback(self):
            self.rollbacks += 1

    db = ConflictDB()
    monkeypatch.setattr(ops_service, "require_ops_schema", lambda *_args: None)
    monkeypatch.setattr(
        ops_service,
        "reserve_deployment_target",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(HTTPException) as raised:
        ops_service.enqueue_job(
            db,  # type: ignore[arg-type]
            job_type="deployment",
            requested_by=uuid4(),
            parameters={"target": "cloud"},
            target_key="deployment:cloud",
            max_retries=0,
        )

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "active_deployment_target"
    assert db.rollbacks == 1


def test_existing_payload_deduplication_conflict_keeps_its_409_contract(monkeypatch) -> None:
    class ConflictDB:
        rollbacks = 0

        def execute(self, *_args, **_kwargs):
            raise IntegrityError(
                "INSERT",
                {},
                RuntimeError("ux_ops_jobs_active_deduplication"),
            )

        def rollback(self):
            self.rollbacks += 1

    db = ConflictDB()
    monkeypatch.setattr(ops_service, "require_ops_schema", lambda *_args: None)
    monkeypatch.setattr(
        ops_service,
        "reserve_deployment_target",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(HTTPException) as raised:
        ops_service.enqueue_job(
            db,  # type: ignore[arg-type]
            job_type="deployment",
            requested_by=uuid4(),
            parameters={"target": "cloud"},
            target_key="deployment:cloud",
            max_retries=0,
        )

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "duplicate_active_job"
    assert db.rollbacks == 1


class _CancellationDB:
    def __init__(self, before: dict):
        self.before = before
        self.calls: list[tuple[str, dict | None]] = []
        self.commits = 0

    def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params))
        if "FROM ops_jobs" in sql and "FOR UPDATE" in sql:
            return _Result(self.before)
        if "UPDATE ops_jobs" in sql:
            now = datetime.now(timezone.utc)
            return _Result(
                {
                    "id": self.before["id"],
                    "status": params["status"],
                    "cancel_requested_at": now,
                    "finished_at": now if params["status"] == "cancelled" else None,
                }
            )
        return _Result()

    def commit(self):
        self.commits += 1


@pytest.mark.parametrize(
    ("job_status", "stale_assignment", "expected_status", "disposition", "terminal"),
    (
        ("queued", False, "cancelled", "cancelled_queued", True),
        (
            "assigned",
            True,
            "cancelled",
            "cancelled_stale_assignment",
            True,
        ),
        (
            "assigned",
            False,
            "assigned",
            "cancellation_requested",
            False,
        ),
        (
            "running",
            False,
            "running",
            "cancellation_requested",
            False,
        ),
    ),
)
def test_deployment_cancel_contract_distinguishes_unowned_and_live_jobs(
    monkeypatch,
    job_status: str,
    stale_assignment: bool,
    expected_status: str,
    disposition: str,
    terminal: bool,
) -> None:
    job_id = uuid4()
    db = _CancellationDB(
        {
            "id": str(job_id),
            "status": job_status,
            "job_type": "deployment",
            "target_key": "deployment:cloud",
            "assigned_at": datetime.now(timezone.utc),
            "started_at": None if job_status != "running" else datetime.now(timezone.utc),
            "heartbeat_at": datetime.now(timezone.utc),
            "stale_assignment": stale_assignment,
        }
    )
    audit: list[dict] = []
    monkeypatch.setattr(ops_v2, "require_ops_schema", lambda *_args: None)
    monkeypatch.setattr(ops_v2, "deployment_heartbeat_lease_seconds", lambda: 300)
    monkeypatch.setattr(ops_v2, "add_job_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        ops_v2,
        "append_audit",
        lambda *_args, **kwargs: audit.append(kwargs),
    )

    result = ops_v2.cancel_job(
        job_id,
        JobActionRequest(reason="operator requested cancellation"),
        SimpleNamespace(),  # type: ignore[arg-type]
        SimpleNamespace(id=uuid4()),  # type: ignore[arg-type]
        db,  # type: ignore[arg-type]
    )

    assert result["status"] == expected_status
    assert result["terminal"] is terminal
    assert result["cancellation_disposition"] == disposition
    assert audit[0]["after_data"]["cancellation_disposition"] == disposition
    select_sql, select_params = next(
        (sql, params) for sql, params in db.calls if "FOR UPDATE" in sql
    )
    assert select_params["stale_after_seconds"] == 300
    assert "status = 'assigned'" in select_sql
    assert "started_at IS NULL" in select_sql
    assert "COALESCE(" in select_sql
    assert "make_interval(secs => :stale_after_seconds)" in select_sql
    deployment_updates = [sql for sql, _params in db.calls if "UPDATE ops_deployments" in sql]
    assert bool(deployment_updates) is terminal
    if deployment_updates:
        assert "deployment_status IN ('queued', 'running')" in deployment_updates[0]
    assert db.commits == 1


@pytest.mark.parametrize("job_status", ("assigned", "running"))
def test_container_deployment_rejects_cancellation_after_remote_assignment(
    monkeypatch: pytest.MonkeyPatch,
    job_status: str,
) -> None:
    job_id = uuid4()
    db = _CancellationDB(
        {
            "id": str(job_id),
            "status": job_status,
            "job_type": "deployment",
            "target_key": "deployment:cloud",
            "deployment_mode": "container",
            "assigned_at": datetime.now(timezone.utc),
            "started_at": None if job_status == "assigned" else datetime.now(timezone.utc),
            "heartbeat_at": datetime.now(timezone.utc),
            "stale_assignment": job_status == "assigned",
        }
    )
    monkeypatch.setattr(ops_v2, "require_ops_schema", lambda *_args: None)
    monkeypatch.setattr(ops_v2, "deployment_heartbeat_lease_seconds", lambda: 300)

    with pytest.raises(HTTPException) as raised:
        ops_v2.cancel_job(
            job_id,
            JobActionRequest(reason="operator requested cancellation"),
            SimpleNamespace(),  # type: ignore[arg-type]
            SimpleNamespace(id=uuid4()),  # type: ignore[arg-type]
            db,  # type: ignore[arg-type]
        )

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "container_deployment_cancellation_forbidden"
    assert not any("UPDATE ops_jobs" in sql for sql, _params in db.calls)
    assert db.commits == 0


def test_create_deployment_uses_environment_returned_by_enqueue_service(monkeypatch) -> None:
    job_id = uuid4()
    deployment_id = uuid4()
    calls: list[tuple[str, str, str]] = []
    enqueued_parameters: list[dict] = []

    class CreateDB:
        commits = 0

        def execute(self, statement, _params=None):
            if "INSERT INTO ops_deployments" in str(statement):
                return _Result({"id": str(deployment_id), "job_id": str(job_id)})
            return _Result()

        def commit(self):
            self.commits += 1

    db = CreateDB()
    monkeypatch.setattr(ops_v2, "require_ops_schema", lambda *_args: None)
    monkeypatch.setattr(
        ops_v2,
        "_deployment_readiness_payload",
        lambda _db: {
            "can_deploy": True,
            "snapshot": {"commit": COMMIT, "source_tree": TREE, "branch": "main"},
            "targets": [
                {"name": "cloud", "key_ready": True, "deploy_profile": "full-stack"}
            ],
            "agent": {"id": str(uuid4()), "hostname": "an2p"},
        },
    )
    monkeypatch.setattr(
        ops_v2,
        "reviewed_target",
        lambda _name: SimpleNamespace(
            name="cloud",
            identity="3" * 64,
            deploy_profile="full-stack",
            environment="production",
        ),
    )
    monkeypatch.setattr(ops_v2, "current_environment", lambda: "production")
    def enqueue(_db, **kwargs):
        calls.append(("enqueue", "production", kwargs["target_key"]))
        enqueued_parameters.append(kwargs["parameters"])
        return {"id": job_id, "status": "queued", "environment": "production"}

    monkeypatch.setattr(ops_v2, "enqueue_job", enqueue)
    monkeypatch.setattr(ops_v2, "append_audit", lambda *_args, **_kwargs: None)
    payload = DeploymentRequest(
        target="cloud",
        target_commit=COMMIT,
        source_tree=TREE,
        confirmation=f"DEPLOY cloud {TREE[:12]}",
    )

    result = ops_v2.create_deployment(
        payload,
        SimpleNamespace(),  # type: ignore[arg-type]
        SimpleNamespace(id=uuid4()),  # type: ignore[arg-type]
        db,  # type: ignore[arg-type]
    )

    assert calls == [("enqueue", "production", "deployment:cloud")]
    assert enqueued_parameters[0]["required_agent_hostname"] == "an2p"
    assert result["deployment"]["id"] == str(deployment_id)
    assert db.commits == 1


def test_create_deployment_rejects_target_from_a_different_environment(
    monkeypatch,
) -> None:
    class CreateDB:
        def execute(self, *_args, **_kwargs):
            return _Result()

    monkeypatch.setattr(ops_v2, "require_ops_schema", lambda *_args: None)
    monkeypatch.setattr(
        ops_v2,
        "_deployment_readiness_payload",
        lambda _db: {
            "can_deploy": True,
            "snapshot": {"commit": COMMIT, "source_tree": TREE, "branch": "main"},
            "targets": [
                {
                    "name": "cloud",
                    "key_ready": True,
                    "deploy_profile": "full-stack",
                    "environment": "staging",
                }
            ],
            "agent": {"id": str(uuid4()), "hostname": "an2p"},
        },
    )
    monkeypatch.setattr(
        ops_v2,
        "reviewed_target",
        lambda _name: SimpleNamespace(
            name="cloud",
            identity="3" * 64,
            deploy_profile="full-stack",
            environment="staging",
        ),
    )
    monkeypatch.setattr(ops_v2, "current_environment", lambda: "production")
    payload = DeploymentRequest(
        target="cloud",
        target_commit=COMMIT,
        source_tree=TREE,
        confirmation=f"DEPLOY cloud {TREE[:12]}",
    )

    with pytest.raises(HTTPException) as raised:
        ops_v2.create_deployment(
            payload,
            SimpleNamespace(),  # type: ignore[arg-type]
            SimpleNamespace(id=uuid4()),  # type: ignore[arg-type]
            CreateDB(),  # type: ignore[arg-type]
        )

    assert raised.value.status_code == 409
    assert raised.value.detail == {
        "code": "deployment_target_environment_mismatch",
        "message": "The selected deployment target belongs to a different environment.",
        "current_environment": "production",
        "target_environment": "staging",
    }


def test_deployment_retry_still_requires_a_new_reviewed_request(monkeypatch) -> None:
    class RetryDB:
        def execute(self, *_args, **_kwargs):
            return _Result(
                {
                    "id": str(uuid4()),
                    "job_type": "deployment",
                    "status": "failed",
                    "target_key": "deployment:cloud",
                    "parameters": {"target": "cloud"},
                    "retry_count": 0,
                    "max_retries": 0,
                }
            )

    monkeypatch.setattr(ops_v2, "require_ops_schema", lambda *_args: None)

    with pytest.raises(HTTPException) as raised:
        ops_v2.retry_job(
            uuid4(),
            JobActionRequest(reason="retry after failure"),
            SimpleNamespace(),  # type: ignore[arg-type]
            SimpleNamespace(id=uuid4()),  # type: ignore[arg-type]
            RetryDB(),  # type: ignore[arg-type]
        )

    assert raised.value.status_code == 409


def test_active_deployment_target_migration_is_partial_and_non_destructive() -> None:
    migration = (
        ROOT / "DB/migrations/20260807_001_ops_active_deployment_target.sql"
    ).read_text(encoding="utf-8")

    assert "CREATE UNIQUE INDEX ux_ops_jobs_active_deployment_target" in migration
    assert "ON ops_jobs (environment, target_key)" in migration
    assert "job_type = 'deployment'" in migration
    assert "status IN ('queued', 'assigned', 'running')" in migration
    assert "HAVING COUNT(*) > 1" in migration
    assert "UPDATE ops_jobs" not in migration
    assert "DELETE FROM ops_jobs" not in migration


def test_active_deployment_target_key_contract_is_fail_closed_and_legacy_safe() -> None:
    migration = (
        ROOT / "DB/migrations/20260807_003_ops_deployment_target_key_contract.sql"
    ).read_text(encoding="utf-8")

    assert "job_type = 'deployment'" in migration
    assert "status IN ('queued', 'assigned', 'running')" in migration
    assert "jsonb_typeof(parameters -> 'target') = 'string'" in migration
    assert "char_length(parameters ->> 'target') BETWEEN 1 AND 32" in migration
    assert "^[a-z][a-z0-9_-]{0,31}$" in migration
    assert "target_key = 'deployment:' || (parameters ->> 'target')" in migration
    assert "NOT COALESCE(" in migration
    assert "ADD CONSTRAINT chk_ops_jobs_active_deployment_target_key" in migration
    assert ") NOT VALID;" in migration
    assert "VALIDATE CONSTRAINT chk_ops_jobs_active_deployment_target_key;" in migration
    assert "UPDATE ops_jobs" not in migration
    assert "DELETE FROM ops_jobs" not in migration


def test_local_ops_schema_includes_deployment_migrations_in_dependency_order() -> None:
    expected = (
        "20260724_001_preserve_course_freshness_on_view",
        "20260725_001_ops_console_core",
        "20260803_001_ops_service_host",
        "20260804_001_ops_job_partial_success",
        "20260806_001_ops_deployment_agent_registration",
        "20260806_002_ops_deployment_worker_read_access",
        "20260807_001_ops_active_deployment_target",
        "20260807_002_ops_deployment_api_cancel_access",
        "20260807_003_ops_deployment_target_key_contract",
        "20260819_001_ops_container_deployment_pipeline",
    )

    assert REQUIRED_MIGRATIONS == expected
    assert len(REQUIRED_MIGRATIONS) == len(set(REQUIRED_MIGRATIONS))
    for version in REQUIRED_MIGRATIONS:
        assert (ROOT / "DB" / "migrations" / f"{version}.sql").is_file()
