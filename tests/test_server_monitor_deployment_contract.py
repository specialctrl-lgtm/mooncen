from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_NAME = "MOONCEN_SERVER_MONITOR_TOKEN"
PARAMETER = "ServerMonitorToken"
LOCAL_VARIABLE = "MoonCenServerMonitorToken"


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_examples_define_an_empty_distinct_server_monitor_credential() -> None:
    assert f"{ENV_NAME}=\n" in _text(".env.example")

    local_example = _text("deploy.local.example.ps1")
    assert f'${LOCAL_VARIABLE} = ""' in local_example
    assert "never reuse the" in local_example
    assert "APK token" in local_example


def test_full_deploy_chain_transports_and_redacts_server_monitor_token() -> None:
    orchestrator = _text("deploy_mooncen.ps1")
    wrapper = _text("deploy_ubuntu.ps1")
    deploy = _text("deploy/ubuntu/deploy_from_windows.ps1")

    assert f'Get-ConfigValue "{LOCAL_VARIABLE}"' in orchestrator
    assert orchestrator.count("-ServerMonitorToken $serverMonitorToken") == 2
    assert f"[string]${PARAMETER}" in wrapper
    assert f"-{PARAMETER} ${PARAMETER}" in wrapper
    assert f"[string]${PARAMETER}" in deploy
    assert f'Get-RemoteEnvValue "{ENV_NAME}"' in deploy
    assert "$serverMonitorTokenB64 = ConvertTo-Base64Utf8 $ServerMonitorToken" in deploy
    assert (
        f"export {ENV_NAME}=\"`$(printf '%s' '$serverMonitorTokenB64' | base64 -d)\""
        in deploy
    )

    for source in (orchestrator, deploy):
        assert ENV_NAME in source
        assert "-ServerMonitorToken" in source
        assert "<redacted>" in source


def test_installers_validate_and_keep_server_monitor_token_api_only() -> None:
    setup = _text("deploy/ubuntu/setup_project.sh")
    split = _text("deploy/ubuntu/setup_split_web.sh")

    api_block = setup.split('install_service_env api.env "$API_OS_USER" <<ENV', 1)[1].split(
        "\nENV\n", 1
    )[0]
    frontend_block = setup.split(
        'install_service_env frontend.env "$FRONTEND_OS_USER" <<ENV', 1
    )[1].split("\nENV\n", 1)[0]
    split_api_block = split.split('cat >"$CONFIG_DIR/api.env" <<EOF', 1)[1].split(
        "\nEOF\n", 1
    )[0]
    split_frontend_block = split.split(
        'cat >"$CONFIG_DIR/frontend.env" <<\'EOF\'', 1
    )[1].split("\nEOF\n", 1)[0]

    assert f'{ENV_NAME}="${{{ENV_NAME}:-}}"' in setup
    assert f"-u {ENV_NAME}" in setup
    assert f'write_deploy_secret_pair {ENV_NAME} "${ENV_NAME}"' in setup
    assert f"{ENV_NAME}=${{{ENV_NAME}}}" in api_block
    assert f'{ENV_NAME}="$(read_env_value {ENV_NAME} "$source_api_env")"' in split
    assert f"{ENV_NAME}=${ENV_NAME}" in split_api_block
    assert ENV_NAME not in frontend_block
    assert ENV_NAME not in split_frontend_block

    for source in (setup, split):
        assert "32" in source
        assert "256" in source
        assert "^[A-Za-z0-9_-]+$" in source

