# MoonCen Monitor API

MoonCen Monitor Android 앱에 서버·서비스·업무 상태를 제공하는 Flask API입니다.
Prometheus가 수집한 원본 메트릭을 조회하며 별도의 대시보드 서비스에 의존하지
않습니다.

## 역할

- Android 앱용 통합 상태 API 제공
- 운영 DB, FRONT, BACKEND, CRAWLER의 Primary 배치와 실제 기능 상태 제공
- 서버, 서비스, 수집 대상과 Prometheus 경보 통합
- MoonCen 핵심 서비스, 크롤러, 백업 상태 요약
- 기본 비활성화된 별도 인증 operation API 제공
- Prometheus용 파생 메트릭 `/metrics` 제공

## 운영 핵심 서비스 구성

검토된 production topology에서 DB, FRONT, BACKEND의 application Primary는
`cloud`입니다. 현재 실측된 CRAWLER timer와 cycle 메트릭도 아직 `cloud`에 있고,
검토된 worker 목표 위치는 `gen1crawler`입니다. 따라서 API는
`crawlerMode=legacy`, `crawler_transition_state=cutover_pending`,
`crawler_runtime_drift=true`를 반환합니다. 공유 staging DB와 중앙
crawler-control의 목표 배치 위치는 `gen1db`이며, 제어 노드는 크롤러 실행 노드
또는 production DB로 취급하지 않습니다.

Prometheus inventory에서는 현재 runtime을 가진 `cloud`가 운영 경보 대상입니다.
실제 distributed cutover가 검증되기 전까지 `gen1crawler`는
`role=crawler-worker`, `gen1db`는 `role=crawler-control`이고 둘 다
`alerting=pending`입니다. CRAWLER 필수 서비스 경보는 현재 실측 runtime인
`cloud`의 timer 중단만 사용합니다.

Primary는 단순히 응답하는 서버를 골라 추정하지 않습니다. 설정된 기대 노드의
PostgreSQL 쓰기 가능 상태를 기준으로 확인하고, 명시적인 역할 불일치도 함께
검사합니다. 역할 메트릭이 없더라도 이미 확인된 쓰기 가능 관측은 유지합니다.
기대 Primary가 응답하지 않더라도 다른 노드를 자동으로 Primary로 표시하지
않습니다.

핵심 서비스 상태는 프로세스 메트릭과 실제 기능 검사를 분리합니다. 기능 검사
결과가 없으면 `unknown`, 기능은 성공했지만 runtime 메트릭이 없으면
`warning`이며 둘 다 장애로 단정하지 않습니다. CRAWLER는 상시 실행 프로세스가
아니라 예약 one-shot이므로 timer 활성 상태와 최근 성공 시각의 최신성을
사용합니다.

## 운영 주소

Android 앱의 기본 주소는 다음과 같습니다.

```text
https://mon.binary.kr
```

Cloudflare Tunnel은 loopback Nginx `127.0.0.1:3000`으로 연결됩니다. 공개
게이트웨이는 다음 여섯 개의 읽기 API만 전달하며, operation·metrics·호환 경로와
trends는 `404`로 차단합니다.

```text
GET /api/monitoring/core
GET /api/monitoring/crawler
GET /api/monitoring/summary
GET /api/monitoring/mooncen
GET /api/monitoring/servers
GET /api/monitoring/tailscale
```

`GET /api/monitoring/crawler`는 Android의 `크롤러` 탭 전용 읽기 모델입니다.
현재 cycle 실행 여부와 마지막 성공, 24시간 수집/Provider 통계, 그리고 실제
runtime·목표 worker·중앙 control 노드의 CPU·메모리·부하·디스크·온도를
section별 `available` 근거와 함께 반환합니다. 중앙 통계 또는 센서 증거가 없으면
수치를 `0`으로 합성하지 않고 `null`과 reason code로 확인 불가를 명시합니다.

모든 요청은 `X-App-Token` 헤더를 사용합니다. 쿼리 문자열 토큰은 허용하지
않으며 서버 토큰이 설정되지 않으면 API는 fail-closed로 `503`을 반환합니다.

## 개발 실행

```bash
cd monitor_app
python3 -m pip install -r requirements.txt
python3 app.py
```

운영에서는 systemd가 Gunicorn을 `127.0.0.1:8088`에만 바인딩합니다.

## 환경 변수

- `MONITOR_APP_HOST`: 개발 서버 기본 `0.0.0.0`
- `MONITOR_APP_PORT`: 개발 서버 기본 `8088`
- `PROMETHEUS_URL`: 기본 `http://localhost:9090`
- `MOONCEN_OPS_API_BASE_URL`: 기본 `https://mooncen.kr/api/ops`
- `MOONCEN_SERVER_MONITOR_BASE_URL`: 생산 품질 전용 서버 API origin. 기본
  `https://mooncen.kr`; HTTPS origin만 허용하며 고정 경로
  `/api/monitoring/crawler-quality`를 사용
- `MOONCEN_SERVER_MONITOR_TOKEN`: 위 읽기 API에
  `X-MoonCen-Monitor-Token`으로 보내는 별도 서버 토큰. 미설정 시 품질 섹션만
  `available=false`, 모든 count `null`로 실패하며 크롤러 최상위 가용성에는
  영향을 주지 않음. upstream 인증과 동일하게 URL-safe 영문·숫자·`_`·`-`만
  사용한 32~256자 값이어야 함
- `MOONCEN_SERVER_MONITOR_TIMEOUT_SECONDS`: 생산 품질 DB 집계의 별도 transport
  timeout. `1`~`30`초 범위이며 기본 `20`초
- `MONITOR_APP_TOKEN`: `/api/*` 요청에 필요한 토큰
- `MONITOR_APP_OPERATION_ENABLED`: operation API 활성화 여부, 기본 `false`
- `MONITOR_APP_OPERATION_TOKEN`: operation 전용 별도 토큰
- `MONITOR_APP_TRUSTED_HOSTS`: 쉼표로 구분한 허용 Host 목록
- `MONITOR_APP_COMMAND_TIMEOUT_SECONDS`: operation 명령 timeout
- `MOONCEN_PUBLIC_BASE_URL`: FRONT와 BACKEND/DB 기능 검사 대상 공개 주소.
  기본 `https://mooncen.kr`
- `MONITOR_APP_PRIMARY_NODE`: production topology의 DB, FRONT, BACKEND
  Primary 노드. 기본 `cloud`
- `MONITOR_APP_CRAWLER_RUNTIME_NODE`: 현재 CRAWLER timer와 cycle 메트릭을
  조회할 노드. 기본 `cloud`
- `MONITOR_APP_CRAWLER_TARGET_NODE`: 검토된 cutover 이후 worker 위치. 기본
  `gen1crawler`. 현재 runtime 판정에는 사용하지 않음
- `MONITOR_APP_CRAWLER_CONTROL_NODE`: 공유 staging DB와 중앙 crawler-control
  배치 노드. 기본 `gen1db`. 이 값은 CRAWLER runtime 판정에 사용하지 않음
- `MONITOR_APP_CRAWLER_NODE`: 이전 설정과의 호환 alias. runtime 변수가 없을
  때만 사용하며 새 배포에서는 설정하지 않음
- `MONITOR_APP_CRAWLER_MAX_AGE_SECONDS`: 마지막 성공 CRAWLER 실행을
  정상으로 인정하는 최대 나이. `3600`~`604800`초 범위이며 기본
  `129600`초(36시간)
- `MONITOR_APP_FUNCTIONAL_TIMEOUT_SECONDS`: 공개 기능 검사별 timeout.
  `1`~`15`초 범위이며 기본 `5`초
- `MONITOR_APP_CORE_CACHE_TTL_SECONDS`: core snapshot 캐시 시간.
  `5`~`300`초 범위이며 기본 `30`초
- `MONITOR_APP_TAILSCALE_SNAPSHOT_FILE`: root 수집기가 생성한 sanitized
  Tailscale snapshot 경로. 기본
  `/var/lib/mooncen-monitor/tailscale-status.json`
- `MONITOR_APP_TAILSCALE_SNAPSHOT_MAX_AGE_SECONDS`: snapshot 최신성 기준.
  30~3600초 범위이며 기본 `180`
- `MONITOR_APP_EXCLUDED_NODES`: 쉼표로 구분한 제외 노드. 기본값은
  `ds1515,ds718,n100`이며 서버, scrape target, Prometheus 경보와 Tailscale
  응답에 동일하게 적용됩니다.

기존 `TAILSCALE_STATUS_FILE`, `TAILSCALE_STATUS_MAX_AGE_SECONDS`도
하위호환 alias로 지원하지만 `MONITOR_APP_TAILSCALE_SNAPSHOT_*` 설정을
우선합니다.

비밀이 아닌 운영 예시는 `../mooncen-monitor-snapshot.env.example`에 있습니다.
systemd 서비스는 기존 `/etc/mooncen-monitor-app.env`와 선택적
`/etc/mooncen-monitor-snapshot.env`를 읽으므로 unit 변경 없이 값을 설정할 수
있습니다. 보호된 앱 환경 파일의 빈 템플릿은
`../mooncen-monitor-app.env.example`입니다. 운영 서버에서는 이를 `root:root`,
mode `0600`으로 설치하고 `MONITOR_APP_TOKEN`과
`MOONCEN_SERVER_MONITOR_TOKEN`을 서로 다른 값으로 주입합니다. 서버 모니터 토큰은
Android 설정, Gradle 설정, APK 또는 공개 download manifest에 넣지 않습니다.
`monitoring.compose.yml`은 Prometheus만 실행하므로 이 API 전용 변수를
추가하지 않습니다. 공개 Nginx allowlist에는 Android가 사용하는
`GET /api/monitoring/core`를 포함해야 하며 내부 호환 경로 `GET /api/core`는
공개하지 않습니다.

생산 품질 집계는 성공 결과를 240초, 실패 결과를 30초 캐시합니다. 캐시가 없을 때
앱 요청은 최대 2초만 최초 갱신을 기다리고, 더 오래 걸리는 최대 20초의 DB 집계는
단일 daemon 갱신으로 계속됩니다. 만료된 캐시가 있으면 즉시 직전 결과를 반환하면서
한 번만 갱신합니다. 따라서 Android의 crawler GET deadline을 품질 집계 하나가
소진하지 않으며 같은 응답에서 upstream을 중복 호출하지 않습니다.

## API

Monitoring:

```text
GET /api/monitoring/core
GET /api/monitoring/summary
GET /api/monitoring/mooncen
GET /api/monitoring/servers
GET /api/monitoring/tailscale
GET /api/monitoring/trends
```

Operation:

```text
GET  /api/operation/actions
POST /api/operation/run
```

Operation API는 기본적으로 비활성화되어 있고 공개 게이트웨이에서는 항상
차단됩니다. 활성화하더라도 `X-Operation-Token`이 추가로 필요합니다.
`gen1crawler crawler 재시작` action은 `sgm` 계정으로 접속한 뒤 sudoers에
등록된 `/usr/local/libexec/mooncen-ops-service crawler-once`만 실행합니다.

Prometheus:

```text
GET /metrics
```

### 핵심 서비스와 Primary

Android의 주기 조회 경로는 `GET /api/monitoring/core`입니다. 이 경로는
캐시된 core snapshot만 반환하며 OPS·품질·서버 상세 수집을 실행하지 않습니다.
`GET /api/monitoring/summary`와 `GET /api/monitoring/mooncen`도 다음 필드를
같은 계약으로 반환합니다.

- `topology`: production 환경, application active node, crawler mode, 현재
  runtime·목표 worker·중앙 제어 위치와 cutover 상태
- `primary`: 기대 Primary와 관측 역할·DB 쓰기 가능 상태의 일치 결과
- `core_services`: DB, FRONT, BACKEND, CRAWLER 네 행의 런타임·기능 상태
- `services`: `core_services`와 같은 네 행을 담는 하위 호환 alias

`runtime_ok`와 `functional_ok`는 관측할 수 없을 때 JSON `null`입니다.
행의 `status`는 `healthy`, `warning`, `critical`, `unknown` 중 하나입니다.
실제 기능 실패만 `critical`이며, 기능은 성공했지만 runtime을 확인하지 못했거나
비정상이면 `warning`입니다. `warning`과 `unknown`을 장애 또는 복구로 알리면
안 됩니다. 자세한 필드 계약은 `../docs_ops_api_contract.md`를 참고하세요.

### Tailscale snapshot

`GET /api/monitoring/tailscale`은 API 프로세스가 직접 Tailscale 권한을
사용하지 않고, 별도 root oneshot이 생성한 allowlist snapshot만 읽습니다.
응답의 `summary`에는 `total`, `online`, `offline`, `direct`, `relay` 수가
포함되며 오래된 snapshot은 `available=true`, `status=stale`,
`error=snapshot_stale`로 표시됩니다. root 소유가 아니거나 권한이 넓거나
형식이 잘못된 파일은 일반화된 unavailable 응답으로 처리합니다.

system group, timer, 파일 권한과 설치·검증 절차는
`../tailscale_snapshot_deployment.md`를 참고하세요.

### MoonCen 백업 최신성

`GET /api/monitoring/mooncen`의 `backup.items`에는
현재 production topology의 `cloud / mooncen-backup.timer`만 포함됩니다.
일반 oneshot service와 `dpkg-db-backup` 항목은 포함하지 않습니다.

최신성은 Prometheus의
`node_systemd_timer_last_trigger_seconds{node="cloud",name="mooncen-backup.timer"}`
값으로 계산합니다. 현재 KST 날짜의 전날 00:00 이후에 timer가 실행된 기록이
있으면 `fresh=true`, `health=healthy`입니다. 그보다 오래됐으면
`fresh=false`, `health=stale`입니다. 이는 예약 실행 시각에 대한 지표이며
백업 산출물 자체의 무결성 검증 결과를 뜻하지 않습니다.

상세 summary API도 같은 결과를 `backup`과
`counts.backup_stale`, `counts.backup_error`, `counts.backup_unknown`에
반영합니다. 최신 지표가 없으면 수집기 장애로 단정하지 않고
`warning / unknown` 백업 문제 한 건을 반환합니다.

자세한 계약은 `docs_ops_api_contract.md`를 참고하세요.

## 테스트

```bash
python -m unittest discover -s monitor_app -p "test_*.py" -v
```

Prometheus 설정을 배포할 때는 프로젝트 루트의 `prometheus.remote.yml`과
`prometheus.rules.yml`을 함께 설치한 뒤 `promtool`로 검사하세요.
