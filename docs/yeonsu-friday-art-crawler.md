# Yeonsu Friday Art Crawler

## Scope

- Provider: `MUNI_WWW_YEONSU_GO_KR_B2B6DF58`
- Source: `https://www.yeonsu.go.kr/culture/show/friday_art/reservation.asp`
- Domain category: `예술/공연`
- Collection type: static HTML cards plus detail pages

The generic generated crawler previously collected menu/navigation rows from the Yeonsu Culture Portal. The dedicated parser now limits extraction to `#contents .reservation_list` performance cards and follows each `상세보기` link.

## Parser

- List selector: `#contents .reservation_list > ul > li`
- Detail selector: `#contents .reservation_view`
- Parser name: `yeonsu_friday_art_cards+detail`

Field mapping:

| Field | Source |
| --- | --- |
| `title` | Card/detail `.tit` |
| `branch`, `venue_name` | Fixed to `연수아트홀` |
| `period`, `schedule_raw`, `schedule_dates` | `공연일시` |
| `apply_period` | `예약기간` |
| `status`, `capacity_remaining` | `남은좌석` |
| `instructor` | Detail `공연단체` |
| `fee` | Fixed to `무료` because the page does not expose paid pricing |
| `target` | Fixed to `전체` |
| `description` | Detail `공연소개` and `공연관련 유의사항` |
| `image_url` | Poster image |
| `application_url` | `예매하기` link when available |

## Validation

Sample command:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_WWW_YEONSU_GO_KR_B2B6DF58.py --per-target-limit 10 --max-pages 2 --detail-limit 10 --timeout 30
```

Quality command:

```powershell
python -X utf8 tools\report_municipal_crawler_quality.py --report logs\municipal_crawler_reports\municipal_yaml_crawler_20260605_103458.yaml --limit 15
```

Result:

| Provider | Rows | Parser | Grade | Core | Important |
| --- | ---: | --- | --- | ---: | ---: |
| `MUNI_WWW_YEONSU_GO_KR_B2B6DF58` | 10 | `yeonsu_friday_art_cards+detail` | A | 100.0% | 100.0% |

Report files:

- `logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_103458.yaml`
- `logs/municipal_crawler_quality/municipal_yaml_crawler_20260605_103458_quality.md`

## Notes

The source page is an archive and includes past performances. DB writes still use the common course lifecycle rule, so rows with an end date before today are skipped at save time.
