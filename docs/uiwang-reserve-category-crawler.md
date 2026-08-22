# Uiwang Reserve Category Crawler

## Provider

- Providers:
  - `MUNI_WWW_UIWANG_GO_KR_2A9DF9A4`
  - `MUNI_WWW_UIWANG_GO_KR_F89FBD11`
- Source: `https://www.uiwang.go.kr/reserve/`
- Parser: `uiwang_reserve_category_cards`
- Domain category: `통합예약`

## Collection Rule

The original YAML pointed to either the integrated-reservation main page or one direct `eduList.do` page. Both providers now use the same parser. The crawler starts at the Uiwang integrated-reservation main page and discovers every visible `eduList.do` category link.

This covers the left/main category structure, including:

- 주민자치: 고천동, 부곡동, 오전동, 내손1동, 내손2동, 청계동
- 평생교육: 평생대학, 의왕학습레일, 인생도서관 사람책, 특화프로그램, 진로진학 프로그램, 체험 프로그램
- 도서관: 중앙도서관, 내손도서관, 글로벌도서관, 포일어울림도서관, 백운호수도서관, 작은도서관
- 청소년, 교육·강좌, 축제·행사, 공연·전시, 체험·캠프 where the site exposes list links

Each category is paginated with `pageIndex`. Detail URLs are reconstructed from the `fnView('RESR_...')` reservation id and the category's `eduView.do` path.

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
- `capacity`
- `room` / `venue_name`
- `description`
- `category` / `category_raw`

The parser uses visible education place as the branch because Uiwang categories often split by resident center, library, or program venue.

## Quality Check

Command:

```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_UIWANG_GO_KR_2A9DF9A4.py --per-target-limit 0 --max-pages 1 --detail-limit 0 --timeout 30
python -X utf8 Crawler/generated_yaml/MUNI_WWW_UIWANG_GO_KR_F89FBD11.py --per-target-limit 0 --max-pages 1 --detail-limit 0 --timeout 30
python -X utf8 tools/report_municipal_crawler_quality.py --report logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_112901.yaml --limit 15
```

Result:

| Rows | Grade | Parser | Core | Important | Period | Schedule | Fee | Status | Target | Description |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 198 | A | `uiwang_reserve_category_cards` | 100.0% | 95.0% | 89.9% | 89.9% | 100.0% | 100.0% | 89.9% | 100.0% |

Some event/facility categories are empty or do not expose course period/target fields on the list card. The course-oriented categories fill the core fields correctly.
