# NATIONAL_ECOLOGY_CENTER Crawler

## Scope

Provider: `NATIONAL_ECOLOGY_CENTER`

Source: `https://www.nie.re.kr/nieResve/pgm/eclgyEdc/list2.do?menuNo=600010`

This parser collects the National Institute of Ecology reservation-service education cards and follows detail pages opened by `fnProgrmView('E...')`.

## Parser

Parser name: `national_ecology_cards`

Collected fields:

- `title`: card `h5`
- `branch`: configured branch, `국립생태원`
- `category`: card category chips except status
- `status`: category chip such as `접수중`, `마감`, `준비중`, `예약마감`
- `period`: `교육기간`
- `apply_period`: `접수기간`
- `schedule_raw`: `교육요일`
- `target`: age/school target chip
- `fee`: detail-page fee text. Calendar UI labels such as `관리` are ignored; repeated `0원` values are normalized to `무료`.
- `description`: detail table or detail text fallback
- `image_url`: card image
- `raw_url` / `application_url`: `view2.do?menuNo=600010&edcId=...`

## Validation

```powershell
python -X utf8 Crawler\generated_yaml\NATIONAL_ECOLOGY_CENTER.py --per-target-limit 0 --max-pages 10 --detail-limit 80 --timeout 25 --save-db --mark-stale
```

Latest local result:

- Report: `logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_041902.yaml`
- Collected: `60`
- Saved active rows: `23`
- Parser: `national_ecology_cards`
- Pagination: detected
- Core fields: title, branch, period, schedule, target, raw URL all `60/60`
- Detail-enriched fields: description `20/60`, fee `18/60`

## Location

Branch coordinates were backfilled with Google Geocoding using query `National Institute of Ecology Seocheon`.

- Address: `대한민국 충청남도 서천군 마서면 금강로 1210`
- Latitude: `36.0295887`
- Longitude: `126.7247973`

