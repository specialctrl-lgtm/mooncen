# Gyeongju Lifelong Lecture Crawler

## Provider

- Provider: `MUNI_WWW_GYEONGJU_GO_KR_ADA8A467`
- Name: `경주시청`
- Source URL: `https://www.gyeongju.go.kr/gjlll/main/lecture/index.do?menu_idx=126`
- Scope: 평생학습포털 경주 `평생학습 강좌`

## Parser

- List parser: `table.apply_list_tbl tbody tr`
- Detail URL: `/gjlll/main/lecture/view.do?lect_no={lect_no}&menu_idx=126`
- Detail parser: `.view_util_box .info_util li`
- Detail fields:
  - title: `.view_tit_box .col.tit`
  - branch: `교육기관`
  - period: `교육 기간`
  - schedule_raw: `교육 요일` + `교육 시간`
  - fee: `수강료`
  - material_fee: `재료비`
  - target: `교육대상`
  - address: `교육장소`
  - instructor: `강사`
  - phone: `문의전화`
  - description: `강의목표`, `강좌개요`, `강의교재`, `강좌안내`

## Rules

- Courses whose education end date is earlier than the current date are skipped.
- The list page uses POST pagination; this crawler currently validates and collects the first visible page.
- The source does not expose course-specific images, so `image_url` remains empty.

## Commands

```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_GYEONGJU_GO_KR_ADA8A467.py --limit 10 --max-pages 1
python -X utf8 Crawler/generated_yaml/MUNI_WWW_GYEONGJU_GO_KR_ADA8A467.py --save-db --limit 10
python -X utf8 run_crawlers.py --providers MUNI_WWW_GYEONGJU_GO_KR_ADA8A467 --limit 10 --once --ignore-active-window --ignore-worker-lock --skip-coordinate-backfill --skip-category-backfill
```

## Validation

| Date | Rows | Saved | Score | Notes |
|---|---:|---:|---:|---|
| 2026-06-07 | 10 | 10 | 90.0 | All core fields filled; no source image. |

