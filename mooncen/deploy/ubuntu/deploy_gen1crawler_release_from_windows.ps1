param(
    [Parameter(Mandatory = $true)][string]$Server,
    [Parameter(Mandatory = $true)][string]$User,
    [string]$IdentityFile = "",
    [Parameter(Mandatory = $true)][string]$ExpectedCommit,
    [Parameter(Mandatory = $true)][string]$ExpectedArchiveSha256,
    [Parameter(Mandatory = $true)][string]$ExpectedTreeSha256,
    [Parameter(Mandatory = $true)][string]$ReleaseSignaturePath
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

function Assert-Exit([string]$message) {
    if ($LASTEXITCODE -ne 0) { throw $message }
}
foreach ($digest in @($ExpectedArchiveSha256, $ExpectedTreeSha256)) {
    if ($digest -cnotmatch '^[0-9a-f]{64}$') { throw "Release digests must be lowercase SHA-256 values" }
}
if ($ExpectedCommit -cnotmatch '^[0-9a-f]{40}$') { throw "ExpectedCommit must be an exact SHA-1 commit" }
if ($Server -notmatch '^[A-Za-z0-9._-]+$' -or $User -notmatch '^[a-z_][a-z0-9_-]{0,31}$') {
    throw "gen1crawler SSH target is invalid"
}
if (-not (Test-Path -LiteralPath $ReleaseSignaturePath -PathType Leaf)) {
    throw "A detached OpenSSH release signature is required"
}
$sshArgs = @("-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ProxyCommand=none", "-o", "PasswordAuthentication=no")
if ($IdentityFile -and $IdentityFile -ne "ssh-agent") {
    $identity = Get-Item -LiteralPath $IdentityFile -Force
    if (($identity.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "SSH identity must not be a reparse point" }
    $sshArgs += @("-i", $identity.FullName, "-o", "IdentitiesOnly=yes")
}
$scpArgs = @($sshArgs | Where-Object { $_ -ne "-T" })
$remote = "$User@$Server"
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { throw "Python is required to rebuild the reviewed release" }
$releaseId = [guid]::NewGuid().ToString("N")
$local = Join-Path ([IO.Path]::GetTempPath()) "mooncen-gen1crawler-$releaseId"
$remoteUpload = ""
try {
    New-Item -ItemType Directory -Path $local | Out-Null
    $json = & $python.Source -I (Join-Path $root "tools\build_gen1crawler_release.py") --repository-root (Split-Path $root) --commit $ExpectedCommit --output-directory $local
    Assert-Exit "Commit-only gen1crawler release build failed"
    $build = ($json -join "`n") | ConvertFrom-Json
    if ($build.commit -cne $ExpectedCommit -or $build.archive_sha256 -cne $ExpectedArchiveSha256 -or $build.tree_sha256 -cne $ExpectedTreeSha256) {
        throw "Rebuilt gen1crawler release differs from the reviewed digests"
    }
    $helper = "/usr/local/libexec/mooncen-activate-gen1crawler-release"
    & ssh @sshArgs $remote "sudo -n '$helper' --verify-bootstrap"
    Assert-Exit "gen1crawler fixed-helper preflight failed"
    $remoteUpload = ((& ssh @sshArgs $remote "umask 077; mktemp -d /tmp/mooncen-gen1crawler-$releaseId.XXXXXXXX") -join "").Trim()
    Assert-Exit "Unable to create gen1crawler upload directory"
    if ($remoteUpload -notmatch "^/tmp/mooncen-gen1crawler-$releaseId\.[A-Za-z0-9]{8}$") { throw "Remote upload path is invalid" }
    & scp @scpArgs $build.archive "${remote}:$remoteUpload/gen1crawler-release.tar.gz"; Assert-Exit "Archive upload failed"
    & scp @scpArgs $build.tree_manifest "${remote}:$remoteUpload/gen1crawler-release.tree"; Assert-Exit "Manifest upload failed"
    & scp @scpArgs $build.metadata "${remote}:$remoteUpload/gen1crawler-release.env"; Assert-Exit "Metadata upload failed"
    & scp @scpArgs $ReleaseSignaturePath "${remote}:$remoteUpload/gen1crawler-release.sig"; Assert-Exit "Signature upload failed"
    & ssh @sshArgs $remote "chmod 0600 '$remoteUpload'/gen1crawler-release.*"
    Assert-Exit "Unable to protect uploaded release"
    $proof = & ssh @sshArgs $remote "sudo -n '$helper' '$releaseId' '$User' '$remoteUpload' '$ExpectedCommit' '$ExpectedArchiveSha256' '$ExpectedTreeSha256'"
    Assert-Exit "gen1crawler release activation failed or rolled back"
    $expected = "MOONCEN_GEN1CRAWLER_RELEASE_ACTIVATED=${ExpectedCommit}:${ExpectedArchiveSha256}:${ExpectedTreeSha256}"
    if ((@($proof | Where-Object { $_ }).Count -ne 1) -or ([string](@($proof | Where-Object { $_ })[0]) -cne $expected)) {
        throw "gen1crawler returned an invalid activation proof"
    }
    $remoteUpload = ""
    $verified = & ssh @sshArgs $remote "sudo -n '$helper' --verify-active '$ExpectedCommit' '$ExpectedArchiveSha256' '$ExpectedTreeSha256'"
    Assert-Exit "Activated gen1crawler release failed independent verification"
    $expectedVerified = "MOONCEN_GEN1CRAWLER_RELEASE_VERIFIED=${ExpectedCommit}:${ExpectedArchiveSha256}:${ExpectedTreeSha256}"
    if ((@($verified | Where-Object { $_ }).Count -ne 1) -or ([string](@($verified | Where-Object { $_ })[0]) -cne $expectedVerified)) {
        throw "gen1crawler returned an invalid active-release proof"
    }
    Write-Output $expected
    Write-Output $expectedVerified
} finally {
    if ($remoteUpload -match '^/tmp/mooncen-gen1crawler-[0-9a-f]{32}\.[A-Za-z0-9]{8}$') {
        & ssh @sshArgs $remote "rm -rf -- '$remoteUpload'" 2>$null
    }
    if (Test-Path $local) { Remove-Item -LiteralPath $local -Recurse -Force }
}
