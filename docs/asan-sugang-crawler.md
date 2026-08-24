# Asan Sugang Crawler

## Summary

- Provider: `MUNI_SUGANG_ASAN_GO_KR_FF504CD1`
- Name: 아산시평생학습관 평생학습강좌
- URL: `https://sugang.asan.go.kr/ilms/learning/learningList.do`
- Parser: list table plus `learningDetail.do?lng_id=...` detail page.
- Branch split: uses detail `교육장소` as branch.

## Collected Fields

| Field | Source |
|---|---|
| `title` | list/detail course title |
| `branch` | detail `교육장소` |
| `address` | inferred as `충청남도 아산시 {교육장소}` when possible |
| `period` | detail `교육기간` |
| `schedule_raw` | detail `교육기간` and `교육시간` |
| `target` | detail `교육대상` |
| `fee` | detail `수강료` |
| `material_fee` | detail `재료비` when extractable |
| `status` | detail `신청상태` and `교육상태` |
| `description` | detail `강좌소개`, `강의계획서`, `주의사항` |
| `raw_url` | generated detail URL |

## Validation

Command:

```bash
python -X utf8 Crawler/generated_yaml/MUNI_SUGANG_ASAN_GO_KR_FF504CD1.py --limit 10 --max-pages 3 --timeout 30
```

Result:

| Metric | Value |
|---|---:|
| Rows | 10 |
| Score | 96.0 |
| Grade | A |
| title | 10 |
| branch | 10 |
| address | 10 |
| period | 10 |
| schedule_raw | 10 |
| target | 10 |
| fee | 10 |
| status | 10 |
| description | 6 |
| raw_url | 10 |

DB save command:

```bash
python -X utf8 run_crawlers.py --providers MUNI_SUGANG_ASAN_GO_KR_FF504CD1 --limit 10 --once --ignore-active-window --ignore-worker-lock --skip-coordinate-backfill --skip-category-backfill
```

DB save result: `saved 10/10`.

## Notes

- Courses whose education period already ended are skipped by default.
- Some detail pages do not contain a separate description. This is why `description` can be lower than other fields.
- The site does not provide a full postal address in the course detail, so branch address is inferred from the venue text and should be handled by the address backfill operation.
