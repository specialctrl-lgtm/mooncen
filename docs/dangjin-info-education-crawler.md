# Dangjin Citizen Information Education Crawler

## Provider

- Provider: `MUNI_WWW_DANGJIN_GO_KR_3C378AA6`
- Name: `당진시청 시민정보화교육`
- Source URL: `https://www.dangjin.go.kr/prog/reprsntInfrmEdu/kor/sub05_07_01/list.do`
- Scope: 당진시청 시민정보화교육 목록 및 상세 페이지

## Parser

- List parser: `table.tbl_basic.center tbody tr`
- Detail parser: `table.basic_table`
- Detail fields:
  - `title`: list row title, with detail heading fallback
  - `period`: `교육기간`
  - `schedule_raw`: `교육시간`
  - `branch`: `교육장소`
  - `target`: `교육대상`
  - `fee`: `교육비`
  - `description`: `기타`
  - `phone`: `전화문의`
  - `capacity_text`: `교육인원`
  - `material_note`: 준비물/재료/교재 문구 extracted from `description`

## Rules

- Courses whose education end date is earlier than today are skipped.
- The list is sorted newest first. If a whole page is expired, crawling stops because following pages are older.
- `--include-expired` is available for parser quality checks only.
- The source does not expose course-specific images, so `image_url` remains empty.
- `교육비` is frequently blank on the source; empty fee is accepted.

## Commands

```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_DANGJIN_GO_KR_3C378AA6.py --limit 10 --max-pages 5
python -X utf8 Crawler/generated_yaml/MUNI_WWW_DANGJIN_GO_KR_3C378AA6.py --include-expired --limit 10 --max-pages 1
python -X utf8 Crawler/generated_yaml/MUNI_WWW_DANGJIN_GO_KR_3C378AA6.py --save-db --limit 10
python -X utf8 run_crawlers.py --providers MUNI_WWW_DANGJIN_GO_KR_3C378AA6 --limit 10 --once --ignore-active-window --ignore-worker-lock --skip-coordinate-backfill --skip-category-backfill
```

## Validation

| Date | Mode | Rows | Saved | Score | Notes |
|---|---|---:|---:|---:|---|
| 2026-06-08 | include expired | 10 | 0 | 84.0 | Fee exists in 4/10 rows; no source images. |
| 2026-06-08 | active only | 0 | 0 | 0.0 | All visible courses ended before 2026-06-08 and are skipped by lifecycle policy. |

