"""Crawler Task Queue Manager.

Manages the lifecycle of crawl tasks in the central queue:
- Enqueuing daily scheduled providers from configuration
- Reaping stale/zombie tasks whose worker heartbeat expired
- Reporting queue status and progress across worker nodes
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    psycopg2 = None
    RealDictCursor = None

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from DB.db_utils import get_db_config
except (ImportError, ModuleNotFoundError):
    def get_db_config():
        return {
            "host": os.getenv("CRAWL_STAGING_DB_HOST", os.getenv("DB_HOST", "localhost")),
            "port": int(os.getenv("CRAWL_STAGING_DB_PORT", os.getenv("DB_PORT", "5432"))),
            "database": os.getenv("CRAWL_STAGING_DB_NAME", os.getenv("DB_NAME", "mooncen_staging")),
            "user": os.getenv("CRAWL_STAGING_DB_USER", os.getenv("DB_USER", "mooncen_crawler_login")),
            "password": os.getenv("CRAWL_STAGING_DB_PASSWORD", os.getenv("DB_PASSWORD", "")),
        }

logger = logging.getLogger("crawler_queue_manager")

DEFAULT_OWNERSHIP_PATH = PROJECT_ROOT / "config" / "production_crawler_provider_ownership.json"
DEFAULT_STALE_SECONDS = 600  # 10 minutes

# High-priority providers that contain large volumes or critical retail data
PROVIDER_PRIORITIES: dict[str, int] = {
    "EMART": 30,
    "HOMEPLUS": 30,
    "LOTTE": 30,
    "LOTTE_MART": 30,
    "HYUNDAI_DEPT": 25,
    "SHINSEGAE_ACADEMY": 25,
    "MUNICIPAL_RESERVATION_TARGETS": 20,
    "EXPERIENCE_TARGETS": 20,
}
DEFAULT_PRIORITY = 10


def get_connection():
    """Create a connection to the queue database using DB.db_utils config."""
    config = get_db_config()
    conn = psycopg2.connect(**config)
    conn.autocommit = False
    return conn


def load_scheduled_providers(manifest_path: Path = DEFAULT_OWNERSHIP_PATH) -> list[str]:
    """Load scheduled provider keys from production crawler ownership config."""
    if not manifest_path.exists():
        raise FileNotFoundError(f"Ownership manifest not found at {manifest_path}")
    with open(manifest_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    scheduled = data.get("scheduled_providers", {})
    return sorted(scheduled.keys())


def get_local_code_version() -> str:
    """Return local crawler code version (git commit, release env, or env var)."""
    env_ver = os.getenv("CRAWLER_CODE_VERSION") or os.getenv("OPS_CRAWLER_CODE_VERSION")
    if env_ver and env_ver.strip():
        return env_ver.strip()

    release_env = PROJECT_ROOT / "release.env"
    if release_env.exists():
        try:
            for line in release_env.read_text(encoding="utf-8").splitlines():
                if line.startswith("CODE_VERSION="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return val
        except Exception:
            pass

    try:
        import subprocess
        res = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        return res.stdout.strip()
    except Exception:
        return "unknown"


def enqueue_tasks(
    conn: Any,
    batch_date: date | None = None,
    providers: list[str] | None = None,
    max_attempts: int = 3,
    code_version: str | None = None,
) -> int:
    """Enqueue scheduled providers for the given date. Idempotent via ON CONFLICT DO NOTHING."""
    target_date = batch_date or date.today()
    target_providers = providers or load_scheduled_providers()
    target_version = code_version or get_local_code_version()
    if not target_providers:
        logger.warning("No providers to enqueue.")
        return 0

    inserted_count = 0
    with conn.cursor() as cursor:
        for provider in target_providers:
            priority = PROVIDER_PRIORITIES.get(provider, DEFAULT_PRIORITY)
            cursor.execute(
                """
                INSERT INTO crawler_task_queue (
                    batch_date, provider_code, priority, status, max_attempts, required_code_version
                )
                VALUES (%s, %s, %s, 'pending', %s, %s)
                ON CONFLICT (batch_date, provider_code) DO NOTHING
                RETURNING id;
                """,
                (target_date, provider, priority, max_attempts, target_version),
            )
            if cursor.fetchone():
                inserted_count += 1
    conn.commit()
    logger.info(
        "Enqueued %d new tasks for batch date %s (code_version=%s, total providers: %d)",
        inserted_count,
        target_date,
        target_version,
        len(target_providers),
    )
    return inserted_count


def reap_stale_tasks(conn: Any, stale_seconds: int = DEFAULT_STALE_SECONDS) -> dict[str, int]:
    """Reap tasks stuck in 'running' state whose heartbeat has expired."""
    stats = {"retried": 0, "failed": 0}
    with conn.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute(
            """
            SELECT id, provider_code, worker_node, attempt_count, max_attempts
            FROM crawler_task_queue
            WHERE status = 'running'
              AND last_heartbeat_at < CURRENT_TIMESTAMP - make_interval(secs => %s)
            FOR UPDATE SKIP LOCKED;
            """,
            (stale_seconds,),
        )
        stale_rows = cursor.fetchall()
        for row in stale_rows:
            task_id = row["id"]
            if row["attempt_count"] < row["max_attempts"]:
                cursor.execute(
                    """
                    UPDATE crawler_task_queue
                    SET status = 'pending',
                        worker_node = NULL,
                        error_message = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s;
                    """,
                    (f"Re-queued after heartbeat timeout from worker {row['worker_node']}", task_id),
                )
                stats["retried"] += 1
            else:
                cursor.execute(
                    """
                    UPDATE crawler_task_queue
                    SET status = 'failed',
                        error_message = %s,
                        finished_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s;
                    """,
                    (f"Exceeded max attempts ({row['max_attempts']}) after heartbeat timeout", task_id),
                )
                stats["failed"] += 1
    conn.commit()
    if stale_rows:
        logger.warning("Reaped stale tasks: %s", stats)
    return stats


def get_queue_summary(conn: Any, batch_date: date | None = None) -> dict[str, Any]:
    """Return summary statistics of queue for a batch date."""
    target_date = batch_date or date.today()
    summary: dict[str, Any] = {
        "batch_date": str(target_date),
        "total": 0,
        "by_status": {},
        "by_worker": {},
    }
    with conn.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute(
            """
            SELECT status, count(*) as count
            FROM crawler_task_queue
            WHERE batch_date = %s
            GROUP BY status;
            """,
            (target_date,),
        )
        total = 0
        for row in cursor.fetchall():
            summary["by_status"][row["status"]] = row["count"]
            total += row["count"]
        summary["total"] = total

        cursor.execute(
            """
            SELECT COALESCE(worker_node, 'unassigned') as worker, status, count(*) as count
            FROM crawler_task_queue
            WHERE batch_date = %s
            GROUP BY worker_node, status
            ORDER BY count DESC;
            """,
            (target_date,),
        )
        for row in cursor.fetchall():
            w = row["worker"]
            if w not in summary["by_worker"]:
                summary["by_worker"][w] = {}
            summary["by_worker"][w][row["status"]] = row["count"]

    return summary


def main():
    parser = argparse.ArgumentParser(description="MoonCen Distributed Crawler Task Queue Manager")
    subparsers = parser.add_subparsers(dest="command", required=True)

    enqueue_p = subparsers.add_parser("enqueue", help="Enqueue scheduled providers for a batch date")
    enqueue_p.add_argument("--batch-date", type=lambda d: datetime.strptime(d, "%Y-%m-%d").date(), default=None)
    enqueue_p.add_argument("--providers", nargs="+", default=None, help="Specific providers to enqueue")
    enqueue_p.add_argument("--max-attempts", type=int, default=3)
    enqueue_p.add_argument("--code-version", default=None, help="Target required code version (defaults to local git/release version)")

    reap_p = subparsers.add_parser("reap", help="Reap stale tasks with expired heartbeats")
    reap_p.add_argument("--stale-seconds", type=int, default=DEFAULT_STALE_SECONDS)

    status_p = subparsers.add_parser("status", help="Get queue status summary")
    status_p.add_argument("--batch-date", type=lambda d: datetime.strptime(d, "%Y-%m-%d").date(), default=None)
    status_p.add_argument("--json", action="store_true", help="Output JSON format")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    conn = get_connection()
    try:
        if args.command == "enqueue":
            count = enqueue_tasks(
                conn,
                batch_date=args.batch_date,
                providers=args.providers,
                max_attempts=args.max_attempts,
                code_version=args.code_version,
            )
            print(f"Enqueued {count} tasks for {args.batch_date or date.today()}")
        elif args.command == "reap":
            stats = reap_stale_tasks(conn, stale_seconds=args.stale_seconds)
            print(f"Reaped stale tasks: {stats}")
        elif args.command == "status":
            summary = get_queue_summary(conn, batch_date=args.batch_date)
            if args.json:
                print(json.dumps(summary, indent=2, ensure_ascii=False))
            else:
                print(f"=== Crawler Task Queue Summary ({summary['batch_date']}) ===")
                print(f"Total Tasks: {summary['total']}")
                print("By Status:")
                for st, count in summary["by_status"].items():
                    print(f"  - {st}: {count}")
                print("By Worker:")
                for w, statuses in summary["by_worker"].items():
                    status_str = ", ".join(f"{s}={c}" for s, c in statuses.items())
                    print(f"  - {w}: {status_str}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
