#!/usr/bin/env bash
set -euo pipefail
umask 077

APP_DIR="${APP_DIR:-/opt/mooncen}"
EXPECTED_BATCH_ID=""

usage() {
  cat >&2 <<'EOF'
Usage: activate_split_crawler.sh --batch-id CRAWL_BATCH_ID

Validates and applies the named, fully-collected staging batch on the current
gen1crawler owner, then enables its crawler and staging-apply timers.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --batch-id)
      EXPECTED_BATCH_ID="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage
      exit 64
      ;;
  esac
done

if [ "$(id -u)" -ne 0 ]; then
  echo "Run activate_split_crawler.sh through sudo." >&2
  exit 77
fi
split_runtime_lock=/run/lock/mooncen-split-crawler.lock
if ! command -v flock >/dev/null 2>&1; then
  echo "flock is required to serialize split crawler setup and activation." >&2
  exit 69
fi
if [ ! -e "$split_runtime_lock" ] && [ ! -L "$split_runtime_lock" ]; then
  install -o root -g root -m 0600 /dev/null "$split_runtime_lock"
fi
if [ ! -f "$split_runtime_lock" ] || [ -L "$split_runtime_lock" ] || \
   [ "$(stat -c '%U:%G:%a' "$split_runtime_lock")" != "root:root:600" ]; then
  echo "Split crawler runtime lock is unsafe." >&2
  exit 78
fi
exec 9<>"$split_runtime_lock"
if ! flock -n 9; then
  echo "Another split crawler setup or activation owns the runtime lock." >&2
  exit 75
fi
if [[ ! "$EXPECTED_BATCH_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$ ]]; then
  echo "--batch-id is required and contains an invalid crawl batch id." >&2
  exit 64
fi
if [ "$APP_DIR" != "/opt/mooncen" ] || [ ! -d "$APP_DIR" ] || [ -L "$APP_DIR" ]; then
  echo "A regular MoonCen release must exist at /opt/mooncen." >&2
  exit 66
fi
if [ "$(stat -c '%U:%G:%a' "$APP_DIR")" != "root:mooncen:750" ]; then
  echo "Split crawler release root must be immutable root:mooncen mode 0750." >&2
  exit 78
fi
if [ "$(cat /etc/mooncen-node-role 2>/dev/null || true)" != "crawler" ]; then
  echo "Split crawler activation requires node role crawler." >&2
  exit 78
fi

for required_file in \
  "$APP_DIR/tools/apply_staging_batch.py" \
  "$APP_DIR/tools/run_pinned_staging_dry_run.py" \
  "$APP_DIR/tools/run_pinned_staging_apply.py" \
  "$APP_DIR/tools/validate_staging_activation_result.py" \
  "$APP_DIR/.deploy-meta" \
  /etc/mooncen/crawler.env \
  /etc/mooncen/applier.env \
  /etc/systemd/system/mooncen-crawler.service \
  /etc/systemd/system/mooncen-crawler.timer \
  /etc/systemd/system/mooncen-crawler-once.service \
  /etc/systemd/system/mooncen-staging-apply.timer \
  /etc/systemd/system/mooncen-staging-apply.service \
  /etc/systemd/system/mooncen-staging-apply@.service \
  /etc/systemd/system/mooncen-staging-apply-dry-run@.service; do
  if [ ! -f "$required_file" ] || [ -L "$required_file" ]; then
    echo "Required split crawler file is unavailable or unsafe: $required_file" >&2
    exit 66
  fi
done
if [ "$(stat -c '%U:%G:%a' "$APP_DIR/.deploy-meta")" != "root:mooncen:640" ]; then
  echo "Crawler deployment provenance metadata is not protected." >&2
  exit 78
fi

for unit in \
  mooncen-crawler.service \
  mooncen-crawler.timer \
  mooncen-crawler-once.service \
  mooncen-staging-apply.timer \
  mooncen-staging-apply.service \
  mooncen-staging-apply@.service \
  mooncen-staging-apply-dry-run@.service; do
  if ! cmp -s \
    "$APP_DIR/deploy/ubuntu/systemd/$unit" \
    "/etc/systemd/system/$unit"; then
    echo "Installed systemd unit does not match the reviewed release: $unit" >&2
    exit 78
  fi
done
if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
  echo "Split crawler Python environment is not executable." >&2
  exit 66
fi

declare -A expected_env_groups=(
  [/etc/mooncen/crawler.env]=mooncen-crawler
  [/etc/mooncen/applier.env]=mooncen-applier
)
for env_file in /etc/mooncen/crawler.env /etc/mooncen/applier.env; do
  mode="$(stat -c '%a' "$env_file")"
  owner="$(stat -c '%U' "$env_file")"
  group="$(stat -c '%G' "$env_file")"
  if [ "$owner" != "root" ] || \
     [ "$group" != "${expected_env_groups[$env_file]}" ] || \
     [ "$mode" != "640" ]; then
    echo "Protected environment owner, group, or mode is unsafe: $env_file" >&2
    exit 78
  fi
done
if ! awk '
  index($0, "KAKAO_MAPS_REST_API_KEY=") == 1 {
    count += 1
    value = substr($0, length("KAKAO_MAPS_REST_API_KEY=") + 1)
  }
  END {
    if (count != 1 || value !~ /^[A-Za-z0-9_-]+$/) {
      exit 1
    }
  }
' /etc/mooncen/crawler.env; then
  echo "Protected crawler environment lacks a valid Kakao REST API key." >&2
  exit 78
fi
if [ "$(timedatectl show --property=Timezone --value 2>/dev/null || true)" != "Asia/Seoul" ]; then
  echo "Split crawler activation requires the host timezone Asia/Seoul." >&2
  exit 78
fi

if ! systemctl disable --now mooncen-crawler.service >/dev/null; then
  echo "Failed to strictly disable the competing long-running crawler service." >&2
  exit 70
fi
if systemctl is-active --quiet mooncen-crawler.service || \
   systemctl is-enabled --quiet mooncen-crawler.service; then
  echo "Long-running crawler service remains active or enabled." >&2
  exit 70
fi
for service in mooncen-crawler-once.service mooncen-staging-apply.service; do
  if systemctl is-active --quiet "$service"; then
    echo "Competing one-shot service is already active: $service" >&2
    exit 70
  fi
done
if ! active_template_output="$(
  systemctl list-units \
    --type=service \
    --state=active,activating,reloading,deactivating \
    --plain \
    --no-legend \
    'mooncen-staging-apply@*.service' \
    'mooncen-staging-apply-dry-run@*.service' 2>/dev/null |
    awk 'NF {print $1}'
)"; then
  echo "Unable to inspect active pinned staging units." >&2
  exit 70
fi
active_template_units=()
if [ -n "$active_template_output" ]; then
  mapfile -t active_template_units <<<"$active_template_output"
fi
if [ "${#active_template_units[@]}" -ne 0 ]; then
  printf 'Pinned staging unit is already active: %s\n' \
    "${active_template_units[*]}" >&2
  exit 70
fi
for timer in mooncen-crawler.timer mooncen-staging-apply.timer; do
  if systemctl is-active --quiet "$timer" || systemctl is-enabled --quiet "$timer"; then
    echo "Automation timer must be inactive and disabled before activation: $timer" >&2
    exit 70
  fi
done
if ! pg_isready -q -h localhost -p 55432 -d mooncen_staging; then
  echo "Dedicated crawler staging database is not ready." >&2
  exit 69
fi

result_dir="/run/mooncen-staging-apply"
dry_run_result="$result_dir/dry-run-${EXPECTED_BATCH_ID}.json"
apply_result="$result_dir/apply-${EXPECTED_BATCH_ID}.json"

read_exact_value() {
  local key="$1"
  local path="$2"
  awk -v expected="$key" '
    index($0, expected "=") == 1 {
      count += 1
      value = substr($0, length(expected) + 2)
    }
    END {
      if (count != 1) {
        exit 65
      }
      printf "%s", value
    }
  ' "$path"
}

validate_result_files() {
  if [ ! -f "$dry_run_result" ] || [ -L "$dry_run_result" ] || \
     [ "$(stat -c '%U:%G:%a' "$dry_run_result")" != "mooncen-applier:mooncen-applier:600" ]; then
    echo "Pinned staging dry-run result is unavailable or unsafe." >&2
    return 70
  fi
  if [ ! -f "$apply_result" ] || [ -L "$apply_result" ] || \
     [ "$(stat -c '%U:%G:%a' "$apply_result")" != "root:root:600" ]; then
    echo "Pinned staging apply result is unavailable or unsafe." >&2
    return 70
  fi
  "$APP_DIR/.venv/bin/python" -I \
    "$APP_DIR/tools/validate_staging_activation_result.py" \
    --mode dry-run \
    --batch-id "$EXPECTED_BATCH_ID" \
    --result-file "$dry_run_result" || return 70
  "$APP_DIR/.venv/bin/python" -I \
    "$APP_DIR/tools/validate_staging_activation_result.py" \
    --mode apply \
    --batch-id "$EXPECTED_BATCH_ID" \
    --result-file "$apply_result" \
    --dry-run-result-file "$dry_run_result" || return 70
}

deploy_commit="$(read_exact_value DEPLOY_COMMIT "$APP_DIR/.deploy-meta")" || {
  echo "Unable to read the reviewed crawler deployment commit." >&2
  exit 78
}
deploy_archive_sha256="$(read_exact_value DEPLOY_ARCHIVE_SHA256 "$APP_DIR/.deploy-meta")" || {
  echo "Unable to read the reviewed crawler archive digest." >&2
  exit 78
}
if [[ ! "$deploy_commit" =~ ^([0-9a-f]{40}|[0-9a-f]{64})$ ]] || \
   [[ ! "$deploy_archive_sha256" =~ ^[0-9a-f]{64}$ ]]; then
  echo "Crawler deployment provenance is incomplete." >&2
  exit 78
fi

dry_run_unit="mooncen-staging-apply-dry-run@${EXPECTED_BATCH_ID}.service"
systemctl reset-failed "$dry_run_unit" >/dev/null 2>&1 || true
rm -f -- "$dry_run_result"
if ! systemctl start "$dry_run_unit" || \
   [ "$(systemctl show "$dry_run_unit" -p Result --value)" != "success" ] || \
   [ "$(systemctl show "$dry_run_unit" -p ExecMainStatus --value)" != "0" ]; then
  echo "Staging-to-primary dry-run failed for batch: $EXPECTED_BATCH_ID" >&2
  exit 70
fi

apply_unit="mooncen-staging-apply@${EXPECTED_BATCH_ID}.service"
systemctl reset-failed "$apply_unit" >/dev/null 2>&1 || true
rm -f -- "$apply_result"
if ! systemctl start "$apply_unit" || \
   [ "$(systemctl show "$apply_unit" -p Result --value)" != "success" ] || \
   [ "$(systemctl show "$apply_unit" -p ExecMainStatus --value)" != "0" ]; then
  echo "Pinned staging-to-primary apply failed for batch: $EXPECTED_BATCH_ID" >&2
  exit 70
fi
if ! validate_result_files; then
  echo "Pinned staging activation failed semantic validation." >&2
  exit 70
fi

# The split node uses nightly one-shot collection. Do not enable the competing
# long-running crawler service. Clearing the old persistent state stamp after
# the reviewed exact apply suppresses only the first missed-run catch-up;
# Persistent=true continues to recover later downtime.
timer_units=(mooncen-crawler.timer mooncen-staging-apply.timer)
rollback_timer_activation=1
rollback_timers() {
  if [ "$rollback_timer_activation" -eq 1 ]; then
    echo "Timer activation failed; disabling both automation timers." >&2
    if ! systemctl disable --now "${timer_units[@]}" >/dev/null 2>&1 || \
       ! systemctl stop mooncen-crawler-once.service mooncen-staging-apply.service \
         >/dev/null 2>&1; then
      echo "WARNING: timer rollback failed; manual intervention is required." >&2
    fi
    for rollback_unit in \
      "${timer_units[@]}" \
      mooncen-crawler-once.service \
      mooncen-staging-apply.service; do
      if systemctl is-active --quiet "$rollback_unit"; then
        echo "WARNING: rollback left a crawler unit active: $rollback_unit" >&2
      fi
    done
  fi
}
trap rollback_timers EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

systemctl clean --what=state "${timer_units[@]}"
systemctl enable "${timer_units[@]}"
systemctl start "${timer_units[@]}"

for timer in "${timer_units[@]}"; do
  systemctl is-enabled --quiet "$timer"
  systemctl is-active --quiet "$timer"
done

rollback_timer_activation=0
trap - EXIT INT TERM
echo "MoonCen split crawler automation enabled after validated batch: $EXPECTED_BATCH_ID"
