# Age Target Parser Fix Notes

## 2026-05-28

Investigated the course title:

`잉글리쉬 토피아 1:1 화상영어 (2019년~성인) 1차 (6월, 1회25분, 주3회)`

Problem:

- Existing production rows had values such as `target_age_group=CHILD`, `target_min_age=8`, `target_max_age=13`.
- The `target_min_age` and `target_max_age` columns are used as month values in the frontend.
- The bad `8~13` values came from an age-group default that represents years, not months.
- `target_cleaner.extract_target_text()` did not recognize `2019년~성인`, so some rows kept only `성인` as the target or left the year fragment in the title.

Fix:

- `target_cleaner.py` now extracts:
  - `2019년~성인`
  - `2019~성인`
  - `2019년생~성인`
  - `성인~2019년생`
  - `8세~성인`
- `data_parser.py` and `ai_processor.py` now convert those explicit ranges to month bounds:
  - `2019년~성인` in 2026 -> `min_age=84`, `max_age=null`
  - `8세~성인` -> `min_age=96`, `max_age=null`

Validation:

- `extract_target_text()` returns `2019년~성인`.
- `parse_crawler_target()` returns `age_is_explicit=True`, `target_min_age=84`, `target_max_age=None`.
- `AIProcessor.analyze_title()` keeps title as `잉글리쉬 토피아 1:1 화상영어` and target as `2019년~성인`.

Existing production rows still need deployment and reprocessing/backfill to replace old `8~13` values.

## 2026-05-29 Homeplus Month Backfill

Homeplus rows had historical `target_min_age` and `target_max_age` values stored as years for explicit month targets.

Examples:

- `12~17개월` was stored as `1~2`.
- `2014~2020년생` was stored as `6~12`.
- Adult rows often carried default `20~59`, which the frontend reads as months.

Fix:

- `Crawler/Crawler_Homeplus.py` now parses target age from title/target/category only, not description, because description often contains unrelated dates or age-like text.
- Adult rows keep `target_age_group=ADULT` but clear min/max month fields.
- Added `tools/maintenance/fix_homeplus_age_months.py` to recalculate existing Homeplus target fields from title, target, and category text.
- The maintenance script works with DBs that do or do not have `target_age_is_explicit`.

Local backfill result:

- Scanned `6,197` Homeplus rows.
- Updated `6,193` rows.
- Verified samples now store explicit month ranges as months, for example `10~16개월 -> 10~16`, `25~36개월 -> 25~36`, and `2020~2022년생 -> 48~72`.
