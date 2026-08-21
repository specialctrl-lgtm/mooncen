# MoonCen Monitor API 계약

이 문서는 Android 앱과 MoonCen Monitor 백엔드 사이의 현재 API 계약을
정의한다. 상태 조회용 `monitoring` API와 명령 실행용 `operation` API는
권한과 공개 범위를 분리한다.

## 기본 주소와 전송 정책

Android 앱의 기본 공개 주소는 다음과 같다.

```text
https://mon.binary.kr
```

공개 주소는 HTTPS 상태 조회 전용이다. Android 앱은 다음 주소를 허용하지
않는다.

- HTTP 주소
- 사용자 정보(`user:password@host`)가 포함된 주소
- 쿼리 문자열 또는 fragment가 포함된 기본 주소
- 서버 리디렉션

주소나 토큰을 변경할 때는 `summary` 응답 형식까지 검사하는 연결 테스트를
통과해야 저장할 수 있다. 공개 기본 주소에서는 원격 작업 API를 호출하지
않는다.

## 인증과 공통 응답 정책

백엔드의 모든 `/api/*` 요청에는 읽기 토큰을 다음 헤더로 전달해야 한다.

```http
X-App-Token: <읽기 토큰>
```

- 쿼리 문자열 토큰은 지원하지 않는다.
- `X-App-Token`이 없거나 일치하지 않으면 `401 Unauthorized`를 반환한다.
- 서버에 읽기 토큰이 설정되지 않았으면 익명 접근을 허용하지 않고
  `503 Service Unavailable`로 fail-closed 처리한다.
- 토큰 값은 문서, URL, 로그 예시, 오류 메시지에 기록하지 않는다.
- `/api/*` 응답에는 `Cache-Control: no-store`, `Pragma: no-cache`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`가
  적용된다.
- 운영 환경은 허용된 `Host`만 받도록 trusted host 목록을 설정한다.

`operation` 실행은 읽기 토큰과 별개의 `X-Operation-Token`을 추가로
요구한다. 두 토큰은 서로 다른 비밀값으로 관리해야 한다.

## 공개 게이트웨이 allowlist

공개 HTTPS 게이트웨이는 다음 여섯 개의 읽기 전용 경로만 전달한다.

```text
GET /api/monitoring/core
GET /api/monitoring/crawler
GET /api/monitoring/summary
GET /api/monitoring/mooncen
GET /api/monitoring/servers
GET /api/monitoring/tailscale
```

그 밖의 경로는 백엔드에 구현되어 있더라도 공개 게이트웨이에서 `404`로
차단한다. 특히 다음 범주는 공개하지 않는다.

- `/api/monitoring/trends`
- 모든 `/api/operation/*` 경로
- 모든 내부 호환 경로
- `/metrics`
- 기타 allowlist에 없는 경로

공개 게이트웨이 설정은 HTTP 메서드까지 제한해야 하며, 위 여섯 경로에는
`GET`만 허용한다.

## Monitoring API

Monitoring API는 Prometheus 파생 상태, 공개 서비스 기능 검사, 정제된
Tailscale snapshot을 Android 앱에 적합한 JSON으로 정규화한다. 부분 수집
실패가 발생하면 가능한 데이터는 그대로 반환하고 `errors` 또는 해당
하위 객체의 상태 필드로 실패를 나타낸다.

### 핵심 서비스와 Primary 공통 계약

`GET /api/monitoring/core`, `GET /api/monitoring/summary`,
`GET /api/monitoring/mooncen`은 DB, FRONT, BACKEND, CRAWLER만을 운영
핵심 서비스로 반환한다. 현재 검토된 production topology에서 DB, FRONT,
BACKEND의 application Primary는 `cloud`다. 실측된 CRAWLER timer와 cycle
메트릭은 아직 `cloud`에 있고, 검토된 worker 목표 위치는 `gen1crawler`다. 공유
staging DB와 중앙 crawler-control의 목표 배치 위치는 `gen1db`다. 이 차이를
숨기지 않고 legacy/cutover-pending 상태로 노출하며, 다른 노드가 응답한다는
이유만으로 현재 실행 주체를 자동 변경하지 않는다.

Prometheus inventory의 `cloud`는 현재 담당 서비스의 운영 경보 대상이다.
검증된 distributed cutover 전까지 `gen1crawler / role=crawler-worker`와
`gen1db / role=crawler-control`은 `alerting=pending`이다. 필수 서비스 경보는
`cloud`의 DB, FRONT, BACKEND 또는 현재 실측 CRAWLER timer가 명시적으로
중단된 경우에만 발생한다.

```json
{
  "topology": {
    "environment": "production",
    "active_node": "cloud",
    "crawler_mode": "legacy",
    "crawler_runtime_node": "cloud",
    "crawler_target_node": "gen1crawler",
    "crawler_control_node": "gen1db",
    "crawler_transition_state": "cutover_pending",
    "crawler_runtime_drift": true,
    "service_nodes": {
      "database": "cloud",
      "frontend": "cloud",
      "backend": "cloud",
      "crawler": "cloud"
    }
  },
  "primary": {
    "node": "cloud",
    "expected_node": "cloud",
    "status": "healthy",
    "ok": true,
    "role_ok": true,
    "database_writable": true,
    "candidates": ["cloud"],
    "matches_topology": true
  },
  "core_services": [
    {
      "service": "database",
      "label": "DB",
      "node": "cloud",
      "primary_node": "cloud",
      "active_nodes": ["cloud"],
      "runtime_ok": true,
      "functional_ok": true,
      "ok": true,
      "status": "healthy",
      "detail": "runtime and functional checks passed",
      "checked_at": "2026-08-07T00:00:00+00:00"
    },
    {
      "service": "frontend",
      "label": "FRONT",
      "node": "cloud",
      "primary_node": "cloud",
      "active_nodes": ["cloud"],
      "runtime_ok": true,
      "functional_ok": true,
      "ok": true,
      "status": "healthy",
      "detail": "runtime and functional checks passed",
      "checked_at": "2026-08-07T00:00:00+00:00"
    },
    {
      "service": "backend",
      "label": "BACKEND",
      "node": "cloud",
      "primary_node": "cloud",
      "active_nodes": ["cloud"],
      "runtime_ok": true,
      "functional_ok": true,
      "ok": true,
      "status": "healthy",
      "detail": "runtime and functional checks passed",
      "checked_at": "2026-08-07T00:00:00+00:00"
    },
    {
      "service": "crawler",
      "label": "CRAWLER",
      "node": "cloud",
      "primary_node": "cloud",
      "active_nodes": ["cloud"],
      "runtime_ok": true,
      "functional_ok": true,
      "ok": true,
      "status": "healthy",
      "detail": "runtime and functional checks passed",
      "checked_at": "2026-08-07T00:00:00+00:00"
    }
  ]
}
```

`core_services`는 위 순서의 네 행만 포함한다. `services`는 같은 네 행을 같은
순서로 담는 하위 호환 alias다. 각 행의 판정 규칙은 다음과 같다.

- `runtime_ok`: topology가 선언한 서비스 실행 노드의 service/timer 메트릭 판정.
  관측 불가 시 `null`
- `functional_ok`: 아래 실제 기능 검사 판정. 관측 불가 시 `null`
- `node`, `primary_node`: topology가 선언한 해당 서비스의 기대 노드. CRAWLER는
  현재 실행 노드이며 `primary_node` 이름은 하위 호환을 위해 유지한다.
- `status`: `healthy`, `warning`, `critical`, `unknown` 중 하나
- `ok`: `status=healthy`일 때만 `true`. 클라이언트는 `false`만 보고
  `warning`, `critical`, `unknown`을 합치지 말고 `status`를 함께 사용해야 한다.
- `active_nodes`: 해당 서비스가 active로 관측된 노드 목록이며 Primary 선언을
  대신하지 않는다.
- `detail`: 운영자 표시용 설명으로, 클라이언트 판정에 사용하지 않는다.

`topology.crawler_runtime_node`와 하위 호환
`topology.service_nodes.crawler`는 현재 실제 timer와 기능 메트릭을 조회하는
동일 노드다. `crawler_target_node`는 검토된 worker 목표 위치이고,
`crawler_control_node`는 중앙 scheduler, release publication, observer와 공유
staging DB의 목표 배치 위치다. target/control은 `core_services`에 추가되지 않고
현재 CRAWLER runtime 판정에도 사용하지 않는다.

runtime과 target이 다르면 `crawler_transition_state=cutover_pending` 및
`crawler_runtime_drift=true`, 같으면 `crawler_transition_state=target_runtime` 및
`crawler_runtime_drift=false`다. 현재 wire `crawler_mode` 허용값은 `legacy`와
`distributed`, transition state 허용값은 `cutover_pending`과 `target_runtime`이다.
필드 누락이나 불변식 불일치 시 클라이언트는 target/control을 runtime으로 추론하지
말고 배치 상태를 `unknown`으로 표시해야 한다.

`primary.expected_node`와 `topology.active_node`는 설정된 기대값이다.
`primary.candidates`는 Primary 역할로 관측된 후보 목록이며,
기대 노드에서 `database_writable=true`이고 명시적인 역할 불일치가 없으면
`primary.status=healthy`다. 역할 메트릭이 없으면 `role_ok=null`일 수 있지만,
이 사실만으로 이미 확인된 쓰기 가능 관측을 폐기하지 않는다. 쓰기 가능 여부를
관측할 수 없으면 `unknown`, 읽기 전용이거나 역할이 명시적으로 불일치하면
`critical`이다. 다른 노드를 Primary로 추정해 채우지 않는다.

서비스별 기능 검사는 다음과 같다.

- FRONT: 공개 `GET /`이 `200`이고 `<div id="root"` marker를 포함
- DB: 공개 `GET /health`가 `200`이고 JSON `status=ready`; PostgreSQL이
  recovery 상태로 명시되면 쓰기 가능한 Primary가 아니므로 실패
- BACKEND: DB health 조건과 공개 `GET /api/courses/?size=1`의 객체 응답 및
  비어 있지 않은 `items` 확인
- CRAWLER: 현재 실행 노드의 timer 런타임과 durable cycle 메트릭이 유효하고, 나쁜
  outcome이 없으며, 요청 Provider 수가 1 이상이고 실패 Provider가 없고,
  마지막 완료가 최신성 한도 이내인지 확인

`functional_ok=false`면 `critical`, `functional_ok=null`이면 `unknown`이다.
기능 검사가 성공하고 `runtime_ok=true`면 `healthy`이며, 기능은 성공했지만
runtime이 비정상이거나 확인되지 않으면 `warning`이다. 따라서 FRONT, DB,
BACKEND의 runtime 정보만으로 기능 장애를 단정하지 않는다. CRAWLER timer가
명시적으로 비정상이면 `functional_ok=false`다. HTTP 연결·응답 계약 실패는
`false`, 필요한 Prometheus 자료가 없거나 쿼리 자체가 실패한 경우는 `null`이다.

core 설정은 다음 환경 변수를 사용한다.

| 환경 변수 | 기본값 | 정책 |
| --- | --- | --- |
| `MOONCEN_PUBLIC_BASE_URL` | `https://mooncen.kr` | FRONT, DB, BACKEND 공개 기능 검사 기준 주소 |
| `MONITOR_APP_PRIMARY_NODE` | `cloud` | topology active node와 DB/FRONT/BACKEND 기대 Primary |
| `MONITOR_APP_CRAWLER_RUNTIME_NODE` | `cloud` | 현재 CRAWLER timer와 cycle 메트릭 조회 노드 |
| `MONITOR_APP_CRAWLER_TARGET_NODE` | `gen1crawler` | 검토된 cutover 이후 worker 위치; runtime 판정에는 사용하지 않음 |
| `MONITOR_APP_CRAWLER_CONTROL_NODE` | `gen1db` | 공유 staging DB와 중앙 crawler-control 배치 위치; runtime 판정에는 사용하지 않음 |
| `MONITOR_APP_CRAWLER_NODE` | 없음 | 이전 runtime 설정의 호환 alias; runtime 변수가 없을 때만 사용 |
| `MONITOR_APP_CRAWLER_MAX_AGE_SECONDS` | `129600` | `3600`~`604800`초로 제한 |
| `MONITOR_APP_FUNCTIONAL_TIMEOUT_SECONDS` | `5` | 검사별 `1`~`15`초로 제한 |
| `MONITOR_APP_CORE_CACHE_TTL_SECONDS` | `30` | `5`~`300`초로 제한 |

숫자 설정을 해석할 수 없으면 표의 기본값을 사용한다.

### `GET /api/monitoring/core`

Android의 주기·백그라운드 조회용 경량 API다. 위 공통 계약과 snapshot
`generated_at`, `status`, `status_label`, core 전용 `counts`, `problems`를
반환하며 서버 목록, 일반 Prometheus 경보, OPS, 품질 데이터를 추가로 수집하지
않는다. `counts`에는 `core_services`, `healthy_services`, `failing_services`,
`warning_services`, `unknown_services`, `critical`, `warning`이 포함된다.
`critical`은 명시적 장애 서비스와 Primary 수이고, `warning`은 warning·unknown
서비스와 Primary 수다. `problems`에는 명시적 `critical`만 포함하며
warning·unknown은 복구나 장애로 단정하지 않는다. snapshot은 기본 30초 동안
캐시되어 같은 시간대의 여러 앱 요청이 공개 기능 probe와 Prometheus 조회를
반복하지 않는다.

내부 호환 경로 `GET /api/core`도 같은 응답을 제공하지만 공개 게이트웨이에서는
노출하지 않는다.

### `GET /api/monitoring/crawler`

Android `크롤러` 탭의 bounded read-only API다. 최상위 필드는
`schema_version=1`, `generated_at`, `available`, `complete`, `partial`,
`status`, `topology`, `latest`, `summary_24h`, `providers`, `nodes`, 선택적
`quality`, `errors`로
고정한다. `partial`은 `available && !complete`와 정확히 같아야 한다.

- `latest`: 현재 cycle의 실행 여부, 마지막 성공 시각/경과시간, 관측된 Provider
  성공·실패 수를 제공한다. 없는 collection/new/updated 수를 추정하지 않는다.
- `summary_24h`, `providers`: 중앙 analytics 근거가 있을 때만 수치를 제공한다.
  unavailable이면 모든 합계는 `null`, 항목은 빈 배열, reason code를 반환한다.
- `nodes`: `runtime`, `target`, `control`을 각각 정확히 한 행씩 반환하며 CPU,
  메모리, 1분 부하, 디스크, 논리 CPU 수와 온도를 포함한다.
- `quality`: root schema version을 올리지 않고 추가한 선택적 생산 DB 품질 요약이다.
  `MOONCEN_SERVER_MONITOR_BASE_URL`의 고정 경로
  `/api/monitoring/crawler-quality`를 별도 `MOONCEN_SERVER_MONITOR_TOKEN` 및
  `X-MoonCen-Monitor-Token` 헤더로 한 번 조회한다. 토큰은 URL-safe
  `[A-Za-z0-9_-]` 32~256자 계약을 양쪽에서 검증한다. 활성 강좌, 필수값 누락,
  날짜·가격 오류, 위치 불완전, 중복 URL, 동기화 차단 등의 count와 최근 품질
  스캔 시각을 제공한다. 토큰 누락, 연결 실패 또는 계약 오류에서는
  `available=false`, `reason_code`와 함께 모든 count를 `null`로 반환한다.
  선택적 품질의 성공·실패는 root `available`, `complete`, `partial` 계산에
  참여하지 않는다. 집계 transport timeout은
  `MOONCEN_SERVER_MONITOR_TIMEOUT_SECONDS`로 별도 제한하며 기본 `20`초,
  허용 범위는 `1..30`초다. 성공 결과는 240초, 실패 결과는 30초 캐시하고 cache
  miss의 최초 대기는 최대 2초다. 이후 집계는 단일 background refresh로 이어져
  Android의 crawler GET 전체를 20초 동안 막지 않는다. 만료된 값이 있으면 즉시
  직전 snapshot을 반환하면서 한 번만 갱신한다.
- 온도는 노드가 UP이고 센서 값이 유한한 `-20..130°C`일 때만 제공한다. Windows
  custom collector는 `success=1`, 센서 수 양수, 수집 시각 `-60..300초` 최신성까지
  만족해야 한다. 그 외 `temperature_available=false`, `temp_celsius=null`이다.

이 API는 오류나 미관측 상태를 `0`으로 합성하지 않는다. 내부 호환 경로
`GET /api/crawler`도 같은 응답을 제공하지만 공개 게이트웨이에서는 노출하지 않는다.

### `GET /api/monitoring/summary`

서버·Exporter·경보·백업 진단을 함께 조회하는 상세 요약이다. Android의 현재
탭과 백그라운드 주기 조회는 이 경로가 아니라 각 전용 경로와 경량 core 경로를
사용한다. 아래 추가
진단 필드는 core `status`와 `problems`를 변경하지 않는다. 공통 계약 필드 중
일부를 포함한 축약 예시는 다음과 같다.

```json
{
  "generated_at": "2026-07-29T03:00:00+00:00",
  "latency_ms": 125,
  "status": "healthy",
  "status_label": "정상",
  "errors": [],
  "counts": {
    "core_services": 4,
    "healthy_services": 4,
    "failing_services": 0,
    "warning_services": 0,
    "unknown_services": 0,
    "critical": 0,
    "warning": 0,
    "servers": 7,
    "down_servers": 0,
    "down_targets": 0,
    "active_alerts": 0,
    "mooncen_failures": 0,
    "backup_stale": 0,
    "backup_error": 0,
    "backup_unknown": 1
  },
  "problems": [],
  "servers": [],
  "targets": [],
  "alerts": [],
  "backup": {
    "items": [],
    "available": false,
    "health": "unknown",
    "fresh": null,
    "freshness_policy": "last trigger must be on or after yesterday 00:00 KST"
  }
}
```

`status`는 다음 세 값 중 하나다.

- `healthy`: 문제가 없음
- `warning`: 주의 문제만 있음
- `critical`: 하나 이상의 장애 문제가 있음

`counts.active_alerts`는 Prometheus에서 `state=firing`인 경보만 계산하는 진단
수치다. core의 `status`와 `problems`는 Primary와 네 `core_services`에서만
결정되며, candidate·auxiliary의 pending 경보는 core 장애로 승격하지 않는다.
`problems[].key`는 API 소비자가 명시적 장애를 식별하는 안정적인 값이므로 의미
없이 변경해서는 안 된다. Android 앱은 `primary`와 `core_services`의 상태를
읽어 `core:primary`, `core:database`, `core:frontend`, `core:backend`,
`core:crawler` 로컬 key로 장애와 복구를 비교한다.

백업 최신성은 `backup` 객체와 `backup_stale`, `backup_error`,
`backup_unknown` 진단 수치로 별도 제공되며 core 문제로 승격하지 않는다.

### `GET /api/monitoring/mooncen`

문센 화면용 core 상태를 크롤러·백업·DB 호환 필드로 재표현한 경량 요약이다.
별도 OPS 또는 데이터 품질 API를 호출하지 않는다. 위 공통 계약 필드를 제외한
호환 필드의 축약 예시는 다음과 같다.

```json
{
  "generated_at": "2026-07-29T03:00:00+00:00",
  "errors": [],
  "crawler": {
    "available": true,
    "ok": true,
    "status": "healthy",
    "detail": "CRAWLER 최근 정상 완료: 2026-07-29T01:00:00+00:00",
    "success_24h": 0,
    "failed_24h": 0,
    "collected_24h": 0,
    "latest_failures": [],
    "summary_24h": []
  },
  "backup": {
    "available": true,
    "health": "healthy",
    "fresh": true,
    "freshness_policy": "last trigger must be on or after yesterday 00:00 KST",
    "items": [
      {
        "node": "cloud",
        "name": "mooncen-backup.timer",
        "active": true,
        "fresh": true,
        "fresh_known": true,
        "health": "healthy",
        "last_success_at": null,
        "last_triggered_at": "2026-07-28T16:00:00+00:00",
        "last_triggered_at_kst": "2026-07-29T01:00:00+09:00",
        "timestamp_kind": "timer_trigger",
        "age_seconds": 39600,
        "fresh_after_kst": "2026-07-28T00:00:00+09:00",
        "freshness_policy": "last trigger must be on or after yesterday 00:00 KST",
        "source": "node_systemd_timer_last_trigger_seconds"
      }
    ]
  },
  "ops": {
    "ok": true,
    "database": "healthy",
    "tables": {},
    "latest_crawler_run": null,
    "latest_quality_check": null
  }
}
```

`success_24h`, `collected_24h`, 실행 목록은 하위 호환 자리표시자로 현재 `0` 또는
빈 배열이다. `failed_24h`는 CRAWLER가 `critical`일 때 `1`, 그 외에는 `0`이다.

#### 백업 신선도 계약

백업 상태에는 `cloud / mooncen-backup.timer` 한 항목만 포함한다. 일반
oneshot service의 현재 상태와 `dpkg-db-backup` 항목은 포함하지 않는다.

판정 원본은 다음 Prometheus 메트릭의 마지막 유효 값이다.

```text
node_systemd_timer_last_trigger_seconds{node="cloud",name="mooncen-backup.timer"}
```

신선도 기준은 요청 시점이 속한 **KST 날짜의 전날 00:00**이다. 예를 들어
요청 시점이 `2026-07-29 12:00 KST`이면 기준은
`2026-07-28 00:00 KST`이다.

- 마지막 timer 실행 시각이 기준 이상이고 현재보다 5분 넘게 미래가 아니면
  `fresh=true`, `fresh_known=true`, `health=healthy`다.
- 유효한 실행 시각이 기준보다 오래됐으면 `fresh=false`,
  `fresh_known=true`, `health=stale`다.
- 실행 시각이 현재보다 5분을 초과해 미래이면 신선함의 증거로 인정하지 않고
  `fresh=false`, `fresh_known=false`, `health=error`다.
- 유효한 메트릭이 없으면 `items=[]`, `available=false`,
  `health=unknown`, `fresh=null`이다.

하위 호환을 위해 `active` 필드를 유지하지만, 이 값은 이제 oneshot 서비스의
실행 여부가 아니라 `fresh`와 같은 신선도 의미다. 클라이언트는
`fresh`, `fresh_known`, `health`, `last_triggered_at`을 우선 사용해야 한다.
이 판정은 timer 예약 실행 시각의 최신성만 나타내며 백업 산출물의 성공,
존재, 복원 가능성 또는 무결성을 보증하지 않는다.

### `GET /api/monitoring/servers`

서버 화면용 자원 요약과 Tailscale 요약을 반환한다.

```json
{
  "generated_at": "2026-07-29T03:00:00+00:00",
  "errors": [],
  "servers": [
    {
      "node": "bot",
      "up": "UP",
      "cpu": "10.1%",
      "mem": "55.0%",
      "disk": "23.0%",
      "temp": "52C",
      "uptime": "4d 14h",
      "role": "monitoring",
      "alerting": ""
    }
  ],
  "tailscale": {
    "available": true,
    "status": "current",
    "stale": false,
    "counts": {
      "total": 5,
      "online": 4,
      "offline": 1
    }
  }
}
```

서버 자원 값은 표시용 문자열이며 값이 없으면 `-`일 수 있다. 상세
Tailscale 객체의 계약은 다음 절을 따른다.

### `GET /api/monitoring/tailscale`

Tailscale 상태 화면용 공개 읽기 API다. 백엔드 프로세스가 Tailscale
권한이나 명령 실행 권한을 직접 사용하지 않고, 별도 root oneshot 수집기가
작성한 정제된 schema version 1 snapshot만 읽는다.

```json
{
  "available": true,
  "status": "current",
  "stale": false,
  "generated_at": "2026-07-29T02:59:30Z",
  "age_seconds": 30,
  "backend_state": "Running",
  "counts": {
    "total": 5,
    "online": 4,
    "offline": 1
  },
  "summary": {
    "total": 5,
    "online": 4,
    "offline": 1,
    "direct": 2,
    "relay": 1
  },
  "self": {
    "name": "bot",
    "dns_name": "bot.example.ts.net",
    "os": "linux",
    "online": true,
    "active": true,
    "connection": "direct",
    "last_seen": "2026-07-29T02:59:29Z",
    "key_expiry": null
  },
  "peers": [],
  "error": null
}
```

snapshot의 기본 최신성 한도는 180초이며 운영 설정으로 30~3600초 범위에서
조정할 수 있다.

- 최신 snapshot: `available=true`, `status=current`, `stale=false`,
  `error=null`
- 오래됐거나 생성 시각이 현재보다 60초 넘게 미래인 snapshot:
  `available=true`, `status=stale`, `stale=true`,
  `error=snapshot_stale`
- 파일 없음: `available=false`, `status=unavailable`,
  `error=snapshot_missing`
- 안전하게 읽을 수 없음: `error=snapshot_unreadable`
- 형식, 크기 또는 schema가 잘못됨: `error=snapshot_invalid`

unavailable 응답은 `generated_at=null`, `age_seconds=null`,
`backend_state=Unknown`, 빈 `peers`, 0인 집계값을 사용한다. 오류는 일반화된
코드만 공개하며 내부 파일 경로, 권한 상세 또는 원본 예외를 노출하지 않는다.

POSIX 운영 환경에서 snapshot은 다음 검사를 통과해야 한다.

- 절대 경로의 일반 파일
- 심볼릭 링크가 아님
- root 소유
- group write/execute 및 모든 other 권한이 없음
- 크기가 1 MiB 이하
- UTF-8 JSON이며 지원하는 schema

피어의 `connection`은 `direct`, `relay`, `idle`, `offline`, `unknown` 중
하나다. `counts`는 피어 기준이며 `self`는 집계에 포함하지 않는다.

## 노드 영구 제외 정책

`ds1515`와 `ds718`은 API 응답과 문제 판정에서 영구 제외한다. 이 두 값은
설정 기본값이 아니라 코드에 내장된 최소 제외 집합이며 환경 변수로 제거할 수
없다. 추가 제외 노드는 운영 설정으로 더할 수만 있다.

제외 비교는 대소문자를 구분하지 않으며 다음 형태에도 적용한다.

- 짧은 노드 이름
- Tailscale FQDN의 첫 label
- 포트가 붙은 instance 주소

제외 범위는 다음과 같다.

- 서버 목록과 서버 상태 집계
- Prometheus scrape target
- Prometheus 경보와 문제 목록
- Tailscale `self`와 피어 목록 및 집계

새 endpoint 또는 새로운 집계 경로를 추가할 때도 동일한 제외 정책을 적용해야
한다.

## 내부 전용 API

### `GET /api/monitoring/trends`

기본 6시간의 CPU, 메모리, 온도 시계열을 반환한다. `hours` 쿼리
파라미터는 1~168 사이의 정수만 허용하며 잘못된 값에는 `400`을 반환한다.
이 경로는 공개 게이트웨이에서 차단한다.

### Operation API

```text
GET  /api/operation/actions
POST /api/operation/run
```

Operation API는 기본 비활성화 상태다. 비활성화 시 두 경로 모두 `404`를
반환한다. 활성화했더라도 operation 토큰이 서버에 설정되지 않았으면 `503`을
반환한다.

`GET /api/operation/actions`는 서버에 등록된 action ID, 표시 이름, 종류,
대상 노드만 반환한다. 명령 문자열, 접속 정보 또는 비밀값은 반환하지 않는다.

```json
{
  "operation_enabled": true,
  "operation_token_required": true,
  "operation_token_configured": true,
  "actions": [
    {
      "id": "restart_cloud_backend",
      "label": "cloud backend 재시작",
      "kind": "restart",
      "node": "cloud"
    }
  ]
}
```

`POST /api/operation/run`은 JSON의 등록된 action ID만 받는다.

```json
{
  "action": "restart_cloud_backend"
}
```

실행 요청에는 두 인증 헤더가 모두 필요하다.

```http
X-App-Token: <읽기 토큰>
X-Operation-Token: <작업 토큰>
```

성공적으로 명령을 실행한 경우의 응답 형태는 다음과 같다. `ok`는 프로세스
종료 코드가 0일 때만 `true`다.

```json
{
  "ok": true,
  "action": "restart_cloud_backend",
  "returncode": 0,
  "stdout": "",
  "stderr": "",
  "elapsed_seconds": 1.2
}
```

알 수 없는 action ID는 `400`, operation 인증 실패는 `401`, timeout은
`504`, 그 밖의 실행 오류는 `500`이다. 실행 시간에는 서버 설정의 timeout을
적용하고 `stdout`과 `stderr`는 각각 마지막 2,000자로 제한한다.

Operation 보안 원칙은 다음과 같다.

- 클라이언트에서 shell 문자열이나 임의 명령을 받지 않는다.
- 서버 코드에 등록된 action ID만 실행한다.
- 읽기 토큰과 operation 토큰을 분리한다.
- 공개 게이트웨이에서는 항상 차단한다.
- 실행 명령과 인프라 접속 세부 정보는 API 응답에 노출하지 않는다.

## 내부 호환 경로와 메트릭

백엔드는 기존 클라이언트를 위해 다음 호환 경로를 유지한다.

```text
GET  /api/core
GET  /api/summary
GET  /api/mooncen
GET  /api/servers
GET  /api/tailscale
GET  /api/operations
POST /api/operations/run
GET  /api/trends
```

이 경로들은 모두 공개 게이트웨이에서 `404`로 차단하며 사설 환경에서만
사용한다. 신규 클라이언트는 `/api/monitoring/*`와
`/api/operation/*` 경로를 사용해야 한다.

Prometheus용 `GET /metrics`도 백엔드 내부 전용이며 공개 게이트웨이에서
노출하지 않는다. `/metrics`는 `/api/*`가 아니므로 애플리케이션의
`X-App-Token` 검사 대상이 아니다. 따라서 네트워크와 게이트웨이 계층에서
반드시 접근을 제한해야 한다.

## 클라이언트 호환성과 보안 체크리스트

- 공개 앱은 여섯 개의 monitoring allowlist 경로만 사용한다.
- Android의 주기 조회는 경량 `GET /api/monitoring/core`를 사용한다.
- 공개 기본 주소에서 operation API를 호출하지 않는다.
- 모든 앱 통신은 HTTPS만 사용하고 리디렉션을 따르지 않는다.
- 토큰은 URL이나 JSON payload가 아니라 헤더로만 전달한다.
- 토큰은 앱 백업 대상에서 제외하고 비밀번호 입력 형식으로 표시한다.
- 부분 수집 실패와 빈 정상 결과를 구분한다.
- 백업은 oneshot의 현재 `active` 상태가 아니라 신선도 필드로 판단한다.
- Tailscale 오류는 일반화된 `error` 코드와 `available`, `stale`로 판단한다.
- `ds1515`, `ds718`을 어떤 화면, 집계 또는 알림에서도 다시 포함하지 않는다.
