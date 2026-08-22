from __future__ import annotations

import argparse
import logging
import os
import signal
import time
from dataclasses import dataclass, replace
from typing import Any
from uuid import UUID

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import Json, RealDictCursor

from DB.connection_settings import bounded_env_int, database_connect_options
from tools.ops_redaction import redact_text

from .crawler_worker import PROJECT_ROOT, normalized_environment, resolve_local_agent_id


load_dotenv(PROJECT_ROOT / ".env")
logger = logging.getLogger(__name__)
RUNNING = True
SCANNER_NAME = "ops_quality_v1"
SUPPORTED_RULES = (
    "missing_required_title",
    "missing_required_branch",
    "missing_required_period",
    "missing_required_schedule",
    "missing_required_fee",
    "missing_required_url",
    "missing_required_category",
    "date_course_reversed",
    "date_application_reversed",
    "date_abnormal_year",
    "price_negative",
    "price_abnormal_high",
    "location_address_missing",
    "location_coordinates_missing",
    "location_out_of_korea",
    "duplicate_raw_url",
)


@dataclass(frozen=True)
class WorkerConfig:
    environment: str
    agent_id: UUID | None
    poll_interval: float
    statement_timeout_ms: int


def queue_database_config() -> dict[str, Any]:
    host = os.getenv(
        "OPS_QUALITY_QUEUE_DB_HOST",
        os.getenv("OPS_QUEUE_DB_HOST", os.getenv("DB_HOST", "localhost")),
    ).strip()
    port = bounded_env_int(
        "OPS_QUALITY_QUEUE_DB_PORT",
        bounded_env_int("OPS_QUEUE_DB_PORT", bounded_env_int("DB_PORT", 5432, 1, 65535), 1, 65535),
        1,
        65535,
    )
    database = os.getenv(
        "OPS_QUALITY_QUEUE_DB_NAME",
        os.getenv("OPS_QUEUE_DB_NAME", os.getenv("DB_NAME", "mooncen")),
    ).strip()
    user = os.getenv("OPS_QUALITY_QUEUE_DB_USER", os.getenv("DB_CHECK_USER", "")).strip()
    password = os.getenv("OPS_QUALITY_QUEUE_DB_PASSWORD", os.getenv("DB_CHECK_PASSWORD", ""))
    if normalized_environment() == "production":
        if not host or not database or not user or not password:
            raise RuntimeError("Production Ops quality worker requires explicit queue database credentials")
        owner = (
            os.getenv("DB_OWNER_USER", "").strip()
            or os.getenv("DB_MIGRATOR_USER", "").strip()
            or os.getenv("DB_USER", "").strip()
        )
        if owner and user == owner:
            raise RuntimeError("Ops quality worker must not use the database owner role")
    return {
        "host": host,
        "port": port,
        "database": database,
        "user": user or os.getenv("DB_USER", "mooncen_check_login"),
        "password": password or os.getenv("DB_PASSWORD", ""),
        **database_connect_options(host, "mooncen-ops-quality-worker"),
    }


def connect_queue(config: WorkerConfig):
    connection = psycopg2.connect(**queue_database_config())
    connection.autocommit = False
    with connection.cursor() as cursor:
        cursor.execute("SET statement_timeout = %s", (config.statement_timeout_ms,))
        cursor.execute("SET lock_timeout = '5s'")
    connection.commit()
    return connection


def _append_log(connection, job_id: str, level: str, message: str, metadata: dict[str, Any] | None = None) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO ops_job_logs (job_id, log_level, message, metadata)
            VALUES (%s, %s, %s, %s)
            """,
            (job_id, level, redact_text(message, maximum=4_000), Json(metadata or {})),
        )
    connection.commit()


def _claim_job(connection, config: WorkerConfig) -> dict[str, Any] | None:
    agent_clause = "AND agent_id IS NULL"
    params: list[Any] = [config.environment]
    if config.agent_id:
        agent_clause = "AND (agent_id IS NULL OR agent_id = %s)"
        params.append(str(config.agent_id))
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute(
            f"""
            SELECT id::text, status, environment, parameters, target_key
            FROM ops_jobs
            WHERE status = 'queued'
              AND environment = %s
              AND job_type = 'data_quality_scan'
              {agent_clause}
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
        cursor.execute(
            """
            UPDATE ops_jobs
            SET status = 'assigned',
                agent_id = COALESCE(%s, agent_id),
                assigned_at = CURRENT_TIMESTAMP,
                heartbeat_at = CURRENT_TIMESTAMP,
                progress = 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND status = 'queued'
            RETURNING id
            """,
            (str(config.agent_id) if config.agent_id else None, row["id"]),
        )
        if cursor.fetchone() is None:
            connection.rollback()
            return None
    connection.commit()
    return dict(row)


def _validated_parameters(value: Any) -> dict[str, str | None]:
    if not isinstance(value, dict):
        raise ValueError("quality scan parameters must be an object")
    content_type = str(value.get("content_type") or "all").strip()
    if content_type not in {"all", "culture_center", "experience", "education"}:
        raise ValueError("unsupported content_type")
    provider = str(value.get("provider") or "").strip() or None
    branch = str(value.get("branch") or "").strip() or None
    if provider and (len(provider) > 100 or "\x00" in provider):
        raise ValueError("provider is invalid")
    if branch and (len(branch) > 160 or "\x00" in branch):
        raise ValueError("branch is invalid")
    return {"content_type": content_type, "provider": provider, "branch": branch}


def _mark_running(connection, job_id: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE ops_jobs
            SET status = 'running', progress = 5,
                started_at = CURRENT_TIMESTAMP,
                heartbeat_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND status = 'assigned'
            """,
            (job_id,),
        )
    connection.commit()


def _scan_course_rules(connection, job_id: str, parameters: dict[str, str | None]) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            WITH scoped_courses AS (
                SELECT
                    c.*,
                    b.name AS branch_name,
                    b.address AS branch_address,
                    b.lat AS branch_lat,
                    b.lon AS branch_lon,
                    CASE
                        WHEN c.service_group = '문화센터' THEN 'culture_center'
                        WHEN c.service_group = '체험' THEN 'experience'
                        WHEN c.service_group = '공공강좌' THEN 'education'
                        ELSE 'unknown'
                    END AS ops_content_type
                FROM courses c
                LEFT JOIN branches b ON b.id = c.branch_id
                WHERE c.is_active = true
                  AND (
                      %s = 'all'
                      OR CASE
                          WHEN c.service_group = '문화센터' THEN 'culture_center'
                          WHEN c.service_group = '체험' THEN 'experience'
                          WHEN c.service_group = '공공강좌' THEN 'education'
                          ELSE 'unknown'
                      END = %s
                  )
                  AND (%s IS NULL OR c.provider = %s)
                  AND (%s IS NULL OR b.name = %s)
            ),
            detected AS (
                SELECT
                    c.id,
                    c.provider,
                    c.branch_name,
                    c.ops_content_type,
                    rule.issue_type,
                    rule.severity,
                    rule.field_name,
                    rule.blocked_sync,
                    rule.current_value
                FROM scoped_courses c
                CROSS JOIN LATERAL (
                    VALUES
                        (
                            'missing_required_title',
                            'critical',
                            'title',
                            true,
                            to_jsonb(c.title),
                            btrim(COALESCE(c.title, '')) = ''
                        ),
                        (
                            'missing_required_branch',
                            'critical',
                            'branch_id',
                            true,
                            to_jsonb(c.branch_id::text),
                            c.branch_id IS NULL
                        ),
                        (
                            'missing_required_period',
                            'warning',
                            'start_date,end_date',
                            false,
                            jsonb_build_object('start_date', c.start_date, 'end_date', c.end_date),
                            c.start_date IS NULL AND c.end_date IS NULL
                        ),
                        (
                            'missing_required_schedule',
                            'warning',
                            'schedule_days,schedule_raw',
                            false,
                            jsonb_build_object('schedule_days', c.schedule_days, 'schedule_raw', c.schedule_raw),
                            COALESCE(array_length(c.schedule_days, 1), 0) = 0
                                AND btrim(COALESCE(c.schedule_raw, '')) = ''
                        ),
                        (
                            'missing_required_fee',
                            'warning',
                            'fee',
                            false,
                            to_jsonb(c.fee),
                            c.fee IS NULL
                        ),
                        (
                            'missing_required_url',
                            'critical',
                            'raw_url,application_url',
                            true,
                            jsonb_build_object('raw_url', c.raw_url, 'application_url', c.application_url),
                            btrim(COALESCE(c.raw_url, c.application_url, '')) = ''
                        ),
                        (
                            'missing_required_category',
                            'warning',
                            'standard_category_key,category_raw',
                            false,
                            jsonb_build_object(
                                'standard_category_key', c.standard_category_key,
                                'category_raw', c.category_raw
                            ),
                            btrim(COALESCE(c.standard_category_key, c.category_raw, '')) = ''
                        ),
                        (
                            'date_course_reversed',
                            'critical',
                            'start_date,end_date',
                            true,
                            jsonb_build_object('start_date', c.start_date, 'end_date', c.end_date),
                            c.start_date IS NOT NULL AND c.end_date IS NOT NULL
                                AND c.start_date > c.end_date
                        ),
                        (
                            'date_application_reversed',
                            'critical',
                            'apply_start,apply_end',
                            true,
                            jsonb_build_object('apply_start', c.apply_start, 'apply_end', c.apply_end),
                            c.apply_start IS NOT NULL AND c.apply_end IS NOT NULL
                                AND c.apply_start > c.apply_end
                        ),
                        (
                            'date_abnormal_year',
                            'warning',
                            'start_date,end_date',
                            false,
                            jsonb_build_object('start_date', c.start_date, 'end_date', c.end_date),
                            (
                                c.start_date IS NOT NULL
                                AND EXTRACT(YEAR FROM c.start_date) NOT BETWEEN 2000 AND 2100
                            ) OR (
                                c.end_date IS NOT NULL
                                AND EXTRACT(YEAR FROM c.end_date) NOT BETWEEN 2000 AND 2100
                            )
                        ),
                        (
                            'price_negative',
                            'critical',
                            'fee',
                            true,
                            to_jsonb(c.fee),
                            c.fee < 0
                        ),
                        (
                            'price_abnormal_high',
                            'warning',
                            'fee',
                            true,
                            to_jsonb(c.fee),
                            c.fee > 100000000
                        ),
                        (
                            'location_address_missing',
                            'warning',
                            'branch.address',
                            false,
                            to_jsonb(c.branch_address),
                            c.branch_id IS NOT NULL AND btrim(COALESCE(c.branch_address, '')) = ''
                        ),
                        (
                            'location_coordinates_missing',
                            'warning',
                            'branch.lat,branch.lon',
                            false,
                            jsonb_build_object('lat', c.branch_lat, 'lon', c.branch_lon),
                            c.branch_id IS NOT NULL AND (c.branch_lat IS NULL OR c.branch_lon IS NULL)
                        ),
                        (
                            'location_out_of_korea',
                            'critical',
                            'branch.lat,branch.lon',
                            true,
                            jsonb_build_object('lat', c.branch_lat, 'lon', c.branch_lon),
                            c.branch_lat IS NOT NULL AND c.branch_lon IS NOT NULL
                                AND NOT (
                                    c.branch_lat BETWEEN 32.0 AND 39.5
                                    AND c.branch_lon BETWEEN 123.0 AND 132.5
                                )
                        )
                ) AS rule(
                    issue_type,
                    severity,
                    field_name,
                    blocked_sync,
                    current_value,
                    matches
                )
                WHERE rule.matches
                  AND c.ops_content_type <> 'unknown'
            )
            INSERT INTO ops_quality_issues (
                issue_key, issue_type, severity, content_type,
                resource_type, resource_id, provider, branch, field_name,
                current_value, status, auto_fixable, blocked_sync,
                detected_at, metadata, created_at, updated_at
            )
            SELECT
                detected.issue_type || ':course:' || detected.id::text,
                detected.issue_type,
                detected.severity,
                detected.ops_content_type,
                'course',
                detected.id::text,
                detected.provider,
                detected.branch_name,
                detected.field_name,
                detected.current_value,
                'open',
                false,
                detected.blocked_sync,
                CURRENT_TIMESTAMP,
                jsonb_build_object('scanner', %s, 'last_scan_job_id', %s),
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            FROM detected
            ON CONFLICT (issue_key)
                WHERE issue_key IS NOT NULL
                  AND btrim(issue_key) <> ''
                  AND status IN ('open', 'reviewing')
            DO UPDATE SET
                severity = EXCLUDED.severity,
                content_type = EXCLUDED.content_type,
                provider = EXCLUDED.provider,
                branch = EXCLUDED.branch,
                field_name = EXCLUDED.field_name,
                current_value = EXCLUDED.current_value,
                blocked_sync = EXCLUDED.blocked_sync,
                detected_at = EXCLUDED.detected_at,
                metadata = ops_quality_issues.metadata || EXCLUDED.metadata,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                parameters["content_type"],
                parameters["content_type"],
                parameters["provider"],
                parameters["provider"],
                parameters["branch"],
                parameters["branch"],
                SCANNER_NAME,
                job_id,
            ),
        )
        affected = cursor.rowcount
    connection.commit()
    return max(0, affected)


def _scan_duplicate_urls(connection, job_id: str, parameters: dict[str, str | None]) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            WITH scoped AS (
                SELECT
                    c.id,
                    c.provider,
                    c.raw_url,
                    b.name AS branch_name,
                    CASE
                        WHEN c.service_group = '문화센터' THEN 'culture_center'
                        WHEN c.service_group = '체험' THEN 'experience'
                        WHEN c.service_group = '공공강좌' THEN 'education'
                        ELSE 'unknown'
                    END AS content_type,
                    COUNT(*) OVER (PARTITION BY c.raw_url) AS duplicate_count
                FROM courses c
                LEFT JOIN branches b ON b.id = c.branch_id
                WHERE c.is_active = true
                  AND btrim(COALESCE(c.raw_url, '')) <> ''
                  AND (%s IS NULL OR c.provider = %s)
                  AND (%s IS NULL OR b.name = %s)
            )
            INSERT INTO ops_quality_issues (
                issue_key, issue_type, severity, content_type,
                resource_type, resource_id, provider, branch, field_name,
                current_value, status, auto_fixable, blocked_sync,
                detected_at, metadata, created_at, updated_at
            )
            SELECT
                'duplicate_raw_url:course:' || id::text,
                'duplicate_raw_url',
                'warning',
                content_type,
                'course',
                id::text,
                provider,
                branch_name,
                'raw_url',
                jsonb_build_object('raw_url', raw_url, 'duplicate_count', duplicate_count),
                'open',
                false,
                false,
                CURRENT_TIMESTAMP,
                jsonb_build_object('scanner', %s, 'last_scan_job_id', %s),
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            FROM scoped
            WHERE duplicate_count > 1
              AND content_type <> 'unknown'
              AND (%s = 'all' OR content_type = %s)
            ON CONFLICT (issue_key)
                WHERE issue_key IS NOT NULL
                  AND btrim(issue_key) <> ''
                  AND status IN ('open', 'reviewing')
            DO UPDATE SET
                content_type = EXCLUDED.content_type,
                provider = EXCLUDED.provider,
                branch = EXCLUDED.branch,
                current_value = EXCLUDED.current_value,
                detected_at = EXCLUDED.detected_at,
                metadata = ops_quality_issues.metadata || EXCLUDED.metadata,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                parameters["provider"],
                parameters["provider"],
                parameters["branch"],
                parameters["branch"],
                SCANNER_NAME,
                job_id,
                parameters["content_type"],
                parameters["content_type"],
            ),
        )
        affected = cursor.rowcount
    connection.commit()
    return max(0, affected)


def _resolve_cleared_issues(connection, job_id: str, parameters: dict[str, str | None]) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE ops_quality_issues
            SET status = 'resolved',
                resolved_at = CURRENT_TIMESTAMP,
                metadata = metadata || jsonb_build_object(
                    'resolution_action', 'cleared_by_rescan',
                    'resolution_scan_job_id', %s
                ),
                updated_at = CURRENT_TIMESTAMP
            WHERE status IN ('open', 'reviewing')
              AND metadata->>'scanner' = %s
              AND COALESCE(metadata->>'last_scan_job_id', '') <> %s
              AND issue_type = ANY(%s)
              AND (%s = 'all' OR content_type = %s)
              AND (%s IS NULL OR provider = %s)
              AND (%s IS NULL OR branch = %s)
            """,
            (
                job_id,
                SCANNER_NAME,
                job_id,
                list(SUPPORTED_RULES),
                parameters["content_type"],
                parameters["content_type"],
                parameters["provider"],
                parameters["provider"],
                parameters["branch"],
                parameters["branch"],
            ),
        )
        resolved = cursor.rowcount
    connection.commit()
    return max(0, resolved)


def _finish_job(
    connection,
    job_id: str,
    *,
    status: str,
    result: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE ops_jobs
            SET status = %s,
                progress = CASE WHEN %s = 'success' THEN 100 ELSE progress END,
                result = %s,
                error_code = %s,
                error_message = %s,
                heartbeat_at = CURRENT_TIMESTAMP,
                finished_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (
                status,
                status,
                Json(result or {}),
                error_code,
                redact_text(error_message, maximum=2_000) if error_message else None,
                job_id,
            ),
        )
    connection.commit()


def execute_job(connection, job: dict[str, Any]) -> None:
    job_id = str(job["id"])
    try:
        parameters = _validated_parameters(job.get("parameters"))
    except ValueError as exc:
        _append_log(connection, job_id, "error", str(exc))
        _finish_job(connection, job_id, status="blocked", error_code="invalid_parameters", error_message=str(exc))
        return

    _mark_running(connection, job_id)
    _append_log(connection, job_id, "info", "운영 DB 기준 품질 규칙 검사를 시작합니다.", parameters)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT cancel_requested_at IS NOT NULL FROM ops_jobs WHERE id = %s",
                (job_id,),
            )
            cancelled = bool(cursor.fetchone()[0])
        connection.commit()
        if cancelled:
            _finish_job(connection, job_id, status="cancelled", result={"cancelled_before_scan": True})
            return
        course_issues = _scan_course_rules(connection, job_id, parameters)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE ops_jobs
                SET progress = 65, heartbeat_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (job_id,),
            )
        connection.commit()
        duplicate_issues = _scan_duplicate_urls(connection, job_id, parameters)
        resolved = _resolve_cleared_issues(connection, job_id, parameters)
        result = {
            "scanner": SCANNER_NAME,
            "course_rule_rows": course_issues,
            "duplicate_rule_rows": duplicate_issues,
            "cleared_issue_rows": resolved,
            "scope": parameters,
        }
        _finish_job(connection, job_id, status="success", result=result)
        _append_log(connection, job_id, "info", "품질 검사가 완료되었습니다.", result)
    except Exception as exc:
        connection.rollback()
        logger.exception("Ops quality scan failed job_id=%s", job_id)
        _finish_job(
            connection,
            job_id,
            status="failed",
            error_code="quality_scan_failed",
            error_message=f"{type(exc).__name__}: quality scan failed",
        )
        _append_log(connection, job_id, "error", f"{type(exc).__name__}: 품질 검사에 실패했습니다.")


def _handle_signal(_signum, _frame) -> None:
    global RUNNING
    RUNNING = False


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MoonCen PostgreSQL-backed Ops data-quality worker")
    parser.add_argument("--once", action="store_true", help="Claim at most one quality job and exit")
    parser.add_argument("--agent-id", default=os.getenv("OPS_QUALITY_AGENT_ID", ""), help="Registered ops_agents UUID")
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=float(os.getenv("OPS_QUALITY_JOB_POLL_INTERVAL_SECONDS", "3")),
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
    config = WorkerConfig(
        environment=normalized_environment(),
        agent_id=args.agent_id,
        poll_interval=args.poll_interval,
        statement_timeout_ms=bounded_env_int("OPS_QUALITY_STATEMENT_TIMEOUT_MS", 900_000, 10_000, 3_600_000),
    )
    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_signal)
    connection = connect_queue(config)
    try:
        if config.agent_id is None:
            config = replace(config, agent_id=resolve_local_agent_id(connection, config.environment))
        while RUNNING:
            try:
                job = _claim_job(connection, config)
                if job:
                    execute_job(connection, job)
                elif args.once:
                    return 0
            except psycopg2.Error:
                connection.rollback()
                logger.exception("Ops quality queue database operation failed")
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
