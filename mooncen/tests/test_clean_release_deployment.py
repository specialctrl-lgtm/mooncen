from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "ubuntu" / "deploy_from_windows.ps1"
RELEASE_GUARD = ROOT / "deploy" / "ubuntu" / "mooncen_release_guard.sh"
RELEASE_PREBUILD = ROOT / "deploy" / "ubuntu" / "mooncen_prebuild_release.sh"
RELEASE_GUARD_UNIT = (
    ROOT / "deploy" / "ubuntu" / "systemd" / "mooncen-deploy-guard@.service"
)
SETUP = ROOT / "deploy" / "ubuntu" / "setup_project.sh"
ORCHESTRATOR = ROOT / "deploy_mooncen.ps1"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _bash() -> str | None:
    executable = shutil.which("bash")
    if executable:
        return executable
    git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    return str(git_bash) if git_bash.is_file() else None


def _embedded_script(source: str, variable: str) -> str:
    match = re.search(rf"\${re.escape(variable)} = @'\n(.*?)\n'@", source, re.DOTALL)
    assert match is not None, f"missing embedded script: {variable}"
    return (
        match.group(1)
        .replace("__REMOTE_DIR__", "/opt/mooncen")
        .replace("__RELEASE_DIR__", "/opt/.mooncen-release-" + "a" * 32)
        .replace("__PREVIOUS_DIR__", "/opt/.mooncen-previous-" + "a" * 32)
        .replace("__FAILED_DIR__", "/opt/.mooncen-failed-" + "a" * 32)
        .replace("__ARCHIVE_PATH__", "/opt/.mooncen-release-" + "a" * 32 + "/release.tar.gz")
        .replace("__LOCK_DIR__", "/opt/.mooncen-deploy.lock")
        .replace("__LOCK_TOKEN__", "a" * 32)
    )


def _bash_array(source: str, variable: str) -> list[str]:
    match = re.search(
        rf"^\s*{re.escape(variable)}=\(\s*\n(.*?)^\s*\)",
        source,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"missing bash array: {variable}"
    return match.group(1).split()


def test_windows_deploy_activates_a_clean_immutable_release() -> None:
    deploy = _text(DEPLOY)
    attributes = _text(ROOT / ".gitattributes")

    assert 'if ($RemoteDir -ne "/opt/mooncen")' in deploy
    assert '"/opt/.mooncen-release-$releaseId"' in deploy
    assert '"/opt/.mooncen-previous-$releaseId"' in deploy
    assert 'tar --extract --gzip --file "$archive_path" --directory "$release_dir"' in deploy
    assert "release artifact must not contain symbolic links" in deploy
    assert "release artifact contains forbidden mutable/local path" in deploy
    assert 'sudo mv -- "$release_dir" "$remote_dir"' in deploy
    assert 'sudo mv -- "$remote_dir" "$previous_dir"' in deploy
    assert "tar -xzf /tmp/mooncen-deploy.tar.gz -C '$RemoteDir'" not in deploy
    assert "sudo chown -R '${User}:${User}' '$RemoteDir'" not in deploy
    assert "lock_dir=/opt/.mooncen-deploy.lock" in deploy
    assert "another deployment holds $lock_dir" in deploy
    assert "mooncen-(previous|failed)-[0-9a-f]{32}" in deploy
    assert 'preserving recovery release; resolve it before a new deployment' in deploy
    assert 'sudo rm -rf -- "$stale_path"' not in deploy
    assert "MoonCen deployment lock token mismatch" in deploy
    assert "tools/ops_dashboard.py export-ignore" not in attributes
    assert "tools/ops_dashboard.html export-ignore" not in attributes
    assert "tools/ops_dashboard.py" not in deploy
    assert "tools/ops_dashboard.html" not in deploy
    assert (
        deploy.count("find \"$release_dir\" -xdev -type f \\( -name '*.sh' -o -name '*.service' -o -name '*.timer' \\)")
        == 1
    )
    assert "find '$RemoteDir' -type f" not in deploy
    assert "deploy/ubuntu/*.sh text eol=lf" in attributes
    assert "deploy/ubuntu/systemd/* text eol=lf" in attributes


@pytest.mark.skipif(
    sys.platform != "linux" or _bash() is None or shutil.which("tar") is None,
    reason="GNU tar and POSIX file modes are production-only",
)
def test_release_extraction_overrides_collaborative_umask(tmp_path: Path) -> None:
    deploy = _text(DEPLOY)
    extract = _embedded_script(deploy, "extractReleaseScript")
    extraction = re.search(
        r"(?ms)^umask 0022\n"
        r"tar --extract --gzip --file \"\$archive_path\" --directory \"\$release_dir\" \\\n"
        r"  --no-same-owner --no-same-permissions$",
        extract,
    )
    assert extraction is not None

    archive_input = tmp_path / "archive-input"
    archive_input.mkdir()
    guard_unit = archive_input / "mooncen-deploy-guard@.service"
    guard_unit.write_text("[Unit]\nDescription=mode fixture\n", encoding="utf-8")
    guard_unit.chmod(0o664)
    deploy_dir = archive_input / "deploy"
    deploy_dir.mkdir()
    deploy_dir.chmod(0o775)
    executable = deploy_dir / "setup.sh"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o775)
    archive = tmp_path / "release.tar.gz"
    subprocess.run(
        [
            shutil.which("tar") or "tar",
            "--create",
            "--gzip",
            "--file",
            str(archive),
            "--directory",
            str(archive_input),
            ".",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    release = tmp_path / "release"
    release.mkdir()
    completed = subprocess.run(
        [_bash() or "bash", "-s", "--", str(release), str(archive)],
        input=(
            "set -euo pipefail\n"
            "umask 0002\n"
            "release_dir=$1\n"
            "archive_path=$2\n"
            f"{extraction.group(0)}\n"
        ),
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert (release / "mooncen-deploy-guard@.service").stat().st_mode & 0o777 == 0o644
    assert (release / "deploy").stat().st_mode & 0o777 == 0o755
    assert (release / "deploy" / "setup.sh").stat().st_mode & 0o777 == 0o755
    assert all(path.stat().st_mode & 0o022 == 0 for path in release.rglob("*"))


def test_deploy_fails_closed_on_uncommitted_source_before_remote_access() -> None:
    deploy = _text(DEPLOY)

    assert "function Get-ValidatedDeployCommit" in deploy
    assert "git rev-parse --verify 'HEAD^{commit}'" in deploy
    assert "git status --porcelain=v1 --untracked-files=all" in deploy
    assert "Deployment requires a clean Git working tree" in deploy
    assert deploy.index("$deployCommit = Get-ValidatedDeployCommit") < deploy.index('Get-RemoteEnvValue "DB_PASSWORD"')
    assert deploy.index("$deployCommit = Get-ValidatedDeployCommit") < deploy.index(
        "Invoke-RemoteBashScriptTty $lockAndCleanupScript"
    )


def test_database_passwords_are_validated_before_remote_mutation() -> None:
    deploy = _text(DEPLOY)
    orchestrator = _text(ORCHESTRATOR)

    assert "function Assert-ValidDatabasePassword" in deploy
    assert "Value.Length -lt 16" in deploy
    assert "Value -match '^(change-me|replace-with)'" in deploy
    assert deploy.index('Assert-ValidDatabasePassword -Name "DB_PASSWORD" -Value $DbPassword') < deploy.index(
        "Invoke-RemoteBashScriptTty $lockAndCleanupScript"
    )
    assert deploy.index(
        "Assert-ValidDatabasePassword -Name ([string]$item.Name) -Value ([string]$currentValue)"
    ) < deploy.index("Invoke-RemoteBashScriptTty $lockAndCleanupScript")
    assert 'Write-CheckLine "DB owner password"' in orchestrator
    assert "MoonCenDbPassword must be random and at least 16 characters" in orchestrator


def test_remote_bash_steps_are_normalized_to_lf_before_upload() -> None:
    deploy = _text(DEPLOY)

    assert '$normalizedScript = $Script.Replace("`r`n", "`n").Replace("`r", "`n")' in deploy
    assert "Write-PrivateLocalTextFile $localScriptPath $normalizedScript" in deploy
    assert '$normalizedRemoteSetupScript = $remoteSetupScript.Replace("`r`n", "`n").Replace("`r", "`n")' in deploy
    assert (
        "Write-PrivateLocalTextFile $remoteSetupLocalPath $normalizedRemoteSetupScript"
        in deploy
    )
    health_check = deploy.split("function Invoke-RemoteHealthCheck", 1)[1].split(
        "function Get-RemoteEnvValue", 1
    )[0]
    assert "Invoke-RemoteBashScriptTty $command" in health_check
    assert "Invoke-Remote $command" not in health_check


def test_crawler_browser_runtime_is_reconciled_before_release_activation() -> None:
    deploy = _text(DEPLOY)

    reconcile = "source deploy/ubuntu/install_system_packages.sh && reconcile_installed_browser"
    assert reconcile in deploy
    assert deploy.index(reconcile) < deploy.index("$activateReleaseScript = @'")
    smoke = 'sudo systemctl start mooncen-crawler-browser-smoke.service'
    assert smoke in deploy
    assert deploy.index("Invoke-RemoteBashScriptTty $installUnitsScript") < deploy.index(smoke)


def test_deploy_verifies_and_records_snapshot_provenance() -> None:
    deploy = _text(DEPLOY)
    setup = _text(SETUP)

    assert "Get-FileHash -LiteralPath $archivePath -Algorithm SHA256" in deploy
    assert "sha256sum --check --strict" in deploy
    assert "export DEPLOY_COMMIT='$deployCommit'" in deploy
    assert "export DEPLOY_ARCHIVE_SHA256='$archiveSha256'" in deploy
    assert "deployment provenance mismatch" in deploy
    assert "verified_deploy_commit" in deploy
    assert "printf 'DEPLOY_COMMIT=%s\\n' \"$DEPLOY_COMMIT\"" in setup
    assert "printf 'DEPLOY_ARCHIVE_SHA256=%s\\n' \"$DEPLOY_ARCHIVE_SHA256\"" in setup


def test_remote_health_check_requires_the_frontend_and_nginx_root() -> None:
    deploy = _text(DEPLOY)

    assert "curl -fsS http://127.0.0.1:5173/ >/dev/null" in deploy
    assert "curl -fsS http://localhost/ >/dev/null" in deploy


def test_deploy_target_must_be_explicit_and_multi_target_action_is_available() -> None:
    orchestrator = _text(ORCHESTRATOR)
    registry_example = _text(ROOT / "config" / "deploy_servers.example.json")
    production_topology = _text(ROOT / "config" / "production_topology.json")

    assert '$Action -in @("deploy", "full-deploy") -and $Target -eq "default"' in orchestrator
    assert "Deployment target is required" in orchestrator
    assert '"deploy-all" {' in orchestrator
    assert '"defaultTarget": "cloud"' in registry_example
    assert '"activeNode": "cloud"' in production_topology
    assert '"crawlerMode": "legacy"' in production_topology
    assert "function Get-ProductionCrawlerContract" in orchestrator
    assert 'git -C $repositoryRoot show "${SnapshotCommit}:config/production_topology.json"' in orchestrator
    assert "Get-ProductionCrawlerContract $registryInfo.Servers $SourceCommit" in orchestrator
    assert "n100 configured" not in orchestrator
    assert "n100 role" not in orchestrator


def test_preflight_accepts_reviewed_default_ssh_authentication() -> None:
    orchestrator = _text(ORCHESTRATOR)

    assert "([string]$item.IdentityFile).Trim()" in orchestrator
    assert '"ssh-agent"' in orchestrator
    assert "Get-Command ssh -ErrorAction SilentlyContinue" in orchestrator
    assert "Get-Command ssh-add -ErrorAction SilentlyContinue" in orchestrator
    assert '$process.StartInfo.Arguments = "-L"' in orchestrator
    assert "$process.WaitForExit(5000)" in orchestrator
    assert "FromBase64String" in orchestrator
    assert "loaded public key verified via ssh-add -L" in orchestrator
    assert "ssh-add -L returned no valid OpenSSH public key" in orchestrator
    assert '"disabled on cloud-only topology"' in orchestrator


def test_reviewed_commit_and_target_identity_are_verified_through_archive_creation() -> None:
    orchestrator = _text(ORCHESTRATOR)
    wrapper = _text(ROOT / "deploy_ubuntu.ps1")
    deploy = _text(DEPLOY)

    assert "[string]$ExpectedCommit" in orchestrator
    assert "[string]$ExpectedTargetIdentity" in orchestrator
    assert "function Get-DeployTargetIdentity" in orchestrator
    identity_function = orchestrator.split(
        "function Get-DeployTargetIdentity",
        1,
    )[1].split("function Get-CurrentDeployCommit", 1)[0]
    for field in (
        "name_b64",
        "server_b64",
        "user_b64",
        "domain_b64",
        "remote_dir_b64",
        "role_b64",
        "deploy_profile_b64",
        "environment_b64",
        "active=",
    ):
        assert field in identity_function
    assert "IdentityFile" not in identity_function
    assert orchestrator.index("$currentTargetIdentity = Get-DeployTargetIdentity $targetConfig") < orchestrator.index(
        "$server = $targetConfig.Server"
    )
    # Both full-stack paths and the signed control-only path pin the reviewed
    # commit. The control action still stops on backup attestation before SSH.
    assert orchestrator.count("Assert-ExpectedDeployCommit $ExpectedCommit") == 3
    assert orchestrator.count("-ExpectedCommit $ExpectedCommit") == 3

    assert "[string]$ExpectedCommit" in wrapper
    assert "-ExpectedCommit $ExpectedCommit" in wrapper
    assert "[string]$ExpectedCommit" in deploy
    assert "function Assert-ExactExpectedDeployCommit" in deploy
    assert deploy.index('Assert-ExactExpectedDeployCommit $deployCommit "deployment preflight"') < deploy.index(
        'Get-RemoteEnvValue "DB_PASSWORD"'
    )
    assert deploy.index('Assert-ExactExpectedDeployCommit $currentDeployCommit "archive creation"') < deploy.index(
        "git -C $script:DeploymentGitRepositoryRoot archive --format=tar.gz"
    )


def test_reviewed_development_snapshot_is_verified_through_archive_creation() -> None:
    orchestrator = _text(ORCHESTRATOR)
    wrapper = _text(ROOT / "deploy_ubuntu.ps1")
    deploy = _text(DEPLOY)

    for source in (orchestrator, wrapper, deploy):
        assert "[string]$SourceCommit" in source
        assert "[string]$ExpectedSourceTree" in source
    assert "-SourceCommit $SourceCommit" in orchestrator
    assert "-ExpectedSourceTree $ExpectedSourceTree" in orchestrator
    assert "-SourceCommit $SourceCommit" in wrapper
    assert "-ExpectedSourceTree $ExpectedSourceTree" in wrapper
    assert "The development snapshot is not based on the reviewed Git HEAD" in deploy
    assert "does not match ExpectedSourceTree" in deploy
    assert "symbolic links or submodules" in deploy
    assert "git -C $repositoryRoot ls-tree -r --name-only" in deploy
    assert "git -C $repositoryRoot ls-tree -r $commit" in deploy
    assert "git -C $script:DeploymentGitRepositoryRoot archive" in deploy
    assert deploy.index("$deployCommit = Get-ValidatedDeployCommit") < deploy.index('Get-RemoteEnvValue "DB_PASSWORD"')


def test_preflight_checks_backup_recipient_and_complete_primary_trust_bundle() -> None:
    orchestrator = _text(ORCHESTRATOR)

    assert 'Write-CheckLine "backup age recipient"' in orchestrator
    assert 'Write-CheckLine "backup trust bundle"' in orchestrator
    assert "function Test-RemoteBackupTrustContract" in orchestrator
    assert "root:root:600" in orchestrator
    assert "age-keygen -y /etc/mooncen/backup-age-key.txt" in orchestrator
    assert "backup-ssh-key root:mooncen-backup:640" in orchestrator
    assert "backup-known-hosts root:mooncen-backup:640" in orchestrator
    assert "backup-manifest-signing-key root:mooncen-backup:640" in orchestrator
    assert "backup-manifest-allowed-signers root:root:644" in orchestrator


def test_post_activation_failure_has_a_global_rollback_guard() -> None:
    deploy = _text(DEPLOY)
    guard = _text(RELEASE_GUARD)
    unit = _text(RELEASE_GUARD_UNIT)

    assert "} catch {\n    $deploymentFailure = $_" in deploy
    assert "$deploymentFailureExitCode = $script:DeploymentRemoteExitCode" in deploy
    assert "    if ($remoteGuardArmed) {" in deploy
    assert "Requesting immediate remote recovery" in deploy
    assert 'sudo "$lock_dir/guard.sh" recover "$lock_dir" "$lock_token"' in deploy
    assert "watch_guard" in guard
    assert "HEARTBEAT_STALE_SECONDS=180" in guard
    assert "guard.sh boot-recover /opt/.mooncen-deploy.lock" in unit
    assert "guard.sh watch /opt/.mooncen-deploy.lock" in unit
    assert "Before=nginx.service cloudflared.service" in unit


def test_reviewed_crawler_host_deploy_does_not_interrupt_an_active_crawler_by_default() -> None:
    deploy = _text(DEPLOY)
    wrapper = _text(ROOT / "deploy_ubuntu.ps1")
    orchestrator = _text(ORCHESTRATOR)

    assert "if ($EnableCrawler -and -not $AllowCrawlerInterruption)" in deploy
    assert "crawler-host deploy blocked: crawler state is $progress_status" in deploy
    assert "wait for sleeping state" in deploy
    assert "sleeping|completed|partial_success|failed|stopped|skipped" in deploy
    assert "[switch]$EnableCrawler" in deploy
    assert "[switch]$EnableCrawler" in wrapper
    assert "function Get-ProductionCrawlerContract" in orchestrator
    assert orchestrator.count("-EnableCrawler:$enableCrawler") == 2
    assert "-EnableCrawler:$EnableCrawler" in wrapper
    assert orchestrator.count("-CrawlerMode $crawlerMode") == 2
    assert "-CrawlerMode $CrawlerMode" in wrapper
    assert "[switch]$AllowCrawlerInterruption" in deploy
    assert "-AllowCrawlerInterruption:$AllowCrawlerInterruption" in wrapper
    assert orchestrator.count("-AllowCrawlerInterruption:$AllowCrawlerInterruption") == 2


def test_reviewed_distributed_mode_cannot_enable_the_legacy_runtime() -> None:
    deploy = _text(DEPLOY)
    wrapper = _text(ROOT / "deploy_ubuntu.ps1")
    orchestrator = _text(ORCHESTRATOR)

    assert '$enableCrawler = $crawlerMode -eq "legacy" -and' in orchestrator
    assert 'CrawlerMode $crawlerMode' in orchestrator
    assert '[ValidateSet("legacy", "distributed", "")]' in wrapper
    assert "-CrawlerMode $CrawlerMode" in wrapper
    assert "CrawlerMode does not match the reviewed production topology" in deploy
    assert 'if ($CrawlerMode -eq "distributed" -and $EnableCrawler)' in deploy
    assert "Distributed crawler mode forbids enabling the legacy crawler runtime" in deploy


def test_non_owner_cloud_deploy_does_not_manage_crawler_units() -> None:
    deploy = _text(DEPLOY)

    scheduler_block = deploy.split(
        "if (-not $Standby -and $EnableCrawler -and -not $SkipWorkers)", 1
    )[1].split("if (-not $SkipWorkers -and -not $Standby)", 1)[0]
    assert "mooncen-crawler.timer" in scheduler_block
    assert "mooncen-staging-apply.timer" in scheduler_block
    assert "else" not in scheduler_block


def test_crawler_owner_uses_isolated_staging_and_pinned_promotion() -> None:
    deploy = _text(DEPLOY)
    setup = _text(SETUP)
    apply_unit = _text(
        ROOT / "deploy" / "ubuntu" / "systemd" / "mooncen-staging-apply.service"
    )

    assert "CRAWL_WRITE_MODE=staging" in setup
    assert "CRAWL_WRITE_MODE=direct" not in setup
    assert 'ENABLE_CRAWLER_STAGING must be 0 or 1' in setup
    assert "CRAWL_STAGING_DB_USER=${CRAWL_STAGING_DB_USER}" in setup
    assert 'USE_DEDICATED_STAGING_CLUSTER=1' in setup
    assert "promote_latest_staging_batch.py" in apply_unit
    assert "TZ=Asia/Seoul" in setup
    assert "if (-not $Standby -and $EnableCrawler -and -not $SkipWorkers)" in deploy
    assert "mooncen-crawler-once.service mooncen-crawler.service" in deploy
    assert "sudo systemctl stop mooncen-staging-apply.service" in deploy
    assert "enable --now mooncen-crawler.timer mooncen-staging-apply.timer" in deploy
    assert "long-running crawler service must remain inactive on the crawler owner" in deploy


def test_crawler_owner_uses_one_timer_scheduler_across_deploy_control_and_monitoring() -> None:
    helper = _text(ROOT / "deploy" / "ubuntu" / "ops_service_helper.sh")
    control = _text(ROOT / "deploy" / "ubuntu" / "mooncenctl.sh")
    metrics = _text(ROOT / "deploy" / "monitoring" / "mooncen_node_metrics.sh")
    timer = _text(ROOT / "deploy" / "ubuntu" / "systemd" / "mooncen-crawler.timer")

    main_services = helper.split("MAIN_SERVICES=(", 1)[1].split(")", 1)[0]
    assert "mooncen-crawler.service" not in main_services
    assert "CRAWLER_SCHEDULER=mooncen-crawler.timer" in helper
    assert 'restart-crawler) require_crawler_owner; service_unit="$CRAWLER_SCHEDULER"' in helper
    assert 'logs-crawler) require_crawler_owner; log_unit="$CRAWLER_RUNNER"' in helper
    assert "CRAWLER_UNITS=(mooncen-crawler.timer mooncen-crawler-once.service)" in control
    assert "mooncen-crawler.timer" in metrics
    assert "mooncen-crawler-once.service" in metrics
    assert "mooncen_crawler_last_success_timestamp_seconds" in metrics
    assert "mooncen_crawler_cycle_partial_success" in metrics
    assert "mooncen_crawler_cycle_skipped_lock_contention" in metrics
    assert "mooncen_crawler_cycle_zero_provider" in metrics
    assert "mooncen_crawler_cycle_state_valid" in metrics
    assert "crawler_cycle_state.json" in metrics
    assert "FROM crawler_run_log" not in metrics
    assert 'exit_status" = "75"' in metrics
    assert 'exit_status" = "4"' in metrics
    assert "ExecMainStatus" in metrics
    assert "OnCalendar=*-*-* 22:00:00 Asia/Seoul" in timer
    once = _text(ROOT / "deploy" / "ubuntu" / "systemd" / "mooncen-crawler-once.service")
    assert "Environment=TZ=Asia/Seoul" in once


def test_crawler_cycle_metric_parser_reads_bounded_durable_state(tmp_path: Path) -> None:
    metrics = _text(ROOT / "deploy" / "monitoring" / "mooncen_node_metrics.sh")
    parser_block = metrics.split("<<'PY'", 1)[1].split("\n", 1)[1]
    parser_source = parser_block.split("\nPY\n", 1)[0]
    state_path = tmp_path / "crawler_cycle_state.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "final_outcome": "zero_provider",
                "zero_provider": True,
                "providers_requested": 2,
                "providers_completed": 0,
                "providers_failed": 2,
                "last_success_at": "2026-08-06T11:00:00+00:00",
                "last_completed_at": "2026-08-07T10:05:00+00:00",
                "finished_at": "2026-08-07T10:05:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, "-I", "-", str(state_path)],
        input=parser_source,
        text=True,
        capture_output=True,
        check=True,
    )
    fields = result.stdout.split("|")
    assert fields[0] == "1"
    assert int(fields[1]) > 0
    assert int(fields[2]) > int(fields[1])
    assert fields[3:] == ["zero_provider", "1", "2", "0", "2"]


def test_crawler_units_never_mask_nonzero_cycle_outcomes_as_success() -> None:
    service = _text(ROOT / "deploy" / "ubuntu" / "systemd" / "mooncen-crawler.service")
    once = _text(
        ROOT / "deploy" / "ubuntu" / "systemd" / "mooncen-crawler-once.service"
    )

    for unit in (service, once):
        assert "SuccessExitStatus=" not in unit
    assert "RestartPreventExitStatus=75" in service
    assert "RestartPreventExitStatus=" not in once


def test_partial_crawler_cycle_remains_visible_to_alerting() -> None:
    alerts = _text(
        ROOT
        / "deploy"
        / "monitoring"
        / "grafana"
        / "provisioning"
        / "alerting"
        / "rules-mooncen-app.yml"
    )

    assert 'max(mooncen_crawler_cycle_partial_success{node="gen1crawler"} != bool 0)' in alerts
    assert "or vector(0)" in alerts
    assert "completed only partially" in alerts


def test_active_cloud_inventory_has_no_retired_n100_runtime_target() -> None:
    topology = _text(ROOT / "config" / "production_topology.json")
    registry = _text(ROOT / "config" / "deploy_servers.example.json")
    prometheus = _text(ROOT / "deploy" / "monitoring" / "prometheus" / "prometheus.yml")
    exporter = _text(ROOT / "deploy" / "monitoring" / "install_exporters.ps1")
    alerts = _text(ROOT / "deploy" / "monitoring" / "grafana" / "provisioning" / "alerting" / "rules-mooncen-app.yml")

    for source in (topology, registry, prometheus, exporter, alerts):
        assert "n100" not in source.lower()
    assert not (ROOT / "deploy" / "ubuntu" / "systemd" / "mooncen-n100-migration-backup.service").exists()
    assert not (ROOT / "deploy" / "ha" / "systemd" / "mooncen-failover-watch.service").exists()
    assert not (ROOT / "deploy" / "ha" / "systemd" / "mooncen-failover-watch.timer").exists()


def test_setup_replaces_the_virtualenv_only_after_hash_locked_install() -> None:
    setup = _text(SETUP)

    create_index = setup.index('python3 -I -m venv --clear "$venv_stage"')
    install_index = setup.index('"$venv_stage/bin/python" -I -m pip install --require-hashes')
    activate_index = setup.index('mv -- "$venv_stage" "$APP_DIR/.venv"')
    assert create_index < install_index < activate_index
    assert 'venv_stage="$(mktemp -d "$APP_DIR/.venv.stage.XXXXXX")"' in setup
    assert 'mv -- "$APP_DIR/.venv" "$venv_previous"' in setup
    assert "Existing virtual environment path is unsafe" in setup
    assert 'python3 -I -m venv "$APP_DIR/.venv"' not in setup
    assert 'if [ "$APP_DIR" != "/opt/mooncen" ]' in setup
    assert 'if [ "$BACKUP_ENV_FILE" != "/etc/mooncen/backup.env" ]' in setup
    assert 'sudo rm -rf -- "$BACKUP_LIBEXEC_DIR"' in setup
    assert 'sudo rm -rf -- "$HA_LIBEXEC_DIR"' in setup


def test_candidate_dependencies_and_frontend_are_prebuilt_before_release_swap() -> None:
    deploy = _text(DEPLOY)
    setup = _text(SETUP)
    prebuild = _text(RELEASE_PREBUILD)
    extract = _embedded_script(deploy, "extractReleaseScript")

    assert "deploy/ubuntu/mooncen_prebuild_release.sh" in extract
    assert "MOONCEN_PREBUILD_CONFIG_STDIN=1" in deploy
    assert "Invoke-RemoteWithInput $prebuildCommand $prebuildInput" in deploy
    assert 'kakao_maps_javascript_key_b64="${kakao_maps_javascript_key_b64#$\'\\xEF\\xBB\\xBF\'}"' in prebuild
    assert 'config_value="${config_value%$\'\\r\'}"' in prebuild
    assert "candidate build configuration contains an invalid transport marker" in prebuild
    assert 'KAKAO_MAPS_JAVASCRIPT_KEY="$(decode_build_config' in prebuild
    assert 'export KAKAO_MAPS_JAVASCRIPT_KEY GOOGLE_OAUTH_CLIENT_ID' in prebuild
    assert 'export KAKAO_MAPS_JAVASCRIPT_KEY="$(decode_build_config' not in prebuild
    assert deploy.index("Invoke-RemoteWithInput $prebuildCommand $prebuildInput") < deploy.index(
        "$activateReleaseScript = @'"
    )
    assert "export PREBUILT_RELEASE=1" in deploy

    assert 'if [ "$PREBUILT_RELEASE" = "1" ]; then' in setup
    prebuilt_branch = setup.split('if [ "$PREBUILT_RELEASE" = "1" ]; then', 1)[1].split(
        "\nelse\n", 1
    )[0]
    assert "pip install" not in prebuilt_branch
    assert "npm ci" not in prebuilt_branch
    assert "npm run build" not in prebuilt_branch
    assert 'stat -c \'%U:%G:%a\' "$prebuild_marker"' in prebuilt_branch
    assert "verify_prebuild_digest REQUIREMENTS_SHA256" in prebuilt_branch
    assert "verify_prebuild_digest PACKAGE_LOCK_SHA256" in prebuilt_branch
    assert "verify_prebuild_digest FRONTEND_ENV_SHA256" in prebuilt_branch
    assert "verify_prebuild_digest FRONTEND_INDEX_SHA256" in prebuilt_branch
    marker_hardening = setup.index('echo "Prebuilt release marker disappeared during setup."')
    recursive_group_read = setup.index('sudo chmod -R g+rX "$APP_DIR"')
    assert recursive_group_read < marker_hardening
    assert 'sudo chown root:root "$prebuild_marker"' in setup[marker_hardening:]
    assert 'sudo chmod 0600 "$prebuild_marker"' in setup[marker_hardening:]

    assert 'python3 -I -m venv --copies "$venv"' in prebuild
    assert "pip install --no-compile --require-hashes" in prebuild
    assert "npm ci --ignore-scripts" in prebuild
    assert "npm run build" in prebuild
    assert "PREBUILD_VERSION=1" in prebuild
    assert 'grep -RIlF -- "$release_dir" "$venv"' in prebuild
    assert 'sed -i "s|$release_dir|$FINAL_APP_DIR|g"' in prebuild


def test_activated_prebuilt_virtualenv_is_relocation_checked_and_api_smoked() -> None:
    setup = _text(SETUP)
    systemd_dir = ROOT / "deploy" / "ubuntu" / "systemd"
    units = "\n".join(_text(path) for path in systemd_dir.glob("mooncen-*.service"))

    assert "Candidate release path leaked into the activated virtual environment" in setup
    assert '"$APP_DIR/.venv/bin/python" -I -c \'import sys; print(sys.prefix)\'' in setup
    assert 'if [ "$actual_venv_prefix" != "$APP_DIR/.venv" ]' in setup
    assert '"$APP_DIR/.venv/bin/python" -I -m pip check' in setup
    assert '"$APP_DIR/.venv/bin/python" -I -m compileall -q -f' in setup
    assert '"$APP_DIR/backend" "$APP_DIR/Crawler" "$APP_DIR/tools"' in setup
    assert "import backend.main" in setup
    assert "/opt/mooncen/.venv/bin/python" in units
    assert "/opt/mooncen/.venv/bin/uvicorn" not in units


def test_deploy_converges_removed_units_and_dropins() -> None:
    deploy = _text(DEPLOY)

    assert "Deployment owns MoonCen drop-ins" in deploy
    assert "/etc/systemd/system/mooncen-*.service.d" in deploy
    assert "MoonCen systemd unit manifest is empty" in deploy
    assert 'grep -Fxq -- "$unit_name" "$manifest"' in deploy
    assert 'sudo systemctl disable --now "$unit_name"' in deploy
    assert 'sudo rm -f -- "$installed"' in deploy
    assert 'sudo install -o root -g root -m 0644 "$unit_source/$unit_name"' in deploy
    assert "mooncen-node-metrics.service|mooncen-node-metrics.timer" in deploy
    assert "These units are owned by deploy/monitoring/install_linux_exporter.sh" in deploy
    assert deploy.count('$1 != "mooncen-node-metrics.service"') >= 1
    assert "/etc/systemd/system/mooncen-node-metrics.service.d|/etc/systemd/system/mooncen-node-metrics.timer.d" in deploy


def test_external_deployment_transport_is_not_application_lifecycle_state() -> None:
    deploy = _text(DEPLOY)
    activation = _embedded_script(deploy, "activateReleaseScript")
    installer = _embedded_script(deploy, "installUnitsScript")
    guard = _text(RELEASE_GUARD)
    transport_case = (
        "mooncen-an2p-deploy-sshd.service|"
        "mooncen-an2p-deploy-sshd.service.d"
    )

    assert transport_case in activation
    assert transport_case in installer
    assert transport_case in guard
    assert 'is_external_control_plane_unit "$unit_name"' in activation
    assert 'is_external_control_plane_unit "$(basename "$dropin")"' in activation
    assert 'is_external_control_plane_unit "$unit_name"' in installer
    assert "external control-plane unit entered the application baseline" in guard
    assert "external control-plane drop-in entered the application baseline" in guard
    assert guard.count('is_external_control_plane_unit_name "$unit_name"') >= 8


@pytest.mark.skipif(_bash() is None, reason="bash unavailable")
def test_activation_and_stale_unit_prune_dynamically_preserve_transport(
    tmp_path: Path,
) -> None:
    deploy = _text(DEPLOY)
    activation = _embedded_script(deploy, "activateReleaseScript")
    installer = _embedded_script(deploy, "installUnitsScript")
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()
    for unit_name in (
        "mooncen-api.service",
        "mooncen-crawler.service",
        "mooncen-node-metrics.service",
        "mooncen-an2p-deploy-sshd.service",
    ):
        (systemd_dir / unit_name).write_text("[Unit]\n", encoding="utf-8")
    transport_dropin = systemd_dir / "mooncen-an2p-deploy-sshd.service.d"
    transport_dropin.mkdir()
    (transport_dropin / "override.conf").write_text("[Service]\n", encoding="utf-8")

    crawler_helper = "is_crawler_runtime_unit() {" + activation.split(
        "is_crawler_runtime_unit() {", 1
    )[1].split("\n}\n", 1)[0] + "\n}\n"
    transport_helper = "is_external_control_plane_unit() {" + activation.split(
        "is_external_control_plane_unit() {", 1
    )[1].split("\n}\n", 1)[0] + "\n}\n"
    selection = "mapfile -t managed_units" + activation.split(
        "mapfile -t managed_units", 1
    )[1].split('if [ "${#managed_units[@]}" -gt 0 ]; then', 1)[0]
    selection = selection.replace("/etc/systemd/system", str(systemd_dir))
    stop_log = tmp_path / "activation-stop.log"
    activation_probe = subprocess.run(
        [_bash() or "bash", "-s"],
        input=(
            "set -euo pipefail\n"
            "crawler_runtime_enabled=0\n"
            f"{crawler_helper}{transport_helper}{selection}"
            f"printf '%s\\n' \"${{managed_units[@]}}\" > {stop_log!s}\n"
        ),
        capture_output=True,
        text=True,
        check=False,
    )
    assert activation_probe.returncode == 0, activation_probe.stderr
    assert stop_log.read_text(encoding="utf-8").splitlines() == [
        "mooncen-api.service"
    ]

    install_transport_helper = "is_external_control_plane_unit() {" + installer.split(
        "is_external_control_plane_unit() {", 1
    )[1].split("\n}\n", 1)[0] + "\n}\n"
    install_crawler_helper = "is_crawler_runtime_unit() {" + installer.split(
        "is_crawler_runtime_unit() {", 1
    )[1].split("\n}\n", 1)[0] + "\n}\n"
    prune = "for installed in /etc/systemd/system/mooncen-*.service" + installer.split(
        "for installed in /etc/systemd/system/mooncen-*.service", 1
    )[1].split("\n\nwhile IFS= read -r unit_name; do", 1)[0]
    prune = prune.replace("/etc/systemd/system", str(systemd_dir))
    manifest = tmp_path / "manifest"
    manifest.write_text("mooncen-api.service\n", encoding="utf-8")
    stale_unit = systemd_dir / "mooncen-retired.service"
    stale_unit.write_text("[Unit]\n", encoding="utf-8")
    prune_probe = subprocess.run(
        [_bash() or "bash", "-s"],
        input=(
            "set -euo pipefail\n"
            "crawler_runtime_enabled=0\n"
            f"manifest={manifest!s}\n"
            "systemctl() { :; }\n"
            "sudo() { \"$@\"; }\n"
            f"{install_crawler_helper}{install_transport_helper}{prune}\n"
        ),
        capture_output=True,
        text=True,
        check=False,
    )
    assert prune_probe.returncode == 0, prune_probe.stderr
    assert not stale_unit.exists()
    assert (systemd_dir / "mooncen-an2p-deploy-sshd.service").is_file()
    assert (transport_dropin / "override.conf").is_file()


def test_fixed_action_runner_can_import_without_the_retired_dashboard() -> None:
    script = (
        "import os,sys; "
        "root=sys.argv[1]; os.environ['MOONCEN_APP_DIR']=root; sys.path.insert(0,root); "
        "from tools import ops_service_action as action; "
        "assert 'ollama-test' in action.ACTION_ACCOUNT_ENV; "
        "assert not hasattr(action, '_dashboard_module')"
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-c", script, str(ROOT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_release_preflight_requires_staging_promotion_runtime_files() -> None:
    deploy = _text(DEPLOY)

    assert "deploy/ubuntu/ops_service_helper.sh" in deploy
    assert "tools/apply_staging_batch.py" in deploy


def test_ci_smoke_tests_the_real_git_archive() -> None:
    workflow = _text(ROOT / ".github" / "workflows" / "ci.yml")

    assert "Smoke-test immutable production archive" in workflow
    assert "git archive --format=tar.gz" in workflow
    assert "tools/ops_dashboard.py" not in workflow
    assert "tools/ops_dashboard.html" not in workflow
    assert "Production archive contains a symbolic link" in workflow
    assert "Production archive contains a forbidden local or sensitive file" in workflow
    assert "config/deploy_servers.json" in workflow
    assert "action._dashboard_module()" not in workflow


def test_release_switch_and_rollback_fail_closed_until_units_exit() -> None:
    deploy = _text(DEPLOY)
    activation = _embedded_script(deploy, "activateReleaseScript")
    guard = _text(RELEASE_GUARD)

    for script, label in ((activation, "release switch"), (guard, "recovery")):
        assert 'systemctl stop "${' in script
        assert "systemctl is-active --quiet" in script
        assert 'systemctl show "$unit_name" -p MainPID --value' in script
        assert (
            f"refusing {label} while unit remains active" in script
            if label == "release switch"
            else "unit remains active during recovery" in script
        )
        stop_command = "sudo systemctl stop" if "sudo systemctl stop" in script else "systemctl stop"
        stop_block = script.split(stop_command, 1)[1].split("fi", 1)[0]
        assert "|| true" not in stop_block


def test_failed_release_restores_previously_active_units() -> None:
    deploy = _text(DEPLOY)
    activation = _embedded_script(deploy, "activateReleaseScript")
    rollback = _embedded_script(deploy, "rollbackReleaseScript")
    guard = _text(RELEASE_GUARD)

    assert 'previously_active_units+=("$unit_name")' in activation
    assert 'sudo tee "$lock_dir/active-units"' in activation
    assert 'sudo "$guard" set-phase "$lock_dir" "$lock_token" activating' in activation
    assert 'sudo "$guard" set-phase "$lock_dir" "$lock_token" activated' in activation
    assert 'recover "$lock_dir" "$lock_token"' in rollback
    assert "stat -c '%U:%G:%a' \"$lock_dir/active-units\"" in guard
    assert 'systemctl start "${units[@]}"' in guard
    recovery = guard.split("recover_release() {", 1)[1].split(
        "bootstrap_value() {", 1
    )[0]
    assert recovery.index("systemctl daemon-reload") < recovery.index(
        'restore_active_units_authorized "$lock_dir" "$token"'
    )
    authorization = guard.split("restore_active_units_authorized() {", 1)[1].split(
        "validate_lock() {", 1
    )[0]
    assert authorization.index("publish_native_start_authorization") < authorization.index(
        'restore_active_units "$lock_dir"'
    ) < authorization.index("clear_native_start_authorization")
    assert "Durable remote guard restored the previous MoonCen release." in rollback


def test_release_guard_preserves_recovery_releases_and_bounded_history() -> None:
    deploy = _text(DEPLOY)
    guard = _text(RELEASE_GUARD)

    assert "HISTORY_KEEP=5" in guard
    assert 'preserve_directory "$PREVIOUS_DIR" "$entry/previous"' in guard
    assert 'preserve_directory "$FAILED_DIR" "$recovered_entry/failed"' in guard
    assert 'install -o root -g root -m 0600 "$REMOTE_DIR/.deploy-info" "$provenance_stage"' in guard
    assert 'mv -fT -- "$provenance_stage" "$provenance_target"' in guard
    assert 'sudo "$guard" commit "$lock_dir" "$lock_token"' in deploy
    assert 'sudo rm -rf -- "$previous_dir"' not in deploy
    assert 'durable deployment guard owns this lock; refusing local cleanup' in deploy


@pytest.mark.skipif(_bash() is None, reason="bash unavailable")
def test_embedded_release_scripts_pass_bash_syntax_check(tmp_path: Path) -> None:
    deploy = _text(DEPLOY)
    variables = (
        "extractReleaseScript",
        "armReleaseGuardScript",
        "activateReleaseScript",
        "rollbackReleaseScript",
        "installUnitsScript",
        "installNginxScript",
        "finalizeReleaseScript",
        "lockAndCleanupScript",
        "unlockScript",
        "crawlerDrainCheckScript",
    )
    paths: list[Path] = []
    for variable in variables:
        path = tmp_path / f"{variable}.sh"
        path.write_text(_embedded_script(deploy, variable), encoding="utf-8")
        paths.append(path)

    subprocess.run([_bash(), "-n", *map(str, paths)], cwd=ROOT, check=True, capture_output=True, text=True)
    subprocess.run(
        [_bash(), "-n", str(RELEASE_GUARD)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [_bash(), "-n", str(RELEASE_PREBUILD)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_release_guard_mutable_inventory_is_an_exact_reviewed_allowlist() -> None:
    guard = _text(RELEASE_GUARD)
    ids = _bash_array(guard, "MUTABLE_ARTIFACT_IDS")
    paths = _bash_array(guard, "MUTABLE_ARTIFACT_PATHS")
    policies = _bash_array(guard, "MUTABLE_ARTIFACT_POLICIES")

    assert len(ids) == len(paths) == len(policies)
    assert len(ids) == len(set(ids))
    assert len(paths) == len(set(paths))
    assert dict(zip(paths, policies, strict=True)) == {
        "/etc/mooncen/api.env": "file",
        "/etc/mooncen/frontend.env": "file",
        "/etc/mooncen/ai.env": "file",
        "/etc/mooncen/container-api.env": "file",
        "/etc/mooncen/container-ai.env": "file",
        "/etc/mooncen/container-migrator.env": "file",
        "/etc/mooncen/container-frontend-runtime-config.js": "file",
        "/etc/mooncen/bot.env": "file",
        "/etc/mooncen/applier.env": "file",
        "/etc/mooncen/functional-test.env": "file",
        "/etc/mooncen/gate.env": "file",
        "/etc/mooncen/backup.env": "file",
        "/etc/mooncen/db-root-ca.crt": "file",
        "/etc/postgresql/16/main/pg_hba.conf": "file-postgres",
        "/etc/cloudflared/token": "file",
        "/etc/mooncen-node-role": "file",
        "/etc/tmpfiles.d/mooncen-backup-restore-lock.conf": "file",
        "/etc/sudoers.d/mooncen-deploy": "file",
        "/etc/sudoers.d/mooncen-bot-db-status": "file",
        "/usr/local/bin/mooncenctl": "file",
        "/usr/local/libexec/mooncen-cloudflared-token": "file",
        "/usr/local/libexec/mooncen-ops-service": "file",
        "/usr/local/libexec/mooncen-ops-service-action.py": "file",
        "/usr/local/libexec/mooncen-postgres-role": "file",
        "/usr/local/libexec/mooncen-configure-container-pg-hba": "file",
        "/usr/local/libexec/mooncen-native-runtime-condition": "file",
        "/usr/local/libexec/mooncen-container-bootstrap": "file",
        "/usr/local/libexec/production_runtime_integrity.py": "file",
        "/usr/local/libexec/mooncen-container-release": "file",
        "/usr/local/libexec/mooncen-container-release-lib": "tree",
        "/etc/mooncen/container-bootstrap.json": "file",
        "/etc/mooncen/an2p-dev-target-identity": "file",
        "/etc/mooncen/container-runtime-installation.json": "file",
        "/usr/local/libexec/mooncen-bot": "tree",
        "/usr/local/libexec/mooncen-backup": "tree",
        "/usr/local/libexec/mooncen-ha": "tree",
        "/etc/nginx/sites-available/mooncen.conf": "file",
        "/etc/nginx/sites-enabled/mooncen.conf": "linkable",
        "/etc/nginx/sites-enabled/default": "linkable",
        "/etc/mooncen": "metadata-root",
        "/etc/cloudflared": "metadata-root",
        "/usr/local/libexec": "metadata-root",
        "/var/log/mooncen": "metadata-any",
    }

    assert '"$SERVICE_CONFIG_DIR"/*.env' not in _text(SETUP)
    assert "validate_restored_mutable_artifacts" in guard
    backup = guard.split("backup_mutable_artifacts()", 1)[1].split(
        "validate_mutable_artifact_backup()",
        1,
    )[0]
    assert "printf '%s|metadata|%s|%s|%s|-\\n'" in backup
    restore = guard.split("restore_mutable_artifacts()", 1)[1].split(
        "cleanup_mutable_artifact_backup()",
        1,
    )[0]
    assert 'validate_restored_mutable_artifacts "$lock_dir"' in restore
    assert "reload_restored_postgresql_hba" in restore
    assert 'nginx -t >/dev/null 2>&1 || die "restored nginx configuration is invalid"' in restore

    finish = guard.split("finish_lock()", 1)[1].split("backup_systemd_units()", 1)[0]
    assert 'cleanup_mutable_artifact_backup "$lock_dir"' in finish
    assert "mutable-artifacts-before-deploy" not in finish
    assert finish.index('cleanup_mutable_artifact_backup "$lock_dir"') < finish.index(
        'disable_guard_unit "$token"'
    )


def test_guard_template_install_is_transactional_and_recovery_exists_before_prebuild() -> None:
    deploy = _text(DEPLOY)
    guard = _text(RELEASE_GUARD)
    arm = _embedded_script(deploy, "armReleaseGuardScript")

    install_guard = 'sudo install -o root -g root -m 0700 "$guard_source" "$lock_dir/guard.sh"'
    bootstrap_guard = 'sudo "$lock_dir/guard.sh" bootstrap'
    arm_guard = 'sudo "$lock_dir/guard.sh" arm'
    assert arm.index("trap recover_failed_arm EXIT") < arm.index(install_guard)
    assert arm.index(install_guard) < arm.index(bootstrap_guard)
    assert arm.index(bootstrap_guard) < arm.index(arm_guard)
    assert 'sudo "$lock_dir/guard.sh" recover "$lock_dir" "$lock_token"' in arm
    assert 'sudo "$lock_dir/guard.sh" abort-bootstrap "$lock_dir" "$lock_token"' in arm

    guarded_arm = guard.split("arm_guard()", 1)[1].split("finalize_commit_release()", 1)[0]
    install_unit = 'install -o root -g root -m 0644 "$unit_source" "$unit_stage"'
    publish_unit = 'mv -fT -- "$unit_stage" "$unit_target"'
    enable_guard = 'systemctl enable "$guard_unit"'
    start_guard = 'systemctl start --no-block "$guard_unit"'
    publish_journal = 'mv -fT -- "$temporary" "$lock_dir/journal.env"'
    assert guarded_arm.index("flock -x 9") < guarded_arm.index('load_bootstrap "$lock_dir" "$token"')
    assert guarded_arm.index(install_unit) < guarded_arm.index(publish_unit)
    assert guarded_arm.index(publish_unit) < guarded_arm.index(enable_guard) < guarded_arm.index(start_guard)
    assert guarded_arm.index(start_guard) < guarded_arm.index('backup_mutable_artifacts "$lock_dir"')
    assert guarded_arm.index('backup_mutable_artifacts "$lock_dir"') < guarded_arm.index(publish_journal)
    assert 'durability_barrier "$unit_stage"' in guarded_arm
    assert "durability_barrier /etc/systemd/system" in guarded_arm

    backup_units = guard.split("backup_systemd_units()", 1)[1].split(
        "restore_systemd_configuration()",
        1,
    )[0]
    assert "/etc/systemd/system/cloudflared.service" in backup_units
    assert "mooncen-deploy-guard@" not in backup_units
    restore_systemd = guard.split("restore_systemd_configuration()", 1)[1].split(
        "prune_history()",
        1,
    )[0]
    assert 'parent_mode="$(stat -c \'%a\' /etc/systemd/system)"' in restore_systemd
    assert "(8#$parent_mode & 8#022) == 0" in restore_systemd
    prepared_recovery = guard.split(
        '  if [ "$PHASE" = recovering_prepared ]; then', 1
    )[1].split("      return 0", 1)[0]
    assert prepared_recovery.index('restore_systemd_configuration "$lock_dir"') < prepared_recovery.index(
        'restore_mutable_artifacts "$lock_dir"'
    )
    assert prepared_recovery.index('restore_mutable_artifacts "$lock_dir"') < prepared_recovery.index(
        'restore_active_units_authorized "$lock_dir" "$token"'
    )
    authorized_restore = guard.split("restore_active_units_authorized()", 1)[1].split(
        "validate_lock()",
        1,
    )[0]
    assert authorized_restore.index(
        'publish_native_start_authorization "$lock_dir" "$token" recovery'
    ) < authorized_restore.index('( restore_active_units "$lock_dir" )')
    assert authorized_restore.index('( restore_active_units "$lock_dir" )') < authorized_restore.index(
        'clear_native_start_authorization "$token"'
    )

    rollback_definition = deploy.index("$rollbackReleaseScript = @'")
    rollback_ready = deploy.index("$rollbackReleaseScript = $rollbackReleaseScript.Replace")
    prebuild = deploy.index("Invoke-RemoteWithInput $prebuildCommand $prebuildInput")
    assert rollback_definition < rollback_ready < prebuild
    rollback = _embedded_script(deploy, "rollbackReleaseScript")
    assert 'if ! sudo test -e "$lock_dir" && ! sudo test -L "$lock_dir"; then' in rollback
    assert '! sudo test -f "$lock_dir/guard.sh" || sudo test -L "$lock_dir/guard.sh"' in rollback
    assert rollback.count('echo "durable deployment guard already completed recovery"') == 2
    guard_check = rollback.index('if ! sudo test -f "$lock_dir/guard.sh"')
    race_check = rollback.index('if ! sudo test -e "$lock_dir"', guard_check)
    unsafe = rollback.index('echo "automatic durable release rollback is unavailable or unsafe"', race_check)
    assert guard_check < race_check < unsafe

    activation = _embedded_script(deploy, "activateReleaseScript")
    assert '! sudo test -f "$guard" || sudo test -L "$guard"' in activation
    assert '[ ! -f "$guard" ] || [ -L "$guard" ]' not in activation

    finalize = _embedded_script(deploy, "finalizeReleaseScript")
    assert '! sudo test -f "$guard" || sudo test -L "$guard"' in finalize
    assert 'if sudo test -e "$lock_dir" || sudo test -L "$lock_dir"; then' in finalize
    assert 'sudo test -f "$guard" && ! sudo test -L "$guard"' in finalize

    unlock = _embedded_script(deploy, "unlockScript")
    assert 'if ! sudo test -d "$lock_dir" || sudo test -L "$lock_dir"; then' in unlock
    assert 'sudo test -e "$lock_dir/journal.env"' in unlock
    assert 'sudo test -e "$lock_dir/guard.sh"' in unlock
    assert 'if ! sudo test -f "$preflight" || sudo test -L "$preflight"' in unlock


def test_standard_deploy_fails_closed_on_remote_database_credential_mismatch() -> None:
    deploy = _text(DEPLOY)
    validator = deploy.split(
        "function Assert-UnchangedRemoteDatabaseCredential",
        1,
    )[1].split("function Mask-SensitiveText", 1)[0]

    assert "[string]::Equals($RemoteValue, $CandidateValue, [StringComparison]::Ordinal)" in validator
    assert "Standard deployment cannot rotate database credentials" in validator
    for output_command in ("Write-Host", "Write-Output", "Write-Warning", "echo"):
        assert output_command not in validator

    owner_lookup = deploy.index('$remoteDbPassword = Get-RemoteEnvValue "DB_PASSWORD"')
    owner_check = deploy.index(
        "Assert-UnchangedRemoteDatabaseCredential `",
        owner_lookup,
    )
    owner_fallback = deploy.index("if (-not $DbPassword)", owner_check)
    assert owner_lookup < owner_check < owner_fallback

    runtime_loop = deploy.split("foreach ($item in $runtimePasswords) {", 1)[1].split("}\n", 1)[0]
    assert runtime_loop.index("$remoteValue = Get-RemoteEnvValue") < runtime_loop.index(
        "Assert-UnchangedRemoteDatabaseCredential `"
    )
    assert runtime_loop.index("Assert-UnchangedRemoteDatabaseCredential `") < runtime_loop.index(
        "if (-not $currentValue)"
    )

    first_remote_mutation = deploy.index("Invoke-RemoteBashScriptTty $lockAndCleanupScript")
    assert owner_check < first_remote_mutation
    assert deploy.index(
        "Assert-UnchangedRemoteDatabaseCredential `",
        owner_check + 1,
    ) < first_remote_mutation


def test_privileged_config_writers_reject_symlinks_and_publish_atomically() -> None:
    setup = _text(SETUP)
    deploy = _text(DEPLOY)
    mooncenctl = setup.split("mooncenctl_source=", 1)[1].split(
        'if [ -f "$APP_DIR/deploy/ubuntu/install_sudoers.sh" ]',
        1,
    )[0]

    assert "mooncenctl_target=/usr/local/bin/mooncenctl" in mooncenctl
    assert "mooncenctl_stage=/usr/local/bin/.mooncenctl.$$" in mooncenctl
    assert '[ -f "$mooncenctl_source" ] && [ ! -L "$mooncenctl_source" ]' in mooncenctl
    assert 'sudo test -e "$mooncenctl_target" || sudo test -L "$mooncenctl_target"' in mooncenctl
    assert 'sudo test -f "$mooncenctl_target" && ! sudo test -L "$mooncenctl_target"' in mooncenctl
    assert 'sudo install -o root -g root -m 0755 "$mooncenctl_source" "$mooncenctl_stage"' in mooncenctl
    assert 'sudo mv -fT -- "$mooncenctl_stage" "$mooncenctl_target"' in mooncenctl
    assert 'sudo cp "$APP_DIR/deploy/ubuntu/mooncenctl.sh" /usr/local/bin/mooncenctl' not in setup

    nginx = _embedded_script(deploy, "installNginxScript")
    assert '[ -f "$nginx_source" ] && [ ! -L "$nginx_source" ]' in nginx
    assert '[ -e "$nginx_target" ] || [ -L "$nginx_target" ]' in nginx
    assert '[ -f "$nginx_target" ] && [ ! -L "$nginx_target" ]' in nginx
    assert re.search(
        r"^nginx_stage=['\"]?/etc/nginx/sites-available/",
        nginx,
        re.MULTILINE,
    )
    assert re.search(
        r"^nginx_link_stage=['\"]?/etc/nginx/sites-enabled/",
        nginx,
        re.MULTILINE,
    )
    assert 'sudo install -o root -g root -m 0644 "$nginx_source" "$nginx_stage"' in nginx
    assert 'sudo mv -fT -- "$nginx_stage" "$nginx_target"' in nginx
    assert 'sudo ln -s -- "$nginx_target" "$nginx_link_stage"' in nginx
    assert 'sudo mv -fT -- "$nginx_link_stage" "$nginx_enabled"' in nginx
    assert '[ -e "$nginx_default" ] || [ -L "$nginx_default" ]' in nginx
    assert 'sudo nginx -t' in nginx
    assert nginx.index('sudo mv -fT -- "$nginx_link_stage" "$nginx_enabled"') < nginx.index(
        "sudo nginx -t"
    )
    assert "sudo cp '$RemoteDir'/deploy/ubuntu/nginx/" not in deploy
    assert "sudo ln -sf /etc/nginx/sites-available/mooncen.conf" not in deploy
