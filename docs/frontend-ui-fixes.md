# Frontend UI Fixes

## 2026-06-17 Dev Server And Narrow UI Fixes

- Verified `start_dev.ps1` and `start_dev.cmd` restart both API and frontend successfully.
- Changed nearby branch-list counts to show only `open_course_count` so the list reflects 접수중 강좌 only.
- Added final course-card spacing rules so the title block and target-age badge cannot overlap on narrow cards.
- Changed narrow category chips to horizontal scrolling with stable text sizing instead of over-compressing labels.

Validation:

- `.\start_dev.ps1 -Restart -StartupTimeoutSec 90`
- `.\start_dev.cmd -Restart`
- HTTP 200 from `http://127.0.0.1:5174`
- API health `{"status":"ok","environment":"development"}`

## 2026-06-15 Aggregate Course Group Expansion

The `전체 강좌` result group should grow with its visible course cards instead of creating an internal scroll area.

- Added a final CSS override for `.aggregate-course-group`.
- Removed max-height and internal `overflow-y` from the aggregate group's `.branch-class-grid`.
- Kept the existing progressive more/collapse count logic unchanged.

Validation:

- `npm run build` in `frontend2`.
- Headless Chrome desktop and mobile checks confirmed `overflow-y: visible`, `max-height: none`, and no horizontal page overflow.

## 2026-06-12 Square Map Ratio Policy

The dashboard map must always render as a 1:1 square. Other dashboard column widths should be adjusted around that rule, not by stretching the map.

- Added an EOF desktop ratio override that keeps `.nearby-map-canvas` at `aspect-ratio: 1 / 1`.
- Adjusted non-map column ratios around the square map column.
- Changed the Google map card to fill the remaining area inside the square map card.
- Synced non-map top-card heights to the square map height so the row is visually consistent.
- Verified with headless Chrome screenshot `logs/ui-dashboard-map-square.png`.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-12 Dashboard CSS Order Repair

The dashboard UI was still broken because older final CSS overrides appeared after the newer four-card rules and overwrote them.

- Split `Sidebar` markup into explicit primary-filter and detail-filter card wrappers.
- Added a true EOF dashboard correction so it wins over older accumulated rules.
- Verified with a headless Chrome screenshot at `logs/ui-dashboard-check-eof.png`.
- Confirmed the top dashboard now renders as four equal cards:
  - scope/category
  - detail filters
  - branch list
  - map
- Kept the map contained inside its card.
- Added ellipsis handling for long detail-filter values.

Validation:

- `npm run build` in `frontend2`.
- Headless Chrome screenshot check.

## 2026-06-12 Map Card Containment

The Google map could overflow the dashboard card because its internal card height/width was calculated independently from the parent card.

- Changed the map card to a two-row grid: toolbar plus remaining map area.
- Forced the Google map card to stretch only inside the remaining card area.
- Removed fixed desktop map dimensions that could exceed the parent.
- Added overflow clipping to the map card and inner Google map root.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-12 Equal Top Dashboard Card Size

The four top dashboard cards needed to look like equally sized cards.

- Fixed the desktop top dashboard card height to 520px for all four columns.
- Kept the four columns at equal width.
- Made the map fill its card height instead of keeping a separate square ratio inside the row.
- Kept the reset action at the bottom-right of the detail-filter card.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-12 Result Brand Icons And Branch Count Alignment

Search result cards only showed provider text, and branch-list counts were visually detached from the row content.

- Added `ProviderIcon` to each course card's center/provider line.
- Kept provider text next to the icon with ellipsis handling.
- Fixed branch-list rows to an icon, branch info, and right-aligned count grid.
- Removed ambiguous count spacing by pinning the count column width and vertical alignment.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-12 Filter Header Removal And Alignment

The filter title area added extra vertical spacing and made the filter columns start lower than the branch list and map.

- Removed the desktop filter header/title spacing.
- Aligned the search scope, category, detail filters, branch list, and map at the same top position.
- Standardized top padding across the filter cards, branch-list header, and map toolbar.
- Kept mobile filter drawer behavior unchanged.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-12 Branch List Row Simplification

Branch list rows duplicated course counts in both the status line and the right-side count column.

- Simplified branch rows to provider/institution icon, branch name, distance, and right-aligned open-course count.
- Removed the duplicated status line from branch rows.
- Kept provider icons visible for all branch rows.
- Kept branch selection and map hover/click behavior unchanged.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-12 Detail Filter Reset Only

The filter panel no longer needs an apply action because filter choices are applied immediately.

- Removed the `Apply` button from the sidebar action area.
- Kept only the reset action.
- Positioned reset under the detail-filter group on desktop.
- Preserved existing immediate filter behavior and mobile panel behavior.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-12 Separate Detail Filter Group

The desktop filter column visually mixed search scope/category controls with detail filters.

- Kept the first dashboard column for search scope and category controls.
- Rendered the detail filters as their own card-style group in the second column.
- Kept the apply action attached to the detail-filter group.
- Preserved the mobile stacked filter panel behavior.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-12 Equal Four Column Dashboard Width

The top dashboard needed equal visual weight across scope/category, filters, branch list, and map.

- Changed the desktop dashboard grid to `repeat(4, minmax(0, 1fr))`.
- Removed the desktop map card max-width cap inside the four-column row so the map fills its equal column.
- Kept the mobile stacked layout unchanged.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-12 Map Pan Incremental Branch Updates

Map movement previously caused branch-visible IDs to change the course query key, clearing all course results before refetching. That made the UI look like a full refresh on every pan.

- Normalized visible map branch IDs by set, so order-only changes no longer trigger result refreshes.
- Split the course query key into a base filter key and a branch-scope key.
- When only the map branch scope changes, existing courses are kept, out-of-scope branch courses are removed, and fetched courses are merged in.
- When real filters/search/status/date change, the result list still resets normally.
- Nearby branch fetches now keep existing branch objects for branches that remain in range and only add newly discovered branches or remove branches outside the search radius.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-12 Four Column Search Dashboard

The main dashboard needed to show the primary controls and result context in one horizontal band.

- Reworked the desktop layout into four top columns:
  - search scope plus category
  - detail filters
  - branch list
  - map
- Kept the course result section below the four-column dashboard row.
- Used CSS placement only, preserving existing API calls, filter state, branch selection, and map behavior.
- Kept the mobile layout stacked so narrow screens do not overflow horizontally.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-12 Square Map Size Cap

The map was square but still too large because its width followed the full remaining dashboard column.

- Kept the Google map area square.
- Capped the desktop map card to 500px so the map does not dominate the first viewport.
- Capped the mobile map card to 340px and centered it.
- Reduced the desktop map/list grid columns so the branch list height tracks the smaller map area.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-12 Branch Group Expand Render Limit

Branch course groups previously rendered every loaded course when the user clicked expand. Large branches could create unnecessary client rendering cost and make the page feel heavy.

- Added `expandedBranchPreviewCount = 20` in `frontend2/src/App.tsx`.
- Collapsed branch groups still show the 3-item preview.
- Manual expand now shows up to 20 courses for that branch.
- Active search/filter states still show all matching courses, so narrowed results remain fully visible.
- Branch-local quick filters also show all matching courses inside that branch.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-12 Sidebar Filter Text Overlap Fix

Culture-center sidebar filters could overlap icon artwork and text, especially in the search scope buttons and compact category chips.

- Added final CSS overrides in `frontend2/src/styles.css` to reserve fixed icon space in `.filter-mode-selector`.
- Forced scope button title/subtitle text to use single-line ellipsis instead of overlapping icons or active indicators.
- Tightened category chip and quick-filter button text handling so long labels do not resize the row or spill into adjacent UI.
- Locked the filter header into a title/reset two-column grid so the reset action cannot overlap the title.
- Reworked the sidebar brand quick-filter chip so its label/value are laid out horizontally without compressed stacked text.
- Added mobile map toolbar/radius-control overrides so the map title and radius buttons do not collide on narrow screens.
- Renamed the sidebar apply action from `조건 적용하기` to `적용하기`.
- Changed the filter panel to an internal scroll layout so the apply action stays inside the filter panel instead of being pushed outside.
- Compacted sidebar filter typography, icon sizes, chip widths, row heights, and section gaps to reduce internal scrolling.
- Removed the sidebar brand filter from the culture-center filter panel.
- Removed fuzzy category mapping so one category chip no longer activates another chip that happened to share the same matched value.
- Separated the reset action visually from the filter title with a small pill button.
- Changed detail filter dropdowns/calendars to floating popups so opening one does not stretch the sidebar and create panel scrolling.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-12 Map Location And Center Point

The map did not always show the current/base location marker, and users could not tell which point became the search center after moving the map.

- Updated `frontend2/src/components/MapSection.tsx`.
- The user/base location marker now renders even when browser GPS is unavailable and the app falls back to an approximate/default location.
- Added a fixed map-center indicator overlay labeled `기준점`; it stays at the visual center while the map is moved.
- Updated `frontend2/src/styles.css`.
- Forced the Google map canvas to a square aspect ratio with a final override.
- Added responsive sizing for the center indicator on mobile.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-12 Map Marker Popup Scrollbar

Clicking a map marker could open a Google InfoWindow that introduced an internal scrollbar or made the map legend show a scrollbar.

- Added final CSS overrides for Google InfoWindow containers under `.google-map-card`.
- Limited marker popup width and disabled popup body overflow scrolling.
- Truncated long branch/provider text inside the popup instead of expanding the popup.
- Disabled legend overflow scrolling and allowed compact wrapping.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-12 Square Map And Branch List Height

The map and branch list did not share a stable height, and the branch list could show its own scrollbar.

- Added a final CSS override in `frontend2/src/styles.css`.
- Kept the visible Google map canvas square with `aspect-ratio: 1 / 1`.
- Stretched the branch list to match the map canvas area.
- Removed internal branch-list scrolling on desktop.
- Distributed visible branch rows within the available branch-list height.
- Kept mobile as normal page flow so the page scrolls instead of the branch card itself.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-12 Education Branch List Compact Rows

Education/experience mode did not need the branch address line or duplicated lower course-count line in the branch list.

- Added `mapMode` to `NearbyCenterMap` so branch-list presentation can differ by search type.
- Passed the current `mapMode` from `App`.
- Added CSS to hide address/subtext and the lower status/count row only in education/experience mode.
- Kept the right-side count visible as the single count indicator.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-12 Branch Distance Uses Map Center

Branch-list distances stayed fixed because they were calculated from the original user location even after the map center moved.

- Added `distanceReferenceLocation = mapSearchCenter ?? userLocation` in `frontend2/src/App.tsx`.
- Nearby branch filtering, sorting, and displayed distances now use the current map search center.
- Kept the actual map user-location marker based on `userLocation`.
- Lowered the map-center update threshold from 1km to 50m so list distances refresh after normal map movement.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-12 Map Marker Hover Links To Branch List

Map markers needed to visually activate the matching branch row when users hover over a marker.

- Added marker hover propagation from `CenterMapMarker` through `MapSection`.
- Shared the hovered branch id in `App`.
- Passed the hovered branch id into `NearbyCenterMap`.
- Added `map-hover` styling so the matching branch row reacts like a map-linked hover state.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-12 Branch Group Bottom Expand Control

Branch course groups had an expand control in the header, which made the top area crowded and could conflict with group quick filters.

- Removed the header expand button and title-click expand behavior from `frontend2/src/App.tsx`.
- Added a single expand/collapse button at the bottom of each collapsible branch group.
- Increased compact course card height and restored enough thumbnail/body space so title, metadata, price, center, and actions remain visible.
- Fixed a CSS cascade bug where an older horizontal card rule left `grid-template-columns: 210px 1fr`, while a newer vertical card rule only changed rows. That trapped the card body in the thumbnail row, so course text could disappear.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-12 Denser Course Cards

Course cards had too much unused whitespace and did not show enough useful course information.

- Added instructor and category rows to `frontend2/src/components/ClassCard.tsx` when meaningful data is available.
- Increased course card height and reduced internal padding/gaps so additional metadata can fit without overlap.
- Adjusted desktop grids to show up to 5 cards per row on wide screens, 4 cards by default, 3 on medium desktop, 2 on tablet, and 1 on mobile.
- Kept mobile card width to a single column and constrained text with ellipsis/line clamps to avoid overflow.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-12 Branch List Display Name Normalization

Branch rows and selected branch labels could repeat the same culture-center brand text in both the provider icon and branch name. Education/experience branch names could also mix region text and institution names in a single title line.

- Added `frontend2/src/utils/branchDisplay.ts` for display-only branch name normalization.
- Culture-center rows now show the short branch name as the title and the provider label as subtext.
- Education/experience rows split region-like prefixes into subtext where possible.
- Applied the normalized display text to the nearby branch list, map popup, selected branch panel, selected branch reset bar, and course result group headers.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-12 Dialog Click Should Not Activate Filters

Login, notification, and course detail overlays could appear below the sidebar filter layer because the final sidebar popup override used a higher z-index than dialogs.

- Added a shared `closeTransientFilters` path in `frontend2/src/App.tsx`.
- Login, notification, and course detail actions now close mobile filters, branch quick filters, and sidebar quick filter menus before opening overlays.
- Added a `closeSignal` prop to `Sidebar` so App-level overlay actions can clear Sidebar-local filter menu state.
- Raised dialog, notification, and toast layers above sidebar filter layers in `frontend2/src/styles.css`.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-11 Map Zoom Enabled

The Google Map was still locked down in normal mode, which prevented practical zooming.

- Enabled the Google Maps zoom control in normal mode.
- Enabled mouse wheel zoom and double-click zoom.
- Re-enabled keyboard shortcuts.
- Relaxed the zoom range from `10~16` to `7~19`.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-11 Branch Group Expand Button Deduplication

Branch course groups showed two expand controls at the same time: the header `expand/collapse` toggle and a lower `expand more` button under the course preview.

- Removed the lower `branch-course-expand-button` block from `frontend2/src/App.tsx`.
- Kept the group header toggle as the single expand/collapse control.
- Removed the now-unused hidden-item count calculation from the group render path.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-11 Non-Culture Provider Favicon Icons

Education/experience branch rows needed real site identity instead of only MoonCen shorthand/type icons.

- Extended backend provider metadata to load provider homepage URLs from YAML crawl target registries.
- Added `website_url` and `favicon_url` to the `/branches/nearby` branch response.
- Kept retail culture-center providers excluded from favicon rendering so official brand logos are not mixed into the custom culture-center UI.
- Updated `ProviderIcon` to render favicon images first for non-culture providers, with fallback to the existing shorthand/type icon when the favicon fails.
- Applied the favicon icon to nearby branch list rows and map info-window provider icons.

Validation:

- `python -m py_compile backend/provider_metadata.py backend/schemas.py backend/routers/locations.py`.
- `npm run build` in `frontend2`.

## 2026-06-11 Emart Branch Map Visibility

Emart branches could disappear from the map even when branch coordinates existed because the map/list visibility check used only active/open course counts. Most nearby Emart branches currently have collected courses but `active_course_count = 0`, so they were filtered out before marker rendering.

- Changed the main nearby branch filter in `App.tsx` to use total collected course count for branch/map visibility.
- Changed `MapSection` marker visibility fallback to use `branch.course_count` first.
- Changed `NearbyCenterMap` list visibility to use total collected course count, while keeping the displayed open count based on `active_course_count` or filtered OPEN results.
- This keeps branch locations visible while still showing `접수중 0개` when a center only has closed courses.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-11 Sidebar Category Filter Icons

The sidebar category filter needed to use the provided mint single-color icon style instead of falling back to generic icons.

- Rebuilt `frontend2/src/utils/categoryIcon.ts` with clean UTF-8 Korean category mappings.
- Added mint-line icon paths for common filter concepts and course categories.
- Culture-center categories now map to icons for baby, art, music, sports, cooking, language/reading, science, and coding.
- Education/experience categories now map to public course, library, museum, science center, experience event, one-day, welfare, and nature/ecology.
- Refined sidebar category chip icon sizing and active/hover colors in `frontend2/src/styles.css`.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-11 Small Map Marker Set

The map marker design was updated to match the latest small marker reference.

- Rebuilt `CenterMapMarker` as a compact SVG marker generator.
- Culture-center markers now use brand-style text pins for Homeplus, emart, LOTTE, AK, THE HYUNDAI, and SHINSEGAE.
- Galleria, Eland, and unknown culture-center providers now fall back to the `기타 문화센터` star marker.
- Education/experience markers now use institution-style icons for library, museum, science center, public institution, youth center, and other.
- Marker course-count numbers remain removed; counts stay in branch lists and popups.
- Favorite and urgent states remain small marker badges.
- Refined the small marker again so the actual map marker uses short, legible brand symbols (`HP`, `E`, `LT`, `AK`, `HD`, `SS`) instead of tiny multi-line logo text.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-11 Apply Button URL Priority

The course card apply button could open `application_url` before the crawler-collected `raw_url`, which could send users to a discovered or inferred URL instead of the collected source page.

- Changed `openCourseApplication()` in `frontend2/src/App.tsx`.
- The apply button now opens `rawUrl` first and falls back to `applicationUrl` only when `rawUrl` is missing.
- Detail-page source action was already using `rawUrl`.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-11 Filter Scope Icons

The sidebar exploration-scope filter needed to use the provided magnifier/book icon style.

- Added `ScopeIcon` with inline SVG artwork for culture-center search and education/experience book modes.
- Replaced the provider-logo icon inside the sidebar `탐색 범위` selector with `ScopeIcon`.
- Added final CSS overrides for glowing mint icon surfaces, active state, and mobile sizing.
- Kept the existing mode-switching behavior unchanged.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-11 Radius Range Selector Icons

The map radius selector needed to follow the provided exploration-range icon reference for 5km, 10km, and 20km.

- Added compact inline SVG range-pin icons to `NearbyCenterMap`.
- Applied separate tones for 5km mint, 10km amber, and 20km blue.
- Kept the existing radius selection behavior and map refit logic unchanged.
- Added final CSS overrides for icon sizing, active glow, mobile sizing, and compact pill layout.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-11 Category Icon Set

Category chips needed to follow the provided culture-center and education/experience category icon reference.

- Added `frontend2/src/utils/categoryIcon.ts` with reusable SVG path metadata for culture-center and education/experience categories.
- Added `CategoryIcon` to render pastel circular category icons without external image assets.
- Replaced `QuickCategoryChips` fallback text icons with actual category icons.
- Added category icons to sidebar category filter chips while preserving existing filter state and click behavior.
- Styled quick category chips and sidebar category chips to use circular icons, pastel backgrounds, and compact labels.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-11 Full Map Marker Visual Set

Map markers needed to follow the provided marker reference for both culture centers and education/experience contents.

- Replaced the marker generator in `CenterMapMarker` with a larger pin-shaped SVG marker set.
- Culture-center markers now render branded text inside the pin: Homeplus, emart, LOTTE, AK, THE HYUNDAI, SHINSEGAE, Galleria, and etc.
- Education/experience markers now render category icons inside the pin for child, nature, art, music, sports, reading, cooking, coding, science, tradition, photo/video, career, language, social/performance, camp/outdoor, and other.
- Favorite and urgent states remain small badges and do not replace the marker identity.
- Map marker numbers remain removed; course counts stay in branch lists and popups.
- Updated the map legend so provider mode explains brand color and education mode explains category icons.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-11 Culture Center Brand Logo Badges

Culture-center branch icons needed to follow the provided brand-logo reference instead of showing only shorthand codes.

- Rebuilt `frontend2/src/utils/providerIcon.ts` with clean UTF-8 provider detection and logo metadata.
- Updated `ProviderIcon` to render culture-center providers as text logo badges with brand name plus `문화센터` or `아카데미` subtext.
- Added brand-color text treatments for Homeplus, emart, LOTTE, AK, THE HYUNDAI, SHINSEGAE, Galleria, Eland, and the default Mooncen culture center fallback.
- Kept education/experience providers as compact shorthand icons.
- Adjusted provider icon CSS widths for branch lists and map popups while preserving small badge behavior in tight spaces.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-11 Culture Center Map Marker Cleanup

The culture-center dashboard map mixed course-count numbers, brand state, favorite state, and urgent state inside one marker, which made the marker meaning ambiguous.

- Rebuilt `CenterMapMarker` to generate mooncen-style SVG map pins per provider instead of using count-label image markers.
- Removed the course-count label from map markers; marker numbers are no longer displayed.
- Applied brand color palettes for Homeplus, Emart, Lotte/Lotte Mart, AK, Hyundai, Shinsegae, and a mint fallback for other culture centers.
- Kept favorite and urgent states as small badges on the marker, not as full-marker color changes.
- Rebuilt `MapSection` text and popup content so course counts are shown only in the selected branch popup, with distance, open count, and urgent count.
- Rebuilt `NearbyCenterMap` so the branch list shows provider, distance, open course count, and urgent count; map markers focus on location and brand.
- Updated the map legend to remove count-number language and explain brand color, favorite, and urgent badges.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-11 Provider Icon System

Branch list icons were too similar, making provider and institution type hard to scan.

- Added `frontend2/src/utils/providerIcon.ts`.
- Added shared `frontend2/src/components/ProviderIcon.tsx`.
- Replaced the local branch-list SVG provider icon in `NearbyCenterMap`.
- Added provider icons to map info windows.
- Replaced sidebar search-scope image icons with the same provider icon component.
- Added small provider icons to course result branch group headers.
- Added CSS for small, medium, and large provider icon sizes.
- Documented the mapping in `docs/brand/provider-icons.md`.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-11 Compact Course Card Text Overlap

Compact course cards could overlap text because the card height was fixed while typography, metadata, price, center, and action buttons exceeded the available vertical space.

- Added a final card layout override in `frontend2/src/styles.css`.
- Kept the compact card height fixed while explicitly defining all five body rows.
- Reduced title, metadata, price, center, and button sizing to fit the fixed card.
- Added line clamps and ellipsis rules for long titles, age labels, dates, times, material fee, and center names.
- Added a mobile-specific compact sizing rule to avoid overlap on narrow screens.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-11 Nearby Map Height Reduction

The branch list and map area took too much vertical space in the main dashboard.

- Added a final desktop override in `frontend2/src/styles.css`.
- Reduced `nearby-map-layout`, `nearby-center-list`, and `nearby-map-canvas` to roughly two-thirds of the previous height.
- Reduced the embedded Google Map height and minimum height accordingly.
- Added a smaller mobile height override for the branch list and map.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-11 Sidebar Top Alignment

The left filter panel could appear lower than the branch list and map after recent dashboard and popup layering overrides.

- Added a desktop-only final override in `frontend2/src/styles.css`.
- Restored `.sidebar-shell` to `position: sticky` after the popup layering override.
- Reduced the sticky offset to `76px` so the filter aligns with the top dashboard row.
- Forced the dashboard grid and sidebar column to align from the top.
- Kept the mobile filter panel as a fixed overlay.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-11 Sidebar Filter Popup Layering

Sidebar filter popups could still appear behind the map, branch list, or result section because parent containers created clipping and stacking contexts.

- Added a final layering override in `frontend2/src/styles.css`.
- Forced the dashboard layout, sidebar shell, filter panel, and quick filter containers to keep dropdown overflow visible on desktop.
- Disabled the `isolation` stacking context on sidebar quick filters.
- Raised active sidebar filter controls and their popup/calendar layers above map, branch, and result sections.
- Kept the mobile sidebar as a fixed overlay with its own scroll behavior.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-11 Sidebar Filter Popup Surface

Sidebar detail filter popups could look transparent over the underlying filter panel and could show their own scrollbar.

- Added a final CSS override in `frontend2/src/styles.css`.
- Filter popups and mini calendars now render as opaque white floating cards.
- Removed popup-level scrollbars with `overflow: visible` and hidden scrollbar rules.
- Multi-option filter menus use a two-column grid so options fit without opening an internal scroll area.
- Mobile keeps the sidebar panel scrollable while the popup itself stays scrollbar-free.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-11 Dashboard Vertical Ratio

The filter/branch/map area was too tall, pushing the results section too far down.

- Added a desktop final override in `frontend2/src/styles.css`.
- Reduced the header to about 64px.
- Reduced `nearby-map-layout` from the previous `58vh` range to about `50vh`.
- Reduced map/list internals, toolbar height, radius buttons, branch row spacing, and map minimum height.
- Gave `results-section` an approximate `40vh` minimum so the page reads closer to Header : Map : Results = `1 : 5 : 4`.
- Added an extra compact rule for shorter desktop viewports.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-11 Course Status Badge Placement

The `status-badge status-open` element did not match the reference card style and could be hard to see on course thumbnails.

- Added a final course card status badge override in `frontend2/src/styles.css`.
- `status-open` now renders as a white pill at the bottom-left of the thumbnail, matching the reference `접수중` treatment.
- `status-new`, `status-deadline`, and `status-closed` stay as colored top-left badges.
- Increased badge z-index and fixed inline-flex sizing so badges remain visible over images.
- Added mobile sizing adjustments.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-11 Age Filter State During Search Type Switch

Switching between `문화센터` and `교육·체험` could display the age filter as `전체` even when the user had not explicitly selected an age filter.

- Kept `ageFilters === null` as an explicit "no age condition" display state in `Sidebar`.
- Sidebar now receives nullable age filter state instead of the computed effective all-age list.
- Clicking an age option from the null state now selects only that age, not every age except the clicked value.
- Filter labels now compare option/value sets, not only array lengths, so mode changes do not incorrectly display `전체`.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-12 Provider Icon Rendering

Branch list and search result course cards could show the wrong or unreadable provider icon.

- Rebuilt `frontend2/src/utils/providerIcon.ts` with stable provider-code and Korean/English keyword matching.
- Added short source-code support for course cards (`H`, `E`, `L`, `M`, `A`, `HD`, `S`, `G`, `ER`).
- Fixed branch-list and map-popup `ProviderIcon` calls to pass the actual provider code instead of the collection category.
- Search result cards and branch list rows now use user-facing Korean badge labels such as `홈플`, `이마트`, `롯데`, `AK`, `현대`, `신세계`, and `갤러리아`.
- Map pins remain separate from normal badges and can use compact pin-only labels such as `HP`.
- Rebuilt `frontend2/src/data/mockData.ts` to remove corrupted display strings while preserving the existing exported types and conversion functions.
- Added final CSS rules to keep branch-list and course-card provider badges readable without clipped text.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-12 Main UI Polish Items 3-10

Polished the main dashboard without changing API, map SDK, or environment settings.

- Standardized provider badges through `ProviderIcon` and `getProviderIcon`; branch-list and course-card badges now use user-facing Korean labels instead of clipped logo text or map-only shorthand.
- Rebuilt `branchDisplay` as the single branch display-name utility and mapped `gwanggyo` to `광교점` while preserving internal ids.
- Course cards now use `branchDisplayName` and `cultureProviderLabel`, removing duplicated provider-label logic from `mockData`.
- Added selected-branch context to the results heading and added `aria-current` to the selected branch row.
- Sidebar detail filters now summarize multi-select values as `N개 선택`; the later UI apply pass removed the automatic-update note and kept explicit reset/apply actions.
- Stabilized course card layout: fixed thumbnail height, two-line title clamp, fixed meta/price/center/action rhythm, and consistent button alignment.
- Removed visible map center-point UI and added a visible-range frame/label overlay for the current map viewport.
- Added responsive final CSS to avoid horizontal overflow and keep 4-column desktop cards, 2-column tablet cards, and 1-column mobile cards.

Validation:

- `npm run build` in `frontend2`.
- Desktop screenshot: `logs/ui-polish-3-10.png`.
- Mobile screenshot: `logs/ui-polish-3-10-mobile.png`.

## 2026-06-13 MOONCEN UI Apply Prompt

Applied `MOONCEN_UI_APPLY_PROMPT.md` to the main search dashboard without changing API response shape, map SDK settings, or environment variables.

- Converted the previous large filter cards into a compact top filter bar.
- Limited primary category chips to `전체`, `영유아`, `미술`, `음악`, `체육`, `요리`, with remaining categories behind `+ 더보기`.
- Removed the automatic-update helper text from the filter action area and kept explicit `상세필터`, `초기화`, and `적용하기` actions.
- Changed the top content area to branch list, square map, and selected-branch summary columns.
- Branch list now shows 5 branches first, then a real `+ N개 지점 더보기` control.
- Added a selected-branch summary card with distance, current-condition count, open/urgent/closed counts, and up to two actions.
- Kept the Google map card square and contained inside its card.
- Unified branch-list, result-header, and course-card counts around the same filtered visible data.
- Updated course title cleanup so date, day, time, and age patterns are removed from card titles and kept in metadata.
- Kept normal UI provider badges in Korean while preserving map-pin-only exceptions.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-13 Screenshot Dashboard Layout Follow-up

Adjusted the main page toward the user-provided dashboard reference while keeping the existing API, map SDK, search, filter, branch selection, compare, apply, and favorite flows intact.

- Rebuilt the sidebar output into a single horizontal filter bar: search scope, compact category chips, age/date/day/time/status filters, and detail/reset/apply actions.
- Fixed the top content area to branch list, map, and selected-branch summary cards in one row on desktop.
- Kept the branch list compact with provider icons, branch name, distance, and one visible course count.
- Added selected-branch summary fallback behavior so the summary card stays stable even before explicit selection.
- Made radius controls readable text pills (`5km`, `10km`, `20km`) instead of clipped icon buttons.
- Added a header logo fallback so the header does not render blank while the logo image is still loading.
- Tightened scope-card layout so the culture/education icons and text do not overlap.

Validation:

- `npm run build` in `frontend2`.
- Desktop screenshot: `logs/mooncen-layout-check-11.png`.

## 2026-06-13 Branch Provider Badge Long Label

Fixed long provider labels overflowing inside the small branch-list provider icon.

- Added a `provider-icon-long-label` class in `ProviderIcon` when the rendered provider label has four or more Korean characters.
- Reduced small branch-list provider badge font size and horizontal scale for long labels.
- Added a targeted rule for the `갤러리아` badge so it stays inside the circular icon.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-13 Branch List More And Scroll

Changed the branch list behavior so it can show more branches without stretching the whole dashboard row.

- Replaced the previous all-at-once branch expansion with an incremental visible limit.
- The branch list starts with 5 branches and `+ N개 지점 더보기` loads up to 10 more at a time.
- Added a `nearby-center-list-body` scroll container so only the branch rows scroll.
- Kept the branch-list header and more/collapse controls fixed inside the branch card.
- Reapplied the compact branch row styling to the new scroll body.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-13 Branch List Compact Distance

Reduced branch-list row height and kept branch distance on a single line.

- Removed the long provider/address fallback from the branch-list distance line.
- Branch rows now show branch name and distance in one compact row.
- Reduced row height, icon size, spacing, and count font size for the branch-list scroll body.
- Kept ellipsis handling for long branch names while preventing distance wrapping.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-13 Map Card And Legend Layout

Adjusted the map area to match the dashboard reference image more closely.

- Fixed the map card as a rectangular dashboard panel instead of inheriting conflicting square/auto-height rules.
- Forced the Google map container to fill the map card with stable absolute sizing.
- Kept radius controls overlaid at the top center of the map.
- Replaced the generic legend text with provider/type legend items.
- Culture-center mode now shows brand legend items: Homeplus, Emart, Lotte, AK PLAZA, Hyundai, Shinsegae, Galleria, plus favorite and urgent states.
- Education/experience mode now shows type legend items: library, museum, science center, public center, youth center, plus favorite and urgent states.
- The same legend renders in loading/error/fallback states so the map card no longer loses its legend while Google Maps is loading.

Validation:

- `npm run build` in `frontend2`.
- Desktop screenshot: `logs/mooncen-map-layout-check-3.png`.

## 2026-06-13 Filter Scope Cropped Icons

Updated the `탐색 범위` filter cards to use cropped PNG icons from the local Mooncen icon guide image.

- Cropped and saved search/book scope icons to:
  - `frontend2/public/assets/icons/scope-culture-search.png`
  - `frontend2/public/assets/icons/scope-education-book.png`
- Replaced the SVG-based `ScopeIcon` with PNG asset rendering.
- Rebuilt the scope-card CSS so icon, title, subtitle, and active dot do not overlap.
- Increased icon contrast and size for better readability inside the compact filter bar.

Validation:

- `npm run build` in `frontend2`.
- Desktop screenshot: `logs/mooncen-scope-icons-check-2.png`.

## 2026-06-13 Branch List Selected Circle Removal

Removed the small circular checked marker that appeared on selected branch-list rows.

- Kept the selected branch row background and border highlight.
- Restored the right-side row affordance to a plain chevron instead of the selected circle marker.
- Removed extra right padding that had been reserved for the old selected marker.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-13 Filter Bar Reference Alignment

Adjusted the main filter strip to match the dashboard reference layout more closely.

- Swapped the scope-card icons to the filter-specific culture/education icon assets.
- Re-aligned the filter strip into scope, category, and compact condition groups inside one white dashboard bar.
- Kept category chips compact with small icons so the filter bar does not grow unexpectedly.
- Moved detailed filter labels above their compact dropdown buttons to match the reference image.
- Kept dropdown popups opaque, elevated, and outside normal layout flow.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-13 Map Favorite Marker Heart

Adjusted map favorite-state rendering.

- Favorited branches now replace only the marker center-circle content with a heart.
- Removed the separate favorite badge from the marker SVG.
- Removed favorite and urgent state items from the map legend while keeping brand/type legend items.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-13 Branch List More Scroll Policy

Fixed branch-list overflow behavior in the dashboard map panel.

- The initial branch list now shows the first five rows without a scrollbar.
- The branch-list body enables vertical scrolling only after the user expands the list with the more button.
- The more/collapse buttons no longer inherit branch-row grid/chevron styles.
- Row height, icon size, distance, and count spacing were tightened so the list does not spill out of the card.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-13 Branch Selection Keeps List Stable

Fixed branch-list collapse/disappearance after selecting a branch.

- Branch clicks now update only the selected branch state and result scope.
- Branch clicks no longer force `providerFilters` to the selected branch provider.
- Clearing a selected branch no longer resets provider filters.
- The branch list remains based on the current map/radius/filter context while selected-branch highlighting is applied separately.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-13 Map Radius And Move-Only List Refresh

Fixed map/list synchronization for radius and zoom behavior.

- Map markers are now filtered by the current map search center and selected radius.
- A 10km radius now limits map markers to branches within 10km, regardless of zoom level.
- Map `idle` no longer refreshes branch IDs on zoom-only changes.
- Branch/list refresh from the map is triggered only when the map center actually moves by at least about 50m.
- The map now receives the same distance reference location that the nearby branch list uses.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-13 Filter Bar No-Overlap Correction

Adjusted the dashboard filter strip to prevent category/detail controls from overlapping.

- Locked the filter bar into isolated scope, category, and detail columns.
- Clipped category chips inside their own column so they cannot push into the age/date controls.
- Tightened category chip width, gap, and icon size.
- Gave detail filters their own internal top label space instead of allowing labels to bleed into the category area.
- Added a narrower-layout breakpoint where detail filters wrap to a second row instead of overlapping.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-13 Immediate Filter Apply Button Removal

Removed the filter apply action from the main dashboard filter bar because filter changes are already applied immediately.

- Removed the `onApplyFilters` prop from `Sidebar`.
- Removed the `onApplyFilters` prop passing from `App`.
- Removed the rendered `적용하기` button from the filter action area.
- Kept only `상세필터` and `초기화` in the filter action area.

Validation:

- `npm run build` in `frontend2`.

## 2026-06-13 UTF-8 Encoding Guard

Cleaned up mojibake introduced in `App.tsx` and `Sidebar.tsx` while removing the immediate-apply button.

- Restored broken Korean UI strings in result headings, filter labels, notices, and location messages.
- Verified `App.tsx` and `Sidebar.tsx` decode as UTF-8 with no replacement characters.
- Scanned `frontend2/src` for the mojibake patterns seen in the breakage.
- Going forward, TSX/TS/CSS edits should avoid shell overwrite commands that can alter encoding. Prefer `apply_patch`, then run a UTF-8/mojibake scan before build.

Validation:

- UTF-8 decode check for `frontend2/src/App.tsx` and `frontend2/src/components/Sidebar.tsx`.
- Mojibake pattern scan for `frontend2/src`.
- `npm run build` in `frontend2`.

## 2026-06-13 Map Branch Select Zoom Stability

Fixed map zoom jitter when selecting a branch marker or branch-list item.

- `MapSection` now distinguishes map-center changes reported from map idle events from external location/radius changes.
- Branch selection and InfoWindow auto-pan no longer trigger an immediate radius `fitBounds` recalculation.
- Radius changes and initial map load still use the existing radius fit behavior.

Validation:

- UTF-8 decode check for `frontend2/src/components/MapSection.tsx`.
- `npm run build` in `frontend2`.

## 2026-06-13 Branch List Selection Does Not Move Map

Stopped branch-list selection from moving the Google Map and refreshing the branch list unexpectedly.

- Disabled Google InfoWindow auto-pan in `MapSection`.
- Branch-list clicks still select the branch and update results.
- Map center/list refresh is no longer triggered just because an InfoWindow opens for the selected branch.

Validation:

- UTF-8 decode check for `frontend2/src/components/MapSection.tsx`.
- `npm run build` in `frontend2`.

## 2026-06-13 Course Card Visual Priority Polish

Adjusted the course card layout to match the compact reference card.

- Added visible instructor information in the card metadata area.
- Changed the status badge to a smaller horizontal pill.
- Formatted course dates from ISO date fields as `YYYY.MM.DD (weekday)`.
- Reworked branch/provider display so the provider and branch name are easier to scan.
- Rebalanced the visual priority of title, target, schedule, instructor, price, branch, and actions.
- Kept the existing card actions and data structure unchanged.

Validation:

- UTF-8 decode check for `frontend2/src/components/ClassCard.tsx` and `frontend2/src/styles.css`.
- Mojibake pattern scan for the changed files.
- `npm run build` in `frontend2`.

## Standing UI Validation Rule

For every frontend UI change, check the following before finishing:

- No visible text overlap, clipped labels, or clipped buttons.
- Spacing and alignment are consistent between neighboring panels, filters, cards, and map/list sections.
- No unintended horizontal scroll on desktop or mobile.
- Dropdowns, popups, and filter menus render above surrounding content and do not show transparent overlap.
- Mobile layouts keep tap targets readable and do not collapse card or filter content.
- If CSS/TSX files are edited, run UTF-8 decode and mojibake checks before build.

## 2026-06-13 Map Popup Disabled

Disabled the Google Maps branch popup while keeping marker selection behavior.

- Removed the active `InfoWindowF` import from `MapSection`.
- Commented out the selected-branch popup rendering block.
- Kept marker clicks wired to `onBranchSelect(branch)` so branch list/result filtering still works.
- Left an inline comment showing where to restore the popup if needed later.

Validation:

- UTF-8 decode check for `frontend2/src/components/MapSection.tsx`.
- `npm run build` in `frontend2`.

## 2026-06-13 Course Detail Modal Redesign

Redesigned the Mooncen course detail modal around compact decision-making.

- Replaced the old long vertical detail layout with a header/body/footer modal structure.
- PC body now uses two columns: image/cost/intro summary on the left and title/core facts/extra actions on the right.
- The hero image uses a 16:9 crop with a small horizontal status pill instead of a vertical status strip.
- Brand and branch are displayed above the title with user-facing brand labels such as `홈플`, `롯데`, `AK`, and `갤러리아`.
- The detail title falls back to category-based labels instead of showing `강좌명 미정`.
- Description is clamped to three lines in the main modal; full description and secondary notices open in a separate modal.
- The footer CTA is fixed as `찜하기`, `내 강좌 등록`, and primary `수강신청`.
- Added a four-item summary strip: `수업일시`, `기간`, `횟수`, `수강료`.
- Split `수업일시` from `기간` so the summary card does not repeat the full period inside the schedule field.
- Replaced separate detail boxes with one compact grid for 대상, 강사, 정원, 카테고리, 준비물, 재료비, 모집상태, and 위치.
- Added a focused cost card with total estimated cost, tuition, material fee, and a small material-fee change notice.
- Kept long description text collapsed by default and removed the extra AI info card from the default detail surface to keep the CTA visible.
- Changed the primary CTA label by status: `마감`, `알림 신청`, or `수강신청`.
- Reordered mobile detail content with CSS order only: image, title, summary, cost, intro, details, extras.
- On mobile, the period summary item spans the full summary width and same-year ranges are compacted, for example `2026.06.03 ~ 08.19`.
- Mobile detail CTA now uses a fixed bottom bar with body bottom padding so actions remain visible without covering content.
- No API response shape or route behavior was changed.
- Applied the same compact detail structure to backend SEO course pages generated by `backend/routers/seo_pages.py`.
- SEO course pages now use `수강신청` instead of `원문 보기` for the primary source CTA.

Validation:

- Checked `git status --short`: current workspace is not a git repository.
- Checked `frontend2/package.json`.
- Checked that no `AGENTS.md` exists in the workspace search.
- Verified detail component and data model fields in `CourseDetailModal.tsx`, `mockData.ts`, and `api.ts`.
- UTF-8 decode check for `frontend2/src/components/CourseDetailModal.tsx`, `frontend2/src/styles.css`, and this document.
- UTF-8 decode check for `backend/routers/seo_pages.py`.
- `npm run build` in `frontend2`.
- `python -m py_compile backend/routers/seo_pages.py`.
- Chrome headless screenshot check for rendered desktop/mobile SEO detail HTML.
- Playwright screenshot verification was not run because Playwright is not installed in `frontend2`; Chrome headless was used instead.

## 2026-06-13 Dashboard Resize Stabilization

Stabilized the main dashboard layout across desktop, tablet, and mobile widths.

- Added final responsive CSS rules for the dashboard so the filter strip, branch list, map, selected branch summary, and results stay in normal document flow.
- Split the layout into clear breakpoints:
  - `1200px+`: filter remains one row and the branch/map/summary panels stay in three columns.
  - `901px-1199px`: filter uses two rows and the selected branch panel moves below the map row.
  - `900px 이하`: filter, branch list, collapsed map, selected branch, and results stack vertically.
- Removed the mobile drawer hiding behavior from the dashboard filter so resizing does not leave an invisible filter taking vertical space.
- Added `min-width: 0`, `max-width: 100%`, and mobile-safe wrapper widths to prevent horizontal overflow.
- Kept map/list/result APIs and event wiring unchanged.
- Restored previously mojibake-damaged user-facing filter strings in `Sidebar.tsx`: `오늘`, `전체`, `선택`, `지점`, and `카테고리`.

Validation:

- Selenium layout metrics at desktop/tablet/mobile showed `documentElement.scrollWidth === clientWidth`.
- Selenium screenshots checked at 1440, 1024, 768, and mobile-emulated 390 widths.
- UTF-8 decode check for `frontend2/src/styles.css`, `frontend2/src/components/Sidebar.tsx`, and this document.
- `npm run build` in `frontend2`.

## 2026-06-13 Filter Encoding Cleanup

Cleaned up remaining mojibake in filter-related UI text.

- Fixed the duplicate mini calendar strings in `App.tsx` from corrupted text to `오늘` and `전체`.
- Fixed the map center reference label in `App.tsx` to `지도 중심 기준`.
- Confirmed no remaining known mojibake patterns in `frontend2/src`.

Validation:

- UTF-8 decode and mojibake pattern check for `frontend2/src/App.tsx`, `frontend2/src/components/Sidebar.tsx`, and `frontend2/src/styles.css`.
- `npm run build` in `frontend2`.

## 2026-06-14 Filter Title Restore

Restored a compact `필터` title above the dashboard filter card.

- Added `mooncen-filter-panel-title` in `Sidebar.tsx` outside the filter grid so the existing scope/category/detail columns are not disturbed.
- Added responsive title spacing and font sizing in `styles.css`.
- Kept all filter state and event handling unchanged.

Validation:

- UTF-8 decode and mojibake pattern check for `frontend2/src/components/Sidebar.tsx`, `frontend2/src/styles.css`, and this document.
- `npm run build` in `frontend2`.

## 2026-06-14 Narrow Width Density Optimization

Reduced excess empty space when the main dashboard is resized horizontally.

- Added final responsive CSS overrides for `1200px-1399px`, `1000px-1199px`, and `901px-999px`.
- Kept the filter in one compact row from `1000px` upward so 상세 조건 no longer drops into a large second row at common narrow desktop widths.
- Reduced filter chip, category chip, scope button, and reset button sizing only inside narrow desktop breakpoints.
- Removed fixed `360px/400px` panel heights in narrow desktop layouts so the branch list, map, and selected branch panel shrink with available width.
- Preserved the map card as a 1:1 square inside a bounded map column while allowing the surrounding dashboard to use less vertical space.
- Kept branch list, map, and selected branch summary in one row for `901px-1399px` instead of pushing the summary into a large second row.
- Added compact two-column filter layouts for `641px-900px` and `520px-640px`; true small mobile remains stacked.
- Kept existing APIs, filter state, map events, branch selection, and course result wiring unchanged.

Validation:

- UTF-8 decode check for `frontend2/src/styles.css` and this document.
- `npm run build` in `frontend2`.

## 2026-06-14 Instructor Visibility Fix

Fixed teacher/instructor text being clipped in course cards and detail views.

- Increased the fixed course-card height slightly so an instructor row can fit without hiding the price, branch, or action buttons.
- Reduced compact meta spacing and kept the instructor row as a single ellipsis line.
- Anchored the compare/apply action row to the bottom of the card with `margin-top: auto`.
- Added a detail-modal `course-detail-info-instructor` class so the instructor field spans two grid columns instead of being squeezed into a narrow cell.
- Kept existing API data shape and card/detail event handling unchanged.

Validation:

- UTF-8 decode check for `frontend2/src/components/CourseDetailModal.tsx`, `frontend2/src/styles.css`, and this document.
- Selenium check with an injected instructor row confirmed the course-card instructor, price, branch, and action rows stay inside the card without overflow.
- Selenium detail-modal check confirmed the instructor field spans two columns and the modal does not overflow.
- `npm run build` in `frontend2`.

## 2026-06-14 Empty Title Bracket Cleanup

Removed empty bracket fragments such as `()`, `[]`, `{}`, `（）`, and `［］` from displayed course titles.

- Added `frontend2/src/utils/titleDisplay.ts` with a shared `normalizeCourseDisplayTitle` helper.
- Applied the helper to `displayTitle()` in `mockData.ts` so API course data is normalized before cards, compare panels, detail modals, and URLs use it.
- Applied the same helper to `CourseDetailModal.tsx` fallback title handling.
- Updated backend course API and SEO page title functions to run `title_cleaner.clean_course_title()` even when AI title processing is not active.
- Kept non-empty parentheses intact; only empty bracket pairs and dangling edge punctuation are removed.

Validation:

- UTF-8 decode check for changed frontend/backend files and this document.
- Direct title normalization sample check.
- `npm run build` in `frontend2`.
- `python -m py_compile backend/routers/courses.py backend/routers/seo_pages.py`.

## 2026-06-14 Detail Summary One-Line Schedule

Kept `수업일시` summary text from wrapping into two lines.

- Changed detail summary labels to one-line ellipsis.
- Changed detail summary values to one-line ellipsis and removed `overflow-wrap: anywhere` from the schedule/value text.
- Changed detail summary items from a two-row label/value layout to a compact one-row icon/label/value layout.
- Gave the `수업일시` summary column more width on desktop and full width on mobile.
- Applied the same behavior to SEO course detail pages.

Validation:

- UTF-8 decode check for `frontend2/src/styles.css`, `backend/routers/seo_pages.py`, and this document.
- Selenium detail-modal check for summary label/value line counts.
- `npm run build` in `frontend2`.
- `python -m py_compile backend/routers/seo_pages.py`.

## 2026-06-14 Course Click Update Queue

Added a one-hour course update queue for user course clicks.

- Added `course_update_requests` to `DB/schema.sql` and `DB/migrate_current.sql`.
- A pending request is unique per course and is refreshed to `expires_at = now() + 1 hour` whenever the same course is clicked again.
- Added `POST /courses/{course_id}/update-request` to enqueue detail/source/apply clicks.
- Added `GET /courses/update-requests?active_only=true&limit=100` so operators can inspect the current update list.
- Added `requestCourseUpdate()` in `frontend2/src/api.ts`.
- Connected course detail, original page, and apply clicks in `frontend2/src/App.tsx`.
- The frontend sends the request as fire-and-forget, so update queue failures do not block the user action.
- Extended `tools/maintenance/refresh_course_status.py --from-update-queue` to process only active click update requests and mark them `checked`, `failed`, or `expired`.

Useful commands:

- Inspect active queue: `GET /courses/update-requests?active_only=true&limit=100`
- Process clicked courses: `python tools/maintenance/refresh_course_status.py --from-update-queue --limit 100`
- Dry-run clicked courses: `python tools/maintenance/refresh_course_status.py --from-update-queue --limit 20 --dry-run`

Validation:

- UTF-8 decode and mojibake pattern check for changed backend/frontend/DB/doc files.
- `python -m py_compile backend/models.py backend/routers/courses.py tools/maintenance/refresh_course_status.py`.
- `npm run build` in `frontend2`.

## 2026-06-14 Branch List Emart Icon Containment

Fixed the Emart provider badge escaping the circular icon in the branch list.

- Scoped the fix to `.nearby-center-list` provider icons only.
- Forced branch-list provider icons to use border-box sizing, no padding, fixed 28px square dimensions, circular radius, and hidden overflow.
- Reduced only the Emart branch-list label font size so `이마트` stays inside the circle.
- Kept provider title/aria labels unchanged.

Validation:

- UTF-8 decode and mojibake pattern check for `frontend2/src/styles.css` and this document.
- `npm run build` in `frontend2`.

## 2026-06-14 Detail Filter Button Wiring

Fixed the main `상세필터` button so it opens an actual extra-filter popover instead of toggling the age dropdown.

- Added a dedicated `detailExpanded` state in `Sidebar.tsx`.
- Kept the visible quick filters immediate: 연령, 날짜, 요일, 시간, 모집상태.
- Restored the missing `수강료` filter inside the 상세필터 popover.
- Added outside-click close handling for the 상세필터 popover.
- Added CSS so the popover overlays the filter bar without pushing or resizing the dashboard layout.

Validation:

- UTF-8 decode and mojibake pattern check for `Sidebar.tsx`, `styles.css`, and this document.
- `npm run build` in `frontend2`.

## 2026-06-14 Category Filter Result Fix

Fixed category chips returning no results when the visible Korean category label did not match stored source category values.

- Added `categoryValues` to `ClassItem` so each course keeps all usable category candidates from AI, domain, collection, raw category, source group, and branch category fields.
- Changed client-side category filtering to match against any candidate category value instead of only the display category.
- Added category alias expansion in `App.tsx`; for example `영유아` also matches `Kids & Children`, `With Mom`, `TODDLER`, and related source categories.
- Stopped sending `category=all` to the backend.
- Changed backend `collection_category` filtering to search `collection_category`, `domain_category`, `ai_category`, `source_group`, and `category_raw` together.

Validation:

- UTF-8 decode and mojibake pattern check for `App.tsx`, `mockData.ts`, `courses.py`, and this document.
- `python -m py_compile backend/routers/courses.py`.
- `npm run build` in `frontend2`.
- DB spot check: expanded `영유아` category aliases match 1,618 active courses locally.

## 2026-06-14 Age Filter Result Fix

Fixed age filters returning no results when one age group was selected.

- Added age alias expansion in `App.tsx`; visible labels such as `유아`, `아동`, and `성인` now also send DB codes such as `TODDLER`, `CHILD`, and `ADULT`.
- Changed client-side age filtering to match both the display age group and the raw `target_age_group` code.
- Changed the backend `age_groups` filter to accept both Korean labels and DB code values.
- Added unknown-age handling for `연령 미정` / `UNKNOWN`.

Validation:

- UTF-8 decode and mojibake pattern check for `App.tsx`, `courses.py`, and this document.
- `python -m py_compile backend/routers/courses.py`.
- DB spot check: expanded `유아` age filter aliases match 1,641 active courses locally.
- `npm run build` in `frontend2`.

## 2026-06-14 Exclusive Filter All Checkbox

Changed filter checkbox behavior so `전체` is exclusive.

- In quick filter menus, `전체` now means "no specific filter" instead of checking every item.
- When `전체` is selected, individual option checkboxes are visually unchecked.
- When an individual option is selected from the `전체` state, `전체` is visually unchecked and only that option is checked.
- If the last individual option is unchecked, the filter returns to `전체`.
- Applied the behavior to age, day, time, fee, status, and category filter state handling.

Validation:

- UTF-8 decode check for `Sidebar.tsx`, `App.tsx`, and this document.
- Mojibake pattern check for `Sidebar.tsx`, `App.tsx`, and this document.
- `npm run build` in `frontend2`.

## 2026-06-14 Narrow Width Map Preservation

Fixed the dashboard map disappearing when the viewport width is reduced.

- Overrode the narrow-width map collapse rule so the map remains visible below 900px.
- Changed 641~900px layout to keep branch list and map side by side, with the selected-branch summary compressed below them.
- Reduced branch-list row height, icon size, heading text, and distance/count spacing on narrow widths.
- Simplified selected-branch summary on narrow widths by hiding low-priority rows/actions and keeping only compact key stats.
- Kept mobile map visible at a fixed compact height instead of collapsing to the toolbar.

Validation:

- UTF-8 decode and mojibake pattern check for `styles.css` and this document.
- `npm run build` in `frontend2`.

## 2026-06-14 Aggregate Course Group For All Branches

Reduced mobile scrolling friction by collapsing all-branch results into a single `전체 강좌` group.

- Added aggregate result grouping in `App.tsx` when no specific branch is selected and the branch filter is effectively `전체`.
- Mixed courses across branches with round-robin ordering so the first visible cards are not dominated by one branch.
- Kept branch-specific grouping for explicitly selected branch filters.
- Added a compact `전체` group badge instead of rendering a fake provider logo for the aggregate group.
- Disabled the old "load more pages to get more branch groups" effect while aggregate grouping is active.

Validation:

- UTF-8 decode and mojibake pattern check for `App.tsx`, `styles.css`, and this document.
- `npm run build` in `frontend2`.

## 2026-06-14 Separate Mobile Home Page

Added a separate mobile-first home page based on `script1.ps1` guidance.

- Added lightweight SPA page detection using `?page=mobile` and `?page=branches`.
- Mobile viewport now renders a dedicated mobile home instead of the full branch/map dashboard.
- The mobile home keeps search context, culture/education scope selection, quick actions, category chips, nearby branch CTA, highlights, and course cards.
- The full branch list, map, and selected-branch summary remain available through `?page=branches`.
- Used query-based navigation because `/branches` is proxied to the backend API in `vite.config.ts`.
- Kept existing API calls, course cards, favorite/apply/compare/detail handlers, and login flow unchanged.

Validation:

- UTF-8 decode and mojibake pattern check for `App.tsx`, `styles.css`, and this document.
- `npm run build` in `frontend2`.
- Dev server checks: `/?page=mobile` and `/?page=branches` both returned HTTP 200.

## 2026-06-14 Detail Filter Title Placement

Added the requested `필터` title directly above the age/date/day/time/status control group.

- Inserted a `필터` label inside the detailed filter section in `Sidebar.tsx`.
- Overrode the previous CSS rule that hid `.mooncen-filter-detail > .mooncen-filter-title`.
- Kept the controls and action buttons on the next row so the filter strip remains aligned.

Validation:

- UTF-8 decode and mojibake pattern check for `Sidebar.tsx`, `styles.css`, and this document.
- `npm run build` in `frontend2`.

## 2026-06-14 Mobile Category Icon Containment

Fixed category SVG artwork escaping from mobile category chips.

- Added `overflow: hidden` to the shared `.category-icon` container and SVG.
- Added final mobile-specific containment for `.mobile-category-scroll .category-icon`.
- Reduced mobile category SVG size to keep large artwork such as the art/palette icon inside the circular chip.

Validation:

- UTF-8 decode and mojibake pattern check for `styles.css` and this document.
- `npm run build` in `frontend2`.

## 2026-06-14 Remove Mobile Home Category SVG Icons

Removed the mobile home category SVG icons because the artwork could still render oversized on the mobile category strip.

- Removed `CategoryIcon` rendering from the mobile home category chips in `App.tsx`.
- Kept desktop/dashboard and sidebar category icons unchanged.
- Mobile home category chips are now text-only to avoid oversized icon overlap.

Validation:

- UTF-8 decode and mojibake pattern check for `App.tsx` and this document.
- `npm run build` in `frontend2`.

## 2026-06-14 Remove Dashboard Filter Category SVG Icons

Confirmed the oversized icon was the `미술` palette SVG rendered by `CategoryIcon` inside the dashboard filter category chips.

- Removed `CategoryIcon` rendering from `Sidebar.tsx` category filter chips.
- Removed the unused `CategoryIcon` import from `Sidebar.tsx`.
- Added a final CSS safety rule so dashboard/mobile filter category chips remain text-only even if a category icon component is rendered by another path.
- Kept category filter values and click behavior unchanged.

Validation:

- UTF-8 decode and mojibake pattern check for `Sidebar.tsx`, `styles.css`, and this document.
- `npm run build` in `frontend2`.

## 2026-06-14 Remove Remaining Quick Category SVG Path

Removed the last category SVG usage path after the oversized palette icon still appeared in the category strip.

- Removed `CategoryIcon` rendering from `QuickCategoryChips.tsx`.
- Added a global final safety rule so `.category-icon` is hidden if any stale or alternate category markup is still rendered.
- Verified locally that `/`, `/?page=mobile`, and `/?page=branches` render no category SVG icons and no large SVG elements in the category area.

Validation:

- UTF-8 decode and mojibake pattern check for `QuickCategoryChips.tsx`, `styles.css`, and this document.
- `npm run build` in `frontend2`.

## 2026-06-14 Scope Icon Image Containment

Confirmed the remaining oversized artwork was not a category SVG. It was the exploration-scope image rendered by `ScopeIcon`.

- Added `scope-icon-img` to the `ScopeIcon` image element.
- Constrained all scope icon images to a 22~24px rendered size.
- Overrode the global absolute-positioned `.scope-icon` rule inside mobile scope tabs and the dashboard filter scope selector.
- Kept the culture/education scope click behavior unchanged.

Validation:

- UTF-8 decode and mojibake pattern check for `ScopeIcon.tsx`, `styles.css`, and this document.
- `npm run build` in `frontend2`.

## 2026-06-14 Compact Selected Branch Summary

Reduced the selected-branch summary card size on narrow screens.

- Shortened secondary action labels in `NearbyCenterMap.tsx`.
- Added final responsive CSS that compresses the selected branch title, statistics, and action buttons below `900px`.
- Removed the large vertical feel by hiding distance/closed duplicate stats and showing current/open/urgent counts in one compact row.
- Kept branch selection, directions, branch change, and full-branch course actions wired to the same handlers.

Validation:

- UTF-8 decode and mojibake pattern check for `NearbyCenterMap.tsx`, `styles.css`, and this document.
- `npm run build` in `frontend2`.

## 2026-06-14 Progressive Course Group More Button

Fixed the course group footer so clicking `더보기` once does not leave only a close button.

- Replaced the single expanded-group key with per-group visible counts in `App.tsx`.
- Default group preview remains 4 cards.
- Each `더보기` click increases that group's visible count by 20.
- If hidden courses remain, the footer continues to show `더보기 (N개 더)`.
- `접기` is shown only as a secondary action after a group has expanded.
- Reset group visible counts when the course query changes.

Validation:

- UTF-8 decode and mojibake pattern check for `App.tsx`, `styles.css`, and this document.
- `npm run build` in `frontend2`.

## 2026-06-14 Stable Branch List Counts

Fixed branch-list counts changing when a branch is clicked.

- Changed `NearbyCenterMap` branch-list count rendering to use stable branch data (`active_course_count` / `course_count`) instead of `visibleItems`-derived counts.
- The branch list no longer recalculates its displayed count from `selectedBranch`-scoped course results.
- Kept the selected-branch summary card using current-condition counts.
- Kept branch selection, map selection, and course result filtering unchanged.

Validation:

- UTF-8 decode and mojibake pattern check for `NearbyCenterMap.tsx` and this document.
- `npm run build` in `frontend2`.

## 2026-06-15 Mobile Viewport Stabilization

Fixed narrow mobile layout overflow and overlapping UI.

- Added final mobile CSS overrides for widths, max-widths, and box sizing across the mobile home, filter, branch list, map, summary, and result sections.
- Compressed the mobile dashboard filter so scope, category, and quick filters stay inside the viewport without horizontal page overflow.
- Kept category chip rows horizontally scrollable while preventing them from expanding the document width.
- Made the branch list, map card, selected-branch summary, and course cards stack cleanly on narrow screens.
- Reduced the mobile toast to a single-line 38px overlay so it does not cover large portions of the page.
- Kept existing API calls, filter state, branch selection, map behavior, and course card actions unchanged.

Validation:

- Selenium mobile emulation at 390px for `/` and `/?page=branches`: `scrollWidth` equals viewport width and no non-map overflow nodes.
- `npm run build` in `frontend2`.

## 2026-06-15 Mobile Header Scroll Containment

Removed the internal scrollbar from the mobile header.

- Forced `.site-header` and `.header-inner` to use hidden overflow below `900px`.
- Set explicit mobile header row heights for the logo/action row and search row.
- Prevented the utility navigation from becoming an internal horizontal or vertical scroll container.
- Kept the existing header search, logo click, favorite, applied-course, notification, and login actions unchanged.

Validation:

- Selenium mobile emulation at 390px: no scrollable elements inside `.site-header`.
- `npm run build` in `frontend2`.
