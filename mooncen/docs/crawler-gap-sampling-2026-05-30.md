# Crawler Gap Sampling - 2026-05-30

This note summarizes which generated/public crawlers fail or collect weak data, based on local reports and DB samples.

## Source

- Main sampling report: `logs/municipal_crawler_reports/municipal_yaml_crawler_20260528_010030.yaml`
- Scope: 435 generated/public targets, 6,251 saved rows
- Current registry snapshot: 608 target rows, 449 runnable targets
- Current DB snapshot: non-culture-center course rows grouped by `collection_category`

## High-Level Findings

| Area | Finding | Impact |
| --- | --- | --- |
| Target registry | 98 `needs_parser`, 41 `blocked`, 10 `needs_discovery` | About 149 targets need parser, URL, or access work before stable collection |
| Latest full run | 2 of 435 targets collected 0 rows | Most runnable targets return rows, but quality varies |
| Field quality | Status 26.5%, fee 35.7%, schedule 47.1%, target 43.9%, description 42.8% | The generic parser finds titles/URLs well, but details are often missing |
| DB quality | 평생학습/public reservation rows have many missing period/schedule/target values | These sites need provider-family parsers, not only generic text extraction |
| Source quality | News/article pages and notice boards produce low-quality rows | These should be discovery seeds, not course providers |
| Fee semantics | `fee = 0` mixes free courses and unknown fees | Add a raw fee/status distinction before judging fee quality |

## Registry Status

| Status | Count | Meaning |
| --- | ---: | --- |
| `partial` | 388 | Generic parser works partly, but may miss fields |
| `ready` | 65 | Better than generic baseline |
| `needs_parser` | 98 | Needs provider-specific parser or better selectors |
| `blocked` | 41 | Access, SSL, JS, or known unsuitable source |
| `needs_discovery` | 10 | Need to discover actual reservation/list page first |

## Latest Full Run Field Rates

| Field | Filled | Rate |
| --- | ---: | ---: |
| title | 6,251 | 100.0% |
| branch | 6,251 | 100.0% |
| raw_url | 6,251 | 100.0% |
| period | 4,074 | 65.2% |
| schedule_raw | 2,945 | 47.1% |
| target | 2,743 | 43.9% |
| description | 2,674 | 42.8% |
| fee | 2,231 | 35.7% |
| status | 1,655 | 26.5% |

## Common Failure Patterns

| Pattern | Samples | Cause | Fix Direction |
| --- | --- | --- | --- |
| News/article source | `boeuni.com`, `brcity.kr`, `domin.co.kr`, `ggilbo.com`, `seoulilbo.com` | Article text mentions a course, but no structured list/detail fields | Move to discovery/reference only; do not save as course provider |
| Notice board instead of course list | Samcheok, Yeongdo, Daedeok samples | Rows are announcements; period/target/schedule are embedded in prose | Either parse notice body as one announcement item, or find actual application list URL |
| Label fragments saved as rows | Gongju, Ansan samples like `운영기간 :`, `신청기간 :` | Generic card/table parser treats labels as titles | Reject label-only titles and require title plus at least one evidence field |
| Main page cards saved as courses | Ansan main page | Homepage snippets are not course rows | Lower priority for main pages; prefer list endpoints discovered from links/forms |
| SSL/old server failures | `dylib.jne.go.kr`, `grlib.jne.go.kr`, `dokseodang.sd.go.kr`, `edu.sokcho.go.kr` | Old TLS/DH settings reject normal requests | Add curl fallback with weaker TLS cipher option or run via browser/Selenium |
| JS/iframe/search-form pages | Chuncheon `bwb.chuncheon.go.kr`, some reservation pages | Data appears after form/search or inside iframe | Add form-submit parser and iframe follow mode |
| Wrong URL/dead URL | Cheongdo 404, Anseong timeout sample | Search result URL moved or no longer valid | Rediscover from site search or mark deprecated |

## Representative Samples

| Provider | Problem | Sample |
| --- | --- | --- |
| `MUNI_WWW_SJ_GO_KR_5AF393CB` | Non-course row | `리베볼 파트타이머 모집...` from a board page |
| `MUNI_WWW_GONGJU_GO_KR_7CBA2D38` | Label-only rows | `운영기간 :`, `접수기간 :` |
| `MUNI_LLL_ANSAN_GO_KR_AE8DC75D` | Homepage snippets | `신청기간 : 2026-05-27 ~ 2026-06-05` saved as title |
| `MUNI_SUGANG_ASAN_GO_KR_FF504CD1` | List title only | Course names saved but period/schedule/target missing |
| `BUSAN_DONGGU_RESERVATION` | Detail text collapsed into title | Title includes 접수기간/교육기간 but dates are not split |
| `NATIONAL_ECOLOGY_CENTER` | Navigation/facility rows | `전시`, `매주 월요일 휴관` |

## DB Category Gaps

| Category | Rows | No Period | No Schedule | No Target | No Description |
| --- | ---: | ---: | ---: | ---: | ---: |
| 평생학습 | 9,020 | 5,182 | 5,310 | 5,768 | 5,399 |
| 공공예약 | 1,975 | 1,082 | 1,283 | 935 | 1,447 |
| 도서관 | 761 | 242 | 375 | 162 | 405 |
| 예술/공연 | 496 | 376 | 150 | 377 | 287 |
| 복지관 | 421 | 115 | 141 | 194 | 307 |
| 체육/스포츠 | 245 | 196 | 102 | 100 | 109 |
| 박물관/과학관 | 102 | 76 | 88 | 45 | 73 |

`fee = 0` is not shown here as a pure missing signal because the DB currently cannot distinguish free from unknown.

## Recommended Fix Plan

| Priority | Work | Why |
| --- | --- | --- |
| P0 | Split `fee_raw` / `fee_status` or equivalent | Avoid treating unknown fee as free |
| P0 | Exclude news/article domains from provider crawling | They are useful as discovery hints but poor course sources |
| P0 | Add generic parser guardrails: reject label-only titles, news/notice titles, nav titles, employment/recruiting rows | Reduces bad data immediately |
| P1 | Add provider-family parsers for common municipal platforms: `*.go.kr` board/list, `lecture.es`, `webEdcLctreList.do`, `learningList.do`, `reserve.busan.go.kr` | These patterns cover many targets |
| P1 | Add form-submit and iframe-follow discovery | Many public reservation pages require selecting/searching before rows appear |
| P1 | Add TLS fallback mode for old public sites | Recovers JNE libraries and older district education pages |
| P2 | Store `source_endpoint` / entry-point key | Prevents stale/deactivate mistakes when one provider has multiple collection URLs |
| P2 | Add Ops Console gap sampler | Operators can click a provider and see missing-field samples and suggested parser family |

## Question

News/article targets are currently the largest noisy source category. They can either be:

1. Deprecated as course providers and kept only as manual discovery references.
2. Parsed as one-off announcement records with weak schedule/target extraction.

The safer default is option 1.

## Implementation Status - 2026-08-09

All eight items in the recommended plan are implemented in the current
working tree. This status does not mean that the migration has been applied or
that the working tree has been deployed.

| Priority | Work | Implemented contract |
| --- | --- | --- |
| P0 | Fee semantics | Canonical storage keeps `NULL` as unknown and `0` as explicitly free. API responses expose `fee_status` (`UNKNOWN`, `FREE`, `PAID`), raw crawler evidence keeps `fee_raw`/`fee_status`, and SEO omits unknown prices instead of publishing zero. |
| P0 | Media exclusion | News/article domains remain discovery-only or deprecated and cannot be promoted as active course providers. |
| P0 | Generic guardrails | Shared semantic/title gates reject editorial, notice, navigation, label-only, employment, recruiting, and other non-course rows before persistence. |
| P1 | Provider families | The configured `lecture.es`, `webEdcLctreList.do`, `learningList.do`, Busan reservation, and reviewed municipal list/detail families dispatch to dedicated parsers. |
| P1 | Form/iframe discovery | Both generic discovery paths follow bounded same-site course iframes and safe GET catalogue/search forms. POST/application/PII forms remain fail-closed; reviewed provider-specific adapters handle sites that require a non-GET contract. |
| P1 | Legacy TLS | Host-scoped compatibility contexts keep certificate and hostname verification enabled for reviewed legacy public hosts. |
| P2 | Source endpoint | `courses.source_endpoint` is stored by manual/generated/municipal paths; direct and staging stale cleanup is scoped to the exact endpoint. Legacy NULL rows are not guessed or cross-deactivated. |
| P2 | Ops gap sampler | Ops Data Quality lets an operator select a provider, inspect missing-field course samples and source links, and see a deterministic suggested parser family. |
