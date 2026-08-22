# MoonCen NAS Backup And Restore

MoonCen can back up the active server to NAS host `wtr-nas`.

## Backup Location

Default remote path:

```text
mooncen_backup@wtr-nas:/volume2/homes/mooncen_backup/mooncen-backup/<server-hostname>/
```

Directory layout:

```text
db/         PostgreSQL custom-format dumps
app/        /opt/mooncen archive without .venv, node_modules, dist, logs, .git
config/     nginx config archive and MoonCen systemd unit archive
manifests/  signed backup metadata and detached SSH signatures
```

## Required NAS Setup

Create a NAS user and allow SSH login:

```text
user: mooncen_backup
host: wtr-nas
path: /volume2/homes/mooncen_backup/mooncen-backup
```

The active server must be able to run:

```bash
ssh mooncen_backup@wtr-nas "mkdir -p /volume2/homes/mooncen_backup/mooncen-backup"
rsync --version
```

Use SSH keys for non-interactive Ops Console runs.

Cloud currently has this backup public key. Add it to NAS user's
`~/.ssh/authorized_keys` for `mooncen_backup`:

```text
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICUD2N/O869+ra+LgxzjB61CqyGhl4cOKdLWiu6orUx/ mooncen-cloud-to-wtr-nas
```

The backup unit runs as the dedicated nologin `mooncen-backup` OS account. Its
NAS identity is fixed at `/etc/mooncen/backup-ssh-key`; login-user home SSH keys
are not accepted as a fallback.

If `mooncen_backup` has a `nologin` shell, SSH can reject the account before key
authentication succeeds. On the NAS, log in with an administrator account and
change only the login shell field:

```bash
grep '^mooncen_backup:' /etc/passwd
sudo cp /etc/passwd "/etc/passwd.backup.$(date +%Y%m%d%H%M%S)"
sudo sed -i 's#^\(mooncen_backup:[^:]*:[^:]*:[^:]*:[^:]*:[^:]*:\).*#\1/bin/sh#' /etc/passwd
grep '^mooncen_backup:' /etc/passwd
```

Then verify the key file and backup directory permissions:

```bash
sudo mkdir -p ~mooncen_backup/.ssh /volume2/homes/mooncen_backup/mooncen-backup
sudo touch ~mooncen_backup/.ssh/authorized_keys
sudo chown -R mooncen_backup:users ~mooncen_backup/.ssh /volume2/homes/mooncen_backup/mooncen-backup
sudo chmod 700 ~mooncen_backup/.ssh
sudo chmod 600 ~mooncen_backup/.ssh/authorized_keys
sudo chmod 750 /volume2/homes/mooncen_backup/mooncen-backup
```

## Required Server Trust Material

Setup fails closed until all of the following files have been provisioned outside
the repository. The backup service group is fixed to `mooncen-backup`.

Create the local service identity before installing its externally provisioned
keys (setup converges the same nologin contract):

```bash
sudo groupadd --system mooncen-backup 2>/dev/null || true
id mooncen-backup >/dev/null 2>&1 || sudo useradd --system \
  --gid mooncen-backup --no-create-home --home-dir /nonexistent \
  --shell /usr/sbin/nologin mooncen-backup
```

```text
/etc/mooncen/backup-age-key.txt                 root:root   0600
/etc/mooncen/backup-ssh-key                     root:mooncen-backup 0640
/etc/mooncen/backup-known-hosts                 root:mooncen-backup 0640
/etc/mooncen/backup-manifest-signing-key        root:mooncen-backup 0640
/etc/mooncen/backup-manifest-allowed-signers    root:root   0644
```

Create a dedicated Ed25519 manifest key once. It must not be the NAS login key:

```bash
sudo install -d -o root -g root -m 0751 /etc/mooncen
sudo ssh-keygen -q -t ed25519 -N '' \
  -C mooncen-backup-manifest -f /etc/mooncen/backup-manifest-signing-key
sudo chown root:mooncen-backup /etc/mooncen/backup-manifest-signing-key
sudo chmod 0640 /etc/mooncen/backup-manifest-signing-key
sudo -u mooncen-backup ssh-keygen -y \
  -f /etc/mooncen/backup-manifest-signing-key \
  | awk '{print "mooncen-backup " $1 " " $2}' \
  | sudo tee /etc/mooncen/backup-manifest-allowed-signers >/dev/null
sudo chown root:root /etc/mooncen/backup-manifest-allowed-signers
sudo chmod 0644 /etc/mooncen/backup-manifest-allowed-signers
```

Pin the NAS host key. `ssh-keyscan` does not authenticate what it receives, so
compare the displayed SHA256 fingerprint with the fingerprint read from the NAS
console or another independent trusted channel before installing it:

```bash
known_hosts_candidate="$(mktemp)"
ssh-keyscan -p 22 -t ed25519 wtr-nas >"$known_hosts_candidate"
ssh-keygen -lf "$known_hosts_candidate"
# Stop here until the fingerprint has been verified out of band.
sudo install -o root -g mooncen-backup -m 0640 "$known_hosts_candidate" \
  /etc/mooncen/backup-known-hosts
rm -f "$known_hosts_candidate"
```

Provision the age identity separately as `root:root` mode `0600`, and make sure
its public recipient exactly matches `BACKUP_AGE_RECIPIENT`. Securely escrow the
age identity and the allowed-signers public file away from the NAS; both are
required for disaster recovery. The signing private key is required to create
new backups and should be escrowed separately from NAS storage as well.

## SSH And Tailscale

Backup scripts use regular non-interactive OpenSSH, not Tailscale SSH browser authentication.

There are two different SSH paths:

1. Ops Console or deploy host → MoonCen server, configured by `config/deploy_servers.json`.
2. MoonCen server → NAS, configured by `BACKUP_HOST` in `/opt/mooncen/.env`.

If the output shows an address like `100.75.187.63`, the failing path is usually path 1. In that case, change `config/deploy_servers.json` on the machine running Ops Console:

```json
{
  "servers": {
    "cloud": {
      "server": "<Oracle public IP or normal SSH DNS name>"
    }
  }
}
```

Do not use a Tailscale MagicDNS name such as `cloud` for unattended backup, deploy, or Ops Console operations.

The backup SSH command ignores the server user's SSH config and accepts only the
pre-provisioned pinned host key:

```bash
BACKUP_SSH_CONFIG=/dev/null
BACKUP_KNOWN_HOSTS_FILE=/etc/mooncen/backup-known-hosts
```

This prevents `~/.ssh/config` host rules or `ProxyCommand` settings from accidentally routing backup traffic through Tailscale SSH. Override this only when a dedicated non-interactive SSH config is required.

If `BACKUP_HOST=wtr-nas` resolves to a Tailscale `100.64.0.0/10` address, the scripts stop before upload by default and print:

```text
backup_host_resolves_to_tailscale=wtr-nas/<ip>
```

Use one of these two options:

1. Set `BACKUP_HOST` to a NAS LAN/public SSH hostname or IP that accepts normal SSH keys. Example:

```bash
BACKUP_HOST=192.168.0.50
# or
BACKUP_HOST=wtr-nas.local
```

2. Keep the Tailscale private IP route, but use normal OpenSSH on the NAS instead of Tailscale SSH:

```bash
BACKUP_HOST=wtr-nas
BACKUP_ALLOW_TAILSCALE_IP=1
BACKUP_SSH_CONFIG=/dev/null
BACKUP_PORT=
```

This still runs normal OpenSSH with `-F /dev/null -i /etc/mooncen/backup-ssh-key` as the dedicated backup account. It does not run `tailscale ssh`, and it does not use browser authentication. Leave `BACKUP_PORT` empty for port 22, or pin the bracketed `[host]:port` token when using a non-standard port.

NAS appliances can reject rsync or SFTP for restricted backup users even when OpenSSH key login works. The backup upload therefore uses legacy `scp -O` for DB dumps, app archives, config archives, and manifests.

### Expired Tailscale Auth Link

If a backup run shows this Tailscale message:

```text
This authentication link could not be located. It may have expired or you may be logged into the wrong account.
```

do not continue with browser authentication for production backup. It means the backup job is still being routed through Tailscale SSH or a Tailscale address.

Use this recovery path on the server running the backup:

```bash
cd /opt/mooncen
getent ahostsv4 wtr-nas
grep -E '^(BACKUP_HOST|BACKUP_ALLOW_TAILSCALE_IP|BACKUP_SSH_CONFIG|BACKUP_PORT)=' .env
```

Required production values for current `wtr-nas` routing:

```bash
BACKUP_HOST=wtr-nas
BACKUP_ALLOW_TAILSCALE_IP=1
BACKUP_SSH_CONFIG=/dev/null
BACKUP_PORT=
```

If switching to a non-Tailscale LAN/public NAS address later, use:

```bash
BACKUP_HOST=<NAS LAN IP or normal SSH DNS name>
BACKUP_ALLOW_TAILSCALE_IP=0
BACKUP_SSH_CONFIG=/dev/null
```

Then test regular OpenSSH directly:

```bash
sudo -u mooncen-backup ssh -F /dev/null \
  -i /etc/mooncen/backup-ssh-key \
  -o IdentitiesOnly=yes \
  -o BatchMode=yes \
  -o PasswordAuthentication=no \
  -o KbdInteractiveAuthentication=no \
  -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile=/etc/mooncen/backup-known-hosts \
  -o GlobalKnownHostsFile=/dev/null \
  mooncen_backup@<NAS LAN IP or normal SSH DNS name> \
  "echo backup-ssh-ok"
```

If that command fails, fix NAS OpenSSH access or the `mooncen_backup` authorized key first. Do not enable Tailscale SSH for the systemd backup job.

## Ops Console Operations

Operations tab:

- `Backup status`: show `mooncen-backup.timer` and latest backup service logs.
- `Backup list`: list latest DB/app/config backups on NAS.
- `Run backup`: dump DB, archive app/config, upload to NAS.
- `Restore test`: restore latest DB dump into a temporary database and validate counts. This does not change production DB.
- `Restore latest`: verify and restore into a uniquely named candidate DB, validate it, then perform a stopped-service database-name swap. This is destructive and should only be used after `Restore test`.

## Automatic Backup

Deployment installs these systemd units:

```text
mooncen-backup.service
mooncen-backup.timer
mooncen-backup-restore-test.service
mooncen-backup-restore-test.timer
```

Schedule:

```text
backup: 03:30 every day, with up to 15 minutes randomized delay
restore test: monthly, with up to 6 hours randomized delay
```

The deploy script enables `mooncen-backup.timer` on the active server and disables it on standby servers.

## Direct Commands

Run on the active server:

```bash
cd /opt/mooncen
mooncenctl backup-status
mooncenctl backup-list
mooncenctl backup-once
mooncenctl backup-test
sudo systemctl start mooncen-backup-restore-test.service
sudo systemd-run --wait --collect --pipe \
  --unit=mooncen-backup-restore-manual \
  --property=Type=oneshot \
  --property=LoadCredential=backup-ssh-key:/etc/mooncen/backup-ssh-key \
  --property=RuntimeMaxSec=3600 \
  --property=MemoryMax=2G \
  --property=TasksMax=128 \
  --property=UMask=0077 \
  --property=PrivateTmp=true \
  --property=PrivateDevices=true \
  --property=ProtectSystem=strict \
  --property=ProtectHome=read-only \
  --property=RestrictAddressFamilies="AF_UNIX AF_INET AF_INET6" \
  --property="CapabilityBoundingSet=CAP_SETUID CAP_SETGID CAP_DAC_READ_SEARCH CAP_CHOWN CAP_AUDIT_WRITE" \
  --setenv=RESTORE_CONFIRM=RESTORE_MOONCEN \
  /bin/bash /usr/local/libexec/mooncen-backup/mooncen_restore_latest_from_wtr_nas.sh
```

Restore jobs keep their PostgreSQL administration privileges, but OpenSSH never
opens the group-readable canonical private key as root. `LoadCredential` exposes
an ephemeral `root:root` mode `0400` copy below `/run/credentials` for the
restore process and removes it with the unit. Direct root execution without that
credential fails closed. The manual command is a privileged disaster-recovery
operation and intentionally carries the same runtime and resource bounds as the
scheduled restore test.

Validated `/etc/mooncen/backup.env` settings (change them through setup, not an
ad-hoc service environment):

```bash
BACKUP_HOST=wtr-nas
BACKUP_USER=mooncen_backup
BACKUP_ROOT=/volume2/homes/mooncen_backup/mooncen-backup
BACKUP_IDENTITY_FILE=/etc/mooncen/backup-ssh-key
BACKUP_KNOWN_HOSTS_FILE=/etc/mooncen/backup-known-hosts
BACKUP_MANIFEST_SIGNING_KEY=/etc/mooncen/backup-manifest-signing-key
BACKUP_MANIFEST_ALLOWED_SIGNERS=/etc/mooncen/backup-manifest-allowed-signers
BACKUP_MAX_AGE_SECONDS=604800
BACKUP_ALLOW_TAILSCALE_IP=1
BACKUP_SSH_CONFIG=/dev/null
BACKUP_PORT=
SERVER_NAME=cloud
BACKUP_FILE=latest
```

The systemd units deliberately do not use `EnvironmentFile=`. Each backup/list/
restore script first fixes the path to `/etc/mooncen/backup.env`, requires an
exact `root:mooncen-backup` mode `0640` regular non-symlink contract, and only
then sources it. Setup regenerates this file from validated deployment inputs.

## Restore Safety

Every restore accepts only a timestamped `.dump.age` whose SHA256 entry is in an
authentic Ed25519-signed manifest. The signed server, database, UTC timestamp,
source database size, and exact dump filename must match the requested restore
context. The signature is checked against the local `allowed_signers` trust point before the dump is downloaded. Remote and local
sizes, work-disk capacity, decrypted size, PostgreSQL data-disk capacity, and
`pg_restore --list` are checked before restore.

`Restore latest` leaves the production database untouched while it restores,
reapplies the role contract, and checks row-count thresholds in a uniquely named
candidate database. It then stops all database-sensitive services, renames the
old database aside, and renames the candidate into the production name. Runtime
database roles remain fenced while an unconditional SQL contract probe runs. If
the API was previously active, only the API is then started, with nginx and
cloudflared still stopped, and its loopback-only `{"status":"ready"}` health
response is verified before the API is stopped again. A pre-commit probe failure
restores the old database name and leaves every managed unit stopped for review.
The old database is deleted only after all probes pass and every unit is
re-quiesced; this deletion is the irreversible commit point. Only then are the
original daemons, ingress, interrupted oneshots, and timers resumed. A
post-commit resume failure never rolls back data that may already have been
written to the restored database; instead all managed units are stopped and an
explicit committed-resume failure is reported. PostgreSQL database rename cannot be wrapped in a
transaction, so the services remain stopped throughout both rename operations
and the script tracks and repairs either intermediate name state on failure.
Backup creation, scheduled restore testing, and production restore share one
root-owned `flock`, so they cannot race. Candidate databases deny connections
during restore and swap boundaries. Both restore paths discard archived owners,
ACLs, and tablespace destinations, converge application object ownership,
reapply the role allowlist, and run runtime-role permission probes.

Default restore bounds are 64 GiB for encrypted and decrypted dumps, 1 GiB free
space reserve in the work filesystem, 2 GiB free reserve in the PostgreSQL data
filesystem, a signed source-database-size allowance (with a 4x dump-size floor),
2 GiB for app archives, and 256 MiB per config archive. Adjust these in
the validated deployment environment before setup when the real dataset requires
different limits.

The signed UTC timestamp must exactly match the dump filename and defaults to a
maximum age of seven days. This prevents an NAS-only attacker from silently
replaying an old signed backup as `latest`. For an explicitly reviewed disaster
recovery from older media, pass `ALLOW_STALE_SIGNED_BACKUP=1` only to the manual
production restore command. The scheduled restore test never enables this
override. Plaintext or unsigned legacy dumps are never accepted.

Frontend/backend code is archived for recovery reference, but production app restore is intentionally not automated yet. Code should normally be restored from Git/deploy artifacts; nginx and systemd archives cover non-secret server configuration. Secrets and `.env` files are deliberately excluded and must be recovered from the external secret store.
