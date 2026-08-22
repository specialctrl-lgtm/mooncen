# 영등포구 통합예약 크롤러

## 대상
- Provider: `MUNI_WWW_YDP_GO_KR_02AFDA7A`
- URL: `https://www.ydp.go.kr/reserve/selectTnEdcLctreListU.do?key=5062&`
- 분류: 공공예약
- Parser: `ydp_reserve_list`

## 구현 내용
- 목록은 `ul.board-lines > li` 단위로 읽어서 상태(`접수중`, `대기접수`)와 상세 링크를 함께 매칭한다.
- 상세 페이지 `viewTnEdcLctreU.do?lctreNo=...`에서 과정명, 교육장소, 강사명, 수강대상, 수강료, 재료비, 접수기간, 교육기간, 강의요일, 정원, 설명을 수집한다.
- `토 토 10:00 ~ 12:00`처럼 요일이 중복되는 값을 `토 10:00 ~ 12:00`으로 정규화한다.
- 페이지네이션이 없는 현재 목록 구조에서는 1페이지만 처리해 동일 목록 반복 수집을 막는다.
- `YDP미래평생학습관`처럼 실제 기관명에 `평생학습`이 포함된 경우 제목의 첫 괄호를 지점명으로 오인하지 않도록 보정했다.
- stale 처리 기준 시점을 저장 직전으로 잡아 `--mark-stale` 실행 시 신규 저장분이 비활성화되지 않도록 수정했다.

## 2026-06-05 실행 결과
- 수집: 10건
- DB 저장: 10건
- 활성 강좌: 10건
- 비활성 처리된 기존 오수집/과거 데이터: 59건
- 활성 지점: 2개 (`YDP미래평생학습관`, `동주민센터`)
- 필드 채움: title/branch/status/fee/material_fee/schedule/period/target/description 100%
- 상태: OPEN 3건, WAITING 7건
- 리포트: `logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_000024.yaml`

## 실행 명령
```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_YDP_GO_KR_02AFDA7A.py --save-db --mark-stale
```
