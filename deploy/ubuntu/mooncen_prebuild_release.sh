#!/usr/bin/env bash
set -euo pipefail
umask 077

FINAL_APP_DIR=/opt/mooncen
release_dir="${1:-}"
expected_commit="${2:-}"
heartbeat="${MOONCEN_DEPLOY_HEARTBEAT:-}"
config_stdin="${MOONCEN_PREBUILD_CONFIG_STDIN:-0}"

die() {
  echo "mooncen release prebuild: $*" >&2
  exit 65
}

without_runtime_secrets() {
  env \
    -u DB_PASSWORD -u DB_API_PASSWORD -u DB_CRAWLER_PASSWORD -u DB_AI_PASSWORD \
    -u DB_APPLIER_PASSWORD -u DB_BACKUP_PASSWORD -u DB_CHECK_PASSWORD \
    -u PRIMARY_DB_PASSWORD -u CRAWL_STAGING_DB_PASSWORD -u AUTH_SECRET \
    -u MOONCEN_OPS_PASSWORD_HASH -u KAKAO_MAPS_JAVASCRIPT_KEY \
    -u KAKAO_MAPS_REST_API_KEY -u GOOGLE_OAUTH_CLIENT_ID \
    -u GOOGLE_OAUTH_CLIENT_SECRET -u NAVER_OAUTH_CLIENT_ID \
    -u NAVER_OAUTH_CLIENT_SECRET -u MOONCEN_BOT_TOKEN "$@"
}

decode_build_config() {
  local encoded="$1"
  local decoded
  if ! decoded="$(printf '%s' "$encoded" | base64 --decode 2>/dev/null)"; then
    die "candidate build configuration encoding is invalid"
  fi
  printf '%s' "$decoded"
}

case "$config_stdin" in
  0) ;;
  1)
    IFS= read -r kakao_maps_javascript_key_b64 ||
      die "candidate build configuration is incomplete"
    IFS= read -r google_oauth_client_id_b64 ||
      die "candidate build configuration is incomplete"
    IFS= read -r naver_oauth_client_id_b64 ||
      die "candidate build configuration is incomplete"
    IFS= read -r domain_b64 ||
      die "candidate build configuration is incomplete"
    # Windows PowerShell 5.1 writes native-process stdin with a UTF-8 BOM and
    # may use CRLF line endings.  Base64 itself contains neither marker, so
    # accept exactly one transport BOM on the first line and one trailing CR
    # per protected line, then reject either marker anywhere else.  This keeps
    # the payload grammar strict without depending on the console encoding.
    kakao_maps_javascript_key_b64="${kakao_maps_javascript_key_b64#$'\xEF\xBB\xBF'}"
    for config_name in \
      kakao_maps_javascript_key_b64 \
      google_oauth_client_id_b64 \
      naver_oauth_client_id_b64 \
      domain_b64; do
      config_value="${!config_name}"
      config_value="${config_value%$'\r'}"
      [[ "$config_value" != *$'\r'* && "$config_value" != *$'\xEF\xBB\xBF'* ]] ||
        die "candidate build configuration contains an invalid transport marker"
      printf -v "$config_name" '%s' "$config_value"
    done
    # Keep decode and export as separate commands: Bash's export builtin would
    # otherwise mask a failed command substitution with a successful status.
    KAKAO_MAPS_JAVASCRIPT_KEY="$(decode_build_config "$kakao_maps_javascript_key_b64")"
    GOOGLE_OAUTH_CLIENT_ID="$(decode_build_config "$google_oauth_client_id_b64")"
    NAVER_OAUTH_CLIENT_ID="$(decode_build_config "$naver_oauth_client_id_b64")"
    DOMAIN="$(decode_build_config "$domain_b64")"
    export KAKAO_MAPS_JAVASCRIPT_KEY GOOGLE_OAUTH_CLIENT_ID NAVER_OAUTH_CLIENT_ID DOMAIN
    ;;
  *) die "MOONCEN_PREBUILD_CONFIG_STDIN must be 0 or 1" ;;
esac

if [[ ! "$release_dir" =~ ^/opt/\.mooncen-release-[0-9a-f]{32}$ ]] ||
   [ ! -d "$release_dir" ] || [ -L "$release_dir" ]; then
  die "candidate release path is unsafe"
fi
[[ "$expected_commit" =~ ^([0-9a-f]{40}|[0-9a-f]{64})$ ]] ||
  die "candidate release commit is invalid"
[[ "${KAKAO_MAPS_JAVASCRIPT_KEY:-}" =~ ^[0-9a-f]{32}$ ]] ||
  die "Kakao JavaScript build configuration is invalid"
[[ "${DOMAIN:-}" =~ ^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$ ]] ||
  die "frontend domain build configuration is invalid"
for value in \
  "${GOOGLE_OAUTH_CLIENT_ID:-}" \
  "${NAVER_OAUTH_CLIENT_ID:-}"; do
  [[ "$value" != *$'\n'* && "$value" != *$'\r'* ]] ||
    die "frontend OAuth build configuration contains line breaks"
  [[ "$value" =~ ^[A-Za-z0-9._!@%+=,:/-]*$ ]] ||
    die "frontend OAuth build configuration is unsafe"
done

requirements="$release_dir/requirements.lock"
package_lock="$release_dir/frontend2/package-lock.json"
frontend_dir="$release_dir/frontend2"
marker="$release_dir/.mooncen-prebuilt-release"
for required in "$requirements" "$package_lock" "$frontend_dir/package.json"; do
  [ -f "$required" ] && [ ! -L "$required" ] ||
    die "candidate prebuild input is missing or unsafe: $required"
done
for generated in \
  "$release_dir/.venv" \
  "$frontend_dir/node_modules" \
  "$frontend_dir/dist" \
  "$frontend_dir/.env.production" \
  "$marker"; do
  if [ -e "$generated" ] || [ -L "$generated" ]; then
    die "candidate artifact unexpectedly contains generated build state: $generated"
  fi
done

heartbeat_pid=""
stop_heartbeat() {
  if [ -n "$heartbeat_pid" ]; then
    kill "$heartbeat_pid" >/dev/null 2>&1 || true
    wait "$heartbeat_pid" >/dev/null 2>&1 || true
    heartbeat_pid=""
  fi
}
cleanup_failed_prebuild() {
  local status=$?
  trap - EXIT HUP INT TERM
  stop_heartbeat
  if [ "$status" -ne 0 ]; then
    rm -rf -- "$release_dir/.venv" "$frontend_dir/node_modules" "$frontend_dir/dist"
    rm -f -- "$frontend_dir/.env.production" "$marker"
  fi
  exit "$status"
}
trap cleanup_failed_prebuild EXIT
trap 'exit 130' HUP INT TERM

if [ -n "$heartbeat" ]; then
  [ "$heartbeat" = "/opt/.mooncen-deploy-heartbeat-${release_dir##*-}" ] ||
    die "deployment heartbeat path does not match the candidate release"
  [ -f "$heartbeat" ] && [ ! -L "$heartbeat" ] ||
    die "deployment heartbeat is missing or unsafe"
  heartbeat_parent=$$
  (
    while kill -0 "$heartbeat_parent" >/dev/null 2>&1; do
      touch "$heartbeat"
      sleep 15
    done
  ) &
  heartbeat_pid=$!
fi

python_version="$(without_runtime_secrets \
  python3 -I -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
case "$python_version" in
  3.12|3.13) ;;
  *) die "unsupported Python version for candidate prebuild: $python_version" ;;
esac

venv="$release_dir/.venv"
without_runtime_secrets python3 -I -m venv --copies "$venv"
without_runtime_secrets \
  "$venv/bin/python" -I -m pip install --no-compile --require-hashes -r "$requirements"
without_runtime_secrets "$venv/bin/python" -I -m pip check
without_runtime_secrets "$venv/bin/python" -I -c 'import fastapi, psycopg2, uvicorn'

# Console scripts, activation helpers, and some package metadata can embed the
# creation directory. Runtime units call python directly, but normalizing every
# text file also prevents the discarded candidate path from leaking into later
# diagnostics after the swap.
mapfile -t venv_path_files < <(
  grep -RIlF -- "$release_dir" "$venv" 2>/dev/null || true
)
for path_file in "${venv_path_files[@]}"; do
  [ -f "$path_file" ] && [ ! -L "$path_file" ] ||
    die "candidate venv path-bearing file is unsafe"
  sed -i "s|$release_dir|$FINAL_APP_DIR|g" "$path_file"
done
mapfile -t remaining_venv_path_files < <(
  grep -RIlF -- "$release_dir" "$venv" 2>/dev/null || true
)
if [ "${#remaining_venv_path_files[@]}" -ne 0 ]; then
  die "candidate path remains embedded in the relocatable virtual environment"
fi

pushd "$frontend_dir" >/dev/null
without_runtime_secrets npm ci --ignore-scripts
{
  printf 'VITE_KAKAO_MAPS_JAVASCRIPT_KEY=%s\n' "$KAKAO_MAPS_JAVASCRIPT_KEY"
  printf 'VITE_GOOGLE_OAUTH_CLIENT_ID=%s\n' "${GOOGLE_OAUTH_CLIENT_ID:-}"
  printf 'VITE_NAVER_OAUTH_CLIENT_ID=%s\n' "${NAVER_OAUTH_CLIENT_ID:-}"
  printf 'VITE_SITE_URL=https://%s\n' "$DOMAIN"
  printf 'VITE_OAUTH_REDIRECT_URI=https://%s/\n' "$DOMAIN"
} > .env.production
chmod 0600 .env.production
without_runtime_secrets npm run build
popd >/dev/null

frontend_index="$frontend_dir/dist/index.html"
[ -f "$frontend_index" ] && [ ! -L "$frontend_index" ] ||
  die "candidate frontend build did not produce a safe index"
requirements_sha256="$(sha256sum "$requirements" | awk '{print $1}')"
package_lock_sha256="$(sha256sum "$package_lock" | awk '{print $1}')"
frontend_env_sha256="$(sha256sum "$frontend_dir/.env.production" | awk '{print $1}')"
frontend_index_sha256="$(sha256sum "$frontend_index" | awk '{print $1}')"
{
  printf 'PREBUILD_VERSION=1\n'
  printf 'DEPLOY_COMMIT=%s\n' "$expected_commit"
  printf 'REQUIREMENTS_SHA256=%s\n' "$requirements_sha256"
  printf 'PACKAGE_LOCK_SHA256=%s\n' "$package_lock_sha256"
  printf 'FRONTEND_ENV_SHA256=%s\n' "$frontend_env_sha256"
  printf 'FRONTEND_INDEX_SHA256=%s\n' "$frontend_index_sha256"
} > "$marker"
chmod 0600 "$marker"

stop_heartbeat
trap - EXIT HUP INT TERM
echo "Candidate Python dependencies and frontend were built before activation."
