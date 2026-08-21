# SYF Lecture Crawler

Provider: `MUNI_YEYAK_SYF_OR_KR_7D3E2EF5`

Source: `https://yeyak.syf.or.kr/www/11`

## Purpose

Collect course/reservation rows from the Suwon Youth and Youth Foundation integrated reservation site.

The original collected URLs, `/www/34` and `/www/37`, are static reception-guide pages. Actual course rows are loaded from the reservation page through FMCS-style JSON endpoints.

## Parser

Parser name: `syf_lecture_api+detail`

Implementation: [Crawler_MunicipalYaml.py](../Crawler/Crawler_MunicipalYaml.py)

The parser:

- Calls `POST /rest/common/company` with `type=L` to get center codes.
- Iterates every returned center code.
- Calls `POST /rest/lecture/list` with `company_code=<center>` and `search_type=R`.
- Reconstructs detail URLs as `/www/11?center=<center>&action=read&comcd=<center>&classcd=<class>&type=R`.
- Follows detail pages for room, monthly period text, description, application period, fee table, image, and instructor.
- Saves each center as a branch using fixed official address mapping.
- Preserves branch names with `preserve_branch=True` so map markers stay at center level.

## Branches

The crawler has address mappings for all current SYF centers:

- `SYF01`: 수원청소년문화센터
- `SYF02`: 권선청소년청년센터
- `SYF03`: 영통청소년청년센터
- `SYF04`: 장안청소년청년센터
- `SYF05`: 광교청소년청년센터
- `SYF06`: 청소년상담복지센터
- `SYF07`: 칠보청소년청년센터
- `SYF08`: 천천청소년청년센터
- `SYF09`: 수원유스호스텔
- `SYF10`: 권선배움마루

Only centers with active API rows are saved as active course branches. Empty centers remain available for future collection when the source site publishes rows.

## Current Result

Last verified: `2026-06-05`

Command:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_YEYAK_SYF_OR_KR_7D3E2EF5.py --per-target-limit 0 --max-pages 3 --detail-limit 100 --timeout 30 --save-db --mark-stale
```

Result:

| Metric | Value |
| --- | ---: |
| Collected | 40 |
| Saved | 40 |
| Active branches | 3 |
| Parser | `syf_lecture_api+detail` |
| Grade | A |
| Core field fill | 100% |
| Important field fill | 95.4% |
| Description fill | 36/40 |
| Target fill | 33/40 |
| Image fill | 29/40 |

Active branch distribution:

| Branch | Courses |
| --- | ---: |
| 수원청소년문화센터 | 32 |
| 칠보청소년청년센터 | 7 |
| 청소년상담복지센터 | 1 |

The 4 missing descriptions and 7 missing target values are source-site omissions, not parser misses.

## Duplicate Target

`MUNI_YEYAK_SYF_OR_KR_9C8EE56C` pointed to the same reservation domain through another static guide page. It is marked as `blocked` with:

```yaml
blocked_reason: duplicate_of:MUNI_YEYAK_SYF_OR_KR_7D3E2EF5
```

This prevents duplicated course rows under two providers.

## Maintenance Commands

Run direct sample:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_YEYAK_SYF_OR_KR_7D3E2EF5.py --per-target-limit 10 --max-pages 3 --detail-limit 10 --timeout 30
```

Run through crawler worker:

```powershell
python -X utf8 run_crawlers.py --providers MUNI_YEYAK_SYF_OR_KR_7D3E2EF5 --once --ignore-active-window
```

Update branch coordinates:

```powershell
python -X utf8 tools\maintenance\kakao_geocode_branches.py --provider MUNI_YEYAK_SYF_OR_KR_7D3E2EF5 --update-all --timeout 20 --delay 0.1 --min-confidence 50
```

Quality report:

```powershell
python -X utf8 tools\report_municipal_crawler_quality.py --report logs\municipal_crawler_reports\<report>.yaml --limit 5
```
