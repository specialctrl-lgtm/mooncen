param(
    [string]$CloudHost = "cloud",
    [string]$CloudUser = "ubuntu",
    [string]$CloudIdentityFile = "",
    [string]$Gen1CrawlerHost = "gen1crawler",
    [string]$Gen1CrawlerUser = "sgm",
    [string]$Gen1CrawlerIdentityFile = "",
    [string]$Gen1DbHost = "gen1db",
    [string]$Gen1DbUser = "sgm",
    [string]$Gen1DbIdentityFile = "",
    [string]$WtrLinuxHost = "wtr-linux",
    [string]$WtrLinuxUser = "sgm",
    [string]$WtrLinuxIdentityFile = "",
    [string]$PrometheusUrl = "http://bot:9090"
)

$ErrorActionPreference = "Stop"
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$linuxInstaller = Join-Path $scriptRoot "install_linux_exporter.sh"
$linuxMetricFiles = @(
    "mooncen_node_metrics.sh",
    "mooncen-node-metrics.service",
    "mooncen-node-metrics.timer"
) | ForEach-Object { Join-Path $scriptRoot $_ }
$results = New-Object System.Collections.Generic.List[object]

function Invoke-RemoteLinuxInstall {
    param(
        [string]$Name,
        [string]$HostName,
        [string]$User,
        [string]$IdentityFile
    )

    if (-not $HostName) {
        $results.Add([pscustomobject]@{name=$Name; status="skipped"; reason="host_not_set"; target=""}) | Out-Null
        return
    }

    $target = "${User}@${HostName}"
    $sshOptions = @("-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=yes", "-o", "UpdateHostKeys=no")
    $scpOptions = @("-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=yes", "-o", "UpdateHostKeys=no")
    if ($IdentityFile) {
        $sshOptions += @("-i", $IdentityFile)
        $scpOptions += @("-i", $IdentityFile)
    }

    Write-Host "[$Name] checking SSH $target"
    & ssh @sshOptions $target "hostname" | Out-Host
    if ($LASTEXITCODE -ne 0) {
        $results.Add([pscustomobject]@{name=$Name; status="blocked"; reason="ssh_failed"; target=$target}) | Out-Null
        return
    }

    Write-Host "[$Name] uploading Linux exporter installer"
    & scp @scpOptions $linuxInstaller "${target}:/tmp/mooncen_install_linux_exporter.sh"
    if ($LASTEXITCODE -ne 0) {
        $results.Add([pscustomobject]@{name=$Name; status="failed"; reason="scp_failed"; target=$target}) | Out-Null
        return
    }
    foreach ($metricFile in $linuxMetricFiles) {
        if (Test-Path $metricFile) {
            & scp @scpOptions $metricFile "${target}:/tmp/$(Split-Path -Leaf $metricFile)"
            if ($LASTEXITCODE -ne 0) {
                $reason = "scp_failed_$([System.IO.Path]::GetFileName($metricFile))"
                $results.Add([pscustomobject]@{name=$Name; status="failed"; reason=$reason; target=$target}) | Out-Null
                return
            }
        }
    }

    Write-Host "[$Name] installing prometheus-node-exporter"
    & ssh @sshOptions $target "bash /tmp/mooncen_install_linux_exporter.sh" | Out-Host
    if ($LASTEXITCODE -ne 0) {
        $results.Add([pscustomobject]@{name=$Name; status="failed"; reason="install_failed"; target=$target}) | Out-Null
        return
    }

    $results.Add([pscustomobject]@{name=$Name; status="installed"; reason=""; target=$target}) | Out-Null
}

Invoke-RemoteLinuxInstall -Name "cloud" -HostName $CloudHost -User $CloudUser -IdentityFile $CloudIdentityFile
Invoke-RemoteLinuxInstall -Name "gen1crawler" -HostName $Gen1CrawlerHost -User $Gen1CrawlerUser -IdentityFile $Gen1CrawlerIdentityFile
Invoke-RemoteLinuxInstall -Name "gen1db" -HostName $Gen1DbHost -User $Gen1DbUser -IdentityFile $Gen1DbIdentityFile
Invoke-RemoteLinuxInstall -Name "wtr-linux" -HostName $WtrLinuxHost -User $WtrLinuxUser -IdentityFile $WtrLinuxIdentityFile

Write-Host ""
Write-Host "Port check"
$ports = @(
    @{Host="bot"; Port=9100},
    @{Host=$CloudHost; Port=9100},
    @{Host=$Gen1CrawlerHost; Port=9100},
    @{Host=$Gen1DbHost; Port=9100},
    @{Host=$WtrLinuxHost; Port=9100},
    @{Host="victus"; Port=9182}
) | Where-Object { $_.Host }
$ports | ForEach-Object {
    $ok = Test-NetConnection -ComputerName $_.Host -Port $_.Port -InformationLevel Quiet -WarningAction SilentlyContinue
    [pscustomobject]@{host=$_.Host; port=$_.Port; reachable=$ok}
} | Format-Table -AutoSize

Write-Host ""
Write-Host "Prometheus up query"
try {
    $data = Invoke-RestMethod "$PrometheusUrl/api/v1/query?query=up"
    $data.data.result | ForEach-Object {
        [pscustomobject]@{job=$_.metric.job; instance=$_.metric.instance; value=$_.value[1]}
    } | Sort-Object job, instance | Format-Table -AutoSize
} catch {
    Write-Warning "Prometheus query failed: $($_.Exception.Message)"
}

Write-Host ""
Write-Host "Install results"
$results | Format-Table -AutoSize

Write-Host ""
Write-Host "Victus must run deploy\monitoring\install_windows_exporter.ps1 locally from elevated PowerShell, or enable SSH/WinRM/admin SMB for remote installation."
