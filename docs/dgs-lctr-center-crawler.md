# DGS Reservation Crawler

Provider: `MUNI_WWW_DGS_GO_KR_566C09FF`

Sources:

- `https://www.dgs.go.kr/reserve/edu/list.do?mid=0101020000&token=1780585043800`
- `https://www.dgs.go.kr/reserve/lctrCntr/list.do?mid=0102000000&token=1780585067625`
- `https://www.dgs.go.kr/reserve/lifelong/list.do?mid=0106000000&token=1780585074227`

## Scope

This crawler collects Daegu Seo-gu public reservation courses from information education, resident self-governing center, and lifelong-learning center lists.

The pages expose card lists under `.edu-application .item`:

- Information education: direct `GET /reserve/edu/view.do?mid=0101020000&idx=<course id>`
- Resident center: `POST /reserve/lctrCntr/view.do?mid=0102000000` with `idx=<course id>`
- Lifelong learning: direct `GET /reserve/lifelong/view.do?mid=0106000000&idx=<course id>`

## Parser

Parser name: `dgs_reserve_three_lists+detail`

Implementation: `collect_dgs_lctr_center_courses()` in `Crawler/Crawler_MunicipalYaml.py`.

The parser:

- Crawls all visible list pages for the three approved URLs.
- Reads course ids from `a[data-req-get-p-idx]` or `href` query `idx`.
- Parses `table.edu_info` on each detail page.
- Splits branches by dong and saves them as `내당1동 주민자치센터`, `평리3동 주민자치센터`, and similar branch names.
- Saves information education under `대구광역시 서구청 정보화교육장`.
- Saves lifelong learning under `대구광역시 서구 평생학습센터`.
- Backfills branch addresses and phone numbers from the official dong direction pages.
- Normalizes Korean fee units such as `1만원` and `5천원`.
- Preserves original card/detail fields under `raw_fields`.

## Fallbacks

Two rows on the current site have malformed schedule tables:

- When a time value appears under `운영기간`, it is moved to `schedule_raw` and `period` is set to `상시`.
- When the schedule is not visible, `schedule_raw` is set to `운영시간 미표기`.

## Quality

Last verified report:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_WWW_DGS_GO_KR_566C09FF.py --per-target-limit 0 --max-pages 5 --detail-limit 120 --timeout 30
python -X utf8 tools\report_municipal_crawler_quality.py --report logs\municipal_crawler_reports\municipal_yaml_crawler_20260605_111224.yaml --limit 10
```

Result:

| Field | Count |
| --- | ---: |
| rows | 169 |
| pages | 14 |
| detail pages | 120 |
| title | 169 |
| period | 169 |
| schedule_raw | 169 |
| fee | 75 |
| status | 169 |
| target | 169 |
| description | 169 |

Quality grade: `A`, core `100.0%`, important `90.7%`. Fee is lower because some information education rows do not expose a fee field.

## Run

Sample without saving:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_WWW_DGS_GO_KR_566C09FF.py --per-target-limit 10 --max-pages 4 --detail-limit 10
```

Production save:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_WWW_DGS_GO_KR_566C09FF.py --save-db --per-target-limit 0 --max-pages 4 --detail-limit 100
```
