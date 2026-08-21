from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from backend import crawler_control_database


class _Result:
    def __init__(self, row: Mapping[str, Any]) -> None:
        self._row = row

    def mappings(self) -> _Result:
        return self

    def one(self) -> Mapping[str, Any]:
        return self._row


class _Connection:
    def __init__(self, row: Mapping[str, Any]) -> None:
        self._row = row

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, _statement: object) -> _Result:
        return _Result(self._row)


class _Engine:
    def __init__(self, row: Mapping[str, Any]) -> None:
        self._row = row

    def connect(self) -> _Connection:
        return _Connection(self._row)


def test_optional_crawler_control_pool_does_not_affect_api_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPS_CRAWLER_API_DB_REQUIRED", raising=False)
    monkeypatch.setattr(
        crawler_control_database,
        "crawler_control_engine",
        lambda: pytest.fail("optional pool must not be opened"),
    )

    crawler_control_database.assert_required_crawler_control_ready()


def test_required_crawler_control_pool_proves_marker_and_read_only_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPS_CRAWLER_API_DB_REQUIRED", "true")
    monkeypatch.setattr(
        crawler_control_database,
        "crawler_control_engine",
        lambda: _Engine(
            {
                "database_name": "mooncen_staging",
                "transaction_read_only": "on",
                "marker_matches": True,
            }
        ),
    )
    monkeypatch.setattr(
        crawler_control_database,
        "_configured_endpoint",
        lambda: ("127.0.0.1", 15433, "mooncen_staging", "crawler_api", "secret"),
    )

    crawler_control_database.assert_required_crawler_control_ready()


@pytest.mark.parametrize(
    ("database_name", "read_only", "marker_matches"),
    [
        ("wrong_database", "on", True),
        ("mooncen_staging", "off", True),
        ("mooncen_staging", "on", False),
    ],
)
def test_required_crawler_control_pool_fails_closed_on_identity_or_boundary_drift(
    monkeypatch: pytest.MonkeyPatch,
    database_name: str,
    read_only: str,
    marker_matches: bool,
) -> None:
    monkeypatch.setenv("OPS_CRAWLER_API_DB_REQUIRED", "true")
    monkeypatch.setattr(
        crawler_control_database,
        "crawler_control_engine",
        lambda: _Engine(
            {
                "database_name": database_name,
                "transaction_read_only": read_only,
                "marker_matches": marker_matches,
            }
        ),
    )
    monkeypatch.setattr(
        crawler_control_database,
        "_configured_endpoint",
        lambda: ("127.0.0.1", 15433, "mooncen_staging", "crawler_api", "secret"),
    )

    with pytest.raises(RuntimeError, match="readiness contract failed"):
        crawler_control_database.assert_required_crawler_control_ready()
