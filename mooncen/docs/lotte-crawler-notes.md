# Lotte Crawler Notes

## 2026-07-17 Reception Notice Priority

- Every successful real-branch LOTTE crawl monitors recent recruitment notices
  from `/community/notice/list.ajax` and their public detail pages.
- The general-public reception date is the `신규회원` date. Existing-member
  dates and exact times remain visible in the source notice; the database's
  reception fields retain day precision.
- A branch recruitment notice has priority for that branch and term. If there
  is no branch notice, the newest main-office notice supplies the schedule for
  the branch's published region group (`수도권` or `서울·지방`).
- A branch notice that exists but cannot be parsed blocks main-office fallback.
  This prevents an incorrect generic date from replacing a branch exception.
- Updates are limited to active courses in the matching branch whose course
  dates overlap the notice's course period.
- `LOTTE_NOTICE_LIMIT` defaults to `200` (bounded to `1..500`) and
  `LOTTE_NOTICE_LOOKBACK_DAYS` defaults to `210` (bounded to `30..730`).
