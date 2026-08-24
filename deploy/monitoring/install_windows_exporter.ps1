param(
    [ValidatePattern("^\d+\.\d+\.\d+$")]
    [string]$Version = "0.31.6",
    [ValidatePattern("^[0-9A-Fa-f]{64}$")]
    [string]$ExpectedSha256 = "767324dc7ea8e6b8b99f610e2fb9f36d029c8f673a94b3d9f5f2c3c579be0b6d",
    [ValidatePattern("^[0-9A-Fa-f]{40}$")]
    [string]$ExpectedSignerThumbprint = "A5A9E97BFAEB629D755EA507FED51073BA605D78",
    [int]$ListenPort = 9182,
    [string]$ListenAddress = "",
    [string]$AllowedRemoteAddress = "100.64.0.0/10",
    [ValidatePattern("^[a-z0-9_,]+$")]
    [string]$Collectors = "cpu,cs,logical_disk,net,os,service,system,memory"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Get-TailscaleIPv4Address {
    $addresses = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object {
            $bytes = ([System.Net.IPAddress]::Parse($_.IPAddress)).GetAddressBytes()
            $_.InterfaceAlias -like "*Tailscale*" -and
                $bytes[0] -eq 100 -and ($bytes[1] -band 0xC0) -eq 64
        } |
        Select-Object -ExpandProperty IPAddress -Unique

    if (@($addresses).Count -ne 1) {
        throw "Expected exactly one Tailscale IPv4 address. Pass -ListenAddress explicitly when using another private interface."
    }
    return [string]$addresses
}

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script from an elevated PowerShell prompt on the Windows target."
}

if ($ListenPort -lt 1 -or $ListenPort -gt 65535) {
    throw "ListenPort must be between 1 and 65535."
}

if (-not $ListenAddress) {
    $ListenAddress = Get-TailscaleIPv4Address
}
$parsedListenAddress = $null
if (-not [System.Net.IPAddress]::TryParse($ListenAddress, [ref]$parsedListenAddress) -or
    $parsedListenAddress.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork -or
    $ListenAddress -eq "0.0.0.0") {
    throw "ListenAddress must be one explicit IPv4 interface address; wildcard listeners are forbidden."
}
if (-not (Get-NetIPAddress -AddressFamily IPv4 -IPAddress $ListenAddress -ErrorAction SilentlyContinue)) {
    throw "ListenAddress is not assigned to a local IPv4 interface: $ListenAddress"
}

if ($AllowedRemoteAddress -notmatch '^[0-9A-Fa-f:.,/]+$') {
    throw "AllowedRemoteAddress must be a comma-separated IP/CIDR allowlist."
}
$allowedAddresses = @($AllowedRemoteAddress.Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ })
if ($allowedAddresses.Count -eq 0 -or
    $allowedAddresses -contains "0.0.0.0/0" -or
    $allowedAddresses -contains "::/0") {
    throw "AllowedRemoteAddress cannot allow the public Internet."
}
foreach ($addressOrCidr in $allowedAddresses) {
    $cidrParts = @($addressOrCidr.Split('/'))
    $parsedRemoteAddress = $null
    if ($cidrParts.Count -gt 2 -or
        -not [System.Net.IPAddress]::TryParse($cidrParts[0], [ref]$parsedRemoteAddress)) {
        throw "Invalid remote IP/CIDR allowlist entry: $addressOrCidr"
    }
    if ($cidrParts.Count -eq 2) {
        $prefixLength = 0
        $maximumPrefix = if ($parsedRemoteAddress.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork) { 32 } else { 128 }
        if (-not [int]::TryParse($cidrParts[1], [ref]$prefixLength) -or
            $prefixLength -lt 1 -or $prefixLength -gt $maximumPrefix) {
            throw "Invalid remote CIDR prefix: $addressOrCidr"
        }
    }
}

$assetName = "windows_exporter-$Version-amd64.msi"
$downloadUrl = "https://github.com/prometheus-community/windows_exporter/releases/download/v$Version/$assetName"
$tempPath = Join-Path $env:TEMP $assetName

try {
    Write-Host "Downloading pinned $assetName..."
    Invoke-WebRequest -UseBasicParsing -Uri $downloadUrl -OutFile $tempPath

    $actualSha256 = (Get-FileHash -Path $tempPath -Algorithm SHA256).Hash
    if ($actualSha256 -ne $ExpectedSha256) {
        throw "windows_exporter MSI SHA256 mismatch. Expected $ExpectedSha256, got $actualSha256."
    }

    $signature = Get-AuthenticodeSignature -FilePath $tempPath
    if ($signature.SignatureType -ne "Authenticode" -or -not $signature.SignerCertificate) {
        throw "windows_exporter MSI does not contain an Authenticode signature."
    }
    if ($signature.SignerCertificate.Thumbprint -ne $ExpectedSignerThumbprint) {
        throw "windows_exporter signer mismatch. Expected $ExpectedSignerThumbprint, got $($signature.SignerCertificate.Thumbprint)."
    }
    # Upstream intentionally uses a self-signed code-signing certificate. A
    # pristine MSI therefore reports UnknownError until an administrator trusts
    # that certificate globally; the pinned SHA256 and signer thumbprint still
    # make HashMismatch, NotSigned, and every other signer fail closed.
    if ([string]$signature.Status -notin @("Valid", "UnknownError")) {
        throw "windows_exporter Authenticode integrity check failed: $($signature.Status) $($signature.StatusMessage)"
    }
    if ($signature.SignerCertificate.Subject -ne "CN=windows_exporter Code Signing" -or
        $signature.SignerCertificate.Issuer -ne $signature.SignerCertificate.Subject) {
        throw "windows_exporter Authenticode certificate identity is invalid."
    }
    if ((Get-Date) -lt $signature.SignerCertificate.NotBefore -or
        (Get-Date) -gt $signature.SignerCertificate.NotAfter) {
        throw "windows_exporter signing certificate is outside its validity period."
    }

    Write-Host "Verified SHA256 and Authenticode signer $ExpectedSignerThumbprint."

    $args = @(
        "/i", "`"$tempPath`"",
        "/qn",
        "/norestart",
        "ADDLOCAL=FirewallException",
        "ENABLED_COLLECTORS=`"$Collectors`"",
        "LISTEN_ADDR=$ListenAddress",
        "LISTEN_PORT=$ListenPort",
        "REMOTE_ADDR=`"$($allowedAddresses -join ',')`""
    )

    Write-Host "Installing windows_exporter on ${ListenAddress}:$ListenPort..."
    $process = Start-Process -FilePath "msiexec.exe" -ArgumentList $args -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "windows_exporter MSI failed with exit code $($process.ExitCode)"
    }

    Set-Service -Name windows_exporter -StartupType Automatic
    Restart-Service -Name windows_exporter

    if (-not (Get-Command New-NetFirewallRule -ErrorAction SilentlyContinue)) {
        throw "Windows Firewall cmdlets are required to enforce the exporter allowlist."
    }
    $ruleName = "MoonCen windows_exporter $ListenPort"
    Get-NetFirewallRule -Direction Inbound -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -like "*windows_exporter*" } |
        Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow -Protocol TCP `
        -LocalAddress $ListenAddress -LocalPort $ListenPort -RemoteAddress $allowedAddresses | Out-Null

    Invoke-WebRequest -UseBasicParsing "http://${ListenAddress}:$ListenPort/metrics" | Out-Null
    Write-Host "windows_exporter_ok http://${ListenAddress}:$ListenPort/metrics remote=$($allowedAddresses -join ',')"
}
finally {
    Remove-Item -LiteralPath $tempPath -Force -ErrorAction SilentlyContinue
}
