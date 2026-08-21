# Geoje Library Course Crawler

## Provider

- Provider: `MUNI_LIB_GEOJE_GO_KR_401A2022`
- Source: `https://lib.geoje.go.kr/com/requestPage.do?selMenuNo=104030100&returnUrl=/culture/d030100.do`
- Actual list: `https://lib.geoje.go.kr/com/requestPage.do?selMenuNo=104030200&returnUrl=/culture/d030200.do`
- Parser: `geoje_library_course_table`
- Domain category: `도서관`
- Collection category: `도서관`

## Collection Rule

The YAML URL points to the online-course guide page. The actual course list is the `강좌 신청하기` page under `/culture/d030200.do`.

The crawler posts to the list form with:

- `currentPageNo`
- `cou_all=전체`
- `cou_search=b_subject`
- empty date and keyword filters

It then parses `table.tbl-type01 tbody tr`.

## Detail Policy

The site's `couDetail(courseId, statusCode)` form submits to `/culture/d030200_detail.do`, but unauthenticated requests redirect to login. Because the list table already exposes the main course fields, the crawler uses list rows as the source of truth.

## Fields

The list table provides:

- `branch`
- `title`
- `target`
- `capacity`
- `waitlist_capacity`
- `capacity_current`
- `period`
- `apply_period`
- `status`

Additional mapping:

- `fee`: `무료`, based on the guide page policy
- `schedule_raw`: same as the visible course period because the list period includes date and time
- `contact`: mapped from the branch library phone number

## Branch Split

Visible branch labels are mapped to full library names:

- 하청 -> 거제시립하청도서관
- 아주 -> 거제시립아주도서관
- 옥포 -> 거제시립옥포도서관
- 장승포 -> 거제시립장승포도서관
- 장평 -> 거제시립장평도서관
- 수양 -> 거제시립수양도서관

## Quality Check

Command:

```bash
python -X utf8 Crawler/generated_yaml/MUNI_LIB_GEOJE_GO_KR_401A2022.py --per-target-limit 10 --max-pages 2 --detail-limit 10 --timeout 30
python -X utf8 tools/report_municipal_crawler_quality.py --report logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_102623.yaml --limit 15
```

Result:

| Rows | Grade | Parser | Core | Important | Period | Schedule | Fee | Status | Target | Description |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 10 | A | `geoje_library_course_table` | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |

## Notes

- `max_pages` controls list pagination.
- Detail pages are not fetched because they require login.
- DB save command: `python -X utf8 Crawler/generated_yaml/MUNI_LIB_GEOJE_GO_KR_401A2022.py --save-db --per-target-limit 0 --max-pages 0 --detail-limit 0 --timeout 30`.
