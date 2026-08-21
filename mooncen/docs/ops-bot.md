# MoonCen Ops Bot

> Archived deployment note. `n100` is retired and none of the commands below
> authorize a current production installation. The reviewed production roles
> are `cloud` for frontend/backend/database and `gen1crawler` for crawler
> execution. A new Ops Bot host and remote-check contract must be reviewed
> separately before this procedure is reused.

MoonCen Ops Bot is a Telegram monitoring and alert layer for operations.

The bot reports abnormal states and provides read-only status menus. It does
not promote PostgreSQL, move Cloudflare tunnels, or enable automatic failover.

## Historical Runtime Host

The retired deployment ran the bot on `n100`.

Historical roles described by this archived procedure:

- `cloud`: frontend, backend, Cloudflare tunnel, primary DB.
- `n100`: standby DB, crawler worker, ops bot.

## Historical Configuration

The retired installation edited `/opt/mooncen/.env` on `n100`:

```bash
MOONCEN_BOT_TOKEN=<telegram_bot_token>
MOONCEN_BOT_CHAT_ID=<allowed_chat_id>
MOONCEN_BOT_MONITOR_INTERVAL=10
OLLAMA_HOSTS=http://wtr-linux:11434,http://victus:11434
OLLAMA_MODEL=qwen3.5:9b
```

`MOONCEN_BOT_CHAT_ID` can contain multiple comma-separated chat IDs.
If it is empty, any chat that knows the bot token can issue commands, so do not leave it empty in production.

## Service Commands

```bash
mooncenctl bot-start
mooncenctl bot-status
mooncenctl bot-stop
```

Ops Console on the same BOT host:

```bash
mooncenctl ops-start
mooncenctl ops-status
mooncenctl ops-stop
```

Ops Console is a separate application and should be run from the operator workstation:

```powershell
cd C:\project\mooncen\ops-console
npm run dev
```

Direct systemd commands:

```bash
sudo systemctl enable --now mooncen-ops-bot
sudo systemctl status mooncen-ops-bot --no-pager
sudo journalctl -u mooncen-ops-bot -f
```

## Telegram Commands

```text
MENU
메뉴
OPERATION
운영
MONITORING
모니터링
/summary
/status
/monitoring
/services
/public_status
/replica_status
/cloudflare_status
/crawler_status
/staging_status
/ai_status
/backup_status
/failover_status
/manual_failover
```

`MENU`, `메뉴`, `/start`, `/help`, `/menu` return the same read-only
operations keyboard. Buttons send the slash commands above.

The following commands are intentionally disabled by policy and return an
instructional message only:

```text
/crawler_restart
/failover_enable
/failover_disable
/promote_n100
```

## Alerts

The retired bot periodically checked:

- n100 node role
- local PostgreSQL role and WAL receiver state
- crawler service state on n100
- crawler staging write mode and staging apply timer on n100
- cloudflared state on standby nodes
- public `mooncen.kr` and `www.mooncen.kr` health endpoints
- external Ollama AI nodes from `OLLAMA_HOSTS` or `OLLAMA_HOST`
- accidental presence of `/opt/mooncen/failover/enable_auto_failover`

It also tails `/opt/mooncen/failover/failover.log` for legacy/manual entries
containing:

- `cloud health failed`
- `promoting`
- `promoted`
- `completed`
- `disabled`

## Historical Failover Safety

Failover is manual. The authoritative pre-failover checks are:

```bash
sudo -u postgres psql -Atqc "SELECT CASE WHEN pg_is_in_recovery() THEN 'standby' ELSE 'primary' END;"
sudo -u postgres psql -Atqc "SELECT status || '|' || coalesce(sender_host,'') || '|' || coalesce(slot_name,'') FROM pg_stat_wal_receiver;"
systemctl is-active mooncen-crawler cloudflared mooncen-ops-bot
```

During a confirmed cloud outage, the operator manually promotes n100
PostgreSQL and manually moves the Cloudflare tunnel/DNS. Automatic failover
remains disabled to avoid split-brain during network partitions.
