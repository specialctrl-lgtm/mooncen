param(
    [Parameter(Mandatory = $true)][string]$Server,
    [Parameter(Mandatory = $true)][string]$User,
    [string]$IdentityFile = "",
    [Parameter(Mandatory = $true)][string]$ExpectedCommit,
    [Parameter(Mandatory = $true)][string]$ExpectedArchiveSha256,
    [Parameter(Mandatory = $true)][string]$ExpectedTreeSha256,
    [Parameter(Mandatory = $true)][string]$ReleaseSignaturePath,
    [string]$TargetName = "gen1db",
    [string]$RemoteDir = "/opt/mooncen",
    [string]$NodeRole = "crawler-control"
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
    throw "Crawler-control SSH target is invalid"
}
if ($TargetName -ne "gen1db" -or $Server -ne "gen1db" -or $RemoteDir -ne "/opt/mooncen" -or $NodeRole -ne "crawler-control") {
    throw "Crawler-control release transport is pinned to crawler-control target gen1db:/opt/mooncen"
}
$ExpectedCommit = $ExpectedCommit.Trim().ToLowerInvariant()
if ($ExpectedCommit -cnotmatch '^(?:[0-9a-f]{40}|[0-9a-f]{64})$') {
    throw "ExpectedCommit must be an exact lowercase Git object identifier"
}
$ExpectedArchiveSha256 = $ExpectedArchiveSha256.Trim().ToLowerInvariant()
$ExpectedTreeSha256 = $ExpectedTreeSha256.Trim().ToLowerInvariant()
Assert-ExactDigest "ExpectedArchiveSha256" $ExpectedArchiveSha256
Assert-ExactDigest "ExpectedTreeSha256" $ExpectedTreeSha256

if (-not (Test-Path -LiteralPath $ReleaseSignaturePath -PathType Leaf)) {
    throw "A detached OpenSSH release signature is required"
}
$signatureItem = Get-Item -LiteralPath $ReleaseSignaturePath -Force
if (($signatureItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $signatureItem.Length -lt 64 -or $signatureItem.Length -gt 16384) {
    throw "Detached release signature must be a bounded regular non-reparse file"
}

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    throw "Python is required to build the canonical crawler-control release"
}
$ssh = Get-Command ssh -ErrorAction SilentlyContinue
$scp = Get-Command scp -ErrorAction SilentlyContinue
if (-not $ssh -or -not $scp) {
    throw "OpenSSH ssh and scp are required for crawler-control release transport"
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
$remote = "$User@$Server"
$releaseId = [guid]::NewGuid().ToString("N")
$localRoot = Join-Path ([IO.Path]::GetTempPath()) "mooncen-control-release-$releaseId"
$remoteUpload = ""

try {
    [IO.Directory]::CreateDirectory($localRoot) | Out-Null
    $builder = Join-Path $RepositoryRoot "tools\build_crawler_control_release.py"
    $buildJson = & $python.Source -I $builder `
        --repository-root $RepositoryRoot `
        --commit $ExpectedCommit `
        --output-directory $localRoot
    Assert-LastExitCode "Canonical crawler-control release build failed"
    try {
        $build = ($buildJson -join "`n") | ConvertFrom-Json
    } catch {
        throw "Canonical crawler-control release builder returned invalid metadata"
    }
    if (
        [string]$build.commit -cne $ExpectedCommit -or
        [string]$build.node_role -cne "crawler-control" -or
        [string]$build.archive_sha256 -cne $ExpectedArchiveSha256 -or
        [string]$build.tree_sha256 -cne $ExpectedTreeSha256
    ) {
        throw "Locally rebuilt release does not match the reviewed commit/archive/tree proof"
    }
    $actualActivatorSha256 = (Get-FileHash -LiteralPath ([string]$build.activator) -Algorithm SHA256).Hash.ToLowerInvariant()
    if ([string]$build.activator_sha256 -cne $actualActivatorSha256) {
        throw "Commit-materialized root activator changed after the canonical build"
    }
    foreach ($artifact in @($build.archive, $build.tree_manifest, $build.metadata, $build.activator)) {
        $artifactItem = Get-Item -LiteralPath ([string]$artifact) -Force
        if (($artifactItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $artifactItem.Directory.FullName -ne $localRoot) {
            throw "Builder returned an artifact outside its private output directory"
        }
    }

    # All conditions below are read-only remote bootstrap/provenance checks.
    # No upload directory is created until every one passes.
    $remotePreflight = @'
set -euo pipefail
PATH=/usr/sbin:/usr/bin:/sbin:/bin
LC_ALL=C
export PATH LC_ALL
unset BASH_ENV CDPATH ENV GLOBIGNORE LD_LIBRARY_PATH LD_PRELOAD PYTHONHOME PYTHONINSPECT PYTHONPATH PYTHONSTARTUP
[ "$(hostname -s)" = gen1db ]
[ -f /etc/mooncen-node-role ] && [ ! -L /etc/mooncen-node-role ]
[ "$(stat -c '%U:%G:%a' /etc/mooncen-node-role)" = root:root:644 ]
[ "$(cat /etc/mooncen-node-role)" = crawler-control ]
[ -f /etc/mooncen/crawler-control-release-allowed-signers ]
[ ! -L /etc/mooncen/crawler-control-release-allowed-signers ]
[ "$(stat -c '%U:%G:%a' /etc/mooncen/crawler-control-release-allowed-signers)" = root:root:644 ]
getent group mooncen >/dev/null
[ "$(uname -m)" = x86_64 ]
command -v python3.11 >/dev/null
python3.11 -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)'
helper=/usr/local/libexec/mooncen-activate-crawler-control-release
[ -f "$helper" ] && [ ! -L "$helper" ]
[ "$(stat -c '%U:%G:%a' "$helper")" = root:root:755 ]
for unit in mooncen-crawler-control-scheduler.service mooncen-crawler-control-finalizer.service mooncen-crawler-release-publisher.service mooncen-crawler-release-publisher.timer mooncen-crawler-control-metrics.service mooncen-crawler-control-metrics.timer; do
  ! systemctl is-active --quiet "$unit"
  ! systemctl is-enabled --quiet "$unit"
done
printf 'helper_sha256=%s\n' "$(sha256sum "$helper" | awk '{print $1}')"
sudo -n "$helper" --verify-bootstrap
'@
    $encodedPreflight = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($remotePreflight))
    $preflightResult = & ssh @sshArguments $remote "printf '%s' '$encodedPreflight' | /usr/bin/base64 -d | /bin/bash"
    Assert-LastExitCode "gen1db failed the non-mutating crawler-control root bootstrap preflight"
    $preflightLines = @($preflightResult | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ })
    if (
        $preflightLines.Count -ne 2 -or
        $preflightLines[0] -cne "helper_sha256=$actualActivatorSha256" -or
        $preflightLines[1] -cne "crawler-control-root-bootstrap-ok"
    ) {
        throw "gen1db returned an invalid crawler-control bootstrap proof"
    }

    $createUpload = "PATH=/usr/sbin:/usr/bin:/sbin:/bin; export PATH; umask 077; d=`$(/usr/bin/mktemp -d /tmp/mooncen-control-upload-$releaseId.XXXXXXXX); [ -d `"`$d`" ] && [ ! -L `"`$d`" ] && [ `"`$(/usr/bin/stat -c '%U:%a' `"`$d`")`" = '${User}:700' ]; printf '%s' `"`$d`""
    $remoteUpload = ((& ssh @sshArguments $remote $createUpload) -join "").Trim()
    Assert-LastExitCode "Unable to create the bounded unprivileged upload directory"
    if ($remoteUpload -notmatch "^/tmp/mooncen-control-upload-$releaseId\.[A-Za-z0-9]{8}$") {
        throw "Remote upload directory did not match the fixed staging namespace"
    }

    $scpArguments = @($sshArguments | Where-Object { $_ -ne "-T" })
    & scp @scpArguments ([string]$build.archive) "${remote}:$remoteUpload/crawler-control-release.tar.gz"
    Assert-LastExitCode "Crawler-control archive upload failed"
    & scp @scpArguments ([string]$build.tree_manifest) "${remote}:$remoteUpload/crawler-control-release.tree"
    Assert-LastExitCode "Crawler-control tree manifest upload failed"
    & scp @scpArguments ([string]$build.metadata) "${remote}:$remoteUpload/crawler-control-release.env"
    Assert-LastExitCode "Crawler-control signed metadata upload failed"
    & scp @scpArguments $signatureItem.FullName "${remote}:$remoteUpload/crawler-control-release.sig"
    Assert-LastExitCode "Crawler-control detached signature upload failed"
    & ssh @sshArguments $remote "/usr/bin/chmod 0600 '$remoteUpload'/crawler-control-release.*"
    Assert-LastExitCode "Unable to protect uploaded crawler-control artifacts"

    $activationResult = & ssh @sshArguments $remote `
        "/usr/bin/sudo -n /usr/local/libexec/mooncen-activate-crawler-control-release '$releaseId' '$User' '$remoteUpload' '$ExpectedCommit' '$ExpectedArchiveSha256' '$ExpectedTreeSha256' crawler-control"
    Assert-LastExitCode "Root verifier rejected or rolled back the crawler-control release"
    $expectedActivationProof = "MOONCEN_CONTROL_RELEASE_ACTIVATED=${ExpectedCommit}:${ExpectedArchiveSha256}:${ExpectedTreeSha256}"
    $activationLines = @($activationResult | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ })
    if ($activationLines.Count -ne 1 -or $activationLines[0] -cne $expectedActivationProof) {
        throw "Root verifier returned an invalid crawler-control activation proof"
    }
    $remoteUpload = ""

    $proofResult = & ssh @sshArguments $remote `
        "/usr/bin/sudo -n /usr/local/libexec/mooncen-activate-crawler-control-release --verify-active '$ExpectedCommit' '$ExpectedArchiveSha256' '$ExpectedTreeSha256' crawler-control"
    Assert-LastExitCode "Activated crawler-control release failed its independent remote proof"
    $expectedRemoteProof = "MOONCEN_CONTROL_RELEASE_VERIFIED=${ExpectedCommit}:${ExpectedArchiveSha256}:${ExpectedTreeSha256}"
    $proofLines = @($proofResult | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ })
    if ($proofLines.Count -ne 1 -or $proofLines[0] -cne $expectedRemoteProof) {
        throw "Activated crawler-control release returned an invalid remote proof"
    }
    Write-Output ("crawler-control-release-verified:{0}:{1}:{2}" -f $ExpectedCommit, $ExpectedArchiveSha256, $ExpectedTreeSha256)
} finally {
    if ($remoteUpload -and $remoteUpload -match "^/tmp/mooncen-control-upload-$releaseId\.[A-Za-z0-9]{8}$") {
        & ssh @sshArguments $remote "/usr/bin/rm -rf -- '$remoteUpload'" 2>$null
    }
    if (Test-Path -LiteralPath $localRoot -PathType Container) {
        Remove-Item -LiteralPath $localRoot -Recurse -Force
    }
}
