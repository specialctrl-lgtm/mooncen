# JPYOUTH Program Crawler

Provider: `MUNI_WWW_JPYOUTH_CO_KR_5E838FBF`

Source URL: `http://www.jpyouth.co.kr/sub.php?menukey=54`

## Structure

The Jeungpyeong Youth Center program page is a static HTML table.

- List URL uses `sub.php?menukey=54&mode=list&page=N`.
- Each row contains program name, combined period/schedule text, capacity, venue, target, and status.
- Detail pages use `sub.php?menukey=54&mode=view&idx=...`.
- Detail tables expose title, program period, program datetime, application period, venue, capacity, target, 담당자, and detail text.

## Parser

`collect_jpyouth_programs` parses list pages and follows detail pages.

Collected fields:

- `title`
- `branch`, `branch_code`, `address`, `venue_name`, `venue_address`
- `period`, `schedule_raw`, `apply_period`
- `target`, `capacity_total`
- `status`, `fee`, `instructor`, `description`
- `raw_url`, `application_url`
- category metadata: `청소년`

The list schedule column combines period and schedule, so `split_jpyouth_list_schedule` separates the first date range from the remaining schedule text. Fee is extracted from the detail `세부내용` text when `참가비`, `수강료`, or `재료비` appears before a KRW amount.

Expired courses are skipped before DB save when `end_date` is older than the current date.

## Test Result

Command:

```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_JPYOUTH_CO_KR_5E838FBF.py --save-db --mark-stale --per-target-limit 0 --max-pages 14 --detail-limit 250 --timeout 30
```

Result on 2026-06-05:

| Metric | Value |
| --- | --- |
| Parser | `jpyouth_table+detail` |
| Rows | 202 |
| Saved | 202 |
| Pages | 14 |
| Detail pages | 202 |
| title / period / schedule / target | 202 / 202 / 202 / 202 |
| description | 176 |
| fee | 82 |

