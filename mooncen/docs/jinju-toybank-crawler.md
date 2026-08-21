# Jinju Toybank Reservation Crawler

Provider: `MUNI_WWW_JINJU_GO_KR_CC4D7F07`

## Scope

- Site: `https://www.jinju.go.kr/yeyak/08870/09630/09653.web?gubunCd=FAC_005&cpage=1`
- Collection category: `공공예약`
- Parser: `jinju_toybank_branch_list`

The configured URL is the top-level `장난감은행 놀이체험교실 > 전체강좌` page. That page contains duplicated content across all branches, so the crawler does not collect it directly. It uses the left menu to discover each branch and then collects each branch's own `전체강좌` page.

## Branch Collection

Collected branch pages:

- `무지개동산 장난감은행`: `/yeyak/08870/09630/09690.web`
- `은하수동산 장난감은행`: `/yeyak/08870/09630/09702.web`
- `충무공동 장난감은행`: `/yeyak/08870/09630/09714.web`
- `천전동 장난감은행`: `/yeyak/08870/09630/09730.web`

The parser fills:

- `title`
- `branch`, `branch_code`, `branch_url`
- `category`
- `period`
- `schedule_raw`
- `target`
- `fee`
- `status`
- `capacity_total`, `capacity_current`, `waitlist_total`
- `application_method_raw`
- `description`
- `image_url`

## Validation

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_WWW_JINJU_GO_KR_CC4D7F07.py --per-target-limit 0 --max-pages 20 --detail-limit 0 --timeout 25 --save-db --mark-stale
```

Result on 2026-06-05:

- Report: `logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_045434.yaml`
- Collected: `119`
- Saved active/non-expired rows: `25`
- Pages fetched: `15`
- Branches discovered: `4`
- Duplicate provider course IDs: `0`
- Duplicate raw URLs: `0`
- Core fields filled: title, branch, period, schedule, target, fee, status, description `119/119`
- Image URLs filled in active DB rows: `24/25`
- Existing single-branch rows under `경상남도 진주시`: `50` inactive after `--mark-stale`

Branch distribution from the full scrape:

| Branch | Scraped Rows |
| --- | ---: |
| 무지개동산 장난감은행 | 25 |
| 은하수동산 장난감은행 | 45 |
| 충무공동 장난감은행 | 25 |
| 천전동 장난감은행 | 24 |

Active DB distribution after expired-course filtering:

| Branch | Active Rows |
| --- | ---: |
| 무지개동산 장난감은행 | 5 |
| 은하수동산 장난감은행 | 10 |
| 충무공동 장난감은행 | 5 |
| 천전동 장난감은행 | 5 |

## Notes

- The Jinju page exposes branch names but not branch-specific addresses. The site footer repeats Jinju City Hall's address, so the crawler intentionally does not save that footer address as a branch address.
- Google Geocoding with branch names alone returns low-confidence or duplicated locations for several branches. Treat Jinju toybank branch locations as manual address-fix targets until branch-specific addresses are verified.
