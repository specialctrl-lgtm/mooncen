# National Intangible Heritage Center Crawler

Provider: `NATIONAL_INTANGIBLE_HERITAGE_CENTER`

## Source

- Homepage: `https://www.nihc.go.kr`
- Professional education list: `https://www.nihc.go.kr/planweb/board/list.9is?boardUid=ff8080816ecaefcf016ecfc31e210243&contentUid=ff8080816eee4ad5016eee8e465400c6`
- Social education list: `https://www.nihc.go.kr/planweb/board/list.9is?boardUid=ff8080816ecaefcf016ecfc31e210243&contentUid=ff8080816eee4ad5016ef8eb5793085b`

## Parser

Parser name: `nihc_type_thumb_detail`

The site uses the 9is board module. The list page exposes course cards under `ul.type-thumb > li`:

- `dt`: title
- first normal `dd`: period and bracketed schedule text
- `dd.btn_rev`: status and detail link
- `.thumbBox1 img`: thumbnail image

The detail page exposes structured fields under `.infoBox li` and the long body under `.viewList .view-con`.

Mapped fields:

- `title`: detail `제목`, falling back to card `dt`
- `period`: detail `교육기간` when available, otherwise detail/list `일정`
- `schedule_raw`: weekday tail from `교육기간` plus `교육시간`
- `target`: `교육대상` or body `모집대상`
- `fee`: `무료` when tuition is free; material cost is separated
- `material_fee`: extracted from `재료비` text
- `material_note`: body lines containing material/preparation/kit hints
- `status`: list card status
- `description`: detail body text
- `image_url`: thumbnail/detail image URL
- `venue_name`, `room`: detail `교육장소`
- `venue_address`, `address`: `전북특별자치도 전주시 완산구 서학로 95(동서학동 896-1)`

Expired education-period rows are skipped before returning rows.

## Robots Policy

`https://www.nihc.go.kr/robots.txt` currently returns:

```text
User-agent : *
Disallow : /
```

For that reason the YAML target is kept as `crawler_status: blocked` with `robots_policy: disallow_all`. The parser exists for manual review and future recheck, but daily automated collection should not run this provider unless policy changes or explicit permission is obtained.

## Manual Verification

Include blocked status when running this provider manually:

```powershell
python -X utf8 Crawler/generated_yaml/NATIONAL_INTANGIBLE_HERITAGE_CENTER.py --include-status blocked --per-target-limit 10 --max-pages 2 --detail-limit 10 --timeout 30
```

Quality check:

```powershell
python -X utf8 tools/report_municipal_crawler_quality.py --report logs/municipal_crawler_reports/<report>.yaml --limit 10
```

Latest local verification on 2026-06-05:

| provider | rows | parser | grade | period | schedule | fee | status | target | description |
| --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `NATIONAL_INTANGIBLE_HERITAGE_CENTER` | 3 | `nihc_type_thumb_detail` | A | 100.0% | 100.0% | 66.7% | 100.0% | 100.0% | 100.0% |

