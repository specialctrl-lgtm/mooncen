# Gyeonggi GSEEK Offline Crawler

## Provider

- Provider: `GYEONGGI_GSEEK`
- Source: `https://www.gseek.kr/user/course/offline/list`
- Parser: `gseek_offline_api`
- Domain category: `평생교육`

## Collection Rule

GSEEK has online, video, and offline courses. MoonCen map data should use the offline course list because it exposes regional institutions and venues.

The crawler calls the AJAX endpoint:

```text
POST https://www.gseek.kr/user/course/offline/list/search
```

Required pagination parameters:

| Parameter | Meaning |
| --- | --- |
| `s_sort_by` | Sort order. `1` is recruitment-status order. |
| `s_row_start` | 1-based start row. |
| `s_row_end` | End row used by the GSEEK frontend. |
| `resion` | Region filter. Empty means all regions. |

The frontend loads 9 rows per request, so the crawler uses 9-row pages. The API currently reports more than 5,000 rows, so production collection must run with a high enough `--max-pages` value to cover the desired volume.

## Fields

The API fills these fields without a detail request:

- `title`
- `branch`
- `raw_url` / `application_url`
- `period`
- `schedule_raw`
- `target`
- `fee`
- `material_fee`
- `status`
- `capacity_total`
- `capacity_current`
- `instructor`
- `description`
- `image_url`
- `category`
- `tags`

`branch` is mapped from `d_edu_gvmnfc`, and `venue_address` currently stores the region name from `d_rgn`. Exact venue addresses are not exposed in the list API and should be backfilled by the branch address updater when needed.

## Quality Check

Command:

```bash
python -X utf8 Crawler/generated_yaml/GYEONGGI_GSEEK.py --per-target-limit 0 --max-pages 5 --detail-limit 0 --timeout 30
python -X utf8 tools/report_municipal_crawler_quality.py --report logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_093922.yaml --limit 15
```

Result:

| Rows | Grade | Parser | Core | Important | Period | Schedule | Fee | Status | Target | Description |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 45 | A | `gseek_offline_api` | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
