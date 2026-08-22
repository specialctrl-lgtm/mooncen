# JHED JNE Experience Crawler

## Summary

- Provider: `MUNI_JHED_JNE_GO_KR_16474ED5`
- Name: 전라남도장흥교육지원청 통합예약 견학체험
- Source: `https://yeyak.jne.kr/yeyak/exprn/selectExprnList.do?mi=10205166&srchAt=Y&pageIndex=10&srchRsSysId=jhed`
- Parser: JNE integrated reservation `selectExprnList.do` table plus `selectExprnInfo.do` detail page.
- Branch split: fixed branch `전라남도장흥교육지원청`.

## Collected Fields

| Field | Source |
|---|---|
| `title` | list link title plus category text |
| `branch` | detail/list operating agency, fallback fixed branch |
| `address` | reservation area, fallback education office address |
| `period` | detail `체험기간` |
| `schedule_raw` | detail calendar reservation time |
| `target` | detail `체험대상` and `신청대상` |
| `fee` | fixed `무료` |
| `status` | list/detail reservation status |
| `description` | detail `이용안내`, `체험안내`, `유의사항` sections |
| `image_url` | not available on current pages |

## Validation

Command:

```bash
python -X utf8 Crawler/generated_yaml/MUNI_JHED_JNE_GO_KR_16474ED5.py --limit 10 --max-pages 1 --timeout 30
```

Result:

| Metric | Value |
|---|---:|
| Rows | 10 |
| Score | 90.0 |
| title | 10 |
| branch | 10 |
| address | 10 |
| period | 10 |
| schedule_raw | 10 |
| target | 10 |
| fee | 10 |
| status | 10 |
| description | 10 |
| image_url | 0 |

DB save command:

```bash
python -X utf8 run_crawlers.py --providers MUNI_JHED_JNE_GO_KR_16474ED5 --limit 10 --once --ignore-active-window --ignore-worker-lock --skip-coordinate-backfill --skip-category-backfill
```

DB save result: `saved 10/10`.

## Notes

- Test and practice rows are skipped.
- Expired education periods are skipped by default unless `--include-expired` is supplied.
- Current active sample rows are mostly `CLOSED` because the reservation application period is closed even though the experience date is still in the future.
