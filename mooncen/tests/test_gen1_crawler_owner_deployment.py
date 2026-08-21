from __future__ import annotations

import json
from pathlib import Path

from ops_agent.deployment_registry import _validate_target


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_registry_declares_gen1crawler_as_current_crawler_only_target() -> None:
    registry = json.loads(_read("config/deploy_servers.example.json"))
    assert registry["defaultTarget"] == "cloud"
    assert registry["servers"]["cloud"]["deployProfile"] == "full-stack"
    assert registry["servers"]["cloud"]["environment"] == "production"

    crawler = registry["servers"]["gen1crawler"]
    assert crawler["server"] == "gen1crawler"
    assert crawler["role"] == "crawler"
    assert crawler["deployProfile"] == "crawler-only"
    assert crawler["environment"] == "production"
    assert crawler["active"] is False

    parsed = _validate_target("gen1crawler", crawler)
    assert parsed.role == "crawler"
    assert parsed.deploy_profile == "crawler-only"
    assert parsed.public_dict(key_ready=True)["deploy_profile"] == "crawler-only"
    assert parsed.public_dict(key_ready=True)["environment"] == "production"
    assert len(parsed.identity) == 64


def test_full_stack_deploy_never_targets_gen1crawler() -> None:
    source = _read("deploy_mooncen.ps1")

    assert "Target '$($targetConfig.Name)' is crawler-only" in source
    assert "use crawler-update to check the dedicated updater status" in source
    assert source.count(
        'Where-Object { $_.DeployProfile -eq "full-stack" }'
    ) == 2
    assert '"deploy_profile_b64=' in source
    assert '"environment_b64=' in source
    assert '@("development", "staging", "production") -notcontains $entry.Environment' in source
    assert '"dedicated"' in source


def test_crawler_activation_contacts_only_reviewed_gen1_owner() -> None:
    source = _read("deploy_mooncen.ps1")
    function = source.split("function Invoke-CurrentCrawlerActivation", 1)[1].split(
        "function Invoke-CrawlerControlInstall", 1
    )[0]

    assert '"crawler-activate"' in source
    assert 'Use -Target gen1crawler for this action.' in source
    assert '$targetConfig.Name -ne $crawlerTarget' in function
    assert '$targetConfig.DeployProfile -ne "crawler-only"' in function
    assert '"gen1crawler"' in function
    assert "--batch-id $BatchId" in function
    assert "cloud" not in function.lower()
    assert function.count("Invoke-RemoteForDeployServer") == 2
    assert "Invoke-RemoteBashScriptForDeployServer $targetConfig" in function


def test_crawler_release_update_status_is_explicitly_fail_closed() -> None:
    source = _read("deploy_mooncen.ps1")
    branch = source.split('"crawler-update" {', 1)[1].split(
        '"crawler-activate" {', 1
    )[0]

    assert "no transactional, provenance-verified gen1crawler release uploader" in branch
    assert "No remote change was attempted" in branch
    assert "Invoke-Remote" not in branch


def test_cloud_deploy_has_no_legacy_owner_guard_or_nonowner_unit_shutdown() -> None:
    source = _read("deploy/ubuntu/deploy_from_windows.ps1")
    setup = _read("deploy/ubuntu/setup_project.sh")

    assert "$legacyOwnershipGuardScript" not in source
    assert "crawler-cutover" not in source
    assert "export ENABLE_CRAWLER_STAGING='$([int][bool]$EnableCrawler)'" in source
    assert "is_crawler_runtime_unit" in source
    assert "Crawler runtime state belongs to gen1crawler" in source
    assert 'case "$ENABLE_CRAWLER_STAGING" in' in setup
    assert setup.count('if [ "$ENABLE_CRAWLER_STAGING" = "1" ]; then') >= 2
    scheduler_block = source.split(
        "if (-not $Standby -and $EnableCrawler -and -not $SkipWorkers)", 1
    )[1].split("if (-not $SkipWorkers -and -not $Standby)", 1)[0]
    assert "mooncen-crawler.timer" in scheduler_block
    assert "else" not in scheduler_block

    activation = source.split("$activateReleaseScript = @'", 1)[1].split("'@", 1)[0]
    assert "is_crawler_runtime_unit" in activation
    assert 'crawler_runtime_enabled=\'__ENABLE_CRAWLER__\'' in activation
    assert 'managed_units=("${non_crawler_units[@]}")' in activation


def test_cloud_release_guard_never_recovers_crawler_owned_state() -> None:
    guard = _read("deploy/ubuntu/mooncen_release_guard.sh")

    assert "is_crawler_runtime_unit_name" in guard
    assert "/etc/mooncen/crawler.env" not in guard
    assert "/etc/mooncen/staging-crawler-password" not in guard
    assert '$1 !~ /^mooncen-crawler/' in guard
    assert '$1 !~ /^mooncen-staging-apply/' in guard
    assert 'ignoring crawler-owned unit in legacy active journal' in guard


def test_cloudflared_primary_actions_skip_crawler_only_registry_targets() -> None:
    source = _read("deploy_mooncen.ps1")
    function = source.split(
        "function Disable-StandbyCloudflaredForActiveTarget", 1
    )[1].split("function Invoke-CurrentCrawlerActivation", 1)[0]

    assert '$item.DeployProfile -ne "full-stack"' in function
    assert 'Refusing cloudflared-stop on crawler-only target' in source


def test_cloud_runtime_controls_are_role_scoped_away_from_crawler_units() -> None:
    helper = _read("deploy/ubuntu/ops_service_helper.sh")
    control = _read("deploy/ubuntu/mooncenctl.sh")

    assert 'require_crawler_owner()' in helper
    assert 'if [ "$NODE_ROLE" = "crawler" ]; then' in helper
    assert 'restart-crawler) require_crawler_owner' in helper
    assert 'logs-crawler) require_crawler_owner' in helper
    assert 'is_crawler_owner()' in control
    assert control.count("if is_crawler_owner; then") >= 4


def test_multi_host_status_checks_crawler_units_only_on_crawler_profile() -> None:
    source = _read("deploy_mooncen.ps1")
    function = source.split("function Invoke-HaStatus", 1)[1].split(
        "switch ($Action)", 1
    )[0]
    general = function.split("$remoteCommand = @'", 1)[1].split("'@", 1)[0]
    crawler = function.split("$crawlerRemoteCommand = @'", 1)[1].split("'@", 1)[0]

    assert "mooncen-crawler" not in general
    assert "mooncen-crawler.timer" in crawler
    assert 'if ($item.DeployProfile -eq "crawler-only")' in function


def test_activation_is_single_owner_pinned_apply_then_timer_enable() -> None:
    source = _read("deploy/ubuntu/activate_split_crawler.sh")

    assert "--prepare" not in source
    assert "--commit" not in source
    assert "cloud-disabled" not in source
    assert "crawler-cutover" not in source
    assert "split_runtime_lock=/run/lock/mooncen-split-crawler.lock" in source
    assert "flock -n 9" in source
    assert "mooncen-staging-apply@*.service" in source
    assert "root:mooncen:750" in source
    assert "root:mooncen:640" in source

    dry_run = source.index('systemctl start "$dry_run_unit"')
    apply = source.index('systemctl start "$apply_unit"')
    validate = source.index("if ! validate_result_files", apply)
    enable = source.index('systemctl enable "${timer_units[@]}"')
    assert dry_run < apply < validate < enable


def test_activation_rollback_stops_triggered_services() -> None:
    source = _read("deploy/ubuntu/activate_split_crawler.sh")
    rollback = source.split("rollback_timers()", 1)[1].split(
        "trap rollback_timers EXIT", 1
    )[0]

    assert 'systemctl disable --now "${timer_units[@]}"' in rollback
    assert "systemctl stop mooncen-crawler-once.service mooncen-staging-apply.service" in rollback


def test_split_setup_is_owner_pinned_and_update_safe() -> None:
    source = _read("deploy/ubuntu/setup_split_crawler.sh")

    assert '"$short_hostname" != "gen1crawler"' in source
    assert "Refusing split crawler setup on node role" in source
    assert "split_runtime_lock=/run/lock/mooncen-split-crawler.lock" in source
    assert "flock -n 9" in source
    assert "mooncen-staging-apply@*.service" in source
    assert "mooncen-staging-apply-dry-run.service" in source
    assert "MainPID" in source
    assert "Unable to inspect active pinned staging units" in source
    assert "Unable to validate the production crawler provider registry" in source
    assert "grep -q '[[:cntrl:]]'" in source
    assert '[[ "$site_url" == *"\\\\"* ]]' in source
    assert 'app_env_tmp="$(mktemp "$APP_DIR/.env.XXXXXX")"' in source
    assert 'deploy_meta_tmp="$(mktemp "$APP_DIR/.deploy-meta.XXXXXX")"' in source
    assert 'chown -R root:mooncen "$APP_DIR"' in source


def test_docs_name_gen1_as_current_owner_and_limit_cloud_contact_to_database() -> None:
    multi = _read("docs/multi-server-deployment.md")
    split = _read("deploy/ubuntu/GEN1_SPLIT.md")

    assert "`gen1crawler` is the current runtime owner" in multi
    assert "never open SSH to cloud" in split
    assert "DNS host `cloud`" in split
    assert "crawler-activate" in multi and "crawler-activate" in split
    assert "crawler-update" in multi and "crawler-update" in split
    assert "fail-closed" in multi and "fail-closed" in split
    assert "crawler-cutover" not in multi and "crawler-cutover" not in split
    assert "cloud remains the crawler owner" not in multi
    assert "cloud remains the crawler owner" not in split
