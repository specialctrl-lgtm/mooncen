# Dobong Integrated Education Crawler

Canonical provider: `MUNI_YEYAK_DOBONG_GO_KR_C2700A4B`

Official source:
`https://yeyak.dobong.go.kr/recruit/Education.asp?Gnb=GnbTp1&MCode=UMA1001`

## Ownership

The integrated reservation education list is the canonical owner of current and
future Dobong education programs. It includes Dobong-gu office, the Dobong
education portal, and resident community-center programs.

The following narrower providers are disabled as duplicates of the canonical
owner:

- `MUNI_EDU_DOBONG_GO_KR_5ADA6E67`
- `MUNI_EDU_DOBONG_GO_KR_779522F2`
- `MUNI_EDU_DOBONG_GO_KR_905EFB5D`

`MUNI_EDU_DOBONG_GO_KR_AD2DB976` remains deprecated. The separate facilities
provider `MUNI_YEYAK_DOBONGSISEOL_OR_KR_0A2D506D` stays enabled because it owns
the facilities-management lecture catalog, not the integrated portal scope.

## Collection Contract

The `dobong_integrated_current_education+detail` parser:

- requests `PageSize=100` and verifies every declared history page and record ID;
- keeps rows whose education end date has not passed in Korea time;
- enriches native `EducationDesc.asp` records and linked official receipt pages;
- caches shared linked details while preserving each integrated record identity;
- splits branches into `도봉구청`, `도봉구 교육포털`, or `{동} 자치회관`;
- locks the result to `교육·강좌` / `공공강좌` and municipality `1132000000`;
- blocks a complete snapshot when pagination, detail enrichment, or configured
  caps are incomplete, so an incomplete run cannot mark missing rows stale.

## Live Dry-run

Validated without database writes on 2026-07-19:

```powershell
python -X utf8 Crawler/Crawler_GeneratedYamlTargets.py `
  --provider MUNI_YEYAK_DOBONG_GO_KR_C2700A4B `
  --dry-run --per-target-limit 0 --max-pages 30 --detail-limit 200 --timeout 40
```

| Metric | Result |
| --- | ---: |
| Historic list rows verified | 2,360 |
| List pages | 24 |
| Current/future rows | 160 |
| Native integrated details | 90 |
| Linked official records | 70 |
| Unique details fetched | 98 |
| Saved | 0 |

The production scheduler runs this provider through the bounded municipal
integrated-reservation aggregate. After the canonical provider has been saved
and its active count checked, old active rows from the three duplicate providers
can be soft-staled with the duplicate-provider deactivation tool. This validation
did not write to the production database or deploy services.
