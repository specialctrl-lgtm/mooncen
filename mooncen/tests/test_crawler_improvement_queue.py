from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from backend.database import get_db
from backend.routers import auth, ops_v2


NOW = datetime(2026, 8, 15, 6, 0, tzinfo=timezone.utc)


def _patch_sources(
    monkeypatch: pytest.MonkeyPatch,
    *,
    available: set[str],
    courses: list[dict[str, Any]] | None = None,
    ops_runs: list[dict[str, Any]] | None = None,
    legacy_runs: list[dict[str, Any]] | None = None,
    scores: list[dict[str, Any]] | None = None,
    issues: list[dict[str, Any]] | None = None,
) -> None:
    monkeypatch.setattr(
        ops_v2,
        "table_exists",
        lambda _db, table_name: table_name in available,
    )
    monkeypatch.setattr(ops_v2, "_improvement_course_rows", lambda _db: courses or [])
    monkeypatch.setattr(
        ops_v2,
        "_improvement_ops_run_rows",
        lambda _db, *, jobs_available: ops_runs or [],
    )
    monkeypatch.setattr(
        ops_v2,
        "_improvement_legacy_run_rows",
        lambda _db: legacy_runs or [],
    )
    monkeypatch.setattr(
        ops_v2,
        "_improvement_quality_score_rows",
        lambda _db, *, courses_available: scores or [],
    )
    monkeypatch.setattr(
        ops_v2,
        "_improvement_quality_issue_rows",
        lambda _db: issues or [],
    )
    monkeypatch.setattr(ops_v2, "reviewed_crawler_providers", lambda: frozenset())


def _run_row(
    provider: str,
    *,
    status: str,
    age_minutes: int,
    rank: int,
    total: int,
    code: str | None = None,
    message: str | None = None,
    source: str = "ops_crawler_runs",
    last_success_at: datetime | None = None,
) -> dict[str, Any]:
    return {
        "run_id": f"{source}-{provider}-{rank}",
        "provider": provider,
        "status": status,
        "run_at": NOW - timedelta(minutes=age_minutes),
        "raw_error_code": code,
        "raw_error_message": message,
        "history_rank": rank,
        "source_total_runs": total,
        "source_last_success_at": last_success_at,
        "run_source": source,
    }


def test_improvement_queue_route_is_viewer_authenticated_and_limit_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = next(
        route
        for route in ops_v2.router.routes
        if isinstance(route, APIRoute)
        and route.path == "/api/ops/crawlers/improvement-queue"
    )
    dependency_names = {
        getattr(dependency.call, "__name__", "")
        for dependency in route.dependant.dependencies
    }
    assert route.methods == {"GET"}
    assert "require_ops_viewer" in dependency_names

    _patch_sources(monkeypatch, available=set())
    app = FastAPI()
    app.include_router(ops_v2.router)
    app.dependency_overrides[auth.require_ops_viewer] = lambda: object()
    app.dependency_overrides[get_db] = lambda: object()
    client = TestClient(app, raise_server_exceptions=False)

    assert client.get("/api/ops/crawlers/improvement-queue?limit=0").status_code == 422
    assert client.get("/api/ops/crawlers/improvement-queue?limit=501").status_code == 422
    response = client.get("/api/ops/crawlers/improvement-queue?limit=1")
    assert response.status_code == 200
    assert response.json()["schema_version"] == 1


def test_missing_sources_stay_null_and_synthetic_providers_are_excluded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_sources(
        monkeypatch,
        available={"courses"},
        courses=[
            {
                "provider": "  REAL_PROVIDER  ",
                "active_course_count": 12,
                "stale_48h_count": 3,
                "stale_7d_count": 1,
            },
            {"provider": "unknown", "active_course_count": 99},
            {"provider": "   ", "active_course_count": 99},
        ],
    )

    payload = ops_v2.crawler_improvement_queue(limit=100, db=object())

    assert payload["available"] is True
    assert payload["complete"] is False
    assert payload["sources"] == {
        "runs": False,
        "freshness": True,
        "quality_scores": False,
        "quality_issues": False,
    }
    assert len(payload["items"]) == 1
    item = payload["items"][0]
    assert item["provider"] == "REAL_PROVIDER"
    assert item["active_course_count"] == 12
    assert item["stale_48h_count"] == 3
    assert item["stale_7d_count"] == 1
    assert item["consecutive_failures"] is None
    assert item["quality_average_score"] is None
    assert item["quality_bad_count"] is None
    assert item["active_quality_issue_count"] is None
    assert item["evidence_complete"] is False


def test_missing_last_seen_is_unknown_not_stale_evidence() -> None:
    class EmptyMappings:
        @staticmethod
        def all():
            return []

    class EmptyResult:
        @staticmethod
        def mappings():
            return EmptyMappings()

    class FakeSession:
        sql = ""

        def execute(self, statement):
            self.sql = " ".join(str(statement).split())
            return EmptyResult()

    session = FakeSession()
    assert ops_v2._improvement_course_rows(session) == []  # type: ignore[arg-type]
    assert "last_seen_at IS NOT NULL AND last_seen_at <" in session.sql
    assert "last_seen_at IS NULL ) AS freshness_unknown_count" in session.sql
    assert "last_seen_at IS NULL OR last_seen_at <" not in session.sql


def test_full_queue_scores_sorts_and_never_returns_raw_error_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = "ALPHA & SEOUL"
    last_success = NOW - timedelta(days=2)
    ops_runs = [
        _run_row(
            provider,
            status="failed",
            age_minutes=1,
            rank=1,
            total=4,
            code="selector_error",
            message="secret-token=must-not-leak source structure changed",
            last_success_at=last_success,
        ),
        _run_row(
            provider,
            status="failed",
            age_minutes=2,
            rank=2,
            total=4,
            code="validation_error",
            last_success_at=last_success,
        ),
        _run_row(
            provider,
            status="failed",
            age_minutes=3,
            rank=3,
            total=4,
            code="parsing_error",
            last_success_at=last_success,
        ),
        _run_row(
            provider,
            status="success",
            age_minutes=4,
            rank=4,
            total=4,
            last_success_at=last_success,
        ),
    ]
    _patch_sources(
        monkeypatch,
        available={
            "courses",
            "ops_crawler_runs",
            "crawler_run_log",
            "course_quality_score",
            "ops_quality_issues",
            "ops_jobs",
        },
        courses=[
            {
                "provider": provider,
                "active_course_count": 1_500,
                "stale_48h_count": 200,
                "stale_7d_count": 100,
            },
            {
                "provider": "BETA",
                "active_course_count": 2,
                "stale_48h_count": 0,
                "stale_7d_count": 0,
            },
        ],
        ops_runs=ops_runs,
        scores=[
            {
                "provider": provider,
                "quality_average_score": 55,
                "quality_bad_count": 10,
            },
            {
                "provider": "BETA",
                "quality_average_score": 98,
                "quality_bad_count": 0,
            },
        ],
        issues=[
            {"provider": provider, "active_quality_issue_count": 4},
        ],
    )

    payload = ops_v2.crawler_improvement_queue(limit=500, db=object())

    assert payload["complete"] is True
    assert payload["total"] == 2
    assert payload["limit"] == 500
    assert payload["truncated"] is False
    assert [item["provider"] for item in payload["items"]] == [provider, "BETA"]
    item = payload["items"][0]
    assert item["priority"] == "P0"
    assert item["score"] == 100
    assert item["evidence_complete"] is True
    assert item["consecutive_failures"] == 3
    assert item["last_success_at"] == last_success
    assert item["error_category"] == "source_contract"
    assert item["error_code"] == "source_contract_changed"
    assert item["recommended_action"] == {
        "code": "inspect_parser",
        "label": "Parser 근거 확인",
        "href": "/crawler-studio?provider=ALPHA+%26+SEOUL",
    }
    assert item["score"] == min(100, sum(reason["points"] for reason in item["reasons"]))
    assert "raw_error_message" not in item
    assert "must-not-leak" not in repr(payload)
    assert payload["items"][1]["active_quality_issue_count"] == 0

    limited = ops_v2.crawler_improvement_queue(limit=1, db=object())
    assert limited["total"] == 2
    assert limited["limit"] == 1
    assert limited["truncated"] is True
    assert [item["provider"] for item in limited["items"]] == [provider]


@pytest.mark.parametrize(
    ("status", "code", "message", "expected"),
    [
        ("partial_success", None, None, "partial_failure"),
        ("failed", "collection_limit", "page cap reached", "collection_limit"),
        ("failed", "selector_error", None, "source_contract"),
        ("failed", "timeout", None, "timeout"),
        ("failed", "network_error", None, "transport"),
        ("failed", "scheduler_error", None, "scheduler"),
        ("failed", "unclassified", "opaque failure", "unknown"),
    ],
)
def test_error_categories_are_bounded_and_normalized(
    status: str,
    code: str | None,
    message: str | None,
    expected: str,
) -> None:
    category, normalized_code = ops_v2._normalized_improvement_error(
        status,
        code,
        message,
    )

    assert category == expected
    assert normalized_code is not None
    assert normalized_code != code or code in {
        "collection_limit",
        "timeout",
    }


def test_success_status_ignores_stale_error_text() -> None:
    assert ops_v2._normalized_improvement_error(
        "success",
        "selector_error",
        "old timeout details",
    ) == (None, None)


def test_exact_execution_target_without_run_gets_scheduler_recommendation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_sources(
        monkeypatch,
        available={
            "courses",
            "ops_crawler_runs",
            "course_quality_score",
            "ops_quality_issues",
        },
        courses=[
            {
                "provider": "DIRECT_TARGET",
                "active_course_count": 10,
                "stale_48h_count": 0,
                "stale_7d_count": 0,
            },
            {
                "provider": "AGGREGATE_CHILD",
                "active_course_count": 10,
                "stale_48h_count": 0,
                "stale_7d_count": 0,
            },
        ],
        scores=[
            {
                "provider": "DIRECT_TARGET",
                "quality_average_score": 100,
                "quality_bad_count": 0,
            },
            {
                "provider": "AGGREGATE_CHILD",
                "quality_average_score": 100,
                "quality_bad_count": 0,
            },
        ],
    )
    monkeypatch.setattr(
        ops_v2,
        "reviewed_crawler_providers",
        lambda: frozenset({"DIRECT_TARGET"}),
    )

    payload = ops_v2.crawler_improvement_queue(limit=100, db=object())
    items = {item["provider"]: item for item in payload["items"]}

    direct = items["DIRECT_TARGET"]
    assert direct["error_category"] == "scheduler"
    assert direct["error_code"] == "no_run_history"
    assert any(reason["code"] == "no_run_history" for reason in direct["reasons"])
    assert direct["recommended_action"]["href"] == "/crawlers?provider=DIRECT_TARGET"
    assert direct["evidence_complete"] is False
    child = items["AGGREGATE_CHILD"]
    assert child["error_category"] is None
    assert child["score"] == 0
    assert child["evidence_complete"] is False


def test_truncated_failure_streak_is_nullable_and_marks_evidence_incomplete() -> None:
    rows = [
        _run_row(
            "LIMITED",
            status="failed",
            age_minutes=index,
            rank=index,
            total=30,
            code="timeout",
        )
        for index in range(1, 26)
    ]

    evidence = ops_v2._improvement_run_evidence(rows)["LIMITED"]

    assert evidence["consecutive_failures"] is None
    assert evidence["failure_streak_lower_bound"] == 25
    assert evidence["run_history_complete"] is False
    assert evidence["error_category"] == "timeout"
