# Ops Console Docker 배포 운영 계약

## 결론과 지원 범위

운영 배포의 기본 경로는 **an2p Ops Console → 전용 배포 worker → cloud의 forced-command
endpoint → root controller**다. 브라우저는 경로, image tag, Compose 파일, SSH 옵션이나
실행 명령을 보내지 않는다. DB의 불변 증적과 live controller CAS에서 만들어진 값만
사용한다.

| 작업 | Ops Console | 실행 주체 |
| --- | --- | --- |
| Build | 조회만, 실행 차단 | an2p의 검토된 clean snapshot 도구 |
| an2p Validate | 조회만, 실행 차단 | 격리된 an2p Docker smoke |
| Promote | fresh exact PASS일 때 지원 | `mooncen_deployment_worker` |
| container→container rollback | previous가 있을 때 지원 | `mooncen_deployment_worker` |
| Docker→native maintenance | pinned native baseline이 있을 때 지원 | `mooncen_deployment_worker` |
| 기존 native 배포 queue | 항상 실행 불가 | an2p 운영자의 대화형 Tailscale 경로만 사용 |

기존 `/api/ops/deployments`는 조회 호환을 위해 남지만 UI에는
`네이티브 배포(레거시)`로 표시하고 `execution_supported=false`로 반환한다. 장기 실행
서비스 계정에는 과거 `ubuntu` full-shell key나 PowerShell 실행 권한이 없다.

> **배포 보류(2026-08-20):** 사고 수정본을 새 review snapshot으로 독립 검토하기 전까지
> an2p root bootstrap, phase 1, 운영 Promote를 실행하지 않는다. 이전에 승격한
> `refs/mooncen/docker-release-snapshots/223fef9f6786da960faf9951324650ad`는
> 무효이며 로컬에서 resolve되더라도 재사용하지 않는다.

```text
clean commit/tree
   │
   ├─ build ──> release.json + images.tar + compose.production.yaml
   │
   └─ an2p smoke ──> validation.json (fresh passed, target_identity 고정)
                         │
root immutable handoff ──┴─> /var/lib/mooncen-deployment-worker/releases/<tree>
                         │
dedicated registrar ─────┴─> append-only release/receipt DB evidence
                         │
Ops admin typed approval ──> approval + job + deployment (한 transaction)
                         │
DB token/global epoch claim ─> remote exclusive lease-bind
                         │
worker fixed stdin/argv ───> forced endpoint ──> cloud controller
                         │
atomic status reconciliation ──> DB terminal ─> lease-release ─> timeline
```

Ops 화면 자체도 이 신뢰 경계 안에 있다. `sgm` worktree의 Vite proxy를 운영 로그인 화면으로
쓰지 않는다. 검토된 source tree에서 pinned Node 컨테이너로 만든 정적 bundle을 같은 Ops API가
직접 제공한다.

## 계정·키·파일 경계

an2p의 네 실행 경계를 섞지 않는다.

| 경계 | OS 계정 | 보유 권한 |
| --- | --- | --- |
| Ops API | `mooncen_ops_api` | API DB credential, status-only SSH profile |
| 배포 worker | `mooncen_deployment_worker` | queue DB credential, deploy-only SSH profile, release read |
| 운영 DB tunnel | `mooncen_ops_db_tunnel` | `127.0.0.1:5432` permitopen 전용 key |
| 개발 Docker | `mooncen_docker_operator` | constrained system unit, 유일한 `docker` group 계정 |

`sgm`, Ops API, worker, tunnel은 `docker`와 `lxd` group에 속하면 안 된다. 기존 group을
제거한 뒤에는 root bootstrap이 옛 group GID를 실제로 보유한 `sgm` PID만 제한적으로
drain한다. exact process를 pidfd로 고정한 뒤 catch·block·ignore할 수 없는 `SIGSTOP`으로 먼저
동결하고 같은 pidfd에 `SIGKILL`을 보낸다. bounded exit 대기 뒤 old-GID process를 다시 검색하고
group membership도 다시 확인한다. 전체 user session을 종료하지 않으며 새로 접속한 clean SSH는
옛 GID가 없으므로 유지된다. Ops API가 worker env, deploy key, release bundle 또는 worker unit을
읽거나 제어할 수 없어야 한다.

고정 경로는 다음과 같다.

- API status transport:
  `/etc/mooncen-an2p/status-transport/{ssh_config,id_ed25519,known_hosts}`
- worker deploy transport:
  `/etc/mooncen-an2p/deploy-transport/{ssh_config,id_ed25519,known_hosts}`
- DB tunnel transport: `/etc/mooncen-an2p/db-tunnel/...`
- worker mutable state:
  `/var/lib/mooncen-deployment-worker/state/{heartbeat.json,spool,pending-final,runtime}`
- worker immutable release root:
  `/var/lib/mooncen-deployment-worker/releases/<40hex-source-tree>`

transport 파일은 `root:<각 서비스 group> 0640`, parent는 `root:<group> 0750`이다.
mutable state만 `mooncen_deployment_worker:mooncen_deployment_worker 0700`이다. release root와
각 tree directory는 `root:mooncen_deployment_worker 0750`, 정확한 네 파일은
`root:mooncen_deployment_worker 0640`이다. worker가 등록·전송 전에 bundle을 바꾸지
못하게 하는 경계다.

control과 Docker snapshot은 한 쌍으로
`/opt/mooncen-an2p-runtime/current -> releases/runtime-pair.<commit>.<tree>.<policy>`에서
선택한다. `/opt/mooncen-an2p-control/current`와 `/opt/mooncen-an2p-docker/current`는 각각
그 pair의 `control`, `docker`를 가리키는 root-owned 불변 alias다. pair 교체 중에는 root
operation lock이 API 재기동과 selector를 직렬화한다.

cloud도 키를 분리한다.

- `cloud-container-status`: `status`/presence 같은 read-only dispatcher만 허용
- `cloud-container-deploy`: ingress와 허용된 controller action만 허용, forwarding 금지
- `cloud-ops-db`: DB port forwarding만 허용, shell 금지

클라이언트의 fixed argv만 믿지 않는다. `authorized_keys command=`와 별도 sshd 정책의
server-side dispatcher가 요청을 exact parse한다. 세 profile의 private key digest는 서로
달라야 한다.

## 불변 DB 증적

마이그레이션은
`DB/migrations/20260819_001_ops_container_deployment_pipeline.sql`이다.

- `ops_container_releases`, `ops_container_validation_receipts`,
  `ops_container_approval_evidence`는 UPDATE/DELETE가 거부된다.
- `manifest_json`, `receipt_json`, image/check object는 unknown/missing key를 거부하고
  모든 scalar column과 exact 일치해야 한다.
- validation의 여섯 check는 boolean exact key set이다. `status=passed`인데 하나라도
  false이거나 빠진 direct INSERT도 DB가 거부한다.
- Promote는 `target=an2p-dev`, exact target identity, 미만료 `passed` receipt를 요구한다.
- API approval TTL은 10분이고 DB 상한은 15분이다. `approval_evidence_id UNIQUE`로 한 번만
  소비한다.
- approval, deployment, job JSON은 action, target name/environment/identity,
  release/receipt, current/previous, generation, full-state SHA-256과 exact 일치해야 한다.
- container job의 assigned/running 상태는 exact `agent_id`, 무작위 `lease_token`, 전역 단조
  증가 `lease_epoch`, fresh `leased_until`을 요구한다. terminal row에는 raw token과
  `leased_until`이 남지 않는다.
- `ops_deployments.environment`는 control-plane partition이고 `target_environment`는 실제
  대상 환경이다. staging approval을 production job에 사용할 수 없다.

DB 역할은 `mooncen_api`가 조회와 approval INSERT, dedicated
`mooncen_deployment_worker`가 release/receipt SELECT·INSERT와 bounded queue 상태 변경만
갖는다. `mooncen_crawler`와 `PUBLIC`은 Docker 증적 INSERT 권한이 없다. 실제 LOGIN은
`mooncen_deployment_worker_login`이며 crawler/API login이나 비밀번호를 재사용하지 않는다.

## Build·Validate에서 등록까지

현재 worktree, `$HOME` release directory 또는 user service에서 실행한
`docker compose build` 결과는 운영 후보가 아니다. root가 검토·설치한 entrypoint만 fixed
repository의 exact release ref를 다시 resolve하고 clean clone에서 build/smoke한다.

최초 root-of-trust는 checkout의 스크립트를 직접 실행해 만들지 않는다. 독립 승인 채널에서
bootstrap 자체 SHA-256과 아래 7개 digest를 받은 뒤 root-owned stage를 만들고 exact digest를
확인한 다음 그 stage만 별도 root system service로 실행한다. recovery를 arm하기 전에
independently authorized installer SHA-256으로 exact installer byte를 검증한다. 검증한 byte는
`/var/lib/mooncen-an2p-runtime/reviewed-install-runtime-snapshot.sh`에 `root:root 0700`으로
fsync한 뒤 parent directory까지 fsync하는 atomic rename으로 게시한다. recovery와 final trust
commit은 이 불변 root stage만 실행하며 mutable `sgm` worktree의 installer를 resolve하거나
실행하지 않는다. stage가 durable해진 뒤에만 exact root-owned
`mooncen-an2p-bootstrap-recovery.service`를 enable/start한다.

그 뒤 bootstrap은 trust byte를 publish하기 전에 old user control unit/credential을 quarantine하고
현재 public development selection을 durably capture한 뒤 `sgm`의 docker/lxd membership과
retained process GID를
제거한다. `loginctl terminate-user`는 호출하지 않는다. membership 제거 뒤에도 옛 GID를
가진 `sgm` process만 매번 재검증해 pidfd로 고정하고 `SIGSTOP`으로 즉시 동결한다. `SIGSTOP`은
catch·block·ignore할 수 없으므로 host-root-capable stale process에 handler 실행 시간을 주지
않는다. 같은 pidfd에 `SIGKILL`을 보낸 뒤 bounded exit 대기와 old-GID rescan을 수행하고,
`sgm`의 docker/lxd membership 부재도 다시 검증한다. clean SSH session은 건드리지 않는다.
그 후 captured native 8001/5174 또는 이미 선택된 reviewed Docker health를 복원·검증한다.
기존 native 복원은 `mooncen-api.service`와
`mooncen-frontend.service`를 직접 다루며 아직 설치되지 않은
`mooncen-development-runtime.target`을 요구하지 않는다. reviewed target은 phase 1이 설치한다.
durable journal은 `prepared` → `membership_revoked` → `privileged_processes_drained` →
`native_restored` → `trust_committed`로만 전진하며 완료한 destructive phase를 반복하지 않는다.
unit은 `Restart=on-abnormal`, 30초 간격, 최대 한 번의 자동 재시작, 15분 start timeout을 사용한다.
따라서 signal/reboot는 exact stage에서 한 번만 자동 재개하고, 명시적인 invariant·health·설치
실패는 재시작하지 않고 fail-stop한다. 운영자가 원인을 확인한 뒤에만 수동 재시도한다.
quarantine, retained GID 부재, public health, installer/trust fsync가 모두 끝난 마지막 commit에서만
스스로 disable된다. reviewed installer stage는 `trust_committed` 성공 뒤에만 제거한다.

```bash
bootstrap_sha256='<reviewed-bootstrap-sha256>'
installer_sha256='<reviewed-installer-sha256>'
integrity_sha256='<reviewed-production-integrity-sha256>'
clean_source_sha256='<reviewed-clean-source-sha256>'
pair_manager_sha256='<reviewed-pair-manager-sha256>'
handoff_sha256='<reviewed-evidence-handoff-sha256>'
registrar_sha256='<reviewed-registrar-sha256>'
host_transition_sha256='<reviewed-host-transition-sha256>'
build_policy_sha256='<reviewed-build-policy-sha256>'
bootstrap_stage=/root/mooncen-an2p-runtime-bootstrap.sh

sudo /usr/bin/install -o root -g root -m 0700 \
  deploy/an2p/bootstrap_runtime_installer.sh "$bootstrap_stage"
printf '%s  %s\n' "$bootstrap_sha256" "$bootstrap_stage" | \
  sudo /usr/bin/sha256sum --check --strict -
sudo /usr/bin/systemd-run --wait --pipe --collect \
  --unit=mooncen-an2p-runtime-bootstrap \
  /usr/bin/env -i HOME=/root LANG=C LC_ALL=C \
  PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  /bin/bash "$bootstrap_stage" \
  --installer-sha256 "$installer_sha256" \
  --integrity-sha256 "$integrity_sha256" \
  --clean-source-sha256 "$clean_source_sha256" \
  --pair-manager-sha256 "$pair_manager_sha256" \
  --handoff-sha256 "$handoff_sha256" \
  --registrar-sha256 "$registrar_sha256" \
  --host-transition-sha256 "$host_transition_sha256" \
  --build-policy-sha256 "$build_policy_sha256"
```

bootstrap은 clean 호출 SSH session을 종료하면 안 된다. 같은 session에서 journal의
`trust_committed`, recovery unit의 inactive/disabled 상태, public development health, installed
installer SHA, reviewed installer stage 제거와 mode-0600 trust envelope를 확인한 뒤
`/root/mooncen-an2p-runtime-bootstrap.sh`를 삭제한다. explicit failure이면 stage와 journal을
보존하고 원인을 진단한 뒤 unit을 수동 재시도한다. 실패 중에는 immutable reviewed installer
stage도 삭제하지 않는다. checkout stage를 digest 확인 없이 실행하거나 일반 `sudo`로 installer를
호출하지 않는다.

최초 host에서 Python 3.12 copy-mode venv prerequisite가 없을 때만 다음 no-argument action을
transient root service로 실행한다.

```bash
sudo /usr/bin/systemd-run --wait --pipe --collect \
  --unit=mooncen-an2p-runtime-prerequisites \
  /usr/bin/env -i HOME=/root LANG=C LC_ALL=C \
  PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  /usr/local/sbin/mooncen-an2p-runtime-install bootstrap-prerequisites
```

설치 action의 argv 순서와 field set은 고정이다.

```bash
sudo /usr/bin/systemd-run --wait --pipe --collect \
  --unit=mooncen-an2p-runtime-install \
  /usr/bin/env -i HOME=/root LANG=C LC_ALL=C \
  PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  /usr/local/sbin/mooncen-an2p-runtime-install install \
  --reference "refs/mooncen/docker-release-snapshots/<32hex>" \
  --commit "<reviewed-snapshot-commit-40hex>" \
  --base-commit "<reviewed-parent-commit-40hex>" \
  --source-tree "<reviewed-source-tree-40hex>" \
  --build-policy "<reviewed-build-policy-64hex>"
```

새 pair의 host-layer ABI가 기존 pair와 다르면 일반 `install`은 global helper/unit을 바꾸기 전에
거부한다. 이때만 현재 pair와 그 receipt의 host-layer SHA-256을 기록하고 다음 별도 maintenance
contract를 사용한다. 전환기는 새 pair와 publication journal을 먼저 durable하게 만들고,
pointer 없음 + native health checkpoint를 지난 뒤 TARGET host bytes와 Docker pair로 전진한다.
전환 도중에는 root-only recovery journal을 유지하며 서로 다른 ABI로 자동 rollback하지 않는다.

```bash
previous_pair='runtime-pair.<previous-commit40>.<previous-tree40>.<previous-policy64>'
previous_host_layer='<previous-host-layer-sha256>'
sudo /usr/bin/systemd-run --wait --pipe --collect \
  --unit=mooncen-an2p-runtime-host-transition \
  /usr/bin/env -i HOME=/root LANG=C LC_ALL=C \
  PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  /usr/local/sbin/mooncen-an2p-runtime-install install-host-transition \
  --reference "refs/mooncen/docker-release-snapshots/<32hex>" \
  --commit "<reviewed-snapshot-commit-40hex>" \
  --base-commit "<reviewed-parent-commit-40hex>" \
  --source-tree "<reviewed-source-tree-40hex>" \
  --build-policy "<reviewed-build-policy-64hex>" \
  --from-pair "$previous_pair" \
  --from-host-layer "$previous_host_layer"
```

entrypoint와 `/etc/mooncen-an2p/runtime-installer.trust`는 root-owned이며 installer는 자신의
SHA-256, reference/commit/parent/tree와 build-policy digest를 검증한다. 그 다음 API/frontend와
Ops static을 pinned build로 한 번만 만들고 an2p smoke PASS를 생성한 뒤 phase 1만 완료한다.

1. root-owned `runtime-pair.<commit>.<tree>.<policy>`와 exact inventory receipt seal
2. 개발 evidence를 `/opt/mooncen-an2p-docker/evidence/<tree>`에 new-only 설치
3. user/account/socket host boundary를 prepare하되 기존 native API/frontend selection은 유지
4. root runtime manager operation journal 안에서 pair를 원자 activate하고 Docker만 start
5. Docker `8001`/`5174` health 뒤 exact pending-control-finalization receipt를 fsync

phase 1은 production credential/SSH/DB를 읽지 않고 handoff, DB registration, Ops API,
worker, tunnel, status agent를 실행하지 않는다. first-install cutover 실패나 reboot는 reviewed
native selection으로, upgrade 실패는 exact previous Docker/native와 finalized control state로
복원한다. 이미 working Docker를 얻은 뒤 phase 2가 실패해도 그 개발 runtime은 유지한다.
manager와 selector, isolated phase-2 installer는 하나의 root operation lock을 공유한다. manager
child만 inherited descriptor로 selector를 호출하고 human selection은 journal commit까지 대기한다.
finalize/rotation 성공 JSON도 같은 lock 아래 exact Docker status와 8001/5174 health를 마지막으로
증명한 뒤 출력하므로 중간 `native-select`를 성공으로 오인하지 않는다.

권위 있는 순서는 **phase-1 Docker PASS → an2p one-use key generation과 private-key strict
receive(아직 production mutation 없음) → fresh backup과 guarded cloud native setup → public-key
dedicated endpoint provision → pending target identity bootstrap → strict control export/receive와
reviewed config/known-host receive → immutable prepare → trusted finalize**다. phase 2의
finalizer만 exact worker release handoff와 dedicated DB registration을 수행하고, registration
전용 DB tunnel만 registration 전에 stage/start한다. registration 성공 뒤 isolated
API/worker/status를 시작하고 tunnel을 포함한 durable control plane을 수렴한다. exact command는
[`deploy/an2p/README.md`](../deploy/an2p/README.md)에 있다.

```bash
sudo /usr/bin/systemd-run --wait --pipe --collect \
  --unit=mooncen-an2p-runtime-rollback \
  /usr/bin/env -i HOME=/root LANG=C LC_ALL=C \
  PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  /usr/local/sbin/mooncen-an2p-runtime-install rollback \
  --pair "runtime-pair.<commit40>.<tree40>.<policy64>"
```

phase-2 finalizer가 내부에서 호출하는 고정 registrar는 tree 외의 입력을 받지 않는다.

`/usr/local/libexec/mooncen-register-container-evidence <tree>`는 Docker phase-1 activation 뒤
`/opt/mooncen-an2p-runtime/releases`에서 source tree가
일치하는 root-owned pending pair가 정확히 하나인지 확인하고 그 control runtime을 pin한 뒤
root runtime manager의 fixed `validate <pair-name>`으로 pair receipt와 control/Docker
inventory를 다시 검증한다. 그 다음에만
`runuser --user mooncen_deployment_worker`와 fixed private environment로
`tools.register_container_deployment_evidence`를 실행한다. 사용자 path, DB password,
SSH 옵션을 argv로 받지 않는다. 결과는 다음 exact key의 compact canonical JSON 한 줄이다.

```json
{"expires_at":"...Z","receipt_digest":"<64hex>","receipt_id":"<uuid>","release_digest":"<64hex>","release_id":"<uuid>","schema_version":1,"source_tree":"<40hex>","status":"passed","target":"an2p-dev","target_identity":"<64hex>"}
```

registrar는 an2p hostname, 전용 OS/DB role, crawler non-membership, root-owned bundle,
canonical manifest/receipt, bundle digest, target identity와 expiry를 다시 확인한다.

## 신뢰된 Ops UI와 단일 origin

운영자의 canonical URL은 `http://127.0.0.1:5175/` 하나다. root systemd
`mooncen-ops-api.socket`이 boot부터 `127.0.0.1:5175`를 계속 점유하고 API는 Uvicorn
`--fd 3`으로 socket을 상속한다. 별도 root socket
`mooncen-ops-api-ipv6.socket`은 `[::1]:5175`를 계속 점유한다. 격리 API 계정으로 실행되는
고정 redirect helper만 이 IPv6 socket을 상속하며, 어떤 요청도 credential을 처리하지 않고
`308 Location: http://127.0.0.1:5175/`와 `Cache-Control: no-store`만 반환한다. 따라서 과거
`localhost`/`ops.localhost` bookmark가 IPv6를 먼저 선택해도 비권한 사용자가 같은 포트에
가짜 로그인 화면을 띄울 수 없다. 두 socket은 API/runtime를 재시작하거나 pair를 바꿀 때
중지하지 않으며 non-loopback 주소에는 bind하지 않는다. pair switch operation lock을 보유한
동안 socket activation으로 old API가 다시 기동할 수 없도록 API start gate도 같은 lock을
확인한다.

정적 UI build는 사용자 홈의 Node/npm 또는 기존 `dist`를 사용하지 않는다.

```text
node:24.18.0-bookworm-slim@sha256:6f7b03f7c2c8e2e784dcf9295400527b9b1270fd37b7e9a7285cf83b6951452d
npm ci --ignore-scripts --no-audit --fund=false
VITE_API_BASE_URL=""
VITE_OPS_BASE_PATH="/"
VITE_OPS_CSRF_COOKIE_NAME="mooncen_ops_csrf"
```

`deploy/an2p/ops_console_static.Dockerfile`은 scratch output으로 `dist`만 내보낸다. root
installer가 이를 pending pair의 `control/ops-console-dist`에 dirs `0755`, files `0644`로
설치한 뒤 installer만 생성할 수 있는 동일한 32자리 staging token을
`tools/seal_ops_static.py <runtime-pair-name> --staging-token <token>`에 전달해
`.mooncen-ops-static.json`을 생성한다. receipt는 exact file set/size/SHA-256과 위 build
contract를 canonical JSON으로 결박한다. compiled JS에 올바른 `mooncen_ops_csrf`,
`/api/auth/ops/login`, `/api/ops`가 없거나 과거 `mooncen_csrf`, `127.0.0.1:8001/8002`
주소가 있으면 activation 전에 실패한다.

FastAPI는 startup과 각 read에서 owner/mode/no-symlink/file-set/digest를 다시 검증한다.
`/api` router를 SPA fallback보다 먼저 평가하며 unknown `/api`는 JSON 404다. `index.html`과
인증 응답은 `no-store`, content-hash asset만 immutable cache를 허용하고 CSP,
`X-Frame-Options: DENY`, `nosniff`를 적용한다. 과거 `mooncen-ops-console.service` Vite와
8002 listener는 disabled/masked/absent여야 한다.

## identity와 최초 bootstrap

`validation.json`의 `target_identity`는 an2p smoke가 hostname, platform, target schema와
검증 정책 파일 digest로 계산한 64 lowercase hex다. 같은 값을 root-only bootstrap
envelope와 cloud의 `/etc/mooncen/an2p-dev-target-identity`에 결박한다. 서로 다르면
readiness와 Promote가 중단된다.

최초 cloud bootstrap/향후 native maintenance는 서비스 key가 아니라 신뢰된 운영자의
Tailscale 경로에서만 한다. phase 1과 guarded cloud native setup, dedicated endpoint provision이
이미 성공해야 한다. endpoint provision 전에는 dedicated UID에 결박된
`/etc/mooncen/container-bootstrap.json`이 없으므로 bootstrap을 먼저 실행하면 안 된다.
bootstrap helper가 stdin 한 줄을 읽으므로 `ssh -n`을 쓰지 않는다. 브라우저 재인증은 별도
non-secret 명령에서 끝내며 exporter pipeline과 섞지 않는다.

```bash
/usr/bin/tailscale ssh ubuntu@cloud.dinosaur-piano.ts.net /usr/bin/true
# `$target_identity` must be extracted by the strict canonical pending-receipt
# parser in docker-production.md, never copied from an earlier smoke shell.
printf '%s\n' "$target_identity" \
| /usr/bin/tailscale ssh ubuntu@cloud.dinosaur-piano.ts.net \
    /usr/bin/sudo -n -- /usr/local/libexec/mooncen-container-bootstrap
```

controller가 아직 설치되지 않은 최초 상태에서는 Ops가 read-only blocked로 기동할 수
있다. controller 파일이 존재하지만 identity/status가 invalid하면 fail-closed한다.

identity bootstrap 뒤 an2p root shell은 `set -euo pipefail`을 적용하고 cloud의 exact
no-argument exporter를 active pair receiver에 직접 연결한다. Ubuntu `sudo` rule은 이 command에
한해 `!use_pty`이며 서비스 계정에는 주지 않는다. remote stderr를 stdout에 합치거나 secret을
regular file, terminal, argv, shell variable, `tee` 또는 로그에 두지 않는다.

```bash
pair="runtime-pair.<commit40>.<tree40>.<policy64>"
receiver="/opt/mooncen-an2p-runtime/releases/$pair/control/deploy/an2p/receive_control_bootstrap.py"
set -euo pipefail
/usr/bin/tailscale ssh ubuntu@cloud.dinosaur-piano.ts.net \
  /usr/bin/sudo -n -- /usr/local/libexec/mooncen-export-an2p-control-secrets \
| /usr/bin/python3.12 -I "$receiver" \
    --pair "$pair" --name control-secrets.env
```

receiver는 exact ordered 8-field envelope를 pending pair/tree/receipt/target/environment/identity에
결박하고 an2p-local 64-character Ops signing secret을 생성·보존한다. public `AUTH_SECRET`은
export되지 않는다. 같은 receiver로 reviewed deploy/status/DB configs와 known-hosts, 서로 다른
세 Ed25519 private key를 root `0600`으로 atomic publish한 뒤 immutable prepare를 실행한다.
그 뒤에만 `/usr/local/sbin/mooncen-an2p-runtime-install finalize-control --pair "$pair"`를
transient root service에서 호출한다.

## Promote와 고정 ingress/controller protocol

typed confirmation은 readiness가 돌려준 문자열을 그대로 사용한다.

```text
PROMOTE <target-identity> <release-digest> <receipt-digest> <generation> <state-sha256>
ROLLBACK <target-identity> <current-digest> <previous-digest> <generation> <state-sha256>
ROLLBACK_NATIVE <target-identity> <current-digest> <native-baseline-identity> <generation> <state-sha256>
```

API는 status-only endpoint의 live `status`에서 current/previous/generation/state hash를
얻고 DB state와 비교한다. 승인과 queue parameters에 같은 tuple을 저장한다. worker claim은
DB의 exact `agent_id`, UUID token, global sequence epoch, expiry로 발급되고 실행 evidence를
읽을 때 다시 검증한다. raw token은 API/UI/log에 반환하지 않는다.

worker는 어떤 ingress/controller 호출보다 먼저 forced deploy endpoint로
`lease-bind <job32> <epoch20> <token32>`을 실행한다. controller의 exclusive control lock이
이미 실행 중인 이전 shared mutation을 끝까지 기다린 뒤 token SHA-256과 최대 epoch를
root-only journal에 기록한다. bind 뒤 live worker-lease와 CAS를 재확인해야 다음 단계로 간다.

Promote의 고정 순서는 다음과 같다.

1. controller `lease-bind <job32> <epoch20> <token32>`
2. ingress `abort <tree>`로 같은 job/tree의 안전한 잔재만 정리
3. ingress `prepare <tree>`
4. 네 번의 `upload <tree> <canonical-name> <decimal-size> <sha256>`; 파일 byte는 stdin
5. controller `stage <tree> <job32> <epoch20> <token32>`
6. `load-images <tree> <job32> <epoch20> <token32>`
7. `preflight <tree> <job32> <epoch20> <token32>`
8. `promote <tree> <generation10> <active64> <previous64> <state64> <job32> <epoch20> <token32>`
9. 별도 `status`로 durable active/previous/generation과 worker lease 재확인
10. DB terminal commit 뒤 exclusive `lease-release <job32> <epoch20> <token32>`

SCP/SFTP, browser path와 remote shell은 사용하지 않는다. worker는 local 파일을
`O_NOFOLLOW`로 다시 열고 size/hash 및 fstat 전후 inode/owner/mode/time이 같은지 확인한
fd를 SSH stdin으로 보낸다. remote ingress helper도 `O_EXCL 0600`, size cap, hash, fsync와
partial cleanup을 강제한다.

container rollback은
`rollback <generation10> <active64> <previous64> <state64> <job32> <epoch20> <token32>` 후
`status`를 실행한다. Docker→native는
`rollback-native <generation10> <active64> <previous64> <state64> <job32> <epoch20> <token32>`
후 `status`를 실행한다.
모든 controller 성공 stdout은 compact/sort-key/single-line JSON + LF이며 exact key set을
검증한다. stderr, extra line 또는 nonzero exit를 성공 증적으로 쓰지 않는다.

## Native maintenance와 baseline

첫 Promote가 캡처하는 `native_fallback`은 단순 unit on/off 값이 아니다.

- `.deploy-info`의 commit/archive/file digest와 metadata
- root-owned prebuild provenance
- native executable tree 전체의 path/type/uid/gid/mode/content 또는 symlink target
- `.venv`, `node_modules`, Python bytecode를 포함한 실행 가능 byte
- API/frontend/AI unit과 native condition helper의 exact digest

runtime log처럼 명시적으로 검토된 mutable path만 제외한다. native unit은
`PYTHONDONTWRITEBYTECODE=1`이고 executable tree는 서비스 사용자가 수정할 수 없어야 한다.

`rollback-native`는 operation lock 안에서 live baseline과 승인된 identity를 다시
검증하고 Docker를 정지한 뒤 native unit 복원, host health, Docker stack disable,
controller state `null`의 durable commit 순으로 진행한다. 중간 실패/재부팅 시 guard가
transaction journal을 복구한다. native가 시작된 뒤 이전 Docker로 복구해야 하면 먼저
native 세 unit을 stop/disable/assert하고 Docker를 시작해 port overlap을 막는다.

성공한 native maintenance는 Ops timeline에 `runtime_target_kind=native`, generation 0,
state-null hash로 기록된다. 따라서 과거 container success가 다음 Promote를 drift로
오인하지 않는다. 이후 실제 native code 배포는 여전히 human/operator-only이며, 완료 뒤
새 clean build/validation evidence를 등록해야 한다.

## 취소·timeout·stale recovery

로컬 SSH 종료는 원격 controller 종료 증명이 아니다. remote mutation을 시작한 container
job은 즉시 cancel하지 않고 `cancellation requested, reconciling`으로 유지한다. worker
crash, lease loss, timeout도 remote fence와 `status`에서 transaction이 사라지고 승인
tuple로 수렴한 뒤에만 success/rollback/recovery_required를 확정한다.

job claim과 linked deployment의 `queued→running`은 한 transaction에서 exact
agent/token/epoch CAS한다. heartbeat, cancel, log/final commit도 같은 tuple과 fresh expiry를
요구한다. DB가 unavailable이거나 ownership이 바뀌면 worker는 다음 remote command를
시작하지 않는다.

stale reclaimer는 old DB owner를 먼저 덮지 않는다. DB row lock을 유지한 채 global
sequence의 더 큰 epoch로 remote `lease-bind`를 호출한다. exclusive controller lock이
old mutation을 끝까지 기다리고 새 epoch를 durable commit한 뒤 live status에서 그 fence를
확인했을 때만 DB owner를 전환한다. fence가 unavailable이면 이전 row를 그대로 둔다.
`recovered_previous`도 authoritative fence 전에는 terminal이 아니다. controller가 inactive
lease에도 최대 epoch를 보존하므로 old worker가 나중에 같은 token/epoch로 promote할 수 없다.
결과 generation은 승인 generation+1이어야 한다.

`status`는 outer 장기 control lock을 기다리지 않고 operation lock 안에서
`native_intent`, `state`, `transaction`, `worker_lease`를 atomic snapshot으로 읽는다.
`worker_lease`는 raw token 대신 job ID, epoch, token SHA-256, active/expiry만 가진다. 일반 native
maintenance의 durable intent는 cloud guard가 terminal commit/recovery/abort에서만
해제한다. an2p worker가 SSH 오류의 `finally`에서 intent를 지우지 않는다.

## API와 UI

- `GET /api/ops/deployments/container/readiness`
- `GET /api/ops/deployments/container/releases`
- `GET /api/ops/deployments/container/releases/{id}`
- `GET /api/ops/deployments/container/validation-receipts`
- `GET /api/ops/deployments/container/approvals`
- `GET /api/ops/deployments/container/timeline`
- `POST /api/ops/deployments/container/actions/promote`
- `POST /api/ops/deployments/container/actions/rollback`
- `POST /api/ops/deployments/container/actions/rollback-native`

Build/Validate POST는 명시적으로 unavailable이다. Deployments 화면은 release/image/bundle,
PASS receipt와 expiry, live current/previous/CAS, native baseline과 timeline을 함께 표시한다.
exact fresh PASS와 executor/status boundary가 없으면 Promote 버튼을 비활성화한다. previous가
없으면 container rollback만 막고, verified native baseline이 있으면 별도
`ROLLBACK_NATIVE` 승인 경로를 표시한다. readiness는 controller status에 exact
`worker_lease` schema가 있을 때만 `remote_claim_fencing_ready=true`이며 UI는 raw token이
아닌 “epoch 고정됨/차단됨” capability만 표시한다.

## 설치 후 GO 체크

다음 조건이 모두 맞기 전에는 운영 mutation을 시작하지 않는다.

1. migration/role/login 적용 및 crawler/API의 evidence INSERT negative test 통과
2. root-owned immutable control/Docker runtime과 activation receipt 검증
3. API/worker/tunnel/Docker operator 네 계정 분리, `sgm`의 docker/lxd membership 부재와
   old-GID process drain 확인; clean SSH session은 유지
4. 세 SSH key가 서로 다르고 forced dispatcher/permitopen negative test 통과
5. worker state와 immutable releases의 owner/mode exact
6. `mooncen-ops-db-tunnel`, `mooncen-ops-api`, `mooncen-deployment-worker` system unit healthy
7. root systemd의 IPv4·IPv6 Ops socket이 각각 `127.0.0.1:5175`, `[::1]:5175`를 계속
   보유하고 IPv6 fixed redirect가 canonical IPv4 origin으로 수렴하며 8002/Vite listener가 없음
8. legacy user unit/shared key가 disabled·quarantine되고 readiness가 이를 허용하지 않음
9. 등록 JSON의 tree/identity/digest와 Console PASS 카드가 exact 일치
10. controller `status`가 exact worker-lease schema를 가지며 transaction/native intent 없이
    DB timeline과 일치

```bash
sudo systemctl --no-pager --full status \
  mooncen-ops-db-tunnel.service \
  mooncen-ops-api.socket \
  mooncen-ops-api-ipv6.socket \
  mooncen-ops-api-ipv6.service \
  mooncen-ops-api.service \
  mooncen-deployment-worker.service

sudo journalctl -u mooncen-deployment-worker.service --since -15min --no-pager
```

API password 변경은 exact active immutable pair의
`tools/rotate_an2p_ops_password.py` → `tools/prepare_an2p_ops_control.py` 뒤 trusted
`apply-ops-rotation --pair <active-finalized-pair>` action으로 적용한다. pending 최초 설치라면
`apply`가 아니라 `finalize-control --pair <pending-pair>`를 사용한다. 환경 파일을 shell로
`source`하거나 비밀번호를 argv/log에 넣지 않는다. local Ops signing secret, worker env와
세 transport는 rotation 중 불변이다. password hash fingerprint가 바뀌면 이전 hash로 발급된
Ops JWT와 legacy no-fingerprint Ops JWT는 `401`이며 새 password login만 성공한다.

forced deploy endpoint의 controller mutation은 claim tuple 없이는 실행되지 않는다. 최초
bootstrap/native maintenance의 human interactive 경로가 필요하더라도 임의 claim token이나
controller action을 만들어 Ops approval/timeline을 우회하지 않는다. 별도 root recovery가
불가피하면 자동 감사 기록이 없다는 한계를 변경 티켓에 명시하고 실행자, 시각, 사유,
사전·사후 status digest와 public health 결과를 보존한 뒤 새 Ops runtime evidence와 DB
timeline을 reconcile하기 전에는 다음 자동 승격을 허용하지 않는다.
