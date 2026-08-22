# Ansan Education Category Crawler

## Provider

- Provider: `MUNI_RESERVE_ANSAN_GO_KR_8236CAF0`
- Source: `https://reserve.ansan.go.kr/edu/E01/eduList.do?currentMenuNo=567`
- Parser: `ansan_edu_category_cards`
- Domain category: `교육·강좌`

## Collection Rule

The original YAML pointed to a single detail URL. That caused miscollection because the generic flow could save attached file names and guidance text as course titles.

The crawler now starts from the education list and iterates every visible `교육·강좌` category:

| Code | Category | currentMenuNo |
| --- | --- | --- |
| `E01` | 외국어 | `567` |
| `E02` | 정보화 | `603` |
| `E03` | 음악 | `604` |
| `E04` | 미술 | `614` |
| `E05` | 체육 | `615` |
| `E07` | 과학 | `703` |
| `E06` | 기타 | `616` |

Each category list is paginated with `pageIndex`. Course detail URLs are reconstructed from the `fnView('RESR_...')` JavaScript reservation id.

## Fields

The list cards currently provide:

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

Branch codes are derived from the visible institution or department name, not from category code, so map grouping remains branch-oriented.

## Quality Check

Command:

```bash
python -X utf8 Crawler/generated_yaml/MUNI_RESERVE_ANSAN_GO_KR_8236CAF0.py --per-target-limit 0 --max-pages 1 --detail-limit 0 --timeout 30
python -X utf8 tools/report_municipal_crawler_quality.py --report logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_091714.yaml --limit 15
```

Result:

| Rows | Grade | Parser | Core | Important | Period | Schedule | Fee | Status | Target | Description |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 70 | A | `ansan_edu_category_cards` | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
