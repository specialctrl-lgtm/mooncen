# MUNI_WWW_ULSANNAMGU_GO_KR_E36CF0FF Crawler

## Scope

- Provider: `MUNI_WWW_ULSANNAMGU_GO_KR_E36CF0FF`
- Source: 울산광역시 남구청 주민자치프로그램
- URL: `https://www.ulsannamgu.go.kr/inhParticipation/residentAutonomy.do?dongName=12`
- Parser: `ulsannamgu_resident_autonomy_table`
- Collection type: `static_html`

The crawler parses the resident-autonomy program table and iterates every `dongName` option from the education-institution select box.

## Branch Iteration

The site exposes branches as `select#dongName` options:

- 신정1동
- 신정2동
- 신정3동
- 신정4동
- 신정5동
- 달동
- 삼산동
- 삼호동
- 무거동
- 옥동
- 야음장생포동
- 대현동
- 수암동
- 선암동
- 기타

Each branch is paginated with `pageIndex`. The crawler follows pagination per branch and stores the visible dong as `branch`.

## Fields

The parser fills:

- `title`
- `branch`, `branch_code`
- `category`
- `schedule_raw`
- `room`, `venue_name`
- `capacity_total`
- `fee`
- `target`
- `status`
- `phone`
- `description`
- `application_type`
- `application_method_raw`

The page does not expose course period or application period. `period` is therefore intentionally left empty. The table header says the listed fee is for three months, so this is preserved in `description`.

## Quality

Latest full test:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_WWW_ULSANNAMGU_GO_KR_E36CF0FF.py --per-target-limit 0 --max-pages 3 --detail-limit 0 --timeout 30
python -X utf8 tools\report_municipal_crawler_quality.py --report logs\municipal_crawler_reports\municipal_yaml_crawler_20260605_090505.yaml --limit 15
```

Result:

| provider | rows | pages | parser | grade | core | important |
| --- | ---: | ---: | --- | --- | ---: | ---: |
| MUNI_WWW_ULSANNAMGU_GO_KR_E36CF0FF | 250 | 31 | ulsannamgu_resident_autonomy_table | A | 100.0% | 83.3% |

Branch distribution in the latest sample:

| branch | rows |
| --- | ---: |
| 신정2동 | 26 |
| 삼산동 | 24 |
| 삼호동 | 22 |
| 신정1동 | 20 |
| 대현동 | 20 |
| 수암동 | 19 |
| 신정4동 | 18 |
| 옥동 | 18 |
| 무거동 | 17 |
| 선암동 | 17 |
| 신정3동 | 15 |
| 신정5동 | 13 |
| 달동 | 12 |
| 야음장생포동 | 9 |

## Run

Sample:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_WWW_ULSANNAMGU_GO_KR_E36CF0FF.py --per-target-limit 10 --max-pages 3 --detail-limit 0 --timeout 30
```

Full collection without DB save:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_WWW_ULSANNAMGU_GO_KR_E36CF0FF.py --per-target-limit 0 --max-pages 3 --detail-limit 0 --timeout 30
```

Save to DB:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_WWW_ULSANNAMGU_GO_KR_E36CF0FF.py --save-db --per-target-limit 0 --max-pages 3 --detail-limit 0 --timeout 30
```
