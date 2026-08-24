# Cheongyang Lifelong Crawler

Provider: `MUNI_WWW_CHEONGYANG_GO_KR_25520BA7`

Source URL: `https://www.cheongyang.go.kr/prog/educate/lll/sub02_01/list.do`

Crawler: `Crawler/generated_yaml/MUNI_WWW_CHEONGYANG_GO_KR_25520BA7.py`

## Scope

The crawler collects Cheongyang lifelong learning course applications from the official course list table.

The previous registry URL pointed to a notice board. The crawler now uses the actual course application list.

## Parser

List page:

- Parses `table.basic_table.center`.
- Uses the `view.do?...eduNo=...` detail link as the stable course key.
- Extracts `title`, `target`, `application_period`, `period`, `capacity`, `schedule_raw`, and `status`.

Detail page:

- Parses the detail `table.basic_table`.
- Fills `title`, `period`, `schedule_raw`, `target`, `venue`, `branch`, `address`, `description`, `contact`, and `teacher`.
- Uses `교육장소` as the branch name so each venue can appear separately on the map.
- Builds the address as `충청남도 청양군 {교육장소}` because the page does not expose a full street address for each venue.

## Known Gaps

The source page does not expose a tuition/fee field, so `fee` remains empty.

Only courses with image attachments expose an `image_url`. Non-image attachments are not treated as course images.

## Expired Course Rule

Courses whose education period end date is before the current date are skipped during collection.

## Commands

Sample quality check:

```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_CHEONGYANG_GO_KR_25520BA7.py --limit 10 --max-pages 3
```

Run through the worker and save to DB:

```bash
python -X utf8 run_crawlers.py --providers MUNI_WWW_CHEONGYANG_GO_KR_25520BA7 --limit 10 --once --ignore-active-window --ignore-worker-lock --skip-coordinate-backfill --skip-category-backfill
```

## Validation

Validated on 2026-06-07.

| Metric | Value |
| --- | ---: |
| Rows collected | 10 |
| Score | 81.0 |
| DB saved | 10 |

Field counts:

| Field | Count |
| --- | ---: |
| title | 10 |
| branch | 10 |
| address | 10 |
| period | 10 |
| schedule_raw | 10 |
| target | 10 |
| fee | 0 |
| status | 10 |
| description | 10 |
| image_url | 1 |
