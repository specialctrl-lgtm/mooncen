from __future__ import annotations

import os
from typing import Any

from DB.connection_settings import bounded_env_int, database_connect_options


def shared_database_endpoint() -> tuple[str, int, str]:
    environment = os.getenv("ENVIRONMENT", "development").strip().lower()
    production_like = environment in {"prod", "production", "stage", "staging"}
    host = os.getenv("OPS_CRAWLER_SHARED_DB_HOST", "").strip()
    name = os.getenv("OPS_CRAWLER_SHARED_DB_NAME", "").strip()
    if production_like:
        raw_port = os.getenv("OPS_CRAWLER_SHARED_DB_PORT", "").strip()
        if not host or not raw_port or not name:
            raise RuntimeError(
                "Production/staging crawler control requires explicit "
                "OPS_CRAWLER_SHARED_DB_HOST/PORT/NAME"
            )
        port = bounded_env_int("OPS_CRAWLER_SHARED_DB_PORT", 5432, 1, 65_535)
    else:
        host = host or os.getenv("DB_HOST", "localhost").strip()
        name = name or os.getenv("DB_NAME", "mooncen").strip()
        port = bounded_env_int(
            "OPS_CRAWLER_SHARED_DB_PORT",
            bounded_env_int("DB_PORT", 5432, 1, 65_535),
            1,
            65_535,
        )
    if not host or not name:
        raise RuntimeError("shared crawler database endpoint is incomplete")
    return host, port, name


def control_database_config() -> dict[str, Any]:
    """Return the privileged control-plane connection, never a worker credential."""
    host, port, database = shared_database_endpoint()
    environment = os.getenv("ENVIRONMENT", "development").strip().lower()
    production_like = environment in {"prod", "production", "stage", "staging"}
    if production_like:
        user = os.getenv("OPS_CRAWLER_CONTROL_DB_USER", "").strip()
        password = os.getenv("OPS_CRAWLER_CONTROL_DB_PASSWORD", "")
    else:
        user = os.getenv("OPS_CRAWLER_CONTROL_DB_USER", os.getenv("DB_API_USER", "")).strip()
        password = os.getenv("OPS_CRAWLER_CONTROL_DB_PASSWORD", os.getenv("DB_API_PASSWORD", ""))
    if not host or not database or not user or not password:
        raise RuntimeError(
            "Explicit OPS_CRAWLER_CONTROL_DB_* credentials are required in production/staging; "
            "worker queue credentials cannot run the scheduler or finalizer"
        )
    return {
        "host": host,
        "port": port,
        "database": database,
        "user": user,
        "password": password,
        **database_connect_options(host, "mooncen-crawler-control"),
    }


def finalizer_database_config() -> dict[str, Any]:
    """Return the staging applier connection used only to seal crawler batches."""
    host, port, database = shared_database_endpoint()
    environment = os.getenv("ENVIRONMENT", "development").strip().lower()
    production_like = environment in {"prod", "production", "stage", "staging"}
    if production_like:
        user = os.getenv("OPS_CRAWLER_FINALIZER_DB_USER", "").strip()
        password = os.getenv("OPS_CRAWLER_FINALIZER_DB_PASSWORD", "")
    else:
        user = os.getenv("OPS_CRAWLER_FINALIZER_DB_USER", os.getenv("DB_APPLIER_USER", "")).strip()
        password = os.getenv("OPS_CRAWLER_FINALIZER_DB_PASSWORD", os.getenv("DB_APPLIER_PASSWORD", ""))
    if not host or not database or not user or not password:
        raise RuntimeError(
            "Explicit OPS_CRAWLER_FINALIZER_DB_* credentials are required in production/staging; "
            "the scheduler and worker roles cannot finalize staging snapshots"
        )
    return {
        "host": host,
        "port": port,
        "database": database,
        "user": user,
        "password": password,
        **database_connect_options(host, "mooncen-crawler-finalizer"),
    }


def publisher_database_config() -> dict[str, Any]:
    """Return the read-only desired-state publisher connection."""
    return _separated_database_config(
        prefix="OPS_CRAWLER_PUBLISHER_DB",
        application_name="mooncen-crawler-publisher",
    )


def approver_database_config() -> dict[str, Any]:
    """Return the human-gated held-batch approval connection."""
    return _separated_database_config(
        prefix="OPS_CRAWLER_APPROVER_DB",
        application_name="mooncen-crawler-approver",
    )


def release_admin_database_config() -> dict[str, Any]:
    """Return the operator-only artifact and rollout administration connection."""
    return _separated_database_config(
        prefix="OPS_CRAWLER_RELEASE_ADMIN_DB",
        application_name="mooncen-crawler-release-admin",
    )


def crawler_api_database_config() -> dict[str, Any]:
    """Return the bounded Ops API read/request-queue connection."""
    return _separated_database_config(
        prefix="OPS_CRAWLER_API_DB",
        application_name="mooncen-crawler-api",
    )


def reporter_database_config() -> dict[str, Any]:
    """Return the append-only per-worker release reporter connection."""
    return _separated_database_config(
        prefix="OPS_CRAWLER_REPORTER_DB",
        application_name="mooncen-crawler-reporter",
    )


def _separated_database_config(*, prefix: str, application_name: str) -> dict[str, Any]:
    host, port, database = shared_database_endpoint()
    user = os.getenv(f"{prefix}_USER", "").strip()
    password = os.getenv(f"{prefix}_PASSWORD", "")
    if not user or not password:
        raise RuntimeError(f"Explicit {prefix}_* credentials are required")
    return {
        "host": host,
        "port": port,
        "database": database,
        "user": user,
        "password": password,
        **database_connect_options(host, application_name),
    }
