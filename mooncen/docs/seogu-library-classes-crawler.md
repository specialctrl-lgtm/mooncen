# Daejeon Seogu Library Classes Crawler

## Scope

- Provider: `MUNI_WWW_SEOGU_GO_KR_A27782FE`
- Source: `https://www.seogu.go.kr/library/wolpyeonglib/index.do`
- Actual list page: `https://www.seogu.go.kr/library/wolpyeonglib/contents/learning/lib/02/lib.02.001.motion?mnucd=MENU0600030`
- Domain category: `도서관`
- Collection type: static HTML table plus detail pages

The YAML target points to the Wolpyeong Library homepage. The dedicated parser redirects internally to the visible `행사 및 강좌 신청` list and parses the course table.

## Parser

- Parser name: `seogu_library_classes+detail`
- List selector: `table.tbl_basic tbody tr`
- Detail selector: `table.tbl_basic_view`
- Detail URL is reconstructed from JavaScript calls like `fn_egov_select(..., 'LEC_000000004659')`.

Field mapping:

| Field | Source |
| --- | --- |
| `title` | List title, with detail title as override when available |
| `branch`, `venue_name` | Library branch from URL, e.g. `월평도서관` |
| `address`, `venue_address` | Static branch address map |
| `period` | List/detail `일시` |
| `schedule_raw` | `일시 + 요일 + 시간` |
| `apply_period` | Detail `신청기간` |
| `target` | Detail/list `대상` |
| `capacity`, `waitlist_total` | Detail/list 모집 and 예비 인원 |
| `instructor` | Detail `강사` |
| `fee` | Fixed to `무료` because the page does not expose paid pricing |
| `description` | Detail `강의내용` |
| `image_url` | First image in detail content, when present |
| `application_url` | Reconstructed detail URL |

## Validation

Sample command:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_WWW_SEOGU_GO_KR_A27782FE.py --per-target-limit 10 --max-pages 2 --detail-limit 10 --timeout 30
```

Quality command:

```powershell
python -X utf8 tools\report_municipal_crawler_quality.py --report logs\municipal_crawler_reports\municipal_yaml_crawler_20260605_104349.yaml --limit 15
```

Result:

| Provider | Rows | Parser | Grade | Core | Important |
| --- | ---: | --- | --- | ---: | ---: |
| `MUNI_WWW_SEOGU_GO_KR_A27782FE` | 10 | `seogu_library_classes+detail` | A | 100.0% | 100.0% |

Report files:

- `logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_104349.yaml`
- `logs/municipal_crawler_quality/municipal_yaml_crawler_20260605_104349_quality.md`

## Notes

The list includes historical library events. DB writes still use the shared lifecycle rule, so ended courses are skipped or later deactivated according to the common crawler policy.
