# Frontend Main Page Staged Redesign

## 변경일
- 2026-06-09

## 목표
- 기존 API, 라우팅, 데이터 구조를 변경하지 않고 메인 페이지 탐색 흐름을 개선한다.
- 기존 검색, 필터, 지도, 신청, 비교, 찜 기능은 유지한다.
- MoonCen의 민트/화이트/라운드 디자인 톤을 유지한다.

## 적용 내용
- `SearchHero` 추가
  - 검색어, 연령, 카테고리, 지역 선택 후 기존 필터 state에 반영한다.
- `QuickCategoryChips` 추가
  - 전체보기, 영유아, 미술, 음악, 체육, 요리, 어학, 과학, 코딩 빠른 선택을 제공한다.
- `NearbyCenterMap` 추가
  - 왼쪽 문화센터 리스트와 오른쪽 기존 Google Map을 좌우 배치한다.
  - 모바일에서는 지도 영역을 기본 접힘으로 두고 `지도 보기` 버튼으로 펼친다.
  - 반경 선택 UI는 5km, 10km, 20km를 제공하며 기존 `MapSection.viewRadiusKm`에 연결한다.
- `HighlightSections` 추가
  - 이번주 인기 강좌, 신규 등록 강좌, 곧 마감되는 강좌, 알림 안내를 표시한다.
- Recommended Courses 영역 정리
  - 기존 결과 그룹과 `ClassCard` 동작은 유지한다.
  - 정렬 옵션은 인기순, 최신순, 마감임박순 중심으로 정리했다.
- CourseCard 표시 개선
  - 이미지 영역을 유지하고 펼친 상태에서도 카드형 UI를 사용한다.
  - 비용은 총 비용 중심으로 표시하고 수강료/재료비를 보조 정보로 함께 표시한다.

## 2026-06-09 상단/지도 섹션 UX 보강
- 범위
  - 메인 상단, 빠른 카테고리, 문화센터/교육·체험 탭, 내 주변 문화센터 지도 섹션만 수정했다.
  - 강좌 카드 리스트는 이번 단계에서 변경하지 않았다.
- `SearchHero`
  - 2컬럼 Hero 구조로 변경했다.
  - 좌측에는 안내 문구, 제목, 설명, 연령/카테고리 선택, 검색/상세 검색 버튼을 유지했다.
  - 우측에는 `/assets/characters/moon-cen-main.png` 캐릭터 영역을 추가했다.
  - 이미지가 없거나 로드 실패해도 placeholder가 표시되도록 처리했다.
- `QuickCategoryChips`
  - 아이콘 원형 배경 크기를 통일했다.
  - 선택 상태는 민트 배경과 테두리로 구분한다.
  - 모바일에서는 가로 스크롤로 대응한다.
- `NearbyCenterMap`
  - 제목, 반경 정보, 반경 선택 버튼의 시각 계층을 정리했다.
  - 좌측 지점 리스트에 지점명, provider label, 강좌 수를 분리 표시한다.
  - 리스트 hover/selected 상태를 민트 톤으로 명확히 했다.
  - 모바일에서는 지도를 기본 접힘으로 두고 `지도 보기` 버튼으로 펼친다.
- CSS 토큰
  - primary, primaryDark, primaryLight, bg, card, border, text, muted, danger, warning 색상을 CSS 변수로 정리했다.
  - radius와 shadow 토큰도 추가했다.

## 2026-06-09 Moon & Cen 브랜드 반영
- `docs/brand/moon-cen.md`에 Moon & Cen 브랜드 가이드를 추가했다.
- Hero 문구를 브랜드 가이드와 메인 UX 요구사항에 맞춰 조정했다.
  - `우리 동네 강좌를 빠르게 비교하세요`
  - `Moon과 Cen이 아이에게 맞는 가까운 강좌를 찾아드릴게요.`
- Hero 우측 캐릭터 영역은 `/assets/characters/moon-cen-main.png`를 우선 사용한다.
- 캐릭터 이미지 후보 경로는 `/assets/characters/moon-cen-main.png`, `/public/assets/characters/moon-cen-main.png`, `/images/characters/moon-cen-main.png` 순서로 시도한다.
- 캐릭터 이미지가 없는 경우에도 `Moon & Cen 캐릭터 이미지 영역` placeholder가 표시되도록 유지했다.
- 브랜드 색상 기준으로 민트/화이트 중심에 Accent Yellow, Accent Coral을 보조 포인트로 적용했다.

## 2026-06-09 Hero/지도 밸런스 조정
- Header, API, 검색, 필터, 지도 동작, 강좌 카드 리스트는 변경하지 않았다.
- Hero 레이아웃을 PC 기준 55% / 45% 2컬럼으로 조정했다.
- Hero 최소 높이는 280~320px 범위로 조정하고 제목 크기는 38~42px 범위로 제한했다.
- 캐릭터 이미지는 우측에서 300~340px 범위로 표시되도록 조정했다.
- 검색 조건 영역은 PC에서 한 줄을 유지하도록 `flex-wrap: nowrap` 기반으로 정리했다.
- 모바일에서는 검색 조건을 2열 그리드로 전환해 Hero가 과도하게 길어지지 않도록 했다.
- 빠른 카테고리 영역은 padding, gap, 버튼 높이를 줄이고 아이콘은 42~44px 범위로 통일했다.
- 문화센터/교육·체험 탭은 `NearbyCenterMap`의 `modeTabs` 슬롯으로 이동해 지도 카드 헤더와 시각적으로 연결했다.
- 지도 섹션은 좌측 리스트 / 우측 지도 비율을 32% / 68%에 가깝게 조정했다.
- 리스트 스크롤바는 얇고 연한 민트 톤으로 유지했다.

## 2026-06-09 검색 결과 우선 메인 구조
- 메인 페이지를 홍보형 랜딩이 아니라 검색 결과가 먼저 보이는 구조로 재정리했다.
- `SearchHero`는 CompactSearchHero 역할이 되도록 PC 기준 약 170px 수준으로 축소했다.
- Moon & Cen 캐릭터는 Hero 우측의 보조 안내 요소로만 유지하고 결과 영역을 밀어내지 않도록 크기를 줄였다.
- Hero 검색 조건 영역은 PC에서 한 줄을 유지하도록 조정했다.
- 빠른 카테고리는 높이를 줄이고 모바일 가로 스크롤은 유지했다.
- `HighlightSections`를 지도와 검색 결과 사이에서 제거하고 검색 결과 섹션 뒤로 이동했다.
- 지도 바로 아래에 `results-section`이 이어지도록 배치했다.
- 결과 섹션 제목을 `검색 결과`로 변경했다.
- 기존 API, 검색, 필터, 지도, 마커, 반경 선택, 강좌 카드 클릭/신청/비교/찜 기능은 변경하지 않았다.

## 2026-06-09 Hero 상단 타입 탭 이동
- 문화센터/교육·체험 탭을 `NearbyCenterMap` 헤더에서 `SearchHero`의 `search-hero-main` 상단으로 이동했다.
- 기존 `mapMode`, provider filter, selected branch 초기화 로직은 그대로 유지했다.
- 지도 섹션 헤더는 제목/반경 선택 중심의 단순한 2컬럼 구조로 되돌렸다.

## 2026-06-10 타입 탭 결과 필터 보정
- 문화센터/교육·체험 탭의 `mapMode`가 지도뿐 아니라 강좌 결과 목록에도 적용되도록 수정했다.
- 문화센터 탭에서는 문화센터 provider 또는 `문화센터` collection category 강좌만 표시한다.
- 교육·체험 탭에서는 문화센터로 분류되지 않는 강좌만 표시한다.
- 찜/내강좌 목록에서도 동일한 탭 기준을 적용한다.

## 2026-06-10 탐색 범위 정보 구조 개선
- `SearchTypeSelector` 컴포넌트를 추가했다.
- 문화센터/교육·체험을 작은 pill 탭이 아니라 1차 탐색 범위 카드로 분리했다.
- `SearchTypeSelector`는 `SearchHero` 아래, `QuickCategoryChips` 위에 배치했다.
- 탐색 범위 섹션 제목은 `탐색 범위`, 카테고리 섹션 제목은 `카테고리`로 표시한다.
- 문화센터 선택 시 카테고리:
  - 전체보기, 영유아, 미술, 음악, 체육, 요리, 어학, 과학, 코딩
- 교육·체험 선택 시 카테고리:
  - 전체보기, 공공강좌, 도서관, 박물관, 과학관, 체험행사, 원데이, 방학특강
- `NearbyCenterMap` 제목은 선택 상태에 따라 `내 주변 문화센터` 또는 `내 주변 교육·체험`으로 표시한다.
- 검색 결과 보조 문구는 선택 상태에 따라 `조건에 맞는 문화센터 강좌` 또는 `조건에 맞는 교육·체험 프로그램`으로 표시한다.

## 2026-06-10 헤더 정렬 체계 보정
- `Header` 내부 컨테이너에 `header-inner` 클래스를 추가했다.
- 헤더 max-width를 본문과 같은 `1240px` 기준으로 맞추고 좌우 padding을 `24px`로 정리했다.
- 헤더 배경은 반투명 흰색, 민트 계열 border, 약한 blur로 조정해 본문 카드 영역과 같은 디자인 시스템 안에 보이도록 했다.
- 로고는 높이 46px 기준으로 제한하고, 검색창은 430px 이하의 pill 형태로 정렬했다.
- 모바일에서는 로고/사용자 메뉴를 첫 줄, 검색창을 두 번째 줄 전체폭으로 배치해 가로 overflow를 막는다.
- 검색, 로그인, 찜, 알림 이벤트 구조는 변경하지 않았다.

## 2026-06-10 브랜드 색상 통일
- UI에서 보라색, 바이올렛, 인디고 계열 색상 토큰과 직접 색상값을 제거했다.
- 문화센터/교육·체험 선택 카드의 active 상태는 모두 `#CCFBF1` 배경, `#14B8A6` border, `#0F766E` 텍스트 기준을 따른다.
- 빠른 카테고리 아이콘은 민트/노랑/코랄/중립 계열로 정리했다.
- 지도 마커는 provider/category 색상 구분 없이 MoonCen 민트/틸 계열로 통일했다.
- 신규 배지, 소스 배지, 카드 썸네일의 보라 계열 포인트를 민트/노랑/코랄 계열로 교체했다.
- API, 필터, 지도, 검색, 로그인 로직은 변경하지 않았다.

## 2026-06-10 검색 결과형 대시보드 레이아웃
- 상단 `SearchHero`, `SearchTypeSelector`, `QuickCategoryChips` 렌더링을 제거했다.
- Header 검색창, 로그인, 찜, 내강좌, 알림 기능은 유지했다.
- 메인 상단의 `dashboard-notice-bar`도 제거해 Header 아래에 대시보드가 바로 시작되게 했다.
- 문화센터/교육·체험 선택은 `Sidebar`의 `탐색 범위` 필터로 이동했다.
- `Sidebar`는 카테고리, 연령, 요일, 시간, 수강료, 접수상태, 조건 적용, 초기화를 포함하는 좌측 필터 패널 역할을 한다.
- PC 레이아웃은 좌측 필터 + 우측 지점 목록/지도 + 하단 강좌 결과 구조로 조정했다.
- `NearbyCenterMap`은 `지점 목록`과 `지도` 영역을 분리하고, 반경 선택 버튼을 지도 툴바에 배치했다.
- 모바일에서는 필터를 drawer로 열고, 지도는 `지도 보기` 버튼으로 펼치는 구조를 유지한다.
- API, 검색, 필터, 지도, 반경 선택, 마커 클릭, 강좌 카드 클릭/신청/비교/찜 기능은 변경하지 않았다.

## 2026-06-10 대시보드 레이아웃 표시 보정
- 첨부 레퍼런스 기준으로 Header 아래에 바로 `필터 / 지점 목록 / 지도` 3컬럼이 보이도록 보정했다.
- 이전 CSS의 `order`와 `display:none` 규칙이 데스크톱 레이아웃에 남아 필터가 본문 오른쪽으로 밀리는 문제를 수정했다.
- Header max-width를 본문과 같은 `1560px` 기준으로 맞춰 로고, 검색창, 본문 카드의 좌우 기준선을 정렬했다.
- `dashboard-notice-bar` 렌더링을 제거해 Hero/빠른 안내 영역 없이 결과형 대시보드가 바로 보이게 했다.
- 사이드바 카테고리는 원본 DB 카테고리 대신 탐색 범위별 화면용 라벨을 우선 표시한다.
  - 문화센터: 전체, 영유아, 미술, 음악, 체육/스포츠, 요리, 어학, 박물관/과학관, 코딩
  - 교육·체험: 전체, 공공강좌, 도서관, 박물관/과학관, 체험행사, 원데이, 방학특강, 복지관, 수목원/생태
- 추천/알림 카드는 검색 결과 아래로 내려 지도 다음에 검색 결과가 바로 이어지게 했다.
- 인코딩 깨짐을 피하기 위해 CSS pseudo icon의 비ASCII 문자를 ASCII 기반 표기로 교체했다.

## 2026-06-10 Header 사용자 메뉴 아이콘화
- Header 우측 `찜`, `내강좌`, `알림`, `로그인/로그아웃` 메뉴를 문자 기호 대신 inline SVG 아이콘으로 변경했다.
- 찜은 heart, 내강좌는 document, 알림은 bell, 로그인은 user, 로그아웃은 exit 형태로 표시한다.
- 데스크톱에서는 아이콘과 텍스트를 함께 보여주고, 모바일에서는 4개 메뉴를 고정 폭 아이콘 버튼으로 표시한다.
- 모바일에서 앞쪽 아이콘이 스크롤 영역 밖으로 밀려 로그인만 보이던 문제를 `max-width: 640px` 헤더 규칙으로 보정했다.

## 2026-06-10 Header 사용자 메뉴 PNG 아이콘 적용
- inline SVG 아이콘이 깨지거나 이상하게 보이는 문제를 피하기 위해 실제 PNG 아이콘 asset을 생성했다.
- Header는 `/assets/icons/nav-*.png` 파일을 직접 참조한다.
- 생성된 아이콘:
  - `/assets/icons/nav-heart.png`
  - `/assets/icons/nav-course.png`
  - `/assets/icons/nav-bell.png`
  - `/assets/icons/nav-user.png`
  - `/assets/icons/nav-logout.png`
- 데스크톱과 모바일 헤더에서 PNG 아이콘 표시를 Playwright 캡처로 확인했다.

## 2026-06-10 탐색 범위 PNG 아이콘 적용
- 좌측 필터의 `문화센터`, `교육·체험` 탐색 범위도 문자 기반 아이콘 대신 직접 생성한 PNG 아이콘을 사용한다.
- 생성된 아이콘:
  - `/assets/icons/filter-culture-center.png`
  - `/assets/icons/filter-education-experience.png`
- `Sidebar`의 탐색 범위 버튼에 PNG icon image를 추가하고 기존 `C/E` pseudo text 아이콘은 제거했다.
- 데스크톱 필터 영역에서 아이콘 표시를 Playwright 캡처로 확인했다.

## 2026-06-10 탐색 범위 아이콘 텍스트 제거 및 재생성
- 첨부 레퍼런스처럼 이미지 안에 텍스트가 포함되는 방식은 사용하지 않고, 아이콘 PNG는 그림만 포함하도록 재생성했다.
- `문화센터` 아이콘은 건물만 남기고, `교육·체험` 아이콘은 팔레트/붓 형태로 변경했다.
- 버튼 라벨 텍스트는 이미지가 아니라 HTML 텍스트로 별도 표시한다.
- 데스크톱 필터 영역에서 텍스트 없는 아이콘 표시를 Playwright 캡처로 확인했다.
- 탐색 범위 버튼 내부를 grid 정렬로 보정해 `문화센터`, `교육·체험` 텍스트가 아이콘 기준 세로 중앙에 오도록 수정했다.
- 필터 헤더의 `초기화`는 아이콘이 작고 어색하게 보이는 문제가 있어 텍스트 버튼으로 단순화하고, 제목 오른쪽 중앙 정렬로 보정했다.

## 2026-06-10 지도 마커 PNG 아이콘 적용
- 지도 마커도 SVG data URL 생성 방식 대신 직접 생성한 PNG asset을 사용한다.
- 강좌 수 숫자는 Google Maps `MarkerF` label로 유지해 필터 변경 시 계속 동적으로 갱신된다.
- 생성된 마커 asset은 기본/선택/비활성 상태와 찜/마감임박 조합을 포함한다.
  - `/assets/icons/map-marker-default.png`
  - `/assets/icons/map-marker-default-favorite.png`
  - `/assets/icons/map-marker-default-urgent.png`
  - `/assets/icons/map-marker-default-favorite-urgent.png`
  - `/assets/icons/map-marker-selected.png`
  - `/assets/icons/map-marker-selected-favorite.png`
  - `/assets/icons/map-marker-selected-urgent.png`
  - `/assets/icons/map-marker-selected-favorite-urgent.png`
  - `/assets/icons/map-marker-inactive.png`
  - `/assets/icons/map-marker-inactive-favorite.png`
  - `/assets/icons/map-marker-inactive-urgent.png`
  - `/assets/icons/map-marker-inactive-favorite-urgent.png`
- `CenterMapMarker`는 상태에 따라 PNG URL을 선택하고, 숫자 label 색상과 크기를 함께 조정한다.
- `npm run build`로 타입/번들 검증을 완료했다.

## 관련 파일
- `docs/brand/moon-cen.md`
- `frontend2/src/components/Header.tsx`
- `frontend2/src/App.tsx`
- `frontend2/src/components/SearchHero.tsx`
- `frontend2/src/components/QuickCategoryChips.tsx`
- `frontend2/src/components/NearbyCenterMap.tsx`
- `frontend2/src/components/Sidebar.tsx`
- `frontend2/src/components/HighlightSections.tsx`
- `frontend2/src/components/ClassCard.tsx`
- `frontend2/src/styles.css`

## 검증
- `npm run build` 통과
- 개발 서버 포트 확인: `5173`, `5174` listen 상태
- 2026-06-09 상단/지도 섹션 보강 후 `frontend2`에서 `npm run build` 통과

## 2026-06-11 map/list search-scope fix
- `App.tsx` branch list filtering now applies the selected search scope.
- `provider` mode shows only culture-center branches.
- `category` mode excludes culture-center branches and applies selected collection categories.
- `MapSection.tsx` marker course count now falls back to branch aggregate counts when the current loaded course page has no count for a branch.
- This prevents culture-center mode from showing education/experience branches in the branch list and prevents map markers from disappearing during initial load or after scope/filter changes.
- Verification: `frontend2` `npm run build` passed.

## 2026-06-11 sidebar category chip stability
- `Sidebar.tsx` now separates category filter values from the short labels shown in the UI.
- Category chips keep the fixed display labels for the selected search scope, so clicking a category no longer expands the chip with a long DB category name.
- `styles.css` changed the category chip area to a stable 3-column grid with single-line ellipsis.
- Detail filter menus now open inline inside the sidebar with bounded scroll, so option lists are not clipped by the sidebar panel.
- Verification: `frontend2` `npm run build` passed.

## 2026-06-11 branch list provider icons
- `NearbyCenterMap.tsx` no longer renders ordinal numbers in the branch list.
- Branch rows now render a provider-specific MoonCen SVG badge for culture-center providers:
  - `HOMEPLUS`, `LOTTE`, `EMART`, `HYUNDAI_DEPT`, `GALLERIA`, `AK_PLAZA`, `ELAND_RETAIL`, `SHINSEGAE_ACADEMY`, `LOTTE_MART`.
- `styles.css` increased the branch-list leading column and added fixed-size icon styling so rows do not shift on hover/selection.
- Verification: `frontend2` `npm run build` passed.

## 2026-06-11 horizontal course card v2
- `ClassCard.tsx` was normalized and restructured into a horizontal card:
  - left image/status area
  - right title, age, schedule, time/session, price, compare/apply actions, and favorite button
- Card status, action, price, and favorite labels were changed to readable Korean text.
- `styles.css` adds the final card override at the end of the stylesheet so older card rules do not override the new layout.
- Desktop results use 2-column horizontal cards; narrower screens collapse to 1 column with reduced typography and action sizes.
- Verification: `frontend2` `npm run build` passed.

## 2026-06-11 multi-select branch list
- `NearbyCenterMap.tsx` now treats branch-list clicks as multi-select toggles instead of single branch selection.
- `App.tsx` connects branch-list selection to `branchFilters`, so multiple selected branches are queried together through `branchIds`.
- `MapSection.tsx` receives selected branch IDs and highlights all selected branch markers.
- The branch list header shows the number of selected branches, and selected rows use a mint left border/dot indicator.
- Verification: `frontend2` `npm run build` passed.

## 2026-06-11 vertical course card restore
- `ClassCard.tsx` restored the original vertical card structure while keeping readable Korean labels.
- The favorite button moved back to the image area; the content body now keeps title, age/date/time/session, price, center, compare/apply actions in a compact vertical flow.
- `styles.css` adds a final card v3 override that restores the original compact card size and grid density while keeping the balanced colors/icons from the reference card.
- Desktop uses 4 columns, then 3/2/1 columns across responsive breakpoints.
- Verification: `frontend2` `npm run build` passed.

## 2026-06-11 sidebar filter popup menus
- `styles.css` adds a final sidebar filter override so detail filter menus are positioned as floating popups.
- Opening age/date/day/time/fee/status no longer inserts menu content into the filter panel flow, preventing panel height changes and horizontal width shifts caused by new scrollbars.
- Popup menus keep their own bounded vertical scroll for long option lists and use thin mint scrollbars.
- Mobile filter drawer keeps scroll for the drawer itself while the opened option menu floats above the filter content.
- Verification: `frontend2` `npm run build` passed.

## 2026-06-11 category chip single-select from all
- `App.tsx` changed category chip behavior when the current category state is all-selected.
- Clicking a category chip such as `영유아` now selects only that category instead of removing it from the all-selected set and leaving every other category active.
- Existing multi-select behavior is preserved after the first explicit category selection.
- Verification: `frontend2` `npm run build` passed.

## 2026-06-11 stable map marker count on hover
- `CenterMapMarker.tsx` no longer changes marker icon asset, size, label origin, or label color on direct mouse hover.
- Hover now only raises z-index, so the course-count label remains stable and does not visually change or jump.
- Selected and list-highlighted markers still use the selected marker style.
- Verification: `frontend2` `npm run build` passed.

## 2026-06-11 course card typography overflow fix
- `styles.css` adds a final card typography override while keeping the current card dimensions.
- Reduced title, metadata, price, favorite, and action button font sizes so text does not overflow the fixed-height cards.
- Title remains clamped to two lines with safer word wrapping.
- Mobile cards use smaller typography and tighter controls to avoid clipped price/action text.
- Verification: `frontend2` `npm run build` passed.
