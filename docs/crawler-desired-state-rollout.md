# Crawler desired-state release management

This is the minimum fail-closed release layer for centrally managed crawler
workers.  It does not schedule crawl jobs and it does not grant a worker an SSH
key or a direct database deployment credential.

The shipped systemd policy starts in `check` mode.  Do not enable `apply` until
the central publisher, signed artifact build, pull worker drain/health files,
Tailscale ACL, and a manually bootstrapped rollback release have all passed a
canary exercise.

## Components and trust boundaries

- `ops_agent.crawler_release_control` is the shared, pure desired-state parser.
  Unknown fields, duplicate JSON keys, non-canonical SHA-256 values, URL-like
  artifact paths, invalid canary placement, and generation rollback fail closed.
- `ops_agent.crawler_release_agent` accepts origins and policy only from the
  root-owned local environment file.  A central response can contain an
  allowlisted relative artifact path, but never a command or URL.  Every CLI
  mode verifies the fixed `/etc/mooncen/crawler-release-agent.env` is a nonempty
  root-owned regular file with no group/world write permission before using it.
- The agent uses exact HTTPS host allowlists, normal CA verification, TLS 1.2 or
  newer, and rejects redirects and IP literals.  Tailscale ACLs must additionally
  allow only tagged crawler workers to reach the control and artifact services.
- The only process operation is the fixed argv
  `systemctl restart mooncen-crawler-pull-worker.service`.  No desired-state
  field can change the executable, unit, arguments, or environment inherited by
  that command.
- Worker DB secrets remain in the worker-only group. A separate non-secret
  `mooncen-crawler-status` group grants the capability-free root release agent
  traverse/read access only to `0750` runtime status and `0640` health/drain
  evidence; it grants neither worker nor reporter DB credentials.
- Artifact signing uses an OpenSSH `sshsig` and a local public allowed-signers
  file.  No signing key is installed on a worker.  The namespace is fixed to
  `mooncen-crawler-release-v1`; `key_id` must also be in the local allowlist.

## Database-to-document contract

Migration ownership remains outside these modules.  The runtime expects these
authoritative relational tables from
`DB/crawler_control_migrations/20260810_001_crawler_control_plane.sql`:

- `ops_crawler_release_artifacts`: immutable digest, code/config revision,
  relative artifact path, exact size, optional signature/key id, metadata.
- `ops_crawler_release_rollouts`: `rollout_epoch`, target and previous artifact
  digests, status, worker count and strategy.
- `ops_crawler_worker_desired_state`: environment/worker identity, rollout,
  generation, `desired_status`, `cohort`, artifact/code/config identity and
  `not_before`.
- `ops_crawler_release_reports`: append-only worker observations.

The central publisher maps `rollout_epoch` to JSON `generation`, joins artifact
digests to immutable artifact rows, maps `desired_status=disabled` to
`enabled=false` (`active` and `draining` remain enabled), and emits the stored
cohort.  The enabled bit is never accepted as evidence that draining completed;
only the generation-bound local drain document authorizes a switch.  The
publisher must never copy a URL or a command from operator input into the
document.  Runtime schema readiness may call
`assert_expected_database_contract()`; it checks columns only and never executes
DDL.

The JSON shape is demonstrated by
`deploy/ubuntu/templates/crawler-release-desired-state.example.json`.  Rollout
rules are deliberately strict:

- `paused`: no worker changes release.
- `canary`: exactly the named canary workers may target the new version.
- `rolling`: enabled workers may be at baseline or target, but canaries cannot
  be moved back silently.
- `complete`: every enabled worker must target the new version.
- `rollback`: every enabled worker must target the baseline.

Changing bytes without changing `code_version` is an immutable-version conflict.
An older generation than the worker has already observed is a replay and is
rejected.  A document at the same generation may be read idempotently only when
the worker already matches it (or remains paused/disabled); any newly requested
release transition needs a greater generation.  Resuming a paused rollout or
re-enabling a worker for a different release therefore also bumps generation.

## Artifact contract

The artifact is a gzip-compressed tar archive and must include this regular file:

```json
{
  "schema_version": 1,
  "code_version": "2026.08.10.1",
  "config_revision": "crawler-config-20260810"
}
```

The agent downloads to a private staging file, checks declared size and lowercase
64-character SHA-256, then verifies the optional OpenSSH signature according to
local policy.  Extraction walks the gzip tar as a bounded stream, so member count
and declared expanded bytes are enforced without first materializing an
unbounded member list.  PAX/GNU extension records have independent size/count
limits, and sparse metadata is rejected before tarfile can materialize it.  The
extractor rejects absolute paths, `..`, duplicate paths,
symlinks, hardlinks, devices, FIFOs, privileged mode bits, excessive file count,
and excessive expanded size.  Existing release directories are never rewritten.

Every installed release gets a generated `release.env` containing only:

```text
OPS_CRAWLER_CODE_VERSION=<validated code version>
OPS_CRAWLER_ARTIFACT_DIGEST=<validated sha256>
OPS_CRAWLER_CONFIG_REVISION=<validated config revision>
```

The pull-worker unit must source `/opt/mooncen-crawler/current/release.env`.  Do
not define or override these names in the agent policy or a shared secret file.
Release contents are root-owned but readable by the worker after the atomic
switch; artifact archives therefore must contain code/config only and never a
secret.  The agent explicitly applies final modes after extraction so the
service's restrictive `UMask=0077` cannot make a healthy release unreadable.
Before reuse, activation, or rollback it walks the installed tree and rejects
links, special files, entries not owned by the agent user, and any group/world
writable entry.

## Drain, switch, health and recovery

Before changing the symlink, the running pull worker must publish a private,
non-symlink `/run/mooncen-crawler/drain.json` bound to this rollout:

```json
{
  "schema_version": 1,
  "worker_id": "gen1crawler",
  "rollout_id": "00000000-0000-0000-0000-000000000042",
  "generation": 42,
  "drained": true,
  "active_jobs": 0,
  "observed_at": "2026-08-10T01:00:00Z"
}
```

A stale marker, different generation, or any active lease blocks switching.  The
pull worker must stop claiming before it publishes this marker and remain
quiescent until the fixed unit is restarted; the marker is not merely a
point-in-time metric.  The agent then fsyncs a pending-switch journal,
atomically replaces the `current` symlink, fsyncs its directory, and restarts
only the fixed pull-worker unit.  The new worker must publish a fresh
`/run/mooncen-crawler/health.json`:

```json
{
  "schema_version": 1,
  "worker_id": "gen1crawler",
  "healthy": true,
  "code_version": "2026.08.10.1",
  "artifact_digest": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "config_revision": "crawler-config-20260810",
  "observed_at": "2026-08-10T01:01:00Z"
}
```

Even when desired state appears to be an idempotent no-op, the agent reports
`ready` only after revalidating the current symlink, the immutable installed
tree/metadata, and an exact health document no older than the local health
timeout.  Missing integrity or stale health is reported as `drifted`.

Health timeout restores the previous symlink, restarts the previous worker, and
requires matching previous-version health.  A killed agent leaves the fsynced
journal; its next `apply` run conservatively completes rollback unless the new
release was already health-checked and durably recorded.  A failed generation is
not retried until the control plane issues a newer generation.

A preparation failure before any switch is also journaled before terminal local
state is recorded.  Startup completes both the local state and deterministic
report spool entry before clearing that journal.  After a successful rollback,
the pending-switch journal remains until its rollback report is durably spooled.
These orderings make a crash repeat reporting/recovery rather than lose central
failure evidence.

The worker must already have one reviewed healthy release and matching local
state.  Automatic first installation is intentionally forbidden because there
would be no rollback target.

## Reports

Reports are written atomically under
`/var/lib/mooncen-crawler-release-agent/reports/`.  Fields align with the
relational report contract: deterministic UUID `id`, `environment`,
`worker_key`, `rollout_id`,
`desired_generation`, allowed status, artifact/code/config identity, bounded
health/error detail, and `reported_at`.  An authenticated central reporter adds
its known `agent_id` when inserting the append-only row.  It must not delete a
spool file until the insert is durably acknowledged; it inserts the supplied
UUID as the relational primary key and treats a matching conflict as an
idempotent retry.  It must reject a conflicting payload for an existing UUID.

## Future installation and staged enablement

Worker inventory validation is ready, but installation is blocked. The current
`deploy/ubuntu/setup_distributed_crawler_worker.sh` exits `NOT READY` before
any filesystem/systemd/database mutation because `/opt/mooncen` is not an
independent installer trust root. `enroll_distributed_crawler_worker.sh` is
also blocked until worker and reporter credentials can be provisioned as one
atomic pair with active-rotation fencing. See
`docs/distributed-crawler-control-plane.md` for the missing bootstrap and pair
transaction requirements.

The following staged sequence is a design requirement, not an executable
runbook:

1. Install the env example as `/etc/mooncen/crawler-release-agent.env`, mode
   `0600`, and replace the example Tailscale HTTPS names.
2. Install only the public allowed-signers file, root-owned and not group/world
   writable.  Keep `OPS_CRAWLER_REQUIRE_SIGNATURE=true` in production.
3. Let the installer create the exact root/reporter setgid report spool and
   reviewed release directories after both dedicated accounts exist.
4. Install the service and timer, run `systemd-analyze verify`, and invoke the
   service in `check` mode.
5. Change to `dry-run`; verify the selected worker/version/generation and that no
   files, units, or database rows change.
6. Bootstrap and validate the previous release through the local-only signed
   artifact path, then issue one new canary generation. Only after
   drain/health/report and forced rollback tests pass may the explicit
   `--enable-reviewed-canary` path use `apply`.
7. Expand desired state one bounded cohort at a time.  Pause immediately on any
   failed or rolled-back report.  Stable workers remain on baseline until a new
   reviewed generation explicitly moves them.

Focused repository checks:

```text
python -m pytest -q tests/test_crawler_release_control.py tests/test_crawler_release_agent.py
ruff check ops_agent/crawler_release_control.py ops_agent/crawler_release_agent.py
systemd-analyze verify deploy/ubuntu/systemd/mooncen-crawler-release-agent.service deploy/ubuntu/systemd/mooncen-crawler-release-agent.timer
```
