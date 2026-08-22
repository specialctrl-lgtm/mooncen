# OAuth Login

Updated: 2026-06-05

## Providers

MoonCen supports login and auto-signup through:

- Google
- Naver

Email/password signup is not part of the current user flow.

## Google Login

Frontend starts Google OAuth with an authorization code request:

```text
https://accounts.google.com/o/oauth2/v2/auth
```

The callback returns to:

```text
https://mooncen.kr/
```

The frontend sends the callback `code`, `state`, and `redirect_uri` to:

```text
POST /api/auth/oauth/google
```

The backend exchanges the code with Google and then requests the user profile. This keeps the Google client secret on the server.

Required settings:

```bash
VITE_GOOGLE_OAUTH_CLIENT_ID=
GOOGLE_OAUTH_CLIENT_ID=
GOOGLE_OAUTH_CLIENT_SECRET=
VITE_OAUTH_REDIRECT_URI=https://mooncen.kr/
```

Google Console redirect URI:

```text
https://mooncen.kr/
```

For local development:

```text
http://localhost:5174/
```

If using a different local host or port, either add that exact URL to Google Console or set:

```bash
VITE_OAUTH_REDIRECT_URI=http://localhost:5174/
```

## Naver Login

Frontend starts Naver OAuth with an authorization code request. The backend exchanges the code with Naver.

Required settings:

```bash
VITE_NAVER_OAUTH_CLIENT_ID=
NAVER_OAUTH_CLIENT_ID=
NAVER_OAUTH_CLIENT_SECRET=

Note:

- Frontend can start OAuth with `VITE_GOOGLE_OAUTH_CLIENT_ID` / `VITE_NAVER_OAUTH_CLIENT_ID`.
- If the Vite variables are missing, frontend falls back to `/api/auth/oauth/config`.
- `/api/auth/oauth/config` exposes only public OAuth client IDs from backend env:
  - `GOOGLE_OAUTH_CLIENT_ID`
  - `NAVER_OAUTH_CLIENT_ID`
- Backend token exchange still requires secrets:
  - `GOOGLE_OAUTH_CLIENT_SECRET`
  - `NAVER_OAUTH_CLIENT_SECRET`
```

Naver callback URL:

```text
https://mooncen.kr/
```

For local development:

```text
http://localhost:5174/
```

## Deploy Config

Set these values in `deploy.local.ps1`:

```powershell
$MoonCenGoogleOAuthClientId = ""
$MoonCenGoogleOAuthClientSecret = ""
$MoonCenNaverOAuthClientId = ""
$MoonCenNaverOAuthClientSecret = ""
```

If OAuth values are omitted during deployment, `deploy_from_windows.ps1` tries to reuse existing values from `/opt/mooncen/.env`.
