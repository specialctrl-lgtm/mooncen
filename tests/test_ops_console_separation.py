import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tools import prepare_an2p_ops_control
from tools import setup_an2p_dev_secrets
from deploy.an2p import check_docker_environment


ROOT = Path(__file__).resolve().parents[1]


def test_public_site_does_not_publish_ops_api() -> None:
    config = (ROOT / "deploy/ubuntu/nginx/mooncen.conf").read_text(encoding="utf-8")

    assert "location = /api/ops {" in config
    assert "location ^~ /api/ops/ {" in config
    assert "location ^~ /api/auth/ops/ {" in config
    assert config.count("return 404;") >= 2
    assert "/opt/mooncen-ops-console" not in config


def test_ops_origin_has_its_own_static_root_and_narrow_api_surface() -> None:
    config = (ROOT / "deploy/ops-console/nginx/ops-console.conf.example").read_text(encoding="utf-8")

    assert "server_name ops.mooncen.kr;" in config
    assert "root /opt/mooncen-ops-console/current;" in config
    assert "location = /api/auth/ops/login {" in config
    assert "location ^~ /api/ops/ {" in config
    assert "location /api/ {" not in config
    assert "/api/auth/oauth/" not in config
    assert "try_files $uri $uri/ /index.html;" in config


def test_public_release_keeps_ops_install_disabled() -> None:
    wrapper = (ROOT / "deploy_ubuntu.ps1").read_text(encoding="utf-8")
    deployer = (ROOT / "deploy/ubuntu/deploy_from_windows.ps1").read_text(encoding="utf-8")

    assert "$InstallOpsConsole = $false" in wrapper
    assert "$InstallOpsConsole = $false" in deployer


def _profile_routes(profile: str) -> set[str]:
    environment = os.environ.copy()
    environment.update(
        {
            "ENVIRONMENT": "development",
            "MOONCEN_API_PROFILE": profile,
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json; from fastapi.routing import APIRoute; "
                "from backend.main import app; paths=[]; "
                "[(paths.extend([r.include_context.prefix + x.path for x in r.original_router.routes "
                "if isinstance(x, APIRoute)]) if hasattr(r, 'original_router') else "
                "paths.append(r.path) if isinstance(r, APIRoute) else None) for r in app.routes]; "
                "print(json.dumps(sorted(paths)))"
            ),
        ],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return set(json.loads(completed.stdout))


def test_an2p_public_and_ops_api_profiles_have_separate_route_surfaces() -> None:
    public_routes = _profile_routes("public")
    ops_routes = _profile_routes("ops")

    assert "/api/courses/" in public_routes
    assert "/api/ops/session" not in public_routes
    assert "/api/ops/session" in ops_routes
    assert "/api/courses/" not in ops_routes
    assert "/api/auth/ops/login" in ops_routes
    assert "/api/auth/logout" in ops_routes
    assert "/api/auth/signup" not in ops_routes


def _an2p_unit(name: str) -> str:
    return (ROOT / "deploy/an2p" / name).read_text(encoding="utf-8")


def test_an2p_units_keep_development_and_production_control_planes_separate() -> None:
    development_api = _an2p_unit("mooncen-api.service")
    status_agent = _an2p_unit("mooncen-status-agent.service")
    retired_console = _an2p_unit("mooncen-ops-console.service")
    ops_api = _an2p_unit("mooncen-ops-api.service")
    recovery = _an2p_unit("mooncen-an2p-runtime-recovery.service")
    installer = _an2p_unit("install_isolated_control_plane.sh")

    assert "EnvironmentFile=%h/.config/mooncen-an2p/api.env" in development_api
    assert "mooncen.env" not in development_api
    assert "--reload --host 127.0.0.1 --port 8001" in development_api
    assert "ConditionPathExists=!/etc/mooncen-an2p/docker-development-enabled" in development_api

    assert "EnvironmentFile=%h/.config/mooncen-an2p/status-agent.env" in status_agent
    assert "mooncen.env" not in status_agent
    assert "api.env" not in status_agent
    assert "After=mooncen-development-runtime.target" in status_agent
    assert "Wants=mooncen-development-runtime.target" in status_agent
    assert "mooncen-ops-console.service" not in status_agent
    assert "mooncen-api.service" not in status_agent
    assert "mooncen-frontend.service" not in status_agent
    assert "RefuseManualStart=yes" in retired_console
    assert "ExecStart=/usr/bin/false" in retired_console
    assert "5175" not in retired_console
    assert "8002" not in retired_console
    assert "npm" not in retired_console

    assert "User=mooncen_ops_api" in ops_api
    assert "Group=mooncen_ops_api" in ops_api
    assert "EnvironmentFile=/etc/mooncen-an2p/ops-api.env" in ops_api
    assert "--fd 3" in ops_api
    assert "--port 8002" not in ops_api
    assert "--reload" not in ops_api
    assert (
        "ExecStartPre=+/usr/local/libexec/mooncen-an2p-runtime-manager "
        "gate-service-start mooncen-ops-api.service"
    ) in ops_api
    ops_requires = next(
        line.removeprefix("Requires=").split()
        for line in ops_api.splitlines()
        if line.startswith("Requires=")
    )
    assert {
        "mooncen-an2p-runtime-recovery.service",
        "mooncen-ops-db-tunnel.service",
        "mooncen-ops-api.socket",
    } <= set(ops_requires)
    assert "InaccessiblePaths=-/var/run/docker.sock" in ops_api
    assert "InaccessiblePaths=-/var/lib/mooncen-deployment-worker" in ops_api

    socket_unit = _an2p_unit("mooncen-ops-api.socket")
    assert "ListenStream=127.0.0.1:5175" in socket_unit
    assert "Accept=no" in socket_unit
    assert "WantedBy=sockets.target" in socket_unit
    ipv6_socket = _an2p_unit("mooncen-ops-api-ipv6.socket")
    ipv6_service = _an2p_unit("mooncen-ops-api-ipv6.service")
    assert "ListenStream=[::1]:5175" in ipv6_socket
    assert "Accept=no" in ipv6_socket
    assert "User=mooncen_ops_api" in ipv6_service
    assert "/usr/local/libexec/mooncen-an2p-loopback-redirect" in ipv6_service
    assert "0.0.0.0" not in ipv6_socket
    assert "[::]:5175" not in ipv6_socket
    assert (
        "ExecStart=/usr/local/libexec/mooncen-an2p-runtime-manager recover-boot"
        in recovery
    )
    assert "Type=oneshot" in recovery
    assert "RemainAfterExit=yes" in recovery
    assert "RestrictAddressFamilies=AF_UNIX AF_INET" in recovery
    assert (
        "Before=mooncen-ops-api.service mooncen-deployment-worker.service "
        "mooncen-docker-dev.service"
    ) in recovery

    # The reviewed static UI is served by the trusted Ops API process.  The
    # mutable developer-owned Vite proxy must be retired and globally masked.
    assert "mooncen-ops-console.service" in installer
    assert "systemctl --global mask" in installer
    # User-home publishing is now performed by the fd-relative safe helper;
    # the reviewed native unit set must not include the retired Ops console.
    assert "safe_user_path_helper" in installer
    assert "install_development_runtime.sh" in installer


def test_an2p_docker_runtime_is_selected_without_deleting_lxd_or_docker_data() -> None:
    docker_unit = _an2p_unit("mooncen-docker-dev.service")
    runtime_target = _an2p_unit("mooncen-development-runtime.target")
    native_api = _an2p_unit("mooncen-api.service")
    native_frontend = _an2p_unit("mooncen-frontend.service")
    installer = (ROOT / "deploy/an2p/install_user_services.sh").read_text(encoding="utf-8")
    selector = _an2p_unit("mooncen_an2p_service_control.py")

    assert "WantedBy=default.target" in runtime_target
    assert "WantedBy=multi-user.target" in docker_unit
    assert "User=mooncen_docker_operator" in docker_unit
    assert "SupplementaryGroups=docker" in docker_unit
    assert "DOCKER_HOST=unix:///var/run/docker.sock" in docker_unit
    installed_root = "/opt/mooncen-an2p-docker/current"
    assert "EnvironmentFile=" not in docker_unit
    assert f"--environment-file {installed_root}/development.env" in docker_unit
    assert f"WorkingDirectory={installed_root}" in docker_unit
    assert f"ConditionPathExists={installed_root}/development.env" in docker_unit
    assert "ConditionPathExists=/etc/mooncen-an2p/docker-development-enabled" in docker_unit
    assert "Environment=MOONCEN_RUNTIME_CONFIG_FILE=/var/lib/mooncen-docker-operator/runtime-config.js" in docker_unit
    assert "check_docker_environment.py" in docker_unit
    assert "--system-runtime --reader-group mooncen_docker_operator" in docker_unit
    assert docker_unit.count("render_runtime_config.py") == 2
    assert docker_unit.count("--output /var/lib/mooncen-docker-operator/runtime-config.js") == 2
    assert "up --detach --no-build --pull never --remove-orphans --wait" in docker_unit
    assert docker_unit.count("--force-recreate --no-deps --wait") == 2
    assert docker_unit.count("--force-recreate --no-deps --wait --wait-timeout 240 frontend") == 2
    assert " compose " in docker_unit
    assert " stop --timeout 90" in docker_unit
    assert " down" not in docker_unit
    assert "--volumes" not in docker_unit

    for native_unit in (native_api, native_frontend):
        assert "ConditionPathExists=!/etc/mooncen-an2p/docker-development-enabled" in native_unit
        assert "WantedBy=mooncen-development-runtime.target" in native_unit

    assert "--development-runtime native|docker" in installer
    assert "development_runtime=" in installer
    assert 'if [[ -z "${development_runtime}" ]]' in installer
    assert "development_runtime=docker" in installer
    assert "development_runtime=native" in installer
    assert "/usr/local/libexec/mooncen-an2p-service-control docker-select" in installer
    assert "/usr/local/libexec/mooncen-an2p-service-control native-select" in installer
    assert "for helper in wait_for_an2p_database.py wait_for_an2p_http.py; do" in installer
    assert "%h/.local/share/mooncen-an2p/wait_for_an2p_database.py" in native_api
    assert "mooncen-development-runtime.target" in installer
    assert "mooncen-docker-dev.service" in selector
    assert 'SELECT_ACTIONS = frozenset({"docker-select", "native-select"})' in selector
    assert "docker-start" not in selector
    assert "docker-stop" not in selector
    assert "_set_marker(True)" in selector
    assert selector.index("_set_marker(True)") < selector.index(
        '_systemctl("disable", "--now", *NATIVE_UNITS, user=True)'
    ) < selector.index('_systemctl("enable", "--now", DOCKER_UNIT)')
    assert selector.index('_systemctl("disable", "--now", DOCKER_UNIT)') < selector.index(
        "_set_marker(False)"
    ) < selector.index('_systemctl("enable", "--now", *NATIVE_UNITS, user=True)')
    assert "mooncen-dev-db" not in installer
    assert " lxc " not in installer
    assert "--volumes" not in installer
    assert "down -v" not in installer


def test_an2p_docker_environment_validator_requires_exact_private_modes(
    tmp_path: Path,
) -> None:
    config = tmp_path / "mooncen-docker"
    config.mkdir(mode=0o700)
    environment = config / "development.env"
    environment.write_text("MOONCEN_AUTH_SECRET=test\n", encoding="utf-8")
    environment.chmod(0o600)

    check_docker_environment.validate_environment_file(environment)

    environment.chmod(0o640)
    with pytest.raises(ValueError, match="mode 0600"):
        check_docker_environment.validate_environment_file(environment)

    environment.chmod(0o600)
    config.chmod(0o750)
    with pytest.raises(ValueError, match="mode 0700"):
        check_docker_environment.validate_environment_file(environment)


def test_an2p_deployment_worker_uses_explicit_production_control_environment() -> None:
    database_tunnel = _an2p_unit("mooncen-ops-db-tunnel.service")
    worker = _an2p_unit("mooncen-deployment-worker.service")
    installer = _an2p_unit("install_isolated_control_plane.sh")

    assert "User=mooncen_ops_db_tunnel" in database_tunnel
    assert "-F /etc/mooncen-an2p/db-tunnel/ssh_config" in database_tunnel
    assert "-i /etc/mooncen-an2p/db-tunnel/id_ed25519" in database_tunnel
    assert "-L 127.0.0.1:15432:127.0.0.1:5432 cloud-ops-db" in database_tunnel
    assert "User=mooncen_deployment_worker" in worker
    assert "Group=mooncen_deployment_worker" in worker
    assert "EnvironmentFile=/etc/mooncen-an2p/deployment-worker.env" in worker
    assert "mooncen.env" not in worker
    assert "EnvironmentFile=/etc/mooncen-an2p/ops-api.env" not in worker
    assert "MOONCEN_GIT_EXECUTABLE=/usr/bin/git" in worker
    assert "MOONCEN_POWERSHELL_EXECUTABLE" not in worker
    assert (
        "ExecStartPre=+/usr/local/libexec/mooncen-an2p-runtime-manager "
        "gate-service-start mooncen-deployment-worker.service"
    ) in worker
    worker_requires = next(
        line.removeprefix("Requires=").split()
        for line in worker.splitlines()
        if line.startswith("Requires=")
    )
    assert {
        "mooncen-an2p-runtime-recovery.service",
        "mooncen-ops-db-tunnel.service",
    } <= set(worker_requires)
    assert "ReadWritePaths=/var/lib/mooncen-deployment-worker" in worker
    assert "InaccessiblePaths=-/var/run/docker.sock" in worker
    for unit in (
        "mooncen-ops-db-tunnel.service",
        "mooncen-ops-api.service",
        "mooncen-ops-api.socket",
        "mooncen-deployment-worker.service",
    ):
        assert unit in installer
    assert "mooncen_ops_api" in installer
    assert "mooncen_deployment_worker" in installer
    assert "mooncen_ops_db_tunnel" in installer
    assert "mooncen_docker_operator" in installer
    assert "/var/lib/mooncen-deployment-worker/state" in installer
    assert "/var/lib/mooncen-deployment-worker/releases" in installer


def test_an2p_development_api_and_status_agent_get_least_privilege_environments(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    api_output = tmp_path / "api.env"
    status_output = tmp_path / "status-agent.env"
    monkeypatch.setattr(setup_an2p_dev_secrets, "API_ENV_PATH", api_output)
    monkeypatch.setattr(setup_an2p_dev_secrets, "STATUS_ENV_PATH", status_output)
    master_environment = "\n".join(
        (
            "ENVIRONMENT=development",
            "DB_HOST=127.0.0.1",
            "DB_PORT=5432",
            "DB_NAME=mooncen",
            "DB_USER=mooncen_admin",
            "DB_PASSWORD=owner-password-canary",
            "DB_API_USER=mooncen_api_login",
            "DB_API_PASSWORD=api-password-canary",
            "DB_RUNTIME_USER=mooncen_api_login",
            "DB_RUNTIME_PASSWORD=api-password-canary",
            "OPS_STATUS_DB_USER=mooncen_status_login",
            "OPS_STATUS_DB_PASSWORD=status-password-canary",
            "DB_SSLMODE=disable",
            "DB_CONNECT_TIMEOUT=5",
            "DB_STATEMENT_TIMEOUT_MS=15000",
            "DB_LOCK_TIMEOUT_MS=3000",
            "AUTH_SECRET=auth-secret-canary",
            "MOONCEN_OPS_LOGIN_ID=opsadmin",
            "MOONCEN_OPS_PASSWORD_HASH=ops-hash-canary",
            "MOONCEN_TRUSTED_HOSTS=localhost,127.0.0.1",
            "OAUTH_REDIRECT_URIS=http://localhost:5174/",
            "VITE_SITE_URL=http://localhost:5174",
            "LOG_LEVEL=INFO",
            "",
        )
    )

    setup_an2p_dev_secrets._write_service_environments(master_environment)

    api_values = setup_an2p_dev_secrets._environment_values(api_output.read_text(encoding="utf-8"))
    status_values = setup_an2p_dev_secrets._environment_values(status_output.read_text(encoding="utf-8"))
    assert api_values["ENVIRONMENT"] == "development"
    assert api_values["MOONCEN_API_PROFILE"] == "public"
    assert api_values["DB_API_USER"] == "mooncen_api_login"
    assert api_values["DB_APPLICATION_NAME"] == "mooncen-an2p-dev-api"
    assert set(api_values["MOONCEN_CORS_ORIGINS"].split(",")) == {
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    }
    assert "DB_USER" not in api_values
    assert "DB_PASSWORD" not in api_values
    assert "OPS_STATUS_DB_PASSWORD" not in api_values
    assert "MOONCEN_OPS_LOGIN_ID" not in api_values
    assert "MOONCEN_OPS_PASSWORD_HASH" not in api_values

    assert status_values["ENVIRONMENT"] == "development"
    assert status_values["OPS_STATUS_DB_USER"] == "mooncen_status_login"
    assert status_values["OPS_STATUS_DB_PASSWORD"] == "status-password-canary"
    assert status_values["OPS_STATUS_DEPLOYMENT_CAPABILITY_ENABLED"] == "false"
    assert "DB_API_PASSWORD" not in status_values
    assert "DB_RUNTIME_PASSWORD" not in status_values
    assert "AUTH_SECRET" not in status_values
    assert "MOONCEN_API_PROFILE" not in status_values
    assert api_output.stat().st_mode & 0o777 == 0o600
    assert status_output.stat().st_mode & 0o777 == 0o600


def test_an2p_control_preparation_splits_consumer_secrets_without_logging_them(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    values = {
        "DB_NAME": "prod_catalog_command_canary",
        "DB_API_USER": "mooncen_api_login",
        "DB_API_PASSWORD": "api-password-canary",
        "DB_DEPLOYMENT_WORKER_USER": "mooncen_deployment_worker_login",
        "DB_DEPLOYMENT_WORKER_PASSWORD": "deployment-worker-password-canary",
        "MOONCEN_OPS_LOGIN_ID": "opsadmin",
        "MOONCEN_OPS_PASSWORD_HASH": "ops-hash-canary",
        "OPS_CONTAINER_DEV_TARGET_IDENTITY": "8" * 64,
    }
    monkeypatch.setattr(
        prepare_an2p_ops_control,
        "DEFAULT_RELEASE_ROOT",
        tmp_path / "container-releases",
    )

    ops_auth_secret = "A" * 64
    api_environment, worker_environment = prepare_an2p_ops_control.render_environments(
        values,
        ops_auth_secret=ops_auth_secret,
    )
    assert "ENVIRONMENT=production" in api_environment
    assert "MOONCEN_API_PROFILE=ops" in api_environment
    assert "MOONCEN_AUTH_COOKIE_PREFIX=mooncen_ops" in api_environment
    assert "DB_API_PASSWORD=api-password-canary" in api_environment
    assert f"AUTH_SECRET={ops_auth_secret}" in api_environment
    assert "MOONCEN_OPS_PASSWORD_HASH=ops-hash-canary" in api_environment
    assert "deployment-worker-password-canary" not in api_environment
    assert "OPS_DEPLOY_QUEUE_" not in api_environment
    assert f"OPS_CONTAINER_DEV_TARGET_IDENTITY={'8' * 64}" in api_environment
    assert "MOONCEN_TRUSTED_HOSTS=localhost,127.0.0.1,[::1]" in api_environment
    assert "ops.localhost" not in api_environment

    assert "ENVIRONMENT=production" in worker_environment
    assert "OPS_DEPLOY_QUEUE_DB_HOST=127.0.0.1" in worker_environment
    assert "OPS_DEPLOY_QUEUE_DB_PORT=15432" in worker_environment
    assert "OPS_DEPLOY_QUEUE_DB_USER=mooncen_deployment_worker_login" in worker_environment
    assert "OPS_DEPLOY_QUEUE_DB_PASSWORD=deployment-worker-password-canary" in worker_environment
    assert f"OPS_CONTAINER_DEV_TARGET_IDENTITY={'8' * 64}" in worker_environment
    assert f"OPS_CONTAINER_RELEASE_ROOT={tmp_path / 'container-releases'}" in worker_environment
    assert "DB_CRAWLER_" not in worker_environment
    assert "api-password-canary" not in worker_environment
    assert ops_auth_secret not in worker_environment
    assert "ops-hash-canary" not in worker_environment


@pytest.mark.parametrize(
    "invalid_line",
    [
        "OPS_CONTAINER_DEV_TARGET_IDENTITY=not-a-digest",
        "MOONCEN_OPS_LOGIN_ID=not-the-fixed-admin",
        "UNREVIEWED_SECRET=unexpected",
        "DB_API_USER=mooncen_deployment_worker_login",
        "DB_DEPLOYMENT_WORKER_USER=custom_worker_login",
    ],
)
def test_an2p_control_rejects_invalid_root_staged_values(
    invalid_line: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "control-secrets.env"
    entries = [
        "DB_NAME=mooncen",
        "DB_API_USER=mooncen_api_login",
        "DB_API_PASSWORD=api-secret",
        "DB_DEPLOYMENT_WORKER_USER=mooncen_deployment_worker_login",
        "DB_DEPLOYMENT_WORKER_PASSWORD=worker-secret",
        "MOONCEN_OPS_LOGIN_ID=opsadmin",
        "MOONCEN_OPS_PASSWORD_HASH=ops-hash",
        f"OPS_CONTAINER_DEV_TARGET_IDENTITY={'9' * 64}",
    ]
    name = invalid_line.partition("=")[0]
    entries = [line for line in entries if not line.startswith(f"{name}=")]
    entries.append(invalid_line)
    source.write_text("\n".join((*entries, "")), encoding="utf-8")
    monkeypatch.setattr(
        prepare_an2p_ops_control,
        "_assert_root_private_file",
        lambda _path: source,
    )
    with pytest.raises(prepare_an2p_ops_control.PreparationError):
        prepare_an2p_ops_control.load_protected_values(source)


def test_an2p_control_uses_only_an_independent_local_ops_signing_secret(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    secret_path = tmp_path / "ops-auth-secret"
    local_secret = "S" * 64
    secret_path.write_text(f"{local_secret}\n", encoding="ascii")
    values = {
        "DB_API_PASSWORD": "api-password-canary",
        "DB_DEPLOYMENT_WORKER_PASSWORD": "worker-password-canary",
    }
    monkeypatch.setattr(
        prepare_an2p_ops_control,
        "_assert_root_private_file",
        lambda path: path,
    )

    assert (
        prepare_an2p_ops_control.load_ops_auth_secret(secret_path, values)
        == local_secret
    )

    for invalid in (
        "short\n",
        f"{'S' * 63}!\n",
        f"{'S' * 64}\n\n",
        f"{'S' * 64}\r\n",
        "api-password-canary\n",
    ):
        secret_path.write_text(invalid, encoding="ascii")
        with pytest.raises(prepare_an2p_ops_control.PreparationError):
            prepare_an2p_ops_control.load_ops_auth_secret(secret_path, values)

    values["DB_API_PASSWORD"] = local_secret
    secret_path.write_text(f"{local_secret}\n", encoding="ascii")
    with pytest.raises(
        prepare_an2p_ops_control.PreparationError,
        match="independent",
    ):
        prepare_an2p_ops_control.load_ops_auth_secret(secret_path, values)


def test_an2p_control_rejects_production_auth_secret_in_exported_envelope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "control-secrets.env"
    source.write_text(
        "\n".join(
            (
                "DB_NAME=mooncen",
                "DB_API_PASSWORD=api-secret",
                "DB_DEPLOYMENT_WORKER_PASSWORD=worker-secret",
                "AUTH_SECRET=production-secret-must-not-cross-boundary",
                "MOONCEN_OPS_PASSWORD_HASH=ops-hash",
                f"OPS_CONTAINER_DEV_TARGET_IDENTITY={'9' * 64}",
                "",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        prepare_an2p_ops_control,
        "_assert_root_private_file",
        lambda _path: source,
    )

    with pytest.raises(
        prepare_an2p_ops_control.PreparationError,
        match="envelope is invalid",
    ):
        prepare_an2p_ops_control.load_protected_values(source)


def test_an2p_control_preparation_has_no_network_or_service_key_surface() -> None:
    source = (ROOT / "tools/prepare_an2p_ops_control.py").read_text(encoding="utf-8")

    assert "import subprocess" not in source
    assert "import socket" in source  # hostname binding only
    assert "ssh_config" not in source
    assert "id_ed25519" not in source
    assert "cloud-deploy" not in source
    assert "_fetch_remote_values" not in source
    assert "DEFAULT_BOOTSTRAP_ROOT" in source
    assert '"control-secrets.env"' in source
    assert '"ops-auth-secret"' in source
    assert '"AUTH_SECRET"' not in source.split("REQUIRED_NAMES", 1)[1].split(")", 1)[0]
    assert "Prepared isolated an2p environments without reading or printing a service key." in source


def test_an2p_operator_docs_publish_only_the_trusted_same_origin() -> None:
    readme = (ROOT / "deploy/an2p/README.md").read_text(encoding="utf-8")
    architecture = (ROOT / "docs/an2p-control-plane-architecture.md").read_text(
        encoding="utf-8"
    )

    for document in (readme, architecture):
        assert "http://127.0.0.1:5175/" in document
        assert "[::1]:5175" in document
        assert "ops.localhost" not in document
        assert ":8002" not in document
        assert "VITE_OPS_API_PROXY_TARGET" not in document
        assert "npm run dev" not in document
