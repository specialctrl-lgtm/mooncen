from __future__ import annotations

import os
from dataclasses import dataclass

from psycopg2.extras import Json, RealDictCursor


SUPPORTED_JOB_TYPES = ("crawler_run", "crawler_retry", "agent_command")
RETRY_BACKOFF_INITIAL_SECONDS = 15
RETRY_BACKOFF_MAX_SECONDS = 3_600


@dataclass(frozen=True)
class ControlRecoveryConfig:
    environment: str


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


def recover_stale_jobs(
    connection,
    config: ControlRecoveryConfig,
    *,
    stale_after_seconds: int,
) -> int:
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute(
            """
            WITH stale AS (
                SELECT id, retry_count, max_retries, lease_token, lease_epoch,
                       cancel_requested_at IS NOT NULL AS was_cancelled,
                       (cancel_requested_at IS NULL AND retry_count < max_retries) AS should_retry
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
                progress = CASE WHEN stale.should_retry THEN 0 ELSE job.progress END,
                retry_count = CASE WHEN stale.should_retry THEN stale.retry_count + 1 ELSE stale.retry_count END,
                available_at = CASE
                    WHEN stale.should_retry THEN CURRENT_TIMESTAMP + make_interval(
                        secs => LEAST(%s, %s * (2 ^ LEAST(stale.retry_count, 20)))::integer
                    )
                    ELSE job.available_at
                END,
                error_code = CASE
                    WHEN stale.was_cancelled THEN 'cancelled_after_lease_expiry'
                    WHEN stale.should_retry THEN 'worker_lease_expired_retry'
                    ELSE 'worker_lease_expired'
                END,
                error_message = CASE
                    WHEN stale.was_cancelled THEN 'Crawler job cancellation was finalized after lease expiry.'
                    ELSE 'Crawler worker lease expired before completion.'
                END,
                agent_id = CASE WHEN stale.should_retry THEN NULL ELSE job.agent_id END,
                lease_token = NULL,
                leased_until = NULL,
                assigned_at = CASE WHEN stale.should_retry THEN NULL ELSE job.assigned_at END,
                started_at = CASE WHEN stale.should_retry THEN NULL ELSE job.started_at END,
                heartbeat_at = CASE WHEN stale.should_retry THEN NULL ELSE CURRENT_TIMESTAMP END,
                finished_at = CASE WHEN stale.should_retry THEN NULL ELSE CURRENT_TIMESTAMP END,
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
                SET status = 'lease_lost', finished_at = CURRENT_TIMESTAMP,
                    error_code = 'worker_lease_expired',
                    error_message = 'Crawler worker lease expired before completion.'
                WHERE job_id = %s AND lease_token = %s AND lease_epoch = %s AND status = 'running'
                """,
                (job["id"], job["expired_lease_token"], job["expired_lease_epoch"]),
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
                WHERE job_id = %s AND lease_token = %s AND lease_epoch = %s AND status = 'lease_lost'
                """,
                (
                    Json({"job_status": job["status"], "retry_count": job["retry_count"]}),
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
                SET status = %s, current_stage = %s,
                    finished_at = CASE WHEN %s THEN NULL ELSE CURRENT_TIMESTAMP END
                WHERE job_id = %s AND status IN ('queued', 'running', 'stopping')
                """,
                (
                    "queued" if requeued else "cancelled" if cancelled else "failed",
                    "retry_scheduled" if requeued else "cancelled" if cancelled else "worker_lease_expired",
                    requeued,
                    job["id"],
                ),
            )
            cursor.execute(
                """
                INSERT INTO ops_job_logs (job_id, log_level, message, metadata)
                VALUES (%s, 'error', %s, %s)
                """,
                (
                    job["id"],
                    "Crawler worker lease expired; the fenced attempt was recovered.",
                    Json(
                        {
                            "reason": "worker_lease_expired",
                            "stale_after_seconds": stale_after_seconds,
                            "disposition": job["status"],
                            "retry_count": job["retry_count"],
                        }
                    ),
                ),
            )
    connection.commit()
    return len(recovered)
