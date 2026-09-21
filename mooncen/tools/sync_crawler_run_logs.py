#!/usr/bin/env python3
"""Synchronize crawler_run_log from staging database to primary production database."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import sys
from typing import Any

from psycopg2.extras import RealDictCursor, execute_batch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.apply_staging_batch import connect, db_config

logger = logging.getLogger("sync_crawler_run_logs")

SYNC_COLUMNS = [
    "target_key",
    "source_type",
    "crawler_name",
    "status",
    "started_at",
    "ended_at",
    "duration_seconds",
    "collected_count",
    "inserted_count",
    "updated_count",
    "skipped_count",
    "error_type",
    "error_message",
    "created_at",
]

UPSERT_SQL = f"""
INSERT INTO crawler_run_log (
    {", ".join(SYNC_COLUMNS)}
) VALUES (
    {", ".join(f"%({col})s" for col in SYNC_COLUMNS)}
)
ON CONFLICT (target_key, started_at) DO UPDATE SET
    source_type = EXCLUDED.source_type,
    crawler_name = EXCLUDED.crawler_name,
    status = EXCLUDED.status,
    ended_at = EXCLUDED.ended_at,
    duration_seconds = EXCLUDED.duration_seconds,
    collected_count = EXCLUDED.collected_count,
    inserted_count = EXCLUDED.inserted_count,
    updated_count = EXCLUDED.updated_count,
    skipped_count = EXCLUDED.skipped_count,
    error_type = EXCLUDED.error_type,
    error_message = EXCLUDED.error_message
"""


def ensure_unique_index(conn: Any) -> None:
    """Ensure the unique index required for ON CONFLICT exists on primary."""
    if not hasattr(conn, "cursor"):
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_crawler_run_log_target_started
            ON crawler_run_log(target_key, started_at)
            """
        )


def latest_primary_log_timestamp(primary_conn: Any) -> datetime | None:
    if not hasattr(primary_conn, "cursor"):
        return None
    with primary_conn.cursor() as cur:
        cur.execute("SELECT MAX(started_at) FROM crawler_run_log")
        row = cur.fetchone()
        return row[0] if row and row[0] else None


def fetch_staging_logs(
    staging_conn: Any,
    since: datetime | None = None,
) -> list[dict[str, Any]]:
    if not hasattr(staging_conn, "cursor"):
        return []
    with staging_conn.cursor(cursor_factory=RealDictCursor) as cur:
        if since is not None:
            cur.execute(
                f"""
                SELECT {", ".join(SYNC_COLUMNS)}
                FROM crawler_run_log
                WHERE started_at >= %s
                ORDER BY started_at ASC
                """,
                (since,),
            )
        else:
            cur.execute(
                f"""
                SELECT {", ".join(SYNC_COLUMNS)}
                FROM crawler_run_log
                ORDER BY started_at ASC
                """
            )
        return [dict(row) for row in cur.fetchall()]


def sync_crawler_run_logs(
    staging_conn: Any,
    primary_conn: Any,
    *,
    since: datetime | None = None,
    full_sync: bool = False,
    batch_size: int = 250,
) -> dict[str, Any]:
    """Sync crawler_run_log from staging to primary production database."""
    if not hasattr(staging_conn, "cursor") or not hasattr(primary_conn, "cursor"):
        return {
            "status": "SKIPPED_NO_CURSOR",
            "fetched": 0,
            "upserted": 0,
        }

    if not full_sync and since is None:
        latest = latest_primary_log_timestamp(primary_conn)
        if latest is not None:
            # If primary has not been updated recently (e.g. frozen in August), backfill from August 1
            threshold = datetime(2026, 9, 1, tzinfo=timezone.utc)
            latest_tz = latest if getattr(latest, "tzinfo", None) else latest.replace(tzinfo=timezone.utc)
            if latest_tz < threshold:
                since = datetime(2026, 8, 1, tzinfo=timezone.utc)
            else:
                since = latest - timedelta(days=2)

    print(f"[sync_crawler_run_logs] Querying staging logs with since={since}", file=sys.stderr)
    rows = fetch_staging_logs(staging_conn, since=since)
    print(f"[sync_crawler_run_logs] Fetched {len(rows)} rows from staging", file=sys.stderr)
    if not rows:
        return {
            "status": "NO_NEW_LOGS",
            "fetched": 0,
            "upserted": 0,
            "since": since.isoformat() if since else None,
        }

    ensure_unique_index(primary_conn)
    if hasattr(primary_conn, "commit"):
        primary_conn.commit()

    with primary_conn.cursor() as cur:
        execute_batch(cur, UPSERT_SQL, rows, page_size=batch_size)
    if hasattr(primary_conn, "commit"):
        primary_conn.commit()
    print(f"[sync_crawler_run_logs] Successfully upserted and committed {len(rows)} rows to primary", file=sys.stderr)

    return {
        "status": "SUCCESS",
        "fetched": len(rows),
        "upserted": len(rows),
        "since": since.isoformat() if since else None,
        "earliest_synced": rows[0]["started_at"].isoformat() if rows[0].get("started_at") else None,
        "latest_synced": rows[-1]["started_at"].isoformat() if rows[-1].get("started_at") else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Synchronize crawler_run_log from staging to primary DB")
    parser.add_argument("--full", action="store_true", help="Sync all logs without timestamp filter")
    parser.add_argument("--since", type=str, default=None, help="Sync logs started at or after ISO timestamp")
    parser.add_argument("--batch-size", type=int, default=250, help="Batch size for upsert")
    args = parser.parse_args()

    since_dt = None
    if args.since:
        since_dt = datetime.fromisoformat(args.since)

    staging_config = db_config(
        "CRAWL_STAGING",
        os.getenv("CRAWL_STAGING_DB_NAME", "mooncen_staging"),
    )
    primary_config = db_config("PRIMARY", os.getenv("PRIMARY_DB_NAME", "mooncen"))

    staging_conn = connect(staging_config)
    primary_conn = connect(primary_config)
    try:
        result = sync_crawler_run_logs(
            staging_conn,
            primary_conn,
            since=since_dt,
            full_sync=args.full,
            batch_size=args.batch_size,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        primary_conn.rollback()
        print(json.dumps({"status": "FAILED", "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    finally:
        staging_conn.close()
        primary_conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
