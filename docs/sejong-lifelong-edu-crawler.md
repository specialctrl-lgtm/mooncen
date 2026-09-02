# SEJONG_LIFELONG_EDU crawler

## Scope

- Provider: `SEJONG_LIFELONG_EDU`
- Site: `https://life.sje.go.kr/edu/community/events/program-list`
- Institution: 세종특별자치시교육청 평생교육원
- Parser: `sejong_lifelong_program_api`

## Implementation

- The public page is JavaScript-rendered, so the crawler uses the site's JSON APIs instead of generic HTML parsing.
- List API: `POST https://life.sje.go.kr/api/homepageprogramlist`
- Detail API: `POST https://life.sje.go.kr/api/homepageprogramdetail`
- Category API: `POST https://life.sje.go.kr/api/getCodeList`
- Required manage code: `150018`
- Status queries:
  - `1and2and6`: 접수중
  - `3`: 마감
  - `5`: 접수대기
- Ended rows are skipped when the program end date is older than the crawler run date.

## Fields

- `title`: `PROGRAM_TITLE`
- `branch`: 세종특별자치시교육청 평생교육원
- `branch_code`: `SEJONG_LIFELONG_EDU_MAIN`
- `address` / `venue_address`: 세종특별자치시 산울3로 124
- `category`: major category description + sub category description
- `period`: `PROGRAM_START_DATE` ~ `PROGRAM_END_DATE`
- `schedule_raw`: `PROGRAM_DAYS` + `PROGRAM_START_TIME`~`PROGRAM_END_TIME`
- `fee`: `PROGRAM_FEE`
- `material_fee`: `MATERIAL_COST`
- `status`: normalized from `PROGRAM_STATUS`
- `target`: explicit target fields when present, otherwise target-like category text or `전체`
- `description`: detail `PROGRAM_DESC`, fallback `PROGRAM_DESC_TEXT`
- `image_url`: `THUMBNAIL_PATH`

## Validation

```powershell
python -X utf8 Crawler\generated_yaml\SEJONG_LIFELONG_EDU.py --per-target-limit 0 --max-pages 5 --detail-limit 100 --timeout 25 --save-db --mark-stale
python -X utf8 tools\report_municipal_crawler_quality.py --report logs\municipal_crawler_reports\municipal_yaml_crawler_20260605_062423.yaml --limit 5
python -X utf8 tools\maintenance\kakao_geocode_branches.py --provider SEJONG_LIFELONG_EDU --timeout 20 --delay 0.1 --min-confidence 50
```

Result:

- Report: `logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_062800.yaml`
- Quality: `A`
- Collected: `87`
- Saved: `87`
- Pages: `3`
- Detail pages: `87`
- Core fields: title, branch, raw_url, period, fee, status, target all `87/87`
- Schedule: `86/87`
- Description: `68/87`
- Active branch: `SEJONG_LIFELONG_EDU_MAIN`
- Active branch coordinates: confidence `100`, `36.5348157, 127.2637839`

Notes:

- Some programs have no description in the API. These remain blank rather than generating synthetic text.
- One program has no schedule time/day in the API. The period is still collected.
