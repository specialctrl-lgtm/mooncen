from __future__ import annotations

from datetime import datetime, timezone

from psycopg2.extras import Json
import pytest

from ops_agent import crawler_control_finalizer as finalizer


def _task(status: str, *, attempt_status: str | None = None, matches: bool = True):
    return {
        "provider": "HOMEPLUS",
        "allowed_output_providers": ["HOMEPLUS"],
        "required": True,
        "job_id": "00000000-0000-0000-0000-000000000010",
        "attempt_id": "00000000-0000-0000-0000-000000000011",
        "job_status": status,
        "attempt_status": attempt_status,
        "attempt_contract_matches": matches,
        "attempt_result_present": attempt_status == "success",
        "attempt_no": 1,
        "lease_epoch": 1,
        "attempt_evidence": {
            "result": {
                "task_result": {
                "providers_requested": ["HOMEPLUS"],
                "provider_results": [
                    {
                        "provider": "HOMEPLUS",
                        "success": True,
                        "exit_code": 0,
                        "collected_courses": 12,
                        "limit": None,
                    }
                ],
                "concrete_provider_results": [],
                    "course_provider_owners": {"HOMEPLUS": "HOMEPLUS"},
                }
            }
        }
        if attempt_status == "success"
        else {},
    }


def test_batch_decision_waits_for_active_tasks_and_requires_fenced_attempt() -> None:
    assert finalizer.decide_batch([_task("running")], 1).status == "running"
    assert finalizer.decide_batch([_task("success", attempt_status="success")], 1).status == "success"
    mismatch = finalizer.decide_batch(
        [_task("success", attempt_status="success", matches=False)],
        1,
    )
    assert mismatch.terminal is True
    assert mismatch.status == "failed"


def test_batch_decision_is_fail_closed_on_missing_or_unknown_tasks() -> None:
    assert finalizer.decide_batch([], 1).reason == "task_count_mismatch"
    assert finalizer.decide_batch([_task("mystery")], 1).reason == "unknown_job_status"


def test_auto_promotion_policy_is_explicit_and_fail_closed(monkeypatch) -> None:
    monkeypatch.delenv(finalizer.AUTO_PROMOTION_ENV, raising=False)
    with pytest.raises(RuntimeError, match="explicitly false"):
        finalizer.auto_promotion_enabled("production")

    monkeypatch.setenv(finalizer.AUTO_PROMOTION_ENV, "false")
    assert finalizer.auto_promotion_enabled("staging") is False
    monkeypatch.setenv(finalizer.AUTO_PROMOTION_ENV, "true")
    with pytest.raises(RuntimeError, match="separate reviewed approver"):
        finalizer.auto_promotion_enabled("production")


def test_retried_batch_is_publishable_with_attempt_bound_snapshots() -> None:
    task = _task("success", attempt_status="success")
    task["attempt_no"] = 2

    decision = finalizer.decide_batch([task], 1)

    assert decision.status == "success"
    assert decision.reason == "all_required_tasks_succeeded"


class _LoadCursor:
    def __init__(self) -> None:
        self.sql = ""
        self.params = None

    def execute(self, sql: str, params=None) -> None:
        self.sql = sql
        self.params = params

    def fetchone(self):
        return None


def test_batch_loader_is_environment_scoped() -> None:
    cursor = _LoadCursor()

    assert finalizer._load_next_batch(cursor, "production") is None
    assert "WHERE environment = %s" in cursor.sql
    assert cursor.params == ("production",)


def test_batch_result_serializes_scheduled_slot_for_json_adapter() -> None:
    batch = {
        "id": "00000000-0000-0000-0000-000000000001",
        "scheduled_slot": datetime(2026, 8, 10, 13, 0, tzinfo=timezone.utc),
        "code_version": "release-42",
        "artifact_digest": "a" * 64,
        "config_revision": "b" * 64,
    }
    task = {
        **_task("success", attempt_status="success"),
        "result": {"return_code": 0},
        "close_missing_eligible": False,
    }
    result = finalizer._batch_result(
        batch,
        [task],
        finalizer.BatchDecision("success", True, "all_required_tasks_succeeded"),
        {"HOMEPLUS": 12},
    )

    assert result["scheduled_slot"] == "2026-08-10T13:00:00+00:00"
    assert result["collection_complete"] is True
    Json(result).getquoted()


def test_partial_aggregate_keeps_successful_concrete_evidence() -> None:
    direct = _task("success", attempt_status="success")
    aggregate = {
        **_task("partial_success", attempt_status="partial_success"),
        "provider": "EXPERIENCE_TARGETS",
        "allowed_output_providers": ["MUNI_A"],
        "job_id": "00000000-0000-0000-0000-000000000020",
        "attempt_id": "00000000-0000-0000-0000-000000000021",
        "attempt_result_present": True,
        "attempt_evidence": {
            "result": {
                "task_result": {
                    "provider_results": [
                        {
                            "provider": "EXPERIENCE_TARGETS",
                            "success": False,
                            "exit_code": 3,
                            "collected_courses": 3,
                            "limit": None,
                        }
                    ],
                    "course_provider_owners": {"MUNI_A": "EXPERIENCE_TARGETS"},
                    "concrete_provider_results": [
                        {
                            "provider": "MUNI_A",
                            "scheduled_owner": "EXPERIENCE_TARGETS",
                            "success": True,
                            "targets_total": 1,
                            "targets_succeeded": 1,
                            "collected_courses": 3,
                            "saved_courses": 3,
                        }
                    ],
                }
            }
        },
    }
    batch = {
        "id": "00000000-0000-0000-0000-000000000001",
        "scheduled_slot": datetime(2026, 8, 10, 13, 0, tzinfo=timezone.utc),
        "code_version": "release-42",
        "artifact_digest": "a" * 64,
        "config_revision": "b" * 64,
    }

    result = finalizer._batch_result(
        batch,
        [direct, aggregate],
        finalizer.BatchDecision("partial_success", True, "some_required_tasks_failed"),
        {"HOMEPLUS": 12, "MUNI_A": 3},
    )

    assert result["providers_completed"] == 1
    assert result["providers_failed"] == 1
    assert result["concrete_providers_completed"] == 1
    assert result["concrete_provider_results"][0]["provider"] == "MUNI_A"


class _Cursor:
    def __init__(self) -> None:
        self.rowcount = 1
        self.executed: list[tuple[str, object]] = []

    def execute(self, sql: str, params=None) -> None:
        self.executed.append((sql, params))

    def fetchone(self):
        sql = self.executed[-1][0]
        if "course_snapshots" in sql:
            return {"total": 0, "valid": 0}
        return {"total": 0}

    def fetchall(self):
        return []


def test_zero_snapshot_downgrades_success_before_terminal_publish() -> None:
    cursor = _Cursor()
    batch = {
        "id": "00000000-0000-0000-0000-000000000001",
        "scheduled_slot": datetime(2026, 8, 10, 13, 0, tzinfo=timezone.utc),
        "started_at": None,
        "code_version": "release-42",
        "artifact_digest": "a" * 64,
        "config_revision": "b" * 64,
    }
    task = {
        **_task("success", attempt_status="success"),
        "result": {"return_code": 0},
        "close_missing_eligible": False,
    }

    decision = finalizer._publish_staging_batch(
        cursor,
        batch,
        [task],
        finalizer.BatchDecision("success", True, "all_required_tasks_succeeded"),
    )

    assert decision.status == "failed"
    terminal = [params for sql, params in cursor.executed if "UPDATE ops_crawler_batches" in sql]
    assert terminal[-1][0] == "failed"
    sealed = [params for sql, params in cursor.executed if "INSERT INTO crawl_batches" in sql]
    result = sealed[-1][-1].adapted
    assert result["promotion_eligible"] is False
    assert result["promotion_policy"] == "held"
