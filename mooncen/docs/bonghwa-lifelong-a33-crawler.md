# Bonghwa Lifelong A33 Crawler

## Summary

- Provider: `MUNI_WWW_BONGHWA_GO_KR_A33FDB5A`
- Name: 봉화군 평생학습관 평생학습강좌
- URL: `https://www.bonghwa.go.kr/edu/portal/academy/program/list.do?mId=0301000000`
- Parser: `program/ajax/list.do` JSON API plus `program/view.do` detail page.
- Branch split: fixed branch `봉화군 평생학습관`.

## Collected Fields

| Field | Source |
|---|---|
| `title` | API `eduTitle` |
| `branch` | fixed branch |
| `address` | fixed lifelong learning center address |
| `period` | API/detail education period |
| `schedule_raw` | detail education period and education time |
| `target` | detail `모집대상` |
| `fee` | API tuition/free flag |
| `status` | API application state |
| `description` | detail `강의내용` |
| `image_url` | detail related image when available |
| `raw_url` | generated detail URL |

## Validation

Command:

```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_BONGHWA_GO_KR_A33FDB5A.py --limit 10 --max-pages 3 --timeout 30
```

Result:

| Metric | Value |
|---|---:|
| Rows | 7 |
| Score | 91.4 |
| Grade | A |
| title | 7 |
| branch | 7 |
| address | 7 |
| period | 7 |
| schedule_raw | 7 |
| target | 7 |
| fee | 7 |
| status | 7 |
| description | 7 |
| image_url | 1 |

DB save command:

```bash
python -X utf8 run_crawlers.py --providers MUNI_WWW_BONGHWA_GO_KR_A33FDB5A --limit 10 --once --ignore-active-window --ignore-worker-lock --skip-coordinate-backfill --skip-category-backfill
```

DB save result: `saved 7/7`.

## Notes

- Current non-expired rows are 7.
- Expired rows can be checked with `--include-expired`.
- Most courses do not expose a related image, so `image_url` quality is lower than other fields.
