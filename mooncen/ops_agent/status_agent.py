from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import re
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import Json

from DB.connection_settings import bounded_env_int, database_connect_options
from tools.ops_redaction import redact_text

from .crawler_worker import PROJECT_ROOT, normalized_environment
load_dotenv(PROJECT_ROOT / ".env")
logger = logging.getLogger(__name__)
SERVICE_HOST_PATTERN = re.compile(r"^[A-Za-z0-9:][A-Za-z0-9._:-]{0,252}$")


@dataclass(frozen=True)
class ServiceStatus:
    name: str
    service_type: str
    status: str
    response_time_ms: int | None = None
    health_url: str | None = None
    error: str | None = None
    dependencies: tuple[str, ...] = ()
    service_host: str | None = None


def _normalized_service_host(value: str | None) -> str | None:
    host = str(value or "").strip().rstrip(".")
    if host.startswith("/"):
        return socket.gethostname()
    if SERVICE_HOST_PATTERN.fullmatch(host):
        return host
    return None


def database_config() -> dict[str, Any]:
    host = os.getenv("OPS_STATUS_DB_HOST", os.getenv("DB_HOST", "localhost")).strip()
    return {
        "host": host,
        "port": bounded_env_int(
            "OPS_STATUS_DB_PORT",
            bounded_env_int("DB_PORT", 5432, 1, 65_535),
            1,
            65_535,
        ),
        "database": os.getenv("OPS_STATUS_DB_NAME", os.getenv("DB_NAME", "mooncen")).strip(),
        "user": os.getenv("OPS_STATUS_DB_USER", os.getenv("DB_API_USER", os.getenv("DB_USER", "mooncen_api"))).strip(),
        "password": os.getenv(
            "OPS_STATUS_DB_PASSWORD",
            os.getenv("DB_API_PASSWORD", os.getenv("DB_PASSWORD", "")),
        ),
        **database_connect_options(host, "mooncen-ops-status-agent"),
    }


def connect_database():
    connection = psycopg2.connect(**database_config())
    connection.autocommit = False
    return connection


def _configured_http_url(name: str, default: str) -> str:
    value = str(os.getenv(name) or default).strip().rstrip("/")
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError(f"{name} must be a plain HTTP origin or health URL")
    return value


def _http_status(name: str, service_type: str, url: str) -> ServiceStatus:
    service_host = _normalized_service_host(urllib.parse.urlsplit(url).hostname)
    started = time.perf_counter()
    request = urllib.request.Request(url, headers={"User-Agent": "mooncen-ops-status-agent"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 - operator-configured status URL.
            response.read(64 * 1024)
            status_code = int(getattr(response, "status", 0) or 0)
        latency = round((time.perf_counter() - started) * 1_000)
        if 200 <= status_code < 400:
            return ServiceStatus(
                name,
                service_type,
                "healthy",
                latency,
                url,
                service_host=service_host,
            )
        return ServiceStatus(
            name,
            service_type,
            "warning",
            latency,
            url,
            f"HTTP {status_code}",
            service_host=service_host,
        )
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        return ServiceStatus(
            name,
            service_type,
            "warning",
            round((time.perf_counter() - started) * 1_000),
            url,
            f"{type(exc).__name__}: health check failed",
            service_host=service_host,
        )


def _database_status(connection, host: str) -> ServiceStatus:
    service_host = _normalized_service_host(host)
    started = time.perf_counter()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return ServiceStatus(
            "PostgreSQL",
            "database",
            "healthy",
            round((time.perf_counter() - started) * 1_000),
            service_host=service_host,
        )
    except Exception as exc:
        connection.rollback()
        return ServiceStatus(
            "PostgreSQL",
            "database",
            "critical",
            round((time.perf_counter() - started) * 1_000),
            error=f"{type(exc).__name__}: database health check failed",
            service_host=service_host,
        )


def _redis_status() -> ServiceStatus:
    configured = str(os.getenv("REDIS_URL") or "").strip()
    if not configured:
        return ServiceStatus("Redis", "redis", "disabled", error="Redis is not configured for this environment")
    parsed = urllib.parse.urlsplit(configured)
    if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
        return ServiceStatus("Redis", "redis", "warning", error="REDIS_URL is invalid")
    started = time.perf_counter()
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 6379), timeout=3):
            pass
        return ServiceStatus(
            "Redis",
            "redis",
            "healthy",
            round((time.perf_counter() - started) * 1_000),
            service_host=_normalized_service_host(parsed.hostname),
        )
    except OSError as exc:
        return ServiceStatus(
            "Redis",
            "redis",
            "warning",
            round((time.perf_counter() - started) * 1_000),
            error=f"{type(exc).__name__}: Redis connection failed",
            service_host=_normalized_service_host(parsed.hostname),
        )


def _git_state() -> tuple[str | None, str | None]:
    try:
        commit_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        status_result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    commit = commit_result.stdout.strip()
    if commit_result.returncode != 0 or len(commit) != 40:
        commit = None
    version = "working-tree" if status_result.returncode == 0 and status_result.stdout.strip() else None
    return commit, version or (commit[:12] if commit else None)


def collect_statuses(connection) -> list[ServiceStatus]:
    backend_url = _configured_http_url("OPS_STATUS_BACKEND_URL", "http://127.0.0.1:8001/health")
    frontend_url = _configured_http_url("OPS_STATUS_FRONTEND_URL", "http://127.0.0.1:5174/")
    console_url = _configured_http_url("OPS_STATUS_CONSOLE_URL", "http://127.0.0.1:5175/")
    ollama_url = _configured_http_url("OPS_STATUS_OLLAMA_URL", "http://127.0.0.1:11434/api/tags")
    return [
        _http_status("MoonCen API", "backend", backend_url),
        _http_status("MoonCen Frontend", "frontend", frontend_url),
        _http_status("Ops Console", "proxy", console_url),
        _database_status(connection, database_config()["host"]),
        _redis_status(),
        _http_status("AI/Ollama", "ai_worker", ollama_url),
        ServiceStatus(
            "Local Ops Agent",
            "agent",
            "healthy",
            dependencies=("database",),
            service_host=_normalized_service_host(socket.gethostname()),
        ),
    ]


def publish_snapshot(connection) -> tuple[UUID, list[ServiceStatus]]:
    environment = normalized_environment()
    hostname = socket.gethostname()
    # This process reports checks performed from its own host.  It is not a
    # crawler/quality worker and must not make its reporter hostname eligible
    # for those queues.  Enrolled workers advertise their own capabilities.
    capabilities = ["service_status"]
    # The isolated deployment worker owns its private heartbeat and registers
    # its own database-backed agent lease.  A status/API process must never
    # infer deployment capability by reading worker-local state or credentials.
    statuses = collect_statuses(connection)
    commit, version = _git_state()
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO ops_agents (
                name, hostname, environment, os_type, ip_address, version,
                status, capabilities, credential_hint, last_seen_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, 'healthy', %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (environment, hostname) DO UPDATE
            SET name = EXCLUDED.name,
                os_type = EXCLUDED.os_type,
                ip_address = EXCLUDED.ip_address,
                version = EXCLUDED.version,
                status = EXCLUDED.status,
                capabilities = EXCLUDED.capabilities,
                credential_hint = EXCLUDED.credential_hint,
                last_seen_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            RETURNING id
            """,
            (
                f"{hostname} local agent",
                hostname,
                environment,
                platform.system().lower(),
                "127.0.0.1",
                "1",
                Json(capabilities),
                "local-db-role",
            ),
        )
        agent_id = UUID(str(cursor.fetchone()[0]))
        for item in statuses:
            cursor.execute(
                """
                INSERT INTO ops_services (
                    agent_id, service_name, service_type, environment,
                    health_url, service_host, current_version, current_commit, status, response_time_ms,
                    last_error, dependencies, last_checked_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (environment, service_name) DO UPDATE
                SET agent_id = EXCLUDED.agent_id,
                    service_type = EXCLUDED.service_type,
                    health_url = EXCLUDED.health_url,
                    service_host = EXCLUDED.service_host,
                    current_version = EXCLUDED.current_version,
                    current_commit = EXCLUDED.current_commit,
                    status = EXCLUDED.status,
                    response_time_ms = EXCLUDED.response_time_ms,
                    last_error = EXCLUDED.last_error,
                    dependencies = EXCLUDED.dependencies,
                    last_checked_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    str(agent_id),
                    item.name,
                    item.service_type,
                    environment,
                    item.health_url,
                    item.service_host,
                    version,
                    commit,
                    item.status,
                    item.response_time_ms,
                    redact_text(item.error, maximum=1_000) or None,
                    Json(list(item.dependencies)),
                ),
            )
    connection.commit()
    return agent_id, statuses


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish real local service health to the MoonCen Ops Console")
    parser.add_argument("--once", action="store_true", help="Publish one snapshot and exit")
    parser.add_argument(
        "--interval",
        type=int,
        default=bounded_env_int("OPS_STATUS_INTERVAL_SECONDS", 30, 5, 300),
        help="Refresh interval between 5 and 300 seconds",
    )
    args = parser.parse_args(argv)
    if not 5 <= args.interval <= 300:
        parser.error("--interval must be between 5 and 300 seconds")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    while True:
        try:
            connection = connect_database()
            try:
                agent_id, statuses = publish_snapshot(connection)
            finally:
                connection.close()
            summary = {
                "agent_id": str(agent_id),
                "services": {item.name: item.status for item in statuses},
            }
            print(json.dumps(summary, ensure_ascii=False))
        except Exception:
            logger.exception("Ops status snapshot failed")
            if args.once:
                return 1
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
