from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from DB.crawler_run_log import finish_crawler_run, log_crawler_failure, start_crawler_run


class FakeCursor:
    def __init__(self) -> None:
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        if self.executed and "to_regclass('public.crawler_run_log')" in self.executed[-1][0]:
            return {"relation": "crawler_run_log"}
        return {"id": 77}

    def close(self):
        pass


class FakeConnection:
    def __init__(self) -> None:
        self.cursor_obj = FakeCursor()
        self.commit_count = 0
        self.rollback_count = 0

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.commit_count += 1

    def rollback(self):
        self.rollback_count += 1


class CrawlerRunLogTests(unittest.TestCase):
    def test_start_and_finish_crawler_run(self) -> None:
        conn = FakeConnection()

        with patch.dict(os.environ, {"DB_USE_MIGRATOR": ""}):
            run_id = start_crawler_run(conn, "HOMEPLUS|branch_code=001", "homeplus", "Crawler_Homeplus.py")
            ok = finish_crawler_run(
                conn,
                run_id,
                "success",
                collected_count=3,
                inserted_count=1,
                updated_count=2,
            )

        self.assertEqual(run_id, 77)
        self.assertTrue(ok)
        sql_text = "\n".join(sql for sql, _params in conn.cursor_obj.executed)
        self.assertIn("to_regclass('public.crawler_run_log')", sql_text)
        self.assertNotIn("CREATE TABLE IF NOT EXISTS crawler_run_log", sql_text)
        self.assertIn("INSERT INTO crawler_run_log", sql_text)
        self.assertIn("UPDATE crawler_run_log", sql_text)
        self.assertGreaterEqual(conn.commit_count, 3)
        self.assertEqual(conn.rollback_count, 0)

    def test_failure_log_writes_error_context(self) -> None:
        conn = FakeConnection()

        run_id = log_crawler_failure(
            conn,
            "MUNI_TEST",
            "education_experience",
            "Crawler_GeneratedYamlTargets.py",
            "TimeoutExpired",
            "blocked at target URL",
        )

        self.assertEqual(run_id, 77)
        update_params = conn.cursor_obj.executed[-1][1]
        self.assertEqual(update_params[0], "failed")
        self.assertEqual(update_params[5], "TimeoutExpired")
        self.assertEqual(update_params[6], "blocked at target URL")

    def test_finish_without_run_id_is_noop(self) -> None:
        conn = FakeConnection()

        self.assertFalse(finish_crawler_run(conn, None, "success"))
        self.assertEqual(conn.cursor_obj.executed, [])


if __name__ == "__main__":
    unittest.main()
