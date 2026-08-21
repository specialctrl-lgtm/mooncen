#!/bin/bash
set -Eeuo pipefail

NODE_EXPORTER_VERSION="1.11.1"
DEFAULT_PORT="9100"
SERVICE_LABEL="kr.binary.mooncen.node_exporter"
PLIST_PATH="/Library/LaunchDaemons/${SERVICE_LABEL}.plist"
INSTALL_PATH="/usr/local/bin/node_exporter"
LISTEN_ADDRESS=""
ALLOW_ANY_LISTEN=0
VALIDATE_ONLY=0

usage() {
    cat <<'EOF'
MoonCen Node Exporter installer for macOS

Usage:
  sudo bash install-macos.sh [options]

The installer detects an existing Tailscale CLI or app. When Tailscale is
connected, Node Exporter binds to its IPv4 address. Otherwise it binds to
127.0.0.1 and can be rerun after Tailscale is connected.

Options:
  --listen-address IP:PORT  Bind to this IPv4 address and port.
                            Default: Tailscale IPv4:9100 when available,
                            otherwise 127.0.0.1:9100.
  --allow-any-listen        Permit 0.0.0.0 binding. This is not recommended.
  --validate-only           Download and verify the release without installing.
  -h, --help                Show this help.

Examples:
  sudo bash install-macos.sh
  sudo bash install-macos.sh --listen-address 192.168.10.40:9100
EOF
}

while (($#)); do
    case "$1" in
        --listen-address)
            if [[ $# -lt 2 ]]; then
                echo "error: --listen-address requires IP:PORT" >&2
                exit 2
            fi
            LISTEN_ADDRESS="$2"
            shift 2
            ;;
        --allow-any-listen)
            ALLOW_ANY_LISTEN=1
            shift
            ;;
        --validate-only)
            VALIDATE_ONLY=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "error: unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "error: this installer supports macOS only." >&2
    exit 1
fi

if [[ "${VALIDATE_ONLY}" -ne 1 && "$(id -u)" -ne 0 ]]; then
    echo "error: run this installer as root (sudo)." >&2
    exit 1
fi

for required_command in curl tar shasum awk sed grep mktemp; do
    if ! command -v "${required_command}" >/dev/null 2>&1; then
        echo "error: required command is missing: ${required_command}" >&2
        exit 1
    fi
done
if [[ "${VALIDATE_ONLY}" -ne 1 ]]; then
    for required_command in install launchctl plutil; do
        if ! command -v "${required_command}" >/dev/null 2>&1; then
            echo "error: required command is missing: ${required_command}" >&2
            exit 1
        fi
    done
fi

case "$(uname -m)" in
    x86_64|amd64)
        release_arch="amd64"
        expected_sha256="782318ceb48cb5501271a666d1b015a9406c02cd45dbc9513deca005b91e03a5"
        ;;
    arm64|aarch64)
        release_arch="arm64"
        expected_sha256="e987428618362c2d2540a68b722bd982ef1486c9961631298f20ea8fd57d3be4"
        ;;
    *)
        echo "error: unsupported architecture: $(uname -m)" >&2
        exit 1
        ;;
esac

temp_root="${TMPDIR:-/tmp}"
temp_root="${temp_root%/}"
temporary_directory="$(mktemp -d "${temp_root}/mooncen-node-exporter.XXXXXXXX")"

cleanup() {
    case "${temporary_directory:-}" in
        "${temp_root}"/mooncen-node-exporter.*)
            if [[ -d "${temporary_directory}" ]]; then
                rm -rf -- "${temporary_directory}"
            fi
            ;;
    esac
}
trap cleanup EXIT

tailscale_cli=""
tailscale_installation="not_found"
tailscale_connected=0
tailscale_ipv4=""

if command -v tailscale >/dev/null 2>&1; then
    tailscale_cli="$(command -v tailscale)"
    tailscale_installation="detected"
elif [[ -x /Applications/Tailscale.app/Contents/MacOS/Tailscale ]]; then
    tailscale_cli="/Applications/Tailscale.app/Contents/MacOS/Tailscale"
    tailscale_installation="detected"
fi

if [[ -n "${tailscale_cli}" ]]; then
    tailscale_ipv4="$(
        TAILSCALE_BE_CLI=1 "${tailscale_cli}" ip -4 2>/dev/null \
            | sed -n '1p' || true
    )"
    if [[ -n "${tailscale_ipv4}" ]]; then
        tailscale_connected=1
    fi
fi

if [[ -z "${LISTEN_ADDRESS}" && "${tailscale_connected}" -eq 1 ]]; then
    LISTEN_ADDRESS="${tailscale_ipv4}:${DEFAULT_PORT}"
fi
if [[ -z "${LISTEN_ADDRESS}" ]]; then
    LISTEN_ADDRESS="127.0.0.1:${DEFAULT_PORT}"
    if [[ "${VALIDATE_ONLY}" -ne 1 ]]; then
        if [[ "${tailscale_installation}" == "detected" ]]; then
            echo "warning: Tailscale is not connected; binding to loopback only." >&2
            echo "warning: connect Tailscale, then rerun this installer." >&2
        else
            echo "warning: Tailscale was not found; binding to loopback only." >&2
            echo "warning: install and connect Tailscale, then rerun this installer." >&2
        fi
        echo "warning: alternatively use --listen-address PRIVATE_IP:9100." >&2
    fi
fi

if [[ ! "${LISTEN_ADDRESS}" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}:[0-9]{1,5}$ ]]; then
    echo "error: --listen-address must be an IPv4 address followed by a port." >&2
    exit 2
fi

listen_ip="${LISTEN_ADDRESS%:*}"
listen_port="${LISTEN_ADDRESS##*:}"
old_ifs="${IFS}"
IFS='.'
set -- ${listen_ip}
IFS="${old_ifs}"
if [[ $# -ne 4 ]]; then
    echo "error: invalid IPv4 address: ${listen_ip}" >&2
    exit 2
fi
for octet in "$@"; do
    if [[ "${octet}" != "0" && "${octet}" == 0* ]]; then
        echo "error: invalid IPv4 address: ${listen_ip}" >&2
        exit 2
    fi
    if ((10#${octet} > 255)); then
        echo "error: invalid IPv4 address: ${listen_ip}" >&2
        exit 2
    fi
done
if [[ "${listen_port}" != "0" && "${listen_port}" == 0* ]]; then
    echo "error: invalid TCP port: ${listen_port}" >&2
    exit 2
fi
if ((10#${listen_port} < 1 || 10#${listen_port} > 65535)); then
    echo "error: invalid TCP port: ${listen_port}" >&2
    exit 2
fi
if [[ "${listen_ip}" == "0.0.0.0" && "${ALLOW_ANY_LISTEN}" -ne 1 ]]; then
    echo "error: refusing 0.0.0.0 binding without --allow-any-listen." >&2
    exit 2
fi

archive_name="node_exporter-${NODE_EXPORTER_VERSION}.darwin-${release_arch}.tar.gz"
download_url="https://github.com/prometheus/node_exporter/releases/download/v${NODE_EXPORTER_VERSION}/${archive_name}"
archive_path="${temporary_directory}/${archive_name}"

curl \
    --proto '=https' \
    --tlsv1.2 \
    --fail \
    --silent \
    --show-error \
    --location \
    --retry 3 \
    --output "${archive_path}" \
    "${download_url}"

actual_sha256="$(shasum -a 256 "${archive_path}" | awk '{print $1}')"
if [[ "${actual_sha256}" != "${expected_sha256}" ]]; then
    echo "error: SHA-256 verification failed for ${archive_name}." >&2
    exit 1
fi

tar -xzf "${archive_path}" -C "${temporary_directory}"
source_binary="${temporary_directory}/node_exporter-${NODE_EXPORTER_VERSION}.darwin-${release_arch}/node_exporter"
if [[ ! -x "${source_binary}" ]]; then
    echo "error: the downloaded archive does not contain node_exporter." >&2
    exit 1
fi

downloaded_version="$("${source_binary}" --version 2>&1 | sed -n '1p')"
if [[ "${downloaded_version}" != *"version ${NODE_EXPORTER_VERSION}"* ]]; then
    echo "error: downloaded node_exporter reported an unexpected version." >&2
    exit 1
fi

if [[ "${VALIDATE_ONLY}" -eq 1 ]]; then
    echo "validation=ok"
    echo "asset=${archive_name}"
    echo "sha256=${actual_sha256}"
    echo "${downloaded_version}"
    exit 0
fi

install -d -m 0755 -o root -g wheel /usr/local/bin
install -m 0755 -o root -g wheel "${source_binary}" "${INSTALL_PATH}"

temporary_plist="${temporary_directory}/${SERVICE_LABEL}.plist"
cat >"${temporary_plist}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${SERVICE_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${INSTALL_PATH}</string>
        <string>--web.listen-address=${LISTEN_ADDRESS}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ProcessType</key>
    <string>Background</string>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>UserName</key>
    <string>nobody</string>
</dict>
</plist>
EOF

plutil -lint "${temporary_plist}"
install -m 0644 -o root -g wheel "${temporary_plist}" "${PLIST_PATH}"

launchctl bootout system "${PLIST_PATH}" >/dev/null 2>&1 || true
launchctl bootstrap system "${PLIST_PATH}"
launchctl enable "system/${SERVICE_LABEL}"
launchctl kickstart -k "system/${SERVICE_LABEL}"

probe_ip="${listen_ip}"
if [[ "${probe_ip}" == "0.0.0.0" ]]; then
    probe_ip="127.0.0.1"
fi
probe_url="http://${probe_ip}:${listen_port}/metrics"
metrics_probe_path="${temporary_directory}/node_exporter-metrics.prom"
metrics_probe_error_path="${temporary_directory}/node_exporter-metrics-error.log"

probe_metrics() {
    rm -f -- "${metrics_probe_path}"
    curl \
        --noproxy '*' \
        --fail \
        --silent \
        --show-error \
        --max-time 2 \
        --output "${metrics_probe_path}" \
        "${probe_url}" 2>"${metrics_probe_error_path}" \
        && grep -q '^node_exporter_build_info' "${metrics_probe_path}"
}

probe_ok=0
for _ in {1..15}; do
    if probe_metrics; then
        probe_ok=1
        break
    fi
    sleep 1
done
if [[ "${probe_ok}" -ne 1 ]]; then
    if [[ -s "${metrics_probe_error_path}" ]]; then
        cat "${metrics_probe_error_path}" >&2
    fi
    launchctl print "system/${SERVICE_LABEL}" >&2 || true
    echo "error: node_exporter is loaded but the metrics probe failed." >&2
    echo "error: probe URL: ${probe_url}" >&2
    exit 1
fi

installed_version="$("${INSTALL_PATH}" --version 2>&1 | sed -n '1p')"
if grep -q '^node_thermal_temperature_celsius' "${metrics_probe_path}"; then
    temperature_metrics="available"
else
    temperature_metrics="unsupported"
fi

echo "installation=ok"
echo "service=${SERVICE_LABEL}"
echo "listen_address=${LISTEN_ADDRESS}"
echo "metrics_url=${probe_url}"
echo "tailscale=${tailscale_installation}"
echo "tailscale_connected=${tailscale_connected}"
echo "temperature_metrics=${temperature_metrics}"
echo "${installed_version}"
