[CmdletBinding()]
param(
    [ValidateSet("Monitor", "Ensure", "Preflight", "Status", "Stop")]
    [string]$Action = "Monitor",
    [ValidateSet("Cloud", "Local")]
    [string]$DataSource = "Cloud",
    [ValidatePattern('^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+$')]
    [string]$SshTarget = "ubuntu@cloud",
    [string]$SshIdentityFile = "",
    [ValidatePattern('^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+$')]
    [string]$CrawlerControlSshTarget = "sgm@gen1db",
    [string]$CrawlerControlSshIdentityFile = "",
    [ValidateRange(10, 300)]
    [int]$CheckIntervalSec = 30,
    [ValidateRange(30, 900)]
    [int]$MaxRetrySec = 300
)

$ErrorActionPreference = "Stop"
$root = [IO.Path]::GetFullPath($PSScriptRoot)
$opsLauncher = Join-Path $root "start_ops_console.ps1"
$devLauncher = Join-Path $root "start_dev.ps1"
$opsStatePath = Join-Path $root "logs\ops-console-local\processes.json"
$frontendRoot = Join-Path $root "frontend2"
$stateDir = Join-Path $root "logs\development-autostart"
$statePath = Join-Path $stateDir "state.json"
$lockPath = Join-Path $stateDir "supervisor.lock"
$eventLogPath = Join-Path $stateDir "supervisor.log"
$powerShellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$crawlerControlAnalyticsEnabled = [bool]$CrawlerControlSshIdentityFile
$opsPorts = @(5175, 8001, 18001, 15432)
if ($DataSource -eq "Cloud" -and $crawlerControlAnalyticsEnabled) {
    $opsPorts += 15433
}
$frontendPort = 5174
$apiPort = 8001
$script:SupervisorInstanceId = [guid]::NewGuid().ToString("D")
$script:SupervisorStartedAt = (Get-Date).ToUniversalTime()
$script:SshIdentityPath = ""
$script:CrawlerControlSshIdentityPath = ""
$script:SupervisorLock = $null
$utf8NoBom = New-Object Text.UTF8Encoding($false)

if ($DataSource -ne "Cloud" -and ($SshIdentityFile -or $CrawlerControlSshIdentityFile)) {
    throw "SSH identity files are allowed only with -DataSource Cloud."
}

function Protect-LogText([string]$Text) {
    if ($null -eq $Text) {
        return ""
    }
    $safe = $Text -replace '(?i)(password|secret|token|api[_-]?key)\s*[=:]\s*\S+', '$1=[redacted]'
    if ($safe.Length -gt 2000) {
        return $safe.Substring(0, 2000) + "..."
    }
    return $safe
}

function Rotate-AutostartLog {
    if (-not (Test-Path -LiteralPath $eventLogPath -PathType Leaf)) {
        return
    }
    $item = Get-Item -LiteralPath $eventLogPath -ErrorAction SilentlyContinue
    if ($null -eq $item -or $item.Length -lt 5242880) {
        return
    }
    for ($index = 3; $index -ge 1; $index--) {
        $source = if ($index -eq 1) { $eventLogPath } else { "$eventLogPath.$($index - 1)" }
        $destination = "$eventLogPath.$index"
        if (Test-Path -LiteralPath $source -PathType Leaf) {
            Move-Item -LiteralPath $source -Destination $destination -Force
        }
    }
}

function Write-AutostartEvent(
    [ValidateSet("INFO", "WARN", "ERROR")][string]$Level,
    [string]$Message
) {
    New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
    Rotate-AutostartLog
    $safe = Protect-LogText $Message
    $line = "{0} [{1}] [{2}] {3}" -f (
        (Get-Date).ToUniversalTime().ToString("o"),
        $Level,
        $script:SupervisorInstanceId,
        $safe
    )
    [IO.File]::AppendAllText($eventLogPath, $line + [Environment]::NewLine, $utf8NoBom)
    Write-Host $line
}

function Write-AtomicJson([string]$Path, [object]$Value) {
    $temporary = "$Path.$PID.$([guid]::NewGuid().ToString('N')).tmp"
    $backup = "$Path.$PID.$([guid]::NewGuid().ToString('N')).bak"
    $json = $Value | ConvertTo-Json -Depth 8
    try {
        [IO.File]::WriteAllText($temporary, $json, $utf8NoBom)
        if (Test-Path -LiteralPath $Path -PathType Leaf) {
            # Windows PowerShell 5.1/.NET Framework rejects a null backup path.
            # A unique sibling backup preserves File.Replace atomicity without
            # leaving supervisor state copies behind after a successful swap.
            [IO.File]::Replace($temporary, $Path, $backup, $true)
        }
        else {
            [IO.File]::Move($temporary, $Path)
        }
    }
    finally {
        foreach ($residue in @($temporary, $backup)) {
            if (Test-Path -LiteralPath $residue -PathType Leaf) {
                [IO.File]::Delete($residue)
            }
        }
    }
}

function Set-SupervisorState(
    [string]$Status,
    [string]$LastError = "",
    [datetime]$NextRetryAt = [datetime]::MinValue
) {
    $process = Get-Process -Id $PID -ErrorAction SilentlyContinue
    $frontendListener = Get-Listener $frontendPort
    $frontendProcess = $null
    if (
        $null -ne $frontendListener -and
        (Test-FrontendProcess ([int]$frontendListener.OwningProcess))
    ) {
        $frontendProcess = Get-Process -Id ([int]$frontendListener.OwningProcess) -ErrorAction SilentlyContinue
    }
    $payload = [ordered]@{
        schema_version = 1
        instance_id = $script:SupervisorInstanceId
        pid = $PID
        process_started_at = if ($process) { $process.StartTime.ToUniversalTime().ToString("o") } else { $null }
        workspace = $root
        data_source = $DataSource
        ssh_target = if ($DataSource -eq "Cloud") { $SshTarget } else { $null }
        crawler_control_analytics_enabled = $crawlerControlAnalyticsEnabled
        crawler_control_ssh_target = if ($crawlerControlAnalyticsEnabled) {
            $CrawlerControlSshTarget
        }
        else {
            $null
        }
        status = $Status
        heartbeat_at = (Get-Date).ToUniversalTime().ToString("o")
        next_retry_at = if ($NextRetryAt -ne [datetime]::MinValue) { $NextRetryAt.ToUniversalTime().ToString("o") } else { $null }
        last_error = Protect-LogText $LastError
        frontend_pid = if ($frontendProcess) { $frontendProcess.Id } else { $null }
        frontend_process_started_at = if ($frontendProcess) { $frontendProcess.StartTime.ToUniversalTime().ToString("o") } else { $null }
    }
    Write-AtomicJson -Path $statePath -Value $payload
}

function Enter-SupervisorLock([bool]$AllowExisting) {
    New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
    try {
        $script:SupervisorLock = [IO.File]::Open(
            $lockPath,
            [IO.FileMode]::OpenOrCreate,
            [IO.FileAccess]::ReadWrite,
            [IO.FileShare]::None
        )
        $script:SupervisorLock.SetLength(0)
        $bytes = $utf8NoBom.GetBytes("$PID`n$($script:SupervisorInstanceId)`n")
        $script:SupervisorLock.Write($bytes, 0, $bytes.Length)
        $script:SupervisorLock.Flush($true)
        return $true
    }
    catch [IO.IOException] {
        if ($AllowExisting) {
            return $false
        }
        throw "Another MoonCen development supervisor owns the lock: $lockPath"
    }
}

function Exit-SupervisorLock {
    if ($null -ne $script:SupervisorLock) {
        $script:SupervisorLock.Dispose()
        $script:SupervisorLock = $null
    }
}

function Get-Listener([int]$Port) {
    $listener = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($listener) {
        return $listener
    }
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
    return [pscustomobject]@{ LocalPort = $Port; OwningProcess = [int]$parts[-1] }
}

function Test-Http([string]$Url) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3
        return [int]$response.StatusCode -ge 200 -and [int]$response.StatusCode -lt 400
    }
    catch {
        return $false
    }
}

function Test-ListeningHttp([int]$Port, [string]$Url) {
    if ($null -eq (Get-Listener $Port)) {
        return $false
    }
    return Test-Http $Url
}

function Get-JsonFile([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

function Test-ProcessStartTime([int]$ProcessId, [object]$Entry) {
    if ($null -eq $Entry.PSObject.Properties["process_started_at"] -or -not [string]$Entry.process_started_at) {
        return $false
    }
    try {
        $expected = [datetime]::Parse(
            [string]$Entry.process_started_at,
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::RoundtripKind
        ).ToUniversalTime()
        $runtime = Get-Process -Id $ProcessId -ErrorAction Stop
        $actual = $null
        try {
            $actual = $runtime.StartTime.ToUniversalTime()
        }
        catch {
            $cim = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop
            $actual = ([datetime]$cim.CreationDate).ToUniversalTime()
        }
        return [Math]::Abs(($actual - $expected).TotalSeconds) -le 2
    }
    catch {
        return $false
    }
}

function Test-OpsProcessEntry([object]$Entry) {
    if ($null -eq $Entry -or $null -eq $Entry.PSObject.Properties["pid"] -or $null -eq $Entry.PSObject.Properties["name"]) {
        return $false
    }
    $processId = [int]$Entry.pid
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
    if ($null -eq $process -or -not (Test-ProcessStartTime -ProcessId $processId -Entry $Entry)) {
        return $false
    }
    $expectedProcessNames = @{
        "ssh-tunnel" = @("ssh.exe", "ssh")
        "crawler-control-ssh-tunnel" = @("ssh.exe", "ssh")
        "api" = @("python.exe", "python")
        "console" = @("node.exe", "node")
        "status-agent" = @("python.exe", "python")
        "crawler-scheduler" = @("python.exe", "python")
        "crawler-worker" = @("python.exe", "python")
        "quality-worker" = @("python.exe", "python")
    }
    $entryName = [string]$Entry.name
    if (
        -not $expectedProcessNames.ContainsKey($entryName) -or
        [string]$process.Name -notin $expectedProcessNames[$entryName]
    ) {
        return $false
    }
    $commandLine = [string]$process.CommandLine
    if ($entryName -in @("ssh-tunnel", "crawler-control-ssh-tunnel")) {
        # A process in Task Scheduler session 0 may intentionally hide its
        # command line from a status command in the interactive session. PID,
        # exact start time and image identity remain available; port ancestry
        # is checked separately before any repair or stop operation.
        if (-not $commandLine) {
            return $true
        }
        $tokens = if ($entryName -eq "ssh-tunnel") {
            @($SshTarget, "127.0.0.1:18001:127.0.0.1:8001", "127.0.0.1:15432:127.0.0.1:5432")
        }
        else {
            @($CrawlerControlSshTarget, "127.0.0.1:15433:127.0.0.1:5432")
        }
        foreach ($token in $tokens) {
            if ($commandLine.IndexOf($token, [StringComparison]::Ordinal) -lt 0) {
                return $false
            }
        }
        return $true
    }
    if (-not $commandLine) {
        return $true
    }
    return $commandLine.IndexOf($root, [StringComparison]::OrdinalIgnoreCase) -ge 0
}

function Get-OpsEntry([object]$State, [string]$Name) {
    if ($null -eq $State -or $null -eq $State.PSObject.Properties["processes"]) {
        return $null
    }
    return @($State.processes | Where-Object { [string]$_.name -eq $Name } | Select-Object -First 1) | Select-Object -First 1
}

function Test-ProcessDescendsFrom([int]$ProcessId, [int]$ExpectedAncestorId) {
    $current = $ProcessId
    for ($depth = 0; $depth -lt 32 -and $current -gt 0; $depth++) {
        if ($current -eq $ExpectedAncestorId) {
            return $true
        }
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $current" -ErrorAction SilentlyContinue
        if ($null -eq $process) {
            return $false
        }
        $current = [int]$process.ParentProcessId
    }
    return $false
}

function Test-FrontendProcess([int]$ProcessId) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        return $false
    }
    $commandLine = [string]$process.CommandLine
    if ($commandLine) {
        return $commandLine.IndexOf($frontendRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and (
            $commandLine -match '(?i)vite' -or $commandLine -match '(?i)npm(-cli\.js|\.cmd)?'
        )
    }
    if ([string]$process.Name -notin @("node.exe", "node")) {
        return $false
    }
    $state = Get-JsonFile $statePath
    if (
        $null -eq $state -or
        $null -eq $state.PSObject.Properties["frontend_pid"] -or
        [int]$state.frontend_pid -ne $ProcessId -or
        $null -eq $state.PSObject.Properties["frontend_process_started_at"]
    ) {
        return $false
    }
    try {
        $expected = [datetime]::Parse(
            [string]$state.frontend_process_started_at,
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::RoundtripKind
        ).ToUniversalTime()
        $runtime = Get-Process -Id $ProcessId -ErrorAction Stop
        try {
            $actual = $runtime.StartTime.ToUniversalTime()
        }
        catch {
            $cim = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop
            $actual = ([datetime]$cim.CreationDate).ToUniversalTime()
        }
        return [Math]::Abs(($actual - $expected).TotalSeconds) -le 2
    }
    catch {
        return $false
    }
}

function Get-OpsHealth {
    $reasons = [Collections.Generic.List[string]]::new()
    $state = Get-JsonFile $opsStatePath
    if ($null -eq $state) {
        $reasons.Add("Ops process state is missing or invalid.")
    }
    elseif ([string]$state.data_source -ne $DataSource) {
        $reasons.Add("Ops data source is '$($state.data_source)', expected '$DataSource'.")
    }
    elseif ($DataSource -eq "Cloud" -and [string]$state.ssh_target -ne $SshTarget) {
        $reasons.Add("Ops SSH target is '$($state.ssh_target)', expected '$SshTarget'.")
    }
    elseif (
        $DataSource -eq "Cloud" -and
        [bool]$state.crawler_control_analytics_enabled -ne $crawlerControlAnalyticsEnabled
    ) {
        $reasons.Add("Ops crawler-control analytics configuration does not match the supervisor.")
    }
    elseif (
        $crawlerControlAnalyticsEnabled -and
        [string]$state.crawler_control_ssh_target -ne $CrawlerControlSshTarget
    ) {
        $reasons.Add("Ops crawler-control SSH target does not match the supervisor.")
    }

    $requiredProcesses = if ($DataSource -eq "Cloud") {
        $names = @("ssh-tunnel", "api", "console")
        if ($crawlerControlAnalyticsEnabled) {
            $names += "crawler-control-ssh-tunnel"
        }
        $names
    }
    else {
        @("api", "console", "status-agent")
    }
    foreach ($name in $requiredProcesses) {
        $entry = Get-OpsEntry -State $state -Name $name
        if ($null -eq $entry -or -not (Test-OpsProcessEntry $entry)) {
            $reasons.Add("Ops process '$name' is not owned and running.")
        }
    }

    if (-not (Test-ListeningHttp 8001 "http://127.0.0.1:8001/health")) {
        $reasons.Add("Ops API health check failed on port 8001.")
    }
    if (-not (Test-ListeningHttp 5175 "http://127.0.0.1:5175/")) {
        $reasons.Add("Ops Console health check failed on port 5175.")
    }
    if ($DataSource -eq "Cloud") {
        if (-not (Test-ListeningHttp 18001 "http://127.0.0.1:18001/health")) {
            $reasons.Add("Cloud API tunnel health check failed on port 18001.")
        }
        if ($null -eq (Get-Listener 15432)) {
            $reasons.Add("Cloud database tunnel is not listening on port 15432.")
        }
        if ($crawlerControlAnalyticsEnabled -and $null -eq (Get-Listener 15433)) {
            $reasons.Add("Crawler-control database tunnel is not listening on port 15433.")
        }
    }

    return [pscustomobject]@{
        Ready = $reasons.Count -eq 0
        Reasons = @($reasons)
        State = $state
    }
}

function Get-FrontendHealth {
    $reasons = [Collections.Generic.List[string]]::new()
    $listener = Get-Listener $frontendPort
    if ($null -eq $listener) {
        $reasons.Add("Development web server is not listening on port $frontendPort.")
    }
    elseif (-not (Test-FrontendProcess ([int]$listener.OwningProcess))) {
        $reasons.Add("Port $frontendPort is owned by an unrecognized process; it will not be stopped.")
    }
    elseif (-not (Test-Http "http://127.0.0.1:$frontendPort/")) {
        $reasons.Add("Development web server HTTP health check failed.")
    }
    return [pscustomobject]@{
        Ready = $reasons.Count -eq 0
        Reasons = @($reasons)
        Listener = $listener
    }
}

function Get-OverallHealth {
    $ops = Get-OpsHealth
    $web = Get-FrontendHealth
    return [pscustomobject]@{
        Ready = [bool]$ops.Ready -and [bool]$web.Ready
        Ops = $ops
        Web = $web
    }
}

function Resolve-VerifiedSshIdentityFile(
    [string]$IdentityFile,
    [string]$Label
) {
    $path = [IO.Path]::GetFullPath($IdentityFile)
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "$Label identity file is missing: $path"
    }
    $rootPrefix = $root.TrimEnd('\') + '\'
    if ($path.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label identity file must be stored outside the MoonCen repository."
    }
    $item = Get-Item -LiteralPath $path
    if ($item.Length -lt 100 -or $item.Length -gt 1048576) {
        throw "$Label identity file size is invalid."
    }
    $broadSids = @("S-1-1-0", "S-1-5-11", "S-1-5-32-545")
    $acl = Get-Acl -LiteralPath $path
    $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    try {
        $ownerSid = (New-Object Security.Principal.NTAccount([string]$acl.Owner)).Translate(
            [Security.Principal.SecurityIdentifier]
        ).Value
    }
    catch {
        throw "$Label identity file owner could not be resolved."
    }
    if ($ownerSid -ne $currentSid) {
        throw "$Label identity file must be owned by the scheduled-task account."
    }
    $allowedSids = @($currentSid, "S-1-5-18", "S-1-5-32-544")
    foreach ($rule in $acl.Access) {
        if ($rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow) {
            continue
        }
        try {
            $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        }
        catch {
            throw "$Label identity file ACL identity could not be resolved."
        }
        if ($sid -in $broadSids) {
            throw "$Label identity file grants access to a broad Windows group: $sid"
        }
        if ($sid -and $sid -notin $allowedSids) {
            throw "$Label identity file grants access to an unexpected Windows identity: $sid"
        }
    }
    return $path
}

function Resolve-SshIdentity {
    if ($DataSource -ne "Cloud") {
        if ($SshIdentityFile -or $CrawlerControlSshIdentityFile) {
            throw "SSH identity files are allowed only with -DataSource Cloud."
        }
        $script:SshIdentityPath = ""
        $script:CrawlerControlSshIdentityPath = ""
        return
    }
    if (-not $SshIdentityFile) {
        throw "Cloud autostart requires -SshIdentityFile. The scheduled task must not depend on an interactive SSH agent."
    }
    $script:SshIdentityPath = Resolve-VerifiedSshIdentityFile $SshIdentityFile "Cloud SSH"
    $script:CrawlerControlSshIdentityPath = ""
    if ($crawlerControlAnalyticsEnabled) {
        $script:CrawlerControlSshIdentityPath = Resolve-VerifiedSshIdentityFile `
            $CrawlerControlSshIdentityFile `
            "Crawler-control SSH"
        if (
            $script:CrawlerControlSshIdentityPath.Equals(
                $script:SshIdentityPath,
                [StringComparison]::OrdinalIgnoreCase
            )
        ) {
            throw "Crawler-control SSH must use a separate identity file from the production cloud tunnel."
        }
    }
    $knownHosts = Join-Path $env:USERPROFILE ".ssh\known_hosts"
    if (-not (Test-Path -LiteralPath $knownHosts -PathType Leaf)) {
        throw "SSH known_hosts is missing for the scheduled-task account."
    }
}

function ConvertTo-NativeArgument([string]$Value) {
    if ($Value -notmatch '[\s"]') {
        return $Value
    }
    $escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    return '"' + $escaped + '"'
}

function Test-SshEndpointReady(
    [string]$Target,
    [string]$IdentityPath,
    [string]$Label
) {
    $ssh = Get-Command ssh.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $ssh) {
        throw "Windows OpenSSH client is unavailable."
    }
    $arguments = @(
        "-T", "-n",
        "-i", $IdentityPath,
        "-o", "IdentitiesOnly=yes",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "UpdateHostKeys=no",
        "-o", "PreferredAuthentications=publickey",
        "-o", "PasswordAuthentication=no",
        "-o", "KbdInteractiveAuthentication=no",
        "-o", "NumberOfPasswordPrompts=0",
        $Target,
        "true"
    )
    $startInfo = New-Object Diagnostics.ProcessStartInfo
    $startInfo.FileName = $ssh.Source
    $nativeArguments = @($arguments | ForEach-Object { ConvertTo-NativeArgument -Value ([string]$_) })
    $startInfo.Arguments = $nativeArguments -join " "
    $startInfo.WorkingDirectory = $root
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $startInfo
    try {
        if (-not $process.Start()) {
            throw "$Label preflight could not start."
        }
        if (-not $process.WaitForExit(15000)) {
            $process.Kill()
            throw "$Label preflight timed out. Verify the key and Tailscale SSH authorization."
        }
        if ($process.ExitCode -ne 0) {
            throw "$Label preflight failed. Verify the key, known_hosts, and Tailscale SSH authorization."
        }
    }
    finally {
        $process.Dispose()
    }
}

function Test-CloudSshReady {
    if ($DataSource -ne "Cloud") {
        return
    }
    Resolve-SshIdentity
    Test-SshEndpointReady $SshTarget $script:SshIdentityPath "Cloud SSH"
    if ($crawlerControlAnalyticsEnabled) {
        Test-SshEndpointReady `
            $CrawlerControlSshTarget `
            $script:CrawlerControlSshIdentityPath `
            "Crawler-control SSH"
    }
}

function Initialize-NodeToolchain {
    $node = Get-Command node.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $node -or $null -eq $npm) {
        $wingetPackages = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
        $nodeCandidates = @(
            Get-ChildItem -LiteralPath $wingetPackages -Directory -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -like "OpenJS.NodeJS.LTS_*" } |
                ForEach-Object {
                    Get-ChildItem -LiteralPath $_.FullName -Filter node.exe -File -Recurse -ErrorAction SilentlyContinue
                } |
                Sort-Object LastWriteTimeUtc -Descending
        )
        foreach ($candidate in $nodeCandidates) {
            $directory = $candidate.DirectoryName
            if (Test-Path -LiteralPath (Join-Path $directory "npm.cmd") -PathType Leaf) {
                $entries = @($env:PATH -split ';' | Where-Object { $_ })
                if ($entries -notcontains $directory) {
                    $env:PATH = "$directory;$env:PATH"
                }
                break
            }
        }
        $node = Get-Command node.exe -ErrorAction SilentlyContinue | Select-Object -First 1
        $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue | Select-Object -First 1
    }
    if ($null -eq $node -or $null -eq $npm) {
        throw "Node.js and npm are unavailable to the scheduled-task account."
    }
    $versionOutput = @(& $node.Source --version 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $versionOutput) {
        throw "Node.js version probe failed for the scheduled-task account."
    }
    $match = [regex]::Match([string]$versionOutput[0], '^v(?<major>\d+)\.(?<minor>\d+)\.')
    if (-not $match.Success) {
        throw "Node.js returned an invalid version string."
    }
    $major = [int]$match.Groups["major"].Value
    $minor = [int]$match.Groups["minor"].Value
    if ($major -lt 22 -or ($major -eq 22 -and $minor -lt 22)) {
        throw "Node.js 22.22 or newer is required for development autostart."
    }
}

function Assert-Prerequisites([bool]$ProbeSsh) {
    foreach ($path in @(
        $opsLauncher,
        $devLauncher,
        (Join-Path $root "venv_clean\Scripts\python.exe"),
        (Join-Path $root "ops-console\node_modules\vite\bin\vite.js"),
        (Join-Path $frontendRoot "node_modules"),
        (Join-Path $frontendRoot "package.json"),
        $powerShellExe
    )) {
        if (-not (Test-Path -LiteralPath $path)) {
            throw "Autostart prerequisite is missing: $path"
        }
    }
    Initialize-NodeToolchain
    if ($DataSource -eq "Cloud") {
        Resolve-SshIdentity
        if ($ProbeSsh) {
            Test-CloudSshReady
        }
    }
    elseif ($null -eq (Get-Listener 5432)) {
        throw "Local data mode requires PostgreSQL to listen on port 5432."
    }
}

function Invoke-Launcher([string]$ScriptPath, [string[]]$Arguments, [string]$Label) {
    Write-AutostartEvent INFO "Running $Label."
    New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
    $nativeArguments = @(
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy", "Bypass",
        "-File", $ScriptPath
    ) + @($Arguments)
    $argumentLine = @($nativeArguments | ForEach-Object { ConvertTo-NativeArgument -Value ([string]$_) }) -join " "
    $exitCode = 1
    $process = $null
    try {
        $startInfo = New-Object Diagnostics.ProcessStartInfo
        $startInfo.FileName = $powerShellExe
        $startInfo.Arguments = $argumentLine
        $startInfo.WorkingDirectory = $root
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true
        # Do not redirect the launcher streams. Long-lived children spawned by
        # the launcher inherit redirected handles on Windows PowerShell 5.1,
        # which either blocks ReadToEnd forever or leaves temporary log files
        # locked after the launcher itself exits.
        $startInfo.RedirectStandardOutput = $false
        $startInfo.RedirectStandardError = $false
        $process = New-Object Diagnostics.Process
        $process.StartInfo = $startInfo
        if (-not $process.Start()) {
            throw "$Label launcher process could not start."
        }
        if (-not $process.WaitForExit(180000)) {
            $taskkill = Join-Path $env:SystemRoot "System32\taskkill.exe"
            & $taskkill /PID $process.Id /T /F *> $null
            $process.WaitForExit(10000) | Out-Null
            throw "$Label timed out after 180 seconds; only its verified launcher process tree was terminated."
        }
        # Diagnostics.Process retains a numeric ExitCode on Windows PowerShell
        # 5.1, unlike Start-Process -PassThru with redirected streams here.
        $exitCode = [int]$process.ExitCode
    }
    finally {
        if ($null -ne $process) {
            $process.Dispose()
        }
    }
    if ($exitCode -ne 0) {
        throw "$Label failed with exit code $exitCode."
    }
    Write-AutostartEvent INFO "$Label completed successfully."
}

function Assert-OpsPortOwnership([object]$State) {
    $portOwners = @{
        5175 = "console"
        8001 = "api"
        18001 = "ssh-tunnel"
        15432 = "ssh-tunnel"
        15433 = "crawler-control-ssh-tunnel"
    }
    foreach ($port in $opsPorts) {
        if ($DataSource -eq "Local" -and $port -in @(18001, 15432, 15433)) {
            continue
        }
        $listener = Get-Listener $port
        if ($null -eq $listener) {
            continue
        }
        $entry = Get-OpsEntry -State $State -Name $portOwners[$port]
        if ($null -eq $entry -or -not (Test-OpsProcessEntry $entry)) {
            throw "Port $port is occupied without a verified Ops owner; no process was stopped."
        }
        if (-not (Test-ProcessDescendsFrom -ProcessId ([int]$listener.OwningProcess) -ExpectedAncestorId ([int]$entry.pid))) {
            throw "Port $port belongs to a process outside the verified Ops process tree; no process was stopped."
        }
    }
}

function Assert-NoUntrackedOpsProcesses([object]$State) {
    $tracked = @{}
    if ($null -ne $State -and $null -ne $State.PSObject.Properties["processes"]) {
        foreach ($entry in @($State.processes)) {
            if (Test-OpsProcessEntry $entry) {
                $tracked[[int]$entry.pid] = $true
            }
        }
    }
    $patterns = @(
        "backend.main:app",
        "ops_agent.status_agent",
        "ops_agent.crawler_scheduler",
        "ops_agent.crawler_worker",
        "ops_agent.quality_worker"
    )
    foreach ($process in @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) {
        $commandLine = [string]$process.CommandLine
        if (-not $commandLine -or $commandLine.IndexOf($root, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
            continue
        }
        $matchesOps = $false
        foreach ($pattern in $patterns) {
            if ($commandLine.IndexOf($pattern, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
                $matchesOps = $true
                break
            }
        }
        if ($matchesOps -and -not $tracked.ContainsKey([int]$process.ProcessId)) {
            $isTrackedDescendant = $false
            foreach ($trackedRoot in @($tracked.Keys)) {
                if (Test-ProcessDescendsFrom -ProcessId ([int]$process.ProcessId) -ExpectedAncestorId ([int]$trackedRoot)) {
                    $isTrackedDescendant = $true
                    break
                }
            }
            if (-not $isTrackedDescendant) {
                throw "An untracked MoonCen Ops process is running (PID $($process.ProcessId)); no process was stopped."
            }
        }
    }
}

function Ensure-Ops {
    $health = Get-OpsHealth
    if ($health.Ready) {
        return
    }
    $observedRunning = $false
    if ($null -ne $health.State) {
        foreach ($entry in @($health.State.processes)) {
            if (Test-OpsProcessEntry $entry) {
                $observedRunning = $true
                break
            }
        }
    }
    if (-not $observedRunning) {
        foreach ($port in $opsPorts) {
            if ($DataSource -eq "Local" -and $port -in @(18001, 15432, 15433)) {
                continue
            }
            if ($null -ne (Get-Listener $port)) {
                $observedRunning = $true
                break
            }
        }
    }
    if ($observedRunning) {
        Start-Sleep -Seconds 5
        $health = Get-OpsHealth
        if ($health.Ready) {
            return
        }
        Start-Sleep -Seconds 5
        $health = Get-OpsHealth
        if ($health.Ready) {
            return
        }
    }
    Assert-Prerequisites -ProbeSsh ($DataSource -eq "Cloud")
    Assert-OpsPortOwnership -State $health.State
    Assert-NoUntrackedOpsProcesses -State $health.State

    $hasLiveManagedProcess = $false
    if ($null -ne $health.State) {
        foreach ($entry in @($health.State.processes)) {
            if (Test-OpsProcessEntry $entry) {
                $hasLiveManagedProcess = $true
                break
            }
        }
    }
    $hasListener = $false
    foreach ($port in $opsPorts) {
        if ($DataSource -eq "Local" -and $port -in @(18001, 15432, 15433)) {
            continue
        }
        if ($null -ne (Get-Listener $port)) {
            $hasListener = $true
            break
        }
    }
    $opsAction = if ($hasLiveManagedProcess -or $hasListener) { "Restart" } else { "Start" }
    $arguments = @("-Action", $opsAction, "-DataSource", $DataSource)
    if ($DataSource -eq "Cloud") {
        $arguments += @(
            "-SshTarget", $SshTarget,
            "-SshIdentityFile", $script:SshIdentityPath
        )
        if ($crawlerControlAnalyticsEnabled) {
            $arguments += @(
                "-CrawlerControlSshTarget", $CrawlerControlSshTarget,
                "-CrawlerControlSshIdentityFile", $script:CrawlerControlSshIdentityPath
            )
        }
    }
    Invoke-Launcher -ScriptPath $opsLauncher -Arguments $arguments -Label "Ops Console $opsAction"
    $after = Get-OpsHealth
    if (-not $after.Ready) {
        throw "Ops Console launcher returned but required health checks did not pass."
    }
}

function Stop-VerifiedFrontend {
    $listener = Get-Listener $frontendPort
    if ($null -eq $listener) {
        return
    }
    $processId = [int]$listener.OwningProcess
    if (-not (Test-FrontendProcess $processId)) {
        throw "Port $frontendPort is owned by an unrecognized process; no process was stopped."
    }
    Stop-Process -Id $processId -Force -ErrorAction Stop
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if ($null -eq (Get-Listener $frontendPort)) {
            return
        }
        Start-Sleep -Milliseconds 200
    }
    throw "Verified development web process did not release port $frontendPort."
}

function Ensure-Frontend {
    $ops = Get-OpsHealth
    if (-not $ops.Ready) {
        throw "Development web server will not start before the shared Ops API is healthy."
    }
    $health = Get-FrontendHealth
    if ($health.Ready) {
        return
    }
    Assert-Prerequisites -ProbeSsh $false
    if ($null -ne $health.Listener) {
        if (-not (Test-FrontendProcess ([int]$health.Listener.OwningProcess))) {
            throw "Port $frontendPort is occupied by an unrecognized process; no process was stopped."
        }
        Stop-VerifiedFrontend
    }
    Invoke-Launcher -ScriptPath $devLauncher -Arguments @(
        "-FrontendOnly",
        "-ApiPort", "$apiPort",
        "-FrontendPort", "$frontendPort",
        "-StartupTimeoutSec", "60"
    ) -Label "development web server"
    $after = Get-FrontendHealth
    if (-not $after.Ready) {
        throw "Development web launcher returned but its ownership or health check failed."
    }
}

function Ensure-DevelopmentServices {
    Ensure-Ops
    Ensure-Frontend
    $health = Get-OverallHealth
    if (-not $health.Ready) {
        throw "Development services did not reach the complete ready state."
    }
}

function Stop-DevelopmentServices {
    Stop-VerifiedFrontend
    $arguments = @("-Action", "Stop", "-DataSource", $DataSource)
    if ($DataSource -eq "Cloud") {
        $arguments += @("-SshTarget", $SshTarget)
        if ($crawlerControlAnalyticsEnabled) {
            $arguments += @("-CrawlerControlSshTarget", $CrawlerControlSshTarget)
        }
    }
    Invoke-Launcher -ScriptPath $opsLauncher -Arguments $arguments -Label "Ops Console stop"
}

function Get-SupervisorHealth {
    $state = Get-JsonFile $statePath
    if (
        $null -eq $state -or
        [string]$state.workspace -ne $root -or
        [string]$state.data_source -ne $DataSource -or
        ($DataSource -eq "Cloud" -and [string]$state.ssh_target -ne $SshTarget) -or
        (
            $DataSource -eq "Cloud" -and
            [bool]$state.crawler_control_analytics_enabled -ne $crawlerControlAnalyticsEnabled
        ) -or
        (
            $crawlerControlAnalyticsEnabled -and
            [string]$state.crawler_control_ssh_target -ne $CrawlerControlSshTarget
        )
    ) {
        return [pscustomobject]@{ Running = $false; Reason = "Supervisor state is missing or does not match this workspace." }
    }
    try {
        $heartbeatAt = [datetime]::Parse(
            [string]$state.heartbeat_at,
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::RoundtripKind
        ).ToUniversalTime()
        $process = Get-Process -Id ([int]$state.pid) -ErrorAction Stop
        $expectedStart = [datetime]::Parse(
            [string]$state.process_started_at,
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::RoundtripKind
        ).ToUniversalTime()
        try {
            $actualStart = $process.StartTime.ToUniversalTime()
        }
        catch {
            $cim = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$state.pid)" -ErrorAction Stop
            $actualStart = ([datetime]$cim.CreationDate).ToUniversalTime()
        }
        if ([Math]::Abs(($actualStart - $expectedStart).TotalSeconds) -gt 2) {
            throw "PID start time mismatch"
        }
        if (((Get-Date).ToUniversalTime() - $heartbeatAt).TotalSeconds -gt ($MaxRetrySec + 90)) {
            throw "Supervisor heartbeat is stale"
        }
        return [pscustomobject]@{ Running = $true; Reason = "" }
    }
    catch {
        return [pscustomobject]@{ Running = $false; Reason = "Supervisor process or heartbeat is stale." }
    }
}

function Show-DevelopmentStatus {
    $health = Get-OverallHealth
    $supervisor = Get-SupervisorHealth
    [pscustomobject]@{
        Supervisor = if ($supervisor.Running) { "running" } else { "stopped" }
        OpsConsole = if ($health.Ops.Ready) { "ready" } else { "not-ready" }
        WebServer = if ($health.Web.Ready) { "ready" } else { "not-ready" }
        OpsUrl = "http://127.0.0.1:5175/"
        WebUrl = "http://127.0.0.1:5174/"
        ApiUrl = "http://127.0.0.1:8001/health"
    } | Format-List | Out-Host
    foreach ($reason in @($health.Ops.Reasons + $health.Web.Reasons + @($supervisor.Reason))) {
        if ($reason) {
            Write-Host "- $reason"
        }
    }
    return [bool]$supervisor.Running -and [bool]$health.Ready
}

try {
    switch ($Action) {
        "Preflight" {
            Assert-Prerequisites -ProbeSsh ($DataSource -eq "Cloud")
            Write-Host "MoonCen development autostart prerequisites are ready."
            exit 0
        }
        "Status" {
            $ready = Show-DevelopmentStatus
            if ($ready) { exit 0 }
            exit 1
        }
        "Ensure" {
            Enter-SupervisorLock -AllowExisting $false | Out-Null
            Write-AutostartEvent INFO "Ensuring Ops Console and development web services."
            Ensure-DevelopmentServices
            Set-SupervisorState -Status "ready"
            Write-AutostartEvent INFO "Development services are ready."
            exit 0
        }
        "Stop" {
            Enter-SupervisorLock -AllowExisting $false | Out-Null
            Write-AutostartEvent INFO "Stopping verified MoonCen development services."
            Stop-DevelopmentServices
            Set-SupervisorState -Status "stopped"
            Write-AutostartEvent INFO "Verified MoonCen development services stopped."
            exit 0
        }
        default {
            if (-not (Enter-SupervisorLock -AllowExisting $true)) {
                Write-Host "MoonCen development supervisor is already running."
                exit 0
            }
            Assert-Prerequisites -ProbeSsh $false
            Write-AutostartEvent INFO "Development supervisor started for data source $DataSource."
            $failureCount = 0
            while ($true) {
                try {
                    Ensure-DevelopmentServices
                    $failureCount = 0
                    $next = (Get-Date).AddSeconds($CheckIntervalSec)
                    Set-SupervisorState -Status "ready" -NextRetryAt $next
                    Start-Sleep -Seconds $CheckIntervalSec
                }
                catch {
                    $failureCount++
                    $power = [Math]::Min($failureCount - 1, 8)
                    $baseDelay = [Math]::Min($MaxRetrySec, $CheckIntervalSec * [Math]::Pow(2, $power))
                    $jitterLimit = [Math]::Max(1, [int]($baseDelay * 0.1))
                    $delay = [int][Math]::Min($MaxRetrySec, $baseDelay + (Get-Random -Minimum 0 -Maximum ($jitterLimit + 1)))
                    $next = (Get-Date).AddSeconds($delay)
                    $message = Protect-LogText $_.Exception.Message
                    Set-SupervisorState -Status "retrying" -LastError $message -NextRetryAt $next
                    Write-AutostartEvent WARN "$message Retrying in $delay seconds."
                    Start-Sleep -Seconds $delay
                }
            }
        }
    }
}
catch {
    $message = Protect-LogText $_.Exception.Message
    if ($Action -in @("Monitor", "Ensure", "Stop")) {
        try {
            Set-SupervisorState -Status "failed" -LastError $message
            Write-AutostartEvent ERROR $message
        }
        catch {
        }
    }
    [Console]::Error.WriteLine("MoonCen development autostart failed: $message")
    exit 1
}
finally {
    Exit-SupervisorLock
}
