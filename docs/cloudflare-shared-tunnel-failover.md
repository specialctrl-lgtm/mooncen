# Cloudflare Shared Tunnel Failover

MoonCen HA uses one shared Cloudflare Tunnel token on every node.

## Why

Automatic failover should not depend on changing DNS records at failure time.
Instead, `mooncen.kr` and `www.mooncen.kr` should point to one shared
Cloudflare Tunnel. Only the active primary node runs `cloudflared`.

## Role Behavior

```text
/etc/mooncen-node-role=primary -> cloudflared may run
/etc/mooncen-node-role=standby -> cloudflared is stopped and disabled
```

The role is enforced by:

```text
mooncen-cloudflared-role-guard.service
mooncen-cloudflared-role-guard.timer
```

## Commands

Sync the active node's tunnel token to standby:

```powershell
.\deploy_mooncen.ps1 cloudflared-token-sync -Target n100
```

Check role guard status:

```powershell
.\deploy_mooncen.ps1 cloudflared-role-guard-status -Target cloud
.\deploy_mooncen.ps1 cloudflared-role-guard-status -Target n100
```

Run role guard immediately:

```powershell
.\deploy_mooncen.ps1 cloudflared-role-guard-run -Target cloud
.\deploy_mooncen.ps1 cloudflared-role-guard-run -Target n100
```

## Cloudflare Settings

Cloudflare DNS/Public Hostname should point both hostnames to the same shared
tunnel:

```text
mooncen.kr
www.mooncen.kr
```

Do not point production hostnames to a standby-only tunnel while the standby
node's `cloudflared` is intentionally stopped.
