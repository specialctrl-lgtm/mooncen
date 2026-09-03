#!/usr/bin/env bash
set -euo pipefail

die() {
  echo "MoonCen Docker decommission: $*" >&2
  exit 78
}

usage() {
  cat >&2 <<'EOF'
usage: decommission_docker_runtime.sh an2p --confirm REMOVE-AN2P-DOCKER
       decommission_docker_runtime.sh cloud --confirm REMOVE-CLOUD-DOCKER
EOF
  exit 64
}

[ "$(id -u)" -eq 0 ] || die "root is required"
[ "$#" -eq 3 ] && [ "$2" = --confirm ] || usage
mode=$1
confirmation=$3
case "$mode:$confirmation" in
  an2p:REMOVE-AN2P-DOCKER|cloud:REMOVE-CLOUD-DOCKER) ;;
  *) usage ;;
esac

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
archive_root=/var/lib/mooncen-native-recovery/docker-decommission.$timestamp
install -d -o root -g root -m 0700 /var/lib/mooncen-native-recovery
[ ! -e "$archive_root" ] && [ ! -L "$archive_root" ] || die "archive path already exists"
install -d -o root -g root -m 0700 "$archive_root"

archive_path() {
  local path=$1 name
  [ -e "$path" ] || [ -L "$path" ] || return 0
  name=$(printf '%s' "$path" | sed 's#^/##; s#/#__#g')
  mv -- "$path" "$archive_root/$name"
}

write_receipt() {
  local receipt=$archive_root/decommission.json
  /usr/bin/python3 -I - "$receipt" "$mode" "$timestamp" <<'PY'
import json
import os
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = {
    "host": os.uname().nodename.split(".", 1)[0],
    "mode": sys.argv[2],
    "schema_version": 1,
    "timestamp": sys.argv[3],
}
payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n"
descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
try:
    os.write(descriptor, payload)
    os.fsync(descriptor)
finally:
    os.close(descriptor)
PY
  sync -f -- "$archive_root"
}

remove_mooncen_docker_objects() {
  local -a ids=() projects=(mooncen-dev mooncen-production)
  local container_id image project
  command -v docker >/dev/null 2>&1 || return 0
  mapfile -t ids < <(
    while read -r container_id; do
      [ -n "$container_id" ] || continue
      image=$(docker inspect --format '{{.Config.Image}}' "$container_id") || continue
      case "$image" in
        mooncen/api:*|mooncen/frontend:*|mooncen/postgres:*|mooncen/ops-console-static:*)
          printf '%s\n' "$container_id"
          ;;
      esac
    done < <(docker ps -aq)
  )
  [ "${#ids[@]}" -eq 0 ] || docker rm -f -- "${ids[@]}"
  mapfile -t ids < <(
    docker ps -a --format '{{.Label "com.docker.compose.project"}}' |
      awk '/^mooncen-smoke-[a-zA-Z0-9_-]+$/' | sort -u
  )
  projects+=("${ids[@]}")
  ids=()
  for project in "${projects[@]}"; do
    mapfile -t ids < <(docker ps -aq --filter "label=com.docker.compose.project=$project")
    [ "${#ids[@]}" -eq 0 ] || docker rm -f -- "${ids[@]}"
  done
  mapfile -t ids < <(
    docker image ls --format '{{.Repository}}:{{.Tag}} {{.ID}}' |
      awk '$1 ~ /^mooncen\/(api|frontend|postgres|ops-console-static):/ {print $2}' |
      sort -u
  )
  [ "${#ids[@]}" -eq 0 ] || docker image rm -f -- "${ids[@]}"
  mapfile -t ids < <(
    docker volume ls -q |
      awk '/^(mooncen-dev_|mooncen-production_|mooncen-smoke-)/'
  )
  [ "${#ids[@]}" -eq 0 ] || docker volume rm -f -- "${ids[@]}"
  mapfile -t ids < <(
    docker network ls --format '{{.Name}}' |
      awk '/^(mooncen-dev_|mooncen-production_|mooncen-smoke-)/'
  )
  [ "${#ids[@]}" -eq 0 ] || docker network rm -- "${ids[@]}"
}

decommission_an2p() {
  [ "$(hostname -s)" = an2p ] || die "an2p mode requires the an2p host"
  local state=/var/lib/mooncen-an2p-runtime
  local transaction=$state/control-finalization-transaction.json
  local pending=$state/pending-control-finalization.json
  local lock=$state/operation.lock
  local transaction_present=false

  if { [ -e "$transaction" ] || [ -L "$transaction" ]; } ||
     { [ -e "$pending" ] || [ -L "$pending" ]; }; then
    [ -f "$transaction" ] && [ ! -L "$transaction" ] || die "partial control transaction residue exists"
    [ -f "$pending" ] && [ ! -L "$pending" ] || die "partial control transaction residue exists"
    [ "$(stat -c '%U:%G:%a' "$transaction")" = root:root:600 ] || die "control transaction metadata is unsafe"
    [ "$(stat -c '%U:%G:%a' "$pending")" = root:root:600 ] || die "pending receipt metadata is unsafe"
    if [ ! -e "$lock" ] && [ ! -L "$lock" ]; then
      install -d -o root -g root -m 0700 "$state"
      install -o root -g root -m 0600 /dev/null "$lock"
    fi
    [ -f "$lock" ] && [ ! -L "$lock" ] && [ "$(stat -c '%U:%G:%a' "$lock")" = root:root:600 ] || die "operation lock is unsafe"
    transaction_present=true
  fi

  if [ -x /usr/local/libexec/mooncen-an2p-service-control ]; then
    /usr/local/libexec/mooncen-an2p-service-control native-select >/dev/null
  elif [ "$transaction_present" = true ]; then
    die "native selector is unavailable while a transaction still exists"
  fi

  if [ "$transaction_present" = true ]; then
    exec 9<>"$lock"
    flock -x 9
    /usr/bin/python3 -I - "$transaction" "$pending" <<'PY'
import hashlib
import json
import pathlib
import sys

transaction_path, pending_path = map(pathlib.Path, sys.argv[1:])
transaction_payload = transaction_path.read_bytes()
pending_payload = pending_path.read_bytes()
transaction = json.loads(transaction_payload.decode("ascii"))
expected_keys = {
    "pair", "pending_sha256", "phase", "registration_sha256",
    "schema_version", "source_tree",
}
canonical = json.dumps(transaction, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n"
if (
    set(transaction) != expected_keys
    or transaction_payload != canonical
    or transaction.get("schema_version") != 1
    or transaction.get("phase") != "started"
    or transaction.get("registration_sha256") != "0" * 64
    or transaction.get("pending_sha256") != hashlib.sha256(pending_payload).hexdigest()
):
    raise SystemExit("refusing to retire a control transaction that may have crossed the registration boundary")
PY
  fi

  ! systemctl is-active --quiet mooncen-docker-dev.service || die "Docker development service remains active"
  ! systemctl is-enabled --quiet mooncen-docker-dev.service || die "Docker development service remains enabled"
  systemctl --user --machine=sgm@ is-active --quiet mooncen-api.service || die "native API is inactive"
  systemctl --user --machine=sgm@ is-active --quiet mooncen-frontend.service || die "native frontend is inactive"
  curl --noproxy '*' -fsS http://127.0.0.1:8001/health | grep -Fxq '{"status":"ready"}' || die "native API health failed"
  curl --noproxy '*' -fsS http://127.0.0.1:5174/ >/dev/null || die "native frontend health failed"

  systemctl disable --now mooncen-docker-dev.service >/dev/null 2>&1 || true
  for unit in \
    mooncen-an2p-bootstrap-recovery.service \
    mooncen-an2p-runtime-recovery.service \
    mooncen-ops-api.socket \
    mooncen-ops-api-ipv6.socket \
    mooncen-ops-api.service \
    mooncen-ops-api-ipv6.service \
    mooncen-ops-db-tunnel.service \
    mooncen-ops-status-agent.service \
    mooncen-deployment-worker.service; do
    systemctl disable --now "$unit" >/dev/null 2>&1 || true
  done
  systemctl reset-failed mooncen-docker-dev.service >/dev/null 2>&1 || true
  for path in \
    /etc/mooncen-an2p/docker-development-enabled \
    /etc/systemd/system/mooncen-docker-dev.service \
    /etc/systemd/system/mooncen-an2p-bootstrap-recovery.service \
    /etc/systemd/system/mooncen-an2p-runtime-recovery.service \
    /etc/systemd/system/mooncen-ops-api.socket \
    /etc/systemd/system/mooncen-ops-api-ipv6.socket \
    /etc/systemd/system/mooncen-ops-api.service \
    /etc/systemd/system/mooncen-ops-api-ipv6.service \
    /etc/systemd/system/mooncen-ops-db-tunnel.service \
    /etc/systemd/system/mooncen-ops-status-agent.service \
    /etc/systemd/system/mooncen-deployment-worker.service \
    /etc/systemd/system/multi-user.target.wants/mooncen-docker-dev.service \
    /etc/systemd/system/multi-user.target.wants/mooncen-an2p-bootstrap-recovery.service \
    /etc/systemd/system/multi-user.target.wants/mooncen-an2p-runtime-recovery.service \
    /etc/systemd/system/sockets.target.wants/mooncen-ops-api.socket \
    /etc/systemd/system/sockets.target.wants/mooncen-ops-api-ipv6.socket \
    /usr/local/libexec/mooncen-an2p-service-control \
    /usr/local/libexec/mooncen-an2p-runtime-manager \
    /usr/local/sbin/mooncen-an2p-runtime-install \
    /root/mooncen-an2p-runtime-bootstrap.sh \
    /root/mooncen-an2p-bootstrap \
    /opt/mooncen-an2p-runtime \
    /opt/mooncen-an2p-control \
    /opt/mooncen-an2p-docker \
    /var/lib/mooncen-an2p-runtime; do
    archive_path "$path"
  done
  remove_mooncen_docker_objects
  if id mooncen_docker_operator >/dev/null 2>&1; then
    pkill -u mooncen_docker_operator >/dev/null 2>&1 || true
    gpasswd -d mooncen_docker_operator docker >/dev/null 2>&1 || true
    usermod --lock --shell /usr/sbin/nologin mooncen_docker_operator
  fi
  systemctl daemon-reload
  write_receipt
  echo "an2p Docker runtime retired; native development is healthy"
  echo "recovery_archive=$archive_root"
}

decommission_cloud() {
  [ "$(hostname -s)" = mooncen ] || die "cloud mode requires the production mooncen host"
  local state=/var/lib/mooncen-container-release
  local transition=/var/lib/mooncen-runtime-transition

  systemctl is-active --quiet mooncen-api.service || die "native API is inactive"
  systemctl is-active --quiet mooncen-frontend.service || die "native frontend is inactive"
  curl --noproxy '*' -fsS http://127.0.0.1:8001/health | grep -Fxq '{"status":"ready"}' || die "native API health failed"
  curl --noproxy '*' -fsS http://127.0.0.1:5173/ >/dev/null || die "native frontend health failed"
  for unsafe in \
    "$state/active.json" \
    "$state/transaction.json" \
    "$state/native-intent.json" \
    "$transition/native-bootstrap-intent.json" \
    /opt/.mooncen-deploy.lock; do
    [ ! -e "$unsafe" ] && [ ! -L "$unsafe" ] || die "active runtime state blocks decommission: $unsafe"
  done

  systemctl disable --now mooncen-container-stack.service >/dev/null 2>&1 || true
  systemctl disable --now mooncen-an2p-deploy-sshd.service >/dev/null 2>&1 || true
  systemctl reset-failed mooncen-container-stack.service mooncen-an2p-deploy-sshd.service >/dev/null 2>&1 || true

  for path in \
    /etc/mooncen/an2p-dev-target-identity \
    /etc/mooncen/container-runtime-installation.json \
    /etc/mooncen/container-bootstrap.json \
    /usr/local/libexec/mooncen-container-bootstrap \
    /usr/local/libexec/mooncen-container-release \
    /usr/local/libexec/mooncen-container-release-lib \
    /usr/local/libexec/mooncen-container-ssh-dispatch \
    /usr/local/libexec/mooncen-container-ingress \
    /etc/systemd/system/mooncen-container-stack.service \
    /etc/systemd/system/multi-user.target.wants/mooncen-container-stack.service \
    /etc/systemd/system/mooncen-container-release-guard@.service \
    /etc/systemd/system/mooncen-an2p-deploy-sshd.service \
    /etc/systemd/system/multi-user.target.wants/mooncen-an2p-deploy-sshd.service \
    /etc/ssh/mooncen-an2p-deploy-sshd_config \
    /var/lib/mooncen-container-release \
    /var/lib/mooncen-container-bootstrap \
    /var/lib/mooncen-container-ingress \
    /opt/mooncen-container-releases; do
    archive_path "$path"
  done
  remove_mooncen_docker_objects
  systemctl daemon-reload
  write_receipt
  echo "cloud Docker controller retired; native production is healthy"
  echo "recovery_archive=$archive_root"
}

case "$mode" in
  an2p) decommission_an2p ;;
  cloud) decommission_cloud ;;
  *) usage ;;
esac
