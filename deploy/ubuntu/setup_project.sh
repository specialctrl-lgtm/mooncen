#!/usr/bin/env bash
set -euo pipefail
umask 077

APP_DIR="${APP_DIR:-/opt/mooncen}"
if [ "${EUID:-$(id -u)}" -eq 0 ] && [ -n "${SUDO_USER:-}" ]; then
  DEPLOY_USER="$SUDO_USER"
else
  DEPLOY_USER="$(id -un)"
fi
if [[ ! "$DEPLOY_USER" =~ ^[A-Za-z_][A-Za-z0-9_-]*$ ]] || ! id "$DEPLOY_USER" >/dev/null 2>&1; then
  echo "Unable to determine a valid deploy user." >&2
  exit 64
fi
DEPLOY_GROUP="$(id -gn "$DEPLOY_USER")"
DEPLOY_HOME="$(getent passwd "$DEPLOY_USER" | cut -d: -f6)"
if [[ ! "$DEPLOY_HOME" =~ ^/[A-Za-z0-9._/-]+$ ]] || [[ "$DEPLOY_HOME" == *".."* ]]; then
  echo "Deploy user home directory is unsafe: $DEPLOY_HOME" >&2
  exit 64
fi
APP_USER=mooncen
API_OS_USER=mooncen-api
CRAWLER_OS_USER=mooncen-crawler
AI_OS_USER=mooncen-ai
FRONTEND_OS_USER=mooncen-web
BOT_OS_USER=mooncen-bot
APPLIER_OS_USER=mooncen-applier
FUNCTIONAL_OS_USER=mooncen-check
CLOUDFLARED_OS_USER=cloudflared
DB_TLS_GROUP=mooncen-db-tls
SERVICE_CONFIG_DIR=/etc/mooncen
DB_NAME="${DB_NAME:-mooncen}"
DB_MIGRATOR_USER="${DB_MIGRATOR_USER:-${DB_USER:-mooncen_admin}}"
DB_USER="$DB_MIGRATOR_USER"
DB_PASSWORD="${DB_PASSWORD:?Set DB_PASSWORD before running this script}"
DB_API_USER="${DB_API_USER:-mooncen_api_login}"
DB_API_PASSWORD="${DB_API_PASSWORD:-}"
DB_CRAWLER_USER="${DB_CRAWLER_USER:-mooncen_crawler_login}"
DB_CRAWLER_PASSWORD="${DB_CRAWLER_PASSWORD:-}"
DB_DEPLOYMENT_WORKER_USER="${DB_DEPLOYMENT_WORKER_USER:-mooncen_deployment_worker_login}"
DB_DEPLOYMENT_WORKER_PASSWORD="${DB_DEPLOYMENT_WORKER_PASSWORD:-}"
ENABLE_CRAWLER_STAGING="${ENABLE_CRAWLER_STAGING:-1}"
CRAWL_STAGING_DB_HOST="${CRAWL_STAGING_DB_HOST:-localhost}"
CRAWL_STAGING_DB_PORT="${CRAWL_STAGING_DB_PORT:-55432}"
CRAWL_STAGING_DB_NAME="${CRAWL_STAGING_DB_NAME:-mooncen_staging}"
CRAWL_STAGING_DB_USER="${CRAWL_STAGING_DB_USER:-mooncen_staging_crawler_login}"
CRAWL_STAGING_DB_PASSWORD="${CRAWL_STAGING_DB_PASSWORD:-}"
CRAWL_STAGING_PASSWORD_FILE="${CRAWL_STAGING_PASSWORD_FILE:-/etc/mooncen/staging-crawler-password}"
DB_AI_USER="${DB_AI_USER:-mooncen_ai_login}"
DB_AI_PASSWORD="${DB_AI_PASSWORD:-}"
DB_APPLIER_USER="${DB_APPLIER_USER:-mooncen_applier_login}"
DB_APPLIER_PASSWORD="${DB_APPLIER_PASSWORD:-}"
DB_BACKUP_USER="${DB_BACKUP_USER:-mooncen_backup_login}"
DB_BACKUP_PASSWORD="${DB_BACKUP_PASSWORD:-}"
DB_CHECK_USER="${DB_CHECK_USER:-mooncen_check_login}"
DB_CHECK_PASSWORD="${DB_CHECK_PASSWORD:-}"
AUTH_SECRET="${AUTH_SECRET:?Set AUTH_SECRET before running this script}"
MOONCEN_OPS_LOGIN_ID="${MOONCEN_OPS_LOGIN_ID:?Set MOONCEN_OPS_LOGIN_ID before running this script}"
MOONCEN_OPS_PASSWORD_HASH="${MOONCEN_OPS_PASSWORD_HASH:?Set MOONCEN_OPS_PASSWORD_HASH before running this script}"
DOMAIN="${DOMAIN:-_}"
DOMAIN_ALIASES="${DOMAIN_ALIASES:-}"
KAKAO_MAPS_JAVASCRIPT_KEY="${KAKAO_MAPS_JAVASCRIPT_KEY:-}"
KAKAO_MAPS_REST_API_KEY="${KAKAO_MAPS_REST_API_KEY:-}"
GOOGLE_OAUTH_CLIENT_ID="${GOOGLE_OAUTH_CLIENT_ID:-}"
GOOGLE_OAUTH_CLIENT_SECRET="${GOOGLE_OAUTH_CLIENT_SECRET:-}"
OAUTH_REDIRECT_URI="${OAUTH_REDIRECT_URI:-https://${DOMAIN}/}"
NAVER_OAUTH_CLIENT_ID="${NAVER_OAUTH_CLIENT_ID:-}"
NAVER_OAUTH_CLIENT_SECRET="${NAVER_OAUTH_CLIENT_SECRET:-}"
OLLAMA_HOST="${OLLAMA_HOST:-http://wtr-linux:11434}"
OLLAMA_HOSTS="${OLLAMA_HOSTS:-}"
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3.5:9b}"
MOONCEN_BOT_TOKEN="${MOONCEN_BOT_TOKEN:-}"
MOONCEN_BOT_CHAT_ID="${MOONCEN_BOT_CHAT_ID:-}"
MOONCEN_ADMIN_EMAILS="${MOONCEN_ADMIN_EMAILS:-}"
MOONCEN_ADMIN_PROVIDER_IDS="${MOONCEN_ADMIN_PROVIDER_IDS:-}"
MOONCEN_BUG_REPORT_TO="${MOONCEN_BUG_REPORT_TO:-}"
MOONCEN_BUG_REPORT_FROM="${MOONCEN_BUG_REPORT_FROM:-}"
MOONCEN_SMTP_HOST="${MOONCEN_SMTP_HOST:-}"
MOONCEN_SMTP_PORT="${MOONCEN_SMTP_PORT:-587}"
MOONCEN_SMTP_USERNAME="${MOONCEN_SMTP_USERNAME:-}"
MOONCEN_SMTP_PASSWORD="${MOONCEN_SMTP_PASSWORD:-}"
MOONCEN_SMTP_SECURITY="${MOONCEN_SMTP_SECURITY:-starttls}"
OPS_CLOUDFLARE_ANALYTICS_ZONE_ID="${OPS_CLOUDFLARE_ANALYTICS_ZONE_ID:-}"
OPS_CLOUDFLARE_ANALYTICS_TOKEN="${OPS_CLOUDFLARE_ANALYTICS_TOKEN:-}"
MOONCEN_SERVER_MONITOR_TOKEN="${MOONCEN_SERVER_MONITOR_TOKEN:-}"
NODE_ROLE="${NODE_ROLE:-primary}"
SKIP_DB_SETUP="${SKIP_DB_SETUP:-0}"
PREBUILT_RELEASE="${PREBUILT_RELEASE:-0}"
DEPLOY_COMMIT="${DEPLOY_COMMIT:-unknown}"
DEPLOY_ARCHIVE_SHA256="${DEPLOY_ARCHIVE_SHA256:-unknown}"
BACKUP_AGE_RECIPIENT="${BACKUP_AGE_RECIPIENT:-}"
BACKUP_AGE_IDENTITY_FILE="${BACKUP_AGE_IDENTITY_FILE:-/etc/mooncen/backup-age-key.txt}"
BACKUP_IDENTITY_FILE="${BACKUP_IDENTITY_FILE:-/etc/mooncen/backup-ssh-key}"
BACKUP_KNOWN_HOSTS_FILE="${BACKUP_KNOWN_HOSTS_FILE:-/etc/mooncen/backup-known-hosts}"
BACKUP_MANIFEST_SIGNING_KEY="${BACKUP_MANIFEST_SIGNING_KEY:-/etc/mooncen/backup-manifest-signing-key}"
BACKUP_MANIFEST_ALLOWED_SIGNERS="${BACKUP_MANIFEST_ALLOWED_SIGNERS:-/etc/mooncen/backup-manifest-allowed-signers}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-35}"
BACKUP_MAX_ENCRYPTED_DUMP_BYTES="${BACKUP_MAX_ENCRYPTED_DUMP_BYTES:-68719476736}"
BACKUP_MAX_DECRYPTED_DUMP_BYTES="${BACKUP_MAX_DECRYPTED_DUMP_BYTES:-68719476736}"
BACKUP_LOCAL_MIN_FREE_BYTES="${BACKUP_LOCAL_MIN_FREE_BYTES:-1073741824}"
BACKUP_DB_MIN_FREE_BYTES="${BACKUP_DB_MIN_FREE_BYTES:-2147483648}"
BACKUP_RESTORE_EXPANSION_FACTOR="${BACKUP_RESTORE_EXPANSION_FACTOR:-4}"
BACKUP_MAX_AGE_SECONDS="${BACKUP_MAX_AGE_SECONDS:-604800}"
BACKUP_MAX_APP_ARCHIVE_BYTES="${BACKUP_MAX_APP_ARCHIVE_BYTES:-2147483648}"
BACKUP_MAX_CONFIG_ARCHIVE_BYTES="${BACKUP_MAX_CONFIG_ARCHIVE_BYTES:-268435456}"
BACKUP_MAX_SOURCE_DB_BYTES="${BACKUP_MAX_SOURCE_DB_BYTES:-274877906944}"
DEPLOY_SECRET_DIR="${DEPLOY_SECRET_DIR:-$DEPLOY_HOME/.config/mooncen}"
DEPLOY_SECRET_FILE="${DEPLOY_SECRET_FILE:-$DEPLOY_SECRET_DIR/deploy-secrets.env}"
ROOT_DEPLOY_SECRET_FILE=/etc/mooncen/deploy-secrets.env
BACKUP_OS_USER="${BACKUP_OS_USER:-}"
BACKUP_ENV_FILE="${BACKUP_ENV_FILE:-/etc/mooncen/backup.env}"
BACKUP_PORT="${BACKUP_PORT:-}"
DB_SSLROOTCERT_SOURCE="${DB_SSLROOTCERT:-}"

without_runtime_secrets() {
  env \
    -u DB_PASSWORD \
    -u DB_API_PASSWORD \
    -u DB_CRAWLER_PASSWORD \
    -u DB_DEPLOYMENT_WORKER_PASSWORD \
    -u DB_AI_PASSWORD \
    -u DB_APPLIER_PASSWORD \
    -u DB_BACKUP_PASSWORD \
    -u DB_CHECK_PASSWORD \
    -u PRIMARY_DB_PASSWORD \
    -u CRAWL_STAGING_DB_PASSWORD \
    -u AUTH_SECRET \
    -u MOONCEN_OPS_LOGIN_ID \
    -u MOONCEN_OPS_PASSWORD_HASH \
    -u KAKAO_MAPS_JAVASCRIPT_KEY \
    -u KAKAO_MAPS_REST_API_KEY \
    -u GOOGLE_MAPS_API_KEY \
    -u VITE_GOOGLE_MAPS_API_KEY \
    -u GOOGLE_OAUTH_CLIENT_ID \
    -u GOOGLE_OAUTH_CLIENT_SECRET \
    -u NAVER_OAUTH_CLIENT_ID \
    -u NAVER_OAUTH_CLIENT_SECRET \
    -u MOONCEN_BOT_TOKEN \
    -u MOONCEN_BOT_CHAT_ID \
    -u MOONCEN_ADMIN_EMAILS \
    -u MOONCEN_ADMIN_PROVIDER_IDS \
    -u MOONCEN_BUG_REPORT_TO \
    -u MOONCEN_BUG_REPORT_FROM \
    -u MOONCEN_SMTP_HOST \
    -u MOONCEN_SMTP_PORT \
    -u MOONCEN_SMTP_USERNAME \
    -u MOONCEN_SMTP_PASSWORD \
    -u MOONCEN_SMTP_SECURITY \
    -u OPS_CLOUDFLARE_ANALYTICS_ZONE_ID \
    -u OPS_CLOUDFLARE_ANALYTICS_TOKEN \
    -u MOONCEN_SERVER_MONITOR_TOKEN \
    "$@"
}

if [ "$NODE_ROLE" = "standby" ]; then
  SKIP_DB_SETUP=1
fi

if [ "$APP_DIR" != "/opt/mooncen" ]; then
  echo "APP_DIR must be /opt/mooncen because installed units and root helpers use that immutable path." >&2
  exit 64
fi
if [ "$BACKUP_ENV_FILE" != "/etc/mooncen/backup.env" ]; then
  echo "BACKUP_ENV_FILE must be /etc/mooncen/backup.env." >&2
  exit 64
fi
if [ "$BACKUP_IDENTITY_FILE" != "/etc/mooncen/backup-ssh-key" ]; then
  echo "BACKUP_IDENTITY_FILE must be /etc/mooncen/backup-ssh-key." >&2
  exit 64
fi
case "$NODE_ROLE" in
  primary|standby) ;;
  *)
    echo "NODE_ROLE must be primary or standby." >&2
    exit 64
    ;;
esac
case "$SKIP_DB_SETUP" in
  0|1) ;;
  *)
    echo "SKIP_DB_SETUP must be 0 or 1." >&2
    exit 64
    ;;
esac
case "$PREBUILT_RELEASE" in
  0|1) ;;
  *)
    echo "PREBUILT_RELEASE must be 0 or 1." >&2
    exit 64
    ;;
esac
case "$ENABLE_CRAWLER_STAGING" in
  0|1) ;;
  *)
    echo "ENABLE_CRAWLER_STAGING must be 0 or 1." >&2
    exit 64
    ;;
esac
if [ "$CRAWL_STAGING_PASSWORD_FILE" != "/etc/mooncen/staging-crawler-password" ]; then
  echo "CRAWL_STAGING_PASSWORD_FILE must use the protected MoonCen path." >&2
  exit 64
fi
if [ "$DEPLOY_COMMIT" != "unknown" ] && [[ ! "$DEPLOY_COMMIT" =~ ^([0-9a-f]{40}|[0-9a-f]{64})$ ]]; then
  echo "DEPLOY_COMMIT must be unknown or a 40/64-character lowercase hexadecimal Git object id." >&2
  exit 64
fi
if [ "$DEPLOY_ARCHIVE_SHA256" != "unknown" ] && [[ ! "$DEPLOY_ARCHIVE_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
  echo "DEPLOY_ARCHIVE_SHA256 must be unknown or a 64-character lowercase hexadecimal digest." >&2
  exit 64
fi

PRIMARY_DB_HOST_EFFECTIVE="${PRIMARY_DB_HOST:-localhost}"
if [ "$SKIP_DB_SETUP" = "1" ] || [ "$NODE_ROLE" = "standby" ]; then
  PRIMARY_DB_HOST_EFFECTIVE="${PRIMARY_DB_HOST:-cloud}"
fi

is_local_db_host() {
  local host="${1,,}"
  host="${host%.}"
  case "$host" in
    ''|localhost|localhost.localdomain|127.*|::1|/*) return 0 ;;
    *) return 1 ;;
  esac
}

validate_dns_hostname() {
  local label="$1"
  local hostname="$2"

  if [ -z "$hostname" ] || [ "${#hostname}" -gt 253 ] || [ "${hostname: -1}" = "." ]; then
    echo "${label} must be a non-empty DNS hostname without a trailing dot." >&2
    return 64
  fi
  if [[ ! "$hostname" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$ ]]; then
    echo "${label} is not a valid DNS hostname: ${hostname}" >&2
    return 64
  fi
}

validate_port() {
  local label="$1"
  local port="$2"

  if [[ ! "$port" =~ ^[0-9]{1,5}$ ]] || (( 10#$port < 1 || 10#$port > 65535 )); then
    echo "${label} must be an integer from 1 through 65535." >&2
    return 64
  fi
}

validate_db_host() {
  local label="$1"
  local host="$2"

  if [[ "$host" == /* ]]; then
    if [[ ! "$host" =~ ^/[A-Za-z0-9._/-]+$ ]] || [[ "$host" == *".."* ]]; then
      echo "${label} contains an unsafe PostgreSQL socket path." >&2
      return 64
    fi
    return 0
  fi
  if [[ "$host" == *:* ]]; then
    if [[ ! "$host" =~ ^[0-9A-Fa-f:]+$ ]]; then
      echo "${label} is not a valid IPv6 address." >&2
      return 64
    fi
    return 0
  fi
  validate_dns_hostname "$label" "$host"
}

validate_ollama_endpoint() {
  local label="$1"
  local endpoint="$2"
  local endpoint_host
  local endpoint_port

  if [[ ! "$endpoint" =~ ^https?://([A-Za-z0-9._-]+|\[[0-9A-Fa-f:]+\])(:[0-9]{1,5})?(/[A-Za-z0-9._~/-]*)?$ ]]; then
    echo "${label} must be an HTTP(S) base URL without credentials, query, fragment, or whitespace." >&2
    return 64
  fi
  endpoint_host="${BASH_REMATCH[1]}"
  endpoint_port="${BASH_REMATCH[2]#:}"
  if [[ "$endpoint_host" != \[*\] ]]; then
    validate_dns_hostname "$label" "$endpoint_host"
  fi
  if [ -n "$endpoint_port" ]; then
    validate_port "$label port" "$endpoint_port"
  fi
}

BACKUP_OS_USER="${BACKUP_OS_USER:-mooncen-backup}"
if [ "$BACKUP_OS_USER" != "mooncen-backup" ]; then
  echo "BACKUP_OS_USER must be the dedicated mooncen-backup service account." >&2
  exit 64
fi

generate_password() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    without_runtime_secrets python3 -I - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
  fi
}

write_deploy_secret_pair() {
  local key="$1"
  local value="$2"
  local encoded
  encoded="$(printf '%s' "$value" | base64 | tr -d '\r\n')"
  # Keep the raw key for operator/backward compatibility. Deploy tooling
  # prefers the base64 companion, so leading/trailing punctuation survives an
  # idempotent remote read without shell/env parsing ambiguity.
  printf '%s=%s\n' "$key" "$value"
  printf '%s_B64=%s\n' "$key" "$encoded"
}

for password_var in DB_PASSWORD DB_API_PASSWORD DB_CRAWLER_PASSWORD DB_DEPLOYMENT_WORKER_PASSWORD DB_AI_PASSWORD DB_APPLIER_PASSWORD DB_BACKUP_PASSWORD DB_CHECK_PASSWORD; do
  if [ -z "${!password_var}" ]; then
    if [ "$SKIP_DB_SETUP" = "1" ]; then
      echo "${password_var} is required when SKIP_DB_SETUP=1; it must match the primary node." >&2
      exit 64
    fi
    printf -v "$password_var" '%s' "$(generate_password)"
    echo "Generated missing ${password_var} for this installation."
  fi
done

if [ "${#AUTH_SECRET}" -lt 32 ] || [[ "$AUTH_SECRET" =~ ^(change-me|mooncen-dev-secret|replace-with) ]]; then
  echo "AUTH_SECRET must be a random value of at least 32 characters." >&2
  exit 64
fi
if [ "$MOONCEN_OPS_LOGIN_ID" != "opsadmin" ]; then
  echo "MOONCEN_OPS_LOGIN_ID must be the dedicated opsadmin account." >&2
  exit 64
fi
if [[ "$MOONCEN_OPS_PASSWORD_HASH" =~ ^pbkdf2_sha256\$([0-9]{6,7})\$([A-Za-z0-9_-]{16,128})\$([0-9a-f]{64})$ ]]; then
  ops_password_rounds="${BASH_REMATCH[1]}"
else
  echo "MOONCEN_OPS_PASSWORD_HASH must be generated by tools/generate_ops_password.py." >&2
  exit 64
fi
if [ "$ops_password_rounds" -lt 310000 ] || [ "$ops_password_rounds" -gt 2000000 ]; then
  echo "MOONCEN_OPS_PASSWORD_HASH uses an unsupported iteration count." >&2
  exit 64
fi
for env_value_var in AUTH_SECRET MOONCEN_OPS_LOGIN_ID KAKAO_MAPS_JAVASCRIPT_KEY KAKAO_MAPS_REST_API_KEY GOOGLE_OAUTH_CLIENT_ID GOOGLE_OAUTH_CLIENT_SECRET NAVER_OAUTH_CLIENT_ID NAVER_OAUTH_CLIENT_SECRET MOONCEN_BOT_TOKEN MOONCEN_BOT_CHAT_ID MOONCEN_ADMIN_EMAILS MOONCEN_ADMIN_PROVIDER_IDS MOONCEN_BUG_REPORT_TO MOONCEN_BUG_REPORT_FROM MOONCEN_SMTP_HOST MOONCEN_SMTP_USERNAME MOONCEN_SMTP_PASSWORD MOONCEN_SMTP_SECURITY OPS_CLOUDFLARE_ANALYTICS_ZONE_ID OPS_CLOUDFLARE_ANALYTICS_TOKEN MOONCEN_SERVER_MONITOR_TOKEN; do
  env_value="${!env_value_var}"
  if [[ "$env_value" == *$'\n'* ]] || [[ "$env_value" == *$'\r'* ]]; then
    echo "${env_value_var} must not contain line breaks." >&2
    exit 64
  fi
  if [[ ! "$env_value" =~ ^[A-Za-z0-9._!@%+=,:/-]*$ ]]; then
    echo "${env_value_var} contains characters that are unsafe in service EnvironmentFiles." >&2
    exit 64
  fi
done
if [ -n "$OPS_CLOUDFLARE_ANALYTICS_ZONE_ID" ] || [ -n "$OPS_CLOUDFLARE_ANALYTICS_TOKEN" ]; then
  if [[ ! "$OPS_CLOUDFLARE_ANALYTICS_ZONE_ID" =~ ^[0-9a-f]{32}$ ]]; then
    echo "OPS_CLOUDFLARE_ANALYTICS_ZONE_ID must be an exact lowercase 32-character Cloudflare zone id." >&2
    exit 64
  fi
  if [ "${#OPS_CLOUDFLARE_ANALYTICS_TOKEN}" -lt 20 ] || [ "${#OPS_CLOUDFLARE_ANALYTICS_TOKEN}" -gt 256 ] || [[ ! "$OPS_CLOUDFLARE_ANALYTICS_TOKEN" =~ ^[A-Za-z0-9_-]+$ ]]; then
    echo "OPS_CLOUDFLARE_ANALYTICS_TOKEN has an invalid format." >&2
    exit 64
  fi
fi
if [ -n "$MOONCEN_SERVER_MONITOR_TOKEN" ] && { [ "${#MOONCEN_SERVER_MONITOR_TOKEN}" -lt 32 ] || [ "${#MOONCEN_SERVER_MONITOR_TOKEN}" -gt 256 ] || [[ ! "$MOONCEN_SERVER_MONITOR_TOKEN" =~ ^[A-Za-z0-9_-]+$ ]]; }; then
  echo "MOONCEN_SERVER_MONITOR_TOKEN has an invalid format." >&2
  exit 64
fi
validate_port MOONCEN_SMTP_PORT "$MOONCEN_SMTP_PORT"
case "$MOONCEN_SMTP_SECURITY" in
  starttls|ssl|none) ;;
  *)
    echo "MOONCEN_SMTP_SECURITY must be starttls, ssl, or none." >&2
    exit 64
    ;;
esac
if [[ ! "$KAKAO_MAPS_JAVASCRIPT_KEY" =~ ^[0-9a-f]{32}$ ]]; then
  echo "KAKAO_MAPS_JAVASCRIPT_KEY must contain a non-empty lowercase 32-character Kakao JavaScript key." >&2
  exit 64
fi
if [[ ! "$KAKAO_MAPS_REST_API_KEY" =~ ^[0-9a-f]{32}$ ]]; then
  echo "KAKAO_MAPS_REST_API_KEY must contain a non-empty lowercase 32-character Kakao REST API key." >&2
  exit 64
fi
if [ "$KAKAO_MAPS_JAVASCRIPT_KEY" = "$KAKAO_MAPS_REST_API_KEY" ]; then
  echo "Kakao JavaScript and REST API credentials must use their distinct app keys." >&2
  exit 64
fi
for env_value_var in \
  DOMAIN DOMAIN_ALIASES OAUTH_REDIRECT_URI OLLAMA_HOST OLLAMA_HOSTS OLLAMA_MODEL \
  NODE_ROLE BACKUP_AGE_RECIPIENT BACKUP_AGE_IDENTITY_FILE BACKUP_IDENTITY_FILE BACKUP_KNOWN_HOSTS_FILE \
  BACKUP_MANIFEST_SIGNING_KEY BACKUP_MANIFEST_ALLOWED_SIGNERS DB_SSLROOTCERT_SOURCE \
  PRIMARY_DB_HOST PRIMARY_DB_PORT PRIMARY_DB_NAME CRAWL_STAGING_DB_HOST \
  CRAWL_STAGING_DB_PORT CRAWL_STAGING_DB_NAME DB_POOL_MIN DB_POOL_MAX AI_WORKERS \
  STAGING_CLOSE_MIN_RATIO STAGING_CLOSE_MAX_ABSOLUTE_DROP STAGING_CLOSE_RATIO_BASELINE; do
  env_value="${!env_value_var-}"
  if [[ "$env_value" == *$'\n'* ]] || [[ "$env_value" == *$'\r'* ]]; then
    echo "${env_value_var} must not contain line breaks." >&2
    exit 64
  fi
done

validate_db_host PRIMARY_DB_HOST "$PRIMARY_DB_HOST_EFFECTIVE"
validate_port PRIMARY_DB_PORT "${PRIMARY_DB_PORT:-5432}"
if [[ ! "${PRIMARY_DB_NAME:-$DB_NAME}" =~ ^[a-z_][a-z0-9_]*$ ]]; then
  echo "PRIMARY_DB_NAME must be a lowercase PostgreSQL identifier." >&2
  exit 64
fi
validate_db_host CRAWL_STAGING_DB_HOST "${CRAWL_STAGING_DB_HOST:-localhost}"
validate_port CRAWL_STAGING_DB_PORT "${CRAWL_STAGING_DB_PORT:-55432}"
if [[ ! "${CRAWL_STAGING_DB_NAME:-mooncen_staging}" =~ ^[a-z_][a-z0-9_]*$ ]]; then
  echo "CRAWL_STAGING_DB_NAME must be a lowercase PostgreSQL identifier." >&2
  exit 64
fi
if [[ ! "$CRAWL_STAGING_DB_USER" =~ ^[a-z_][a-z0-9_]*$ ]]; then
  echo "CRAWL_STAGING_DB_USER must be a lowercase PostgreSQL identifier." >&2
  exit 64
fi
if ! is_local_db_host "$CRAWL_STAGING_DB_HOST"; then
  echo "Primary-node crawler staging must use a dedicated local PostgreSQL cluster." >&2
  exit 64
fi
case "$CRAWL_STAGING_DB_USER" in
  "$DB_MIGRATOR_USER"|"$DB_API_USER"|"$DB_CRAWLER_USER"|"$DB_AI_USER"|"$DB_APPLIER_USER"|"$DB_BACKUP_USER"|"$DB_CHECK_USER")
    echo "Crawler staging login must differ from every primary database login." >&2
    exit 64
    ;;
esac
validate_ollama_endpoint OLLAMA_HOST "$OLLAMA_HOST"
if [ -n "$OLLAMA_HOSTS" ]; then
  if [[ "$OLLAMA_HOSTS" == ,* ]] || [[ "$OLLAMA_HOSTS" == *, ]] || [[ "$OLLAMA_HOSTS" == *,,* ]]; then
    echo "OLLAMA_HOSTS must be a comma-separated list without empty entries." >&2
    exit 64
  fi
  IFS=',' read -r -a ollama_endpoint_list <<< "$OLLAMA_HOSTS"
  for ollama_endpoint in "${ollama_endpoint_list[@]}"; do
    validate_ollama_endpoint OLLAMA_HOSTS "$ollama_endpoint"
  done
fi
if [[ ! "$OLLAMA_MODEL" =~ ^[A-Za-z0-9._:/-]+$ ]]; then
  echo "OLLAMA_MODEL contains characters that are unsafe in a service EnvironmentFile." >&2
  exit 64
fi
for numeric_var in DB_POOL_MIN DB_POOL_MAX AI_WORKERS; do
  numeric_default=1
  case "$numeric_var" in
    DB_POOL_MAX) numeric_default=8 ;;
    AI_WORKERS) numeric_default=2 ;;
  esac
  numeric_value="${!numeric_var:-$numeric_default}"
  if [[ ! "$numeric_value" =~ ^[0-9]+$ ]] || (( 10#$numeric_value < 1 || 10#$numeric_value > 128 )); then
    echo "${numeric_var} must be an integer from 1 through 128." >&2
    exit 64
  fi
done
if (( 10#${DB_POOL_MIN:-1} > 10#${DB_POOL_MAX:-8} )); then
  echo "DB_POOL_MIN must not exceed DB_POOL_MAX." >&2
  exit 64
fi
for ratio_var in STAGING_CLOSE_MIN_RATIO STAGING_CLOSE_RATIO_BASELINE; do
  ratio_value="${!ratio_var:-}"
  if [ -n "$ratio_value" ] && [[ ! "$ratio_value" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
    echo "${ratio_var} must be a non-negative decimal number." >&2
    exit 64
  fi
done
if [ -n "${STAGING_CLOSE_MAX_ABSOLUTE_DROP:-}" ] && [[ ! "$STAGING_CLOSE_MAX_ABSOLUTE_DROP" =~ ^[0-9]+$ ]]; then
  echo "STAGING_CLOSE_MAX_ABSOLUTE_DROP must be a non-negative integer." >&2
  exit 64
fi

if [ "$DOMAIN" = "_" ]; then
  echo "DOMAIN must be the public production hostname; the nginx '_' wildcard is not accepted." >&2
  exit 64
fi
validate_dns_hostname DOMAIN "$DOMAIN"

domain_names="$DOMAIN"
if [[ "$DOMAIN" == www.* ]]; then
  domain_names="$domain_names ${DOMAIN#www.}"
else
  domain_names="$domain_names www.${DOMAIN}"
fi
domain_alias_list=()
if [ -n "$DOMAIN_ALIASES" ]; then
  read -r -a domain_alias_list <<< "$DOMAIN_ALIASES"
  for alias in "${domain_alias_list[@]}"; do
    validate_dns_hostname DOMAIN_ALIASES "$alias"
    case " $domain_names " in
      *" $alias "*) ;;
      *) domain_names="$domain_names $alias" ;;
    esac
  done
fi

if [[ ! "$OAUTH_REDIRECT_URI" =~ ^https://([A-Za-z0-9.-]+)(:443)?(/[A-Za-z0-9._~/?=\&%+,:@-]*)?$ ]]; then
  echo "OAUTH_REDIRECT_URI must be an HTTPS URL with no credentials, fragment, whitespace, or unsafe EnvironmentFile characters." >&2
  exit 64
fi
oauth_redirect_host="${BASH_REMATCH[1]}"
validate_dns_hostname OAUTH_REDIRECT_URI "$oauth_redirect_host"
oauth_redirect_allowed=0
for hostname in $domain_names; do
  if [ "${hostname,,}" = "${oauth_redirect_host,,}" ]; then
    oauth_redirect_allowed=1
    break
  fi
done
if [ "$oauth_redirect_allowed" -ne 1 ]; then
  echo "OAUTH_REDIRECT_URI host must match DOMAIN or one of its trusted aliases." >&2
  exit 64
fi

trusted_hosts=""
for hostname in $domain_names; do
  if [ -z "$trusted_hosts" ]; then
    trusted_hosts="$hostname"
  else
    trusted_hosts="$trusted_hosts,$hostname"
  fi
done
for password_var in DB_PASSWORD DB_API_PASSWORD DB_CRAWLER_PASSWORD DB_DEPLOYMENT_WORKER_PASSWORD DB_AI_PASSWORD DB_APPLIER_PASSWORD DB_BACKUP_PASSWORD DB_CHECK_PASSWORD; do
  password_value="${!password_var}"
  if [ "${#password_value}" -lt 16 ] || [[ "$password_value" =~ ^(change-me|replace-with) ]]; then
    echo "${password_var} must be a random value of at least 16 characters." >&2
    exit 64
  fi
  if [[ "$password_value" == *$'\n'* ]] || [[ "$password_value" == *$'\r'* ]]; then
    echo "${password_var} must not contain line breaks." >&2
    exit 64
  fi
done
for password_var in DB_PASSWORD DB_API_PASSWORD DB_CRAWLER_PASSWORD DB_DEPLOYMENT_WORKER_PASSWORD DB_AI_PASSWORD DB_APPLIER_PASSWORD DB_BACKUP_PASSWORD DB_CHECK_PASSWORD; do
  password_value="${!password_var}"
  if [[ ! "$password_value" =~ ^[A-Za-z0-9._!@%+=,:/-]+$ ]]; then
    echo "${password_var} contains characters that are unsafe in service EnvironmentFiles." >&2
    exit 64
  fi
done
[ "$DB_DEPLOYMENT_WORKER_USER" = mooncen_deployment_worker_login ] || {
  echo "DB_DEPLOYMENT_WORKER_USER must be the fixed mooncen_deployment_worker_login capability identity." >&2
  exit 64
}
for role_name in "$DB_MIGRATOR_USER" "$DB_API_USER" "$DB_CRAWLER_USER" "$DB_DEPLOYMENT_WORKER_USER" "$DB_AI_USER" "$DB_APPLIER_USER" "$DB_BACKUP_USER" "$DB_CHECK_USER"; do
  if [[ ! "$role_name" =~ ^[a-z_][a-z0-9_]{0,62}$ ]]; then
    echo "Database role names must be lowercase PostgreSQL identifiers." >&2
    exit 64
  fi
  case "$role_name" in
    mooncen_api|mooncen_crawler|mooncen_deployment_worker|mooncen_ai|mooncen_applier|mooncen_readonly|mooncen_check)
      echo "LOGIN role ${role_name} must differ from the NOLOGIN permission groups." >&2
      exit 64
      ;;
  esac
done
if [[ ! "$DB_NAME" =~ ^[a-z_][a-z0-9_]*$ ]]; then
  echo "DB_NAME must be a lowercase PostgreSQL identifier." >&2
  exit 64
fi
if [ "$(printf '%s\n' "$DB_MIGRATOR_USER" "$DB_API_USER" "$DB_CRAWLER_USER" "$DB_DEPLOYMENT_WORKER_USER" "$DB_AI_USER" "$DB_APPLIER_USER" "$DB_BACKUP_USER" "$DB_CHECK_USER" | sort -u | wc -l)" -ne 8 ]; then
  echo "Migration and runtime database role names must be distinct." >&2
  exit 64
fi
database_password_vars=(
  DB_PASSWORD DB_API_PASSWORD DB_CRAWLER_PASSWORD
  DB_DEPLOYMENT_WORKER_PASSWORD DB_AI_PASSWORD DB_APPLIER_PASSWORD
  DB_BACKUP_PASSWORD DB_CHECK_PASSWORD
)
for ((password_index = 0; password_index < ${#database_password_vars[@]}; password_index++)); do
  for ((other_password_index = password_index + 1; other_password_index < ${#database_password_vars[@]}; other_password_index++)); do
    password_var="${database_password_vars[$password_index]}"
    other_password_var="${database_password_vars[$other_password_index]}"
    if [ "${!password_var}" = "${!other_password_var}" ]; then
      echo "Database LOGIN credentials must be pairwise distinct." >&2
      exit 64
    fi
  done
done
if [ -n "$BACKUP_AGE_RECIPIENT" ] && [[ ! "$BACKUP_AGE_RECIPIENT" =~ ^age1[0-9a-z]+$ ]]; then
  echo "BACKUP_AGE_RECIPIENT must be an age X25519 recipient beginning with age1." >&2
  exit 64
fi
if [ "$NODE_ROLE" = "primary" ] && [ -z "$BACKUP_AGE_RECIPIENT" ]; then
  echo "BACKUP_AGE_RECIPIENT is required on a primary node." >&2
  exit 64
fi
if [ "$NODE_ROLE" = "primary" ]; then
  for backup_trust_path_var in \
    BACKUP_AGE_IDENTITY_FILE BACKUP_IDENTITY_FILE BACKUP_KNOWN_HOSTS_FILE \
    BACKUP_MANIFEST_SIGNING_KEY BACKUP_MANIFEST_ALLOWED_SIGNERS; do
    backup_trust_path="${!backup_trust_path_var}"
    case "$backup_trust_path" in
      /etc/mooncen/*) ;;
      *)
        echo "$backup_trust_path_var must be stored below /etc/mooncen on a primary node." >&2
        exit 78
        ;;
    esac
  done
  if ! sudo test -f "$BACKUP_AGE_IDENTITY_FILE" || sudo test -L "$BACKUP_AGE_IDENTITY_FILE"; then
    echo "Backup age identity must already exist as a regular non-symlink file: $BACKUP_AGE_IDENTITY_FILE" >&2
    exit 78
  fi
  backup_age_identity_contract="$(sudo stat -c '%U:%G:%a' "$BACKUP_AGE_IDENTITY_FILE")"
  if [ "$backup_age_identity_contract" != "root:root:600" ]; then
    echo "Backup age identity must be owned by root:root with mode 0600." >&2
    exit 78
  fi
  command -v age-keygen >/dev/null 2>&1 || { echo "age-keygen is required to validate the backup identity." >&2; exit 69; }
  backup_age_public_recipient="$(sudo age-keygen -y "$BACKUP_AGE_IDENTITY_FILE" 2>/dev/null)"
  if [ "$backup_age_public_recipient" != "$BACKUP_AGE_RECIPIENT" ]; then
    echo "Backup age identity does not match BACKUP_AGE_RECIPIENT." >&2
    exit 78
  fi
fi
if [[ ! "$BACKUP_RETENTION_DAYS" =~ ^[0-9]+$ ]]; then
  echo "BACKUP_RETENTION_DAYS must be a non-negative integer." >&2
  exit 64
fi
if [ -n "$BACKUP_PORT" ]; then
  if [[ ! "$BACKUP_PORT" =~ ^[0-9]+$ ]] || [ "$BACKUP_PORT" -lt 1 ] || [ "$BACKUP_PORT" -gt 65535 ]; then
    echo "BACKUP_PORT must be empty or an integer from 1 to 65535." >&2
    exit 64
  fi
fi
for backup_bound_var in \
  BACKUP_MAX_ENCRYPTED_DUMP_BYTES BACKUP_MAX_DECRYPTED_DUMP_BYTES \
  BACKUP_LOCAL_MIN_FREE_BYTES BACKUP_DB_MIN_FREE_BYTES BACKUP_RESTORE_EXPANSION_FACTOR \
  BACKUP_MAX_APP_ARCHIVE_BYTES BACKUP_MAX_CONFIG_ARCHIVE_BYTES BACKUP_MAX_SOURCE_DB_BYTES; do
  backup_bound_value="${!backup_bound_var}"
  if [[ ! "$backup_bound_value" =~ ^[0-9]+$ ]]; then
    echo "$backup_bound_var must be a non-negative integer." >&2
    exit 64
  fi
done
if [ "$BACKUP_MAX_ENCRYPTED_DUMP_BYTES" -lt 1048576 ] || \
   [ "$BACKUP_MAX_ENCRYPTED_DUMP_BYTES" -gt 1099511627776 ] || \
   [ "$BACKUP_MAX_DECRYPTED_DUMP_BYTES" -lt 1048576 ] || \
   [ "$BACKUP_MAX_DECRYPTED_DUMP_BYTES" -gt 1099511627776 ] || \
   [ "$BACKUP_LOCAL_MIN_FREE_BYTES" -gt 1099511627776 ] || \
   [ "$BACKUP_DB_MIN_FREE_BYTES" -gt 1099511627776 ] || \
   [ "$BACKUP_RESTORE_EXPANSION_FACTOR" -lt 1 ] || [ "$BACKUP_RESTORE_EXPANSION_FACTOR" -gt 16 ] || \
   [[ ! "$BACKUP_MAX_AGE_SECONDS" =~ ^[0-9]+$ ]] || \
   [ "$BACKUP_MAX_AGE_SECONDS" -lt 3600 ] || [ "$BACKUP_MAX_AGE_SECONDS" -gt 315360000 ]; then
  echo "Backup restore size policy is outside the allowed bounds." >&2
  exit 64
fi
if [ "$BACKUP_MAX_APP_ARCHIVE_BYTES" -lt 1048576 ] || [ "$BACKUP_MAX_APP_ARCHIVE_BYTES" -gt 68719476736 ] || \
   [ "$BACKUP_MAX_CONFIG_ARCHIVE_BYTES" -lt 1048576 ] || [ "$BACKUP_MAX_CONFIG_ARCHIVE_BYTES" -gt 17179869184 ] || \
   [ "$BACKUP_MAX_SOURCE_DB_BYTES" -lt 1048576 ] || [ "$BACKUP_MAX_SOURCE_DB_BYTES" -gt 4398046511104 ]; then
  echo "Backup source/archive size policy is outside the allowed bounds." >&2
  exit 64
fi
if ! is_local_db_host "$PRIMARY_DB_HOST_EFFECTIVE" && [ -z "$DB_SSLROOTCERT_SOURCE" ]; then
  echo "DB_SSLROOTCERT is required for remote production primary DB host ${PRIMARY_DB_HOST_EFFECTIVE}." >&2
  exit 64
fi
if [ -n "$DB_SSLROOTCERT_SOURCE" ]; then
  if [[ "$DB_SSLROOTCERT_SOURCE" != /* ]] || [[ ! "$DB_SSLROOTCERT_SOURCE" =~ ^[A-Za-z0-9._/-]+$ ]]; then
    echo "DB_SSLROOTCERT must be an absolute path without shell-sensitive characters." >&2
    exit 64
  fi
  if ! sudo test -f "$DB_SSLROOTCERT_SOURCE" || sudo test -L "$DB_SSLROOTCERT_SOURCE"; then
    echo "DB_SSLROOTCERT must be an existing regular file, not a symlink." >&2
    exit 78
  fi
  db_ca_source_mode="$(sudo stat -c '%a' "$DB_SSLROOTCERT_SOURCE")"
  if (( (8#$db_ca_source_mode & 8#022) != 0 )); then
    echo "DB_SSLROOTCERT must not be group- or world-writable." >&2
    exit 78
  fi
fi

APP_GROUP=mooncen
if ! getent group "$APP_GROUP" >/dev/null 2>&1; then
  sudo groupadd --system "$APP_GROUP"
fi
if ! id "$APP_USER" >/dev/null 2>&1; then
  sudo useradd --system --gid "$APP_GROUP" --create-home --shell /usr/sbin/nologin "$APP_USER"
else
  sudo usermod --gid "$APP_GROUP" --groups "$APP_GROUP" --shell /usr/sbin/nologin "$APP_USER"
fi
if [ "$DEPLOY_USER" != root ]; then
  sudo usermod --append --groups "$APP_GROUP" "$DEPLOY_USER"
fi

ensure_service_account() {
  local service_user="$1"

  if ! getent group "$service_user" >/dev/null 2>&1; then
    sudo groupadd --system "$service_user"
  fi
  if ! id "$service_user" >/dev/null 2>&1; then
    sudo useradd \
      --system \
      --gid "$service_user" \
      --groups "$APP_GROUP" \
      --no-create-home \
      --home-dir /nonexistent \
      --shell /usr/sbin/nologin \
      "$service_user"
  else
    sudo usermod --gid "$service_user" --groups "$APP_GROUP" "$service_user"
  fi
  sudo usermod --home /nonexistent --shell /usr/sbin/nologin "$service_user"
}

for service_user in \
  "$API_OS_USER" \
  "$CRAWLER_OS_USER" \
  "$AI_OS_USER" \
  "$FRONTEND_OS_USER" \
  "$BOT_OS_USER" \
  "$APPLIER_OS_USER" \
  "$FUNCTIONAL_OS_USER" \
  "$BACKUP_OS_USER"; do
  ensure_service_account "$service_user"
done

if ! getent group "$DB_TLS_GROUP" >/dev/null 2>&1; then
  sudo groupadd --system "$DB_TLS_GROUP"
fi
for db_service_user in \
  "$API_OS_USER" \
  "$CRAWLER_OS_USER" \
  "$AI_OS_USER" \
  "$APPLIER_OS_USER" \
  "$FUNCTIONAL_OS_USER"; do
  sudo usermod --append --groups "$DB_TLS_GROUP" "$db_service_user"
done

if ! getent group "$CLOUDFLARED_OS_USER" >/dev/null 2>&1; then
  sudo groupadd --system "$CLOUDFLARED_OS_USER"
fi
if ! id "$CLOUDFLARED_OS_USER" >/dev/null 2>&1; then
  sudo useradd \
    --system \
    --gid "$CLOUDFLARED_OS_USER" \
    --no-create-home \
    --home-dir /var/lib/cloudflared \
    --shell /usr/sbin/nologin \
    "$CLOUDFLARED_OS_USER"
else
  sudo usermod \
    --gid "$CLOUDFLARED_OS_USER" \
    --groups "$CLOUDFLARED_OS_USER" \
    --home /var/lib/cloudflared \
    --shell /usr/sbin/nologin \
    "$CLOUDFLARED_OS_USER"
fi
sudo install -d -o root -g "$CLOUDFLARED_OS_USER" -m 0750 /etc/cloudflared
if [ -f /etc/cloudflared/token ] && [ ! -L /etc/cloudflared/token ]; then
  sudo chown root:"$CLOUDFLARED_OS_USER" /etc/cloudflared/token
  sudo chmod 0640 /etc/cloudflared/token
fi

if ! id "$BACKUP_OS_USER" >/dev/null 2>&1; then
  echo "Backup OS user does not exist: $BACKUP_OS_USER" >&2
  exit 67
fi
BACKUP_OS_GROUP="${BACKUP_OS_GROUP:-mooncen-backup}"
if [ "$BACKUP_OS_GROUP" != "mooncen-backup" ] || [ "$(id -gn "$BACKUP_OS_USER")" != "mooncen-backup" ]; then
  echo "BACKUP_OS_GROUP and the backup account primary group must be mooncen-backup." >&2
  exit 64
fi
if [ "$NODE_ROLE" = "primary" ]; then
  for backup_trust_contract in \
    "$BACKUP_IDENTITY_FILE|root:${BACKUP_OS_GROUP}:640|Backup SSH identity" \
    "$BACKUP_KNOWN_HOSTS_FILE|root:${BACKUP_OS_GROUP}:640|Backup pinned known_hosts" \
    "$BACKUP_MANIFEST_SIGNING_KEY|root:${BACKUP_OS_GROUP}:640|Backup manifest signing key" \
    "$BACKUP_MANIFEST_ALLOWED_SIGNERS|root:root:644|Backup manifest allowed_signers"; do
    IFS='|' read -r trust_path expected_contract trust_label <<<"$backup_trust_contract"
    if ! sudo test -f "$trust_path" || sudo test -L "$trust_path"; then
      echo "$trust_label must already exist as a regular non-symlink file: $trust_path" >&2
      exit 78
    fi
    actual_contract="$(sudo stat -c '%U:%G:%a' "$trust_path")"
    if [ "$actual_contract" != "$expected_contract" ]; then
      echo "$trust_label must have ownership/mode $expected_contract (found $actual_contract)." >&2
      exit 78
    fi
  done
  backup_known_host_token=wtr-nas
  if [ -n "$BACKUP_PORT" ] && [ "$BACKUP_PORT" != "22" ]; then
    backup_known_host_token="[wtr-nas]:${BACKUP_PORT}"
  fi
  known_hosts_contract="$(sudo awk -v expected_host="$backup_known_host_token" '
    /^[[:space:]]*($|#)/ {next}
    {count++; if (NF == 3 && $1 == expected_host && $2 == "ssh-ed25519") line=$0; else invalid=1}
    END {if (count == 1 && invalid != 1) print line}
  ' "$BACKUP_KNOWN_HOSTS_FILE")"
  if [ -z "$known_hosts_contract" ] || \
     ! sudo ssh-keygen -l -f "$BACKUP_KNOWN_HOSTS_FILE" >/dev/null 2>&1; then
    echo "Backup pinned known_hosts must contain exactly one literal $backup_known_host_token Ed25519 host key." >&2
    exit 78
  fi
  # The key is intentionally root:mooncen-backup 0640. OpenSSH rejects a
  # group-readable private key when root (the owner) opens it, so validate it
  # as the dedicated service account that consumes it through group access.
  signing_public_key="$(sudo -u "$BACKUP_OS_USER" ssh-keygen -y -f "$BACKUP_MANIFEST_SIGNING_KEY" 2>/dev/null | awk '{print $1 " " $2}')"
  allowed_public_key="$(sudo awk '
    /^[[:space:]]*($|#)/ {next}
    {count++; if (NF == 3 && $1 == "mooncen-backup" && $2 == "ssh-ed25519") key=$2 " " $3; else invalid=1}
    END {if (count == 1 && invalid != 1) print key}
  ' "$BACKUP_MANIFEST_ALLOWED_SIGNERS")"
  if [[ "$signing_public_key" != ssh-ed25519\ * ]] || [ "$signing_public_key" != "$allowed_public_key" ]; then
    echo "Backup manifest signing key must be Ed25519 and match the single mooncen-backup allowed_signers entry." >&2
    exit 78
  fi
fi
BACKUP_LOCK_TMPFILES=/etc/tmpfiles.d/mooncen-backup-restore-lock.conf
sudo tee "$BACKUP_LOCK_TMPFILES" >/dev/null <<EOF
f /run/lock/mooncen-backup-restore.lock 0660 root ${BACKUP_OS_GROUP} -
EOF
sudo chown root:root "$BACKUP_LOCK_TMPFILES"
sudo chmod 0644 "$BACKUP_LOCK_TMPFILES"
sudo systemd-tmpfiles --create "$BACKUP_LOCK_TMPFILES"
if sudo test -L /run/lock/mooncen-backup-restore.lock || \
   [ "$(sudo stat -c '%U:%G:%a' /run/lock/mooncen-backup-restore.lock 2>/dev/null || true)" != "root:${BACKUP_OS_GROUP}:660" ]; then
  echo "Backup operation lock must be root:${BACKUP_OS_GROUP} mode 0660 and not a symlink." >&2
  exit 78
fi
sudo usermod --append --groups "$APP_GROUP" "$BACKUP_OS_USER"
sudo install -d -o root -g root -m 0755 /etc/systemd/system/mooncen-backup.service.d
sudo tee /etc/systemd/system/mooncen-backup.service.d/10-runtime-user.conf >/dev/null <<UNIT
[Service]
User=${BACKUP_OS_USER}
Group=${BACKUP_OS_GROUP}
UNIT
sudo chmod 0644 /etc/systemd/system/mooncen-backup.service.d/10-runtime-user.conf

install_service_env() {
  local filename="$1"
  local service_group="$2"

  sudo install -o root -g "$service_group" -m 0640 \
    /dev/null "$SERVICE_CONFIG_DIR/$filename"
  sudo tee "$SERVICE_CONFIG_DIR/$filename" >/dev/null
}

# Container runtime inputs use a separate root-only boundary.  The native
# services keep their root:<service> 0640 files so a first-cutover rollback can
# restart them without changing their credential contract.  Container files
# are rendered from explicit allowlists; combined native env files are never
# copied across the public-container boundary.
install_container_env() {
  local destination_filename="$1"
  local stage

  stage="$(sudo mktemp "$SERVICE_CONFIG_DIR/.${destination_filename}.XXXXXX")"
  if ! sudo chown root:root "$stage" ||
     ! sudo chmod 0600 "$stage" ||
     ! sudo tee "$stage" >/dev/null ||
     ! sudo sync -f -- "$stage" ||
     ! sudo mv -fT -- "$stage" "$SERVICE_CONFIG_DIR/$destination_filename"; then
    sudo rm -f -- "$stage"
    return 1
  fi
  sudo sync -f -- "$SERVICE_CONFIG_DIR"
}

# The operations bot contains three fixed PostgreSQL health queries. Preserve
# those diagnostics without granting the network-facing bot arbitrary psql or
# postgres-shell access: sudo may execute only this root-owned query allowlist.
BOT_PSQL_HELPER_DIR=/usr/local/libexec/mooncen-bot
BOT_PSQL_HELPER="$BOT_PSQL_HELPER_DIR/psql"
BOT_SUDOERS_FILE=/etc/sudoers.d/mooncen-bot-db-status

sudo rm -rf -- "$BOT_PSQL_HELPER_DIR"
sudo install -d -o root -g root -m 0755 "$BOT_PSQL_HELPER_DIR"
sudo install -o root -g root -m 0755 /dev/null "$BOT_PSQL_HELPER"
sudo tee "$BOT_PSQL_HELPER" >/dev/null <<'SH'
#!/bin/sh
set -eu

if [ "$#" -ne 2 ] || [ "$1" != "-Atqc" ]; then
  echo "mooncen bot DB helper: unsupported arguments" >&2
  exit 64
fi

case "$2" in
  "SELECT CASE WHEN pg_is_in_recovery() THEN 'standby' ELSE 'primary' END;"|\
  "SELECT status || '|' || coalesce(sender_host,'') || '|' || coalesce(slot_name,'') FROM pg_stat_wal_receiver;"|\
  "SELECT status FROM pg_stat_wal_receiver LIMIT 1;")
    query="$2"
    ;;
  *)
    echo "mooncen bot DB helper: query is not allowlisted" >&2
    exit 64
    ;;
esac

exec /usr/bin/env -i \
  PATH=/usr/bin:/bin \
  HOME=/var/lib/postgresql \
  /usr/bin/psql -X --no-password -d postgres -Atqc "$query"
SH
sudo chown root:root "$BOT_PSQL_HELPER"
sudo chmod 0755 "$BOT_PSQL_HELPER"

sudo install -o root -g root -m 0440 /dev/null "$BOT_SUDOERS_FILE"
sudo tee "$BOT_SUDOERS_FILE" >/dev/null <<'SUDOERS'
# Query validation and environment sanitization are enforced by this root-owned helper.
Defaults:mooncen-bot secure_path="/usr/local/libexec/mooncen-bot:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
mooncen-bot ALL=(postgres) NOPASSWD: /usr/local/libexec/mooncen-bot/psql
SUDOERS
sudo chmod 0440 "$BOT_SUDOERS_FILE"
sudo visudo -cf "$BOT_SUDOERS_FILE" >/dev/null

sudo mkdir -p "$APP_DIR"
sudo chown -R "$DEPLOY_USER":"$DEPLOY_GROUP" "$APP_DIR"

python_version="$(without_runtime_secrets python3 -I -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
case "$python_version" in
  3.12|3.13) ;;
  *)
    echo "Unsupported Python ${python_version}; MoonCen deployment supports Python 3.12 and 3.13." >&2
    exit 65
    ;;
esac
if [ ! -f "$APP_DIR/requirements.lock" ] || [ -L "$APP_DIR/requirements.lock" ]; then
  echo "Missing hash-locked dependency file: $APP_DIR/requirements.lock" >&2
  exit 66
fi
if [ "$PREBUILT_RELEASE" = "1" ]; then
  prebuild_marker="$APP_DIR/.mooncen-prebuilt-release"
  marker_value() {
    local marker_key="$1"
    awk -F= -v key="$marker_key" '
      $1 == key { count += 1; value = substr($0, length(key) + 2) }
      END { if (count != 1) exit 1; print value }
    ' "$prebuild_marker"
  }
  verify_prebuild_digest() {
    local marker_key="$1"
    local artifact="$2"
    local expected actual
    [ -f "$artifact" ] && [ ! -L "$artifact" ] || {
      echo "Prebuilt release artifact is missing or unsafe: $artifact" >&2
      exit 66
    }
    expected="$(marker_value "$marker_key")" || {
      echo "Prebuilt release marker field is invalid: $marker_key" >&2
      exit 65
    }
    [[ "$expected" =~ ^[0-9a-f]{64}$ ]] || {
      echo "Prebuilt release marker digest is invalid: $marker_key" >&2
      exit 65
    }
    actual="$(sha256sum "$artifact" | awk '{print $1}')"
    if [ "$actual" != "$expected" ]; then
      echo "Prebuilt release artifact digest mismatch: $artifact" >&2
      exit 65
    fi
  }

  [ -f "$prebuild_marker" ] && [ ! -L "$prebuild_marker" ] || {
    echo "PREBUILT_RELEASE=1 requires a regular prebuild marker." >&2
    exit 66
  }
  marker_metadata="$(stat -c '%U:%G:%a' "$prebuild_marker")"
  if [ "$marker_metadata" != "$DEPLOY_USER:$DEPLOY_GROUP:600" ]; then
    echo "Prebuilt release marker ownership or mode is unsafe." >&2
    exit 65
  fi
  if [ "$(marker_value PREBUILD_VERSION)" != "1" ] ||
     [ "$(marker_value DEPLOY_COMMIT)" != "$DEPLOY_COMMIT" ]; then
    echo "Prebuilt release provenance does not match this deployment." >&2
    exit 65
  fi
  verify_prebuild_digest REQUIREMENTS_SHA256 "$APP_DIR/requirements.lock"
  verify_prebuild_digest PACKAGE_LOCK_SHA256 "$APP_DIR/frontend2/package-lock.json"
  verify_prebuild_digest FRONTEND_ENV_SHA256 "$APP_DIR/frontend2/.env.production"
  verify_prebuild_digest FRONTEND_INDEX_SHA256 "$APP_DIR/frontend2/dist/index.html"

  for directory in \
    "$APP_DIR/.venv" \
    "$APP_DIR/frontend2/node_modules" \
    "$APP_DIR/frontend2/dist"; do
    if [ ! -d "$directory" ] || [ -L "$directory" ]; then
      echo "Prebuilt release directory is missing or unsafe: $directory" >&2
      exit 66
    fi
  done
  if [ ! -f "$APP_DIR/.venv/bin/python" ] ||
     [ -L "$APP_DIR/.venv/bin/python" ] ||
     [ ! -x "$APP_DIR/.venv/bin/python" ]; then
    echo "Prebuilt release Python executable is missing or unsafe." >&2
    exit 66
  fi
  mapfile -t leaked_candidate_paths < <(
    grep -RIlE -- '/opt/\.mooncen-release-[0-9a-f]{32}' "$APP_DIR/.venv" 2>/dev/null || true
  )
  if [ "${#leaked_candidate_paths[@]}" -ne 0 ]; then
    echo "Candidate release path leaked into the activated virtual environment." >&2
    exit 65
  fi

  actual_venv_prefix="$(without_runtime_secrets \
    "$APP_DIR/.venv/bin/python" -I -c 'import sys; print(sys.prefix)')"
  if [ "$actual_venv_prefix" != "$APP_DIR/.venv" ]; then
    echo "Prebuilt virtual environment did not relocate to $APP_DIR/.venv." >&2
    exit 65
  fi
  without_runtime_secrets "$APP_DIR/.venv/bin/python" -I -m pip check
  without_runtime_secrets "$APP_DIR/.venv/bin/python" -I -m compileall -q -f \
    "$APP_DIR/backend" "$APP_DIR/Crawler" "$APP_DIR/tools"
  without_runtime_secrets "$APP_DIR/.venv/bin/python" -I - "$APP_DIR" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve(strict=True)
sys.path.insert(0, str(root))
import fastapi  # noqa: F401, E402
import psycopg2  # noqa: F401, E402
import uvicorn  # noqa: F401, E402
import backend.main  # noqa: F401, E402
PY
else
  venv_stage="$(mktemp -d "$APP_DIR/.venv.stage.XXXXXX")"
  venv_previous="$APP_DIR/.venv.previous.$$"
  cleanup_venv_stage() {
    case "${venv_stage:-}" in
      "$APP_DIR"/.venv.stage.*) rm -rf -- "$venv_stage" ;;
    esac
    case "${venv_previous:-}" in
      "$APP_DIR"/.venv.previous.*)
        if [ -d "$venv_previous" ] && [ ! -e "$APP_DIR/.venv" ]; then
          mv -- "$venv_previous" "$APP_DIR/.venv"
        fi
        ;;
    esac
  }
  trap cleanup_venv_stage EXIT HUP INT TERM
  without_runtime_secrets python3 -I -m venv --clear "$venv_stage"
  without_runtime_secrets \
    "$venv_stage/bin/python" -I -m pip install --require-hashes -r "$APP_DIR/requirements.lock"
  if [ -e "$APP_DIR/.venv" ] || [ -L "$APP_DIR/.venv" ]; then
    if [ ! -d "$APP_DIR/.venv" ] || [ -L "$APP_DIR/.venv" ] || [ -e "$venv_previous" ]; then
      echo "Existing virtual environment path is unsafe; refusing replacement." >&2
      exit 65
    fi
    mv -- "$APP_DIR/.venv" "$venv_previous"
  fi
  if ! mv -- "$venv_stage" "$APP_DIR/.venv"; then
    if [ -d "$venv_previous" ] && [ ! -e "$APP_DIR/.venv" ]; then
      mv -- "$venv_previous" "$APP_DIR/.venv"
    fi
    exit 74
  fi
  venv_stage=""
  if [ -d "$venv_previous" ]; then
    case "$venv_previous" in
      "$APP_DIR"/.venv.previous.*) rm -rf -- "$venv_previous" ;;
    esac
  fi
  trap - EXIT HUP INT TERM

  pushd "$APP_DIR/frontend2" >/dev/null
  without_runtime_secrets npm ci --ignore-scripts
  {
    printf 'VITE_KAKAO_MAPS_JAVASCRIPT_KEY=%s\n' "$KAKAO_MAPS_JAVASCRIPT_KEY"
    printf 'VITE_GOOGLE_OAUTH_CLIENT_ID=%s\n' "$GOOGLE_OAUTH_CLIENT_ID"
    printf 'VITE_NAVER_OAUTH_CLIENT_ID=%s\n' "$NAVER_OAUTH_CLIENT_ID"
    printf 'VITE_SITE_URL=https://%s\n' "$DOMAIN"
    printf 'VITE_OAUTH_REDIRECT_URI=%s\n' "$OAUTH_REDIRECT_URI"
  } > .env.production
  chmod 600 .env.production
  without_runtime_secrets npm run build
  popd >/dev/null
fi

# Persist the validated deploy and schema-owner credentials before changing any
# PostgreSQL role password. If a later migration fails, the next clean deploy
# can recover the exact credential from this root-protected store.
mkdir -p "$DEPLOY_SECRET_DIR"
chmod 700 "$DEPLOY_SECRET_DIR"
deploy_secret_tmp="$(mktemp "$DEPLOY_SECRET_DIR/deploy-secrets.env.XXXXXX")"
{
  printf 'DB_NAME=%s\n' "$DB_NAME"
  printf 'DB_MIGRATOR_USER=%s\n' "$DB_MIGRATOR_USER"
  printf 'DB_USER=%s\n' "$DB_MIGRATOR_USER"
  write_deploy_secret_pair DB_PASSWORD "$DB_PASSWORD"
  printf 'DB_API_USER=%s\n' "$DB_API_USER"
  write_deploy_secret_pair DB_API_PASSWORD "$DB_API_PASSWORD"
  write_deploy_secret_pair DB_CRAWLER_PASSWORD "$DB_CRAWLER_PASSWORD"
  printf 'DB_DEPLOYMENT_WORKER_USER=%s\n' "$DB_DEPLOYMENT_WORKER_USER"
  write_deploy_secret_pair DB_DEPLOYMENT_WORKER_PASSWORD "$DB_DEPLOYMENT_WORKER_PASSWORD"
  printf 'DB_AI_USER=%s\n' "$DB_AI_USER"
  write_deploy_secret_pair DB_AI_PASSWORD "$DB_AI_PASSWORD"
  printf 'DB_APPLIER_USER=%s\n' "$DB_APPLIER_USER"
  write_deploy_secret_pair PRIMARY_DB_PASSWORD "$DB_APPLIER_PASSWORD"
  write_deploy_secret_pair DB_BACKUP_PASSWORD "$DB_BACKUP_PASSWORD"
  printf 'DB_CHECK_USER=%s\n' "$DB_CHECK_USER"
  write_deploy_secret_pair DB_CHECK_PASSWORD "$DB_CHECK_PASSWORD"
  write_deploy_secret_pair AUTH_SECRET "$AUTH_SECRET"
  write_deploy_secret_pair MOONCEN_OPS_LOGIN_ID "$MOONCEN_OPS_LOGIN_ID"
  write_deploy_secret_pair MOONCEN_OPS_PASSWORD_HASH "$MOONCEN_OPS_PASSWORD_HASH"
  write_deploy_secret_pair KAKAO_MAPS_JAVASCRIPT_KEY "$KAKAO_MAPS_JAVASCRIPT_KEY"
  write_deploy_secret_pair KAKAO_MAPS_REST_API_KEY "$KAKAO_MAPS_REST_API_KEY"
  printf 'GOOGLE_OAUTH_CLIENT_ID=%s\n' "$GOOGLE_OAUTH_CLIENT_ID"
  write_deploy_secret_pair GOOGLE_OAUTH_CLIENT_SECRET "$GOOGLE_OAUTH_CLIENT_SECRET"
  printf 'NAVER_OAUTH_CLIENT_ID=%s\n' "$NAVER_OAUTH_CLIENT_ID"
  write_deploy_secret_pair NAVER_OAUTH_CLIENT_SECRET "$NAVER_OAUTH_CLIENT_SECRET"
  write_deploy_secret_pair MOONCEN_BUG_REPORT_TO "$MOONCEN_BUG_REPORT_TO"
  write_deploy_secret_pair MOONCEN_BUG_REPORT_FROM "$MOONCEN_BUG_REPORT_FROM"
  write_deploy_secret_pair MOONCEN_SMTP_HOST "$MOONCEN_SMTP_HOST"
  write_deploy_secret_pair MOONCEN_SMTP_PORT "$MOONCEN_SMTP_PORT"
  write_deploy_secret_pair MOONCEN_SMTP_USERNAME "$MOONCEN_SMTP_USERNAME"
  write_deploy_secret_pair MOONCEN_SMTP_PASSWORD "$MOONCEN_SMTP_PASSWORD"
  write_deploy_secret_pair MOONCEN_SMTP_SECURITY "$MOONCEN_SMTP_SECURITY"
  write_deploy_secret_pair OPS_CLOUDFLARE_ANALYTICS_ZONE_ID "$OPS_CLOUDFLARE_ANALYTICS_ZONE_ID"
  write_deploy_secret_pair OPS_CLOUDFLARE_ANALYTICS_TOKEN "$OPS_CLOUDFLARE_ANALYTICS_TOKEN"
  write_deploy_secret_pair MOONCEN_SERVER_MONITOR_TOKEN "$MOONCEN_SERVER_MONITOR_TOKEN"
  printf 'BACKUP_AGE_RECIPIENT=%s\n' "$BACKUP_AGE_RECIPIENT"
  printf 'BACKUP_PORT=%s\n' "$BACKUP_PORT"
} > "$deploy_secret_tmp"
chmod 600 "$deploy_secret_tmp"
deploy_secret_sha256="$(sha256sum "$deploy_secret_tmp")"
deploy_secret_sha256="${deploy_secret_sha256%% *}"
if [[ ! "$deploy_secret_sha256" =~ ^[0-9a-f]{64}$ ]]; then
  echo "Validated deploy secret digest is invalid." >&2
  exit 74
fi
mv -f "$deploy_secret_tmp" "$DEPLOY_SECRET_FILE"
if [ "$DEPLOY_SECRET_FILE" != "$DEPLOY_SECRET_DIR/migrator.env" ]; then
  rm -f "$DEPLOY_SECRET_DIR/migrator.env"
fi

if [ "$SKIP_DB_SETUP" != "1" ]; then
  db_password_b64="$(printf '%s' "$DB_PASSWORD" | base64 | tr -d '\r\n')"
  sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
SET password_encryption = 'scram-sha-256';
SELECT format('CREATE ROLE %I LOGIN', '${DB_MIGRATOR_USER}')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${DB_MIGRATOR_USER}')
\gexec
SELECT format(
  'ALTER ROLE %I WITH LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
  '${DB_MIGRATOR_USER}',
  convert_from(decode('${db_password_b64}', 'base64'), 'UTF8')
) \gexec
SELECT format('REVOKE %I FROM %I', parent.rolname, member.rolname)
FROM pg_auth_members membership
JOIN pg_roles parent ON parent.oid = membership.roleid
JOIN pg_roles member ON member.oid = membership.member
WHERE member.rolname = '${DB_MIGRATOR_USER}'
\gexec
SELECT format('CREATE DATABASE %I OWNER %I', '${DB_NAME}', '${DB_MIGRATOR_USER}')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '${DB_NAME}')
\gexec
SELECT format('ALTER DATABASE %I OWNER TO %I', db.datname, target_owner.rolname)
FROM pg_database db
JOIN pg_roles target_owner ON target_owner.rolname = '${DB_MIGRATOR_USER}'
WHERE db.datname = '${DB_NAME}'
  AND db.datdba <> target_owner.oid
\gexec
SQL

  sudo -u postgres psql -v ON_ERROR_STOP=1 -d "$DB_NAME" <<SQL
CREATE EXTENSION IF NOT EXISTS postgis WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;
-- \gexec runs every generated ALTER in its own autocommit transaction. This
-- prevents ownership convergence from retaining locks across unrelated objects.
SELECT format('ALTER SCHEMA %I OWNER TO %I', namespace.nspname, target_owner.rolname)
FROM pg_namespace namespace
JOIN pg_roles target_owner ON target_owner.rolname = '${DB_MIGRATOR_USER}'
WHERE namespace.nspname IN ('public', 'crawl_staging')
  AND namespace.nspowner <> target_owner.oid
\gexec
GRANT USAGE, CREATE ON SCHEMA public TO "${DB_MIGRATOR_USER}";
SELECT CASE c.relkind
    WHEN 'S' THEN format('ALTER SEQUENCE %I.%I OWNER TO %I', n.nspname, c.relname, target_owner.rolname)
    WHEN 'v' THEN format('ALTER VIEW %I.%I OWNER TO %I', n.nspname, c.relname, target_owner.rolname)
    WHEN 'm' THEN format('ALTER MATERIALIZED VIEW %I.%I OWNER TO %I', n.nspname, c.relname, target_owner.rolname)
    ELSE format('ALTER TABLE %I.%I OWNER TO %I', n.nspname, c.relname, target_owner.rolname)
  END
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_roles target_owner ON target_owner.rolname = '${DB_MIGRATOR_USER}'
WHERE n.nspname IN ('public', 'crawl_staging')
  AND c.relkind IN ('r', 'p', 'v', 'm', 'S')
  AND c.relowner <> target_owner.oid
  AND NOT EXISTS (
    SELECT 1 FROM pg_depend d
    WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e'
  )
\gexec

SELECT format(
    'ALTER FUNCTION %I.%I(%s) OWNER TO %I',
    n.nspname,
    p.proname,
    pg_get_function_identity_arguments(p.oid),
    target_owner.rolname
  )
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
JOIN pg_roles target_owner ON target_owner.rolname = '${DB_MIGRATOR_USER}'
WHERE n.nspname IN ('public', 'crawl_staging')
  AND p.prokind = 'f'
  AND p.proowner <> target_owner.oid
  AND NOT EXISTS (
    SELECT 1 FROM pg_depend d
    WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e'
  )
\gexec
SQL
fi

cors_origins="https://${DOMAIN}"
for alias in $domain_names; do
  if [ "$alias" != "$DOMAIN" ] && [ "$alias" != "_" ]; then
    cors_origins="${cors_origins},https://${alias}"
  fi
done

sudo install -d -o root -g root -m 0751 "$SERVICE_CONFIG_DIR"
# Service environment ownership is an exact allowlist. Do not glob-delete
# retired or locally managed files here: the release guard snapshots only the
# reviewed files written below, and rollback must never expand its scope from
# mutable directory contents discovered after activation.
if [ "$ENABLE_CRAWLER_STAGING" = "1" ]; then
  staging_password_tmp=""
  cleanup_staging_password_tmp() {
    if [ -n "${staging_password_tmp:-}" ] && [ -e "$staging_password_tmp" ]; then
      rm -f -- "$staging_password_tmp"
    fi
  }
  trap cleanup_staging_password_tmp EXIT HUP INT TERM
  if sudo test -L "$CRAWL_STAGING_PASSWORD_FILE" || \
     { sudo test -e "$CRAWL_STAGING_PASSWORD_FILE" && ! sudo test -f "$CRAWL_STAGING_PASSWORD_FILE"; }; then
    echo "Stored crawler staging password path is unsafe." >&2
    exit 78
  fi
  if [ -n "$CRAWL_STAGING_DB_PASSWORD" ]; then
    if [[ ! "$CRAWL_STAGING_DB_PASSWORD" =~ ^[A-Za-z0-9_-]{32,256}$ ]]; then
      echo "CRAWL_STAGING_DB_PASSWORD must be a 32-256 character URL-safe secret." >&2
      exit 64
    fi
    staging_password_tmp="$(mktemp)"
    printf '%s' "$CRAWL_STAGING_DB_PASSWORD" > "$staging_password_tmp"
    sudo install -o root -g root -m 0600 "$staging_password_tmp" "$CRAWL_STAGING_PASSWORD_FILE"
    rm -f -- "$staging_password_tmp"
    staging_password_tmp=""
  elif sudo test -f "$CRAWL_STAGING_PASSWORD_FILE" && ! sudo test -L "$CRAWL_STAGING_PASSWORD_FILE"; then
    if [ "$(sudo stat -c '%U:%G:%a' "$CRAWL_STAGING_PASSWORD_FILE")" != "root:root:600" ]; then
      echo "Stored crawler staging password ownership or mode is unsafe." >&2
      exit 78
    fi
    CRAWL_STAGING_DB_PASSWORD="$(sudo cat "$CRAWL_STAGING_PASSWORD_FILE")"
  else
    staging_password_tmp="$(mktemp)"
    without_runtime_secrets openssl rand -hex 32 > "$staging_password_tmp"
    sudo install -o root -g root -m 0600 "$staging_password_tmp" "$CRAWL_STAGING_PASSWORD_FILE"
    CRAWL_STAGING_DB_PASSWORD="$(sudo cat "$CRAWL_STAGING_PASSWORD_FILE")"
    rm -f -- "$staging_password_tmp"
    staging_password_tmp=""
  fi
  trap - EXIT HUP INT TERM
  if [[ ! "$CRAWL_STAGING_DB_PASSWORD" =~ ^[A-Za-z0-9_-]{32,256}$ ]]; then
    echo "Stored crawler staging password is invalid." >&2
    exit 78
  fi
  for primary_password in \
    "$DB_PASSWORD" "$DB_API_PASSWORD" "$DB_CRAWLER_PASSWORD" \
    "$DB_DEPLOYMENT_WORKER_PASSWORD" "$DB_AI_PASSWORD" \
    "$DB_APPLIER_PASSWORD" "$DB_BACKUP_PASSWORD" "$DB_CHECK_PASSWORD"; do
    if [ -n "$primary_password" ] && [ "$CRAWL_STAGING_DB_PASSWORD" = "$primary_password" ]; then
      echo "Crawler staging password must differ from every primary database password." >&2
      exit 64
    fi
  done
fi
SERVICE_DB_SSLROOTCERT=""
if [ -n "$DB_SSLROOTCERT_SOURCE" ]; then
  service_db_ca="$SERVICE_CONFIG_DIR/db-root-ca.crt"
  source_db_ca="$(sudo readlink -f "$DB_SSLROOTCERT_SOURCE")"
  target_db_ca="$(sudo readlink -m "$service_db_ca")"
  if [ "$source_db_ca" != "$target_db_ca" ]; then
    sudo install -o root -g "$DB_TLS_GROUP" -m 0640 "$source_db_ca" "$service_db_ca"
  else
    sudo chown root:"$DB_TLS_GROUP" "$service_db_ca"
    sudo chmod 0640 "$service_db_ca"
  fi
  SERVICE_DB_SSLROOTCERT="$service_db_ca"
fi

# This compatibility file intentionally contains no credentials. Operator
# tooling reads it for provider lists and local endpoints, while every service
# receives its own root-owned EnvironmentFile below.
install -m 0644 /dev/null "$APP_DIR/.env"
cat > "$APP_DIR/.env" <<ENV
DB_HOST=localhost
DB_PORT=5432
DB_NAME=${DB_NAME}
VITE_SITE_URL=https://${DOMAIN}
ENVIRONMENT=production
API_HOST=127.0.0.1
API_PORT=8001
FRONTEND_HOST=127.0.0.1
FRONTEND_PORT=5173
OLLAMA_HOST=${OLLAMA_HOST}
OLLAMA_HOSTS=${OLLAMA_HOSTS}
OLLAMA_MODEL=${OLLAMA_MODEL}
CRAWLER_PROVIDERS="MUNI_DOKSEODANG_SD_GO_KR_A8C20229 MUNI_JANGAN_SUWON_GO_KR_D82A0EAE MUNI_LEARNING_SUWON_GO_KR_3AF2DB76 MUNI_LEARNING_SUWON_GO_KR_402954DA MUNI_LEARNING_SUWON_GO_KR_6ABE3488 MUNI_LEARNING_SUWON_GO_KR_A915395E MUNI_PALDAL_SUWON_GO_KR_7F5BC8C6 MUNI_PALDAL_SUWON_GO_KR_D78BD1B4 MUNI_YEYAK_SYF_OR_KR_7D3E2EF5 HYUNDAI_DEPT LOTTE_MART GALLERIA AK_PLAZA ELAND_RETAIL SHINSEGAE_ACADEMY HOMEPLUS EMART LOTTE SEONGNAM_BAEUMSOOP ANYANG_LIFELONG_LEARNING YONGIN_LIFELONG_LEARNING BUSAN_RESERVATION BABSANG_WELFARE_PROGRAM DAEGU_RESERVATION DAEJEON_OK_RESERVATION GWANGJU_RESERVATION INCHEON_RESERVATION MUNI_WWW_DANGJIN_GO_KR_3C378AA6 MUNI_WWW_GURO_GO_KR_A4A5D3E3 MUNI_WWW_DAEDEOK_GO_KR_360B9B7C MUNI_SUGANG_SEONGNAM_GO_KR_4D24781E MUNI_SUGANG_SEONGNAM_GO_KR_D447262D MUNI_SUGANG_SEONGNAM_GO_KR_FAA99A7B MUNI_WWW_YANGJU_GO_KR_1A2AECAC MUNI_WWW_GONGJU_GO_KR_7CBA2D38 SEOSAN_WELFARE_TOTAL_RESERVATION SEOUL_PUBLIC_SERVICE MUNI_ORG_JJE_GO_KR_3205C1E8 MUNI_MBIS_POHANG_GO_KR_0407D99A MUNI_SEJONG_NL_GO_KR_7F55E25D EXPERIENCE_TARGETS MUNICIPAL_RESERVATION_TARGETS"
ENV
chmod 0644 "$APP_DIR/.env"

install_service_env api.env "$API_OS_USER" <<ENV
DB_SSLROOTCERT=${SERVICE_DB_SSLROOTCERT}
DB_HOST=localhost
DB_PORT=5432
DB_NAME=${DB_NAME}
DB_OWNER_USER=${DB_MIGRATOR_USER}
DB_API_USER=${DB_API_USER}
DB_API_PASSWORD=${DB_API_PASSWORD}
DB_POOL_MIN=${DB_POOL_MIN:-1}
DB_POOL_MAX=${DB_POOL_MAX:-8}
ENVIRONMENT=production
API_HOST=127.0.0.1
API_PORT=8001
API_WORKERS=2
MOONCEN_CORS_ORIGINS=${cors_origins}
MOONCEN_TRUSTED_HOSTS=${trusted_hosts}
AUTH_SECRET=${AUTH_SECRET}
MOONCEN_OPS_SINGLE_ACCOUNT_ONLY=true
MOONCEN_OPS_LOGIN_ID=${MOONCEN_OPS_LOGIN_ID}
MOONCEN_OPS_PASSWORD_HASH=${MOONCEN_OPS_PASSWORD_HASH}
NAVER_OAUTH_CLIENT_ID=${NAVER_OAUTH_CLIENT_ID}
NAVER_OAUTH_CLIENT_SECRET=${NAVER_OAUTH_CLIENT_SECRET}
GOOGLE_OAUTH_CLIENT_ID=${GOOGLE_OAUTH_CLIENT_ID}
GOOGLE_OAUTH_CLIENT_SECRET=${GOOGLE_OAUTH_CLIENT_SECRET}
OAUTH_REDIRECT_URIS=${OAUTH_REDIRECT_URI}
MOONCEN_ADMIN_EMAILS=${MOONCEN_ADMIN_EMAILS}
MOONCEN_ADMIN_PROVIDER_IDS=${MOONCEN_ADMIN_PROVIDER_IDS}
MOONCEN_BUG_REPORT_TO=${MOONCEN_BUG_REPORT_TO}
MOONCEN_BUG_REPORT_FROM=${MOONCEN_BUG_REPORT_FROM}
MOONCEN_SMTP_HOST=${MOONCEN_SMTP_HOST}
MOONCEN_SMTP_PORT=${MOONCEN_SMTP_PORT}
MOONCEN_SMTP_USERNAME=${MOONCEN_SMTP_USERNAME}
MOONCEN_SMTP_PASSWORD=${MOONCEN_SMTP_PASSWORD}
MOONCEN_SMTP_SECURITY=${MOONCEN_SMTP_SECURITY}
OPS_CLOUDFLARE_ANALYTICS_ZONE_ID=${OPS_CLOUDFLARE_ANALYTICS_ZONE_ID}
OPS_CLOUDFLARE_ANALYTICS_TOKEN=${OPS_CLOUDFLARE_ANALYTICS_TOKEN}
MOONCEN_SERVER_MONITOR_TOKEN=${MOONCEN_SERVER_MONITOR_TOKEN}
VITE_SITE_URL=https://${DOMAIN}
SITE_URL=https://${DOMAIN}
ENV

install_container_env container-api.env <<ENV
ENVIRONMENT=production
DB_NAME=${DB_NAME}
DB_API_USER=${DB_API_USER}
DB_API_PASSWORD=${DB_API_PASSWORD}
DB_POOL_MIN=${DB_POOL_MIN:-1}
DB_POOL_MAX=${DB_POOL_MAX:-8}
MOONCEN_CORS_ORIGINS=${cors_origins}
MOONCEN_TRUSTED_HOSTS=${trusted_hosts}
AUTH_SECRET=${AUTH_SECRET}
NAVER_OAUTH_CLIENT_ID=${NAVER_OAUTH_CLIENT_ID}
NAVER_OAUTH_CLIENT_SECRET=${NAVER_OAUTH_CLIENT_SECRET}
GOOGLE_OAUTH_CLIENT_ID=${GOOGLE_OAUTH_CLIENT_ID}
GOOGLE_OAUTH_CLIENT_SECRET=${GOOGLE_OAUTH_CLIENT_SECRET}
OAUTH_REDIRECT_URIS=${OAUTH_REDIRECT_URI}
MOONCEN_ADMIN_EMAILS=${MOONCEN_ADMIN_EMAILS}
MOONCEN_ADMIN_PROVIDER_IDS=${MOONCEN_ADMIN_PROVIDER_IDS}
MOONCEN_BUG_REPORT_TO=${MOONCEN_BUG_REPORT_TO}
MOONCEN_BUG_REPORT_FROM=${MOONCEN_BUG_REPORT_FROM}
MOONCEN_SMTP_HOST=${MOONCEN_SMTP_HOST}
MOONCEN_SMTP_PORT=${MOONCEN_SMTP_PORT}
MOONCEN_SMTP_USERNAME=${MOONCEN_SMTP_USERNAME}
MOONCEN_SMTP_PASSWORD=${MOONCEN_SMTP_PASSWORD}
MOONCEN_SMTP_SECURITY=${MOONCEN_SMTP_SECURITY}
SITE_URL=https://${DOMAIN}
ENV

install_container_env container-migrator.env <<ENV
DB_NAME=${DB_NAME}
DB_USER=${DB_MIGRATOR_USER}
DB_PASSWORD=${DB_PASSWORD}
ENV

install_service_env frontend.env "$FRONTEND_OS_USER" <<ENV
FRONTEND_HOST=127.0.0.1
FRONTEND_PORT=5173
NODE_ENV=production
ENV

container_runtime_config="$SERVICE_CONFIG_DIR/container-frontend-runtime-config.js"
container_runtime_stage="$(sudo mktemp "$SERVICE_CONFIG_DIR/.container-runtime-config.XXXXXX")"
if ! sudo env -i \
    PATH=/usr/bin:/bin \
    MOONCEN_SITE_URL="https://${DOMAIN}" \
    MOONCEN_OAUTH_REDIRECT_URI="$OAUTH_REDIRECT_URI" \
    MOONCEN_KAKAO_MAPS_JAVASCRIPT_KEY="$KAKAO_MAPS_JAVASCRIPT_KEY" \
    MOONCEN_GOOGLE_OAUTH_CLIENT_ID="$GOOGLE_OAUTH_CLIENT_ID" \
    MOONCEN_NAVER_OAUTH_CLIENT_ID="$NAVER_OAUTH_CLIENT_ID" \
    /usr/bin/python3 -I "$APP_DIR/deploy/docker/render_runtime_config.py" \
      --output "$container_runtime_stage" ||
   ! sudo chown root:root "$container_runtime_stage" ||
   ! sudo chmod 0644 "$container_runtime_stage" ||
   ! sudo sync -f -- "$container_runtime_stage" ||
   ! sudo mv -fT -- "$container_runtime_stage" "$container_runtime_config"; then
  sudo rm -f -- "$container_runtime_stage"
  echo "Container frontend runtime configuration could not be installed." >&2
  exit 74
fi
sudo sync -f -- "$SERVICE_CONFIG_DIR"

if [ "$ENABLE_CRAWLER_STAGING" = "1" ]; then
  install_service_env crawler.env "$CRAWLER_OS_USER" <<ENV
ENVIRONMENT=production
TZ=Asia/Seoul
CRAWL_WRITE_MODE=staging
DB_SSLROOTCERT=${SERVICE_DB_SSLROOTCERT}
DB_HOST=${CRAWL_STAGING_DB_HOST}
DB_PORT=${CRAWL_STAGING_DB_PORT}
DB_NAME=${CRAWL_STAGING_DB_NAME}
DB_CRAWLER_USER=${CRAWL_STAGING_DB_USER}
DB_CRAWLER_PASSWORD=${CRAWL_STAGING_DB_PASSWORD}
CRAWL_STAGING_DB_HOST=${CRAWL_STAGING_DB_HOST}
CRAWL_STAGING_DB_PORT=${CRAWL_STAGING_DB_PORT}
CRAWL_STAGING_DB_NAME=${CRAWL_STAGING_DB_NAME}
CRAWL_STAGING_DB_USER=${CRAWL_STAGING_DB_USER}
CRAWL_STAGING_DB_PASSWORD=${CRAWL_STAGING_DB_PASSWORD}
DB_POOL_MIN=${DB_POOL_MIN:-1}
DB_POOL_MAX=${DB_POOL_MAX:-8}
KAKAO_MAPS_REST_API_KEY=${KAKAO_MAPS_REST_API_KEY}
KAKAO_GEOCODE_MAX_REQUESTS_PER_RUN=1000
SITE_URL=https://${DOMAIN}
CHROME_BINARY=/usr/local/bin/mooncen-chrome
CHROMEDRIVER=/usr/local/bin/mooncen-chromedriver
SELENIUM_PAGE_LOAD_TIMEOUT_SECONDS=45
SELENIUM_SCRIPT_TIMEOUT_SECONDS=30
CRAWLER_PROVIDERS="MUNI_DOKSEODANG_SD_GO_KR_A8C20229 MUNI_JANGAN_SUWON_GO_KR_D82A0EAE MUNI_LEARNING_SUWON_GO_KR_3AF2DB76 MUNI_LEARNING_SUWON_GO_KR_402954DA MUNI_LEARNING_SUWON_GO_KR_6ABE3488 MUNI_LEARNING_SUWON_GO_KR_A915395E MUNI_PALDAL_SUWON_GO_KR_7F5BC8C6 MUNI_PALDAL_SUWON_GO_KR_D78BD1B4 MUNI_YEYAK_SYF_OR_KR_7D3E2EF5 HYUNDAI_DEPT LOTTE_MART GALLERIA AK_PLAZA ELAND_RETAIL SHINSEGAE_ACADEMY HOMEPLUS EMART LOTTE SEONGNAM_BAEUMSOOP ANYANG_LIFELONG_LEARNING YONGIN_LIFELONG_LEARNING BUSAN_RESERVATION BABSANG_WELFARE_PROGRAM DAEGU_RESERVATION DAEJEON_OK_RESERVATION GWANGJU_RESERVATION INCHEON_RESERVATION MUNI_WWW_DANGJIN_GO_KR_3C378AA6 MUNI_WWW_GURO_GO_KR_A4A5D3E3 MUNI_WWW_DAEDEOK_GO_KR_360B9B7C MUNI_SUGANG_SEONGNAM_GO_KR_4D24781E MUNI_SUGANG_SEONGNAM_GO_KR_D447262D MUNI_SUGANG_SEONGNAM_GO_KR_FAA99A7B MUNI_WWW_YANGJU_GO_KR_1A2AECAC MUNI_WWW_GONGJU_GO_KR_7CBA2D38 SEOSAN_WELFARE_TOTAL_RESERVATION SEOUL_PUBLIC_SERVICE MUNI_ORG_JJE_GO_KR_3205C1E8 MUNI_MBIS_POHANG_GO_KR_0407D99A MUNI_SEJONG_NL_GO_KR_7F55E25D EXPERIENCE_TARGETS MUNICIPAL_RESERVATION_TARGETS"
CRAWLER_MAX_WORKERS=4
CRAWLER_RUN_INTERVAL=86400
CRAWLER_COORDINATE_BACKFILL_LIMIT=100
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
ENV
fi

install_service_env ai.env "$AI_OS_USER" <<ENV
ENVIRONMENT=production
DB_SSLROOTCERT=${SERVICE_DB_SSLROOTCERT}
DB_HOST=localhost
DB_PORT=5432
DB_NAME=${DB_NAME}
DB_RUNTIME_USER=${DB_AI_USER}
DB_RUNTIME_PASSWORD=${DB_AI_PASSWORD}
DB_APPLICATION_NAME=mooncen-ai
DB_POOL_MIN=${DB_POOL_MIN:-1}
DB_POOL_MAX=${DB_POOL_MAX:-8}
OLLAMA_HOST=${OLLAMA_HOST}
OLLAMA_HOSTS=${OLLAMA_HOSTS}
OLLAMA_MODEL=${OLLAMA_MODEL}
AI_PROVIDER=OLLAMA
AI_WORKERS=${AI_WORKERS:-2}
AI_BATCH_SIZE=20
AI_DELAY=0
AI_POLL_INTERVAL=60
AI_ACTIVE_START=22:00
AI_ACTIVE_END=07:00
AI_WEEKEND_24H=1
ENV

install_container_env container-ai.env <<ENV
ENVIRONMENT=production
DB_NAME=${DB_NAME}
DB_RUNTIME_USER=${DB_AI_USER}
DB_RUNTIME_PASSWORD=${DB_AI_PASSWORD}
DB_APPLICATION_NAME=mooncen-ai
DB_POOL_MIN=${DB_POOL_MIN:-1}
DB_POOL_MAX=${DB_POOL_MAX:-8}
OLLAMA_HOST=${OLLAMA_HOST}
OLLAMA_HOSTS=${OLLAMA_HOSTS}
OLLAMA_MODEL=${OLLAMA_MODEL}
AI_PROVIDER=OLLAMA
AI_WORKERS=${AI_WORKERS:-2}
AI_BATCH_SIZE=20
AI_DELAY=0
AI_POLL_INTERVAL=60
AI_ACTIVE_START=22:00
AI_ACTIVE_END=07:00
AI_WEEKEND_24H=1
ENV

install_service_env bot.env "$BOT_OS_USER" <<ENV
APP_DIR=${APP_DIR}
MOONCEN_BOT_STATE_DIR=/var/lib/mooncen-bot
MOONCEN_BOT_STATE_FILE=/var/lib/mooncen-bot/bot_state.json
FAILOVER_LOG=${APP_DIR}/failover/failover.log
FAIL_COUNT_FILE=${APP_DIR}/failover/cloud_fail_count
ENABLE_FILE=${APP_DIR}/failover/enable_auto_failover
CLOUDFLARE_GATE_DISABLE_FILE=${APP_DIR}/failover/disable_cloudflare_gate
MOONCEN_BOT_TOKEN=${MOONCEN_BOT_TOKEN}
MOONCEN_BOT_CHAT_ID=${MOONCEN_BOT_CHAT_ID}
MOONCEN_BOT_MONITOR_INTERVAL=10
MOONCEN_OPS_HOST=127.0.0.1
MOONCEN_OPS_PORT=8765
OLLAMA_HOST=${OLLAMA_HOST}
OLLAMA_HOSTS=${OLLAMA_HOSTS}
OLLAMA_MODEL=${OLLAMA_MODEL}
ENV

install_service_env applier.env "$APPLIER_OS_USER" <<ENV
ENVIRONMENT=production
DB_SSLROOTCERT=${SERVICE_DB_SSLROOTCERT}
DB_HOST=localhost
DB_PORT=5432
DB_NAME=${DB_NAME}
CRAWL_STAGING_DB_HOST=${CRAWL_STAGING_DB_HOST}
CRAWL_STAGING_DB_PORT=${CRAWL_STAGING_DB_PORT}
CRAWL_STAGING_DB_NAME=${CRAWL_STAGING_DB_NAME}
CRAWL_STAGING_DB_USER=${CRAWL_STAGING_DB_USER}
CRAWL_STAGING_DB_PASSWORD=${CRAWL_STAGING_DB_PASSWORD}
PRIMARY_DB_HOST=${PRIMARY_DB_HOST_EFFECTIVE}
PRIMARY_DB_PORT=${PRIMARY_DB_PORT:-5432}
PRIMARY_DB_NAME=${PRIMARY_DB_NAME:-${DB_NAME}}
PRIMARY_DB_USER=${DB_APPLIER_USER}
PRIMARY_DB_PASSWORD=${DB_APPLIER_PASSWORD}
STAGING_CLOSE_MIN_RATIO=${STAGING_CLOSE_MIN_RATIO:-0.65}
STAGING_CLOSE_MAX_ABSOLUTE_DROP=${STAGING_CLOSE_MAX_ABSOLUTE_DROP:-2000}
STAGING_CLOSE_RATIO_BASELINE=${STAGING_CLOSE_RATIO_BASELINE:-20}
ENV

install_service_env functional-test.env "$FUNCTIONAL_OS_USER" <<ENV
ENVIRONMENT=production
DB_HOST=localhost
DB_PORT=5432
DB_NAME=${DB_NAME}
DB_RUNTIME_USER=${DB_CHECK_USER}
DB_RUNTIME_PASSWORD=${DB_CHECK_PASSWORD}
DB_APPLICATION_NAME=mooncen-functional-test
DB_SSLROOTCERT=${SERVICE_DB_SSLROOTCERT}
MOONCEN_FUNCTIONAL_TEST_BASE_URL=https://${DOMAIN}
MOONCEN_FUNCTIONAL_TEST_INTERNAL_API_URL=http://127.0.0.1:8001
MOONCEN_FUNCTIONAL_TEST_REPORT_DIR=/var/lib/mooncen-check
MOONCEN_BOT_TOKEN=${MOONCEN_BOT_TOKEN}
MOONCEN_BOT_CHAT_ID=${MOONCEN_BOT_CHAT_ID}
ENV

install_service_env gate.env root <<ENV
APP_DIR=${APP_DIR}
DB_NAME=${DB_NAME}
FRONTEND_PORT=5173
CLOUDFLARE_GATE_API_HEALTH_URL=http://127.0.0.1:8001/health
CLOUDFLARE_GATE_NGINX_HEALTH_URL=http://127.0.0.1/health
CLOUDFLARE_GATE_FRONTEND_URL=http://127.0.0.1:5173
ENV

sudo install -d -o root -g root -m 0751 "$(dirname "$BACKUP_ENV_FILE")"
sudo install -o root -g "$BACKUP_OS_GROUP" -m 0640 /dev/null "$BACKUP_ENV_FILE"
sudo tee "$BACKUP_ENV_FILE" >/dev/null <<ENV
DB_HOST=localhost
DB_PORT=5432
DB_NAME=${DB_NAME}
DB_OWNER_USER=${DB_MIGRATOR_USER}
DB_BACKUP_USER=${DB_BACKUP_USER}
DB_BACKUP_PASSWORD=${DB_BACKUP_PASSWORD}
BACKUP_HOST=wtr-nas
BACKUP_USER=mooncen_backup
BACKUP_ROOT=/volume2/homes/mooncen_backup/mooncen-backup
BACKUP_IDENTITY_FILE=${BACKUP_IDENTITY_FILE}
BACKUP_KNOWN_HOSTS_FILE=${BACKUP_KNOWN_HOSTS_FILE}
BACKUP_ALLOW_TAILSCALE_IP=1
BACKUP_SSH_CONFIG=/dev/null
BACKUP_PORT=${BACKUP_PORT}
BACKUP_AGE_RECIPIENT=${BACKUP_AGE_RECIPIENT}
BACKUP_AGE_IDENTITY_FILE=${BACKUP_AGE_IDENTITY_FILE}
BACKUP_MANIFEST_SIGNING_KEY=${BACKUP_MANIFEST_SIGNING_KEY}
BACKUP_MANIFEST_ALLOWED_SIGNERS=${BACKUP_MANIFEST_ALLOWED_SIGNERS}
BACKUP_RETENTION_DAYS=${BACKUP_RETENTION_DAYS}
BACKUP_MAX_ENCRYPTED_DUMP_BYTES=${BACKUP_MAX_ENCRYPTED_DUMP_BYTES}
BACKUP_MAX_DECRYPTED_DUMP_BYTES=${BACKUP_MAX_DECRYPTED_DUMP_BYTES}
BACKUP_LOCAL_MIN_FREE_BYTES=${BACKUP_LOCAL_MIN_FREE_BYTES}
BACKUP_DB_MIN_FREE_BYTES=${BACKUP_DB_MIN_FREE_BYTES}
BACKUP_RESTORE_EXPANSION_FACTOR=${BACKUP_RESTORE_EXPANSION_FACTOR}
BACKUP_MAX_AGE_SECONDS=${BACKUP_MAX_AGE_SECONDS}
BACKUP_MAX_APP_ARCHIVE_BYTES=${BACKUP_MAX_APP_ARCHIVE_BYTES}
BACKUP_MAX_CONFIG_ARCHIVE_BYTES=${BACKUP_MAX_CONFIG_ARCHIVE_BYTES}
BACKUP_MAX_SOURCE_DB_BYTES=${BACKUP_MAX_SOURCE_DB_BYTES}
ENV
sudo chown root:"$BACKUP_OS_GROUP" "$BACKUP_ENV_FILE"
sudo chmod 0640 "$BACKUP_ENV_FILE"

CONTAINER_PG_HBA_SOURCE="$APP_DIR/deploy/ubuntu/configure_container_pg_hba.py"
CONTAINER_PG_HBA_HELPER=/usr/local/libexec/mooncen-configure-container-pg-hba
NATIVE_RUNTIME_CONDITION_SOURCE="$APP_DIR/deploy/ubuntu/mooncen_native_runtime_condition.py"
NATIVE_RUNTIME_CONDITION_HELPER=/usr/local/libexec/mooncen-native-runtime-condition
AN2P_CONTROL_SECRETS_EXPORT_SOURCE="$APP_DIR/deploy/ubuntu/export_an2p_control_secrets.py"
AN2P_CONTROL_SECRETS_EXPORT_HELPER=/usr/local/libexec/mooncen-export-an2p-control-secrets

install_root_runtime_helper() {
  local helper_source="$1"
  local helper_target="$2"
  local helper_stage
  [ -f "$helper_source" ] && [ ! -L "$helper_source" ] || {
    echo "Required container runtime helper source is unavailable or unsafe." >&2
    exit 78
  }
  helper_stage="${helper_target}.staged.$$"
  if sudo test -e "$helper_stage" || sudo test -L "$helper_stage"; then
    echo "Container runtime helper staging path is unsafe." >&2
    exit 78
  fi
  sudo install -o root -g root -m 0755 "$helper_source" "$helper_stage"
  sudo mv -fT -- "$helper_stage" "$helper_target"
  sudo test -f "$helper_target" && ! sudo test -L "$helper_target" &&
    [ "$(sudo stat -c '%U:%G:%a' "$helper_target")" = root:root:755 ] || {
      echo "Installed container runtime helper metadata is unsafe." >&2
      exit 78
    }
}

# A previous exporter must not remain callable while the database/HBA contract
# is being changed. It is restored only after every live authorization probe
# and the root-only source commit have succeeded.
if sudo test -e "$AN2P_CONTROL_SECRETS_EXPORT_HELPER" ||
   sudo test -L "$AN2P_CONTROL_SECRETS_EXPORT_HELPER"; then
  sudo rm -f -- "$AN2P_CONTROL_SECRETS_EXPORT_HELPER"
fi
if sudo test -e "$AN2P_CONTROL_SECRETS_EXPORT_HELPER" ||
   sudo test -L "$AN2P_CONTROL_SECRETS_EXPORT_HELPER"; then
  echo "Deployment control-secret exporter could not be revoked." >&2
  exit 78
fi
install_root_runtime_helper "$CONTAINER_PG_HBA_SOURCE" "$CONTAINER_PG_HBA_HELPER"

if [ "$SKIP_DB_SETUP" != "1" ]; then
DB_USE_MIGRATOR=1 \
DB_HOST=localhost \
DB_PORT=5432 \
DB_NAME="$DB_NAME" \
DB_USER="$DB_MIGRATOR_USER" \
DB_PASSWORD="$DB_PASSWORD" \
  "$APP_DIR/.venv/bin/python" "$APP_DIR/DB/setup_db.py" --mode migrate

sudo cat "$APP_DIR/DB/roles.sql" |
  sudo -u postgres psql -v ON_ERROR_STOP=1 -d "$DB_NAME"

db_deployment_worker_password_b64="$(printf '%s' "$DB_DEPLOYMENT_WORKER_PASSWORD" | base64 | tr -d '\r\n')"
{
  printf "SET password_encryption = 'scram-sha-256';\n"
  printf '\\set db_deployment_worker_user %s\n' "$DB_DEPLOYMENT_WORKER_USER"
  printf '\\set db_deployment_worker_password_b64 %s\n' "$db_deployment_worker_password_b64"
  cat "$APP_DIR/DB/provision_deployment_worker_login.sql"
} | sudo -u postgres psql -v ON_ERROR_STOP=1 -d "$DB_NAME"

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
SELECT format(
  'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT SELECT ON TABLES TO mooncen_readonly',
  '${DB_MIGRATOR_USER}'
) \gexec
SELECT format(
  'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT SELECT ON SEQUENCES TO mooncen_readonly',
  '${DB_MIGRATOR_USER}'
) \gexec
SELECT format(
  'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA crawl_staging GRANT SELECT ON TABLES TO mooncen_readonly',
  '${DB_MIGRATOR_USER}'
)
WHERE EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'crawl_staging')
\gexec
SELECT format(
  'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA crawl_staging GRANT SELECT ON SEQUENCES TO mooncen_readonly',
  '${DB_MIGRATOR_USER}'
)
WHERE EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'crawl_staging')
\gexec
SQL

db_role_contract="$(sudo -u postgres psql -At -v ON_ERROR_STOP=1 -d "$DB_NAME" -c "
WITH public_sequences AS MATERIALIZED (
  SELECT class.oid
  FROM pg_class class
  JOIN pg_namespace namespace ON namespace.oid = class.relnamespace
  WHERE namespace.nspname = 'public'
    AND class.relkind = 'S'
)
SELECT
  has_table_privilege('${DB_API_USER}', 'courses', 'SELECT')
  AND has_column_privilege('${DB_API_USER}', 'courses', 'view_count', 'UPDATE')
  AND has_function_privilege(
    '${DB_API_USER}',
    'public.mooncen_raw_url_fingerprint(text)',
    'EXECUTE'
  )
  AND NOT has_column_privilege('${DB_API_USER}', 'courses', 'title', 'UPDATE')
  AND has_table_privilege('${DB_API_USER}', 'users', 'SELECT')
  AND has_table_privilege('${DB_API_USER}', 'users', 'INSERT')
  AND has_table_privilege('${DB_API_USER}', 'users', 'UPDATE')
  AND has_table_privilege('${DB_API_USER}', 'users', 'DELETE')
  AND has_table_privilege('${DB_CRAWLER_USER}', 'courses', 'SELECT')
  AND has_table_privilege('${DB_CRAWLER_USER}', 'courses', 'INSERT')
  AND has_table_privilege('${DB_CRAWLER_USER}', 'courses', 'UPDATE')
  AND has_table_privilege('${DB_CRAWLER_USER}', 'courses', 'DELETE')
  AND NOT has_table_privilege('${DB_CRAWLER_USER}', 'users', 'SELECT')
  AND has_column_privilege('${DB_AI_USER}', 'courses', 'id', 'SELECT')
  AND has_column_privilege('${DB_AI_USER}', 'courses', 'description', 'SELECT')
  AND has_column_privilege('${DB_AI_USER}', 'courses', 'created_at', 'SELECT')
  AND has_column_privilege('${DB_AI_USER}', 'courses', 'ai_summary', 'UPDATE')
  AND has_column_privilege('${DB_AI_USER}', 'courses', 'title', 'UPDATE')
  AND has_column_privilege('${DB_AI_USER}', 'courses', 'target_age_is_explicit', 'UPDATE')
  AND has_function_privilege(
    '${DB_AI_USER}',
    'public.mooncen_raw_url_fingerprint(text)',
    'EXECUTE'
  )
  AND has_function_privilege(
    '${DB_AI_USER}',
    'public.mooncen_search_ngrams(text)',
    'EXECUTE'
  )
  AND has_function_privilege(
    '${DB_AI_USER}',
    'public.mooncen_text_contains_any(text,text[])',
    'EXECUTE'
  )
  AND has_function_privilege(
    '${DB_AI_USER}',
    'public.mooncen_infer_course_service_group(text,text,text,text,text,text,text,text,text,text,text)',
    'EXECUTE'
  )
  AND has_function_privilege(
    '${DB_AI_USER}',
    'public.mooncen_resolve_course_service_group(text,text,text,text,text,text,text,text,text,text,text,text)',
    'EXECUTE'
  )
  AND NOT has_column_privilege('${DB_AI_USER}', 'courses', 'raw_url', 'SELECT')
  AND NOT has_column_privilege('${DB_AI_USER}', 'courses', 'raw_url', 'UPDATE')
  AND NOT has_table_privilege('${DB_AI_USER}', 'branches', 'SELECT')
  AND NOT has_table_privilege('${DB_AI_USER}', 'users', 'SELECT')
  AND NOT has_table_privilege('${DB_AI_USER}', 'courses', 'INSERT')
  AND NOT has_table_privilege('${DB_AI_USER}', 'courses', 'DELETE')
  AND has_table_privilege('${DB_APPLIER_USER}', 'courses', 'SELECT')
  AND has_table_privilege('${DB_APPLIER_USER}', 'courses', 'INSERT')
  AND has_table_privilege('${DB_APPLIER_USER}', 'courses', 'UPDATE')
  AND NOT has_table_privilege('${DB_APPLIER_USER}', 'courses', 'DELETE')
  AND NOT has_table_privilege('${DB_APPLIER_USER}', 'users', 'SELECT')
  AND has_table_privilege('${DB_BACKUP_USER}', 'users', 'SELECT')
  AND NOT has_table_privilege('${DB_BACKUP_USER}', 'users', 'INSERT')
  AND has_table_privilege('${DB_CHECK_USER}', 'courses', 'SELECT')
  AND has_table_privilege('${DB_CHECK_USER}', 'branches', 'SELECT')
  AND NOT has_table_privilege('${DB_CHECK_USER}', 'courses', 'INSERT')
  AND NOT has_table_privilege('${DB_CHECK_USER}', 'courses', 'UPDATE')
  AND NOT has_table_privilege('${DB_CHECK_USER}', 'courses', 'DELETE')
  AND NOT has_table_privilege('${DB_CHECK_USER}', 'branches', 'INSERT')
  AND NOT has_table_privilege('${DB_CHECK_USER}', 'branches', 'UPDATE')
  AND NOT has_table_privilege('${DB_CHECK_USER}', 'branches', 'DELETE')
  AND NOT has_table_privilege('${DB_CHECK_USER}', 'users', 'SELECT')
  AND NOT has_schema_privilege('${DB_API_USER}', 'public', 'CREATE')
  AND NOT has_schema_privilege('${DB_CRAWLER_USER}', 'public', 'CREATE')
  AND NOT has_schema_privilege('${DB_AI_USER}', 'public', 'CREATE')
  AND NOT has_schema_privilege('${DB_APPLIER_USER}', 'public', 'CREATE')
  AND NOT has_schema_privilege('${DB_BACKUP_USER}', 'public', 'CREATE')
  AND NOT has_schema_privilege('${DB_CHECK_USER}', 'public', 'CREATE')
  AND NOT has_database_privilege('${DB_API_USER}', '${DB_NAME}', 'CREATE')
  AND NOT has_database_privilege('${DB_CRAWLER_USER}', '${DB_NAME}', 'CREATE')
  AND NOT has_database_privilege('${DB_AI_USER}', '${DB_NAME}', 'CREATE')
  AND NOT has_database_privilege('${DB_APPLIER_USER}', '${DB_NAME}', 'CREATE')
  AND NOT has_database_privilege('${DB_BACKUP_USER}', '${DB_NAME}', 'CREATE')
  AND NOT has_database_privilege('${DB_CHECK_USER}', '${DB_NAME}', 'CREATE')
  AND NOT has_database_privilege('${DB_API_USER}', '${DB_NAME}', 'TEMPORARY')
  AND has_database_privilege('${DB_CRAWLER_USER}', '${DB_NAME}', 'TEMPORARY')
  AND NOT has_database_privilege('${DB_AI_USER}', '${DB_NAME}', 'TEMPORARY')
  AND has_database_privilege('${DB_APPLIER_USER}', '${DB_NAME}', 'TEMPORARY')
  AND NOT has_database_privilege('${DB_BACKUP_USER}', '${DB_NAME}', 'TEMPORARY')
  AND NOT has_database_privilege('${DB_CHECK_USER}', '${DB_NAME}', 'TEMPORARY')
  AND NOT has_database_privilege('${DB_DEPLOYMENT_WORKER_USER}', '${DB_NAME}', 'TEMPORARY')
  AND has_table_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_container_releases', 'SELECT')
  AND has_table_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_container_releases', 'INSERT')
  AND NOT has_table_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_container_releases', 'UPDATE')
  AND NOT has_table_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_container_releases', 'DELETE')
  AND has_table_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_container_validation_receipts', 'SELECT')
  AND has_table_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_container_validation_receipts', 'INSERT')
  AND NOT has_table_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_container_validation_receipts', 'UPDATE')
  AND NOT has_table_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_container_validation_receipts', 'DELETE')
  AND has_table_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_container_approval_evidence', 'SELECT')
  AND NOT has_table_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_container_approval_evidence', 'INSERT')
  AND NOT has_table_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_container_approval_evidence', 'UPDATE')
  AND NOT has_table_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_container_approval_evidence', 'DELETE')
  AND has_table_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_agents', 'SELECT')
  AND has_table_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_agents', 'INSERT')
  AND has_table_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_agents', 'UPDATE')
  AND NOT has_any_column_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_agents', 'REFERENCES')
  AND has_table_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_jobs', 'SELECT')
  AND has_column_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_jobs', 'lease_token', 'UPDATE')
  AND has_column_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_jobs', 'lease_epoch', 'UPDATE')
  AND has_column_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_jobs', 'leased_until', 'UPDATE')
  AND NOT has_column_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_jobs', 'job_type', 'UPDATE')
  AND NOT has_column_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_jobs', 'parameters', 'UPDATE')
  AND has_table_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_deployments', 'SELECT')
  AND has_column_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_deployments', 'runtime_generation', 'UPDATE')
  AND NOT has_column_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_deployments', 'deployment_mode', 'UPDATE')
  AND has_table_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_job_logs', 'SELECT')
  AND has_table_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_job_logs', 'INSERT')
  AND NOT has_table_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_job_logs', 'UPDATE')
  AND NOT has_table_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_job_logs', 'DELETE')
  AND NOT has_any_column_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_job_logs', 'REFERENCES')
  AND has_sequence_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_job_logs_id_seq', 'USAGE')
  AND has_sequence_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_job_logs_id_seq', 'SELECT')
  AND has_sequence_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_container_deployment_lease_epoch_seq', 'USAGE')
  AND has_sequence_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_container_deployment_lease_epoch_seq', 'SELECT')
  AND NOT has_sequence_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_job_logs_id_seq', 'UPDATE')
  AND NOT has_sequence_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_container_deployment_lease_epoch_seq', 'UPDATE')
  AND NOT has_any_column_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_jobs', 'INSERT')
  AND NOT has_table_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_jobs', 'DELETE')
  AND NOT has_table_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_jobs', 'TRUNCATE')
  AND NOT has_table_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_jobs', 'TRIGGER')
  AND NOT has_any_column_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_jobs', 'REFERENCES')
  AND NOT EXISTS (
    SELECT 1
    FROM pg_attribute attribute
    WHERE attribute.attrelid = 'public.ops_jobs'::regclass
      AND attribute.attnum > 0
      AND NOT attribute.attisdropped
      AND has_column_privilege(
        '${DB_DEPLOYMENT_WORKER_USER}',
        attribute.attrelid,
        attribute.attnum,
        'UPDATE'
      ) IS DISTINCT FROM (
        attribute.attname::text = ANY (ARRAY[
          'status', 'agent_id', 'assigned_at', 'started_at',
          'heartbeat_at', 'progress', 'result', 'error_code',
          'error_message', 'cancel_requested_at', 'finished_at',
          'updated_at', 'lease_token', 'lease_epoch', 'leased_until'
        ])
      )
  )
  AND NOT has_any_column_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_deployments', 'INSERT')
  AND NOT has_table_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_deployments', 'DELETE')
  AND NOT has_table_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_deployments', 'TRUNCATE')
  AND NOT has_table_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_deployments', 'TRIGGER')
  AND NOT has_any_column_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_deployments', 'REFERENCES')
  AND NOT EXISTS (
    SELECT 1
    FROM pg_attribute attribute
    WHERE attribute.attrelid = 'public.ops_deployments'::regclass
      AND attribute.attnum > 0
      AND NOT attribute.attisdropped
      AND has_column_privilege(
        '${DB_DEPLOYMENT_WORKER_USER}',
        attribute.attrelid,
        attribute.attnum,
        'UPDATE'
      ) IS DISTINCT FROM (
        attribute.attname::text = ANY (ARRAY[
          'target_version', 'target_commit', 'deployment_status',
          'started_at', 'finished_at', 'runtime_generation',
          'activated_release_digest', 'runtime_previous_release_digest',
          'controller_state_sha256', 'runtime_target_kind',
          'runtime_native_baseline_identity'
        ])
      )
  )
  AND NOT EXISTS (
    SELECT 1
    FROM pg_namespace namespace
    WHERE namespace.nspname <> 'public'
      AND namespace.nspname !~ '^pg_'
      AND namespace.nspname <> 'information_schema'
      AND (
        has_schema_privilege('${DB_DEPLOYMENT_WORKER_USER}', namespace.oid, 'CREATE')
        OR (
          NOT EXISTS (
            SELECT 1
            FROM pg_depend dependency
            WHERE dependency.classid = 'pg_namespace'::regclass
              AND dependency.objid = namespace.oid
              AND dependency.deptype = 'e'
          )
          AND has_schema_privilege('${DB_DEPLOYMENT_WORKER_USER}', namespace.oid, 'USAGE')
        )
      )
  )
  AND NOT EXISTS (
    SELECT 1
    FROM pg_class relation
    JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
    WHERE namespace.nspname !~ '^pg_'
      AND namespace.nspname <> 'information_schema'
      AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
      AND EXISTS (
        SELECT 1 FROM pg_depend dependency
        WHERE dependency.classid = 'pg_class'::regclass
          AND dependency.objid = relation.oid
          AND dependency.deptype = 'e'
      )
      AND (
        has_any_column_privilege('${DB_DEPLOYMENT_WORKER_USER}', relation.oid, 'INSERT')
        OR has_any_column_privilege('${DB_DEPLOYMENT_WORKER_USER}', relation.oid, 'UPDATE')
        OR has_any_column_privilege('${DB_DEPLOYMENT_WORKER_USER}', relation.oid, 'REFERENCES')
        OR has_table_privilege('${DB_DEPLOYMENT_WORKER_USER}', relation.oid, 'DELETE')
        OR has_table_privilege('${DB_DEPLOYMENT_WORKER_USER}', relation.oid, 'TRUNCATE')
        OR has_table_privilege('${DB_DEPLOYMENT_WORKER_USER}', relation.oid, 'TRIGGER')
      )
  )
  AND NOT EXISTS (
    SELECT 1
    FROM pg_class sequence
    JOIN pg_namespace namespace ON namespace.oid = sequence.relnamespace
    WHERE namespace.nspname !~ '^pg_'
      AND namespace.nspname <> 'information_schema'
      AND sequence.relkind = 'S'
      AND EXISTS (
        SELECT 1 FROM pg_depend dependency
        WHERE dependency.classid = 'pg_class'::regclass
          AND dependency.objid = sequence.oid
          AND dependency.deptype = 'e'
      )
      AND (
        has_sequence_privilege('${DB_DEPLOYMENT_WORKER_USER}', sequence.oid, 'USAGE')
        OR has_sequence_privilege('${DB_DEPLOYMENT_WORKER_USER}', sequence.oid, 'SELECT')
        OR has_sequence_privilege('${DB_DEPLOYMENT_WORKER_USER}', sequence.oid, 'UPDATE')
      )
  )
  AND NOT EXISTS (
    SELECT 1
    FROM pg_class relation
    JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
    WHERE namespace.nspname !~ '^pg_'
      AND namespace.nspname <> 'information_schema'
      AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
      AND NOT EXISTS (
        SELECT 1 FROM pg_depend dependency
        WHERE dependency.classid = 'pg_class'::regclass
          AND dependency.objid = relation.oid
          AND dependency.deptype = 'e'
      )
      AND (
        namespace.nspname <> 'public'
        OR relation.relname::text <> ALL (ARRAY[
          'ops_container_releases', 'ops_container_validation_receipts',
          'ops_container_approval_evidence', 'ops_agents', 'ops_jobs',
          'ops_deployments', 'ops_job_logs'
        ])
      )
      AND (
        has_any_column_privilege('${DB_DEPLOYMENT_WORKER_USER}', relation.oid, 'SELECT')
        OR has_any_column_privilege('${DB_DEPLOYMENT_WORKER_USER}', relation.oid, 'INSERT')
        OR has_any_column_privilege('${DB_DEPLOYMENT_WORKER_USER}', relation.oid, 'UPDATE')
        OR has_any_column_privilege('${DB_DEPLOYMENT_WORKER_USER}', relation.oid, 'REFERENCES')
        OR has_table_privilege('${DB_DEPLOYMENT_WORKER_USER}', relation.oid, 'DELETE')
        OR has_table_privilege('${DB_DEPLOYMENT_WORKER_USER}', relation.oid, 'TRUNCATE')
        OR has_table_privilege('${DB_DEPLOYMENT_WORKER_USER}', relation.oid, 'TRIGGER')
      )
  )
  AND NOT EXISTS (
    SELECT 1
    FROM pg_class sequence
    JOIN pg_namespace namespace ON namespace.oid = sequence.relnamespace
    WHERE namespace.nspname !~ '^pg_'
      AND namespace.nspname <> 'information_schema'
      AND sequence.relkind = 'S'
      AND NOT EXISTS (
        SELECT 1 FROM pg_depend dependency
        WHERE dependency.classid = 'pg_class'::regclass
          AND dependency.objid = sequence.oid
          AND dependency.deptype = 'e'
      )
      AND (
        namespace.nspname <> 'public'
        OR sequence.relname::text <> ALL (ARRAY[
          'ops_job_logs_id_seq',
          'ops_container_deployment_lease_epoch_seq'
        ])
      )
      AND (
        has_sequence_privilege('${DB_DEPLOYMENT_WORKER_USER}', sequence.oid, 'USAGE')
        OR has_sequence_privilege('${DB_DEPLOYMENT_WORKER_USER}', sequence.oid, 'SELECT')
        OR has_sequence_privilege('${DB_DEPLOYMENT_WORKER_USER}', sequence.oid, 'UPDATE')
      )
  )
  AND NOT EXISTS (
    SELECT 1
    FROM pg_proc procedure
    JOIN pg_namespace namespace ON namespace.oid = procedure.pronamespace
    WHERE namespace.nspname !~ '^pg_'
      AND namespace.nspname <> 'information_schema'
      AND procedure.prokind IN ('f', 'p', 'a', 'w')
      AND NOT EXISTS (
        SELECT 1 FROM pg_depend dependency
        WHERE dependency.classid = 'pg_proc'::regclass
          AND dependency.objid = procedure.oid
          AND dependency.deptype = 'e'
      )
      AND has_function_privilege('${DB_DEPLOYMENT_WORKER_USER}', procedure.oid, 'EXECUTE')
  )
  AND NOT EXISTS (
    SELECT 1 FROM pg_largeobject_metadata
  )
  AND NOT has_function_privilege(
    '${DB_DEPLOYMENT_WORKER_USER}',
    'pg_catalog.lo_creat(integer)',
    'EXECUTE'
  )
  AND NOT has_function_privilege(
    '${DB_DEPLOYMENT_WORKER_USER}',
    'pg_catalog.lo_create(oid)',
    'EXECUTE'
  )
  AND NOT has_function_privilege(
    '${DB_DEPLOYMENT_WORKER_USER}',
    'pg_catalog.lo_from_bytea(oid,bytea)',
    'EXECUTE'
  )
  AND NOT has_function_privilege(
    '${DB_DEPLOYMENT_WORKER_USER}',
    'pg_catalog.lo_import(text)',
    'EXECUTE'
  )
  AND NOT has_function_privilege(
    '${DB_DEPLOYMENT_WORKER_USER}',
    'pg_catalog.lo_import(text,oid)',
    'EXECUTE'
  )
  AND NOT has_function_privilege(
    '${DB_DEPLOYMENT_WORKER_USER}',
    'pg_catalog.lo_export(oid,text)',
    'EXECUTE'
  )
  AND NOT EXISTS (
    SELECT 1
    FROM pg_proc procedure
    JOIN pg_namespace namespace ON namespace.oid = procedure.pronamespace
    CROSS JOIN LATERAL aclexplode(
      COALESCE(procedure.proacl, acldefault('f', procedure.proowner))
    ) acl
    WHERE namespace.nspname = 'pg_catalog'
      AND acl.privilege_type = 'EXECUTE'
      AND acl.grantee IN (
        SELECT role.oid
        FROM pg_roles role
        WHERE role.rolname IN (
          '${DB_DEPLOYMENT_WORKER_USER}',
          'mooncen_deployment_worker'
        )
      )
  )
  AND NOT has_schema_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public', 'CREATE')
  AND NOT has_database_privilege('${DB_DEPLOYMENT_WORKER_USER}', '${DB_NAME}', 'CREATE')
  AND NOT has_table_privilege('${DB_API_USER}', 'public.ops_container_releases', 'INSERT')
  AND NOT has_table_privilege('${DB_API_USER}', 'public.ops_container_validation_receipts', 'INSERT')
  AND NOT has_table_privilege('${DB_CRAWLER_USER}', 'public.ops_container_releases', 'INSERT')
  AND NOT has_table_privilege('${DB_CRAWLER_USER}', 'public.ops_container_validation_receipts', 'INSERT')
  AND NOT EXISTS (
    SELECT 1
    FROM public_sequences sequence
    WHERE has_sequence_privilege('${DB_AI_USER}', sequence.oid, 'USAGE')
  )
  AND has_sequence_privilege(
    '${DB_CHECK_USER}',
    'public.ops_job_logs_id_seq',
    'USAGE'
  )
  AND NOT EXISTS (
    SELECT 1
    FROM public_sequences sequence
    JOIN pg_class class ON class.oid = sequence.oid
    WHERE class.relname <> 'ops_job_logs_id_seq'
      AND has_sequence_privilege('${DB_CHECK_USER}', sequence.oid, 'USAGE')
  )
  AND pg_has_role('${DB_API_USER}', 'mooncen_api', 'member')
  AND pg_has_role('${DB_CRAWLER_USER}', 'mooncen_crawler', 'member')
  AND pg_has_role('${DB_AI_USER}', 'mooncen_ai', 'member')
  AND pg_has_role('${DB_APPLIER_USER}', 'mooncen_applier', 'member')
  AND pg_has_role('${DB_BACKUP_USER}', 'mooncen_readonly', 'member')
  AND pg_has_role('${DB_CHECK_USER}', 'mooncen_check', 'member')
  AND pg_has_role('${DB_DEPLOYMENT_WORKER_USER}', 'mooncen_deployment_worker', 'member')
  AND NOT pg_has_role('${DB_DEPLOYMENT_WORKER_USER}', 'mooncen_api', 'member')
  AND NOT pg_has_role('${DB_DEPLOYMENT_WORKER_USER}', 'mooncen_crawler', 'member')
  AND EXISTS (
    SELECT 1
    FROM pg_roles login
    JOIN pg_authid secret ON secret.oid = login.oid
    WHERE login.rolname = '${DB_DEPLOYMENT_WORKER_USER}'
      AND login.rolcanlogin
      AND login.rolinherit
      AND NOT login.rolsuper
      AND NOT login.rolcreatedb
      AND NOT login.rolcreaterole
      AND NOT login.rolreplication
      AND NOT login.rolbypassrls
      AND secret.rolpassword LIKE 'SCRAM-SHA-256$%'
  )
  AND EXISTS (
    SELECT 1
    FROM pg_roles permission_group
    WHERE permission_group.rolname = 'mooncen_deployment_worker'
      AND NOT permission_group.rolcanlogin
      AND permission_group.rolinherit
      AND NOT permission_group.rolsuper
      AND NOT permission_group.rolcreatedb
      AND NOT permission_group.rolcreaterole
      AND NOT permission_group.rolreplication
      AND NOT permission_group.rolbypassrls
  )
  AND 1 = (
    SELECT COUNT(*)
    FROM pg_auth_members membership
    JOIN pg_roles member ON member.oid = membership.member
    WHERE member.rolname = '${DB_DEPLOYMENT_WORKER_USER}'
  )
  AND EXISTS (
    SELECT 1
    FROM pg_auth_members membership
    JOIN pg_roles parent ON parent.oid = membership.roleid
    JOIN pg_roles member ON member.oid = membership.member
    WHERE member.rolname = '${DB_DEPLOYMENT_WORKER_USER}'
      AND parent.rolname = 'mooncen_deployment_worker'
  )
  AND NOT EXISTS (
    SELECT 1
    FROM pg_auth_members membership
    JOIN pg_roles member ON member.oid = membership.member
    WHERE member.rolname = 'mooncen_deployment_worker'
  )
  AND NOT EXISTS (
    SELECT 1 FROM pg_roles
    WHERE rolname IN ('${DB_API_USER}', '${DB_CRAWLER_USER}', '${DB_AI_USER}', '${DB_APPLIER_USER}', '${DB_BACKUP_USER}', '${DB_CHECK_USER}')
      AND (NOT rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)
  )
  AND 6 = (
    SELECT COUNT(*)
    FROM pg_auth_members membership
    JOIN pg_roles member ON member.oid = membership.member
    WHERE member.rolname IN ('${DB_API_USER}', '${DB_CRAWLER_USER}', '${DB_AI_USER}', '${DB_APPLIER_USER}', '${DB_BACKUP_USER}', '${DB_CHECK_USER}')
  );
")"
if [ "$db_role_contract" != "t" ]; then
  echo "Database runtime role contract verification failed." >&2
  exit 70
fi

# Ubuntu's local peer fallback intentionally remains for native administration.
# Prove the three local container SCRAM rules and the deployment worker's
# TLS-only exact-database fence before publishing any credential export source.
printf '%s\n%s\n%s\n%s\n' \
  "$DB_PASSWORD" \
  "$DB_API_PASSWORD" \
  "$DB_AI_PASSWORD" \
  "$DB_DEPLOYMENT_WORKER_PASSWORD" |
  sudo "$CONTAINER_PG_HBA_HELPER" install \
    --database "$DB_NAME" \
    --migrator-role "$DB_MIGRATOR_USER" \
    --api-role "$DB_API_USER" \
    --ai-role "$DB_AI_USER" \
    --worker-role "$DB_DEPLOYMENT_WORKER_USER" >/dev/null

# Reuse the exact registrar authorization query against the live dedicated
# LOGIN before its credential can cross the production-to-an2p boundary.
printf '%s\n' "$DB_DEPLOYMENT_WORKER_PASSWORD" |
  (
    cd "$APP_DIR"
    env -i \
      HOME=/nonexistent \
      LANG=C \
      LC_ALL=C \
      PATH=/usr/bin:/bin \
      PYTHONDONTWRITEBYTECODE=1 \
      "$APP_DIR/.venv/bin/python" \
        -m tools.register_container_deployment_evidence \
        --verify-database-boundary \
        --database "$DB_NAME" \
        --user "$DB_DEPLOYMENT_WORKER_USER"
  ) >/dev/null

# Publish the export source only after the schema, dedicated LOGIN, and exact
# ACL/HBA contracts have all converged. The user-readable deploy store remains
# the retry journal; this second copy is the root-only source for an explicit
# production-to-an2p encrypted pipe.
deploy_secret_metadata="$(stat -c '%U:%a' "$DEPLOY_SECRET_FILE")"
if [ ! -f "$DEPLOY_SECRET_FILE" ] || [ -L "$DEPLOY_SECRET_FILE" ] || \
   { [ "$deploy_secret_metadata" != "$DEPLOY_USER:600" ] && \
     [ "$deploy_secret_metadata" != "root:600" ]; }; then
  echo "Validated deploy secret source became unsafe before root handoff commit." >&2
  exit 78
fi
current_deploy_secret_sha256="$(sha256sum "$DEPLOY_SECRET_FILE")"
current_deploy_secret_sha256="${current_deploy_secret_sha256%% *}"
if [ "$current_deploy_secret_sha256" != "$deploy_secret_sha256" ]; then
  echo "Validated deploy secret source changed before root handoff commit." >&2
  exit 78
fi
if sudo test -L "$SERVICE_CONFIG_DIR"; then
  echo "Root deploy secret directory must not be a symlink." >&2
  exit 78
fi
sudo install -d -o root -g root -m 0751 "$SERVICE_CONFIG_DIR"
root_deploy_secret_stage="$(sudo mktemp "$SERVICE_CONFIG_DIR/.deploy-secrets.env.XXXXXX")"
cleanup_root_deploy_secret_stage() {
  if [ -n "${root_deploy_secret_stage:-}" ]; then
    sudo rm -f -- "$root_deploy_secret_stage"
  fi
}
trap cleanup_root_deploy_secret_stage EXIT HUP INT TERM
if [[ ! "$root_deploy_secret_stage" =~ ^/etc/mooncen/\.deploy-secrets\.env\.[A-Za-z0-9]+$ ]]; then
  echo "Root deploy secret staging path is invalid." >&2
  exit 78
fi
sudo install -o root -g root -m 0600 \
  "$DEPLOY_SECRET_FILE" "$root_deploy_secret_stage"
root_deploy_secret_sha256="$(sudo sha256sum "$root_deploy_secret_stage")"
root_deploy_secret_sha256="${root_deploy_secret_sha256%% *}"
if [ "$root_deploy_secret_sha256" != "$deploy_secret_sha256" ]; then
  echo "Root deploy secret staging digest does not match the validated source." >&2
  exit 78
fi
sudo sync -f -- "$root_deploy_secret_stage"
sudo mv -fT -- "$root_deploy_secret_stage" "$ROOT_DEPLOY_SECRET_FILE"
root_deploy_secret_stage=""
sudo sync -f -- "$SERVICE_CONFIG_DIR"
trap - EXIT HUP INT TERM
install_root_runtime_helper \
  "$AN2P_CONTROL_SECRETS_EXPORT_SOURCE" \
  "$AN2P_CONTROL_SECRETS_EXPORT_HELPER"
else
  echo "Skipping DB setup/migration because SKIP_DB_SETUP=1."
fi

# Keep the reviewed native-condition byte installed on standby nodes too. Only
# a primary that converged the database contract installs the exporter.
install_root_runtime_helper \
  "$NATIVE_RUNTIME_CONDITION_SOURCE" \
  "$NATIVE_RUNTIME_CONDITION_HELPER"

if [ "$NODE_ROLE" = "primary" ] && [ "$ENABLE_CRAWLER_STAGING" = "1" ]; then
  echo "Configuring the dedicated local crawler staging database..."
  sudo env \
    APP_DIR="$APP_DIR" \
    USE_DEDICATED_STAGING_CLUSTER=1 \
    STAGING_DB_NAME="$CRAWL_STAGING_DB_NAME" \
    STAGING_DB_USER="$CRAWL_STAGING_DB_USER" \
    STAGING_DB_PORT="$CRAWL_STAGING_DB_PORT" \
    bash "$APP_DIR/deploy/ha/n100_crawler_staging_setup.sh"
fi
ENVIRONMENT=production \
DB_HOST=localhost \
DB_PORT=5432 \
DB_NAME="$DB_NAME" \
DB_CRAWLER_USER="$DB_CRAWLER_USER" \
DB_CRAWLER_PASSWORD="$DB_CRAWLER_PASSWORD" \
DB_SSLROOTCERT="$SERVICE_DB_SSLROOTCERT" \
  "$APP_DIR/.venv/bin/python" "$APP_DIR/tools/generate_frontend_sitemap.py" --site-url "https://${DOMAIN}"

if [ -f "$APP_DIR/deploy/ubuntu/nginx/mooncen.conf" ]; then
  sed -i "s/server_name _;/server_name ${domain_names};/" "$APP_DIR/deploy/ubuntu/nginx/mooncen.conf"
fi

mooncenctl_source="$APP_DIR/deploy/ubuntu/mooncenctl.sh"
mooncenctl_target=/usr/local/bin/mooncenctl
mooncenctl_stage=/usr/local/bin/.mooncenctl.$$
if [ -e "$mooncenctl_source" ] || [ -L "$mooncenctl_source" ]; then
  [ -f "$mooncenctl_source" ] && [ ! -L "$mooncenctl_source" ] || {
    echo "mooncenctl source must be a regular non-symlink file." >&2
    exit 78
  }
  if sudo test -e "$mooncenctl_target" || sudo test -L "$mooncenctl_target"; then
    sudo test -f "$mooncenctl_target" && ! sudo test -L "$mooncenctl_target" || {
      echo "mooncenctl target must be absent or a regular non-symlink file." >&2
      exit 78
    }
  fi
  if sudo test -e "$mooncenctl_stage" || sudo test -L "$mooncenctl_stage"; then
    echo "mooncenctl staging path already exists." >&2
    exit 78
  fi
  [ -d /usr/local/bin ] && [ ! -L /usr/local/bin ] &&
    [ "$(stat -c '%U:%G' /usr/local/bin)" = "root:root" ] || {
    echo "mooncenctl parent directory is unsafe." >&2
    exit 78
  }
  mooncenctl_parent_mode="$(stat -c '%a' /usr/local/bin)"
  (( (8#$mooncenctl_parent_mode & 8#022) == 0 )) || {
    echo "mooncenctl parent directory mode is unsafe." >&2
    exit 78
  }
  cleanup_mooncenctl_stage() {
    sudo rm -f -- "$mooncenctl_stage"
  }
  trap cleanup_mooncenctl_stage EXIT
  trap 'exit 130' HUP INT TERM
  if ! sudo install -o root -g root -m 0755 "$mooncenctl_source" "$mooncenctl_stage"; then
    sudo rm -f -- "$mooncenctl_stage"
    exit 1
  fi
  if ! sudo test -f "$mooncenctl_stage" || sudo test -L "$mooncenctl_stage"; then
    sudo rm -f -- "$mooncenctl_stage"
    echo "mooncenctl staging file is unsafe." >&2
    exit 78
  fi
  if ! sudo mv -fT -- "$mooncenctl_stage" "$mooncenctl_target"; then
    sudo rm -f -- "$mooncenctl_stage"
    exit 1
  fi
  sudo test -f "$mooncenctl_target" && ! sudo test -L "$mooncenctl_target" &&
    [ "$(sudo stat -c '%U:%G:%a' "$mooncenctl_target")" = "root:root:755" ] || {
    echo "installed mooncenctl target metadata is unsafe." >&2
    exit 78
  }
  trap - EXIT HUP INT TERM
fi

if [ -f "$APP_DIR/deploy/ubuntu/install_sudoers.sh" ]; then
  sudo bash "$APP_DIR/deploy/ubuntu/install_sudoers.sh" "$DEPLOY_USER"
fi

BACKUP_LIBEXEC_DIR=/usr/local/libexec/mooncen-backup
sudo rm -rf -- "$BACKUP_LIBEXEC_DIR"
sudo install -d -o root -g root -m 0755 "$BACKUP_LIBEXEC_DIR"
for backup_script in "$APP_DIR"/deploy/backup/*.sh; do
  [ -f "$backup_script" ] || continue
  sudo install -o root -g root -m 0755 "$backup_script" "$BACKUP_LIBEXEC_DIR/$(basename "$backup_script")"
done

HA_LIBEXEC_DIR=/usr/local/libexec/mooncen-ha
sudo rm -rf -- "$HA_LIBEXEC_DIR"
sudo install -d -o root -g root -m 0755 "$HA_LIBEXEC_DIR"
for ha_script in cloudflare_health_gate.sh cloudflared_role_guard.sh; do
  sudo install -o root -g root -m 0755 \
    "$APP_DIR/deploy/ha/$ha_script" "$HA_LIBEXEC_DIR/$ha_script"
done

deploy_epoch="$(date +%s)"
{
  printf 'DEPLOY_EPOCH=%s\n' "$deploy_epoch"
  printf 'DEPLOY_UTC=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  printf 'DEPLOY_COMMIT=%s\n' "$DEPLOY_COMMIT"
  printf 'DEPLOY_ARCHIVE_SHA256=%s\n' "$DEPLOY_ARCHIVE_SHA256"
  printf 'NODE_ROLE=%s\n' "$NODE_ROLE"
  printf 'DOMAIN=%s\n' "$DOMAIN"
  printf 'HOSTNAME=%s\n' "$(hostname -f 2>/dev/null || hostname)"
} > "$APP_DIR/.deploy-info"
chmod 600 "$APP_DIR/.deploy-info"

# The native executable tree is an immutable rollback artifact.  Native
# services and the interactive deploy account receive group-read/execute only;
# no long-lived service UID owns bytes that rollback-native may execute.
sudo chown -R root:"$APP_GROUP" "$APP_DIR"
sudo chmod -R g-w,o-rwx "$APP_DIR"
sudo chmod -R g+rX "$APP_DIR"
if [ "$PREBUILT_RELEASE" = "1" ]; then
  prebuild_marker="$APP_DIR/.mooncen-prebuilt-release"
  sudo test -f "$prebuild_marker" && ! sudo test -L "$prebuild_marker" || {
    echo "Prebuilt release marker disappeared during setup." >&2
    exit 66
  }
  marker_commit="$(sudo awk -F= '$1=="DEPLOY_COMMIT" {count++; value=$2} END {if(count!=1) exit 1; print value}' "$prebuild_marker")" || {
    echo "Prebuilt release marker commit is invalid after setup." >&2
    exit 66
  }
  [ "$marker_commit" = "$DEPLOY_COMMIT" ] || {
    echo "Prebuilt release marker commit changed during setup." >&2
    exit 66
  }
fi
for sitemap_file in \
  "$APP_DIR/frontend2/public/sitemap.xml" \
  "$APP_DIR/frontend2/dist/sitemap.xml"; do
  if [ -f "$sitemap_file" ]; then
    sudo chown "$CRAWLER_OS_USER":"$APP_GROUP" "$sitemap_file"
    sudo chmod 0640 "$sitemap_file"
  fi
done

# Existing log/report artifacts and the application log path belong only to
# the crawler. The AI unit bind-mounts its private StateDirectory on this path
# inside its own mount namespace, so the two workers cannot alter each other's
# PID, progress, metric, or report files.
sudo install -d -o "$CRAWLER_OS_USER" -g "$CRAWLER_OS_USER" -m 0700 "$APP_DIR/logs"
sudo chown -R "$CRAWLER_OS_USER":"$CRAWLER_OS_USER" "$APP_DIR/logs"
sudo chmod -R u+rwX,go-rwx "$APP_DIR/logs"

# Failover state is root-written and bot-readable. Bot-owned mutable state is
# instead provided by StateDirectory=mooncen-bot in its service unit.
sudo install -d -o root -g "$APP_GROUP" -m 0750 "$APP_DIR/failover"
sudo chown -R root:"$APP_GROUP" "$APP_DIR/failover"
sudo chmod -R u+rwX,g+rX,g-w,o-rwx "$APP_DIR/failover"

# Keep the deliberately credential-free compatibility file readable by
# operator tooling after application-tree permissions are tightened.
sudo chmod 0644 "$APP_DIR/.env"

sudo install -d -o "$CRAWLER_OS_USER" -g "$CRAWLER_OS_USER" -m 0700 /var/log/mooncen

if [ "$PREBUILT_RELEASE" = "1" ]; then
  # Attest only after every included file's content, owner, group, and mode
  # has converged. The marker and deploy-info are separately pinned.
  immutable_tree_sha256="$(
    sudo /usr/bin/python3 -I "$APP_DIR/deploy/docker/native_baseline.py" \
      --root "$APP_DIR"
  )" || {
    echo "Immutable native runtime inventory could not be attested." >&2
    exit 66
  }
  [[ "$immutable_tree_sha256" =~ ^[0-9a-f]{64}$ ]] || {
    echo "Immutable native runtime inventory digest is invalid." >&2
    exit 66
  }
  {
    printf 'DEPLOY_ARCHIVE_SHA256=%s\n' "$DEPLOY_ARCHIVE_SHA256"
    printf 'IMMUTABLE_TREE_SHA256=%s\n' "$immutable_tree_sha256"
  } | sudo /usr/bin/tee -a "$prebuild_marker" >/dev/null
  sudo chown root:root "$prebuild_marker"
  sudo chmod 0600 "$prebuild_marker"
fi

echo "Project setup completed at $APP_DIR."
