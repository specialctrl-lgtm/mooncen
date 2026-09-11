#!/bin/bash
# Fixed root-side verifier and transactional activator for the legacy crawler owner.
set -euo pipefail
umask 077
PATH=/usr/sbin:/usr/bin:/sbin:/bin
LC_ALL=C
IFS=$' \t\n'
export PATH LC_ALL
unset BASH_ENV CDPATH ENV GLOBIGNORE LD_LIBRARY_PATH LD_PRELOAD PYTHONHOME PYTHONINSPECT PYTHONPATH PYTHONSTARTUP
hash -r

die() { printf 'gen1crawler release rejected: %s\n' "$1" >&2; exit "${2:-78}"; }
read_value() {
  awk -F= -v key="$1" '$1 == key { count++; value=substr($0,length(key)+2) } END { if(count != 1) exit 65; printf "%s",value }' "$2"
}
assert_host() {
  [ "$(id -u)" -eq 0 ] || die "root privileges are required" 77
  [ "$(hostname -s)" = gen1crawler ] || die "hostname is not gen1crawler"
  [ -f /etc/mooncen-node-role ] && [ ! -L /etc/mooncen-node-role ] || die "node role marker is missing"
  [ "$(stat -c '%U:%G:%a:%h' /etc/mooncen-node-role)" = root:root:644:1 ] || die "node role marker is unsafe"
  [ "$(cat /etc/mooncen-node-role)" = crawler ] || die "node role is not crawler"
  allowed=/etc/mooncen/gen1crawler-release-allowed-signers
  [ -f "$allowed" ] && [ ! -L "$allowed" ] || die "release allowed-signers policy is missing"
  [ "$(stat -c '%U:%G:%a:%h' "$allowed")" = root:root:644:1 ] || die "release allowed-signers policy is unsafe"
  command -v python3.12 >/dev/null || die "CPython 3.12 is required" 69
  python3.12 -I -c 'import sys; raise SystemExit(sys.version_info[:2] != (3,12))' || die "exact CPython 3.12 is required" 69
}

assert_host
if [ "$#" -eq 1 ] && [ "$1" = --verify-bootstrap ]; then
  for unit in mooncen-crawler-once.service mooncen-staging-apply.service mooncen-staging-apply-dry-run.service; do
    ! systemctl is-active --quiet "$unit" || die "one-shot unit is active: $unit" 70
  done
  printf 'gen1crawler-release-bootstrap-ok\n'
  exit 0
fi
if [ "$#" -eq 4 ] && [ "$1" = --verify-active ]; then
  verify_commit="$2"; verify_archive="$3"; verify_tree="$4"
  [[ "$verify_commit" =~ ^[0-9a-f]{40}$ ]] || die "verify commit is invalid" 64
  [[ "$verify_archive" =~ ^[0-9a-f]{64}$ && "$verify_tree" =~ ^[0-9a-f]{64}$ ]] || die "verify digest is invalid" 64
  marker=/opt/mooncen/.gen1crawler-release.env
  [ -L /opt/mooncen ] && [ -f "$marker" ] && [ ! -L "$marker" ] || die "active release marker is unavailable"
  [ "$(readlink -f /opt/mooncen)" = "/opt/mooncen-releases/$verify_commit" ] || die "active release target differs"
  [ "$(read_value DEPLOY_COMMIT "$marker")" = "$verify_commit" ] || die "active release commit differs"
  [ "$(read_value ARCHIVE_SHA256 "$marker")" = "$verify_archive" ] || die "active archive digest differs"
  [ "$(read_value TREE_SHA256 "$marker")" = "$verify_tree" ] || die "active tree digest differs"
  systemctl is-enabled --quiet mooncen-crawler.timer || die "crawler timer is not enabled"
  systemctl is-active --quiet mooncen-crawler.timer || die "crawler timer is not active"
  systemctl is-enabled --quiet mooncen-staging-apply.timer || die "staging timer is not enabled"
  systemctl is-active --quiet mooncen-staging-apply.timer || die "staging timer is not active"
  ! systemctl is-active --quiet mooncen-crawler.service || die "long-running crawler service is active"
  printf 'MOONCEN_GEN1CRAWLER_RELEASE_VERIFIED=%s:%s:%s\n' "$verify_commit" "$verify_archive" "$verify_tree"
  exit 0
fi
[ "$#" -eq 6 ] || die "expected release-id, deploy-user, upload-dir, commit, archive digest, and tree digest" 64
release_id="$1"; deploy_user="$2"; upload="$3"; commit="$4"; archive_sha="$5"; tree_sha="$6"
[[ "$release_id" =~ ^[0-9a-f]{32}$ ]] || die "release id is invalid" 64
[[ "$deploy_user" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || die "deploy user is invalid" 64
[[ "$commit" =~ ^[0-9a-f]{40}$ ]] || die "commit is invalid" 64
[[ "$archive_sha" =~ ^[0-9a-f]{64}$ && "$tree_sha" =~ ^[0-9a-f]{64}$ ]] || die "digest is invalid" 64
[[ "$upload" =~ ^/tmp/mooncen-gen1crawler-${release_id}\.[A-Za-z0-9]{8}$ ]] || die "upload path is outside the fixed namespace"
[ -d "$upload" ] && [ ! -L "$upload" ] || die "upload directory is unsafe"
[ "$(stat -c '%U:%a' "$upload")" = "$deploy_user:700" ] || die "upload directory ownership is unsafe"
[ "$(find "$upload" -mindepth 1 -maxdepth 1 -printf '%f\n' | sort | tr '\n' ' ')" = 'gen1crawler-release.env gen1crawler-release.sig gen1crawler-release.tar.gz gen1crawler-release.tree ' ] || die "upload directory has unexpected files"

archive="$upload/gen1crawler-release.tar.gz"; tree="$upload/gen1crawler-release.tree"
metadata="$upload/gen1crawler-release.env"; signature="$upload/gen1crawler-release.sig"
for file in "$archive" "$tree" "$metadata" "$signature"; do
  [ -f "$file" ] && [ ! -L "$file" ] || die "release artifact is unsafe"
  [ "$(stat -c '%U:%a:%h' "$file")" = "$deploy_user:600:1" ] || die "release artifact metadata is unsafe"
done
[ "$(sha256sum "$archive" | awk '{print $1}')" = "$archive_sha" ] || die "archive digest differs"
[ "$(sha256sum "$tree" | awk '{print $1}')" = "$tree_sha" ] || die "tree digest differs"
ssh-keygen -Y verify -f /etc/mooncen/gen1crawler-release-allowed-signers \
  -I mooncen-gen1crawler-release -n mooncen-gen1crawler-release \
  -s "$signature" <"$metadata" >/dev/null 2>&1 || die "metadata signature is invalid" 65
[ "$(wc -l <"$metadata")" -eq 7 ] || die "metadata field count is invalid"
[ "$(read_value FORMAT "$metadata")" = mooncen-gen1crawler-release-v1 ] || die "metadata format is invalid"
[ "$(read_value DEPLOY_COMMIT "$metadata")" = "$commit" ] || die "metadata commit differs"
source_tree="$(read_value SOURCE_TREE "$metadata")"; [[ "$source_tree" =~ ^[0-9a-f]{40}$ ]] || die "source tree is invalid"
[ "$(read_value ARCHIVE_SHA256 "$metadata")" = "$archive_sha" ] || die "metadata archive differs"
[ "$(read_value TREE_SHA256 "$metadata")" = "$tree_sha" ] || die "metadata tree differs"
[ "$(read_value NODE_ROLE "$metadata")" = crawler ] || die "metadata role differs"
[ "$(read_value TARGET_HOSTNAME "$metadata")" = gen1crawler ] || die "metadata hostname differs"

for unit in mooncen-crawler-once.service mooncen-staging-apply.service mooncen-staging-apply-dry-run.service; do
  ! systemctl is-active --quiet "$unit" || die "one-shot unit is active: $unit" 70
done
exec 9>/run/lock/mooncen-gen1crawler-release.lock
flock -n 9 || die "another crawler release activation is running" 75

base=/opt/mooncen-releases
candidate="$base/$commit"
transactions="$base/.transactions"
install -d -o root -g root -m 0755 "$base"
install -d -o root -g root -m 0700 "$transactions"
[ ! -e "$candidate" ] && [ ! -L "$candidate" ] || die "release already exists" 75
install -d -o root -g root -m 0750 "$candidate"
cleanup_candidate=1
cleanup() {
  status=$?
  trap - EXIT INT TERM HUP
  if [ "$status" -ne 0 ] && [ "$cleanup_candidate" -eq 1 ] && [ -d "$candidate" ] && [ ! -L "$candidate" ]; then rm -rf -- "$candidate"; fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP; trap 'exit 130' INT; trap 'exit 143' TERM

python3.12 -I - "$archive" "$tree" "$candidate" "$commit" "$source_tree" <<'PY'
import gzip, hashlib, os, pathlib, re, sys, tarfile
archive_path, tree_path, target_arg, commit, source_tree = sys.argv[1:]
target = pathlib.Path(target_arg).resolve(strict=True)
lines = pathlib.Path(tree_path).read_text("utf-8").splitlines()
expected = ["mooncen-gen1crawler-release-v1", f"commit={commit}", f"source_tree={source_tree}"]
if lines[:3] != expected or len(lines) < 6 or lines[4] != "--files--": raise SystemExit("tree manifest header differs")
try: count = int(lines[3].removeprefix("file_count="))
except ValueError: raise SystemExit("tree manifest count is invalid")
records = {}
for line in lines[5:]:
    mode, digest, size, name = line.split(" ", 3)
    if name in records or not re.fullmatch(r"[A-Za-z0-9_./+@\-]+", name): raise SystemExit("tree path is invalid")
    records[name] = (int(mode, 8), digest, int(size))
if len(records) != count: raise SystemExit("tree manifest count differs")
seen = set()
with tarfile.open(archive_path, "r:gz") as tar:
    for member in tar:
        name = member.name.rstrip("/")
        if member.isdir():
            continue
        if not member.isfile() or name not in records: raise SystemExit("archive contains an unreviewed member")
        mode, digest, size = records[name]
        if member.size != size or size > 64 * 1024 * 1024: raise SystemExit("archive member size differs")
        destination = target.joinpath(*pathlib.PurePosixPath(name).parts)
        if target not in destination.parents: raise SystemExit("archive path escapes target")
        destination.parent.mkdir(parents=True, exist_ok=True)
        stream = tar.extractfile(member)
        content = stream.read(size + 1) if stream else b""
        if len(content) != size or hashlib.sha256(content).hexdigest() != digest: raise SystemExit("archive member digest differs")
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
        with os.fdopen(fd, "wb") as output: output.write(content)
        seen.add(name)
if seen != set(records): raise SystemExit("archive is incomplete")
PY

python3.12 -m venv "$candidate/.venv"
"$candidate/.venv/bin/python" -m pip install --disable-pip-version-check --no-input --require-hashes -r "$candidate/requirements.lock"
"$candidate/.venv/bin/python" -I -m compileall -q "$candidate/Crawler" "$candidate/DB" "$candidate/tools" "$candidate/run_crawlers.py"
"$candidate/.venv/bin/python" -I "$candidate/run_crawlers.py" --help >/dev/null
chown -R root:mooncen "$candidate"
find "$candidate" -type d -exec chmod 0750 {} +

state="$transactions/$release_id"
install -d -o root -g root -m 0700 "$state"
units=(
  mooncen-branch-coordinates.service
  mooncen-crawler.service
  mooncen-crawler-once.service
  mooncen-crawler.timer
  mooncen-staging-apply.service
  mooncen-staging-apply@.service
  mooncen-staging-apply-dry-run.service
  mooncen-staging-apply-dry-run@.service
  mooncen-staging-apply.timer
)
for unit in "${units[@]}"; do
  systemctl is-enabled "$unit" >"$state/$unit.enabled" 2>/dev/null || true
  systemctl is-active "$unit" >"$state/$unit.active" 2>/dev/null || true
  [ ! -e "/etc/systemd/system/$unit" ] || cp -a "/etc/systemd/system/$unit" "$state/$unit.file"
done
systemctl stop mooncen-crawler.timer mooncen-staging-apply.timer mooncen-crawler.service || true

log_root=/var/lib/mooncen-crawler/logs
install -d -o mooncen-crawler -g mooncen-crawler -m 0750 /var/lib/mooncen-crawler "$log_root"
if [ -L /opt/mooncen/logs ]; then
  [ "$(readlink -f /opt/mooncen/logs)" = "$log_root" ] || die "active log link has an unexpected target"
elif [ -d /opt/mooncen/logs ]; then
  [ -z "$(find /opt/mooncen/logs -xdev \( -type l -o \! -type d -a \! -type f \) -print -quit)" ] || \
    die "active log tree contains links or special files"
  cp -a /opt/mooncen/logs/. "$log_root/"
elif [ -e /opt/mooncen/logs ]; then
  die "active log path is unsafe"
fi
ln -s "$log_root" "$candidate/logs"

old_kind=none; old_target=
if [ -L /opt/mooncen ]; then
  old_kind=link; old_target="$(readlink /opt/mooncen)"
elif [ -d /opt/mooncen ]; then
  old_kind=directory; old_target="$base/legacy-${release_id}"
  mv /opt/mooncen "$old_target"
elif [ -e /opt/mooncen ]; then
  die "/opt/mooncen is unsafe"
fi

rollback=1
restore() {
  status=$?
  trap - EXIT INT TERM HUP
  if [ "$rollback" -eq 1 ]; then
    rm -f /opt/mooncen
    if [ "$old_kind" = link ]; then ln -s "$old_target" /opt/mooncen; fi
    if [ "$old_kind" = directory ]; then mv "$old_target" /opt/mooncen; fi
    for unit in "${units[@]}"; do
      if [ -f "$state/$unit.file" ]; then cp -a "$state/$unit.file" "/etc/systemd/system/$unit"; else rm -f "/etc/systemd/system/$unit"; fi
    done
    systemctl daemon-reload || true
    for unit in "${units[@]}"; do
      grep -qx enabled "$state/$unit.enabled" 2>/dev/null && systemctl enable "$unit" >/dev/null 2>&1 || true
      grep -qx active "$state/$unit.active" 2>/dev/null && systemctl start "$unit" >/dev/null 2>&1 || true
    done
    if [ -d "$candidate" ] && [ ! -L "$candidate" ]; then rm -rf -- "$candidate"; fi
  fi
  exit "$status"
}
trap restore EXIT; trap 'exit 129' HUP; trap 'exit 130' INT; trap 'exit 143' TERM

ln -s "$candidate" /opt/.mooncen-next-$release_id
mv -Tf /opt/.mooncen-next-$release_id /opt/mooncen
for unit in "${units[@]}"; do
  install -o root -g root -m 0644 "$candidate/deploy/ubuntu/systemd/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl disable mooncen-crawler.service >/dev/null 2>&1 || true
systemctl enable mooncen-crawler.timer mooncen-staging-apply.timer >/dev/null
systemctl start mooncen-crawler.timer mooncen-staging-apply.timer
systemctl is-enabled --quiet mooncen-crawler.timer
systemctl is-active --quiet mooncen-crawler.timer
systemctl is-enabled --quiet mooncen-staging-apply.timer
systemctl is-active --quiet mooncen-staging-apply.timer
! systemctl is-active --quiet mooncen-crawler.service || die "long-running crawler service survived activation"

install -o root -g root -m 0444 "$metadata" "$candidate/.gen1crawler-release.env"
rollback=0; cleanup_candidate=0
rm -rf -- "$state" "$upload"
printf 'MOONCEN_GEN1CRAWLER_RELEASE_ACTIVATED=%s:%s:%s\n' "$commit" "$archive_sha" "$tree_sha"
