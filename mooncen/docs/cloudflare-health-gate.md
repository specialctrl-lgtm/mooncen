# Cloudflare Health Gate

Cloudflare tunnel이 살아있으면 서버 내부 서비스가 죽어도 외부 접속이 계속 들어올 수 있습니다.
`mooncen-cloudflare-gate.timer`는 로컬 서비스 상태를 1분마다 확인하고, 비정상이 연속으로 감지되면 `cloudflared`를 중지해서 외부 접속을 막습니다.

## 기본 검사 대상

```text
postgresql
local PostgreSQL connection to DB_NAME
nginx
mooncen-api
mooncen-frontend
http://127.0.0.1:8001/health
http://127.0.0.1/health
http://127.0.0.1:5173
```

## 설치/활성화

```bash
cd /opt/mooncen
sudo cp deploy/ubuntu/systemd/mooncen-cloudflare-gate.service /etc/systemd/system/
sudo cp deploy/ubuntu/systemd/mooncen-cloudflare-gate.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mooncen-cloudflare-gate.timer
```

Cloud active 서버에는 배포 후 자동으로 timer가 켜집니다.
n100 standby에서는 켜지지 않고, n100이 failover로 승격될 때 자동으로 켜집니다.

## 상태 확인

```bash
mooncenctl cloudflare-gate-status
```

## 임시 비활성화

```bash
mooncenctl cloudflare-gate-disable
```

## 다시 활성화

```bash
mooncenctl cloudflare-gate-enable
```

## 설정

설정 파일:

```bash
sudo nano /opt/mooncen/failover/failover.env
```

주요 설정:

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

## 동작 방식

1. 로컬 서비스와 HTTP health를 검사합니다.
2. 실패가 `CLOUDFLARE_GATE_FAIL_THRESHOLD`회 연속 발생하면 `cloudflared.service`를 중지합니다.
3. 정상 상태가 `CLOUDFLARE_GATE_RECOVER_THRESHOLD`회 연속 확인되면 `cloudflared.service`가 enabled 상태일 때만 다시 시작합니다.
4. `/opt/mooncen/failover/disable_cloudflare_gate` 파일이 있으면 아무 작업도 하지 않습니다.
