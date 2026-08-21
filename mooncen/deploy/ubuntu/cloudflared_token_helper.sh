#!/usr/bin/env bash
set -euo pipefail
umask 077

CLOUDFLARED_USER=cloudflared
CLOUDFLARED_GROUP=cloudflared
CLOUDFLARED_DIR=/etc/cloudflared
TOKEN_FILE="$CLOUDFLARED_DIR/token"
UNIT_FILE=/etc/systemd/system/cloudflared.service

die() {
  printf '%s\n' "$*" >&2
  exit 64
}

require_root() {
  if [ "${EUID:-$(id -u)}" -ne 0 ]; then
    die "cloudflared token helper must run as root"
  fi
}

ensure_service_account() {
  if ! getent group "$CLOUDFLARED_GROUP" >/dev/null 2>&1; then
    groupadd --system "$CLOUDFLARED_GROUP"
  fi
  if ! id "$CLOUDFLARED_USER" >/dev/null 2>&1; then
    useradd \
      --system \
      --gid "$CLOUDFLARED_GROUP" \
      --no-create-home \
      --home-dir /var/lib/cloudflared \
      --shell /usr/sbin/nologin \
      "$CLOUDFLARED_USER"
  else
    usermod \
      --gid "$CLOUDFLARED_GROUP" \
      --groups "$CLOUDFLARED_GROUP" \
      --home /var/lib/cloudflared \
      --shell /usr/sbin/nologin \
      "$CLOUDFLARED_USER"
  fi
}

verify_hardened_unit() {
  [ -f "$UNIT_FILE" ] || die "missing $UNIT_FILE; install MoonCen systemd units first"
  grep -Fxq 'User=cloudflared' "$UNIT_FILE" || die "cloudflared unit is missing its dedicated user"
  grep -Fxq 'Group=cloudflared' "$UNIT_FILE" || die "cloudflared unit is missing its dedicated group"
  grep -Fxq 'LoadCredential=cloudflared-token:/etc/cloudflared/token' "$UNIT_FILE" || \
    die "cloudflared unit is missing its token credential"
  grep -Fxq 'NoNewPrivileges=true' "$UNIT_FILE" || die "cloudflared unit is missing NoNewPrivileges"
  grep -Fxq 'ProtectSystem=strict' "$UNIT_FILE" || die "cloudflared unit is missing ProtectSystem=strict"
}

install_token() {
  if [ -t 0 ]; then
    die "token must be provided through standard input"
  fi

  local token=""
  local extra=""
  IFS= read -r token || true
  IFS= read -r extra || true
  # Windows PowerShell 5.1 prefixes native-process stdin with a UTF-8 BOM and
  # writes CRLF. Remove exactly one leading transport BOM and one trailing CR;
  # embedded or repeated control markers remain rejected by the strict token
  # character allowlist below.
  token="${token#$'\xEF\xBB\xBF'}"
  token="${token%$'\r'}"
  extra="${extra%$'\r'}"
  [ -n "$token" ] || die "cloudflared token input is empty"
  [ -z "$extra" ] || die "cloudflared token input must contain exactly one line"
  [ "${#token}" -le 8192 ] || die "cloudflared token input is too long"
  [[ "$token" =~ ^[A-Za-z0-9._+/=-]+$ ]] || die "cloudflared token contains invalid characters"

  ensure_service_account
  verify_hardened_unit
  install -d -o root -g "$CLOUDFLARED_GROUP" -m 0750 "$CLOUDFLARED_DIR"

  local temporary_file
  temporary_file="$(mktemp "$CLOUDFLARED_DIR/.token.XXXXXX")"
  trap 'rm -f "${temporary_file:-}"' EXIT
  printf '%s\n' "$token" > "$temporary_file"
  chown root:"$CLOUDFLARED_GROUP" "$temporary_file"
  chmod 0640 "$temporary_file"
  mv -f "$temporary_file" "$TOKEN_FILE"
  trap - EXIT
  chown root:"$CLOUDFLARED_GROUP" "$TOKEN_FILE"
  chmod 0640 "$TOKEN_FILE"

  systemctl daemon-reload
  local role="primary"
  if [ -f /etc/mooncen-node-role ]; then
    role="$(tr '[:upper:]' '[:lower:]' < /etc/mooncen-node-role | tr -d '[:space:]')"
  fi
  case "$role" in
    standby|replica|backup)
      systemctl disable --now cloudflared.service >/dev/null 2>&1 || true
      systemctl enable --now mooncen-cloudflared-role-guard.timer >/dev/null 2>&1 || true
      printf 'cloudflared token installed; node role=%s, tunnel kept stopped\n' "$role"
      ;;
    *)
      systemctl enable cloudflared.service >/dev/null
      systemctl restart cloudflared.service
      systemctl enable --now mooncen-cloudflared-role-guard.timer >/dev/null 2>&1 || true
      printf 'cloudflared token installed; node role=%s\n' "$role"
      ;;
  esac
}

read_token() {
  [ -f "$TOKEN_FILE" ] || die "cloudflared token is not installed"
  [ ! -L "$TOKEN_FILE" ] || die "cloudflared token must not be a symlink"
  [ "$(stat -c '%U:%G:%a' "$TOKEN_FILE")" = 'root:cloudflared:640' ] || \
    die "cloudflared token ownership or mode is invalid"
  IFS= read -r token < "$TOKEN_FILE" || true
  [ -n "${token:-}" ] || die "cloudflared token file is empty"
  printf '%s\n' "$token"
}

main() {
  require_root
  [ "$#" -eq 1 ] || die "usage: cloudflared_token_helper.sh install|read"
  case "$1" in
    install) install_token ;;
    read) read_token ;;
    *) die "unsupported cloudflared token helper action" ;;
  esac
}

main "$@"
