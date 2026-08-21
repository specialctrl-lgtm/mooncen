from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from tools.apply_staging_batch import validate_expected_staging_fingerprint
from tools.run_pinned_staging_apply import execute_pinned_apply, main
from tools.run_pinned_staging_dry_run import create_pinned_dry_run


BATCH_ID = "reviewed-batch-1"
FINGERPRINT = "a" * 64


def dry_run_result() -> dict:
    return {
        "batch_id": BATCH_ID,
        "dry_run": True,
        "status": "DRY_RUN",
        "batch_status": "COLLECTED",
        "collection_complete": True,
        "collection_completion_reason": "complete",
        "partial_batch": False,
        "scheduled_owners": ["SCHEDULED_OWNER"],
        "successful_owners": ["SCHEDULED_OWNER"],
        "failed_owners": [],
        "providers": ["CONCRETE_PROVIDER"],
        "providers_completed": 1,
        "providers_failed": 0,
        "excluded_failed_branches": 0,
        "excluded_failed_courses": 0,
        "courses": 2,
        "valid_courses": 2,
        "invalid_courses": 0,
        "close_missing_enabled": False,
        "close_requested_providers": [],
        "closed_providers": [],
        "close_blocked": {},
        "closed": 0,
        "staging_fingerprint": FINGERPRINT,
    }


def write_result(path: Path, result: dict) -> None:
    path.write_text(json.dumps(result), encoding="utf-8")
    path.chmod(0o600)


def test_wrapper_passes_reviewed_fingerprint_to_forced_exact_apply(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result_file = tmp_path / "dry-run.json"
    write_result(result_file, dry_run_result())
    called: list[tuple[str, list[str]]] = []

    def fake_exec(executable: str, command: list[str]) -> object:
        called.append((executable, command))
        return None

    with pytest.raises(OSError, match="unexpectedly returned"):
        execute_pinned_apply(
            batch_id=BATCH_ID,
            dry_run_result_file=result_file,
            exec_func=fake_exec,
        )

    assert len(called) == 1
    executable, command = called[0]
    assert command[0] == executable
    assert command[command.index("--batch-id") + 1] == BATCH_ID
    assert command[command.index("--expected-staging-fingerprint") + 1] == FINGERPRINT
    assert "--require-latest-batch" in command
    assert "--force" in command
    assert capsys.readouterr().out == ""


def test_dry_run_wrapper_publishes_service_readable_json_without_stdout(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result_file = tmp_path / f"dry-run-{BATCH_ID}.json"
    called: list[list[str]] = []

    def fake_run(command, *, stdout, check):
        called.append(command)
        stdout.write(json.dumps(dry_run_result()).encode("utf-8"))
        return subprocess.CompletedProcess(command, 0)

    create_pinned_dry_run(
        batch_id=BATCH_ID,
        result_file=result_file,
        run_func=fake_run,
    )

    assert len(called) == 1
    assert "--dry-run" in called[0]
    assert "--require-latest-batch" in called[0]
    assert json.loads(result_file.read_text(encoding="utf-8"))["batch_id"] == BATCH_ID
    if os.name == "posix":
        assert stat.S_IMODE(result_file.stat().st_mode) == 0o600
        assert result_file.stat().st_uid == os.geteuid()
    assert capsys.readouterr().out == ""


def test_held_control_batch_dry_run_never_requires_promotion_eligibility(
    tmp_path: Path,
) -> None:
    result_file = tmp_path / f"dry-run-{BATCH_ID}.json"
    called: list[list[str]] = []

    def fake_run(command, *, stdout, check):
        called.append(command)
        stdout.write(json.dumps(dry_run_result()).encode("utf-8"))
        return subprocess.CompletedProcess(command, 0)

    create_pinned_dry_run(
        batch_id=BATCH_ID,
        result_file=result_file,
        require_latest_batch=False,
        run_func=fake_run,
    )

    assert "--dry-run" in called[0]
    assert "--require-latest-batch" not in called[0]


def test_wrapper_rejects_wrong_dry_run_batch_without_stdout(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result_file = tmp_path / "dry-run.json"
    result = dry_run_result()
    result["batch_id"] = "other-batch"
    write_result(result_file, result)

    assert main(
        [
            "--batch-id",
            BATCH_ID,
            "--dry-run-result-file",
            str(result_file),
        ]
    ) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Pinned staging apply rejected" in captured.err


def test_changed_staging_fingerprint_is_rejected() -> None:
    validate_expected_staging_fingerprint(FINGERPRINT, FINGERPRINT)

    with pytest.raises(RuntimeError, match="changed after the reviewed dry-run"):
        validate_expected_staging_fingerprint("b" * 64, FINGERPRINT)
