param(
    [int]$BatchSize = 10,
    [int]$Workers = 2,
    [double]$Delay = 2.0,
    [double]$PollInterval = 60.0,
    [double]$RetryWait = 300.0,
    [string]$ActiveStart = "22:00",
    [string]$ActiveEnd = "07:00",
    [double]$ActiveCheckInterval = 1800.0,
    [switch]$IgnoreActiveWindow
)

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDir = Join-Path $projectRoot "logs"
$logFile = Join-Path $logDir "ai_worker.log"
$pidFile = Join-Path $logDir "ai_worker.pid"

if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}

if (Test-Path $pidFile) {
    $existingPid = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
    if ($existingPid) {
        $runningProcess = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
        if ($runningProcess) {
            Write-Host "AI worker is already running. PID=$existingPid"
            Write-Host "Log file: $logFile"
            exit 0
        }
        Remove-Item $pidFile -ErrorAction SilentlyContinue
    }
}

$arguments = @(
    "run_ai_pipeline.py"
    "--batch-size", $BatchSize
    "--workers", $Workers
    "--delay", $Delay
    "--poll-interval", $PollInterval
    "--retry-wait", $RetryWait
    "--active-start", $ActiveStart
    "--active-end", $ActiveEnd
    "--active-check-interval", $ActiveCheckInterval
)

if ($IgnoreActiveWindow) {
    $arguments += "--ignore-active-window"
}

$process = Start-Process `
    -FilePath "python" `
    -ArgumentList $arguments `
    -WorkingDirectory $projectRoot `
    -RedirectStandardOutput $logFile `
    -RedirectStandardError $logFile `
    -PassThru `
    -WindowStyle Hidden

Write-Host "AI worker started. PID=$($process.Id)"
Write-Host "Workers: $Workers"
Write-Host "Active window: $ActiveStart-$ActiveEnd"
Write-Host "Log file: $logFile"
