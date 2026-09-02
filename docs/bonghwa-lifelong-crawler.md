# 봉화군 평생학습관 크롤러

## 대상

- Provider: `MUNI_WWW_BONGHWA_GO_KR_C3F54364`
- URL: `https://www.bonghwa.go.kr/edu/portal/academy/program/list.do?mId=0301000000`
- 분류: 평생학습
- 크롤러: `Crawler/generated_yaml/MUNI_WWW_BONGHWA_GO_KR_C3F54364.py`

## 구조

- 목록 화면은 HTML에 강좌 목록을 직접 포함하지 않는다.
- 브라우저 JS가 `POST /edu/portal/academy/program/ajax/list.do` JSON API를 호출한다.
- 목록 파라미터는 `mId=0301000000`, `page`, `searchAppSortState` 등을 사용한다.
- 기본 수집은 현재 유효한 `모집중(ing)`, `모집예정(wait)`만 조회한다.
- 상세는 `/edu/portal/academy/program/view.do?mId=0301000000&programAppIdx={id}`로 접근한다.

## 수집 필드

- `title`: API `eduTitle`
- `branch`: `봉화군 평생학습관`
- `address`: `경상북도 봉화군 봉화읍 내성로 5길 13`
- `period`: API/상세 `교육기간`
- `schedule_raw`: `교육기간 + 교육시간`
- `target`: 상세 `모집대상`
- `fee`: API 수강료, 무료 여부
- `material_fee`, `material_note`: API/상세 재료비
- `status`: `모집중 -> OPEN`, `모집예정 -> SCHEDULED`, 마감류는 `CLOSED`
- `description`: 상세 `강의내용`
- `image_url`: 상세 관련 이미지가 있는 경우 수집

## 실행

```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_BONGHWA_GO_KR_C3F54364.py --limit 10 --max-pages 3
python -X utf8 Crawler/generated_yaml/MUNI_WWW_BONGHWA_GO_KR_C3F54364.py --limit 10 --max-pages 3 --save-db
```

## 검증 결과

- 2026-06-08 현재 유효 강좌 7건 수집
- DB 저장 7/7 성공
- 품질 점수 91.4
- 필드 카운트: title/branch/address/period/schedule_raw/target/fee/status/description 7/7, image_url 1/7
- 교육기간 종료 강좌는 기본 수집에서 제외한다.
