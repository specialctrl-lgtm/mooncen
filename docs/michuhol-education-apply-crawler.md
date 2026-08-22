# Michuhol Education Apply Crawler

Provider: `MUNI_WWW_MICHUHOL_GO_KR_29D0C0F5`

Source URL: `https://www.michuhol.go.kr/reserve/education_apply/list.do?organ_cd=001001`

## Structure

The Michuhol reservation portal exposes education courses in a table under `education_apply/list.do`.

- Pagination uses the `page` query parameter.
- Course detail pages are linked as `step1.do?sq=...`.
- Application pages are linked as `step2.do?edu_sq=...`.
- Detail pages expose structured `dl` fields for period, time, room, fee, material fee, contact phone, and description.

## Parser

`collect_michuhol_education_apply` reads the list table, follows each `step1.do` detail URL, and fills course fields from the detail `dl` pairs.

Collected fields:

- `title`
- `branch`, `branch_code`, `category`
- `period`, `apply_period`, `schedule_raw`
- `target`, `capacity`, `room`, `venue_name`
- `fee`, `material_fee`, `phone`, `description`
- `raw_url`, `application_url`, `status`

## Test Result

Command:

```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_MICHUHOL_GO_KR_29D0C0F5.py --save-db --per-target-limit 0 --max-pages 10 --detail-limit 500 --timeout 30 --mark-stale
```

Result on 2026-06-05:

| Metric | Value |
| --- | --- |
| Parser | `michuhol_education_apply_table` |
| Rows | 284 |
| Saved | 40 |
| Pages | 10 |
| Detail pages | 284 |
| title / period / schedule / fee / description | 284 / 284 / 284 / 284 / 284 |

