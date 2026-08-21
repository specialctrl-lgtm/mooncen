from __future__ import annotations

import json
import socket
from pathlib import Path
from uuid import UUID

import pytest

from ops_agent import crawler_worker


def _config(tmp_path: Path) -> crawler_worker.WorkerConfig:
    return crawler_worker.WorkerConfig(
        environment="production",
        agent_id=UUID("00000000-0000-0000-0000-000000000111"),
        poll_interval=2,
        command_timeout=60,
        code_version="release-42",
        artifact_digest="a" * 64,
        config_revision="config-42",
        worker_key="gen1crawler",
        health_state_path=tmp_path / "health.json",
        drain_state_path=tmp_path / "drain.json",
    )


@pytest.mark.parametrize(
    ("return_code", "status", "expected"),
    [
        (0, "success", False),
        (1, "failed", True),
        (3, "partial_success", True),
        (75, "failed", True),
    ],
)
def test_process_level_failures_use_the_retry_budget(
    return_code: int, status: str, expected: bool
) -> None:
    assert crawler_worker._retryable_crawler_outcome(return_code, status) is expected


def test_worker_health_matches_release_agent_status_contract(tmp_path: Path) -> None:
    crawler_worker._publish_worker_health(_config(tmp_path))

    payload = json.loads((tmp_path / "health.json").read_text(encoding="utf-8"))
    assert set(payload) == {
        "schema_version",
        "worker_id",
        "healthy",
        "code_version",
        "artifact_digest",
        "config_revision",
        "observed_at",
    }
    assert payload["healthy"] is True
    assert payload["worker_id"] == "gen1crawler"


def test_worker_drain_is_bound_to_central_rollout_generation(tmp_path: Path) -> None:
    crawler_worker._publish_worker_drain(
        _config(tmp_path),
        {
            "rollout_id": "00000000-0000-0000-0000-000000000222",
            "generation": 42,
            "desired_status": "draining",
        },
    )

    payload = json.loads((tmp_path / "drain.json").read_text(encoding="utf-8"))
    assert set(payload) == {
        "schema_version",
        "worker_id",
        "rollout_id",
        "generation",
        "drained",
        "active_jobs",
        "observed_at",
    }
    assert payload["drained"] is True
    assert payload["active_jobs"] == 0


def test_worker_hostname_requires_exact_enrolled_host(monkeypatch: pytest.MonkeyPatch) -> None:
    actual = socket.gethostname().strip().lower().rstrip(".")
    monkeypatch.setenv("OPS_CRAWLER_WORKER_HOSTNAME", actual)

    assert crawler_worker.configured_worker_hostname("staging") == actual

    monkeypatch.setenv("OPS_CRAWLER_WORKER_HOSTNAME", "different-worker.example")
    with pytest.raises(RuntimeError, match="does not match"):
        crawler_worker.configured_worker_hostname("staging")


class _HeartbeatCursor:
    def __init__(self, row) -> None:
        self.row = row
        self.statement = ""
        self.params = ()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, params) -> None:
        self.statement = statement
        self.params = params

    def fetchone(self):
        return self.row


class _HeartbeatConnection:
    def __init__(self, row) -> None:
        self.cursor_value = _HeartbeatCursor(row)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_value

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class _DesiredStateCursor:
    def __init__(self, row) -> None:
        self.row = row
        self.statement = ""
        self.params = ()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, params) -> None:
        self.statement = statement
        self.params = params

    def fetchone(self):
        return self.row


class _DesiredStateConnection:
    def __init__(self, row) -> None:
        self.cursor_value = _DesiredStateCursor(row)
        self.commits = 0

    def cursor(self, **_kwargs):
        return self.cursor_value

    def commit(self) -> None:
        self.commits += 1


def test_agent_heartbeat_is_bound_to_login_environment_and_hostname(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config = crawler_worker.replace(config, hostname="worker-01")
    connection = _HeartbeatConnection((str(config.agent_id),))

    crawler_worker._heartbeat_registered_agent(connection, config)

    assert "credential_hint = 'crawler-worker:' || session_user" in connection.cursor_value.statement
    assert "AND environment = %s" in connection.cursor_value.statement
    assert "AND hostname = %s" in connection.cursor_value.statement
    assert connection.cursor_value.params[-2:] == ("production", "worker-01")
    assert connection.commits == 1
    assert connection.rollbacks == 0


def test_agent_heartbeat_fails_closed_for_missing_enrollment(tmp_path: Path) -> None:
    config = crawler_worker.replace(_config(tmp_path), hostname="worker-01")
    connection = _HeartbeatConnection(None)

    with pytest.raises(crawler_worker.CrawlerLeaseLost, match="enrollment"):
        crawler_worker._heartbeat_registered_agent(connection, config)

    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_worker_desired_state_requires_exact_running_release(tmp_path: Path) -> None:
    config = _config(tmp_path)
    connection = _DesiredStateConnection(
        {
            "rollout_id": "00000000-0000-4000-8000-000000000222",
            "generation": 42,
            "desired_status": "active",
            "code_version": config.code_version,
            "artifact_digest": config.artifact_digest,
            "config_revision": config.config_revision,
        }
    )

    desired = crawler_worker._load_worker_desired_state(connection, config)

    assert desired is not None
    assert desired["generation"] == 42
    assert "code_version, artifact_digest, config_revision" in connection.cursor_value.statement
    assert connection.commits == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("code_version", "release-43"),
        ("artifact_digest", "b" * 64),
        ("config_revision", "config-43"),
    ],
)
def test_worker_desired_state_rejects_release_drift(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    config = _config(tmp_path)
    row = {
        "rollout_id": "00000000-0000-4000-8000-000000000222",
        "generation": 42,
        "desired_status": "active",
        "code_version": config.code_version,
        "artifact_digest": config.artifact_digest,
        "config_revision": config.config_revision,
    }
    row[field] = value
    connection = _DesiredStateConnection(row)

    with pytest.raises(RuntimeError, match="release identity"):
        crawler_worker._load_worker_desired_state(connection, config)

    assert connection.commits == 1
