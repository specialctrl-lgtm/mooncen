# Busan Reserve Lecture Crawler

## Provider

- Provider: `MUNI_RESERVE_BUSAN_GO_KR_2CB22A99`
- Name: `부산광역시 통합예약`
- Source: `https://reserve.busan.go.kr/lctre/list?&srchGugun=2&srchResveInsttCd=33`
- Parser: `busan_reserve_list+detail`
- Domain category: `평생교육`
- Collection category: `공공예약`

## Collection Rule

The 부산광역시 통합예약 lecture list renders course cards under `ul.reserveList > li`.
The list card contains the visible title, status, 기관, 대상, 장소, 일자, 방법, 문의, and an `fn_viewProgrm(group, program)` detail link.

The crawler now follows each detail link:

```text
https://reserve.busan.go.kr/lctre/view?resveGroupSn={group}&progrmSn={program}
```

The detail page is required because the list page does not expose fee or weekday/time reliably.

## Fields

The parser fills:

- `title`
- `branch` / `branch_code`
- `raw_url` / `application_url`
- `period`
- `apply_period`
- `schedule_raw`
- `target`
- `fee`
- `status`
- `room` / `venue_name`
- `description`
- `application_method_raw`
- `collection_category`
- `domain_category`
- `operator_type`
- `source_group`
- `program_type`

Detail fields are mapped from the `dl` label/value pairs such as `운영기간`, `신청기간`, `수강료`, `요일 /시간`, `운영기관`, and `대상`.

## Quality Check

Command:

```bash
python -X utf8 Crawler/generated_yaml/MUNI_RESERVE_BUSAN_GO_KR_2CB22A99.py --per-target-limit 0 --max-pages 2 --detail-limit 0 --timeout 30
python -X utf8 tools/report_municipal_crawler_quality.py --report logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_095804.yaml --limit 15
```

Result:

| Rows | Grade | Parser | Core | Important | Period | Schedule | Fee | Status | Target | Description |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 20 | A | `busan_reserve_list+detail` | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |

## Notes

- The crawler skips expired operation periods through the common municipal row filtering path.
- `detail_limit=0` means all discovered details are fetched.
- This provider is registered as `ready` in both `config/collected_yaml_crawl_targets.yaml` and `config/crawl_targets/public_reservation.yaml`.
