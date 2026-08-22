#!/usr/bin/env bash
set -euo pipefail
umask 077

source_env=/etc/mooncen/crawler.env
output=/home/sgm/n100-crawler.env
deploy_user=sgm

if [ "$(id -u)" -ne 0 ]; then
  echo "Run export_n100_crawler_settings.sh through sudo." >&2
  exit 77
fi
if [ ! -f "$source_env" ] || [ -L "$source_env" ]; then
  echo "Crawler environment is unavailable or unsafe: $source_env" >&2
  exit 66
fi
if ! id "$deploy_user" >/dev/null 2>&1; then
  echo "Deploy user does not exist: $deploy_user" >&2
  exit 67
fi

maps_key="$(
  awk '
    index($0, "KAKAO_MAPS_REST_API_KEY=") == 1 {
      count += 1
      value = substr($0, length("KAKAO_MAPS_REST_API_KEY=") + 1)
    }
    END {
      if (count != 1 || value == "") {
        exit 65
      }
      printf "%s", value
    }
  ' "$source_env"
)" || {
  echo "Expected exactly one non-empty KAKAO_MAPS_REST_API_KEY entry." >&2
  exit 65
}
if [[ "$maps_key" == *$'\r'* ]] || [[ "$maps_key" == *$'\n'* ]]; then
  echo "Kakao Maps REST API key contains an invalid line break." >&2
  exit 65
fi

tmp="$(mktemp "/home/$deploy_user/.n100-crawler.env.XXXXXX")"
trap 'rm -f -- "$tmp"' EXIT HUP INT TERM
{
  printf 'KAKAO_MAPS_REST_API_KEY=%s\n' "$maps_key"
  printf 'CRAWLER_MAX_WORKERS=2\n'
} >"$tmp"
chown "$deploy_user:$deploy_user" "$tmp"
chmod 0600 "$tmp"
mv -fT -- "$tmp" "$output"
trap - EXIT HUP INT TERM

echo "N100 crawler settings export ready: $output"
