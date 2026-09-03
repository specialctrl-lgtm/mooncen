#!/usr/bin/env bash
set -euo pipefail

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  echo "Run this bootstrap script as root." >&2
  exit 77
fi

DEPLOY_USER="${1:-${SUDO_USER:-ubuntu}}"
SUDOERS_FILE=/etc/sudoers.d/mooncen-deploy
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLOUDFLARED_HELPER_SOURCE="$SCRIPT_DIR/cloudflared_token_helper.sh"
CLOUDFLARED_HELPER=/usr/local/libexec/mooncen-cloudflared-token
OPS_HELPER_SOURCE="$SCRIPT_DIR/ops_service_helper.sh"
OPS_RUNNER_SOURCE="$SCRIPT_DIR/../../tools/ops_service_action.py"
OPS_HELPER=/usr/local/libexec/mooncen-ops-service
OPS_RUNNER=/usr/local/libexec/mooncen-ops-service-action.py
POSTGRES_ROLE_HELPER_SOURCE="$SCRIPT_DIR/postgres_role_helper.sh"
POSTGRES_ROLE_HELPER=/usr/local/libexec/mooncen-postgres-role

case "$DEPLOY_USER" in
  ''|*[!a-zA-Z0-9_-]*) echo "Invalid deploy user: $DEPLOY_USER" >&2; exit 64 ;;
esac
for source in "$CLOUDFLARED_HELPER_SOURCE" "$OPS_HELPER_SOURCE" \
  "$OPS_RUNNER_SOURCE" "$POSTGRES_ROLE_HELPER_SOURCE"; do
  [ -f "$source" ] && [ ! -L "$source" ] || {
    echo "Missing or unsafe MoonCen helper: $source" >&2
    exit 66
  }
done
bash -n "$CLOUDFLARED_HELPER_SOURCE" "$OPS_HELPER_SOURCE" "$POSTGRES_ROLE_HELPER_SOURCE"

install -d -o root -g root -m 0755 /usr/local/libexec
install -o root -g root -m 0755 "$CLOUDFLARED_HELPER_SOURCE" "$CLOUDFLARED_HELPER"
install -o root -g root -m 0755 "$OPS_HELPER_SOURCE" "$OPS_HELPER"
install -o root -g root -m 0755 "$OPS_RUNNER_SOURCE" "$OPS_RUNNER"
install -o root -g root -m 0755 "$POSTGRES_ROLE_HELPER_SOURCE" "$POSTGRES_ROLE_HELPER"

cat >"$SUDOERS_FILE" <<EOF
# MoonCen native deployment grants only fixed role-scoped operations.
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
EOF

chmod 0440 "$SUDOERS_FILE"
visudo -cf "$SUDOERS_FILE"
echo "Installed MoonCen native operations sudoers rules for ${DEPLOY_USER}."
