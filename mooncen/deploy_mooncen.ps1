param(
    [ValidateSet("deploy", "full-deploy", "deploy-all", "full-deploy-all", "preflight", "ha-status", "targets", "summary", "status", "restart", "health", "logs", "doctor", "coordinates", "locations", "crawler-once", "crawler-update", "crawler-activate", "crawler-control-install", "ai-reset-start", "ai-reset-full-start", "ai-quality", "functional-test", "functional-test-status", "frontend-diagnose", "frontend-fix-permissions", "frontend-rebuild", "cloudflare-gate-status", "cloudflare-gate-disable", "cloudflare-gate-enable", "cloudflared-status", "cloudflared-stop", "cloudflared-start", "cloudflared-reset", "cloudflared-token-id", "cloudflared-role-guard-status", "cloudflared-role-guard-run", "cloudflared-token-sync", "bot-status", "bot-start", "bot-stop", "replica-status", "failover-status", "failover-enable", "failover-disable", "standby-secrets-check")]
    [string]$Action = "deploy",
    [string]$Target = "default",
    [string]$ExpectedCommit = "",
    [string]$ExpectedArchiveSha256 = "",
    [string]$ExpectedReleaseTreeSha256 = "",
    [string]$ReleaseSignaturePath = "",
    [string]$SourceCommit = "",
    [string]$ExpectedSourceTree = "",
    [string]$ExpectedTargetIdentity = "",
    [string]$DeploymentIntentToken = "",
    [string]$BatchId = "",
    [string]$Service = "",
    [switch]$SkipWorkers,
    [switch]$AllowCrawlerInterruption,
    [switch]$ActiveCloud
)

$ErrorActionPreference = "Stop"

$configPath = Join-Path $PSScriptRoot "deploy.local.ps1"
if (-not (Test-Path $configPath)) {
    throw "Missing deploy.local.ps1. Copy deploy.local.example.ps1 to deploy.local.ps1 and edit it once."
}

. $configPath

function Get-ConfigValue {
    param(
        [string]$Name,
        [object]$Default = ""
    )
    $variable = Get-Variable -Name $Name -ErrorAction SilentlyContinue
    if ($variable -and $null -ne $variable.Value -and "$($variable.Value)" -ne "") {
        return $variable.Value
    }
    return $Default
}

function Mask-SensitiveText {
    param([string]$Text)
    if (-not $Text) {
        return ""
    }
    $masked = $Text
    $masked = $masked -replace '(?i)(DB_PASSWORD|DB_API_PASSWORD|DB_CRAWLER_PASSWORD|DB_AI_PASSWORD|DB_APPLIER_PASSWORD|PRIMARY_DB_PASSWORD|DB_BACKUP_PASSWORD|DB_CHECK_PASSWORD|AUTH_SECRET|MOONCEN_OPS_PASSWORD_HASH|KAKAO_MAPS_REST_API_KEY|GOOGLE_OAUTH_CLIENT_SECRET|NAVER_OAUTH_CLIENT_SECRET|MOONCEN_BOT_TOKEN|MOONCEN_SMTP_PASSWORD|OPS_CLOUDFLARE_ANALYTICS_TOKEN|MOONCEN_SERVER_MONITOR_TOKEN|TUNNEL_TOKEN)=["'']?[^"''\s;]+', '$1=<redacted>'
    $masked = $masked -replace '(?i)(-OpsPasswordHash\s+)[^\s]+', '$1<redacted>'
    $masked = $masked -replace '(?i)(-SmtpPassword\s+)[^\s]+', '$1<redacted>'
    $masked = $masked -replace '(?i)(-OpsCloudflareAnalyticsToken\s+)[^\s]+', '$1<redacted>'
    $masked = $masked -replace '(?i)(-ServerMonitorToken\s+)[^\s]+', '$1<redacted>'
    $masked = $masked -replace '(?i)(--token\s+)[^\s]+', '$1<redacted>'
    $masked = $masked -replace 'GOCSPX-[A-Za-z0-9_-]+', '<redacted-google-secret>'
    $masked = $masked -replace 'eyJhIjoi[A-Za-z0-9_-]+', '<redacted-cloudflare-token>'
    $masked = $masked -replace '[0-9]{8,12}:[A-Za-z0-9_-]{30,}', '<redacted-telegram-token>'
    return $masked
}

function Get-DeployServerRegistry {
    $registryPath = Join-Path $PSScriptRoot "config/deploy_servers.json"
    $registry = [ordered]@{}

    if (Test-Path $registryPath) {
        $json = Get-Content $registryPath -Raw | ConvertFrom-Json
        $defaultTarget = if ($json.defaultTarget) { [string]$json.defaultTarget } else { "cloud" }
        foreach ($prop in $json.servers.PSObject.Properties) {
            $row = $prop.Value
            $entry = [pscustomobject]@{
                Name = $prop.Name
                Server = [string]$row.server
                User = if ($row.user) { [string]$row.user } else { "ubuntu" }
                Domain = if ($row.domain) { [string]$row.domain } else { [string]$row.server }
                RemoteDir = if ($row.remoteDir) { [string]$row.remoteDir } else { "/opt/mooncen" }
                IdentityFile = if ($row.identityFile) { [string]$row.identityFile } else { "" }
                Role = if ($row.role) { ([string]$row.role).ToLowerInvariant() } else { "standby" }
                DeployProfile = if ($row.deployProfile) { ([string]$row.deployProfile).ToLowerInvariant() } else { "full-stack" }
                Environment = if ($row.environment) { ([string]$row.environment).ToLowerInvariant() } else { "production" }
                ReplicatesFrom = if ($row.replicatesFrom) { [string]$row.replicatesFrom } else { "" }
                Active = if ($null -ne $row.active) { [bool]$row.active } else { $false }
                OpsConsole = if ($null -ne $row.opsConsole) { [bool]$row.opsConsole } else { $false }
            }
            if (
                $entry.Name -notmatch '^[a-z][a-z0-9_-]{0,31}$' -or
                @("primary", "standby", "crawler", "crawler-control") -notcontains $entry.Role -or
                @("full-stack", "crawler-only", "control-only") -notcontains $entry.DeployProfile -or
                @("development", "staging", "production") -notcontains $entry.Environment -or
                (($entry.Role -eq "crawler") -ne ($entry.DeployProfile -eq "crawler-only")) -or
                (($entry.Role -eq "crawler-control") -ne ($entry.DeployProfile -eq "control-only")) -or
                ($entry.DeployProfile -in @("crawler-only", "control-only") -and $entry.Active) -or
                ($entry.DeployProfile -in @("crawler-only", "control-only") -and $entry.RemoteDir -ne "/opt/mooncen")
            ) {
                throw "Invalid environment/role/deployProfile contract for deploy target '$($entry.Name)'."
            }
            $registry[$prop.Name] = $entry
        }
        return [pscustomobject]@{ DefaultTarget = $defaultTarget; Servers = $registry; Source = $registryPath }
    }

    $cloudServer = Get-ConfigValue "MoonCenCloudServer" "cloud"
    if ($cloudServer) {
        $registry["cloud"] = [pscustomobject]@{
            Name = "cloud"
            Server = $cloudServer
            User = Get-ConfigValue "MoonCenCloudUser" "ubuntu"
            Domain = Get-ConfigValue "MoonCenCloudDomain" (Get-ConfigValue "MoonCenDomain" $cloudServer)
            RemoteDir = Get-ConfigValue "MoonCenCloudRemoteDir" (Get-ConfigValue "MoonCenRemoteDir" "/opt/mooncen")
            IdentityFile = Get-ConfigValue "MoonCenCloudIdentityFile" (Join-Path $PSScriptRoot "oracle_cloud_mooncen.key")
            Role = "primary"
            DeployProfile = "full-stack"
            Environment = "production"
            ReplicatesFrom = ""
            Active = $true
            OpsConsole = $false
        }
    }

    return [pscustomobject]@{ DefaultTarget = "cloud"; Servers = $registry; Source = $configPath }
}

function Get-ProductionCrawlerContract {
    param(
        [object]$Servers,
        [string]$SnapshotCommit = ""
    )

    $topologyPath = Join-Path $PSScriptRoot "config/production_topology.json"
    if ($SnapshotCommit) {
        Push-Location $PSScriptRoot
        try {
            $topologyText = @(& git show "${SnapshotCommit}:config/production_topology.json" 2>$null) -join "`n"
            if ($LASTEXITCODE -ne 0 -or -not $topologyText) {
                throw "Reviewed deployment snapshot is missing config/production_topology.json"
            }
        } finally {
            Pop-Location
        }
    } else {
        if (-not (Test-Path -LiteralPath $topologyPath -PathType Leaf)) {
            throw "Missing reviewed production topology: config/production_topology.json"
        }
        $topologyText = Get-Content -LiteralPath $topologyPath -Raw
    }
    try {
        $topology = $topologyText | ConvertFrom-Json
    } catch {
        throw "Reviewed production topology is not valid JSON"
    }
    $crawlerModeProperty = $topology.PSObject.Properties["crawlerMode"]
    if (
        $null -eq $crawlerModeProperty -or
        $crawlerModeProperty.Value -isnot [string] -or
        @("legacy", "distributed") -notcontains [string]$crawlerModeProperty.Value
    ) {
        throw "Reviewed production topology crawlerMode must be exactly 'legacy' or 'distributed'"
    }
    $crawlerMode = [string]$crawlerModeProperty.Value
    $primaryPlacements = @(
        $topology.services.crawler |
            Where-Object { [string]$_.role -eq "primary" }
    )
    if ($primaryPlacements.Count -ne 1) {
        throw "Reviewed production topology must declare exactly one crawler primary"
    }
    $crawlerTarget = [string]$primaryPlacements[0].node
    if ($crawlerTarget -notmatch '^[a-z][a-z0-9_-]{0,31}$') {
        throw "Reviewed production topology has an invalid crawler target"
    }
    if ($null -eq $Servers[$crawlerTarget]) {
        throw "Crawler target '$crawlerTarget' is not in config/deploy_servers.json"
    }
    if ($crawlerTarget -eq "cloud") {
        throw "The cloud Web/API/DB host cannot own the crawler runtime"
    }
    $controlService = $topology.services.PSObject.Properties["crawler_control"]
    if ($null -eq $controlService) {
        throw "Reviewed production topology must declare crawler_control"
    }
    $controlPrimaryPlacements = @(
        $controlService.Value |
            Where-Object { [string]$_.role -eq "primary" }
    )
    if ($controlPrimaryPlacements.Count -ne 1) {
        throw "Reviewed production topology must declare exactly one crawler_control primary"
    }
    $controlTarget = [string]$controlPrimaryPlacements[0].node
    if ($controlTarget -notmatch '^[a-z][a-z0-9_-]{0,31}$') {
        throw "Reviewed production topology has an invalid crawler_control target"
    }
    $controlServer = $Servers[$controlTarget]
    if ($null -eq $controlServer) {
        throw "Crawler control target '$controlTarget' is not in config/deploy_servers.json"
    }
    $stagingDatabaseService = $topology.services.PSObject.Properties["staging_database"]
    if ($null -eq $stagingDatabaseService) {
        throw "Reviewed production topology must declare staging_database"
    }
    $stagingDatabasePrimaries = @(
        $stagingDatabaseService.Value |
            Where-Object { [string]$_.role -eq "primary" }
    )
    if (
        $stagingDatabasePrimaries.Count -ne 1 -or
        [string]$stagingDatabasePrimaries[0].node -ne $controlTarget
    ) {
        throw "Reviewed staging_database primary must be co-located with crawler_control"
    }
    $productionDatabasePrimaries = @(
        $topology.services.database |
            Where-Object { [string]$_.role -eq "primary" }
    )
    if (
        $productionDatabasePrimaries.Count -ne 1 -or
        [string]$productionDatabasePrimaries[0].node -ne "cloud"
    ) {
        throw "Reviewed production database primary must remain on cloud"
    }
    if (
        $controlTarget -eq "cloud" -or
        $controlTarget -eq $crawlerTarget -or
        $controlServer.Role -ne "crawler-control" -or
        $controlServer.DeployProfile -ne "control-only" -or
        $controlServer.Active
    ) {
        throw "The crawler_control primary must be a distinct inactive crawler-control/control-only target"
    }
    return [pscustomobject]@{
        Mode = $crawlerMode
        Target = $crawlerTarget
        ControlTarget = $controlTarget
    }
}

function Expand-ConfigPath {
    param([string]$PathValue)
    if (-not $PathValue) {
        return ""
    }
    if ([string]::Equals($PathValue.Trim(), "ssh-agent", [StringComparison]::OrdinalIgnoreCase)) {
        return ""
    }
    $expanded = [Environment]::ExpandEnvironmentVariables($PathValue)
    $userProfile = if ($env:USERPROFILE) { $env:USERPROFILE } else { $env:HOME }
    if ($userProfile) {
        $expanded = $expanded.Replace('$env:USERPROFILE', $userProfile)
    }
    return $expanded
}

function Test-ReadableConfigPath {
    param([string]$PathValue)
    if (-not $PathValue) {
        return $false
    }
    try {
        return Test-Path -LiteralPath $PathValue
    } catch {
        throw "Configured identity file is not accessible: $PathValue. Fix the local SSH key path/ACL or update config/deploy_servers.json."
    }
}

function Test-StrictReadableFile {
    param([string]$PathValue)
    if (-not $PathValue) {
        return $false
    }
    try {
        if (-not [bool](Test-Path -LiteralPath $PathValue -PathType Leaf -ErrorAction Stop)) {
            return $false
        }
        $stream = [System.IO.File]::Open($PathValue, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
        $stream.Close()
        return $true
    } catch {
        return $false
    }
}

function Get-SshAgentReadiness {
    $ssh = Get-Command ssh -ErrorAction SilentlyContinue
    if ($null -eq $ssh) {
        return [pscustomobject]@{ Ok = $false; Detail = "ssh command not found" }
    }

    $sshAdd = Get-Command ssh-add -ErrorAction SilentlyContinue
    if ($null -eq $sshAdd) {
        return [pscustomobject]@{ Ok = $false; Detail = "ssh-add command not found" }
    }

    $process = New-Object System.Diagnostics.Process
    try {
        $process.StartInfo = New-Object System.Diagnostics.ProcessStartInfo
        $process.StartInfo.FileName = $sshAdd.Source
        $process.StartInfo.Arguments = "-L"
        $process.StartInfo.UseShellExecute = $false
        $process.StartInfo.CreateNoWindow = $true
        $process.StartInfo.RedirectStandardOutput = $true
        $process.StartInfo.RedirectStandardError = $true
        if (-not $process.Start()) {
            return [pscustomobject]@{ Ok = $false; Detail = "ssh-add could not be started" }
        }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(5000)) {
            try { $process.Kill() } catch { }
            return [pscustomobject]@{ Ok = $false; Detail = "ssh-add -L timed out" }
        }
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        if ($process.ExitCode -ne 0) {
            $reason = ([string]$stderr).Trim()
            if (-not $reason) {
                $reason = "ssh-agent is unavailable or has no loaded key"
            }
            return [pscustomobject]@{ Ok = $false; Detail = $reason }
        }

        foreach ($line in ([string]$stdout -split "`r?`n")) {
            $parts = @($line.Trim() -split '\s+', 3)
            if ($parts.Count -lt 2 -or $parts[0] -notmatch '^(?:(?:ssh-(?:ed25519|rsa)|ecdsa-sha2-nistp(?:256|384|521)|sk-(?:ssh-ed25519|ecdsa-sha2-nistp256)@openssh\.com)|(?:ssh-(?:ed25519|rsa)|ecdsa-sha2-nistp(?:256|384|521)|sk-(?:ssh-ed25519|ecdsa-sha2-nistp256))-cert-v01@openssh\.com)$') {
                continue
            }
            try {
                $blob = [Convert]::FromBase64String($parts[1])
            } catch {
                continue
            }
            if ($blob.Length -lt 8) {
                continue
            }
            $typeLength = `
                ([int]$blob[0] -shl 24) -bor `
                ([int]$blob[1] -shl 16) -bor `
                ([int]$blob[2] -shl 8) -bor `
                [int]$blob[3]
            if ($typeLength -le 0 -or $typeLength -gt ($blob.Length - 4)) {
                continue
            }
            $embeddedType = [Text.Encoding]::ASCII.GetString($blob, 4, $typeLength)
            if ([string]::Equals($embeddedType, $parts[0], [StringComparison]::Ordinal)) {
                return [pscustomobject]@{
                    Ok = $true
                    Detail = "loaded public key verified via ssh-add -L ($($parts[0]))"
                }
            }
        }
        return [pscustomobject]@{
            Ok = $false
            Detail = "ssh-add -L returned no valid OpenSSH public key"
        }
    } catch {
        return [pscustomobject]@{
            Ok = $false
            Detail = "ssh-agent key verification failed: $($_.Exception.Message)"
        }
    } finally {
        $process.Dispose()
    }
}

function Test-PathExistsSafe {
    param([string]$PathValue)
    if (-not $PathValue) {
        return $false
    }
    try {
        return [bool](Test-Path -LiteralPath $PathValue -ErrorAction Stop)
    } catch {
        return $false
    }
}

function Write-CheckLine {
    param(
        [string]$Name,
        [bool]$Ok,
        [string]$Detail
    )
    $state = if ($Ok) { "OK" } else { "CHECK" }
    Write-Host ("{0,-28} {1,-6} {2}" -f $Name, $state, $Detail)
}

function Test-DeployPreflight {
    $checks = New-Object System.Collections.Generic.List[bool]
    Write-Host "MoonCen deploy preflight"
    Write-Host ("registry: {0}" -f $registryInfo.Source)
    Write-Host ("default_target: {0}" -f $registryInfo.DefaultTarget)
    Write-Host ""

    $cloud = $registryInfo.Servers["cloud"]
    $hasCloud = $null -ne $cloud
    $crawlerOwner = $registryInfo.Servers[$crawlerTarget]
    $hasCrawlerOwner = $null -ne $crawlerOwner
    $crawlerControl = $registryInfo.Servers[$crawlerControlTarget]
    $hasCrawlerControl = $null -ne $crawlerControl
    Write-CheckLine "cloud configured" $hasCloud $(if ($hasCloud) { "$($cloud.User)@$($cloud.Server) role=$($cloud.Role) profile=$($cloud.DeployProfile) active=$($cloud.Active)" } else { "missing config/deploy_servers.json server 'cloud'" })
    $checks.Add($hasCloud)
    Write-CheckLine "crawler owner configured" $hasCrawlerOwner $(if ($hasCrawlerOwner) { "$crawlerTarget -> $($crawlerOwner.User)@$($crawlerOwner.Server) mode=$crawlerMode" } else { "topology crawler owner is missing from the deploy registry" })
    $checks.Add($hasCrawlerOwner)
    Write-CheckLine "crawler control configured" $hasCrawlerControl $(if ($hasCrawlerControl) { "$crawlerControlTarget -> $($crawlerControl.User)@$($crawlerControl.Server) mode=$crawlerMode" } else { "topology crawler_control primary is missing from the deploy registry" })
    $checks.Add($hasCrawlerControl)
    if ($hasCloud) {
        $ok = ($cloud.Role -eq "primary" -and $cloud.DeployProfile -eq "full-stack" -and $cloud.Active)
        Write-CheckLine "cloud role" $ok "expected primary profile=full-stack active=true"
        $checks.Add($ok)
    }
    if ($hasCrawlerOwner) {
        $ok = $crawlerOwner.Role -eq "crawler" -and
            $crawlerOwner.DeployProfile -eq "crawler-only" -and
            -not $crawlerOwner.Active
        $expectedCrawlerRole = "expected dedicated role=crawler profile=crawler-only active=false"
        Write-CheckLine "crawler owner role" $ok $expectedCrawlerRole
        $checks.Add($ok)
    }
    if ($hasCrawlerControl) {
        $ok = $crawlerControl.Role -eq "crawler-control" -and
            $crawlerControl.DeployProfile -eq "control-only" -and
            -not $crawlerControl.Active
        $expectedControlRole = "expected dedicated role=crawler-control profile=control-only active=false"
        Write-CheckLine "crawler control role" $ok $expectedControlRole
        $checks.Add($ok)
    }
    $okDefault = $registryInfo.Servers.Contains($registryInfo.DefaultTarget)
    Write-CheckLine "default target" $okDefault "configured=$($registryInfo.DefaultTarget); deploy/full-deploy require an explicit -Target"
    $checks.Add($okDefault)

    $git = Get-Command git -ErrorAction SilentlyContinue
    $gitOk = $null -ne $git
    Write-CheckLine "git available" $gitOk $(if ($gitOk) { $git.Source } else { "git command not found" })
    $checks.Add($gitOk)
    if ($gitOk) {
        Push-Location $PSScriptRoot
        try {
            $previousErrorActionPreference = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            try {
                $head = & git rev-parse --verify 'HEAD^{commit}' 2>$null
                $headExitCode = $LASTEXITCODE
            } finally {
                $ErrorActionPreference = $previousErrorActionPreference
            }
            $headOk = $headExitCode -eq 0 -and "$head" -match '^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$'
            Write-CheckLine "deploy Git HEAD" $headOk $(if ($headOk) { "$head".Trim().ToLowerInvariant() } else { "missing committed HEAD" })
            $checks.Add($headOk)
            $worktree = @(git status --porcelain=v1 --untracked-files=all)
            $worktreeOk = $LASTEXITCODE -eq 0 -and $worktree.Count -eq 0
            $worktreeDetail = if ($worktreeOk) { "clean" } else { "$($worktree.Count) changed/untracked path(s); deployment would omit them" }
            Write-CheckLine "deploy Git worktree" $worktreeOk $worktreeDetail
            $checks.Add($worktreeOk)
        } finally {
            Pop-Location
        }
    }

    foreach ($item in $registryInfo.Servers.Values) {
        $usesDefaultSsh = [string]::Equals(
            ([string]$item.IdentityFile).Trim(),
            "ssh-agent",
            [StringComparison]::OrdinalIgnoreCase
        )
        if ($usesDefaultSsh) {
            $agentReadiness = Get-SshAgentReadiness
            $ready = [bool]$agentReadiness.Ok
            Write-CheckLine "$($item.Name) SSH auth" $ready ([string]$agentReadiness.Detail)
            $checks.Add($ready)
            continue
        }
        $path = Expand-ConfigPath $item.IdentityFile
        $exists = Test-PathExistsSafe $path
        $readable = Test-StrictReadableFile $path
        Write-CheckLine "$($item.Name) identity exists" $exists $path
        Write-CheckLine "$($item.Name) identity readable" $readable "required for SSH deploy/status from this Windows user"
        $checks.Add($exists)
        $checks.Add($readable)
    }

    Write-Host ""
    $tokenOk = -not [string]::IsNullOrWhiteSpace([string]$botToken)
    $chatOk = -not [string]::IsNullOrWhiteSpace([string]$botChatId)
    $botConfigOk = ($tokenOk -and $chatOk) -or (-not $tokenOk -and -not $chatOk)
    Write-CheckLine "telegram bot" $botConfigOk $(if ($tokenOk -and $chatOk) { "optional credentials configured (hidden)" } elseif ($botConfigOk) { "disabled on cloud-only topology" } else { "token and chat id must be configured together" })
    $checks.Add($botConfigOk)

    $configuredDbPassword = [string]$dbPassword
    $dbPasswordOk = (
        -not $configuredDbPassword -or (
            $configuredDbPassword.Length -ge 16 -and
            $configuredDbPassword -notmatch '^(change-me|replace-with)' -and
            -not $configuredDbPassword.Contains("`n") -and
            -not $configuredDbPassword.Contains("`r")
        )
    )
    Write-CheckLine "DB owner password" $dbPasswordOk $(
        if (-not $configuredDbPassword) {
            "not stored locally; primary protected secret store will be used"
        } elseif ($dbPasswordOk) {
            "configured locally (hidden)"
        } else {
            "MoonCenDbPassword must be random and at least 16 characters"
        }
    )
    $checks.Add($dbPasswordOk)

    $effectiveBackupAgeRecipient = [string]$backupAgeRecipient
    if (-not $effectiveBackupAgeRecipient -and $hasCloud) {
        $effectiveBackupAgeRecipient = Get-RemoteEnvValueFromHost `
            $cloud.Server $cloud.User (Expand-ConfigPath $cloud.IdentityFile) $cloud.RemoteDir "BACKUP_AGE_RECIPIENT"
    }
    $backupAgeRecipientOk = $effectiveBackupAgeRecipient -match '^age1[0-9a-z]+$'
    Write-CheckLine "backup age recipient" $backupAgeRecipientOk $(if ($backupAgeRecipientOk) { "configured public recipient" } else { "missing MoonCenBackupAgeRecipient and no reusable primary value" })
    $checks.Add($backupAgeRecipientOk)
    $backupTrustOk = $backupAgeRecipientOk -and $hasCloud -and (
        Test-RemoteBackupTrustContract $cloud $effectiveBackupAgeRecipient
    )
    Write-CheckLine "backup trust bundle" $backupTrustOk $(if ($backupTrustOk) { "all five primary trust files match ownership, mode, and recipient contracts" } else { "provision and escrow age, NAS SSH, known_hosts, and manifest signing trust on primary" })
    $checks.Add($backupTrustOk)

    $hostSingleOk = -not ([string]$ollamaHost).Contains(",")
    Write-CheckLine "ollama host" $hostSingleOk "MoonCenOllamaHost=$ollamaHost"
    $checks.Add($hostSingleOk)
    $hostList = @()
    if ($ollamaHosts) {
        $hostList = @($ollamaHosts -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    }
    $hostsOk = ($hostList -contains "http://wtr-linux:11434") -and ($hostList -contains "http://victus:11434")
    Write-CheckLine "ollama hosts" $hostsOk $(if ($hostList.Count) { ($hostList -join ",") } else { "missing MoonCenOllamaHosts" })
    $checks.Add($hostsOk)
    $modelOk = $ollamaModel -eq "qwen3.5:9b"
    Write-CheckLine "ollama model" $modelOk "MoonCenOllamaModel=$ollamaModel"
    $checks.Add($modelOk)

    Write-Host ""
    Write-CheckLine "ops console remote install" (-not $installOpsConsole) "local-only; remote install disabled"
    Write-CheckLine "automatic failover" $true "disabled by deploy script and bot policy"
    $crawlerSchedulingDetail = if ($crawlerMode -eq "legacy") {
        "reviewed owner=$crawlerTarget; legacy timer managed by deploy"
    } else {
        "reviewed owner=$crawlerTarget; distributed control plane managed separately"
    }
    Write-CheckLine "crawler scheduling" $true $crawlerSchedulingDetail

    if ($checks -contains $false) {
        Write-Host ""
        Write-Host "Preflight result: CHECK"
        exit 1
    }
    Write-Host ""
    Write-Host "Preflight result: OK"
}

function Get-DeployServer {
    param(
        [object]$Servers,
        [string]$Name
    )
    if (-not $Servers.Contains($Name)) {
        $known = ($Servers.Keys | Sort-Object) -join ", "
        throw "Unknown deploy target '$Name'. Known targets: $known. Add it to config/deploy_servers.json."
    }
    return $Servers[$Name]
}

function Get-ActiveDeployServer {
    param([object]$Servers)
    foreach ($item in $Servers.Values) {
        if ($item.Active -or $item.Role -eq "primary") {
            return $item
        }
    }
    return $Servers.Values | Select-Object -First 1
}

function ConvertTo-TargetIdentityBase64 {
    param([AllowEmptyString()][string]$Value)
    return [Convert]::ToBase64String(
        [Text.Encoding]::UTF8.GetBytes(([string]$Value).Trim())
    )
}

function Get-DeployTargetIdentity {
    param([Parameter(Mandatory = $true)][object]$ServerConfig)

    $canonical = @(
        "name_b64=$(ConvertTo-TargetIdentityBase64 ([string]$ServerConfig.Name))"
        "server_b64=$(ConvertTo-TargetIdentityBase64 ([string]$ServerConfig.Server))"
        "user_b64=$(ConvertTo-TargetIdentityBase64 ([string]$ServerConfig.User))"
        "domain_b64=$(ConvertTo-TargetIdentityBase64 ([string]$ServerConfig.Domain))"
        "remote_dir_b64=$(ConvertTo-TargetIdentityBase64 ([string]$ServerConfig.RemoteDir))"
        "role_b64=$(ConvertTo-TargetIdentityBase64 (([string]$ServerConfig.Role).ToLowerInvariant()))"
        "deploy_profile_b64=$(ConvertTo-TargetIdentityBase64 (([string]$ServerConfig.DeployProfile).ToLowerInvariant()))"
        "environment_b64=$(ConvertTo-TargetIdentityBase64 (([string]$ServerConfig.Environment).ToLowerInvariant()))"
        "active=$(if ([bool]$ServerConfig.Active) { '1' } else { '0' })"
    ) -join "`n"
    $sha256 = [Security.Cryptography.SHA256]::Create()
    try {
        $digest = $sha256.ComputeHash([Text.Encoding]::UTF8.GetBytes($canonical))
        return ($digest | ForEach-Object { $_.ToString("x2") }) -join ""
    } finally {
        $sha256.Dispose()
    }
}

function Get-CurrentDeployCommit {
    $git = Get-Command git -ErrorAction SilentlyContinue
    if (-not $git) {
        throw "git is required to verify the reviewed deployment commit"
    }

    Push-Location $PSScriptRoot
    try {
        $previousErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $rawCommit = & git rev-parse --verify 'HEAD^{commit}' 2>$null
            $gitExitCode = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $previousErrorActionPreference
        }
        if ($gitExitCode -ne 0 -or -not $rawCommit) {
            throw "Unable to resolve the current Git HEAD for deployment"
        }
        $commit = ([string]$rawCommit).Trim().ToLowerInvariant()
        if ($commit -notmatch '^(?:[0-9a-f]{40}|[0-9a-f]{64})$') {
            throw "Git returned an invalid deployment commit identifier"
        }
        return $commit
    } finally {
        Pop-Location
    }
}

function Assert-ExpectedDeployCommit {
    param([AllowEmptyString()][string]$Expected)
    if (-not $Expected) {
        return
    }
    $normalizedExpected = $Expected.Trim().ToLowerInvariant()
    if ($normalizedExpected -notmatch '^(?:[0-9a-f]{40}|[0-9a-f]{64})$') {
        throw "ExpectedCommit must be an exact Git object identifier"
    }
    $actual = Get-CurrentDeployCommit
    if (-not [string]::Equals($actual, $normalizedExpected, [StringComparison]::Ordinal)) {
        throw "Git HEAD no longer matches ExpectedCommit; create a new reviewed deployment plan"
    }
}

$registryInfo = Get-DeployServerRegistry
if ($Action -in @("deploy", "full-deploy") -and $Target -eq "default") {
    throw "Deployment target is required. Use -Target cloud for this action."
}
if ($Action -in @("crawler-update", "crawler-activate", "crawler-once", "coordinates", "locations") -and $Target -eq "default") {
    throw "Deployment target is required. Use -Target gen1crawler for this action."
}
if ($Action -eq "crawler-control-install" -and $Target -eq "default") {
    throw "Deployment target is required. Use -Target gen1db for crawler-control-install."
}
if ($Target -eq "default") {
    $Target = $registryInfo.DefaultTarget
}
$targetConfig = Get-DeployServer $registryInfo.Servers $Target
$activeConfig = Get-ActiveDeployServer $registryInfo.Servers

if ($ExpectedTargetIdentity) {
    $ExpectedTargetIdentity = $ExpectedTargetIdentity.Trim().ToLowerInvariant()
    if ($ExpectedTargetIdentity -notmatch '^[0-9a-f]{64}$') {
        throw "ExpectedTargetIdentity must be a SHA-256 hexadecimal digest"
    }
    $currentTargetIdentity = Get-DeployTargetIdentity $targetConfig
    if (-not [string]::Equals(
        $currentTargetIdentity,
        $ExpectedTargetIdentity,
        [StringComparison]::Ordinal
    )) {
        throw "Deployment target identity changed after the reviewed plan was created"
    }
}
if ($DeploymentIntentToken) {
    $DeploymentIntentToken = $DeploymentIntentToken.Trim().ToLowerInvariant()
    if ($DeploymentIntentToken -notmatch '^[0-9a-f]{32}$') {
        throw "DeploymentIntentToken must be an exact lowercase 32-character hexadecimal token"
    }
    if ($Action -notin @("deploy", "full-deploy")) {
        throw "DeploymentIntentToken is only valid for a single native deployment"
    }
}
if ($ExpectedCommit) {
    $ExpectedCommit = $ExpectedCommit.Trim().ToLowerInvariant()
    if ($ExpectedCommit -notmatch '^(?:[0-9a-f]{40}|[0-9a-f]{64})$') {
        throw "ExpectedCommit must be an exact Git object identifier"
    }
}
if ($ExpectedArchiveSha256) {
    $ExpectedArchiveSha256 = $ExpectedArchiveSha256.Trim().ToLowerInvariant()
    if ($ExpectedArchiveSha256 -notmatch '^[0-9a-f]{64}$') {
        throw "ExpectedArchiveSha256 must be an exact lowercase SHA-256 digest"
    }
    if ($Action -ne "crawler-control-install") {
        throw "ExpectedArchiveSha256 is only valid with crawler-control-install"
    }
}
if ($ExpectedReleaseTreeSha256) {
    $ExpectedReleaseTreeSha256 = $ExpectedReleaseTreeSha256.Trim().ToLowerInvariant()
    if ($ExpectedReleaseTreeSha256 -cnotmatch '^[0-9a-f]{64}$') {
        throw "ExpectedReleaseTreeSha256 must be an exact lowercase SHA-256 digest"
    }
    if ($Action -ne "crawler-control-install") {
        throw "ExpectedReleaseTreeSha256 is only valid with crawler-control-install"
    }
}
if ($ReleaseSignaturePath -and $Action -ne "crawler-control-install") {
    throw "ReleaseSignaturePath is only valid with crawler-control-install"
}
if ([bool]$SourceCommit -xor [bool]$ExpectedSourceTree) {
    throw "SourceCommit and ExpectedSourceTree must be provided together"
}
if ($SourceCommit) {
    $SourceCommit = $SourceCommit.Trim().ToLowerInvariant()
    $ExpectedSourceTree = $ExpectedSourceTree.Trim().ToLowerInvariant()
    if ($SourceCommit -notmatch '^(?:[0-9a-f]{40}|[0-9a-f]{64})$') {
        throw "SourceCommit must be an exact Git object identifier"
    }
    if ($ExpectedSourceTree -notmatch '^(?:[0-9a-f]{40}|[0-9a-f]{64})$') {
        throw "ExpectedSourceTree must be an exact Git tree identifier"
    }
}
$crawlerContract = Get-ProductionCrawlerContract $registryInfo.Servers $SourceCommit
$crawlerTarget = $crawlerContract.Target
$crawlerControlTarget = $crawlerContract.ControlTarget
$crawlerMode = $crawlerContract.Mode
$enableCrawler = $crawlerMode -eq "legacy" -and $targetConfig.Name -eq $crawlerTarget
if (
    $Action -in @("deploy", "full-deploy") -and
    $targetConfig.DeployProfile -ne "full-stack"
) {
    if ($targetConfig.DeployProfile -eq "crawler-only") {
        throw "Target '$($targetConfig.Name)' is crawler-only. Full-stack deploy is forbidden; use crawler-update to check the dedicated updater status."
    }
    throw "Target '$($targetConfig.Name)' is control-only. Full-stack deploy is forbidden; use crawler-control-install for the dedicated control-plane path."
}

$server = $targetConfig.Server
$user = $targetConfig.User
$domain = $targetConfig.Domain
$remoteDir = $targetConfig.RemoteDir
$identityFile = Expand-ConfigPath $targetConfig.IdentityFile
$kakaoMapsJavascriptKey = Get-ConfigValue "MoonCenKakaoMapsJavascriptKey"
$kakaoMapsRestApiKey = Get-ConfigValue "MoonCenKakaoMapsRestApiKey"
$googleOAuthClientId = Get-ConfigValue "MoonCenGoogleOAuthClientId"
$googleOAuthClientSecret = Get-ConfigValue "MoonCenGoogleOAuthClientSecret"
$naverOAuthClientId = Get-ConfigValue "MoonCenNaverOAuthClientId"
$naverOAuthClientSecret = Get-ConfigValue "MoonCenNaverOAuthClientSecret"
$cloudflaredToken = Get-ConfigValue "MoonCenCloudflaredToken"
$dbPassword = Get-ConfigValue "MoonCenDbPassword"
$dbApiPassword = Get-ConfigValue "MoonCenDbApiPassword"
$dbCrawlerPassword = Get-ConfigValue "MoonCenDbCrawlerPassword"
$dbAiPassword = Get-ConfigValue "MoonCenDbAiPassword"
$dbApplierPassword = Get-ConfigValue "MoonCenDbApplierPassword"
$dbBackupPassword = Get-ConfigValue "MoonCenDbBackupPassword"
$dbCheckPassword = Get-ConfigValue "MoonCenDbCheckPassword"
$authSecret = Get-ConfigValue "MoonCenAuthSecret"
$opsLoginId = Get-ConfigValue "MoonCenOpsLoginId" $env:MOONCEN_OPS_LOGIN_ID
$opsPasswordHash = Get-ConfigValue "MoonCenOpsPasswordHash" $env:MOONCEN_OPS_PASSWORD_HASH
$dbSslRootCert = Get-ConfigValue "MoonCenDbSslRootCert"
$backupAgeRecipient = Get-ConfigValue "MoonCenBackupAgeRecipient"
$backupPort = Get-ConfigValue "MoonCenBackupPort"
$ollamaHost = Get-ConfigValue "MoonCenOllamaHost" "http://wtr-linux:11434"
$ollamaHosts = Get-ConfigValue "MoonCenOllamaHosts" $env:OLLAMA_HOSTS
$ollamaModel = Get-ConfigValue "MoonCenOllamaModel" "qwen3.5:9b"
$botToken = Get-ConfigValue "MoonCenBotToken"
$botChatId = Get-ConfigValue "MoonCenBotChatId"
$adminEmails = Get-ConfigValue "MoonCenAdminEmails"
$adminProviderIds = Get-ConfigValue "MoonCenAdminProviderIds"
$bugReportTo = Get-ConfigValue "MoonCenBugReportTo"
$bugReportFrom = Get-ConfigValue "MoonCenBugReportFrom"
$smtpHost = Get-ConfigValue "MoonCenSmtpHost"
$smtpPort = Get-ConfigValue "MoonCenSmtpPort"
$smtpUsername = Get-ConfigValue "MoonCenSmtpUsername"
$smtpPassword = Get-ConfigValue "MoonCenSmtpPassword"
$smtpSecurity = Get-ConfigValue "MoonCenSmtpSecurity"
$opsCloudflareAnalyticsZoneId = Get-ConfigValue "MoonCenOpsCloudflareAnalyticsZoneId"
$opsCloudflareAnalyticsToken = Get-ConfigValue "MoonCenOpsCloudflareAnalyticsToken"
$serverMonitorToken = Get-ConfigValue "MoonCenServerMonitorToken"
$skipSystemPackages = [bool](Get-ConfigValue "MoonCenSkipSystemPackages" $true)
$useScpFallback = [bool](Get-ConfigValue "MoonCenUseScpFallback" $false)
$standbyDeploy = ($targetConfig.Role -eq "standby" -or -not $targetConfig.Active) -and -not $ActiveCloud
if ($standbyDeploy -and -not $dbSslRootCert) {
    # Standby setup installs and maintains the PostgreSQL trust anchor at this
    # fixed root-owned path. Reuse it on later deploys; setup_project.sh still
    # fails closed if the file is absent, a symlink, or writable by group/world.
    $dbSslRootCert = "/etc/mooncen/db-root-ca.crt"
}
# Ops Console is local-only. Public application deployment must not install it.
$installOpsConsole = $false
$needsStandbySecrets = $standbyDeploy -and ($Action -in @("deploy", "full-deploy"))

if (-not $server) {
    throw "Set MoonCen server in deploy.local.ps1. Target=$Target"
}

$sshArgs = @()
if ($identityFile -and (Test-ReadableConfigPath $identityFile)) {
    $sshArgs += @("-i", (Resolve-Path $identityFile).Path, "-o", "IdentitiesOnly=yes")
}
$sshArgs += @(
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=yes",
    "-o", "UpdateHostKeys=no",
    "-o", "PreferredAuthentications=publickey",
    "-o", "PasswordAuthentication=no",
    "-o", "KbdInteractiveAuthentication=no",
    "-o", "NumberOfPasswordPrompts=0"
)
$remote = "$user@$server"

function Get-RemoteEnvValueFromHost {
    param(
        [string]$HostName,
        [string]$HostUser,
        [string]$HostIdentityFile,
        [string]$HostRemoteDir,
        [string]$Name
    )
    $args = @(
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
    if ($HostIdentityFile -and (Test-ReadableConfigPath $HostIdentityFile)) {
        $args += @("-i", (Resolve-Path $HostIdentityFile).Path, "-o", "IdentitiesOnly=yes")
    }
    if ($Name -notmatch '^[A-Z0-9_]+$') {
        throw "Invalid remote environment key name."
    }
    $envPath = "$HostRemoteDir/.env"
    $candidateFiles = "'$envPath' /etc/mooncen/api.env /etc/mooncen/backup.env"
    $candidateFiles += ' "$HOME/.config/mooncen/deploy-secrets.env" "$HOME/.config/mooncen/migrator.env"'
    $command = "for file in $candidateFiles; do if [ -r `"`$file`" ]; then encoded=`$(grep -E '^${Name}_B64=' `"`$file`" | tail -n1 | cut -d= -f2-); if [ -n `"`$encoded`" ]; then printf '%s' `"`$encoded`"; exit 0; fi; raw=`$(grep -E '^${Name}=' `"`$file`" | tail -n1 | cut -d= -f2-); if [ -n `"`$raw`" ]; then printf '%s' `"`$raw`" | base64 | tr -d '\r\n'; exit 0; fi; fi; done"
    # Windows OpenSSH reconstructs a remote command from native arguments and
    # can strip the nested quotes that protect $HOME, $file, and command
    # substitutions. Transport the non-secret reader as base64 so Bash sees
    # the exact reviewed command. Only the requested key name and file paths
    # are embedded; secret values still travel solely over SSH stdout.
    $encodedCommand = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($command))
    $transportCommand = "printf '%s' '$encodedCommand' | base64 -d | bash"
    $value = (& ssh @args "$HostUser@$HostName" $transportCommand 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $value) {
        return ""
    }
    try {
        $encodedValue = "$($value | Select-Object -First 1)".Trim()
        return [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encodedValue))
    } catch {
        return ""
    }
}

function Get-RemoteCloudflaredTokenFromHost {
    param(
        [string]$HostName,
        [string]$HostUser,
        [string]$HostIdentityFile
    )
    $args = @(
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
    if ($HostIdentityFile -and (Test-ReadableConfigPath $HostIdentityFile)) {
        $args += @("-i", (Resolve-Path $HostIdentityFile).Path, "-o", "IdentitiesOnly=yes")
    }
    $command = @'
sudo -n /usr/local/libexec/mooncen-cloudflared-token read
'@
    $value = (& ssh @args "$HostUser@$HostName" $command 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $value) {
        return ""
    }
    return "$($value | Select-Object -First 1)".Trim()
}

function Test-RemoteBackupTrustContract {
    param(
        [object]$Config,
        [string]$Recipient
    )
    if (-not $Config -or $Recipient -notmatch '^age1[0-9a-z]+$') {
        return $false
    }
    $args = @(
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
    $identityFile = Expand-ConfigPath $Config.IdentityFile
    if ($identityFile -and (Test-ReadableConfigPath $identityFile)) {
        $args += @("-i", (Resolve-Path $identityFile).Path, "-o", "IdentitiesOnly=yes")
    }
    $contractScript = @'
set -euo pipefail
recipient='__RECIPIENT__'
check_contract() {
  path="$1"
  expected="$2"
  sudo -n test -f "$path"
  ! sudo -n test -L "$path"
  [ "$(sudo -n stat -c '%U:%G:%a' "$path")" = "$expected" ]
}
check_contract /etc/mooncen/backup-age-key.txt root:root:600
[ "$(sudo -n age-keygen -y /etc/mooncen/backup-age-key.txt 2>/dev/null)" = "$recipient" ]
check_contract /etc/mooncen/backup-ssh-key root:mooncen-backup:640
check_contract /etc/mooncen/backup-known-hosts root:mooncen-backup:640
check_contract /etc/mooncen/backup-manifest-signing-key root:mooncen-backup:640
check_contract /etc/mooncen/backup-manifest-allowed-signers root:root:644
printf 'ok'
'@
    $contractScript = $contractScript.Replace('__RECIPIENT__', $Recipient)
    $encodedScript = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($contractScript))
    $command = "printf '%s' '$encodedScript' | base64 -d | bash"
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $result = & ssh @args "$($Config.User)@$($Config.Server)" $command 2>$null
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    return $exitCode -eq 0 -and "$result".Trim() -eq "ok"
}

if ($standbyDeploy -and ($needsStandbySecrets -or $Action -eq "standby-secrets-check")) {
    if (-not $dbPassword) {
        $dbPassword = Get-RemoteEnvValueFromHost $activeConfig.Server $activeConfig.User (Expand-ConfigPath $activeConfig.IdentityFile) $activeConfig.RemoteDir "DB_PASSWORD"
    }
    if (-not $authSecret) {
        $authSecret = Get-RemoteEnvValueFromHost $activeConfig.Server $activeConfig.User (Expand-ConfigPath $activeConfig.IdentityFile) $activeConfig.RemoteDir "AUTH_SECRET"
    }
    if (-not $opsLoginId) {
        $opsLoginId = Get-RemoteEnvValueFromHost $activeConfig.Server $activeConfig.User (Expand-ConfigPath $activeConfig.IdentityFile) $activeConfig.RemoteDir "MOONCEN_OPS_LOGIN_ID"
    }
    if (-not $opsPasswordHash) {
        $opsPasswordHash = Get-RemoteEnvValueFromHost $activeConfig.Server $activeConfig.User (Expand-ConfigPath $activeConfig.IdentityFile) $activeConfig.RemoteDir "MOONCEN_OPS_PASSWORD_HASH"
    }
    if (-not $dbApplierPassword) {
        $dbApplierPassword = Get-RemoteEnvValueFromHost $activeConfig.Server $activeConfig.User (Expand-ConfigPath $activeConfig.IdentityFile) $activeConfig.RemoteDir "PRIMARY_DB_PASSWORD"
    }
    if (-not $dbApiPassword) {
        $dbApiPassword = Get-RemoteEnvValueFromHost $activeConfig.Server $activeConfig.User (Expand-ConfigPath $activeConfig.IdentityFile) $activeConfig.RemoteDir "DB_API_PASSWORD"
    }
    if (-not $dbCrawlerPassword) {
        $dbCrawlerPassword = Get-RemoteEnvValueFromHost $activeConfig.Server $activeConfig.User (Expand-ConfigPath $activeConfig.IdentityFile) $activeConfig.RemoteDir "DB_CRAWLER_PASSWORD"
    }
    if (-not $dbAiPassword) {
        $dbAiPassword = Get-RemoteEnvValueFromHost $activeConfig.Server $activeConfig.User (Expand-ConfigPath $activeConfig.IdentityFile) $activeConfig.RemoteDir "DB_AI_PASSWORD"
    }
    if (-not $dbBackupPassword) {
        $dbBackupPassword = Get-RemoteEnvValueFromHost $activeConfig.Server $activeConfig.User (Expand-ConfigPath $activeConfig.IdentityFile) $activeConfig.RemoteDir "DB_BACKUP_PASSWORD"
    }
    if (-not $dbCheckPassword) {
        $dbCheckPassword = Get-RemoteEnvValueFromHost $activeConfig.Server $activeConfig.User (Expand-ConfigPath $activeConfig.IdentityFile) $activeConfig.RemoteDir "DB_CHECK_PASSWORD"
    }
    if (-not $backupAgeRecipient) {
        $backupAgeRecipient = Get-RemoteEnvValueFromHost $activeConfig.Server $activeConfig.User (Expand-ConfigPath $activeConfig.IdentityFile) $activeConfig.RemoteDir "BACKUP_AGE_RECIPIENT"
    }
    if ($Action -eq "standby-secrets-check") {
        Write-Host ("Active target: {0} {1}@{2} {3}" -f $activeConfig.Name, $activeConfig.User, $activeConfig.Server, $activeConfig.RemoteDir)
        Write-Host ("Active identity exists: {0}" -f (Test-ReadableConfigPath (Expand-ConfigPath $activeConfig.IdentityFile)))
        Write-Host ("DB_PASSWORD: {0}" -f $(if ($dbPassword) { "found" } else { "missing" }))
        Write-Host ("AUTH_SECRET: {0}" -f $(if ($authSecret) { "found" } else { "missing" }))
        Write-Host ("Ops login configuration: {0}" -f $(if ($opsLoginId -and $opsPasswordHash) { "found" } else { "missing" }))
        Write-Host ("DB_API_PASSWORD: {0}" -f $(if ($dbApiPassword) { "found" } else { "missing" }))
        Write-Host ("DB_CRAWLER_PASSWORD: {0}" -f $(if ($dbCrawlerPassword) { "found" } else { "missing" }))
        Write-Host ("DB_AI_PASSWORD: {0}" -f $(if ($dbAiPassword) { "found" } else { "missing" }))
        Write-Host ("PRIMARY_DB_PASSWORD: {0}" -f $(if ($dbApplierPassword) { "found" } else { "missing" }))
        Write-Host ("DB_BACKUP_PASSWORD: {0}" -f $(if ($dbBackupPassword) { "found" } else { "missing" }))
        Write-Host ("DB_CHECK_PASSWORD: {0}" -f $(if ($dbCheckPassword) { "found" } else { "missing" }))
    } elseif (
        -not $dbPassword -or -not $authSecret -or -not $opsLoginId -or -not $opsPasswordHash -or -not $dbApiPassword -or
        -not $dbCrawlerPassword -or -not $dbAiPassword -or -not $dbApplierPassword -or
        -not $dbBackupPassword -or -not $dbCheckPassword
    ) {
        Write-Host ("Active target: {0} {1}@{2} {3}" -f $activeConfig.Name, $activeConfig.User, $activeConfig.Server, $activeConfig.RemoteDir)
        Write-Host ("Active identity exists: {0}" -f (Test-ReadableConfigPath (Expand-ConfigPath $activeConfig.IdentityFile)))
        Write-Host ("DB_PASSWORD: {0}" -f $(if ($dbPassword) { "found" } else { "missing" }))
        Write-Host ("AUTH_SECRET: {0}" -f $(if ($authSecret) { "found" } else { "missing" }))
        Write-Host ("Ops login configuration: {0}" -f $(if ($opsLoginId -and $opsPasswordHash) { "found" } else { "missing" }))
        Write-Host ("DB_API_PASSWORD: {0}" -f $(if ($dbApiPassword) { "found" } else { "missing" }))
        Write-Host ("DB_CRAWLER_PASSWORD: {0}" -f $(if ($dbCrawlerPassword) { "found" } else { "missing" }))
        Write-Host ("DB_AI_PASSWORD: {0}" -f $(if ($dbAiPassword) { "found" } else { "missing" }))
        Write-Host ("PRIMARY_DB_PASSWORD: {0}" -f $(if ($dbApplierPassword) { "found" } else { "missing" }))
        Write-Host ("DB_BACKUP_PASSWORD: {0}" -f $(if ($dbBackupPassword) { "found" } else { "missing" }))
        Write-Host ("DB_CHECK_PASSWORD: {0}" -f $(if ($dbCheckPassword) { "found" } else { "missing" }))
        throw "Standby deploy requires owner, auth, Ops login, and all runtime DB-role secrets from active target '$($activeConfig.Name)'. Deploy the primary first, set the corresponding MoonCenDb*/MoonCenAuthSecret/MoonCenOps* values, or keep the protected remote secret stores readable by SSH."
    }
    if (-not $cloudflaredToken) {
        $cloudflaredToken = Get-RemoteCloudflaredTokenFromHost $activeConfig.Server $activeConfig.User (Expand-ConfigPath $activeConfig.IdentityFile)
        if ($cloudflaredToken) {
            Write-Host "Using Cloudflared tunnel token from active target '$($activeConfig.Name)' for standby deploy."
        } else {
            Write-Host "Cloudflared tunnel token was not found on active target '$($activeConfig.Name)'. Standby tunnel token sync skipped."
        }
    }
}

function Invoke-RemoteMoonCen {
    param([string]$Command)
    ssh @sshArgs $remote $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Remote command failed: $(Mask-SensitiveText $Command)"
    }
}

function Invoke-RemoteMoonCenWithInput {
    param(
        [string]$Command,
        [string]$InputText
    )

    $InputText | & ssh @sshArgs $remote $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Remote command with protected stdin failed: $(Mask-SensitiveText $Command)"
    }
}

function Invoke-RemoteBashScript {
    param([string]$Script)

    $encodedScript = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($Script))
    $command = "printf '%s' $encodedScript | base64 -d | bash"
    ssh @sshArgs $remote $command
    if ($LASTEXITCODE -ne 0) {
        throw "Remote bash script failed."
    }
}

function Get-SshArgsForDeployServer {
    param([object]$Config)

    $args = @()
    $configIdentityFile = Expand-ConfigPath $Config.IdentityFile
    if ($configIdentityFile -and (Test-ReadableConfigPath $configIdentityFile)) {
        $args += @("-i", (Resolve-Path $configIdentityFile).Path, "-o", "IdentitiesOnly=yes")
    }
    $args += @(
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "UpdateHostKeys=no",
        "-o", "PreferredAuthentications=publickey",
        "-o", "PasswordAuthentication=no",
        "-o", "KbdInteractiveAuthentication=no",
        "-o", "NumberOfPasswordPrompts=0",
        "-o", "ConnectTimeout=10",
        "-o", "ConnectionAttempts=1"
    )
    return $args
}

function Invoke-RemoteForDeployServer {
    param(
        [object]$Config,
        [string]$Command
    )

    $configSshArgs = Get-SshArgsForDeployServer $Config
    $configRemote = "$($Config.User)@$($Config.Server)"
    ssh @configSshArgs $configRemote $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Remote command failed on '$($Config.Name)': $(Mask-SensitiveText $Command)"
    }
}

function Invoke-RemoteBashScriptForDeployServer {
    param(
        [object]$Config,
        [string]$Script
    )

    $configSshArgs = Get-SshArgsForDeployServer $Config
    $configRemote = "$($Config.User)@$($Config.Server)"
    $encodedScript = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($Script))
    $command = "printf '%s' $encodedScript | base64 -d | bash"
    ssh @configSshArgs $configRemote $command
    if ($LASTEXITCODE -ne 0) {
        throw "Remote bash script failed on '$($Config.Name)'."
    }
}

function Invoke-RemoteSudoBashScriptForDeployServer {
    param(
        [object]$Config,
        [string]$Script
    )

    $configSshArgs = Get-SshArgsForDeployServer $Config
    $configRemote = "$($Config.User)@$($Config.Server)"
    $encodedScript = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($Script))
    $command = "printf '%s' $encodedScript | base64 -d | sudo -n bash"
    ssh @configSshArgs $configRemote $command
    if ($LASTEXITCODE -ne 0) {
        throw "Remote privileged bash script failed on '$($Config.Name)'."
    }
}

function Disable-StandbyCloudflaredForActiveTarget {
    param([string]$ActiveTargetName)

    foreach ($item in $registryInfo.Servers.Values) {
        if ($item.Name -eq $ActiveTargetName) {
            continue
        }
        if ($item.Active -or $item.Role -eq "primary") {
            continue
        }
        if ($item.DeployProfile -ne "full-stack") {
            continue
        }
        Write-Host "Disabling standby cloudflared on $($item.Name)..."
        Invoke-RemoteForDeployServer $item "printf 'standby\n' | sudo -n tee /etc/mooncen-node-role >/dev/null; sudo -n rm -f /opt/mooncen/failover/enable_auto_failover || true; sudo -n systemctl disable --now cloudflared.service; sudo -n systemctl reset-failed cloudflared.service || true; printf '$($item.Name) node_role='; cat /etc/mooncen-node-role 2>/dev/null || true; printf '$($item.Name) cloudflared active='; systemctl is-active cloudflared.service || true; printf '$($item.Name) cloudflared enabled='; systemctl is-enabled cloudflared.service || true"
    }
}

function Invoke-CurrentCrawlerActivation {
    if ($crawlerMode -ne "legacy") {
        throw "crawler-activate is only valid while the reviewed crawler mode is legacy."
    }
    if ($targetConfig.Name -ne $crawlerTarget) {
        throw "crawler-activate requires the reviewed crawler owner '-Target $crawlerTarget'."
    }
    if (
        $targetConfig.Role -ne "crawler" -or
        $targetConfig.DeployProfile -ne "crawler-only" -or
        $targetConfig.RemoteDir -ne "/opt/mooncen" -or
        $targetConfig.Active
    ) {
        throw "The reviewed crawler owner must be a dedicated crawler-only registry target."
    }
    if ($BatchId -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$') {
        throw "crawler-activate requires a reviewed -BatchId containing only letters, digits, dot, underscore, or hyphen."
    }

    $preflight = @'
set -euo pipefail
if [ "$(hostname -s 2>/dev/null || true)" != "gen1crawler" ] || \
   [ "$(cat /etc/mooncen-node-role 2>/dev/null || true)" != "crawler" ]; then
  echo "crawler activation target is not the reviewed gen1crawler owner" >&2
  exit 78
fi
if [ ! -f /opt/mooncen/deploy/ubuntu/activate_split_crawler.sh ] || \
   [ -L /opt/mooncen/deploy/ubuntu/activate_split_crawler.sh ] || \
   [ ! -x /opt/mooncen/.venv/bin/python ]; then
  echo "reviewed split crawler runtime is not installed" >&2
  exit 66
fi
for timer in mooncen-crawler.timer mooncen-staging-apply.timer; do
  ! systemctl is-enabled --quiet "$timer"
  ! systemctl is-active --quiet "$timer"
done
for service in mooncen-crawler.service mooncen-crawler-once.service mooncen-staging-apply.service; do
  ! systemctl is-active --quiet "$service"
done
'@
    Invoke-RemoteForDeployServer $targetConfig "sudo -n true"
    Invoke-RemoteBashScriptForDeployServer $targetConfig $preflight
    Invoke-RemoteForDeployServer $targetConfig (
        "sudo -n bash /opt/mooncen/deploy/ubuntu/activate_split_crawler.sh --batch-id $BatchId"
    )
}

function Assert-CrawlerControlBackupAttestationReady {
    # The Linux consumer is implemented by
    # future fixed root helper must invoke an absolute, digest-verified
    # attestation verifier using the fixed root-owned JSON/key paths and a
    # 24-hour maximum age. The real
    # gen1db evidence has not been issued or authenticated from this deploy
    # path, so this outer gate remains local and precedes every SSH connection
    # and filesystem/database mutation.
    throw "NOT READY: crawler-control-install requires fresh real-gen1db mooncen_staging backup/restore evidence at /etc/mooncen/crawler-control-backup-attestation.json and its protected key. No SSH connection, release activation, or database mutation was attempted."
}

function Invoke-CrawlerControlInstall {
    if (
        $targetConfig.Name -ne $crawlerControlTarget -or
        $targetConfig.Name -ne "gen1db" -or
        $targetConfig.Server -ne "gen1db" -or
        $targetConfig.Role -ne "crawler-control" -or
        $targetConfig.DeployProfile -ne "control-only" -or
        $targetConfig.RemoteDir -ne "/opt/mooncen" -or
        $targetConfig.Active
    ) {
        throw "crawler-control-install is pinned to the reviewed inactive gen1db crawler-control/control-only target."
    }
    if (-not $ExpectedCommit -or -not $ExpectedArchiveSha256 -or -not $ExpectedReleaseTreeSha256 -or -not $ReleaseSignaturePath) {
        throw "crawler-control-install requires ExpectedCommit, ExpectedArchiveSha256, ExpectedReleaseTreeSha256, and a detached OpenSSH ReleaseSignaturePath."
    }
    if ($SourceCommit -or $ExpectedSourceTree) {
        throw "crawler-control-install packages only the exact reviewed Git HEAD; development snapshots are forbidden."
    }
    Assert-ExpectedDeployCommit $ExpectedCommit

    # Backup recovery evidence must exist before even the signed source-only
    # release is uploaded.  This call intentionally throws today.
    Assert-CrawlerControlBackupAttestationReady

    # Future continuation after the attestation implementation is reviewed:
    # the dedicated transport rebuilds the clean commit, verifies archive and
    # canonical tree digests, requires a detached signature accepted by the
    # root-owned allowed-signers policy, creates a hash-locked runtime, atomically
    # activates it with rollback, and returns an independent remote proof.
        $releaseScript = Join-Path $PSScriptRoot "deploy/ubuntu/deploy_crawler_control_from_windows.ps1"
    $releaseProof = & $releaseScript `
        -Server $targetConfig.Server `
        -User $targetConfig.User `
        -IdentityFile $targetConfig.IdentityFile `
        -ExpectedCommit $ExpectedCommit `
        -ExpectedArchiveSha256 $ExpectedArchiveSha256 `
        -ExpectedTreeSha256 $ExpectedReleaseTreeSha256 `
        -ReleaseSignaturePath $ReleaseSignaturePath `
        -TargetName $targetConfig.Name `
        -RemoteDir $targetConfig.RemoteDir `
        -NodeRole $targetConfig.Role
    if ($LASTEXITCODE -ne 0 -or ($releaseProof -join "`n") -notmatch '^crawler-control-release-verified:') {
        throw "Crawler-control release did not return the exact local-and-remote provenance proof."
    }

    # The direct DB/systemd installer remains independently blocked until it
    # consumes and re-verifies the backup attestation interface above.
    throw "NOT READY: signed control release is verified, but database/control-plane installation remains blocked until setup_distributed_crawler_control.sh consumes the reviewed backup attestation. No database mutation was attempted."
}

function Invoke-HaStatus {
    $remoteCommand = @'
set +e
printf 'host '; hostname 2>/dev/null || echo unknown
printf 'node_role '
cat /etc/mooncen-node-role 2>/dev/null || echo unknown
printf 'postgres '
systemctl is-active postgresql 2>/dev/null || true
printf 'db_role '
sudo -n -u postgres psql -Atqc "SELECT CASE WHEN pg_is_in_recovery() THEN 'standby' ELSE 'primary' END;" 2>/dev/null || true
printf 'wal_sender_count '
sudo -n -u postgres psql -Atqc "SELECT count(*) FROM pg_stat_replication;" 2>/dev/null || true
printf 'wal_receiver '
sudo -n -u postgres psql -Atqc "SELECT status || '|' || coalesce(sender_host,'') || '|' || coalesce(slot_name,'') FROM pg_stat_wal_receiver;" 2>/dev/null || true
printf 'replay_delay_seconds '
sudo -n -u postgres psql -Atqc "SELECT COALESCE(EXTRACT(EPOCH FROM now() - pg_last_xact_replay_timestamp())::int::text, '');" 2>/dev/null || true
printf 'api '
systemctl is-active mooncen-api 2>/dev/null || true
printf 'frontend '
systemctl is-active mooncen-frontend 2>/dev/null || true
printf 'nginx '
systemctl is-active nginx 2>/dev/null || true
printf 'ai '
systemctl is-active mooncen-ai-worker 2>/dev/null || true
printf 'cloudflared '
systemctl is-active cloudflared 2>/dev/null || true
printf 'cloudflared_enabled '
systemctl is-enabled cloudflared 2>/dev/null || true
printf 'role_guard '
systemctl is-active mooncen-cloudflared-role-guard.timer 2>/dev/null || true
printf 'bot '
systemctl is-active mooncen-ops-bot 2>/dev/null || true
printf 'bot_enabled '
systemctl is-enabled mooncen-ops-bot 2>/dev/null || true
printf 'auto_failover_file '
test -f /opt/mooncen/failover/enable_auto_failover && echo present || echo absent
printf 'cloudflare_gate_disabled '
test -f /opt/mooncen/failover/disable_cloudflare_gate && echo present || echo absent
printf 'local_api_health '
curl -fsS -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8001/health 2>/dev/null || true
printf '\n'
'@
    $crawlerRemoteCommand = @'
set +e
printf 'crawler_timer '
systemctl is-active mooncen-crawler.timer 2>/dev/null || true
printf 'crawler_timer_enabled '
systemctl is-enabled mooncen-crawler.timer 2>/dev/null || true
printf 'crawler_run '
systemctl is-active mooncen-crawler-once.service 2>/dev/null || true
printf 'crawler_last_result '
systemctl show mooncen-crawler-once.service -p Result --value 2>/dev/null || true
printf 'crawler_next_run '
systemctl show mooncen-crawler.timer -p NextElapseUSecRealtime --value 2>/dev/null || true
printf 'crawler_env '
sudo -n grep -E '^DB_HOST=|^DB_PORT=|^CRAWL_WRITE_MODE=' /etc/mooncen/crawler.env 2>/dev/null | paste -sd ' ' - || true
printf 'staging_timer '
systemctl is-active mooncen-staging-apply.timer 2>/dev/null || true
'@
    foreach ($item in ($registryInfo.Servers.Values | Sort-Object @{ Expression = { if ($_.Active -or $_.Role -eq "primary") { 0 } else { 1 } } }, Name)) {
        Write-Host ""
        Write-Host ("== {0} ({1}@{2}, role={3}, active={4}) ==" -f $item.Name, $item.User, $item.Server, $item.Role, $item.Active)
        Invoke-RemoteBashScriptForDeployServer $item $remoteCommand
        if ($item.DeployProfile -eq "crawler-only") {
            Invoke-RemoteBashScriptForDeployServer $item $crawlerRemoteCommand
        }
    }
    Write-Host ""
    Write-Host "== Public endpoints =="
    foreach ($url in @("https://mooncen.kr/health", "https://www.mooncen.kr/health")) {
        try {
            $response = Invoke-WebRequest -Uri $url -UseBasicParsing -Method Get -TimeoutSec 8
            Write-Host ("{0} {1}" -f $url, [int]$response.StatusCode)
        } catch {
            Write-Host ("{0} CHECK {1}" -f $url, $_.Exception.Message)
        }
    }
}

switch ($Action) {
    "preflight" {
        Test-DeployPreflight
    }
    "ha-status" {
        Invoke-HaStatus
    }
    "targets" {
        Write-Host "Server registry: $($registryInfo.Source)"
        Write-Host "Default target: $($registryInfo.DefaultTarget)"
        foreach ($item in $registryInfo.Servers.Values) {
            $activeText = if ($item.Active) {
                "active"
            } elseif ($item.DeployProfile -in @("crawler-only", "control-only")) {
                "dedicated"
            } else {
                "standby"
            }
            $opsText = if ($item.OpsConsole) { "ops" } else { "-" }
            Write-Host ("{0,-16} {1,-8} {2,-12} {3,-8} {4,-4} {5}@{6} {7}" -f $item.Name, $item.Role, $item.DeployProfile, $activeText, $opsText, $item.User, $item.Server, $item.RemoteDir)
        }
    }
    "standby-secrets-check" {
        Write-Host ("Active target: {0} {1}@{2} {3}" -f $activeConfig.Name, $activeConfig.User, $activeConfig.Server, $activeConfig.RemoteDir)
        Write-Host ("Target: {0} {1}@{2} {3}" -f $targetConfig.Name, $targetConfig.User, $targetConfig.Server, $targetConfig.RemoteDir)
        Write-Host ("DB_PASSWORD: {0}" -f $(if ($dbPassword) { "found" } else { "missing" }))
        Write-Host ("AUTH_SECRET: {0}" -f $(if ($authSecret) { "found" } else { "missing" }))
        Write-Host ("DB_API_PASSWORD: {0}" -f $(if ($dbApiPassword) { "found" } else { "missing" }))
        Write-Host ("DB_CRAWLER_PASSWORD: {0}" -f $(if ($dbCrawlerPassword) { "found" } else { "missing" }))
        Write-Host ("DB_AI_PASSWORD: {0}" -f $(if ($dbAiPassword) { "found" } else { "missing" }))
        Write-Host ("PRIMARY_DB_PASSWORD: {0}" -f $(if ($dbApplierPassword) { "found" } else { "missing" }))
        Write-Host ("DB_BACKUP_PASSWORD: {0}" -f $(if ($dbBackupPassword) { "found" } else { "missing" }))
        Write-Host ("DB_CHECK_PASSWORD: {0}" -f $(if ($dbCheckPassword) { "found" } else { "missing" }))
    }
    "deploy-all" {
        if ($ExpectedTargetIdentity) {
            throw "ExpectedTargetIdentity is only valid for a single explicit deployment target"
        }
        $orderedTargets = @(
            $registryInfo.Servers.Values |
                Where-Object { $_.DeployProfile -eq "full-stack" } |
                Sort-Object @{ Expression = { if ($_.Active -or $_.Role -eq "primary") { 0 } else { 1 } } }, Name
        )
        foreach ($item in $orderedTargets) {
            Write-Host "== Deploying target: $($item.Name) =="
            $childArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $PSCommandPath, "deploy", "-Target", $item.Name)
            if ($ExpectedCommit) {
                $childArgs += @("-ExpectedCommit", $ExpectedCommit)
            }
            if ($SkipWorkers) {
                $childArgs += "-SkipWorkers"
            }
            if ($ActiveCloud) {
                $childArgs += "-ActiveCloud"
            }
            if ($AllowCrawlerInterruption) {
                $childArgs += "-AllowCrawlerInterruption"
            }
            & powershell @childArgs
            if ($LASTEXITCODE -ne 0) {
                exit $LASTEXITCODE
            }
        }
    }
    "full-deploy-all" {
        if ($ExpectedTargetIdentity) {
            throw "ExpectedTargetIdentity is only valid for a single explicit deployment target"
        }
        $orderedTargets = @(
            $registryInfo.Servers.Values |
                Where-Object { $_.DeployProfile -eq "full-stack" } |
                Sort-Object @{ Expression = { if ($_.Active -or $_.Role -eq "primary") { 0 } else { 1 } } }, Name
        )
        foreach ($item in $orderedTargets) {
            Write-Host "== Full deploying target: $($item.Name) =="
            $childArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $PSCommandPath, "full-deploy", "-Target", $item.Name)
            if ($ExpectedCommit) {
                $childArgs += @("-ExpectedCommit", $ExpectedCommit)
            }
            if ($SkipWorkers) {
                $childArgs += "-SkipWorkers"
            }
            if ($ActiveCloud) {
                $childArgs += "-ActiveCloud"
            }
            if ($AllowCrawlerInterruption) {
                $childArgs += "-AllowCrawlerInterruption"
            }
            & powershell @childArgs
            if ($LASTEXITCODE -ne 0) {
                exit $LASTEXITCODE
            }
        }
    }
    "deploy" {
        Assert-ExpectedDeployCommit $ExpectedCommit
        $script = Join-Path $PSScriptRoot "deploy_ubuntu.ps1"
        & $script `
            -Server $server `
            -User $user `
            -IdentityFile $identityFile `
            -RemoteDir $remoteDir `
            -Domain $domain `
            -DbPassword $dbPassword `
            -DbApiPassword $dbApiPassword `
            -DbCrawlerPassword $dbCrawlerPassword `
            -DbAiPassword $dbAiPassword `
            -DbApplierPassword $dbApplierPassword `
            -DbBackupPassword $dbBackupPassword `
            -DbCheckPassword $dbCheckPassword `
            -AuthSecret $authSecret `
            -OpsLoginId $opsLoginId `
            -OpsPasswordHash $opsPasswordHash `
            -DbSslRootCert $dbSslRootCert `
            -BackupAgeRecipient $backupAgeRecipient `
            -BackupPort $backupPort `
            -KakaoMapsJavascriptKey $kakaoMapsJavascriptKey `
            -KakaoMapsRestApiKey $kakaoMapsRestApiKey `
            -GoogleOAuthClientId $googleOAuthClientId `
            -GoogleOAuthClientSecret $googleOAuthClientSecret `
            -NaverOAuthClientId $naverOAuthClientId `
            -NaverOAuthClientSecret $naverOAuthClientSecret `
            -CloudflaredToken $cloudflaredToken `
            -OllamaHost $ollamaHost `
            -OllamaHosts $ollamaHosts `
            -OllamaModel $ollamaModel `
            -BotToken $botToken `
            -BotChatId $botChatId `
            -AdminEmails $adminEmails `
            -AdminProviderIds $adminProviderIds `
            -BugReportTo $bugReportTo `
            -BugReportFrom $bugReportFrom `
            -SmtpHost $smtpHost `
            -SmtpPort $smtpPort `
            -SmtpUsername $smtpUsername `
            -SmtpPassword $smtpPassword `
            -SmtpSecurity $smtpSecurity `
            -OpsCloudflareAnalyticsZoneId $opsCloudflareAnalyticsZoneId `
            -OpsCloudflareAnalyticsToken $opsCloudflareAnalyticsToken `
            -ServerMonitorToken $serverMonitorToken `
            -ExpectedCommit $ExpectedCommit `
            -DeploymentIntentToken $DeploymentIntentToken `
            -SourceCommit $SourceCommit `
            -ExpectedSourceTree $ExpectedSourceTree `
            -NodeRole $targetConfig.Role `
            -CrawlerMode $crawlerMode `
            -EnableCrawler:$enableCrawler `
            -InstallOpsConsole:$installOpsConsole `
            -SkipSystemPackages:$skipSystemPackages `
            -SkipWorkers:$SkipWorkers `
            -UseScpFallback:$useScpFallback `
            -AllowCrawlerInterruption:$AllowCrawlerInterruption `
            -Standby:$standbyDeploy
        exit $LASTEXITCODE
    }
    "full-deploy" {
        Assert-ExpectedDeployCommit $ExpectedCommit
        $script = Join-Path $PSScriptRoot "deploy_ubuntu.ps1"
        & $script `
            -Server $server `
            -User $user `
            -IdentityFile $identityFile `
            -RemoteDir $remoteDir `
            -Domain $domain `
            -DbPassword $dbPassword `
            -DbApiPassword $dbApiPassword `
            -DbCrawlerPassword $dbCrawlerPassword `
            -DbAiPassword $dbAiPassword `
            -DbApplierPassword $dbApplierPassword `
            -DbBackupPassword $dbBackupPassword `
            -DbCheckPassword $dbCheckPassword `
            -AuthSecret $authSecret `
            -OpsLoginId $opsLoginId `
            -OpsPasswordHash $opsPasswordHash `
            -DbSslRootCert $dbSslRootCert `
            -BackupAgeRecipient $backupAgeRecipient `
            -BackupPort $backupPort `
            -KakaoMapsJavascriptKey $kakaoMapsJavascriptKey `
            -KakaoMapsRestApiKey $kakaoMapsRestApiKey `
            -GoogleOAuthClientId $googleOAuthClientId `
            -GoogleOAuthClientSecret $googleOAuthClientSecret `
            -NaverOAuthClientId $naverOAuthClientId `
            -NaverOAuthClientSecret $naverOAuthClientSecret `
            -CloudflaredToken $cloudflaredToken `
            -OllamaHost $ollamaHost `
            -OllamaHosts $ollamaHosts `
            -OllamaModel $ollamaModel `
            -BotToken $botToken `
            -BotChatId $botChatId `
            -AdminEmails $adminEmails `
            -AdminProviderIds $adminProviderIds `
            -BugReportTo $bugReportTo `
            -BugReportFrom $bugReportFrom `
            -SmtpHost $smtpHost `
            -SmtpPort $smtpPort `
            -SmtpUsername $smtpUsername `
            -SmtpPassword $smtpPassword `
            -SmtpSecurity $smtpSecurity `
            -OpsCloudflareAnalyticsZoneId $opsCloudflareAnalyticsZoneId `
            -OpsCloudflareAnalyticsToken $opsCloudflareAnalyticsToken `
            -ServerMonitorToken $serverMonitorToken `
            -ExpectedCommit $ExpectedCommit `
            -DeploymentIntentToken $DeploymentIntentToken `
            -SourceCommit $SourceCommit `
            -ExpectedSourceTree $ExpectedSourceTree `
            -NodeRole $targetConfig.Role `
            -CrawlerMode $crawlerMode `
            -EnableCrawler:$enableCrawler `
            -InstallOpsConsole:$installOpsConsole `
            -SkipWorkers:$SkipWorkers `
            -UseScpFallback:$useScpFallback `
            -AllowCrawlerInterruption:$AllowCrawlerInterruption `
            -Standby:$standbyDeploy
        exit $LASTEXITCODE
    }
    "summary" {
        Invoke-RemoteMoonCen "mooncenctl summary"
    }
    "status" {
        Invoke-RemoteMoonCen "mooncenctl status"
    }
    "restart" {
        Invoke-RemoteMoonCen "mooncenctl restart"
    }
    "health" {
        Invoke-RemoteMoonCen "mooncenctl health"
    }
    "doctor" {
        Invoke-RemoteMoonCen "mooncenctl doctor"
    }
    "coordinates" {
        if ($targetConfig.Name -ne $crawlerTarget -or $targetConfig.DeployProfile -ne "crawler-only") {
            throw "coordinates must target the reviewed crawler owner '$crawlerTarget'."
        }
        Invoke-RemoteMoonCen "mooncenctl coordinates"
    }
    "locations" {
        if ($targetConfig.Name -ne $crawlerTarget -or $targetConfig.DeployProfile -ne "crawler-only") {
            throw "locations must target the reviewed crawler owner '$crawlerTarget'."
        }
        Invoke-RemoteMoonCen "mooncenctl locations"
    }
    "crawler-once" {
        if ($targetConfig.Name -ne $crawlerTarget -or $targetConfig.DeployProfile -ne "crawler-only") {
            throw "crawler-once must target the reviewed crawler owner '$crawlerTarget'."
        }
        Invoke-RemoteMoonCen "mooncenctl crawler-once"
    }
    "crawler-update" {
        if ($targetConfig.Name -ne $crawlerTarget -or $targetConfig.DeployProfile -ne "crawler-only") {
            throw "crawler-update requires the reviewed crawler owner '-Target $crawlerTarget'."
        }
        throw "crawler-update is unavailable: no transactional, provenance-verified gen1crawler release uploader is implemented. No remote change was attempted."
    }
    "crawler-activate" {
        Invoke-CurrentCrawlerActivation
    }
    "crawler-control-install" {
        Invoke-CrawlerControlInstall
    }
    "ai-reset-start" {
        Invoke-RemoteMoonCen "mooncenctl ai-reset-start"
    }
    "ai-reset-full-start" {
        Invoke-RemoteMoonCen "mooncenctl ai-reset-full-start"
    }
    "ai-quality" {
        Invoke-RemoteMoonCen "mooncenctl ai-quality"
    }
    "functional-test" {
        Invoke-RemoteMoonCen "mooncenctl functional-test"
    }
    "functional-test-status" {
        Invoke-RemoteMoonCen "mooncenctl functional-test-status"
    }
    "frontend-diagnose" {
        $remoteCommand = @'
set +e
cd /opt/mooncen/frontend2 2>/dev/null || { echo "frontend_dir missing"; exit 0; }
printf 'pwd '; pwd
printf 'node '; node -v 2>/dev/null || true
printf 'npm '; npm -v 2>/dev/null || true
printf 'dist_index '; test -f dist/index.html && echo yes || echo no
printf 'node_modules '; test -d node_modules && echo yes || echo no
printf 'package_preview_script '
node -e "const p=require('./package.json'); console.log((p.scripts&&p.scripts.preview)||'missing')" 2>/dev/null || true
printf 'service_active '
systemctl is-active mooncen-frontend.service 2>/dev/null || true
printf 'port_5173 '
ss -lntp 2>/dev/null | grep -E ':5173\b' || echo none
printf '\nrecent frontend log:\n'
journalctl -u mooncen-frontend.service -n 80 --no-pager 2>/dev/null || true
'@
        Invoke-RemoteBashScript $remoteCommand
    }
    "frontend-fix-permissions" {
        $remoteCommand = @'
set -e
sudo -n systemctl stop mooncen-frontend.service || true
sudo -n chown -R mooncen:mooncen /opt/mooncen/frontend2 /opt/mooncen/.env
sudo -n find /opt/mooncen/frontend2 -maxdepth 1 -name 'vite.config.ts.timestamp-*.mjs' -delete
sudo -n systemctl reset-failed mooncen-frontend.service || true
sudo -n systemctl start mooncen-frontend.service
sleep 3
printf 'frontend_active '
systemctl is-active mooncen-frontend.service || true
printf 'port_5173 '
ss -lntp 2>/dev/null | grep -E ':5173\b' || echo none
printf '\nfrontend direct:\n'
curl -fsSI http://127.0.0.1:5173 | head -n 5 || true
printf '\nfrontend nginx:\n'
curl -fsSI http://localhost/ | head -n 5 || true
printf '\nrecent frontend log:\n'
journalctl -u mooncen-frontend.service -n 40 --no-pager 2>/dev/null || true
'@
        Invoke-RemoteBashScript $remoteCommand
    }
    "frontend-rebuild" {
        $remoteCommand = @'
set -e
cd /opt/mooncen/frontend2
sudo -n chown -R mooncen:mooncen /opt/mooncen/frontend2
sudo -n -u mooncen bash -lc 'cd /opt/mooncen/frontend2 && npm ci && npm run build'
sudo -n systemctl reset-failed mooncen-frontend.service || true
sudo -n systemctl restart mooncen-frontend.service
sleep 3
systemctl --no-pager --full status mooncen-frontend.service | sed -n '1,35p' || true
printf '\nhealth:\n'
curl -fsSI http://127.0.0.1:5173 | head -n 5 || true
curl -fsSI http://localhost/ | head -n 5 || true
'@
        Invoke-RemoteBashScript $remoteCommand
    }
    "cloudflare-gate-status" {
        $remoteCommand = @'
set +e
printf 'disable_file '
test -f /opt/mooncen/failover/disable_cloudflare_gate && echo present || echo absent
printf 'gate_timer '
systemctl is-active mooncen-cloudflare-gate.timer 2>/dev/null || true
printf 'gate_service '
systemctl is-active mooncen-cloudflare-gate.service 2>/dev/null || true
printf 'gate_fail_count '
cat /opt/mooncen/failover/cloudflare_gate_fail_count 2>/dev/null || echo 0
printf 'gate_recover_count '
cat /opt/mooncen/failover/cloudflare_gate_recover_count 2>/dev/null || echo 0
printf '\nrecent gate log:\n'
journalctl -u mooncen-cloudflare-gate.service -n 80 --no-pager 2>/dev/null || true
'@
        Invoke-RemoteBashScript $remoteCommand
    }
    "cloudflare-gate-disable" {
        Invoke-RemoteMoonCen "sudo -n mkdir -p /opt/mooncen/failover; sudo -n touch /opt/mooncen/failover/disable_cloudflare_gate; sudo -n systemctl disable --now mooncen-cloudflare-gate.timer >/dev/null 2>&1 || true; echo cloudflare_gate_disabled"
    }
    "cloudflare-gate-enable" {
        Invoke-RemoteMoonCen "sudo -n rm -f /opt/mooncen/failover/disable_cloudflare_gate; sudo -n rm -f /opt/mooncen/failover/cloudflare_gate_fail_count /opt/mooncen/failover/cloudflare_gate_recover_count 2>/dev/null || true; sudo -n systemctl enable --now mooncen-cloudflare-gate.timer; echo cloudflare_gate_enabled"
    }
    "cloudflared-status" {
        $remoteCommand = @'
set +e
printf 'node_role '
cat /etc/mooncen-node-role 2>/dev/null || echo unknown
printf 'cloudflared_active '
systemctl is-active cloudflared.service 2>/dev/null || true
printf 'cloudflared_enabled '
systemctl is-enabled cloudflared.service 2>/dev/null || true
printf 'cloudflared_main_pid '
systemctl show -p MainPID --value cloudflared.service 2>/dev/null || true
printf 'cloudflared_processes\n'
pgrep -af cloudflared 2>/dev/null | sed -E 's/(--token[[:space:]]+)[^[:space:]]+/\1<redacted>/g; s/(TUNNEL_TOKEN=)[^[:space:]]+/\1<redacted>/g' || echo none
printf 'role_guard_timer '
systemctl is-active mooncen-cloudflared-role-guard.timer 2>/dev/null || true
printf '\nrecent cloudflared log:\n'
journalctl -u cloudflared.service -n 30 --no-pager 2>/dev/null | sed -E 's/(--token[[:space:]]+)[^[:space:]]+/\1<redacted>/g; s/(TUNNEL_TOKEN=)[^[:space:]]+/\1<redacted>/g' || true
printf '\nrecent role guard log:\n'
journalctl -u mooncen-cloudflared-role-guard.service -n 20 --no-pager 2>/dev/null || true
'@
        Invoke-RemoteBashScript $remoteCommand
    }
    "cloudflared-stop" {
        if ($targetConfig.DeployProfile -ne "full-stack") {
            throw "Refusing cloudflared-stop on crawler-only target '$($targetConfig.Name)'."
        }
        $rolePrefix = ""
        if ($targetConfig.Role -ne "primary" -and -not $targetConfig.Active) {
            $rolePrefix = "printf 'standby\n' | sudo -n tee /etc/mooncen-node-role >/dev/null; "
        }
        Invoke-RemoteMoonCen "${rolePrefix}sudo -n systemctl disable --now cloudflared.service; sudo -n systemctl reset-failed cloudflared.service || true; printf 'cloudflared active='; systemctl is-active cloudflared.service || true; printf 'cloudflared enabled='; systemctl is-enabled cloudflared.service || true"
    }
    "cloudflared-start" {
        if ($targetConfig.Role -ne "primary" -and -not $targetConfig.Active) {
            throw "Refusing to start cloudflared on non-primary target '$($targetConfig.Name)'. Start it only after failover promotes that target."
        }
        Invoke-RemoteMoonCen "printf 'primary\n' | sudo -n tee /etc/mooncen-node-role >/dev/null; sudo -n systemctl reset-failed cloudflared.service || true; sudo -n systemctl enable --now cloudflared.service; sleep 2; printf 'node_role='; cat /etc/mooncen-node-role 2>/dev/null || true; printf 'cloudflared active='; systemctl is-active cloudflared.service || true; printf 'cloudflared enabled='; systemctl is-enabled cloudflared.service || true"
        Disable-StandbyCloudflaredForActiveTarget $targetConfig.Name
    }
    "cloudflared-reset" {
        if ($targetConfig.Role -ne "primary" -and -not $targetConfig.Active) {
            throw "Refusing to reset/start cloudflared on non-primary target '$($targetConfig.Name)'."
        }
        $remoteCommand = @'
set -e
printf 'primary\n' | sudo -n tee /etc/mooncen-node-role >/dev/null
sudo -n mkdir -p /opt/mooncen/failover
sudo -n touch /opt/mooncen/failover/disable_cloudflare_gate
sudo -n systemctl disable --now mooncen-cloudflare-gate.timer >/dev/null 2>&1 || true
sudo -n systemctl stop cloudflared.service || true
sudo -n pkill -TERM -x cloudflared 2>/dev/null || true
sleep 2
sudo -n pkill -KILL -x cloudflared 2>/dev/null || true
sudo -n systemctl reset-failed cloudflared.service || true
sudo -n systemctl enable --now cloudflared.service
sleep 5
printf 'node_role '
cat /etc/mooncen-node-role 2>/dev/null || true
printf 'cloudflared_active '
systemctl is-active cloudflared.service || true
printf 'cloudflared_enabled '
systemctl is-enabled cloudflared.service || true
printf 'cloudflare_gate '
systemctl is-active mooncen-cloudflare-gate.timer 2>/dev/null || true
printf 'cloudflared_processes\n'
pgrep -af cloudflared 2>/dev/null | sed -E 's/(--token[[:space:]]+)[^[:space:]]+/\1<redacted>/g; s/(TUNNEL_TOKEN=)[^[:space:]]+/\1<redacted>/g' || echo none
printf '\nrecent cloudflared log:\n'
journalctl -u cloudflared.service -n 30 --no-pager 2>/dev/null | sed -E 's/(--token[[:space:]]+)[^[:space:]]+/\1<redacted>/g; s/(TUNNEL_TOKEN=)[^[:space:]]+/\1<redacted>/g' || true
'@
        Invoke-RemoteBashScript $remoteCommand
        Disable-StandbyCloudflaredForActiveTarget $targetConfig.Name
    }
    "cloudflared-token-id" {
        $remoteCommand = @'
set +e
sudo -n /usr/local/libexec/mooncen-cloudflared-token read | python3 -c '
import base64
import json
import sys

token = sys.stdin.read().strip()
if not token:
    print("token_present false")
    raise SystemExit(0)
try:
    padded = token + ("=" * (-len(token) % 4))
    payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
except Exception as exc:
    print("token_present true")
    print(f"token_decode_error {exc.__class__.__name__}")
    raise SystemExit(0)
print("token_present true")
print("token_tunnel_id " + str(payload.get("t") or ""))
print("token_account_present " + str(bool(payload.get("a"))))
print("token_secret_present " + str(bool(payload.get("s"))))
'
'@
        Invoke-RemoteBashScript $remoteCommand
    }
    "cloudflared-role-guard-status" {
        Invoke-RemoteMoonCen "mooncenctl cloudflared-role-guard-status"
    }
    "cloudflared-role-guard-run" {
        Invoke-RemoteMoonCen "mooncenctl cloudflared-role-guard-run"
    }
    "cloudflared-token-sync" {
        $token = $cloudflaredToken
        if (-not $token) {
            $token = Get-RemoteCloudflaredTokenFromHost $activeConfig.Server $activeConfig.User (Expand-ConfigPath $activeConfig.IdentityFile)
        }
        if (-not $token) {
            throw "Cloudflared token not found. Set MoonCenCloudflaredToken in deploy.local.ps1 or install token on active target '$($activeConfig.Name)'."
        }
        Invoke-RemoteMoonCenWithInput "mooncenctl cloudflared-token" $token
    }
    "bot-status" {
        Invoke-RemoteMoonCen "mooncenctl bot-status"
    }
    "bot-start" {
        Invoke-RemoteMoonCen "mooncenctl bot-start"
    }
    "bot-stop" {
        Invoke-RemoteMoonCen "mooncenctl bot-stop"
    }
    "replica-status" {
        $remoteCommand = @'
set +e
printf 'node_role '
cat /etc/mooncen-node-role 2>/dev/null || echo unknown
printf 'postgres_service '
systemctl is-active postgresql 2>/dev/null || true
printf 'db_role '
sudo -n -u postgres psql -Atqc "SELECT CASE WHEN pg_is_in_recovery() THEN 'standby' ELSE 'primary' END;" 2>/dev/null || true
printf 'wal_receiver '
sudo -n -u postgres psql -Atqc "SELECT status || '|' || coalesce(sender_host,'') || '|' || coalesce(slot_name,'') FROM pg_stat_wal_receiver;" 2>/dev/null || true
printf 'cloudflared '
systemctl is-active cloudflared 2>/dev/null || true
'@
        Invoke-RemoteBashScript $remoteCommand
    }
    "failover-status" {
        $remoteCommand = @'
set +e
printf 'policy cloud-only-manual\n'
printf 'db_role '
sudo -n -u postgres psql -Atqc "SELECT CASE WHEN pg_is_in_recovery() THEN 'standby' ELSE 'primary' END;" 2>/dev/null || true
'@
        Invoke-RemoteBashScript $remoteCommand
    }
    "failover-enable" {
        throw "Automatic failover is disabled by policy. Use manual DB promotion and manual Cloudflare tunnel change during failover."
    }
    "failover-disable" {
        Invoke-RemoteMoonCen "sudo -n rm -f /opt/mooncen/failover/enable_auto_failover && echo disabled"
    }
    "logs" {
        $logService = if ($Service) { $Service } else { "api" }
        Invoke-RemoteMoonCen "mooncenctl logs $logService"
    }
}
