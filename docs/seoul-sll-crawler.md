# Seoul SLL Citizen University Crawler

## Provider

- Provider: `MUNI_SLL_SEOUL_GO_KR_529FCCEC`
- Name: `서울런4050 서울시평생학습포털`
- Source URLs:
  - `https://sll.seoul.go.kr/lms/simin_course/courseRequest/doListView.do?main_se=ssu&simin_yn=M%2CDC%2CMD%2CO%2CR%2CRG&mnid=202501604868`
  - `https://sll.seoul.go.kr/lms/simin_course/courseRequest/doListView.do?main_se=ssu&simin_yn=W%2CPA&mnid=202501763468`

## Parser

- List API: `POST /lms/simin_course/courseRequest/doListSiminCourse.do`
- Detail URL: `/lms/simin_course/courseRequest/doDetailInfo.do?course_id={course_id}&class_no={class_no}&course_gubun={course_gubun}&simin_yn={simin_yn}`
- List fields:
  - title: `course_nm`
  - period: `course_str_dt` + `course_end_dt`
  - schedule_raw: `weekday` + course start/end time
  - fee: `fee`
  - status: `status`
  - category: `category_nm2`
  - instructor: `prof_nm`
  - capacity_text: `capacity`
- Detail fields:
  - target: `수강대상`
  - description: `과정소개`
  - reception_period: `신청기간`
  - branch/place: `교육장소`

## Branch Mapping

- `M`: 중부권캠퍼스, `서울특별시 중구 칠패로 5`
- `DC`: 동남권캠퍼스, `서울특별시 강동구 고덕로 399`
- `MD`: 모두의학교캠퍼스, `서울특별시 금천구 남부순환로 128길 42`
- `RG`: 다시가는캠퍼스, `서울특별시 관악구 낙성대로 70`
- `O`, `R`, `W`, `PA`: source place is kept as branch when a mapped campus is not found.

## Rules

- Courses whose education end date is earlier than the current date are skipped.
- `image_url` is empty because the source list/detail does not expose course-specific images.
- The crawler accepts `--limit`, `--save-db`, `--include-expired`, and compatibility args used by `run_crawlers.py`.

## Commands

```bash
python -X utf8 Crawler/generated_yaml/MUNI_SLL_SEOUL_GO_KR_529FCCEC.py --limit 10
python -X utf8 Crawler/generated_yaml/MUNI_SLL_SEOUL_GO_KR_529FCCEC.py --save-db --limit 10
python -X utf8 run_crawlers.py --providers MUNI_SLL_SEOUL_GO_KR_529FCCEC --limit 10 --once --ignore-active-window --ignore-worker-lock --skip-coordinate-backfill --skip-category-backfill
```

## Validation

| Date | Rows | Saved | Score | Notes |
|---|---:|---:|---:|---|
| 2026-06-08 | 10 | 10 | 90.0 | All core fields filled; no source image. |

