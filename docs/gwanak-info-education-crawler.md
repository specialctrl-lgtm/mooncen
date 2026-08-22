# 관악구 구민 정보화교육 크롤러

## 대상

- Provider: `MUNI_WWW_GWANAK_GO_KR_51D9DCB4`
- URL: `https://www.gwanak.go.kr/site/edu/lecture/Lecture_List.do?scLcOrganization1=29000400`
- 분류: 평생학습

## 구현 내용

- 목록 `table.list`에서 강좌명, 교육기간, 교육장소, 수강료, 신청/정원, 접수방법, 접수상태를 수집한다.
- 상세 이동 함수 `doLectureView(clIdx, ..., scLcOrganization1)`에서 강좌 ID와 기관 코드를 추출한다.
- 상세 `Lecture_View.do`의 `table.info-table`에서 교육대상, 강사명, 교육장소, 교육기간, 수강요일, 접수기간, 신청제한, 전화문의, 비고를 보강한다.
- 교육장소 기준으로 `난곡 정보화 교육장`, `성현 정보화 교육장` 지점을 분리한다.
- 상세주소는 목록/상세에 직접 제공되지 않아 관악구청 기본 주소를 fallback으로 저장한다. 주소 정밀 보정 대상에서는 `address_source=crawler_fallback`로 구분한다.
- 교육기간 종료일이 지난 강좌는 기본 수집에서 제외한다.

## 품질 확인

2026-06-08 개발 DB 기준 10건 샘플 수집 및 저장 결과:

| 항목 | 결과 |
| --- | --- |
| collected | 10 |
| saved | 10 |
| score | 100.0 |
| grade | A |
| parser | gwanak_info_education_table+detail |

필수 필드 `title`, `branch`, `address`, `period`, `schedule_raw`, `target`, `fee`, `status`, `description`, `raw_url` 모두 10/10건 채워졌다.

## 실행

```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_GWANAK_GO_KR_51D9DCB4.py --limit 10 --max-pages 2
python -X utf8 Crawler/generated_yaml/MUNI_WWW_GWANAK_GO_KR_51D9DCB4.py --save-db
```
