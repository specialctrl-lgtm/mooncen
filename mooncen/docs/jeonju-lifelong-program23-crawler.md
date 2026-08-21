# Jeonju Lifelong Program23 Crawler

## Provider

- Provider: `MUNI_E_JEONJU_GO_KR_91CFC934`
- Name: `전주시평생학습관`
- Source URL: `https://e.jeonju.go.kr/main/menu?gc=Program23`
- Scope: 전주시평생학습관 `Program23` 인문학 강좌 목록

## Parser

- List parser: `ul.class_list_wrap > li`
- Detail parser: `article .class_view_wrap`
- Detail fields:
  - title: `.tit strong`
  - status: `.tit span`, list button text fallback
  - period: `진행기간`
  - schedule_raw: `강의일시`
  - target: `대상`
  - fee: `수강료`
  - address: `교육장 주소`
  - description: `.program_viewbox .cont`
- Branch: source has a single center, so rows are saved as `전주시평생학습관`.

## Rules

- Courses whose end date is earlier than the current date are skipped at crawl time.
- `image_url` is intentionally empty when the source does not expose a course-specific image.
- `raw_url` uses the detail page URL containing `program_id`.

## Commands

```bash
python -X utf8 Crawler/generated_yaml/MUNI_E_JEONJU_GO_KR_91CFC934.py --limit 10 --max-pages 1
python -X utf8 Crawler/generated_yaml/MUNI_E_JEONJU_GO_KR_91CFC934.py --save-db --limit 10
python -X utf8 run_crawlers.py --providers MUNI_E_JEONJU_GO_KR_91CFC934 --limit 10 --once --ignore-active-window --ignore-worker-lock --skip-coordinate-backfill --skip-category-backfill
```

## Validation

| Date | Rows | Saved | Score | Notes |
|---|---:|---:|---:|---|
| 2026-06-07 | 6 | 6 | 90.0 | 10 list rows found, 4 expired rows skipped, source has no course-specific image. |

