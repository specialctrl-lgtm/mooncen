#!/bin/bash
# One-time, console-authenticated installation of the fixed release trust boundary.
set -euo pipefail
umask 077
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

die() { printf 'gen1crawler uploader bootstrap failed: %s\n' "$1" >&2; exit "${2:-78}"; }
[ "$(id -u)" -eq 0 ] || die "root privileges are required" 77
[ "$(hostname -s)" = gen1crawler ] || die "hostname is not gen1crawler"
[ -f /etc/mooncen-node-role ] && [ ! -L /etc/mooncen-node-role ] || die "node role marker is missing"
[ "$(cat /etc/mooncen-node-role)" = crawler ] || die "node role is not crawler"

deploy_user=sgm
allowed_source=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --deploy-user) [ "$#" -ge 2 ] || die "missing deploy user" 64; deploy_user="$2"; shift 2 ;;
    --allowed-signers) [ "$#" -ge 2 ] || die "missing allowed-signers path" 64; allowed_source="$2"; shift 2 ;;
    *) die "unknown argument: $1" 64 ;;
  esac
done
[[ "$deploy_user" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || die "deploy user is invalid" 64
getent passwd "$deploy_user" >/dev/null || die "deploy user does not exist"
[ -n "$allowed_source" ] && [ -f "$allowed_source" ] && [ ! -L "$allowed_source" ] || die "allowed-signers source is unsafe"
grep -Eq '^mooncen-gen1crawler-release[[:space:]]+(ssh-ed25519|ecdsa-sha2-nistp256|sk-ssh-ed25519@openssh.com)[[:space:]]+' "$allowed_source" || \
  die "allowed-signers policy lacks the fixed release principal"

source_dir="$(cd "$(dirname "$0")" && pwd -P)"
helper_source="$source_dir/activate_gen1crawler_release.sh"
[ -f "$helper_source" ] && [ ! -L "$helper_source" ] || die "activator source is unsafe"
install -d -o root -g root -m 0755 /usr/local/libexec
install -d -o root -g root -m 0755 /etc/mooncen
install -o root -g root -m 0755 "$helper_source" /usr/local/libexec/mooncen-activate-gen1crawler-release
install -o root -g root -m 0644 "$allowed_source" /etc/mooncen/gen1crawler-release-allowed-signers

sudoers_tmp="$(mktemp /etc/sudoers.d/.mooncen-gen1crawler-release.XXXXXX)"
trap 'rm -f -- "$sudoers_tmp"' EXIT
printf '%s ALL=(root) NOPASSWD: /usr/local/libexec/mooncen-activate-gen1crawler-release\n' "$deploy_user" >"$sudoers_tmp"
chmod 0440 "$sudoers_tmp"
visudo -cf "$sudoers_tmp" >/dev/null
mv -fT "$sudoers_tmp" /etc/sudoers.d/mooncen-gen1crawler-release
trap - EXIT
/usr/local/libexec/mooncen-activate-gen1crawler-release --verify-bootstrap
printf 'gen1crawler release uploader bootstrap installed\n'
