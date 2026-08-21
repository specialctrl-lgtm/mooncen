import argparse
import json
import os
import sys
import time
from argparse import Namespace
from pathlib import Path

import pytest
import yaml

from tools import crawler_recursive_loop as loop


def _audit_args() -> Namespace:
    return Namespace(
        audit_no_compile=True,
        include_large_registries=False,
        providers=[],
        audit_timeout=5,
    )


def test_run_audit_reads_path_emitted_by_successful_process(tmp_path: Path, monkeypatch):
    report_path = tmp_path / "crawler_recursive_audit_1.json"
    report_path.write_text(json.dumps({"summary": {"syntax_errors": 0}}), encoding="utf-8")
    monkeypatch.setattr(loop, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(
        loop,
        "run_process",
        lambda _command, timeout: {
            "exit_code": 0,
            "output_tail": f"iteration=1 json={report_path} md={tmp_path / 'audit.md'}",
        },
    )

    result = loop.run_audit(_audit_args())

    assert result["summary"] == {"syntax_errors": 0}


def test_run_audit_does_not_reuse_stale_report(tmp_path: Path, monkeypatch):
    (tmp_path / "crawler_recursive_audit_latest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(loop, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(
        loop,
        "run_process",
        lambda _command, timeout: {"exit_code": 2, "output_tail": "audit failed"},
    )

    with pytest.raises(RuntimeError, match="audit failed"):
        loop.run_audit(_audit_args())


def test_corrupt_loop_state_fails_closed(tmp_path: Path):
    state_path = tmp_path / "state.json"
    state_path.write_text("{broken", encoding="utf-8")

    with pytest.raises(ValueError, match="--reset-state"):
        loop.load_state(state_path)


def test_numeric_arguments_have_hard_bounds():
    parse_timeout = loop.int_between(1, 60)

    assert parse_timeout("60") == 60
    with pytest.raises(argparse.ArgumentTypeError):
        parse_timeout("0")
    with pytest.raises(argparse.ArgumentTypeError):
        parse_timeout("61")


def test_strict_tls_failure_is_classified_as_external_access_error():
    assert loop.quality_status(
        1,
        {"error": "RequestException: Strict TLS request failed for https://example.com"},
    ) == "site_or_access_error"


def test_nonzero_process_exit_cannot_be_overridden_by_high_grade_report():
    assert loop.quality_status(
        1,
        {
            "provider": "SPOOFED_SUCCESS",
            "success": True,
            "collected": 10,
            "grade": "A",
            "fields": {"title": 10, "branch": 10, "raw_url": 10},
        },
    ) == "failed"


def test_timeout_terminates_descendant_processes(tmp_path: Path):
    marker = tmp_path / "descendant-survived.txt"
    child_code = f"import time; from pathlib import Path; time.sleep(1); Path({str(marker)!r}).write_text('alive')"
    parent_code = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        "time.sleep(30)"
    )

    result = loop.run_process([sys.executable, "-c", parent_code], timeout=0.2)
    time.sleep(1.2)

    assert result["timed_out"] is True
    assert not marker.exists()


def test_evidence_import_requires_exact_provider_and_recent_log(tmp_path: Path, monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    report_path = logs / "quality.yaml"
    report_path.write_text(
        yaml.safe_dump(
            {
                "reports": [
                    {
                        "provider": "EXACT_PROVIDER",
                        "success": True,
                        "collected": 1,
                        "fields": {
                            "title": 1,
                            "branch": 1,
                            "raw_url": 1,
                            "period": 1,
                            "schedule_raw": 1,
                            "fee": 1,
                            "status": 1,
                            "target": 1,
                            "description": 1,
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(loop, "ROOT", tmp_path)

    resolved = loop.resolve_evidence_report(report_path, max_age_hours=1)
    run = loop.run_from_evidence_report("EXACT_PROVIDER", resolved)

    assert run is not None
    assert run["analysis"]["status"] == "passed"
    assert loop.run_from_evidence_report("OTHER_PROVIDER", resolved) is None

    duplicate_document = yaml.safe_load(report_path.read_text(encoding="utf-8"))
    duplicate_document["reports"][0]["success"] = False
    report_path.write_text(yaml.safe_dump(duplicate_document), encoding="utf-8")
    failed_run = loop.run_from_evidence_report("EXACT_PROVIDER", resolved)
    assert failed_run is not None
    assert failed_run["analysis"]["status"] == "failed"

    duplicate_document["reports"][0]["success"] = True
    duplicate_document["reports"].append(dict(duplicate_document["reports"][0]))
    report_path.write_text(yaml.safe_dump(duplicate_document), encoding="utf-8")
    assert loop.run_from_evidence_report("EXACT_PROVIDER", resolved) is None

    old_timestamp = time.time() - 7200
    os.utime(report_path, (old_timestamp, old_timestamp))
    with pytest.raises(ValueError, match="older than"):
        loop.resolve_evidence_report(report_path, max_age_hours=1)
