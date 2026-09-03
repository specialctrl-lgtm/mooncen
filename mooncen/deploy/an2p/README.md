# an2p native 개발 환경

an2p는 MoonCen API와 frontend를 사용자 systemd 서비스로 실행한다. Docker 개발 runtime,
runtime pair, container 승격 및 운영 controller는 폐기되었다.

## 서비스

| 서비스 | 주소 |
| --- | --- |
| 개발 API | `http://127.0.0.1:8001/` |
| 개발 frontend | `http://127.0.0.1:5174/` |
| 문서 | `http://127.0.0.1:8765/` |

사용자 서비스는 다음과 같다.

- `mooncen-api.service`
- `mooncen-frontend.service`
- `mooncen-status-agent.service`
- `mooncen-docs.service`
- `mooncen-development-runtime.target`

설치하거나 갱신하려면 `sgm`으로 실행한다.

```bash
cd /home/sgm/src/project/mooncen
./deploy/an2p/install_user_services.sh --restart
```

정상 상태는 다음 명령으로 확인한다.

```bash
systemctl --user is-active mooncen-api.service
systemctl --user is-active mooncen-frontend.service
curl --noproxy '*' -fsS http://127.0.0.1:8001/health
curl --noproxy '*' -fsSI http://127.0.0.1:5174/
```

## Docker 배포 폐기

기존 호스트에 남아 있는 Docker runtime은 저장소 루트의
`deploy/decommission_docker_runtime.sh`로 철회한다. 이 도구는 native health를 먼저
검증하며, 제거 대상은 즉시 삭제하지 않고 `/var/lib/mooncen-native-recovery/` 아래의
root 전용 archive로 이동한다.

an2p의 미완료 control transaction은 `phase=started`, registration digest가 0이고 pending
receipt hash가 일치하는 경우에만 철회된다. DB 등록 이후 단계는 자동 철회하지 않는다.
