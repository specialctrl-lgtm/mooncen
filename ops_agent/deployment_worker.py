from __future__ import annotations

import argparse
import errno
import hashlib
import json
import logging
import os
import queue
import re
import signal
import shutil
import socket
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any, Callable
from uuid import UUID, uuid4

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import Json, RealDictCursor

from DB.connection_settings import (
    bounded_env_int,
    database_connect_options,
    deployment_heartbeat_lease_seconds,
)
from tools.ops_redaction import redact_text

from .crawler_worker import PROJECT_ROOT, normalized_environment
from .container_deployment import (
    MAX_CONTROLLER_OUTPUT_BYTES,
    ContainerDeploymentError,
    ContainerExecutionEvidence,
    ContainerIngressUpload,
    assert_container_runtime_cas,
    assert_container_worker_lease,
    assert_container_worker_lease_claim,
    build_container_controller_command,
    build_container_ingress_commands,
    build_container_status_command,
    build_container_worker_lease_command,
    build_container_worker_lease_claim_command,
    container_execution_prerequisites_ready,
    container_release_files,
    load_container_execution_evidence,
    parse_container_action_result,
    parse_container_ingress_result,
    parse_container_pipeline_step_result,
    parse_container_status,
    parse_container_worker_lease_result,
    parse_container_worker_lease_claim_result,
    read_container_controller_status,
    read_container_controller_presence,
    reconcile_container_status,
)
from .deployment_registry import (
    COMMIT_PATTERN,
    DEPLOYMENT_WORKER_HEARTBEAT_PATH,
    DEPLOYMENT_WORKER_STATE_ROOT,
    create_deployment_snapshot_commit,
    deployment_readiness,
    identity_file_ready,
    powershell_executable,
    preserve_deployment_release_reference,
    release_deployment_snapshot_reference,
    reviewed_target,
)


load_dotenv(PROJECT_ROOT / ".env")
logger = logging.getLogger(__name__)
RUNNING = True
ACTIVE_PROCESS: subprocess.Popen[str] | None = None
ALLOWED_PARAMETER_KEYS = {
    "action",
    "target",
    "target_commit",
    "target_identity",
    "service_type",
    "skip_workers",
    "source_tree",
    "required_agent_hostname",
}
DEPLOYMENT_SUBPROCESS_SECRET_NAMES = {
    "AUTH_SECRET",
    "BOT_TOKEN",
    "CLOUDFLARED_TOKEN",
    "DATABASE_URL",
    "DB_API_PASSWORD",
    "DB_AI_PASSWORD",
    "DB_APPLIER_PASSWORD",
    "DB_BACKUP_PASSWORD",
    "DB_CHECK_PASSWORD",
    "DB_CRAWLER_PASSWORD",
    "DB_PASSWORD",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "KAKAO_MAPS_JAVASCRIPT_KEY",
    "KAKAO_MAPS_REST_API_KEY",
    "MOONCEN_BOT_TOKEN",
    "MOONCEN_OPS_PASSWORD_HASH",
    "NAVER_OAUTH_CLIENT_SECRET",
    "OPS_DEPLOY_QUEUE_DB_PASSWORD",
    "OPS_QUEUE_DB_PASSWORD",
    "PGPASSWORD",
    "PRIMARY_DB_PASSWORD",
    "TUNNEL_TOKEN",
}
DEPLOYMENT_SUBPROCESS_SECRET_NAME = re.compile(
    r"(?i)(?:password|passwd|secret|token|credential|authorization|cookie|"
    r"private[_-]?key|(?:api|javascript)[_-]?key|database[_-]?url|dsn)"
)
TRANSIENT_DATABASE_SQLSTATES = frozenset({"40001", "40P01", "55P03", "57014"})
TRANSIENT_DATABASE_ERROR_TYPES = (
    psycopg2.errors.DeadlockDetected,
    psycopg2.errors.LockNotAvailable,
    psycopg2.errors.QueryCanceled,
    psycopg2.errors.SerializationFailure,
)
BEST_EFFORT_DATABASE_ATTEMPTS = 2
REQUIRED_DATABASE_ATTEMPTS = 5
DATABASE_RETRY_INITIAL_DELAY_SECONDS = 0.1
DATABASE_RETRY_MAX_DELAY_SECONDS = 2.0
LOG_WRITE_RETRY_COOLDOWN_SECONDS = 5.0
DATABASE_RECONNECT_COOLDOWN_SECONDS = 5.0
PREPARATION_CONTROL_INTERVAL_SECONDS = 2.0
PENDING_FINAL_REPLAY_INTERVAL_SECONDS = 15.0
WORKER_HEARTBEAT_REPLACE_ATTEMPTS = 6
WORKER_HEARTBEAT_RETRY_INITIAL_DELAY_SECONDS = 0.025
WORKER_HEARTBEAT_RETRY_MAX_DELAY_SECONDS = 0.2
JOB_LEASE_MINIMUM_SAFETY_MARGIN_SECONDS = 30.0
JOB_LEASE_MAXIMUM_SAFETY_MARGIN_SECONDS = 120.0
DEPLOYMENT_SPOOL_DIRECTORY = Path("spool")
DEPLOYMENT_SPOOL_MAX_BYTES = 1_048_576
DEPLOYMENT_SPOOL_MAX_FILES = 200
DEPLOYMENT_PENDING_FINAL_DIRECTORY = Path("pending-final")
DEPLOYMENT_RUNTIME_DIRECTORY = Path("runtime")
SENSITIVE_METADATA_KEY = re.compile(r"(?i)(?:password|passwd|secret|token|api[_-]?key|credential|authorization|cookie)")
_LOG_WRITE_RETRY_AFTER = 0.0
_DATABASE_RECONNECT_RETRY_AFTER = 0.0
_FINAL_STATUS_SPOOL_ERROR_AT: float | None = None
_WORKER_HEARTBEAT_LOCK = Lock()

REMOTE_DEPLOY_EXIT_CODES: dict[int, tuple[str, str]] = {
    65: (
        "unsafe_remote_state",
        "The target contains an unsafe deployment state that requires manual review.",
    ),
    73: (
        "lock_busy",
        "Another deployment owns the target lock. Wait for it to finish before retrying.",
    ),
    75: (
        "recovery_required",
        "The target could not safely converge its previous deployment state; recovery review is required.",
    ),
}


class DeploymentPreparationCancelled(RuntimeError):
    """Raised after a cancellation is observed while preparing a snapshot."""


class DeploymentOwnershipLost(RuntimeError):
    """Raised when the queue proves that this worker no longer owns the job."""


class DeploymentLeaseExpired(RuntimeError):
    """Raised before a database partition can outlive the shared job lease."""


class NativeDeploymentBlockedByContainerRuntime(RuntimeError):
    """Raised when a native deployment could overlap a container runtime transition."""


class JobLeaseRefresh(str, Enum):
    REFRESHED = "refreshed"
    UNAVAILABLE = "unavailable"
    OWNERSHIP_LOST = "ownership_lost"


class JobLeaseTracker:
    """Track the last database-confirmed lease using a monotonic local clock."""

    def __init__(
        self,
        stale_after_seconds: int,
        *,
        confirmed_at: float | None = None,
    ) -> None:
        self.stale_after_seconds = stale_after_seconds
        self._last_confirmed_at = time.monotonic() if confirmed_at is None else confirmed_at
        self._lock = Lock()

    @property
    def local_deadline_seconds(self) -> float:
        safety_margin = min(
            JOB_LEASE_MAXIMUM_SAFETY_MARGIN_SECONDS,
            max(
                JOB_LEASE_MINIMUM_SAFETY_MARGIN_SECONDS,
                self.stale_after_seconds * 0.2,
            ),
        )
        return max(
            PREPARATION_CONTROL_INTERVAL_SECONDS,
            self.stale_after_seconds - safety_margin,
        )

    def confirm(self, *, now: float | None = None) -> None:
        confirmed_at = time.monotonic() if now is None else now
        with self._lock:
            self._last_confirmed_at = confirmed_at

    def expired(self, *, now: float | None = None) -> bool:
        checked_at = time.monotonic() if now is None else now
        with self._lock:
            last_confirmed_at = self._last_confirmed_at
        return checked_at - last_confirmed_at >= self.local_deadline_seconds


@dataclass(frozen=True)
class WorkerConfig:
    environment: str
    agent_id: UUID | None
    poll_interval: float
    command_timeout: int
    stale_after_seconds: int = 300
    container_only: bool = False


def _job_lease_identity(job: dict[str, Any]) -> tuple[str, str, int, str, int]:
    """Return the exact DB claim tuple carried by one in-memory job."""

    try:
        job_id = str(UUID(str(job["id"])))
        agent_id = str(UUID(str(job["agent_id"])))
        lease_token = str(UUID(str(job["lease_token"])))
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise DeploymentOwnershipLost("deployment claim identity is incomplete") from exc
    lease_epoch = job.get("lease_epoch")
    lease_seconds = job.get("_lease_seconds")
    if (
        type(lease_epoch) is not int
        or not 1 <= lease_epoch <= 9_223_372_036_854_775_807
        or type(lease_seconds) is not int
        or not 30 <= lease_seconds <= 86_400
    ):
        raise DeploymentOwnershipLost("deployment claim counters are invalid")
    return job_id, lease_token, lease_epoch, agent_id, lease_seconds


def queue_database_config() -> dict[str, Any]:
    environment = normalized_environment()
    if environment == "production":
        required_names = (
            "OPS_DEPLOY_QUEUE_DB_HOST",
            "OPS_DEPLOY_QUEUE_DB_PORT",
            "OPS_DEPLOY_QUEUE_DB_NAME",
            "OPS_DEPLOY_QUEUE_DB_USER",
            "OPS_DEPLOY_QUEUE_DB_PASSWORD",
        )
        configured = {name: os.getenv(name) for name in required_names}
        if any(value is None or not value.strip() for value in configured.values()):
            raise RuntimeError(
                "Production deployment worker requires explicit queue database "
                "credentials via "
                "OPS_DEPLOY_QUEUE_DB_HOST/PORT/NAME/USER/PASSWORD"
            )
        host = str(configured["OPS_DEPLOY_QUEUE_DB_HOST"]).strip()
        port = bounded_env_int("OPS_DEPLOY_QUEUE_DB_PORT", 0, 1, 65535)
        database = str(configured["OPS_DEPLOY_QUEUE_DB_NAME"]).strip()
        user = str(configured["OPS_DEPLOY_QUEUE_DB_USER"]).strip()
        password = str(configured["OPS_DEPLOY_QUEUE_DB_PASSWORD"])
        owner = (
            os.getenv("DB_OWNER_USER", "").strip()
            or os.getenv("DB_MIGRATOR_USER", "").strip()
            or os.getenv("DB_USER", "").strip()
        )
        if owner and user == owner:
            raise RuntimeError("Deployment worker must not use the database owner role")
    else:
        host = os.getenv(
            "OPS_DEPLOY_QUEUE_DB_HOST",
            os.getenv("OPS_QUEUE_DB_HOST", os.getenv("DB_HOST", "localhost")),
        ).strip()
        port = bounded_env_int(
            "OPS_DEPLOY_QUEUE_DB_PORT",
            bounded_env_int(
                "OPS_QUEUE_DB_PORT",
                bounded_env_int("DB_PORT", 5432, 1, 65535),
                1,
                65535,
            ),
            1,
            65535,
        )
        database = os.getenv(
            "OPS_DEPLOY_QUEUE_DB_NAME",
            os.getenv("OPS_QUEUE_DB_NAME", os.getenv("DB_NAME", "mooncen")),
        ).strip()
        user = os.getenv(
            "OPS_DEPLOY_QUEUE_DB_USER",
            os.getenv("OPS_QUEUE_DB_USER", os.getenv("DB_CRAWLER_USER", "")),
        ).strip() or os.getenv("DB_USER", "mooncen_crawler_login")
        password = os.getenv(
            "OPS_DEPLOY_QUEUE_DB_PASSWORD",
            os.getenv("OPS_QUEUE_DB_PASSWORD", os.getenv("DB_CRAWLER_PASSWORD", "")),
        ) or os.getenv("DB_PASSWORD", "")
    return {
        "host": host,
        "port": port,
        "database": database,
        "user": user,
        "password": password,
        **database_connect_options(host, "mooncen-ops-deployment-worker"),
    }


def connect_queue():
    connection = psycopg2.connect(**queue_database_config())
    connection.autocommit = False
    return connection


def container_worker_service_boundary_ready() -> bool:
    """Require the dedicated OS/DB/transport identity before advertising execution."""

    required_queue_values = {
        name: os.getenv(name, "").strip()
        for name in (
            "OPS_DEPLOY_QUEUE_DB_HOST",
            "OPS_DEPLOY_QUEUE_DB_PORT",
            "OPS_DEPLOY_QUEUE_DB_NAME",
            "OPS_DEPLOY_QUEUE_DB_USER",
            "OPS_DEPLOY_QUEUE_DB_PASSWORD",
        )
    }
    return bool(
        all(required_queue_values.values())
        and required_queue_values["OPS_DEPLOY_QUEUE_DB_USER"]
        == "mooncen_deployment_worker_login"
        and container_execution_prerequisites_ready()
    )


def _deployment_agent_capabilities() -> list[str]:
    capabilities = ["deployment_queue"]
    if (
        socket.gethostname().split(".", 1)[0].lower() == "an2p"
        and container_worker_service_boundary_ready()
    ):
        capabilities.append("container_deployment")
    return capabilities


def _register_deployment_agent(connection, environment: str) -> UUID:
    hostname = socket.gethostname()
    capability = Json(_deployment_agent_capabilities())
    exclusive = os.getenv("OPS_DEPLOY_AGENT_EXCLUSIVE", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    capability_update = (
        "EXCLUDED.capabilities"
        if exclusive
        else """CASE
                    WHEN (ops_agents.capabilities - 'container_deployment') @> %(capability)s
                    THEN ops_agents.capabilities - 'container_deployment'
                    ELSE (ops_agents.capabilities - 'container_deployment') || %(capability)s
                END"""
    )
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            INSERT INTO ops_agents (
                name, hostname, environment, os_type, status,
                capabilities, credential_hint, last_seen_at
            )
            VALUES (
                %(name)s, %(hostname)s, %(environment)s, %(os_type)s,
                'healthy', %(capability)s, %(credential_hint)s, CURRENT_TIMESTAMP
            )
            ON CONFLICT (environment, hostname) DO UPDATE
            SET status = 'healthy',
                capabilities = {capability_update},
                credential_hint = EXCLUDED.credential_hint,
                last_seen_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            RETURNING id
            """,
            {
                "name": f"{hostname} deployment agent",
                "hostname": hostname,
                "environment": environment,
                "os_type": os.name,
                "capability": capability,
                "credential_hint": "local reviewed deployment worker",
            },
        )
        row = cursor.fetchone()
    connection.commit()
    if not row:
        raise RuntimeError("Deployment agent registration did not return an id")
    return UUID(str(row[0]))


def _touch_deployment_agent(connection, agent_id: UUID, environment: str) -> None:
    capability = Json(_deployment_agent_capabilities())
    exclusive = os.getenv("OPS_DEPLOY_AGENT_EXCLUSIVE", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    capability_update = (
        "%(capability)s"
        if exclusive
        else """CASE
                    WHEN (capabilities - 'container_deployment') @> %(capability)s
                    THEN capabilities - 'container_deployment'
                    ELSE (capabilities - 'container_deployment') || %(capability)s
                END"""
    )
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            UPDATE ops_agents
            SET status = 'healthy',
                capabilities = {capability_update},
                last_seen_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %(agent_id)s
              AND environment = %(environment)s
            """,
            {
                "capability": capability,
                "agent_id": str(agent_id),
                "environment": environment,
            },
        )
        updated = cursor.rowcount
    connection.commit()
    if updated != 1:
        raise RuntimeError("Deployment agent registration is unavailable")


def _worker_heartbeat_replace_retryable(exc: OSError) -> bool:
    """Return whether Windows may release the destination after a short wait."""

    return (
        isinstance(exc, PermissionError)
        or exc.errno in {errno.EACCES, errno.EBUSY, errno.EPERM}
        or getattr(exc, "winerror", None) in {5, 32, 33}
    )


def _worker_heartbeat_retry_delay(attempt: int) -> float:
    return min(
        WORKER_HEARTBEAT_RETRY_MAX_DELAY_SECONDS,
        WORKER_HEARTBEAT_RETRY_INITIAL_DELAY_SECONDS * (2 ** max(0, attempt - 1)),
    )


def _cleanup_worker_heartbeat_temporary(path: Path) -> None:
    for attempt in range(1, WORKER_HEARTBEAT_REPLACE_ATTEMPTS + 1):
        try:
            path.unlink()
            return
        except FileNotFoundError:
            return
        except OSError as exc:
            if not _worker_heartbeat_replace_retryable(exc) or attempt >= WORKER_HEARTBEAT_REPLACE_ATTEMPTS:
                logger.warning(
                    "Unable to remove deployment worker heartbeat temporary file",
                    exc_info=True,
                )
                return
            time.sleep(_worker_heartbeat_retry_delay(attempt))


def _private_worker_state_root(root: Path = DEPLOYMENT_WORKER_STATE_ROOT) -> Path:
    try:
        if root.is_symlink() or not root.is_dir():
            raise OSError("deployment worker state root is unsafe")
        resolved = root.resolve(strict=True)
        metadata = resolved.stat()
    except (OSError, RuntimeError) as exc:
        raise OSError("deployment worker state root is unavailable") from exc
    if metadata.st_mode & 0o077:
        raise OSError("deployment worker state root permissions are unsafe")
    if os.name != "nt" and metadata.st_uid != os.geteuid():
        raise OSError("deployment worker state root owner is unsafe")
    return resolved


def _publish_worker_heartbeat(root: Path = DEPLOYMENT_WORKER_STATE_ROOT) -> None:
    payload: dict[str, Any] = {"pid": os.getpid(), "updated_at": time.time()}
    if _FINAL_STATUS_SPOOL_ERROR_AT is not None:
        payload["pending_final_spool_error_at"] = _FINAL_STATUS_SPOOL_ERROR_AT
    path = _private_worker_state_root(root) / DEPLOYMENT_WORKER_HEARTBEAT_PATH
    with _WORKER_HEARTBEAT_LOCK:
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="ascii",
                newline="\n",
                prefix=f".{path.name}.{os.getpid()}.",
                suffix=".tmp",
                dir=path.parent,
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(payload, handle, separators=(",", ":"))

            for attempt in range(1, WORKER_HEARTBEAT_REPLACE_ATTEMPTS + 1):
                try:
                    os.replace(temporary, path)
                    return
                except OSError as exc:
                    if not _worker_heartbeat_replace_retryable(exc) or attempt >= WORKER_HEARTBEAT_REPLACE_ATTEMPTS:
                        raise
                    logger.debug(
                        "Retrying deployment worker heartbeat atomic replace attempt=%s/%s",
                        attempt,
                        WORKER_HEARTBEAT_REPLACE_ATTEMPTS,
                    )
                    time.sleep(_worker_heartbeat_retry_delay(attempt))
        finally:
            if temporary is not None:
                _cleanup_worker_heartbeat_temporary(temporary)


def _publish_worker_heartbeat_resilient() -> bool:
    try:
        _publish_worker_heartbeat()
        return True
    except OSError:
        logger.warning(
            "Local deployment worker heartbeat could not be published",
            exc_info=True,
        )
        return False


def _touch_deployment_agent_resilient(connection, config: WorkerConfig):
    if config.agent_id is None:
        return connection
    try:
        _touch_deployment_agent(connection, config.agent_id, config.environment)
        return connection
    except psycopg2.Error:
        logger.warning(
            "Deployment agent heartbeat failed; reconnecting without stopping the active deployment",
            exc_info=True,
        )
        try:
            connection.rollback()
        except psycopg2.Error:
            pass
        return _try_reconnect_queue(connection)
    except RuntimeError:
        logger.warning(
            "Deployment agent heartbeat row is unavailable while a deployment is active",
            exc_info=True,
        )
        return connection


def _start_preparation_monitor(
    config: WorkerConfig,
    job: dict[str, Any],
    lease_tracker: JobLeaseTracker,
) -> tuple[Event, Event, Event, Event, Thread | None]:
    """Keep both worker and job leases fresh during slow Git snapshot work."""

    job_id = str(job["id"])
    stop_requested = Event()
    cancel_requested = Event()
    ownership_lost = Event()
    lease_expired = Event()
    if config.agent_id is None:
        return (
            stop_requested,
            cancel_requested,
            ownership_lost,
            lease_expired,
            None,
        )

    def monitor() -> None:
        monitor_connection = None
        try:
            while not stop_requested.is_set():
                try:
                    _publish_worker_heartbeat_resilient()
                    if monitor_connection is None:
                        monitor_connection = connect_queue()
                    monitor_connection = _touch_deployment_agent_resilient(
                        monitor_connection,
                        config,
                    )
                    monitor_connection, lease_refresh = _refresh_job_lease_with_reconnect(
                        monitor_connection,
                        job,
                        5,
                    )
                    if lease_refresh is JobLeaseRefresh.REFRESHED:
                        lease_tracker.confirm()
                    elif lease_refresh is JobLeaseRefresh.OWNERSHIP_LOST:
                        ownership_lost.set()
                        break
                    else:
                        if lease_tracker.expired():
                            lease_expired.set()
                            break
                    if _cancellation_requested(monitor_connection, job):
                        cancel_requested.set()
                except Exception:  # pragma: no cover - defensive daemon boundary
                    logger.warning(
                        "Deployment preparation monitor could not refresh its lease",
                        exc_info=True,
                    )
                    if monitor_connection is not None:
                        try:
                            monitor_connection.close()
                        except Exception:
                            pass
                    monitor_connection = None
                    if lease_tracker.expired():
                        lease_expired.set()
                        break
                if stop_requested.wait(PREPARATION_CONTROL_INTERVAL_SECONDS):
                    break
        finally:
            if monitor_connection is not None:
                try:
                    monitor_connection.close()
                except Exception:
                    pass

    thread = Thread(
        target=monitor,
        name=f"ops-deploy-{job_id}-preparation",
        daemon=True,
    )
    thread.start()
    return (
        stop_requested,
        cancel_requested,
        ownership_lost,
        lease_expired,
        thread,
    )


def _stop_preparation_monitor(stop_requested: Event, thread: Thread | None) -> None:
    stop_requested.set()
    if thread is not None:
        thread.join(timeout=5)


def _clear_worker_heartbeat(root: Path = DEPLOYMENT_WORKER_STATE_ROOT) -> None:
    try:
        path = _private_worker_state_root(root) / DEPLOYMENT_WORKER_HEARTBEAT_PATH
        payload = json.loads(path.read_text(encoding="ascii"))
        if isinstance(payload, dict) and payload.get("pid") == os.getpid():
            path.unlink()
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass


def _bounded_text(value: Any, *, maximum: int, field: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned or len(cleaned) > maximum or "\x00" in cleaned or any(character in cleaned for character in "\r\n"):
        raise ValueError(f"{field} is invalid")
    return cleaned


def validated_parameters(
    parameters: Any,
    *,
    root: Path = PROJECT_ROOT,
    readiness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(parameters, dict):
        raise ValueError("deployment parameters must be an object")
    unknown = set(parameters) - ALLOWED_PARAMETER_KEYS
    if unknown:
        raise ValueError("deployment parameters contain unsupported fields")
    if parameters.get("action") != "deploy":
        raise ValueError("only the reviewed standard deployment action is supported")
    if parameters.get("service_type") != "full":
        raise ValueError("only a full application deployment is supported")

    target_name = _bounded_text(parameters.get("target"), maximum=32, field="target")
    target_commit = _bounded_text(
        parameters.get("target_commit"),
        maximum=64,
        field="target_commit",
    ).lower()
    target_identity = _bounded_text(
        parameters.get("target_identity"),
        maximum=64,
        field="target_identity",
    ).lower()
    source_tree = _bounded_text(
        parameters.get("source_tree"),
        maximum=64,
        field="source_tree",
    ).lower()
    skip_workers = parameters.get("skip_workers", False)
    required_agent_hostname = str(parameters.get("required_agent_hostname") or "").strip().lower()
    if not COMMIT_PATTERN.fullmatch(target_commit):
        raise ValueError("target_commit must be an exact Git object identifier")
    if len(target_identity) != 64 or any(character not in "0123456789abcdef" for character in target_identity):
        raise ValueError("target_identity must be a SHA-256 digest")
    if not COMMIT_PATTERN.fullmatch(source_tree):
        raise ValueError("source_tree must be an exact Git tree identifier")
    if not isinstance(skip_workers, bool):
        raise ValueError("skip_workers must be boolean")
    if required_agent_hostname and (
        len(required_agent_hostname) > 253
        or not all(
            character.isascii() and (character.isalnum() or character in {"-", "."})
            for character in required_agent_hostname
        )
        or required_agent_hostname != socket.gethostname().strip().lower()
    ):
        raise ValueError("deployment job is assigned to a different executor host")

    target = reviewed_target(target_name, root)
    worker_environment = normalized_environment()
    if target.environment != worker_environment:
        raise ValueError("deployment target environment does not match worker environment")
    if target.deploy_profile != "full-stack":
        raise ValueError("crawler-only targets cannot run a full application deployment")
    if target.identity != target_identity:
        raise ValueError("deployment target identity changed after review")
    if not identity_file_ready(target):
        raise ValueError("deployment target key is unavailable")

    state = readiness if readiness is not None else deployment_readiness(root)
    snapshot = state.get("snapshot") if isinstance(state, dict) else None
    if not state.get("available") or not state.get("can_deploy") or not isinstance(snapshot, dict):
        raise ValueError("deployment runtime is unavailable")
    if snapshot.get("commit") != target_commit:
        raise ValueError("Git HEAD changed after the deployment plan was reviewed")
    if snapshot.get("source_tree") != source_tree:
        raise ValueError("development files changed after the deployment plan was reviewed")
    return {
        "target": target_name,
        "target_commit": target_commit,
        "target_identity": target_identity,
        "skip_workers": skip_workers,
        "source_tree": source_tree,
        "required_agent_hostname": required_agent_hostname,
    }


def _build_deployment_command(
    reviewed: dict[str, Any],
    *,
    source_commit: str,
    native_intent_token: str | None = None,
    root: Path = PROJECT_ROOT,
) -> list[str]:
    if not COMMIT_PATTERN.fullmatch(source_commit):
        raise ValueError("source_commit must be an exact Git commit identifier")
    powershell = powershell_executable()
    if not powershell:
        raise ValueError("Reviewed PowerShell deployment runtime is unavailable")
    script = root / "deploy_mooncen.ps1"
    if not script.is_file():
        raise ValueError("reviewed deployment script is unavailable")
    command = [
        powershell,
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "deploy",
        "-Target",
        reviewed["target"],
        "-ExpectedCommit",
        reviewed["target_commit"],
        "-SourceCommit",
        source_commit,
        "-ExpectedSourceTree",
        reviewed["source_tree"],
        "-ExpectedTargetIdentity",
        reviewed["target_identity"],
    ]
    if reviewed["skip_workers"]:
        command.append("-SkipWorkers")
    if native_intent_token is not None:
        if re.fullmatch(r"[0-9a-f]{32}", native_intent_token) is None:
            raise ValueError("native deployment intent token is invalid")
        command.extend(["-DeploymentIntentToken", native_intent_token])
    return command


def build_deployment_command(
    parameters: Any,
    *,
    source_commit: str,
    root: Path = PROJECT_ROOT,
    readiness: dict[str, Any] | None = None,
) -> list[str]:
    reviewed = validated_parameters(parameters, root=root, readiness=readiness)
    return _build_deployment_command(
        reviewed,
        source_commit=source_commit,
        root=root,
    )


def _is_transient_database_contention(error: BaseException) -> bool:
    sqlstate = getattr(error, "pgcode", None) or getattr(error, "sqlstate", None)
    return isinstance(error, TRANSIENT_DATABASE_ERROR_TYPES) or sqlstate in TRANSIENT_DATABASE_SQLSTATES


def _database_retry_delay(attempt: int) -> float:
    exponent = max(0, min(attempt - 1, 10))
    return min(
        DATABASE_RETRY_INITIAL_DELAY_SECONDS * (2**exponent),
        DATABASE_RETRY_MAX_DELAY_SECONDS,
    )


def _rollback_retryable_transaction(connection) -> None:
    try:
        connection.rollback()
    except psycopg2.Error:
        logger.exception("PostgreSQL rollback failed while recovering from transient contention")
        raise


def _rollback_best_effort(connection, *, operation_name: str) -> bool:
    try:
        connection.rollback()
        return True
    except psycopg2.Error:
        logger.warning(
            "PostgreSQL rollback failed for best-effort operation=%s",
            operation_name,
            exc_info=True,
        )
        return False


def _run_best_effort_database_transaction(
    connection,
    operation: Callable[[Any], Any],
    *,
    operation_name: str,
    attempts: int = BEST_EFFORT_DATABASE_ATTEMPTS,
) -> tuple[bool, Any]:
    for attempt in range(1, attempts + 1):
        try:
            with connection.cursor() as cursor:
                result = operation(cursor)
            connection.commit()
            return True, result
        except psycopg2.Error as exc:
            if not _rollback_best_effort(
                connection,
                operation_name=operation_name,
            ):
                return False, None
            if not _is_transient_database_contention(exc):
                logger.warning(
                    "Spooling best-effort PostgreSQL operation after database error operation=%s error=%s sqlstate=%s",
                    operation_name,
                    type(exc).__name__,
                    getattr(exc, "pgcode", None),
                )
                return False, None
            if attempt >= attempts:
                logger.warning(
                    "Dropping best-effort PostgreSQL operation after transient contention "
                    "operation=%s attempts=%s error=%s sqlstate=%s",
                    operation_name,
                    attempts,
                    type(exc).__name__,
                    getattr(exc, "pgcode", None),
                )
                return False, None
            time.sleep(_database_retry_delay(attempt))
    raise AssertionError("best-effort database retry loop exited unexpectedly")


def _run_required_database_transaction(
    connection,
    operation: Callable[[Any], Any],
    *,
    operation_name: str,
    attempts: int = REQUIRED_DATABASE_ATTEMPTS,
) -> Any:
    for attempt in range(1, attempts + 1):
        try:
            with connection.cursor() as cursor:
                result = operation(cursor)
            connection.commit()
            return result
        except psycopg2.Error as exc:
            _rollback_retryable_transaction(connection)
            if not _is_transient_database_contention(exc):
                raise
            if attempt >= attempts:
                raise
            logger.warning(
                "Retrying required PostgreSQL operation after transient contention "
                "operation=%s attempt=%s/%s error=%s sqlstate=%s",
                operation_name,
                attempt,
                attempts,
                type(exc).__name__,
                getattr(exc, "pgcode", None),
            )
            _publish_worker_heartbeat_resilient()
            time.sleep(_database_retry_delay(attempt))
    raise AssertionError("required database retry loop exited unexpectedly")


def _validated_job_id(job_id: str) -> str | None:
    normalized = str(job_id).strip().lower()
    try:
        return normalized if str(UUID(normalized)) == normalized else None
    except ValueError:
        return None


def _bounded_spool_directory(
    relative: Path,
    *,
    root: Path = DEPLOYMENT_WORKER_STATE_ROOT,
) -> Path:
    if relative.is_absolute() or len(relative.parts) != 1 or relative.name in {"", ".", ".."}:
        raise OSError("deployment spool directory is unsafe")
    state_root = _private_worker_state_root(root)
    directory = state_root / relative
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        pass
    metadata = directory.stat()
    if (
        directory.is_symlink()
        or not directory.is_dir()
        or directory.resolve(strict=True).parent != state_root
        or metadata.st_mode & 0o077
        or (os.name != "nt" and metadata.st_uid != os.geteuid())
    ):
        raise OSError("deployment spool directory is unsafe")
    return directory


def _prune_spool_files(directory: Path, *, keep: int = DEPLOYMENT_SPOOL_MAX_FILES) -> None:
    try:
        files = sorted(
            (path for path in directory.iterdir() if path.is_file() and not path.is_symlink()),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        for stale in files[keep:]:
            stale.unlink(missing_ok=True)
    except OSError:
        logger.warning("Unable to prune the bounded deployment spool", exc_info=True)


def _fsync_spool_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_final_conflict_evidence(
    directory: Path,
    job_id: str,
    encoded: bytes,
) -> Path:
    digest = hashlib.sha256(encoded).hexdigest()
    candidates = (
        directory / f"{job_id}.conflict",
        directory / f"{job_id}.{digest}.conflict",
    )
    for destination in candidates:
        if destination.exists() or destination.is_symlink():
            if destination.is_symlink() or not destination.is_file() or destination.stat().st_size > 32_768:
                raise OSError("deployment final-status conflict destination is unsafe")
            if destination.read_bytes() == encoded:
                return destination
            continue
        try:
            with destination.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            continue
        _fsync_spool_directory(directory)
        return destination
    raise OSError("deployment final-status conflict evidence already exists")


def _preserve_pending_final_conflict(path: Path) -> Path:
    if path.is_symlink() or not path.is_file():
        raise OSError("deployment final-status conflict source is unsafe")
    encoded = path.read_bytes()
    if len(encoded) > 32_768:
        raise OSError("deployment final-status conflict record is too large")
    destination = _write_final_conflict_evidence(path.parent, path.stem, encoded)
    path.unlink()
    _fsync_spool_directory(path.parent)
    return destination


def _redact_spool_metadata(value: Any, *, depth: int = 0) -> Any:
    if depth >= 6:
        return "<metadata depth truncated>"
    if isinstance(value, dict):
        return {
            str(key)[:128]: (
                "<redacted>"
                if SENSITIVE_METADATA_KEY.search(str(key))
                else _redact_spool_metadata(item, depth=depth + 1)
            )
            for key, item in list(value.items())[:100]
        }
    if isinstance(value, (list, tuple)):
        return [_redact_spool_metadata(item, depth=depth + 1) for item in value[:100]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(value, maximum=1_000)


def _spool_log(
    job_id: str,
    level: str,
    message: str,
    metadata: dict[str, Any] | None = None,
    *,
    root: Path = DEPLOYMENT_WORKER_STATE_ROOT,
) -> bool:
    normalized_job_id = _validated_job_id(job_id)
    if normalized_job_id is None:
        return False
    record = {
        "schema_version": 1,
        "job_id": normalized_job_id,
        "level": level if level in {"debug", "info", "warning", "error"} else "info",
        "message": redact_text(message, maximum=4_000),
        "metadata_text": json.dumps(
            _redact_spool_metadata(metadata or {}),
            ensure_ascii=True,
            default=str,
            sort_keys=True,
        )[:8_000],
        "spooled_at_epoch": int(time.time()),
    }
    encoded = (json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n").encode("utf-8")
    if len(encoded) > DEPLOYMENT_SPOOL_MAX_BYTES:
        return False
    try:
        directory = _bounded_spool_directory(DEPLOYMENT_SPOOL_DIRECTORY, root=root)
        path = directory / f"{normalized_job_id}.jsonl"
        if path.is_symlink():
            raise OSError("deployment log spool path is a symbolic link")
        current_size = path.stat().st_size if path.exists() else 0
        if current_size + len(encoded) > DEPLOYMENT_SPOOL_MAX_BYTES:
            logger.warning(
                "Deployment log spool reached its per-job bound job_id=%s bytes=%s",
                normalized_job_id,
                DEPLOYMENT_SPOOL_MAX_BYTES,
            )
            return False
        with path.open("ab") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        _prune_spool_files(directory)
        return True
    except OSError:
        logger.warning(
            "Unable to persist a deployment log in the bounded local spool job_id=%s",
            normalized_job_id,
            exc_info=True,
        )
        return False


def _flush_spooled_logs(
    connection,
    job_id: str,
    *,
    root: Path = DEPLOYMENT_WORKER_STATE_ROOT,
) -> bool:
    normalized_job_id = _validated_job_id(job_id)
    if normalized_job_id is None:
        return False
    try:
        directory = _bounded_spool_directory(DEPLOYMENT_SPOOL_DIRECTORY, root=root)
        path = directory / f"{normalized_job_id}.jsonl"
        if not path.exists():
            return True
        if path.is_symlink() or not path.is_file() or path.stat().st_size > DEPLOYMENT_SPOOL_MAX_BYTES:
            raise OSError("deployment log spool file is unsafe")
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, UnicodeError, json.JSONDecodeError):
        logger.warning(
            "Unable to read the bounded deployment log spool job_id=%s",
            normalized_job_id,
            exc_info=True,
        )
        return False

    def insert_spooled(cursor) -> None:
        for record in records:
            if record.get("job_id") != normalized_job_id:
                raise ValueError("deployment spool job id mismatch")
            cursor.execute(
                """
                INSERT INTO ops_job_logs (job_id, log_level, message, metadata)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    normalized_job_id,
                    record.get("level", "info"),
                    redact_text(record.get("message"), maximum=4_000),
                    Json(
                        {
                            "spooled": True,
                            "original_metadata": redact_text(record.get("metadata_text"), maximum=8_000),
                            "spooled_at_epoch": record.get("spooled_at_epoch"),
                        }
                    ),
                ),
            )

    try:
        written, _ = _run_best_effort_database_transaction(
            connection,
            insert_spooled,
            operation_name="deployment spooled log flush",
        )
    except ValueError:
        logger.warning("Deployment log spool validation failed", exc_info=True)
        return False
    if not written:
        return False
    try:
        path.unlink()
    except OSError:
        logger.warning("Flushed deployment spool could not be removed", exc_info=True)
        return False
    return True


def _try_reconnect_queue(connection):
    global _DATABASE_RECONNECT_RETRY_AFTER
    now = time.monotonic()
    if now < _DATABASE_RECONNECT_RETRY_AFTER:
        return connection
    try:
        replacement = connect_queue()
    except (OSError, ValueError, RuntimeError, psycopg2.Error):
        _DATABASE_RECONNECT_RETRY_AFTER = now + DATABASE_RECONNECT_COOLDOWN_SECONDS
        logger.warning("Deployment queue reconnect failed; child process will continue", exc_info=True)
        return connection
    _DATABASE_RECONNECT_RETRY_AFTER = 0.0
    try:
        connection.close()
    except (AttributeError, OSError, psycopg2.Error):
        logger.warning("Unable to close replaced deployment queue connection", exc_info=True)
    return replacement


def _append_log(
    connection,
    job_id: str,
    level: str,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> bool:
    global _LOG_WRITE_RETRY_AFTER
    now = time.monotonic()
    if now < _LOG_WRITE_RETRY_AFTER:
        _spool_log(job_id, level, message, metadata)
        return False

    redacted_message = redact_text(message, maximum=4_000)
    redacted_metadata = Json(_redact_spool_metadata(metadata or {}))

    def insert_log(cursor) -> None:
        cursor.execute(
            """
            INSERT INTO ops_job_logs (job_id, log_level, message, metadata)
            VALUES (%s, %s, %s, %s)
            """,
            (
                job_id,
                level,
                redacted_message,
                redacted_metadata,
            ),
        )

    written, _ = _run_best_effort_database_transaction(
        connection,
        insert_log,
        operation_name="deployment job log insert",
    )
    if written:
        _LOG_WRITE_RETRY_AFTER = 0.0
    else:
        _LOG_WRITE_RETRY_AFTER = time.monotonic() + LOG_WRITE_RETRY_COOLDOWN_SECONDS
        _spool_log(job_id, level, message, metadata)
    return written


def _claim_job(connection, config: WorkerConfig) -> dict[str, Any] | None:
    if config.agent_id is None:
        return None
    agent_clause = "AND agent_id IS NULL"
    params: list[Any] = [config.environment]
    if config.agent_id:
        agent_clause = """
              AND (
                    agent_id IS NULL
                    OR agent_id = %s
                    OR NOT EXISTS (
                        SELECT 1
                        FROM ops_agents AS owner
                        WHERE owner.id = ops_jobs.agent_id
                          AND owner.environment = ops_jobs.environment
                          AND owner.status = 'healthy'
                          AND owner.last_seen_at >= CURRENT_TIMESTAMP - INTERVAL '2 minutes'
                          AND owner.capabilities @> '["deployment_queue"]'::jsonb
                    )
              )
        """
        params.append(str(config.agent_id))
    params.append(socket.gethostname().strip().lower())
    mode_clause = (
        "AND parameters->>'deployment_mode' = 'container'"
        if config.container_only
        else ""
    )
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute(
            f"""
            SELECT id::text, job_type, status, environment, parameters,
                   target_key, max_retries
            FROM ops_jobs
            WHERE status = 'queued'
              AND environment = %s
              AND job_type = 'deployment'
              {mode_clause}
              {agent_clause}
              AND (
                    parameters->>'required_agent_hostname' IS NULL
                    OR lower(parameters->>'required_agent_hostname') = %s
              )
            ORDER BY queued_at, created_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """,
            params,
        )
        row = cursor.fetchone()
        if not row:
            connection.commit()
            return None
        runtime_lock_key = f"mooncen:ops:deployment-runtime:{config.environment}:{row['target_key']}"
        cursor.execute(
            "SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0))",
            (runtime_lock_key,),
        )
        if cursor.fetchone()[0] is not True:
            connection.rollback()
            return None
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM ops_jobs conflicting
                WHERE conflicting.id <> %s
                  AND conflicting.environment = %s
                  AND conflicting.target_key = %s
                  AND conflicting.job_type = 'deployment'
                  AND conflicting.status IN ('assigned', 'running')
            )
            """,
            (row["id"], config.environment, row["target_key"]),
        )
        if cursor.fetchone()[0] is True:
            connection.rollback()
            return None
        lease_token = uuid4()
        cursor.execute(
            """
            UPDATE ops_jobs
            SET status = 'assigned',
                agent_id = %s,
                lease_token = %s,
                lease_epoch = nextval('ops_container_deployment_lease_epoch_seq'),
                leased_until = CURRENT_TIMESTAMP + make_interval(secs => %s),
                assigned_at = CURRENT_TIMESTAMP,
                heartbeat_at = CURRENT_TIMESTAMP,
                progress = 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND status = 'queued'
            RETURNING id::text, job_type, status, environment, parameters,
                      target_key, max_retries, agent_id::text, lease_token::text,
                      lease_epoch
            """,
            (
                str(config.agent_id),
                str(lease_token),
                config.stale_after_seconds,
                row["id"],
            ),
        )
        claimed_row = cursor.fetchone()
        if claimed_row is None:
            connection.rollback()
            return None
        cursor.execute(
            """
            UPDATE ops_deployments
            SET deployment_status = 'running',
                started_at = COALESCE(started_at, CURRENT_TIMESTAMP)
            WHERE job_id = %s
              AND deployment_status = 'queued'
            RETURNING id
            """,
            (row["id"],),
        )
        if cursor.fetchone() is None:
            connection.rollback()
            return None
    connection.commit()
    claimed = dict(claimed_row)
    claimed["_lease_seconds"] = config.stale_after_seconds
    return claimed


def _recover_stale_jobs(
    connection,
    config: WorkerConfig,
    *,
    stale_after_seconds: int,
) -> int:
    # A dead/replaced agent cannot recover its own lease. Every healthy worker
    # for the environment may therefore fence and expire any globally stale
    # deployment; the timestamp predicate protects fresh owners.
    params: list[Any] = [config.environment, stale_after_seconds]
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute(
            """
            WITH stale AS (
                SELECT id
                FROM ops_jobs
                WHERE status IN ('assigned', 'running')
                  AND environment = %s
                  AND job_type = 'deployment'
                  AND COALESCE(parameters->>'deployment_mode', 'native') <> 'container'
                  AND leased_until <= CURRENT_TIMESTAMP
                  AND COALESCE(
                      heartbeat_at, started_at, assigned_at, updated_at, created_at
                  ) < CURRENT_TIMESTAMP - make_interval(secs => %s)
                FOR UPDATE SKIP LOCKED
            )
            UPDATE ops_jobs AS job
            SET status = 'timed_out',
                error_code = 'worker_heartbeat_expired',
                error_message = 'Deployment worker heartbeat expired before completion.',
                heartbeat_at = CURRENT_TIMESTAMP,
                lease_token = NULL,
                leased_until = NULL,
                finished_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            FROM stale
            WHERE job.id = stale.id
            RETURNING job.id::text
            """,
            params,
        )
        recovered = [dict(row) for row in cursor.fetchall()]
        for job in recovered:
            cursor.execute(
                """
                UPDATE ops_deployments
                SET deployment_status = 'failed',
                    finished_at = CURRENT_TIMESTAMP
                WHERE job_id = %s
                  AND deployment_status IN ('queued', 'running')
                """,
                (job["id"],),
            )
    connection.commit()
    for job in recovered:
        _append_log(
            connection,
            job["id"],
            "error",
            "이전 배포 worker의 heartbeat가 만료되어 작업을 timed_out 상태로 정리했습니다.",
            {"reason": "worker_heartbeat_expired", "stale_after_seconds": stale_after_seconds},
        )
    return len(recovered)


def _rotate_remote_worker_lease(
    config: WorkerConfig,
    *,
    job_id: str,
    lease_epoch: int,
    lease_token: str,
    action: str,
) -> dict[str, Any]:
    """Wait out older controller calls, then rotate one durable remote epoch."""

    command = build_container_worker_lease_claim_command(
        job_id=job_id,
        lease_epoch=lease_epoch,
        lease_token=lease_token,
        action=action,
    )
    try:
        completed = subprocess.run(  # noqa: S603 - fixed forced-command argv.
            command,
            cwd=PROJECT_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=config.command_timeout + 60,
            shell=False,
            env=_deployment_subprocess_environment(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ContainerDeploymentError("remote deployment lease fence is unavailable") from exc
    if completed.returncode != 0 or completed.stderr:
        raise ContainerDeploymentError("remote deployment lease fence failed closed")
    result = parse_container_worker_lease_claim_result(
        _single_controller_output_line(completed.stdout),
        job_id=job_id,
        lease_epoch=lease_epoch,
        lease_token=lease_token,
        active=action == "lease-bind",
    )
    status_value = read_container_controller_status(
        timeout_seconds=15,
        transport_profile="deploy",
    )
    if status_value is None:
        raise ContainerDeploymentError("remote deployment lease status is unavailable")
    assert_container_worker_lease_claim(
        status_value,
        job_id=job_id,
        lease_epoch=lease_epoch,
        lease_token=lease_token,
        active=action == "lease-bind",
    )
    return result


def _fence_remote_worker_lease(
    config: WorkerConfig,
    evidence: ContainerExecutionEvidence,
) -> bool:
    """Prove this claim ended, or that a strictly newer epoch superseded it."""

    try:
        _rotate_remote_worker_lease(
            config,
            job_id=evidence.job_id,
            lease_epoch=evidence.lease_epoch,
            lease_token=evidence.lease_token,
            action="lease-release",
        )
        return True
    except ContainerDeploymentError:
        status_value = read_container_controller_status(
            timeout_seconds=15,
            transport_profile="deploy",
        )
        if status_value is None:
            return False
        observed = status_value.get("worker_lease")
        if not isinstance(observed, dict):
            return False
        observed_epoch = observed.get("claim_epoch")
        if type(observed_epoch) is int and observed_epoch > evidence.lease_epoch:
            # A higher epoch can only be published while holding the remote
            # exclusive control lock, after this claim's command exited.
            return True
        try:
            assert_container_worker_lease(
                status_value,
                evidence,
                active=False,
            )
        except ContainerDeploymentError:
            return False
        return True


def _claim_stale_container_job(
    connection,
    config: WorkerConfig,
    *,
    stale_after_seconds: int,
) -> dict[str, Any] | None:
    if config.agent_id is None:
        return None
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute(
            """
            SELECT id::text, job_type, status, environment, parameters,
                   target_key, max_retries, agent_id::text,
                   lease_token::text, lease_epoch
            FROM ops_jobs
            WHERE status IN ('assigned', 'running')
              AND environment = %s
              AND job_type = 'deployment'
              AND parameters->>'deployment_mode' = 'container'
              AND leased_until <= CURRENT_TIMESTAMP
              AND COALESCE(
                  heartbeat_at, started_at, assigned_at, updated_at, created_at
              ) < CURRENT_TIMESTAMP - make_interval(secs => %s)
            ORDER BY COALESCE(
                heartbeat_at, started_at, assigned_at, updated_at, created_at
            )
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """,
            (config.environment, stale_after_seconds),
        )
        row = cursor.fetchone()
        if row is None:
            connection.commit()
            return None
        stale = dict(row)
        cursor.execute(
            "SELECT nextval('ops_container_deployment_lease_epoch_seq') AS claim_epoch"
        )
        epoch_row = cursor.fetchone()
        new_epoch = int(epoch_row["claim_epoch"])
        new_token = str(uuid4())
        try:
            # This exclusive cloud-side fence waits for every command carrying
            # the old shared token to exit before DB ownership can move.
            _rotate_remote_worker_lease(
                config,
                job_id=str(stale["id"]),
                lease_epoch=new_epoch,
                lease_token=new_token,
                action="lease-bind",
            )
        except ContainerDeploymentError:
            connection.rollback()
            logger.warning(
                "Stale container claim could not fence the remote owner job_id=%s",
                stale["id"],
                exc_info=True,
            )
            return None
        cursor.execute(
            """
            UPDATE ops_jobs
            SET status = 'running',
                agent_id = %s,
                lease_token = %s,
                lease_epoch = %s,
                leased_until = CURRENT_TIMESTAMP + make_interval(secs => %s),
                heartbeat_at = CURRENT_TIMESTAMP,
                progress = GREATEST(progress, 90),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
              AND status IN ('assigned', 'running')
              AND agent_id = %s::uuid
              AND lease_token = %s::uuid
              AND lease_epoch = %s
              AND leased_until <= CURRENT_TIMESTAMP
            RETURNING id::text, job_type, status, environment, parameters,
                      target_key, max_retries, agent_id::text,
                      lease_token::text, lease_epoch
            """,
            (
                str(config.agent_id),
                new_token,
                new_epoch,
                stale_after_seconds,
                stale["id"],
                stale["agent_id"],
                stale["lease_token"],
                stale["lease_epoch"],
            ),
        )
        claimed_row = cursor.fetchone()
        if claimed_row is None:
            connection.rollback()
            return None
        cursor.execute(
            """
            UPDATE ops_deployments
            SET deployment_status = 'running',
                started_at = COALESCE(started_at, CURRENT_TIMESTAMP)
            WHERE job_id = %s
              AND deployment_status = 'queued'
            """,
            (stale["id"],),
        )
        cursor.execute(
            """
            SELECT deployment_status
            FROM ops_deployments
            WHERE job_id = %s
            FOR UPDATE
            """,
            (stale["id"],),
        )
        deployment_row = cursor.fetchone()
        deployment_status = (
            deployment_row.get("deployment_status")
            if deployment_row is not None
            else None
        )
        if deployment_status != "running":
            connection.rollback()
            return None
    connection.commit()
    claimed = dict(claimed_row)
    claimed["_lease_seconds"] = stale_after_seconds
    claimed["_remote_fence_confirmed"] = True
    return claimed


def _reconcile_stale_container_job(
    connection,
    config: WorkerConfig,
    *,
    stale_after_seconds: int,
):
    job = _claim_stale_container_job(
        connection,
        config,
        stale_after_seconds=stale_after_seconds,
    )
    if job is None:
        return connection, 0
    try:
        evidence = load_container_execution_evidence(
            connection,
            job,
            development_target_identity=_configured_container_development_identity(),
            require_fresh=False,
        )
    except (ContainerDeploymentError, ValueError) as exc:
        connection = _report_log_with_reconnect(
            connection,
            str(job["id"]),
            "error",
            "Stale container job evidence could not be revalidated; terminal state was not guessed.",
            {"error": str(exc), "error_code": "container_reconciliation_evidence_invalid"},
        )
        return connection, 0
    connection = _report_log_with_reconnect(
        connection,
        str(job["id"]),
        "warning",
        "A stale container worker lease was fenced; reconciling the durable remote controller state.",
        {"stale_after_seconds": stale_after_seconds},
    )
    connection, terminal = _reconcile_container_job(
        connection,
        job,
        evidence,
        started=time.monotonic(),
        return_code=None,
        wait_for_guard=True,
        authoritative_fence=bool(job.get("_remote_fence_confirmed")),
    )
    if terminal and job.get("_final_persisted") is True:
        _fence_remote_worker_lease(config, evidence)
    return connection, 1 if terminal else 0


def _mark_running(connection, job: dict[str, Any]) -> bool:
    job_id, lease_token, lease_epoch, agent_id, lease_seconds = _job_lease_identity(job)
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
              AND status = 'assigned'
              AND agent_id = %s::uuid
              AND lease_token = %s::uuid
              AND lease_epoch = %s
              AND leased_until > CURRENT_TIMESTAMP
            """,
            (lease_seconds, job_id, agent_id, lease_token, lease_epoch),
        )
        claimed = cursor.rowcount
        if claimed != 1:
            connection.rollback()
            return False
        cursor.execute(
            """
            UPDATE ops_deployments
            SET deployment_status = 'running',
                started_at = CURRENT_TIMESTAMP
            WHERE job_id = %s
              AND deployment_status = 'queued'
            """,
            (job_id,),
        )
    connection.commit()
    return True


def _record_source_snapshot(
    connection,
    job_id: str,
    *,
    source_commit: str,
    source_tree: str,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE ops_deployments
            SET target_commit = %s,
                target_version = %s
            WHERE job_id = %s
              AND deployment_status IN ('queued', 'running')
            """,
            (
                source_commit,
                f"worktree@{source_commit[:12]}",
                job_id,
            ),
        )
    connection.commit()
    _append_log(
        connection,
        job_id,
        "info",
        "현재 개발 파일을 불변 배포 스냅샷으로 고정했습니다.",
        {
            "source_commit": source_commit,
            "source_tree": source_tree,
        },
    )


def _heartbeat(connection, job: dict[str, Any], progress: int) -> JobLeaseRefresh:
    try:
        job_id, lease_token, lease_epoch, agent_id, lease_seconds = _job_lease_identity(job)
    except DeploymentOwnershipLost:
        return JobLeaseRefresh.OWNERSHIP_LOST

    def update_heartbeat(cursor) -> int:
        cursor.execute(
            """
            UPDATE ops_jobs
            SET heartbeat_at = CURRENT_TIMESTAMP,
                leased_until = CURRENT_TIMESTAMP + make_interval(secs => %s),
                progress = GREATEST(progress, %s),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
              AND status IN ('assigned', 'running')
              AND agent_id = %s::uuid
              AND lease_token = %s::uuid
              AND lease_epoch = %s
              AND leased_until > CURRENT_TIMESTAMP
            """,
            (
                lease_seconds,
                progress,
                job_id,
                agent_id,
                lease_token,
                lease_epoch,
            ),
        )
        return cursor.rowcount

    available, updated_rows = _run_best_effort_database_transaction(
        connection,
        update_heartbeat,
        operation_name="deployment job heartbeat",
    )
    if not available:
        return JobLeaseRefresh.UNAVAILABLE
    if updated_rows == 1:
        return JobLeaseRefresh.REFRESHED
    return JobLeaseRefresh.OWNERSHIP_LOST


def _refresh_job_lease_with_reconnect(connection, job: dict[str, Any], progress: int):
    lease_refresh = _heartbeat(connection, job, progress)
    if lease_refresh is not JobLeaseRefresh.UNAVAILABLE:
        return connection, lease_refresh
    replacement = _try_reconnect_queue(connection)
    if replacement is connection:
        return connection, lease_refresh
    return replacement, _heartbeat(replacement, job, progress)


def _cancellation_requested(connection, job: dict[str, Any]) -> bool:
    try:
        job_id, lease_token, lease_epoch, agent_id, _lease_seconds = _job_lease_identity(job)
    except DeploymentOwnershipLost:
        return False

    def read_cancellation(cursor):
        cursor.execute(
            """
            SELECT cancel_requested_at IS NOT NULL
            FROM ops_jobs
            WHERE id = %s
              AND status IN ('assigned', 'running')
              AND agent_id = %s::uuid
              AND lease_token = %s::uuid
              AND lease_epoch = %s
              AND leased_until > CURRENT_TIMESTAMP
            """,
            (job_id, agent_id, lease_token, lease_epoch),
        )
        return cursor.fetchone()

    read, row = _run_best_effort_database_transaction(
        connection,
        read_cancellation,
        operation_name="deployment cancellation check",
    )
    if not read:
        return False
    return bool(row and row[0])


def _finish_job(
    connection,
    job: dict[str, Any],
    *,
    final_status: str,
    return_code: int | None,
    duration_seconds: float,
    detail: str = "",
    source_commit: str = "",
    error_code: str = "",
    container_result: dict[str, Any] | None = None,
) -> str:
    job_id, lease_token, lease_epoch, agent_id, _lease_seconds = _job_lease_identity(job)
    parameters = job.get("parameters") or {}
    result = {
        "return_code": return_code,
        "target": parameters.get("target"),
        "target_commit": parameters.get("target_commit"),
        "duration_seconds": round(duration_seconds, 3),
        "source_commit": source_commit or None,
        "source_tree": parameters.get("source_tree"),
        "claim_epoch": lease_epoch,
    }
    if parameters.get("deployment_mode") == "container":
        result.update(
            {
                "deployment_mode": "container",
                "deployment_action": parameters.get("action"),
                "target_runtime_kind": parameters.get("target_runtime_kind"),
                "native_baseline_identity": parameters.get("native_baseline_identity"),
                "release_digest": parameters.get("release_digest"),
                "previous_release_digest": parameters.get("current_release_digest"),
                **(container_result or {}),
            }
        )
    deployment_status = {
        "success": "success",
        "cancelled": "cancelled",
        "blocked": "blocked",
    }.get(final_status, "failed")
    error_message = redact_text(detail, maximum=2_000) or None
    expected_error_code = None if final_status == "success" else (error_code or final_status)

    def finish(cursor) -> str:
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
              AND status IN ('assigned', 'running')
              AND agent_id = %s::uuid
              AND lease_token = %s::uuid
              AND lease_epoch = %s
              AND leased_until > CURRENT_TIMESTAMP
            RETURNING status
            """,
            (
                final_status,
                final_status,
                Json(result),
                expected_error_code,
                error_message,
                job_id,
                agent_id,
                lease_token,
                lease_epoch,
            ),
        )
        if cursor.fetchone() is None:
            cursor.execute(
                """
                SELECT status, error_code, result
                FROM ops_jobs
                WHERE id = %s
                """,
                (job_id,),
            )
            current = cursor.fetchone()
            if current is None:
                raise RuntimeError("deployment job disappeared before finalization")
            current_status, current_error_code, current_result = current
            current_result = current_result if isinstance(current_result, dict) else {}
            if (
                current_status == final_status
                and current_error_code == expected_error_code
                and current_result.get("return_code") == return_code
                and current_result.get("controller_state_sha256")
                == (container_result or {}).get("controller_state_sha256")
            ):
                return "idempotent"
            return f"conflict:{current_status}"
        if parameters.get("deployment_mode") == "container":
            cursor.execute(
                """
                UPDATE ops_deployments
                SET deployment_status = %s,
                    runtime_generation = %s,
                    activated_release_digest = %s,
                    runtime_previous_release_digest = %s,
                    controller_state_sha256 = %s,
                    runtime_target_kind = %s,
                    runtime_native_baseline_identity = %s,
                    finished_at = CURRENT_TIMESTAMP
                WHERE job_id = %s
                  AND deployment_mode = 'container'
                """,
                (
                    deployment_status,
                    (container_result or {}).get("runtime_generation"),
                    (container_result or {}).get("activated_release_digest"),
                    (container_result or {}).get("runtime_previous_release_digest"),
                    (container_result or {}).get("controller_state_sha256"),
                    (container_result or {}).get("runtime_target_kind"),
                    (container_result or {}).get("runtime_native_baseline_identity"),
                    job_id,
                ),
            )
        else:
            cursor.execute(
                """
                UPDATE ops_deployments
                SET deployment_status = %s,
                    finished_at = CURRENT_TIMESTAMP
                WHERE job_id = %s
                """,
                (deployment_status, job_id),
            )
        return "written"

    disposition = _run_required_database_transaction(
        connection,
        finish,
        operation_name="deployment final status",
    )
    if disposition == "idempotent":
        return disposition
    if disposition.startswith("conflict:"):
        preserved_status = disposition.split(":", 1)[1]
        logger.warning(
            "Discarding stale deployment final result job_id=%s attempted=%s preserved=%s",
            job_id,
            final_status,
            preserved_status,
        )
        _append_log(
            connection,
            job_id,
            "warning",
            "A stale worker result was ignored because the job was already terminal.",
            {
                "attempted_status": final_status,
                "preserved_status": preserved_status,
                "return_code": return_code,
            },
        )
        return disposition
    _append_log(
        connection,
        job_id,
        "info" if final_status == "success" else "error",
        f"배포 작업이 {final_status} 상태로 종료되었습니다.",
        {"return_code": return_code, "duration_seconds": round(duration_seconds, 3)},
    )
    return disposition


def _encoded_final_status_record(
    job: dict[str, Any],
    *,
    final_status: str,
    return_code: int | None,
    duration_seconds: float,
    detail: str = "",
    source_commit: str = "",
    error_code: str = "",
    container_result: dict[str, Any] | None = None,
) -> tuple[str, bytes] | None:
    try:
        job_id, lease_token, lease_epoch, agent_id, lease_seconds = _job_lease_identity(job)
    except DeploymentOwnershipLost:
        return None
    parameters = job.get("parameters") or {}
    payload = {
        "schema_version": 1,
        "job": {
            "id": job_id,
            "agent_id": agent_id,
            "lease_token": lease_token,
            "lease_epoch": lease_epoch,
            "_lease_seconds": lease_seconds,
            "parameters": {
                "target": parameters.get("target"),
                "target_commit": parameters.get("target_commit"),
                "source_tree": parameters.get("source_tree"),
                "deployment_mode": parameters.get("deployment_mode"),
                "action": parameters.get("action"),
                "release_digest": parameters.get("release_digest"),
                "current_release_digest": parameters.get("current_release_digest"),
            },
        },
        "final_status": final_status,
        "return_code": return_code,
        "duration_seconds": round(duration_seconds, 3),
        "detail": redact_text(detail, maximum=2_000),
        "source_commit": source_commit,
        "error_code": error_code,
        "container_result": container_result,
        "spooled_at_epoch": int(time.time()),
    }
    encoded = (json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n").encode("utf-8")
    if len(encoded) > 32_768:
        return None
    return job_id, encoded


def _spool_final_status(
    job: dict[str, Any],
    *,
    final_status: str,
    return_code: int | None,
    duration_seconds: float,
    detail: str = "",
    source_commit: str = "",
    error_code: str = "",
    container_result: dict[str, Any] | None = None,
    root: Path = DEPLOYMENT_WORKER_STATE_ROOT,
) -> bool:
    global _FINAL_STATUS_SPOOL_ERROR_AT
    record = _encoded_final_status_record(
        job,
        final_status=final_status,
        return_code=return_code,
        duration_seconds=duration_seconds,
        detail=detail,
        source_commit=source_commit,
        error_code=error_code,
        container_result=container_result,
    )
    if record is None:
        _FINAL_STATUS_SPOOL_ERROR_AT = time.time()
        return False
    job_id, encoded = record
    try:
        directory = _bounded_spool_directory(
            DEPLOYMENT_PENDING_FINAL_DIRECTORY,
            root=root,
        )
        destination = directory / f"{job_id}.json"
        if destination.is_symlink():
            raise OSError("deployment final-status spool path is a symbolic link")
        temporary = directory / f".{job_id}.{os.getpid()}.tmp"
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()
        with temporary.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        _fsync_spool_directory(directory)
        _prune_spool_files(directory)
        return True
    except OSError:
        _FINAL_STATUS_SPOOL_ERROR_AT = time.time()
        logger.error(
            "Unable to persist pending deployment final status job_id=%s",
            job_id,
            exc_info=True,
        )
        return False


def _spool_final_conflict_evidence(
    job: dict[str, Any],
    *,
    final_status: str,
    return_code: int | None,
    duration_seconds: float,
    detail: str = "",
    source_commit: str = "",
    error_code: str = "",
    container_result: dict[str, Any] | None = None,
    root: Path = DEPLOYMENT_WORKER_STATE_ROOT,
) -> bool:
    global _FINAL_STATUS_SPOOL_ERROR_AT
    record = _encoded_final_status_record(
        job,
        final_status=final_status,
        return_code=return_code,
        duration_seconds=duration_seconds,
        detail=detail,
        source_commit=source_commit,
        error_code=error_code,
        container_result=container_result,
    )
    if record is None:
        _FINAL_STATUS_SPOOL_ERROR_AT = time.time()
        return False
    job_id, encoded = record
    try:
        directory = _bounded_spool_directory(
            DEPLOYMENT_PENDING_FINAL_DIRECTORY,
            root=root,
        )
        _write_final_conflict_evidence(directory, job_id, encoded)
        _prune_spool_files(directory)
        return True
    except OSError:
        _FINAL_STATUS_SPOOL_ERROR_AT = time.time()
        logger.error(
            "Unable to persist conflicting deployment final status job_id=%s",
            job_id,
            exc_info=True,
        )
        return False


def _finish_job_resilient(
    connection,
    job: dict[str, Any],
    *,
    final_status: str,
    return_code: int | None,
    duration_seconds: float,
    detail: str = "",
    source_commit: str = "",
    error_code: str = "",
    container_result: dict[str, Any] | None = None,
):
    arguments = {
        "final_status": final_status,
        "return_code": return_code,
        "duration_seconds": duration_seconds,
        "detail": detail,
        "source_commit": source_commit,
    }
    if error_code:
        arguments["error_code"] = error_code
    if container_result is not None:
        arguments["container_result"] = container_result
    try:
        disposition = _finish_job(connection, job, **arguments)
        if disposition.startswith("conflict:"):
            _spool_final_conflict_evidence(job, **arguments)
            job["_final_persisted"] = False
        else:
            job["_final_persisted"] = True
        return connection
    except psycopg2.Error:
        logger.warning(
            "Deployment final status write failed; reconnecting without affecting the child result",
            exc_info=True,
        )
    replacement = _try_reconnect_queue(connection)
    if replacement is not connection:
        try:
            disposition = _finish_job(replacement, job, **arguments)
            if disposition.startswith("conflict:"):
                _spool_final_conflict_evidence(job, **arguments)
                job["_final_persisted"] = False
            else:
                job["_final_persisted"] = True
            return replacement
        except psycopg2.Error:
            logger.warning(
                "Deployment final status retry failed; persisting it locally",
                exc_info=True,
            )
    _spool_final_status(job, **arguments)
    job["_final_persisted"] = False
    return replacement


def _replay_pending_final_statuses(
    connection,
    *,
    root: Path = DEPLOYMENT_WORKER_STATE_ROOT,
) -> int:
    global _FINAL_STATUS_SPOOL_ERROR_AT
    replay_error = False
    try:
        directory = _bounded_spool_directory(
            DEPLOYMENT_PENDING_FINAL_DIRECTORY,
            root=root,
        )
        candidates = list(directory.glob("*.json"))
        unsafe_candidates = [path for path in candidates if path.is_symlink() or not path.is_file()]
        if unsafe_candidates:
            replay_error = True
            logger.error(
                "Unsafe pending deployment final-status paths require manual review count=%s",
                len(unsafe_candidates),
            )
        paths = sorted(
            (path for path in candidates if path.is_file() and not path.is_symlink()),
            key=lambda path: path.stat().st_mtime_ns,
        )[:DEPLOYMENT_SPOOL_MAX_FILES]
    except OSError:
        _FINAL_STATUS_SPOOL_ERROR_AT = time.time()
        logger.warning("Unable to inspect pending deployment final statuses", exc_info=True)
        return 0
    replayed = 0
    for path in paths:
        try:
            if path.stat().st_size > 32_768:
                raise ValueError("pending deployment final-status record is too large")
            payload = json.loads(path.read_text(encoding="utf-8"))
            job = payload["job"]
            if _validated_job_id(str(job.get("id") or "")) != path.stem:
                raise ValueError("pending deployment final-status job id mismatch")
            disposition = _finish_job(
                connection,
                job,
                final_status=str(payload["final_status"]),
                return_code=payload.get("return_code"),
                duration_seconds=float(payload["duration_seconds"]),
                detail=str(payload.get("detail") or ""),
                source_commit=str(payload.get("source_commit") or ""),
                error_code=str(payload.get("error_code") or ""),
                container_result=(
                    payload.get("container_result") if isinstance(payload.get("container_result"), dict) else None
                ),
            )
            if disposition.startswith("conflict:"):
                _preserve_pending_final_conflict(path)
            else:
                path.unlink()
                _fsync_spool_directory(directory)
            _prune_spool_files(directory)
            replayed += 1
        except psycopg2.Error:
            replay_error = True
            logger.warning("Pending deployment final status replay is still offline")
            break
        except (KeyError, TypeError, ValueError, OSError, UnicodeError, json.JSONDecodeError):
            replay_error = True
            logger.error(
                "Pending deployment final status is invalid and requires manual review path=%s",
                path,
                exc_info=True,
            )
    try:
        pending_records_remain = any(directory.glob("*.json"))
    except OSError:
        replay_error = True
        pending_records_remain = True
    if replay_error:
        _FINAL_STATUS_SPOOL_ERROR_AT = time.time()
    elif not pending_records_remain:
        _FINAL_STATUS_SPOOL_ERROR_AT = None
    return replayed


def _progress_for_line(line: str, current: int) -> int:
    lowered = line.lower()
    stages = (
        (("uploading immutable deployment artifact", "uploading"), 25),
        (("activating", "release switch"), 45),
        (("setup_project", "installing python", "npm ci"), 60),
        (("health check", "smoke test", "checking endpoints"), 85),
        (("deployment completed", "deploy complete"), 95),
    )
    for needles, progress in stages:
        if any(needle in lowered for needle in needles):
            return max(current, progress)
    return current


def _line_level(line: str) -> str:
    lowered = line.lower()
    if any(
        token in lowered
        for token in (
            " error",
            "failed",
            "failure",
            "refusing",
            "unsafe_remote_state",
            "recovery_required",
        )
    ):
        return "error"
    if any(
        token in lowered
        for token in (
            " warning",
            "check ",
            "retry",
            "another deployment holds",
            "lock_busy",
        )
    ):
        return "warning"
    return "info"


def _deployment_failure_result(
    return_code: int,
    last_diagnostic: str,
) -> tuple[str, str, str]:
    classified = REMOTE_DEPLOY_EXIT_CODES.get(return_code)
    if classified is None:
        error_code = "deployment_process_failed"
        final_status = "failed"
        default_detail = f"deploy_mooncen.ps1 exited with code {return_code}."
    else:
        error_code, default_detail = classified
        final_status = "blocked"
    diagnostic = redact_text(last_diagnostic, maximum=700).strip()
    if diagnostic:
        return final_status, error_code, f"{default_detail} Last output: {diagnostic}"
    return final_status, error_code, default_detail


def _terminate_active_process() -> None:
    global ACTIVE_PROCESS
    process = ACTIVE_PROCESS
    if process is None or process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                capture_output=True,
                timeout=15,
                shell=False,
            )
        else:
            os.killpg(process.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError, subprocess.SubprocessError):
        pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except ProcessLookupError:
            pass


def _deployment_subprocess_environment(
    *,
    runtime_directory: Path | None = None,
) -> dict[str, str]:
    environment = os.environ.copy()
    for name in tuple(environment):
        if name in DEPLOYMENT_SUBPROCESS_SECRET_NAMES or DEPLOYMENT_SUBPROCESS_SECRET_NAME.search(name):
            environment.pop(name, None)
    if runtime_directory is not None:
        runtime_text = str(runtime_directory)
        environment["MOONCEN_DEPLOY_TEMP_ROOT"] = runtime_text
        environment["TEMP"] = runtime_text
        environment["TMP"] = runtime_text
        environment["TMPDIR"] = runtime_text
    return environment


def _create_deployment_runtime_directory(
    job_id: str,
    *,
    root: Path = DEPLOYMENT_WORKER_STATE_ROOT,
) -> Path:
    normalized_job_id = _validated_job_id(job_id)
    if normalized_job_id is None:
        raise OSError("deployment runtime job id is invalid")
    parent = _bounded_spool_directory(DEPLOYMENT_RUNTIME_DIRECTORY, root=root)
    parent.chmod(0o700)
    if parent.stat().st_mode & 0o077:
        raise OSError("deployment runtime parent permissions are unsafe")
    destination = parent / normalized_job_id
    if destination.exists() or destination.is_symlink():
        raise OSError("deployment runtime directory already exists")
    destination.mkdir(mode=0o700)
    destination.chmod(0o700)
    metadata = destination.stat()
    if destination.is_symlink() or not destination.is_dir() or metadata.st_mode & 0o077:
        raise OSError("deployment runtime directory permissions are unsafe")
    if os.name != "nt" and metadata.st_uid != os.geteuid():
        raise OSError("deployment runtime directory owner is unsafe")
    return destination


def _remove_deployment_runtime_directory(
    path: Path | None,
    *,
    root: Path = DEPLOYMENT_WORKER_STATE_ROOT,
) -> None:
    if path is None:
        return
    expected_parent = (root / DEPLOYMENT_RUNTIME_DIRECTORY).resolve()
    try:
        if path.parent.resolve() != expected_parent or path.is_symlink():
            raise OSError("deployment runtime cleanup path is unsafe")
        shutil.rmtree(path)
    except FileNotFoundError:
        return


def _remove_deployment_runtime_directory_resilient(
    path: Path | None,
    *,
    root: Path = DEPLOYMENT_WORKER_STATE_ROOT,
) -> bool:
    try:
        _remove_deployment_runtime_directory(path, root=root)
    except Exception:  # noqa: BLE001 - cleanup must not mask the finalized job result.
        logger.warning(
            "Unable to remove the private deployment runtime directory",
            exc_info=True,
        )
        return False
    return True


def _report_log_with_reconnect(
    connection,
    job_id: str,
    level: str,
    message: str,
    metadata: dict[str, Any] | None = None,
):
    if _append_log(connection, job_id, level, message, metadata):
        _flush_spooled_logs(connection, job_id)
        return connection
    replacement = _try_reconnect_queue(connection)
    if replacement is not connection:
        _flush_spooled_logs(replacement, job_id)
    return replacement


CONTAINER_RECONCILE_DEADLINE_SECONDS = 16 * 60
CONTAINER_RECONCILE_POLL_SECONDS = 5.0


def _configured_container_development_identity() -> str:
    value = os.getenv("OPS_CONTAINER_DEV_TARGET_IDENTITY", "").strip().lower()
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ContainerDeploymentError("OPS_CONTAINER_DEV_TARGET_IDENTITY is not a canonical SHA-256 identity")
    return value


def _single_controller_output_line(output: str) -> str:
    if len(output.encode("utf-8")) > MAX_CONTROLLER_OUTPUT_BYTES:
        raise ContainerDeploymentError("container controller output exceeded its bound")
    lines = [line for line in output.splitlines() if line]
    if len(lines) != 1:
        raise ContainerDeploymentError("container controller did not emit exactly one result line")
    return lines[0]


def _native_container_database_state(
    connection,
    *,
    environment: str,
    target_name: str,
) -> tuple[int, int]:
    """Return successful history and active container jobs for one native target."""

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'ops_deployments'
                      AND column_name = 'deployment_mode'
                )
                """
            )
            contract_available = bool(cursor.fetchone()[0])
            if not contract_available:
                connection.commit()
                return 0, 0
            cursor.execute(
                """
                SELECT
                    (
                        SELECT count(*)
                        FROM ops_deployments deployment
                        WHERE deployment.environment = %s
                          AND deployment.target_name = %s
                          AND deployment.deployment_mode = 'container'
                          AND deployment.deployment_status = 'success'
                    ),
                    (
                        SELECT count(*)
                        FROM ops_jobs job
                        WHERE job.environment = %s
                          AND job.target_key = %s
                          AND job.job_type = 'deployment'
                          AND job.parameters->>'deployment_mode' = 'container'
                          AND job.status IN ('queued', 'assigned', 'running')
                    )
                """,
                (
                    environment,
                    target_name,
                    environment,
                    f"deployment:{target_name}",
                ),
            )
            successful_count, active_count = cursor.fetchone()
        connection.commit()
    except psycopg2.Error as exc:
        connection.rollback()
        raise NativeDeploymentBlockedByContainerRuntime(
            "Container deployment database state could not be verified."
        ) from exc
    return int(successful_count or 0), int(active_count or 0)


def _assert_native_deployment_not_mixed_with_container(
    connection,
    job: dict[str, Any],
) -> bool:
    parameters = job.get("parameters") or {}
    target_name = str(parameters.get("target") or "")
    if not target_name:
        raise NativeDeploymentBlockedByContainerRuntime(
            "Native deployment target is unavailable for container-state verification."
        )
    successful_count, active_count = _native_container_database_state(
        connection,
        environment=str(job.get("environment") or normalized_environment()),
        target_name=target_name,
    )
    if active_count:
        raise NativeDeploymentBlockedByContainerRuntime("A container deployment for the same target is non-terminal.")
    status_value = read_container_controller_status(
        timeout_seconds=15,
        transport_profile="deploy",
    )
    if status_value is None:
        controller_presence = read_container_controller_presence(
            timeout_seconds=15,
            transport_profile="deploy",
        )
        if successful_count or controller_presence is not False:
            raise NativeDeploymentBlockedByContainerRuntime(
                "Container controller is present/corrupt or its absence cannot be proven."
            )
        # Before the first reviewed bootstrap the controller is intentionally
        # absent. With no successful/non-terminal container evidence, native is
        # the only runtime that can be active and remains deployable.
        return False
    if (
        status_value.get("transaction") is not None
        or status_value.get("state") is not None
        or status_value.get("native_intent") is not None
    ):
        raise NativeDeploymentBlockedByContainerRuntime(
            "Container runtime, transaction, or native intent is active on the production target."
        )
    return True


def _container_status_once(
    *,
    command: list[str] | None = None,
) -> dict[str, Any] | None:
    status_command = command or build_container_status_command(transport_profile="deploy")
    try:
        completed = subprocess.run(
            status_command,
            cwd=PROJECT_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=45,
            shell=False,
            env=_deployment_subprocess_environment(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0 or completed.stderr.strip():
        return None
    try:
        return parse_container_status(_single_controller_output_line(completed.stdout))
    except ContainerDeploymentError:
        return None


def _finish_reconciled_container_job(
    connection,
    job: dict[str, Any],
    evidence: ContainerExecutionEvidence,
    status_value: dict[str, Any],
    *,
    started: float,
    return_code: int | None,
    authoritative_fence: bool,
):
    disposition = reconcile_container_status(status_value, evidence)
    if disposition == "pending":
        return connection, False
    if disposition == "success":
        state_line = json.dumps(
            status_value["state"],
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        container_result = parse_container_action_result(state_line, evidence)
        connection = _finish_job_resilient(
            connection,
            job,
            final_status="success",
            return_code=return_code,
            duration_seconds=time.monotonic() - started,
            container_result=container_result,
        )
        return connection, True
    if not authoritative_fence:
        # A stale-looking pre-action status is not terminal evidence while an
        # older remote command may still be waiting to acquire its lock.
        return connection, False
    if disposition == "recovered_previous":
        connection = _finish_job_resilient(
            connection,
            job,
            final_status="blocked",
            return_code=return_code,
            duration_seconds=time.monotonic() - started,
            detail="The remote guard converged to the pre-action runtime state.",
            error_code="container_controller_recovered_previous_state",
        )
        return connection, True
    connection = _finish_job_resilient(
        connection,
        job,
        final_status="blocked",
        return_code=return_code,
        duration_seconds=time.monotonic() - started,
        detail="Remote transaction ended in a state outside the approved release transition.",
        error_code="container_recovery_required",
    )
    return connection, True


def _reconcile_container_job(
    connection,
    job: dict[str, Any],
    evidence: ContainerExecutionEvidence,
    *,
    started: float,
    return_code: int | None,
    wait_for_guard: bool,
    authoritative_fence: bool,
):
    deadline = time.monotonic() + (CONTAINER_RECONCILE_DEADLINE_SECONDS if wait_for_guard else 0)
    status_command: list[str] | None = None
    try:
        status_command = build_container_status_command(transport_profile="deploy")
    except ContainerDeploymentError:
        logger.exception("Pinned container status command is unavailable")
    while True:
        status_value = _container_status_once(command=status_command) if status_command is not None else None
        if status_value is not None:
            connection, terminal = _finish_reconciled_container_job(
                connection,
                job,
                evidence,
                status_value,
                started=started,
                return_code=return_code,
                authoritative_fence=authoritative_fence,
            )
            if terminal:
                return connection, True
        if not wait_for_guard or not RUNNING or time.monotonic() >= deadline:
            connection = _report_log_with_reconnect(
                connection,
                str(job["id"]),
                "warning",
                "Remote container state is still reconciling; the job remains non-terminal.",
                {"error_code": "container_reconciliation_pending"},
            )
            return connection, False
        connection, lease_refresh = _refresh_job_lease_with_reconnect(
            connection,
            job,
            95,
        )
        if lease_refresh is JobLeaseRefresh.OWNERSHIP_LOST:
            # Never translate a lost local lease into a false remote terminal
            # state.  Continue the read-only reconciliation and let the exact
            # controller state decide the result.
            logger.warning(
                "Container reconciliation lost its database ownership job_id=%s",
                job["id"],
            )
        time.sleep(CONTAINER_RECONCILE_POLL_SECONDS)


def _run_fixed_container_command(
    connection,
    job: dict[str, Any],
    config: WorkerConfig,
    command: list[str],
    *,
    runtime_directory: Path,
    progress: int,
    action_started: float,
    stdin_upload: ContainerIngressUpload | None = None,
):
    """Run one fixed argv while preserving the database/remote uncertainty boundary."""

    global ACTIVE_PROCESS
    job_id = str(job["id"])
    output_parts: list[str] = []
    output_size = 0
    return_code: int | None = None
    process_started = False
    interrupted = False
    input_descriptor: int | None = None
    try:
        connection, initial_refresh = _refresh_job_lease_with_reconnect(
            connection,
            job,
            progress,
        )
        if initial_refresh is not JobLeaseRefresh.REFRESHED:
            return connection, None, "", False, True
        if stdin_upload is not None:
            input_descriptor = os.open(
                stdin_upload.path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            current = os.fstat(input_descriptor)
            if (
                not stat.S_ISREG(current.st_mode)
                or current.st_dev != stdin_upload.device
                or current.st_ino != stdin_upload.inode
                or current.st_uid != stdin_upload.uid
                or current.st_gid != stdin_upload.gid
                or stat.S_IMODE(current.st_mode) != stdin_upload.mode
                or current.st_size != stdin_upload.size
                or current.st_mtime_ns != stdin_upload.mtime_ns
                or current.st_ctime_ns != stdin_upload.ctime_ns
            ):
                raise ContainerDeploymentError("pinned container upload changed before transport")
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        ACTIVE_PROCESS = subprocess.Popen(  # noqa: S603 - fixed allowlisted argv.
            command,
            cwd=PROJECT_ROOT,
            stdin=input_descriptor if input_descriptor is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            start_new_session=os.name != "nt",
            creationflags=creation_flags,
            env=_deployment_subprocess_environment(
                runtime_directory=runtime_directory,
            ),
        )
        if input_descriptor is not None:
            os.close(input_descriptor)
            input_descriptor = None
        process_started = True
        process = ACTIVE_PROCESS
        assert process.stdout is not None
        output_queue: queue.Queue[str | None] = queue.Queue(maxsize=1_000)

        def read_output() -> None:
            try:
                for output_line in process.stdout:
                    output_queue.put(output_line)
            finally:
                output_queue.put(None)

        reader = Thread(
            target=read_output,
            name=f"ops-container-{job_id}-output",
            daemon=True,
        )
        reader.start()
        output_done = False
        next_control_check = 0.0
        while True:
            try:
                line = output_queue.get(timeout=0.25)
            except queue.Empty:
                line = ""
            if line is None:
                output_done = True
            elif line:
                output_size += len(line.encode("utf-8", errors="replace"))
                if output_size <= MAX_CONTROLLER_OUTPUT_BYTES:
                    output_parts.append(line)
            return_code = process.poll()
            if return_code is not None and output_done:
                break
            now = time.monotonic()
            if now >= next_control_check:
                _publish_worker_heartbeat_resilient()
                connection, lease_refresh = _refresh_job_lease_with_reconnect(
                    connection,
                    job,
                    progress,
                )
                connection = _touch_deployment_agent_resilient(connection, config)
                if (
                    lease_refresh is not JobLeaseRefresh.REFRESHED
                    or now - action_started > config.command_timeout
                    or not RUNNING
                ):
                    # Local SSH process termination does not prove the remote
                    # command stopped. Only status reconciliation may write a
                    # terminal container result from this point onward.
                    interrupted = True
                    _terminate_active_process()
                    return_code = process.poll()
                    break
                next_control_check = time.monotonic() + 2
        reader.join(timeout=1)
    except (OSError, subprocess.SubprocessError):
        logger.warning("Fixed container command could not be started", exc_info=True)
    finally:
        if input_descriptor is not None:
            os.close(input_descriptor)
        _terminate_active_process()
        ACTIVE_PROCESS = None
    if output_size > MAX_CONTROLLER_OUTPUT_BYTES:
        raise ContainerDeploymentError("container controller output exceeded its bound")
    return connection, return_code, "".join(output_parts), process_started, interrupted


def _cleanup_container_ingress(
    command: list[str],
    evidence: ContainerExecutionEvidence,
) -> None:
    """Best-effort cleanup confined to one exact tree-derived ingress directory."""

    if not RUNNING:
        return
    try:
        completed = subprocess.run(  # noqa: S603 - fixed tree-derived argv.
            command,
            cwd=PROJECT_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=15,
            shell=False,
            env=_deployment_subprocess_environment(),
        )
        if completed.returncode != 0 or completed.stderr:
            raise ContainerDeploymentError("forced ingress abort failed")
        parse_container_ingress_result(
            _single_controller_output_line(completed.stdout),
            evidence,
            "abort",
        )
    except (ContainerDeploymentError, OSError, subprocess.SubprocessError):
        logger.warning("Exact container ingress cleanup did not complete", exc_info=True)


def _execute_container_job(
    connection,
    job: dict[str, Any],
    config: WorkerConfig,
):
    job_id = str(job["id"])
    started = time.monotonic()
    runtime_directory: Path | None = None
    remote_started = False
    evidence: ContainerExecutionEvidence | None = None
    ingress_plan: dict[str, Any] | None = None
    try:
        if _mark_running(connection, job) is not True:
            return connection
        evidence = load_container_execution_evidence(
            connection,
            job,
            development_target_identity=_configured_container_development_identity(),
        )
        if evidence.action == "promote":
            release_files = container_release_files(evidence)
            ingress_plan = build_container_ingress_commands(
                evidence,
                release_files=release_files,
            )
        connection = _report_log_with_reconnect(
            connection,
            job_id,
            "info",
            "Exact container evidence was revalidated; starting the fixed remote pipeline.",
            {
                "action": evidence.action,
                "target": evidence.target_name,
                "target_runtime_kind": evidence.target_runtime_kind,
                "release_digest": (
                    evidence.release_digest if evidence.release is not None else None
                ),
                "source_tree": evidence.source_tree if evidence.release is not None else None,
                "native_baseline_identity": evidence.native_baseline_identity,
                "approval_evidence_id": evidence.approval_id,
                "expected_runtime_generation": evidence.expected_runtime_generation,
                "expected_controller_state_sha256": (evidence.expected_controller_state_sha256),
            },
        )
    except (ContainerDeploymentError, ValueError) as exc:
        return _finish_job_resilient(
            connection,
            job,
            final_status="blocked",
            return_code=None,
            duration_seconds=time.monotonic() - started,
            detail=str(exc),
            error_code="container_evidence_revalidation_failed",
        )
    except Exception as exc:
        logger.exception("Container deployment preparation failed job_id=%s", job_id)
        try:
            connection.rollback()
        except psycopg2.Error:
            connection = _try_reconnect_queue(connection)
        return _finish_job_resilient(
            connection,
            job,
            final_status="failed",
            return_code=None,
            duration_seconds=time.monotonic() - started,
            detail=f"{type(exc).__name__}: container deployment preparation failed",
            error_code="container_preparation_failed",
        )

    assert evidence is not None
    return_code: int | None = None
    interrupted = False
    failed_step: str | None = None
    remote_lease_bound = False
    authoritative_fence = False
    try:
        runtime_directory = _create_deployment_runtime_directory(job_id)
        lease_command = build_container_worker_lease_command(evidence, "lease-bind")
        connection, return_code, output, started_command, interrupted = _run_fixed_container_command(
            connection,
            job,
            config,
            lease_command,
            runtime_directory=runtime_directory,
            progress=7,
            action_started=started,
        )
        remote_started = remote_started or started_command
        if interrupted or return_code != 0:
            failed_step = "lease-bind"
        else:
            parse_container_worker_lease_result(
                _single_controller_output_line(output),
                evidence,
                active=True,
            )
            remote_lease_bound = True
            live_status = read_container_controller_status(
                timeout_seconds=15,
                transport_profile="deploy",
            )
            if live_status is None:
                raise ContainerDeploymentError(
                    "container controller status is unavailable after lease bind"
                )
            assert_container_worker_lease(live_status, evidence)
            assert_container_runtime_cas(live_status, evidence)

        if failed_step is None and evidence.action == "promote":
            assert ingress_plan is not None
            # A worker/SSH loss after upload or stage may leave only this
            # exact reviewed tree inbox.  Remove the four allowlisted files
            # and directory before new-only recreation; unknown contents make
            # the subsequent mkdir fail closed.
            _cleanup_container_ingress(ingress_plan["abort"], evidence)
            transport_steps: list[
                tuple[str, list[str], int, ContainerIngressUpload | None]
            ] = [
                ("ingress-prepare", ingress_plan["prepare"], 10, None),
                *[
                    (
                        f"ingress-upload-{index}",
                        list(upload.command),
                        12 + index * 4,
                        upload,
                    )
                    for index, upload in enumerate(ingress_plan["uploads"], start=1)
                ],
            ]
            for step_name, command, progress, upload in transport_steps:
                connection, return_code, output, started_command, interrupted = _run_fixed_container_command(
                    connection,
                    job,
                    config,
                    command,
                    runtime_directory=runtime_directory,
                    progress=progress,
                    action_started=started,
                    stdin_upload=upload,
                )
                remote_started = remote_started or started_command
                if interrupted or return_code != 0:
                    failed_step = step_name
                    break
                result_line = _single_controller_output_line(output)
                parse_container_ingress_result(
                    result_line,
                    evidence,
                    "prepare" if upload is None else "upload",
                    upload=upload,
                )
            if failed_step is None:
                controller_steps = (
                    ("stage", 40),
                    ("load-images", 55),
                    ("preflight", 70),
                    ("promote", 85),
                )
            else:
                controller_steps = ()
        elif failed_step is None and evidence.action == "rollback":
            controller_steps = (("rollback", 80),)
        elif failed_step is None:
            controller_steps = (("rollback-native", 80),)
        else:
            controller_steps = ()

        for step_name, progress in controller_steps:
            command = build_container_controller_command(evidence, step_name)
            connection, return_code, output, started_command, interrupted = _run_fixed_container_command(
                connection,
                job,
                config,
                command,
                runtime_directory=runtime_directory,
                progress=progress,
                action_started=started,
            )
            remote_started = remote_started or started_command
            if interrupted or return_code != 0:
                failed_step = step_name
                break
            result_line = _single_controller_output_line(output)
            if step_name in {"stage", "load-images", "preflight"}:
                parse_container_pipeline_step_result(result_line, evidence, step_name)
            else:
                # This is an additional byte/schema check only. The terminal
                # result is always derived from a separate fixed status read.
                parse_container_action_result(result_line, evidence)
            connection = _report_log_with_reconnect(
                connection,
                job_id,
                "info",
                f"Fixed container pipeline step completed: {step_name}.",
                {"step": step_name, "return_code": return_code},
            )
            if step_name == "stage" and ingress_plan is not None:
                _cleanup_container_ingress(ingress_plan["abort"], evidence)
        if failed_step is None:
            # Every fixed command returned after releasing its shared remote
            # lock; a later terminal status cannot race a still-running call.
            authoritative_fence = True
    except (ContainerDeploymentError, OSError, subprocess.SubprocessError) as exc:
        logger.warning(
            "Container pipeline did not return a directly trusted result job_id=%s: %s",
            job_id,
            exc,
        )
        failed_step = failed_step or "result-validation"
    except Exception:
        logger.exception("Container pipeline execution failed job_id=%s", job_id)
        failed_step = failed_step or "execution"
    finally:
        if ingress_plan is not None and not interrupted:
            _cleanup_container_ingress(ingress_plan["abort"], evidence)
        _remove_deployment_runtime_directory_resilient(runtime_directory)

    if remote_started and failed_step is not None:
        authoritative_fence = _fence_remote_worker_lease(config, evidence)

    if remote_started:
        if failed_step:
            connection = _report_log_with_reconnect(
                connection,
                job_id,
                "warning",
                "Container pipeline step did not complete; reconciling the durable remote state.",
                {
                    "step": failed_step,
                    "return_code": return_code,
                    "interrupted": interrupted,
                },
            )
        connection, terminal = _reconcile_container_job(
            connection,
            job,
            evidence,
            started=started,
            return_code=return_code,
            wait_for_guard=True,
            authoritative_fence=authoritative_fence,
        )
        if terminal and job.get("_final_persisted") is True and remote_lease_bound:
            if not _fence_remote_worker_lease(config, evidence):
                logger.warning(
                    "Terminal container job retained a remote lease until a higher epoch binds job_id=%s",
                    job_id,
                )
        return connection
    return _finish_job_resilient(
        connection,
        job,
        final_status="failed",
        return_code=return_code,
        duration_seconds=time.monotonic() - started,
        detail="Fixed container pipeline could not be started.",
        error_code="container_controller_start_failed",
    )


def execute_job(connection, job: dict[str, Any], config: WorkerConfig):
    if (job.get("parameters") or {}).get("deployment_mode") == "container":
        return _execute_container_job(connection, job, config)
    if config.container_only:
        return _finish_job_resilient(
            connection,
            job,
            final_status="blocked",
            return_code=None,
            duration_seconds=0,
            detail=(
                "Long-lived deployment workers cannot execute native releases; "
                "use the reviewed interactive an2p operator path."
            ),
            error_code="native_deployment_operator_only",
        )
    global ACTIVE_PROCESS
    job_id = str(job["id"])
    started = time.monotonic()
    lease_tracker = JobLeaseTracker(
        config.stale_after_seconds,
        confirmed_at=started,
    )
    source_commit = ""
    deployment_runtime_directory: Path | None = None
    snapshot_reference = f"refs/mooncen/deploy-snapshots/{job_id}"
    try:
        if _mark_running(connection, job) is not True:
            logger.warning(
                "Deployment ownership was lost before preparation job_id=%s",
                job_id,
            )
            return connection
        _publish_worker_heartbeat_resilient()
        connection = _touch_deployment_agent_resilient(connection, config)
        connection, initial_lease_refresh = _refresh_job_lease_with_reconnect(
            connection,
            job,
            5,
        )
        if initial_lease_refresh is JobLeaseRefresh.REFRESHED:
            lease_tracker.confirm()
        elif initial_lease_refresh is JobLeaseRefresh.OWNERSHIP_LOST:
            logger.warning(
                "Deployment ownership was lost immediately after assignment job_id=%s",
                job_id,
            )
            return connection
        connection = _report_log_with_reconnect(
            connection,
            job_id,
            "info",
            "Deployment assigned; preparing the reviewed source snapshot.",
            {"stage": "preparing_snapshot", "progress": 5},
        )
    except Exception as exc:
        logger.exception("Deployment could not enter its preparation stage job_id=%s", job_id)
        try:
            connection.rollback()
        except psycopg2.Error:
            connection = _try_reconnect_queue(connection)
        return _finish_job_resilient(
            connection,
            job,
            final_status="failed",
            return_code=None,
            duration_seconds=time.monotonic() - started,
            detail=f"{type(exc).__name__}: deployment preparation could not start",
            error_code="preparation_start_failed",
        )

    (
        preparation_stop,
        preparation_cancelled,
        preparation_ownership_lost,
        preparation_lease_expired,
        preparation_thread,
    ) = _start_preparation_monitor(
        config,
        job,
        lease_tracker,
    )

    def check_preparation_cancellation() -> None:
        if preparation_cancelled.is_set():
            raise DeploymentPreparationCancelled("Deployment was cancelled while preparing the source snapshot.")
        if preparation_ownership_lost.is_set():
            raise DeploymentOwnershipLost("Deployment ownership was lost while preparing the source snapshot.")
        if preparation_lease_expired.is_set() or lease_tracker.expired():
            raise DeploymentLeaseExpired("Deployment database lease could not be refreshed before its safety deadline.")
        if _cancellation_requested(connection, job):
            raise DeploymentPreparationCancelled("Deployment was cancelled while preparing the source snapshot.")
        if lease_tracker.expired():
            raise DeploymentLeaseExpired("Deployment database lease could not be refreshed before its safety deadline.")

    try:
        check_preparation_cancellation()
        state = deployment_readiness()
        check_preparation_cancellation()
        reviewed = validated_parameters(
            job.get("parameters"),
            readiness=state,
        )
        check_preparation_cancellation()
        source_commit = create_deployment_snapshot_commit(
            expected_base_commit=reviewed["target_commit"],
            expected_source_tree=reviewed["source_tree"],
            reference=snapshot_reference,
        )
        check_preparation_cancellation()
        preserve_deployment_release_reference(
            commit=source_commit,
            source_tree=reviewed["source_tree"],
            base_commit=reviewed["target_commit"],
            job_id=job_id,
            status="deploying",
        )
        check_preparation_cancellation()
        command = _build_deployment_command(
            reviewed,
            source_commit=source_commit,
            native_intent_token=UUID(job_id).hex,
        )
        _record_source_snapshot(
            connection,
            job_id,
            source_commit=source_commit,
            source_tree=reviewed["source_tree"],
        )
        check_preparation_cancellation()
        connection, final_preparation_lease_refresh = _refresh_job_lease_with_reconnect(
            connection,
            job,
            5,
        )
        if final_preparation_lease_refresh is JobLeaseRefresh.REFRESHED:
            lease_tracker.confirm()
        elif final_preparation_lease_refresh is JobLeaseRefresh.OWNERSHIP_LOST:
            raise DeploymentOwnershipLost("Deployment ownership was lost before the remote process started.")
        elif lease_tracker.expired():
            raise DeploymentLeaseExpired("Deployment database lease expired before the remote process started.")
    except DeploymentPreparationCancelled as exc:
        if source_commit:
            release_deployment_snapshot_reference(
                snapshot_reference,
                source_commit,
            )
        connection = _report_log_with_reconnect(
            connection,
            job_id,
            "warning",
            str(exc),
        )
        connection = _finish_job_resilient(
            connection,
            job,
            final_status="cancelled",
            return_code=None,
            duration_seconds=time.monotonic() - started,
            detail=str(exc),
            source_commit=source_commit,
        )
        return connection
    except (DeploymentOwnershipLost, DeploymentLeaseExpired) as exc:
        if source_commit:
            release_deployment_snapshot_reference(
                snapshot_reference,
                source_commit,
            )
        connection = _report_log_with_reconnect(
            connection,
            job_id,
            "error",
            str(exc),
        )
        connection = _finish_job_resilient(
            connection,
            job,
            final_status="timed_out",
            return_code=None,
            duration_seconds=time.monotonic() - started,
            detail=str(exc),
            source_commit=source_commit,
            error_code=(
                "deployment_ownership_lost" if isinstance(exc, DeploymentOwnershipLost) else "deployment_lease_expired"
            ),
        )
        return connection
    except ValueError as exc:
        if source_commit:
            release_deployment_snapshot_reference(
                snapshot_reference,
                source_commit,
            )
        connection = _report_log_with_reconnect(
            connection,
            job_id,
            "error",
            str(exc),
        )
        connection = _finish_job_resilient(
            connection,
            job,
            final_status="blocked",
            return_code=None,
            duration_seconds=time.monotonic() - started,
            detail=str(exc),
            source_commit=source_commit,
        )
        return connection
    except Exception as exc:
        logger.exception("Deployment preparation failed job_id=%s", job_id)
        if source_commit:
            release_deployment_snapshot_reference(
                snapshot_reference,
                source_commit,
            )
        try:
            connection.rollback()
        except psycopg2.Error:
            connection = _try_reconnect_queue(connection)
        connection = _finish_job_resilient(
            connection,
            job,
            final_status="failed",
            return_code=None,
            duration_seconds=time.monotonic() - started,
            detail=f"{type(exc).__name__}: deployment preparation failed",
            source_commit=source_commit,
        )
        return connection
    finally:
        _stop_preparation_monitor(preparation_stop, preparation_thread)

    def finish_after_lease_failure(
        detail: str,
        *,
        error_code: str,
        return_code: int | None = None,
    ):
        nonlocal connection
        _terminate_active_process()
        if return_code is None and ACTIVE_PROCESS is not None:
            return_code = ACTIVE_PROCESS.returncode
        connection = _report_log_with_reconnect(
            connection,
            job_id,
            "error",
            detail,
        )
        connection = _finish_job_resilient(
            connection,
            job,
            final_status="timed_out",
            return_code=return_code,
            duration_seconds=time.monotonic() - started,
            detail=detail,
            source_commit=source_commit,
            error_code=error_code,
        )
        return connection

    connection = _report_log_with_reconnect(
        connection,
        job_id,
        "info",
        "검토된 개발 파일 스냅샷으로 배포를 시작합니다.",
        {
            "target": (job.get("parameters") or {}).get("target"),
            "target_commit": (job.get("parameters") or {}).get("target_commit"),
            "source_commit": source_commit,
            "source_tree": (job.get("parameters") or {}).get("source_tree"),
        },
    )
    line_count = 0
    progress = 10
    last_diagnostic = ""
    try:
        connection, prelaunch_lease_refresh = _refresh_job_lease_with_reconnect(
            connection,
            job,
            progress,
        )
        if prelaunch_lease_refresh is JobLeaseRefresh.REFRESHED:
            lease_tracker.confirm()
        elif prelaunch_lease_refresh is JobLeaseRefresh.OWNERSHIP_LOST:
            return finish_after_lease_failure(
                "Deployment ownership was lost before the remote process started.",
                error_code="deployment_ownership_lost",
            )
        elif lease_tracker.expired():
            return finish_after_lease_failure(
                "Deployment database lease expired before the remote process started.",
                error_code="deployment_lease_expired",
            )
        if _cancellation_requested(connection, job):
            connection = _finish_job_resilient(
                connection,
                job,
                final_status="cancelled",
                return_code=None,
                duration_seconds=time.monotonic() - started,
                detail="Deployment was cancelled before the remote process started.",
                source_commit=source_commit,
            )
            return connection
        if lease_tracker.expired():
            return finish_after_lease_failure(
                "Deployment database lease expired before the remote process started.",
                error_code="deployment_lease_expired",
            )
        _assert_native_deployment_not_mixed_with_container(connection, job)
        deployment_runtime_directory = _create_deployment_runtime_directory(job_id)
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        ACTIVE_PROCESS = subprocess.Popen(  # noqa: S603 - fixed allowlisted argv.
            command,
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            start_new_session=os.name != "nt",
            creationflags=creation_flags,
            env=_deployment_subprocess_environment(
                runtime_directory=deployment_runtime_directory,
            ),
        )
        process = ACTIVE_PROCESS
        assert process.stdout is not None
        output_queue: queue.Queue[str | None] = queue.Queue(maxsize=1_000)

        def read_output() -> None:
            try:
                for output_line in process.stdout:
                    output_queue.put(output_line)
            finally:
                output_queue.put(None)

        reader = Thread(target=read_output, name=f"ops-deploy-{job_id}-output", daemon=True)
        reader.start()
        next_control_check = 0.0
        output_done = False
        while True:
            try:
                line = output_queue.get(timeout=0.25)
            except queue.Empty:
                line = ""
            if line is None:
                output_done = True
            elif line and line_count < 20_000:
                cleaned = line.rstrip()
                if cleaned:
                    last_diagnostic = redact_text(cleaned, maximum=700)
                progress = _progress_for_line(cleaned, progress)
                connection = _report_log_with_reconnect(
                    connection,
                    job_id,
                    _line_level(cleaned),
                    cleaned,
                )
                line_count += 1
            return_code = process.poll()
            if return_code is not None and output_done:
                break
            now = time.monotonic()
            if now >= next_control_check:
                if lease_tracker.expired(now=now):
                    return finish_after_lease_failure(
                        "Deployment database lease expired while the remote process was active.",
                        error_code="deployment_lease_expired",
                        return_code=process.poll(),
                    )
                _publish_worker_heartbeat_resilient()
                connection, lease_refresh = _refresh_job_lease_with_reconnect(
                    connection,
                    job,
                    progress,
                )
                if lease_refresh is JobLeaseRefresh.REFRESHED:
                    lease_tracker.confirm()
                elif lease_refresh is JobLeaseRefresh.OWNERSHIP_LOST:
                    return finish_after_lease_failure(
                        "Deployment ownership was lost while the remote process was active.",
                        error_code="deployment_ownership_lost",
                        return_code=process.poll(),
                    )
                elif lease_tracker.expired():
                    return finish_after_lease_failure(
                        "Deployment database lease expired while the remote process was active.",
                        error_code="deployment_lease_expired",
                        return_code=process.poll(),
                    )
                connection = _touch_deployment_agent_resilient(connection, config)
                if lease_tracker.expired():
                    return finish_after_lease_failure(
                        "Deployment database lease expired while the remote process was active.",
                        error_code="deployment_lease_expired",
                        return_code=process.poll(),
                    )
                if _cancellation_requested(connection, job):
                    connection = _report_log_with_reconnect(
                        connection,
                        job_id,
                        "warning",
                        "취소 요청을 확인하여 배포 프로세스를 종료합니다.",
                    )
                    _terminate_active_process()
                    connection = _finish_job_resilient(
                        connection,
                        job,
                        final_status="cancelled",
                        return_code=ACTIVE_PROCESS.returncode,
                        duration_seconds=now - started,
                        source_commit=source_commit,
                    )
                    return connection
                control_checked_at = time.monotonic()
                if lease_tracker.expired(now=control_checked_at):
                    return finish_after_lease_failure(
                        "Deployment database lease expired while the remote process was active.",
                        error_code="deployment_lease_expired",
                        return_code=process.poll(),
                    )
                if control_checked_at - started > config.command_timeout:
                    connection = _report_log_with_reconnect(
                        connection,
                        job_id,
                        "error",
                        "배포 작업 제한 시간을 초과했습니다.",
                    )
                    _terminate_active_process()
                    connection = _finish_job_resilient(
                        connection,
                        job,
                        final_status="timed_out",
                        return_code=ACTIVE_PROCESS.returncode,
                        duration_seconds=control_checked_at - started,
                        detail="Deployment execution deadline exceeded.",
                        source_commit=source_commit,
                    )
                    return connection
                next_control_check = time.monotonic() + 2
        reader.join(timeout=1)
        final_status = "success"
        error_code = ""
        detail = ""
        if return_code != 0:
            final_status, error_code, detail = _deployment_failure_result(
                return_code,
                last_diagnostic,
            )
        if return_code == 0:
            try:
                preserve_deployment_release_reference(
                    commit=source_commit,
                    source_tree=reviewed["source_tree"],
                    base_commit=reviewed["target_commit"],
                    job_id=job_id,
                    status="activated",
                )
            except Exception as exc:
                logger.exception(
                    "Successful deployment release reference could not be fully preserved job_id=%s",
                    job_id,
                )
                connection = _report_log_with_reconnect(
                    connection,
                    job_id,
                    "warning",
                    str(exc),
                    {"release_ref_retention": "failed"},
                )
        connection = _finish_job_resilient(
            connection,
            job,
            final_status=final_status,
            return_code=return_code,
            duration_seconds=time.monotonic() - started,
            detail=detail,
            source_commit=source_commit,
            error_code=error_code,
        )
    except NativeDeploymentBlockedByContainerRuntime as exc:
        connection = _report_log_with_reconnect(
            connection,
            job_id,
            "error",
            str(exc),
            {"error_code": "native_deploy_blocked_by_container_runtime"},
        )
        connection = _finish_job_resilient(
            connection,
            job,
            final_status="blocked",
            return_code=None,
            duration_seconds=time.monotonic() - started,
            detail=str(exc),
            source_commit=source_commit,
            error_code="native_deploy_blocked_by_container_runtime",
        )
    except Exception as exc:
        logger.exception("Deployment job failed job_id=%s", job_id)
        _terminate_active_process()
        try:
            connection.rollback()
        except psycopg2.Error:
            connection = _try_reconnect_queue(connection)
        connection = _finish_job_resilient(
            connection,
            job,
            final_status="failed",
            return_code=ACTIVE_PROCESS.returncode if ACTIVE_PROCESS else None,
            duration_seconds=time.monotonic() - started,
            detail=f"{type(exc).__name__}: deployment worker execution failed",
            source_commit=source_commit,
        )
    finally:
        _terminate_active_process()
        ACTIVE_PROCESS = None
        if source_commit:
            release_deployment_snapshot_reference(
                snapshot_reference,
                source_commit,
            )
        _remove_deployment_runtime_directory_resilient(deployment_runtime_directory)
    return connection


def _handle_signal(_signum, _frame) -> None:
    global RUNNING
    RUNNING = False
    _terminate_active_process()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MoonCen PostgreSQL-backed deployment worker")
    parser.add_argument("--once", action="store_true", help="Claim at most one deployment job and exit")
    parser.add_argument("--agent-id", default=os.getenv("OPS_AGENT_ID", ""))
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=float(os.getenv("OPS_DEPLOY_POLL_INTERVAL_SECONDS", "2")),
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
    if not container_worker_service_boundary_ready():
        logger.error(
            "Deployment worker requires the isolated mooncen_deployment_worker "
            "account, dedicated queue role, private deploy transport, and release root"
        )
        return 1
    stale_after_seconds = deployment_heartbeat_lease_seconds()
    config = WorkerConfig(
        environment=normalized_environment(),
        agent_id=args.agent_id,
        poll_interval=args.poll_interval,
        command_timeout=bounded_env_int("OPS_DEPLOY_JOB_TIMEOUT_SECONDS", 7_200, 300, 21_600),
        stale_after_seconds=stale_after_seconds,
        container_only=True,
    )
    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_signal)

    connection = connect_queue()
    try:
        if config.agent_id is None:
            config = replace(
                config,
                agent_id=_register_deployment_agent(connection, config.environment),
            )
        else:
            _touch_deployment_agent(connection, config.agent_id, config.environment)
        if not _publish_worker_heartbeat_resilient():
            logger.error("Deployment worker startup aborted because its local heartbeat is unavailable")
            return 1
        stale_recovery_interval = min(60.0, max(10.0, stale_after_seconds / 2))
        _replay_pending_final_statuses(connection)
        connection, _ = _reconcile_stale_container_job(
            connection,
            config,
            stale_after_seconds=stale_after_seconds,
        )
        if not config.container_only:
            _recover_stale_jobs(
                connection,
                config,
                stale_after_seconds=stale_after_seconds,
            )
        schedule_started = time.monotonic()
        next_stale_recovery = schedule_started + stale_recovery_interval
        next_final_replay = schedule_started + PENDING_FINAL_REPLAY_INTERVAL_SECONDS
        while RUNNING:
            try:
                if config.agent_id is None:
                    raise RuntimeError("Deployment agent id is unavailable")
                _touch_deployment_agent(connection, config.agent_id, config.environment)
                if not _publish_worker_heartbeat_resilient():
                    logger.error("Deployment worker stopped because its local heartbeat remained unavailable")
                    return 1
                now = time.monotonic()
                if now >= next_stale_recovery:
                    # A locally persisted real child result wins over a lease
                    # timeout when connectivity returns at this boundary.
                    _replay_pending_final_statuses(connection)
                    connection, _ = _reconcile_stale_container_job(
                        connection,
                        config,
                        stale_after_seconds=stale_after_seconds,
                    )
                    if not config.container_only:
                        _recover_stale_jobs(
                            connection,
                            config,
                            stale_after_seconds=stale_after_seconds,
                        )
                    next_stale_recovery = now + stale_recovery_interval
                    next_final_replay = now + PENDING_FINAL_REPLAY_INTERVAL_SECONDS
                elif now >= next_final_replay:
                    _replay_pending_final_statuses(connection)
                    next_final_replay = now + PENDING_FINAL_REPLAY_INTERVAL_SECONDS
                job = _claim_job(connection, config)
                if job:
                    connection = execute_job(connection, job, config)
                    _replay_pending_final_statuses(connection)
                    next_final_replay = time.monotonic() + PENDING_FINAL_REPLAY_INTERVAL_SECONDS
                elif args.once:
                    return 0
            except psycopg2.Error:
                logger.exception("Deployment queue database operation failed")
                try:
                    connection.rollback()
                except psycopg2.Error:
                    connection = _try_reconnect_queue(connection)
                if args.once:
                    return 1
            if args.once:
                return 0
            time.sleep(config.poll_interval)
    finally:
        _clear_worker_heartbeat()
        connection.close()
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("OPS_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    raise SystemExit(main())
