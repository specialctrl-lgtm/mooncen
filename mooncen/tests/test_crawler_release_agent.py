from __future__ import annotations

import io
import json
import os
import stat
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ops_agent import crawler_release_agent as agent
from ops_agent.crawler_release_agent import (
    AgentConfig,
    HttpsEndpoint,
    LocalState,
    ReleaseAgentError,
)
from ops_agent.crawler_release_control import parse_desired_state, reconcile_decision


ROOT = Path(__file__).resolve().parents[1]
OLD_DIGEST = "b" * 64
NEW_DIGEST = "a" * 64


def desired_payload() -> dict:
    return {
        "schema_version": 1,
        "environment": "production",
        "generation": 42,
        "rollout": {
            "id": "00000000-0000-0000-0000-000000000042",
            "state": "canary",
            "target_version": "2026.08.10.1",
            "baseline_version": "2026.08.09.3",
            "canary_workers": ["gen1crawler"],
        },
        "artifacts": [
            {
                "code_version": "2026.08.10.1",
                "relative_path": "2026.08.10.1/crawler-release.tar.gz",
                "sha256": NEW_DIGEST,
                "size_bytes": 123,
                "config_revision": "crawler-config-20260810",
            },
            {
                "code_version": "2026.08.09.3",
                "relative_path": "2026.08.09.3/crawler-release.tar.gz",
                "sha256": OLD_DIGEST,
                "size_bytes": 120,
                "config_revision": "crawler-config-20260809",
            },
        ],
        "workers": [
            {
                "worker_id": "gen1crawler",
                "desired_version": "2026.08.10.1",
                "config_revision": "crawler-config-20260810",
                "cohort": "canary",
                "enabled": True,
            },
            {
                "worker_id": "cloud",
                "desired_version": "2026.08.09.3",
                "config_revision": "crawler-config-20260809",
                "cohort": "stable",
                "enabled": True,
            },
        ],
    }


def config(tmp_path: Path, *, require_signature: bool = False) -> AgentConfig:
    root = tmp_path / "release-root"
    state = tmp_path / "state"
    for directory in (root, root / "releases", root / ".staging", state, state / "reports"):
        directory.mkdir(mode=0o700)
    if os.name == "posix":
        (state / "reports").chmod(0o2770)
    return AgentConfig(
        worker_id="gen1crawler",
        environment="production",
        desired_state=HttpsEndpoint(
            url="https://control.tailnet.example/v1/desired.json",
            hostname="control.tailnet.example",
            base_path="/v1/desired.json",
        ),
        artifact_base=HttpsEndpoint(
            url="https://artifacts.tailnet.example/crawler/",
            hostname="artifacts.tailnet.example",
            base_path="/crawler/",
        ),
        allowed_https_hosts=frozenset({"control.tailnet.example", "artifacts.tailnet.example"}),
        release_root=root,
        state_directory=state,
        drain_state_path=tmp_path / "run" / "drain.json",
        health_state_path=tmp_path / "run" / "health.json",
        require_signature=require_signature,
        allowed_key_ids=frozenset({"release-2026"}),
        allowed_signers_path=None,
        tls_ca_file=None,
        health_timeout_seconds=10,
        drain_max_age_seconds=120,
    )


def desired_state():
    return parse_desired_state(json.dumps(desired_payload()))


def local_state() -> LocalState:
    return LocalState(
        worker_id="gen1crawler",
        observed_generation=41,
        applied_generation=41,
        rollout_id="00000000-0000-0000-0000-000000000041",
        current_code_version="2026.08.09.3",
        current_artifact_digest=OLD_DIGEST,
        current_config_revision="crawler-config-20260809",
        last_attempt_status="ready",
        updated_at="2026-08-09T00:00:00Z",
    )


def decision(state=None):
    state = desired_state() if state is None else state
    local = local_state()
    return reconcile_decision(
        state,
        "gen1crawler",
        current_version=local.current_code_version,
        current_digest=local.current_artifact_digest,
        current_config_revision=local.current_config_revision,
        last_generation=local.observed_generation,
    )


def write_private_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)


def add_tar_file(archive: tarfile.TarFile, name: str, contents: bytes, mode: int = 0o644) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(contents)
    info.mode = mode
    archive.addfile(info, io.BytesIO(contents))


def test_https_policy_rejects_redirect_primitives_and_non_allowlisted_hosts() -> None:
    allowed = frozenset({"artifacts.tailnet.example"})
    endpoint = agent.validate_https_endpoint(
        "https://artifacts.tailnet.example/releases/",
        allowed_hosts=allowed,
        require_trailing_slash=True,
    )
    assert endpoint.hostname == "artifacts.tailnet.example"

    for value in (
        "http://artifacts.tailnet.example/releases/",
        "https://evil.example/releases/",
        "https://user@artifacts.tailnet.example/releases/",
        "https://artifacts.tailnet.example/releases/?next=https://evil.example",
        "https://127.0.0.1/releases/",
        "https://artifacts.tailnet.example/releases/../private/",
        "https://artifacts.tailnet.example/releases/./nested/",
        "https://artifacts.tailnet.example/releases/%2e%2e/private/",
    ):
        with pytest.raises(ReleaseAgentError):
            agent.validate_https_endpoint(
                value,
                allowed_hosts=allowed,
                require_trailing_slash=True,
            )


def test_managed_directory_policy_rejects_broad_write_roots(tmp_path: Path) -> None:
    with pytest.raises(ReleaseAgentError, match="too broad"):
        agent._managed_directory(tmp_path.anchor, label="release root")


def test_dry_run_is_a_fixed_plan_with_standard_worker_identity_names(tmp_path: Path) -> None:
    payload = agent.dry_run_plan(config(tmp_path), decision())

    assert payload["rollout_action"] == "deploy"
    assert set(payload["desired"]) == {
        "OPS_CRAWLER_CODE_VERSION",
        "OPS_CRAWLER_ARTIFACT_DIGEST",
        "OPS_CRAWLER_CONFIG_REVISION",
    }
    assert "rollback_and_report_on_failure" in payload["steps"]
    assert "url" not in json.dumps(payload).lower()
    assert "command" not in json.dumps(payload).lower()


def test_optional_local_state_means_missing_not_corrupt(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    assert agent.load_local_state(cfg, required=False).last_attempt_status == "bootstrap_required"

    cfg.local_state_path.write_text("not-json", encoding="utf-8")
    cfg.local_state_path.chmod(0o600)
    with pytest.raises(ReleaseAgentError, match="JSON"):
        agent.load_local_state(cfg, required=False)


def test_local_state_rejects_wrong_json_types_without_runtime_type_errors(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    payload = {
        "schema_version": 1,
        **local_state().__dict__,
    }
    payload["current_artifact_digest"] = 42
    write_private_json(cfg.local_state_path, payload)

    with pytest.raises(ReleaseAgentError, match="artifact digest"):
        agent.load_local_state(cfg, required=True)


def test_safe_tar_extraction_accepts_regular_release_and_rejects_links(
    tmp_path: Path,
) -> None:
    artifact = desired_state().artifacts["2026.08.10.1"]
    archive_path = tmp_path / "release.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        add_tar_file(
            archive,
            "crawler-release.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "code_version": artifact.code_version,
                    "config_revision": artifact.config_revision,
                }
            ).encode(),
        )
        add_tar_file(archive, "worker/main.py", b"print('worker')\n")

    candidate = tmp_path / "candidate"
    agent.extract_release_archive(
        archive_path,
        candidate,
        artifact,
        max_files=20,
        max_unpacked_bytes=1024 * 1024,
    )
    assert (candidate / "worker" / "main.py").read_text() == "print('worker')\n"

    malicious = tmp_path / "malicious.tar.gz"
    with tarfile.open(malicious, "w:gz") as archive:
        link = tarfile.TarInfo("worker-link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        archive.addfile(link)
    with pytest.raises(ReleaseAgentError, match="unsupported"):
        agent.extract_release_archive(
            malicious,
            tmp_path / "malicious-candidate",
            artifact,
            max_files=20,
            max_unpacked_bytes=1024 * 1024,
        )


def test_streaming_tar_limit_rejects_before_unbounded_member_collection(tmp_path: Path) -> None:
    artifact = desired_state().artifacts["2026.08.10.1"]
    archive_path = tmp_path / "too-many.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        add_tar_file(archive, "one", b"1")
        add_tar_file(archive, "two", b"2")
        add_tar_file(archive, "three", b"3")

    candidate = tmp_path / "too-many-candidate"
    with pytest.raises(ReleaseAgentError, match="file count"):
        agent.extract_release_archive(
            archive_path,
            candidate,
            artifact,
            max_files=2,
            max_unpacked_bytes=1024,
        )
    assert not candidate.exists()


def test_tar_extension_metadata_is_bounded_before_tarfile_materializes_it(tmp_path: Path) -> None:
    artifact = desired_state().artifacts["2026.08.10.1"]
    archive_path = tmp_path / "oversized-pax.tar.gz"
    with tarfile.open(archive_path, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        info = tarfile.TarInfo("crawler-release.json")
        contents = b"{}"
        info.size = len(contents)
        info.pax_headers = {"comment": "x" * (agent.MAX_TAR_EXTENSION_BYTES + 1)}
        archive.addfile(info, io.BytesIO(contents))

    with pytest.raises(ReleaseAgentError, match="extension metadata"):
        agent.extract_release_archive(
            archive_path,
            tmp_path / "oversized-pax-candidate",
            artifact,
            max_files=20,
            max_unpacked_bytes=1024,
        )


def test_tar_extension_metadata_has_an_aggregate_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    artifact = desired_state().artifacts["2026.08.10.1"]
    archive_path = tmp_path / "aggregate-pax.tar.gz"
    with tarfile.open(archive_path, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        for name in ("one", "two"):
            info = tarfile.TarInfo(name)
            contents = b"x"
            info.size = len(contents)
            info.pax_headers = {"comment": "x" * 40_000}
            archive.addfile(info, io.BytesIO(contents))

    monkeypatch.setattr(agent, "MAX_TAR_EXTENSION_TOTAL_BYTES", 60_000)
    with pytest.raises(ReleaseAgentError, match="extension metadata"):
        agent.extract_release_archive(
            archive_path,
            tmp_path / "aggregate-pax-candidate",
            artifact,
            max_files=20,
            max_unpacked_bytes=1024,
        )


def test_drain_authorization_is_fresh_and_bound_to_generation(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    state = desired_state()
    drain = {
        "schema_version": 1,
        "worker_id": "gen1crawler",
        "rollout_id": state.rollout.rollout_id,
        "generation": state.generation,
        "drained": True,
        "active_jobs": 0,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }
    write_private_json(cfg.drain_state_path, drain)
    agent.assert_drained(cfg, state)

    drain["generation"] -= 1
    write_private_json(cfg.drain_state_path, drain)
    with pytest.raises(ReleaseAgentError, match="does not authorize"):
        agent.assert_drained(cfg, state)


def test_required_signature_rejects_unsigned_artifact_before_any_verifier(
    tmp_path: Path,
) -> None:
    cfg = config(tmp_path, require_signature=True)
    artifact = desired_state().artifacts["2026.08.10.1"]
    archive = tmp_path / "artifact.tar.gz"
    archive.write_bytes(b"not-relevant")

    with pytest.raises(ReleaseAgentError, match="unsigned"):
        agent.verify_artifact_signature(cfg, artifact, archive)


def test_paused_rollout_reports_current_identity_as_pending(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    payload = desired_payload()
    payload["rollout"]["state"] = "paused"
    payload["rollout"]["canary_workers"] = []
    state = parse_desired_state(json.dumps(payload))
    local = local_state()
    selected = reconcile_decision(
        state,
        "gen1crawler",
        current_version=local.current_code_version,
        current_digest=local.current_artifact_digest,
        current_config_revision=local.current_config_revision,
        last_generation=local.observed_generation,
    )

    result = agent.reconcile_apply(cfg, state, local, selected)

    assert result.status == "pending"
    assert result.code_version == local.current_code_version
    report = json.loads(next(cfg.reports_directory.glob("*.json")).read_text())
    assert report["status"] == "pending"
    assert report["health"] == {"healthy": False}
    assert report["artifact_digest"] == OLD_DIGEST


def test_noop_does_not_report_ready_without_installed_release_and_health(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    state = desired_state()
    current = LocalState(
        worker_id="gen1crawler",
        observed_generation=42,
        applied_generation=42,
        rollout_id="00000000-0000-0000-0000-000000000042",
        current_code_version="2026.08.10.1",
        current_artifact_digest=NEW_DIGEST,
        current_config_revision="crawler-config-20260810",
        last_attempt_status="ready",
        updated_at="2026-08-10T00:00:00Z",
    )
    selected = reconcile_decision(
        state,
        "gen1crawler",
        current_version=current.current_code_version,
        current_digest=current.current_artifact_digest,
        current_config_revision=current.current_config_revision,
        last_generation=current.observed_generation,
    )

    result = agent.reconcile_apply(cfg, state, current, selected)

    assert result.status == "drifted"
    stored = json.loads(cfg.local_state_path.read_text())
    assert stored["last_attempt_status"] == "failed"
    report = json.loads(next(cfg.reports_directory.glob("*.json")).read_text())
    assert report["health"] == {"healthy": False}


def test_each_health_observation_has_a_new_id_and_each_file_is_retryable(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    result = agent.ReconcileResult(
        status="ready",
        detail="desired crawler release is healthy",
        generation=42,
        rollout_id="00000000-0000-0000-0000-000000000042",
        code_version="2026.08.10.1",
        artifact_digest=NEW_DIGEST,
        config_revision="crawler-config-20260810",
    )

    first = agent.write_report(cfg, result)
    second = agent.write_report(cfg, result)
    first_payload = json.loads(first.read_text())
    second_payload = json.loads(second.read_text())

    assert first != second
    assert len(list(cfg.reports_directory.glob("*.json"))) == 2
    assert first_payload["id"] in first.name
    assert second_payload["id"] in second.name
    assert first_payload["id"] != second_payload["id"]
    assert json.loads(first.read_text()) == first_payload
    assert first.name < second.name

    changed = agent.write_report(
        cfg,
        agent.ReconcileResult(**{**result.__dict__, "detail": "different semantic outcome"}),
    )
    assert changed not in {first, second}


@pytest.mark.skipif(os.name != "posix", reason="POSIX umask and modes are production-only")
def test_materialized_release_is_readable_under_service_umask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = config(tmp_path)
    artifact = desired_state().artifacts["2026.08.10.1"]
    archive_path = tmp_path / "release.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        add_tar_file(
            archive,
            "crawler-release.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "code_version": artifact.code_version,
                    "config_revision": artifact.config_revision,
                }
            ).encode(),
        )
        add_tar_file(archive, "worker/main.py", b"print('worker')\n")

    monkeypatch.setattr(agent.os, "chown", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(agent.os, "fchown", lambda *_args, **_kwargs: None)
    previous_umask = os.umask(0o077)
    try:
        release = agent.materialize_release(cfg, artifact, archive_path)
    finally:
        os.umask(previous_umask)

    assert stat.S_IMODE(release.stat().st_mode) == 0o755
    assert stat.S_IMODE((release / "worker").stat().st_mode) == 0o755
    assert stat.S_IMODE((release / "release.env").stat().st_mode) == 0o444
    assert stat.S_IMODE((release / ".mooncen-crawler-release.json").stat().st_mode) == 0o444
    assert agent._release_tree_is_immutable(release)

    (release / "worker" / "main.py").chmod(0o666)
    assert not agent._release_tree_is_immutable(release)


def test_health_failure_switches_back_and_reports_rolled_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = config(tmp_path)
    state = desired_state()
    local = local_state()
    selected = decision(state)
    switches: list[str] = []
    restarts: list[str] = []

    monkeypatch.setattr(agent, "_current_release_target", lambda *_args: "releases/old")
    monkeypatch.setattr(
        agent,
        "_download_artifact",
        lambda _cfg, _artifact, destination: destination.write_bytes(b"archive"),
    )
    monkeypatch.setattr(agent, "verify_artifact_signature", lambda *_args: None)
    monkeypatch.setattr(
        agent,
        "materialize_release",
        lambda *_args: cfg.releases_directory / "new",
    )
    monkeypatch.setattr(agent, "assert_drained", lambda *_args: None)
    monkeypatch.setattr(agent, "_switch_current", lambda _cfg, target: switches.append(target))
    monkeypatch.setattr(agent, "restart_worker", lambda: restarts.append("restart"))

    def health(_cfg, artifact, **_kwargs):
        if artifact.code_version == "2026.08.10.1":
            raise ReleaseAgentError("new release unhealthy")

    monkeypatch.setattr(agent, "wait_for_health", health)
    result = agent.reconcile_apply(cfg, state, local, selected)

    assert result.status == "rolled_back"
    assert switches == ["releases/new", "releases/old"]
    assert len(restarts) == 2
    assert not cfg.pending_switch_path.exists()
    stored = json.loads(cfg.local_state_path.read_text())
    assert stored["current_code_version"] == "2026.08.09.3"
    assert stored["last_attempt_status"] == "rolled_back"
    report = json.loads(next(cfg.reports_directory.glob("*.json")).read_text())
    assert report["status"] == "rolled_back"
    assert report["health"] == {"healthy": True}
    assert report["worker_key"] == "gen1crawler"
    assert report["desired_generation"] == 42


def test_prepared_journal_fsync_ambiguity_uses_conservative_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = config(tmp_path)
    state = desired_state()
    local = local_state()
    selected = decision(state)
    switches: list[str] = []

    monkeypatch.setattr(agent, "_current_release_target", lambda *_args: "releases/old")
    monkeypatch.setattr(
        agent,
        "_download_artifact",
        lambda _cfg, _artifact, destination: destination.write_bytes(b"archive"),
    )
    monkeypatch.setattr(agent, "verify_artifact_signature", lambda *_args: None)
    monkeypatch.setattr(agent, "materialize_release", lambda *_args: cfg.releases_directory / "new")
    monkeypatch.setattr(agent, "assert_drained", lambda *_args: None)
    monkeypatch.setattr(agent, "_switch_current", lambda _cfg, target: switches.append(target))
    monkeypatch.setattr(agent, "restart_worker", lambda: None)
    monkeypatch.setattr(agent, "wait_for_health", lambda *_args, **_kwargs: None)

    def publish_then_raise(_cfg, payload):
        write_private_json(cfg.pending_switch_path, dict(payload))
        raise ReleaseAgentError("simulated directory fsync ambiguity")

    monkeypatch.setattr(agent, "_write_pending_switch", publish_then_raise)

    result = agent.reconcile_apply(cfg, state, local, selected)

    assert result.status == "rolled_back"
    assert switches == ["releases/old"]
    assert not cfg.pending_switch_path.exists()
    assert not cfg.terminal_failure_path.exists()


def test_pending_switch_recovery_restores_previous_health(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = config(tmp_path)
    local = local_state()
    payload = {
        "schema_version": 1,
        "worker_id": "gen1crawler",
        "rollout_id": "00000000-0000-0000-0000-000000000042",
        "generation": 42,
        "phase": "switched",
        "previous_target": "releases/old",
        "target": "releases/new",
        "previous_code_version": "2026.08.09.3",
        "previous_artifact_digest": OLD_DIGEST,
        "previous_config_revision": "crawler-config-20260809",
        "target_code_version": "2026.08.10.1",
        "target_artifact_digest": NEW_DIGEST,
        "target_config_revision": "crawler-config-20260810",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    write_private_json(cfg.pending_switch_path, payload)
    switches: list[str] = []
    monkeypatch.setattr(agent, "_target_metadata_matches", lambda *_args: True)
    monkeypatch.setattr(agent, "_current_link_target", lambda _cfg: "releases/new")
    monkeypatch.setattr(agent, "_switch_current", lambda _cfg, target: switches.append(target))
    monkeypatch.setattr(agent, "restart_worker", lambda: None)
    monkeypatch.setattr(agent, "wait_for_health", lambda *_args, **_kwargs: None)

    recovered = agent.recover_pending_switch(cfg, local)

    assert switches == ["releases/old"]
    assert recovered.last_attempt_status == "recovered_previous"
    assert recovered.observed_generation == 42
    assert not cfg.pending_switch_path.exists()
    report = json.loads(next(cfg.reports_directory.glob("*.json")).read_text())
    assert report["status"] == "rolled_back"


def test_pre_switch_failure_journal_recovers_state_and_report_after_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = config(tmp_path)
    state = desired_state()
    local = local_state()
    selected = decision(state)
    original_save = agent.save_local_state

    monkeypatch.setattr(agent, "_current_release_target", lambda *_args: "releases/old")

    def fail_download(*_args):
        raise ReleaseAgentError("download failed")

    monkeypatch.setattr(agent, "_download_artifact", fail_download)

    def crash_before_state(*_args):
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(agent, "save_local_state", crash_before_state)
    with pytest.raises(RuntimeError, match="simulated crash"):
        agent.reconcile_apply(cfg, state, local, selected)
    assert cfg.terminal_failure_path.exists()
    assert not list(cfg.reports_directory.glob("*.json"))

    monkeypatch.setattr(agent, "save_local_state", original_save)
    recovered = agent.recover_terminal_failure(cfg, local)

    assert recovered.last_attempt_status == "failed"
    assert not cfg.terminal_failure_path.exists()
    report = json.loads(next(cfg.reports_directory.glob("*.json")).read_text())
    assert report["status"] == "failed"


def test_systemd_template_ships_check_only_without_ssh_or_shell_escape() -> None:
    unit = (ROOT / "deploy" / "ubuntu" / "systemd" / "mooncen-crawler-release-agent.service").read_text(
        encoding="utf-8"
    )
    timer = (ROOT / "deploy" / "ubuntu" / "systemd" / "mooncen-crawler-release-agent.timer").read_text(encoding="utf-8")
    environment = (ROOT / "deploy" / "ubuntu" / "templates" / "crawler-release-agent.env.example").read_text(
        encoding="utf-8"
    )
    example = (ROOT / "deploy" / "ubuntu" / "templates" / "crawler-release-desired-state.example.json").read_bytes()

    assert "OPS_CRAWLER_RELEASE_MODE=check" in environment
    assert "OPS_CRAWLER_REQUIRE_SIGNATURE=true" in environment
    assert (
        "ExecStart=/opt/mooncen-worker/current/.venv/bin/python -X utf8 "
        "-m ops_agent.crawler_release_agent"
    ) in unit
    assert "ProtectSystem=strict" in unit
    assert "CapabilityBoundingSet=" in unit
    assert "ReadWritePaths=/opt/mooncen-crawler /var/lib/mooncen-crawler-release-agent" in unit
    assert "Persistent=false" in timer
    assert "ssh " not in unit.lower()
    assert "/bin/bash" not in unit
    assert parse_desired_state(example).rollout.state == "canary"
