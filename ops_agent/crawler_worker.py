from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import queue
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from dataclasses import replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import Json, RealDictCursor

from Crawler.site_adapters import BRANCH_FILTER_PROVIDERS
from DB.connection_settings import bounded_env_int, database_connect_options
from ops_agent.crawler_registry import (
    CrawlerProviderRegistryError,
    resolve_crawler_provider_execution,
)
from ops_agent.crawler_outcome import ops_status_for_crawler_exit_code
from tools.ops_redaction import redact_text


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")
logger = logging.getLogger(__name__)
RUNNING = True
ACTIVE_PROCESS: subprocess.Popen[str] | None = None
SUPPORTED_JOB_TYPES = ("crawler_run", "crawler_retry", "agent_command")
CRAWLER_LOCK_CONTENTION_EXIT_CODE = 75
DEFAULT_LEASE_SECONDS = 60
CONTROL_CHECK_INTERVAL_SECONDS = 2.0
MAX_DISTRIBUTED_TASK_RESULT_BYTES = 1_048_576
WORKER_STATUS_INTERVAL_SECONDS = 30.0
WORKER_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
WORKER_HOSTNAME_PATTERN = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,62})(?:\.[a-z0-9](?:[a-z0-9-]{0,62}))*$"
)
CRAWLER_PROVIDER_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,99}$")
AGGREGATE_SCHEDULED_PROVIDERS = frozenset(
    {"EXPERIENCE_TARGETS", "MUNICIPAL_RESERVATION_TARGETS"}
)
DEDICATED_WORKER_DATABASE_ROLE = "mooncen_crawler_worker"
RETRY_BACKOFF_INITIAL_SECONDS = 15
RETRY_BACKOFF_MAX_SECONDS = 3_600
HEARTBEAT_OBSERVATION_INTERVAL_SECONDS = 30


def _retryable_crawler_outcome(return_code: int | None, final_status: str) -> bool:
    """Retry process-level collection failures, never contract rejections.

    This helper is used only after the reviewed child command completes. Job
    validation and result-envelope failures take separate fail-closed paths.
    """

    return return_code == CRAWLER_LOCK_CONTENTION_EXIT_CODE or final_status in {
        "failed",
        "partial_success",
    }


class CrawlerLeaseLost(RuntimeError):
    """Raised when the queue proves that this worker no longer owns the job."""


class CrawlerTaskResultError(RuntimeError):
    """Raised when a child cannot prove which fenced attempt produced its result."""


class JobLeaseRefresh(str, Enum):
    REFRESHED = "refreshed"
    OWNERSHIP_LOST = "ownership_lost"


class JobLeaseTracker:
    """Stop local work before a database partition can outlive its shared lease."""

    def __init__(self, lease_seconds: int, *, confirmed_at: float | None = None) -> None:
        self.lease_seconds = lease_seconds
        self.confirmed_at = time.monotonic() if confirmed_at is None else confirmed_at

    @property
    def local_deadline_seconds(self) -> float:
        safety_margin = max(5.0, min(30.0, self.lease_seconds * 0.2))
        return max(CONTROL_CHECK_INTERVAL_SECONDS, self.lease_seconds - safety_margin)

    def confirm(self, *, now: float | None = None) -> None:
        self.confirmed_at = time.monotonic() if now is None else now

    def expired(self, *, now: float | None = None) -> bool:
        checked_at = time.monotonic() if now is None else now
        return checked_at - self.confirmed_at >= self.local_deadline_seconds


@dataclass(frozen=True)
class WorkerConfig:
    environment: str
    agent_id: UUID | None
    poll_interval: float
    command_timeout: int
    lease_seconds: int = DEFAULT_LEASE_SECONDS
    code_version: str = ""
    artifact_digest: str = ""
    config_revision: str = ""
    worker_key: str = ""
    hostname: str = ""
    health_state_path: Path | None = None
    drain_state_path: Path | None = None
    max_concurrency: int = 5


def normalized_environment() -> str:
    value = os.getenv("ENVIRONMENT", "development").strip().lower()
    return {
        "prod": "production",
        "production": "production",
        "stage": "staging",
        "staging": "staging",
        "dev": "development",
        "development": "development",
        "test": "development",
    }.get(value, "development")


def configured_worker_hostname(environment: str) -> str:
    actual = socket.gethostname().strip().lower().rstrip(".")
    configured = os.getenv("OPS_CRAWLER_WORKER_HOSTNAME", "").strip().lower().rstrip(".")
    if environment in {"production", "staging"}:
        if (
            not configured
            or len(configured) > 253
            or not WORKER_HOSTNAME_PATTERN.fullmatch(configured)
        ):
            raise RuntimeError("OPS_CRAWLER_WORKER_HOSTNAME must be an explicit canonical hostname")
        if configured != actual:
            raise RuntimeError("OPS_CRAWLER_WORKER_HOSTNAME does not match the running host")
    return configured or actual


def validate_control_plane_colocation(environment: str) -> None:
    """Fail closed unless the queue and fenced staging writes share one DB."""

    if environment not in {"production", "staging"}:
        return
    if os.getenv("CRAWL_WRITE_MODE", "").strip().lower() != "staging":
        raise RuntimeError("Production/staging crawler workers require CRAWL_WRITE_MODE=staging")
    queue_names = (
        "OPS_QUEUE_DB_HOST",
        "OPS_QUEUE_DB_PORT",
        "OPS_QUEUE_DB_NAME",
    )
    staging_names = (
        "CRAWL_STAGING_DB_HOST",
        "CRAWL_STAGING_DB_PORT",
        "CRAWL_STAGING_DB_NAME",
    )
    shared_names = (
        "OPS_CRAWLER_SHARED_DB_HOST",
        "OPS_CRAWLER_SHARED_DB_PORT",
        "OPS_CRAWLER_SHARED_DB_NAME",
    )
    values: dict[str, str] = {}
    for name in (*queue_names, *staging_names, *shared_names):
        raw = os.getenv(name)
        if raw is None or not raw.strip():
            raise RuntimeError(
                "Production/staging crawler workers require explicit shared, queue, and staging DB host, port, and name"
            )
        values[name] = _bounded_text(raw, maximum=255, field=name)
        if raw != values[name]:
            raise RuntimeError(f"{name} must not contain surrounding whitespace")
    for name in ("OPS_QUEUE_DB_PORT", "CRAWL_STAGING_DB_PORT", "OPS_CRAWLER_SHARED_DB_PORT"):
        try:
            port = int(values[name])
        except ValueError as exc:
            raise RuntimeError(f"{name} must be a valid PostgreSQL port") from exc
        if not 1 <= port <= 65_535 or values[name] != str(port):
            raise RuntimeError(f"{name} must be a canonical PostgreSQL port")
    queue_identity = tuple(values[name] for name in queue_names)
    staging_identity = tuple(values[name] for name in staging_names)
    shared_identity = tuple(values[name] for name in shared_names)
    if queue_identity != staging_identity or queue_identity != shared_identity:
        raise RuntimeError(
            "Ops queue, crawler staging, and shared control database host, port, and name must match exactly"
        )
    queue_user = os.getenv("OPS_QUEUE_DB_USER", "").strip()
    queue_password = os.getenv("OPS_QUEUE_DB_PASSWORD", "")
    staging_user = os.getenv("CRAWL_STAGING_DB_USER", "").strip()
    staging_password = os.getenv("CRAWL_STAGING_DB_PASSWORD", "")
    if not queue_user or not queue_password or not staging_user or not staging_password:
        raise RuntimeError(
            "Production/staging crawler workers require explicit queue and staging credentials"
        )
    if queue_user != staging_user or queue_password != staging_password:
        raise RuntimeError(
            "Queue and staging writes must use the same dedicated crawler worker login"
        )


def queue_database_config() -> dict[str, Any]:
    environment = normalized_environment()
    if environment in {"production", "staging"}:
        host = os.getenv("OPS_QUEUE_DB_HOST", "").strip()
        port = bounded_env_int("OPS_QUEUE_DB_PORT", 0, 1, 65535)
        database = os.getenv("OPS_QUEUE_DB_NAME", "").strip()
        user = os.getenv("OPS_QUEUE_DB_USER", "").strip()
        password = os.getenv("OPS_QUEUE_DB_PASSWORD", "")
        if not host or not database or not user or not password:
            raise RuntimeError("Production Ops crawler worker requires explicit queue database credentials")
        owner = (
            os.getenv("DB_OWNER_USER", "").strip()
            or os.getenv("DB_MIGRATOR_USER", "").strip()
            or os.getenv("DB_USER", "").strip()
        )
        if owner and user == owner:
            raise RuntimeError("Ops crawler worker must not use the database owner role")
    else:
        host = os.getenv("OPS_QUEUE_DB_HOST", os.getenv("DB_HOST", "localhost")).strip()
        port = bounded_env_int("OPS_QUEUE_DB_PORT", bounded_env_int("DB_PORT", 5432, 1, 65535), 1, 65535)
        database = os.getenv("OPS_QUEUE_DB_NAME", os.getenv("DB_NAME", "mooncen")).strip()
        user = os.getenv("OPS_QUEUE_DB_USER", os.getenv("DB_CRAWLER_USER", "")).strip()
        password = os.getenv("OPS_QUEUE_DB_PASSWORD", os.getenv("DB_CRAWLER_PASSWORD", ""))
    return {
        "host": host,
        "port": port,
        "database": database,
        "user": user or os.getenv("DB_USER", "mooncen_crawler_login"),
        "password": password or os.getenv("DB_PASSWORD", ""),
        **database_connect_options(host, "mooncen-ops-crawler-worker"),
    }


def connect_queue():
    connection = psycopg2.connect(**queue_database_config())
    connection.autocommit = False
    return connection


def assert_dedicated_worker_database_role(connection, environment: str) -> None:
    if environment not in {"production", "staging"}:
        return
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_has_role(session_user, %s, 'member'), session_user::text",
            (DEDICATED_WORKER_DATABASE_ROLE,),
        )
        row = cursor.fetchone()
    if not row or row[0] is not True:
        login_name = str(row[1] if row and len(row) > 1 else "unknown")
        raise RuntimeError(
            f"Crawler worker login {login_name!r} is not a member of "
            f"{DEDICATED_WORKER_DATABASE_ROLE}"
        )


def _worker_status_path(name: str, default: str, environment: str) -> Path | None:
    raw = os.getenv(name, "").strip()
    if not raw and environment in {"production", "staging"}:
        raw = default
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        raise RuntimeError(f"{name} must be an absolute path")
    return path


def _atomic_worker_status(path: Path, payload: dict[str, Any]) -> None:
    parent = path.parent
    try:
        resolved_parent = parent.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("crawler worker status directory is unavailable") from exc
    if parent.is_symlink() or not resolved_parent.is_dir():
        raise RuntimeError("crawler worker status directory is unsafe")
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise RuntimeError("crawler worker status target is unsafe")
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    temporary = resolved_parent / f".{path.name}.{uuid4().hex}.new"
    try:
        # The capability-free release agent reads only this non-secret status
        # handoff through mooncen-crawler-status. The worker service's primary
        # runtime group pins the file group; explicit fchmod defeats UMask=0077
        # without making status writable by that group.
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            os.fchmod(handle.fileno(), 0o640)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            directory_fd = os.open(resolved_parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _publish_worker_health(config: WorkerConfig) -> None:
    if config.health_state_path is None:
        return
    _atomic_worker_status(
        config.health_state_path,
        {
            "schema_version": 1,
            "worker_id": config.worker_key,
            "healthy": True,
            "code_version": config.code_version,
            "artifact_digest": config.artifact_digest,
            "config_revision": config.config_revision,
            "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
    )


def _heartbeat_registered_agent(connection, config: WorkerConfig) -> None:
    if config.environment not in {"production", "staging"}:
        return
    if config.agent_id is None or not config.hostname:
        raise CrawlerLeaseLost("crawler worker has no enrolled agent identity")
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE ops_agents
            SET status = 'healthy',
                version = %s,
                last_seen_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
              AND environment = %s
              AND hostname = %s
              AND credential_hint = 'crawler-worker:' || session_user
              AND maintenance_mode IS FALSE
              AND status IN ('unknown', 'healthy', 'warning')
            RETURNING id
            """,
            (
                config.code_version,
                str(config.agent_id),
                config.environment,
                config.hostname,
            ),
        )
        row = cursor.fetchone()
    if not row or str(row[0]) != str(config.agent_id):
        connection.rollback()
        raise CrawlerLeaseLost("crawler worker agent enrollment is missing, disabled, or mismatched")
    connection.commit()


def _load_worker_desired_state(connection, config: WorkerConfig) -> dict[str, Any] | None:
    if not config.worker_key:
        return None
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute(
            """
            SELECT rollout_id::text, generation, desired_status,
                   code_version, artifact_digest, config_revision
            FROM ops_crawler_worker_desired_state
            WHERE environment = %s
              AND worker_key = %s
              AND not_before <= CURRENT_TIMESTAMP
              AND (agent_id IS NULL OR agent_id = %s)
            """,
            (config.environment, config.worker_key, str(config.agent_id)),
        )
        row = cursor.fetchone()
    connection.commit()
    if row is None and config.environment in {"production", "staging"}:
        raise RuntimeError("crawler worker has no matching central desired state")
    desired = dict(row) if row else None
    if desired is not None and config.environment in {"production", "staging"}:
        expected_identity = (
            str(desired.get("code_version") or ""),
            str(desired.get("artifact_digest") or "").lower(),
            str(desired.get("config_revision") or ""),
        )
        running_identity = (
            config.code_version,
            config.artifact_digest.lower(),
            config.config_revision,
        )
        if expected_identity != running_identity:
            raise RuntimeError(
                "crawler worker release identity differs from central desired state"
            )
    return desired


def _publish_worker_drain(config: WorkerConfig, desired: dict[str, Any]) -> None:
    if config.drain_state_path is None:
        raise RuntimeError("crawler drain state path is not configured")
    _atomic_worker_status(
        config.drain_state_path,
        {
            "schema_version": 1,
            "worker_id": config.worker_key,
            "rollout_id": str(desired["rollout_id"]),
            "generation": int(desired["generation"]),
            "drained": True,
            "active_jobs": 0,
            "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
    )


def resolve_local_agent_id(connection, environment: str) -> UUID | None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id
            FROM ops_agents
            WHERE environment = %s
              AND hostname = %s
              AND status = 'healthy'
              AND last_seen_at >= NOW() - INTERVAL '2 minutes'
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (environment, socket.gethostname()),
        )
        row = cursor.fetchone()
    connection.commit()
    return UUID(str(row[0])) if row else None


def _bounded_text(value: Any, *, maximum: int, field: str) -> str:
    cleaned = str(value or "").strip()
    if len(cleaned) > maximum or "\x00" in cleaned:
        raise ValueError(f"{field} is invalid")
    return cleaned


def worker_compatibility_from_environment(environment: str) -> tuple[str, str, str]:
    code_version = _bounded_text(
        os.getenv("OPS_CRAWLER_CODE_VERSION"),
        maximum=200,
        field="OPS_CRAWLER_CODE_VERSION",
    )
    artifact_digest = _bounded_text(
        os.getenv("OPS_CRAWLER_ARTIFACT_DIGEST"),
        maximum=200,
        field="OPS_CRAWLER_ARTIFACT_DIGEST",
    ).lower()
    config_revision = _bounded_text(
        os.getenv("OPS_CRAWLER_CONFIG_REVISION"),
        maximum=200,
        field="OPS_CRAWLER_CONFIG_REVISION",
    )
    if environment in {"production", "staging"} and not all(
        (code_version, artifact_digest, config_revision)
    ):
        raise RuntimeError(
            "Production crawler worker requires OPS_CRAWLER_CODE_VERSION, "
            "OPS_CRAWLER_ARTIFACT_DIGEST, and OPS_CRAWLER_CONFIG_REVISION"
        )
    return code_version, artifact_digest, config_revision


def _compatibility_predicate(config: WorkerConfig) -> tuple[str, list[str]]:
    values = [config.code_version, config.artifact_digest, config.config_revision]
    if config.environment in {"production", "staging"}:
        return (
            """
              AND required_code_version = %s
              AND artifact_digest = %s
              AND config_revision = %s
            """,
            values,
        )
    return (
        """
          AND (NULLIF(BTRIM(required_code_version), '') IS NULL OR required_code_version = %s)
          AND (NULLIF(BTRIM(artifact_digest), '') IS NULL OR artifact_digest = %s)
          AND (NULLIF(BTRIM(config_revision), '') IS NULL OR config_revision = %s)
        """,
        values,
    )


def _retry_backoff_seconds(retry_count: int) -> int:
    exponent = max(0, min(int(retry_count), 20))
    return min(RETRY_BACKOFF_MAX_SECONDS, RETRY_BACKOFF_INITIAL_SECONDS * (2**exponent))


def _lease_identity(job: dict[str, Any]) -> tuple[str, str, int, str]:
    job_id = str(job.get("id") or "")
    lease_token = str(job.get("lease_token") or "")
    lease_epoch = int(job.get("lease_epoch") or 0)
    agent_id = str(job.get("agent_id") or "")
    if not job_id or not lease_token or lease_epoch <= 0 or not agent_id:
        raise CrawlerLeaseLost("crawler job has no valid lease identity")
    return job_id, lease_token, lease_epoch, agent_id


def _attempt_identity(job: dict[str, Any]) -> tuple[str, str, int, int]:
    job_id, _lease_token, lease_epoch, _agent_id = _lease_identity(job)
    attempt_id = str(job.get("attempt_id") or "")
    attempt_no = int(job.get("attempt_no") or 0)
    try:
        UUID(attempt_id)
    except (ValueError, AttributeError) as exc:
        raise CrawlerLeaseLost("crawler job has no valid attempt identity") from exc
    if attempt_no <= 0:
        raise CrawlerLeaseLost("crawler job has no valid attempt identity")
    return attempt_id, job_id, attempt_no, lease_epoch


def _insert_attempt_observation(
    cursor,
    job: dict[str, Any],
    observation_kind: str,
    payload: dict[str, Any],
    *,
    sample_interval_seconds: int | None = None,
) -> None:
    attempt_id, job_id, attempt_no, lease_epoch = _attempt_identity(job)
    if sample_interval_seconds is None:
        cursor.execute(
            """
            INSERT INTO ops_crawler_task_observations (
                attempt_id, job_id, attempt_no, lease_epoch,
                observation_kind, observed_at, payload
            )
            VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP, %s)
            """,
            (
                attempt_id,
                job_id,
                attempt_no,
                lease_epoch,
                observation_kind,
                Json(payload),
            ),
        )
        return
    cursor.execute(
        """
        INSERT INTO ops_crawler_task_observations (
            attempt_id, job_id, attempt_no, lease_epoch,
            observation_kind, observed_at, payload
        )
        SELECT %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, %s
        WHERE NOT EXISTS (
            SELECT 1
            FROM ops_crawler_task_observations
            WHERE attempt_id = %s
              AND job_id = %s
              AND attempt_no = %s
              AND lease_epoch = %s
              AND observation_kind = %s
              AND observed_at >= CURRENT_TIMESTAMP - make_interval(secs => %s)
        )
        """,
        (
            attempt_id,
            job_id,
            attempt_no,
            lease_epoch,
            observation_kind,
            Json(payload),
            attempt_id,
            job_id,
            attempt_no,
            lease_epoch,
            observation_kind,
            sample_interval_seconds,
        ),
    )


def _canonical_uuid(value: Any, *, field: str) -> str:
    text = _bounded_text(value, maximum=36, field=field)
    try:
        canonical = str(UUID(text))
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"{field} must be a canonical UUID") from exc
    if text != canonical:
        raise ValueError(f"{field} must be a canonical UUID")
    return canonical


def _canonical_provider_list(
    value: Any,
    *,
    field: str,
    maximum: int,
) -> tuple[str, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= maximum:
        raise ValueError(f"{field} must be a bounded provider list")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        provider = str(item or "")
        if not CRAWLER_PROVIDER_PATTERN.fullmatch(provider) or provider in seen:
            raise ValueError(f"{field} contains an invalid or duplicate provider")
        normalized.append(provider)
        seen.add(provider)
    return tuple(normalized)


def build_crawler_execution(
    parameters: dict[str, Any],
    *,
    max_concurrency: int = 5,
) -> tuple[list[str], dict[str, str]]:
    """Build the only process template and environment this worker may execute."""

    if not isinstance(parameters, dict):
        raise ValueError("job parameters must be an object")
    if (
        not isinstance(max_concurrency, int)
        or isinstance(max_concurrency, bool)
        or not 1 <= max_concurrency <= 5
    ):
        raise ValueError("worker concurrency limit is out of bounds")
    scope = _bounded_text(parameters.get("scope"), maximum=30, field="scope") or "provider"
    run_mode = _bounded_text(parameters.get("run_mode"), maximum=20, field="run_mode") or "apply"
    if run_mode != "apply":
        raise ValueError("dry_run/review requires the staging review worker, which is not configured")
    if scope not in {"provider", "branch"}:
        raise ValueError("this worker supports only provider and branch crawler scopes")

    provider = _bounded_text(parameters.get("provider"), maximum=100, field="provider").upper()
    requested_execution_provider = _bounded_text(
        parameters.get("execution_provider"),
        maximum=100,
        field="execution_provider",
    ).upper()
    try:
        execution = resolve_crawler_provider_execution(
            provider,
            PROJECT_ROOT,
            scheduled_provider=requested_execution_provider or None,
        )
    except CrawlerProviderRegistryError as exc:
        raise ValueError("provider is not in the reviewed crawler registry") from exc

    command = [
        sys.executable,
        str(PROJECT_ROOT / "run_crawlers.py"),
        "--providers",
        execution.scheduled_provider,
        "--once",
        "--ignore-active-window",
        "--skip-coordinate-backfill",
        "--skip-category-backfill",
    ]
    branch = _bounded_text(parameters.get("branch"), maximum=160, field="branch")
    if branch:
        if provider not in BRANCH_FILTER_PROVIDERS:
            raise ValueError("provider does not support reviewed branch filtering")
        command.extend(["--branch-name", branch])
    concurrency = parameters.get("concurrency", 1)
    if not isinstance(concurrency, int) or isinstance(concurrency, bool) or not 1 <= concurrency <= 5:
        raise ValueError("concurrency is out of bounds")
    if concurrency > max_concurrency:
        raise ValueError("concurrency exceeds this worker's reviewed limit")
    if concurrency > 1:
        command.extend(["--parallel", "--max-workers", str(concurrency)])
    process_environment = dict(execution.environment)
    has_central_scope = (
        "allowed_output_providers" in parameters
        or "scheduled_providers" in parameters
    )
    if has_central_scope:
        allowed_output_providers = _canonical_provider_list(
            parameters.get("allowed_output_providers"),
            field="allowed_output_providers",
            maximum=4096,
        )
        scheduled_providers = _canonical_provider_list(
            parameters.get("scheduled_providers"),
            field="scheduled_providers",
            maximum=512,
        )
        if provider not in scheduled_providers:
            raise ValueError("central crawler job is outside its frozen task schedule")
        if requested_execution_provider:
            if execution.scheduled_provider != requested_execution_provider:
                raise ValueError("central crawler job execution owner is invalid")
        elif execution.scheduled_provider != provider:
            raise ValueError("central crawler alias jobs require an explicit execution owner")
        process_environment["CRAWL_ALLOWED_OUTPUT_PROVIDERS_JSON"] = json.dumps(
            list(allowed_output_providers),
            separators=(",", ":"),
        )
        process_environment["CRAWL_SCHEDULED_TASK_PROVIDER"] = execution.scheduled_provider
        if provider == execution.scheduled_provider and provider in AGGREGATE_SCHEDULED_PROVIDERS:
            process_environment["CRAWLER_PROVIDERS"] = " ".join(scheduled_providers)
    elif requested_execution_provider:
        raise ValueError("execution_provider is reserved for centrally scoped crawler jobs")
    return command, process_environment


def build_crawler_command(parameters: dict[str, Any]) -> list[str]:
    return build_crawler_execution(parameters)[0]


def _task_result_path(job: dict[str, Any]) -> Path:
    job_id, lease_token, _lease_epoch, _agent_id = _lease_identity(job)
    configured = os.getenv("OPS_CRAWLER_TASK_RESULT_DIR", "").strip()
    if not configured:
        if normalized_environment() in {"production", "staging"}:
            raise CrawlerTaskResultError("OPS_CRAWLER_TASK_RESULT_DIR is required")
        configured = str(PROJECT_ROOT / "logs" / "ops_task_results")
    directory = Path(configured)
    if not directory.is_absolute():
        raise CrawlerTaskResultError("crawler task result directory must be absolute")
    if not directory.exists() and normalized_environment() == "development":
        directory.mkdir(parents=True, mode=0o700)
    try:
        resolved = directory.resolve(strict=True)
    except OSError as exc:
        raise CrawlerTaskResultError("crawler task result directory is unavailable") from exc
    if directory.is_symlink() or not resolved.is_dir():
        raise CrawlerTaskResultError("crawler task result directory is unsafe")
    destination = resolved / f"{job_id}-{lease_token}.json"
    if destination.exists() or destination.is_symlink():
        raise CrawlerTaskResultError("crawler task result path already exists")
    return destination


def _normalized_task_result(result: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    parameters = job.get("parameters") or {}
    provider = str(parameters.get("provider") or "").strip().upper()
    execution_provider = str(parameters.get("execution_provider") or provider).strip().upper()
    if result.get("providers_requested") != [execution_provider]:
        raise CrawlerTaskResultError("crawler task result execution provider does not match the job")
    if execution_provider == provider:
        return result

    try:
        allowed = _canonical_provider_list(
            parameters.get("allowed_output_providers"),
            field="allowed_output_providers",
            maximum=4096,
        )
    except ValueError as exc:
        raise CrawlerTaskResultError("crawler alias task has no exact output scope") from exc
    if allowed != (provider,) or execution_provider not in AGGREGATE_SCHEDULED_PROVIDERS:
        raise CrawlerTaskResultError("crawler alias task output scope is not one concrete provider")

    provider_results = result.get("provider_results")
    owners = result.get("course_provider_owners")
    concrete_results = result.get("concrete_provider_results")
    if (
        not isinstance(provider_results, list)
        or len(provider_results) != 1
        or not isinstance(provider_results[0], dict)
        or str(provider_results[0].get("provider") or "").strip().upper() != execution_provider
        or not isinstance(owners, dict)
        or {
            str(concrete).strip().upper(): str(owner).strip().upper()
            for concrete, owner in owners.items()
        }
        != {provider: execution_provider}
        or not isinstance(concrete_results, list)
        or len(concrete_results) != 1
        or not isinstance(concrete_results[0], dict)
        or str(concrete_results[0].get("provider") or "").strip().upper() != provider
        or str(concrete_results[0].get("scheduled_owner") or "").strip().upper()
        != execution_provider
    ):
        raise CrawlerTaskResultError("crawler alias result does not prove its exact concrete provider")

    normalized = copy.deepcopy(result)
    normalized["providers_requested"] = [provider]
    normalized["provider_results"][0]["provider"] = provider
    normalized["course_provider_owners"] = {provider: provider}
    normalized["concrete_provider_results"] = []
    normalized["concrete_providers_completed"] = 0
    normalized["concrete_providers_failed"] = 0
    normalized["concrete_providers_total"] = 0
    normalized["failed_providers"] = [
        provider if str(item).strip().upper() == execution_provider else item
        for item in normalized.get("failed_providers", [])
    ]
    # A provider selected through an aggregate exclusion scope must never
    # authorize provider-wide stale closure on its own.
    normalized["close_missing_enabled"] = False
    return normalized


def _load_task_result(path: Path, job: dict[str, Any], batch_id: str) -> dict[str, Any]:
    try:
        metadata = path.lstat()
        if path.is_symlink() or not path.is_file() or metadata.st_size > MAX_DISTRIBUTED_TASK_RESULT_BYTES:
            raise CrawlerTaskResultError("crawler task result file is unsafe")
        encoded = path.read_bytes()
    except FileNotFoundError as exc:
        raise CrawlerTaskResultError("crawler task did not publish a result") from exc
    if not encoded or len(encoded) > MAX_DISTRIBUTED_TASK_RESULT_BYTES:
        raise CrawlerTaskResultError("crawler task result size is invalid")
    try:
        payload = json.loads(encoded.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CrawlerTaskResultError("crawler task result is invalid JSON") from exc
    if type(payload) is not dict or set(payload) != {
        "schema_version",
        "job_id",
        "lease_token",
        "lease_epoch",
        "attempt_no",
        "batch_id",
        "result",
    }:
        raise CrawlerTaskResultError("crawler task result contract is invalid")
    job_id, lease_token, lease_epoch, _agent_id = _lease_identity(job)
    if (
        payload.get("schema_version") != 1
        or payload.get("job_id") != job_id
        or payload.get("lease_token") != lease_token
        or payload.get("lease_epoch") != lease_epoch
        or payload.get("attempt_no") != int(job.get("attempt_no") or 0)
        or payload.get("batch_id") != batch_id
        or type(payload.get("result")) is not dict
    ):
        raise CrawlerTaskResultError("crawler task result fence identity does not match")
    return _normalized_task_result(payload["result"], job)


def _insert_log(
    cursor,
    job_id: str,
    level: str,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    safe_message = redact_text(message, maximum=4_000)
    cursor.execute(
        """
        INSERT INTO ops_job_logs (job_id, log_level, message, metadata)
        VALUES (%s, %s, %s, %s)
        """,
        (job_id, level, safe_message, Json(metadata or {})),
    )


def _append_log(connection, job_id: str, level: str, message: str, metadata: dict[str, Any] | None = None) -> None:
    with connection.cursor() as cursor:
        _insert_log(cursor, job_id, level, message, metadata)
    connection.commit()


def _recover_stale_jobs(
    connection,
    config: WorkerConfig,
    *,
    stale_after_seconds: int,
) -> int:
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute(
            """
            WITH stale AS (
                SELECT id, retry_count, max_retries, lease_token, lease_epoch,
                       cancel_requested_at IS NOT NULL AS was_cancelled,
                       (
                           cancel_requested_at IS NULL
                           AND retry_count < max_retries
                       ) AS should_retry
                FROM ops_jobs
                WHERE status IN ('assigned', 'running')
                  AND environment = %s
                  AND job_type = ANY(%s)
                  AND leased_until <= CURRENT_TIMESTAMP
                FOR UPDATE SKIP LOCKED
            )
            UPDATE ops_jobs AS job
            SET status = CASE
                    WHEN stale.was_cancelled THEN 'cancelled'
                    WHEN stale.should_retry THEN 'queued'
                    ELSE 'dead_lettered'
                END,
                progress = CASE
                    WHEN stale.should_retry THEN 0
                    ELSE job.progress
                END,
                retry_count = CASE
                    WHEN stale.should_retry THEN stale.retry_count + 1
                    ELSE stale.retry_count
                END,
                available_at = CASE
                    WHEN stale.should_retry THEN
                        CURRENT_TIMESTAMP + make_interval(
                            secs => LEAST(
                                %s,
                                %s * (2 ^ LEAST(stale.retry_count, 20))
                            )::integer
                        )
                    ELSE job.available_at
                END,
                error_code = CASE
                    WHEN stale.was_cancelled THEN 'cancelled_after_lease_expiry'
                    WHEN stale.should_retry
                        THEN 'worker_lease_expired_retry'
                    ELSE 'worker_lease_expired'
                END,
                error_message = CASE
                    WHEN stale.was_cancelled
                        THEN 'Crawler job cancellation was finalized after lease expiry.'
                    ELSE 'Crawler worker lease expired before completion.'
                END,
                agent_id = CASE
                    WHEN stale.should_retry THEN NULL
                    ELSE job.agent_id
                END,
                lease_token = NULL,
                leased_until = NULL,
                assigned_at = CASE
                    WHEN stale.should_retry THEN NULL
                    ELSE job.assigned_at
                END,
                started_at = CASE
                    WHEN stale.should_retry THEN NULL
                    ELSE job.started_at
                END,
                heartbeat_at = CASE
                    WHEN stale.should_retry THEN NULL
                    ELSE CURRENT_TIMESTAMP
                END,
                finished_at = CASE
                    WHEN stale.should_retry THEN NULL
                    ELSE CURRENT_TIMESTAMP
                END,
                updated_at = CURRENT_TIMESTAMP
            FROM stale
            WHERE job.id = stale.id
            RETURNING job.id::text, job.parameters, job.status,
                      job.retry_count, job.available_at,
                      stale.lease_token::text AS expired_lease_token,
                      stale.lease_epoch AS expired_lease_epoch
            """,
            (
                config.environment,
                list(SUPPORTED_JOB_TYPES),
                RETRY_BACKOFF_MAX_SECONDS,
                RETRY_BACKOFF_INITIAL_SECONDS,
            ),
        )
        recovered = [dict(row) for row in cursor.fetchall()]
        for job in recovered:
            requeued = job["status"] == "queued"
            cancelled = job["status"] == "cancelled"
            cursor.execute(
                """
                UPDATE ops_crawler_task_attempts
                SET status = 'lease_lost',
                    finished_at = CURRENT_TIMESTAMP,
                    error_code = 'worker_lease_expired',
                    error_message = 'Crawler worker lease expired before completion.'
                WHERE job_id = %s
                  AND lease_token = %s
                  AND lease_epoch = %s
                  AND status = 'running'
                """,
                (
                    job["id"],
                    job["expired_lease_token"],
                    job["expired_lease_epoch"],
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise RuntimeError("expired crawler lease has no matching running attempt")
            cursor.execute(
                """
                INSERT INTO ops_crawler_task_observations (
                    attempt_id, job_id, attempt_no, lease_epoch,
                    observation_kind, observed_at, payload
                )
                SELECT id, job_id, attempt_no, lease_epoch,
                       'lease_lost', CURRENT_TIMESTAMP, %s
                FROM ops_crawler_task_attempts
                WHERE job_id = %s
                  AND lease_token = %s
                  AND lease_epoch = %s
                  AND status = 'lease_lost'
                """,
                (
                    Json(
                        {
                            "job_status": job["status"],
                            "retry_count": job["retry_count"],
                        }
                    ),
                    job["id"],
                    job["expired_lease_token"],
                    job["expired_lease_epoch"],
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise RuntimeError("expired crawler lease observation was not recorded")
            cursor.execute(
                """
                UPDATE ops_crawler_runs
                SET status = %s,
                    current_stage = %s,
                    finished_at = CASE WHEN %s THEN NULL ELSE CURRENT_TIMESTAMP END
                WHERE job_id = %s
                  AND status IN ('queued', 'running', 'stopping')
                """,
                (
                    "queued" if requeued else "cancelled" if cancelled else "failed",
                    ("retry_scheduled" if requeued else "cancelled" if cancelled else "worker_lease_expired"),
                    requeued,
                    job["id"],
                ),
            )
    connection.commit()

    for job in recovered:
        _append_log(
            connection,
            job["id"],
            "error",
            "이전 크롤러 워커의 lease가 만료되어 기존 attempt를 fence 처리했습니다.",
            {
                "reason": "worker_lease_expired",
                "stale_after_seconds": stale_after_seconds,
                "disposition": job["status"],
                "retry_count": job["retry_count"],
            },
        )
    if recovered:
        logger.warning("Recovered %s stale crawler job(s).", len(recovered))
    return len(recovered)


def _claim_release_generation(
    config: WorkerConfig,
    desired: dict[str, Any] | None,
) -> tuple[str | None, int | None]:
    if config.environment not in {"production", "staging"}:
        return None, None
    if desired is None or desired.get("desired_status") != "active":
        raise RuntimeError("crawler worker claim requires active central desired state")
    try:
        rollout_id = _canonical_uuid(desired.get("rollout_id"), field="desired rollout_id")
        generation = int(desired.get("generation") or 0)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("crawler worker desired release generation is invalid") from exc
    if generation <= 0:
        raise RuntimeError("crawler worker desired release generation is invalid")
    desired_identity = (
        str(desired.get("code_version") or ""),
        str(desired.get("artifact_digest") or "").lower(),
        str(desired.get("config_revision") or ""),
    )
    running_identity = (
        config.code_version,
        config.artifact_digest.lower(),
        config.config_revision,
    )
    if desired_identity != running_identity:
        raise RuntimeError("crawler worker claim differs from central desired release")
    return rollout_id, generation


def _claim_job(
    connection,
    config: WorkerConfig,
    desired: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if config.agent_id is None:
        return None
    rollout_id, release_generation = _claim_release_generation(config, desired)
    compatibility_clause, compatibility_params = _compatibility_predicate(config)
    params: list[Any] = [
        config.environment,
        list(SUPPORTED_JOB_TYPES),
        str(config.agent_id),
        str(config.agent_id),
        *compatibility_params,
    ]
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute(
            f"""
            SELECT id::text, job_type, status, environment, parameters,
                   target_key, retry_count, max_retries, attempt_no,
                   required_code_version, artifact_digest, config_revision
            FROM ops_jobs
            WHERE status = 'queued'
              AND available_at <= CURRENT_TIMESTAMP
              AND cancel_requested_at IS NULL
              AND environment = %s
              AND job_type = ANY(%s)
              AND (agent_id IS NULL OR agent_id = %s)
              AND (job_type <> 'agent_command' OR agent_id = %s)
              {compatibility_clause}
            ORDER BY available_at, queued_at, created_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """,
            params,
        )
        row = cursor.fetchone()
        if not row:
            connection.commit()
            return None
        lease_token = uuid4()
        cursor.execute(
            """
            UPDATE ops_jobs
            SET status = 'assigned',
                agent_id = %s,
                lease_token = %s,
                lease_epoch = lease_epoch + 1,
                leased_until = CURRENT_TIMESTAMP + make_interval(secs => %s),
                attempt_no = attempt_no + 1,
                assigned_at = CURRENT_TIMESTAMP,
                heartbeat_at = CURRENT_TIMESTAMP,
                progress = 1,
                error_code = NULL,
                error_message = NULL,
                finished_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND status = 'queued'
            RETURNING id, status, agent_id::text, lease_token::text,
                      lease_epoch, leased_until, attempt_no
            """,
            (
                str(config.agent_id),
                str(lease_token),
                config.lease_seconds,
                row["id"],
            ),
        )
        lease = cursor.fetchone()
        if lease is None:
            connection.rollback()
            return None
        cursor.execute(
            """
            INSERT INTO ops_crawler_task_attempts (
                job_id, attempt_no, lease_epoch, lease_token, agent_id,
                status, worker_code_version, artifact_digest, config_revision,
                rollout_id, release_generation
            )
            VALUES (%s, %s, %s, %s, %s, 'running', %s, %s, %s, %s, %s)
            RETURNING id::text, rollout_id::text, release_generation
            """,
            (
                row["id"],
                lease["attempt_no"],
                lease["lease_epoch"],
                lease["lease_token"],
                lease["agent_id"],
                config.code_version or "development-unpinned",
                config.artifact_digest or "development-unpinned",
                config.config_revision or "development-unpinned",
                rollout_id,
                release_generation,
            ),
        )
        attempt = cursor.fetchone()
        if attempt is None:
            connection.rollback()
            return None
        claimed = dict(row)
        claimed.update(dict(lease))
        claimed["attempt_id"] = attempt["id"]
        claimed["rollout_id"] = attempt.get("rollout_id")
        claimed["release_generation"] = attempt.get("release_generation")
        claimed["_lease_seconds"] = config.lease_seconds
        _insert_attempt_observation(
            cursor,
            claimed,
            "claimed",
            {
                "status": "assigned",
                "lease_epoch": claimed["lease_epoch"],
                "attempt_no": claimed["attempt_no"],
            },
        )
    connection.commit()
    claimed["_last_heartbeat_observation_at"] = time.monotonic()
    return claimed


def _heartbeat(
    connection,
    job: dict[str, Any],
    progress: int | None = None,
) -> JobLeaseRefresh:
    job_id, lease_token, lease_epoch, agent_id = _lease_identity(job)
    lease_seconds = int(job.get("_lease_seconds") or DEFAULT_LEASE_SECONDS)
    observed_at = time.monotonic()
    last_observed_at = float(job.get("_last_heartbeat_observation_at") or 0.0)
    should_observe = observed_at - last_observed_at >= HEARTBEAT_OBSERVATION_INTERVAL_SECONDS
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE ops_jobs
            SET heartbeat_at = CURRENT_TIMESTAMP,
                leased_until = CURRENT_TIMESTAMP + make_interval(secs => %s),
                progress = GREATEST(progress, COALESCE(%s, progress)),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
              AND lease_token = %s
              AND lease_epoch = %s
              AND agent_id = %s
              AND status IN ('assigned', 'running')
              AND leased_until > CURRENT_TIMESTAMP
            """,
            (
                lease_seconds,
                progress,
                job_id,
                lease_token,
                lease_epoch,
                agent_id,
            ),
        )
        refreshed = cursor.rowcount == 1
        if refreshed and should_observe:
            _insert_attempt_observation(
                cursor,
                job,
                "heartbeat",
                {"progress": progress},
                sample_interval_seconds=HEARTBEAT_OBSERVATION_INTERVAL_SECONDS,
            )
    connection.commit()
    if refreshed and should_observe:
        job["_last_heartbeat_observation_at"] = observed_at
    return JobLeaseRefresh.REFRESHED if refreshed else JobLeaseRefresh.OWNERSHIP_LOST


def _cancellation_requested(connection, job: dict[str, Any]) -> bool:
    job_id, lease_token, lease_epoch, agent_id = _lease_identity(job)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT cancel_requested_at IS NOT NULL
            FROM ops_jobs
            WHERE id = %s
              AND lease_token = %s
              AND lease_epoch = %s
              AND agent_id = %s
              AND status IN ('assigned', 'running')
              AND leased_until > CURRENT_TIMESTAMP
            """,
            (job_id, lease_token, lease_epoch, agent_id),
        )
        row = cursor.fetchone()
    connection.commit()
    if row is None:
        raise CrawlerLeaseLost("crawler job ownership was lost before cancellation check")
    return bool(row and row[0])


def _mark_running(connection, job: dict[str, Any]) -> bool:
    job_id, lease_token, lease_epoch, agent_id = _lease_identity(job)
    lease_seconds = int(job.get("_lease_seconds") or DEFAULT_LEASE_SECONDS)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE ops_jobs
            SET status = 'running', started_at = CURRENT_TIMESTAMP,
                heartbeat_at = CURRENT_TIMESTAMP,
                leased_until = CURRENT_TIMESTAMP + make_interval(secs => %s),
                progress = 5,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
              AND lease_token = %s
              AND lease_epoch = %s
              AND agent_id = %s
              AND status = 'assigned'
              AND leased_until > CURRENT_TIMESTAMP
            """,
            (lease_seconds, job_id, lease_token, lease_epoch, agent_id),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            return False
        _insert_attempt_observation(
            cursor,
            job,
            "started",
            {"status": "running"},
        )
        cursor.execute(
            """
            UPDATE ops_crawler_runs
            SET status = 'running', current_stage = 'crawler_process',
                started_at = CURRENT_TIMESTAMP
            WHERE job_id = %s
            """,
            (job_id,),
        )
    connection.commit()
    return True


def _run_counts(connection, job_id: str, provider: str) -> dict[str, Any]:
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute(
            """
            SELECT
                COALESCE(SUM(collected_count), 0) AS total_count,
                COALESCE(SUM(GREATEST(collected_count - skipped_count, 0)), 0) AS success_count,
                COUNT(*) FILTER (WHERE status IN ('failed', 'stopped')) AS failed_count,
                COALESCE(SUM(inserted_count), 0) AS new_count,
                COALESCE(SUM(updated_count), 0) AS updated_count,
                MAX(error_type) FILTER (WHERE status IN ('failed', 'stopped')) AS error_code,
                MAX(error_message) FILTER (WHERE status IN ('failed', 'stopped')) AS error_message
            FROM crawler_run_log
            WHERE started_at >= COALESCE(
                (SELECT started_at FROM ops_jobs WHERE id = %s),
                NOW() - INTERVAL '1 hour'
            )
              AND (target_key = %s OR crawler_name = %s)
            """,
            (job_id, provider, provider),
        )
        row = cursor.fetchone()
    connection.commit()
    return dict(row or {})


def _finish_job(
    connection,
    job: dict[str, Any],
    *,
    final_status: str,
    return_code: int | None,
    detail: str = "",
    retryable: bool = False,
    error_code: str = "",
    task_result: dict[str, Any] | None = None,
) -> str:
    job_id, lease_token, lease_epoch, agent_id = _lease_identity(job)
    parameters = job.get("parameters") or {}
    provider = str(parameters.get("provider") or "").strip().upper()
    counts = _run_counts(connection, job_id, provider) if provider else {}
    error_message = redact_text(detail or counts.get("error_message"), maximum=2_000) or None
    result = {
        "return_code": return_code,
        "provider": provider or None,
        "counts": {
            key: int(counts.get(key) or 0)
            for key in ("total_count", "success_count", "failed_count", "new_count", "updated_count")
        },
    }
    if task_result is not None:
        result["task_result"] = task_result
    retry_count = int(job.get("retry_count") or 0)
    max_retries = int(job.get("max_retries") or 0)
    retry_scheduled = retryable and retry_count < max_retries
    persisted_status = "queued" if retry_scheduled else "dead_lettered" if retryable else final_status
    effective_error_code = (
        error_code or counts.get("error_code") or (None if final_status == "success" else final_status)
    )
    crawler_status = (
        "queued"
        if retry_scheduled
        else "failed"
        if persisted_status in {"failed", "timed_out", "dead_lettered"}
        else persisted_status
    )
    attempt_status = "failed" if final_status == "blocked" else final_status
    with connection.cursor() as cursor:
        # Lock and validate the live lease before writing mutable runtime
        # evidence. A reaper or competing completion cannot cross this row
        # lock, and any later fence failure rolls this transaction back.
        cursor.execute(
            """
            SELECT 1
            FROM ops_jobs
            WHERE id = %s
              AND lease_token = %s
              AND lease_epoch = %s
              AND agent_id = %s
              AND status IN ('assigned', 'running')
              AND leased_until > CURRENT_TIMESTAMP
            FOR UPDATE
            """,
            (job_id, lease_token, lease_epoch, agent_id),
        )
        if cursor.fetchone() is None:
            connection.rollback()
            return "ownership_lost"
        cursor.execute(
            """
            UPDATE ops_crawler_runs
            SET status = %s,
                current_stage = %s,
                total_count = %s,
                processed_count = %s,
                success_count = %s,
                failed_count = %s,
                new_count = %s,
                updated_count = %s,
                finished_at = CASE WHEN %s THEN NULL ELSE CURRENT_TIMESTAMP END
            WHERE job_id = %s
            """,
            (
                crawler_status,
                "retry_scheduled" if retry_scheduled else "finished",
                int(counts.get("total_count") or 0),
                int(counts.get("total_count") or 0),
                int(counts.get("success_count") or 0),
                int(counts.get("failed_count") or 0),
                int(counts.get("new_count") or 0),
                int(counts.get("updated_count") or 0),
                retry_scheduled,
                job_id,
            ),
        )
        _insert_log(
            cursor,
            job_id,
            (
                "info"
                if final_status == "success"
                else "warning"
                if final_status == "partial_success"
                else "error"
            ),
            f"crawler job completed with status {final_status}",
            {
                "return_code": return_code,
                "disposition": "retry_scheduled" if retry_scheduled else persisted_status,
            },
        )
        # Seal the physical attempt and append its single terminal observation
        # while the lease is still live.  The job transition follows in the
        # same transaction; a failed/fenced job update rolls all evidence back.
        cursor.execute(
            """
            UPDATE ops_crawler_task_attempts
            SET status = %s,
                finished_at = CURRENT_TIMESTAMP,
                exit_code = %s,
                error_code = %s,
                error_message = %s,
                metrics = %s
            WHERE job_id = %s
              AND lease_token = %s
              AND lease_epoch = %s
              AND agent_id = %s
              AND status = 'running'
            """,
            (
                attempt_status,
                return_code,
                effective_error_code,
                error_message,
                Json(result),
                job_id,
                lease_token,
                lease_epoch,
                agent_id,
            ),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            return "ownership_lost"
        _insert_attempt_observation(
            cursor,
            job,
            "finished",
            {
                "attempt_status": attempt_status,
                "job_status": persisted_status,
                "return_code": return_code,
                "retry_scheduled": retry_scheduled,
                "result": result,
            },
        )
        if retry_scheduled:
            cursor.execute(
                """
                UPDATE ops_jobs
                SET status = 'queued',
                    progress = 0,
                    result = %s,
                    error_code = %s,
                    error_message = %s,
                    retry_count = retry_count + 1,
                    available_at = CURRENT_TIMESTAMP + make_interval(secs => %s),
                    queued_at = CURRENT_TIMESTAMP,
                    agent_id = NULL,
                    lease_token = NULL,
                    leased_until = NULL,
                    assigned_at = NULL,
                    started_at = NULL,
                    heartbeat_at = NULL,
                    finished_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                  AND lease_token = %s
                  AND lease_epoch = %s
                  AND agent_id = %s
                  AND status IN ('assigned', 'running')
                  AND leased_until > CURRENT_TIMESTAMP
                  AND cancel_requested_at IS NULL
                  AND retry_count = %s
                """,
                (
                    Json(result),
                    effective_error_code,
                    error_message,
                    _retry_backoff_seconds(retry_count),
                    job_id,
                    lease_token,
                    lease_epoch,
                    agent_id,
                    retry_count,
                ),
            )
        else:
            cursor.execute(
                """
                UPDATE ops_jobs
                SET status = %s,
                    progress = CASE
                        WHEN %s IN ('success', 'partial_success') THEN 100
                        ELSE progress
                    END,
                    result = %s,
                    error_code = %s,
                    error_message = %s,
                    heartbeat_at = CURRENT_TIMESTAMP,
                    lease_token = NULL,
                    leased_until = NULL,
                    finished_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                  AND lease_token = %s
                  AND lease_epoch = %s
                  AND agent_id = %s
                  AND status IN ('assigned', 'running')
                  AND leased_until > CURRENT_TIMESTAMP
                """,
                (
                    persisted_status,
                    persisted_status,
                    Json(result),
                    effective_error_code,
                    error_message,
                    job_id,
                    lease_token,
                    lease_epoch,
                    agent_id,
                ),
            )
        if cursor.rowcount != 1:
            connection.rollback()
            return "ownership_lost"
    connection.commit()
    return "retry_scheduled" if retry_scheduled else "finished"


def _finish_diagnostic_job(
    connection,
    job: dict[str, Any],
    *,
    final_status: str,
    result: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> bool:
    job_id, lease_token, lease_epoch, agent_id = _lease_identity(job)
    safe_error_message = redact_text(error_message, maximum=2_000) if error_message else None
    attempt_status = "failed" if final_status == "blocked" else final_status
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT 1
            FROM ops_jobs
            WHERE id = %s
              AND lease_token = %s
              AND lease_epoch = %s
              AND agent_id = %s
              AND status IN ('assigned', 'running')
              AND leased_until > CURRENT_TIMESTAMP
            FOR UPDATE
            """,
            (job_id, lease_token, lease_epoch, agent_id),
        )
        if cursor.fetchone() is None:
            connection.rollback()
            return False
        _insert_log(
            cursor,
            job_id,
            "info" if final_status == "success" else "error",
            f"diagnostic job completed with status {final_status}",
            {"error_code": error_code} if error_code else None,
        )
        cursor.execute(
            """
            UPDATE ops_crawler_task_attempts
            SET status = %s,
                finished_at = CURRENT_TIMESTAMP,
                error_code = %s,
                error_message = %s,
                metrics = %s
            WHERE job_id = %s
              AND lease_token = %s
              AND lease_epoch = %s
              AND agent_id = %s
              AND status = 'running'
            """,
            (
                attempt_status,
                error_code,
                safe_error_message,
                Json(result or {}),
                job_id,
                lease_token,
                lease_epoch,
                agent_id,
            ),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            return False
        _insert_attempt_observation(
            cursor,
            job,
            "finished",
            {
                "attempt_status": attempt_status,
                "job_status": final_status,
                "result": result or {},
            },
        )
        cursor.execute(
            """
            UPDATE ops_jobs
            SET status = %s,
                progress = CASE WHEN %s = 'success' THEN 100 ELSE progress END,
                result = %s,
                error_code = %s,
                error_message = %s,
                heartbeat_at = CURRENT_TIMESTAMP,
                lease_token = NULL,
                leased_until = NULL,
                finished_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
              AND lease_token = %s
              AND lease_epoch = %s
              AND agent_id = %s
              AND status IN ('assigned', 'running')
              AND leased_until > CURRENT_TIMESTAMP
            """,
            (
                final_status,
                final_status,
                Json(result or {}),
                error_code,
                safe_error_message,
                job_id,
                lease_token,
                lease_epoch,
                agent_id,
            ),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            return False
    connection.commit()
    return True


def _execute_parser_probe(connection, job: dict[str, Any]) -> None:
    from backend.ops.service import sanitize_for_audit
    from tools.parser_probe import parser_probe

    job_id = str(job["id"])
    parameters = job.get("parameters") or {}
    if not isinstance(parameters, dict) or parameters.get("command") != "parser_probe":
        message = "agent_command is not in the reviewed crawler-worker command registry"
        _finish_diagnostic_job(
            connection,
            job,
            final_status="blocked",
            error_code="unsupported_agent_command",
            error_message=message,
        )
        return
    if not _mark_running(connection, job):
        return
    _append_log(connection, job_id, "info", "DB 저장 없이 Parser Probe를 시작합니다.")
    try:
        result = parser_probe(
            {
                "url": parameters.get("url"),
                "timeout": parameters.get("timeout", 25),
            }
        )
        safe_result = sanitize_for_audit(result)
        _finish_diagnostic_job(
            connection,
            job,
            final_status="success",
            result=safe_result if isinstance(safe_result, dict) else {"probe_result": safe_result},
        )
    except Exception as exc:
        logger.exception("Parser Probe failed job_id=%s", job_id)
        connection.rollback()
        _finish_diagnostic_job(
            connection,
            job,
            final_status="failed",
            error_code="parser_probe_failed",
            error_message=f"{type(exc).__name__}: parser probe failed",
        )


def _terminate_active_process() -> None:
    global ACTIVE_PROCESS
    process = ACTIVE_PROCESS
    if process is None or process.poll() is not None:
        return
    try:
        if os.name == "nt":
            process.terminate()
        else:
            os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        process.wait(timeout=5)


def execute_job(connection, job: dict[str, Any], config: WorkerConfig) -> None:
    global ACTIVE_PROCESS
    job_id = str(job["id"])
    if job.get("job_type") == "agent_command":
        _execute_parser_probe(connection, job)
        return
    task_result_path: Path | None = None
    try:
        parameters = job.get("parameters") or {}
        if config.environment in {"production", "staging"} and (
            "allowed_output_providers" not in parameters
            or "scheduled_providers" not in parameters
        ):
            raise ValueError("central crawler job is missing its immutable provider scope")
        requested_concurrency = parameters.get("concurrency", 1)
        if (
            isinstance(requested_concurrency, int)
            and not isinstance(requested_concurrency, bool)
            and requested_concurrency > config.max_concurrency
        ):
            raise ValueError("concurrency exceeds this worker's reviewed limit")
        command, environment_overrides = build_crawler_execution(parameters)
        batch_id = _canonical_uuid(parameters.get("batch_id"), field="batch_id")
    except ValueError as exc:
        _finish_job(
            connection,
            job,
            final_status="blocked",
            return_code=None,
            detail=str(exc),
            error_code="invalid_crawler_job",
        )
        return

    if not _mark_running(connection, job):
        return
    _append_log(
        connection,
        job_id,
        "info",
        "검토된 크롤러 명령 템플릿으로 작업을 시작합니다.",
        {"provider": (job.get("parameters") or {}).get("provider")},
    )
    started = time.monotonic()
    lease_tracker = JobLeaseTracker(config.lease_seconds)
    line_count = 0
    try:
        process_environment = os.environ.copy()
        task_result_path = _task_result_path(job)
        process_environment.update(environment_overrides)
        process_environment.update(
            {
                "CRAWL_JOB_ID": job_id,
                "CRAWL_LEASE_TOKEN": str(job["lease_token"]),
                "CRAWL_LEASE_EPOCH": str(job["lease_epoch"]),
                "CRAWL_ATTEMPT_NO": str(job["attempt_no"]),
                "CRAWL_REQUIRE_LEASE": "1",
                "CRAWL_BATCH_ID": batch_id,
                "CRAWL_DISTRIBUTED_TASK": "1",
                "CRAWL_TASK_RESULT_DIR": str(task_result_path.parent),
                "CRAWL_TASK_RESULT_PATH": str(task_result_path),
            }
        )
        ACTIVE_PROCESS = subprocess.Popen(  # noqa: S603 - argv is built by a fixed allowlisted template.
            command,
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            start_new_session=os.name != "nt",
            env=process_environment,
        )
        assert ACTIVE_PROCESS.stdout is not None
        output_queue: queue.Queue[str | None] = queue.Queue(maxsize=1_000)

        def read_output() -> None:
            try:
                for output_line in ACTIVE_PROCESS.stdout:
                    output_queue.put(output_line)
            finally:
                output_queue.put(None)

        reader = threading.Thread(target=read_output, name=f"ops-job-{job_id}-output", daemon=True)
        reader.start()
        next_control_check = 0.0
        next_health_publish = 0.0
        while True:
            try:
                line = output_queue.get(timeout=0.25)
            except queue.Empty:
                line = ""
            if line and line_count < 20_000:
                _append_log(connection, job_id, "info", line.rstrip())
                line_count += 1
            return_code = ACTIVE_PROCESS.poll()
            if return_code is not None and output_queue.empty():
                break
            now = time.monotonic()
            if now >= next_control_check:
                if lease_tracker.expired(now=now):
                    raise CrawlerLeaseLost("crawler worker local lease deadline expired")
                if _cancellation_requested(connection, job):
                    _append_log(connection, job_id, "warning", "취소 요청을 확인하여 크롤러를 종료합니다.")
                    _terminate_active_process()
                    _finish_job(
                        connection,
                        job,
                        final_status="cancelled",
                        return_code=ACTIVE_PROCESS.returncode,
                    )
                    return
                if now - started > config.command_timeout:
                    _append_log(connection, job_id, "error", "작업 제한 시간을 초과하여 크롤러를 종료합니다.")
                    _terminate_active_process()
                    _finish_job(
                        connection,
                        job,
                        final_status="timed_out",
                        return_code=ACTIVE_PROCESS.returncode,
                        retryable=True,
                        error_code="crawler_command_timeout",
                    )
                    return
                refresh = _heartbeat(connection, job, progress=10)
                if refresh is JobLeaseRefresh.OWNERSHIP_LOST:
                    raise CrawlerLeaseLost("crawler worker lease was fenced")
                lease_tracker.confirm(now=now)
                if now >= next_health_publish:
                    _heartbeat_registered_agent(connection, config)
                    _publish_worker_health(config)
                    next_health_publish = now + WORKER_STATUS_INTERVAL_SECONDS
                next_control_check = now + CONTROL_CHECK_INTERVAL_SECONDS
        final_status = ops_status_for_crawler_exit_code(return_code)
        task_result = (
            _load_task_result(task_result_path, job, batch_id)
            if final_status in {"success", "partial_success"}
            else None
        )
        _finish_job(
            connection,
            job,
            final_status=final_status,
            return_code=return_code,
            retryable=_retryable_crawler_outcome(return_code, final_status),
            error_code=(
                "crawler_lock_contention"
                if return_code == CRAWLER_LOCK_CONTENTION_EXIT_CODE
                else "crawler_partial_result"
                if final_status == "partial_success"
                else "crawler_process_failed"
                if final_status == "failed"
                else ""
            ),
            task_result=task_result,
        )
    except CrawlerLeaseLost:
        logger.warning("Crawler job lease lost; terminating child without publishing a result job_id=%s", job_id)
        connection.rollback()
        _terminate_active_process()
    except psycopg2.Error:
        connection.rollback()
        _terminate_active_process()
        raise
    except CrawlerTaskResultError as exc:
        logger.error("Crawler task result rejected job_id=%s error=%s", job_id, exc)
        connection.rollback()
        _terminate_active_process()
        _finish_job(
            connection,
            job,
            final_status="failed",
            return_code=ACTIVE_PROCESS.returncode if ACTIVE_PROCESS else None,
            detail=str(exc),
            error_code="invalid_crawler_task_result",
        )
    except Exception as exc:
        logger.exception("Ops crawler job failed job_id=%s", job_id)
        connection.rollback()
        _terminate_active_process()
        _finish_job(
            connection,
            job,
            final_status="failed",
            return_code=ACTIVE_PROCESS.returncode if ACTIVE_PROCESS else None,
            detail=f"{type(exc).__name__}: worker execution failed",
            retryable=True,
            error_code="crawler_worker_execution_failed",
        )
    finally:
        if task_result_path is not None:
            try:
                task_result_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove crawler task result file job_id=%s", job_id)
        ACTIVE_PROCESS = None


def _handle_signal(_signum, _frame) -> None:
    global RUNNING
    RUNNING = False
    _terminate_active_process()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MoonCen PostgreSQL-backed Ops crawler worker")
    parser.add_argument("--once", action="store_true", help="Claim at most one job and exit")
    parser.add_argument("--agent-id", default=os.getenv("OPS_AGENT_ID", ""), help="Registered ops_agents UUID")
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=float(os.getenv("OPS_JOB_POLL_INTERVAL_SECONDS", "2")),
        help="Queue polling interval between 0.5 and 60 seconds",
    )
    args = parser.parse_args(argv)
    if not 0.5 <= args.poll_interval <= 60:
        parser.error("--poll-interval must be between 0.5 and 60 seconds")
    try:
        args.agent_id = UUID(args.agent_id) if args.agent_id else None
    except ValueError:
        parser.error("--agent-id must be a UUID")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    environment = normalized_environment()
    validate_control_plane_colocation(environment)
    code_version, artifact_digest, config_revision = worker_compatibility_from_environment(environment)
    worker_key = os.getenv("OPS_CRAWLER_WORKER_ID", "").strip()
    if environment in {"production", "staging"} and not WORKER_KEY_PATTERN.fullmatch(worker_key):
        raise RuntimeError("OPS_CRAWLER_WORKER_ID is required and must be a canonical worker key")
    max_concurrency_value = os.getenv("OPS_CRAWLER_MAX_CONCURRENCY", "").strip()
    if environment in {"production", "staging"} and not max_concurrency_value:
        raise RuntimeError(
            "OPS_CRAWLER_MAX_CONCURRENCY must come from the reviewed host resource drop-in"
        )
    config = WorkerConfig(
        environment=environment,
        agent_id=args.agent_id,
        poll_interval=args.poll_interval,
        command_timeout=bounded_env_int("OPS_CRAWLER_JOB_TIMEOUT_SECONDS", 32_400, 60, 86_400),
        lease_seconds=bounded_env_int(
            "OPS_CRAWLER_LEASE_SECONDS",
            DEFAULT_LEASE_SECONDS,
            30,
            900,
        ),
        code_version=code_version,
        artifact_digest=artifact_digest,
        config_revision=config_revision,
        worker_key=worker_key,
        hostname=configured_worker_hostname(environment),
        health_state_path=_worker_status_path(
            "OPS_CRAWLER_HEALTH_STATE",
            "/run/mooncen-crawler/health.json",
            environment,
        ),
        drain_state_path=_worker_status_path(
            "OPS_CRAWLER_DRAIN_STATE",
            "/run/mooncen-crawler/drain.json",
            environment,
        ),
        max_concurrency=bounded_env_int(
            "OPS_CRAWLER_MAX_CONCURRENCY",
            5,
            1,
            5,
        ),
    )
    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_signal)

    connection = connect_queue()
    try:
        assert_dedicated_worker_database_role(connection, config.environment)
        if config.agent_id is None:
            config = replace(config, agent_id=resolve_local_agent_id(connection, config.environment))
        if config.agent_id is None:
            raise RuntimeError("Crawler worker requires a healthy registered ops_agent for lease ownership")
        if config.environment in {"production", "staging"} and config.agent_id.int == 0:
            raise RuntimeError("Crawler worker OPS_AGENT_ID cannot be the nil UUID")
        _heartbeat_registered_agent(connection, config)
        next_status_publish = 0.0
        while RUNNING:
            try:
                desired = _load_worker_desired_state(connection, config)
                now = time.monotonic()
                if now >= next_status_publish:
                    _heartbeat_registered_agent(connection, config)
                    _publish_worker_health(config)
                    if desired and desired.get("desired_status") in {"draining", "disabled"}:
                        _publish_worker_drain(config, desired)
                    next_status_publish = now + WORKER_STATUS_INTERVAL_SECONDS
                if desired and desired.get("desired_status") in {"draining", "disabled"}:
                    if args.once:
                        return 0
                    time.sleep(config.poll_interval)
                    continue
                if desired and desired.get("desired_status") != "active":
                    raise RuntimeError("crawler worker central desired status is invalid")
                job = _claim_job(connection, config, desired)
                if job:
                    execute_job(connection, job, config)
                elif args.once:
                    return 0
            except psycopg2.Error:
                connection.rollback()
                logger.exception("Ops queue database operation failed")
                if args.once:
                    return 1
            if args.once:
                return 0
            time.sleep(config.poll_interval)
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("OPS_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    raise SystemExit(main())
