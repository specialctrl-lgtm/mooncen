from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

ENV_NAMES = (
    "MOONCEN_BUG_REPORT_TO",
    "MOONCEN_BUG_REPORT_FROM",
    "MOONCEN_SMTP_HOST",
    "MOONCEN_SMTP_PORT",
    "MOONCEN_SMTP_USERNAME",
    "MOONCEN_SMTP_PASSWORD",
    "MOONCEN_SMTP_SECURITY",
)

PARAMETERS = (
    "BugReportTo",
    "BugReportFrom",
    "SmtpHost",
    "SmtpPort",
    "SmtpUsername",
    "SmtpPassword",
    "SmtpSecurity",
)

LOCAL_VARIABLES = (
    "MoonCenBugReportTo",
    "MoonCenBugReportFrom",
    "MoonCenSmtpHost",
    "MoonCenSmtpPort",
    "MoonCenSmtpUsername",
    "MoonCenSmtpPassword",
    "MoonCenSmtpSecurity",
)


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_bug_report_mail_settings_are_documented_without_real_credentials() -> None:
    example = _text(".env.example")
    local_example = _text("deploy.local.example.ps1")

    for name in ENV_NAMES:
        assert f"{name}=" in example
    assert "MOONCEN_BUG_REPORT_TO=\n" in example
    assert "MOONCEN_BUG_REPORT_FROM=\n" in example
    assert "MOONCEN_SMTP_PASSWORD=\n" in example
    assert "MOONCEN_SMTP_PORT=587" in example
    assert "MOONCEN_SMTP_SECURITY=starttls" in example

    for name in LOCAL_VARIABLES:
        assert f"${name} =" in local_example
    assert '$MoonCenBugReportTo = ""' in local_example
    assert '$MoonCenBugReportFrom = ""' in local_example
    assert '$MoonCenSmtpPassword = ""' in local_example


def test_powershell_deploy_chain_forwards_every_mail_setting() -> None:
    orchestrator = _text("deploy_mooncen.ps1")
    wrapper = _text("deploy_ubuntu.ps1")
    deploy = _text("deploy/ubuntu/deploy_from_windows.ps1")

    for parameter in PARAMETERS:
        assert f"[string]${parameter}" in wrapper
        assert f"[string]${parameter}" in deploy
        assert f"-{parameter} ${parameter}" in wrapper

    for local_name in LOCAL_VARIABLES:
        assert f'Get-ConfigValue "{local_name}"' in orchestrator

    orchestrator_values = (
        "bugReportTo",
        "bugReportFrom",
        "smtpHost",
        "smtpPort",
        "smtpUsername",
        "smtpPassword",
        "smtpSecurity",
    )
    for parameter, value in zip(PARAMETERS, orchestrator_values, strict=True):
        assert orchestrator.count(f"-{parameter} ${value}") == 2

    for source in (orchestrator, deploy):
        assert "MOONCEN_SMTP_PASSWORD" in source
        assert "-SmtpPassword" in source
        assert "<redacted>" in source


def test_remote_deploy_preserves_and_base64_transports_mail_settings() -> None:
    deploy = _text("deploy/ubuntu/deploy_from_windows.ps1")
    value_names = (
        "BugReportTo",
        "BugReportFrom",
        "SmtpHost",
        "SmtpPort",
        "SmtpUsername",
        "SmtpPassword",
        "SmtpSecurity",
    )
    b64_names = (
        "bugReportToB64",
        "bugReportFromB64",
        "smtpHostB64",
        "smtpPortB64",
        "smtpUsernameB64",
        "smtpPasswordB64",
        "smtpSecurityB64",
    )

    for env_name, value_name, b64_name in zip(ENV_NAMES, value_names, b64_names, strict=True):
        assert f'Get-RemoteEnvValue "{env_name}"' in deploy
        assert f"${b64_name} = ConvertTo-Base64Utf8 ${value_name}" in deploy
        assert f"export {env_name}=\"`$(printf '%s' '${b64_name}' | base64 -d)\"" in deploy

    assert 'if (-not $SmtpPort) {\n    $SmtpPort = "587"' in deploy
    assert 'if (-not $SmtpSecurity) {\n    $SmtpSecurity = "starttls"' in deploy


def test_full_and_split_installs_keep_mail_settings_api_only() -> None:
    setup = _text("deploy/ubuntu/setup_project.sh")
    split = _text("deploy/ubuntu/setup_split_web.sh")

    api_block = setup.split('install_service_env api.env "$API_OS_USER" <<ENV', 1)[1].split("\nENV\n", 1)[0]
    frontend_block = setup.split('install_service_env frontend.env "$FRONTEND_OS_USER" <<ENV', 1)[1].split(
        "\nENV\n", 1
    )[0]
    split_api_block = split.split('cat >"$CONFIG_DIR/api.env" <<EOF', 1)[1].split("\nEOF\n", 1)[0]
    split_frontend_block = split.split('cat >"$CONFIG_DIR/frontend.env" <<\'EOF\'', 1)[1].split("\nEOF\n", 1)[0]

    for name in ENV_NAMES:
        assert f'{name}="${{{name}:-' in setup
        assert f"-u {name}" in setup
        assert f'write_deploy_secret_pair {name} "${name}"' in setup
        assert f"{name}=${{{name}}}" in api_block

        assert f'{name}="$(read_env_value {name} "$source_api_env")"' in split
        assert f"{name}=${name}" in split_api_block

        assert name not in frontend_block
        assert name not in split_frontend_block

    assert 'validate_port MOONCEN_SMTP_PORT "$MOONCEN_SMTP_PORT"' in setup
    assert "starttls|ssl|none" in setup
    assert "starttls|ssl|none" in split

