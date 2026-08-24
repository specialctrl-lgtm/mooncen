#!/usr/bin/env bash
set -euo pipefail

die() {
  echo "mooncen container runtime installer: $*" >&2
  exit 64
}

[ "$(id -u)" -eq 0 ] || die "must run as root"
[ "$#" -eq 4 ] && [ "$1" = "--an2p-target-identity" ] && \
  [ "$3" = "--expected-build-policy-sha256" ] ||
  die "usage: $0 --an2p-target-identity <64-lowercase-hex> --expected-build-policy-sha256 <64-lowercase-hex>"
target_identity="$2"
expected_build_policy="$4"
[[ "$target_identity" =~ ^[0-9a-f]{64}$ ]] || die "target identity is invalid"
[[ "$expected_build_policy" =~ ^[0-9a-f]{64}$ ]] || die "build policy digest is invalid"

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repository_root="$(cd -- "$script_dir/../.." && pwd -P)"
library_root=/usr/local/libexec/mooncen-container-release-lib
library_package="$library_root/deploy/docker"
controller=/usr/local/libexec/mooncen-container-release
identity_file=/etc/mooncen/an2p-dev-target-identity
installation_receipt=/etc/mooncen/container-runtime-installation.json
release_root=/opt/mooncen-container-releases
state_root=/var/lib/mooncen-container-release
control_lock="$state_root/control.lock"
transition_root=/var/lib/mooncen-runtime-transition
transition_lock="$transition_root/control.lock"
bootstrap_native_intent="$transition_root/native-bootstrap-intent.json"

[ -x /usr/bin/python3 ] || die "/usr/bin/python3 is required"
[ -x /usr/bin/flock ] || die "/usr/bin/flock is required"

# This stable lock exists before the container controller.  It serializes the
# first native deployment intent with the one-time controller installation;
# the controller's own control.lock takes over after bootstrap.
if [ -e "$transition_root" ] || [ -L "$transition_root" ]; then
  [ -d "$transition_root" ] && [ ! -L "$transition_root" ] &&
    [ "$(stat -c '%U:%G:%a' "$transition_root")" = root:root:700 ] ||
    die "runtime transition directory is unsafe"
else
  install -d -o root -g root -m 0700 "$transition_root"
fi
if [ -L "$transition_lock" ] || { [ -e "$transition_lock" ] && [ ! -f "$transition_lock" ]; }; then
  die "runtime transition lock is unsafe"
fi
if [ ! -e "$transition_lock" ]; then
  install -o root -g root -m 0600 /dev/null "$transition_lock"
fi
[ "$(stat -c '%U:%G:%a' "$transition_lock")" = root:root:600 ] ||
  die "runtime transition lock metadata is unsafe"
exec 8<>"$transition_lock"
/usr/bin/flock -x 8
if [ -e "$bootstrap_native_intent" ] || [ -L "$bootstrap_native_intent" ]; then
  die "an active first-bootstrap native deployment blocks controller installation"
fi
if [ -e /opt/.mooncen-deploy.lock ] || [ -L /opt/.mooncen-deploy.lock ]; then
  die "an active native deployment lock blocks controller installation"
fi
for relative in \
  deploy/docker/mooncen_container_release.py \
  deploy/docker/native_baseline.py \
  deploy/docker/production_runtime_integrity.py \
  deploy/docker/release_manifest.py \
  deploy/docker/verify_release_bundle.py \
  deploy/docker/mooncen-container-release \
  deploy/an2p/cloud/mooncen-an2p-deploy-sshd.service \
  deploy/an2p/cloud/mooncen-an2p-deploy-sshd_config \
  deploy/an2p/cloud/mooncen_container_ingress.py \
  deploy/an2p/cloud/mooncen_container_ssh_dispatch.py \
  deploy/ubuntu/configure_container_pg_hba.py \
  deploy/ubuntu/export_an2p_control_secrets.py \
  deploy/ubuntu/mooncen_native_runtime_condition.py \
  deploy/ubuntu/systemd/mooncen-ai-worker.service \
  deploy/ubuntu/systemd/mooncen-api.service \
  deploy/ubuntu/systemd/mooncen-container-stack.service \
  deploy/ubuntu/systemd/mooncen-container-release-guard@.service \
  deploy/ubuntu/systemd/mooncen-deploy-guard@.service \
  deploy/ubuntu/systemd/mooncen-frontend.service \
  docs/docker-production.md; do
  source_path="$repository_root/$relative"
  [ -f "$source_path" ] && [ ! -L "$source_path" ] ||
    die "required source is missing or unsafe: $relative"
done

/usr/bin/python3 -I \
  "$repository_root/deploy/docker/production_runtime_integrity.py" \
  verify-source --source-root "$repository_root" --expected "$expected_build_policy" \
  >/dev/null || die "reviewed build policy verification failed"

# The guarded native setup deliberately revokes this exporter before any DB,
# role, ACL, or HBA mutation and republishes it only after all authoritative
# probes and the root-only secret source commit succeed.  Target-identity
# bootstrap may bind and receipt those exact bytes, but must never recreate a
# capability that a skipped or failed guarded setup left absent.
control_exporter=/usr/local/libexec/mooncen-export-an2p-control-secrets
[ -f "$control_exporter" ] && [ ! -L "$control_exporter" ] && \
  [ "$(stat -c '%U:%G:%a' "$control_exporter")" = root:root:755 ] && \
  cmp -s -- "$repository_root/deploy/ubuntu/export_an2p_control_secrets.py" \
    "$control_exporter" || \
  die "guarded native setup did not publish the exact control-secret exporter"
[ -f /etc/mooncen/deploy-secrets.env ] && \
  [ ! -L /etc/mooncen/deploy-secrets.env ] && \
  [ "$(stat -c '%U:%G:%a' /etc/mooncen/deploy-secrets.env)" = root:root:600 ] || \
  die "guarded native setup did not commit its root-only control source"

install -d -o root -g root -m 0700 "$release_root" "$state_root"
if [ -L "$control_lock" ] || { [ -e "$control_lock" ] && [ ! -f "$control_lock" ]; }; then
  die "container control lock is unsafe"
fi
if [ ! -e "$control_lock" ]; then
  install -o root -g root -m 0600 /dev/null "$control_lock"
fi
[ "$(stat -c '%U:%G:%a' "$control_lock")" = "root:root:600" ] ||
  die "container control lock metadata is unsafe"
exec 9<>"$control_lock"
/usr/bin/flock -x 9
if [ -e "$state_root/transaction.json" ] || [ -L "$state_root/transaction.json" ]; then
  die "an active container transaction blocks controller replacement"
fi
if [ -e "$state_root/native-intent.json" ] || [ -L "$state_root/native-intent.json" ]; then
  die "an active native deployment intent blocks controller replacement"
fi
if [ -e "$state_root/active.json" ] || [ -L "$state_root/active.json" ]; then
  die "an active Docker runtime blocks controller replacement; use the reviewed rollback-native transition first"
fi
worker_lease="$state_root/worker-lease.json"
if [ -e "$worker_lease" ] || [ -L "$worker_lease" ]; then
  [ -f "$worker_lease" ] && [ ! -L "$worker_lease" ] &&
    [ "$(stat -c '%U:%G:%a' "$worker_lease")" = root:root:600 ] ||
    die "deployment worker lease journal is unsafe"
  if ! /usr/bin/python3 -I - "$worker_lease" <<'PY'
import json
import pathlib
import re
import sys
import time

path = pathlib.Path(sys.argv[1])
payload = path.read_bytes()
if len(payload) > 1024 * 1024 or b"\x00" in payload:
    raise SystemExit(78)
try:
    value = json.loads(payload.decode("ascii"))
except (UnicodeDecodeError, ValueError):
    raise SystemExit(78)
expected = {
    "active",
    "claim_epoch",
    "claim_token_sha256",
    "expires_epoch",
    "job_id",
    "schema_version",
}
canonical = json.dumps(
    value,
    ensure_ascii=True,
    allow_nan=False,
    sort_keys=True,
    separators=(",", ":"),
).encode("ascii") + b"\n"
if (
    not isinstance(value, dict)
    or set(value) != expected
    or payload != canonical
    or value.get("schema_version") != 1
    or re.fullmatch(r"[0-9a-f]{32}", str(value.get("job_id", ""))) is None
    or type(value.get("claim_epoch")) is not int
    or not 1 <= value["claim_epoch"] <= 9_223_372_036_854_775_807
    or re.fullmatch(r"[0-9a-f]{64}", str(value.get("claim_token_sha256", ""))) is None
    or type(value.get("active")) is not bool
    or type(value.get("expires_epoch")) is not int
    or value["expires_epoch"] <= 0
    or (value["active"] and value["expires_epoch"] > int(time.time()))
):
    raise SystemExit(78)
PY
  then
    die "a live or invalid deployment worker lease blocks controller replacement"
  fi
fi

install -d -o root -g root -m 0755 /usr/local/libexec
install -d -o root -g root -m 0755 \
  "$library_root" "$library_root/deploy" "$library_package"
install -o root -g root -m 0644 /dev/null "$library_root/deploy/__init__.py"
install -o root -g root -m 0644 /dev/null "$library_package/__init__.py"
install -o root -g root -m 0644 \
  "$repository_root/deploy/docker/mooncen_container_release.py" \
  "$library_package/mooncen_container_release.py"
install -o root -g root -m 0644 \
  "$repository_root/deploy/docker/native_baseline.py" \
  "$library_package/native_baseline.py"
install -o root -g root -m 0644 \
  "$repository_root/deploy/docker/production_runtime_integrity.py" \
  "$library_package/production_runtime_integrity.py"
install -o root -g root -m 0644 \
  "$repository_root/deploy/docker/production_runtime_integrity.py" \
  /usr/local/libexec/production_runtime_integrity.py
install -o root -g root -m 0644 \
  "$repository_root/deploy/docker/release_manifest.py" \
  "$library_package/release_manifest.py"
install -o root -g root -m 0644 \
  "$repository_root/deploy/docker/verify_release_bundle.py" \
  "$library_package/verify_release_bundle.py"
install -o root -g root -m 0755 \
  "$repository_root/deploy/docker/mooncen-container-release" \
  "$controller"
install -o root -g root -m 0755 \
  "$repository_root/deploy/ubuntu/configure_container_pg_hba.py" \
  /usr/local/libexec/mooncen-configure-container-pg-hba
install -o root -g root -m 0755 \
  "$repository_root/deploy/ubuntu/mooncen_native_runtime_condition.py" \
  /usr/local/libexec/mooncen-native-runtime-condition
install -o root -g root -m 0755 \
  "$repository_root/deploy/an2p/cloud/mooncen_container_ssh_dispatch.py" \
  /usr/local/libexec/mooncen-container-ssh-dispatch
install -o root -g root -m 0755 \
  "$repository_root/deploy/an2p/cloud/mooncen_container_ingress.py" \
  /usr/local/libexec/mooncen-container-ingress

install -d -o root -g root -m 0755 /etc/systemd/system
install -d -o root -g root -m 0751 /etc/mooncen
install -o root -g root -m 0644 \
  "$repository_root/deploy/ubuntu/systemd/mooncen-container-stack.service" \
  /etc/systemd/system/mooncen-container-stack.service
install -o root -g root -m 0644 \
  "$repository_root/deploy/ubuntu/systemd/mooncen-container-release-guard@.service" \
  /etc/systemd/system/mooncen-container-release-guard@.service
install -o root -g root -m 0644 \
  "$repository_root/deploy/ubuntu/systemd/mooncen-deploy-guard@.service" \
  /etc/systemd/system/mooncen-deploy-guard@.service
install -o root -g root -m 0644 \
  "$repository_root/deploy/ubuntu/systemd/mooncen-api.service" \
  /etc/systemd/system/mooncen-api.service
install -o root -g root -m 0644 \
  "$repository_root/deploy/ubuntu/systemd/mooncen-frontend.service" \
  /etc/systemd/system/mooncen-frontend.service
install -o root -g root -m 0644 \
  "$repository_root/deploy/ubuntu/systemd/mooncen-ai-worker.service" \
  /etc/systemd/system/mooncen-ai-worker.service
install -d -o root -g root -m 0755 /etc/ssh
install -o root -g root -m 0644 \
  "$repository_root/deploy/an2p/cloud/mooncen-an2p-deploy-sshd_config" \
  /etc/ssh/mooncen-an2p-deploy-sshd_config
install -o root -g root -m 0644 \
  "$repository_root/deploy/an2p/cloud/mooncen-an2p-deploy-sshd.service" \
  /etc/systemd/system/mooncen-an2p-deploy-sshd.service

install -d -o root -g root -m 0755 /opt/mooncen/docs
if [ "$repository_root/docs/docker-production.md" = "/opt/mooncen/docs/docker-production.md" ]; then
  chown root:root /opt/mooncen/docs/docker-production.md
  chmod 0644 /opt/mooncen/docs/docker-production.md
else
  install -o root -g root -m 0644 \
    "$repository_root/docs/docker-production.md" \
    /opt/mooncen/docs/docker-production.md
fi

identity_stage="$(mktemp /etc/mooncen/.an2p-dev-target-identity.XXXXXX)"
cleanup() {
  [ -z "$identity_stage" ] || rm -f -- "$identity_stage"
}
trap cleanup EXIT
printf '%s\n' "$target_identity" >"$identity_stage"
chown root:root "$identity_stage"
chmod 0600 "$identity_stage"
sync -f -- "$identity_stage"
mv -fT -- "$identity_stage" "$identity_file"
identity_stage=
sync -f -- /etc/mooncen

/usr/bin/python3 -I "$library_package/production_runtime_integrity.py" \
  write-installation-receipt \
  --source-root "$repository_root" \
  --expected "$expected_build_policy" \
  --output "$installation_receipt" ||
  die "production runtime installation receipt could not be committed"
[ "$(stat -c '%U:%G:%a' "$installation_receipt")" = "root:root:600" ] ||
  die "production runtime installation receipt metadata is unsafe"

systemctl daemon-reload
/usr/bin/flock -u 9
"$controller" status >/dev/null
/usr/bin/flock -u 8

echo "MoonCen container runtime controller installed; no service was started."
