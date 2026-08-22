# Yongsan Education Integrated Crawler

## Summary

- Provider: `MUNI_YEDU_YONGSAN_GO_KR_4E97CC33`
- Name: 용산구교육종합포털 통합 수강신청
- Source: `https://yedu.yongsan.go.kr`
- Parser: common list table plus `form.do` detail page.
- Branch split: uses the detail `교육장` or `장소` value as branch.

## Target URLs

| Type | URL |
|---|---|
| 정보화교육 | `https://yedu.yongsan.go.kr/site/edtotal/lesson/userlist.do?sitecdv=S0000500&decorator=user27EdTotal&menucdv=02020000&searchEdutypecdv=F0810101` |
| 평생학습관 | `https://yedu.yongsan.go.kr/site/edtotal/lifeStudy/userlist.do?sitecdv=S0000500&menucdv=02070000&decorator=user27EdTotal` |
| 서로서로학교 | `https://yedu.yongsan.go.kr/site/edtotal/eachOther/userlist.do?sitecdv=S0000500&menucdv=02040100&decorator=user27EdTotal` |
| 동네배움터 | `https://yedu.yongsan.go.kr/site/edtotal/happyStudy/userlist.do?sitecdv=S0000500&menucdv=02060000&decorator=user27EdTotal` |

## Collected Fields

| Field | Source |
|---|---|
| `title` | detail `강좌명` |
| `branch` | detail `교육장` or `장소` |
| `address` | known branch mapping or address text in description |
| `period` | detail `교육기간` |
| `schedule_raw` | `교육기간`, `수업요일`, `교육시간` |
| `target` | detail `접수나이`, normalized |
| `fee` | detail/list `수강료` |
| `status` | detail/list `접수상태` |
| `description` | detail `강좌소개` and `강좌계획서` |
| `material_fee` | extracted from description when present |

## Validation

Command:

```bash
python -X utf8 Crawler/generated_yaml/MUNI_YEDU_YONGSAN_GO_KR_4E97CC33.py --limit 10 --max-pages 5 --timeout 30
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
python -X utf8 run_crawlers.py --providers MUNI_YEDU_YONGSAN_GO_KR_4E97CC33 --limit 10 --once --ignore-active-window --ignore-worker-lock --skip-coordinate-backfill --skip-category-backfill
```

DB save result: `saved 10/10`.

## Notes

- Education periods that already ended are skipped by default.
- Closed-but-not-ended courses are still collected because the course period can still be active or upcoming.
- This provider replaces multiple manually added Yongsan URLs with one integrated crawler.
