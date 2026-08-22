# MoonCen Monitoring Stack

Central host: `bot`

Services:
- Grafana: `http://bot:3000`
- Uptime Kuma: `http://bot:3001`
- Prometheus: `http://bot:9090`
- Node Exporter on `bot`: `http://bot:9100/metrics`

Install from Windows:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy\monitoring\install_bot.ps1 -HostName bot -User ubuntu
```

If Tailscale SSH requires an interactive check, use the normal SSH endpoint and key:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy\monitoring\install_bot.ps1 -HostName 193.122.112.7 -User ubuntu -IdentityFile C:\project\BOT\oracle_bot.key
```

Install locally on `bot`:

```bash
bash deploy/monitoring/install_bot.sh
```

The stack stores data under `/opt/mooncen-monitoring` and creates a private `.env` there for the Grafana admin password. It detects exactly one Tailscale IPv4 address and binds Grafana, Prometheus, and optional Uptime Kuma only to that address. Installation fails closed if Tailscale is unavailable; for another private interface, set `MONITOR_BIND_ADDR` or pass `-BindAddress` explicitly. Wildcard and public addresses are rejected.

Notes:
- `bot` is a small host, so the default compose profile starts Prometheus and Grafana only.
- Uptime Kuma is optional: `docker compose --profile uptime up -d uptime-kuma`.
- Existing installations whose `.env` contains `MONITOR_BIND_ADDR=0.0.0.0` must replace it with the host's private local IPv4 address before restart.
- `prometheus-node-exporter` should run as an OS service on Linux nodes.
- Grafana includes a provisioned `MoonCen Node Summary` dashboard.
- Grafana alerting sends Telegram notifications for targets labeled `alerting: enabled`.
- Compose images are immutable version-and-digest references. The reviewed pins
  are Prometheus `v3.13.0`, Grafana OSS `13.1.0`, and optional Uptime Kuma
  `2.4.0-rootless`; update each tag and multi-architecture digest in the same review.
- Before enabling Uptime Kuma 2.x on an existing 1.x data volume, make a volume backup and follow the [upstream v1-to-v2 migration guide](https://github.com/louislam/uptime-kuma/wiki/Migration-From-v1-To-v2). The optional profile is not started by the base install.

Telegram alerting:

The Telegram token and chat ID are stored only in `/opt/mooncen-monitoring/.env` on `bot`.

```env
GRAFANA_TELEGRAM_BOT_TOKEN=...
GRAFANA_TELEGRAM_CHAT_ID=...
```

Provisioned alert rules:
- target down for 2 minutes
- CPU above 90% for 10 minutes
- memory above 90% for 10 minutes
- disk usage above 90% for 10 minutes
- MoonCen app textfile metrics stale for 3 minutes
- cloud deployment provenance missing
- cloud no longer reports primary role or writable PostgreSQL
- gen1crawler crawler cycle evidence invalid, failed, empty, partial, provider-failed, or stale
- gen1crawler isolated-staging collection or pinned promotion scheduler inactive/disabled
- gen1crawler latest pinned staging promotion failed
- gen1db staging PostgreSQL/control collector, central systemd units, leases, queue, workers, batches, or releases unhealthy (pending until distributed cutover)
- cloud API/frontend systemd unit inactive

Exporter install:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy\monitoring\install_exporters.ps1
```

Linux local install on a target:

```bash
bash deploy/monitoring/install_linux_exporter.sh
```

The Linux exporter binds to the node's Tailscale IPv4 address by default. It
fails closed when Tailscale is unavailable instead of listening on
`0.0.0.0`. For a deliberately selected private interface, pass one explicit
local address, for example `LISTEN_ADDRESS=10.20.30.40:9100`; never use a
wildcard address. Prometheus must reach that interface through an approved
private network.

The Linux exporter installer also enables the Node Exporter textfile collector
and installs `mooncen-node-metrics.timer`. That timer writes
`/var/lib/node_exporter/textfile_collector/mooncen.prom` every minute with:

- `mooncen_deploy_timestamp_seconds`
- `mooncen_node_role`
- `mooncen_postgres_in_recovery`
- `mooncen_cloud_db_ready`
- `mooncen_crawler_cycle_state_valid`
- `mooncen_crawler_cycle_outcome{outcome="success|partial_success|failed|zero_provider|running|unknown"}`
- `mooncen_crawler_cycle_last_completion_timestamp_seconds`
- `mooncen_crawler_last_success_timestamp_seconds`
- `mooncen_crawler_cycle_providers_requested`
- `mooncen_crawler_cycle_providers_completed`
- `mooncen_crawler_cycle_providers_failed`
- `mooncen_crawler_cycle_partial_success`
- `mooncen_crawler_cycle_zero_provider`
- `mooncen_crawler_cycle_skipped_lock_contention`
- `mooncen_systemd_unit_active`
- `mooncen_systemd_unit_enabled`
- `mooncen_systemd_unit_result_failed`

The distributed control observer on `gen1db` writes a separate bounded
`mooncen_crawler_control.prom` textfile. Its reviewed metric contract is:

- `mooncen_crawler_control_queue_jobs{state="ready|running|dead_lettered"}`
- `mooncen_crawler_control_oldest_ready_age_seconds`
- `mooncen_crawler_control_expired_leases`
- `mooncen_crawler_control_retry_jobs{state="scheduled|exhausted"}`
- `mooncen_crawler_control_latest_batch_outcome{status="..."}`
- `mooncen_crawler_control_latest_batch_present`
- `mooncen_crawler_control_latest_batch_age_seconds`
- `mooncen_crawler_control_workers{heartbeat="fresh|stale"}`
- `mooncen_crawler_control_release_reports{status="..."}`
- `mooncen_crawler_control_collector_success`
- `mooncen_crawler_control_generated_timestamp_seconds`

These metrics come from the dedicated column-limited observer login. They do
not expose provider names, worker keys, job IDs, payloads, errors, or secrets.
The `gen1db` alert and dashboard use only this existing contract plus
`mooncen_postgres_in_recovery` from the host collector. The latter proves that
the local PostgreSQL instance is observable and not in recovery; successful,
fresh control metrics provide the stronger check that the marked shared
staging/control database and its bounded aggregate queries are available.

The collector emits the `mooncen_crawler_*` family and the
`mooncen-crawler*`/`mooncen-staging-apply*` systemd unit series only when the
reviewed node role is `crawler`. A `primary` cloud node continues to emit its
deployment, PostgreSQL, Web/API, and common systemd metrics, but emits no
crawler series at all. This prevents stale local files or retired units on
cloud from becoming crawler execution evidence.

With reviewed node role `crawler-control`, the same bounded systemd metric
family includes only the central scheduler/finalizer, release publisher,
control observer, and pinned staging-applier units. It does not emit legacy
crawler-cycle series. This role must be set only on `gen1db` after its
authenticated host identity is confirmed.

The still-reviewed legacy crawler runs on `gen1crawler`. Its existing
dashboard continues to show the isolated-staging one-shot and pinned promotion
contract until an authenticated inventory and reviewed cutover establish the
new placement. The distributed target placement is different: `gen1db` owns
the shared staging database, crawler-control services, and pinned staging
applier, while `gen1crawler` and later nodes are worker-only. No dashboard or
repository change is proof that this remote cutover occurred.

A legacy cycle is unhealthy when its bounded state file is invalid or missing,
its terminal outcome is failed/partial/zero-provider, any provider failed, or
the last terminal cycle is older than 36 hours. A confirmed lock-contention
exit is displayed on the dashboard but is not by itself a data-health alert;
cycle evidence and freshness still fail closed if the active run never
produces a valid terminal result.

`mooncen_crawler_cycle_outcome` has one fixed `outcome` label with six bounded
values. Provider names, batch IDs, paths, and exception text are deliberately
excluded from metrics to keep Prometheus cardinality bounded. Detailed failure
evidence remains in the cycle report and service journal.

Prometheus assigns crawler runtime evidence only to the static
`node="gen1crawler"` target. Cloud remains the Web/API/writable-PostgreSQL
target. Old crawler series labeled `node="cloud"` are intentionally excluded
from crawler dashboards and alerts and must never be treated as current
execution evidence.

Prometheus assigns the shared staging/control evidence only to the static
`node="gen1db"` target. It is labeled `alerting: pending` initially so applying
the bot configuration before installing and verifying the private exporter
does not create a false target-down or control-health incident. It must remain
pending while `config/production_topology.json` says `"crawlerMode": "legacy"`.
The crawler-control alert is gated on
`up{node="gen1db",alerting="enabled"} == 1`, because crawler mode is not an
exported metric and therefore cannot be joined safely inside PromQL.

Promote the target only in the monitoring change paired with the exact release
that changes `crawlerMode` to `distributed`: first authenticate both hosts and
prove the legacy scheduler/applier are stopped on `gen1crawler`; then verify
from `bot` that the private exporter and observer textfile are fresh and that
these five recurring units on `gen1db` are both active and enabled:
`mooncen-crawler-control-scheduler.service`,
`mooncen-crawler-control-finalizer.service`,
`mooncen-crawler-release-publisher.timer`,
`mooncen-crawler-control-metrics.timer`, and
`mooncen-staging-apply.timer`. Also verify the publisher, metrics, and staging
apply one-shot result metrics are zero. Only then change the single gen1db
label in `deploy/monitoring/prometheus/prometheus.yml` from
`alerting: pending` to `alerting: enabled`, deploy that reviewed monitoring
configuration to `bot`, and confirm Prometheus exposes
`up{node="gen1db",alerting="enabled"} == 1`. Never relabel `gen1db` as the
production database; that database remains on `cloud`.

Windows local install on `victus` from an elevated PowerShell prompt:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy\monitoring\install_windows_exporter.ps1
```

The Windows installer defaults to `windows_exporter 0.31.6`, verifies the MSI
against SHA256
`767324dc7ea8e6b8b99f610e2fb9f36d029c8f673a94b3d9f5f2c3c579be0b6d`,
and pins the Authenticode signer certificate thumbprint
`A5A9E97BFAEB629D755EA507FED51073BA605D78`. It does not query a mutable
`latest` endpoint. By default it binds only to the local Tailscale IPv4 address
and limits Windows Firewall sources to Tailscale's IPv4 range
`100.64.0.0/10`. To use another private network, pass both
`-ListenAddress` and an explicit `-AllowedRemoteAddress` CIDR allowlist.

The default MSI checksum comes from the upstream `v0.31.6` release
`sha256sums.txt`. Upstream documents both Authenticode signing and the
`REMOTE_ADDR` firewall allowlist at
<https://github.com/prometheus-community/windows_exporter>.

Current immutable image references (Docker Hub multi-architecture manifests):

- `prom/prometheus:v3.13.0@sha256:c6b27ea434f8389bfe233fbc7be381cf50587c286e871bc842008f5a1b1908a7`
- `grafana/grafana:13.1.0@sha256:121a7a9ece6dc10b969f1f96eed64b4f07dfac0d0b8abc070f7cb83bbde86f63`
- `louislam/uptime-kuma:2.4.0-rootless@sha256:a23b9d0029e6f1bc4a0fea0f3ee306d51f43216cd9f8115f8d84d146e9411e4c`

Targets:
- Linux nodes: `bot`, `cloud`, `gen1crawler`, `gen1db`, `wtr-linux` expose Node Exporter on `:9100`.
- `gen1db` is the shared staging/crawler-control target, not the production database target.
- `wtr-linux` uses collector role `crawler-worker`, but its target remains
  `alerting=pending` while worker setup/enrollment are `NOT READY`. That role
  exports only pull-worker, release-agent, and release-reporter service/timer
  evidence; it does not emit legacy scheduler or staging-applier evidence.
- Windows nodes: `victus` exposes Windows Exporter on `:9182`.
- Telegram alerts: configure Uptime Kuma notification or add Prometheus Alertmanager after base metrics are stable.
