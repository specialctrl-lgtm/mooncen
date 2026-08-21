from __future__ import annotations

import copy
from datetime import datetime, timezone

import pytest

from ops_agent import crawler_control_finalizer, crawler_worker
from ops_agent.crawler_registry import CrawlerProviderExecution


PROVIDER = "MUNI_EXAMPLE"
EXECUTION_PROVIDER = "EXPERIENCE_TARGETS"


def _parameters() -> dict[str, object]:
    return {
        "scope": "provider",
        "run_mode": "apply",
        "provider": PROVIDER,
        "execution_provider": EXECUTION_PROVIDER,
        "allowed_output_providers": [PROVIDER],
        "scheduled_providers": [PROVIDER, "HOMEPLUS"],
        "concurrency": 1,
    }


def _aggregate_result() -> dict[str, object]:
    return {
        "providers_requested": [EXECUTION_PROVIDER],
        "provider_results": [
            {
                "provider": EXECUTION_PROVIDER,
                "success": True,
                "exit_code": 0,
                "collected_courses": 3,
                "limit": None,
            }
        ],
        "providers_total": 1,
        "providers_completed": 1,
        "providers_failed": 0,
        "failed_providers": [],
        "concrete_provider_results": [
            {
                "provider": PROVIDER,
                "scheduled_owner": EXECUTION_PROVIDER,
                "success": True,
                "targets_total": 1,
                "targets_succeeded": 1,
                "collected_courses": 3,
                "saved_courses": 3,
            }
        ],
        "concrete_providers_total": 1,
        "concrete_providers_completed": 1,
        "concrete_providers_failed": 0,
        "course_provider_owners": {PROVIDER: EXECUTION_PROVIDER},
        "collection_outcome": "success",
        "collection_complete": True,
        "close_missing_enabled": True,
    }


def test_concrete_task_keeps_exact_aggregate_execution_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        crawler_worker,
        "resolve_crawler_provider_execution",
        lambda provider, _root, *, scheduled_provider=None: CrawlerProviderExecution(
            requested_provider=provider,
            scheduled_provider=str(scheduled_provider),
            environment={"CRAWLER_PROVIDERS": "MUNI_OTHER"},
        ),
    )

    command, environment = crawler_worker.build_crawler_execution(_parameters())

    assert command[command.index("--providers") + 1] == EXECUTION_PROVIDER
    assert environment["CRAWLER_PROVIDERS"] == "MUNI_OTHER"
    assert environment["CRAWL_SCHEDULED_TASK_PROVIDER"] == EXECUTION_PROVIDER
    assert environment["CRAWL_ALLOWED_OUTPUT_PROVIDERS_JSON"] == '["MUNI_EXAMPLE"]'


def test_aggregate_result_is_normalized_to_one_concrete_task_without_mutation() -> None:
    source = _aggregate_result()
    original = copy.deepcopy(source)

    normalized = crawler_worker._normalized_task_result(
        source,
        {"parameters": _parameters()},
    )

    assert source == original
    assert normalized["providers_requested"] == [PROVIDER]
    assert normalized["provider_results"][0]["provider"] == PROVIDER
    assert normalized["course_provider_owners"] == {PROVIDER: PROVIDER}
    assert normalized["concrete_provider_results"] == []
    assert normalized["concrete_providers_total"] == 0
    assert normalized["close_missing_enabled"] is False


@pytest.mark.parametrize(
    "mutate",
    [
        lambda result: result["course_provider_owners"].update(
            {"MUNI_OTHER": EXECUTION_PROVIDER}
        ),
        lambda result: result["concrete_provider_results"][0].update(
            {"provider": "MUNI_OTHER"}
        ),
        lambda result: result["concrete_provider_results"][0].update(
            {"scheduled_owner": "MUNICIPAL_RESERVATION_TARGETS"}
        ),
    ],
)
def test_aggregate_result_rejects_scope_or_owner_mismatch(mutate) -> None:
    result = _aggregate_result()
    mutate(result)

    with pytest.raises(crawler_worker.CrawlerTaskResultError, match="exact concrete"):
        crawler_worker._normalized_task_result(
            result,
            {"parameters": _parameters()},
        )


def test_normalized_concrete_task_is_publishable_by_existing_finalizer() -> None:
    task_result = crawler_worker._normalized_task_result(
        _aggregate_result(),
        {"parameters": _parameters()},
    )
    task = {
        "provider": PROVIDER,
        "allowed_output_providers": [PROVIDER],
        "required": True,
        "close_missing_eligible": False,
        "shard_index": 0,
        "shard_count": 1,
        "job_id": "00000000-0000-4000-8000-000000000101",
        "job_status": "success",
        "attempt_id": "00000000-0000-4000-8000-000000000102",
        "attempt_no": 1,
        "lease_epoch": 1,
        "attempt_status": "success",
        "attempt_contract_matches": True,
        "attempt_result_present": True,
        "attempt_evidence": {"result": {"task_result": task_result}},
    }
    batch = {
        "id": "00000000-0000-4000-8000-000000000100",
        "scheduled_slot": datetime(2026, 8, 10, tzinfo=timezone.utc),
        "code_version": "release-42",
        "artifact_digest": "a" * 64,
        "config_revision": "config-42",
    }

    result = crawler_control_finalizer._batch_result(
        batch,
        [task],
        crawler_control_finalizer.BatchDecision(
            "success",
            True,
            "all_required_tasks_succeeded",
        ),
        {PROVIDER: 3},
    )

    assert result["providers_requested"] == [PROVIDER]
    assert result["course_provider_owners"] == {PROVIDER: PROVIDER}
    assert result["provider_course_counts"] == {PROVIDER: 3}
    assert result["collection_complete"] is True
    assert result["close_missing_enabled"] is False
