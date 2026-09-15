from __future__ import annotations

import json
import os

from tools import crawler_runtime_summary


def test_summary_reports_bounded_live_provider_failures(tmp_path, monkeypatch) -> None:
    releases = tmp_path / "releases"
    release = releases / "commit"
    logs = release / "logs"
    logs.mkdir(parents=True)
    release.chmod(0o755)
    active = tmp_path / "current"
    active.symlink_to(release)
    (logs / "crawler_progress.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "failed",
                "run_id": "batch-1",
                "providers": [
                    {
                        "provider": "homeplus",
                        "state": "failed",
                        "exit_code": 1,
                        "error": "selector changed",
                    },
                    {"provider": "emart", "state": "success", "total": 12},
                ],
            }
        ),
        encoding="utf-8",
    )
    (logs / "crawler_cycle_state.json").write_text(
        json.dumps({"schema_version": 1, "crawl_batch_id": "batch-1"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(crawler_runtime_summary, "APP_LINK", active)
    monkeypatch.setattr(crawler_runtime_summary, "RELEASES_ROOT", releases)
    monkeypatch.setattr(crawler_runtime_summary, "EXPECTED_RELEASE_UID", os.getuid())

    summary = crawler_runtime_summary.build_summary()

    assert summary["crawl_batch_id"] == "batch-1"
    assert summary["total"] == 2
    assert summary["success"] == 1
    assert summary["failed"] == 1
    assert summary["providers"][0] == {
        "provider": "HOMEPLUS",
        "state": "failed",
        "exit_code": 1,
        "error_type": None,
        "error_message": "selector changed",
        "started_at": None,
        "finished_at": None,
        "total": None,
    }
