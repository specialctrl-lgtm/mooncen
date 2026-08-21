# Standalone Ops Console deployment

The Ops Console is a separate web deployment. Its public site is not mounted
under `mooncen.kr`, and the normal MoonCen release scripts intentionally do not
install or start it.

## Build artifact

Build from `ops-console/` with the standalone root base:

```bash
npm ci
VITE_OPS_BASE_PATH=/ VITE_API_BASE_URL= npm run build
```

Publish only the contents of `ops-console/dist/` to a reviewed release
directory such as `/opt/mooncen-ops-console/releases/<commit>/`, then point
`/opt/mooncen-ops-console/current` at that release. Do not copy `frontend2`
assets into this directory.

The example Nginx server in `nginx/ops-console.conf.example` serves the
standalone SPA and exposes only the Ops API plus its dedicated login/logout
endpoints. Put an identity-aware access layer or private-network policy in
front of this origin.

## Required API configuration

For `https://ops.mooncen.kr/`:

```env
MOONCEN_TRUSTED_HOSTS=mooncen.kr,ops.mooncen.kr
MOONCEN_OPS_SINGLE_ACCOUNT_ONLY=true
MOONCEN_OPS_LOGIN_ID=opsadmin
MOONCEN_OPS_PASSWORD_HASH=pbkdf2_sha256$600000$<salt>$<digest>
```

The hash is PBKDF2-HMAC-SHA256, not a single fast SHA-256 digest. Enter an
existing password of at least 8 characters without terminal echo and confirm
it:

```bash
python tools/generate_ops_password.py
```

For a Windows production deploy, copy the ID and hash to the untracked
`deploy.local.ps1` as `$MoonCenOpsLoginId` and `$MoonCenOpsPasswordHash`; the
deployer writes them only to the API environment and its protected secret
store. To create a new random 256-bit password instead, run
`python tools/generate_ops_password.py --generate` and retain the printed
password in a password manager.

Because the Ops host reverse-proxies `/api`, browser requests and cookies
remain same-origin; do not broaden the cookie domain to `.mooncen.kr`.

When the API is not colocated, send `/api` to a private authenticated upstream
and retain the original `Host: ops.mooncen.kr`. Add that host to
`MOONCEN_TRUSTED_HOSTS`.
