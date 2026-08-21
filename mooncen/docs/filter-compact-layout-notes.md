# Filter Compact Layout Notes

## 2026-06-01 Mobile Filter And Compare

- Mobile quick filters are forced into one horizontal row with fixed compact chips.
- Active filter chips stay hidden on mobile so they do not create a second filter row.
- Mobile compare view reflows the transposed comparison table into stacked cards.
- Compare titles are clamped to two lines and field labels are shown beside each value, reducing horizontal overflow.

## 2026-06-01 Mobile UX Optimization

- Mobile header is reduced to a compact two-row layout: logo and user actions on the first row, search on the second row.
- Mobile map height uses viewport-based limits so the first screen leaves room for filters and results.
- Branch group headers, branch quick filters, and course cards are tightened for one-hand scanning.
- Collapsed branch groups keep a short vertical preview area; expanded groups show the full list without internal scrolling.
- Detail modals use a bottom-sheet layout with sticky actions and safe-area padding for mobile browsers.

## 2026-05-28

The frontend filter area now separates high-frequency filters from advanced filters.

- Quick filters stay visible: provider, age, category, date, day, and closed-course exclusion.
- Advanced filters are grouped under `상세 필터`: visible branches, time bucket, fee, and full status.
- Active filter chips are shown below the quick filters and can remove each applied condition directly.
- The mobile filter button shows how many filters are currently active.
- Mobile keeps the bottom-sheet filter interaction, but advanced filters remain collapsed until needed.

The goal is to keep map and branch-group results visible while still allowing detailed filtering when required.

## 2026-05-28 One-Line Desktop Bar

The desktop filter bar is now forced into a single row.

- Quick filter buttons no longer wrap; the row scrolls horizontally when the viewport is narrow.
- Active filter chips are hidden on desktop to keep the bar height to one row.
- Advanced filters open as an overlay dropdown instead of pushing the page downward.
- The selected branch notice is hidden inside the desktop filter bar so it does not create another row.

## 2026-05-28 Summary Bar

The filter UI now defaults to a single summary row instead of showing every filter button.

- The row shows `필터 N개 적용중` and a short summary such as `홈플러스 · 유아 · 토요일 외 1개`.
- Filter controls are rendered only after pressing `변경`.
- The summary text is ellipsized, so the bar does not get clipped or wrap into multiple rows.
