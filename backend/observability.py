from __future__ import annotations

import math
import re
import threading
import time
from collections import Counter, deque
from typing import Any


_MAX_SAMPLES = 20_000
_samples: deque[tuple[float, str, int, float]] = deque(maxlen=_MAX_SAMPLES)
_exception_samples: deque[tuple[float, str, str]] = deque(maxlen=_MAX_SAMPLES)
_lock = threading.Lock()
_started_at = time.time()
_uuid_segment = re.compile(r"^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")
_opaque_segment = re.compile(r"^[A-Za-z0-9_-]{24,}$")


def normalized_route(path: str) -> str:
    """Bound metric cardinality without retaining user-controlled identifiers."""
    parts: list[str] = []
    for raw in str(path or "/").split("/"):
        if not raw:
            continue
        if raw.isdigit() or _uuid_segment.fullmatch(raw) or _opaque_segment.fullmatch(raw):
            parts.append("{id}")
        else:
            parts.append(raw[:80])
    return "/" + "/".join(parts)


def record_request(path: str, status_code: int, duration_seconds: float) -> None:
    if path in {"/health", "/live", "/api/ops/runtime-metrics"}:
        return
    sample = (
        time.time(),
        normalized_route(path),
        max(100, min(int(status_code), 599)),
        max(0.0, min(float(duration_seconds), 300.0)),
    )
    with _lock:
        _samples.append(sample)


def record_exception(path: str, exc: BaseException) -> None:
    """Record bounded exception identity without retaining messages or request data."""

    raw_kind = type(exc).__name__
    kind = re.sub(r"[^0-9A-Za-z_]", "_", raw_kind)[:80] or "Exception"
    sample = (time.time(), normalized_route(path), kind)
    with _lock:
        _exception_samples.append(sample)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * percentile) - 1))
    return ordered[index]


def runtime_metrics(window_seconds: int = 900) -> dict[str, Any]:
    window_seconds = max(60, min(int(window_seconds), 86_400))
    cutoff = time.time() - window_seconds
    with _lock:
        rows = [row for row in _samples if row[0] >= cutoff]
        exception_rows = [row for row in _exception_samples if row[0] >= cutoff]

    requests = len(rows)
    server_errors = sum(1 for _, _, status, _ in rows if status >= 500)
    client_errors = sum(1 for _, _, status, _ in rows if 400 <= status < 500)
    durations = [duration for _, _, _, duration in rows]
    by_route: Counter[str] = Counter(route for _, route, _, _ in rows)
    route_errors: Counter[str] = Counter(route for _, route, status, _ in rows if status >= 500)
    slowest: dict[str, list[float]] = {}
    for _, route, _, duration in rows:
        slowest.setdefault(route, []).append(duration)
    exception_types: Counter[str] = Counter(kind for _, _, kind in exception_rows)
    exception_routes: Counter[str] = Counter(route for _, route, _ in exception_rows)

    route_rows = [
        {
            "route": route,
            "requests": count,
            "server_errors": route_errors.get(route, 0),
            "p95_ms": round(_percentile(slowest.get(route, []), 0.95) * 1_000, 1),
        }
        for route, count in by_route.most_common(20)
    ]
    return {
        "ok": True,
        "process_started_at": _started_at,
        "window_seconds": window_seconds,
        "sample_capacity": _MAX_SAMPLES,
        "sample_count": requests,
        "requests": requests,
        "server_errors": server_errors,
        "client_errors": client_errors,
        "exceptions": len(exception_rows),
        "exception_types": [
            {"type": kind, "count": count}
            for kind, count in exception_types.most_common(20)
        ],
        "exception_routes": [
            {"route": route, "count": count}
            for route, count in exception_routes.most_common(20)
        ],
        "error_rate_percent": round(server_errors / requests * 100, 3) if requests else 0.0,
        "p50_ms": round(_percentile(durations, 0.50) * 1_000, 1),
        "p95_ms": round(_percentile(durations, 0.95) * 1_000, 1),
        "p99_ms": round(_percentile(durations, 0.99) * 1_000, 1),
        "routes": route_rows,
        "scope": "current_api_worker",
    }
