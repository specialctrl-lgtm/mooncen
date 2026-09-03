#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
project_root="$(cd -- "${script_dir}/../.." && pwd -P)"
user_home="$(getent passwd "$(id -u)" | cut -d: -f6)"
unit_dir="${user_home}/.config/systemd/user"
runtime_dir="${user_home}/.local/share/mooncen-an2p"
expected_root="${user_home}/src/project/mooncen"
restart_services=false

[[ "$(hostname -s)" == an2p ]] || {
    echo "Refusing to install an2p services on host $(hostname -s)." >&2
    exit 2
}
[[ "${project_root}" == "${expected_root}" ]] || {
    echo "Expected the an2p repository at ${expected_root}; found ${project_root}." >&2
    exit 2
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --restart)
            restart_services=true
            shift
            ;;
        --development-runtime)
            [[ $# -ge 2 ]] || {
                echo "--development-runtime requires native." >&2
                exit 2
            }
            [[ "$2" == native ]] || {
                echo "Docker development runtime has been retired; use native." >&2
                exit 2
            }
            shift 2
            ;;
        *)
            echo "Usage: $0 [--restart] [--development-runtime native]" >&2
            exit 2
            ;;
    esac
done

if systemctl is-active --quiet mooncen-docker-dev.service ||
   systemctl is-enabled --quiet mooncen-docker-dev.service; then
    echo "Retired Docker development runtime is still selected; run the reviewed root decommission first." >&2
    exit 78
fi
if [[ -e /etc/mooncen-an2p/docker-development-enabled ||
      -L /etc/mooncen-an2p/docker-development-enabled ]]; then
    echo "Retired Docker development marker is still present." >&2
    exit 78
fi

for prerequisite in \
    "${user_home}/.config/mooncen-an2p/api.env" \
    "${user_home}/.config/mooncen-an2p/status-agent.env"; do
    if [[ ! -f "${prerequisite}" || -L "${prerequisite}" ]] ||
       [[ "$(stat -c '%u:%a' "${prerequisite}")" != "$(id -u):600" ]]; then
        echo "an2p prerequisite must be user-owned mode 0600: ${prerequisite}" >&2
        exit 78
    fi
done

native_units=(mooncen-api.service mooncen-frontend.service)
auxiliary_units=(mooncen-status-agent.service mooncen-docs.service)
user_units=(
    "${native_units[@]}"
    mooncen-development-runtime.target
    "${auxiliary_units[@]}"
)

install -d -m 0755 "${unit_dir}"
install -d -m 0700 "${runtime_dir}"
for helper in wait_for_an2p_database.py wait_for_an2p_http.py; do
    source="${project_root}/tools/${helper}"
    [[ -f "${source}" && ! -L "${source}" ]] || {
        echo "Unsafe user runtime helper: ${source}" >&2
        exit 78
    }
    install -m 0700 "${source}" "${runtime_dir}/${helper}"
done

for unit in "${user_units[@]}"; do
    source="${script_dir}/${unit}"
    target="${unit_dir}/${unit}"
    [[ -f "${source}" && ! -L "${source}" ]] || {
        echo "Unsafe user unit source: ${source}" >&2
        exit 78
    }
    if [[ -e "${target}" || -L "${target}" ]]; then
        [[ -f "${target}" && ! -L "${target}" ]] || {
            echo "Unsafe existing user unit: ${target}" >&2
            exit 78
        }
    fi
    install -m 0644 "${source}" "${target}"
done

systemctl --user daemon-reload
systemctl --user enable mooncen-development-runtime.target \
    "${native_units[@]}" "${auxiliary_units[@]}" >/dev/null
if [[ "${restart_services}" == true ]]; then
    systemctl --user restart "${native_units[@]}" "${auxiliary_units[@]}"
else
    systemctl --user start "${native_units[@]}" "${auxiliary_units[@]}"
fi
systemctl --user start mooncen-development-runtime.target
systemctl --user reset-failed

for unit in mooncen-development-runtime.target \
    "${native_units[@]}" "${auxiliary_units[@]}"; do
    systemctl --user is-enabled --quiet "${unit}"
    systemctl --user is-active --quiet "${unit}"
done

curl --noproxy '*' -fsS http://127.0.0.1:8001/health |
    grep -Fxq '{"status":"ready"}'
curl --noproxy '*' -fsS http://127.0.0.1:5174/ >/dev/null

echo "Installed native an2p development services."
