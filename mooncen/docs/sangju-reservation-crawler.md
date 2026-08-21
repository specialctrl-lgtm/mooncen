# Sangju Reservation Crawler

Provider: `MUNI_WWW_SANGJU_GO_KR_AEA6F278`

Source URL: `https://www.sangju.go.kr/reserve/page/15375/11881.tc`

Crawler: `Crawler/generated_yaml/MUNI_WWW_SANGJU_GO_KR_AEA6F278.py`

## Scope

The crawler collects Sangju public reservation education/course entries.

The list page renders reservation cards under `#reserveList`. Pagination is executed through `/reserve/reservation/list.tc` with `pageIndex`.

## Parser

List page:

- Parses `#reserveList section`.
- Uses `reserveList.detail('{cyclNo}')` as the stable course key.
- Extracts `title`, `branch`, `address`, `period`, `status`, category text, and compact reservation information.

Detail page:

- Requests `/reserve/reservation/detail.tc?mn=15375&pageNo=11881&searchTrgtClsfCd=RMS004001&cyclNo={cyclNo}`.
- Parses `.img_jb` for title, facility, address, operating period, teacher, and image.
- Parses `.hidden_box ul.table_shape` for application period, operating period, capacity, selection method, and fee.
- Parses `#tab1_panel .bd_scroll` for description.

## Expired Course Rule

Courses whose operating period end date is before the current date are skipped during collection.

## Known Gaps

Some entries are information-only pages and do not expose a fee or target field.

Target is only filled when the source description explicitly includes `대상:` or `교육대상:`.

## Commands

Sample quality check:

```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_SANGJU_GO_KR_AEA6F278.py --limit 10 --max-pages 3
```

Run through the worker and save to DB:

```bash
python -X utf8 run_crawlers.py --providers MUNI_WWW_SANGJU_GO_KR_AEA6F278 --limit 10 --once --ignore-active-window --ignore-worker-lock --skip-coordinate-backfill --skip-category-backfill
```

## Validation

Validated on 2026-06-07.

| Metric | Value |
| --- | ---: |
| Rows collected | 10 |
| Score | 90.0 |
| DB saved | 10 |

Field counts:

| Field | Count |
| --- | ---: |
| title | 10 |
| branch | 10 |
| address | 10 |
| period | 10 |
| schedule_raw | 10 |
| target | 1 |
| fee | 9 |
| status | 10 |
| description | 10 |
| image_url | 10 |
