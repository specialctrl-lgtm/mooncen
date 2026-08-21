param(
    [string]$HostName = "bot",
    [string]$User = "ubuntu",
    [string]$RemoteDir = "/opt/mooncen-monitoring",
    [string]$BindAddress = "",
    [string]$IdentityFile = ""
)

$ErrorActionPreference = "Stop"
if ($RemoteDir -notmatch '^/(opt|srv)/[A-Za-z0-9._/-]+$' -or
    $RemoteDir.Contains('..') -or
    $RemoteDir -in @('/opt', '/opt/', '/srv', '/srv/')) {
    throw "RemoteDir must be a dedicated absolute directory below /opt or /srv."
}
$parsedBindAddress = $null
if ($BindAddress) {
    if (-not [System.Net.IPAddress]::TryParse($BindAddress, [ref]$parsedBindAddress) -or
        $parsedBindAddress.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork -or
        $BindAddress -eq "0.0.0.0") {
        throw "BindAddress must be one explicit private IPv4 address, not a wildcard."
    }
    $bytes = $parsedBindAddress.GetAddressBytes()
    $isPrivate = $bytes[0] -eq 10 -or
        ($bytes[0] -eq 172 -and $bytes[1] -ge 16 -and $bytes[1] -le 31) -or
        ($bytes[0] -eq 192 -and $bytes[1] -eq 168) -or
        ($bytes[0] -eq 100 -and ($bytes[1] -band 0xC0) -eq 64)
    if (-not $isPrivate) {
        throw "BindAddress must be an RFC1918 or Tailscale IPv4 address."
    }
}
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$source = Join-Path $root "monitoring"
$target = "${User}@${HostName}"
$sshOptions = @("-o", "BatchMode=yes", "-o", "ConnectTimeout=10")
$scpOptions = @("-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10")
if ($IdentityFile) {
    $sshOptions += @("-i", $IdentityFile)
    $scpOptions += @("-i", $IdentityFile)
}

function Invoke-Ssh {
    param([string]$Command)
    & ssh @sshOptions $target $Command
    if ($LASTEXITCODE -ne 0) {
        throw "SSH command failed on ${target}: $Command"
    }
}

Write-Host "Checking SSH target ${target}..."
Invoke-Ssh "hostname"

Write-Host "Preparing ${RemoteDir}..."
Invoke-Ssh "sudo mkdir -p '$RemoteDir/prometheus' '$RemoteDir/grafana/provisioning/datasources' '$RemoteDir/grafana/provisioning/dashboards/json' '$RemoteDir/grafana/provisioning/alerting' && sudo chown -R `$(id -u):`$(id -g) '$RemoteDir'"

Write-Host "Uploading monitoring stack files..."
& scp @scpOptions (Join-Path $source "docker-compose.yml") "${target}:${RemoteDir}/docker-compose.yml"
if ($LASTEXITCODE -ne 0) { throw "scp docker-compose.yml failed" }
& scp @scpOptions (Join-Path $source "prometheus\prometheus.yml") "${target}:${RemoteDir}/prometheus/prometheus.yml"
if ($LASTEXITCODE -ne 0) { throw "scp prometheus.yml failed" }
& scp @scpOptions (Join-Path $source "grafana\provisioning\datasources\prometheus.yml") "${target}:${RemoteDir}/grafana/provisioning/datasources/prometheus.yml"
if ($LASTEXITCODE -ne 0) { throw "scp grafana datasource failed" }
& scp @scpOptions (Join-Path $source "grafana\provisioning\dashboards\mooncen.yml") "${target}:${RemoteDir}/grafana/provisioning/dashboards/mooncen.yml"
if ($LASTEXITCODE -ne 0) { throw "scp grafana dashboard provider failed" }
& scp @scpOptions (Join-Path $source "grafana\provisioning\dashboards\json\mooncen-node-summary.json") "${target}:${RemoteDir}/grafana/provisioning/dashboards/json/mooncen-node-summary.json"
if ($LASTEXITCODE -ne 0) { throw "scp grafana dashboard json failed" }
Get-ChildItem (Join-Path $source "grafana\provisioning\alerting") -Filter "*.yml" | ForEach-Object {
    & scp @scpOptions $_.FullName "${target}:${RemoteDir}/grafana/provisioning/alerting/$($_.Name)"
    if ($LASTEXITCODE -ne 0) { throw "scp grafana alerting file failed: $($_.Name)" }
}

$remote = @"
set -e
cd '$RemoteDir'
if ! command -v docker >/dev/null 2>&1; then
  echo 'docker_missing'
  exit 20
fi
if ! docker compose version >/dev/null 2>&1; then
  echo 'docker_compose_missing'
  exit 21
fi
if [ -L .env ]; then
  echo 'refusing_symlinked_monitoring_env'
  exit 29
fi
if [ ! -f .env ]; then
  bind_address='$BindAddress'
  if [ -z "`$bind_address" ]; then
    if ! command -v tailscale >/dev/null 2>&1; then
      echo 'tailscale_missing_set_BindAddress'
      exit 22
    fi
    bind_address=`$(tailscale ip -4 2>/dev/null | awk '/^100\./ {print}')
    if [ `$(printf '%s\n' "`$bind_address" | sed '/^`$/d' | wc -l) -ne 1 ]; then
      echo 'expected_one_tailscale_ipv4_set_BindAddress'
      exit 23
    fi
  fi
  case "`$bind_address" in
    10.*|192.168.*|100.6[4-9].*|100.[7-9][0-9].*|100.1[01][0-9].*|100.12[0-7].*|172.1[6-9].*|172.2[0-9].*|172.3[01].*) ;;
    *) echo 'monitor_bind_address_must_be_private'; exit 24 ;;
  esac
  if ! ip -4 -o address show | awk '{print `$4}' | cut -d/ -f1 | grep -Fqx -- "`$bind_address"; then
    echo 'monitor_bind_address_not_local'
    exit 25
  fi
  pass=`$(openssl rand -base64 30 2>/dev/null || python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)
  umask 077
  {
    printf 'MONITOR_BIND_ADDR=%s\n' "`$bind_address"
    printf 'GRAFANA_ADMIN_USER=admin\n'
    printf 'GRAFANA_ADMIN_PASSWORD=%s\n' "`$pass"
    printf 'PROMETHEUS_RETENTION=30d\n'
  } > .env
fi
chmod 0600 .env
configured_bind=`$(sed -n 's/^MONITOR_BIND_ADDR=//p' .env | tail -n1)
if [ -z "`$configured_bind" ] || [ "`$configured_bind" = '0.0.0.0' ]; then
  echo 'existing_env_requires_private_MONITOR_BIND_ADDR'
  exit 26
fi
case "`$configured_bind" in
  10.*|192.168.*|100.6[4-9].*|100.[7-9][0-9].*|100.1[01][0-9].*|100.12[0-7].*|172.1[6-9].*|172.2[0-9].*|172.3[01].*) ;;
  *) echo 'existing_MONITOR_BIND_ADDR_must_be_private'; exit 27 ;;
esac
if ! ip -4 -o address show | awk '{print `$4}' | cut -d/ -f1 | grep -Fqx -- "`$configured_bind"; then
  echo 'existing_MONITOR_BIND_ADDR_not_local'
  exit 28
fi
docker compose pull
docker compose up -d
docker compose ps
"@

Write-Host "Starting monitoring stack on ${target}..."
Invoke-Ssh $remote

Write-Host ""
Write-Host "Monitoring stack started."
Write-Host "Grafana:      http://${HostName}:3000"
Write-Host "Uptime Kuma:  http://${HostName}:3001"
Write-Host "Prometheus:   http://${HostName}:9090"
Write-Host "Grafana password is stored in ${RemoteDir}/.env on ${HostName}."
