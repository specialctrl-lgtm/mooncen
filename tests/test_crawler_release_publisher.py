from __future__ import annotations

import json
from pathlib import Path

import pytest

from ops_agent.crawler_release_control import parse_desired_state
from ops_agent.crawler_release_publisher import (
    ReleasePublisherError,
    atomic_publish,
    build_desired_state_document,
    encode_desired_state,
)


TARGET = "a" * 64
BASELINE = "b" * 64


def _rows(state: str = "canary"):
    rollout = {
        "id": "00000000-0000-0000-0000-000000000042",
        "environment": "production",
        "rollout_epoch": 42,
        "artifact_digest": TARGET,
        "previous_artifact_digest": BASELINE,
        "status": "running",
        "requested_worker_count": 2,
        "strategy": {"state": state, "canary_workers": ["gen1crawler"]},
    }
    artifacts = [
        {
            "artifact_digest": TARGET,
            "code_version": "release-42",
            "config_revision": "config-42",
            "artifact_path": "crawler/release-42.tar.gz",
            "size_bytes": 123,
            "signature": None,
            "key_id": None,
        },
        {
            "artifact_digest": BASELINE,
            "code_version": "release-41",
            "config_revision": "config-41",
            "artifact_path": "crawler/release-41.tar.gz",
            "size_bytes": 122,
            "signature": None,
            "key_id": None,
        },
    ]
    workers = [
        {
            "environment": "production",
            "worker_key": "gen1crawler",
            "rollout_id": rollout["id"],
            "generation": 42,
            "desired_status": "draining",
            "cohort": "canary",
            "artifact_digest": TARGET,
            "code_version": "release-42",
            "config_revision": "config-42",
        },
        {
            "environment": "production",
            "worker_key": "cloudworker",
            "rollout_id": rollout["id"],
            "generation": 42,
            "desired_status": "active",
            "cohort": "stable",
            "artifact_digest": BASELINE,
            "code_version": "release-41",
            "config_revision": "config-41",
        },
    ]
    return rollout, workers, artifacts


def test_publisher_maps_relational_canary_state_to_worker_contract() -> None:
    document = build_desired_state_document(*_rows())

    state = parse_desired_state(encode_desired_state(document))
    assert state.generation == 42
    assert state.rollout.canary_workers == ("gen1crawler",)
    assert state.workers["gen1crawler"].desired_version == "release-42"
    assert state.workers["cloudworker"].desired_version == "release-41"


def test_publisher_rejects_worker_identity_drift() -> None:
    rollout, workers, artifacts = _rows()
    workers[0]["config_revision"] = "wrong-config"

    with pytest.raises(ReleasePublisherError, match="differs from its artifact"):
        build_desired_state_document(rollout, workers, artifacts)


def test_publisher_rejects_phase_status_mismatch() -> None:
    rollout, workers, artifacts = _rows("complete")

    with pytest.raises(ReleasePublisherError, match="phase disagree"):
        build_desired_state_document(rollout, workers, artifacts)


def test_atomic_publish_replaces_a_regular_file(tmp_path: Path) -> None:
    output = tmp_path / "desired-state.json"
    output.write_text("old", encoding="utf-8")
    document = build_desired_state_document(*_rows())

    atomic_publish(output, encode_desired_state(document))

    assert json.loads(output.read_text(encoding="utf-8"))["generation"] == 42
    assert not list(tmp_path.glob("*.new"))
