# ICHEON_WORKER_WELFARE Crawler

## Scope

Provider: `ICHEON_WORKER_WELFARE`

Source:

- Configured URL: `https://www.icheon-hrd.or.kr/main/program/rule.jsp`
- Actual list URL: `https://www.icheon-hrd.or.kr/program/programInfoList.do?pgno=1`

The configured page is a course-rule/application-guide page. The crawler follows the site's `수강신청` menu and collects the real paginated course list.

## Parser

Parser name: `icheon_worker_welfare_table+detail`

Collection behavior:

- Parses the list table from `/program/programInfoList.do?pgno=N`.
- Stops at the site-reported last page from `현재페이지 : n/m` to avoid repeated last-page rows.
- Follows each `/program/programInfoDetail.do?prgm_seq=...` detail page.
- Preserves branch as `이천시노동자복지관`; classroom values are stored as `venue_name`.

Collected fields:

- `title`: list/detail title, with leading time-slot labels such as `(오전)` removed
- `branch`: `이천시노동자복지관`
- `address` / `venue_address`: `경기도 이천시 부발읍 무촌로18번길 60-26`
- `category`: time slot plus detail field category
- `status`: list status badges such as `접수마감 / 교육중`
- `period`: detail `교육기간`
- `apply_period`: detail/list application period with two-digit years normalized to `20YY-MM-DD`
- `schedule_raw`: detail `교육일시`
- `fee`: detail `수강료`
- `capacity_total`, `capacity_current`, `waitlist_total`: list/detail capacity values
- `target`: detail description target hints such as `일반모집대상` and `최소연령제한`
- `instructor`: detail `강사명`, with the trailing `상세보기` label removed
- `description`: detail `.board_view .con`
- `material_note` / `material_fee`: material-related lines and extracted KRW amount when present
- `application_url`: detail URL

## Validation

```powershell
python -X utf8 Crawler\generated_yaml\ICHEON_WORKER_WELFARE.py --per-target-limit 0 --max-pages 30 --detail-limit 500 --timeout 25 --save-db --mark-stale
```

Latest local result:

- Report: `logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_043718.yaml`
- Collected: `444`
- Saved active rows: `17`
- Pages: `23`
- Detail pages: `444`
- Core fields: title, branch, address, venue, status, fee, schedule, period, target, description all `444/444`

Most rows on the site are historical courses. The DB writer skips rows whose `end_date` is before the current date, so only current/future courses remain active.

## Location

The active branch is map-ready:

- Name: `이천시노동자복지관`
- Address: `경기도 이천시 부발읍 무촌로18번길 60-26`
- Latitude: `37.2784460`
- Longitude: `127.4779222`

