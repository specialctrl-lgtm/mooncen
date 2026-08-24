param(
    [string[]]$Providers = @("HOMEPLUS", "EMART", "LOTTE", "EXPERIENCE_TARGETS", "MUNICIPAL_RESERVATION_TARGETS"),
    [int]$Limit = 0,
    [double]$RunInterval = 86400.0,
    [string]$ActiveStart = "22:00",
    [string]$ActiveEnd = "07:00",
    [double]$ActiveCheckInterval = 1800.0,
    [switch]$Parallel,
    [int]$MaxWorkers = 2,
    [switch]$Once,
    [switch]$IgnoreActiveWindow,
    [string]$PythonPath = "",
    [ValidateRange(1, 300)]
    [int]$StartupTimeoutSeconds = 30
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDir = Join-Path $projectRoot "logs"
$stdoutLogFile = Join-Path $logDir "crawler_worker.log"
$stderrLogFile = Join-Path $logDir "crawler_worker.error.log"
$pidFile = Join-Path $logDir "crawler_worker.pid"
$crawlerScript = Join-Path $projectRoot "run_crawlers.py"

function Resolve-CrawlerPython {
    param([string]$RequestedPath)

    if ($RequestedPath) {
        $expandedPath = [Environment]::ExpandEnvironmentVariables($RequestedPath)
        if (-not [IO.Path]::IsPathRooted($expandedPath)) {
            $expandedPath = Join-Path $projectRoot $expandedPath
        }
        $candidatePaths = @($expandedPath)
    }
    else {
        $candidatePaths = @(
            (Join-Path $projectRoot "venv_clean\Scripts\python.exe"),
            (Join-Path $projectRoot "venv\Scripts\python.exe"),
            (Join-Path $projectRoot ".venv\Scripts\python.exe")
        )
    }

    foreach ($candidatePath in $candidatePaths) {
        if (-not (Test-Path -LiteralPath $candidatePath -PathType Leaf)) {
            continue
        }

        $resolvedPath = (Resolve-Path -LiteralPath $candidatePath).Path
        try {
            $versionOutput = @(
                & $resolvedPath -I -X utf8 -c `
                    "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            )
            $probeExitCode = $LASTEXITCODE
        }
        catch {
            continue
        }

        $version = [string]($versionOutput | Select-Object -Last 1)
        if ($probeExitCode -eq 0 -and $version.Trim() -match '^3\.(12|13)$') {
            return $resolvedPath
        }
    }

    $checked = $candidatePaths -join ", "
    throw "A runnable Python 3.12 or 3.13 interpreter was not found. Checked: $checked"
}

function Read-CrawlerWorkerPid {
    if (-not (Test-Path -LiteralPath $pidFile -PathType Leaf)) {
        return $null
    }

    $rawPid = [string](Get-Content -LiteralPath $pidFile -Raw -ErrorAction SilentlyContinue)
    $parsedPid = 0
    if ([int]::TryParse($rawPid.Trim(), [ref]$parsedPid) -and $parsedPid -gt 0) {
        return $parsedPid
    }
    return $null
}

function Stop-UnreadyCrawlerProcess {
    param([System.Diagnostics.Process]$Process)

    if ($null -eq $Process) {
        return
    }
    try {
        $Process.Refresh()
        if (-not $Process.HasExited) {
            Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
            $Process.WaitForExit(5000) | Out-Null
        }
    }
    catch {
        # Preserve the original startup error. The worker lock prevents a
        # second crawler from continuing if cleanup races with process exit.
    }
}

if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}

if (-not (Test-Path -LiteralPath $crawlerScript -PathType Leaf)) {
    throw "Crawler entrypoint is missing: $crawlerScript"
}

$existingPid = Read-CrawlerWorkerPid
if ($null -ne $existingPid) {
    $runningProcess = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
    if ($runningProcess) {
        Write-Host "Crawler worker is already running. PID=$existingPid"
        Write-Host "Log file: $stdoutLogFile"
        Write-Host "Error log: $stderrLogFile"
        exit 0
    }
}
if (Test-Path -LiteralPath $pidFile -PathType Leaf) {
    Remove-Item -LiteralPath $pidFile -Force
}

$pythonExe = Resolve-CrawlerPython -RequestedPath $PythonPath

$arguments = @(
    "-X", "utf8"
    "run_crawlers.py"
    "--providers"
)
$arguments += $Providers
$arguments += @(
    "--run-interval", $RunInterval.ToString([Globalization.CultureInfo]::InvariantCulture)
    "--active-start", $ActiveStart
    "--active-end", $ActiveEnd
    "--active-check-interval", $ActiveCheckInterval.ToString([Globalization.CultureInfo]::InvariantCulture)
)

if ($Limit -gt 0) {
    $arguments += @("--limit", $Limit.ToString([Globalization.CultureInfo]::InvariantCulture))
}

if ($Once) {
    $arguments += "--once"
}

if ($Parallel) {
    $arguments += @("--parallel", "--max-workers", $MaxWorkers.ToString([Globalization.CultureInfo]::InvariantCulture))
}

if ($IgnoreActiveWindow) {
    $arguments += "--ignore-active-window"
}

$process = $null
try {
    $process = Start-Process `
        -FilePath $pythonExe `
        -ArgumentList $arguments `
        -WorkingDirectory $projectRoot `
        -RedirectStandardOutput $stdoutLogFile `
        -RedirectStandardError $stderrLogFile `
        -PassThru `
        -WindowStyle Hidden

    $deadline = [DateTime]::UtcNow.AddSeconds($StartupTimeoutSeconds)
    $workerReady = $false
    while ([DateTime]::UtcNow -lt $deadline) {
        $process.Refresh()
        if ($process.HasExited) {
            throw (
                "Crawler worker exited before publishing its PID. " +
                "ExitCode=$($process.ExitCode). Review the error log: $stderrLogFile"
            )
        }

        $publishedPid = Read-CrawlerWorkerPid
        if ($publishedPid -eq $process.Id) {
            $workerReady = $true
            break
        }
        if ($null -ne $publishedPid) {
            $publishedProcess = Get-Process -Id $publishedPid -ErrorAction SilentlyContinue
            if ($publishedProcess) {
                throw (
                    "Crawler PID file points to another live process. " +
                    "expected=$($process.Id) published=$publishedPid"
                )
            }
        }
        Start-Sleep -Milliseconds 200
    }

    if (-not $workerReady) {
        throw (
            "Crawler worker did not publish a matching PID within " +
            "$StartupTimeoutSeconds seconds. Review the error log: $stderrLogFile"
        )
    }
}
catch {
    Stop-UnreadyCrawlerProcess -Process $process
    throw
}

Write-Host "Crawler worker started. PID=$($process.Id)"
Write-Host "Python: $pythonExe"
Write-Host "Providers: $($Providers -join ', ')"
Write-Host "Parallel: $($Parallel.IsPresent), MaxWorkers: $MaxWorkers"
Write-Host "Active window: $ActiveStart-$ActiveEnd"
Write-Host "Log file: $stdoutLogFile"
Write-Host "Error log: $stderrLogFile"
