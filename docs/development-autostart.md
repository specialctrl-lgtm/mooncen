# Development server autostart

The Windows development host uses one startup supervisor for both local UIs:

- Ops Console: `http://127.0.0.1:5175/`
- development web: `http://127.0.0.1:5174/`
- shared API: `http://127.0.0.1:8001/health`

Do not register `start_ops_console.ps1` and `start_dev.ps1` as separate startup tasks. Both full launchers use port `8001`. The supervisor starts Cloud-backed Ops first and starts the development web with `-FrontendOnly` only after the shared API is healthy.

## Prerequisites

Cloud mode must work without an interactive SSH prompt. Create a dedicated, restricted SSH key outside the repository, install its public key for `ubuntu@cloud`, and restrict the private key ACL to the scheduled-task account, `SYSTEM`, and `Administrators`. Do not put the key, its passphrase, or a Windows password in this repository, task arguments, or log files.

The real connection check is intentionally fail-closed:

```powershell
cd C:\project\project\mooncen
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass `
  -File .\start_development_autostart.ps1 `
  -Action Preflight `
  -DataSource Cloud `
  -SshIdentityFile "$env:USERPROFILE\.ssh\mooncen_ops_autostart"
```

It checks the repository runtimes, key ACL, `known_hosts`, and a bounded `BatchMode` SSH connection. The task is not registered if any check fails.

Visitor analytics does not require another SSH tunnel. When both
`OPS_CLOUDFLARE_ANALYTICS_ZONE_ID` and `OPS_CLOUDFLARE_ANALYTICS_TOKEN` exist
in the cloud deploy account's protected mode-`0600`
`~/.config/mooncen/deploy-secrets.env`, the launcher passes them only to the
local Ops API. If both are absent, the Dashboard reports that aggregation is
unavailable. A partial or malformed pair stops startup instead of silently
showing zero.

Crawler analytics is a separate, explicit opt-in. It does not reuse the cloud
database tunnel or production API login. Before enabling it, install a second
restricted public key for `sgm@gen1db`. Its `authorized_keys` entry must begin
with this exact forward-only constraint before the public-key type and key:

```text
restrict,port-forwarding,command="/usr/bin/false",permitopen="127.0.0.1:5432" ssh-ed25519 PUBLIC_KEY mooncen-crawler-control-ops
```

Do not authorize this key without the constraint, omit the forced `command`, or
add another `permitopen`. `restrict` plus `permitopen` alone still permits an
SSH exec request, so `/usr/bin/false` is required to make the key forward-only.
It is dedicated to the loopback PostgreSQL forward and cannot be used as a
general-purpose login key. The three `OPS_CRAWLER_*` API values
remain in the cloud deploy account's protected mode-`0600`
`~/.config/mooncen/deploy-secrets.env`; no database password is stored on or
transported through the gen1db SSH identity. The launcher reads only the three
allowlisted keys and never writes or logs that file.

## Install the startup task

Run the following command. If the current window is not elevated, the installer opens a visible UAC administrator window automatically. Approve that prompt; the elevated window then asks for the scheduled-task account password.

```powershell
cd C:\project\project\mooncen
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install_development_autostart.ps1 `
  -Action Install `
  -DataSource Cloud `
  -SshIdentityFile "$env:USERPROFILE\.ssh\mooncen_ops_autostart" `
  -StartNow
```

The installer prompts locally for the Windows password of the task account. The password is passed to Windows Task Scheduler in memory and is never written to the task command, XML, repository, or MoonCen logs.

To opt into crawler analytics, add the separate target and key. Omitting these
arguments preserves the current disabled/unavailable behavior.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install_development_autostart.ps1 `
  -Action Install `
  -DataSource Cloud `
  -SshIdentityFile "$env:USERPROFILE\.ssh\mooncen_ops_autostart" `
  -CrawlerControlSshTarget "sgm@gen1db" `
  -CrawlerControlSshIdentityFile "$env:USERPROFILE\.ssh\mooncen_crawler_control_readonly" `
  -StartNow
```

This creates a second SSH forward on `127.0.0.1:15433` to the loopback
PostgreSQL listener on `gen1db`. Only the local API receives the resulting
`OPS_CRAWLER_*` environment. API readiness then proves the marked
`mooncen_staging` database and the login's server-side read-only transaction
default without returning a credential or crawler record.

If an existing startup task is already running with analytics disabled or with
different SSH arguments, `Install -StartNow` fails before changing the task.
Stop the verified task and services first, then repeat the full install command:

```powershell
.\install_development_autostart.ps1 -Action Stop -DataSource Cloud
```

Alternatively, omit `-StartNow` when installing the new definition and reboot;
the boot trigger will start only the newly registered supervisor. Do not rely
on `StartNow` to replace a running `IgnoreNew` supervisor.

The exact task name is `MoonCen-DevelopmentServices`. It has one `AtStartup` trigger with a 60-second delay, `Password` logon, least privilege, `StartWhenAvailable`, bounded Task Scheduler restart, and `IgnoreNew` duplicate-instance handling. A file lock provides a second singleton guard.

## Status and control

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install_development_autostart.ps1 -Action Status -DataSource Cloud
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install_development_autostart.ps1 -Action Start -DataSource Cloud
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install_development_autostart.ps1 -Action Stop -DataSource Cloud
```

To remove it, use an elevated PowerShell window:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install_development_autostart.ps1 -Action Uninstall -DataSource Cloud
```

The supervisor log and state live under `logs/development-autostart/`. The log rotates at 5 MiB and keeps three prior files. The monitor applies bounded exponential retry, checks the Ops process identity and deployment-worker heartbeat, and never stops an unrelated process that happens to own one of its ports.

## Failure behavior

- If SSH, Tailscale, or the cloud host is temporarily unavailable, the supervisor remains alive and retries with backoff.
- The development web does not start until the Ops API is healthy.
- A foreign listener on `5174`, `5175`, `8001`, `15432`, `15433` (when opted in), or `18001` blocks repair; it is reported but not killed.
- An active deployment prevents Ops shutdown/restart through the existing Ops safety gate.
- Local data mode requires a real PostgreSQL listener on `5432`; it never enables the local crawler runtime automatically.

After installation, verify one actual reboot before treating the host as boot-ready: check the task result, all three HTTP endpoints, port `15432`, and a fresh `logs/ops-console-local/deployment-worker.heartbeat.json`.
