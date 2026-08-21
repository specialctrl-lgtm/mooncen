#!/usr/bin/env bash
set -euo pipefail
umask 077

die() {
  printf '%s\n' "$*" >&2
  exit 78
}

[ "$(id -u)" -eq 0 ] || die "run as root"
[ "$(hostname -s)" = "mooncen" ] || die "unexpected host"
[ "$(cat /etc/mooncen-node-role 2>/dev/null || true)" = "primary" ] || \
  die "unexpected MoonCen node role"
if [ "$#" -ne 8 ]; then
  echo "usage: $0 SSHD_CONFIG SYSTEMD_UNIT DISPATCHER INGRESS_HELPER DEPLOY_PUBLIC_KEY STATUS_PUBLIC_KEY DB_PUBLIC_KEY LEGACY_SHARED_PUBLIC_KEY" >&2
  exit 64
fi

sshd_source=$1
unit_source=$2
dispatcher_source=$3
ingress_source=$4
deploy_public_key_source=$5
status_public_key_source=$6
db_public_key_source=$7
legacy_public_key_source=$8
for source_path in "$sshd_source" "$unit_source" "$dispatcher_source" \
  "$ingress_source" "$deploy_public_key_source" "$status_public_key_source" \
  "$db_public_key_source" "$legacy_public_key_source"; do
  [ -f "$source_path" ] && [ ! -L "$source_path" ] && [ -r "$source_path" ] || \
    die "unsafe provisioning input"
done

validate_public_key() {
  local source=$1 expected_comment=$2 key key_type key_blob key_comment extra
  key=$(tr -d '\r\n' <"$source")
  read -r key_type key_blob key_comment extra <<EOF
$key
EOF
  [ "$key_type" = "ssh-ed25519" ] && [ -n "$key_blob" ] && \
    [ "$key_comment" = "$expected_comment" ] && [ -z "${extra:-}" ] || \
    die "unexpected SSH public key"
  /usr/bin/ssh-keygen -l -f "$source" >/dev/null 2>&1 || \
    die "invalid SSH public key"
  printf '%s\n' "$key"
}

deploy_public_key=$(validate_public_key \
  "$deploy_public_key_source" mooncen-an2p-container-deploy-20260819)
status_public_key=$(validate_public_key \
  "$status_public_key_source" mooncen-an2p-container-status-20260819)
db_public_key=$(validate_public_key \
  "$db_public_key_source" mooncen-an2p-ops-db-20260819)
legacy_public_key=$(validate_public_key \
  "$legacy_public_key_source" mooncen-an2p-deploy-20260819)
deploy_blob=$(printf '%s\n' "$deploy_public_key" | cut -d' ' -f2)
status_blob=$(printf '%s\n' "$status_public_key" | cut -d' ' -f2)
db_blob=$(printf '%s\n' "$db_public_key" | cut -d' ' -f2)
legacy_blob=$(printf '%s\n' "$legacy_public_key" | cut -d' ' -f2)
[ "$deploy_blob" != "$status_blob" ] && [ "$deploy_blob" != "$db_blob" ] && \
  [ "$status_blob" != "$db_blob" ] && [ "$legacy_blob" != "$deploy_blob" ] && \
  [ "$legacy_blob" != "$status_blob" ] && [ "$legacy_blob" != "$db_blob" ] || \
  die "SSH transport keys must be distinct"

ensure_account() {
  local account=$1 home=$2 shell=$3 record actual_home actual_shell primary_group
  if ! record=$(getent passwd "$account"); then
    /usr/sbin/useradd --system --user-group --home-dir "$home" --create-home \
      --shell "$shell" "$account"
    record=$(getent passwd "$account")
  fi
  actual_home=$(printf '%s' "$record" | cut -d: -f6)
  actual_shell=$(printf '%s' "$record" | cut -d: -f7)
  primary_group=$(id -gn "$account")
  [ "$actual_home" = "$home" ] && [ "$actual_shell" = "$shell" ] && \
    [ "$primary_group" = "$account" ] || \
    die "unexpected dedicated SSH account metadata"
  install -d -o "$account" -g "$account" -m 0700 "$home"
  # A leading '!' makes OpenSSH reject the account before public-key auth on
  # some Ubuntu/PAM combinations. '*' is an impossible password hash while
  # the endpoint also disables every password authentication method.
  /usr/sbin/usermod --password '*' "$account"
}

deploy_user=mooncen_container_deploy
status_user=mooncen_container_status
db_user=mooncen_ops_db_tunnel
ensure_account "$deploy_user" /var/lib/mooncen-container-deploy /bin/sh
ensure_account "$status_user" /var/lib/mooncen-container-status /bin/sh
ensure_account "$db_user" /var/lib/mooncen-ops-db-tunnel /usr/sbin/nologin

state_root=/var/lib/mooncen-container-release
control_lock="$state_root/control.lock"
install -d -o root -g root -m 0700 "$state_root"
if [ -L "$control_lock" ] || { [ -e "$control_lock" ] && [ ! -f "$control_lock" ]; }; then
  die "container control lock is unsafe"
fi
if [ ! -e "$control_lock" ]; then
  install -o root -g root -m 0600 /dev/null "$control_lock"
fi
[ "$(stat -c '%U:%G:%a' "$control_lock")" = root:root:600 ] || \
  die "container control lock metadata is unsafe"
exec 9<>"$control_lock"
/usr/bin/flock -x 9
for journal in transaction.json native-intent.json active.json worker-lease.json; do
  if [ -e "$state_root/$journal" ] || [ -L "$state_root/$journal" ]; then
    die "runtime state blocks deployment endpoint identity replacement: $journal"
  fi
done

dispatcher=/usr/local/libexec/mooncen-container-ssh-dispatch
ingress_helper=/usr/local/libexec/mooncen-container-ingress
controller=/usr/local/libexec/mooncen-container-release

# Older native-host sudoers bundled the controller mutations into the
# interactive ubuntu rule. A full-shell credential could then mint a higher
# claim epoch and bypass the Ops approval/DB lease boundary. Converge that
# one known file before installing the forced deployment account; retain only
# its non-container/native maintenance commands and validate the result.
legacy_sudoers=/etc/sudoers.d/mooncen-deploy
if [ -e "$legacy_sudoers" ] || [ -L "$legacy_sudoers" ]; then
  [ -f "$legacy_sudoers" ] && [ ! -L "$legacy_sudoers" ] && \
    [ "$(stat -c '%U:%G:%a' "$legacy_sudoers")" = root:root:440 ] || \
    die "legacy deployment sudoers is unsafe"
  legacy_sudoers_tmp=$(mktemp /etc/sudoers.d/.mooncen-deploy.container-split.XXXXXX)
  /usr/bin/python3 -I - "$legacy_sudoers" "$legacy_sudoers_tmp" "$controller" <<'PY'
import os
import pathlib
import sys

source = pathlib.Path(sys.argv[1])
destination = pathlib.Path(sys.argv[2])
controller = sys.argv[3]
forbidden = {
    "lease-bind",
    "lease-release",
    "stage",
    "load-images",
    "preflight",
    "promote",
    "rollback",
    "rollback-native",
}
payload = source.read_bytes()
if not payload or len(payload) > 1024 * 1024 or b"\x00" in payload:
    raise SystemExit(78)
lines = payload.decode("utf-8", errors="strict").splitlines(keepends=True)
result: list[str] = []
for line in lines:
    if not any(f"{controller} {action}" in line for action in forbidden):
        result.append(line)
        continue
    body = line[:-1] if line.endswith("\n") else line
    header, separator, command_list = body.partition("NOPASSWD:")
    if not separator or "\\" in body:
        raise SystemExit(78)
    retained: list[str] = []
    removed = 0
    for raw_command in command_list.split(","):
        command = raw_command.strip()
        if command.startswith(f"{controller} "):
            remainder = command[len(controller) + 1 :]
            action = remainder.split(maxsplit=1)[0] if remainder else ""
            if action in forbidden:
                removed += 1
                continue
        retained.append(command)
    if removed == 0:
        raise SystemExit(78)
    if retained:
        result.append(f"{header}NOPASSWD: {', '.join(retained)}\n")
encoded = "".join(result).encode("utf-8")
if any(f"{controller} {action}".encode() in encoded for action in forbidden):
    raise SystemExit(78)
descriptor = os.open(destination, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
with os.fdopen(descriptor, "wb") as stream:
    stream.write(encoded)
    stream.flush()
    os.fsync(stream.fileno())
PY
  chown root:root "$legacy_sudoers_tmp"
  chmod 0440 "$legacy_sudoers_tmp"
  /usr/sbin/visudo -cf "$legacy_sudoers_tmp" >/dev/null
  mv -fT -- "$legacy_sudoers_tmp" "$legacy_sudoers"
  /bin/sync -d "$legacy_sudoers"
fi

install -d -o root -g root -m 0755 /usr/local/libexec
install -o root -g root -m 0755 "$dispatcher_source" "$dispatcher"
install -o root -g root -m 0755 "$ingress_source" "$ingress_helper"
if [ -d /var/lib/mooncen-container-ingress ] && \
  find /var/lib/mooncen-container-ingress -mindepth 1 -maxdepth 1 -print -quit | \
    grep -q .; then
  die "ingress root must be empty before changing its dedicated identity"
fi
install -d -o "$deploy_user" -g "$deploy_user" -m 0700 \
  /var/lib/mooncen-container-ingress

integrity=/usr/local/libexec/production_runtime_integrity.py
[ -f "$integrity" ] && [ ! -L "$integrity" ] && \
  [ "$(stat -c '%U:%G:%a' "$integrity")" = root:root:644 ] || \
  die "installed production integrity helper is unavailable"
install -d -o root -g root -m 0751 /etc/mooncen
/usr/bin/python3 -I "$integrity" write-bootstrap-config \
  --source-root /opt/mooncen \
  --deploy-user "$deploy_user" \
  --deploy-uid "$(id -u "$deploy_user")" \
  --deploy-gid "$(id -g "$deploy_user")" \
  --output /etc/mooncen/container-bootstrap.json || \
  die "container bootstrap identity could not be rebound"
[ "$(stat -c '%U:%G:%a' /etc/mooncen/container-bootstrap.json)" = root:root:600 ] || \
  die "container bootstrap identity metadata is unsafe"

key_dir=/etc/mooncen/ssh/authorized_keys
install -d -o root -g root -m 0755 /etc/mooncen /etc/mooncen/ssh "$key_dir"
install_key() {
  local account=$1 options=$2 key=$3 temporary
  temporary=$(mktemp "$key_dir/.${account}.XXXXXX")
  printf '%s %s\n' "$options" "$key" >"$temporary"
  install -o root -g root -m 0600 "$temporary" "$key_dir/$account"
  rm -f -- "$temporary"
}
install_key "$deploy_user" \
  'from="100.64.198.9",restrict,command="/usr/local/libexec/mooncen-container-ssh-dispatch"' \
  "$deploy_public_key"
install_key "$status_user" \
  'from="100.64.198.9",restrict,command="/usr/local/libexec/mooncen-container-ssh-dispatch"' \
  "$status_public_key"
install_key "$db_user" \
  'from="100.64.198.9",restrict,port-forwarding,permitopen="127.0.0.1:5432",command="/usr/bin/false"' \
  "$db_public_key"

# Remove the exact superseded shared key blob regardless of mutable comments or
# authorized_keys options. Unrelated interactive operator keys are preserved.
ubuntu_keys=/home/ubuntu/.ssh/authorized_keys
if [ -e "$ubuntu_keys" ] || [ -L "$ubuntu_keys" ]; then
  [ -f "$ubuntu_keys" ] && [ ! -L "$ubuntu_keys" ] || \
    die "unsafe legacy authorized_keys"
  [ "$(stat -c '%U:%G:%a' "$ubuntu_keys")" = "ubuntu:ubuntu:600" ] || \
    die "unsafe legacy authorized_keys metadata"
  filtered=$(mktemp /home/ubuntu/.ssh/.authorized_keys.container-split.XXXXXX)
  /usr/bin/python3 -I - "$ubuntu_keys" "$filtered" "$legacy_blob" <<'PY'
import os
import pathlib
import re
import sys

source = pathlib.Path(sys.argv[1])
output = pathlib.Path(sys.argv[2])
blob = sys.argv[3]
if re.fullmatch(r"[A-Za-z0-9+/]+={0,3}", blob) is None:
    raise SystemExit(78)
payload = source.read_bytes()
if len(payload) > 1024 * 1024 or b"\x00" in payload:
    raise SystemExit(78)
lines = payload.decode("utf-8", errors="strict").splitlines(keepends=True)
retained = []
for line in lines:
    tokens = line.strip().split()
    if blob not in tokens:
        retained.append(line)
result = "".join(retained).encode("utf-8")
descriptor = os.open(output, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
with os.fdopen(descriptor, "wb") as stream:
    stream.write(result)
    stream.flush()
    os.fsync(stream.fileno())
if any(blob in line.strip().split() for line in retained):
    raise SystemExit(78)
PY
  install -o ubuntu -g ubuntu -m 0600 "$filtered" "$ubuntu_keys"
  rm -f -- "$filtered"
  ! awk -v blob="$legacy_blob" '{for (i=1;i<=NF;i++) if ($i==blob) found=1} END {exit found ? 0 : 1}' \
    "$ubuntu_keys" || die "legacy shared SSH key blob remains authorized"
fi

repeat_class() {
  local class=$1 count=$2 result= index=0
  while [ "$index" -lt "$count" ]; do
    result="${result}${class}"
    index=$((index + 1))
  done
  printf '%s' "$result"
}
tree_arg=$(repeat_class '[0-9a-f]' 40)
generation_arg=$(repeat_class '[0-9]' 10)
digest_arg=$(repeat_class '[0-9a-f]' 64)
claim_arg=$(repeat_class '[0-9a-f]' 32)
claim_epoch_arg=$(repeat_class '[0-9]' 20)
sudoers=/etc/sudoers.d/mooncen-container-transport
sudoers_tmp=$(mktemp /etc/sudoers.d/.mooncen-container-transport.XXXXXX)
cat >"$sudoers_tmp" <<EOF
# Dedicated forced-command accounts. No shell/bootstrap/native-deploy sudo.
${status_user} ALL=(root) NOPASSWD: ${controller} status, ${controller} target-identity
${deploy_user} ALL=(root) NOPASSWD: ${controller} status, ${controller} target-identity, ${controller} lease-bind ${claim_arg} ${claim_epoch_arg} ${claim_arg}, ${controller} lease-release ${claim_arg} ${claim_epoch_arg} ${claim_arg}, ${controller} stage ${tree_arg} ${claim_arg} ${claim_epoch_arg} ${claim_arg}, ${controller} load-images ${tree_arg} ${claim_arg} ${claim_epoch_arg} ${claim_arg}, ${controller} preflight ${tree_arg} ${claim_arg} ${claim_epoch_arg} ${claim_arg}, ${controller} promote ${tree_arg} ${generation_arg} ${digest_arg} ${digest_arg} ${digest_arg} ${claim_arg} ${claim_epoch_arg} ${claim_arg}, ${controller} rollback ${generation_arg} ${digest_arg} ${digest_arg} ${digest_arg} ${claim_arg} ${claim_epoch_arg} ${claim_arg}, ${controller} rollback-native ${generation_arg} ${digest_arg} ${digest_arg} ${digest_arg} ${claim_arg} ${claim_epoch_arg} ${claim_arg}
EOF
chmod 0440 "$sudoers_tmp"
/usr/sbin/visudo -cf "$sudoers_tmp" >/dev/null
install -o root -g root -m 0440 "$sudoers_tmp" "$sudoers"
rm -f -- "$sudoers_tmp"

install -o root -g root -m 0644 "$sshd_source" \
  /etc/ssh/mooncen-an2p-deploy-sshd_config
install -o root -g root -m 0644 "$unit_source" \
  /etc/systemd/system/mooncen-an2p-deploy-sshd.service
/usr/sbin/sshd -t -f /etc/ssh/mooncen-an2p-deploy-sshd_config
systemctl daemon-reload
systemctl enable --now mooncen-an2p-deploy-sshd.service
systemctl is-active --quiet mooncen-an2p-deploy-sshd.service
ss -H -ltn 'sport = :2222' | grep -Fq '100.75.187.63:2222'

printf '%s\n' "split container deploy/status and DB tunnel SSH endpoint is active"
