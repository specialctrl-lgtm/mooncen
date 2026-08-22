"""Durably ingest worker release-agent spool files into the shared control DB."""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg2
from psycopg2.extras import Json, RealDictCursor

from ops_agent.crawler_control_db import reporter_database_config
from ops_agent.crawler_worker import (
    configured_worker_hostname,
    normalized_environment,
)


MAX_REPORT_BYTES = 64 * 1024
REPORT_STATUSES = frozenset(
    {"pending", "downloading", "installing", "verifying", "ready", "failed", "rolled_back", "drifted"}
)
WORKER_KEY = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")


class ReleaseReportError(RuntimeError):
    pass


def _uuid(value: Any, field: str) -> str:
    text = str(value or "")
    try:
        canonical = str(UUID(text))
    except (ValueError, AttributeError) as exc:
        raise ReleaseReportError(f"release report {field} is invalid") from exc
    if text != canonical:
        raise ReleaseReportError(f"release report {field} is not canonical")
    return canonical


def _timestamp(value: Any) -> datetime:
    text = str(value or "")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReleaseReportError("release report timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise ReleaseReportError("release report timestamp must include a timezone")
    normalized = parsed.astimezone(timezone.utc)
    if normalized > datetime.now(timezone.utc).replace(microsecond=999999):
        raise ReleaseReportError("release report timestamp is in the future")
    return normalized


def parse_report(path: Path, *, environment: str, worker_key: str) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_REPORT_BYTES:
            raise ReleaseReportError("release report spool entry is unsafe")
        encoded = path.read_bytes()
    except OSError as exc:
        raise ReleaseReportError("release report spool entry is unavailable") from exc
    if not encoded or len(encoded) > MAX_REPORT_BYTES:
        raise ReleaseReportError("release report size is invalid")
    try:
        payload = json.loads(encoded.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseReportError("release report is invalid JSON") from exc
    required = {
        "schema_version",
        "id",
        "environment",
        "worker_key",
        "rollout_id",
        "desired_generation",
        "status",
        "code_version",
        "artifact_digest",
        "config_revision",
        "health",
        "error_code",
        "error_message",
        "reported_at",
    }
    if type(payload) is not dict or set(payload) != required or payload.get("schema_version") != 1:
        raise ReleaseReportError("release report contract is invalid")
    if payload.get("environment") != environment or payload.get("worker_key") != worker_key:
        raise ReleaseReportError("release report identity differs from this worker")
    if not WORKER_KEY.fullmatch(worker_key):
        raise ReleaseReportError("configured worker key is invalid")
    report_id = _uuid(payload.get("id"), "id")
    rollout_id = _uuid(payload.get("rollout_id"), "rollout_id")
    filename_suffix = path.stem.rsplit("-", 5)[-5:]
    if "-".join(filename_suffix) != report_id:
        raise ReleaseReportError("release report filename does not match its id")
    generation = payload.get("desired_generation")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation <= 0:
        raise ReleaseReportError("release report generation is invalid")
    status = payload.get("status")
    if status not in REPORT_STATUSES:
        raise ReleaseReportError("release report status is invalid")
    health = payload.get("health")
    if type(health) is not dict or type(health.get("healthy")) is not bool:
        raise ReleaseReportError("release report health is invalid")
    expected_healthy = status in {"ready", "rolled_back"}
    if health["healthy"] is not expected_healthy:
        raise ReleaseReportError("release report status and health differ")
    normalized: dict[str, Any] = {
        **payload,
        "id": report_id,
        "rollout_id": rollout_id,
        "reported_at": _timestamp(payload.get("reported_at")),
    }
    for field, pattern in (
        ("artifact_digest", DIGEST),
        ("code_version", VERSION),
        ("config_revision", VERSION),
    ):
        value = str(payload.get(field) or "")
        if value and not pattern.fullmatch(value):
            raise ReleaseReportError(f"release report {field} is invalid")
        normalized[field] = value or None
    for field in ("error_code", "error_message"):
        value = payload.get(field)
        if value is not None and (type(value) is not str or len(value) > 512 or "\x00" in value):
            raise ReleaseReportError(f"release report {field} is invalid")
    return normalized


def _verified_agent_id(connection, environment: str, requested: str) -> str:
    agent_id = _uuid(requested, "agent_id")
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id::text
            FROM ops_agents
            WHERE id = %s
              AND environment = %s
              AND hostname = %s
              AND status <> 'disabled'
            """,
            (agent_id, environment, configured_worker_hostname(environment)),
        )
        row = cursor.fetchone()
    connection.commit()
    if not row:
        raise ReleaseReportError("release reporter agent identity is not registered")
    return str(row[0])


def ingest_report(connection, report: dict[str, Any], agent_id: str) -> bool:
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM ops_crawler_worker_desired_state
                WHERE environment = %s
                  AND worker_key = %s
                  AND rollout_id = %s
                  AND generation = %s
                  AND (agent_id IS NULL OR agent_id = %s)
                """,
                (
                    report["environment"],
                    report["worker_key"],
                    report["rollout_id"],
                    report["desired_generation"],
                    agent_id,
                ),
            )
            if cursor.fetchone() is None:
                raise ReleaseReportError("release report has no matching central desired state")
            cursor.execute(
                """
                INSERT INTO ops_crawler_release_reports (
                    id, rollout_id, environment, worker_key, agent_id,
                    desired_generation, status, artifact_digest, code_version,
                    config_revision, health, error_code, error_message, reported_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    report["id"],
                    report["rollout_id"],
                    report["environment"],
                    report["worker_key"],
                    agent_id,
                    report["desired_generation"],
                    report["status"],
                    report["artifact_digest"],
                    report["code_version"],
                    report["config_revision"],
                    Json(report["health"]),
                    report["error_code"],
                    report["error_message"],
                    report["reported_at"],
                ),
            )
            inserted = cursor.rowcount == 1
            if not inserted:
                cursor.execute(
                    """
                    SELECT (
                        rollout_id = %s AND environment = %s AND worker_key = %s
                        AND agent_id = %s AND desired_generation = %s AND status = %s
                        AND artifact_digest IS NOT DISTINCT FROM %s
                        AND code_version IS NOT DISTINCT FROM %s
                        AND config_revision IS NOT DISTINCT FROM %s
                        AND health = %s AND error_code IS NOT DISTINCT FROM %s
                        AND error_message IS NOT DISTINCT FROM %s
                    ) AS identical
                    FROM ops_crawler_release_reports
                    WHERE id = %s
                    """,
                    (
                        report["rollout_id"],
                        report["environment"],
                        report["worker_key"],
                        agent_id,
                        report["desired_generation"],
                        report["status"],
                        report["artifact_digest"],
                        report["code_version"],
                        report["config_revision"],
                        Json(report["health"]),
                        report["error_code"],
                        report["error_message"],
                        report["id"],
                    ),
                )
                row = cursor.fetchone()
                if not row or row.get("identical") is not True:
                    raise ReleaseReportError("release report id conflicts with different evidence")
        connection.commit()
        return inserted
    except Exception:
        connection.rollback()
        raise


def _assert_reporter_database_role(connection, environment: str) -> None:
    if environment not in {"production", "staging"}:
        return
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT pg_has_role(session_user, 'mooncen_crawler_reporter', 'member'),
                   pg_has_role(session_user, 'mooncen_crawler_worker', 'member'),
                   session_user::text
            """
        )
        row = cursor.fetchone()
    if not row or row[0] is not True or row[1] is not False:
        raise ReleaseReportError("release reporter database login has an unsafe role membership")


def flush_spool(directory: Path, *, environment: str, worker_key: str, agent_id: str) -> int:
    if directory.is_symlink() or not directory.is_dir():
        raise ReleaseReportError("release report spool directory is unavailable")
    connection = psycopg2.connect(**reporter_database_config())
    acknowledged = 0
    try:
        _assert_reporter_database_role(connection, environment)
        verified_agent = _verified_agent_id(connection, environment, agent_id)
        for path in sorted(directory.glob("*.json")):
            report = parse_report(path, environment=environment, worker_key=worker_key)
            ingest_report(connection, report, verified_agent)
            path.unlink()
            acknowledged += 1
    finally:
        connection.close()
    return acknowledged


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest crawler release reports into the shared control DB")
    parser.add_argument(
        "--spool",
        default=os.path.join(
            os.getenv("OPS_CRAWLER_RELEASE_STATE_DIR", "/var/lib/mooncen-crawler-release-agent"),
            "reports",
        ),
    )
    parser.add_argument("--worker-key", default=os.getenv("OPS_CRAWLER_WORKER_ID", ""))
    parser.add_argument("--agent-id", default=os.getenv("OPS_AGENT_ID", ""))
    args = parser.parse_args(argv)
    environment = normalized_environment()
    if environment not in {"production", "staging"}:
        parser.error("ENVIRONMENT must be production or staging")
    if not args.worker_key or not args.agent_id or not Path(args.spool).is_absolute():
        parser.error("worker key, agent id, and an absolute spool path are required")
    flush_spool(
        Path(args.spool),
        environment=environment,
        worker_key=args.worker_key,
        agent_id=args.agent_id,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
