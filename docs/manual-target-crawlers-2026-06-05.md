# Manual Target Crawlers

Date: 2026-06-05

## Summary

Ops Console/manual changed targets are now executable without depending on `Crawler/Crawler_MunicipalYaml.py`.

Shared crawler:

- `Crawler/generated_yaml/manual_generic_crawler.py`

Converted generated wrappers:

- `BUSAN_NATIONAL_SCIENCE_MUSEUM`
- `DAEGU_NATIONAL_SCIENCE_MUSEUM`
- `GWACHEON_NATIONAL_SCIENCE_MUSEUM`
- `GWANGJU_NATIONAL_SCIENCE_MUSEUM`
- `NATIONAL_OCEAN_SCIENCE_MUSEUM`
- `NATIONAL_SCIENCE_MUSEUM`
- `DAEGU_RESERVATION`
- `DAEJEON_OK_RESERVATION`
- `GWANGJU_RESERVATION`
- `INCHEON_RESERVATION`
- `MUNI_WWW_GN_GO_KR_5623F7DB`

Converted static provider files:

- `ANYANG_LIFELONG_LEARNING`
- `YONGIN_LIFELONG_LEARNING`
- `BUSAN_RESERVATION`
- `BABSANG_WELFARE_PROGRAM`
- `SEOUL_PUBLIC_SERVICE`

Already standalone before this work:

- `HONAM_BIOLOGICAL_RESOURCES`
- `CNALL_LECTURE`
- `SEONGNAM_BAEUMSOOP`

## Target Deduplication

Duplicate target rows were removed with:

```powershell
python -X utf8 tools\dedupe_crawl_targets.py
```

Result on 2026-06-05:

- Removed duplicate rows: `951`
- Deduplication key: `provider + url`
- Kept priority: `config/crawl_targets/*.yaml` first, then `config/collected_yaml_crawl_targets.yaml`
- `config/generated_yaml_crawler_registry.yaml` is no longer included by default because it is an execution registry, not the primary target list.

To audit without writing:

```powershell
python -X utf8 tools\dedupe_crawl_targets.py --dry-run
```

To intentionally include the execution registry:

```powershell
python -X utf8 tools\dedupe_crawl_targets.py --include-registry --dry-run
```

## Production Deployment

The production crawler provider list now includes culture-center providers and the enabled manual/other providers.

Service:

```bash
mooncenctl crawler-status
```

Deployment health:

```powershell
.\deploy_mooncen.ps1 summary
```

Expected state after deployment:

- `mooncen-crawler` active/enabled
- `CRAWLER_PROVIDERS` contains both culture-center and manual/other providers
- `run_crawlers.py` runs with `--parallel --max-workers 4`

## Sample Result

All converted providers compile and return rows with `--limit 3` except cases where the source page itself has only one detected row.

| Provider | Rows | Quality Note |
|---|---:|---|
| `ANYANG_LIFELONG_LEARNING` | 3 | Good list extraction, detail not required for sample |
| `YONGIN_LIFELONG_LEARNING` | 1 | Needs dedicated parser for multiple education tabs |
| `BUSAN_RESERVATION` | 1 | Needs dedicated parser for real item splitting |
| `BABSANG_WELFARE_PROGRAM` | 3 | Good detail extraction |
| `SEOUL_PUBLIC_SERVICE` | 1 | Needs dedicated parser or API request for result cards |
| `BUSAN_NATIONAL_SCIENCE_MUSEUM` | 1 | TLS fallback works, needs dedicated parser |
| `DAEGU_NATIONAL_SCIENCE_MUSEUM` | 1 | Dynamic page, needs dedicated parser/API |
| `GWACHEON_NATIONAL_SCIENCE_MUSEUM` | 1 | Angular/template page detected, needs API parser |
| `GWANGJU_NATIONAL_SCIENCE_MUSEUM` | 1 | Basic page extraction works |
| `NATIONAL_OCEAN_SCIENCE_MUSEUM` | 1 | Basic page extraction works |
| `NATIONAL_SCIENCE_MUSEUM` | 3 | Reservation detail links detected |
| `DAEGU_RESERVATION` | 1 | Needs dedicated parser/API |
| `DAEJEON_OK_RESERVATION` | 1 | Needs dedicated parser/API |
| `GWANGJU_RESERVATION` | 1 | Needs dedicated parser for booking list |
| `INCHEON_RESERVATION` | 1 | Needs dedicated parser/API |
| `MUNI_WWW_GN_GO_KR_5623F7DB` | 1 | HTTPS handshake fallback to HTTP works |

## Commands

Run a manual provider sample:

```powershell
python -X utf8 Crawler\generated_yaml\DAEGU_RESERVATION.py --limit 3 --max-pages 1 --detail-limit 3 --timeout 15
```

Run a static provider sample:

```powershell
python -X utf8 Crawler\Crawler_AnyangLearning.py --limit 3 --max-pages 1 --detail-limit 3 --timeout 15
```

Save only after quality review:

```powershell
python -X utf8 Crawler\generated_yaml\NATIONAL_SCIENCE_MUSEUM.py --limit 10 --max-pages 2 --detail-limit 10 --timeout 20 --save-db
```

## Remaining Work

`manual_generic_crawler.py` is an execution fallback, not the final parser quality target. The following need dedicated provider parsers before bulk DB save:

- `YONGIN_LIFELONG_LEARNING`
- `BUSAN_RESERVATION`
- `SEOUL_PUBLIC_SERVICE`
- `DAEGU_RESERVATION`
- `DAEJEON_OK_RESERVATION`
- `INCHEON_RESERVATION`
- `GWACHEON_NATIONAL_SCIENCE_MUSEUM`
- `DAEGU_NATIONAL_SCIENCE_MUSEUM`
