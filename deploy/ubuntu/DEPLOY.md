# MoonCen Ubuntu Deployment

> Production role boundary: `cloud` hosts Web/API/PostgreSQL only, while
> `gen1crawler` is the crawler owner. Never install, enable, inspect, or run
> crawler units on `cloud`. Use `docs/multi-server-deployment.md` and
> `deploy/ubuntu/GEN1_SPLIT.md` for the current production crawler workflow.
> Single-host crawler commands retained below apply only to an isolated
> development host or to `gen1crawler` itself.

Recommended OS: Ubuntu Server 24.04 LTS.

This guide assumes the project is deployed to `/opt/mooncen` and runs:

- Nginx on port `80`
- Frontend service on `127.0.0.1:5173`
- FastAPI service on `127.0.0.1:8001`
- PostgreSQL/PostGIS locally
- crawler worker as a systemd service, active from 22:00 to 07:00
- AI worker as a systemd service, active from 22:00 to 07:00

## 1. Create deploy directory

```bash
sudo mkdir -p /opt/mooncen
sudo chown "$USER":"$USER" /opt/mooncen
```

## 2. Copy project files

Example from your PC:

```bash
rsync -av --exclude node_modules --exclude frontend2/dist --exclude __pycache__ ./ user@SERVER_IP:/opt/mooncen/
```

Or clone/pull from your repository into `/opt/mooncen`.

### Windows one-command deploy

Deployment SSH is fail-closed with `StrictHostKeyChecking=yes`. Before the
first connection, obtain the server's Ed25519 host-key fingerprint through the
cloud console or another authenticated channel, then compare it with a
temporary scan before adding it to your Windows OpenSSH trust store:

```powershell
$candidate = New-TemporaryFile
ssh-keyscan -t ed25519 your-domain.com | Set-Content -Encoding ascii $candidate
ssh-keygen -lf $candidate
# Stop unless this SHA256 fingerprint exactly matches the independently verified value.
New-Item -ItemType Directory -Force "$HOME\.ssh" | Out-Null
Get-Content $candidate | Add-Content -Encoding ascii "$HOME\.ssh\known_hosts"
Remove-Item -LiteralPath $candidate -Force
```

`ssh-keyscan` does not authenticate the key by itself. Pin each exact hostname
or IP spelling used by deployment and monitoring commands.

From PowerShell in the project root:

```powershell
.\deploy_ubuntu.ps1 `
  -Server your-domain.com `
  -User ubuntu `
  -Domain your-domain.com `
  -KakaoMapsJavascriptKey "your-kakao-javascript-key" `
  -KakaoMapsRestApiKey "your-kakao-rest-api-key" `
  -OllamaHost "http://victus:11434"
```

Simpler repeated deploy after creating `deploy.local.ps1`:

```powershell
Copy-Item deploy.local.example.ps1 deploy.local.ps1
notepad deploy.local.ps1
.\deploy_mooncen.ps1 deploy
```

Useful one-command operations from Windows:

```powershell
.\deploy_mooncen.ps1 status
.\deploy_mooncen.ps1 doctor
.\deploy_mooncen.ps1 restart
.\deploy_mooncen.ps1 logs -Service api
.\deploy_mooncen.ps1 coordinates -Target gen1crawler
.\deploy_mooncen.ps1 crawler-once -Target gen1crawler
```

`-Server` can be either an IP address or a domain. If `-Domain` is omitted,
the script uses `-Server` as the service domain automatically.

If SSH must connect to an IP but Nginx/CORS should use a domain:

```powershell
.\deploy_ubuntu.ps1 `
  -SshHost SERVER_IP `
  -User ubuntu `
  -Domain your-domain.com
```

If `rsync` is not installed on Windows, force the built-in `tar + scp` fallback:

```powershell
.\deploy_ubuntu.ps1 -Server your-domain.com -User ubuntu -UseScpFallback
```

After the first deploy, faster app-only redeploy:

```powershell
.\deploy_ubuntu.ps1 -Server your-domain.com -User ubuntu -SkipSystemPackages
```

## 3. Install packages

Optional preflight: install the least-privilege diagnostic and Cloudflared
token-helper sudo rules before the first setup (the setup script also installs
the same rules for its invoking deploy user):

```bash
cd /opt/mooncen
chmod +x deploy/ubuntu/install_sudoers.sh
./deploy/ubuntu/install_sudoers.sh sgm
```

```bash
cd /opt/mooncen
chmod +x deploy/ubuntu/install_system_packages.sh
./deploy/ubuntu/install_system_packages.sh
```

The installer does not execute downloaded setup scripts. Its supply-chain pins
are reviewed in source:

- Node.js defaults to the official `v24.18.0` Active LTS Linux archive. The installer
  verifies Node's signed `SHASUMS256.txt.asc` with the Node.js release keyring
  pinned at commit `890d535527789c9ebccdccdafd708f60dbd56786` and keyring
  SHA256 `6030d4e0cd53330acf2ab68acd455b7ca98bb5d5975376f0b7c0892308ba2d57`.
- On amd64, Chrome for Testing and ChromeDriver are both pinned to
  `150.0.7871.115` and verified against the SHA256 values in the installer.
  Dependencies come from the verified archive's `deb.deps` manifest. A narrow,
  root-owned AppArmor profile permits Chrome's user-namespace sandbox at the
  versioned `/opt/chrome-for-testing` path on Ubuntu 24.04. Other architectures
  use Ubuntu's distro-verified matching Chromium/ChromeDriver packages and fail
  installation if a matching pair is unavailable.
- `NODE_VERSION` may select another official release, but its archive must
  still be present in a manifest signed by the pinned Node.js release keyring.

Review and update the version, keyring commit, and checksum together. Node's
official verification procedure is documented at
<https://github.com/nodejs/node#verifying-binaries>. Chrome for Testing's
official availability and download metadata is documented at
<https://github.com/GoogleChromeLabs/chrome-for-testing>.

## 4. Configure and build

Set secrets first:

```bash
export DB_PASSWORD='CHANGE_ME_STRONG_PASSWORD'
export AUTH_SECRET="$(openssl rand -hex 32)"
export NODE_ROLE='primary'
export BACKUP_AGE_RECIPIENT='age1...PUBLIC_RECIPIENT...'
export DOMAIN='your-domain.com'
export KAKAO_MAPS_JAVASCRIPT_KEY='your-kakao-javascript-key'
export KAKAO_MAPS_REST_API_KEY='your-kakao-rest-api-key'
export MOONCEN_ADMIN_EMAILS='verified-admin@example.com'
export MOONCEN_ADMIN_PROVIDER_IDS='google:provider-subject-id'
export MOONCEN_OPS_LOGIN_ID='opsadmin'
export MOONCEN_OPS_PASSWORD_HASH='pbkdf2_sha256$600000$...$...'
export OLLAMA_HOST='http://victus:11434'
export OLLAMA_MODEL='qwen3.5:9b'
```

`KAKAO_MAPS_JAVASCRIPT_KEY` is compiled into the frontend bundle. Register the
production and local origins in the Kakao JavaScript SDK domain settings and
enable the Kakao Map product before deploying. `KAKAO_MAPS_REST_API_KEY` is a
server-only crawler/geocoding credential installed only in `crawler.env`; never
give it a `VITE_*` prefix or substitute it for the JavaScript key. Coordinate
backfill is Kakao-only; Google Maps credentials are neither accepted by the
deployment command nor installed in any service environment. Each run is
limited by `KAKAO_GEOCODE_MAX_REQUESTS_PER_RUN` (default `1000`).

Kakao Maps is not unconditionally free. Since 2026-07-21, the free quota is
available only to the first Kakao Maps app activated for a developer account;
confirm that the app shows the `카카오맵 무료 쿼터` badge before rollout. Other
apps and over-quota traffic require paid API settings. Check the current
[Kakao Maps policy](https://developers.kakao.com/docs/ko/kakaomap/common) and
[quota/pricing table](https://developers.kakao.com/docs/ko/getting-started/quota)
before changing the request limit.

`BACKUP_AGE_RECIPIENT` is public. Keep its matching private identity outside
the repository at `/etc/mooncen/backup-age-key.txt` with restricted access.
On a primary node setup fails closed unless that identity already exists as a
regular, non-symlink `root:root` file with mode `0600`; setup never creates or
replaces the private identity.

```bash
sudo install -d -o root -g root -m 0751 /etc/mooncen
sudo install -o root -g root -m 0600 /secure/operator/path/age-identity.txt /etc/mooncen/backup-age-key.txt
```

Runtime API/crawler/applier/backup passwords are generated separately when
omitted. No database password, OAuth secret, auth signing secret, bot token, or
AI credential is written to `/opt/mooncen/.env`; that file is a deliberately
credential-free compatibility file for operator tooling.

Generate the dedicated Ops password verifier with
`python tools/generate_ops_password.py`. Windows deployments read
`$MoonCenOpsLoginId` and `$MoonCenOpsPasswordHash` from `deploy.local.ps1` (or
the matching process environment variables), transmit them through the same
Base64 transport used for other deployment secrets, and install them only in
`/etc/mooncen/api.env`. After the first successful deploy, both values are
preserved in the deploy user's mode-`0600` secret store and reused when the
local values are omitted. A deploy fails before activation if neither source
contains a complete, valid Ops login configuration.

If `PRIMARY_DB_HOST` is not local, provision the PostgreSQL CA certificate on
the server before setup and export its absolute path:

```bash
sudo install -o root -g root -m 0644 company-postgres-ca.crt /etc/mooncen-db-root-ca.crt
export DB_SSLROOTCERT=/etc/mooncen-db-root-ca.crt
```

Remote production DB setup fails when this value is absent, points to a
symlink/non-file, or is group/world-writable. Setup copies it to
`/etc/mooncen/db-root-ca.crt` as `root:mooncen-db-tls` mode `0640`; only Python
DB service accounts join that group. Crawler, AI, applier, and functional
environments explicitly set `ENVIRONMENT=production`, so a later remote host
change defaults to `sslmode=verify-full` rather than `prefer`.

Run project setup:

```bash
cd /opt/mooncen
chmod +x deploy/ubuntu/setup_project.sh
./deploy/ubuntu/setup_project.sh
```

## 5. Install systemd services

```bash
sudo cp deploy/ubuntu/systemd/*.service /etc/systemd/system/
sudo cp deploy/ubuntu/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable mooncen-api mooncen-frontend mooncen-ai-worker mooncen-crawler.timer
sudo systemctl restart mooncen-api mooncen-frontend mooncen-ai-worker mooncen-crawler.timer
```

Check logs:

```bash
sudo journalctl -u mooncen-frontend -f
sudo journalctl -u mooncen-api -f
sudo journalctl -u mooncen-ai-worker -f
sudo journalctl -u mooncen-crawler-once -f
```

Cloudflared runs as the non-login `cloudflared` account with a hardened unit.
Its token remains `/etc/cloudflared/token` as `root:cloudflared` mode `0640`
and reaches the process through systemd credentials. Token install/update uses
stdin only:

```bash
printf '%s\n' "$TUNNEL_TOKEN" | mooncenctl cloudflared-token
```

The deploy user's NOPASSWD contract permits only the root-owned helper's exact
`install` and `read` actions. The token is never passed in argv or embedded in
the unit. Do not grant direct `cat`, `tee`, or arbitrary systemctl sudo access.

## 6. Install Nginx config

```bash
sudo cp deploy/ubuntu/nginx/mooncen.conf /etc/nginx/sites-available/mooncen.conf
sudo ln -sf /etc/nginx/sites-available/mooncen.conf /etc/nginx/sites-enabled/mooncen.conf
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

## 7. Verify

```bash
curl http://127.0.0.1:8001/health
curl http://localhost/health
```

Open:

```text
http://SERVER_IP/
```

## Useful commands

MoonCen control helper:

```bash
mooncenctl status
mooncenctl doctor
mooncenctl restart
mooncenctl logs api
mooncenctl logs frontend
# Run these two commands only on gen1crawler.
mooncenctl coordinates
mooncenctl crawler-once
```

Role-scoped data actions (`sitemap`, coordinates, AI reset/quality, and summary
DB reads), fixed service lifecycle commands, one-shot checks/backups, and recent
log reads use the root-owned exact-action operations helper and are safe for
non-TTY automation. `mooncenctl logs` intentionally returns a bounded recent
window instead of holding a privileged `journalctl -f` process open. No raw
`systemctl`, wildcard journal-read, or wildcard service-mutation sudo rule is
installed.

Service status. Query crawler units only on `gen1crawler`:

```bash
# cloud
sudo systemctl status mooncen-frontend mooncen-api mooncen-ai-worker

# gen1crawler
sudo systemctl status mooncen-crawler.timer mooncen-crawler-once.service
```

Restart services on their owning hosts:

```bash
# cloud
sudo systemctl restart mooncen-frontend mooncen-api mooncen-ai-worker

# gen1crawler
sudo systemctl restart mooncen-crawler.timer
```

Edit service settings:

```bash
sudoedit /etc/mooncen/api.env
sudo systemctl restart mooncen-api
```

Use the matching file when changing another service:

| Service | Unix account | Environment file | Sensitive scope |
| --- | --- | --- | --- |
| API | `mooncen-api` | `/etc/mooncen/api.env` | API DB login, auth signing secret, OAuth secrets, admin allowlists, Ops password verifier |
| crawler / coordinates | `mooncen-crawler.timer` + `mooncen-crawler-once.service` | `/etc/mooncen/crawler.env` | crawler DB login, Kakao REST geocoding key |
| AI worker | `mooncen-ai` | `/etc/mooncen/ai.env` | AI-only column-scoped DB login, AI endpoints/config |
| frontend | `mooncen-web` | `/etc/mooncen/frontend.env` | none; host, port, and `NODE_ENV` only |
| ops bot | `mooncen-bot` | `/etc/mooncen/bot.env` | Telegram token/chat IDs only |
| staging applier | `mooncen-applier` | `/etc/mooncen/applier.env` | staging crawler and primary applier DB logins |
| functional check | `mooncen-check` | `/etc/mooncen/functional-test.env` | courses/branches check-only DB login and Telegram failure-alert values |
| backup | configured backup OS user | `/etc/mooncen/backup.env` | read-only backup DB login and backup settings |

The setup script creates each file as `root:<service-group>` with mode `0640`.
Do not copy values between these files. In particular, the frontend account
must never be added to API, crawler, AI, bot, applier, or backup groups.
The configured backup OS account is added only to the credential-free
application code-read group so it can traverse and execute the root-owned
backup scripts; it receives no API/crawler/AI/bot/applier environment group.
The functional check receives its own courses/branches SELECT-only login because
its required checks issue direct queries; it does not share the broader backup
login. The AI worker likewise uses a dedicated column-scoped login instead of
the crawler's mutation role. The check receives only the bot token/chat values
needed to report failures, not the bot's state or AI config.
If either bot value is intentionally empty, Telegram failure delivery is
disabled and the systemd journal plus `/var/lib/mooncen-check/latest.json` are
the authoritative alert/report path; this is an explicit journald-only policy.

Run one crawler test immediately on `gen1crawler` only:

```bash
mooncenctl crawler-once
```

Run the scheduled crawler manually through systemd on `gen1crawler` only:

```bash
mooncenctl crawler-once
```

Run one AI batch immediately:

```bash
sudo systemctl stop mooncen-ai-worker.service
sudo -u mooncen-ai /bin/bash -c 'set -a; source /etc/mooncen/ai.env; set +a; cd /opt/mooncen; exec .venv/bin/python -X utf8 run_ai_pipeline.py --once --limit 10 --delay 0 --ignore-active-window'
sudo systemctl start mooncen-ai-worker.service
```

Rebuild frontend:

```bash
cd /opt/mooncen/frontend2
sudo -u mooncen npm run build
sudo systemctl restart mooncen-frontend
```

## Notes

- Windows deployment extracts the committed Git archive into a private `/opt/.mooncen-release-*` staging directory, rejects local/mutable paths and symlinks, verifies every MoonCen unit has stopped with `MainPID=0`, and then activates it with bounded same-filesystem renames. Only `logs` and `failover` state are carried forward. A setup failure restores the previous code release and leaves services stopped for a clean redeploy.
- Setup builds a new isolated virtual environment under `.venv.stage.*`, installs only the hash-locked requirements there, and renames it into place after installation succeeds. Old packages, `.pth` files, removed root helper scripts, obsolete systemd units, and obsolete MoonCen drop-ins cannot survive a successful deployment.
- Service runtime settings and secrets are split under `/etc/mooncen`; `/opt/mooncen/.env` contains no credentials.
- Setup validates the public domain, aliases, and HTTPS OAuth redirect before writing Nginx or systemd configuration. The API receives the same normalized domain set as `MOONCEN_TRUSTED_HOSTS` and the CORS allowlist.
- `deploy/ubuntu/mooncen.env.example` shows the supported settings.
- `mooncen-frontend.service` serves the built frontend through the production static server.
- `mooncen-crawler.timer` schedules one complete run at 22:00 Asia/Seoul.
- `mooncen-crawler-once.service` performs the scheduled or manual one-shot run.
- The legacy long-running `mooncen-crawler.service` remains disabled so it cannot hold the global crawler lock between runs.
- `mooncen-ai-worker.service` is always running, but only processes data from 22:00 to 07:00 in `TZ=Asia/Seoul`.
- The crawler uses parallel crawlers with `CRAWLER_MAX_WORKERS=4` by default.
- If Ollama is on another host, update `OLLAMA_HOST` in `/etc/mooncen/ai.env` and `/etc/mooncen/bot.env`, then restart those services.
- Application units use private temporary/device views, read-only system/application paths, and a restrictive umask. The crawler owns `/opt/mooncen/logs`; the AI unit bind-mounts `/var/lib/mooncen-ai` over that path only inside its mount namespace. Functional-check and bot state are isolated in `/var/lib/mooncen-check` and `/var/lib/mooncen-bot`.
- Functional reports are read from `/var/lib/mooncen-check/latest.json`; the service exposes them read-only to the credential-free `mooncen` application group after every run.
- Among the API/crawler/frontend/AI/bot/applier/check units, `mooncen-ops-bot` is the only `NoNewPrivileges=false` exception because its existing diagnostics call `sudo -u postgres psql`. It has only `CAP_SETUID`, `CAP_SETGID`, and `CAP_AUDIT_WRITE` in its bounding set. Sudo permits only `/usr/local/libexec/mooncen-bot/psql`; that root-owned helper rejects every argument/query except the three fixed read-only recovery-status queries, clears the environment, and disables `psql` startup files with `-X`.
- Backup scripts are installed root-owned under `/usr/local/libexec/mooncen-backup`. The restore verification is a hardened root oneshot because it must switch to PostgreSQL's OS account; its explicit `TEST_DB=mooncen_restore_contract_test` prevents arbitrary database targets, and its private temporary directory is removed after every run.
- Root HA gates execute reviewed copies under `/usr/local/libexec/mooncen-ha`, never mutable scripts from the application tree. Their units are sandboxed and may write only `/opt/mooncen/failover`; the crawler watchdog uses only its root-owned inline unit command.
- Uvicorn's raw access log is disabled so search/filter query strings are not persisted to journald; application errors and explicit structured application logs remain enabled.
- For HTTPS, add Certbot after Nginx is working.
