from __future__ import annotations

import logging
import os
import re
from typing import Any, Callable

from DB.db_utils import get_db_cursor


logger = logging.getLogger(__name__)

_SENSITIVE_VALUE_RE = re.compile(
    r"(?i)(\b(?:authorization|access[_-]?token|refresh[_-]?token|token|secret|password|passwd|api[_-]?key|client[_-]?secret)\b\s*[=:]\s*)([^\s&,;]+)"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_MAX_COUNTER = 2_147_483_647


def _safe_log_text(value: Any, maximum: int) -> str:
    text = " ".join(str(value or "").split())
    text = _BEARER_RE.sub("Bearer <redacted>", text)
    text = _SENSITIVE_VALUE_RE.sub(r"\1<redacted>", text)
    return text[:maximum]


def _bounded_count(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return min(max(parsed, 0), _MAX_COUNTER)


CREATE_CRAWLER_RUN_LOG_SQL = """
CREATE TABLE IF NOT EXISTS crawler_run_log (
    id BIGSERIAL PRIMARY KEY,
    target_key TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL DEFAULT '',
    crawler_name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'running',
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMPTZ,
    duration_seconds NUMERIC,
    collected_count INTEGER NOT NULL DEFAULT 0,
    inserted_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    error_type TEXT,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_crawler_run_log_status
        CHECK (status IN ('running', 'success', 'failed', 'stopped', 'skipped'))
)
"""


ALTER_CRAWLER_RUN_LOG_SQL = """
ALTER TABLE crawler_run_log ADD COLUMN IF NOT EXISTS target_key TEXT NOT NULL DEFAULT '';
ALTER TABLE crawler_run_log ADD COLUMN IF NOT EXISTS source_type TEXT NOT NULL DEFAULT '';
ALTER TABLE crawler_run_log ADD COLUMN IF NOT EXISTS crawler_name TEXT NOT NULL DEFAULT '';
ALTER TABLE crawler_run_log ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'running';
ALTER TABLE crawler_run_log ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE crawler_run_log ADD COLUMN IF NOT EXISTS ended_at TIMESTAMPTZ;
ALTER TABLE crawler_run_log ADD COLUMN IF NOT EXISTS duration_seconds NUMERIC;
ALTER TABLE crawler_run_log ADD COLUMN IF NOT EXISTS collected_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE crawler_run_log ADD COLUMN IF NOT EXISTS inserted_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE crawler_run_log ADD COLUMN IF NOT EXISTS updated_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE crawler_run_log ADD COLUMN IF NOT EXISTS skipped_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE crawler_run_log ADD COLUMN IF NOT EXISTS error_type TEXT;
ALTER TABLE crawler_run_log ADD COLUMN IF NOT EXISTS error_message TEXT;
ALTER TABLE crawler_run_log ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP;
"""


CREATE_CRAWLER_RUN_LOG_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_crawler_run_log_started
    ON crawler_run_log(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_crawler_run_log_status_started
    ON crawler_run_log(status, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_crawler_run_log_source_started
    ON crawler_run_log(source_type, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_crawler_run_log_target_key
    ON crawler_run_log(target_key);
"""


def _close_cursor(cursor: Any) -> None:
    close = getattr(cursor, "close", None)
    if callable(close):
        close()


def _commit(conn: Any) -> None:
    commit = getattr(conn, "commit", None)
    if callable(commit):
        commit()


def _rollback(conn: Any) -> None:
    rollback = getattr(conn, "rollback", None)
    if callable(rollback):
        rollback()


def _with_cursor(conn: Any | None, callback: Callable[[Any], Any]) -> Any:
    if conn is None:
        with get_db_cursor() as cursor:
            return callback(cursor)

    cursor = conn.cursor()
    try:
        result = callback(cursor)
        _commit(conn)
        return result
    except Exception:
        _rollback(conn)
        raise
    finally:
        _close_cursor(cursor)


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[index]
    except (IndexError, KeyError, TypeError):
        return None


def ensure_crawler_run_log_table(conn: Any | None = None) -> bool:
    try:
        def execute(cursor: Any) -> None:
            # DDL is reserved for setup_db.py/the migration owner.  The
            # explicit escape hatch preserves legacy maintenance workflows,
            # while normal crawler services only verify their prerequisite.
            if os.getenv("DB_USE_MIGRATOR", "").strip().lower() in {"1", "true", "yes"}:
                cursor.execute(CREATE_CRAWLER_RUN_LOG_SQL)
                cursor.execute(ALTER_CRAWLER_RUN_LOG_SQL)
                cursor.execute(CREATE_CRAWLER_RUN_LOG_INDEX_SQL)
                return

            cursor.execute("SELECT to_regclass('public.crawler_run_log') AS relation")
            if _row_value(cursor.fetchone(), "relation") is None:
                raise RuntimeError(
                    "crawler_run_log is missing; run DB/setup_db.py --mode migrate "
                    "with the migration owner"
                )

        _with_cursor(conn, execute)
        return True
    except Exception as exc:
        logger.error("Failed to ensure crawler_run_log table. error_type=%s", type(exc).__name__)
        return False


def start_crawler_run(
    conn: Any | None = None,
    target_key: str = "",
    source_type: str = "",
    crawler_name: str = "",
) -> int | None:
    target_key = _safe_log_text(target_key, 512)
    source_type = _safe_log_text(source_type, 100)
    crawler_name = _safe_log_text(crawler_name, 2_048)
    if not ensure_crawler_run_log_table(conn):
        return None

    try:
        def execute(cursor: Any) -> int | None:
            cursor.execute(
                """
                INSERT INTO crawler_run_log (
                    target_key, source_type, crawler_name, status, started_at
                )
                VALUES (%s, %s, %s, 'running', CURRENT_TIMESTAMP)
                RETURNING id
                """,
                (target_key, source_type, crawler_name),
            )
            return _row_value(cursor.fetchone(), "id")

        run_id = _with_cursor(conn, execute)
        return int(run_id) if run_id is not None else None
    except Exception as exc:
        logger.error(
            "Failed to start crawler run target=%s source=%s crawler=%s error_type=%s",
            target_key,
            source_type,
            crawler_name,
            type(exc).__name__,
        )
        return None


def finish_crawler_run(
    conn: Any | None = None,
    run_id: int | None = None,
    status: str = "success",
    collected_count: int = 0,
    inserted_count: int = 0,
    updated_count: int = 0,
    skipped_count: int = 0,
    error_type: str | None = None,
    error_message: str | None = None,
) -> bool:
    if not run_id:
        return False
    status = (status or "success").strip().lower()
    if status not in {"success", "failed", "stopped", "skipped"}:
        status = "failed"
    collected_count = _bounded_count(collected_count)
    inserted_count = _bounded_count(inserted_count)
    updated_count = _bounded_count(updated_count)
    skipped_count = _bounded_count(skipped_count)
    error_type = _safe_log_text(error_type, 100) or None
    error_message = _safe_log_text(error_message, 2_000) or None

    try:
        def execute(cursor: Any) -> bool:
            cursor.execute(
                """
                UPDATE crawler_run_log
                   SET status = %s,
                       ended_at = CURRENT_TIMESTAMP,
                       duration_seconds = EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - started_at)),
                       collected_count = %s,
                       inserted_count = %s,
                       updated_count = %s,
                       skipped_count = %s,
                       error_type = %s,
                       error_message = %s
                 WHERE id = %s
                """,
                (
                    status,
                    collected_count,
                    inserted_count,
                    updated_count,
                    skipped_count,
                    error_type,
                    error_message,
                    run_id,
                ),
            )
            return getattr(cursor, "rowcount", -1) != 0

        return bool(_with_cursor(conn, execute))
    except Exception as exc:
        logger.error("Failed to finish crawler run id=%s status=%s error_type=%s", run_id, status, type(exc).__name__)
        return False


def log_crawler_success(
    conn: Any | None = None,
    target_key: str = "",
    source_type: str = "",
    crawler_name: str = "",
    collected_count: int = 0,
    inserted_count: int = 0,
    updated_count: int = 0,
    skipped_count: int = 0,
) -> int | None:
    run_id = start_crawler_run(conn, target_key, source_type, crawler_name)
    if run_id:
        finish_crawler_run(
            conn,
            run_id,
            "success",
            collected_count=collected_count,
            inserted_count=inserted_count,
            updated_count=updated_count,
            skipped_count=skipped_count,
        )
    return run_id


def log_crawler_failure(
    conn: Any | None = None,
    target_key: str = "",
    source_type: str = "",
    crawler_name: str = "",
    error_type: str | None = None,
    error_message: str | None = None,
    collected_count: int = 0,
    inserted_count: int = 0,
    updated_count: int = 0,
    skipped_count: int = 0,
) -> int | None:
    run_id = start_crawler_run(conn, target_key, source_type, crawler_name)
    if run_id:
        finish_crawler_run(
            conn,
            run_id,
            "failed",
            collected_count=collected_count,
            inserted_count=inserted_count,
            updated_count=updated_count,
            skipped_count=skipped_count,
            error_type=error_type,
            error_message=error_message,
        )
    return run_id
