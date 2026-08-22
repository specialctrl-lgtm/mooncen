# Seoul Lifelong Learning Offline Crawler

## Provider

- Provider: `SEOUL_LIFELONG_LEARNING`
- Source: `https://sll.seoul.go.kr/main/doLifeLongEduInstListView.do?main_se=lie&mnid=202501953013`
- Parser: `sll_offline_district_links`
- Domain category: `평생교육`

## Collection Rule

The `오프라인학습 > 자치구평생학습` page is a district portal directory, not a course list. It exposes one link per Seoul district.

The crawler collects the 25 district links as branch-level rows:

- `branch`: district name such as `강남구`, `강동구`, `중랑구`
- `title`: `{branch} 평생학습`
- `raw_url` / `application_url`: district lifelong-learning portal URL
- `status`: `기관 링크`
- `target`: `지역 주민`
- `application_type`: `EXTERNAL_NOTICE`
- `discovery_status`: `district_learning_portal_link`

Course period, schedule, and fee are intentionally empty because the Seoul portal does not expose course details on this page. Detailed course extraction needs provider-specific parsers for each district portal.

## Quality Check

Command:

```bash
python -X utf8 Crawler/generated_yaml/SEOUL_LIFELONG_LEARNING.py --per-target-limit 0 --max-pages 1 --detail-limit 0 --timeout 30
python -X utf8 tools/report_municipal_crawler_quality.py --report logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_092652.yaml --limit 15
```

Result:

| Rows | Grade | Parser | Core | Important | Status | Target | Description |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 25 | B | `sll_offline_district_links` | 100.0% | 50.0% | 100.0% | 100.0% | 100.0% |

The grade is `B` because this is a branch/link discovery crawler. It is not expected to fill `period`, `schedule_raw`, or `fee`.
