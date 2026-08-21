from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ops_agent import crawler_release_reporter as reporter


REPORT_ID = "00000000-0000-0000-0000-000000000111"
ROLLOUT_ID = "00000000-0000-0000-0000-000000000222"


def _payload() -> dict:
    return {
        "schema_version": 1,
        "id": REPORT_ID,
        "environment": "production",
        "worker_key": "gen1crawler",
        "rollout_id": ROLLOUT_ID,
        "desired_generation": 42,
        "status": "ready",
        "code_version": "release-42",
        "artifact_digest": "a" * 64,
        "config_revision": "config-42",
        "health": {"healthy": True},
        "error_code": None,
        "error_message": "healthy",
        "reported_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
    }


def _write(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / f"{42:020d}-{REPORT_ID}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_report_parser_binds_spool_filename_and_worker_identity(tmp_path: Path) -> None:
    report = reporter.parse_report(
        _write(tmp_path, _payload()),
        environment="production",
        worker_key="gen1crawler",
    )

    assert report["id"] == REPORT_ID
    assert report["reported_at"].tzinfo is not None


def test_report_parser_rejects_filename_id_mismatch(tmp_path: Path) -> None:
    path = _write(tmp_path, _payload())
    wrong = tmp_path / f"{42:020d}-00000000-0000-0000-0000-000000000333.json"
    path.rename(wrong)

    with pytest.raises(reporter.ReleaseReportError, match="filename"):
        reporter.parse_report(wrong, environment="production", worker_key="gen1crawler")


def test_report_parser_rejects_cross_worker_spool(tmp_path: Path) -> None:
    with pytest.raises(reporter.ReleaseReportError, match="identity"):
        reporter.parse_report(
            _write(tmp_path, _payload()),
            environment="production",
            worker_key="otherworker",
        )


@pytest.mark.parametrize("health", [{"healthy": "unknown"}, {}, {"healthy": False}])
def test_report_parser_rejects_invalid_or_status_mismatched_health(
    tmp_path: Path, health: dict
) -> None:
    payload = {**_payload(), "health": health}
    with pytest.raises(reporter.ReleaseReportError, match="health"):
        reporter.parse_report(
            _write(tmp_path, payload),
            environment="production",
            worker_key="gen1crawler",
        )
