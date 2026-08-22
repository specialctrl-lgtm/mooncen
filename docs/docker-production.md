# Docker production release

이 문서는 MoonCen 애플리케이션의 새 Docker 운영 경로를 다룬다. 기준 구현은
`deploy/docker/mooncen_container_release.py`,
`deploy/docker/bootstrap_production_runtime.py`,
`deploy/docker/install_production_runtime.sh`,
`deploy/docker/production_runtime_integrity.py`,
`deploy/docker/compose.production.yaml`과 두 개의
`mooncen-container-*.service` unit이다. 개발 측의 review snapshot, 단일 build,
an2p 검증 절차는 [Docker development stack](docker-development.md), Ops 증적
스키마와 API는 [Ops Console Docker 배포 증적 계약](docker-ops-console.md)을 함께
본다.

## 현재 상태를 추측하지 않는 전환 gate

문서의 과거 host 확인 결과를 현재 상태로 간주하지 않는다. 전환 창에서 an2p phase-1
Docker health, cloud의 승인된 Docker 패키지/daemon/Compose, fresh backup, guarded native
release, controller receipt를 다시 증명한다. 현재 시작한 배포가 native 배포라면 그것만으로
Docker 전환이 아니다. 아래 `promote`는 Ops approval 소비 기록, DB claim, 원격 exclusive
fence와 `stage -> load-images -> preflight -> promote -> status` 결과가 모두 일치할 때만
실행한다.

## 불변 릴리스 흐름

```text
reviewed snapshot commit/tree
        |
        | build_release_bundle.py --platform linux/amd64 (API/frontend 각 1회)
        v
release.json + images.tar + compose.production.yaml
        |
        | an2p: images.tar load, --no-build --pull never smoke
        v
validation.json (target=an2p-dev, exact target_identity, PASS, unexpired)
        |
        | DB lease claim -> remote exclusive lease-bind
        | fixed deploy-user ingress에 네 파일 전송
        v
stage -> load-images -> preflight (runtime cutover 없음)
        |
        | promote
        v
candidate 18001/15173 -> active 8001/5173 -> durable current/previous state
        |
        | exact status -> DB terminal commit -> exclusive lease-release
```

릴리스 식별자는 mutable tag가 아니라 다음 값의 결합이다.

- reviewed `source_tree`와 canonical `release_digest`
- `images.tar`의 `bundle_sha256`
- API/frontend의 실제 Docker image ID `sha256:<64 hex>`
- production Compose와 build policy, migration ledger의 SHA-256
- 이 릴리스에 정확히 묶인 canonical `validation.json`의 `receipt_digest`

API와 frontend 이미지는 clean, detached release checkout에서 각각 한 번만
build한다. an2p와 cloud는 같은 `images.tar`를 load한다. production에는 build
context가 없고 registry push/pull도 없다. AI와 migration container는 manifest의
API image ID를 재사용한다. an2p의 PostgreSQL image는 개발 smoke 전용이며 운영에
승격하지 않는다.

## host-native 경계

Docker로 전환되는 것은 `api`, `frontend`, `ai`와 read-only migration check뿐이다.
다음 항목은 container controller의 권한 밖이며 전환 중에도 host-native로 유지한다.

- **PostgreSQL**: `/var/run/postgresql` Unix socket을 API/AI/migration container에
  read-only bind한다. DB container나 TCP DB endpoint를 만들지 않는다. Ubuntu의
  기본 `local all all peer`는 그대로 보존하고, 그 앞에 MoonCen DB와 migrator/API/AI
  login 각각에만 `scram-sha-256` 규칙을 둔다. 설치는 원본 HBA를 atomic 교체한 뒤
  `pg_reload_conf()`, `pg_hba_file_rules`, 세 role의 SCRAM verifier와 실제 password
  socket login을 모두 증명하며 실패 시 원본 byte를 복원하고 다시 reload한다.
- **nginx**: host nginx가 계속 `127.0.0.1:8001`과 `127.0.0.1:5173`을 origin으로
  사용한다. controller는 nginx 설정을 설치하거나 nginx를 재시작하지 않는다.
- **cloudflared**: host `cloudflared.service`와 Cloudflare health gate를 변경하지
  않는다. systemd의 `Before=`/`After=`는 boot 순서일 뿐 제어 권한이 아니다.
- **backup**: `mooncen-backup.service`/timer, NAS 증적과 restore 절차를 실행하거나
  변경하지 않는다. fresh backup 확인은 별도 전환 승인 조건이다.
- **deploy SSH**: 장기 실행 worker는 pinned `cloud-container-deploy` forced endpoint만
  사용한다. 최초 bootstrap과 native maintenance의 human interactive Tailscale 경로는
  별도이며 그 full-shell credential을 API/worker/DB tunnel service가 읽을 수 없다.
  Docker socket이나 SSH key를 container에 mount하지 않는다.
- crawler workers, crawler control plane, Ops Console, monitoring도 이 Compose
  project에 들어가지 않는다.

첫 Docker 승격 때 controller가 snapshot하고 stop/disable하는 native unit은 정확히
`mooncen-api.service`, `mooncen-frontend.service`, `mooncen-ai-worker.service` 세 개다.
PostgreSQL, nginx, cloudflared, backup, SSH 또는 crawler unit은 대상이 아니다.

## 필요한 root-owned 입력과 권한

controller는 symlink, 잘못된 owner, 넓은 쓰기 권한, 예상 밖의 release 파일을
fail-closed로 거부한다. 권장 설치 상태는 다음과 같다.

| 경로 | owner/mode | 용도 |
| --- | --- | --- |
| `/usr/local/libexec/mooncen-container-bootstrap` | `root:root 0755` | stdin identity만 받는 최초 설치/upgrade helper |
| `/etc/mooncen/container-bootstrap.json` | `root:root 0600` | native release에서 고정한 source/policy/deploy UID |
| `/usr/local/libexec/mooncen-container-release` | `root:root 0755` | 고정 wrapper |
| `/usr/local/libexec/mooncen-container-release-lib/` | `root:root 0755` | controller/manifest/verifier Python |
| `/etc/mooncen/container-runtime-installation.json` | `root:root 0600` | build policy와 installed byte receipt |
| `/etc/systemd/system/mooncen-container-stack.service` | `root:root 0644` | boot convergence |
| `/etc/systemd/system/mooncen-container-release-guard@.service` | `root:root 0644` | orphan transaction recovery |
| `/etc/mooncen/an2p-dev-target-identity` | `root:root 0600` | 신뢰하는 an2p policy identity 한 줄 |
| `/etc/mooncen/api.env`, `ai.env` | `root:<native-service> 0640` | native rollback 전용; mode를 바꾸지 않음 |
| `/etc/mooncen/container-api.env` | `root:root 0600` | public API 전용 allowlist runtime secret |
| `/etc/mooncen/container-ai.env` | `root:root 0600` | AI 전용 allowlist runtime secret |
| `/etc/mooncen/container-migrator.env` | `root:root 0600` | ledger check용 DB name/user/password만 포함 |
| `/etc/mooncen/container-frontend-runtime-config.js` | `root:root 0644` | allowlist된 public browser config |
| `/etc/postgresql/16/main/pg_hba.conf` | `postgres:postgres 0640` | exact MoonCen local SCRAM 규칙과 기존 peer fallback |
| `/usr/local/libexec/mooncen-configure-container-pg-hba` | `root:root 0755` | HBA atomic install/reload/실접속 검증 |
| `/usr/local/libexec/mooncen-native-runtime-condition` | `root:root 0755` | native/container 동시 시작 차단 |
| `/var/lib/mooncen-container-ingress/` | `<deploy-user>:<deploy-group> 0700` | 고정 SFTP/SCP inbox; 경로 인자를 받지 않음 |
| `/opt/mooncen-container-releases/` | `root:root 0700` | 불변 릴리스 root |
| `/opt/mooncen-container-releases/<source_tree>/` | `root:root 0700` | 한 릴리스; symlink 금지 |
| `images.tar` | `root:root 0600` | exact saved images |
| `release.json`, `validation.json`, `compose.production.yaml` | `root:root 0644` | canonical evidence/Compose |
| `/var/lib/mooncen-container-release/` | `root:root 0700` | active state, journal, lock |

릴리스 디렉터리에는 위 네 파일만 있어야 한다. controller가 쓰는 `active.json`,
`transaction.json`, `native-intent.json`, `operation.lock`, `control.lock`은 root-only
`0600`이고 직접 편집하지 않는다. `control.lock`은 controller mutation과 installer를
직렬화하고, operation lock 아래 atomic `status` snapshot은 state/transaction/native
intent가 서로 다른 시점에서 읽히는 일을 막는다. controller가 container를 정지한 뒤
native를 복구하는 짧은 구간만 `/run/mooncen-container-release/native-restore.json`
(`root:root 0600`, parent `0700`)의 exact transaction token으로 허용한다.
stack과 transaction guard unit은 모두 systemd `RuntimeDirectory`로 이 parent를
`0700` 생성하고 `ProtectSystem=strict` sandbox의 exact write path로만 연다. 두 unit이
같은 안전 경계를 공유하므로 `RuntimeDirectoryPreserve=yes`로 unit restart/stop 사이의
경로 제거 race를 막으며, reboot 때 빈 `/run`에서도 systemd가 controller 실행 전에
다시 만든다. 남은 authorization 파일이 있어도 현재 transaction token과 일치하지
않으면 native `ExecCondition`은 거부한다.
`/var/run/postgresql`은 실제 디렉터리여야 하며 symlink여서는 안 된다. runtime env나
receipt에 secret을 넣지 않는다.

target identity의 cloud source of truth는 root-owned 파일이다. Gate 6의 reviewed exporter stream은
그 64-hex 값을 strict 8-field control envelope에 넣고, an2p receiver가 phase-1 pending receipt와
같은지 확인한다. Gate 7 finalizer는 저장된 값만 믿지 않고 status와 deploy 두 forced endpoint의
다음 고정 read-only 명령이 반환하는 exact canonical JSON도 각각 strict parse한다.

```text
/usr/bin/sudo -n -- /usr/local/libexec/mooncen-container-release target-identity
{"schema_version":1,"target":"an2p-dev","target_identity":"<64 lowercase hex>"}
```

## build와 an2p PASS receipt

전체 snapshot 명령은 development 문서의 **Persistent an2p development runtime**
절을 따른다. 핵심 build는 clean detached checkout에서 한 번만 실행한다.

```bash
"$repo/.venv/bin/python" \
  "$release_checkout/deploy/docker/build_release_bundle.py" \
  --source-root "$release_checkout" \
  --output-root "$release_root" \
  --base-commit "$base_commit" \
  --source-tree "$source_tree" \
  --snapshot-commit "$snapshot_commit" \
  --platform linux/amd64
release_dir="$release_root/$source_tree"
```

an2p에서 clean release checkout과 local amd64 Docker daemon으로 canonical target
identity를 계산하고, prebuilt API/frontend만 load하여 검증한다.

```bash
target_identity="$("$repo/.venv/bin/python" \
  "$release_checkout/deploy/docker/smoke.py" \
  --print-development-target-identity)"

"$repo/.venv/bin/python" "$release_checkout/deploy/docker/smoke.py" \
  --release-directory "$release_dir" \
  --receipt-output "$release_dir/validation.json" \
  --validation-target an2p-dev \
  --target-identity "$target_identity" \
  --platform linux/amd64
```

이 release validation은 clean checkout, exact snapshot commit/tree/base, bundle와
Compose digest, daemon platform, 실제 image ID, migration ledger, API/frontend health,
DB least privilege, public/Ops route boundary와 runtime hardening을 확인한다. 여섯 check가
모두 `true`일 때만 `status=passed`다. receipt는 기본 24시간, 최대 168시간 유효하며
새 경로에 한 번만 쓸 수 있다. 실패하거나 만료한 receipt를 덮어쓰지 말고 새 reviewed
release와 receipt를 만든다.

개발 smoke는 무작위 이름의 격리된 ephemeral project/volume만 자체 정리한다.
그 내부 구현의 `down --volumes`는 운영 project에 같은 명령을 허용한다는 뜻이 아니다.

## cloud 설치

먼저 승인된 OS 설치 경로로 an2p와 cloud에 Docker Engine, CLI와 Compose plugin을
설치한다. Ubuntu 24.04에서는 `apt-get install docker.io docker-compose-v2
docker-buildx`와 `systemctl enable --now docker`를 사용하며, 임의의 curl-pipe
installer를 사용하지 않는다. production controller가 요구하는 cloud 조건은 root
기준 `default` context, local Unix socket, `linux/x86_64`, 작동하는 `docker compose`다.

### 권위 있는 최초 설치 순서

다음 gate는 production mutation 순서다. 문서 뒤쪽의 상세 reference block이 배치된 순서로
실행하지 말고 이 목록을 따른다.

1. **an2p phase 1 먼저**: trusted installer가 exact pair를 publish하고 persistent
   `mooncen-docker-dev.service`를 enable/start하여 `127.0.0.1:8001/health`와 `5174`를
   통과한다. 이 시점에는 cloud, production DB, endpoint, exporter stream을 변경하지 않고
   Ops API/worker/status/tunnel도 시작하지 않는다.
   pair manager와 human selector는 같은 root operation lock을 사용하며 성공 JSON은 exact
   Docker status와 두 health를 그 lock 아래 마지막으로 확인한 뒤에만 출력한다.
2. an2p root의 one-use `0700` stage에서 exact comment의 서로 다른 Ed25519 key 세 개를
   만들고 private key bytes를 pending pair의 strict receiver에 바로 publish한다. receiver가
   canonical public blobs의 distinctness를 검증한다. private source copy는 제거하며 `.pub`만
   cloud root staging에 전달할 수 있다. 이 단계는 production mutation이 아니다.
3. fresh backup 증적을 확인한 뒤 같은 reviewed source의 guarded native setup을 cloud에
   배포한다. 이 단계가 controller/bootstrap source·helper contract, integrity/exporter와
   exact no-argument human `sudo -n` rule을 설치한다. 또한 live PostgreSQL HBA를 atomic
   교체·reload하고, 전용 worker의 TLS/SCRAM 접속과 다른 DB 거부를 실제 probe한 뒤에만
   exporter를 게시한다. container controller/library와 stack/guard unit은 아직 설치·기동하지
   않으며 Gate 5 target-identity bootstrap만 이를 설치한다. 이 단계는 container를 선택하지도
   않는다.
4. `.pub` 세 개와 reviewed endpoint files를 interactive human 경로로 cloud root staging에
   넣고 provisioner를 실행한다. 이때 dedicated UID/config를 만들고 legacy shared public
   key를 제거한다. 성공 뒤 one-use public stage도 제거한다.
5. endpoint provision으로 `/etc/mooncen/container-bootstrap.json`이 dedicated deploy
   UID/GID에 결박된 뒤에만 phase-1 pending receipt의 64-hex `target_identity`를 cloud
   bootstrap stdin으로 전달한다.
6. 별도 non-secret Tailscale pre-auth를 끝낸 뒤 cloud exporter의 stdout을 an2p active
   immutable pair의 strict receiver stdin에 직접 연결한다. 동일 receiver에 세 reviewed
   ssh_config와 세 reviewed known_hosts를 넣고 immutable prepare를 실행한다.
7. trusted `finalize-control --pair <exact-pair>`가 live status/deploy identity/status와 DB
   registration 전용 tunnel을 stage/start하여 검증한 다음 evidence handoff와 dedicated
   worker DB registration을 수행한다. registration 성공 뒤에만 Ops API/worker/status를
   시작하고 tunnel을 포함한 최종 durable control plane을 수렴한다.
   isolated convergence와 최종 success proof도 같은 selector fence를 유지하므로 concurrent
   `native-select`를 성공으로 오인하지 않는다.

Gate 1의 exact install/health, Gate 6의 direct pipe 및 Gate 7 명령은
[`deploy/an2p/README.md`](../deploy/an2p/README.md)에 있다. phase 1 전에 이 절의 cloud setup,
endpoint provision, target bootstrap, exporter 호출을 실행하면 안 된다.

### Gates 2와 4 reference: service SSH key와 endpoint를 분리

이 단계는 container service credential이 아니라 승인된 human operator의 interactive
Tailscale/cloud console에서 수행한다. Gate 1 PASS 뒤 an2p key를 먼저 생성·receive하고,
Gate 3 guarded native setup 뒤에 public key로 endpoint를 provision한다. 기존 `ubuntu`
full-shell key를 Ops API나 worker가 읽게 하지 않는다. receiver destination인
`/root/mooncen-an2p-bootstrap`에 `ssh-keygen`으로 직접 써서 pending-pair 검증을 우회하면 안
된다. comment도 provisioner 계약의 일부다. 아래 generation source는 receiver success 뒤
private file을 지우고 public file만 Gate 4 cloud root staging까지 보존한다.

```bash
key_stage=/run/mooncen-an2p-key-generation
[ ! -e "$key_stage" ] && [ ! -L "$key_stage" ]
sudo install -d -o root -g root -m 0700 "$key_stage"
sudo ssh-keygen -q -t ed25519 -N '' \
  -C mooncen-an2p-container-deploy-20260819 \
  -f "$key_stage/deploy-id_ed25519"
sudo ssh-keygen -q -t ed25519 -N '' \
  -C mooncen-an2p-container-status-20260819 \
  -f "$key_stage/status-id_ed25519"
sudo ssh-keygen -q -t ed25519 -N '' \
  -C mooncen-an2p-ops-db-20260819 \
  -f "$key_stage/db-id_ed25519"
sudo chmod 0600 "$key_stage"/{deploy,status,db}-id_ed25519
```

각 private file은 active pair의
`/usr/bin/python3.12 -I .../receive_control_bootstrap.py --pair "$pair" --name
<role>-id_ed25519` stdin에 direct pipe하고 receiver canonical success를 확인한 직후 source
private file을 제거한다. 이 exact command는 `sudo -i` an2p root shell로 실행하는 an2p README에
있다. `.pub` 세 개의 fingerprint와
exact comment를 확인해 cloud root endpoint staging에 넣은 뒤 Gate 4 성공 시 one-use stage를
다음 exact file set으로 제거한다.

```bash
sudo /usr/bin/rm -- "$key_stage"/{deploy,status,db}-id_ed25519.pub
sudo /usr/bin/rmdir -- "$key_stage"
```

세 public key와 폐기할 기존 shared key의 실제 key blob을 interactive operator로만 cloud
root staging에 전달한다. comment만 같거나 다른 이름으로 복사된 동일 blob도 모두
제거해야 하므로 legacy input은 exact comment
`mooncen-an2p-deploy-20260819`의 유효한 public key여야 한다.

reviewed native tree의 네 control file과 네 public key를 root-only staging에 둔 뒤 cloud
interactive root가 provisioner를 정확히 8개 인자로 실행한다. native tree의 script mode를
실행 권한으로 신뢰하지 않는다. 네 control file은 reviewed snapshot에서 받은 out-of-band
SHA-256과 root staging copy를 먼저 대조하고, 검증한 copy만 `/bin/bash`로 실행한다. 이 명령은 container
state/transaction/native-intent/worker-lease가 모두 없는 최초 native bootstrap 창에서만
허용된다.

이 provisioner는 일반 key-rotation 명령이 아니다. 첫 container job 뒤에는 inactive
worker lease도 최대 fenced epoch를 보존하므로 파일을 지우거나 이 절차를 재실행하지
않는다. 향후 key 교체는 retained epoch와 controller source receipt를 보존하는 별도
reviewed rotation 도구가 제공되기 전까지 HOLD다.

```bash
sudo install -d -o root -g root -m 0700 /root/mooncen-container-endpoint
sudo install -o root -g root -m 0600 \
  /opt/mooncen/deploy/an2p/cloud/provision_cloud_deploy_endpoint.sh \
  /opt/mooncen/deploy/an2p/cloud/mooncen-an2p-deploy-sshd_config \
  /opt/mooncen/deploy/an2p/cloud/mooncen-an2p-deploy-sshd.service \
  /opt/mooncen/deploy/an2p/cloud/mooncen_container_ssh_dispatch.py \
  /opt/mooncen/deploy/an2p/cloud/mooncen_container_ingress.py \
  /root/mooncen-container-endpoint/
# 별도 검토 채널에서 받은 5개 SHA-256을 넣은 root:root:0600 파일이다.
sudo sha256sum --check --strict \
  /root/mooncen-container-endpoint/reviewed-control.sha256
sudo /bin/bash \
  /root/mooncen-container-endpoint/provision_cloud_deploy_endpoint.sh \
  /root/mooncen-container-endpoint/mooncen-an2p-deploy-sshd_config \
  /root/mooncen-container-endpoint/mooncen-an2p-deploy-sshd.service \
  /root/mooncen-container-endpoint/mooncen_container_ssh_dispatch.py \
  /root/mooncen-container-endpoint/mooncen_container_ingress.py \
  /root/mooncen-container-endpoint/deploy-id_ed25519.pub \
  /root/mooncen-container-endpoint/status-id_ed25519.pub \
  /root/mooncen-container-endpoint/db-id_ed25519.pub \
  /root/mooncen-container-endpoint/legacy-shared-id_ed25519.pub
```

provisioner는 `mooncen_container_deploy`, `mooncen_container_status`,
`mooncen_ops_db_tunnel` 전용 계정과 root-owned authorized-key 파일을 만들고,
`container-bootstrap.json`의 ingress UID/GID를 deploy 계정으로 다시 결박한다. deploy key는
fixed ingress/controller mutation만, status key는 `status`/`target-identity`/presence만,
DB key는 `127.0.0.1:5432` local forwarding만 허용한다. 어떤 service key에도 bootstrap,
native PowerShell, shell, PTY, agent/X11 forwarding 또는 SFTP 권한이 없다.

설치 직후 cloud의 config 해석과 listener만 root로 확인한다.

```bash
sudo sshd -t -f /etc/ssh/mooncen-an2p-deploy-sshd_config
for remote_user in mooncen_container_deploy mooncen_container_status mooncen_ops_db_tunnel; do
  sudo sshd -T -f /etc/ssh/mooncen-an2p-deploy-sshd_config \
    -C "user=$remote_user,host=mooncen,addr=100.64.198.9"
done
sudo systemctl is-active --quiet mooncen-an2p-deploy-sshd.service
sudo ss -H -ltn 'sport = :2222' | grep -F '100.75.187.63:2222'
```

Gate 7 finalizer는 receiver/prepare가 끝난 뒤 설치된 exact triplet으로 positive/negative probe를
실행한다. `cloud-container-status`와 `cloud-container-deploy`의 canonical
`target-identity`가 pending identity와 같고 두 endpoint의 exact-key canonical `status` bytes가
같아야 한다. status mutation, deploy arbitrary shell, DB key command는 실패하고 DB key의
`127.0.0.1:5432` tunnel만 성공해야 한다. `/etc/mooncen-an2p/*-transport`는 finalize가
staging한 뒤에만 존재하므로 그 경로를 Gate 3에서 미리 probe하지 않는다.
endpoint 자체의 negative matrix에는 deploy/status key의 `ssh -tt` 요청과 `sftp` 요청 거부,
DB key로 `-o ExitOnForwardFailure=yes`를 사용한 `127.0.0.1:22` local-forward 거부가 포함된다.
오직 같은 option으로 여는 `127.0.0.1:5432` registration tunnel만 허용한다. 이 결과를
positive target-identity/status proof와 함께 확인하지 못하면 finalize를 계속하지 않는다.

마지막으로 `/home/ubuntu/.ssh/authorized_keys`의 모든 token에서 legacy blob이 없고, 전용
세 key fingerprint가 서로 다르며, old shared private key가 API/worker/DB tunnel 환경과
service account에서 읽히지 않음을 확인한다. 이 검증이 끝나기 전에는 service worker를
시작하지 않는다. 향후 container/native host maintenance와 최초 controller bootstrap은
human interactive Tailscale 경로이며 forced deploy key로 실행하지 않는다.

### Gate 3 reference: guarded native DB·HBA·bootstrap 준비

먼저 승격할 bundle과 같은 reviewed source를 기존 backup/guard가 적용되는 native
배포 경로로 cloud에 배포한다. 이 gate는 아직 container runtime을 선택하지 않지만,
production DB/HBA를 변경하는 guarded native 단계다. `setup_project.sh`는 다음을 수행한다.

- fresh backup/restore 증적 아래 migration, permission role, 전용 deployment-worker LOGIN을
  순서대로 수렴하고 같은 shared DB boundary query를 통과시킨다.
- PostgreSQL HBA를 atomic 교체·reload한 뒤 전용 worker의 TLS/SCRAM 연결, target DB 허용,
  모든 다른 `datallowconn` DB 거부를 실제 probe한다.
- DB/HBA 검증이 모두 끝난 뒤에만 root-only control source와 exact no-argument exporter를
  게시한다. skip·실패 경로에서는 기존 exporter를 먼저 제거하고 다시 게시하지 않는다.

- 기존 native `api.env`/`ai.env`는 `root:<service> 0640`으로 그대로 둔다.
- container 전용 API/AI allowlist env와 migrator env는 `root:root 0600`으로 atomic 설치한다.
- public 다섯 필드만 renderer에 전달해
  `container-frontend-runtime-config.js`를 `root:root 0644`로 설치한다.
- root-owned bootstrap helper와 당시 source의 canonical build-policy digest를 설치한다.
- 고정 sudo 명령을 설치하되 container installer, target identity, service는 실행하거나
  선택하지 않는다. exporter는 이후 Gate 6의 직접 pipe에서만 호출한다.

container Compose는 Unix socket DB와 `DB_SSLMODE=disable`을 강제하며 native env에 남은
`DB_SSLROOTCERT`/`DB_SSLCERT`/`DB_SSLKEY`를 빈 값으로 override한다. 따라서 host의
`/etc/mooncen/db-root-ca.crt` 경로가 container 안으로 새지 않는다. migrator env에는
`DB_NAME`, `DB_USER`, `DB_PASSWORD`만 둔다. 파일 내용이나 secret은 로그/receipt에
출력하지 않는다. public API allowlist에는 API login/name/pool, auth/OAuth, admin IDs,
bug SMTP, site/CORS/trusted-host 필드만 포함한다. `DB_OWNER_USER`, Ops login/hash,
Cloudflare analytics/monitor token과 native DB host/port/TLS path는 포함하지 않는다.

native 배포가 실패하면 `mooncen_release_guard.sh`가 새 container env/runtime config,
PostgreSQL HBA(복원 뒤 reload/parse 확인), bootstrap config/helper, identity, installation
receipt와 controller tree를 exact mutable artifact allowlist로 복구한다.

### Gate 5 reference: human Tailscale 경로에서 target identity를 한 번 bootstrap

target identity는 임의 setup 변수로 자동 선택하지 않는다. an2p가 계산한 정확한
64-lowercase-hex 한 줄을 고정 helper의 stdin으로 전달한다. helper는 argv를 받지
않으며 추가 줄/문자, symlink, source-policy drift를 거부한다.

이 단계는 forced `cloud-container-deploy` key의 허용 명령이 아니다. 승인된 운영자가
Tailscale/cloud console의 interactive root session에서만 아래 root helper를 실행한다.
장기 실행 API/worker/tunnel 계정에는 이 session credential이나 bootstrap 권한을 주지
않는다.

an2p root shell에서 먼저 별도 non-secret pre-auth를 끝내고, identity 한 줄만 remote helper의
stdin으로 보낸다. 이 값은 phase-1 pending receipt에서 읽어 검증한 공개 policy digest이며
secret shell 변수나 exporter stream과 섞지 않는다.

```bash
/usr/bin/tailscale ssh ubuntu@cloud.dinosaur-piano.ts.net /usr/bin/true
pair="runtime-pair.<commit40>.<tree40>.<policy64>"
target_identity=$(/usr/bin/python3.12 -I - \
  /var/lib/mooncen-an2p-runtime/pending-control-finalization.json "$pair" <<'PY'
import json
import pathlib
import re
import stat
import sys

path = pathlib.Path(sys.argv[1])
pair = sys.argv[2]
metadata = path.lstat()
payload = path.read_bytes()
value = json.loads(payload.decode("ascii"))
required = {
    "environment", "environment_sha256", "pair", "receipt_digest",
    "release_digest", "schema_version", "source_tree", "target",
    "target_identity",
}
canonical = json.dumps(
    value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")
).encode("ascii") + b"\n"
match = re.fullmatch(
    r"runtime-pair\.[0-9a-f]{40}\.([0-9a-f]{40})\.[0-9a-f]{64}", pair
)
if (
    path.is_symlink()
    or not stat.S_ISREG(metadata.st_mode)
    or metadata.st_nlink != 1
    or (metadata.st_uid, metadata.st_gid, stat.S_IMODE(metadata.st_mode)) != (0, 0, 0o600)
    or set(value) != required
    or payload != canonical
    or match is None
    or value.get("schema_version") != 1
    or value.get("pair") != pair
    or value.get("source_tree") != match.group(1)
    or value.get("environment") != "development"
    or value.get("target") != "an2p-dev"
    or re.fullmatch(r"[0-9a-f]{64}", str(value.get("target_identity", ""))) is None
):
    raise SystemExit("pending target identity is not exact")
print(value["target_identity"])
PY
)
printf '%s\n' "$target_identity" \
| /usr/bin/tailscale ssh ubuntu@cloud.dinosaur-piano.ts.net \
    /usr/bin/sudo -n -- /usr/local/libexec/mooncen-container-bootstrap
```

bootstrap은 root-only private snapshot에서 pinned build policy를 다시 계산한 뒤
installer를 실행한다. installer는 `control.lock`을 exclusive로 잡고 transaction과
native intent뿐 아니라 `active.json`도 모두 없는 native-only 상태인지 확인한 후
controller/library, HBA·native mutual-exclusion helper, 세 native unit, guard/stack unit,
identity와 이 문서를 설치한다. exact installed byte digests는
`container-runtime-installation.json`에 마지막으로 commit한다. 이후 lock을
풀고 `status`만 호출한다. service start/enable, Docker build/pull, image load는 하지
않는다.

source release가 나중에 `/opt/mooncen`에서 교체되어도 live controller는 root-owned
installed library를 사용하므로 자동으로 바뀌지 않는다. controller upgrade는 Docker가
active인 채 수행할 수 없다. 반드시 CAS `rollback-native` → native guard terminal과
`state=null` 확인 → 새 reviewed native release 배포/setup → 동일 identity로 bootstrap
재실행 → 새 installed-byte receipt/status 확인 → 그 policy와 정확히 같은 release 승격
순서로 한다. installer exclusive lock과 모든 controller 명령의 shared lock이
worker/guard 중간 교체를 막는다.

### 3. fixed ingress에서 root release로 stage

bootstrap이 만든 `/var/lib/mooncen-container-ingress` 아래에 an2p가 네 파일을
`<source_tree>` 디렉터리로 atomic 전송한다. 브라우저/API/SSH 명령은 local path를
입력받지 않고 40-lowercase-hex tree만 전달한다. ingress 디렉터리는 deploy 계정
0700, 파일은 regular non-symlink이고 group/other writable이면 안 된다.

container worker는 먼저 DB claim을 `job UUID -> 32 lowercase hex`, 전역 단조 증가 epoch를
20자리 zero-padded decimal, 무작위 UUID token을 32 lowercase hex로 고정한다. raw token은
로그/API/status에 노출하지 않는다. DB row lock을 유지한 채 다음 원격 명령이 exclusive
`control.lock`을 획득해 이전 shared mutation이 끝날 때까지 기다리고, 그 뒤 durable
`worker-lease.json`을 새 epoch로 교체한다.

```text
/usr/bin/sudo -n -- /usr/local/libexec/mooncen-container-release \
  lease-bind JOB32 EPOCH20 TOKEN32
```

정상 `lease-bind` result와 `status.worker_lease`는 exact
`schema_version,job_id,claim_epoch,claim_token_sha256,active,expires_epoch`만 가진다.
status에는 token 자체가 아니라 SHA-256만 보인다. bind 직후 worker가 DB claim과 live
controller CAS를 다시 확인한 뒤에만 ingress를 준비한다. 이 tuple은 dedicated worker가
DB에서 파생하며 운영자나 브라우저가 입력하지 않는다.

`stage`는 fixed directory fd와 `O_NOFOLLOW`로 exact 네 파일만 복사하고, new-only
root 0700 destination에서 manifest/bundle/Compose/canonical receipt, unexpired PASS,
`target=an2p-dev`, target identity, installed build-policy receipt를 재검증한 뒤 atomic
rename한다. Docker나 application runtime은 변경하지 않는다.

`stage`의 controller protocol은 다음과 같다. forced dispatcher와 sudoers는 이 exact
인자 수/형식만 허용한다.

```text
stage TREE40 JOB32 EPOCH20 TOKEN32
```

## preflight

image-store mutation과 read-only production gate를 명확히 분리한다. worker가 호출하는
고정 protocol은 다음 순서다.

```text
load-images TREE40 JOB32 EPOCH20 TOKEN32
preflight     TREE40 JOB32 EPOCH20 TOKEN32
```

`load-images`만 local Docker image store를 변경하며 application container/transaction을
만들지 않는다. `preflight`는 image를 다시 load하지 않고 exact existing image ID,
unexpired receipt/identity, container env, installed byte receipt, production DB ledger와
현재 host nginx loopback health를 확인한다. transaction/guard/supervisor/candidate를
만들거나 native unit을 변경하지 않는다. `promote`는 race 방지를 위해 동일 gate와
expiry를 다시 검사한다.

각 성공 stdout은 canonical single-line JSON이다. 실패는 exit 1, stdout 없음, redacted
stderr이며 성공 증적으로 파싱하지 않는다. 승인 전 별도로 fresh backup/restore evidence,
deploy SSH, disk, PostgreSQL/nginx/cloudflared 상태와 `status.transaction=null`을 확인한다.

성공 key allowlist는 다음과 같다.

| 명령 | exact top-level keys |
| --- | --- |
| `stage` | `schema_version, staged, source_tree, release_digest, bundle_sha256, compose_sha256, image_ids` |
| `load-images` | `schema_version, images_loaded, source_tree, release_digest, image_ids` |
| `preflight` | `schema_version, preflight, source_tree, release_digest, image_ids, migration_ledger_sha256` |
| `status` | `native_intent, schema_version, state, transaction, worker_lease` |
| `lease-bind`, `lease-release` | `schema_version, job_id, claim_epoch, claim_token_sha256, active, expires_epoch` |
| `target-identity` | `schema_version, target, target_identity` |

`schema_version=1`, `staged/images_loaded=true`, `preflight="passed"`를 exact 확인한다.
promote와 Docker-to-Docker `rollback`은 active state JSON을 반환한다.
`rollback-native`는 stdout JSON `null`을 반환하며, 이어지는 `status`가
`state=null, transaction=null`인지 확인해야 한다.

### migration은 read-only ledger gate

production Compose의 `migrate` profile은 다음 명령만 실행한다.

```text
python DB/setup_db.py --mode plan --json --require-current
```

controller는 pending list가 비어 있고 expected/applied count와 두 ledger SHA-256이
manifest의 `migration_ledger_sha256`과 정확히 같을 때만 진행한다. SQL migration을
적용하거나 schema를 변경하지 않는다. pending migration이 있으면 transaction과
candidate를 만들기 전에 실패한다. Docker가 active인 상태에서는 native intent gate가
native deploy/migration을 의도적으로 차단한다. 다음 순서를 지킨다.

```text
CAS rollback-native
  -> native backup-guarded deploy/migrate
  -> guard terminal + native intent 해제 + state=null 확인
  -> container controller installer/bootstrap
  -> 동일 reviewed image/bundle의 fresh validation receipt 재검증
  -> stage/load-images/preflight
  -> CAS promote
```

즉 Docker active 상태에서 pending migration을 곧바로 native deploy로 덮지 않는다.
이 gate를 우회하려고 migration container 명령, HBA, env 또는 state JSON을 수동 변경하지
않는다.

## promote

변경 창과 독립 승인을 확인한 Ops API가 status-only endpoint의 atomic state에서 CAS를
계산해 append-only approval과 job에 결박한다. dedicated worker만 다음 exact protocol을
forced deploy endpoint로 호출한다.

```text
promote TREE40 GENERATION10 EXPECTED_ACTIVE64 EXPECTED_PREVIOUS64 \
  EXPECTED_STATE_SHA25664 JOB32 EPOCH20 TOKEN32
```

네 CAS 값은 승인 직전 atomic `status`의 compact `state`에서 계산한다.
generation은 10자리 zero-padded decimal이고, active/previous가 null이면 정확히 64개의
`0`을 쓴다. state hash는 LF 없는 compact sort-key JSON의 SHA-256이다. Ops worker가 이
tuple, approval, DB claim을 다시 읽어 exact 일치시킨다. 브라우저는 path/argv/claim token을
제공하지 않으며 사람의 수동 controller mutation도 지원하지 않는다.

controller의 고정 순서는 다음과 같다.

1. root, private layout, local Docker, root-owned host input을 확인한다.
2. 네 파일의 정확한 inventory, manifest/receipt canonical digest와 expiry, exact
   `an2p-dev` identity, bundle/Compose/image ID를 확인하고 images를 load한다.
3. 위 read-only production migration ledger gate를 실행한다.
4. native unit 상태 또는 기존 Docker state를 snapshot하고 stack supervisor를 arm한다.
   token-bound guard를 먼저 running 상태로 만들고, guard가 기다리는 exact token으로
   15분 durable transaction을 publish한다.
5. `mooncen-production-candidate`를 `127.0.0.1:18001`(API),
   `127.0.0.1:15173`(frontend)에 `up --detach --no-build`로 시작한다. AI는 candidate에
   시작하지 않는다.
6. candidate의 API `/health`, frontend `/_frontend_health`와 `/health`가 200인지,
   API root/profile, course read, OAuth config, `/api/auth/me` 401을 확인한다.
7. direct API와 frontend proxy 양쪽에서 `/api/ops`,
   `/api/ops/runtime-metrics`, `/api/auth/ops`, `/api/auth/ops/login`이 모두 404인지
   확인하고, container의 실제 image ID/running/restart count를 manifest와 대조한다.
8. 이전 runtime을 stop한다. 첫 승격이면 세 native app unit을 stop/disable하고,
   이후 승격이면 기존 active Compose app을 stop한다.
9. `mooncen-production`을 `127.0.0.1:8001`과 `127.0.0.1:5173`에 시작하고 같은
   health/contract/404/image 검증을 반복한다. active AI도 API image ID인지 확인한다.
10. host nginx 경로 `http://127.0.0.1/health`에 `Host: mooncen.kr`을 보내 JSON
    `status=ready`를 확인한다. 이 host-origin check는 health만 검사하며 public host의
    Ops 404를 대신하지 않는다.
11. `active.json`의 generation/current/previous/native fallback을 atomic commit하고
    candidate를 `stop`과 `rm`으로 정리한다.

성공 JSON과 다음 상태/health를 보관한다. public host에서도 Ops 경계가 필요한 경우
별도 외부 probe로 404를 확인한다.

```bash
sudo /usr/local/libexec/mooncen-container-release status | python3 -m json.tool
sudo systemctl status mooncen-container-stack.service --no-pager
curl --noproxy '*' -fsS -H 'Host: mooncen.kr' http://127.0.0.1/health
```

## guard와 rollback

각 transaction은 random 32-hex token, controller PID/start ticks/boot ID와 15분
deadline을 root-only journal에 기록한다. token-bound guard unit은 journal보다 먼저
시작해 exact token publication을 최대 30초 기다리므로 worker가 journal과 guard arm
사이에서 죽는 공백이 없다. journal이 publish되면 guard는 5초마다 owner를
확인한다. owner가 사라지거나 deadline을 넘으면 pidfd로 정확한 owner만 fence한 뒤
candidate를 정리하고 이전 Docker release 또는 저장한 native unit 상태로 복구한다.
commit은 `transaction.json` 제거가 durable point다.

반대 방향인 native deploy는 remote mutation 전에 controller의 `native-begin <32hex>`을
operation lock 아래 기록한다. 그 token은 native preflight, bootstrap, main guard
journal에 이어져 commit/recovery/abort의 durability barrier가 끝난 terminal point에서만
`native-end <same-token>`으로 해제된다. worker 취소나 SSH 단절은 fence를 해제하지
않으며, pre-guard crash는 root lock의 boot-aware stale recovery가 candidate를 보존한
뒤에만 해제한다. controller가 아직 설치되지 않은 최초 bootstrap은 receipt, state,
library와 stack unit이 모두 없는 clean host에서만 예외다. 설치 흔적이 일부라도 있으면
fail-closed다.

세 native unit의 root `ExecCondition`도 atomic status를 확인한다. 일반 시작은
`state=null, transaction=null, native_intent=null`이고 fresh active worker lease도 없을
때만 허용한다. guard-owned native intent authorization 또는 controller rollback이 active
Compose를 정확히 멈춘 뒤 만든 transaction-token `native-restore.json` 구간만 예외다.
따라서 수동 `systemctl start mooncen-api`로 active container와 native API를 동시에
열 수 없다.

rollback은 목적에 따라 두 명령을 구분한다. 둘 다 실행 직전 atomic `status`에서 같은
CAS tuple을 계산하며, mismatch는 다른 작업이 state를 바꿨다는 뜻이므로 새 승인을
받기 전 재시도하지 않는다.

```text
# previous Docker release로만 복귀; previous=null이면 controller가 거부한다.
rollback GENERATION10 EXPECTED_ACTIVE64 EXPECTED_PREVIOUS64 EXPECTED_STATE_SHA25664 \
  JOB32 EPOCH20 TOKEN32

# previous 유무와 무관하게 pinned native maintenance baseline으로 전환한다.
rollback-native GENERATION10 EXPECTED_ACTIVE64 EXPECTED_PREVIOUS64 EXPECTED_STATE_SHA25664 \
  JOB32 EPOCH20 TOKEN32
```

위 protocol도 서로 다른 admin typed confirmation, one-time approval, DB claim으로만
worker가 실행한다. raw claim tuple을 운영자가 만들어 수동 호출하지 않는다. 한 CAS
tuple으로 두 명령을 연속 실행하지 않는다. `rollback`은 `previous` Docker
release가 있을 때만 보존된 local image ID와 root release를 다시 검증하고 candidate
검증 후 되돌린다. rollback/boot convergence는 과거 PASS receipt의
TTL을 다시 요구하지 않아 장애 시 복구를 만료로 막지 않지만, 새 stage/load/preflight/
promote는 항상 현재 expiry를 강제한다.

`rollback-native`는 최초 승격 때 state에 결박한 native baseline identity, deploy/archive,
root-owned prebuild marker, immutable runtime inventory와 세 native unit/condition-helper
digest를 journal publication 뒤 다시 검증한다. active containers를 멈춘 뒤에만 exact
transaction token의 native restore authorization을 만들고 저장된 enabled/active 상태를
복구한다. `systemctl`의 accepted idempotency return code만 신뢰하지 않고 각 unit의
`UnitFileState`/`ActiveState`가 snapshot과 exact 일치하며 saved-active는 `MainPID>0`,
saved-inactive는 `MainPID=0`임을 확인한다. 반대로 첫 Docker cutover는 세 unit 모두
disabled/inactive/`MainPID=0`임을 확인하기 전 Compose를 시작하지 않는다. host nginx
origin health가 통과하면 stack을 disable하고 `active.json`과
transaction을 durable하게 제거한다. 검증·native health가 실패하거나 controller가
죽으면 guard가 native units를 다시 stop/disable한 뒤 prior Docker release를 exact image로
복구한다. 따라서 active/previous release directory와 images.tar, pinned native source 및
control bytes를 임의 변경하거나 제거하지 않는다.

DB heartbeat가 unavailable, token/epoch/agent가 달라짐, lease 만료 중 하나라도 감지되면
worker는 다음 ingress/controller 명령을 시작하지 않고 실행 중인 local SSH를 중단한다.
local SSH 종료만으로 cloud 명령 종료를 주장하지 않는다. 정상 worker는 exact
`lease-release JOB32 EPOCH20 TOKEN32`의 exclusive lock이 반환된 뒤에만 pre-action 상태를
terminal recovery로 기록한다. stale reclaimer도 DB row lock 아래 전역 sequence의 더 큰
epoch를 할당하고 `lease-bind`로 이전 shared mutation 종료를 기다린 다음 live status를
재조회한 후에만 DB owner를 바꾼다. 원격 fence가 unavailable이면 이전 DB owner와 job
상태를 그대로 두며 `recovered_previous`를 terminal로 확정하지 않는다. controller는
inactive lease에서도 최대 epoch를 보존하므로 폐기된 token/epoch는 이후 재사용할 수 없다.

`status.transaction` 또는 `status.native_intent`가 non-null이거나 `status.state`가
non-null이거나 fresh active `status.worker_lease`가 있으면 installer를 실행하지 않는다.
새 promote/rollback도 transaction/intent가 non-null이면 시도하지 않는다.
guard가 수렴하는지 journal을 보고, `phase=rollback_failed`이면 deploy SSH를 보존한
채 원인을 진단한다. 수동 `docker compose down`이나 state JSON 편집으로 journal을
속이지 않는다.

```bash
sudo /usr/local/libexec/mooncen-container-release status | python3 -m json.tool
sudo systemctl status 'mooncen-container-release-guard@*' --no-pager
sudo journalctl -u 'mooncen-container-release-guard@*' \
  -u mooncen-container-stack.service -n 300 --no-pager
```

boot 때 `mooncen-container-stack.service`는 transaction이 없고 recorded state가 있을
때만 `ensure-active`로 exact active release를 수렴시킨다. 이때 세 native app unit 중
하나라도 active/enabled이면 drift로 보고 시작을 거부한다.

## production에서 절대 금지

운영 host와 `mooncen-production`/`mooncen-production-candidate` project에는 다음을
직접 실행하지 않는다.

- `docker compose down`, 특히 `down -v` 또는 `down --volumes`
- `docker compose build`, `up --build`, image rebuild
- `docker compose pull`, `docker pull`, mutable tag 교체
- `docker system prune`, `docker image prune`, release image 삭제
- active/previous release directory, `images.tar`, state/journal 삭제 또는 수정
- controller 밖에서 active container를 `rm`하거나 project/port/Compose file을 변경
- 전환 문제를 해결하려고 PostgreSQL/nginx/cloudflared/backup/deploy SSH를 함께
  stop/restart

controller 자체도 Compose `build`, `pull`, `push`, `prune`, `down`, `-v`,
`--volumes`를 allowlist에서 거부한다. 정상 candidate 정리는 정확한 service의
`stop`과 `rm`만 사용한다.

## Ops Console의 역할

Ops Console은 다음 증적을 서로 exact binding하고, 실행 worker가 활성화된 환경에서만
고정 `cloud-container-deploy` forced SSH target과 위 controller argv를 호출한다.

- canonical release, source tree, API/frontend image ID와 bundle SHA-256
- `an2p-dev` target identity, PASS/FAIL, receipt digest와 expiry
- reviewed production target name/environment/identity
- promotion typed confirmation과 존재하는 short-lived approval evidence
- recorded current/previous release와 deployment timeline
- DB `agent_id, lease_token, lease_epoch, leased_until`과 원격 worker-lease digest

readiness의 `promotion_evidence_ready=true`와 `remote_claim_fencing_ready=true`가 모두
필요하지만 그것만으로 실행하지 않는다. worker는 단계마다
exit code와 exact JSON key/release tuple을 확인하고, promote/rollback 뒤 다시 `status`를
조회해 `transaction=null`과 exact active/previous가 수렴했을 때만 terminal success로
기록한다. SSH 취소나 worker crash는 성공으로 간주하지 않고 guard/status reconciliation을
계속한다. stderr는 redacted log일 뿐 성공 evidence가 아니다. API/UI에는 raw claim token을
반환하지 않고 controller status의 SHA-256과 epoch capability만 표시한다.

## 실제 cutover 완료 조건과 코드 이슈

다음 조건을 모두 닫기 전에는 production Docker cutover를 승인하지 않는다.

1. an2p/cloud Docker 설치와 위 local-context/platform/Compose 점검이 통과한다.
2. clean reviewed snapshot에서 한 번 build한 bundle과 fresh exact an2p PASS receipt가
   있고 cloud 네 파일/owner/mode가 검증된다.
3. fresh backup/restore evidence, deploy SSH, PostgreSQL/nginx/cloudflared health와
   read-only migration ledger가 확인된다.
4. `/etc/mooncen`의 세 container private env, public runtime config, target identity,
   bootstrap/install receipt가 준비된다.
5. active/previous bundle 보존, 변경 창, rollback 담당자와 public health/Ops-404
   probe가 준비된다.
6. 새 native release와 container bootstrap이 같은 build-policy digest인지, controller
   installation receipt의 모든 installed byte가 그대로인지 확인한다.
7. `stage`, `load-images`, `preflight`의 exact success JSON과 최종 promote approval을
   보존하고, `mooncen-container-stack.service`의 150초 stop timeout 및 guard가 실제
   host systemd에서 로드됐는지 확인한다.

남은 환경 의존 조건은 이 저장소가 Docker 패키지를 설치하지 않고, 실제 원격
bootstrap/cutover도 수행하지 않았다는 점이다. 설치 전 Docker 부재, 잘못된 SSH target,
stale approval/receipt, pending migration, fresh backup 부재 중 하나라도 있으면 HOLD다.
