from __future__ import annotations

from uuid import uuid4

import pytest

from DB import db_utils


class _Cursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...] | None]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql: str, params=None) -> None:
        self.executed.append((sql, params))


class _Connection:
    def __init__(self) -> None:
        self.cursor_value = _Cursor()
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_value

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _set_valid_lease(monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    job_id = str(uuid4())
    token = str(uuid4())
    monkeypatch.setenv("CRAWL_WRITE_MODE", "staging")
    monkeypatch.setenv("CRAWL_REQUIRE_LEASE", "true")
    monkeypatch.setenv("CRAWL_JOB_ID", job_id)
    monkeypatch.setenv("CRAWL_LEASE_TOKEN", token)
    monkeypatch.setenv("CRAWL_LEASE_EPOCH", "7")
    monkeypatch.setenv("CRAWL_ATTEMPT_NO", "3")
    return job_id, token


def test_disabled_lease_clears_all_authority_markers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CRAWL_REQUIRE_LEASE", raising=False)
    monkeypatch.setenv("CRAWL_JOB_ID", str(uuid4()))
    monkeypatch.setenv("CRAWL_LEASE_TOKEN", str(uuid4()))
    monkeypatch.setenv("CRAWL_LEASE_EPOCH", "9")
    monkeypatch.setenv("CRAWL_ATTEMPT_NO", "2")

    settings = db_utils._crawl_lease_session_settings()

    assert settings["mooncen.require_crawler_lease"] == "off"
    assert all(not value for key, value in settings.items() if key != "mooncen.require_crawler_lease")


def test_required_lease_is_valid_only_for_staging_with_complete_markers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_valid_lease(monkeypatch)
    settings = db_utils._crawl_lease_session_settings()
    assert settings["mooncen.crawl_lease_epoch"] == "7"
    assert settings["mooncen.crawl_attempt_no"] == "3"

    monkeypatch.setenv("CRAWL_WRITE_MODE", "primary")
    with pytest.raises(RuntimeError, match="staging database"):
        db_utils._crawl_lease_session_settings()

    monkeypatch.setenv("CRAWL_WRITE_MODE", "staging")
    monkeypatch.setenv("CRAWL_LEASE_TOKEN", "not-a-token")
    with pytest.raises(RuntimeError, match="lease token"):
        db_utils._crawl_lease_session_settings()


def test_session_configuration_publishes_fence_before_batch_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id, token = _set_valid_lease(monkeypatch)
    monkeypatch.setenv("CRAWL_BATCH_ID", "batch-20260810")
    connection = _Connection()

    db_utils._configure_session(connection)

    settings = {
        params[0]: params[1]
        for sql, params in connection.cursor_value.executed
        if params and sql == "SELECT set_config(%s, %s, false)"
    }
    assert settings == {
        "mooncen.crawl_job_id": job_id,
        "mooncen.crawl_lease_token": token,
        "mooncen.crawl_lease_epoch": "7",
        "mooncen.crawl_attempt_no": "3",
        "mooncen.require_crawler_lease": "on",
    }
    assert connection.cursor_value.executed[-1][1] == ("batch-20260810",)
    assert connection.commits == 1
    assert connection.rollbacks == 0
