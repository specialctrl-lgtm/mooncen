# Gyeongsan Lifelong Town Crawler

## Provider

- Provider: `MUNI_WWW_GBGS_GO_KR_4D7732DD`
- Name: Gyeongsan lifelong learning town center programs
- URL: `https://www.gbgs.go.kr/lll/page/2391/1649.tc?mn=2391&pageIndex=1&pageNo=1649&paramIdx=&eduNo=-1&searchInstNo=1&srchCtgryCd=&srchLlPrgrmCd=&srchRgnCd=&srchEduNm=`
- Crawler: `Crawler/generated_yaml/MUNI_WWW_GBGS_GO_KR_4D7732DD.py`

Additional provider:

- Provider: `MUNI_WWW_GBGS_GO_KR_999BABE7`
- Name: Gyeongsan lifelong learning programs
- URL: `https://www.gbgs.go.kr/lll/page/2400/1604.tc?mn=2400&pageIndex=1&pageNo=1604&paramIdx=&eduNo=-1&searchInstNo=2&srchCtgryCd=&srchLlPrgrmCd=&srchRgnCd=&srchEduNm=`
- Crawler: `Crawler/generated_yaml/MUNI_WWW_GBGS_GO_KR_999BABE7.py`

## Structure

- The list page uses repeated `ul.content_list` cards.
- There is no detail page per course; all fields are present in the card.
- Pagination is controlled by `pageIndex`.
- The shared parser supports configurable `mn`, `pageNo`, `searchInstNo`, and list path values for GBGS lifelong learning course pages.
- Page 16 currently contains data and page 17 returns `자료가 없습니다.`.

## Collected Fields

- `title`: first line after branch prefix, for example `[중앙동] 요가`.
- `branch`: branch prefix inside brackets.
- `address`: `경상북도 경산시 <branch>` fallback for map grouping.
- `period`: `수강기간`.
- `schedule_raw`: `수강기간` plus `수강시간`.
- `target`: fallback `경산시민`, because the card does not expose a target field.
- `fee`: `수강료`.
- `material_note`: fee note when it mentions 교재비 or 재료비.
- `status`: `CLOSED` when application period has passed; expired education periods are skipped by default.
- `description`: raw card text.

## Verification

2026-06-08:

```text
python -m py_compile Crawler/generated_yaml/MUNI_WWW_GBGS_GO_KR_4D7732DD.py
python -X utf8 Crawler/generated_yaml/MUNI_WWW_GBGS_GO_KR_4D7732DD.py --limit 10 --max-pages 2 --timeout 35
python -X utf8 Crawler/generated_yaml/MUNI_WWW_GBGS_GO_KR_4D7732DD.py --limit 10 --max-pages 2 --timeout 35 --save-db
```

Result:

- Collected: 10
- Saved: 10
- Quality: A / 100.0

## MUNI_WWW_GBGS_GO_KR_999BABE7 Verification

2026-06-09:

```text
python -m py_compile Crawler/generated_yaml/MUNI_WWW_GBGS_GO_KR_999BABE7.py
python -X utf8 Crawler/generated_yaml/MUNI_WWW_GBGS_GO_KR_999BABE7.py --limit 10 --max-pages 2 --timeout 35
python -X utf8 run_crawlers.py --providers MUNI_WWW_GBGS_GO_KR_999BABE7 --limit 10 --once --ignore-active-window --ignore-worker-lock --skip-coordinate-backfill --skip-category-backfill
```

Result:

- Collected: 10
- Saved: 10
- Quality: A / 100.0
- Required fields were filled for `title`, `branch`, `address`, `period`, `schedule_raw`, `target`, `fee`, `status`, `description`, and `raw_url`.
