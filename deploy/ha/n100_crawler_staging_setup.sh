#!/usr/bin/env bash
set -euo pipefail
umask 077

APP_DIR="${APP_DIR:-/opt/mooncen}"
STAGING_DB_NAME="${STAGING_DB_NAME:-mooncen_staging}"
STAGING_DB_OWNER_USER="${STAGING_DB_OWNER_USER:-mooncen_staging_owner}"
STAGING_DB_USER="${STAGING_DB_USER:-}"
STAGING_DB_PORT="${STAGING_DB_PORT:-55432}"
STAGING_CLUSTER_VERSION="${STAGING_CLUSTER_VERSION:-16}"
STAGING_CLUSTER_NAME="${STAGING_CLUSTER_NAME:-mooncen_staging}"
USE_DEDICATED_STAGING_CLUSTER="${USE_DEDICATED_STAGING_CLUSTER:-0}"
PRIMARY_DB_HOST="${PRIMARY_DB_HOST:-}"
PRIMARY_DB_PORT="${PRIMARY_DB_PORT:-}"
PRIMARY_DB_NAME="${PRIMARY_DB_NAME:-}"
PRIMARY_DB_USER="${PRIMARY_DB_USER:-}"
PRIMARY_DB_PASSWORD="${PRIMARY_DB_PASSWORD:-}"
DB_SSLROOTCERT="${DB_SSLROOTCERT:-}"
APPLIER_ENV_FILE=/etc/mooncen/applier.env
CRAWLER_ENV_FILE=/etc/mooncen/crawler.env

cd "$APP_DIR"

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  echo "Crawler staging setup must run as root." >&2
  exit 77
fi

validate_protected_env_file() {
  local file="$1"
  local owner
  local mode

  if [ ! -f "$file" ] || [ -L "$file" ]; then
    echo "Protected environment file must be a regular file, not a symlink: $file" >&2
    exit 78
  fi
  owner="$(stat -c '%U' "$file")"
  mode="$(stat -c '%a' "$file")"
  if [ "$owner" != "root" ] || [[ ! "$mode" =~ ^[0-7]{3,4}$ ]] || (( (8#$mode & 8#022) != 0 )); then
    echo "Protected environment file must be root-owned and not group/world-writable: $file" >&2
    exit 78
  fi
}

read_protected_env_value() {
  local file="$1"
  local key="$2"

  case "$file:$key" in
    "$APPLIER_ENV_FILE:PRIMARY_DB_HOST" | \
    "$APPLIER_ENV_FILE:PRIMARY_DB_PORT" | \
    "$APPLIER_ENV_FILE:PRIMARY_DB_NAME" | \
    "$APPLIER_ENV_FILE:PRIMARY_DB_USER" | \
    "$APPLIER_ENV_FILE:PRIMARY_DB_PASSWORD" | \
    "$APPLIER_ENV_FILE:CRAWL_STAGING_DB_USER" | \
    "$APPLIER_ENV_FILE:CRAWL_STAGING_DB_PASSWORD" | \
    "$APPLIER_ENV_FILE:DB_SSLROOTCERT" | \
    "$CRAWLER_ENV_FILE:CRAWL_STAGING_DB_USER" | \
    "$CRAWLER_ENV_FILE:CRAWL_STAGING_DB_PASSWORD" | \
    "$CRAWLER_ENV_FILE:DB_SSLROOTCERT")
      ;;
    *)
      echo "Refusing unsupported protected environment key: $key" >&2
      exit 64
      ;;
  esac

  awk -v wanted="$key" '
    index($0, wanted "=") == 1 {
      count += 1
      value = substr($0, length(wanted) + 2)
    }
    END {
      if (count > 1) {
        print "Duplicate protected environment key: " wanted > "/dev/stderr"
        exit 65
      }
      if (count == 1) {
        printf "%s", value
      }
    }
  ' "$file"
}

validate_protected_env_file "$APPLIER_ENV_FILE"
validate_protected_env_file "$CRAWLER_ENV_FILE"

if [ -z "$PRIMARY_DB_HOST" ]; then PRIMARY_DB_HOST="$(read_protected_env_value "$APPLIER_ENV_FILE" PRIMARY_DB_HOST)"; fi
if [ -z "$PRIMARY_DB_PORT" ]; then PRIMARY_DB_PORT="$(read_protected_env_value "$APPLIER_ENV_FILE" PRIMARY_DB_PORT)"; fi
if [ -z "$PRIMARY_DB_NAME" ]; then PRIMARY_DB_NAME="$(read_protected_env_value "$APPLIER_ENV_FILE" PRIMARY_DB_NAME)"; fi
PRIMARY_DB_HOST="${PRIMARY_DB_HOST:-cloud}"
PRIMARY_DB_PORT="${PRIMARY_DB_PORT:-5432}"
PRIMARY_DB_NAME="${PRIMARY_DB_NAME:-mooncen}"

if [ -z "$PRIMARY_DB_USER" ]; then
  PRIMARY_DB_USER="$(read_protected_env_value "$APPLIER_ENV_FILE" PRIMARY_DB_USER)"
fi
PRIMARY_DB_USER="${PRIMARY_DB_USER:-mooncen_applier_login}"
if [ -z "$PRIMARY_DB_PASSWORD" ]; then
  PRIMARY_DB_PASSWORD="$(read_protected_env_value "$APPLIER_ENV_FILE" PRIMARY_DB_PASSWORD)"
fi
if [ -z "$PRIMARY_DB_PASSWORD" ]; then
  echo "PRIMARY_DB_PASSWORD is required for staging-to-primary apply." >&2
  exit 64
fi

if [ -z "$STAGING_DB_USER" ]; then
  STAGING_DB_USER="$(read_protected_env_value "$CRAWLER_ENV_FILE" CRAWL_STAGING_DB_USER)"
fi
if [ -z "$STAGING_DB_USER" ]; then
  STAGING_DB_USER="$(read_protected_env_value "$APPLIER_ENV_FILE" CRAWL_STAGING_DB_USER)"
fi
STAGING_DB_USER="${STAGING_DB_USER:-mooncen_crawler_login}"

STAGING_DB_PASSWORD="${CRAWL_STAGING_DB_PASSWORD:-${DB_CRAWLER_PASSWORD:-}}"
if [ -z "$STAGING_DB_PASSWORD" ]; then
  STAGING_DB_PASSWORD="$(read_protected_env_value "$CRAWLER_ENV_FILE" CRAWL_STAGING_DB_PASSWORD)"
fi
if [ -z "$STAGING_DB_PASSWORD" ]; then
  STAGING_DB_PASSWORD="$(read_protected_env_value "$APPLIER_ENV_FILE" CRAWL_STAGING_DB_PASSWORD)"
fi
if [ -z "$STAGING_DB_PASSWORD" ]; then
  echo "CRAWL_STAGING_DB_PASSWORD or DB_CRAWLER_PASSWORD is required for staging runtime." >&2
  exit 64
fi
if [ -z "$DB_SSLROOTCERT" ]; then
  DB_SSLROOTCERT="$(read_protected_env_value "$APPLIER_ENV_FILE" DB_SSLROOTCERT)"
fi
if [ -z "$DB_SSLROOTCERT" ]; then
  DB_SSLROOTCERT="$(read_protected_env_value "$CRAWLER_ENV_FILE" DB_SSLROOTCERT)"
fi
if [[ "$DB_SSLROOTCERT" != /* ]] || [[ ! "$DB_SSLROOTCERT" =~ ^[A-Za-z0-9._/-]+$ ]] || \
   [ ! -f "$DB_SSLROOTCERT" ] || [ -L "$DB_SSLROOTCERT" ]; then
  echo "DB_SSLROOTCERT must be an existing absolute regular file, not a symlink." >&2
  exit 78
fi
db_ca_owner="$(stat -c '%U' "$DB_SSLROOTCERT")"
db_ca_mode="$(stat -c '%a' "$DB_SSLROOTCERT")"
if [ "$db_ca_owner" != "root" ] || [[ ! "$db_ca_mode" =~ ^[0-7]{3,4}$ ]] || \
   (( (8#$db_ca_mode & 8#022) != 0 )); then
  echo "DB_SSLROOTCERT must be root-owned and not group/world-writable." >&2
  exit 78
fi

for identifier in "$STAGING_DB_NAME" "$STAGING_DB_OWNER_USER" "$STAGING_DB_USER" "$PRIMARY_DB_NAME" "$PRIMARY_DB_USER"; do
  if [[ ! "$identifier" =~ ^[a-z_][a-z0-9_]*$ ]]; then
    echo "Invalid PostgreSQL identifier: $identifier" >&2
    exit 64
  fi
done
if [[ ! "$PRIMARY_DB_HOST" =~ ^[A-Za-z0-9._:-]+$ ]] || [[ ! "$PRIMARY_DB_PORT" =~ ^[0-9]+$ ]] || [ "$PRIMARY_DB_PORT" -lt 1 ] || [ "$PRIMARY_DB_PORT" -gt 65535 ]; then
  echo "Invalid primary database host or port." >&2
  exit 64
fi
if [ "$STAGING_DB_OWNER_USER" = "$STAGING_DB_USER" ]; then
  echo "Staging owner and crawler LOGIN roles must be distinct." >&2
  exit 64
fi
for role_name in "$STAGING_DB_OWNER_USER" "$STAGING_DB_USER"; do
  case "$role_name" in
    mooncen_api|mooncen_crawler|mooncen_applier|mooncen_readonly)
      echo "Staging LOGIN role ${role_name} must differ from the NOLOGIN permission groups." >&2
      exit 64
      ;;
  esac
done
if [ "${#STAGING_DB_PASSWORD}" -lt 16 ] || [ "${#PRIMARY_DB_PASSWORD}" -lt 16 ]; then
  echo "Staging runtime and primary database passwords must be at least 16 characters." >&2
  exit 64
fi
if [[ ! "$USE_DEDICATED_STAGING_CLUSTER" =~ ^[01]$ ]]; then
  echo "USE_DEDICATED_STAGING_CLUSTER must be 0 or 1." >&2
  exit 64
fi

LOCAL_DB_ROLE="$(sudo -n -u postgres psql -Atqc "SELECT CASE WHEN pg_is_in_recovery() THEN 'standby' ELSE 'primary' END;" 2>/dev/null || echo unknown)"
if [ "$USE_DEDICATED_STAGING_CLUSTER" = "1" ] || [ "$LOCAL_DB_ROLE" = "standby" ]; then
  if ! command -v pg_createcluster >/dev/null 2>&1; then
    echo "pg_createcluster is required to create a separate writable staging cluster."
    exit 1
  fi
  if ! pg_lsclusters | awk '{print $1, $2}' | grep -qx "${STAGING_CLUSTER_VERSION} ${STAGING_CLUSTER_NAME}"; then
    sudo -n pg_createcluster "$STAGING_CLUSTER_VERSION" "$STAGING_CLUSTER_NAME" -p "$STAGING_DB_PORT"
  fi
  sudo -n pg_ctlcluster "$STAGING_CLUSTER_VERSION" "$STAGING_CLUSTER_NAME" start || true
  PSQL_BASE=(sudo -n -u postgres psql -p "$STAGING_DB_PORT")
else
  PSQL_BASE=(sudo -n -u postgres psql)
fi

staging_runtime_password_b64="$(printf '%s' "$STAGING_DB_PASSWORD" | base64 | tr -d '\r\n')"

"${PSQL_BASE[@]}" -d postgres -v ON_ERROR_STOP=1 <<SQL
SELECT format('CREATE ROLE %I NOLOGIN', '${STAGING_DB_OWNER_USER}')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${STAGING_DB_OWNER_USER}')
\gexec
SELECT format(
  'ALTER ROLE %I WITH NOLOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD NULL',
  '${STAGING_DB_OWNER_USER}'
) \gexec
SELECT format('CREATE ROLE %I LOGIN', '${STAGING_DB_USER}')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${STAGING_DB_USER}')
\gexec
SELECT format(
  'ALTER ROLE %I WITH LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
  '${STAGING_DB_USER}',
  convert_from(decode('${staging_runtime_password_b64}', 'base64'), 'UTF8')
) \gexec
SELECT format('REVOKE %I FROM %I', parent.rolname, member.rolname)
FROM pg_auth_members membership
JOIN pg_roles parent ON parent.oid = membership.roleid
JOIN pg_roles member ON member.oid = membership.member
WHERE member.rolname IN ('${STAGING_DB_OWNER_USER}', '${STAGING_DB_USER}')
\gexec
SELECT format('CREATE DATABASE %I OWNER %I', '${STAGING_DB_NAME}', '${STAGING_DB_OWNER_USER}')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '${STAGING_DB_NAME}')
\gexec
SELECT format('ALTER DATABASE %I OWNER TO %I', '${STAGING_DB_NAME}', '${STAGING_DB_OWNER_USER}') \gexec
SQL

"${PSQL_BASE[@]}" -d "$STAGING_DB_NAME" -v ON_ERROR_STOP=1 <<SQL
CREATE EXTENSION IF NOT EXISTS postgis WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;
ALTER SCHEMA public OWNER TO "${STAGING_DB_OWNER_USER}";
DO \$\$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'crawl_staging') THEN
    EXECUTE format('ALTER SCHEMA crawl_staging OWNER TO %I', '${STAGING_DB_OWNER_USER}');
  END IF;
END \$\$;

DO \$\$
DECLARE
  item record;
BEGIN
  FOR item IN
    SELECT n.nspname AS schemaname, c.relname AS tablename
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname IN ('public', 'crawl_staging')
      AND c.relkind IN ('r', 'p')
      AND NOT EXISTS (
        SELECT 1 FROM pg_depend d
        WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e'
      )
  LOOP
    EXECUTE format('ALTER TABLE %I.%I OWNER TO %I', item.schemaname, item.tablename, '${STAGING_DB_OWNER_USER}');
  END LOOP;

  FOR item IN
    SELECT n.nspname AS schema_name, c.relname AS object_name, c.relkind
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname IN ('public', 'crawl_staging')
      AND c.relkind IN ('v', 'm')
      AND NOT EXISTS (
        SELECT 1 FROM pg_depend d
        WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e'
      )
  LOOP
    IF item.relkind = 'm' THEN
      EXECUTE format('ALTER MATERIALIZED VIEW %I.%I OWNER TO %I', item.schema_name, item.object_name, '${STAGING_DB_OWNER_USER}');
    ELSE
      EXECUTE format('ALTER VIEW %I.%I OWNER TO %I', item.schema_name, item.object_name, '${STAGING_DB_OWNER_USER}');
    END IF;
  END LOOP;

  FOR item IN
    SELECT n.nspname AS sequence_schema, c.relname AS sequence_name
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname IN ('public', 'crawl_staging')
      AND c.relkind = 'S'
      AND NOT EXISTS (
        SELECT 1 FROM pg_depend d
        WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e'
      )
  LOOP
    EXECUTE format('ALTER SEQUENCE %I.%I OWNER TO %I', item.sequence_schema, item.sequence_name, '${STAGING_DB_OWNER_USER}');
  END LOOP;

  FOR item IN
    SELECT n.nspname, p.proname, pg_get_function_identity_arguments(p.oid) AS args
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname IN ('public', 'crawl_staging')
      AND p.prokind = 'f'
      AND NOT EXISTS (
        SELECT 1 FROM pg_depend d
        WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e'
      )
  LOOP
    EXECUTE format('ALTER FUNCTION %I.%I(%s) OWNER TO %I', item.nspname, item.proname, item.args, '${STAGING_DB_OWNER_USER}');
  END LOOP;
END \$\$;
SQL

{
  printf 'SET ROLE "%s";\n' "$STAGING_DB_OWNER_USER"
  cat DB/auth_schema.sql DB/schema.sql DB/staging_schema.sql
} | "${PSQL_BASE[@]}" -d "$STAGING_DB_NAME" -v ON_ERROR_STOP=1

cat "$APP_DIR/DB/roles.sql" | \
  "${PSQL_BASE[@]}" -d "$STAGING_DB_NAME" -v ON_ERROR_STOP=1
"${PSQL_BASE[@]}" -d "$STAGING_DB_NAME" -v ON_ERROR_STOP=1 <<SQL
ALTER SCHEMA public OWNER TO "${STAGING_DB_OWNER_USER}";
ALTER SCHEMA crawl_staging OWNER TO "${STAGING_DB_OWNER_USER}";
-- Staging contains no production users. These narrow grants let the crawler's
-- duplicate-course repair preserve any synthetic association rows without
-- exposing the users table or granting auth-data access on primary.
GRANT SELECT, INSERT, DELETE ON user_favorites, user_course_marks,
  user_course_notification_settings TO mooncen_crawler;
GRANT DELETE ON course_update_requests TO mooncen_crawler;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM "${STAGING_DB_USER}";
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM "${STAGING_DB_USER}";
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA crawl_staging FROM "${STAGING_DB_USER}";
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA crawl_staging FROM "${STAGING_DB_USER}";
REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM "${STAGING_DB_USER}";
REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA crawl_staging FROM "${STAGING_DB_USER}";
REVOKE ALL PRIVILEGES ON SCHEMA public FROM "${STAGING_DB_USER}";
REVOKE ALL PRIVILEGES ON SCHEMA crawl_staging FROM "${STAGING_DB_USER}";
REVOKE ALL PRIVILEGES ON DATABASE "${STAGING_DB_NAME}" FROM "${STAGING_DB_USER}";
GRANT mooncen_crawler TO "${STAGING_DB_USER}";
GRANT CONNECT ON DATABASE "${STAGING_DB_NAME}" TO "${STAGING_DB_USER}";
SQL

if ! staging_role_contract="$("${PSQL_BASE[@]}" -d "$STAGING_DB_NAME" -Atqc "
SELECT
  has_table_privilege('${STAGING_DB_USER}', 'courses', 'SELECT')
  AND has_table_privilege('${STAGING_DB_USER}', 'courses', 'INSERT')
  AND has_table_privilege('${STAGING_DB_USER}', 'courses', 'UPDATE')
  AND has_table_privilege('${STAGING_DB_USER}', 'courses', 'DELETE')
  AND has_table_privilege('${STAGING_DB_USER}', 'crawl_batches', 'SELECT')
  AND has_table_privilege('${STAGING_DB_USER}', 'crawl_batches', 'INSERT')
  AND has_table_privilege('${STAGING_DB_USER}', 'crawl_batches', 'UPDATE')
  AND has_table_privilege('${STAGING_DB_USER}', 'crawl_progress', 'SELECT')
  AND has_table_privilege('${STAGING_DB_USER}', 'crawl_progress', 'INSERT')
  AND has_table_privilege('${STAGING_DB_USER}', 'crawl_progress', 'UPDATE')
  AND NOT has_table_privilege('${STAGING_DB_USER}', 'users', 'SELECT')
  AND NOT has_schema_privilege('${STAGING_DB_USER}', 'public', 'CREATE');
")"; then
  echo "Unable to query the staging crawler role contract." >&2
  exit 70
fi
if [ "$staging_role_contract" != "t" ]; then
  echo "Staging crawler role contract verification failed." >&2
  exit 70
fi

if ! primary_role_contract="$(PGPASSWORD="$PRIMARY_DB_PASSWORD" \
  PGSSLMODE=verify-full \
  PGSSLROOTCERT="$DB_SSLROOTCERT" \
  PGCONNECT_TIMEOUT=5 \
  psql \
  -h "$PRIMARY_DB_HOST" -p "$PRIMARY_DB_PORT" -U "$PRIMARY_DB_USER" -d "$PRIMARY_DB_NAME" \
  -At -v ON_ERROR_STOP=1 -c "
SELECT current_user = '${PRIMARY_DB_USER}'
  AND has_table_privilege(current_user, 'courses', 'SELECT')
  AND has_table_privilege(current_user, 'courses', 'INSERT')
  AND has_table_privilege(current_user, 'courses', 'UPDATE')
  AND NOT has_table_privilege(current_user, 'courses', 'DELETE')
  AND NOT has_table_privilege(current_user, 'users', 'SELECT');
")"; then
  echo "Unable to connect with the primary applier login." >&2
  exit 70
fi
if [ "$primary_role_contract" != "t" ]; then
  echo "Primary applier login or least-privilege contract verification failed." >&2
  exit 70
fi

converge_crawler_staging_env() {
  local file="$CRAWLER_ENV_FILE"
  local tmp

  validate_protected_env_file "$file"
  if ! awk -F= '
    /^[A-Z][A-Z0-9_]*=/ {
      count[$1] += 1
      if (count[$1] > 1) {
        print "Duplicate protected environment key: " $1 > "/dev/stderr"
        duplicate = 1
      }
    }
    END { exit duplicate ? 65 : 0 }
  ' "$file"; then
    exit 65
  fi

  tmp="$(mktemp /etc/mooncen/crawler.env.tmp.XXXXXX)"
  if ! awk -F= \
    -v crawl_write_mode="staging" \
    -v staging_host="localhost" \
    -v staging_port="$STAGING_DB_PORT" \
    -v staging_name="$STAGING_DB_NAME" \
    -v staging_user="$STAGING_DB_USER" '
    BEGIN {
      desired["CRAWL_WRITE_MODE"] = crawl_write_mode
      desired["CRAWL_STAGING_DB_HOST"] = staging_host
      desired["CRAWL_STAGING_DB_PORT"] = staging_port
      desired["CRAWL_STAGING_DB_NAME"] = staging_name
      desired["CRAWL_STAGING_DB_USER"] = staging_user
      order[1] = "CRAWL_WRITE_MODE"
      order[2] = "CRAWL_STAGING_DB_HOST"
      order[3] = "CRAWL_STAGING_DB_PORT"
      order[4] = "CRAWL_STAGING_DB_NAME"
      order[5] = "CRAWL_STAGING_DB_USER"
    }
    /^[A-Z][A-Z0-9_]*=/ && ($1 in desired) {
      print $1 "=" desired[$1]
      written[$1] = 1
      next
    }
    { print }
    END {
      for (position = 1; position <= 5; position += 1) {
        key = order[position]
        if (!(key in written)) {
          print key "=" desired[key]
        }
      }
    }
  ' "$file" > "$tmp"; then
    rm -f -- "$tmp"
    exit 65
  fi
  if ! chown root:mooncen-crawler "$tmp" || ! chmod 0640 "$tmp"; then
    rm -f -- "$tmp"
    exit 78
  fi
  if ! mv -fT -- "$tmp" "$file"; then
    rm -f -- "$tmp"
    exit 74
  fi
}

# The systemd drop-in and root-owned crawler EnvironmentFile must agree.
# Operations helpers read the EnvironmentFile directly for one-shot crawls, so
# converge only these non-secret routing keys and preserve every credential.
converge_crawler_staging_env

sudo -n mkdir -p \
  /etc/systemd/system/mooncen-crawler.service.d \
  /etc/systemd/system/mooncen-crawler-once.service.d \
  /etc/systemd/system/mooncen-staging-apply.service.d \
  /etc/systemd/system/mooncen-staging-apply-dry-run.service.d

sudo -n rm -f \
  /etc/systemd/system/mooncen-crawler.service.d/10-cloud-primary-db.conf \
  /etc/systemd/system/mooncen-crawler-once.service.d/10-cloud-primary-db.conf \
  /etc/systemd/system/mooncen-branch-coordinates.service.d/10-cloud-primary-db.conf 2>/dev/null || true

sudo -n tee /etc/systemd/system/mooncen-crawler.service.d/20-staging-db.conf >/dev/null <<UNIT
[Service]
Environment="CRAWL_WRITE_MODE=staging"
Environment="CRAWL_STAGING_DB_HOST=localhost"
Environment="CRAWL_STAGING_DB_PORT=${STAGING_DB_PORT}"
Environment="CRAWL_STAGING_DB_NAME=${STAGING_DB_NAME}"
Environment="CRAWL_STAGING_DB_USER=${STAGING_DB_USER}"
UNIT

sudo -n tee /etc/systemd/system/mooncen-crawler-once.service.d/20-staging-db.conf >/dev/null <<UNIT
[Service]
Environment="CRAWL_WRITE_MODE=staging"
Environment="CRAWL_STAGING_DB_HOST=localhost"
Environment="CRAWL_STAGING_DB_PORT=${STAGING_DB_PORT}"
Environment="CRAWL_STAGING_DB_NAME=${STAGING_DB_NAME}"
Environment="CRAWL_STAGING_DB_USER=${STAGING_DB_USER}"
UNIT

sudo -n tee /etc/systemd/system/mooncen-staging-apply.service.d/10-primary-db.conf >/dev/null <<UNIT
[Service]
Environment="CRAWL_STAGING_DB_HOST=localhost"
Environment="CRAWL_STAGING_DB_PORT=${STAGING_DB_PORT}"
Environment="CRAWL_STAGING_DB_NAME=${STAGING_DB_NAME}"
Environment="CRAWL_STAGING_DB_USER=${STAGING_DB_USER}"
Environment="PRIMARY_DB_HOST=${PRIMARY_DB_HOST}"
Environment="PRIMARY_DB_PORT=${PRIMARY_DB_PORT}"
Environment="PRIMARY_DB_NAME=${PRIMARY_DB_NAME}"
Environment="PRIMARY_DB_USER=${PRIMARY_DB_USER}"
UNIT

sudo -n tee /etc/systemd/system/mooncen-staging-apply-dry-run.service.d/10-primary-db.conf >/dev/null <<UNIT
[Service]
Environment="CRAWL_STAGING_DB_HOST=localhost"
Environment="CRAWL_STAGING_DB_PORT=${STAGING_DB_PORT}"
Environment="CRAWL_STAGING_DB_NAME=${STAGING_DB_NAME}"
Environment="CRAWL_STAGING_DB_USER=${STAGING_DB_USER}"
Environment="PRIMARY_DB_HOST=${PRIMARY_DB_HOST}"
Environment="PRIMARY_DB_PORT=${PRIMARY_DB_PORT}"
Environment="PRIMARY_DB_NAME=${PRIMARY_DB_NAME}"
Environment="PRIMARY_DB_USER=${PRIMARY_DB_USER}"
UNIT

sudo -n systemctl daemon-reload

cat <<REPORT
Crawler staging configured.
staging_db=localhost:${STAGING_DB_PORT}/${STAGING_DB_NAME}
primary_db=${PRIMARY_DB_HOST}:${PRIMARY_DB_PORT}/${PRIMARY_DB_NAME}

Next:
  sudo systemctl restart mooncen-crawler
  sudo systemctl start mooncen-crawler-once
  sudo systemctl start mooncen-staging-apply.service
REPORT
