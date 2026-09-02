[CmdletBinding()]
param(
    [ValidateSet("Install", "Uninstall", "Status", "Start", "Stop")]
    [string]$Action = "Status",
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')]
    [string]$TaskName = "MoonCen-DevelopmentServices",
    [string]$RunAsUser = "",
    [ValidateSet("Cloud", "Local")]
    [string]$DataSource = "Cloud",
    [string]$SshIdentityFile = "",
    [ValidatePattern('^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+$')]
    [string]$CrawlerControlSshTarget = "sgm@gen1db",
    [string]$CrawlerControlSshIdentityFile = "",
    [switch]$StartNow,
    [switch]$ElevationAttempted
)

$ErrorActionPreference = "Stop"
$root = [IO.Path]::GetFullPath($PSScriptRoot)
$supervisor = Join-Path $root "start_development_autostart.ps1"
$powerShellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$taskPath = "\"
$taskMarker = "MoonCen development services autostart v1; workspace=$root"
if (-not $RunAsUser) {
    $RunAsUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
}

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-ElevatedSelf {
    $payload = [ordered]@{
        SchemaVersion = 1
        ScriptPath = [IO.Path]::GetFullPath($PSCommandPath)
        Action = $Action
        TaskName = $TaskName
        RunAsUser = $RunAsUser
        DataSource = $DataSource
        SshIdentityFile = if ($SshIdentityFile) { [IO.Path]::GetFullPath($SshIdentityFile) } else { "" }
        CrawlerControlSshTarget = $CrawlerControlSshTarget
        CrawlerControlSshIdentityFile = if ($CrawlerControlSshIdentityFile) {
            [IO.Path]::GetFullPath($CrawlerControlSshIdentityFile)
        }
        else {
            ""
        }
        StartNow = [bool]$StartNow
    }
    $payloadJson = $payload | ConvertTo-Json -Compress
    $payloadBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($payloadJson))
    $bootstrap = @"
`$ErrorActionPreference = 'Stop'
`$payloadJson = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('$payloadBase64'))
`$payload = `$payloadJson | ConvertFrom-Json
if ([int]`$payload.SchemaVersion -ne 1) { throw 'Unsupported elevation payload.' }
`$childArguments = @(
    '-Action', [string]`$payload.Action,
    '-TaskName', [string]`$payload.TaskName,
    '-RunAsUser', [string]`$payload.RunAsUser,
    '-DataSource', [string]`$payload.DataSource,
    '-ElevationAttempted'
)
if ([string]`$payload.SshIdentityFile) {
    `$childArguments += @('-SshIdentityFile', [string]`$payload.SshIdentityFile)
}
if ([string]`$payload.CrawlerControlSshIdentityFile) {
    `$childArguments += @(
        '-CrawlerControlSshTarget', [string]`$payload.CrawlerControlSshTarget,
        '-CrawlerControlSshIdentityFile', [string]`$payload.CrawlerControlSshIdentityFile
    )
}
if ([bool]`$payload.StartNow) { `$childArguments += '-StartNow' }
& ([string]`$payload.ScriptPath) @childArguments
exit `$LASTEXITCODE
"@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($bootstrap))
    try {
        Write-Host "Administrator approval is required. Accept the Windows UAC prompt."
        $process = Start-Process `
            -FilePath $powerShellExe `
            -Verb RunAs `
            -ArgumentList "-NoProfile -ExecutionPolicy Bypass -EncodedCommand $encoded" `
            -WorkingDirectory $root `
            -WindowStyle Normal `
            -Wait `
            -PassThru
        return $process.ExitCode
    }
    catch {
        $nativeError = if ($null -ne $_.Exception.PSObject.Properties["NativeErrorCode"]) {
            [int]$_.Exception.NativeErrorCode
        }
        else {
            0
        }
        if ($nativeError -eq 1223 -or $_.Exception.Message -match '(?i)canceled|cancelled') {
            throw "Administrator approval was canceled; the scheduled task was not changed."
        }
        throw "Unable to open the elevated installer: $($_.Exception.Message)"
    }
}

function Resolve-AccountSid([string]$Account) {
    try {
        if ($Account -match '^S-\d(?:-\d+)+$') {
            return (New-Object Security.Principal.SecurityIdentifier($Account)).Value
        }
        return (New-Object Security.Principal.NTAccount($Account)).Translate(
            [Security.Principal.SecurityIdentifier]
        ).Value
    }
    catch {
        throw "Windows account could not be resolved: $Account"
    }
}

function Quote-TaskArgument([string]$Value) {
    if ($Value.Contains('"')) {
        throw "Scheduled task argument contains an unsupported quote character."
    }
    return '"' + $Value + '"'
}

function Get-SupervisorArguments([string]$SupervisorAction) {
    $arguments = @(
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy", "Bypass",
        "-File", (Quote-TaskArgument $supervisor),
        "-Action", $SupervisorAction,
        "-DataSource", $DataSource
    )
    if ($DataSource -eq "Cloud" -and $SshIdentityFile) {
        $identityPath = [IO.Path]::GetFullPath($SshIdentityFile)
        $arguments += @("-SshIdentityFile", (Quote-TaskArgument $identityPath))
    }
    if ($DataSource -eq "Cloud" -and $CrawlerControlSshIdentityFile) {
        $crawlerIdentityPath = [IO.Path]::GetFullPath($CrawlerControlSshIdentityFile)
        $arguments += @(
            "-CrawlerControlSshTarget", $CrawlerControlSshTarget,
            "-CrawlerControlSshIdentityFile", (Quote-TaskArgument $crawlerIdentityPath)
        )
    }
    return $arguments -join " "
}

function Get-ExactTask {
    $matches = @(Get-ScheduledTask -TaskName $TaskName -TaskPath $taskPath -ErrorAction SilentlyContinue |
        Where-Object { $_.TaskName -ceq $TaskName -and $_.TaskPath -ceq $taskPath })
    if ($matches.Count -gt 1) {
        throw "Multiple scheduled tasks unexpectedly matched the exact MoonCen task name."
    }
    if ($matches.Count -eq 0) {
        return $null
    }
    return $matches[0]
}

function Assert-RecognizedTask([object]$Task) {
    if ($null -eq $Task) {
        return
    }
    if ([string]$Task.Description -cne $taskMarker) {
        throw "Scheduled task '$TaskName' exists but is not owned by this MoonCen workspace."
    }
    $actions = @($Task.Actions)
    if (
        $actions.Count -ne 1 -or
        [IO.Path]::GetFullPath([string]$actions[0].Execute) -ne [IO.Path]::GetFullPath($powerShellExe) -or
        ([string]$actions[0].Arguments).IndexOf($supervisor, [StringComparison]::OrdinalIgnoreCase) -lt 0
    ) {
        throw "Scheduled task '$TaskName' has an unrecognized action and will not be changed."
    }
}

function Invoke-Supervisor([string]$SupervisorAction) {
    $effectiveCrawlerTarget = $CrawlerControlSshTarget
    $effectiveCrawlerIdentity = $CrawlerControlSshIdentityFile
    if (
        $DataSource -eq "Cloud" -and
        -not $effectiveCrawlerIdentity -and
        $SupervisorAction -in @("Status", "Stop")
    ) {
        $registeredTask = Get-ExactTask
        if ($null -ne $registeredTask) {
            $registeredArguments = [string](@($registeredTask.Actions)[0].Arguments)
            if ($registeredArguments -match '-CrawlerControlSshIdentityFile\s+"([^"]+)"') {
                $effectiveCrawlerIdentity = [IO.Path]::GetFullPath($matches[1])
                if (
                    $registeredArguments -match
                        '-CrawlerControlSshTarget\s+([A-Za-z0-9._-]+@[A-Za-z0-9.-]+)'
                ) {
                    $effectiveCrawlerTarget = $matches[1]
                }
            }
        }
    }
    $arguments = @(
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy", "Bypass",
        "-File", $supervisor,
        "-Action", $SupervisorAction,
        "-DataSource", $DataSource
    )
    if ($DataSource -eq "Cloud" -and $SshIdentityFile) {
        $arguments += @("-SshIdentityFile", [IO.Path]::GetFullPath($SshIdentityFile))
    }
    if ($DataSource -eq "Cloud" -and $effectiveCrawlerIdentity) {
        $arguments += @(
            "-CrawlerControlSshTarget", $effectiveCrawlerTarget,
            "-CrawlerControlSshIdentityFile", [IO.Path]::GetFullPath($effectiveCrawlerIdentity)
        )
    }
    $previousErrorAction = $ErrorActionPreference
    $exitCode = 1
    try {
        $ErrorActionPreference = "Continue"
        & $powerShellExe @arguments 2>&1 | Out-Host
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorAction
    }
    return $exitCode
}

function Assert-TaskDefinition([object]$Task) {
    Assert-RecognizedTask $Task
    $actions = @($Task.Actions)
    $actionArguments = [string]$actions[0].Arguments
    if ([IO.Path]::GetFullPath([string]$actions[0].WorkingDirectory) -ne $root) {
        throw "The registered task has an unexpected working directory."
    }
    if (
        $actionArguments.IndexOf("-Action Monitor", [StringComparison]::Ordinal) -lt 0 -or
        $actionArguments.IndexOf("-DataSource $DataSource", [StringComparison]::Ordinal) -lt 0 -or
        $actionArguments.IndexOf("EnableLocalCrawlerRuntime", [StringComparison]::OrdinalIgnoreCase) -ge 0
    ) {
        throw "The registered task has an unexpected supervisor mode."
    }
    if ($DataSource -eq "Cloud") {
        if ($actionArguments -notmatch '-SshIdentityFile\s+"([^"]+)"') {
            throw "The registered Cloud task has no explicit SSH identity file."
        }
        $registeredIdentity = [IO.Path]::GetFullPath($matches[1])
        if ($SshIdentityFile -and $registeredIdentity -ne [IO.Path]::GetFullPath($SshIdentityFile)) {
            throw "The registered task uses a different SSH identity file."
        }
        $hasCrawlerIdentity = $actionArguments -match '-CrawlerControlSshIdentityFile\s+"([^"]+)"'
        if ($CrawlerControlSshIdentityFile -and -not $hasCrawlerIdentity) {
            throw "The registered task crawler-control analytics mode does not match this invocation."
        }
        if ($hasCrawlerIdentity -and $CrawlerControlSshIdentityFile) {
            $registeredCrawlerIdentity = [IO.Path]::GetFullPath($matches[1])
            if (
                $registeredCrawlerIdentity -ne [IO.Path]::GetFullPath($CrawlerControlSshIdentityFile)
            ) {
                throw "The registered task uses a different crawler-control SSH identity file."
            }
            if (
                $actionArguments.IndexOf(
                    "-CrawlerControlSshTarget $CrawlerControlSshTarget",
                    [StringComparison]::Ordinal
                ) -lt 0
            ) {
                throw "The registered task uses a different crawler-control SSH target."
            }
        }
    }
    $triggers = @($Task.Triggers)
    if ($triggers.Count -ne 1 -or $triggers[0].CimClass.CimClassName -ne "MSFT_TaskBootTrigger") {
        throw "The registered task does not have exactly one boot trigger."
    }
    if ([string]$triggers[0].Delay -ne "PT1M") {
        throw "The registered boot trigger does not have the required 60-second delay."
    }
    if ([string]$Task.Principal.LogonType -ne "Password") {
        throw "The registered task is not using Password logon for pre-login network access."
    }
    if ([string]$Task.Principal.RunLevel -ne "Limited") {
        throw "The registered task is not running with least privilege."
    }
    if ((Resolve-AccountSid ([string]$Task.Principal.UserId)) -ne (Resolve-AccountSid $RunAsUser)) {
        throw "The registered task uses an unexpected Windows account."
    }
    if ([string]$Task.Settings.MultipleInstances -ne "IgnoreNew") {
        throw "The registered task does not reject duplicate supervisor instances."
    }
    if (
        -not [bool]$Task.Settings.Enabled -or
        -not [bool]$Task.Settings.StartWhenAvailable -or
        [bool]$Task.Settings.DisallowStartIfOnBatteries -or
        [bool]$Task.Settings.StopIfGoingOnBatteries -or
        [string]$Task.Settings.ExecutionTimeLimit -ne "PT0S" -or
        [int]$Task.Settings.RestartCount -ne 10 -or
        [string]$Task.Settings.RestartInterval -ne "PT1M"
    ) {
        throw "The registered task recovery settings do not match the MoonCen contract."
    }
}

function Wait-TaskStopped {
    $deadline = (Get-Date).AddSeconds(30)
    do {
        $task = Get-ExactTask
        if ($null -eq $task -or [string]$task.State -ne "Running") {
            return
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    throw "Scheduled task did not stop within 30 seconds."
}

if (-not (Test-Path -LiteralPath $supervisor -PathType Leaf)) {
    throw "Development supervisor is missing: $supervisor"
}
if (-not (Test-Path -LiteralPath $powerShellExe -PathType Leaf)) {
    throw "Windows PowerShell is missing: $powerShellExe"
}
if ($DataSource -ne "Cloud" -and ($SshIdentityFile -or $CrawlerControlSshIdentityFile)) {
    throw "SSH identity files are allowed only with -DataSource Cloud."
}
if (
    $CrawlerControlSshIdentityFile -and
    $SshIdentityFile -and
    [IO.Path]::GetFullPath($CrawlerControlSshIdentityFile) -eq
        [IO.Path]::GetFullPath($SshIdentityFile)
) {
    throw "Crawler-control SSH must use a separate identity file from the production cloud tunnel."
}
if ($Action -eq "Install" -and $DataSource -eq "Cloud" -and -not $SshIdentityFile) {
    throw "Cloud autostart requires -SshIdentityFile with a restricted, non-interactive SSH key."
}
if ($Action -in @("Install", "Uninstall") -and -not (Test-IsAdministrator)) {
    if ($ElevationAttempted) {
        throw "Windows did not grant administrator rights after the UAC elevation attempt."
    }
    $elevatedExitCode = Invoke-ElevatedSelf
    exit $elevatedExitCode
}

switch ($Action) {
    "Install" {
        if (-not (Test-IsAdministrator)) {
            throw "The elevated installer did not receive administrator rights."
        }
        $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        $runAsSid = Resolve-AccountSid $RunAsUser
        if ($runAsSid -ne $currentSid) {
            throw "Install must run elevated as the same Windows account that will own the task so SSH preflight uses the correct profile."
        }
        if ($DataSource -eq "Cloud" -and -not $SshIdentityFile) {
            throw "Cloud autostart requires -SshIdentityFile with a restricted, non-interactive SSH key."
        }
        $preflightExitCode = Invoke-Supervisor "Preflight"
        if ($preflightExitCode -ne 0) {
            throw "Autostart preflight failed; the scheduled task was not registered."
        }

        $existing = Get-ExactTask
        Assert-RecognizedTask $existing
        $requestedTaskArguments = Get-SupervisorArguments "Monitor"
        if (
            $null -ne $existing -and
            [string]$existing.State -eq "Running" -and
            $StartNow -and
            [string](@($existing.Actions)[0].Arguments) -cne $requestedTaskArguments
        ) {
            throw (
                "The running startup task uses different arguments, so -StartNow cannot activate " +
                "the new definition safely. No task change was made. First run " +
                ".\install_development_autostart.ps1 -Action Stop -DataSource Cloud, then rerun " +
                "Install with -StartNow; or omit -StartNow and reboot to activate the new definition."
            )
        }
        $scheduledAction = New-ScheduledTaskAction `
            -Execute $powerShellExe `
            -Argument $requestedTaskArguments `
            -WorkingDirectory $root
        $trigger = New-ScheduledTaskTrigger -AtStartup
        $trigger.Delay = "PT1M"
        $settings = New-ScheduledTaskSettingsSet `
            -StartWhenAvailable `
            -MultipleInstances IgnoreNew `
            -RestartCount 10 `
            -RestartInterval (New-TimeSpan -Minutes 1) `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit ([TimeSpan]::Zero)
        $principal = New-ScheduledTaskPrincipal `
            -UserId $RunAsUser `
            -LogonType Password `
            -RunLevel Limited
        $definition = New-ScheduledTask `
            -Action $scheduledAction `
            -Trigger $trigger `
            -Settings $settings `
            -Principal $principal `
            -Description $taskMarker

        $securePassword = Read-Host "Windows password for scheduled-task account $RunAsUser" -AsSecureString
        $bstr = [IntPtr]::Zero
        $plainPassword = ""
        try {
            $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
            $plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
            if (-not $plainPassword) {
                throw "The scheduled-task account password cannot be empty."
            }
            Register-ScheduledTask `
                -TaskName $TaskName `
                -TaskPath $taskPath `
                -InputObject $definition `
                -User $RunAsUser `
                -Password $plainPassword `
                -Force | Out-Null
        }
        finally {
            $plainPassword = $null
            if ($bstr -ne [IntPtr]::Zero) {
                [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
            }
        }

        $registered = Get-ExactTask
        Assert-TaskDefinition $registered
        Write-Host "Installed startup task: $TaskName"
        Write-Host "Trigger: system startup + 60 seconds"
        Write-Host "Run as: $RunAsUser (Password logon, least privilege)"
        if ($StartNow) {
            Start-ScheduledTask -TaskName $TaskName -TaskPath $taskPath
            Write-Host "Startup task was started. Use -Action Status to check readiness."
        }
        exit 0
    }
    "Uninstall" {
        if (-not (Test-IsAdministrator)) {
            throw "The elevated uninstaller did not receive administrator rights."
        }
        $existing = Get-ExactTask
        if ($null -eq $existing) {
            Write-Host "Startup task is already absent: $TaskName"
            exit 0
        }
        Assert-RecognizedTask $existing
        Stop-ScheduledTask -TaskName $TaskName -TaskPath $taskPath -ErrorAction SilentlyContinue
        Wait-TaskStopped
        $stopExitCode = Invoke-Supervisor "Stop"
        if ($stopExitCode -ne 0) {
            throw "Verified development services could not be stopped; the startup task was kept."
        }
        Unregister-ScheduledTask -TaskName $TaskName -TaskPath $taskPath -Confirm:$false
        Write-Host "Removed startup task: $TaskName"
        exit 0
    }
    "Start" {
        $existing = Get-ExactTask
        if ($null -eq $existing) {
            throw "Startup task is not installed: $TaskName"
        }
        Assert-TaskDefinition $existing
        Start-ScheduledTask -TaskName $TaskName -TaskPath $taskPath
        Write-Host "Started startup task: $TaskName"
        exit 0
    }
    "Stop" {
        $existing = Get-ExactTask
        if ($null -ne $existing) {
            Assert-RecognizedTask $existing
            Stop-ScheduledTask -TaskName $TaskName -TaskPath $taskPath -ErrorAction SilentlyContinue
            Wait-TaskStopped
        }
        $stopExitCode = Invoke-Supervisor "Stop"
        exit $stopExitCode
    }
    default {
        $existing = Get-ExactTask
        $taskReady = $false
        if ($null -eq $existing) {
            Write-Host "Scheduled task: not installed ($TaskName)"
        }
        else {
            try {
                Assert-TaskDefinition $existing
                $info = Get-ScheduledTaskInfo -TaskName $TaskName -TaskPath $taskPath
                Write-Host "Scheduled task: $($existing.State)"
                Write-Host "Last result: $($info.LastTaskResult)"
                Write-Host "Last run: $($info.LastRunTime)"
                $taskReady = $true
            }
            catch {
                Write-Host "Scheduled task: invalid - $($_.Exception.Message)"
            }
        }
        $serviceExitCode = Invoke-Supervisor "Status"
        if ($taskReady -and $serviceExitCode -eq 0) {
            exit 0
        }
        exit 1
    }
}
