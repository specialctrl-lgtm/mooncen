# 국립과천과학관 크롤러

## 대상

- Provider: `GWACHEON_NATIONAL_SCIENCE_MUSEUM`
- Site: `https://www.sciencecenter.go.kr`
- Source group: `museum_science`
- Parser: `gwacheon_scipia_table`

## 수집 방식

- 과천과학관 `scipia/schedules` 일정 테이블을 수집한다.
- 현재 수집 URL:
  - `https://www.sciencecenter.go.kr/scipia/schedules?ACADEMY_CD=ACD012`
  - `https://www.sciencecenter.go.kr/scipia/schedules?ACADEMY_CD=ACD006&COURSE_CD=&endNum=100&SEMESTER_CD=&TYPE=&FLAG=&CLASS_CD=&OWNER_LIMIT_FLAG=`
- 테이블 컬럼에서 제목, 기간, 장소, 대상, 이용료, 상태, 소개, 예약 방식을 추출한다.
- `자세히보기` 링크를 `raw_url`로 저장한다.
- 예약 버튼의 `goPost(...)`, `ShowList(...)`, `href`를 해석해 `application_url`로 저장한다.
- 과천과학관 주소는 `경기도 과천시 상하벌로 110 국립과천과학관`으로 고정 보강한다.

## 필드

- `title`: 프로그램명
- `period`: 기간 또는 일시
- `schedule_raw`: 단일 일시 또는 상시 항목
- `room`, `venue_name`: 장소
- `target`: 대상
- `fee`: 이용료
- `status`: 진행상태
- `raw_url`: 소개/상세 URL
- `application_url`: 예약 URL
- `venue_address`, `address`: 과천과학관 주소
- `capacity`, `capacity_current`: 테이블 hidden input이 있는 경우 정원/신청 인원

## 검증 결과

- 샘플 리포트: `logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_122705.yaml`
- DB 저장 리포트: `logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_122848.yaml`
- 수집: 6건
- 저장: 5건. `BOOK&GO(북앤고)`는 2026-05-30 단일 일정으로 이미 지나 저장 단계에서 제외됐다.
- `title`, `period`, `fee`, `target`, `status`, `address`: 6/6
- `application_url`: 5/6
- `description`: 원천 테이블의 버튼 텍스트만 있어 저장하지 않는다.

## 실행

```bash
python -X utf8 Crawler/generated_yaml/GWACHEON_NATIONAL_SCIENCE_MUSEUM.py --save-db --max-pages 5 --detail-limit 20
```
