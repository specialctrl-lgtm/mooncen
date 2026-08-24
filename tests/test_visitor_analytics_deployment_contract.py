from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

ENV_NAMES = (
    "OPS_CLOUDFLARE_ANALYTICS_ZONE_ID",
    "OPS_CLOUDFLARE_ANALYTICS_TOKEN",
)
PARAMETERS = (
    "OpsCloudflareAnalyticsZoneId",
    "OpsCloudflareAnalyticsToken",
)
LOCAL_VARIABLES = (
    "MoonCenOpsCloudflareAnalyticsZoneId",
    "MoonCenOpsCloudflareAnalyticsToken",
)


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_cloudflare_analytics_examples_contain_no_credentials() -> None:
    example = _text(".env.example")
    local_example = _text("deploy.local.example.ps1")
    normalized_local_example = " ".join(line.lstrip("# ") for line in local_example.splitlines())

    for name in ENV_NAMES:
        assert f"{name}=\n" in example
    for name in LOCAL_VARIABLES:
        assert f'${name} = ""' in local_example
    assert "GraphQL Analytics token guide" in normalized_local_example
    assert "read-only analytics access" in normalized_local_example
    assert "VITE_*" in local_example


def test_deploy_chain_preserves_optional_server_side_analytics_settings() -> None:
    orchestrator = _text("deploy_mooncen.ps1")
    wrapper = _text("deploy_ubuntu.ps1")
    deploy = _text("deploy/ubuntu/deploy_from_windows.ps1")

    for parameter in PARAMETERS:
        assert f"[string]${parameter}" in wrapper
        assert f"[string]${parameter}" in deploy
        assert f"-{parameter} ${parameter}" in wrapper
    for name in LOCAL_VARIABLES:
        assert f'Get-ConfigValue "{name}"' in orchestrator

    assert orchestrator.count("-OpsCloudflareAnalyticsZoneId $opsCloudflareAnalyticsZoneId") == 2
    assert orchestrator.count("-OpsCloudflareAnalyticsToken $opsCloudflareAnalyticsToken") == 2
    for source in (orchestrator, deploy):
        assert "-OpsCloudflareAnalyticsToken" in source
        assert "OPS_CLOUDFLARE_ANALYTICS_TOKEN" in source
        assert "<redacted>" in source


def test_remote_deploy_reuses_validates_and_base64_transports_analytics_settings() -> None:
    deploy = _text("deploy/ubuntu/deploy_from_windows.ps1")

    expected = (
        (
            "OPS_CLOUDFLARE_ANALYTICS_ZONE_ID",
            "OpsCloudflareAnalyticsZoneId",
            "opsCloudflareAnalyticsZoneIdB64",
        ),
        (
            "OPS_CLOUDFLARE_ANALYTICS_TOKEN",
            "OpsCloudflareAnalyticsToken",
            "opsCloudflareAnalyticsTokenB64",
        ),
    )
    for env_name, value_name, b64_name in expected:
        assert f'Get-RemoteEnvValue "{env_name}"' in deploy
        assert f"${b64_name} = ConvertTo-Base64Utf8 ${value_name}" in deploy
        assert f"export {env_name}=\"`$(printf '%s' '${b64_name}' | base64 -d)\"" in deploy

    assert "zone id and token must be configured together" in deploy
    assert "^[0-9a-f]{32}$" in deploy


def test_full_and_split_web_installs_keep_analytics_credentials_api_only() -> None:
    setup = _text("deploy/ubuntu/setup_project.sh")
    split = _text("deploy/ubuntu/setup_split_web.sh")

    api_block = setup.split('install_service_env api.env "$API_OS_USER" <<ENV', 1)[1].split("\nENV\n", 1)[0]
    frontend_block = setup.split('install_service_env frontend.env "$FRONTEND_OS_USER" <<ENV', 1)[1].split(
        "\nENV\n", 1
    )[0]
    split_api_block = split.split('cat >"$CONFIG_DIR/api.env" <<EOF', 1)[1].split("\nEOF\n", 1)[0]
    split_frontend_block = split.split('cat >"$CONFIG_DIR/frontend.env" <<\'EOF\'', 1)[1].split(
        "\nEOF\n", 1
    )[0]

    for name in ENV_NAMES:
        assert f'{name}="${{{name}:-}}"' in setup
        assert f"-u {name}" in setup
        assert f'write_deploy_secret_pair {name} "${name}"' in setup
        assert f"{name}=${{{name}}}" in api_block
        assert f'{name}="$(read_env_value {name} "$source_api_env")"' in split
        assert f"{name}=${name}" in split_api_block
        assert name not in frontend_block
        assert name not in split_frontend_block
        assert f"-u {name}" in split


def test_local_cloud_ops_reads_protected_values_and_injects_only_the_api() -> None:
    launcher = _text("start_ops_console.ps1")

    for name in ENV_NAMES:
        assert f"^{name}=" in launcher
        assert f'$apiEnvironment["{name}"]' in launcher

    assert "Get-CloudflareAnalyticsEnvironment $SshExecutable" in launcher
    assert "SetEnvironmentVariable($analyticsEnvironmentName, $null, \"Process\")" in launcher
    assert launcher.count("$nonApiAnalyticsEnvironment") >= 4
    assert "zone_count" in launcher and "token_count" in launcher
    assert "PAIR:" in launcher and "ABSENT" in launcher
    assert "malformed or unreadable" in launcher
    assert '"`$HOME/.config/mooncen/deploy-secrets.env"' in launcher
    assert '"`$mode" = 600' in launcher
    worker_block = launcher.split("Worker = @{", 1)[1].split("\n        }", 1)[0]
    for name in ENV_NAMES:
        assert f'{name} = ""' in worker_block
        assert f'$apiEnvironment["{name}"]' in launcher


def test_operator_docs_define_metric_semantics_and_fail_closed_state() -> None:
    ops_doc = _text("docs/ops-console.md")
    autostart_doc = _text("docs/development-autostart.md")
    normalized_ops_doc = " ".join(ops_doc.split())

    for name in ENV_NAMES:
        assert name in ops_doc
        assert name in autostart_doc
    assert "not unique people" in normalized_ops_doc
    assert "www.mooncen.kr" in normalized_ops_doc
    assert "displayed as estimates" in normalized_ops_doc
    assert "is not labelled as page views" in normalized_ops_doc
    assert "집계 불가" in ops_doc
    assert "partial or malformed pair stops startup" in autostart_doc
