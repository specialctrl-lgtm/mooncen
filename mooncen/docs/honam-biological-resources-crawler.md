# HONAM_BIOLOGICAL_RESOURCES Crawler

Provider: `HONAM_BIOLOGICAL_RESOURCES`

Source:

- List: `https://resve.hnibr.re.kr/front/edu/eduFrontList.do?menu_id=00000440`
- Detail: `https://resve.hnibr.re.kr/index.do?menu_id=00000440&menu_link=front/edu/eduFrontDetail.do&edu_id={edu_id}`

Implementation:

- File: `Crawler/generated_yaml/HONAM_BIOLOGICAL_RESOURCES.py`
- Parser: `honam_bio_cards`
- This crawler is standalone and does not depend on `Crawler_MunicipalYaml.py`.

Fields:

- `title`
- `branch`
- `address`
- `period`
- `schedule_raw`
- `target`
- `fee`
- `status`
- `description`
- `image_url`
- `application_url`
- `venue_name`
- `venue_address`

Run:

```bash
python -X utf8 Crawler/generated_yaml/HONAM_BIOLOGICAL_RESOURCES.py --limit 10 --max-pages 3 --detail-limit 10 --timeout 20
python -X utf8 Crawler/generated_yaml/HONAM_BIOLOGICAL_RESOURCES.py --limit 10 --max-pages 3 --detail-limit 10 --timeout 20 --save-db
```

Latest local verification:

| Rows | Saved | Pages | Detail | Title | Branch | Address | Period | Schedule | Fee | Target | Description | Image | Application URL |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 10 | 10 | 2 | 10 | 10 | 10 | 10 | 7 | 10 | 10 | 10 | 10 | 10 | 10 |

DB verification after saving 10 rows:

| Total | Title | Branch | Start Date | End Date | Schedule | Fee Numeric | Description | Image | Application URL |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 10 | 10 | 10 | 5 | 5 | 10 | 1 | 10 | 10 | 10 |

Some detail pages expose multi-date education days such as `2026-07-11 ~ 2026-08-22 ~ 2026-09-05`; the crawler writes the first and last detected date to `start_date` / `end_date` and preserves the original period in `raw_fields.period_raw`.

The source exposes fee labels as `무료` or `유료`. `무료` is converted to numeric `fee = 0`; `유료` without an amount is preserved in `raw_fields.fee_raw` and leaves the numeric `fee` empty.
