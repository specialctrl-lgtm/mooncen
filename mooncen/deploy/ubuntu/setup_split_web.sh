#!/usr/bin/env bash
set -euo pipefail
umask 077

APP_DIR=/opt/mooncen
CONFIG_DIR=/etc/mooncen
DB_TLS_GROUP=mooncen-db-tls
API_USER=mooncen-api
WEB_USER=mooncen-web

site_host=""
db_client_env=""
db_ca=""
source_api_env=""
source_frontend_env=""
deploy_commit="unknown"

usage() {
  cat >&2 <<'EOF'
Usage: setup_split_web.sh \
  --site-host HOST \
  --db-client-env PATH \
  --db-ca PATH \
  [--source-api-env PATH] \
  [--source-frontend-env PATH] \
  [--deploy-commit HASH]
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --site-host)
      site_host="${2:-}"
      shift 2
      ;;
    --db-client-env)
      db_client_env="${2:-}"
      shift 2
      ;;
    --db-ca)
      db_ca="${2:-}"
      shift 2
      ;;
    --source-api-env)
      source_api_env="${2:-}"
      shift 2
      ;;
    --source-frontend-env)
      source_frontend_env="${2:-}"
      shift 2
      ;;
    --deploy-commit)
      deploy_commit="${2:-}"
      shift 2
      ;;
    *)
      usage
      exit 64
      ;;
  esac
done

if [ "$(id -u)" -ne 0 ]; then
  echo "Run setup_split_web.sh through sudo." >&2
  exit 77
fi
if [ ! -f "$APP_DIR/requirements.lock" ] || [ ! -f "$APP_DIR/frontend2/package-lock.json" ] || [ -L "$APP_DIR" ]; then
  echo "A regular MoonCen release must exist at $APP_DIR." >&2
  exit 66
fi
if [[ ! "$site_host" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$ ]]; then
  usage
  exit 64
fi
for required_file in "$db_client_env" "$db_ca"; do
  if [ ! -f "$required_file" ] || [ -L "$required_file" ]; then
    echo "Required split-web input is unavailable or unsafe: $required_file" >&2
    exit 66
  fi
done
for optional_file in "$source_api_env" "$source_frontend_env"; do
  [ -z "$optional_file" ] && continue
  if [ ! -f "$optional_file" ] || [ -L "$optional_file" ]; then
    echo "Optional split-web input is unavailable or unsafe: $optional_file" >&2
    exit 66
  fi
done
if [ "$deploy_commit" != "unknown" ] && [[ ! "$deploy_commit" =~ ^[0-9a-f]{40,64}$ ]]; then
  echo "Invalid deployment commit." >&2
  exit 64
fi

read_env_value() {
  local key="$1"
  local path="$2"
  [ -n "$path" ] || return 0
  awk -v expected="$key" '
    index($0, expected "=") == 1 {
      print substr($0, length(expected) + 2)
      exit
    }
  ' "$path"
}

DB_HOST="$(read_env_value DB_HOST "$db_client_env")"
DB_PORT="$(read_env_value DB_PORT "$db_client_env")"
DB_NAME="$(read_env_value DB_NAME "$db_client_env")"
DB_OWNER_USER="$(read_env_value DB_OWNER_USER "$db_client_env")"
DB_API_USER="$(read_env_value DB_API_USER "$db_client_env")"
DB_API_PASSWORD="$(read_env_value DB_API_PASSWORD "$db_client_env")"
for variable_name in DB_HOST DB_PORT DB_NAME DB_OWNER_USER DB_API_USER DB_API_PASSWORD; do
  value="${!variable_name:-}"
  if [ -z "$value" ] || [[ "$value" == *$'\n'* ]] || [[ "$value" == *$'\r'* ]]; then
    echo "Invalid DB client setting: $variable_name" >&2
    exit 78
  fi
done
if [[ ! "$DB_API_PASSWORD" =~ ^[0-9a-f]{64}$ ]]; then
  echo "Invalid DB API password contract." >&2
  exit 78
fi

AUTH_SECRET="$(read_env_value AUTH_SECRET "$source_api_env")"
GOOGLE_OAUTH_CLIENT_ID="$(read_env_value GOOGLE_OAUTH_CLIENT_ID "$source_api_env")"
GOOGLE_OAUTH_CLIENT_SECRET="$(read_env_value GOOGLE_OAUTH_CLIENT_SECRET "$source_api_env")"
NAVER_OAUTH_CLIENT_ID="$(read_env_value NAVER_OAUTH_CLIENT_ID "$source_api_env")"
NAVER_OAUTH_CLIENT_SECRET="$(read_env_value NAVER_OAUTH_CLIENT_SECRET "$source_api_env")"
MOONCEN_ADMIN_EMAILS="$(read_env_value MOONCEN_ADMIN_EMAILS "$source_api_env")"
MOONCEN_ADMIN_PROVIDER_IDS="$(read_env_value MOONCEN_ADMIN_PROVIDER_IDS "$source_api_env")"
MOONCEN_BUG_REPORT_TO="$(read_env_value MOONCEN_BUG_REPORT_TO "$source_api_env")"
MOONCEN_BUG_REPORT_FROM="$(read_env_value MOONCEN_BUG_REPORT_FROM "$source_api_env")"
MOONCEN_SMTP_HOST="$(read_env_value MOONCEN_SMTP_HOST "$source_api_env")"
MOONCEN_SMTP_PORT="$(read_env_value MOONCEN_SMTP_PORT "$source_api_env")"
MOONCEN_SMTP_USERNAME="$(read_env_value MOONCEN_SMTP_USERNAME "$source_api_env")"
MOONCEN_SMTP_PASSWORD="$(read_env_value MOONCEN_SMTP_PASSWORD "$source_api_env")"
MOONCEN_SMTP_SECURITY="$(read_env_value MOONCEN_SMTP_SECURITY "$source_api_env")"
OPS_CLOUDFLARE_ANALYTICS_ZONE_ID="$(read_env_value OPS_CLOUDFLARE_ANALYTICS_ZONE_ID "$source_api_env")"
OPS_CLOUDFLARE_ANALYTICS_TOKEN="$(read_env_value OPS_CLOUDFLARE_ANALYTICS_TOKEN "$source_api_env")"
MOONCEN_SERVER_MONITOR_TOKEN="$(read_env_value MOONCEN_SERVER_MONITOR_TOKEN "$source_api_env")"
KAKAO_MAPS_JAVASCRIPT_KEY="$(read_env_value VITE_KAKAO_MAPS_JAVASCRIPT_KEY "$source_frontend_env")"
if [ -z "$GOOGLE_OAUTH_CLIENT_ID" ]; then
  GOOGLE_OAUTH_CLIENT_ID="$(read_env_value VITE_GOOGLE_OAUTH_CLIENT_ID "$source_frontend_env")"
fi
if [ -z "$NAVER_OAUTH_CLIENT_ID" ]; then
  NAVER_OAUTH_CLIENT_ID="$(read_env_value VITE_NAVER_OAUTH_CLIENT_ID "$source_frontend_env")"
fi
if [ "${#AUTH_SECRET}" -lt 32 ]; then
  AUTH_SECRET="$(openssl rand -hex 32)"
fi
: "${MOONCEN_SMTP_PORT:=587}"
: "${MOONCEN_SMTP_SECURITY:=starttls}"
for variable_name in \
  AUTH_SECRET GOOGLE_OAUTH_CLIENT_ID GOOGLE_OAUTH_CLIENT_SECRET \
  NAVER_OAUTH_CLIENT_ID NAVER_OAUTH_CLIENT_SECRET \
  MOONCEN_ADMIN_EMAILS MOONCEN_ADMIN_PROVIDER_IDS \
  MOONCEN_BUG_REPORT_TO MOONCEN_BUG_REPORT_FROM MOONCEN_SMTP_HOST \
  MOONCEN_SMTP_PORT MOONCEN_SMTP_USERNAME MOONCEN_SMTP_PASSWORD MOONCEN_SMTP_SECURITY \
  OPS_CLOUDFLARE_ANALYTICS_ZONE_ID OPS_CLOUDFLARE_ANALYTICS_TOKEN MOONCEN_SERVER_MONITOR_TOKEN \
  KAKAO_MAPS_JAVASCRIPT_KEY; do
  value="${!variable_name:-}"
  if [[ "$value" == *$'\n'* ]] || [[ "$value" == *$'\r'* ]]; then
    echo "Invalid API/frontend setting: $variable_name" >&2
    exit 78
  fi
done
if [ -n "$OPS_CLOUDFLARE_ANALYTICS_ZONE_ID" ] || [ -n "$OPS_CLOUDFLARE_ANALYTICS_TOKEN" ]; then
  if [[ ! "$OPS_CLOUDFLARE_ANALYTICS_ZONE_ID" =~ ^[0-9a-f]{32}$ ]]; then
    echo "Invalid Cloudflare analytics zone id." >&2
    exit 78
  fi
  if [ "${#OPS_CLOUDFLARE_ANALYTICS_TOKEN}" -lt 20 ] || [ "${#OPS_CLOUDFLARE_ANALYTICS_TOKEN}" -gt 256 ] || [[ ! "$OPS_CLOUDFLARE_ANALYTICS_TOKEN" =~ ^[A-Za-z0-9_-]+$ ]]; then
    echo "Invalid Cloudflare analytics token." >&2
    exit 78
  fi
fi
if [ -n "$MOONCEN_SERVER_MONITOR_TOKEN" ] && { [ "${#MOONCEN_SERVER_MONITOR_TOKEN}" -lt 32 ] || [ "${#MOONCEN_SERVER_MONITOR_TOKEN}" -gt 256 ] || [[ ! "$MOONCEN_SERVER_MONITOR_TOKEN" =~ ^[A-Za-z0-9_-]+$ ]]; }; then
  echo "Invalid server monitor token." >&2
  exit 78
fi
if [[ ! "$MOONCEN_SMTP_PORT" =~ ^[0-9]{1,5}$ ]] || \
   (( 10#$MOONCEN_SMTP_PORT < 1 || 10#$MOONCEN_SMTP_PORT > 65535 )); then
  echo "Invalid SMTP port." >&2
  exit 78
fi
case "$MOONCEN_SMTP_SECURITY" in
  starttls|ssl|none) ;;
  *)
    echo "Invalid SMTP security mode." >&2
    exit 78
    ;;
esac

deploy_user="${SUDO_USER:-sgm}"
if ! id "$deploy_user" >/dev/null 2>&1; then
  echo "Unable to identify the deploy user." >&2
  exit 77
fi

if ! getent group mooncen >/dev/null; then
  groupadd --system mooncen
fi
for service_user in "$API_USER" "$WEB_USER"; do
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
usermod --append --groups "$DB_TLS_GROUP" "$API_USER"

install -d -o root -g root -m 0751 "$CONFIG_DIR"
install -o root -g "$DB_TLS_GROUP" -m 0640 "$db_ca" "$CONFIG_DIR/db-root-ca.crt"

chown -R "$deploy_user":"$(id -gn "$deploy_user")" "$APP_DIR"
runuser -u "$deploy_user" -- python3 -I -m venv --clear "$APP_DIR/.venv"
runuser -u "$deploy_user" -- \
  "$APP_DIR/.venv/bin/python" -I -m pip install \
    --require-hashes \
    -r "$APP_DIR/requirements.lock"

cat >"$APP_DIR/frontend2/.env.production" <<EOF
VITE_KAKAO_MAPS_JAVASCRIPT_KEY=$KAKAO_MAPS_JAVASCRIPT_KEY
VITE_GOOGLE_OAUTH_CLIENT_ID=$GOOGLE_OAUTH_CLIENT_ID
VITE_NAVER_OAUTH_CLIENT_ID=$NAVER_OAUTH_CLIENT_ID
VITE_SITE_URL=https://$site_host
VITE_OAUTH_REDIRECT_URI=https://$site_host/
EOF
chown "$deploy_user":"$(id -gn "$deploy_user")" "$APP_DIR/frontend2/.env.production"
chmod 0600 "$APP_DIR/frontend2/.env.production"
runuser -u "$deploy_user" -- env \
  -u OPS_CLOUDFLARE_ANALYTICS_ZONE_ID \
  -u OPS_CLOUDFLARE_ANALYTICS_TOKEN \
  -u MOONCEN_SERVER_MONITOR_TOKEN \
  bash -lc \
  "cd '$APP_DIR/frontend2' && npm ci --ignore-scripts && npm run build"
node_modules_dir="$APP_DIR/frontend2/node_modules"
if [ ! -d "$node_modules_dir" ] || [ -L "$node_modules_dir" ]; then
  echo "Frontend dependency directory is unsafe after the build." >&2
  exit 78
fi
rm -rf -- "$node_modules_dir"

cat >"$APP_DIR/.env" <<EOF
DB_HOST=$DB_HOST
DB_PORT=$DB_PORT
DB_NAME=$DB_NAME
VITE_SITE_URL=https://$site_host
ENVIRONMENT=production
API_HOST=127.0.0.1
API_PORT=8001
FRONTEND_HOST=127.0.0.1
FRONTEND_PORT=5173
EOF
chown "$deploy_user":"$(id -gn "$deploy_user")" "$APP_DIR/.env"
chmod 0644 "$APP_DIR/.env"

cat >"$CONFIG_DIR/api.env" <<EOF
DB_SSLROOTCERT=$CONFIG_DIR/db-root-ca.crt
DB_SSLMODE=verify-full
DB_HOST=$DB_HOST
DB_PORT=$DB_PORT
DB_NAME=$DB_NAME
DB_OWNER_USER=$DB_OWNER_USER
DB_API_USER=$DB_API_USER
DB_API_PASSWORD=$DB_API_PASSWORD
DB_POOL_MIN=1
DB_POOL_MAX=12
ENVIRONMENT=production
API_HOST=127.0.0.1
API_PORT=8001
API_WORKERS=3
MOONCEN_CORS_ORIGINS=https://$site_host
MOONCEN_TRUSTED_HOSTS=$site_host
AUTH_SECRET=$AUTH_SECRET
NAVER_OAUTH_CLIENT_ID=$NAVER_OAUTH_CLIENT_ID
NAVER_OAUTH_CLIENT_SECRET=$NAVER_OAUTH_CLIENT_SECRET
GOOGLE_OAUTH_CLIENT_ID=$GOOGLE_OAUTH_CLIENT_ID
GOOGLE_OAUTH_CLIENT_SECRET=$GOOGLE_OAUTH_CLIENT_SECRET
OAUTH_REDIRECT_URIS=https://$site_host/
MOONCEN_ADMIN_EMAILS=$MOONCEN_ADMIN_EMAILS
MOONCEN_ADMIN_PROVIDER_IDS=$MOONCEN_ADMIN_PROVIDER_IDS
MOONCEN_BUG_REPORT_TO=$MOONCEN_BUG_REPORT_TO
MOONCEN_BUG_REPORT_FROM=$MOONCEN_BUG_REPORT_FROM
MOONCEN_SMTP_HOST=$MOONCEN_SMTP_HOST
MOONCEN_SMTP_PORT=$MOONCEN_SMTP_PORT
MOONCEN_SMTP_USERNAME=$MOONCEN_SMTP_USERNAME
MOONCEN_SMTP_PASSWORD=$MOONCEN_SMTP_PASSWORD
MOONCEN_SMTP_SECURITY=$MOONCEN_SMTP_SECURITY
OPS_CLOUDFLARE_ANALYTICS_ZONE_ID=$OPS_CLOUDFLARE_ANALYTICS_ZONE_ID
OPS_CLOUDFLARE_ANALYTICS_TOKEN=$OPS_CLOUDFLARE_ANALYTICS_TOKEN
MOONCEN_SERVER_MONITOR_TOKEN=$MOONCEN_SERVER_MONITOR_TOKEN
VITE_SITE_URL=https://$site_host
SITE_URL=https://$site_host
EOF
chown root:"$API_USER" "$CONFIG_DIR/api.env"
chmod 0640 "$CONFIG_DIR/api.env"

cat >"$CONFIG_DIR/frontend.env" <<'EOF'
FRONTEND_HOST=127.0.0.1
FRONTEND_PORT=5173
NODE_ENV=production
EOF
chown root:"$WEB_USER" "$CONFIG_DIR/frontend.env"
chmod 0640 "$CONFIG_DIR/frontend.env"

install -o root -g root -m 0644 \
  "$APP_DIR/deploy/ubuntu/systemd/mooncen-api.service" \
  /etc/systemd/system/mooncen-api.service
install -d -o root -g root -m 0755 /etc/systemd/system/mooncen-api.service.d
cat >/etc/systemd/system/mooncen-api.service.d/10-split-db.conf <<'EOF'
[Unit]
Wants=network-online.target
After=network-online.target
EOF
chmod 0644 /etc/systemd/system/mooncen-api.service.d/10-split-db.conf

nginx_target=/etc/nginx/sites-available/mooncen.conf
install -o root -g root -m 0644 \
  "$APP_DIR/deploy/ubuntu/nginx/mooncen_split_web.conf" \
  "$nginx_target"
sed -i "s/server_name _;/server_name $site_host;/" "$nginx_target"
ln -sfn "$nginx_target" /etc/nginx/sites-enabled/mooncen.conf
rm -f /etc/nginx/sites-enabled/default
usermod --append --groups mooncen www-data
nginx -t

cat >"$APP_DIR/.deploy-meta" <<EOF
DEPLOY_COMMIT=$deploy_commit
DEPLOY_UTC=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
NODE_ROLE=web
DB_HOST=$DB_HOST
SITE_HOST=$site_host
EOF

# Build under a restrictive umask, then expose only read/traverse access to
# the dedicated service accounts through their shared application group.
chown -R "$deploy_user":mooncen "$APP_DIR"
chmod -R g-w,o-rwx "$APP_DIR"
chmod -R g+rX "$APP_DIR"
chmod 0640 "$APP_DIR/.deploy-meta"

systemctl daemon-reload
systemctl disable --now mooncen-frontend >/dev/null 2>&1 || true
rm -f /etc/systemd/system/mooncen-frontend.service
systemctl daemon-reload
systemctl enable mooncen-api nginx
systemctl restart mooncen-api nginx

for attempt in $(seq 1 30); do
  if curl --fail --silent --show-error \
    -H "Host: $site_host" \
    http://127.0.0.1/health >/dev/null; then
    break
  fi
  if [ "$attempt" -eq 30 ]; then
    journalctl -u mooncen-api -n 80 --no-pager >&2
    exit 75
  fi
  sleep 2
done

if ! timeout 30s tailscale serve \
  --bg \
  --yes \
  --https=443 \
  http://127.0.0.1:80; then
  echo "Tailscale Serve is not enabled. Approve the URL above, then run:" >&2
  echo "  sudo tailscale serve --bg --yes --https=443 http://127.0.0.1:80" >&2
  exit 75
fi
curl --fail --silent --show-error \
  -H "Host: $site_host" \
  http://127.0.0.1/health
echo
echo "MoonCen split web node is ready at https://$site_host"
