# PTLIB Library Crawler

## Scope

- Provider: `MUNI_WWW_PTLIB_GO_KR_D9537B1F`
- Source: `https://www.ptlib.go.kr/intro/menu/10025/program/30025/lectureList.do`
- Parser: `ptlib_lecture_list`
- Wrapper: `Crawler/generated_yaml/MUNI_WWW_PTLIB_GO_KR_D9537B1F.py`

## Collection Rule

The site uses a static HTML table and JavaScript form functions:

- `fnList(page)` changes `currentPageNo`.
- `fnDetail(lectureIdx)` submits to `lectureDetail.do`.
- `manageCd` selects a library branch.

The crawler:

1. Reads all `manageCd` options from the list page.
2. Cycles each library branch.
3. Requests paginated list pages with `recordCountPerPage=50`.
4. Extracts `lectureIdx` from `fnDetail('...')`.
5. Follows `lectureDetail.do?lectureIdx=...`.
6. Skips courses whose education period has already ended.

## Fields

Filled fields:

- `title`
- `branch`, `branch_code`
- `raw_url`, `application_url`
- `period`
- `apply_period`
- `schedule_raw`
- `target`
- `capacity`
- `status`
- `room`, `venue_name`

The detail page does not expose fee or a course introduction body, so `fee` and `description` are expected to remain empty.

## Validation

Sample:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_WWW_PTLIB_GO_KR_D9537B1F.py --per-target-limit 20 --max-pages 2 --detail-limit 20 --timeout 30
```

Full check:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_WWW_PTLIB_GO_KR_D9537B1F.py --per-target-limit 0 --max-pages 5 --detail-limit 200 --timeout 30
python -X utf8 tools\report_municipal_crawler_quality.py --report logs\municipal_crawler_reports\municipal_yaml_crawler_20260605_115510.yaml --limit 10
```

Latest result:

| provider | rows | pages | detail | parser | grade | core | important |
| --- | ---: | ---: | ---: | --- | --- | ---: | ---: |
| `MUNI_WWW_PTLIB_GO_KR_D9537B1F` | 32 | 21 | 32 | `ptlib_lecture_list` | A | 100.0% | 66.7% |

Field counts:

| field | count |
| --- | ---: |
| title | 32 |
| branch | 32 |
| raw_url | 32 |
| period | 32 |
| schedule_raw | 32 |
| fee | 0 |
| status | 32 |
| target | 32 |
| description | 0 |

## Run

Save to DB:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_WWW_PTLIB_GO_KR_D9537B1F.py --save-db --per-target-limit 0 --max-pages 5 --detail-limit 200 --timeout 30
```

Run through the worker:

```powershell
python -X utf8 run_crawlers.py --providers MUNI_WWW_PTLIB_GO_KR_D9537B1F --once --ignore-active-window
```
