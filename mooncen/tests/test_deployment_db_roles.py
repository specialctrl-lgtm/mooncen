from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from DB import db_utils


ROOT = Path(__file__).resolve().parents[1]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_runtime_db_utils_prefers_crawler_and_migrator_is_explicit(monkeypatch):
    monkeypatch.delenv("CRAWL_WRITE_MODE", raising=False)
    monkeypatch.setenv("DB_USER", "owner_login")
    monkeypatch.setenv("DB_PASSWORD", "owner-password")
    monkeypatch.setenv("DB_CRAWLER_USER", "crawler_login")
    monkeypatch.setenv("DB_CRAWLER_PASSWORD", "crawler-password")
    monkeypatch.delenv("DB_USE_MIGRATOR", raising=False)

    runtime = db_utils.get_db_config()
    assert runtime["user"] == "crawler_login"
    assert runtime["password"] == "crawler-password"

    monkeypatch.setenv("DB_USE_MIGRATOR", "1")
    migrator = db_utils.get_db_config()
    assert migrator["user"] == "owner_login"
    assert migrator["password"] == "owner-password"


def test_staging_db_utils_prefers_staging_runtime_credentials(monkeypatch):
    monkeypatch.setenv("CRAWL_WRITE_MODE", "staging")
    monkeypatch.setenv("DB_CRAWLER_USER", "primary_crawler")
    monkeypatch.setenv("DB_CRAWLER_PASSWORD", "primary-password")
    monkeypatch.setenv("CRAWL_STAGING_DB_USER", "staging_crawler")
    monkeypatch.setenv("CRAWL_STAGING_DB_PASSWORD", "staging-password")

    config = db_utils.get_db_config()
    assert config["user"] == "staging_crawler"
    assert config["password"] == "staging-password"


def test_staging_db_utils_rejects_owner_fallback(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("CRAWL_WRITE_MODE", "staging")
    for name in (
        "CRAWL_STAGING_DB_USER",
        "CRAWL_STAGING_DB_PASSWORD",
        "DB_RUNTIME_USER",
        "DB_RUNTIME_PASSWORD",
        "DB_CRAWLER_USER",
        "DB_CRAWLER_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="explicit staging DB credentials"):
        db_utils.get_db_config()


def test_backend_rejects_owner_fallback_in_production():
    env = os.environ.copy()
    env.update({"ENVIRONMENT": "production", "DB_API_USER": "", "DB_API_PASSWORD": ""})
    result = subprocess.run(
        [os.sys.executable, "-c", "import backend.database"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "Production requires explicit DB_API_USER" in result.stderr


def test_backend_rejects_api_login_equal_to_owner_in_production():
    env = os.environ.copy()
    env.update(
        {
            "ENVIRONMENT": "production",
            "DB_OWNER_USER": "mooncen_owner",
            "DB_API_USER": "mooncen_owner",
            "DB_API_PASSWORD": "separate-password-is-required",
        }
    )
    result = subprocess.run(
        [os.sys.executable, "-c", "import backend.database"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "must differ from the database owner" in result.stderr


def test_setup_keeps_owner_secret_out_of_runtime_and_backup_env():
    setup = _text("deploy/ubuntu/setup_project.sh")
    public_block = setup.split('cat > "$APP_DIR/.env" <<ENV', 1)[1].split("\nENV\n", 1)[0]
    api_block = setup.split('install_service_env api.env "$API_OS_USER" <<ENV', 1)[1].split("\nENV\n", 1)[0]
    crawler_block = setup.split('install_service_env crawler.env "$CRAWLER_OS_USER" <<ENV', 1)[1].split("\nENV\n", 1)[0]
    applier_block = setup.split('install_service_env applier.env "$APPLIER_OS_USER" <<ENV', 1)[1].split("\nENV\n", 1)[0]
    backup_block = setup.split('sudo tee "$BACKUP_ENV_FILE" >/dev/null <<ENV', 1)[1].split("\nENV\n", 1)[0]

    for secret_name in (
        "PASSWORD",
        "AUTH_SECRET",
        "MOONCEN_OPS_PASSWORD_HASH",
        "OAUTH_CLIENT_SECRET",
        "BOT_TOKEN",
        "KAKAO_MAPS_REST_API_KEY",
        "GOOGLE_MAPS_API_KEY",
    ):
        assert secret_name not in public_block

    assert "DB_PASSWORD=${DB_PASSWORD}" not in api_block
    assert "DB_API_USER=${DB_API_USER}" in api_block
    assert "DB_API_PASSWORD=${DB_API_PASSWORD}" in api_block
    assert "MOONCEN_OPS_LOGIN_ID=${MOONCEN_OPS_LOGIN_ID}" in api_block
    assert "MOONCEN_OPS_PASSWORD_HASH=${MOONCEN_OPS_PASSWORD_HASH}" in api_block
    assert "DB_CRAWLER_USER=${CRAWL_STAGING_DB_USER}" in crawler_block
    assert "DB_CRAWLER_PASSWORD=${CRAWL_STAGING_DB_PASSWORD}" in crawler_block
    assert "CRAWL_WRITE_MODE=staging" in crawler_block
    assert "DB_CRAWLER_USER=${DB_CRAWLER_USER}" not in crawler_block
    assert "DB_CRAWLER_PASSWORD=${DB_CRAWLER_PASSWORD}" not in crawler_block
    assert "CRAWL_STAGING_DB_USER=${CRAWL_STAGING_DB_USER}" in crawler_block
    assert "CRAWL_STAGING_DB_PASSWORD=${CRAWL_STAGING_DB_PASSWORD}" in crawler_block
    assert "KAKAO_MAPS_REST_API_KEY=${KAKAO_MAPS_REST_API_KEY}" in crawler_block
    assert "KAKAO_GEOCODE_MAX_REQUESTS_PER_RUN=1000" in crawler_block
    assert "GOOGLE_MAPS_API_KEY" not in crawler_block
    assert "PRIMARY_DB_USER=${DB_APPLIER_USER}" in applier_block
    assert "PRIMARY_DB_PASSWORD=${DB_APPLIER_PASSWORD}" in applier_block
    assert "CRAWL_STAGING_DB_PASSWORD=${CRAWL_STAGING_DB_PASSWORD}" in applier_block
    assert "DB_BACKUP_PASSWORD=${DB_BACKUP_PASSWORD}" not in api_block
    assert "KAKAO_MAPS_REST_API_KEY" not in api_block
    assert "KAKAO_MAPS_REST_API_KEY" not in applier_block
    assert "KAKAO_MAPS_REST_API_KEY" not in backup_block

    assert "DB_BACKUP_USER=${DB_BACKUP_USER}" in backup_block
    assert "DB_BACKUP_PASSWORD=${DB_BACKUP_PASSWORD}" in backup_block
    assert "BACKUP_AGE_RECIPIENT=${BACKUP_AGE_RECIPIENT}" in backup_block
    assert "DEPLOY_SECRET_DIR/deploy-secrets.env" in setup
    assert "write_deploy_secret_pair DB_PASSWORD" in setup
    assert setup.count("write_deploy_secret_pair DB_PASSWORD") == 1
    assert setup.index('mv -f "$deploy_secret_tmp" "$DEPLOY_SECRET_FILE"') < setup.index(
        "'ALTER ROLE %I WITH LOGIN INHERIT"
    )
    assert "FromBase64String" in _text("deploy/ubuntu/deploy_from_windows.ps1")
    assert "DB_USE_MIGRATOR=1" in setup


def test_primary_crawler_receives_only_dedicated_staging_credentials():
    setup = _text("deploy/ubuntu/setup_project.sh")
    crawler_block = setup.split(
        'install_service_env crawler.env "$CRAWLER_OS_USER" <<ENV', 1
    )[1].split("\nENV\n", 1)[0]

    assert 'CRAWL_STAGING_DB_USER="${CRAWL_STAGING_DB_USER:-mooncen_staging_crawler_login}"' in setup
    assert 'CRAWL_STAGING_PASSWORD_FILE="${CRAWL_STAGING_PASSWORD_FILE:-/etc/mooncen/staging-crawler-password}"' in setup
    assert "openssl rand -hex 32" in setup
    assert 'root:root:600' in setup
    assert "Crawler staging password must differ from every primary database password" in setup
    assert "Crawler staging login must differ from every primary database login" in setup
    assert "DB_HOST=${CRAWL_STAGING_DB_HOST}" in crawler_block
    assert "DB_PORT=${CRAWL_STAGING_DB_PORT}" in crawler_block
    assert "DB_NAME=${CRAWL_STAGING_DB_NAME}" in crawler_block
    assert "${DB_CRAWLER_PASSWORD}" not in crawler_block


def test_role_sql_is_piped_by_a_privileged_reader():
    setup = _text("deploy/ubuntu/setup_project.sh")

    assert 'sudo cat "$APP_DIR/DB/roles.sql" |' in setup
    assert 'sudo -u postgres psql -v ON_ERROR_STOP=1 -d "$DB_NAME"' in setup
    assert '-f "$APP_DIR/DB/roles.sql"' not in setup


@pytest.mark.skip(reason="the retired Ops deployment worker is no longer provisioned")
def test_guarded_native_deploy_bootstraps_dedicated_deployment_worker_login():
    setup = _text("deploy/ubuntu/setup_project.sh")
    deploy = _text("deploy/ubuntu/deploy_from_windows.ps1")

    assert (
        'DB_DEPLOYMENT_WORKER_USER="${DB_DEPLOYMENT_WORKER_USER:-mooncen_deployment_worker_login}"'
        in setup
    )
    assert '[string]$DbDeploymentWorkerUser = "mooncen_deployment_worker_login"' in deploy
    assert '[string]$DbDeploymentWorkerPassword = ""' in deploy
    assert (
        'if ($DbDeploymentWorkerUser -cne "mooncen_deployment_worker_login")'
        in deploy
    )
    assert (
        '[ "$DB_DEPLOYMENT_WORKER_USER" = mooncen_deployment_worker_login ]'
        in setup
    )

    secret_block = setup.split(
        'deploy_secret_tmp="$(mktemp "$DEPLOY_SECRET_DIR/deploy-secrets.env.XXXXXX")"',
        1,
    )[1].split('mv -f "$deploy_secret_tmp" "$DEPLOY_SECRET_FILE"', 1)[0]
    assert "DB_DEPLOYMENT_WORKER_USER=%s" in secret_block
    assert (
        'write_deploy_secret_pair DB_DEPLOYMENT_WORKER_PASSWORD '
        '"$DB_DEPLOYMENT_WORKER_PASSWORD"'
        in secret_block
    )

    exporter_revoke = setup.index(
        'sudo rm -f -- "$AN2P_CONTROL_SECRETS_EXPORT_HELPER"'
    )
    db_setup_marker = 'if [ "$SKIP_DB_SETUP" != "1" ]; then'
    db_setup_start = setup.index(db_setup_marker, exporter_revoke)
    db_setup = setup[db_setup_start + len(db_setup_marker) :].split(
        "\nelse\n  echo \"Skipping DB setup/migration",
        1,
    )[0]
    migrate = db_setup.index('DB/setup_db.py" --mode migrate')
    roles = db_setup.index('sudo cat "$APP_DIR/DB/roles.sql"')
    deployment_login = db_setup.index(
        'cat "$APP_DIR/DB/provision_deployment_worker_login.sql"'
    )
    runtime_logins = db_setup.index('cat "$APP_DIR/DB/provision_login_roles.sql"')
    hba_probe = db_setup.index('sudo "$CONTAINER_PG_HBA_HELPER" install')
    exact_worker_boundary = db_setup.index("--verify-database-boundary")
    root_source_commit = db_setup.index(
        'sudo mv -fT -- "$root_deploy_secret_stage" "$ROOT_DEPLOY_SECRET_FILE"'
    )
    exporter_restore = db_setup.index(
        '"$AN2P_CONTROL_SECRETS_EXPORT_SOURCE" \\\n'
        '  "$AN2P_CONTROL_SECRETS_EXPORT_HELPER"'
    )
    assert exporter_revoke < db_setup_start
    assert (
        migrate
        < roles
        < deployment_login
        < runtime_logins
        < hba_probe
        < exact_worker_boundary
        < root_source_commit
        < exporter_restore
    )
    assert 'printf "SET password_encryption = \'scram-sha-256\';\\n"' in db_setup
    assert "printf '\\\\set db_deployment_worker_user %s\\n'" in db_setup
    assert "printf '\\\\set db_deployment_worker_password_b64 %s\\n'" in db_setup
    assert (
        '} | sudo -u postgres psql -v ON_ERROR_STOP=1 -d "$DB_NAME"'
        in db_setup
    )

    for contract in (
        "secret.rolpassword LIKE 'SCRAM-SHA-256$%'",
        "permission_group.rolname = 'mooncen_deployment_worker'",
        "NOT permission_group.rolcanlogin",
        "parent.rolname = 'mooncen_deployment_worker'",
        "public.ops_container_releases', 'INSERT'",
        "public.ops_container_validation_receipts', 'INSERT'",
        "public.ops_container_approval_evidence', 'SELECT'",
        "public.ops_container_approval_evidence', 'INSERT'",
        "public.ops_container_deployment_lease_epoch_seq', 'USAGE'",
        "attribute.attrelid = 'public.ops_jobs'::regclass",
        "attribute.attrelid = 'public.ops_deployments'::regclass",
        "dependency.classid = 'pg_namespace'::regclass",
        "dependency.classid = 'pg_class'::regclass",
        "dependency.classid = 'pg_proc'::regclass",
        "has_any_column_privilege('${DB_DEPLOYMENT_WORKER_USER}', relation.oid, 'REFERENCES')",
        "has_any_column_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_agents', 'REFERENCES')",
        "has_any_column_privilege('${DB_DEPLOYMENT_WORKER_USER}', 'public.ops_job_logs', 'REFERENCES')",
        "has_function_privilege('${DB_DEPLOYMENT_WORKER_USER}', procedure.oid, 'EXECUTE')",
        "procedure.prokind IN ('f', 'p', 'a', 'w')",
    ):
        assert contract in db_setup

    remote_reader = deploy.split("function Get-RemoteEnvValue", 1)[1].split(
        "\n}",
        1,
    )[0]
    assert remote_reader.index("^${Name}_B64=") < remote_reader.index("^${Name}=")
    worker_resolution = deploy.split(
        '$remoteDbDeploymentWorkerPassword = Get-RemoteEnvValue '
        '"DB_DEPLOYMENT_WORKER_PASSWORD"',
        1,
    )[1].split("$runtimePasswords = @(", 1)[0]
    assert "New-RandomSecret" in worker_resolution
    assert "New-DerivedSecret" not in worker_resolution
    assert "protected remote identity" in deploy
    assert "must differ from every other database LOGIN credential" in deploy
    assert "Database LOGIN credentials must be pairwise distinct." in setup
    assert "Database LOGIN credentials must be pairwise distinct." in deploy
    for other_password in (
        "$DbPassword",
        "$DbApiPassword",
        "$DbCrawlerPassword",
        "$DbAiPassword",
        "$DbApplierPassword",
        "$DbBackupPassword",
        "$DbCheckPassword",
    ):
        assert other_password in deploy
    setup_password_contract = setup.split(
        "database_password_vars=(",
        1,
    )[1].split("\n)", 1)[0]
    for other_password_var in (
        "DB_PASSWORD",
        "DB_API_PASSWORD",
        "DB_CRAWLER_PASSWORD",
        "DB_DEPLOYMENT_WORKER_PASSWORD",
        "DB_AI_PASSWORD",
        "DB_APPLIER_PASSWORD",
        "DB_BACKUP_PASSWORD",
        "DB_CHECK_PASSWORD",
    ):
        assert other_password_var in setup_password_contract
    powershell_password_contract = deploy.split(
        "$databaseLoginCredentials = @(",
        1,
    )[1].split("\n)", 1)[0]
    for other_password in (
        "$DbPassword",
        "$DbApiPassword",
        "$DbCrawlerPassword",
        "$DbDeploymentWorkerPassword",
        "$DbAiPassword",
        "$DbApplierPassword",
        "$DbBackupPassword",
        "$DbCheckPassword",
    ):
        assert other_password in powershell_password_contract
    staging_distinct = setup.split("for primary_password in", 1)[1].split(
        "\ndone",
        1,
    )[0]
    assert '"$DB_DEPLOYMENT_WORKER_PASSWORD"' in staging_distinct
    assert "DB_DEPLOYMENT_WORKER_PASSWORD|DB_AI_PASSWORD" in deploy
    assert "-DbDeploymentWorkerPassword\\s+" in deploy
    assert not any(
        "$DbDeploymentWorkerPassword" in line
        for line in deploy.splitlines()
        if "Write-Host" in line
    )
    assert not any(
        "$DB_DEPLOYMENT_WORKER_PASSWORD" in line
        for line in setup.splitlines()
        if line.lstrip().startswith(("echo ", "printf "))
        and "db_deployment_worker_password_b64" not in line
        and line.strip() != "printf '%s\\n' \"$DB_DEPLOYMENT_WORKER_PASSWORD\" |"
    )
    assert (
        "printf '%s\\n' \"$DB_DEPLOYMENT_WORKER_PASSWORD\" |\n"
        "  (\n"
        "    cd \"$APP_DIR\""
        in setup
    )
    assert (
        "$dbDeploymentWorkerPasswordB64 = ConvertTo-Base64Utf8 "
        "$DbDeploymentWorkerPassword"
        in deploy
    )
    assert (
        'export DB_DEPLOYMENT_WORKER_PASSWORD="`$(printf \'%s\' '
        "'$dbDeploymentWorkerPasswordB64' | base64 -d)\""
        in deploy
    )
    assert "DB/provision_deployment_worker_login.sql" in deploy


def test_standby_receives_worker_secret_but_never_provisions_production_db():
    setup = _text("deploy/ubuntu/setup_project.sh")
    deploy = _text("deploy/ubuntu/deploy_from_windows.ps1")

    password_generation = setup.split(
        "for password_var in DB_PASSWORD",
        1,
    )[1].split("\ndone", 1)[0]
    assert "DB_DEPLOYMENT_WORKER_PASSWORD" in password_generation
    assert 'if [ "$SKIP_DB_SETUP" = "1" ]; then' in password_generation

    primary_db_setup, skipped_db_setup = setup.split(
        '\nelse\n  echo "Skipping DB setup/migration because SKIP_DB_SETUP=1."',
        1,
    )
    assert "provision_deployment_worker_login.sql" in primary_db_setup
    assert "provision_deployment_worker_login.sql" not in skipped_db_setup

    remote_setup = deploy.split('$remoteSetupScript = @"', 1)[1].split(
        '"@',
        1,
    )[0]
    worker_export = remote_setup.index("export DB_DEPLOYMENT_WORKER_PASSWORD=")
    skip_db_export = remote_setup.index("export SKIP_DB_SETUP=")
    setup_call = remote_setup.index("./deploy/ubuntu/setup_project.sh")
    assert worker_export < skip_db_export < setup_call


def test_deployment_worker_login_convergence_is_atomic_and_password_is_stdin_only():
    provision = _text("DB/provision_deployment_worker_login.sql")
    statements = [
        line.strip()
        for line in provision.splitlines()
        if line.strip() and not line.lstrip().startswith("--")
    ]

    assert statements.count("BEGIN;") == 1
    assert statements.count("COMMIT;") == 1
    assert statements.index("BEGIN;") < statements.index("COMMIT;")
    assert "decode(:'db_deployment_worker_password_b64', 'base64')" in provision
    assert "PASSWORD %L" in provision
    assert "FROM pg_auth_members membership" in provision
    assert "GRANT mooncen_deployment_worker TO %I" in provision
    assert "FROM information_schema.column_privileges" in provision
    assert "grantee = :'db_deployment_worker_user'" in provision
    assert "REVOKE ALL PRIVILEGES (%I) ON TABLE %I.%I FROM %I" in provision
    assert "procedure.prokind IN ('f', 'p', 'a', 'w')" in provision
    assert "CASE procedure.prokind WHEN 'p' THEN 'PROCEDURE' ELSE 'FUNCTION' END" in provision
    assert (
        "WHERE :'db_deployment_worker_user' <> "
        "'mooncen_deployment_worker_login'"
        in provision
    )
    assert "!~ '^[a-z_][a-z0-9_]{0,62}$'" not in provision
    assert "db_deployment_worker_password_b64=" not in provision


def test_setup_owner_convergence_only_alters_mismatches_per_transaction():
    setup = _text("deploy/ubuntu/setup_project.sh")
    owner_block = setup.split(
        "CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;", 1
    )[1].split("\nSQL\nfi", 1)[0]

    assert "db.datdba <> target_owner.oid" in setup
    assert "namespace.nspname IN ('public', 'crawl_staging')" in owner_block
    assert "namespace.nspowner <> target_owner.oid" in owner_block
    assert "c.relowner <> target_owner.oid" in owner_block
    assert "p.proowner <> target_owner.oid" in owner_block

    # Each generated ALTER is autocommitted by psql instead of accumulating
    # AccessExclusive locks in one DO transaction.
    assert "DO \\$\\$" not in owner_block
    assert sum(line.strip() == "\\gexec" for line in owner_block.splitlines()) == 3

    for statement in (
        "ALTER SEQUENCE %I.%I OWNER TO %I",
        "ALTER VIEW %I.%I OWNER TO %I",
        "ALTER MATERIALIZED VIEW %I.%I OWNER TO %I",
        "ALTER TABLE %I.%I OWNER TO %I",
        "ALTER FUNCTION %I.%I(%s) OWNER TO %I",
    ):
        assert statement in owner_block

    assert owner_block.count("d.deptype = 'e'") == 2


def test_role_sql_converges_old_grants_and_preserves_column_level_api_write():
    roles = _text("DB/roles.sql")
    provision = _text("DB/provision_login_roles.sql")
    courses = _text("backend/routers/courses.py")

    assert "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public" in roles
    assert "REVOKE CONNECT, TEMPORARY ON DATABASE %I FROM PUBLIC" in roles
    assert "GRANT CONNECT ON DATABASE %I TO mooncen_api, mooncen_crawler" in roles
    assert "GRANT TEMPORARY ON DATABASE %I TO mooncen_crawler, mooncen_applier" in roles
    assert "GRANT UPDATE(view_count) ON courses TO mooncen_api" in roles
    assert (
        "has_function_privilege(\n"
        "    '${DB_API_USER}',\n"
        "    'public.mooncen_raw_url_fingerprint(text)',\n"
        "    'EXECUTE'\n"
        "  )"
    ) in _text("deploy/ubuntu/setup_project.sh")
    assert (
        "has_function_privilege(\n"
        "    '${DB_AI_USER}',\n"
        "    'public.mooncen_raw_url_fingerprint(text)',\n"
        "    'EXECUTE'\n"
        "  )"
    ) in _text("deploy/ubuntu/setup_project.sh")
    setup = _text("deploy/ubuntu/setup_project.sh")
    assert "'${DB_DEPLOYMENT_WORKER_USER}', '${DB_NAME}', 'TEMPORARY'" in setup
    for ai_function_signature in (
        "mooncen_search_ngrams(text)",
        "mooncen_text_contains_any(text,text[])",
        "mooncen_infer_course_service_group(text,text,text,text,text,text,text,text,text,text,text)",
        "mooncen_resolve_course_service_group(text,text,text,text,text,text,text,text,text,text,text,text)",
    ):
        assert (
            "has_function_privilege(\n"
            "    '${DB_AI_USER}',\n"
            f"    'public.{ai_function_signature}',\n"
            "    'EXECUTE'\n"
            "  )"
        ) in setup
    detail_handler = courses.split("def get_course_detail(", 1)[1].split(
        '@router.post(\n    "/{course_id}/update-request"',
        1,
    )[0]
    assert "SET view_count = COALESCE(view_count, 0) + 1" in detail_handler
    assert "updated_at" not in detail_handler.split("db.execute(", 1)[1]
    assert "db.query(models.Course).filter(models.Course.id == course_id).update(" not in detail_handler
    assert "GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO mooncen_readonly" in roles
    assert (
        "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public\n"
        "    TO mooncen_api, mooncen_crawler, mooncen_crawler_worker, mooncen_applier;"
        not in roles
    )
    assert "GRANT USAGE, SELECT ON SEQUENCE crawler_run_log_id_seq" in roles
    assert "WHEN column_item.grantee = 'PUBLIC' THEN 'PUBLIC'" in roles
    for default_object_kind in ("TABLES", "SEQUENCES", "TYPES"):
        assert (
            "IN SCHEMA public REVOKE ALL PRIVILEGES ON "
            f"{default_object_kind} FROM PUBLIC"
        ) in roles
        assert (
            "IN SCHEMA crawl_staging REVOKE ALL PRIVILEGES ON "
            f"{default_object_kind} FROM PUBLIC"
        ) in roles
    assert "GRANT SELECT ON ALL TABLES IN SCHEMA crawl_staging TO mooncen_readonly" in roles
    assert "course_quality_score" in roles
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS" in provision
    assert "FROM pg_auth_members membership" in provision
    assert "REVOKE ALL PRIVILEGES ON DATABASE" in provision
    assert "GRANT mooncen_readonly" in provision
    assert "PASSWORD %L" in provision
    assert "decode(:'db_api_password_b64', 'base64')" in provision


def test_runtime_acl_and_login_membership_convergence_are_atomic():
    roles = _text("DB/roles.sql")
    provision = _text("DB/provision_login_roles.sql")

    for sql in (roles, provision):
        statements = [
            line.strip()
            for line in sql.splitlines()
            if line.strip() and not line.lstrip().startswith("--")
        ]
        assert statements.count("BEGIN;") == 1
        assert statements.count("COMMIT;") == 1
        assert statements.index("BEGIN;") < next(
            index
            for index, statement in enumerate(statements)
            if statement.startswith(("DO $$", "SELECT format", "REVOKE", "ALTER ROLE"))
        )
        assert statements.index("COMMIT;") > max(
            index
            for index, statement in enumerate(statements)
            if statement.startswith(("GRANT", "REVOKE", "ALTER", "END $$", "\\gexec"))
        )

    membership_revoke = provision.index("FROM pg_auth_members membership")
    intended_membership_grants = provision.index("SELECT format('GRANT mooncen_api TO %I'")
    assert provision.index("BEGIN;") < membership_revoke < intended_membership_grants
    assert intended_membership_grants < provision.rindex("COMMIT;")


def test_course_view_counter_does_not_change_content_freshness():
    migration = _text("DB/migrations/20260724_001_preserve_course_freshness_on_view.sql")
    for schema_source in ("DB/schema.sql", "DB/migrate_current.sql"):
        definition = (
            _text(schema_source)
            .split(
                "CREATE OR REPLACE FUNCTION update_modified_column()",
                1,
            )[1]
            .split("$$ LANGUAGE plpgsql;", 1)[0]
        )
        assert "NEW.view_count IS DISTINCT FROM OLD.view_count" in definition
        assert "to_jsonb(NEW) - 'view_count' - 'updated_at'" in definition
        assert "NEW.updated_at = OLD.updated_at" in definition
        assert "NEW.updated_at = now()" in definition

    assert "NEW.view_count IS DISTINCT FROM OLD.view_count" in migration
    assert "NEW.updated_at = OLD.updated_at" in migration
    assert "NEW.updated_at = now()" in migration


def test_role_sql_closes_public_routine_acl_without_touching_extensions():
    roles = _text("DB/roles.sql")

    routine_block = roles.split("A direct", 1)[1].split("END $$;", 1)[0]
    assert "namespace.nspname IN ('public', 'crawl_staging')" in routine_block
    assert "procedure.prokind IN ('f', 'p', 'a', 'w')" in routine_block
    assert "dependency.classid = 'pg_proc'::regclass" in routine_block
    assert "dependency.deptype = 'e'" in routine_block
    assert "ALTER %s %I.%I(%s) SECURITY INVOKER" in routine_block
    assert "IF routine.prokind IN ('f', 'p') THEN" in routine_block
    assert "CASE routine.prokind WHEN 'p' THEN 'PROCEDURE' ELSE 'FUNCTION' END" in routine_block
    assert "FROM PUBLIC, mooncen_api, mooncen_crawler" in routine_block
    assert "REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA" not in roles

    assert (
        "GRANT EXECUTE ON FUNCTION public.mooncen_raw_url_fingerprint(text) "
        "TO mooncen_api, mooncen_crawler, mooncen_crawler_worker, mooncen_applier, mooncen_ai"
    ) in roles
    assert (
        "GRANT EXECUTE ON FUNCTION public.mooncen_search_ngrams(text) "
        "TO mooncen_crawler, mooncen_crawler_worker, mooncen_applier, mooncen_ai"
    ) in roles
    for helper_signature in (
        "mooncen_text_contains_any(text,text[])",
        "mooncen_infer_course_service_group(text,text,text,text,text,text,text,text)",
        "mooncen_infer_course_service_group(text,text,text,text,text,text,text,text,text,text,text)",
        "mooncen_resolve_course_service_group(text,text,text,text,text,text,text,text,text)",
        "mooncen_resolve_course_service_group(text,text,text,text,text,text,text,text,text,text,text,text)",
    ):
        assert (
            f"GRANT EXECUTE ON FUNCTION public.{helper_signature} "
            "TO mooncen_crawler, mooncen_crawler_worker, mooncen_applier"
        ) in roles
    for ai_helper_signature in (
        "mooncen_text_contains_any(text,text[])",
        "mooncen_infer_course_service_group(text,text,text,text,text,text,text,text,text,text,text)",
        "mooncen_resolve_course_service_group(text,text,text,text,text,text,text,text,text,text,text,text)",
    ):
        assert (
            f"GRANT EXECUTE ON FUNCTION public.{ai_helper_signature} "
            "TO mooncen_crawler, mooncen_crawler_worker, mooncen_applier, mooncen_ai"
        ) in roles
    assert "GRANT EXECUTE ON FUNCTION public.current_crawl_batch_id() TO mooncen_crawler" in roles
    for trigger_entry_point in (
        "sync_branch_location()",
        "update_modified_column()",
        "mooncen_update_course_search_document()",
        "mooncen_set_course_service_group()",
        "mooncen_protect_oauth_identity()",
        "set_current_crawl_batch_id()",
        "update_crawl_batch_modtime()",
    ):
        assert f"GRANT EXECUTE ON FUNCTION public.{trigger_entry_point}" not in roles

    defaults_block = roles.split("Default routine ACLs belong", 1)[1].split("END $$;", 1)[0]
    assert "procedure.proowner AS owner_oid" in defaults_block
    assert "procedure.prokind IN ('f', 'p', 'a', 'w')" in defaults_block
    assert "class.relowner AS owner_oid" in defaults_block
    assert "a per-schema default REVOKE cannot subtract" in defaults_block
    assert "ALTER DEFAULT PRIVILEGES FOR ROLE %I REVOKE EXECUTE ON ROUTINES" in defaults_block
    assert "IN SCHEMA %I REVOKE EXECUTE ON ROUTINES" not in defaults_block
    assert "REVOKE EXECUTE ON ROUTINES FROM PUBLIC" in defaults_block
    assert "REVOKE EXECUTE ON ROUTINES FROM mooncen_api, mooncen_crawler" in defaults_block


@pytest.mark.skip(reason="the retired Ops deployment worker is no longer provisioned")
def test_deployment_worker_routine_boundary_covers_every_executable_pg_proc_kind():
    expected_filter = "procedure.prokind IN ('f', 'p', 'a', 'w')"
    for path in (
        "DB/roles.sql",
        "DB/roles_body.sql",
        "DB/provision_deployment_worker_login.sql",
        "deploy/ubuntu/setup_project.sh",
        "tools/register_container_deployment_evidence.py",
    ):
        source = _text(path)
        assert expected_filter in source, path
        assert "procedure.prokind IN ('f', 'p')" not in source, path

    for path in ("DB/roles.sql", "DB/roles_body.sql"):
        source = _text(path)
        assert source.count(expected_filter) == 3
        assert "IF routine.prokind IN ('f', 'p') THEN" in source
        assert (
            "CASE routine.prokind WHEN 'p' THEN 'PROCEDURE' ELSE 'FUNCTION' END"
            in source
        )


@pytest.mark.skip(reason="the retired container evidence registrar is no longer installed")
def test_runtime_roles_cannot_create_or_retain_postgresql_large_objects():
    roles = _text("DB/roles.sql")
    roles_body = _text("DB/roles_body.sql")
    worker_login = _text("DB/provision_deployment_worker_login.sql")
    runtime_logins = _text("DB/provision_login_roles.sql")
    setup = _text("deploy/ubuntu/setup_project.sh")
    registrar = _text("tools/register_container_deployment_evidence.py")
    creator_signatures = (
        "pg_catalog.lo_creat(integer)",
        "pg_catalog.lo_create(oid)",
        "pg_catalog.lo_from_bytea(oid,bytea)",
        "pg_catalog.lo_import(text)",
        "pg_catalog.lo_import(text,oid)",
        "pg_catalog.lo_export(oid,text)",
    )

    # roles_body is also executed by a non-superuser crawler schema installer;
    # keep the pg_catalog mutation in the postgres-only LOGIN provision step.
    for role_source in (roles, roles_body):
        assert "ALL ROUTINES IN SCHEMA pg_catalog" not in role_source
        assert "ALTER LARGE OBJECT" not in role_source

    assert "rolname = current_user AND rolsuper" in worker_login
    assert "FROM pg_largeobject_metadata large_object" in worker_login
    assert "ALTER LARGE OBJECT %s OWNER TO %I" in worker_login
    assert "REVOKE ALL PRIVILEGES ON LARGE OBJECT %s FROM PUBLIC" in worker_login
    assert "ALL ROUTINES IN SCHEMA pg_catalog" in worker_login
    assert "REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC" in worker_login
    for signature in creator_signatures:
        assert signature in worker_login

    assert "FROM pg_largeobject_metadata large_object" in runtime_logins
    assert "REVOKE ALL PRIVILEGES ON LARGE OBJECT %s FROM %I" in runtime_logins
    assert "ALL ROUTINES IN SCHEMA pg_catalog" in runtime_logins

    for boundary_source in (setup, registrar):
        assert "pg_largeobject_metadata" in boundary_source
        assert "has_largeobject_privilege" not in boundary_source
        for signature in creator_signatures:
            assert signature in boundary_source

    assert "large_objects_absent" in registrar
    assert "large_object_entry_points_denied" in registrar
    assert "pg_catalog_routine_privileges_exact" in registrar
    assert "aclexplode" in registrar


@pytest.mark.skip(reason="the retired container evidence registrar is no longer installed")
def test_deployment_worker_system_catalog_boundary_is_converged_and_shared():
    worker_login = _text("DB/provision_deployment_worker_login.sql")
    runtime_logins = _text("DB/provision_login_roles.sql")
    setup = _text("deploy/ubuntu/setup_project.sh")
    registrar = _text("tools/register_container_deployment_evidence.py")

    for source in (worker_login, runtime_logins):
        assert "ALTER ROLE %I RESET ALL" in source
        assert "pg_catalog.pg_db_role_setting" in source
        assert "ALL TABLES IN SCHEMA %I" in source
        assert "ALL SEQUENCES IN SCHEMA %I" in source
        assert "ALL ROUTINES IN SCHEMA %I" in source
        assert "pg_catalog.pg_attribute" in source
        assert "REVOKE ALL PRIVILEGES (%I) ON TABLE %I.%I" in source
        assert "pg_catalog.pg_parameter_acl" in source
        assert "FOREIGN DATA WRAPPER" in source
        assert "FOREIGN SERVER" in source
        assert "pg_catalog.pg_user_mapping" in source
        assert "information_schema" in source

    assert "pg_catalog.pg_init_privs" in worker_login
    assert "pg_catalog.acldefault('c', relation.relowner)" in worker_login
    assert "current_acl.is_grantable" in worker_login
    assert "REVOKE ALL PRIVILEGES ON SCHEMA %I" in worker_login
    assert "ON SCHEMA %I FROM PUBLIC" in worker_login
    assert "initial_acl.initprivs" in worker_login

    for field in (
        "role_settings_safe",
        "system_schema_inventory_exact",
        "system_schema_privileges_exact",
        "extension_inventory_exact",
        "system_relation_privileges_exact",
        "user_defined_system_objects_absent",
        "pg_catalog_routine_privileges_exact",
        "parameter_privileges_absent",
        "foreign_data_access_denied",
    ):
        assert field in registrar

    for token in (
        "pg_catalog.pg_init_privs",
        "pg_catalog.pg_parameter_acl",
        "pg_catalog.pg_db_role_setting",
        "current_setting('session_replication_role') = 'origin'",
        "pg_catalog.pg_foreign_data_wrapper",
        "pg_catalog.pg_foreign_server",
        "pg_catalog.pg_user_mapping",
        "relation.relkind IN ('r', 'p', 'v', 'm', 'f', 'S', 't')",
        "relation.relkind IN ('r', 'p', 'v', 'm', 'f', 't')",
        "namespace.nspname = 'information_schema'",
    ):
        assert token in registrar

    for extension in (
        "pg_trgm|1.6|public|postgres",
        "pgcrypto|1.3|public|postgres",
        "plpgsql|1.0|pg_catalog|postgres",
        "postgis|3.4.2|public|postgres",
        "uuid-ossp|1.1|public|postgres",
    ):
        assert extension in registrar

    # setup_project must run the exact registrar query after the HBA probe and
    # before publishing the root-only exporter source; no divergent inline
    # approximation may be treated as the final boundary.
    verify = "--verify-database-boundary"
    assert setup.index(verify) < setup.index("root_deploy_secret_stage=")


def test_crawler_runtime_tables_are_owner_managed_not_runtime_ddl():
    schema = _text("DB/schema.sql")
    migration = _text("DB/migrations/20260710_009_crawl_progress_owner_managed.sql")
    progress = _text("DB/crawl_progress.py")
    run_log = _text("DB/crawler_run_log.py")

    assert "CREATE TABLE IF NOT EXISTS crawl_progress" in schema
    assert "CREATE TABLE IF NOT EXISTS crawl_progress" in migration
    assert "to_regclass('public.crawl_progress')" in progress
    assert "to_regclass('public.crawler_run_log')" in run_log
    assert 'os.getenv("DB_USE_MIGRATOR"' in progress
    assert 'os.getenv("DB_USE_MIGRATOR"' in run_log


def test_backup_age_recipient_and_readonly_db_credentials_reach_service():
    deploy = _text("deploy/ubuntu/deploy_from_windows.ps1")
    deploy_wrapper = _text("deploy_ubuntu.ps1")
    deploy_orchestrator = _text("deploy_mooncen.ps1")
    setup = _text("deploy/ubuntu/setup_project.sh")
    backup_service = _text("deploy/ubuntu/systemd/mooncen-backup.service")
    backup_script = _text("deploy/backup/mooncen_backup_to_synology.sh")

    assert '[string]$BackupAgeRecipient = ""' in deploy
    assert "$backupAgeRecipientB64 = ConvertTo-Base64Utf8 $BackupAgeRecipient" in deploy
    assert "export BACKUP_AGE_RECIPIENT=" in deploy
    assert "install -d -m 700 '$remoteSetupRemoteDir'" in deploy
    assert "sudo -n /bin/mkdir -- '$remoteReleaseDir'" in deploy
    assert "sudo -n /bin/chown '$User' '$remoteReleaseDir'" in deploy
    assert "sudo -n /bin/chmod 0700 '$remoteReleaseDir'" in deploy
    assert "-BackupAgeRecipient $BackupAgeRecipient" in deploy_wrapper
    assert "-DbApplierPassword $DbApplierPassword" in deploy_wrapper
    assert 'Get-ConfigValue "MoonCenBackupAgeRecipient"' in deploy_orchestrator
    assert "Get-RemoteEnvValueFromHost $activeConfig.Server" in deploy_orchestrator
    assert '"PRIMARY_DB_PASSWORD"' in deploy_orchestrator
    assert "cat $envPath" not in deploy_orchestrator
    assert "BACKUP_AGE_RECIPIENT=${BACKUP_AGE_RECIPIENT}" in setup
    assert "EnvironmentFile=" not in backup_service
    assert "EnvironmentFile=-/opt/mooncen/.env" not in backup_service
    assert 'DB_USER="${DB_BACKUP_USER:-}"' in backup_script
    assert 'DB_PASSWORD="${DB_BACKUP_PASSWORD:-}"' in backup_script
    assert "${DB_USER:-" not in backup_script
    assert "${DB_PASSWORD:-" not in backup_script
    assert "refusing to create a plaintext backup" in backup_script


def test_remote_secret_reader_preserves_shell_quoting_across_windows_ssh():
    deploy = _text("deploy/ubuntu/deploy_from_windows.ps1")
    orchestrator = _text("deploy_mooncen.ps1")

    for script, function_name in (
        (deploy, "function Get-RemoteEnvValue"),
        (orchestrator, "function Get-RemoteEnvValueFromHost"),
    ):
        function = script.split(function_name, 1)[1].split("\n}", 1)[0]
        assert "[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($command))" in function
        assert "$transportCommand = \"printf '%s' '$encodedCommand' | base64 -d | bash\"" in function
        assert "$transportCommand 2>$null" in function
        assert "$command 2>$null" not in function


def test_standby_deploy_reuses_the_managed_database_ca_path():
    deploy = _text("deploy/ubuntu/deploy_from_windows.ps1")
    orchestrator = _text("deploy_mooncen.ps1")
    setup = _text("deploy/ubuntu/setup_project.sh")
    managed_ca_path = "/etc/mooncen/db-root-ca.crt"

    assert f'$dbSslRootCert = "{managed_ca_path}"' in orchestrator
    assert orchestrator.index(f'$dbSslRootCert = "{managed_ca_path}"') < orchestrator.index(
        "-DbSslRootCert $dbSslRootCert"
    )
    assert f'$DbSslRootCert = "{managed_ca_path}"' in deploy
    assert deploy.index(f'$DbSslRootCert = "{managed_ca_path}"') < deploy.index(
        "$dbSslRootCertB64 = ConvertTo-Base64Utf8 $DbSslRootCert"
    )

    # Reuse remains fail-closed: setup accepts the managed file as its own
    # source but still rejects missing files, symlinks, and unsafe write modes.
    assert 'source_db_ca="$(sudo readlink -f "$DB_SSLROOTCERT_SOURCE")"' in setup
    assert 'target_db_ca="$(sudo readlink -m "$service_db_ca")"' in setup
    assert 'if [ "$source_db_ca" != "$target_db_ca" ]; then' in setup
    assert "DB_SSLROOTCERT must be an existing regular file, not a symlink" in setup
    assert "DB_SSLROOTCERT must not be group- or world-writable" in setup


def test_standby_deploy_disables_every_primary_backup_timer() -> None:
    deploy = _text("deploy/ubuntu/deploy_from_windows.ps1")
    standby_disable = next(
        line for line in deploy.splitlines() if "for unit in mooncen-api" in line and "systemctl disable --now" in line
    )

    assert "mooncen-backup.timer" in standby_disable
    assert "mooncen-backup-restore-test.timer" in standby_disable
    assert "mooncen-functional-test.timer" in standby_disable


def test_staging_setup_separates_owner_and_crawler_login():
    staging = _text("deploy/ha/n100_crawler_staging_setup.sh")
    assert 'STAGING_DB_OWNER_USER="${STAGING_DB_OWNER_USER:-mooncen_staging_owner}"' in staging
    assert 'STAGING_DB_USER="${STAGING_DB_USER:-mooncen_crawler_login}"' in staging
    assert "GRANT mooncen_crawler" in staging
    assert "REVOKE ALL PRIVILEGES ON SCHEMA public" in staging
    assert "Staging owner and crawler LOGIN roles must be distinct" in staging
    assert "CREATE ROLE %I NOLOGIN" in staging
    assert "PASSWORD NULL" in staging
    assert "STAGING_DB_OWNER_PASSWORD" not in staging
    assert 'PGPASSWORD="$PRIMARY_DB_PASSWORD"' in staging
    assert "Primary applier login or least-privilege contract verification failed" in staging
    assert 'Environment="PRIMARY_DB_PASSWORD=' not in staging


def test_staging_setup_streams_role_sql_across_the_postgres_permission_boundary():
    staging = _text("deploy/ha/n100_crawler_staging_setup.sh")

    assert 'cat "$APP_DIR/DB/roles.sql" | \\' in staging
    assert '"${PSQL_BASE[@]}" -d "$STAGING_DB_NAME" -v ON_ERROR_STOP=1' in staging
    assert "-f DB/roles.sql" not in staging


def test_staging_setup_reads_only_exact_keys_from_protected_service_envs():
    staging = _text("deploy/ha/n100_crawler_staging_setup.sh")

    assert "APPLIER_ENV_FILE=/etc/mooncen/applier.env" in staging
    assert "CRAWLER_ENV_FILE=/etc/mooncen/crawler.env" in staging
    assert 'if [ "${EUID:-$(id -u)}" -ne 0 ]; then' in staging
    assert "validate_protected_env_file" in staging
    assert '[ ! -f "$file" ] || [ -L "$file" ]' in staging
    assert 'owner="$(stat -c \'%U\' "$file")"' in staging
    assert "8#$mode & 8#022" in staging
    assert 'index($0, wanted "=") == 1' in staging
    assert "Duplicate protected environment key" in staging
    assert "$APP_DIR/.env" not in staging
    assert "source " not in staging

    for key in (
        "PRIMARY_DB_USER",
        "PRIMARY_DB_PASSWORD",
        "CRAWL_STAGING_DB_USER",
        "CRAWL_STAGING_DB_PASSWORD",
        "DB_SSLROOTCERT",
    ):
        assert 'read_protected_env_value "' in staging
        assert key in staging


def test_staging_primary_contract_forces_full_tls_verification():
    staging = _text("deploy/ha/n100_crawler_staging_setup.sh")
    primary_check = staging.split('if ! primary_role_contract="$(', 1)[1].split(
        '")"; then',
        1,
    )[0]

    assert 'PGPASSWORD="$PRIMARY_DB_PASSWORD"' in primary_check
    assert "PGSSLMODE=verify-full" in primary_check
    assert 'PGSSLROOTCERT="$DB_SSLROOTCERT"' in primary_check
    assert "PGCONNECT_TIMEOUT=5" in primary_check
    assert '-h "$PRIMARY_DB_HOST"' in primary_check
    assert "sslmode=require" not in primary_check
    assert "sslmode=prefer" not in primary_check


def test_staging_setup_atomically_converges_only_nonsecret_crawler_routing():
    staging = _text("deploy/ha/n100_crawler_staging_setup.sh")
    converge = staging.split("converge_crawler_staging_env() {", 1)[1].split(
        "\n}\n",
        1,
    )[0]

    for key in (
        "CRAWL_WRITE_MODE",
        "CRAWL_STAGING_DB_HOST",
        "CRAWL_STAGING_DB_PORT",
        "CRAWL_STAGING_DB_NAME",
        "CRAWL_STAGING_DB_USER",
    ):
        assert f'desired["{key}"]' in converge
    for secret_key in (
        "CRAWL_STAGING_DB_PASSWORD",
        "DB_CRAWLER_PASSWORD",
        "PRIMARY_DB_PASSWORD",
    ):
        assert f'desired["{secret_key}"]' not in converge

    assert 'tmp="$(mktemp /etc/mooncen/crawler.env.tmp.XXXXXX)"' in converge
    assert "Duplicate protected environment key" in converge
    assert 'chown root:mooncen-crawler "$tmp"' in converge
    assert 'chmod 0640 "$tmp"' in converge
    assert 'mv -fT -- "$tmp" "$file"' in converge
    assert 'rm -f -- "$tmp"' in converge
    assert staging.index("\nconverge_crawler_staging_env\n") < staging.index("sudo -n mkdir -p")
    assert staging.index("\nconverge_crawler_staging_env\n") > staging.index('if [ "$primary_role_contract" != "t" ]')


def test_skip_workers_keeps_standby_crawler_and_timers_disabled():
    deploy = _text("deploy/ubuntu/deploy_from_windows.ps1")
    standby = deploy.split(
        "if ($Standby) {\n    Invoke-RemoteBashScriptTty",
        1,
    )[1].split("\n} else {", 1)[0]
    skipped = standby.split("if ($SkipWorkers) {", 1)[1].split("\n    } else {", 1)[0]
    enabled = standby.split("\n    } else {", 1)[1]
    units = "mooncen-crawler mooncen-staging-apply.timer mooncen-crawler-watchdog.timer"

    assert f"sudo systemctl disable --now {units}" in skipped
    assert "sudo systemctl enable" not in skipped
    assert f"sudo systemctl enable {units}" in enabled
    assert "sudo systemctl restart mooncen-crawler" in enabled


@pytest.mark.skipif(os.name != "nt" or not shutil.which("powershell"), reason="Windows PowerShell unavailable")
def test_changed_powershell_deployment_scripts_parse():
    files = [
        ROOT / "deploy" / "ubuntu" / "deploy_from_windows.ps1",
        ROOT / "deploy_ubuntu.ps1",
        ROOT / "deploy_mooncen.ps1",
    ]
    for path in files:
        escaped = str(path).replace("'", "''")
        command = (
            "$tokens=$null; $errors=$null; "
            f"[System.Management.Automation.Language.Parser]::ParseFile('{escaped}', [ref]$tokens, [ref]$errors) | Out-Null; "
            "if ($errors.Count) { $errors | ForEach-Object { Write-Error $_.Message }; exit 1 }"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            check=True,
            capture_output=True,
            text=True,
        )
