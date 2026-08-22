# Generated Crawler Failure Strategy

Generated YAML crawler 품질 점검 이후, 현재 레지스트리 기준으로 수집 실패한 대상을 분석한 결과입니다.

## 기준

- 기준 레지스트리: `config/generated_yaml_crawler_registry.yaml`
- 현재 provider 수: 599
- 기준 품질 리포트: `logs/crawler_dev_reports/generated_612_quality_20260526_183657.json`
- `e-ncom.co.kr` 계열 13개 제거 후 재계산

## 전체 결과

| 항목 | 건수 |
| --- | ---: |
| 현재 provider | 599 |
| 수집 성공 | 431 |
| 수집 실패 | 168 |
| 성공률 | 72.0% |

## 실패 유형

| 유형 | 건수 | 의미 | 처리 방법 |
| --- | ---: | --- | --- |
| NO_PARSER_MATCH | 103 | 페이지 접속은 됐지만 범용 파서가 강좌 목록 구조를 못 잡음 | HTML 목록/테이블/JS 포털 전용 파서 추가 |
| SSL_HANDSHAKE | 33 | 구형 TLS/SSL 또는 서버 설정으로 `requests` 접속 실패 | Selenium/Chrome fallback 또는 legacy TLS 세션 |
| TIMEOUT | 11 | 응답 지연, 차단, 외부 접근 불안정 | timeout/retry/backoff, 운영 서버 기준 재검증 |
| CONNECTION | 9 | 연결 거부/리셋/서버 다운 가능성 | URL 재검증, 후보 보류 |
| NOT_FOUND | 8 | URL 만료 또는 잘못된 후보 | URL discovery 재수행 |
| HTTP_ERROR | 4 | HTTP 오류 | 상태 코드별 개별 처리 |

## URL 형태별 판단

| 형태 | 건수 | 판단 |
| --- | ---: | --- |
| HOME_OR_SECTION_ROOT | 59 | 홈페이지/섹션 루트라 내부 링크 탐색이 필요 |
| NOTICE_OR_NEWS_DETAIL | 39 | 단일 공지/뉴스 글이라 목록 크롤러 대상이 아닐 가능성이 큼 |
| OTHER_PAGE | 35 | 직접 구조 확인 필요 |
| LIKELY_LIST_OR_PORTAL | 31 | 전용 크롤러로 살릴 가능성이 높음 |
| FILE_ATTACHMENT | 2 | HWP/PDF 등 첨부파일, 목록 크롤러에서 제외 또는 문서 파서 필요 |
| NEWS_DOMAIN | 2 | 언론 기사, 크롤링 후보에서 제거 권장 |

## 우선 구현 그룹

### 1. 통합예약/평생학습 포털 전용 파서

대상 예:

- `sugang.seongnam.go.kr`
- `learning.anyang.go.kr`
- `lll.yongin.go.kr`
- `lll.yw.go.kr`
- `esongpa.or.kr`
- `gangbuk.go.kr/rsvt`
- `yeyak.*`

필요 작업:

- 검색 조건 form 분석
- 목록 API 또는 POST 파라미터 추출
- 페이지 번호/기관/분류 파라미터 반복
- 상세 링크가 있으면 상세 파싱
- JS 렌더링만 되는 경우 Selenium fallback

### 2. 지자체 표준 예약 테이블 파서

대상 예:

- `geumcheon.go.kr/reserve/webEdcLctreList.do`
- `gimpo.go.kr/reserve/webEdcLctreList.do`
- `gn.go.kr/yeyak/selectUnityProgrmWebList.do`
- `gwangju.go.kr/reserve/bookingList.do`

관찰:

- HTML `table tbody tr`에 강좌명, 기관, 대상, 접수기간, 교육기간, 요일/시간, 정원, 수강료가 들어 있음
- 현재 범용 파서가 이 테이블을 강좌 row로 인식하지 못하는 케이스가 있음

필요 작업:

- `webEdcLctreList`, `selectUnityProgrmWebList`, `bookingList` 계열 전용 table mapper 작성
- `신청 :`, `교육 :`, `(화)13:00 ~ 15:00`, `무료` 같은 텍스트 패턴 분리

### 3. 게시판/공지 기반 후보 정리

대상 예:

- `articleView`
- `newsView`
- `selectBbsNttView`
- `bbsMsgDetail`
- `download.do`
- `.hwp`, `.pdf`

판단:

- 대부분 강좌 목록이 아니라 홍보 기사나 첨부 공지임
- 강좌 목록으로 바로 수집하기보다 `URL discovery`로 실제 신청/예약 URL을 다시 찾아야 함

필요 작업:

- 뉴스/공지/첨부 URL은 generated crawler registry에서 낮은 우선순위 또는 제외 처리
- 본문에서 `신청`, `예약`, `수강신청` 링크가 있으면 그 링크를 새 target으로 승격

### 4. SSL/접속 실패 그룹

대상 예:

- `learning.anyang.go.kr`
- 일부 `go.kr`, `lib.jne.go.kr` 계열

필요 작업:

- `requests` 실패 시 Selenium Chrome으로 fallback
- 운영 서버에서 동일 URL 접근 가능 여부 재검증
- 계속 실패하면 `paused_network` 상태로 분리

## 결론

지금 바로 크롤러를 늘릴 때 가장 효율적인 순서는 다음과 같습니다.

1. 표준 HTML 테이블 파서 보강
2. 통합예약/평생학습 포털 전용 파서 작성
3. 공지/뉴스/첨부 후보를 실제 신청 URL로 재발견하는 discovery 단계 추가
   - 2026-05-30: 생성형 YAML 크롤러에 `예약`, `통합예약`, `온라인예약`, `예약하기`, `예약신청`, `신청예약` 링크 fallback을 추가했다.
   - 원본 URL에서 같은 사이트의 예약 링크를 발견하면 해당 예약 페이지에서 강좌/교육/신청 링크를 최대 2단계 더 따라가며 수집한다.
   - 리포트에는 `reservation_discovery_links`, `reservation_fallback_pages`가 기록된다.
   - 2026-05-30 URL 수정 반영 테스트: `GWANGJU_NATIONAL_SCIENCE_MUSEUM`, `NATIONAL_OCEAN_SCIENCE_MUSEUM`, `NATIONAL_SCIENCE_MUSEUM`은 수정 URL에서 수집 가능해 `crawler_status: partial`로 승격했다. `DAEGU_NATIONAL_SCIENCE_MUSEUM`은 0건이라 `needs_parser`로 유지한다.
4. SSL/timeout 계열은 Selenium fallback과 보류 상태 분리
