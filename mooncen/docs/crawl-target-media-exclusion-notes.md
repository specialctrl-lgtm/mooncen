# Crawl Target Media Exclusion Notes

## 2026-06-03 Newspaper / News Media Exclusion

Newspaper and news-media URLs are excluded from crawl targets because they are usually article or notice pages, not course application sources.

Applied changes:

- Moved 19 active newspaper/news-media targets from `config/crawl_targets/*.yaml` to `config/crawl_targets/deprecated.yaml`.
- Added media-domain/name detection to `tools/split_crawl_targets_by_category.py` so future split/regeneration routes those rows to `deprecated`.
- Expanded low-value media domains in `Crawler/Crawler_MunicipalYaml.py` and `tools/validate_municipal_course_yaml.py`.

Active targets moved to deprecated:

- `전남일보`
- `전남투데이`
- `광주전남일보`
- `경인매일` 3 URLs
- `경북일보`
- `고양신문`
- `포인트경제`
- `서울일보`
- `투데이안`
- `양산신문`
- `영광21`
- `용인시민신문`
- `아시아투데이`
- `서울강북신문`
- `중도일보` 2 URLs
- `경북신문`

Validation:

- Active `config/crawl_targets/*.yaml` media candidates: `0`
- `python -m py_compile tools/split_crawl_targets_by_category.py tools/validate_municipal_course_yaml.py Crawler/Crawler_MunicipalYaml.py`
