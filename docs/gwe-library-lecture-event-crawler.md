# GWE Library Lecture Event Crawler

Provider: `MUNI_LIB_GWE_GO_KR_303FFE72`

Source URL: `https://lib.gwe.go.kr/samecc/menu/3541/book/search`

## Structure

The GWE library site can start from a book search page, but lecture programs are under `lecture-event`.

- The crawler derives the correct list URL from the source menu id: `/samecc/menu/3541/lecture-event/list/all`.
- List cards are `.lecture_item`.
- Course details are available at `/samecc/menu/3541/lecture-event/{event_id}`.

## Parser

`collect_gwe_library_events_detail` parses list card `dl` pairs and follows each detail page.

Collected fields:

- `title`
- `branch`, `branch_code`, `address`, `venue_address`
- `period`, `schedule_raw`, `apply_period`
- `target`, `capacity`, `application_method_raw`
- `room`, `venue_name`, `material_fee`, `material_note`
- `status`, `description`, `image_url`
- `raw_url`, `application_url`

The source currently marks participation fee as `-`, so `fee` remains empty.

## Test Result

Command:

```bash
python -X utf8 Crawler/generated_yaml/MUNI_LIB_GWE_GO_KR_303FFE72.py --save-db --per-target-limit 0 --max-pages 3 --detail-limit 20 --timeout 30 --mark-stale
```

Result on 2026-06-05:

| Metric | Value |
| --- | --- |
| Parser | `gwe_library_lecture_event+detail` |
| Rows | 4 |
| Saved | 4 |
| Pages | 2 |
| Detail pages | 4 |
| title / branch / address / period / schedule / target / description | 4 / 4 / 4 / 4 / 4 / 4 / 4 |
| fee | 0 |

