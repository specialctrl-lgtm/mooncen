from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest

from ops_agent.crawler_control_scheduler import ControlSchedulerConfig
from tools import enqueue_crawler_canary as canary


SLOT = datetime(2026, 8, 10, 13, 0, 0, 123456, tzinfo=timezone.utc)


def _config(tmp_path: Path) -> ControlSchedulerConfig:
    manifest = tmp_path / "providers.yaml"
    manifest.write_text(
        "version: 1\nproviders:\n  - HOMEPLUS\n  - EMART\n",
        encoding="utf-8",
    )
    return ControlSchedulerConfig(
        environment="staging",
        providers=("HOMEPLUS", "EMART"),
        provider_manifest=manifest,
        code_version="release-42",
        artifact_digest="a" * 64,
        config_revision="b" * 64,
        schedule_hour=22,
        schedule_minute=0,
        timezone_name="Asia/Seoul",
        max_late_seconds=21_600,
        poll_seconds=30,
        max_retries=2,
    )


class _Cursor:
    def __init__(self, responses) -> None:
        self.responses = iter(responses)
        self.executed: list[tuple[str, object]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql: str, params=None) -> None:
        self.executed.append((sql, params))

    def fetchone(self):
        return next(self.responses)


class _Connection:
    def __init__(self, responses) -> None:
        self.cursor_value = _Cursor(responses)
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self, **_kwargs):
        return self.cursor_value

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


def test_slot_is_canonical_fractional_recent_utc() -> None:
    assert canary.parse_canary_slot("2026-08-10T13:00:00.123456Z") == SLOT
    canary.assert_recent_slot(SLOT, now=SLOT + timedelta(minutes=15))

    with pytest.raises(canary.CanaryEnqueueError, match="canonical UTC"):
        canary.parse_canary_slot("2026-08-10T13:00:00Z")
    with pytest.raises(canary.CanaryEnqueueError, match="non-zero"):
        canary.parse_canary_slot("2026-08-10T13:00:00.000000Z")
    with pytest.raises(canary.CanaryEnqueueError, match="within 15 minutes"):
        canary.assert_recent_slot(SLOT, now=SLOT + timedelta(minutes=16))


def test_selection_requires_one_reviewed_manifest_owner_and_bounded_retry(
    tmp_path: Path,
) -> None:
    selected = canary.selected_canary_config(
        _config(tmp_path),
        provider="HOMEPLUS",
        max_retries=4,
    )

    assert selected.providers == ("HOMEPLUS",)
    assert selected.max_retries == 4
    assert selected.artifact_digest == "a" * 64

    with pytest.raises(canary.CanaryEnqueueError, match="expanded reviewed task set"):
        canary.selected_canary_config(_config(tmp_path), provider="UNKNOWN")
    with pytest.raises(canary.CanaryEnqueueError, match="canonical uppercase"):
        canary.selected_canary_config(_config(tmp_path), provider="homeplus")
    with pytest.raises(canary.CanaryEnqueueError, match="between 0 and 20"):
        canary.selected_canary_config(
            _config(tmp_path),
            provider="HOMEPLUS",
            max_retries=21,
        )


def test_canary_reuses_atomic_scheduler_contract_for_exactly_one_job(
    tmp_path: Path,
) -> None:
    config = canary.selected_canary_config(
        _config(tmp_path),
        provider="HOMEPLUS",
        max_retries=3,
    )
    connection = _Connection([(True,), None, ("batch",)])

    result = canary.enqueue_canary(connection, config, slot=SLOT)

    assert result.reason == "enqueued"
    assert result.job_count == 1
    assert isinstance(result.batch_id, UUID)
    sql = "\n".join(statement for statement, _params in connection.cursor_value.executed)
    assert sql.count("INSERT INTO ops_crawler_batches") == 1
    assert sql.count("INSERT INTO ops_jobs") == 1
    assert sql.count("INSERT INTO ops_crawler_batch_tasks") == 1
    assert sql.count("INSERT INTO ops_crawler_runs") == 1
    assert "pg_try_advisory_xact_lock" in sql
    active_query = next(
        params
        for statement, params in connection.cursor_value.executed
        if "status IN ('queued', 'assigned', 'running')" in statement
    )
    assert active_query == ("staging", ["HOMEPLUS"])
    batch_params = next(
        params
        for statement, params in connection.cursor_value.executed
        if "INSERT INTO ops_crawler_batches" in statement
    )
    assert UUID(batch_params[0]).int != 0
    assert batch_params[2] == SLOT
    assert batch_params[3] == 1
    job_params = next(
        params
        for statement, params in connection.cursor_value.executed
        if "INSERT INTO ops_jobs" in statement
    )
    assert job_params[3] == "crawler-provider:staging:HOMEPLUS"
    assert job_params[5] == 3
    assert job_params[6:] == ("release-42", "a" * 64, "b" * 64)
    task_params = next(
        params
        for statement, params in connection.cursor_value.executed
        if "INSERT INTO ops_crawler_batch_tasks" in statement
    )
    assert task_params[3:] == ("HOMEPLUS", ["HOMEPLUS"])
    assert connection.commits == 1


def test_active_same_provider_is_fail_closed_without_partial_batch(
    tmp_path: Path,
) -> None:
    config = canary.selected_canary_config(
        _config(tmp_path),
        provider="HOMEPLUS",
    )
    connection = _Connection([(True,), ("HOMEPLUS",)])

    with pytest.raises(canary.CanaryEnqueueError, match="previous_batch_active"):
        canary.enqueue_canary(connection, config, slot=SLOT)

    sql = "\n".join(statement for statement, _params in connection.cursor_value.executed)
    assert "INSERT INTO ops_crawler_batches" not in sql
    assert "INSERT INTO ops_jobs" not in sql
    assert connection.commits == 1


def test_review_document_contains_release_allowlist_slot_and_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = canary.selected_canary_config(_config(tmp_path), provider="HOMEPLUS")
    monkeypatch.setattr(
        canary,
        "provider_output_allowlists",
        lambda _providers: {"HOMEPLUS": ("HOMEPLUS", "MUNI_EXAMPLE")},
    )

    document = canary.review_document(config, slot=SLOT, status="VALIDATED")

    assert document == {
        "status": "VALIDATED",
        "environment": "staging",
        "provider": "HOMEPLUS",
        "execution_provider": "HOMEPLUS",
        "allowed_output_providers": ["HOMEPLUS", "MUNI_EXAMPLE"],
        "scheduled_slot": "2026-08-10T13:00:00.123456Z",
        "max_retries": 2,
        "code_version": "release-42",
        "artifact_digest": "a" * 64,
        "config_revision": "b" * 64,
    }


def test_review_document_exposes_aggregate_execution_owner(tmp_path: Path) -> None:
    config = canary.selected_canary_config(
        replace(
            _config(tmp_path),
            providers=("MUNI_EXAMPLE",),
            provider_execution_owners=(
                ("MUNI_EXAMPLE", "MUNICIPAL_RESERVATION_TARGETS"),
            ),
        ),
        provider="MUNI_EXAMPLE",
    )

    document = canary.review_document(config, slot=SLOT, status="VALIDATED")

    assert document["provider"] == "MUNI_EXAMPLE"
    assert document["execution_provider"] == "MUNICIPAL_RESERVATION_TARGETS"
    assert document["allowed_output_providers"] == ["MUNI_EXAMPLE"]


def test_validation_is_default_and_never_opens_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env_file = tmp_path / "scheduler.env"
    env_file.write_text("ENVIRONMENT=staging\n", encoding="utf-8")
    monkeypatch.setattr(canary, "assert_recent_slot", lambda _slot: None)
    monkeypatch.setattr(canary, "_protected_environment", lambda _path: {"ENVIRONMENT": "staging"})
    monkeypatch.setattr(canary, "_assert_component_environment_permissions", lambda *_args: None)
    monkeypatch.setattr(canary, "_check_required_paths", lambda *_args: None)
    monkeypatch.setattr(canary, "load_config", lambda: _config(tmp_path))
    monkeypatch.setattr(
        canary.psycopg2,
        "connect",
        lambda **_kwargs: pytest.fail("validation must not connect to PostgreSQL"),
    )

    exit_code = canary.main(
        [
            "--env-file",
            str(env_file),
            "--provider",
            "HOMEPLUS",
            "--slot",
            "2026-08-10T13:00:00.123456Z",
        ]
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "VALIDATED"
    assert output["provider"] == "HOMEPLUS"


def test_enqueue_requires_matching_provider_and_artifact_before_connect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / "scheduler.env"
    env_file.write_text("ENVIRONMENT=staging\n", encoding="utf-8")
    monkeypatch.setattr(canary, "assert_recent_slot", lambda _slot: None)
    monkeypatch.setattr(canary, "_protected_environment", lambda _path: {"ENVIRONMENT": "staging"})
    monkeypatch.setattr(canary, "_assert_component_environment_permissions", lambda *_args: None)
    monkeypatch.setattr(canary, "_check_required_paths", lambda *_args: None)
    monkeypatch.setattr(canary, "load_config", lambda: _config(tmp_path))
    monkeypatch.setattr(
        canary.psycopg2,
        "connect",
        lambda **_kwargs: pytest.fail("mismatched confirmation must not connect"),
    )

    exit_code = canary.main(
        [
            "--env-file",
            str(env_file),
            "--provider",
            "HOMEPLUS",
            "--slot",
            "2026-08-10T13:00:00.123456Z",
            "--enqueue",
            "--confirm-provider",
            "HOMEPLUS",
            "--confirm-artifact-sha256",
            "c" * 64,
        ]
    )

    assert exit_code == 1


def test_enqueue_uses_scheduler_identity_and_closes_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env_file = tmp_path / "scheduler.env"
    env_file.write_text("ENVIRONMENT=staging\n", encoding="utf-8")
    connection = _Connection([])
    calls: list[object] = []
    monkeypatch.setattr(canary, "assert_recent_slot", lambda _slot: None)
    monkeypatch.setattr(canary, "_protected_environment", lambda _path: {"ENVIRONMENT": "staging"})
    monkeypatch.setattr(canary, "_assert_component_environment_permissions", lambda *_args: None)
    monkeypatch.setattr(canary, "_check_required_paths", lambda *_args: None)
    monkeypatch.setattr(canary, "load_config", lambda: _config(tmp_path))
    monkeypatch.setattr(
        canary,
        "_connection_config",
        lambda component, environment: {
            "database": "mooncen_staging",
            "user": "control-login",
            "application_name": "preflight",
        },
    )
    monkeypatch.setattr(
        canary.psycopg2,
        "connect",
        lambda **kwargs: calls.append(kwargs) or connection,
    )
    monkeypatch.setattr(
        canary,
        "_database_contract",
        lambda component, _connection, database, environment: calls.append(
            (component, database, environment)
        ),
    )
    monkeypatch.setattr(
        canary,
        "enqueue_canary",
        lambda _connection, _config, *, slot: canary.ScheduleResult(
            is_leader=True,
            batch_id=UUID("aaaaaaaa-0000-0000-0000-000000000001"),
            job_count=1,
            reason="enqueued",
        ),
    )

    exit_code = canary.main(
        [
            "--env-file",
            str(env_file),
            "--provider",
            "HOMEPLUS",
            "--slot",
            "2026-08-10T13:00:00.123456Z",
            "--enqueue",
            "--confirm-provider",
            "HOMEPLUS",
            "--confirm-artifact-sha256",
            "a" * 64,
        ]
    )

    assert exit_code == 0
    assert calls[0] == {
        "database": "mooncen_staging",
        "user": "control-login",
        "application_name": "mooncen-crawler-canary-enqueue",
    }
    assert calls[1] == (
        "scheduler",
        "mooncen_staging",
        {"ENVIRONMENT": "staging"},
    )
    assert connection.closed is True
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "ENQUEUED"
    assert output["batch_id"] == "aaaaaaaa-0000-0000-0000-000000000001"
    assert output["job_count"] == 1
