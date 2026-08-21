# Tailscale 상태 snapshot 배포

이 구성은 root 권한으로 `tailscale status --json`을 실행하되 원본 응답을
디스크나 로그에 남기지 않는다. 수집기는 allowlist에 포함된 상태 필드만
`/var/lib/mooncen-monitor/tailscale-status.json`에 원자적으로 교체한다.
파일 소유권은 `root:mooncen-monitor-snapshot`, 모드는 `0640`이며,
`DynamicUser=yes`인 API 프로세스는 정적 supplementary group을 통해 읽기만 한다.

snapshot에는 `schema_version`, 생성 시각, backend 상태, peer 수, 그리고 각
노드의 이름·DNS 첫 label·OS·온라인/활성 여부·파생 연결 상태·최근 확인
시각·키 만료 시각만 포함된다. 연결 상태는 `offline`, `idle`, `direct`,
`relay`, `unknown` 중 하나이며 원본 주소나 relay 이름은 저장하지 않는다.
`NodeKey`, `MachineKey`, `UserID`, Tailscale IP, endpoint, tailnet 정보와 Peer
맵의 key는 포함하지 않는다. `MONITOR_APP_EXCLUDED_NODES`에 지정한 노드와
기본 제외 노드 `ds1515`, `ds718`도 포함하지 않는다.

## 설치

아래 명령은 배포 호스트에서 root로 실행한다. 이 저장소에서는 원격 배포를
실행하지 않는다.

```sh
install -D -m 0644 mooncen-monitor-snapshot.sysusers.conf \
  /etc/sysusers.d/mooncen-monitor-snapshot.conf
systemd-sysusers /etc/sysusers.d/mooncen-monitor-snapshot.conf
install -m 0640 -o root -g root mooncen-monitor-snapshot.env.example \
  /etc/mooncen-monitor-snapshot.env

install -D -m 0755 monitor_app/collect_tailscale_status.py \
  /usr/local/libexec/mooncen-monitor/collect_tailscale_status.py
install -D -m 0644 tailscale_snapshot_deployment.md \
  /usr/local/share/doc/mooncen-monitor/tailscale-snapshot.md
install -m 0644 mooncen-tailscale-snapshot.service \
  /etc/systemd/system/mooncen-tailscale-snapshot.service
install -m 0644 mooncen-tailscale-snapshot.timer \
  /etc/systemd/system/mooncen-tailscale-snapshot.timer
install -m 0644 mooncen-monitor-app.service \
  /etc/systemd/system/mooncen-monitor-app.service

systemctl daemon-reload
systemctl enable --now mooncen-tailscale-snapshot.timer
systemctl start mooncen-tailscale-snapshot.service
systemctl restart mooncen-monitor-app.service
```

API unit은 `MONITOR_APP_TAILSCALE_SNAPSHOT_FILE`의 기본값과 같은 경로를
사용하고, `MONITOR_APP_TAILSCALE_SNAPSHOT_MAX_AGE_SECONDS`는 기본
180초다. 기존 `TAILSCALE_STATUS_FILE`, `TAILSCALE_STATUS_MAX_AGE_SECONDS`
이름도 하위호환 alias로 지원한다. 다른 값을 사용할 때도 수집기 출력
경로와 API 환경변수 경로를 함께 바꿔야 한다.

제외 노드는 `/etc/mooncen-monitor-snapshot.env`에서 설정한다. 두 unit이
이 비밀값 없는 파일만 공유하므로 collector가 API token이 들어 있는
`/etc/mooncen-monitor-app.env`를 읽을 필요가 없다.

## 배포 전/후 검증

```sh
python3 -m unittest monitor_app.test_collect_tailscale_status monitor_app.test_app
promtool check config prometheus.remote.yml

systemd-analyze verify \
  ./mooncen-tailscale-snapshot.service \
  ./mooncen-tailscale-snapshot.timer \
  ./mooncen-monitor-app.service

systemctl start mooncen-tailscale-snapshot.service
systemctl status --no-pager mooncen-tailscale-snapshot.service
systemctl list-timers --all mooncen-tailscale-snapshot.timer
stat -c '%U:%G %a %n' /var/lib/mooncen-monitor/tailscale-status.json
sudo -u nobody test ! -r /var/lib/mooncen-monitor/tailscale-status.json
```

마지막으로 API unit의 실제 동적 사용자 접근과 민감 key 부재를 확인한다.

```sh
systemd-run --wait --pipe --quiet \
  -p DynamicUser=yes \
  -p SupplementaryGroups=mooncen-monitor-snapshot \
  -p ReadOnlyPaths=/var/lib/mooncen-monitor \
  /usr/bin/test -r /var/lib/mooncen-monitor/tailscale-status.json

python3 - <<'PY'
import json
from pathlib import Path

data = json.loads(Path("/var/lib/mooncen-monitor/tailscale-status.json").read_text())
forbidden = {
    "NodeKey", "MachineKey", "UserID", "Endpoints", "TailscaleIPs",
    "AllowedIPs", "Addrs", "CurAddr", "PeerAPIURL", "MagicDNSSuffix",
}

def keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from keys(child)

found = forbidden.intersection(keys(data))
assert not found, found
print("snapshot allowlist check: ok")
PY
```
