# MoonCen HA: Cloud Primary, n100 Standby

## Target Topology

- `Cloud`: primary PostgreSQL DB and normal production app host.
- `n100`: PostgreSQL streaming replica and standby app host.
- If Cloud fails, n100 PostgreSQL is promoted and n100 app services are started.

This is not active-active. Only one DB must accept writes at a time.

Server inventory is managed by:

```text
config/deploy_servers.json
```

Current default target is `cloud`. Add future servers to the same file instead of hardcoding new target names into deployment scripts.

## Expected Production State

Before running standby deploy, confirm:

- `cloud` is the active primary DB and app host.
- `n100` is a PostgreSQL streaming standby using replication slot `mooncen_n100_standby`.
- `cloud` serves frontend/backend/Cloudflare traffic only. `mooncen-crawler` and `mooncen-ai-worker` stay disabled on Cloud.
- `n100` runs `mooncen-crawler` while still using Cloud as the writable primary DB through a systemd DB override.
- `n100` app, AI worker, and `cloudflared` stay stopped while Cloud is healthy.
- `n100` runs the optional Telegram Ops Bot when `MOONCEN_BOT_TOKEN` is configured.
- Automatic promotion is disabled. Do not create `/opt/mooncen/failover/enable_auto_failover` during normal operation.
- `n100` is monitored by the Telegram Ops Bot; PostgreSQL promotion and Cloudflare tunnel changes are manual operator actions.
- `mooncen-cloudflare-gate.timer` is installed on `n100` but disabled until manual promotion.

Quick status checks:

```bash
# Cloud
sudo -u postgres psql -Atqc "SELECT CASE WHEN pg_is_in_recovery() THEN 'standby' ELSE 'primary' END;"
sudo -u postgres psql -Atqc "SELECT count(*) FROM pg_stat_replication;"
systemctl is-active postgresql mooncen-api mooncen-frontend mooncen-crawler mooncen-ai-worker cloudflared mooncen-cloudflare-gate.timer

# n100
sudo -u postgres psql -Atqc "SELECT CASE WHEN pg_is_in_recovery() THEN 'standby' ELSE 'primary' END;"
sudo -u postgres psql -Atqc "SELECT status || '|' || coalesce(sender_host,'') || '|' || coalesce(slot_name,'') FROM pg_stat_wal_receiver;"
systemctl is-active postgresql mooncen-api mooncen-frontend mooncen-crawler mooncen-ai-worker cloudflared mooncen-failover-watch.timer mooncen-cloudflare-gate.timer
tail -n 20 /opt/mooncen/failover/failover.log
```

Crawler placement:

```bash
# Cloud should be service-only.
systemctl is-active mooncen-api mooncen-frontend cloudflared mooncen-crawler mooncen-ai-worker
# Expected: active active active inactive inactive

# n100 should crawl against Cloud primary DB while local PostgreSQL remains standby.
systemctl is-active mooncen-crawler mooncen-ai-worker cloudflared mooncen-failover-watch.timer
# Expected: active inactive inactive inactive

systemctl show mooncen-crawler -p Environment --value | tr ' ' '\n' | grep -E '^DB_HOST=|^DB_PORT='
# Expected: DB_HOST=cloud, DB_PORT=5432
```

Cloud PostgreSQL must allow normal MoonCen app DB connections from n100 in addition to physical replication:

```text
host mooncen mooncen_admin 100.113.112.64/32 scram-sha-256
```

This line belongs in Cloud's `pg_hba.conf`, then PostgreSQL must be reloaded.

When n100 is manually promoted during failover, remove the crawler DB override so the crawler switches back to local PostgreSQL after n100 becomes primary.

Ops Bot placement:

```bash
# Configure on n100 only.
sudo nano /opt/mooncen/.env

MOONCEN_BOT_TOKEN=<telegram_bot_token>
MOONCEN_BOT_CHAT_ID=<telegram_chat_id>

mooncenctl bot-start
mooncenctl bot-status
```

The bot is a monitoring and alert layer. It does not promote PostgreSQL, switch Cloudflare tunnels, or enable automatic failover.
Details: `docs/ops-bot.md`.

Ops Console placement:

```bash
# Run on n100/BOT for LAN/mobile monitoring.
mooncenctl ops-start
mooncenctl ops-status
```

Default URL: `http://BOT:8765`.

## Required Network

- Cloud PostgreSQL must allow TCP `5432` from n100.
- n100 must be able to reach Cloud on TCP `5432`.
- n100 must already be a PostgreSQL standby before standby app deploy.
- n100 app/crawler/AI services should stay disabled while Cloud is healthy.

## 1. Deploy App to Cloud as Active

From Windows:

```powershell
.\deploy_mooncen.ps1 targets
.\deploy_mooncen.ps1 deploy -Target cloud -ActiveCloud
```

Use `full-deploy` only for the first install or when OS packages are missing:

```powershell
.\deploy_mooncen.ps1 full-deploy -Target cloud -ActiveCloud
```

## 2. Prepare Cloud Primary for n100 Replica

Run on Cloud:

```bash
cd /opt/mooncen
sudo chmod +x deploy/ha/postgres_primary_prepare.sh
sudo ./deploy/ha/postgres_primary_prepare.sh \
  --standby-ip <N100_IP_VISIBLE_FROM_CLOUD> \
  --replication-password '<REPLICATION_PASSWORD>' \
  --slot-name mooncen_n100_standby
```

Use a random hex password and reuse the same value in the n100 clone command.

## 3. Clone n100 as Standby

Run on n100:

```bash
cd /opt/mooncen
sudo chmod +x deploy/ha/postgres_standby_clone.sh
sudo ./deploy/ha/postgres_standby_clone.sh \
  --primary-host <CLOUD_IP_OR_HOST_REACHABLE_FROM_N100> \
  --replication-password '<REPLICATION_PASSWORD>' \
  --slot-name mooncen_n100_standby \
  --wipe-data
```

This replaces n100 local PostgreSQL data with a base backup from Cloud.

## 4. Check Replication

On Cloud:

```bash
sudo ./deploy/ha/postgres_replication_status.sh
```

On n100:

```bash
sudo ./deploy/ha/postgres_replication_status.sh
```

n100 should show `role=standby`.

From Windows:

```powershell
.\deploy_mooncen.ps1 replica-status -Target cloud
.\deploy_mooncen.ps1 replica-status -Target n100
```

## 5. Deploy App to n100 as Standby

Run this only after n100 reports `db_role standby`:

```powershell
.\deploy_mooncen.ps1 deploy -Target n100 -SkipWorkers
```

The deploy script checks `pg_is_in_recovery()` before enabling standby crawler/staging services.

Keep frontend/API/AI/cloudflared stopped until failover:

```bash
sudo systemctl disable --now mooncen-api mooncen-frontend mooncen-ai-worker cloudflared mooncen-cloudflare-gate.timer || true
```

## 6. Keep Automatic Failover Disabled

The legacy watcher units may exist for diagnostics, but the timer must stay disabled in normal operation:

```bash
cd /opt/mooncen
sudo systemctl disable --now mooncen-failover-watch.timer
```

Create watcher config:

```bash
sudo mkdir -p /opt/mooncen/failover
sudo tee /opt/mooncen/failover/failover.env >/dev/null <<'ENV'
CLOUD_DB_HOST=cloud
CLOUD_DB_PORT=5432
FAIL_THRESHOLD=5
START_WORKERS=0
ENV
sudo chown -R mooncen:mooncen /opt/mooncen/failover || true
```

Keep automatic failover disabled during normal operation:

```bash
sudo rm -f /opt/mooncen/failover/enable_auto_failover
```

## 7. Failover Behavior

Failover is manual. Use the Telegram Ops Bot and Ops Console for monitoring,
then promote n100 PostgreSQL and move the Cloudflare tunnel manually if Cloud is
confirmed unavailable.

If Cloud DB health fails `FAIL_THRESHOLD` consecutive times:

1. n100 confirms its local DB is still `standby`.
2. n100 runs `pg_promote()`.
3. n100 starts `mooncen-api`, `mooncen-frontend`, and `cloudflared`.
4. Workers stay stopped unless `START_WORKERS=1`.

## 8. Cloudflare Health Gate

Cloudflare Tunnel can keep routing traffic to a broken host if the tunnel process is still running.
MoonCen includes a local health gate that stops `cloudflared` when local services become unhealthy.

MoonCen also includes a Cloudflared role guard:

```text
mooncen-cloudflared-role-guard.service
mooncen-cloudflared-role-guard.timer
```

The guard reads `/etc/mooncen-node-role`.

- `primary`: `cloudflared` may run. If it is enabled but stopped, the guard starts it.
- `standby`: `cloudflared` is stopped and disabled automatically.

This prevents Cloudflare from routing the same public tunnel to both `cloud` and `n100`
while `n100` is only a standby node. During manual failover, the operator changes
`/etc/mooncen-node-role` to `primary` before starting `cloudflared`.

The shared tunnel design uses one Cloudflare Tunnel token across MoonCen
nodes. Do not create separate DNS records for a `cloud` tunnel and an `n100`
tunnel for the same hostname. Instead:

```text
mooncen.kr / www.mooncen.kr
-> one Cloudflare Tunnel
-> cloudflared runs only on the node whose /etc/mooncen-node-role is primary
```

Set `MoonCenCloudflaredToken` in `deploy.local.ps1`, or let standby deploy copy
the active node's installed token when SSH access allows it.

Installed units:

```text
mooncen-cloudflare-gate.service
mooncen-cloudflare-gate.timer
```

Default checks:

- `postgresql`
- local PostgreSQL connection to `DB_NAME`
- `nginx`
- `mooncen-api`
- `mooncen-frontend`
- `http://127.0.0.1:8001/health`
- `http://127.0.0.1/health`
- `http://127.0.0.1:5173`

Default behavior:

- If health fails 2 consecutive checks, stop `cloudflared.service`.
- If health recovers, start `cloudflared.service` only when the service is enabled.
- Check interval is 1 minute.

Install or refresh on a server:

```bash
cd /opt/mooncen
sudo cp deploy/ubuntu/systemd/mooncen-cloudflare-gate.service /etc/systemd/system/
sudo cp deploy/ubuntu/systemd/mooncen-cloudflare-gate.timer /etc/systemd/system/
sudo cp deploy/ubuntu/systemd/mooncen-cloudflared-role-guard.service /etc/systemd/system/
sudo cp deploy/ubuntu/systemd/mooncen-cloudflared-role-guard.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mooncen-cloudflare-gate.timer
sudo systemctl enable --now mooncen-cloudflared-role-guard.timer
```

Check status:

```bash
mooncenctl cloudflare-gate-status
```

Temporarily disable the gate:

```bash
mooncenctl cloudflare-gate-disable
```

Enable it again:

```bash
mooncenctl cloudflare-gate-enable
```

Optional config can be placed in `/opt/mooncen/failover/failover.env`:

```bash
CLOUDFLARE_GATE_FAIL_THRESHOLD=2
CLOUDFLARE_GATE_RECOVER_THRESHOLD=1
CLOUDFLARE_GATE_AUTO_RESTORE=1
CLOUDFLARE_GATE_CHECK_DB=1
CLOUDFLARE_GATE_REQUIRED_SERVICES="postgresql nginx mooncen-api mooncen-frontend"
CLOUDFLARE_GATE_API_HEALTH_URL=http://127.0.0.1:8001/health
CLOUDFLARE_GATE_NGINX_HEALTH_URL=http://127.0.0.1/health
CLOUDFLARE_GATE_FRONTEND_URL=http://127.0.0.1:5173
```

On standby n100, do not enable this timer until n100 is manually promoted.

## Split-Brain Warning

Automatic failover can be wrong during a network partition. If Cloud is still alive but n100 cannot reach it, n100 may promote itself and both databases may accept writes.

Safer production options:

- Keep auto failover disabled and promote n100 manually.
- Use Cloudflare/API health plus DB health before promotion.
- Add fencing: stop Cloud app/DB before n100 promotion when possible.
- After failover, rebuild the old Cloud DB from n100 before using Cloud again.

## Manual Failover to n100

Run on n100:

```bash
cd /opt/mooncen
sudo ./deploy/ha/postgres_promote_standby.sh
sudo systemctl enable --now mooncen-api mooncen-frontend
sudo systemctl enable --now cloudflared
```

Start workers only after confirming Cloud is down:

```bash
sudo systemctl enable --now mooncen-ai-worker mooncen-crawler
```
