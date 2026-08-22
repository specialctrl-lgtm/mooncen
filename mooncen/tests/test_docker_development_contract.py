from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_clean_source_git_supplies_its_own_exact_safe_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = importlib.import_module("deploy.docker.verify_clean_source")
    captured: dict[str, object] = {}

    class Result:
        returncode = 0
        stdout = b""

    def fake_run(command: list[str], **kwargs: object) -> Result:
        captured.update(command=command, kwargs=kwargs)
        return Result()

    monkeypatch.setattr(verifier.subprocess, "run", fake_run)
    assert verifier._git(tmp_path, ("status", "--porcelain")) == b""
    assert f"safe.directory={tmp_path.resolve()}" in captured["command"]
    assert captured["kwargs"]["cwd"] == tmp_path.resolve()


def test_docker_ci_has_secret_scan_native_and_arm64_full_stack_gates() -> None:
    workflow = yaml.safe_load(_read(".github/workflows/docker-development.yml"))

    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {"secret-scan", "compose-smoke", "arm64-build"}
    workflow_text = _read(".github/workflows/docker-development.yml")
    triggers = workflow.get("on", workflow.get(True))
    assert ".gitleaks.toml" in triggers["push"]["paths"]
    assert ".gitleaks.toml" in triggers["pull_request"]["paths"]
    assert workflow_text.count("run: python deploy/docker/verify_clean_source.py") == 2
    assert "python deploy/docker/smoke.py" in workflow_text
    assert "python deploy/docker/smoke.py --platform linux/arm64" in workflow_text
    assert "tonistiigi/binfmt:latest@sha256:" in workflow_text
    assert "platforms: arm64" in workflow_text
    assert "version: v0.36.1" in workflow_text
    assert "moby/buildkit:buildx-stable-1@sha256:" in workflow_text
    for job_name in ("compose-smoke", "arm64-build"):
        job = workflow["jobs"][job_name]
        assert job["needs"] == "secret-scan"
        checkout = next(
            step for step in job["steps"] if step["name"] == "Check out repository"
        )
        assert checkout["with"]["persist-credentials"] is False
        commands = [step["run"] for step in job["steps"] if "run" in step]
        assert commands[0] == "python deploy/docker/verify_clean_source.py"
        assert commands[1].startswith("python deploy/docker/smoke.py")


def test_docker_ci_secret_scan_is_pinned_canaried_and_scans_both_sources() -> None:
    workflow_text = _read(".github/workflows/docker-development.yml")
    workflow = yaml.safe_load(workflow_text)
    secret_job = workflow["jobs"]["secret-scan"]
    steps = {step["name"]: step for step in secret_job["steps"]}

    checkout = steps["Check out full history"]
    assert checkout["with"] == {
        "fetch-depth": 0,
        "persist-credentials": False,
    }

    install = steps["Install verified Gitleaks binary"]["run"]
    assert "releases/download/v8.30.0/gitleaks_8.30.0_linux_x64.tar.gz" in install
    assert (
        "79a3ab579b53f71efd634f3aaf7e04a0fa0cf206b7ed434638d1547a2470a66e"
        in install
    )
    assert "sha256sum --check --strict" in install

    canary = steps["Prove Gitleaks default rules detect a synthetic secret"]["run"]
    assert "set -euo pipefail" in canary
    assert "canary_prefix=\"$(printf '\\x67\\x68\\x70\\x5f')\"" in canary
    assert "\"$canary_prefix\"" in canary
    assert "'fOP5YQKEKPR3SlpzCtaPmDOdfxSBTfgLrTYT'" in canary
    assert "set +e" in canary
    assert "canary_status=$?" in canary
    assert 'if [[ "$canary_status" -ne 1 ]]' in canary
    assert "ghp_" not in workflow_text

    scans = steps["Scan Git history and checked-out files"]["run"]
    assert (
        '"$RUNNER_TEMP/gitleaks" git --config .gitleaks.toml '
        "--redact --no-banner --verbose"
    ) in scans
    assert (
        '"$RUNNER_TEMP/gitleaks" dir --config .gitleaks.toml '
        "--redact --no-banner --verbose ."
    ) in scans
    assert "--pipe" not in workflow_text


def test_smoke_runner_is_ephemeral_and_rejects_remote_daemons() -> None:
    smoke = _read("deploy/docker/smoke.py")

    assert 'project = f"mooncen-smoke-' in smoke
    assert 'environment.get("DOCKER_HOST")' in smoke
    assert "SAFE_LOCAL_CONTEXTS" in smoke
    assert '"down", "--volumes", "--remove-orphans"' in smoke
    assert "secrets.token_urlsafe" in smoke
    assert "EXPECTED_EXTENSIONS" in smoke
    assert "PROTECTED_PATHS" in smoke
    assert '"/api/ops/runtime-metrics"' in smoke
    assert '"/api/auth/ops/login"' in smoke
    assert "/api/courses/?page=1&size=1" in smoke
    assert "/api/branches/providers" in smoke
    assert "/api/auth/oauth/config" in smoke
    assert "/api/auth/me" in smoke
    assert '"{{.HostConfig.ReadonlyRootfs}}"' in smoke
    assert "_enforce_clean_source(allow_dirty_source=allow_dirty_source)" in smoke
    assert '"--allow-dirty-source"' in smoke
    assert "reviewed_crawler_providers" in smoke
    assert "/app/logs/crawler_municipal_yaml.log" in smoke
    assert "CRAWLER_REGISTRY_TIMEOUT_SECONDS = 300" in smoke


def _repository_copy_sources(dockerfile: str) -> set[str]:
    verifier = importlib.import_module("deploy.docker.verify_clean_source")
    return set(verifier.repository_copy_sources(dockerfile))


def test_clean_source_manifest_covers_every_repository_copy_source() -> None:
    verifier = importlib.import_module("deploy.docker.verify_clean_source")
    copy_sources = set()
    for dockerfile in (
        "deploy/docker/api.Dockerfile",
        "deploy/docker/frontend.Dockerfile",
        "deploy/docker/postgres.Dockerfile",
    ):
        copy_sources.update(_repository_copy_sources(_read(dockerfile)))

    monitored = set(verifier.BUILD_INPUT_PATHS)
    assert copy_sources.issubset(monitored)
    assert {
        ".dockerignore",
        ".gitattributes",
        ".gitleaks.toml",
        ".github/workflows/docker-development.yml",
        "compose.yaml",
        "DB/migrations/20260819_001_ops_container_deployment_pipeline.sql",
        "DB/provision_deployment_worker_login.sql",
        "backend/ops/schemas.py",
        "backend/ops/service.py",
        "backend/routers/ops_v2.py",
        "DB/provision_deployment_worker_login.sql",
        "DB/roles.sql",
        "DB/roles_body.sql",
        "deploy/an2p/mooncen-deployment-worker.service",
        "deploy/an2p/mooncen-ops-api.service",
        "deploy/an2p/mooncen-ops-control-env.service",
        "deploy/docker/mooncen_container_release.py",
        "deploy/docker/production_runtime_integrity.py",
        "deploy/docker/verify_clean_source.py",
        "deploy/ubuntu/configure_container_pg_hba.py",
        "deploy/ubuntu/install_sudoers.sh",
        "deploy/ubuntu/nginx/mooncen.conf",
        "deploy/ubuntu/deploy_from_windows.ps1",
        "deploy/ubuntu/mooncen_native_runtime_condition.py",
        "deploy/ubuntu/mooncen_release_guard.sh",
        "deploy/ubuntu/systemd/mooncen-ai-worker.service",
        "deploy/ubuntu/systemd/mooncen-api.service",
        "deploy/ubuntu/systemd/mooncen-container-release-guard@.service",
        "deploy/ubuntu/systemd/mooncen-container-stack.service",
        "deploy/ubuntu/systemd/mooncen-frontend.service",
        "deploy_mooncen.ps1",
        "docs/docker-ops-console.md",
        "ops-console/src/pages/DeploymentsPage.test.tsx",
        "ops-console/src/pages/DeploymentsPage.tsx",
        "ops_agent/container_deployment.py",
        "ops_agent/deployment_registry.py",
        "ops_agent/deployment_worker.py",
        "ops_agent/production_topology.py",
        "tests/test_ai_check_db_roles.py",
        "tests/test_deployment_registry_profiles.py",
        "tests/test_deployment_db_roles.py",
        "tests/test_staging_safety_contract.py",
        "tests/test_docker_clean_source.py",
        "tests/test_docker_development_contract.py",
        "tests/test_docker_production_runtime.py",
        "tests/test_remaining_security_contracts.py",
        "tests/test_ops_container_deployment_pipeline.py",
        "tests/test_ops_deployment_api_gating.py",
        "tests/test_deployment_worker_recovery.py",
        "tests/test_production_topology_contract.py",
        "tools/prepare_an2p_ops_control.py",
        "tools/register_container_deployment_evidence.py",
    }.issubset(set(verifier.REQUIRED_CONTROL_PATHS))
    assert len(verifier.REQUIRED_CONTROL_PATHS) == len(
        set(verifier.REQUIRED_CONTROL_PATHS)
    )
    assert verifier.CONTROL_INPUT_PATHS == (
        "deploy/an2p",
        "deploy/ops-console",
        "ops-console",
    )


def test_api_image_copies_only_the_runtime_imports_required_from_deploy_tree() -> None:
    sources = _repository_copy_sources(_read("deploy/docker/api.Dockerfile"))

    assert "deploy" not in sources
    assert {
        "deploy/docker/provision_api_login.py",
        "deploy/docker/release_manifest.py",
        "deploy/docker/verify_release_bundle.py",
    }.issubset(sources)


def test_compose_orders_database_migration_api_and_frontend() -> None:
    compose = yaml.safe_load(_read("compose.yaml"))
    services = compose["services"]

    assert set(services) == {"postgres", "migrate", "api", "frontend"}
    assert services["migrate"]["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert services["api"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert services["frontend"]["depends_on"]["api"]["condition"] == "service_healthy"


def test_compose_api_is_public_only_and_every_service_has_bounded_resources() -> None:
    services = yaml.safe_load(_read("compose.yaml"))["services"]

    assert services["api"]["environment"]["MOONCEN_API_PROFILE"] == "public"
    expected_limits = {
        "postgres": ("2.0", "2g", 256),
        "migrate": ("2.0", "2g", 256),
        "api": ("2.0", "2g", 256),
        "frontend": ("1.0", "256m", 128),
    }
    for service_name, (cpus, memory, pids) in expected_limits.items():
        service = services[service_name]
        assert service["cpus"] == cpus
        assert service["mem_limit"] == memory
        assert service["pids_limit"] == pids
        assert service["logging"] == {
            "driver": "local",
            "options": {"max-size": "10m", "max-file": "3"},
        }


def test_compose_is_local_only_and_does_not_mount_the_repository() -> None:
    compose = yaml.safe_load(_read("compose.yaml"))
    services = compose["services"]

    assert "ports" not in services["postgres"]
    assert services["api"]["ports"] == ["127.0.0.1:${MOONCEN_API_PORT:-8001}:8001"]
    assert services["frontend"]["ports"] == ["127.0.0.1:${MOONCEN_WEB_PORT:-5174}:8080"]
    for name, service in services.items():
        assert "network_mode" not in service, name
        assert "privileged" not in service, name
        assert all("/var/run/docker.sock" not in str(volume) for volume in service.get("volumes", []))

    assert "/app/logs:size=64m,uid=10001,gid=10001,mode=0700" in services["api"]["tmpfs"]


def test_compose_separates_database_and_frontend_networks() -> None:
    compose = yaml.safe_load(_read("compose.yaml"))
    services = compose["services"]

    assert compose["networks"] == {"data": {"internal": True}, "web": None}
    assert services["postgres"]["networks"] == ["data"]
    assert services["migrate"]["networks"] == ["data"]
    assert services["api"]["networks"] == ["data", "web"]
    assert services["frontend"]["networks"] == ["web"]


def test_runtime_secrets_are_required_and_not_frontend_build_arguments() -> None:
    compose_text = _read("compose.yaml")
    compose = yaml.safe_load(compose_text)
    frontend = compose["services"]["frontend"]

    assert "${MOONCEN_DB_PASSWORD:?" in compose_text
    assert "${MOONCEN_DB_API_PASSWORD:?" in compose_text
    assert "${MOONCEN_AUTH_SECRET:?" in compose_text
    assert set(frontend["build"]) == {"context", "dockerfile"}
    assert frontend["volumes"] == [
        {
            "type": "bind",
            "source": "${MOONCEN_RUNTIME_CONFIG_FILE:-./frontend2/public/runtime-config.js}",
            "target": "/usr/share/nginx/html/runtime-config.js",
            "read_only": True,
            "bind": {"create_host_path": False},
        }
    ]


def test_api_uses_only_the_dedicated_least_privilege_database_login() -> None:
    compose = yaml.safe_load(_read("compose.yaml"))
    api_environment = compose["services"]["api"]["environment"]
    migrate_environment = compose["services"]["migrate"]["environment"]
    migrate_command = "\n".join(compose["services"]["migrate"]["command"])

    assert "DB_USER" not in api_environment
    assert "DB_PASSWORD" not in api_environment
    assert api_environment["DB_API_USER"] == api_environment["DB_RUNTIME_USER"]
    assert api_environment["DB_API_PASSWORD"] == api_environment["DB_RUNTIME_PASSWORD"]
    assert "DB_USER" in migrate_environment
    assert "DB_PASSWORD" in migrate_environment
    assert migrate_environment["DB_API_USER"] != migrate_environment["DB_USER"]
    assert "DB/setup_db.py --mode migrate" in migrate_command
    assert "python -m deploy.docker.provision_api_login" in migrate_command

    provisioner = _read("deploy/docker/provision_api_login.py")
    assert "DB/roles.sql" in provisioner
    assert "mooncen:docker-development:api-login:v1" in provisioner
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE" in provisioner
    assert "NOREPLICATION NOBYPASSRLS" in provisioner
    assert "Refusing to repurpose database role" in provisioner
    assert '[("mooncen_api",)]' in provisioner
    assert "public.courses', 'DELETE'" in provisioner
    assert "'view_count', 'UPDATE'" in provisioner


def test_api_role_provisioner_runs_as_a_module_and_bounds_configuration_errors() -> None:
    environment = os.environ.copy()
    for name in tuple(environment):
        if name.startswith("DB_") or name == "ENVIRONMENT":
            environment.pop(name, None)
    result = subprocess.run(
        [sys.executable, "-m", "deploy.docker.provision_api_login"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.count("\n") == 1
    assert "Docker API role provisioning failed:" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("outcome", ["success", "execute_failure", "open_transaction"])
def test_roles_sql_owns_its_transaction_boundary_and_restores_connection(
    outcome: str,
) -> None:
    provisioner = importlib.import_module("deploy.docker.provision_api_login")

    class Cursor:
        def __init__(self, connection: Connection) -> None:
            self.connection = connection

        def __enter__(self) -> Cursor:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, statement: str) -> None:
            assert statement == "BEGIN; SELECT 1; COMMIT;"
            assert self.connection.autocommit is True
            self.connection.executed = True
            if outcome == "execute_failure":
                self.connection.status = 2
                raise RuntimeError("simulated SQL failure")
            if outcome == "open_transaction":
                self.connection.status = 2

    class Connection:
        def __init__(self) -> None:
            self.autocommit = False
            self.status = provisioner.TRANSACTION_STATUS_IDLE
            self.executed = False
            self.rollbacks = 0

        def cursor(self) -> Cursor:
            return Cursor(self)

        def get_transaction_status(self) -> int:
            return self.status

        def rollback(self) -> None:
            self.rollbacks += 1
            self.status = provisioner.TRANSACTION_STATUS_IDLE

    connection = Connection()
    if outcome == "execute_failure":
        with pytest.raises(RuntimeError, match="simulated SQL failure"):
            provisioner._apply_roles_sql(connection, "BEGIN; SELECT 1; COMMIT;")
    elif outcome == "open_transaction":
        with pytest.raises(provisioner.ProvisioningError, match="did not close"):
            provisioner._apply_roles_sql(connection, "BEGIN; SELECT 1; COMMIT;")
    else:
        provisioner._apply_roles_sql(connection, "BEGIN; SELECT 1; COMMIT;")

    assert connection.executed
    assert connection.autocommit is False
    assert connection.rollbacks == int(outcome != "success")


def test_compose_image_tags_can_be_isolated_per_project() -> None:
    services = yaml.safe_load(_read("compose.yaml"))["services"]

    assert services["postgres"]["image"].startswith("${MOONCEN_POSTGRES_IMAGE:-")
    assert services["migrate"]["image"] == services["api"]["image"]
    assert services["api"]["image"].startswith("${MOONCEN_API_IMAGE:-")
    assert services["frontend"]["image"].startswith("${MOONCEN_FRONTEND_IMAGE:-")


def test_dockerignore_excludes_local_secrets_and_heavy_artifacts() -> None:
    dockerignore = _read(".dockerignore").splitlines()

    required = {
        ".git",
        ".github",
        ".env",
        ".env.*",
        "**/.env",
        "**/.env.*",
        "**/*.env",
        "deploy.local.ps1",
        "config/deploy_servers.json",
        "config/municipal_integrated_reservation_search_results.yaml",
        "Crawler/municipal_geumcheon_gfmc.py",
        "**/.npmrc",
        "**/.pypirc",
        "**/.netrc",
        "**/*.key",
        "**/*.pem",
        "**/*.p8",
        "**/node_modules",
        "**/test-results",
        "venv_*",
        "logs",
    }
    assert required.issubset(set(dockerignore))


def test_images_are_locked_and_run_application_processes_without_root() -> None:
    api = _read("deploy/docker/api.Dockerfile")
    frontend = _read("deploy/docker/frontend.Dockerfile")
    postgres = _read("deploy/docker/postgres.Dockerfile")

    assert "python:3.13.14-slim-bookworm@sha256:" in api
    assert "--require-hashes -r requirements.lock" in api
    assert "USER mooncen" in api
    assert "node:24.18.0-bookworm-slim@sha256:" in frontend
    assert "nginx:1.30.4-alpine@sha256:" in frontend
    assert "npm ci --ignore-scripts --no-audit --fund=false" in frontend
    assert "USER nginx" in frontend
    assert "postgres:16.14-bookworm@sha256:" in postgres
    assert "postgresql-16-postgis-3=3.6.4+dfsg-2.pgdg12+1" in postgres
    assert "postgresql-16-postgis-3-scripts=3.6.4+dfsg-2.pgdg12+1" in postgres
    for extension in ("postgis", "uuid-ossp", "pg_trgm", "pgcrypto"):
        assert f"extension/{extension}.control" in postgres


def test_database_extensions_are_installed_in_the_reviewed_public_schema() -> None:
    expected = {
        "postgis": "CREATE EXTENSION IF NOT EXISTS postgis WITH SCHEMA public;",
        "uuid-ossp": 'CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA public;',
        "pg_trgm": "CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;",
        "pgcrypto": "CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;",
    }
    sql_by_path = {
        "DB/auth_schema.sql": {"pgcrypto"},
        "DB/schema.sql": {"postgis", "uuid-ossp", "pg_trgm"},
        "DB/migrate_current.sql": {"postgis", "uuid-ossp", "pg_trgm"},
        "DB/staging_primary_schema.sql": {"pgcrypto"},
        "DB/staging_schema.sql": {"uuid-ossp", "pgcrypto"},
    }

    for path, extensions in sql_by_path.items():
        sql = _read(path)
        for extension in extensions:
            assert expected[extension] in sql


def test_frontend_image_preserves_allowlisted_repository_relative_notice() -> None:
    frontend = _read("deploy/docker/frontend.Dockerfile")

    assert "WORKDIR /build/frontend2" in frontend
    assert (
        "COPY config/privacy_membership_notice.json "
        "/build/config/privacy_membership_notice.json"
    ) in frontend
    assert "COPY config/ " not in frontend
    assert (
        "COPY --from=build --chown=nginx:nginx /build/frontend2/dist "
        "/usr/share/nginx/html"
    ) in frontend


def test_frontend_image_uses_runtime_public_config_without_build_arguments() -> None:
    frontend = _read("deploy/docker/frontend.Dockerfile")
    public_build_variables = (
        "VITE_SITE_URL",
        "VITE_OAUTH_REDIRECT_URI",
        "VITE_KAKAO_MAPS_JAVASCRIPT_KEY",
        "VITE_GOOGLE_OAUTH_CLIENT_ID",
        "VITE_NAVER_OAUTH_CLIENT_ID",
    )

    assert all(f"ARG {name}" not in frontend for name in public_build_variables)
    assert all(f"ENV {name}" not in frontend for name in public_build_variables)
    assert "frontend2/public/runtime-config.js" in set(
        importlib.import_module("deploy.docker.verify_clean_source").REQUIRED_CONTROL_PATHS
    )


def test_frontend_serves_runtime_config_before_main_without_caching() -> None:
    index = _read("frontend2/index.html")
    nginx = _read("deploy/docker/nginx.conf")
    default_config = _read("frontend2/public/runtime-config.js")

    assert index.index('src="/runtime-config.js"') < index.index('src="/src/main.tsx"')
    assert "location = /runtime-config.js" in nginx
    assert 'no-store, no-cache, must-revalidate, max-age=0' in nginx
    assert "window.__MOONCEN_RUNTIME_CONFIG__ = Object.freeze" in default_config


def test_frontend_proxy_preserves_api_and_server_rendered_seo_routes() -> None:
    nginx = _read("deploy/docker/nginx.conf")

    for route in ("/api/", "/course/", "/category/", "/branch/"):
        assert f"location {route}" in nginx
    assert "location = /_frontend_health" in nginx
    assert "try_files /index.html =503" in nginx
    assert "resolver 127.0.0.11" in nginx
    assert "server api:8001 resolve" in nginx
    assert "try_files $uri $uri/ /index.html" in nginx
    assert "client_max_body_size 10m" in nginx
    assert "location = /api/ops" in nginx
    assert "location ^~ /api/ops/" in nginx
    assert "location = /api/auth/ops" in nginx
    assert "location ^~ /api/auth/ops/" in nginx
    assert 'add_header X-Content-Type-Options "nosniff" always' in nginx
    assert 'add_header X-Frame-Options "DENY" always' in nginx
    assert "add_header Content-Security-Policy" in nginx
    assert "upgrade-insecure-requests" not in nginx
