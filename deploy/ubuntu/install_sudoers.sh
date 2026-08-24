#!/usr/bin/env bash
set -euo pipefail

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  echo "Run this bootstrap script as root. It intentionally does not grant passwordless shell or file-write commands." >&2
  exit 77
fi

DEPLOY_USER="${1:-${SUDO_USER:-ubuntu}}"
CONTAINER_DEPLOY_USER="${2:-mooncen_container_deploy}"
SUDOERS_FILE="/etc/sudoers.d/mooncen-deploy"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLOUDFLARED_HELPER_SOURCE="$SCRIPT_DIR/cloudflared_token_helper.sh"
CLOUDFLARED_HELPER=/usr/local/libexec/mooncen-cloudflared-token
OPS_HELPER_SOURCE="$SCRIPT_DIR/ops_service_helper.sh"
OPS_RUNNER_SOURCE="$SCRIPT_DIR/../../tools/ops_service_action.py"
OPS_HELPER=/usr/local/libexec/mooncen-ops-service
OPS_RUNNER=/usr/local/libexec/mooncen-ops-service-action.py
POSTGRES_ROLE_HELPER_SOURCE="$SCRIPT_DIR/postgres_role_helper.sh"
POSTGRES_ROLE_HELPER=/usr/local/libexec/mooncen-postgres-role
AN2P_CONTROL_EXPORT_SOURCE="$SCRIPT_DIR/export_an2p_control_secrets.py"
AN2P_CONTROL_EXPORT=/usr/local/libexec/mooncen-export-an2p-control-secrets
CONTAINER_BOOTSTRAP_SOURCE="$SCRIPT_DIR/../docker/bootstrap_production_runtime.py"
CONTAINER_INTEGRITY_SOURCE="$SCRIPT_DIR/../docker/production_runtime_integrity.py"
CONTAINER_BOOTSTRAP=/usr/local/libexec/mooncen-container-bootstrap
CONTAINER_INTEGRITY=/usr/local/libexec/production_runtime_integrity.py
CONTAINER_CONTROLLER=/usr/local/libexec/mooncen-container-release
CONTAINER_BOOTSTRAP_CONFIG=/etc/mooncen/container-bootstrap.json
REPOSITORY_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd -P)"
# The interactive native operator may only create/end the guard-owned native
# intent and read controller state. Container claim tokens and mutations are
# available exclusively through the forced deployment account provisioner.
CONTAINER_TOKEN_ARG='[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'

case "$DEPLOY_USER" in
  ''|*[!a-zA-Z0-9_-]*)
    echo "Invalid deploy user: $DEPLOY_USER" >&2
    exit 64
    ;;
esac
case "$CONTAINER_DEPLOY_USER" in
  ''|*[!a-zA-Z0-9_-]*)
    echo "Invalid container ingress user: $CONTAINER_DEPLOY_USER" >&2
    exit 64
    ;;
esac

if [ ! -f "$CLOUDFLARED_HELPER_SOURCE" ]; then
  echo "Missing Cloudflared token helper: $CLOUDFLARED_HELPER_SOURCE" >&2
  exit 66
fi
if [ ! -f "$OPS_HELPER_SOURCE" ] || [ ! -f "$OPS_RUNNER_SOURCE" ]; then
  echo "Missing MoonCen role-scoped operations helper or runner." >&2
  exit 66
fi
if [ ! -f "$POSTGRES_ROLE_HELPER_SOURCE" ]; then
  echo "Missing PostgreSQL role helper: $POSTGRES_ROLE_HELPER_SOURCE" >&2
  exit 66
fi
if [ ! -f "$AN2P_CONTROL_EXPORT_SOURCE" ] || [ -L "$AN2P_CONTROL_EXPORT_SOURCE" ]; then
  echo "Missing or unsafe an2p control-secret exporter: $AN2P_CONTROL_EXPORT_SOURCE" >&2
  exit 66
fi
if [ ! -f "$CONTAINER_BOOTSTRAP_SOURCE" ] || [ -L "$CONTAINER_BOOTSTRAP_SOURCE" ] || \
   [ ! -f "$CONTAINER_INTEGRITY_SOURCE" ] || [ -L "$CONTAINER_INTEGRITY_SOURCE" ]; then
  echo "Missing or unsafe MoonCen container bootstrap source." >&2
  exit 66
fi

bash -n "$CLOUDFLARED_HELPER_SOURCE" "$OPS_HELPER_SOURCE" "$POSTGRES_ROLE_HELPER_SOURCE"

install -d -o root -g root -m 0755 "$(dirname "$CLOUDFLARED_HELPER")"
install -o root -g root -m 0755 "$CLOUDFLARED_HELPER_SOURCE" "$CLOUDFLARED_HELPER"
install -o root -g root -m 0755 "$OPS_HELPER_SOURCE" "$OPS_HELPER"
install -o root -g root -m 0755 "$OPS_RUNNER_SOURCE" "$OPS_RUNNER"
install -o root -g root -m 0755 "$POSTGRES_ROLE_HELPER_SOURCE" "$POSTGRES_ROLE_HELPER"
install -o root -g root -m 0755 "$AN2P_CONTROL_EXPORT_SOURCE" "$AN2P_CONTROL_EXPORT"
install -o root -g root -m 0755 "$CONTAINER_BOOTSTRAP_SOURCE" "$CONTAINER_BOOTSTRAP"
install -o root -g root -m 0644 "$CONTAINER_INTEGRITY_SOURCE" "$CONTAINER_INTEGRITY"

install -d -o root -g root -m 0751 /etc/mooncen
if getent passwd "$CONTAINER_DEPLOY_USER" >/dev/null; then
  /usr/bin/python3 -I "$CONTAINER_INTEGRITY" write-bootstrap-config \
    --source-root "$REPOSITORY_ROOT" \
    --deploy-user "$CONTAINER_DEPLOY_USER" \
    --deploy-uid "$(id -u "$CONTAINER_DEPLOY_USER")" \
    --deploy-gid "$(id -g "$CONTAINER_DEPLOY_USER")" \
    --output "$CONTAINER_BOOTSTRAP_CONFIG"
  [ "$(stat -c '%U:%G:%a' "$CONTAINER_BOOTSTRAP_CONFIG")" = "root:root:600" ] || {
    echo "Container bootstrap configuration metadata is unsafe." >&2
    exit 78
  }
else
  # Native setup may precede the dedicated endpoint provisioner.  Never write
  # an ubuntu/operator UID as a fallback: the root endpoint provisioner will
  # atomically create the exact dedicated identity before bootstrap is usable.
  if [ -e "$CONTAINER_BOOTSTRAP_CONFIG" ] || [ -L "$CONTAINER_BOOTSTRAP_CONFIG" ]; then
    echo "Dedicated container ingress account is absent but a bootstrap identity exists." >&2
    exit 78
  fi
fi

cat > "$SUDOERS_FILE" <<EOF
# MoonCen grants only fixed role-scoped operations without a password.
# Cloudflared token install/read is confined to a root-owned helper. It reads
# tokens only through stdin/stdout and accepts no token-bearing arguments.
# The exporter itself rejects a terminal or regular-file stdout. Override
# Ubuntu's command PTY default only for this exact no-argument executable so
# the reviewed SSH handoff remains a kernel pipe/socket end to end.
Defaults!${AN2P_CONTROL_EXPORT} !use_pty
Cmnd_Alias MOONCEN_ROLE_OPS = \
  ${OPS_HELPER} crawler-provider, \
  ${OPS_HELPER} cleanup-ended-courses, \
  ${OPS_HELPER} db-summary, \
  ${OPS_HELPER} coordinate-summary, \
  ${OPS_HELPER} coordinate-backfill, \
  ${OPS_HELPER} crawler-config, \
  ${OPS_HELPER} crawler-provider-summary, \
  ${OPS_HELPER} replication-summary, \
  ${OPS_HELPER} ai-reset, \
  ${OPS_HELPER} ai-reset-full, \
  ${OPS_HELPER} ai-quality, \
  ${OPS_HELPER} ollama-test, \
  ${OPS_HELPER} sitemap, \
  ${OPS_HELPER} ai-worker-start, \
  ${OPS_HELPER} ai-worker-stop, \
  ${OPS_HELPER} start-all, \
  ${OPS_HELPER} stop-all, \
  ${OPS_HELPER} restart-all, \
  ${OPS_HELPER} restart-frontend, \
  ${OPS_HELPER} restart-api, \
  ${OPS_HELPER} restart-crawler, \
  ${OPS_HELPER} restart-ai, \
  ${OPS_HELPER} restart-nginx, \
  ${OPS_HELPER} restart-cloudflared, \
  ${OPS_HELPER} logs-frontend, \
  ${OPS_HELPER} logs-api, \
  ${OPS_HELPER} logs-crawler, \
  ${OPS_HELPER} logs-ai, \
  ${OPS_HELPER} logs-nginx, \
  ${OPS_HELPER} logs-cloudflared, \
  ${OPS_HELPER} logs-functional-test, \
  ${OPS_HELPER} logs-cloudflare-gate, \
  ${OPS_HELPER} logs-role-guard, \
  ${OPS_HELPER} logs-bot, \
  ${OPS_HELPER} logs-backup, \
  ${OPS_HELPER} logs-staging, \
  ${OPS_HELPER} crawler-once, \
  ${OPS_HELPER} functional-test, \
  ${OPS_HELPER} cloudflare-gate-enable, \
  ${OPS_HELPER} cloudflare-gate-disable, \
  ${OPS_HELPER} role-guard-run, \
  ${OPS_HELPER} bot-start, \
  ${OPS_HELPER} bot-stop, \
  ${OPS_HELPER} backup-once, \
  ${OPS_HELPER} backup-list, \
  ${OPS_HELPER} backup-test, \
  ${OPS_HELPER} staging-dry-run, \
  ${OPS_HELPER} staging-apply, \
  ${OPS_HELPER} staging-promote-provider, \
  ${OPS_HELPER} failover-disable
${DEPLOY_USER} ALL=(root) NOPASSWD: ${CLOUDFLARED_HELPER} install, ${CLOUDFLARED_HELPER} read
${DEPLOY_USER} ALL=(root) NOPASSWD: MOONCEN_ROLE_OPS
${DEPLOY_USER} ALL=(postgres) NOPASSWD: ${POSTGRES_ROLE_HELPER}
${DEPLOY_USER} ALL=(root) NOPASSWD: ${AN2P_CONTROL_EXPORT}
${DEPLOY_USER} ALL=(root) NOPASSWD: ${CONTAINER_BOOTSTRAP}
${DEPLOY_USER} ALL=(root) NOPASSWD: ${CONTAINER_CONTROLLER} native-begin ${CONTAINER_TOKEN_ARG}, ${CONTAINER_CONTROLLER} native-end ${CONTAINER_TOKEN_ARG}, ${CONTAINER_CONTROLLER} status, ${CONTAINER_CONTROLLER} target-identity
EOF

chmod 440 "$SUDOERS_FILE"
visudo -cf "$SUDOERS_FILE"
echo "Installed exact MoonCen operations and Cloudflared helper sudoers rules for ${DEPLOY_USER}: ${SUDOERS_FILE}"
