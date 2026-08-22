# MoonCen production deployment

The reviewed target topology has three service hosts. Frontend, backend, and
the production PostgreSQL database run on DNS host `cloud`. The shared writable
staging database and distributed crawler-control services run on DNS host
`gen1db`. Crawler execution runs on `gen1crawler` and later worker nodes.
`gen1db` is never the production database.

| Topology service | Node | Placement contract |
| --- | --- | --- |
| `frontend`, `backend`, `database` | `cloud` | production application and production DB |
| `staging_database` | `gen1db` | shared writable crawler staging only |
| `crawler_control` | `gen1db` | scheduler, finalizer, release publication, observer, pinned applier |
| `crawler` | `gen1crawler` and reviewed workers | legacy primary during transition; worker-only after cutover |

The current transition state remains explicit: while
`config/production_topology.json` says `"crawlerMode": "legacy"`,
`gen1crawler` is still the legacy runtime owner. Adding the target
`staging_database` and `crawler_control` placements does not itself enable the
new scheduler or prove a remote cutover. `config/production_topology.json` is
the service-placement authority and must not infer crawler or control
ownership from the Web/API/production-database host.

## Server registry

Copy the tracked example once and keep the local file secret-free except for
the SSH authentication selector:

```powershell
Copy-Item config\deploy_servers.example.json config\deploy_servers.json
notepad config\deploy_servers.json
```

The application transport registry contains the reviewed application and
legacy crawler targets. `deployProfile` prevents the dedicated crawler target
from ever entering the full-stack deployment path:

```json
{
  "defaultTarget": "cloud",
  "servers": {
    "cloud": {
      "server": "cloud",
      "user": "ubuntu",
      "domain": "mooncen.kr",
      "remoteDir": "/opt/mooncen",
      "identityFile": "ssh-agent",
      "role": "primary",
      "deployProfile": "full-stack",
      "opsConsole": false,
      "active": true
    },
    "gen1db": {
      "server": "gen1db",
      "user": "sgm",
      "domain": "gen1db",
      "remoteDir": "/opt/mooncen",
      "identityFile": "ssh-agent",
      "role": "crawler-control",
      "deployProfile": "control-only",
      "opsConsole": false,
      "active": false
    },
    "gen1crawler": {
      "server": "gen1crawler",
      "user": "sgm",
      "domain": "gen1crawler",
      "remoteDir": "/opt/mooncen",
      "identityFile": "ssh-agent",
      "role": "crawler",
      "deployProfile": "crawler-only",
      "opsConsole": false,
      "active": false
    }
  }
}
```

`server` is the reviewed DNS name, not a copied IP address. `ssh-agent` means
the normal OpenSSH/Tailscale/default-agent authentication path; it is not a
filesystem key name. `/opt/mooncen` is fixed because the systemd and root-owned
helper contracts use that path. `control-only` is not an application deployment
profile: it permits only the separately reviewed staging/control workflow and
must remain `active=false` in the application transport registry.

## Deployment

An application deployment must name the target explicitly:

```powershell
.\deploy_mooncen.ps1 deploy -Target cloud
```

The normal cloud application deployment installs and operates:

- frontend and API;
- the production PostgreSQL database and schema migrations;
- non-crawler application workers.

The legacy crawler runtime is owned by `gen1crawler`, not by the cloud
application deployment. Its local staging and applier units are transition
state that must be authenticated and inventoried; they are not the distributed
target. The target placement makes `gen1db` the shared staging/control host and
pinned-applier owner, while `gen1crawler` becomes worker-only. Follow
`deploy/ubuntu/GEN1_SPLIT.md` for legacy safeguards and
`docs/distributed-crawler-control-plane.md` for the two-host cutover. Do not
enable a competing crawler scheduler on `cloud`, `gen1crawler`, or `gen1db`.

`gen1db` must use the dedicated fail-closed crawler-control installer and a
separately reviewed non-full-stack transport path. Never run the normal cloud
deployment against it and never migrate or restore the production `cloud`
database there as part of crawler-control setup.

The source-only transport is present but not enabled by the installer. It
requires a clean exact commit, reviewed archive and canonical tree SHA-256,
detached OpenSSH signature accepted by a root-owned allowed-signers policy,
and a hash-locked CPython 3.11 x86_64 runtime. The outer action stops locally
until fresh staging backup/restore evidence is issued and authenticated on the
real `gen1db`; do not invoke the transport script directly to bypass that gate.

Updating the standard deploy registry or moving crawler ownership requires a
separate reviewed topology/deployment change. It is not implied by a cloud web
deployment.

## Crawler updates

`gen1crawler` is the current runtime owner; this is not a cloud-to-crawler
cutover. A normal cloud Web/API/DB deployment neither checks nor changes any
crawler unit. It disables local crawler staging during setup and excludes
crawler/staging units from the cloud systemd install manifest. If an obsolete
crawler unit is ever discovered on cloud, report it as topology drift and
handle it in a separately reviewed cleanup—it is not part of application
deployment or crawler update.

There is currently no supported automated crawler-only release uploader from
Windows. `deploy` and `full-deploy` therefore reject `-Target gen1crawler`, and
`deploy-all` excludes its `crawler-only` profile. The explicit updater status
action is fail-closed and performs no SSH or remote mutation:

```powershell
.\deploy_mooncen.ps1 crawler-update -Target gen1crawler
```

It reports unavailable until a transactional, provenance-verified crawler
release uploader exists. Do not substitute an in-place copy followed by
`setup_split_crawler.sh`: setup disables the current timers and has no release
rollback, so it is bootstrap/lab tooling rather than a supported live-owner
update.

`crawler-activate` is narrower than a release update. It may activate an exact
reviewed staging batch only after a separately trusted, quiescent installation
has already established the release and disabled both timers:

```powershell
.\deploy_mooncen.ps1 crawler-activate -Target gen1crawler -BatchId REVIEWED_CRAWL_BATCH_ID
```

This action authenticates only the reviewed `gen1crawler` registry target. It
runs the pinned dry-run, verifies and applies the exact batch, then enables the
two gen1crawler timers. A failed validation leaves those timers disabled; it
does not SSH to cloud or mutate cloud systemd state. The gen1crawler applier
connects with its restricted DB role to the production PostgreSQL endpoint on
DNS host `cloud`, because applying the reviewed batch is the intended data
update.

## Operations

```powershell
.\deploy_mooncen.ps1 summary -Target cloud
.\deploy_mooncen.ps1 status -Target cloud
.\deploy_mooncen.ps1 health -Target cloud
ssh gen1crawler 'systemctl status mooncen-crawler.timer mooncen-crawler-once.service'
ssh gen1db 'systemctl status postgresql.service mooncen-crawler-control-metrics.timer'
```

Ops Console remains local-only. It reads desired placement from
`config/production_topology.json`, so Web, Backend, and DB display `cloud`,
Crawler displays `gen1crawler`, and staging database/crawler control display
`gen1db`; the checked endpoint and reporting Agent are shown separately. Until
the distributed cutover is authenticated, the UI must also continue to show
the legacy crawler runtime state rather than presenting desired placement as a
completed installation.
Starting the local console does not start a crawler scheduler, crawler worker,
or quality worker. Those processes require the explicit
`-EnableLocalCrawlerRuntime` switch and are only for an isolated development
database; production crawling remains owned by the timer on `gen1crawler`.

Historical HA and staging documents may remain for audit provenance, but they
do not override the current placement. In particular, `gen1db` is no longer to
be described as a retired experiment: it is the selected staging/control host,
not the production DB. Adding another node requires a reviewed topology
change, matching registry entry, monitoring inventory update, and a new
deployment snapshot.
