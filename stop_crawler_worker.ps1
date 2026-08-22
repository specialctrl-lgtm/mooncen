param()

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDir = Join-Path $projectRoot "logs"
$pidFile = Join-Path $logDir "crawler_worker.pid"

if (-not (Test-Path $pidFile)) {
    Write-Host "Crawler worker is not running."
    exit 0
}

$pidValue = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()

if (-not $pidValue) {
    Remove-Item $pidFile -ErrorAction SilentlyContinue
    Write-Host "PID file was empty and has been removed."
    exit 0
}

$process = Get-Process -Id $pidValue -ErrorAction SilentlyContinue

if (-not $process) {
    Remove-Item $pidFile -ErrorAction SilentlyContinue
    Write-Host "Stale PID file removed. No running crawler worker found."
    exit 0
}

Get-CimInstance Win32_Process -Filter "ParentProcessId = $pidValue" -ErrorAction SilentlyContinue |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -ErrorAction SilentlyContinue
    }

Stop-Process -Id $pidValue -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1

if (Get-Process -Id $pidValue -ErrorAction SilentlyContinue) {
    Write-Host "Failed to stop crawler worker. PID=$pidValue"
    exit 1
}

Remove-Item $pidFile -ErrorAction SilentlyContinue
Write-Host "Crawler worker stopped. PID=$pidValue"
