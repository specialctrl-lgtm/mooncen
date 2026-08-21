# GWANGJU_RESERVATION Crawler

## Scope

- Provider: `GWANGJU_RESERVATION`
- Site: `https://www.gwangju.go.kr/reserve/main.do`
- Data source: Gwangju reservation JSON endpoint under `/reserve/getBookingList.do`
- Parser: `gwangju_booking_api`

## Implementation

- `Crawler/Crawler_MunicipalYaml.py` redirects the target main URL to:
  - `https://www.gwangju.go.kr/reserve/bookingList.do?pageId=reserve1&searchCate1=A`
- The parser posts paginated requests to `/reserve/getBookingList.do`.
- Current API response includes `totalCnt` and `pageCnt`; the crawler stops when `page >= pageCnt`.
- Rows are saved with:
  - `title`
  - `branch`
  - `venue_name`
  - `venue_address`
  - `period`
  - `schedule_raw`
  - `target`
  - `fee`
  - `status`
  - `description`
  - `application_url`
  - capacity fields

## Branch And Address Rules

- `eduAddress` is treated as the venue source.
- Known institution names are normalized so branches are map-friendly:
  - `광주역사민속박물관`
  - `광주광역시농업기술센터`
  - `농식품가공창업보육센터`
  - `광주광역시청`
  - `유덕동 도시텃밭정원`
- `신청한 장소`, `회차별상이`, and broad `광주` values are not usable as map locations.
  - For city-operated departments such as `안전정책관` and `관리운영과`, the branch is normalized to `광주광역시청`.
- The explicit test item `부도테스트` is skipped.

## Address Confidence

- Addresses found directly in the Gwangju API are used as crawler addresses.
- Known fallback addresses are applied for recurring active branches:
  - `광주역사민속박물관`: `광주광역시 북구 서하로 48-25`
  - `광주광역시청`: `광주광역시 서구 내방로 111`
  - `광주광역시농업기술센터`: `광주광역시 광산구 평동로 639-22`
  - `농식품가공창업보육센터`: `광주광역시 광산구 평동로 639-22`
  - `유덕동 도시텃밭정원`: `광주광역시 서구 유촌동 820-8`
- `유덕동 도시텃밭정원` is a location hint, not a road-address value from the reservation API.

## Validation

Latest run:

```powershell
python -X utf8 Crawler\generated_yaml\GWANGJU_RESERVATION.py --per-target-limit 0 --max-pages 60 --detail-limit 0 --timeout 25 --save-db --mark-stale
```

Result:

- Collected: `649`
- Saved current/future rows: `11`
- Pages: `55`
- Parser: `gwangju_booking_api`
- Quality: `A`
- Core fields: `100.0%`
- Important fields: `89.5%`
- Report:
  - `logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_031727.yaml`
  - `logs/municipal_crawler_quality/municipal_yaml_crawler_20260605_031727_quality.md`

Active branch coordinate status after Google Geocoding:

| Branch | Active Rows | Coordinate Confidence |
| --- | ---: | ---: |
| 광주광역시청 | 4 | 100 |
| 광주역사민속박물관 | 3 | 100 |
| 광주광역시농업기술센터 | 2 | 100 |
| 농식품가공창업보육센터 | 1 | 100 |
| 유덕동 도시텃밭정원 | 1 | 100 |

## Notes

- The API contains many past reservations. DB saving skips expired rows before branch creation.
- Some rows do not expose `target`; target quality is `243/649`.
- The frontend map should use active branches with active courses. Inactive low-confidence branch coordinates were cleared after validation.
