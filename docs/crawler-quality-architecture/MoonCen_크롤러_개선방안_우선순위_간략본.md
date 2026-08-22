# MoonCen 크롤러 개선방안·우선순위 간략본

> 우선순위의 기준은 “편집 화면을 얼마나 빨리 만들 수 있는가”가 아니라 **오탐을 줄이는 동안 미탐을 늘리지 않았음을 증명하고 안전하게 되돌릴 수 있는가**이다.

## 1. 한눈에 보는 우선순위

| 우선순위 | 작업 | 효과 | 완료 기준 |
|---|---|---|---|
| P0 | FP/FN·필드 오류 taxonomy와 검수 지침 | 팀이 같은 기준으로 판정 | 모든 label에 taxonomy version, snapshot/candidate identity, reason, reviewer가 있음 |
| P0 | 필터 전 candidate와 decision trace 보존 | 미탐을 관측할 수 있음 | include/exclude/abstain 모두 rule id/reason/evidence가 남음 |
| P0 | 평가 결과를 attempt/batch/artifact/config/rule/generation에 귀속 | release별 개선·회귀 판별 | timestamp 추정 없이 exact identity로 조회 가능 |
| P1 | raw snapshot quarantine, sanitizer, fixture registry | 동일 입력 재현과 개인정보 차단 | content SHA, signed manifest, PII hard gate, 보존기간/ACL 검증 |
| P1 | networkless baseline/candidate replay와 row/field diff | 변경 영향 자동 확인 | 동일 입력 반복 output digest 일치, 예상 밖 network 0 |
| P1 | Quality Inbox·Label Viewer·Replay Lab MVP | 운영자가 한 화면에서 사례를 해결 | case→label→replay→review 링크가 끊기지 않음 |
| P2 | strict Rule Pack schema/compiler/runtime | 단순 오탐·미탐 수정 속도 향상 | unknown field/operator 차단, code/network/filesystem 실행 불가 |
| P2 | Guided Rule Builder와 독립 approval | 비개발자의 안전한 수정 | 작성자 자기 승인 불가, 모든 승인 SHA fence 적용 |
| P2 | legacy staging shadow와 품질 gate | primary 영향 없이 live drift 확인 | primary write 0, baseline/candidate가 같은 capture를 비교 |
| P3 | 분산 canary 연결·자동 pause/rollback | 점진 배포 | worker activation gate 완료 후 generation-bound evidence 확보 |
| P3 | batch change ledger·조건부 data rollback | 이미 반영된 오류 복구 | before/after hash와 CAS 기반 보정, primary-owned 필드 보존 |
| P3 | drift sampling·active review | 미탐의 지속 탐지 | 신규/변경/거부 candidate가 층화 표본으로 inbox에 유입 |

## 2. 추천 MVP

P0와 P1을 하나의 MVP로 묶는다.

```text
Quality Case
 → sanitized frozen fixture
 → record/field label
 → baseline/candidate offline replay
 → row/field/coverage diff
 → independent review evidence
```

MVP 범위:

- Generated/YAML·generic 경로의 3~5개 대표 provider
- 행 단위 `eligible/non_course/ambiguous/unverifiable`
- 필드 단위 `correct/incorrect/missing/not_applicable/unverifiable`
- include/exclude/abstain prediction과 안정된 reason code
- HTML/JSON fixture, list/detail/pagination/partial failure 경계 fixture
- 현재 adapter+rule baseline과 후보의 동일 입력 비교
- UI는 case, evidence/label, replay diff, review까지만 제공
- 실제 production 자동 승격과 브라우저 Python 실행은 제외

## 3. 왜 Rule Builder가 P2인가

정답셋과 리플레이가 없는 Rule Builder는 “수정하기 쉬운 회귀 생성기”가 될 수 있다. P1에서 다음을 먼저 증명해야 한다.

- dropped candidate까지 관측되는가
- 같은 fixture를 반복했을 때 결과가 같은가
- 기존/후보 rule의 차이를 행·필드·완전성 단위로 설명하는가
- critical FP, PII, unexpected request, coverage 실패를 막는가
- 승인자가 결과와 SHA를 재확인할 수 있는가

이 조건 뒤에 Rule Builder를 열면 개발자 의존성을 줄이면서 안전성도 유지할 수 있다.

## 4. 실행 순서와 예상 기간

기간은 1개 전담 스쿼드 기준의 계획 범위이며 확정 일정이 아니다.

| 구간 | 권장 기간 | 결과 |
|---|---|---|
| 0. 기준 확정 | 1~2주 | taxonomy, label guideline, provider pilot, baseline |
| 1. 증거·라벨 | 2~4주 | snapshot/candidate/label store, annotation workflow |
| 2. Replay MVP | 4~6주 | sandbox, fixture, diff, hard gate, evaluation API |
| 3. Rule Pack·UI | 4~8주 | compiler/runtime, guided editor, independent review |
| 4. Shadow·운영화 | 3~6주 | staging shadow, dashboard, runbook, alert, rollback drill |
| 5. 확장 | 지속 | provider migration, canary activation, drift sampling |

각 구간은 앞 단계 완료 기준을 통과한 뒤 진행한다. 일정을 맞추기 위해 provenance나 safety gate를 생략하지 않는다.

## 5. 영향 대비 난이도

| 항목 | 영향 | 난이도 | 추천 |
|---|---|---|---|
| taxonomy + decision trace | 매우 큼 | 중 | 즉시 시작 |
| fixture + replay + diff | 매우 큼 | 큼 | 핵심 투자 |
| generation attribution | 매우 큼 | 중~큼 | release 분석과 함께 P0 |
| Guided Rule Builder | 큼 | 중 | replay 뒤에 진행 |
| full DSL migration | 중~큼 | 매우 큼 | generic provider부터 점진 전환 |
| distributed canary | 큼 | 매우 큼 | 별도 activation readiness 뒤에 진행 |
| AI rule suggestion | 불확실 | 중~큼 | P3 이후, 제안만 허용 |

## 6. 하지 말아야 할 것

- Crawler Studio 초안 Python을 API process에서 직접 실행
- 브라우저가 URL, cookie, header, secret, shell/SQL/Python expression을 rule로 입력
- live source를 서로 다른 시각에 실행한 결과를 baseline/candidate diff로 간주
- field completeness 점수를 precision/recall로 표시
- unlabeled candidate를 TN으로 가정
- 불완전 pagination/detail 결과로 missing course를 종료 처리
- 작성자·라벨러·release 승인자를 한 사람으로 고정
- mutable DB rule을 digest 없이 runtime에서 즉시 읽음

## 7. 투자 승인용 완료 지표

- `100%` rule release: fixture replay + independent approval + exact rule/artifact digest
- `100%` 평가 결과: provider/snapshot/adapter/rule/reference clock/generation 귀속
- critical safety fixture FP `0`
- sanitizer/fixture PII 유출 `0`
- production primary 직접 쓰기 `0`
- 일상 수정 중 no-Python 비율 `≥80%` 목표
- label→검증 후보 중앙값 `≤1영업일` 목표
- rollback drill 분기 1회 이상, rule rollback `≤10분` 목표

표본 수, label coverage, 신뢰구간을 충족하지 못하면 “목표 달성” 대신 “근거 불충분”으로 표시한다.

