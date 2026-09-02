# Removed Crawl Targets

This document records crawl targets that were intentionally removed so they are not reintroduced during YAML regeneration.

## 2026-05-27

Removed from collected crawl targets, national institution search targets, category YAML, generated registry, and generated crawler stubs:

- `https://www.bdna.or.kr`
- `https://www.nnibr.re.kr`
- `https://kna.forest.go.kr`
- `https://www.nibr.go.kr`

The national institution search YAML generator keeps these domains in `EXCLUDED_BASE_URLS`.

## 2026-06-05

Removed from welfare/collected crawl targets, category YAML, generated registry, and generated crawler stubs:

- `WOLBAE_SENIOR_PROGRAM_SCHEDULE`
- URL: `https://www.wbnb.or.kr/guide/guide_02.html`
- Reason: user-requested removal; the target was a static program schedule page, not an active course application list.

Existing DB rows for this provider were marked inactive instead of hard-deleted so historical crawler state remains auditable.
