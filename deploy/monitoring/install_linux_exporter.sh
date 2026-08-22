#!/usr/bin/env bash
set -euo pipefail

LISTEN_ADDRESS="${LISTEN_ADDRESS:-}"
TEXTFILE_DIR="${TEXTFILE_DIR:-/var/lib/node_exporter/textfile_collector}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v sudo >/dev/null 2>&1; then
  echo "sudo_missing"
  exit 20
fi

if [ -z "$LISTEN_ADDRESS" ]; then
  tailscale_ip=""
  if command -v tailscale >/dev/null 2>&1; then
    tailscale_ip="$(tailscale ip -4 2>/dev/null | head -n 1 || true)"
  fi
  if [ -z "$tailscale_ip" ] && command -v ip >/dev/null 2>&1; then
    tailscale_ip="$(ip -o -4 address show dev tailscale0 2>/dev/null | awk 'NR == 1 {split($4, addr, "/"); print addr[1]}' || true)"
  fi
  if [ -z "$tailscale_ip" ]; then
    echo "tailscale_ipv4_missing" >&2
    echo "Set LISTEN_ADDRESS to one explicit local IPv4 address and port; wildcard listeners are forbidden." >&2
    exit 22
  fi
  LISTEN_ADDRESS="${tailscale_ip}:9100"
fi

if ! [[ "$LISTEN_ADDRESS" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}:[0-9]{1,5}$ ]] || \
  [[ "$LISTEN_ADDRESS" == 0.0.0.0:* ]]; then
  echo "invalid_exporter_listen_address=$LISTEN_ADDRESS" >&2
  echo "Use a Tailscale or other explicit local IPv4 address; wildcard listeners are forbidden." >&2
  exit 23
fi

listen_ip="${LISTEN_ADDRESS%:*}"
listen_port="${LISTEN_ADDRESS##*:}"
IFS='.' read -r octet1 octet2 octet3 octet4 <<<"$listen_ip"
for octet in "$octet1" "$octet2" "$octet3" "$octet4"; do
  if [ $((10#$octet)) -gt 255 ]; then
    echo "invalid_exporter_listen_ip=$listen_ip" >&2
    exit 24
  fi
done
if [ "$listen_port" -lt 1 ] || [ "$listen_port" -gt 65535 ]; then
  echo "invalid_exporter_listen_port=$listen_port" >&2
  exit 25
fi
if command -v ip >/dev/null 2>&1 && \
  ! ip -o -4 address show | awk '{split($4, addr, "/"); print addr[1]}' | grep -Fxq "$listen_ip"; then
  echo "exporter_listen_address_is_not_local=$listen_ip" >&2
  exit 26
fi
if ! [[ "$TEXTFILE_DIR" =~ ^/[A-Za-z0-9._/-]+$ ]]; then
  echo "invalid_textfile_directory=$TEXTFILE_DIR" >&2
  exit 27
fi

if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y prometheus-node-exporter
else
  echo "unsupported_linux_package_manager"
  echo "Install prometheus-node-exporter manually, then expose :9100 to the Tailscale network."
  exit 21
fi

sudo mkdir -p "$TEXTFILE_DIR"
sudo chown root:root "$TEXTFILE_DIR"
sudo chmod 755 "$TEXTFILE_DIR"

exporter_binary="/usr/bin/prometheus-node-exporter"
if [ ! -x "$exporter_binary" ] || \
  ! dpkg-query -S "$exporter_binary" 2>/dev/null | grep -q '^prometheus-node-exporter:'; then
  echo "prometheus_node_exporter_binary_missing" >&2
  exit 28
fi
sudo mkdir -p /etc/systemd/system/prometheus-node-exporter.service.d
printf '%s\n' \
  '[Service]' \
  'ExecStart=' \
  "ExecStart=${exporter_binary} --web.listen-address=${LISTEN_ADDRESS} --collector.textfile.directory=${TEXTFILE_DIR}" \
  | sudo tee /etc/systemd/system/prometheus-node-exporter.service.d/10-mooncen-private-listener.conf >/dev/null

if [ -f "$SCRIPT_DIR/mooncen_node_metrics.sh" ]; then
  sudo cp "$SCRIPT_DIR/mooncen_node_metrics.sh" /usr/local/bin/mooncen_node_metrics.sh
  sudo chmod 755 /usr/local/bin/mooncen_node_metrics.sh
fi

if [ -f "$SCRIPT_DIR/mooncen-node-metrics.service" ] && [ -f "$SCRIPT_DIR/mooncen-node-metrics.timer" ]; then
  sudo cp "$SCRIPT_DIR/mooncen-node-metrics.service" /etc/systemd/system/mooncen-node-metrics.service
  sudo cp "$SCRIPT_DIR/mooncen-node-metrics.timer" /etc/systemd/system/mooncen-node-metrics.timer
  sudo systemctl daemon-reload
  sudo systemctl enable --now mooncen-node-metrics.timer
  sudo systemctl start mooncen-node-metrics.service || true
fi

sudo systemctl daemon-reload
sudo systemctl enable --now prometheus-node-exporter
sudo systemctl restart prometheus-node-exporter

systemctl is-active prometheus-node-exporter
ss -ltnp 2>/dev/null | grep -F ":$listen_port" || true
curl -fsS "http://${listen_ip}:${listen_port}/metrics" >/dev/null
grep -E '^mooncen_' "$TEXTFILE_DIR/mooncen.prom" 2>/dev/null | head || true
echo "node_exporter_ok listen=${LISTEN_ADDRESS}"
