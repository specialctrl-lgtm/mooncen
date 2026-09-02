# National Museum Of Korea MODU Crawler

## Provider

- Provider: `NATIONAL_MUSEUM_OF_KOREA`
- Source: `https://modu.museum.go.kr/learn`
- Parser: `modu_learn_list+detail`
- Domain category: `박물관/과학관`
- Collection category: `박물관/과학관`

## Collection Rule

The original `museum.go.kr` target only exposed low-value static pages. Education reservation data is served by the MODU education platform.

The crawler now:

- iterates all 14 museum filter values from the `museum` checkbox list
- calls `/learn?museum={id}&searchApplyStatus=ONGOING`
- replaces browser infinite scroll with `/learn/append?page=N&size=8`
- follows `/learn/detail/{id}` for detail fields

This avoids Selenium while matching the site's search button and scroll-loading behavior.

## Branch Split

Rows are split by the visible museum name so map markers can be created per branch:

- 국립중앙박물관
- 국립경주박물관
- 국립광주박물관
- 국립전주박물관
- 국립대구박물관
- 국립부여박물관
- 국립공주박물관
- 국립진주박물관
- 국립청주박물관
- 국립김해박물관
- 국립제주박물관
- 국립춘천박물관
- 국립나주박물관
- 국립익산박물관

Known branch addresses are filled in the crawler for map geocoding.

## Fields

The list page provides:

- `title`
- `branch`
- `period`
- `target`
- `status`
- `image_url`

The detail page table provides:

- `apply_period`
- `fee`
- `room`
- `instructor`
- `application_method_raw`
- `category`
- `description`
- round schedule rows for `schedule_raw`

## Quality Check

Command:

```bash
python -X utf8 Crawler/generated_yaml/NATIONAL_MUSEUM_OF_KOREA.py --per-target-limit 10 --max-pages 2 --detail-limit 10 --timeout 30
python -X utf8 tools/report_municipal_crawler_quality.py --report logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_101336.yaml --limit 15
```

Result:

| Rows | Grade | Parser | Core | Important | Period | Schedule | Fee | Status | Target | Description |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 10 | A | `modu_learn_list+detail` | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |

## Notes

- `max_pages` is applied per museum filter.
- `detail_limit=0` fetches all discovered detail pages.
- The wrapper is `Crawler/generated_yaml/NATIONAL_MUSEUM_OF_KOREA.py`.
