# Gen1 legacy crawler installation and transition

The reviewed placement must distinguish the currently active legacy runtime
from the distributed target. `cloud` owns Web/API and the production
PostgreSQL database. `gen1crawler` is the current runtime owner until an
authenticated inventory and an explicit cutover prove otherwise. The selected
distributed target makes `gen1db` the shared writable staging database and
crawler-control host, including the pinned staging applier; `gen1crawler` and
additional nodes then become worker-only.

The commands in this file describe the existing single-host legacy bootstrap
contract. They do not install the distributed target and must not be used to
create a second staging database on `gen1crawler` during the `gen1db` rollout.
Use `docs/distributed-crawler-control-plane.md` for that rollout. Repository
topology, monitoring, and documentation changes alone are not proof that any
remote service moved.

`gen1crawler` is present in the deployment transport registry with
`role=crawler`, `deployProfile=crawler-only`, and `active=false`. It is not a
primary/standby full-stack target: the generic `deploy`, `full-deploy`, and
`*-all` paths must never install Web/API/DB services there. Its registry entry
supports owner-specific status and activation against gen1crawler only.
Installing it does not change Cloudflare, DNS, or the current cloud Web/API/DB
node. A normal cloud deployment sets crawler staging off, does not install or
operate crawler/staging systemd units, and leaves any obsolete cloud unit as
separately reported topology drift.

## Crawler node

Stage the crawler DB client environment and CA certificate issued for access to
the production database on cloud. The
source `crawler.env` is required and must contain a non-empty Kakao Maps REST
API key; it is used only as a source for that key and worker count. The
installer generates a new local staging password. The reusable crawler setup
and activation both require the host
timezone to be `Asia/Seoul` so the nightly calendar has an unambiguous meaning:

```bash
sudo timedatectl set-timezone Asia/Seoul
```

The following setup command is for a new or already quiescent bootstrap only;
it is not an approved update command for the active production owner:

```bash
sudo bash /opt/mooncen/deploy/ubuntu/setup_split_crawler.sh \
  --db-client-env /home/sgm/cloud-db-client-crawler.env \
  --db-ca /home/sgm/cloud-db-root-ca.crt \
  --source-crawler-env /home/sgm/crawler-source.env \
  --site-url https://mooncen.kr \
  --deploy-commit "$(git rev-parse HEAD)" \
  --deploy-archive-sha256 SHA256_FROM_RELEASE_META
```

`gen1crawler` is already the current crawler owner; there is no cloud-to-gen1
cutover. Setup and activation never open SSH to cloud or manage cloud systemd
state. They do use the restricted applier/check database roles to validate and
update production PostgreSQL on the reviewed DNS host `cloud`.

There is currently no supported live-owner crawler release uploader. Confirm
the fail-closed status without contacting the server:

```powershell
.\deploy_mooncen.ps1 crawler-update -Target gen1crawler
```

The full-stack deployment path also rejects `gen1crawler`. Do not copy a new
tree over the active `/opt/mooncen` and run setup as an update: the script
disables timers before dependency/environment/unit replacement and cannot
transactionally restore the old release. The setup command above is restricted
to an initial or otherwise quiescent bootstrap whose release provenance was
established by a separate reviewed process. The setup script is pinned to the
`gen1crawler` hostname and crawler node role, takes the shared split-runtime
lock, strictly disables only gen1crawler automation, and refuses to replace the
runtime while a one-shot or pinned apply/dry-run unit is active.

Setup requires the DB client file to name `DB_HOST=cloud`; an arbitrary
database endpoint is rejected. A setup failure leaves gen1crawler timers
disabled and requires manual recovery. It has no effect on cloud Web/API/DB
deployment.

This legacy installer keeps a separate local `mooncen_staging` cluster on port
`55432` on `gen1crawler`. That is transition evidence, not the distributed
target: the reviewed target endpoint is the marked staging/control database on
DNS host `gen1db`. Never run this legacy bootstrap to create a competing
staging authority after `gen1db` has been selected.
It verifies the production DB applier login and runs a no-batch dry-run, but
leaves `mooncen-crawler.timer` and `mooncen-staging-apply.timer` disabled.
Run one controlled collection while both recurring timers remain disabled:

```bash
sudo systemctl start mooncen-crawler-once.service
sudo systemctl show mooncen-crawler-once.service -p Result -p ExecMainStatus
jq -r '.status, .run_id' /opt/mooncen/logs/crawler_progress.json
```

For an approved quiescent bootstrap only, proceed when the service result and
progress are successful. Review the reported batch and staging quality, then
activate that exact `run_id` from Windows:

```powershell
.\deploy_mooncen.ps1 crawler-activate `
  -Target gen1crawler `
  -BatchId REVIEWED_CRAWL_BATCH_ID
```

The activation command authenticates only `gen1crawler`, requires the reviewed
crawler-only registry target and node role, and shares the same runtime lock as
setup. It runs the pinned dry-run and accepts it only when the JSON names the
same complete `COLLECTED` batch with no validation failures or
`close_blocked` providers and a consistent `close_missing` scope. The real
apply is pinned to the same staging fingerprint and must produce a fresh full
`SUCCESS`.

Only after the exact apply passes does activation clean old persistent timer
state and enable the nightly crawler timer and hourly staging-applier timer.
If state cleanup or timer enable/start/postcheck fails, it disables both timers
and stops any triggered one-shot services. The competing long-running
`mooncen-crawler.service` remains strictly disabled throughout.

## Distributed transition boundary

Before changing the active legacy state, obtain an authenticated inventory on
both hosts. On `gen1crawler`, record the enabled/active/result state for
`mooncen-crawler.timer`, `mooncen-crawler-once.service`, and every legacy
staging-apply unit. On `gen1db`, prove the exact marked staging database,
schema/migration digests, restricted applier identity, node-exporter target,
and crawler-control preflights. Historical assumptions about where the
staging applier runs are not accepted as evidence.

The host actions are intentionally separate:

1. Prepare and validate the staging database, control services, observer, and
   pinned staging applier on `gen1db` while all new recurring control units
   remain disabled.
2. Canary workers against the exact `gen1db` staging/control endpoint without
   enabling the central schedule.
3. In a reviewed maintenance window, wait for the legacy one-shot to finish
   and disable the legacy scheduler and competing applier automation on
   `gen1crawler`.
4. Recheck that the pinned applier on `gen1db` is ready, then enable the
   distributed control plane there. Never enable a control scheduler based on
   a repository-only status change.

No step in this transition moves or copies the production database from
`cloud`; `gen1db` is staging/control only.
