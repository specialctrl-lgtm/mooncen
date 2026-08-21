# Android·iPhone 앱 재설계 및 기능 점검

점검일: 2026-08-09  
대상: `mooncen-app` Expo SDK 57 소비자 앱

## 결론

기존 샘플 데이터 기반 프로토타입을 현재 문센 웹 콘셉트와 같은 운영 앱 구조로 전환했다. Android와 iPhone은 하나의 Expo 코드베이스를 공유하며, 운영 API·KST 접수 상태·카카오 지도·서버 페이지네이션·기기 저장을 실제 기능으로 연결했다.

다만 이 결과는 스토어 서명까지 완료된 출시본은 아니다. EAS 프로젝트와 Apple/Google 배포 자격 증명, 모바일 인증 계약, Universal/App Link와 푸시 서버 계약은 저장소에서 확인할 수 없는 외부 조건이라 별도 작업이 필요하다.

## 처음 상태에서 확인한 문제

- 강좌 조회가 전부 메모리 `mockApi`였고 운영 API base URL도 없었다.
- 샘플 18건의 접수 종료일이 모두 지났지만 대부분 접수중·마감임박·접수예정으로 노출됐다.
- 센터 화면은 실제 지도가 아닌 격자형 좌표 미리보기였다.
- 검색이 전체 카탈로그를 한 번에 렌더링해 운영 데이터 규모를 감당할 수 없었다.
- 모바일 상태 enum과 운영 API enum·nullable DTO가 서로 달랐다.
- 사용자 지역·관심 설정이 앱 재시작 시 사라졌다.
- 기본 Expo 아이콘과 보라색 샘플 UI가 현재 문센의 민트·청록 콘셉트와 달랐다.
- 테스트가 없고 Expo SDK 패치 버전도 일치하지 않았다.

## 반영한 설계와 기능

### 정보 구조와 디자인

- 탭을 `홈 / 강좌찾기 / 지도 / 보관함 / 마이`로 정리했다.
- 웹과 같은 청록·민트 중심 색상, 밝은 카드, 산호색 보조 강조를 공통 테마로 적용했다.
- 문센 초승달·가족 심볼을 새 앱 아이콘, 헤더, 스플래시, favicon에 적용했다.
- 홈을 범위 탐색, 위치 탐색, 대상별 바로가기, 인기·마감임박·접수예정 순서로 재구성했다.
- 모든 핵심 버튼의 최소 터치 크기, 접근성 label/role, 안전 영역, 키보드 회피와 빈 상태를 보강했다.

### 운영 데이터와 상태 정확성

- `/api/courses`, `/api/courses/{id}`, `/api/branches/nearby`를 nullable-safe DTO로 연결했다.
- 강좌 검색을 30건 단위 무한 페이지네이션과 서버 필터로 바꿨다.
- 서버 canonical 상태 `SCHEDULED/OPEN/WAITING/DEADLINE/CLOSED`를 손실 없이 사용한다.
- `OPEN/DEADLINE`이라도 `apply_end`가 KST 기준 과거이거나 예약 불가이면 `CLOSED`로 강등한다.
- 접수 CTA는 안전한 `application_url`만 사용하고 기관 원문 `raw_url`과 분리한다.
- API 오류, 비JSON 응답, 네트워크 단절, 15초 timeout, 재시도·새로고침을 처리한다.

### 검색·지도·보관

- 검색어 debounce, 최소 2자, 범위·상태·연령·요일·시간·가격·정렬 필터를 적용했다.
- 현재 위치 권한은 사용자가 주변 검색을 누를 때만 요청한다.
- Android·iPhone에서는 Kakao Maps JavaScript SDK를 제한된 WebView에 표시하고, 웹 미리보기는 외부 카카오맵과 기관 목록으로 대체한다.
- 지도 마커와 기관 선택, 기관별 강좌 필터를 `branch_id`로 연결했다.
- 찜 ID, 선택 지역, 관심 분야를 versioned AsyncStorage에 저장한다.
- React Query를 앱 foreground와 네트워크 연결 상태에 동기화했다.

## 보안·비용 원칙

- Google Maps API 설정이나 Google 지도 키를 모바일 코드에 사용하지 않는다.
- 카카오 REST API 키는 모바일 환경 변수와 번들에 넣지 않는다.
- 클라이언트에는 공개 JavaScript 키만 두고 카카오 콘솔에서 도메인·플랫폼 제한을 적용한다.
- 실행 가능한 URL scheme과 사용자 정보가 포함된 URL은 신청 링크로 열지 않는다.
- 현재 인증 버튼을 가장하지 않는다. 기존 웹의 HttpOnly cookie/CSRF/OAuth 계약은 native callback을 지원하지 않으므로 PKCE 또는 별도 모바일 세션 계약이 선행돼야 한다.

## 검증 항목

| 항목 | 결과 |
| --- | --- |
| TypeScript strict 검사 | 통과 |
| API·KST 상태 단위 테스트 | 13건 통과 |
| Expo Doctor | 20/20 통과 |
| Android Hermes bundle export | 통과 |
| iOS Hermes bundle export | 통과 |
| Web static export | 통과 |
| 운영 강좌·주변 기관 API smoke | 통과 |
| 모바일 크기 Web 렌더 검수 | 통과 |
| `npm audit --omit=dev` | critical 0, high 14 (Expo/Metro/RN 전이 의존성) |

## 출시 전에 남은 외부 조건

1. Expo owner/project ID와 `eas.json` 배포 프로필을 실제 운영 계정으로 연결한다.
   원격 빌드에는 Git에서 제외된 `.env.local` 대신 EAS 환경 변수로 API 주소와 카카오 JavaScript 키를 등록한다.
2. Apple Team/provisioning과 Android upload key를 등록해 AAB·IPA를 빌드한다.
3. Android/iPhone 실기기에서 카카오 콘솔 허용 도메인, 위치 권한 거절·재허용, VoiceOver/TalkBack, 큰 글자를 점검한다.
4. 서버에 AASA와 `assetlinks.json`을 배포한 뒤 Universal Link/App Link를 앱 설정에 추가한다.
5. 로그인·서버 찜이 필요하면 PKCE 기반 모바일 인증 계약을 먼저 확정한다.
6. 푸시 알림은 APNs/FCM 토큰 등록과 서버 구독·중복 제거 계약이 생긴 뒤 추가한다.
7. Expo/Metro/RN 상위 패치에서 `image-size` 등 전이 의존성 보안 권고가 해결되는지 추적한다. 현재 `audit fix --force` 제안은 Expo 53·RN 0.72로 역행하므로 적용하지 않는다.

## 최신 렌더 검수 이미지

- [홈](./screenshots-v2/home.png)
- [강좌찾기](./screenshots-v2/search.png)
- [강좌 상세](./screenshots-v2/detail.png)
- [지도](./screenshots-v2/map.png)
- [보관함](./screenshots-v2/favorites.png)
- [마이](./screenshots-v2/my.png)

이미지는 정적 Web 빌드를 모바일 크기로 렌더링한 UI 검수 자료다. 네이티브 카카오 지도와 위치 권한은 Android/iPhone 실기기 검수 항목이다.
