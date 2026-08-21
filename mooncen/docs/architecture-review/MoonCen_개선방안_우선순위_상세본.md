# MoonCen 개선방안 및 우선순위 — 상세본

## 문서 목적

이 문서는 현재 워크스페이스 정적 분석 결과를 실행 가능한 개선 backlog로 변환합니다. 우선순위는 다음 기준을 함께 고려했습니다.

- **영향도**: 데이터 손실, 보안 침해, 장기 중단, 잘못된 운영 작업의 최대 피해
- **발생 가능성**: 현재 구조와 통제 상태에서 현실적으로 발생할 가능성
- **긴급성**: 다음 배포·장애·전환 전에 해결해야 하는지
- **선행성**: 다른 개선이 의존하는 기준선인지
- **실행 비용**: 팀 규모가 확인되지 않아 상대값 `S/M/L/XL`로 표현
- **근거 신뢰도**: 코드로 확인된 사실인지, 운영 환경 확인이 필요한 조건부 위험인지

우선순위 의미는 다음과 같습니다.

| 등급 | 의미 | 목표 시점 |
|---|---|---|
| P0 | 다음 production 변경·외부 노출·분산 전환 전에 완료 | 즉시~2주 |
| P1 | 장애 탐지·복구·릴리스 신뢰성의 핵심 | 30일 내 |
| P2 | 변경 비용·drift·품질 자동화를 낮추는 과제 | 1~3개월 |
| P3 | SLO·사업 규모를 근거로 선택하는 전략 과제 | 3~6개월 이상 |

---

## 1. 전체 backlog 요약

| ID | 우선 | 과제 | 영향 | 비용 | 핵심 선행조건 |
|---|---|---|---|---|---|
| GOV-01 | P0 | 운영 release source of truth 확립 | 매우 높음 | L | 변경 소유자·검토 범위 확정 |
| OPS-01 | P0 | 권위 topology/HA/Cloudflare 런북 통합 | 매우 높음 | M | 실제 host inventory |
| DATA-01 | P0 | crawler 실제 write path와 승인 경계 확정 | 매우 높음 | M | production env 읽기 권한 |
| REL-01 | P0 | `gen1crawler` 안전 코드 릴리스·rollback 경로 | 높음 | L | GOV-01, DATA-01 |
| DIST-01 | P0 | distributed transition gate 유지·go/no-go 증거 | 매우 높음 | M | OPS-01, DR-01, OBS-01 |
| SEC-01 | P0 | Ops 접근·서버 RBAC·파괴 작업 통제 검증 | 매우 높음 | M | 실제 Access 설정 확인 |
| SEC-02 | P0 | web localStorage 사용자 필드 allowlist | 높음 | S | 회귀 테스트 |
| DR-01 | P1 | RPO/RTO 승인, PITR 또는 restore-only 정책 | 매우 높음 | L | 서비스 owner 결정 |
| DR-02 | P1 | immutable/offsite 2차 backup과 key escrow | 매우 높음 | L | DR-01 |
| OBS-01 | P1 | API·DB·crawler·AI 중앙 SLO 관측 | 높음 | L | 지표 owner·cardinality budget |
| OBS-02 | P1 | backup/restore/monitoring self-health alert | 높음 | M | 외부 failure domain |
| REL-02 | P1 | clean CI artifact 기반 production CD | 높음 | L | GOV-01 |
| DATA-02 | P1 | schema/API 계약과 migration rehearsal | 높음 | L | canonical migration authority 유지 |
| SEC-03 | P1 | fleet/edge rate limit 운영 기준 | 높음 | M | Cloudflare/Nginx 실배포 확인 |
| AI-01 | P1 | AI 외부 전송·endpoint·보존 정책 | 높음 | M | 데이터 분류·provider 계약 |
| TEST-01 | P1 | Ops/mobile/crawler critical-path E2E | 높음 | L | test environment |
| ARCH-01 | P2 | 대형 backend/frontend/crawler 모듈 분리 | 중~높음 | XL | characterization test |
| DATA-03 | P2 | provider configuration 단일 source of truth | 높음 | XL | DATA-01 |
| DATA-04 | P2 | canonical crawler envelope/persistence | 높음 | XL | DATA-03 |
| OPS-02 | P2 | 운영 설정 CI와 중앙 로그 | 중~높음 | L | REL-02 |
| SUP-01 | P2 | SBOM·CVE·artifact signing/attestation | 중~높음 | L | REL-02 |
| MOB-01 | P2 | 모바일 출시 범위·보안·telemetry | 조건부 높음 | L | 제품 출시 결정 |
| GOV-02 | P2 | CODEOWNERS·SECURITY·ADR·문서 drift 검사 | 중간 | M | GOV-01 |
| AI-02 | P2 | AI singleton/retry 영속화와 명칭 정리 | 중간 | M | AI-01 |
| DATA-05 | P2 | 레거시 사용자 테이블 정리 | 중간 | M | 사용량 계측·migration |
| HA-01 | P3 | DB HA 또는 restore-only DR의 최종 구현 | 매우 높음 | XL | DR-01, 실측 SLO·비용 |
| SCALE-01 | P3 | application replica·event 기반 비동기화 | 조건부 | XL | load/capacity 근거 |

---

## 2. P0 — 다음 운영 변경 전

### GOV-01. 운영 release source of truth 확립

**문제**  
분석 시점에 983개의 변경 상태 항목이 있었고, tracked diff는 562개 파일에 걸쳐 있습니다. `production_topology.json`, 일부 workflow·systemd·Nginx·package 파일은 현재 기준선에서 미추적일 수 있습니다. 표준 배포는 dirty tree를 거부하지만 Ops deployment worker는 검토된 dirty snapshot을 synthetic commit으로 만들 수 있어 GitHub branch CI를 통과한 revision과 다른 artifact가 production에 갈 수 있습니다.

**위험**  
사고 시 “무엇이 배포됐는지” 재현하지 못하고, rollback 대상과 migration/설정 호환성을 판단하기 어렵습니다. 개선된 CI 파일 자체가 배포 revision에 포함되지 않았다면 통과했다고 가정할 수도 없습니다.

**실행안**

1. 변경을 backend, crawler/data, frontend, ops/deploy, mobile, docs로 나누고 각 영역 owner를 지정합니다.
2. secrets, 생성물, local state를 분류하고 `.gitignore` 또는 관리 대상에 명시합니다.
3. 운영 관련 파일을 모두 Git 추적 대상으로 고정합니다.
4. exact commit에서 전체 CI를 통과시킵니다.
5. release manifest에 commit SHA, tree SHA, migration set checksum, frontend digest, topology revision을 기록합니다.
6. production 배포는 clean reviewed commit에서 만든 signed artifact만 허용합니다.
7. dirty synthetic snapshot은 development/staging 진단용으로 제한하거나 별도 break-glass 승인을 요구합니다.

**완료 기준**

- production 파일 100%가 reviewed commit에 포함
- 서버별 deployed commit/tree SHA와 release manifest가 일치
- rollback artifact와 migration compatibility가 사전 검증됨
- GitHub/CI와 Ops 배포가 동일 provenance를 표시

**담당 권장**: Release owner + 각 도메인 reviewer  
**비용**: L  
**선행조건**: 없음 — 다른 모든 과제의 기준선

---

### OPS-01. 권위 topology·HA·Cloudflare 런북 통합

**문제**  
최신 선언은 `cloud/gen1crawler/gen1db/wtr-linux`, `crawlerMode=legacy`이며 `n100`은 퇴역 상태입니다. 반면 `deploy/ha`와 일부 Cloudflare 문서는 `n100`을 current standby/crawler와 실제 failover 대상으로 기술합니다.

**위험**  
장애 중 오래된 명령을 실행하면 잘못된 DB 승격, split-brain, tunnel 오전환, 데이터 손실이 생길 수 있습니다.

**실행안**

1. 읽기 전용 inventory로 hostname, IP, 역할, systemd unit, DB `pg_is_in_recovery()`, endpoint, tunnel route를 수집합니다.
2. desired topology와 observed topology를 나란히 기록합니다.
3. `production_topology.json`을 단일 machine-readable desired authority로 채택하되 실제 observed evidence와 revision을 연결합니다.
4. 과거 문서는 `ARCHIVED — DO NOT RUN` 처리하고 위험 명령을 제거하거나 explicit guard 뒤로 이동합니다.
5. 문서·script CI에서 퇴역 node가 production 명령에 등장하면 실패하게 합니다.
6. 최신 topology 기준 DB 장애, crawler 장애, tunnel 전환 tabletop을 수행합니다.

**완료 기준**

- desired/observed 차이 0건 또는 승인된 exception
- 모든 active runbook에 owner, review date, topology revision
- `n100` production action reference 0건
- tabletop 중 모호하거나 실행 불가능한 단계 0건

**담당 권장**: Platform/DB owner  
**비용**: M  
**선행조건**: 운영 읽기 권한

---

### DATA-01. crawler 실제 쓰기 경로와 승인 경계 확정

**문제**  
토폴로지는 legacy mode와 disabled distributed workers를 선언하지만, 코드에는 direct-primary와 staging write mode가 모두 있습니다. staging/control DB와 강력한 promotion gate가 있어도 실제 legacy 환경의 `CRAWL_WRITE_MODE`, DB URL/role, apply 승인 주체는 저장소만으로 알 수 없습니다.

**위험**  
운영자가 staging 보호를 사용한다고 믿는 동안 crawler가 primary에 직접 쓸 수 있습니다. 또는 scheduler/apply가 이중 실행돼 close-missing과 lifecycle 변경이 충돌할 수 있습니다.

**실행안**

1. `gen1crawler`의 실제 environment, unit, DB endpoint/role을 secret 값을 노출하지 않고 fingerprint 형태로 수집합니다.
2. 최근 batch/apply log와 primary write audit를 대조합니다.
3. production 정책을 `staging-only` 또는 명시적으로 승인된 legacy direct path 중 하나로 결정합니다.
4. 선택하지 않은 path는 production에서 fail-closed 합니다.
5. batch owner, dry-run reviewer, approver, applier 역할과 분리 조건을 RACI로 만듭니다.
6. partial/zero/급락/close-missing 차단 시나리오를 drill 합니다.

**완료 기준**

- 실제 write mode·DB role·endpoint·scheduler owner가 inventory에 기록
- production에서 의도한 단일 write path만 동작
- 승인 없는 primary 변경 테스트가 실패
- dry-run fingerprint부터 apply audit까지 하나의 batch ID로 추적

**담당 권장**: Data pipeline owner + DBA  
**비용**: M  
**선행조건**: OPS-01 inventory

---

### REL-01. `gen1crawler` 안전 코드 릴리스와 rollback 경로

**문제**  
현재 crawler owner는 `gen1crawler`지만 지원되는 crawler code updater는 의도적으로 unavailable인 부분이 있습니다. exact reviewed staging batch activation은 존재해도 crawler code 긴급 수정·rollback 표준 경로가 완결됐다고 보기 어렵습니다.

**실행안**

1. GOV-01의 exact reviewed commit으로 crawler 전용 artifact를 만듭니다.
2. registry/config/migration compatibility와 Chrome/Selenium smoke를 preflight합니다.
3. canary provider 또는 격리 staging batch로 새 revision을 실행합니다.
4. 이전 immutable release tree를 유지하고 health·batch 결과 기준 자동 switch/rollback을 구현합니다.
5. release provenance를 control DB와 monitoring textfile에 기록합니다.
6. 연속 2회 staging drill과 1회 rollback drill을 증거화합니다.

**완료 기준**: arbitrary command 없이 고정 action으로 배포, code/config digest 검증, canary 실패 자동 원복, revision별 batch 추적  
**담당 권장**: Data platform + Release owner  
**비용**: L  
**선행조건**: GOV-01, DATA-01

---

### DIST-01. 분산 전환 gate 유지와 go/no-go 계약

**문제**  
control plane, fenced lease, immutable observation, finalizer, release agent는 구현돼 있으나 worker는 disabled이고 문서는 전환을 명시적으로 `NOT READY`로 둡니다. builder/sign approval handoff 일부도 fail-closed입니다.

**실행안**

전환을 다음 원자적 checklist로 관리합니다.

1. authoritative clean release와 signed crawler artifact
2. staging/control DB backup·restore receipt
3. scheduler/finalizer/approver/worker별 최소권한 role 검증
4. canary worker bootstrap, signature, fencing, kill/lease-expiry 테스트
5. 42 logical group → concrete task coverage와 ownership 검증
6. queue/freshness/partial/zero/dead task/apply monitoring
7. legacy scheduler 중지·원복 계획
8. topology mode, monitoring labels, runbook을 같은 change window에 변경
9. stop/go/rollback threshold와 의사결정자 서명

**완료 기준**: 모든 evidence가 한 change record에 있고 하나라도 실패하면 worker activation이 불가능  
**담당 권장**: Data platform lead + DBA + Operations approver  
**비용**: M(검증), 실제 전환은 L~XL  
**선행조건**: GOV-01, OPS-01, DATA-01, REL-01, DR-01, OBS-01

---

### SEC-01. Ops 접근·서버 권한·파괴 작업 통제 검증

**문제**  
public/ops origin 분리와 viewer/operator/admin UI는 강점입니다. 그러나 UI 역할은 보안 경계가 아니며 Cloudflare Access/VPN/MFA와 production Ops agent 보호가 실제 적용됐는지는 확인되지 않습니다.

**실행안**

1. Ops origin을 private network 또는 Cloudflare Access 뒤로 제한하고 MFA를 필수화합니다.
2. 세션 TTL, logout-all, credential rotation, break-glass를 검증합니다.
3. 모든 mutation의 role × environment × action matrix를 서버 계약 테스트로 만듭니다.
4. migration, restore, production deploy, rollback, content override에 재인증 또는 2인 승인을 적용합니다.
5. job parameter secret 거부, nonce/replay 방지, fixed command registry와 audit가 우회되지 않는지 테스트합니다.
6. 401, CSRF, SSE reconnect, viewer/operator/admin을 browser E2E에 포함합니다.

**완료 기준**: 무인증 public 접근 실패, MFA 우회 실패, viewer mutation 100% 실패, 모든 파괴 작업에 actor/request/change/artifact 기록  
**담당 권장**: Security + Ops backend owner  
**비용**: M  
**선행조건**: 실제 access 설정 조회

---

### SEC-02. 웹 사용자 정보 localStorage allowlist

**문제**  
`frontend2/src/auth.ts`의 `AuthUser`에 `accessToken`, `code` 선택 필드가 있고 객체 전체를 localStorage에 저장합니다. 현재 응답에 credential이 포함된 증거는 없지만 API 변화가 보안 회귀로 이어질 수 있습니다.

**실행안**

- 저장 전에 `id`, `provider`, `name`, `email` 등 표시용 필드만 새 객체로 만듭니다.
- token/code/password/secret류 필드가 전달돼도 저장되지 않는 테스트를 추가합니다.
- 기존 storage migration에서 예상치 못한 필드를 제거합니다.
- CSP와 XSS 방어를 유지하되 localStorage를 인증 상태의 source of truth로 사용하지 않습니다.

**완료 기준**: credential canary를 포함한 user payload 저장 후 localStorage에 민감 필드 0건  
**담당 권장**: Web owner  
**비용**: S  
**선행조건**: 없음

---

## 3. P1 — 30일 내

### DR-01. 서비스별 RPO/RTO와 DB 복구 정책 승인

**현재 근거**  
일일 암호화 dump와 월간 restore test 자산은 있으나 승인된 RPO/RTO와 실측 결과가 없습니다. 최신 topology에는 production standby가 없고 PITR/WAL archiving 증거도 없습니다.

**실행안**

1. 서비스·데이터별 허용 손실과 중단 시간을 business owner와 승인합니다.
2. 일일 dump만 유지한다면 “최악 약 24시간 RPO 가능”을 명시적으로 수용합니다.
3. 더 짧은 목표가 필요하면 pgBackRest/WAL-G 등 PITR과 독립적인 archive target을 설계합니다.
4. HA와 restore-only DR을 비용·복잡도·RTO로 비교한 ADR을 만듭니다.
5. 비식별 isolated 환경에서 DB뿐 아니라 app, secret, tunnel/DNS, functional journey까지 복구합니다.
6. 분기마다 RTO/RPO를 실제 측정합니다.

**완료 기준**: 승인된 목표, 복구 절차와 owner, 최근 drill 결과가 하나의 dashboard/change record에 존재  
**비용**: L

---

### DR-02. immutable/offsite 2차 backup과 key escrow

**현재 근거**  
`age` 암호화, Ed25519 manifest, SHA-256 검증은 강하지만 저장소상 목적지는 단일 NAS이며 retention 계정이 이전 세대를 삭제할 수 있습니다.

**실행안**

- 다른 failure domain의 object-lock/immutable copy를 추가합니다.
- write-only 또는 append-only upload credential과 retention-admin credential을 분리합니다.
- age identity, signing key, runtime secret과 restore 문서를 외부 escrow에 보관하고 정기 접근 테스트를 합니다.
- NAS unavailable, credential compromise, mass deletion 시나리오를 drill 합니다.

**완료 기준**: primary/NAS가 모두 불가한 상태에서 escrow로 목표 시간 내 복구 성공  
**비용**: L

---

### OBS-01. 중앙 API·DB·crawler·AI SLO 관측

**현재 근거**  
node/systemd/textfile monitoring은 충실하지만 FastAPI metrics는 process-local deque이고 중앙 Prometheus 집계가 아닙니다.

**권장 지표**

| 영역 | 최소 지표 |
|---|---|
| API | request rate, status/error, p50/p95/p99, timeout, route group, active worker |
| DB | pool usage/wait, connection error, lock wait, statement timeout, slow query, DB size |
| crawler | last successful complete batch, partial/zero/failed, provider freshness, lease age, dead task |
| apply | dry-run/apply result, fingerprint mismatch, close-missing block, row delta |
| AI | backlog age, provider latency/error, retry, host health, update count |
| clients | web JS error, Ops SSE reconnect, mobile crash/ANR/network error by app version |
| external | public search/login/safe detail synthetic journey, TLS expiry |

label cardinality를 provider/course/user 단위로 무제한 만들지 말고 aggregate와 drill-down log를 분리합니다.

**완료 기준**: 서비스 owner가 승인한 SLO/error budget, alert가 사용자 영향과 연결되고 synthetic failure drill에서 정해진 시간 내 page  
**비용**: L

---

### OBS-02. backup·restore와 monitoring 자체의 dead-man

**문제**  
backup freshness, restore-test success/staleness와 monitoring 자체 장애를 외부 failure domain에서 감시하는 증거가 부족합니다. 같은 `bot`에서 Prometheus/Grafana/Telegram이 동작하면 bot 장애가 무음일 수 있습니다.

**실행안**

- backup ID, age, size, manifest/signature verification, remote copy result를 textfile metric으로 발행합니다.
- restore test ID, backup ID, duration, row/check result, 성공 시각을 metric/audit로 발행합니다.
- 외부 uptime/dead-man 서비스가 public health와 Prometheus heartbeat를 검사하도록 합니다.
- backup/restore/functional test unit에 공통 `OnFailure` notification을 연결합니다.

**완료 기준**: timer 중지·서명 실패·bot 중지 simulation이 외부 채널로 2~5분 내 통보  
**비용**: M

---

### REL-02. clean CI artifact 기반 production CD

**실행안**

1. protected branch/tag와 승인된 GitHub Environment를 사용합니다.
2. exact commit에서 backend/frontend/config/migration artifact를 만듭니다.
3. SBOM, test result, vulnerability result, provenance와 signature를 생성합니다.
4. production deployer는 signature와 expected tree/migration/topology revision을 검증합니다.
5. preflight backup evidence, health, canary와 rollback threshold를 확인합니다.
6. 배포 후 `/live`, `/health`, 주요 API, SEO, OAuth config, DB role, frontend asset digest를 검사합니다.
7. 실패 시 이전 immutable release로 자동 원복하고 DB migration은 expand-contract/forward-fix 정책을 따릅니다.

**완료 기준**: 누가·무엇을·왜·어떤 artifact로 배포했고 어떤 검사를 통과했는지 1분 내 조회 가능  
**비용**: L

---

### DATA-02. API·schema 계약과 migration rehearsal

**문제**  
웹·모바일·Ops가 API DTO를 각각 구현하고 웹은 런타임 검증이 약합니다. ORM과 canonical SQL schema의 자동 drift 검사가 뚜렷하지 않습니다. CI migration은 새 DB 중심입니다.

**실행안**

- production에서 UI를 숨기더라도 build-time OpenAPI artifact를 생성하고 version 관리합니다.
- web/mobile/ops client type과 query serializer를 생성하거나 공통 schema에서 만듭니다.
- 경계에서는 Zod 등 런타임 검증을 적용해 server drift를 명시적 오류로 처리합니다.
- canonical SQL migration authority는 `DB/setup_db.py`로 유지하고 별도 migration authority를 병행하지 않습니다.
- ORM ↔ live schema contract, migration checksum, grant/role contract를 CI에서 비교합니다.
- 비식별 production-like snapshot에 migration을 rehearsal하고 직전 app/new schema 또는 forward-fix 전략을 검증합니다.

**완료 기준**: breaking contract가 merge 전에 실패하고 production-like migration 결과·rollback/forward plan이 release evidence에 포함  
**비용**: L

---

### SEC-03. fleet/edge rate limit 운영 기준

**현재 근거**  
Nginx와 애플리케이션 bucket이 있으나 인스턴스별 메모리 기반입니다. Cloudflare 실제 rule은 저장소만으로 확인할 수 없습니다.

**실행안**

- 로그인, OAuth state, bug report, write API별 edge limit을 threat model에 맞춰 정의합니다.
- trusted proxy chain과 real client IP를 검증합니다.
- 다중 instance 시 shared store 또는 Cloudflare global policy를 authority로 사용하고 app limiter는 defense-in-depth로 둡니다.
- IPv6, NAT, header spoofing, retry storm, false-positive를 load/security test합니다.

**완료 기준**: 단일·다중 worker에서 합산 limit이 예상대로 작동하고 정상 사용자의 거부율이 SLO 이내  
**비용**: M

---

### AI-01. AI 데이터 외부 전송과 endpoint 정책

**문제**  
강좌 title/description/category가 Gemini 또는 구성된 Ollama host로 전달될 수 있습니다. Ollama URL은 운영 환경을 신뢰하고 OpenAI provider는 인식하지만 구현되지 않았습니다.

**실행안**

- 전송 필드의 데이터 분류와 provider별 허용 정책을 승인합니다.
- endpoint/domain allowlist, HTTPS 검증, 내부 Ollama mTLS 또는 private network를 적용합니다.
- request/response log에 원문·secret이 남지 않도록 redaction합니다.
- provider 보존·학습 사용·지역·삭제 정책을 기록합니다.
- 장애 시 deterministic fallback과 update하지 않을 조건을 명시합니다.
- 실제로 쓰지 않는 provider/config를 제거하거나 명확히 disabled 처리합니다.

**완료 기준**: 승인되지 않은 endpoint로 요청 실패, provider별 data-flow register와 incident/disable 절차 존재  
**비용**: M

---

### TEST-01. critical-path E2E와 권한·복구 계약

추가할 우선 시나리오는 다음과 같습니다.

- Ops: login/401/CSRF, viewer/operator/admin, SSE reconnect/backoff, production confirmation, audit
- crawler: complete/partial/zero/timeout, lease expiry, fenced stale worker, fingerprint mismatch, close block
- migration: production-like snapshot, role grant, previous app compatibility
- backup: corrupt/signature fail/stale/latest-too-old/NAS unavailable
- mobile: 위치 거부/재허용, WebView navigation·message 위조, background resume, API version mismatch
- web: credential payload가 localStorage에 남지 않음, OAuth state/PKCE 실패, 취소된 검색 요청

**완료 기준**: critical path map, flaky test owner/expiry, release gate와 연결  
**비용**: L

---

## 4. P2 — 1~3개월

### ARCH-01. 대형 모듈 단계적 분리

한 번에 재작성하지 않고 characterization test와 facade를 둔 단계적 추출을 권장합니다.

| 대상 | 권장 경계 |
|---|---|
| `Crawler_MunicipalYaml.py` | target schema, transport, parser registry, normalization, persistence, orchestration |
| `run_crawlers.py` | registry, execution engine, batch lifecycle, maintenance, distributed adapter |
| `frontend2/App.tsx` | search, map, auth, user-course, modal feature + server-state layer |
| `backend/routers/ops_v2.py` | services, crawlers, jobs/SSE, deployments, audit, settings routers |
| `crawler_analytics.py` | query repository, domain metrics, response DTO |
| Ops workers | validation/policy, executor, persistence, event reporting |

**완료 기준**: 공개 계약과 DB result가 characterization test에서 동일하고 각 모듈 owner·dependency 방향이 명확  
**비용**: XL

---

### DATA-03. provider configuration 단일 source of truth

registry, generated registry, production provider manifest, ownership, YAML target, coverage가 여러 파일에 흩어져 있습니다.

권장 모델은 하나의 versioned provider catalog에서 다음을 생성하는 방식입니다.

- runner registry와 fixed argv
- scheduler concrete task manifest
- provider ownership과 close-missing scope
- documentation/coverage report
- config schema validation과 compatibility digest

생성 산출물은 source와 digest를 포함하고 직접 수정하면 CI가 실패해야 합니다.

**비용**: XL

---

### DATA-04. canonical crawler collection envelope와 persistence

각 crawler가 SQL/upsert/lifecycle을 중복 구현하지 않도록 다음 계약을 공통화합니다.

- provider, source URL, fetched_at, parser/config revision
- external branch/course identity
- normalized field + raw evidence + validation warning
- scope/completeness/sample flag
- batch/lease/attempt ownership
- idempotency key와 close-missing eligibility

전용 crawler는 raw collection과 mapping만 구현하고 staging writer가 공통 persistence를 담당하도록 단계적으로 이동합니다. direct-primary는 migration 기간을 제외하고 production에서 제거하거나 명시적 break-glass로 제한합니다.

**비용**: XL

---

### OPS-02. 운영 설정 CI와 중앙 로그

**설정 CI**

- 모든 Nginx 구성의 `nginx -t`
- `systemd-analyze verify`
- `promtool check config/rules`
- Grafana provisioning/schema 검사
- Compose config와 image digest 검사
- 문서 link/path/node name 검사
- functional report path 등 문서-실제 unit 불일치 검사

**로그**

- request ID, job ID, batch ID, release ID 공통 correlation
- 중앙 수집, 보존 기간과 용량 budget
- query string·token·OAuth code·personal data redaction
- ingestion gap와 disk pressure alert

**비용**: L

---

### SUP-01. 공급망 증적 강화

- 활성 package root 전체 Dependabot/Renovate 범위 정리
- Python/Node/container/filesystem CVE scan
- SPDX/CycloneDX SBOM
- artifact/container signature와 build provenance
- base image digest와 runtime EOL calendar
- release에서 signature/SBOM/policy 실패 시 배포 차단
- CodeQL 또는 동등한 정적 분석을 위험 모듈부터 적용

**비용**: L

---

### MOB-01. 모바일 출시 범위·보안·telemetry

먼저 제품 결정을 둘로 나눕니다.

1. **read-only companion 앱**: 로컬 찜, 공개 API, 위치·지도 중심
2. **계정 기반 앱**: native OAuth, 서버 찜, 알림/push, 다기기 동기화

공통 출시 gate는 EAS project, AAB/IPA signing, Kakao key platform restriction, WebView host/navigation allowlist, 실기기 권한 테스트, crash/ANR/network telemetry, privacy/store metadata, staged rollout/rollback입니다. 계정 기반이면 PKCE/deep link/session/push consent 계약이 추가됩니다.

보관함의 ID별 최대 50개 detail 요청은 batch API 또는 인증 사용자 목록으로 개선합니다.

**비용**: L

---

### GOV-02. 소유권·보안·ADR·문서 drift 관리

현재 `CODEOWNERS`, `SECURITY.md`, `CONTRIBUTING.md`, `CHANGELOG.md`, ADR index가 확인되지 않습니다.

권장 최소 산출물은 다음과 같습니다.

- 위험 경로별 CODEOWNERS: auth, DB migration/roles, apply, deploy, topology, backup
- 취약점 접수·응답 SLA와 secret 사고 절차
- migration/config/generated-file 기여 규칙
- topology, HA 대 restore-only, mobile scope, API contract, AI provider ADR
- 문서 owner/review date/topology revision
- 퇴역 node·경로·명령과 실제 파일 존재 여부를 검사하는 CI

**비용**: M

---

### AI-02. AI worker concurrency와 retry 영속화

- PID file 조회→생성 경쟁을 OS file lock 또는 DB leader advisory lock으로 교체
- retry/backoff/dead-letter를 영속 job table로 이동
- provider별 circuit breaker와 queue age alert
- “AI title/summary”가 실제 deterministic 동작과 맞도록 명칭을 정리하거나 의도 기능을 구현
- 미구현 OpenAI 경로와 사용되지 않는 prompt/config 정리

**비용**: M

---

### DATA-05. 레거시 사용자 테이블 정리

`notifications`/`user_favorites`와 현행 user-course notification/favorite 테이블이 함께 있습니다. 먼저 코드·query·row count·last write를 계측하고 다음 중 하나를 결정합니다.

- compatibility view와 명확한 deprecation 기간
- backfill → dual-read 검증 → single-write → 제거 migration
- 다른 의미라면 schema/doc/model 이름으로 역할을 명확히 구분

**비용**: M

---

## 5. P3 — 전략 과제

### HA-01. DB HA 또는 restore-only DR 최종 구현

최신 manifest 기준 `cloud`가 frontend/backend/primary DB를 모두 담당해 단일 장애 지점입니다. 그렇다고 즉시 자동 failover를 도입하면 fencing, split-brain, 운영 복잡도가 오히려 위험할 수 있습니다.

다음 데이터를 먼저 확보한 뒤 ADR로 결정합니다.

- 실제 availability SLO와 error budget
- 허용 RTO/RPO
- 현재 incident/maintenance downtime
- replication·standby 운영 역량과 비용

HA를 선택하면 synchronous/asynchronous 정책, lag alert, fencing, promotion authority, old primary isolation, rejoin, DNS/tunnel, 분기 failover drill을 갖춰야 합니다. restore-only를 선택하면 예상 downtime과 데이터 손실을 이해관계자가 승인하고 full restore automation을 강화해야 합니다.

### SCALE-01. application replica와 event 기반 비동기화

API replica, read replica, notification queue, 별도 analytics/data platform은 load와 SLO가 근거일 때 추진합니다. 현재는 process-local limiter/metrics와 primary DB 의존을 먼저 제거하지 않으면 replica가 일관성과 관측 문제를 확대할 수 있습니다.

---

## 6. 의존관계와 실행 순서

```text
GOV-01 clean release baseline
  ├─ OPS-01 authoritative topology
  │    ├─ DATA-01 actual crawler path
  │    │    ├─ REL-01 crawler release
  │    │    └─ DATA-03/04 provider + persistence consolidation
  │    └─ DR-01/02 recovery policy
  ├─ REL-02 production CD
  │    ├─ SUP-01 supply-chain evidence
  │    └─ OPS-02 configuration CI
  └─ DATA-02 schema/API contract

OBS-01 + OBS-02 + DR evidence + REL-01
  └─ DIST-01 distributed go/no-go
```

P0는 병렬로 시작할 수 있지만 distributed activation은 위 증거를 모두 기다려야 합니다.

---

## 7. 90일 실행 제안

### 1주차 — 권위 복구

- release/change inventory와 owner 지정
- server/DB/tunnel/crawler observed inventory
- 과거 HA 문서 실행 금지
- actual write mode와 distributed-disabled 확인
- Ops Access/MFA/RBAC와 backup 영수증 확인
- localStorage allowlist 변경·테스트

### 2~4주 — 배포·복구·관측 기반

- clean exact-commit release와 signed manifest
- crawler release/rollback 및 batch apply drill
- RPO/RTO/HA ADR 초안
- API RED, crawler freshness/apply, backup/restore/dead-man alert
- Ops browser E2E와 mutation role contract
- AI data-flow policy

### 2개월차 — 계약과 복구 실증

- immutable/offsite copy와 key escrow
- production-like migration rehearsal
- OpenAPI/client runtime validation pilot
- full-stack DR drill
- systemd/promtool/Nginx/Grafana CI

### 3개월차 — 구조 개선

- Municipal crawler와 `App.tsx` 한 경계씩 추출
- provider catalog pilot와 generated registry
- canonical staging envelope/persistence pilot
- 중앙 로그와 release SBOM/signing
- 모바일 제품 범위가 승인된 경우 출시 gate 실행

---

## 8. 권장 KPI와 정기 검토

| 목표 | KPI | 검토 주기 |
|---|---|---|
| release 추적성 | deployed revision 중 signed clean artifact 비율 100% | 매 배포 |
| topology 정합성 | desired/observed drift 수, stale runbook 수 | 매주 |
| crawler 신뢰성 | complete batch freshness, partial/zero율, blocked apply 수 | 매일/주간 |
| 데이터 안전 | fingerprint mismatch, unauthorized primary write 0건 | 매 batch |
| 복구 가능성 | backup age, restore drill 성공률, 실측 RTO/RPO | 일간/월간/분기 |
| API 사용자 경험 | availability, p95/p99, 5xx, synthetic success | 상시 |
| 보안 | Ops MFA coverage, RBAC contract pass, secret/credential regression | 매 release |
| 구조 품질 | hotspot churn, cycle dependency, flaky test, schema/client drift | 월간 |

우선순위는 월 1회 재평가하되 P0의 완료 기준이 충족되지 않았다는 이유로 의미를 낮추지 않습니다. 실제 운영 증거가 새로 확인되면 위험을 재산정하고 문서 revision을 갱신합니다.

