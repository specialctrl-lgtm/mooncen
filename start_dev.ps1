param(
    [int]$ApiPort = 8001,
    [int]$FrontendPort = 5174,
    [int]$StartupTimeoutSec = 60,
    [switch]$Restart,
    [switch]$Stop,
    [switch]$Status,
    [switch]$FrontendOnly,
    [switch]$Open
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendRoot = Join-Path $root "backend"
$frontendRoot = Join-Path $root "frontend2"
$logRoot = Join-Path $root "logs"
$statusFile = Join-Path $logRoot "dev_servers.status.txt"
$persistStatusOutput = -not $Status
$appendStatusOutput = $env:MOONCEN_DEV_APPEND_STATUS -eq "1"
if ($appendStatusOutput) {
    Remove-Item Env:\MOONCEN_DEV_APPEND_STATUS -ErrorAction SilentlyContinue
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:NO_COLOR = "1"
$env:FORCE_COLOR = "0"

New-Item -ItemType Directory -Force -Path $logRoot | Out-Null

if (-not $Status) {
    $mode = if ($Restart) { "restart" } elseif ($Stop) { "stop" } else { "start" }
    $statusLine = "MoonCen dev server ${mode}: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    if ($appendStatusOutput) {
        Add-Content -LiteralPath $statusFile -Value $statusLine -Encoding UTF8
    } else {
        Set-Content -LiteralPath $statusFile -Value $statusLine -Encoding UTF8
    }
}

function Write-Info {
    param([string]$Message)
    Write-Host $Message
    if ($persistStatusOutput -and (Test-Path -LiteralPath $logRoot)) {
        Add-Content -LiteralPath $statusFile -Value $Message -Encoding UTF8
    }
}

trap {
    try {
        Write-Info ""
        Write-Info "MoonCen dev server failed."
        Write-Info "Error: $($_.Exception.Message)"
        Write-Info "Status: .\start_dev.ps1 -Status"
        Write-Info "Restart: .\start_dev.ps1 -Restart"
        Write-Info "Frontend only: .\start_dev.ps1 -FrontendOnly"
        Write-Info "CMD wrapper: .\start_dev.cmd -Restart"
        Write-Info "Logs: $logRoot"
    } catch {
        [Console]::Error.WriteLine("MoonCen dev server failed: $($_.Exception.Message)")
    }
    exit 1
}

function Resolve-RequiredCommand {
    param(
        [string[]]$Names,
        [string]$InstallHint
    )

    foreach ($name in $Names) {
        $command = Get-Command $name -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($command) {
            if ($command.Source) {
                return $command.Source
            }
            return $command.Path
        }
    }

    throw "Required command not found: $($Names -join ', '). $InstallHint"
}

function Assert-PathExists {
    param(
        [string]$Path,
        [string]$Message
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        throw $Message
    }
}

function Quote-CmdArgument {
    param([string]$Value)

    '"' + ($Value -replace '"', '""') + '"'
}

function Import-DotEnvFile {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#") -or $trimmed -notmatch "^[A-Za-z_][A-Za-z0-9_]*=") {
            continue
        }

        $name, $rawValue = $trimmed -split "=", 2
        $value = $rawValue.Trim()
        if (
            ($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))
        ) {
            $value = $value.Substring(1, $value.Length - 2)
        }

        if ($value -or -not [Environment]::GetEnvironmentVariable($name, "Process")) {
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
}

function Set-EnvFromFallback {
    param(
        [string]$TargetName,
        [string[]]$SourceNames
    )

    if ([Environment]::GetEnvironmentVariable($TargetName, "Process")) {
        return
    }

    foreach ($sourceName in $SourceNames) {
        $value = [Environment]::GetEnvironmentVariable($sourceName, "Process")
        if ($value) {
            [Environment]::SetEnvironmentVariable($TargetName, $value, "Process")
            return
        }
    }
}

function Set-EnvIfMissing {
    param(
        [string]$Name,
        [string]$Value
    )

    if (-not $Value) {
        return
    }

    if ([Environment]::GetEnvironmentVariable($Name, "Process")) {
        return
    }

    [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
}

function Import-DeployLocalOAuthFallback {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    . $Path

    Set-EnvIfMissing -Name "KAKAO_MAPS_JAVASCRIPT_KEY" -Value $MoonCenKakaoMapsJavascriptKey
    Set-EnvIfMissing -Name "KAKAO_MAPS_REST_API_KEY" -Value $MoonCenKakaoMapsRestApiKey
    Set-EnvIfMissing -Name "GOOGLE_OAUTH_CLIENT_ID" -Value $MoonCenGoogleOAuthClientId
    Set-EnvIfMissing -Name "GOOGLE_OAUTH_CLIENT_SECRET" -Value $MoonCenGoogleOAuthClientSecret
    Set-EnvIfMissing -Name "NAVER_OAUTH_CLIENT_ID" -Value $MoonCenNaverOAuthClientId
    Set-EnvIfMissing -Name "NAVER_OAUTH_CLIENT_SECRET" -Value $MoonCenNaverOAuthClientSecret
}

function Start-LoggedProcess {
    param(
        [string]$Command,
        [string]$WorkingDirectory
    )

    Start-Process `
        -FilePath "cmd.exe" `
        -ArgumentList "/d /s /c `"$Command`"" `
        -WorkingDirectory $WorkingDirectory `
        -WindowStyle Hidden `
        -PassThru
}

function Resolve-PowerShellExecutable {
    $currentProcess = Get-Process -Id $PID -ErrorAction SilentlyContinue
    if ($currentProcess -and $currentProcess.Path -and (Test-Path -LiteralPath $currentProcess.Path)) {
        return $currentProcess.Path
    }

    foreach ($name in @("powershell.exe", "pwsh.exe", "powershell", "pwsh")) {
        $command = Get-Command $name -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($command) {
            if ($command.Source) {
                return $command.Source
            }
            if ($command.Path) {
                return $command.Path
            }
        }
    }

    throw "PowerShell executable not found. Install PowerShell or add it to PATH."
}

function Get-LogTail {
    param(
        [string]$Path,
        [int]$Lines = 40
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return @()
    }

    $item = Get-Item -LiteralPath $Path -ErrorAction SilentlyContinue
    if (-not $item -or $item.Length -eq 0) {
        return @()
    }

    return Get-Content -LiteralPath $Path -Tail $Lines -ErrorAction SilentlyContinue
}

function Get-Listener {
    param([int]$Port)

    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($listener) {
        return $listener
    }

    $line = netstat -ano | Select-String ":$Port\s+.*LISTENING" | Select-Object -First 1
    if (-not $line) {
        return $null
    }

    $parts = ($line.Line -replace '^\s+', '') -split '\s+'
    [pscustomobject]@{
        LocalPort = $Port
        OwningProcess = [int]$parts[-1]
    }
}

function Get-ProcessChildren {
    param([int]$ProcessId)

    Get-CimInstance Win32_Process -Filter "ParentProcessId=$ProcessId" -ErrorAction SilentlyContinue
}

function Stop-ProcessTree {
    param([int]$ProcessId)

    if ($ProcessId -le 0) {
        return
    }

    foreach ($child in Get-ProcessChildren -ProcessId $ProcessId) {
        Stop-ProcessTree -ProcessId ([int]$child.ProcessId)
    }

    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($process) {
        Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Stop-MatchingDevProcesses {
    param([ValidateSet("api", "frontend")] [string]$Kind)

    $processes = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $commandLine = $_.CommandLine
        if (-not $commandLine) {
            return $false
        }

        if ($Kind -eq "api") {
            return $commandLine -match "uvicorn" -and $commandLine -match "backend\.main:app"
        }

        return ($commandLine -match [regex]::Escape($frontendRoot)) -and (
            $commandLine -match "vite" -or
            $commandLine -match 'npm(\.cmd)?"?\s+run\s+dev' -or
            $commandLine -match "npm-cli\.js"
        )
    }

    foreach ($process in $processes) {
        Stop-ProcessTree -ProcessId ([int]$process.ProcessId)
    }
}

function Wait-PortClosed {
    param(
        [int]$Port,
        [string]$Name
    )

    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if (-not (Get-Listener -Port $Port)) {
            return
        }
        Start-Sleep -Milliseconds 300
    }

    $listener = Get-Listener -Port $Port
    if ($listener) {
        throw "$Name did not stop cleanly. Port $Port is still listening (PID $($listener.OwningProcess))."
    }
}

function Stop-Listener {
    param(
        [int]$Port,
        [string]$Name,
        [ValidateSet("api", "frontend")] [string]$Kind
    )

    $listener = Get-Listener -Port $Port
    if (-not $listener) {
        Stop-MatchingDevProcesses -Kind $Kind
        Write-Info "$Name is not running on port $Port."
        return
    }

    Stop-ProcessTree -ProcessId ([int]$listener.OwningProcess)
    Stop-MatchingDevProcesses -Kind $Kind
    Wait-PortClosed -Port $Port -Name $Name
    Write-Info "Stopped $Name on port $Port (PID $($listener.OwningProcess))."
}

function Test-Http {
    param(
        [string]$Url,
        [int]$ExpectedStatus = 200
    )

    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
        return [int]$response.StatusCode -eq $ExpectedStatus
    } catch {
        return $false
    }
}

function Wait-ForHttp {
    param(
        [string]$Name,
        [string]$Url,
        [int]$Port,
        [int]$TimeoutSec = 60,
        [System.Diagnostics.Process]$Process = $null,
        [string]$OutLog = "",
        [string]$ErrLog = ""
    )

    $attempts = [Math]::Max(1, [int][Math]::Ceiling($TimeoutSec * 2))
    for ($attempt = 0; $attempt -lt $attempts; $attempt++) {
        Start-Sleep -Milliseconds 500
        if ((Get-Listener -Port $Port) -and (Test-Http -Url $Url)) {
            return
        }
    }

    $details = [System.Collections.Generic.List[string]]::new()
    $details.Add("$Name failed to start within ${TimeoutSec}s. Expected $Url to respond.")

    $listener = Get-Listener -Port $Port
    if ($listener) {
        $details.Add("Port $Port is listening (PID $($listener.OwningProcess)), but HTTP did not become healthy.")
    } else {
        $details.Add("Port $Port is not listening.")
    }

    if ($Process) {
        $Process.Refresh()
        if ($Process.HasExited) {
            $details.Add("Starter process exited with code $($Process.ExitCode).")
        } else {
            $details.Add("Starter process is still running (PID $($Process.Id)).")
        }
    }

    foreach ($logPath in @($ErrLog, $OutLog)) {
        if (-not $logPath) {
            continue
        }
        $tail = Get-LogTail -Path $logPath
        if ($tail.Count -gt 0) {
            $details.Add("")
            $details.Add("Last $($tail.Count) lines from ${logPath}:")
            foreach ($line in $tail) {
                $details.Add($line)
            }
        }
    }

    throw ($details -join [Environment]::NewLine)
}

function Write-ServiceStatus {
    param(
        [string]$Name,
        [int]$Port,
        [string]$Url
    )

    $listener = Get-Listener -Port $Port
    if ($listener) {
        $health = if (Test-Http -Url $Url) { "healthy" } else { "listening" }
        Write-Info "${Name}: $health at $Url (PID $($listener.OwningProcess))"
    } else {
        Write-Info "${Name}: stopped on port $Port"
    }
}

function Write-FrontendPortHint {
    if ($FrontendPort -ne 5173) {
        $legacyListener = Get-Listener -Port 5173
        if (-not $legacyListener) {
            Write-Info "Note: local dev frontend URL is http://127.0.0.1:$FrontendPort, not http://127.0.0.1:5173."
        }
    }
}

if ($Stop -or $Restart) {
    Stop-Listener -Port $FrontendPort -Name "MoonCen frontend" -Kind "frontend"
    if (-not $FrontendOnly) {
        Stop-Listener -Port $ApiPort -Name "MoonCen API" -Kind "api"
    }

    if ($Stop -and -not $Restart) {
        exit 0
    }

    if ($Restart) {
        Write-Info "Starting MoonCen dev servers in a fresh PowerShell process..."
        $restartArgs = @(
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            $PSCommandPath,
            "-ApiPort",
            "$ApiPort",
            "-FrontendPort",
            "$FrontendPort",
            "-StartupTimeoutSec",
            "$StartupTimeoutSec"
        )
        if ($FrontendOnly) {
            $restartArgs += "-FrontendOnly"
        }
        if ($Open) {
            $restartArgs += "-Open"
        }

        $env:MOONCEN_DEV_APPEND_STATUS = "1"
        $powerShellExe = Resolve-PowerShellExecutable
        & $powerShellExe @restartArgs
        $restartExitCode = $LASTEXITCODE
        Remove-Item Env:\MOONCEN_DEV_APPEND_STATUS -ErrorAction SilentlyContinue
        exit $restartExitCode
    }
}

if ($Status) {
    Write-ServiceStatus -Name "MoonCen API" -Port $ApiPort -Url "http://127.0.0.1:$ApiPort/health"
    Write-ServiceStatus -Name "MoonCen frontend" -Port $FrontendPort -Url "http://127.0.0.1:$FrontendPort"
    Write-FrontendPortHint
    exit 0
}

Write-Info "Starting MoonCen dev servers..."
Write-Info "Project:  $root"
Write-Info "Frontend: $frontendRoot"
Write-Info "Ports:    frontend $FrontendPort, API $ApiPort"

Import-DotEnvFile -Path (Join-Path $root ".env")
Import-DotEnvFile -Path (Join-Path $frontendRoot ".env")
Import-DeployLocalOAuthFallback -Path (Join-Path $root "deploy.local.ps1")
Set-EnvFromFallback -TargetName "VITE_KAKAO_MAPS_JAVASCRIPT_KEY" -SourceNames @("KAKAO_MAPS_JAVASCRIPT_KEY")
Set-EnvFromFallback -TargetName "VITE_GOOGLE_OAUTH_CLIENT_ID" -SourceNames @("GOOGLE_OAUTH_CLIENT_ID")
Set-EnvFromFallback -TargetName "VITE_NAVER_OAUTH_CLIENT_ID" -SourceNames @("NAVER_OAUTH_CLIENT_ID")

Assert-PathExists -Path $frontendRoot -Message "frontend2 directory not found: $frontendRoot"
Assert-PathExists -Path (Join-Path $frontendRoot "package.json") -Message "frontend2 package.json not found: $frontendRoot"
if (-not $FrontendOnly) {
    Assert-PathExists -Path (Join-Path $backendRoot "main.py") -Message "backend main module not found: $(Join-Path $backendRoot "main.py")"
}

$npmExe = Resolve-RequiredCommand -Names @("npm.cmd", "npm") -InstallHint "Install Node.js/npm or add it to PATH."
if (-not $FrontendOnly) {
    $pythonExe = Resolve-RequiredCommand -Names @("python") -InstallHint "Install Python or add it to PATH."
}

if (-not (Test-Path -LiteralPath (Join-Path $frontendRoot "node_modules"))) {
    throw "frontend2 dependencies are not installed. Run: cd frontend2; npm install"
}

if (-not $FrontendOnly) {
    & $pythonExe -c "import uvicorn" *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Python package 'uvicorn' is not installed for $pythonExe. Run: python -m pip install -r requirements.txt"
    }
}

if (-not $FrontendOnly) {
    $apiListener = Get-Listener -Port $ApiPort
    if ($apiListener) {
        if (-not (Test-Http -Url "http://127.0.0.1:$ApiPort/health")) {
            throw "MoonCen API port $ApiPort is listening but /health is not responding. Run .\start_dev.ps1 -Restart or free the port manually."
        }
        Write-Info "MoonCen API is already running: http://127.0.0.1:$ApiPort (PID $($apiListener.OwningProcess))"
    } else {
        $apiOut = Join-Path $logRoot "dev_api_$ApiPort.out.log"
        $apiErr = Join-Path $logRoot "dev_api_$ApiPort.err.log"
        Set-Content -LiteralPath $apiOut -Value "" -Encoding UTF8
        Set-Content -LiteralPath $apiErr -Value "" -Encoding UTF8

        Write-Info "Starting MoonCen API on port $ApiPort..."
        $apiCommand = @(
            (Quote-CmdArgument $pythonExe),
            "-m",
            "uvicorn",
            "backend.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "$ApiPort",
            "--reload",
            "--reload-dir",
            (Quote-CmdArgument $backendRoot),
            ">",
            (Quote-CmdArgument $apiOut),
            "2>",
            (Quote-CmdArgument $apiErr)
        ) -join " "
        $apiProcess = Start-LoggedProcess -Command $apiCommand -WorkingDirectory $root

        Wait-ForHttp -Name "MoonCen API" -Url "http://127.0.0.1:$ApiPort/health" -Port $ApiPort -TimeoutSec $StartupTimeoutSec -Process $apiProcess -OutLog $apiOut -ErrLog $apiErr
        $apiListener = Get-Listener -Port $ApiPort
        $apiPid = if ($apiListener) { $apiListener.OwningProcess } else { $apiProcess.Id }
        Write-Info "MoonCen API started: http://127.0.0.1:$ApiPort (PID $apiPid)"
    }
} else {
    if (Test-Http -Url "http://127.0.0.1:$ApiPort/health") {
        Write-Info "Skipping API start; existing API is healthy at http://127.0.0.1:$ApiPort"
    } else {
        Write-Info "Skipping API start because -FrontendOnly was specified."
        Write-Info "Frontend API proxy target remains http://127.0.0.1:$ApiPort"
    }
}

$frontendListener = Get-Listener -Port $FrontendPort
if ($frontendListener) {
    if (-not (Test-Http -Url "http://127.0.0.1:$FrontendPort")) {
        throw "MoonCen frontend port $FrontendPort is listening but HTTP is not responding. Run .\start_dev.ps1 -Restart or free the port manually."
    }
    Write-Info "MoonCen frontend is already running: http://127.0.0.1:$FrontendPort (PID $($frontendListener.OwningProcess))"
} else {
    $frontendOut = Join-Path $logRoot "dev_frontend_$FrontendPort.out.log"
    $frontendErr = Join-Path $logRoot "dev_frontend_$FrontendPort.err.log"
    $frontendArgs = @("run", "dev", "--", "--port", "$FrontendPort", "--strictPort")
    Set-Content -LiteralPath $frontendOut -Value "" -Encoding UTF8
    Set-Content -LiteralPath $frontendErr -Value "" -Encoding UTF8

    $env:VITE_API_PROXY_TARGET = "http://127.0.0.1:$ApiPort"
    $env:VITE_DEV_PORT = "$FrontendPort"
    if (-not $env:VITE_OAUTH_REDIRECT_URI) {
        $env:VITE_OAUTH_REDIRECT_URI = "http://localhost:$FrontendPort/"
    }

    Write-Info "Starting MoonCen frontend on port $FrontendPort..."
    $frontendCommandParts = @((Quote-CmdArgument $npmExe)) + $frontendArgs + @(
        ">",
        (Quote-CmdArgument $frontendOut),
        "2>",
        (Quote-CmdArgument $frontendErr)
    )
    $frontendCommand = $frontendCommandParts -join " "
    $frontendProcess = Start-LoggedProcess -Command $frontendCommand -WorkingDirectory $frontendRoot

    Wait-ForHttp -Name "MoonCen frontend" -Url "http://127.0.0.1:$FrontendPort" -Port $FrontendPort -TimeoutSec $StartupTimeoutSec -Process $frontendProcess -OutLog $frontendOut -ErrLog $frontendErr
    $frontendListener = Get-Listener -Port $FrontendPort
    $frontendPid = if ($frontendListener) { $frontendListener.OwningProcess } else { $frontendProcess.Id }
    Write-Info "MoonCen frontend started: http://127.0.0.1:$FrontendPort (PID $frontendPid)"
}

Write-Info ""
Write-Info "MoonCen dev servers are ready."
Write-Info "Frontend: http://127.0.0.1:$FrontendPort"
Write-Info "API:      http://127.0.0.1:$ApiPort"
Write-Info "Logs:     $logRoot"
Write-FrontendPortHint

if ($Open) {
    Start-Process "http://127.0.0.1:$FrontendPort" | Out-Null
}
