# MoonCen 운영 보안 가이드

이 문서는 MoonCen의 배포·운영·장애 대응에 필요한 최소 보안 기준을 정의합니다. 실제 비밀값, 개인 장비 경로, 운영 호스트 식별자, 접근 토큰은 이 문서나 저장소에 기록하지 않습니다.

## 1. 보안 경계와 책임

| 영역 | 주요 자산 | 필수 통제 |
|---|---|---|
| Edge/Nginx | 공개 HTTP(S), 정적 파일, API 프록시 | TLS, 보안 헤더, 요청 크기 제한, rate limit, active/standby 분리 |
| FastAPI | 계정, 토큰, OAuth, 강좌·운영 API | 입력 검증, 최소 권한, 인증/인가, CORS allowlist, 감사 로그 |
| PostgreSQL/PostGIS | 사용자·강좌·수집 상태 | 네트워크 제한, 역할 분리, 암호화 연결, 백업·복원 검증 |
| Crawler/AI | 외부 페이지와 API, 수집 데이터 | egress 제한, TLS 검증, 타임아웃, staging 검증, API key 최소 권한 |
| Web/Expo | 사용자 세션, 공개 설정 | 비밀 미포함, 의존성 잠금, CSP, 안전한 OAuth state 흐름 |
| 운영/모니터링 | 로그, 메트릭, 배포·백업 권한 | 사설 접근, 강한 인증, 로그 마스킹, 명령 allowlist |

운영자, 배포 자동화, API, 크롤러, 백업은 가능한 한 서로 다른 계정과 자격 증명을 사용합니다. 한 구성요소의 탈취가 전체 시스템 권한으로 이어지지 않도록 DB 역할, 외부 API scope, SSH 권한을 각각 최소화합니다.

## 2. 비밀과 설정 관리

### 저장 원칙

- 운영 비밀은 배포 플랫폼 또는 별도 secret manager에서 런타임에 주입합니다.
- 비밀, 개인 키, 인증 쿠키, DB 덤프는 Git, 문서, 이슈, 로그, 컨테이너 이미지, 프런트 번들, CI artifact에 넣지 않습니다.
- 로그에는 Authorization/Cookie 헤더, OAuth code/state, 비밀번호, 전체 토큰을 남기지 않습니다.
- Nginx 원본 access log는 query string과 Referer를 기록하지 않습니다. Cloudflare edge log에서도 OAuth code/state가 포함된 query를 비활성화하거나 마스킹합니다.
- Cloudflare Tunnel 토큰은 표준 입력으로 root-owned 설치 helper에 전달하고, 서비스에는 전용 `--token-file`만 제공합니다. 토큰을 명령행 인자나 일반 환경 파일에 넣지 않습니다.
- 테스트는 운영 비밀을 복제하지 않고 폐기 가능한 전용 자격 증명을 사용합니다.
- 비밀은 용도와 환경별로 분리하고, 소유자·발급일·마지막 회전일·다음 회전일만 별도 자산대장에 기록합니다.

### 운영 필수 설정

값은 이 문서에 기록하지 않습니다. 배포 환경에는 필요한 항목만 제공합니다.

- 런타임: `ENVIRONMENT`, `AUTH_SECRET`, `AUTH_KEY_ID`
- DB: `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_API_USER`, `DB_API_PASSWORD`, `DB_SSLMODE`
- DB 관리/수집: `DB_USER`, `DB_PASSWORD`, `DB_USE_MIGRATOR`와 별도 crawler/staging 역할 자격 증명
- 요청 경계: `MOONCEN_CORS_ORIGINS`, 필요 시 `MOONCEN_CORS_ORIGIN_REGEX`
- OAuth: provider별 client ID/secret, `OAUTH_REDIRECT_URIS`, `OAUTH_ALLOW_LEGACY_STATE`
- 관리자: 검증된 OAuth 이메일용 `MOONCEN_ADMIN_EMAILS`, 불변 공급자 식별자용 `MOONCEN_ADMIN_PROVIDER_IDS` (`provider:id` 형식)
- 선택 기능: 지도, AI, 메시징, 터널 등 외부 서비스별 키와 토큰

운영의 `AUTH_SECRET`은 예측 불가능한 고유 값이어야 하며 32자 이상이어야 합니다. 개발 기본값이나 CI 전용 값을 운영에 재사용하지 않습니다. `MOONCEN_CORS_ORIGINS`, OAuth redirect, 관리자 allowlist는 와일드카드 대신 정확한 값으로 제한합니다.

## 3. 인증·OAuth·관리 기능

- OAuth 시작 전에 클라이언트는 `/api/auth/oauth/state`에서 짧은 수명의 서명된 state를 발급받아야 합니다.
- 운영에서는 `OAUTH_ALLOW_LEGACY_STATE=false`를 유지하고, 허용된 redirect URI만 사용합니다.
- OAuth provider callback 오류에 code, state, provider 응답 원문을 노출하거나 기록하지 않습니다.
- 관리자 권한은 명시적인 사용자 ID 또는 검증된 이메일 allowlist로만 부여합니다. allowlist가 비어 있거나 잘못된 배포는 관리자 기능을 열지 않습니다.
- 로그아웃, 계정 잠금, 침해 대응 시 기존 세션을 무효화할 수 있도록 token version 또는 signing key 교체 절차를 유지합니다.
- 운영·모니터링 UI는 인터넷에 직접 공개하지 않습니다. 사설망/VPN 또는 별도 인증 프록시 뒤에 두고, 실행 가능한 작업은 서버측 allowlist로 제한합니다.

## 4. DB와 데이터 안전

- 스키마 변경은 `python DB/setup_db.py --mode migrate`로 수행합니다. 적용된 `DB/migrations/` 파일은 수정하지 않고 새 버전을 추가합니다.
- migration 역할은 DDL 권한을 가지되 API 역할은 필요한 테이블/동작에만 권한을 가집니다. 자세한 기준은 [DB 역할 분리](../DB/ROLE_SEPARATION.md)를 따릅니다.
- 운영 DB는 공개 인터넷에서 직접 접근할 수 없게 하고, 허용된 애플리케이션·관리 네트워크만 열어 둡니다.
- 원격 DB 연결은 인증서 검증이 포함된 TLS를 사용합니다. 검증 비활성화는 일시적인 장애 분석에도 운영 기본값으로 저장하지 않습니다.
- 수집 데이터는 staging에서 무결성·감소율·중복·URL 검사를 통과한 뒤 primary에 반영합니다.
- 백업은 암호화하고 접근 계정을 분리합니다. 보관 정책에 따라 자동 삭제하되, 정기 복원 테스트 결과와 RPO/RTO 충족 여부를 남깁니다.

## 5. 외부 요청과 크롤러

- HTTP 클라이언트는 TLS 인증서를 검증하고 연결/읽기 타임아웃, 응답 크기 제한, redirect 횟수 제한을 둡니다.
- 사용자 또는 외부 데이터에서 파생한 URL은 scheme, host, 해석된 IP를 검사합니다. redirect와 DNS 재해석 뒤에도 사설·loopback·link-local·metadata 대역 접근을 차단합니다.
- 크롤러와 AI worker의 egress는 필요한 목적지와 포트로 제한합니다.
- provider별 동시성·재시도·backoff를 적용하고, 원문 응답이나 오류에 포함된 개인정보·토큰은 로그에서 제거합니다.
- 수집 실패가 기존 정상 데이터를 대량 종료하지 않도록 staging promotion guard와 실패 임계치를 유지합니다.

## 6. CI와 변경 승인

[CI workflow](../.github/workflows/ci.yml)는 운영 비밀 없이 다음을 검증합니다.

- Python 해시 잠금 설치, PostGIS migration, `pytest`, `pip-audit`
- 기본 웹 앱 lint/test/build/npm audit
- 레거시 웹 앱 lint/build/npm audit
- Expo typecheck/Doctor/export/npm audit
- Android lint/debug APK 빌드와 Gradle wrapper 검증
- 전체 Git history와 checkout 파일의 Gitleaks secret scan
- 배포 셸 구문/ShellCheck 오류 차단·경고 보고와 active/standby Nginx 설정

기본 브랜치는 모든 CI job 성공, 최신 기준 브랜치 반영, 최소 1인 검토를 merge 조건으로 설정합니다. workflow 권한은 기본 `contents: read`로 유지하고, fork PR에는 쓰기 토큰이나 운영 환경 접근을 제공하지 않습니다. 배포 권한이 필요한 workflow는 CI와 분리하고 보호된 environment 승인을 사용합니다.

의존성을 변경할 때 Python lock의 해시와 각 npm lock을 함께 갱신합니다. audit 예외는 취약점 ID, 영향 분석, 보완 통제, 담당자, 만료일이 있는 경우에만 허용합니다.

## 7. 배포 체크리스트

### 배포 전

- [ ] 모든 필수 CI job이 성공했다.
- [ ] DB migration의 lock, 소요 시간, 하위 호환성, 재실행 안전성을 검토했다.
- [ ] 최근 백업이 있고 격리 환경의 복원 검증이 유효하다.
- [ ] 배포 artifact에 비밀, 개인 키, 덤프, 개발 설정, 불필요한 로그가 없다.
- [ ] 운영 설정의 필수 이름과 allowlist를 검증했으며 값은 로그에 출력하지 않았다.
- [ ] Nginx 원본은 loopback에만 바인딩되고 클라우드 보안 그룹/UFW도 외부 80/443 직접 접근을 차단한다.
- [ ] Grafana, Prometheus, exporter는 Tailscale 또는 명시적으로 승인한 사설 IP에만 바인딩된다.
- [ ] 롤백 기준, 담당자, 관찰 시간, 사용자 공지 기준을 정했다.

### 배포 중

- [ ] active 역할을 확인하고 migration을 한 번만 실행한다.
- [ ] API를 먼저 배포해 DB 호환성을 확인한 뒤 웹 정적 자산을 전환한다.
- [ ] standby가 사용자/API 트래픽을 처리하지 않는지 확인한다.
- [ ] 비정상 상태에서 자동 반복 배포나 자동 DB 승격을 중단할 수 있다.

### 배포 후

- [ ] `/health`와 DB 연결 상태가 정상이다.
- [ ] 강좌 검색·필터·지도·상세·로그인·OAuth·관심 강좌 핵심 흐름을 smoke test했다.
- [ ] 4xx/5xx, 429, DB pool, latency, crawler 품질 지표에 급격한 변화가 없다.
- [ ] 배포 버전과 migration version을 기록했다. 비밀값은 기록하지 않았다.
- [ ] 관찰 시간 종료 후 롤백 준비를 해제하거나 후속 조치를 등록했다.

DB migration은 원칙적으로 전진 수정합니다. 앱 롤백이 필요할 때도 새 스키마와 이전 앱이 호환되는지 먼저 확인하고, 데이터 삭제형 롤백은 별도 승인과 검증된 복원 절차 없이는 실행하지 않습니다.

## 8. 외부 secret rotation 체크리스트

노출 의심만으로도 실제 악용 증거를 기다리지 않고 회전을 시작합니다.

### 공통 절차

- [ ] 영향받은 secret의 종류, 권한, 사용 환경, 연결된 서비스와 소유자를 식별했다.
- [ ] 외부 provider 콘솔에서 기존 secret을 먼저 폐기하거나 즉시 만료시켰다.
- [ ] 최소 scope와 환경 제한을 적용한 새 secret을 발급했다.
- [ ] 보호된 secret store/environment를 갱신하고 평문으로 복사한 임시 자료를 제거했다.
- [ ] 관련 서비스를 재배포 또는 안전하게 reload했다.
- [ ] 새 secret으로 정상 동작하고 이전 secret으로는 요청이 거부되는지 확인했다.
- [ ] provider·애플리케이션·DB 감사 로그에서 발급 시점 이후의 비정상 사용을 조사했다.
- [ ] CI artifact, 패키지, 이미지, 로그, 백업, Git history 등 2차 노출 위치를 확인하고 보존 정책에 따라 격리·정리했다.
- [ ] 회전 완료 시각, 범위, 검증 결과와 후속 작업을 자산대장/incident 기록에 남겼다. secret 자체는 기록하지 않았다.

### 종류별 추가 조치

- 인증 signing secret: 새 key ID로 전환하고 기존 access token·세션을 무효화한다. 필요 시 모든 사용자 재로그인을 요구한다.
- OAuth client secret: provider에서 이전 secret을 revoke하고 redirect/origin allowlist도 함께 재검토한다.
- DB 비밀번호: API, migration, crawler, staging, backup 역할을 각각 회전하고 이전 세션을 종료한다. 불필요한 역할과 권한을 제거한다.
- 지도·AI·검색 등 API key: origin/IP/API/scope 제한과 사용량 알림을 설정하고 비정상 과금 여부를 확인한다.
- 메시징 bot·알림 webhook: 이전 토큰/webhook을 폐기하고 허용 채널·명령·발신자를 재검토한다.
- 터널·프록시 토큰: 기존 connector/session을 종료하고 ingress route와 public exposure를 재검토한다.
- SSH·배포·백업 키: 이전 public key와 계정 접근을 제거하고 용도별 새 키를 발급한다. 백업 암호화 키가 영향받았으면 기존 백업의 재암호화 또는 격리 계획도 수립한다.

외부 서비스가 이중 secret을 지원하면 짧은 중첩 기간에 새 secret을 배포·검증한 뒤 기존 secret을 폐기합니다. 노출이 확인된 secret은 중첩 없이 즉시 폐기하는 것을 우선합니다.

## 9. 보안 사고 대응

1. 노출된 경로와 계정을 격리하고 자동 배포·worker·의심 세션을 중지합니다.
2. 위 rotation 절차로 자격 증명을 폐기하고 세션을 무효화합니다.
3. edge, API, DB, provider 감사 로그의 시간을 맞춰 최초 접근·권한 상승·데이터 접근 범위를 조사합니다.
4. 손상되지 않은 기준으로 복구하고 CI, migration, smoke test, 모니터링을 통과시킵니다.
5. 사용자/관계기관 통지 의무와 보존 의무를 확인합니다.
6. 근본 원인, 탐지 공백, 재발 방지, 담당자와 기한을 기록합니다. 사고 기록에도 secret 원문은 남기지 않습니다.

## 10. 정기 점검과 남은 과제

- 매일: health, 5xx/429, DB/디스크, 수집 실패·데이터 급감 알림 확인
- 매주: 취약점 audit, 운영/인증 이상 로그, 실패한 백업·복원 작업 확인
- 매월: 계정·DB 역할·외부 API scope·관리자 allowlist·공개 endpoint 검토
- 분기: 격리 복원 훈련, 장애 전환 훈련, secret rotation 표본 훈련

공개 배포 전 특히 확인할 항목은 분산 rate limit, 운영 UI의 별도 인증, crawler TLS 검증, SSRF/egress 방어, OAuth 계정 연결·이메일 신뢰 정책, secret scanning과 artifact 검사를 포함한 공급망 통제입니다. 보완 통제가 없는 항목은 risk owner와 완료 기한을 지정합니다.
