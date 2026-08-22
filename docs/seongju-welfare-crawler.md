# Seongju Welfare Platform Crawler

## Scope

- Provider: `SEONGJU_WELFARE_PLATFORM`
- Source site: `https://sj-welfare.or.kr`
- Active YAML: `config/crawl_targets/lifelong_learning.yaml`
- Wrapper: `Crawler/generated_yaml/SEONGJU_WELFARE_PLATFORM.py`

## Crawled Lists

- `https://sj-welfare.or.kr/cnts/community/educationApplication.html`
- `https://sj-welfare.or.kr/cnts/community/youthCulturalCenter.html`

The old Seongju county candidate URL in collected YAML points to the county job board, so it is not used for course crawling.

## Parser

`Crawler/Crawler_MunicipalYaml.py` contains the dedicated `collect_seongju_welfare_platform()` parser.

It reads the list table, then opens each detail URL to fill:

- `title`
- `period`
- `schedule_raw`
- `target`
- `fee`
- `material_fee`
- `status`
- `description`
- `venue_name`
- `venue_address`

Known venue address mappings:

- `창의문화센터`: `경북 성주군 성주읍 경산길 17`
- `문화예술회관`: `경북 성주군 성주읍 성주로 3204`
- `성주군청소년문화의집`: `경북 성주군 성주읍 성주로 3200`

## Latest Local Test

Command:

```bash
python -X utf8 Crawler/Crawler_GeneratedYamlTargets.py --provider SEONGJU_WELFARE_PLATFORM --per-target-limit 30 --max-pages 5 --detail-limit 30 --timeout 20 --save-db
```

Result:

- Collected: 18
- Saved: 9
- Parser: `seongju_welfare_table+detail`
- Filled fields: title 18, period 18, schedule 18, fee 18, description 18

Nine rows were skipped because their education end dates were before the current date.

The municipal DB writer now skips expired rows before branch creation, preventing expired-only rows from leaving zero-course branch markers on the map.
