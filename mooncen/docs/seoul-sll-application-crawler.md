# 서울런4050 신청형 강좌 크롤러

## Provider
- `MUNI_SLL_SEOUL_GO_KR_A0D6D8A2`

## Source
- 원래 URL은 서울시 평생학습포털의 기관별 소식 공지 상세다.
- 공지 본문 자체는 2023년 외부기관 안내라 강좌 수집 대상으로 부적합하다.
- 같은 페이지 내 실제 수강신청 메뉴를 기준으로 아래 신청형 강좌 API를 수집한다.

## Targets
- 인생디자인학교: `simin_yn=LD`
- 서울리테크 경제교육: `simin_yn=MC`
- 서울마이칼리지: `simin_yn=U`
- 팝업스쿨: `simin_yn=PS`

## Parser
- 목록 API: `/lms/simin_course/courseRequest/doListSiminCourse.do`
- 상세 API/page: `/lms/simin_course/courseRequest/doDetailInfo.do`
- 기존 시민대학 파서와 같은 JSON 목록 및 상세 HTML 구조를 사용한다.
- 교육기간 종료일이 지난 강좌는 기본 수집에서 제외한다.
- `FULL`, `PAST` 상태는 각각 `접수마감`, `종료`로 정규화한다.
- 온라인 및 외부기관 과정은 확정 주소가 없으면 주소를 비워 잘못된 지도 좌표 생성을 피한다.

## Quality
- 10건 샘플 기준 score `84.0`
- 5건 저장 검증 기준 score `86.0`
- 필드 채움:
  - title 채움
  - branch 채움
  - period 채움
  - schedule_raw 채움
  - target 채움
  - fee 채움
  - status 채움
  - description 채움
  - address는 서울시 캠퍼스 매칭 가능 건만 채움
  - image_url은 원본 API/상세에서 대표 이미지가 없어 비어 있음

## Commands
```bash
python -X utf8 Crawler/generated_yaml/MUNI_SLL_SEOUL_GO_KR_A0D6D8A2.py --limit 10
python -X utf8 Crawler/generated_yaml/MUNI_SLL_SEOUL_GO_KR_A0D6D8A2.py --limit 10 --save-db
python -X utf8 run_crawlers.py --providers MUNI_SLL_SEOUL_GO_KR_A0D6D8A2 --limit 10 --once --ignore-active-window --ignore-worker-lock --skip-coordinate-backfill --skip-category-backfill
```
