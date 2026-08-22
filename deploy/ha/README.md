# MoonCen HA Scripts

Current topology:

- `cloud`: primary PostgreSQL DB and production app host.
- `n100`: PostgreSQL streaming standby and crawler host.
- Cloudflare tunnel should run only on the active primary host.
- Automatic failover is disabled by policy. Promotion is manual.

The detailed runbook is:

```text
deploy/ha/CLOUD_PRIMARY_N100_STANDBY.md
```

## Prepare Cloud Primary

Run on `cloud`:

```bash
cd /opt/mooncen
sudo chmod +x deploy/ha/postgres_primary_prepare.sh
sudo ./deploy/ha/postgres_primary_prepare.sh \
  --standby-ip <N100_IP_VISIBLE_FROM_CLOUD> \
  --replication-password '<REPLICATION_PASSWORD>' \
  --slot-name mooncen_n100_standby
```

Use the same replication password when cloning n100.

## Clone n100 Standby

This is destructive on n100 PostgreSQL data. Run only after confirming that
`cloud` is the authoritative primary.

Run on `n100`:

```bash
cd /opt/mooncen
sudo chmod +x deploy/ha/postgres_standby_clone.sh
sudo ./deploy/ha/postgres_standby_clone.sh \
  --primary-host <CLOUD_IP_OR_HOST_REACHABLE_FROM_N100> \
  --replication-password '<REPLICATION_PASSWORD>' \
  --slot-name mooncen_n100_standby \
  --wipe-data
```

## Check Replication

Run on both servers:

```bash
cd /opt/mooncen
sudo chmod +x deploy/ha/postgres_replication_status.sh
sudo ./deploy/ha/postgres_replication_status.sh
```

Expected roles:

- `cloud`: `primary`
- `n100`: `standby`

From Windows:

```powershell
.\deploy_mooncen.ps1 replica-status -Target cloud
.\deploy_mooncen.ps1 replica-status -Target n100
.\deploy_mooncen.ps1 ha-status
```

After n100 shows `standby`, standby deploy can proceed:

```powershell
.\deploy_mooncen.ps1 deploy -Target n100 -SkipWorkers
```
