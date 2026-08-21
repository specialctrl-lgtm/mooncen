#!/usr/bin/env bash
set -Eeuo pipefail

NODE_EXPORTER_VERSION="1.11.1"
DEFAULT_PORT="9100"
LISTEN_ADDRESS=""
ALLOW_ANY_LISTEN=0
VALIDATE_ONLY=0

declare -A SHA256_BY_ARCH=(
    [amd64]="9f5ea48e5bc7b656f8a91a32e7d7deb89f70f73dabd0d974418aca15f37d6810"
    [arm64]="ba1886efbd76cb96b0087c695ea8d1b9cb6e8aa946c996d744e9ee16c8e3591a"
    [armv7]="661c662e566d5c68950d63f7dfd1ec46ba28858f91aeba220c32baa1d3ad7b33"
)

usage() {
    cat <<'EOF'
MoonCen Node Exporter installer for Ubuntu

Usage:
  sudo bash install.sh [options]

The installer also installs Tailscale from its official stable APT repository
and enables tailscaled. If the machine is not authenticated yet, run
"sudo tailscale up" and rerun this installer to bind Node Exporter to its
Tailscale IPv4 address.

Options:
  --listen-address IP:PORT  Bind to this IPv4 address and port.
                            Default: Tailscale IPv4:9100 when available,
                            otherwise 127.0.0.1:9100.
  --allow-any-listen        Permit 0.0.0.0 binding. This is not recommended.
  --validate-only           Download and verify the release without installing.
  -h, --help                Show this help.

Examples:
  sudo bash install.sh
  sudo bash install.sh --listen-address 192.168.10.20:9100
EOF
}

while (($#)); do
    case "$1" in
        --listen-address)
            [[ $# -ge 2 ]] || {
                echo "error: --listen-address requires IP:PORT" >&2
                exit 2
            }
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

if [[ "${VALIDATE_ONLY}" -ne 1 && "${EUID}" -ne 0 ]]; then
    echo "error: run this installer as root (sudo)." >&2
    exit 1
fi

if [[ ! -r /etc/os-release ]]; then
    echo "error: /etc/os-release is missing." >&2
    exit 1
fi

# shellcheck disable=SC1091
source /etc/os-release
distribution="${ID:-} ${ID_LIKE:-}"
if [[ "${distribution,,}" != *ubuntu* && "${distribution,,}" != *debian* ]]; then
    echo "error: this installer supports Ubuntu/Debian systems only." >&2
    exit 1
fi

case "$(uname -m)" in
    x86_64|amd64)
        release_arch="amd64"
        ;;
    aarch64|arm64)
        release_arch="arm64"
        ;;
    armv7l|armv7)
        release_arch="armv7"
        ;;
    *)
        echo "error: unsupported architecture: $(uname -m)" >&2
        exit 1
        ;;
esac

missing_packages=()
command -v curl >/dev/null 2>&1 || missing_packages+=(curl)
command -v tar >/dev/null 2>&1 || missing_packages+=(tar)
command -v sha256sum >/dev/null 2>&1 || missing_packages+=(coreutils)
if [[ "${VALIDATE_ONLY}" -eq 1 ]]; then
    if ((${#missing_packages[@]})); then
        echo "error: validation requires: ${missing_packages[*]}" >&2
        exit 1
    fi
else
    dpkg-query -W -f='${Status}' ca-certificates 2>/dev/null \
        | grep -q '^install ok installed$' \
        || missing_packages+=(ca-certificates)
    dpkg-query -W -f='${Status}' lm-sensors 2>/dev/null \
        | grep -q '^install ok installed$' \
        || missing_packages+=(lm-sensors)
fi
if ((${#missing_packages[@]})); then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y "${missing_packages[@]}"
fi

archive_name="node_exporter-${NODE_EXPORTER_VERSION}.linux-${release_arch}.tar.gz"
download_url="https://github.com/prometheus/node_exporter/releases/download/v${NODE_EXPORTER_VERSION}/${archive_name}"
expected_sha256="${SHA256_BY_ARCH[${release_arch}]}"
temporary_directory="$(mktemp -d -t mooncen-node-exporter.XXXXXXXX)"

cleanup() {
    resolved="$(readlink -f -- "${temporary_directory}" 2>/dev/null || true)"
    if [[ "${resolved}" == /tmp/mooncen-node-exporter.* && -d "${resolved}" ]]; then
        rm -rf -- "${resolved}"
    fi
}
trap cleanup EXIT

tailscale_installation="not_checked"
tailscale_connected=0
tailscale_detected=0
if [[ "${VALIDATE_ONLY}" -ne 1 ]]; then
    if command -v tailscale >/dev/null 2>&1; then
        tailscale_installation="already_installed"
    else
        case "${ID,,}" in
            ubuntu)
                tailscale_repository_os="ubuntu"
                tailscale_repository_codename="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
                ;;
            debian)
                tailscale_repository_os="debian"
                tailscale_repository_codename="${VERSION_CODENAME:-}"
                ;;
            *)
                if [[ " ${ID_LIKE,,} " == *" ubuntu "* ]]; then
                    tailscale_repository_os="ubuntu"
                    tailscale_repository_codename="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
                elif [[ " ${ID_LIKE,,} " == *" debian "* ]]; then
                    tailscale_repository_os="debian"
                    tailscale_repository_codename="${VERSION_CODENAME:-}"
                else
                    echo "error: could not select a Tailscale APT repository for ${ID:-unknown}." >&2
                    exit 1
                fi
                ;;
        esac

        if [[ ! "${tailscale_repository_codename}" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
            echo "error: unsupported Tailscale repository codename: ${tailscale_repository_codename:-missing}." >&2
            exit 1
        fi

        tailscale_repository_base="https://pkgs.tailscale.com/stable/${tailscale_repository_os}"
        tailscale_key_path="${temporary_directory}/tailscale-archive-keyring.gpg"
        tailscale_list_path="${temporary_directory}/tailscale.list"
        curl \
            --proto '=https' \
            --tlsv1.2 \
            --fail \
            --silent \
            --show-error \
            --location \
            --retry 3 \
            --output "${tailscale_key_path}" \
            "${tailscale_repository_base}/${tailscale_repository_codename}.noarmor.gpg"
        curl \
            --proto '=https' \
            --tlsv1.2 \
            --fail \
            --silent \
            --show-error \
            --location \
            --retry 3 \
            --output "${tailscale_list_path}" \
            "${tailscale_repository_base}/${tailscale_repository_codename}.tailscale-keyring.list"

        expected_tailscale_repository="deb [signed-by=/usr/share/keyrings/tailscale-archive-keyring.gpg] ${tailscale_repository_base} ${tailscale_repository_codename} main"
        actual_tailscale_repositories="$(
            grep -Ev '^[[:space:]]*(#|$)' "${tailscale_list_path}" \
                | sed 's/[[:space:]]\+$//'
        )"
        if [[ "${actual_tailscale_repositories}" != "${expected_tailscale_repository}" ]]; then
            echo "error: the downloaded Tailscale repository definition was unexpected." >&2
            exit 1
        fi
        if [[ ! -s "${tailscale_key_path}" ]]; then
            echo "error: the downloaded Tailscale repository key is empty." >&2
            exit 1
        fi

        install -d -m 0755 -o root -g root /usr/share/keyrings /etc/apt/sources.list.d
        install \
            -m 0644 \
            -o root \
            -g root \
            "${tailscale_key_path}" \
            /usr/share/keyrings/tailscale-archive-keyring.gpg
        install \
            -m 0644 \
            -o root \
            -g root \
            "${tailscale_list_path}" \
            /etc/apt/sources.list.d/tailscale.list
        apt-get update
        DEBIAN_FRONTEND=noninteractive apt-get install -y tailscale
        tailscale_installation="installed"
    fi

    systemctl enable --now tailscaled.service
    if ! systemctl is-active --quiet tailscaled.service; then
        journalctl -u tailscaled.service -n 50 --no-pager >&2 || true
        echo "error: tailscaled did not become active." >&2
        exit 1
    fi
fi

tailscale_ipv4=""
if command -v tailscale >/dev/null 2>&1; then
    tailscale_ipv4="$(tailscale ip -4 2>/dev/null || true)"
    tailscale_ipv4="${tailscale_ipv4%%$'\n'*}"
    if [[ -n "${tailscale_ipv4}" ]]; then
        tailscale_connected=1
    fi
fi
if [[ -z "${LISTEN_ADDRESS}" && "${tailscale_connected}" -eq 1 ]]; then
    LISTEN_ADDRESS="${tailscale_ipv4}:${DEFAULT_PORT}"
    tailscale_detected=1
fi

if [[ -z "${LISTEN_ADDRESS}" ]]; then
    LISTEN_ADDRESS="127.0.0.1:${DEFAULT_PORT}"
    if [[ "${VALIDATE_ONLY}" -ne 1 ]]; then
        echo "warning: Tailscale is installed but not connected; binding to loopback only." >&2
        echo "warning: run 'sudo tailscale up', then rerun this installer." >&2
        echo "warning: alternatively use --listen-address PRIVATE_IP:9100." >&2
    fi
fi

if [[ ! "${LISTEN_ADDRESS}" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}:[0-9]{1,5}$ ]]; then
    echo "error: --listen-address must be an IPv4 address followed by a port." >&2
    exit 2
fi

listen_ip="${LISTEN_ADDRESS%:*}"
listen_port="${LISTEN_ADDRESS##*:}"
IFS='.' read -r octet1 octet2 octet3 octet4 <<<"${listen_ip}"
for octet in "${octet1}" "${octet2}" "${octet3}" "${octet4}"; do
    if ((10#${octet} > 255)); then
        echo "error: invalid IPv4 address: ${listen_ip}" >&2
        exit 2
    fi
done
if ((10#${listen_port} < 1 || 10#${listen_port} > 65535)); then
    echo "error: invalid TCP port: ${listen_port}" >&2
    exit 2
fi
if [[ "${listen_ip}" == "0.0.0.0" && "${ALLOW_ANY_LISTEN}" -ne 1 ]]; then
    echo "error: refusing 0.0.0.0 binding without --allow-any-listen." >&2
    exit 2
fi

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

actual_sha256="$(sha256sum "${archive_path}" | awk '{print $1}')"
if [[ "${actual_sha256}" != "${expected_sha256}" ]]; then
    echo "error: SHA-256 verification failed for ${archive_name}." >&2
    exit 1
fi

tar -xzf "${archive_path}" -C "${temporary_directory}"
source_binary="${temporary_directory}/node_exporter-${NODE_EXPORTER_VERSION}.linux-${release_arch}/node_exporter"
[[ -x "${source_binary}" ]] || {
    echo "error: the downloaded archive does not contain node_exporter." >&2
    exit 1
}

if [[ "${VALIDATE_ONLY}" -eq 1 ]]; then
    echo "validation=ok"
    echo "asset=${archive_name}"
    echo "sha256=${actual_sha256}"
    exit 0
fi

if ! getent group node_exporter >/dev/null 2>&1; then
    groupadd --system node_exporter
fi
if ! id -u node_exporter >/dev/null 2>&1; then
    useradd \
        --system \
        --gid node_exporter \
        --no-create-home \
        --home-dir /nonexistent \
        --shell /usr/sbin/nologin \
        node_exporter
fi

install -m 0755 -o root -g root "${source_binary}" /usr/local/bin/node_exporter

temperature_module=""
if [[ "${release_arch}" == "amd64" && -r /proc/cpuinfo ]]; then
    cpu_vendor="$(awk -F: '/vendor_id/{gsub(/[[:space:]]/, "", $2); print $2; exit}' /proc/cpuinfo)"
    case "${cpu_vendor}" in
        AuthenticAMD)
            temperature_module="k10temp"
            ;;
        GenuineIntel)
            temperature_module="coretemp"
            ;;
    esac
fi
if [[ -n "${temperature_module}" ]] && modinfo "${temperature_module}" >/dev/null 2>&1; then
    printf '%s\n' "${temperature_module}" >"${temporary_directory}/mooncen-hwmon.conf"
    install \
        -m 0644 \
        -o root \
        -g root \
        "${temporary_directory}/mooncen-hwmon.conf" \
        /etc/modules-load.d/mooncen-hwmon.conf
    if ! modprobe "${temperature_module}"; then
        echo "warning: could not load ${temperature_module}; temperature may be unavailable." >&2
    fi
fi

tailscale_unit_lines=""
if [[ "${tailscale_detected}" -eq 1 ]]; then
    tailscale_unit_lines=$'After=tailscaled.service\nWants=tailscaled.service'
fi

unit_path="/etc/systemd/system/node_exporter.service"
temporary_unit="${temporary_directory}/node_exporter.service"
cat >"${temporary_unit}" <<EOF
[Unit]
Description=Prometheus Node Exporter
Documentation=https://github.com/prometheus/node_exporter
After=network-online.target
Wants=network-online.target
${tailscale_unit_lines}

[Service]
Type=simple
User=node_exporter
Group=node_exporter
ExecStart=/usr/local/bin/node_exporter --web.listen-address=${LISTEN_ADDRESS} --collector.systemd
Restart=on-failure
RestartSec=5s
UMask=0077
NoNewPrivileges=true
PrivateDevices=true
PrivateTmp=true
ProtectClock=true
ProtectControlGroups=true
ProtectHome=true
ProtectHostname=true
ProtectKernelLogs=true
ProtectKernelModules=true
ProtectKernelTunables=true
ProtectSystem=strict
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
RestrictRealtime=true
RestrictSUIDSGID=true
LockPersonality=true
CapabilityBoundingSet=

[Install]
WantedBy=multi-user.target
EOF

install -m 0644 -o root -g root "${temporary_unit}" "${unit_path}"
systemd-analyze verify "${unit_path}"
systemctl daemon-reload
systemctl enable --now node_exporter.service
systemctl restart node_exporter.service

if ! systemctl is-active --quiet node_exporter.service; then
    journalctl -u node_exporter.service -n 50 --no-pager >&2 || true
    echo "error: node_exporter did not become active." >&2
    exit 1
fi

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
        --fail \
        --silent \
        --show-error \
        --max-time 2 \
        --output "${metrics_probe_path}" \
        "${probe_url}" 2>"${metrics_probe_error_path}" \
        && grep -q '^node_exporter_build_info' "${metrics_probe_path}"
}

probe_ok=0
for _ in {1..10}; do
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
    journalctl -u node_exporter.service -n 50 --no-pager >&2 || true
    echo "error: node_exporter is active but the metrics probe failed." >&2
    echo "error: probe URL: ${probe_url}" >&2
    exit 1
fi

installed_version="$(/usr/local/bin/node_exporter --version 2>&1 | head -n 1)"
if grep -q '^node_hwmon_temp_celsius' "${metrics_probe_path}"; then
    temperature_metrics="available"
else
    temperature_metrics="unsupported"
fi
echo "installation=ok"
echo "service=node_exporter"
echo "listen_address=${LISTEN_ADDRESS}"
echo "metrics_url=${probe_url}"
echo "tailscale=${tailscale_installation}"
echo "tailscale_connected=${tailscale_connected}"
echo "lm_sensors=installed"
echo "temperature_metrics=${temperature_metrics}"
echo "${installed_version}"
