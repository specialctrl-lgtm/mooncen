# MoonCen (문센) 🌙

전국 문화센터·공공 교육 강좌를 수집하고, 지도·검색·관심 강좌·알림으로 연결하는 통합 탐색 서비스입니다.

별도 관리자 앱과 단계별 운영 전환 방법은
[`docs/ops-console.md`](docs/ops-console.md)에 정리되어 있습니다. 일반
사용자용 `frontend2`에는 운영 콘솔 코드를 포함하지 않습니다.

## 현재 구성

- `frontend2/`: 운영 기준 React 18 + TypeScript + Vite 웹 앱
- `backend/`: FastAPI API, 인증/OAuth, 강좌·지점·운영 API
- `DB/`: PostgreSQL/PostGIS 스키마와 순차 마이그레이션
- `Crawler/`, `config/`: 유통사·공공기관 크롤러와 수집 대상 정의
- `ops-console/`, `ops_agent/`: 인증된 운영 UI와 제한된 작업·배포 제어면
- `mooncen-app/`: Expo Router 기반 모바일 UI 프로토타입(현재 mock catalog 사용)
- `frontend/`: Google Maps 재도입을 막기 위해 의도적으로 빌드 불가 처리한 레거시 계약
- `tools/`, `deploy/`: 유지보수, 모니터링, 백업, Ubuntu/HA 운영 도구
- `tests/`: 백엔드·DB·보안 회귀 테스트

재현 가능한 CI 기준 버전은 Python 3.12/3.13, Node.js 22, PostgreSQL 16 + PostGIS입니다. Ubuntu 운영 설치기는 서명과 해시를 검증한 Node.js 24.18.0을 사용합니다.

## 로컬 개발

사전 준비: Python 3.12 또는 3.13, Node.js 22 이상, PostgreSQL/PostGIS.

### 백엔드와 DB

```bash
python -m venv .venv
# 가상환경을 활성화한 뒤 실행
python -m pip install --require-hashes -r requirements-dev.lock
python DB/setup_db.py --mode migrate
python -m pytest -q
python -m uvicorn backend.main:app --reload --port 8001
```

DB 연결에는 `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`가 필요합니다. 운영에서는 마이그레이션 계정과 API 런타임 계정을 분리하고, 민감한 값은 저장소 밖의 비밀 저장소에서 주입합니다.

### 기본 웹 앱

```bash
cd frontend2
npm ci
npm run check
npm run dev
```

기본 개발 주소는 운영 기준 웹 앱 `http://localhost:5174`, API `http://localhost:8001`입니다. 레거시 웹 앱은 별도로 실행할 때 Vite 기본 포트 `5173`을 사용합니다.
다른 기기에서 개발 서버에 접근해야 할 때만 `VITE_DEV_HOST=0.0.0.0`과 쉼표로 구분한 `VITE_ALLOWED_HOSTS`를 명시합니다.

### Expo 모바일 프로토타입

```bash
cd mooncen-app
npm ci
npm run typecheck
npm run doctor
npx expo start
```

이 디렉터리는 화면·탐색 흐름 검증용이며 아직 운영 API·인증과 연결된 배포 대상 앱이 아닙니다.

## 검증과 CI

[GitHub Actions CI](.github/workflows/ci.yml)는 다음 검사를 PR·push마다 수행합니다.

- 해시 잠금된 Python 설치, PostGIS 마이그레이션, 전체 `pytest`, `pip-audit`
- `frontend2` lint/test/build/npm audit
- `ops-console` lint/test/build/npm audit
- 레거시 `frontend`가 다시 빌드되거나 Google Maps를 활성화하지 않는지 검증
- Expo 프로토타입 typecheck/Doctor/web export/npm audit
- 배포 셸 스크립트의 구문·ShellCheck 오류 차단/경고 보고와 active/standby Nginx 설정 검사

CI DB와 자격 증명은 작업이 끝나면 폐기되는 테스트 전용 값이며 운영 비밀을 사용하지 않습니다. 브랜치 보호에서 모든 CI job을 필수 검사로 지정하는 것을 권장합니다.

## 운영 원칙

- 비밀, 개인 키, DB 덤프, 운영 호스트 정보를 커밋·문서·빌드 산출물에 넣지 않습니다.
- 운영은 `ENVIRONMENT=production`, 충분히 긴 고유 `AUTH_SECRET`, 명시적 CORS/OAuth redirect/admin allowlist를 사용합니다.
- 스키마는 `python DB/setup_db.py --mode migrate`로만 전진 적용하고, 적용된 버전 마이그레이션 파일은 수정하지 않습니다.
- 배포 전 백업과 복원 검증, 배포 후 `/health`와 핵심 사용자 흐름을 확인합니다.

상세 체크리스트와 외부 secret rotation 절차는 [운영 보안 가이드](docs/operations-security.md)를 따릅니다.

## 관련 문서

- [저장소 구조와 유지 범위](docs/repository-layout.md)
- [DB 설치와 마이그레이션](DB/README.md)
- [운영 DB를 개발 DB로 안전하게 동기화](docs/production-to-development-sync.md)
- [API 라우팅](docs/api-routing.md)
- [OAuth 로그인](docs/oauth-login.md)
- [기능 테스트](docs/functional-testing.md)
- [Ubuntu 배포](deploy/ubuntu/DEPLOY.md)
- [고가용성 운영](deploy/ha/README.md)

## 라이선스

개인 프로젝트입니다.
