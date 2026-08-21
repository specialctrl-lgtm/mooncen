from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ops_agent import crawler_release_agent as release_agent
from ops_agent import crawler_worker
from ops_agent.crawler_release_control import ArtifactMetadata
from tools.bootstrap_distributed_crawler_release import _publish_bootstrap
from tools.preflight_distributed_crawler_worker_host import (
    WorkerHostPreflightError,
    _release_identity,
    render_reviewed_worker_systemd_drop_in,
    validate_bootstrap_baseline,
    validate_reviewed_worker_assignment,
)


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "deploy" / "ubuntu" / "setup_distributed_crawler_worker.sh"


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_worker_host_installer_is_not_ready_before_any_mutation() -> None:
    source = INSTALLER.read_text(encoding="utf-8")
    enrollment = _read("deploy/ubuntu/enroll_distributed_crawler_worker.sh")

    not_ready = source.index("NOT READY: distributed crawler worker installation")
    first_mutation = source.index("installer_lock_dir=")
    conflict_check = source.index('legacy_units=(')
    account_convergence = source.index("ensure_service_account()", conflict_check)
    protected_install = source.index('install_protected_file "$worker_env"', account_convergence)
    optional_enable = source.index('if [ "$enable_reviewed_canary" -eq 1 ]', protected_install)
    assert not_ready < first_mutation < conflict_check
    assert conflict_check < account_convergence < protected_install < optional_enable

    assert "--enable-reviewed-canary" in source
    assert "Every non-help invocation exits before filesystem, systemd" in source
    assert "/opt/mooncen is not an installer trust root" in source
    assert "this installer will not stop it" in source
    assert "assert_legacy_unchanged" in source
    assert "validate_root_owned_parent_chain" in source
    assert "assert_reviewed_release_path" in source
    assert "Protected installer input path must be canonical" in source
    assert "unit_state_is_enabled" in source
    assert "unit_state_is_live" in source
    assert "mooncen-crawler-watchdog.timer" in source
    assert "systemctl disable --now mooncen-crawler.timer" not in source
    assert "systemctl stop mooncen-crawler.timer" not in source
    assert "systemctl stop mooncen-crawler.service" not in source
    assert 'systemctl disable "${canary_enable_units[@]}"' in source
    assert "CRITICAL: one or more new canary units could not be stopped." in source
    assert "CRITICAL: manual shutdown required for new unit" in source

    enrollment_gate = enrollment.index("NOT READY: crawler worker enrollment")
    enrollment_first_mutation = enrollment.index("installer_lock_dir=")
    assert enrollment_gate < enrollment_first_mutation
    assert "No database or filesystem state was changed" in enrollment
    assert "atomic worker/reporter pair provisioner is unavailable" in enrollment
    assert "-m tools.provision_crawler_service_login" not in enrollment


def test_worker_host_installer_pins_identity_database_and_credentials() -> None:
    source = INSTALLER.read_text(encoding="utf-8")

    for identity_key in (
        "OPS_CRAWLER_WORKER_ID",
        "OPS_AGENT_ID",
        "OPS_CRAWLER_WORKER_HOSTNAME",
    ):
        assert source.count(f"read_env_value {identity_key}") >= 2
    assert "socket.gethostname().strip().lower()" in source
    assert "must exactly match the lowercase kernel hostname" in source
    assert 'reporter_agent_id" != "$agent_id' in source
    assert 'release_worker_id" != "$worker_id' in source

    assert "Worker queue, fenced staging, reporter, and shared control endpoints must match exactly." in source
    assert 'shared_database" != "$confirmed_database' in source
    assert 'queue_user" != "$staging_user' in source
    assert 'queue_user" != "$crawler_user' in source
    assert 'queue_password" != "$staging_password' in source
    assert 'queue_password" != "$crawler_password' in source
    assert 'queue_user" = "$reporter_user' in source
    assert 'queue_password" = "$reporter_password' in source
    assert "DB_SSLMODE" in source
    assert "verify-full" in source
    assert "/etc/mooncen/db-root-ca.crt" in source


def test_worker_host_installer_requires_reviewed_disabled_fleet_assignment() -> None:
    source = INSTALLER.read_text(encoding="utf-8")
    unit = _read("deploy/ubuntu/systemd/mooncen-crawler-pull-worker.service")
    enrollment = _read("deploy/ubuntu/enroll_distributed_crawler_worker.sh")

    fleet_preflight = source.index("inventory_preflight_args=(")
    legacy_check = source.index("legacy_units=(")
    account_convergence = source.index("ensure_service_account()", legacy_check)
    assert fleet_preflight < legacy_check < account_convergence
    assert "--require-enabled" in source
    assert "--render-systemd-drop-in" in source
    assert "10-reviewed-worker-resources.conf" in source
    assert "OPS_CRAWLER_REPORTER_DB_PASSWORD OPS_CRAWLER_MAX_CONCURRENCY" in source
    assert 'mktemp "$installer_lock_dir/.worker-resources.XXXXXX"' in source
    assert 'stat -c \'%U:%G:%a\' "$worker_dropin"' in source
    assert "--require-enabled" in unit
    assert "preflight_distributed_crawler_worker_host.py --inventory-only" in unit

    enrollment_preflight = enrollment.index("--inventory-only")
    first_mutation = enrollment.index("-m tools.ensure_crawler_control_schema")
    assert enrollment_preflight < first_mutation

    render = source.index("--render-systemd-drop-in")
    atomic_install = source.index(
        'install_reviewed_file "$generated_dropin" "$worker_dropin" 0644',
        render,
    )
    ownership_check = source.index(
        'stat -c \'%U:%G:%a\' "$worker_dropin"',
        atomic_install,
    )
    verify = source.index('systemd-analyze verify "${new_units[@]/#/$SYSTEMD_DIR/}"')
    reload = source.index("systemctl daemon-reload", verify)
    assert render < atomic_install < ownership_check < verify < reload
    install_function = source[source.index("install_reviewed_file()") : atomic_install]
    assert 'install -o root -g root -m "$mode" "$source" "$temporary"' in install_function
    assert 'mv -fT -- "$temporary" "$destination"' in install_function


def test_dormant_installer_rejects_overrides_and_checks_effective_systemd_contract() -> None:
    source = INSTALLER.read_text(encoding="utf-8")

    for root in (
        "/etc/systemd/system",
        "/run/systemd/system",
        "/usr/local/lib/systemd/system",
        "/usr/lib/systemd/system",
        "/lib/systemd/system",
    ):
        assert root in source
    assert 'printf \'%s\\n\' "$root/$unit.d" "$root/$unit_type.d"' in source
    assert 'printf \'%s\\n\' "$root/$prefix-.$unit_type.d"' in source
    assert "Unreviewed systemd worker override blocks installation" in source
    assert "Existing worker resource override is not the reviewed profile" in source
    assert '$(<"$entry")' in source
    assert "Unreviewed effective systemd drop-in blocks installation" in source
    assert "DropInPaths" in source
    assert "FragmentPath" in source

    reload = source.index("systemctl daemon-reload")
    effective_assertion = source.index("assert_effective_worker_units", reload)
    assert reload < effective_assertion
    for property_name in (
        "User",
        "ExecStart",
        "EnvironmentFiles",
        "MemoryHigh",
        "MemoryMax",
        "CPUQuotaPerSecUSec",
        "NoNewPrivileges",
        "PrivateTmp",
        "PrivateDevices",
        "ProtectSystem",
        "ProtectHome",
        "ProtectKernelTunables",
        "ProtectKernelModules",
        "ProtectKernelLogs",
        "ProtectControlGroups",
        "ProtectClock",
        "ProtectHostname",
        "ProtectProc",
        "ProcSubset",
        "RestrictSUIDSGID",
        "RestrictRealtime",
        "LockPersonality",
        "RemoveIPC",
        "CapabilityBoundingSet",
        "AmbientCapabilities",
    ):
        assert property_name in source


def test_reviewed_worker_profile_matches_exact_hosts_and_limits() -> None:
    wtr = validate_reviewed_worker_assignment(
        "wtr-linux",
        "sgm-standard-pc-i440fx-piix-1996",
        root=ROOT,
    )
    assert render_reviewed_worker_systemd_drop_in(wtr) == (
        "# Generated from config/production_topology.json; do not edit.\n"
        "[Service]\n"
        "Environment=OPS_CRAWLER_MAX_CONCURRENCY=1\n"
        "MemoryHigh=4G\n"
        "MemoryMax=6G\n"
        "CPUQuota=300%\n"
    )
    gen1crawler = validate_reviewed_worker_assignment(
        "gen1crawler",
        "gen1crawler",
        root=ROOT,
    )
    assert "MemoryHigh=2G" in render_reviewed_worker_systemd_drop_in(gen1crawler)
    assert "MemoryMax=4G" in render_reviewed_worker_systemd_drop_in(gen1crawler)
    assert "CPUQuota=200%" in render_reviewed_worker_systemd_drop_in(gen1crawler)
    with pytest.raises(WorkerHostPreflightError, match="kernel hostname does not match"):
        validate_reviewed_worker_assignment("wtr-linux", "wtr-linux", root=ROOT)
    with pytest.raises(WorkerHostPreflightError, match="disabled and pending"):
        validate_reviewed_worker_assignment(
            "wtr-linux",
            "sgm-standard-pc-i440fx-piix-1996",
            root=ROOT,
            require_enabled=True,
        )


def test_worker_execution_rejects_concurrency_above_host_profile() -> None:
    with pytest.raises(ValueError, match="exceeds this worker's reviewed limit"):
        crawler_worker.build_crawler_execution(
            {
                "scope": "provider",
                "provider": "HOMEPLUS",
                "run_mode": "apply",
                "concurrency": 2,
            },
            max_concurrency=1,
        )


def test_worker_host_installer_separates_accounts_and_pins_report_spool() -> None:
    source = INSTALLER.read_text(encoding="utf-8")

    assert "ensure_service_account mooncen-crawler-worker" in source
    assert "ensure_service_account mooncen-crawler-reporter" in source
    assert "groupadd --system mooncen-crawler-status" in source
    assert "mooncen,mooncen-crawler-status" in source
    assert 'status_members" != "mooncen-crawler-worker"' in source
    assert "Worker and reporter must use distinct OS identities." in source
    assert "--shell /usr/sbin/nologin" in source
    assert "usermod --lock --expiredate -1" in source
    assert '"/var/lib/mooncen-crawler-release-agent/reports|root:mooncen-crawler-reporter:2770"' in source
    assert "systemd-tmpfiles --create" in source

    tmpfiles = _read("deploy/ubuntu/templates/crawler-release-agent.tmpfiles.conf")
    assert "d /var/lib/mooncen-crawler-release-agent/reports 2770 root mooncen-crawler-reporter -" in tmpfiles
    assert "d /opt/mooncen-crawler 0750 root mooncen-crawler-worker -" in tmpfiles


def test_status_handoff_uses_a_non_secret_dac_group() -> None:
    worker_unit = _read("deploy/ubuntu/systemd/mooncen-crawler-pull-worker.service")
    release_agent_unit = _read("deploy/ubuntu/systemd/mooncen-crawler-release-agent.service")
    worker_source = _read("ops_agent/crawler_worker.py")
    installer = INSTALLER.read_text(encoding="utf-8")

    assert "Group=mooncen-crawler-status" in worker_unit
    assert "SupplementaryGroups=mooncen-crawler-worker mooncen" in worker_unit
    assert "RuntimeDirectoryMode=0750" in worker_unit
    assert "SupplementaryGroups=mooncen-crawler-status" in release_agent_unit
    assert "CapabilityBoundingSet=\n" in release_agent_unit
    assert "os.O_EXCL, 0o640" in worker_source
    assert "os.fchmod(handle.fileno(), 0o640)" in worker_source
    assert "mooncen-crawler-worker:mooncen-crawler-status:640" in installer
    assert "mooncen-crawler-worker:mooncen-crawler-status:750" in installer
    assert "crawler-worker.env" not in release_agent_unit


@pytest.mark.skipif(os.name != "posix", reason="status handoff mode is POSIX-only")
def test_worker_status_mode_overrides_service_umask(tmp_path: Path) -> None:
    path = tmp_path / "health.json"
    prior_umask = os.umask(0o077)
    try:
        crawler_worker._atomic_worker_status(path, {"healthy": True})
    finally:
        os.umask(prior_umask)
    assert path.stat().st_mode & 0o777 == 0o640


def test_worker_host_installer_installs_and_verifies_only_reviewed_units() -> None:
    source = INSTALLER.read_text(encoding="utf-8")

    for unit in (
        "mooncen-crawler-pull-worker.service",
        "mooncen-crawler-release-agent.service",
        "mooncen-crawler-release-agent.timer",
        "mooncen-crawler-release-reporter.service",
        "mooncen-crawler-release-reporter.timer",
    ):
        assert unit in source
    assert 'source_unit="$APP_DIR/deploy/ubuntu/systemd/$unit"' in source
    assert 'systemd-analyze verify "${new_units[@]/#/$SYSTEMD_DIR/}"' in source
    assert 'bash -n "$APP_DIR/deploy/ubuntu/setup_distributed_crawler_worker.sh"' in source
    assert "--installation-validation" in source
    assert "--component worker" in source
    assert "--component reporter" in source
    assert "--require-baseline" in source
    assert "OPS_CRAWLER_RELEASE_MODE=apply" in source


def test_worker_host_canary_requires_signed_local_baseline_before_enable() -> None:
    source = INSTALLER.read_text(encoding="utf-8")
    bootstrap = _read("tools/bootstrap_distributed_crawler_release.py")

    baseline = source.index("--require-baseline")
    runtime_preflight = source.index("--component worker --env-file", baseline)
    start_worker = source.index("systemctl start mooncen-crawler-pull-worker.service", runtime_preflight)
    start_agent_timer = source.index("systemctl start mooncen-crawler-release-agent.timer", start_worker)
    health = source.index("--require-current-health", start_worker)
    enable = source.index("systemctl enable mooncen-crawler-worker.target", health)
    assert baseline < runtime_preflight < start_worker < health < start_agent_timer < enable
    assert 'canary_enable_units=(\n    mooncen-crawler-worker.target' in source

    signature = bootstrap.index("verify_artifact_signature")
    materialize = bootstrap.index("materialize_release", signature)
    publish = bootstrap.index("_publish_bootstrap", materialize)
    assert signature < materialize < publish
    assert "automatic first" in bootstrap
    assert "expected_digest" in bootstrap
    assert "expected_size" in bootstrap
    assert '"services_started": False' in bootstrap
    assert "urllib" not in bootstrap
    assert "requests" not in bootstrap
    assert "systemctl" not in bootstrap


def test_release_identity_parser_is_exact(tmp_path: Path) -> None:
    release_env = tmp_path / "release.env"
    release_env.write_text(
        "OPS_CRAWLER_CODE_VERSION=2026.08.10.1\n"
        f"OPS_CRAWLER_ARTIFACT_DIGEST={'a' * 64}\n"
        "OPS_CRAWLER_CONFIG_REVISION=config-20260810\n",
        encoding="ascii",
    )

    assert _release_identity(release_env) == {
        "OPS_CRAWLER_CODE_VERSION": "2026.08.10.1",
        "OPS_CRAWLER_ARTIFACT_DIGEST": "a" * 64,
        "OPS_CRAWLER_CONFIG_REVISION": "config-20260810",
    }
    release_env.write_text(release_env.read_text() + "EXTRA=value\n", encoding="ascii")
    with pytest.raises(WorkerHostPreflightError, match="identity is invalid"):
        _release_identity(release_env)


def test_baseline_preflight_matches_current_metadata_and_entrypoints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_root = tmp_path / "release-root"
    state = tmp_path / "state"
    current = release_root / "releases" / "baseline"
    (current / "ops_agent").mkdir(parents=True)
    state.mkdir()
    (current / "ops_agent" / "crawler_worker.py").write_text("# worker\n")
    (current / "run_crawlers.py").write_text("# runner\n")
    (current / "release.env").write_text(
        "OPS_CRAWLER_CODE_VERSION=baseline-1\n"
        f"OPS_CRAWLER_ARTIFACT_DIGEST={'b' * 64}\n"
        "OPS_CRAWLER_CONFIG_REVISION=config-1\n",
        encoding="ascii",
    )
    config = release_agent.AgentConfig(
        worker_id="worker-01",
        environment="production",
        desired_state=release_agent.HttpsEndpoint("https://control.test/state.json", "control.test", "/state.json"),
        artifact_base=release_agent.HttpsEndpoint("https://artifacts.test/releases/", "artifacts.test", "/releases/"),
        allowed_https_hosts=frozenset({"control.test", "artifacts.test"}),
        release_root=release_root,
        state_directory=state,
        drain_state_path=tmp_path / "drain.json",
        health_state_path=tmp_path / "health.json",
        require_signature=True,
        allowed_key_ids=frozenset({"release-key"}),
        allowed_signers_path=tmp_path / "allowed-signers",
        tls_ca_file=tmp_path / "ca.crt",
    )
    local = release_agent.LocalState(
        worker_id="worker-01",
        observed_generation=0,
        applied_generation=0,
        rollout_id="bootstrap",
        current_code_version="baseline-1",
        current_artifact_digest="b" * 64,
        current_config_revision="config-1",
        last_attempt_status="ready",
        updated_at="2026-08-10T00:00:00Z",
    )
    monkeypatch.setattr(release_agent, "load_local_state", lambda *_args, **_kwargs: local)
    monkeypatch.setattr(
        release_agent,
        "_current_release_target",
        lambda *_args: "releases/baseline",
    )

    result = validate_bootstrap_baseline(config)
    assert result["baseline_ready"] is True
    assert result["artifact_digest"] == "b" * 64

    (current / "release.env").write_text(
        (current / "release.env").read_text().replace("baseline-1", "different"),
        encoding="ascii",
    )
    with pytest.raises(WorkerHostPreflightError, match="differs"):
        validate_bootstrap_baseline(config)


@pytest.mark.skipif(os.name != "posix", reason="atomic symlink bootstrap is POSIX-only")
def test_bootstrap_publication_is_journaled_and_idempotent(tmp_path: Path) -> None:
    release_root = tmp_path / "release-root"
    releases = release_root / "releases"
    release_directory = releases / "baseline-aaaaaaaaaaaaaaaa"
    state = tmp_path / "state"
    release_directory.mkdir(parents=True, mode=0o755)
    release_root.chmod(0o755)
    releases.chmod(0o755)
    release_directory.chmod(0o755)
    state.mkdir(mode=0o700)
    artifact = ArtifactMetadata(
        code_version="baseline-1",
        relative_path="bootstrap/baseline-1.tar.gz",
        sha256="a" * 64,
        size_bytes=100,
        config_revision="config-1",
    )
    release_metadata = release_directory / ".mooncen-crawler-release.json"
    release_metadata.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "code_version": artifact.code_version,
                "artifact_digest": artifact.sha256,
                "config_revision": artifact.config_revision,
            }
        ),
        encoding="utf-8",
    )
    release_metadata.chmod(0o444)
    config = release_agent.AgentConfig(
        worker_id="worker-01",
        environment="production",
        desired_state=release_agent.HttpsEndpoint("https://control.test/state.json", "control.test", "/state.json"),
        artifact_base=release_agent.HttpsEndpoint("https://artifacts.test/releases/", "artifacts.test", "/releases/"),
        allowed_https_hosts=frozenset({"control.test", "artifacts.test"}),
        release_root=release_root,
        state_directory=state,
        drain_state_path=tmp_path / "drain.json",
        health_state_path=tmp_path / "health.json",
        require_signature=True,
        allowed_key_ids=frozenset({"release-key"}),
        allowed_signers_path=tmp_path / "allowed-signers",
        tls_ca_file=tmp_path / "ca.crt",
    )

    assert _publish_bootstrap(config, artifact, release_directory) == "installed"
    assert config.current_link.is_symlink()
    assert os.readlink(config.current_link) == "releases/baseline-aaaaaaaaaaaaaaaa"
    assert config.local_state_path.is_file()
    assert not (state / "bootstrap-pending.json").exists()
    assert _publish_bootstrap(config, artifact, release_directory) == "already-installed"


def test_installer_has_valid_bash_syntax_when_bash_is_available() -> None:
    bash = shutil.which("bash") or shutil.which("sh")
    if bash is None:
        pytest.skip("bash is unavailable on this test host")
    completed = subprocess.run(
        [bash, "-n", str(INSTALLER)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
