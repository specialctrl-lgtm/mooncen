from __future__ import annotations

import copy
import hashlib
import math
import os
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests


CLOUDFLARE_GRAPHQL_URL = "https://api.cloudflare.com/client/v4/graphql"
VISITOR_ANALYTICS_HOSTNAME = "mooncen.kr"
VISITOR_ANALYTICS_HOSTNAMES = ("mooncen.kr", "www.mooncen.kr")
VISITOR_ANALYTICS_TIMEZONE = "Asia/Seoul"

_KST = timezone(timedelta(hours=9), name=VISITOR_ANALYTICS_TIMEZONE)
_CACHE_TTL_SECONDS = 300.0
_NEGATIVE_CACHE_TTL_SECONDS = 15.0
_CACHE_MAX_ENTRIES = 32
_MAX_DATA_REQUESTS = 31
# One settings request plus the bounded maximum number of data requests, each
# with a 2s connect and 5s read timeout, plus a small scheduling margin.
_SINGLE_FLIGHT_WAIT_SECONDS = (_MAX_DATA_REQUESTS + 1) * 7.0 + 5.0
_CONFIDENCE_LEVEL = 0.95
_MAX_SETTING_SECONDS = 10 * 366 * 24 * 60 * 60
_MAX_PAGE_SIZE = 1_000_000
_MAX_METRIC_VALUE = 10**18
_cache_lock = threading.Lock()
_CacheKey = tuple[int, str, bytes, date]
_cache: OrderedDict[_CacheKey, tuple[float, dict[str, Any]]] = OrderedDict()
_inflight: dict[_CacheKey, threading.Event] = {}

_SETTINGS_QUERY = """
query MoonCenVisitorSettings($zoneTag: string) {
  viewer {
    zones(filter: {zoneTag: $zoneTag}) {
      settings {
        httpRequestsAdaptiveGroups {
          enabled
          availableFields
          maxDuration
          maxPageSize
          notOlderThan
        }
      }
    }
  }
}
""".strip()

_DATA_QUERY = """
query MoonCenVisitorSummary($zoneTag: string, $start: Time, $end: Time, $limit: Int) {
  viewer {
    zones(filter: {zoneTag: $zoneTag}) {
      visitorSeries: httpRequestsAdaptiveGroups(
        limit: $limit
        orderBy: [datetimeHour_ASC]
        filter: {
          datetime_geq: $start
          datetime_lt: $end
          requestSource: "eyeball"
          OR: [
            {clientRequestHTTPHost: "mooncen.kr"}
            {clientRequestHTTPHost: "www.mooncen.kr"}
          ]
        }
      ) {
        count
        avg {
          sampleInterval
        }
        sum {
          visits
        }
        confidence(level: 0.95) {
          level
          count {
            estimate
            lower
            upper
            sampleSize
          }
          sum {
            visits {
              estimate
              lower
              upper
              sampleSize
            }
          }
        }
        dimensions {
          datetimeHour
        }
      }
    }
  }
}
""".strip()

_SOURCE = {
    "provider": "cloudflare",
    "dataset": "httpRequestsAdaptiveGroups",
    "hostname": VISITOR_ANALYTICS_HOSTNAME,
    "hostnames": list(VISITOR_ANALYTICS_HOSTNAMES),
    "request_source": "eyeball",
    "granularity": "hour",
    "adaptive_sampling": True,
    "values_are_estimates": True,
}

_METRIC_DEFINITIONS = {
    "visits": {
        "label": "Visits",
        "description": (
            "Cloudflare visits started by a direct request or a referrer outside the requested hostname; "
            "one visit may contain multiple requests, and automated traffic may be included."
        ),
        "unique_visitors": False,
        "estimated": True,
    },
    "requests": {
        "label": "HTTP requests",
        "description": (
            "Cloudflare eyeball HTTP requests matching mooncen.kr or www.mooncen.kr, including documents, assets, "
            "API requests, and potentially automated traffic."
        ),
        "pageviews": False,
        "estimated": True,
    },
    "pageviews": {
        "available": False,
        "reason_code": "pageviews_not_provided_by_http_requests_adaptive_groups",
    },
}


class _CloudflareAnalyticsError(RuntimeError):
    """Internal sentinel whose message is never returned to clients."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class _DatasetLimits:
    max_duration_seconds: int
    max_page_size: int
    not_older_than_seconds: int


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _monotonic() -> float:
    return time.monotonic()


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _base_payload(*, days: int, generated_at: datetime) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "available": False,
        "reason_code": None,
        "estimated": True,
        "timezone": VISITOR_ANALYTICS_TIMEZONE,
        "requested_days": days,
        "source": copy.deepcopy(_SOURCE),
        "metric_definitions": copy.deepcopy(_METRIC_DEFINITIONS),
        "sampling": {
            "method": "cloudflare_adaptive",
            "confidence_level": _CONFIDENCE_LEVEL,
            "confidence_intervals_requested": True,
            "validated_points": 0,
            "max_sample_interval": None,
            "min_sample_size": None,
            "aggregate_bounds_available": False,
        },
        "summary": None,
        "series": [],
        "data_through": None,
        "generated_at": _iso_utc(generated_at),
    }


def _unavailable(*, days: int, reason_code: str, generated_at: datetime) -> dict[str, Any]:
    payload = _base_payload(days=days, generated_at=generated_at)
    payload["reason_code"] = reason_code
    return payload


def _parse_hour(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise _CloudflareAnalyticsError("CLOUDFLARE_ANALYTICS_INVALID_RESPONSE")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _CloudflareAnalyticsError("CLOUDFLARE_ANALYTICS_INVALID_RESPONSE") from exc
    if parsed.tzinfo is None:
        raise _CloudflareAnalyticsError("CLOUDFLARE_ANALYTICS_INVALID_RESPONSE")
    return parsed.astimezone(timezone.utc)


def _nonnegative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _CloudflareAnalyticsError("CLOUDFLARE_ANALYTICS_INVALID_RESPONSE")
    if not math.isfinite(value) or value < 0 or int(value) != value:
        raise _CloudflareAnalyticsError("CLOUDFLARE_ANALYTICS_INVALID_RESPONSE")
    return int(value)


def _bounded_number(value: object, *, minimum: float = 0.0, maximum: float = float(_MAX_METRIC_VALUE)) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _CloudflareAnalyticsError("CLOUDFLARE_ANALYTICS_INVALID_RESPONSE")
    result = float(value)
    if not math.isfinite(result) or result < minimum or result > maximum:
        raise _CloudflareAnalyticsError("CLOUDFLARE_ANALYTICS_INVALID_RESPONSE")
    return result


def _bounded_positive_int(value: object, *, maximum: int) -> int:
    result = _nonnegative_int(value)
    if result < 1 or result > maximum:
        raise _CloudflareAnalyticsError("CLOUDFLARE_ANALYTICS_INVALID_RESPONSE")
    return result


def _post_graphql(*, token: str, query: str, variables: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        response = requests.post(
            CLOUDFLARE_GRAPHQL_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json={
                "query": query,
                "variables": dict(variables),
            },
            timeout=(2.0, 5.0),
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        raise _CloudflareAnalyticsError("CLOUDFLARE_ANALYTICS_UNAVAILABLE") from exc

    if response.status_code != 200:
        raise _CloudflareAnalyticsError("CLOUDFLARE_ANALYTICS_UNAVAILABLE")
    try:
        payload = response.json()
    except (ValueError, TypeError) as exc:
        raise _CloudflareAnalyticsError("CLOUDFLARE_ANALYTICS_INVALID_RESPONSE") from exc
    if not isinstance(payload, Mapping) or payload.get("errors"):
        raise _CloudflareAnalyticsError("CLOUDFLARE_ANALYTICS_INVALID_RESPONSE")
    return payload


def _one_zone(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    data = payload.get("data")
    viewer = data.get("viewer") if isinstance(data, Mapping) else None
    zones = viewer.get("zones") if isinstance(viewer, Mapping) else None
    if not isinstance(zones, list) or len(zones) != 1 or not isinstance(zones[0], Mapping):
        raise _CloudflareAnalyticsError("CLOUDFLARE_ANALYTICS_INVALID_RESPONSE")
    return zones[0]


def _request_dataset_limits(*, zone_id: str, token: str) -> _DatasetLimits:
    zone = _one_zone(
        _post_graphql(
            token=token,
            query=_SETTINGS_QUERY,
            variables={"zoneTag": zone_id},
        )
    )
    settings = zone.get("settings")
    dataset = settings.get("httpRequestsAdaptiveGroups") if isinstance(settings, Mapping) else None
    if not isinstance(dataset, Mapping):
        raise _CloudflareAnalyticsError("CLOUDFLARE_ANALYTICS_INVALID_RESPONSE")
    if dataset.get("enabled") is not True:
        raise _CloudflareAnalyticsError("CLOUDFLARE_ANALYTICS_UNAVAILABLE")

    available_fields = dataset.get("availableFields")
    if (
        not isinstance(available_fields, list)
        or len(available_fields) > 10_000
        or not all(isinstance(field, str) and 0 < len(field) <= 200 for field in available_fields)
    ):
        raise _CloudflareAnalyticsError("CLOUDFLARE_ANALYTICS_INVALID_RESPONSE")
    required_fields = {
        "avg_sampleInterval",
        "count",
        "dimensions_clientRequestHTTPHost",
        "dimensions_datetimeHour",
        "dimensions_requestSource",
        "sum_visits",
    }
    if not required_fields.issubset(set(available_fields)):
        raise _CloudflareAnalyticsError("CLOUDFLARE_ANALYTICS_UNAVAILABLE")

    return _DatasetLimits(
        max_duration_seconds=_bounded_positive_int(
            dataset.get("maxDuration"),
            maximum=_MAX_SETTING_SECONDS,
        ),
        max_page_size=_bounded_positive_int(
            dataset.get("maxPageSize"),
            maximum=_MAX_PAGE_SIZE,
        ),
        not_older_than_seconds=_bounded_positive_int(
            dataset.get("notOlderThan"),
            maximum=_MAX_SETTING_SECONDS,
        ),
    )


def _request_rows(
    *,
    zone_id: str,
    token: str,
    start: datetime,
    end: datetime,
    limit: int,
) -> list[Mapping[str, Any]]:
    zone = _one_zone(
        _post_graphql(
            token=token,
            query=_DATA_QUERY,
            variables={
                "zoneTag": zone_id,
                "start": _iso_utc(start),
                "end": _iso_utc(end),
                "limit": limit,
            },
        )
    )
    rows = zone.get("visitorSeries")
    if not isinstance(rows, list) or len(rows) > limit or not all(isinstance(row, Mapping) for row in rows):
        raise _CloudflareAnalyticsError("CLOUDFLARE_ANALYTICS_INVALID_RESPONSE")
    return rows


def _query_chunks(
    *,
    start: datetime,
    end: datetime,
    now: datetime,
    limits: _DatasetLimits,
) -> list[tuple[datetime, datetime, int]]:
    age_seconds = max(0, math.ceil((now - start).total_seconds()))
    if limits.not_older_than_seconds < age_seconds:
        raise _CloudflareAnalyticsError("CLOUDFLARE_ANALYTICS_RANGE_UNAVAILABLE")

    capacity_hours = min(limits.max_duration_seconds // 3_600, limits.max_page_size)
    # A minimum daily window bounds the number of upstream calls. Larger
    # allowances are rounded down to whole days and capped at one week to
    # reduce adaptive sampling variance on broad monthly queries.
    if capacity_hours < 24:
        raise _CloudflareAnalyticsError("CLOUDFLARE_ANALYTICS_RANGE_UNAVAILABLE")
    chunk_hours = min(7 * 24, (capacity_hours // 24) * 24)
    chunk_span = timedelta(hours=chunk_hours)
    chunks: list[tuple[datetime, datetime, int]] = []
    cursor = start
    while cursor < end:
        chunk_end = min(end, cursor + chunk_span)
        expected_hours = max(1, math.ceil((chunk_end - cursor).total_seconds() / 3_600))
        chunks.append((cursor, chunk_end, min(limits.max_page_size, expected_hours)))
        cursor = chunk_end
    if not chunks or len(chunks) > _MAX_DATA_REQUESTS:
        raise _CloudflareAnalyticsError("CLOUDFLARE_ANALYTICS_RANGE_UNAVAILABLE")
    return chunks


def _collect_rows(
    *,
    zone_id: str,
    token: str,
    chunks: list[tuple[datetime, datetime, int]],
) -> list[Mapping[str, Any]]:
    collected: list[Mapping[str, Any]] = []
    seen_hours: set[datetime] = set()
    for start, end, limit in chunks:
        rows = _request_rows(
            zone_id=zone_id,
            token=token,
            start=start,
            end=end,
            limit=limit,
        )
        for row in rows:
            dimensions = row.get("dimensions")
            if not isinstance(dimensions, Mapping):
                raise _CloudflareAnalyticsError("CLOUDFLARE_ANALYTICS_INVALID_RESPONSE")
            hour = _parse_hour(dimensions.get("datetimeHour"))
            if hour < start or hour >= end or hour in seen_hours:
                raise _CloudflareAnalyticsError("CLOUDFLARE_ANALYTICS_INVALID_RESPONSE")
            seen_hours.add(hour)
            collected.append(row)
    return collected


def _validate_confidence_interval(value: object, *, point_estimate: int) -> int:
    if not isinstance(value, Mapping):
        raise _CloudflareAnalyticsError("CLOUDFLARE_ANALYTICS_INVALID_RESPONSE")
    estimate = _bounded_number(value.get("estimate"))
    lower = _bounded_number(value.get("lower"))
    upper = _bounded_number(value.get("upper"))
    sample_size = _nonnegative_int(value.get("sampleSize"))
    if sample_size > _MAX_METRIC_VALUE or lower > estimate or estimate > upper:
        raise _CloudflareAnalyticsError("CLOUDFLARE_ANALYTICS_INVALID_RESPONSE")
    if not math.isclose(estimate, float(point_estimate), rel_tol=1e-9, abs_tol=0.5):
        raise _CloudflareAnalyticsError("CLOUDFLARE_ANALYTICS_INVALID_RESPONSE")
    return sample_size


def _validate_sampling(row: Mapping[str, Any], *, requests_count: int, visits: int) -> tuple[float, int]:
    average = row.get("avg")
    confidence = row.get("confidence")
    if not isinstance(average, Mapping) or not isinstance(confidence, Mapping):
        raise _CloudflareAnalyticsError("CLOUDFLARE_ANALYTICS_INVALID_RESPONSE")
    sample_interval = _bounded_number(
        average.get("sampleInterval"),
        minimum=1.0,
        maximum=1_000_000_000.0,
    )
    level = _bounded_number(confidence.get("level"), minimum=0.0, maximum=1.0)
    if not math.isclose(level, _CONFIDENCE_LEVEL, rel_tol=0.0, abs_tol=1e-9):
        raise _CloudflareAnalyticsError("CLOUDFLARE_ANALYTICS_INVALID_RESPONSE")
    count_sample_size = _validate_confidence_interval(
        confidence.get("count"),
        point_estimate=requests_count,
    )
    confidence_sum = confidence.get("sum")
    if not isinstance(confidence_sum, Mapping):
        raise _CloudflareAnalyticsError("CLOUDFLARE_ANALYTICS_INVALID_RESPONSE")
    visits_sample_size = _validate_confidence_interval(
        confidence_sum.get("visits"),
        point_estimate=visits,
    )
    return sample_interval, min(count_sample_size, visits_sample_size)


def _window_totals(
    daily: Mapping[date, Mapping[str, int]],
    *,
    start: date,
    end: date,
    today: date,
) -> dict[str, Any]:
    visits = 0
    requests_count = 0
    current = start
    while current <= end:
        point = daily.get(current, {})
        visits += int(point.get("visits", 0))
        requests_count += int(point.get("requests", 0))
        current += timedelta(days=1)
    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "visits": visits,
        "requests": requests_count,
        "partial": end >= today,
        "estimated": True,
    }


def _aggregate(
    rows: list[Mapping[str, Any]],
    *,
    days: int,
    end: datetime,
    calendar_today: date,
    history_days: int,
    generated_at: datetime,
) -> dict[str, Any]:
    daily: dict[date, dict[str, int]] = {}
    sample_intervals: list[float] = []
    sample_sizes: list[int] = []
    for row in rows:
        dimensions = row.get("dimensions")
        sums = row.get("sum")
        if not isinstance(dimensions, Mapping) or not isinstance(sums, Mapping):
            raise _CloudflareAnalyticsError("CLOUDFLARE_ANALYTICS_INVALID_RESPONSE")
        visits = _nonnegative_int(sums.get("visits"))
        requests_count = _nonnegative_int(row.get("count"))
        sample_interval, sample_size = _validate_sampling(
            row,
            requests_count=requests_count,
            visits=visits,
        )
        sample_intervals.append(sample_interval)
        sample_sizes.append(sample_size)
        day = _parse_hour(dimensions.get("datetimeHour")).astimezone(_KST).date()
        point = daily.setdefault(day, {"visits": 0, "requests": 0})
        point["visits"] += visits
        point["requests"] += requests_count

    today = calendar_today
    series_start = today - timedelta(days=days - 1)
    series: list[dict[str, Any]] = []
    for offset in range(days):
        current = series_start + timedelta(days=offset)
        point = daily.get(current, {"visits": 0, "requests": 0})
        series.append(
            {
                "date": current.isoformat(),
                "visits": point["visits"],
                "requests": point["requests"],
                "partial": current == today,
                "estimated": True,
            }
        )

    yesterday = today - timedelta(days=1)
    last_7_start = today - timedelta(days=6)
    previous_7_end = last_7_start - timedelta(days=1)
    previous_7_start = previous_7_end - timedelta(days=6)
    payload = _base_payload(days=days, generated_at=generated_at)
    payload.update(
        {
            "available": True,
            "summary": {
                "today": _window_totals(daily, start=today, end=today, today=today),
                "yesterday": _window_totals(daily, start=yesterday, end=yesterday, today=today),
                "last_7_days": _window_totals(daily, start=last_7_start, end=today, today=today),
                "previous_7_days": (
                    _window_totals(
                        daily,
                        start=previous_7_start,
                        end=previous_7_end,
                        today=today,
                    )
                    if history_days >= 14
                    else None
                ),
            },
            "series": series,
            "sampling": {
                "method": "cloudflare_adaptive",
                "confidence_level": _CONFIDENCE_LEVEL,
                "confidence_intervals_requested": True,
                "validated_points": len(rows),
                "max_sample_interval": max(sample_intervals, default=None),
                "min_sample_size": min(sample_sizes, default=None),
                # Hour-level confidence intervals cannot be added together
                # and represented as a statistically valid daily interval.
                "aggregate_bounds_available": False,
            },
            # Only complete UTC/KST-aligned hours are requested. This is the
            # exclusive upper bound of the successfully queried interval.
            "data_through": _iso_utc(end),
        }
    )
    return payload


def clear_visitor_analytics_cache() -> None:
    """Clear process-local analytics state; intended for tests and controlled reloads."""
    with _cache_lock:
        _cache.clear()
        events = list(_inflight.values())
        _inflight.clear()
    for event in events:
        event.set()


def _cached_payload_locked(cache_key: _CacheKey, monotonic_now: float) -> dict[str, Any] | None:
    cached = _cache.get(cache_key)
    if cached is None:
        return None
    expires_at, payload = cached
    if monotonic_now >= expires_at:
        _cache.pop(cache_key, None)
        return None
    _cache.move_to_end(cache_key)
    return copy.deepcopy(payload)


def _store_payload_locked(
    cache_key: _CacheKey,
    payload: Mapping[str, Any],
    *,
    ttl_seconds: float,
) -> None:
    _cache[cache_key] = (_monotonic() + ttl_seconds, copy.deepcopy(dict(payload)))
    _cache.move_to_end(cache_key)
    while len(_cache) > _CACHE_MAX_ENTRIES:
        _cache.popitem(last=False)


def get_visitor_summary(days: int) -> dict[str, Any]:
    """Return bounded Cloudflare visit/request aggregates without exposing credentials."""
    now = _utc_now().astimezone(timezone.utc)
    zone_id = os.getenv("OPS_CLOUDFLARE_ANALYTICS_ZONE_ID", "").strip()
    token = os.getenv("OPS_CLOUDFLARE_ANALYTICS_TOKEN", "").strip()
    if not zone_id or not token:
        return _unavailable(
            days=days,
            reason_code="CLOUDFLARE_ANALYTICS_NOT_CONFIGURED",
            generated_at=now,
        )

    snapshot_day = now.astimezone(_KST).date()
    cache_key = (days, zone_id, hashlib.sha256(token.encode("utf-8")).digest(), snapshot_day)
    leader = False
    with _cache_lock:
        cached_payload = _cached_payload_locked(cache_key, _monotonic())
        if cached_payload is not None:
            return cached_payload
        event = _inflight.get(cache_key)
        if event is None:
            event = threading.Event()
            _inflight[cache_key] = event
            leader = True

    if not leader:
        event.wait(_SINGLE_FLIGHT_WAIT_SECONDS)
        with _cache_lock:
            cached_payload = _cached_payload_locked(cache_key, _monotonic())
        if cached_payload is not None:
            return cached_payload
        return _unavailable(
            days=days,
            reason_code="CLOUDFLARE_ANALYTICS_UNAVAILABLE",
            generated_at=_utc_now(),
        )

    # Korea has used UTC+09:00 without DST throughout the API's data
    # retention period. Query complete hours only so partial hourly groups
    # are never presented as final values.
    end = now.replace(minute=0, second=0, microsecond=0)
    today = now.astimezone(_KST).date()
    retained_days = max(days, 7)
    first_day = today - timedelta(days=retained_days - 1)
    start = datetime.combine(first_day, datetime.min.time(), tzinfo=_KST).astimezone(timezone.utc)
    try:
        limits = _request_dataset_limits(zone_id=zone_id, token=token)
        chunks = _query_chunks(start=start, end=end, now=now, limits=limits)
        rows = _collect_rows(zone_id=zone_id, token=token, chunks=chunks)
        payload = _aggregate(
            rows,
            days=days,
            end=end,
            calendar_today=today,
            history_days=retained_days,
            generated_at=_utc_now(),
        )
    except _CloudflareAnalyticsError as exc:
        payload = _unavailable(
            days=days,
            reason_code=exc.reason_code,
            generated_at=_utc_now(),
        )
    except Exception:
        # Do not let an unexpected client/shape failure expose a request,
        # response, or credential through FastAPI's global exception logger.
        payload = _unavailable(
            days=days,
            reason_code="CLOUDFLARE_ANALYTICS_INVALID_RESPONSE",
            generated_at=_utc_now(),
        )

    with _cache_lock:
        ttl_seconds = _CACHE_TTL_SECONDS if payload["available"] is True else _NEGATIVE_CACHE_TTL_SECONDS
        _store_payload_locked(cache_key, payload, ttl_seconds=ttl_seconds)
        completed = _inflight.pop(cache_key, None)
    if completed is not None:
        completed.set()
    return payload
