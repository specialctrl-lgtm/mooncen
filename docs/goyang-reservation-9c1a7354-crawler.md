# Goyang Reservation 9C1A7354 Crawler

## Summary

- Provider: `MUNI_WWW_GOYANG_GO_KR_9C1A7354`
- Name: 고양시 통합예약 교육강좌
- URL: `https://www.goyang.go.kr/resve/manage/BD_selectResveManageList.do?q_resveTopClCode=CL_01`
- Parser: `BD_selectResveManageList.do` list cards plus `BD_selectResveManage.do` detail page.
- Branch split: normalizes venue names from list/detail pages. Gu office locations and known venues are mapped to stable addresses.

## Target Scope

The crawler cycles these Gu department filters:

| Code | Scope |
|---|---|
| `395000000` | Deogyang-gu filtered reservation list |
| `396010000` | Ilsandong-gu filtered reservation list |
| `410010000` | Ilsanseo-gu filtered reservation list |

## Collected Fields

| Field | Source |
|---|---|
| `title` | detail/list title |
| `branch` | normalized detail `장소` |
| `address` | known venue address mapping or address in parentheses |
| `period` | detail `교육.강좌 일시` |
| `schedule_raw` | full detail `교육.강좌 일시` |
| `target` | detail `이용대상` |
| `fee` | detail `이용료` |
| `status` | list reservation state |
| `description` | detail `#tab-cont1` |
| `image_url` | detail reservation image |
| `raw_url` | generated detail URL |

## Validation

Command:

```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_GOYANG_GO_KR_9C1A7354.py --limit 10 --max-pages 3 --timeout 30
```

Result:

| Metric | Value |
|---|---:|
| Rows | 10 |
| Score | 100.0 |
| Grade | A |
| title | 10 |
| branch | 10 |
| address | 10 |
| period | 10 |
| schedule_raw | 10 |
| target | 10 |
| fee | 10 |
| status | 10 |
| description | 10 |
| image_url | 10 |

DB save command:

```bash
python -X utf8 run_crawlers.py --providers MUNI_WWW_GOYANG_GO_KR_9C1A7354 --limit 10 --once --ignore-active-window --ignore-worker-lock --skip-coordinate-backfill --skip-category-backfill
```

DB save result: `saved 10/10`.

## Notes

- Courses whose education period already ended are skipped by default.
- Closed registration with a current or future education period is still collected with status `CLOSED`.
- Some school/gym venues do not expose a full address. Those currently fall back to the default known city address and should be improved by address backfill if map precision is required.
