# MoonCen Monitor Android

MoonCen Monitor API를 사용하는 네이티브 Android 운영 앱입니다. Grafana 없이
인터넷에서 서버와 MoonCen 상태를 빠르게 확인할 수 있습니다.

## 화면

- 크롤러: 최근 cycle, 24시간 수집·신규·업데이트·실패, Provider 성과와 실행/목표/제어 노드의 CPU·메모리·부하·디스크·논리 CPU·온도 표시
- 문센: Primary, DB·FRONT·BACKEND·CRAWLER, 크롤러 현재 실행/목표 워커/중앙 제어 노드, 전환·drift 상태와 백업을 상세 표시
- 서버: CPU, 메모리, 디스크, 온도, 가동 시간
- 작업: 호환되는 별도 사설 HTTPS API에서만 허용된 원격 작업 제공

화면은 상태 배지, 핵심 수치, 항목별 카드로 구성됩니다. 앱 화면이 활성화된 동안
크롤러·문센 탭은 5분마다 자동 갱신합니다. 서버·작업 탭은
탭에 들어갈 때 한 번 조회하며 이후에는 상단의 새로고침 버튼으로만 갱신합니다.
60초 안에 문센 화면으로 빠르게 돌아오면 직전 core 응답을 재사용하고, 크롤러 화면은
별도 crawler 응답을 다시 조회합니다. 응답의 `generated_at`은 기기 현지 시간으로 표시하며, 부분
수집 오류도 정상 데이터와 구분해 표시합니다.
문센 화면 갱신과 백그라운드 점검이 겹치면 동일한 core GET을 한 번만 보내고 결과를
공유합니다. 사용자가 누른 새로고침은 완료된 캐시를 건너뛰어 실제 상태를 다시
조회하며, 장애 응답이 확인되면 이전 정상 캐시를 즉시 폐기합니다.

백업은 일회 실행 서비스의 현재 `active` 값이 아니라 API가 제공하는 `fresh`,
`fresh_known`, `health`, 최근 성공 또는 실행 시각으로 판단합니다. 따라서 정상적으로
완료되어 비활성 상태인 일회 실행 백업을 `중지`로 표시하지 않습니다.

## 공개 연결

기본 API 주소는 `https://mon.binary.kr`입니다. 이 공개 주소는 **상태 조회 전용**이며,
크롤러·문센·서버 화면만 조회합니다. 작업 탭은 공개 주소에서 작업 API를 호출하지 않고
`인터넷 연결은 상태 조회 전용입니다. 원격 작업은 비활성화되어 있습니다.`라고
표시합니다.

서버의 Prometheus exporter는 상태 API를 만드는 내부 수집원으로 계속 운영합니다.
Android 앱은 exporter 대상 목록을 별도 조회하거나 화면에 중복 표시하지 않습니다.

앱은 HTTPS 주소만 허용하고 HTTP 예외를 두지 않습니다. 사용자 정보, 쿼리 문자열,
fragment가 들어간 주소와 서버 리디렉션도 허용하지 않습니다. 주소나 토큰을 바꾸면
core 응답 형식까지 확인하는 **연결 테스트**를 통과해야 저장할 수 있습니다. API
응답 본문은 512 KiB까지만 허용합니다.

이전 버전에 저장된 HTTP·기존 내부망·잘못된 주소는 첫 실행 또는 백그라운드 실행 때
공개 HTTPS 주소로 한 번 자동 이전되며, 장애 비교 캐시도 함께 초기화됩니다. 유효한
사용자 지정 HTTPS 주소는 그대로 보존합니다. 처음 설치했거나 주소가 자동 이전된
경우 연결 설정 창이 바로 열리므로, 새 `MONITOR_APP_TOKEN`을 입력하고 **연결
테스트** 후 저장하세요.

## 웹 배포와 업데이트

연결 토큰이 설정된 앱은 활성 화면에서 최대 24시간에 한 번
`https://mon.binary.kr/android/latest.json`을 자동 확인합니다. 현재 `versionCode`보다
큰 버전이 있으면 릴리스 노트와 APK SHA-256 일부를 표시하고, 사용자가 **다운로드**를
선택했을 때만 고정된 HTTPS APK 주소를 브라우저로 엽니다. 자동 확인 시각이나 토큰
설정 여부와 관계없이 상단의 `⇧` 버튼으로 언제든지 수동 확인할 수 있습니다.

```text
https://mon.binary.kr/android/latest.json
https://mon.binary.kr/android/mooncen-monitor.apk
```

Android 보안 정책상 웹에서 받은 APK의 설치와 업그레이드는 사용자가 직접
확인해야 합니다. 기존 앱과 동일한 application ID 및 서명키를 사용해야 데이터와
설정을 유지한 채 업그레이드됩니다.

## API

공개 주소에서 앱이 사용하는 상태 조회 API:

```text
GET /api/monitoring/core
GET /api/monitoring/crawler
GET /api/monitoring/mooncen
GET /api/monitoring/servers
```

문센 화면은 크롤러의 현재 상태와 목표 구조를 혼동하지 않도록
`topology.crawler_runtime_node`, `crawler_target_node`,
`crawler_control_node`, `crawler_mode`, `crawler_transition_state`,
`crawler_runtime_drift`를 각각 읽습니다. 현재 legacy 전환 상태에서는 현재 실행
`cloud`, 목표 워커 `gen1crawler`, 중앙 제어 `gen1db`, 전환 대기로 표시됩니다.
필드가 누락되거나 값 사이의 관계가 맞지 않으면 다른 노드에서 추정하지 않고 세
배치와 전환·drift 상태를 모두 `확인 불가`로 표시합니다. 이 경우에도 별도로
검증된 DB·FRONT·BACKEND·CRAWLER 서비스 상태는 계속 표시합니다.

크롤러 화면은 schema version 1 응답을 구역별로 검증합니다. 최근 cycle,
24시간 성과, Provider 집계, 노드 자원은 서로 독립적으로 `available`을 판단하며
누락된 숫자를 `0`으로 바꾸지 않습니다. OPS 상세 원본이나 하드웨어 센서가 없으면
해당 수치와 구역별 사유를 `확인 불가`와 함께 표시하고, 함께 반환된 정상 구역은 계속
보여 줍니다. 온도는 온라인 노드에서 실제 센서 값이 확인될 때만 °C로 표시하며,
센서 부재와 수집 실패를 추정해 서로 바꾸지 않습니다.

같은 schema version 1의 선택적 `quality` 객체가 있으면 생산 데이터 품질을 별도
카드로 표시합니다. 활성 강좌, 필수값 누락, 날짜·가격 오류, 위치 불완전, 중복 URL,
동기화 차단과 최근 품질 스캔을 보여 줍니다. 이전 서버처럼 `quality`가 없거나 해당
객체가 잘못됐거나 `available=false`이면 품질 카드만 `확인 불가`로 닫고 숫자를
0으로 만들지 않습니다. 기존 최근 수집·24시간 성과·Provider·노드 카드와 root
schema v1 호환성은 유지됩니다. 생산 품질용 서버 토큰은 모니터 서버의 보호된
EnvironmentFile에만 있으며 Android 설정이나 APK에는 포함되지 않습니다.

호환되는 별도 사설 HTTPS API를 설정한 경우에만 작업 탭에서 아래 API를 사용합니다.

```text
GET  /api/operation/actions
POST /api/operation/run
```

## 백그라운드 알림

Android 15에서 `dataSync` Foreground Service는 하루 6시간으로 제한되므로 상시
Foreground Service를 사용하지 않습니다. 대신 알림 권한과 연결 토큰이 모두 있을
때만 `JobScheduler`가 네트워크 연결 및 배터리 부족이 아닌 상태에서 약 30분 주기로
경량 core API를 확인합니다. 조건이 사라지면 예약과 상시 상태 알림을 취소합니다.
실행 시점은 Android가 배터리 상태에 따라 조정할 수 있습니다. core API는 DB,
FRONT, BACKEND, CRAWLER와 Primary만 반환하며 서버 목록·Prometheus 전체 경보·OPS·
품질 조회를 수행하지 않습니다.

알림 권한을 허용하면 저우선 **현재 모니터링 상태** 알림이 계속 표시됩니다.
알림에는 Primary 서버, 핵심 서비스 정상 수, 현재 장애 수와 마지막 확인
시각이 표시됩니다. 앱의 유효한 문센 화면 갱신과 백그라운드 확인 때 같은
알림을 사용하되, 내용이 같으면 최대 한 시간에 한 번만 다시 게시합니다. 기기를
재부팅하면 마지막으로 확인한 상태를 복원합니다. 이 알림은 실시간 실행 중이라는
의미가 아니라 마지막 API 확인 결과를 나타냅니다.

장애·복구 알림 대상은 Primary와 네 핵심 서비스뿐입니다. 안정적인 로컬 key는
`core:primary`, `core:database`, `core:frontend`, `core:backend`,
`core:crawler`입니다.

- 새 `critical`만 장애 알림
- 이전 `critical`이 명시적인 `healthy`가 되면 복구 알림
- `warning`과 `unknown`은 새 장애나 복구로 간주하지 않음
- 프로세스 재시작 후에도 같은 문제를 중복 알림하지 않음
- API 연결 실패는 상시 상태 알림에 표시하되, 관측 불가 서비스를 복구로
  처리하지 않음

즉시 장애 통지가 필요하면 서버 측 Prometheus 알림 전송을 함께 운영해야 합니다.

## 보안

- 모든 API 통신은 HTTPS만 허용합니다.
- 설정 토큰은 비밀번호 입력 형식으로 표시합니다.
- 앱 백업은 비활성화되어 토큰이 기기 백업에 포함되지 않습니다.
- 공개 기본 주소에서는 작업 API를 호출하지 않습니다.
- 별도 사설 HTTPS 주소의 원격 작업은 서버가 제공한 action ID만 실행합니다.

## 빌드

요구사항:

- JDK 17
- Android SDK Platform 35
- Android SDK Build Tools 34.0.0 이상

저장소에 포함된 Gradle Wrapper로 빌드합니다.

```powershell
.\gradlew.bat assembleDebug
```

APK:

```text
app/build/outputs/apk/debug/app-debug.apk
```

추가 검증:

```powershell
.\gradlew.bat lintDebug
.\gradlew.bat testDebugUnitTest
```
