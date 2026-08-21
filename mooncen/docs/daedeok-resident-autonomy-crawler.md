# 대덕구 주민자치프로그램 크롤러

## Provider
- `MUNI_WWW_DAEDEOK_GO_KR_360B9B7C`

## Source URL
- `https://edu.daedeok.go.kr/damoa/contents/dms/edu/02/edu.02.001.motion?mnucd=MENU0100010`

## Parser
- 목록은 `table.table` 행을 헤더 순서 기준으로 파싱한다.
- 상세 이동은 행 또는 신청 버튼의 `fn_egov_select1(lecId, ordCd, ordSidoCd, ordLocalCd)` 값을 사용한다.
- 상세 페이지는 같은 사이트 엔진의 `bmode=detail1` POST 요청으로 조회한다.
- 지점은 상세의 `교육장소`를 우선 사용하고, 없으면 목록의 `기관명`을 사용한다.
- 교육기간 종료일이 지난 강좌는 기본 수집에서 제외한다. 필요 시 `--include-expired`를 사용한다.

## Quality
- 10건 샘플 기준 score `90.0`
- 필드 채움:
  - title 10/10
  - branch 10/10
  - address 10/10
  - period 10/10
  - schedule_raw 10/10
  - target 10/10
  - fee 10/10
  - status 10/10
  - description 10/10
  - image_url 0/10
- 원본 페이지에 강좌별 대표 이미지가 없어 `image_url`은 비어 있다.

## Commands
```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_DAEDEOK_GO_KR_360B9B7C.py --limit 10
python -X utf8 Crawler/generated_yaml/MUNI_WWW_DAEDEOK_GO_KR_360B9B7C.py --limit 10 --save-db
python -X utf8 run_crawlers.py --providers MUNI_WWW_DAEDEOK_GO_KR_360B9B7C --limit 10 --once --ignore-active-window --ignore-worker-lock --skip-coordinate-backfill --skip-category-backfill
```
