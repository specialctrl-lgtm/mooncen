#!/bin/bash
# Fixed root-side verifier for a source-only crawler-worker bootstrap release.
set -euo pipefail
umask 077

# This file is a sudo trust boundary.  Never inherit lookup paths or language
# startup hooks from the unprivileged transport account.
PATH=/usr/sbin:/usr/bin:/sbin:/bin
LC_ALL=C
IFS=$' \t\n'
export PATH LC_ALL
unset BASH_ENV CDPATH ENV GLOBIGNORE LD_LIBRARY_PATH LD_PRELOAD \
  PYTHONHOME PYTHONINSPECT PYTHONPATH PYTHONSTARTUP
hash -r

die() {
  printf 'crawler-worker bootstrap release rejected: %s\n' "$1" >&2
  exit "${2:-78}"
}

read_exact_value() {
  key="$1"
  file="$2"
  awk -F= -v expected="$key" '
    $1 == expected { count += 1; value = substr($0, length(expected) + 2) }
    END { if (count != 1) exit 65; printf "%s", value }
  ' "$file"
}

assert_root_identity() {
  expected_worker_key="$1"
  expected_kernel_hostname="$2"
  [ "$(id -u)" -eq 0 ] || die "root privileges are required" 77
  [ "$(hostname -s 2>/dev/null || true)" = "$expected_kernel_hostname" ] || \
    die "kernel hostname differs from the signed worker assignment"
  for identity_file in /etc/mooncen-node-role /etc/mooncen-worker-key; do
    [ -f "$identity_file" ] && [ ! -L "$identity_file" ] || \
      die "root-owned worker identity is missing: $identity_file"
    [ "$(stat -c '%U:%G:%a:%h' "$identity_file")" = root:root:644:1 ] || \
      die "worker identity must be root:root mode 0644 with one link"
  done
  [ "$(cat /etc/mooncen-node-role)" = crawler-worker ] || die "node role is not crawler-worker"
  [ "$(cat /etc/mooncen-worker-key)" = "$expected_worker_key" ] || \
    die "worker key differs from the root bootstrap identity"
}

if [ "$#" -eq 3 ] && [ "$1" = --verify-bootstrap ]; then
  worker_key="$2"
  kernel_hostname="$3"
  [[ "$worker_key" =~ ^[a-z][a-z0-9_-]{0,63}$ ]] || die "worker key is invalid" 64
  [[ "$kernel_hostname" =~ ^[a-z0-9]([a-z0-9-]{0,62})(\.[a-z0-9]([a-z0-9-]{0,62}))*$ ]] || \
    die "kernel hostname is invalid" 64
  assert_root_identity "$worker_key" "$kernel_hostname"
  allowed_signers=/etc/mooncen/crawler-worker-bootstrap-release-allowed-signers
  [ -f "$allowed_signers" ] && [ ! -L "$allowed_signers" ] || \
    die "worker bootstrap allowed-signers policy is missing"
  [ "$(stat -c '%U:%G:%a:%h' "$allowed_signers")" = root:root:644:1 ] || \
    die "worker bootstrap allowed-signers policy is unsafe"
  [ "$(uname -m)" = x86_64 ] || die "worker bootstrap runtime is pinned to x86_64"
  command -v python3.12 >/dev/null 2>&1 || die "CPython 3.12 is not installed"
  [ -x /usr/bin/chattr ] || die "fixed immutable-provenance helper /usr/bin/chattr is missing"
  python3.12 -I -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)' || \
    die "worker bootstrap runtime is not exact CPython 3.12"
  for unit in \
    mooncen-crawler-worker.target \
    mooncen-crawler-pull-worker.service \
    mooncen-crawler-release-agent.service \
    mooncen-crawler-release-agent.timer \
    mooncen-crawler-release-reporter.service \
    mooncen-crawler-release-reporter.timer; do
    ! systemctl is-active --quiet "$unit" || die "worker unit must be stopped: $unit" 70
    ! systemctl is-enabled --quiet "$unit" || die "worker unit must be disabled: $unit" 70
  done
  printf 'crawler-worker-root-bootstrap-ok:%s:%s\n' "$worker_key" "$kernel_hostname"
  exit 0
fi

if [ "$#" -ne 9 ]; then
  die "expected release-id, deploy-user, upload-dir, commit, archive/tree digests, role, worker key, and hostname" 64
fi

release_id="$1"
deploy_user="$2"
upload_dir="$3"
expected_commit="$4"
expected_archive_sha256="$5"
expected_tree_sha256="$6"
expected_role="$7"
expected_worker_key="$8"
expected_kernel_hostname="$9"

[[ "$release_id" =~ ^[0-9a-f]{32}$ ]] || die "release id is invalid" 64
[[ "$deploy_user" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || die "deploy user is invalid" 64
[[ "$expected_commit" =~ ^[0-9a-f]{40}$|^[0-9a-f]{64}$ ]] || die "commit is invalid" 64
[[ "$expected_archive_sha256" =~ ^[0-9a-f]{64}$ ]] || die "archive digest is invalid" 64
[[ "$expected_tree_sha256" =~ ^[0-9a-f]{64}$ ]] || die "tree digest is invalid" 64
[ "$expected_role" = crawler-worker ] || die "target role is invalid" 64
[[ "$expected_worker_key" =~ ^[a-z][a-z0-9_-]{0,63}$ ]] || die "worker key is invalid" 64
[[ "$expected_kernel_hostname" =~ ^[a-z0-9]([a-z0-9-]{0,62})(\.[a-z0-9]([a-z0-9-]{0,62}))*$ ]] || \
  die "kernel hostname is invalid" 64
[[ "$upload_dir" =~ ^/tmp/mooncen-worker-bootstrap-${expected_worker_key}-${release_id}\.[A-Za-z0-9]{8}$ ]] || \
  die "upload path is outside the fixed worker bootstrap namespace"

for command in awk cat chattr cmp find flock getent grep install lsattr mkdir python3.12 readlink rm sha256sum sort ssh-keygen stat systemctl tr uname wc; do
  command -v "$command" >/dev/null 2>&1 || die "required verifier command is unavailable: $command" 69
done
assert_root_identity "$expected_worker_key" "$expected_kernel_hostname"

allowed_signers=/etc/mooncen/crawler-worker-bootstrap-release-allowed-signers
[ -f "$allowed_signers" ] && [ ! -L "$allowed_signers" ] || die "allowed-signers policy is missing"
[ "$(stat -c '%U:%G:%a:%h' "$allowed_signers")" = root:root:644:1 ] || die "allowed-signers policy is unsafe"
[ -d "$upload_dir" ] && [ ! -L "$upload_dir" ] || die "upload directory is unsafe"
[ "$(stat -c '%U:%a' "$upload_dir")" = "$deploy_user:700" ] || die "upload directory ownership is unsafe"
[ "$(find "$upload_dir" -mindepth 1 -maxdepth 1 -printf '%f\n' | LC_ALL=C sort | tr '\n' ' ')" = \
  "crawler-worker-bootstrap-release.env crawler-worker-bootstrap-release.sig crawler-worker-bootstrap-release.tar.gz crawler-worker-bootstrap-release.tree " ] || \
  die "upload directory must contain exactly four reviewed artifacts"

archive="$upload_dir/crawler-worker-bootstrap-release.tar.gz"
tree="$upload_dir/crawler-worker-bootstrap-release.tree"
metadata="$upload_dir/crawler-worker-bootstrap-release.env"
signature="$upload_dir/crawler-worker-bootstrap-release.sig"
for artifact in "$archive" "$tree" "$metadata" "$signature"; do
  [ -f "$artifact" ] && [ ! -L "$artifact" ] || die "uploaded artifact is unsafe"
  [ "$(stat -c '%U:%a:%h' "$artifact")" = "$deploy_user:600:1" ] || \
    die "uploaded artifact owner, mode, or link count is unsafe"
done
[ "$(sha256sum "$archive" | awk '{print $1}')" = "$expected_archive_sha256" ] || die "archive digest differs"
[ "$(sha256sum "$tree" | awk '{print $1}')" = "$expected_tree_sha256" ] || die "tree digest differs"
if ! ssh-keygen -Y verify \
  -f "$allowed_signers" \
  -I mooncen-crawler-worker-bootstrap-release \
  -n mooncen-crawler-worker-bootstrap-release \
  -s "$signature" < "$metadata" >/dev/null 2>&1; then
  die "worker bootstrap metadata signature is invalid" 65
fi

[ "$(wc -l < "$metadata")" -eq 18 ] || die "metadata contains unreviewed fields" 65
[ "$(read_exact_value FORMAT "$metadata")" = mooncen-crawler-worker-bootstrap-release-v1 ] || die "metadata format is invalid" 65
[ "$(read_exact_value DEPLOY_COMMIT "$metadata")" = "$expected_commit" ] || die "metadata commit differs" 65
[ "$(read_exact_value DEPLOY_ARCHIVE_SHA256 "$metadata")" = "$expected_archive_sha256" ] || die "metadata archive differs" 65
[ "$(read_exact_value DEPLOY_TREE_SHA256 "$metadata")" = "$expected_tree_sha256" ] || die "metadata tree differs" 65
[ "$(read_exact_value NODE_ROLE "$metadata")" = crawler-worker ] || die "metadata role differs" 65
crawler_mode="$(read_exact_value CRAWLER_MODE "$metadata")"
[[ "$crawler_mode" =~ ^(legacy|distributed)$ ]] || die "metadata crawler mode is invalid" 65
[ "$(read_exact_value WORKER_KEY "$metadata")" = "$expected_worker_key" ] || die "metadata worker key differs" 65
[ "$(read_exact_value TARGET_KERNEL_HOSTNAME "$metadata")" = "$expected_kernel_hostname" ] || die "metadata hostname differs" 65
[[ "$(read_exact_value TOPOLOGY_SHA256 "$metadata")" =~ ^[0-9a-f]{64}$ ]] || die "metadata topology digest is invalid" 65
[[ "$(read_exact_value RESOURCE_DROPIN_SHA256 "$metadata")" =~ ^[0-9a-f]{64}$ ]] || die "metadata resource digest is invalid" 65
[[ "$(read_exact_value ROLLOUT_ORDER "$metadata")" =~ ^[1-9][0-9]*$ ]] || die "metadata rollout order is invalid" 65
[[ "$(read_exact_value CANARY "$metadata")" =~ ^(true|false)$ ]] || die "metadata canary field is invalid" 65
[[ "$(read_exact_value WORKER_ENABLED "$metadata")" =~ ^(true|false)$ ]] || die "metadata enabled field is invalid" 65
[[ "$(read_exact_value RESOURCE_CONCURRENCY "$metadata")" =~ ^[1-9][0-9]?$ ]] || die "metadata concurrency is invalid" 65
[[ "$(read_exact_value RESOURCE_MEMORY_HIGH "$metadata")" =~ ^[1-9][0-9]{0,4}[MG]$ ]] || die "metadata MemoryHigh is invalid" 65
[[ "$(read_exact_value RESOURCE_MEMORY_MAX "$metadata")" =~ ^[1-9][0-9]{0,4}[MG]$ ]] || die "metadata MemoryMax is invalid" 65
[[ "$(read_exact_value RESOURCE_CPU_QUOTA "$metadata")" =~ ^[1-9][0-9]{0,3}%$ ]] || die "metadata CPUQuota is invalid" 65

worker_enabled="$(read_exact_value WORKER_ENABLED "$metadata")"
if [ "$crawler_mode" != distributed ] || [ "$worker_enabled" != true ]; then
  die "NOT READY: signed topology keeps this worker disabled or crawlerMode is not distributed. No root path, account, or unit was changed." 70
fi

# Every check above is read-only.  Root mutation begins only for an externally
# signed release whose committed topology explicitly enables this exact host.
[ "$(uname -m)" = x86_64 ] || die "worker runtime is pinned to x86_64" 69
python3.12 -I -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)' || \
  die "worker runtime requires exact CPython 3.12" 69
getent group mooncen >/dev/null || die "root bootstrap must create group mooncen" 69

for unit in \
  mooncen-crawler-worker.target \
  mooncen-crawler-pull-worker.service \
  mooncen-crawler-release-agent.service \
  mooncen-crawler-release-agent.timer \
  mooncen-crawler-release-reporter.service \
  mooncen-crawler-release-reporter.timer; do
  ! systemctl is-active --quiet "$unit" || die "worker unit must be stopped before bootstrap activation: $unit" 70
  ! systemctl is-enabled --quiet "$unit" || die "worker unit must be disabled before bootstrap activation: $unit" 70
done

wheelhouse=/var/cache/mooncen-worker/wheelhouse
[ -d "$wheelhouse" ] && [ ! -L "$wheelhouse" ] || die "offline worker wheelhouse is missing" 69
[ "$(stat -c '%U:%G:%a' "$wheelhouse")" = root:root:755 ] || \
  die "offline worker wheelhouse must be root:root mode 0755" 69

exec 9>/run/lock/mooncen-worker-bootstrap-release.lock
flock -n 9 || die "another worker bootstrap activation holds the fixed root lock" 75

base=/opt/mooncen-worker
if [ -e "$base" ] || [ -L "$base" ]; then
  [ -d "$base" ] && [ ! -L "$base" ] || die "worker release root is unsafe"
  [ "$(stat -c '%U:%G:%a' "$base")" = root:root:755 ] || die "worker release root ownership is unsafe"
else
  install -d -o root -g root -m 0755 -- "$base"
fi
for directory in .staging .transactions .ingress; do
  path="$base/$directory"
  if [ -e "$path" ] || [ -L "$path" ]; then
    [ -d "$path" ] && [ ! -L "$path" ] || die "worker release state directory is unsafe: $path"
    [ "$(stat -c '%U:%G:%a' "$path")" = root:root:700 ] || die "worker release state directory ownership is unsafe"
  else
    install -d -o root -g root -m 0700 -- "$path"
  fi
  [ "$(stat -c '%d' "$path")" = "$(stat -c '%d' "$base")" ] || die "worker release state crosses filesystems"
done
releases="$base/releases"
if [ -e "$releases" ] || [ -L "$releases" ]; then
  [ -d "$releases" ] && [ ! -L "$releases" ] || die "worker releases directory is unsafe"
  [ "$(stat -c '%U:%G:%a' "$releases")" = root:mooncen:750 ] || die "worker releases directory ownership is unsafe"
else
  install -d -o root -g mooncen -m 0750 -- "$releases"
fi
[ "$(stat -c '%d' "$releases")" = "$(stat -c '%d' "$base")" ] || die "worker releases cross filesystems"

ingress="$base/.ingress/$release_id"
candidate="$base/.staging/$release_id"
for transaction_path in "$ingress" "$candidate" "$base/releases/$release_id"; do
  [ ! -e "$transaction_path" ] && [ ! -L "$transaction_path" ] || \
    die "release-specific worker path already exists: $transaction_path" 75
done
install -d -o root -g root -m 0700 -- "$ingress" "$candidate"
engine_started=0
cleanup() {
  status="$?"
  trap - EXIT INT TERM HUP
  if [ -d "$ingress" ] && [ ! -L "$ingress" ] && [ "$(stat -c '%U' "$ingress")" = root ]; then
    rm -rf -- "$ingress"
  fi
  if [ "$status" -ne 0 ] && [ "$engine_started" -eq 0 ] && \
     [ -d "$candidate" ] && [ ! -L "$candidate" ] && [ "$(stat -c '%U' "$candidate")" = root ]; then
    rm -rf -- "$candidate"
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

install -o root -g root -m 0600 -- "$archive" "$ingress/release.tar.gz"
install -o root -g root -m 0400 -- "$tree" "$ingress/release.tree"
install -o root -g root -m 0400 -- "$metadata" "$ingress/release.env"
install -o root -g root -m 0400 -- "$signature" "$ingress/release.sig"
[ "$(sha256sum "$ingress/release.tar.gz" | awk '{print $1}')" = "$expected_archive_sha256" ] || die "root ingress archive digest differs" 65
[ "$(sha256sum "$ingress/release.tree" | awk '{print $1}')" = "$expected_tree_sha256" ] || die "root ingress tree digest differs" 65
ssh-keygen -Y verify \
  -f "$allowed_signers" \
  -I mooncen-crawler-worker-bootstrap-release \
  -n mooncen-crawler-worker-bootstrap-release \
  -s "$ingress/release.sig" < "$ingress/release.env" >/dev/null 2>&1 || \
  die "root ingress signature verification failed" 65

topology_sha256="$(read_exact_value TOPOLOGY_SHA256 "$ingress/release.env")"
resource_dropin_sha256="$(read_exact_value RESOURCE_DROPIN_SHA256 "$ingress/release.env")"
dns_host="$(read_exact_value TARGET_DNS_HOST "$ingress/release.env")"
rollout_order="$(read_exact_value ROLLOUT_ORDER "$ingress/release.env")"
canary="$(read_exact_value CANARY "$ingress/release.env")"
concurrency="$(read_exact_value RESOURCE_CONCURRENCY "$ingress/release.env")"
memory_high="$(read_exact_value RESOURCE_MEMORY_HIGH "$ingress/release.env")"
memory_max="$(read_exact_value RESOURCE_MEMORY_MAX "$ingress/release.env")"
cpu_quota="$(read_exact_value RESOURCE_CPU_QUOTA "$ingress/release.env")"

# Validate every tar member and its canonical manifest record before writing
# it beneath the root candidate.  All archive entries must be regular files;
# extraction uses O_EXCL and never follows links.
python3.12 -I - \
  "$ingress/release.tar.gz" "$ingress/release.tree" "$candidate" \
  "$expected_commit" "$expected_worker_key" "$expected_kernel_hostname" \
  "$topology_sha256" "$resource_dropin_sha256" "$dns_host" "$rollout_order" \
  "$canary" "$worker_enabled" "$concurrency" "$memory_high" "$memory_max" "$cpu_quota" <<'PY'
import hashlib
import json
import os
import pathlib
import re
import sys
import tarfile

(archive_arg, manifest_arg, candidate_arg, commit, worker_key, kernel_hostname,
 topology_sha, resource_sha, dns_host, rollout_order, canary, enabled,
 concurrency, memory_high, memory_max, cpu_quota) = sys.argv[1:]
archive_path = pathlib.Path(archive_arg)
manifest_path = pathlib.Path(manifest_arg)
candidate = pathlib.Path(candidate_arg).resolve(strict=True)
manifest = manifest_path.read_bytes()
lines = manifest.decode("utf-8").splitlines()
expected_headers = [
    "format=mooncen-crawler-worker-bootstrap-tree-v1",
    f"commit={commit}",
    "node_role=crawler-worker",
    f"worker_key={worker_key}",
    f"kernel_hostname={kernel_hostname}",
    f"topology_sha256={topology_sha}",
    f"resource_dropin_sha256={resource_sha}",
    "crawler_mode=distributed",
]
if lines[:8] != expected_headers or len(lines) < 11 or lines[9] != "--files--":
    raise SystemExit("worker tree manifest header differs from signed metadata")
try:
    file_count = int(lines[8].removeprefix("file_count="))
except ValueError as exc:
    raise SystemExit("worker tree manifest file count is invalid") from exc
records = lines[10:]
if file_count != len(records) or not 1 <= file_count <= 128:
    raise SystemExit("worker tree manifest count differs")
safe = re.compile(r"[A-Za-z0-9_./-]+")
expected = {}
for record in records:
    fields = record.split("\t")
    if len(fields) != 4:
        raise SystemExit("worker tree manifest record is invalid")
    mode, size, digest, relative = fields
    pure = pathlib.PurePosixPath(relative)
    if (mode not in {"0644", "0755"} or not size.isdigit()
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or not safe.fullmatch(relative) or pure.is_absolute()
            or any(part in {"", ".", ".."} for part in pure.parts)
            or relative in expected):
        raise SystemExit("worker tree manifest record is unsafe")
    expected[relative] = (int(mode, 8), int(size), digest)
embedded_name = ".mooncen-worker-bootstrap-tree.manifest"
with tarfile.open(archive_path, "r:gz") as archive:
    members = archive.getmembers()
    if len(members) != file_count + 1 or {member.name for member in members} != {*expected, embedded_name}:
        raise SystemExit("worker archive membership differs from exact allowlist")
    for member in members:
        pure = pathlib.PurePosixPath(member.name)
        if (not member.isfile() or member.pax_headers or pure.is_absolute()
                or any(part in {"", ".", ".."} for part in pure.parts)
                or member.size < 0 or member.size > 16 * 1024 * 1024):
            raise SystemExit("worker archive contains an unsafe member")
        handle = archive.extractfile(member)
        if handle is None:
            raise SystemExit("worker archive member cannot be read")
        content = handle.read()
        if member.name == embedded_name:
            if content != manifest:
                raise SystemExit("embedded worker manifest differs")
            continue
        mode, size, digest = expected[member.name]
        if member.mode != mode or len(content) != size or hashlib.sha256(content).hexdigest() != digest:
            raise SystemExit("worker archive member differs from manifest")
        destination = candidate.joinpath(*pure.parts)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
    embedded = candidate / embedded_name
    descriptor = os.open(embedded, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    with os.fdopen(descriptor, "wb") as output:
        output.write(manifest)
        output.flush()
        os.fsync(output.fileno())

topology_path = candidate / "config/production_topology.json"
topology_bytes = topology_path.read_bytes()
if hashlib.sha256(topology_bytes).hexdigest() != topology_sha:
    raise SystemExit("worker topology digest differs")
topology = json.loads(topology_bytes)
if topology.get("crawlerMode") != "distributed":
    raise SystemExit("worker topology is not distributed")
sys.path.insert(0, str(candidate))
from ops_agent.production_topology import load_production_topology
try:
    authoritative = load_production_topology(candidate)
    authoritative_worker = authoritative.crawler_worker_for(worker_key)
except (KeyError, ValueError) as exc:
    raise SystemExit("worker topology violates the authoritative production contract") from exc
if authoritative.crawler_mode != "distributed" or not authoritative_worker.enabled:
    raise SystemExit("authoritative worker assignment is not enabled for distributed mode")
matches = [row for row in topology.get("crawlerWorkers", []) if row.get("workerKey") == worker_key]
if len(matches) != 1:
    raise SystemExit("worker topology assignment is not unique")
row = matches[0]
resources = row.get("resourceLimits", {})
expected_values = {
    "dnsHost": dns_host,
    "kernelHostname": kernel_hostname,
    "rolloutOrder": int(rollout_order),
    "canary": canary == "true",
    "enabled": enabled == "true",
}
if any(row.get(key) != value for key, value in expected_values.items()):
    raise SystemExit("worker topology identity differs from signed metadata")
if (
    authoritative_worker.dns_host != dns_host
    or authoritative_worker.kernel_hostname != kernel_hostname
    or authoritative_worker.rollout_order != int(rollout_order)
    or authoritative_worker.canary is not (canary == "true")
):
    raise SystemExit("authoritative worker identity differs from signed metadata")
if resources != {
    "concurrency": int(concurrency),
    "memoryHigh": memory_high,
    "memoryMax": memory_max,
    "cpuQuota": cpu_quota,
}:
    raise SystemExit("worker topology resources differ from signed metadata")
node = topology.get("nodes", {}).get(row.get("topologyNode"), {})
if node != {"dnsHost": dns_host}:
    raise SystemExit("worker DNS topology binding differs")
dropin = candidate / "deploy/ubuntu/systemd/mooncen-crawler-pull-worker.service.d/10-reviewed-worker-resources.conf"
if hashlib.sha256(dropin.read_bytes()).hexdigest() != resource_sha:
    raise SystemExit("worker resource drop-in digest differs")
PY

# If worker units already exist, require a complete exact unit/config set from
# the currently active signed release. Units are stopped and disabled, so the
# source pointer may advance first; setup then converges the new signed unit
# bytes under the same root lock. Requiring candidate bytes here would make a
# legitimate unit/drop-in update impossible (current cannot advance until the
# new unit is installed, while setup cannot trust it before current advances).
unit_contract_root="$candidate"
if [ -e "$base/current" ] || [ -L "$base/current" ]; then
  [ -L "$base/current" ] || die "existing worker current pointer is unsafe"
  current_contract_target="$(readlink -- "$base/current")"
  [[ "$current_contract_target" =~ ^releases/[0-9a-f]{32}$ ]] || die "existing worker current target is invalid"
  unit_contract_root="$(readlink -f -- "$base/current")"
  [ "$unit_contract_root" = "$base/$current_contract_target" ] && \
    [ -d "$unit_contract_root" ] && [ ! -L "$unit_contract_root" ] && \
    [ "$(stat -c '%U:%G:%a' "$unit_contract_root")" = root:mooncen:750 ] || \
    die "existing signed worker release root is unsafe"
  current_info="$unit_contract_root/.deploy-info"
  [ -f "$current_info" ] && [ ! -L "$current_info" ] && \
    [ "$(stat -c '%U:%G:%a:%h' "$current_info")" = root:root:400:1 ] || \
    die "existing signed worker provenance is unsafe"
  /usr/bin/lsattr -d -- "$current_info" | awk '{print $1}' | grep -q i || \
    die "existing signed worker provenance is not immutable"
fi
installed_units=0
for unit in \
  mooncen-crawler-worker.target \
  mooncen-crawler-pull-worker.service \
  mooncen-crawler-release-agent.service \
  mooncen-crawler-release-agent.timer \
  mooncen-crawler-release-reporter.service \
  mooncen-crawler-release-reporter.timer; do
  target="/etc/systemd/system/$unit"
  if [ -e "$target" ] || [ -L "$target" ]; then
    [ -f "$target" ] && [ ! -L "$target" ] && [ "$(stat -c '%U:%G:%a:%h' "$target")" = root:root:644:1 ] || \
      die "installed worker unit is unsafe: $unit"
    cmp -s -- "$target" "$unit_contract_root/deploy/ubuntu/systemd/$unit" || die "installed worker unit differs from active signed release: $unit"
    installed_units=$((installed_units + 1))
  fi
done
if [ "$installed_units" -ne 0 ]; then
  die "NOT READY: this helper is initial-bootstrap-only; an existing worker unit set requires a separately reviewed signed unit-convergence transaction. No current pointer was changed." 70
fi
if [ "$installed_units" -ne 0 ] && [ "$installed_units" -ne 6 ]; then
  die "installed worker unit set is partial"
fi
if [ "$installed_units" -eq 6 ]; then
  dropin=/etc/systemd/system/mooncen-crawler-pull-worker.service.d/10-reviewed-worker-resources.conf
  [ -f "$dropin" ] && [ ! -L "$dropin" ] && [ "$(stat -c '%U:%G:%a:%h' "$dropin")" = root:root:644:1 ] || \
    die "installed worker resource drop-in is unsafe"
  cmp -s -- "$dropin" "$unit_contract_root/deploy/ubuntu/systemd/mooncen-crawler-pull-worker.service.d/10-reviewed-worker-resources.conf" || \
    die "installed worker resource drop-in differs from the active signed binding"
  for contract in \
    /etc/mooncen/crawler-worker.env:mooncen-crawler-worker:640 \
    /etc/mooncen/crawler-release-reporter.env:mooncen-crawler-reporter:640 \
    /etc/mooncen/crawler-release-agent.env:root:600; do
    env_path="${contract%%:*}"
    remainder="${contract#*:}"
    env_group="${remainder%%:*}"
    env_mode="${remainder#*:}"
    [ -f "$env_path" ] && [ ! -L "$env_path" ] && \
      [ "$(stat -c '%U:%G:%a:%h' "$env_path")" = "root:$env_group:$env_mode:1" ] || \
      die "installed worker environment trust contract is unsafe: $env_path"
  done
fi

engine_started=1
python3.12 -I "$candidate/tools/activate_crawler_worker_bootstrap_state.py" \
  --base "$base" \
  --candidate "$candidate" \
  --wheelhouse "$wheelhouse" \
  --python /usr/bin/python3.12 \
  --release-id "$release_id" \
  --commit "$expected_commit" \
  --archive-sha256 "$expected_archive_sha256" \
  --tree-sha256 "$expected_tree_sha256" \
  --worker-key "$expected_worker_key" \
  --kernel-hostname "$expected_kernel_hostname" \
  --topology-sha256 "$topology_sha256" \
  --resource-dropin-sha256 "$resource_dropin_sha256"

rm -rf -- "$ingress" "$upload_dir"
trap - EXIT INT TERM HUP
printf 'MOONCEN_WORKER_BOOTSTRAP_RELEASE_VERIFIED=%s:%s:%s:%s\n' \
  "$expected_worker_key" "$expected_commit" "$expected_archive_sha256" "$expected_tree_sha256"
