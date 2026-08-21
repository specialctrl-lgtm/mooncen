from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR = ROOT / "start_development_autostart.ps1"
INSTALLER = ROOT / "install_development_autostart.ps1"
OPS_LAUNCHER = ROOT / "start_ops_console.ps1"
DOC = ROOT / "docs" / "development-autostart.md"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_autostart_uses_one_ordered_supervisor_and_shared_api() -> None:
    source = _source(SUPERVISOR)
    ensure_body = source[source.index("function Ensure-DevelopmentServices") :]
    assert ensure_body.index("Ensure-Ops") < ensure_body.index("Ensure-Frontend")
    assert '"-FrontendOnly"' in source
    assert '"-ApiPort", "$apiPort"' in source
    assert '"-FrontendPort", "$frontendPort"' in source
    assert "EnableLocalCrawlerRuntime" not in source
    assert "Development web server will not start before the shared Ops API is healthy" in source


def test_boot_task_contract_is_prelogin_singleton_and_has_no_secret_argument() -> None:
    source = _source(INSTALLER)
    assert "New-ScheduledTaskTrigger -AtStartup" in source
    assert '$trigger.Delay = "PT1M"' in source
    assert "-LogonType Password" in source
    assert "-RunLevel Limited" in source
    assert "-MultipleInstances IgnoreNew" in source
    assert "-StartWhenAvailable" in source
    assert "-RestartCount 10" in source
    assert "-AllowStartIfOnBatteries" in source
    assert "-DontStopIfGoingOnBatteries" in source
    assert "-ExecutionTimeLimit ([TimeSpan]::Zero)" in source
    assert "Task.Settings.Enabled" in source
    assert "function Wait-TaskStopped" in source
    assert "New-ScheduledTaskTrigger -AtLogOn" not in source
    assert "InteractiveToken" not in source
    assert "S4U" not in source
    assert "-EnableLocalCrawlerRuntime" not in source
    task_arguments = source[source.index("function Get-SupervisorArguments") : source.index("function Get-ExactTask")]
    assert "Password" not in task_arguments
    assert "Secret" not in task_arguments


def test_installer_is_exact_idempotent_and_refuses_foreign_task() -> None:
    source = _source(INSTALLER)
    assert 'Where-Object { $_.TaskName -ceq $TaskName -and $_.TaskPath -ceq $taskPath }' in source
    assert "is not owned by this MoonCen workspace" in source
    assert "-Force | Out-Null" in source
    assert "Startup task is already absent" in source
    assert "Unregister-ScheduledTask -TaskName $TaskName -TaskPath $taskPath" in source
    assert not re.search(r"Unregister-ScheduledTask[^\n]*['\"]\*", source)
    assert "Invoke-ElevatedSelf" in source
    assert "-Verb RunAs" in source
    assert "-EncodedCommand $encoded" in source
    assert "ConvertTo-Json -Compress" in source
    assert "[Text.Encoding]::UTF8.GetBytes($payloadJson)" in source
    assert "@childArguments" in source
    assert "-ElevationAttempted" in source
    assert "Invoke-Expression" not in source
    assert "Administrator approval was canceled" in source
    assert "-NonInteractive" not in source[source.index("function Invoke-ElevatedSelf") : source.index("function Resolve-AccountSid")]


def test_supervisor_has_bounded_recovery_logs_and_strict_process_ownership() -> None:
    source = _source(SUPERVISOR)
    assert "[IO.FileShare]::None" in source
    assert "5242880" in source
    assert "for ($index = 3; $index -ge 1; $index--)" in source
    assert "[Math]::Min($MaxRetrySec" in source
    assert "Get-Random -Minimum 0" in source
    assert "Test-ProcessStartTime" in source
    assert "Test-ProcessDescendsFrom" in source
    assert "Test-FrontendProcess" in source
    assert "Assert-NoUntrackedOpsProcesses" in source
    assert "An untracked MoonCen Ops process is running" in source
    assert "Test-ActiveOpsDeployment" in source
    assert "Ops repair is deferred without stopping it" in source
    assert "occupied by an unrecognized process; no process was stopped" in source
    assert "Deployment worker heartbeat is stale" in source
    assert "Stop-MatchingDevProcesses" not in source


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell runtime is Windows-only")
def test_supervisor_atomic_state_and_launcher_work_on_windows_powershell_51(tmp_path: Path) -> None:
    powershell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    probe = r'''
$ErrorActionPreference = "Stop"
$sourcePath = $env:MOONCEN_SUPERVISOR_SOURCE
$errors = $null
$tokens = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($sourcePath, [ref]$tokens, [ref]$errors)
if ($errors.Count -gt 0) { throw "supervisor parse failed" }
function Get-FunctionText([string]$Name) {
    $node = @($ast.FindAll({
        param($candidate)
        $candidate -is [Management.Automation.Language.FunctionDefinitionAst] -and
            $candidate.Name -ceq $Name
    }, $true) | Select-Object -First 1)
    if ($node.Count -ne 1) { throw "missing function: $Name" }
    return $node[0].Extent.Text
}
Invoke-Expression (Get-FunctionText "Write-AtomicJson")
Invoke-Expression (Get-FunctionText "ConvertTo-NativeArgument")
Invoke-Expression (Get-FunctionText "Invoke-Launcher")

$utf8NoBom = New-Object Text.UTF8Encoding($false)
$stateDir = $env:MOONCEN_SUPERVISOR_TEST_DIR
$root = $stateDir
$powerShellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
function Write-AutostartEvent { param($Level, $Message) }

$statePath = Join-Path $stateDir "state.json"
Write-AtomicJson -Path $statePath -Value ([ordered]@{ generation = 1 })
Write-AtomicJson -Path $statePath -Value ([ordered]@{ generation = 2 })
$state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
if ([int]$state.generation -ne 2) { throw "second atomic state write was not durable" }
if (@(Get-ChildItem -LiteralPath $stateDir -File | Where-Object { $_.Name -like "state.json.*" }).Count -ne 0) {
    throw "atomic state residue remains"
}

$okPath = Join-Path $stateDir "ok.ps1"
$failPath = Join-Path $stateDir "fail.ps1"
[IO.File]::WriteAllText($okPath, "exit 0`n", $utf8NoBom)
[IO.File]::WriteAllText($failPath, "exit 7`n", $utf8NoBom)
Invoke-Launcher -ScriptPath $okPath -Arguments @() -Label "success probe"
$failedAsExpected = $false
try {
    Invoke-Launcher -ScriptPath $failPath -Arguments @() -Label "failure probe"
}
catch {
    if ($_.Exception.Message -ceq "failure probe failed with exit code 7.") {
        $failedAsExpected = $true
    }
    else {
        throw
    }
}
if (-not $failedAsExpected) { throw "nonzero launcher exit was not propagated" }
'''
    environment = os.environ.copy()
    environment["MOONCEN_SUPERVISOR_SOURCE"] = str(SUPERVISOR)
    environment["MOONCEN_SUPERVISOR_TEST_DIR"] = str(tmp_path)
    result = subprocess.run(
        [
            str(powershell),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            probe,
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_cloud_ssh_is_explicit_noninteractive_and_threaded_into_ops_launcher() -> None:
    supervisor = _source(SUPERVISOR)
    ops = _source(OPS_LAUNCHER)
    for token in (
        "IdentitiesOnly=yes",
        "BatchMode=yes",
        "StrictHostKeyChecking=yes",
        "UpdateHostKeys=no",
        "PasswordAuthentication=no",
        "KbdInteractiveAuthentication=no",
        "NumberOfPasswordPrompts=0",
    ):
        assert token in supervisor
    assert "Cloud autostart requires -SshIdentityFile" in supervisor
    assert "$Label identity file must be stored outside the MoonCen repository" in supervisor
    assert "$Label identity file ACL identity could not be resolved" in supervisor
    assert '"-SshIdentityFile", $script:SshIdentityPath' in supervisor
    assert '[string]$SshIdentityFile = ""' in ops
    assert "Get-SshIdentityArguments" in ops
    assert "IdentitiesOnly=yes" in ops


def test_crawler_control_autostart_is_explicit_and_propagated() -> None:
    supervisor = _source(SUPERVISOR)
    installer = _source(INSTALLER)

    for source in (supervisor, installer):
        assert '[string]$CrawlerControlSshTarget = "sgm@gen1db"' in source
        assert '[string]$CrawlerControlSshIdentityFile = ""' in source
    assert '"-CrawlerControlSshTarget", $CrawlerControlSshTarget' in supervisor
    assert '"-CrawlerControlSshIdentityFile", $script:CrawlerControlSshIdentityPath' in supervisor
    assert '"-CrawlerControlSshTarget", $CrawlerControlSshTarget' in installer
    assert '"-CrawlerControlSshIdentityFile", (Quote-TaskArgument $crawlerIdentityPath)' in installer
    assert '$opsPorts += 15433' in supervisor


def test_running_task_argument_transition_fails_before_registration() -> None:
    installer = _source(INSTALLER)
    install_block = installer.split('"Install" {', 1)[1].split('"Uninstall" {', 1)[0]

    guard = "The running startup task uses different arguments"
    assert guard in install_block
    assert "$StartNow" in install_block
    assert '-cne $requestedTaskArguments' in install_block
    assert "No task change was made" in install_block
    assert "-Action Stop -DataSource Cloud" in install_block
    assert "omit -StartNow and reboot" in install_block
    assert install_block.index(guard) < install_block.index("Register-ScheduledTask")


def test_docs_describe_real_boot_semantics_and_reboot_verification() -> None:
    source = _source(DOC)
    assert "AtStartup" in source
    assert "60-second delay" in source
    assert "`Password` logon" in source
    assert "Do not register `start_ops_console.ps1` and `start_dev.ps1` as separate" in source
    assert "verify one actual reboot" in source
    assert "Do not put the key" in source


def test_docs_require_gen1db_key_to_allow_only_the_database_forward() -> None:
    source = _source(DOC)

    exact_constraint = (
        'restrict,port-forwarding,command="/usr/bin/false",'
        'permitopen="127.0.0.1:5432" '
        "ssh-ed25519 PUBLIC_KEY mooncen-crawler-control-ops"
    )
    assert exact_constraint in source
    assert "Do not authorize this key without the constraint" in source
    assert "`restrict` plus `permitopen` alone still permits an" in source
    assert "`/usr/bin/false` is required to make the key forward-only" in source


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell parser is Windows-only")
def test_autostart_powershell_files_parse() -> None:
    powershell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    script = r"""
$failed = $false
foreach ($path in ($env:MOONCEN_AUTOSTART_PARSE_FILES -split ';')) {
    $errors = $null
    $tokens = $null
    [void][Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors)
    if ($errors.Count -gt 0) {
        $failed = $true
        $errors | ForEach-Object { [Console]::Error.WriteLine($_.Message) }
    }
}
if ($failed) { exit 1 }
"""
    environment = os.environ.copy()
    environment["MOONCEN_AUTOSTART_PARSE_FILES"] = ";".join(
        str(path) for path in (SUPERVISOR, INSTALLER, OPS_LAUNCHER)
    )
    result = subprocess.run(
        [
            str(powershell),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
