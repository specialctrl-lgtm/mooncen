# 문센 모바일 앱

문센의 Android·iPhone 공용 Expo 앱입니다. 문화센터, 전시·체험, 평생교육 강좌를 운영 API에서 검색하고, 주변 기관을 카카오 지도에서 확인하며, 관심 강좌를 기기에 보관할 수 있습니다.

## 실행

Node.js 22.13 이상에서 다음 순서로 실행합니다.

```powershell
npm ci
Copy-Item .env.example .env.local
npm run start
```

`.env.local`에는 다음 공개 클라이언트 설정만 둡니다.

- `EXPO_PUBLIC_API_BASE_URL`: 기본값은 `https://mooncen.kr`
- `EXPO_PUBLIC_KAKAO_MAPS_JAVASCRIPT_KEY`: 네이티브 WebView 카카오 지도 표시용 JavaScript 키

카카오 REST API 키는 앱 번들에 넣지 않습니다. 주소 좌표 변환은 서버에서만 수행해야 합니다. JavaScript 키는 카카오 개발자 콘솔에서 서비스 도메인과 앱 식별자를 제한합니다.

`.env.local`은 Git에서 제외됩니다. 원격 EAS 빌드를 연결할 때는 같은 공개 값을 EAS 환경 변수로 별도 등록해야 합니다.

## 주요 기능

- 홈: 문화센터·체험·교육 범위별 추천, 인기, 접수예정, 마감임박
- 강좌찾기: 운영 API 서버 검색, 페이지네이션, 상태·요일·시간·가격·대상 필터
- 지도: 현재 위치 권한을 사용한 주변 기관 조회와 카카오 지도 마커
- 상세: KST 접수기간과 예약 가능 여부를 함께 검증한 신청 CTA
- 보관함·마이: 기기 로컬 보관, 관심 분야와 지역 설정 영속화

서버의 상태가 `OPEN` 또는 `DEADLINE`이어도 `apply_end`가 KST 기준 지났거나 `reservation_available=false`이면 앱에서는 접수마감으로 처리합니다. 신청 버튼은 안전한 `application_url`이 있는 경우에만 활성화하며 정보 원문인 `raw_url`과 분리합니다.

## 검증

```powershell
npm run typecheck
npm test
npm run doctor
npm run export:all
```

현재 프로젝트는 Android·iOS·Web JavaScript 번들을 검증합니다. 스토어 제출에는 별도로 Expo/EAS 프로젝트 연결, Apple·Google 서명 자격 증명, 실기기 접근성·권한·카카오 지도 검수가 필요합니다.

자세한 설계·기능 점검 결과는 [모바일 앱 점검 문서](./docs/mobile-audit-2026-08-09.md)에 있습니다.
