# GNE go.kr Crawler Group

## Scope
- `gne.go.kr` 계열 URL 중 도서관 평생학습 강좌 구조를 묶어서 처리한다.
- 현재 강좌 수집 가능 provider:
  - `MUNI_TYLIB_GNE_GO_KR_7D159AC1`: 경상남도교육청 통영도서관
  - `MUNI_CNLIB_GNE_GO_KR_A3514402`: 경상남도교육청 창녕도서관

## Parser
- Implementation: `Crawler/Crawler_MunicipalYaml.py`
- Parser: `gne_library_lec_list`
- Entry URL: `menu.es?...`
- Actual list URL pattern: `/usr_gne/lec_list.es`
- Detail URL pattern: `/usr_gne/lec_v.es`

## Current Result
- Run time: `2026-06-05T01:08:04+09:00`
- Command:

```bash
python -X utf8 Crawler/Crawler_GeneratedYamlTargets.py --provider MUNI_TYLIB_GNE_GO_KR_7D159AC1 --provider MUNI_CNLIB_GNE_GO_KR_A3514402 --include-status needs_parser --per-target-limit 0 --max-pages 20 --detail-limit 100 --timeout 20 --save-db --mark-stale
```

| provider | collected | saved active | parser |
| --- | ---: | ---: | --- |
| `MUNI_CNLIB_GNE_GO_KR_A3514402` | 4 | 4 | `gne_library_lec_list` |
| `MUNI_TYLIB_GNE_GO_KR_7D159AC1` | 17 | 7 | `gne_library_lec_list` |

## Non-course GNE URLs
- `MUNI_HCEDU_GNE_GO_KR_5A363840`: current URL is an education-office notice board.
- `MUNI_HYEDU_GNE_GO_KR_DA4FC571`: current URL is a civil complaint/report content page.
- Both are marked `needs_discovery` and disabled in the generated registry.
- Old false-positive DB rows from those two providers were marked inactive.

## Notes
- `gne.go.kr` is not automatically a course source. The reusable course structure is the library lecture pattern: `menu.es` leading to `/usr_gne/lec_list.es`.
- Notice-board URLs under `/na/ntt/` and static content URLs under `/cm/cntnts/` are routed to `no_structured_courses` to prevent generic parser false positives.
