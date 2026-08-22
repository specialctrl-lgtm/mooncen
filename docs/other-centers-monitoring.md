# Other Centers Monitoring

Ops Console has a separate `Other Centers` tab for generated YAML crawlers.

## What It Shows

The main `Monitoring` tab is also organized as a compact dashboard:

- `Monitor Summary`: front, backend, DB, AI, crawler failures, CPU, memory, network, provider count, and check time
- `Service Health`: service-level status table for front, backend, DB, AI, crawler, nginx, and cloudflared
- `Crawler Monitor`: daily crawler progress, latest report, provider result counts, and DB provider table
- `Today's Collections`: provider-level course collection counts for the current Seoul date
- `Server Resources`: CPU, memory, network, and recent sampled history

The `Other Centers` tab focuses on generated YAML crawlers:

- `YAML targets`: generated non-culture-center providers selected by `Crawler/Crawler_GeneratedYamlTargets.py`
- `Other Target URL Manager`: add a new URL as a generated YAML target, update an existing target URL/status/category, or delete a target by moving it to `config/crawl_targets/deprecated.yaml`
- `Application URL Discovery`: run `tools/discover_application_urls.py` against weak, empty, info-only, or notice/article targets to find better application/reservation URL candidates. Culture-center providers are excluded from the default batch; use `--include-culture` only when intentionally checking them. Candidates are shown in Ops Console and can be copied into the URL manager with the `Use` button; they are not applied automatically.
- `Providers with DB data`: providers that already saved rows to the local development DB
- `DB quality score`: weighted fill score for `title`, `branch`, `raw_url`, `status`, `period`, `schedule_raw`, `description`, `target`, and `fee`
- Extra reservation fields are also tracked as fill rates: application period, application URL, program type, venue, capacity, eligibility text, and preserved raw fields.
- `Latest Report Quality`: provider-level quality from the newest `logs/municipal_crawler_reports/municipal_yaml_crawler_*.yaml`; click the `Saved` count to inspect collected course rows for that provider.
- Generated crawler reports also include reservation-link discovery counts: `reservation_discovery_links` and `reservation_fallback_pages`.
- `DB Quality by Provider`: local DB fill rates grouped by provider and collection category
- `URL`: source page link for each provider in the latest report and DB provider quality tables

The `Operations` tab is grouped by task:

- `Diagnostics`: summary, health, and doctor checks
- `Deploy`: app deploy, deploy with workers, and full deploy
- `Service Control`: restart all, restart selected service, and service logs
- `Data Maintenance`: missing location update, ended-course cleanup, address fix, and other-center monitor shortcuts.
  Address fix is embedded in the Operations tab; `All address rows` loads every branch address row, while crawler `All provider branches` means running the selected provider without a branch filter.
- `Crawler Operations`: run the scheduled crawler once or run a selected provider/branch.
  Culture-center providers are shown separately from generated/other providers, and the other-provider list has a search box for quickly finding municipal, library, welfare, sports, museum, and similar targets.
- `AI Operations`: Ollama connection test, AI quality report, worker start/stop/status, and AI reset jobs

Crawler lifecycle rules:

- Crawlers skip saving rows when `end_date` is before the current date.
- Each crawler cycle automatically deactivates courses whose `end_date` was at least 7 days ago.
- The Operations tab also has `Cleanup ended courses`, which runs the same 7-day cleanup manually.
- Cleanup marks rows inactive with `is_active = false` and sets `removed_at`; it does not hard-delete course rows.

## Reservation-Oriented Fields

Generated public-facility crawlers still save into `courses`, but these sites often expose reservation details that culture centers do not. The DB therefore keeps these optional columns:

| Column | Meaning |
| --- | --- |
| `apply_period_raw` | Original application or reservation period text |
| `schedule_dates` | Actual class dates from calendar-style pages, stored as a JSON date array |
| `capacity_total` | Total capacity when the page exposes it |
| `capacity_current` | Current applied or registered count |
| `capacity_remaining` | Remaining seats |
| `waitlist_total` | Waitlist capacity or count |
| `venue_name` | Venue, room, or place name |
| `venue_address` | Venue-specific address when different from the branch address |
| `application_url` | Direct application/reservation URL when detectable |
| `application_type` | Application classification. Default to `OFFLINE_APPLY` when structured course data exists and there is no clear online-reservation evidence. Use `ONLINE_RESERVATION` only when the page exposes an application URL plus online/application wording. `INFO_ONLY` and `EXTERNAL_NOTICE` mean crawler review is needed, not a normal target category. |
| `application_method_raw` | Original text describing phone, visit, email, or other application method |
| `reservation_available` | Whether an online reservation URL was detected |
| `discovery_status` | Short parser/discovery reason such as `application_url_found` or `course_info_without_application_url` |
| `program_type` | Broad activity type such as education, experience, tour, sport, exhibition, or performance |
| `eligibility_raw` | Original eligibility/target text |
| `raw_fields` | Publicly visible source fields preserved as JSON for parser debugging |

`raw_fields` must not contain secrets or request credentials. It is intended only for public page labels and values.

Calendar-style pages should save the visible class dates in `schedule_dates` and still populate `start_date` / `end_date` with the first and last class date. Date filtering should prefer exact `schedule_dates` matches; the range fields are fallback values for sites that only expose a continuous period.

Application classification is intentionally separate from course collection success. Most generated public/institution targets are offline programs. If a crawler finds structured course data but cannot prove online reservation, save it as `OFFLINE_APPLY`.

`INFO_ONLY` and `EXTERNAL_NOTICE` should be treated as parser/discovery issues. They usually mean the crawler landed on an announcement, board, or guide page instead of the actual application/course list. Ops Console groups these values under crawler review.

Existing generated-course rows can be normalized with:

```bash
python -X utf8 DB/backfill_other_contents_application_type.py
```

URL discovery order:

1. Same-domain internal links and homepage/menu links.
2. Same-domain `sitemap.xml` links.
3. Optional Google Custom Search API when `GOOGLE_SEARCH_API_KEY` and `GOOGLE_SEARCH_CX` are configured and the discovery job is run with Google enabled.

Discovery results are saved under `logs/url_discovery_reports/url_discovery_*.yaml`.
The report distinguishes high-scoring URL candidates from `parse-ready` candidates. `parse-ready` means the current generated parser can already extract rows with a period, schedule, or application URL; candidates without that marker usually need a provider-specific parser before being used for production collection.

Provider-specific parser notes:

- `MUNI_WWW_SEOGU_GO_KR_A27782FE` redirects from the Wolpyeong Library homepage to the `행사 및 강좌 신청` table, reconstructs detail URLs from `fn_egov_select`, and fills the map branch/address as `월평도서관`.
- `MUNI_WWW_YEONSU_GO_KR_B2B6DF58` parses Yeonsu Culture Portal Friday Art performance cards and follows detail pages. It fills title, branch/venue `연수아트홀`, performance date/time, reservation period, remaining seats/status, free fee, poster image, detail description, and reservation URL when available.
- `MUNI_WWW_UIWANG_GO_KR_2A9DF9A4` starts from the Uiwang integrated-reservation main page and discovers all visible `eduList.do` category links. It iterates resident-center, lifelong-learning, library, youth, education, event, exhibition, and experience categories, then parses `ul.blog.reserv` cards and reconstructs detail URLs from `fnView('RESR_...')`.
- `GYEONGGI_GSEEK` uses the GSEEK offline-learning AJAX endpoint `/user/course/offline/list/search`. It collects only offline courses for map-oriented data, maps institutions from `d_edu_gvmnfc`, and fills fee, material fee, period, weekday/time, target, capacity, instructor, description, image, category, and tags directly from the JSON response.
- `SEOUL_LIFELONG_LEARNING` parses `오프라인학습 > 자치구평생학습` as a branch directory. It saves the 25 Seoul district lifelong-learning portal links as offline/check-needed branch-level rows; detailed course rows require district-specific parsers.
- `MUNI_RESERVE_ANSAN_GO_KR_02253999` uses the Ansan integrated reservation `체험·견학` lists. The parser iterates `실내체험`, `실외체험`, and `견학`, reconstructs detail URLs from `fnView('RESR_...')`, and classifies program type as `체험` or `견학`.
- `MUNI_RESERVE_ANSAN_GO_KR_8236CAF0` uses the Ansan integrated reservation education list rather than a single detail URL. The parser iterates the `교육·강좌` categories `외국어`, `정보화`, `음악`, `미술`, `체육`, `과학`, and `기타`, reconstructs detail URLs from `fnView('RESR_...')`, and keeps map grouping branch-oriented by deriving branch codes from the visible institution/department.
- `BUSAN_NATIONAL_SCIENCE_MUSEUM` uses the public `IndivCurriMgr` JSON endpoint instead of the empty rendered list shell. The parser skips notice-only rows, keeps the detail URL as the application URL, and backfills target text from age or school-grade hints in the title when the API does not expose a target field.
- `HONAM_BIOLOGICAL_RESOURCES` uses `front/edu/eduFrontList.do` cards and follows `fn_detail('EDU_...')` to the detail page through `index.do?menu_link=front/edu/eduFrontDetail.do`.
- `HAMAN_WELFARE_LIFELONG_COURSE` parses the Haman integrated-reservation education pages instead of the 10-row main JSON widget. It iterates the left menu's agency pages (`AGENCY001`, `AGENCY024`, `AGENCY003`, `AGENCY005`, `AGENCY006`, and the literature-center page), paginates with `cpage`, follows `.edu1view` detail pages, skips expired course periods, and stores the visible education institution as the branch.
- `NATIONAL_ECOLOGY_CENTER` uses the reservation-service education list `nieResve/pgm/eclgyEdc/list2.do` and follows `fnProgrmView('E...')` detail pages.
- `NATIONAL_INTANGIBLE_HERITAGE_CENTER` parses the NIH 9is education board cards (`ul.type-thumb > li`) and detail `.infoBox`/`.view-con` fields. It separates free tuition from material fees, keeps the fixed Jeonju venue address for mapping, and skips expired education periods. The target remains `blocked` because `robots.txt` disallows `/`.
- `NATIONAL_OCEAN_SCIENCE_MUSEUM` uses `kosm/bbs/boardAjax.do?bbsId=BSD0008&type=r` JSON. The parser maps reservation dates, schedules, target, capacity, and detail/image URLs from that response.
- `NATIONAL_PARK_RESERVATION` uses the KNPS trail program POST list and maps program title, branch, period, target, capacity, application method, and detail description.
- `NATIONAL_SCIENCE_MUSEUM` uses the national science museum education-program table and follows detail pages for descriptions.
- `GYEONGSAN_LIFELONG` style targets under `gbgs.go.kr/lll/page/` parse the `ul.content_list` card layout directly so title, period, schedule, fee, room, and description are not mixed into the title field.
- `ESONGPA` lecture targets use the `/data/getLectureList` JSON endpoint with the page CSRF token because the static list page renders course rows through AJAX.
- `BUSAN_RESERVE` lecture targets under `reserve.busan.go.kr/lctre/list` parse `ul.reserveList` cards and follow `fn_viewProgrm(group, program)` detail pages. The `busan_reserve_list+detail` parser uses detail `dl` pairs to fill fee, weekday/time, operation period, application period, branch, target, and description.
- `GWE_LIBRARY` targets under `lib.gwe.go.kr` follow the internal `lecture-event/list` application URL when the YAML points to a homepage/search page, then parse `.lecture_item` cards.
- `GEOJE_LIBRARY` uses the `강좌 신청하기` page `/culture/d030200.do` instead of the guide page. Detail pages redirect to login, so the parser uses the visible course table for title, branch, target, capacity, period, apply period, status, and free-fee policy.
- `YJLIB` targets under `yjlib.go.kr` parse `.article-item` library lecture cards and follow `lectureDetail.do?lectureIdx=...` detail pages. The parser normalizes short library labels such as `여주` to map-ready branch names such as `여주도서관`, and fills weekday/time, instructor, target, material notes, description, and image URLs from the detail page.
- `CNALL_LECTURE` parses the Chungnam Office of Education lifelong-learning portal. It reads `/organ/organList.do` first so every library/education office is saved as a separate branch with stable `organ_*` branch codes, address, phone, and homepage. It then parses `.lecture-list > li` cards, follows `lectureDetail.do?lectureIdx=...`, and fills period, schedule, fee, target, capacity, description, application URL, and branch address.
- `MUNI_WWW_NAMWON_GO_KR_37D4EA88` parses Namwon's `items.do` JSON reservation API across lifelong learning, education, performance, experience, museum, lodging, camping, sports rental, and support menus. It keeps map branches at institution/external-venue level and stores internal rooms or camping site types in `venue_name` so map markers are not split into room-level branches.
- `GANGSEO_RESERVATION` redirects the information page `/reserve/re010200` to the actual course application list `/reserve/re010202` before parsing.
- `DAEGU_NATIONAL_SCIENCE_MUSEUM` requires browser-fingerprint cookies before the reservation list is returned. The parser sets those cookies and reads the `rsv-list` reservation cards.
- `DAEGU_RESERVATION` uses the public `yeyak.daegu.go.kr` experience/tour JSON APIs. It splits rows by `instNm` as map branches, follows the detail API for address, venue, description, image, target, fee, status, and schedule hints, and dedupes repeated `(instId, ftrPrgrmId)` rows. The lecture API is not used yet because direct server calls are blocked by the site's NetFUNNEL browser gate. See `docs/daegu-reservation-crawler.md`.
- `MUNI_WWW_DGS_GO_KR_566C09FF` parses the three approved Daegu Seo-gu reservation lists: information education, resident center courses, and lifelong-learning center courses. It handles direct GET detail pages for `edu/lifelong`, POST detail pages for `lctrCntr`, separates resident-center branches for map display, fills official dong addresses where available, normalizes Korean fee units, and keeps explicit `상시` / `운영시간 미표기` fallbacks for malformed schedule rows.
- `GWACHEON_NATIONAL_SCIENCE_MUSEUM` uses the `scipia/schedules` schedule tables. This avoids saving Vue template placeholders from the legacy education page.
- `NATIONAL_MUSEUM_OF_KOREA` uses the MODU education platform `/learn` instead of the static museum homepage. It iterates the 14 museum filters, replaces scroll loading with `/learn/append`, follows detail pages, and stores each museum as a separate map branch.
- `GWANGJU_NATIONAL_SCIENCE_MUSEUM` still uses the generic card/detail flow, but the shared title selector ignores low-value labels such as `상세보기`, `스킵네비게이션`, and menu titles so detail page titles can replace link labels.
- `DAEJEON_OK_RESERVATION` parses the `facil-list` cards and reconstructs detail/application URLs from the `goDtl(...)` JavaScript parameters.
- `GWANGJU_RESERVATION` is redirected to `reserve/bookingList.do?pageId=reserve1&searchCate1=A` before collection so it uses the booking API instead of the notice-heavy landing page.
- `GBE_EQ` targets under `gbe.kr/uj/eq/view/selectEqList.do` parse the education table directly and split application period, course period, schedule, capacity, method, and status.
- `CHANGWON/JINJU_BOOKING` targets using `.cp31edu1list1` parse the reservation card list directly. The parser handles both Changwon and Jinju variants, including Jinju rows that start with `강의계획서`.
- `ILMS_LEARNING` targets under `/ilms/learning/learningList.do` parse Seongnam/Asan-style table rows and split short Korean dates, multi-day schedules, capacity, application period, and status.
- `HSCITY_LECTURE` targets under `yeyak.hscity.go.kr/1002/3001/lectureList.do` parse `.table-list-item` rows and follow detail pages. For the `INS01` library target, the parser treats `운영기관`/list `info-title` as the branch, splits libraries as separate branches, collects weekday/time, instructor, material fee, image, and excludes historical `end` pages by crawling `apply`, `wait`, `ready`, and `finish` status filters.
- `YDP_RESERVE` targets under `ydp.go.kr/reserve/selectTnEdcLctreListU.do` follow each detail page so course period, weekday/time, fee, material fee, instructor, target, and description come from the official detail table.
- `ICE_LIBRARY_TEACH` targets under `lib.ice.go.kr/*/module/teach/index.do` parse the expandable `.item` rows and split single-date or date-range course periods from weekday/time text.
- `JONGNO_EDU_APPLY` redirects the information page `/edu/eduIntro.do` to the real application list `/edu/eduApplyList.do` and parses branch, application period, period, schedule, capacity, and status.
- `SANGJU_RESERVE` targets under `/page/15375/11881.tc` parse the `#reserveList` reservation cards and extract facility, period, address, application period, capacity, and status.
- `NAJU_COURSE_RECEPTION` redirects Naju info/category pages to `/edu/lifelong/course_reception/all`, parses the real course table, and follows detail pages for target, category, venue, and description.
- `BSBUKGU_RESERVATION` parses Busan Buk-gu library reservation list links and follows details for period, schedule, fee, description, instructor, and target hints.
- `SD_BOOKING` redirects Seongdong information pages to `booking/webExcursionsProgramList.do` and follows details for branch, period, schedule, fee, material fee, capacity, and reservation method.
- `CHUNGJU_RESERVE` parses `chungju.go.kr/rev/reserve/*` resident-center lecture categories. It iterates the top 읍면동 category bar, follows detail pages, fills weekday/time, fee, instructor, classroom, address, and skips expired education periods.
- `GONGJU_NURIM` redirects the Nurim landing page to `prog/nurimLeaEducate/.../list.do` and follows details for period, schedule, fee, target, venue, instructor, and description.
- `JNTLE_DAMOA` parses the Jeonnam lifelong `uDamoaLecture` card list. The site exposes title, period, branch, status, and description, but not a separate fee/schedule field.
- `YONGSAN_LESSONS` redirects Yongsan member/index pages to the lesson list and parses period, fee, description, and branch from the official lesson rows.
- `SLL_ONLINE` redirects the Seoul lifelong learning root to the online course list and parses title, period, schedule, fee, target, and description.
- `ICDONGGU_FUTURE_EDU` parses Incheon Dong-gu future-education cards so title, branch, target, fee, application period, course period, schedule hints, image, and status are no longer mixed into the title.
- `YEONJE_LECTURE_LIST` parses Yeonje lifelong `.edu_items` and follows GET detail pages for schedule, target, fee when present, instructor, venue, capacity, and description.
- `SASANG_APPLY_LIST` parses Sasang integrated reservation `.bbs_edu` rows, reconstructs `view.sasang` URLs from `url_chk(...)`, follows detail pages, and separates status, category, target, fee, material notes, period, application period, schedule, branch, room, venue, capacity, instructor, phone, and description. Current/future education-period filtering is applied by default so expired reservation rows are not re-collected.
- `YANGJU_EDU_LECTURE_LIST` parses Yangju integrated reservation `table.list_table` rows, follows `eduLctreWebView.do` detail pages, and separates branch, venue, period, application period, weekday/time, target, fee, status, capacity, instructor, phone, application URL, and description. Expired education-period rows are filtered before saving, and known Yangju facility addresses are mapped for geocoding.
- `DAEJEON_DONGGU_EDU` parses Daejeon Dong-gu `selectUserEduList.do` tables and follows detail pages for category, target, address, age notes, material notes, schedule, capacity, instructor, and description. Stale search-result page URLs are normalized back to page 1.
- `BUPYEONG_LECTURE_LIST` parses Bupyeong lifelong `.lecList` cards and follows detail pages for target, fee, material fee, schedule, instructor, image, and notice text. The information page `/lll/edu/info.jsp` is redirected to the actual lecture list.
- `ULSAN_YES_LECTURE` parses `yes.ulsan.go.kr/lecture` result cards and separates branch, title, status, period, schedule, fee, target, capacity, method, and contact.
- `MUNI_WWW_ULSANNAMGU_GO_KR_E36CF0FF` parses Ulsan Nam-gu resident-autonomy program tables. It iterates every `dongName` branch option, paginates with `pageIndex`, reads the branch phone table, and stores each dong as a map branch. The source does not expose course/application periods, so `period` is intentionally empty.
- `YUSEONG_LIFELONG_CLASSES` parses Yuseong `classList.do` `.item-inner` cards and separates title, branch, status, application period, period, schedule, fee, target, capacity, method, and contact.
- `DOBONG_COURSE_BOXES` parses Dobong `lecture_*_Lst.asp` course boxes, including image, venue tag, category tag, target tag, application period, course period when exposed, fee, status, and description.
- `FMCS_LECTURE_API` parses FMCS-style sports/facility reservation sites by calling `rest/common/company` and `rest/lecture/list` directly. It handles both GET and POST deployments, branch codes, target, fee, schedule, status, capacity, and limited detail-page enrichment where the FMCS detail page exposes descriptions.
- `MUNI_YSSPORTS_YONG_SAN_OR_KR_67C8F87B` uses a dedicated FMCS variant parser. Its old `/www/50` URL is an information page, so collection is normalized to `/fmcs/8?center=YGSN01`. The parser cycles center/category metadata, collects legacy lecture rows, splits map branches by `comnm`, and enriches period/schedule/target from `proc_read dl` detail fields.
- `SYF_LECTURE_API` parses the Suwon Youth and Youth Foundation integrated reservation site. It redirects the stale guide-page targets to `/www/11`, iterates center codes from `POST /rest/common/company`, calls `POST /rest/lecture/list`, follows `action=read` detail pages, maps official center addresses for geocoding, and blocks the duplicate `MUNI_YEYAK_SYF_OR_KR_9C8EE56C` target.
- `BUAN_RESERVE_CARD` parses Buan integrated reservation education pages. It redirects the stale facility-guide target to the real education-course pages, clicks/iterates `.basic_tab2` branch tabs, paginates `.ed_list` cards, follows `상세보기` detail pages, and fills period, schedule, fee, target, status, instructor, description, image, capacity, and application period.
- `DOBONGSISEOL_LECTURE_TABLE` parses Dobong Facilities reservation PHP tables, including row-span course rows, `goLink(...)` detail POST parameters, fee, status, capacity, schedule, period, description, material notes, and material fee when exposed.
- `CHEONGJU_LCTRE_API` parses Cheongju lifelong-learning lecture APIs (`/info/lctre/request/paging`, `/info/lctre/request`, `/info/schdul`). It redirects the static regular-program information page to the actual request-list page and fills period, weekday/time, fee, status, application period, description, material fee, and schedule dates.
- `GNE_LIBRARY_LEC_LIST` parses Gyeongnam Office of Education library course pages. It discovers the actual `/usr_gne/lec_list.es` page from guide/menu URLs, parses `tstyle_list` rows, and follows `lec_v.es` detail pages with the required referer to fill instructor, venue, material fee, and description.
- `CHUNCHEON_LIFELONG` parses Chuncheon lifelong-learning gisu pages. It follows `edu_gisu_list.do`, POSTs selected gisu IDs to `edu_class_list.do`, then POSTs class IDs to `edu_class_view.do` for fee, material fee, instructor, and description.
- `SUSEONG_LEARNINGTALK` parses Suseong LearningTalk course detail pages. It discovers homepage `details.do` links and maps the detail `tbl02` table into branch, category, period, schedule, fee, material fee, capacity, venue, address, instructor, application period, and description.
- Seongnam/Asan ILMS office or media landing URLs are redirected to the actual `learningList.do` page before the shared `ILMS_LEARNING` parser runs.
- Gangbuk broken `gangbuk.go.kr/rsvt/cntedu/eclstPrgm/list.do` URLs are redirected to the working `office.gangbuk.go.kr/rsvt/main/main.do` reservation entry.
- `HNYOUTH`, `YEONGDO_HLL`, `BSJUNGGU_LLL`, and `SPORTS_VOUCHER` currently return `no_structured_courses` when the page exposes no active structured course list. This is intentional to avoid saving popups, notices, portal menus, or file-download rows as courses.

Generated YAML targets exclude known non-course URL shapes such as news/article pages, notice board detail URLs, and downloaded documents. These exclusions are intentionally applied before sampling so crawler quality reports focus on pages that can plausibly contain current reservation/course rows.
The shared generic parser also applies a final low-quality guard before rows are returned. It drops rows that look like navigation labels, board next/previous links, numeric post IDs, homepage/search labels, news/data/media pages, downloaded documents, popup text, or filter controls unless the row has strong course evidence. This protects the DB from false positives while provider-specific parsers are still being written.
Generated/public reservation crawlers now promote row-level facility data into `branches` when saving. If a row has `venue_address`, `address`, or `place_address`, that address is saved on the branch. If a reservation portal exposes a course facility through `venue_name`, `venue`, `place`, or a leading title label such as `[봉담와우도서관]`, that facility is used as the branch name instead of the broad city/reservation portal name. The normal crawler worker then runs the Google coordinate backfill so those facilities can appear on the map once geocoded.
Generated/public crawlers also treat row labels such as `운영기관`, `기관명`, `시설명`, `센터명`, `지점`, `지점명`, `도서관명`, and `복지관명` as branch candidates. Run `tools/report_branch_split_candidates.py --write` to find providers that still have many active courses but only one broad branch.
Quality audits can run generated YAML targets concurrently:

```powershell
python -X utf8 Crawler/Crawler_GeneratedYamlTargets.py --all --per-target-limit 10 --max-pages 3 --detail-limit 8 --parallel-workers 8
```

## Run

```powershell
cd C:\project\mooncen
cd ops-console
npm run dev
```

Open:

```text
http://127.0.0.1:5175
```

수집 현황은 `Crawlers`, 품질 결과는 `Data Quality`에서 확인합니다.

The batch form runs:

```powershell
python -X utf8 Crawler/Crawler_GeneratedYamlTargets.py --all --max-priority 2 --offset <offset> --target-limit <targets> --save-db --per-target-limit <limit>
```

If the offset field is empty, the console suggests the first generated provider that does not yet have local DB rows.

For quality audits, include parser-missing targets without saving:

```powershell
python -X utf8 Crawler/Crawler_GeneratedYamlTargets.py --all --include-status needs_parser --target-limit 50 --per-target-limit 20
```

## Generated Provider Files

Each generated YAML target has its own wrapper under `Crawler/generated_yaml/`.
The wrapper keeps Ops Console and `run_crawlers.py` provider execution simple while the shared parser logic stays in `Crawler/Crawler_GeneratedYamlTargets.py`.

Regenerate the provider registry and wrapper files after changing YAML target files:

```powershell
python -X utf8 Crawler/Crawler_GeneratedYamlTargets.py --write-registry
python -X utf8 tools/generate_registry_crawler_files.py
```

Registry behavior:

- `ready`, `partial`, `needs_discovery`, `needs_parser`, and `candidate` targets are enabled unless the URL shape is known to be non-course content.
- `blocked` targets remain in `config/generated_yaml_crawler_registry.yaml` and still get wrapper files, but `run_crawlers.py` skips them by default.
- Notice/news/article URL shapes are kept as disabled registry rows so they are visible for cleanup or rediscovery without being run by the daily worker.

Run one generated provider directly:

```powershell
python -X utf8 Crawler/generated_yaml/BUSAN_NATIONAL_SCIENCE_MUSEUM.py --per-target-limit 10
```

Run one generated provider through the worker:

```powershell
python -X utf8 run_crawlers.py --providers BUSAN_NATIONAL_SCIENCE_MUSEUM --once --ignore-active-window
```
