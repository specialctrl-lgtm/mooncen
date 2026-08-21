from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_ubuntu_installer_verifies_node_chrome_and_driver_before_install() -> None:
    script = _text("deploy/ubuntu/install_system_packages.sh")

    assert not re.search(r"curl\b[^\n|]*\|\s*(?:sudo\s+)?(?:-E\s+)?bash", script)
    assert 'NODE_VERSION="${NODE_VERSION:-24.18.0}"' in script
    assert "NODE_RELEASE_KEYS_COMMIT=\"890d535527789c9ebccdccdafd708f60dbd56786\"" in script
    assert "NODE_RELEASE_KEYRING_SHA256=\"6030d4e0cd53330acf2ab68acd455b7ca98bb5d5975376f0b7c0892308ba2d57\"" in script
    assert "https://nodejs.org/dist/v${NODE_VERSION}" in script
    assert "gpgv --keyring" in script
    assert "sha256sum --check --strict" in script

    assert 'CHROME_FOR_TESTING_VERSION="150.0.7871.115"' in script
    assert "1be2db033133c5e2dd1a4e8664bf67b19a61bcf6ed28d2b00f433b3f0b4f9585" in script
    assert "6ac3919edd107ca13d08cccc118dc83821877e504014233f171bbd94cb01a80e" in script
    assert "chrome-for-testing-public/${CHROME_FOR_TESTING_VERSION}/linux64" in script
    assert 'download_https "$base_url/chrome-linux64.zip"' in script
    assert 'download_https "$base_url/chromedriver-linux64.zip"' in script
    assert 'sudo apt-get satisfy -y --no-install-recommends "${chrome_dependencies[@]}"' in script
    assert 'sudo install -d -o root -g root -m 0755 /opt/chrome-for-testing' in script
    assert 'sudo ln -sfn "$install_dir/chrome-linux64/chrome" /usr/local/bin/mooncen-chrome' in script
    assert (
        'sudo ln -sfn "$install_dir/chromedriver-linux64/chromedriver" '
        "/usr/local/bin/mooncen-chromedriver"
    ) in script
    assert "ChromeDriver $CHROME_FOR_TESTING_VERSION" in script
    assert "profile mooncen-chrome-for-testing /opt/chrome-for-testing/*/chrome-linux64/chrome" in script
    assert "  userns," in script
    assert "apparmor_parser --replace /etc/apparmor.d/mooncen-chrome-for-testing" in script
    assert "A distro-verified Chromium and matching ChromeDriver are required" in script
    assert "Chromium and ChromeDriver versions do not match" in script
    assert "reconcile_installed_browser()" in script
    assert "profile mooncen-chromium-arm64 /snap/chromium/*/usr/lib/chromium-browser/chrome" in script
    assert "apparmor_parser --replace /etc/apparmor.d/mooncen-chromium-arm64" in script
    for required_package in ("iproute2", "openssh-client", "openssl", "rsync"):
        assert (f"  {required_package} " + "\\") in script
    assert script.index(
        '"/snap/chromium/current/usr/lib/chromium-browser/chrome|/snap/chromium/current/usr/lib/chromium-browser/chromedriver"'
    ) < script.index('"${wrapper_chrome}|${wrapper_driver}"')
    assert 'candidate_chrome="${pair%%|*}"' in script
    assert 'candidate_driver="${pair#*|}"' in script

    driver = _text("Crawler/selenium_driver.py")
    assert 'DEFAULT_CHROME_BINARY = "/usr/local/bin/mooncen-chrome"' in driver
    assert 'DEFAULT_CHROMEDRIVER = "/usr/local/bin/mooncen-chromedriver"' in driver
    assert "_required_root_executable" in driver
    assert "protected_paths = {resolved, *resolved.parents, *candidate.parents}" in driver
    assert "_reject_sandbox_disabling_arguments(options)" in driver
    assert "browser_environment = _browser_service_environment()" in driver
    assert "Service(executable_path=chromedriver, env=browser_environment)" in driver
    assert "webdriver.Chrome(service=service, options=options)" in driver
    assert "webdriver.Chrome(options=options)" not in driver


def test_ubuntu_installer_pins_and_verifies_cloudflared() -> None:
    script = _text("deploy/ubuntu/install_system_packages.sh")

    assert 'CLOUDFLARED_VERSION="2026.6.0"' in script
    assert "releases/download/${CLOUDFLARED_VERSION}/${asset}" in script
    for checksum in (
        "08d27c4c5d3ed73ee3e98ef2ddceb4ad09fd4cfc28e243565a189538e8ccd706",
        "8482ebf1e74a2a4a1a9f1e090e17e3de08423f94100ece6789287cb26fb9480f",
        "7d854dedec8fc043554d468a29abe1217890b670a00fd29898c0fc39ef1e071c",
        "dd6a63c418f87dfd51596aac00cf9613cd633aa10282faef1f46afdce813f476",
    ):
        assert checksum in script
    assert "sha256sum --check --strict" in script
    assert 'sudo ln -sfn "$install_path" /usr/bin/cloudflared' in script
    assert 'grep -Fq "cloudflared version ${CLOUDFLARED_VERSION}"' in script
    assert "cloudflared tunnel run --help" in script
    assert "grep -F -- '--token-file' >/dev/null" in script
    assert "grep -q -- '--token-file'" not in script
    assert "install_verified_cloudflared" in script


def test_remote_operations_require_pretrusted_ssh_host_keys() -> None:
    paths = (
        "deploy/ubuntu/deploy_from_windows.ps1",
        "deploy_mooncen.ps1",
        "deploy/ha/oracle_cloud_ssh.ps1",
        "deploy/monitoring/install_exporters.ps1",
    )
    for path in paths:
        source = _text(path)
        assert "StrictHostKeyChecking=accept-new" not in source, path
        assert "StrictHostKeyChecking=yes" in source, path
        assert "UpdateHostKeys=no" in source, path


def test_backup_ssh_port_is_validated_and_propagated_through_windows_deploy() -> None:
    deploy = _text("deploy/ubuntu/deploy_from_windows.ps1")
    wrapper = _text("deploy_ubuntu.ps1")
    orchestrator = _text("deploy_mooncen.ps1")
    example = _text("deploy.local.example.ps1")

    assert '[string]$BackupPort = ""' in deploy
    assert 'Get-RemoteEnvValue "BACKUP_PORT"' in deploy
    assert "[int64]$BackupPort -lt 1" in deploy
    assert "[int64]$BackupPort -gt 65535" in deploy
    assert 'export BACKUP_PORT="`$(printf' in deploy
    assert "-BackupPort $BackupPort" in wrapper
    assert 'Get-ConfigValue "MoonCenBackupPort"' in orchestrator
    assert orchestrator.count("-BackupPort $backupPort") == 2
    assert '$MoonCenBackupPort = ""' in example


def test_secret_scan_allowlist_is_narrow_and_emart_key_is_not_embedded() -> None:
    policy = _text(".gitleaks.toml")
    emart = _text("Crawler/Crawler_Emart.py")
    workflow = _text(".github/workflows/ci.yml")

    assert "useDefault = true" in policy
    assert 'condition = "AND"' in policy
    assert "deploy/ubuntu/install_system_packages" in policy
    assert "890d535527789c9ebccdccdafd708f60dbd56786" in policy
    assert "Crawler/municipal_gyeongnam_changnyeong" in policy
    assert "MUNI_IR_633F4DEC9CBE" in policy
    assert "Crawler/.*" not in policy
    assert 'regexTarget = "secret"' in policy
    assert 'os.getenv("EMART_GRAPHQL_API_KEY", "").strip()' in emart
    assert not re.search(r'"da2-[a-z0-9]{20,}"', emart)
    assert "--config .gitleaks.toml" in workflow


def test_secret_scan_binary_and_default_rule_canary_fail_closed() -> None:
    workflow_source = _text(".github/workflows/ci.yml")
    workflow = yaml.safe_load(workflow_source)
    steps = workflow["jobs"]["secret-scan"]["steps"]
    named_steps = {step["name"]: step for step in steps}

    install = named_steps["Install verified Gitleaks binary"]["run"]
    assert "v8.30.0/gitleaks_8.30.0_linux_x64.tar.gz" in install
    assert "79a3ab579b53f71efd634f3aaf7e04a0fa0cf206b7ed434638d1547a2470a66e" in install
    assert "sha256sum --check --strict" in install
    assert "v8.30.1" not in workflow_source

    canary = named_steps["Prove Gitleaks default rules detect a synthetic secret"]["run"]
    assert named_steps["Prove Gitleaks default rules detect a synthetic secret"]["shell"] == "bash"
    assert 'mktemp --directory "$RUNNER_TEMP/gitleaks-canary.XXXXXX"' in canary
    assert "\\x67\\x68\\x70\\x5f" in canary
    assert '"$RUNNER_TEMP/gitleaks" dir' in canary
    assert "--config .gitleaks.toml" in canary
    assert "canary_status=$?" in canary
    assert '[[ "$canary_status" -ne 1 ]]' in canary
    assert "exit 1" in canary
    assert "--pipe" not in canary
    assert not re.search(r"ghp_[0-9A-Za-z]{36}", workflow_source)

    step_names = [step["name"] for step in steps]
    assert step_names.index("Install verified Gitleaks binary") < step_names.index(
        "Prove Gitleaks default rules detect a synthetic secret"
    ) < step_names.index("Scan Git history and checked-out files")


def test_windows_exporter_is_pinned_verified_and_private() -> None:
    script = _text("deploy/monitoring/install_windows_exporter.ps1")

    assert '[string]$Version = "0.31.6"' in script
    assert "767324dc7ea8e6b8b99f610e2fb9f36d029c8f673a94b3d9f5f2c3c579be0b6d" in script
    assert "A5A9E97BFAEB629D755EA507FED51073BA605D78" in script
    assert "releases/latest" not in script
    assert "Get-FileHash -Path $tempPath -Algorithm SHA256" in script
    assert "Get-AuthenticodeSignature -FilePath $tempPath" in script
    assert "SignatureType -ne \"Authenticode\"" in script

    assert "Get-TailscaleIPv4Address" in script
    assert "LISTEN_ADDR=$ListenAddress" in script
    assert "REMOTE_ADDR=" in script
    assert '[string]$AllowedRemoteAddress = "100.64.0.0/10"' in script
    assert "0.0.0.0/0" in script
    assert "New-NetFirewallRule" in script
    assert "-RemoteAddress $allowedAddresses" in script


def test_monitoring_images_are_versioned_and_digest_pinned() -> None:
    compose = yaml.safe_load(_text("deploy/monitoring/docker-compose.yml"))
    expected = {
        "prometheus": (
            "prom/prometheus:v3.13.0@"
            "sha256:c6b27ea434f8389bfe233fbc7be381cf50587c286e871bc842008f5a1b1908a7"
        ),
        "grafana": (
            "grafana/grafana:13.1.0@"
            "sha256:121a7a9ece6dc10b969f1f96eed64b4f07dfac0d0b8abc070f7cb83bbde86f63"
        ),
        "uptime-kuma": (
            "louislam/uptime-kuma:2.4.0-rootless@"
            "sha256:a23b9d0029e6f1bc4a0fea0f3ee306d51f43216cd9f8115f8d84d146e9411e4c"
        ),
    }

    actual = {name: compose["services"][name]["image"] for name in expected}
    assert actual == expected
    assert all(":latest" not in image and image.rsplit(":", 1)[-1] != "1" for image in actual.values())
    assert "--web.enable-lifecycle" not in _text("deploy/monitoring/docker-compose.yml")
    for service in expected:
        assert compose["services"][service]["cap_drop"] == ["ALL"]
        assert compose["services"][service]["security_opt"] == ["no-new-privileges:true"]


def test_monitoring_installers_protect_recursive_target_and_secret_env() -> None:
    bash_installer = _text("deploy/monitoring/install_bot.sh")
    powershell_installer = _text("deploy/monitoring/install_bot.ps1")

    assert 'REMOTE_DIR cannot be a system directory' in bash_installer
    assert 'if [ -L .env ]' in bash_installer
    assert 'chmod 0600 .env' in bash_installer
    assert "RemoteDir must be a dedicated absolute directory below /opt or /srv" in powershell_installer
    assert "if [ -L .env ]" in powershell_installer
    assert "chmod 0600 .env" in powershell_installer


def test_monitoring_services_bind_only_to_explicit_private_address() -> None:
    source = _text("deploy/monitoring/docker-compose.yml")
    linux_installer = _text("deploy/monitoring/install_bot.sh")
    windows_installer = _text("deploy/monitoring/install_bot.ps1")

    assert "--web.listen-address=${MONITOR_BIND_ADDR:?" in source
    assert "--web.listen-address=127.0.0.1" not in source
    assert "GF_SERVER_HTTP_ADDR: ${MONITOR_BIND_ADDR:?" in source
    assert "UPTIME_KUMA_HOST: ${MONITOR_BIND_ADDR:?" in source
    assert 'MONITOR_BIND_ADDR="${MONITOR_BIND_ADDR:-}"' in linux_installer
    assert "detect_tailscale_address" in linux_installer
    assert "MONITOR_BIND_ADDR must be an RFC1918 or Tailscale IPv4 address" in linux_installer
    assert '[string]$BindAddress = ""' in windows_installer
    assert "BindAddress must be an RFC1918 or Tailscale IPv4 address" in windows_installer
    assert "0.0.0.0" not in source
    assert "- bot:9090" in _text("deploy/monitoring/prometheus/prometheus.yml")
    assert "url: http://${MONITOR_BIND_ADDR}:9090" in _text(
        "deploy/monitoring/grafana/provisioning/datasources/prometheus.yml"
    )


def test_linux_exporter_rejects_wildcard_listener() -> None:
    script = _text("deploy/monitoring/install_linux_exporter.sh")

    assert 'LISTEN_ADDRESS="${LISTEN_ADDRESS:-}"' in script
    assert "tailscale ip -4" in script
    assert "address show dev tailscale0" in script
    assert "[[ \"$LISTEN_ADDRESS\" == 0.0.0.0:* ]]" in script
    assert 'curl -fsS "http://${listen_ip}:${listen_port}/metrics"' in script
    assert "0.0.0.0:9100" not in script
    assert "- bot:9100" in _text("deploy/monitoring/prometheus/prometheus.yml")


@pytest.mark.parametrize(
    "relative",
    [
        "deploy/ubuntu/install_system_packages.sh",
        "deploy/monitoring/install_linux_exporter.sh",
    ],
)
def test_modified_bash_installers_parse(relative: str) -> None:
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash is not available")
    result = subprocess.run(
        [bash, "-n", str(ROOT / relative)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_windows_exporter_installer_parses_in_powershell() -> None:
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if not powershell:
        pytest.skip("PowerShell is not available")
    path = ROOT / "deploy/monitoring/install_windows_exporter.ps1"
    command = (
        "$tokens=$null; $errors=$null; "
        f"[void][System.Management.Automation.Language.Parser]::ParseFile('{path}', "
        "[ref]$tokens, [ref]$errors); "
        "if ($errors.Count) { $errors | ForEach-Object { Write-Error $_ }; exit 1 }"
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
