from __future__ import annotations

from contextlib import contextmanager

import pytest

from tools.maintenance import backfill_standard_categories as backfill


def test_schema_preflight_is_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    queries: list[str] = []

    class Cursor:
        def execute(self, query, params) -> None:
            queries.append(str(query))
            assert params == (
                tuple(sorted(backfill.REQUIRED_COURSE_COLUMNS)),
            )

        def fetchall(self):
            return [
                {"column_name": name}
                for name in sorted(backfill.REQUIRED_COURSE_COLUMNS)
            ]

    @contextmanager
    def fake_cursor():
        yield Cursor()

    monkeypatch.setattr(backfill, "get_db_cursor", fake_cursor)

    backfill.ensure_columns()

    assert len(queries) == 1
    assert "information_schema.columns" in queries[0]
    assert "ALTER TABLE" not in queries[0]
    assert "CREATE INDEX" not in queries[0]


def test_schema_preflight_rejects_missing_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Cursor:
        def execute(self, _query, _params) -> None:
            pass

        def fetchall(self):
            return [{"column_name": "standard_category_key"}]

    @contextmanager
    def fake_cursor():
        yield Cursor()

    monkeypatch.setattr(backfill, "get_db_cursor", fake_cursor)

    with pytest.raises(RuntimeError, match="standard_category_label"):
        backfill.ensure_columns()
