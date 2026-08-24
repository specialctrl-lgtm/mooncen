from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_post_deploy_ollama_check_uses_the_exact_role_scoped_helper() -> None:
    deploy = _text("deploy/ubuntu/deploy_from_windows.ps1")
    tail = deploy.split('Write-Host "Checking Ollama model..."', 1)[1]
    first_command = next(line.strip() for line in tail.splitlines() if line.strip())

    assert first_command == "try {"
    assert 'Invoke-RemoteTty "sudo -n /usr/local/libexec/mooncen-ops-service ollama-test"' in tail
    assert "The web deployment remains active" in tail
    assert "if (-not $SkipWorkers -and -not $Standby)" in deploy
    assert "mooncen-ollama-check.sh" not in deploy
    assert ". ./.env" not in deploy


def test_node_metrics_reads_only_primary_endpoint_from_applier_env() -> None:
    metrics = _text("deploy/monitoring/mooncen_node_metrics.sh")
    block = metrics.split("cloud_db_ready() {", 1)[1].split("\n}\n", 1)[0]

    assert "local applier_env=/etc/mooncen/applier.env" in block
    assert 'read_kv PRIMARY_DB_HOST "$applier_env"' in block
    assert 'read_kv PRIMARY_DB_PORT "$applier_env"' in block
    assert 'pg_isready -h "$host" -p "$port" -t 3' in block
    assert '[ -L "$applier_env" ]' in block
    assert 'env_owner" != root' in block
    assert "8#$env_mode & 8#022" in block
    assert "$APP_DIR/.env" not in block
    assert "set -a" not in block
    assert "PASSWORD" not in block
    assert "source " not in block


def test_applier_service_env_provides_the_monitored_primary_endpoint() -> None:
    setup = _text("deploy/ubuntu/setup_project.sh")
    applier = setup.split('install_service_env applier.env "$APPLIER_OS_USER" <<ENV', 1)[1].split("\nENV\n", 1)[0]

    assert "PRIMARY_DB_HOST=${PRIMARY_DB_HOST_EFFECTIVE}" in applier
    assert "PRIMARY_DB_PORT=${PRIMARY_DB_PORT:-5432}" in applier


def test_ops_login_credentials_are_preserved_and_scoped_to_the_api() -> None:
    orchestrator = _text("deploy_mooncen.ps1")
    wrapper = _text("deploy_ubuntu.ps1")
    deploy = _text("deploy/ubuntu/deploy_from_windows.ps1")
    setup = _text("deploy/ubuntu/setup_project.sh")
    example = _text("deploy.local.example.ps1")

    assert 'Get-ConfigValue "MoonCenOpsLoginId" $env:MOONCEN_OPS_LOGIN_ID' in orchestrator
    assert 'Get-ConfigValue "MoonCenOpsPasswordHash" $env:MOONCEN_OPS_PASSWORD_HASH' in orchestrator
    assert orchestrator.count("-OpsLoginId $opsLoginId") == 2
    assert orchestrator.count("-OpsPasswordHash $opsPasswordHash") == 2
    assert '[string]$OpsLoginId = ""' in wrapper
    assert '[string]$OpsPasswordHash = ""' in wrapper
    assert "-OpsLoginId $OpsLoginId" in wrapper
    assert "-OpsPasswordHash $OpsPasswordHash" in wrapper

    assert '[string]$OpsLoginId = ""' in deploy
    assert '[string]$OpsPasswordHash = ""' in deploy
    assert 'Get-RemoteEnvValue "MOONCEN_OPS_LOGIN_ID"' in deploy
    assert 'Get-RemoteEnvValue "MOONCEN_OPS_PASSWORD_HASH"' in deploy
    assert "/etc/mooncen/api.env" in deploy
    assert "$opsLoginIdB64 = ConvertTo-Base64Utf8 $OpsLoginId" in deploy
    assert "$opsPasswordHashB64 = ConvertTo-Base64Utf8 $OpsPasswordHash" in deploy
    assert "export MOONCEN_OPS_LOGIN_ID=" in deploy
    assert "export MOONCEN_OPS_PASSWORD_HASH=" in deploy
    assert "OpsPasswordHash must be a supported PBKDF2-HMAC-SHA256 verifier" in deploy
    assert "MOONCEN_OPS_PASSWORD_HASH|KAKAO_MAPS_REST_API_KEY" in deploy
    assert "-OpsPasswordHash\\s+" in deploy

    assert '${MOONCEN_OPS_LOGIN_ID:?Set MOONCEN_OPS_LOGIN_ID' in setup
    assert '${MOONCEN_OPS_PASSWORD_HASH:?Set MOONCEN_OPS_PASSWORD_HASH' in setup
    assert '-u MOONCEN_OPS_LOGIN_ID' in setup
    assert '-u MOONCEN_OPS_PASSWORD_HASH' in setup
    assert 'write_deploy_secret_pair MOONCEN_OPS_LOGIN_ID "$MOONCEN_OPS_LOGIN_ID"' in setup
    assert 'write_deploy_secret_pair MOONCEN_OPS_PASSWORD_HASH "$MOONCEN_OPS_PASSWORD_HASH"' in setup

    api = setup.split('install_service_env api.env "$API_OS_USER" <<ENV', 1)[1].split("\nENV\n", 1)[0]
    assert "MOONCEN_OPS_SINGLE_ACCOUNT_ONLY=true" in api
    assert "MOONCEN_OPS_LOGIN_ID=${MOONCEN_OPS_LOGIN_ID}" in api
    assert "MOONCEN_OPS_PASSWORD_HASH=${MOONCEN_OPS_PASSWORD_HASH}" in api
    for marker in (
        'cat > "$APP_DIR/.env" <<ENV',
        'install_service_env frontend.env "$FRONTEND_OS_USER" <<ENV',
        'install_service_env crawler.env "$CRAWLER_OS_USER" <<ENV',
        'install_service_env ai.env "$AI_OS_USER" <<ENV',
        'install_service_env bot.env "$BOT_OS_USER" <<ENV',
        'install_service_env applier.env "$APPLIER_OS_USER" <<ENV',
        'install_service_env functional-test.env "$FUNCTIONAL_OS_USER" <<ENV',
    ):
        block = setup.split(marker, 1)[1].split("\nENV\n", 1)[0]
        assert "MOONCEN_OPS_PASSWORD_HASH" not in block

    assert '$MoonCenOpsLoginId = "opsadmin"' in example
    assert '$MoonCenOpsPasswordHash = ""' in example
