from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path

import pytest

from ops_agent import crawler_control_scheduler as scheduler


def _config(tmp_path: Path) -> scheduler.ControlSchedulerConfig:
    manifest = tmp_path / "providers.yaml"
    manifest.write_text("version: 1\nproviders:\n  - HOMEPLUS\n  - EMART\n", encoding="utf-8")
    return scheduler.ControlSchedulerConfig(
        environment="production",
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


def test_latest_slot_is_kst_aligned_and_bounded(tmp_path: Path) -> None:
    config = _config(tmp_path)

    assert scheduler.latest_schedule_slot(
        config,
        datetime(2026, 8, 10, 13, 5, tzinfo=timezone.utc),
    ) == datetime(2026, 8, 10, 13, 0, tzinfo=timezone.utc)
    assert scheduler.latest_schedule_slot(
        config,
        datetime(2026, 8, 10, 21, 30, tzinfo=timezone.utc),
    ) is None


def test_manifest_revision_and_reviewed_provider_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = tmp_path / "providers.yaml"
    manifest.write_text("version: 1\nproviders:\n  - HOMEPLUS\n  - EMART\n", encoding="utf-8")
    monkeypatch.setattr(scheduler, "reviewed_crawler_providers", lambda _root: {"HOMEPLUS", "EMART"})

    providers, revision = scheduler.load_provider_manifest(manifest)

    assert providers == ("HOMEPLUS", "EMART")
    assert len(revision) == 64

    manifest.write_text("version: 1\nproviders:\n  - HOMEPLUS\n  - UNKNOWN\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="unreviewed"):
        scheduler.load_provider_manifest(manifest)


def test_manifest_expands_aggregate_owners_into_concrete_parallel_tasks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = tmp_path / "providers.yaml"
    manifest.write_text(
        "version: 1\nproviders:\n  - HOMEPLUS\n  - EXPERIENCE_TARGETS\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        scheduler,
        "reviewed_crawler_providers",
        lambda _root: {"HOMEPLUS", "EXPERIENCE_TARGETS", "MUNI_A", "MUNI_B"},
    )
    monkeypatch.setattr(
        scheduler,
        "build_course_provider_owners",
        lambda _providers: {
            "HOMEPLUS": "HOMEPLUS",
            "MUNI_B": "EXPERIENCE_TARGETS",
            "MUNI_A": "EXPERIENCE_TARGETS",
        },
    )

    providers, _revision, execution_owners = scheduler._load_provider_manifest_details(
        manifest
    )

    assert providers == ("HOMEPLUS", "MUNI_A", "MUNI_B")
    assert execution_owners == (
        ("HOMEPLUS", "HOMEPLUS"),
        ("MUNI_A", "EXPERIENCE_TARGETS"),
        ("MUNI_B", "EXPERIENCE_TARGETS"),
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

    def cursor(self):
        return self.cursor_value

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


def test_leader_enqueues_one_version_pinned_job_per_provider(tmp_path: Path) -> None:
    config = _config(tmp_path)
    connection = _Connection([(True,), None, ("batch",)])

    result = scheduler.enqueue_schedule_slot(
        connection,
        config,
        datetime(2026, 8, 10, 13, 0, tzinfo=timezone.utc),
    )

    assert result.reason == "enqueued"
    assert result.job_count == 2
    assert connection.commits == 1
    assert connection.rollbacks == 0
    sql = "\n".join(statement for statement, _params in connection.cursor_value.executed)
    assert "pg_try_advisory_xact_lock" in sql
    assert sql.count("INSERT INTO ops_jobs") == 2
    assert sql.count("INSERT INTO ops_crawler_batch_tasks") == 2
    assert "required_code_version, artifact_digest, config_revision" in sql
    job_params = [
        params
        for statement, params in connection.cursor_value.executed
        if "INSERT INTO ops_jobs" in statement
    ]
    assert all(params[3].startswith("crawler-provider:production:") for params in job_params)
    assert {params[6] for params in job_params} == {"release-42"}
    assert {params[7] for params in job_params} == {"a" * 64}
    assert {params[8] for params in job_params} == {"b" * 64}
    task_params = [
        params
        for statement, params in connection.cursor_value.executed
        if "INSERT INTO ops_crawler_batch_tasks" in statement
    ]
    assert {tuple(params[4]) for params in task_params} == {("HOMEPLUS",), ("EMART",)}
    assert "allow_provider_fanout" not in sql


def test_enqueued_concrete_task_pins_its_aggregate_execution_owner(tmp_path: Path) -> None:
    config = replace(
        _config(tmp_path),
        providers=("MUNI_A",),
        provider_execution_owners=(("MUNI_A", "EXPERIENCE_TARGETS"),),
    )
    connection = _Connection([(True,), None, ("batch",)])

    scheduler.enqueue_schedule_slot(
        connection,
        config,
        datetime(2026, 8, 10, 13, 0, tzinfo=timezone.utc),
    )

    job_insert = next(
        params
        for statement, params in connection.cursor_value.executed
        if "INSERT INTO ops_jobs" in statement
    )
    assert job_insert[4].adapted["provider"] == "MUNI_A"
    assert job_insert[4].adapted["execution_provider"] == "EXPERIENCE_TARGETS"
    active_query = next(
        params
        for statement, params in connection.cursor_value.executed
        if "status IN ('queued', 'assigned', 'running')" in statement
    )
    assert active_query[1] == ["EXPERIENCE_TARGETS", "MUNI_A"]


def test_scheduler_freezes_exact_non_overlapping_output_provider_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        scheduler,
        "build_course_provider_owners",
        lambda _providers: {
            "HOMEPLUS": "HOMEPLUS",
            "MUNI_A": "EXPERIENCE_TARGETS",
            "MUNI_B": "EXPERIENCE_TARGETS",
        },
    )

    allowlists = scheduler.provider_output_allowlists(
        ("HOMEPLUS", "EXPERIENCE_TARGETS")
    )

    assert allowlists == {
        "HOMEPLUS": ("HOMEPLUS",),
        "EXPERIENCE_TARGETS": ("MUNI_A", "MUNI_B"),
    }


def test_scheduler_does_not_split_a_new_batch_around_active_provider(tmp_path: Path) -> None:
    connection = _Connection([(True,), ("HOMEPLUS",)])

    result = scheduler.enqueue_schedule_slot(
        connection,
        _config(tmp_path),
        datetime(2026, 8, 10, 13, 0, tzinfo=timezone.utc),
    )

    assert result.reason == "previous_batch_active"
    assert not any("INSERT INTO ops_crawler_batches" in sql for sql, _params in connection.cursor_value.executed)
    assert connection.commits == 1


def test_central_scheduler_reaps_expired_leases_even_without_due_slot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection([])
    recovered: list[tuple[str, object]] = []
    monkeypatch.setattr(scheduler, "latest_schedule_slot", lambda _config: None)
    monkeypatch.setattr(scheduler, "control_database_config", lambda: {})
    monkeypatch.setattr(scheduler.psycopg2, "connect", lambda **_kwargs: connection)

    def _recover(_connection, worker_config, *, stale_after_seconds):
        recovered.append((worker_config.environment, stale_after_seconds))
        return 0

    monkeypatch.setattr(scheduler, "_recover_stale_jobs", _recover)

    assert scheduler.run_scheduler(_config(tmp_path), once=True) == 0
    assert recovered == [("production", 0)]
    assert connection.closed is True
