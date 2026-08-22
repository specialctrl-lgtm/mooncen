# Android·iOS 모바일 앱 점검 및 개선 결과

> 이 문서는 2026-07-12 프로토타입 기준 기록입니다. 운영 API와 카카오 지도까지 반영한 최신 결과는 [2026-08-09 재설계 점검](./mobile-audit-2026-08-09.md)을 확인하세요.

점검일: 2026-07-12  
대상: `mooncen-app` Expo SDK 57 소비자 앱, 별도 `android` Ops 모니터

## 결론

소비자 앱은 Android와 iOS가 공유하는 Expo 코드베이스로 정리했고, 샘플 앱이 실제 예약·지도·알림을 제공하는 것처럼 보이던 UI를 제거했다. 플랫폼별 앱 식별자와 버전, 안전 영역, 뒤로가기, 키보드, 접근성, 오류·새로고침 상태를 보강했다.

현재 결과물은 UI 검증이 가능한 개발 버전이다. Android/iOS/Web JavaScript 번들은 생성되지만, 실제 스토어 배포에는 서명 자격 증명과 네이티브 기기 검증이 더 필요하다.

## 반영한 개선

- 앱 이름을 `문센`, 버전을 `0.2.0`으로 정리했다.
- iOS `bundleIdentifier/buildNumber`, Android `package/versionCode`를 추가했다.
- Android 예측 뒤로가기와 키보드 resize를 활성화했다.
- 탭을 홈·센터 찾기·강좌 검색·찜·마이 5개로 재구성했다.
- 지도 SDK·권한 없이 실제 지도처럼 보이던 UI는 사용하지 않고, 저장된 좌표와 선택 지역을 이용한 `실제 지도 아님` 위치 미리보기로 대체했다.
- 센터 찾기는 지름 5/10/20km를 반경 2.5/5/10km로 계산해 같은 결과를 미리보기와 목록에 표시한다.
- 홈 히어로, 대상별 바로가기, 주변 센터, 이미지형 인기 강좌를 보라색 디자인 시스템으로 통합했다.
- 검색 카드, 상세 이미지·하단 고정 CTA, 기기 저장 중심 마이 화면을 함께 개편했다.
- 동작하지 않는 로그인, 계정 관리, 알림 미리보기 버튼을 제거했다.
- `접수 시작 알림` 버튼이 기관 홈페이지를 열던 의미 오류를 수정했다.
- 기관 링크는 HTTPS이며 userinfo가 없는 경우만 열고, 외부 사이트임을 버튼과 안내문에 표시한다.
- iOS 상세 화면에 명시적 뒤로가기를 추가하고 상세 화면에서는 탭 바를 숨겼다.
- Android/iOS safe area를 적용하고 상세 화면 하단 inset을 반영했다.
- 검색 키보드 dismiss, 검색어 삭제, 44pt 이상 터치 영역, pull-to-refresh를 추가했다.
- 홈·검색·찜·상세에 로딩/오류/재시도 상태를 추가했다.
- 카드 전체 버튼과 카드 내부 찜 버튼의 중첩 Pressable을 형제 구조로 분리했다.
- 헤더·탭·필터·검색 진입점의 접근성 role/label/state를 보강했다.
- 작은 텍스트의 대비가 부족했던 primary, secondary, warning, accent 색상을 더 어둡게 조정했다.
- 사용하지 않는 기본 Expo `App.tsx`, `index.ts` 진입 파일을 제거했다.
- CI가 Web만이 아니라 Android·iOS·Web 번들을 모두 export하도록 변경했다.

## 검증 결과

| 항목 | 결과 |
| --- | --- |
| TypeScript strict 검사 | 통과 |
| Expo Doctor | 20/20 통과 |
| npm production dependency audit | 취약점 0건 |
| Android JS/Hermes bundle export | 통과 |
| iOS JS/Hermes bundle export | 통과 |
| Web static export | 통과 |
| 412×915 Android 뷰포트 UI smoke | 통과 |
| 393×852 iPhone 뷰포트 UI smoke | 통과 |

## 남은 차단 사항

### 실제 데이터 API

운영 공개 API는 무인증 조회가 가능하지만 현재 앱 타입과 바로 호환되지 않는다.

- API는 `{ total, page, size, items }` 페이지형이고 앱은 전체 카탈로그를 메모리에 올리는 구조다.
- 기관 코드와 실제 지점 정보가 분리되어 있고 지점은 nullable이다.
- 날짜, 가격, 연령, 이미지, 신청 URL도 nullable이다.
- API 상태값 `SCHEDULED/OPEN/WAITING/DEADLINE/CLOSED`를 앱 상태로 변환해야 한다.
- `application_url`과 정보 원문 `raw_url`은 다른 의미이므로 동일한 예약 버튼으로 처리하면 안 된다.

따라서 HTTPS base URL 검증, API DTO, nullable UI, 상태 변환, 서버 검색·페이지네이션을 포함한 어댑터 작업이 필요하다. 그 전까지 앱은 샘플 데이터임을 화면에 명시한다.

### 접수 알림

앱에는 아직 APNs/FCM 토큰 등록, 알림 권한, 서버 구독, 중복 제거, 시간대 처리가 없다. 수집 DB도 접수 일자 위주라 정확한 접수 시작 시각과 회원군을 보존하지 못한다. 이 계약이 준비되기 전에는 알림 버튼을 노출하지 않는다.

### 네이티브 배포

- 현재 Windows 환경에서는 Xcode/iOS Simulator/IPA archive를 실행할 수 없다.
- Expo owner/project ID, Apple Team, provisioning profile, Android upload key와 EAS build profile이 없다.
- 실제 TalkBack, VoiceOver, 큰 글자, 키보드, 공유 시트, 외부 브라우저 복귀를 실기기에서 확인해야 한다.
- 현재 앱 아이콘은 Expo 템플릿 자산이므로 스토어 제출 전 문센 브랜드 아이콘으로 교체해야 한다.
- Universal Link/AASA가 없어 현재는 `mooncen://` custom scheme만 설정되어 있다.

### 별도 Android Ops 모니터

`android` 디렉터리는 소비자 앱의 Android 네이티브 프로젝트가 아니라 별도 Ops 모니터다. Debug/Release 컴파일과 Lint는 통과했지만 다음 문제 때문에 운영 배포 대상에서는 제외해야 한다.

- 문서는 인증된 endpoint를 요구하지만 Authorization/Cookie/OIDC 구현이 없다.
- 서버와 앱 계약에 `alerts[]`, severity, incident, stale 정보가 없어 Critical 운영 경고를 표시할 수 없다.
- Activity 종료 뒤 refresh callback이 재예약될 수 있다.
- Release APK가 unsigned이며 테스트 소스가 없다.
- Android 15 edge-to-edge inset 처리가 없다.

## 샘플 화면

- [Android 홈](./screenshots/android-home.png)
- [Android 센터 찾기](./screenshots/android-centers.png)
- [Android 검색](./screenshots/android-search.png)
- [iOS 홈](./screenshots/ios-home.png)
- [iOS 센터 찾기](./screenshots/ios-centers.png)
- [iOS 강좌 검색](./screenshots/ios-search.png)
- [iOS 상세](./screenshots/ios-detail.png)
- [iOS 마이](./screenshots/ios-my.png)

샘플 PNG는 정적 Web 빌드를 모바일 화면 크기로 실제 렌더링한 검수 이미지다. 네이티브 에뮬레이터 캡처는 아니며 자세한 조건은 [스크린샷 안내](./screenshots/README.md)에 기록했다.
