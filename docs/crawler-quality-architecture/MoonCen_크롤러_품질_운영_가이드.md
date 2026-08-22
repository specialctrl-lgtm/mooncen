# MoonCen 크롤러 품질 운영 가이드

> 이 가이드는 두 상태를 구분한다. **현재 즉시 적용할 임시 절차**는 현 저장소 기능으로 할 수 있는 범위이고, **목표 운영 절차**는 본 문서 묶음의 Quality Workbench가 구현된 뒤 적용한다. 구현되지 않은 API나 화면을 현재 존재하는 기능처럼 사용하면 안 된다.

## 1. 운영 원칙

1. 원본 증거 없는 오탐·미탐 수정은 긴급 차단 외에는 승인하지 않는다.
2. 정답 라벨 없는 지표를 precision/recall이라고 부르지 않는다.
3. baseline과 candidate는 반드시 동일 snapshot, adapter, clock을 사용한다.
4. global rule보다 provider/target scope를 기본값으로 한다.
5. 완전하지 않은 snapshot은 missing-course closure 근거가 될 수 없다.
6. 작성자는 자기 rule revision을 승인하지 않는다.
7. 수집 성공과 primary promotion은 별도 사건이다.
8. rule rollback과 이미 반영된 데이터 복구는 별도 절차다.
9. raw source는 최소 수집·비식별·암호화·기간 제한한다.
10. 증거가 부족하면 성공/0이 아니라 `unknown` 또는 `blocked`로 기록한다.

## 2. 현재 즉시 적용할 임시 절차

Quality Workbench 구현 전에도 다음 운영 규칙을 적용한다.

### 2.1 Studio 검증 해석

- Crawler Studio의 source draft/revision/review는 변경 기록으로 사용한다.
- Studio 화면의 legacy validation은 **현재 배포·등록된 crawler 점검**일 뿐 해당 draft를 실행한 결과가 아니다.
- 일반 crawler worker가 `dry_run/review`를 거부하므로 성공한 draft replay 증거로 표시하지 않는다.
- fixture validation/source execution/build/sign capability가 unavailable이면 수동 테스트와 기존 release 절차로 fail-closed한다.

### 2.2 임시 오탐/미탐 티켓 필수 항목

- provider와 target/source URL
- 발견 시각과 환경
- 행/필드/duplicate/lifecycle 중 문제 유형
- source가 실제로 보여 주는 기대값
- 현재 출력/누락 결과
- sanitized screenshot 또는 최소 HTML/JSON fragment
- 예상 수정 위치: target config / common semantic / generic parser / provider collector / identity / coverage
- 영향 provider 범위
- 추가한 regression test
- staging 검증과 rollback 계획

### 2.3 임시 변경 분류

| 문제 | 우선 위치 | 검증 범위 |
|---|---|---|
| 잘못된 target URL/status | target YAML/registry | target validator, wrapper/registry validation, provider test |
| provider 한정 title/category filter | provider collector 또는 scoped helper | 해당 provider fixture + common safety suite |
| generic filter 변경 | generic eligibility/filter | 모든 generic provider 대표 test + semantic suite |
| global semantic 변경 | common semantic policy | 전체 semantic regression + critical negative corpus |
| field mapping/normalization | provider/generic parser | source fixture field truth + date/fee/URL guards |
| pagination/detail 누락 | Adapter/collector code | page/sentinel/partial failure/atomic snapshot |
| identity/dedupe | identity guard | collision, alias, raw URL, historical row test |

### 2.4 임시 release 금지 조건

- reproducer fixture/test가 없음
- 수정 전 실패·수정 후 성공을 확인하지 못함
- provider-scoped change가 가능한데 global regex를 변경
- incomplete pagination/detail을 성공으로 처리
- 현재 배포본 legacy validation만으로 draft가 검증됐다고 주장
- rollback 기준과 owner가 없음

## 3. 역할과 책임

| 역할 | 책임 | 독립성 |
|---|---|---|
| Triage Operator | case 접수, 증거 확인, provider/심각도/owner 지정 | release 승인 안 함 |
| Labeler | snapshot candidate의 1차 truth | rule author와 분리 권장 |
| Adjudicator | 불일치·고위험 label 확정 | 1차 labeler와 분리 |
| Rule/Adapter Author | 수정안, 설명, replay 요청 | 자기 revision 승인 금지 |
| Rule Reviewer | diff·gate·영향 검토 | author와 분리 |
| Release Proposer | staging/canary/rollback 요청 | isolated approver와 분리 |
| Isolated Approver | exact action digest 승인 | API/worker/control 역할 금지 |
| Incident Commander | 품질 장애 containment·소통 | 변경 author일 필요 없음 |
| Security Owner | raw access, PII incident, retention | break-glass 승인 |

## 4. 심각도와 SLA 예시

| 등급 | 사례 | 초기 대응 목표 | 기본 조치 |
|---|---|---|---|
| SEV-1 | PII/private content 노출, 대규모 잘못된 close, 광범위 오탐 | 즉시 | promotion 중지, provider quarantine, 보안/IC 호출 |
| SEV-2 | 다수 provider 회귀, 주요 provider 대량 FP/FN | 30분 이내 triage | canary pause/rollback, batch hold |
| SEV-3 | 한 provider의 지속 FP/FN, 중요 field 오류 | 1영업일 | case+snapshot+rule/adapter 수정 |
| SEV-4 | 소수 edge case, 개선 요청 | backlog SLA | 층화 검수와 정기 release |

목표 시간은 실제 당직 체계와 위험도에 맞춰 확정한다.

## 5. 표준 오탐 처리

### 5.1 Triage

1. 실제 비강좌인지, 강좌지만 lifecycle/duplicate/publish policy 때문에 제외해야 하는지 분리한다.
2. source snapshot과 candidate boundary를 고정한다.
3. 현재 decision stage/rule/reason을 확인한다.
4. 이미 primary에 반영되었는지와 사용자 영향 범위를 확인한다.
5. PII/private/authenticated content이면 일반 case가 아니라 보안 incident로 승격한다.

### 5.2 Label

- semantic: `non_course`
- publish: 대개 `exclude`
- bounded reason과 source locator
- field 오류가 함께 있으면 별도 field truth
- duplicate이면 canonical relation을 별도 기록

### 5.3 수정 경로

- 특정 provider/title/category: provider rule
- source family 공통 현상: family rule, 대표 provider 전체 replay
- global hard-negative: security owner와 광범위 corpus 검토
- transport/pagination/protocol: Adapter code
- 긴급 단일 행: 만료 시간이 있는 quarantine/override

### 5.4 검증

- 해당 FP가 baseline include→candidate exclude
- 같은 reason slice의 실제 강좌가 새 FN이 되지 않음
- unlabeled new drop은 review queue
- field/identity/coverage non-regression
- critical safety fixture와 holdout 통과

### 5.5 Release 후

- canary에서 candidate/output/reason 분포 확인
- 잘못된 row가 이미 primary에 있으면 corrective batch
- case는 release/generation과 연결하고 관찰 창 뒤에 닫음

## 6. 표준 미탐 처리

미탐은 먼저 “어느 단계에서 사라졌는가”를 찾는다.

### 6.1 Source universe 확인

- source surface/partition/page에 실제 항목이 있었는가
- advertised total, sitemap, category tabs, API total이 있는가
- snapshot 시점에 course가 current/future였는가
- source 자체가 일시적으로 누락한 것은 아닌가

전체 universe를 알 수 없으면 `global FN`이 아니라 `audited surface FN`으로 기록한다.

### 6.2 Stage diagnosis

```text
target enabled?
  → request success?
  → partition/page visited?
  → item candidate discovered?
  → detail fetched?
  → fields extracted?
  → semantic decision?
  → identity collision?
  → snapshot complete?
  → staging/promotion held?
```

| 마지막으로 관측된 단계 | 주 수정 위치 |
|---|---|
| target 없음/disabled | config/source catalog |
| request 실패 | Adapter transport/session |
| page/partition 누락 | pagination/coverage contract |
| source에는 있으나 candidate 없음 | discovery/extract rule 또는 Adapter |
| candidate가 exclude | classify/semantic rule |
| candidate가 include지만 row 없음 | validate/identity/writer |
| staging에 있으나 primary 없음 | gate/approval/promotion |

### 6.3 Label과 검증

- semantic `eligible_course`
- publish `publish` 또는 안전 사유가 있으면 `hold`
- lifecycle과 field truth
- root-cause stage
- baseline exclude/absent→candidate include
- 같은 scope에서 FP 증가 여부
- page/detail/identity completeness

### 6.4 미탐 지표 해석

- candidate가 발견된 범위: classification recall
- frozen source 전체를 검수한 범위: audited-source recall
- 전체 기관 강좌: 독립 catalogue가 없으면 측정 불가

coverage proxy를 recall로 이름 바꾸지 않는다.

## 7. 필드 오류 처리

1. source에 해당 field 근거가 실제 존재하는지 확인한다.
2. `incorrect`, `missing`, `not_applicable`, `unverifiable` 중 하나를 선택한다.
3. extraction locator와 raw value, normalized value를 비교한다.
4. placeholder(`별도 안내` 등)를 추출 성공으로 세지 않는다.
5. normalize rule은 pure transform으로 제한하고 reference clock을 고정한다.
6. field diff가 identity/publish decision을 바꾸는지 확인한다.
7. provider field truth corpus와 common date/fee/URL guard를 replay한다.

## 8. Rule 수정 표준 절차

### 8.1 Draft

- case와 final label 선택
- 최소 scope 선택
- existing rule/adapter version과 expected impact 확인
- change reason, owner, expiry/review date
- compile/static validation

### 8.2 Replay

- fixture-set digest 확인
- baseline/candidate exact identity 확인
- reference clock/locale 동일 확인
- determinism double-run
- fixed/new/unchanged FP/FN
- field/identity/coverage/security 결과
- unlabeled changes review

### 8.3 Review

Reviewer checklist:

- [ ] target case가 실제로 고쳐짐
- [ ] scope가 필요 이상 넓지 않음
- [ ] global safety가 완화되지 않음
- [ ] new critical FP/FN 없음
- [ ] unlabeled change가 해소 또는 명시적으로 hold됨
- [ ] source coverage와 close policy가 안전함
- [ ] rule/fixture/replay result SHA가 정확함
- [ ] author와 reviewer가 다름
- [ ] rollback revision이 지정됨

### 8.4 Build/Release

- 승인 evidence를 isolated builder가 검증
- canonical rule digest를 signed artifact/config revision에 포함
- staging shadow
- promotion held
- exact batch fingerprint와 별도 승인
- observation window 후 case close

## 9. Adapter 코드 수정 절차

다음은 Rule Pack이 아니라 Adapter 변경으로 처리한다.

- 로그인/세션/CSRF
- JS browser interaction
- signed/private API protocol
- multi-endpoint state machine
- complex cursor/retry behavior
- source-specific anti-bot 대응

추가 검증:

- safe outbound HTTP policy
- request budget, timeout, redirect, TLS
- pagination sentinel과 partial failure
- raw capture redaction
- fixture replay request matching
- resource limit
- 기존 provider output byte-equivalence 또는 명시적 diff
- cross-provider shared adapter 영향

## 10. Release Gate 운영

### 10.1 Hard Gate

아래 중 하나라도 발생하면 release를 차단한다.

- critical negative fixture FP
- PII/private/authenticated content
- unexpected network
- signature/digest/manifest mismatch
- nondeterministic output
- parser/schema/operation budget 실패
- global safety override
- coverage contract 실패
- exact generation attribution 누락
- approved closure fingerprint mismatch

### 10.2 통계 Gate

provider risk tier별 정책을 사용한다.

| Tier | 예 | 권장 정책 |
|---|---|---|
| High | 대량 사용자 노출, PII 인접, close 영향 큼 | full/census 검수 우선, 2인 label, manual advance |
| Medium | 일반 public course provider | stratified sample + holdout + manual review |
| Low | 작은 public source, 영향 제한 | representative golden + rolling drift |

분모가 작으면 비율보다 절대 FP/FN과 신뢰구간을 우선한다. `FP=0`이 표본 충분성을 뜻하지 않는다.

### 10.3 Count/Coverage Gate

- current 65% 급락 방어 등 기존 count gate 유지
- advertised/observed ratio
- partition/page/detail/sentinel
- new/existing/closed candidate delta
- identity collision
- 불완전하면 close-missing 금지

## 11. Canary와 모니터링

### Legacy 단계

1. offline replay
2. staging shadow
3. same-capture baseline/candidate dual comparison
4. held batch review
5. exact fingerprint promotion

### Distributed 준비 이후

1. canary worker generation pin
2. minimum observation window
3. health + truth quality + coverage gate
4. manual advance
5. stable cohort

### Canary 관찰 항목

- output/candidate/rejected/abstain count
- reason distribution
- selector zero/multi
- page/detail/request count와 bytes
- run duration/error/timeout
- labeled precision/recall과 new critical case
- staging insert/update/close
- artifact/config/rule/generation identity

## 12. Pause와 Rollback

### 즉시 Pause 신호

- PII/security detector
- critical FP
- count 급락 또는 zero result
- source coverage incomplete
- generation/artifact identity 불명
- deterministic mismatch
- canary worker health 악화

### Rule/Artifact Rollback

1. 새 promotion 중지
2. 이전 approved artifact/config/rule revision을 새 generation으로 pin
3. worker/report identity가 baseline과 일치하는지 확인
4. baseline shadow 결과 확인
5. terminal rollback evidence와 incident link 저장

### Data Repair

1. 영향 batch와 inserted/updated/closed 행 식별
2. primary-owned field/user interaction을 제외
3. 현재 hash가 batch after hash와 같은 행만 자동 대상
4. 가능하면 baseline rule로 corrective forward batch 생성
5. CAS mismatch는 수동 review
6. 별도 승인과 audit

코드 rollback 성공을 데이터 복구 완료로 간주하지 않는다.

## 13. 품질 Incident Runbook

### SEV-1: PII/private content

1. provider promotion/crawl pause
2. affected object와 UI 접근 차단
3. security owner/IC 호출
4. raw/fixture/audit access log 보존
5. primary 노출 행 quarantine
6. leak surface와 downstream cache 조사
7. disclosure/법적 절차 판단
8. sanitizer/adversarial fixture 보강
9. 독립 승인 후 재개

### SEV-2: 대량 오탐

1. release/generation/rule digest 확인
2. canary/stable pause 및 previous rule pin
3. source snapshot과 decision reason 분포 확보
4. primary 영향 batch hold/repair
5. 최소 scope rule 수정과 replay
6. incident review와 global rule blast radius 분석

### SEV-2: 대량 미탐/zero

1. close-missing 즉시 금지
2. source availability와 request/partition/page/detail 확인
3. count/sentinel/identity와 last complete snapshot 비교
4. transport/adapter/rule/coverage stage 분리
5. 이전 artifact로 shadow
6. recovery 전까지 stale data 보존

### Evidence Store/Replay 장애

- 새 rule release를 fail-closed
- 현재 안정 artifact는 계속 운영
- raw 접근 우회나 live two-run diff로 대체하지 않음
- backlog와 oldest age alert

## 14. 정기 운영 주기

### 매일

- SEV-1/2, critical FP, PII gate
- provider zero/급락, incomplete snapshot
- replay queue oldest age
- attribution missing
- promotion hold age

### 매주

- improvement queue P0/P1
- new reject reason와 unlabeled changes
- label backlog/agreement
- rolling fixture drift
- expiring suppression/override
- provider별 FP/FN·field error review

### 매월

- high-risk provider audited-source review
- fixture freshness/retention/restore sample
- raw break-glass audit
- rule override age와 owner
- adapter/dispatch migration 진척
- SLO와 incident action review

### 분기

- forced rule rollback drill
- data repair tabletop
- sandbox/PII/adversarial test
- isolated approver credential/role review
- backup/restore drill
- taxonomy와 sampling policy review

## 15. Dashboard 권장 구성

### Executive

- provider risk와 open case
- labeled precision/recall + label coverage/CI
- critical FP/FN
- release evidence coverage
- rollback/incident

### Quality Operations

- case age/owner/status
- reason/root-cause distribution
- label backlog/agreement
- fixed/new FP/FN
- field accuracy

### Source Coverage

- partition/page/detail/sentinel
- advertised/observed
- selector drift
- candidate/output delta

### Release

- source/rule/fixture/result/artifact/generation chain
- staging/canary/stable
- promotion holds
- pause/rollback

unknown evidence는 초록색 0으로 표시하지 않는다.

## 16. 보존·접근 권장안

실제 기간은 법무·보안·source 이용 조건과 함께 확정한다.

| 데이터 | 기본 접근 | 권장 보존 원칙 |
|---|---|---|
| Raw quarantine | break-glass only | 가장 짧게, 자동 lifecycle |
| Sanitized golden fixture | quality/replay roles | active rule 수명 + audit 요구 |
| Rolling drift fixture | quality/replay roles | 짧은 rolling window |
| Label/review/result digest | viewer 이상 | 장기 append-only |
| Detailed diff/log | bounded viewer | release/incident 보존 정책 |
| Audit/approval | audit/security | 규정과 release 추적 기간 |

object 삭제 후에도 digest/삭제 시각/승인자는 metadata에 남기되 raw 내용은 남기지 않는다.

## 17. 운영 Checklists

### Case 완료

- [ ] snapshot/candidate identity
- [ ] final truth와 reason
- [ ] root-cause stage
- [ ] linked rule/adapter revision
- [ ] baseline/candidate replay
- [ ] new regression 검토
- [ ] reviewer approval
- [ ] release/generation 또는 no-change reason
- [ ] 관찰 창과 close note

### Production Promotion

- [ ] signed artifact/config/rule digest
- [ ] valid fixture/replay result
- [ ] hard gate 통과
- [ ] label coverage/sample 적정
- [ ] staging complete/fingerprint exact
- [ ] close-missing 안전
- [ ] independent approval receipt
- [ ] rollback artifact와 owner
- [ ] dashboard/alert 준비

### Incident 종료

- [ ] containment 완료
- [ ] 데이터 영향 조사/repair
- [ ] evidence 보존
- [ ] root cause
- [ ] regression fixture
- [ ] alert/runbook 개선
- [ ] owner/due date
- [ ] 독립 재발 방지 검증

## 18. 운영상 가장 중요한 경고

- field가 채워졌다는 사실은 값이 맞다는 뜻이 아니다.
- candidate가 없다는 사실은 source에 강좌가 없다는 뜻이 아니다.
- `snapshot_complete`는 정의된 surface 계약의 완전성이지 기관의 전 세계적 catalogue truth가 아니다.
- 현재 Studio draft와 legacy validation 결과는 서로 결박되어 있지 않다.
- 이전 rule로 돌렸다고 이미 반영된 데이터가 자동 복구되지는 않는다.

이 다섯 문장을 dashboard와 review UI의 도움말로 고정하는 것을 권장한다.

