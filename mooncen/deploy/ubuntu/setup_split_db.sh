#!/usr/bin/env bash
set -euo pipefail
umask 077

APP_DIR=/opt/mooncen
CONFIG_DIR=/etc/mooncen
SECRETS_FILE="$CONFIG_DIR/db-node.env"
DB_NAME=mooncen
DB_MIGRATOR_USER=mooncen_admin
DB_API_USER=mooncen_api_login
DB_CRAWLER_USER=mooncen_crawler_login
DB_AI_USER=mooncen_ai_login
DB_APPLIER_USER=mooncen_applier_login
DB_BACKUP_USER=mooncen_backup_login
DB_CHECK_USER=mooncen_check_login

bind_address=""
web_address=""
crawler_address=""
server_name="gen1db"
server_fqdn=""
restore_dump=""

usage() {
  cat >&2 <<'EOF'
Usage: setup_split_db.sh \
  --bind-address IP \
  --web-address IP/CIDR \
  [--crawler-address IP/CIDR] \
  [--server-name HOST] \
  [--server-fqdn HOST] \
  [--restore-dump PATH]
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --bind-address)
      bind_address="${2:-}"
      shift 2
      ;;
    --web-address)
      web_address="${2:-}"
      shift 2
      ;;
    --crawler-address)
      crawler_address="${2:-}"
      shift 2
      ;;
    --server-name)
      server_name="${2:-}"
      shift 2
      ;;
    --server-fqdn)
      server_fqdn="${2:-}"
      shift 2
      ;;
    --restore-dump)
      restore_dump="${2:-}"
      shift 2
      ;;
    *)
      usage
      exit 64
      ;;
  esac
done

if [ "$(id -u)" -ne 0 ]; then
  echo "Run setup_split_db.sh through sudo." >&2
  exit 77
fi
if [ ! -f "$APP_DIR/DB/setup_db.py" ] || [ -L "$APP_DIR" ]; then
  echo "A regular MoonCen release must exist at $APP_DIR." >&2
  exit 66
fi
if [ -z "$bind_address" ] || [ -z "$web_address" ]; then
  usage
  exit 64
fi

python3 -I - "$bind_address" "$web_address" "$crawler_address" <<'PY'
import ipaddress
import sys

ipaddress.ip_address(sys.argv[1])
for label, value in (("web-address", sys.argv[2]), ("crawler-address", sys.argv[3])):
    if not value:
        continue
    network = ipaddress.ip_network(value, strict=False)
    if network.num_addresses != 1:
        raise SystemExit(f"{label} must identify exactly one host")
PY

for hostname in "$server_name" "$server_fqdn"; do
  [ -z "$hostname" ] && continue
  if [[ ! "$hostname" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$ ]]; then
    echo "Invalid database certificate hostname: $hostname" >&2
    exit 64
  fi
done
if [ -n "$restore_dump" ] && { [ ! -f "$restore_dump" ] || [ -L "$restore_dump" ]; }; then
  echo "Restore dump is unavailable or unsafe: $restore_dump" >&2
  exit 66
fi

install -d -o root -g root -m 0750 "$CONFIG_DIR"
if [ ! -f "$SECRETS_FILE" ]; then
  secret_tmp="$(mktemp "$CONFIG_DIR/.db-node.env.XXXXXX")"
  {
    printf 'DB_PASSWORD=%s\n' "$(openssl rand -hex 32)"
    printf 'DB_API_PASSWORD=%s\n' "$(openssl rand -hex 32)"
    printf 'DB_CRAWLER_PASSWORD=%s\n' "$(openssl rand -hex 32)"
    printf 'DB_AI_PASSWORD=%s\n' "$(openssl rand -hex 32)"
    printf 'DB_APPLIER_PASSWORD=%s\n' "$(openssl rand -hex 32)"
    printf 'DB_BACKUP_PASSWORD=%s\n' "$(openssl rand -hex 32)"
    printf 'DB_CHECK_PASSWORD=%s\n' "$(openssl rand -hex 32)"
  } >"$secret_tmp"
  chown root:root "$secret_tmp"
  chmod 0600 "$secret_tmp"
  mv -f "$secret_tmp" "$SECRETS_FILE"
fi
if [ -L "$SECRETS_FILE" ] || [ "$(stat -c '%U:%G:%a' "$SECRETS_FILE")" != "root:root:600" ]; then
  echo "DB node secrets must be root:root mode 0600." >&2
  exit 78
fi

set -a
# shellcheck disable=SC1090
source "$SECRETS_FILE"
set +a
for secret_name in \
  DB_PASSWORD DB_API_PASSWORD DB_CRAWLER_PASSWORD DB_AI_PASSWORD \
  DB_APPLIER_PASSWORD DB_BACKUP_PASSWORD DB_CHECK_PASSWORD; do
  secret_value="${!secret_name:-}"
  if [[ ! "$secret_value" =~ ^[0-9a-f]{64}$ ]]; then
    echo "Invalid generated database secret: $secret_name" >&2
    exit 78
  fi
done

pg_version="$(pg_lsclusters --no-header | awk '$2 == "main" {print $1; exit}')"
if [[ ! "$pg_version" =~ ^[0-9]+$ ]]; then
  echo "Unable to find the PostgreSQL main cluster." >&2
  exit 69
fi
pg_config_dir="/etc/postgresql/$pg_version/main"
pg_hba="$pg_config_dir/pg_hba.conf"
pg_conf_dir="$pg_config_dir/conf.d"
install -d -o postgres -g postgres -m 0750 "$pg_conf_dir"

ca_key="$CONFIG_DIR/db-ca.key"
ca_cert="$CONFIG_DIR/db-root-ca.crt"
server_key="$pg_config_dir/mooncen-server.key"
server_cert="$pg_config_dir/mooncen-server.crt"
if [ ! -f "$ca_key" ] || [ ! -f "$ca_cert" ] || [ ! -f "$server_key" ] || [ ! -f "$server_cert" ]; then
  cert_dir="$(mktemp -d)"
  trap 'rm -rf -- "$cert_dir"' EXIT HUP INT TERM
  openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out "$cert_dir/ca.key"
  openssl req -x509 -new -sha256 -days 3650 \
    -key "$cert_dir/ca.key" \
    -subj "/CN=MoonCen Gen1 Database CA" \
    -out "$cert_dir/ca.crt"
  openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out "$cert_dir/server.key"
  {
    printf 'subjectAltName=DNS:%s,IP:%s' "$server_name" "$bind_address"
    if [ -n "$server_fqdn" ]; then
      printf ',DNS:%s' "$server_fqdn"
    fi
    printf '\nextendedKeyUsage=serverAuth\n'
    printf 'keyUsage=digitalSignature,keyEncipherment\n'
  } >"$cert_dir/server.ext"
  openssl req -new -sha256 \
    -key "$cert_dir/server.key" \
    -subj "/CN=$server_name" \
    -out "$cert_dir/server.csr"
  openssl x509 -req -sha256 -days 825 \
    -in "$cert_dir/server.csr" \
    -CA "$cert_dir/ca.crt" \
    -CAkey "$cert_dir/ca.key" \
    -CAcreateserial \
    -extfile "$cert_dir/server.ext" \
    -out "$cert_dir/server.crt"
  install -o root -g root -m 0600 "$cert_dir/ca.key" "$ca_key"
  install -o root -g root -m 0644 "$cert_dir/ca.crt" "$ca_cert"
  install -o postgres -g postgres -m 0600 "$cert_dir/server.key" "$server_key"
  install -o postgres -g postgres -m 0644 "$cert_dir/server.crt" "$server_cert"
  rm -rf -- "$cert_dir"
  trap - EXIT HUP INT TERM
fi

cat >"$pg_conf_dir/90-mooncen-split.conf" <<EOF
listen_addresses = '127.0.0.1,$bind_address'
ssl = on
ssl_cert_file = '$server_cert'
ssl_key_file = '$server_key'
password_encryption = 'scram-sha-256'
max_connections = 150
shared_buffers = '2GB'
effective_cache_size = '5GB'
maintenance_work_mem = '512MB'
work_mem = '8MB'
wal_compression = on
EOF
chown postgres:postgres "$pg_conf_dir/90-mooncen-split.conf"
chmod 0640 "$pg_conf_dir/90-mooncen-split.conf"

sed -i '/^# BEGIN MOONCEN SPLIT ACCESS$/,/^# END MOONCEN SPLIT ACCESS$/d' "$pg_hba"
cat >>"$pg_hba" <<EOF
# BEGIN MOONCEN SPLIT ACCESS
hostssl $DB_NAME $DB_API_USER $web_address scram-sha-256
EOF
if [ -n "$crawler_address" ]; then
  cat >>"$pg_hba" <<EOF
hostssl $DB_NAME $DB_APPLIER_USER $crawler_address scram-sha-256
hostssl $DB_NAME $DB_CHECK_USER $crawler_address scram-sha-256
EOF
fi
cat >>"$pg_hba" <<EOF
# END MOONCEN SPLIT ACCESS
EOF
chown postgres:postgres "$pg_hba"
chmod 0640 "$pg_hba"

systemctl restart postgresql
pg_isready -q

db_password_b64="$(printf '%s' "$DB_PASSWORD" | base64 | tr -d '\r\n')"
sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
SELECT format('CREATE ROLE %I LOGIN', '$DB_MIGRATOR_USER')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$DB_MIGRATOR_USER')
\gexec
SELECT format(
  'ALTER ROLE %I WITH LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
  '$DB_MIGRATOR_USER',
  convert_from(decode('$db_password_b64', 'base64'), 'UTF8')
) \gexec
SELECT format('CREATE DATABASE %I OWNER %I', '$DB_NAME', '$DB_MIGRATOR_USER')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '$DB_NAME')
\gexec
SELECT format('ALTER DATABASE %I OWNER TO %I', '$DB_NAME', '$DB_MIGRATOR_USER') \gexec
SQL

if [ -n "$restore_dump" ]; then
  existing_tables="$(sudo -u postgres psql -At -d "$DB_NAME" -c \
    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p');")"
  if [ "$existing_tables" != "0" ]; then
    echo "Refusing to restore into a non-empty MoonCen database." >&2
    exit 73
  fi
  restore_input="$restore_dump"
  restore_stage=""
  if ! sudo -u postgres test -r "$restore_input"; then
    restore_stage="/var/lib/postgresql/.mooncen-restore.$$.dump"
    install -o postgres -g postgres -m 0600 "$restore_input" "$restore_stage"
    restore_input="$restore_stage"
  fi
  cleanup_restore_stage() {
    if [ -n "$restore_stage" ]; then
      rm -f -- "$restore_stage"
    fi
  }
  trap cleanup_restore_stage EXIT HUP INT TERM
  sudo -u postgres pg_restore \
    --exit-on-error \
    --clean \
    --if-exists \
    --no-owner \
    --no-privileges \
    --dbname "$DB_NAME" \
    "$restore_input"
  cleanup_restore_stage
  trap - EXIT HUP INT TERM
fi

sudo -u postgres psql -v ON_ERROR_STOP=1 -d "$DB_NAME" <<SQL
CREATE EXTENSION IF NOT EXISTS postgis WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;
ALTER SCHEMA public OWNER TO "$DB_MIGRATOR_USER";
GRANT USAGE, CREATE ON SCHEMA public TO "$DB_MIGRATOR_USER";
DO \$\$
DECLARE
  item record;
BEGIN
  IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'crawl_staging') THEN
    EXECUTE format('ALTER SCHEMA crawl_staging OWNER TO %I', '$DB_MIGRATOR_USER');
  END IF;
  FOR item IN
    SELECT n.nspname AS schema_name, c.relname AS object_name, c.relkind
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname IN ('public', 'crawl_staging')
      AND c.relkind IN ('r', 'p', 'v', 'm', 'S')
      AND NOT EXISTS (
        SELECT 1 FROM pg_depend d
        WHERE d.classid = 'pg_class'::regclass
          AND d.objid = c.oid
          AND (
            d.deptype = 'e'
            OR (c.relkind = 'S' AND d.deptype IN ('a', 'i'))
          )
      )
  LOOP
    CASE item.relkind
      WHEN 'S' THEN EXECUTE format('ALTER SEQUENCE %I.%I OWNER TO %I', item.schema_name, item.object_name, '$DB_MIGRATOR_USER');
      WHEN 'v' THEN EXECUTE format('ALTER VIEW %I.%I OWNER TO %I', item.schema_name, item.object_name, '$DB_MIGRATOR_USER');
      WHEN 'm' THEN EXECUTE format('ALTER MATERIALIZED VIEW %I.%I OWNER TO %I', item.schema_name, item.object_name, '$DB_MIGRATOR_USER');
      ELSE EXECUTE format('ALTER TABLE %I.%I OWNER TO %I', item.schema_name, item.object_name, '$DB_MIGRATOR_USER');
    END CASE;
  END LOOP;
  FOR item IN
    SELECT n.nspname AS schema_name, p.proname, p.prokind,
           pg_get_function_identity_arguments(p.oid) AS args
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname IN ('public', 'crawl_staging')
      AND p.prokind IN ('f', 'p')
      AND NOT EXISTS (
        SELECT 1 FROM pg_depend d
        WHERE d.classid = 'pg_proc'::regclass
          AND d.objid = p.oid
          AND d.deptype = 'e'
      )
  LOOP
    IF item.prokind = 'p' THEN
      EXECUTE format('ALTER PROCEDURE %I.%I(%s) OWNER TO %I', item.schema_name, item.proname, item.args, '$DB_MIGRATOR_USER');
    ELSE
      EXECUTE format('ALTER FUNCTION %I.%I(%s) OWNER TO %I', item.schema_name, item.proname, item.args, '$DB_MIGRATOR_USER');
    END IF;
  END LOOP;
END \$\$;
SQL

ENVIRONMENT=production \
DB_USE_MIGRATOR=1 \
DB_HOST=127.0.0.1 \
DB_PORT=5432 \
DB_NAME="$DB_NAME" \
DB_USER="$DB_MIGRATOR_USER" \
DB_PASSWORD="$DB_PASSWORD" \
  /usr/bin/python3 "$APP_DIR/DB/setup_db.py" --mode migrate

sudo -u postgres psql -v ON_ERROR_STOP=1 -d "$DB_NAME" <"$APP_DIR/DB/roles.sql"

db_api_password_b64="$(printf '%s' "$DB_API_PASSWORD" | base64 | tr -d '\r\n')"
db_crawler_password_b64="$(printf '%s' "$DB_CRAWLER_PASSWORD" | base64 | tr -d '\r\n')"
db_ai_password_b64="$(printf '%s' "$DB_AI_PASSWORD" | base64 | tr -d '\r\n')"
db_applier_password_b64="$(printf '%s' "$DB_APPLIER_PASSWORD" | base64 | tr -d '\r\n')"
db_backup_password_b64="$(printf '%s' "$DB_BACKUP_PASSWORD" | base64 | tr -d '\r\n')"
db_check_password_b64="$(printf '%s' "$DB_CHECK_PASSWORD" | base64 | tr -d '\r\n')"
{
  printf '\\set db_api_user %s\n' "$DB_API_USER"
  printf '\\set db_api_password_b64 %s\n' "$db_api_password_b64"
  printf '\\set db_crawler_user %s\n' "$DB_CRAWLER_USER"
  printf '\\set db_crawler_password_b64 %s\n' "$db_crawler_password_b64"
  printf '\\set db_ai_user %s\n' "$DB_AI_USER"
  printf '\\set db_ai_password_b64 %s\n' "$db_ai_password_b64"
  printf '\\set db_applier_user %s\n' "$DB_APPLIER_USER"
  printf '\\set db_applier_password_b64 %s\n' "$db_applier_password_b64"
  printf '\\set db_backup_user %s\n' "$DB_BACKUP_USER"
  printf '\\set db_backup_password_b64 %s\n' "$db_backup_password_b64"
  printf '\\set db_check_user %s\n' "$DB_CHECK_USER"
  printf '\\set db_check_password_b64 %s\n' "$db_check_password_b64"
  cat "$APP_DIR/DB/provision_login_roles.sql"
} | sudo -u postgres psql -v ON_ERROR_STOP=1 -d "$DB_NAME"

sudo -u postgres psql -v ON_ERROR_STOP=1 -d "$DB_NAME" <<SQL
ALTER DEFAULT PRIVILEGES FOR ROLE "$DB_MIGRATOR_USER" IN SCHEMA public
  GRANT SELECT ON TABLES TO mooncen_readonly;
ALTER DEFAULT PRIVILEGES FOR ROLE "$DB_MIGRATOR_USER" IN SCHEMA public
  GRANT SELECT ON SEQUENCES TO mooncen_readonly;
SQL

client_env="$CONFIG_DIR/db-client-api.env"
cat >"$client_env" <<EOF
DB_HOST=$server_name
DB_PORT=5432
DB_NAME=$DB_NAME
DB_OWNER_USER=$DB_MIGRATOR_USER
DB_API_USER=$DB_API_USER
DB_API_PASSWORD=$DB_API_PASSWORD
DB_SSLMODE=verify-full
EOF
chown root:root "$client_env"
chmod 0600 "$client_env"

if [ -n "$crawler_address" ]; then
  crawler_client_env="$CONFIG_DIR/db-client-crawler.env"
  cat >"$crawler_client_env" <<EOF
DB_HOST=$server_name
DB_PORT=5432
DB_NAME=$DB_NAME
DB_APPLIER_USER=$DB_APPLIER_USER
DB_APPLIER_PASSWORD=$DB_APPLIER_PASSWORD
DB_CHECK_USER=$DB_CHECK_USER
DB_CHECK_PASSWORD=$DB_CHECK_PASSWORD
DB_SSLMODE=verify-full
EOF
  chown root:root "$crawler_client_env"
  chmod 0600 "$crawler_client_env"
fi

openssl verify -CAfile "$ca_cert" "$server_cert"
sudo -u postgres psql -At -d "$DB_NAME" -c \
  "SELECT 'courses=' || count(*) FROM courses; SELECT 'branches=' || count(*) FROM branches;"
ss -ltn | grep -F "$bind_address:5432" >/dev/null
echo "MoonCen split DB node is ready."
