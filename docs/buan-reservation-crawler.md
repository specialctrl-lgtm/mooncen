# Buan Reservation Crawler

Provider: `MUNI_WWW_BUAN_GO_KR_B5BDBAE0`

## Scope

The Buan integrated reservation target originally pointed at a facility guide page. The dedicated crawler now collects the course-oriented reservation pages:

- `평생학습`
- `옹기종기문화센터`
- `예술회관`
- `미디어센터`

## Parser

Parser name: `buan_reserve_card+detail`

The parser:

- discovers branch/place tabs from `.basic_tab2`
- iterates branch URLs instead of relying only on the `전체` page
- paginates with `pageIndex` and `startPage`
- parses `.ed_list` cards for title, venue, target, application period, education period, schedule, phone, capacity, fee, and status
- follows `상세보기` links to `.bbs_view` detail pages
- enriches rows with description, instructor, image URL, material note/fee, attachments, and application URL when visible
- treats `모두배움터` as a grouping tab and promotes the visible venue to branch for map display
- keeps `preserve_branch=True` so the branch chosen by the parser is not overwritten by generic branch promotion

Known branch address mapping is intentionally conservative:

- `부안예술회관`: `전북특별자치도 부안군 부안읍 예술회관길 11`
- `달콩시루공방`: `전북특별자치도 부안군 부안읍 서외리 79-5`
- `삼남중학교`: `전북특별자치도 부안군 부안읍 용계길 29`
- `선돌로 106`: `전북특별자치도 부안군 부안읍 선돌로 106`
- broad `부안군` fallback: `전북특별자치도 부안군 부안읍 당산로 91`

Other venues are left with their official venue names and can be geocoded by the existing address-fix operation.

## Commands

Sample:

```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_BUAN_GO_KR_B5BDBAE0.py --per-target-limit 10 --max-pages 2 --detail-limit 10 --timeout 30
```

Full collection:

```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_BUAN_GO_KR_B5BDBAE0.py --per-target-limit 0 --max-pages 10 --detail-limit 200 --timeout 30 --save-db --mark-stale
```

Quality report:

```bash
python -X utf8 tools/report_municipal_crawler_quality.py --report logs/municipal_crawler_reports/municipal_yaml_crawler_YYYYMMDD_HHMMSS.yaml --limit 8
```

## Latest Local Verification

Date: `2026-06-05`

- collected: `72`
- saved: `23`
- parser: `buan_reserve_card+detail`
- quality: `A`
- core fill: `100.0%`
- important fill: `98.6%`
- period: `72/72`
- schedule: `70/72`
- fee: `72/72`
- target: `70/72`
- description: `70/72`

Rows whose education period has already ended are skipped by the shared saver before branch/course insert.
