# mooncen frontend

문센/문화센터 강좌를 지도 기반으로 검색하는 프론트엔드 목업입니다.
React + TypeScript + Vite 기반이며, 별도 이미지 에셋 없이 CSS와 mock data로 구성했습니다.

## 실행

```bash
npm install
npm run dev
```

## 빌드

```bash
npm run build
```

## 스타일 구조와 점검

`src/styles.css`는 cascade 순서만 관리하는 진입점입니다. 아래 파일의 import 순서를 바꾸면 기존 화면 우선순위가 달라질 수 있습니다.

- `src/styles/core.css`: 공통 토큰, 컨트롤, 카드, 모달, 필터 기반 스타일
- `src/styles/dashboard.css`: 검색, 대시보드, 지점, 지도, 강좌 표현
- `src/styles/dashboard-current.css`: 현재 대시보드와 동적 상태 보정
- `src/styles/responsive.css`: 모바일 홈, 반응형 containment, 접근성 보정

`npm run audit:css`는 전체 import graph의 크기·규칙·`!important` 수를 출력합니다. 클래스나 화면 구조를 크게 정리한 뒤에는 `npm run maintain:css`로 미사용 selector와 동일 cascade 내 지배된 선언을 안전하게 제거하고, 반드시 `npm run test:e2e`로 모바일·데스크톱 상태를 확인합니다.

## 주요 구성

- `src/App.tsx`: 전체 페이지 레이아웃
- `src/components/Header.tsx`: 로고, 검색, 상단 메뉴, 카테고리 탭
- `src/components/Sidebar.tsx`: 필터 패널
- `src/components/MapSection.tsx`: 지도 검색 영역과 핀 UI
- `src/components/ClassCard.tsx`: 강좌 카드
- `src/components/PopularClassCard.tsx`: 인기 강좌 카드
- `src/data/mockData.ts`: 카테고리/강좌/지도 핀 mock data
