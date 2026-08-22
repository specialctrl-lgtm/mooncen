# Busan Lifelong Learning Office Cycle Crawler

Provider: `MUNI_LLL_BUSAN_GO_KR_944C621B`

Source URL: `https://lll.busan.go.kr/yeyak/ilms/learning/officeList.do`

## Structure

The Busan lifelong learning platform exposes offices on `officeList.do`.

- Office codes are available from `#o_search_ch option[value]`.
- Visible office cards also expose `fn_learning_list('OFFICE_...')`, phone number, and office application status.
- Course rows are collected from `learningList.do?inst_id={office_code}&pageIndex={page}&searchCondition=0&searchKeyword=&out_inst_yn=N`.

## Parser

`collect_busan_lifelong_office_cycle` extracts all offices, builds one `learningList.do` URL per office, and reuses `collect_busan_lifelong_learning` for table parsing.

Collected fields:

- `title`
- `branch`, `branch_code`, `venue_name`, `phone`
- `period`, `schedule_raw`, `apply_period`
- `fee`, `status`, `capacity`, `instructor`
- `raw_url`, `application_url`, `description`

The source list does not expose structured address or target age fields. Address is handled by the branch address backfill process, and target age remains a post-processing/AI candidate.

## Test Result

Command:

```bash
python -X utf8 Crawler/generated_yaml/MUNI_LLL_BUSAN_GO_KR_944C621B.py --per-target-limit 0 --max-pages 5 --detail-limit 0 --timeout 30
```

Result on 2026-06-05:

| Metric | Value |
| --- | --- |
| Parser | `busan_lifelong_office_cycle` |
| Offices | 34 |
| Rows | 740 |
| Pages | 108 |
| title / branch / period / schedule / fee / description | 740 / 740 / 740 / 740 / 740 / 740 |
| address / target | 0 / 0 |

