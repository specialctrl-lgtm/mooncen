param(
    [string]$Server = "",
    [string]$SshHost = "",

    [string]$User = "ubuntu",
    [string]$IdentityFile = "",
    [string]$RemoteDir = "/opt/mooncen",
    [string]$Domain = "_",
    [string]$DbPassword = "",
    [string]$DbApiPassword = "",
    [string]$DbCrawlerPassword = "",
    [string]$DbDeploymentWorkerUser = "mooncen_deployment_worker_login",
    [string]$DbDeploymentWorkerPassword = "",
    [string]$DbAiPassword = "",
    [string]$DbApplierPassword = "",
    [string]$DbBackupPassword = "",
    [string]$DbCheckPassword = "",
    [string]$AuthSecret = "",
    [string]$OpsLoginId = "",
    [string]$OpsPasswordHash = "",
    [string]$DbSslRootCert = "",
    [string]$BackupAgeRecipient = "",
    [string]$BackupPort = "",
    [string]$KakaoMapsJavascriptKey = "",
    [string]$KakaoMapsRestApiKey = "",
    [string]$GoogleOAuthClientId = "",
    [string]$GoogleOAuthClientSecret = "",
    [string]$NaverOAuthClientId = "",
    [string]$NaverOAuthClientSecret = "",
    [string]$CloudflaredToken = "",
    [string]$OllamaHost = "http://wtr-linux:11434",
    [string]$OllamaHosts = "",
    [string]$OllamaModel = "qwen3.5:9b",
    [string]$BotToken = "",
    [string]$BotChatId = "",
    [string]$AdminEmails = "",
    [string]$AdminProviderIds = "",
    [string]$BugReportTo = "",
    [string]$BugReportFrom = "",
    [string]$SmtpHost = "",
    [string]$SmtpPort = "",
    [string]$SmtpUsername = "",
    [string]$SmtpPassword = "",
    [ValidateSet("starttls", "ssl", "none", "")]
    [string]$SmtpSecurity = "",
    [string]$OpsCloudflareAnalyticsZoneId = "",
    [string]$OpsCloudflareAnalyticsToken = "",
    [string]$ServerMonitorToken = "",
    [string]$ExpectedCommit = "",
    [string]$DeploymentIntentToken = "",
    [string]$SourceCommit = "",
    [string]$ExpectedSourceTree = "",
    [ValidateSet("primary", "standby", "")]
    [string]$NodeRole = "",
    [ValidateSet("legacy", "distributed", "")]
    [string]$CrawlerMode = "",
    [switch]$EnableCrawler,
    [switch]$SkipSystemPackages,
    [switch]$SkipWorkers,
    [switch]$UseScpFallback,
    [switch]$InstallOpsConsole,
    [switch]$AllowCrawlerInterruption,
    [switch]$Standby
)

$ErrorActionPreference = "Stop"

$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "../..")

function Get-ReviewedCrawlerMode {
    param([string]$SnapshotCommit = "")

    $topologyPath = Join-Path $projectRoot "config/production_topology.json"
    if ($SnapshotCommit) {
        Push-Location $projectRoot
        try {
            $repositoryRoot = ([string](& git rev-parse --show-toplevel 2>$null)).Trim()
            if ($LASTEXITCODE -ne 0 -or -not $repositoryRoot) {
                throw "Unable to resolve the Git repository root for the reviewed deployment snapshot."
            }
            $topologyText = @(& git -C $repositoryRoot show "${SnapshotCommit}:config/production_topology.json" 2>$null) -join "`n"
            if ($LASTEXITCODE -ne 0 -or -not $topologyText) {
                throw "Reviewed deployment snapshot is missing config/production_topology.json."
            }
        } finally {
            Pop-Location
        }
    } else {
        if (-not (Test-Path -LiteralPath $topologyPath -PathType Leaf)) {
            throw "Missing reviewed production topology: config/production_topology.json."
        }
        $topologyText = Get-Content -LiteralPath $topologyPath -Raw
    }
    try {
        $topology = $topologyText | ConvertFrom-Json
    } catch {
        throw "Reviewed production topology is not valid JSON."
    }
    $modeProperty = $topology.PSObject.Properties["crawlerMode"]
    if (
        $null -eq $modeProperty -or
        $modeProperty.Value -isnot [string] -or
        @("legacy", "distributed") -notcontains [string]$modeProperty.Value
    ) {
        throw "Reviewed production topology crawlerMode must be exactly 'legacy' or 'distributed'."
    }
    return [string]$modeProperty.Value
}

# Ops Console has its own host and release path. Public MoonCen deploys must
# not install or enable it.
$InstallOpsConsole = $false
$targetHost = if ($SshHost) { $SshHost } else { $Server }
if (-not $targetHost) {
    throw "Set -Server or -SshHost. You can use either an IP address or a domain."
}
if ($User -notmatch '^[A-Za-z_][A-Za-z0-9_-]*$') {
    throw "Invalid SSH user name."
}
if ($targetHost -notmatch '^[A-Za-z0-9._:-]+$') {
    throw "Invalid SSH host or alias."
}
if ($RemoteDir -ne "/opt/mooncen") {
    throw "RemoteDir must be /opt/mooncen because systemd units and root-owned helpers use that immutable path."
}
if ($Domain -eq "_") {
    $Domain = $targetHost
}
if ($Domain -notmatch '^[A-Za-z0-9.-]+$') {
    throw "Domain must be a hostname without a scheme, path, or shell metacharacters."
}
if ($DbDeploymentWorkerUser -cne "mooncen_deployment_worker_login") {
    throw "DbDeploymentWorkerUser must be the fixed mooncen_deployment_worker_login capability identity."
}
if ($Standby -and $NodeRole -and $NodeRole -ne "standby") {
    throw "-Standby cannot be combined with -NodeRole '$NodeRole'."
}
if ($CrawlerMode -notin @("legacy", "distributed")) {
    throw "CrawlerMode must come from the reviewed production topology."
}
if ($CrawlerMode -eq "distributed" -and $EnableCrawler) {
    throw "Distributed crawler mode forbids enabling the legacy crawler runtime."
}
if ($Standby -and $EnableCrawler) {
    throw "-EnableCrawler cannot be combined with -Standby."
}
if ($NodeRole -eq "standby") {
    $Standby = $true
}
if (-not $NodeRole) {
    $NodeRole = if ($Standby) { "standby" } else { "primary" }
}
if ($Standby -and -not $DbSslRootCert) {
    # Direct standby deployments use the same root-owned trust anchor as the
    # top-level orchestrator. setup_project.sh validates it before reuse.
    $DbSslRootCert = "/etc/mooncen/db-root-ca.crt"
}
$normalizedExpectedCommit = $ExpectedCommit.Trim().ToLowerInvariant()
if (
    $normalizedExpectedCommit -and
    $normalizedExpectedCommit -notmatch '^(?:[0-9a-f]{40}|[0-9a-f]{64})$'
) {
    throw "ExpectedCommit must be an exact Git object identifier."
}
$normalizedDeploymentIntentToken = $DeploymentIntentToken.Trim().ToLowerInvariant()
if (
    $normalizedDeploymentIntentToken -and
    $normalizedDeploymentIntentToken -notmatch '^[0-9a-f]{32}$'
) {
    throw "DeploymentIntentToken must be an exact lowercase 32-character hexadecimal token."
}
$normalizedSourceCommit = $SourceCommit.Trim().ToLowerInvariant()
$normalizedExpectedSourceTree = $ExpectedSourceTree.Trim().ToLowerInvariant()
if ([bool]$normalizedSourceCommit -xor [bool]$normalizedExpectedSourceTree) {
    throw "SourceCommit and ExpectedSourceTree must be provided together."
}
if ($normalizedSourceCommit -and -not $normalizedExpectedCommit) {
    throw "ExpectedCommit is required for a development snapshot deployment."
}
if (
    $normalizedSourceCommit -and
    $normalizedSourceCommit -notmatch '^(?:[0-9a-f]{40}|[0-9a-f]{64})$'
) {
    throw "SourceCommit must be an exact Git object identifier."
}
if (
    $normalizedExpectedSourceTree -and
    $normalizedExpectedSourceTree -notmatch '^(?:[0-9a-f]{40}|[0-9a-f]{64})$'
) {
    throw "ExpectedSourceTree must be an exact Git tree identifier."
}
$reviewedCrawlerMode = Get-ReviewedCrawlerMode $normalizedSourceCommit
if ($CrawlerMode -ne $reviewedCrawlerMode) {
    throw "CrawlerMode does not match the reviewed production topology."
}
$normalizedExpectedDeployCommit = if ($normalizedSourceCommit) {
    $normalizedSourceCommit
} else {
    $normalizedExpectedCommit
}

$remote = "$User@$targetHost"
$releaseId = [guid]::NewGuid().ToString("N")
if (-not $normalizedDeploymentIntentToken) {
    $normalizedDeploymentIntentToken = $releaseId
}

function New-LocalDeploymentTempDirectory {
    $basePath = if ($env:MOONCEN_DEPLOY_TEMP_ROOT) {
        $env:MOONCEN_DEPLOY_TEMP_ROOT
    } elseif ($IsWindows -or $env:OS -eq "Windows_NT") {
        $env:TEMP
    } else {
        throw "MOONCEN_DEPLOY_TEMP_ROOT is required for a POSIX deployment."
    }
    if (-not $basePath -or -not (Test-Path -LiteralPath $basePath -PathType Container)) {
        throw "The reviewed local deployment temp root is unavailable."
    }
    $resolvedBase = (Resolve-Path -LiteralPath $basePath).Path
    $baseItem = Get-Item -LiteralPath $resolvedBase -Force
    if ($baseItem.LinkType) {
        throw "The reviewed local deployment temp root must not be a symbolic link."
    }
    if (-not ($IsWindows -or $env:OS -eq "Windows_NT")) {
        $baseMetadata = @(& /usr/bin/stat -c "%u:%a" -- $resolvedBase 2>$null)
        $currentUid = @(& /usr/bin/id -u 2>$null)
        if (
            $LASTEXITCODE -ne 0 -or
            $baseMetadata.Count -ne 1 -or
            $currentUid.Count -ne 1 -or
            $baseMetadata[0] -ne "$($currentUid[0]):700"
        ) {
            throw "The POSIX deployment temp root must be owned by the operator with mode 0700."
        }
    }
    $destination = Join-Path $resolvedBase "release-$releaseId"
    if (Test-Path -LiteralPath $destination) {
        throw "The local deployment temp directory already exists."
    }
    [System.IO.Directory]::CreateDirectory($destination) | Out-Null
    if (-not ($IsWindows -or $env:OS -eq "Windows_NT")) {
        & /usr/bin/chmod 0700 -- $destination
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to protect the local deployment temp directory."
        }
        $metadata = @(& /usr/bin/stat -c "%u:%a" -- $destination 2>$null)
        $currentUid = @(& /usr/bin/id -u 2>$null)
        if ($metadata.Count -ne 1 -or $currentUid.Count -ne 1 -or $metadata[0] -ne "$($currentUid[0]):700") {
            throw "The local deployment temp directory permissions are unsafe."
        }
    }
    return $destination
}

function Write-PrivateLocalTextFile {
    param(
        [string]$Path,
        [string]$Content
    )
    if (Test-Path -LiteralPath $Path) {
        throw "Refusing to overwrite an existing local deployment file."
    }
    $encoding = New-Object System.Text.UTF8Encoding($false)
    $stream = [System.IO.File]::Open(
        $Path,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None
    )
    try {
        $writer = New-Object System.IO.StreamWriter($stream, $encoding)
        try {
            $writer.Write($Content)
            $writer.Flush()
        } finally {
            $writer.Dispose()
        }
    } finally {
        $stream.Dispose()
    }
    if (-not ($IsWindows -or $env:OS -eq "Windows_NT")) {
        & /usr/bin/chmod 0600 -- $Path
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to protect a local deployment file."
        }
    }
}

$localDeploymentTemp = New-LocalDeploymentTempDirectory
$archivePath = Join-Path $localDeploymentTemp "release.tar.gz"
$remoteReleaseDir = "/opt/.mooncen-release-$releaseId"
$remotePreviousDir = "/opt/.mooncen-previous-$releaseId"
$remoteFailedDir = "/opt/.mooncen-failed-$releaseId"
$remoteArchivePath = "$remoteReleaseDir/release.tar.gz"
$remoteDeployLock = "/opt/.mooncen-deploy.lock"
$remoteGuardHeartbeat = "/opt/.mooncen-deploy-heartbeat-$releaseId"
$remoteGuardUnit = "mooncen-deploy-guard@$releaseId.service"
$releaseSwapped = $false
$remoteGuardArmed = $false
$nativeIntentFenceEstablished = $false
$DeploymentRemoteExitCode = 0
$remoteSetupLocalPath = Join-Path $localDeploymentTemp "remote-setup.sh"
$sshBaseArgs = @()
if ($IdentityFile) {
    $resolvedIdentityFile = (Resolve-Path $IdentityFile).Path
    $sshBaseArgs += @("-i", "$resolvedIdentityFile", "-o", "IdentitiesOnly=yes")
}
$sshBaseArgs += @(
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=yes",
    "-o", "UpdateHostKeys=no",
    "-o", "PreferredAuthentications=publickey",
    "-o", "PasswordAuthentication=no",
    "-o", "KbdInteractiveAuthentication=no",
    "-o", "NumberOfPasswordPrompts=0"
)

function New-RandomSecret {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    } finally {
        $rng.Dispose()
    }
    return ($bytes | ForEach-Object { $_.ToString("x2") }) -join ""
}

function New-DerivedSecret {
    param(
        [Parameter(Mandatory = $true)][string]$MasterSecret,
        [Parameter(Mandatory = $true)][string]$Context
    )
    $key = [Text.Encoding]::UTF8.GetBytes($MasterSecret)
    $payload = [Text.Encoding]::UTF8.GetBytes("mooncen-db-role-v1:$Context")
    $hmac = [System.Security.Cryptography.HMACSHA256]::new($key)
    try {
        return ($hmac.ComputeHash($payload) | ForEach-Object { $_.ToString("x2") }) -join ""
    } finally {
        $hmac.Dispose()
    }
}

function ConvertTo-Base64Utf8 {
    param([AllowEmptyString()][string]$Value)
    return [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes([string]$Value))
}

function Assert-ValidDatabasePassword {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [AllowEmptyString()][string]$Value,
        [switch]$AllowEmpty
    )

    if ($AllowEmpty -and -not $Value) {
        return
    }
    if (
        $Value.Length -lt 16 -or
        $Value -match '^(change-me|replace-with)' -or
        $Value.Contains("`n") -or
        $Value.Contains("`r")
    ) {
        throw "$Name must be a random value of at least 16 characters without line breaks."
    }
}

function Assert-UnchangedRemoteDatabaseCredential {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [AllowEmptyString()][string]$RemoteValue,
        [AllowEmptyString()][string]$CandidateValue
    )

    if (
        $RemoteValue -and
        $CandidateValue -and
        -not [string]::Equals($RemoteValue, $CandidateValue, [StringComparison]::Ordinal)
    ) {
        throw "$Name differs from the protected remote credential. Standard deployment cannot rotate database credentials; use a separately reviewed rotation workflow."
    }
}

function Mask-SensitiveText {
    param([string]$Text)
    if (-not $Text) {
        return ""
    }
    $masked = $Text
    $masked = $masked -replace '(?i)(DB_PASSWORD|DB_API_PASSWORD|DB_CRAWLER_PASSWORD|DB_DEPLOYMENT_WORKER_PASSWORD|DB_AI_PASSWORD|DB_APPLIER_PASSWORD|PRIMARY_DB_PASSWORD|DB_BACKUP_PASSWORD|DB_CHECK_PASSWORD|AUTH_SECRET|MOONCEN_OPS_PASSWORD_HASH|KAKAO_MAPS_REST_API_KEY|KAKAO_MAPS_JAVASCRIPT_KEY|GOOGLE_OAUTH_CLIENT_SECRET|NAVER_OAUTH_CLIENT_SECRET|MOONCEN_BOT_TOKEN|MOONCEN_SMTP_PASSWORD|OPS_CLOUDFLARE_ANALYTICS_TOKEN|MOONCEN_SERVER_MONITOR_TOKEN|TUNNEL_TOKEN)=["'']?[^"''\s;]+', '$1=<redacted>'
    $masked = $masked -replace '(?i)(-DbDeploymentWorkerPassword\s+)[^\s]+', '$1<redacted>'
    $masked = $masked -replace '(?i)(-OpsPasswordHash\s+)[^\s]+', '$1<redacted>'
    $masked = $masked -replace '(?i)(-SmtpPassword\s+)[^\s]+', '$1<redacted>'
    $masked = $masked -replace '(?i)(-OpsCloudflareAnalyticsToken\s+)[^\s]+', '$1<redacted>'
    $masked = $masked -replace '(?i)(-ServerMonitorToken\s+)[^\s]+', '$1<redacted>'
    $masked = $masked -replace '(?i)(-(?:KakaoMapsJavascriptKey|KakaoMapsRestApiKey)\s+)[^\s]+', '$1<redacted>'
    $masked = $masked -replace '(?i)(--token\s+)[^\s]+', '$1<redacted>'
    $masked = $masked -replace 'GOCSPX-[A-Za-z0-9_-]+', '<redacted-google-secret>'
    $masked = $masked -replace 'eyJhIjoi[A-Za-z0-9_-]+', '<redacted-cloudflare-token>'
    $masked = $masked -replace '[0-9]{8,12}:[A-Za-z0-9_-]{30,}', '<redacted-telegram-token>'
    return $masked
}

function Get-DeploymentRemoteErrorCode {
    param([int]$ExitCode)
    switch ($ExitCode) {
        65 { return "unsafe_remote_state" }
        73 { return "lock_busy" }
        75 { return "recovery_required" }
        default { return "" }
    }
}

function Write-DeploymentFailureMarker {
    param([int]$ExitCode)
    $errorCode = Get-DeploymentRemoteErrorCode $ExitCode
    if ($errorCode) {
        # Stable, non-secret sentinel consumed by the Ops deployment worker.
        Write-Host "MOONCEN_DEPLOY_FAILURE error_code=$errorCode remote_exit=$ExitCode"
    }
}

function Invoke-Remote {
    param([string]$Command)
    Update-RemoteDeploymentGuardHeartbeat
    $script:DeploymentRemoteExitCode = 0
    ssh @sshBaseArgs $remote $Command
    $remoteExitCode = $LASTEXITCODE
    if ($remoteExitCode -ne 0) {
        if (Get-DeploymentRemoteErrorCode $remoteExitCode) {
            $script:DeploymentRemoteExitCode = $remoteExitCode
        }
        throw "Remote command failed: $(Mask-SensitiveText $Command)"
    }
}

function Invoke-RemoteWithInput {
    param(
        [string]$Command,
        [string]$InputText
    )

    Update-RemoteDeploymentGuardHeartbeat
    $script:DeploymentRemoteExitCode = 0
    $InputText | & ssh @sshBaseArgs $remote $Command
    $remoteExitCode = $LASTEXITCODE
    if ($remoteExitCode -ne 0) {
        if (Get-DeploymentRemoteErrorCode $remoteExitCode) {
            $script:DeploymentRemoteExitCode = $remoteExitCode
        }
        throw "Remote command with protected stdin failed: $(Mask-SensitiveText $Command)"
    }
}

function Invoke-RemoteTty {
    param([string]$Command)
    Update-RemoteDeploymentGuardHeartbeat
    $script:DeploymentRemoteExitCode = 0
    ssh @sshBaseArgs -tt $remote $Command
    $remoteExitCode = $LASTEXITCODE
    if ($remoteExitCode -ne 0) {
        if (Get-DeploymentRemoteErrorCode $remoteExitCode) {
            $script:DeploymentRemoteExitCode = $remoteExitCode
        }
        throw "Remote command failed: $(Mask-SensitiveText $Command)"
    }
}

function Invoke-RemoteBashScriptTty {
    param(
        [string]$Script,
        [switch]$SkipGuardHeartbeat
    )

    $script:DeploymentRemoteExitCode = 0
    if (-not $SkipGuardHeartbeat) {
        Update-RemoteDeploymentGuardHeartbeat
    }
    $localScriptPath = Join-Path $localDeploymentTemp ("remote-step-{0}.sh" -f ([guid]::NewGuid().ToString("N")))
    $remoteScriptPath = "/tmp/" + [System.IO.Path]::GetFileName($localScriptPath)
    $normalizedScript = $Script.Replace("`r`n", "`n").Replace("`r", "`n")
    Write-PrivateLocalTextFile $localScriptPath $normalizedScript

    try {
        scp @sshBaseArgs $localScriptPath "${remote}:$remoteScriptPath"
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to upload remote script: $remoteScriptPath"
        }

        $runCommand = "chmod 700 $remoteScriptPath && bash $remoteScriptPath; status=`$?; rm -f $remoteScriptPath; exit `$status"
        ssh @sshBaseArgs -tt $remote $runCommand
        $remoteScriptExitCode = $LASTEXITCODE
        if ($remoteScriptExitCode -ne 0) {
            if (Get-DeploymentRemoteErrorCode $remoteScriptExitCode) {
                $script:DeploymentRemoteExitCode = $remoteScriptExitCode
            }
            throw "Remote script failed with exit code ${remoteScriptExitCode}: $remoteScriptPath"
        }
    } finally {
        Remove-Item -LiteralPath $localScriptPath -Force -ErrorAction SilentlyContinue
    }
}

function Update-RemoteDeploymentGuardHeartbeat {
    if (-not $script:remoteGuardArmed -and -not $script:remoteDeployLockAcquired) {
        return
    }
    & ssh @sshBaseArgs $remote "test -f '$remoteGuardHeartbeat' -a ! -L '$remoteGuardHeartbeat' && touch '$remoteGuardHeartbeat'" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Remote deployment heartbeat failed; deployment ownership may already be recovering or stale."
    }
}

function Invoke-RemoteHealthCheck {
    $command = @'
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8001/health >/dev/null 2>&1 &&
     curl -fsS http://127.0.0.1:5173/ >/dev/null 2>&1 &&
     curl -fsS http://localhost/health >/dev/null 2>&1 &&
     curl -fsS http://localhost/ >/dev/null 2>&1; then
    curl -fsS http://127.0.0.1:8001/health
    echo
    curl -fsS http://localhost/health
    exit 0
  fi
  sleep 1
done
echo "Health check failed after 30 seconds."
systemctl --no-pager --full status mooncen-api mooncen-frontend nginx | sed -n '1,120p' || true
exit 1
'@
    Invoke-RemoteBashScriptTty $command
}

function Get-RemoteEnvValue {
    param([string]$Name)

    if ($Name -notmatch '^[A-Z0-9_]+$') {
        throw "Invalid remote environment key name."
    }
    $candidateFiles = "'$RemoteDir/.env' /etc/mooncen/api.env /etc/mooncen/backup.env"
    $candidateFiles += ' "$HOME/.config/mooncen/deploy-secrets.env" "$HOME/.config/mooncen/migrator.env"'
    $command = "for file in $candidateFiles; do if [ -r `"`$file`" ]; then encoded=`$(grep -E '^${Name}_B64=' `"`$file`" | tail -n1 | cut -d= -f2-); if [ -n `"`$encoded`" ]; then printf '%s' `"`$encoded`"; exit 0; fi; raw=`$(grep -E '^${Name}=' `"`$file`" | tail -n1 | cut -d= -f2-); if [ -n `"`$raw`" ]; then printf '%s' `"`$raw`" | base64 | tr -d '\r\n'; exit 0; fi; fi; done"
    # Preserve the reader's nested shell quoting across Windows OpenSSH's
    # native-argument reconstruction. The encoded payload contains only the
    # reviewed read command; secret values return solely over SSH stdout.
    $encodedCommand = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($command))
    $transportCommand = "printf '%s' '$encodedCommand' | base64 -d | bash"
    $value = (& ssh @sshBaseArgs $remote $transportCommand 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $value) {
        return ""
    }

    $firstValue = $value | Select-Object -First 1
    if (-not $firstValue) {
        return ""
    }

    try {
        return [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("$firstValue".Trim()))
    } catch {
        return ""
    }
}

function Get-ValidatedDeployCommit {
    # A deployment must describe exactly one reviewed snapshot.  Validate this
    # before any SSH connection so a local source problem cannot acquire a
    # remote lock or read deployment secrets.
    $git = Get-Command git -ErrorAction SilentlyContinue
    if (-not $git) {
        throw "git is required to create a safe deployment artifact"
    }

    Push-Location $projectRoot
    try {
        $repositoryRootRaw = & git rev-parse --show-toplevel 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $repositoryRootRaw) {
            throw "Unable to resolve the Git repository root for deployment."
        }
        $repositoryRoot = ([string]$repositoryRootRaw).Trim()
        $previousErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $headCommitRaw = & git rev-parse --verify 'HEAD^{commit}' 2>$null
            $revParseExitCode = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $previousErrorActionPreference
        }
        if ($revParseExitCode -ne 0 -or -not $headCommitRaw) {
            throw "Deployment requires a committed Git HEAD. Initialize the repository and review the first commit before deploying."
        }
        $headCommit = ([string]$headCommitRaw).Trim().ToLowerInvariant()
        if ($headCommit -notmatch '^(?:[0-9a-f]{40}|[0-9a-f]{64})$') {
            throw "Git returned an invalid deployment commit identifier."
        }
        if (
            $normalizedExpectedCommit -and
            -not [string]::Equals(
                $headCommit,
                $normalizedExpectedCommit,
                [StringComparison]::Ordinal
            )
        ) {
            throw "Git HEAD does not match ExpectedCommit during deployment source validation."
        }

        $commit = $headCommit
        if ($normalizedSourceCommit) {
            $sourceCommitRaw = & git -C $repositoryRoot rev-parse --verify "${normalizedSourceCommit}^{commit}" 2>$null
            if ($LASTEXITCODE -ne 0 -or -not $sourceCommitRaw) {
                throw "The reviewed development snapshot commit is unavailable."
            }
            $commit = ([string]$sourceCommitRaw).Trim().ToLowerInvariant()
            if (-not [string]::Equals(
                $commit,
                $normalizedSourceCommit,
                [StringComparison]::Ordinal
            )) {
                throw "The development snapshot commit identifier changed."
            }
            $snapshotParent = (& git -C $repositoryRoot rev-parse --verify "${commit}^1" 2>$null)
            if (
                $LASTEXITCODE -ne 0 -or
                -not [string]::Equals(
                    ([string]$snapshotParent).Trim().ToLowerInvariant(),
                    $headCommit,
                    [StringComparison]::Ordinal
                )
            ) {
                throw "The development snapshot is not based on the reviewed Git HEAD."
            }
            $snapshotTree = (& git -C $repositoryRoot rev-parse --verify "${commit}^{tree}" 2>$null)
            if (
                $LASTEXITCODE -ne 0 -or
                -not [string]::Equals(
                    ([string]$snapshotTree).Trim().ToLowerInvariant(),
                    $normalizedExpectedSourceTree,
                    [StringComparison]::Ordinal
                )
            ) {
                throw "The development snapshot tree does not match ExpectedSourceTree."
            }
        } else {
            $worktreeStatus = @(git status --porcelain=v1 --untracked-files=all)
            if ($LASTEXITCODE -ne 0) {
                throw "Unable to inspect the Git working tree before deployment."
            }
            if ($worktreeStatus.Count -gt 0) {
                $preview = @($worktreeStatus | Select-Object -First 20) -join [Environment]::NewLine
                $more = if ($worktreeStatus.Count -gt 20) { [Environment]::NewLine + "... and $($worktreeStatus.Count - 20) more path(s)" } else { "" }
                throw "Deployment requires a clean Git working tree because only committed files are packaged. Commit or remove these changes first:$([Environment]::NewLine)$preview$more"
            }
        }

        $forbiddenPathspec = @(
            '.env', '.env.*', 'deploy.local.ps1', 'config/deploy_servers.json',
            '*.key', '*.pem', '*.p12', '*.pfx', '*.jks', '*.p8',
            '*.mobileprovision', '*.sqlite', '*.db-wal', '*.db-shm',
            '*.dump', '*.dump.*', '.venv/**', 'venv/**', 'venv_clean/**',
            '**/node_modules/**', '**/__pycache__/**', '*.pyc', 'tmp_*/*',
            'chromedriver/*', 'ops-console/**', 'deploy/ops-console/**'
        )
        $forbiddenTracked = if ($normalizedSourceCommit) {
            @(& git -C $repositoryRoot ls-tree -r --name-only $commit -- @forbiddenPathspec)
        } else {
            @(git ls-files -- @forbiddenPathspec)
        }
        $forbiddenTracked = @($forbiddenTracked) |
            Where-Object { $_ -and $_ -ne '.env.example' }
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to inspect tracked files before deployment."
        }
        if ($forbiddenTracked.Count -gt 0) {
            throw "Refusing to deploy tracked sensitive/local files: $($forbiddenTracked -join ', ')"
        }
        $unsafeEntries = @(& git -C $repositoryRoot ls-tree -r $commit) |
            Where-Object { $_ -match '^(120000|160000) ' }
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to inspect deployment tree entry modes."
        }
        if ($unsafeEntries.Count -gt 0) {
            throw "Refusing to deploy a source tree containing symbolic links or submodules."
        }
        $script:DeploymentGitRepositoryRoot = $repositoryRoot
        return $commit
    } finally {
        Pop-Location
    }
}

function Assert-ExactExpectedDeployCommit {
    param(
        [Parameter(Mandatory = $true)][string]$ActualCommit,
        [Parameter(Mandatory = $true)][string]$Stage
    )
    if (
        $normalizedExpectedDeployCommit -and
        -not [string]::Equals(
            $ActualCommit,
            $normalizedExpectedDeployCommit,
            [StringComparison]::Ordinal
        )
    ) {
        throw "Git HEAD does not match ExpectedCommit at $Stage. Create a new reviewed deployment plan."
    }
}

foreach ($explicitPassword in @(
    @{ Name = "DB_PASSWORD"; Value = $DbPassword },
    @{ Name = "DB_API_PASSWORD"; Value = $DbApiPassword },
    @{ Name = "DB_CRAWLER_PASSWORD"; Value = $DbCrawlerPassword },
    @{ Name = "DB_DEPLOYMENT_WORKER_PASSWORD"; Value = $DbDeploymentWorkerPassword },
    @{ Name = "DB_AI_PASSWORD"; Value = $DbAiPassword },
    @{ Name = "PRIMARY_DB_PASSWORD"; Value = $DbApplierPassword },
    @{ Name = "DB_BACKUP_PASSWORD"; Value = $DbBackupPassword },
    @{ Name = "DB_CHECK_PASSWORD"; Value = $DbCheckPassword }
)) {
    Assert-ValidDatabasePassword `
        -Name ([string]$explicitPassword.Name) `
        -Value ([string]$explicitPassword.Value) `
        -AllowEmpty
}

$deployCommit = Get-ValidatedDeployCommit
Assert-ExactExpectedDeployCommit $deployCommit "deployment preflight"


if ($EnableCrawler -and -not $AllowCrawlerInterruption) {
    $crawlerDrainCheckScript = @'
set -euo pipefail
active_crawler_unit=""
for candidate in mooncen-crawler-once.service mooncen-crawler.service; do
  if systemctl is-active --quiet "$candidate"; then
    active_crawler_unit="$candidate"
    break
  fi
done
if [ -z "$active_crawler_unit" ]; then
  echo "crawler_deploy_state=inactive"
  exit 0
fi
progress_file=/opt/mooncen/logs/crawler_progress.json
if [ ! -r "$progress_file" ] || [ -L "$progress_file" ]; then
  echo "crawler-host deploy blocked: active crawler progress is unavailable or unsafe" >&2
  exit 75
fi
progress_status="$(python3 -I - "$progress_file" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except (OSError, UnicodeError, json.JSONDecodeError):
    raise SystemExit(75)
status = payload.get("status")
if not isinstance(status, str) or not status:
    raise SystemExit(75)
print(status)
PY
)" || {
  echo "crawler-host deploy blocked: active crawler progress could not be parsed" >&2
  exit 75
}
main_pid="$(systemctl show "$active_crawler_unit" -p MainPID --value)"
case "$progress_status" in
  sleeping|completed|partial_success|failed|stopped|skipped)
    if [ "${main_pid:-0}" != "0" ] && pgrep -P "$main_pid" >/dev/null 2>&1; then
      echo "crawler-host deploy blocked: crawler reports $progress_status but still has an active provider process" >&2
      exit 75
    fi
    echo "crawler_deploy_state=$progress_status unit=$active_crawler_unit"
    ;;
  *)
    echo "crawler-host deploy blocked: crawler state is $progress_status; wait for sleeping state" >&2
    echo "Use -AllowCrawlerInterruption only for an intentional emergency stop." >&2
    exit 75
    ;;
esac
'@
    try {
        Invoke-RemoteBashScriptTty $crawlerDrainCheckScript
    } catch {
        $crawlerDrainFailure = $_
        $crawlerDrainFailureExitCode = $script:DeploymentRemoteExitCode
        if (Get-DeploymentRemoteErrorCode $crawlerDrainFailureExitCode) {
            Write-DeploymentFailureMarker $crawlerDrainFailureExitCode
            exit $crawlerDrainFailureExitCode
        }
        throw $crawlerDrainFailure
    }
}

$remoteDbPassword = Get-RemoteEnvValue "DB_PASSWORD"
Assert-UnchangedRemoteDatabaseCredential `
    -Name "DB_PASSWORD" -RemoteValue $remoteDbPassword -CandidateValue $DbPassword
if (-not $DbPassword) {
    $DbPassword = $remoteDbPassword
    if ($DbPassword) {
        Write-Host "Using existing DB password from the remote deploy secret store."
    } else {
        $DbPassword = New-RandomSecret
        Write-Host "Generated DB password for this deploy."
    }
}
Assert-ValidDatabasePassword -Name "DB_PASSWORD" -Value $DbPassword

$remoteDbDeploymentWorkerUser = Get-RemoteEnvValue "DB_DEPLOYMENT_WORKER_USER"
if (
    $remoteDbDeploymentWorkerUser -and
    -not [string]::Equals(
        $remoteDbDeploymentWorkerUser,
        $DbDeploymentWorkerUser,
        [StringComparison]::Ordinal
    )
) {
    throw "DB_DEPLOYMENT_WORKER_USER differs from the protected remote identity. Standard deployment cannot rotate the dedicated worker LOGIN."
}
$remoteDbDeploymentWorkerPassword = Get-RemoteEnvValue "DB_DEPLOYMENT_WORKER_PASSWORD"
Assert-UnchangedRemoteDatabaseCredential `
    -Name "DB_DEPLOYMENT_WORKER_PASSWORD" `
    -RemoteValue $remoteDbDeploymentWorkerPassword `
    -CandidateValue $DbDeploymentWorkerPassword
if (-not $DbDeploymentWorkerPassword) {
    $DbDeploymentWorkerPassword = $remoteDbDeploymentWorkerPassword
    if ($DbDeploymentWorkerPassword) {
        Write-Host "Using existing DB_DEPLOYMENT_WORKER_PASSWORD from the remote environment."
    } else {
        $DbDeploymentWorkerPassword = New-RandomSecret
        Write-Host "Generated an independent DB_DEPLOYMENT_WORKER_PASSWORD for this deployment."
    }
}
Assert-ValidDatabasePassword `
    -Name "DB_DEPLOYMENT_WORKER_PASSWORD" `
    -Value $DbDeploymentWorkerPassword

$runtimePasswords = @(
    @{ Name = "DB_API_PASSWORD"; Parameter = "DbApiPassword"; Context = "api" },
    @{ Name = "DB_CRAWLER_PASSWORD"; Parameter = "DbCrawlerPassword"; Context = "crawler" },
    @{ Name = "DB_AI_PASSWORD"; Parameter = "DbAiPassword"; Context = "ai" },
    @{ Name = "PRIMARY_DB_PASSWORD"; Parameter = "DbApplierPassword"; Context = "applier" },
    @{ Name = "DB_BACKUP_PASSWORD"; Parameter = "DbBackupPassword"; Context = "backup" },
    @{ Name = "DB_CHECK_PASSWORD"; Parameter = "DbCheckPassword"; Context = "check" }
)
foreach ($item in $runtimePasswords) {
    $parameterName = [string]$item.Parameter
    $currentValue = Get-Variable -Name $parameterName -ValueOnly
    $remoteValue = Get-RemoteEnvValue ([string]$item.Name)
    Assert-UnchangedRemoteDatabaseCredential `
        -Name ([string]$item.Name) `
        -RemoteValue $remoteValue `
        -CandidateValue ([string]$currentValue)
    if (-not $currentValue) {
        $currentValue = $remoteValue
        if ($currentValue) {
            Write-Host "Using existing $($item.Name) from the remote environment."
        } else {
            $currentValue = New-DerivedSecret -MasterSecret $DbPassword -Context ([string]$item.Context)
            Write-Host "Derived a distinct $($item.Name) for this deployment."
        }
        Set-Variable -Name $parameterName -Value $currentValue
    }
    Assert-ValidDatabasePassword -Name ([string]$item.Name) -Value ([string]$currentValue)
}
foreach ($otherDbPassword in @(
    $DbPassword,
    $DbApiPassword,
    $DbCrawlerPassword,
    $DbAiPassword,
    $DbApplierPassword,
    $DbBackupPassword,
    $DbCheckPassword
)) {
    if (
        [string]::Equals(
            $DbDeploymentWorkerPassword,
            $otherDbPassword,
            [StringComparison]::Ordinal
        )
    ) {
        throw "DB_DEPLOYMENT_WORKER_PASSWORD must differ from every other database LOGIN credential."
    }
}
$databaseLoginCredentials = @(
    @{ Name = "DB_PASSWORD"; Value = $DbPassword },
    @{ Name = "DB_API_PASSWORD"; Value = $DbApiPassword },
    @{ Name = "DB_CRAWLER_PASSWORD"; Value = $DbCrawlerPassword },
    @{ Name = "DB_DEPLOYMENT_WORKER_PASSWORD"; Value = $DbDeploymentWorkerPassword },
    @{ Name = "DB_AI_PASSWORD"; Value = $DbAiPassword },
    @{ Name = "PRIMARY_DB_PASSWORD"; Value = $DbApplierPassword },
    @{ Name = "DB_BACKUP_PASSWORD"; Value = $DbBackupPassword },
    @{ Name = "DB_CHECK_PASSWORD"; Value = $DbCheckPassword }
)
for ($passwordIndex = 0; $passwordIndex -lt $databaseLoginCredentials.Count; $passwordIndex++) {
    for (
        $otherPasswordIndex = $passwordIndex + 1;
        $otherPasswordIndex -lt $databaseLoginCredentials.Count;
        $otherPasswordIndex++
    ) {
        if (
            [string]::Equals(
                [string]$databaseLoginCredentials[$passwordIndex].Value,
                [string]$databaseLoginCredentials[$otherPasswordIndex].Value,
                [StringComparison]::Ordinal
            )
        ) {
            throw "Database LOGIN credentials must be pairwise distinct."
        }
    }
}

if (-not $BackupAgeRecipient) {
    $BackupAgeRecipient = Get-RemoteEnvValue "BACKUP_AGE_RECIPIENT"
    if ($BackupAgeRecipient) {
        Write-Host "Using existing backup age recipient from the remote backup environment."
    }
}
if (-not $Standby -and -not $BackupAgeRecipient) {
    throw "Set -BackupAgeRecipient to an age X25519 public recipient (age1...). Plaintext backups are not allowed."
}
if ($BackupAgeRecipient -and $BackupAgeRecipient -notmatch '^age1[0-9a-z]+$') {
    throw "BackupAgeRecipient must be an age X25519 public recipient beginning with age1."
}
if (-not $BackupPort) {
    $BackupPort = Get-RemoteEnvValue "BACKUP_PORT"
    if ($BackupPort) {
        Write-Host "Using existing BACKUP_PORT from the remote backup environment."
    }
}
if ($BackupPort -and ($BackupPort -notmatch '^[0-9]+$' -or [int64]$BackupPort -lt 1 -or [int64]$BackupPort -gt 65535)) {
    throw "BackupPort must be empty or an integer between 1 and 65535."
}

if (-not $AuthSecret) {
    $AuthSecret = Get-RemoteEnvValue "AUTH_SECRET"
    if ($AuthSecret) {
        Write-Host "Using existing AUTH_SECRET from remote .env."
    } else {
        $AuthSecret = New-RandomSecret
        Write-Host "Generated AUTH_SECRET for this deploy."
    }
}

if (-not $KakaoMapsJavascriptKey) {
    $KakaoMapsJavascriptKey = Get-RemoteEnvValue "KAKAO_MAPS_JAVASCRIPT_KEY"
}
if (-not $KakaoMapsRestApiKey) {
    $KakaoMapsRestApiKey = Get-RemoteEnvValue "KAKAO_MAPS_REST_API_KEY"
}
if (
    $KakaoMapsJavascriptKey -cnotmatch '^[0-9a-f]{32}$' -or
    $KakaoMapsRestApiKey -cnotmatch '^[0-9a-f]{32}$'
) {
    throw "Production deployment requires valid 32-character Kakao Maps JavaScript and REST API keys."
}

if (-not $OpsLoginId) {
    $OpsLoginId = Get-RemoteEnvValue "MOONCEN_OPS_LOGIN_ID"
}
if (-not $OpsPasswordHash) {
    $OpsPasswordHash = Get-RemoteEnvValue "MOONCEN_OPS_PASSWORD_HASH"
}
if (-not $OpsLoginId -or -not $OpsPasswordHash) {
    throw "Ops login configuration is required. Set MoonCenOpsLoginId and MoonCenOpsPasswordHash, or deploy once with an existing protected remote configuration."
}
if (-not [string]::Equals($OpsLoginId, "opsadmin", [StringComparison]::Ordinal)) {
    throw "OpsLoginId must be the dedicated opsadmin account."
}
$opsHashMatch = [regex]::Match(
    $OpsPasswordHash,
    '^pbkdf2_sha256\$([0-9]{6,7})\$([A-Za-z0-9_-]{16,128})\$([0-9a-f]{64})$'
)
if (
    -not $opsHashMatch.Success -or
    [int64]$opsHashMatch.Groups[1].Value -lt 310000 -or
    [int64]$opsHashMatch.Groups[1].Value -gt 2000000
) {
    throw "OpsPasswordHash must be a supported PBKDF2-HMAC-SHA256 verifier generated by tools/generate_ops_password.py."
}

if (-not $GoogleOAuthClientId) {
    $GoogleOAuthClientId = Get-RemoteEnvValue "GOOGLE_OAUTH_CLIENT_ID"
    if ($GoogleOAuthClientId) {
        Write-Host "Using existing Google OAuth client id from remote .env."
    }
}

if (-not $GoogleOAuthClientSecret) {
    $GoogleOAuthClientSecret = Get-RemoteEnvValue "GOOGLE_OAUTH_CLIENT_SECRET"
    if ($GoogleOAuthClientSecret) {
        Write-Host "Using existing Google OAuth client secret from remote .env."
    }
}

if (-not $NaverOAuthClientId) {
    $NaverOAuthClientId = Get-RemoteEnvValue "NAVER_OAUTH_CLIENT_ID"
    if ($NaverOAuthClientId) {
        Write-Host "Using existing Naver OAuth client id from remote .env."
    }
}

if (-not $NaverOAuthClientSecret) {
    $NaverOAuthClientSecret = Get-RemoteEnvValue "NAVER_OAUTH_CLIENT_SECRET"
    if ($NaverOAuthClientSecret) {
        Write-Host "Using existing Naver OAuth client secret from remote .env."
    }
}

if (-not $BugReportTo) {
    $BugReportTo = Get-RemoteEnvValue "MOONCEN_BUG_REPORT_TO"
}
if (-not $BugReportFrom) {
    $BugReportFrom = Get-RemoteEnvValue "MOONCEN_BUG_REPORT_FROM"
}
if (-not $SmtpHost) {
    $SmtpHost = Get-RemoteEnvValue "MOONCEN_SMTP_HOST"
}
if (-not $SmtpPort) {
    $SmtpPort = Get-RemoteEnvValue "MOONCEN_SMTP_PORT"
}
if (-not $SmtpUsername) {
    $SmtpUsername = Get-RemoteEnvValue "MOONCEN_SMTP_USERNAME"
}
if (-not $SmtpPassword) {
    $SmtpPassword = Get-RemoteEnvValue "MOONCEN_SMTP_PASSWORD"
}
if (-not $SmtpSecurity) {
    $SmtpSecurity = Get-RemoteEnvValue "MOONCEN_SMTP_SECURITY"
}
if (-not $OpsCloudflareAnalyticsZoneId) {
    $OpsCloudflareAnalyticsZoneId = Get-RemoteEnvValue "OPS_CLOUDFLARE_ANALYTICS_ZONE_ID"
}
if (-not $OpsCloudflareAnalyticsToken) {
    $OpsCloudflareAnalyticsToken = Get-RemoteEnvValue "OPS_CLOUDFLARE_ANALYTICS_TOKEN"
}
if (-not $ServerMonitorToken) {
    $ServerMonitorToken = Get-RemoteEnvValue "MOONCEN_SERVER_MONITOR_TOKEN"
}
if (-not $SmtpPort) {
    $SmtpPort = "587"
}
if (-not $SmtpSecurity) {
    $SmtpSecurity = "starttls"
}
if ($SmtpPort -notmatch '^[0-9]+$' -or [int64]$SmtpPort -lt 1 -or [int64]$SmtpPort -gt 65535) {
    throw "SmtpPort must be an integer between 1 and 65535."
}
if ($SmtpSecurity -notin @("starttls", "ssl", "none")) {
    throw "SmtpSecurity must be starttls, ssl, or none."
}
if ([bool]$OpsCloudflareAnalyticsZoneId -xor [bool]$OpsCloudflareAnalyticsToken) {
    throw "Cloudflare analytics zone id and token must be configured together."
}
if ($OpsCloudflareAnalyticsZoneId -and $OpsCloudflareAnalyticsZoneId -notmatch '^[0-9a-f]{32}$') {
    throw "OpsCloudflareAnalyticsZoneId must be an exact lowercase 32-character Cloudflare zone id."
}
if ($OpsCloudflareAnalyticsToken -and ($OpsCloudflareAnalyticsToken.Length -lt 20 -or $OpsCloudflareAnalyticsToken.Length -gt 256 -or $OpsCloudflareAnalyticsToken -notmatch '^[A-Za-z0-9_-]+$')) {
    throw "OpsCloudflareAnalyticsToken has an invalid format."
}
if ($ServerMonitorToken -and ($ServerMonitorToken.Length -lt 32 -or $ServerMonitorToken.Length -gt 256 -or $ServerMonitorToken -notmatch '^[A-Za-z0-9_-]+$')) {
    throw "ServerMonitorToken has an invalid format."
}

$beginNativeIntentScript = @'
set -euo pipefail
intent_token='__NATIVE_INTENT_TOKEN__'
transition_root=/var/lib/mooncen-runtime-transition
transition_lock="$transition_root/control.lock"
[[ "$intent_token" =~ ^[0-9a-f]{32}$ ]] || {
  echo "native deployment intent token is invalid" >&2
  exit 64
}
if sudo test -e "$transition_root" || sudo test -L "$transition_root"; then
  sudo test -d "$transition_root" && ! sudo test -L "$transition_root" &&
    [ "$(sudo stat -c '%U:%G:%a' "$transition_root")" = root:root:700 ] || {
      echo "runtime transition directory is unsafe" >&2
      exit 65
    }
else
  sudo install -d -o root -g root -m 0700 "$transition_root"
fi
if sudo test -L "$transition_lock" ||
   { sudo test -e "$transition_lock" && ! sudo test -f "$transition_lock"; }; then
  echo "runtime transition lock is unsafe" >&2
  exit 65
fi
if ! sudo test -e "$transition_lock"; then
  sudo install -o root -g root -m 0600 /dev/null "$transition_lock"
fi
[ "$(sudo stat -c '%U:%G:%a' "$transition_lock")" = root:root:600 ] || {
  echo "runtime transition lock metadata is unsafe" >&2
  exit 65
}
sudo /usr/bin/flock -x "$transition_lock" /bin/bash -s -- "$intent_token" <<'ROOT'
set -euo pipefail
intent_token="$1"
controller=/usr/local/libexec/mooncen-container-release
transition_root=/var/lib/mooncen-runtime-transition
bootstrap_intent="$transition_root/native-bootstrap-intent.json"
expected="{\"schema_version\":1,\"token\":\"${intent_token}\"}"
if [ ! -e "$controller" ] && [ ! -L "$controller" ]; then
  for partial_runtime in \
    /etc/mooncen/container-bootstrap.json \
    /etc/mooncen/container-runtime-installation.json \
    /var/lib/mooncen-container-release \
    /usr/local/libexec/mooncen-container-release-lib; do
    if [ -e "$partial_runtime" ] || [ -L "$partial_runtime" ]; then
      echo "partial container runtime exists without its root controller" >&2
      exit 65
    fi
  done
  if systemctl is-active --quiet mooncen-container-stack.service ||
     systemctl is-enabled --quiet mooncen-container-stack.service; then
    echo "container stack exists without its root controller" >&2
    exit 65
  fi
  if [ -e /opt/.mooncen-deploy.lock ] || [ -L /opt/.mooncen-deploy.lock ]; then
    echo "another native deployment owns the release lock" >&2
    exit 75
  fi
  if [ -e "$bootstrap_intent" ] || [ -L "$bootstrap_intent" ]; then
    [ -f "$bootstrap_intent" ] && [ ! -L "$bootstrap_intent" ] &&
      [ "$(stat -c '%U:%G:%a' "$bootstrap_intent")" = root:root:600 ] &&
      [ "$(cat "$bootstrap_intent")" = "$expected" ] || {
        echo "another first-bootstrap native deployment owns the runtime fence" >&2
        exit 75
      }
  else
    temporary="$transition_root/.native-bootstrap-intent.$$.tmp"
    trap 'rm -f -- "$temporary"' EXIT HUP INT TERM
    printf '%s\n' "$expected" > "$temporary"
    chown root:root "$temporary"
    chmod 0600 "$temporary"
    sync -f -- "$temporary"
    mv -fT -- "$temporary" "$bootstrap_intent"
    sync -f -- "$transition_root"
    trap - EXIT HUP INT TERM
  fi
  echo "native_intent=first-bootstrap"
  exit 0
fi
[ ! -e "$bootstrap_intent" ] && [ ! -L "$bootstrap_intent" ] || {
  echo "first-bootstrap intent exists beside an installed controller" >&2
  exit 65
}
[ -f "$controller" ] && [ ! -L "$controller" ] &&
  [ "$(stat -c '%U:%G:%a' "$controller")" = root:root:755 ] || {
    echo "container controller is unavailable or unsafe" >&2
    exit 65
  }
output="$("$controller" native-begin "$intent_token")" || {
  echo "container state or transaction blocks native deployment" >&2
  exit 75
}
expected="{\"schema_version\":1,\"token\":\"${intent_token}\"}"
[ "$output" = "$expected" ] && [[ "$output" != *$'\n'* ]] || {
  echo "native deployment intent output is invalid" >&2
  exit 65
}
echo "native_intent=controller"
ROOT
'@
$beginNativeIntentScript = $beginNativeIntentScript.Replace('__NATIVE_INTENT_TOKEN__', $normalizedDeploymentIntentToken)
Write-Host "Acquiring exclusive native/container runtime intent..."
Invoke-RemoteBashScriptTty $beginNativeIntentScript
$nativeIntentFenceEstablished = $true

$remoteDeployLockAcquired = $false
$lockAndCleanupScript = @'
set -euo pipefail
umask 0077
lock_dir=/opt/.mooncen-deploy.lock
lock_token='__LOCK_TOKEN__'
native_intent_token='__NATIVE_INTENT_TOKEN__'
deploy_user='__DEPLOY_USER__'
lock_stage="/opt/.mooncen-deploy-lock-${lock_token}.staged"
heartbeat="/opt/.mooncen-deploy-heartbeat-${lock_token}"
current_boot_id="$(cat /proc/sys/kernel/random/boot_id)"
now_epoch="$(date +%s)"

clear_native_bootstrap_intent() {
  local token="$1"
  local transition_root=/var/lib/mooncen-runtime-transition
  local transition_lock="$transition_root/control.lock"
  [[ "$token" =~ ^[0-9a-f]{32}$ ]] || return 65
  if ! sudo test -e "$transition_root" && ! sudo test -L "$transition_root"; then
    return 0
  fi
  sudo test -d "$transition_root" && ! sudo test -L "$transition_root" &&
    [ "$(sudo stat -c '%U:%G:%a' "$transition_root")" = root:root:700 ] &&
    sudo test -f "$transition_lock" && ! sudo test -L "$transition_lock" &&
    [ "$(sudo stat -c '%U:%G:%a' "$transition_lock")" = root:root:600 ] || return 65
  sudo /usr/bin/flock -x "$transition_lock" /bin/bash -s -- "$token" <<'ROOT'
set -euo pipefail
token="$1"
root=/var/lib/mooncen-runtime-transition
intent="$root/native-bootstrap-intent.json"
expected="{\"schema_version\":1,\"token\":\"${token}\"}"
if [ ! -e "$intent" ] && [ ! -L "$intent" ]; then
  exit 0
fi
[ -f "$intent" ] && [ ! -L "$intent" ] &&
  [ "$(stat -c '%U:%G:%a' "$intent")" = root:root:600 ] &&
  [ "$(cat "$intent")" = "$expected" ] || exit 65
rm -f -- "$intent"
sync -f -- "$root"
ROOT
}

end_native_intent() {
  local token="$1"
  local controller=/usr/local/libexec/mooncen-container-release
  local output expected
  [[ "$token" =~ ^[0-9a-f]{32}$ ]] || return 65
  if [ ! -e "$controller" ] && [ ! -L "$controller" ]; then
    ! sudo test -e /etc/mooncen/container-runtime-installation.json &&
      ! sudo test -L /etc/mooncen/container-runtime-installation.json &&
      ! sudo test -e /var/lib/mooncen-container-release &&
      ! sudo test -L /var/lib/mooncen-container-release || return 65
    clear_native_bootstrap_intent "$token" || return $?
    return 0
  fi
  sudo test -f "$controller" && ! sudo test -L "$controller" &&
    [ "$(sudo stat -c '%U:%G:%a' "$controller")" = root:root:755 ] || return 65
  output="$(sudo -n -- "$controller" native-end "$token")" || return 75
  expected="{\"ended\":true,\"schema_version\":1,\"token\":\"${token}\"}"
  [ "$output" = "$expected" ] && [[ "$output" != *$'\n'* ]]
}

release_lock_on_error() {
  local cleanup_status=$?
  trap - EXIT HUP INT TERM
  if [ "$cleanup_status" -ne 0 ]; then
    if sudo test -d "$lock_dir" && ! sudo test -L "$lock_dir" &&
       [ "$(sudo cat "$lock_dir/token" 2>/dev/null || true)" = "$lock_token" ]; then
      sudo rm -f -- "$heartbeat" >/dev/null 2>&1 || true
      sudo rm -rf -- "$lock_dir" >/dev/null 2>&1 || true
    fi
    if sudo test -d "$lock_stage" && ! sudo test -L "$lock_stage"; then
      sudo rm -rf -- "$lock_stage" >/dev/null 2>&1 || true
    fi
    sudo sync -f -- /opt >/dev/null 2>&1 || true
    if ! end_native_intent "$native_intent_token"; then
      echo "native deployment intent remains fenced after pre-guard failure" >&2
      cleanup_status=75
    fi
  fi
  exit "$cleanup_status"
}
trap release_lock_on_error EXIT
trap 'exit 130' HUP INT TERM

[[ "$lock_token" =~ ^[0-9a-f]{32}$ ]] &&
  [[ "$native_intent_token" =~ ^[0-9a-f]{32}$ ]] &&
  [[ "$deploy_user" =~ ^[A-Za-z_][A-Za-z0-9_-]{0,63}$ ]] &&
  id "$deploy_user" >/dev/null 2>&1 &&
  [[ "$current_boot_id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] || {
    echo "deployment lock preflight inputs are unsafe" >&2
    exit 65
  }

root_manifest_value() {
  local manifest="$1" key="$2"
  sudo awk -F= -v wanted="$key" '
    $1 == wanted { count++; value=substr($0, length(wanted) + 2) }
    END { if (count != 1) exit 1; print value }
  ' "$manifest"
}

state_is_stale() {
  local recorded_boot="$1" deadline="$2"
  [[ "$recorded_boot" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] &&
    [[ "$deadline" =~ ^[0-9]{10,12}$ ]] || return 65
  [ "$recorded_boot" != "$current_boot_id" ] || [ "$now_epoch" -ge "$deadline" ]
}

validate_raw_preguard_shape() {
  local entry name mode
  sudo test -d "$lock_dir" && ! sudo test -L "$lock_dir" &&
    [ "$(sudo stat -c '%U:%G:%a' "$lock_dir")" = root:root:700 ] || return 65
  ! sudo mountpoint -q "$lock_dir" || return 65
  if sudo find "$lock_dir" -xdev -mindepth 1 \( -type l -o \( ! -type d ! -type f \) \) -print -quit |
     grep -q .; then
    return 65
  fi
  if sudo find "$lock_dir" -xdev -mindepth 1 -type d -exec mountpoint -q {} \; -print -quit |
     grep -q .; then
    return 65
  fi
  while IFS= read -r entry; do
    [ -n "$entry" ] || continue
    name="$(basename "$entry")"
    case "$name" in
      token|preflight.env|guard.sh|operation.lock|systemd-unit-names|systemd-enabled-units|systemd-unit-metadata|systemd-dropin-metadata|systemd-units|systemd-dropins)
        ;;
      .bootstrap.*.tmp)
        [[ "$name" =~ ^\.bootstrap\.[0-9]+\.tmp$ ]] || return 65
        ;;
      *) return 65 ;;
    esac
    [ "$(sudo stat -c '%u' "$entry")" = 0 ] || return 65
    mode="$(sudo stat -c '%a' "$entry")"
    (( (8#$mode & 8#022) == 0 )) || return 65
    if sudo test -d "$entry" && sudo mountpoint -q "$entry"; then
      return 65
    fi
  done < <(sudo find "$lock_dir" -mindepth 1 -maxdepth 1 -print | LC_ALL=C sort)
}

preserve_raw_candidate() {
  local old_token="$1"
  local release_dir="/opt/.mooncen-release-${old_token}"
  local previous_dir="/opt/.mooncen-previous-${old_token}"
  local failed_dir="/opt/.mooncen-failed-${old_token}"
  local history_root=/opt/.mooncen-release-history
  local history_entry="$history_root/$old_token"
  local destination="$history_entry/preflight-unactivated"
  [ ! -e "$previous_dir" ] && [ ! -L "$previous_dir" ] &&
    [ ! -e "$failed_dir" ] && [ ! -L "$failed_dir" ] || return 65
  if [ -e "$release_dir" ] || [ -L "$release_dir" ]; then
    [ -d "$release_dir" ] && [ ! -L "$release_dir" ] || return 65
  fi
  if sudo test -e "$history_root" || sudo test -L "$history_root"; then
    sudo test -d "$history_root" && ! sudo test -L "$history_root" &&
      [ "$(sudo stat -c '%U:%G:%a' "$history_root")" = root:root:700 ] || return 65
  else
    sudo install -d -o root -g root -m 0700 "$history_root"
  fi
  if sudo test -e "$history_entry" || sudo test -L "$history_entry"; then
    sudo test -d "$history_entry" && ! sudo test -L "$history_entry" &&
      [ "$(sudo stat -c '%U:%G:%a' "$history_entry")" = root:root:700 ] || return 65
  else
    sudo install -d -o root -g root -m 0700 "$history_entry"
  fi
  if [ -e "$release_dir" ] || [ -L "$release_dir" ]; then
    if sudo test -e "$destination" || sudo test -L "$destination"; then
      return 65
    fi
    sudo mv -T -- "$release_dir" "$destination"
    sudo sync -f -- /opt
  elif sudo test -e "$destination" || sudo test -L "$destination"; then
    sudo test -d "$destination" && ! sudo test -L "$destination" || return 65
  fi
}

reclaim_raw_preguard_lock() {
  local expected_old_token="$1"
  local original_lock_mtime="$2"
  local actual_token="" state_manifest state_token state_boot state_deadline state_intent_token="" boot_epoch direct_name
  [[ "$original_lock_mtime" =~ ^[0-9]{10,12}$ ]] || return 65
  sudo test -d "$lock_dir" && ! sudo test -L "$lock_dir" &&
    [ "$(sudo stat -c '%U:%G:%a' "$lock_dir")" = root:root:700 ] || return 65
  if sudo test -e "$lock_dir/journal.env" || sudo test -L "$lock_dir/journal.env" ||
     sudo test -e "$lock_dir/bootstrap.env" || sudo test -L "$lock_dir/bootstrap.env"; then
    return 75
  fi
  if sudo test -e "$lock_dir/token" || sudo test -L "$lock_dir/token"; then
    sudo test -f "$lock_dir/token" && ! sudo test -L "$lock_dir/token" &&
      [ "$(sudo stat -c '%U:%G:%a' "$lock_dir/token")" = root:root:600 ] || return 65
    actual_token="$(sudo cat "$lock_dir/token")"
    [[ "$actual_token" =~ ^[0-9a-f]{32}$ ]] || return 65
  fi
  if [ "$expected_old_token" = - ]; then
    [ -z "$actual_token" ] || return 65
  else
    [ "$actual_token" = "$expected_old_token" ] || return 75
  fi

  if sudo test -e "$lock_dir/preflight.env" || sudo test -L "$lock_dir/preflight.env"; then
    [ -n "$actual_token" ] || return 65
    state_manifest="$lock_dir/preflight.env"
    sudo test -f "$state_manifest" && ! sudo test -L "$state_manifest" &&
      [ "$(sudo stat -c '%U:%G:%a' "$state_manifest")" = root:root:600 ] || return 65
    [ "$(root_manifest_value "$state_manifest" VERSION)" = 1 ] || return 65
    state_token="$(root_manifest_value "$state_manifest" TOKEN)" || return 65
    state_boot="$(root_manifest_value "$state_manifest" BOOT_ID)" || return 65
    state_deadline="$(root_manifest_value "$state_manifest" DEADLINE_EPOCH)" || return 65
    state_intent_token="$(root_manifest_value "$state_manifest" NATIVE_INTENT_TOKEN)" || return 65
    [ "$state_token" = "$actual_token" ] &&
      [[ "$state_boot" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] &&
      [[ "$state_deadline" =~ ^[0-9]{10,12}$ ]] &&
      [[ "$state_intent_token" =~ ^[0-9a-f]{32}$ ]] &&
      [ "$(root_manifest_value "$state_manifest" RELEASE_DIR)" = "/opt/.mooncen-release-${actual_token}" ] || return 65
    # A raw pre-guard owner has no always-on watcher. Deadline-only takeover on
    # the same boot could steal an active long package/browser step, so reclaim
    # this state automatically only after a boot-id change.
    [ "$state_boot" != "$current_boot_id" ] || return 73
  else
    boot_epoch="$(awk '$1=="btime" {print $2}' /proc/stat)"
    [[ "$boot_epoch" =~ ^[0-9]{10,12}$ ]] || return 65
    [ "$original_lock_mtime" -lt "$boot_epoch" ] || return 73
  fi

  validate_raw_preguard_shape || return 65
  if [ -n "$actual_token" ]; then
    preserve_raw_candidate "$actual_token" || return 65
    sudo rm -f -- "/opt/.mooncen-deploy-heartbeat-${actual_token}"
    # Current deployments publish preflight.env in the staging directory
    # before the lock-directory rename, so a visible current lock always has
    # this token. A token-only lock can only predate native intent fencing.
    if [ -n "$state_intent_token" ]; then
      end_native_intent "$state_intent_token" || return $?
    fi
  else
    # flock created operation.lock. A legacy lock without a token is safe to
    # remove only when it contains no other entry at all.
    while IFS= read -r direct_name; do
      [ "$direct_name" = operation.lock ] || return 65
    done < <(sudo find "$lock_dir" -mindepth 1 -maxdepth 1 -printf '%f\n')
  fi
  sudo rm -rf -- "$lock_dir"
  sudo sync -f -- /opt
  echo "reclaimed a stale pre-guard deployment lock${actual_token:+ from $actual_token}" >&2
}

converge_existing_lock() {
  local actual_token state_manifest state_boot state_deadline state_token status raw_lock_mtime
  sudo test -d "$lock_dir" && ! sudo test -L "$lock_dir" &&
    [ "$(sudo stat -c '%U:%G:%a' "$lock_dir")" = root:root:700 ] || return 65
  if sudo test -e "$lock_dir/token" || sudo test -L "$lock_dir/token"; then
    sudo test -f "$lock_dir/token" && ! sudo test -L "$lock_dir/token" &&
      [ "$(sudo stat -c '%U:%G:%a' "$lock_dir/token")" = root:root:600 ] || return 65
    actual_token="$(sudo cat "$lock_dir/token")"
    [[ "$actual_token" =~ ^[0-9a-f]{32}$ ]] || return 65
  else
    raw_lock_mtime="$(sudo stat -c '%Y' "$lock_dir")"
    sudo flock -x "$lock_dir/operation.lock" /bin/bash "$0" __reclaim_raw - "$raw_lock_mtime"
    return $?
  fi

  if sudo test -e "$lock_dir/journal.env" || sudo test -L "$lock_dir/journal.env"; then
    state_manifest="$lock_dir/journal.env"
    sudo test -f "$state_manifest" && ! sudo test -L "$state_manifest" &&
      [ "$(sudo stat -c '%U:%G:%a' "$state_manifest")" = root:root:600 ] || return 65
    state_token="$(root_manifest_value "$state_manifest" TOKEN)" || return 65
    state_boot="$(root_manifest_value "$state_manifest" ARM_BOOT_ID)" || return 65
    state_deadline="$(root_manifest_value "$state_manifest" DEADLINE_EPOCH)" || return 65
    [ "$state_token" = "$actual_token" ] || return 65
    if state_is_stale "$state_boot" "$state_deadline"; then
      :
    else
      status=$?
      [ "$status" -eq 1 ] && return 73
      return 65
    fi
    sudo test -f "$lock_dir/guard.sh" && ! sudo test -L "$lock_dir/guard.sh" &&
      [ "$(sudo stat -c '%U:%G:%a' "$lock_dir/guard.sh")" = root:root:700 ] || return 65
    echo "recovering a stale journaled deployment before acquiring a new lock" >&2
    sudo "$lock_dir/guard.sh" recover "$lock_dir" "$actual_token" || return 75
    sudo test ! -e "$lock_dir" && ! sudo test -L "$lock_dir" || return 75
    return 0
  fi

  if sudo test -e "$lock_dir/bootstrap.env" || sudo test -L "$lock_dir/bootstrap.env"; then
    state_manifest="$lock_dir/bootstrap.env"
    sudo test -f "$state_manifest" && ! sudo test -L "$state_manifest" &&
      [ "$(sudo stat -c '%U:%G:%a' "$state_manifest")" = root:root:600 ] || return 65
    state_token="$(root_manifest_value "$state_manifest" TOKEN)" || return 65
    state_boot="$(root_manifest_value "$state_manifest" BOOT_ID)" || return 65
    state_deadline="$(root_manifest_value "$state_manifest" DEADLINE_EPOCH)" || return 65
    [ "$state_token" = "$actual_token" ] || return 65
    if state_is_stale "$state_boot" "$state_deadline"; then
      :
    else
      status=$?
      [ "$status" -eq 1 ] && return 73
      return 65
    fi
    sudo test -f "$lock_dir/guard.sh" && ! sudo test -L "$lock_dir/guard.sh" &&
      [ "$(sudo stat -c '%U:%G:%a' "$lock_dir/guard.sh")" = root:root:700 ] || return 65
    echo "aborting a stale deployment bootstrap before acquiring a new lock" >&2
    sudo "$lock_dir/guard.sh" abort-bootstrap "$lock_dir" "$actual_token" || return 75
    if sudo test -e "$lock_dir" || sudo test -L "$lock_dir"; then
      # arm may have won operation.lock and published journal.env. Re-evaluate
      # the now-authoritative state on the next acquisition attempt.
      return 75
    fi
    return 0
  fi

  raw_lock_mtime="$(sudo stat -c '%Y' "$lock_dir")"
  sudo flock -x "$lock_dir/operation.lock" /bin/bash "$0" __reclaim_raw "$actual_token" "$raw_lock_mtime"
}

if [ "${1:-}" = __reclaim_raw ]; then
  trap - EXIT HUP INT TERM
  [ "$#" -eq 3 ] || exit 65
  sudo test -f "$lock_dir/operation.lock" &&
    ! sudo test -L "$lock_dir/operation.lock" &&
    [ "$(sudo stat -c '%U:%G' "$lock_dir/operation.lock")" = root:root ] || exit 65
  sudo chmod 0600 "$lock_dir/operation.lock"
  [ "$(sudo stat -c '%U:%G:%a' "$lock_dir/operation.lock")" = root:root:600 ] || exit 65
  reclaim_raw_preguard_lock "$2" "$3"
  exit $?
fi

if sudo test -e "$lock_dir" || sudo test -L "$lock_dir"; then
  if converge_existing_lock; then
    :
  else
    status=$?
    if [ "$status" -eq 73 ]; then
      echo "another deployment holds $lock_dir; the same-boot owner is not eligible for automatic takeover" >&2
      echo "wait for the original deployment; if it is no longer running, reboot the target and retry; do not remove the lock manually" >&2
    else
      echo "stale deployment lock could not be safely converged" >&2
    fi
    exit "$status"
  fi
fi

[ ! -e "$lock_stage" ] && [ ! -L "$lock_stage" ] || {
  echo "deployment lock staging path already exists" >&2
  exit 65
}
sudo mkdir -m 0700 -- "$lock_stage"
printf '%s\n' "$lock_token" | sudo tee "$lock_stage/token" >/dev/null
sudo chown root:root "$lock_stage/token"
sudo chmod 0600 "$lock_stage/token"
preflight_stage="$lock_stage/.preflight.$$.tmp"
{
  printf 'VERSION=1\n'
  printf 'TOKEN=%s\n' "$lock_token"
  printf 'BOOT_ID=%s\n' "$current_boot_id"
  printf 'DEADLINE_EPOCH=%s\n' "$((now_epoch + 21600))"
  printf 'RELEASE_DIR=/opt/.mooncen-release-%s\n' "$lock_token"
  printf 'NATIVE_INTENT_TOKEN=%s\n' "$native_intent_token"
} | sudo tee "$preflight_stage" >/dev/null
sudo chown root:root "$preflight_stage"
sudo chmod 0600 "$preflight_stage"
sudo sync -f -- "$lock_stage"
sudo mv -fT -- "$preflight_stage" "$lock_stage/preflight.env"
sudo sync -f -- "$lock_stage"
sudo mv -nT -- "$lock_stage" "$lock_dir"
if sudo test -e "$lock_stage" || sudo test -L "$lock_stage"; then
  sudo rm -rf -- "$lock_stage"
  echo "another deployment won the atomic lock acquisition race" >&2
  echo "wait for the original deployment; do not remove the live lock manually" >&2
  exit 73
fi
sudo sync -f -- /opt
sudo install -o "$deploy_user" -g "$(id -gn "$deploy_user")" -m 0600 /dev/null "$heartbeat"
sudo sync -f -- /opt

recovery_blocked=0
for stale_path in /opt/.mooncen-previous-* /opt/.mooncen-failed-*; do
  [ -e "$stale_path" ] || [ -L "$stale_path" ] || continue
  if [[ ! "$stale_path" =~ ^/opt/\.mooncen-(previous|failed)-[0-9a-f]{32}$ ]] ||
     [ ! -d "$stale_path" ] || [ -L "$stale_path" ]; then
    echo "unsafe stale release path requires manual review: $stale_path" >&2
    exit 65
  fi
  echo "preserving recovery release; resolve it before a new deployment: $stale_path" >&2
  recovery_blocked=1
done
if [ "$recovery_blocked" -ne 0 ]; then
  exit 75
fi
for stale_path in /opt/.mooncen-release-*; do
  [ -e "$stale_path" ] || [ -L "$stale_path" ] || continue
  if [ "$stale_path" = /opt/.mooncen-release-history ]; then
    sudo test -d "$stale_path" && ! sudo test -L "$stale_path" &&
      [ "$(sudo stat -c '%U:%G:%a' "$stale_path")" = root:root:700 ] || {
        echo "release history path is unsafe: $stale_path" >&2
        exit 65
      }
    continue
  fi
  if [[ ! "$stale_path" =~ ^/opt/\.mooncen-release-[0-9a-f]{32}$ ]] ||
     [ ! -d "$stale_path" ] || [ -L "$stale_path" ]; then
    echo "unsafe stale release path requires manual review: $stale_path" >&2
    exit 65
  fi
  echo "preserving an unactivated release candidate: $stale_path" >&2
done
trap - EXIT HUP INT TERM
'@
$lockAndCleanupScript = $lockAndCleanupScript.Replace('__LOCK_TOKEN__', $releaseId).Replace('__NATIVE_INTENT_TOKEN__', $normalizedDeploymentIntentToken).Replace('__DEPLOY_USER__', $User)
try {
    Invoke-RemoteBashScriptTty $lockAndCleanupScript
} catch {
    $lockFailure = $_
    $lockFailureExitCode = $script:DeploymentRemoteExitCode
    $releaseUnownedNativeIntentScript = @'
set -euo pipefail
intent_token='__NATIVE_INTENT_TOKEN__'
controller=/usr/local/libexec/mooncen-container-release
clear_first_intent() {
  local token="$1" root=/var/lib/mooncen-runtime-transition
  local lock="$root/control.lock"
  [[ "$token" =~ ^[0-9a-f]{32}$ ]] || return 65
  if ! sudo test -e "$root" && ! sudo test -L "$root"; then return 0; fi
  sudo test -d "$root" && ! sudo test -L "$root" &&
    [ "$(sudo stat -c '%U:%G:%a' "$root")" = root:root:700 ] &&
    sudo test -f "$lock" && ! sudo test -L "$lock" &&
    [ "$(sudo stat -c '%U:%G:%a' "$lock")" = root:root:600 ] || return 65
  sudo /usr/bin/flock -x "$lock" /bin/bash -s -- "$token" <<'ROOT'
set -euo pipefail
token="$1"; root=/var/lib/mooncen-runtime-transition
intent="$root/native-bootstrap-intent.json"
expected="{\"schema_version\":1,\"token\":\"${token}\"}"
if [ ! -e "$intent" ] && [ ! -L "$intent" ]; then exit 0; fi
[ -f "$intent" ] && [ ! -L "$intent" ] &&
  [ "$(stat -c '%U:%G:%a' "$intent")" = root:root:600 ] &&
  [ "$(cat "$intent")" = "$expected" ] || exit 65
rm -f -- "$intent"; sync -f -- "$root"
ROOT
}
if sudo test -e /opt/.mooncen-deploy.lock || sudo test -L /opt/.mooncen-deploy.lock; then
  echo "native deployment lock may still own the intent" >&2
  exit 75
fi
if [ ! -e "$controller" ] && [ ! -L "$controller" ]; then
  ! sudo test -e /etc/mooncen/container-runtime-installation.json &&
    ! sudo test -L /etc/mooncen/container-runtime-installation.json &&
    ! sudo test -e /var/lib/mooncen-container-release &&
    ! sudo test -L /var/lib/mooncen-container-release || exit 65
  clear_first_intent "$intent_token"
  exit 0
fi
sudo test -f "$controller" && ! sudo test -L "$controller" &&
  [ "$(sudo stat -c '%U:%G:%a' "$controller")" = root:root:755 ] || exit 65
output="$(sudo -n -- "$controller" native-end "$intent_token")"
expected="{\"ended\":true,\"schema_version\":1,\"token\":\"${intent_token}\"}"
[ "$output" = "$expected" ] && [[ "$output" != *$'\n'* ]]
'@
    $releaseUnownedNativeIntentScript = $releaseUnownedNativeIntentScript.Replace('__NATIVE_INTENT_TOKEN__', $normalizedDeploymentIntentToken)
    try {
        Invoke-RemoteBashScriptTty $releaseUnownedNativeIntentScript -SkipGuardHeartbeat
        $nativeIntentFenceEstablished = $false
    } catch {
        Write-Warning "Native runtime intent remains fenced because remote lock ownership is not terminal."
    }
    if (Get-DeploymentRemoteErrorCode $lockFailureExitCode) {
        Write-DeploymentFailureMarker $lockFailureExitCode
        exit $lockFailureExitCode
    }
    throw $lockFailure
}
$remoteDeployLockAcquired = $true

$deploymentFailure = $null
$deploymentFailureExitCode = 0
try {
Write-Host "Deploying MoonCen to ${remote}:${RemoteDir}"
Write-Host "Service domain: $Domain"
Write-Host "Node role: $NodeRole"
Write-Host "Crawler owner: $EnableCrawler"
$nativeWebStartScript = @'
set -euo pipefail
lock_dir='__LOCK_DIR__'
lock_token='__LOCK_TOKEN__'
guard="$lock_dir/guard.sh"
[ "$lock_dir" = /opt/.mooncen-deploy.lock ] &&
  [[ "$lock_token" =~ ^[0-9a-f]{32}$ ]] &&
  sudo test -f "$guard" && ! sudo test -L "$guard" || {
    echo "durable deployment guard is unavailable for native service start" >&2
    exit 65
  }
cleanup_native_start() {
  local status=$?
  trap - EXIT HUP INT TERM
  sudo "$guard" revoke-start "$lock_dir" "$lock_token" || status=75
  exit "$status"
}
sudo "$guard" authorize-start "$lock_dir" "$lock_token" candidate
trap cleanup_native_start EXIT
trap 'exit 130' HUP INT TERM
sudo systemctl enable mooncen-api mooncen-frontend
sudo systemctl restart mooncen-api mooncen-frontend
trap - EXIT HUP INT TERM
sudo "$guard" revoke-start "$lock_dir" "$lock_token"
'@
$nativeWebStartScript = $nativeWebStartScript.Replace('__LOCK_DIR__', $remoteDeployLock).Replace('__LOCK_TOKEN__', $releaseId)

$nativeAiStartScript = @'
set -euo pipefail
lock_dir='__LOCK_DIR__'
lock_token='__LOCK_TOKEN__'
guard="$lock_dir/guard.sh"
[ "$lock_dir" = /opt/.mooncen-deploy.lock ] &&
  [[ "$lock_token" =~ ^[0-9a-f]{32}$ ]] &&
  sudo test -f "$guard" && ! sudo test -L "$guard" || {
    echo "durable deployment guard is unavailable for native worker start" >&2
    exit 65
  }
cleanup_native_start() {
  local status=$?
  trap - EXIT HUP INT TERM
  sudo "$guard" revoke-start "$lock_dir" "$lock_token" || status=75
  exit "$status"
}
sudo "$guard" authorize-start "$lock_dir" "$lock_token" candidate
trap cleanup_native_start EXIT
trap 'exit 130' HUP INT TERM
sudo systemctl enable mooncen-ai-worker
sudo systemctl restart mooncen-ai-worker
trap - EXIT HUP INT TERM
sudo "$guard" revoke-start "$lock_dir" "$lock_token"
'@
$nativeAiStartScript = $nativeAiStartScript.Replace('__LOCK_DIR__', $remoteDeployLock).Replace('__LOCK_TOKEN__', $releaseId)

if ($Standby) {
    Write-Host "Standby mode: DB setup and service startup will be skipped."
}

# Deploy only the immutable Git snapshot. Revalidate immediately before
# archiving so a concurrent local edit or commit cannot change the snapshot
# after the early preflight.
if (Test-Path $archivePath) {
    Remove-Item -LiteralPath $archivePath -Force
}

Push-Location $projectRoot
try {
    $currentDeployCommit = Get-ValidatedDeployCommit
    if ($currentDeployCommit -ne $deployCommit) {
        throw "Git HEAD changed during deployment preflight. Run the deployment again from a stable clean snapshot."
    }
    Assert-ExactExpectedDeployCommit $currentDeployCommit "archive creation"

    & git -C $script:DeploymentGitRepositoryRoot archive --format=tar.gz --output="$archivePath" "$deployCommit"
    if ($LASTEXITCODE -ne 0) {
        throw "git archive failed"
    }
} finally {
    Pop-Location
}
$archiveSha256 = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($archiveSha256 -notmatch '^[0-9a-f]{64}$') {
    throw "Unable to calculate a valid SHA-256 digest for the deployment artifact."
}

Write-Host "Uploading immutable deployment artifact from commit $deployCommit (sha256=$archiveSha256)..."
# Bootstrap the private release directory with the small sudo command set used
# by older standby hosts.  The full root-owned helper allowlist is refreshed by
# setup_project.sh later in the deployment.
Invoke-RemoteTty "sudo -n /bin/mkdir -- '$remoteReleaseDir' && sudo -n /bin/chown '$User' '$remoteReleaseDir' && sudo -n /bin/chmod 0700 '$remoteReleaseDir'"
scp @sshBaseArgs $archivePath "${remote}:$remoteArchivePath"
if ($LASTEXITCODE -ne 0) {
    throw "scp upload failed"
}
Invoke-RemoteTty "printf '%s  %s\n' '$archiveSha256' '$remoteArchivePath' | sha256sum --check --strict"
Remove-Item -LiteralPath $archivePath -Force -ErrorAction SilentlyContinue

$extractReleaseScript = @'
set -euo pipefail
release_dir='__RELEASE_DIR__'
archive_path='__ARCHIVE_PATH__'

if [[ ! "$release_dir" =~ ^/opt/\.mooncen-release-[0-9a-f]{32}$ ]] ||
   [ "$archive_path" != "$release_dir/release.tar.gz" ] ||
   [ -L "$release_dir" ]; then
  echo "unsafe MoonCen release staging path" >&2
  exit 64
fi
cleanup_failed_extract() {
  local status=$?
  trap - EXIT HUP INT TERM
  if [ "$status" -ne 0 ] && [ -d "$release_dir" ] && [ ! -L "$release_dir" ]; then
    rm -rf -- "$release_dir"
  fi
  exit "$status"
}
trap cleanup_failed_extract EXIT
trap 'exit 130' HUP INT TERM

# The deploy account may inherit a collaborative umask (for example 0002).
# Git archives commonly carry regular-file mode 0664, so GNU tar's
# --no-same-permissions still needs an explicit safe extraction umask.
umask 0022
tar --extract --gzip --file "$archive_path" --directory "$release_dir" \
  --no-same-owner --no-same-permissions
rm -f -- "$archive_path"

for required in \
  requirements.lock \
  frontend2/package-lock.json \
  deploy/ubuntu/setup_project.sh \
  deploy/ubuntu/install_sudoers.sh \
  deploy/ubuntu/ops_service_helper.sh \
  deploy/ubuntu/mooncen_release_guard.sh \
  deploy/ubuntu/mooncen_prebuild_release.sh \
  deploy/ubuntu/systemd/mooncen-api.service \
  deploy/ubuntu/systemd/mooncen-deploy-guard@.service \
  DB/provision_login_roles.sql \
  DB/provision_deployment_worker_login.sql \
  tools/apply_staging_batch.py \
  tools/ops_service_action.py; do
  if [ ! -f "$release_dir/$required" ] || [ -L "$release_dir/$required" ]; then
    echo "release artifact is missing a regular required file: $required" >&2
    exit 66
  fi
done
for forbidden in .env .venv deploy.local.ps1; do
  if [ -e "$release_dir/$forbidden" ] || [ -L "$release_dir/$forbidden" ]; then
    echo "release artifact contains forbidden mutable/local path: $forbidden" >&2
    exit 65
  fi
done
if find "$release_dir" -xdev -type l -print -quit | grep -q .; then
  echo "release artifact must not contain symbolic links" >&2
  exit 65
fi
find "$release_dir" -xdev -type f \( -name '*.sh' -o -name '*.service' -o -name '*.timer' \) \
  -exec sed -i 's/\r$//' {} +
trap - EXIT HUP INT TERM
'@
$extractReleaseScript = $extractReleaseScript.Replace('__RELEASE_DIR__', $remoteReleaseDir).Replace('__ARCHIVE_PATH__', $remoteArchivePath)
Invoke-RemoteBashScriptTty $extractReleaseScript

if (-not $SkipSystemPackages) {
    Write-Host "Installing Ubuntu system packages..."
    Invoke-RemoteTty "cd '$remoteReleaseDir' && chmod +x deploy/ubuntu/install_system_packages.sh && ./deploy/ubuntu/install_system_packages.sh"
}
if ($EnableCrawler) {
    Write-Host "Reconciling crawler browser runtime..."
    Invoke-RemoteTty "cd '$remoteReleaseDir' && export MOONCEN_INSTALL_LIBRARY_ONLY=1 && source deploy/ubuntu/install_system_packages.sh && reconcile_installed_browser"
}

$armReleaseGuardScript = @'
set -euo pipefail
lock_dir='__LOCK_DIR__'
lock_token='__LOCK_TOKEN__'
remote_dir='__REMOTE_DIR__'
release_dir='__RELEASE_DIR__'
previous_dir='__PREVIOUS_DIR__'
failed_dir='__FAILED_DIR__'
expected_commit='__DEPLOY_COMMIT__'
deploy_user='__DEPLOY_USER__'
guard_source="$release_dir/deploy/ubuntu/mooncen_release_guard.sh"

if [ "$lock_dir" != "/opt/.mooncen-deploy.lock" ] ||
   [[ ! "$lock_token" =~ ^[0-9a-f]{32}$ ]] ||
   [ ! -f "$guard_source" ] || [ -L "$guard_source" ]; then
  echo "durable deployment guard sources or paths are unsafe" >&2
  exit 65
fi

assert_lock_ownership() {
  [ "$(sudo cat "$lock_dir/token" 2>/dev/null || true)" = "$lock_token" ] &&
    sudo test -f "$lock_dir/preflight.env" && ! sudo test -L "$lock_dir/preflight.env" &&
    [ "$(sudo stat -c '%U:%G:%a' "$lock_dir/preflight.env")" = root:root:600 ] &&
    [ "$(sudo awk -F= '$1=="TOKEN" {count++; value=$2} END {if(count!=1) exit 1; print value}' "$lock_dir/preflight.env")" = "$lock_token" ] || {
      echo "deployment lock ownership or preflight fence changed" >&2
      exit 73
    }
}

guard_installed=0
recover_failed_arm() {
  local status=$?
  trap - EXIT HUP INT TERM
  if [ "$status" -ne 0 ]; then
    if [ "$(sudo cat "$lock_dir/token" 2>/dev/null || true)" != "$lock_token" ]; then
      echo "deployment lock ownership changed; refusing to mutate the new owner's recovery state" >&2
    elif sudo test -e "$lock_dir/journal.env" || sudo test -L "$lock_dir/journal.env"; then
      if ! { sudo test -f "$lock_dir/journal.env" && ! sudo test -L "$lock_dir/journal.env"; }; then
        echo "durable release journal is unsafe; recovery remains blocked in $lock_dir" >&2
      elif ! sudo "$lock_dir/guard.sh" recover "$lock_dir" "$lock_token"; then
        echo "durable release recovery remains pending in $lock_dir" >&2
      fi
    elif sudo test -e "$lock_dir/bootstrap.env" || sudo test -L "$lock_dir/bootstrap.env"; then
      if ! { sudo test -f "$lock_dir/bootstrap.env" && ! sudo test -L "$lock_dir/bootstrap.env"; }; then
        echo "durable release bootstrap is unsafe; recovery remains blocked in $lock_dir" >&2
      elif ! sudo "$lock_dir/guard.sh" abort-bootstrap "$lock_dir" "$lock_token"; then
        echo "durable bootstrap recovery remains pending in $lock_dir" >&2
      fi
    elif [ "$guard_installed" = "1" ]; then
      # bootstrap failed before publishing any recovery state, so the only
      # durable artifact owned by this script is the exact guard copy.
      sudo rm -f -- "$lock_dir/guard.sh"
    fi
  fi
  exit "$status"
}
trap recover_failed_arm EXIT
trap 'exit 130' HUP INT TERM
assert_lock_ownership
sudo install -o root -g root -m 0700 "$guard_source" "$lock_dir/guard.sh"
guard_installed=1
sudo "$lock_dir/guard.sh" bootstrap \
  "$lock_dir" "$lock_token" "$release_dir" "$deploy_user" 900 '__NATIVE_INTENT_TOKEN__'
assert_lock_ownership
sudo test -f "$lock_dir/bootstrap.env" && ! sudo test -L "$lock_dir/bootstrap.env" &&
  [ "$(sudo awk -F= '$1=="TOKEN" {count++; value=$2} END {if(count!=1) exit 1; print value}' "$lock_dir/bootstrap.env")" = "$lock_token" ] || {
    echo "deployment bootstrap fence was not published for this token" >&2
    exit 73
  }
assert_lock_ownership
sudo "$lock_dir/guard.sh" arm \
  "$lock_dir" "$lock_token" "$remote_dir" "$release_dir" "$previous_dir" "$failed_dir" \
  "$expected_commit" "$deploy_user" 21600
trap - EXIT HUP INT TERM
'@
$armReleaseGuardScript = $armReleaseGuardScript.Replace('__LOCK_DIR__', $remoteDeployLock).Replace('__LOCK_TOKEN__', $releaseId).Replace('__NATIVE_INTENT_TOKEN__', $normalizedDeploymentIntentToken).Replace('__REMOTE_DIR__', $RemoteDir).Replace('__RELEASE_DIR__', $remoteReleaseDir).Replace('__PREVIOUS_DIR__', $remotePreviousDir).Replace('__FAILED_DIR__', $remoteFailedDir).Replace('__DEPLOY_COMMIT__', $deployCommit).Replace('__DEPLOY_USER__', $User)
Write-Host "Arming durable remote deployment recovery guard..."
Invoke-RemoteBashScriptTty $armReleaseGuardScript
$remoteGuardArmed = $true

$rollbackReleaseScript = @'
set -euo pipefail
lock_dir='__LOCK_DIR__'
lock_token='__LOCK_TOKEN__'
if ! sudo test -e "$lock_dir" && ! sudo test -L "$lock_dir"; then
  echo "durable deployment guard already completed recovery"
  exit 0
fi
if [ "$lock_dir" != "/opt/.mooncen-deploy.lock" ] ||
   [[ ! "$lock_token" =~ ^[0-9a-f]{32}$ ]]; then
  echo "automatic durable release rollback is unavailable or unsafe" >&2
  exit 65
fi
if ! sudo test -f "$lock_dir/guard.sh" || sudo test -L "$lock_dir/guard.sh"; then
  # The watcher atomically renames the root-only lock when it finishes.  A
  # disappearance between the first existence check and this guarded-file
  # check means recovery already completed, not that the guard was unsafe.
  if ! sudo test -e "$lock_dir" && ! sudo test -L "$lock_dir"; then
    echo "durable deployment guard already completed recovery"
    exit 0
  fi
  echo "automatic durable release rollback is unavailable or unsafe" >&2
  exit 65
fi
sudo "$lock_dir/guard.sh" recover "$lock_dir" "$lock_token"
history_journal="/opt/.mooncen-release-history/${lock_token}/journal.env"
terminal_phase=""
if sudo test -f "$history_journal" && ! sudo test -L "$history_journal"; then
  terminal_phase="$(sudo awk -F= '$1=="PHASE" {count++; value=$2} END {if(count!=1) exit 1; print value}' "$history_journal" || true)"
fi
if [ "$terminal_phase" = committed ]; then
  echo "Durable remote guard completed the in-progress deployment commit."
else
  echo "Durable remote guard restored the previous MoonCen release."
fi
'@
$rollbackReleaseScript = $rollbackReleaseScript.Replace('__LOCK_DIR__', $remoteDeployLock).Replace('__LOCK_TOKEN__', $releaseId)

$prebuildKakaoMapsJavascriptKeyB64 = ConvertTo-Base64Utf8 $KakaoMapsJavascriptKey
$prebuildGoogleOAuthClientIdB64 = ConvertTo-Base64Utf8 $GoogleOAuthClientId
$prebuildNaverOAuthClientIdB64 = ConvertTo-Base64Utf8 $NaverOAuthClientId
$prebuildDomainB64 = ConvertTo-Base64Utf8 $Domain
$prebuildInput = [string]::Join("`n", @(
    $prebuildKakaoMapsJavascriptKeyB64,
    $prebuildGoogleOAuthClientIdB64,
    $prebuildNaverOAuthClientIdB64,
    $prebuildDomainB64
)) + "`n"
$prebuildCommand = "cd '$remoteReleaseDir' && chmod +x deploy/ubuntu/mooncen_prebuild_release.sh && export MOONCEN_DEPLOY_HEARTBEAT='$remoteGuardHeartbeat' MOONCEN_PREBUILD_CONFIG_STDIN=1 && ./deploy/ubuntu/mooncen_prebuild_release.sh '$remoteReleaseDir' '$deployCommit'"
Write-Host "Prebuilding candidate dependencies and frontend before release activation..."
Invoke-RemoteWithInput $prebuildCommand $prebuildInput

$activateReleaseScript = @'
set -euo pipefail
remote_dir='__REMOTE_DIR__'
release_dir='__RELEASE_DIR__'
previous_dir='__PREVIOUS_DIR__'
lock_dir='__LOCK_DIR__'
lock_token='__LOCK_TOKEN__'
guard="$lock_dir/guard.sh"
crawler_runtime_enabled='__ENABLE_CRAWLER__'

is_crawler_runtime_unit() {
  case "$1" in
    mooncen-crawler*.service|mooncen-crawler*.timer|\
    mooncen-staging-apply*.service|mooncen-staging-apply*.timer|\
    mooncen-crawler*.service.d|mooncen-crawler*.timer.d|\
    mooncen-staging-apply*.service.d|mooncen-staging-apply*.timer.d|\
    mooncen-branch-coordinates.service|mooncen-branch-coordinates.service.d)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

is_external_control_plane_unit() {
  case "$1" in
    mooncen-an2p-deploy-sshd.service|mooncen-an2p-deploy-sshd.service.d)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

is_container_runtime_unit() {
  case "$1" in
    mooncen-container-stack.service|mooncen-container-stack.service.d|\
    mooncen-container-release-guard@*.service|\
    mooncen-container-release-guard@*.service.d)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

if [ "$remote_dir" != "/opt/mooncen" ] ||
   [[ ! "$release_dir" =~ ^/opt/\.mooncen-release-[0-9a-f]{32}$ ]] ||
   [[ ! "$previous_dir" =~ ^/opt/\.mooncen-previous-[0-9a-f]{32}$ ]] ||
   [ "$lock_dir" != "/opt/.mooncen-deploy.lock" ] ||
   [[ ! "$lock_token" =~ ^[0-9a-f]{32}$ ]] ||
   ! sudo test -f "$guard" || sudo test -L "$guard" ||
   [ -L "$release_dir" ] || [ ! -d "$release_dir" ] || [ -e "$previous_dir" ]; then
  echo "unsafe MoonCen release activation paths" >&2
  exit 64
fi
if [ -e "$remote_dir" ] && { [ ! -d "$remote_dir" ] || [ -L "$remote_dir" ]; }; then
  echo "existing MoonCen application path is not a regular directory" >&2
  exit 65
fi

previously_active_units=()
rollback_activation() {
  local status=$?
  trap - EXIT HUP INT TERM
  if [ "$status" -ne 0 ]; then
    echo "release activation failed; handing recovery to the durable remote guard" >&2
    sudo "$guard" recover "$lock_dir" "$lock_token" ||
      echo "durable release recovery remains pending in $lock_dir" >&2
  fi
  exit "$status"
}
trap rollback_activation EXIT
trap 'exit 130' HUP INT TERM

mapfile -t managed_units < <(
  find /etc/systemd/system -maxdepth 1 -type f \
    \( -name 'mooncen-*.service' -o -name 'mooncen-*.timer' \) -printf '%f\n' |
    awk '$1 !~ /^mooncen-deploy-guard@/ && $1 != "mooncen-node-metrics.service" && $1 != "mooncen-node-metrics.timer" {print $1}' |
    LC_ALL=C sort -u
)
application_units=()
for unit_name in "${managed_units[@]}"; do
  # The pinned an2p SSH endpoint is deployment transport owned by the
  # external control plane. Stopping it here strands this deployment.
  if is_external_control_plane_unit "$unit_name"; then
    continue
  fi
  if is_container_runtime_unit "$unit_name"; then
    continue
  fi
  application_units+=("$unit_name")
done
managed_units=("${application_units[@]}")
if [ "$crawler_runtime_enabled" != "1" ]; then
  non_crawler_units=()
  for unit_name in "${managed_units[@]}"; do
    if ! is_crawler_runtime_unit "$unit_name"; then
      non_crawler_units+=("$unit_name")
    fi
  done
  managed_units=("${non_crawler_units[@]}")
fi
if [ "${#managed_units[@]}" -gt 0 ]; then
  for unit_name in "${managed_units[@]}"; do
    if [ "$unit_name" != "mooncen-ops-console.service" ] && systemctl is-active --quiet "$unit_name"; then
      previously_active_units+=("$unit_name")
    fi
  done
fi
for unit_name in "${previously_active_units[@]}"; do
  printf '%s\n' "$unit_name"
done | sudo tee "$lock_dir/active-units" >/dev/null
sudo chown root:root "$lock_dir/active-units"
sudo chmod 0600 "$lock_dir/active-units"
sudo "$guard" set-phase "$lock_dir" "$lock_token" activating

if [ "${#managed_units[@]}" -gt 0 ]; then
  sudo systemctl stop "${managed_units[@]}"
  for unit_name in "${managed_units[@]}"; do
    if systemctl is-active --quiet "$unit_name"; then
      echo "refusing release switch while unit remains active: $unit_name" >&2
      exit 70
    fi
    main_pid="$(systemctl show "$unit_name" -p MainPID --value)"
    if [ "${main_pid:-0}" != "0" ]; then
      echo "refusing release switch while unit still has MainPID=$main_pid: $unit_name" >&2
      exit 70
    fi
  done
fi

# Deployment owns MoonCen drop-ins. Removing them before setup prevents a
# deleted or renamed override from surviving a clean release; setup and the
# standby staging installer recreate the reviewed role-specific overrides.
for dropin in /etc/systemd/system/mooncen-*.service.d /etc/systemd/system/mooncen-*.timer.d; do
  [ -e "$dropin" ] || continue
  if is_external_control_plane_unit "$(basename "$dropin")"; then
    continue
  fi
  if is_container_runtime_unit "$(basename "$dropin")"; then
    continue
  fi
  if [ "$crawler_runtime_enabled" != "1" ] && \
     is_crawler_runtime_unit "$(basename "$dropin")"; then
    continue
  fi
  case "$dropin" in
    /etc/systemd/system/mooncen-node-metrics.service.d|/etc/systemd/system/mooncen-node-metrics.timer.d)
      continue
      ;;
    /etc/systemd/system/mooncen-*.service.d|/etc/systemd/system/mooncen-*.timer.d)
      sudo rm -rf -- "$dropin"
      ;;
    *)
      echo "refusing unsafe systemd drop-in cleanup path: $dropin" >&2
      exit 65
      ;;
  esac
done
sudo sync -f -- /etc/systemd/system

if [ -d "$remote_dir" ]; then
  sudo mv -- "$remote_dir" "$previous_dir"
  sudo sync -f -- /opt
fi
sudo mv -- "$release_dir" "$remote_dir"
sudo sync -f -- /opt

if [ -d "$previous_dir" ]; then
  for state_name in logs failover; do
    old_state="$previous_dir/$state_name"
    new_state="$remote_dir/$state_name"
    if [ -d "$old_state" ] && [ ! -L "$old_state" ]; then
      sudo rm -rf -- "$new_state"
      sudo mv -- "$old_state" "$new_state"
      sudo sync -f -- /opt
    fi
  done
fi
sudo "$guard" set-phase "$lock_dir" "$lock_token" activated
trap - EXIT HUP INT TERM
'@
$activateReleaseScript = $activateReleaseScript.Replace('__REMOTE_DIR__', $RemoteDir).Replace('__RELEASE_DIR__', $remoteReleaseDir).Replace('__PREVIOUS_DIR__', $remotePreviousDir).Replace('__LOCK_DIR__', $remoteDeployLock).Replace('__LOCK_TOKEN__', $releaseId).Replace('__ENABLE_CRAWLER__', ([int][bool]$EnableCrawler).ToString())
Invoke-RemoteBashScriptTty $activateReleaseScript
$releaseSwapped = $true

Write-Host "Running project setup..."
$dbPasswordB64 = ConvertTo-Base64Utf8 $DbPassword
$dbApiPasswordB64 = ConvertTo-Base64Utf8 $DbApiPassword
$dbCrawlerPasswordB64 = ConvertTo-Base64Utf8 $DbCrawlerPassword
$dbDeploymentWorkerUserB64 = ConvertTo-Base64Utf8 $DbDeploymentWorkerUser
$dbDeploymentWorkerPasswordB64 = ConvertTo-Base64Utf8 $DbDeploymentWorkerPassword
$dbAiPasswordB64 = ConvertTo-Base64Utf8 $DbAiPassword
$dbApplierPasswordB64 = ConvertTo-Base64Utf8 $DbApplierPassword
$dbBackupPasswordB64 = ConvertTo-Base64Utf8 $DbBackupPassword
$dbCheckPasswordB64 = ConvertTo-Base64Utf8 $DbCheckPassword
$authSecretB64 = ConvertTo-Base64Utf8 $AuthSecret
$opsLoginIdB64 = ConvertTo-Base64Utf8 $OpsLoginId
$opsPasswordHashB64 = ConvertTo-Base64Utf8 $OpsPasswordHash
$dbSslRootCertB64 = ConvertTo-Base64Utf8 $DbSslRootCert
$backupAgeRecipientB64 = ConvertTo-Base64Utf8 $BackupAgeRecipient
$backupPortB64 = ConvertTo-Base64Utf8 $BackupPort
$domainB64 = ConvertTo-Base64Utf8 $Domain
$kakaoMapsJavascriptKeyB64 = ConvertTo-Base64Utf8 $KakaoMapsJavascriptKey
$kakaoMapsRestApiKeyB64 = ConvertTo-Base64Utf8 $KakaoMapsRestApiKey
$googleOAuthClientIdB64 = ConvertTo-Base64Utf8 $GoogleOAuthClientId
$googleOAuthClientSecretB64 = ConvertTo-Base64Utf8 $GoogleOAuthClientSecret
$naverOAuthClientIdB64 = ConvertTo-Base64Utf8 $NaverOAuthClientId
$naverOAuthClientSecretB64 = ConvertTo-Base64Utf8 $NaverOAuthClientSecret
$ollamaHostB64 = ConvertTo-Base64Utf8 $OllamaHost
$ollamaHostsB64 = ConvertTo-Base64Utf8 $OllamaHosts
$ollamaModelB64 = ConvertTo-Base64Utf8 $OllamaModel
$botTokenB64 = ConvertTo-Base64Utf8 $BotToken
$botChatIdB64 = ConvertTo-Base64Utf8 $BotChatId
$adminEmailsB64 = ConvertTo-Base64Utf8 $AdminEmails
$adminProviderIdsB64 = ConvertTo-Base64Utf8 $AdminProviderIds
$bugReportToB64 = ConvertTo-Base64Utf8 $BugReportTo
$bugReportFromB64 = ConvertTo-Base64Utf8 $BugReportFrom
$smtpHostB64 = ConvertTo-Base64Utf8 $SmtpHost
$smtpPortB64 = ConvertTo-Base64Utf8 $SmtpPort
$smtpUsernameB64 = ConvertTo-Base64Utf8 $SmtpUsername
$smtpPasswordB64 = ConvertTo-Base64Utf8 $SmtpPassword
$smtpSecurityB64 = ConvertTo-Base64Utf8 $SmtpSecurity
$opsCloudflareAnalyticsZoneIdB64 = ConvertTo-Base64Utf8 $OpsCloudflareAnalyticsZoneId
$opsCloudflareAnalyticsTokenB64 = ConvertTo-Base64Utf8 $OpsCloudflareAnalyticsToken
$serverMonitorTokenB64 = ConvertTo-Base64Utf8 $ServerMonitorToken
$remoteSetupScript = @"
#!/usr/bin/env bash
set -euo pipefail
umask 077
guard_heartbeat='$remoteGuardHeartbeat'
if [ ! -f "`$guard_heartbeat" ] || [ -L "`$guard_heartbeat" ]; then
  echo "durable deployment guard heartbeat is missing or unsafe" >&2
  exit 70
fi
heartbeat_parent=`$$
(
  while kill -0 "`$heartbeat_parent" >/dev/null 2>&1; do
    touch "`$guard_heartbeat"
    sleep 15
  done
) &
heartbeat_pid=`$!
cleanup_heartbeat() {
  kill "`$heartbeat_pid" >/dev/null 2>&1 || true
  wait "`$heartbeat_pid" >/dev/null 2>&1 || true
}
trap cleanup_heartbeat EXIT
trap 'exit 130' HUP INT TERM
cd '$RemoteDir'
chmod +x deploy/ubuntu/setup_project.sh
export DB_PASSWORD="`$(printf '%s' '$dbPasswordB64' | base64 -d)"
export DB_API_PASSWORD="`$(printf '%s' '$dbApiPasswordB64' | base64 -d)"
export DB_CRAWLER_PASSWORD="`$(printf '%s' '$dbCrawlerPasswordB64' | base64 -d)"
export DB_DEPLOYMENT_WORKER_USER="`$(printf '%s' '$dbDeploymentWorkerUserB64' | base64 -d)"
export DB_DEPLOYMENT_WORKER_PASSWORD="`$(printf '%s' '$dbDeploymentWorkerPasswordB64' | base64 -d)"
export DB_AI_PASSWORD="`$(printf '%s' '$dbAiPasswordB64' | base64 -d)"
export DB_APPLIER_PASSWORD="`$(printf '%s' '$dbApplierPasswordB64' | base64 -d)"
export DB_BACKUP_PASSWORD="`$(printf '%s' '$dbBackupPasswordB64' | base64 -d)"
export DB_CHECK_PASSWORD="`$(printf '%s' '$dbCheckPasswordB64' | base64 -d)"
export AUTH_SECRET="`$(printf '%s' '$authSecretB64' | base64 -d)"
export MOONCEN_OPS_LOGIN_ID="`$(printf '%s' '$opsLoginIdB64' | base64 -d)"
export MOONCEN_OPS_PASSWORD_HASH="`$(printf '%s' '$opsPasswordHashB64' | base64 -d)"
export DB_SSLROOTCERT="`$(printf '%s' '$dbSslRootCertB64' | base64 -d)"
export BACKUP_AGE_RECIPIENT="`$(printf '%s' '$backupAgeRecipientB64' | base64 -d)"
export BACKUP_PORT="`$(printf '%s' '$backupPortB64' | base64 -d)"
export DOMAIN="`$(printf '%s' '$domainB64' | base64 -d)"
export KAKAO_MAPS_JAVASCRIPT_KEY="`$(printf '%s' '$kakaoMapsJavascriptKeyB64' | base64 -d)"
export KAKAO_MAPS_REST_API_KEY="`$(printf '%s' '$kakaoMapsRestApiKeyB64' | base64 -d)"
export GOOGLE_OAUTH_CLIENT_ID="`$(printf '%s' '$googleOAuthClientIdB64' | base64 -d)"
export GOOGLE_OAUTH_CLIENT_SECRET="`$(printf '%s' '$googleOAuthClientSecretB64' | base64 -d)"
export NAVER_OAUTH_CLIENT_ID="`$(printf '%s' '$naverOAuthClientIdB64' | base64 -d)"
export NAVER_OAUTH_CLIENT_SECRET="`$(printf '%s' '$naverOAuthClientSecretB64' | base64 -d)"
export OLLAMA_HOST="`$(printf '%s' '$ollamaHostB64' | base64 -d)"
export OLLAMA_HOSTS="`$(printf '%s' '$ollamaHostsB64' | base64 -d)"
export OLLAMA_MODEL="`$(printf '%s' '$ollamaModelB64' | base64 -d)"
export MOONCEN_BOT_TOKEN="`$(printf '%s' '$botTokenB64' | base64 -d)"
export MOONCEN_BOT_CHAT_ID="`$(printf '%s' '$botChatIdB64' | base64 -d)"
export MOONCEN_ADMIN_EMAILS="`$(printf '%s' '$adminEmailsB64' | base64 -d)"
export MOONCEN_ADMIN_PROVIDER_IDS="`$(printf '%s' '$adminProviderIdsB64' | base64 -d)"
export MOONCEN_BUG_REPORT_TO="`$(printf '%s' '$bugReportToB64' | base64 -d)"
export MOONCEN_BUG_REPORT_FROM="`$(printf '%s' '$bugReportFromB64' | base64 -d)"
export MOONCEN_SMTP_HOST="`$(printf '%s' '$smtpHostB64' | base64 -d)"
export MOONCEN_SMTP_PORT="`$(printf '%s' '$smtpPortB64' | base64 -d)"
export MOONCEN_SMTP_USERNAME="`$(printf '%s' '$smtpUsernameB64' | base64 -d)"
export MOONCEN_SMTP_PASSWORD="`$(printf '%s' '$smtpPasswordB64' | base64 -d)"
export MOONCEN_SMTP_SECURITY="`$(printf '%s' '$smtpSecurityB64' | base64 -d)"
export OPS_CLOUDFLARE_ANALYTICS_ZONE_ID="`$(printf '%s' '$opsCloudflareAnalyticsZoneIdB64' | base64 -d)"
export OPS_CLOUDFLARE_ANALYTICS_TOKEN="`$(printf '%s' '$opsCloudflareAnalyticsTokenB64' | base64 -d)"
export MOONCEN_SERVER_MONITOR_TOKEN="`$(printf '%s' '$serverMonitorTokenB64' | base64 -d)"
export DEPLOY_COMMIT='$deployCommit'
export DEPLOY_ARCHIVE_SHA256='$archiveSha256'
export NODE_ROLE='$NodeRole'
export ENABLE_CRAWLER_STAGING='$([int][bool]$EnableCrawler)'
export SKIP_DB_SETUP='$([int][bool]$Standby)'
export PREBUILT_RELEASE=1
./deploy/ubuntu/setup_project.sh 2>&1 | tee /tmp/mooncen_setup.log
status=`${PIPESTATUS[0]}
cleanup_heartbeat
trap - EXIT HUP INT TERM
if [ "`$status" -ne 0 ]; then
  echo "setup_project.sh failed. Last log lines:"
  tail -n 80 /tmp/mooncen_setup.log
  exit "`$status"
fi
"@
$normalizedRemoteSetupScript = $remoteSetupScript.Replace("`r`n", "`n").Replace("`r", "`n")
Write-PrivateLocalTextFile $remoteSetupLocalPath $normalizedRemoteSetupScript
$remoteSetupRemoteDir = "/tmp/mooncen-secure-setup-" + ([guid]::NewGuid().ToString("N"))
$remoteSetupRemotePath = "$remoteSetupRemoteDir/setup.sh"
$remoteSetupPrepared = $false
try {
    # The temporary script contains base64-encoded credentials.  A private
    # directory protects it even if an scp implementation changes file mode.
    Invoke-Remote "install -d -m 700 '$remoteSetupRemoteDir' && install -m 600 /dev/null '$remoteSetupRemotePath'"
    $remoteSetupPrepared = $true
    scp @sshBaseArgs $remoteSetupLocalPath "${remote}:$remoteSetupRemotePath"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to upload remote setup script"
    }
    ssh @sshBaseArgs -tt $remote "chmod 700 '$remoteSetupRemotePath' && '$remoteSetupRemotePath'; status=`$?; rm -f '$remoteSetupRemotePath'; rmdir '$remoteSetupRemoteDir' >/dev/null 2>&1 || true; exit `$status"
    $remoteSetupFailureExitCode = $LASTEXITCODE
    if ($remoteSetupFailureExitCode -ne 0) {
        Write-Host ""
        Write-Host "Remote setup failed. Last setup log lines:"
        ssh @sshBaseArgs $remote "if [ -f /tmp/mooncen_setup.log ]; then tail -n 120 /tmp/mooncen_setup.log; else echo '/tmp/mooncen_setup.log not found'; fi"
        if ($releaseSwapped) {
            Write-Host "Restoring the previous clean code release; services will remain stopped..."
            try {
                Invoke-RemoteBashScriptTty $rollbackReleaseScript -SkipGuardHeartbeat
                $releaseSwapped = $false
                $remoteGuardArmed = $false
                $remoteDeployLockAcquired = $false
            } catch {
                Write-Warning "Automatic code release rollback failed. Previous release remains at $remotePreviousDir."
            }
        }
        if (Get-DeploymentRemoteErrorCode $remoteSetupFailureExitCode) {
            # A nested rollback call resets the shared marker; the original
            # setup failure retains precedence when it is already semantic.
            $script:DeploymentRemoteExitCode = $remoteSetupFailureExitCode
        }
        throw "Remote setup failed. Check /tmp/mooncen_setup.log on $remote"
    }
} finally {
    if ($remoteSetupPrepared) {
        & ssh @sshBaseArgs $remote "rm -f '$remoteSetupRemotePath'; rmdir '$remoteSetupRemoteDir' >/dev/null 2>&1 || true" 2>$null | Out-Null
    }
    Remove-Item -LiteralPath $remoteSetupLocalPath -Force -ErrorAction SilentlyContinue
}

$verifyDeployInfoScript = @'
set -euo pipefail
deploy_info='__REMOTE_DIR__/.deploy-info'
expected_commit='__DEPLOY_COMMIT__'
expected_archive_sha256='__DEPLOY_ARCHIVE_SHA256__'
if [ ! -f "$deploy_info" ] || [ -L "$deploy_info" ]; then
  echo "deployment provenance file is missing or unsafe: $deploy_info" >&2
  exit 66
fi
actual_commit="$(awk -F= '$1=="DEPLOY_COMMIT" {print $2; exit}' "$deploy_info")"
actual_archive_sha256="$(awk -F= '$1=="DEPLOY_ARCHIVE_SHA256" {print $2; exit}' "$deploy_info")"
if [ "$actual_commit" != "$expected_commit" ] || [ "$actual_archive_sha256" != "$expected_archive_sha256" ]; then
  echo "deployment provenance mismatch" >&2
  echo "expected commit=$expected_commit archive_sha256=$expected_archive_sha256" >&2
  echo "actual commit=${actual_commit:-missing} archive_sha256=${actual_archive_sha256:-missing}" >&2
  exit 65
fi
printf 'verified_deploy_commit=%s\n' "$actual_commit"
printf 'verified_archive_sha256=%s\n' "$actual_archive_sha256"
'@
$verifyDeployInfoScript = $verifyDeployInfoScript.Replace('__REMOTE_DIR__', $RemoteDir).Replace('__DEPLOY_COMMIT__', $deployCommit).Replace('__DEPLOY_ARCHIVE_SHA256__', $archiveSha256)
Invoke-RemoteBashScriptTty $verifyDeployInfoScript

Write-Host "Installing systemd services..."
$installUnitsScript = @'
set -euo pipefail
remote_dir='__REMOTE_DIR__'
crawler_runtime_enabled='__ENABLE_CRAWLER__'
unit_source="$remote_dir/deploy/ubuntu/systemd"
manifest="$(mktemp)"
trap 'rm -f -- "$manifest"' EXIT HUP INT TERM

is_crawler_runtime_unit() {
  case "$1" in
    mooncen-crawler*.service|mooncen-crawler*.timer|\
    mooncen-staging-apply*.service|mooncen-staging-apply*.timer|\
    mooncen-branch-coordinates.service)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

is_external_control_plane_unit() {
  case "$1" in
    mooncen-an2p-deploy-sshd.service|mooncen-an2p-deploy-sshd.service.d)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

is_container_runtime_unit() {
  case "$1" in
    mooncen-container-stack.service|mooncen-container-stack.service.d|\
    mooncen-container-release-guard@*.service|\
    mooncen-container-release-guard@*.service.d)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

if [ "$remote_dir" != "/opt/mooncen" ] || [ ! -d "$unit_source" ] || [ -L "$unit_source" ]; then
  echo "unsafe MoonCen systemd unit source" >&2
  exit 64
fi

find "$unit_source" -maxdepth 1 -type f \( -name '*.service' -o -name '*.timer' \) \
  -printf '%f\n' | LC_ALL=C sort -u > "$manifest"
application_manifest="$(mktemp)"
while IFS= read -r unit_name; do
  if ! is_container_runtime_unit "$unit_name"; then
    printf '%s\n' "$unit_name"
  fi
done < "$manifest" > "$application_manifest"
mv -f -- "$application_manifest" "$manifest"
if [ "$crawler_runtime_enabled" != "1" ]; then
  non_crawler_manifest="$(mktemp)"
  while IFS= read -r unit_name; do
    if ! is_crawler_runtime_unit "$unit_name"; then
      printf '%s\n' "$unit_name"
    fi
  done < "$manifest" > "$non_crawler_manifest"
  mv -f -- "$non_crawler_manifest" "$manifest"
fi
if [ ! -s "$manifest" ]; then
  echo "MoonCen systemd unit manifest is empty" >&2
  exit 66
fi
while IFS= read -r unit_name; do
  if [[ ! "$unit_name" =~ ^(cloudflared|mooncen-[A-Za-z0-9_.@-]+)\.(service|timer)$ ]] ||
     [ "$unit_name" = "mooncen-ops-console.service" ] ||
     is_external_control_plane_unit "$unit_name"; then
    echo "unsafe or obsolete systemd unit in release: $unit_name" >&2
    exit 65
  fi
done < "$manifest"

for installed in /etc/systemd/system/mooncen-*.service /etc/systemd/system/mooncen-*.timer; do
  [ -f "$installed" ] || continue
  unit_name="$(basename "$installed")"
  case "$unit_name" in
    mooncen-node-metrics.service|mooncen-node-metrics.timer)
      # These units are owned by deploy/monitoring/install_linux_exporter.sh.
      continue
      ;;
  esac
  if is_external_control_plane_unit "$unit_name"; then
    # Provisioned by deploy/an2p/cloud and required to keep the deployment
    # transport alive. Full-stack releases never prune or replace it.
    continue
  fi
  if is_container_runtime_unit "$unit_name"; then
    # The root-installed controller receipt owns these exact bytes.
    continue
  fi
  if [ "$crawler_runtime_enabled" != "1" ] && is_crawler_runtime_unit "$unit_name"; then
    # Crawler runtime state belongs to gen1crawler. A cloud Web/API/DB deploy
    # neither installs nor removes stale crawler units; report drift separately.
    continue
  fi
  if ! grep -Fxq -- "$unit_name" "$manifest"; then
    sudo systemctl disable --now "$unit_name" >/dev/null 2>&1 || true
    sudo rm -f -- "$installed"
    sudo rm -rf -- "$installed.d"
  fi
done

while IFS= read -r unit_name; do
  sudo install -o root -g root -m 0644 "$unit_source/$unit_name" "/etc/systemd/system/$unit_name"
done < "$manifest"
sudo systemctl disable --now mooncen-ops-console.service >/dev/null 2>&1 || true
sudo rm -f -- /etc/systemd/system/mooncen-ops-console.service
sudo rm -rf -- /etc/systemd/system/mooncen-ops-console.service.d
sudo systemctl daemon-reload
'@
$installUnitsScript = $installUnitsScript.Replace('__REMOTE_DIR__', $RemoteDir).Replace('__ENABLE_CRAWLER__', ([int][bool]$EnableCrawler).ToString())
Invoke-RemoteBashScriptTty $installUnitsScript
if ($EnableCrawler) {
    Write-Host "Checking crawler browser sandbox..."
    Invoke-RemoteTty "sudo systemctl start mooncen-crawler-browser-smoke.service"
}
Invoke-RemoteTty "printf '%s\n' '$NodeRole' | sudo tee /etc/mooncen-node-role >/dev/null && sudo chmod 644 /etc/mooncen-node-role && sudo systemctl enable --now mooncen-cloudflared-role-guard.timer"
if ($CloudflaredToken) {
    Invoke-RemoteWithInput "mooncenctl cloudflared-token" $CloudflaredToken
}

if ($Standby) {
    Invoke-RemoteBashScriptTty @'
role_check_err=/tmp/mooncen_pg_role_check.err
role=$(sudo -n -u postgres /usr/local/libexec/mooncen-postgres-role 2>"$role_check_err" || true)
if [ -z "$role" ]; then
  role=unknown
fi
if [ "$role" != "standby" ]; then
  echo "postgres_role=$role"
  if [ -s "$role_check_err" ]; then
    echo "postgres_role_check_error:"
    cat "$role_check_err"
  fi
  echo "standby deploy blocked: this target must already be a PostgreSQL streaming replica."
  echo "Expected topology: cloud=primary, n100=standby."
  echo "Check from Windows:"
  echo "  powershell -NoProfile -ExecutionPolicy Bypass -File .\\deploy_mooncen.ps1 replica-status -Target n100"
  echo "Replica setup guide:"
  echo "  deploy/ha/CLOUD_PRIMARY_N100_STANDBY.md"
  exit 1
fi
echo "postgres_role=$role"
'@
    Invoke-RemoteTty "for unit in mooncen-api mooncen-frontend mooncen-ai-worker mooncen-crawler mooncen-backup.timer mooncen-backup-restore-test.timer mooncen-functional-test.timer cloudflared mooncen-cloudflare-gate.timer; do sudo systemctl disable --now `"`$unit`" >/dev/null 2>&1 || true; done"
    Invoke-RemoteTty "sudo mkdir -p '$RemoteDir'/failover && sudo rm -f '$RemoteDir'/failover/enable_auto_failover"
    Invoke-RemoteTty "cd '$RemoteDir' && sudo bash deploy/ha/n100_crawler_staging_setup.sh"
    if ($SkipWorkers) {
        Invoke-RemoteTty "sudo systemctl disable --now mooncen-crawler mooncen-staging-apply.timer mooncen-crawler-watchdog.timer"
        Write-Host "Standby workers remain disabled because -SkipWorkers was specified."
    } else {
        Invoke-RemoteTty "sudo systemctl enable mooncen-crawler mooncen-staging-apply.timer mooncen-crawler-watchdog.timer && sudo systemctl restart mooncen-crawler && sudo systemctl start mooncen-staging-apply.timer mooncen-crawler-watchdog.timer"
    }
    if ($BotToken -and $BotChatId) {
        Invoke-RemoteTty "sudo systemctl enable --now mooncen-ops-bot"
    } else {
        Invoke-RemoteTty "sudo systemctl disable --now mooncen-ops-bot >/dev/null 2>&1 || true"
    }
} else {
    Invoke-RemoteBashScriptTty $nativeWebStartScript
    Invoke-RemoteTty "sudo systemctl disable --now mooncen-ops-console >/dev/null 2>&1 || true"
    Invoke-RemoteTty "sudo systemctl disable --now mooncen-ops-bot >/dev/null 2>&1 || true"
    Invoke-RemoteTty "sudo systemctl enable --now mooncen-backup.timer mooncen-backup-restore-test.timer mooncen-functional-test.timer"
}

Invoke-RemoteTty "sudo systemctl disable --now mooncen-ops-console >/dev/null 2>&1 || true; sudo rm -f /etc/systemd/system/mooncen-ops-console.service; sudo systemctl daemon-reload"

if (-not $Standby -and $EnableCrawler -and -not $SkipWorkers) {
    Invoke-RemoteTty "for unit in mooncen-crawler.service mooncen-crawler-watchdog.timer mooncen-crawler-watchdog.service; do sudo systemctl disable --now `"`$unit`" >/dev/null 2>&1 || true; done; sudo systemctl stop mooncen-staging-apply.service >/dev/null 2>&1 || true; sudo systemctl enable --now mooncen-crawler.timer mooncen-staging-apply.timer; sudo systemctl is-enabled --quiet mooncen-crawler.timer mooncen-staging-apply.timer; sudo systemctl is-active --quiet mooncen-crawler.timer mooncen-staging-apply.timer; if systemctl is-active --quiet mooncen-crawler.service; then echo 'long-running crawler service must remain inactive on the crawler owner' >&2; exit 70; fi"
}

if (-not $SkipWorkers -and -not $Standby) {
    Invoke-RemoteBashScriptTty $nativeAiStartScript
}

Write-Host "Installing Nginx config..."
if ($Standby) {
    $nginxSource = "$RemoteDir/deploy/ubuntu/nginx/mooncen_standby.conf"
} else {
    $nginxSource = "$RemoteDir/deploy/ubuntu/nginx/mooncen.conf"
}
$installNginxScript = @'
set -euo pipefail
nginx_source='__NGINX_SOURCE__'
nginx_target=/etc/nginx/sites-available/mooncen.conf
nginx_enabled=/etc/nginx/sites-enabled/mooncen.conf
nginx_default=/etc/nginx/sites-enabled/default
nginx_stage=/etc/nginx/sites-available/.mooncen.conf.$$
nginx_link_stage=/etc/nginx/sites-enabled/.mooncen.conf.$$

[ -f "$nginx_source" ] && [ ! -L "$nginx_source" ] || {
  echo "nginx configuration source must be a regular non-symlink file" >&2
  exit 78
}
for nginx_parent in /etc/nginx/sites-available /etc/nginx/sites-enabled; do
  [ -d "$nginx_parent" ] && [ ! -L "$nginx_parent" ] || {
    echo "nginx configuration parent is unsafe" >&2
    exit 78
  }
  [ "$(stat -c '%u' -- "$nginx_parent")" = 0 ] || {
    echo "nginx configuration parent is not root-owned" >&2
    exit 78
  }
  nginx_parent_mode="$(stat -c '%a' -- "$nginx_parent")"
  (( (8#$nginx_parent_mode & 8#022) == 0 )) || {
    echo "nginx configuration parent mode is unsafe" >&2
    exit 78
  }
done
if [ -e "$nginx_target" ] || [ -L "$nginx_target" ]; then
  [ -f "$nginx_target" ] && [ ! -L "$nginx_target" ] || {
    echo "nginx configuration target must be absent or a regular non-symlink file" >&2
    exit 78
  }
fi
if [ -e "$nginx_enabled" ] || [ -L "$nginx_enabled" ]; then
  [ -L "$nginx_enabled" ] || {
    echo "nginx enabled target must be absent or a symbolic link" >&2
    exit 78
  }
  case "$(readlink -- "$nginx_enabled")" in
    /etc/nginx/sites-available/mooncen.conf|../sites-available/mooncen.conf) ;;
    *) echo "nginx enabled link target is unsafe" >&2; exit 78 ;;
  esac
fi
if [ -e "$nginx_default" ] || [ -L "$nginx_default" ]; then
  [ -f "$nginx_default" ] || [ -L "$nginx_default" ] || {
    echo "nginx default target has an unsupported type" >&2
    exit 78
  }
fi
for nginx_temporary in "$nginx_stage" "$nginx_link_stage"; do
  [ ! -e "$nginx_temporary" ] && [ ! -L "$nginx_temporary" ] || {
    echo "nginx configuration staging path already exists" >&2
    exit 78
  }
done
cleanup_nginx_stages() {
  sudo rm -f -- "$nginx_stage" "$nginx_link_stage"
}
trap cleanup_nginx_stages EXIT
trap 'exit 130' HUP INT TERM
sudo install -o root -g root -m 0644 "$nginx_source" "$nginx_stage"
sudo test -f "$nginx_stage" && ! sudo test -L "$nginx_stage" || {
  echo "nginx configuration staging file is unsafe" >&2
  exit 78
}
sudo mv -fT -- "$nginx_stage" "$nginx_target"
sudo ln -s -- "$nginx_target" "$nginx_link_stage"
sudo test -L "$nginx_link_stage" &&
  [ "$(sudo readlink -- "$nginx_link_stage")" = "$nginx_target" ] || {
    echo "nginx link staging file is unsafe" >&2
    exit 78
  }
sudo mv -fT -- "$nginx_link_stage" "$nginx_enabled"
sudo test -f "$nginx_target" && ! sudo test -L "$nginx_target" &&
  [ "$(sudo stat -c '%U:%G:%a' "$nginx_target")" = "root:root:644" ] || {
    echo "installed nginx configuration metadata is unsafe" >&2
    exit 78
  }
sudo test -L "$nginx_enabled" &&
  [ "$(sudo readlink -- "$nginx_enabled")" = "$nginx_target" ] || {
    echo "installed nginx enabled link is unsafe" >&2
    exit 78
  }
sudo rm -f -- "$nginx_default"
sudo nginx -t
sudo systemctl reload nginx || sudo systemctl restart nginx
trap - EXIT HUP INT TERM
'@
$installNginxScript = $installNginxScript.Replace('__NGINX_SOURCE__', $nginxSource)
Invoke-RemoteBashScriptTty $installNginxScript

if (-not $Standby) {
    Write-Host "Checking health..."
    Invoke-RemoteHealthCheck
    Write-Host "Enabling Cloudflare health gate..."
    Invoke-RemoteTty "sudo systemctl enable --now mooncen-cloudflare-gate.timer"
} else {
    Write-Host "Skipping health check because standby services are intentionally stopped."
}

if (-not $SkipWorkers -and -not $Standby) {
    Write-Host "Checking Ollama model..."
    try {
        Invoke-RemoteTty "sudo -n /usr/local/libexec/mooncen-ops-service ollama-test"
    } catch {
        Write-Warning "Ollama model check failed. The web deployment remains active; inspect the AI endpoint and worker separately."
    }
} else {
    Write-Host "Skipping Ollama model check because the AI worker is not part of this deployment."
}

if ($releaseSwapped -and $remoteGuardArmed) {
    $finalizeReleaseScript = @'
set -euo pipefail
lock_dir='__LOCK_DIR__'
lock_token='__LOCK_TOKEN__'
guard="$lock_dir/guard.sh"
if [ "$lock_dir" != "/opt/.mooncen-deploy.lock" ] ||
   [[ ! "$lock_token" =~ ^[0-9a-f]{32}$ ]] ||
   ! sudo test -f "$guard" || sudo test -L "$guard"; then
  echo "durable deployment guard is unavailable or unsafe during commit" >&2
  exit 65
fi
sudo "$guard" set-phase "$lock_dir" "$lock_token" verified
set +e
sudo "$guard" commit "$lock_dir" "$lock_token"
commit_status=$?
set -e
if [ "$commit_status" -ne 0 ]; then
  if sudo test -e "$lock_dir" || sudo test -L "$lock_dir"; then
    sudo test -f "$guard" && ! sudo test -L "$guard" || exit "$commit_status"
    sudo "$guard" recover "$lock_dir" "$lock_token" || exit "$commit_status"
  fi
  history_journal="/opt/.mooncen-release-history/${lock_token}/journal.env"
  if sudo test -f "$history_journal" && ! sudo test -L "$history_journal" &&
     [ "$(sudo stat -c '%U:%G:%a' "$history_journal")" = "root:root:600" ]; then
    terminal_phase="$(sudo awk -F= '$1=="PHASE" {count++; value=$2} END {if(count!=1) exit 1; print value}' "$history_journal")"
    if [ "$terminal_phase" = committed ]; then
      echo "Durable deployment commit finalization resumed successfully."
      exit 0
    fi
  fi
  exit "$commit_status"
fi
'@
    $finalizeReleaseScript = $finalizeReleaseScript.Replace('__LOCK_DIR__', $remoteDeployLock).Replace('__LOCK_TOKEN__', $releaseId)
    Invoke-RemoteBashScriptTty $finalizeReleaseScript
    $releaseSwapped = $false
    $remoteGuardArmed = $false
    $remoteDeployLockAcquired = $false
    $nativeIntentFenceEstablished = $false
}

Write-Host ""
Write-Host "Deploy completed."
Write-Host "Open: http://$Domain/"
Write-Host "Status: mooncenctl status"
Write-Host "Doctor: mooncenctl doctor"
if ($Standby) {
    Write-Host "Standby mode: services are installed but disabled. Promote DB and enable services only during failover."
} else {
    Write-Host "Cloudflare gate: sudo systemctl status mooncen-cloudflare-gate.timer"
    Write-Host "Frontend logs: sudo journalctl -u mooncen-frontend -f"
    Write-Host "API logs: sudo journalctl -u mooncen-api -f"
}
if ($EnableCrawler -and -not $SkipWorkers -and -not $Standby) {
    Write-Host "Crawler schedule: sudo systemctl status mooncen-crawler.timer"
    Write-Host "Pinned staging promotion: sudo systemctl status mooncen-staging-apply.timer"
    Write-Host "Crawler logs: sudo journalctl -u mooncen-crawler-once.service -f"
}
if (-not $SkipWorkers -and -not $Standby) {
    Write-Host "AI logs: sudo journalctl -u mooncen-ai-worker -f"
}
} catch {
    $deploymentFailure = $_
    $deploymentFailureExitCode = $script:DeploymentRemoteExitCode
    if ($remoteGuardArmed) {
        Write-Warning "Deployment failed while the durable release guard was armed. Requesting immediate remote recovery."
        try {
            Invoke-RemoteBashScriptTty $rollbackReleaseScript -SkipGuardHeartbeat
            $releaseSwapped = $false
            $remoteGuardArmed = $false
            $remoteDeployLockAcquired = $false
            $nativeIntentFenceEstablished = $false
        } catch {
            $recoveryFailureExitCode = $script:DeploymentRemoteExitCode
            if (
                -not (Get-DeploymentRemoteErrorCode $deploymentFailureExitCode) -and
                (Get-DeploymentRemoteErrorCode $recoveryFailureExitCode)
            ) {
                $deploymentFailureExitCode = $recoveryFailureExitCode
            }
            Write-Warning "Immediate recovery request failed. The root-owned remote watchdog remains responsible for rollback; inspect $remoteDeployLock and $remotePreviousDir."
        }
    }
} finally {
    if ($remoteDeployLockAcquired -and -not $remoteGuardArmed) {
$unlockScript = @'
set -euo pipefail
lock_dir=/opt/.mooncen-deploy.lock
expected_token='__LOCK_TOKEN__'
native_intent_token='__NATIVE_INTENT_TOKEN__'
release_dir='__RELEASE_DIR__'
clear_first_intent() {
  local token="$1" root=/var/lib/mooncen-runtime-transition
  local lock="$root/control.lock"
  [[ "$token" =~ ^[0-9a-f]{32}$ ]] || return 65
  if ! sudo test -e "$root" && ! sudo test -L "$root"; then return 0; fi
  sudo test -d "$root" && ! sudo test -L "$root" &&
    [ "$(sudo stat -c '%U:%G:%a' "$root")" = root:root:700 ] &&
    sudo test -f "$lock" && ! sudo test -L "$lock" &&
    [ "$(sudo stat -c '%U:%G:%a' "$lock")" = root:root:600 ] || return 65
  sudo /usr/bin/flock -x "$lock" /bin/bash -s -- "$token" <<'ROOT'
set -euo pipefail
token="$1"; root=/var/lib/mooncen-runtime-transition
intent="$root/native-bootstrap-intent.json"
expected="{\"schema_version\":1,\"token\":\"${token}\"}"
if [ ! -e "$intent" ] && [ ! -L "$intent" ]; then exit 0; fi
[ -f "$intent" ] && [ ! -L "$intent" ] &&
  [ "$(stat -c '%U:%G:%a' "$intent")" = root:root:600 ] &&
  [ "$(cat "$intent")" = "$expected" ] || exit 65
rm -f -- "$intent"; sync -f -- "$root"
ROOT
}
if ! sudo test -d "$lock_dir" || sudo test -L "$lock_dir"; then
  echo "MoonCen deployment lock directory is missing or unsafe" >&2
  exit 65
fi
if sudo test -e "$lock_dir/journal.env" || sudo test -L "$lock_dir/journal.env" ||
   sudo test -e "$lock_dir/bootstrap.env" || sudo test -L "$lock_dir/bootstrap.env" ||
   sudo test -e "$lock_dir/guard.sh" || sudo test -L "$lock_dir/guard.sh"; then
  echo "durable deployment guard owns this lock; refusing local cleanup" >&2
  exit 75
fi
actual_token="$(sudo cat "$lock_dir/token")"
if [ "$actual_token" != "$expected_token" ]; then
  echo "MoonCen deployment lock token mismatch" >&2
  exit 65
fi
preflight="$lock_dir/preflight.env"
if ! sudo test -f "$preflight" || sudo test -L "$preflight" ||
   [ "$(sudo stat -c '%U:%G:%a' "$preflight")" != root:root:600 ] ||
   [ "$(sudo awk -F= '$1=="TOKEN" {count++; value=$2} END {if(count!=1) exit 1; print value}' "$preflight")" != "$expected_token" ]; then
  echo "MoonCen deployment preflight manifest is unsafe" >&2
  exit 65
fi
if [[ ! "$release_dir" =~ ^/opt/\.mooncen-release-[0-9a-f]{32}$ ]]; then
  echo "unsafe release cleanup path" >&2
  exit 65
fi
if [ -e "$release_dir" ] || [ -L "$release_dir" ]; then
  if [ ! -d "$release_dir" ] || [ -L "$release_dir" ]; then
    echo "release cleanup path requires manual review" >&2
    exit 65
  fi
  sudo rm -rf -- "$release_dir"
fi
controller=/usr/local/libexec/mooncen-container-release
if [ ! -e "$controller" ] && [ ! -L "$controller" ]; then
  ! sudo test -e /etc/mooncen/container-runtime-installation.json &&
    ! sudo test -L /etc/mooncen/container-runtime-installation.json &&
    ! sudo test -e /var/lib/mooncen-container-release &&
    ! sudo test -L /var/lib/mooncen-container-release || {
      echo "container runtime state exists without its root controller" >&2
      exit 65
    }
  clear_first_intent "$native_intent_token"
else
  sudo test -f "$controller" && ! sudo test -L "$controller" &&
    [ "$(sudo stat -c '%U:%G:%a' "$controller")" = root:root:755 ] || exit 65
  output="$(sudo -n -- "$controller" native-end "$native_intent_token")"
  expected="{\"ended\":true,\"schema_version\":1,\"token\":\"${native_intent_token}\"}"
  [ "$output" = "$expected" ] && [[ "$output" != *$'\n'* ]] || exit 65
fi
sudo rm -f -- "/opt/.mooncen-deploy-heartbeat-${expected_token}"
sudo rm -f -- "$lock_dir/preflight.env"
sudo rm -f -- "$lock_dir/token"
sudo rmdir -- "$lock_dir"
sudo sync -f -- /opt
'@
        $unlockScript = $unlockScript.Replace('__LOCK_TOKEN__', $releaseId).Replace('__NATIVE_INTENT_TOKEN__', $normalizedDeploymentIntentToken).Replace('__RELEASE_DIR__', $remoteReleaseDir)
        try {
            Invoke-RemoteBashScriptTty $unlockScript
            $remoteDeployLockAcquired = $false
            $nativeIntentFenceEstablished = $false
        } catch {
            $unlockFailure = $_
            $unlockFailureExitCode = $script:DeploymentRemoteExitCode
            if (
                -not (Get-DeploymentRemoteErrorCode $deploymentFailureExitCode) -and
                $unlockFailureExitCode -in @(65, 75)
            ) {
                $deploymentFailureExitCode = $unlockFailureExitCode
            }
            if ($null -eq $deploymentFailure) {
                $deploymentFailure = $unlockFailure
            }
            Write-Warning "Failed to release $remoteDeployLock. Inspect the token before removing it as root."
        }
    }
    Remove-Item -LiteralPath $archivePath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $remoteSetupLocalPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $localDeploymentTemp -Force -Recurse -ErrorAction SilentlyContinue
}

if ($null -ne $deploymentFailure) {
    if (Get-DeploymentRemoteErrorCode $deploymentFailureExitCode) {
        Write-DeploymentFailureMarker $deploymentFailureExitCode
        exit $deploymentFailureExitCode
    }
    throw $deploymentFailure
}

exit 0
