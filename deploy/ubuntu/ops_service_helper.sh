#!/usr/bin/env bash
set -euo pipefail
umask 077

APP_DIR=/opt/mooncen
RUNNER=/usr/local/libexec/mooncen-ops-service-action.py

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  echo "mooncen ops helper must run as root" >&2
  exit 77
fi

if [ "$#" -ne 1 ]; then
  echo "usage: mooncen-ops-service ACTION" >&2
  exit 64
fi

action="$1"

NODE_ROLE_FILE=/etc/mooncen-node-role
if [ ! -f "$NODE_ROLE_FILE" ] || [ -L "$NODE_ROLE_FILE" ] || \
   [ "$(stat -c '%U:%G:%a' "$NODE_ROLE_FILE")" != "root:root:644" ]; then
  echo "mooncen ops helper requires a protected node-role file" >&2
  exit 78
fi
NODE_ROLE="$(< "$NODE_ROLE_FILE")"
case "$NODE_ROLE" in
  primary|standby|crawler) ;;
  *)
    echo "mooncen ops helper found an unsupported node role" >&2
    exit 78
    ;;
esac

require_crawler_owner() {
  if [ "$NODE_ROLE" != "crawler" ]; then
    echo "mooncen ops helper: crawler action requires the gen1crawler owner" >&2
    exit 77
  fi
}

MAIN_SERVICES=(
  mooncen-frontend.service
  mooncen-api.service
  mooncen-ai-worker.service
)
CRAWLER_SCHEDULER=mooncen-crawler.timer
CRAWLER_RUNNER=mooncen-crawler-once.service

recent_logs() {
  local unit="$1"
  local lines="${2:-120}"
  /usr/bin/journalctl -u "$unit" -n "$lines" --no-pager
}

run_oneshot() {
  local unit="$1"
  local lines="${2:-120}"
  local status=0

  /usr/bin/systemctl start "$unit" || status=$?
  recent_logs "$unit" "$lines" || true
  return "$status"
}

case "$action" in
  start-all)
    if [ "$NODE_ROLE" = "crawler" ]; then
      /usr/bin/systemctl start "$CRAWLER_SCHEDULER"
      exec /usr/bin/systemctl is-active "$CRAWLER_SCHEDULER"
    fi
    /usr/bin/systemctl start "${MAIN_SERVICES[@]}"
    exec /usr/bin/systemctl is-active "${MAIN_SERVICES[@]}"
    ;;
  stop-all)
    if [ "$NODE_ROLE" = "crawler" ]; then
      /usr/bin/systemctl stop "$CRAWLER_SCHEDULER" "$CRAWLER_RUNNER"
      /usr/bin/systemctl is-active "$CRAWLER_SCHEDULER" || true
      exit 0
    fi
    /usr/bin/systemctl stop "${MAIN_SERVICES[@]}"
    /usr/bin/systemctl is-active "${MAIN_SERVICES[@]}" || true
    exit 0
    ;;
  restart-all)
    if [ "$NODE_ROLE" = "crawler" ]; then
      /usr/bin/systemctl restart "$CRAWLER_SCHEDULER"
      exec /usr/bin/systemctl is-active "$CRAWLER_SCHEDULER"
    fi
    /usr/bin/systemctl restart "${MAIN_SERVICES[@]}"
    exec /usr/bin/systemctl is-active "${MAIN_SERVICES[@]}"
    ;;
  ai-worker-start)
    /usr/bin/systemctl start mooncen-ai-worker.service
    exec /usr/bin/systemctl is-active mooncen-ai-worker.service
    ;;
  ai-worker-stop)
    /usr/bin/systemctl stop mooncen-ai-worker.service
    /usr/bin/systemctl is-active mooncen-ai-worker.service || true
    exit 0
    ;;
  restart-frontend) service_unit=mooncen-frontend.service ;;
  restart-api) service_unit=mooncen-api.service ;;
  restart-crawler) require_crawler_owner; service_unit="$CRAWLER_SCHEDULER" ;;
  restart-ai) service_unit=mooncen-ai-worker.service ;;
  restart-nginx) service_unit=nginx.service ;;
  restart-cloudflared) service_unit=cloudflared.service ;;
  logs-frontend) log_unit=mooncen-frontend.service ;;
  logs-api) log_unit=mooncen-api.service ;;
  logs-crawler) require_crawler_owner; log_unit="$CRAWLER_RUNNER" ;;
  logs-ai) log_unit=mooncen-ai-worker.service ;;
  logs-nginx) log_unit=nginx.service ;;
  logs-cloudflared) log_unit=cloudflared.service ;;
  logs-functional-test) log_unit=mooncen-functional-test.service ;;
  logs-cloudflare-gate) log_unit=mooncen-cloudflare-gate.service ;;
  logs-role-guard) log_unit=mooncen-cloudflared-role-guard.service ;;
  logs-bot) log_unit=mooncen-ops-bot.service ;;
  logs-backup) log_unit=mooncen-backup.service ;;
  logs-staging) require_crawler_owner; log_unit=mooncen-staging-apply.service ;;
  crawler-once)
    require_crawler_owner
    run_oneshot "$CRAWLER_RUNNER"
    exit $?
    ;;
  functional-test)
    run_oneshot mooncen-functional-test.service
    exit $?
    ;;
  cloudflare-gate-enable)
    /usr/bin/install -d -o root -g mooncen -m 0750 "$APP_DIR/failover"
    /usr/bin/rm -f -- "$APP_DIR/failover/disable_cloudflare_gate"
    /usr/bin/systemctl enable --now mooncen-cloudflare-gate.timer
    exec /usr/bin/systemctl is-active mooncen-cloudflare-gate.timer
    ;;
  cloudflare-gate-disable)
    /usr/bin/install -d -o root -g mooncen -m 0750 "$APP_DIR/failover"
    /usr/bin/install -o root -g mooncen -m 0640 /dev/null "$APP_DIR/failover/disable_cloudflare_gate"
    echo "Cloudflare health gate disabled by $APP_DIR/failover/disable_cloudflare_gate"
    exit 0
    ;;
  role-guard-run)
    run_oneshot mooncen-cloudflared-role-guard.service 80
    /usr/bin/systemctl is-active cloudflared.service || true
    exit 0
    ;;
  bot-start)
    /usr/bin/systemctl enable --now mooncen-ops-bot.service
    exec /usr/bin/systemctl is-active mooncen-ops-bot.service
    ;;
  bot-stop)
    /usr/bin/systemctl disable --now mooncen-ops-bot.service
    /usr/bin/systemctl is-active mooncen-ops-bot.service || true
    exit 0
    ;;
  backup-once)
    run_oneshot mooncen-backup.service 80
    exit $?
    ;;
  backup-list)
    exec /usr/sbin/runuser -u mooncen-backup -- \
      /bin/bash /usr/local/libexec/mooncen-backup/mooncen_backup_list_wtr_nas.sh
    ;;
  backup-test)
    run_oneshot mooncen-backup-restore-test.service 120
    exit $?
    ;;
  staging-dry-run)
    require_crawler_owner
    run_oneshot mooncen-staging-apply-dry-run.service
    exit $?
    ;;
  staging-apply)
    require_crawler_owner
    run_oneshot mooncen-staging-apply.service
    exit $?
    ;;
  failover-disable)
    rm -f -- "$APP_DIR/failover/enable_auto_failover"
    echo disabled
    exit 0
    ;;
esac

if [ -n "${service_unit:-}" ]; then
  /usr/bin/systemctl restart "$service_unit"
  exec /usr/bin/systemctl is-active "$service_unit"
fi
if [ -n "${log_unit:-}" ]; then
  exec /usr/bin/journalctl -u "$log_unit" -n 120 --no-pager
fi

case "$action" in
  crawler-provider|coordinate-backfill|db-summary|coordinate-summary|crawler-config|crawler-provider-summary|replication-summary)
    require_crawler_owner
    service_user=mooncen-crawler
    ;;
  cleanup-ended-courses)
    service_user=mooncen-applier
    ;;
  staging-promote-provider)
    if [ ! -f /etc/mooncen-node-role ]; then
      echo "mooncen ops helper: staging promotion requires a standby or crawler node" >&2
      exit 77
    fi
    case "$(< /etc/mooncen-node-role)" in
      standby|crawler)
        ;;
      *)
        echo "mooncen ops helper: staging promotion requires a standby or crawler node" >&2
        exit 77
        ;;
    esac
    service_user=mooncen-applier
    ;;
  ai-reset|ai-reset-full|ai-quality|ollama-test)
    service_user=mooncen-ai
    ;;
  sitemap)
    require_crawler_owner
    service_user=mooncen-crawler
    ;;
  *)
    echo "mooncen ops helper: unsupported action" >&2
    exit 64
    ;;
esac

if [ ! -f "$RUNNER" ] || [ -L "$RUNNER" ]; then
  echo "mooncen ops helper: root-owned action runner is unavailable" >&2
  exit 66
fi
runner_owner="$(stat -c '%U:%G' "$RUNNER")"
runner_mode="$(stat -c '%a' "$RUNNER")"
if [ "$runner_owner" != "root:root" ] || (( (8#$runner_mode & 8#022) != 0 )); then
  echo "mooncen ops helper: unsafe action runner ownership or mode" >&2
  exit 78
fi

run_action() {
  local sitemap_output="${1:-}"
  local runtime_home
  local action_status=0
  runtime_home="$(/usr/bin/mktemp -d /tmp/mooncen-ops-runtime.XXXXXX)"
  /usr/bin/chown "$service_user:$service_user" "$runtime_home"
  /usr/bin/chmod 0700 "$runtime_home"
  /usr/bin/install -d -o "$service_user" -g "$service_user" -m 0700 \
    "$runtime_home/.cache" "$runtime_home/.config"
  local -a clean_env=(
    /usr/bin/env -i
    "HOME=$runtime_home"
    "TMPDIR=$runtime_home"
    "TMP=$runtime_home"
    "TEMP=$runtime_home"
    "XDG_CACHE_HOME=$runtime_home/.cache"
    "XDG_CONFIG_HOME=$runtime_home/.config"
    "XDG_RUNTIME_DIR=$runtime_home"
    "LANG=C.UTF-8"
    "LC_ALL=C.UTF-8"
    "PATH=$APP_DIR/.venv/bin:/usr/bin:/bin"
    "PYTHONUNBUFFERED=1"
    "MOONCEN_APP_DIR=$APP_DIR"
    "MOONCEN_OPS_SERVICE_ACTION=1"
  )
  if [ -n "$sitemap_output" ]; then
    clean_env+=("MOONCEN_SITEMAP_OUTPUT=$sitemap_output")
  fi
  /usr/sbin/runuser -u "$service_user" -- \
    "${clean_env[@]}" \
    "$APP_DIR/.venv/bin/python" -I -X utf8 "$RUNNER" "$action" \
    || action_status=$?
  /usr/bin/rm -rf -- "$runtime_home"
  return "$action_status"
}

case "$action" in
  ai-reset|ai-reset-full)
    restart_pending=1
    restart_ai() {
      if [ "$restart_pending" -eq 1 ]; then
        /usr/bin/systemctl start mooncen-ai-worker.service >/dev/null 2>&1 || true
      fi
    }
    trap restart_ai EXIT HUP INT TERM
    /usr/bin/systemctl stop mooncen-ai-worker.service || true
    action_rc=0
    run_action || action_rc=$?
    /usr/bin/systemctl start mooncen-ai-worker.service
    restart_pending=0
    trap - EXIT HUP INT TERM
    /usr/bin/systemctl is-active mooncen-ai-worker.service
    exit "$action_rc"
    ;;
  sitemap)
    sitemap_tmp_dir="$(mktemp -d /var/tmp/mooncen-sitemap.XXXXXX)"
    trap 'rm -rf -- "$sitemap_tmp_dir"' EXIT HUP INT TERM
    chown "$service_user:$service_user" "$sitemap_tmp_dir"
    chmod 0700 "$sitemap_tmp_dir"
    sitemap_tmp="$sitemap_tmp_dir/sitemap.xml"
    run_action "$sitemap_tmp"
    if [ ! -s "$sitemap_tmp" ] || [ -L "$sitemap_tmp" ]; then
      echo "mooncen ops helper: sitemap output is missing or unsafe" >&2
      exit 70
    fi
    install -D -o mooncen-crawler -g mooncen -m 0640 "$sitemap_tmp" "$APP_DIR/frontend2/public/sitemap.xml"
    install -D -o mooncen-crawler -g mooncen -m 0640 "$sitemap_tmp" "$APP_DIR/frontend2/dist/sitemap.xml"
    ;;
  *)
    exec 3<&0
    run_action <&3
    ;;
esac
