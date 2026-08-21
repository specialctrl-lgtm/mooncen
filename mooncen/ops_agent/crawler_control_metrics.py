"""Export bounded, low-cardinality crawler control-plane metrics.

The collector intentionally has no HTTP listener.  A systemd timer runs it
with a dedicated read-only database login and it atomically replaces one
node_exporter textfile.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg2
from psycopg2.extras import RealDictCursor

from DB.connection_settings import bounded_env_int, database_connect_options


CRAWLER_JOB_TYPES = ("crawler_run", "crawler_retry")
QUEUE_STATES = ("ready", "running", "dead_lettered")
BATCH_STATUSES = (
    "planning",
    "queued",
    "running",
    "finalizing",
    "success",
    "partial_success",
    "failed",
    "cancelled",
    "dead_lettered",
)
RELEASE_REPORT_STATUSES = (
    "pending",
    "downloading",
    "installing",
    "verifying",
    "ready",
    "failed",
    "rolled_back",
    "drifted",
    "missing",
)
OUTPUT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.prom")
MAX_TEXTFILE_BYTES = 128 * 1024


class CrawlerMetricsError(RuntimeError):
    pass


@dataclass(frozen=True)
class MetricsConfig:
    environment: str
    output_path: Path
    statement_timeout_ms: int
    lock_timeout_ms: int


@dataclass(frozen=True)
class MetricsSnapshot:
    queue_ready: int
    queue_running: int
    queue_dead_lettered: int
    oldest_ready_age_seconds: float
    expired_leases: int
    retries_scheduled: int
    retries_exhausted: int
    latest_batch_status: str | None
    latest_batch_age_seconds: float
    workers_fresh: int
    workers_stale: int
    release_reports: dict[str, int]
    generated_timestamp_seconds: float


def normalized_environment() -> str:
    value = os.getenv("ENVIRONMENT", "").strip().lower()
    aliases = {"prod": "production", "stage": "staging"}
    normalized = aliases.get(value, value)
    if normalized not in {"production", "staging"}:
        raise CrawlerMetricsError("ENVIRONMENT must be production or staging")
    return normalized


def metrics_database_config() -> dict[str, Any]:
    host = os.getenv("OPS_CRAWLER_SHARED_DB_HOST", "").strip()
    database = os.getenv("OPS_CRAWLER_SHARED_DB_NAME", "").strip()
    raw_port = os.getenv("OPS_CRAWLER_SHARED_DB_PORT", "").strip()
    user = os.getenv("OPS_CRAWLER_METRICS_DB_USER", "").strip()
    password = os.getenv("OPS_CRAWLER_METRICS_DB_PASSWORD", "")
    if not all((host, database, raw_port, user, password)):
        raise CrawlerMetricsError(
            "Explicit OPS_CRAWLER_SHARED_DB_* and OPS_CRAWLER_METRICS_DB_* settings are required"
        )
    port = bounded_env_int("OPS_CRAWLER_SHARED_DB_PORT", 5432, 1, 65_535)
    privileged_users = {
        os.getenv(name, "").strip()
        for name in (
            "OPS_CRAWLER_CONTROL_DB_USER",
            "OPS_CRAWLER_FINALIZER_DB_USER",
            "OPS_QUEUE_DB_USER",
            "CRAWL_STAGING_DB_USER",
        )
        if os.getenv(name, "").strip()
    }
    if user in privileged_users:
        raise CrawlerMetricsError("crawler metrics must use a distinct observer database login")
    return {
        "host": host,
        "port": port,
        "database": database,
        "user": user,
        "password": password,
        **database_connect_options(host, "mooncen-crawler-control-metrics"),
    }


def load_config(output_override: str | None = None) -> MetricsConfig:
    output = output_override or os.getenv(
        "OPS_CRAWLER_METRICS_OUTPUT",
        "/var/lib/mooncen-crawler-observer/mooncen_crawler_control.prom",
    )
    return MetricsConfig(
        environment=normalized_environment(),
        output_path=Path(output),
        statement_timeout_ms=bounded_env_int(
            "OPS_CRAWLER_METRICS_STATEMENT_TIMEOUT_MS", 5_000, 100, 30_000
        ),
        lock_timeout_ms=bounded_env_int(
            "OPS_CRAWLER_METRICS_LOCK_TIMEOUT_MS", 1_000, 100, 10_000
        ),
    )


def _require_observer_contract(cursor) -> None:
    cursor.execute(
        """
        SELECT (
            current_setting('transaction_read_only') = 'on'
            AND role.rolcanlogin
            AND role.rolinherit
            AND role.rolconnlimit BETWEEN 1 AND 4
            AND NOT role.rolsuper
            AND NOT role.rolcreaterole
            AND NOT role.rolcreatedb
            AND NOT role.rolbypassrls
            AND pg_has_role(current_user, 'mooncen_crawler_observer', 'member')
            AND NOT pg_has_role(current_user, 'mooncen_api', 'member')
            AND NOT pg_has_role(current_user, 'mooncen_crawler', 'member')
            AND NOT pg_has_role(current_user, 'mooncen_crawler_control', 'member')
            AND NOT pg_has_role(current_user, 'mooncen_crawler_worker', 'member')
            AND NOT pg_has_role(current_user, 'mooncen_applier', 'member')
            AND NOT pg_has_role(current_user, 'mooncen_ai', 'member')
            AND NOT pg_has_role(current_user, 'mooncen_check', 'member')
            AND NOT pg_has_role(current_user, 'mooncen_readonly', 'member')
            AND has_column_privilege(current_user, 'public.ops_jobs', 'status', 'SELECT')
            AND has_column_privilege(current_user, 'public.ops_agents', 'last_seen_at', 'SELECT')
            AND has_column_privilege(current_user, 'public.ops_crawler_batches', 'status', 'SELECT')
            AND has_column_privilege(
                current_user, 'public.ops_crawler_worker_desired_state', 'desired_status', 'SELECT'
            )
            AND has_column_privilege(
                current_user, 'public.ops_crawler_release_reports', 'status', 'SELECT'
            )
            AND has_column_privilege(
                current_user, 'public.ops_crawler_release_reports', 'agent_id', 'SELECT'
            )
            AND NOT has_column_privilege(current_user, 'public.ops_jobs', 'parameters', 'SELECT')
            AND NOT has_column_privilege(current_user, 'public.ops_jobs', 'result', 'SELECT')
            AND NOT has_column_privilege(current_user, 'public.ops_jobs', 'error_message', 'SELECT')
            AND NOT has_column_privilege(current_user, 'public.ops_agents', 'credential_hint', 'SELECT')
            AND NOT has_column_privilege(
                current_user, 'public.ops_crawler_release_reports', 'health', 'SELECT'
            )
            AND NOT has_column_privilege(
                current_user, 'public.ops_crawler_release_reports', 'error_message', 'SELECT'
            )
            AND NOT has_table_privilege(
                current_user, 'public.ops_jobs', 'INSERT,UPDATE,DELETE,TRUNCATE'
            )
            AND NOT has_table_privilege(
                current_user, 'public.ops_agents', 'INSERT,UPDATE,DELETE,TRUNCATE'
            )
            AND NOT has_table_privilege(
                current_user, 'public.ops_crawler_batches', 'INSERT,UPDATE,DELETE,TRUNCATE'
            )
            AND NOT has_table_privilege(
                current_user,
                'public.ops_crawler_worker_desired_state',
                'INSERT,UPDATE,DELETE,TRUNCATE'
            )
            AND NOT has_table_privilege(
                current_user,
                'public.ops_crawler_release_reports',
                'INSERT,UPDATE,DELETE,TRUNCATE'
            )
        ) AS observer_contract_ok
        FROM pg_roles AS role
        WHERE role.rolname = current_user
        """
    )
    row = cursor.fetchone()
    valid = row.get("observer_contract_ok") if isinstance(row, dict) else (row[0] if row else False)
    if valid is not True:
        raise CrawlerMetricsError("database login does not satisfy the crawler observer contract")


def _nonnegative_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise CrawlerMetricsError(f"{name} is invalid")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise CrawlerMetricsError(f"{name} is invalid") from exc
    if result < 0:
        raise CrawlerMetricsError(f"{name} is invalid")
    return result


def _nonnegative_float(value: Any, name: str) -> float:
    try:
        result = float(value or 0)
    except (TypeError, ValueError) as exc:
        raise CrawlerMetricsError(f"{name} is invalid") from exc
    if not math.isfinite(result) or result < 0:
        raise CrawlerMetricsError(f"{name} is invalid")
    return result


def collect_metrics(connection, config: MetricsConfig) -> MetricsSnapshot:
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute(
                """
                SELECT set_config('statement_timeout', %s, true),
                       set_config('lock_timeout', %s, true),
                       set_config('search_path', 'pg_catalog,public', true)
                """,
                (str(config.statement_timeout_ms), str(config.lock_timeout_ms)),
            )
            _require_observer_contract(cursor)

            cursor.execute(
                """
                SELECT
                    COUNT(*) FILTER (
                        WHERE status = 'queued'
                          AND available_at <= CURRENT_TIMESTAMP
                          AND cancel_requested_at IS NULL
                    ) AS queue_ready,
                    COUNT(*) FILTER (WHERE status IN ('assigned', 'running')) AS queue_running,
                    COUNT(*) FILTER (WHERE status = 'dead_lettered') AS queue_dead_lettered,
                    COALESCE(
                        GREATEST(EXTRACT(EPOCH FROM (
                            CURRENT_TIMESTAMP - MIN(available_at) FILTER (
                                WHERE status = 'queued'
                                  AND available_at <= CURRENT_TIMESTAMP
                                  AND cancel_requested_at IS NULL
                            )
                        )), 0),
                        0
                    ) AS oldest_ready_age_seconds,
                    COUNT(*) FILTER (
                        WHERE status IN ('assigned', 'running')
                          AND leased_until <= CURRENT_TIMESTAMP
                    ) AS expired_leases,
                    COUNT(*) FILTER (
                        WHERE status = 'queued' AND retry_count > 0
                    ) AS retries_scheduled,
                    COUNT(*) FILTER (
                        WHERE status = 'dead_lettered' AND retry_count >= max_retries
                    ) AS retries_exhausted
                FROM public.ops_jobs
                WHERE environment = %s
                  AND job_type = ANY(%s)
                  AND status IN ('queued', 'assigned', 'running', 'dead_lettered')
                """,
                (config.environment, list(CRAWLER_JOB_TYPES)),
            )
            queue = cursor.fetchone() or {}

            cursor.execute(
                """
                SELECT status,
                       GREATEST(EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - scheduled_slot)), 0)
                           AS batch_age_seconds
                FROM public.ops_crawler_batches
                WHERE environment = %s
                ORDER BY scheduled_slot DESC
                LIMIT 1
                """,
                (config.environment,),
            )
            batch = cursor.fetchone()

            cursor.execute(
                """
                SELECT
                    COUNT(*) FILTER (
                        WHERE agent.status = 'healthy'
                          AND agent.last_seen_at >= CURRENT_TIMESTAMP - INTERVAL '2 minutes'
                    ) AS workers_fresh,
                    COUNT(*) FILTER (
                        WHERE agent.id IS NULL
                           OR agent.status <> 'healthy'
                           OR agent.last_seen_at IS NULL
                           OR agent.last_seen_at < CURRENT_TIMESTAMP - INTERVAL '2 minutes'
                    ) AS workers_stale
                FROM public.ops_crawler_worker_desired_state AS desired
                LEFT JOIN LATERAL (
                    SELECT report.agent_id
                    FROM public.ops_crawler_release_reports AS report
                    WHERE report.environment = desired.environment
                      AND report.worker_key = desired.worker_key
                      AND report.desired_generation = desired.generation
                    ORDER BY report.reported_at DESC, report.created_at DESC
                    LIMIT 1
                ) AS reported ON true
                LEFT JOIN public.ops_agents AS agent
                  ON agent.id = COALESCE(desired.agent_id, reported.agent_id)
                 AND agent.environment = desired.environment
                WHERE desired.environment = %s
                  AND desired.desired_status = 'active'
                """,
                (config.environment,),
            )
            workers = cursor.fetchone() or {}

            cursor.execute(
                """
                WITH desired AS MATERIALIZED (
                    SELECT worker_key, generation
                    FROM public.ops_crawler_worker_desired_state
                    WHERE environment = %s
                      AND desired_status <> 'disabled'
                ), latest_report AS (
                    SELECT DISTINCT ON (desired.worker_key)
                           desired.worker_key,
                           report.status
                    FROM desired
                    LEFT JOIN public.ops_crawler_release_reports AS report
                      ON report.environment = %s
                     AND report.worker_key = desired.worker_key
                     AND report.desired_generation = desired.generation
                    ORDER BY desired.worker_key, report.reported_at DESC, report.created_at DESC
                )
                SELECT COALESCE(status, 'missing') AS status, COUNT(*) AS worker_count
                FROM latest_report
                GROUP BY COALESCE(status, 'missing')
                """,
                (config.environment, config.environment),
            )
            release_rows = cursor.fetchall()
        connection.commit()
    except Exception:
        connection.rollback()
        raise

    latest_status = None if not batch else str(batch.get("status") or "")
    if latest_status is not None and latest_status not in BATCH_STATUSES:
        raise CrawlerMetricsError("latest crawler batch status is outside the fixed metric contract")
    release_counts = dict.fromkeys(RELEASE_REPORT_STATUSES, 0)
    for row in release_rows:
        status_value = str(row.get("status") or "")
        if status_value not in release_counts:
            raise CrawlerMetricsError("release report status is outside the fixed metric contract")
        release_counts[status_value] = _nonnegative_int(row.get("worker_count"), "release worker count")

    return MetricsSnapshot(
        queue_ready=_nonnegative_int(queue.get("queue_ready"), "ready queue count"),
        queue_running=_nonnegative_int(queue.get("queue_running"), "running queue count"),
        queue_dead_lettered=_nonnegative_int(queue.get("queue_dead_lettered"), "dead queue count"),
        oldest_ready_age_seconds=_nonnegative_float(
            queue.get("oldest_ready_age_seconds"), "oldest ready age"
        ),
        expired_leases=_nonnegative_int(queue.get("expired_leases"), "expired lease count"),
        retries_scheduled=_nonnegative_int(queue.get("retries_scheduled"), "scheduled retry count"),
        retries_exhausted=_nonnegative_int(queue.get("retries_exhausted"), "exhausted retry count"),
        latest_batch_status=latest_status,
        latest_batch_age_seconds=_nonnegative_float(
            batch.get("batch_age_seconds") if batch else 0, "latest batch age"
        ),
        workers_fresh=_nonnegative_int(workers.get("workers_fresh"), "fresh worker count"),
        workers_stale=_nonnegative_int(workers.get("workers_stale"), "stale worker count"),
        release_reports=release_counts,
        generated_timestamp_seconds=datetime.now(timezone.utc).timestamp(),
    )


def _metric(lines: list[str], name: str, help_text: str, samples: list[tuple[str, Any]]) -> None:
    lines.extend((f"# HELP {name} {help_text}", f"# TYPE {name} gauge"))
    lines.extend(f"{name}{labels} {value}" for labels, value in samples)


def render_metrics(snapshot: MetricsSnapshot, environment: str) -> str:
    if environment not in {"production", "staging"}:
        raise CrawlerMetricsError("metric environment is outside the fixed label contract")
    env = f'environment="{environment}"'
    lines: list[str] = []
    queue_values = {
        "ready": snapshot.queue_ready,
        "running": snapshot.queue_running,
        "dead_lettered": snapshot.queue_dead_lettered,
    }
    _metric(
        lines,
        "mooncen_crawler_control_queue_jobs",
        "Current crawler jobs by fixed queue state.",
        [(f'{{{env},state="{state}"}}', queue_values[state]) for state in QUEUE_STATES],
    )
    _metric(
        lines,
        "mooncen_crawler_control_oldest_ready_age_seconds",
        "Age of the oldest claimable crawler job.",
        [(f"{{{env}}}", snapshot.oldest_ready_age_seconds)],
    )
    _metric(
        lines,
        "mooncen_crawler_control_expired_leases",
        "Crawler jobs whose active lease is already expired.",
        [(f"{{{env}}}", snapshot.expired_leases)],
    )
    _metric(
        lines,
        "mooncen_crawler_control_retry_jobs",
        "Crawler retry jobs by fixed retry state.",
        [
            (f'{{{env},state="scheduled"}}', snapshot.retries_scheduled),
            (f'{{{env},state="exhausted"}}', snapshot.retries_exhausted),
        ],
    )
    _metric(
        lines,
        "mooncen_crawler_control_latest_batch_outcome",
        "One-hot status of the latest scheduled crawler batch.",
        [
            (
                f'{{{env},status="{status_value}"}}',
                int(snapshot.latest_batch_status == status_value),
            )
            for status_value in BATCH_STATUSES
        ],
    )
    _metric(
        lines,
        "mooncen_crawler_control_latest_batch_present",
        "Whether a scheduled crawler batch exists.",
        [(f"{{{env}}}", int(snapshot.latest_batch_status is not None))],
    )
    _metric(
        lines,
        "mooncen_crawler_control_latest_batch_age_seconds",
        "Age of the latest scheduled crawler batch.",
        [(f"{{{env}}}", snapshot.latest_batch_age_seconds)],
    )
    _metric(
        lines,
        "mooncen_crawler_control_workers",
        "Desired active crawler workers by fixed heartbeat state.",
        [
            (f'{{{env},heartbeat="fresh"}}', snapshot.workers_fresh),
            (f'{{{env},heartbeat="stale"}}', snapshot.workers_stale),
        ],
    )
    _metric(
        lines,
        "mooncen_crawler_control_release_reports",
        "Latest release report for each non-disabled desired worker.",
        [
            (f'{{{env},status="{status_value}"}}', snapshot.release_reports[status_value])
            for status_value in RELEASE_REPORT_STATUSES
        ],
    )
    _metric(
        lines,
        "mooncen_crawler_control_collector_success",
        "Whether the last atomic crawler control collection succeeded.",
        [(f"{{{env}}}", 1)],
    )
    _metric(
        lines,
        "mooncen_crawler_control_generated_timestamp_seconds",
        "Unix timestamp of the last atomic crawler control collection.",
        [(f"{{{env}}}", snapshot.generated_timestamp_seconds)],
    )
    rendered = "\n".join(lines) + "\n"
    if len(rendered.encode("ascii")) > MAX_TEXTFILE_BYTES:
        raise CrawlerMetricsError("rendered crawler metrics exceed the textfile size limit")
    return rendered


def atomic_write_textfile(path: Path, contents: str) -> None:
    if not path.is_absolute() or not OUTPUT_NAME.fullmatch(path.name):
        raise CrawlerMetricsError("metrics output must be an absolute bounded .prom path")
    try:
        parent = path.parent.resolve(strict=True)
        parent_stat = parent.stat()
    except OSError as exc:
        raise CrawlerMetricsError("metrics output directory is unavailable") from exc
    if not stat.S_ISDIR(parent_stat.st_mode) or (
        os.name == "posix" and parent_stat.st_mode & stat.S_IWOTH
    ):
        raise CrawlerMetricsError("metrics output directory is unsafe")
    target = parent / path.name
    try:
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise CrawlerMetricsError("metrics output target is unsafe")
    except OSError as exc:
        raise CrawlerMetricsError("metrics output target is unavailable") from exc

    encoded = contents.encode("ascii")
    if not encoded or len(encoded) > MAX_TEXTFILE_BYTES:
        raise CrawlerMetricsError("metrics textfile size is invalid")
    temporary = parent / f".{path.name}.{uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, flags, 0o644)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        if os.name == "posix":
            directory_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except OSError as exc:
        raise CrawlerMetricsError("metrics textfile could not be replaced atomically") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export distributed crawler control-plane metrics")
    parser.add_argument("--output", help="absolute node_exporter .prom output path")
    args = parser.parse_args(argv)
    config = load_config(args.output)
    connection = psycopg2.connect(**metrics_database_config())
    try:
        snapshot = collect_metrics(connection, config)
    finally:
        connection.close()
    atomic_write_textfile(config.output_path, render_metrics(snapshot, config.environment))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
