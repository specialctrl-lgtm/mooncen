from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _launcher() -> str:
    return (ROOT / "start_ops_console.ps1").read_text(encoding="utf-8")


def test_ops_console_defaults_to_fixed_production_cloud_tunnel() -> None:
    launcher = _launcher()

    assert '[ValidateSet("Cloud", "Local")]' in launcher
    assert '[string]$DataSource = "Cloud"' in launcher
    assert '[string]$SshTarget = "ubuntu@cloud"' in launcher
    assert "$cloudSshTarget = $SshTarget" in launcher
    assert '$cloudTunnelForward = "127.0.0.1:18001:127.0.0.1:8001"' in launcher
    assert '$cloudDbTunnelForward = "127.0.0.1:15432:127.0.0.1:5432"' in launcher
    assert '$crawlerControlDbTunnelForward = "127.0.0.1:15433:127.0.0.1:5432"' in launcher
    assert '[string]$CrawlerControlSshTarget = "sgm@gen1db"' in launcher
    assert '[string]$CrawlerControlSshIdentityFile = ""' in launcher
    assert '$env:VITE_OPS_API_PROXY_TARGET = "http://127.0.0.1:8001"' in launcher
    assert '"-o", "BatchMode=yes"' in launcher
    assert '"-o", "ExitOnForwardFailure=yes"' in launcher
    assert '"-o", "StrictHostKeyChecking=yes"' in launcher
    assert '"-L", $cloudTunnelForward' in launcher
    assert '"-L", $cloudDbTunnelForward' in launcher


def test_crawler_control_pool_is_explicit_separate_and_api_only() -> None:
    launcher = _launcher()

    assert '$crawlerControlEnvironmentEnabled = [bool]$CrawlerControlSshIdentityFile' in launcher
    assert 'Get-CrawlerControlSshIdentityArguments' in launcher
    assert '"-L", $crawlerControlDbTunnelForward' in launcher
    assert 'New-ManagedProcessEntry "crawler-control-ssh-tunnel" $crawlerTunnel' in launcher
    assert '"`$HOME/.config/mooncen/deploy-secrets.env"' in launcher
    assert '$value = (& $SshExecutable @sshArguments $cloudSshTarget' in launcher
    assert '"OPS_CRAWLER_API_DB_REQUIRED"] = "true"' in launcher
    assert '"OPS_CRAWLER_SHARED_DB_HOST"] = $crawlerControlDbTunnelAddress' in launcher
    assert '"OPS_CRAWLER_API_DB_PASSWORD"] = $crawlerValues[' in launcher

    assert "Worker = @{" not in launcher
    assert "Crawler-control SSH must use a separate identity file" in launcher
    assert "crawler_control_analytics_enabled" in launcher
    assert "crawler_control_ssh_target" in launcher


def test_crawler_control_pool_is_disabled_by_default() -> None:
    launcher = _launcher()

    assert '[string]$CrawlerControlSshIdentityFile = ""' in launcher
    required_ports = launcher.split("$requiredPorts = if", 1)[1].split(
        "foreach ($port in $requiredPorts)", 1
    )[0]
    assert "if ($crawlerControlEnvironmentEnabled)" in required_ports
    assert "$ports += $crawlerControlDbTunnelPort" in required_ports


def test_cloud_mode_starts_local_control_plane_for_the_production_database() -> None:
    launcher = _launcher()
    start_function = launcher.split("function Start-OpsConsole {", 1)[1].split(
        "function Refresh-OpsControl {", 1
    )[0]

    assert 'if ($DataSource -eq "Cloud") {' in start_function
    assert 'Get-CloudControlEnvironment $ssh' in start_function
    assert 'New-ManagedProcessEntry "ssh-tunnel" $tunnel' in start_function
    assert 'New-ManagedProcessEntry "api" $api' in start_function
    assert 'New-ManagedProcessEntry "deployment-worker"' not in start_function
    assert 'name = "status-agent"' in start_function
    assert 'New-ManagedProcessEntry "console" $console' in start_function
    assert 'if ($DataSource -eq "Local" -and $EnableLocalCrawlerRuntime)' in start_function
    assert '$apiEnvironment = if ($DataSource -eq "Cloud") { $cloudControlEnvironment.Api } else { @{} }' in start_function
    assert '$apiStandardOutputLog $apiStandardErrorLog' in start_function
    assert '$cloudControlEnvironment.Worker' not in start_function
    assert 'if ($DataSource -eq "Local") {' in start_function
    assert (
        '-EnableLocalCrawlerRuntime is allowed only with -DataSource Local.'
        in launcher
    )


def test_tunnel_process_is_recorded_validated_stopped_and_reported() -> None:
    launcher = _launcher()

    assert "function Get-ManagedProcess([object]$Entry)" in launcher
    assert "function Stop-ManagedProcessTree([object]$Entry)" in launcher
    assert '"ssh-tunnel" = @($cloudTunnelPort, $cloudDbTunnelPort)' in launcher
    assert "Stop-ProcessTree $launcherProcessId $launcherStartedAt" in launcher
    assert 'Where-Object { [string]$_.Name -notin @("conhost.exe", "conhost") }' in launcher
    assert 'process_started_at = $Process.StartTime.ToUniversalTime().ToString("o")' in launcher
    assert 'data_source = $DataSource' in launcher
    assert 'api_proxy_target = $env:VITE_OPS_API_PROXY_TARGET' in launcher
    assert '$statusNames += "crawler-control-ssh-tunnel"' in launcher
    assert "foreach ($name in $statusNames)" in launcher
    assert "Stop-ManagedProcessTree $entry" in launcher
    assert "@(5175, 8001, $cloudTunnelPort, $cloudDbTunnelPort)" in launcher


def test_cmd_wrapper_forwards_data_source_arguments() -> None:
    wrapper = (ROOT / "start_ops_console.cmd").read_text(encoding="utf-8")

    assert 'start_ops_console.ps1" %*' in wrapper
    assert "exit /b %ERRORLEVEL%" in wrapper


def test_api_output_is_persisted_with_bounded_startup_rotation() -> None:
    launcher = _launcher()

    assert '$apiStandardOutputLog = Join-Path $stateDir "api.stdout.log"' in launcher
    assert '$apiStandardErrorLog = Join-Path $stateDir "api.stderr.log"' in launcher
    assert "function Rotate-BoundedLog(" in launcher
    assert "[long]$MaximumBytes = 5MB" in launcher
    assert "[int]$RetainedFiles = 3" in launcher
    assert launcher.count("Prepare-ApiLogs") >= 3
    assert 'api_stdout_log = $apiStandardOutputLog' in launcher
    assert 'api_stderr_log = $apiStandardErrorLog' in launcher


def test_session_zero_state_records_runtime_and_launcher_identities() -> None:
    launcher = _launcher()

    for field in (
        "launcher_pid",
        "launcher_started_at",
        "process_name",
        "listener_ports",
    ):
        assert field in launcher
    assert "function New-ListenerManagedProcessEntry(" in launcher
    assert (
        'New-ListenerManagedProcessEntry "api" $api @($cloudApiPort)'
        in launcher
    )
    assert '"deployment-worker" $deploymentWorker' not in launcher
    assert 'New-ListenerManagedProcessEntry "console" $console @(5175)' in launcher


def test_session_zero_identity_checks_do_not_depend_on_visible_command_lines() -> None:
    launcher = _launcher()

    assert "function Get-ProcessCreationTime([int]$ProcessId)" in launcher
    assert "$snapshot.CreationDate" in launcher
    assert "function Test-ProcessDescendsFrom(" in launcher
    assert "Test-ListenerRuntimeOwnership" in launcher
    assert "if ($commandLine) {" in launcher
    assert "Recorded $($Entry.name) PID $recordedId is running but its identity cannot be verified" in launcher


def test_stop_uses_verified_launcher_tree() -> None:
    launcher = _launcher()

    assert "function Stop-VerifiedProcess(" in launcher
    assert "Stop-ProcessTree $launcherProcessId $launcherStartedAt" in launcher
    stop_tree = launcher.split("function Stop-ProcessTree(", 1)[1].split(
        "function Stop-ManagedProcessTree", 1
    )[0]
    assert "ParentProcessId = $parentId" in stop_tree
    assert ".CommandLine" not in stop_tree
    assert "did not stop within 5 seconds" in launcher
    assert "deployment-worker" not in launcher
