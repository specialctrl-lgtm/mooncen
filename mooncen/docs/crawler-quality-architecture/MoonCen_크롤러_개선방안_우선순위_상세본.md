# MoonCen 크롤러 개선방안·우선순위 상세본

> 이 문서는 목표 아키텍처를 실제 작업으로 나눈 실행안이다. 일정은 1개 전담 스쿼드 기준의 계획 범위이며, 현재 운영 데이터·인력·보안 검토 결과에 따라 조정한다.

## 1. 우선순위 원칙

우선순위는 다음 순서로 평가한다.

1. 미탐을 관측할 수 있는가
2. 같은 입력으로 수정 전후를 재현할 수 있는가
3. 변경 범위와 회귀를 설명할 수 있는가
4. 독립 승인과 rollback이 가능한가
5. 운영자가 Python 없이 처리할 수 있는가
6. 자동화가 사람의 검수 책임을 약화하지 않는가

이 기준 때문에 Rule Builder UI보다 evidence/label/replay가 앞선다.

## 2. 전체 로드맵

| 단계 | 작업 묶음 | 권장 기간 | 핵심 산출물 | Gate |
|---|---|---|---|---|
| P0-A | 언어·범위 확정 | 1~2주 | taxonomy v1, label guideline, pilot provider | 이견 사례 adjudication 가능 |
| P0-B | provenance 기반 | 2~4주 | candidate trace, case/label schema, generation identity | include/exclude/abstain 모두 추적 |
| P1-A | 재현 기반 | 3~5주 | raw quarantine, sanitizer, fixture registry | PII·signature·retention gate |
| P1-B | Replay MVP | 4~6주 | networkless worker, diff, hard gate | deterministic baseline/candidate |
| P1-C | 운영 UI MVP | 3~5주 | Inbox, Evidence/Label, Replay Lab | case부터 review까지 연결 |
| P2-A | Rule Pack | 4~8주 | schema/compiler/runtime/evaluator registry | code/network/filesystem escape 0 |
| P2-B | Release 연동 | 3~6주 | exact approval, builder handoff, staging shadow | signed evidence 없으면 release 불가 |
| P2-C | Adapter 분해 | 지속 | provider adapter registry와 decision trace | provider별 byte-equivalent migration |
| P3 | 운영 고도화 | 지속 | canary, drift sampling, data repair | drill과 SLO 근거 |

작업은 병렬화할 수 있지만 Gate 순서는 건너뛰지 않는다.

## 3. P0 작업 패키지

### P0-1. Taxonomy v1과 검수 지침

**문제**  
현재 “오탐/미탐”은 행 의미 오류, field 오류, 종료 정책, duplicate, provider scope를 혼합할 수 있다.

**작업**

- semantic/publish/lifecycle/duplicate/field 축 분리
- 기존 `course_registration_v1` stable reason mapping
- root-cause stage 정의
- ambiguous/unverifiable 처리와 metric 제외 규칙
- snapshot-bound label과 supersession 규칙
- 2인 review/adjudication 기준
- 30~50개 대표 case로 calibration workshop

**완료 기준**

- 검수자 간 raw agreement와 disagreement reason이 보고됨
- 모든 label이 taxonomy version, snapshot/candidate digest, reviewer를 가짐
- 종료 강좌와 비강좌가 구분됨
- unlabeled/ambiguous를 TN으로 처리하지 않음

**위험**

- reason을 너무 세분화해 일관성이 낮아질 수 있다. v1은 운영 의사결정에 필요한 수준으로 제한하고 자유형 설명은 보조로 둔다.

### P0-2. 필터 전 Candidate Decision Trace

**문제**  
현재 저장 전에 거절되는 candidate는 로그 warning으로 사라지고 중앙 review가 어렵다.

**작업**

- `discover → extract → normalize → classify → validate → identity → coverage` stage 정의
- candidate stable identity와 content digest
- accept/reject/abstain, rule id, reason, evidence locator
- bounded trace sink와 redaction
- 다음 지점부터 계측:
  - generic row build/filter
  - generated row normalization
  - semantic guard
  - DB writer rejection
- accepted/rejected count reconciliation

**완료 기준**

- pilot provider의 source candidate 100%가 terminal decision을 가짐
- collected/saved/rejected/abstained 수가 합계로 일치
- rejected candidate를 Quality Inbox case로 전환 가능
- PII/secret이 trace에 없음

**위험**

- candidate volume이 커질 수 있다. DB에는 bounded index/summary를 두고 상세 trace는 압축 object로 저장하며 retention을 구분한다.

### P0-3. Release Generation Attribution

**문제**  
현재 shared quality row는 exact attempt/batch/release generation identity가 없어 timestamp로 release 품질을 추정할 수 없다.

**작업**

- snapshot, replay, quality result에 environment/provider/attempt/batch/artifact/config/rule/rollout/generation 필수화
- legacy 실행에는 명시적 `legacy_unattributed`가 아니라 가능한 batch identity를 생성
- quality analytics를 timestamp window가 아닌 exact FK/digest로 전환
- attribution missing 자체를 hard blocker metric으로 노출

**완료 기준**

- 모든 새 평가 결과의 exact identity coverage 100%
- generation별 precision/recall/coverage 조회 가능
- `generation_quality_attribution_unavailable` 공백을 새 quality path에서 제거

### P0-4. Pilot Provider와 Baseline

**선정 기준**

- generic HTML table/card, JSON list, detail enrichment를 각각 대표
- source가 public GET이며 PII 위험이 낮음
- 기존 strict pytest가 있어 baseline을 확인 가능
- output 규모가 너무 크지 않음
- 최근 quality issue나 improvement queue signal이 있음

**권장 범위**

- 3~5 provider
- positive/negative/abstain 각각 최소 사례
- pagination/detail/partial failure 경계 포함

**완료 기준**

- provider별 current decision distribution과 field completeness baseline
- audited-surface candidate census 또는 명시적 sampling plan
- 운영 owner와 검수 owner 지정

## 4. P1 작업 패키지

### P1-1. Raw Snapshot Quarantine와 Sanitizer

**작업**

- reviewed source catalog와 capture request schema
- existing safe outbound HTTP boundary 재사용
- raw encrypted content-addressed object store
- cookie/header/query/body secret 제거
- PII/private/applicant route deny
- media type/encoding/decompression/size limit
- sanitizer version, raw/sanitized digest, retention expiry
- break-glass raw access와 audit
- cryptographic erase 또는 object lifecycle policy

**완료 기준**

- 동일 response bytes가 동일 digest
- sanitizer 실패 시 fixture 생성 차단
- 테스트 PII/secret corpus leak 0
- UI는 기본적으로 sanitized object만 접근
- retention/restore/delete test 통과

### P1-2. Fixture Registry

**작업**

- fixture manifest schema
- conformance/provider golden/boundary/adversarial/rolling/holdout 분류
- expected request fingerprint와 response mapping
- reference clock/locale/timezone
- candidate/field truth label reference
- immutable member list와 detached signature
- stale fixture review date

**완료 기준**

- manifest 변경 시 fixture-set digest 변경
- label supersession이 기존 평가를 소급 변경하지 않고 새 fixture revision 생성
- holdout 권한이 author와 분리
- source 사용 조건과 retention owner가 기록됨

### P1-3. Networkless Replay Worker

**작업**

- no-network sandbox와 read-only fixture
- fixed clock/locale/random seed
- expected request matcher
- CPU/memory/time/output/DOM/decompressed-byte budget
- baseline/candidate artifact pin
- same input double-run determinism check
- bounded logs와 result object digest
- existing jobs/SSE pattern과 연동

**완료 기준**

- unexpected network 0
- 같은 identity의 반복 output digest 일치
- timeout/OOM/parser error가 bounded reason으로 종료
- replay worker에 primary DB credential과 signer key 없음
- fixture 밖 request와 사용되지 않은 mandatory response를 실패 처리

### P1-4. Row/Field/Coverage Diff와 Gate

**작업**

- source-native identity 우선 matching
- URL/title/date fallback identity와 collision 표시
- decision transition과 reason change
- normalized field before/after/source evidence
- coverage page/partition/count/detail/sentinel diff
- confusion matrix, abstain, label coverage
- hard block와 statistical/manual review 분리

**완료 기준**

- 목표 case의 fixed FP/FN이 명시됨
- new FP/FN과 unlabeled changes가 별도 queue로 표시
- identity ambiguity를 자동 match로 숨기지 않음
- source universe가 불완전하면 global recall을 표시하지 않음

### P1-5. Quality Workbench MVP

**화면**

1. Quality Inbox
2. Evidence & Label Viewer
3. Replay Lab
4. Read-only Review Evidence

**연동**

- 기존 improvement queue에서 provider/case deep-link
- quality issue/gap sample/rejected candidate에서 case 생성
- 기존 Studio source revision을 exact reference로 연결
- legacy validation은 별도 탭과 명확한 경고 유지

**완료 기준**

- 운영자가 한 case를 열어 snapshot→label→replay→review evidence를 추적
- raw access 없이 일반 검수가 가능
- unknown evidence가 0으로 보이지 않음
- 모든 mutation audit와 optimistic revision fence 적용

## 5. P2 작업 패키지

### P2-1. Rule Pack Schema와 Compiler

**작업**

- target schema와 rule-pack schema 분리
- global/family/provider/target/emergency precedence
- extract/normalize/classify/validate/identity/coverage stage
- evaluator/operator allowlist
- RE2/selector/JSON path/operation budget
- conflict detector와 unknown-field reject
- canonical JSON + definition SHA
- schema/evaluator/compiler version identity
- fuzz/adversarial test

**완료 기준**

- arbitrary code, network, filesystem, SQL, template expression 실행 불가
- global security deny override 불가
- 같은 precedence conflict 자동 숨김 없음
- canonical compile이 byte-deterministic
- error가 field/rule 위치와 bounded reason을 제공

### P2-2. Existing Semantic Evaluator 감싸기

**작업**

- `course_title_quality`, `generic_course_eligibility`, `course_semantic_eligibility`를 versioned evaluator key로 등록
- 기존 `course_registration_v1` 결과와 replay byte-equivalence 확인
- current reason/evidence를 rule trace contract로 변환
- provider override가 global fail-closed를 약화하지 않도록 분리

**완료 기준**

- representative corpus에서 current baseline 결과 동일
- evaluator version change가 artifact digest에 반영
- global safety regression fixture FP 0

### P2-3. Guided Rule Builder

**작업**

- scope/provider/target impact 고정 표시
- stage별 form editor
- matched fixture/case 수 실시간 preview
- compile errors와 conflict 시각화
- unsupported requirement를 Adapter change case로 전환
- YAML advanced mode는 strict validation
- author self-approval 차단

**완료 기준**

- pilot routine change의 80%가 Python 없이 후보 생성
- builder가 임의 source URL/secret/header를 받지 않음
- 저장한 revision이 canonical SHA와 결박
- replay 없이는 review submit 불가

### P2-4. Exact Review, Builder, Signer Handoff

**작업**

- rule revision + Studio source SHA + fixture-set + replay-result digest 묶음
- author/reviewer/approver 분리
- approval receipt TTL과 exact digest
- isolated builder가 승인 evidence를 검증
- signed artifact의 config revision에 rule digest 포함
- existing crawler release artifact/action schema와 binding

**완료 기준**

- evidence 중 하나가 바뀌면 approval 무효
- Quality API가 sign/build/rollout 권한을 갖지 않음
- current disabled build/register capability를 명시적 readiness gate 뒤에서만 활성화
- artifact에서 rule/source/evaluator version을 역추적

### P2-5. Legacy Staging Shadow

**작업**

- 같은 capture에 baseline/candidate dual run
- candidate는 staging-only credential
- current promotion validation과 fingerprint 재사용
- quality gate 결과를 batch에 exact binding
- 수동 advance 기본, 자동 pause/rollback alert

**완료 기준**

- candidate primary DML 0
- incomplete coverage에서 close 0
- staging diff와 offline replay 불일치 시 hold
- approval 전 promotion 0

### P2-6. Adapter Registry와 Giant Dispatch 분해

**작업**

- provider/family matcher registry
- 기존 collector wrapper로 behavior-preserving 이동
- source adapter contract와 decision trace
- 한 provider씩 `collect_from_url` branch 제거
- registry completeness/duplicate ownership test

**완료 기준**

- 각 migration은 기존/신규 golden output digest equivalence
- exactly one adapter owner
- fallback 경로와 blast radius 명시
- rollback 시 기존 branch로 돌아갈 수 있음

## 6. P3 작업 패키지

### P3-1. Distributed Canary

분산 worker 활성화 readiness는 별도 프로젝트로 관리한다.

- signed artifact trust/bootstrap
- worker heartbeat와 generation report
- backup/restore와 forced rollback drill
- resource limit와 lease/fencing
- canary→stable manual advance

현재 topology가 disabled이므로 P1/P2 MVP의 선행 조건이 아니다.

### P3-2. Drift Sampling과 Active Review

- 신규 candidate, changed candidate, new reject reason, selector anomaly를 층화 표본
- sampling probability 기록
- label backlog/freshness SLO
- rolling fixture는 자동 golden 승격 금지
- high-risk provider census 우선

### P3-3. Batch Change Ledger와 Data Repair

- inserted/updated/closed의 crawler-owned before/after
- compare-and-swap repair
- primary-owned field 보존
- corrective forward batch 우선
- repair approval과 별도 audit

### P3-4. Rule Suggestion

AI/통계 기반 추천은 P3 이후 고려한다.

- 제안만 생성, 자동 저장/승인/배포 금지
- 근거 case와 예상 영향 표시
- holdout 결과를 추천 모델에 노출하지 않음
- prompt/input에 raw PII/secret 금지

## 7. 팀과 책임

| 역할 | 주 책임 |
|---|---|
| Product/Quality Owner | taxonomy, priority, SLO, provider risk tier |
| Crawler Engineer | adapter contract, capture, provider migration |
| Data/Backend Engineer | control schema, API, replay result, metrics |
| Frontend/Ops Engineer | Inbox, Evidence Viewer, Rule Builder, Replay Lab |
| Security/Platform | sandbox, object ACL, signer, RLS, audit, retention |
| Label Reviewer | truth guideline, independent review, adjudication |
| Release Operator | staging/canary/rollback, evidence verification |

최소 독립성:

- rule author ≠ rule approver
- high-risk label author ≠ adjudicator
- release proposer ≠ isolated action approver
- replay worker ≠ builder/signer

## 8. Provider 전환 전략

| 유형 | 예 | 전환 |
|---|---|---|
| Generic static HTML | table/card | Rule Pack 우선, fastest pilot |
| Generic JSON list | public API | restricted JSON path + pagination contract |
| Dedicated public collector | 복잡한 여러 endpoint | Adapter 유지, classify/normalize부터 Rule Pack |
| Browser/CSRF/session | stateful site | 코드 Adapter 유지, snapshot/replay contract만 공통화 |
| PII/private risk | 신청자/관리자 surface | capture deny 또는 별도 비공개 검토, publish hard block |

모든 provider를 DSL 비율로 평가하지 않는다. 중요한 것은 동일 evidence/replay/release 계약을 준수하는지다.

## 9. Risk Register

| 위험 | 가능성 | 영향 | 완화 |
|---|---|---|---|
| raw fixture에 PII/secret 포함 | 중 | 매우 큼 | capture deny, sanitizer hard gate, raw quarantine |
| DSL이 제2의 프로그래밍 언어로 비대해짐 | 중 | 큼 | 제한 operator, unsupported→Adapter |
| label 편향으로 수치가 좋아 보임 | 큼 | 큼 | stratified sample, holdout, coverage/CI |
| giant dispatch migration 회귀 | 큼 | 큼 | wrapper-first, provider별 equivalence |
| rule scope가 예상보다 넓음 | 중 | 큼 | impact preview, provider default, global separate approval |
| object/replay 비용 증가 | 중 | 중 | retention tier, compression, changed-surface sampling |
| Studio legacy validation 오해 | 큼 | 중 | 명칭/배지 분리, draft SHA 없는 결과 승인 금지 |
| rollback이 이미 반영된 데이터를 못 고침 | 큼 | 큼 | data repair ledger, forward correction |
| distributed readiness와 일정 결합 | 중 | 큼 | legacy-first architecture |

## 10. Go/No-Go 기준

### P0→P1 Go

- taxonomy와 reviewer guideline 승인
- candidate terminal decision coverage가 pilot에서 100%
- PII/secret trace leak 0
- exact release identity model 합의

### P1→P2 Go

- deterministic replay mismatch 0
- fixture signature/sanitizer/retention test 통과
- baseline/candidate diff가 목표 FP/FN과 new regression을 구분
- source universe 불완전 시 metric 명칭이 fail-closed
- Quality Workbench audit/RBAC 검증

### P2 Production Rule Go

- global safety regression 0
- independent review와 exact digest receipt
- signed artifact 및 staging shadow
- no new unlabeled critical change
- coverage complete, close policy safe
- rollback drill 성공

### No-Go

- draft/source/rule/fixture/result digest 중 하나라도 불명
- same input replay가 비결정적
- sanitizer 결과 불명 또는 PII detector hit
- sample/label coverage가 없는데 precision/recall을 주장
- source count 급락 원인을 설명하지 못함
- 작성자가 자기 변경을 승인

## 11. KPI와 완료 지표

### 속도

- case 생성→최종 label 중앙값
- label→검증 가능한 candidate rule 중앙값
- review cycle time
- provider onboarding time

### 안전

- release evidence identity coverage
- critical fixture FP
- new unlabeled drops
- nondeterministic replay
- PII/security gate
- incomplete close 시도
- rollback/repair 시간

### 품질

- provider별 labeled precision/recall + CI
- audited-surface recall과 coverage proxy
- field correctness/completeness
- abstain rate
- label coverage/freshness/agreement

### 유지보수

- routine no-Python change 비율
- `collect_from_url` branch 감소
- rule override age/expiry 준수
- fixture stale ratio
- decision trace reason unknown 비율

## 12. 추천 첫 분기 Backlog

1. Taxonomy/guideline ADR
2. Pilot provider 3~5개와 reviewer 지정
3. Candidate/decision trace envelope
4. Control DB case/label/snapshot/replay identity migration 설계
5. Sanitizer threat model과 test corpus
6. Fixture manifest schema/CLI
7. Networkless replay prototype
8. Baseline/candidate row/field/coverage diff
9. Quality Inbox/Evidence Viewer 최소 UI
10. Exact-digest review proof of concept
11. 기존 semantic evaluator wrapper
12. Staging shadow runbook과 rollback tabletop

첫 분기의 성공은 production 자동 편집이 아니라, 한 실제 오탐과 한 실제 미탐을 동일한 증거 사슬로 재현·수정·검토할 수 있음을 보이는 것이다.

## 13. 최종 권고

P0/P1에 투자해 “정답과 재현”을 먼저 만든다. P2에서 Rule Pack과 UI를 열고, provider별 migration은 generic family부터 점진 수행한다. P3 canary와 자동화는 evidence chain과 운영 drill이 안정된 뒤 진행한다.

가장 위험한 지름길은 현재 Studio source draft를 바로 실행하거나, field completeness를 precision/recall로 사용하거나, live source 두 번 실행을 전후 diff로 간주하는 것이다. 이 세 가지를 architecture gate에서 명시적으로 차단한다.

