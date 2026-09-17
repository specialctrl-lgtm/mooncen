import sys
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

# Ensure test runs even in environments without psycopg2/dotenv installed
if "psycopg2" not in sys.modules:
    mock_pg = MagicMock()
    sys.modules["psycopg2"] = mock_pg
    sys.modules["psycopg2.extras"] = MagicMock()
    sys.modules["psycopg2.pool"] = MagicMock()
if "dotenv" not in sys.modules:
    mock_dotenv = MagicMock()
    mock_dotenv.load_dotenv = MagicMock()
    mock_dotenv.dotenv_values = MagicMock(return_value={})
    sys.modules["dotenv"] = mock_dotenv

from tools import crawler_queue_manager as qm
from tools import crawler_queue_worker as qw


class MockCursor:
    def __init__(self, connection):
        self.connection = connection
        self.row = None
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def execute(self, sql: str, params=None):
        self.connection.queries.append((sql, params))
        if "SELECT to_regclass" in sql:
            self.row = ("crawler_task_queue",)
        elif "INSERT INTO crawler_task_queue" in sql:
            self.row = (1,)
        elif "UPDATE crawler_task_queue" in sql and "SKIP LOCKED" in sql:
            if self.connection.pending_tasks:
                task = self.connection.pending_tasks.pop(0)
                self.row = task
            else:
                self.row = None
        elif "SELECT id, provider_code, worker_node" in sql and "last_heartbeat_at" in sql:
            self.rows = self.connection.stale_tasks
        elif "SELECT status, count(*)" in sql:
            self.rows = [
                {"status": "completed", "count": 10},
                {"status": "running", "count": 2},
                {"status": "pending", "count": 5},
            ]
        elif "SELECT COALESCE(worker_node" in sql:
            self.rows = [
                {"worker": "mac", "status": "completed", "count": 7},
                {"worker": "gen1crawler", "status": "completed", "count": 3},
                {"worker": "mac", "status": "running", "count": 2},
            ]

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class MockConnection:
    def __init__(self):
        self.queries: list[tuple[str, object]] = []
        self.commits = 0
        self.pending_tasks: list[dict] = []
        self.stale_tasks: list[dict] = []

    def cursor(self, **_kwargs):
        return MockCursor(self)

    def commit(self):
        self.commits += 1

    def close(self):
        pass


class CrawlerQueueTestCase(unittest.TestCase):
    def test_enqueue_tasks(self):
        conn = MockConnection()
        providers = ["EMART", "LOTTE", "HOMEPLUS"]
        count = qm.enqueue_tasks(conn, batch_date=date(2026, 9, 17), providers=providers, code_version="v2026.09.17")

        self.assertEqual(count, 3)
        self.assertEqual(conn.commits, 1)
        self.assertEqual(len(conn.queries), 3)
        # Check that priority for EMART is elevated to 30 and version is bound
        self.assertEqual(conn.queries[0][1][2], 30)
        self.assertEqual(conn.queries[0][1][4], "v2026.09.17")

    def test_reap_stale_tasks(self):
        conn = MockConnection()
        conn.stale_tasks = [
            {"id": 101, "provider_code": "EMART", "worker_node": "mac", "attempt_count": 1, "max_attempts": 3},
            {"id": 102, "provider_code": "LOTTE", "worker_node": "gen1crawler", "attempt_count": 3, "max_attempts": 3},
        ]

        stats = qm.reap_stale_tasks(conn, stale_seconds=300)

        # id 101 should be retried (re-queued to pending), id 102 should fail (reached max attempts 3)
        self.assertEqual(stats["retried"], 1)
        self.assertEqual(stats["failed"], 1)
        self.assertEqual(conn.commits, 1)

    def test_queue_summary(self):
        conn = MockConnection()
        summary = qm.get_queue_summary(conn, batch_date=date(2026, 9, 17))

        self.assertEqual(summary["total"], 17)
        self.assertEqual(summary["by_status"]["completed"], 10)
        self.assertEqual(summary["by_status"]["pending"], 5)
        self.assertEqual(summary["by_worker"]["mac"]["completed"], 7)
        self.assertEqual(summary["by_worker"]["gen1crawler"]["completed"], 3)

    def test_worker_claim_task(self):
        conn = MockConnection()
        conn.pending_tasks = [
            {"id": 1, "provider_code": "EMART", "attempt_count": 1, "max_attempts": 3, "required_code_version": "v1.0"}
        ]

        task = qw.claim_task(conn, worker_node="mac", batch_date=date(2026, 9, 17), worker_code_version="v1.0")

        self.assertIsNotNone(task)
        self.assertEqual(task["provider_code"], "EMART")
        self.assertEqual(conn.commits, 1)
        # Ensure SKIP LOCKED was in query
        self.assertIn("FOR UPDATE SKIP LOCKED", conn.queries[0][0])
        # Ensure version check was in query
        self.assertIn("required_code_version = %s", conn.queries[0][0])

    def test_worker_claim_task_version_clause(self):
        conn = MockConnection()
        # When enforce_version is True, query requires version match
        qw.claim_task(conn, worker_node="mac", batch_date=date(2026, 9, 17), worker_code_version="v2.0", enforce_version=True)
        sql, params = conn.queries[0]
        self.assertIn("required_code_version = %s", sql)
        self.assertEqual(params[1], "v2.0")  # worker_code_version in SET
        self.assertEqual(params[3], "v2.0")  # required_code_version in WHERE

        # When enforce_version is False, version filter is omitted from WHERE
        conn.queries.clear()
        qw.claim_task(conn, worker_node="mac", batch_date=date(2026, 9, 17), worker_code_version="v2.0", enforce_version=False)
        sql2, params2 = conn.queries[0]
        self.assertNotIn("required_code_version = %s", sql2)

    def test_worker_claim_task_empty(self):
        conn = MockConnection()
        conn.pending_tasks = []

        task = qw.claim_task(conn, worker_node="mac", batch_date=date(2026, 9, 17))

        self.assertIsNone(task)
        self.assertEqual(conn.commits, 1)

    def test_worker_complete_task(self):
        conn = MockConnection()
        qw.complete_task(conn, task_id=1, status="completed", exit_code=0)

        self.assertEqual(conn.commits, 1)
        sql, params = conn.queries[0]
        self.assertIn("UPDATE crawler_task_queue", sql)
        self.assertEqual(params[0], "completed")
        self.assertEqual(params[1], 0)

    @patch("tools.crawler_queue_worker.get_connection")
    @patch("tools.crawler_queue_worker.run_crawler_subprocess")
    def test_worker_loop_dry_run(self, mock_run, mock_get_conn):
        conn = MockConnection()
        conn.pending_tasks = [
            {"id": 1, "provider_code": "EMART", "attempt_count": 1, "max_attempts": 3},
            {"id": 2, "provider_code": "LOTTE", "attempt_count": 1, "max_attempts": 3},
        ]
        mock_get_conn.return_value = conn
        mock_run.return_value = (0, "")

        qw.worker_loop(
            worker_node="mac",
            batch_date=date(2026, 9, 17),
            max_tasks=2,
            dry_run=True,
        )

        self.assertEqual(mock_run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
