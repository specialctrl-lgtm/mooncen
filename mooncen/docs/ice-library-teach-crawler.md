# ICE Library Teach Crawler

Provider: `MUNI_LIB_ICE_GO_KR_019A3D01`

Source URL: `https://lib.ice.go.kr/seogu/module/teach/index.do?menu_idx=85`

## Structure

The Incheon education library sites expose course cards under `module/teach/index.do`.

- Course rows are rendered as `.item`.
- The list card contains title, category, application status, application period, lecture period, target, and capacity.
- The detail button contains `group_idx`, `category_idx`, and `teach_idx`.
- Full detail fields are available from `module/teach/detail.do`.

## Parser

`collect_ice_library_teach_detail` collects the list rows and requests each detail page.

Collected fields:

- `title`
- `branch`, `branch_code`, `address`, `venue_address`
- `category`, `period`, `schedule_raw`, `apply_period`
- `target`, `capacity`, `instructor`, `venue_name`, `room`
- `status`, `raw_url`, `application_url`, `description`

The source page does not expose a fee field, so `fee` remains empty.

## Test Result

Command:

```bash
python -X utf8 Crawler/generated_yaml/MUNI_LIB_ICE_GO_KR_019A3D01.py --save-db --per-target-limit 0 --max-pages 3 --detail-limit 0 --timeout 30 --mark-stale
```

Result on 2026-06-05:

| Metric | Value |
| --- | --- |
| Parser | `ice_library_teach_detail` |
| Rows | 20 |
| Saved | 16 |
| Detail pages | 20 |
| title / branch / address / period / schedule / description | 20 / 20 / 20 / 20 / 20 / 20 |
| target | 18 |
| fee | 0 |

