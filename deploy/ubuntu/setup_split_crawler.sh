#!/usr/bin/env bash
set -euo pipefail
umask 077

APP_DIR=/opt/mooncen
CONFIG_DIR=/etc/mooncen
DB_TLS_GROUP=mooncen-db-tls
CRAWLER_USER=mooncen-crawler
APPLIER_USER=mooncen-applier
NODE_SECRETS_FILE="$CONFIG_DIR/crawler-node.env"
PRODUCTION_PROVIDERS_FILE="$APP_DIR/config/production_crawler_providers.yaml"

db_client_env=""
db_ca=""
source_crawler_env=""
site_url="https://mooncen.kr"
deploy_commit=""
deploy_archive_sha256=""

usage() {
  cat >&2 <<'EOF'
Usage: setup_split_crawler.sh \
  --db-client-env PATH \
  --db-ca PATH \
  --source-crawler-env PATH \
  [--site-url URL] \
  --deploy-commit HASH \
  --deploy-archive-sha256 SHA256
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --db-client-env)
      db_client_env="${2:-}"
      shift 2
      ;;
    --db-ca)
      db_ca="${2:-}"
      shift 2
      ;;
    --source-crawler-env)
      source_crawler_env="${2:-}"
      shift 2
      ;;
    --site-url)
      site_url="${2:-}"
      shift 2
      ;;
    --deploy-commit)
      deploy_commit="${2:-}"
      shift 2
      ;;
    --deploy-archive-sha256)
      deploy_archive_sha256="${2:-}"
      shift 2
      ;;
    *)
      usage
      exit 64
      ;;
  esac
done

if [ "$(id -u)" -ne 0 ]; then
  echo "Run setup_split_crawler.sh through sudo." >&2
  exit 77
fi
existing_node_role="$(cat /etc/mooncen-node-role 2>/dev/null || true)"
short_hostname="$(hostname -s 2>/dev/null || true)"
if [ "$short_hostname" != "gen1crawler" ]; then
  echo "Split crawler setup is pinned to hostname gen1crawler, got: ${short_hostname:-unknown}" >&2
  exit 78
fi
if [ -n "$existing_node_role" ] && [ "$existing_node_role" != "crawler" ]; then
  echo "Refusing split crawler setup on node role: $existing_node_role" >&2
  exit 78
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
if [ "$(timedatectl show --property=Timezone --value 2>/dev/null || true)" != "Asia/Seoul" ]; then
  echo "Split crawler setup requires the host timezone Asia/Seoul." >&2
  exit 78
fi
if [ ! -f "$APP_DIR/requirements.lock" ] || \
   [ ! -f "$APP_DIR/DB/staging_schema.sql" ] || \
   [ ! -f "$PRODUCTION_PROVIDERS_FILE" ] || \
   [ -L "$APP_DIR" ]; then
  echo "A regular MoonCen release must exist at $APP_DIR." >&2
  exit 66
fi
for required_file in "$db_client_env" "$db_ca" "$source_crawler_env"; do
  if [ ! -f "$required_file" ] || [ -L "$required_file" ]; then
    echo "Required split-crawler input is unavailable or unsafe: $required_file" >&2
    exit 66
  fi
done
site_url_has_control=0
if printf '%s' "$site_url" | LC_ALL=C grep -q '[[:cntrl:]]'; then
  site_url_has_control=1
fi
if [[ "$site_url" == *$'\n'* ]] || [[ "$site_url" == *$'\r'* ]] || \
   [[ "$site_url" == *"\\"* ]] || [[ "$site_url" =~ [[:space:]] ]] || \
   [ "$site_url_has_control" -ne 0 ] || \
   [[ ! "$site_url" =~ ^https://[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?(/.*)?$ ]]; then
  echo "site-url must be an HTTPS URL." >&2
  exit 64
fi
if [[ ! "$deploy_commit" =~ ^([0-9a-f]{40}|[0-9a-f]{64})$ ]]; then
  echo "deploy-commit is required and must be a lowercase Git object id." >&2
  exit 64
fi
if [[ ! "$deploy_archive_sha256" =~ ^[0-9a-f]{64}$ ]]; then
  echo "deploy-archive-sha256 is required and must be lowercase SHA-256." >&2
  exit 64
fi

read_env_value() {
  local key="$1"
  local path="$2"
  local required="${3:-0}"
  [ -n "$path" ] || return 0
  awk -v expected="$key" -v required="$required" '
    index($0, expected "=") == 1 {
      count += 1
      value = substr($0, length(expected) + 2)
    }
    END {
      if (count > 1 || (required == 1 && count != 1)) {
        print "Invalid environment key count: " expected > "/dev/stderr"
        exit 65
      }
      if (count == 1) {
        printf "%s", value
      }
    }
  ' "$path"
}

DB_HOST="$(read_env_value DB_HOST "$db_client_env" 1)"
DB_PORT="$(read_env_value DB_PORT "$db_client_env" 1)"
DB_NAME="$(read_env_value DB_NAME "$db_client_env" 1)"
DB_APPLIER_USER="$(read_env_value DB_APPLIER_USER "$db_client_env" 1)"
DB_APPLIER_PASSWORD="$(read_env_value DB_APPLIER_PASSWORD "$db_client_env" 1)"
DB_CHECK_USER="$(read_env_value DB_CHECK_USER "$db_client_env" 1)"
DB_CHECK_PASSWORD="$(read_env_value DB_CHECK_PASSWORD "$db_client_env" 1)"
DB_SSLMODE="$(read_env_value DB_SSLMODE "$db_client_env" 1)"

if [ "$DB_HOST" != "cloud" ] || \
   [[ ! "$DB_PORT" =~ ^[0-9]+$ ]] || [ "$DB_PORT" -lt 1 ] || [ "$DB_PORT" -gt 65535 ]; then
  echo "The reviewed production database endpoint must use DNS host cloud." >&2
  exit 78
fi
for identifier in "$DB_NAME" "$DB_APPLIER_USER" "$DB_CHECK_USER"; do
  if [[ ! "$identifier" =~ ^[a-z_][a-z0-9_]*$ ]]; then
    echo "Invalid database identifier: $identifier" >&2
    exit 78
  fi
done
for password in "$DB_APPLIER_PASSWORD" "$DB_CHECK_PASSWORD"; do
  if [[ ! "$password" =~ ^[0-9a-f]{64}$ ]]; then
    echo "Invalid split database password contract." >&2
    exit 78
  fi
done
if [ "$DB_SSLMODE" != "verify-full" ]; then
  echo "The production DB client contract must require DB_SSLMODE=verify-full." >&2
  exit 78
fi

KAKAO_MAPS_REST_API_KEY="$(read_env_value KAKAO_MAPS_REST_API_KEY "$source_crawler_env" 1)"
CRAWLER_MAX_WORKERS="$(read_env_value CRAWLER_MAX_WORKERS "$source_crawler_env")"
if [[ ! "$KAKAO_MAPS_REST_API_KEY" =~ ^[A-Za-z0-9_-]+$ ]]; then
  echo "The source crawler environment must contain a non-empty Kakao REST API key." >&2
  exit 78
fi
CRAWLER_MAX_WORKERS="${CRAWLER_MAX_WORKERS:-4}"
if [[ ! "$CRAWLER_MAX_WORKERS" =~ ^[0-9]+$ ]] || \
   [ "$CRAWLER_MAX_WORKERS" -lt 1 ] || [ "$CRAWLER_MAX_WORKERS" -gt 16 ]; then
  echo "CRAWLER_MAX_WORKERS must be between 1 and 16." >&2
  exit 78
fi
for maps_key_name in KAKAO_MAPS_REST_API_KEY; do
  maps_key="${!maps_key_name}"
  if [[ "$maps_key" == *$'\n'* ]] || [[ "$maps_key" == *$'\r'* ]]; then
    echo "Invalid map API key: $maps_key_name." >&2
    exit 78
  fi
done

deploy_user="${SUDO_USER:-sgm}"
if ! id "$deploy_user" >/dev/null 2>&1; then
  echo "Unable to identify the deploy user." >&2
  exit 77
fi

strictly_disable_if_installed() {
  local unit="$1"
  if systemctl cat "$unit" >/dev/null 2>&1; then
    systemctl disable --now "$unit" >/dev/null
    if systemctl is-active --quiet "$unit" || systemctl is-enabled --quiet "$unit"; then
      echo "Unable to stop and disable split crawler unit before setup: $unit" >&2
      exit 70
    fi
  fi
}

pre_setup_units=(
  mooncen-crawler.service
  mooncen-crawler.timer
  mooncen-staging-apply.timer
)
for pre_setup_unit in "${pre_setup_units[@]}"; do
  strictly_disable_if_installed "$pre_setup_unit"
done
assert_unit_quiescent() {
  local unit="$1"
  local active_state main_pid
  active_state="$(systemctl show "$unit" -p ActiveState --value 2>/dev/null || true)"
  main_pid="$(systemctl show "$unit" -p MainPID --value 2>/dev/null || true)"
  case "$active_state" in
    active|activating|reloading|deactivating)
      echo "Refusing to update while a split crawler one-shot is active: $unit" >&2
      exit 70
      ;;
  esac
  if [[ "$main_pid" =~ ^[0-9]+$ ]] && [ "$main_pid" -ne 0 ]; then
    echo "Refusing to update while a split crawler one-shot still has MainPID: $unit" >&2
    exit 70
  fi
}

for one_shot in \
  mooncen-crawler-once.service \
  mooncen-staging-apply.service \
  mooncen-staging-apply-dry-run.service; do
  assert_unit_quiescent "$one_shot"
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
  printf 'Refusing to update while a pinned staging unit is active: %s\n' \
    "${active_template_units[*]}" >&2
  exit 70
fi

if ! getent group mooncen >/dev/null; then
  groupadd --system mooncen
fi
service_users=("$CRAWLER_USER" "$APPLIER_USER")
for service_user in "${service_users[@]}"; do
  if ! getent group "$service_user" >/dev/null; then
    groupadd --system "$service_user"
  fi
  if ! id "$service_user" >/dev/null 2>&1; then
    useradd \
      --system \
      --gid "$service_user" \
      --groups mooncen \
      --no-create-home \
      --home-dir /nonexistent \
      --shell /usr/sbin/nologin \
      "$service_user"
  else
    usermod \
      --gid "$service_user" \
      --groups mooncen \
      --home /nonexistent \
      --shell /usr/sbin/nologin \
      "$service_user"
  fi
done
if ! getent group "$DB_TLS_GROUP" >/dev/null; then
  groupadd --system "$DB_TLS_GROUP"
fi
usermod --append --groups "$DB_TLS_GROUP" "$CRAWLER_USER"
usermod --append --groups "$DB_TLS_GROUP" "$APPLIER_USER"

install -d -o root -g root -m 0751 "$CONFIG_DIR"
install -o root -g "$DB_TLS_GROUP" -m 0640 "$db_ca" "$CONFIG_DIR/db-root-ca.crt"
openssl verify -CAfile "$CONFIG_DIR/db-root-ca.crt" "$CONFIG_DIR/db-root-ca.crt"

if [ ! -f "$NODE_SECRETS_FILE" ]; then
  secrets_tmp="$(mktemp "$CONFIG_DIR/.crawler-node.env.XXXXXX")"
  printf 'STAGING_DB_PASSWORD=%s\n' "$(openssl rand -hex 32)" >"$secrets_tmp"
  chown root:root "$secrets_tmp"
  chmod 0600 "$secrets_tmp"
  mv -fT -- "$secrets_tmp" "$NODE_SECRETS_FILE"
fi
if [ -L "$NODE_SECRETS_FILE" ] || \
   [ "$(stat -c '%U:%G:%a' "$NODE_SECRETS_FILE")" != "root:root:600" ]; then
  echo "Crawler node secrets must be root:root mode 0600." >&2
  exit 78
fi
STAGING_DB_PASSWORD="$(read_env_value STAGING_DB_PASSWORD "$NODE_SECRETS_FILE" 1)"
if [[ ! "$STAGING_DB_PASSWORD" =~ ^[0-9a-f]{64}$ ]]; then
  echo "Invalid staging database password contract." >&2
  exit 78
fi

chown -R "$deploy_user":mooncen "$APP_DIR"
runuser -u "$deploy_user" -- python3 -I -m venv --clear "$APP_DIR/.venv"
runuser -u "$deploy_user" -- \
  "$APP_DIR/.venv/bin/python" -I -m pip install \
    --require-hashes \
    -r "$APP_DIR/requirements.lock"
chown -R "$deploy_user":mooncen "$APP_DIR/.venv"

if ! production_provider_output="$(
  "$APP_DIR/.venv/bin/python" -I - "$PRODUCTION_PROVIDERS_FILE" <<'PY'
import re
import sys

import yaml

document = yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}
providers = document.get("providers")
if not isinstance(providers, list) or not providers:
    raise SystemExit("production crawler provider registry is empty")
for provider in providers:
    if not isinstance(provider, str) or not re.fullmatch(r"[A-Z0-9_]+", provider):
        raise SystemExit(f"invalid production crawler provider: {provider!r}")
    print(provider)
PY
)"; then
  echo "Unable to validate the production crawler provider registry." >&2
  exit 78
fi
mapfile -t production_providers <<<"$production_provider_output"
if [ "${#production_providers[@]}" -eq 0 ]; then
  echo "Production crawler provider registry is empty." >&2
  exit 78
fi
CRAWLER_PROVIDERS="${production_providers[*]}"

# Freeze the reviewed tree before placing generated metadata inside it. The
# deploy account is used only to build the virtualenv above.
chown -R root:mooncen "$APP_DIR"
chmod -R u=rwX,g=rX,o= "$APP_DIR"
app_env_tmp="$(mktemp "$APP_DIR/.env.XXXXXX")"
cat >"$app_env_tmp" <<EOF
ENVIRONMENT=production
DB_HOST=localhost
DB_PORT=55432
DB_NAME=mooncen_staging
CRAWL_WRITE_MODE=staging
EOF
chown root:mooncen "$app_env_tmp"
chmod 0640 "$app_env_tmp"
mv -fT -- "$app_env_tmp" "$APP_DIR/.env"

install_service_env() {
  local filename="$1"
  local group="$2"
  local tmp
  tmp="$(mktemp "$CONFIG_DIR/.${filename}.XXXXXX")"
  cat >"$tmp"
  chown root:"$group" "$tmp"
  chmod 0640 "$tmp"
  mv -fT -- "$tmp" "$CONFIG_DIR/$filename"
}

install_service_env crawler.env "$CRAWLER_USER" <<EOF
ENVIRONMENT=production
DB_SSLROOTCERT=$CONFIG_DIR/db-root-ca.crt
DB_HOST=localhost
DB_PORT=55432
DB_NAME=mooncen_staging
DB_CRAWLER_USER=mooncen_crawler_login
DB_CRAWLER_PASSWORD=$STAGING_DB_PASSWORD
CRAWL_WRITE_MODE=staging
CRAWL_STAGING_DB_HOST=localhost
CRAWL_STAGING_DB_PORT=55432
CRAWL_STAGING_DB_NAME=mooncen_staging
CRAWL_STAGING_DB_USER=mooncen_crawler_login
CRAWL_STAGING_DB_PASSWORD=$STAGING_DB_PASSWORD
DB_POOL_MIN=1
DB_POOL_MAX=8
KAKAO_MAPS_REST_API_KEY=$KAKAO_MAPS_REST_API_KEY
KAKAO_GEOCODE_MAX_REQUESTS_PER_RUN=1000
SITE_URL=$site_url
CHROME_BINARY=/usr/local/bin/mooncen-chrome
CHROMEDRIVER=/usr/local/bin/mooncen-chromedriver
SELENIUM_PAGE_LOAD_TIMEOUT_SECONDS=45
SELENIUM_SCRIPT_TIMEOUT_SECONDS=30
CRAWLER_PROVIDERS="$CRAWLER_PROVIDERS"
CRAWLER_MAX_WORKERS=$CRAWLER_MAX_WORKERS
CRAWLER_RUN_INTERVAL=86400
CRAWLER_COORDINATE_BACKFILL_LIMIT=
CRAWLER_COORDINATE_BACKFILL_DELAY=0.5
CRAWLER_LOCATION_MIN_CONFIDENCE=75
CRAWLER_DELAY=1
CRAWLER_TIMEOUT=10
COLLECTED_YAML_SOURCE=collected
COLLECTED_YAML_TARGET_LIMIT=30
COLLECTED_YAML_PER_TARGET_LIMIT=20
COLLECTED_YAML_MAX_DEPTH=1
COLLECTED_YAML_MAX_PAGES=20
COLLECTED_YAML_DETAIL_LIMIT=30
COLLECTED_YAML_INCLUDE_REVIEW=false
YAML_TARGETS_SOURCE=
YAML_TARGETS_MAX_PRIORITY=1
YAML_TARGETS_TARGET_LIMIT=50
YAML_TARGETS_PER_TARGET_LIMIT=20
YAML_TARGETS_MAX_DEPTH=1
YAML_TARGETS_MAX_PAGES=20
YAML_TARGETS_DETAIL_LIMIT=30
USER_AGENT="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
EOF

install_service_env applier.env "$APPLIER_USER" <<EOF
ENVIRONMENT=production
DB_SSLROOTCERT=$CONFIG_DIR/db-root-ca.crt
CRAWL_STAGING_DB_HOST=localhost
CRAWL_STAGING_DB_PORT=55432
CRAWL_STAGING_DB_NAME=mooncen_staging
CRAWL_STAGING_DB_USER=mooncen_crawler_login
CRAWL_STAGING_DB_PASSWORD=$STAGING_DB_PASSWORD
PRIMARY_DB_HOST=$DB_HOST
PRIMARY_DB_PORT=$DB_PORT
PRIMARY_DB_NAME=$DB_NAME
PRIMARY_DB_USER=$DB_APPLIER_USER
PRIMARY_DB_PASSWORD=$DB_APPLIER_PASSWORD
STAGING_CLOSE_MIN_RATIO=0.65
STAGING_CLOSE_MAX_ABSOLUTE_DROP=2000
STAGING_CLOSE_RATIO_BASELINE=20
EOF

install -d -o "$CRAWLER_USER" -g "$CRAWLER_USER" -m 0750 "$APP_DIR/logs"
for unit in \
  mooncen-branch-coordinates.service \
  mooncen-crawler.service \
  mooncen-crawler-once.service \
  mooncen-crawler.timer \
  mooncen-staging-apply.service \
  mooncen-staging-apply@.service \
  mooncen-staging-apply-dry-run.service \
  mooncen-staging-apply-dry-run@.service \
  mooncen-staging-apply.timer; do
  install -o root -g root -m 0644 \
    "$APP_DIR/deploy/ubuntu/systemd/$unit" \
    "/etc/systemd/system/$unit"
done

printf 'crawler\n' >"$CONFIG_DIR/node-role"
chown root:root "$CONFIG_DIR/node-role"
chmod 0644 "$CONFIG_DIR/node-role"
printf 'crawler\n' >/etc/mooncen-node-role
chown root:root /etc/mooncen-node-role
chmod 0644 /etc/mooncen-node-role

deploy_meta_tmp="$(mktemp "$APP_DIR/.deploy-meta.XXXXXX")"
cat >"$deploy_meta_tmp" <<EOF
DEPLOY_COMMIT=$deploy_commit
DEPLOY_ARCHIVE_SHA256=$deploy_archive_sha256
DEPLOYED_AT=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
NODE_ROLE=crawler
DB_HOST=$DB_HOST
EOF
chown root:mooncen "$deploy_meta_tmp"
chmod 0640 "$deploy_meta_tmp"
mv -fT -- "$deploy_meta_tmp" "$APP_DIR/.deploy-meta"

# A staged release becomes immutable to the deploy account only after every
# generated file has been atomically installed. Runtime write access is then
# restored solely for the crawler log directory.
chown -R root:mooncen "$APP_DIR"
chmod -R u=rwX,g=rX,o= "$APP_DIR"
chown -R "$CRAWLER_USER":"$CRAWLER_USER" "$APP_DIR/logs"
chmod 0750 "$APP_DIR/logs"

USE_DEDICATED_STAGING_CLUSTER=1 \
  bash "$APP_DIR/deploy/ha/n100_crawler_staging_setup.sh"

systemctl daemon-reload
for unit in \
  mooncen-crawler.service \
  mooncen-crawler.timer \
  mooncen-staging-apply.timer; do
  strictly_disable_if_installed "$unit"
done
systemctl start mooncen-staging-apply-dry-run.service

pg_isready -q -p 55432 -d mooncen_staging
systemctl is-active --quiet mooncen-crawler.service && {
  echo "Crawler service unexpectedly active before validation." >&2
  exit 70
}
systemctl is-enabled --quiet mooncen-crawler.timer && {
  echo "mooncen-crawler.timer unexpectedly enabled before validation." >&2
  exit 70
}
systemctl is-enabled --quiet mooncen-staging-apply.timer && {
  echo "mooncen-staging-apply.timer unexpectedly enabled before validation." >&2
  exit 70
}

echo "MoonCen split crawler node is staged with all automatic timers disabled."
echo "Validate a sample crawl and dry-run before enabling crawler/apply timers."
