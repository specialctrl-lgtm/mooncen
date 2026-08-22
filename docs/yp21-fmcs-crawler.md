# YP21 FMCS Crawler

## Scope

- Provider: `MUNI_WWW_YP21_GO_KR_EA0D7B81`
- Source: `https://www.yp21.go.kr/pool/fmcs/9`
- Parser: `yp21_fmcs_category_api`
- Wrapper: `Crawler/generated_yaml/MUNI_WWW_YP21_GO_KR_EA0D7B81.py`

## Collection Rule

The site is an FMCS pool reservation system. Its JSON endpoints only work with POST.

The crawler:

1. Reads centers from `rest/common/company?type=L`.
2. Reads the swim category tree from `rest/common/category`.
3. Calls `rest/lecture/list` with `company_code`, `category_cd`, `category_level`, and `search_type=%`.
4. Deduplicates repeated lecture rows.
5. Follows detail pages to fill product duration and refine fee values.

## Fields

Filled fields:

- `title`
- `branch`, `branch_code`
- `category`
- `raw_url`
- `status`
- `fee`
- `period`
- `schedule_raw`
- `target`
- `instructor`
- `capacity`
- `address`

The source exposes product duration such as `3개월` rather than a calendar date range. The crawler stores that value in `period`.

The detail page does not expose a course introduction body, so `description` is expected to remain empty.

## Validation

Sample:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_WWW_YP21_GO_KR_EA0D7B81.py --per-target-limit 20 --max-pages 2 --detail-limit 20 --timeout 30
```

Full check:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_WWW_YP21_GO_KR_EA0D7B81.py --per-target-limit 0 --max-pages 2 --detail-limit 100 --timeout 30
python -X utf8 tools\report_municipal_crawler_quality.py --report logs\municipal_crawler_reports\municipal_yaml_crawler_20260605_114813.yaml --limit 10
```

Latest result:

| provider | rows | pages | detail | parser | grade | core | important |
| --- | ---: | ---: | ---: | --- | --- | ---: | ---: |
| `MUNI_WWW_YP21_GO_KR_EA0D7B81` | 58 | 7 | 58 | `yp21_fmcs_category_api` | A | 100.0% | 83.3% |

Field counts:

| field | count |
| --- | ---: |
| title | 58 |
| branch | 58 |
| raw_url | 58 |
| period | 58 |
| schedule_raw | 58 |
| fee | 58 |
| status | 58 |
| target | 58 |
| description | 0 |

## Run

Save to DB:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_WWW_YP21_GO_KR_EA0D7B81.py --save-db --per-target-limit 0 --max-pages 2 --detail-limit 100 --timeout 30
```

Run through the worker:

```powershell
python -X utf8 run_crawlers.py --providers MUNI_WWW_YP21_GO_KR_EA0D7B81 --once --ignore-active-window
```
