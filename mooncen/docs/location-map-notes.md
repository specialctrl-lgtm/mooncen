# Location And Map Startup Notes

## 2026-05-28 Faster Refresh Location

The map previously rendered with the default Seoul City Hall center before browser geolocation completed.

- The frontend stores the last detected location in `sessionStorage` for 30 minutes.
- On refresh, the cached location is used immediately so the map starts near the user's previous location.
- Browser geolocation runs only after the user selects `내 위치 사용`.
- A new current location clears the map search center so an old map center cannot keep branch searches pinned elsewhere.
- Location acquisition checks a denied site permission before requesting the device position. It first accepts a recent balanced-accuracy position for up to 12 seconds, then retries once with high accuracy for up to 20 seconds.
- Only the final failure is shown. Permission denial, unavailable location, timeout, and insecure HTTP are reported separately.

## 2026-05-29 Map Tab Semantics

The map tabs were simplified from implementation terms to user-facing collection groups.

- The old `기본 지도` tab is now labeled `문화센터`.
- The old `카테고리 지도` tab is now labeled `기타`.
- `문화센터` shows only culture-center branches:
  - `HOMEPLUS`
  - `LOTTE`
  - `EMART`
  - `HYUNDAI_DEPT`
  - `GALLERIA`
  - `AK_PLAZA`
  - `ELAND_RETAIL`
  - `SHINSEGAE_ACADEMY`
  - `LOTTE_MART`
  - branches whose collection category is `문화센터`
- `기타` shows only non-culture-center branches and keeps the collection-category legend.
- Switching map tabs clears the selected branch and resets provider filters so a previously selected branch/provider does not leave the opposite tab empty.
