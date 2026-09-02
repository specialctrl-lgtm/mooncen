from __future__ import annotations

import logging
import os
import re
from typing import Any

from DB.db_utils import get_db_cursor


logger = logging.getLogger(__name__)

_SENSITIVE_VALUE_RE = re.compile(
    r"(?i)(\b(?:authorization|access[_-]?token|refresh[_-]?token|token|secret|password|passwd|api[_-]?key|client[_-]?secret)\b\s*[=:]\s*)([^\s&,;]+)"
)


STATE_MAP = {
    "pending": "pending",
    "running": "in_progress",
    "success": "completed",
    "failed": "failed",
    "stopped": "failed",
    "skipped": "completed",
}


def _bounded_text(value: Any, maximum: int) -> str:
    text = " ".join(str(value or "").split())
    return _SENSITIVE_VALUE_RE.sub(r"\1<redacted>", text)[:maximum]


def _bounded_elapsed(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return min(max(parsed, 0.0), 31_536_000.0)


def normalize_progress_status(state: str) -> str:
    normalized = str(state or "").strip().lower()
    return STATE_MAP.get(normalized, "failed")


def ensure_crawl_progress_table() -> None:
    with get_db_cursor() as cursor:
        if os.getenv("DB_USE_MIGRATOR", "").strip().lower() in {"1", "true", "yes"}:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS crawl_progress (
                    run_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    started_at TIMESTAMPTZ,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    completed_at TIMESTAMPTZ,
                    elapsed_seconds NUMERIC,
                    exit_code INTEGER,
                    latest_report TEXT,
                    error TEXT,
                    PRIMARY KEY (run_id, provider)
                )
                """
            )
            return

        cursor.execute("SELECT to_regclass('public.crawl_progress') AS relation")
        row = cursor.fetchone()
        relation = row.get("relation") if isinstance(row, dict) else (row[0] if row else None)
        if relation is None:
            raise RuntimeError(
                "crawl_progress is missing; run DB/setup_db.py --mode migrate "
                "with the migration owner"
            )


def init_crawl_progress(run_id: str, providers: list[str], latest_report: str = "") -> bool:
    run_id = _bounded_text(run_id, 200)
    providers = list(dict.fromkeys(_bounded_text(provider, 100) for provider in providers if str(provider).strip()))
    latest_report = _bounded_text(latest_report, 1_024)
    if not run_id or not providers:
        return False
    ensure_crawl_progress_table()
    with get_db_cursor() as cursor:
        for provider in providers:
            cursor.execute(
                """
                INSERT INTO crawl_progress (
                    run_id, provider, status, started_at, updated_at, completed_at,
                    elapsed_seconds, exit_code, latest_report, error
                )
                VALUES (%s, %s, 'pending', NULL, CURRENT_TIMESTAMP, NULL, NULL, NULL, %s, NULL)
                ON CONFLICT (run_id, provider)
                DO UPDATE SET
                    status = 'pending',
                    started_at = NULL,
                    updated_at = CURRENT_TIMESTAMP,
                    completed_at = NULL,
                    elapsed_seconds = NULL,
                    exit_code = NULL,
                    latest_report = EXCLUDED.latest_report,
                    error = NULL
                """,
                (run_id, provider, latest_report),
            )
    return True


def update_crawl_progress(run_id: str, provider: str, state: str, **fields: Any) -> bool:
    run_id = _bounded_text(run_id, 200)
    provider = _bounded_text(provider, 100)
    if not run_id or not provider:
        return False
    status = normalize_progress_status(state)
    try:
        ensure_crawl_progress_table()
        with get_db_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO crawl_progress (
                    run_id, provider, status, started_at, updated_at, completed_at,
                    elapsed_seconds, exit_code, latest_report, error
                )
                VALUES (
                    %(run_id)s,
                    %(provider)s,
                    %(status)s,
                    CASE WHEN %(status)s = 'in_progress' THEN CURRENT_TIMESTAMP ELSE NULL END,
                    CURRENT_TIMESTAMP,
                    CASE WHEN %(status)s IN ('completed', 'failed') THEN CURRENT_TIMESTAMP ELSE NULL END,
                    %(elapsed_seconds)s,
                    %(exit_code)s,
                    %(latest_report)s,
                    %(error)s
                )
                ON CONFLICT (run_id, provider)
                DO UPDATE SET
                    status = EXCLUDED.status,
                    started_at = CASE
                        WHEN EXCLUDED.status = 'in_progress'
                        THEN COALESCE(crawl_progress.started_at, CURRENT_TIMESTAMP)
                        ELSE crawl_progress.started_at
                    END,
                    updated_at = CURRENT_TIMESTAMP,
                    completed_at = CASE
                        WHEN EXCLUDED.status IN ('completed', 'failed') THEN CURRENT_TIMESTAMP
                        ELSE NULL
                    END,
                    elapsed_seconds = COALESCE(EXCLUDED.elapsed_seconds, crawl_progress.elapsed_seconds),
                    exit_code = COALESCE(EXCLUDED.exit_code, crawl_progress.exit_code),
                    latest_report = COALESCE(NULLIF(EXCLUDED.latest_report, ''), crawl_progress.latest_report),
                    error = COALESCE(NULLIF(EXCLUDED.error, ''), crawl_progress.error)
                """,
                {
                    "run_id": run_id,
                    "provider": provider,
                    "status": status,
                    "elapsed_seconds": _bounded_elapsed(fields.get("elapsed_seconds")),
                    "exit_code": fields.get("exit_code") if isinstance(fields.get("exit_code"), int) else None,
                    "latest_report": _bounded_text(fields.get("latest_report"), 1_024),
                    "error": _bounded_text(fields.get("error"), 2_000),
                },
            )
        return True
    except Exception as exc:
        logger.error(
            "Failed to update crawl_progress run_id=%s provider=%s state=%s error_type=%s",
            run_id,
            provider,
            state,
            type(exc).__name__,
        )
        return False
