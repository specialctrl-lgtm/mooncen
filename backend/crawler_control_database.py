"""Optional, isolated connection to the crawler-control staging database.

The normal API session targets the application database.  Release requests
must never be written there, so this module has no fallback to ``get_db`` or to
the normal API credential.  Missing configuration intentionally yields no
session and lets read endpoints report ``available=false``.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Generator

from sqlalchemy import URL, create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from DB.connection_settings import bounded_env_int, database_connect_options


def _configured_endpoint() -> tuple[str, int, str, str, str] | None:
    raw_environment = os.getenv("ENVIRONMENT", "development").strip().lower()
    environment = {
        "prod": "production",
        "production": "production",
        "stage": "staging",
        "staging": "staging",
        "dev": "development",
        "development": "development",
        "test": "development",
    }.get(raw_environment)
    if environment is None:
        raise RuntimeError("crawler-control API environment is invalid")

    host = os.getenv("OPS_CRAWLER_SHARED_DB_HOST", "").strip()
    database = os.getenv("OPS_CRAWLER_SHARED_DB_NAME", "").strip()
    user = os.getenv("OPS_CRAWLER_API_DB_USER", "").strip()
    password = os.getenv("OPS_CRAWLER_API_DB_PASSWORD", "")
    if not any((host, database, user, password)):
        return None
    if not all((host, database, user, password)):
        raise RuntimeError("crawler-control API database configuration is incomplete")
    port = bounded_env_int("OPS_CRAWLER_SHARED_DB_PORT", 5432, 1, 65_535)

    if environment in {"production", "staging"}:
        primary_identity = (
            os.getenv("DB_HOST", "localhost").strip().lower(),
            bounded_env_int("DB_PORT", 5432, 1, 65_535),
            os.getenv("DB_NAME", "mooncen").strip().lower(),
        )
        control_identity = (host.lower(), port, database.lower())
        if control_identity == primary_identity:
            raise RuntimeError("crawler-control API database must be separate from the primary database")
        if user.lower() == os.getenv("DB_API_USER", "").strip().lower():
            raise RuntimeError("crawler-control API must use a distinct login credential")
    return host, port, database, user, password


@lru_cache(maxsize=1)
def crawler_control_engine() -> Engine | None:
    configured = _configured_endpoint()
    if configured is None:
        return None
    host, port, database, user, password = configured
    url = URL.create(
        "postgresql+psycopg2",
        username=user,
        password=password,
        host=host,
        port=port,
        database=database,
    )
    pool_size = bounded_env_int("OPS_CRAWLER_API_DB_POOL_SIZE", 2, 1, 10)
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=0,
        pool_timeout=bounded_env_int("OPS_CRAWLER_API_DB_POOL_TIMEOUT", 5, 1, 30),
        pool_recycle=bounded_env_int("OPS_CRAWLER_API_DB_POOL_RECYCLE", 1_800, 60, 86_400),
        connect_args=database_connect_options(host, "mooncen-crawler-control-api"),
    )


def crawler_control_required() -> bool:
    return os.getenv("OPS_CRAWLER_API_DB_REQUIRED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def assert_required_crawler_control_ready() -> None:
    """Fail readiness when an explicitly required isolated pool is unhealthy.

    The query proves that the tunnel selects the marked staging database and
    that the dedicated login's server-side read-only default is effective.  It
    returns no credential or operational data to the health endpoint.
    """

    if not crawler_control_required():
        return
    engine = crawler_control_engine()
    if engine is None:
        raise RuntimeError("required crawler-control API database is not configured")
    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT current_database() AS database_name,
                       current_setting('transaction_read_only') AS transaction_read_only,
                       EXISTS (
                           SELECT 1
                           FROM ops_crawler_control_database_marker
                           WHERE singleton IS TRUE
                             AND database_name = current_database()
                       ) AS marker_matches
                """
            )
        ).mappings().one()
    configured = _configured_endpoint()
    if configured is None:
        raise RuntimeError("required crawler-control API database is not configured")
    if (
        str(row["database_name"]) != configured[2]
        or str(row["transaction_read_only"]).strip().lower() not in {"on", "true"}
        or row["marker_matches"] is not True
    ):
        raise RuntimeError("required crawler-control API database readiness contract failed")


def get_crawler_control_db() -> Generator[Session | None, None, None]:
    try:
        engine = crawler_control_engine()
    except RuntimeError:
        yield None
        return
    if engine is None:
        yield None
        return
    factory = sessionmaker(autoflush=False, expire_on_commit=False, bind=engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()
