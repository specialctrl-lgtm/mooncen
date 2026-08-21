# Gunsan Lifelong Crawler

Provider: `MUNI_WWW_GUNSAN_GO_KR_FF0982F2`

## Source

- Registry URL: `https://www.gunsan.go.kr/main/m140`
- Actual course list: `https://lll.gunsan.go.kr/pro/course.php?pm=list`
- The city page is a public notice/menu page. The crawler follows the Gunsan lifelong learning portal link and reads the course application table there.

## Parser

- Script: `Crawler/generated_yaml/MUNI_WWW_GUNSAN_GO_KR_FF0982F2.py`
- Parser name: `gunsan_lifelong_table`
- Table columns handled:
  - status
  - category
  - title
  - day
  - time
  - branch
  - venue
  - capacity
  - target/gender
  - application link

## DB Mapping

- Branches are split by the table's `읍면동` value.
- `venue_name` is filled from the table's `장소` value.
- `application_url` uses the application link when present, otherwise the course row URL.
- Expired courses are skipped by default when saving.

## Current Validation

Command:

```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_GUNSAN_GO_KR_FF0982F2.py --limit 10 --max-pages 3
python -X utf8 run_crawlers.py --providers MUNI_WWW_GUNSAN_GO_KR_FF0982F2 --once --ignore-active-window --ignore-worker-lock --skip-coordinate-backfill --skip-category-backfill
```

Result on 2026-06-08:

- Crawler execution: success
- Rows collected: 0
- Reason: the Gunsan lifelong learning portal currently returns no rows for both `신청 강좌` and `마감 강좌`.
- The crawler remains registered as `ready` because the source page and table structure are reachable.
- Existing 19 bad rows from the old generic parser were marked inactive after the new crawler returned zero valid rows.
- `tools/crawler_quality_report.py` now reports active rows by default. Use `--include-inactive` when stale rows must be audited.
