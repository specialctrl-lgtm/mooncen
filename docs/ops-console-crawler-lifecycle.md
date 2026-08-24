# Ops Console crawler lifecycle

## Goal

Ops Console is the operator workspace for crawler development, validation, and
release approval. The crawler control host, colocated with the shared staging
database, is the authority for signed releases, worker desired state, collection
evidence, update outcomes, quality signals, and statistics.

The browser and the public API never receive an artifact signing key, a worker
SSH key, a crawler database password, or a primary database write credential.

## Responsibility boundary

| Plane | Responsibilities | Must not do |
| --- | --- | --- |
| Ops Console | Draft/review metadata, start parser probes and bounded dry-runs, inspect diffs and test evidence, request reviewed release transitions when their gates are available, display analytics | Sign artifacts, execute root commands, write worker desired state directly |
| Ops API | Authenticate the operator, validate bounded requests, append an audit record, enqueue a release action, serve redacted read models | Run `ssh`, invoke a shell, read signing secrets, mutate release tables with the normal API login |
| Builder/signer | Check out an exact reviewed source tree, run tests and static checks, produce a deterministic archive and manifest, sign its digest, publish build evidence | Select rollout targets or bypass approval |
| Crawler control host | Own the durable action queue, artifact registry, rollout generation, worker bindings, scheduler, finalizer, analytics, and desired-state publication | Hand primary DB credentials to workers, accept unsigned releases |
| Worker | Pull only its fenced jobs, verify signed desired state/artifacts, drain, switch atomically, report health/result evidence | Choose its own version, claim another worker's lease, write primary directly |
| Applier | Validate and promote an explicitly approved staging batch to primary | Promote an unapproved or incomplete batch |

## Lifecycle

1. **Develop** — an operator selects a provider, records the source URL and
   parser configuration, and works in an isolated draft. Existing released
   source is read-only in the browser.
2. **Probe** — Ops Console submits an allowlisted parser probe. The result keeps
   sanitized HTML/selector evidence and never changes collection data.
3. **Validate** — the draft runs against bounded fixtures and a staging-only
   dry-run. Unit tests, provider contract checks, output schema validation,
   duplicate checks, and quality deltas become immutable build evidence.
4. **Build and sign** — the builder uses an exact source-tree digest. A separate
   signer produces a detached OpenSSH signature. Ops Console sees only the
   digest, version, signer key id, tests, and manifest.
5. **Register** — a release-admin agent on the control host verifies the digest,
   signature, immutable artifact path, and monotonic version, then registers the
   artifact.
6. **Canary** — the operator confirms an exact rollout id, generation, artifact,
   baseline, and worker list. One reviewed canary drains, verifies, switches,
   restarts, and reports fresh matching health.
7. **Roll out** — each generation advances only after current target workers
   report the exact artifact/config identity and healthy status. Pause and
   rollback remain available at every non-terminal stage.
8. **Collect and analyze** — the control host correlates release generation,
   worker attempt, provider, batch, collected/new/updated/failed counts, staging
   validation, quality issues, duration, retries, and lease loss.
9. **Approve data** — collection success does not imply primary promotion. The
   finalizer seals evidence as held; a separate approver reviews the pinned
   dry-run before the applier can update primary.

## Console information architecture

### Crawler Studio

- provider registry and released source identity;
- draft and review status;
- parser probe and bounded dry-run;
- fixture/contract/unit-test evidence;
- output and quality delta preview;
- build request and immutable build result.

Current implementation stores only allowlisted provider/path source drafts,
append-only UTF-8/SHA-256 revisions, and non-approval source-review decisions.
One environment/path has one authoritative draft even when several providers
share the same physical source file. Approved or archived history is reopened
only by appending a new revision; an in-review draft must leave review first.
Every review request is fenced by both revision number and the exact stored
source digest. Independent source approval is **NOT READY** because the normal
API database credential cannot prove an independent administrator receipt; the
API and database both reject `approve` until that evidence path exists. Source
approval, once implemented, will still not be an independent release approval.
The remaining submit/change-request/archive rows are append-only collaboration
metadata from the authenticated API, not authorization evidence for execution,
build, signing, release, or primary promotion.
Because the control database is separate from the primary user database, its
generic audit row leaves the primary-only `user_id` foreign key null. The
authenticated UUID is retained in the Studio append-only `created_by` or
`reviewed_by` field and in sanitized audit JSON, avoiding a cross-database user
replication dependency.
The central fixture runner, source execution sandbox, builder evidence handoff,
and signer handoff are explicitly unavailable. Existing development-only
probe/dry-run endpoints remain visible, but a distributed `409`/`503` is never
bypassed by falling back to local execution.

### Releases

- registered signed artifacts and signer identity;
- active rollout, generation, baseline, phase, and blockers;
- desired versus observed version for every worker;
- canary approval, pause, rolling expansion, and rollback requests;
- append-only operator and release-agent audit trail.

### Operations and analytics

- queue depth, oldest ready age, active leases, retries, dead letters;
- collection totals, new/updated/failed rows, provider success rate and latency;
- staging batch status and promotion eligibility;
- field completeness, invalid/duplicate/missing values, quality trends;
- release-correlated regressions and before/after comparisons.

The Analytics read model and its bounded batch drill-down use only the marked,
environment-bound crawler-control database. Recent batches expose immutable
attempt/observation evidence for duration, retries, lease loss, collection
counts, and staging validation. Missing relations, columns, or grants make the
affected component `available=false`; they are never rendered as zero.

New production/staging attempts record an immutable `rollout_id` and positive
`release_generation` in the same claim transaction. Database triggers bind that
pair to the enrolled worker's current desired state, the job's required
artifact/code/config identity, and the exact append-only generation roster.
Analytics attributes only evidence that matches all of those fences. Attempts
from before the roster/fence migration remain an explicit `legacy_unattributed`
NULL pair; timestamps and artifact similarity are never used to invent a
generation. Collection results are attributed to the job's latest attempt, and
staging validation totals are attributed only when every task's latest attempt
shares one exact generation. Quality tables are shared staging snapshots;
forced row-level policies hide them from production/development crawler API
logins, and they do not carry an immutable attempt or batch generation foreign
key. Overall staging quality therefore remains visible while generation-level
quality comparison stays
`generation_quality_attribution_unavailable`. The console's batch detail uses
the same central endpoint and never links to legacy primary-DB operation
details.

## API contract

Read APIs may return `available=false` when the central control connection or
schema is not configured. They must not silently fall back to synthesized zero
values.

Mutation APIs append a request and an audit record. A privileged release-admin
agent claims the request using a lease and executes a fixed action; the API does
not execute the release operation inline.

Minimum actions are:

- `build` (builder queue, exact source tree and test profile);
- `register_artifact` (verified builder output only);
- `create_canary`;
- `advance_rollout`;
- `pause_rollout`;
- `rollback_rollout`;
- `complete_rollback`.

Every mutation carries an idempotency key, environment, expected generation,
bounded confirmation phrase, authenticated requester, and sanitized reason.

### Privileged action consumer

Five rollout mutations (`create_canary`, `advance_rollout`, `pause_rollout`,
`rollback_rollout`, and `complete_rollback`) now require a separately
credentialed, immutable operator-approval receipt. The approver first previews
the server-canonical request with `tools/approve_crawler_release_action.py`,
then supplies the exact typed confirmation in a separate invocation. The DB
stamps the approver login, environment, request digest, and bounded expiry; the
consumer claims only an exact, unexpired receipt. Ops Console exposes these
five capabilities only while the reviewed catalog contract and the
long-running consumer heartbeat are both current. Read models, analytics, and
Studio draft storage do not weaken this gate.

Live installation nevertheless remains **NOT READY**: the direct central
installer intentionally exits before its first filesystem or database write
until a release-bound, OpenSSH-signed backup receipt can be consumed by the
root-trust gate. Therefore this source revision implements the approval path
but does not authorize or perform a live control-host deployment.

`mooncen-crawler-release-action-worker.service` runs only on the crawler
control host. It uses the root-owned `crawler-release-admin.env`, claims one
request with `FOR UPDATE SKIP LOCKED`, and fences renew/retry/completion by the
request id, attempt number, lease owner, and random lease token. Expired leases
are requeued up to five mutation attempts. The final ambiguous attempt receives
one read-only reconciliation lease; if exact committed state still cannot be
proved, the request becomes `reconciliation_required` instead of being falsely
reported as failed. A retry after the release transaction committed reconciles
the exact rollout id, generation, phase, worker snapshot, and artifact evidence
before reporting success.

The consumer dispatches only the in-process `create_rollout` and
`advance_rollout` release-admin functions. It has no shell, SSH, arbitrary
module, command, or caller-selected path interface. The artifact root is fixed
at `/var/lib/mooncen-crawler-control/public`. A canary request names the entire
reviewed rollout fleet, including disabled workers, rather than only the canary
subset. Cohort, enablement, order, and kernel hostname come from the signed
production topology; agent UUID, status, capabilities, and the exact paired
worker/reporter bindings come from the control DB. At least one reviewed
canary must be enabled. The full-fleet input prevents the caller from hiding
workers by submitting only a hand-picked desired-state subset; the separate
first-rollout baseline dependency described below still remains fail-closed.

`create_canary` treats `expected_generation` as the new next monotonic rollout
generation. Other rollout actions treat it as the current generation and write
exactly `expected_generation + 1`. The consumer recomputes the confirmation
phrase, including target/baseline digest prefixes and the order-independent
worker-set digest, before execution.

Every agent health cycle writes a new append-only report id; retransmitting the
same spool file remains idempotent. Forward rollout, rollout completion, and
rollback completion require the exact release identity, a boolean healthy
report, and a healthy non-maintenance agent heartbeat within the bounded
30–900 second freshness window (360 seconds by default, covering the two
independent two-minute agent/reporter timers and their jitter). Starting an emergency rollback intentionally
does not require fresh health from the release being withdrawn.

Rollback is intentionally two actions. `rollback_rollout` advances to
`rolling_back` and publishes baseline desired state. After every enabled worker
reports that exact baseline generation healthy, `complete_rollback` invokes the
existing `rolled_back` report gate and advances to the terminal `rolled_back`
state. The second action requires `COMPLETE_ROLLBACK <rollout-id>
<current-generation>`; the rollback button is not implicitly reused.

`build` and `register_artifact` have the additional
`immutable_builder_evidence_handoff_not_implemented` gate. They remain closed
until an immutable builder/signer evidence handoff exists; the consumer never
accepts archive, signature, key, or filesystem paths from a request.

The first distributed rollout has a separate bootstrap dependency. Canary
creation now requires a single existing desired baseline identity and fresh,
healthy matching reports from every enabled worker. Those rows do not yet
exist before the first rollout creates desired state, so a future immutable
bootstrap-evidence import must seed and verify the baseline. Until then the
first Canary remains unavailable instead of guessing from rollout metadata or
a worker-local file.

## Rollout gate

Production enablement remains blocked until all of the following are true:

- central and worker DB roles pass the exact RLS/ACL preflight;
- tailnet-only HTTPS, certificate hostname, and ACL policy are reviewed;
- artifact signer and offline recovery procedure are verified;
- at least one immutable rollback baseline is installed;
- canary worker identity and resource limits are approved;
- collection, quality, retry, lease-loss, drain, rollback, and promotion tests
  pass with audit evidence;
- legacy crawler and distributed scheduler are never active concurrently.
