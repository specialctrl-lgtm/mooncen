#!/bin/bash
# Root-side verifier and atomic activator for a source-only crawler-control release.
set -euo pipefail
umask 077

# This file is a sudo trust boundary.  Do not inherit command lookup or Python
# startup hooks from the unprivileged deploy account.  In particular, the
# fixed-helper sudo rule must remain safe even on a host whose global sudoers
# does not define secure_path.
PATH=/usr/sbin:/usr/bin:/sbin:/bin
LC_ALL=C
IFS=$' \t\n'
export PATH LC_ALL
unset BASH_ENV CDPATH ENV GLOBIGNORE LD_LIBRARY_PATH LD_PRELOAD \
  PYTHONHOME PYTHONINSPECT PYTHONPATH PYTHONSTARTUP
hash -r

die() {
  printf 'crawler-control release rejected: %s\n' "$1" >&2
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

if [ "$#" -eq 1 ] && [ "$1" = --verify-bootstrap ]; then
  [ "$(id -u)" -eq 0 ] || die "root privileges are required" 77
  [ "$(hostname -s 2>/dev/null || true)" = gen1db ] || die "bootstrap is pinned to hostname gen1db"
  [ -f /etc/mooncen-node-role ] && [ ! -L /etc/mooncen-node-role ] || die "node role file is missing or unsafe"
  [ "$(stat -c '%U:%G:%a' /etc/mooncen-node-role)" = root:root:644 ] || die "node role file must be root:root mode 0644"
  [ "$(cat /etc/mooncen-node-role)" = crawler-control ] || die "remote node role is not crawler-control"
  [ -f /etc/mooncen/crawler-control-release-allowed-signers ] && \
    [ ! -L /etc/mooncen/crawler-control-release-allowed-signers ] || die "allowed-signers policy is missing or unsafe"
  [ "$(stat -c '%U:%G:%a' /etc/mooncen/crawler-control-release-allowed-signers)" = root:root:644 ] || \
    die "allowed-signers policy must be root:root mode 0644"
  trust_helper=/usr/local/libexec/mooncen-crawler-control-root-trust
  [ -f "$trust_helper" ] && [ ! -L "$trust_helper" ] || die "fixed crawler-control root trust helper is missing"
  [ "$(stat -c '%U:%G:%a:%h' "$trust_helper")" = root:root:755:1 ] || \
    die "fixed crawler-control root trust helper is unsafe"
  trust_policy=/etc/mooncen/crawler-control-root-trust.policy
  [ -f "$trust_policy" ] && [ ! -L "$trust_policy" ] && \
    [ "$(stat -c '%U:%G:%a:%h' "$trust_policy")" = root:root:400:1 ] || \
    die "fixed crawler-control root trust policy is unsafe"
  [ "$(wc -l < "$trust_policy")" -eq 5 ] || die "root trust policy field count is invalid"
  [ "$(read_exact_value FORMAT "$trust_policy")" = mooncen-crawler-control-root-trust-policy-v1 ] || \
    die "root trust policy format is invalid"
  trust_helper_sha256="$(read_exact_value ROOT_TRUST_HELPER_SHA256 "$trust_policy")" || \
    die "root trust helper policy digest is missing"
  [[ "$trust_helper_sha256" =~ ^[0-9a-f]{64}$ ]] || die "root trust helper policy digest is invalid"
  [ "$(sha256sum "$trust_helper" | awk '{print $1}')" = "$trust_helper_sha256" ] || \
    die "fixed root trust helper differs from the out-of-band policy"
  "$trust_helper" verify-bootstrap >/dev/null || die "fixed crawler-control root trust bootstrap is incomplete"
  getent group mooncen >/dev/null || die "mooncen group is missing"
  [ "$(uname -m)" = x86_64 ] || die "runtime architecture is not x86_64"
  command -v python3.11 >/dev/null 2>&1 || die "CPython 3.11 is missing"
  python3.11 -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)' || die "runtime Python is not 3.11"
  printf 'crawler-control-root-bootstrap-ok\n'
  exit 0
fi

if [ "$#" -eq 5 ] && [ "$1" = --verify-active ]; then
  expected_commit="$2"
  expected_archive_sha256="$3"
  expected_tree_sha256="$4"
  expected_role="$5"
  [[ "$expected_commit" =~ ^[0-9a-f]{40}$|^[0-9a-f]{64}$ ]] || die "active-proof commit is invalid" 64
  [[ "$expected_archive_sha256" =~ ^[0-9a-f]{64}$ ]] || die "active-proof archive digest is invalid" 64
  [[ "$expected_tree_sha256" =~ ^[0-9a-f]{64}$ ]] || die "active-proof tree digest is invalid" 64
  [ "$expected_role" = crawler-control ] || die "active-proof role is invalid" 64
  [ "$(id -u)" -eq 0 ] || die "root privileges are required" 77
  [ "$(hostname -s 2>/dev/null || true)" = gen1db ] || die "active proof is pinned to hostname gen1db"
  for command in awk cat cmp find grep lsattr python3.11 sha256sum stat wc; do
    command -v "$command" >/dev/null 2>&1 || die "active-proof command is unavailable: $command" 69
  done
  node_role_file=/etc/mooncen-node-role
  [ -f "$node_role_file" ] && [ ! -L "$node_role_file" ] || die "node role file is missing or unsafe"
  [ "$(stat -c '%U:%G:%a' "$node_role_file")" = root:root:644 ] || die "node role file must be root:root mode 0644"
  [ "$(cat "$node_role_file")" = crawler-control ] || die "remote node role is not crawler-control"
  remote_dir=/opt/mooncen
  [ -d "$remote_dir" ] && [ ! -L "$remote_dir" ] || die "active release root is missing or unsafe" 65
  [ "$(stat -c '%U:%G:%a' "$remote_dir")" = root:mooncen:750 ] || die "active release root ownership or mode is unsafe" 65
  info="$remote_dir/.deploy-info"
  tree_manifest="$remote_dir/.mooncen-control-tree.manifest"
  runtime_manifest="$remote_dir/.mooncen-control-runtime.manifest"
  [ -f "$info" ] && [ ! -L "$info" ] && [ "$(stat -c '%U:%G:%a:%h' "$info")" = root:root:400:1 ] || \
    die "active provenance is missing or unsafe" 65
  lsattr -d -- "$info" | awk '{print $1}' | grep -q 'i' || die "active provenance is not filesystem-immutable" 65
  [ "$(wc -l < "$info")" -eq 9 ] || die "active provenance contains unreviewed fields" 65
  [ "$(read_exact_value DEPLOY_INFO_FORMAT "$info")" = mooncen-crawler-control-provenance-v1 ] || die "active provenance format is invalid" 65
  [ "$(read_exact_value DEPLOY_COMMIT "$info")" = "$expected_commit" ] || die "active provenance commit is invalid" 65
  [ "$(read_exact_value DEPLOY_ARCHIVE_SHA256 "$info")" = "$expected_archive_sha256" ] || die "active provenance archive digest is invalid" 65
  [ "$(read_exact_value DEPLOY_TREE_SHA256 "$info")" = "$expected_tree_sha256" ] || die "active provenance tree digest is invalid" 65
  [ "$(read_exact_value NODE_ROLE "$info")" = crawler-control ] || die "active provenance role is invalid" 65
  [ "$(read_exact_value TARGET_HOST "$info")" = gen1db ] || die "active provenance host is invalid" 65
  release_id="$(read_exact_value RELEASE_ID "$info")"
  runtime_lock_sha256="$(read_exact_value RUNTIME_LOCK_SHA256 "$info")"
  runtime_tree_sha256="$(read_exact_value RUNTIME_TREE_SHA256 "$info")"
  [[ "$release_id" =~ ^[0-9a-f]{32}$ ]] || die "active provenance release id is invalid" 65
  [[ "$runtime_lock_sha256" =~ ^[0-9a-f]{64}$ ]] || die "active runtime lock provenance is invalid" 65
  [[ "$runtime_tree_sha256" =~ ^[0-9a-f]{64}$ ]] || die "active runtime tree provenance is invalid" 65
  [ -f "$tree_manifest" ] && [ ! -L "$tree_manifest" ] && \
    [ "$(sha256sum "$tree_manifest" | awk '{print $1}')" = "$expected_tree_sha256" ] || \
    die "active canonical tree manifest is invalid" 65
  [ -f "$runtime_manifest" ] && [ ! -L "$runtime_manifest" ] && \
    [ "$(sha256sum "$runtime_manifest" | awk '{print $1}')" = "$runtime_tree_sha256" ] || \
    die "active runtime manifest is invalid" 65
  [ "$(sha256sum "$remote_dir/deploy/ubuntu/requirements-crawler-control.lock" | awk '{print $1}')" = "$runtime_lock_sha256" ] || \
    die "active runtime lock differs from provenance" 65
  python3.11 -I - "$remote_dir" "$tree_manifest" <<'PY'
import hashlib
import pathlib
import re
import stat
import sys

root = pathlib.Path(sys.argv[1]).resolve(strict=True)
manifest = pathlib.Path(sys.argv[2]).resolve(strict=True)
safe = re.compile(r"[A-Za-z0-9_./-]+")
lines = manifest.read_text(encoding="utf-8").splitlines()
if len(lines) < 6 or lines[4] != "--files--":
    raise SystemExit("active source manifest header is invalid")
try:
    declared_count = int(lines[3].removeprefix("file_count="))
except ValueError as exc:
    raise SystemExit("active source manifest count is invalid") from exc
records = lines[5:]
if declared_count != len(records):
    raise SystemExit("active source manifest count differs")
seen = set()
for record in records:
    fields = record.split("\t")
    if len(fields) != 4:
        raise SystemExit("active source manifest record is invalid")
    mode_text, size_text, digest, relative = fields
    if (
        mode_text not in {"0644", "0755"}
        or not size_text.isascii()
        or not size_text.isdigit()
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
        or not safe.fullmatch(relative)
        or relative.startswith(".")
        or ".." in relative
        or relative in seen
    ):
        raise SystemExit("active source manifest record is unsafe")
    seen.add(relative)
    path = root / relative
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise SystemExit("active source member is not a single-link regular file")
    if f"0{stat.S_IMODE(metadata.st_mode):03o}" != mode_text or metadata.st_size != int(size_text):
        raise SystemExit("active source member metadata differs")
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise SystemExit("active source member digest differs")
PY
  (cd "$remote_dir" && PYTHONDONTWRITEBYTECODE=1 "$remote_dir/.venv/bin/python" -I -c 'import dotenv, psycopg2, yaml') || \
    die "active control runtime import proof failed" 65
  printf 'MOONCEN_CONTROL_RELEASE_VERIFIED=%s:%s:%s\n' "$expected_commit" "$expected_archive_sha256" "$expected_tree_sha256"
  exit 0
fi

if [ "$#" -ne 7 ]; then
  die "expected release-id, deploy-user, upload-dir, commit, archive digest, tree digest, and target role" 64
fi

release_id="$1"
deploy_user="$2"
upload_dir="$3"
expected_commit="$4"
expected_archive_sha256="$5"
expected_tree_sha256="$6"
expected_role="$7"

[[ "$release_id" =~ ^[0-9a-f]{32}$ ]] || die "release id is invalid" 64
[[ "$deploy_user" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || die "deploy user is invalid" 64
[[ "$expected_commit" =~ ^[0-9a-f]{40}$|^[0-9a-f]{64}$ ]] || die "commit is invalid" 64
[[ "$expected_archive_sha256" =~ ^[0-9a-f]{64}$ ]] || die "archive digest is invalid" 64
[[ "$expected_tree_sha256" =~ ^[0-9a-f]{64}$ ]] || die "tree digest is invalid" 64
[ "$expected_role" = crawler-control ] || die "target role must be crawler-control" 64
[[ "$upload_dir" =~ ^/tmp/mooncen-control-upload-${release_id}\.[A-Za-z0-9]{6,32}$ ]] || \
  die "unprivileged upload path is outside the fixed staging namespace"
[ "$(id -u)" -eq 0 ] || die "root privileges are required" 77
[ "$(hostname -s 2>/dev/null || true)" = gen1db ] || die "release activation is pinned to hostname gen1db"

for command in \
  awk cat chattr chmod chown cmp find getent grep gzip install lsattr mkdir mv \
  python3.11 rm rmdir sed sha256sum sort ssh-keygen stat sync systemctl tail tar \
  tr uname wc; do
  command -v "$command" >/dev/null 2>&1 || die "required verifier command is unavailable: $command" 69
done

node_role_file=/etc/mooncen-node-role
[ -f "$node_role_file" ] && [ ! -L "$node_role_file" ] || die "node role file is missing or unsafe"
[ "$(stat -c '%U:%G:%a' "$node_role_file")" = root:root:644 ] || die "node role file must be root:root mode 0644"
[ "$(cat "$node_role_file")" = crawler-control ] || die "remote node role is not crawler-control"
getent group mooncen >/dev/null || die "root bootstrap must create the mooncen group before a control release"
[ "$(uname -m)" = x86_64 ] || die "hash-locked control runtime currently supports x86_64 only"
command -v python3.11 >/dev/null 2>&1 || die "root bootstrap must provide CPython 3.11"
python3.11 -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)' || \
  die "root bootstrap Python does not match the CPython 3.11 runtime lock"

allowed_signers=/etc/mooncen/crawler-control-release-allowed-signers
[ -f "$allowed_signers" ] && [ ! -L "$allowed_signers" ] || die "root-owned control release allowed-signers policy is missing"
[ "$(stat -c '%U:%G:%a' "$allowed_signers")" = root:root:644 ] || \
  die "control release allowed-signers policy must be root:root mode 0644"

upload_owner="$(stat -c '%U' "$upload_dir" 2>/dev/null || true)"
upload_mode="$(stat -c '%a' "$upload_dir" 2>/dev/null || true)"
[ -d "$upload_dir" ] && [ ! -L "$upload_dir" ] || die "upload directory is unavailable or unsafe"
[ "$upload_owner" = "$deploy_user" ] && [ "$upload_mode" = 700 ] || die "upload directory ownership or mode is unsafe"
[ "$(find "$upload_dir" -mindepth 1 -maxdepth 1 -printf '%f\n' | LC_ALL=C sort | tr '\n' ' ')" = \
  "crawler-control-release.env crawler-control-release.sig crawler-control-release.tar.gz crawler-control-release.tree " ] || \
  die "upload directory must contain exactly the four reviewed artifact files"

archive_source="$upload_dir/crawler-control-release.tar.gz"
manifest_source="$upload_dir/crawler-control-release.tree"
metadata_source="$upload_dir/crawler-control-release.env"
signature_source="$upload_dir/crawler-control-release.sig"
for source_file in "$archive_source" "$manifest_source" "$metadata_source" "$signature_source"; do
  [ -f "$source_file" ] && [ ! -L "$source_file" ] || die "uploaded artifact is not a regular non-symlink file"
  [ "$(stat -c '%U:%a:%h' "$source_file")" = "$deploy_user:600:1" ] || \
    die "uploaded artifact ownership, mode, or link count is unsafe"
done

# Authenticate the candidate through the separately bootstrapped fixed helper
# before the fail-closed activation gate.  This is read-only: the helper does
# not issue a backup receipt in this mode and neither helper writes below /opt.
trust_helper=/usr/local/libexec/mooncen-crawler-control-root-trust
[ -f "$trust_helper" ] && [ ! -L "$trust_helper" ] && \
  [ "$(stat -c '%U:%G:%a:%h' "$trust_helper")" = root:root:755:1 ] || \
  die "fixed crawler-control root trust helper is missing or unsafe"
trust_policy=/etc/mooncen/crawler-control-root-trust.policy
[ -f "$trust_policy" ] && [ ! -L "$trust_policy" ] && \
  [ "$(stat -c '%U:%G:%a:%h' "$trust_policy")" = root:root:400:1 ] && \
  [ "$(wc -l < "$trust_policy")" -eq 5 ] || die "fixed root trust policy is missing or unsafe"
trust_helper_sha256="$(read_exact_value ROOT_TRUST_HELPER_SHA256 "$trust_policy")" || \
  die "fixed root trust helper digest policy is invalid"
[[ "$trust_helper_sha256" =~ ^[0-9a-f]{64}$ ]] && \
  [ "$(sha256sum "$trust_helper" | awk '{print $1}')" = "$trust_helper_sha256" ] || \
  die "fixed root trust helper differs from the out-of-band policy"
"$trust_helper" verify-candidate \
  --release-id "$release_id" \
  --deploy-user "$deploy_user" \
  --candidate-dir "$upload_dir" \
  --expected-commit "$expected_commit" \
  --expected-archive-sha256 "$expected_archive_sha256" \
  --expected-tree-sha256 "$expected_tree_sha256" >/dev/null || \
  die "fixed root trust helper rejected the signed candidate" 65

# The deploy wrapper is not an authorization boundary: an operator with the
# fixed-helper sudo grant can invoke this helper directly.  Until the helper
# can independently authenticate the fresh gen1db backup/restore attestation
# and the activation is implemented without a two-rename availability gap,
# stop before creating the root lock or writing anything below /opt.  Keep the
# verifier below hardened and tested so that removing this gate is itself a
# small, explicit, reviewed change.
die "NOT READY: direct crawler-control activation is disabled until the root helper independently verifies the backup attestation and an atomic activation contract. No root path mutation occurred." 70

lock_dir=/opt/.mooncen-control-deploy.lock
if ! mkdir -m 0700 -- "$lock_dir" 2>/dev/null; then
  die "another crawler-control release operation holds the root lock" 75
fi
lock_held=1
candidate=/opt/.mooncen-control-candidate-$release_id
ingress=/opt/.mooncen-control-ingress-$release_id
previous=/opt/.mooncen-control-previous-$release_id
failed=/opt/.mooncen-control-failed-$release_id
remote_dir=/opt/mooncen
activated=0
candidate_created=0
ingress_created=0
previous_moved=0
previous_move_started=0
activation_move_started=0
candidate_identity=""

remove_current_transaction_tree() {
  cleanup_path="$1"
  expected_prefix="$2"
  case "$cleanup_path" in
    "$expected_prefix$release_id") ;;
    *) return 1 ;;
  esac
  if [ -L "$cleanup_path" ]; then
    return 1
  fi
  if [ -e "$cleanup_path" ]; then
    [ -d "$cleanup_path" ] || return 1
    [ "$(stat -c '%U' "$cleanup_path")" = root ] || return 1
    if [ -f "$cleanup_path/.deploy-info" ] && [ ! -L "$cleanup_path/.deploy-info" ]; then
      chattr -i -- "$cleanup_path/.deploy-info" 2>/dev/null || true
    fi
    rm -rf -- "$cleanup_path"
  fi
}

cleanup_and_exit() {
  status="$1"
  trap - EXIT INT TERM HUP
  cleanup_failed=0
  if [ "$status" -ne 0 ]; then
    if [ "$activated" -eq 1 ] || [ "$activation_move_started" -eq 1 ]; then
      if [ -n "$candidate_identity" ] && \
         [ -d "$remote_dir" ] && [ ! -L "$remote_dir" ] && \
         [ "$(stat -c '%d:%i' "$remote_dir" 2>/dev/null || true)" = "$candidate_identity" ]; then
        if [ -f "$remote_dir/.deploy-info" ] && [ ! -L "$remote_dir/.deploy-info" ]; then
          chattr -i -- "$remote_dir/.deploy-info" 2>/dev/null || true
        fi
        if [ ! -e "$failed" ] && [ ! -L "$failed" ] && \
           mv -T -- "$remote_dir" "$failed" 2>/dev/null; then
          activated=0
          activation_move_started=0
        else
          cleanup_failed=1
        fi
      elif [ -n "$candidate_identity" ] && \
           [ -d "$candidate" ] && [ ! -L "$candidate" ] && \
           [ "$(stat -c '%d:%i' "$candidate" 2>/dev/null || true)" = "$candidate_identity" ]; then
        # The atomic rename itself did not take effect.
        activated=0
        activation_move_started=0
      else
        cleanup_failed=1
      fi
    fi
    if [ "$previous_moved" -eq 1 ]; then
      if [ ! -e "$remote_dir" ] && [ ! -L "$remote_dir" ] && \
         [ -d "$previous" ] && [ ! -L "$previous" ] && \
         mv -T -- "$previous" "$remote_dir" 2>/dev/null; then
        previous_moved=0
        previous_move_started=0
        sync -f /opt 2>/dev/null || cleanup_failed=1
      elif [ ! -e "$previous" ] && [ ! -L "$previous" ] && \
           [ -d "$remote_dir" ] && [ ! -L "$remote_dir" ]; then
        # The prior-release rename itself did not take effect.
        previous_moved=0
        previous_move_started=0
      else
        cleanup_failed=1
      fi
    fi
    if [ "$candidate_created" -eq 1 ]; then
      remove_current_transaction_tree "$candidate" /opt/.mooncen-control-candidate- || cleanup_failed=1
    fi
    if [ "$ingress_created" -eq 1 ]; then
      remove_current_transaction_tree "$ingress" /opt/.mooncen-control-ingress- || cleanup_failed=1
    fi
  fi
  if [ "$lock_held" -eq 1 ]; then
    rmdir -- "$lock_dir" 2>/dev/null || cleanup_failed=1
  fi
  if [ "$cleanup_failed" -ne 0 ]; then
    printf 'crawler-control release cleanup requires manual recovery for release-id %s\n' "$release_id" >&2
    status=70
  fi
  exit "$status"
}

on_exit() {
  cleanup_and_exit "$?"
}

on_signal() {
  cleanup_and_exit "$1"
}

trap on_exit EXIT
trap 'on_signal 129' HUP
trap 'on_signal 130' INT
trap 'on_signal 143' TERM

# Never accumulate unbounded full-release trees.  A prior successful release
# or failed forensic tree blocks the next deployment until a reviewed operator
# handles it; this transaction only removes its own candidate/ingress paths.
prior_previous_count="$(find /opt -mindepth 1 -maxdepth 1 -name '.mooncen-control-previous-*' -printf x | wc -c)"
prior_failed_count="$(find /opt -mindepth 1 -maxdepth 1 -name '.mooncen-control-failed-*' -printf x | wc -c)"
prior_candidate_count="$(find /opt -mindepth 1 -maxdepth 1 \( -name '.mooncen-control-candidate-*' -o -name '.mooncen-control-ingress-*' \) -printf x | wc -c)"
[ "$prior_previous_count" -eq 0 ] || die "one retained previous release already requires reviewed retirement" 75
[ "$prior_failed_count" -eq 0 ] || die "a failed release tree requires manual forensic review" 75
[ "$prior_candidate_count" -eq 0 ] || die "an incomplete release transaction requires manual recovery" 75

for protected_path in "$candidate" "$ingress" "$previous" "$failed"; do
  if [ -e "$protected_path" ] || [ -L "$protected_path" ]; then
    die "release-specific root path already exists and requires manual review: $protected_path" 75
  fi
done
ingress_created=1
candidate_created=1
install -d -o root -g root -m 0700 -- "$ingress" "$candidate"

# Copy out of the deploy user's mutable directory first.  Every later read is
# from root-owned storage and both digests are rechecked after the copy.
install -o root -g root -m 0600 -- "$archive_source" "$ingress/release.tar.gz"
install -o root -g root -m 0400 -- "$manifest_source" "$ingress/release.tree"
install -o root -g root -m 0400 -- "$metadata_source" "$ingress/release.env"
install -o root -g root -m 0400 -- "$signature_source" "$ingress/release.sig"
sync -f "$ingress/release.tar.gz"
sync -f "$ingress/release.tree"
sync -f "$ingress/release.env"
sync -f "$ingress/release.sig"
sync -f "$ingress"

[ "$(sha256sum "$ingress/release.tar.gz" | awk '{print $1}')" = "$expected_archive_sha256" ] || \
  die "root-owned archive digest does not match the reviewed digest" 65
[ "$(sha256sum "$ingress/release.tree" | awk '{print $1}')" = "$expected_tree_sha256" ] || \
  die "root-owned canonical tree digest does not match the reviewed digest" 65
if ! ssh-keygen -Y verify \
  -f "$allowed_signers" \
  -I mooncen-crawler-control-release \
  -n mooncen-crawler-control-release \
  -s "$ingress/release.sig" < "$ingress/release.env" >/dev/null 2>&1; then
  die "release metadata is not signed by the fixed root trust policy" 65
fi

[ "$(read_exact_value FORMAT "$ingress/release.env")" = mooncen-crawler-control-release-v1 ] || die "metadata format is invalid" 65
[ "$(read_exact_value DEPLOY_COMMIT "$ingress/release.env")" = "$expected_commit" ] || die "metadata commit is invalid" 65
[ "$(read_exact_value DEPLOY_ARCHIVE_SHA256 "$ingress/release.env")" = "$expected_archive_sha256" ] || die "metadata archive digest is invalid" 65
[ "$(read_exact_value DEPLOY_TREE_SHA256 "$ingress/release.env")" = "$expected_tree_sha256" ] || die "metadata tree digest is invalid" 65
[ "$(read_exact_value NODE_ROLE "$ingress/release.env")" = crawler-control ] || die "metadata role is invalid" 65
[ "$(read_exact_value TARGET_HOST "$ingress/release.env")" = gen1db ] || die "metadata host is invalid" 65
[ "$(wc -l < "$ingress/release.env")" -eq 6 ] || die "metadata contains unreviewed fields" 65

uncompressed_bytes="$(gzip -cd -- "$ingress/release.tar.gz" | wc -c)"
[[ "$uncompressed_bytes" =~ ^[0-9]+$ ]] && [ "$uncompressed_bytes" -le 100663296 ] || \
  die "archive exceeds the bounded uncompressed size" 65

python3.11 -I - "$ingress/release.tar.gz" <<'PY'
import pathlib
import re
import sys
import tarfile

archive_path = pathlib.Path(sys.argv[1])
safe = re.compile(r"[A-Za-z0-9_./-]+")
seen = set()
with tarfile.open(archive_path, mode="r:gz") as archive:
    for member in archive:
        name = member.name
        parts = pathlib.PurePosixPath(name).parts
        if (
            not safe.fullmatch(name)
            or pathlib.PurePosixPath(name).is_absolute()
            or any(part in {"", ".", ".."} for part in parts)
            or name in seen
            or not member.isfile()
            or member.pax_headers
            or member.size < 0
            or member.size > 16 * 1024 * 1024
        ):
            raise SystemExit("unsafe tar member")
        seen.add(name)
if not seen:
    raise SystemExit("empty release archive")
PY

archive_list="$ingress/archive.list"
tar -tzf "$ingress/release.tar.gz" > "$archive_list"
[ -s "$archive_list" ] || die "archive is empty" 65
entry_count=0
while IFS= read -r archive_path; do
  [[ "$archive_path" =~ ^[A-Za-z0-9_./-]+$ ]] || die "archive contains a non-canonical path" 65
  [[ "$archive_path" != /* && "$archive_path" != *".."* && "$archive_path" != */./* ]] || \
    die "archive path escapes the release root" 65
  entry_count=$((entry_count + 1))
done < "$archive_list"
[ "$entry_count" -eq "$(LC_ALL=C sort -u "$archive_list" | wc -l)" ] || die "archive contains duplicate paths" 65

tar --extract --gzip --file "$ingress/release.tar.gz" --directory "$candidate" \
  --no-same-owner --same-permissions --no-overwrite-dir
if find "$candidate" -xdev \( -type l -o \! -type d -a \! -type f \) -print -quit | grep -q .; then
  die "extracted release contains a link or special file" 65
fi
if find "$candidate" -xdev -type f -links +1 -print -quit | grep -q .; then
  die "extracted release contains a hard-linked file" 65
fi

embedded_manifest="$candidate/.mooncen-control-tree.manifest"
[ -f "$embedded_manifest" ] && [ ! -L "$embedded_manifest" ] || die "archive lacks its embedded canonical manifest" 65
cmp -s -- "$ingress/release.tree" "$embedded_manifest" || die "embedded canonical manifest differs from the reviewed manifest" 65
[ "$(sed -n '1p' "$embedded_manifest")" = "format=mooncen-crawler-control-tree-v1" ] || die "tree manifest format is invalid" 65
[ "$(sed -n '2p' "$embedded_manifest")" = "commit=$expected_commit" ] || die "tree manifest commit is invalid" 65
[ "$(sed -n '3p' "$embedded_manifest")" = "node_role=crawler-control" ] || die "tree manifest role is invalid" 65
[ "$(sed -n '5p' "$embedded_manifest")" = "--files--" ] || die "tree manifest header is invalid" 65
file_count="$(sed -n '4s/^file_count=//p' "$embedded_manifest")"
[[ "$file_count" =~ ^[1-9][0-9]{0,3}$ ]] || die "tree manifest file count is invalid" 65
[ "$entry_count" -eq $((file_count + 1)) ] || die "archive entry count differs from the canonical manifest" 65

verified_paths="$ingress/verified-paths"
: > "$verified_paths"
verified_count=0
while IFS=$'\t' read -r expected_mode expected_size expected_sha256 relative_path extra; do
  [ -z "${extra:-}" ] || die "tree manifest record has extra fields" 65
  [[ "$expected_mode" =~ ^0(644|755)$ ]] || die "tree manifest file mode is invalid" 65
  [[ "$expected_size" =~ ^[0-9]+$ ]] || die "tree manifest file size is invalid" 65
  [[ "$expected_sha256" =~ ^[0-9a-f]{64}$ ]] || die "tree manifest file digest is invalid" 65
  [[ "$relative_path" =~ ^[A-Za-z0-9_./-]+$ ]] || die "tree manifest path is invalid" 65
  [[ "$relative_path" != .* && "$relative_path" != /* && "$relative_path" != *".."* ]] || \
    die "tree manifest contains a reserved or escaping path" 65
  release_file="$candidate/$relative_path"
  [ -f "$release_file" ] && [ ! -L "$release_file" ] || die "manifest file is absent from extracted release" 65
  [ "$(stat -c '%a:%s:%h' "$release_file")" = "${expected_mode#0}:$expected_size:1" ] || \
    die "extracted release mode, size, or link count differs from manifest" 65
  [ "$(sha256sum "$release_file" | awk '{print $1}')" = "$expected_sha256" ] || \
    die "extracted release file digest differs from manifest" 65
  printf '%s\n' "$relative_path" >> "$verified_paths"
  verified_count=$((verified_count + 1))
done < <(tail -n +6 "$embedded_manifest")
[ "$verified_count" -eq "$file_count" ] || die "tree manifest record count is invalid" 65
[ "$verified_count" -eq "$(LC_ALL=C sort -u "$verified_paths" | wc -l)" ] || die "tree manifest contains duplicate paths" 65

actual_paths="$ingress/actual-paths"
find "$candidate" -xdev -type f -printf '%P\n' | grep -v '^\.mooncen-control-tree\.manifest$' | LC_ALL=C sort > "$actual_paths"
LC_ALL=C sort "$verified_paths" -o "$verified_paths"
cmp -s -- "$verified_paths" "$actual_paths" || die "extracted tree contains files outside the exact allowlist" 65

runtime_lock="$candidate/deploy/ubuntu/requirements-crawler-control.lock"
[ -f "$runtime_lock" ] && [ ! -L "$runtime_lock" ] || die "hash-locked control runtime contract is missing" 65
runtime_lock_sha256="$(sha256sum "$runtime_lock" | awk '{print $1}')"
python3.11 -m venv --copies "$candidate/.venv"
"$candidate/.venv/bin/python" -m pip --isolated install \
  --disable-pip-version-check \
  --no-input \
  --no-cache-dir \
  --no-deps \
  --only-binary=:all: \
  --require-hashes \
  --requirement "$runtime_lock"
"$candidate/.venv/bin/python" -I - <<'PY'
from importlib.metadata import version
expected = {
    "psycopg2-binary": "2.9.12",
    "python-dotenv": "1.2.2",
    "PyYAML": "6.0.3",
}
for distribution, required in expected.items():
    if version(distribution) != required:
        raise SystemExit(f"runtime distribution mismatch: {distribution}")
import dotenv
import psycopg2
import yaml
PY
for smoke_module in \
  ops_agent.crawler_control_scheduler \
  ops_agent.crawler_control_finalizer \
  ops_agent.crawler_control_metrics \
  ops_agent.crawler_release_action_worker \
  ops_agent.crawler_release_publisher \
  tools.crawler_control_backup_attestation \
  tools.approve_crawler_control_batch; do
  (cd "$candidate" && PYTHONDONTWRITEBYTECODE=1 "$candidate/.venv/bin/python" -X utf8 -m "$smoke_module" --help >/dev/null)
done
if find "$candidate/.venv" -xdev ! -type l \( ! -user root -o -perm /022 \) -print -quit | grep -q .; then
  die "generated control runtime is not root-owned and non-writable by group/world" 65
fi
runtime_manifest="$candidate/.mooncen-control-runtime.manifest"
python3.11 -I - "$candidate" "$runtime_manifest" <<'PY'
import hashlib
import os
import pathlib
import stat
import sys

root = pathlib.Path(sys.argv[1]).resolve(strict=True)
venv = root / ".venv"
output = pathlib.Path(sys.argv[2])
records = []
for current, directories, files in os.walk(venv, topdown=True, followlinks=False):
    directories.sort()
    files.sort()
    for name in [*directories, *files]:
        path = pathlib.Path(current, name)
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        if metadata.st_uid != 0 or metadata.st_mode & 0o022:
            raise SystemExit("unsafe generated runtime owner or mode")
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if stat.S_ISLNK(metadata.st_mode):
            target = os.readlink(path)
            resolved = (path.parent / target).resolve()
            if os.path.isabs(target) or root not in (resolved, *resolved.parents):
                raise SystemExit("generated runtime symlink escapes the release")
            digest = hashlib.sha256(target.encode("utf-8")).hexdigest()
            records.append(f"link\t{digest}\t{relative}\t{target}")
            continue
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise SystemExit("generated runtime contains a special or hard-linked file")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        records.append(f"file\t{metadata.st_mode & 0o777:04o}\t{metadata.st_size}\t{digest}\t{relative}")
output.write_text(
    "format=mooncen-crawler-control-runtime-v1\n" + "\n".join(sorted(records)) + "\n",
    encoding="utf-8",
    newline="\n",
)
with output.open("rb") as handle:
    os.fsync(handle.fileno())
PY
chown root:root -- "$runtime_manifest"
chmod 0444 -- "$runtime_manifest"
runtime_tree_sha256="$(sha256sum "$runtime_manifest" | awk '{print $1}')"

for unit in \
  mooncen-crawler-control-scheduler.service \
  mooncen-crawler-control-finalizer.service \
  mooncen-crawler-release-publisher.service \
  mooncen-crawler-release-publisher.timer \
  mooncen-crawler-control-metrics.service \
  mooncen-crawler-control-metrics.timer; do
  ! systemctl is-active --quiet "$unit" || die "control unit must be stopped before release activation: $unit" 70
  ! systemctl is-enabled --quiet "$unit" || die "control unit must be disabled before release activation: $unit" 70
done

# Make every release path root-owned and immutable to the service accounts.
find "$candidate" -xdev -type d -exec chown root:mooncen {} + -exec chmod 0750 {} +
while IFS=$'\t' read -r source_mode _source_size _source_sha256 relative_path _extra; do
  chown root:mooncen -- "$candidate/$relative_path"
  chmod "${source_mode#0}" -- "$candidate/$relative_path"
done < <(tail -n +6 "$embedded_manifest")
chown root:root -- "$embedded_manifest"
chmod 0444 -- "$embedded_manifest"

deploy_info="$candidate/.deploy-info"
printf '%s\n' \
  'DEPLOY_INFO_FORMAT=mooncen-crawler-control-provenance-v1' \
  "DEPLOY_COMMIT=$expected_commit" \
  "DEPLOY_ARCHIVE_SHA256=$expected_archive_sha256" \
  "DEPLOY_TREE_SHA256=$expected_tree_sha256" \
  "RUNTIME_LOCK_SHA256=$runtime_lock_sha256" \
  "RUNTIME_TREE_SHA256=$runtime_tree_sha256" \
  'NODE_ROLE=crawler-control' \
  'TARGET_HOST=gen1db' \
  "RELEASE_ID=$release_id" > "$deploy_info"
chown root:root -- "$deploy_info"
chmod 0400 -- "$deploy_info"
chattr +i -- "$deploy_info"
lsattr -d -- "$deploy_info" | awk '{print $1}' | grep -q 'i' || die "deployment provenance did not become filesystem-immutable" 65

while IFS= read -r durable_file; do sync -f "$durable_file"; done < <(find "$candidate" -xdev -type f -print)
while IFS= read -r durable_dir; do sync -f "$durable_dir"; done < <(find "$candidate" -xdev -depth -type d -print)
sync -f /opt

if [ -e "$remote_dir" ] || [ -L "$remote_dir" ]; then
  [ -d "$remote_dir" ] && [ ! -L "$remote_dir" ] || die "/opt/mooncen is not an activatable regular directory"
  [ "$(stat -c '%U:%G:%a' "$remote_dir")" = root:mooncen:750 ] || die "existing release root ownership or mode is unsafe"
  [ -f "$remote_dir/.deploy-info" ] && [ ! -L "$remote_dir/.deploy-info" ] || die "existing release lacks immutable provenance"
  lsattr -d -- "$remote_dir/.deploy-info" | awk '{print $1}' | grep -q 'i' || die "existing release provenance is not immutable"
  previous_moved=1
  previous_move_started=1
  mv -T -- "$remote_dir" "$previous"
  previous_move_started=0
  sync -f /opt
fi
candidate_identity="$(stat -c '%d:%i' "$candidate")"
activation_move_started=1
mv -T -- "$candidate" "$remote_dir"
candidate_created=0
activated=1
activation_move_started=0
sync -f /opt

[ "$(stat -c '%U:%G:%a' "$remote_dir")" = root:mooncen:750 ] || die "activated release root verification failed" 65
[ "$(sha256sum "$remote_dir/.mooncen-control-tree.manifest" | awk '{print $1}')" = "$expected_tree_sha256" ] || \
  die "activated release tree digest verification failed" 65
[ "$(read_exact_value DEPLOY_COMMIT "$remote_dir/.deploy-info")" = "$expected_commit" ] || die "activated provenance commit verification failed" 65
[ "$(read_exact_value DEPLOY_ARCHIVE_SHA256 "$remote_dir/.deploy-info")" = "$expected_archive_sha256" ] || die "activated provenance archive verification failed" 65
[ "$(read_exact_value DEPLOY_TREE_SHA256 "$remote_dir/.deploy-info")" = "$expected_tree_sha256" ] || die "activated provenance tree verification failed" 65
[ "$(read_exact_value RUNTIME_LOCK_SHA256 "$remote_dir/.deploy-info")" = "$runtime_lock_sha256" ] || die "activated provenance runtime verification failed" 65
[ "$(read_exact_value RUNTIME_TREE_SHA256 "$remote_dir/.deploy-info")" = "$runtime_tree_sha256" ] || die "activated runtime-tree provenance verification failed" 65
[ "$(sha256sum "$remote_dir/.mooncen-control-runtime.manifest" | awk '{print $1}')" = "$runtime_tree_sha256" ] || die "activated runtime-tree manifest verification failed" 65
[ "$(read_exact_value NODE_ROLE "$remote_dir/.deploy-info")" = crawler-control ] || die "activated provenance role verification failed" 65
lsattr -d -- "$remote_dir/.deploy-info" | awk '{print $1}' | grep -q 'i' || die "activated provenance is not immutable" 65

# Root no longer needs the unprivileged upload or root ingress.  Exact regexes
# above and fixed release-id paths bound both removals to this transaction.
rm -rf -- "$ingress" "$upload_dir"
ingress_created=0
sync -f /opt
activated=0
rmdir -- "$lock_dir"
lock_held=0
trap - EXIT INT TERM HUP
printf 'MOONCEN_CONTROL_RELEASE_ACTIVATED=%s:%s:%s\n' "$expected_commit" "$expected_archive_sha256" "$expected_tree_sha256"
