#!/usr/bin/env python3
"""Build and verify the isolated MoonCen Docker development stack."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from deploy.docker.render_runtime_config import (
        RuntimeConfigError,
        public_config,
        render_javascript,
        render_to_file,
    )
except ModuleNotFoundError:  # Direct execution resolves the sibling module.
    from render_runtime_config import (
        RuntimeConfigError,
        public_config,
        render_javascript,
        render_to_file,
    )


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploy.docker.release_manifest import (  # noqa: E402
    VALIDATION_CHECKS,
    ManifestError,
    create_validation_receipt,
    load_json_evidence,
    write_json_evidence,
)
from deploy.docker.verify_release_bundle import (  # noqa: E402
    VerificationError,
    verify_release_directory,
)

COMPOSE_FILE = ROOT / "compose.yaml"
SAFE_LOCAL_CONTEXTS = {"default", "desktop-linux"}
SAFE_LOCAL_DOCKER_ENDPOINT_PREFIXES = ("npipe://", "unix://")
EXPECTED_EXTENSIONS = ("pg_trgm", "pgcrypto", "postgis", "uuid-ossp")
EXPECTED_COMPOSE_BUILDS = {
    "postgres": "deploy/docker/postgres.Dockerfile",
    "migrate": "deploy/docker/api.Dockerfile",
    "api": "deploy/docker/api.Dockerfile",
    "frontend": "deploy/docker/frontend.Dockerfile",
}
EXPECTED_COMPOSE_NETWORKS = {
    "postgres": {"data"},
    "migrate": {"data"},
    "api": {"data", "web"},
    "frontend": {"web"},
}
EXPECTED_RUNTIME_LIMITS = {
    "postgres": {"cpus": 2.0, "memory": 2 * 1024**3, "pids": 256},
    "migrate": {"cpus": 2.0, "memory": 2 * 1024**3, "pids": 256},
    "api": {"cpus": 2.0, "memory": 2 * 1024**3, "pids": 256},
    "frontend": {"cpus": 1.0, "memory": 256 * 1024**2, "pids": 128},
}
EXPECTED_LOGGING = {
    "driver": "local",
    "options": {"max-file": "3", "max-size": "10m"},
}
PROTECTED_PATHS = (
    "/api/ops",
    "/api/ops/runtime-metrics",
    "/api/auth/ops",
    "/api/auth/ops/login",
)
SOURCE_VERIFIER = ROOT / "deploy" / "docker" / "verify_clean_source.py"
DEFAULT_RUNTIME_CONFIG = ROOT / "frontend2" / "public" / "runtime-config.js"
MAX_SOURCE_DIAGNOSTIC_CHARS = 8_192
MAX_COMPOSE_MODEL_CHARS = 1_000_000
CRAWLER_REGISTRY_TIMEOUT_SECONDS = 300
MINIMUM_COMPOSE_VERSION = (2, 35, 0)
DEVELOPMENT_VALIDATION_POLICY_PATHS = (
    ".dockerignore",
    ".gitattributes",
    "compose.yaml",
    "deploy/an2p/check_docker_environment.py",
    "deploy/an2p/install_user_services.sh",
    "deploy/an2p/mooncen-api.service",
    "deploy/an2p/mooncen-development-runtime.target",
    "deploy/an2p/mooncen-docker-dev.service",
    "deploy/an2p/mooncen-frontend.service",
    "deploy/an2p/mooncen-status-agent.service",
    "deploy/an2p/validate_docker_release.py",
    "deploy/docker/postgres.Dockerfile",
    "deploy/docker/release_manifest.py",
    "deploy/docker/render_runtime_config.py",
    "deploy/docker/smoke.py",
    "deploy/docker/verify_release_bundle.py",
    "tools/wait_for_an2p_database.py",
    "tools/wait_for_an2p_http.py",
)


class SmokeFailure(RuntimeError):
    """Raised when a Docker smoke invariant is not met."""


def _git_value(*arguments: str) -> str:
    environment = os.environ.copy()
    for name in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    ):
        environment.pop(name, None)
    try:
        result = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", *arguments],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SmokeFailure("Reviewed Git source could not be attested.") from exc
    if result.returncode != 0:
        raise SmokeFailure("Reviewed Git source could not be attested.")
    return result.stdout.strip().lower()


def _attest_release_checkout(release: Mapping[str, Any]) -> None:
    if _git_value("rev-parse", "--verify", "HEAD^{commit}") != release["snapshot_commit"]:
        raise SmokeFailure("Validation checkout does not match the release snapshot commit.")
    if _git_value("rev-parse", "--verify", "HEAD^{tree}") != release["source_tree"]:
        raise SmokeFailure("Validation checkout does not match the release source tree.")
    if _git_value("rev-parse", "--verify", "HEAD^1") != release["base_commit"]:
        raise SmokeFailure("Validation checkout does not match the release base commit.")


def _normalized_daemon_platform(value: str) -> str:
    aliases = {
        "linux/x86_64": "linux/amd64",
        "linux/aarch64": "linux/arm64",
    }
    return aliases.get(value, value)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _development_target_identity(*, hostname: str, platform: str) -> str:
    normalized_hostname = hostname.strip().lower()
    normalized_platform = _normalized_daemon_platform(platform)
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,252}", normalized_hostname):
        raise SmokeFailure("Development validator hostname is invalid.")
    if normalized_platform not in {"linux/amd64", "linux/arm64"}:
        raise SmokeFailure("Development validator platform is unsupported.")
    policies: list[dict[str, str]] = []
    for relative in DEVELOPMENT_VALIDATION_POLICY_PATHS:
        path = ROOT / relative
        try:
            metadata = path.lstat()
            content = path.read_bytes()
        except OSError as exc:
            raise SmokeFailure("Development validation policy cannot be read.") from exc
        if path.is_symlink() or not path.is_file() or metadata.st_size != len(content):
            raise SmokeFailure("Development validation policy path is unsafe.")
        policies.append({"path": relative, "sha256": hashlib.sha256(content).hexdigest()})
    payload = {
        "schema_version": 1,
        "target": "an2p-dev",
        "environment": "development",
        "executor_hostname": normalized_hostname,
        "platform": normalized_platform,
        "policies": policies,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _write_validation_receipt(
    *,
    output: Path,
    release: Mapping[str, Any],
    target: str,
    target_identity: str,
    checks: Mapping[str, bool],
    ttl_hours: int,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    receipt = create_validation_receipt(
        release=release,
        target=target,
        target_identity=target_identity,
        checks=checks,
        validated_at=_timestamp(now),
        expires_at=_timestamp(now + timedelta(hours=ttl_hours)),
    )
    write_json_evidence(output, receipt, receipt=True)
    return receipt


def _enforce_clean_source(*, allow_dirty_source: bool) -> None:
    if allow_dirty_source:
        print(
            "WARNING: --allow-dirty-source bypasses clean/HEAD-tracked source "
            "verification; this smoke is not clean-clone evidence.",
            file=sys.stderr,
        )
        return
    try:
        result = subprocess.run(
            [sys.executable, str(SOURCE_VERIFIER)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SmokeFailure("Docker source verification could not run.") from exc
    if result.returncode != 0:
        diagnostic = result.stderr.strip() or "Docker source verification failed."
        if len(diagnostic) > MAX_SOURCE_DIAGNOSTIC_CHARS:
            diagnostic = f"{diagnostic[: MAX_SOURCE_DIAGNOSTIC_CHARS - 3]}..."
        raise SmokeFailure(diagnostic)


def _run(
    command: Sequence[str],
    *,
    environment: dict[str, str],
    capture: bool = False,
    check: bool = True,
    timeout: int = 300,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=ROOT,
        env=environment,
        check=check,
        capture_output=capture,
        text=True,
        timeout=timeout,
    )


def _reserve_local_ports(count: int = 2) -> list[int]:
    sockets: list[socket.socket] = []
    try:
        for _ in range(count):
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.bind(("127.0.0.1", 0))
            sockets.append(listener)
        return [int(listener.getsockname()[1]) for listener in sockets]
    finally:
        for listener in sockets:
            listener.close()


def _smoke_environment(
    api_port: int,
    web_port: int,
    project: str,
    *,
    platform: str | None = None,
) -> dict[str, str]:
    environment = os.environ.copy()
    for name in tuple(environment):
        if name.startswith("COMPOSE_"):
            environment.pop(name, None)
    environment.pop("DOCKER_DEFAULT_PLATFORM", None)

    owner_password = secrets.token_urlsafe(32)
    api_password = secrets.token_urlsafe(32)
    if owner_password == api_password:
        raise SmokeFailure("Generated database passwords unexpectedly collided.")

    environment.update(
        {
            # Compose otherwise reads ROOT/.env before interpolating compose.yaml.
            # The smoke must use only the random credentials and ports below.
            "COMPOSE_DISABLE_ENV_FILE": "1",
            "MOONCEN_DB_NAME": "mooncen_smoke",
            "MOONCEN_DB_USER": "mooncen_smoke",
            "MOONCEN_DB_PASSWORD": owner_password,
            "MOONCEN_DB_API_USER": "mooncen_smoke_api",
            "MOONCEN_DB_API_PASSWORD": api_password,
            "MOONCEN_AUTH_SECRET": secrets.token_urlsafe(48),
            "MOONCEN_WEB_PORT": str(web_port),
            "MOONCEN_API_PORT": str(api_port),
            "MOONCEN_SITE_URL": f"http://localhost:{web_port}",
            "MOONCEN_CORS_ORIGINS": f"http://localhost:{web_port}",
            "MOONCEN_OAUTH_REDIRECT_URI": f"http://localhost:{web_port}/",
            "MOONCEN_KAKAO_MAPS_JAVASCRIPT_KEY": "",
            "MOONCEN_GOOGLE_OAUTH_CLIENT_ID": "",
            "MOONCEN_GOOGLE_OAUTH_CLIENT_SECRET": "",
            "MOONCEN_NAVER_OAUTH_CLIENT_ID": "",
            "MOONCEN_NAVER_OAUTH_CLIENT_SECRET": "",
            "MOONCEN_POSTGRES_IMAGE": f"mooncen/postgres:{project}",
            "MOONCEN_API_IMAGE": f"mooncen/api:{project}",
            "MOONCEN_FRONTEND_IMAGE": f"mooncen/frontend:{project}",
        }
    )
    if platform is not None:
        environment["DOCKER_DEFAULT_PLATFORM"] = platform
    return environment


def _http_status(url: str, *, timeout: float = 5.0) -> int:
    request = urllib.request.Request(url, headers={"User-Agent": "mooncen-docker-smoke/1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read(1024)
            return int(response.status)
    except urllib.error.HTTPError as exc:
        exc.read(1024)
        return int(exc.code)


def _http_json(url: str, *, timeout: float = 10.0) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": "mooncen-docker-smoke/1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get_content_type()
        if response.status != 200 or content_type != "application/json":
            raise SmokeFailure(f"Expected JSON HTTP 200 from {url}, got {response.status} {content_type}.")
        payload = response.read()
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SmokeFailure(f"Expected valid UTF-8 JSON from {url}.") from exc


def _http_headers(url: str, *, timeout: float = 5.0) -> dict[str, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "mooncen-docker-smoke/1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response.read(1024)
        return {name.lower(): value for name, value in response.headers.items()}


def _http_text(url: str, *, timeout: float = 5.0) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "mooncen-docker-smoke/1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise SmokeFailure(f"Expected HTTP 200 from {url}, got {response.status}.")
        payload = response.read(64 * 1024 + 1)
    if len(payload) > 64 * 1024:
        raise SmokeFailure("Runtime browser configuration exceeds the smoke limit.")
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SmokeFailure("Runtime browser configuration is not valid UTF-8.") from exc


def _wait_for_health(api_port: int, web_port: int, *, timeout: float = 120.0) -> None:
    urls = (
        f"http://127.0.0.1:{api_port}/health",
        f"http://127.0.0.1:{web_port}/_frontend_health",
        f"http://127.0.0.1:{web_port}/health",
    )
    deadline = time.monotonic() + timeout
    last_statuses: dict[str, str] = {}
    while time.monotonic() < deadline:
        ready = True
        for url in urls:
            try:
                status = _http_status(url)
                last_statuses[url] = str(status)
                ready = ready and status == 200
            except (OSError, urllib.error.URLError) as exc:
                last_statuses[url] = type(exc).__name__
                ready = False
        if ready:
            return
        time.sleep(2)
    raise SmokeFailure(f"Docker services did not become healthy: {last_statuses}")


def _captured_text(command: Sequence[str], *, environment: dict[str, str], timeout: int = 60) -> str:
    return _run(command, environment=environment, capture=True, timeout=timeout).stdout.strip()


def _compose_path(value: object, *, base: Path) -> Path | None:
    """Resolve a Compose path without exposing an invalid value in diagnostics."""
    if not isinstance(value, str) or not value or "\x00" in value:
        return None
    try:
        path = Path(value)
        return (path if path.is_absolute() else base / path).resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None


def _empty_or_absent(mapping: dict[str, object], key: str) -> bool:
    if key not in mapping:
        return True
    value = mapping[key]
    return value is None or value is False or value == "" or value == [] or value == {}


def _validate_compose_build(
    service_name: str,
    service: dict[str, object],
) -> None:
    build = service.get("build")
    if not isinstance(build, dict):
        raise SmokeFailure(f"Compose service {service_name} must use its reviewed local build.")

    forbidden_inputs = (
        "additional_contexts",
        "additionalContexts",
        "dockerfile_inline",
        "dockerfileInline",
        "secrets",
        "ssh",
    )
    if any(not _empty_or_absent(build, key) for key in forbidden_inputs):
        raise SmokeFailure(f"Compose service {service_name} has an unreviewed build input.")
    if not _empty_or_absent(build, "network") or not _empty_or_absent(build, "entitlements"):
        raise SmokeFailure(f"Compose service {service_name} has unsafe build privileges.")
    if build.get("privileged") not in (None, False):
        raise SmokeFailure(f"Compose service {service_name} has unsafe build privileges.")

    if set(build) != {"context", "dockerfile"}:
        raise SmokeFailure(f"Compose service {service_name} has an unexpected build model.")

    context = _compose_path(build.get("context"), base=ROOT)
    if context != ROOT.resolve(strict=False):
        raise SmokeFailure(f"Compose service {service_name} has an unexpected build context.")
    dockerfile = _compose_path(build.get("dockerfile"), base=context)
    expected = (ROOT / EXPECTED_COMPOSE_BUILDS[service_name]).resolve(strict=False)
    if dockerfile != expected:
        raise SmokeFailure(f"Compose service {service_name} has an unexpected Dockerfile.")


def _validate_compose_ports(
    service_name: str,
    service: dict[str, object],
    *,
    api_port: int,
    web_port: int,
) -> None:
    expected = {
        "api": (api_port, 8001),
        "frontend": (web_port, 8080),
    }.get(service_name)
    ports = service.get("ports", [])
    if not isinstance(ports, list):
        raise SmokeFailure(f"Compose service {service_name} has an invalid published-port model.")
    if expected is None:
        if ports:
            raise SmokeFailure(f"Compose service {service_name} must not publish a host port.")
        return
    if len(ports) != 1 or not isinstance(ports[0], dict):
        raise SmokeFailure(f"Compose service {service_name} must publish exactly one loopback port.")

    port = ports[0]
    published, target = expected
    if (
        port.get("host_ip") != "127.0.0.1"
        or str(port.get("published")) != str(published)
        or port.get("target") != target
        or port.get("protocol", "tcp") != "tcp"
        or port.get("mode", "ingress") != "ingress"
    ):
        raise SmokeFailure(f"Compose service {service_name} has an unexpected published port.")


def _validate_compose_service_networks(
    service_name: str,
    service: dict[str, object],
) -> None:
    networks = service.get("networks")
    if not isinstance(networks, dict) or set(networks) != EXPECTED_COMPOSE_NETWORKS[service_name]:
        raise SmokeFailure(f"Compose service {service_name} has an unexpected network attachment.")
    if any(value not in (None, {}) for value in networks.values()):
        raise SmokeFailure(f"Compose service {service_name} customizes a network attachment.")


def _validate_compose_service_volumes(
    service_name: str,
    service: dict[str, object],
    *,
    runtime_config_file: Path,
) -> None:
    volumes = service.get("volumes", [])
    if not isinstance(volumes, list):
        raise SmokeFailure(f"Compose service {service_name} has an invalid volume model.")
    if service_name == "frontend":
        if len(volumes) != 1 or not isinstance(volumes[0], dict):
            raise SmokeFailure("Compose frontend must mount one runtime configuration file.")
        mount = volumes[0]
        source = _compose_path(mount.get("source"), base=ROOT)
        bind_options = mount.get("bind")
        if (
            mount.get("type") != "bind"
            or source != runtime_config_file.resolve(strict=False)
            or mount.get("target") != "/usr/share/nginx/html/runtime-config.js"
            or mount.get("read_only") is not True
            or bind_options not in ({}, {"create_host_path": False})
        ):
            raise SmokeFailure("Compose frontend has an unsafe runtime configuration mount.")
        return
    if service_name != "postgres":
        if volumes:
            raise SmokeFailure(f"Compose service {service_name} must not mount a volume.")
        return
    if len(volumes) != 1 or not isinstance(volumes[0], dict):
        raise SmokeFailure("Compose postgres must mount exactly one named data volume.")
    mount = volumes[0]
    volume_options = mount.get("volume")
    if (
        mount.get("type") != "volume"
        or mount.get("source") != "postgres-data"
        or mount.get("target") != "/var/lib/postgresql/data"
        or mount.get("read_only", False) is not False
        or volume_options not in (None, {})
    ):
        raise SmokeFailure("Compose postgres has an unexpected data-volume mount.")


def _memory_limit_bytes(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized.isdecimal():
        return int(normalized)
    match = re.fullmatch(r"([1-9][0-9]*)([kmgt])(?:i?b)?", normalized)
    if match is None:
        return None
    exponent = "kmgt".index(match.group(2)) + 1
    return int(match.group(1)) * 1024**exponent


def _validate_compose_runtime_limits(
    service_name: str,
    service: dict[str, object],
) -> None:
    expected = EXPECTED_RUNTIME_LIMITS[service_name]
    try:
        cpus = float(service.get("cpus", 0))
        pids = int(service.get("pids_limit", 0))
    except (TypeError, ValueError, OverflowError) as exc:
        raise SmokeFailure(f"Compose service {service_name} has invalid resource limits.") from exc
    memory = _memory_limit_bytes(service.get("mem_limit"))
    if cpus != expected["cpus"] or memory != expected["memory"] or pids != expected["pids"]:
        raise SmokeFailure(f"Compose service {service_name} has unexpected resource limits.")
    if service.get("logging") != EXPECTED_LOGGING:
        raise SmokeFailure(f"Compose service {service_name} has unbounded logging.")


def _validate_compose_resources(model: dict[str, object], *, project: str) -> None:
    volumes = model.get("volumes")
    if not isinstance(volumes, dict) or set(volumes) != {"postgres-data"}:
        raise SmokeFailure("Compose must declare exactly one named postgres data volume.")
    volume = volumes["postgres-data"]
    if not isinstance(volume, dict):
        raise SmokeFailure("Compose postgres data-volume definition is invalid.")
    if (
        volume.get("name") != f"{project}_postgres-data"
        or volume.get("external") not in (None, False)
        or volume.get("driver") not in (None, "local")
        or not _empty_or_absent(volume, "driver_opts")
    ):
        raise SmokeFailure("Compose postgres data-volume definition is unsafe.")

    networks = model.get("networks")
    if not isinstance(networks, dict) or set(networks) != {"data", "web"}:
        raise SmokeFailure("Compose must declare exactly the reviewed data and web networks.")
    for name, internal in (("data", True), ("web", False)):
        network = networks[name]
        if not isinstance(network, dict):
            raise SmokeFailure(f"Compose {name} network definition is invalid.")
        if (
            network.get("name") != f"{project}_{name}"
            or bool(network.get("internal", False)) is not internal
            or network.get("external") not in (None, False)
            or network.get("driver") not in (None, "bridge")
            or not _empty_or_absent(network, "driver_opts")
            or network.get("attachable") not in (None, False)
        ):
            raise SmokeFailure(f"Compose {name} network definition is unsafe.")


def _validate_compose_model(
    model: object,
    *,
    project: str,
    api_port: int,
    web_port: int,
    runtime_config_file: Path = DEFAULT_RUNTIME_CONFIG,
) -> None:
    """Fail closed unless the rendered Compose model is the reviewed local stack."""
    if not isinstance(model, dict):
        raise SmokeFailure("Compose config did not return a JSON object.")
    if model.get("name") != project:
        raise SmokeFailure("Compose config returned an unexpected project model.")
    if "include" in model:
        raise SmokeFailure("Compose include files are not allowed in the smoke model.")
    if not _empty_or_absent(model, "configs") or not _empty_or_absent(model, "secrets"):
        raise SmokeFailure("Compose configs and secrets are not allowed in the smoke model.")

    services = model.get("services")
    if not isinstance(services, dict) or set(services) != set(EXPECTED_COMPOSE_BUILDS):
        raise SmokeFailure("Compose config returned an incomplete or unexpected service set.")
    for service_name in EXPECTED_COMPOSE_BUILDS:
        service = services[service_name]
        if not isinstance(service, dict):
            raise SmokeFailure(f"Compose service {service_name} has an invalid model.")
        if any(key in service for key in ("extends", "env_file", "configs", "secrets", "develop")):
            raise SmokeFailure(f"Compose service {service_name} loads an external file or resource.")
        if service.get("privileged") not in (None, False):
            raise SmokeFailure(f"Compose service {service_name} requests privileged mode.")
        if service.get("network_mode") not in (None, ""):
            raise SmokeFailure(f"Compose service {service_name} requests a host network mode.")
        if service.get("use_api_socket") not in (None, False):
            raise SmokeFailure(f"Compose service {service_name} requests Docker API socket access.")
        if not _empty_or_absent(service, "devices") or not _empty_or_absent(service, "cap_add"):
            raise SmokeFailure(f"Compose service {service_name} requests additional host privileges.")

        _validate_compose_build(service_name, service)
        _validate_compose_ports(
            service_name,
            service,
            api_port=api_port,
            web_port=web_port,
        )
        _validate_compose_service_networks(service_name, service)
        _validate_compose_service_volumes(
            service_name,
            service,
            runtime_config_file=runtime_config_file,
        )
        _validate_compose_runtime_limits(service_name, service)
        if service_name == "api":
            environment = service.get("environment")
            if not isinstance(environment, dict) or environment.get("MOONCEN_API_PROFILE") != "public":
                raise SmokeFailure("Compose API must use the public-only route profile.")
    _validate_compose_resources(model, project=project)


def _guard_compose_model(
    compose: Sequence[str],
    *,
    environment: dict[str, str],
    project: str,
    api_port: int,
    web_port: int,
    runtime_config_file: Path = DEFAULT_RUNTIME_CONFIG,
) -> None:
    result = _run(
        [*compose, "config", "--format", "json", "--no-env-resolution"],
        environment=environment,
        capture=True,
        timeout=60,
    )
    rendered = result.stdout
    if len(rendered) > MAX_COMPOSE_MODEL_CHARS:
        raise SmokeFailure("Compose config JSON exceeds the safe inspection limit.")
    try:
        model = json.loads(rendered)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise SmokeFailure("Compose config did not return valid JSON.") from exc
    _validate_compose_model(
        model,
        project=project,
        api_port=api_port,
        web_port=web_port,
        runtime_config_file=runtime_config_file,
    )


def _inspect_value(container_id: str, template: str, *, environment: dict[str, str]) -> str:
    if not container_id:
        raise SmokeFailure("Expected Compose container is missing.")
    return _captured_text(
        ["docker", "inspect", "--format", template, container_id],
        environment=environment,
    )


def _api_database_contract(compose: Sequence[str], *, environment: dict[str, str]) -> dict[str, object]:
    probe = '''
import json
import os

from sqlalchemy.exc import DBAPIError

from backend.database import engine

contract = {
    "owner_user_env_absent": "DB_USER" not in os.environ,
    "owner_password_env_absent": "DB_PASSWORD" not in os.environ,
    "runtime_user_is_api_user": os.getenv("DB_RUNTIME_USER") == os.getenv("DB_API_USER"),
    "runtime_password_is_api_password": (
        os.getenv("DB_RUNTIME_PASSWORD") == os.getenv("DB_API_PASSWORD")
    ),
}
with engine.connect() as connection:
    row = connection.exec_driver_sql(
        """
        SELECT current_user, session_user, role.rolcanlogin, role.rolinherit,
               role.rolsuper, role.rolcreatedb, role.rolcreaterole,
               role.rolreplication, role.rolbypassrls,
               has_database_privilege(current_user, current_database(), 'CONNECT')
                   AS database_connect,
               has_database_privilege(current_user, current_database(), 'CREATE')
                   AS database_create,
               has_schema_privilege(current_user, 'public', 'CREATE') AS schema_create,
               has_table_privilege(current_user, 'public.courses', 'SELECT')
                   AS courses_select,
               has_table_privilege(current_user, 'public.courses', 'DELETE')
                   AS courses_delete,
               has_column_privilege(
                   current_user, 'public.courses', 'view_count', 'UPDATE'
               ) AS view_count_update,
               has_column_privilege(
                   current_user, 'public.courses', 'title', 'UPDATE'
               ) AS title_update
        FROM pg_roles role
        WHERE role.rolname = current_user
        """
    ).mappings().one()
    contract.update(dict(row))
    connection.exec_driver_sql(
        "UPDATE public.courses SET view_count = view_count WHERE FALSE"
    )
    connection.rollback()
    contract["view_count_update_statement"] = True

for name, statement in (
    ("courses_delete_statement_denied", "DELETE FROM public.courses WHERE FALSE"),
    ("public_create_statement_denied", "CREATE TABLE public.mooncen_denied_probe(id int)"),
):
    with engine.connect() as connection:
        try:
            connection.exec_driver_sql(statement)
        except DBAPIError as exc:
            contract[name] = getattr(exc.orig, "pgcode", None) == "42501"
            connection.rollback()
        else:
            contract[name] = False
            connection.rollback()

print(json.dumps(contract, sort_keys=True))
'''.strip()
    raw = _captured_text(
        [*compose, "exec", "-T", "api", "python", "-c", probe],
        environment=environment,
    )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SmokeFailure("API database contract probe did not return valid JSON.") from exc
    if not isinstance(payload, dict):
        raise SmokeFailure("API database contract probe did not return a JSON object.")
    return payload


def _validate_local_daemon(environment: dict[str, str], *, allow_nonlocal: bool) -> str:
    if environment.get("DOCKER_HOST") and not allow_nonlocal:
        raise SmokeFailure("DOCKER_HOST is set. Refusing to run a destructive smoke against a remote daemon.")
    try:
        context = _captured_text(["docker", "context", "show"], environment=environment, timeout=30)
    except FileNotFoundError as exc:
        raise SmokeFailure("Docker CLI is not installed or is not on PATH.") from exc
    if context not in SAFE_LOCAL_CONTEXTS and not allow_nonlocal:
        raise SmokeFailure(
            f"Docker context {context!r} is not an approved local context; use "
            "--allow-nonlocal-context only after reviewing the target."
        )
    if not allow_nonlocal:
        try:
            endpoint = _captured_text(
                [
                    "docker",
                    "context",
                    "inspect",
                    context,
                    "--format",
                    "{{.Endpoints.docker.Host}}",
                ],
                environment=environment,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SmokeFailure("Could not verify the Docker context endpoint.") from exc
        if not endpoint.startswith(SAFE_LOCAL_DOCKER_ENDPOINT_PREFIXES):
            raise SmokeFailure(
                f"Docker context {context!r} uses non-local endpoint {endpoint!r}; refusing the destructive smoke."
            )
    try:
        server = _captured_text(
            ["docker", "info", "--format", "{{.OSType}}/{{.Architecture}}"],
            environment=environment,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SmokeFailure("A reachable Docker Linux daemon is required.") from exc
    if not server.startswith("linux/"):
        raise SmokeFailure(f"Linux containers are required; Docker reported {server!r}.")
    return server


def _validate_compose_version(environment: dict[str, str]) -> None:
    required = ".".join(str(part) for part in MINIMUM_COMPOSE_VERSION)
    try:
        raw_version = _captured_text(
            ["docker", "compose", "version", "--short"],
            environment=environment,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SmokeFailure(f"Docker Compose v{required} or newer is required.") from exc
    match = re.fullmatch(
        r"v?(\d+)\.(\d+)\.(\d+)(?:[+~-][0-9A-Za-z.+:~-]+)?",
        raw_version,
    )
    if match is None or tuple(int(part) for part in match.groups()) < MINIMUM_COMPOSE_VERSION:
        raise SmokeFailure(f"Docker Compose v{required} or newer is required.")


def _validate_release_migration_plan(
    compose: Sequence[str],
    *,
    environment: dict[str, str],
    release: Mapping[str, Any],
) -> None:
    raw = _captured_text(
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
        ],
        environment=environment,
        timeout=1800,
    )
    try:
        plan = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SmokeFailure("Migration plan did not return canonical JSON.") from exc
    expected_digest = release["migration_ledger_sha256"]
    if (
        not isinstance(plan, dict)
        or plan.get("schema_version") != 1
        or plan.get("current") is not True
        or plan.get("pending") != []
        or plan.get("expected_ledger_sha256") != expected_digest
        or plan.get("applied_ledger_sha256") != expected_digest
    ):
        raise SmokeFailure("Database migration ledger does not match the release.")


def _validate_release_container_images(
    compose: Sequence[str],
    *,
    environment: dict[str, str],
    release: Mapping[str, Any],
) -> None:
    expected = {
        "migrate": release["images"]["api"]["image_id"],
        "api": release["images"]["api"]["image_id"],
        "frontend": release["images"]["frontend"]["image_id"],
    }
    for service, expected_id in expected.items():
        ps_args = [*compose, "ps", "--quiet", service]
        if service == "migrate":
            ps_args.insert(-2, "--all")
        container_id = _captured_text(ps_args, environment=environment)
        actual_id = _inspect_value(container_id, "{{.Image}}", environment=environment).lower()
        if actual_id != expected_id:
            raise SmokeFailure(f"{service} container image ID does not match the reviewed release.")


def run_smoke(
    *,
    keep_on_failure: bool,
    allow_nonlocal: bool,
    allow_dirty_source: bool = False,
    platform: str | None = None,
    release_directory: Path | None = None,
    receipt_output: Path | None = None,
    validation_target: str | None = None,
    target_identity: str | None = None,
    receipt_ttl_hours: int = 24,
) -> None:
    _enforce_clean_source(allow_dirty_source=allow_dirty_source)
    release: dict[str, Any] | None = None
    checks = {name: False for name in sorted(VALIDATION_CHECKS)}
    if release_directory is not None:
        if allow_dirty_source or allow_nonlocal:
            raise SmokeFailure("Release evidence requires a clean checkout and the reviewed local daemon.")
        if receipt_output is None or validation_target is None or target_identity is None:
            raise SmokeFailure("Release validation requires receipt output, target, and target identity.")
        if not 1 <= receipt_ttl_hours <= 168:
            raise SmokeFailure("Validation receipt lifetime must be between 1 and 168 hours.")
        try:
            release = load_json_evidence(release_directory / "release.json")
        except ManifestError as exc:
            raise SmokeFailure(str(exc)) from exc
        _attest_release_checkout(release)
        if platform is not None and platform != release["platform"]:
            raise SmokeFailure("Requested validation platform does not match the release.")
        platform = release["platform"]
    elif any(value is not None for value in (receipt_output, validation_target, target_identity)):
        raise SmokeFailure("Validation receipt options require a release directory.")

    api_port, web_port = _reserve_local_ports()
    project = f"mooncen-smoke-{os.getpid()}-{secrets.token_hex(4)}"
    environment = _smoke_environment(api_port, web_port, project, platform=platform)
    if release is not None:
        environment["MOONCEN_API_IMAGE"] = release["images"]["api"]["tag"]
        environment["MOONCEN_FRONTEND_IMAGE"] = release["images"]["frontend"]["tag"]
    smoke_images = [
        environment["MOONCEN_POSTGRES_IMAGE"],
    ]
    if release is None:
        smoke_images.extend([environment["MOONCEN_API_IMAGE"], environment["MOONCEN_FRONTEND_IMAGE"]])
    compose = [
        "docker",
        "compose",
        "--project-name",
        project,
        "--file",
        str(COMPOSE_FILE),
    ]
    started = False
    succeeded = False
    cleanup_failure: str | None = None

    server = _validate_local_daemon(environment, allow_nonlocal=allow_nonlocal)
    _validate_compose_version(environment)
    if release is not None:
        if _normalized_daemon_platform(server) != release["platform"]:
            raise SmokeFailure("Local Docker daemon platform does not match the release.")
        actual_target_identity = _development_target_identity(
            hostname=socket.gethostname(),
            platform=server,
        )
        if target_identity != actual_target_identity:
            raise SmokeFailure("Development target identity changed after validation was approved.")
        target_identity = actual_target_identity
        try:
            verified = verify_release_directory(release_directory, load_images=True)
        except VerificationError as exc:
            raise SmokeFailure(str(exc)) from exc
        if verified["release_digest"] != release["release_digest"]:
            raise SmokeFailure("Loaded Docker bundle does not match the release manifest.")
        for service, record in release["images"].items():
            actual_platform = _captured_text(
                [
                    "docker",
                    "image",
                    "inspect",
                    "--format",
                    "{{.Os}}/{{.Architecture}}",
                    record["tag"],
                ],
                environment=environment,
            )
            if _normalized_daemon_platform(actual_platform) != release["platform"]:
                raise SmokeFailure(f"{service} release image platform does not match the manifest.")
    target_platform = platform or server
    print(f"Using isolated Compose project {project} on daemon {server}; target {target_platform}.")
    runtime_config_directory = tempfile.TemporaryDirectory(prefix=f"{project}-runtime-config-")
    runtime_config_file = Path(runtime_config_directory.name) / "runtime-config.js"
    environment["MOONCEN_RUNTIME_CONFIG_FILE"] = str(runtime_config_file)
    expected_runtime_config = render_javascript(public_config(environment))
    try:
        render_to_file(runtime_config_file, environment)
        _guard_compose_model(
            compose,
            environment=environment,
            project=project,
            api_port=api_port,
            web_port=web_port,
            runtime_config_file=runtime_config_file,
        )
        # Build one service at a time. Docker Desktop development hosts are often
        # memory constrained, and Compose otherwise schedules independent builds
        # concurrently.
        service_images = {"postgres": environment["MOONCEN_POSTGRES_IMAGE"]}
        if release is None:
            service_images.update(
                {
                    "api": environment["MOONCEN_API_IMAGE"],
                    "frontend": environment["MOONCEN_FRONTEND_IMAGE"],
                }
            )
        for service, image in service_images.items():
            _run(
                [*compose, "build", service],
                environment=environment,
                timeout=2700,
            )
            if platform is not None:
                actual_platform = _captured_text(
                    [
                        "docker",
                        "image",
                        "inspect",
                        "--format",
                        "{{.Os}}/{{.Architecture}}",
                        image,
                    ],
                    environment=environment,
                )
                if actual_platform != platform:
                    raise SmokeFailure(
                        f"{service} image platform mismatch: expected {platform!r}, got {actual_platform!r}."
                    )
        up_command = [*compose, "up", "--detach", "--no-build"]
        if release is not None:
            up_command.extend(("--pull", "never"))
        # Compose may create healthy dependencies and a failed one-shot
        # migration container before returning nonzero.  Mark the attempt
        # before invoking it so the exception path preserves bounded ps/log
        # diagnostics prior to teardown.
        started = True
        _run(
            up_command,
            environment=environment,
            timeout=600,
        )

        for _ in range(2):
            _run(
                [*compose, "run", "--rm", "migrate"],
                environment=environment,
                timeout=1800,
            )

        if release is not None:
            _validate_release_migration_plan(
                compose,
                environment=environment,
                release=release,
            )
            checks["migration_ledger"] = True
            _validate_release_container_images(
                compose,
                environment=environment,
                release=release,
            )

        extension_sql = (
            "SELECT extname FROM pg_extension "
            "WHERE extname IN ('pg_trgm','pgcrypto','postgis','uuid-ossp') "
            "ORDER BY extname"
        )
        extensions = _captured_text(
            [
                *compose,
                "exec",
                "-T",
                "postgres",
                "sh",
                "-c",
                f'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "{extension_sql}"',
            ],
            environment=environment,
        ).splitlines()
        if tuple(extensions) != EXPECTED_EXTENSIONS:
            raise SmokeFailure(f"Unexpected PostgreSQL extensions: {extensions!r}")

        api_database_contract = _api_database_contract(compose, environment=environment)
        expected_database_contract: dict[str, object] = {
            "owner_user_env_absent": True,
            "owner_password_env_absent": True,
            "runtime_user_is_api_user": True,
            "runtime_password_is_api_password": True,
            "current_user": environment["MOONCEN_DB_API_USER"],
            "session_user": environment["MOONCEN_DB_API_USER"],
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
        mismatches = {
            name: api_database_contract.get(name)
            for name, expected in expected_database_contract.items()
            if api_database_contract.get(name) != expected
        }
        if mismatches:
            raise SmokeFailure(f"API least-privilege database contract mismatch: {mismatches!r}")
        checks["database_least_privilege"] = True

        _wait_for_health(api_port, web_port)
        checks["api_health"] = True
        if _http_status(f"http://127.0.0.1:{web_port}/") != 200:
            raise SmokeFailure("Frontend root did not return HTTP 200.")
        checks["frontend_health"] = True
        root_headers = _http_headers(f"http://127.0.0.1:{web_port}/")
        expected_headers = {
            "x-content-type-options": "nosniff",
            "x-frame-options": "DENY",
            "referrer-policy": "strict-origin-when-cross-origin",
        }
        for name, expected in expected_headers.items():
            if root_headers.get(name) != expected:
                raise SmokeFailure(f"Frontend security header {name} is missing or invalid.")
        if "default-src 'self'" not in root_headers.get("content-security-policy", ""):
            raise SmokeFailure("Frontend Content-Security-Policy is missing or invalid.")
        if "camera=()" not in root_headers.get("permissions-policy", ""):
            raise SmokeFailure("Frontend Permissions-Policy is missing or invalid.")

        runtime_config_url = f"http://127.0.0.1:{web_port}/runtime-config.js"
        if _http_text(runtime_config_url) != expected_runtime_config:
            raise SmokeFailure("Frontend did not serve the exact per-run runtime configuration.")
        runtime_headers = _http_headers(runtime_config_url)
        if "no-store" not in runtime_headers.get("cache-control", "").lower():
            raise SmokeFailure("Frontend runtime configuration is cacheable.")

        courses = _http_json(f"http://127.0.0.1:{web_port}/api/courses/?page=1&size=1")
        if (
            not isinstance(courses, dict)
            or not isinstance(courses.get("items"), list)
            or not isinstance(courses.get("total"), int)
        ):
            raise SmokeFailure("Course list response does not match the public API contract.")
        providers = _http_json(f"http://127.0.0.1:{web_port}/api/branches/providers")
        if not isinstance(providers, list):
            raise SmokeFailure("Provider list response does not match the public API contract.")
        oauth_config = _http_json(f"http://127.0.0.1:{web_port}/api/auth/oauth/config")
        if not isinstance(oauth_config, dict):
            raise SmokeFailure("OAuth configuration response is not a JSON object.")
        if _http_status(f"http://127.0.0.1:{web_port}/api/auth/me") != 401:
            raise SmokeFailure("Unauthenticated /api/auth/me did not return HTTP 401.")

        for surface, port in (("direct API", api_port), ("frontend proxy", web_port)):
            for path in PROTECTED_PATHS:
                status = _http_status(f"http://127.0.0.1:{port}{path}")
                if status != 404:
                    raise SmokeFailure(f"Protected path {path} on {surface} returned HTTP {status}, not 404.")
        checks["protected_routes"] = True

        service_expectations = {
            "migrate": ("mooncen", {f"{project}_data"}),
            "api": ("mooncen", {f"{project}_data", f"{project}_web"}),
            "frontend": ("nginx", {f"{project}_web"}),
        }
        for service, (expected_user, expected_networks) in service_expectations.items():
            ps_args = [*compose, "ps", "--quiet", service]
            if service == "migrate":
                ps_args.insert(-2, "--all")
            container_id = _captured_text(ps_args, environment=environment)
            user = _inspect_value(container_id, "{{.Config.User}}", environment=environment)
            read_only = _inspect_value(container_id, "{{.HostConfig.ReadonlyRootfs}}", environment=environment)
            if user != expected_user or read_only.lower() != "true":
                raise SmokeFailure(f"{service} isolation mismatch: user={user!r}, read_only={read_only!r}")
            networks = json.loads(
                _inspect_value(
                    container_id,
                    "{{json .NetworkSettings.Networks}}",
                    environment=environment,
                )
            )
            if set(networks) != expected_networks:
                raise SmokeFailure(f"{service} network isolation mismatch: {sorted(networks)!r}")

        data_internal = _captured_text(
            [
                "docker",
                "network",
                "inspect",
                "--format",
                "{{.Internal}}",
                f"{project}_data",
            ],
            environment=environment,
        )
        if data_internal.lower() != "true":
            raise SmokeFailure("Compose data network is not internal.")

        _run(
            [
                *compose,
                "exec",
                "-T",
                "api",
                "sh",
                "-c",
                "touch /tmp/api-write-probe && ! touch /home/mooncen/api-write-probe 2>/dev/null",
            ],
            environment=environment,
        )
        _run(
            [
                *compose,
                "exec",
                "-T",
                "api",
                "sh",
                "-ec",
                (
                    "test -d /app/logs && test -w /app/logs && "
                    "python -c 'from ops_agent.crawler_registry import "
                    "reviewed_crawler_providers; "
                    "providers = reviewed_crawler_providers(); assert providers' && "
                    "test -f /app/logs/crawler_municipal_yaml.log"
                ),
            ],
            environment=environment,
            timeout=CRAWLER_REGISTRY_TIMEOUT_SECONDS,
        )
        _run(
            [*compose, "exec", "-T", "frontend", "sh", "-c", "touch /tmp/frontend-write-probe"],
            environment=environment,
        )
        checks["runtime_hardening"] = True
        succeeded = True
    except (OSError, RuntimeConfigError, SmokeFailure, subprocess.SubprocessError) as exc:
        if started:
            _run(
                [*compose, "ps", "--all"],
                environment=environment,
                check=False,
                timeout=60,
            )
            _run(
                [*compose, "logs", "--no-color", "--tail=200"],
                environment=environment,
                check=False,
                timeout=120,
            )
        if release is not None and receipt_output is not None:
            try:
                _write_validation_receipt(
                    output=receipt_output,
                    release=release,
                    target=validation_target or "",
                    target_identity=target_identity or "",
                    checks=checks,
                    ttl_hours=receipt_ttl_hours,
                )
            except ManifestError as evidence_exc:
                raise SmokeFailure(f"{exc}; failed validation receipt could not be written: {evidence_exc}") from exc
        raise SmokeFailure(str(exc)) from exc
    finally:
        if succeeded or not keep_on_failure:
            down_result = _run(
                [*compose, "down", "--volumes", "--remove-orphans"],
                environment=environment,
                check=False,
                timeout=300,
            )
            image_result = _run(
                ["docker", "image", "rm", *smoke_images],
                environment=environment,
                capture=True,
                check=False,
                timeout=300,
            )
            if succeeded and (down_result.returncode != 0 or image_result.returncode != 0):
                cleanup_failure = "Successful smoke could not remove its isolated containers, volume, or image tags."
        elif started:
            print(f"Kept failed Compose project {project} for inspection.", file=sys.stderr)
        runtime_config_directory.cleanup()
    if cleanup_failure:
        raise SmokeFailure(cleanup_failure)
    if succeeded:
        if release is not None and receipt_output is not None:
            receipt = _write_validation_receipt(
                output=receipt_output,
                release=release,
                target=validation_target or "",
                target_identity=target_identity or "",
                checks=checks,
                ttl_hours=receipt_ttl_hours,
            )
            print(f"Docker development release validation passed: receipt={receipt['receipt_digest']}.")
        print("Docker development stack smoke passed and cleaned up.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep-on-failure",
        action="store_true",
        help="Keep only this randomly named smoke project after a failure.",
    )
    parser.add_argument(
        "--allow-nonlocal-context",
        action="store_true",
        help="Allow a reviewed non-default Docker context (never enabled in CI).",
    )
    parser.add_argument(
        "--allow-dirty-source",
        action="store_true",
        help=(
            "Bypass the clean/HEAD-tracked source gate for an explicitly reviewed "
            "local test (never clean-clone evidence)."
        ),
    )
    parser.add_argument(
        "--platform",
        choices=("linux/amd64", "linux/arm64"),
        help="Build, verify, and run every image for one explicit Linux platform.",
    )
    parser.add_argument(
        "--release-directory",
        type=Path,
        help=("Load and validate the exact prebuilt release bundle instead of rebuilding the API and frontend images."),
    )
    parser.add_argument(
        "--receipt-output",
        type=Path,
        help="Write the immutable development validation receipt to this new path.",
    )
    parser.add_argument(
        "--validation-target",
        choices=("an2p-dev",),
        help="Reviewed development target recorded in the validation receipt.",
    )
    parser.add_argument(
        "--target-identity",
        help="SHA-256 identity of the reviewed development target configuration.",
    )
    parser.add_argument(
        "--receipt-ttl-hours",
        type=int,
        default=24,
        help="Validation receipt validity window (1-168 hours; default: 24).",
    )
    parser.add_argument(
        "--print-development-target-identity",
        action="store_true",
        help=(
            "Verify the clean validation policy and local daemon, then print the "
            "canonical an2p-dev target identity without starting containers."
        ),
    )
    args = parser.parse_args()
    try:
        if args.print_development_target_identity:
            if (
                any(
                    value is not None
                    for value in (
                        args.release_directory,
                        args.receipt_output,
                        args.validation_target,
                        args.target_identity,
                        args.platform,
                    )
                )
                or args.allow_dirty_source
                or args.allow_nonlocal_context
                or args.keep_on_failure
            ):
                raise SmokeFailure("Target identity inspection cannot be combined with smoke options.")
            _enforce_clean_source(allow_dirty_source=False)
            environment = os.environ.copy()
            server = _validate_local_daemon(environment, allow_nonlocal=False)
            _validate_compose_version(environment)
            print(
                _development_target_identity(
                    hostname=socket.gethostname(),
                    platform=server,
                )
            )
            return 0
        run_smoke(
            keep_on_failure=args.keep_on_failure,
            allow_nonlocal=args.allow_nonlocal_context,
            allow_dirty_source=args.allow_dirty_source,
            platform=args.platform,
            release_directory=args.release_directory,
            receipt_output=args.receipt_output,
            validation_target=args.validation_target,
            target_identity=args.target_identity,
            receipt_ttl_hours=args.receipt_ttl_hours,
        )
    except (ManifestError, OSError, SmokeFailure, subprocess.SubprocessError) as exc:
        print(f"Docker smoke failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
