# 경상북도울진교육지원청 프로그램 신청 크롤러

## 대상

- Provider: `MUNI_WWW_GBE_KR_98673AC8`
- URL: `https://www.gbe.kr/uj/eq/view/selectEqList.do?mi=22841`
- 분류: 교육·체험 / 평생학습

## 구현 내용

- 목록 `selectEqList.do`의 강좌 표에서 강좌명, 접수기간, 강좌기간, 모집인원, 접수방식, 진행상태를 수집한다.
- 상세 `selectEqInfo.do`를 추가 요청해 `강좌내용`, `교육기간`, `신청기간`, `참가대상자구분`을 보강한다.
- 제목 앞의 `[교원 연수]`, `[학부모 연수]`, `[지역주민 연수]` 같은 값을 `category_raw`로 분리한다.
- 카테고리/제목 기반으로 `교원`, `학부모`, `성인`, `학생` 대상을 추정한다.
- 접수방식이 `인터넷`이면 `application_type=ONLINE`으로 저장한다.
- 교육기간 종료일이 지난 강좌는 기본 수집에서 제외한다.

## 품질 확인

2026-06-09 개발 DB 기준:

| 조건 | collected | saved | score | grade |
| --- | ---: | ---: | ---: | --- |
| 기본 수집 | 1 | 1 | 100.0 | A |
| `--include-expired --limit 10` | 10 | 0 | 100.0 | A |

필수 필드 `title`, `branch`, `address`, `period`, `schedule_raw`, `target`, `fee`, `status`, `description`, `raw_url` 모두 채워졌다.

## 실행

```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_GBE_KR_98673AC8.py --save-db
python -X utf8 Crawler/generated_yaml/MUNI_WWW_GBE_KR_98673AC8.py --include-expired --limit 10
python -X utf8 run_crawlers.py --providers MUNI_WWW_GBE_KR_98673AC8 --limit 10 --once --ignore-active-window --ignore-worker-lock --skip-coordinate-backfill --skip-category-backfill
```
