from __future__ import annotations

import argparse
import hashlib
import logging
import os
import re
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import psycopg2
import yaml
from psycopg2.extras import Json

from ops_agent.crawler_control_db import control_database_config
from ops_agent.crawler_control_provider_scope import (
    EXPERIENCE_AGGREGATE_OWNER,
    MUNICIPAL_AGGREGATE_OWNER,
    build_course_provider_owners,
    reviewed_crawler_providers,
)
from ops_agent.crawler_control_recovery import (
    ControlRecoveryConfig,
    normalized_environment,
    recover_stale_jobs,
)

# Compatibility seam retained for tests and operators that instrument stale
# recovery without importing the crawler worker runtime.
_recover_stale_jobs = recover_stale_jobs


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROVIDER_MANIFEST = PROJECT_ROOT / "config" / "production_crawler_providers.yaml"
CONTROL_LOCK_PREFIX = "mooncen.crawler-control.scheduler"
VERSION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")
PROVIDER_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{0,99}")
RUNNING = True
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ControlSchedulerConfig:
    environment: str
    providers: tuple[str, ...]
    provider_manifest: Path
    code_version: str
    artifact_digest: str
    config_revision: str
    schedule_hour: int
    schedule_minute: int
    timezone_name: str
    max_late_seconds: int
    poll_seconds: int
    max_retries: int
    provider_execution_owners: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ScheduleResult:
    is_leader: bool
    batch_id: UUID | None = None
    job_count: int = 0
    reason: str = ""


def _required_version(name: str) -> str:
    value = str(os.getenv(name) or "").strip()
    if not VERSION_PATTERN.fullmatch(value):
        raise RuntimeError(f"{name} must be an explicit bounded release identifier")
    return value


def _required_digest(name: str) -> str:
    value = str(os.getenv(name) or "").strip().lower()
    if not DIGEST_PATTERN.fullmatch(value):
        raise RuntimeError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = str(os.getenv(name) or default).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def _load_provider_manifest_details(
    path: Path,
) -> tuple[tuple[str, ...], str, tuple[tuple[str, str], ...]]:
    resolved = path.resolve()
    if not resolved.is_file() or resolved.is_symlink():
        raise RuntimeError("Crawler provider manifest must be a regular file")
    raw = resolved.read_bytes()
    if not raw or len(raw) > 1_048_576:
        raise RuntimeError("Crawler provider manifest size is invalid")
    try:
        document = yaml.safe_load(raw.decode("utf-8")) or {}
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise RuntimeError("Crawler provider manifest is invalid") from exc
    providers = document.get("providers") if isinstance(document, dict) else None
    if document.get("version") != 1 or not isinstance(providers, list) or not providers:
        raise RuntimeError("Crawler provider manifest contract is invalid")
    normalized = tuple(str(provider).strip().upper() for provider in providers)
    if len(normalized) > 512 or len(normalized) != len(set(normalized)):
        raise RuntimeError("Crawler provider manifest contains duplicate or excessive entries")
    if any(not PROVIDER_PATTERN.fullmatch(provider) for provider in normalized):
        raise RuntimeError("Crawler provider manifest contains an invalid provider")
    reviewed = set(reviewed_crawler_providers(PROJECT_ROOT))
    unknown = sorted(set(normalized) - reviewed)
    if unknown:
        raise RuntimeError(f"Crawler provider manifest contains unreviewed providers: {', '.join(unknown)}")
    try:
        declared_owners = build_course_provider_owners(list(normalized))
    except (RuntimeError, ValueError) as exc:
        raise RuntimeError("Crawler provider manifest ownership expansion failed") from exc

    aggregate_owners = {EXPERIENCE_AGGREGATE_OWNER, MUNICIPAL_AGGREGATE_OWNER}
    expanded: list[str] = []
    execution_owners: list[tuple[str, str]] = []
    for provider in normalized:
        concrete_providers = (
            sorted(
                concrete
                for concrete, owner in declared_owners.items()
                if owner == provider
            )
            if provider in aggregate_owners
            else [provider]
        )
        if not concrete_providers:
            raise RuntimeError(f"Aggregate crawler provider has no concrete tasks: {provider}")
        for concrete in concrete_providers:
            expanded.append(concrete)
            execution_owners.append((concrete, provider))

    if len(expanded) > 512 or len(expanded) != len(set(expanded)):
        raise RuntimeError("Expanded crawler provider manifest overlaps or exceeds 512 tasks")
    expanded_unknown = sorted(set(expanded) - reviewed)
    if expanded_unknown:
        raise RuntimeError(
            "Expanded crawler manifest contains unreviewed providers: "
            + ", ".join(expanded_unknown)
        )
    return (
        tuple(expanded),
        hashlib.sha256(raw).hexdigest(),
        tuple(execution_owners),
    )


def load_provider_manifest(path: Path) -> tuple[tuple[str, ...], str]:
    providers, revision, _execution_owners = _load_provider_manifest_details(path)
    return providers, revision


def load_config() -> ControlSchedulerConfig:
    if str(os.getenv("OPS_CRAWLER_CONTROL_ENABLED") or "").strip().lower() not in {"1", "true", "yes", "on"}:
        raise RuntimeError("OPS_CRAWLER_CONTROL_ENABLED must be explicitly enabled")
    environment = normalized_environment()
    if environment not in {"staging", "production"}:
        raise RuntimeError("The central crawler scheduler is restricted to staging or production")
    manifest = Path(os.getenv("OPS_CRAWLER_PROVIDER_MANIFEST", str(DEFAULT_PROVIDER_MANIFEST)))
    providers, config_revision, execution_owners = _load_provider_manifest_details(manifest)
    expected_revision = str(os.getenv("OPS_CRAWLER_CONFIG_REVISION") or "").strip().lower()
    if expected_revision and expected_revision != config_revision:
        raise RuntimeError("OPS_CRAWLER_CONFIG_REVISION does not match the provider manifest")
    timezone_name = str(os.getenv("OPS_CRAWLER_SCHEDULE_TIMEZONE") or "Asia/Seoul").strip()
    try:
        ZoneInfo(timezone_name)
    except Exception as exc:
        raise RuntimeError("OPS_CRAWLER_SCHEDULE_TIMEZONE is invalid") from exc
    return ControlSchedulerConfig(
        environment=environment,
        providers=providers,
        provider_manifest=manifest.resolve(),
        code_version=_required_version("OPS_CRAWLER_CODE_VERSION"),
        artifact_digest=_required_digest("OPS_CRAWLER_ARTIFACT_DIGEST"),
        config_revision=config_revision,
        schedule_hour=_bounded_int("OPS_CRAWLER_SCHEDULE_HOUR", 22, 0, 23),
        schedule_minute=_bounded_int("OPS_CRAWLER_SCHEDULE_MINUTE", 0, 0, 59),
        timezone_name=timezone_name,
        max_late_seconds=_bounded_int("OPS_CRAWLER_SCHEDULE_MAX_LATE_SECONDS", 21_600, 60, 86_400),
        poll_seconds=_bounded_int("OPS_CRAWLER_CONTROL_POLL_SECONDS", 30, 5, 3_600),
        max_retries=_bounded_int("OPS_CRAWLER_TASK_MAX_RETRIES", 2, 0, 20),
        provider_execution_owners=execution_owners,
    )


def latest_schedule_slot(config: ControlSchedulerConfig, now: datetime | None = None) -> datetime | None:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    local_zone = ZoneInfo(config.timezone_name)
    local_now = current.astimezone(local_zone)
    slot = local_now.replace(hour=config.schedule_hour, minute=config.schedule_minute, second=0, microsecond=0)
    if local_now < slot:
        slot -= timedelta(days=1)
    utc_slot = slot.astimezone(timezone.utc)
    if not timedelta(0) <= current.astimezone(timezone.utc) - utc_slot <= timedelta(seconds=config.max_late_seconds):
        return None
    return utc_slot


def provider_output_allowlists(providers: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    """Freeze the exact concrete-provider scope for every scheduled task."""

    owners = build_course_provider_owners(list(providers))
    allowlists: dict[str, list[str]] = {provider: [] for provider in providers}
    for concrete_provider, scheduled_owner in owners.items():
        if scheduled_owner not in allowlists:
            raise RuntimeError("Crawler ownership map references an unscheduled task")
        allowlists[scheduled_owner].append(concrete_provider)
    if any(not values for values in allowlists.values()):
        raise RuntimeError("Every scheduled crawler task requires a concrete provider scope")
    flattened = [provider for values in allowlists.values() for provider in values]
    if len(flattened) != len(set(flattened)):
        raise RuntimeError("Concrete crawler providers cannot be owned by multiple tasks")
    return {owner: tuple(sorted(values)) for owner, values in allowlists.items()}


def _job_parameters(
    config: ControlSchedulerConfig,
    batch_id: UUID,
    provider: str,
    allowed_output_providers: tuple[str, ...],
) -> dict[str, Any]:
    execution_owner = dict(config.provider_execution_owners).get(provider, provider)
    return {
        "scope": "provider",
        "content_type": "all",
        "provider": provider,
        "execution_provider": execution_owner,
        "batch_id": str(batch_id),
        "run_mode": "apply",
        "concurrency": 1,
        # This copy is diagnostic only.  The immutable task mapping in
        # ops_crawler_batch_tasks is the database authorization boundary.
        "allowed_output_providers": list(allowed_output_providers),
        "scheduled_providers": list(config.providers),
        "trigger": "control_schedule",
        "code_version": config.code_version,
        "artifact_digest": config.artifact_digest,
        "config_revision": config.config_revision,
    }


def enqueue_schedule_slot(connection, config: ControlSchedulerConfig, slot: datetime) -> ScheduleResult:
    batch_id = uuid4()
    # Config providers are already the immutable expanded concrete task set;
    # aggregate execution ownership is carried separately below.
    output_allowlists = {provider: (provider,) for provider in config.providers}
    try:
        with connection.cursor() as cursor:
            active_provider_keys = sorted(
                set(config.providers)
                | {
                    owner
                    for provider, owner in config.provider_execution_owners
                    if provider in config.providers
                }
            )
            cursor.execute(
                "SELECT pg_try_advisory_xact_lock(hashtext(%s))",
                (f"{CONTROL_LOCK_PREFIX}:{config.environment}",),
            )
            leader_row = cursor.fetchone()
            if not leader_row or leader_row[0] is not True:
                connection.commit()
                return ScheduleResult(False, reason="standby")

            cursor.execute(
                """
                SELECT UPPER(parameters ->> 'provider')
                FROM ops_jobs
                WHERE environment = %s
                  AND job_type IN ('crawler_run', 'crawler_retry')
                  AND status IN ('queued', 'assigned', 'running')
                  AND UPPER(parameters ->> 'provider') = ANY(%s)
                LIMIT 1
                """,
                (config.environment, active_provider_keys),
            )
            if cursor.fetchone() is not None:
                connection.commit()
                return ScheduleResult(True, reason="previous_batch_active")

            cursor.execute(
                """
                INSERT INTO ops_crawler_batches (
                    id, environment, scheduled_slot, status, expected_task_count,
                    code_version, artifact_digest, config_revision
                )
                VALUES (%s, %s, %s, 'queued', %s, %s, %s, %s)
                ON CONFLICT (environment, scheduled_slot) DO NOTHING
                RETURNING id
                """,
                (
                    str(batch_id),
                    config.environment,
                    slot,
                    len(config.providers),
                    config.code_version,
                    config.artifact_digest,
                    config.config_revision,
                ),
            )
            if cursor.fetchone() is None:
                connection.commit()
                return ScheduleResult(True, reason="already_scheduled")

            for provider in config.providers:
                job_id = uuid4()
                allowed_output_providers = output_allowlists[provider]
                parameters = _job_parameters(
                    config,
                    batch_id,
                    provider,
                    allowed_output_providers,
                )
                cursor.execute(
                    """
                    INSERT INTO ops_jobs (
                        id, job_type, status, environment, target_key,
                        deduplication_key, parameters, max_retries, available_at,
                        required_code_version, artifact_digest, config_revision
                    )
                    VALUES (
                        %s, 'crawler_run', 'queued', %s, %s, %s, %s, %s,
                        CURRENT_TIMESTAMP, %s, %s, %s
                    )
                    """,
                    (
                        str(job_id),
                        config.environment,
                        f"provider:{provider}",
                        f"crawler-provider:{config.environment}:{provider}",
                        Json(parameters),
                        config.max_retries,
                        config.code_version,
                        config.artifact_digest,
                        config.config_revision,
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO ops_crawler_batch_tasks (
                        batch_id, job_id, task_key, provider, allowed_output_providers
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        str(batch_id),
                        str(job_id),
                        f"provider:{provider}",
                        provider,
                        list(allowed_output_providers),
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO ops_crawler_runs (
                        crawler_name, content_type, provider, job_id, status, run_mode
                    )
                    VALUES (%s, 'all', %s, %s, 'queued', 'apply')
                    """,
                    (provider, provider, str(job_id)),
                )
        connection.commit()
        return ScheduleResult(True, batch_id=batch_id, job_count=len(config.providers), reason="enqueued")
    except Exception:
        connection.rollback()
        raise


def run_scheduler(config: ControlSchedulerConfig, *, once: bool = False) -> int:
    while RUNNING:
        slot = latest_schedule_slot(config)
        connection = psycopg2.connect(**control_database_config())
        try:
            recovered = _recover_stale_jobs(
                connection,
                ControlRecoveryConfig(environment=config.environment),
                stale_after_seconds=0,
            )
            if recovered:
                logger.warning("Recovered %s expired crawler leases", recovered)
            if slot is not None:
                result = enqueue_schedule_slot(connection, config, slot)
                logger.info(
                    "Crawler control schedule result=%s leader=%s batch_id=%s jobs=%s",
                    result.reason,
                    result.is_leader,
                    result.batch_id,
                    result.job_count,
                )
        except Exception:
            logger.exception("Crawler control scheduler iteration failed")
            if once:
                return 1
        finally:
            connection.close()
        if once:
            return 0
        deadline = time.monotonic() + config.poll_seconds
        while RUNNING and time.monotonic() < deadline:
            time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
    return 0


def _stop(_signum: int, _frame: object) -> None:
    global RUNNING
    RUNNING = False


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MoonCen central crawler control-plane scheduler")
    parser.add_argument("--once", action="store_true", help="Schedule the current due slot once and exit")
    parser.add_argument("--check", action="store_true", help="Validate the pinned release and schedule configuration")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config()
    if args.check:
        logger.info(
            "Crawler control configuration valid providers=%s code=%s digest=%s config=%s",
            len(config.providers),
            config.code_version,
            config.artifact_digest,
            config.config_revision,
        )
        return 0
    return run_scheduler(config, once=args.once)


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("OPS_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _stop)
    raise SystemExit(main())
