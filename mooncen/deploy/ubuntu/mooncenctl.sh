#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/mooncen}"
OPS_HELPER=/usr/local/libexec/mooncen-ops-service
SERVICES=(mooncen-frontend mooncen-api mooncen-ai-worker)
CRAWLER_UNITS=(mooncen-crawler.timer mooncen-crawler-once.service)
NODE_ROLE=unknown
if [ -f /etc/mooncen-node-role ] && [ ! -L /etc/mooncen-node-role ]; then
  NODE_ROLE="$(< /etc/mooncen-node-role)"
fi

is_crawler_owner() {
  [ "$NODE_ROLE" = "crawler" ]
}

require_crawler_owner() {
  if ! is_crawler_owner; then
    echo "This command is available only on the reviewed gen1crawler owner." >&2
    return 77
  fi
}

run_role_ops() {
  local action="$1"
  if [ ! -x "$OPS_HELPER" ]; then
    echo "Role-scoped operations helper is not installed. Re-run setup_project.sh or install_sudoers.sh." >&2
    return 66
  fi
  sudo -n "$OPS_HELPER" "$action"
}

usage() {
  cat <<'USAGE'
Usage: mooncenctl <command> [service]

Commands:
  summary         Show a compact operation summary
  status          Show MoonCen service status
  start           Start all MoonCen services
  stop            Stop all MoonCen services
  restart         Restart all MoonCen services
  health          Check local frontend/API through nginx and direct ports
  doctor          Run status, port, health, and coordinate checks
  ports           Show listening MoonCen ports
  logs [service]  Show recent logs. service: frontend, api, crawler, ai, nginx, cloudflared
  coordinates     Update missing branch addresses/coordinates with Kakao Local REST once
  locations       Alias of coordinates
  sitemap         Generate frontend sitemap.xml from active DB courses
  crawler-once    Run crawler one-shot service once
  ai-reset-start   Reset AI outputs and restart AI worker from the beginning
  ai-reset-full-start
                  Reset AI outputs plus target/age fields, then restart AI worker
  ai-quality       Print AI processing quality report
  functional-test  Run production functional tests now
  functional-test-status
                  Show functional test timer/status and latest report
  cloudflared-token
                  Read a tunnel token from stdin and install/update the Cloudflare Tunnel service
  cloudflare-gate-status
                  Show Cloudflare health gate timer/status and recent logs
  cloudflare-gate-enable
                  Enable Cloudflare health gate checks
  cloudflare-gate-disable
                  Disable Cloudflare health gate checks without removing timer
  cloudflared-role-guard-status
                  Show tunnel role guard status and recent logs
  cloudflared-role-guard-run
                  Run tunnel role guard now
  bot-status      Show Telegram ops bot status and recent logs
  bot-start       Start Telegram ops bot
  bot-stop        Stop Telegram ops bot
  ops-status      Show Ops Console status and recent logs
  ops-start       Start Ops Console on MOONCEN_OPS_PORT, default 8765
  ops-stop        Stop Ops Console
  ops-restart     Restart Ops Console
  backup-status   Show NAS backup timer/service status
  backup-list     List NAS backups
  backup-once     Run NAS backup now
  backup-test     Restore latest backup into a temporary DB and validate
  staging-status  Show crawler staging/apply service status
  staging-dry-run Validate latest staging batch without primary commit
  staging-apply   Apply latest staging batch to primary
  env             Print active /opt/mooncen/.env without secret values
USAGE
}

log_action() {
  case "${1:-}" in
    frontend) echo "logs-frontend" ;;
    api|backend|"") echo "logs-api" ;;
    crawler) require_crawler_owner; echo "logs-crawler" ;;
    ai|worker) echo "logs-ai" ;;
    nginx) echo "logs-nginx" ;;
    cloudflared) echo "logs-cloudflared" ;;
    cloudflare-gate) echo "logs-cloudflare-gate" ;;
    cloudflared-role-guard) echo "logs-role-guard" ;;
    bot|ops-bot) echo "logs-bot" ;;
    backup) echo "logs-backup" ;;
    functional-test) echo "logs-functional-test" ;;
    staging-apply) require_crawler_owner; echo "logs-staging" ;;
    *)
      echo "Unsupported log service: $1" >&2
      return 64
      ;;
  esac
}

status_all() {
  if is_crawler_owner; then
    systemctl --no-pager --full status "${CRAWLER_UNITS[@]}" || true
  else
    systemctl --no-pager --full status "${SERVICES[@]}" || true
  fi
}

health() {
  echo "API direct:"
  curl -fsS http://127.0.0.1:8001/health || true
  echo
  echo "API through nginx:"
  curl -fsS http://localhost/health || true
  echo
  echo "Frontend direct:"
  curl -fsSI http://127.0.0.1:5173 | head -n 5 || true
  echo "Frontend through nginx:"
  curl -fsSI http://localhost | head -n 5 || true
}

ports() {
  ss -lnt | grep -E ':80|:443|:5173|:5174|:8001' || true
}

coordinate_summary() {
  run_role_ops coordinate-summary
}

service_line() {
  local name="$1"
  local active enabled since
  active="$(systemctl is-active "$name" 2>/dev/null || true)"
  enabled="$(systemctl is-enabled "$name" 2>/dev/null || true)"
  since="$(systemctl show "$name" -p ActiveEnterTimestamp --value 2>/dev/null || true)"
  printf "%-20s %-9s enabled=%-8s %s\n" "$name" "${active:-unknown}" "${enabled:-unknown}" "$since"
}

http_line() {
  local label="$1"
  local url="$2"
  local code
  code="$(curl -fsS -o /dev/null -w "%{http_code}" --max-time 3 "$url" 2>/dev/null || true)"
  if [[ "$code" =~ ^[23][0-9][0-9]$ ]]; then
    printf "%-20s OK http=%s %s\n" "$label" "$code" "$url"
  elif [ -n "$code" ]; then
    printf "%-20s FAIL http=%s %s\n" "$label" "$code" "$url"
  else
    printf "%-20s FAIL %s\n" "$label" "$url"
  fi
}

db_summary() {
  run_role_ops db-summary
}

crawler_provider_config() {
  run_role_ops crawler-config
}

crawler_running_summary() {
  local line
  line="$(pgrep -af 'run_crawlers.py' | grep -v 'pgrep -af' | head -n 1 || true)"
  if [ -z "$line" ]; then
    echo "running_worker       none"
    return
  fi
  echo "running_worker       $line"
}

crawler_db_provider_summary() {
  run_role_ops crawler-provider-summary
}

crawler_report_summary() {
  local latest
  latest="$(ls -t "$APP_DIR"/logs/crawler_reports/crawler_report_*.json 2>/dev/null | head -n 1 || true)"
  if [ -z "$latest" ]; then
    echo "latest_report        none"
    return
  fi
  echo "latest_report        $latest"
  CRAWLER_CONFIGURED_PROVIDERS="$(crawler_provider_config)" "$APP_DIR/.venv/bin/python" - "$latest" <<'PY' 2>/dev/null || true
import json
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
print(f"report_time          {data.get('created_at') or data.get('finished_at') or ''}")
configured = os.environ.get("CRAWLER_CONFIGURED_PROVIDERS", "").split()
seen = set()
for provider in data.get("providers", []):
    name = provider.get("provider", "")
    if name:
        seen.add(name)
    success = provider.get("success")
    collected = provider.get("collected", provider.get("saved", ""))
    error = provider.get("error") or provider.get("note") or ""
    print(f"crawler_provider     {name:<9} success={success} collected={collected} {error[:80]}")
for name in configured:
    if name not in seen:
        print(f"crawler_provider     {name:<9} success=not_in_latest_report collected= latest report has no completed run for this provider")
PY
}

crawler_progress_summary() {
  local progress="$APP_DIR/logs/crawler_progress.json"
  if [ ! -f "$progress" ]; then
    echo "crawler_progress     status=unknown percent=0 completed=0/0 success=0 failed=0 running=- updated_at= next_run_at="
    return
  fi
  "$APP_DIR/.venv/bin/python" - "$progress" <<'PY' 2>/dev/null || true
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
total = int(data.get("total") or len(data.get("providers") or []) or 0)
completed = int(data.get("completed") or 0)
percent = data.get("progress_percent", 0)
running = ",".join(data.get("running") or []) or "-"
print(
    "crawler_progress     "
    f"status={data.get('status') or 'unknown'} "
    f"percent={percent} "
    f"completed={completed}/{total} "
    f"success={int(data.get('success') or 0)} "
    f"failed={int(data.get('failed') or 0)} "
    f"running={running} "
    f"updated_at={data.get('updated_at') or ''} "
    f"next_run_at={data.get('next_run_at') or ''}"
)
for row in data.get("providers") or []:
    provider = str(row.get("provider") or "")
    if not provider:
        continue
    elapsed = row.get("elapsed_seconds")
    elapsed_text = "" if elapsed is None else str(elapsed)
    print(
        f"crawler_progress_provider {provider:<24} "
        f"state={row.get('state') or 'unknown'} "
        f"success={row.get('success', '')} "
        f"elapsed={elapsed_text} "
        f"exit_code={row.get('exit_code', '')}"
    )
PY
}

functional_test_summary() {
  local latest="/var/lib/mooncen-check/latest.json"
  if [ ! -f "$latest" ]; then
    echo "functional_test     latest=none"
    return
  fi
  "$APP_DIR/.venv/bin/python" - "$latest" <<'PY' 2>/dev/null || true
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
summary = data.get("summary") or {}
print(
    "functional_test     "
    f"ok={data.get('ok')} "
    f"finished_at={data.get('finished_at') or ''} "
    f"passed={summary.get('passed')} "
    f"failed={summary.get('failed')} "
    f"skipped={summary.get('skipped')}"
)
for check in data.get("checks", []):
    if check.get("status") == "fail":
        print(f"functional_failure  {check.get('name')} {check.get('error') or ''}")
PY
}

install_cloudflared_token_service() {
  if [ "$#" -ne 0 ] || [ -t 0 ]; then
    echo "Usage: printf '%s\\n' \"\$TUNNEL_TOKEN\" | mooncenctl cloudflared-token"
    echo "The token is accepted only through standard input so it cannot appear in the process list."
    exit 2
  fi
  if ! /usr/bin/cloudflared tunnel run --help 2>&1 | grep -F -- '--token-file' >/dev/null; then
    echo "cloudflared 2025.4.0 or later is required for --token-file support."
    exit 1
  fi

  local helper=/usr/local/libexec/mooncen-cloudflared-token
  if [ ! -x "$helper" ]; then
    echo "Cloudflared token helper is not installed. Re-run setup_project.sh or install_sudoers.sh."
    exit 1
  fi
  if ! sudo -n "$helper" install; then
    echo "Cloudflared token install failed. Re-run install_sudoers.sh for the current deploy user."
    exit 1
  fi
  systemctl --no-pager --full status cloudflared.service | sed -n '1,20p' || true
}

summary() {
  echo "== Services =="
  for service in "${SERVICES[@]}"; do
    service_line "$service"
  done
  if is_crawler_owner; then
    for unit in "${CRAWLER_UNITS[@]}"; do
      service_line "$unit"
    done
  fi
  service_line nginx
  systemctl list-unit-files cloudflared.service >/dev/null 2>&1 && service_line cloudflared || true
  systemctl list-unit-files mooncen-cloudflare-gate.timer >/dev/null 2>&1 && service_line mooncen-cloudflare-gate.timer || true
  systemctl list-unit-files mooncen-cloudflared-role-guard.timer >/dev/null 2>&1 && service_line mooncen-cloudflared-role-guard.timer || true
  systemctl list-unit-files mooncen-ops-bot.service >/dev/null 2>&1 && service_line mooncen-ops-bot || true
  systemctl list-unit-files mooncen-backup.timer >/dev/null 2>&1 && service_line mooncen-backup.timer || true
  systemctl list-unit-files mooncen-functional-test.timer >/dev/null 2>&1 && service_line mooncen-functional-test.timer || true

  echo
  echo "== HTTP =="
  http_line "api-direct" "http://127.0.0.1:8001/health"
  http_line "nginx-health" "http://localhost/health"
  http_line "frontend-direct" "http://127.0.0.1:5173"
  http_line "frontend-nginx" "http://localhost"

  echo
  echo "== Ports =="
  ss -lnt | grep -E ':80|:443|:5173|:5174|:8001|:8765' || true

  if is_crawler_owner; then
    echo
    echo "== Database =="
    db_summary | while IFS=$'\t' read -r kind a b; do
      case "$kind" in
        db) printf "%-20s name=%s size=%s\n" "database" "$a" "$b" ;;
        courses) printf "%-20s count=%s latest_updated=%s\n" "courses" "$a" "$b" ;;
        branches) printf "%-20s count=%s with_coordinates=%s\n" "branches" "$a" "$b" ;;
      esac
    done
  fi

  echo
  echo "== Functional Test =="
  functional_test_summary

  if is_crawler_owner; then
    echo
    echo "== Crawler =="
    echo "configured_providers $(crawler_provider_config)"
    crawler_running_summary
    crawler_progress_summary
    crawler_report_summary
    echo
    echo "== Crawler DB Providers =="
    crawler_db_provider_summary | while IFS=$'\t' read -r provider total active latest desc branch; do
      printf "%-20s total=%-6s active=%-6s desc=%-6s branch=%-6s latest=%s\n" \
        "$provider" "$total" "$active" "$desc" "$branch" "$latest"
    done
  fi
}

case "${1:-}" in
  summary)
    summary
    ;;
  status)
    status_all
    ;;
  start)
    run_role_ops start-all
    ;;
  stop)
    run_role_ops stop-all
    ;;
  restart)
    run_role_ops restart-all
    ;;
  health)
    health
    ;;
  ports)
    ports
    ;;
  doctor)
    status_all
    echo
    ports
    echo
    health
    echo
    if is_crawler_owner; then
      coordinate_summary || true
    fi
    ;;
  logs)
    action="$(log_action "${2:-api}")"
    run_role_ops "$action"
    ;;
  coordinates|locations)
    require_crawler_owner
    echo "Updating branches with missing address/lat/lon via Kakao Local REST API."
    echo "Default scope: missing location fields only. Set CRAWLER_COORDINATE_BACKFILL_LIMIT to limit the batch."
    run_role_ops coordinate-backfill
    ;;
  sitemap)
    require_crawler_owner
    run_role_ops sitemap
    ;;
  crawler-once)
    require_crawler_owner
    run_role_ops crawler-once
    ;;
  ai-reset-start)
    echo "Stopping AI worker, resetting AI outputs, then starting AI worker."
    run_role_ops ai-reset
    ;;
  ai-reset-full-start)
    echo "Stopping AI worker, resetting AI outputs and target/age fields, then starting AI worker."
    run_role_ops ai-reset-full
    ;;
  ai-quality)
    run_role_ops ai-quality
    ;;
  functional-test)
    run_role_ops functional-test
    ;;
  functional-test-status)
    systemctl --no-pager --full status mooncen-functional-test.timer mooncen-functional-test.service || true
    echo
    functional_test_summary
    echo
    run_role_ops logs-functional-test || true
    ;;
  cloudflared-token)
    shift
    install_cloudflared_token_service "$@"
    ;;
  cloudflare-gate-status)
    systemctl --no-pager --full status mooncen-cloudflare-gate.timer mooncen-cloudflare-gate.service || true
    echo
    run_role_ops logs-cloudflare-gate || true
    ;;
  cloudflare-gate-enable)
    run_role_ops cloudflare-gate-enable
    ;;
  cloudflare-gate-disable)
    run_role_ops cloudflare-gate-disable
    ;;
  cloudflared-role-guard-status)
    echo "node_role $(cat /etc/mooncen-node-role 2>/dev/null || echo unknown)"
    systemctl --no-pager --full status mooncen-cloudflared-role-guard.timer mooncen-cloudflared-role-guard.service || true
    echo
    run_role_ops logs-role-guard || true
    ;;
  cloudflared-role-guard-run)
    run_role_ops role-guard-run
    ;;
  bot-status)
    systemctl --no-pager --full status mooncen-ops-bot.service || true
    echo
    run_role_ops logs-bot || true
    ;;
  bot-start)
    run_role_ops bot-start
    ;;
  bot-stop)
    run_role_ops bot-stop
    ;;
  ops-status)
    echo "Ops Console is a separate application. Run 'cd ops-console && npm run dev' on the operator workstation."
    ;;
  ops-start)
    echo "Ops Console is local-only. Remote start is disabled." >&2
    exit 64
    ;;
  ops-stop)
    echo "Ops Console is local-only. Remote stop is disabled." >&2
    exit 64
    ;;
  ops-restart)
    echo "Ops Console is local-only. Remote restart is disabled." >&2
    exit 64
    ;;
  backup-status)
    systemctl --no-pager --full status mooncen-backup.timer mooncen-backup.service || true
    echo
    run_role_ops logs-backup || true
    ;;
  backup-list)
    run_role_ops backup-list
    ;;
  backup-once)
    run_role_ops backup-once
    ;;
  backup-test)
    run_role_ops backup-test
    ;;
  staging-status)
    require_crawler_owner
    systemctl --no-pager --full status mooncen-staging-apply.timer mooncen-staging-apply.service || true
    echo
    run_role_ops logs-staging || true
    ;;
  staging-dry-run)
    require_crawler_owner
    run_role_ops staging-dry-run
    ;;
  staging-apply)
    require_crawler_owner
    run_role_ops staging-apply
    ;;
  env)
    if [ -f "$APP_DIR/.env" ]; then
      sed -E 's/(PASSWORD|SECRET|KEY)=.*/\1=***hidden***/' "$APP_DIR/.env"
    else
      echo "$APP_DIR/.env not found"
      exit 1
    fi
    ;;
  ""|-h|--help|help)
    usage
    ;;
  *)
    echo "Unknown command: $1"
    usage
    exit 2
    ;;
esac
