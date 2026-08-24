from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from sqlalchemy.exc import SQLAlchemyError

from backend.main import app
from backend.routers.crawler_analytics import crawler_analytics, crawler_analytics_batch_detail
from backend.services import crawler_analytics as analytics


def test_deployment_analytics_counts_verified_rollback_as_current_ready() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "backend/services/crawler_analytics.py"
    ).read_text(encoding="utf-8")
    assert "reported_status IN ('ready', 'rolled_back')" in source
    assert "reported_health->'healthy' = 'true'::jsonb" in source
    assert 'state = "ready" if healthy and fresh else "unhealthy"' in source
    assert "AS report_fresh" in source
    assert "AS agent_fresh" in source


class FakeMappingsResult:
    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = rows

    def mappings(self) -> FakeMappingsResult:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self.rows

    def first(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None

    def scalar(self) -> Any:
        if not self.rows:
            return None
        return next(iter(self.rows[0].values()))


class FakeAnalyticsSession:
    def __init__(self, *, include_marker: bool = True):
        self.include_marker = include_marker
        self.executed: list[tuple[str, dict[str, Any]]] = []
        self.rollback_count = 0
        self.now = datetime(2026, 8, 12, tzinfo=timezone.utc)
        self.rollout_id = str(uuid4())
        self.agent_id = str(uuid4())

    def rollback(self) -> None:
        self.rollback_count += 1

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        bound = dict(params or {})
        self.executed.append((sql, bound))

        if sql == "SET TRANSACTION READ ONLY":
            return FakeMappingsResult([])
        if "FROM information_schema.columns" in sql:
            rows = [
                {"table_name": table_name, "column_name": column_name}
                for table_name, columns in analytics._REQUIRED_COLUMNS.items()
                if self.include_marker or table_name != "ops_crawler_control_database_marker"
                for column_name in columns
            ]
            return FakeMappingsResult(rows)
        if "FROM ops_crawler_control_database_marker" in sql:
            return FakeMappingsResult([{"database_name": "mooncen_staging"}])
        if "current_crawler_api_environment() AS environment" in sql:
            return FakeMappingsResult([{"environment": "staging"}])
        if "FROM ops_crawler_release_rollouts r" in sql:
            return FakeMappingsResult(
                [
                    {
                        "id": self.rollout_id,
                        "rollout_epoch": 7,
                        "status": "running",
                        "requested_worker_count": 2,
                        "strategy": {"kind": "canary"},
                        "artifact_digest": "a" * 64,
                        "code_version": "crawler-v7",
                        "config_revision": "config-v3",
                        "size_bytes": 2048,
                        "key_id": "release-key-1",
                        "artifact_created_at": self.now,
                        "previous_artifact_digest": "b" * 64,
                        "created_at": self.now,
                        "started_at": self.now,
                        "finished_at": None,
                    }
                ]
            )
        if "WITH worker_state AS" in sql:
            return FakeMappingsResult(
                [
                    {
                        "desired_workers": 2,
                        "active_workers": 2,
                        "draining_workers": 0,
                        "disabled_workers": 0,
                        "unreported_workers": 0,
                        "ready_current_workers": 1,
                        "outdated_workers": 1,
                        "failed_workers": 0,
                        "drifted_workers": 0,
                    }
                ]
            )
        if "SELECT desired.worker_key" in sql:
            return FakeMappingsResult(
                [
                    {
                        "worker_key": "crawler-worker-1",
                        "agent_id": self.agent_id,
                        "rollout_id": self.rollout_id,
                        "generation": 7,
                        "desired_status": "active",
                        "cohort": "canary",
                        "desired_artifact_digest": "a" * 64,
                        "desired_code_version": "crawler-v7",
                        "desired_config_revision": "config-v3",
                        "not_before": self.now,
                        "updated_at": self.now,
                        "reported_rollout_id": self.rollout_id,
                        "reported_generation": 7,
                        "reported_status": "ready",
                        "reported_artifact_digest": "a" * 64,
                        "reported_code_version": "crawler-v7",
                        "reported_config_revision": "config-v3",
                        "health": {"healthy": True, "service": "healthy"},
                        "report_fresh": True,
                        "agent_fresh": True,
                        "error_code": None,
                        "error_message": None,
                        "reported_at": self.now,
                    },
                    {
                        "worker_key": "crawler-worker-2",
                        "agent_id": str(uuid4()),
                        "rollout_id": self.rollout_id,
                        "generation": 7,
                        "desired_status": "active",
                        "cohort": "stable",
                        "desired_artifact_digest": "a" * 64,
                        "desired_code_version": "crawler-v7",
                        "desired_config_revision": "config-v3",
                        "not_before": self.now,
                        "updated_at": self.now,
                        "reported_rollout_id": self.rollout_id,
                        "reported_generation": 6,
                        "reported_status": "ready",
                        "reported_artifact_digest": "b" * 64,
                        "reported_code_version": "crawler-v6",
                        "reported_config_revision": "config-v2",
                        "health": {},
                        "report_fresh": True,
                        "agent_fresh": True,
                        "error_code": None,
                        "error_message": None,
                        "reported_at": self.now,
                    },
                ]
            )
        if "FROM ops_crawler_runs run JOIN ops_jobs job" in sql and "GROUP BY run.provider" not in sql:
            return FakeMappingsResult(
                [
                    {
                        "run_count": 4,
                        "successful_runs": 3,
                        "partial_runs": 0,
                        "failed_runs": 1,
                        "in_progress_runs": 0,
                        "collected_count": 120,
                        "processed_count": 119,
                        "successful_item_count": 118,
                        "failed_item_count": 1,
                        "new_count": 20,
                        "updated_count": 10,
                        "deleted_candidate_count": 2,
                        "last_run_at": self.now,
                    }
                ]
            )
        if (
            "FROM ops_crawler_batches" in sql
            and "expected_task_count" in sql
            and "WITH recent_batches AS" not in sql
        ):
            return FakeMappingsResult(
                [
                    {
                        "batch_count": 1,
                        "successful_batches": 1,
                        "partial_batches": 0,
                        "failed_batches": 0,
                        "active_batches": 0,
                        "expected_tasks": 2,
                        "last_finished_at": self.now,
                        "last_scheduled_at": self.now,
                    }
                ]
            )
        if "FROM crawl_batches staging" in sql:
            return FakeMappingsResult(
                [
                    {
                        "sealed_batch_count": 1,
                        "total_courses": 120,
                        "valid_courses": 119,
                        "invalid_courses": 1,
                        "held_for_approval_batches": 1,
                        "promotion_eligible_batches": 0,
                    }
                ]
            )
        if "GROUP BY run.provider" in sql:
            return FakeMappingsResult(
                [
                    {
                        "provider": "MUNI_TEST",
                        "run_count": 4,
                        "successful_runs": 3,
                        "partial_runs": 0,
                        "failed_runs": 1,
                        "collected_count": 120,
                        "new_count": 20,
                        "updated_count": 10,
                        "failed_item_count": 1,
                        "success_rate": 75,
                        "last_run_at": self.now,
                        "total_providers": 1,
                    }
                ]
            )
        if "FROM ops_jobs" in sql and "oldest_ready_age_seconds" in sql:
            return FakeMappingsResult(
                [
                    {
                        "tracked_jobs": 8,
                        "ready_jobs": 2,
                        "delayed_jobs": 1,
                        "assigned_jobs": 0,
                        "running_jobs": 2,
                        "cancellation_requested_jobs": 0,
                        "expired_leases": 0,
                        "dead_lettered_jobs": 1,
                        "exhausted_failed_jobs": 1,
                        "oldest_ready_age_seconds": 30,
                    }
                ]
            )
        if "FROM ops_agents agent" in sql and "COUNT(*) AS worker_count" in sql:
            return FakeMappingsResult(
                [
                    {
                        "worker_count": 2,
                        "maintenance_workers": 0,
                        "disabled_workers": 0,
                        "stale_workers": 1,
                        "critical_workers": 0,
                        "warning_workers": 0,
                        "healthy_workers": 1,
                        "latest_heartbeat_at": self.now,
                    }
                ]
            )
        if "FROM ops_agents agent" in sql and "COUNT(*) OVER () AS total_workers" in sql:
            return FakeMappingsResult(
                [
                    {
                        "id": self.agent_id,
                        "name": "worker-1",
                        "hostname": "worker-1.internal",
                        "status": "healthy",
                        "maintenance_mode": False,
                        "last_seen_at": self.now,
                        "heartbeat_stale": False,
                        "total_workers": 2,
                    },
                    {
                        "id": str(uuid4()),
                        "name": "worker-2",
                        "hostname": "worker-2.internal",
                        "status": "healthy",
                        "maintenance_mode": False,
                        "last_seen_at": self.now,
                        "heartbeat_stale": True,
                        "total_workers": 2,
                    },
                ]
            )
        if "WITH exact_activity AS" in sql:
            return FakeMappingsResult(
                [
                    {
                        "rollout_id": self.rollout_id,
                        "generation": 7,
                        "providers": ["MUNI_TEST"],
                        "activity_started_at": self.now,
                        "activity_finished_at": self.now,
                        "average_score": 91.5,
                        "bad_courses": 1,
                        "incomplete_courses": 2,
                        "issue_count": 2,
                        "critical_issues": 0,
                        "blocked_sync_issues": 1,
                        "total_generations": 1,
                    }
                ]
            )
        if "FROM course_quality_score" in sql and "GROUP BY provider" not in sql:
            return FakeMappingsResult(
                [
                    {
                        "scored_courses": 120,
                        "average_score": 91.5,
                        "good_courses": 100,
                        "warning_courses": 19,
                        "bad_courses": 1,
                        "incomplete_courses": 2,
                        "stale_scores": 3,
                        "latest_checked_at": self.now,
                    }
                ]
            )
        if "FROM course_quality_score" in sql and "GROUP BY provider" in sql:
            return FakeMappingsResult(
                [
                    {
                        "provider": "MUNI_TEST",
                        "scored_courses": 120,
                        "average_score": 91.5,
                        "good_courses": 100,
                        "warning_courses": 19,
                        "bad_courses": 1,
                        "latest_checked_at": self.now,
                        "total_providers": 1,
                    }
                ]
            )
        if "FROM ops_quality_issues" in sql:
            return FakeMappingsResult(
                [
                    {
                        "issue_count": 2,
                        "active_issues": 1,
                        "active_critical_issues": 0,
                        "active_warning_issues": 1,
                        "blocked_sync_issues": 1,
                        "latest_detected_at": self.now,
                    }
                ]
            )
        if "WITH recent AS" in sql and "legacy_unattributed_attempts" in sql:
            return FakeMappingsResult(
                [
                    {
                        "total_attempts": 2,
                        "attributed_attempts": 2,
                        "legacy_unattributed_attempts": 0,
                        "rejected_mismatched_attempts": 0,
                        "validation_attributed_batches": 1,
                        "validation_legacy_excluded_batches": 0,
                        "validation_conflicting_excluded_batches": 0,
                        "validation_pending_or_partial_excluded_batches": 0,
                    }
                ]
            )
        if "WITH exact_attempts AS" in sql:
            return FakeMappingsResult(
                [
                    {
                        "rollout_id": self.rollout_id,
                        "generation": 7,
                        "generation_started_at": self.now,
                        "generation_finished_at": self.now,
                        "code_versions": ["crawler-v7"],
                        "attempt_count": 2,
                        "failed_attempts": 0,
                        "retried_tasks": 1,
                        "lease_lost_attempts": 1,
                        "duration_seconds": 42,
                        "collected_count": 120,
                        "new_count": 20,
                        "updated_count": 10,
                        "failed_item_count": 1,
                        "total_courses": 120,
                        "valid_courses": 119,
                        "invalid_courses": 1,
                        "total_generations": 1,
                    }
                ]
            )
        if "WITH recent_batches AS" in sql and "task_evidence AS" in sql:
            return FakeMappingsResult(
                [
                    {
                        "id": str(uuid4()),
                        "scheduled_slot": self.now,
                        "status": "success",
                        "expected_task_count": 1,
                        "code_version": "crawler-v7",
                        "artifact_digest": "a" * 64,
                        "config_revision": "config-v3",
                        "started_at": self.now,
                        "finished_at": self.now,
                        "created_at": self.now,
                        "task_count": 1,
                        "providers": ["MUNI_TEST"],
                        "latest_attempts": 1,
                        "legacy_unattributed_tasks": 0,
                        "attributed_tasks": 1,
                        "attributed_generations": 1,
                        "rollout_id": self.rollout_id,
                        "generation": 7,
                        "collected_count": 120,
                        "new_count": 20,
                        "updated_count": 10,
                        "failed_item_count": 1,
                        "attempt_count": 2,
                        "retry_attempts": 1,
                        "lease_lost_attempts": 1,
                        "duration_seconds": 42,
                        "total_courses": 120,
                        "valid_courses": 119,
                        "invalid_courses": 1,
                        "promotion_policy": "held",
                        "promotion_eligible": False,
                        "total_batches": 1,
                    }
                ]
            )
        raise AssertionError(f"Unexpected crawler analytics query: {sql}")


class FakeBatchDetailSession(FakeAnalyticsSession):
    def __init__(self, *, legacy: bool = False):
        super().__init__()
        self.batch_id = str(uuid4())
        self.attempt_id = str(uuid4())
        self.legacy = legacy

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        bound = dict(params or {})
        if "WHERE batch.id = CAST(:batch_id AS uuid)" in sql:
            self.executed.append((sql, bound))
            return FakeMappingsResult(
                [
                    {
                        "id": self.batch_id,
                        "environment": "staging",
                        "scheduled_slot": self.now,
                        "status": "success",
                        "expected_task_count": 1,
                        "code_version": "crawler-v7",
                        "artifact_digest": "a" * 64,
                        "config_revision": "config-v3",
                        "started_at": self.now,
                        "finished_at": self.now,
                        "created_at": self.now,
                        "duration_seconds": 42,
                        "total_courses": 120,
                        "valid_courses": 119,
                        "invalid_courses": 1,
                        "validation_result": {"promotion_eligible": False, "secret": "drop"},
                    }
                ]
            )
        if sql.startswith("SELECT COUNT(*) FROM ops_crawler_batch_tasks"):
            self.executed.append((sql, bound))
            return FakeMappingsResult([{"count": 1}])
        if "known_identity_count" in sql and "FROM ops_crawler_batch_tasks task" in sql:
            self.executed.append((sql, bound))
            return FakeMappingsResult(
                [
                    {
                        "task_key": "MUNI_TEST:0",
                        "provider": "MUNI_TEST",
                        "allowed_output_providers": ["MUNI_TEST"],
                        "required": True,
                        "shard_index": 0,
                        "shard_count": 1,
                        "job_id": str(uuid4()),
                        "job_status": "success",
                        "retry_count": 1,
                        "queued_at": self.now,
                        "job_started_at": self.now,
                        "job_finished_at": self.now,
                        "run_id": str(uuid4()),
                        "run_status": "success",
                        "total_count": 120,
                        "processed_count": 120,
                        "success_count": 119,
                        "failed_count": 1,
                        "new_count": 20,
                        "updated_count": 10,
                        "attempt_id": self.attempt_id,
                        "attempt_no": 2,
                        "lease_epoch": 2,
                        "agent_id": self.agent_id,
                        "attempt_status": "success",
                        "worker_code_version": "crawler-v7",
                        "artifact_digest": "a" * 64,
                        "config_revision": "config-v3",
                        "attempt_started_at": self.now,
                        "attempt_finished_at": self.now,
                        "exit_code": 0,
                        "error_code": None,
                        "attempt_duration_seconds": 42,
                        "attempt_count": 2,
                        "lease_lost_attempts": 1,
                        "known_identity_count": 0 if self.legacy else 1,
                        "rollout_id": None if self.legacy else self.rollout_id,
                        "generation": None if self.legacy else 7,
                        "worker_key": None if self.legacy else "crawler-worker-1",
                    }
                ]
            )
        return super().execute(statement, params)


class OneSectionFailureSession(FakeAnalyticsSession):
    def __init__(self):
        super().__init__()
        self.failed = False

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        if "FROM ops_crawler_release_rollouts r" in sql and not self.failed:
            self.failed = True
            self.executed.append((sql, dict(params or {})))
            raise SQLAlchemyError("simulated deployment analytics failure")
        return super().execute(statement, params)


class MissingCorrelationSchemaSession(FakeAnalyticsSession):
    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        if "FROM information_schema.columns" in sql:
            bound = dict(params or {})
            self.executed.append((sql, bound))
            return FakeMappingsResult(
                [
                    {"table_name": table_name, "column_name": column_name}
                    for table_name, columns in analytics._REQUIRED_COLUMNS.items()
                    if table_name != "ops_crawler_rollout_worker_snapshots"
                    for column_name in columns
                ]
            )
        return super().execute(statement, params)


def _build(db, **overrides):
    arguments = {
        "environment": "staging",
        "window_hours": 24,
        "provider_limit": 50,
        "worker_limit": 100,
        "correlation_limit": 25,
        "heartbeat_timeout_seconds": 180,
        **overrides,
    }
    return analytics.build_crawler_analytics(db, **arguments)


def test_crawler_analytics_requires_dedicated_control_database_pool() -> None:
    payload = _build(None)

    assert payload["schema_version"] == 2
    assert payload["available"] is False
    assert payload["complete"] is False
    assert payload["partial"] is False
    assert payload["reasons"] == [
        {
            "code": "crawler_control_database_not_configured",
            "message": "The dedicated crawler-control read-only API pool is not configured",
            "required_connection": "dedicated_crawler_analytics_readonly_pool",
        }
    ]
    assert all(payload[name]["available"] is False for name in analytics_section_names())


def test_deployment_analytics_never_requires_ungranted_desired_state_columns() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "backend/services/crawler_analytics.py"
    ).read_text(encoding="utf-8")

    assert "SELECT desired.*" not in source
    assert "desired.created_at" not in source


def test_crawler_analytics_rejects_unmarked_primary_database() -> None:
    db = FakeAnalyticsSession(include_marker=False)

    payload = _build(db)

    assert payload["available"] is False
    assert payload["reasons"][0]["code"] == "crawler_control_database_unavailable"
    assert payload["reasons"][0]["required_connection"] == "dedicated_crawler_analytics_readonly_pool"
    assert len(db.executed) == 2
    assert db.executed[0][0] == "SET TRANSACTION READ ONLY"
    assert "information_schema.columns" in db.executed[1][0]


def test_missing_correlation_schema_is_incomplete_not_a_zero_snapshot(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "staging")
    payload = _build(MissingCorrelationSchemaSession())

    assert payload["available"] is True
    assert payload["partial"] is True
    correlations = payload["correlations"]
    assert correlations["available"] is False
    assert correlations["has_data"] is False
    assert correlations["components"]["batches"]["has_data"] is None
    assert correlations["components"]["batches"]["items"] is None
    assert correlations["components"]["batches"]["total"] is None
    assert correlations["components"]["batches"]["reasons"][0] == {
        "code": "missing_table",
        "relation": "public.ops_crawler_rollout_worker_snapshots",
    }


def test_crawler_analytics_returns_central_release_collection_quality_and_health(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "staging")
    db = FakeAnalyticsSession()

    payload = _build(db)

    assert payload["available"] is True
    assert payload["schema_version"] == 2
    assert payload["complete"] is False
    assert payload["partial"] is True
    assert payload["environment"] == "staging"
    assert payload["data_source"]["authority_verified"] is True
    assert payload["deployment"]["components"]["rollout"]["latest"]["code_version"] == "crawler-v7"
    versions = payload["deployment"]["components"]["versions"]
    assert versions["summary"]["ready_current_workers"] == 1
    assert [item["version_state"] for item in versions["items"]] == ["ready", "outdated"]
    run_totals = payload["collection"]["components"]["runs"]["totals"]
    assert run_totals["new_count"] == 20
    assert run_totals["updated_count"] == 10
    assert run_totals["failed_item_count"] == 1
    assert payload["collection"]["components"]["validation"]["totals"]["valid_courses"] == 119
    assert payload["providers"]["components"]["collection"]["items"][0]["success_rate"] == 75.0
    assert payload["quality"]["components"]["scores"]["summary"]["average_score"] == 91.5
    assert payload["quality"]["components"]["issues"]["summary"]["blocked_sync_issues"] == 1
    assert payload["workers"]["components"]["health"]["summary"]["stale_workers"] == 1
    assert payload["queue"]["components"]["health"]["metrics"]["dead_lettered_jobs"] == 1
    correlations = payload["correlations"]["components"]
    assert correlations["generations"]["available"] is True
    assert correlations["quality"]["available"] is False
    assert correlations["generations"]["items"][0]["generation"] == 7
    assert correlations["generations"]["items"][0]["attempt_count"] == 2
    assert correlations["quality"]["reasons"][-1]["code"] == (
        "generation_quality_attribution_unavailable"
    )
    assert correlations["batches"]["items"][0]["attribution_state"] == "attributed"
    assert correlations["batches"]["items"][0]["generation"] == 7
    assert correlations["attribution"]["summary"]["legacy_unattributed_attempts"] == 0

    environment_queries = [params for _sql, params in db.executed if "environment" in params]
    assert environment_queries
    assert all(params["environment"] == "staging" for params in environment_queries)
    assert any(params.get("provider_limit") == 50 for params in environment_queries)
    assert any(params.get("worker_limit") == 100 for params in environment_queries)


def test_quality_does_not_mix_current_database_data_into_another_environment(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    db = FakeAnalyticsSession()

    quality = analytics._quality(
        db,
        analytics._schema_inventory(db),
        environment="production",
        provider_limit=50,
    )

    assert quality["available"] is False
    assert quality["has_data"] is False
    assert quality["components"]["scores"]["summary"] is None
    assert quality["reasons"][0]["code"] == "environment_dimension_unavailable"
    assert quality["reasons"][0]["supported_environment"] == "staging"
    assert quality["reasons"][0]["data_scope"] == "shared_staging"


def test_section_query_failure_reestablishes_read_only_before_next_section(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "staging")
    db = OneSectionFailureSession()

    payload = _build(db)

    failed_index = next(
        index for index, (sql, _params) in enumerate(db.executed) if "FROM ops_crawler_release_rollouts r" in sql
    )
    assert db.executed[failed_index + 1][0] == "SET TRANSACTION READ ONLY"
    assert "FROM ops_crawler_runs run JOIN ops_jobs job" in db.executed[failed_index + 2][0]
    assert payload["deployment"]["available"] is False
    assert payload["collection"]["available"] is True
    assert payload["partial"] is True
    assert db.rollback_count >= 2


def test_crawler_analytics_route_is_bounded_and_uses_ops_viewer_security(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "staging")
    route = next(
        route
        for route in analytics_router_routes()
        if isinstance(route, APIRoute) and route.path == "/api/ops/crawlers/analytics"
    )

    assert route.methods == {"GET"}
    dependency_names = {dependency.call.__name__ for dependency in route.dependant.dependencies}
    assert "require_ops_viewer" in dependency_names
    assert "rate_limit_ops_crawler_analytics" in dependency_names
    assert "get_crawler_control_db" in dependency_names

    payload = crawler_analytics(
        environment="staging",
        window_hours=1,
        provider_limit=1,
        worker_limit=1,
        correlation_limit=1,
        heartbeat_timeout_seconds=30,
        db=None,
    )
    assert payload["limits"] == {"providers": 1, "workers": 1, "correlations": 1}
    assert payload["heartbeat_timeout_seconds"] == 30


def test_crawler_analytics_rejects_cross_environment_reads(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")

    with pytest.raises(HTTPException) as rejected:
        crawler_analytics(
            environment="staging",
            window_hours=24,
            provider_limit=50,
            worker_limit=100,
            correlation_limit=25,
            heartbeat_timeout_seconds=180,
            db=None,
        )

    assert rejected.value.status_code == 409
    assert rejected.value.detail["code"] == "crawler_analytics_environment_mismatch"


def test_batch_detail_uses_exact_immutable_release_generation_evidence(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "staging")
    db = FakeBatchDetailSession()

    payload = analytics.build_crawler_batch_detail(
        db,
        environment="staging",
        batch_id=db.batch_id,
        task_limit=10,
        task_offset=0,
    )

    assert payload["available"] is True
    assert payload["item"]["validation"] == {"promotion_eligible": False}
    task = payload["tasks"][0]
    assert task["attribution_state"] == "attributed"
    assert task["rollout_id"] == db.rollout_id
    assert task["generation"] == 7
    assert task["worker_key"] == "crawler-worker-1"
    assert payload["attribution"]["available"] is True
    assert payload["attribution"]["reasons"] == []
    query_source = " ".join(sql for sql, _params in db.executed)
    assert "attempt.*" not in query_source
    assert "lease_token" not in query_source
    assert "error_message" not in query_source
    assert "metrics" not in query_source


def test_batch_detail_keeps_legacy_null_attempt_explicitly_unattributed(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "staging")
    db = FakeBatchDetailSession(legacy=True)

    payload = analytics.build_crawler_batch_detail(
        db,
        environment="staging",
        batch_id=db.batch_id,
        task_limit=10,
        task_offset=0,
    )

    task = payload["tasks"][0]
    assert task["attribution_state"] == "legacy_unattributed"
    assert task["rollout_id"] is None
    assert task["generation"] is None
    assert payload["attribution"]["available"] is True
    assert payload["attribution"]["summary"]["legacy_unattributed_tasks"] == 1
    assert payload["attribution"]["reasons"][0]["code"] == (
        "generation_attribution_evidence_unavailable"
    )


def test_batch_detail_route_is_bounded_and_rejects_cross_environment(monkeypatch) -> None:
    route = next(
        route
        for route in analytics_router_routes()
        if isinstance(route, APIRoute)
        and route.path == "/api/ops/crawlers/analytics/batches/{batch_id}"
    )
    assert route.methods == {"GET"}
    dependency_names = {dependency.call.__name__ for dependency in route.dependant.dependencies}
    assert "require_ops_viewer" in dependency_names
    assert "rate_limit_ops_crawler_analytics" in dependency_names
    assert "get_crawler_control_db" in dependency_names

    monkeypatch.setenv("ENVIRONMENT", "production")
    unavailable = crawler_analytics_batch_detail(
        batch_id=uuid4(),
        environment="production",
        task_limit=10,
        task_offset=0,
        db=None,
    )
    assert unavailable["available"] is False
    assert unavailable["item"] is None
    assert unavailable["tasks"] is None
    assert unavailable["total_tasks"] is None

    with pytest.raises(HTTPException) as rejected:
        crawler_analytics_batch_detail(
            batch_id=uuid4(),
            environment="staging",
            task_limit=10,
            task_offset=0,
            db=None,
        )
    assert rejected.value.status_code == 409


def test_correlation_source_never_uses_snapshot_timestamp_as_effective_boundary() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "backend/services/crawler_analytics.py"
    ).read_text(encoding="utf-8")
    assert "point.created_at <= attempt.started_at" not in source
    assert "point.created_at <= ranked.started_at" not in source
    assert "point.created_at <= latest.started_at" not in source


def analytics_section_names() -> tuple[str, ...]:
    return "deployment", "collection", "providers", "quality", "workers", "queue", "correlations"


def analytics_router_routes() -> list[Any]:
    included = next(
        item
        for item in app.routes
        if getattr(item, "original_router", None) is not None
        and any(
            getattr(route, "path", None) == "/api/ops/crawlers/analytics"
            for route in item.original_router.routes
        )
    )
    return list(included.original_router.routes)
