# Homeplus Crawler Notes

## 2026-07-17 Reception Notice Monitoring

- The crawler reads the public `/CommunityNoAuth/GetNoticeList` feed on every
  successful HOMEPLUS course run and inspects recent reception/member-recruitment notices.
- A notice is applied only to the HOMEPLUS branch code published by the notice
  feed and only to active courses overlapping the notice's explicit course period.
- Notices are applied oldest first, so a newer and narrower opening notice wins
  for overlapping courses without changing other terms or branches.
- Open-ended wording such as `선착순 마감` stores the confirmed start date and
  leaves `apply_end` null. The original wording and notice URL are retained in
  `apply_period_raw`.
- Image-only schedules are not guessed. They are logged with
  `RECEPTION_PERIOD_NOT_IN_TEXT` and are excluded from batch application.
- `HOMEPLUS_NOTICE_LIMIT` defaults to `200` (bounded to `1..500`) and
  `HOMEPLUS_NOTICE_LOOKBACK_DAYS` defaults to `210` (bounded to `30..730`).

## 2026-06-13 Detail Title Extraction

Issue:

- `https://mschool.homeplus.co.kr/Lecture/Detail?LectureMasterID=9976388` has the detail title `We do 2.0 코딩` in `.newCon_tit`.
- The Homeplus crawler only used list-page `.title_1` / `.title_2` values, so list metadata such as `2018~20년생` could leak into `title_raw`.
- If a list item was incomplete, the UI could fall back to `강좌명 미정` even though the detail page had a valid title.
- `title_cleaner.py` also treated `2.0` as a date-like token because the date pattern allowed day `0`.

Fix:

- `Crawler/Crawler_Homeplus.py` now extracts the detail title from `.newCon_tit` and falls back to lecture image `alt` text.
- Detail title values overwrite list title values during the existing `course_data.update(detail_info)` merge.
- `title_cleaner.py` now limits date tokens to valid month/day ranges, so `We do 2.0 코딩` preserves `2.0`.
- Standalone `년생` / `개월` target fragments are removed from display titles after target extraction.
- Leading date and time-range fragments such as `8.31 (월) 13:30 ~ 15:30` and `2026.06.03 (수)` are removed as schedule fragments.
- Frontend display title fallback now uses `title` then `title_raw`; `title_prefix_removed` is metadata about removed fragments and is not a display title.

Validation:

- Detail parser returns `title = We do 2.0 코딩` for `LectureMasterID=9976388`.
- `clean_course_title("We do 2.0 코딩")` returns `We do 2.0 코딩`.
- `clean_course_title("We do 2.0 코딩 2018~20년생")` returns `We do 2.0 코딩`.
- `clean_course_title("8.31 (월) 13:30 ~ 15:30 그리너리 꽃다발")` returns `그리너리 꽃다발`.
- `clean_course_title("2026.06.03 (수) 키즈 쿠킹 클래스")` returns `키즈 쿠킹 클래스`.
