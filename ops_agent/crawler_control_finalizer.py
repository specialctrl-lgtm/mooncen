from __future__ import annotations

import argparse
import logging
import os
import re
import signal
import socket
import time
from dataclasses import dataclass
from typing import Any
import psycopg2
from psycopg2.extras import Json, RealDictCursor

from ops_agent.crawler_control_db import finalizer_database_config
from ops_agent.crawler_control_provider_scope import AGGREGATE_PROVIDER_OWNERS
from ops_agent.crawler_control_recovery import normalized_environment


logger = logging.getLogger(__name__)
RUNNING = True
FINALIZER_LOCK = "mooncen.crawler-control.finalizer"
ACTIVE_JOB_STATUSES = frozenset({"queued", "assigned", "running"})
SUCCESS_JOB_STATUSES = frozenset({"success"})
TERMINAL_JOB_STATUSES = frozenset(
    {
        "success",
        "partial_success",
        "failed",
        "cancelled",
        "timed_out",
        "blocked",
        "dead_lettered",
    }
)
PROVIDER_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{0,99}")
AUTO_PROMOTION_ENV = "OPS_CRAWLER_AUTO_PROMOTION_ENABLED"


@dataclass(frozen=True)
class BatchDecision:
    status: str
    terminal: bool
    reason: str


def auto_promotion_enabled(environment: str) -> bool:
    """Load the explicit primary-promotion policy for a production-like run.

    There is intentionally no implicit production default.  A missing or
    misspelled value must stop the finalizer before it can make a newly sealed
    control-plane batch visible to the legacy hourly promotion timer.
    """
    raw = os.getenv(AUTO_PROMOTION_ENV, "").strip().lower()
    if environment in {"production", "staging"} and raw != "false":
        raise RuntimeError(
            f"{AUTO_PROMOTION_ENV} must be explicitly false; "
            "promotion requires the separate reviewed approver credential"
        )
    if raw not in {"", "false"}:
        raise RuntimeError(f"{AUTO_PROMOTION_ENV} must be false")
    return False


def decide_batch(tasks: list[dict[str, Any]], expected_task_count: int) -> BatchDecision:
    if len(tasks) != expected_task_count or expected_task_count <= 0:
        return BatchDecision("failed", True, "task_count_mismatch")
    statuses = {str(task.get("job_status") or "") for task in tasks}
    if statuses & ACTIVE_JOB_STATUSES:
        running = bool(statuses & {"assigned", "running"})
        return BatchDecision("running" if running else "queued", False, "tasks_active")
    if any(status not in TERMINAL_JOB_STATUSES for status in statuses):
        return BatchDecision("failed", True, "unknown_job_status")
    required = [task for task in tasks if task.get("required") is not False]
    if not required:
        return BatchDecision("failed", True, "no_required_tasks")
    if len(required) != len(tasks):
        return BatchDecision("failed", True, "optional_tasks_are_not_yet_publishable")
    providers = [str(task.get("provider") or "") for task in tasks]
    if len(providers) != len(set(providers)) or any(int(task.get("shard_count") or 1) != 1 for task in tasks):
        return BatchDecision("failed", True, "sharded_tasks_are_not_yet_publishable")
    successful = [
        task
        for task in required
        if task.get("job_status") in SUCCESS_JOB_STATUSES
        and task.get("attempt_status") == "success"
        and task.get("attempt_contract_matches") is True
        and task.get("attempt_result_present") is True
    ]
    if len(successful) == len(required):
        return BatchDecision("success", True, "all_required_tasks_succeeded")
    if successful:
        return BatchDecision("partial_success", True, "some_required_tasks_failed")
    if statuses == {"cancelled"}:
        return BatchDecision("cancelled", True, "all_required_tasks_cancelled")
    if "dead_lettered" in statuses:
        return BatchDecision("dead_lettered", True, "retry_budget_exhausted")
    return BatchDecision("failed", True, "required_tasks_failed")


def _load_next_batch(cursor, environment: str) -> dict[str, Any] | None:
    cursor.execute(
        """
        SELECT id::text, environment, scheduled_slot, status,
               expected_task_count, code_version, artifact_digest, config_revision
        FROM ops_crawler_batches
        WHERE environment = %s
          AND status IN ('planning', 'queued', 'running', 'finalizing')
        ORDER BY scheduled_slot, created_at
        FOR UPDATE SKIP LOCKED
        LIMIT 1
        """,
        (environment,),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def _load_tasks(cursor, batch: dict[str, Any]) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT t.task_key, t.provider, t.allowed_output_providers,
               t.required, t.close_missing_eligible,
               t.shard_index, t.shard_count,
               j.id::text AS job_id, j.status AS job_status, j.result,
               j.error_code, j.error_message, j.attempt_no, j.lease_epoch,
               a.id::text AS attempt_id, a.status AS attempt_status,
               finished.payload AS attempt_evidence,
               a.worker_code_version, a.artifact_digest AS attempt_artifact_digest,
               a.config_revision AS attempt_config_revision,
               (
                   a.worker_code_version = %s
                   AND a.artifact_digest = %s
                   AND a.config_revision = %s
               ) AS attempt_contract_matches
               , ((finished.payload -> 'result') ? 'task_result') AS attempt_result_present
        FROM ops_crawler_batch_tasks t
        JOIN ops_jobs j ON j.id = t.job_id
        LEFT JOIN ops_crawler_task_attempts a
          ON a.job_id = j.id
         AND a.attempt_no = j.attempt_no
         AND a.lease_epoch = j.lease_epoch
        LEFT JOIN LATERAL (
            SELECT observation.payload
            FROM ops_crawler_task_observations observation
            WHERE observation.attempt_id = a.id
              AND observation.job_id = a.job_id
              AND observation.attempt_no = a.attempt_no
              AND observation.lease_epoch = a.lease_epoch
              AND observation.observation_kind = 'finished'
            ORDER BY observation.observed_at DESC, observation.created_at DESC
            LIMIT 1
        ) finished ON true
        WHERE t.batch_id = %s
        ORDER BY t.task_key
        """,
        (
            batch["code_version"],
            batch["artifact_digest"],
            batch["config_revision"],
            batch["id"],
        ),
    )
    return [dict(row) for row in cursor.fetchall()]


def _snapshot_counts(cursor, batch_id: str) -> tuple[int, int, int, dict[str, int]]:
    cursor.execute(
        """
        WITH selected AS (
            SELECT DISTINCT ON (
                snapshot.attempt_id, snapshot.provider, snapshot.provider_course_id
            ) snapshot.provider, snapshot.row_data
            FROM crawl_staging.fenced_course_snapshots snapshot
            JOIN ops_jobs job
              ON job.id = snapshot.job_id
             AND job.attempt_no = snapshot.attempt_no
             AND job.lease_epoch = snapshot.lease_epoch
            JOIN ops_crawler_task_attempts attempt
              ON attempt.id = snapshot.attempt_id
             AND attempt.job_id = snapshot.job_id
             AND attempt.attempt_no = snapshot.attempt_no
             AND attempt.lease_epoch = snapshot.lease_epoch
            WHERE snapshot.crawl_batch_id = %s::uuid
            ORDER BY snapshot.attempt_id, snapshot.provider,
                     snapshot.provider_course_id, snapshot.snapshot_id DESC
        )
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (
                   WHERE NULLIF(BTRIM(row_data ->> 'provider'), '') IS NOT NULL
                     AND NULLIF(BTRIM(row_data ->> 'provider_course_id'), '') IS NOT NULL
                     AND NULLIF(BTRIM(row_data ->> 'title'), '') IS NOT NULL
               ) AS valid
        FROM selected
        """,
        (batch_id,),
    )
    course_row = cursor.fetchone() or {"total": 0, "valid": 0}
    cursor.execute(
        """
        WITH selected AS (
            SELECT DISTINCT ON (
                snapshot.attempt_id, snapshot.provider, snapshot.branch_code
            ) snapshot.row_data
            FROM crawl_staging.fenced_branch_snapshots snapshot
            JOIN ops_jobs job
              ON job.id = snapshot.job_id
             AND job.attempt_no = snapshot.attempt_no
             AND job.lease_epoch = snapshot.lease_epoch
            JOIN ops_crawler_task_attempts attempt
              ON attempt.id = snapshot.attempt_id
             AND attempt.job_id = snapshot.job_id
             AND attempt.attempt_no = snapshot.attempt_no
             AND attempt.lease_epoch = snapshot.lease_epoch
            WHERE snapshot.crawl_batch_id = %s::uuid
            ORDER BY snapshot.attempt_id, snapshot.provider,
                     snapshot.branch_code, snapshot.snapshot_id DESC
        )
        SELECT COUNT(*) AS total
        FROM selected
        """,
        (batch_id,),
    )
    branch_row = cursor.fetchone() or {"total": 0}
    cursor.execute(
        """
        WITH selected AS (
            SELECT DISTINCT ON (
                snapshot.attempt_id, snapshot.provider, snapshot.provider_course_id
            ) snapshot.provider
            FROM crawl_staging.fenced_course_snapshots snapshot
            JOIN ops_jobs job
              ON job.id = snapshot.job_id
             AND job.attempt_no = snapshot.attempt_no
             AND job.lease_epoch = snapshot.lease_epoch
            JOIN ops_crawler_task_attempts attempt
              ON attempt.id = snapshot.attempt_id
             AND attempt.job_id = snapshot.job_id
             AND attempt.attempt_no = snapshot.attempt_no
             AND attempt.lease_epoch = snapshot.lease_epoch
            WHERE snapshot.crawl_batch_id = %s::uuid
            ORDER BY snapshot.attempt_id, snapshot.provider,
                     snapshot.provider_course_id, snapshot.snapshot_id DESC
        )
        SELECT provider, COUNT(*) AS course_count
        FROM selected
        GROUP BY provider
        ORDER BY provider
        """,
        (batch_id,),
    )
    provider_counts = {
        str(row["provider"]): int(row["course_count"] or 0)
        for row in cursor.fetchall()
        if row.get("provider")
    }
    total = int(course_row.get("total") or 0)
    valid = int(course_row.get("valid") or 0)
    return int(branch_row.get("total") or 0), total, valid, provider_counts


def _persisted_provider_owners(tasks: list[dict[str, Any]]) -> dict[str, str]:
    owners: dict[str, str] = {}
    scheduled: set[str] = set()
    for task in tasks:
        owner = str(task.get("provider") or "")
        raw_allowlist = task.get("allowed_output_providers")
        if not PROVIDER_PATTERN.fullmatch(owner) or owner in scheduled:
            raise RuntimeError("batch task provider mapping is invalid")
        if not isinstance(raw_allowlist, (list, tuple)) or not 1 <= len(raw_allowlist) <= 4096:
            raise RuntimeError("batch task output provider scope is invalid")
        scheduled.add(owner)
        local: set[str] = set()
        for raw_provider in raw_allowlist:
            concrete = str(raw_provider or "")
            if not PROVIDER_PATTERN.fullmatch(concrete) or concrete in local or concrete in owners:
                raise RuntimeError("batch task output provider scope overlaps or is invalid")
            local.add(concrete)
            owners[concrete] = owner
    if set(owners.values()) != scheduled:
        raise RuntimeError("a scheduled crawler task has no output provider scope")
    return dict(sorted(owners.items()))


def _selected_attempts(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen_attempts: set[str] = set()
    seen_jobs: set[str] = set()
    for task in tasks:
        attempt_id = task.get("attempt_id")
        if not attempt_id:
            continue
        attempt_id = str(attempt_id)
        job_id = str(task["job_id"])
        attempt_no = int(task.get("attempt_no") or 0)
        lease_epoch = int(task.get("lease_epoch") or 0)
        if (
            attempt_no <= 0
            or lease_epoch <= 0
            or attempt_id in seen_attempts
            or job_id in seen_jobs
        ):
            raise RuntimeError("selected crawler attempt identity is invalid")
        seen_attempts.add(attempt_id)
        seen_jobs.add(job_id)
        selected.append(
            {
                "attempt_id": attempt_id,
                "job_id": job_id,
                "attempt_no": attempt_no,
                "lease_epoch": lease_epoch,
            }
        )
    return selected


def _batch_result(
    batch: dict[str, Any],
    tasks: list[dict[str, Any]],
    decision: BatchDecision,
    provider_counts: dict[str, int],
) -> dict[str, Any]:
    providers = [str(task["provider"]) for task in tasks]
    owners = _persisted_provider_owners(tasks)
    publishable_decision = decision.status in {"success", "partial_success"}
    success_by_owner = {
        str(task["provider"]): bool(
            publishable_decision
            and
            task.get("job_status") == "success"
            and task.get("attempt_status") == "success"
            and task.get("attempt_contract_matches") is True
        )
        for task in tasks
    }
    required = [task for task in tasks if task.get("required") is not False]
    required_providers = {str(task["provider"]) for task in required}
    provider_results: list[dict[str, Any]] = []
    concrete_results: list[dict[str, Any]] = []
    seen_concrete: set[str] = set()
    for task in tasks:
        provider = str(task["provider"])
        evidence = task.get("attempt_evidence")
        result_evidence = evidence.get("result") if isinstance(evidence, dict) else None
        task_result = result_evidence.get("task_result") if isinstance(result_evidence, dict) else None
        raw_provider_results = task_result.get("provider_results") if isinstance(task_result, dict) else None
        raw_concrete = task_result.get("concrete_provider_results") if isinstance(task_result, dict) else None
        raw_owners = task_result.get("course_provider_owners") if isinstance(task_result, dict) else None
        has_task_result = isinstance(raw_provider_results, list) and len(raw_provider_results) == 1
        if has_task_result:
            provider_result = raw_provider_results[0]
            if (
                not isinstance(provider_result, dict)
                or str(provider_result.get("provider") or "").strip().upper() != provider
                or not isinstance(provider_result.get("success"), bool)
                or provider_result.get("success") is not success_by_owner[provider]
            ):
                raise RuntimeError("task provider result is invalid")
            provider_results.append(dict(provider_result))
            if not isinstance(raw_owners, dict):
                raise RuntimeError("task ownership evidence is missing")
            expected_owned = {concrete for concrete, owner in owners.items() if owner == provider}
            normalized_raw_owners = {
                str(concrete).strip().upper(): str(owner).strip().upper()
                for concrete, owner in raw_owners.items()
            }
            expected_raw_owners = {concrete: provider for concrete in expected_owned}
            if normalized_raw_owners != expected_raw_owners:
                raise RuntimeError("task ownership evidence differs from the batch")
            if provider in AGGREGATE_PROVIDER_OWNERS:
                if not isinstance(raw_concrete, list):
                    raise RuntimeError("aggregate task has no concrete result evidence")
                actual_concrete: set[str] = set()
                for item in raw_concrete:
                    if not isinstance(item, dict):
                        raise RuntimeError("aggregate concrete result is invalid")
                    concrete = str(item.get("provider") or "").strip().upper()
                    if (
                        concrete in seen_concrete
                        or str(item.get("scheduled_owner") or "").strip().upper() != provider
                        or concrete not in expected_owned
                    ):
                        raise RuntimeError("aggregate concrete result ownership is invalid")
                    for field in (
                        "targets_total",
                        "targets_succeeded",
                        "collected_courses",
                        "saved_courses",
                    ):
                        value = item.get(field)
                        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                            raise RuntimeError("aggregate concrete result counters are invalid")
                    if (
                        item["targets_total"] <= 0
                        or item["targets_succeeded"] > item["targets_total"]
                        or (item.get("success") is True and item["targets_succeeded"] != item["targets_total"])
                    ):
                        raise RuntimeError("aggregate concrete result completion is invalid")
                    actual_concrete.add(concrete)
                    seen_concrete.add(concrete)
                    concrete_results.append(dict(item))
                if actual_concrete != expected_owned:
                    raise RuntimeError("aggregate concrete result set is incomplete")
        else:
            if success_by_owner[provider]:
                raise RuntimeError("successful task has no exact provider result")
            provider_results.append(
                {
                    "provider": provider,
                    "success": False,
                    "exit_code": None,
                    "collected_courses": sum(
                        count for concrete, count in provider_counts.items() if owners.get(concrete) == provider
                    ),
                    "limit": None,
                }
            )
    completed = sum(1 for provider in required_providers if success_by_owner.get(provider, False))
    complete = bool(
        decision.status == "success"
        and provider_counts
        and completed == len(required_providers)
    )
    close_missing = bool(
        complete
        and required
        and all(task.get("close_missing_eligible") is True for task in required)
    )
    return {
        "control_plane": True,
        "control_batch_id": batch["id"],
        "scheduled_slot": (
            batch["scheduled_slot"].isoformat()
            if hasattr(batch["scheduled_slot"], "isoformat")
            else str(batch["scheduled_slot"])
        ),
        "code_version": batch["code_version"],
        "artifact_digest": batch["artifact_digest"],
        "config_revision": batch["config_revision"],
        "providers_requested": providers,
        "provider_results": provider_results,
        "providers_total": len(required_providers),
        "providers_completed": completed,
        "providers_failed": len(required_providers) - completed,
        "failed_providers": sorted(
            provider for provider in required_providers if not success_by_owner.get(provider, False)
        ),
        "concrete_provider_results": concrete_results,
        "concrete_providers_total": len(concrete_results),
        "concrete_providers_completed": sum(1 for item in concrete_results if item["success"]),
        "concrete_providers_failed": sum(1 for item in concrete_results if not item["success"]),
        "course_provider_owners": owners,
        "selected_attempts": _selected_attempts(tasks),
        "provider_course_counts": provider_counts,
        "collection_outcome": decision.status,
        "collection_complete": complete,
        "close_missing_enabled": close_missing,
        "limit": None,
        "branch_code": None,
        "branch_name": None,
        "finalizer_reason": decision.reason,
        "control_plane_rejected": False,
    }


def _publish_staging_batch(
    cursor,
    batch: dict[str, Any],
    tasks: list[dict[str, Any]],
    decision: BatchDecision,
    *,
    promotion_eligible: bool = False,
) -> BatchDecision:
    if promotion_eligible:
        raise RuntimeError("finalizer cannot authorize promotion; use the reviewed approver")
    branch_count, course_count, valid_count, provider_counts = _snapshot_counts(cursor, batch["id"])
    if decision.status == "success" and course_count <= 0:
        decision = BatchDecision("failed", True, "successful_tasks_produced_no_snapshot")
    if decision.status == "success" and valid_count != course_count:
        decision = BatchDecision("failed", True, "successful_tasks_produced_invalid_snapshot")
    try:
        result = _batch_result(batch, tasks, decision, provider_counts)
    except (ValueError, RuntimeError) as exc:
        decision = BatchDecision("failed", True, f"invalid_task_evidence:{type(exc).__name__}")
        result = {
            "control_plane": True,
            "control_batch_id": batch["id"],
            "scheduled_slot": (
                batch["scheduled_slot"].isoformat()
                if hasattr(batch["scheduled_slot"], "isoformat")
                else str(batch["scheduled_slot"])
            ),
            "code_version": batch["code_version"],
            "artifact_digest": batch["artifact_digest"],
            "config_revision": batch["config_revision"],
            "providers_requested": [str(task["provider"]) for task in tasks],
            "provider_results": [],
            "providers_total": len([task for task in tasks if task.get("required") is not False]),
            "providers_completed": 0,
            "providers_failed": len([task for task in tasks if task.get("required") is not False]),
            "failed_providers": [str(task["provider"]) for task in tasks],
            "concrete_provider_results": [],
            "concrete_providers_total": 0,
            "concrete_providers_completed": 0,
            "concrete_providers_failed": 0,
            "course_provider_owners": {},
            "selected_attempts": [],
            "provider_course_counts": provider_counts,
            "collection_outcome": "failed",
            "collection_complete": False,
            "close_missing_enabled": False,
            "limit": None,
            "branch_code": None,
            "branch_name": None,
            "finalizer_reason": decision.reason,
            "control_plane_rejected": True,
        }
    result["promotion_eligible"] = promotion_eligible
    result["promotion_policy"] = "automatic" if promotion_eligible else "held"
    staging_status = "COLLECTED" if decision.status == "success" else "FAILED"
    cursor.execute(
        """
        INSERT INTO crawl_batches (
            crawl_batch_id, source_host, mode, providers, status,
            started_at, finished_at, total_branches, total_courses,
            valid_courses, invalid_courses, result
        )
        VALUES (
            %s, %s, 'distributed', %s, %s,
            COALESCE(%s, CURRENT_TIMESTAMP), CURRENT_TIMESTAMP, %s, %s, %s, %s, %s
        )
        ON CONFLICT (crawl_batch_id) DO UPDATE
        SET status = EXCLUDED.status,
            finished_at = EXCLUDED.finished_at,
            total_branches = EXCLUDED.total_branches,
            total_courses = EXCLUDED.total_courses,
            valid_courses = EXCLUDED.valid_courses,
            invalid_courses = EXCLUDED.invalid_courses,
            result = EXCLUDED.result,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            batch["id"],
            socket.gethostname(),
            [str(task["provider"]) for task in tasks],
            staging_status,
            batch.get("started_at") or batch.get("scheduled_slot"),
            branch_count,
            course_count,
            valid_count,
            max(0, course_count - valid_count),
            Json(result),
        ),
    )
    terminal_status = decision.status
    cursor.execute(
        """
        UPDATE ops_crawler_batches
        SET status = %s,
            started_at = COALESCE(started_at, scheduled_slot),
            finished_at = CURRENT_TIMESTAMP
        WHERE id = %s
          AND status IN ('planning', 'queued', 'running', 'finalizing')
        """,
        (terminal_status, batch["id"]),
    )
    if cursor.rowcount != 1:
        raise RuntimeError("crawler batch lost finalizer ownership")
    return decision


def finalize_once(
    connection,
    environment: str,
    *,
    promotion_eligible: bool = False,
) -> BatchDecision | None:
    if promotion_eligible:
        raise RuntimeError("finalizer cannot authorize promotion; use the reviewed approver")
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT pg_try_advisory_xact_lock(hashtext(%s)) AS acquired",
                (f"{FINALIZER_LOCK}:{environment}",),
            )
            leader = cursor.fetchone()
            if not leader or leader.get("acquired") is not True:
                connection.commit()
                return None
            batch = _load_next_batch(cursor, environment)
            if batch is None:
                connection.commit()
                return None
            tasks = _load_tasks(cursor, batch)
            decision = decide_batch(tasks, int(batch["expected_task_count"]))
            if not decision.terminal:
                cursor.execute(
                    """
                    UPDATE ops_crawler_batches
                    SET status = %s,
                        started_at = CASE
                            WHEN %s = 'running' THEN COALESCE(started_at, CURRENT_TIMESTAMP)
                            ELSE started_at
                        END
                    WHERE id = %s
                      AND status IN ('planning', 'queued', 'running', 'finalizing')
                    """,
                    (decision.status, decision.status, batch["id"]),
                )
            else:
                cursor.execute(
                    """
                    UPDATE ops_crawler_batches
                    SET status = 'finalizing', started_at = COALESCE(started_at, scheduled_slot)
                    WHERE id = %s
                      AND status IN ('planning', 'queued', 'running', 'finalizing')
                    """,
                    (batch["id"],),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("crawler batch could not enter finalizing state")
                batch["started_at"] = batch.get("started_at") or batch.get("scheduled_slot")
                decision = _publish_staging_batch(
                    cursor,
                    batch,
                    tasks,
                    decision,
                    promotion_eligible=promotion_eligible,
                )
        connection.commit()
        return decision
    except Exception:
        connection.rollback()
        raise


def run_finalizer(
    environment: str,
    *,
    once: bool = False,
    poll_seconds: int = 10,
    promotion_eligible: bool = False,
) -> int:
    while RUNNING:
        connection = None
        try:
            connection = psycopg2.connect(**finalizer_database_config())
            try:
                decision = finalize_once(
                    connection,
                    environment,
                    promotion_eligible=promotion_eligible,
                )
                if decision is not None:
                    logger.info("Crawler finalizer status=%s reason=%s", decision.status, decision.reason)
            except psycopg2.Error:
                logger.exception("Crawler finalizer database operation failed")
                if once:
                    return 1
            except Exception:
                logger.exception("Crawler finalizer failed closed")
                if once:
                    return 1
        finally:
            if connection is not None:
                connection.close()
        if once:
            return 0
        deadline = time.monotonic() + poll_seconds
        while RUNNING and time.monotonic() < deadline:
            time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
    return 0


def _stop(_signum: int, _frame: object) -> None:
    global RUNNING
    RUNNING = False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Finalize fenced distributed crawler batches")
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=int(os.getenv("OPS_CRAWLER_FINALIZER_POLL_SECONDS", "10")),
    )
    args = parser.parse_args(argv)
    if not 2 <= args.poll_seconds <= 3_600:
        parser.error("--poll-seconds must be between 2 and 3600")
    environment = normalized_environment()
    if environment not in {"production", "staging"}:
        parser.error("ENVIRONMENT must be production or staging")
    try:
        promotion_eligible = auto_promotion_enabled(environment)
    except RuntimeError as exc:
        parser.error(str(exc))
    return run_finalizer(
        environment,
        once=args.once,
        poll_seconds=args.poll_seconds,
        promotion_eligible=promotion_eligible,
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("OPS_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _stop)
    raise SystemExit(main())
