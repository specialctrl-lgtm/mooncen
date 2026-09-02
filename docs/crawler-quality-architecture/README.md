# MoonCen 크롤러 품질 아키텍처 문서 묶음

이 디렉터리는 크롤러의 오탐·미탐과 필드 오류를 더 빠르고 안전하게 수정하기 위한 목표 아키텍처와 도입·운영 방안을 담는다. 문서는 2026-08-19 UTC의 로컬 워크스페이스를 정적으로 분석해 작성했다.

> 중요: 문서의 **현재 사실(AS-IS)** 과 **제안(TO-BE)** 을 구분해서 읽어야 한다. 저장소에 코드·설정이 있다는 사실은 해당 기능이 운영 환경에서 활성화되었다는 증거가 아니다. 현재 선언 토폴로지는 `crawlerMode=legacy`이며 분산 worker는 비활성이다.

## 문서 구성

| 문서 | 권장 독자 | 열람/편집 | Word 다운로드 |
|---|---|---|---|
| 품질 아키텍처 간략본 | 의사결정자·PO·운영 책임자 | [Markdown](./MoonCen_크롤러_품질_아키텍처_간략본.md) | [Word](./MoonCen_크롤러_품질_아키텍처_간략본.docx) |
| 품질 아키텍처 상세본 | 백엔드·크롤러·데이터·보안 개발자 | [Markdown](./MoonCen_크롤러_품질_아키텍처_상세본.md) | [Word](./MoonCen_크롤러_품질_아키텍처_상세본.docx) |
| 개선방안·우선순위 간략본 | 일정·투자 결정자 | [Markdown](./MoonCen_크롤러_개선방안_우선순위_간략본.md) | [Word](./MoonCen_크롤러_개선방안_우선순위_간략본.docx) |
| 개선방안·우선순위 상세본 | 기술 리드·실행 팀 | [Markdown](./MoonCen_크롤러_개선방안_우선순위_상세본.md) | [Word](./MoonCen_크롤러_개선방안_우선순위_상세본.docx) |
| 품질 운영 가이드 | 운영자·검수자·당직자 | [Markdown](./MoonCen_크롤러_품질_운영_가이드.md) | [Word](./MoonCen_크롤러_품질_운영_가이드.docx) |

각 문서는 편집 가능한 Markdown과 배포 가능한 Word(`.docx`) 두 형식으로 제공한다. 도식은 편집 가능한 SVG와 Word 삽입용 PNG를 함께 제공한다.

## 가장 중요한 결론

브라우저에서 Python을 직접 고치고 실행하는 방식은 목표가 아니다. 일상적인 수정은 제한된 **Rule Pack**으로 이동하고, 복잡한 로그인·세션·브라우저 동작·API 서명만 검토된 **Source Adapter 코드**로 남긴다.

```text
동결된 원본 증거
  → 검수 라벨/정답셋
  → 안전한 룰 초안
  → 동일 입력의 기존/후보 리플레이
  → 행·필드·완전성 차이와 품질 gate
  → 독립 승인·불변 artifact
  → 스테이징 shadow/canary
  → 기존 fingerprint 승격 gate
  → 모니터링·룰 revision 롤백
```

정답 라벨 없이 precision/recall을 주장하지 않고, 완전한 source enumeration 근거 없이 “전체 미탐률”이라고 부르지 않는 것이 설계의 핵심 원칙이다.

## 현재 코드에서 확인된 핵심 제약

- Crawler Studio는 append-only 소스 보관/리뷰만 제공하고 fixture 실행, source 실행, build, sign, rollout은 제공하지 않는다.
- Studio의 “검증 실행”은 선택한 초안 revision이 아니라 현재 서버 registry의 크롤러를 호출한다. 현재 일반 worker는 `dry_run/review`를 거부한다.
- YAML target validator에는 selector와 제한 JSON path 검사가 있지만 현재 `config/crawl_targets/*.yaml`은 해당 필드를 사용하지 않으며, 실제 Municipal parser 선택은 거대한 Python 분기에서 일어난다.
- staging snapshot은 정규화된 row JSONB이고, 필터 전 raw HTML/JSON 및 제외 candidate의 중앙 fixture는 아니다.
- 현재 품질 검사는 필수 필드·범위·중복 등 구조 오류를 찾지만, 독립적인 semantic FP/FN 정답셋과 replay 비교는 없다.
- 출판 직전 semantic eligibility, 완전성 검증, staging fingerprint, 별도 승격 승인 등은 새 구조가 재사용할 강한 안전장치다.

## 추천 읽기 순서

1. 간략 아키텍처에서 목표와 경계를 확인한다.
2. 간략 우선순위에서 P0/P1 MVP를 결정한다.
3. 상세 아키텍처로 데이터·API·보안 계약을 확정한다.
4. 상세 우선순위에서 팀별 작업을 배정한다.
5. 운영 가이드로 실제 업무 절차와 gate를 훈련한다.

## 문서 재생성

```bash
PYTHONPATH=/tmp/mooncen-docs-py python3 docs/crawler-quality-architecture/build_docs.py
PYTHONPATH=/tmp/mooncen-docs-py python3 docs/crawler-quality-architecture/validate_deliverables.py
```

필수 Python 패키지는 `requirements-docs.txt`에 기록했다. 시스템에 Cairo가 없으면 빌더는 SVG와 동일한 내용을 Pillow로 렌더링한다.

## 범위 밖

- 이 문서 작성 과정에서 애플리케이션, 크롤러, DB migration, 배포 설정은 변경하지 않았다.
- 실제 운영 DB 데이터의 FP/FN 표본 조사, 원격 서비스 상태, 배포 revision, backup 성공 여부는 검증하지 않았다.
- 문서의 임계값은 초기 기본값 또는 승인 원칙이며 provider별 baseline과 검수 표본을 통해 확정해야 한다.
