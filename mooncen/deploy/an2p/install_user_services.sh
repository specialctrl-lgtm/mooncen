#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
project_root="$(cd -- "${script_dir}/../.." && pwd -P)"
user_home="$(getent passwd "$(id -u)" | cut -d: -f6)"
unit_dir="${user_home}/.config/systemd/user"
runtime_dir="${user_home}/.local/share/mooncen-an2p"
expected_root="${user_home}/src/project/mooncen"
development_runtime=
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
            if [[ $# -lt 2 || ( "$2" != native && "$2" != docker ) ]]; then
                echo "--development-runtime must be native or docker." >&2
                exit 2
            fi
            development_runtime=$2
            shift 2
            ;;
        *)
            echo "Usage: $0 [--restart] [--development-runtime native|docker]" >&2
            exit 2
            ;;
    esac
done
if [[ -z "${development_runtime}" ]]; then
    if systemctl is-active --quiet mooncen-docker-dev.service; then
        development_runtime=docker
    else
        development_runtime=native
    fi
fi

# docker and lxd are both host-root capabilities.  Database/Docker lifecycle
# is available only through the fixed root helper installed by the isolated
# control-plane bootstrap; no long-lived user process may retain either group.
for privileged_group in docker lxd; do
    if id -nG | tr ' ' '\n' | grep -Fxq "${privileged_group}"; then
        echo "Current login still has ${privileged_group} host-root capability; a root operator must complete the reviewed root-of-trust bootstrap and phase-1 pair install, then you must log in again." >&2
        exit 78
    fi
done

system_units=(
    mooncen-ops-db-tunnel.service
    mooncen-ops-api.socket
    mooncen-ops-api.service
    mooncen-deployment-worker.service
    mooncen-docker-dev.service
)
for unit in "${system_units[@]}"; do
    path="/etc/systemd/system/${unit}"
    if [[ ! -f "${path}" || -L "${path}" ]] || \
       [[ "$(stat -c '%U:%G:%a' "${path}")" != root:root:644 ]]; then
        echo "Missing root-owned isolated system unit: ${unit}" >&2
        exit 78
    fi
done
legacy_system_user_units=(
    mooncen-ops-control-env.service
    mooncen-ops-db-tunnel.service
    mooncen-ops-api.service
    mooncen-deployment-worker.service
    mooncen-docker-dev.service
    mooncen-ops-console.service
)
for unit in "${legacy_system_user_units[@]}"; do
    mask="/etc/systemd/user/${unit}"
    if [[ ! -L "${mask}" || "$(readlink "${mask}")" != /dev/null ]]; then
        echo "Legacy user service is not globally masked: ${unit}" >&2
        exit 78
    fi
done

for protected in \
    /etc/mooncen-an2p/ops-api.env \
    /etc/mooncen-an2p/deployment-worker.env \
    /etc/mooncen-an2p/deploy-transport/id_ed25519 \
    /etc/mooncen-an2p/status-transport/id_ed25519 \
    /etc/mooncen-an2p/db-tunnel/id_ed25519 \
    /var/lib/mooncen-deployment-worker/releases; do
    if [[ -r "${protected}" || -w "${protected}" ]]; then
        echo "Untrusted development user can access isolated control material: ${protected}" >&2
        exit 78
    fi
done
for legacy in \
    "${user_home}/.config/mooncen-an2p/cloud-deploy.ssh_config" \
    "${user_home}/.config/mooncen-an2p/keys/cloud-deploy-ed25519" \
    "${user_home}/.config/mooncen-an2p/ops-api.env" \
    "${user_home}/.config/mooncen-an2p/deployment-worker.env"; do
    if [[ -e "${legacy}" || -L "${legacy}" ]]; then
        echo "Superseded shared service credential remains in the user home: ${legacy}" >&2
        exit 78
    fi
done

private_prerequisites=("${user_home}/.config/mooncen-an2p/status-agent.env")
if [[ "${development_runtime}" == native ]]; then
    private_prerequisites+=("${user_home}/.config/mooncen-an2p/api.env")
fi
for prerequisite in "${private_prerequisites[@]}"; do
    if [[ ! -f "${prerequisite}" || -L "${prerequisite}" ]] || \
       [[ "$(stat -c '%u:%a' "${prerequisite}")" != "$(id -u):600" ]]; then
        echo "an2p prerequisite must be user-owned mode 0600: ${prerequisite}" >&2
        exit 78
    fi
done

native_units=(mooncen-api.service mooncen-frontend.service)
auxiliary_units=(
    mooncen-status-agent.service
    mooncen-docs.service
)
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

if [[ "${development_runtime}" == docker ]]; then
    /usr/bin/sudo -n /usr/local/libexec/mooncen-an2p-service-control docker-select
    systemctl is-enabled mooncen-docker-dev.service >/dev/null
    systemctl is-active mooncen-docker-dev.service >/dev/null
    for unit in "${native_units[@]}"; do
        systemctl --user is-enabled --quiet "${unit}" && {
            echo "Native user runtime stayed enabled during Docker selection: ${unit}" >&2
            exit 78
        }
        systemctl --user is-active --quiet "${unit}" && {
            echo "Native user runtime stayed active during Docker selection: ${unit}" >&2
            exit 78
        }
    done
else
    /usr/bin/sudo -n /usr/local/libexec/mooncen-an2p-service-control native-select
    systemctl is-enabled --quiet mooncen-docker-dev.service && {
        echo "Docker system runtime stayed enabled during native selection." >&2
        exit 78
    }
    systemctl is-active --quiet mooncen-docker-dev.service && {
        echo "Docker system runtime stayed active during native selection." >&2
        exit 78
    }
    if [[ "${restart_services}" == true ]]; then
        /usr/bin/sudo -n /usr/local/libexec/mooncen-an2p-service-control native-select
    fi
fi

systemctl --user enable mooncen-development-runtime.target "${auxiliary_units[@]}" >/dev/null
if [[ "${restart_services}" == true ]]; then
    systemctl --user restart "${auxiliary_units[@]}"
else
    systemctl --user start "${auxiliary_units[@]}"
fi
systemctl --user start mooncen-development-runtime.target
systemctl --user reset-failed

for unit in mooncen-development-runtime.target "${auxiliary_units[@]}"; do
    systemctl --user is-enabled "${unit}" >/dev/null
    systemctl --user is-active "${unit}" >/dev/null
done
for unit in mooncen-ops-db-tunnel.service mooncen-ops-api.service \
  mooncen-deployment-worker.service; do
    systemctl is-enabled "${unit}" >/dev/null
    systemctl is-active "${unit}" >/dev/null
done
if [[ "${development_runtime}" == docker ]]; then
    systemctl is-active mooncen-docker-dev.service >/dev/null
else
    for unit in "${native_units[@]}"; do
        systemctl --user is-active "${unit}" >/dev/null
    done
fi

printf 'Installed %s development runtime plus isolated system control plane.\n' \
  "${development_runtime}"
