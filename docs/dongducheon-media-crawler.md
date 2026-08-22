# Dongducheon Media Center Crawler

## Provider

- Provider: `MUNI_WWW_DDC_GO_KR_33877EF5`
- Name: `동두천시청`
- Source URL: `https://www.ddc.go.kr/media/selectBbsNttList.do?bbsNo=201&key=2136`
- Scope: 동두천미디어센터 미디어교육 교육신청

## Parser

- List parser: `ul.guide_item_list li.guide_item`
- Detail parser: `table.table.type2.bbs_default.view`
- Detail fields:
  - title: table caption before ` - `
  - reception_period: `모집기간`
  - capacity_text: `모집인원`
  - fee: `수강료`
  - period: `교육기간`
  - schedule_raw: `교육시간`
  - branch: `동두천미디어센터` + `교육장소`
  - image_url: `/DATA/bbs/201/...` image

## Branch

- Base branch: `동두천미디어센터`
- Address: `경기도 동두천시 동두천로 314`
- Classroom names from `교육장소` are appended to the branch name.

## Rules

- Courses whose education end date is earlier than the current date are skipped.
- The source status can be `접수마감` while the education period is still active; those rows are collected because they are still ongoing courses.
- If a page is entirely expired, crawling stops because older pages are expected to be older courses.

## Commands

```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_DDC_GO_KR_33877EF5.py --limit 10 --max-pages 2
python -X utf8 Crawler/generated_yaml/MUNI_WWW_DDC_GO_KR_33877EF5.py --save-db --limit 10
python -X utf8 run_crawlers.py --providers MUNI_WWW_DDC_GO_KR_33877EF5 --limit 10 --once --ignore-active-window --ignore-worker-lock --skip-coordinate-backfill --skip-category-backfill
```

## Validation

| Date | Rows | Saved | Score | Notes |
|---|---:|---:|---:|---|
| 2026-06-08 | 4 | 4 | 100.0 | Active education-period rows only; expired older rows skipped. |

