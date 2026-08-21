from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.database import SessionLocal
from backend.ops.service import current_environment, enqueue_job
from run_crawlers import PROVIDER_ADAPTERS


logger = logging.getLogger(__name__)
RUNNING = True
DEFAULT_PROVIDERS = (
    "HOMEPLUS",
    "EMART",
    "LOTTE",
    "EXPERIENCE_TARGETS",
    "MUNICIPAL_RESERVATION_TARGETS",
)


@dataclass(frozen=True)
class SchedulerConfig:
    providers: tuple[str, ...]
    interval_seconds: int
    poll_seconds: int
    environment: str


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = str(os.getenv(name) or default).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def configured_providers() -> tuple[str, ...]:
    raw = (
        str(os.getenv("OPS_LOCAL_CRAWLER_PROVIDERS") or "").strip()
        or str(os.getenv("CRAWLER_PROVIDERS") or "").strip()
    )
    providers = tuple(part.upper() for part in raw.replace(",", " ").split()) if raw else DEFAULT_PROVIDERS
    if not providers:
        raise RuntimeError("At least one local crawler provider is required")
    if len(providers) > 100:
        raise RuntimeError("Local crawler schedule accepts at most 100 providers")
    if len(providers) != len(set(providers)):
        raise RuntimeError("Local crawler providers must not contain duplicates")
    unknown = sorted(set(providers) - set(PROVIDER_ADAPTERS))
    if unknown:
        raise RuntimeError(f"Local crawler providers are not registered: {', '.join(unknown)}")
    return providers


def load_config() -> SchedulerConfig:
    environment = current_environment()
    if environment != "development":
        raise RuntimeError(
            "The Ops local crawler scheduler is development-only; use the reviewed service manager in other environments"
        )
    return SchedulerConfig(
        providers=configured_providers(),
        interval_seconds=_bounded_env_int(
            "OPS_LOCAL_CRAWLER_INTERVAL_SECONDS",
            _bounded_env_int("CRAWLER_RUN_INTERVAL", 86_400, 60, 604_800),
            60,
            604_800,
        ),
        poll_seconds=_bounded_env_int("OPS_LOCAL_CRAWLER_SCHEDULER_POLL_SECONDS", 30, 5, 3_600),
        environment=environment,
    )


def _schedule_state(db: Session, environment: str) -> tuple[dict[str, datetime], set[str]]:
    latest_rows = db.execute(
        text(
            """
            SELECT UPPER(parameters ->> 'provider') AS provider,
                   MAX(created_at) AS latest_created_at
            FROM ops_jobs
            WHERE environment = :environment
              AND job_type IN ('crawler_run', 'crawler_retry')
              AND parameters ->> 'trigger' = 'local_schedule'
            GROUP BY UPPER(parameters ->> 'provider')
            """
        ),
        {"environment": environment},
    ).mappings()
    latest = {
        str(row["provider"]): row["latest_created_at"]
        for row in latest_rows
        if row.get("provider") and row.get("latest_created_at")
    }
    active_rows = db.execute(
        text(
            """
            SELECT DISTINCT UPPER(parameters ->> 'provider') AS provider
            FROM ops_jobs
            WHERE environment = :environment
              AND job_type IN ('crawler_run', 'crawler_retry')
              AND status IN ('queued', 'assigned', 'running')
            """
        ),
        {"environment": environment},
    ).mappings()
    active = {str(row["provider"]) for row in active_rows if row.get("provider")}
    return latest, active


def _job_parameters(provider: str) -> dict[str, Any]:
    return {
        "scope": "provider",
        "content_type": "all",
        "provider": provider,
        "run_mode": "apply",
        "compare_existing": True,
        "review_before_apply": False,
        "save_screenshot": True,
        "save_html": False,
        "browser_visible": False,
        "max_retries": 1,
        "concurrency": 1,
        "force_full_refresh": False,
        "trigger": "local_schedule",
    }


def _enqueue_provider(db: Session, provider: str) -> str:
    parameters = _job_parameters(provider)
    job = enqueue_job(
        db,
        job_type="crawler_run",
        requested_by=None,
        parameters=parameters,
        target_key=f"provider:{provider}",
        max_retries=1,
    )
    run = db.execute(
        text(
            """
            INSERT INTO ops_crawler_runs (
                crawler_name, content_type, provider, job_id, status, run_mode
            )
            VALUES (:provider, 'all', :provider, :job_id, 'queued', 'apply')
            RETURNING id::text
            """
        ),
        {"provider": provider, "job_id": str(job["id"])},
    ).scalar_one()
    db.execute(
        text(
            """
            INSERT INTO ops_audit_logs (
                user_id, action, resource_type, resource_id,
                after_data, result, job_id
            )
            VALUES (
                NULL, 'crawler.schedule', 'crawler_run', :run_id,
                CAST(:after_data AS jsonb), 'success', :job_id
            )
            """
        ),
        {
            "run_id": run,
            "after_data": json.dumps(parameters, ensure_ascii=False),
            "job_id": str(job["id"]),
        },
    )
    db.commit()
    return str(job["id"])


def enqueue_due_jobs(db: Session, config: SchedulerConfig, *, now: datetime | None = None) -> list[str]:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    due_before = current - timedelta(seconds=config.interval_seconds)
    latest, active = _schedule_state(db, config.environment)
    enqueued: list[str] = []
    for provider in config.providers:
        last_created = latest.get(provider)
        if provider in active or (last_created is not None and last_created > due_before):
            continue
        try:
            job_id = _enqueue_provider(db, provider)
        except HTTPException as exc:
            db.rollback()
            if exc.status_code == 409:
                logger.info("Crawler schedule already has an active job. provider=%s", provider)
                continue
            raise
        enqueued.append(job_id)
        logger.info("Scheduled crawler job queued. provider=%s job_id=%s", provider, job_id)
    return enqueued


def _stop(_signum: int, _frame: object) -> None:
    global RUNNING
    RUNNING = False


def run_scheduler(config: SchedulerConfig, *, once: bool = False) -> int:
    while RUNNING:
        try:
            with SessionLocal() as db:
                enqueue_due_jobs(db, config)
        except Exception:
            logger.exception("Local crawler scheduler iteration failed")
            if once:
                return 1
        if once:
            return 0
        deadline = time.monotonic() + config.poll_seconds
        while RUNNING and time.monotonic() < deadline:
            time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Queue development crawler jobs on the Ops control plane")
    parser.add_argument("--once", action="store_true", help="Queue due jobs once and exit")
    parser.add_argument("--check", action="store_true", help="Validate configuration and exit")
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config = load_config()
    args = parse_args()
    if args.check:
        logger.info(
            "Local crawler scheduler configuration is valid. providers=%s interval=%ss",
            ",".join(config.providers),
            config.interval_seconds,
        )
        return 0
    return run_scheduler(config, once=args.once)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _stop)
    raise SystemExit(main())
