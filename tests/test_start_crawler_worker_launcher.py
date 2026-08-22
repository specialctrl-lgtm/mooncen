from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "start_crawler_worker.ps1"


def _source() -> str:
    return LAUNCHER.read_text(encoding="utf-8")


def test_launcher_uses_verified_project_python_and_separate_logs() -> None:
    source = _source()

    assert '[string]$PythonPath = ""' in source
    for environment in ("venv_clean", "venv", ".venv"):
        assert (
            f'(Join-Path $projectRoot "{environment}\\Scripts\\python.exe")'
            in source
        )
    assert "A runnable Python 3.12 or 3.13 interpreter was not found" in source
    assert '-FilePath $pythonExe' in source
    assert '-FilePath "python"' not in source
    assert '-RedirectStandardOutput $stdoutLogFile' in source
    assert '-RedirectStandardError $stderrLogFile' in source
    assert '$stdoutLogFile = Join-Path $logDir "crawler_worker.log"' in source
    assert '$stderrLogFile = Join-Path $logDir "crawler_worker.error.log"' in source


def test_launcher_reports_success_only_after_child_pid_handshake() -> None:
    source = _source()

    start_index = source.index("$process = Start-Process")
    pid_match_index = source.index("$publishedPid -eq $process.Id")
    ready_index = source.index("$workerReady = $true", pid_match_index)
    success_index = source.index('Write-Host "Crawler worker started.')

    assert start_index < pid_match_index < ready_index < success_index
    assert "$process.HasExited" in source
    assert "Crawler worker exited before publishing its PID" in source
    assert "Crawler worker did not publish a matching PID" in source
    assert "Stop-UnreadyCrawlerProcess -Process $process" in source


@pytest.mark.skipif(
    os.name != "nt" or not shutil.which("powershell"),
    reason="Windows PowerShell unavailable",
)
def test_launcher_has_valid_powershell_syntax() -> None:
    escaped = str(LAUNCHER).replace("'", "''")
    command = (
        "$tokens=$null; $errors=$null; "
        f"[System.Management.Automation.Language.Parser]::ParseFile('{escaped}', "
        "[ref]$tokens, [ref]$errors) | Out-Null; "
        "if ($errors.Count) { "
        "$errors | ForEach-Object { Write-Error $_.Message }; exit 1 }"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        check=True,
        capture_output=True,
        text=True,
    )
