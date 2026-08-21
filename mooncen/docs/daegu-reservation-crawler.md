# DAEGU_RESERVATION Crawler

## Scope

- Provider: `DAEGU_RESERVATION`
- Source: 대구광역시 통합예약
- Active collection URL: `https://yeyak.daegu.go.kr/expr`
- Collection type: `api_json`
- Current parser: `daegu_reservation_expr_api+detail`

The crawler currently collects the public experience/tour reservation list. The lecture/class API exists, but direct server calls are blocked by the site's NetFUNNEL browser gate.

## API

List:

```text
POST https://yeyak.daegu.go.kr/api/v1/res/expr/user/expr-prod-list
```

Detail:

```text
POST https://yeyak.daegu.go.kr/api/v1/res/expr/user/expr-prod-detail
```

Lecture list checked but not used:

```text
POST https://yeyak.daegu.go.kr/api/v1/res/lect/user/user-lect-rsvt-list
```

The lecture endpoint returns a NetFUNNEL error when called outside the browser flow.

## Fields

The parser fills:

- `title`
- `branch` and `branch_code`
- `address`, `venue_name`, `venue_address`, `room`
- `period`
- `schedule_raw`
- `target`
- `fee`
- `status`
- `description`
- `image_url`
- `application_url`
- `application_type`
- `capacity_total`
- `phone`
- `program_type`

Branches are split by `instNm`, so each facility can be geocoded and shown separately on the map.

## Quality

Latest test:

```powershell
python -X utf8 Crawler\generated_yaml\DAEGU_RESERVATION.py --per-target-limit 0 --max-pages 4 --detail-limit 100 --timeout 30
python -X utf8 tools\report_municipal_crawler_quality.py --report logs\municipal_crawler_reports\municipal_yaml_crawler_20260605_084229.yaml --limit 15
```

Result:

| provider | rows | pages | detail | parser | grade | core | important |
| --- | ---: | ---: | ---: | --- | --- | ---: | ---: |
| DAEGU_RESERVATION | 92 | 3 | 92 | daegu_reservation_expr_api+detail | A | 100.0% | 100.0% |

The source API returned 93 raw rows, but one `(instId, ftrPrgrmId)` pair was duplicated. The crawler deduped this to 92 rows.

## Run

Sample only:

```powershell
python -X utf8 Crawler\generated_yaml\DAEGU_RESERVATION.py --per-target-limit 10 --max-pages 1 --detail-limit 10 --timeout 30
```

Full collection without DB save:

```powershell
python -X utf8 Crawler\generated_yaml\DAEGU_RESERVATION.py --per-target-limit 0 --max-pages 4 --detail-limit 100 --timeout 30
```

Save to DB:

```powershell
python -X utf8 Crawler\generated_yaml\DAEGU_RESERVATION.py --save-db --per-target-limit 0 --max-pages 4 --detail-limit 100 --timeout 30
```
