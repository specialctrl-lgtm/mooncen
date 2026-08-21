# Title Cleaning Notes

Updated: 2026-06-05

## Fixed Pattern

Some course titles included malformed schedule fragments such as:

```text
금< ~ ,4회>1:1기구필라테스 16:00~
```

The display title should be:

```text
1:1기구필라테스
```

## Code Changes

`title_cleaner.py` now removes:

- malformed weekday/session prefixes:

```text
금< ~ ,4회>
```

- trailing incomplete time fragments:

```text
16:00~
```

- trailing full time ranges:

```text
16:00~17:00
```

The cleaner keeps legitimate `1:1` course names intact.

## Backfill

Added:

```text
DB/backfill_malformed_schedule_titles.py
```

This script only updates rows whose current title contains malformed schedule prefix/suffix patterns.

Executed on development DB:

```text
rows scanned: 27,358
titles changed: 295
```

## Remaining Issue

Some remaining rows have title values that are only schedule text, for example:

```text
수 16:00~
```

Those are not title-cleaning cases. They indicate crawler field mapping problems where the real course title was saved into `schedule_raw` or another field. Those providers need crawler-specific fixes.
