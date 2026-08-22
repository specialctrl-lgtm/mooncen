from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from ops_agent.crawler_release_control import (
    EXPECTED_DATABASE_CONTRACT,
    assert_expected_database_contract,
    parse_desired_state,
    reconcile_decision,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "DB"
    / "crawler_control_migrations"
    / "20260810_001_crawler_control_plane.sql"
)
OLD_DIGEST = "b" * 64
NEW_DIGEST = "a" * 64


def desired_payload(*, state: str = "canary", generation: int = 42) -> dict:
    old_version = "2026.08.09.3"
    new_version = "2026.08.10.1"
    canaries = ["gen1crawler"] if state in {"canary", "rolling", "complete"} else []
    cloud_version = new_version if state == "complete" else old_version
    crawler_version = old_version if state == "rollback" else new_version
    return {
        "schema_version": 1,
        "environment": "production",
        "generation": generation,
        "rollout": {
            "id": "00000000-0000-0000-0000-000000000042",
            "state": state,
            "target_version": new_version,
            "baseline_version": old_version,
            "canary_workers": canaries,
        },
        "artifacts": [
            {
                "code_version": new_version,
                "relative_path": f"{new_version}/crawler-release.tar.gz",
                "sha256": NEW_DIGEST,
                "size_bytes": 123,
                "config_revision": "crawler-config-20260810",
            },
            {
                "code_version": old_version,
                "relative_path": f"{old_version}/crawler-release.tar.gz",
                "sha256": OLD_DIGEST,
                "size_bytes": 120,
                "config_revision": "crawler-config-20260809",
            },
        ],
        "workers": [
            {
                "worker_id": "gen1crawler",
                "desired_version": crawler_version,
                "config_revision": (
                    "crawler-config-20260809" if crawler_version == old_version else "crawler-config-20260810"
                ),
                "cohort": "canary",
                "enabled": True,
            },
            {
                "worker_id": "cloud",
                "desired_version": cloud_version,
                "config_revision": (
                    "crawler-config-20260810" if cloud_version == new_version else "crawler-config-20260809"
                ),
                "cohort": "stable",
                "enabled": True,
            },
        ],
    }


def parse(payload: dict):
    return parse_desired_state(json.dumps(payload, separators=(",", ":")))


def test_valid_canary_selects_only_reviewed_worker() -> None:
    state = parse(desired_payload())

    canary = reconcile_decision(
        state,
        "gen1crawler",
        current_version="2026.08.09.3",
        current_digest=OLD_DIGEST,
        current_config_revision="crawler-config-20260809",
        last_generation=41,
    )
    stable = reconcile_decision(
        state,
        "cloud",
        current_version="2026.08.09.3",
        current_digest=OLD_DIGEST,
        current_config_revision="crawler-config-20260809",
        last_generation=41,
    )

    assert canary.action == "deploy"
    assert canary.artifact.sha256 == NEW_DIGEST
    assert stable.action == "noop"


def test_rollout_state_machine_rejects_non_canary_targeting() -> None:
    payload = desired_payload()
    payload["workers"][1]["desired_version"] = "2026.08.10.1"
    payload["workers"][1]["config_revision"] = "crawler-config-20260810"

    with pytest.raises(ValueError, match="exactly the reviewed canary"):
        parse(payload)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("sha256", "A" * 64, "sha256"),
        ("relative_path", "https://evil.example/release.tar.gz", "relative_path"),
        ("relative_path", "../release.tar.gz", "relative_path"),
    ],
)
def test_artifact_identity_rejects_noncanonical_or_url_input(field: str, value: str, message: str) -> None:
    payload = desired_payload()
    payload["artifacts"][0][field] = value

    with pytest.raises(ValueError, match=message):
        parse(payload)


def test_signature_and_key_id_are_atomic_optional_metadata() -> None:
    payload = desired_payload()
    payload["artifacts"][0]["signature"] = "c2lnbmF0dXJl"

    with pytest.raises(ValueError, match="supplied together"):
        parse(payload)


def test_rollout_id_uses_the_authoritative_database_uuid_shape() -> None:
    payload = desired_payload()
    payload["rollout"]["id"] = "operator-label-is-not-a-database-id"

    with pytest.raises(ValueError, match="rollout id"):
        parse(payload)


def test_distinct_versions_cannot_alias_one_artifact_digest() -> None:
    payload = desired_payload()
    payload["artifacts"][1]["sha256"] = NEW_DIGEST

    with pytest.raises(ValueError, match="reuses one digest"):
        parse(payload)


def test_generation_replay_and_immutable_digest_conflict_fail_closed() -> None:
    state = parse(desired_payload())

    with pytest.raises(ValueError, match="older than local"):
        reconcile_decision(
            state,
            "gen1crawler",
            current_version="2026.08.09.3",
            current_digest=OLD_DIGEST,
            current_config_revision="crawler-config-20260809",
            last_generation=43,
        )
    with pytest.raises(ValueError, match="conflicting digest"):
        reconcile_decision(
            state,
            "gen1crawler",
            current_version="2026.08.10.1",
            current_digest="c" * 64,
            current_config_revision="crawler-config-20260810",
            last_generation=41,
        )
    with pytest.raises(ValueError, match="newer central generation"):
        reconcile_decision(
            state,
            "gen1crawler",
            current_version="2026.08.09.3",
            current_digest=OLD_DIGEST,
            current_config_revision="crawler-config-20260809",
            last_generation=42,
        )


def test_expected_database_contract_matches_authoritative_migration_columns() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    observed: dict[str, set[str]] = {}
    for table in EXPECTED_DATABASE_CONTRACT:
        match = re.search(
            rf"CREATE TABLE IF NOT EXISTS {re.escape(table)} \((.*?)\n\);",
            source,
            flags=re.DOTALL,
        )
        assert match, table
        observed[table] = {
            column.group(1)
            for column in re.finditer(r"^    ([a-z][a-z0-9_]*)\s+", match.group(1), flags=re.MULTILINE)
            if not column.group(1).startswith("constraint")
        }

    assert_expected_database_contract(observed)
    observed["ops_crawler_release_artifacts"].remove("artifact_digest")
    with pytest.raises(ValueError, match="ops_crawler_release_artifacts"):
        assert_expected_database_contract(observed)
