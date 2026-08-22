from __future__ import annotations

from unittest.mock import Mock

import pytest

from DB import db_utils
from DB.crawl_progress import _bounded_text, normalize_progress_status
from DB.crawler_run_log import finish_crawler_run
from DB.course_upsert_guards import coalesce_provider_course_id_by_raw_url


class SessionCursor:
    def __init__(self) -> None:
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))


class SessionConnection:
    closed = False

    def __init__(self, cursor=None) -> None:
        self.cursor_obj = cursor or SessionCursor()
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    def cursor(self, **_kwargs):
        return self.cursor_obj

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closes += 1


def test_session_configuration_clears_old_batch_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CRAWL_BATCH_ID", raising=False)
    connection = SessionConnection()
    db_utils._configure_session(connection)
    assert connection.cursor_obj.executed[-1][1] == ("",)
    assert connection.commits == 1


def test_failed_pooled_session_reset_returns_and_closes_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = SessionConnection()
    pool = Mock()
    pool.getconn.return_value = connection
    monkeypatch.setattr(db_utils, "_get_connection_pool", lambda: pool)
    monkeypatch.setattr(db_utils, "_configure_session", lambda _connection: (_ for _ in ()).throw(RuntimeError("bad")))
    with pytest.raises(RuntimeError, match="bad"):
        db_utils._get_pooled_connection()
    pool.putconn.assert_called_once_with(connection, close=True)


def test_direct_connection_is_closed_when_session_setup_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = SessionConnection()
    monkeypatch.setattr(db_utils.psycopg2, "connect", lambda **_kwargs: connection)
    monkeypatch.setattr(db_utils, "get_db_config", lambda: {})
    monkeypatch.setattr(db_utils, "_configure_session", lambda _connection: (_ for _ in ()).throw(RuntimeError("bad")))
    with pytest.raises(RuntimeError, match="bad"):
        db_utils.get_db_connection()
    assert connection.closes == 1


def test_run_log_unknown_id_and_counter_bounds_fail_closed() -> None:
    class Cursor(SessionCursor):
        rowcount = 0

    connection = SessionConnection(Cursor())
    assert (
        finish_crawler_run(
            connection,
            999,
            "success",
            collected_count=-3,
            inserted_count=10**30,
            error_message="access_token=super-secret",
        )
        is False
    )
    params = connection.cursor_obj.executed[-1][1]
    assert params[1] == 0
    assert params[2] == 2_147_483_647
    assert "super-secret" not in params[6]
    assert "<redacted>" in params[6]


def test_progress_unknown_state_and_secret_text_fail_closed() -> None:
    assert normalize_progress_status("invented") == "failed"
    assert _bounded_text("access_token=super-secret", 100) == "access_token=<redacted>"


def test_raw_url_guard_uses_literal_prefix_and_never_logs_full_url() -> None:
    class Cursor(SessionCursor):
        def fetchone(self):
            return {"provider_course_id": "existing", "title": "old", "branch_id": "old-branch"}

    cursor = Cursor()
    logger = Mock()
    raw_url = "https://example.test/course/%_?key=public-course-key"
    course = {
        "provider": "TEST",
        "provider_course_id": "incoming",
        "raw_url": raw_url,
        "title": "new",
        "schedule_raw": "2026-08-01 ~ 2026-08-31 매주 월요일 10:00",
        "status": "OPEN",
        "branch_id": "new-branch",
    }
    coalesce_provider_course_id_by_raw_url(cursor, course, logger)
    sql = cursor.executed[-1][0]
    assert "starts_with" in sql
    assert " LIKE " not in sql
    assert raw_url not in repr(logger.warning.call_args)


def test_raw_url_guard_rejects_secret_query_before_sql_or_logging() -> None:
    cursor = SessionCursor()
    logger = Mock()
    course = {
        "provider": "TEST",
        "provider_course_id": "incoming",
        "raw_url": "https://example.test/course/1?access_token=super-secret",
        "title": "new",
        "branch_id": "new-branch",
    }

    with pytest.raises(ValueError, match="raw_url"):
        coalesce_provider_course_id_by_raw_url(cursor, course, logger)

    assert cursor.executed == []
    assert logger.warning.call_args is None
