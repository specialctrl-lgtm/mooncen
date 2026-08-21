# MoonCen 전체 아키텍처 — 상세본

## 문서 정보

| 항목 | 값 |
|---|---|
| 작성 기준일 | 2026-08-19 UTC |
| 분석 대상 | `/home/sgm/src/project/mooncen` 현재 워크스페이스 |
| Git 기준 | `master@8d55e873bfb06ec33f566839fce7ee98650955f8` |
| 분석 방식 | 소스·설정·SQL·테스트·배포 자산 정적 분석 |
| 실행 검증 | 전체 의존성 미설치로 전체 테스트·빌드 미실행 |
| 중요한 제약 | 저장소 선언은 실제 서버 배포 상태의 증거가 아님 |

분석 시작 시 작업 트리에는 983개의 변경 상태 항목이 있었습니다. 수정 506개, 삭제 57개, 미추적 상태 항목 420개이며 tracked diff는 562개 파일, 약 `+77,901/-51,506`이었습니다. 따라서 이 문서는 Git HEAD의 안정 릴리스가 아니라 **현재 워크스페이스 스냅샷**을 설명합니다.

이 문서에서 다음 용어를 구분합니다.

- **확인된 사실**: 코드·설정·SQL·테스트에서 직접 확인한 내용
- **선언 상태**: 토폴로지나 배포 파일이 원하는 상태로 명시한 내용
- **추론**: 여러 근거를 결합한 판단이지만 운영 환경 확인이 필요한 내용
- **목표 구조**: 구현 또는 문서화됐지만 현재 활성화 증거가 없는 구조

---

## 1. 시스템 목적과 경계

MoonCen은 유통사 문화센터, 지방자치단체, 공공기관과 교육·체험 제공처의 강좌 정보를 수집해 다음 기능을 제공합니다.

- 강좌 키워드·분류·연령·요일·시간·가격·접수 상태 검색
- 위치 기반 지점 및 강좌 탐색
- Google/Naver OAuth 기반 사용자 기능
- 찜, 수강 표시, 강좌 알림, 수정 요청과 버그 제보
- 강좌·분류·지점 SEO 페이지와 JSON-LD
- 크롤러 실행, 데이터 품질, 배포, 서비스 상태를 다루는 별도 Ops 제어면
- 모바일의 공개 검색·지도·기기 로컬 찜

시스템 외부 경계에는 제공기관 웹사이트, Cloudflare, Kakao Maps, Google/Naver OAuth, SMTP, Ollama/Gemini, NAS, 운영자 단말과 앱 스토어가 있습니다. 이 외부 서비스의 가용성, 할당량, 계정 상태와 정책은 저장소만으로 보장할 수 없습니다.

![현재 저장소가 선언하는 MoonCen 아키텍처](assets/01-current-architecture.svg)

---

## 2. 저장소 규모와 구조

### 2.1 정적 규모

| 영역 | 파일 수 | 관련 코드·설정 행 수(근사) | 비고 |
|---|---:|---:|---|
| `Crawler/` | 461 | 447,964 | 271개 핵심 모듈과 190개 생성 wrapper 포함 |
| `backend/` | 31 | 19,179 | API, 인증, Ops, 서비스 계층 |
| `DB/` | 79 | 16,871 | base schema, role, migration, staging/control |
| `frontend2/` | 132 | 19,398 | TypeScript 계열 기준; 대형 CSS 별도 |
| `ops-console/` | 54 | 13,759 | 운영자 SPA |
| `mooncen-app/` | 74 | 14,760 | Expo 모바일 |
| `ops_agent/` | 22 | 15,035 | 운영·분산 제어 worker |
| `tools/` | 125 | 63,668 | staging apply, 점검, 생성·운영 도구 |
| `tests/` | 487 | 222,004 | Python 테스트 함수 약 4,805개 |
| `deploy/` | 140 | 24,668 | Docker, Ubuntu, systemd, Nginx, monitoring, HA |
| `config/` | 45 | 629,976 | 생성·provider·target 데이터 포함 |

전체적으로 Python 파일 약 1,170개, TypeScript/TSX 167개, SQL 57개, YAML/YML 49개이며 versioned DB migration SQL은 primary/control 계열을 합쳐 40개입니다.

### 2.2 주요 복잡도 집중 지점

| 파일 | 규모/징후 | 구조적 의미 |
|---|---:|---|
| `Crawler/Crawler_MunicipalYaml.py` | 60,610행 | target, parser, transport, normalization, writer, 실행이 단일 파일에 집중 |
| `Crawler/Crawler_GeneratedYamlTargets.py` | 4,711행 | 생성 target 실행과 DB 저장 경계가 큼 |
| `run_crawlers.py` | 3,336행 | registry, subprocess, lock, batch, maintenance, 분산 task를 조립 |
| `tools/apply_staging_batch.py` | 1,980행 | 중요한 운영 데이터 반영 경계가 한 파일에 집중 |
| `backend/routers/ops_v2.py` | 4,031행 | 운영 API 도메인이 단일 router에 집중 |
| `backend/services/crawler_analytics.py` | 2,848행 | 분석 쿼리와 응답 조합의 높은 응집도 |
| `frontend2/src/App.tsx` | 2,207행, `useState` 59개 | 사용자 웹의 상태·흐름·모달이 단일 root에 집중 |
| `ops_agent/deployment_worker.py` | 2,463행 | 배포 검증·실행·상태 관리 집중 |

### 2.3 운영 산출물 분류

- 운영 사용자 웹: `frontend2`
- 운영 API: `backend`
- 운영 DB: `DB`
- 크롤러: `Crawler`, `run_crawlers.py`
- 별도 운영면: `ops-console`, `ops_agent`
- 출시 전 모바일: `mooncen-app`
- 퇴역 웹: `frontend` — 명령이 의도적으로 실패하도록 구성

루트 README의 모바일 설명은 현재 코드와 불일치합니다. README는 mock/prototype로 표현하지만 모바일은 기본값으로 `https://mooncen.kr`의 공개 API를 읽습니다. 다만 EAS project, 서명·스토어 파이프라인은 확인되지 않아 “실데이터 연동을 마친 출시 전 앱”으로 보는 것이 타당합니다.

---

## 3. 논리 아키텍처

```text
Public user
  ├─ frontend2 SPA ─┐
  ├─ SEO route ─────┼─ Cloudflare/Nginx ─ FastAPI ─ Primary PostgreSQL/PostGIS
  └─ Expo mobile ───┘                         │
                                             ├─ Google/Naver OAuth
                                             ├─ Kakao/SMTP/Cloudflare analytics
                                             └─ read-only crawler-control view

Provider sources ─ legacy runner ─ child crawlers ─ staging/control DB
                                                └─ validation + fingerprint
                                                   └─ reviewed apply ─ primary DB

Ops browser ─ separate Ops origin ─ ops-console ─ FastAPI Ops API
                                               ├─ ops tables / job queue
                                               └─ ops_agent workers

bot ─ Prometheus/Grafana ─ node/textfile metrics and alerts
cloud ─ encrypted/signed daily backup ─ NAS
```

논리적으로는 사용자 제공면, 데이터 수집면, 운영 제어면, 관측·복구면이 나뉩니다. 물리적으로는 현재 선언상 사용자 웹·API·운영 DB가 모두 `cloud`에 있어 논리 분리가 고가용성을 뜻하지는 않습니다.

---

## 4. 사용자 웹 `frontend2`

### 4.1 구성

- React 18, TypeScript, Vite
- `src/main.tsx`의 `StrictMode → App` 단일 root
- React Router 없이 History API를 감싼 커스텀 routing hook 사용
- 별도 전역 상태 라이브러리 없이 `App.tsx` 중심 로컬 상태
- API는 상대 경로 `/api/*`를 사용하므로 동일 출처 reverse proxy 전제

주요 경로는 `/branches`, `/branch-search`, `/map`, `/course/{id}/{slug}`이며 강좌 상세 URL로 직접 진입하면 API에서 데이터를 다시 읽어 모달 상태를 복원합니다.

### 4.2 데이터 흐름

검색 조건이 바뀌면 query key를 계산하고 이전 요청을 취소한 뒤 페이지별 결과를 병합합니다. 지점 지도는 현재 중심과 반경으로 `/api/branches/nearby`를 별도 조회합니다. 찜·내강좌 변경은 낙관적으로 반영하고 실패 시 원복합니다.

브라우저 위치는 첫 진입 때 요청하며 30분간 session storage에 저장합니다. 개발 모드에만 IP 기반 위치 fallback이 있습니다. Kakao Maps SDK는 동적으로 로드하며 외부 신청 URL은 HTTP(S), 자격증명 없는 URL만 허용합니다.

### 4.3 인증과 클라이언트 저장소

- OAuth 제공자: Google, Naver
- 브라우저 요청: cookie 포함
- unsafe method: `mooncen_csrf` cookie를 `X-CSRF-Token`으로 전송
- 표시용 사용자 정보: `localStorage`
- OAuth 예상 state와 최근 위치: `sessionStorage`

`AuthUser` type에는 `accessToken`, `code` 선택 필드가 있고 `storeUser`가 객체 전체를 localStorage에 직렬화합니다. 현재 서버 응답에 해당 값이 있다는 증거는 없지만 향후 응답 변화가 credential 영속화로 이어질 수 있으므로 저장 필드 allowlist가 필요합니다.

### 4.4 SEO와 품질

정적 metadata 외에 선택 강좌에 따라 canonical, OpenGraph, Twitter, Course/Event JSON-LD를 갱신합니다. CI 정의는 Vitest, Vite build, Playwright 모바일/데스크톱, axe WCAG A/AA와 가로 넘침·runtime error 검사를 포함합니다.

---

## 5. 모바일 `mooncen-app`

### 5.1 구성과 범위

- Expo SDK 57, Expo Router, React Native 0.86, React 19
- React Query: 서버 캐시
- Zustand + AsyncStorage: 검색 설정, 기기 로컬 찜·관심사
- WebView: Android/iOS Kakao Maps JavaScript SDK

홈, 검색, 지도, 보관함, 마이 탭과 강좌 상세 route를 제공합니다. 기본 API 주소는 `https://mooncen.kr`이며 다음 공개 read-only API를 사용합니다.

- `/api/courses/`
- `/api/courses/{id}`
- `/api/branches/nearby`

로그인과 서버 찜 동기화는 없습니다. 모바일 출시 요구가 서버 찜, push, 다기기 동기화를 포함한다면 native OAuth PKCE/deep-link/session 계약이 선행돼야 합니다.

### 5.2 안전과 출시 상태

API 계층은 timeout, abort, non-JSON, 빈 응답과 DTO 런타임 정규화를 다룹니다. 현재 위치 권한은 사용자 동작 시 요청합니다. 반면 지도 WebView의 `originWhitelist`가 광범위하고 navigation allowlist가 없어 Kakao 필수 origin만 허용하도록 강화할 필요가 있습니다.

CI 정의에는 typecheck, Vitest, Expo Doctor, Android/iOS/Web export와 npm audit가 있습니다. EAS project ID, `eas.json`, 스토어 서명·배포 절차는 확인되지 않았으므로 운영 앱 출시 상태라고 보기는 어렵습니다.

---

## 6. 운영 콘솔 `ops-console`

### 6.1 구조

- React 19, TypeScript, Vite
- React Router, TanStack Query/Table
- `/api/ops/session`으로 시작하는 별도 운영자 세션
- `viewer | operator | admin` 역할
- 데이터 갱신: 페이지별 polling, mutation 후 cache invalidation
- 작업·배포 상세: credential 포함 EventSource/SSE

주요 화면은 Dashboard, Services, Crawler Studio, Crawlers, Releases, Analytics, Data Quality, Content, Deployments, Jobs/Audit, Agents, Settings입니다.

API client는 12초 timeout, cookie session, CSRF header, request ID 포함 오류, 401 전역 세션 만료를 구현합니다. UI 역할 검사는 사용자 경험 통제일 뿐 보안 경계가 아니므로 모든 mutation은 서버 권한 검증과 감사로그를 통과해야 합니다.

### 6.2 배포 경계

Ops 콘솔은 일반 사용자 웹과 다른 origin을 사용합니다. public Nginx는 `/api/ops`와 Ops 로그인 경로를 404 처리하고, Ops Nginx는 허용된 로그인·Ops API·SSE만 proxy합니다. 실제 Cloudflare Access/VPN/MFA 적용 여부는 저장소만으로 확인되지 않습니다.

---

## 7. FastAPI 백엔드

### 7.1 진입점과 미들웨어

`backend/main.py`가 FastAPI app을 생성하고 router를 조립합니다. 운영에서는 Swagger/OpenAPI UI를 숨깁니다. 공통 계층은 다음을 담당합니다.

- exact CORS origin 검증, 운영 HTTPS origin 강제
- Trusted Host 검증
- request ID, 처리시간, 오류 은닉과 bounded logging
- 보안 header
- `/health`: DB schema와 선택적 crawler-control readiness
- `/live`: 프로세스 liveness

운영 직접 바인딩 기본값은 `127.0.0.1:8001`입니다.

### 7.2 API 도메인

| 도메인 | 대표 경로 | 기능 |
|---|---|---|
| 강좌 | `/api/courses/`, `/api/courses/{id}` | 복합 검색, 상세, 조회수, 갱신 요청 |
| 장소 | `/api/branches/providers`, `/api/branches/nearby` | 제공자 집계, PostGIS 반경, 동일 물리 지점 병합 |
| 인증 | `/api/auth/*` | signup/login/logout, Google/Naver OAuth, 세션·탈퇴 |
| 사용자 | `/api/users/me/*`, `/api/user-courses/*` | 수강 표시, 알림, 찜, 강좌 alert |
| SEO | `/course`, `/category`, `/branch` | 서버 HTML, JSON-LD |
| 버그 제보 | `/api/bug-reports` | rate limit, 이미지 구조 검증, SMTP |
| Ops | `/api/ops/*` | 상태, 크롤러, 품질, job/SSE, 배포, 감사, 설정 |
| Crawler Studio | `/api/ops/crawler-studio/*` | 초안·리비전·리뷰; 실행/배포는 의도적으로 제외 |
| Crawler Control | `/api/ops/crawler-control/*` | artifact, batch, worker, rollout 조회·승인 |

강좌 검색은 키워드, 분류, service group, provider, branch, PostGIS radius, 비용, 연령, 시간, 상태, 요일, 날짜와 정렬·페이지네이션을 결합합니다.

### 7.3 인증·권한

- 사용자 password: Argon2id 계열
- access token: HS256 JWT, issuer/audience/kid/token-version, 기본 1시간
- cookie: HttpOnly access cookie + CSRF double-submit cookie/header
- 운영 secret: 최소 길이와 production fail-closed 검증
- OAuth: HMAC state, nonce, redirect binding, 개인정보 동의, Google PKCE
- Ops: 별도 로그인과 viewer/operator/admin 서버 권한

인증 rate limiter는 프로세스 로컬 메모리 bucket이며 코드 자체도 edge 분산 제한을 별도 요구합니다. 따라서 Nginx/Cloudflare 설정을 실제 배포에서 검증하고, 수평 확장 시 공유 rate limiting 또는 edge 정책을 운영 기준으로 삼아야 합니다.

### 7.4 DB 연결

SQLAlchemy/psycopg2 connection pool을 사용합니다. production API는 명시적 `DB_API_USER/PASSWORD`가 필요하며 owner와 동일 계정이면 시작을 거부합니다. 원격 production/staging은 `sslmode=verify-full`과 connection/statement/lock timeout을 적용합니다. crawler-control 조회는 별도 read-only DB 연결을 사용하고 primary fallback을 금지합니다.

---

## 8. 데이터 아키텍처

### 8.1 운영 데이터 모델

| 집합 | 주요 테이블/모델 | 핵심 제약·기능 |
|---|---|---|
| 지점 | `branches` / `Branch` | `(provider, branch_code)` 고유, 주소·좌표·region·geocode 상태 |
| 강좌 | `courses` / `Course` | `(provider, provider_course_id)` 고유, 분류·기간·가격·lifecycle·AI·연령 |
| 공간 검색 | PostGIS geography | lat/lon 동기화 trigger, GIST index, 반경 검색 |
| 텍스트 검색 | search vector/trigram | 한국어 n-gram trigger, GIN/trigram/active index |
| 인증 | `users`, `oauth_accounts` | 사용자, provider identity, token version |
| 개인정보 | notice/version/acceptance | 불변 고지 버전과 사용자 동의 |
| 사용자 강좌 | marks, notification settings, favorites, alerts | 상태·알림·즐겨찾기 |
| 운영 | `ops_*` | service, job/log, deployment, audit, alert, quality, override |
| 수집·품질 | crawler run/progress/quality | 수집 이력과 course quality score |

`notifications`·`user_favorites`와 현행 `user_course_notification_settings`·`user_favorite_courses`가 함께 존재해 레거시와 현행 모델의 경계를 확인할 필요가 있습니다.

### 8.2 스테이징 DB

스테이징 schema는 운영 DB와 분리해 설치하도록 명시돼 있습니다. 다음 정보를 보유합니다.

- `crawl_batches`
- staged branch/course와 `crawl_batch_id`
- validation error와 apply log
- branch/course snapshot
- DB session marker 기반 batch 자동 기록

### 8.3 분산 crawler control DB

목표 control plane에는 다음이 구현돼 있습니다.

- release artifact와 compatibility
- batch, task, attempt, observation
- rollout desired state와 worker binding/report
- action request와 receipt consumption
- Crawler Studio draft/revision/review/approval
- quality isolation
- fenced lease와 immutable snapshot trigger

worker가 현재 lease·attempt identity를 갖지 않으면 snapshot 저장을 DB가 거부합니다. finalizer와 approver 역할도 분리되어 finalizer가 스스로 승인할 수 없습니다.

### 8.4 migration authority와 DB role

`DB/setup_db.py`가 fresh/migrate의 단일 authority입니다. SHA-256 checksum ledger, advisory lock, migration별 transaction을 사용하고 이미 적용된 파일의 checksum이 바뀌면 실패합니다. 새로운 병렬 migration framework를 추가하는 것보다 기존 authority를 보강하는 것이 안전합니다.

DB role은 owner/migrator, API, crawler staging, distributed worker, applier, AI, check, readonly, scheduler, finalizer, approver, reporter, release-admin 등으로 세분화됩니다. API는 제한된 컬럼만 수정하고 AI도 허용된 course 컬럼만 갱신합니다.

---

## 9. 크롤러 아키텍처

### 9.1 orchestration

`run_crawlers.py`는 정적 registry와 생성 registry를 병합하고 허용된 script path·argv만 subprocess로 실행합니다.

- provider 최대 512, 병렬 worker 최대 16
- provider timeout 최대 9시간
- lock과 contention exit
- progress/run report
- process tree 종료와 timeout 처리
- 실행 후 좌표·분류·종료 강좌 maintenance
- staging mode에서 batch begin/finalize
- success/partial/zero/failed 결과 분류
- 완전한 전체 수집일 때만 `collection_complete`와 close-missing 활성화

자식 환경변수는 정제하고 지도 credential을 제거합니다. 외부 HTTP는 `SafeSession`을 통해 SSRF, DNS rebinding, redirect, TLS, 응답 크기와 timeout을 제한하며 Selenium은 sandbox 해제 인자를 금지하고 executable을 검증합니다.

### 9.2 provider 구현

Homeplus, Emart, Lotte 같은 전용 crawler와 YAML/생성 target crawler가 공존합니다. 여러 crawler가 branch/course persistence를 자체 구현합니다. 스테이징 반영 경계는 중앙화됐지만 수집 측 persistence 계약은 중복되어 provider별 차이가 발생할 여지가 있습니다.

`Crawler_MunicipalYaml.py`는 60,610행이며 parser, target, transport, normalization, persistence, 실행이 집중돼 있습니다. 운영 지자체 wrapper는 검토된 allowlist만 선택하고, aggregate wrapper는 중복 provider를 제외하는 안전장치를 가집니다.

### 9.3 현재 legacy 경로

`config/production_topology.json`은 다음을 선언합니다.

- `crawlerMode: legacy`
- crawler primary: `gen1crawler`
- staging/control primary: `gen1db`
- `wtr-linux` canary worker: disabled
- `gen1crawler` distributed worker: disabled

그러나 코드에는 direct-primary와 staging write mode가 모두 있습니다. 실제 `CRAWL_WRITE_MODE`, DB endpoint, role이 배포 환경에 없어 현재 legacy 실행이 어느 쓰기 경로를 사용하는지 저장소만으로 확정할 수 없습니다.

### 9.4 스테이징 반영 경계

`tools/apply_staging_batch.py`가 운영 데이터 변경의 핵심 경계입니다.

1. staging을 read-only / repeatable-read로 읽습니다.
2. primary advisory apply lock을 획득합니다.
3. batch/provider/ownership, completeness, control approval, immutable snapshot을 검증합니다.
4. 검토한 dry-run SHA-256 fingerprint와 현재 데이터를 비교합니다.
5. 수집량 급락과 위험한 close-missing을 차단합니다.
6. branch/course upsert와 lifecycle close를 단일 primary transaction에서 처리합니다.
7. 오류가 나면 rollback하고 apply log를 남깁니다.

![현재와 목표 크롤러 데이터 흐름](assets/02-crawler-flow.svg)

### 9.5 분산 목표 구조

분산 구조는 42개 논리 provider 그룹을 약 434개 concrete task로 확장하도록 설계돼 있습니다.

`scheduler → artifact/desired state → canary worker → task/attempt/fenced lease → immutable observation → finalizer → 별도 approver → 동일 promotion gate`

release agent는 HTTPS/DNS, signature/hash/size, immutable release tree, drain/switch/health/rollback을 검증합니다. action worker는 고정 Python action만 실행하고 arbitrary command를 받지 않습니다. 다만 실제 builder/signing handoff 일부는 의도적으로 fail-closed이며 문서도 전환을 `NOT READY`로 표시합니다.

따라서 분산 구조는 **구현된 목표 기능**이지 현재 운영 경로가 아닙니다.

---

## 10. AI 데이터 보강 파이프라인

`run_ai_pipeline.py`는 운영 `courses`에서 활성·미처리 강좌를 읽고 course별 PostgreSQL advisory lock 후 결과를 반영합니다. 1~16 thread와 복수 Ollama host round-robin, 야간 active window를 지원합니다.

실제 동작을 명칭과 구분해야 합니다.

- title 분석은 LLM이 아니라 deterministic regex/rule 중심입니다.
- category와 summary도 deterministic/fallback 비중이 큽니다.
- LLM 결과에서 실질적으로 사용하는 값은 tags입니다.
- Ollama `/api/generate`, Gemini HTTPS API를 지원합니다.
- OpenAI key를 인식하지만 provider 구현은 완료되지 않았습니다.

worker singleton은 원자적 OS lock이 아닌 PID file 방식이며 retry 상태는 메모리 dict입니다. course advisory lock이 중복 row 처리는 막지만 worker 시작 경쟁과 재시작 후 retry 이력은 별도 보강이 필요합니다.

강좌 title/description/category가 외부 Gemini 또는 운영자가 지정한 Ollama endpoint로 전달될 수 있으므로 데이터 전송 허용 범위, endpoint allowlist, TLS/mTLS, 보존·장애 정책을 운영 문서에 명시해야 합니다.

---

## 11. 물리·배포 아키텍처

### 11.1 권위 토폴로지 선언

| 노드 | 선언 역할 | 상태 해석 |
|---|---|---|
| `cloud` | public frontend, backend, primary DB, AI | 사용자 트래픽 중심; DB standby는 최신 manifest에 없음 |
| `gen1crawler` | legacy crawler primary | 현재 crawler owner; distributed worker는 disabled |
| `gen1db` | staging/control primary | desired 위치; 실제 cutover 증거 필요 |
| `wtr-linux` | canary distributed worker, Ollama 후보 | worker disabled |
| `bot` | Prometheus/Grafana | private/Tailscale 관측 노드 |

### 11.2 public 경계

Ubuntu production은 systemd + Nginx + Cloudflare Tunnel을 사용하도록 구성돼 있습니다. public Nginx는 loopback origin, Cloudflare real IP 신뢰 제한, rate limit, CSP와 보안 header, API/SEO/front proxy를 설정합니다. public host의 Ops API와 Ops login은 404 처리합니다.

### 11.3 서비스 격리와 schedule

API, frontend, AI, crawler, applier, backup을 별도 OS 사용자로 실행하며 `NoNewPrivileges`, `ProtectSystem`, `ProtectHome`, `PrivateTmp`, capability 축소를 폭넓게 사용합니다.

저장소에 선언된 대표 일정은 다음과 같습니다.

- legacy crawler: 야간 22:00 KST
- staging apply: 매시간
- DB backup: 매일 03:30
- restore test: 매월
- functional smoke: 매일 08:20 전후
- Cloudflare health gate: 매분

실제 timer enable/last result는 서버에서 확인해야 합니다.

### 11.4 배포 방식

표준 경로는 exact Git commit을 archive하고 SHA를 검증한 뒤 동일 파일시스템 rename으로 release를 교체합니다. release guard는 root lock, durable journal, 중단 복구와 자동 rollback을 지원합니다. Python 의존성은 hash를 요구하고 frontend prebuilt digest를 기록합니다.

표준 배포는 dirty tree를 거부하지만 Ops deployment worker는 검토된 dirty snapshot을 synthetic commit으로 패키징할 수 있습니다. 이는 불변성을 만들지만 GitHub branch CI를 통과한 commit과 같다는 보장은 없습니다. production은 clean, reviewed, signed CI artifact로 제한하는 것이 바람직합니다.

### 11.5 토폴로지 문서 충돌

최신 문서는 `n100`이 퇴역하고 `cloud/gen1crawler/gen1db`가 권위 토폴로지라고 설명합니다. 반면 다음 과거 문서는 `n100`을 current standby/crawler 또는 failover 대상으로 서술합니다.

- `deploy/ha/README.md`
- `deploy/ha/CLOUD_PRIMARY_N100_STANDBY.md`
- 일부 Cloudflare failover 문서
- `DB/staging_schema.sql`의 과거 설명

이 충돌은 장애 대응자가 잘못된 노드를 승격하거나 tunnel을 전환할 수 있는 운영 안전 문제입니다. 정합성 복구 전 과거 failover 명령은 실행 금지 대상으로 취급해야 합니다.

---

## 12. 관측성

### 12.1 현재 자산

- `bot`의 Prometheus, Grafana, 선택적 Uptime Kuma
- 15초 scrape, 30일 Prometheus retention 선언
- node exporter와 atomic textfile collector
- 활성 target: `bot`, `cloud`, `gen1crawler`
- pending target: `gen1db`, `wtr-linux` 등
- alert: target down, CPU/memory/disk 90%, stale textfile, provenance, DB role, crawler cycle/freshness, scheduler/apply, API/frontend unit
- Telegram contact point
- 매일 functional smoke

### 12.2 현재 간극

FastAPI runtime metrics는 process별 최대 20,000개 메모리 deque이며 재시작 시 사라집니다. API worker가 둘이면 전체 서비스 집계도 아닙니다. 중앙 Prometheus exporter가 아니므로 fleet 단위 request rate/error/latency를 보장하지 않습니다.

다음 신호는 명확히 보강할 가치가 있습니다.

- API RED: request rate, error, p50/p95/p99
- DB: pool, connection, lock, slow query, statement timeout
- crawler: batch freshness, partial/zero, queue lease, dead task, apply failure/close block
- AI: backlog, provider latency/error, retry
- backup/restore test freshness와 실패
- TLS 만료와 외부 blackbox synthetic journey
- monitoring 자체 dead-man
- 웹·Ops·모바일 client error/crash/ANR

---

## 13. 백업과 복구

### 13.1 확인된 설계

- 전용 nologin backup 계정과 root-owned 환경/known_hosts
- 일일 PostgreSQL dump와 앱/config 참조 archive
- `age` 암호화
- Ed25519 서명 manifest, SHA-256, 크기 검증
- 기본 35일 보존
- 월간 restore test timer
- candidate DB 복원 → 검증 → 서비스 정지 → DB name swap → health → 실패 시 rollback

### 13.2 복구 위험

- 승인된 RPO/RTO와 실측 drill 시간이 없습니다.
- PITR/WAL 연속 복구 증거가 없어 일일 dump만 보면 최악 RPO가 약 24시간일 수 있습니다.
- 저장소상 목적지는 단일 NAS이며 immutable/offsite 두 번째 사본이 확인되지 않습니다.
- application, secret, DNS/tunnel까지 신규 host에 복구하는 full-stack DR drill은 확인되지 않습니다.
- encryption/signing/runtime secret의 외부 escrow 완료 여부는 확인되지 않습니다.
- backup·restore 실패와 중앙 alert 연계가 불명확합니다.

---

## 14. 보안과 신뢰 경계

| 경계 | 보호 장치 | 남은 확인/위험 |
|---|---|---|
| 인터넷 → public | Cloudflare/Nginx, exact host/CORS, HTTPS, rate limit, CSP | edge 정책 실제 배포, fleet 단위 제한 |
| browser → API | HttpOnly JWT, CSRF, OAuth state/PKCE | web localStorage allowlist, client telemetry |
| Ops browser → Ops API | 별도 origin, cookie/CSRF, RBAC, audit | Cloudflare Access/VPN/MFA 실제 상태, destructive action 재승인 |
| API → primary DB | owner 분리, 최소 권한, TLS/timeout | 실제 runtime role/endpoint 검증 |
| API → control DB | 별도 read-only, primary fallback 금지 | 실제 cutover 위치 |
| crawler → 외부 | SafeSession, TLS/redirect/DNS/size/timeout | provider 약관·rate·변경 탐지 |
| worker → staging | lease/attempt marker, DB fencing | 분산 worker 현재 비활성 |
| staging → primary | read-only snapshot, fingerprint, ownership, lock, transaction | 실제 legacy write mode |
| AI → provider | timeout, 입력 제한 | endpoint allowlist, 전송·보존 정책 |
| release → worker | signature/hash/size, immutable tree, atomic switch | builder/sign handoff 일부 미완료 |
| backup → NAS | encryption/signature/strict host key | offsite immutability, key escrow, 성공 alert |

Ops audit는 secret/token/password류 key를 마스킹하고 job parameter에 secret을 담는 것을 거부합니다. proxy client IP도 직접 peer가 loopback일 때만 신뢰합니다.

---

## 15. CI, 테스트와 공급망

현재 작업 트리의 CI 정의는 다음을 포함합니다.

- Gitleaks 전체 이력과 synthetic canary
- Python 3.12/3.13 + PostGIS
- migration 두 번 적용, compileall, Ruff, pytest, pip-audit
- frontend2 lint/test/build/Playwright/axe/npm audit
- ops-console lint/test/build/npm audit
- 퇴역 frontend가 build되면 실패하는 guard
- Expo typecheck/test/doctor/all-platform export/audit
- deploy shell/PowerShell/Nginx syntax 검증

테스트 자산은 방대하지만 현재 CI 파일 자체가 변경 상태이고 일부 workflow와 운영 설정이 미추적이므로 GitHub가 이 정확한 정의를 실행한다고 단정할 수 없습니다. 현 환경에는 `pytest`, Node/npm과 전체 Python 의존성이 없어 실행 결과는 검증하지 못했습니다.

보강 후보는 systemd verify, promtool, 모든 Nginx/Grafana provisioning 검증, 운영 snapshot migration rehearsal, coverage trend, SBOM, artifact/image signing과 provenance, container CVE scan입니다.

---

## 16. 핵심 런타임 시퀀스

### 16.1 강좌 검색

1. 사용자가 웹/모바일에서 필터를 설정합니다.
2. client가 `/api/courses/` query를 생성합니다.
3. Nginx가 FastAPI로 전달합니다.
4. API가 텍스트·상태·기간·PostGIS 조건을 조합합니다.
5. SQLAlchemy가 제한된 API role로 primary DB를 조회합니다.
6. client가 pagination 결과와 상세 데이터를 표시합니다.

### 16.2 OAuth

1. 개인정보 고지/동의를 확인합니다.
2. 서버가 HMAC state와 redirect binding을 발급합니다.
3. Google은 PKCE를 추가 사용하고 제공자 token/profile API를 호출합니다.
4. 서버가 검증된 identity를 local user/OAuth account와 연결합니다.
5. HttpOnly access cookie와 CSRF cookie를 발급합니다.
6. 브라우저는 표시용 user data만 저장해야 합니다.

### 16.3 안전한 crawler 반영

1. runner가 검토된 registry에서 provider subprocess를 시작합니다.
2. crawler가 batch/lease 문맥으로 staging row를 저장합니다.
3. batch가 success/partial/zero/failed와 completeness를 기록합니다.
4. validation과 control gate가 소유권·snapshot을 확인합니다.
5. dry-run이 fingerprint와 변경 요약을 생성합니다.
6. 운영자가 검토한 fingerprint를 pinned apply에 전달합니다.
7. apply가 fingerprint 불일치·급락·ownership 오류 시 중단합니다.
8. 통과한 provider만 primary transaction으로 upsert/close합니다.

### 16.4 운영 job

1. Ops UI가 role과 CSRF를 포함해 mutation을 요청합니다.
2. 서버가 권한·환경·parameter를 검증하고 audit/job을 생성합니다.
3. worker가 고정 registry의 작업만 claim합니다.
4. progress/log가 DB와 SSE로 UI에 전달됩니다.
5. 결과·request ID·배포 provenance가 감사 근거로 남습니다.

---

## 17. 구조적 판단

### 강점

MoonCen은 데이터 반영 경계와 운영 안전에 상당한 투자가 돼 있습니다. 최소 권한 DB role, fail-closed 환경 검증, immutable/fenced evidence, fingerprint promotion, release guard, 암호화·서명 backup은 일반적인 크롤링 서비스보다 높은 수준의 기반입니다.

### 핵심 위험

가장 큰 위험은 개별 기능 부족보다 **운영 권위의 불명확성**입니다.

1. 현재 workspace와 배포 revision의 관계가 불명확합니다.
2. 선언 토폴로지와 과거 HA 문서가 충돌합니다.
3. legacy crawler의 실제 write mode가 불명확합니다.
4. 분산 기능이 많지만 활성화 조건은 충족되지 않았습니다.
5. 최신 manifest 기준 production DB standby가 없습니다.
6. application-level 관측이 process-local이며 외부 복구 증거가 부족합니다.

즉, 다음 단계는 새 기능 확대보다 “어떤 commit·topology·쓰기 경로·복구 근거가 운영 권위인지”를 하나의 검증 가능한 baseline으로 고정하는 것입니다.

---

## 18. 주요 근거 파일

| 주제 | 근거 |
|---|---|
| 전체 소개·분류 | `README.md`, `docs/repository-layout.md` |
| backend 진입·보안·health | `backend/main.py`, `backend/readiness.py` |
| 인증 | `backend/routers/auth.py` |
| 강좌·지점 API | `backend/routers/courses.py`, `backend/routers/locations.py` |
| 사용자 기능 | `backend/routers/user_courses.py` |
| Ops API | `backend/routers/ops_v2.py`, `backend/ops/service.py` |
| ORM | `backend/models.py`, `backend/ops_models.py` |
| DB base/auth/role | `DB/schema.sql`, `DB/auth_schema.sql`, `DB/roles_body.sql` |
| migration authority | `DB/setup_db.py`, `DB/migrations/`, `DB/crawler_control_migrations/` |
| staging/control | `DB/staging_schema.sql`, `DB/staging_control_plane.sql` |
| crawler orchestration | `run_crawlers.py` |
| crawler 구현 | `Crawler/`, `config/production_crawler_providers.yaml` |
| staging apply | `tools/apply_staging_batch.py` |
| AI | `run_ai_pipeline.py`, `ai_processor.py` |
| 사용자 웹 | `frontend2/src/App.tsx`, `frontend2/src/api.ts`, `frontend2/src/auth.ts` |
| Ops UI | `ops-console/src/App.tsx`, `ops-console/src/api.ts`, `ops-console/src/pages/` |
| 모바일 | `mooncen-app/src/api/mooncenApi.ts`, `mooncen-app/app/` |
| topology | `config/production_topology.json`, `docs/multi-server-deployment.md` |
| 분산 전환 상태 | `docs/distributed-crawler-control-plane.md`, `ops_agent/` |
| public/Ops proxy | `deploy/ubuntu/nginx/mooncen.conf`, `deploy/ops-console/nginx/` |
| systemd·schedule | `deploy/ubuntu/systemd/` |
| monitoring | `deploy/monitoring/` |
| backup/restore | `docs/synology-backup-restore.md`, `deploy/ubuntu/` backup/restore assets |
| CI | `.github/workflows/ci.yml` |
| HA 문서 충돌 | `deploy/ha/README.md`, `deploy/ha/CLOUD_PRIMARY_N100_STANDBY.md` |

---

## 19. 분석 한계와 후속 검증

이 문서만으로 다음을 확정할 수 없습니다.

- 실제 production commit/tree SHA
- 실제 host inventory, unit 활성 상태와 환경변수
- 실제 `CRAWL_WRITE_MODE`
- `gen1db` cutover 완료 여부
- Cloudflare Access, MFA, DNS/tunnel 정책
- 최근 backup/restore/functional smoke 성공 여부
- production standby, replication, PITR 상태
- OAuth·SMTP·Kakao·Gemini 계정과 quota
- 모바일 스토어 계정·서명·출시 상태

운영 가이드의 “최초 기준선 확인” 절차로 위 항목을 증거화한 뒤 이 문서의 선언 상태와 대조해야 합니다.

