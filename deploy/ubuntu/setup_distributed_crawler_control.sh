#!/usr/bin/env bash
set -euo pipefail
umask 077

APP_DIR="${APP_DIR:-/opt/mooncen}"
CONFIG_DIR="${CONFIG_DIR:-/etc/mooncen}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
PYTHON="$APP_DIR/.venv/bin/python"

schema_env=""
scheduler_env=""
finalizer_env=""
publisher_env=""
approver_env=""
release_approver_env=""
release_admin_env=""
metrics_env=""
backup_receipt=""
backup_receipt_signature=""
backup_receipt_nonce=""
confirmed_database=""
enable_control_plane=0
enable_release_actions=0
replace_protected_env=0

usage() {
  cat >&2 <<'EOF'
Usage: setup_distributed_crawler_control.sh \
  --schema-env PATH \
  --scheduler-env PATH \
  --finalizer-env PATH \
  --publisher-env PATH \
  --approver-env PATH \
  --release-approver-env PATH \
  --release-admin-env PATH \
  --metrics-env PATH \
  --backup-receipt /var/lib/mooncen-crawler-control-root-trust/receipts/NONCE/receipt.json \
  --backup-receipt-signature /var/lib/mooncen-crawler-control-root-trust/receipts/NONCE/receipt.json.sig \
  --backup-receipt-nonce NONCE \
  --confirm-staging-database NAME \
  [--replace-protected-env] \
  [--enable-control-plane] \
  [--enable-release-actions]

All non-help invocations currently fail closed before filesystem or database
mutation. The signed atomic release-tree transport is implemented separately,
but no install or enable mode is available until a fresh verified gen1db backup attestation,
including its isolated restore evidence, is consumed here. There is no override flag.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --schema-env)
      schema_env="${2:-}"
      shift 2
      ;;
    --scheduler-env)
      scheduler_env="${2:-}"
      shift 2
      ;;
    --finalizer-env)
      finalizer_env="${2:-}"
      shift 2
      ;;
    --publisher-env)
      publisher_env="${2:-}"
      shift 2
      ;;
    --approver-env)
      approver_env="${2:-}"
      shift 2
      ;;
    --release-approver-env)
      release_approver_env="${2:-}"
      shift 2
      ;;
    --release-admin-env)
      release_admin_env="${2:-}"
      shift 2
      ;;
    --metrics-env)
      metrics_env="${2:-}"
      shift 2
      ;;
    --backup-receipt)
      backup_receipt="${2:-}"
      shift 2
      ;;
    --backup-receipt-signature)
      backup_receipt_signature="${2:-}"
      shift 2
      ;;
    --backup-receipt-nonce)
      backup_receipt_nonce="${2:-}"
      shift 2
      ;;
    --confirm-staging-database)
      confirmed_database="${2:-}"
      shift 2
      ;;
    --replace-protected-env)
      replace_protected_env=1
      shift
      ;;
    --enable-control-plane)
      enable_control_plane=1
      shift
      ;;
    --enable-release-actions)
      enable_release_actions=1
      shift
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

if [ "$enable_release_actions" -eq 1 ] && [ "$enable_control_plane" -ne 1 ]; then
  echo "--enable-release-actions requires --enable-control-plane." >&2
  exit 64
fi

# This gate is intentionally unconditional. The direct installer must not
# create a lock directory, inspect credentials, or connect to PostgreSQL until
# backup recovery evidence is consumed by a machine-verifiable contract. The
# signed root-owned release is prepared by the Windows transport, never by a
# direct invocation of this installer. There is no override flag.
echo "NOT READY: distributed crawler control installation is disabled." >&2
echo "Missing gate: a release-bound, OpenSSH-signed real-gen1db backup receipt and atomic advisory/audit consumption." >&2
echo "No filesystem installation or database mutation was attempted." >&2
exit 70

if [ "$(id -u)" -ne 0 ]; then
  echo "Run setup_distributed_crawler_control.sh as root." >&2
  exit 77
fi
if ! command -v flock >/dev/null 2>&1; then
  echo "flock is required to serialize control-plane installation." >&2
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
  echo "Another distributed crawler control-plane installation is running." >&2
  exit 75
fi
if [ "$APP_DIR" != "/opt/mooncen" ] || \
   [ "$CONFIG_DIR" != "/etc/mooncen" ] || \
   [ "$SYSTEMD_DIR" != "/etc/systemd/system" ]; then
  echo "The shipped systemd units require canonical app, config, and systemd paths." >&2
  exit 78
fi
export MOONCEN_CRAWLER_INSTALL_LOCK_FD=9
if [[ ! "$confirmed_database" =~ ^[a-z_][a-z0-9_]*$ ]]; then
  echo "--confirm-staging-database is required and must be a PostgreSQL identifier." >&2
  exit 64
fi
for required_file in \
  "$PYTHON" \
  "$APP_DIR/.deploy-info" \
  "$APP_DIR/.mooncen-control-tree.manifest" \
  "$APP_DIR/.mooncen-control-runtime.manifest" \
  "$APP_DIR/deploy/ubuntu/requirements-crawler-control.lock" \
  "$APP_DIR/config/production_topology.json" \
  "$APP_DIR/ops_agent/production_topology.py" \
  "$APP_DIR/ops_agent/crawler_release_action_worker.py" \
  "$APP_DIR/tools/ensure_crawler_control_schema.py" \
  "$APP_DIR/tools/bootstrap_crawler_credential_registry.py" \
  "$APP_DIR/tools/approve_crawler_release_action.py" \
  "$APP_DIR/tools/manage_crawler_release.py" \
  "$APP_DIR/tools/provision_crawler_service_login.py" \
  "$APP_DIR/tools/postgres_scram_verifier.py" \
  "$APP_DIR/tools/preflight_distributed_crawler_control.py" \
  "$APP_DIR/tools/crawler_control_backup_attestation.py" \
  "$APP_DIR/DB/roles_body.sql" \
  "$APP_DIR/DB/crawler_control_migrations/20260810_001_crawler_control_plane.sql" \
  "$APP_DIR/DB/crawler_control_migrations/20260812_001_install_receipt_consumption.sql" \
  "$APP_DIR/DB/crawler_control_migrations/20260812_002_release_action_requests.sql" \
  "$APP_DIR/DB/crawler_control_migrations/20260812_003_crawler_studio.sql" \
  "$APP_DIR/DB/crawler_control_migrations/20260812_004_rollout_worker_snapshots.sql" \
  "$APP_DIR/DB/crawler_control_migrations/20260812_005_attempt_release_generation.sql" \
  "$APP_DIR/DB/crawler_control_migrations/20260812_006_release_operator_approvals.sql" \
  "$APP_DIR/DB/crawler_control_migrations/20260812_007_quality_environment_isolation.sql" \
  "$APP_DIR/DB/crawler_control_database_marker.sql" \
  "$APP_DIR/DB/staging_control_plane.sql"; do
  if [ ! -f "$required_file" ] || [ -L "$required_file" ]; then
    echo "Required reviewed release file is unavailable or unsafe: $required_file" >&2
    exit 66
  fi
done
cd "$APP_DIR"

if [ ! -d "$APP_DIR" ] || [ -L "$APP_DIR" ] || \
   [ "$(hostname -s 2>/dev/null || true)" != gen1db ]; then
  echo "Distributed crawler control installation is pinned to a regular /opt/mooncen release on hostname gen1db." >&2
  exit 78
fi

deploy_info="$APP_DIR/.deploy-info"
if [ "$(stat -c '%U:%G:%a' "$deploy_info")" != root:root:400 ] || \
   ! lsattr -d -- "$deploy_info" | awk '{print $1}' | grep -q i; then
  echo "Crawler-control deployment provenance file is unsafe." >&2
  exit 78
fi

deploy_manifest_value() {
  local key="$1"
  awk -F= -v expected="$key" '
    $1 == expected {
      count += 1
      value = substr($0, length(expected) + 2)
    }
    END {
      if (count != 1) exit 65
      printf "%s", value
    }
  ' "$deploy_info"
}

deploy_commit="$(deploy_manifest_value DEPLOY_COMMIT)" || {
  echo "Crawler-control release commit provenance is invalid." >&2
  exit 65
}
deploy_archive_sha256="$(deploy_manifest_value DEPLOY_ARCHIVE_SHA256)" || {
  echo "Crawler-control release artifact provenance is invalid." >&2
  exit 65
}
deploy_tree_sha256="$(deploy_manifest_value DEPLOY_TREE_SHA256)" || {
  echo "Crawler-control release tree provenance is invalid." >&2
  exit 65
}
runtime_lock_sha256="$(deploy_manifest_value RUNTIME_LOCK_SHA256)" || {
  echo "Crawler-control runtime lock provenance is invalid." >&2
  exit 65
}
runtime_tree_sha256="$(deploy_manifest_value RUNTIME_TREE_SHA256)" || {
  echo "Crawler-control runtime tree provenance is invalid." >&2
  exit 65
}
deploy_node_role="$(deploy_manifest_value NODE_ROLE)" || {
  echo "Crawler-control release role provenance is invalid." >&2
  exit 65
}
if [[ ! "$deploy_commit" =~ ^[0-9a-f]{40}$|^[0-9a-f]{64}$ ]] || \
   [[ ! "$deploy_archive_sha256" =~ ^[0-9a-f]{64}$ ]] || \
   [[ ! "$deploy_tree_sha256" =~ ^[0-9a-f]{64}$ ]] || \
   [[ ! "$runtime_lock_sha256" =~ ^[0-9a-f]{64}$ ]] || \
   [[ ! "$runtime_tree_sha256" =~ ^[0-9a-f]{64}$ ]] || \
   [ "$deploy_node_role" != crawler-control ]; then
  echo "The installed release is not a provenance-marked crawler-control release." >&2
  exit 65
fi
if [ "$(sha256sum "$APP_DIR/.mooncen-control-tree.manifest" | awk '{print $1}')" != "$deploy_tree_sha256" ] || \
   [ "$(sha256sum "$APP_DIR/deploy/ubuntu/requirements-crawler-control.lock" | awk '{print $1}')" != "$runtime_lock_sha256" ] || \
   [ "$(sha256sum "$APP_DIR/.mooncen-control-runtime.manifest" | awk '{print $1}')" != "$runtime_tree_sha256" ]; then
  echo "Crawler-control installed source/runtime provenance does not match .deploy-info." >&2
  exit 65
fi
"$PYTHON" -I - <<'PY'
from importlib.metadata import version
expected = {"psycopg2-binary": "2.9.12", "python-dotenv": "1.2.2", "PyYAML": "6.0.3"}
for distribution, required in expected.items():
    if version(distribution) != required:
        raise SystemExit(65)
import dotenv
import psycopg2
import yaml
PY

reviewed_crawler_mode() {
  "$PYTHON" -I - "$APP_DIR" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
from ops_agent.production_topology import load_production_topology

print(load_production_topology(root).crawler_mode)
PY
}

reviewed_control_contract() {
  "$PYTHON" -I - "$APP_DIR" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
from ops_agent.production_topology import load_production_topology

topology = load_production_topology(root)
placement = topology.primary_for("crawler_control")
print(f"{topology.crawler_mode}\t{placement.node}\t{placement.service_host}")
PY
}

if ! control_contract="$(reviewed_control_contract)"; then
  echo "The installed release has no valid reviewed crawler_control topology." >&2
  exit 65
fi
IFS=$'\t' read -r topology_crawler_mode topology_control_node topology_control_host \
  <<<"$control_contract"
if { [ "$topology_crawler_mode" != legacy ] && \
     [ "$topology_crawler_mode" != distributed ]; } || \
   [ "$topology_control_node" != gen1db ] || \
   [ "$topology_control_host" != gen1db ]; then
  echo "The reviewed crawler_control primary must be gen1db." >&2
  exit 78
fi

install -d -o root -g root -m 0751 "$CONFIG_DIR"

validate_protected_file() {
  local path="$1"
  local mode
  if [ ! -f "$path" ] || [ -L "$path" ]; then
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
      key = pair[1]
      count[key] += 1
      if (count[key] > 1) {
        print "Duplicate protected environment key: " key > "/dev/stderr"
        invalid = 1
      }
      next
    }
    {
      print "Invalid protected environment line: " NR > "/dev/stderr"
      invalid = 1
    }
    END { exit invalid ? 65 : 0 }
  ' "$path"; then
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
      if (count > 1 || (required == 1 && count != 1)) {
        print "Invalid environment key count: " expected > "/dev/stderr"
        exit 65
      }
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

for protected_input in \
  "$schema_env" "$scheduler_env" "$finalizer_env" "$publisher_env" \
  "$approver_env" "$release_approver_env" "$release_admin_env" "$metrics_env"; do
  if [ -z "$protected_input" ]; then
    usage
    exit 64
  fi
  validate_protected_file "$protected_input"
done

schema_host="$(read_env_value OPS_CRAWLER_SCHEMA_DB_HOST "$schema_env")"
schema_port="$(read_env_value OPS_CRAWLER_SCHEMA_DB_PORT "$schema_env")"
schema_database="$(read_env_value OPS_CRAWLER_SCHEMA_DB_NAME "$schema_env")"
schema_user="$(read_env_value OPS_CRAWLER_SCHEMA_DB_USER "$schema_env")"
schema_password="$(read_env_value OPS_CRAWLER_SCHEMA_DB_PASSWORD "$schema_env")"
schema_owner="$(read_env_value OPS_CRAWLER_SCHEMA_OBJECT_OWNER "$schema_env")"
if [[ ! "$schema_host" =~ ^[A-Za-z0-9._:-]+$ ]] || \
   [[ ! "$schema_port" =~ ^[0-9]+$ ]] || \
   [ "$schema_port" -lt 1 ] || [ "$schema_port" -gt 65535 ] || \
   [[ ! "$schema_database" =~ ^[a-z_][a-z0-9_]*$ ]] || \
   [[ ! "$schema_user" =~ ^[a-z_][a-z0-9_]*$ ]] || \
   [[ ! "$schema_owner" =~ ^[a-z_][a-z0-9_]*$ ]] || \
   [ "$schema_owner" = "$schema_user" ]; then
  echo "Schema administrator endpoint or role is invalid." >&2
  exit 78
fi
if [ "$schema_database" != "$confirmed_database" ]; then
  echo "Confirmed staging database does not match the schema administrator target." >&2
  exit 78
fi
if [ "$schema_host" != gen1db ] || \
   [ "$schema_database" != mooncen_staging ] || \
   [ "$confirmed_database" != mooncen_staging ]; then
  echo "Production crawler-control setup is pinned to gen1db/mooncen_staging." >&2
  exit 78
fi

runtime_environment="$(read_env_value ENVIRONMENT "$scheduler_env")"
if [ "$runtime_environment" != "production" ] && [ "$runtime_environment" != "staging" ]; then
  echo "Scheduler ENVIRONMENT must be production or staging." >&2
  exit 78
fi

shared_host="$(read_env_value OPS_CRAWLER_SHARED_DB_HOST "$scheduler_env")"
shared_port="$(read_env_value OPS_CRAWLER_SHARED_DB_PORT "$scheduler_env")"
shared_database="$(read_env_value OPS_CRAWLER_SHARED_DB_NAME "$scheduler_env")"
if [[ ! "$shared_host" =~ ^[A-Za-z0-9._:-]+$ ]] || \
   [[ ! "$shared_port" =~ ^[0-9]+$ ]] || \
   [ "$shared_port" -lt 1 ] || [ "$shared_port" -gt 65535 ] || \
   [ "$shared_host" != "$schema_host" ] || [ "$shared_port" != "$schema_port" ] || \
   [ "$shared_database" != "$schema_database" ]; then
  echo "The service control endpoint and schema administrator must select the same database." >&2
  exit 78
fi
if [ "$shared_host" != gen1db ] || [ "$shared_database" != mooncen_staging ]; then
  echo "Every crawler-control service must use the exact gen1db/mooncen_staging endpoint." >&2
  exit 78
fi

if [[ ! "$backup_receipt_nonce" =~ ^[0-9a-f]{64}$ ]] || \
   [ "$backup_receipt" != "/var/lib/mooncen-crawler-control-root-trust/receipts/$backup_receipt_nonce/receipt.json" ] || \
   [ "$backup_receipt_signature" != "/var/lib/mooncen-crawler-control-root-trust/receipts/$backup_receipt_nonce/receipt.json.sig" ]; then
  echo "The signed backup receipt must use its canonical root-only nonce path." >&2
  exit 78
fi

assert_shared_endpoint() {
  local path="$1"
  local component="$2"
  local host port database environment
  host="$(read_env_value OPS_CRAWLER_SHARED_DB_HOST "$path")"
  port="$(read_env_value OPS_CRAWLER_SHARED_DB_PORT "$path")"
  database="$(read_env_value OPS_CRAWLER_SHARED_DB_NAME "$path")"
  environment="$(read_env_value ENVIRONMENT "$path")"
  if [ "$host" != "$shared_host" ] || [ "$port" != "$shared_port" ] || \
     [ "$database" != "$shared_database" ]; then
    echo "$component does not target the exact shared staging endpoint." >&2
    exit 78
  fi
  if [ "$environment" != "$runtime_environment" ]; then
    echo "$component ENVIRONMENT differs from the scheduler." >&2
    exit 78
  fi
}

assert_shared_endpoint "$scheduler_env" scheduler
assert_shared_endpoint "$finalizer_env" finalizer
assert_shared_endpoint "$publisher_env" publisher
assert_shared_endpoint "$approver_env" approver
assert_shared_endpoint "$release_approver_env" release_approver
assert_shared_endpoint "$release_admin_env" release_admin
assert_shared_endpoint "$metrics_env" observer

control_user="$(read_env_value OPS_CRAWLER_CONTROL_DB_USER "$scheduler_env")"
publisher_user="$(read_env_value OPS_CRAWLER_PUBLISHER_DB_USER "$publisher_env")"
finalizer_user="$(read_env_value OPS_CRAWLER_FINALIZER_DB_USER "$finalizer_env")"
approver_user="$(read_env_value OPS_CRAWLER_APPROVER_DB_USER "$approver_env")"
release_approver_user="$(read_env_value OPS_CRAWLER_RELEASE_APPROVER_DB_USER "$release_approver_env")"
release_admin_user="$(read_env_value OPS_CRAWLER_RELEASE_ADMIN_DB_USER "$release_admin_env")"
observer_user="$(read_env_value OPS_CRAWLER_METRICS_DB_USER "$metrics_env")"
finalizer_password="$(read_env_value OPS_CRAWLER_FINALIZER_DB_PASSWORD "$finalizer_env")"
approver_password="$(read_env_value OPS_CRAWLER_APPROVER_DB_PASSWORD "$approver_env")"
release_approver_password="$(read_env_value OPS_CRAWLER_RELEASE_APPROVER_DB_PASSWORD "$release_approver_env")"
release_admin_password="$(read_env_value OPS_CRAWLER_RELEASE_ADMIN_DB_PASSWORD "$release_admin_env")"
observer_password="$(read_env_value OPS_CRAWLER_METRICS_DB_PASSWORD "$metrics_env")"
auto_promotion="$(read_env_value OPS_CRAWLER_AUTO_PROMOTION_ENABLED "$finalizer_env")"
control_password="$(read_env_value OPS_CRAWLER_CONTROL_DB_PASSWORD "$scheduler_env")"
publisher_password="$(read_env_value OPS_CRAWLER_PUBLISHER_DB_PASSWORD "$publisher_env")"
managed_users=(
  "$control_user" "$publisher_user" "$finalizer_user" "$approver_user"
  "$release_approver_user" "$release_admin_user" "$observer_user"
)
managed_passwords=(
  "$control_password" "$publisher_password" "$finalizer_password"
  "$approver_password" "$release_approver_password" "$release_admin_password" "$observer_password"
)
for index in "${!managed_users[@]}"; do
  if [[ ! "${managed_users[$index]}" =~ ^[a-z_][a-z0-9_]*$ ]] || \
     [ "${managed_users[$index]}" = "$schema_user" ] || \
     [ "${managed_users[$index]}" = "$schema_owner" ] || \
     [ "${managed_passwords[$index]}" = "$schema_password" ]; then
    echo "A runtime login reuses a schema administrator/owner identity or secret." >&2
    exit 78
  fi
  for other in "${!managed_users[@]}"; do
    if [ "$other" -le "$index" ]; then
      continue
    fi
    if [ "${managed_users[$index]}" = "${managed_users[$other]}" ] || \
       [ "${managed_passwords[$index]}" = "${managed_passwords[$other]}" ]; then
      echo "Every crawler component requires a distinct database login and password." >&2
      exit 78
    fi
  done
done
if [ "$auto_promotion" != "false" ]; then
  echo "OPS_CRAWLER_AUTO_PROMOTION_ENABLED must remain false; approval is a separate role." >&2
  exit 78
fi
publisher_output="$(read_env_value OPS_CRAWLER_DESIRED_STATE_OUTPUT "$publisher_env")"
publisher_environment="$(read_env_value OPS_CRAWLER_RELEASE_ENVIRONMENT "$publisher_env")"
metrics_output="$(read_env_value OPS_CRAWLER_METRICS_OUTPUT "$metrics_env")"
release_public_root="$(read_env_value OPS_CRAWLER_RELEASE_PUBLIC_ROOT "$release_admin_env")"
release_allowed_signers="$(read_env_value OPS_CRAWLER_ALLOWED_SIGNERS "$release_admin_env")"
scheduler_enabled="$(read_env_value OPS_CRAWLER_CONTROL_ENABLED "$scheduler_env")"
scheduler_manifest="$(read_env_value OPS_CRAWLER_PROVIDER_MANIFEST "$scheduler_env")"
scheduler_code_version="$(read_env_value OPS_CRAWLER_CODE_VERSION "$scheduler_env")"
scheduler_digest="$(read_env_value OPS_CRAWLER_ARTIFACT_DIGEST "$scheduler_env")"
scheduler_config_revision="$(read_env_value OPS_CRAWLER_CONFIG_REVISION "$scheduler_env")"
scheduler_manifest_revision=""
if [ -f "$scheduler_manifest" ] && [ ! -L "$scheduler_manifest" ]; then
  scheduler_manifest_revision="$(sha256sum -- "$scheduler_manifest" | awk '{ print $1 }')"
fi
if [ "$publisher_output" != "/var/lib/mooncen-crawler-control/public/state/desired-state.json" ] || \
   [ "$publisher_environment" != "$runtime_environment" ]; then
  echo "Publisher output must match the sandboxed public desired-state path." >&2
  exit 78
fi
if [ "$release_public_root" != "/var/lib/mooncen-crawler-control/public" ] || \
   [[ "$release_allowed_signers" != /* ]] || \
   [ ! -f "$release_allowed_signers" ] || [ -L "$release_allowed_signers" ] || \
   [ "$(stat -c '%U' "$release_allowed_signers")" != "root" ] || \
   (( (8#$(stat -c '%a' "$release_allowed_signers") & 8#022) != 0 )); then
  echo "Release-admin public root or allowed-signers file is unsafe." >&2
  exit 78
fi
if [ "$metrics_output" != "/var/lib/mooncen-crawler-observer/mooncen_crawler_control.prom" ]; then
  echo "Observer output must match its fixed StateDirectory path." >&2
  exit 78
fi
if [ "$scheduler_enabled" != "true" ] || \
   [[ "$scheduler_manifest" != /* ]] || \
   [ ! -f "$scheduler_manifest" ] || [ -L "$scheduler_manifest" ] || \
   [ -z "$scheduler_code_version" ] || \
   [[ ! "$scheduler_digest" =~ ^[0-9a-f]{64}$ ]] || \
   [ "$scheduler_digest" = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" ] || \
   [ "$scheduler_config_revision" != "$scheduler_manifest_revision" ]; then
  echo "Scheduler release identity or reviewed provider manifest is invalid." >&2
  exit 78
fi

# Reject the public template sentinels and SASLprep-sensitive password bytes
# before the first database write.  Provisioners repeat these checks at their
# own trust boundaries.
"$PYTHON" -X utf8 -m tools.postgres_scram_verifier \
  --env-file "$schema_env" \
  --password-key OPS_CRAWLER_SCHEMA_DB_PASSWORD \
  --validate-only >/dev/null
"$PYTHON" -X utf8 -m tools.postgres_scram_verifier \
  --env-file "$scheduler_env" \
  --password-key OPS_CRAWLER_CONTROL_DB_PASSWORD \
  --validate-only >/dev/null
"$PYTHON" -X utf8 -m tools.postgres_scram_verifier \
  --env-file "$publisher_env" \
  --password-key OPS_CRAWLER_PUBLISHER_DB_PASSWORD \
  --validate-only >/dev/null
"$PYTHON" -X utf8 -m tools.postgres_scram_verifier \
  --env-file "$finalizer_env" \
  --password-key OPS_CRAWLER_FINALIZER_DB_PASSWORD \
  --validate-only >/dev/null
"$PYTHON" -X utf8 -m tools.postgres_scram_verifier \
  --env-file "$approver_env" \
  --password-key OPS_CRAWLER_APPROVER_DB_PASSWORD \
  --validate-only >/dev/null
"$PYTHON" -X utf8 -m tools.postgres_scram_verifier \
  --env-file "$release_approver_env" \
  --password-key OPS_CRAWLER_RELEASE_APPROVER_DB_PASSWORD \
  --validate-only >/dev/null
"$PYTHON" -X utf8 -m tools.postgres_scram_verifier \
  --env-file "$release_admin_env" \
  --password-key OPS_CRAWLER_RELEASE_ADMIN_DB_PASSWORD \
  --validate-only >/dev/null
"$PYTHON" -X utf8 -m tools.postgres_scram_verifier \
  --env-file "$metrics_env" \
  --password-key OPS_CRAWLER_METRICS_DB_PASSWORD \
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

assert_new_control_units_quiescent() {
  local unit enabled active
  for unit in \
    mooncen-crawler-control-scheduler.service \
    mooncen-crawler-control-finalizer.service \
    mooncen-crawler-release-action-worker.service \
    mooncen-crawler-release-publisher.service \
    mooncen-crawler-release-publisher.timer \
    mooncen-crawler-control-metrics.service \
    mooncen-crawler-control-metrics.timer; do
    enabled="$(systemctl is-enabled "$unit" 2>/dev/null || true)"
    active="$(systemctl is-active "$unit" 2>/dev/null || true)"
    if unit_state_is_enabled "$enabled" || unit_state_is_live "$active"; then
      echo "Control-plane unit became enabled or live during protected convergence: $unit" >&2
      return 1
    fi
  done
}

control_plane_runtime_snapshot() {
  local unit enabled active substate result main_pid
  for unit in \
    mooncen-crawler-control-scheduler.service \
    mooncen-crawler-control-finalizer.service; do
    enabled="$(systemctl is-enabled "$unit" 2>/dev/null || true)"
    active="$(systemctl is-active "$unit" 2>/dev/null || true)"
    substate="$(systemctl show "$unit" --property=SubState --value 2>/dev/null || true)"
    result="$(systemctl show "$unit" --property=Result --value 2>/dev/null || true)"
    main_pid="$(systemctl show "$unit" --property=MainPID --value 2>/dev/null || true)"
    if ! unit_state_is_enabled "$enabled" || [ "$active" != active ] || \
       [ "$substate" != running ] || [ "$result" != success ] || \
       [[ ! "$main_pid" =~ ^[1-9][0-9]*$ ]]; then
      echo "Control-plane service did not reach a stable running state: $unit" >&2
      return 1
    fi
    printf '%s=%s\n' "$unit" "$main_pid"
  done
  for unit in \
    mooncen-crawler-release-publisher.timer \
    mooncen-crawler-control-metrics.timer; do
    enabled="$(systemctl is-enabled "$unit" 2>/dev/null || true)"
    active="$(systemctl is-active "$unit" 2>/dev/null || true)"
    substate="$(systemctl show "$unit" --property=SubState --value 2>/dev/null || true)"
    if ! unit_state_is_enabled "$enabled" || [ "$active" != active ] || \
       [ "$substate" != waiting ]; then
      echo "Control-plane timer did not reach its waiting state: $unit" >&2
      return 1
    fi
  done
  if [ "$(systemctl show mooncen-crawler-release-publisher.service \
          --property=Result --value 2>/dev/null || true)" != success ] || \
     [ "$(systemctl show mooncen-crawler-release-publisher.service \
          --property=ExecMainStatus --value 2>/dev/null || true)" != 0 ] || \
     [ ! -f /var/lib/mooncen-crawler-control/public/state/desired-state.json ] || \
     [ -L /var/lib/mooncen-crawler-control/public/state/desired-state.json ] || \
     [ "$(stat -c '%U:%G:%a' \
          /var/lib/mooncen-crawler-control/public/state/desired-state.json)" != \
       "mooncen-crawler-publisher:mooncen-crawler-publisher:644" ]; then
    echo "Crawler desired-state publisher did not produce its exact public document." >&2
    return 1
  fi
}

# Snapshot and reject dual-scheduler states before schema, role, or password
# convergence.  A Type=oneshot unit reports "activating" while it is running,
# so exact comparison with only "active" is unsafe here.
legacy_timer_enabled_before="$(systemctl is-enabled mooncen-crawler.timer 2>/dev/null || true)"
legacy_timer_active_before="$(systemctl is-active mooncen-crawler.timer 2>/dev/null || true)"
legacy_service_enabled_before="$(systemctl is-enabled mooncen-crawler.service 2>/dev/null || true)"
legacy_service_active_before="$(systemctl is-active mooncen-crawler.service 2>/dev/null || true)"
legacy_once_active_before="$(systemctl is-active mooncen-crawler-once.service 2>/dev/null || true)"
legacy_apply_enabled_before="$(systemctl is-enabled mooncen-staging-apply.timer 2>/dev/null || true)"
legacy_apply_active_before="$(systemctl is-active mooncen-staging-apply.timer 2>/dev/null || true)"
legacy_apply_service_before="$(systemctl is-active mooncen-staging-apply.service 2>/dev/null || true)"
legacy_apply_instances_before="$(
  systemctl list-units --type=service --all --no-legend --no-pager \
    'mooncen-staging-apply@*.service' 2>/dev/null || true
)"

assert_new_control_units_quiescent || exit 70

if [ "$enable_control_plane" -eq 1 ]; then
  if ! topology_crawler_mode="$(reviewed_crawler_mode)"; then
    echo "Refusing control-plane enable without a valid reviewed crawler topology." >&2
    exit 70
  fi
  if [ "$topology_crawler_mode" != distributed ]; then
    echo "Refusing control-plane enable until crawlerMode is reviewed as distributed." >&2
    exit 70
  fi
fi

if [ "$enable_control_plane" -eq 1 ] && \
   { unit_state_is_enabled "$legacy_timer_enabled_before" || \
     unit_state_is_live "$legacy_timer_active_before" || \
     unit_state_is_enabled "$legacy_service_enabled_before" || \
     unit_state_is_live "$legacy_service_active_before" || \
     unit_state_is_live "$legacy_once_active_before"; }; then
  echo "Refusing to enable the control scheduler while the legacy crawler is enabled or live." >&2
  echo "Complete the documented canary and manual legacy cutover first." >&2
  exit 70
fi
if [ "$enable_control_plane" -eq 1 ] && \
   { ! unit_state_is_enabled "$legacy_apply_enabled_before" || \
     ! unit_state_is_live "$legacy_apply_active_before"; }; then
  echo "Refusing cutover without the pinned staging apply downstream timer enabled and active." >&2
  exit 70
fi

# Existing marked logins must already be represented by the protected
# fingerprint registry. Older installations use the explicit authenticated
# bootstrap command documented below; fail before schema/password writes.
root_trust_helper=/usr/local/libexec/mooncen-crawler-control-root-trust
if [ ! -f "$root_trust_helper" ] || [ -L "$root_trust_helper" ] || \
   [ "$(stat -c '%U:%G:%a:%h' "$root_trust_helper")" != root:root:755:1 ]; then
  echo "The independently bootstrapped crawler-control root trust helper is unavailable or unsafe." >&2
  exit 78
fi
"$root_trust_helper" verify-receipt \
  --release-id "$release_id" \
  --expected-commit "$deploy_commit" \
  --expected-archive-sha256 "$deploy_archive_sha256" \
  --expected-tree-sha256 "$deploy_tree_sha256" \
  --nonce "$backup_receipt_nonce" \
  --receipt "$backup_receipt" \
  --receipt-signature "$backup_receipt_signature"

"$PYTHON" -X utf8 -m tools.bootstrap_crawler_credential_registry \
  --schema-env "$schema_env" \
  --confirm-staging-database "$confirmed_database" \
  --audit-only

# A database containing both the base staging snapshots and the confirmed name
# must pass a no-write dry-run before any role or schema change is attempted.
"$PYTHON" -X utf8 -m tools.ensure_crawler_control_schema \
  --env-file "$schema_env" \
  --confirm-staging-database "$confirmed_database" \
  --dry-run

# Close the config-management/manual-start race immediately before the first
# schema/role write. The same assertion is repeated before service activation.
assert_new_control_units_quiescent || exit 70

# This fixed-helper command is the final gate immediately before the first DB
# write. It currently exits NOT READY after read-only verification. A future
# reviewed implementation must consume the unique receipt and start the schema
# convergence under one PostgreSQL advisory/audit transaction.
"$root_trust_helper" consume-receipt \
  --release-id "$release_id" \
  --expected-commit "$deploy_commit" \
  --expected-archive-sha256 "$deploy_archive_sha256" \
  --expected-tree-sha256 "$deploy_tree_sha256" \
  --nonce "$backup_receipt_nonce" \
  --receipt "$backup_receipt" \
  --receipt-signature "$backup_receipt_signature"

# Safe order: base check (above), role bootstrap, migration, staging guards,
# and a final grants convergence.  The Python installer holds a DB advisory
# lock and records the immutable migration checksum atomically.
"$PYTHON" -X utf8 -m tools.ensure_crawler_control_schema \
  --env-file "$schema_env" \
  --confirm-staging-database "$confirmed_database" \
  --apply \
  --install-receipt "$backup_receipt" \
  --install-receipt-signature "$backup_receipt_signature" \
  --install-receipt-nonce "$backup_receipt_nonce" \
  --release-id "$release_id" \
  --expected-commit "$deploy_commit" \
  --expected-archive-sha256 "$deploy_archive_sha256" \
  --expected-tree-sha256 "$deploy_tree_sha256"

# Runtime logins are converged only after the NOLOGIN permission groups exist.
# Passwords never appear in process arguments and are converted to SCRAM
# verifiers client-side before ALTER ROLE reaches PostgreSQL.
"$PYTHON" -X utf8 -m tools.provision_crawler_service_login \
  --schema-env "$schema_env" \
  --service-env "$scheduler_env" \
  --component control \
  --confirm-staging-database "$confirmed_database"
"$PYTHON" -X utf8 -m tools.provision_crawler_service_login \
  --schema-env "$schema_env" \
  --service-env "$publisher_env" \
  --component publisher \
  --confirm-staging-database "$confirmed_database"
"$PYTHON" -X utf8 -m tools.provision_crawler_service_login \
  --schema-env "$schema_env" \
  --service-env "$finalizer_env" \
  --component finalizer \
  --confirm-staging-database "$confirmed_database"
"$PYTHON" -X utf8 -m tools.provision_crawler_service_login \
  --schema-env "$schema_env" \
  --service-env "$approver_env" \
  --component approver \
  --confirm-staging-database "$confirmed_database"
"$PYTHON" -X utf8 -m tools.provision_crawler_service_login \
  --schema-env "$schema_env" \
  --service-env "$release_approver_env" \
  --component release_approver \
  --confirm-staging-database "$confirmed_database"
"$PYTHON" -X utf8 -m tools.provision_crawler_service_login \
  --schema-env "$schema_env" \
  --service-env "$release_admin_env" \
  --component release_admin \
  --confirm-staging-database "$confirmed_database"
"$PYTHON" -X utf8 -m tools.provision_crawler_service_login \
  --schema-env "$schema_env" \
  --service-env "$metrics_env" \
  --component observer \
  --confirm-staging-database "$confirmed_database"

ensure_service_account() {
  local account="$1"
  local account_gid actual_groups expected_groups foreign_primary foreign_supplementary
  local system_uid_max account_uid password_status
  local supplementary_groups
  system_uid_max="$(awk '$1 == "SYS_UID_MAX" { value=$2 } END { print value }' /etc/login.defs)"
  system_uid_max="${system_uid_max:-999}"
  if ! getent group "$account" >/dev/null; then
    groupadd --system "$account"
  fi
  supplementary_groups=mooncen
  if getent group mooncen-db-tls >/dev/null; then
    supplementary_groups=mooncen,mooncen-db-tls
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
      echo "Refusing to repurpose a non-system UID as a crawler service account: $account" >&2
      exit 78
    fi
    usermod \
      --gid "$account" \
      --groups "$supplementary_groups" \
      --home /nonexistent \
      --shell /usr/sbin/nologin \
      "$account"
  fi
  # Keep non-interactive service accounts password-locked without marking the
  # account expired: runuser/systemd preflight must still be able to assume the
  # UID through the local service manager.
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
    echo "Dedicated secret group has an unexpected member: $account" >&2
    exit 78
  fi
  actual_groups="$(id -nG "$account" | tr ' ' '\n' | sort -u)"
  expected_groups="$(printf '%s\n' "$account" mooncen ${supplementary_groups#mooncen,} | \
    awk 'NF' | sort -u)"
  if [ "$actual_groups" != "$expected_groups" ]; then
    echo "Dedicated service account has unexpected supplementary groups: $account" >&2
    exit 78
  fi
}

if ! getent group mooncen >/dev/null; then
  groupadd --system mooncen
fi
ensure_service_account mooncen-crawler-control
ensure_service_account mooncen-crawler-publisher
ensure_service_account mooncen-crawler-finalizer
ensure_service_account mooncen-crawler-observer
install -d -o root -g root -m 0751 "$CONFIG_DIR"

install_service_environment() {
  local source="$1"
  local filename="$2"
  local group="$3"
  local mode="${4:-0640}"
  local destination="$CONFIG_DIR/$filename"
  local temporary
  if [ -e "$destination" ] || [ -L "$destination" ]; then
    if [ -L "$destination" ] || [ ! -f "$destination" ]; then
      echo "Refusing to replace unsafe environment target: $destination" >&2
      exit 78
    fi
    if ! cmp -s -- "$source" "$destination" && [ "$replace_protected_env" -ne 1 ]; then
      echo "Protected environment differs: $destination (use --replace-protected-env)." >&2
      exit 73
    fi
  fi
  temporary="$(mktemp "$CONFIG_DIR/.${filename}.XXXXXX")"
  install -o root -g "$group" -m "$mode" "$source" "$temporary"
  mv -fT -- "$temporary" "$destination"
}

install_service_environment "$scheduler_env" crawler-control-scheduler.env mooncen-crawler-control
install_service_environment "$finalizer_env" crawler-control-finalizer.env mooncen-crawler-finalizer
install_service_environment "$publisher_env" crawler-release-publisher.env mooncen-crawler-publisher
install_service_environment "$approver_env" crawler-control-approver.env root 0600
install_service_environment "$release_approver_env" crawler-release-approver.env root 0600
install_service_environment "$release_admin_env" crawler-release-admin.env root 0600
install_service_environment "$metrics_env" crawler-control-metrics.env mooncen-crawler-observer

assert_runtime_tls_access() {
  local path="$1"
  local account="$2"
  local key material mode
  for key in DB_SSLROOTCERT DB_SSLCERT DB_SSLKEY; do
    material="$(read_env_value "$key" "$path" 0)"
    if [ -z "$material" ]; then
      continue
    fi
    if [[ "$material" != /* ]] || [ ! -f "$material" ] || [ -L "$material" ]; then
      echo "$key must be an absolute regular non-symlink for $account: $material" >&2
      exit 78
    fi
    mode="$(stat -c '%a' "$material")"
    if [ "$(stat -c '%U' "$material")" != "root" ] || \
       [[ ! "$mode" =~ ^[0-7]{3,4}$ ]] || (( (8#$mode & 8#022) != 0 )); then
      echo "$key must be root-owned and not group/world writable: $material" >&2
      exit 78
    fi
    if ! runuser -u "$account" -- test -r "$material"; then
      echo "$account cannot read configured $key: $material" >&2
      exit 77
    fi
  done
}

assert_runtime_tls_access "$scheduler_env" mooncen-crawler-control
assert_runtime_tls_access "$finalizer_env" mooncen-crawler-finalizer
assert_runtime_tls_access "$publisher_env" mooncen-crawler-publisher
assert_runtime_tls_access "$approver_env" root
assert_runtime_tls_access "$release_approver_env" root
assert_runtime_tls_access "$release_admin_env" root
assert_runtime_tls_access "$metrics_env" mooncen-crawler-observer

install -d -o root -g root -m 0751 /var/lib/mooncen-crawler-control
install -d -o root -g root -m 0755 /var/lib/mooncen-crawler-control/public
install -d -o mooncen-crawler-publisher -g mooncen-crawler-publisher -m 0755 \
  /var/lib/mooncen-crawler-control/public/state
install -d -o root -g root -m 0755 \
  /var/lib/mooncen-crawler-control/public/artifacts
install -d -o root -g root -m 0700 /var/lib/mooncen-crawler-control/reviews
if [ -L /var/lib/mooncen-crawler-control/public ] || \
   [ "$(stat -c '%U:%G:%a' /var/lib/mooncen-crawler-control/public)" != "root:root:755" ] || \
   [ -L /var/lib/mooncen-crawler-control/public/state ] || \
   [ "$(stat -c '%U:%G:%a' /var/lib/mooncen-crawler-control/public/state)" != \
     "mooncen-crawler-publisher:mooncen-crawler-publisher:755" ] || \
   [ -L /var/lib/mooncen-crawler-control/public/artifacts ] || \
   [ "$(stat -c '%U:%G:%a' /var/lib/mooncen-crawler-control/public/artifacts)" != \
     "root:root:755" ]; then
  echo "Crawler release publication directories failed exact ownership validation." >&2
  exit 78
fi

central_units=(
  mooncen-crawler-control-scheduler.service
  mooncen-crawler-control-finalizer.service
  mooncen-crawler-release-action-worker.service
  mooncen-crawler-release-publisher.service
  mooncen-crawler-release-publisher.timer
  mooncen-crawler-control-metrics.service
  mooncen-crawler-control-metrics.timer
)
enabled_units=(
  mooncen-crawler-release-publisher.timer
  mooncen-crawler-control-metrics.timer
  mooncen-crawler-control-finalizer.service
  mooncen-crawler-control-scheduler.service
)
release_action_start=()
if [ "$enable_release_actions" -eq 1 ]; then
  enabled_units+=(mooncen-crawler-release-action-worker.service)
  release_action_start+=(mooncen-crawler-release-action-worker.service)
fi
for unit in "${central_units[@]}"; do
  source_unit="$APP_DIR/deploy/ubuntu/systemd/$unit"
  if [ ! -f "$source_unit" ] || [ -L "$source_unit" ]; then
    echo "Reviewed systemd unit is unavailable or unsafe: $source_unit" >&2
    exit 66
  fi
  install -o root -g root -m 0644 "$source_unit" "$SYSTEMD_DIR/$unit"
done

systemctl daemon-reload
systemd-analyze verify "${central_units[@]/#/$SYSTEMD_DIR/}"
# Rollout transitions may be activated independently of build/register.  The
# API remains fail-closed until the long-running worker emits a fresh DB-stamped
# heartbeat; the ExecStartPre check is deliberately read-only.
if [ "$enable_release_actions" -ne 1 ]; then
  systemctl disable --now mooncen-crawler-release-action-worker.service \
    >/dev/null 2>&1 || true
fi

# Exercise each exact service credential without running the scheduler,
# finalizer, or publisher.  These checks perform SELECT-only transactions.
scheduler_install_preflight=()
if [ "$enable_control_plane" -ne 1 ]; then
  scheduler_install_preflight+=(--installation-validation)
fi
runuser -u mooncen-crawler-control -- /usr/bin/env PYTHONPATH="$APP_DIR" \
  "$PYTHON" -X utf8 -m tools.preflight_distributed_crawler_control \
  --component scheduler \
  --env-file "$CONFIG_DIR/crawler-control-scheduler.env" \
  "${scheduler_install_preflight[@]}"
runuser -u mooncen-crawler-finalizer -- /usr/bin/env PYTHONPATH="$APP_DIR" \
  "$PYTHON" -X utf8 -m tools.preflight_distributed_crawler_control \
  --component finalizer --env-file "$CONFIG_DIR/crawler-control-finalizer.env"
runuser -u mooncen-crawler-publisher -- /usr/bin/env PYTHONPATH="$APP_DIR" \
  "$PYTHON" -X utf8 -m tools.preflight_distributed_crawler_control \
  --component publisher --env-file "$CONFIG_DIR/crawler-release-publisher.env"
"$PYTHON" -X utf8 -m tools.preflight_distributed_crawler_control \
  --component approver --env-file "$CONFIG_DIR/crawler-control-approver.env"
"$PYTHON" -X utf8 -m tools.preflight_distributed_crawler_control \
  --component release_approver \
  --env-file "$CONFIG_DIR/crawler-release-approver.env"
"$PYTHON" -X utf8 -m tools.preflight_distributed_crawler_control \
  --component release_admin --env-file "$CONFIG_DIR/crawler-release-admin.env"
runuser -u mooncen-crawler-observer -- /usr/bin/env PYTHONPATH="$APP_DIR" \
  "$PYTHON" -X utf8 -m tools.preflight_distributed_crawler_control \
  --component observer --env-file "$CONFIG_DIR/crawler-control-metrics.env"

observer_metric=/var/lib/mooncen-crawler-observer/mooncen_crawler_control.prom
node_textfile_dir=/var/lib/node_exporter/textfile_collector
node_metric_link="$node_textfile_dir/mooncen_crawler_control.prom"
if [ ! -e "$node_textfile_dir" ] && [ ! -L "$node_textfile_dir" ]; then
  install -d -o root -g root -m 0755 "$node_textfile_dir"
fi
if [ ! -d "$node_textfile_dir" ] || [ -L "$node_textfile_dir" ] || \
   [ "$(stat -c '%U:%G:%a' "$node_textfile_dir")" != "root:root:755" ]; then
  echo "node_exporter textfile directory must remain a root:root 0755 regular directory." >&2
  exit 78
fi
systemctl start mooncen-crawler-control-metrics.service
if [ ! -f "$observer_metric" ] || [ -L "$observer_metric" ] || \
   [ "$(stat -c '%U:%G' "$observer_metric")" != \
     "mooncen-crawler-observer:mooncen-crawler-observer" ]; then
  echo "Observer did not create its protected StateDirectory metric file." >&2
  exit 70
fi
if [ -e "$node_metric_link" ] || [ -L "$node_metric_link" ]; then
  if [ ! -L "$node_metric_link" ] || \
     [ "$(readlink -- "$node_metric_link")" != "$observer_metric" ] || \
     [ "$(stat -c '%U:%G' "$node_metric_link")" != "root:root" ]; then
    echo "Refusing to replace an unexpected node_exporter metric path." >&2
    exit 78
  fi
else
  ln -s -- "$observer_metric" "$node_metric_link"
fi
chown -h root:root "$node_metric_link"
if [ "$(stat -c '%U:%G' "$node_metric_link")" != "root:root" ]; then
  echo "node_exporter metric symlink must be root-owned." >&2
  exit 78
fi

if [ "$enable_control_plane" -eq 1 ]; then
  assert_new_control_units_quiescent || exit 70
  cutover_timer_enabled="$(systemctl is-enabled mooncen-crawler.timer 2>/dev/null || true)"
  cutover_timer_active="$(systemctl is-active mooncen-crawler.timer 2>/dev/null || true)"
  cutover_service_enabled="$(systemctl is-enabled mooncen-crawler.service 2>/dev/null || true)"
  cutover_service_active="$(systemctl is-active mooncen-crawler.service 2>/dev/null || true)"
  cutover_once_active="$(systemctl is-active mooncen-crawler-once.service 2>/dev/null || true)"
  cutover_apply_enabled="$(systemctl is-enabled mooncen-staging-apply.timer 2>/dev/null || true)"
  cutover_apply_active="$(systemctl is-active mooncen-staging-apply.timer 2>/dev/null || true)"
  if unit_state_is_enabled "$cutover_timer_enabled" || \
     unit_state_is_live "$cutover_timer_active" || \
     unit_state_is_enabled "$cutover_service_enabled" || \
     unit_state_is_live "$cutover_service_active" || \
     unit_state_is_live "$cutover_once_active" || \
     ! unit_state_is_enabled "$cutover_apply_enabled" || \
     ! unit_state_is_live "$cutover_apply_active"; then
    echo "Cutover state changed before enable; no new recurring unit was started." >&2
    exit 70
  fi
  if ! systemctl enable "${enabled_units[@]}" || \
     ! systemctl start mooncen-crawler-release-publisher.service || \
      ! systemctl restart mooncen-crawler-control-finalizer.service \
          mooncen-crawler-control-scheduler.service "${release_action_start[@]}" || \
     ! systemctl start mooncen-crawler-release-publisher.timer \
         mooncen-crawler-control-metrics.timer; then
    systemctl disable --now "${enabled_units[@]}" >/dev/null 2>&1 || true
    echo "Control-plane enable failed; all new recurring units were disabled again." >&2
    exit 70
  fi
  if ! runtime_snapshot_before="$(control_plane_runtime_snapshot)"; then
    systemctl disable --now "${enabled_units[@]}" >/dev/null 2>&1 || true
    echo "Control-plane units failed their initial post-start readiness gate." >&2
    exit 70
  fi
  sleep 5
  if ! runtime_snapshot_after="$(control_plane_runtime_snapshot)" || \
     [ "$runtime_snapshot_before" != "$runtime_snapshot_after" ]; then
    systemctl disable --now "${enabled_units[@]}" >/dev/null 2>&1 || true
    echo "Control-plane units did not remain stable through the settle window." >&2
    exit 70
  fi
fi

if [ "$enable_control_plane" -ne 1 ]; then
  assert_new_control_units_quiescent || exit 70
fi

legacy_timer_enabled_after="$(systemctl is-enabled mooncen-crawler.timer 2>/dev/null || true)"
legacy_timer_active_after="$(systemctl is-active mooncen-crawler.timer 2>/dev/null || true)"
legacy_service_enabled_after="$(systemctl is-enabled mooncen-crawler.service 2>/dev/null || true)"
legacy_service_active_after="$(systemctl is-active mooncen-crawler.service 2>/dev/null || true)"
legacy_once_active_after="$(systemctl is-active mooncen-crawler-once.service 2>/dev/null || true)"
legacy_apply_enabled_after="$(systemctl is-enabled mooncen-staging-apply.timer 2>/dev/null || true)"
legacy_apply_active_after="$(systemctl is-active mooncen-staging-apply.timer 2>/dev/null || true)"
legacy_apply_service_after="$(systemctl is-active mooncen-staging-apply.service 2>/dev/null || true)"
legacy_apply_instances_after="$(
  systemctl list-units --type=service --all --no-legend --no-pager \
    'mooncen-staging-apply@*.service' 2>/dev/null || true
)"
if [ "$legacy_timer_enabled_before" != "$legacy_timer_enabled_after" ] || \
   [ "$legacy_timer_active_before" != "$legacy_timer_active_after" ] || \
   [ "$legacy_service_enabled_before" != "$legacy_service_enabled_after" ] || \
   [ "$legacy_apply_enabled_before" != "$legacy_apply_enabled_after" ] || \
   [ "$legacy_apply_active_before" != "$legacy_apply_active_after" ]; then
  if [ "$enable_control_plane" -eq 1 ]; then
    systemctl disable --now "${enabled_units[@]}" >/dev/null 2>&1 || true
  fi
  echo "Legacy crawler timer state changed unexpectedly; refusing the installation result." >&2
  exit 70
fi
if [ "$enable_control_plane" -eq 1 ] && \
   { unit_state_is_enabled "$legacy_timer_enabled_after" || \
     unit_state_is_live "$legacy_timer_active_after" || \
     unit_state_is_enabled "$legacy_service_enabled_after" || \
     unit_state_is_live "$legacy_service_active_after" || \
     unit_state_is_live "$legacy_once_active_after" || \
     ! unit_state_is_enabled "$legacy_apply_enabled_after" || \
     ! unit_state_is_live "$legacy_apply_active_after"; }; then
  systemctl disable --now "${enabled_units[@]}" >/dev/null 2>&1 || true
  echo "Cutover state drifted while new units started; all new recurring units were disabled again." >&2
  exit 70
fi

cat <<REPORT
Distributed crawler control plane installed and preflighted.
database=${schema_host}:${schema_port}/${schema_database}
runtime_environment=${runtime_environment}
legacy_timer_enabled=${legacy_timer_enabled_after:-unknown}
legacy_timer_active=${legacy_timer_active_after:-unknown}
legacy_service_enabled=${legacy_service_enabled_after:-unknown}
legacy_service_before=${legacy_service_active_before:-unknown}
legacy_service_after=${legacy_service_active_after:-unknown}
legacy_once_before=${legacy_once_active_before:-unknown}
legacy_once_after=${legacy_once_active_after:-unknown}
legacy_apply_enabled=${legacy_apply_enabled_after:-unknown}
legacy_apply_active=${legacy_apply_active_after:-unknown}
legacy_apply_service_before=${legacy_apply_service_before:-unknown}
legacy_apply_service_after=${legacy_apply_service_after:-unknown}
legacy_apply_instances_before=${legacy_apply_instances_before:-none}
legacy_apply_instances_after=${legacy_apply_instances_after:-none}
auto_promotion=${auto_promotion}
new_units_enabled=${enable_control_plane}

The legacy timer was not stopped or disabled. Follow the reviewed canary and
manual cutover procedure in docs/distributed-crawler-control-plane.md.
REPORT
