from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from backend.database import get_db
from backend.main import app
from backend.routers import auth, server_monitor


TOKEN = "server-monitor-token-0123456789abcdef"
REPLACEMENT_TOKEN = "replacement-monitor-token-0123456789ab"
FIXED_NOW = datetime(2026, 8, 15, 4, 5, 6, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _isolate_server_monitor(monkeypatch: pytest.MonkeyPatch):
    app.dependency_overrides.clear()
    with auth._rate_limit_lock:
        auth._rate_limit_buckets.clear()
    monkeypatch.delenv("MOONCEN_SERVER_MONITOR_TOKEN", raising=False)
    yield
    app.dependency_overrides.clear()


def _client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _headers(token: str = TOKEN) -> dict[str, str]:
    return {"X-MoonCen-Monitor-Token": token}


def _quality_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "available": True,
        "counts": {
            "active_courses": 125,
            "missing_required": 0,
            "invalid_dates": None,
            "invalid_prices": 2,
            "missing_address": 3,
            "missing_coordinates": 4,
            "incomplete_location": 5,
            "out_of_korea": 6,
            "duplicate_urls": 7,
            "blocked_sync": 8,
            "internal_only_count": 999,
        },
        "issue_statuses": [
            {"status": "open", "severity": "high", "issue_count": 0, "secret": "drop-me"},
            {"status": "reviewing", "severity": "medium", "issue_count": None},
        ],
        "latest_scan_at": datetime(2026, 8, 14, 23, 59, tzinfo=timezone.utc),
        "rule_source": "production courses/service_group",
        "internal_details": {"raw_rows": ["drop-me"]},
    }
    payload.update(overrides)
    return payload


def _allow_database() -> object:
    database = object()
    app.dependency_overrides[get_db] = lambda: database
    return database


def test_route_is_registered_with_dedicated_auth_and_rate_limit() -> None:
    candidates: list[tuple[str, APIRoute]] = []
    for included in app.routes:
        if isinstance(included, APIRoute):
            candidates.append(("", included))
        elif hasattr(included, "original_router") and hasattr(included, "include_context"):
            candidates.extend(
                (included.include_context.prefix, route)
                for route in included.original_router.routes
                if isinstance(route, APIRoute)
            )
    route = next(
        route
        for prefix, route in candidates
        if prefix + route.path == "/api/monitoring/crawler-quality"
    )
    dependency_names = {
        getattr(dependency.call, "__name__", "") for dependency in route.dependant.dependencies
    }

    assert route.methods == {"GET"}
    assert "require_server_monitor_token" in dependency_names
    assert "rate_limit_server_monitor_crawler_quality" in dependency_names


@pytest.mark.parametrize(
    ("configured", "supplied"),
    [
        (None, None),
        (None, TOKEN),
        ("short", "short"),
        (TOKEN, None),
        (TOKEN, "wrong-monitor-token-0123456789abcdef"),
        (TOKEN, f"{TOKEN}!"),
    ],
)
def test_authentication_failures_are_hidden_as_not_found(
    monkeypatch: pytest.MonkeyPatch,
    configured: str | None,
    supplied: str | None,
) -> None:
    if configured is not None:
        monkeypatch.setenv("MOONCEN_SERVER_MONITOR_TOKEN", configured)
    monkeypatch.setattr(
        server_monitor,
        "quality_summary",
        lambda _db: pytest.fail("quality query must not run before authorization"),
    )
    request_headers = {} if supplied is None else _headers(supplied)

    response = _client().get("/api/monitoring/crawler-quality", headers=request_headers)

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
    assert configured not in response.text if configured else True
    assert supplied not in response.text if supplied else True


def test_valid_token_returns_only_the_bounded_production_quality_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOONCEN_SERVER_MONITOR_TOKEN", TOKEN)
    monkeypatch.setattr(server_monitor, "_utc_now", lambda: FIXED_NOW)
    database = _allow_database()
    seen_databases: list[object] = []

    def fake_quality_summary(db: object) -> dict[str, Any]:
        seen_databases.append(db)
        return _quality_payload()

    monkeypatch.setattr(server_monitor, "quality_summary", fake_quality_summary)

    response = _client().get("/api/monitoring/crawler-quality", headers=_headers())

    assert response.status_code == 200
    assert seen_databases == [database]
    assert response.json() == {
        "schema_version": 1,
        "generated_at": "2026-08-15T04:05:06Z",
        "available": True,
        "source": "production_database",
        "counts": {
            "active_courses": 125,
            "missing_required": 0,
            "invalid_dates": None,
            "invalid_prices": 2,
            "missing_address": 3,
            "missing_coordinates": 4,
            "incomplete_location": 5,
            "out_of_korea": 6,
            "duplicate_urls": 7,
            "blocked_sync": 8,
        },
        "issue_statuses": [
            {"status": "open", "severity": "high", "issue_count": 0},
            {"status": "reviewing", "severity": "medium", "issue_count": None},
        ],
        "latest_scan_at": "2026-08-14T23:59:00Z",
        "rule_source": "production courses/service_group",
    }
    serialized = json.dumps(response.json())
    assert TOKEN not in serialized
    assert "internal_only_count" not in serialized
    assert "internal_details" not in serialized
    assert "secret" not in serialized


def test_issue_statuses_are_bounded_and_projected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOONCEN_SERVER_MONITOR_TOKEN", TOKEN)
    _allow_database()
    statuses = [
        {"status": f"status-{index}", "severity": "low", "issue_count": index, "raw": TOKEN}
        for index in range(150)
    ]
    monkeypatch.setattr(
        server_monitor,
        "quality_summary",
        lambda _db: _quality_payload(issue_statuses=statuses),
    )

    response = _client().get("/api/monitoring/crawler-quality", headers=_headers())

    assert response.status_code == 200
    returned = response.json()["issue_statuses"]
    assert len(returned) == 100
    assert returned[0] == {"status": "status-0", "severity": "low", "issue_count": 0}
    assert returned[-1] == {"status": "status-99", "severity": "low", "issue_count": 99}
    assert TOKEN not in response.text


def test_token_configuration_is_read_for_every_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOONCEN_SERVER_MONITOR_TOKEN", TOKEN)
    _allow_database()
    monkeypatch.setattr(server_monitor, "quality_summary", lambda _db: _quality_payload())
    client = _client()

    assert client.get("/api/monitoring/crawler-quality", headers=_headers()).status_code == 200
    monkeypatch.setenv("MOONCEN_SERVER_MONITOR_TOKEN", REPLACEMENT_TOKEN)
    assert client.get("/api/monitoring/crawler-quality", headers=_headers()).status_code == 404
    assert client.get(
        "/api/monitoring/crawler-quality",
        headers=_headers(REPLACEMENT_TOKEN),
    ).status_code == 200
