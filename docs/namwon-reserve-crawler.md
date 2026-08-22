# Namwon Reserve Crawler

Provider: `MUNI_WWW_NAMWON_GO_KR_37D4EA88`

URL: `https://www.namwon.go.kr/reserve`

## Parser

`Crawler/Crawler_MunicipalYaml.py` uses `collect_namwon_reserve_api`.

The Namwon reservation portal renders the list through the JSON endpoint:

```text
https://www.namwon.go.kr/reserve/integr/rsvt/fclt/item/api/items.do
```

The parser calls the endpoint for the major reservation menus:

- 평생학습관
- 시민참여교육
- 공연강좌
- 체험견학
- 김병종미술관
- 백두대간생태체험교육장
- 교룡공원 숲속야영장
- 공공체육시설
- 지원사업

Rows are mapped into the common generated/public reservation shape:

- `title`
- `branch`
- `category`
- `collection_category`
- `raw_url` / `application_url`
- `application_type`
- `reservation_available`
- `program_type`
- `status`
- `fee`
- `period`
- `apply_period`
- `schedule_raw`
- `target`
- `description`
- `image_url`
- `instructor`
- `capacity_total` / `capacity_current` / `waitlist_total`
- `venue_name`
- `venue_address`
- `raw_fields`

## Branch Handling

Namwon exposes facility data at several levels: facility name, classroom or site name, and free-text place/address text.

The crawler keeps map branches at the actual institution or external venue level:

- `평생학습관`
- `백두대간생태체험교육장`
- `교룡공원 숲속야영장`
- `남원종합스포츠타운`
- `남원시립김병종미술관`
- `혼불문학관`
- external 어디나 venues such as `초록나무협동조합`

Internal rooms and rental sites such as `104호(재봉실)`, `에코롯지`, `데크사이트`, and `파쇄석사이트` are stored in `venue_name`, not promoted to `branches`.

`preserve_branch` is set on Namwon rows so the common DB writer does not promote `venue_name` back into the branch name.

Address hints are stored for known public facilities and parsed from row-level text when present. Google Geocoding is then used to standardize address and fill coordinates.

## Current Result

Last verified locally on 2026-06-05:

```text
collected=1025
saved=136
pages=27
detail_pages=0
parser=namwon_reserve_api
report=logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_025652.yaml
```

Field fill in the crawl report:

```text
title=1025
branch=1025
address=877
venue_name=1025
venue_address=877
raw_url=1025
status=1025
fee=1025
schedule_raw=1012
period=929
target=929
description=931
```

Active DB result after expired-row skip:

```text
active_courses=136
active_branches=15
located_active_branches=15
fee=136/136
schedule_raw=123/136
target=40/136
description=79/136
```

Many missing `target`, `period`, and `description` values are lodging, rental, or support reservation rows where those fields are not exposed as course-style data.

## Commands

Sample:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_WWW_NAMWON_GO_KR_37D4EA88.py --per-target-limit 10 --max-pages 2 --detail-limit 10 --timeout 25
```

Full save:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_WWW_NAMWON_GO_KR_37D4EA88.py --per-target-limit 0 --max-pages 20 --detail-limit 0 --timeout 25 --save-db --mark-stale
```

Coordinate backfill:

```powershell
python -X utf8 tools\maintenance\kakao_geocode_branches.py --provider MUNI_WWW_NAMWON_GO_KR_37D4EA88 --update-all
```

Regenerate generated-provider registry:

```powershell
python -X utf8 Crawler\Crawler_GeneratedYamlTargets.py --write-registry
```
