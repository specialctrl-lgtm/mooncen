# 수성구 평생교육 플랫폼 러닝톡 프로그램신청 크롤러

## 대상

- Provider: `MUNI_LLL_SUSEONG_KR_C40B81D9`
- URL: `https://lll.suseong.kr/index.do?menu_id=00001883&menu_link=/front/education/searchEdu.do`
- 분류: 평생학습

## 구현 내용

- 목록 `table.tbl_eduprog`에서 강좌명, 교육기관, 신청기간, 교육기간, 요일/시간, 수강료, 재료비, 모집인원, 접수방법, 상태를 수집한다.
- `fn_education_details(course_id, type)`에서 상세 ID를 추출한다.
- `EDU_...`는 `/front/education/details.do?edu_id=...`, `CRS_...`는 `/reservation/learning/details.do?crsId=...`로 상세 URL을 분기한다.
- 상세에서 강좌분류, 내용분류, 교육대상, 교육장소, 주소, 문의전화, 강좌소개, 오시는 길, 주의사항, 신청상태를 보강한다.
- 교육기관을 지점으로 분리하고, 상세 주소가 있으면 지점 주소로 저장한다.
- 교육기간 종료일이 지난 강좌는 기본 수집에서 제외한다.

## 품질 확인

2026-06-08 개발 DB 기준 10건 샘플 수집 및 저장 결과:

| 항목 | 결과 |
| --- | --- |
| collected | 10 |
| saved | 10 |
| score | 100.0 |
| grade | A |
| parser | suseong_education_table+detail |

필수 필드 `title`, `branch`, `address`, `period`, `schedule_raw`, `target`, `fee`, `status`, `description`, `raw_url` 모두 10/10건 채워졌다.

## 실행

```bash
python -X utf8 Crawler/generated_yaml/MUNI_LLL_SUSEONG_KR_C40B81D9.py --limit 10 --max-pages 2
python -X utf8 Crawler/generated_yaml/MUNI_LLL_SUSEONG_KR_C40B81D9.py --save-db
```
