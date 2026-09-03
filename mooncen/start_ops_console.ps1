[CmdletBinding()]
param(
    [ValidateSet("Start", "Stop", "Restart", "RefreshControl", "Status")]
    [string]$Action = "Start",
    [ValidateSet("Cloud", "Local")]
    [string]$DataSource = "Cloud",
    [ValidatePattern('^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+$')]
    [string]$SshTarget = "ubuntu@cloud",
    [string]$SshIdentityFile = "",
    [ValidatePattern('^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+$')]
    [string]$CrawlerControlSshTarget = "sgm@gen1db",
    [string]$CrawlerControlSshIdentityFile = "",
    [switch]$EnableLocalCrawlerRuntime
)

$ErrorActionPreference = "Stop"
# Never let an ambient Windows Process/User/Machine value leak into SSH,
# frontend, or worker children. Cloud mode re-injects the validated protected
# pair into the API child only; Local mode may still load an explicit repo .env.
foreach ($analyticsEnvironmentName in @(
    "OPS_CLOUDFLARE_ANALYTICS_ZONE_ID",
    "OPS_CLOUDFLARE_ANALYTICS_TOKEN"
)) {
    [Environment]::SetEnvironmentVariable($analyticsEnvironmentName, $null, "Process")
}
$root = [IO.Path]::GetFullPath($PSScriptRoot)
$python = Join-Path $root "venv_clean\Scripts\python.exe"
$opsDir = Join-Path $root "ops-console"
$vite = Join-Path $opsDir "node_modules\vite\bin\vite.js"
$stateDir = Join-Path $root "logs\ops-console-local"
$statePath = Join-Path $stateDir "processes.json"
$deploymentHeartbeat = Join-Path $stateDir "deployment-worker.heartbeat.json"
$apiStandardOutputLog = Join-Path $stateDir "api.stdout.log"
$apiStandardErrorLog = Join-Path $stateDir "api.stderr.log"
$cloudSshTarget = $SshTarget
$cloudTunnelAddress = "127.0.0.1"
$cloudTunnelPort = 18001
$cloudApiAddress = "127.0.0.1"
$cloudApiPort = 8001
$cloudTunnelForward = "127.0.0.1:18001:127.0.0.1:8001"
$cloudDbTunnelAddress = "127.0.0.1"
$cloudDbTunnelPort = 15432
$cloudDbTunnelForward = "127.0.0.1:15432:127.0.0.1:5432"
$crawlerControlDbTunnelAddress = "127.0.0.1"
$crawlerControlDbTunnelPort = 15433
$crawlerControlDbTunnelForward = "127.0.0.1:15433:127.0.0.1:5432"
$crawlerControlEnvironmentEnabled = [bool]$CrawlerControlSshIdentityFile
$cloudRemoteDir = "/opt/mooncen"
$nonApiAnalyticsEnvironment = @{
    OPS_CLOUDFLARE_ANALYTICS_ZONE_ID = ""
    OPS_CLOUDFLARE_ANALYTICS_TOKEN = ""
}

if ($EnableLocalCrawlerRuntime -and $DataSource -ne "Local") {
    throw "-EnableLocalCrawlerRuntime is allowed only with -DataSource Local."
}
if ($SshIdentityFile -and $DataSource -ne "Cloud") {
    throw "-SshIdentityFile is allowed only with -DataSource Cloud."
}
if ($CrawlerControlSshIdentityFile -and $DataSource -ne "Cloud") {
    throw "-CrawlerControlSshIdentityFile is allowed only with -DataSource Cloud."
}

function Resolve-ExternalIdentityPath([string]$Path, [string]$Label) {
    if (-not $Path) {
        return ""
    }
    $resolved = [IO.Path]::GetFullPath($Path)
    if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
        throw "$Label identity file is missing: $resolved"
    }
    $rootPrefix = $root.TrimEnd('\') + '\'
    if ($resolved.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label identity file must be stored outside the MoonCen repository."
    }
    return $resolved
}

$SshIdentityFile = Resolve-ExternalIdentityPath $SshIdentityFile "Cloud SSH"
$CrawlerControlSshIdentityFile = Resolve-ExternalIdentityPath `
    $CrawlerControlSshIdentityFile "Crawler-control SSH"
if (
    $CrawlerControlSshIdentityFile -and
    $SshIdentityFile -and
    $CrawlerControlSshIdentityFile.Equals($SshIdentityFile, [StringComparison]::OrdinalIgnoreCase)
) {
    throw "Crawler-control SSH must use a separate identity file from the production cloud tunnel."
}

function Get-SshIdentityArguments {
    if (-not $SshIdentityFile) {
        return @()
    }
    return @("-i", $SshIdentityFile, "-o", "IdentitiesOnly=yes")
}

function Get-CrawlerControlSshIdentityArguments {
    if (-not $CrawlerControlSshIdentityFile) {
        return @()
    }
    return @("-i", $CrawlerControlSshIdentityFile, "-o", "IdentitiesOnly=yes")
}

function ConvertTo-NativeArgument([string]$Value) {
    if ($Value -notmatch '[\s"]') {
        return $Value
    }
    $escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    return '"' + $escaped + '"'
}

function Get-WorkspaceProcess([int]$ProcessId) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        return $null
    }
    $commandLine = [string]$process.CommandLine
    if (-not $commandLine.StartsWith('"')) {
        $commandLine = $commandLine.Trim()
    }
    if ($commandLine.IndexOf($root, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
        return $null
    }
    return $process
}

function Get-ProcessExecutablePath([Diagnostics.Process]$Process) {
    try {
        $path = [string]$Process.Path
        if ($path) {
            return [IO.Path]::GetFullPath($path)
        }
    }
    catch {
    }
    $snapshot = Get-CimInstance Win32_Process -Filter "ProcessId = $($Process.Id)" -ErrorAction SilentlyContinue
    if ($null -ne $snapshot -and [string]$snapshot.ExecutablePath) {
        return [IO.Path]::GetFullPath([string]$snapshot.ExecutablePath)
    }
    return ""
}

function New-ManagedProcessEntry(
    [string]$Name,
    [Diagnostics.Process]$Process,
    [Diagnostics.Process]$LauncherProcess = $null,
    [int[]]$ListenerPorts = @(),
    [string]$HeartbeatPath = ""
) {
    if ($null -eq $LauncherProcess) {
        $LauncherProcess = $Process
    }
    return [pscustomobject]@{
        name = $Name
        pid = $Process.Id
        process_started_at = $Process.StartTime.ToUniversalTime().ToString("o")
        process_name = $Process.ProcessName
        executable_path = Get-ProcessExecutablePath $Process
        launcher_pid = $LauncherProcess.Id
        launcher_started_at = $LauncherProcess.StartTime.ToUniversalTime().ToString("o")
        launcher_process_name = $LauncherProcess.ProcessName
        launcher_executable_path = Get-ProcessExecutablePath $LauncherProcess
        listener_ports = @($ListenerPorts)
        heartbeat_path = $HeartbeatPath
    }
}

function Get-ProcessCreationTime([int]$ProcessId) {
    $snapshot = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if ($null -ne $snapshot -and $null -ne $snapshot.CreationDate) {
        return ([datetime]$snapshot.CreationDate).ToUniversalTime()
    }
    try {
        $startedAt = (Get-Process -Id $ProcessId -ErrorAction Stop).StartTime
        if ($null -ne $startedAt) {
            return $startedAt.ToUniversalTime()
        }
    }
    catch {
    }
    return $null
}

function Test-ProcessStartTimeValue(
    [int]$ProcessId,
    [string]$ExpectedValue
) {
    if (-not $ExpectedValue) {
        return $false
    }
    try {
        $expected = [datetime]::Parse(
            $ExpectedValue,
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::RoundtripKind
        ).ToUniversalTime()
        $actual = Get-ProcessCreationTime $ProcessId
        if ($null -eq $actual) {
            return $false
        }
        return [Math]::Abs(($actual - $expected).TotalSeconds) -le 2
    }
    catch {
        return $false
    }
}

function Test-ManagedProcessStartTime(
    [int]$ProcessId,
    [object]$Entry
) {
    $property = $Entry.PSObject.Properties["process_started_at"]
    if ($null -eq $property) {
        return $false
    }
    return Test-ProcessStartTimeValue $ProcessId ([string]$property.Value)
}

function Test-ProcessDescendsFrom(
    [int]$ProcessId,
    [int]$ExpectedAncestorId
) {
    $current = $ProcessId
    $visited = @{}
    for ($depth = 0; $depth -lt 64 -and $current -gt 0; $depth++) {
        if ($current -eq $ExpectedAncestorId) {
            return $true
        }
        if ($visited.ContainsKey($current)) {
            return $false
        }
        $visited[$current] = $true
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $current" -ErrorAction SilentlyContinue
        if ($null -eq $process) {
            return $false
        }
        $current = [int]$process.ParentProcessId
    }
    return $false
}

function Get-ExpectedProcessNames([string]$Name) {
    $expected = @{
        "ssh-tunnel" = @("ssh.exe", "ssh")
        "crawler-control-ssh-tunnel" = @("ssh.exe", "ssh")
        "api" = @("python.exe", "python")
        "console" = @("node.exe", "node")
        "status-agent" = @("python.exe", "python")
        "crawler-scheduler" = @("python.exe", "python")
        "crawler-worker" = @("python.exe", "python")
        "quality-worker" = @("python.exe", "python")
        "deployment-worker" = @("python.exe", "python")
    }
    if (-not $expected.ContainsKey($Name)) {
        return @()
    }
    return @($expected[$Name])
}

function Get-ExpectedListenerPorts([string]$Name) {
    $ports = @{
        "ssh-tunnel" = @($cloudTunnelPort, $cloudDbTunnelPort)
        "crawler-control-ssh-tunnel" = @($crawlerControlDbTunnelPort)
        "api" = @($cloudApiPort)
        "console" = @(5175)
    }
    if (-not $ports.ContainsKey($Name)) {
        return @()
    }
    return @($ports[$Name])
}

function Test-ListenerRuntimeOwnership(
    [int]$RuntimeProcessId,
    [int]$LauncherProcessId,
    [int[]]$Ports,
    [switch]$RequireExactRuntime
) {
    if ($Ports.Count -eq 0) {
        return $false
    }
    foreach ($port in $Ports) {
        $ownerIds = @(
            Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
                Select-Object -ExpandProperty OwningProcess -Unique
        )
        if ($ownerIds.Count -ne 1) {
            return $false
        }
        $ownerId = [int]$ownerIds[0]
        if ($RequireExactRuntime) {
            if ($ownerId -ne $RuntimeProcessId) {
                return $false
            }
        }
        elseif (-not (Test-ProcessDescendsFrom $ownerId $LauncherProcessId)) {
            return $false
        }
    }
    return $true
}

function Test-HeartbeatRuntimeOwnership(
    [int]$RuntimeProcessId,
    [int]$LauncherProcessId,
    [string]$HeartbeatPath,
    [switch]$RequireExactRuntime
) {
    if (-not $HeartbeatPath -or -not (Test-Path -LiteralPath $HeartbeatPath -PathType Leaf)) {
        return $false
    }
    try {
        if ([IO.Path]::GetFullPath($HeartbeatPath) -ne [IO.Path]::GetFullPath($deploymentHeartbeat)) {
            return $false
        }
        $heartbeat = Get-Content -LiteralPath $HeartbeatPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($null -eq $heartbeat.PSObject.Properties["pid"]) {
            return $false
        }
        $heartbeatProcessId = [int]$heartbeat.pid
        if ($RequireExactRuntime) {
            return $heartbeatProcessId -eq $RuntimeProcessId
        }
        return Test-ProcessDescendsFrom $heartbeatProcessId $LauncherProcessId
    }
    catch {
        return $false
    }
}

function Get-StoredProcess(
    [int]$ProcessId,
    [string]$StartedAt,
    [string]$ExpectedProcessName = "",
    [string]$ExpectedExecutablePath = ""
) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if ($null -eq $process -or -not (Test-ProcessStartTimeValue $ProcessId $StartedAt)) {
        return $null
    }
    if ($ExpectedProcessName) {
        $normalizedExpectedName = [IO.Path]::GetFileNameWithoutExtension($ExpectedProcessName)
        $normalizedActualName = [IO.Path]::GetFileNameWithoutExtension([string]$process.Name)
        if (-not $normalizedActualName.Equals($normalizedExpectedName, [StringComparison]::OrdinalIgnoreCase)) {
            return $null
        }
    }
    if ($ExpectedExecutablePath -and [string]$process.ExecutablePath) {
        $actualPath = [IO.Path]::GetFullPath([string]$process.ExecutablePath)
        $expectedPath = [IO.Path]::GetFullPath($ExpectedExecutablePath)
        if (-not $actualPath.Equals($expectedPath, [StringComparison]::OrdinalIgnoreCase)) {
            return $null
        }
    }
    return $process
}

function New-ListenerManagedProcessEntry(
    [string]$Name,
    [Diagnostics.Process]$LauncherProcess,
    [int[]]$Ports
) {
    $ownerIds = @()
    foreach ($port in $Ports) {
        $portOwnerIds = @(
            Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
                Select-Object -ExpandProperty OwningProcess -Unique
        )
        if ($portOwnerIds.Count -ne 1) {
            throw "$Name runtime ownership is ambiguous on port $port."
        }
        $ownerIds += [int]$portOwnerIds[0]
    }
    $ownerIds = @($ownerIds | Select-Object -Unique)
    if ($ownerIds.Count -ne 1) {
        throw "$Name listeners are owned by different processes."
    }
    $runtimeProcessId = [int]$ownerIds[0]
    if (-not (Test-ProcessDescendsFrom $runtimeProcessId $LauncherProcess.Id)) {
        throw "$Name listener is outside its verified launcher process tree."
    }
    $runtimeProcess = Get-Process -Id $runtimeProcessId -ErrorAction Stop
    return New-ManagedProcessEntry $Name $runtimeProcess $LauncherProcess $Ports
}

function New-HeartbeatManagedProcessEntry(
    [string]$Name,
    [Diagnostics.Process]$LauncherProcess,
    [string]$HeartbeatPath
) {
    try {
        $heartbeat = Get-Content -LiteralPath $HeartbeatPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($null -eq $heartbeat.PSObject.Properties["pid"]) {
            throw "Heartbeat PID is missing."
        }
        $runtimeProcessId = [int]$heartbeat.pid
    }
    catch {
        throw "$Name heartbeat identity is invalid: $($_.Exception.Message)"
    }
    if (-not (Test-ProcessDescendsFrom $runtimeProcessId $LauncherProcess.Id)) {
        throw "$Name heartbeat process is outside its verified launcher process tree."
    }
    $runtimeProcess = Get-Process -Id $runtimeProcessId -ErrorAction Stop
    return New-ManagedProcessEntry $Name $runtimeProcess $LauncherProcess @() $HeartbeatPath
}

function Get-ManagedProcess([object]$Entry) {
    if (
        $null -eq $Entry -or
        $null -eq $Entry.PSObject.Properties["pid"] -or
        $null -eq $Entry.PSObject.Properties["name"] -or
        $null -eq $Entry.PSObject.Properties["process_started_at"]
    ) {
        return $null
    }
    $processId = [int]$Entry.pid
    $name = [string]$Entry.name
    $expectedNames = @(Get-ExpectedProcessNames $name)
    if ($expectedNames.Count -eq 0) {
        return $null
    }
    $storedProcessName = if ($null -ne $Entry.PSObject.Properties["process_name"]) {
        [string]$Entry.process_name
    }
    else {
        [IO.Path]::GetFileNameWithoutExtension([string]$expectedNames[0])
    }
    $storedExecutablePath = if ($null -ne $Entry.PSObject.Properties["executable_path"]) {
        [string]$Entry.executable_path
    }
    else {
        ""
    }
    $process = Get-StoredProcess $processId ([string]$Entry.process_started_at) $storedProcessName $storedExecutablePath
    if ($null -eq $process -or [string]$process.Name -notin $expectedNames) {
        return $null
    }

    $launcherProcessId = if ($null -ne $Entry.PSObject.Properties["launcher_pid"]) {
        [int]$Entry.launcher_pid
    }
    else {
        $processId
    }
    $launcherStartedAt = if ($null -ne $Entry.PSObject.Properties["launcher_started_at"]) {
        [string]$Entry.launcher_started_at
    }
    else {
        [string]$Entry.process_started_at
    }
    $launcherProcessName = if ($null -ne $Entry.PSObject.Properties["launcher_process_name"]) {
        [string]$Entry.launcher_process_name
    }
    else {
        $storedProcessName
    }
    $launcherExecutablePath = if ($null -ne $Entry.PSObject.Properties["launcher_executable_path"]) {
        [string]$Entry.launcher_executable_path
    }
    else {
        $storedExecutablePath
    }
    $launcher = Get-StoredProcess $launcherProcessId $launcherStartedAt $launcherProcessName $launcherExecutablePath
    if ($null -eq $launcher -or -not (Test-ProcessDescendsFrom $processId $launcherProcessId)) {
        return $null
    }

    $recordedListenerPorts = if ($null -ne $Entry.PSObject.Properties["listener_ports"]) {
        @($Entry.listener_ports | ForEach-Object { [int]$_ })
    }
    else {
        @()
    }
    $expectedListenerPorts = @(Get-ExpectedListenerPorts $name)
    $commandLine = [string]$process.CommandLine
    if ($recordedListenerPorts.Count -gt 0) {
        $recordedKey = (@($recordedListenerPorts | Sort-Object) -join ",")
        $expectedKey = (@($expectedListenerPorts | Sort-Object) -join ",")
        if (
            $recordedKey -ne $expectedKey -or
            -not (Test-ListenerRuntimeOwnership $processId $launcherProcessId $recordedListenerPorts -RequireExactRuntime)
        ) {
            return $null
        }
    }
    elseif (-not $commandLine -and $expectedListenerPorts.Count -gt 0) {
        # Compatibility with state written before runtime listener PIDs were
        # recorded. The exact launcher creation time and current process tree
        # must still own every expected listener.
        if (-not (Test-ListenerRuntimeOwnership $processId $launcherProcessId $expectedListenerPorts)) {
            return $null
        }
    }

    $heartbeatPath = if ($null -ne $Entry.PSObject.Properties["heartbeat_path"]) {
        [string]$Entry.heartbeat_path
    }
    else {
        ""
    }
    if ($heartbeatPath) {
        if (-not (Test-HeartbeatRuntimeOwnership $processId $launcherProcessId $heartbeatPath -RequireExactRuntime)) {
            return $null
        }
    }
    elseif (-not $commandLine -and $name -eq "deployment-worker") {
        if (-not (Test-HeartbeatRuntimeOwnership $processId $launcherProcessId $deploymentHeartbeat)) {
            return $null
        }
    }

    if ($commandLine) {
        if ($name -in @("ssh-tunnel", "crawler-control-ssh-tunnel")) {
            $sshTargetToken = if ($name -eq "ssh-tunnel") {
                $cloudSshTarget
            }
            else {
                $CrawlerControlSshTarget
            }
            $forwardToken = if ($name -eq "ssh-tunnel") {
                $cloudTunnelForward
            }
            else {
                $crawlerControlDbTunnelForward
            }
            foreach ($requiredToken in @(
                $sshTargetToken,
                $forwardToken,
                "BatchMode=yes",
                "ExitOnForwardFailure=yes"
            )) {
                if ($commandLine.IndexOf($requiredToken, [StringComparison]::Ordinal) -lt 0) {
                    return $null
                }
            }
        }
        elseif ($commandLine.IndexOf($root, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
            return $null
        }
    }
    return $process
}

function Get-State {
    if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

function Stop-VerifiedProcess(
    [int]$ProcessId,
    [string]$StartedAt
) {
    if (-not (Test-ProcessStartTimeValue $ProcessId $StartedAt)) {
        return
    }
    try {
        Stop-Process -Id $ProcessId -Force -ErrorAction Stop
    }
    catch {
        throw "Verified process PID $ProcessId could not be stopped: $($_.Exception.Message)"
    }
    $deadline = (Get-Date).AddSeconds(5)
    while ((Get-Date) -lt $deadline) {
        if (-not (Test-ProcessStartTimeValue $ProcessId $StartedAt)) {
            return
        }
        Start-Sleep -Milliseconds 100
    }
    throw "Verified process PID $ProcessId did not stop within 5 seconds."
}

function Stop-ProcessTree(
    [int]$ProcessId,
    [string]$StartedAt
) {
    $descendants = @()
    $pending = @([pscustomobject]@{ pid = $ProcessId; depth = 0 })
    $visited = @{}
    while ($pending.Count -gt 0) {
        $current = $pending[0]
        $pending = @($pending | Select-Object -Skip 1)
        $parentId = [int]$current.pid
        if ($visited.ContainsKey($parentId)) {
            continue
        }
        $visited[$parentId] = $true
        $children = @(
            Get-CimInstance Win32_Process -Filter "ParentProcessId = $parentId" -ErrorAction SilentlyContinue |
                # Console hosts are OS-owned infrastructure, not a managed
                # MoonCen child. Killing a Session 0 conhost is both unnecessary
                # and denied to the least-privilege scheduled-task account.
                Where-Object { [string]$_.Name -notin @("conhost.exe", "conhost") }
        )
        foreach ($child in $children) {
            $childEntry = [pscustomobject]@{
                pid = [int]$child.ProcessId
                depth = [int]$current.depth + 1
                created_at = ([datetime]$child.CreationDate).ToUniversalTime().ToString("o")
            }
            $descendants += $childEntry
            $pending += $childEntry
        }
    }
    foreach ($child in @($descendants | Sort-Object depth -Descending)) {
        Stop-VerifiedProcess ([int]$child.pid) ([string]$child.created_at)
    }
    Stop-VerifiedProcess $ProcessId $StartedAt
}

function Stop-ManagedProcessTree([object]$Entry) {
    $process = Get-ManagedProcess $Entry
    if ($null -eq $process) {
        $recordedIds = @([int]$Entry.pid)
        if ($null -ne $Entry.PSObject.Properties["launcher_pid"]) {
            $recordedIds += [int]$Entry.launcher_pid
        }
        foreach ($recordedId in @($recordedIds | Select-Object -Unique)) {
            if ($null -ne (Get-CimInstance Win32_Process -Filter "ProcessId = $recordedId" -ErrorAction SilentlyContinue)) {
                throw "Recorded $($Entry.name) PID $recordedId is running but its identity cannot be verified; no process was stopped."
            }
        }
        return
    }
    $launcherProcessId = if ($null -ne $Entry.PSObject.Properties["launcher_pid"]) {
        [int]$Entry.launcher_pid
    }
    else {
        [int]$Entry.pid
    }
    $launcherStartedAt = if ($null -ne $Entry.PSObject.Properties["launcher_started_at"]) {
        [string]$Entry.launcher_started_at
    }
    else {
        [string]$Entry.process_started_at
    }
    Stop-ProcessTree $launcherProcessId $launcherStartedAt
}

function Test-ActiveDeployment([object]$State) {
    if ($null -eq $State) {
        return $false
    }
    $entry = @($State.processes | Where-Object { $_.name -eq "deployment-worker" } | Select-Object -First 1)
    if ($entry.Count -eq 0 -or $null -eq (Get-ManagedProcess $entry[0])) {
        return $false
    }
    $pending = @([int]$entry[0].pid)
    $visited = @{}
    while ($pending.Count -gt 0) {
        $parentId = [int]$pending[0]
        $pending = @($pending | Select-Object -Skip 1)
        if ($visited.ContainsKey($parentId)) {
            continue
        }
        $visited[$parentId] = $true
        $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $parentId" -ErrorAction SilentlyContinue)
        foreach ($child in $children) {
            $commandLine = [string]$child.CommandLine
            if ($commandLine.IndexOf("deploy_mooncen.ps1", [StringComparison]::OrdinalIgnoreCase) -ge 0) {
                return $true
            }
            if (-not $commandLine -and [string]$child.Name -in @("powershell.exe", "powershell", "pwsh.exe", "pwsh")) {
                # Session 0 can hide command lines from an interactive status
                # process. A shell below the deployment worker may be the live
                # deploy script, so stopping must fail closed.
                return $true
            }
            $pending += [int]$child.ProcessId
        }
    }
    return $false
}

function Stop-OpsConsole {
    $state = Get-State
    if (Test-ActiveDeployment $state) {
        throw "An application deployment is active. Wait for it to finish or cancel it from Ops Console before stopping."
    }
    if ($null -ne $state) {
        $stopOrder = @(
            "console",
            "crawler-scheduler",
            "crawler-worker",
            "quality-worker",
            "status-agent",
            "deployment-worker",
            "api",
            "crawler-control-ssh-tunnel",
            "ssh-tunnel"
        )
        foreach ($name in $stopOrder) {
            foreach ($entry in @($state.processes | Where-Object { $_.name -eq $name })) {
                Stop-ManagedProcessTree $entry
            }
        }
    }
    if (Test-Path -LiteralPath $statePath -PathType Leaf) {
        [IO.File]::Delete($statePath)
    }
    Write-Host "Standalone Ops Console processes stopped."
}

function Show-Status {
    $state = Get-State
    $stateProcesses = if ($null -ne $state) { @($state.processes) } else { @() }
    $stateDataSource = if ($null -ne $state -and $state.data_source) {
        [string]$state.data_source
    }
    elseif (@($stateProcesses | Where-Object { $_.name -eq "ssh-tunnel" }).Count -gt 0) {
        "Cloud"
    }
    elseif (@($stateProcesses | Where-Object { $_.name -eq "api" }).Count -gt 0) {
        "Local"
    }
    else {
        "unknown"
    }
    Write-Host "Data source: $stateDataSource"
    $statusNames = @("ssh-tunnel")
    if ($null -ne $state -and [bool]$state.crawler_control_analytics_enabled) {
        $statusNames += "crawler-control-ssh-tunnel"
    }
    $statusNames += @("api", "console", "status-agent", "crawler-scheduler", "crawler-worker", "quality-worker", "deployment-worker")
    foreach ($name in $statusNames) {
        $entry = @($stateProcesses | Where-Object { $_.name -eq $name } | Select-Object -First 1)
        $running = $false
        $pidValue = $null
        if ($entry.Count -gt 0) {
            $pidValue = [int]$entry[0].pid
            $running = $null -ne (Get-ManagedProcess $entry[0])
        }
        [pscustomobject]@{
            Component = $name
            Running = $running
            PID = $pidValue
        }
    }
    $statusPorts = if ($stateDataSource -eq "Cloud") {
        $ports = @(5175, 8001, $cloudTunnelPort, $cloudDbTunnelPort)
        if ($null -ne $state -and [bool]$state.crawler_control_analytics_enabled) {
            $ports += $crawlerControlDbTunnelPort
        }
        $ports
    }
    elseif ($stateDataSource -eq "Local") {
        @(5175, 8001, 8765)
    }
    else {
        @(5175, $cloudTunnelPort, 8001, 8765)
    }
    foreach ($port in $statusPorts) {
        $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
        [pscustomobject]@{
            Component = "port-$port"
            Running = [bool]$listener
            PID = if ($listener) { $listener[0].OwningProcess } else { $null }
        }
    }
}

function Wait-Http(
    [string]$Url,
    [int]$Seconds = 30,
    [Diagnostics.Process]$GuardProcess = $null
) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    do {
        if ($null -ne $GuardProcess) {
            try {
                if ($GuardProcess.HasExited) {
                    return $false
                }
            }
            catch {
                return $false
            }
        }
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) {
                return $true
            }
        }
        catch {
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    return $false
}

function Wait-Listener(
    [int]$Port,
    [int]$Seconds = 30,
    [Diagnostics.Process]$GuardProcess = $null
) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    do {
        if ($null -ne $GuardProcess) {
            try {
                if ($GuardProcess.HasExited) {
                    return $false
                }
            }
            catch {
                return $false
            }
        }
        if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
            return $true
        }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $deadline)
    return $false
}

function Wait-DeploymentWorker(
    [int]$ProcessId,
    [datetime]$NotBefore,
    [int]$Seconds = 10
) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    do {
        if ($null -eq (Get-WorkspaceProcess $ProcessId)) {
            return $false
        }
        if (Test-Path -LiteralPath $deploymentHeartbeat -PathType Leaf) {
            $heartbeat = Get-Item -LiteralPath $deploymentHeartbeat
            if ($heartbeat.LastWriteTimeUtc -ge $NotBefore.ToUniversalTime()) {
                return $true
            }
        }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $deadline)
    return $false
}

function Invoke-CheckedNative(
    [string]$FilePath,
    [string[]]$Arguments,
    [string]$FailureMessage,
    [switch]$Quiet
) {
    # Windows PowerShell 5.1 turns native stderr into ErrorRecord objects.
    # Python logging commonly uses stderr even on success, so decide success
    # from the native exit code instead of $ErrorActionPreference.
    $previousErrorAction = $ErrorActionPreference
    $nativeOutput = @()
    $exitCode = 1
    try {
        $ErrorActionPreference = "Continue"
        $nativeOutput = @(& $FilePath @Arguments 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorAction
    }
    if (-not $Quiet) {
        foreach ($line in $nativeOutput) {
            Write-Host ([string]$line)
        }
    }
    if ($exitCode -ne 0) {
        throw "$FailureMessage (exit code $exitCode)"
    }
}

function Resolve-NodeExecutable {
    $command = Get-Command node -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }
    $wingetPackages = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
    $candidates = @(
        Get-ChildItem -LiteralPath $wingetPackages -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like "OpenJS.NodeJS.LTS_*" } |
            ForEach-Object {
                Get-ChildItem -LiteralPath $_.FullName -Filter node.exe -File -Recurse -ErrorAction SilentlyContinue
            } |
            Sort-Object LastWriteTimeUtc -Descending
    )
    if ($candidates.Count -gt 0) {
        return $candidates[0].FullName
    }
    throw "Node.js 22.22 or newer is unavailable."
}

function Resolve-SshExecutable {
    $command = Get-Command ssh.exe -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw "Windows OpenSSH client is unavailable. Install the OpenSSH Client capability."
    }
    return $command.Source
}

function Get-RemoteEnvironmentValue(
    [string]$SshExecutable,
    [string]$Name
) {
    if ($Name -notmatch '^[A-Z0-9_]+$') {
        throw "Invalid remote environment key name."
    }
    $sshArguments = @(
        "-T", "-n",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "UpdateHostKeys=no",
        "-o", "PreferredAuthentications=publickey",
        "-o", "PasswordAuthentication=no",
        "-o", "KbdInteractiveAuthentication=no",
        "-o", "NumberOfPasswordPrompts=0"
    )
    $sshArguments += @(Get-SshIdentityArguments)
    $candidateFiles = "'$cloudRemoteDir/.env' /etc/mooncen/api.env"
    $candidateFiles += ' "$HOME/.config/mooncen/deploy-secrets.env" "$HOME/.config/mooncen/migrator.env"'
    $command = "for file in $candidateFiles; do if [ -r `"`$file`" ]; then encoded=`$(grep -E '^${Name}_B64=' `"`$file`" | tail -n1 | cut -d= -f2-); if [ -n `"`$encoded`" ]; then printf '%s' `"`$encoded`"; exit 0; fi; raw=`$(grep -E '^${Name}=' `"`$file`" | tail -n1 | cut -d= -f2-); if [ -n `"`$raw`" ]; then printf '%s' `"`$raw`" | base64 | tr -d '\r\n'; exit 0; fi; fi; done"
    $encodedCommand = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($command))
    $transportCommand = "printf '%s' '$encodedCommand' | base64 -d | bash"
    $value = (& $SshExecutable @sshArguments $cloudSshTarget $transportCommand 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $value) {
        return ""
    }
    try {
        $encodedValue = "$($value | Select-Object -First 1)".Trim()
        return [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encodedValue))
    }
    catch {
        return ""
    }
}

function Get-LocalEnvironmentValue([string]$Name) {
    if ($Name -notmatch '^[A-Z0-9_]+$') {
        throw "Invalid local environment key name."
    }
    $path = Join-Path $root ".env"
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        return ""
    }
    $prefix = "$Name="
    $line = Get-Content -LiteralPath $path -Encoding UTF8 |
        Where-Object { $_.StartsWith($prefix, [StringComparison]::Ordinal) } |
        Select-Object -Last 1
    if (-not $line) {
        return ""
    }
    return $line.Substring($prefix.Length).Trim()
}

function Get-CrawlerControlEnvironmentValue(
    [string]$SshExecutable,
    [string]$Name
) {
    if ($Name -notmatch '^[A-Z0-9_]+$') {
        throw "Invalid crawler-control environment key name."
    }
    $sshArguments = @(
        "-T", "-n",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "UpdateHostKeys=no",
        "-o", "PreferredAuthentications=publickey",
        "-o", "PasswordAuthentication=no",
        "-o", "KbdInteractiveAuthentication=no",
        "-o", "NumberOfPasswordPrompts=0"
    )
    $sshArguments += @(Get-SshIdentityArguments)
    # The local Ops API credential remains in the cloud deploy account's
    # protected secret store. The gen1db identity is authorized only for the
    # database forward and is never used to read or carry database passwords.
    $command = @"
file="`$HOME/.config/mooncen/deploy-secrets.env"
[ -f "`$file" ] && [ ! -L "`$file" ] && [ -r "`$file" ] || exit 66
owner=`$(stat -c '%U' "`$file" 2>/dev/null) || exit 78
mode=`$(stat -c '%a' "`$file" 2>/dev/null) || exit 78
[ "`$owner" = "`$(id -un)" ] && [ "`$mode" = 600 ] || exit 78
count=`$(grep -Ec '^${Name}=' "`$file" || true)
[ "`$count" -eq 1 ] || exit 65
raw=`$(grep -E '^${Name}=' "`$file" | cut -d= -f2-)
[ -n "`$raw" ] || exit 65
printf '%s' "`$raw" | base64 | tr -d '\r\n'
"@
    $encodedCommand = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($command))
    $transportCommand = "printf '%s' '$encodedCommand' | base64 -d | bash"
    $value = (& $SshExecutable @sshArguments $cloudSshTarget $transportCommand 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $value) {
        return ""
    }
    try {
        $encodedValue = "$($value | Select-Object -First 1)".Trim()
        $decoded = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encodedValue))
        if (-not $decoded -or $decoded.Length -gt 4096 -or $decoded -match '[\x00\r\n]') {
            return ""
        }
        return $decoded
    }
    catch {
        return ""
    }
}

function Get-CloudflareAnalyticsEnvironment([string]$SshExecutable) {
    $sshArguments = @(
        "-T", "-n",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "UpdateHostKeys=no",
        "-o", "PreferredAuthentications=publickey",
        "-o", "PasswordAuthentication=no",
        "-o", "KbdInteractiveAuthentication=no",
        "-o", "NumberOfPasswordPrompts=0"
    )
    $sshArguments += @(Get-SshIdentityArguments)
    # Read the optional pair in one remote process so an update can never mix
    # values from two generations. Distinguish an absent pair from an invalid,
    # duplicate, empty, unreadable, or weakly protected secret store.
    $command = @'
file="$HOME/.config/mooncen/deploy-secrets.env"
[ -f "$file" ] && [ ! -L "$file" ] && [ -r "$file" ] || exit 78
owner=$(stat -c '%U' "$file" 2>/dev/null) || exit 78
mode=$(stat -c '%a' "$file" 2>/dev/null) || exit 78
[ "$owner" = "$(id -un)" ] && [ "$mode" = 600 ] || exit 78
zone_count=$(grep -Ec '^OPS_CLOUDFLARE_ANALYTICS_ZONE_ID=' "$file" || true)
token_count=$(grep -Ec '^OPS_CLOUDFLARE_ANALYTICS_TOKEN=' "$file" || true)
if [ "$zone_count" -eq 0 ] && [ "$token_count" -eq 0 ]; then
  printf 'ABSENT'
  exit 0
fi
[ "$zone_count" -eq 1 ] && [ "$token_count" -eq 1 ] || exit 65
zone=$(grep -E '^OPS_CLOUDFLARE_ANALYTICS_ZONE_ID=' "$file" | cut -d= -f2-)
token=$(grep -E '^OPS_CLOUDFLARE_ANALYTICS_TOKEN=' "$file" | cut -d= -f2-)
[ -n "$zone" ] && [ -n "$token" ] || exit 65
printf 'PAIR:'
printf '%s\n%s' "$zone" "$token" | base64 | tr -d '\r\n'
'@
    $encodedCommand = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($command))
    $transportCommand = "printf '%s' '$encodedCommand' | base64 -d | bash"
    $value = (& $SshExecutable @sshArguments $cloudSshTarget $transportCommand 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $value) {
        throw "Protected Cloudflare analytics configuration is malformed or unreadable."
    }
    $wireValue = "$($value | Select-Object -First 1)".Trim()
    if ($wireValue -eq "ABSENT") {
        return @{ Configured = $false; ZoneId = ""; Token = "" }
    }
    if (-not $wireValue.StartsWith("PAIR:", [StringComparison]::Ordinal)) {
        throw "Protected Cloudflare analytics configuration returned an invalid envelope."
    }
    try {
        $decoded = [Text.Encoding]::UTF8.GetString(
            [Convert]::FromBase64String($wireValue.Substring(5))
        )
    }
    catch {
        throw "Protected Cloudflare analytics configuration returned invalid encoding."
    }
    if (-not $decoded -or $decoded.Length -gt 512 -or $decoded -match '[\x00\r]') {
        throw "Protected Cloudflare analytics configuration returned invalid content."
    }
    $parts = @($decoded -split "`n", -1)
    if ($parts.Count -ne 2 -or -not $parts[0] -or -not $parts[1]) {
        throw "Protected Cloudflare analytics configuration pair is incomplete."
    }
    return @{ Configured = $true; ZoneId = $parts[0]; Token = $parts[1] }
}

function Get-CloudControlEnvironment([string]$SshExecutable) {
    $remoteNames = @(
        "DB_NAME",
        "DB_API_PASSWORD",
        "AUTH_SECRET"
    )
    $remoteValues = @{}
    foreach ($name in $remoteNames) {
        $value = Get-RemoteEnvironmentValue $SshExecutable $name
        if (-not $value) {
            throw "Cloud control plane credential is unavailable: $name"
        }
        $remoteValues[$name] = $value
    }
    $remoteValues["DB_API_USER"] = Get-RemoteEnvironmentValue $SshExecutable "DB_API_USER"
    if (-not $remoteValues["DB_API_USER"]) {
        $remoteValues["DB_API_USER"] = "mooncen_api_login"
    }
    $remoteValues["MOONCEN_OPS_LOGIN_ID"] = Get-RemoteEnvironmentValue $SshExecutable "MOONCEN_OPS_LOGIN_ID"
    if (-not $remoteValues["MOONCEN_OPS_LOGIN_ID"]) {
        $remoteValues["MOONCEN_OPS_LOGIN_ID"] = "opsadmin"
    }
    $remoteValues["MOONCEN_OPS_PASSWORD_HASH"] = Get-RemoteEnvironmentValue $SshExecutable "MOONCEN_OPS_PASSWORD_HASH"
    if (-not $remoteValues["MOONCEN_OPS_PASSWORD_HASH"]) {
        $remoteValues["MOONCEN_OPS_PASSWORD_HASH"] = Get-LocalEnvironmentValue "MOONCEN_OPS_PASSWORD_HASH"
    }
    if (-not $remoteValues["MOONCEN_OPS_PASSWORD_HASH"]) {
        throw "Cloud control plane credential is unavailable: MOONCEN_OPS_PASSWORD_HASH"
    }
    $cloudflareAnalytics = Get-CloudflareAnalyticsEnvironment $SshExecutable
    $remoteValues["OPS_CLOUDFLARE_ANALYTICS_ZONE_ID"] = $cloudflareAnalytics.ZoneId
    $remoteValues["OPS_CLOUDFLARE_ANALYTICS_TOKEN"] = $cloudflareAnalytics.Token
    $cloudflareAnalyticsConfigured = [bool]$cloudflareAnalytics.Configured
    if ($cloudflareAnalyticsConfigured) {
        if ($remoteValues["OPS_CLOUDFLARE_ANALYTICS_ZONE_ID"] -notmatch '^[0-9a-f]{32}$') {
            throw "Cloudflare analytics zone id is unavailable or invalid."
        }
        $analyticsToken = $remoteValues["OPS_CLOUDFLARE_ANALYTICS_TOKEN"]
        if (-not $analyticsToken -or $analyticsToken.Length -lt 20 -or $analyticsToken.Length -gt 256 -or $analyticsToken -notmatch '^[A-Za-z0-9_-]+$') {
            throw "Cloudflare analytics token is unavailable or invalid."
        }
    }
    $apiEnvironment = @{
            ENVIRONMENT = "production"
            DB_HOST = $cloudDbTunnelAddress
            DB_PORT = "$cloudDbTunnelPort"
            DB_NAME = $remoteValues["DB_NAME"]
            DB_SSLMODE = "require"
            DB_USER = "mooncen_admin"
            DB_PASSWORD = ""
            DB_OWNER_USER = "mooncen_admin"
            DB_API_USER = $remoteValues["DB_API_USER"]
            DB_API_PASSWORD = $remoteValues["DB_API_PASSWORD"]
            DB_CRAWLER_USER = ""
            DB_CRAWLER_PASSWORD = ""
            AUTH_SECRET = $remoteValues["AUTH_SECRET"]
            MOONCEN_OPS_LOGIN_ID = $remoteValues["MOONCEN_OPS_LOGIN_ID"]
            MOONCEN_OPS_PASSWORD_HASH = $remoteValues["MOONCEN_OPS_PASSWORD_HASH"]
            MOONCEN_OPS_SINGLE_ACCOUNT_ONLY = "true"
            OPS_LOCAL_CRAWLER_RUNTIME_ENABLED = "false"
            # Cloud analytics credentials are API-only. Explicit empty values
            # also stop a parent Process environment or repository .env from
            # being inherited when the protected pair is not configured.
            OPS_CLOUDFLARE_ANALYTICS_ZONE_ID = ""
            OPS_CLOUDFLARE_ANALYTICS_TOKEN = ""
    }
    if ($cloudflareAnalyticsConfigured) {
        $apiEnvironment["OPS_CLOUDFLARE_ANALYTICS_ZONE_ID"] = $remoteValues["OPS_CLOUDFLARE_ANALYTICS_ZONE_ID"]
        $apiEnvironment["OPS_CLOUDFLARE_ANALYTICS_TOKEN"] = $remoteValues["OPS_CLOUDFLARE_ANALYTICS_TOKEN"]
    }
    if ($crawlerControlEnvironmentEnabled) {
        $crawlerValues = @{}
        foreach ($name in @(
            "OPS_CRAWLER_SHARED_DB_NAME",
            "OPS_CRAWLER_API_DB_USER",
            "OPS_CRAWLER_API_DB_PASSWORD"
        )) {
            $value = Get-CrawlerControlEnvironmentValue $SshExecutable $name
            if (-not $value) {
                throw "Crawler-control API credential is unavailable: $name"
            }
            $crawlerValues[$name] = $value
        }
        if ($crawlerValues["OPS_CRAWLER_SHARED_DB_NAME"] -ne "mooncen_staging") {
            throw "Crawler-control API must target the reviewed mooncen_staging database."
        }
        if ($crawlerValues["OPS_CRAWLER_API_DB_USER"] -notmatch '^[a-z_][a-z0-9_]{0,62}$') {
            throw "Crawler-control API database login name is invalid."
        }
        if ($crawlerValues["OPS_CRAWLER_API_DB_USER"] -eq $remoteValues["DB_API_USER"]) {
            throw "Crawler-control API and production API roles must be separated."
        }
        $apiEnvironment["OPS_CRAWLER_SHARED_DB_HOST"] = $crawlerControlDbTunnelAddress
        $apiEnvironment["OPS_CRAWLER_SHARED_DB_PORT"] = "$crawlerControlDbTunnelPort"
        $apiEnvironment["OPS_CRAWLER_SHARED_DB_NAME"] = $crawlerValues["OPS_CRAWLER_SHARED_DB_NAME"]
        $apiEnvironment["OPS_CRAWLER_API_DB_USER"] = $crawlerValues["OPS_CRAWLER_API_DB_USER"]
        $apiEnvironment["OPS_CRAWLER_API_DB_PASSWORD"] = $crawlerValues["OPS_CRAWLER_API_DB_PASSWORD"]
        $apiEnvironment["OPS_CRAWLER_API_DB_REQUIRED"] = "true"
        $apiEnvironment["OPS_CRAWLER_API_DB_POOL_SIZE"] = "2"
        $apiEnvironment["OPS_CRAWLER_API_DB_POOL_TIMEOUT"] = "5"
        $apiEnvironment["OPS_CRAWLER_API_DB_POOL_RECYCLE"] = "1800"
    }
    return @{
        Api = $apiEnvironment
    }
}

function Set-ProcessEnvironment([hashtable]$Values) {
    $previous = @{}
    foreach ($name in $Values.Keys) {
        $previous[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
        [Environment]::SetEnvironmentVariable($name, [string]$Values[$name], "Process")
    }
    return $previous
}

function Restore-ProcessEnvironment([hashtable]$Previous) {
    if ($null -eq $Previous) {
        return
    }
    foreach ($name in $Previous.Keys) {
        [Environment]::SetEnvironmentVariable($name, $Previous[$name], "Process")
    }
}

function Rotate-BoundedLog(
    [string]$Path,
    [long]$MaximumBytes = 5MB,
    [int]$RetainedFiles = 3
) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return
    }
    $file = Get-Item -LiteralPath $Path -ErrorAction Stop
    if ($file.Length -lt $MaximumBytes) {
        return
    }
    for ($index = $RetainedFiles; $index -ge 1; $index--) {
        $destination = "$Path.$index"
        if (Test-Path -LiteralPath $destination -PathType Leaf) {
            [IO.File]::Delete($destination)
        }
        $source = if ($index -eq 1) { $Path } else { "$Path.$($index - 1)" }
        if (Test-Path -LiteralPath $source -PathType Leaf) {
            [IO.File]::Move($source, $destination)
        }
    }
}

function Prepare-ApiLogs {
    Rotate-BoundedLog $apiStandardOutputLog
    Rotate-BoundedLog $apiStandardErrorLog
}

function Start-ProcessWithEnvironment(
    [string]$FilePath,
    [string[]]$Arguments,
    [string]$WorkingDirectory,
    [hashtable]$Environment,
    [string]$StandardOutputPath = "",
    [string]$StandardErrorPath = ""
) {
    $previous = Set-ProcessEnvironment $Environment
    try {
        $startParameters = @{
            FilePath = $FilePath
            ArgumentList = $Arguments
            WorkingDirectory = $WorkingDirectory
            WindowStyle = "Hidden"
            PassThru = $true
        }
        if ($StandardOutputPath) {
            $startParameters["RedirectStandardOutput"] = $StandardOutputPath
        }
        if ($StandardErrorPath) {
            $startParameters["RedirectStandardError"] = $StandardErrorPath
        }
        return Start-Process @startParameters
    }
    finally {
        Restore-ProcessEnvironment $previous
    }
}

function Resolve-GitExecutable {
    $command = Get-Command git.exe -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }
    $wingetPackages = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
    $candidates = @(
        Get-ChildItem -LiteralPath $wingetPackages -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like "Git.Git_*" -or $_.Name -like "Git.MinGit_*" } |
            ForEach-Object {
                Get-ChildItem -LiteralPath $_.FullName -Filter git.exe -File -Recurse -ErrorAction SilentlyContinue
            } |
            Sort-Object `
                @{ Expression = { if ($_.FullName -match '[\\/]cmd[\\/]git\.exe$') { 0 } else { 1 } } }, `
                @{ Expression = { $_.LastWriteTimeUtc }; Descending = $true }
    )
    if ($candidates.Count -gt 0) {
        return $candidates[0].FullName
    }
    return ""
}

function Add-ExecutableDirectoryToPath([string]$Executable) {
    if (-not $Executable) {
        return
    }
    $directory = Split-Path -Parent $Executable
    $entries = @($env:PATH -split ';' | Where-Object { $_ })
    if ($entries -notcontains $directory) {
        $env:PATH = "$directory;$env:PATH"
    }
}

function Start-OpsConsole {
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "Python environment is missing: $python"
    }
    if (-not (Test-Path -LiteralPath $vite -PathType Leaf)) {
        throw "Ops Console dependencies are missing. Run npm ci in $opsDir."
    }
    $requiredPorts = if ($DataSource -eq "Cloud") {
        $ports = @(5175, 8001, $cloudTunnelPort, $cloudDbTunnelPort)
        if ($crawlerControlEnvironmentEnabled) {
            $ports += $crawlerControlDbTunnelPort
        }
        $ports
    }
    else {
        @(5175, 8001)
    }
    foreach ($port in $requiredPorts) {
        if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
            throw "Port $port is already in use. Run this launcher with -Action Stop or stop the owning process."
        }
    }
    $node = Resolve-NodeExecutable
    $ssh = ""
    $cloudControlEnvironment = $null
    Add-ExecutableDirectoryToPath (Resolve-GitExecutable)
    $env:VITE_OPS_API_PROXY_TARGET = "http://127.0.0.1:8001"
    if ($DataSource -eq "Cloud") {
        $ssh = Resolve-SshExecutable
        $cloudControlEnvironment = Get-CloudControlEnvironment $ssh
    }
    else {
        if ($EnableLocalCrawlerRuntime) {
            $env:OPS_LOCAL_CRAWLER_RUNTIME_ENABLED = "true"
        }
        else {
            $env:OPS_LOCAL_CRAWLER_RUNTIME_ENABLED = "false"
        }
    }
    New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
    $started = @()
    try {
        if ($DataSource -eq "Cloud") {
            $tunnelArguments = @(
                "-N",
                "-T",
                "-o", "BatchMode=yes",
                "-o", "ExitOnForwardFailure=yes",
                "-o", "StrictHostKeyChecking=yes",
                "-o", "ConnectTimeout=10",
                "-o", "ServerAliveInterval=15",
                "-o", "ServerAliveCountMax=3",
                "-L", $cloudTunnelForward,
                "-L", $cloudDbTunnelForward,
                $cloudSshTarget
            )
            if ($SshIdentityFile) {
                $target = $tunnelArguments[-1]
                $tunnelArguments = @($tunnelArguments[0..($tunnelArguments.Count - 2)]) +
                    @(Get-SshIdentityArguments) + @($target)
            }
            $tunnelArgumentLine = @(
                $tunnelArguments | ForEach-Object { ConvertTo-NativeArgument -Value ([string]$_) }
            ) -join " "
            $tunnel = Start-Process -FilePath $ssh -ArgumentList $tunnelArgumentLine `
                -WorkingDirectory $root -WindowStyle Hidden -PassThru
            $tunnelEntryIndex = $started.Count
            $started += New-ManagedProcessEntry "ssh-tunnel" $tunnel
            if (-not (Wait-Http "http://${cloudTunnelAddress}:${cloudTunnelPort}/health" 30 $tunnel)) {
                throw "Cloud API SSH tunnel did not become ready. Verify DNS, host key, and SSH key access for $cloudSshTarget."
            }
            $started[$tunnelEntryIndex] = New-ListenerManagedProcessEntry `
                "ssh-tunnel" $tunnel @($cloudTunnelPort, $cloudDbTunnelPort)

            if ($crawlerControlEnvironmentEnabled) {
                $crawlerTunnelArguments = @(
                    "-N",
                    "-T",
                    "-o", "BatchMode=yes",
                    "-o", "ExitOnForwardFailure=yes",
                    "-o", "StrictHostKeyChecking=yes",
                    "-o", "ConnectTimeout=10",
                    "-o", "ServerAliveInterval=15",
                    "-o", "ServerAliveCountMax=3",
                    "-L", $crawlerControlDbTunnelForward
                ) + @(Get-CrawlerControlSshIdentityArguments) + @($CrawlerControlSshTarget)
                $crawlerTunnelArgumentLine = @(
                    $crawlerTunnelArguments | ForEach-Object {
                        ConvertTo-NativeArgument -Value ([string]$_)
                    }
                ) -join " "
                $crawlerTunnel = Start-Process `
                    -FilePath $ssh `
                    -ArgumentList $crawlerTunnelArgumentLine `
                    -WorkingDirectory $root `
                    -WindowStyle Hidden `
                    -PassThru
                $crawlerTunnelEntryIndex = $started.Count
                $started += New-ManagedProcessEntry "crawler-control-ssh-tunnel" $crawlerTunnel
                if (-not (Wait-Listener $crawlerControlDbTunnelPort 30 $crawlerTunnel)) {
                    throw "Crawler-control database SSH tunnel did not become ready. Verify the dedicated gen1db SSH identity and host authorization."
                }
                $started[$crawlerTunnelEntryIndex] = New-ListenerManagedProcessEntry `
                    "crawler-control-ssh-tunnel" `
                    $crawlerTunnel `
                    @($crawlerControlDbTunnelPort)
            }
        }

        if ($DataSource -eq "Local") {
            Invoke-CheckedNative $python @((Join-Path $root "tools\ensure_ops_console_schema.py")) `
                "Ops schema preparation failed."
        }
        if ($DataSource -eq "Local" -and $EnableLocalCrawlerRuntime) {
            Invoke-CheckedNative $python @("-m", "ops_agent.crawler_scheduler", "--check") `
                "Local crawler scheduler configuration is invalid."
        }

        $apiArguments = @("-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8001")
        Prepare-ApiLogs
        $apiEnvironment = if ($DataSource -eq "Cloud") { $cloudControlEnvironment.Api } else { @{} }
        $api = Start-ProcessWithEnvironment `
            $python $apiArguments $root $apiEnvironment `
            $apiStandardOutputLog $apiStandardErrorLog
        $apiEntryIndex = $started.Count
        $started += New-ManagedProcessEntry "api" $api
        if (-not (Wait-Http "http://127.0.0.1:8001/health" 30 $api)) {
            throw "MoonCen API did not become ready."
        }
        $started[$apiEntryIndex] = New-ListenerManagedProcessEntry "api" $api @($cloudApiPort)

        if ($DataSource -eq "Local") {
            Invoke-CheckedNative $python @("-m", "ops_agent.status_agent", "--once") `
                "Initial Ops status snapshot failed." -Quiet
        }

        $workers = @()
        if ($DataSource -eq "Local") {
            $workers += @(
                @{ name = "status-agent"; module = "ops_agent.status_agent"; extra = @("--interval", "30") }
            )
        }
        if ($DataSource -eq "Local" -and $EnableLocalCrawlerRuntime) {
            $workers += @(
                @{ name = "crawler-scheduler"; module = "ops_agent.crawler_scheduler"; extra = @() },
                @{ name = "crawler-worker"; module = "ops_agent.crawler_worker"; extra = @() },
                @{ name = "quality-worker"; module = "ops_agent.quality_worker"; extra = @() }
            )
        }
        foreach ($worker in $workers) {
            $arguments = @("-m", $worker.module) + @($worker.extra)
            $process = Start-ProcessWithEnvironment `
                $python $arguments $root $nonApiAnalyticsEnvironment
            $started += New-ManagedProcessEntry $worker.name $process
        }

        $console = Start-ProcessWithEnvironment `
            $node `
            @($vite, "--host", "127.0.0.1", "--port", "5175") `
            $opsDir `
            $nonApiAnalyticsEnvironment
        $consoleEntryIndex = $started.Count
        $started += New-ManagedProcessEntry "console" $console

        if (-not (Wait-Http "http://127.0.0.1:5175/" 30 $console)) {
            throw "Standalone Ops Console did not become ready."
        }
        $started[$consoleEntryIndex] = New-ListenerManagedProcessEntry "console" $console @(5175)

        [pscustomobject]@{
            started_at = (Get-Date).ToUniversalTime().ToString("o")
            workspace = $root
            data_source = $DataSource
            api_proxy_target = $env:VITE_OPS_API_PROXY_TARGET
            api_stdout_log = $apiStandardOutputLog
            api_stderr_log = $apiStandardErrorLog
            ssh_target = if ($DataSource -eq "Cloud") { $cloudSshTarget } else { $null }
            crawler_control_analytics_enabled = [bool]$crawlerControlEnvironmentEnabled
            crawler_control_ssh_target = if ($crawlerControlEnvironmentEnabled) {
                $CrawlerControlSshTarget
            }
            else {
                $null
            }
            local_crawler_runtime_enabled = [bool]$EnableLocalCrawlerRuntime
            processes = $started
        } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $statePath -Encoding UTF8
    }
    catch {
        foreach ($entry in $started) {
            Stop-ManagedProcessTree $entry
        }
        if (Test-Path -LiteralPath $statePath -PathType Leaf) {
            [IO.File]::Delete($statePath)
        }
        throw
    }
    Write-Host "Ops Console: http://127.0.0.1:5175/"
    if ($DataSource -eq "Cloud") {
        Write-Host "Data source: production cloud database via SSH tunnel on 127.0.0.1:$cloudDbTunnelPort"
        Write-Host "Control API: http://127.0.0.1:8001/health"
        Write-Host "Deployment worker: enabled for the local reviewed worktree"
        if ($crawlerControlEnvironmentEnabled) {
            Write-Host "Crawler analytics: gen1db crawler-control pool via dedicated SSH tunnel on 127.0.0.1:$crawlerControlDbTunnelPort"
        }
        else {
            Write-Host "Crawler analytics: not configured (set a dedicated crawler-control SSH identity to opt in)"
        }
    }
    else {
        Write-Host "API:         http://127.0.0.1:8001/health"
    }
    if ($DataSource -eq "Local" -and -not $EnableLocalCrawlerRuntime) {
        Write-Host "Local crawler/quality runtime: disabled (use -EnableLocalCrawlerRuntime for isolated development only)"
    }
}

function Refresh-OpsControl {
    if ($DataSource -ne "Local") {
        throw "RefreshControl is available only with -DataSource Local. Cloud mode has no local control plane."
    }
    $state = Get-State
    if ($null -eq $state) {
        throw "Ops Console process state is unavailable. Run a full Start instead."
    }
    if ($state.data_source -and [string]$state.data_source -ne "Local") {
        throw "The recorded Ops Console is using Cloud data. Run Restart instead of refreshing a local control plane."
    }
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "Python environment is missing: $python"
    }
    $localCrawlerRuntimeEnabled = [bool]$state.local_crawler_runtime_enabled
    $env:OPS_LOCAL_CRAWLER_RUNTIME_ENABLED = if ($localCrawlerRuntimeEnabled) { "true" } else { "false" }
    Add-ExecutableDirectoryToPath (Resolve-GitExecutable)

    $controlNames = @("api", "status-agent")
    foreach ($entry in @($state.processes | Where-Object { $_.name -in $controlNames })) {
        Stop-ManagedProcessTree $entry
    }
    Start-Sleep -Milliseconds 500
    if (Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue) {
        throw "Port 8001 is still in use after stopping the recorded Ops API."
    }

    $started = @()
    try {
        Prepare-ApiLogs
        $api = Start-ProcessWithEnvironment `
            $python `
            @("-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8001") `
            $root `
            @{} `
            $apiStandardOutputLog `
            $apiStandardErrorLog
        $apiEntryIndex = $started.Count
        $started += New-ManagedProcessEntry "api" $api
        if (-not (Wait-Http "http://127.0.0.1:8001/health" 30 $api)) {
            throw "Reloaded Ops API did not become ready."
        }
        $started[$apiEntryIndex] = New-ListenerManagedProcessEntry "api" $api @($cloudApiPort)

        Invoke-CheckedNative $python @("-m", "ops_agent.status_agent", "--once") `
            "Initial refreshed Ops status snapshot failed." -Quiet
        $statusAgent = Start-Process -FilePath $python `
            -ArgumentList @("-m", "ops_agent.status_agent", "--interval", "30") `
            -WorkingDirectory $root -WindowStyle Hidden -PassThru
        $started += New-ManagedProcessEntry "status-agent" $statusAgent

        $preserved = @($state.processes | Where-Object { $_.name -notin $controlNames })
        $state.started_at = (Get-Date).ToUniversalTime().ToString("o")
        $state | Add-Member -NotePropertyName "data_source" -NotePropertyValue "Local" -Force
        $state | Add-Member -NotePropertyName "api_proxy_target" -NotePropertyValue "http://127.0.0.1:8001" -Force
        $state | Add-Member -NotePropertyName "api_stdout_log" -NotePropertyValue $apiStandardOutputLog -Force
        $state | Add-Member -NotePropertyName "api_stderr_log" -NotePropertyValue $apiStandardErrorLog -Force
        $state | Add-Member -NotePropertyName "local_crawler_runtime_enabled" -NotePropertyValue $localCrawlerRuntimeEnabled -Force
        $state.processes = @($preserved + $started)
        $state | ConvertTo-Json -Depth 4 |
            Set-Content -LiteralPath $statePath -Encoding UTF8
    }
    catch {
        foreach ($entry in $started) {
            Stop-ManagedProcessTree $entry
        }
        throw
    }

    Write-Host "Ops control plane refreshed without stopping crawler workers."
    Write-Host "Ops Console: http://127.0.0.1:5175/"
    Write-Host "API:         http://127.0.0.1:8001/health"
}

switch ($Action) {
    "Stop" { Stop-OpsConsole }
    "Restart" {
        Stop-OpsConsole
        Start-Sleep -Milliseconds 500
        Start-OpsConsole
    }
    "RefreshControl" { Refresh-OpsControl }
    "Status" { Show-Status | Format-Table -AutoSize }
    default { Start-OpsConsole }
}
