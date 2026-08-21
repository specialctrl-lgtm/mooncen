#!/usr/bin/env bash
set -euo pipefail

REMOTE_DIR="${REMOTE_DIR:-/opt/mooncen-monitoring}"
MONITOR_BIND_ADDR="${MONITOR_BIND_ADDR:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! "$REMOTE_DIR" =~ ^/(opt|srv)/[A-Za-z0-9._/-]+$ ]] || [[ "$REMOTE_DIR" == *".."* ]]; then
  echo "REMOTE_DIR must be a dedicated absolute directory below /opt or /srv." >&2
  exit 64
fi
case "$REMOTE_DIR" in
  /opt|/srv|/opt/|/srv/) echo "REMOTE_DIR cannot be a system directory." >&2; exit 64 ;;
esac

validate_monitor_bind_address() {
  local address="$1"
  if ! [[ "$address" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || [ "$address" = "0.0.0.0" ]; then
    echo "MONITOR_BIND_ADDR must be one explicit private IPv4 address, not a wildcard." >&2
    return 30
  fi
  case "$address" in
    10.*|192.168.*|100.6[4-9].*|100.[7-9][0-9].*|100.1[01][0-9].*|100.12[0-7].*) ;;
    172.1[6-9].*|172.2[0-9].*|172.3[01].*) ;;
    *)
      echo "MONITOR_BIND_ADDR must be an RFC1918 or Tailscale IPv4 address: $address" >&2
      return 31
      ;;
  esac
  if ! ip -4 -o address show | awk '{print $4}' | cut -d/ -f1 | grep -Fqx -- "$address"; then
    echo "MONITOR_BIND_ADDR is not assigned to this host: $address" >&2
    return 32
  fi
}

detect_tailscale_address() {
  if ! command -v tailscale >/dev/null 2>&1; then
    echo "Tailscale is unavailable. Set MONITOR_BIND_ADDR to an explicit local private IPv4 address." >&2
    return 33
  fi
  mapfile -t addresses < <(tailscale ip -4 2>/dev/null | awk '/^100\./ {print}')
  if [ "${#addresses[@]}" -ne 1 ]; then
    echo "Expected exactly one Tailscale IPv4 address; set MONITOR_BIND_ADDR explicitly." >&2
    return 34
  fi
  printf '%s\n' "${addresses[0]}"
}

sudo mkdir -p "$REMOTE_DIR/prometheus" \
  "$REMOTE_DIR/grafana/provisioning/datasources" \
  "$REMOTE_DIR/grafana/provisioning/dashboards/json" \
  "$REMOTE_DIR/grafana/provisioning/alerting"
sudo chown -R "$(id -u):$(id -g)" "$REMOTE_DIR"
cp "$SCRIPT_DIR/docker-compose.yml" "$REMOTE_DIR/docker-compose.yml"
cp "$SCRIPT_DIR/prometheus/prometheus.yml" "$REMOTE_DIR/prometheus/prometheus.yml"
cp "$SCRIPT_DIR/grafana/provisioning/datasources/prometheus.yml" "$REMOTE_DIR/grafana/provisioning/datasources/prometheus.yml"
cp "$SCRIPT_DIR/grafana/provisioning/dashboards/mooncen.yml" "$REMOTE_DIR/grafana/provisioning/dashboards/mooncen.yml"
cp "$SCRIPT_DIR/grafana/provisioning/dashboards/json/mooncen-node-summary.json" "$REMOTE_DIR/grafana/provisioning/dashboards/json/mooncen-node-summary.json"
cp "$SCRIPT_DIR/grafana/provisioning/alerting/"*.yml "$REMOTE_DIR/grafana/provisioning/alerting/"

cd "$REMOTE_DIR"
if ! command -v docker >/dev/null 2>&1; then
  echo "docker_missing"
  exit 20
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "docker_compose_missing"
  exit 21
fi

if [ -L .env ]; then
  echo "Refusing a symlinked monitoring .env file." >&2
  exit 36
fi
if [ -f .env ]; then
  chmod 0600 .env
  configured_bind="$(sed -n 's/^MONITOR_BIND_ADDR=//p' .env | tail -n1)"
  if [ -z "$configured_bind" ]; then
    echo "Existing $REMOTE_DIR/.env has no MONITOR_BIND_ADDR. Add a private local IPv4 address." >&2
    exit 35
  fi
  MONITOR_BIND_ADDR="$configured_bind"
else
  if [ -z "$MONITOR_BIND_ADDR" ]; then
    MONITOR_BIND_ADDR="$(detect_tailscale_address)"
  fi
  validate_monitor_bind_address "$MONITOR_BIND_ADDR"
  pass="$(openssl rand -base64 30 2>/dev/null || python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"
  umask 077
  {
    printf 'MONITOR_BIND_ADDR=%s\n' "$MONITOR_BIND_ADDR"
    printf 'GRAFANA_ADMIN_USER=admin\n'
    printf 'GRAFANA_ADMIN_PASSWORD=%s\n' "$pass"
    printf 'PROMETHEUS_RETENTION=30d\n'
  } > .env
fi
chmod 0600 .env
validate_monitor_bind_address "$MONITOR_BIND_ADDR"

docker compose pull
docker compose up -d
docker compose ps

echo "Grafana:      http://${MONITOR_BIND_ADDR}:3000"
echo "Uptime Kuma:  http://${MONITOR_BIND_ADDR}:3001"
echo "Prometheus:   http://${MONITOR_BIND_ADDR}:9090"
echo "Grafana password is stored in $REMOTE_DIR/.env."
