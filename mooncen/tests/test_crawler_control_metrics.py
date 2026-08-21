from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from ops_agent import crawler_control_metrics as metrics


ROOT = Path(__file__).resolve().parents[1]


class FakeCursor:
    def __init__(self) -> None:
        self.current: Any = None
        self.executed: list[tuple[str, Any]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        normalized = " ".join(sql.split())
        self.executed.append((normalized, params))
        if "observer_contract_ok" in sql:
            self.current = {"observer_contract_ok": True}
        elif "AS queue_ready" in sql:
            self.current = {
                "queue_ready": 7,
                "queue_running": 2,
                "queue_dead_lettered": 1,
                "oldest_ready_age_seconds": 91.5,
                "expired_leases": 1,
                "retries_scheduled": 3,
                "retries_exhausted": 1,
            }
        elif "FROM public.ops_crawler_batches" in sql:
            self.current = {"status": "partial_success", "batch_age_seconds": 600.25}
        elif "AS workers_fresh" in sql:
            self.current = {"workers_fresh": 4, "workers_stale": 1}
        elif "WITH desired AS MATERIALIZED" in sql:
            self.current = [
                {"status": "ready", "worker_count": 3},
                {"status": "failed", "worker_count": 1},
                {"status": "missing", "worker_count": 1},
            ]
        else:
            self.current = {}

    def fetchone(self):
        if isinstance(self.current, list):
            return self.current[0] if self.current else None
        return self.current

    def fetchall(self):
        return list(self.current)


class FakeConnection:
    def __init__(self) -> None:
        self.cursor_value = FakeCursor()
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, **_kwargs):
        return self.cursor_value

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _config(output: Path) -> metrics.MetricsConfig:
    return metrics.MetricsConfig(
        environment="production",
        output_path=output,
        statement_timeout_ms=5_000,
        lock_timeout_ms=1_000,
    )


def test_metrics_database_requires_a_distinct_explicit_observer_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DB_SSLMODE", "verify-full")
    monkeypatch.setenv("OPS_CRAWLER_SHARED_DB_HOST", "staging-db")
    monkeypatch.setenv("OPS_CRAWLER_SHARED_DB_PORT", "5432")
    monkeypatch.setenv("OPS_CRAWLER_SHARED_DB_NAME", "mooncen_staging")
    monkeypatch.setenv("OPS_CRAWLER_METRICS_DB_USER", "metrics_login")
    monkeypatch.setenv("OPS_CRAWLER_METRICS_DB_PASSWORD", "secret")
    monkeypatch.setenv("OPS_CRAWLER_CONTROL_DB_USER", "control_login")

    config = metrics.metrics_database_config()

    assert config["user"] == "metrics_login"
    assert config["application_name"] == "mooncen-crawler-control-metrics"

    monkeypatch.setenv("OPS_CRAWLER_METRICS_DB_USER", "control_login")
    with pytest.raises(metrics.CrawlerMetricsError, match="distinct observer"):
        metrics.metrics_database_config()


def test_collect_metrics_uses_read_only_bounded_aggregate_queries(tmp_path: Path) -> None:
    connection = FakeConnection()

    snapshot = metrics.collect_metrics(connection, _config(tmp_path / "crawler.prom"))

    assert snapshot.queue_ready == 7
    assert snapshot.queue_running == 2
    assert snapshot.expired_leases == 1
    assert snapshot.latest_batch_status == "partial_success"
    assert snapshot.workers_fresh == 4
    assert snapshot.release_reports["ready"] == 3
    assert snapshot.release_reports["pending"] == 0
    assert connection.commits == 1
    assert connection.rollbacks == 0

    statements = "\n".join(sql for sql, _params in connection.cursor_value.executed)
    assert "SET TRANSACTION READ ONLY" in statements
    assert "set_config('statement_timeout'" in statements
    assert "ORDER BY scheduled_slot DESC LIMIT 1" in statements
    assert "WITH desired AS MATERIALIZED" in statements
    assert "LEFT JOIN LATERAL" in statements
    assert "parameters" not in statements.split("AS queue_ready", 1)[1]
    assert "report.health" not in statements.split("AS queue_ready", 1)[1]


def test_observer_contract_rejects_privileged_or_payload_capable_login(tmp_path: Path) -> None:
    connection = FakeConnection()
    original_execute = connection.cursor_value.execute

    def execute(sql: str, params: Any = None) -> None:
        original_execute(sql, params)
        if "observer_contract_ok" in sql:
            connection.cursor_value.current = {"observer_contract_ok": False}

    connection.cursor_value.execute = execute  # type: ignore[method-assign]

    with pytest.raises(metrics.CrawlerMetricsError, match="observer contract"):
        metrics.collect_metrics(connection, _config(tmp_path / "crawler.prom"))

    assert connection.commits == 0
    assert connection.rollbacks == 1
    contract = next(
        sql for sql, _params in connection.cursor_value.executed if "observer_contract_ok" in sql
    )
    assert "mooncen_crawler_observer" in contract
    assert "role.rolconnlimit BETWEEN 1 AND 4" in contract
    assert "NOT role.rolsuper" in contract
    assert "NOT has_column_privilege(current_user, 'public.ops_jobs', 'parameters', 'SELECT')" in contract
    assert (
        "NOT has_column_privilege( current_user, 'public.ops_crawler_release_reports', 'health', 'SELECT' )"
        in contract
    )
    assert "INSERT,UPDATE,DELETE,TRUNCATE" in contract


def test_rendered_metrics_have_only_fixed_low_cardinality_labels() -> None:
    snapshot = metrics.MetricsSnapshot(
        queue_ready=2,
        queue_running=1,
        queue_dead_lettered=0,
        oldest_ready_age_seconds=15.0,
        expired_leases=0,
        retries_scheduled=1,
        retries_exhausted=0,
        latest_batch_status="success",
        latest_batch_age_seconds=120.0,
        workers_fresh=3,
        workers_stale=0,
        release_reports=dict.fromkeys(metrics.RELEASE_REPORT_STATUSES, 0) | {"ready": 3},
        generated_timestamp_seconds=1_786_310_400.0,
    )

    rendered = metrics.render_metrics(snapshot, "production")

    assert 'state="ready"' in rendered
    assert 'status="success"} 1' in rendered
    assert 'heartbeat="fresh"} 3' in rendered
    assert 'status="missing"' in rendered
    assert "worker_key" not in rendered
    assert "provider" not in rendered
    assert "job_id" not in rendered
    assert "token" not in rendered
    assert "payload" not in rendered


def test_atomic_textfile_replace_preserves_old_file_on_unsafe_target(tmp_path: Path) -> None:
    output = tmp_path / "crawler.prom"
    output.write_text("old\n", encoding="ascii")

    metrics.atomic_write_textfile(output, "new\n")

    assert output.read_text(encoding="ascii") == "new\n"
    assert not list(tmp_path.glob(".*.tmp"))

    bad_output = tmp_path / "crawler.txt"
    with pytest.raises(metrics.CrawlerMetricsError, match=r"\.prom"):
        metrics.atomic_write_textfile(bad_output, "bad\n")
    assert not bad_output.exists()


@pytest.mark.skipif(os.name == "nt", reason="symlink creation is privilege-dependent on Windows")
def test_atomic_textfile_rejects_symlink_target(tmp_path: Path) -> None:
    victim = tmp_path / "victim"
    victim.write_text("keep\n", encoding="ascii")
    output = tmp_path / "crawler.prom"
    output.symlink_to(victim)

    with pytest.raises(metrics.CrawlerMetricsError, match="unsafe"):
        metrics.atomic_write_textfile(output, "overwrite\n")

    assert victim.read_text(encoding="ascii") == "keep\n"


def test_observer_provisioning_is_column_only_and_runtime_units_are_hardened() -> None:
    sql = (ROOT / "DB/provision_crawler_observer_login.sql").read_text(encoding="utf-8")
    service = (
        ROOT / "deploy/ubuntu/systemd/mooncen-crawler-control-metrics.service"
    ).read_text(encoding="utf-8")
    timer = (
        ROOT / "deploy/ubuntu/systemd/mooncen-crawler-control-metrics.timer"
    ).read_text(encoding="utf-8")

    grants = sql.split("GRANT USAGE ON SCHEMA public", 1)[1].split(
        "SELECT format('GRANT mooncen_crawler_observer", 1
    )[0]
    assert "GRANT SELECT (" in grants
    assert "GRANT SELECT ON TABLE" not in grants
    assert "parameters" not in grants
    assert "error_message" not in grants
    assert "health" not in grants
    assert "default_transaction_read_only = on" in sql
    assert "crawler_observer_password_verifier_b64" in sql
    assert "SCRAM-SHA-256" in sql
    assert "crawler_observer_password_b64" not in sql
    assert "REVOKE ALL PRIVILEGES ON" in sql
    assert "REVOKE mooncen_crawler_observer FROM %I" in sql
    assert "crawler_observer_contract_verified" in sql

    assert "User=mooncen-crawler-observer" in service
    assert "NoNewPrivileges=true" in service
    assert "ProtectSystem=strict" in service
    assert "CapabilityBoundingSet=" in service
    assert "StateDirectory=mooncen-crawler-observer" in service
    assert "ReadWritePaths=/var/lib/mooncen-crawler-observer" in service
    assert "ReadWritePaths=/var/lib/node_exporter/textfile_collector" not in service
    assert "OnCalendar=*-*-* *:*:00" in timer
    assert "Persistent=true" in timer
