from __future__ import annotations

import copy
import importlib.util
import json
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path
from types import ModuleType

import pytest

from deploy.docker.release_manifest import (
    create_release_manifest,
    load_json_evidence,
)


ROOT = Path(__file__).resolve().parents[1]


def _release_manifest() -> dict[str, object]:
    return create_release_manifest(
        base_commit="a" * 40,
        source_tree="b" * 40,
        snapshot_commit="c" * 40,
        platform="linux/amd64",
        bundle_sha256="d" * 64,
        compose_sha256="e" * 64,
        build_policy_sha256="f" * 64,
        migration_ledger_sha256="1" * 64,
        images={
            "api": {
                "tag": f"mooncen/api:release-{'b' * 40}",
                "image_id": f"sha256:{'2' * 64}",
            },
            "frontend": {
                "tag": f"mooncen/frontend:release-{'b' * 40}",
                "image_id": f"sha256:{'3' * 64}",
            },
        },
        created_at="2026-08-19T12:00:00Z",
    )


def _load_smoke() -> ModuleType:
    path = ROOT / "deploy" / "docker" / "smoke.py"
    spec = importlib.util.spec_from_file_location("mooncen_docker_smoke", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _valid_compose_model(
    project: str = "mooncen-smoke-1234-test",
    *,
    api_port: int = 18001,
    web_port: int = 15174,
    runtime_config_file: Path = ROOT / "frontend2" / "public" / "runtime-config.js",
) -> dict[str, object]:
    build_context = str(ROOT.resolve())
    return {
        "name": project,
        "services": {
            "postgres": {
                "build": {
                    "context": build_context,
                    "dockerfile": "deploy/docker/postgres.Dockerfile",
                },
                "networks": {"data": None},
                "cpus": 2.0,
                "mem_limit": 2 * 1024**3,
                "pids_limit": 256,
                "logging": {
                    "driver": "local",
                    "options": {"max-file": "3", "max-size": "10m"},
                },
                "volumes": [
                    {
                        "type": "volume",
                        "source": "postgres-data",
                        "target": "/var/lib/postgresql/data",
                        "volume": {},
                    }
                ],
            },
            "migrate": {
                "build": {
                    "context": build_context,
                    "dockerfile": "deploy/docker/api.Dockerfile",
                },
                "networks": {"data": None},
                "cpus": 2.0,
                "mem_limit": 2 * 1024**3,
                "pids_limit": 256,
                "logging": {
                    "driver": "local",
                    "options": {"max-file": "3", "max-size": "10m"},
                },
            },
            "api": {
                "build": {
                    "context": build_context,
                    "dockerfile": "deploy/docker/api.Dockerfile",
                },
                "environment": {"MOONCEN_API_PROFILE": "public"},
                "networks": {"data": None, "web": None},
                "cpus": 2.0,
                "mem_limit": 2 * 1024**3,
                "pids_limit": 256,
                "logging": {
                    "driver": "local",
                    "options": {"max-file": "3", "max-size": "10m"},
                },
                "ports": [
                    {
                        "mode": "ingress",
                        "target": 8001,
                        "published": str(api_port),
                        "protocol": "tcp",
                        "host_ip": "127.0.0.1",
                    }
                ],
            },
            "frontend": {
                "build": {
                    "context": build_context,
                    "dockerfile": "deploy/docker/frontend.Dockerfile",
                },
                "networks": {"web": None},
                "cpus": 1.0,
                "mem_limit": 256 * 1024**2,
                "pids_limit": 128,
                "logging": {
                    "driver": "local",
                    "options": {"max-file": "3", "max-size": "10m"},
                },
                "ports": [
                    {
                        "mode": "ingress",
                        "target": 8080,
                        "published": str(web_port),
                        "protocol": "tcp",
                        "host_ip": "127.0.0.1",
                    }
                ],
                "volumes": [
                    {
                        "type": "bind",
                        "source": str(runtime_config_file.resolve()),
                        "target": "/usr/share/nginx/html/runtime-config.js",
                        "read_only": True,
                        "bind": {"create_host_path": False},
                    }
                ],
            },
        },
        "volumes": {
            "postgres-data": {"name": f"{project}_postgres-data"},
        },
        "networks": {
            "data": {"name": f"{project}_data", "internal": True},
            "web": {"name": f"{project}_web"},
        },
    }


def test_smoke_environment_overrides_local_compose_and_application_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke()
    monkeypatch.setenv("COMPOSE_FILE", "unsafe-compose.yaml")
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "unsafe-project")
    monkeypatch.setenv("COMPOSE_DISABLE_ENV_FILE", "0")
    monkeypatch.setenv("DOCKER_DEFAULT_PLATFORM", "linux/s390x")
    monkeypatch.setenv("MOONCEN_DB_PASSWORD", "do-not-reuse")
    monkeypatch.setenv("MOONCEN_GOOGLE_OAUTH_CLIENT_SECRET", "do-not-reuse")

    environment = smoke._smoke_environment(18001, 15174, "mooncen-smoke-test")

    assert "COMPOSE_FILE" not in environment
    assert "COMPOSE_PROJECT_NAME" not in environment
    assert environment["COMPOSE_DISABLE_ENV_FILE"] == "1"
    assert "DOCKER_DEFAULT_PLATFORM" not in environment
    assert environment["MOONCEN_DB_PASSWORD"] != "do-not-reuse"
    assert environment["MOONCEN_DB_API_PASSWORD"] != environment["MOONCEN_DB_PASSWORD"]
    assert environment["MOONCEN_DB_API_USER"] == "mooncen_smoke_api"
    assert len(environment["MOONCEN_AUTH_SECRET"]) >= 32
    assert environment["MOONCEN_GOOGLE_OAUTH_CLIENT_SECRET"] == ""
    assert environment["MOONCEN_API_PORT"] == "18001"
    assert environment["MOONCEN_WEB_PORT"] == "15174"
    assert environment["MOONCEN_SITE_URL"] == "http://localhost:15174"
    assert environment["MOONCEN_API_IMAGE"] == "mooncen/api:mooncen-smoke-test"

    arm_environment = smoke._smoke_environment(
        18001,
        15174,
        "mooncen-smoke-test-arm",
        platform="linux/arm64",
    )
    assert arm_environment["DOCKER_DEFAULT_PLATFORM"] == "linux/arm64"


def test_compose_model_guard_accepts_only_the_reviewed_local_stack() -> None:
    smoke = _load_smoke()

    smoke._validate_compose_model(
        _valid_compose_model(),
        project="mooncen-smoke-1234-test",
        api_port=18001,
        web_port=15174,
    )


def test_compose_model_guard_accepts_compose_omitted_false_bind_option() -> None:
    smoke = _load_smoke()
    model = _valid_compose_model()
    model["services"]["frontend"]["volumes"][0]["bind"] = {}

    smoke._validate_compose_model(
        model,
        project="mooncen-smoke-1234-test",
        api_port=18001,
        web_port=15174,
    )


@pytest.mark.parametrize(
    "attack",
    (
        "additional_context",
        "bind_docker_socket",
        "build_network_host",
        "cap_add",
        "device",
        "env_file",
        "extends",
        "external_config",
        "external_network",
        "external_secret",
        "external_volume",
        "host_network_mode",
        "include",
        "non_loopback_port",
        "privileged",
        "remote_build_context",
        "removed_resource_limit",
        "unbounded_logging",
        "combined_api_profile",
        "create_host_path",
        "use_api_socket",
        "unexpected_dockerfile",
        "unexpected_build_arg",
        "unexpected_network_attachment",
        "unexpected_service",
    ),
)
def test_compose_model_guard_rejects_unreviewed_host_or_file_access(
    attack: str,
) -> None:
    smoke = _load_smoke()
    model = copy.deepcopy(_valid_compose_model())
    services = model["services"]
    api = services["api"]

    if attack == "additional_context":
        api["build"]["additional_contexts"] = {"private": "../operator-home"}
    elif attack == "bind_docker_socket":
        api["volumes"] = [
            {
                "type": "bind",
                "source": "/var/run/docker.sock",
                "target": "/var/run/docker.sock",
            }
        ]
    elif attack == "build_network_host":
        api["build"]["network"] = "host"
    elif attack == "cap_add":
        api["cap_add"] = ["SYS_ADMIN"]
    elif attack == "device":
        api["devices"] = ["/dev/kvm:/dev/kvm"]
    elif attack == "env_file":
        api["env_file"] = ["../operator-secret.env"]
    elif attack == "extends":
        api["extends"] = {"file": "../external.yaml", "service": "api"}
    elif attack == "external_config":
        model["configs"] = {"operator-config": {"file": "../operator.conf"}}
    elif attack == "external_network":
        model["networks"]["web"]["external"] = True
    elif attack == "external_secret":
        model["secrets"] = {"operator-secret": {"file": "../operator.secret"}}
    elif attack == "external_volume":
        model["volumes"]["postgres-data"]["external"] = True
    elif attack == "host_network_mode":
        api["network_mode"] = "host"
    elif attack == "include":
        model["include"] = ["../external.yaml"]
    elif attack == "non_loopback_port":
        api["ports"][0]["host_ip"] = "0.0.0.0"
    elif attack == "privileged":
        api["privileged"] = True
    elif attack == "remote_build_context":
        api["build"]["context"] = "https://attacker.invalid/repository.git"
    elif attack == "removed_resource_limit":
        api.pop("mem_limit")
    elif attack == "unbounded_logging":
        api["logging"] = {"driver": "json-file", "options": {}}
    elif attack == "combined_api_profile":
        api["environment"]["MOONCEN_API_PROFILE"] = "combined"
    elif attack == "create_host_path":
        services["frontend"]["volumes"][0]["bind"] = {
            "create_host_path": True
        }
    elif attack == "use_api_socket":
        api["use_api_socket"] = True
    elif attack == "unexpected_dockerfile":
        api["build"]["dockerfile"] = "deploy/docker/frontend.Dockerfile"
    elif attack == "unexpected_build_arg":
        api["build"]["args"] = {"AUTH_TOKEN": "never-print-this-model-secret"}
    elif attack == "unexpected_network_attachment":
        api["networks"]["operator"] = None
    elif attack == "unexpected_service":
        services["operator"] = copy.deepcopy(api)
    else:  # pragma: no cover - the parameter list is exhaustive
        raise AssertionError(f"Unknown test mutation: {attack}")

    with pytest.raises(smoke.SmokeFailure) as failure:
        smoke._validate_compose_model(
            model,
            project="mooncen-smoke-1234-test",
            api_port=18001,
            web_port=15174,
        )
    assert "never-print-this-model-secret" not in str(failure.value)


def test_compose_model_guard_uses_canonical_json_without_leaking_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke()
    secret = "never-print-this-compose-secret"
    commands: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(
        command: list[str] | tuple[str, ...],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        commands.append((list(command), kwargs))
        return subprocess.CompletedProcess(command, 0, f'{{"secret":"{secret}",', "")

    monkeypatch.setattr(smoke, "_run", fake_run)
    with pytest.raises(smoke.SmokeFailure) as failure:
        smoke._guard_compose_model(
            ["docker", "compose", "--project-name", "mooncen-smoke-test"],
            environment={"MOONCEN_DB_PASSWORD": secret},
            project="mooncen-smoke-test",
            api_port=18001,
            web_port=15174,
    )

    assert secret not in str(failure.value)
    assert len(commands) == 1
    command, kwargs = commands[0]
    assert command[-5:] == [
        "mooncen-smoke-test",
        "config",
        "--format",
        "json",
        "--no-env-resolution",
    ]
    assert kwargs["capture"] is True


def test_reserved_smoke_ports_are_distinct_and_released() -> None:
    smoke = _load_smoke()

    ports = smoke._reserve_local_ports()

    assert len(ports) == len(set(ports)) == 2
    for port in ports:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.bind(("127.0.0.1", port))
        finally:
            listener.close()


def test_remote_docker_daemon_is_rejected_before_context_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke()
    probed = False

    def unexpected_probe(*_args: object, **_kwargs: object) -> str:
        nonlocal probed
        probed = True
        return "default"

    monkeypatch.setattr(smoke, "_captured_text", unexpected_probe)

    with pytest.raises(smoke.SmokeFailure, match="DOCKER_HOST is set"):
        smoke._validate_local_daemon(
            {"DOCKER_HOST": "ssh://production.invalid"}, allow_nonlocal=False
        )
    assert not probed


def test_missing_docker_cli_has_a_bounded_operator_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke()
    monkeypatch.setattr(
        smoke,
        "_captured_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError()),
    )

    with pytest.raises(smoke.SmokeFailure, match="Docker CLI is not installed"):
        smoke._validate_local_daemon({}, allow_nonlocal=False)


def test_approved_context_with_remote_endpoint_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke()
    responses = iter(("desktop-linux", "ssh://production.invalid"))
    monkeypatch.setattr(
        smoke,
        "_captured_text",
        lambda *_args, **_kwargs: next(responses),
    )

    with pytest.raises(smoke.SmokeFailure, match="uses non-local endpoint"):
        smoke._validate_local_daemon({}, allow_nonlocal=False)


def test_approved_context_with_local_endpoint_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke()
    responses = iter(("desktop-linux", "npipe:////./pipe/dockerDesktopLinuxEngine", "linux/amd64"))
    monkeypatch.setattr(
        smoke,
        "_captured_text",
        lambda *_args, **_kwargs: next(responses),
    )

    assert smoke._validate_local_daemon({}, allow_nonlocal=False) == "linux/amd64"


@pytest.mark.parametrize(
    "version",
    (
        "2.35.0",
        "v2.35.1-desktop.1",
        "2.40.3+ds1-0ubuntu1~24.04.1",
        "5.3.1",
    ),
)
def test_supported_compose_version_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
    version: str,
) -> None:
    smoke = _load_smoke()
    monkeypatch.setattr(smoke, "_captured_text", lambda *_args, **_kwargs: version)

    smoke._validate_compose_version({})


@pytest.mark.parametrize("version", ("2.34.0", "1.29.2", "not-a-version"))
def test_unsupported_compose_version_has_a_bounded_operator_error(
    monkeypatch: pytest.MonkeyPatch,
    version: str,
) -> None:
    smoke = _load_smoke()
    monkeypatch.setattr(smoke, "_captured_text", lambda *_args, **_kwargs: version)

    with pytest.raises(smoke.SmokeFailure, match=r"Compose v2\.35\.0 or newer"):
        smoke._validate_compose_version({})


def test_invalid_json_response_has_a_bounded_operator_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke()

    class FakeHeaders:
        @staticmethod
        def get_content_type() -> str:
            return "application/json"

    class FakeResponse:
        status = 200
        headers = FakeHeaders()

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        @staticmethod
        def read() -> bytes:
            return b"not-json"

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: FakeResponse())

    with pytest.raises(smoke.SmokeFailure, match="Expected valid UTF-8 JSON"):
        smoke._http_json("http://127.0.0.1:18001/api/courses/")


def test_dirty_source_escape_hatch_warns_and_does_not_run_verifier(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    smoke = _load_smoke()
    monkeypatch.setattr(
        smoke.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("source verifier must be bypassed")
        ),
    )

    smoke._enforce_clean_source(allow_dirty_source=True)

    warning = capsys.readouterr().err
    assert "WARNING: --allow-dirty-source" in warning
    assert "not clean-clone evidence" in warning


def test_source_gate_fails_before_ports_or_docker_daemon_are_touched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke()
    monkeypatch.setattr(
        smoke,
        "_enforce_clean_source",
        lambda *, allow_dirty_source: (_ for _ in ()).throw(
            smoke.SmokeFailure("source is dirty")
        ),
    )
    monkeypatch.setattr(
        smoke,
        "_reserve_local_ports",
        lambda: (_ for _ in ()).throw(AssertionError("ports must not be reserved")),
    )
    monkeypatch.setattr(
        smoke,
        "_validate_local_daemon",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Docker daemon must not be probed")
        ),
    )

    with pytest.raises(smoke.SmokeFailure, match="source is dirty"):
        smoke.run_smoke(keep_on_failure=False, allow_nonlocal=False)


def test_smoke_orchestrates_isolated_checks_and_always_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke()
    commands: list[list[str]] = []
    http_status_urls: list[str] = []
    runtime_config_paths: list[Path] = []
    runtime_config_scripts: list[str] = []
    project = "mooncen-smoke-1234-test"

    monkeypatch.setattr(smoke, "_enforce_clean_source", lambda **_kwargs: None)
    monkeypatch.setattr(smoke, "_reserve_local_ports", lambda: [18001, 15174])
    monkeypatch.setattr(smoke.os, "getpid", lambda: 1234)
    monkeypatch.setattr(smoke.secrets, "token_hex", lambda _size: "test")
    monkeypatch.setattr(
        smoke,
        "_validate_local_daemon",
        lambda _environment, *, allow_nonlocal: "linux/amd64",
    )
    monkeypatch.setattr(smoke, "_validate_compose_version", lambda _environment: None)
    monkeypatch.setattr(smoke, "_wait_for_health", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        smoke,
        "_http_headers",
        lambda *_args, **_kwargs: {
            "x-content-type-options": "nosniff",
            "x-frame-options": "DENY",
            "referrer-policy": "strict-origin-when-cross-origin",
            "content-security-policy": "default-src 'self'",
            "permissions-policy": "camera=()",
            "cache-control": "no-store, no-cache, must-revalidate, max-age=0",
        },
    )
    monkeypatch.setattr(
        smoke,
        "_http_text",
        lambda *_args, **_kwargs: smoke.render_javascript(
            smoke.public_config(
                {
                    "MOONCEN_SITE_URL": "http://localhost:15174",
                    "MOONCEN_OAUTH_REDIRECT_URI": "http://localhost:15174/",
                    "MOONCEN_KAKAO_MAPS_JAVASCRIPT_KEY": "",
                    "MOONCEN_GOOGLE_OAUTH_CLIENT_ID": "",
                    "MOONCEN_NAVER_OAUTH_CLIENT_ID": "",
                }
            )
        ),
    )
    def fake_http_status(url: str, **_kwargs: object) -> int:
        http_status_urls.append(url)
        return (
            404
            if any(path in url for path in smoke.PROTECTED_PATHS)
            else 401
            if url.endswith("/api/auth/me")
            else 200
        )

    monkeypatch.setattr(smoke, "_http_status", fake_http_status)
    monkeypatch.setattr(
        smoke,
        "_http_json",
        lambda url, **_kwargs: (
            {"items": [], "total": 0, "page": 1, "size": 1}
            if "/api/courses/" in url
            else []
            if url.endswith("/api/branches/providers")
            else {"providers": []}
        ),
    )

    def fake_run(
        command: list[str] | tuple[str, ...],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        command_list = list(command)
        commands.append(command_list)
        if "config" in command_list and "--format" in command_list:
            runtime_config_file = Path(
                str(_kwargs["environment"]["MOONCEN_RUNTIME_CONFIG_FILE"])
            )
            runtime_config_paths.append(runtime_config_file)
            runtime_config_scripts.append(runtime_config_file.read_text(encoding="utf-8"))
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps(
                    _valid_compose_model(
                        project,
                        runtime_config_file=runtime_config_file,
                    )
                ),
                "",
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    def fake_capture(command: list[str], **_kwargs: object) -> str:
        commands.append(list(command))
        if "exec" in command and "postgres" in command:
            return "\n".join(smoke.EXPECTED_EXTENSIONS)
        if "exec" in command and "api" in command:
            return json.dumps(
                {
                    "owner_user_env_absent": True,
                    "owner_password_env_absent": True,
                    "runtime_user_is_api_user": True,
                    "runtime_password_is_api_password": True,
                    "current_user": "mooncen_smoke_api",
                    "session_user": "mooncen_smoke_api",
                    "rolcanlogin": True,
                    "rolinherit": True,
                    "rolsuper": False,
                    "rolcreatedb": False,
                    "rolcreaterole": False,
                    "rolreplication": False,
                    "rolbypassrls": False,
                    "database_connect": True,
                    "database_create": False,
                    "schema_create": False,
                    "courses_select": True,
                    "courses_delete": False,
                    "view_count_update": True,
                    "title_update": False,
                    "view_count_update_statement": True,
                    "courses_delete_statement_denied": True,
                    "public_create_statement_denied": True,
                }
            )
        if "ps" in command:
            return f"{command[-1]}-container"
        if command[:2] == ["docker", "inspect"]:
            template = command[-2]
            container = command[-1]
            if template == "{{.Config.User}}":
                return "nginx" if container.startswith("frontend-") else "mooncen"
            if template == "{{.HostConfig.ReadonlyRootfs}}":
                return "true"
            if template == "{{json .NetworkSettings.Networks}}":
                project = container.split("-container", maxsplit=1)[0]
                if project == "api":
                    return (
                        '{"mooncen-smoke-1234-test_data":{},'
                        '"mooncen-smoke-1234-test_web":{}}'
                    )
                suffix = "web" if project == "frontend" else "data"
                return f'{{"mooncen-smoke-1234-test_{suffix}":{{}}}}'
        if command[:3] == ["docker", "network", "inspect"]:
            return "true"
        raise AssertionError(f"Unexpected captured command: {command!r}")

    monkeypatch.setattr(smoke, "_run", fake_run)
    monkeypatch.setattr(smoke, "_captured_text", fake_capture)

    smoke.run_smoke(keep_on_failure=False, allow_nonlocal=False)

    assert sum("run" in command and "migrate" in command for command in commands) == 2
    build_commands = [command for command in commands if "build" in command]
    assert [command[-2:] for command in build_commands] == [
        ["build", "postgres"],
        ["build", "api"],
        ["build", "frontend"],
    ]
    config_command = next(command for command in commands if "config" in command)
    assert config_command[-4:] == [
        "config",
        "--format",
        "json",
        "--no-env-resolution",
    ]
    assert "--file" in config_command
    assert Path(config_command[config_command.index("--file") + 1]) == ROOT / "compose.yaml"
    assert commands.index(config_command) < min(
        commands.index(command) for command in build_commands
    )
    up_command = next(command for command in commands if "up" in command)
    assert up_command[-3:] == ["up", "--detach", "--no-build"]
    assert max(commands.index(command) for command in build_commands) < commands.index(
        up_command
    )
    assert any("exec" in command and "api" in command for command in commands)
    assert any(
        "reviewed_crawler_providers" in " ".join(command)
        and "/app/logs" in " ".join(command)
        for command in commands
    )
    down_command = next(command for command in commands if "down" in command)
    assert down_command[-2:] == ["--volumes", "--remove-orphans"]
    assert "image" in commands[-1] and "rm" in commands[-1]
    assert any(f"{project}_data" in command for command in commands)
    assert len(runtime_config_paths) == 1
    assert not runtime_config_paths[0].exists()
    assert "http://localhost:15174" in runtime_config_scripts[0]
    assert "Object.freeze" in runtime_config_scripts[0]
    for port in (18001, 15174):
        for path in smoke.PROTECTED_PATHS:
            assert f"http://127.0.0.1:{port}{path}" in http_status_urls


def test_failed_build_quietly_cleans_only_random_smoke_image_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke()
    calls: list[tuple[list[str], dict[str, object]]] = []
    project = "mooncen-smoke-1234-test"

    monkeypatch.setattr(smoke, "_enforce_clean_source", lambda **_kwargs: None)
    monkeypatch.setattr(smoke, "_reserve_local_ports", lambda: [18001, 15174])
    monkeypatch.setattr(smoke.os, "getpid", lambda: 1234)
    monkeypatch.setattr(smoke.secrets, "token_hex", lambda _size: "test")
    monkeypatch.setattr(
        smoke,
        "_validate_local_daemon",
        lambda _environment, *, allow_nonlocal: "linux/amd64",
    )
    monkeypatch.setattr(smoke, "_validate_compose_version", lambda _environment: None)

    def fake_run(
        command: list[str] | tuple[str, ...],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        command_list = list(command)
        calls.append((command_list, kwargs))
        if "config" in command_list and "--format" in command_list:
            runtime_config_file = Path(
                str(kwargs["environment"]["MOONCEN_RUNTIME_CONFIG_FILE"])
            )
            return subprocess.CompletedProcess(
                command_list,
                0,
                json.dumps(
                    _valid_compose_model(
                        project,
                        runtime_config_file=runtime_config_file,
                    )
                ),
                "",
            )
        if command_list[-2:] == ["build", "api"]:
            raise subprocess.CalledProcessError(1, command_list)
        returncode = 1 if command_list[:3] == ["docker", "image", "rm"] else 0
        return subprocess.CompletedProcess(command_list, returncode, "", "not found")

    monkeypatch.setattr(smoke, "_run", fake_run)

    with pytest.raises(smoke.SmokeFailure):
        smoke.run_smoke(keep_on_failure=False, allow_nonlocal=False)

    image_command, image_kwargs = next(
        call for call in calls if call[0][:3] == ["docker", "image", "rm"]
    )
    assert image_command == [
        "docker",
        "image",
        "rm",
        f"mooncen/postgres:{project}",
        f"mooncen/api:{project}",
        f"mooncen/frontend:{project}",
    ]
    assert image_kwargs["capture"] is True
    assert image_kwargs["check"] is False


def test_failed_compose_up_emits_bounded_diagnostics_before_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke()
    calls: list[list[str]] = []
    project = "mooncen-smoke-1234-test"

    monkeypatch.setattr(smoke, "_enforce_clean_source", lambda **_kwargs: None)
    monkeypatch.setattr(smoke, "_reserve_local_ports", lambda: [18001, 15174])
    monkeypatch.setattr(smoke.os, "getpid", lambda: 1234)
    monkeypatch.setattr(smoke.secrets, "token_hex", lambda _size: "test")
    monkeypatch.setattr(
        smoke,
        "_validate_local_daemon",
        lambda _environment, *, allow_nonlocal: "linux/amd64",
    )
    monkeypatch.setattr(smoke, "_validate_compose_version", lambda _environment: None)

    def fake_run(
        command: list[str] | tuple[str, ...],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        command_list = list(command)
        calls.append(command_list)
        if "config" in command_list and "--format" in command_list:
            runtime_config_file = Path(
                str(kwargs["environment"]["MOONCEN_RUNTIME_CONFIG_FILE"])
            )
            return subprocess.CompletedProcess(
                command_list,
                0,
                json.dumps(
                    _valid_compose_model(
                        project,
                        runtime_config_file=runtime_config_file,
                    )
                ),
                "",
            )
        if "up" in command_list:
            raise subprocess.CalledProcessError(1, command_list)
        return subprocess.CompletedProcess(command_list, 0, "", "")

    monkeypatch.setattr(smoke, "_run", fake_run)

    with pytest.raises(smoke.SmokeFailure):
        smoke.run_smoke(keep_on_failure=False, allow_nonlocal=False)

    up_index = next(index for index, command in enumerate(calls) if "up" in command)
    ps_index = next(index for index, command in enumerate(calls) if "ps" in command)
    logs_index = next(index for index, command in enumerate(calls) if "logs" in command)
    down_index = next(index for index, command in enumerate(calls) if "down" in command)
    assert up_index < ps_index < logs_index < down_index
    assert calls[logs_index][-3:] == ["logs", "--no-color", "--tail=200"]


def test_explicit_platform_is_checked_after_each_image_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke()
    captures: list[list[str]] = []

    monkeypatch.setattr(smoke, "_enforce_clean_source", lambda **_kwargs: None)
    monkeypatch.setattr(smoke, "_reserve_local_ports", lambda: [18001, 15174])
    monkeypatch.setattr(smoke.os, "getpid", lambda: 1234)
    monkeypatch.setattr(smoke.secrets, "token_hex", lambda _size: "platform")
    monkeypatch.setattr(
        smoke,
        "_validate_local_daemon",
        lambda _environment, *, allow_nonlocal: "linux/amd64",
    )
    monkeypatch.setattr(smoke, "_validate_compose_version", lambda _environment: None)

    def fake_run(
        command: list[str] | tuple[str, ...],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        command_list = list(command)
        if "config" in command_list and "--format" in command_list:
            project_index = command_list.index("--project-name") + 1
            runtime_config_file = Path(
                str(kwargs["environment"]["MOONCEN_RUNTIME_CONFIG_FILE"])
            )
            return subprocess.CompletedProcess(
                command_list,
                0,
                json.dumps(
                    _valid_compose_model(
                        command_list[project_index],
                        runtime_config_file=runtime_config_file,
                    )
                ),
                "",
            )
        if command_list[-2:] == ["build", "frontend"]:
            raise subprocess.CalledProcessError(1, command_list)
        return subprocess.CompletedProcess(command_list, 0, "", "")

    def fake_capture(command: list[str], **_kwargs: object) -> str:
        captures.append(command)
        return "linux/arm64"

    monkeypatch.setattr(smoke, "_run", fake_run)
    monkeypatch.setattr(smoke, "_captured_text", fake_capture)

    with pytest.raises(smoke.SmokeFailure):
        smoke.run_smoke(
            keep_on_failure=False,
            allow_nonlocal=False,
            platform="linux/arm64",
        )

    assert len(captures) == 2
    assert all(command[:3] == ["docker", "image", "inspect"] for command in captures)


def test_release_migration_plan_must_be_current_and_digest_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke()
    release = _release_manifest()
    compose = ["docker", "compose", "--project-name", "release-test"]
    captured: list[list[str]] = []

    def valid_capture(command: list[str], **_kwargs: object) -> str:
        captured.append(command)
        return json.dumps(
            {
                "schema_version": 1,
                "current": True,
                "pending": [],
                "expected_ledger_sha256": "1" * 64,
                "applied_ledger_sha256": "1" * 64,
            }
        )

    monkeypatch.setattr(smoke, "_captured_text", valid_capture)
    smoke._validate_release_migration_plan(
        compose,
        environment={},
        release=release,
    )
    assert captured == [
        [
            *compose,
            "run",
            "--rm",
            "migrate",
            "python",
            "DB/setup_db.py",
            "--mode",
            "plan",
            "--json",
            "--require-current",
        ]
    ]

    monkeypatch.setattr(
        smoke,
        "_captured_text",
        lambda *_args, **_kwargs: json.dumps(
            {
                "schema_version": 1,
                "current": True,
                "pending": [],
                "expected_ledger_sha256": "4" * 64,
                "applied_ledger_sha256": "4" * 64,
            }
        ),
    )
    with pytest.raises(smoke.SmokeFailure, match="migration ledger"):
        smoke._validate_release_migration_plan(
            compose,
            environment={},
            release=release,
        )


def test_release_container_must_run_exact_manifest_image_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke()
    release = _release_manifest()
    monkeypatch.setattr(
        smoke,
        "_captured_text",
        lambda command, **_kwargs: f"{command[-1]}-container",
    )

    def inspect(container_id: str, _template: str, **_kwargs: object) -> str:
        if container_id.startswith("frontend"):
            return str(release["images"]["frontend"]["image_id"])
        return str(release["images"]["api"]["image_id"])

    monkeypatch.setattr(smoke, "_inspect_value", inspect)
    smoke._validate_release_container_images(
        ["docker", "compose"],
        environment={},
        release=release,
    )

    monkeypatch.setattr(
        smoke,
        "_inspect_value",
        lambda *_args, **_kwargs: f"sha256:{'9' * 64}",
    )
    with pytest.raises(smoke.SmokeFailure, match="image ID"):
        smoke._validate_release_container_images(
            ["docker", "compose"],
            environment={},
            release=release,
        )


def test_development_target_identity_binds_host_platform_and_validation_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke()
    monkeypatch.setattr(smoke, "ROOT", tmp_path)
    for index, relative in enumerate(smoke.DEVELOPMENT_VALIDATION_POLICY_PATHS):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"policy-{index}\n", encoding="utf-8")

    identity = smoke._development_target_identity(
        hostname="AN2P",
        platform="linux/x86_64",
    )
    assert len(identity) == 64
    assert identity == smoke._development_target_identity(
        hostname="an2p",
        platform="linux/amd64",
    )
    assert identity != smoke._development_target_identity(
        hostname="other-host",
        platform="linux/amd64",
    )

    policy = tmp_path / smoke.DEVELOPMENT_VALIDATION_POLICY_PATHS[-1]
    policy.write_text("changed\n", encoding="utf-8")
    assert identity != smoke._development_target_identity(
        hostname="an2p",
        platform="linux/amd64",
    )


def test_target_identity_cli_does_not_start_or_build_containers(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    smoke = _load_smoke()
    calls: list[str] = []
    monkeypatch.setattr(
        smoke.sys,
        "argv",
        ["smoke.py", "--print-development-target-identity"],
    )
    monkeypatch.setattr(
        smoke,
        "_enforce_clean_source",
        lambda **_kwargs: calls.append("source"),
    )
    monkeypatch.setattr(
        smoke,
        "_validate_local_daemon",
        lambda *_args, **_kwargs: "linux/x86_64",
    )
    monkeypatch.setattr(
        smoke,
        "_validate_compose_version",
        lambda *_args, **_kwargs: calls.append("compose"),
    )
    monkeypatch.setattr(
        smoke,
        "_development_target_identity",
        lambda **_kwargs: "7" * 64,
    )
    monkeypatch.setattr(
        smoke,
        "run_smoke",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not start smoke")),
    )

    assert smoke.main() == 0
    assert capsys.readouterr().out.strip() == "7" * 64
    assert calls == ["source", "compose"]


def test_prebuilt_release_smoke_never_rebuilds_or_removes_release_images_and_writes_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke()
    release = _release_manifest()
    release_directory = tmp_path / "release"
    release_directory.mkdir(mode=0o700)
    receipt_path = release_directory / "validation.json"
    commands: list[tuple[list[str], dict[str, object]]] = []
    verified: list[tuple[Path, bool]] = []

    monkeypatch.setattr(smoke, "_enforce_clean_source", lambda **_kwargs: None)
    monkeypatch.setattr(smoke, "load_json_evidence", lambda _path: release)
    monkeypatch.setattr(smoke, "_attest_release_checkout", lambda _release: None)
    monkeypatch.setattr(smoke, "_reserve_local_ports", lambda: [18001, 15174])
    monkeypatch.setattr(smoke.os, "getpid", lambda: 1234)
    monkeypatch.setattr(smoke.secrets, "token_hex", lambda _size: "release")
    monkeypatch.setattr(
        smoke,
        "_validate_local_daemon",
        lambda _environment, *, allow_nonlocal: "linux/x86_64",
    )
    monkeypatch.setattr(smoke, "_validate_compose_version", lambda _environment: None)

    def fake_verify(directory: Path, *, load_images: bool) -> dict[str, object]:
        verified.append((directory, load_images))
        return {"release_digest": release["release_digest"]}

    monkeypatch.setattr(
        smoke,
        "_development_target_identity",
        lambda **_kwargs: "4" * 64,
    )
    monkeypatch.setattr(smoke, "verify_release_directory", fake_verify)
    monkeypatch.setattr(smoke, "_guard_compose_model", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(smoke, "_validate_release_migration_plan", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(smoke, "_validate_release_container_images", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(smoke, "_wait_for_health", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        smoke,
        "_http_headers",
        lambda *_args, **_kwargs: {
            "x-content-type-options": "nosniff",
            "x-frame-options": "DENY",
            "referrer-policy": "strict-origin-when-cross-origin",
            "content-security-policy": "default-src 'self'",
            "permissions-policy": "camera=()",
            "cache-control": "no-store",
        },
    )
    monkeypatch.setattr(
        smoke,
        "_http_text",
        lambda *_args, **_kwargs: smoke.render_javascript(
            smoke.public_config(
                {
                    "MOONCEN_SITE_URL": "http://localhost:15174",
                    "MOONCEN_OAUTH_REDIRECT_URI": "http://localhost:15174/",
                    "MOONCEN_KAKAO_MAPS_JAVASCRIPT_KEY": "",
                    "MOONCEN_GOOGLE_OAUTH_CLIENT_ID": "",
                    "MOONCEN_NAVER_OAUTH_CLIENT_ID": "",
                }
            )
        ),
    )
    monkeypatch.setattr(
        smoke,
        "_http_status",
        lambda url, **_kwargs: (
            404
            if any(path in url for path in smoke.PROTECTED_PATHS)
            else 401
            if url.endswith("/api/auth/me")
            else 200
        ),
    )
    monkeypatch.setattr(
        smoke,
        "_http_json",
        lambda url, **_kwargs: (
            {"items": [], "total": 0}
            if "/api/courses/" in url
            else []
            if url.endswith("/api/branches/providers")
            else {}
        ),
    )
    monkeypatch.setattr(
        smoke,
        "_api_database_contract",
        lambda *_args, **_kwargs: {
            "owner_user_env_absent": True,
            "owner_password_env_absent": True,
            "runtime_user_is_api_user": True,
            "runtime_password_is_api_password": True,
            "current_user": "mooncen_smoke_api",
            "session_user": "mooncen_smoke_api",
            "rolcanlogin": True,
            "rolinherit": True,
            "rolsuper": False,
            "rolcreatedb": False,
            "rolcreaterole": False,
            "rolreplication": False,
            "rolbypassrls": False,
            "database_connect": True,
            "database_create": False,
            "schema_create": False,
            "courses_select": True,
            "courses_delete": False,
            "view_count_update": True,
            "title_update": False,
            "view_count_update_statement": True,
            "courses_delete_statement_denied": True,
            "public_create_statement_denied": True,
        },
    )

    def fake_run(
        command: list[str] | tuple[str, ...],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        command_list = list(command)
        commands.append((command_list, kwargs))
        return subprocess.CompletedProcess(command_list, 0, "", "")

    def fake_capture(command: list[str], **_kwargs: object) -> str:
        if command[:3] == ["docker", "image", "inspect"]:
            return "linux/amd64"
        if "exec" in command and "postgres" in command:
            return "\n".join(smoke.EXPECTED_EXTENSIONS)
        if "ps" in command:
            return f"{command[-1]}-container"
        if command[:3] == ["docker", "network", "inspect"]:
            return "true"
        if command[:2] == ["docker", "inspect"]:
            template = command[-2]
            container = command[-1]
            if template == "{{.Config.User}}":
                return "nginx" if container.startswith("frontend") else "mooncen"
            if template == "{{.HostConfig.ReadonlyRootfs}}":
                return "true"
            if template == "{{json .NetworkSettings.Networks}}":
                if container.startswith("api"):
                    return json.dumps(
                        {
                            "mooncen-smoke-1234-release_data": {},
                            "mooncen-smoke-1234-release_web": {},
                        }
                    )
                suffix = "web" if container.startswith("frontend") else "data"
                return json.dumps({f"mooncen-smoke-1234-release_{suffix}": {}})
        raise AssertionError(command)

    monkeypatch.setattr(smoke, "_run", fake_run)
    monkeypatch.setattr(smoke, "_captured_text", fake_capture)

    smoke.run_smoke(
        keep_on_failure=False,
        allow_nonlocal=False,
        release_directory=release_directory,
        receipt_output=receipt_path,
        validation_target="an2p-dev",
        target_identity="4" * 64,
    )

    receipt = load_json_evidence(receipt_path, receipt=True)
    assert receipt["status"] == "passed"
    assert receipt["release_digest"] == release["release_digest"]
    assert receipt["image_ids"] == {
        "api": release["images"]["api"]["image_id"],
        "frontend": release["images"]["frontend"]["image_id"],
    }
    assert verified == [(release_directory, True)]
    build_commands = [command for command, _kwargs in commands if "build" in command]
    assert [command[-2:] for command in build_commands] == [["build", "postgres"]]
    up_command, up_kwargs = next(
        (command, kwargs) for command, kwargs in commands if "up" in command
    )
    assert up_command[-5:] == ["up", "--detach", "--no-build", "--pull", "never"]
    assert up_kwargs["environment"]["MOONCEN_API_IMAGE"] == release["images"]["api"]["tag"]
    assert up_kwargs["environment"]["MOONCEN_FRONTEND_IMAGE"] == release["images"]["frontend"]["tag"]
    image_remove = next(
        command for command, _kwargs in commands if command[:3] == ["docker", "image", "rm"]
    )
    assert image_remove == [
        "docker",
        "image",
        "rm",
        "mooncen/postgres:mooncen-smoke-1234-release",
    ]
