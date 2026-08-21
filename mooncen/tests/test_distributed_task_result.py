from __future__ import annotations

import json
from pathlib import Path

import pytest

import run_crawlers
from ops_agent import crawler_worker


JOB_ID = "00000000-0000-0000-0000-000000000101"
TOKEN = "00000000-0000-0000-0000-000000000202"
BATCH_ID = "00000000-0000-0000-0000-000000000303"
AGENT_ID = "00000000-0000-0000-0000-000000000404"


def _job() -> dict:
    return {
        "id": JOB_ID,
        "lease_token": TOKEN,
        "lease_epoch": 7,
        "attempt_no": 1,
        "agent_id": AGENT_ID,
        "parameters": {"provider": "HOMEPLUS", "batch_id": BATCH_ID},
    }


def _result() -> dict:
    return {
        "providers_requested": ["HOMEPLUS"],
        "provider_results": [
            {"provider": "HOMEPLUS", "success": True, "exit_code": 0, "collected_courses": 2, "limit": None}
        ],
        "concrete_provider_results": [],
        "concrete_providers_completed": 0,
        "concrete_providers_failed": 0,
        "concrete_providers_total": 0,
        "collection_outcome": "success",
        "providers_completed": 1,
        "providers_failed": 0,
        "providers_total": 1,
        "failed_providers": [],
        "course_provider_owners": {"HOMEPLUS": "HOMEPLUS"},
        "limit": None,
        "branch_code": None,
        "branch_name": None,
        "close_missing_enabled": True,
    }


def _environment(monkeypatch: pytest.MonkeyPatch, directory: Path, destination: Path) -> None:
    monkeypatch.setenv("CRAWL_TASK_RESULT_DIR", str(directory))
    monkeypatch.setenv("CRAWL_TASK_RESULT_PATH", str(destination))
    monkeypatch.setenv("CRAWL_JOB_ID", JOB_ID)
    monkeypatch.setenv("CRAWL_LEASE_TOKEN", TOKEN)
    monkeypatch.setenv("CRAWL_LEASE_EPOCH", "7")
    monkeypatch.setenv("CRAWL_ATTEMPT_NO", "1")


def test_child_result_is_bound_to_job_lease_attempt_and_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / f"{JOB_ID}-{TOKEN}.json"
    _environment(monkeypatch, tmp_path, destination)

    run_crawlers.publish_distributed_task_result(BATCH_ID, _result())
    loaded = crawler_worker._load_task_result(destination, _job(), BATCH_ID)

    assert loaded == _result()


def test_worker_rejects_result_from_another_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / f"{JOB_ID}-{TOKEN}.json"
    _environment(monkeypatch, tmp_path, destination)
    run_crawlers.publish_distributed_task_result(BATCH_ID, _result())
    payload = json.loads(destination.read_text(encoding="utf-8"))
    payload["lease_epoch"] = 8
    destination.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(crawler_worker.CrawlerTaskResultError, match="fence identity"):
        crawler_worker._load_task_result(destination, _job(), BATCH_ID)


def test_child_refuses_to_overwrite_existing_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / f"{JOB_ID}-{TOKEN}.json"
    destination.write_text("existing", encoding="utf-8")
    _environment(monkeypatch, tmp_path, destination)

    with pytest.raises(ValueError, match="already exists"):
        run_crawlers.publish_distributed_task_result(BATCH_ID, _result())


def test_distributed_task_uses_scheduler_frozen_provider_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CRAWL_SCHEDULED_TASK_PROVIDER", "EXPERIENCE_TARGETS")
    monkeypatch.setenv(
        "CRAWL_ALLOWED_OUTPUT_PROVIDERS_JSON",
        '["MUNI_A","MUNI_B"]',
    )

    owners = run_crawlers.distributed_course_provider_owners(
        ["EXPERIENCE_TARGETS"]
    )

    assert owners == {
        "MUNI_A": "EXPERIENCE_TARGETS",
        "MUNI_B": "EXPERIENCE_TARGETS",
    }


def test_worker_passes_full_schedule_and_exact_output_scope_to_aggregate() -> None:
    command, environment = crawler_worker.build_crawler_execution(
        {
            "scope": "provider",
            "provider": "EXPERIENCE_TARGETS",
            "run_mode": "apply",
            "concurrency": 1,
            "allowed_output_providers": ["MUNI_A", "MUNI_B"],
            "scheduled_providers": ["HOMEPLUS", "EXPERIENCE_TARGETS"],
        }
    )

    assert command[command.index("--providers") + 1] == "EXPERIENCE_TARGETS"
    assert environment["CRAWLER_PROVIDERS"] == "HOMEPLUS EXPERIENCE_TARGETS"
    assert json.loads(environment["CRAWL_ALLOWED_OUTPUT_PROVIDERS_JSON"]) == [
        "MUNI_A",
        "MUNI_B",
    ]
