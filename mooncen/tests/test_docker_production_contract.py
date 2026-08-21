from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = ROOT / "deploy" / "docker" / "compose.production.yaml"


def compose() -> dict[str, object]:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def test_production_stack_contains_only_containerized_application_services() -> None:
    services = compose()["services"]
    assert set(services) == {"migrate", "api", "frontend", "ai"}
    assert "postgres" not in services
    assert all("build" not in service for service in services.values())
    assert all(service["pull_policy"] == "never" for service in services.values())


def test_production_ports_are_exact_loopback_contract() -> None:
    services = compose()["services"]
    assert services["api"]["ports"] == [
        "127.0.0.1:${MOONCEN_API_BIND_PORT:-8001}:8001"
    ]
    assert services["frontend"]["ports"] == [
        "127.0.0.1:${MOONCEN_FRONTEND_BIND_PORT:-5173}:8080"
    ]
    assert "ports" not in services["ai"]
    assert "ports" not in services["migrate"]


def test_project_scoped_network_allows_isolated_candidate_validation() -> None:
    rendered = compose()
    assert rendered["name"] == "mooncen-production"
    assert rendered["networks"] == {"app": {}}


def test_host_control_plane_and_docker_socket_are_never_mounted() -> None:
    rendered = COMPOSE_PATH.read_text(encoding="utf-8")
    assert "/var/run/docker.sock" not in rendered
    assert "/opt/mooncen" not in rendered
    assert ".:/" not in rendered
    assert "deploy/an2p" not in rendered
    assert "ops-console" not in rendered
    assert "cloudflared" not in rendered
    assert "nginx.service" not in rendered


def test_database_is_an_external_host_socket_not_a_container_or_tcp_host() -> None:
    services = compose()["services"]
    for name in ("migrate", "api", "ai"):
        service = services[name]
        assert service["environment"]["DB_HOST"] == "/var/run/postgresql"
        assert service["environment"]["DB_SSLMODE"] == "disable"
        assert service["environment"]["DB_SSLROOTCERT"] == ""
        assert service["environment"]["DB_SSLCERT"] == ""
        assert service["environment"]["DB_SSLKEY"] == ""
        mounts = service["volumes"]
        assert mounts == [
            {
                "type": "bind",
                "source": "/var/run/postgresql",
                "target": "/var/run/postgresql",
                "read_only": True,
            }
        ]


def test_container_secrets_never_reuse_native_group_readable_env_paths() -> None:
    services = compose()["services"]
    assert services["api"]["env_file"] == ["/etc/mooncen/container-api.env"]
    assert services["ai"]["env_file"] == ["/etc/mooncen/container-ai.env"]
    rendered = COMPOSE_PATH.read_text(encoding="utf-8")
    assert "- /etc/mooncen/api.env" not in rendered
    assert "- /etc/mooncen/ai.env" not in rendered


def test_runtime_services_are_read_only_non_privileged_and_bounded() -> None:
    services = compose()["services"]
    for name, service in services.items():
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service["pids_limit"] >= 64
        assert service["mem_limit"]
        assert service["cpus"] > 0
        assert service["logging"] == {
            "driver": "json-file",
            "options": {"max-size": "10m", "max-file": "3"},
        }


def test_public_api_profile_and_runtime_frontend_config_are_explicit() -> None:
    services = compose()["services"]
    assert services["api"]["environment"]["MOONCEN_API_PROFILE"] == "public"
    assert services["frontend"]["volumes"] == [
        {
            "type": "bind",
            "source": "${MOONCEN_RUNTIME_CONFIG_FILE:-/etc/mooncen/container-frontend-runtime-config.js}",
            "target": "/usr/share/nginx/html/runtime-config.js",
            "read_only": True,
        }
    ]


def test_migration_is_explicit_one_shot_profile_with_private_env_contract() -> None:
    migration = compose()["services"]["migrate"]
    assert migration["profiles"] == ["migration"]
    assert migration["restart"] == "no"
    assert migration["env_file"] == [
        "${MOONCEN_MIGRATOR_ENV_FILE:?Set a private one-shot migrator env file}"
    ]
    assert migration["command"] == [
        "python",
        "DB/setup_db.py",
        "--mode",
        "plan",
        "--json",
        "--require-current",
    ]


def test_application_images_are_manifest_supplied_and_ai_reuses_api_image() -> None:
    services = compose()["services"]
    expected_api = "${MOONCEN_API_IMAGE:?Set the manifest-bound API image tag}"
    assert services["api"]["image"] == expected_api
    assert services["migrate"]["image"] == expected_api
    assert services["ai"]["image"] == expected_api
    assert services["frontend"]["image"] == (
        "${MOONCEN_FRONTEND_IMAGE:?Set the manifest-bound frontend image tag}"
    )
