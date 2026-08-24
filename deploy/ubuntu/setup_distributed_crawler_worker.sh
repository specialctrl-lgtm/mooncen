#!/usr/bin/env bash
set -euo pipefail
umask 077

APP_DIR="${APP_DIR:-/opt/mooncen-worker/current}"
CONFIG_DIR="${CONFIG_DIR:-/etc/mooncen}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
TMPFILES_DIR="${TMPFILES_DIR:-/etc/tmpfiles.d}"
PYTHON="$APP_DIR/.venv/bin/python"

worker_env=""
reporter_env=""
release_env=""
db_ca=""
release_ca=""
allowed_signers=""
confirmed_database=""
replace_protected_files=0
enable_reviewed_canary=0
bootstrap_artifact=""
bootstrap_signature=""
bootstrap_key_id=""
bootstrap_code_version=""
bootstrap_config_revision=""
bootstrap_sha256=""
bootstrap_size_bytes=""

usage() {
  cat >&2 <<'EOF'
Usage: setup_distributed_crawler_worker.sh \
  --worker-env PATH \
  --reporter-env PATH \
  --release-agent-env PATH \
  --db-ca PATH \
  --release-ca PATH \
  --allowed-signers PATH \
  --confirm-staging-database NAME \
  [--replace-protected-files] \
  [--bootstrap-artifact PATH \
   --bootstrap-signature PATH \
   --bootstrap-key-id ID \
   --bootstrap-code-version VERSION \
   --bootstrap-config-revision REVISION \
   --bootstrap-sha256 HEX \
   --bootstrap-size-bytes BYTES] \
  [--enable-reviewed-canary]

NOT READY: this release has no externally signed, digest-attested root-owned
worker bootstrap. Every non-help invocation exits before filesystem, systemd,
or database mutation. The remaining body is a dormant future contract only.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --worker-env)
      worker_env="${2:-}"
      shift 2
      ;;
    --reporter-env)
      reporter_env="${2:-}"
      shift 2
      ;;
    --release-agent-env)
      release_env="${2:-}"
      shift 2
      ;;
    --db-ca)
      db_ca="${2:-}"
      shift 2
      ;;
    --release-ca)
      release_ca="${2:-}"
      shift 2
      ;;
    --allowed-signers)
      allowed_signers="${2:-}"
      shift 2
      ;;
    --confirm-staging-database)
      confirmed_database="${2:-}"
      shift 2
      ;;
    --replace-protected-files)
      replace_protected_files=1
      shift
      ;;
    --enable-reviewed-canary)
      enable_reviewed_canary=1
      shift
      ;;
    --bootstrap-artifact)
      bootstrap_artifact="${2:-}"
      shift 2
      ;;
    --bootstrap-signature)
      bootstrap_signature="${2:-}"
      shift 2
      ;;
    --bootstrap-key-id)
      bootstrap_key_id="${2:-}"
      shift 2
      ;;
    --bootstrap-code-version)
      bootstrap_code_version="${2:-}"
      shift 2
      ;;
    --bootstrap-config-revision)
      bootstrap_config_revision="${2:-}"
      shift 2
      ;;
    --bootstrap-sha256)
      bootstrap_sha256="${2:-}"
      shift 2
      ;;
    --bootstrap-size-bytes)
      bootstrap_size_bytes="${2:-}"
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
  echo "Run setup_distributed_crawler_worker.sh as root." >&2
  exit 77
fi
if [ "$APP_DIR" != "/opt/mooncen-worker/current" ] || \
   [ "$CONFIG_DIR" != "/etc/mooncen" ] || \
   [ "$SYSTEMD_DIR" != "/etc/systemd/system" ] || \
   [ "$TMPFILES_DIR" != "/etc/tmpfiles.d" ]; then
  echo "The reviewed worker units require canonical installation paths." >&2
  exit 78
fi
echo "NOT READY: distributed crawler worker installation requires an active externally signed root-owned /opt/mooncen-worker/current bootstrap; /opt/mooncen is not an installer trust root. No files or units were changed." >&2
exit 69

# Unreachable design record below. Keep the future convergence contract
# reviewable, but do not remove the gate until an external trust-root bootstrap
# verifies the release before this mutable application tree is executed.
release_lock=/run/lock/mooncen-worker-bootstrap-release.lock
exec 8>"$release_lock"
if ! flock -n 8; then
  echo "Worker bootstrap activation or another worker install holds the release lock." >&2
  exit 75
fi
active_target="$(readlink -- "$APP_DIR" 2>/dev/null || true)"
if [[ ! "$active_target" =~ ^releases/[0-9a-f]{32}$ ]] || \
   [ "$(stat -c '%U:%G' -- "$APP_DIR" 2>/dev/null || true)" != "root:root" ]; then
  echo "The reviewed worker current pointer is not a root-owned versioned release link." >&2
  exit 78
fi
resolved_app_dir="$(readlink -f -- "$APP_DIR")"
if [ "$resolved_app_dir" != "/opt/mooncen-worker/$active_target" ] || \
   [ ! -d "$resolved_app_dir" ] || [ -L "$resolved_app_dir" ] || \
   [ "$(stat -c '%U:%G:%a' "$resolved_app_dir")" != "root:mooncen:750" ]; then
  echo "The active worker bootstrap release root is unsafe." >&2
  exit 78
fi
APP_DIR="$resolved_app_dir"
PYTHON="$APP_DIR/.venv/bin/python"
active_release_identity="$(stat -c '%d:%i' "$APP_DIR")"
assert_active_release_pinned() {
  if [ "$(readlink -f -- /opt/mooncen-worker/current 2>/dev/null || true)" != "$APP_DIR" ] || \
     [ "$(stat -c '%d:%i' "$APP_DIR" 2>/dev/null || true)" != "$active_release_identity" ]; then
    echo "Active worker bootstrap release changed during the locked installation." >&2
    exit 75
  fi
}
if [[ ! "$confirmed_database" =~ ^[a-z_][a-z0-9_]*$ ]]; then
  echo "--confirm-staging-database is required and must be a PostgreSQL identifier." >&2
  exit 64
fi
for required_argument in \
  "$worker_env" "$reporter_env" "$release_env" "$db_ca" "$release_ca" \
  "$allowed_signers"; do
  if [ -z "$required_argument" ]; then
    usage
    exit 64
  fi
done

bootstrap_values=(
  "$bootstrap_artifact"
  "$bootstrap_signature"
  "$bootstrap_key_id"
  "$bootstrap_code_version"
  "$bootstrap_config_revision"
  "$bootstrap_sha256"
  "$bootstrap_size_bytes"
)
bootstrap_value_count=0
for bootstrap_value in "${bootstrap_values[@]}"; do
  if [ -n "$bootstrap_value" ]; then
    bootstrap_value_count=$((bootstrap_value_count + 1))
  fi
done
if [ "$bootstrap_value_count" -ne 0 ] && \
   [ "$bootstrap_value_count" -ne "${#bootstrap_values[@]}" ]; then
  echo "Every --bootstrap-* argument must be supplied together." >&2
  exit 64
fi
if [ "$bootstrap_value_count" -ne 0 ] && \
   { [[ ! "$bootstrap_size_bytes" =~ ^[1-9][0-9]*$ ]] || \
     [ "$bootstrap_size_bytes" -gt 536870912 ]; }; then
  echo "--bootstrap-size-bytes must be a canonical value up to 512 MiB." >&2
  exit 64
fi

for command_name in \
  awk basename bash cmp cut dirname flock getent groupadd id install mkdir \
  mktemp mv openssl passwd readlink rm runuser sort ssh-keygen stat systemctl \
  systemd-analyze systemd-tmpfiles sleep tr useradd usermod; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Required worker installer command is unavailable: $command_name" >&2
    exit 69
  fi
done

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

required_release_files=(
  "$PYTHON"
  "$APP_DIR/tools/postgres_scram_verifier.py"
  "$APP_DIR/tools/preflight_distributed_crawler_control.py"
  "$APP_DIR/tools/preflight_distributed_crawler_worker_host.py"
  "$APP_DIR/tools/bootstrap_distributed_crawler_release.py"
  "$APP_DIR/tools/run_distributed_crawler_preflight.py"
  "$APP_DIR/tools/run_crawler_release_reporter.py"
  "$APP_DIR/config/production_topology.json"
  "$APP_DIR/ops_agent/production_topology.py"
  "$APP_DIR/ops_agent/crawler_release_agent.py"
  "$APP_DIR/ops_agent/crawler_release_reporter.py"
  "$APP_DIR/ops_agent/crawler_worker.py"
  "$APP_DIR/deploy/ubuntu/setup_distributed_crawler_worker.sh"
  "$APP_DIR/deploy/ubuntu/templates/crawler-release-agent.tmpfiles.conf"
)
assert_reviewed_release_path() {
  local path="$1"
  local relative component current mode owner
  if [[ "$path" != "$APP_DIR"/* ]]; then
    echo "Reviewed release path escapes APP_DIR: $path" >&2
    exit 78
  fi
  relative="${path#"$APP_DIR"/}"
  current="$APP_DIR"
  IFS='/' read -r -a path_components <<<"$relative"
  for component in "${path_components[@]}"; do
    if [ -z "$component" ] || [ "$component" = "." ] || [ "$component" = ".." ]; then
      echo "Reviewed release path is not canonical: $path" >&2
      exit 78
    fi
    current="$current/$component"
    if [ -L "$current" ]; then
      echo "Reviewed release path contains a symlink: $current" >&2
      exit 78
    fi
    mode="$(stat -c '%a' "$current")"
    owner="$(stat -c '%U' "$current")"
    if { [ "$owner" != "root" ] && [ "$owner" != "mooncen" ]; } || \
       [[ ! "$mode" =~ ^[0-7]{3,4}$ ]] || (( (8#$mode & 8#022) != 0 )); then
      echo "Reviewed release path is writable by an untrusted identity: $current" >&2
      exit 78
    fi
  done
}

if [ ! -d "$APP_DIR" ] || [ -L "$APP_DIR" ]; then
  echo "A regular reviewed MoonCen release is required at $APP_DIR." >&2
  exit 66
fi
app_mode="$(stat -c '%a' "$APP_DIR")"
app_owner="$(stat -c '%U' "$APP_DIR")"
if { [ "$app_owner" != "root" ] && [ "$app_owner" != "mooncen" ]; } || \
   [[ ! "$app_mode" =~ ^[0-7]{3,4}$ ]] || (( (8#$app_mode & 8#022) != 0 )); then
  echo "Reviewed MoonCen release root has unsafe ownership or write permissions." >&2
  exit 78
fi
for required_file in "${required_release_files[@]}"; do
  if [ ! -f "$required_file" ] || [ -L "$required_file" ]; then
    echo "Required reviewed worker release file is unavailable or unsafe: $required_file" >&2
    exit 66
  fi
  assert_reviewed_release_path "$required_file"
done
if [ ! -x "$PYTHON" ]; then
  echo "Reviewed MoonCen Python environment is not executable." >&2
  exit 66
fi
cd "$APP_DIR"
bash -n "$APP_DIR/deploy/ubuntu/setup_distributed_crawler_worker.sh"

validate_root_owned_parent_chain() {
  local path="$1"
  local canonical parent mode
  if [[ "$path" != /* ]]; then
    echo "Protected installer input must use an absolute path: $path" >&2
    exit 78
  fi
  canonical="$(readlink -m -- "$path")"
  if [ "$canonical" != "$path" ]; then
    echo "Protected installer input path must be canonical: $path" >&2
    exit 78
  fi
  parent="$(dirname "$path")"
  while [ "$parent" != "/" ]; do
    if [ ! -d "$parent" ] || [ -L "$parent" ]; then
      echo "Protected installer input has an unsafe parent: $parent" >&2
      exit 78
    fi
    mode="$(stat -c '%a' "$parent")"
    if [ "$(stat -c '%U' "$parent")" != "root" ] || \
       [[ ! "$mode" =~ ^[0-7]{3,4}$ ]] || (( (8#$mode & 8#022) != 0 )); then
      echo "Protected installer input parent must be root-owned and private: $parent" >&2
      exit 78
    fi
    parent="$(dirname "$parent")"
  done
}

validate_protected_environment() {
  local path="$1"
  local mode
  validate_root_owned_parent_chain "$path"
  if [ ! -f "$path" ] || [ -L "$path" ]; then
    echo "Protected environment must be a regular non-symlink: $path" >&2
    exit 66
  fi
  mode="$(stat -c '%a' "$path")"
  if [ "$(stat -c '%U' "$path")" != "root" ] || \
     [[ ! "$mode" =~ ^[0-7]{3,4}$ ]] || (( (8#$mode & 8#077) != 0 )); then
    echo "Protected environment must be root-owned mode 0600 or stricter: $path" >&2
    exit 78
  fi
  if ! awk '
    /^[[:space:]]*($|#)/ { next }
    {
      equals = index($0, "=")
      key = substr($0, 1, equals - 1)
      value = substr($0, equals + 1)
      if (
        equals < 2 || key !~ /^[A-Z][A-Z0-9_]*$/ || value == "" ||
        value ~ /[[:space:]\\]/ || index(value, "\"") ||
        index(value, sprintf("%c", 39)) || seen[key]++
      ) invalid = 1
    }
    END { exit invalid ? 65 : 0 }
  ' "$path"; then
    echo "Protected environment has an invalid or duplicate entry: $path" >&2
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
      if (count == 1) printf "%s", value
    }
  ' "$path"
}

reject_environment_key() {
  local path="$1"
  local key="$2"
  if [ -n "$(read_env_value "$key" "$path" 0)" ]; then
    echo "Forbidden privileged or conflicting environment key in $path: $key" >&2
    exit 78
  fi
}

validate_trust_input() {
  local path="$1"
  local label="$2"
  local mode
  validate_root_owned_parent_chain "$path"
  if [ ! -s "$path" ] || [ -L "$path" ] || [ ! -f "$path" ]; then
    echo "$label must be a nonempty regular non-symlink: $path" >&2
    exit 66
  fi
  mode="$(stat -c '%a' "$path")"
  if [ "$(stat -c '%U' "$path")" != "root" ] || \
     [[ ! "$mode" =~ ^[0-7]{3,4}$ ]] || (( (8#$mode & 8#022) != 0 )); then
    echo "$label must be root-owned and not group/world writable: $path" >&2
    exit 78
  fi
}

for protected_env in "$worker_env" "$reporter_env" "$release_env"; do
  validate_protected_environment "$protected_env"
done
validate_trust_input "$db_ca" "Staging DB CA"
validate_trust_input "$release_ca" "Release HTTPS CA"
validate_trust_input "$allowed_signers" "Crawler allowed-signers file"
if [ "$bootstrap_value_count" -ne 0 ]; then
  validate_trust_input "$bootstrap_artifact" "Bootstrap crawler artifact"
  validate_trust_input "$bootstrap_signature" "Bootstrap artifact signature"
fi
if ! openssl x509 -in "$db_ca" -noout >/dev/null 2>&1 || \
   ! openssl x509 -in "$release_ca" -noout >/dev/null 2>&1; then
  echo "Each supplied CA must contain a parseable X.509 certificate." >&2
  exit 78
fi
if ! awk '
  /^[[:space:]]*($|#)/ { next }
  /BEGIN [A-Z ]*PRIVATE KEY/ { invalid = 1; next }
  {
    found = 0
    for (field = 1; field < NF; field += 1) {
      if ($field ~ /^(ssh-|ecdsa-|sk-)[A-Za-z0-9@._+-]+$/ &&
          $(field + 1) ~ /^[A-Za-z0-9+\/=]+$/) found = 1
    }
    if (!found) invalid = 1
    valid += found
  }
  END { exit invalid || valid < 1 ? 65 : 0 }
' "$allowed_signers"; then
  echo "Allowed-signers input is empty, private, or malformed." >&2
  exit 78
fi

worker_environment="$(read_env_value ENVIRONMENT "$worker_env")"
worker_id="$(read_env_value OPS_CRAWLER_WORKER_ID "$worker_env")"
agent_id="$(read_env_value OPS_AGENT_ID "$worker_env")"
worker_hostname="$(read_env_value OPS_CRAWLER_WORKER_HOSTNAME "$worker_env")"
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
crawler_user="$(read_env_value DB_CRAWLER_USER "$worker_env")"
crawler_password="$(read_env_value DB_CRAWLER_PASSWORD "$worker_env")"
worker_sslmode="$(read_env_value DB_SSLMODE "$worker_env")"
worker_sslrootcert="$(read_env_value DB_SSLROOTCERT "$worker_env")"

reporter_environment="$(read_env_value ENVIRONMENT "$reporter_env")"
reporter_worker_id="$(read_env_value OPS_CRAWLER_WORKER_ID "$reporter_env")"
reporter_agent_id="$(read_env_value OPS_AGENT_ID "$reporter_env")"
reporter_hostname="$(read_env_value OPS_CRAWLER_WORKER_HOSTNAME "$reporter_env")"
reporter_host="$(read_env_value OPS_CRAWLER_SHARED_DB_HOST "$reporter_env")"
reporter_port="$(read_env_value OPS_CRAWLER_SHARED_DB_PORT "$reporter_env")"
reporter_database="$(read_env_value OPS_CRAWLER_SHARED_DB_NAME "$reporter_env")"
reporter_user="$(read_env_value OPS_CRAWLER_REPORTER_DB_USER "$reporter_env")"
reporter_password="$(read_env_value OPS_CRAWLER_REPORTER_DB_PASSWORD "$reporter_env")"
reporter_sslmode="$(read_env_value DB_SSLMODE "$reporter_env")"
reporter_sslrootcert="$(read_env_value DB_SSLROOTCERT "$reporter_env")"
reporter_state_dir="$(read_env_value OPS_CRAWLER_RELEASE_STATE_DIR "$reporter_env")"

release_mode="$(read_env_value OPS_CRAWLER_RELEASE_MODE "$release_env")"
release_worker_id="$(read_env_value OPS_CRAWLER_WORKER_ID "$release_env")"
release_environment="$(read_env_value OPS_CRAWLER_ENVIRONMENT "$release_env")"
release_tls_ca="$(read_env_value OPS_CRAWLER_TLS_CA_FILE "$release_env")"
release_signers="$(read_env_value OPS_CRAWLER_ALLOWED_SIGNERS "$release_env")"
release_signature_required="$(read_env_value OPS_CRAWLER_REQUIRE_SIGNATURE "$release_env")"
release_root="$(read_env_value OPS_CRAWLER_RELEASE_ROOT "$release_env")"
release_state_dir="$(read_env_value OPS_CRAWLER_RELEASE_STATE_DIR "$release_env")"
release_drain_state="$(read_env_value OPS_CRAWLER_DRAIN_STATE "$release_env")"
release_health_state="$(read_env_value OPS_CRAWLER_HEALTH_STATE "$release_env")"
worker_drain_state="$(read_env_value OPS_CRAWLER_DRAIN_STATE "$worker_env")"
worker_health_state="$(read_env_value OPS_CRAWLER_HEALTH_STATE "$worker_env")"

if [[ ! "$worker_id" =~ ^[a-z][a-z0-9_-]{0,63}$ ]] || \
   [[ ! "$agent_id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] || \
   [ "$agent_id" = "00000000-0000-0000-0000-000000000000" ] || \
   [[ ! "$worker_hostname" =~ ^[a-z0-9]([a-z0-9-]{0,62})(\.[a-z0-9]([a-z0-9-]{0,62}))*$ ]]; then
  echo "Worker key, agent UUID, or canonical hostname is invalid." >&2
  exit 78
fi
runtime_hostname="$($PYTHON -I -X utf8 -c 'import socket; print(socket.gethostname().strip().lower().rstrip("."))')"
if [ "$worker_hostname" != "$runtime_hostname" ]; then
  echo "OPS_CRAWLER_WORKER_HOSTNAME must exactly match the lowercase kernel hostname." >&2
  exit 78
fi
if [ "$worker_environment" != "production" ] && [ "$worker_environment" != "staging" ]; then
  echo "Distributed crawler ENVIRONMENT must be production or staging." >&2
  exit 78
fi
if [ "$reporter_worker_id" != "$worker_id" ] || \
   [ "$reporter_agent_id" != "$agent_id" ] || \
   [ "$reporter_hostname" != "$worker_hostname" ] || \
   [ "$reporter_environment" != "$worker_environment" ] || \
   [ "$release_worker_id" != "$worker_id" ] || \
   [ "$release_environment" != "$worker_environment" ]; then
  echo "Worker, reporter, and release policy must identify the exact same worker." >&2
  exit 78
fi

worker_assignment_args=(
  --worker-key "$worker_id"
  --kernel-hostname "$worker_hostname"
)
inventory_preflight_args=(
  --inventory-only
  "${worker_assignment_args[@]}"
)
if [ "$enable_reviewed_canary" -eq 1 ]; then
  inventory_preflight_args+=(--require-enabled)
fi
"$PYTHON" -X utf8 -m tools.preflight_distributed_crawler_worker_host \
  "${inventory_preflight_args[@]}" >/dev/null
expected_worker_dropin="$(
  "$PYTHON" -X utf8 -m tools.preflight_distributed_crawler_worker_host \
    --inventory-only \
    "${worker_assignment_args[@]}" \
    --render-systemd-drop-in
)"
if [ -z "$expected_worker_dropin" ]; then
  echo "Reviewed worker resource profile is empty." >&2
  exit 78
fi

canonical_port() {
  local value="$1"
  [[ "$value" =~ ^[1-9][0-9]{0,4}$ ]] && \
    [ "$value" -le 65535 ] && [ "$value" = "$((10#$value))" ]
}
if [[ ! "$shared_host" =~ ^[A-Za-z0-9._:-]+$ ]] || \
   ! canonical_port "$shared_port" || \
   [ "$shared_database" != "$confirmed_database" ] || \
   [ "$queue_host:$queue_port/$queue_database" != \
     "$shared_host:$shared_port/$shared_database" ] || \
   [ "$staging_host:$staging_port/$staging_database" != \
     "$shared_host:$shared_port/$shared_database" ] || \
   [ "$reporter_host:$reporter_port/$reporter_database" != \
     "$shared_host:$shared_port/$shared_database" ]; then
  echo "Worker queue, fenced staging, reporter, and shared control endpoints must match exactly." >&2
  exit 78
fi
if [ "$(read_env_value CRAWL_WRITE_MODE "$worker_env")" != "staging" ]; then
  echo "Distributed worker requires CRAWL_WRITE_MODE=staging." >&2
  exit 78
fi
if [[ ! "$queue_user" =~ ^[a-z_][a-z0-9_]*$ ]] || \
   [[ ! "$reporter_user" =~ ^[a-z_][a-z0-9_]*$ ]] || \
   [ "$queue_user" != "$staging_user" ] || \
   [ "$queue_user" != "$crawler_user" ] || \
   [ "$queue_password" != "$staging_password" ] || \
   [ "$queue_password" != "$crawler_password" ] || \
   [ "$queue_user" = "$reporter_user" ] || \
   [ "$queue_password" = "$reporter_password" ]; then
  echo "Worker writes need one dedicated credential; reporter credential must be distinct." >&2
  exit 78
fi
if [ "$worker_sslmode" != "verify-full" ] || \
   [ "$reporter_sslmode" != "verify-full" ] || \
   [ "$worker_sslrootcert" != "/etc/mooncen/db-root-ca.crt" ] || \
   [ "$reporter_sslrootcert" != "/etc/mooncen/db-root-ca.crt" ]; then
  echo "Worker and reporter DB TLS must use verify-full and the installed staging CA." >&2
  exit 78
fi
if [ "$release_tls_ca" != "/etc/mooncen/crawler-release-tls-ca.crt" ] || \
   [ "$release_signers" != "/etc/mooncen/crawler-release-allowed-signers" ] || \
   [ "$release_signature_required" != "true" ] || \
   [ "$release_root" != "/opt/mooncen-crawler" ] || \
   [ "$release_state_dir" != "/var/lib/mooncen-crawler-release-agent" ] || \
   [ "$reporter_state_dir" != "$release_state_dir" ] || \
   [ "$release_drain_state" != "/run/mooncen-crawler/drain.json" ] || \
   [ "$release_health_state" != "/run/mooncen-crawler/health.json" ] || \
   [ "$worker_drain_state" != "$release_drain_state" ] || \
   [ "$worker_health_state" != "$release_health_state" ]; then
  echo "Release trust, state, drain, or health paths differ from the reviewed units." >&2
  exit 78
fi
case "$release_mode" in
  check|dry-run|apply) ;;
  *)
    echo "OPS_CRAWLER_RELEASE_MODE must be check, dry-run, or apply." >&2
    exit 78
    ;;
esac
if [ "$enable_reviewed_canary" -eq 1 ] && [ "$release_mode" != "apply" ]; then
  echo "Reviewed canary enable requires OPS_CRAWLER_RELEASE_MODE=apply." >&2
  exit 78
fi

for forbidden_key in \
  DB_OWNER_USER DB_OWNER_PASSWORD DB_MIGRATOR_USER DB_MIGRATOR_PASSWORD \
  DB_API_USER DB_API_PASSWORD OPS_CRAWLER_CONTROL_DB_USER \
  OPS_CRAWLER_CONTROL_DB_PASSWORD OPS_CRAWLER_FINALIZER_DB_USER \
  OPS_CRAWLER_FINALIZER_DB_PASSWORD OPS_CRAWLER_REPORTER_DB_USER \
  OPS_CRAWLER_REPORTER_DB_PASSWORD OPS_CRAWLER_MAX_CONCURRENCY; do
  reject_environment_key "$worker_env" "$forbidden_key"
done
for forbidden_key in \
  OPS_QUEUE_DB_USER OPS_QUEUE_DB_PASSWORD CRAWL_STAGING_DB_USER \
  CRAWL_STAGING_DB_PASSWORD DB_CRAWLER_USER DB_CRAWLER_PASSWORD \
  OPS_CRAWLER_CONTROL_DB_USER OPS_CRAWLER_CONTROL_DB_PASSWORD; do
  reject_environment_key "$reporter_env" "$forbidden_key"
done
for forbidden_key in \
  OPS_CRAWLER_CODE_VERSION OPS_CRAWLER_ARTIFACT_DIGEST \
  OPS_CRAWLER_CONFIG_REVISION; do
  reject_environment_key "$release_env" "$forbidden_key"
done

"$PYTHON" -X utf8 -m tools.postgres_scram_verifier \
  --env-file "$worker_env" \
  --password-key OPS_QUEUE_DB_PASSWORD \
  --matching-password-key CRAWL_STAGING_DB_PASSWORD \
  --validate-only >/dev/null
"$PYTHON" -X utf8 -m tools.postgres_scram_verifier \
  --env-file "$reporter_env" \
  --password-key OPS_CRAWLER_REPORTER_DB_PASSWORD \
  --validate-only >/dev/null

unit_state_is_live() {
  case "$1" in
    ""|inactive|failed|not-found) return 1 ;;
    *) return 0 ;;
  esac
}

unit_state_is_enabled() {
  case "$1" in
    enabled|enabled-runtime|linked|linked-runtime|alias) return 0 ;;
    *) return 1 ;;
  esac
}

legacy_units=(
  mooncen-crawler.timer
  mooncen-crawler.service
  mooncen-crawler-once.service
  mooncen-crawler-watchdog.timer
  mooncen-crawler-watchdog.service
)
new_units=(
  mooncen-crawler-worker.target
  mooncen-crawler-pull-worker.service
  mooncen-crawler-release-agent.service
  mooncen-crawler-release-agent.timer
  mooncen-crawler-release-reporter.service
  mooncen-crawler-release-reporter.timer
)

# Future trust-root bootstrap contract: systemd applies exact, dash-prefix and
# type-wide drop-ins from several precedence roots. Refuse every pre-existing
# override except the one atomically managed resource profile. The allowed
# file is replaced from the reviewed inventory before daemon-reload.
reviewed_worker_dropin="$SYSTEMD_DIR/mooncen-crawler-pull-worker.service.d/10-reviewed-worker-resources.conf"
systemd_search_roots=(
  /etc/systemd/system
  /run/systemd/system
  /usr/local/lib/systemd/system
  /usr/lib/systemd/system
  /lib/systemd/system
)
worker_unit_dropin_directories() {
  local root="$1"
  local unit="$2"
  local stem="${unit%.*}"
  local unit_type="${unit##*.}"
  local prefix="$stem"
  printf '%s\n' "$root/$unit.d" "$root/$unit_type.d"
  while [[ "$prefix" == *-* ]]; do
    prefix="${prefix%-*}"
    printf '%s\n' "$root/$prefix-.$unit_type.d"
  done
}

reject_unreviewed_worker_dropins() {
  local unit root dropin_dir entry mode
  local -A inspected=()
  for unit in "${new_units[@]}"; do
    for root in "${systemd_search_roots[@]}"; do
      while IFS= read -r dropin_dir; do
        if [ -n "${inspected[$dropin_dir]:-}" ]; then
          continue
        fi
        inspected[$dropin_dir]=1
        if [ ! -e "$dropin_dir" ] && [ ! -L "$dropin_dir" ]; then
          continue
        fi
        if [ ! -d "$dropin_dir" ] || [ -L "$dropin_dir" ] || \
           [ "$(stat -c '%U:%G' "$dropin_dir")" != "root:root" ]; then
          echo "Systemd worker drop-in directory is unsafe: $dropin_dir" >&2
          exit 78
        fi
        mode="$(stat -c '%a' "$dropin_dir")"
        if [[ ! "$mode" =~ ^[0-7]{3,4}$ ]] || (( (8#$mode & 8#022) != 0 )); then
          echo "Systemd worker drop-in directory is writable by an untrusted identity: $dropin_dir" >&2
          exit 78
        fi
        for entry in "$dropin_dir"/* "$dropin_dir"/.[!.]* "$dropin_dir"/..?*; do
          if [ ! -e "$entry" ] && [ ! -L "$entry" ]; then
            continue
          fi
          if [ "$entry" != "$reviewed_worker_dropin" ]; then
            echo "Unreviewed systemd worker override blocks installation: $entry" >&2
            exit 78
          fi
          if [ ! -f "$entry" ] || [ -L "$entry" ] || \
             [ "$(stat -c '%U:%G:%a' "$entry")" != "root:root:644" ]; then
            echo "Reviewed worker resource override is unsafe: $entry" >&2
            exit 78
          fi
          if [ "$(<"$entry")" != "$expected_worker_dropin" ]; then
            echo "Existing worker resource override is not the reviewed profile: $entry" >&2
            exit 78
          fi
        done
      done < <(worker_unit_dropin_directories "$root" "$unit")
    done
  done
}

reject_unreviewed_worker_dropins

legacy_enabled_before=()
legacy_active_before=()
for legacy_unit in "${legacy_units[@]}"; do
  legacy_enabled_before+=("$(systemctl is-enabled "$legacy_unit" 2>/dev/null || true)")
  legacy_active_before+=("$(systemctl is-active "$legacy_unit" 2>/dev/null || true)")
done
for new_unit in "${new_units[@]}"; do
  existing_enabled="$(systemctl is-enabled "$new_unit" 2>/dev/null || true)"
  existing_active="$(systemctl is-active "$new_unit" 2>/dev/null || true)"
  if unit_state_is_enabled "$existing_enabled" || unit_state_is_live "$existing_active"; then
    echo "Refusing worker convergence while a new unit is enabled or live: $new_unit" >&2
    exit 70
  fi
done
if [ "$enable_reviewed_canary" -eq 1 ]; then
  for index in "${!legacy_units[@]}"; do
    if unit_state_is_enabled "${legacy_enabled_before[$index]}" || \
       unit_state_is_live "${legacy_active_before[$index]}"; then
      echo "Legacy crawler conflicts with reviewed canary: ${legacy_units[$index]}" >&2
      echo "Record and complete the manual legacy cutover; this installer will not stop it." >&2
      exit 70
    fi
  done
fi

ensure_service_account() {
  local account="$1"
  local supplementary_groups="$2"
  local account_gid account_uid actual_groups expected_groups
  local foreign_primary foreign_supplementary password_status system_uid_max
  system_uid_max="$(awk '$1 == "SYS_UID_MAX" { value=$2 } END { print value }' /etc/login.defs)"
  system_uid_max="${system_uid_max:-999}"
  if ! getent group "$account" >/dev/null; then
    groupadd --system "$account"
  fi
  if ! id "$account" >/dev/null 2>&1; then
    useradd \
      --system \
      --gid "$account" \
      --groups "$supplementary_groups" \
      --no-create-home \
      --home-dir /nonexistent \
      --shell /usr/sbin/nologin \
      "$account"
  else
    account_uid="$(id -u "$account")"
    if [ "$account_uid" -gt "$system_uid_max" ]; then
      echo "Refusing to repurpose a non-system UID: $account" >&2
      exit 78
    fi
    usermod \
      --gid "$account" \
      --groups "$supplementary_groups" \
      --home /nonexistent \
      --shell /usr/sbin/nologin \
      "$account"
  fi
  usermod --lock --expiredate -1 "$account"
  password_status="$(passwd -S "$account" | awk '{ print $2 }')"
  if [ "$(id -u "$account")" -eq 0 ] || \
     [ "$password_status" != "L" ] || \
     [ "$(id -gn "$account")" != "$account" ] || \
     [ "$(getent passwd "$account" | cut -d: -f6)" != "/nonexistent" ] || \
     [ "$(getent passwd "$account" | cut -d: -f7)" != "/usr/sbin/nologin" ]; then
    echo "Dedicated service account convergence failed: $account" >&2
    exit 78
  fi
  account_gid="$(getent group "$account" | cut -d: -f3)"
  foreign_primary="$(getent passwd | awk -F: -v expected="$account" -v gid="$account_gid" \
    '$1 != expected && $4 == gid { print $1 }')"
  foreign_supplementary="$(getent group "$account" | cut -d: -f4 | tr ',' '\n' | \
    awk -v expected="$account" 'NF && $0 != expected { print }')"
  if [ -n "$foreign_primary" ] || [ -n "$foreign_supplementary" ]; then
    echo "Dedicated service group has an unexpected member: $account" >&2
    exit 78
  fi
  actual_groups="$(id -nG "$account" | tr ' ' '\n' | sort -u)"
  expected_groups="$({
    printf '%s\n' "$account"
    printf '%s' "$supplementary_groups" | tr ',' '\n'
  } | awk 'NF' | sort -u)"
  if [ "$actual_groups" != "$expected_groups" ]; then
    echo "Dedicated service account has unexpected supplementary groups: $account" >&2
    exit 78
  fi
}

if ! getent group mooncen >/dev/null; then
  groupadd --system mooncen
fi
if ! getent group mooncen-crawler-status >/dev/null; then
  groupadd --system mooncen-crawler-status
fi
ensure_service_account mooncen-crawler-worker mooncen,mooncen-crawler-status
ensure_service_account mooncen-crawler-reporter mooncen
if [ "$(id -u mooncen-crawler-worker)" = "$(id -u mooncen-crawler-reporter)" ]; then
  echo "Worker and reporter must use distinct OS identities." >&2
  exit 78
fi
status_gid="$(getent group mooncen-crawler-status | cut -d: -f3)"
status_foreign_primary="$(getent passwd | awk -F: -v gid="$status_gid" '$4 == gid { print $1 }')"
status_members="$(getent group mooncen-crawler-status | cut -d: -f4 | tr ',' '\n' | \
  awk 'NF' | sort -u)"
if [ -n "$status_foreign_primary" ] || \
   [ "$status_members" != "mooncen-crawler-worker" ]; then
  echo "Crawler status group must contain only the worker and no primary account." >&2
  exit 78
fi

if [ -e "$CONFIG_DIR" ] || [ -L "$CONFIG_DIR" ]; then
  if [ ! -d "$CONFIG_DIR" ] || [ -L "$CONFIG_DIR" ]; then
    echo "Configuration directory is unsafe: $CONFIG_DIR" >&2
    exit 78
  fi
fi
install -d -o root -g root -m 0751 "$CONFIG_DIR"
for reviewed_parent in "$SYSTEMD_DIR" "$TMPFILES_DIR"; do
  if [ -e "$reviewed_parent" ] || [ -L "$reviewed_parent" ]; then
    if [ ! -d "$reviewed_parent" ] || [ -L "$reviewed_parent" ]; then
      echo "Reviewed installation directory is unsafe: $reviewed_parent" >&2
      exit 78
    fi
  fi
done
install -d -o root -g root -m 0755 "$SYSTEMD_DIR" "$TMPFILES_DIR"
worker_dropin_dir="$SYSTEMD_DIR/mooncen-crawler-pull-worker.service.d"
if [ -e "$worker_dropin_dir" ] || [ -L "$worker_dropin_dir" ]; then
  if [ ! -d "$worker_dropin_dir" ] || [ -L "$worker_dropin_dir" ]; then
    echo "Reviewed worker drop-in directory is unsafe: $worker_dropin_dir" >&2
    exit 78
  fi
fi
install -d -o root -g root -m 0755 "$worker_dropin_dir"
if [ "$(stat -c '%U:%G:%a' "$worker_dropin_dir")" != "root:root:755" ]; then
  echo "Reviewed worker drop-in directory ownership or mode is unsafe." >&2
  exit 78
fi

install_protected_file() {
  local source="$1"
  local destination="$2"
  local group="$3"
  local mode="$4"
  local temporary
  if [ -e "$destination" ] || [ -L "$destination" ]; then
    if [ -L "$destination" ] || [ ! -f "$destination" ]; then
      echo "Refusing to replace unsafe protected target: $destination" >&2
      exit 78
    fi
    if ! cmp -s -- "$source" "$destination" && \
       [ "$replace_protected_files" -ne 1 ]; then
      echo "Protected file differs: $destination (use --replace-protected-files)." >&2
      exit 73
    fi
  fi
  temporary="$(mktemp "$CONFIG_DIR/.$(basename "$destination").XXXXXX")"
  install -o root -g "$group" -m "$mode" "$source" "$temporary"
  mv -fT -- "$temporary" "$destination"
  if [ "$(stat -c '%U:%G:%a' "$destination")" != "root:$group:${mode#0}" ]; then
    echo "Protected file owner or mode convergence failed: $destination" >&2
    exit 78
  fi
}

install_reviewed_file() {
  local source="$1"
  local destination="$2"
  local mode="$3"
  local destination_directory temporary
  if [ -e "$destination" ] || [ -L "$destination" ]; then
    if [ -L "$destination" ] || [ ! -f "$destination" ]; then
      echo "Refusing to replace unsafe reviewed target: $destination" >&2
      exit 78
    fi
  fi
  destination_directory="$(dirname "$destination")"
  temporary="$(mktemp "$destination_directory/.$(basename "$destination").XXXXXX")"
  install -o root -g root -m "$mode" "$source" "$temporary"
  mv -fT -- "$temporary" "$destination"
}

install_protected_file "$worker_env" "$CONFIG_DIR/crawler-worker.env" \
  mooncen-crawler-worker 0640
install_protected_file "$reporter_env" "$CONFIG_DIR/crawler-release-reporter.env" \
  mooncen-crawler-reporter 0640
install_protected_file "$release_env" "$CONFIG_DIR/crawler-release-agent.env" root 0600
install_protected_file "$db_ca" "$CONFIG_DIR/db-root-ca.crt" root 0644
install_protected_file "$release_ca" "$CONFIG_DIR/crawler-release-tls-ca.crt" root 0644
install_protected_file "$allowed_signers" \
  "$CONFIG_DIR/crawler-release-allowed-signers" root 0600

tmpfiles_source="$APP_DIR/deploy/ubuntu/templates/crawler-release-agent.tmpfiles.conf"
tmpfiles_target="$TMPFILES_DIR/mooncen-crawler-release-agent.conf"
install_reviewed_file "$tmpfiles_source" "$tmpfiles_target" 0644
for managed_directory in \
  /opt/mooncen-crawler \
  /opt/mooncen-crawler/releases \
  /opt/mooncen-crawler/.staging \
  /var/lib/mooncen-crawler-release-agent \
  /var/lib/mooncen-crawler-release-agent/reports; do
  if [ -e "$managed_directory" ] || [ -L "$managed_directory" ]; then
    if [ ! -d "$managed_directory" ] || [ -L "$managed_directory" ]; then
      echo "Refusing tmpfiles convergence through an unsafe path: $managed_directory" >&2
      exit 78
    fi
  fi
done
systemd-tmpfiles --create "$tmpfiles_target"

expected_directories=(
  "/opt/mooncen-crawler|root:mooncen-crawler-worker:750"
  "/opt/mooncen-crawler/releases|root:mooncen-crawler-worker:750"
  "/opt/mooncen-crawler/.staging|root:root:700"
  "/var/lib/mooncen-crawler-release-agent|root:mooncen-crawler-reporter:710"
  "/var/lib/mooncen-crawler-release-agent/reports|root:mooncen-crawler-reporter:2770"
)
for directory_contract in "${expected_directories[@]}"; do
  directory_path="${directory_contract%%|*}"
  expected_stat="${directory_contract#*|}"
  if [ ! -d "$directory_path" ] || [ -L "$directory_path" ] || \
     [ "$(stat -c '%U:%G:%a' "$directory_path")" != "$expected_stat" ]; then
    echo "Crawler release directory contract failed: $directory_path" >&2
    exit 78
  fi
done
if ! runuser -u mooncen-crawler-worker -- \
     test -r "$CONFIG_DIR/crawler-worker.env" || \
   ! runuser -u mooncen-crawler-reporter -- \
     test -r "$CONFIG_DIR/crawler-release-reporter.env"; then
  echo "A service account cannot read its own protected environment." >&2
  exit 77
fi
if runuser -u mooncen-crawler-worker -- \
     test -r "$CONFIG_DIR/crawler-release-reporter.env" || \
   runuser -u mooncen-crawler-reporter -- \
     test -r "$CONFIG_DIR/crawler-worker.env" || \
   runuser -u mooncen-crawler-worker -- \
     test -r "$CONFIG_DIR/crawler-release-agent.env" || \
   runuser -u mooncen-crawler-reporter -- \
     test -r "$CONFIG_DIR/crawler-release-agent.env"; then
  echo "Worker/reporter OS credential isolation failed." >&2
  exit 78
fi
if ! runuser -u mooncen-crawler-reporter -- \
     test -r /var/lib/mooncen-crawler-release-agent/reports || \
   ! runuser -u mooncen-crawler-reporter -- \
     test -w /var/lib/mooncen-crawler-release-agent/reports || \
   runuser -u mooncen-crawler-worker -- \
     test -r /var/lib/mooncen-crawler-release-agent/reports || \
   runuser -u mooncen-crawler-worker -- \
     test -w /var/lib/mooncen-crawler-release-agent/reports; then
  echo "Reporter spool UID/group isolation failed." >&2
  exit 78
fi

for unit in "${new_units[@]}"; do
  assert_active_release_pinned
  source_unit="$APP_DIR/deploy/ubuntu/systemd/$unit"
  if [ ! -f "$source_unit" ] || [ -L "$source_unit" ]; then
    echo "Reviewed worker systemd unit is unavailable or unsafe: $source_unit" >&2
    exit 66
  fi
  install_reviewed_file "$source_unit" "$SYSTEMD_DIR/$unit" 0644
done
generated_dropin="$(mktemp "$installer_lock_dir/.worker-resources.XXXXXX")"
if ! "$PYTHON" -X utf8 -m tools.preflight_distributed_crawler_worker_host \
  --inventory-only \
  "${worker_assignment_args[@]}" \
  --render-systemd-drop-in >"$generated_dropin"; then
  rm -f -- "$generated_dropin"
  echo "Reviewed worker resource drop-in generation failed." >&2
  exit 78
fi
worker_dropin="$worker_dropin_dir/10-reviewed-worker-resources.conf"
install_reviewed_file "$generated_dropin" "$worker_dropin" 0644
rm -f -- "$generated_dropin"
if [ ! -f "$worker_dropin" ] || [ -L "$worker_dropin" ] || \
   [ "$(stat -c '%U:%G:%a' "$worker_dropin")" != "root:root:644" ]; then
  echo "Reviewed worker resource drop-in ownership or mode is unsafe." >&2
  exit 78
fi
systemd-analyze verify "${new_units[@]/#/$SYSTEMD_DIR/}"
systemctl daemon-reload

unit_property() {
  systemctl show "$1" --property="$2" --value
}

assert_unit_property() {
  local unit="$1"
  local property="$2"
  local expected="$3"
  local actual
  actual="$(unit_property "$unit" "$property")"
  if [ "$actual" != "$expected" ]; then
    echo "Effective systemd property mismatch: $unit $property" >&2
    exit 78
  fi
}

assert_unit_property_contains() {
  local unit="$1"
  local property="$2"
  local expected="$3"
  local actual
  actual="$(unit_property "$unit" "$property")"
  if [[ "$actual" != *"$expected"* ]]; then
    echo "Effective systemd property lacks reviewed value: $unit $property" >&2
    exit 78
  fi
}

assert_effective_worker_units() {
  local unit dropins property
  for unit in "${new_units[@]}"; do
    assert_unit_property "$unit" LoadState loaded
    assert_unit_property "$unit" FragmentPath "$SYSTEMD_DIR/$unit"
    dropins="$(unit_property "$unit" DropInPaths)"
    if [ "$unit" = "mooncen-crawler-pull-worker.service" ]; then
      if [ "$dropins" != "$reviewed_worker_dropin" ]; then
        echo "Pull-worker effective drop-in set is not exactly reviewed." >&2
        exit 78
      fi
    elif [ -n "$dropins" ]; then
      echo "Unreviewed effective systemd drop-in blocks installation: $unit" >&2
      exit 78
    fi
  done

  assert_unit_property mooncen-crawler-pull-worker.service User mooncen-crawler-worker
  assert_unit_property mooncen-crawler-release-agent.service User root
  assert_unit_property mooncen-crawler-release-reporter.service User mooncen-crawler-reporter
  assert_unit_property_contains mooncen-crawler-pull-worker.service ExecStart \
    "/opt/mooncen-worker/current/.venv/bin/python -X utf8 -m ops_agent.crawler_worker"
  assert_unit_property_contains mooncen-crawler-release-agent.service ExecStart \
    "/opt/mooncen-worker/current/.venv/bin/python -X utf8 -m ops_agent.crawler_release_agent"
  assert_unit_property_contains mooncen-crawler-release-reporter.service ExecStart \
    "/opt/mooncen-worker/current/tools/run_crawler_release_reporter.py"
  assert_unit_property_contains mooncen-crawler-pull-worker.service EnvironmentFiles \
    "/etc/mooncen/crawler-worker.env"
  assert_unit_property_contains mooncen-crawler-pull-worker.service EnvironmentFiles \
    "/opt/mooncen-crawler/current/release.env"
  assert_unit_property_contains mooncen-crawler-release-agent.service EnvironmentFiles \
    "/etc/mooncen/crawler-release-agent.env"
  assert_unit_property_contains mooncen-crawler-release-reporter.service EnvironmentFiles \
    "/etc/mooncen/crawler-release-reporter.env"

  case "$worker_id" in
    wtr-linux)
      assert_unit_property mooncen-crawler-pull-worker.service MemoryHigh 4294967296
      assert_unit_property mooncen-crawler-pull-worker.service MemoryMax 6442450944
      assert_unit_property mooncen-crawler-pull-worker.service CPUQuotaPerSecUSec 3s
      ;;
    gen1crawler)
      assert_unit_property mooncen-crawler-pull-worker.service MemoryHigh 2147483648
      assert_unit_property mooncen-crawler-pull-worker.service MemoryMax 4294967296
      assert_unit_property mooncen-crawler-pull-worker.service CPUQuotaPerSecUSec 2s
      ;;
    *)
      echo "Reviewed worker has no effective systemd resource assertion." >&2
      exit 78
      ;;
  esac

  for unit in \
    mooncen-crawler-pull-worker.service \
    mooncen-crawler-release-agent.service \
    mooncen-crawler-release-reporter.service; do
    for property in \
      NoNewPrivileges PrivateTmp PrivateDevices ProtectHome \
      ProtectKernelTunables ProtectKernelModules ProtectKernelLogs \
      ProtectControlGroups ProtectClock ProtectHostname RestrictSUIDSGID \
      RestrictRealtime LockPersonality RemoveIPC; do
      assert_unit_property "$unit" "$property" yes
    done
    assert_unit_property "$unit" ProtectSystem strict
    assert_unit_property "$unit" ProtectProc invisible
    assert_unit_property "$unit" ProcSubset pid
    assert_unit_property "$unit" CapabilityBoundingSet ""
    assert_unit_property "$unit" AmbientCapabilities ""
  done
}

assert_effective_worker_units

"$PYTHON" -X utf8 -m tools.preflight_distributed_crawler_worker_host \
  --release-env "$CONFIG_DIR/crawler-release-agent.env" \
  "${worker_assignment_args[@]}"
runuser -u mooncen-crawler-worker -- /usr/bin/env PYTHONPATH="$APP_DIR" \
  "$PYTHON" -X utf8 -m tools.preflight_distributed_crawler_control \
  --component worker \
  --env-file "$CONFIG_DIR/crawler-worker.env" \
  --installation-validation
runuser -u mooncen-crawler-reporter -- /usr/bin/env PYTHONPATH="$APP_DIR" \
  "$PYTHON" -X utf8 -m tools.preflight_distributed_crawler_control \
  --component reporter \
  --env-file "$CONFIG_DIR/crawler-release-reporter.env" \
  --installation-validation

if [ "$bootstrap_value_count" -ne 0 ]; then
  "$PYTHON" -X utf8 -m tools.bootstrap_distributed_crawler_release \
    --release-env "$CONFIG_DIR/crawler-release-agent.env" \
    --artifact "$bootstrap_artifact" \
    --signature "$bootstrap_signature" \
    --key-id "$bootstrap_key_id" \
    --code-version "$bootstrap_code_version" \
    --config-revision "$bootstrap_config_revision" \
    --sha256 "$bootstrap_sha256" \
    --size-bytes "$bootstrap_size_bytes"
fi

assert_legacy_unchanged() {
  local index enabled_after active_after
  for index in "${!legacy_units[@]}"; do
    enabled_after="$(systemctl is-enabled "${legacy_units[$index]}" 2>/dev/null || true)"
    active_after="$(systemctl is-active "${legacy_units[$index]}" 2>/dev/null || true)"
    if [ "$enabled_after" != "${legacy_enabled_before[$index]}" ] || \
       [ "$active_after" != "${legacy_active_before[$index]}" ]; then
      echo "Legacy crawler state changed unexpectedly: ${legacy_units[$index]}" >&2
      exit 70
    fi
  done
}

if [ "$enable_reviewed_canary" -eq 1 ]; then
  assert_legacy_unchanged
  for index in "${!legacy_units[@]}"; do
    if unit_state_is_enabled "${legacy_enabled_before[$index]}" || \
       unit_state_is_live "${legacy_active_before[$index]}"; then
      echo "Legacy crawler became conflicting before canary enable." >&2
      exit 70
    fi
  done
  "$PYTHON" -X utf8 -m tools.preflight_distributed_crawler_worker_host \
    --release-env "$CONFIG_DIR/crawler-release-agent.env" \
    "${worker_assignment_args[@]}" \
    --require-enabled \
    --require-baseline
  if ! runuser -u mooncen-crawler-worker -- \
    test -r /opt/mooncen-crawler/current/release.env; then
    echo "Worker OS identity cannot read the reviewed rollback baseline." >&2
    exit 77
  fi
  runuser -u mooncen-crawler-worker -- /usr/bin/env PYTHONPATH="$APP_DIR" \
    "$PYTHON" -X utf8 -m tools.preflight_distributed_crawler_control \
    --component worker --env-file "$CONFIG_DIR/crawler-worker.env"
  runuser -u mooncen-crawler-reporter -- /usr/bin/env PYTHONPATH="$APP_DIR" \
    "$PYTHON" -X utf8 -m tools.preflight_distributed_crawler_control \
    --component reporter --env-file "$CONFIG_DIR/crawler-release-reporter.env"

  canary_enable_units=(
    mooncen-crawler-worker.target
  )
  rollback_canary=1
  rollback_new_canary() {
    if [ "$rollback_canary" -eq 1 ]; then
      echo "Canary activation failed; stopping and disabling only the new worker units." >&2
      if ! systemctl stop \
        mooncen-crawler-worker.target \
        mooncen-crawler-release-agent.timer \
        mooncen-crawler-release-reporter.timer \
        mooncen-crawler-release-agent.service \
        mooncen-crawler-release-reporter.service \
        mooncen-crawler-pull-worker.service >/dev/null 2>&1; then
        echo "CRITICAL: one or more new canary units could not be stopped." >&2
      fi
      if ! systemctl disable "${canary_enable_units[@]}" >/dev/null 2>&1; then
        echo "CRITICAL: one or more new canary units could not be disabled." >&2
      fi
      for rollback_unit in "${new_units[@]}"; do
        rollback_enabled="$(systemctl is-enabled "$rollback_unit" 2>/dev/null || true)"
        rollback_active="$(systemctl is-active "$rollback_unit" 2>/dev/null || true)"
        if unit_state_is_enabled "$rollback_enabled" || \
           unit_state_is_live "$rollback_active"; then
          echo "CRITICAL: manual shutdown required for new unit: $rollback_unit" >&2
        fi
      done
    fi
  }
  trap rollback_new_canary EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  systemctl start mooncen-crawler-pull-worker.service
  current_health_ready=0
  health_attempt=0
  while [ "$health_attempt" -lt 60 ]; do
    if "$PYTHON" -X utf8 -m tools.preflight_distributed_crawler_worker_host \
      --release-env "$CONFIG_DIR/crawler-release-agent.env" \
      "${worker_assignment_args[@]}" \
      --require-enabled \
      --require-baseline \
      --require-current-health >/dev/null 2>&1; then
      current_health_ready=1
      break
    fi
    health_attempt=$((health_attempt + 1))
    sleep 1
  done
  if [ "$current_health_ready" -ne 1 ]; then
    "$PYTHON" -X utf8 -m tools.preflight_distributed_crawler_worker_host \
      --release-env "$CONFIG_DIR/crawler-release-agent.env" \
      "${worker_assignment_args[@]}" \
      --require-enabled \
      --require-baseline \
      --require-current-health || true
    echo "Worker did not publish fresh baseline health before release automation." >&2
    exit 70
  fi
  if [ ! -d /run/mooncen-crawler ] || [ -L /run/mooncen-crawler ] || \
     [ "$(stat -c '%U:%G:%a' /run/mooncen-crawler)" != \
       "mooncen-crawler-worker:mooncen-crawler-status:750" ] || \
     [ ! -f /run/mooncen-crawler/health.json ] || \
     [ -L /run/mooncen-crawler/health.json ] || \
     [ "$(stat -c '%U:%G:%a' /run/mooncen-crawler/health.json)" != \
       "mooncen-crawler-worker:mooncen-crawler-status:640" ]; then
    echo "Worker status handoff ownership or mode is unsafe." >&2
    exit 78
  fi
  if [ -e /run/mooncen-crawler/drain.json ] || \
     [ -L /run/mooncen-crawler/drain.json ]; then
    if [ ! -f /run/mooncen-crawler/drain.json ] || \
       [ -L /run/mooncen-crawler/drain.json ] || \
       [ "$(stat -c '%U:%G:%a' /run/mooncen-crawler/drain.json)" != \
         "mooncen-crawler-worker:mooncen-crawler-status:640" ]; then
      echo "Worker drain handoff ownership or mode is unsafe." >&2
      exit 78
    fi
  fi
  systemctl start mooncen-crawler-release-reporter.timer
  systemctl start mooncen-crawler-release-agent.timer
  for canary_unit in \
    mooncen-crawler-pull-worker.service \
    mooncen-crawler-release-agent.timer \
    mooncen-crawler-release-reporter.timer; do
    if ! systemctl is-active --quiet "$canary_unit"; then
      echo "Healthy canary dependency did not remain active: $canary_unit" >&2
      exit 70
    fi
  done
  # One target symlink is the only persistent boot transition. All dependency
  # health checks completed before this point; a crash earlier leaves no new
  # boot-enabled worker unit, and rollback removes this single target grant.
  systemctl start mooncen-crawler-worker.target
  assert_active_release_pinned
  systemctl enable mooncen-crawler-worker.target
  if ! systemctl is-enabled --quiet mooncen-crawler-worker.target || \
     ! systemctl is-active --quiet mooncen-crawler-worker.target; then
    echo "Reviewed worker activation target did not become enabled and active." >&2
    exit 70
  fi
  assert_legacy_unchanged
  rollback_canary=0
  trap - EXIT INT TERM
else
  for new_unit in "${new_units[@]}"; do
    installed_enabled="$(systemctl is-enabled "$new_unit" 2>/dev/null || true)"
    installed_active="$(systemctl is-active "$new_unit" 2>/dev/null || true)"
    if unit_state_is_enabled "$installed_enabled" || unit_state_is_live "$installed_active"; then
      echo "Default installation unexpectedly activated a new unit: $new_unit" >&2
      exit 70
    fi
  done
  assert_legacy_unchanged
fi

cat <<REPORT
Distributed crawler worker host converged.
worker_id=${worker_id}
agent_id=${agent_id}
hostname=${worker_hostname}
database=${shared_host}:${shared_port}/${shared_database}
worker_login=${queue_user}
reporter_login=${reporter_user}
reviewed_canary_enabled=${enable_reviewed_canary}
bootstrap_supplied=$([ "$bootstrap_value_count" -ne 0 ] && printf true || printf false)

Legacy crawler units were not stopped or disabled.
REPORT
