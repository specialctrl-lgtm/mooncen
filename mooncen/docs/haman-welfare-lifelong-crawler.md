# HAMAN_WELFARE_LIFELONG_COURSE Crawler

## Scope

- Provider: `HAMAN_WELFARE_LIFELONG_COURSE`
- Source: 함안군 통합예약 교육/강좌
- Main URL: `https://www.haman.go.kr/02697/02708.web`
- Parser: `haman_lifelong_agency_list+detail`
- Collection type: `static_html`

The crawler does not use the main-page JSON card endpoint because that endpoint returns only 10 display rows. It parses the official HTML list pages with `cpage` pagination instead.

## Branch Iteration

The left education/lecture menu is collected by agency:

| agency | branch | URL |
| --- | --- | --- |
| `AGENCY001` | 함안군평생교육원 | `/02697/02705.web` |
| `AGENCY024` | 평생학습센터 | `/02697/02705.web` |
| `AGENCY003` | 군민정보화교육 | `/02697/02707.web` |
| `AGENCY005` | 종합사회복지관 | `/02697/02708.web` |
| `AGENCY006` | 여성센터 | `/02697/02709.web` |
| empty | 복합문학관 | `/02697/06826.web` |

Rows are saved with the visible education institution as `branch`. The visible classroom/place is stored as `venue_name` and `room`.

## Fields

The parser fills:

- `title`
- `branch`, `branch_code`
- `status`
- `fee`
- `period`
- `apply_period`
- `schedule_raw`
- `room`, `venue_name`
- `capacity_current`, `capacity_total`
- `application_url`
- `application_type`
- `application_method_raw`
- `description`
- `instructor`
- `phone`

Expired course periods are skipped during collection.

The site does not expose a separate target/age field on the list or detail pages, so `target` may remain empty.

## Quality

Latest full test:

```powershell
python -X utf8 Crawler\generated_yaml\HAMAN_WELFARE_LIFELONG_COURSE.py --per-target-limit 0 --max-pages 60 --detail-limit 120 --timeout 30
python -X utf8 tools\report_municipal_crawler_quality.py --report logs\municipal_crawler_reports\municipal_yaml_crawler_20260605_085309.yaml --limit 15
```

Result:

| provider | rows | pages | detail | parser | grade | core | important |
| --- | ---: | ---: | ---: | --- | --- | ---: | ---: |
| HAMAN_WELFARE_LIFELONG_COURSE | 52 | 14 | 52 | haman_lifelong_agency_list+detail | A | 100.0% | 83.3% |

Branch distribution in the latest active/future sample:

| branch | rows |
| --- | ---: |
| 종합사회복지관 | 26 |
| 여성센터 | 16 |
| 평생학습센터 | 8 |
| 군민정보화교육 | 2 |

## Run

Sample:

```powershell
python -X utf8 Crawler\generated_yaml\HAMAN_WELFARE_LIFELONG_COURSE.py --per-target-limit 10 --max-pages 2 --detail-limit 10 --timeout 30
```

Full active/future collection without DB save:

```powershell
python -X utf8 Crawler\generated_yaml\HAMAN_WELFARE_LIFELONG_COURSE.py --per-target-limit 0 --max-pages 60 --detail-limit 120 --timeout 30
```

Save to DB:

```powershell
python -X utf8 Crawler\generated_yaml\HAMAN_WELFARE_LIFELONG_COURSE.py --save-db --per-target-limit 0 --max-pages 60 --detail-limit 120 --timeout 30
```
