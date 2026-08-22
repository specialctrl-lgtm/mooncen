# 용산구교육종합포털 수강신청 크롤러

## 대상

- Provider: `MUNI_YEDU_YONGSAN_GO_KR_36A48D5E`
- URL: `http://yedu.yongsan.go.kr/site/edtotal/lesson/userlist.do?sitecdv=S0000500&decorator=user27EdTotal&menucdv=02020000`
- 분류: 평생학습

## 구현 내용

- 목록 테이블에서 강좌명, 접수기간, 기관명, 정원, 수강료, 접수상태를 수집한다.
- `javascript:goWrite(lesseqn, edutypecdv)` 값을 추출해 상세 URL을 생성한다.
- 상세 페이지의 강좌내용 표에서 교육기간, 수업요일, 교육시간, 접수나이, 강좌소개, 담당부서를 보강한다.
- 기본 수집은 모집중/모집예정 상태만 조회한다.
- 교육기간 종료일이 현재일보다 지난 강좌는 기본 수집에서 제외한다.
- 접수나이의 `남자 20세 에서 ~ 100세 까지 여자 20세...` 형태는 `성인 20세 이상`처럼 정규화한다.

## 품질 확인

2026-06-08 개발 DB 기준 10건 샘플 수집 및 저장 결과:

| 항목 | 결과 |
| --- | --- |
| collected | 10 |
| saved | 10 |
| score | 100.0 |
| grade | A |
| parser | yongsan_lesson_table+detail |

필수 필드 `title`, `branch`, `address`, `period`, `schedule_raw`, `target`, `fee`, `status`, `description`, `raw_url` 모두 10/10건 채워졌다.

## 실행

```bash
python -X utf8 Crawler/generated_yaml/MUNI_YEDU_YONGSAN_GO_KR_36A48D5E.py --limit 10 --max-pages 3
python -X utf8 Crawler/generated_yaml/MUNI_YEDU_YONGSAN_GO_KR_36A48D5E.py --save-db
```
