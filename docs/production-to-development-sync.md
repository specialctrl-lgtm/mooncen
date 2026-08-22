# 운영 PostgreSQL → 개발 DB 동기화

`tools/sync_production_to_development.py`는 운영(`cloud`) 데이터를 로컬/개발 PostgreSQL로 가져오되 계정, OAuth, 알림 토큰, 사용자 활동, 세션성 데이터와 Ops 로그를 개발 환경에 남기지 않는 교체 도구다. 이 도구는 운영 DB를 수정하지 않으며 DSN을 입력이나 로그 형식으로 사용하지 않는다.

## 안전 계약

- 운영 연결에는 항상 `default_transaction_read_only=on`을 강제하고 `psql` 사전 확인과 `pg_dump`만 실행한다. 운영 로그인이 superuser, DB `CREATE` 권한 보유자 또는 어떤 비시스템 테이블이라도 쓰기 가능한 역할이면 거부한다.
- 대상 호스트·DB·사용자에 `cloud`, `prod`, `production`, `live` 표식이 있으면 확인 옵션과 관계없이 거부한다.
- 두 연결의 설정 호스트가 같거나 실제 PostgreSQL 서버 주소/포트 또는 DB catalog fingerprint가 같으면 DB 이름이 달라도 거부한다. 개발 DB는 운영과 다른 PostgreSQL cluster에 있어야 한다.
- 실행에는 `--confirm-development-replace` 값이 대상 DB 이름과 대소문자까지 정확히 같아야 한다.
- 알려진 개인정보 테이블은 `pg_dump --exclude-table-data`로 아카이브에서 먼저 제외한다. 격리 DB 복원 뒤에는 스키마를 다시 검사해 사용자/OAuth/세션/토큰/알림/Ops/감사 데이터를 비우고 잔존 행이 0인지 확인한다.
- 기존 개발 DB를 즉시 삭제하지 않는다. 검증된 격리 DB를 활성화한 뒤 다시 검사하고, 성공한 경우에만 이전 개발 DB를 삭제한다. 전환 검증 실패 시 이전 개발 DB로 롤백한다.
- manifest에는 단계, 종료 코드, 시간, 아카이브 해시, 삭제 행 수만 기록한다. 비밀번호, DSN, 명령 stderr는 기록하지 않는다.
- 덤프는 권한이 제한된 임시 디렉터리에만 생성하고 모든 경로에서 종료 시 삭제한다. SSD/스냅샷 계층의 물리적 완전 삭제는 운영체제가 보장하지 않으므로 암호화된 로컬 디스크를 사용한다.

보존되는 것은 지점·강좌 등 공개 카탈로그 데이터다. 다음 종류는 행 단위로 삭제된다.

- `users`, `oauth_accounts`, `user_*`, `notifications`, `course_alerts`, `search_logs`
- `ops_*` 전체
- 크롤러 실행/진행/스테이징/검증 로그와 `course_update_requests`
- 향후 추가되는 테이블 중 이름이나 컬럼이 사용자, OAuth, 세션, 토큰, 이메일, 비밀번호, FCM 토큰, IP 주소 등의 규칙에 맞는 데이터

시설의 공개 연락처인 `branches.phone`은 공개 카탈로그 데이터로 유지된다.

## 사전 조건

1. PostgreSQL 클라이언트 `psql`, `pg_dump`, `pg_restore`가 설치되어 있어야 한다. PATH에 없다면 `MOONCEN_SYNC_PG_BIN`에 세 실행 파일이 있는 디렉터리를 지정한다.
2. 운영 계정은 `CONNECT`와 필요한 스키마/테이블의 읽기 권한만 가진 전용 dump 계정을 사용한다. 도구가 세션 읽기 전용을 추가로 강제하지만 쓰기 권한을 가진 애플리케이션 계정을 재사용하지 않는다.
3. 개발 소유자는 격리 DB에 스키마를 복원하고 `TRUNCATE`, `COMMENT`할 수 있어야 한다.
4. 개발 관리자 계정은 개발 PostgreSQL에서 `CREATE DATABASE`, `ALTER DATABASE`, `DROP DATABASE`, 개발 연결 종료 권한이 있어야 한다. 운영 PostgreSQL의 관리자 계정을 사용하지 않는다.
5. 전환 직전 로컬 API·worker처럼 개발 DB에 자동 재접속하는 프로세스를 중지한다. 도구가 기존 세션을 종료하지만 빠른 자동 재접속은 이름 전환과 경합할 수 있다.
6. manifest 디렉터리는 POSIX에서 권한 `0700`이어야 한다.

운영 DB에는 다음처럼 명시적인 환경 표식을 두는 것을 권장한다. 이 표식이 대상에서 발견되면 도구는 hard fail한다.

```sql
ALTER DATABASE mooncen SET mooncen.environment = 'production';
COMMENT ON DATABASE mooncen IS 'mooncen.environment=production';
```

## 설정

도구는 `.env`를 자동으로 읽지 않는다. 비밀번호나 DSN을 명령행 인자로 전달하지 말고 현재 프로세스의 개별 환경 변수로만 설정한다.

```powershell
$env:MOONCEN_SYNC_SOURCE_HOST = 'cloud'
$env:MOONCEN_SYNC_SOURCE_PORT = '5432'
$env:MOONCEN_SYNC_SOURCE_DATABASE = 'mooncen'
$env:MOONCEN_SYNC_SOURCE_USER = 'mooncen_sync_reader'
$env:MOONCEN_SYNC_SOURCE_PASSWORD = '<read-only-password>'
$env:MOONCEN_SYNC_SOURCE_SSLMODE = 'require'

$env:MOONCEN_SYNC_DEST_HOST = 'localhost'
$env:MOONCEN_SYNC_DEST_PORT = '5432'
$env:MOONCEN_SYNC_DEST_DATABASE = 'mooncen_dev'
$env:MOONCEN_SYNC_DEST_USER = 'mooncen_dev_owner'
$env:MOONCEN_SYNC_DEST_PASSWORD = '<development-owner-password>'
$env:MOONCEN_SYNC_DEST_SSLMODE = 'prefer'
$env:MOONCEN_SYNC_DEST_ENVIRONMENT = 'development'

$env:MOONCEN_SYNC_DEST_ADMIN_DATABASE = 'postgres'
$env:MOONCEN_SYNC_DEST_ADMIN_USER = 'mooncen_dev_admin'
$env:MOONCEN_SYNC_DEST_ADMIN_PASSWORD = '<development-admin-password>'
```

관리자 호스트/포트/SSL 설정은 기본적으로 개발 대상 설정을 상속한다. 다른 개발 관리자 연결값이 필요하면 `MOONCEN_SYNC_DEST_ADMIN_HOST`, `..._PORT`, `..._SSLMODE`도 지정할 수 있다. `*_DSN`과 `*_URL` 설정은 의도적으로 거부된다.

## 실행 순서

먼저 연결하지 않는 계획을 만든다. 계획 모드에서는 비밀번호가 없어도 된다.

```powershell
python tools/sync_production_to_development.py --plan
```

그다음 두 DB에 읽기 전용 사전 점검만 수행한다. 덤프나 DB 생성은 하지 않는다.

```powershell
python tools/sync_production_to_development.py --dry-run
```

manifest에서 `status=validated`, source read-only, 서로 다른 server fingerprint, destination production marker false를 확인한 뒤 실제 교체한다.

```powershell
python tools/sync_production_to_development.py `
  --execute `
  --confirm-development-replace mooncen_dev
```

성공 출력은 비밀값 없이 `status`, `run_id`, `manifest` 경로만 제공한다. 성공 기준은 manifest의 `status=succeeded`, `sanitization.remaining_sensitive_rows=0`, `result.cleanup_completed=true`다.

`pg_dump` 또는 `pg_restore`가 non-zero로 끝나면 같은 종료 코드가 단계 manifest에 남고 전체 실행도 실패한다. 전환 전 실패에서는 도구가 만든 격리 DB만 삭제한다. `status=recovery_required`이면 자동 롤백까지 실패한 것이므로 manifest의 `recovery_required` DB 이름을 확인하고 개발 PostgreSQL 관리자와 수동 복구해야 하며, 추가 실행으로 상태를 덮지 않는다.
