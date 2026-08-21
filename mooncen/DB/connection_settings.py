from __future__ import annotations

import ipaddress
import os
from typing import Any


_SSL_MODES = {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}
# Staging carries real crawler credentials and production-shaped data.  Remote
# staging connections therefore require the same hostname-verified TLS policy
# as production rather than silently accepting libpq's opportunistic default.
_PRODUCTION_ENVIRONMENTS = {"prod", "production", "stage", "staging"}
DEPLOYMENT_HEARTBEAT_LEASE_ENV = "OPS_DEPLOY_STALE_HEARTBEAT_SECONDS"
DEPLOYMENT_HEARTBEAT_LEASE_DEFAULT_SECONDS = 300
DEPLOYMENT_HEARTBEAT_LEASE_MIN_SECONDS = 60
DEPLOYMENT_HEARTBEAT_LEASE_MAX_SECONDS = 3_600


def bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def deployment_heartbeat_lease_seconds() -> int:
    """Return the shared API/worker ownership deadline for deployment jobs."""

    return bounded_env_int(
        DEPLOYMENT_HEARTBEAT_LEASE_ENV,
        DEPLOYMENT_HEARTBEAT_LEASE_DEFAULT_SECONDS,
        DEPLOYMENT_HEARTBEAT_LEASE_MIN_SECONDS,
        DEPLOYMENT_HEARTBEAT_LEASE_MAX_SECONDS,
    )


def is_local_database_host(host: str) -> bool:
    normalized = (host or "").strip().rstrip(".").lower()
    if not normalized or normalized.startswith("/"):
        return True
    if normalized in {"localhost", "localhost.localdomain"}:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def database_sslmode(host: str, environment: str | None = None) -> str:
    configured = os.getenv("DB_SSLMODE", "").strip().lower()
    current_environment = (environment or os.getenv("ENVIRONMENT", "development")).strip().lower()
    if configured:
        if configured not in _SSL_MODES:
            raise RuntimeError("DB_SSLMODE must be a valid libpq SSL mode")
        if (
            current_environment in _PRODUCTION_ENVIRONMENTS
            and not is_local_database_host(host)
            and configured != "verify-full"
        ):
            raise RuntimeError("Remote production/staging databases require DB_SSLMODE=verify-full")
        return configured

    if current_environment in _PRODUCTION_ENVIRONMENTS and not is_local_database_host(host):
        return "verify-full"
    return "prefer"


def database_connect_options(host: str, application_name: str) -> dict[str, Any]:
    statement_timeout_ms = bounded_env_int("DB_STATEMENT_TIMEOUT_MS", 15_000, 100, 600_000)
    lock_timeout_ms = bounded_env_int("DB_LOCK_TIMEOUT_MS", 3_000, 100, 120_000)
    options: dict[str, Any] = {
        "connect_timeout": bounded_env_int("DB_CONNECT_TIMEOUT", 5, 1, 60),
        "application_name": application_name,
        "sslmode": database_sslmode(host),
        "options": (
            "-c search_path=pg_catalog,public "
            f"-c statement_timeout={statement_timeout_ms} "
            f"-c lock_timeout={lock_timeout_ms}"
        ),
    }
    for environment_name, libpq_name in (
        ("DB_SSLROOTCERT", "sslrootcert"),
        ("DB_SSLCERT", "sslcert"),
        ("DB_SSLKEY", "sslkey"),
    ):
        value = os.getenv(environment_name, "").strip()
        if value:
            options[libpq_name] = value
    return options
