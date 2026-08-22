# Daejeon Donggu Lifelong Crawler

## Summary

- Provider: `MUNI_WWW_DONGGU_GO_KR_9A7A5E6F`
- Name: 대전광역시 동구 평생학습 수강신청
- URL: `https://www.donggu.go.kr/lll/www/selectUserEduList.do?key=733`
- Parser: `selectUserEduList.do` list table plus `selectUserEduView.do` detail page.
- Branch split: detail `교육장소` is normalized to actual venue when possible; Dong-gu office rooms are grouped under `대전광역시 동구청`.

## Collected Fields

| Field | Source |
|---|---|
| `title` | list/detail course title |
| `branch` | normalized detail `교육장소` |
| `address` | venue address from `교육장소` or `교육장소주소` |
| `period` | detail `교육기간` |
| `schedule_raw` | detail `교육기간` and `교육시간` |
| `target` | detail `교육대상` |
| `fee` | list fee |
| `status` | list/detail application status |
| `description` | detail `강의내용` |
| `instructor` | detail `강사` |
| `raw_url` | generated detail URL |

## Validation

Command:

```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_DONGGU_GO_KR_9A7A5E6F.py --limit 10 --max-pages 3 --timeout 30
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
python -X utf8 run_crawlers.py --providers MUNI_WWW_DONGGU_GO_KR_9A7A5E6F --limit 10 --once --ignore-active-window --ignore-worker-lock --skip-coordinate-backfill --skip-category-backfill
```

DB save result: `saved 10/10`.

## Notes

- Education periods that already ended are skipped by default.
- Courses with closed application but future education dates are still collected, with status `CLOSED`.
- Internal Dong-gu office rooms are grouped under the office branch to avoid noisy map markers.
