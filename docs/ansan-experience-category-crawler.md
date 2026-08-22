# Ansan Experience Category Crawler

## Provider

- Provider: `MUNI_RESERVE_ANSAN_GO_KR_02253999`
- Source: `https://reserve.ansan.go.kr/exp/X01/expList.do?currentMenuNo=667`
- Parser: `ansan_experience_category_cards`
- Domain category: `체험·견학`

## Collection Rule

The crawler iterates the three visible Ansan integrated-reservation experience categories:

| Code | Category | currentMenuNo |
| --- | --- | --- |
| `X01` | 실내체험 | `667` |
| `X02` | 실외체험 | `668` |
| `X03` | 견학 | `669` |

Each category page uses the same `ul.blog.reserv > li` card structure. Detail URLs are reconstructed from the `fnView('RESR_...')` JavaScript reservation id.

## Fields

The list cards provide:

- `title`
- `branch`
- `raw_url` / `application_url`
- `period`
- `apply_period`
- `schedule_raw`
- `target`
- `fee`
- `status`
- `room` / `venue_name`
- `description`
- `category` / `category_raw`

The parser stores `program_type` as `체험` for `실내체험` and `실외체험`, and `견학` for the `견학` category.

## Quality Check

Command:

```bash
python -X utf8 Crawler/generated_yaml/MUNI_RESERVE_ANSAN_GO_KR_02253999.py --per-target-limit 0 --max-pages 1 --detail-limit 0 --timeout 30
python -X utf8 tools/report_municipal_crawler_quality.py --report logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_093148.yaml --limit 15
```

Result:

| Rows | Grade | Parser | Core | Important | Period | Schedule | Fee | Status | Target | Description |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 30 | A | `ansan_experience_category_cards` | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
