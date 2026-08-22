# Bukgu Daegu Reservation Crawler

## Scope

- Provider: `MUNI_WWW_BUK_DAEGU_KR_RESERVATION`
- Source: Bukgu Daegu reservation pages
- Parser: `bukgu_daegu_lec_list+detail`
- Wrapper: `Crawler/generated_yaml/MUNI_WWW_BUK_DAEGU_KR_RESERVATION.py`

The old provider name `DAEGU_BUKGU_RESERVATION` was replaced because the target is Daegu Buk-gu and should follow the municipal provider naming convention.

## Collection URLs

The crawler cycles these Bukgu reservation menus:

- `https://www.buk.daegu.kr/reserve/index.do?menu_id=00002617` lifelong-learning programs
- `https://www.buk.daegu.kr/reserve/index.do?menu_id=00002777` information education
- `https://www.buk.daegu.kr/reserve/index.do?menu_id=00002965` foreign-language education
- `https://www.buk.daegu.kr/reserve/index.do?menu_id=00002619` resident-center courses

The health CPR guide page is not included because it does not expose the same structured course list.

## Fields

The parser reads `.lec_list` rows and follows each detail page when available.

Filled fields:

- `title`
- `branch`, `branch_code`
- `category`
- `period`
- `apply_period`
- `schedule_raw`
- `target`
- `capacity`
- `fee`
- `status`
- `instructor`
- `room`, `venue_name`
- `address` when the venue can be trusted
- `description`
- `raw_url`, `application_url`

Known Bukgu office venues are mapped to `Daegu Buk-gu Oksan-ro 65`. Other venue names are preserved without forcing an address so map coordinates are not polluted by guessed locations.

## Validation

Sample command:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_WWW_BUK_DAEGU_KR_RESERVATION.py --per-target-limit 20 --max-pages 2 --detail-limit 20 --timeout 30
```

Quality command:

```powershell
python -X utf8 tools\report_municipal_crawler_quality.py --report logs\municipal_crawler_reports\municipal_yaml_crawler_20260605_112454.yaml --limit 10
```

Latest result:

| provider | rows | pages | detail | parser | grade | core | important |
| --- | ---: | ---: | ---: | --- | --- | ---: | ---: |
| `MUNI_WWW_BUK_DAEGU_KR_RESERVATION` | 143 | 16 | 120 | `bukgu_daegu_lec_list+detail` | A | 100.0% | 91.8% |

Field counts:

| field | count |
| --- | ---: |
| title | 143 |
| branch | 143 |
| raw_url | 143 |
| period | 143 |
| schedule_raw | 120 |
| fee | 96 |
| status | 143 |
| target | 143 |
| description | 143 |

## Run

Sample only:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_WWW_BUK_DAEGU_KR_RESERVATION.py --per-target-limit 20 --max-pages 2 --detail-limit 20 --timeout 30
```

Save to DB:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_WWW_BUK_DAEGU_KR_RESERVATION.py --save-db --per-target-limit 0 --max-pages 5 --detail-limit 120 --timeout 30
```

Run through the worker:

```powershell
python -X utf8 run_crawlers.py --providers MUNI_WWW_BUK_DAEGU_KR_RESERVATION --once --ignore-active-window
```
