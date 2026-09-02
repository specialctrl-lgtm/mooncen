# Frontend Home/Card Visual Refresh

## 목적

메인 화면과 강좌 카드를 사용자가 제시한 MoonCen 홈 화면 레퍼런스에 가깝게 정리한다.

## 변경 파일

- `frontend2/src/components/Header.tsx`
- `frontend2/src/components/SearchHero.tsx`
- `frontend2/src/components/QuickCategoryChips.tsx`
- `frontend2/src/components/NearbyCenterMap.tsx`
- `frontend2/src/components/HighlightSections.tsx`
- `frontend2/src/components/ClassCard.tsx`
- `frontend2/src/styles.css`

## 적용 내용

- Header를 로고, 중앙 검색창, 찜/내강좌/알림/로그인 구조로 단순화했다.
- SearchHero를 큰 문구, 설명, 연령/카테고리/지역/검색 버튼 중심으로 재구성했다.
- SearchHero 크기를 줄이고 Header 검색창과 중복되는 검색어 입력은 제거했다.
- SearchHero의 지역 선택은 제거했다.
- SearchHero에는 추천 후보 키워드 3개와 상세 검색 버튼을 배치했다.
- 상세 검색 버튼은 기존 전체 필터 패널을 오버레이로 열도록 연결했다.
- 빠른 카테고리는 아이콘형 카드 스트립으로 변경했다.
- 주변 문화센터 영역은 좌측 리스트와 우측 지도 구조를 유지하면서 카드형 레이아웃으로 정리했다.
- HighlightSections는 4개의 안내 카드 형태로 변경했다.
- ClassCard는 이미지 상단, 상태 배지, 찜 버튼, 제목 2줄, 핵심 메타, 가격, 비교/신청 CTA 구조로 변경했다.
- 상세 필터 사이드바는 메인 화면 레퍼런스와 맞추기 위해 기본 화면에서 숨김 처리했다.

## 검증

- `npm run build` 통과.

## 남은 개선 후보

- Hero 우측 비주얼은 현재 CSS 기반 일러스트 스타일이다. 실제 사진형 에셋을 사용할 경우 `public`에 이미지 파일을 추가하고 hero background를 교체하면 된다.
- 카테고리 아이콘은 별도 아이콘 라이브러리 없이 텍스트 심볼로 처리했다. 추후 아이콘 패키지를 도입하면 시각 품질을 더 높일 수 있다.
