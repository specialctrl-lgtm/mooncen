# 구로구 통합예약 교육강좌 크롤러

## Provider
- `MUNI_WWW_GURO_GO_KR_A4A5D3E3`

## Source URLs
- 정보화교육: `https://www.guro.go.kr/yeyak/webEdcLctreList.do?key=3589&rep=1&searchLctreGroup=1&jachi=0&`
- 자치회관: `https://www.guro.go.kr/yeyak/webEdcLctreList.do?key=3600&rep=1&searchLctreGroup=0&jachi=1&`

## Parser
- 목록: `table.p-table`의 교육명, 장소, 접수기간, 교육기간, 상태 컬럼을 수집한다.
- 상세: `edcLctreView.do` 상세 페이지의 `table.p-table.block`에서 강좌상태, 신청기간, 교육기간, 강의시간, 수강료, 강의장소, 수강대상, 정원, 문의를 보강한다.
- 지점: 강의장소 텍스트를 우선 사용하고, 주소가 없으면 `서울특별시 구로구 가마산로 245`를 fallback으로 사용한다.
- 종료 강좌: 기본 실행에서는 교육 종료일이 지난 강좌를 제외한다. 필요 시 `--include-expired`로 포함한다.

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
- 구로구 원본 페이지는 강좌별 대표 이미지가 없어 `image_url`은 비어 있다.

## Commands
```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_GURO_GO_KR_A4A5D3E3.py --limit 10
python -X utf8 Crawler/generated_yaml/MUNI_WWW_GURO_GO_KR_A4A5D3E3.py --limit 10 --save-db
python -X utf8 run_crawlers.py --providers MUNI_WWW_GURO_GO_KR_A4A5D3E3 --limit 10 --once --ignore-active-window --ignore-worker-lock --skip-coordinate-backfill --skip-category-backfill
```
