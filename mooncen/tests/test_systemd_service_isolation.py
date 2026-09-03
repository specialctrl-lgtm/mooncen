from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SYSTEMD_DIR = ROOT / "deploy" / "ubuntu" / "systemd"
SETUP_PATH = ROOT / "deploy" / "ubuntu" / "setup_project.sh"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _heredoc(setup: str, marker: str) -> str:
    return setup.split(marker, 1)[1].split("\nENV\n", 1)[0]


def _find_bash() -> str | None:
    candidate = shutil.which("bash")
    if candidate:
        return candidate
    if os.name == "nt":
        for path in (
            Path("C:/Program Files/Git/bin/bash.exe"),
            Path("C:/Program Files/Git/usr/bin/bash.exe"),
        ):
            if path.exists():
                return str(path)
    return None


SERVICE_CONTRACT = {
    "mooncen-api.service": ("mooncen-api", "/etc/mooncen/api.env"),
    "mooncen-crawler-browser-smoke.service": ("mooncen-crawler", "/etc/mooncen/crawler.env"),
    "mooncen-crawler.service": ("mooncen-crawler", "/etc/mooncen/crawler.env"),
    "mooncen-crawler-once.service": ("mooncen-crawler", "/etc/mooncen/crawler.env"),
    "mooncen-branch-coordinates.service": ("mooncen-crawler", "/etc/mooncen/crawler.env"),
    "mooncen-ai-worker.service": ("mooncen-ai", "/etc/mooncen/ai.env"),
    "mooncen-frontend.service": ("mooncen-web", "/etc/mooncen/frontend.env"),
    "mooncen-ops-bot.service": ("mooncen-bot", "/etc/mooncen/bot.env"),
    "mooncen-staging-apply.service": ("mooncen-applier", "/etc/mooncen/applier.env"),
    "mooncen-staging-apply-dry-run.service": ("mooncen-applier", "/etc/mooncen/applier.env"),
    "mooncen-functional-test.service": ("mooncen-check", "/etc/mooncen/functional-test.env"),
}


def test_application_units_use_dedicated_accounts_and_environment_files():
    for filename, (account, env_file) in SERVICE_CONTRACT.items():
        unit = _text(SYSTEMD_DIR / filename)
        assert f"User={account}" in unit
        assert f"Group={account}" in unit
        assert f"EnvironmentFile={env_file}" in unit
        assert "/opt/mooncen/.env" not in unit

    accounts = {account for account, _ in SERVICE_CONTRACT.values()}
    assert "mooncen" not in accounts
    assert len(accounts) == 7


def test_crawler_oneshot_exposes_an_existing_worker_as_non_success():
    unit = _text(SYSTEMD_DIR / "mooncen-crawler-once.service")
    assert "Type=oneshot" in unit
    assert "SuccessExitStatus=" not in unit
    assert "TimeoutStartSec=9h" in unit
    assert "RuntimeMaxSec=" not in unit


def test_service_secret_scopes_are_minimal_and_frontend_has_no_secret():
    setup = _text(SETUP_PATH)
    public = _heredoc(setup, 'cat > "$APP_DIR/.env" <<ENV')
    api = _heredoc(setup, 'install_service_env api.env "$API_OS_USER" <<ENV')
    frontend = _heredoc(setup, 'install_service_env frontend.env "$FRONTEND_OS_USER" <<ENV')
    crawler = _heredoc(setup, 'install_service_env crawler.env "$CRAWLER_OS_USER" <<ENV')
    ai = _heredoc(setup, 'install_service_env ai.env "$AI_OS_USER" <<ENV')
    bot = _heredoc(setup, 'install_service_env bot.env "$BOT_OS_USER" <<ENV')
    applier = _heredoc(setup, 'install_service_env applier.env "$APPLIER_OS_USER" <<ENV')
    functional = _heredoc(setup, 'install_service_env functional-test.env "$FUNCTIONAL_OS_USER" <<ENV')

    sensitive_names = {
        "DB_API_PASSWORD",
        "DB_CRAWLER_PASSWORD",
        "PRIMARY_DB_PASSWORD",
        "AUTH_SECRET",
        "MOONCEN_OPS_PASSWORD_HASH",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "NAVER_OAUTH_CLIENT_SECRET",
        "KAKAO_MAPS_REST_API_KEY",
        "GOOGLE_MAPS_API_KEY",
        "MOONCEN_BOT_TOKEN",
    }
    assert not any(name in public for name in sensitive_names)

    frontend_keys = {line.split("=", 1)[0] for line in frontend.strip().splitlines() if "=" in line}
    assert frontend_keys == {"FRONTEND_HOST", "FRONTEND_PORT", "NODE_ENV"}

    assert "DB_API_PASSWORD=${DB_API_PASSWORD}" in api
    assert "AUTH_SECRET=${AUTH_SECRET}" in api
    assert "MOONCEN_OPS_SINGLE_ACCOUNT_ONLY=true" in api
    assert "MOONCEN_OPS_LOGIN_ID=${MOONCEN_OPS_LOGIN_ID}" in api
    assert "MOONCEN_OPS_PASSWORD_HASH=${MOONCEN_OPS_PASSWORD_HASH}" in api
    assert "GOOGLE_OAUTH_CLIENT_SECRET=${GOOGLE_OAUTH_CLIENT_SECRET}" in api
    assert "MOONCEN_ADMIN_EMAILS=${MOONCEN_ADMIN_EMAILS}" in api
    assert "MOONCEN_ADMIN_PROVIDER_IDS=${MOONCEN_ADMIN_PROVIDER_IDS}" in api
    assert "DB_CRAWLER_PASSWORD" not in api
    assert "MOONCEN_BOT_TOKEN" not in api
    assert "KAKAO_MAPS_REST_API_KEY" not in api
    assert "GOOGLE_MAPS_API_KEY" not in api

    assert "DB_CRAWLER_PASSWORD=${CRAWL_STAGING_DB_PASSWORD}" in crawler
    assert "DB_CRAWLER_USER=${CRAWL_STAGING_DB_USER}" in crawler
    assert "CRAWL_WRITE_MODE=staging" in crawler
    assert "DB_CRAWLER_PASSWORD=${DB_CRAWLER_PASSWORD}" not in crawler
    assert "KAKAO_MAPS_REST_API_KEY=${KAKAO_MAPS_REST_API_KEY}" in crawler
    assert "KAKAO_GEOCODE_MAX_REQUESTS_PER_RUN=1000" in crawler
    assert "GOOGLE_MAPS_API_KEY" not in crawler
    assert "AUTH_SECRET" not in crawler
    assert "OAUTH_CLIENT_SECRET" not in crawler
    assert "MOONCEN_BOT_TOKEN" not in crawler

    assert "DB_RUNTIME_USER=${DB_AI_USER}" in ai
    assert "DB_RUNTIME_PASSWORD=${DB_AI_PASSWORD}" in ai
    assert "DB_APPLICATION_NAME=mooncen-ai" in ai
    assert "DB_CRAWLER_PASSWORD" not in ai
    assert "OLLAMA_HOST=${OLLAMA_HOST}" in ai
    assert "AUTH_SECRET" not in ai
    assert "OAUTH_CLIENT_SECRET" not in ai
    assert "KAKAO_MAPS_REST_API_KEY" not in ai
    assert "GOOGLE_MAPS_API_KEY" not in ai
    assert "MOONCEN_BOT_TOKEN" not in ai

    assert "MOONCEN_BOT_TOKEN=${MOONCEN_BOT_TOKEN}" in bot
    assert "DB_" not in bot
    assert "AUTH_SECRET" not in bot
    assert "OAUTH_CLIENT_SECRET" not in bot
    assert "KAKAO_MAPS_REST_API_KEY" not in bot
    assert "GOOGLE_MAPS_API_KEY" not in bot

    assert "CRAWL_STAGING_DB_PASSWORD=${CRAWL_STAGING_DB_PASSWORD}" in applier
    assert "CRAWL_STAGING_DB_USER=${CRAWL_STAGING_DB_USER}" in applier
    assert "CRAWL_STAGING_DB_PASSWORD=${DB_CRAWLER_PASSWORD}" not in applier
    assert "PRIMARY_DB_PASSWORD=${DB_APPLIER_PASSWORD}" in applier
    assert "AUTH_SECRET" not in applier
    assert "OAUTH_CLIENT_SECRET" not in applier
    assert "KAKAO_MAPS_REST_API_KEY" not in applier
    assert "MOONCEN_BOT_TOKEN" not in applier

    assert "DB_RUNTIME_USER=${DB_CHECK_USER}" in functional
    assert "DB_RUNTIME_PASSWORD=${DB_CHECK_PASSWORD}" in functional
    assert "DB_APPLICATION_NAME=mooncen-functional-test" in functional
    assert "DB_BACKUP_PASSWORD" not in functional
    assert "MOONCEN_BOT_TOKEN=${MOONCEN_BOT_TOKEN}" in functional
    assert "MOONCEN_BOT_CHAT_ID=${MOONCEN_BOT_CHAT_ID}" in functional
    assert "AUTH_SECRET" not in functional
    assert "OAUTH_CLIENT_SECRET" not in functional
    assert "KAKAO_MAPS_REST_API_KEY" not in functional
    assert "GOOGLE_MAPS_API_KEY" not in functional


def test_setup_installs_root_owned_group_readable_service_envs():
    setup = _text(SETUP_PATH)
    assert "sudo install -o root -g \"$service_group\" -m 0640" in setup
    assert "sudo install -d -o root -g root -m 0751 \"$SERVICE_CONFIG_DIR\"" in setup
    assert "sudo useradd" in setup
    for account in {
        "mooncen-api",
        "mooncen-crawler",
        "mooncen-ai",
        "mooncen-web",
        "mooncen-bot",
        "mooncen-applier",
        "mooncen-check",
    }:
        assert account in setup


def test_application_units_enforce_sandbox_baseline():
    required = {
        "PrivateTmp=true",
        "ProtectSystem=strict",
        "ProtectHome=true",
        "ProtectKernelTunables=true",
        "ProtectKernelModules=true",
        "ProtectKernelLogs=true",
        "ProtectControlGroups=true",
        "RestrictSUIDSGID=true",
        "LockPersonality=true",
    }
    for filename in SERVICE_CONTRACT:
        unit = _text(SYSTEMD_DIR / filename)
        missing = required.difference(unit.splitlines())
        assert not missing, f"{filename} missing hardening directives: {sorted(missing)}"
        expected_families = (
            "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK"
            if filename == "mooncen-frontend.service"
            else "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6"
        )
        assert expected_families in unit
        expected_umask = "UMask=0027" if filename == "mooncen-functional-test.service" else "UMask=0077"
        assert expected_umask in unit
        if filename != "mooncen-ops-bot.service":
            assert "NoNewPrivileges=true" in unit
            assert "CapabilityBoundingSet=" in unit

    assert "ReadWritePaths=/opt/mooncen/logs" in _text(SYSTEMD_DIR / "mooncen-crawler.service")
    ai = _text(SYSTEMD_DIR / "mooncen-ai-worker.service")
    assert "StateDirectory=mooncen-ai" in ai
    assert "BindPaths=/var/lib/mooncen-ai:/opt/mooncen/logs" in ai
    assert "ReadWritePaths=/opt/mooncen/logs" not in ai
    frontend = _text(SYSTEMD_DIR / "mooncen-frontend.service")
    assert {
        line
        for line in frontend.splitlines()
        if line.startswith("ReadWritePaths=")
    } == set()
    assert "IPAddressDeny=any" in frontend
    assert "IPAddressAllow=localhost" in frontend
    bot = _text(SYSTEMD_DIR / "mooncen-ops-bot.service")
    assert "StateDirectory=mooncen-bot" in bot
    assert "/opt/mooncen/failover/failover.env" not in bot
    assert "NoNewPrivileges=false" in bot
    assert "CapabilityBoundingSet=CAP_SETUID CAP_SETGID CAP_AUDIT_WRITE" in bot


def test_bot_postgres_diagnostics_are_limited_to_fixed_readonly_queries():
    setup = _text(SETUP_PATH)
    bot = _text(SYSTEMD_DIR / "mooncen-ops-bot.service")

    assert "BOT_PSQL_HELPER_DIR=/usr/local/libexec/mooncen-bot" in setup
    assert 'BOT_PSQL_HELPER="$BOT_PSQL_HELPER_DIR/psql"' in setup
    assert 'if [ "$#" -ne 2 ] || [ "$1" != "-Atqc" ]' in setup
    assert "SELECT CASE WHEN pg_is_in_recovery() THEN 'standby' ELSE 'primary' END;" in setup
    assert "FROM pg_stat_wal_receiver;" in setup
    assert "SELECT status FROM pg_stat_wal_receiver LIMIT 1;" in setup
    assert "/usr/bin/env -i" in setup
    assert "/usr/bin/psql -X --no-password -d postgres -Atqc" in setup
    assert 'Defaults:mooncen-bot secure_path="/usr/local/libexec/mooncen-bot:' in setup
    assert "mooncen-bot ALL=(postgres) NOPASSWD: /usr/local/libexec/mooncen-bot/psql" in setup
    assert "sudo visudo -cf \"$BOT_SUDOERS_FILE\"" in setup
    assert "NOPASSWD: /usr/bin/psql" not in setup
    assert "Environment=PATH=/usr/local/libexec/mooncen-bot:" in bot


def test_functional_report_and_notification_contract_is_consistent():
    setup = _text(SETUP_PATH)
    unit = _text(SYSTEMD_DIR / "mooncen-functional-test.service")
    control = _text(ROOT / "deploy" / "ubuntu" / "mooncenctl.sh")

    assert "MOONCEN_FUNCTIONAL_TEST_REPORT_DIR=/var/lib/mooncen-check" in setup
    assert "StateDirectory=mooncen-check" in unit
    assert "--report-dir /var/lib/mooncen-check" in unit
    assert "ExecStopPost=/bin/chgrp -R mooncen /var/lib/mooncen-check" in unit
    assert 'local latest="/var/lib/mooncen-check/latest.json"' in control
    assert "logs/functional_tests/latest.json" not in control


def test_python_runtime_install_is_hash_locked_and_version_bounded():
    setup = _text(SETUP_PATH)
    assert '3.12|3.13)' in setup
    assert 'python3 -I -m venv --clear "$venv_stage"' in setup
    assert '"$venv_stage/bin/python" -I -m pip install --require-hashes -r "$APP_DIR/requirements.lock"' in setup
    assert 'mv -- "$venv_stage" "$APP_DIR/.venv"' in setup
    assert "pip install --upgrade" not in setup
    assert 'pip" install -r "$APP_DIR/requirements.txt"' not in setup


def test_dependency_install_and_frontend_build_do_not_receive_runtime_secrets():
    setup = _text(SETUP_PATH)
    sanitizer = setup.split("without_runtime_secrets() {", 1)[1].split("\n}", 1)[0]
    for name in (
        "DB_PASSWORD",
        "DB_API_PASSWORD",
        "DB_CRAWLER_PASSWORD",
        "DB_AI_PASSWORD",
        "DB_APPLIER_PASSWORD",
        "DB_BACKUP_PASSWORD",
        "DB_CHECK_PASSWORD",
        "AUTH_SECRET",
        "MOONCEN_OPS_LOGIN_ID",
        "MOONCEN_OPS_PASSWORD_HASH",
        "KAKAO_MAPS_REST_API_KEY",
        "GOOGLE_MAPS_API_KEY",
        "VITE_GOOGLE_MAPS_API_KEY",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "NAVER_OAUTH_CLIENT_SECRET",
        "MOONCEN_BOT_TOKEN",
        "MOONCEN_BOT_CHAT_ID",
    ):
        assert f"-u {name}" in sanitizer
    assert "without_runtime_secrets python3 -I -m venv" in setup
    assert '"$venv_stage/bin/python" -I -m pip install --require-hashes' in setup
    assert "without_runtime_secrets npm ci --ignore-scripts" in setup
    assert "without_runtime_secrets npm run build" in setup


def test_admin_allowlists_are_base64_forwarded_to_api_only():
    setup = _text(SETUP_PATH)
    deploy = _text(ROOT / "deploy" / "ubuntu" / "deploy_from_windows.ps1")
    wrapper = _text(ROOT / "deploy_ubuntu.ps1")
    orchestrator = _text(ROOT / "deploy_mooncen.ps1")
    example = _text(ROOT / "deploy.local.example.ps1")

    assert "MOONCEN_ADMIN_EMAILS" in setup
    assert "MOONCEN_ADMIN_PROVIDER_IDS" in setup
    assert "$adminEmailsB64 = ConvertTo-Base64Utf8 $AdminEmails" in deploy
    assert "$adminProviderIdsB64 = ConvertTo-Base64Utf8 $AdminProviderIds" in deploy
    assert "export MOONCEN_ADMIN_EMAILS=" in deploy
    assert "export MOONCEN_ADMIN_PROVIDER_IDS=" in deploy
    assert "-AdminEmails $AdminEmails" in wrapper
    assert "-AdminProviderIds $AdminProviderIds" in wrapper
    assert 'Get-ConfigValue "MoonCenAdminEmails"' in orchestrator
    assert 'Get-ConfigValue "MoonCenAdminProviderIds"' in orchestrator
    assert "$MoonCenAdminEmails" in example
    assert "$MoonCenAdminProviderIds" in example


def test_api_access_log_does_not_persist_query_strings():
    api = _text(SYSTEMD_DIR / "mooncen-api.service")
    assert "--no-access-log" in api


def test_production_cors_origins_are_https_only():
    setup = _text(SETUP_PATH)
    assert 'cors_origins="https://${DOMAIN}"' in setup
    assert 'cors_origins="https://${DOMAIN},http://${DOMAIN}"' not in setup
    assert ',http://${alias}' not in setup


def test_remote_database_tls_is_fail_closed_for_python_services():
    setup = _text(SETUP_PATH)
    assert 'if ! is_local_db_host "$PRIMARY_DB_HOST_EFFECTIVE" && [ -z "$DB_SSLROOTCERT_SOURCE" ]' in setup
    assert "DB_SSLROOTCERT must be an existing regular file, not a symlink" in setup
    assert 'sudo install -o root -g "$DB_TLS_GROUP" -m 0640' in setup

    for marker in (
        'install_service_env crawler.env "$CRAWLER_OS_USER" <<ENV',
        'install_service_env ai.env "$AI_OS_USER" <<ENV',
        'install_service_env applier.env "$APPLIER_OS_USER" <<ENV',
        'install_service_env functional-test.env "$FUNCTIONAL_OS_USER" <<ENV',
    ):
        block = _heredoc(setup, marker)
        assert "ENVIRONMENT=production" in block
        assert "DB_SSLROOTCERT=${SERVICE_DB_SSLROOTCERT}" in block

    deploy = _text(ROOT / "deploy" / "ubuntu" / "deploy_from_windows.ps1")
    assert "$dbSslRootCertB64 = ConvertTo-Base64Utf8 $DbSslRootCert" in deploy
    assert "export DB_SSLROOTCERT=" in deploy


def test_setup_sitemap_generation_uses_explicit_crawler_credentials_and_is_required():
    setup = _text(SETUP_PATH)
    command = setup.split('"$APP_DIR/tools/generate_frontend_sitemap.py"', 1)[0][-500:]
    assert "ENVIRONMENT=production" in command
    assert 'DB_CRAWLER_USER="$DB_CRAWLER_USER"' in command
    assert 'DB_CRAWLER_PASSWORD="$DB_CRAWLER_PASSWORD"' in command
    assert 'DB_SSLROOTCERT="$SERVICE_DB_SSLROOTCERT"' in command
    assert "|| true" not in setup.split('"$APP_DIR/tools/generate_frontend_sitemap.py"', 1)[1].split("\n", 1)[0]


def test_cloudflared_token_uses_exact_root_helper_and_hardened_service():
    helper = _text(ROOT / "deploy" / "ubuntu" / "cloudflared_token_helper.sh")
    control = _text(ROOT / "deploy" / "ubuntu" / "mooncenctl.sh")
    sudoers = _text(ROOT / "deploy" / "ubuntu" / "install_sudoers.sh")
    deploy = _text(ROOT / "deploy_mooncen.ps1")
    unit = _text(SYSTEMD_DIR / "cloudflared.service")

    assert "User=cloudflared" in unit
    assert "Group=cloudflared" in unit
    assert "LoadCredential=cloudflared-token:/etc/cloudflared/token" in unit
    assert "--token-file %d/cloudflared-token" in unit
    for directive in (
        "NoNewPrivileges=true",
        "ProtectSystem=strict",
        "ProtectHome=true",
        "PrivateTmp=true",
        "PrivateDevices=true",
        "CapabilityBoundingSet=",
    ):
        assert directive in unit

    assert 'sudo -n "$helper" install' in control
    assert 'token="${token#$\'\\xEF\\xBB\\xBF\'}"' in helper
    assert 'token="${token%$\'\\r\'}"' in helper
    assert "grep -F -- '--token-file' >/dev/null" in control
    assert "grep -q -- '--token-file'" not in control
    assert "sudo tee /etc/cloudflared/token" not in control
    assert "sudo tee /etc/systemd/system/cloudflared.service" not in control
    assert "token=\"${token%$'\\r'}\"" in helper
    assert "extra=\"${extra%$'\\r'}\"" in helper
    assert "install -d -o root -g \"$CLOUDFLARED_GROUP\" -m 0750" in helper
    assert "chmod 0640 \"$TOKEN_FILE\"" in helper
    assert "token must be provided through standard input" in helper
    assert "verify_hardened_unit" in helper
    assert "${CLOUDFLARED_HELPER} install, ${CLOUDFLARED_HELPER} read" in sudoers
    assert "sudo -n /usr/local/libexec/mooncen-cloudflared-token read" in deploy
    assert "sudo -n cat /etc/cloudflared/token" not in deploy
    assert "sudo -n python3" not in deploy
    assert "read | python3 -c" in deploy


def test_role_scoped_ops_helper_is_root_owned_and_exactly_allowlisted():
    helper = _text(ROOT / "deploy" / "ubuntu" / "ops_service_helper.sh")
    runner = _text(ROOT / "tools" / "ops_service_action.py")
    sudoers = _text(ROOT / "deploy" / "ubuntu" / "install_sudoers.sh")
    control = _text(ROOT / "deploy" / "ubuntu" / "mooncenctl.sh")

    assert "OPS_HELPER=/usr/local/libexec/mooncen-ops-service" in sudoers
    assert "OPS_RUNNER=/usr/local/libexec/mooncen-ops-service-action.py" in sudoers
    assert 'install -o root -g root -m 0755 "$OPS_HELPER_SOURCE" "$OPS_HELPER"' in sudoers
    assert 'install -o root -g root -m 0755 "$OPS_RUNNER_SOURCE" "$OPS_RUNNER"' in sudoers
    assert "Cmnd_Alias MOONCEN_ROLE_OPS" in sudoers
    for action in (
        "db-summary",
        "coordinate-summary",
        "coordinate-backfill",
        "crawler-config",
        "crawler-provider-summary",
        "sitemap",
        "ai-reset",
        "ai-reset-full",
        "ai-quality",
    ):
        assert f"${{OPS_HELPER}} {action}" in sudoers
        assert f"run_role_ops {action}" in control
    for privileged_action in (
        "replication-summary",
        "staging-promote-provider",
        "ai-worker-start",
        "ai-worker-stop",
        "restart-api",
        "restart-nginx",
        "logs-api",
        "logs-cloudflared",
        "failover-disable",
    ):
        assert f"${{OPS_HELPER}} {privileged_action}" in sudoers
    assert "${OPS_HELPER} *" not in sudoers
    assert "/usr/bin/journalctl -u mooncen-*.service *" not in sudoers
    assert 'if [ "$#" -ne 1 ]' in helper
    assert "/usr/sbin/runuser -u \"$service_user\"" in helper
    assert "/usr/bin/mktemp -d /tmp/mooncen-ops-runtime.XXXXXX" in helper
    assert '"HOME=$runtime_home"' in helper
    assert '"TMPDIR=$runtime_home"' in helper
    assert '"TMP=$runtime_home"' in helper
    assert '"TEMP=$runtime_home"' in helper
    assert '"XDG_CACHE_HOME=$runtime_home/.cache"' in helper
    assert '"XDG_RUNTIME_DIR=$runtime_home"' in helper
    assert '"PYTHONUNBUFFERED=1"' in helper
    assert "/usr/bin/rm -rf -- \"$runtime_home\"" in helper
    assert "ACTION_ACCOUNT_ENV" in runner
    assert "dotenv_values(env_path, interpolate=False)" in runner
    assert "MOONCEN_SITEMAP_OUTPUT" in runner
    assert "staging promotion requires a standby or crawler node" in helper
    assert "standby|crawler)" in helper
    assert "install -D -o mooncen-crawler -g mooncen -m 0640" in helper
    assert "sudo -u mooncen" not in control
    assert "sudo -u postgres" not in control


def test_deploy_postgres_role_check_uses_a_fixed_root_owned_helper():
    helper = _text(ROOT / "deploy" / "ubuntu" / "postgres_role_helper.sh")
    sudoers = _text(ROOT / "deploy" / "ubuntu" / "install_sudoers.sh")
    deploy = _text(ROOT / "deploy" / "ubuntu" / "deploy_from_windows.ps1")

    assert 'if [ "$#" -ne 0 ]' in helper
    assert "/usr/bin/env -i" in helper
    assert "PGHOST=/var/run/postgresql" in helper
    assert "SELECT CASE WHEN pg_is_in_recovery()" in helper
    assert "POSTGRES_ROLE_HELPER=/usr/local/libexec/mooncen-postgres-role" in sudoers
    assert 'install -o root -g root -m 0755 "$POSTGRES_ROLE_HELPER_SOURCE" "$POSTGRES_ROLE_HELPER"' in sudoers
    assert "${DEPLOY_USER} ALL=(postgres) NOPASSWD: ${POSTGRES_ROLE_HELPER}" in sudoers
    assert "NOPASSWD: /usr/bin/psql" not in sudoers
    assert "sudo -n -u postgres /usr/local/libexec/mooncen-postgres-role" in deploy


def test_mooncenctl_privileged_paths_use_only_exact_root_helpers():
    helper = _text(ROOT / "deploy" / "ubuntu" / "ops_service_helper.sh")
    sudoers = _text(ROOT / "deploy" / "ubuntu" / "install_sudoers.sh")
    control = _text(ROOT / "deploy" / "ubuntu" / "mooncenctl.sh")

    actions = (
        "start-all",
        "stop-all",
        "restart-all",
        "crawler-once",
        "functional-test",
        "logs-functional-test",
        "cloudflare-gate-enable",
        "cloudflare-gate-disable",
        "logs-cloudflare-gate",
        "role-guard-run",
        "logs-role-guard",
        "bot-start",
        "bot-stop",
        "logs-bot",
        "backup-once",
        "backup-test",
        "logs-backup",
        "staging-dry-run",
        "staging-apply",
        "logs-staging",
    )
    for action in actions:
        assert f"${{OPS_HELPER}} {action}" in sudoers
        assert action in helper
        assert action in control

    for forbidden in (
        "sudo systemctl",
        "sudo journalctl",
        "sudo ss",
        "sudo mkdir",
        "sudo rm",
        "| sudo tee",
    ):
        assert forbidden not in control
    assert "/usr/bin/systemctl status mooncen-*" not in sudoers
    assert "/usr/bin/journalctl" not in sudoers
    assert "${OPS_HELPER} *" not in sudoers


def test_setup_validates_host_derived_environment_values():
    setup = _text(SETUP_PATH)
    api_env = setup.split('install_service_env api.env "$API_OS_USER" <<ENV', 1)[1].split("\nENV\n", 1)[0]

    assert "validate_dns_hostname()" in setup
    assert 'validate_dns_hostname DOMAIN "$DOMAIN"' in setup
    assert 'validate_dns_hostname DOMAIN_ALIASES "$alias"' in setup
    assert "OAUTH_REDIRECT_URI must be an HTTPS URL" in setup
    assert "OAUTH_REDIRECT_URI host must match DOMAIN or one of its trusted aliases" in setup
    assert "DOMAIN DOMAIN_ALIASES OAUTH_REDIRECT_URI" in setup
    assert "MOONCEN_TRUSTED_HOSTS=${trusted_hosts}" in api_env


def test_backup_restore_uses_fixed_database_and_root_owned_scripts():
    setup = _text(SETUP_PATH)
    backup = _text(SYSTEMD_DIR / "mooncen-backup.service")
    restore = _text(SYSTEMD_DIR / "mooncen-backup-restore-test.service")
    deploy = _text(ROOT / "deploy" / "ubuntu" / "deploy_from_windows.ps1")

    assert 'sudo usermod --append --groups "$APP_GROUP" "$BACKUP_OS_USER"' in setup
    assert "BACKUP_LIBEXEC_DIR=/usr/local/libexec/mooncen-backup" in setup
    assert "Backup age identity must already exist as a regular non-symlink file" in setup
    assert '"root:root:600"' in setup
    assert "/usr/local/libexec/mooncen-backup/mooncen_backup_to_wtr_nas.sh" in backup
    assert "LoadCredential=backup-ssh-key:" not in backup
    assert "User=root" in restore
    assert "Environment=TEST_DB=mooncen_restore_contract_test" in restore
    assert "LoadCredential=backup-ssh-key:/etc/mooncen/backup-ssh-key" in restore
    assert "/usr/local/libexec/mooncen-backup/mooncen_restore_test_from_wtr_nas.sh" in restore
    assert "ProtectSystem=strict" in restore
    assert "CapabilityBoundingSet=CAP_SETUID CAP_SETGID CAP_DAC_READ_SEARCH CAP_CHOWN CAP_AUDIT_WRITE" in restore
    restore_script = _text(ROOT / "deploy" / "backup" / "mooncen_restore_test_from_synology.sh")
    assert 'chown root:postgres "$WORK_DIR" "$LOCAL_DUMP"' in restore_script
    assert 'chmod 0640 "$LOCAL_DUMP"' in restore_script
    backup_common = _text(ROOT / "deploy" / "backup" / "backup_ssh_common.sh")
    assert "backup_restore_database_candidate" in restore_script
    assert "--no-owner" in backup_common
    assert "--no-privileges" in backup_common
    assert "--no-tablespaces" in backup_common
    assert "--exit-on-error" in backup_common

    full_restore = _text(ROOT / "deploy" / "backup" / "mooncen_restore_latest_from_synology.sh")
    assert "backup_restore_fetch_verified_dump" in full_restore
    assert "ALLOW_LEGACY_UNENCRYPTED_BACKUP" not in full_restore
    assert "--single-transaction" in backup_common
    assert "--exit-on-error" in backup_common
    assert "restore_failed_services_remain_stopped=1" in full_restore
    assert 'ACTIVE_UNITS+=("$unit")' in full_restore
    assert "resume_active_units" in full_restore
    assert "systemctl start --no-block" in full_restore
    assert 'systemctl start "${ACTIVE_UNITS[@]}"' not in full_restore
    assert "DATABASE_COMMITTED=1" in full_restore
    assert "RESTORE_SUCCEEDED=1" in full_restore
    assert 'RESTORE_MIN_COURSES="${RESTORE_MIN_COURSES:-1}"' in full_restore
    assert 'RESTORE_STAGE_DB="${DB_NAME}_rnew_${restore_token}"' in full_restore
    assert 'RESTORE_OLD_DB="${DB_NAME}_rold_${restore_token}"' in full_restore
    assert "Restore validation counts are below the configured minimums" in backup_common

    ssh_common = _text(ROOT / "deploy" / "backup" / "backup_ssh_common.sh")
    assert "backup_validate_config" in ssh_common
    assert 'backup_validate_remote_path "$remote_path"' in ssh_common
    assert "Invalid BACKUP_HOST" in ssh_common
    assert 'sudo chown -R mooncen:mooncen' not in deploy


def test_root_ha_units_execute_root_owned_copies_with_sandboxing():
    setup = _text(SETUP_PATH)
    gate = _text(SYSTEMD_DIR / "mooncen-cloudflare-gate.service")
    guard = _text(SYSTEMD_DIR / "mooncen-cloudflared-role-guard.service")
    watchdog = _text(SYSTEMD_DIR / "mooncen-crawler-watchdog.service")
    gate_script = _text(ROOT / "deploy" / "ha" / "cloudflare_health_gate.sh")
    guard_script = _text(ROOT / "deploy" / "ha" / "cloudflared_role_guard.sh")

    assert "HA_LIBEXEC_DIR=/usr/local/libexec/mooncen-ha" in setup
    assert "/usr/local/libexec/mooncen-ha/cloudflare_health_gate.sh" in gate
    assert "/usr/local/libexec/mooncen-ha/cloudflared_role_guard.sh" in guard
    assert "/opt/mooncen/deploy/ha/" not in gate
    assert "/opt/mooncen/deploy/ha/" not in guard
    for unit in (gate, guard, watchdog):
        for directive in (
            "UMask=0077",
            "NoNewPrivileges=true",
            "PrivateTmp=true",
            "PrivateDevices=true",
            "ProtectSystem=strict",
            "ProtectHome=true",
            "RestrictSUIDSGID=true",
        ):
            assert directive in unit
    assert "ReadWritePaths=/opt/mooncen/failover" in gate
    assert "ReadWritePaths=/opt/mooncen/failover" in guard
    for unit in (gate, guard):
        assert "User=root" in unit
        assert "Group=mooncen" in unit
        assert "Group=root" not in unit
    assert "sudo -u postgres" not in gate_script
    assert "sudo -n" not in guard_script
    assert "runuser -u postgres -- psql" in gate_script
    assert "runuser -u postgres -- psql" in guard_script
    assert "CapabilityBoundingSet=CAP_SETUID CAP_SETGID" in guard


@pytest.mark.skipif(_find_bash() is None, reason="bash unavailable")
def test_bot_db_helper_rejects_unknown_queries(tmp_path: Path):
    setup = _text(SETUP_PATH)
    helper = setup.split('sudo tee "$BOT_PSQL_HELPER" >/dev/null <<\'SH\'', 1)[1].split("\nSH\n", 1)[0]
    helper_path = tmp_path / "mooncen-bot-psql"
    helper_path.write_text(helper.lstrip(), encoding="utf-8")

    rejected = subprocess.run(
        [_find_bash(), str(helper_path), "-Atqc", "DELETE FROM courses;"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode == 64
    assert "query is not allowlisted" in rejected.stderr

    malformed = subprocess.run(
        [_find_bash(), str(helper_path), "-Atqc", "SELECT 1;", "extra"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert malformed.returncode == 64
    assert "unsupported arguments" in malformed.stderr


@pytest.mark.skipif(_find_bash() is None, reason="bash unavailable")
def test_setup_script_passes_bash_syntax_check():
    scripts = (
        SETUP_PATH,
        ROOT / "deploy" / "ubuntu" / "mooncenctl.sh",
        ROOT / "deploy" / "ubuntu" / "install_sudoers.sh",
        ROOT / "deploy" / "ubuntu" / "cloudflared_token_helper.sh",
        ROOT / "deploy" / "ubuntu" / "ops_service_helper.sh",
        ROOT / "deploy" / "ubuntu" / "postgres_role_helper.sh",
        ROOT / "deploy" / "ubuntu" / "mooncen_branch_coordinate_backfill.sh",
        ROOT / "deploy" / "ha" / "n100_crawler_staging_setup.sh",
        ROOT / "deploy" / "backup" / "mooncen_restore_test_from_synology.sh",
    )
    subprocess.run([_find_bash(), "-n", *map(str, scripts)], cwd=ROOT, check=True, capture_output=True, text=True)


def test_systemd_units_have_well_formed_static_contract():
    for path in sorted(SYSTEMD_DIR.glob("*.service")):
        section = None
        seen_sections: set[str] = set()
        for line_number, raw_line in enumerate(_text(path).splitlines(), start=1):
            line = raw_line.strip()
            if not line or line.startswith(("#", ";")):
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1]
                seen_sections.add(section)
                continue
            assert section is not None, f"{path.name}:{line_number}: assignment before section"
            assert "=" in line, f"{path.name}:{line_number}: malformed directive"

        assert "Unit" in seen_sections
        assert "Service" in seen_sections
