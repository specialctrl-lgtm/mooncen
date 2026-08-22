# 모바일 UI 샘플

이 폴더의 PNG는 개선된 Expo 앱을 정적 Web 빌드로 렌더링한 뒤 모바일 뷰포트에서 캡처한 검수용 이미지다.

- `android-home.png`: 412 × 915 Android 기준 홈 화면
- `android-centers.png`: 412 × 915 Android 기준 센터 찾기 화면
- `android-search.png`: 412 × 915 Android 기준 강좌 검색 화면
- `ios-home.png`: 393 × 852 iPhone 기준 홈 화면
- `ios-centers.png`: 393 × 852 iPhone 기준 센터 찾기 화면
- `ios-search.png`: 393 × 852 iPhone 기준 강좌 검색 화면
- `ios-detail.png`: 393 × 852 iPhone 기준 프로그램 상세 화면
- `ios-my.png`: 393 × 852 iPhone 기준 마이 화면

실제 Android 에뮬레이터 및 iOS Simulator 캡처가 아니므로 시스템 글꼴, 상태 표시줄, 키보드, 공유 시트와 같은 네이티브 요소는 릴리스 빌드에서 별도 검증해야 한다.

센터 찾기의 지도 형태 영역은 지도 SDK나 현재 위치 권한을 사용하지 않는 좌표 기반 위치 미리보기다. 화면에도 `실제 지도 아님`을 명시하며, 선택 지역과 지름 5/10/20km 조건에 포함된 센터만 목록과 동일하게 표시한다.
