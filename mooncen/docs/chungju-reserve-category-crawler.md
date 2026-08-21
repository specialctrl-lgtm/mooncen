# Chungju Reserve Category Crawler

## Provider

- Provider: `MUNI_WWW_CHUNGJU_GO_KR_7EE8620A`
- Source: `https://www.chungju.go.kr/rev/reserve/99?document_category_srl=37`
- Parser: `chungju_reserve_category+detail`
- Domain category: `공공예약`
- Collection category: `공공예약`

## Collection Rule

The Chungju reservation page exposes resident-center lecture categories in the top category bar.

The crawler now:

- discovers `.modules_lecture .category a[href*='document_category_srl']`
- skips `전체보기` to avoid duplicate all-category rows
- iterates each 읍면동 center category
- paginates with `page=N`
- follows each detail link using `action=read&action-value=...`

## Detail Enrichment

The list card only contains basic fields. The detail table is required for high-quality data.

Detail fields collected:

- `기관명`
- `강좌명`
- `접수방식`
- `교육 기간`
- `교육요일`
- `수업시간`
- `접수 기간`
- `정원`
- `모집연령`
- `수업료`
- `강사`
- `문의 연락처`
- `교육장`
- `교육장주소`
- `수업 내용`

Expired education periods are skipped through the common lifecycle filter.

## Branch Split

Rows use the visible `기관명` as the branch. This keeps map markers at the actual resident-center/institution level instead of one city-level marker.

## Quality Check

Command:

```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_CHUNGJU_GO_KR_7EE8620A.py --per-target-limit 10 --max-pages 1 --detail-limit 10 --timeout 30
python -X utf8 tools/report_municipal_crawler_quality.py --report logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_101949.yaml --limit 15
```

Result:

| Rows | Grade | Parser | Core | Important | Period | Schedule | Fee | Status | Target | Description |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 10 | A | `chungju_reserve_category+detail` | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |

## Notes

- `max_pages` is applied per center category.
- `detail_limit=0` fetches all detail pages.
- The 10-row sample discovered 25 center categories and 355 course links.
