# an2p native 개발 환경

an2p는 MoonCen API와 frontend를 사용자 systemd 서비스로 실행한다.

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
