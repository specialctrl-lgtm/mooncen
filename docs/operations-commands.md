# MoonCen 운영 주요 명령어

## 로컬 Ops Console

Ops Console은 공개 MoonCen 웹사이트와 분리된 `ops-console/` 애플리케이션입니다.
개발 PC의 기본 런처는 API, 콘솔, status agent, deployment worker만 실행합니다.
복원된 개발 DB에 Ops 스키마가 없으면 필요한 immutable migration만 먼저
적용합니다. 로컬 crawler scheduler/worker와 quality worker는 격리된 개발
검증에서만 `-EnableLocalCrawlerRuntime`으로 명시해 실행합니다.

```powershell
cd C:\project\mooncen
.\start_ops_console.ps1
# 격리된 로컬 수집 개발에서만 사용
.\start_ops_console.ps1 -DataSource Local -EnableLocalCrawlerRuntime
```

```powershell
.\start_ops_console.ps1 -Action Status
.\start_ops_console.ps1 -Action Restart
.\start_ops_console.ps1 -Action Stop
```

브라우저에서 `http://127.0.0.1:5175/`를 엽니다. 로그인 계정은
`MOONCEN_OPS_LOGIN_ID`와 `MOONCEN_OPS_PASSWORD_HASH`로만 관리합니다.
구형 8765 콘솔과 일회성 access token 방식은 지원하지 않습니다.

## 운영 서버 상태

프로젝트 루트에서 서버 상태를 조회합니다.

```powershell
.\deploy_mooncen.ps1 summary
.\deploy_mooncen.ps1 status
.\deploy_mooncen.ps1 health
.\deploy_mooncen.ps1 doctor
```

서비스 로그와 재시작 등 고정 작업은 서버에서 `mooncenctl`을 사용합니다.

```bash
mooncenctl summary
mooncenctl status
mooncenctl health
mooncenctl doctor
mooncenctl logs api
```

## 크롤러와 품질

지점 분리 후보를 개발 DB 기준으로 점검합니다.

```powershell
python -X utf8 tools\report_branch_split_candidates.py --min-active 20 --limit 200 --write
```

운영 서버에서는 서비스 계정으로 실행합니다.

```bash
cd /opt/mooncen
sudo -u mooncen /opt/mooncen/.venv/bin/python -X utf8 \
  tools/report_branch_split_candidates.py --min-active 20 --limit 200 --write
```

Ops Console의 크롤러 실행과 품질 평가는 PostgreSQL `ops_` 작업 큐와 감사
로그를 사용합니다. 관련 스키마가 없는 개발 DB는 런처가 다음 검증 도구로
갱신합니다.

```powershell
python tools\ensure_ops_console_schema.py
```

자세한 구성과 권한 모델은 [Ops Console](ops-console.md)을 참고합니다.
