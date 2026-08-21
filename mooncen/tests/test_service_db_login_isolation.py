from __future__ import annotations

from pathlib import Path

from tools import ops_service_action


ROOT = Path(__file__).resolve().parents[1]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _heredoc(source: str, marker: str) -> str:
    return source.split(marker, 1)[1].split("\nENV\n", 1)[0]


def test_ai_and_functional_check_have_distinct_runtime_db_logins() -> None:
    setup = _text("deploy/ubuntu/setup_project.sh")
    ai = _heredoc(setup, 'install_service_env ai.env "$AI_OS_USER" <<ENV')
    functional = _heredoc(setup, 'install_service_env functional-test.env "$FUNCTIONAL_OS_USER" <<ENV')

    assert 'DB_AI_USER="${DB_AI_USER:-mooncen_ai_login}"' in setup
    assert 'DB_CHECK_USER="${DB_CHECK_USER:-mooncen_check_login}"' in setup
    assert "DB_RUNTIME_USER=${DB_AI_USER}" in ai
    assert "DB_RUNTIME_PASSWORD=${DB_AI_PASSWORD}" in ai
    assert "DB_APPLICATION_NAME=mooncen-ai" in ai
    assert "DB_CRAWLER_USER" not in ai
    assert "DB_CRAWLER_PASSWORD" not in ai
    assert "DB_RUNTIME_USER=${DB_CHECK_USER}" in functional
    assert "DB_RUNTIME_PASSWORD=${DB_CHECK_PASSWORD}" in functional
    assert "DB_APPLICATION_NAME=mooncen-functional-test" in functional
    assert "DB_BACKUP_USER" not in functional
    assert "DB_BACKUP_PASSWORD" not in functional


def test_setup_provisions_and_verifies_ai_and_check_login_contracts() -> None:
    setup = _text("deploy/ubuntu/setup_project.sh")

    for password in ("DB_AI_PASSWORD", "DB_CHECK_PASSWORD"):
        assert f"write_deploy_secret_pair {password}" in setup
        assert f'-u {password}' in setup
    for provision_variable in (
        "db_ai_user",
        "db_ai_password_b64",
        "db_check_user",
        "db_check_password_b64",
    ):
        assert f"\\set {provision_variable}" in setup

    assert "pg_has_role('${DB_AI_USER}', 'mooncen_ai', 'member')" in setup
    assert "pg_has_role('${DB_CHECK_USER}', 'mooncen_check', 'member')" in setup
    assert "has_column_privilege('${DB_AI_USER}', 'courses', 'ai_summary', 'UPDATE')" in setup
    assert "has_table_privilege('${DB_CHECK_USER}', 'courses', 'SELECT')" in setup
    assert "has_table_privilege('${DB_CHECK_USER}', 'branches', 'SELECT')" in setup
    assert "has_sequence_privilege('${DB_AI_USER}', sequence.oid, 'USAGE')" in setup
    assert "'public.ops_job_logs_id_seq'," in setup
    assert "class.relname <> 'ops_job_logs_id_seq'" in setup
    assert "has_sequence_privilege('${DB_CHECK_USER}', sequence.oid, 'USAGE')" in setup
    assert "AND 6 = (" in setup


def test_windows_deploy_chain_recovers_transports_and_redacts_new_passwords() -> None:
    example = _text("deploy.local.example.ps1")
    wrapper = _text("deploy_ubuntu.ps1")
    orchestrator = _text("deploy_mooncen.ps1")
    deploy = _text("deploy/ubuntu/deploy_from_windows.ps1")

    for pascal_name, env_name in (
        ("Ai", "DB_AI_PASSWORD"),
        ("Check", "DB_CHECK_PASSWORD"),
    ):
        assert f'$MoonCenDb{pascal_name}Password = ""' in example
        assert f'[string]$Db{pascal_name}Password = ""' in wrapper
        assert f"-Db{pascal_name}Password $Db{pascal_name}Password" in wrapper
        assert f'Get-ConfigValue "MoonCenDb{pascal_name}Password"' in orchestrator
        assert f'"{env_name}"' in orchestrator
        assert f"-Db{pascal_name}Password $db{pascal_name}Password" in orchestrator
        assert f'[string]$Db{pascal_name}Password = ""' in deploy
        assert f'Name = "{env_name}"' in deploy
        assert f"ConvertTo-Base64Utf8 $Db{pascal_name}Password" in deploy
        assert f"export {env_name}=" in deploy
        assert env_name in orchestrator.split("function Mask-SensitiveText", 1)[1].split(
            "function Get-DeployServerRegistry", 1
        )[0]
        assert env_name in deploy.split("function Mask-SensitiveText", 1)[1].split("function Invoke-Remote", 1)[0]


def test_kakao_rest_key_is_redacted_by_both_deployment_layers() -> None:
    orchestrator = _text("deploy_mooncen.ps1")
    deploy = _text("deploy/ubuntu/deploy_from_windows.ps1")

    assert "KAKAO_MAPS_REST_API_KEY" in orchestrator.split(
        "function Mask-SensitiveText", 1
    )[1].split("function Get-DeployServerRegistry", 1)[0]
    assert "KAKAO_MAPS_REST_API_KEY" in deploy.split(
        "function Mask-SensitiveText", 1
    )[1].split("function Invoke-Remote", 1)[0]


def test_ops_ai_actions_require_ai_runtime_credentials() -> None:
    expected = (
        "DB_HOST",
        "DB_NAME",
        "DB_RUNTIME_USER",
        "DB_RUNTIME_PASSWORD",
        "DB_APPLICATION_NAME",
    )
    for action in ("ai-reset", "ai-reset-full", "ai-quality"):
        assert ops_service_action._required_service_keys(action) == expected
    assert ops_service_action._required_service_keys("ollama-test") == ()
    assert ops_service_action._required_service_keys("db-summary") == (
        "DB_HOST",
        "DB_NAME",
        "DB_CRAWLER_USER",
        "DB_CRAWLER_PASSWORD",
    )
