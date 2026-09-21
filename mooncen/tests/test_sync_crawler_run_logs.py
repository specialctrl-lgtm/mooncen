from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call

import pytest

import tools.promote_latest_staging_batch as promotion
from tools.sync_crawler_run_logs import (
    SYNC_COLUMNS,
    fetch_staging_logs,
    sync_crawler_run_logs,
)


class FakeCursor:
    def __init__(self, fetch_data: list[dict[str, Any]] | None = None) -> None:
        self.fetch_data = fetch_data or []
        self.executed_queries: list[tuple[str, Any]] = []

    def execute(self, query: str, params: Any = None) -> None:
        self.executed_queries.append((query, params))

    def fetchall(self) -> list[dict[str, Any]]:
        return self.fetch_data

    def fetchone(self) -> tuple[Any, ...] | None:
        if self.fetch_data:
            return (self.fetch_data[0].get("started_at"),)
        return None

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


class FakeConnection:
    def __init__(self, fetch_data: list[dict[str, Any]] | None = None) -> None:
        self.closed = False
        self.readonly = False
        self.committed = False
        self.rolled_back = False
        self.fetch_data = fetch_data or []
        self.cursor_instance = FakeCursor(self.fetch_data)

    def set_session(self, *, readonly: bool, autocommit: bool) -> None:
        self.readonly = readonly

    def cursor(self, *args: Any, **kwargs: Any) -> FakeCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def test_sync_crawler_run_logs_no_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    staging = FakeConnection([])
    primary = FakeConnection([])
    monkeypatch.setattr(
        "tools.sync_crawler_run_logs.ensure_unique_index",
        lambda _conn: None,
    )

    result = sync_crawler_run_logs(staging, primary, full_sync=True)
    assert result["status"] == "NO_NEW_LOGS"
    assert result["fetched"] == 0
    assert result["upserted"] == 0
    assert not primary.committed


def test_sync_crawler_run_logs_with_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    sample_log = {
        "target_key": "EMART",
        "source_type": "web",
        "crawler_name": "Crawler_Emart.py",
        "status": "success",
        "started_at": datetime(2026, 9, 21, 10, 0, 0, tzinfo=timezone.utc),
        "ended_at": datetime(2026, 9, 21, 10, 3, 0, tzinfo=timezone.utc),
        "duration_seconds": 180,
        "collected_count": 100,
        "inserted_count": 10,
        "updated_count": 90,
        "skipped_count": 0,
        "error_type": None,
        "error_message": None,
        "created_at": datetime(2026, 9, 21, 10, 0, 0, tzinfo=timezone.utc),
    }
    staging = FakeConnection([sample_log])
    primary = FakeConnection([])

    monkeypatch.setattr(
        "tools.sync_crawler_run_logs.ensure_unique_index",
        lambda _conn: None,
    )
    executed_batches: list[tuple[Any, str, list[dict]]] = []
    monkeypatch.setattr(
        "tools.sync_crawler_run_logs.execute_batch",
        lambda cur, sql, rows, page_size: executed_batches.append((cur, sql, rows)),
    )

    result = sync_crawler_run_logs(staging, primary, full_sync=True)
    assert result["status"] == "SUCCESS"
    assert result["fetched"] == 1
    assert result["upserted"] == 1
    assert primary.committed
    assert len(executed_batches) == 1
    assert executed_batches[0][2] == [sample_log]


def test_promote_latest_batch_invokes_sync_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_path.chmod(0o700)
    staging = FakeConnection([])
    primary = FakeConnection([])
    connections = iter((staging, primary))

    monkeypatch.setattr(promotion, "latest_batch_id", lambda _conn: "batch-123")
    monkeypatch.setattr(
        promotion,
        "successful_apply_result",
        lambda _conn, _batch_id: {"staging_fingerprint": "ffff" * 16},
    )

    sync_called: list[bool] = []

    def mock_sync(s_conn: Any, p_conn: Any) -> dict[str, Any]:
        sync_called.append(True)
        return {"status": "SUCCESS", "upserted": 5}

    result = promotion.promote_latest_batch(
        runtime_directory=tmp_path,
        connect_func=lambda _config: next(connections),
        sync_logs_func=mock_sync,
    )

    assert result["status"] == "NO_NEW_BATCH"
    assert len(sync_called) == 1

