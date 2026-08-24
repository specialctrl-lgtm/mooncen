# Distributed crawler control-plane observability

The metrics collector runs on the staging database/control host once per
minute. It opens a read-only transaction with a dedicated observer LOGIN and
atomically replaces a node_exporter textfile. It does not open another network
listener.

The exported series cover ready/running/dead-letter jobs, oldest-ready age,
expired leases, scheduled/exhausted retries, latest batch status and age,
fresh/stale desired worker heartbeats, and the latest release-report status for
each desired worker. Labels are limited to the configured environment and
fixed state/status enums. Worker keys, providers, job IDs, error text, health
JSON, job parameters, and result payloads are never exported.

Worker freshness uses the same fixed two-minute `ops_agents.last_seen_at`
boundary that crawler lease ownership uses, so the dashboard cannot report a
worker as active after it has become ineligible to claim work.

Provision only through `setup_distributed_crawler_control.sh` with a protected
copy of `crawler-control-metrics.env.example`. The shared Python SCRAM
provisioner creates a marked observer LOGIN whose only membership is
`mooncen_crawler_observer`; `roles.sql` converges the bounded column SELECT
grants. Do not run the deprecated standalone observer SQL path.

The central setup creates the locked `mooncen-crawler-observer` system account,
installs the environment mode `0640`, verifies the real credential and systemd
units, and starts the collector once. It gives that account write access only
to `/var/lib/mooncen-crawler-observer`. Do **not** make node_exporter's collector
directory group/world writable. Instead, setup creates one root-owned symlink named
`mooncen_crawler_control.prom` in that directory, pointing to the same-named
file in the observer state directory. This lets node_exporter follow the one
reviewed file without letting the observer replace other textfile metrics.
Install both systemd units, run `systemd-analyze verify`, start the service once
to create and validate the target, then install/verify the root-owned symlink
before enabling the timer. A failed collection leaves the previous
textfile intact; alert on service failures and on an old
`mooncen_crawler_control_generated_timestamp_seconds` value.

The default install leaves the metrics timer disabled with the other recurring
control units. `--enable-control-plane` enables it only after the reviewed
canary/cutover guards pass. The installer never changes node_exporter's network
listener configuration.

The provisioned gen1db control-health rule also consumes the existing bounded
`mooncen_systemd_unit_active`, `mooncen_systemd_unit_enabled`, and
`mooncen_systemd_unit_result_failed` series. It requires active and enabled
scheduler/finalizer services plus publisher, observer, and pinned-applier
timers, and successful publisher/observer/applier one-shot results. The rule is
inert while the Prometheus target has `alerting="pending"`, which is mandatory
while the reviewed production topology remains in `crawlerMode=legacy`.
Because crawler mode is not an exported metric, promote that target label to
`alerting="enabled"` only in the reviewed monitoring deployment paired with a
future authenticated two-host cutover and the exact
`crawlerMode=distributed` release.
