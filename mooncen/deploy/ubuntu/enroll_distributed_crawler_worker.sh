#!/usr/bin/env bash
set -euo pipefail
umask 077

APP_DIR="${APP_DIR:-/opt/mooncen}"
PYTHON="$APP_DIR/.venv/bin/python"
schema_env=""
worker_env=""
reporter_env=""
confirmed_database=""

usage() {
  cat >&2 <<'EOF'
Usage: enroll_distributed_crawler_worker.sh \
  --schema-env PATH \
  --worker-env PATH \
  --reporter-env PATH \
  --confirm-staging-database NAME

NOT READY: the repository contains a dormant atomic worker/reporter pair
transaction, but this mutable-tree shell is not an authenticated bootstrap and
must not consume it. Every non-help invocation exits before filesystem or
database mutation.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --schema-env)
      schema_env="${2:-}"
      shift 2
      ;;
    --worker-env)
      worker_env="${2:-}"
      shift 2
      ;;
    --reporter-env)
      reporter_env="${2:-}"
      shift 2
      ;;
    --confirm-staging-database)
      confirmed_database="${2:-}"
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
  echo "Run enroll_distributed_crawler_worker.sh as root." >&2
  exit 77
fi
echo "NOT READY: crawler worker enrollment requires one atomic worker/reporter database and credential-registry transaction with active-rotation fencing. No database or filesystem state was changed." >&2
exit 69

# Unreachable design record below. A future implementation must replace the
# old per-component workflow with one paired transaction; this script must not
# call the single-login provisioner for worker or reporter enrollment.
if ! command -v flock >/dev/null 2>&1; then
  echo "flock is required to serialize worker enrollment." >&2
  exit 69
fi
installer_lock_dir=/run/mooncen-distributed-crawler-control-install
if [ ! -e "$installer_lock_dir" ] && [ ! -L "$installer_lock_dir" ]; then
  mkdir -m 0700 -- "$installer_lock_dir"
fi
if [ ! -d "$installer_lock_dir" ] || [ -L "$installer_lock_dir" ] || \
   [ "$(stat -c '%U:%G:%a' "$installer_lock_dir")" != "root:root:700" ]; then
  echo "Installer lock directory must be a root:root 0700 non-symlink directory." >&2
  exit 78
fi
exec 9<"$installer_lock_dir"
if ! flock -n 9; then
  echo "Another control-plane install or worker enrollment is running." >&2
  exit 75
fi
export MOONCEN_CRAWLER_INSTALL_LOCK_FD=9
if [ "$APP_DIR" != "/opt/mooncen" ] || \
   [ ! -x "$PYTHON" ] || \
   [ ! -f "$APP_DIR/tools/postgres_scram_verifier.py" ] || \
   [ -L "$APP_DIR/tools/postgres_scram_verifier.py" ] || \
   [ ! -f "$APP_DIR/tools/bootstrap_crawler_credential_registry.py" ] || \
   [ -L "$APP_DIR/tools/bootstrap_crawler_credential_registry.py" ] || \
   [ ! -f "$APP_DIR/tools/preflight_distributed_crawler_worker_host.py" ] || \
   [ -L "$APP_DIR/tools/preflight_distributed_crawler_worker_host.py" ] || \
   [ ! -f "$APP_DIR/config/production_topology.json" ] || \
   [ -L "$APP_DIR/config/production_topology.json" ]; then
  echo "A reviewed MoonCen control release is required at /opt/mooncen." >&2
  exit 66
fi
cd "$APP_DIR"
if [[ ! "$confirmed_database" =~ ^[a-z_][a-z0-9_]*$ ]]; then
  echo "--confirm-staging-database is required and must be a PostgreSQL identifier." >&2
  exit 64
fi
validate_protected_file() {
  local path="$1"
  local mode
  if [ -z "$path" ] || [ ! -f "$path" ] || [ -L "$path" ]; then
    echo "Protected input must be a regular non-symlink: $path" >&2
    exit 66
  fi
  mode="$(stat -c '%a' "$path")"
  if [ "$(stat -c '%U' "$path")" != "root" ] || \
     [[ ! "$mode" =~ ^[0-7]{3,4}$ ]] || (( (8#$mode & 8#077) != 0 )); then
    echo "Protected input must be root-owned mode 0600 or stricter: $path" >&2
    exit 78
  fi
  if ! awk '
    /^[[:space:]]*($|#)/ { next }
    /^[A-Z][A-Z0-9_]*=[^[:space:]].*$/ {
      split($0, pair, "=")
      count[pair[1]] += 1
      if (count[pair[1]] > 1) invalid = 1
      next
    }
    { invalid = 1 }
    END { exit invalid ? 65 : 0 }
  ' "$path"; then
    echo "Protected environment contains an invalid or duplicate entry: $path" >&2
    exit 65
  fi
}

read_env_value() {
  local key="$1"
  local path="$2"
  local required="${3:-1}"
  awk -v expected="$key" -v required="$required" '
    index($0, expected "=") == 1 {
      count += 1
      value = substr($0, length(expected) + 2)
    }
    END {
      if (count > 1 || (required == 1 && count != 1)) exit 65
      if (count == 1) {
        first = substr(value, 1, 1)
        last = substr(value, length(value), 1)
        if (length(value) >= 2 && first == last && (first == "\"" || first == "\047")) {
          value = substr(value, 2, length(value) - 2)
        }
        printf "%s", value
      }
    }
  ' "$path"
}

validate_protected_file "$schema_env"
validate_protected_file "$worker_env"
validate_protected_file "$reporter_env"

schema_host="$(read_env_value OPS_CRAWLER_SCHEMA_DB_HOST "$schema_env")"
schema_port="$(read_env_value OPS_CRAWLER_SCHEMA_DB_PORT "$schema_env")"
schema_database="$(read_env_value OPS_CRAWLER_SCHEMA_DB_NAME "$schema_env")"
schema_user="$(read_env_value OPS_CRAWLER_SCHEMA_DB_USER "$schema_env")"
schema_password="$(read_env_value OPS_CRAWLER_SCHEMA_DB_PASSWORD "$schema_env")"
schema_owner="$(read_env_value OPS_CRAWLER_SCHEMA_OBJECT_OWNER "$schema_env")"
shared_host="$(read_env_value OPS_CRAWLER_SHARED_DB_HOST "$worker_env")"
shared_port="$(read_env_value OPS_CRAWLER_SHARED_DB_PORT "$worker_env")"
shared_database="$(read_env_value OPS_CRAWLER_SHARED_DB_NAME "$worker_env")"
queue_host="$(read_env_value OPS_QUEUE_DB_HOST "$worker_env")"
queue_port="$(read_env_value OPS_QUEUE_DB_PORT "$worker_env")"
queue_database="$(read_env_value OPS_QUEUE_DB_NAME "$worker_env")"
queue_user="$(read_env_value OPS_QUEUE_DB_USER "$worker_env")"
queue_password="$(read_env_value OPS_QUEUE_DB_PASSWORD "$worker_env")"
staging_host="$(read_env_value CRAWL_STAGING_DB_HOST "$worker_env")"
staging_port="$(read_env_value CRAWL_STAGING_DB_PORT "$worker_env")"
staging_database="$(read_env_value CRAWL_STAGING_DB_NAME "$worker_env")"
staging_user="$(read_env_value CRAWL_STAGING_DB_USER "$worker_env")"
staging_password="$(read_env_value CRAWL_STAGING_DB_PASSWORD "$worker_env")"
worker_id="$(read_env_value OPS_CRAWLER_WORKER_ID "$worker_env")"
agent_id="$(read_env_value OPS_AGENT_ID "$worker_env")"
worker_hostname="$(read_env_value OPS_CRAWLER_WORKER_HOSTNAME "$worker_env")"
reporter_host="$(read_env_value OPS_CRAWLER_SHARED_DB_HOST "$reporter_env")"
reporter_port="$(read_env_value OPS_CRAWLER_SHARED_DB_PORT "$reporter_env")"
reporter_database="$(read_env_value OPS_CRAWLER_SHARED_DB_NAME "$reporter_env")"
reporter_user="$(read_env_value OPS_CRAWLER_REPORTER_DB_USER "$reporter_env")"
reporter_password="$(read_env_value OPS_CRAWLER_REPORTER_DB_PASSWORD "$reporter_env")"
reporter_worker_id="$(read_env_value OPS_CRAWLER_WORKER_ID "$reporter_env")"
reporter_agent_id="$(read_env_value OPS_AGENT_ID "$reporter_env")"
reporter_hostname="$(read_env_value OPS_CRAWLER_WORKER_HOSTNAME "$reporter_env")"
reporter_environment="$(read_env_value ENVIRONMENT "$reporter_env")"
worker_environment="$(read_env_value ENVIRONMENT "$worker_env")"

if [[ ! "$schema_host" =~ ^[A-Za-z0-9._:-]+$ ]] || \
   [[ ! "$schema_port" =~ ^[0-9]+$ ]] || \
   [ "$schema_port" -lt 1 ] || [ "$schema_port" -gt 65535 ] || \
   [[ ! "$schema_user" =~ ^[a-z_][a-z0-9_]*$ ]] || \
   [[ ! "$schema_owner" =~ ^[a-z_][a-z0-9_]*$ ]] || \
   [ "$schema_owner" = "$schema_user" ]; then
  echo "Schema administrator endpoint or role is invalid." >&2
  exit 78
fi
if [ "$schema_host:$schema_port/$schema_database" != \
     "$shared_host:$shared_port/$shared_database" ] || \
   [ "$schema_database" != "$confirmed_database" ]; then
  echo "Schema administrator, worker, and confirmation must select the same staging database." >&2
  exit 78
fi
if [ "$queue_host:$queue_port/$queue_database" != \
     "$shared_host:$shared_port/$shared_database" ] || \
   [ "$staging_host:$staging_port/$staging_database" != \
     "$shared_host:$shared_port/$shared_database" ]; then
  echo "Worker queue, staging, and shared control endpoints must match exactly." >&2
  exit 78
fi
if [[ ! "$queue_user" =~ ^[a-z_][a-z0-9_]*$ ]] || \
   [ "$queue_user" != "$staging_user" ] || \
   [ "$queue_user" = "$schema_user" ] || \
   [ "$queue_user" = "$schema_owner" ] || \
   [ "$queue_password" = "$schema_password" ] || \
   [ "$queue_password" != "$staging_password" ] || \
   [ "${#queue_password}" -lt 32 ] || [ "${#queue_password}" -gt 512 ]; then
  echo "OPS_QUEUE and CRAWL_STAGING must use one distinct, identical, bounded worker credential." >&2
  exit 78
fi
if [ "$(read_env_value CRAWL_WRITE_MODE "$worker_env")" != "staging" ]; then
  echo "Worker enrollment requires CRAWL_WRITE_MODE=staging." >&2
  exit 78
fi
if [[ ! "$reporter_user" =~ ^[a-z_][a-z0-9_]*$ ]] || \
   [ "$reporter_host:$reporter_port/$reporter_database" != \
     "$shared_host:$shared_port/$shared_database" ] || \
   [ "$reporter_worker_id" != "$worker_id" ] || \
   [ "$reporter_agent_id" != "$agent_id" ] || \
   [ "$reporter_hostname" != "$worker_hostname" ] || \
   [ "$reporter_environment" != "$worker_environment" ] || \
   [ "$reporter_user" = "$queue_user" ] || \
   [ "$reporter_user" = "$schema_user" ] || \
   [ "$reporter_user" = "$schema_owner" ] || \
   [ "$reporter_password" = "$queue_password" ] || \
   [ "$reporter_password" = "$schema_password" ]; then
  echo "Reporter must use a distinct credential bound to the exact same worker agent." >&2
  exit 78
fi

# Validate the reviewed, disabled fleet assignment before any login or schema
# operation can mutate the control database. This deliberately does not
# require enabled=true: enrollment prepares a pending worker but never starts it.
"$PYTHON" -X utf8 -m tools.preflight_distributed_crawler_worker_host \
  --inventory-only \
  --worker-key "$worker_id" \
  --kernel-hostname "$worker_hostname" >/dev/null

"$PYTHON" -X utf8 -m tools.postgres_scram_verifier \
  --env-file "$schema_env" \
  --password-key OPS_CRAWLER_SCHEMA_DB_PASSWORD \
  --validate-only >/dev/null
"$PYTHON" -X utf8 -m tools.postgres_scram_verifier \
  --env-file "$worker_env" \
  --password-key OPS_QUEUE_DB_PASSWORD \
  --matching-password-key CRAWL_STAGING_DB_PASSWORD \
  --validate-only >/dev/null
"$PYTHON" -X utf8 -m tools.postgres_scram_verifier \
  --env-file "$reporter_env" \
  --password-key OPS_CRAWLER_REPORTER_DB_PASSWORD \
  --validate-only >/dev/null

# Re-verify schema checksum, fenced objects, and application-owner authority
# before the reviewed Python enrollment contract can change a LOGIN.
"$PYTHON" -X utf8 -m tools.ensure_crawler_control_schema \
  --env-file "$schema_env" \
  --confirm-staging-database "$confirmed_database" \
  --dry-run --require-applied

"$PYTHON" -X utf8 -m tools.bootstrap_crawler_credential_registry \
  --schema-env "$schema_env" \
  --confirm-staging-database "$confirmed_database" \
  --audit-only

echo "NOT READY: atomic worker/reporter pair provisioner is unavailable to this untrusted shell bootstrap." >&2
exit 69

"$PYTHON" -X utf8 -m tools.preflight_distributed_crawler_control \
  --component worker --env-file "$worker_env" --installation-validation
"$PYTHON" -X utf8 -m tools.preflight_distributed_crawler_control \
  --component reporter --env-file "$reporter_env" --installation-validation

cat <<REPORT
Distributed crawler worker login enrolled and preflighted.
worker_id=${worker_id}
agent_id=${agent_id}
worker_hostname=${worker_hostname}
database=${shared_host}:${shared_port}/${shared_database}
login=${queue_user}
reporter_login=${reporter_user}

No remote worker service was enabled. Transfer the protected worker policy by
the approved secret channel, then run its systemd preflight before canary.
REPORT
