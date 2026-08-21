from __future__ import annotations

from backend import observability


def test_normalized_route_bounds_dynamic_cardinality() -> None:
    assert observability.normalized_route("/api/courses/123") == "/api/courses/{id}"
    assert (
        observability.normalized_route("/api/courses/123e4567-e89b-12d3-a456-426614174000")
        == "/api/courses/{id}"
    )
    assert observability.normalized_route("/api/courses/search") == "/api/courses/search"


def test_runtime_metrics_reports_error_rate_and_latency() -> None:
    with observability._lock:
        observability._samples.clear()
        observability._exception_samples.clear()
    observability.record_request("/api/courses/123", 200, 0.010)
    observability.record_request("/api/courses/456", 503, 0.250)
    observability.record_request("/health", 503, 10.0)

    metrics = observability.runtime_metrics(900)

    assert metrics["requests"] == 2
    assert metrics["server_errors"] == 1
    assert metrics["error_rate_percent"] == 50.0
    assert metrics["p95_ms"] == 250.0
    assert metrics["routes"] == [
        {
            "route": "/api/courses/{id}",
            "requests": 2,
            "server_errors": 1,
            "p95_ms": 250.0,
        }
    ]


def test_runtime_metrics_classifies_exceptions_without_retaining_messages() -> None:
    with observability._lock:
        observability._samples.clear()
        observability._exception_samples.clear()

    secret_message = "database-password-that-must-not-be-retained"
    observability.record_exception("/api/courses/123", RuntimeError(secret_message))
    observability.record_exception("/api/courses/456", ValueError("bad input"))
    metrics = observability.runtime_metrics(900)

    assert metrics["exceptions"] == 2
    assert metrics["exception_types"] == [
        {"type": "RuntimeError", "count": 1},
        {"type": "ValueError", "count": 1},
    ]
    assert metrics["exception_routes"] == [
        {"route": "/api/courses/{id}", "count": 2}
    ]
    assert secret_message not in repr(metrics)


def test_metrics_polling_does_not_distort_application_samples() -> None:
    with observability._lock:
        observability._samples.clear()
        observability._exception_samples.clear()

    observability.record_request("/api/ops/runtime-metrics", 200, 0.5)

    assert observability.runtime_metrics(900)["requests"] == 0
