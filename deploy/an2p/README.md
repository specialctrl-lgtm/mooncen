# an2p 개발·Ops 제어·Docker 배포 허브

an2p는 개발 애플리케이션과 운영 제어 기능을 같은 호스트에서 실행하지만, 같은 계정·DB
credential·SSH key·서비스 프로세스를 공유하지 않는다. 운영자가 접속하는 Ops Console은
root가 예약한 `http://127.0.0.1:5175/`에서 정적 UI와 API를 같은 origin으로 제공한다.
사용자 작업공간의 개발 frontend나 프록시는 운영 로그인 경로에 참여하지 않는다.

상세 신뢰 경계와 장애 복구는
[`docs/an2p-control-plane-architecture.md`](../../docs/an2p-control-plane-architecture.md),
Docker 증적·승인·rollback 계약은
[`docs/docker-ops-console.md`](../../docs/docker-ops-console.md)를 함께 본다.

> **배포 보류(2026-08-20):** incident-safe bootstrap 수정본을 새 review snapshot으로 독립
> 검토하기 전에는 아래 root bootstrap, phase 1, 운영 Promote를 실행하지 않는다. 기존
> `refs/mooncen/docker-release-snapshots/223fef9f6786da960faf9951324650ad`는
> 무효이며 로컬에 남아 있어도 재사용하지 않는다.

## 먼저 열 주소

VS Code Remote의 **PORTS** 화면에서 필요한 포트만 전달하고 Visibility를 반드시
`Private`로 유지한다.

| 기능 | canonical an2p 주소 | 설명 |
| --- | --- | --- |
| 사용자 개발 웹 | `http://127.0.0.1:5174/` | native 또는 Docker 개발 API를 사용하는 UI |
| Ops Console + Ops API | `http://127.0.0.1:5175/` | root-owned 정적 UI와 `/api`의 단일 신뢰 origin |
| 개발 API | `http://127.0.0.1:8001/` | public profile, 개발 DB 전용 |
| 문서 다운로드 | `http://127.0.0.1:8765/` | 생성 문서 폴더만 제공 |
| native 개발 PostgreSQL | `127.0.0.1:5432` | LXD PostgreSQL 16 + PostGIS |
| 운영 DB tunnel | `127.0.0.1:15432` | 격리된 Ops 서비스 계정용 |

`[::1]:5175`도 root systemd socket이 선점한다. 이 주소는 로그인을 처리하지 않고 항상
canonical IPv4 URL로 `308` redirect한다. 과거 hostname bookmark가 IPv6를 먼저 선택해도
일반 사용자가 같은 포트에 가짜 로그인 화면을 띄울 수 없게 하는 예약 경계다. non-loopback
주소에는 Ops listener를 만들지 않는다.

과거 별도 개발 프록시와 별도 Ops API port는 retired 상태다. 저장소의
`mooncen-ops-console.service`는 fail-closed sentinel이고 전역 mask되어야 한다. 운영 절차에서
이를 unmask하거나 실행하면 안 된다.

## 실행 계정과 권한

| 역할 | OS 계정 | 허용된 자원 |
| --- | --- | --- |
| Ops API + reviewed static UI | `mooncen_ops_api` | API DB credential, status-only SSH profile |
| container 배포 worker | `mooncen_deployment_worker` | queue DB credential, deploy-only SSH profile, immutable release read |
| 운영 DB tunnel | `mooncen_ops_db_tunnel` | DB port-forward 전용 key |
| 개발 Docker operator | `mooncen_docker_operator` | constrained system unit, 유일한 `docker` group membership |
| 개발 사용자 | `sgm` | native 개발 UI/API, status/docs, 고정 sudo helper만 |

`sgm`, Ops API, worker, tunnel은 `docker`와 `lxd` group에 속하면 안 된다. group을 제거한
뒤에도 옛 supplementary group GID를 유지한 `sgm` process만 root bootstrap이 bounded
drain한다. exact process를 pidfd로 고정하고 `SIGSTOP`으로 즉시 동결한 뒤 같은 pidfd로
`SIGKILL`한다. catch·block·ignore할 수 없는 freeze이므로 stale host-root-capable process에
handler 실행 기회를 주지 않는다. bounded exit 대기 뒤 old-GID rescan과 group membership
재검증을 거친다. 전체 user session은 종료하지 않으며 clean SSH session은 옛 GID가 없으므로
유지된다. `sgm`은 API/worker env, worker release root, deploy/status/DB private key를 읽을 수
없어야 한다.

고정 credential 경로는 다음과 같다.

- `/etc/mooncen-an2p/ops-api.env`
- `/etc/mooncen-an2p/deployment-worker.env`
- `/etc/mooncen-an2p/status-transport/`
- `/etc/mooncen-an2p/deploy-transport/`
- `/etc/mooncen-an2p/db-tunnel/`

각 파일은 `root:<consumer group> 0640`, parent는 `root:<consumer group> 0750`이다. worker의
mutable state만 `/var/lib/mooncen-deployment-worker/state`에서 worker 소유 `0700`이고,
`releases/<40hex-tree>`와 exact 네 evidence 파일은 root-owned read-only다.

## 서비스 구성

root system service:

- `mooncen-an2p-runtime-recovery.service`
- `mooncen-ops-db-tunnel.service`
- `mooncen-ops-api.socket` / `mooncen-ops-api.service`
- `mooncen-ops-api-ipv6.socket` / `mooncen-ops-api-ipv6.service`
- `mooncen-deployment-worker.service`
- 선택적으로 `mooncen-docker-dev.service`

`sgm` user service:

- `mooncen-development-runtime.target`
- native 선택 시 `mooncen-api.service`, `mooncen-frontend.service`
- `mooncen-status-agent.service`, `mooncen-docs.service`

Ops API와 worker는 root-owned atomic runtime pair의 같은 control snapshot에서 실행한다.

```text
/opt/mooncen-an2p-runtime/current
  -> releases/runtime-pair.<commit>.<tree>.<policy>
       ├─ control/
       └─ docker/
```

`/opt/mooncen-an2p-control/current`와 `/opt/mooncen-an2p-docker/current`는 이 단일 pointer의
고정 alias다. pair manager는 operation journal과 lock으로 API/worker/Docker를 정지하고
pointer를 교체하며, boot recovery가 중단된 전환을 먼저 수렴시킨다. Ops IPv4·IPv6 socket은
pair 교체 중에도 중지하지 않아 5175를 계속 예약한다.
root bootstrap은 첫 host-security mutation 전에 enabled
`mooncen-an2p-bootstrap-recovery.service`를 설치한다. recovery를 arm하기 전 independently
authorized exact installer SHA-256을 먼저 검증하고,
검증한 byte를 `/var/lib/mooncen-an2p-runtime/reviewed-install-runtime-snapshot.sh`에
`root:root 0700`으로 fsync한 뒤 parent directory까지 fsync하는 atomic rename으로 게시한다.
recovery와 final trust commit은 mutable `sgm` worktree가 아니라 이 불변 root stage만 실행한다.
stage 게시가 durable하지 않으면 recovery를 arm하거나 host-security mutation을 시작하지 않는다.
bootstrap은 `loginctl terminate-user`를 호출하지 않는다. membership을 제거한 뒤 captured old
docker/lxd GID를 가진 exact `sgm` process만 재검증해 pidfd로 고정하고 `SIGSTOP`으로 먼저
동결한다. `SIGSTOP`은 catch·block·ignore할 수 없으므로 host-root-capable stale process가
handler를 실행할 window가 없다. 이어 같은 pidfd에 `SIGKILL`을 보내고 bounded exit 대기,
old-GID process rescan, `sgm` group membership 부재 재검증을 모두 통과해야 한다. 새 clean SSH
session에는 old GID가 없으므로 recovery 재개 중에도 살아 있어야 한다.

durable journal은 `prepared` → `membership_revoked` → `privileged_processes_drained` →
`native_restored` → `trust_committed` 순서로만 전진하며 완료 phase를 다시 실행하지 않는다.
기존 native 복구는 설치돼 있던 `mooncen-api.service`와 `mooncen-frontend.service`를 직접
복원하고 `mooncen-development-runtime.target`을 요구하지 않는다. reviewed target은 phase 1이
나중에 설치한다. recovery unit은 `Restart=on-abnormal`, 30초 간격, 최대 한 번 자동 재시작,
15분 start timeout으로 signal/reboot만 제한적으로 재개한다. 명시적 invariant·health·설치 실패는
fail-stop하고 journal/stage를 보존하며, 원인 확인 뒤 수동으로만 재시도한다. retained GID 제거,
credential quarantine, native/Docker public health, installer/trust fsync가 모두 끝난
`trust_committed`에서만 disable된다. 그 전에는
`/root/mooncen-an2p-runtime-bootstrap.sh`와 reviewed installer stage를 삭제하지 않는다.
`trust_committed` 성공 뒤에는 reviewed installer stage를 제거하고, recovery unit의
inactive/disabled 상태와 설치된 installer SHA를 확인한 뒤 one-use bootstrap stage를 제거한다.

## 일상적인 개발 runtime 선택

root isolation 설치가 끝난 뒤 개발 사용자는 다음 wrapper만 사용한다.

```bash
cd /home/sgm/src/project/mooncen
/bin/bash ./deploy/an2p/install_user_services.sh \
  --development-runtime native --restart
```

또는 reviewed Docker runtime을 선택한다.

```bash
/bin/bash ./deploy/an2p/install_user_services.sh \
  --development-runtime docker --restart
```

이 wrapper는 다음 조건이 하나라도 틀리면 중단한다.

- 현재 session에 `docker` 또는 `lxd` host-root group이 남아 있음
- isolated root system unit이나 root runtime snapshot이 없음
- 옛 user control-plane unit이 전역 mask되지 않음
- user home에 superseded shared key/env가 남아 있음
- 개발 사용자가 protected service credential/release를 읽을 수 있음

runtime 전환 자체는 고정 root helper가 맡는다. Docker 선택은 native API/frontend를 먼저
stop·disable하고, native 선택은 Docker unit을 먼저 stop·disable한다. LXD native DB와 Docker
named volume은 서로 다른 데이터이며 runtime 선택으로 삭제하지 않는다. `down --volumes`,
LXD delete/restore 같은 파괴 작업은 이 wrapper에 없다.
selector는 pair manager와 phase-2 installer의 root operation lock을 공유한다. manager child만
검증된 inherited lock descriptor를 사용하고, human 선택은 transaction commit까지 기다리며
crash journal이 남으면 거부된다. phase-1/finalize/rotation 성공 JSON은 같은 fence 아래 exact
Docker marker/unit/native 부재와 8001/5174 health를 마지막으로 확인한 뒤 출력된다.

현재 worktree의 임의 `docker compose build`는 영구 runtime 또는 운영 Promote 증적이 아니다.
review snapshot, clean build, an2p PASS receipt를 만드는 절차는
[`docs/docker-development.md`](../../docs/docker-development.md)의
`Persistent an2p development runtime` 절을 따른다.

## 최초 설치는 반드시 두 단계로 끝낸다

신규 설치의 권위 순서는 다음과 같다. 순서를 바꾸거나 phase 1 전에 cloud/운영 DB를
변경하지 않는다.

최초 pair가 아직 없는 host는 reviewed native API/frontend가 active+enabled인 상태에서만
bootstrap할 수 있다. retired split alias를 쓰는 legacy root Docker가 선택돼 있으면 bootstrap은
어떤 session/unit/pointer도 바꾸기 전에 중단한다. 먼저 기존 reviewed 절차로 native를 복구한
뒤 다시 시작해야 하며, installer는 pair 없는 legacy Docker prestate를 추측해 재구성하지 않는다.

1. root-of-trust bootstrap 직후 trusted `prepare-development-bootstrap` no-argument action으로
   `/root/mooncen-an2p-bootstrap/docker-development.env`를 생성·검증한다. 독립 CSPRNG
   development DB owner/API/auth secret 세 개와 localhost allowlist만 포함하며 production,
   Ops, user-home, LXD/native credential은 읽거나 재사용하지 않는다.
2. trusted root installer의 `install`로 exact pair를 만들고 Docker 개발 runtime의
   `8001`/`5174` health가 통과하는지 확인한다. 이 phase는 운영 credential을 읽지 않고,
   DB tunnel·Ops API·worker·status agent를 시작하지 않으며 운영 DB/SSH endpoint를
   변경하지 않는다.
3. an2p root의 일회성 `0700` stage에서 exact comment를 가진 서로 다른 Ed25519 key 세 개를
   만들고 private bytes를 pending pair의 strict receiver에 먼저 publish한다. cloud로 옮길 수
   있는 것은 `.pub` 세 개뿐이다.
4. fresh backup을 확인한 뒤 같은 reviewed native release를 cloud에 guarded setup하여
   controller/bootstrap source·helper 계약과 root-only exporter를 설치한다. 이 setup은 live
   PostgreSQL HBA를 atomic 교체·reload하고 전용 worker TLS/SCRAM 및 다른 DB 거부를 실제
   probe한 뒤 exporter를 게시한다. container controller/library와 stack/guard unit은 아직
   설치·기동하지 않고 위 목록의 6단계 target-identity bootstrap만 이를 수행한다.
5. an2p에서 만든 서로 다른 deploy/status/DB public key로 cloud 전용 endpoint와
   `/etc/mooncen/container-bootstrap.json`을 provision하고 legacy shared key를 제거한다.
6. phase 1 pending receipt의 exact `target_identity`를 human Tailscale root 경로에서
   `mooncen-container-bootstrap` stdin으로 한 번 결박한다.
7. 별도 non-secret Tailscale pre-auth 뒤 cloud exporter의 stdout을 active pair의 strict
   receiver stdin에 직접 연결한다. 이어 reviewed config/known-host bytes도 같은 receiver로
   받는다.
8. immutable pair의 준비기를 실행한 뒤에만 trusted `finalize-control --pair "$pair"`를
   실행한다. preflight 뒤 registration 전용 DB tunnel만 먼저 stage/start하여 transport를
   probe하고 evidence handoff와 dedicated DB registration을 수행한다. registration 성공 뒤
   isolated Ops API/worker/status와 최종 durable control plane을 수렴한다.

`install` 성공 JSON의 `active_pair`가 `$pair`와 같고 아래 검사가 모두 성공해야 phase 1
PASS다. `pending-control-finalization.json`은 exact pair/tree/receipt/target/environment에
결박된 root-only durable receipt다. 실패·재부팅·재시도 중에도 Docker 개발 runtime은
유지되고, 만료한 PASS receipt는 같은 tree에서 갱신하지 않는다. 새 reviewed pair를 만든다.

```bash
pair="runtime-pair.<commit40>.<tree40>.<policy64>"
sudo /usr/local/libexec/mooncen-an2p-service-control runtime-status
systemctl is-enabled --quiet mooncen-docker-dev.service
systemctl is-active --quiet mooncen-docker-dev.service
curl --noproxy '*' -fsS http://127.0.0.1:8001/health
curl --noproxy '*' -fsSI http://127.0.0.1:5174/
! systemctl is-active --quiet mooncen-ops-db-tunnel.service
! systemctl is-active --quiet mooncen-ops-api.service
! systemctl is-active --quiet mooncen-deployment-worker.service
! systemctl is-active --quiet mooncen-ops-status-agent.service
```

endpoint provision 전에 필요한 key를 다음처럼 만든다. 이 동작은 an2p root local
filesystem과 pending receipt만 변경하며 cloud/DB를 변경하지 않는다. receiver 성공 뒤 private
source copy를 바로 제거하고 `.pub`만 human root staging에 사용한다. endpoint provision이
완료되면 남은 public stage도 제거한다.

```bash
sudo -i
set -euo pipefail
pair="runtime-pair.<commit40>.<tree40>.<policy64>"
control="/opt/mooncen-an2p-runtime/releases/$pair/control"
receiver="$control/deploy/an2p/receive_control_bootstrap.py"
key_stage=/run/mooncen-an2p-key-generation
[ ! -e "$key_stage" ] && [ ! -L "$key_stage" ]
/usr/bin/install -d -o root -g root -m 0700 "$key_stage"
umask 077
/usr/bin/ssh-keygen -q -t ed25519 -N '' \
  -C mooncen-an2p-container-deploy-20260819 \
  -f "$key_stage/deploy-id_ed25519"
/usr/bin/ssh-keygen -q -t ed25519 -N '' \
  -C mooncen-an2p-container-status-20260819 \
  -f "$key_stage/status-id_ed25519"
/usr/bin/ssh-keygen -q -t ed25519 -N '' \
  -C mooncen-an2p-ops-db-20260819 \
  -f "$key_stage/db-id_ed25519"
for role in deploy status db; do
  /usr/bin/cat "$key_stage/$role-id_ed25519" \
  | /usr/bin/python3.12 -I "$receiver" \
      --pair "$pair" --name "$role-id_ed25519"
  /usr/bin/rm -- "$key_stage/$role-id_ed25519"
done
```

cloud root staging에는 위 stage의 three `.pub` files와 reviewed provisioner inputs만 전송한다.
endpoint 성공과 public fingerprints 확인 뒤
`/usr/bin/rm -- "$key_stage"/{deploy,status,db}-id_ed25519.pub` 및
`/usr/bin/rmdir -- "$key_stage"`로 one-use public stage를 제거한다. reboot로 `/run` stage가
사라지면 남은 bootstrap private keys를 재사용해 public blob만 안전하게 재도출하는 별도
reviewed recovery 없이는 endpoint provision을 계속하지 않는다.
cloud endpoint provision과 target-identity bootstrap은
[`docs/docker-production.md`](../../docs/docker-production.md)의 ordered gate를 그대로
따른다. Tailscale 브라우저 재인증 문구가 secret stream에 섞이지 않도록 exporter pipe와
분리하여 먼저 확인한다.

```bash
sudo -i
set -euo pipefail
pair="runtime-pair.<commit40>.<tree40>.<policy64>"
control="/opt/mooncen-an2p-runtime/releases/$pair/control"
receiver="$control/deploy/an2p/receive_control_bootstrap.py"

# 비밀이 없는 별도 사전 인증. 성공한 뒤에만 다음 pipeline을 시작한다.
/usr/bin/tailscale ssh ubuntu@cloud.dinosaur-piano.ts.net /usr/bin/true

# remote stderr는 별도 stderr로 둔다. 2>&1, tee, 임시 파일, shell 변수, argv를 쓰지 않는다.
/usr/bin/tailscale ssh ubuntu@cloud.dinosaur-piano.ts.net \
  /usr/bin/sudo -n -- /usr/local/libexec/mooncen-export-an2p-control-secrets \
| /usr/bin/python3.12 -I "$receiver" \
    --pair "$pair" --name control-secrets.env
```

위 root shell의 `set -o pipefail`이 실제 적용되어야 한다. 성공 stdout은 receiver가 만든
canonical JSON 한 줄이어야 하며 `name=control-secrets.env`, exact pair를 가리켜야 한다.
exporter는 exact no-argument `sudo -n` rule과 command-scoped `!use_pty`만 사용한다. secret
bytes를 terminal, regular file, command line, shell variable, `tee`, 로그에 두거나 stderr와
합치지 않는다. exporter의 8개 ordered field에는 public `AUTH_SECRET`이 없으며 receiver가
an2p 전용 signing secret을 root-only로 생성·보존한다.

세 transport triplet도 pending pair의 receiver를 통과해야 한다. key는 앞 단계에서 이미
receiver가 root `0600`으로 publish했으며 endpoint provision에 사용한 public blob에 대응하고
서로 다른 derived public blob이어야 한다. config와 known-host bytes는 active immutable
pair가 producer다.

```bash
for spec in \
  deploy-ssh_config:cloud-container-deploy.ssh_config \
  status-ssh_config:cloud-container-status.ssh_config \
  db-ssh_config:cloud-ops-db.ssh_config; do
  name=${spec%%:*}
  template=${spec#*:}
  /usr/bin/cat "$control/deploy/an2p/local/$template" \
  | /usr/bin/python3.12 -I "$receiver" --pair "$pair" --name "$name"
done

for name in deploy-known_hosts status-known_hosts db-known_hosts; do
  /usr/bin/cat "$control/deploy/an2p/local/cloud-deploy.known_hosts" \
  | /usr/bin/python3.12 -I "$receiver" --pair "$pair" --name "$name"
done

"$control/.venv/bin/python" -I \
  "$control/tools/prepare_an2p_ops_control.py"

/usr/bin/systemd-run --wait --pipe --collect \
  --unit=mooncen-an2p-runtime-finalize-control \
  /usr/bin/env -i HOME=/root LANG=C LC_ALL=C \
  PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  /usr/local/sbin/mooncen-an2p-runtime-install \
  finalize-control --pair "$pair"
```

receiver는 missing/extra/duplicate/reordered/comment/blank/CR/no-final-LF 입력, pair/identity
불일치, 다른 residue를 거부한다. `finalize-control` 재실행은 exact durable transaction만
이어가며 Docker를 내리지 않는다. 성공은 5175 same-origin health와 IPv6 308, worker의
authoritative heartbeat, system status agent, tunnel, legacy 8002/user service 부재까지
검증한 뒤에만 반환한다.

## Ops Console 로그인과 비밀번호 변경

로그인 ID는 고정 `opsadmin`이다. cloud에서 가져온 최초 envelope에는 password hash만 있고
평문 `ops-credentials.txt`는 없다. 기존 hash에 대응하는 비밀번호를 모르면 pending pair에서
rotation tool을 먼저 실행하고, 그 다음 prepare와 `finalize-control`을 수행한다. rotation이
생성한 root-only `/root/mooncen-an2p-bootstrap/ops-credentials.txt`를 password manager로 옮기고
일반 shell, 채팅, Git, service env에 평문을 복사하지 않는다.

```bash
sudo -i
pair="runtime-pair.<active-finalized-commit40>.<tree40>.<policy64>"
control="/opt/mooncen-an2p-runtime/releases/$pair/control"
"$control/.venv/bin/python" -I "$control/tools/rotate_an2p_ops_password.py"
"$control/.venv/bin/python" -I "$control/tools/prepare_an2p_ops_control.py"
/usr/bin/systemd-run --wait --pipe --collect \
  --unit=mooncen-an2p-apply-ops-rotation \
  /usr/bin/env -i HOME=/root LANG=C LC_ALL=C \
  PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  /usr/local/sbin/mooncen-an2p-runtime-install \
  apply-ops-rotation --pair "$pair"
/usr/bin/cat /root/mooncen-an2p-bootstrap/ops-credentials.txt
```

rotation tool은 canonical URL, login ID와 새 password만 root-only credential 파일에 기록하고
service key나 cloud secret을 읽지 않는다. API env에는 hash만 들어가고 an2p 전용
`AUTH_SECRET`, worker env와 세 transport는 바뀌지 않는다. pending 최초 설치에서는 마지막
action이 `finalize-control --pair "$pair"`이고, 위 `apply-ops-rotation`은 이미 finalized된 pair에만
사용한다. 적용 후 새 hash fingerprint가 없는 이전 Ops JWT/cookie는 `401`이고 새 값 로그인은
성공해야 한다. 일반 public 사용자 token에는 이 Ops 전용 fingerprint 경계를 적용하지 않는다.

## Docker 운영 배포

container 운영 배포의 권위 경로는 다음과 같다.

```text
reviewed clean tree
  -> immutable release.json/images.tar/compose/validation.json
  -> root new-only handoff
  -> dedicated worker DB evidence registration
  -> Ops admin typed approval
  -> deploy-only forced-command SSH endpoint
  -> cloud root controller CAS + durable status reconciliation
```

브라우저는 path, Compose, image tag, SSH option이나 명령을 보내지 않는다. fresh exact PASS
receipt와 live controller current/previous/generation/state hash가 모두 일치할 때만 Promote가
활성화된다. container rollback과 pinned native-baseline maintenance도 별도 typed confirmation과
one-time approval을 사용한다.

장기 실행 worker에는 일반 cloud shell key와 PowerShell이 없다. 기존 native 배포 queue는
실행하지 않는다. 최초 bootstrap 또는 향후 native code maintenance는 an2p의 신뢰된 운영자가
대화형 Tailscale 경로에서 수행하고, container runtime/transaction/native intent가 있으면
fail-closed한다.

## 개발 DB가 비어 있을 때

빈 개발 DB가 운영 DB fallback보다 안전한 기본값이다. 개발 schema를 먼저 적용하고, 기능
검증에 필요한 공개 catalog만 별도 sync 절차로 가져온다. 사용자, OAuth, Ops session/audit,
비밀과 원본 운영 식별자는 복제하지 않는다.

```bash
.venv/bin/dotenv -f /home/sgm/.config/mooncen-an2p/mooncen.env run -- \
  .venv/bin/python DB/setup_db.py --mode migrate
```

공개 catalog 동기화 계약은
[`docs/production-to-development-sync.md`](../../docs/production-to-development-sync.md)를
따른다. LXD lifecycle은 `sgm`의 direct `lxc` 권한 대신 설치된 고정 helper의
`lxd-db-start|lxd-db-stop|lxd-db-status` action만 사용한다.

## 상태 확인

```bash
systemctl --no-pager --full status \
  mooncen-an2p-runtime-recovery.service \
  mooncen-ops-db-tunnel.service \
  mooncen-ops-api.socket mooncen-ops-api.service \
  mooncen-ops-api-ipv6.socket mooncen-ops-api-ipv6.service \
  mooncen-deployment-worker.service

systemctl --user --no-pager --full status \
  mooncen-development-runtime.target \
  mooncen-api.service mooncen-frontend.service \
  mooncen-status-agent.service mooncen-docs.service

curl --noproxy '*' -fsS http://127.0.0.1:5175/health
curl --noproxy '*' -fsSI http://127.0.0.1:5175/
curl -g -fsSI 'http://[::1]:5175/'
curl --noproxy '*' -fsS http://127.0.0.1:8001/health
curl --noproxy '*' -fsSI http://127.0.0.1:5174/
curl --noproxy '*' -fsSI http://127.0.0.1:8765/
sudo /usr/local/libexec/mooncen-an2p-service-control runtime-status
sudo /usr/local/libexec/mooncen-an2p-service-control lxd-db-status
```

IPv6 응답은 `308`과 `Location: http://127.0.0.1:5175/`이어야 한다. 다음 결과는 GO 조건이다.

- 5175의 IPv4·IPv6 listener가 root systemd socket 소유
- retired 별도 Ops API port와 mutable frontend proxy listener가 없음
- API/worker/tunnel/`sgm`에 `docker`·`lxd` membership이 없음
- deploy/status/DB key digest가 모두 다르고 각 consumer 외에는 읽을 수 없음
- worker heartbeat와 PASS evidence가 Console 표시와 일치
- `systemctl --failed`와 `systemctl --user --failed`가 비어 있음

전체 재부팅 drill, LXD snapshot restore, runtime pair activation은 사용자 작업을 중단할 수 있다.
자동으로 수행하지 말고 변경 창구와 복구 증적을 준비한 뒤 실행한다.

## Word 문서 가져오기

문서 서버는 저장소 전체가 아니라 `docs/crawler-quality-architecture`만 제공한다. 직접 파일을
받을 때는 기존 운영자 SSH의 SFTP를 사용하며 Samba/445를 새로 열지 않는다.

```bash
sftp sgm@an2p
cd /home/sgm/src/project/mooncen/docs/crawler-quality-architecture
get *.docx
```
