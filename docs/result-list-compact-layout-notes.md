# Result List Compact Layout Notes

## 2026-05-28

The course result list now uses a branch-first accordion layout.

- Collapsed branch rows show only the cultural center/provider, branch name, course count, open/closed counts, age groups, categories, and lowest price.
- Course cards are rendered only when the branch row is expanded.
- Expanded course items use a compact row layout instead of image-heavy cards.
- Images, instructor, branch repetition, and extra metadata are hidden in branch result rows to keep more courses visible.
- Mobile keeps the same branch-first structure and uses compact rows with title, target, schedule, price, and action.

This layout is intended to make several cultural centers visible on one screen before the user chooses a specific branch to inspect.

## 2026-05-28 Branch Preview Rows

Collapsed branch groups now show representative courses.

- Each collapsed branch shows up to two course preview rows.
- Preview rows include status, title, target, schedule, and fee.
- Clicking a preview row opens the course detail modal.
- Expanded branch groups still show the full compact course list.

## 2026-05-28 Expanded Row Stabilization

Expanded branch groups now have a final CSS override to avoid conflicts with older fixed-height card rules.

- Expanded courses are forced into one-column compact rows.
- Thumbnail blocks are hidden in expanded branch rows.
- Title, target, schedule, and fee use explicit grid areas.
- Mobile keeps the same compact row layout with adjusted columns.

## 2026-05-28 Readability Fix

Branch result rows were adjusted after the compact layout became too dense.

- Collapsed preview rows now show a small course image or fallback thumbnail.
- Status labels are shortened to 접수, 예정, 임박, 대기, or 마감 so they do not dominate the row.
- Expanded branch rows show course images again.
- Expanded course titles are no longer clamped, so long titles can wrap instead of being cut off.

## 2026-05-28 Desktop Expanded Grid

Expanded branch groups now use a responsive desktop grid.

- Desktop widths show four compact course cards per row.
- Mobile and tablet widths keep a single-column course list.
- Expanded course cards use a tighter internal layout so images, title, target, schedule, and fee fit inside each grid cell.
- Expanded course titles are clamped to two lines to reduce vertical spacing.
- The course action button label is shown as 신청 in result cards.
- Text inside expanded desktop cards is larger while row gaps and line height are reduced for higher information density.

## 2026-05-28 Collapsed Scroll Grid

Collapsed branch groups now use the same course card layout as expanded groups.

- Collapsed branch groups show course cards in the same dense grid style.
- Collapsed groups keep a fixed vertical area with internal vertical scrolling.
- Expanded groups remove the internal scroll and show the full course list.
- Desktop keeps four course cards per row in both collapsed and expanded states.

## 2026-05-28 Branch Group Separation

Branch groups now have stronger visual boundaries.

- Each branch group has a visible border, shadow, and colored left accent line.
- Header backgrounds differ between collapsed and expanded states.
- Group spacing was increased so adjacent branches are easier to distinguish.
- Course grids use a subtle inner background to separate group content from the page.

## 2026-05-28 Long Target And Price Stabilization

Dense branch course cards now reserve space for fee text and cap long target labels.

- Target labels inside branch course cards use a two-column mini layout: label + value.
- Long target values are clamped to two lines, preventing three-line target blocks from pushing the footer down.
- Fee text is non-wrapping and does not shrink, so values like `100,000원` or `수강료 확인` are not clipped.
- The action button keeps a small fixed minimum width on desktop and mobile.

## 2026-05-28 Three Column Desktop Grid

Desktop branch course grids now show three cards per row instead of four.

- Collapsed and expanded branch groups use the same three-column grid on desktop.
- Row spacing was increased slightly so cards do not feel cramped after target and fee stabilization.
- Card fonts were raised slightly for title, target, schedule, fee, and action text now that each row has more horizontal space.

## 2026-05-28 Collapsed Preview And Filter Expansion

Branch groups now keep collapsed results shorter while making filtered results easier to scan.

- Collapsed desktop branch groups render only the first row, which is three course cards.
- Collapsed tablet/mobile branch groups render one preview card.
- A bottom expand button appears when more courses are hidden.
- Search, explicit filters, selected branch, favorites, and my-course views force all branch groups into expanded mode.
- The default closed-course exclusion does not count as an explicit filter for auto-expansion.

## 2026-05-28 Branch Header Quick Filters

Branch result headers now show compact controls instead of passive summary chips.

- The previous open/closed/category/price summary chips were removed from each branch header.
- Quick filters were added beside the branch name for age, date, day, and time bucket.
- The branch title and expand button remain separate buttons so filter controls do not accidentally toggle the branch.
- Mobile stacks the quick filters into two columns, then one column on very narrow screens.

## 2026-05-28 Subdued Quick Filter Chips

The branch header quick filters were visually too heavy when rendered as full select/date inputs.

- Quick filters now render as muted dashed chips without the crossed-out text treatment.
- Clicking a chip opens a small local menu for age, date, day, or time selection.
- The menu closes after a selection, keeping the branch header visually close to a passive information row.
- The branch group allows overflow so chip menus are not clipped by the card boundary.
- The age filter's all-age course option is labeled `전연령` so it does not duplicate the filter reset option `전체`.
- The date chip now opens the same local quick-filter menu pattern as the other chips instead of using the browser-native date input.
- Open quick-filter menus close when the user clicks or taps outside the active quick filter.

## 2026-05-28 Custom Quick Date Calendar

The branch header date filter now uses a compact in-app calendar.

- The custom calendar shows a month grid with previous/next month navigation.
- Today and the selected date have distinct visual states.
- `오늘` and `전체` actions are available inside the calendar footer.
- Selecting a date or clearing the filter closes the quick-filter popover immediately.

## 2026-05-28 Main Quick Filter Bar

The primary filter bar now matches the compact quick-filter chip pattern.

- The main filter order is now culture center, displayed branch, age, date, day, and time.
- Category, fee, and recruitment status are now shown directly in the main filter row.
- The main date filter uses the same custom mini calendar interaction.
- Desktop keeps the main filters in a single horizontal row with overflow scrolling when needed.
- The duplicate detail-filter button in the filter header was removed; only the detail-filter chip remains.
- The remaining detail-filter chip and panel were removed after shrinking main chips to near branch quick-filter size.

## 2026-05-28 Course Card Readability Pass

Branch result cards now separate title, target, and price more clearly.

- Course titles use a slightly heavier weight and more readable line height.
- A divider below the title separates the title from target information.
- Target text no longer uses the colored badge treatment, making it feel like normal course metadata.
- The target label is vertically centered against the target value.
- Price now renders as a labeled `수강료` block beside the action button for more consistent alignment.
- The extra divider above the price block was removed so schedule and price do not create stacked lines.
- Target, schedule, and price labels/values now share a consistent color and type scale.
- Target, schedule, and price rows now share the same label-column width and left padding so their values start from the same visual baseline.
- Course-card schedule renders date and time on one line for single-day courses, but keeps period schedules on two lines with the period above the time.
- Course-card metadata order is target, schedule, instructor, fee, then secondary branch/category details.
- Target, schedule, instructor, and fee rows use the same two-column label/value rhythm so values keep the same left alignment.
- Schedule rows now use the same transparent row padding as target, instructor, and fee rows instead of the older highlighted priority box.
- Instructor names are rendered as their own `dt/dd` row, aligned with schedule and fee rows.
- Instructor display strips a duplicated leading `강사` from the value because the row label already says `강사`.
- The compact-card CSS explicitly keeps the instructor row visible because older dense-layout rules hide non-schedule metadata rows.
- Course cards now have a `비교` action beside `신청`. Up to four selected courses are shown in a comparison table above the result list.
- Comparison table columns use a fixed table layout so each selected course has the same width regardless of title or field length.
- Mobile comparison uses a transposed table: each selected course is a row and comparison fields are columns, so multiple selected courses remain visible vertically.
- On mobile course cards, the fee row uses the full card width and action buttons move below it so long won amounts are not clipped.

## 2026-05-28 Branch Local Subfilters

Branch header quick filters now apply only to the branch group where they are changed.

- Age, date, day, and time quick filters no longer mutate the global main filter state.
- Each branch group stores its own temporary subfilter values.
- A filtered branch group shows `filtered/total` course count.
- When a branch subfilter has no matches, the empty state is displayed inside that branch group only.

## 2026-06-02 Branch Header Total Count

Branch group headers now prefer full branch/API counts instead of only rendered card counts.

- A selected branch uses the course API `total`, so the header count is not limited by the first loaded page.
- Non-selected branch groups use branch metadata counts, preferring `active_course_count` in normal mode and `course_count` in debug mode.
- Favorite and my-course views keep using the rendered saved-item count because those views are not full branch result sets.
- Branch-local subfilters still show `filtered/total`, with the total coming from the full-count source where available.
