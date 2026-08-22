# HYUNDAI_DEPT Crawler Notes

Updated: 2026-05-29

## Scope

The Hyundai Department Store culture-center crawler collects public course data from:

- `https://www.ehyundai.com/newCulture/CT/CT010100_L.do`
- Provider: `HYUNDAI_DEPT`
- Runtime: `Crawler/Crawler_YamlSources.py --provider HYUNDAI_DEPT`

## Collection Strategy

- Uses the public HTML list page, not Selenium.
- Requests all branches with `stCd=ALL`.
- Uses `pageSize=36` and follows the list pagination hints.
- Fetches each detail page to enrich list rows.

## Fields

List page:

- `title`
- `branch`
- `branch_code`
- `status`
- `category`
- `target`
- `period`
- `schedule_raw`
- `instructor`
- `sessions`
- `fee`
- `image_url`
- `raw_url`

Detail page:

- `branch`
- `instructor`
- `schedule_raw`
- `period`
- `sessions`
- `fee`
- `material_fee`
- `material_note`
- `room`
- `description`
- `image_url`

## Registration

The provider is registered in:

- `tools/sample_collect_from_yaml.py`
- `Crawler/Crawler_YamlSources.py`
- `run_crawlers.py`
- `config/crawler_targets.yaml`
- `config/crawl_targets/retail_culture.yaml`
- `deploy/ubuntu/setup_project.sh`
- `deploy/ubuntu/mooncen.env.example`
- `deploy/ubuntu/mooncenctl.sh`
- Ops Console branch-filter provider list

## Verification

Sample collection:

```bash
python tools/sample_collect_from_yaml.py --provider HYUNDAI_DEPT --limit 10
```

Result:

- collected: 10/10
- pages: 1
- title: 10/10
- branch: 10/10
- url: 10/10
- status: 10/10
- fee: 10/10
- target: 10/10
- image: 10/10
- description: 10/10
- material_fee: 10/10
- material_note: 10/10

DB save path:

```bash
python -X utf8 Crawler/Crawler_YamlSources.py --provider HYUNDAI_DEPT --limit 10
```

Result:

- collected: 10
- saved: 10/10

Integrated runner path:

```bash
python -X utf8 run_crawlers.py --providers HYUNDAI_DEPT --once --limit 10 --ignore-active-window --ignore-worker-lock --skip-coordinate-backfill --skip-category-backfill
```

Result:

- provider completed successfully
- saved: 10/10
- cycle report written under `logs/crawler_reports`

## Notes

- `target_age_group` may be empty for general adult/category rows until the DB normalization step runs, because many list rows expose only broad Korean category text and no explicit age range.
- Detail request failures should be handled as row-level failures in future if Hyundai starts timing out like LOTTE_MART.
- Local Windows runs can hit a progress-file replace failure when `logs` is a reparse-point directory. `run_crawlers.py` now falls back to direct progress-file writes after a failed atomic replace.
