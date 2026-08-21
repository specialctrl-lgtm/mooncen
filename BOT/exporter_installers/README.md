# MoonCen Exporter 자동 설치기

이 디렉터리는 BOT의 중앙 Prometheus가 Ubuntu, Windows, macOS 노드를 수집할 수
있도록 exporter를 자동 설치한다.

- Ubuntu/Debian: Prometheus `node_exporter` 1.11.1
- Ubuntu/Debian: Tailscale 공식 stable APT 패키지와 `tailscaled`
- macOS Intel/Apple Silicon: Prometheus `node_exporter` 1.11.1과 `launchd`
- Windows 10/11 및 Windows Server 2016 이상:
  Prometheus Community `windows_exporter` 0.31.7
- Ubuntu 온도: `lm-sensors`와 커널 `hwmon`
- macOS 온도: Node Exporter 기본 `thermal` collector
- Windows 온도: `LibreHardwareMonitor` 0.9.6과 Windows Exporter
  `textfile` collector

Windows에서는 `node_exporter`가 아니라 공식적으로 권장되는
`windows_exporter`를 사용한다.

세 설치기는 다음 원칙을 공유한다.

- 관리자 권한 확인
- CPU 아키텍처 자동 판별
- 버전이 고정된 공식 GitHub 릴리스 다운로드
- 내장 SHA-256과 다운로드 파일 비교
- 운영체제 서비스 등록 및 자동 시작
- Ubuntu에서 Tailscale가 없으면 공식 저장소를 등록해 자동 설치
- macOS에서는 이미 설치된 Tailscale CLI 또는 앱을 자동 감지
- Tailscale IPv4 자동 감지 및 해당 주소에만 바인딩
- Tailscale이 없으면 안전하게 loopback 주소에만 바인딩
- 설치 후 `/metrics` 자체 검사
- 모든 인터페이스 바인딩은 명시적인 위험 승인 없이는 거부
- 온도 라이브러리 설치와 Prometheus 온도 메트릭 연결

설치 전에 다운로드와 SHA-256 검증만 시험할 수도 있다.

```bash
bash install.sh --validate-only
bash install-macos.sh --validate-only
```

```powershell
.\Install-WindowsExporter.ps1 -ValidateOnly
```

## 외부 다운로드 설치

공개 Nginx 설정을 배포하면 다음 HTTPS 주소에서 설치기를 받을 수 있다.

```text
https://mon.binary.kr/installers/install.sh
https://mon.binary.kr/installers/install-macos.sh
https://mon.binary.kr/installers/Install-WindowsExporter.ps1
https://mon.binary.kr/installers/SHA256SUMS
```

Ubuntu에서는 설치기와 해시 목록을 받은 뒤 검증하고 실행한다.

```bash
install_dir="$(mktemp -d)"
trap 'rm -rf -- "$install_dir"' EXIT
curl --proto '=https' --tlsv1.2 -fsSLo \
  "$install_dir/install.sh" \
  https://mon.binary.kr/installers/install.sh
curl --proto '=https' --tlsv1.2 -fsSLo \
  "$install_dir/SHA256SUMS" \
  https://mon.binary.kr/installers/SHA256SUMS
(
  cd "$install_dir"
  grep ' install.sh$' SHA256SUMS | sha256sum -c -
)
sudo bash "$install_dir/install.sh"
```

macOS에서는 Terminal에서 설치기와 해시 목록을 받은 뒤 검증하고 실행한다.

```bash
install_dir="$(mktemp -d)"
trap 'rm -rf -- "$install_dir"' EXIT
curl --proto '=https' --tlsv1.2 -fsSLo \
  "$install_dir/install-macos.sh" \
  https://mon.binary.kr/installers/install-macos.sh
curl --proto '=https' --tlsv1.2 -fsSLo \
  "$install_dir/SHA256SUMS" \
  https://mon.binary.kr/installers/SHA256SUMS
(
  cd "$install_dir"
  grep ' install-macos.sh$' SHA256SUMS | shasum -a 256 -c -
)
sudo bash "$install_dir/install-macos.sh"
```

Windows에서는 관리자 PowerShell에서 다운로드 파일의 SHA-256을 검증한 뒤
실행한다.

```powershell
$BaseUrl = "https://mon.binary.kr/installers"
$Installer = Join-Path $env:TEMP "Install-WindowsExporter.ps1"
Invoke-WebRequest "$BaseUrl/Install-WindowsExporter.ps1" -OutFile $Installer
$Expected = (
    (Invoke-RestMethod "$BaseUrl/SHA256SUMS") -split "`n" |
        Where-Object { $_ -match " Install-WindowsExporter\.ps1$" }
).Split()[0]
if ((Get-FileHash $Installer -Algorithm SHA256).Hash -ne $Expected) {
    throw "Installer SHA-256 verification failed."
}
powershell -NoProfile -ExecutionPolicy Bypass -File $Installer
```

`curl ... | bash` 또는 `irm ... | iex`처럼 다운로드 내용을 즉시 실행하지
않고, 항상 파일로 저장한 후 `SHA256SUMS`와 비교한다. 설치기 파일이 변경될
때마다 `SHA256SUMS`도 함께 갱신해야 한다.

## Ubuntu

파일을 대상 서버에 복사한 다음 실행한다.

```bash
chmod +x install.sh
sudo ./install.sh
```

Tailscale이 동작 중이면 자동으로 다음과 같은 주소에 바인딩한다.

```text
TAILSCALE_IPV4:9100
```

Tailscale가 설치되지 않은 Ubuntu/Debian에서는 공식 stable APT 저장소와
서명 키를 등록하고 `tailscale` 패키지를 설치한 뒤 `tailscaled` 서비스를
활성화한다. 처음 설치한 장비는 다음 명령으로 브라우저 인증을 완료한다.

```bash
sudo tailscale up
sudo ./install.sh
```

인증 전에는 Tailscale IPv4가 없으므로 Node Exporter를 `127.0.0.1:9100`에
안전하게 바인딩한다. 인증 후 설치기를 다시 실행하면 Tailscale IPv4로
자동 변경된다. Tailscale 인증키는 설치기에 포함하거나 로그에 출력하지
않는다.

Tailscale을 사용하지 않고 사설 LAN 주소에 바인딩하려면 명시한다.

```bash
sudo ./install.sh \
  --listen-address 192.168.10.20:9100
```

모든 인터페이스에 바인딩하는 것은 권장하지 않는다. 꼭 필요한 경우에만
다음처럼 명시적으로 승인한다.

```bash
sudo ./install.sh \
  --listen-address 0.0.0.0:9100 \
  --allow-any-listen
```

확인:

```bash
systemctl status node_exporter --no-pager
systemctl status tailscaled --no-pager
tailscale status
curl -fsS http://$(tailscale ip -4):9100/metrics | head
```

Ubuntu 설치기는 `lm-sensors`를 자동 설치하고 CPU 제조사에 따라
`k10temp` 또는 `coretemp` 커널 모듈을 사용할 수 있으면 즉시 로드한다.
재부팅 후에도 로드하도록 `/etc/modules-load.d/mooncen-hwmon.conf`를
관리한다. 일반 호스트 지표와 함께 `systemd` collector를 활성화하고,
물리 센서가 커널의 `/sys/class/hwmon/`에 노출되면 기본 `hwmon` collector가
다음 온도 지표를 제공한다.

```text
node_hwmon_temp_celsius
```

VM처럼 물리 센서가 없는 시스템에서는 설치가 성공해도 온도 지표가 없을 수
있으며, 설치 결과에 `temperature_metrics=unsupported`로 표시된다.

설치 중 서비스는 `active`인데 metrics probe가 실패했다고 표시되면 최신
설치기를 다시 내려받아 실행한다. 초기 배포본은 큰 `/metrics` 응답을
`curl | grep -q`로 검사하면서 정상 응답을 실패로 오판할 수 있었으며, 현재
버전은 응답을 임시 파일에 완전히 저장한 뒤 검사한다.

## Windows

관리자 PowerShell에서 실행 정책을 현재 프로세스에만 허용하고 설치한다.

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\Install-WindowsExporter.ps1
```

Tailscale을 사용하지 않는 사설 LAN 설치:

```powershell
.\Install-WindowsExporter.ps1 `
  -ListenAddress 192.168.10.30 `
  -AllowedRemoteAddress 192.168.10.10
```

`AllowedRemoteAddress`에는 BOT Prometheus의 사설 IP 또는 CIDR을 지정할 수
있다. 기본값은 `Any`지만 방화벽 규칙의 로컬 주소가 선택한 Tailscale 또는
사설 IP로 제한된다.

기본 collector에 온도 브리지용 `textfile` collector를 자동으로 추가한다.
추가 collector가 필요하면 공백 없이 전달한다.

```powershell
.\Install-WindowsExporter.ps1 `
  -Collectors "[defaults],process,scheduled_task"
```

확인:

```powershell
Get-Service windows_exporter
Invoke-WebRequest "http://$(tailscale ip -4):9182/metrics" -UseBasicParsing
```

Windows 설치기는 공식 LibreHardwareMonitor 릴리스를 함께 검증·설치한다.
관리자 권한의 예약 작업이 1분마다 CPU, GPU, 메인보드, 저장장치 등에서
읽을 수 있는 온도를 다음 파일에 원자적으로 기록한다.

```text
C:\ProgramData\windows_exporter\textfile_inputs\mooncen_temperature.prom
```

Windows Exporter의 `textfile` collector를 통해 다음 메트릭이 제공된다.

```text
mooncen_hardware_temperature_celsius
mooncen_temperature_sensor_count
mooncen_temperature_collector_success
mooncen_temperature_collector_timestamp_seconds
```

센서가 없는 VM이나 지원하지 않는 하드웨어에서는
`mooncen_temperature_sensor_count 0`으로 표시되며 설치 실패로 취급하지
않는다. 라이브러리 실행 자체가 실패하면
`mooncen_temperature_collector_success 0`으로 표시된다.

## macOS

Intel Mac과 Apple Silicon Mac을 자동 판별한다. 설치기는 공식 Node Exporter
Darwin 릴리스를 고정 SHA-256으로 검증하고 다음 경로에 설치한다.

```text
/usr/local/bin/node_exporter
/Library/LaunchDaemons/kr.binary.mooncen.node_exporter.plist
```

Tailscale이 연결되어 있으면 `TAILSCALE_IPV4:9100`에 바인딩한다. PATH에
`tailscale` 명령이 없더라도 `/Applications/Tailscale.app`에 포함된 CLI를
감지한다. Tailscale이 없거나 연결되지 않은 상태에서는 안전하게
`127.0.0.1:9100`에만 바인딩한다. Tailscale 설치와 로그인을 마친 뒤 설치기를
다시 실행하면 주소가 자동 변경된다.

```bash
chmod +x install-macos.sh
sudo ./install-macos.sh
```

Tailscale을 사용하지 않고 사설 LAN 주소에 바인딩할 수도 있다.

```bash
sudo ./install-macos.sh \
  --listen-address 192.168.10.40:9100
```

확인:

```bash
sudo launchctl print system/kr.binary.mooncen.node_exporter
TAILSCALE_BE_CLI=1 tailscale ip -4
curl -fsS http://$(TAILSCALE_BE_CLI=1 tailscale ip -4):9100/metrics | head
```

macOS의 기본 `thermal` collector가 지원하는 장비에서는 다음 지표로 온도를
수집한다. Node Exporter 1.11 계열은 Apple Silicon CPU 온도 지표를 지원한다.
Intel Mac이나 일부 macOS/하드웨어 조합에서는 전력 제한 지표만 나오고 온도
센서 값은 없을 수 있으며, 이 경우 설치 결과에
`temperature_metrics=unsupported`가 표시된다.

```text
node_thermal_temperature_celsius
```

## BOT Prometheus 등록

Linux 대상:

```yaml
- targets:
    - gen1:9100
  labels:
    node: gen1
    role: hypervisor
    alerting: enabled
```

Windows 대상:

```yaml
- targets:
    - windows-host:9182
  labels:
    node: windows-host
    role: windows
    alerting: pending
```

macOS 대상:

```yaml
- targets:
    - mac:9100
  labels:
    node: mac
    role: macos
    alerting: pending
```

설정 반영 전 `promtool check config`를 실행해야 한다. 외부 인터넷에는
9100 또는 9182 포트를 공개하지 않는다.
