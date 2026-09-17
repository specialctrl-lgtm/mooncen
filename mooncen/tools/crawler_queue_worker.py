"""Crawler Task Queue Worker Daemon.

Pulls tasks from the centralized staging crawler_task_queue using atomic
'FOR UPDATE SKIP LOCKED' queries, runs the target provider crawler, and
updates execution state and heartbeats.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import date, datetime
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

logger = logging.getLogger("crawler_queue_worker")

RUNNING = True
ACTIVE_PROCESS: subprocess.Popen[str] | None = None


def get_connection():
    """Create a non-autocommit DB connection."""
    config = get_db_config()
    conn = psycopg2.connect(**config)
    conn.autocommit = False
    return conn


def handle_shutdown(signum: int, frame: Any) -> None:
    """Handle graceful termination on SIGINT or SIGTERM."""
    global RUNNING, ACTIVE_PROCESS
    logger.info("Shutdown signal (%d) received. Finishing active task or terminating...", signum)
    RUNNING = False
    if ACTIVE_PROCESS and ACTIVE_PROCESS.poll() is None:
        try:
            ACTIVE_PROCESS.terminate()
        except OSError:
            pass


class HeartbeatUpdater(threading.Thread):
    """Background thread to update last_heartbeat_at while a task runs."""

    def __init__(self, task_id: int, worker_node: str, interval: float = 30.0) -> None:
        super().__init__(daemon=True)
        self.task_id = task_id
        self.worker_node = worker_node
        self.interval = interval
        self.stopped = threading.Event()

    def stop(self) -> None:
        self.stopped.set()

    def run(self) -> None:
        while not self.stopped.wait(self.interval):
            try:
                conn = get_connection()
                try:
                    with conn.cursor() as cursor:
                        cursor.execute(
                            """
                            UPDATE crawler_task_queue
                            SET last_heartbeat_at = CURRENT_TIMESTAMP
                            WHERE id = %s AND status = 'running' AND worker_node = %s;
                            """,
                            (self.task_id, self.worker_node),
                        )
                    conn.commit()
                finally:
                    conn.close()
            except Exception as exc:
                logger.warning("Heartbeat failed for task %d: %s", self.task_id, exc)


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


def claim_task(
    conn: Any,
    worker_node: str,
    batch_date: date | None = None,
    worker_code_version: str | None = None,
    enforce_version: bool = True,
) -> dict[str, Any] | None:
    """Claim the next available pending task atomically via SKIP LOCKED, verifying code version."""
    target_date = batch_date or date.today()
    version = worker_code_version or get_local_code_version()

    version_clause = ""
    params: list[Any] = [worker_node, version, target_date]
    if enforce_version and version and version != "unknown":
        version_clause = "AND (required_code_version IS NULL OR required_code_version = %s)"
        params.append(version)

    with conn.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute(
            f"""
            UPDATE crawler_task_queue
            SET status = 'running',
                worker_node = %s,
                worker_code_version = %s,
                attempt_count = attempt_count + 1,
                started_at = CURRENT_TIMESTAMP,
                last_heartbeat_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = (
                SELECT id FROM crawler_task_queue
                WHERE batch_date = %s AND status = 'pending'
                  {version_clause}
                ORDER BY priority DESC, id ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING id, provider_code, attempt_count, max_attempts, required_code_version;
            """,
            params,
        )
        task = cursor.fetchone()
    conn.commit()
    return task


def complete_task(
    conn: Any,
    task_id: int,
    status: str,
    exit_code: int | None = None,
    error_message: str | None = None,
) -> None:
    """Record completion or failure of a task."""
    with conn.cursor() as cursor:
        cursor.execute(
            """
            UPDATE crawler_task_queue
            SET status = %s,
                exit_code = %s,
                error_message = %s,
                finished_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s;
            """,
            (status, exit_code, error_message, task_id),
        )
    conn.commit()


def run_crawler_subprocess(provider_code: str, dry_run: bool = False) -> tuple[int, str]:
    """Run run_crawlers.py for the specified provider."""
    global ACTIVE_PROCESS
    if dry_run:
        logger.info("[DRY-RUN] Simulating crawl for provider %s...", provider_code)
        time.sleep(1.0)
        return 0, ""

    cmd = [
        sys.executable,
        "-X",
        "utf8",
        str(PROJECT_ROOT / "run_crawlers.py"),
        "--providers",
        provider_code,
        "--once",
        "--ignore-active-window",
    ]
    logger.info("Executing crawler: %s", " ".join(cmd))
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    process = subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    ACTIVE_PROCESS = process
    stdout, _ = process.communicate()
    ACTIVE_PROCESS = None
    exit_code = process.returncode
    return exit_code, (stdout[-2000:] if stdout else "")


def worker_loop(
    worker_node: str,
    batch_date: date | None = None,
    poll_interval: float = 5.0,
    heartbeat_interval: float = 30.0,
    max_tasks: int | None = None,
    dry_run: bool = False,
    exit_when_empty: bool = False,
    code_version: str | None = None,
    enforce_version: bool = True,
) -> None:
    """Main worker loop."""
    global RUNNING
    conn = get_connection()
    tasks_processed = 0
    version = code_version or get_local_code_version()
    logger.info(
        "Worker '%s' started (code_version=%s, enforce_version=%s). Polling queue (batch_date=%s)...",
        worker_node,
        version,
        enforce_version,
        batch_date or date.today(),
    )

    try:
        while RUNNING:
            if max_tasks and tasks_processed >= max_tasks:
                logger.info("Reached maximum requested task limit (%d). Exiting.", max_tasks)
                break

            task = claim_task(
                conn,
                worker_node,
                batch_date=batch_date,
                worker_code_version=version,
                enforce_version=enforce_version,
            )
            if not task:
                if exit_when_empty:
                    logger.info("Queue is empty and exit_when_empty=True. Exiting.")
                    break
                time.sleep(poll_interval)
                continue

            task_id = task["id"]
            provider = task["provider_code"]
            req_ver = task.get("required_code_version")
            logger.info(
                "Claimed task %d: %s (attempt %d/%d, required_version=%s)",
                task_id,
                provider,
                task["attempt_count"],
                task["max_attempts"],
                req_ver,
            )

            heartbeat = HeartbeatUpdater(task_id, worker_node, interval=heartbeat_interval)
            heartbeat.start()
            start_time = time.time()

            try:
                exit_code, output_tail = run_crawler_subprocess(provider, dry_run=dry_run)
                duration = time.time() - start_time
                if exit_code == 0:
                    logger.info("Task %d (%s) COMPLETED in %.1fs", task_id, provider, duration)
                    complete_task(conn, task_id, status="completed", exit_code=0)
                else:
                    logger.error("Task %d (%s) FAILED with code %d in %.1fs. Output tail:\n%s", task_id, provider, exit_code, duration, output_tail)
                    complete_task(conn, task_id, status="failed", exit_code=exit_code, error_message=output_tail)
            except Exception as exc:
                logger.exception("Task %d (%s) crashed with exception: %s", task_id, provider, exc)
                complete_task(conn, task_id, status="failed", exit_code=99, error_message=str(exc))
            finally:
                heartbeat.stop()
                tasks_processed += 1

    finally:
        conn.close()
        logger.info("Worker '%s' terminated after processing %d tasks.", worker_node, tasks_processed)


def main():
    parser = argparse.ArgumentParser(description="MoonCen Distributed Crawler Queue Worker")
    parser.add_argument(
        "--worker-node",
        default=os.getenv("WORKER_NODE", socket.gethostname().strip().lower()),
        help="Identifier for this worker node (e.g. gen1crawler, mac)",
    )
    parser.add_argument("--batch-date", type=lambda d: datetime.strptime(d, "%Y-%m-%d").date(), default=None)
    parser.add_argument("--code-version", default=None, help="Override detected worker code version")
    parser.add_argument(
        "--no-enforce-version",
        dest="enforce_version",
        action="store_false",
        default=True,
        help="Disable strict version checking against task required_code_version",
    )
    parser.add_argument("--poll-interval", type=float, default=5.0, help="Polling interval when queue is empty")
    parser.add_argument("--heartbeat-interval", type=float, default=30.0, help="Heartbeat update interval")
    parser.add_argument("--max-tasks", type=int, default=None, help="Stop after processing N tasks")
    parser.add_argument("--dry-run", action="store_true", help="Simulate crawl execution without running actual scripts")
    parser.add_argument("--exit-when-empty", action="store_true", help="Exit when no pending tasks remain")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s")

    signal.signal(signal.SIGINT, handle_shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_shutdown)

    worker_loop(
        worker_node=args.worker_node,
        batch_date=args.batch_date,
        poll_interval=args.poll_interval,
        heartbeat_interval=args.heartbeat_interval,
        max_tasks=args.max_tasks,
        dry_run=args.dry_run,
        exit_when_empty=args.exit_when_empty,
        code_version=args.code_version,
        enforce_version=args.enforce_version,
    )


if __name__ == "__main__":
    main()
