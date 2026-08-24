from __future__ import annotations

import errno
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from ops_agent import deployment_worker
from ops_agent.deployment_registry import DEPLOYMENT_WORKER_HEARTBEAT_PATH


AGENT_ID = UUID("11111111-1111-4111-8111-111111111111")


class SharingViolation(PermissionError):
    winerror = 32


def _heartbeat_path(root: Path) -> Path:
    return root / DEPLOYMENT_WORKER_HEARTBEAT_PATH


def _temporary_heartbeats(path: Path) -> list[Path]:
    return list(path.parent.glob(f".{path.name}.*.tmp"))


def test_worker_heartbeat_retries_windows_sharing_violation_atomically(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = _heartbeat_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    original = {"pid": 999, "updated_at": 1.0}
    path.write_text(json.dumps(original), encoding="ascii")
    real_replace = deployment_worker.os.replace
    attempts: list[tuple[Path, Path]] = []
    sleeps: list[float] = []

    def sharing_then_replace(source: Path, destination: Path) -> None:
        attempts.append((Path(source), Path(destination)))
        if len(attempts) <= 2:
            assert json.loads(path.read_text(encoding="ascii")) == original
            raise SharingViolation(errno.EACCES, "file is open by a reader")
        real_replace(source, destination)

    monkeypatch.setattr(deployment_worker.os, "replace", sharing_then_replace)
    monkeypatch.setattr(deployment_worker.time, "sleep", sleeps.append)

    deployment_worker._publish_worker_heartbeat(tmp_path)

    published = json.loads(path.read_text(encoding="ascii"))
    assert published["pid"] == deployment_worker.os.getpid()
    assert published["updated_at"] > original["updated_at"]
    assert len(attempts) == 3
    assert sleeps == [
        deployment_worker._worker_heartbeat_retry_delay(1),
        deployment_worker._worker_heartbeat_retry_delay(2),
    ]
    assert _temporary_heartbeats(path) == []


def test_worker_heartbeat_exhausts_bounded_retry_without_destroying_previous_value(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = _heartbeat_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    original = {"pid": 999, "updated_at": 1.0}
    path.write_text(json.dumps(original), encoding="ascii")
    attempts = 0
    sleeps: list[float] = []

    def sharing_violation(_source: Path, _destination: Path) -> None:
        nonlocal attempts
        attempts += 1
        raise SharingViolation(errno.EACCES, "file is still open")

    monkeypatch.setattr(deployment_worker.os, "replace", sharing_violation)
    monkeypatch.setattr(deployment_worker.time, "sleep", sleeps.append)

    with pytest.raises(SharingViolation, match="still open"):
        deployment_worker._publish_worker_heartbeat(tmp_path)

    assert attempts == deployment_worker.WORKER_HEARTBEAT_REPLACE_ATTEMPTS
    assert len(sleeps) == deployment_worker.WORKER_HEARTBEAT_REPLACE_ATTEMPTS - 1
    assert json.loads(path.read_text(encoding="ascii")) == original
    assert _temporary_heartbeats(path) == []


@pytest.mark.parametrize("failed_publish_call", [1, 2], ids=["startup", "main-loop"])
def test_worker_stops_when_required_heartbeat_cannot_be_published(
    monkeypatch: pytest.MonkeyPatch,
    failed_publish_call: int,
) -> None:
    class Connection:
        closed = False

        def close(self) -> None:
            self.closed = True

    connection = Connection()
    publish_calls = 0

    def publish() -> bool:
        nonlocal publish_calls
        publish_calls += 1
        return publish_calls != failed_publish_call

    monkeypatch.setattr(
        deployment_worker,
        "parse_args",
        lambda _argv=None: SimpleNamespace(
            once=False,
            agent_id=AGENT_ID,
            poll_interval=0.5,
        ),
    )
    monkeypatch.setattr(deployment_worker, "normalized_environment", lambda: "production")
    monkeypatch.setattr(
        deployment_worker,
        "container_worker_service_boundary_ready",
        lambda: True,
    )
    monkeypatch.setattr(deployment_worker, "deployment_heartbeat_lease_seconds", lambda: 300)
    monkeypatch.setattr(deployment_worker, "connect_queue", lambda: connection)
    monkeypatch.setattr(deployment_worker.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(deployment_worker.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(deployment_worker, "_touch_deployment_agent", lambda *_args: None)
    monkeypatch.setattr(deployment_worker, "_publish_worker_heartbeat_resilient", publish)
    monkeypatch.setattr(deployment_worker, "_clear_worker_heartbeat", lambda: None)
    monkeypatch.setattr(deployment_worker, "_replay_pending_final_statuses", lambda *_args: 0)
    monkeypatch.setattr(deployment_worker, "_recover_stale_jobs", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        deployment_worker,
        "_reconcile_stale_container_job",
        lambda active, _config, **_kwargs: (active, False),
    )
    monkeypatch.setattr(
        deployment_worker,
        "_claim_job",
        lambda *_args: pytest.fail("an unhealthy worker must not claim a deployment"),
    )
    monkeypatch.setattr(deployment_worker, "RUNNING", True)

    assert deployment_worker.main([]) == 1
    assert publish_calls == failed_publish_call
    assert connection.closed is True
