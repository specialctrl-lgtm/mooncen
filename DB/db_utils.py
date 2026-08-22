import atexit
import logging
import os
import re
import threading
from contextlib import contextmanager

import psycopg2
from dotenv import load_dotenv
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

try:
    from DB.connection_settings import bounded_env_int, database_connect_options
except ModuleNotFoundError:  # Support `python DB/db_utils.py` from the repository root.
    from connection_settings import bounded_env_int, database_connect_options


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

logger = logging.getLogger(__name__)
_pool_lock = threading.Lock()
_connection_pool = None

_UUID_PATTERN = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}"
)


def _crawl_lease_session_settings() -> dict[str, str]:
    """Return validated crawler lease markers for a database session.

    Pooled connections are reused across jobs, so every marker is returned even
    when lease enforcement is disabled.  Writing empty values prevents a later
    maintenance task from inheriting an earlier crawler's authority.
    """

    required = os.getenv("CRAWL_REQUIRE_LEASE", "").strip().lower() in {"1", "true", "yes", "on"}
    values = {
        "mooncen.crawl_job_id": os.getenv("CRAWL_JOB_ID", "").strip(),
        "mooncen.crawl_lease_token": os.getenv("CRAWL_LEASE_TOKEN", "").strip(),
        "mooncen.crawl_lease_epoch": os.getenv("CRAWL_LEASE_EPOCH", "").strip(),
        "mooncen.crawl_attempt_no": os.getenv("CRAWL_ATTEMPT_NO", "").strip(),
        "mooncen.require_crawler_lease": "on" if required else "off",
    }
    if not required:
        return {key: (value if key == "mooncen.require_crawler_lease" else "") for key, value in values.items()}

    if os.getenv("CRAWL_WRITE_MODE", "").strip().lower() != "staging":
        raise RuntimeError("Fenced crawler jobs may write only to the staging database")
    if not _UUID_PATTERN.fullmatch(values["mooncen.crawl_job_id"]):
        raise RuntimeError("Fenced crawler job id is missing or invalid")
    if not _UUID_PATTERN.fullmatch(values["mooncen.crawl_lease_token"]):
        raise RuntimeError("Fenced crawler lease token is missing or invalid")
    for key in ("mooncen.crawl_lease_epoch", "mooncen.crawl_attempt_no"):
        raw = values[key]
        if not raw.isdecimal() or not 1 <= int(raw) <= 2_147_483_647:
            raise RuntimeError(f"Fenced crawler session marker is missing or invalid: {key}")
    return values


def get_db_config():
    """Load database configuration from environment variables."""
    if os.getenv("CRAWL_WRITE_MODE", "").lower() == "staging":
        host = os.getenv("CRAWL_STAGING_DB_HOST", os.getenv("DB_HOST", "localhost"))
        staging_user = os.getenv(
            "CRAWL_STAGING_DB_USER",
            os.getenv("DB_RUNTIME_USER", os.getenv("DB_CRAWLER_USER", "")),
        ).strip()
        staging_password = os.getenv(
            "CRAWL_STAGING_DB_PASSWORD",
            os.getenv("DB_RUNTIME_PASSWORD", os.getenv("DB_CRAWLER_PASSWORD", "")),
        )
        if os.getenv("ENVIRONMENT", "development").strip().lower() in {
            "prod",
            "production",
            "stage",
            "staging",
        }:
            if not staging_user or not staging_password:
                raise RuntimeError("Production/staging workers require explicit staging DB credentials")
            owner_user = (
                os.getenv("DB_OWNER_USER", "").strip()
                or os.getenv("DB_MIGRATOR_USER", "").strip()
                or os.getenv("DB_USER", "").strip()
            )
            if owner_user and staging_user == owner_user:
                raise RuntimeError("Production staging DB user must differ from the database owner/migration role")
        return {
            "host": host,
            "port": os.getenv("CRAWL_STAGING_DB_PORT", os.getenv("DB_PORT", "5432")),
            "database": os.getenv("CRAWL_STAGING_DB_NAME", os.getenv("DB_NAME", "mooncen_staging")),
            "user": staging_user or os.getenv("DB_USER", "mooncen_crawler_login"),
            "password": staging_password or os.getenv("DB_PASSWORD", ""),
            **database_connect_options(host, "mooncen-crawler-staging"),
        }
    use_migrator = os.getenv("DB_USE_MIGRATOR", "").strip().lower() in {"1", "true", "yes"}
    runtime_user = os.getenv("DB_RUNTIME_USER", "").strip() or os.getenv("DB_CRAWLER_USER", "").strip()
    runtime_password = os.getenv("DB_RUNTIME_PASSWORD", "") or os.getenv("DB_CRAWLER_PASSWORD", "")
    environment = os.getenv("ENVIRONMENT", "development").strip().lower()
    if environment in {"prod", "production", "stage", "staging"} and not use_migrator:
        if not runtime_user or not runtime_password:
            raise RuntimeError(
                "Production/staging DB workers require explicit DB_RUNTIME_USER and DB_RUNTIME_PASSWORD"
            )
        owner_user = (
            os.getenv("DB_OWNER_USER", "").strip()
            or os.getenv("DB_MIGRATOR_USER", "").strip()
            or os.getenv("DB_USER", "").strip()
        )
        if owner_user and runtime_user == owner_user:
            raise RuntimeError("Production DB_RUNTIME_USER must differ from the database owner/migration role")
    user = (
        os.getenv("DB_USER", "mooncen_admin")
        if use_migrator
        else runtime_user or os.getenv("DB_USER", "mooncen_crawler_login")
    )
    password = (
        os.getenv("DB_PASSWORD", "")
        if use_migrator
        else runtime_password or os.getenv("DB_PASSWORD", "")
    )
    host = os.getenv("DB_HOST", "localhost")
    return {
        "host": host,
        "port": os.getenv("DB_PORT", "5432"),
        "database": os.getenv("DB_NAME", "mooncen"),
        "user": user,
        "password": password,
        **database_connect_options(host, os.getenv("DB_APPLICATION_NAME", "mooncen-db-worker")),
    }


def get_db_connection():
    conn = None
    try:
        conn = psycopg2.connect(**get_db_config())
        _configure_session(conn)
        logger.debug("Database connection established")
        return conn
    except Exception as exc:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        logger.error("Database connection failed. error_type=%s", type(exc).__name__)
        raise


def _get_connection_pool():
    global _connection_pool
    if _connection_pool is not None:
        return _connection_pool

    with _pool_lock:
        if _connection_pool is None:
            minconn = bounded_env_int("DB_POOL_MIN", 1, 1, 100)
            maxconn = bounded_env_int("DB_POOL_MAX", 8, minconn, 200)
            _connection_pool = pool.ThreadedConnectionPool(minconn, maxconn, **get_db_config())
            logger.info("Database connection pool initialized min=%s max=%s", minconn, maxconn)
    return _connection_pool


def _get_pooled_connection():
    connection_pool = _get_connection_pool()
    conn = connection_pool.getconn()
    if conn.closed:
        connection_pool.putconn(conn, close=True)
        conn = connection_pool.getconn()
    try:
        _configure_session(conn)
    except Exception:
        # A failed session reset must never leak a checked-out or partially
        # configured connection from the pool.
        connection_pool.putconn(conn, close=True)
        raise
    logger.debug("Database pooled connection checked out")
    return conn


def _configure_session(conn):
    batch_id = os.getenv("CRAWL_BATCH_ID", "").strip()
    lease_settings = _crawl_lease_session_settings()
    try:
        with conn.cursor() as cursor:
            # Pooled sessions outlive one crawler batch. Explicitly write an
            # empty value when the environment marker is absent so maintenance
            # and primary-mode work cannot inherit a previous staging batch.
            for setting, value in lease_settings.items():
                cursor.execute("SELECT set_config(%s, %s, false)", (setting, value))
            cursor.execute("SELECT set_config('mooncen.crawl_batch_id', %s, false)", (batch_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Failed to configure crawler DB session markers")
        raise


def close_db_pool():
    global _connection_pool
    with _pool_lock:
        if _connection_pool is not None:
            _connection_pool.closeall()
            _connection_pool = None
            logger.info("Database connection pool closed")


atexit.register(close_db_pool)


@contextmanager
def get_db_cursor(dict_cursor=True):
    conn = _get_pooled_connection()
    cursor_factory = RealDictCursor if dict_cursor else None
    cursor = None

    try:
        cursor = conn.cursor(cursor_factory=cursor_factory)
        yield cursor
        conn.commit()
    except Exception as exc:
        conn.rollback()
        logger.error("Database operation failed: %s", exc)
        raise
    finally:
        if cursor is not None:
            cursor.close()
        _get_connection_pool().putconn(conn, close=bool(conn.closed))


def test_connection():
    try:
        with get_db_cursor() as cursor:
            cursor.execute("SELECT version();")
            version = cursor.fetchone()
            logger.info("PostgreSQL version: %s", version)
            return True
    except Exception as exc:
        logger.error("Connection test failed: %s", exc)
        return False


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    if test_connection():
        print("Database connection successful")
    else:
        print("Database connection failed")
