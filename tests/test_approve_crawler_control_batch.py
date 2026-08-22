from __future__ import annotations

import pytest

from tools import approve_crawler_control_batch as approval


BATCH_ID = "aaaaaaaa-0000-0000-0000-000000000001"
FINGERPRINT = "a" * 64


class FakeCursor:
    def __init__(self, connection) -> None:
        self.connection = connection
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, sql: str, params=None) -> None:
        self.connection.executed.append((sql, params))
        if "UPDATE crawl_batches" in sql:
            self.rowcount = self.connection.update_count

    def fetchone(self):
        return self.connection.row


class FakeConnection:
    def __init__(self, row: dict) -> None:
        self.row = row
        self.update_count = 1
        self.executed: list[tuple[str, object]] = []
        self.commits = 0
        self.rollbacks = 0
        self.session = None

    def set_session(self, **kwargs) -> None:
        self.session = kwargs

    def cursor(self, **_kwargs):
        return FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def held_result() -> dict:
    return {
        "control_plane": True,
        "control_batch_id": BATCH_ID,
        "collection_outcome": "success",
        "collection_complete": True,
        "control_plane_rejected": False,
        "promotion_eligible": False,
        "promotion_policy": "held",
    }


def test_approval_binds_exact_reviewed_fingerprint(monkeypatch) -> None:
    connection = FakeConnection(
        {
            "staging_status": "COLLECTED",
            "control_status": "success",
            "result": held_result(),
        }
    )
    monkeypatch.setattr(approval, "_current_fingerprint", lambda *_args: FINGERPRINT)

    result = approval.approve_batch(
        connection,
        batch_id=BATCH_ID,
        expected_fingerprint=FINGERPRINT,
    )

    assert result["status"] == "APPROVED"
    assert connection.commits == 1
    assert connection.rollbacks == 0
    update = [item for item in connection.executed if "UPDATE crawl_batches" in item[0]]
    assert update[-1][1] == (FINGERPRINT, BATCH_ID)
    assert connection.session == {
        "isolation_level": "SERIALIZABLE",
        "readonly": False,
        "autocommit": False,
    }


def test_approval_rejects_evidence_drift(monkeypatch) -> None:
    connection = FakeConnection(
        {
            "staging_status": "COLLECTED",
            "control_status": "success",
            "result": held_result(),
        }
    )
    monkeypatch.setattr(approval, "_current_fingerprint", lambda *_args: "b" * 64)

    with pytest.raises(RuntimeError, match="changed after"):
        approval.approve_batch(
            connection,
            batch_id=BATCH_ID,
            expected_fingerprint=FINGERPRINT,
        )

    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_approval_is_idempotent_only_for_same_review() -> None:
    result = held_result()
    result.update(
        promotion_eligible=True,
        promotion_policy="approved",
        promotion_approval_fingerprint=FINGERPRINT,
    )
    connection = FakeConnection(
        {
            "staging_status": "COLLECTED",
            "control_status": "success",
            "result": result,
        }
    )

    approved = approval.approve_batch(
        connection,
        batch_id=BATCH_ID,
        expected_fingerprint=FINGERPRINT,
    )

    assert approved["status"] == "ALREADY_APPROVED"
    assert connection.commits == 1
    with pytest.raises(ValueError, match="canonical UUID"):
        approval.canonical_batch_id(BATCH_ID.upper())
