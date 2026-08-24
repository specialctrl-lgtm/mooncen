# Paju PCY FMCS Crawler

## Scope

- Provider: `MUNI_PAJU_PCY_OR_KR_412053A6`
- Source: `https://paju.pcy.or.kr/fmcs/2?center=PJYF01&event=1050000000&class=1050020000`
- Parser: `paju_pcy_fmcs_category_api`
- Wrapper: `Crawler/generated_yaml/MUNI_PAJU_PCY_OR_KR_412053A6.py`

## Collection Rule

The original URL has `center`, `event`, and `class` query parameters, but the working FMCS API filter is:

- `company_code`
- `category_cd`
- `category_level`
- `search_type=%`

The crawler now:

1. Reads centers from `rest/common/company?type=L`.
2. Reads each center's category tree from `rest/common/category`.
3. Calls `rest/lecture/list` for each center/category combination.
4. Deduplicates repeated lecture rows by provider, branch, title, and class code.
5. Follows detail pages to fill description and product duration.

## Fields

Filled fields:

- `title`
- `branch`, `branch_code`
- `category`
- `raw_url`, `application_url`
- `status`
- `fee`
- `period`
- `schedule_raw`
- `target`
- `instructor`
- `capacity`
- `description`

The source does not expose a calendar date range for many sports lectures. Detail pages expose product duration such as `1개월`, so the parser stores that as `period`.

## Validation

Sample:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_PAJU_PCY_OR_KR_412053A6.py --per-target-limit 20 --max-pages 2 --detail-limit 20 --timeout 30
```

Full check:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_PAJU_PCY_OR_KR_412053A6.py --per-target-limit 0 --max-pages 2 --detail-limit 250 --timeout 30
python -X utf8 tools\report_municipal_crawler_quality.py --report logs\municipal_crawler_reports\municipal_yaml_crawler_20260605_114250.yaml --limit 10
```

Latest result:

| provider | rows | pages | detail | parser | grade | core | important |
| --- | ---: | ---: | ---: | --- | --- | ---: | ---: |
| `MUNI_PAJU_PCY_OR_KR_412053A6` | 160 | 45 | 160 | `paju_pcy_fmcs_category_api` | A | 100.0% | 95.0% |

Field counts:

| field | count |
| --- | ---: |
| title | 160 |
| branch | 160 |
| raw_url | 160 |
| period | 160 |
| schedule_raw | 160 |
| fee | 160 |
| status | 160 |
| target | 160 |
| description | 112 |

## Run

Save to DB:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_PAJU_PCY_OR_KR_412053A6.py --save-db --per-target-limit 0 --max-pages 2 --detail-limit 250 --timeout 30
```

Run through the worker:

```powershell
python -X utf8 run_crawlers.py --providers MUNI_PAJU_PCY_OR_KR_412053A6 --once --ignore-active-window
```
