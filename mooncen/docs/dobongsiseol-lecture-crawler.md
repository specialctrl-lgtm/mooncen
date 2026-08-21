# Dobong Facilities Lecture Crawler

Provider: `MUNI_YEYAK_DOBONGSISEOL_OR_KR_0A2D506D`

Source URL: `https://yeyak.dobongsiseol.or.kr/lecture/guide.php?c_id=12&page_info=guide&n_type=lecture`

## Structure

The Dobong Facilities reservation site starts on `guide.php`, but course data is under each facility's `수강신청` link.

- `guide.php` exposes one `수강신청` link per facility.
- Each facility course list uses `/lecture/index.php?c_id=...&page_info=index&n_type=lecture`.
- List rows contain `goLink(...)` parameters.
- Detail fields are loaded by POSTing those parameters to `/lecture/lecture_iview.php`.

## Parser

`collect_dobongsiseol_lecture` now extracts all facility `수강신청` links from `guide.php`, then cycles through facility/category/page links.

Collected fields:

- `title`
- `branch`, `branch_code`
- `period`, `apply_period`, `schedule_raw`
- `target`, `capacity`, `instructor`
- `fee`, `room`, `venue_name`
- `status`, `description`, `material_note`
- `raw_url`, `application_url`

Address is not exposed in the course list or detail response and remains a branch address backfill target.

## Test Result

Command:

```bash
python -X utf8 Crawler/generated_yaml/MUNI_YEYAK_DOBONGSISEOL_OR_KR_0A2D506D.py --save-db --per-target-limit 0 --max-pages 120 --detail-limit 1000 --timeout 30 --mark-stale
```

Result on 2026-06-05:

| Metric | Value |
| --- | --- |
| Parser | `dobongsiseol_lecture_table` |
| Rows | 445 |
| Saved | 445 |
| Pages | 78 |
| Detail pages | 707 |
| title / schedule / fee | 445 / 445 / 445 |
| period / description | 444 / 444 |

