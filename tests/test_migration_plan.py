from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from DB import setup_db


class Cursor:
    def __init__(self, rows: list[tuple[str, str]], *, ledger_exists: bool = True):
        self.rows = rows
        self.ledger_exists = ledger_exists
        self.query = ""

    def execute(self, query: str) -> None:
        self.query = query

    def fetchone(self):
        if "to_regclass" in self.query:
            return ("mooncen_schema_migrations" if self.ledger_exists else None,)
        raise AssertionError(self.query)

    def fetchall(self):
        assert "SELECT version, checksum" in self.query
        return self.rows


class Connection:
    def __init__(self, cursor: Cursor):
        self._cursor = cursor
        self.rollbacks = 0
        self.closed = False

    def cursor(self) -> Cursor:
        return self._cursor

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


def _migrations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    root = tmp_path / "migrations"
    root.mkdir()
    (root / "002_second.sql").write_text("SELECT 2;\n", encoding="utf-8")
    (root / "001_first.sql").write_text("SELECT 1;\n", encoding="utf-8")
    monkeypatch.setattr(setup_db, "MIGRATIONS_DIR", root)
    return setup_db.expected_migration_ledger()


def test_plan_is_read_only_current_and_digest_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _migrations(tmp_path, monkeypatch)
    connection = Connection(
        Cursor([(item["version"], item["checksum"]) for item in expected])
    )
    monkeypatch.setattr(setup_db, "get_db_connection", lambda: connection)

    plan = setup_db.versioned_migration_plan()

    assert plan["current"] is True
    assert plan["pending"] == []
    assert plan["expected_ledger_sha256"] == plan["applied_ledger_sha256"]
    assert connection.rollbacks == 1
    assert connection.closed is True


def test_plan_reports_pending_without_applying_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _migrations(tmp_path, monkeypatch)
    connection = Connection(Cursor([(expected[0]["version"], expected[0]["checksum"])]))
    monkeypatch.setattr(setup_db, "get_db_connection", lambda: connection)

    plan = setup_db.versioned_migration_plan()

    assert plan["current"] is False
    assert plan["pending"] == [expected[1]["version"]]
    assert connection.rollbacks == 1


def test_plan_rejects_unknown_or_mismatched_database_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _migrations(tmp_path, monkeypatch)
    unknown = Connection(Cursor([("999_unknown", "f" * 64)]))
    monkeypatch.setattr(setup_db, "get_db_connection", lambda: unknown)
    with pytest.raises(RuntimeError, match="unknown migration"):
        setup_db.versioned_migration_plan()
    assert unknown.rollbacks == 2

    mismatch = Connection(Cursor([(expected[0]["version"], "f" * 64)]))
    monkeypatch.setattr(setup_db, "get_db_connection", lambda: mismatch)
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        setup_db.versioned_migration_plan()
    assert mismatch.rollbacks == 2


def test_plan_requires_existing_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _migrations(tmp_path, monkeypatch)
    connection = Connection(Cursor([], ledger_exists=False))
    monkeypatch.setattr(setup_db, "get_db_connection", lambda: connection)
    with pytest.raises(RuntimeError, match="ledger is missing"):
        setup_db.versioned_migration_plan()
    assert connection.rollbacks == 2


def test_empty_applied_digest_is_canonical_empty_array() -> None:
    assert hashlib.sha256(b"[]").hexdigest() == setup_db.migration_ledger_digest([])
