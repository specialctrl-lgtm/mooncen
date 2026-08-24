# MoonCen integrated Ops Console

Docker 불변 이미지 배포의 증적·권한·API·UI 계약은
[`docker-ops-console.md`](docker-ops-console.md)를 참고한다. 기존
`/api/ops/deployments` 실행 경로는 네이티브(레거시)로 구분된다.

The integrated Ops Console is a separate administrator application in
`ops-console/`. It does not add administrator code to `frontend2`, is not
served by the public MoonCen website, and is the only supported Ops Console.
The former Python/HTML console on port 8765 has been retired.

This document describes the first production-safe delivery: shared schema,
role-based APIs, Dashboard, crawler/job inspection and queueing, Data Quality,
Services inventory, deployment inventory, and Audit Log.

## Retired-console feature mapping

| Existing local screen | Integrated menu | Current integration |
| --- | --- | --- |
| Summary / deployment control | Dashboard, Deployments | Immutable development-tree deployment and history are connected |
| Monitoring | Dashboard, Services | Registered `ops_services` plus existing crawler telemetry |
| Operations | Services, Crawlers, Deployments | Crawler provider and reviewed deployment Jobs are connected |
| Job Results | Jobs & Audit | PostgreSQL-backed jobs, logs, SSE, audit |
| AI Work | Services | Status agent reports the configured Ollama endpoint |
| 품질 작업대 | Data Quality | Production `service_group` data and `course_quality_score` |
| 문화센터 / 체험 / 교육 | Content with a type filter | List/detail/source evidence is live; mutation is not exposed |
| Address Fix | Data Quality | Read-only candidate list is live; update workflow is not exposed |
| 복구 및 감사 | Jobs & Audit, Deployments | Deployment execution is audited; rollback and restore are not exposed |

Functions that are not marked as connected in this table are intentionally not
exposed as legacy fallbacks. They must be added to the standalone console with
the same authenticated job and audit model.

## Migration authority

MoonCen already has an immutable, checksummed migration ledger in
`DB/setup_db.py` and `mooncen_schema_migrations`. Adding Alembic in parallel
would create two schema authorities and unsafe ordering. The Ops schema is
therefore delivered as:

```text
DB/migrations/20260725_001_ops_console_core.sql
```

The migration creates only new `ops_` tables and indexes. It does not drop or
rewrite user-facing tables. Once applied, never edit it; add another versioned
SQL migration.

Service placement reporting is extended by the additive migration below. It
stores the checked endpoint host separately from the Agent that reported it:

```text
DB/migrations/20260803_001_ops_service_host.sql
```

Apply on a reviewed environment:

```bash
python DB/setup_db.py --mode migrate
```

백업에서 복원한 로컬 개발 DB의 Ops 필수 스키마만 복구할 때:

```powershell
python tools\ensure_ops_console_schema.py
```

`DB/roles.sql` must then be applied by the existing cluster-role provisioning
workflow so the API and worker receive their narrow Ops privileges. Do not run
the FastAPI service with the migration owner.

## Backend

Start the existing API in development:

```bash
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8001
```

All Ops routes are provided by `backend/routers/ops_v2.py` under `/api/ops`.

The standalone console uses one independent administrator identity. It is not
a MoonCen signup or OAuth account:

```env
MOONCEN_OPS_SINGLE_ACCOUNT_ONLY=true
MOONCEN_OPS_LOGIN_ID=opsadmin
MOONCEN_OPS_PASSWORD_HASH=pbkdf2_sha256$...
```

The password hash uses PBKDF2-HMAC-SHA256 with a random salt and 600,000
iterations. A single fast SHA-256 digest is intentionally rejected. The first
successful login creates a reserved internal `provider=ops` user solely so
jobs and audit logs retain a valid immutable user UUID.

For production deployment, keep the generated values as
`$MoonCenOpsLoginId` and `$MoonCenOpsPasswordHash` in the untracked
`deploy.local.ps1`. The deployment installs them only in
`/etc/mooncen/api.env` and a mode-`0600` deploy-secret store; subsequent
deployments reuse that protected copy when local values are omitted. Missing or
invalid Ops credentials stop deployment before the API environment is replaced,
preventing a successful deploy from turning Ops login into a 503 response.

When an Ops table or Agent is not connected, APIs return `available: false` or
`unknown`; they do not return generated success data.

### Visitor analytics

The Dashboard can read MoonCen zone aggregates from the Cloudflare GraphQL
Analytics API. This is server-side only and does not add a browser analytics
SDK, cookie, or public frontend credential. Configure both values together in
the protected deployment secret store:

```env
OPS_CLOUDFLARE_ANALYTICS_ZONE_ID=32-character-zone-id
OPS_CLOUDFLARE_ANALYTICS_TOKEN=zone-scoped-token
```

Follow Cloudflare's current GraphQL Analytics token guide: grant only the
read-only analytics permission it specifies and resource-scope the token to the
MoonCen zone. It is loaded into the API process, never the frontend or
deployment worker. Missing, partial, rejected, out-of-retention, or malformed
configuration is rendered as `집계 불가`; it must never be converted to numeric
zero.

Cloudflare `visits` count page views that start from a direct link or a referrer
outside `mooncen.kr` or `www.mooncen.kr`; one visit may include multiple page
views. They are not unique people, members, devices, or IP addresses, and
automated traffic may be included. The adaptive dataset is sampled, so both
visits and the secondary eyeball HTTP-request count are displayed as estimates.
The requests metric is not labelled as page views. Daily grouping uses `Asia/Seoul` and
only completed hours. The API returns explicit metric and sampling metadata so
the Ops UI retains those qualifications.

The Dashboard requests the most recent seven days so it remains usable within
Cloudflare Free and Pro retention. The API accepts 1–30 days for operator use,
but first reads the zone's dataset settings and returns `집계 불가` when the
requested history is outside that zone's actual retention instead of filling
missing dates with zero.

Production placement is declared in the tracked, secret-free
`config/production_topology.json`. The current contract places frontend,
backend, and database on DNS host `cloud`; the legacy crawler primary is the
separate DNS host `gen1crawler`. The retired `n100` node is not part of the
reviewed production topology or deployment registry. The
Services and Deployments screens deliberately show three different values:
desired production location, checked endpoint, and reporting Agent. An Agent
hostname must never be treated as the service location.

`cloud` has no crawler runtime. For crawler rows, the configured owner comes
only from the reviewed topology (`gen1crawler`). `ops_agents.hostname` is the
status reporter and `ops_services.service_host` is the endpoint that reporter
checked; neither is executor-host evidence. A crawler run-log timestamp, a
stale/fresh metric, and node_exporter/systemd timer metrics describe history or
the metric source only. They must not populate `observed_runtime_host` or change
the configured owner. That field stays null unless a worker supplies explicit,
authenticated runtime-host evidence.

### Region collection coverage

`GET /api/ops/crawlers/region-coverage` powers the crawler region screen. It
always returns the canonical 269 municipalities, including municipalities with
no configured Provider and no collected rows. The screen separates `experience`
and `education` and reports, for each province and city/county/district:

- configured, active, and historical Provider counts;
- active and historical data counts;
- active and historical branch counts; and
- the exact Provider detail behind each aggregate.

A parent general city can roll up its child districts. Its rollup is not
additive with direct child-district ownership, and both the API payload and UI
label that distinction. `?refresh=true` bypasses the region snapshot cache for
an operator-requested refresh; normal polling keeps the bounded cache.

### Crawler improvement queue

`GET /api/ops/crawlers/improvement-queue` provides a read-only, explainable
triage queue for crawler maintenance. It combines four independent evidence
sources when they are present:

- recent crawler run outcomes;
- active-course observation freshness based on `courses.last_seen_at`; missing observation timestamps stay unknown and are never counted as 48-hour or 7-day stale evidence;
- per-course quality scores; and
- unresolved Ops quality issues.

Missing evidence remains unavailable and its metrics remain `null`; it is not
converted to a zero count or a healthy result. Every priority score is bounded
and accompanied by the individual reason codes and point contributions used to
calculate it. The score orders investigation work only. It is not a release,
data-loss, or crawler-execution decision.

The endpoint accepts a bounded `limit` of 1 through 500 and returns `total` and
`truncated` separately from the visible `items`. The UI requests the current
maximum so its local Provider search covers the complete reviewed production
inventory today; if the inventory later exceeds that bound it shows the
truncation explicitly instead of presenting a partial search as complete.

The corresponding `Crawler Improvements` screen links an operator to the
existing crawler evidence, Data Quality, and Crawler Studio screens with the
selected Provider. It intentionally exposes no run, retry, source-approval, or
deployment mutation. Those actions retain their existing role, audit, reviewed
registry, distributed-runtime, and independent release-approval boundaries.

## Frontend

The secure React Router version used by the standalone console requires Node
22.22 or newer.

```bash
cd ops-console
npm ci
npm run dev
```

Development serves at `http://127.0.0.1:5175/` and proxies `/api` to the
FastAPI server. The production console uses a separate origin such as
`https://ops.mooncen.kr/`; it is not mounted below `https://mooncen.kr/ops`.
The production Ops web server must:

1. restrict the entire Ops origin with the chosen Cloudflare Access or
   private-network policy;
2. serve `ops-console/dist` with SPA fallback to `index.html`;
3. proxy `/api` to the existing FastAPI backend;
4. send `Cache-Control: no-store` for the Ops HTML shell;
5. keep the regular `frontend2` deployment independent.

The console has only the independent `opsadmin` ID/password form. Public
MoonCen password and Google/Naver OAuth accounts are not accepted while
`MOONCEN_OPS_SINGLE_ACCOUNT_ONLY=true`.

The same-origin API proxy issues a host-only HttpOnly login cookie and CSRF
cookie for the Ops origin, so the public MoonCen browser session is neither
required nor shared. Add the Ops hostname to the API trusted-host list; do not
set a parent-domain cookie.

The standalone Nginx boundary and release notes are in
`deploy/ops-console/`. The normal `deploy_ubuntu.ps1` path intentionally
refuses to install the Ops console.

## Crawler worker

`ops_agent/crawler_worker.py` is a restricted PostgreSQL queue worker. It
claims jobs with `FOR UPDATE SKIP LOCKED`, accepts only provider/branch jobs,
validates the provider against the in-repository crawler registry, and builds
an argv list for `run_crawlers.py`. It never accepts a shell command.

On a crawler host, configure a primary Ops queue connection separately from
the child crawler's optional staging DB:

```env
OPS_QUEUE_DB_HOST=
OPS_QUEUE_DB_PORT=5432
OPS_QUEUE_DB_NAME=mooncen
OPS_QUEUE_DB_USER=mooncen_crawler_login
OPS_QUEUE_DB_PASSWORD=
OPS_AGENT_ID=
```

Then use a service manager to run:

```bash
python -m ops_agent.crawler_worker
```

For a queue/command validation check that claims at most one queued job:

```bash
python -m ops_agent.crawler_worker --once
```

The Windows launcher uses production cloud data by default. It opens
loopback-only SSH forwards for the production API
(`127.0.0.1:18001 -> cloud:8001`) and PostgreSQL
(`127.0.0.1:15432 -> cloud:5432`). A local API on `127.0.0.1:8001` reads the
production database while computing deployment readiness from the reviewed
Windows worktree. A restricted local deployment worker registers its own
fresh `deployment_queue` Agent and executes the immutable snapshot selected by
the console. The Vite console remains at `http://127.0.0.1:5175/`.

SSH authentication is non-interactive and the host key must already be
trusted. Required API and queue credentials are read over SSH from the
protected cloud secret store, inherited only by the corresponding child
process, and are not written to the launcher state file or passed in command
arguments. Cloud mode does not start a crawler scheduler, crawler worker,
quality worker, or status Agent on the operator workstation.

```powershell
.\start_ops_console.ps1
```

Use the local database and local Ops control-plane processes only for explicit
isolated development:

```powershell
.\start_ops_console.ps1 -DataSource Local
```

The reviewed production topology assigns crawler execution to `gen1crawler`, so
opening a local console must not also create a second scheduler or consume
crawler/quality Jobs from the local database. For an isolated development
database only, opt in explicitly:

```powershell
.\start_ops_console.ps1 -DataSource Local -EnableLocalCrawlerRuntime
```

That switch starts `ops_agent.crawler_scheduler`, `ops_agent.crawler_worker`, and
`ops_agent.quality_worker`. The scheduler queues due provider Jobs into the same
`ops_jobs`/`ops_crawler_runs` control plane, so automatic and manual runs share
history, audit, cancellation, and the restricted worker command template. It is
development-only and uses `OPS_LOCAL_CRAWLER_PROVIDERS` (falling back to
`CRAWLER_PROVIDERS` and then the reviewed built-in provider set) with a 24-hour
default interval. The launcher also sets `OPS_LOCAL_CRAWLER_RUNTIME_ENABLED`;
when it is false, the API disables Run/Retry instead of leaving unconsumed Jobs
in the queue. Production runs use the reviewed one-shot command on
`gen1crawler`.

The worker intentionally blocks `dry_run`, `review`, region, URL, and global
jobs until their reviewed staging/Agent templates exist.

Crawler exit code `3` means that persistence evidence proves at least one
provider completed while another failed. Ops records this as `partial_success`;
all-failed runs, missing aggregate manifests, maintenance failures, and batch
finalization failures remain `failed`. Production systemd keeps code `3` as a
non-success result so the existing crawler alert remains visible, while the
timer still schedules the next run normally. Per-provider completeness and
close-missing gates are unchanged.

## Deployment worker

`ops_agent/deployment_worker.py` claims only `deployment` Jobs assigned to the
local development Agent. The API and worker independently validate the same
`config/deploy_servers.json` target, target identity, exact base commit, current
development tree hash, and readable SSH key. A temporary Git index captures
tracked changes, deletions, and safe untracked source files without changing
the user's branch or index. The worker builds a fixed argument list for:

```powershell
.\deploy_mooncen.ps1 deploy `
  -Target <reviewed-target> `
  -ExpectedCommit <exact-base-commit> `
  -SourceCommit <ephemeral-snapshot-commit> `
  -ExpectedSourceTree <reviewed-tree> `
  -ExpectedTargetIdentity <sha256>
```

No shell command, server address, key path, secret, full-deploy flag, or crawler
interruption override is accepted from the browser. Deployment output,
heartbeat, cancellation, and final status are written to the existing
`ops_jobs`, `ops_job_logs`, `ops_deployments`, and `ops_audit_logs` tables.

The local launcher starts this worker in both cloud-data and isolated-local
modes. Crawler and quality workers remain opt-in and local-development-only.
For a separately managed development Agent:

```env
OPS_DEPLOY_QUEUE_DB_HOST=
OPS_DEPLOY_QUEUE_DB_NAME=mooncen
OPS_DEPLOY_QUEUE_DB_USER=mooncen_crawler_login
OPS_DEPLOY_QUEUE_DB_PASSWORD=
OPS_AGENT_ID=
```

```bash
python -m ops_agent.deployment_worker
```

The Deployments button can package a dirty development worktree. The typed
confirmation uses the exact source-tree hash, and the worker rejects the job
if HEAD or any included file changes before it creates the immutable commit.
Local-only and secret-bearing paths such as `.env`, `deploy.local.ps1`,
`config/deploy_servers.json`, `ops-console`, virtual environments, logs, and
caches are excluded before Git reads their contents.

`ops_agent/quality_worker.py` separately claims `data_quality_scan` Jobs with
the DB check role. It applies the versioned `ops_quality_v1` required-field,
date, price, location, and duplicate-URL rules using set-based SQL, upserts
active issues, and resolves scanner-owned issues that disappeared on a later
scan:

```env
OPS_QUALITY_QUEUE_DB_HOST=
OPS_QUALITY_QUEUE_DB_NAME=mooncen
OPS_QUALITY_QUEUE_DB_USER=mooncen_check_login
OPS_QUALITY_QUEUE_DB_PASSWORD=
```

```bash
python -m ops_agent.quality_worker
```

## Tests

```bash
python -m pytest -q tests/test_ops_console_v2.py tests/test_ops_deployment_control.py tests/test_backend_security.py
python -m ruff check backend/ops backend/ops_models.py backend/routers/ops_v2.py backend/routers/auth.py ops_agent

cd ops-console
npm ci
npm run lint
npm run test
npm run build
npm audit --audit-level=moderate
```

No test in this set contacts or changes a production server.

## Remaining production integrations

- Content 목록·상세·원본값·품질 근거 조회는 연결됨. 수동값 편집과 필드 잠금은 미구현
- address/coordinate correction mutations and map review
- automatic provider-level bulk-change/deletion blocking gate
- 로컬 status agent와 DB queue worker는 연결됨. 운영용 HTTPS/HMAC Agent
  registration, nonce replay protection, fixed service/log/backup commands는 미구현
- service restart, cache clear, and log streaming
- rollback, DB backup/restore, and migration execution Jobs
- notification delivery outside the console

미구현 변경 작업은 읽기 전용으로 유지하며 성공한 것처럼 표시하지 않습니다.
