param(
    [Parameter(Mandatory = $true)][ValidateSet("wtr-linux", "gen1crawler")][string]$WorkerKey,
    [Parameter(Mandatory = $true)][string]$Server,
    [Parameter(Mandatory = $true)][string]$User,
    [string]$IdentityFile = "",
    [Parameter(Mandatory = $true)][string]$ExpectedCommit,
    [Parameter(Mandatory = $true)][string]$ExpectedArchiveSha256,
    [Parameter(Mandatory = $true)][string]$ExpectedTreeSha256,
    [Parameter(Mandatory = $true)][string]$ReleaseSignaturePath
)

$ErrorActionPreference = "Stop"
$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

function Assert-ExactDigest {
    param([string]$Name, [string]$Value)
    if ($Value -cnotmatch '^[0-9a-f]{64}$') {
        throw "$Name must be an exact lowercase SHA-256 digest"
    }
}

function Assert-LastExitCode {
    param([string]$Message)
    if ($LASTEXITCODE -ne 0) {
        throw $Message
    }
}

if ($Server -notmatch '^[A-Za-z0-9._-]+$' -or $User -notmatch '^[a-z_][a-z0-9_-]{0,31}$') {
    throw "Crawler-worker SSH target is invalid"
}
$ExpectedCommit = $ExpectedCommit.Trim().ToLowerInvariant()
$ExpectedArchiveSha256 = $ExpectedArchiveSha256.Trim().ToLowerInvariant()
$ExpectedTreeSha256 = $ExpectedTreeSha256.Trim().ToLowerInvariant()
if ($ExpectedCommit -cnotmatch '^(?:[0-9a-f]{40}|[0-9a-f]{64})$') {
    throw "ExpectedCommit must be an exact lowercase Git object identifier"
}
Assert-ExactDigest "ExpectedArchiveSha256" $ExpectedArchiveSha256
Assert-ExactDigest "ExpectedTreeSha256" $ExpectedTreeSha256
if (-not (Test-Path -LiteralPath $ReleaseSignaturePath -PathType Leaf)) {
    throw "A detached OpenSSH worker bootstrap signature is required"
}
$signatureItem = Get-Item -LiteralPath $ReleaseSignaturePath -Force
if (($signatureItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $signatureItem.Length -lt 64 -or $signatureItem.Length -gt 16384) {
    throw "Detached signature must be a bounded regular non-reparse file"
}
$python = Get-Command python -ErrorAction SilentlyContinue
$ssh = Get-Command ssh -ErrorAction SilentlyContinue
$scp = Get-Command scp -ErrorAction SilentlyContinue
if (-not $python -or -not $ssh -or -not $scp) {
    throw "Python and OpenSSH ssh/scp are required for worker bootstrap release transport"
}
$sshArguments = @(
    "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
    "-o", "UpdateHostKeys=no", "-o", "PreferredAuthentications=publickey",
    "-o", "PasswordAuthentication=no", "-o", "KbdInteractiveAuthentication=no",
    "-o", "NumberOfPasswordPrompts=0", "-o", "ConnectTimeout=10",
    "-o", "ConnectionAttempts=1"
)
if ($IdentityFile -and $IdentityFile -ne "ssh-agent") {
    if (-not (Test-Path -LiteralPath $IdentityFile -PathType Leaf)) {
        throw "Configured SSH identity is unavailable"
    }
    $identityItem = Get-Item -LiteralPath $IdentityFile -Force
    if (($identityItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Configured SSH identity must not be a reparse point"
    }
    $sshArguments += @("-i", $identityItem.FullName, "-o", "IdentitiesOnly=yes")
}

$releaseId = [guid]::NewGuid().ToString("N")
$localRoot = Join-Path ([IO.Path]::GetTempPath()) "mooncen-worker-bootstrap-$releaseId"
$remoteUpload = ""
try {
    [IO.Directory]::CreateDirectory($localRoot) | Out-Null
    $builder = Join-Path $RepositoryRoot "tools\build_crawler_worker_bootstrap_release.py"
    $buildJson = & $python.Source -I $builder `
        --repository-root $RepositoryRoot `
        --commit $ExpectedCommit `
        --worker-key $WorkerKey `
        --output-directory $localRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Canonical worker bootstrap release build failed"
    }
    try {
        $build = ($buildJson -join "`n") | ConvertFrom-Json
    } catch {
        throw "Worker bootstrap builder returned invalid metadata"
    }
    if (
        [string]$build.commit -cne $ExpectedCommit -or
        [string]$build.node_role -cne "crawler-worker" -or
        [string]$build.worker_key -cne $WorkerKey -or
        [string]$build.target_dns_host -cne $Server -or
        [string]$build.archive_sha256 -cne $ExpectedArchiveSha256 -or
        [string]$build.tree_sha256 -cne $ExpectedTreeSha256
    ) {
        throw "Locally rebuilt worker release differs from the reviewed host/commit/digest proof"
    }
    $actualActivatorSha256 = (Get-FileHash -LiteralPath ([string]$build.activator) -Algorithm SHA256).Hash.ToLowerInvariant()
    if ([string]$build.activator_sha256 -cne $actualActivatorSha256) {
        throw "Commit-materialized worker root helper changed after the canonical build"
    }

    if ([string]$build.crawler_mode -cne "distributed" -or [bool]$build.worker_enabled -ne $true) {
        throw "NOT READY: committed topology keeps crawlerMode legacy or this worker disabled; no SSH or remote mutation occurred"
    }
    $kernelHostname = [string]$build.target_kernel_hostname
    if ($kernelHostname -cnotmatch '^[a-z0-9](?:[a-z0-9-]{0,62})(?:\.[a-z0-9](?:[a-z0-9-]{0,62}))*$') {
        throw "Builder returned an invalid kernel hostname binding"
    }
    $remote = "$User@$Server"
    $helper = "/usr/local/libexec/mooncen-activate-crawler-worker-bootstrap-release"
    $preflightCommand = "set -euo pipefail; PATH=/usr/sbin:/usr/bin:/sbin:/bin; export PATH; unset BASH_ENV CDPATH ENV GLOBIGNORE LD_LIBRARY_PATH LD_PRELOAD PYTHONHOME PYTHONINSPECT PYTHONPATH PYTHONSTARTUP; [ -f '$helper' ] && [ ! -L '$helper' ]; [ `"`$(stat -c '%U:%G:%a:%h' '$helper')`" = root:root:755:1 ]; printf 'helper_sha256=%s\n' `"`$(sha256sum '$helper' | awk '{print `$1}')`"; sudo -n '$helper' --verify-bootstrap '$WorkerKey' '$kernelHostname'"
    $preflightResult = & ssh @sshArguments $remote "/bin/bash -c `"$preflightCommand`""
    Assert-LastExitCode "Worker failed the non-mutating root bootstrap preflight"
    $proof = @($preflightResult | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ })
    if ($proof.Count -ne 2 -or $proof[0] -cne "helper_sha256=$actualActivatorSha256" -or $proof[1] -cne "crawler-worker-root-bootstrap-ok:${WorkerKey}:${kernelHostname}") {
        throw "Worker returned an invalid fixed-helper provenance proof"
    }

    $createUpload = "PATH=/usr/sbin:/usr/bin:/sbin:/bin; export PATH; umask 077; d=`$(/usr/bin/mktemp -d /tmp/mooncen-worker-bootstrap-$WorkerKey-$releaseId.XXXXXXXX); [ -d `"`$d`" ] && [ ! -L `"`$d`" ] && [ `"`$(/usr/bin/stat -c '%U:%a' `"`$d`")`" = '${User}:700' ]; printf '%s' `"`$d`""
    $remoteUpload = ((& ssh @sshArguments $remote $createUpload) -join "").Trim()
    Assert-LastExitCode "Unable to create the bounded worker upload directory"
    if ($remoteUpload -notmatch "^/tmp/mooncen-worker-bootstrap-$WorkerKey-$releaseId\.[A-Za-z0-9]{8}$") {
        throw "Remote worker upload directory is outside the fixed namespace"
    }
    $scpArguments = @($sshArguments | Where-Object { $_ -ne "-T" })
    & scp @scpArguments ([string]$build.archive) "${remote}:$remoteUpload/crawler-worker-bootstrap-release.tar.gz"
    Assert-LastExitCode "Worker bootstrap archive upload failed"
    & scp @scpArguments ([string]$build.tree_manifest) "${remote}:$remoteUpload/crawler-worker-bootstrap-release.tree"
    Assert-LastExitCode "Worker bootstrap manifest upload failed"
    & scp @scpArguments ([string]$build.metadata) "${remote}:$remoteUpload/crawler-worker-bootstrap-release.env"
    Assert-LastExitCode "Worker bootstrap metadata upload failed"
    & scp @scpArguments $signatureItem.FullName "${remote}:$remoteUpload/crawler-worker-bootstrap-release.sig"
    Assert-LastExitCode "Worker bootstrap signature upload failed"
    & ssh @sshArguments $remote "/usr/bin/chmod 0600 '$remoteUpload'/crawler-worker-bootstrap-release.*"
    Assert-LastExitCode "Unable to protect uploaded worker bootstrap artifacts"

    $activation = & ssh @sshArguments $remote "/usr/bin/sudo -n '$helper' '$releaseId' '$User' '$remoteUpload' '$ExpectedCommit' '$ExpectedArchiveSha256' '$ExpectedTreeSha256' crawler-worker '$WorkerKey' '$kernelHostname'"
    Assert-LastExitCode "Root helper rejected or rolled back the worker bootstrap release"
    $activationLines = @($activation | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ })
    $expectedEngine = "MOONCEN_WORKER_BOOTSTRAP_ACTIVATED=${WorkerKey}:${ExpectedCommit}:${ExpectedArchiveSha256}:${ExpectedTreeSha256}"
    $expectedVerifier = "MOONCEN_WORKER_BOOTSTRAP_RELEASE_VERIFIED=${WorkerKey}:${ExpectedCommit}:${ExpectedArchiveSha256}:${ExpectedTreeSha256}"
    if ($activationLines.Count -ne 2 -or $activationLines[0] -cne $expectedEngine -or $activationLines[1] -cne $expectedVerifier) {
        throw "Worker bootstrap activation returned an invalid proof"
    }
    $remoteUpload = ""
    Write-Output ("crawler-worker-bootstrap-release-verified:{0}:{1}:{2}:{3}" -f $WorkerKey, $ExpectedCommit, $ExpectedArchiveSha256, $ExpectedTreeSha256)
} finally {
    if ($remoteUpload -and $remoteUpload -match "^/tmp/mooncen-worker-bootstrap-$WorkerKey-$releaseId\.[A-Za-z0-9]{8}$") {
        & ssh @sshArguments "$User@$Server" "/usr/bin/rm -rf -- '$remoteUpload'" 2>$null
    }
    if (Test-Path -LiteralPath $localRoot -PathType Container) {
        Remove-Item -LiteralPath $localRoot -Recurse -Force
    }
}
