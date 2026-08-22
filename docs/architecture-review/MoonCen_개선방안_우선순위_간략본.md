# MoonCen 개선방안 및 우선순위 — 간략본

## 1. 우선순위 원칙

- **P0 — 다음 운영 변경 전**: 잘못된 revision·topology·권한·데이터 경로가 사고를 만드는 것을 먼저 차단합니다.
- **P1 — 30일 내**: 장애 탐지·복구·릴리스 신뢰성과 사용자 영향 관측을 확보합니다.
- **P2 — 1~3개월**: 대형 모듈·중복 계약·운영 자동화를 정리해 변경 비용을 낮춥니다.
- **P3 — 전략 과제**: 검증된 SLO와 사업 필요에 따라 고가용성·플랫폼화를 추진합니다.

현재 가장 중요한 문제는 기능 부족보다 **운영 기준선이 하나로 고정되지 않은 점**입니다. 분석 시작 시 983개의 변경 상태 항목이 있었고, 권위 토폴로지와 과거 `n100` HA 문서가 충돌하며, 실제 legacy crawler write mode도 저장소만으로 확정되지 않습니다.

## 2. 핵심 우선순위

| 우선 | 과제 | 이유 | 완료 기준 |
|---|---|---|---|
| P0-1 | 운영 release source of truth 확립 | dirty snapshot과 실제 배포본 관계가 불명확하고 일부 핵심 운영 파일이 미추적 상태 | 운영 파일 전부 추적, 변경을 리뷰 가능한 commit으로 분리, exact commit에서 전체 CI, signed tag/manifest, 서버 tree SHA 일치 |
| P0-2 | 토폴로지·HA·Cloudflare 런북 통합 | 최신 `cloud/gen1crawler/gen1db`와 과거 `n100` 명령 충돌은 잘못된 승격 위험 | 단일 권위 topology, 과거 문서 `ARCHIVED/DO NOT RUN`, node/명령 CI 검사, tabletop drill |
| P0-3 | crawler 실제 쓰기·배포 경로 확정 | `legacy`, disabled distributed workers, direct/staging 코드가 공존 | `CRAWL_WRITE_MODE`, DB endpoint/role, 승인 주체를 증거화하고 production을 의도한 단일 경로로 fail-closed; gen1crawler exact-commit rollback drill |
| P0-4 | 분산 전환 gate 유지·명문화 | control plane 코드는 있으나 builder/sign handoff와 cutover 증거가 미완료 | 백업·복구, artifact, bootstrap, canary, monitoring, legacy scheduler 정지를 모두 통과하기 전 worker 활성화 금지 |
| P0-5 | Ops 접근·서버 RBAC 배포 검증 | UI role은 보안 경계가 아니며 실제 Access/MFA는 저장소로 확인 불가 | Cloudflare Access/VPN+MFA, 짧은 세션, 모든 mutation 권한 계약 테스트, destructive action 감사·재승인 |
| P0-6 | 웹 사용자 저장 allowlist | 미래 API 응답에 credential 필드가 생기면 localStorage에 남을 수 있음 | `id/provider/name/email` 등 표시 필드만 저장하고 credential 회귀 테스트 추가 |
| P1-1 | RPO/RTO 승인과 복구 강화 | 최신 manifest상 DB standby가 없고 PITR·full-stack DR 증거 없음 | 서비스별 RPO/RTO, PITR 또는 24시간 RPO의 명시적 수용, 분기 full DR 실측 |
| P1-2 | immutable/offsite 2차 백업과 성공 alert | 단일 NAS와 삭제 가능한 보존 계정은 동시 장애에 취약 | object-lock/immutable 2차 사본, 별도 자격증명, backup/restore freshness alert, key escrow 검증 |
| P1-3 | 중앙 API·DB·crawler SLO 관측 | 현재 앱 지표가 process-local이고 system/node 신호 중심 | API RED, DB pool/lock/query, crawler freshness/apply, AI backlog, synthetic journey, dead-man alert |
| P1-4 | clean CI artifact 기반 운영 CD | 수동 배포와 synthetic dirty release가 branch CI와 분리될 수 있음 | clean commit만 production 대상, SBOM/서명/provenance/승인, canary·health·자동 rollback 증적 |
| P1-5 | API·DB 계약과 migration rehearsal | 웹·모바일 DTO 중복, ORM/SQL drift, clean DB migration 중심 CI | OpenAPI 기반 생성/런타임 검증, ORM-schema drift 검사, 운영 snapshot upgrade rehearsal |
| P2-1 | 대형 모듈 분리 | `MunicipalYaml` 60k행, `App.tsx` 2.2k행, `ops_v2` 4k행은 변경 영향이 큼 | 기능 경계별 모듈화, characterization test, 단계적 전환 |
| P2-2 | provider 구성·persistence 단일화 | registry/manifest/YAML/ownership과 crawler별 SQL이 분산 | 단일 provider source와 생성 산출물, canonical collection envelope/persistence library |
| P2-3 | 운영 품질 자동화 | 설정 검증·중앙 로그·공급망 증적이 부분적 | systemd/promtool/Nginx/Grafana CI, 중앙 로그, SBOM·CVE·artifact signing |
| P2-4 | 모바일 출시 의사결정·보안 | 실데이터 연동은 됐지만 EAS/서명/인증/telemetry가 없음 | 출시 범위 결정, WebView allowlist, 실기기·스토어·privacy·rollback gate |
| P3 | DB HA·서비스 replica·데이터 플랫폼화 | 현재 단일 cloud 구조의 장기 확장 문제 | SLO/비용 근거 ADR 후 fencing·failover drill 또는 restore-only 정책 확정 |

## 3. 권장 90일 순서

### 0~7일

1. 변경 상태를 영역별로 분리하고 운영 파일을 Git에 포함합니다.
2. 실제 서버의 commit, unit, endpoint, DB role, crawler mode를 읽기 전용으로 수집합니다.
3. `production_topology.json`을 기준으로 과거 `n100` 명령을 실행 금지 표시합니다.
4. distributed worker가 비활성인지 재확인하고 전환 gate를 변경 승인 항목으로 만듭니다.
5. Ops 접근/MFA/RBAC와 최근 backup·restore 영수증을 확인합니다.
6. 웹 localStorage 저장 필드를 allowlist로 제한합니다.

### 2~4주

1. clean commit → CI → 서명 artifact → 승인 → 배포 → 자동 rollback 흐름을 고정합니다.
2. `gen1crawler` 코드 릴리스·원복과 staging batch apply drill을 수행합니다.
3. backup/restore freshness, 외부 blackbox와 dead-man alert를 추가합니다.
4. API RED, DB, crawler, AI 핵심 지표와 SLO 초안을 운영합니다.
5. RPO/RTO와 HA 대 restore-only 결정을 ADR로 승인합니다.

### 1~3개월

1. immutable/offsite backup과 full-stack DR drill을 완료합니다.
2. OpenAPI/client 계약과 schema drift/migration rehearsal을 도입합니다.
3. 대형 파일을 characterization test 아래 단계적으로 분리합니다.
4. provider source of truth와 crawler persistence 계약을 통합합니다.
5. Ops E2E, 모바일 native 보안·telemetry, 공급망·설정 CI를 보강합니다.

## 4. 제안 성공 지표

| 영역 | 측정 지표 |
|---|---|
| 릴리스 | production 100%가 reviewed clean commit과 artifact hash로 역추적됨 |
| 토폴로지 | 실제 host/service/DB inventory와 권위 manifest 차이 0건 |
| 복구 | 월간 restore test와 분기 full DR 성공; 실측 RTO/RPO가 승인 목표 이내 |
| 데이터 | crawler batch freshness·partial/zero·apply block가 중앙 관측되고 owner가 정해짐 |
| API | availability와 p95/error SLO, 외부 synthetic journey, error budget 운영 |
| 보안 | Ops MFA/서버 RBAC 계약 테스트 100%, credential localStorage 회귀 0건 |
| 품질 | 운영 설정·migration·client contract가 merge 전 자동 검증됨 |

## 5. 당장 하지 말아야 할 것

- 실제 inventory 확인 없이 과거 `n100` failover 명령 실행
- 현재 dirty workspace를 운영 기준선이라고 가정
- distributed worker를 개별적으로 먼저 활성화
- 검토 fingerprint 없이 staging batch를 운영 DB에 반영
- backup 파일 존재만으로 복구 가능성을 판단
- UI의 role disable만으로 Ops 권한을 보장한다고 판단
- 모바일 export 성공을 스토어 출시 준비 완료로 해석

