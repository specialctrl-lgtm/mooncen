# SEONGNAM_BAEUMSOOP Crawler

## Source

- Provider: `SEONGNAM_BAEUMSOOP`
- Site: `https://sugang.seongnam.go.kr`
- List pattern: `/ilms/learning/learningList.do?searchUseYn=Y&searchCondition3={OFFICE_CODE}`

## Collection Strategy

`officeList.do` can return the TRACER waiting/intro page, so the crawler does not depend on that page for course extraction. It discovers known `OFFICE_########` codes from local YAML/registry/log files, validates each office through `learningList.do`, and crawls each office separately.

Current verified offices:

| Office code | Branch | Rows |
|---|---:|---:|
| `OFFICE_00000670` | 분당구청 시민정보화교육 | 5 |
| `OFFICE_00000680` | 수정구청 시민정보화교육 | 5 |
| `OFFICE_00000681` | 중원구청 시민정보화교육 | 3 |
| `OFFICE_00001080` | 녹지과 | 13 |
| `OFFICE_00002180` | 가천대학교 평생교육원 | 1 |

## Fields

The list page provides `title`, `branch`, `period`, `schedule_raw`, `status`, `capacity_total`, and `capacity_current`. Detail pages are requested after the list page session is established and are used to fill `target`, `fee`, and `description` when available.

Address is not present in the course list response. The crawler fills known office addresses for verified office codes so branches can appear on the map immediately. Course titles containing practice/test markers such as `수강신청 연습용`, `강의접수 연습용`, `실제 강좌 아님`, or `실제 강의 아님` are skipped.

## Commands

Sample:

```bash
python -X utf8 Crawler/Crawler_SeongnamBaeumsoop.py --limit 10 --office-limit 5 --max-pages 3
```

Save to DB:

```bash
python -X utf8 Crawler/Crawler_SeongnamBaeumsoop.py --office-limit 5 --max-pages 3 --save-db
```

Worker provider run:

```bash
python -X utf8 run_crawlers.py --providers SEONGNAM_BAEUMSOOP --ignore-active-window
```

## Latest Verification

2026-06-08 local run:

| Metric | Value |
|---|---:|
| Collected | 10 |
| Saved test | 2 |
| Branches checked | 4 |
| title | 10 |
| branch | 10 |
| address | 10 |
| period | 10 |
| schedule_raw | 10 |
| status | 10 |
| target | 10 |
| fee | 10 |
| description | 10 |
| image_url | 0 |
| application_url | 2 |
