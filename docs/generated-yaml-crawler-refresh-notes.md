# Generated YAML Crawler Refresh Notes

## 2026-06-05 GWACHEON_NATIONAL_SCIENCE_MUSEUM

Scope:

- Completed the National Gwacheon Science Museum schedule crawler.
- Existing route now uses the `scipia/schedules` table parser with field enrichment.
- The parser extracts detail URLs from `자세히보기` links and reservation URLs from `goPost(...)`, `ShowList(...)`, or direct `href`.

Fields:

- Fills title, period, room, target, fee, status, raw URL, application URL, and fixed venue address.
- Capacity fields are extracted when hidden `courseCapacity`, `applyCnt`, and `waitCapacity` inputs exist.
- Button-only description text such as `자세히보기` is discarded.

Validation:

- Report: `logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_122705.yaml`
- Result: 6 rows, parser `gwacheon_scipia_table`, application URLs 5/6.

YAML status:

- `config/collected_yaml_crawl_targets.yaml`: `crawler_status=ready`, parser `gwacheon_scipia_table`.
- `config/crawl_targets/museum_science.yaml`: `crawler_status=ready`, parser `gwacheon_scipia_table`.

## 2026-06-05 MUNI_WWW_SB_GO_KR_FF615DE7

Scope:

- Completed the Seongbuk-gu integrated reservation target.
- Added dedicated parser `sb_unity_program_categories`.
- The crawler cycles `searchProgrmSe` categories and uses the `viewType=list` table for title, period, weekday, capacity, branch, and status.
- Detail pages are reconstructed from `fnView(this, progrmNo)` and fetched through `unityProgrmWebView.do`.

Fields:

- List parsing fills title, apply period, education period, branch, weekday, capacity, status, and detail URL.
- Detail parsing fills category, target, room, venue name, venue address, phone, schedule time, fee, material fee when present, and application method.
- Map address is read from `.map_info_item.address`; this avoids header links such as `서울런` being misclassified as addresses.

Validation:

- Sample report: `logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_121048.yaml`
- Full category-cycle report: `logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_120821.yaml`
- Result: 1,014 rows, 57 pages, parser `sb_unity_program_categories`.
- Fee/address/target are detail-only fields, so production runs should use `--detail-limit 1200`.

YAML status:

- `config/collected_yaml_crawl_targets.yaml`: `crawler_status=ready`, parser `sb_unity_program_categories`.
- `config/crawl_targets/public_reservation.yaml`: `crawler_status=ready`, parser `sb_unity_program_categories`.
- `config/generated_yaml_crawler_registry.yaml`: target enabled as `ready`, command includes `--max-pages 30 --detail-limit 1200`.

## 2026-06-05 Daejeon Seogu Wolpyeong Library Parser

Scope:

- Completed `MUNI_WWW_SEOGU_GO_KR_A27782FE`.
- The YAML target points to the Wolpyeong Library homepage, so the parser redirects internally to the `행사 및 강좌 신청` list page.
- The crawler parses `table.tbl_basic` rows and follows reconstructed detail pages from `fn_egov_select(..., 'LEC_...')`.

Fields:

- List parsing fills title, category, target, period, time, capacity, and detail URL.
- Detail parsing fills weekday, application period, instructor, target override, waitlist, description, and image URL when present.
- Branch/address are mapped from the library URL; this target saves rows under `월평도서관`.

Validation:

- Sample report: `logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_104349.yaml`
- Quality report: `logs/municipal_crawler_quality/municipal_yaml_crawler_20260605_104349_quality.md`
- Result: 10 rows, grade `A`, core `100.0%`, important `100.0%`.

YAML status:

- `config/collected_yaml_crawl_targets.yaml`: `crawler_status=ready`, parser `seogu_library_classes+detail`.
- `config/crawl_targets/library.yaml`: `crawler_status=ready`, parser `seogu_library_classes+detail`.

## 2026-06-05 Yeonsu Friday Art Parser

Scope:

- Completed `MUNI_WWW_YEONSU_GO_KR_B2B6DF58`.
- The crawler reads Yeonsu Culture Portal Friday Art performance cards at `https://www.yeonsu.go.kr/culture/show/friday_art/reservation.asp`.
- The previous generic parser collected portal menu rows. The dedicated parser now restricts extraction to `#contents .reservation_list > ul > li` and follows `상세보기` detail pages.

Fields:

- List parsing fills title, performance date/time, reservation period, remaining seats, status, poster image, detail URL, and reservation URL.
- Detail parsing fills description, performance group as instructor, poster image, and normalized schedule fields.
- Branch and venue are fixed to `연수아트홀` for map grouping.

Validation:

- Sample report: `logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_103458.yaml`
- Quality report: `logs/municipal_crawler_quality/municipal_yaml_crawler_20260605_103458_quality.md`
- Result: 10 rows, grade `A`, core `100.0%`, important `100.0%`.

YAML status:

- `config/collected_yaml_crawl_targets.yaml`: `crawler_status=ready`, parser `yeonsu_friday_art_cards+detail`.
- `config/crawl_targets/arts_culture.yaml`: `crawler_status=ready`, parser `yeonsu_friday_art_cards+detail`.
- `config/generated_yaml_crawler_registry.yaml`: target enabled as `ready`.

## 2026-06-05 Ijongno FMCS JONGNO02 Parser

Scope:

- Completed `MUNI_WWW_IJONGNO_CO_KR_D4C024C6`.
- This target is the Jongno Facilities Management Corporation FMCS lecture list filtered by `center=JONGNO02`.
- The crawler uses the shared `fmcs_lecture_api` parser and now respects the `center` query parameter instead of crawling every FMCS company/category combination.
- For this site, the API and detail page expose weekday/time, fee, target, status, venue, and instructor, but do not expose a concrete class date range for the monthly facility classes. Rows without `train_sdate/train_edate` are stored with `period = "월 단위 상시 강좌"` to distinguish them from missing data.

Fields:

- API parsing fills `title`, `branch`, `branch_code`, `category`, `raw_url`, `status`, `fee`, `period`, `schedule_raw`, `target`, `instructor`, `capacity`, and `application_method_raw`.
- Detail parsing fills `venue_name`, detail target/instructor/capacity overrides, and `description` where the site actually provides text.
- Google geocoding fills the active branch `종로구민회관` at `서울특별시 종로구 지봉로5길 7-5`.

Validation:

- Full save command:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_WWW_IJONGNO_CO_KR_D4C024C6.py --per-target-limit 0 --max-pages 5 --detail-limit 120 --timeout 25 --save-db --mark-stale
```

- Integration command:

```powershell
python -X utf8 run_crawlers.py --providers MUNI_WWW_IJONGNO_CO_KR_D4C024C6 --limit 3 --once --ignore-active-window --ignore-worker-lock --skip-coordinate-backfill --skip-category-backfill
```

- Reports:
  - Full save report: `logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_061133.yaml`
  - Quality report: `logs/municipal_crawler_quality/municipal_yaml_crawler_20260605_061133_quality.md`
  - Integration report: `logs/crawler_reports/crawler_report_20260605_061336.json`
  - `114` rows collected and saved.
  - API list pages reduced from the previous broad fallback path to `2` pages.
  - Field quality grade `A`: core `100.0%`, important `83.8%`, period/schedule/fee/status/target all `100.0%`.
  - Description coverage is low (`3/114`) because most FMCS facility-class detail pages do not provide a description body.

YAML status:

- `config/collected_yaml_crawl_targets.yaml`: `crawler_status=ready`, `collection_type=static_html`, parser `fmcs_lecture_api`.
- `config/crawl_targets/sports_facility.yaml`: `crawler_status=ready`, parser `fmcs_lecture_api`.
- `config/generated_yaml_crawler_registry.yaml`: target enabled as `ready`.

## 2026-06-05 Inje Lifelong Learning Parser

Scope:

- Added a dedicated parser for `MUNI_WWW_INJE_GO_KR_44A2D640`.
- The original target URL pointed at the Inje portal participation shell. The crawler now reads the actual lifelong-learning course lists:
  - `https://lifelong.inje.go.kr/lct/course/list`
  - `https://lifelong.inje.go.kr/lct/edu/list`
- Detail pages are fetched from `/lct/course/view?courseSeq=...`.
- Facility metadata is read from `/facilities/list` and merged into branch/address data.

Fields:

- List parsing fills `title`, `category`, `period`, `apply_period`, `target`, `status`, `fee`, `material_fee`, capacity, and detail URL.
- Detail parsing fills `schedule_raw`, `description`, `venue_name`, instructor, phone, and image URL when present.
- Branches are split by provider branch plus venue, for example `Inje Lifelong Learning Center - Life Science Hall`, so map markers can reflect actual venues.
- Address extraction handles square-bracket and parenthesized road-address fragments such as `비봉로44번길 105` and `북면 원통로74번길 10-4`.

Validation:

- Sample command:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_WWW_INJE_GO_KR_44A2D640.py --per-target-limit 100 --max-pages 8 --detail-limit 100 --timeout 25
```

- Full save command:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_WWW_INJE_GO_KR_44A2D640.py --per-target-limit 0 --max-pages 20 --detail-limit 250 --timeout 25 --save-db --mark-stale
```

- Integration command:

```powershell
python -X utf8 run_crawlers.py --providers MUNI_WWW_INJE_GO_KR_44A2D640 --limit 3 --once --ignore-active-window --ignore-worker-lock --skip-coordinate-backfill --skip-category-backfill
```

- Reports:
  - Full save report: `logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_060412.yaml`
  - Quality report: `logs/municipal_crawler_quality/municipal_yaml_crawler_20260605_060412_quality.md`
  - Integration report: `logs/crawler_reports/crawler_report_20260605_060635.json`
  - `335` rows collected across `34` list pages and `250` detail pages.
  - `34` current/non-expired rows saved.
  - Field quality grade `A`: core `100.0%`, important `94.5%`, fee `97.9%`, target `97.3%`, description `71.9%`.
  - Google geocoding updated `14/14` Inje branches with verified coordinates.

YAML status:

- `config/collected_yaml_crawl_targets.yaml`: `crawler_status=ready`, `collection_type=static_html`, parser `inje_lifelong_course_cards`.
- `config/crawl_targets/arts_culture.yaml`: `crawler_status=ready`, parser `inje_lifelong_course_cards`.
- `config/generated_yaml_crawler_registry.yaml`: target enabled as `ready`.

## 2026-06-05 National Hangeul Museum Parser

Scope:

- Added a dedicated parser for `NATIONAL_HANGEUL_MUSEUM`.
- The crawler ignores the homepage shell and reads the actual education list at `/education`.
- It also reads cultural and academic event lists at `/eduCul/event/reservList.do` with `eventType=1` and `eventType=2`.
- Detail pages are fetched through `/education/view?...programNo=...` and `/eduCul/event/onlineReservDetail.do?...eventNo=...`.

Fields:

- List parsing fills `title`, `status`, labels/category, target, application period, education/event period, branch, address, and list image when present.
- Detail parsing fills `fee`, `description`, `capacity`, `application_method_raw`, `venue_name`, `phone`, detailed schedule rows, and image URL.
- The branch is normalized to a single `국립한글박물관` branch with address `서울시 용산구 서빙고로 139`.
- Expired rows are collected for quality evaluation but skipped before DB save by the existing lifecycle policy.

Validation:

- Sample command:

```powershell
python -X utf8 Crawler\generated_yaml\NATIONAL_HANGEUL_MUSEUM.py --per-target-limit 10 --max-pages 5 --detail-limit 10 --timeout 25
```

- Full save command:

```powershell
python -X utf8 Crawler\generated_yaml\NATIONAL_HANGEUL_MUSEUM.py --per-target-limit 100 --max-pages 6 --detail-limit 100 --timeout 25 --save-db --mark-stale
```

- Report:
  - Full sample: `logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_053407.yaml`
  - Saved run: `logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_053929.yaml`
  - Quality report: `logs/municipal_crawler_quality/municipal_yaml_crawler_20260605_053929_quality.md`
  - `75` rows collected across `8` list pages and `75` detail pages.
  - `8` current/non-expired rows saved.
  - Field quality grade `A`: core `100.0%`, important `99.3%`, fee `98.7%`, description `97.3%`.
  - Active branch coordinates are present for `국립한글박물관`.

YAML status:

- `config/collected_yaml_crawl_targets.yaml`: `crawler_status=ready`, `collection_type=static_html`, parser `national_hangeul_education_event`.
- `config/crawl_targets/museum_science.yaml`: `crawler_status=ready`, parser `national_hangeul_education_event`.
- `config/generated_yaml_crawler_registry.yaml`: target enabled as `ready`.

## 2026-06-05 National Lighthouse Museum Parser

Scope:

- Added a dedicated parser for `NATIONAL_LIGHTHOUSE_MUSEUM`.
- The crawler ignores the broad homepage and reads the actual education/event list at `/prog/evntPrgrm/kor/sub01_02/list.do`.
- Detail pages are fetched through `view.do?prgrmNo=...` and merged into each row.

Fields:

- List parsing fills `title`, `category`, `application_method_raw`, `period`, `apply_period`, `schedule_raw`, `target`, `venue_name`, `image_url`, and status inferred from the application period.
- Detail parsing fills `description`, more specific `target` text when present, `capacity`, material notes/fees when present, and explicit fee text such as `참가비: 무료`.
- The branch is normalized to `국립등대박물관` with address `경북 포항시 남구 호미곶면 해맞이로150번길 20`.

Validation:

- Sample command:

```powershell
python -X utf8 Crawler\generated_yaml\NATIONAL_LIGHTHOUSE_MUSEUM.py --per-target-limit 10 --max-pages 5 --detail-limit 10 --timeout 25
```

- Full save command:

```powershell
python -X utf8 Crawler\generated_yaml\NATIONAL_LIGHTHOUSE_MUSEUM.py --per-target-limit 100 --max-pages 5 --detail-limit 50 --timeout 25 --save-db --mark-stale
```

- Report:
  - Full sample: `logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_052133.yaml`
  - Save/active quality: `logs/crawler_reports/crawler_report_20260605_052032.json`
  - `30` rows collected.
  - `1` current/non-expired row saved; expired rows were skipped before branch save.
  - Active branch coordinates were backfilled with Google Geocoding.
  - Active saved-row quality is `100.0%` for title, branch, URL, description, image, schedule, target age group, fee, and status. The saved row has no specific class time because the site exposes the current program as a month range.

YAML status:

- `config/collected_yaml_crawl_targets.yaml`: `crawler_status=ready`, `collection_type=static_html`.
- `config/crawl_targets/museum_science.yaml`: `crawler_status=ready`, parser `national_lighthouse_event_programs`.

## 2026-06-05 Namwon Reserve API Parser

Scope:

- Added a dedicated parser for `MUNI_WWW_NAMWON_GO_KR_37D4EA88`.
- The crawler reads Namwon's `items.do` JSON reservation API instead of the rendered reservation shell.
- The parser covers lifelong learning, citizen education, performance lectures, experience/tour, Kim Byeongjong Museum, Baekdudaegan lodging/camping, Gyoryong camping, public sports rental, and support programs.

Branch handling:

- Map branches are preserved at institution or external venue level.
- Internal rooms, lodging subtypes, and campsite types are saved as `venue_name`.
- Known facility address hints and row-level road-address parsing feed branch address creation.
- Active branch coordinates were backfilled with Google Geocoding after DB save.

Validation:

- Sample command:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_WWW_NAMWON_GO_KR_37D4EA88.py --per-target-limit 10 --max-pages 2 --detail-limit 10 --timeout 25
```

- Full save command:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_WWW_NAMWON_GO_KR_37D4EA88.py --per-target-limit 0 --max-pages 20 --detail-limit 0 --timeout 25 --save-db --mark-stale
```

- Report:
  - `logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_025652.yaml`
  - `1025` rows collected.
  - `136` non-expired/current rows saved.
  - Active DB branches: `15`.
  - Active branches with coordinates: `15/15`.

YAML status:

- `config/collected_yaml_crawl_targets.yaml`: `crawler_status=ready`, `collection_type=api_json`.
- `config/crawl_targets/public_reservation.yaml`: `crawler_status=ready`, `collection_type=api_json`.
- `config/generated_yaml_crawler_registry.yaml`: target enabled as `ready`.

## 2026-06-04 URL-Updated Targets

The generated YAML crawler registry and wrapper files were refreshed from the current `config/crawl_targets/*.yaml` files after URL edits in Ops Console.

Changes:

- Rebuilt `config/generated_yaml_crawler_registry.yaml` with `Crawler/Crawler_GeneratedYamlTargets.py --write-registry`.
- Regenerated provider wrapper scripts under `Crawler/generated_yaml/`.
- Updated `tools/generate_registry_crawler_files.py` so multiple URLs under the same provider create only one wrapper script.
- Expanded generated-crawler media URL exclusions in `Crawler/Crawler_GeneratedYamlTargets.py`.
- Confirmed newspaper/news-media providers are no longer present in the generated registry or generated wrapper folder.

Current counts:

- Registry targets: `549`
- Enabled generated targets: `449`
- Disabled generated targets: `100`
- Generated wrapper Python files: `546` provider wrappers plus `__init__.py`

Validation:

- Compiled `Crawler/Crawler_GeneratedYamlTargets.py` and `tools/generate_registry_crawler_files.py`.
- Compiled all `Crawler/generated_yaml/*.py` wrapper files.
- Sample crawl succeeded for recent URL-updated providers:
  - `MUNI_TYLIB_GNE_GO_KR_7D159AC1`: 10 rows, parser `gne_library_lec_list`
  - `MUNI_USBL_BUKGU_ULSAN_KR_A68023CB`: 10 rows, parser `card+detail`
  - `MUNI_WWW_BCL_GO_KR_DDAE2544`: 10 rows, parser `detail+table`
- Wrapper smoke test succeeded:
  - `python -X utf8 Crawler/generated_yaml/MUNI_WWW_BCL_GO_KR_DDAE2544.py --per-target-limit 3 --max-pages 2 --detail-limit 3 --timeout 20`

Run examples:

```powershell
python -X utf8 Crawler/Crawler_GeneratedYamlTargets.py --provider MUNI_WWW_BCL_GO_KR_DDAE2544 --save-db
python -X utf8 Crawler/generated_yaml/MUNI_WWW_BCL_GO_KR_DDAE2544.py --save-db
python -X utf8 run_crawlers.py --providers MUNI_WWW_BCL_GO_KR_DDAE2544
```

## 2026-06-04 Manual URL Parser Refresh

Scope:

- Re-tested Ops Console manually changed targets with `manual_action: add/update`.
- Added dedicated parsers for pages that generic table/card parsing could not read.
- Split active crawl targets from discovery-only URLs so invalid landing/board URLs do not run in the default generated crawler queue.

New dedicated parser routes in `Crawler/Crawler_MunicipalYaml.py`:

- `yjlib_article_cards`: Yeoju library `lectureList.do` article cards.
- `yeongtong_welfare_api`: Yeongtong welfare center JSON API.
- `daegu_expr_reservation`: Daegu reservation JSON API.
- `geochang_lifelong_cards`: Geochang lifelong learning course cards.
- `ydct_lecture2_table`: Yeongdeok culture foundation `lecture2` table.
- `anyang_learning_search_table`: Anyang integrated lifelong-learning table with curl fallback for legacy TLS/EUC-KR.

Status updates:

- `ready`: Yeoju library branch URLs, Yeongtong welfare, Daegu reservation, Geochang lifelong learning, Yeongdeok culture foundation, Anyang integrated lifelong-learning URL.
- `needs_discovery`: Hamyang welfare landing page, Inje old participation board URL, and GWE Yeongwol library URL with no active lecture items.
- Generated crawler default statuses now include only `ready`, `partial`, and `candidate`.
- `needs_discovery` and `needs_parser` remain visible in the registry but are disabled by default; use `--include-status` only for audit runs.

Validation reports:

- All manually changed URLs before disabling discovery-only rows:
  - `logs/municipal_crawler_reports/municipal_yaml_crawler_20260604_185813.yaml`
  - `94` targets, `91` success, `743` rows, `0` DB saves.
- Active manually changed URLs after status cleanup:
  - `logs/municipal_crawler_reports/municipal_yaml_crawler_20260604_190942.yaml`
  - `86` targets, `86` success, `708` rows, `0` DB saves.
- Quality output:
  - `logs/municipal_crawler_quality/municipal_yaml_crawler_20260604_190942_quality.md`
  - `A: 53`, `B: 21`, `C: 10`, `D: 2`, `NO_DATA: 0`, `ERROR: 0`.

Latest generated registry counts:

- Registry targets: `548`
- Enabled generated targets: `403`
- Disabled generated targets: `145`
- Generated wrapper Python files: `536` provider wrappers plus `__init__.py`

Run examples:

```powershell
python -X utf8 Crawler/Crawler_GeneratedYamlTargets.py --all --per-target-limit 10 --max-pages 4 --detail-limit 8 --timeout 18
python -X utf8 Crawler/Crawler_GeneratedYamlTargets.py --all --include-status needs_discovery --per-target-limit 5
python -X utf8 Crawler/generated_yaml/MUNI_EDUCITY_GEOCHANG_GO_KR_3187BF2A.py --limit 10
```

## 2026-06-04 Busan Lifelong Platform Branch Targets

Scope:

- Added Busan Lifelong Learning Platform branch-level targets from `https://lll.busan.go.kr/yeyak/ilms/learning/officeList.do`.
- Registered 24 `learningList.do?inst_id=OFFICE_...` targets under `BUSAN_LIFELONG_PLATFORM` in `config/crawl_targets/lifelong_learning.yaml`.
- Marked the old root URL and single office-list URL as `needs_discovery` so the default generated crawler queue uses branch course lists instead of the institution-list page.

Parser:

- Added `busan_lifelong_learning_table` in `Crawler/Crawler_MunicipalYaml.py`.
- The parser reads the Busan `learningList.do` table directly and fills:
  - `title`
  - `branch`
  - `branch_code`
  - `raw_url` / `application_url`
  - `status`
  - `fee`
  - `period`
  - `apply_period`
  - `schedule_raw`
  - `capacity`
  - `description`

Validation:

- Command:

```powershell
python -X utf8 Crawler/generated_yaml/BUSAN_LIFELONG_PLATFORM.py --per-target-limit 5 --max-pages 2 --detail-limit 0 --timeout 20 --parallel-workers 4
```

- Report:
  - `logs/municipal_crawler_reports/municipal_yaml_crawler_20260604_221215.yaml`
  - `24` branch targets executed.
  - `17` targets returned current course rows.
  - `7` targets returned no current rows.
  - `81` total sample rows collected.
  - `0` DB saves.
- Quality:
  - `logs/municipal_crawler_quality/municipal_yaml_crawler_20260604_221215_quality.md`
  - `A: 17`, `NO_DATA: 7`, `ERROR: 0`.

## 2026-06-04 Ansan Integrated Reservation URL Unblock

Scope:

- Unblocked `MUNI_RESERVE_ANSAN_GO_KR_5D6B8309`.
- Replaced the broken root/session URL with the current education list URL:
  - `https://reserve.ansan.go.kr/edu/E01/eduList.do?currentMenuNo=567`
- Assigned the dedicated parser `ansan_reserve_cards`.

Parser:

- Added `ansan_reserve_cards` in `Crawler/Crawler_MunicipalYaml.py`.
- The parser reads Ansan reservation card lists and fills:
  - `title`
  - `branch`
  - `raw_url` / `application_url`
  - `status`
  - `fee`
  - `period`
  - `apply_period`
  - `schedule_raw`
  - `target`
  - `venue_name`
  - `description`

Validation:

- Sample command:

```powershell
python -X utf8 Crawler/generated_yaml/MUNI_RESERVE_ANSAN_GO_KR_5D6B8309.py --limit 10 --max-pages 4 --detail-limit 0 --timeout 20
```

- DB collection command:

```powershell
python -X utf8 Crawler/generated_yaml/MUNI_RESERVE_ANSAN_GO_KR_5D6B8309.py --save-db --limit 50 --max-pages 5 --detail-limit 0 --timeout 20
```

- Reports:
  - `logs/municipal_crawler_reports/municipal_yaml_crawler_20260604_222300.yaml`
  - `logs/municipal_crawler_reports/municipal_yaml_crawler_20260604_222329.yaml`
- Result:
  - `50` rows collected.
  - `50` rows saved to DB.
  - `title`, `branch`, `raw_url`, `period`, `schedule_raw`, `fee`, `status`, `target`, and `description` were all filled for the saved sample.
- Quality:
  - `logs/municipal_crawler_quality/municipal_yaml_crawler_20260604_222329_quality.md`
  - Grade `A`, core `100.0%`, important `100.0%`.

## 2026-06-04 Anyang Lifelong Learning Dedicated Crawler

Scope:

- Updated `Crawler/Crawler_AnyangLearning.py` for `ANYANG_LIFELONG_LEARNING`.
- The site uses legacy TLS, so the crawler keeps the existing `curl -k` fallback.
- The integrated course search URL is:
  - `https://learning.anyang.go.kr/ay_network/Lecture_Search/list.asp`

Changes:

- Added pagination support for `Page=N`.
- Added `--max-pages`, `--detail-limit`, and `--stop-after-expired-pages` options.
- Added education-period end-date filtering so already ended courses are skipped during collection.
- Added early stop when expired-only pages repeat.
- Fixed detail fee parsing:
  - During Lifelong Learning Center list details, the crawler now matches the row by course title instead of using the first table row.
  - Manan Lifelong Learning Center detail pages are parsed from label/value rows such as `수강료`, `교육대상`, `강사명`, `교육내용`, and `강의실`.
  - Senior welfare center course details with zero-price list rows are normalized as free when fee text is unavailable.

Validation:

- Sample command:

```powershell
python -X utf8 Crawler/Crawler_AnyangLearning.py --limit 25 --max-pages 5 --detail-limit 25 --timeout 30
```

- DB collection command:

```powershell
python -X utf8 Crawler/Crawler_AnyangLearning.py --save-db --max-pages 0 --detail-limit -1 --timeout 30
```

- Report:
  - `logs/crawler_reports/anyang_learning_20260604_224900.yaml`
- Result:
  - `360` current/future rows collected.
  - `360` rows saved to DB.
  - `39` pages scanned.
  - Detected site last page: `838`.
  - Stopped after expired-only pages.
  - `30` expired rows skipped.
  - `fee`: `360/360`.
  - `target`, `description`, and `venue_name`: `114/360`, currently available from Manan detail pages.

## 2026-06-04 Low Score Crawler Improvements

Scope:

- Improved low-score generated target crawlers for:
  - `DAEJEON_OK_RESERVATION`
  - `GANGSEO_RESERVATION`
  - `MUNI_DYLIB_JNE_GO_KR_0EC67D8E`
  - `MUNI_DYLIB_JNE_GO_KR_1412DDEF`
  - `MUNI_DYLIB_JNE_GO_KR_A2AEEC45`
  - `MUNI_GRLIB_JNE_GO_KR_133262C9`
  - `MUNI_GRLIB_JNE_GO_KR_E6838F98`
  - `MUNI_GSLIB_JNE_GO_KR_80914C01`
  - `MUNI_GYLIB_JNE_GO_KR_15EB3C2E`
  - `MUNI_HNLIB_JNE_GO_KR_3E3E5BCA`
  - `BUSAN_NATIONAL_SCIENCE_MUSEUM`
  - `MUNI_CNLIB_GNE_GO_KR_A3514402`

Changes:

- Added `daejeon_ok_table` collection for Daejeon OK Reservation.
  - Uses the `pubFcltInfoListForm` POST pagination field `pageIdx`.
  - Avoids generic BFS detail-link pollution.
  - Fills title, branch/facility, period, schedule, fee, status, capacity, application URL, and description from the list table.
- Added `gangseo_reservation_table` parsing for Gangseo Reservation.
  - Reads the information-education course table directly.
  - Uses education location as branch/venue.
  - Defaults fee to free because the list does not expose a paid fee field.
- Added `jne_library_lecture_table` for Jeonnam Education Office library `lecture.es` pages.
  - Handles legacy TLS through the existing curl fallback in `fetch_soup`.
  - Handles both normal tables and tables with an extra image column.
  - Fills title, target, period, schedule, apply period, capacity, status, fee, and description.

Validation:

- Daejeon DB collection:

```powershell
python -X utf8 Crawler/Crawler_GeneratedYamlTargets.py --provider DAEJEON_OK_RESERVATION --save-db --per-target-limit 300 --max-pages 30 --detail-limit 0 --timeout 25
```

- Report:
  - `logs/municipal_crawler_reports/municipal_yaml_crawler_20260604_230658.yaml`
  - `300` rows collected.
  - `287` current/future rows saved.
  - Quality: `A`, core `100.0%`, important `83.3%`.

- Gangseo and JNE DB collection:

```powershell
python -X utf8 Crawler/Crawler_GeneratedYamlTargets.py --provider GANGSEO_RESERVATION --provider MUNI_DYLIB_JNE_GO_KR_0EC67D8E --provider MUNI_DYLIB_JNE_GO_KR_1412DDEF --provider MUNI_DYLIB_JNE_GO_KR_A2AEEC45 --provider MUNI_GRLIB_JNE_GO_KR_133262C9 --provider MUNI_GRLIB_JNE_GO_KR_E6838F98 --provider MUNI_GSLIB_JNE_GO_KR_80914C01 --provider MUNI_GYLIB_JNE_GO_KR_15EB3C2E --provider MUNI_HNLIB_JNE_GO_KR_3E3E5BCA --save-db --per-target-limit 50 --max-pages 5 --detail-limit 0 --timeout 25
```

- Reports:
  - `logs/municipal_crawler_reports/municipal_yaml_crawler_20260604_230230.yaml`
  - `logs/municipal_crawler_reports/municipal_yaml_crawler_20260604_230745.yaml`
- Quality:
  - `GANGSEO_RESERVATION`: `A`, `100.0%`.
  - JNE normal table providers: `A`.
  - JNE image-column variants after parser offset fix: `A`.

Config:

- Updated `last_quality`, `crawler_status`, and `parser_assigned` for the 12 providers above.
- Regenerated:
  - `config/generated_yaml_crawler_registry.yaml`
  - `Crawler/generated_yaml/*.py`

## 2026-06-05 GWANGJU_RESERVATION

Scope:

- Implemented and validated `GWANGJU_RESERVATION` as an API-backed generated YAML crawler.
- Parser: `gwangju_booking_api`.

Changes:

- Uses `/reserve/getBookingList.do` pagination instead of generic static HTML parsing.
- Normalizes `eduAddress` into map-friendly branch and venue fields.
- Skips the explicit test row `부도테스트`.
- Maps broad venue values such as `신청한 장소` to `광주광역시청` for city-operated reservation rows.
- Adds fallback addresses for recurring active branches so Google Geocoding can populate map coordinates.
- Clears low-confidence inactive branch coordinates created during validation.

Validation:

```powershell
python -X utf8 Crawler\generated_yaml\GWANGJU_RESERVATION.py --per-target-limit 0 --max-pages 60 --detail-limit 0 --timeout 25 --save-db --mark-stale
```

Result:

- Report: `logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_031727.yaml`
- Quality: `A`
- Collected: `649`
- Saved current/future rows: `11`
- Pages: `55`
- Core fields: `100.0%`
- Important fields: `89.5%`
- Active branches with verified coordinates: `5/5`

Config:

- Updated `config/crawl_targets/public_reservation.yaml`:
  - `collection_type: api_json`
  - `crawler_status: ready`
  - `last_quality.collected: 649`
  - `last_quality.grade: A`
  - `last_quality.parser: gwangju_booking_api`
- Regenerated `config/generated_yaml_crawler_registry.yaml`.

## 2026-06-05 MUNI_WWW_PC_GO_KR_B11A1ACA

Scope:

- Converted the Pyeongchang generated target from a notice-page false positive to the real integrated reservation system.
- Parser: `pc_reservation_facility`.

Changes:

- Updated target URL from `www.pc.go.kr` notice detail to `https://reserve.pc.go.kr/pcreserve/reserve/sport`.
- Added a dedicated parser for:
  - public sports facilities
  - culture facilities
  - training facilities
- Parses list cards and POST detail views.
- Stores facilities as `FACILITY_RESERVATION` rows.
- Uses `시설명 + 주소` for branch identity so duplicated facilities across sport/training sections share one map branch.
- Backfilled active branch coordinates with Google Geocoding.

Validation:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_WWW_PC_GO_KR_B11A1ACA.py --per-target-limit 0 --max-pages 5 --detail-limit 0 --timeout 25 --save-db --mark-stale
```

Result:

- Report: `logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_033540.yaml`
- Quality: `A`
- Collected: `46`
- Saved: `46`
- Active branches: `35`
- Active branch coordinates: `35/35`
- Core fields: `100.0%`
- Important fields: `93.1%`
- Raw fee field: `27/46`

Config:

- Updated `config/crawl_targets/arts_culture.yaml`.
- Updated `config/collected_yaml_crawl_targets.yaml`.
- Regenerated `config/generated_yaml_crawler_registry.yaml`.

## 2026-06-05 MUNI_WWW_IJONGNO_CO_KR_F9ED1CA5

Scope:

- Converted the Jongno Facilities Management Corporation FMCS target from partial static/detail crawling to the FMCS lecture API.
- Parser: `fmcs_lecture_api`.

Changes:

- Added FMCS category discovery to `Crawler/Crawler_MunicipalYaml.py`.
- Uses the Jongno UI-equivalent `전체 + 검색` request first: empty `company_code`, empty `category_cd`, `category_level=9`, `search_type=%`.
- Keeps `center x event category x class category x page` as fallback when the global all query returns no rows.
- Uses site-compatible `search_type=%` and maps row `status` from the API response.
- Applies `max_pages` per query combination instead of using it as a global API page cutoff.
- Dedupes repeated top/child category rows by `provider_course_id`.
- Backfilled branch coordinates with Google Geocoding after DB save.

Validation:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_WWW_IJONGNO_CO_KR_F9ED1CA5.py --per-target-limit 0 --max-pages 20 --detail-limit 80 --timeout 25 --save-db --mark-stale
python -X utf8 tools\maintenance\kakao_geocode_branches.py --provider MUNI_WWW_IJONGNO_CO_KR_F9ED1CA5 --timeout 20 --delay 0.1 --min-confidence 50
```

Result:

- Report: `logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_035932.yaml`
- Quality: `B`
- Collected: `438`
- Saved: `438`
- API list pages: `5`
- Active branches: `3`
- Active branch coordinates: `3/3`
- Core fields: title, branch, schedule, fee, target all `438/438`
- `period` remains empty because the API/detail page does not expose course start/end dates.

Config:

- Updated `config/crawl_targets/sports_facility.yaml`:
  - `collection_type: api_json`
  - `crawler_status: ready`
  - `operator_type: 지자체/공공기관`
  - `last_quality.collected: 438`
  - `last_quality.grade: B`
  - `last_quality.parser: fmcs_lecture_api`
- Regenerated `config/generated_yaml_crawler_registry.yaml`.
- Added `docs/ijongno-fmcs-crawler.md`.

## 2026-06-05 MUNI_WWW_GBELIB_KR_04DB1B82

Scope:

- Added a dedicated parser for the GBELIB Yeongdeok library teach module.
- Parser: `gbelib_library_teach`.

Changes:

- The configured lifelong-learning URL currently has no rows.
- The parser discovers same-library `/yd/module/teach/index.do` application links and collects the active culture-event teach menu.
- Parses list cards from `#list_mode .item`.
- Uses `a.detail-btn` key values to fetch `/yd/module/teach/detail.do`.
- Preserves the actual branch name `경상북도교육청 영덕도서관`; classroom values stay in `venue_name`.
- Backfilled branch coordinates and restored the site footer address as crawler source.

Validation:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_WWW_GBELIB_KR_04DB1B82.py --per-target-limit 0 --max-pages 5 --detail-limit 20 --timeout 25 --save-db --mark-stale
python -X utf8 tools\maintenance\kakao_geocode_branches.py --provider MUNI_WWW_GBELIB_KR_04DB1B82 --timeout 20 --delay 0.1 --min-confidence 50
```

Result:

- Report: `logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_040931.yaml`
- Quality: `B`
- Collected: `2`
- Saved: `2`
- Active branches: `1`
- Active branch coordinates: `1/1`
- Core fields: title, branch, period, schedule, target, description all `2/2`
- Fee: `0/2`; no fee field is exposed by the site.

Config:

- Updated `config/crawl_targets/library.yaml`:
  - `crawler_status: ready`
  - `last_quality.collected: 2`
  - `last_quality.grade: B`
  - `last_quality.parser: gbelib_library_teach`
- Regenerated `config/generated_yaml_crawler_registry.yaml`.
- Added `docs/gbelib-library-teach-crawler.md`.

## 2026-06-05 NATIONAL_ECOLOGY_CENTER

Scope:

- Refreshed the dedicated National Institute of Ecology reservation education parser.
- Parser: `national_ecology_cards`.

Changes:

- Collects the reservation-service education list at `nieResve/pgm/eclgyEdc/list2.do?menuNo=600010`.
- Follows `fnProgrmView('E...')` detail pages.
- Normalizes detail-page fee values so calendar UI labels such as `관리` are not saved as tuition.
- Treats repeated `0원` reservation fee rows as `무료`.
- Backfilled branch address and coordinates with Google Geocoding.

Validation:

```powershell
python -X utf8 Crawler\generated_yaml\NATIONAL_ECOLOGY_CENTER.py --per-target-limit 0 --max-pages 10 --detail-limit 80 --timeout 25 --save-db --mark-stale
```

Result:

- Report: `logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_041902.yaml`
- Quality: `B`
- Collected: `60`
- Saved: `23`
- Active branches: `1`
- Active branch coordinates: `1/1`
- Core fields: title, branch, period, schedule, target, raw URL all `60/60`
- Detail-enriched fields: description `20/60`, fee `18/60`

Config:

- Updated `config/crawl_targets/arboretum_ecology.yaml`:
  - `crawler_status: ready`
  - `last_quality.collected: 60`
  - `last_quality.saved: 23`
  - `last_quality.grade: B`
  - `last_quality.parser: national_ecology_cards`
- Regenerated `config/generated_yaml_crawler_registry.yaml`.
- Added `docs/national-ecology-center-crawler.md`.

## 2026-06-05 ICHEON_WORKER_WELFARE

Scope:

- Added a dedicated parser for Icheon Worker Welfare Center lifelong-learning courses.
- Parser: `icheon_worker_welfare_table+detail`.

Changes:

- Redirects collection from the configured guide page to the real course list at `/program/programInfoList.do`.
- Parses the paginated course table and follows `/program/programInfoDetail.do?prgm_seq=...` detail pages.
- Stops at the site-reported final page from `현재페이지 : n/m` so repeated last-page rows are not collected.
- Preserves branch as `이천시노동자복지관`; classroom names stay in `venue_name`.
- Cleans title time-slot prefixes and removes the trailing `상세보기` label from instructor names.
- Extracts target hints from `일반모집대상` and `최소연령제한` in the detail description.
- Backfilled branch coordinates using the existing geocoded address.

Validation:

```powershell
python -X utf8 Crawler\generated_yaml\ICHEON_WORKER_WELFARE.py --per-target-limit 0 --max-pages 30 --detail-limit 500 --timeout 25 --save-db --mark-stale
```

Result:

- Report: `logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_043718.yaml`
- Quality: `A`
- Collected: `444`
- Saved: `17`
- Pages: `23`
- Detail pages: `444`
- Active branches: `1`
- Active branch coordinates: `1/1`
- Core fields: title, branch, address, venue, status, fee, schedule, period, target, description all `444/444`

Config:

- Updated `config/crawl_targets/welfare.yaml`:
  - `crawler_status: ready`
  - `last_quality.collected: 444`
  - `last_quality.saved: 17`
  - `last_quality.grade: A`
  - `last_quality.parser: icheon_worker_welfare_table+detail`
- Updated `config/collected_yaml_crawl_targets.yaml` with the same status and quality values.
- Regenerated `config/generated_yaml_crawler_registry.yaml`.
- Added `docs/icheon-worker-welfare-crawler.md`.

## 2026-06-05 MUNI_WWW_JINJU_GO_KR_CC4D7F07

Scope:

- Added a dedicated parser for Jinju City toybank reservation courses.
- Parser: `jinju_toybank_branch_list`.

Changes:

- Stops using the Changwon shared `.cp31edu1list1` parser for Jinju.
- Discovers branch-specific `전체강좌` links from the left menu.
- Excludes the top-level aggregate `전체강좌` page to avoid duplicated course rows.
- Splits rows into `무지개동산 장난감은행`, `은하수동산 장난감은행`, `충무공동 장난감은행`, and `천전동 장난감은행`.
- Extracts title, status, category, target, period, schedule, fee, capacity/current/waitlist, application method, description, and image URL from the list cards.
- Uses `--mark-stale` to deactivate the previous single-branch rows under `경상남도 진주시`.

Validation:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_WWW_JINJU_GO_KR_CC4D7F07.py --per-target-limit 0 --max-pages 20 --detail-limit 0 --timeout 25 --save-db --mark-stale
```

Result:

- Report: `logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_045434.yaml`
- Quality: `B`
- Collected: `119`
- Saved: `25`
- Pages: `15`
- Active branches: `4`
- Inactive stale single-branch rows: `50`
- Duplicate provider course IDs: `0`
- Core fields: title, branch, period, schedule, target, fee, status, description all `119/119`
- Address: `0/119`; the site does not expose branch-specific addresses on these pages.
- Google Geocoding with branch names alone returned low-confidence or duplicated locations, so branch coordinates remain a manual address-fix task.

Config:

- Updated `config/crawl_targets/public_reservation.yaml`:
  - `crawler_status: ready`
  - `last_quality.collected: 119`
  - `last_quality.saved: 25`
  - `last_quality.grade: B`
  - `last_quality.parser: jinju_toybank_branch_list`
- Updated `config/collected_yaml_crawl_targets.yaml` with the same status and quality values.
- Regenerated `config/generated_yaml_crawler_registry.yaml`.
- Added `docs/jinju-toybank-crawler.md`.

## 2026-06-05 SEJONG_LIFELONG_EDU

Scope:

- Added a dedicated API parser for 세종특별자치시교육청 평생교육원.
- Parser: `sejong_lifelong_program_api`.

Changes:

- Replaced the previous root-page generic parser with the real program list URL:
  `https://life.sje.go.kr/edu/community/events/program-list`.
- Uses `homepageprogramlist`, `homepageprogramdetail`, and `getCodeList` APIs with `manage_code=150018`.
- Collects active, closed, and waiting rows with status queries `1and2and6`, `3`, and `5`.
- Skips programs whose end date is already older than the crawler run date.
- Preserves the institution as the branch and stores classroom/facility as `venue_name`.
- Adds the known institution address so the branch can be geocoded and shown on the map.

Validation:

```powershell
python -X utf8 Crawler\generated_yaml\SEJONG_LIFELONG_EDU.py --per-target-limit 0 --max-pages 5 --detail-limit 100 --timeout 25 --save-db --mark-stale
python -X utf8 tools\maintenance\kakao_geocode_branches.py --provider SEJONG_LIFELONG_EDU --timeout 20 --delay 0.1 --min-confidence 50
```

Result:

- Report: `logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_062800.yaml`
- Quality: `A`
- Collected: `87`
- Saved: `87`
- Pages: `3`
- Detail pages: `87`
- Active branch coordinates: confidence `100`, `36.5348157, 127.2637839`
- Core fields: title, branch, raw_url, period, fee, status, target all `87/87`
- Schedule: `86/87`
- Description: `68/87`

Config:

- Updated `config/crawl_targets/public_reservation.yaml`:
  - `url: https://life.sje.go.kr/edu/community/events/program-list`
  - `collection_category: 평생학습`
  - `crawler_status: ready`
  - `last_quality.grade: A`
- Updated `config/collected_yaml_crawl_targets.yaml` with the same status and quality values.
- Regenerated `config/generated_yaml_crawler_registry.yaml`.
- Added `docs/sejong-lifelong-edu-crawler.md`.

## 2026-06-05 MUNI_YSSPORTS_YONG_SAN_OR_KR_67C8F87B

Scope:

- Added a dedicated parser for the Yong-san FMCS sports reservation site.
- Parser: `yssports_fmcs_category_api`.

Changes:

- Normalizes the old candidate URL `https://yssports.yong-san.or.kr/www/50` to the real lecture URL:
  `https://yssports.yong-san.or.kr/fmcs/8?center=YGSN01`.
- Cycles `rest/common/company` and `rest/common/category` to capture the site's center/category structure.
- Uses the site's legacy `rest/lecture/list` response because the documented FMCS `company_code/category_cd` parameters return empty rows for this deployment.
- Splits map branches by the API `comnm` field and adds known branch addresses for map geocoding.
- Extends the shared FMCS detail parser to read `proc_read dl` fields and `수강기간`.

Validation:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_YSSPORTS_YONG_SAN_OR_KR_67C8F87B.py --per-target-limit 10 --max-pages 2 --detail-limit 10 --timeout 30
python -X utf8 tools\report_municipal_crawler_quality.py --report logs\municipal_crawler_reports\municipal_yaml_crawler_20260605_110101.yaml --limit 15
```

Result:

- Report: `logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_110101.yaml`
- Quality: `A`
- Collected: `10`
- Pages: `2`
- Detail pages: `10`
- Core fields: `100.0%`
- Important fields: `83.3%`
- Period, schedule, fee, status, and target: `10/10`
- Description: `0/10`; sampled detail pages do not expose a course introduction body.

Config:

- Updated `config/crawl_targets/sports_facility.yaml`:
  - `url: https://yssports.yong-san.or.kr/fmcs/8?center=YGSN01`
  - `collection_type: api`
  - `crawler_status: ready`
  - `last_quality.grade: A`
- Updated `config/collected_yaml_crawl_targets.yaml` and `config/generated_yaml_crawler_registry.yaml` with the same ready URL/status.
- Added `docs/yssports-fmcs-crawler.md`.

## 2026-06-05 MUNI_WWW_BUK_DAEGU_KR_RESERVATION

Scope:

- Renamed the old active provider `DAEGU_BUKGU_RESERVATION` to `MUNI_WWW_BUK_DAEGU_KR_RESERVATION`.
- Added a dedicated parser for Bukgu Daegu reservation pages.
- Parser: `bukgu_daegu_lec_list+detail`.

Changes:

- Collects four Bukgu reservation menus: lifelong learning, information education, foreign-language education, and resident-center courses.
- Follows detail pages for course body fields instead of relying on the guide page.
- Splits branch/venue fields from detail labels and only maps trusted Bukgu office venues to a known address.
- Removes the old generated wrapper from `Crawler/generated_yaml/`.

Validation:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_WWW_BUK_DAEGU_KR_RESERVATION.py --per-target-limit 20 --max-pages 2 --detail-limit 20 --timeout 30
python -X utf8 Crawler\generated_yaml\MUNI_WWW_BUK_DAEGU_KR_RESERVATION.py --per-target-limit 0 --max-pages 5 --detail-limit 120 --timeout 30
python -X utf8 tools\report_municipal_crawler_quality.py --report logs\municipal_crawler_reports\municipal_yaml_crawler_20260605_112454.yaml --limit 10
```

Result:

- Report: `logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_112454.yaml`
- Quality: `A`
- Collected: `143`
- Pages: `16`
- Detail pages: `120`
- Core fields: `100.0%`
- Important fields: `91.8%`
- Title, branch, raw_url, period, status, target, description: `143/143`
- Schedule: `120/143`
- Fee: `96/143`

Config:

- Updated `config/crawl_targets/public_reservation.yaml`.
- Updated `config/collected_yaml_crawl_targets.yaml`.
- Updated `config/generated_yaml_crawler_registry.yaml`.
- Updated `config/public_course_targets.yaml`.
- Added `docs/bukgu-daegu-reservation-crawler.md`.

## 2026-06-05 MUNI_WWW_UIWANG_GO_KR_F89FBD11

Scope:

- Routed the direct Uiwang `eduList.do` provider through the existing Uiwang category crawler.
- Parser: `uiwang_reserve_category_cards`.

Changes:

- `MUNI_WWW_UIWANG_GO_KR_F89FBD11` now starts from the integrated-reservation main page logic and cycles the visible left category links.
- Reuses the same resident-center, lifelong-learning, library, youth, education, event, exhibition, and experience category discovery used by `MUNI_WWW_UIWANG_GO_KR_2A9DF9A4`.
- Updated target status from `partial` to `ready`.

Validation:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_WWW_UIWANG_GO_KR_F89FBD11.py --per-target-limit 20 --max-pages 1 --detail-limit 0 --timeout 30
python -X utf8 Crawler\generated_yaml\MUNI_WWW_UIWANG_GO_KR_F89FBD11.py --per-target-limit 0 --max-pages 1 --detail-limit 0 --timeout 30
python -X utf8 tools\report_municipal_crawler_quality.py --report logs\municipal_crawler_reports\municipal_yaml_crawler_20260605_112901.yaml --limit 10
```

Result:

- Report: `logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_112901.yaml`
- Quality: `A`
- Collected: `198`
- Pages: `27`
- Core fields: `100.0%`
- Important fields: `95.0%`
- Title, branch, raw_url, fee, status, description: `198/198`
- Period, schedule, target: `178/198`

Config:

- Updated `config/crawl_targets/public_reservation.yaml`.
- Updated `config/collected_yaml_crawl_targets.yaml`.
- Updated `config/generated_yaml_crawler_registry.yaml`.
- Updated `docs/uiwang-reserve-category-crawler.md`.

## 2026-06-05 MUNI_PAJU_PCY_OR_KR_412053A6

Scope:

- Added a dedicated parser for the Paju City Youth Foundation FMCS reservation site.
- Parser: `paju_pcy_fmcs_category_api`.

Changes:

- Cycles `rest/common/company` centers and each center's `rest/common/category` tree.
- Uses `rest/lecture/list` with `company_code`, `category_cd`, `category_level`, and `search_type=%`.
- Avoids the misleading URL `event/class` parameters because they do not apply the actual API filter on this site.
- Follows detail pages to fill description and product duration; product duration such as `1개월` is stored in `period`.
- Updated target status from `partial` to `ready`.

Validation:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_PAJU_PCY_OR_KR_412053A6.py --per-target-limit 20 --max-pages 2 --detail-limit 20 --timeout 30
python -X utf8 Crawler\generated_yaml\MUNI_PAJU_PCY_OR_KR_412053A6.py --per-target-limit 0 --max-pages 2 --detail-limit 250 --timeout 30
python -X utf8 tools\report_municipal_crawler_quality.py --report logs\municipal_crawler_reports\municipal_yaml_crawler_20260605_114250.yaml --limit 10
```

Result:

- Report: `logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_114250.yaml`
- Quality: `A`
- Collected: `160`
- Pages: `45`
- Detail pages: `160`
- Core fields: `100.0%`
- Important fields: `95.0%`
- Title, branch, raw_url, period, schedule, fee, status, target: `160/160`
- Description: `112/160`

Config:

- Updated `config/crawl_targets/sports_facility.yaml`.
- Updated `config/collected_yaml_crawl_targets.yaml`.
- Updated `config/generated_yaml_crawler_registry.yaml`.
- Added `docs/paju-pcy-fmcs-crawler.md`.

## 2026-06-05 MUNI_WWW_YP21_GO_KR_EA0D7B81

Scope:

- Added a dedicated parser for the Yangpyeong pool FMCS reservation site.
- Parser: `yp21_fmcs_category_api`.

Changes:

- Uses POST-only FMCS endpoints under `https://www.yp21.go.kr/pool/`.
- Cycles `rest/common/company` and `rest/common/category`.
- Calls `rest/lecture/list` with `company_code`, `category_cd`, `category_level`, and `search_type=%`.
- Follows detail pages to fill product duration such as `3개월` into `period`.
- Adds target fallback from title for free-swim and gender-specific rows where the API omits `target_age_name`.
- Updated target status from `partial` to `ready`.

Validation:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_WWW_YP21_GO_KR_EA0D7B81.py --per-target-limit 20 --max-pages 2 --detail-limit 20 --timeout 30
python -X utf8 Crawler\generated_yaml\MUNI_WWW_YP21_GO_KR_EA0D7B81.py --per-target-limit 0 --max-pages 2 --detail-limit 100 --timeout 30
python -X utf8 tools\report_municipal_crawler_quality.py --report logs\municipal_crawler_reports\municipal_yaml_crawler_20260605_114813.yaml --limit 10
```

Result:

- Report: `logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_114813.yaml`
- Quality: `A`
- Collected: `58`
- Pages: `7`
- Detail pages: `58`
- Core fields: `100.0%`
- Important fields: `83.3%`
- Title, branch, raw_url, period, schedule, fee, status, target: `58/58`
- Description: `0/58`; detail pages do not expose a course introduction body.

Config:

- Updated `config/crawl_targets/sports_facility.yaml`.
- Updated `config/collected_yaml_crawl_targets.yaml`.
- Updated `config/generated_yaml_crawler_registry.yaml`.
- Added `docs/yp21-fmcs-crawler.md`.

## 2026-06-05 MUNI_WWW_PTLIB_GO_KR_D9537B1F

Scope:

- Added a dedicated parser for Pyeongtaek City Library cultural event pages.
- Parser: `ptlib_lecture_list`.

Changes:

- Cycles `manageCd` library branches from the list page.
- Parses `lectureIdx` from `fnDetail('...')` and follows `lectureDetail.do`.
- Fills recruitment period, education period, schedule, venue, target, capacity, and status from detail tables.
- Skips expired courses whose education period has already ended.
- Updated target status from `partial` to `ready`.

Validation:

```powershell
python -X utf8 Crawler\generated_yaml\MUNI_WWW_PTLIB_GO_KR_D9537B1F.py --per-target-limit 20 --max-pages 2 --detail-limit 20 --timeout 30
python -X utf8 Crawler\generated_yaml\MUNI_WWW_PTLIB_GO_KR_D9537B1F.py --per-target-limit 0 --max-pages 5 --detail-limit 200 --timeout 30
python -X utf8 tools\report_municipal_crawler_quality.py --report logs\municipal_crawler_reports\municipal_yaml_crawler_20260605_115510.yaml --limit 10
```

Result:

- Report: `logs/municipal_crawler_reports/municipal_yaml_crawler_20260605_115510.yaml`
- Quality: `A`
- Collected: `32`
- Pages: `21`
- Detail pages: `32`
- Core fields: `100.0%`
- Important fields: `66.7%`
- Title, branch, raw_url, period, schedule, status, target: `32/32`
- Fee and description: `0/32`; detail pages do not expose those fields.

Config:

- Updated `config/crawl_targets/library.yaml`.
- Updated `config/collected_yaml_crawl_targets.yaml`.
- Updated `config/generated_yaml_crawler_registry.yaml`.
- Added `docs/ptlib-library-crawler.md`.
# 2026-06-05 - JPYOUTH Program Parser

- Implemented `MUNI_WWW_JPYOUTH_CO_KR_5E838FBF` with `jpyouth_table+detail`.
- The parser cycles `sub.php?menukey=54&mode=list&page=N` and follows each detail page.
- It separates the combined list schedule into `period` and `schedule_raw`, fills branch/address metadata for `증평군청소년수련관`, and extracts fee from detail text.
- Test result: 202 rows collected and saved, 14 pages visited, 202 detail requests.
- Registry command now uses `--per-target-limit 0 --max-pages 14 --detail-limit 250`.
- Detailed notes: `docs/jpyouth-program-crawler.md`.

# 2026-06-05 - GWE Library Lecture Event Detail Parser

- Upgraded `MUNI_LIB_GWE_GO_KR_303FFE72` to `gwe_library_lecture_event+detail`.
- The parser now derives `/lecture-event/list/all` from the source menu id instead of selecting an arbitrary `lecture-event` link.
- It follows each detail page and fills address, venue, period, schedule, target, capacity, material fields, description, and image.
- Test result: 4 rows collected and saved, 2 pages visited, 4 detail pages fetched.
- Registry command now uses `--max-pages 3 --detail-limit 20`.
- Detailed notes: `docs/gwe-library-lecture-event-crawler.md`.

# 2026-06-05 - Dobong Facilities Lecture Branch Cycle

- Implemented branch cycling for `MUNI_YEYAK_DOBONGSISEOL_OR_KR_0A2D506D`.
- The parser now starts at `guide.php`, extracts each facility's `수강신청` link, and cycles through facility/category/page URLs.
- It follows `goLink(...)` parameters with `lecture_iview.php` detail POST requests to fill period, schedule, fee, room, and description.
- Test result: 445 rows collected and saved, 78 pages visited, 707 detail requests, parser `dobongsiseol_lecture_table`.
- Registry command now uses `--max-pages 120 --detail-limit 1000`.
- Detailed notes: `docs/dobongsiseol-lecture-crawler.md`.

# 2026-06-05 - Michuhol Education Apply Parser

- Implemented `MUNI_WWW_MICHUHOL_GO_KR_29D0C0F5` with `michuhol_education_apply_table`.
- The parser follows `education_apply/list.do?page=...` and loads each `step1.do?sq=...` detail page.
- It separates application period from course period and fills schedule, room, fee, material fee, phone, description, and application URL.
- Test result: 284 rows collected, 40 saved after expired-course filtering, 284 detail pages fetched.
- Registry command now uses `--max-pages 10 --detail-limit 500`.
- Detailed notes: `docs/michuhol-education-apply-crawler.md`.

# 2026-06-05 - ICE Library Teach Detail Parser

- Upgraded `MUNI_LIB_ICE_GO_KR_019A3D01` from the basic `ice_library_teach` parser to `ice_library_teach_detail`.
- The parser now follows each `detail.do` page using `group_idx`, `category_idx`, and `teach_idx`.
- It fills branch name, branch code, address, venue address, venue name, room, target, capacity, instructor, application URL, and detailed description.
- Test result: 20 rows collected, 16 saved after expired-course filtering, 20 detail pages fetched.
- Detailed notes: `docs/ice-library-teach-crawler.md`.

# 2026-06-05 - Busan Lifelong Learning Office Cycle

- Implemented `MUNI_LLL_BUSAN_GO_KR_944C621B` as a branch-cycling crawler.
- The crawler reads office codes from `officeList.do` and requests `learningList.do?inst_id=...` for each office.
- Test result: 34 offices, 740 rows, parser `busan_lifelong_office_cycle`, 100% filled for title, branch, period, schedule, fee, and description.
- Registry command now uses `--max-pages 5` so the daily run covers multi-page office results.
- Detailed notes: `docs/busan-lifelong-office-cycle-crawler.md`.
