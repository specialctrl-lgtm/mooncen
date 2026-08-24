# YJLIB Crawler

Provider: `MUNI_WWW_YJLIB_GO_KR_ED9BCD30`

URL: `https://www.yjlib.go.kr/web/menu/10071/program/30020/lectureList.do`

## Parser

`Crawler/Crawler_MunicipalYaml.py` uses `collect_yjlib_lecture_list`.

The list page exposes course cards under `.article-item`. Each card links to `lectureDetail.do?lectureIdx=...`. The parser follows every detail page because the detail page contains fields that are not reliable enough on the list card alone:

- `강좌시간`
- `강좌요일`
- `장소`
- `강사명`
- `수강료 및 재료비`
- `준비물`
- `대상`
- `문의전화`
- detail description
- content image

## Branch Handling

The site exposes the library code as a short label such as `여주`. For map display, the crawler normalizes this to real library branch names:

- `여주` -> `여주도서관`
- `세종` -> `세종도서관`
- `점동` -> `점동도서관`
- `대신` -> `대신도서관`
- `산북` -> `산북작은도서관`
- `북내` -> `북내작은도서관`
- `여주기적` -> `여주기적의도서관`
- `흥천` -> `흥천도서관`
- `금사` -> `금사도서관`

If the venue or title contains a full `...도서관` name, that value takes priority over the short-code mapping.

## Current Result

Last verified locally:

```text
collected=360
saved=24
pages=37
detail_pages=360
parser=yjlib_article_cards+detail
```

Active DB rows are saved under `여주도서관`. Google Geocoding resolved the branch to:

```text
대한민국 경기도 여주시 여주읍 여양로 190-17
lat=37.2996997
lon=127.6509998
confidence=80
verified=true
```

Most crawled rows are historical and are skipped by the common expired-course lifecycle rule before DB save.

## Commands

Sample:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_WWW_YJLIB_GO_KR_ED9BCD30.py --per-target-limit 10 --max-pages 2 --detail-limit 10 --timeout 20
```

Full save:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_WWW_YJLIB_GO_KR_ED9BCD30.py --per-target-limit 0 --max-pages 50 --detail-limit 0 --timeout 20 --save-db --mark-stale
```

Branch coordinate backfill:

```powershell
python -X utf8 tools\maintenance\kakao_geocode_branches.py --provider MUNI_WWW_YJLIB_GO_KR_ED9BCD30
```
