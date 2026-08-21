from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import tools.promote_latest_staging_batch as promotion
from tools.apply_staging_batch import latest_batch_id


BATCH_ID = "nightly-batch-1"
FINGERPRINT = "c" * 64


def result_document(*, dry_run: bool, status: str) -> dict:
    return {
        "batch_id": BATCH_ID,
        "dry_run": dry_run,
        "status": status,
        "batch_status": "COLLECTED",
        "collection_complete": True,
        "collection_completion_reason": "complete",
        "partial_batch": False,
        "scheduled_owners": ["OWNER"],
        "successful_owners": ["OWNER"],
        "failed_owners": [],
        "providers": ["PROVIDER"],
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


class FakeConnection:
    def __init__(self) -> None:
        self.closed = False
        self.readonly = False

    def set_session(self, *, readonly: bool, autocommit: bool) -> None:
        self.readonly = readonly
        assert autocommit is False

    def close(self) -> None:
        self.closed = True


class CapturingCursor:
    def __init__(self) -> None:
        self.sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, sql: str) -> None:
        self.sql = sql

    def fetchone(self):
        return None


class CapturingConnection:
    def __init__(self) -> None:
        self.query = CapturingCursor()

    def cursor(self):
        return self.query


def test_latest_selector_excludes_held_control_plane_batches() -> None:
    connection = CapturingConnection()

    with pytest.raises(RuntimeError, match="No ready staging batch"):
        latest_batch_id(connection)

    assert "result->>'control_plane' IS DISTINCT FROM 'true'" in connection.query.sql
    assert "result->>'promotion_eligible' = 'true'" in connection.query.sql


def test_latest_batch_is_dry_run_and_applied_with_one_fingerprint(
    tmp_path: Path,
    monkeypatch,
) -> None:
    tmp_path.chmod(0o700)
    staging = FakeConnection()
    primary = FakeConnection()
    connections = iter((staging, primary))
    commands: list[list[str]] = []

    monkeypatch.setattr(promotion, "latest_batch_id", lambda _connection: BATCH_ID)
    monkeypatch.setattr(
        promotion,
        "successful_apply_result",
        lambda _connection, _batch_id: None,
    )

    def fake_dry_run(*, batch_id: str, result_file: Path) -> None:
        assert batch_id == BATCH_ID
        result_file.write_text(
            json.dumps(result_document(dry_run=True, status="DRY_RUN")),
            encoding="utf-8",
        )
        result_file.chmod(0o600)

    def fake_run(command, *, stdout, check):
        commands.append(command)
        assert check is False
        stdout.write(
            json.dumps(result_document(dry_run=False, status="SUCCESS")).encode(
                "utf-8"
            )
        )
        return subprocess.CompletedProcess(command, 0)

    result = promotion.promote_latest_batch(
        runtime_directory=tmp_path,
        connect_func=lambda _config: next(connections),
        dry_run_func=fake_dry_run,
        run_func=fake_run,
    )

    assert result["status"] == "SUCCESS"
    assert result["batch_id"] == BATCH_ID
    assert result["staging_fingerprint"] == FINGERPRINT
    assert staging.closed and primary.closed and staging.readonly
    assert len(commands) == 1
    assert commands[0][commands[0].index("--expected-staging-fingerprint") + 1] == FINGERPRINT
    assert "--require-latest-batch" in commands[0]
    assert "--force" not in commands[0]
    assert (tmp_path / f"dry-run-{BATCH_ID}.json").is_file()
    assert (tmp_path / f"apply-{BATCH_ID}.json").is_file()


def test_already_applied_latest_batch_is_not_reprocessed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    tmp_path.chmod(0o700)
    staging = FakeConnection()
    primary = FakeConnection()
    connections = iter((staging, primary))
    monkeypatch.setattr(promotion, "latest_batch_id", lambda _connection: BATCH_ID)
    monkeypatch.setattr(
        promotion,
        "successful_apply_result",
        lambda _connection, _batch_id: {"staging_fingerprint": FINGERPRINT},
    )

    result = promotion.promote_latest_batch(
        runtime_directory=tmp_path,
        connect_func=lambda _config: next(connections),
        dry_run_func=lambda **_kwargs: (_ for _ in ()).throw(AssertionError()),
        run_func=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError()),
    )

    assert result == {
        "status": "NO_NEW_BATCH",
        "batch_id": BATCH_ID,
        "staging_fingerprint": FINGERPRINT,
    }
    assert staging.closed and primary.closed


def test_service_uses_the_pinned_automatic_promoter() -> None:
    unit = (
        Path(__file__).resolve().parents[1]
        / "deploy"
        / "ubuntu"
        / "systemd"
        / "mooncen-staging-apply.service"
    ).read_text(encoding="utf-8")

    assert "promote_latest_staging_batch.py" in unit
    assert "RuntimeDirectory=mooncen-staging-apply" in unit
    assert "RuntimeDirectoryMode=0700" in unit
    assert "tools/apply_staging_batch.py\n" not in unit
