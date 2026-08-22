from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
import requests
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from backend.main import app
from backend.routers import auth, visitor_analytics as visitor_router
from backend.services import visitor_analytics


FIXED_NOW = datetime(2026, 8, 14, 3, 30, tzinfo=timezone.utc)


class _Response:
    def __init__(self, payload: object, *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> object:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


AVAILABLE_FIELDS = [
    "avg_sampleInterval",
    "count",
    "dimensions_clientRequestHTTPHost",
    "dimensions_datetimeHour",
    "dimensions_requestSource",
    "sum_visits",
]


def _cloudflare_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "data": {
            "viewer": {
                "zones": [
                    {
                        "visitorSeries": rows,
                    }
                ]
            }
        },
        "errors": None,
    }


def _settings_payload(
    *,
    enabled: bool = True,
    max_duration: int = 7 * 24 * 3_600,
    max_page_size: int = 1_000,
    not_older_than: int = 90 * 24 * 3_600,
    available_fields: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "data": {
            "viewer": {
                "zones": [
                    {
                        "settings": {
                            "httpRequestsAdaptiveGroups": {
                                "enabled": enabled,
                                "availableFields": (
                                    AVAILABLE_FIELDS if available_fields is None else available_fields
                                ),
                                "maxDuration": max_duration,
                                "maxPageSize": max_page_size,
                                "notOlderThan": not_older_than,
                            }
                        }
                    }
                ]
            }
        },
        "errors": None,
    }


def _confidence(estimate: int, *, sample_size: int) -> dict[str, int | float]:
    return {
        "estimate": estimate,
        "lower": max(0, estimate - 1),
        "upper": estimate + 1,
        "sampleSize": sample_size,
    }


def _row(
    hour: str,
    *,
    visits: int,
    requests_count: int,
    sample_interval: float = 1.25,
    sample_size: int = 10,
) -> dict[str, Any]:
    return {
        "count": requests_count,
        "avg": {"sampleInterval": sample_interval},
        "sum": {"visits": visits},
        "confidence": {
            "level": 0.95,
            "count": _confidence(requests_count, sample_size=sample_size),
            "sum": {"visits": _confidence(visits, sample_size=sample_size)},
        },
        "dimensions": {"datetimeHour": hour},
    }


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _responder(
    rows: list[dict[str, Any]],
    calls: list[dict[str, Any]],
    *,
    settings_payload: dict[str, Any] | None = None,
):
    def fake_post(url: str, **kwargs: Any) -> _Response:
        calls.append({"url": url, **kwargs})
        query = kwargs["json"]["query"]
        if "MoonCenVisitorSettings" in query:
            return _Response(settings_payload or _settings_payload())
        variables = kwargs["json"]["variables"]
        start = _parse_iso(variables["start"])
        end = _parse_iso(variables["end"])
        selected = [
            row
            for row in rows
            if start <= _parse_iso(row["dimensions"]["datetimeHour"]) < end
        ]
        return _Response(_cloudflare_payload(selected))

    return fake_post


@pytest.fixture(autouse=True)
def _isolate_visitor_analytics(monkeypatch: pytest.MonkeyPatch):
    app.dependency_overrides.clear()
    visitor_analytics.clear_visitor_analytics_cache()
    with auth._rate_limit_lock:
        auth._rate_limit_buckets.clear()
    monkeypatch.delenv("OPS_CLOUDFLARE_ANALYTICS_ZONE_ID", raising=False)
    monkeypatch.delenv("OPS_CLOUDFLARE_ANALYTICS_TOKEN", raising=False)
    yield
    app.dependency_overrides.clear()
    visitor_analytics.clear_visitor_analytics_cache()


def test_visitor_summary_route_requires_ops_viewer_and_has_dedicated_rate_limit() -> None:
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
        if prefix + route.path == "/api/ops/dashboard/visitor-summary"
    )
    dependency_names = {
        getattr(dependency.call, "__name__", "") for dependency in route.dependant.dependencies
    }

    assert "require_ops_viewer" in dependency_names
    assert "rate_limit_ops_visitor_summary" in dependency_names
    response = TestClient(app, raise_server_exceptions=False).get(
        "/api/ops/dashboard/visitor-summary"
    )
    assert response.status_code == 401


def test_visitor_summary_route_validates_days_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[int] = []
    app.dependency_overrides[auth.require_ops_viewer] = lambda: object()
    monkeypatch.setattr(
        visitor_router,
        "get_visitor_summary",
        lambda days: seen.append(days) or {"available": True, "requested_days": days},
    )
    client = TestClient(app, raise_server_exceptions=False)

    assert client.get("/api/ops/dashboard/visitor-summary?days=0").status_code == 422
    assert client.get("/api/ops/dashboard/visitor-summary?days=31").status_code == 422
    valid = client.get("/api/ops/dashboard/visitor-summary?days=7")

    assert valid.status_code == 200
    assert valid.json() == {"available": True, "requested_days": 7}
    defaulted = client.get("/api/ops/dashboard/visitor-summary")
    assert defaulted.status_code == 200
    assert defaulted.json() == {"available": True, "requested_days": 7}
    assert seen == [7, 7]


def test_cloudflare_query_is_fixed_and_aggregates_hourly_rows_into_kst_days(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    token = "top-secret-cloudflare-token"
    monkeypatch.setenv("OPS_CLOUDFLARE_ANALYTICS_ZONE_ID", "zone-123")
    monkeypatch.setenv("OPS_CLOUDFLARE_ANALYTICS_TOKEN", token)
    monkeypatch.setattr(visitor_analytics, "_utc_now", lambda: FIXED_NOW)

    rows = [
        _row("2026-08-13T14:00:00Z", visits=2, requests_count=20),
        _row("2026-08-13T15:00:00Z", visits=3, requests_count=30),
        _row("2026-08-07T15:00:00Z", visits=5, requests_count=50),
        _row("2026-08-06T15:00:00Z", visits=7, requests_count=70),
        _row("2026-07-31T15:00:00Z", visits=11, requests_count=110),
    ]

    monkeypatch.setattr(visitor_analytics.requests, "post", _responder(rows, calls))

    payload = visitor_analytics.get_visitor_summary(3)

    assert len(calls) == 2
    assert all(call["url"] == "https://api.cloudflare.com/client/v4/graphql" for call in calls)
    assert all(call["timeout"] == (2.0, 5.0) for call in calls)
    assert all(call["allow_redirects"] is False for call in calls)
    assert all(call["headers"]["Authorization"] == f"Bearer {token}" for call in calls)
    settings_query = calls[0]["json"]["query"]
    assert "settings" in settings_query
    assert "availableFields" in settings_query
    assert "maxDuration" in settings_query
    assert "maxPageSize" in settings_query
    assert "notOlderThan" in settings_query
    query = calls[1]["json"]["query"]
    assert "httpRequestsAdaptiveGroups" in query
    assert 'requestSource: "eyeball"' in query
    assert 'clientRequestHTTPHost: "mooncen.kr"' in query
    assert 'clientRequestHTTPHost: "www.mooncen.kr"' in query
    assert "OR:" in query
    assert "count" in query and "visits" in query and "datetimeHour" in query
    assert "sampleInterval" in query
    assert "confidence(level: 0.95)" in query
    assert calls[1]["json"]["variables"] == {
        "zoneTag": "zone-123",
        "start": "2026-08-07T15:00:00Z",
        "end": "2026-08-14T03:00:00Z",
        "limit": 156,
    }

    assert payload["schema_version"] == 1
    assert payload["available"] is True
    assert payload["reason_code"] is None
    assert payload["estimated"] is True
    assert payload["timezone"] == "Asia/Seoul"
    assert payload["source"]["hostname"] == "mooncen.kr"
    assert payload["source"]["hostnames"] == ["mooncen.kr", "www.mooncen.kr"]
    assert payload["source"]["adaptive_sampling"] is True
    assert payload["data_through"] == "2026-08-14T03:00:00Z"
    assert payload["summary"]["today"] == {
        "start_date": "2026-08-14",
        "end_date": "2026-08-14",
        "visits": 3,
        "requests": 30,
        "partial": True,
        "estimated": True,
    }
    assert payload["summary"]["yesterday"]["visits"] == 2
    assert payload["summary"]["last_7_days"]["visits"] == 10
    assert payload["summary"]["last_7_days"]["requests"] == 100
    assert payload["summary"]["previous_7_days"] is None
    assert payload["series"] == [
        {"date": "2026-08-12", "visits": 0, "requests": 0, "partial": False, "estimated": True},
        {"date": "2026-08-13", "visits": 2, "requests": 20, "partial": False, "estimated": True},
        {"date": "2026-08-14", "visits": 3, "requests": 30, "partial": True, "estimated": True},
    ]
    assert payload["sampling"] == {
        "method": "cloudflare_adaptive",
        "confidence_level": 0.95,
        "confidence_intervals_requested": True,
        "validated_points": 3,
        "max_sample_interval": 1.25,
        "min_sample_size": 10,
        "aggregate_bounds_available": False,
    }
    assert payload["metric_definitions"]["visits"]["unique_visitors"] is False
    assert "automated traffic" in payload["metric_definitions"]["visits"]["description"]
    assert payload["metric_definitions"]["requests"]["pageviews"] is False
    assert "automated traffic" in payload["metric_definitions"]["requests"]["description"]
    assert payload["metric_definitions"]["pageviews"]["available"] is False
    assert token not in json.dumps(payload)


def test_previous_seven_day_summary_is_only_returned_with_fourteen_day_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    rows = [
        _row("2026-08-13T15:00:00Z", visits=3, requests_count=30),
        _row("2026-08-06T15:00:00Z", visits=7, requests_count=70),
        _row("2026-07-31T15:00:00Z", visits=11, requests_count=110),
    ]
    monkeypatch.setenv("OPS_CLOUDFLARE_ANALYTICS_ZONE_ID", "zone-123")
    monkeypatch.setenv("OPS_CLOUDFLARE_ANALYTICS_TOKEN", "secret")
    monkeypatch.setattr(visitor_analytics, "_utc_now", lambda: FIXED_NOW)
    monkeypatch.setattr(visitor_analytics.requests, "post", _responder(rows, calls))

    payload = visitor_analytics.get_visitor_summary(14)

    assert payload["available"] is True
    assert payload["summary"]["previous_7_days"]["visits"] == 18
    assert payload["summary"]["previous_7_days"]["requests"] == 180


def test_seven_day_plan_retention_supports_default_but_rejects_fourteen_days(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setenv("OPS_CLOUDFLARE_ANALYTICS_ZONE_ID", "zone-123")
    monkeypatch.setenv("OPS_CLOUDFLARE_ANALYTICS_TOKEN", "secret")
    monkeypatch.setattr(visitor_analytics, "_utc_now", lambda: FIXED_NOW)
    monkeypatch.setattr(
        visitor_analytics.requests,
        "post",
        _responder(
            [],
            calls,
            settings_payload=_settings_payload(not_older_than=7 * 24 * 3_600),
        ),
    )

    default_range = visitor_analytics.get_visitor_summary(7)
    extended_range = visitor_analytics.get_visitor_summary(14)

    assert default_range["available"] is True
    assert default_range["summary"]["previous_7_days"] is None
    assert extended_range["available"] is False
    assert extended_range["reason_code"] == "CLOUDFLARE_ANALYTICS_RANGE_UNAVAILABLE"
    assert len(calls) == 3


def test_visitor_summary_uses_a_five_minute_copy_safe_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    clock = [100.0]
    monkeypatch.setenv("OPS_CLOUDFLARE_ANALYTICS_ZONE_ID", "zone-123")
    monkeypatch.setenv("OPS_CLOUDFLARE_ANALYTICS_TOKEN", "secret")
    monkeypatch.setattr(visitor_analytics, "_utc_now", lambda: FIXED_NOW)
    monkeypatch.setattr(visitor_analytics, "_monotonic", lambda: clock[0])

    monkeypatch.setattr(
        visitor_analytics.requests,
        "post",
        _responder(
            [_row("2026-08-13T15:00:00Z", visits=1, requests_count=10)],
            calls,
        ),
    )

    first = visitor_analytics.get_visitor_summary(1)
    first["series"][0]["visits"] = 999
    second = visitor_analytics.get_visitor_summary(1)
    clock[0] += 301.0
    third = visitor_analytics.get_visitor_summary(1)

    assert len(calls) == 4
    assert second["series"][0]["visits"] == 1
    assert third["series"][0]["visits"] == 1
    assert len(visitor_analytics._cache) <= visitor_analytics._CACHE_MAX_ENTRIES


def test_settings_limits_split_the_range_into_daily_bounded_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setenv("OPS_CLOUDFLARE_ANALYTICS_ZONE_ID", "zone-123")
    monkeypatch.setenv("OPS_CLOUDFLARE_ANALYTICS_TOKEN", "secret")
    monkeypatch.setattr(visitor_analytics, "_utc_now", lambda: FIXED_NOW)
    monkeypatch.setattr(
        visitor_analytics.requests,
        "post",
        _responder(
            [],
            calls,
            settings_payload=_settings_payload(
                max_duration=24 * 3_600,
                max_page_size=24,
            ),
        ),
    )

    payload = visitor_analytics.get_visitor_summary(14)

    assert payload["available"] is True
    assert len(calls) == 15
    for call in calls[1:]:
        variables = call["json"]["variables"]
        assert _parse_iso(variables["end"]) - _parse_iso(variables["start"]) <= timedelta(days=1)
        assert 1 <= variables["limit"] <= 24


@pytest.mark.parametrize(
    "settings_payload",
    [
        _settings_payload(not_older_than=24 * 3_600),
        _settings_payload(max_duration=23 * 3_600),
        _settings_payload(max_page_size=23),
    ],
)
def test_insufficient_settings_range_fails_closed_before_data_queries(
    monkeypatch: pytest.MonkeyPatch,
    settings_payload: dict[str, Any],
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setenv("OPS_CLOUDFLARE_ANALYTICS_ZONE_ID", "zone-123")
    monkeypatch.setenv("OPS_CLOUDFLARE_ANALYTICS_TOKEN", "secret")
    monkeypatch.setattr(visitor_analytics, "_utc_now", lambda: FIXED_NOW)
    monkeypatch.setattr(
        visitor_analytics.requests,
        "post",
        _responder([], calls, settings_payload=settings_payload),
    )

    payload = visitor_analytics.get_visitor_summary(14)

    assert payload["available"] is False
    assert payload["reason_code"] == "CLOUDFLARE_ANALYTICS_RANGE_UNAVAILABLE"
    assert len(calls) == 1


def test_settings_missing_a_required_field_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setenv("OPS_CLOUDFLARE_ANALYTICS_ZONE_ID", "zone-123")
    monkeypatch.setenv("OPS_CLOUDFLARE_ANALYTICS_TOKEN", "secret")
    monkeypatch.setattr(visitor_analytics, "_utc_now", lambda: FIXED_NOW)
    monkeypatch.setattr(
        visitor_analytics.requests,
        "post",
        _responder(
            [],
            calls,
            settings_payload=_settings_payload(
                available_fields=[field for field in AVAILABLE_FIELDS if field != "sum_visits"]
            ),
        ),
    )

    payload = visitor_analytics.get_visitor_summary(14)

    assert payload["available"] is False
    assert payload["reason_code"] == "CLOUDFLARE_ANALYTICS_UNAVAILABLE"
    assert len(calls) == 1


def test_invalid_sampling_metadata_fails_closed_without_claiming_exact_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    bad_row = _row("2026-08-13T15:00:00Z", visits=1, requests_count=10)
    bad_row["confidence"]["count"]["lower"] = 11
    monkeypatch.setenv("OPS_CLOUDFLARE_ANALYTICS_ZONE_ID", "zone-123")
    monkeypatch.setenv("OPS_CLOUDFLARE_ANALYTICS_TOKEN", "secret")
    monkeypatch.setattr(visitor_analytics, "_utc_now", lambda: FIXED_NOW)
    monkeypatch.setattr(visitor_analytics.requests, "post", _responder([bad_row], calls))

    payload = visitor_analytics.get_visitor_summary(14)

    assert payload["available"] is False
    assert payload["reason_code"] == "CLOUDFLARE_ANALYTICS_INVALID_RESPONSE"
    assert payload["estimated"] is True
    assert payload["summary"] is None


def test_calendar_day_is_fixed_to_the_initial_snapshot_across_kst_midnight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    before_midnight = datetime(2026, 8, 14, 14, 59, tzinfo=timezone.utc)
    after_midnight = datetime(2026, 8, 14, 15, 1, tzinfo=timezone.utc)
    times = iter([before_midnight, after_midnight])
    monkeypatch.setenv("OPS_CLOUDFLARE_ANALYTICS_ZONE_ID", "zone-123")
    monkeypatch.setenv("OPS_CLOUDFLARE_ANALYTICS_TOKEN", "secret")
    monkeypatch.setattr(visitor_analytics, "_utc_now", lambda: next(times))
    monkeypatch.setattr(visitor_analytics.requests, "post", _responder([], calls))

    payload = visitor_analytics.get_visitor_summary(1)

    assert payload["available"] is True
    assert payload["series"][0]["date"] == "2026-08-14"
    assert payload["summary"]["today"]["start_date"] == "2026-08-14"
    assert payload["generated_at"] == "2026-08-14T15:01:00Z"


def test_single_flight_does_not_hold_the_cache_lock_during_network_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    entered = threading.Event()
    release = threading.Event()
    base_responder = _responder([], calls)
    monkeypatch.setenv("OPS_CLOUDFLARE_ANALYTICS_ZONE_ID", "zone-123")
    monkeypatch.setenv("OPS_CLOUDFLARE_ANALYTICS_TOKEN", "secret")
    monkeypatch.setattr(visitor_analytics, "_utc_now", lambda: FIXED_NOW)

    def blocking_post(url: str, **kwargs: Any) -> _Response:
        if "MoonCenVisitorSettings" in kwargs["json"]["query"]:
            entered.set()
            assert release.wait(2.0)
        return base_responder(url, **kwargs)

    monkeypatch.setattr(visitor_analytics.requests, "post", blocking_post)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(visitor_analytics.get_visitor_summary, 14)
        assert entered.wait(1.0)
        assert visitor_analytics._cache_lock.acquire(timeout=0.2)
        visitor_analytics._cache_lock.release()
        second = executor.submit(visitor_analytics.get_visitor_summary, 14)
        release.set()
        first_payload = first.result(timeout=5.0)
        second_payload = second.result(timeout=5.0)

    assert first_payload["available"] is True
    assert second_payload == first_payload
    assert len(calls) == 3


def test_missing_cloudflare_configuration_is_reported_without_an_upstream_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        visitor_analytics.requests,
        "post",
        lambda *_args, **_kwargs: pytest.fail("Cloudflare must not be called"),
    )

    payload = visitor_analytics.get_visitor_summary(14)

    assert payload["available"] is False
    assert payload["reason_code"] == "CLOUDFLARE_ANALYTICS_NOT_CONFIGURED"
    assert payload["summary"] is None
    assert payload["series"] == []


def test_cloudflare_transport_error_returns_only_a_safe_reason_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "never-return-this-token"
    calls = 0
    clock = [100.0]
    monkeypatch.setenv("OPS_CLOUDFLARE_ANALYTICS_ZONE_ID", "zone-123")
    monkeypatch.setenv("OPS_CLOUDFLARE_ANALYTICS_TOKEN", token)
    monkeypatch.setattr(visitor_analytics, "_monotonic", lambda: clock[0])

    def fail(*_args: Any, **_kwargs: Any) -> _Response:
        nonlocal calls
        calls += 1
        raise requests.Timeout(f"failed with {token}")

    monkeypatch.setattr(visitor_analytics.requests, "post", fail)

    payload = visitor_analytics.get_visitor_summary(14)
    cached = visitor_analytics.get_visitor_summary(14)
    clock[0] += 16.0
    retried = visitor_analytics.get_visitor_summary(14)

    assert payload["available"] is False
    assert payload["reason_code"] == "CLOUDFLARE_ANALYTICS_UNAVAILABLE"
    assert cached["reason_code"] == "CLOUDFLARE_ANALYTICS_UNAVAILABLE"
    assert retried["reason_code"] == "CLOUDFLARE_ANALYTICS_UNAVAILABLE"
    assert calls == 2
    assert token not in json.dumps([payload, cached, retried])


def test_cloudflare_invalid_response_returns_only_a_safe_reason_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "never-return-this-token"
    monkeypatch.setenv("OPS_CLOUDFLARE_ANALYTICS_ZONE_ID", "zone-123")
    monkeypatch.setenv("OPS_CLOUDFLARE_ANALYTICS_TOKEN", token)
    monkeypatch.setattr(
        visitor_analytics.requests,
        "post",
        lambda *_args, **_kwargs: _Response(
            {"errors": [{"message": f"upstream leaked {token}"}]}
        ),
    )

    payload = visitor_analytics.get_visitor_summary(14)

    assert payload["available"] is False
    assert payload["reason_code"] == "CLOUDFLARE_ANALYTICS_INVALID_RESPONSE"
    assert token not in json.dumps(payload)
