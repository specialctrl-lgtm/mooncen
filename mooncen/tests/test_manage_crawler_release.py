from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from tools import manage_crawler_release as release_admin


ENVIRONMENT = "staging"
AGENT_ID = "11111111-1111-4111-8111-111111111111"


def _private_json(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)
    return path


def test_reviewed_workers_are_strict_unique_and_require_an_enabled_canary(tmp_path: Path) -> None:
    path = _private_json(
        tmp_path / "workers.json",
        {
            "schema_version": 1,
            "environment": ENVIRONMENT,
            "workers": [
                {
                    "worker_key": "worker-a",
                    "agent_id": AGENT_ID,
                    "hostname": "crawler-a.internal",
                    "cohort": "canary",
                    "enabled": True,
                }
            ],
        },
    )

    workers = release_admin.load_reviewed_workers(path, environment=ENVIRONMENT)

    assert workers == [
        {
            "worker_key": "worker-a",
            "agent_id": AGENT_ID,
            "hostname": "crawler-a.internal",
            "cohort": "canary",
            "enabled": True,
        }
    ]

    path.write_text(
        '{"schema_version":1,"schema_version":1,"environment":"staging","workers":[]}',
        encoding="utf-8",
    )
    with pytest.raises(release_admin.CrawlerReleaseAdminError, match="duplicate"):
        release_admin.load_reviewed_workers(path, environment=ENVIRONMENT)


def test_artifact_publication_is_digest_pinned_atomic_and_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "release.tar.gz"
    source.write_bytes(b"reviewed crawler release")
    source.chmod(0o600)
    signature = tmp_path / "release.sig"
    signature.write_bytes(b"signed")
    signature.chmod(0o600)
    allowed_signers = tmp_path / "allowed_signers"
    allowed_signers.write_text("release-key ssh-ed25519 AAAA\n", encoding="utf-8")
    allowed_signers.chmod(0o600)
    public_root = tmp_path / "public"
    artifacts = public_root / "artifacts"
    artifacts.mkdir(parents=True)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    verified: list[Path] = []

    monkeypatch.setattr(
        release_admin,
        "_secure_directory",
        lambda path, **_kwargs: path,
    )
    monkeypatch.setattr(
        release_admin,
        "_verify_signature",
        lambda archive, *_args, **_kwargs: verified.append(archive) or b"signed",
    )

    destination, size, relative = release_admin.publish_reviewed_artifact(
        source,
        signature,
        public_root,
        allowed_signers,
        expected_digest=digest,
        key_id="release-key",
    )
    second = release_admin.publish_reviewed_artifact(
        source,
        signature,
        public_root,
        allowed_signers,
        expected_digest=digest,
        key_id="release-key",
    )

    assert destination.read_bytes() == source.read_bytes()
    assert size == len(source.read_bytes())
    assert relative == f"artifacts/{digest}.tar.gz"
    assert second[:2] == (destination, size)
    assert len(verified) == 2
    assert not list(artifacts.glob(".*.new"))


def test_artifact_digest_mismatch_never_publishes_metadata_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "release.tar.gz"
    source.write_bytes(b"wrong bytes")
    source.chmod(0o600)
    signature = tmp_path / "release.sig"
    signature.write_bytes(b"signed")
    signature.chmod(0o600)
    allowed_signers = tmp_path / "allowed_signers"
    allowed_signers.write_text("policy", encoding="utf-8")
    allowed_signers.chmod(0o600)
    public_root = tmp_path / "public"
    artifacts = public_root / "artifacts"
    artifacts.mkdir(parents=True)
    expected = "0" * 64
    monkeypatch.setattr(release_admin, "_secure_directory", lambda path, **_kwargs: path)
    monkeypatch.setattr(
        release_admin,
        "_verify_signature",
        lambda *_args, **_kwargs: pytest.fail("signature verification follows the digest gate"),
    )

    with pytest.raises(release_admin.CrawlerReleaseAdminError, match="SHA-256"):
        release_admin.publish_reviewed_artifact(
            source,
            signature,
            public_root,
            allowed_signers,
            expected_digest=expected,
            key_id="release-key",
        )

    assert not (artifacts / f"{expected}.tar.gz").exists()
    assert not list(artifacts.glob(".*.new"))


def test_release_admin_role_is_narrow_and_primary_cleanup_includes_it() -> None:
    root = Path(__file__).resolve().parents[1]
    roles = (root / "DB" / "roles.sql").read_text(encoding="utf-8")
    migration = (
        root
        / "DB"
        / "crawler_control_migrations"
        / "20260810_001_crawler_control_plane.sql"
    ).read_text(encoding="utf-8")

    assert "CREATE ROLE mooncen_crawler_release_admin NOLOGIN" in roles
    assert "GRANT SELECT, INSERT ON ops_crawler_release_artifacts\n            TO mooncen_crawler_release_admin" in roles
    assert "ops_crawler_worker_desired_state\n            TO mooncen_crawler_release_admin" in roles
    assert "ops_crawler_rollout_worker_snapshots" in roles
    assert "mooncen_crawler_release_admin" in migration
    release_grants = "\n".join(
        statement
        for statement in roles.split(";")
        if "TO mooncen_crawler_release_admin" in statement
    )
    assert "ops_jobs" not in release_grants
    assert "crawl_batches" not in release_grants
    assert "branches" not in release_grants
    assert "courses" not in release_grants
    assert "ops_crawler_release_reports\n            TO mooncen_crawler_release_admin" in release_grants
    assert (
        "REVOKE ALL PRIVILEGES ON DATABASE %I FROM mooncen_crawler_worker"
        in roles
        and "mooncen_crawler_observer, mooncen_crawler_release_admin" in roles
    )


def test_transition_generation_is_exactly_monotonic() -> None:
    with pytest.raises(release_admin.CrawlerReleaseAdminError, match="increment exactly once"):
        release_admin.advance_rollout(
            object(),
            environment=ENVIRONMENT,
            rollout_id=AGENT_ID,
            expected_generation=10,
            next_generation=12,
            phase="rolling",
            target_workers=[],
            public_root=Path("/not/reached"),
        )


def test_forward_release_gate_requires_fresh_report_and_agent_heartbeat() -> None:
    source = (Path(__file__).resolve().parents[1] / "tools/manage_crawler_release.py").read_text(
        encoding="utf-8"
    )
    assert "AS report_fresh" in source
    assert "AS agent_fresh" in source
    assert 'report["agent_status"] != "healthy"' in source
    assert 'report["maintenance_mode"] is not False' in source
    assert "fresh exact healthy report and heartbeat" in source


def test_every_rollout_generation_appends_an_immutable_worker_snapshot() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "tools" / "manage_crawler_release.py").read_text(encoding="utf-8")
    migration = (
        root
        / "DB"
        / "crawler_control_migrations"
        / "20260812_004_rollout_worker_snapshots.sql"
    ).read_text(encoding="utf-8")

    assert source.count("_append_rollout_worker_snapshot(") == 3
    assert "INSERT INTO ops_crawler_rollout_worker_snapshots" in source
    assert "BEFORE UPDATE OR DELETE ON ops_crawler_rollout_worker_snapshots" in migration
    assert "zz_enforce_crawler_rollout_snapshot_commit" in migration


@pytest.mark.skipif(os.name != "posix", reason="POSIX privilege gate only")
def test_release_admin_main_requires_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    assert release_admin.main(["status", "--environment", ENVIRONMENT]) == 1
