# Distributed crawler control-plane deployment

> **NOT READY — this document is a design record, not an executable runbook.**
> `crawler-control-install` and `setup_distributed_crawler_control.sh` still
> stop before SSH, filesystem installation, or database mutation. There is no
> override. The repository now contains the signed atomic root-owned
> control-only release transport, but the orchestration gate remains closed
> until an independently bootstrapped root helper consumes a release-bound,
> machine-verified `gen1db` backup/restore receipt.
> Worker inventory validation is ready, but
> `setup_distributed_crawler_worker.sh` and
> `enroll_distributed_crawler_worker.sh` are also unconditionally blocked
> before filesystem, systemd, credential-registry, or database mutation.

The atomic root-owned control-only release transport and its canonical
release-tree digest do not substitute for the independent database recovery
gate: a fresh, machine-verified `gen1db` backup/restore attestation described
below is still required.

Worker bootstrap source now has a separate review seam:
`tools/build_crawler_worker_bootstrap_release.py` rebuilds an exact clean Git
commit and binds its signed metadata to one reviewed worker key, DNS host,
kernel hostname, topology digest, rollout position, enabled/canary state, and
the exact concurrency/MemoryHigh/MemoryMax/CPUQuota drop-in digest. The fixed
root verifier is `activate_crawler_worker_bootstrap_release.sh`; its OpenSSH
signature namespace is `mooncen-crawler-worker-bootstrap-release`, and it also
requires root-owned `/etc/mooncen-worker-key` plus node role `crawler-worker`.
`requirements-crawler-worker.lock` now pins all 35 crawler-only direct and
transitive distributions to exact CPython 3.12 Linux x86_64 wheel hashes. The
root activator accepts only a root-owned offline wheelhouse whose hash set is
exactly equal to that lock; it never resolves dependencies or contacts an
index as root. Versioned releases live below
`/opt/mooncen-worker/releases/<release-id>`, and one relative `current`
symlink is replaced atomically on the same filesystem. A private fsynced
transaction journal recovers prepared/published/activated crash windows,
rolls a post-rename prepared orphan back, restores the prior pointer on
failure, and retains at most the active release plus two predecessors.

The current production topology still has `crawlerMode=legacy` and both
workers `enabled=false`, so both Windows transport and root activator return
`NOT READY` before SSH/root mutation respectively. Activation becomes
reachable only from a future clean signed commit whose authoritative topology
parser proves distributed mode and a canary-first contiguous enabled prefix.
The old mutable `/opt/mooncen` worker setup path remains independently blocked
and is not a trust root. Before a future activation, an out-of-band root
bootstrap must install the exact 35 verified wheels at
`/var/cache/mooncen-worker/wheelhouse`, the fixed helper, node/worker identity
files, allowed-signers policy, CPython 3.12, and the `mooncen` group.
The fixed activator is currently **initial-bootstrap-only**: if any reviewed
worker unit is already installed it exits `NOT READY` before changing the
`current` pointer. A later source/unit update requires a separate signed unit
convergence transaction; accepting old units while switching to incompatible
new source would create an update deadlock or mixed-release boot state.

The reviewed target control authority is DNS host `gen1db`. It owns the shared
writable staging PostgreSQL database, queue, per-agent bindings, fenced attempt
evidence, release desired state, held batch decisions, central control
services, and the pinned staging applier. It is not the production database;
Web/API and the production PostgreSQL database remain on `cloud`.
The corresponding topology service keys are `staging_database` and
`crawler_control`; both must resolve to node `gen1db` before control enable.

The legacy crawler remains the production scheduler on `gen1crawler` during
installation and canary. After the explicit cutover, `gen1crawler` and any
additional crawler hosts are worker-only. This repository procedure does not
prove that the remote transition happened: an authenticated inventory of both
`gen1db` and `gen1crawler` is required before enabling or disabling a recurring
unit.

The reviewed provider manifest remains compact, but the central scheduler
deterministically expands aggregate owners into concrete provider tasks before
enqueue. With the current manifest this produces 434 non-overlapping jobs
instead of 42 top-level jobs; every worker still claims one job at a time with
`FOR UPDATE SKIP LOCKED`. Workers never invent or subdivide work locally.
Process-level failed and partial collections consume the bounded retry budget
with backoff before dead-lettering. Invalid job scopes and malformed/fenced
result envelopes remain non-retryable security failures.

The database identities are intentionally separate:

| Component | PostgreSQL group | Credential use |
| --- | --- | --- |
| scheduler | `mooncen_crawler_control` | enqueue/reap crawler jobs |
| publisher | `mooncen_crawler_publisher` | read release desired state |
| finalizer | `mooncen_crawler_finalizer` | seal held evidence |
| approver | `mooncen_crawler_approver` | reviewed held-to-approved transition |
| release admin | `mooncen_crawler_release_admin` | reviewed artifact/rollout/desired-state writes |
| worker | `mooncen_crawler_worker` | its own queue/staging lease |
| reporter | `mooncen_crawler_reporter` | its own desired state/report rows |
| metrics | `mooncen_crawler_observer` | bounded metric columns only |

Never share usernames or passwords between these components. Production and
staging require `OPS_CRAWLER_AUTO_PROMOTION_ENABLED=false`; approval is always
an explicit, separate command. `mooncen-staging-apply.timer` remains enabled
through canary, cutover, and rollback because it is the pinned downstream that
applies approved batches to primary.

## Blocked `gen1db` preparation design (not executable)

Do not run control-plane installation commands on any host yet. The future
installer is pinned to authenticated host `gen1db`; it must never run on
`cloud` or a worker. Before that path can be enabled, automation must create
and verify a fresh backup/restore attestation containing the existing staging
database endpoint, PostgreSQL port, database name and immutable identity,
owner, extensions, schema digests, TLS identity, backup object digest,
completion time, restore-test result, free disk, and current systemd state.
The endpoint must be DNS host `gen1db`; automation must not silently create a
replacement local cluster merely because a historical document mentions port
`55432`.

The confirmed database must already contain the normal base and Ops schemas,
including branches, courses, crawl batches, `ops_agents`, `ops_jobs`, legacy
staging snapshots, and `mooncen_schema_migrations`. The explicit installer is
the only path that creates `ops_crawler_control_database_marker`; generic
primary database setup does not read the dedicated migration directory.

Prepare root-owned mode 0600 copies of:

- `crawler-control-schema.env.example`
- `crawler-control-scheduler.env.example`
- `crawler-release-publisher.env.example`
- `crawler-control-finalizer.env.example`
- `crawler-control-approver.env.example`
- `crawler-release-admin.env.example`
- `crawler-control-metrics.env.example`

Generate a distinct URL-safe password for every file, for example:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

The schema login must be a temporary `CREATEROLE` administrator that can `SET
ROLE` to `OPS_CRAWLER_SCHEMA_OBJECT_OWNER`. Revoke it after installation. Do
not reuse its password. All files must use the exact same canonical staging DB
host, port, and name. Remote access requires `DB_SSLMODE=verify-full` and a
protected CA readable by the relevant service account.

An installation created before the fingerprint registry will eventually need
one authenticated bootstrap under the installer lock. That migration is also
blocked; do not invoke `tools.bootstrap_crawler_credential_registry` directly
to bypass the installer. The future guarded workflow must authenticate the
exact set of marked logins over TLS, reject credential reuse, and only then
write active keyed fingerprints.

The Windows `crawler-control-install` action and direct Linux installer remain
non-operational because no fresh attestation has been issued and authenticated
on the real `gen1db`, and the outer handoff remains deliberately closed.
The release seam is present for review in
`tools/build_crawler_control_release.py`,
`deploy/ubuntu/deploy_crawler_control_from_windows.ps1`, and
`deploy/ubuntu/activate_crawler_control_release.sh`. It rebuilds the exact clean
Git commit, packages only an exact file allowlist, recomputes the reviewed
42-owner/434-concrete-provider snapshot, rejects links, submodules, mutable
paths, dynamic/local dependency gaps, and verifies both archive SHA-256 and a
canonical release-tree digest.

The root activator's verification path accepts only metadata signed by the fixed OpenSSH
allowed-signers policy, copies the unprivileged upload into a root-owned `/opt`
ingress, verifies again, fsyncs the result, creates a CPython 3.11 x86_64 venv
using `requirements-crawler-control.lock --require-hashes --only-binary`, runs
control-component import/`--help` smoke checks, records a canonical runtime
manifest, and prepares a guarded candidate/previous rollback transaction. `.deploy-info`
is root-owned mode 0400 and filesystem-immutable. None of these scripts accepts
credentials on argv or stdin.

This is deliberately **not an executable release handoff yet**. The root helper
itself returns `NOT READY` before creating its `/opt` lock or writing any root
path. This closes the direct-helper/direct-transport bypass around the outer
Windows backup gate. The gate stays in place until the helper can independently
authenticate the fresh backup/restore attestation and the current two-rename
replacement is replaced by an activation contract without a missing
`/opt/mooncen` window. The dormant rollback code uses signal-specific nonzero
status, inode identity across rename boundaries, removes only the current
transaction's candidate/ingress, and refuses another deployment while any
retained previous/failed tree awaits reviewed handling; it does not silently
accumulate full venv trees.

Before that transport can ever be enabled, a separate root bootstrap must
provide the `mooncen` group, exact CPython 3.11 on x86_64, node role
`crawler-control`, and the root-owned mode 0644 non-symlink trust policy at
`/etc/mooncen/crawler-control-release-allowed-signers`. It must also install
the reviewed activator as root-owned mode 0755 at
`/usr/local/libexec/mooncen-activate-crawler-control-release` and grant the
deploy identity sudo access only to that fixed helper, not to `bash`, `sh`,
`install`, `mv`, or a wildcard command. The transport verifies the helper hash
against the exact commit-materialized activator before invoking it. A reviewed release
signer signs the generated `crawler-control-release.env` with namespace
`mooncen-crawler-control-release`; the private signing key never reaches a
target or deployment command. The action also requires the independently
reviewed archive and tree digests. Privileged command lookup is pinned to the
root-owned system PATH, and active provenance is checked through the fixed root
helper because `.deploy-info` is intentionally unreadable by the deploy user.
Direct transport/helper invocation cannot activate a release while the root
gate above remains present.

The repository module is not the first-install trust root. The reviewed source
for the independent helper is
`deploy/ubuntu/crawler_control_root_trust.py`, but deployment automation must
never copy it from `/opt/mooncen`. A separately reviewed copy must be installed
manually/out of band as root-owned mode 0755 at
`/usr/local/libexec/mooncen-crawler-control-root-trust`; it is **not installed
on gen1db by this repository change**. The same root ceremony installs the
fixed evidence engine and pins its digest in the root-owned
`crawler-control-root-trust.policy`. The offline-only
`tools/build_crawler_control_root_trust_bundle.py` materializes all three files
from one exact clean commit, records every source/target, SHA-256, byte count,
`root:root` owner/group and mode, and marks remote automation false. Its
canonical manifest must be signed out of band with namespace
`mooncen-crawler-control-root-bootstrap-v1`; this repository does not transfer
or install that bundle. The activator independently hashes the installed helper
against the root-owned mode 0400 policy before calling it.

That fixed helper authenticates the signed candidate, invokes only the absolute
digest-pinned evidence engine for the real backup/isolated restore, generates a
256-bit nonce, and signs canonical receipt JSON with a gen1db-local OpenSSH
key. The receipt binds the commit, archive/tree digests, release signing
principal, candidate metadata/signature hashes, recovery evidence hash,
`gen1db:5432/mooncen_staging`, nonce and expiry. Neither its private signing key
nor the evidence HMAC key is sent to Windows. The exact JSON contract is
`config/crawler_control_backup_receipt.schema.json`.

The activator now calls only read-only `verify-candidate` before its existing
`NOT READY` gate. The dormant installer calls `verify-receipt`, then
`consume-receipt` immediately before the first schema apply. Consumption still
returns `NOT READY`: the exact future ledger is documented in
`DB/crawler_control_migrations/20260812_001_install_receipt_consumption.sql`.
The checksummed migration, exact owner/constraint/ACL validation, canonical
receipt audit bytes, database-clock expiry and unique digest/nonce/release-id
insert primitive are implemented. No runtime role receives ledger privileges,
and every `roles.sql` convergence explicitly revokes its broad grants again.

`DB/roles_body.sql` is the include-safe, idempotent form without transaction
tokens; existing primary/restore paths retain the mechanically equivalent
`DB/roles.sql` transaction wrapper. The dormant atomic coordinator now uses one
SERIALIZABLE transaction and transaction advisory lock, runs both role passes
and every marker/control/staging/ACL check without an intermediate commit,
inserts the receipt last, and commits once. Fault-injection tests cover every
phase and prove zero commit on failure.

Durable mutation nevertheless remains blocked. Activation and install must
share a root OS lock or stable release-specific inode to prevent `/opt/mooncen`
replacement while waiting for the DB lock. PostgreSQL advisory locks are scoped
to one database while role DDL is cluster-wide, so every MoonCen role installer
must also share a host-level lock. Until those seams and the activator's no-gap
rename are reviewed, the Windows action, activator, direct schema `--apply`, and
installer remain blocked before release or database mutation.

### Fresh backup/restore attestation contract

`tools.crawler_control_backup_attestation` is pinned to Linux hostname
`gen1db`, DNS/TLS server name `gen1db`, port `5432`, database
`mooncen_staging`, and `verify-full` TLS. The issuer accepts no caller-provided
backup digest, restore database name, result, timestamps, object list, or row
counts. As root it holds a non-blocking flock, verifies that the local cluster
is the same PostgreSQL system identity reached over verified TLS, creates a
fresh custom-format `pg_dump`, and checks `pg_restore --list`.

It rejects any orphan restore database, generates a CSPRNG restore database
name, rejects a pre-existing name,
creates the database from `template0`, revokes public connection, restores with
`--exit-on-error --single-transaction --no-owner --no-privileges
--no-tablespaces`, checks the
six required staging objects and non-empty course/branch counts, and compares
canonical schema-only digests. A `finally` path terminates connections and
independently attempts `DROP DATABASE IF EXISTS ... WITH (FORCE)` even after a
preceding cleanup failure. Cleanup failure prevents issuance and is reported
explicitly. The retained backup evidence is under the root-only
`/var/lib/mooncen-crawler-control-backup-attestation/evidence` tree.

Before dumping, the issuer reads `pg_database_size`, checks free space on both
the evidence and PostgreSQL filesystems (including an 8 GiB reserve and the
shared-filesystem combined case), and rejects a source database above 16 GiB.
Custom tablespaces, configured temporary tablespaces, and an external/symlinked
`pg_wal` are rejected rather than risking an unmeasured filesystem during the
restore drill. Evidence is capped at two exact root-owned generations and 32
GiB total; reaching the cap fails closed and requires reviewed retirement of a
specific old directory—nothing is automatically deleted.
The dump child also inherits a 16 GiB `RLIMIT_FSIZE`, so the cap is enforced
while writing rather than after the disk is full. Issuance is serialized by the
root-only OS flock; it does not claim to hold a PostgreSQL advisory lock across
the workflow. The before/after live schema probes detect a concurrent schema
change, while the dormant installer independently acquires its migration lock
after attestation verification.

Only after that workflow succeeds does the issuer atomically write canonical
JSON authenticated with HMAC-SHA256. It records the backup object SHA-256 and
size, database OID/owner/extensions, PostgreSQL system identifier and version,
resolved server address, TLS CA and peer-certificate SHA-256 fingerprints,
source/restored schema digests, restore counts/results, tool version, and UTC
issue/validity timestamps. The hard maximum validity and verifier override are
both bounded to 24 hours. The verifier rejects duplicate/non-canonical JSON,
wrong keys, stale evidence, symlinks, non-root ownership, permissive modes,
signature mismatch, and any change in the live database, TLS, extensions, or
schema contract. Passwords and HMAC key bytes are never printed.
Verification also reopens the canonical retained backup under the protected
evidence root and rechecks its owner, mode, link status, byte count, and
SHA-256. Deleting or changing the dump therefore invalidates the gate.

The retained local dump is a gate artifact, not a disaster-recovery copy. A
reviewed operator must additionally copy the encrypted backup and its signed
manifest to the existing `wtr-nas` backup target (or a separately mounted,
durable volume) and complete the normal restore drill before production
cutover. Do not point `EVIDENCE_ROOT` at the live PostgreSQL data directory.

There is intentionally no executable first-install command yet. The fixed
helper removes the circular trust dependency for candidate/receipt
verification, but the atomic database-consumption seam is still blocked.

`generate-key` refuses to overwrite an existing key. Keep
`/etc/mooncen/crawler-control-backup-attestation.key` root-owned mode `0600`;
do not copy it into the repository or send it through chat. It is internal to
the fixed helper and is not installer authorization. The dormant installer
accepts only these canonical signed-receipt arguments:

```text
--backup-receipt /var/lib/mooncen-crawler-control-root-trust/receipts/NONCE/receipt.json
--backup-receipt-signature /var/lib/mooncen-crawler-control-root-trust/receipts/NONCE/receipt.json.sig
--backup-receipt-nonce NONCE
```

The verifier is placed before the first schema/role write in the dormant Linux
installer. The earlier unconditional `NOT READY` exit remains in force, so
these preparation commands cannot install or enable the control plane.

The fail-closed order is:

1. Verify the confirmed base staging schema and exact non-extension owner.
2. Run include-safe `roles_body.sql` once to create safe NOLOGIN groups; without a DB marker its
   distributed privileges remain revoked.
3. As the object owner, create the exact database marker, apply the dedicated
   `DB/crawler_control_migrations/20260810_001_crawler_control_plane.sql`, and
   apply `staging_control_plane.sql` in one transaction.
4. Record immutable marker, migration, staging, and live RLS-policy digests.
5. Run `roles_body.sql` again for final grant convergence and record its checksum.
6. Provision six distinct central LOGINs with client-generated SCRAM
   verifiers; plaintext passwords are never sent in SQL.
7. Install and statically verify scheduler, publisher, finalizer, release-action,
   and metrics units; run every real credential through a read-only preflight.

The approver and release-admin environments are installed root:root 0600 and
are never exposed to the API or workers. The root-only release-action consumer
loads the release-admin environment; operator commands use the same protected
role contract. The installer keeps a root:root 0600 keyed-HMAC
fingerprint key and registry under `/etc/mooncen`; it stores no plaintext and
rejects password reuse across every central, worker, and reporter login.
Publisher, finalizer, scheduler, and observer use separate locked OS accounts.
The observer writes only
`/var/lib/mooncen-crawler-observer/mooncen_crawler_control.prom`; root installs
a fixed symlink in the root:root 0755 node_exporter textfile directory. The
node_exporter directory is never made group-writable.

An already enabled/live new control unit causes setup to stop before any DB or
password write. Replacing a protected installed environment requires
`--replace-protected-env` during a coordinated maintenance stop.

## Enroll a worker and reporter

The reviewed desired fleet lives in `config/production_topology.json` under
`crawlerWorkers`. It is not inferred from SSH aliases or DNS. The initial
inventory is deliberately pending and cannot start a distributed unit:

| order | worker key | topology DNS | exact kernel hostname | canary | limits | enabled |
| ---: | --- | --- | --- | --- | --- | --- |
| 1 | `wtr-linux` | `wtr-linux` | `sgm-standard-pc-i440fx-piix-1996` | yes | concurrency 1, high 4G, max 6G, CPU 300% | false |
| 2 | `gen1crawler` | `gen1crawler` | `gen1crawler` | no | concurrency 1, high 2G, max 4G, CPU 200% | false |

Legacy mode rejects any fleet entry with `enabled=true`. The inventory and
host/resource validation contract are ready, but **worker enrollment and host
installation are not**. Both scripts contain an unconditional `NOT READY`
gate before their first lock-directory creation or database operation. This
release cannot install even a stopped agent and cannot activate a canary.

The host installer must remain blocked until a bootstrap outside the mutable
`/opt/mooncen` tree verifies an externally signed archive and independently
reviewed digest, extracts it to a root-owned non-writable release, and invokes
the installer from that attested tree. A digest calculated by code inside the
same mutable tree is not an attestation.

For each host prepare separate root-owned mode 0600 copies of
`crawler-worker.env.example` and `crawler-release-reporter.env.example`.
`OPS_AGENT_ID`, `OPS_CRAWLER_WORKER_ID`, `ENVIRONMENT`, and
`OPS_CRAWLER_WORKER_HOSTNAME` must be identical in both files. The hostname is
the exact lowercase kernel hostname returned by `socket.gethostname()`, not a
DNS alias. Worker and reporter database credentials must be different.

The following is a dormant interface reference only. Do not run it; the
current command returns `NOT READY` without changing `gen1db`:

```bash
sudo /opt/mooncen/deploy/ubuntu/enroll_distributed_crawler_worker.sh \
  --schema-env /root/mooncen-control/schema.env \
  --worker-env /root/mooncen-control/worker-01.env \
  --reporter-env /root/mooncen-control/reporter-01.env \
  --confirm-staging-database mooncen_staging
```

The previous sequential single-login calls were removed. The dormant
`tools.provision_crawler_worker_pair` interface now validates the exact paired
environment files, reserves both keyed credential fingerprints in one atomic
registry write, and stages a root-owned mode `0600` one-time secret envelope.
It acquires one agent/pair advisory transaction lock, verifies the required RLS
tables/policies, and creates or rotates both LOGINs, exact memberships, UUID /
hostname / environment bindings in one serializable PostgreSQL transaction.
Rotation fails while the agent has a healthy or recent heartbeat, active lease,
running attempt, or active/draining desired state. A pre-commit failure rolls
back the database, removes the staged envelope, and restores the registry pair;
an uncertain commit leaves both fingerprints pending and the envelope staged
for explicit reconciliation rather than claiming partial success.

That helper is a repository contract, not an authorization to enroll. The
current shell is still sourced from mutable `/opt/mooncen` and exits before it
can consume the helper. It remains blocked until the signed root-owned worker
bootstrap invokes the fixed helper path and defines reviewed uncertain-commit
reconciliation. A dormant interface example is:

```bash
python -I -m tools.provision_crawler_worker_pair \
  --schema-env /root/mooncen-control/schema.env \
  --worker-env /root/mooncen-control/worker-01.env \
  --reporter-env /root/mooncen-control/reporter-01.env \
  --confirm-staging-database mooncen_staging \
  --secret-envelope /root/mooncen-control/worker-01-pair.json
```

The generic `provision_crawler_service_login.py` entry point also rejects the
`worker` and `reporter` components before acquiring its installer lock; it
remains available only for the independently provisioned central components.

### Install the remote worker host

Transfer the worker/reporter/release-agent environments, staging DB CA, release
HTTPS CA, and public OpenSSH allowed-signers file through the approved secret
channel. Never copy an artifact-signing private key. All three environments
must select the same worker/environment, worker and reporter must use distinct
DB logins, and the configured hostname must exactly equal the lowercase kernel
hostname. The queue, fenced staging, reporter, and shared-control endpoint must
be the same confirmed staging database.

The following is also a dormant interface reference. The current worker-host
installer returns `NOT READY` before creating accounts or files:

```bash
sudo /opt/mooncen/deploy/ubuntu/setup_distributed_crawler_worker.sh \
  --worker-env /root/mooncen-worker/worker.env \
  --reporter-env /root/mooncen-worker/reporter.env \
  --release-agent-env /root/mooncen-worker/release-agent.env \
  --db-ca /root/mooncen-worker/staging-db-root-ca.crt \
  --release-ca /root/mooncen-worker/release-https-root-ca.crt \
  --allowed-signers /root/mooncen-worker/crawler-release-allowed-signers \
  --confirm-staging-database mooncen_staging
```

The dormant future contract requires the worker DB environment to remain
readable only by the worker secret group. Before any eventual activation it
must reject unit-specific, dash-prefix, and type-wide drop-ins from `/etc`,
`/run`, `/usr/local/lib`, `/usr/lib`, and `/lib` systemd roots, except the exact
atomically installed resource profile. After `daemon-reload` it must verify
the effective fragment/drop-in paths plus User, ExecStart, EnvironmentFiles,
MemoryHigh, MemoryMax, CPUQuota, and sandbox properties for all worker service
units. This dormant verification code does not make the current installer
ready.

The future release agent must never perform an automatic first installation because a
worker without a previous release cannot roll back. To initialize that one
baseline, provide a locally transferred reviewed archive and its detached
OpenSSH signature plus independently confirmed metadata. The bootstrap path
accepts no URL, verifies size, SHA-256, allowed key identity, signature,
archive manifest and safe extraction, and does not start a service:

```bash
sudo /opt/mooncen/deploy/ubuntu/setup_distributed_crawler_worker.sh \
  --worker-env /root/mooncen-worker/worker.env \
  --reporter-env /root/mooncen-worker/reporter.env \
  --release-agent-env /root/mooncen-worker/release-agent.env \
  --db-ca /root/mooncen-worker/staging-db-root-ca.crt \
  --release-ca /root/mooncen-worker/release-https-root-ca.crt \
  --allowed-signers /root/mooncen-worker/crawler-release-allowed-signers \
  --confirm-staging-database mooncen_staging \
  --bootstrap-artifact /root/mooncen-worker/crawler-baseline.tar.gz \
  --bootstrap-signature /root/mooncen-worker/crawler-baseline.tar.gz.sig \
  --bootstrap-key-id mooncen-crawler-release-2026 \
  --bootstrap-code-version 2026.08.10.1 \
  --bootstrap-config-revision crawler-config-20260810 \
  --bootstrap-sha256 <independently-reviewed-lowercase-sha256> \
  --bootstrap-size-bytes <independently-reviewed-byte-count>
```

Canary enable is unavailable in this release. In a future reviewed release,
before explicit canary enable, publish an exact active/draining desired
state for this agent, exercise release-agent `check` and `dry-run`, change the
protected release policy to `OPS_CRAWLER_RELEASE_MODE=apply`, and ensure every
legacy crawler service/timer is manually stopped and disabled. Then rerun with
`--replace-protected-files --enable-reviewed-canary`. The installer rechecks
the immutable rollback baseline, full runtime desired-state binding, and
legacy states immediately before enable. It never stops or disables a legacy
unit; any enabled/live legacy unit is a hard conflict.

Artifact/desired-state HTTPS publication and tailnet ACL configuration remain
`gen1db` control-host operations. The worker installer consumes the pinned HTTPS policy
but does not create an HTTPS server or change network ACLs.

### Publish an artifact and create the canary rollout

The release-admin command is root-only. It verifies the detached OpenSSH
signature and independently reviewed SHA-256 before atomically publishing the
archive under the root-owned `public/artifacts/` directory. Register both the
reviewed baseline and target; a `code_version` can identify only one artifact.

```bash
cd /opt/mooncen
sudo .venv/bin/python -X utf8 -m tools.manage_crawler_release \
  --env-file /etc/mooncen/crawler-release-admin.env \
  register-artifact \
  --archive /root/review/crawler-2026.08.10.1.tar.gz \
  --signature /root/review/crawler-2026.08.10.1.tar.gz.sig \
  --expected-sha256 <reviewed-target-sha256> \
  --code-version 2026.08.10.1 \
  --config-revision <reviewed-config-revision> \
  --key-id mooncen-crawler-release-2026
```

Create a root-owned mode 0600 worker review file. Every identity must already
be enrolled and the hostname must be the exact kernel hostname:

```json
{
  "schema_version": 1,
  "environment": "staging",
  "workers": [
    {
      "worker_key": "worker-01",
      "agent_id": "11111111-1111-4111-8111-111111111111",
      "hostname": "worker-01",
      "cohort": "canary",
      "enabled": true
    }
  ]
}
```

Create the fenced generation-1 rollout only after both target and baseline
artifacts are registered and present on disk:

```bash
rollout_id=$(python3 -c 'import uuid; print(uuid.uuid4())')
sudo .venv/bin/python -X utf8 -m tools.manage_crawler_release \
  --env-file /etc/mooncen/crawler-release-admin.env \
  create-rollout --environment staging --rollout-id "$rollout_id" \
  --generation 1 --target-sha256 <reviewed-target-sha256> \
  --baseline-sha256 <reviewed-baseline-sha256> \
  --workers-file /root/review/staging-workers.json
sudo systemctl start mooncen-crawler-release-publisher.service
```

The HTTPS server exposes only `/state/desired-state.json` and immutable
`/artifacts/<sha256>.tar.gz`. The publisher owns only `public/state/`; it
cannot replace or delete root-owned artifacts. After the canary has an exact
healthy `ready` report, use generation-fenced transitions and inspect status:

```bash
sudo .venv/bin/python -X utf8 -m tools.manage_crawler_release \
  --env-file /etc/mooncen/crawler-release-admin.env \
  status --environment staging
sudo .venv/bin/python -X utf8 -m tools.manage_crawler_release \
  --env-file /etc/mooncen/crawler-release-admin.env \
  advance-rollout --environment staging --rollout-id "$rollout_id" \
  --expected-generation 1 --next-generation 2 --phase rolling \
  --target-worker worker-01
sudo systemctl start mooncen-crawler-release-publisher.service
```

After enrollment:

1. Publish a signed artifact and create one canary rollout/desired-state row
   bound to the exact agent UUID and worker key.
2. Run release-agent `check`, `dry-run`, and reviewed `apply` modes.
3. Confirm the reporter row and worker heartbeat match that agent and release.
4. Start the pull worker and enqueue one bounded task.
5. Confirm one current attempt, immutable observations, fenced snapshots, and
   a successful held finalizer result. Test retry, DLQ, and lease loss.

The systemd worker/reporter ExecStartPre performs the stricter local-host and
desired-state readiness checks. For a bounded central canary, manually start
the publisher one-shot and finalizer, then stop the finalizer afterward:

```bash
sudo systemctl start mooncen-crawler-release-publisher.service
sudo systemctl start mooncen-crawler-control-finalizer.service

# Use a recent, non-daily UTC slot and the exact reviewed release digest.
provider=HOMEPLUS
slot="$(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)"
artifact_sha256=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa

# Validation is local-only and is the default; inspect this JSON first.
sudo systemd-run --wait --pipe --collect \
  --unit=mooncen-crawler-canary-review \
  --uid=mooncen-crawler-control --gid=mooncen-crawler-control \
  --working-directory=/opt/mooncen \
  --property=EnvironmentFile=/etc/mooncen/crawler-control-scheduler.env \
  /opt/mooncen/.venv/bin/python -X utf8 \
  -m tools.enqueue_crawler_canary \
  --provider "$provider" --slot "$slot"

# Enqueue exactly one reviewed provider after confirming provider and artifact.
sudo systemd-run --wait --pipe --collect \
  --unit=mooncen-crawler-canary-enqueue \
  --uid=mooncen-crawler-control --gid=mooncen-crawler-control \
  --working-directory=/opt/mooncen \
  --property=EnvironmentFile=/etc/mooncen/crawler-control-scheduler.env \
  /opt/mooncen/.venv/bin/python -X utf8 \
  -m tools.enqueue_crawler_canary \
  --provider "$provider" --slot "$slot" --max-retries 2 --enqueue \
  --confirm-provider "$provider" \
  --confirm-artifact-sha256 "$artifact_sha256"

sudo systemctl stop mooncen-crawler-control-finalizer.service
```

The tool rejects providers outside the scheduler's expanded reviewed task set,
aliases without an exact aggregate execution owner, stale/non-fractional
slots, release digest mismatches, and any already active job for the same
provider or its aggregate owner. It uses the same advisory lock, unique
active-provider key, output-provider allowlist, release identity, and
scheduler PostgreSQL role as the recurring control scheduler.

## Review and approve a held batch

First create a pinned dry-run artifact on `gen1db` as its separate restricted
applier identity. During preparation this validates the target without
authorizing a recurring apply timer; do not reuse a crawler-control, worker, or
production-database login:

```bash
batch_id=00000000-0000-0000-0000-000000000000
sudo install -d -o mooncen-applier -g mooncen-applier -m 0700 \
  /run/mooncen-staging-apply
sudo systemd-run --wait --collect \
  --unit="mooncen-held-review-${batch_id}" \
  --uid=mooncen-applier --gid=mooncen-applier \
  --working-directory=/opt/mooncen \
  --property=EnvironmentFile=/etc/mooncen/applier.env \
  --property=UMask=0077 \
  --property=ReadWritePaths=/run/mooncen-staging-apply \
  /opt/mooncen/.venv/bin/python -I -X utf8 \
  /opt/mooncen/tools/run_pinned_staging_dry_run.py \
  --allow-held-control-batch --batch-id "$batch_id" \
  --result-file "/run/mooncen-staging-apply/dry-run-${batch_id}.json"
```

After human review, root copies the immutable result and runs the distinct
approver credential:

```bash
sudo install -o root -g root -m 0600 \
  "/run/mooncen-staging-apply/dry-run-${batch_id}.json" \
  "/var/lib/mooncen-crawler-control/reviews/dry-run-${batch_id}.json"
cd /opt/mooncen
sudo .venv/bin/python -X utf8 -m tools.approve_crawler_control_batch \
  --env-file /etc/mooncen/crawler-control-approver.env \
  --batch-id "$batch_id" \
  --dry-run-result-file \
  "/var/lib/mooncen-crawler-control/reviews/dry-run-${batch_id}.json"
```

The approval command matches the fingerprint against current fenced evidence
and changes only the held-to-approved fields. The automatic staging-apply
timer performs another pinned dry-run before applying; the review artifact is
not itself an apply command.

## Cutover and rollback design — NOT EXECUTABLE

No cutover command is authorized by this document. In particular, do not stop,
disable, or alter the legacy scheduler or staging applier on `gen1crawler`:
the corresponding control-plane install/enable path on `gen1db` is unavailable.
The production topology therefore remains `"crawlerMode": "legacy"`.

Read-only inventory may record the legacy states and confirm that
`mooncen-crawler-once.service` is not running:

```bash
# gen1crawler
systemctl is-enabled mooncen-crawler.timer
systemctl is-active mooncen-crawler.timer
systemctl is-enabled mooncen-crawler.service
systemctl is-active mooncen-crawler.service
systemctl is-active mooncen-crawler-once.service
systemctl is-enabled mooncen-staging-apply.timer
systemctl is-active mooncen-staging-apply.timer
systemctl list-units --type=service --all 'mooncen-staging-apply@*.service'
```

Separately collect read-only target evidence on `gen1db`. The exact marked database and
restricted observer must pass their preflights, the control metric textfile
must be fresh, there must be no unexpected live control scheduler, and the
target pinned applier must be installed and ready under its own account. Do not
infer any of this from the topology JSON or dashboard. Evidence collection does
not authorize installation or cutover:

```bash
# gen1db -- read-only readiness evidence
hostname
systemctl is-active postgresql.service
systemctl is-enabled mooncen-crawler-control-metrics.timer
systemctl is-active mooncen-crawler-control-metrics.timer
systemctl is-enabled mooncen-staging-apply.timer
systemctl is-active mooncen-staging-apply.timer
systemctl is-enabled mooncen-crawler-control-scheduler.service
systemctl is-active mooncen-crawler-control-scheduler.service
systemctl is-enabled mooncen-crawler-control-finalizer.service
systemctl is-active mooncen-crawler-control-finalizer.service
systemctl is-enabled mooncen-crawler-release-publisher.timer
systemctl is-active mooncen-crawler-release-publisher.timer
systemctl show mooncen-crawler-release-publisher.service -p Result --value
systemctl show mooncen-crawler-control-metrics.service -p Result --value
systemctl show mooncen-staging-apply.service -p Result --value
```

The future cutover implementation must be one fenced, auditable two-host state
machine. It must prove the atomic release and fresh backup attestations, verify
zero live legacy one-shots and applies, preserve exactly one staging applier,
activate the new scheduler with a health acknowledgement, and change the
reviewed topology without exposing an unowned or dual-scheduler interval. A
failed acknowledgement must automatically keep or restore the previously
recorded legacy state. Manual copy-and-paste service mutations are not an
acceptable substitute.

The future rollback implementation must first drain and disable desired worker
state, stop central recurring units, restore a separately reviewed legacy
topology release, and restore only the recorded legacy scheduler/applier state.
It must not remove schema, agent bindings, attempts, reports, or review
evidence. Until that implementation and its failure-injection tests exist,
there is no executable cutover or rollback procedure here.

The monitoring target must likewise remain `alerting: pending` throughout the
current legacy mode. The future atomic cutover must deploy the exact reviewed
`crawlerMode=distributed` release, prove the legacy scheduler/applier stopped,
and make every recurring unit above active and enabled with successful
one-shot results. Only after that acknowledgement may the paired reviewed
monitoring change replace the gen1db label in
`deploy/monitoring/prometheus/prometheus.yml` with `alerting: enabled` and
deploy it to `bot`. Crawler mode is not an exported metric, so this explicit
promotion gate cannot be inferred by the alert expression itself.
