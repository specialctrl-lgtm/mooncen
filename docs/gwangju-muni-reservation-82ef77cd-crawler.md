# Gwangju Municipal Reservation Crawler

## Summary

- Provider: `MUNI_WWW_GWANGJU_GO_KR_82EF77CD`
- Name: 광주광역시 통합예약 교육강좌
- URL: `https://www.gwangju.go.kr/reserve/bookingList.do?pageId=reserve1&searchCate1=A`
- Parser: `/reserve/getBookingList.do` JSON API.
- Branch split: normalizes `eduAddress` into map-friendly branches.

## Collected Fields

| Field | Source |
|---|---|
| `title` | API `eduNm` |
| `branch` | normalized API `eduAddress`, fallback department/city hall |
| `address` | known branch address mapping or address extracted from `eduAddress` |
| `period` | API `startEduDate`, `endEduDate` |
| `schedule_raw` | period plus API `startEduTime`, `endEduTime` |
| `target` | API target fields or target text extracted from description |
| `fee` | API `eduPriceType` |
| `status` | API `status` and status labels |
| `description` | API `contents` |
| `material_note` | API `notes` |
| `image_url` | API `fileMediaThumbUrl` |
| `raw_url` | generated `bookingView.do` URL |

## Validation

Command:

```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_GWANGJU_GO_KR_82EF77CD.py --limit 10 --max-pages 5 --timeout 30
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
| raw_url | 10 |

DB save command:

```bash
python -X utf8 run_crawlers.py --providers MUNI_WWW_GWANGJU_GO_KR_82EF77CD --limit 10 --once --ignore-active-window --ignore-worker-lock --skip-coordinate-backfill --skip-category-backfill
```

DB save result: `saved 10/10`.

## Notes

- The API reported `total_count=651`, `page_count=55` during validation.
- Education periods that already ended are skipped by default.
- Some rows expose broad locations such as `신청한 장소`; those are normalized to `광주광역시청` when the managing department is city-level.
