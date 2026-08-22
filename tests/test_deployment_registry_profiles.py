from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from ops_agent import deployment_worker
from ops_agent import deployment_registry
from ops_agent.deployment_registry import DeployTarget, load_deploy_targets
from ops_agent.production_topology import load_production_topology


ROOT = Path(__file__).resolve().parents[1]


def _public_key_line(key_type: str = "ssh-ed25519") -> bytes:
    encoded_type = key_type.encode("ascii")
    public_key = b"\x42" * 32
    blob = (
        len(encoded_type).to_bytes(4, byteorder="big")
        + encoded_type
        + len(public_key).to_bytes(4, byteorder="big")
        + public_key
    )
    return (
        encoded_type
        + b" "
        + base64.b64encode(blob)
        + b" mooncen-deploy@test\n"
    )


def _write_registry(
    tmp_path: Path,
    limited_target: dict[str, object],
    *,
    target_name: str = "gen1crawler",
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    payload = {
        "defaultTarget": "cloud",
        "servers": {
            "cloud": {
                "server": "cloud",
                "user": "ubuntu",
                "domain": "mooncen.kr",
                "remoteDir": "/opt/mooncen",
                "identityFile": "ssh-agent",
                "role": "primary",
                "deployProfile": "full-stack",
                "active": True,
            },
            target_name: limited_target,
        },
    }
    (config_dir / "deploy_servers.json").write_text(json.dumps(payload), encoding="utf-8")


def _crawler_entry(**overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "server": "gen1crawler",
        "user": "sgm",
        "domain": "gen1crawler",
        "remoteDir": "/opt/mooncen",
        "identityFile": "ssh-agent",
        "role": "crawler",
        "deployProfile": "crawler-only",
        "active": False,
    }
    entry.update(overrides)
    return entry


def _control_entry(**overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "server": "gen1db",
        "user": "sgm",
        "domain": "gen1db",
        "remoteDir": "/opt/mooncen",
        "identityFile": "ssh-agent",
        "role": "crawler-control",
        "deployProfile": "control-only",
        "active": False,
    }
    entry.update(overrides)
    return entry


def test_example_registry_loads_crawler_only_transport_profile(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "deploy_servers.json").write_text(
        (ROOT / "config" / "deploy_servers.example.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    default_target, targets = load_deploy_targets(tmp_path)

    assert default_target == "cloud"
    assert targets["cloud"].deploy_profile == "full-stack"
    assert targets["cloud"].environment == "production"
    assert targets["gen1crawler"].role == "crawler"
    assert targets["gen1crawler"].deploy_profile == "crawler-only"
    assert targets["gen1crawler"].active is False
    assert targets["gen1db"].role == "crawler-control"
    assert targets["gen1db"].deploy_profile == "control-only"
    assert targets["gen1db"].active is False


def test_ssh_agent_identity_requires_a_loaded_valid_public_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _readiness_target(
        "gen1db",
        role="crawler-control",
        deploy_profile="control-only",
    )
    calls: list[tuple[list[str], dict[str, object]]] = []

    monkeypatch.setattr(
        deployment_registry.shutil,
        "which",
        lambda command: f"C:/OpenSSH/{command}.exe",
    )

    def _run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=_public_key_line(),
            stderr=b"",
        )

    monkeypatch.setattr(deployment_registry.subprocess, "run", _run)

    assert deployment_registry.identity_file_ready(target) is True
    assert calls[0][0] == ["C:/OpenSSH/ssh-add.exe", "-L"]
    assert calls[0][1]["stdin"] is subprocess.DEVNULL
    assert calls[0][1]["stderr"] is subprocess.DEVNULL
    assert calls[0][1]["timeout"] == 5


@pytest.mark.parametrize(
    "returncode, output",
    [
        (1, b"The agent has no identities.\n"),
        (0, b"The agent has no identities.\n"),
        (0, b"ssh-ed25519 not-base64\n"),
        (0, b"ssh-ed25519 ZmFrZQ== comment\n"),
        (0, _public_key_line("ssh-rsa").replace(b"ssh-rsa ", b"ssh-ed25519 ", 1)),
    ],
)
def test_ssh_agent_identity_fails_closed_without_a_valid_loaded_key(
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    output: bytes,
) -> None:
    target = _readiness_target(
        "gen1db",
        role="crawler-control",
        deploy_profile="control-only",
    )
    monkeypatch.setattr(
        deployment_registry.shutil,
        "which",
        lambda command: f"C:/OpenSSH/{command}.exe",
    )
    monkeypatch.setattr(
        deployment_registry.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            returncode=returncode,
            stdout=output,
            stderr=b"",
        ),
    )

    assert deployment_registry.identity_file_ready(target) is False


@pytest.mark.parametrize("missing_command", ["ssh", "ssh-add"])
def test_ssh_agent_identity_fails_closed_on_missing_binary_or_timeout(
    monkeypatch: pytest.MonkeyPatch,
    missing_command: str,
) -> None:
    target = _readiness_target(
        "gen1db",
        role="crawler-control",
        deploy_profile="control-only",
    )
    monkeypatch.setattr(
        deployment_registry.shutil,
        "which",
        lambda command: (
            None if command == missing_command else f"C:/OpenSSH/{command}.exe"
        ),
    )
    assert deployment_registry.identity_file_ready(target) is False

    monkeypatch.setattr(
        deployment_registry.shutil,
        "which",
        lambda command: f"C:/OpenSSH/{command}.exe",
    )

    def _timeout(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired("ssh-add", timeout=5)

    monkeypatch.setattr(deployment_registry.subprocess, "run", _timeout)
    assert deployment_registry.identity_file_ready(target) is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"role": "crawler-control", "deployProfile": "full-stack"},
        {"role": "crawler-control", "deployProfile": "crawler-only"},
        {"role": "primary", "deployProfile": "control-only"},
        {"role": "crawler", "deployProfile": "control-only"},
        {"role": "crawler-control", "deployProfile": "control-only", "active": True},
        {
            "role": "crawler-control",
            "deployProfile": "control-only",
            "remoteDir": "/opt/other",
        },
    ],
)
def test_registry_rejects_control_role_profile_mismatches(
    tmp_path: Path,
    overrides: dict[str, object],
) -> None:
    _write_registry(
        tmp_path,
        _control_entry(**overrides),
        target_name="gen1db",
    )

    with pytest.raises(ValueError, match="role|profile|active|remoteDir"):
        load_deploy_targets(tmp_path)


@pytest.mark.parametrize(
    "overrides",
    [
        {"role": "crawler", "deployProfile": "full-stack"},
        {"role": "primary", "deployProfile": "crawler-only"},
        {"role": "standby", "deployProfile": "crawler-only"},
        {"role": "crawler", "deployProfile": "crawler-only", "active": True},
        {"role": "crawler", "deployProfile": "crawler-only", "remoteDir": "/opt/other"},
        {"role": "primary", "deployProfile": "unknown"},
    ],
)
def test_registry_rejects_role_profile_mismatches(
    tmp_path: Path,
    overrides: dict[str, object],
) -> None:
    _write_registry(tmp_path, _crawler_entry(**overrides))

    with pytest.raises(ValueError, match="role|profile|active|remoteDir"):
        load_deploy_targets(tmp_path)


def test_registry_defaults_environment_to_production_and_rejects_unknown_values(
    tmp_path: Path,
) -> None:
    _write_registry(tmp_path, _crawler_entry())

    _default_target, targets = load_deploy_targets(tmp_path)
    assert targets["cloud"].environment == "production"
    assert targets["gen1crawler"].environment == "production"

    config_path = tmp_path / "config" / "deploy_servers.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["servers"]["gen1crawler"]["environment"] = "prod"
    config_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="environment"):
        load_deploy_targets(tmp_path)


def test_target_identity_matches_powershell_profile_aware_canonicalization() -> None:
    target = DeployTarget(
        name="gen1crawler",
        server="gen1crawler",
        user="sgm",
        domain="gen1crawler",
        remote_dir="/opt/mooncen",
        identity_file="ssh-agent",
        role="crawler",
        active=False,
        deploy_profile="crawler-only",
    )
    values = (
        ("name_b64", target.name),
        ("server_b64", target.server),
        ("user_b64", target.user),
        ("domain_b64", target.domain),
        ("remote_dir_b64", target.remote_dir),
        ("role_b64", target.role.lower()),
        ("deploy_profile_b64", target.deploy_profile.lower()),
        ("environment_b64", target.environment.lower()),
    )
    canonical = "\n".join(
        f"{key}={base64.b64encode(value.strip().encode('utf-8')).decode('ascii')}"
        for key, value in values
    )
    canonical += "\nactive=0"

    assert target.identity == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert target.public_dict(key_ready=True)["deploy_profile"] == "crawler-only"
    assert target.public_dict(key_ready=True)["environment"] == "production"


def test_worker_rejects_crawler_only_target_before_full_deployment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = DeployTarget(
        name="gen1crawler",
        server="gen1crawler",
        user="sgm",
        domain="gen1crawler",
        remote_dir="/opt/mooncen",
        identity_file="ssh-agent",
        role="crawler",
        active=False,
        deploy_profile="crawler-only",
    )
    monkeypatch.setattr(deployment_worker, "reviewed_target", lambda *_args: target)
    monkeypatch.setattr(
        deployment_worker, "normalized_environment", lambda: "production"
    )

    with pytest.raises(ValueError, match="crawler-only"):
        deployment_worker.validated_parameters(
            {
                "action": "deploy",
                "target": target.name,
                "target_commit": "1" * 40,
                "target_identity": target.identity,
                "service_type": "full",
                "skip_workers": False,
                "source_tree": "2" * 40,
            },
            root=ROOT,
            readiness={
                "available": True,
                "can_deploy": True,
                "snapshot": {"commit": "1" * 40, "source_tree": "2" * 40},
            },
        )


def test_worker_rejects_control_only_target_before_full_deployment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = DeployTarget(
        name="gen1db",
        server="gen1db",
        user="sgm",
        domain="gen1db",
        remote_dir="/opt/mooncen",
        identity_file="ssh-agent",
        role="crawler-control",
        active=False,
        deploy_profile="control-only",
    )
    monkeypatch.setattr(deployment_worker, "reviewed_target", lambda *_args: target)
    monkeypatch.setattr(
        deployment_worker, "normalized_environment", lambda: "production"
    )

    with pytest.raises(ValueError, match="cannot run a full application deployment"):
        deployment_worker.validated_parameters(
            {
                "action": "deploy",
                "target": target.name,
                "target_commit": "1" * 40,
                "target_identity": target.identity,
                "service_type": "full",
                "skip_workers": False,
                "source_tree": "2" * 40,
            },
            root=ROOT,
            readiness={
                "available": True,
                "can_deploy": True,
                "snapshot": {"commit": "1" * 40, "source_tree": "2" * 40},
            },
        )


def test_worker_rejects_target_from_a_different_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = DeployTarget(
        name="cloud",
        server="cloud",
        user="ubuntu",
        domain="mooncen.kr",
        remote_dir="/opt/mooncen",
        identity_file="ssh-agent",
        role="primary",
        active=True,
        environment="staging",
    )
    monkeypatch.setattr(deployment_worker, "reviewed_target", lambda *_args: target)
    monkeypatch.setattr(
        deployment_worker, "normalized_environment", lambda: "production"
    )

    with pytest.raises(ValueError, match="target environment.*worker environment"):
        deployment_worker.validated_parameters(
            {
                "action": "deploy",
                "target": target.name,
                "target_commit": "1" * 40,
                "target_identity": target.identity,
                "service_type": "full",
                "skip_workers": False,
                "source_tree": "2" * 40,
            },
            root=ROOT,
            readiness={
                "available": True,
                "can_deploy": True,
                "snapshot": {"commit": "1" * 40, "source_tree": "2" * 40},
            },
        )


def _readiness_target(
    name: str,
    *,
    role: str,
    deploy_profile: str,
    active: bool = False,
) -> DeployTarget:
    return DeployTarget(
        name=name,
        server=name,
        user="sgm" if name != "cloud" else "ubuntu",
        domain=name,
        remote_dir="/opt/mooncen",
        identity_file="ssh-agent",
        role=role,
        active=active,
        deploy_profile=deploy_profile,
    )


def _deployment_readiness_for_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    targets: dict[str, DeployTarget],
) -> dict[str, object]:
    topology = load_production_topology(ROOT)
    monkeypatch.setattr(
        deployment_registry,
        "load_deploy_targets",
        lambda _root: ("cloud", targets),
    )
    monkeypatch.setattr(
        deployment_registry,
        "load_production_topology",
        lambda _root: topology,
    )
    monkeypatch.setattr(
        deployment_registry,
        "git_snapshot",
        lambda _root: {"commit": "a" * 40},
    )
    monkeypatch.setattr(
        deployment_registry,
        "identity_file_ready",
        lambda _target: True,
    )
    monkeypatch.setattr(
        deployment_registry,
        "powershell_executable",
        lambda: "powershell.exe",
    )
    (tmp_path / deployment_registry.DEPLOY_SCRIPT_PATH).write_text(
        "# fixture\n",
        encoding="utf-8",
    )
    (tmp_path / deployment_registry.DEPLOY_LOCAL_PATH).write_text(
        "# fixture\n",
        encoding="utf-8",
    )
    return deployment_registry.deployment_readiness(tmp_path)


def _reviewed_readiness_targets() -> dict[str, DeployTarget]:
    return {
        "cloud": _readiness_target(
            "cloud",
            role="primary",
            deploy_profile="full-stack",
            active=True,
        ),
        "gen1crawler": _readiness_target(
            "gen1crawler",
            role="crawler",
            deploy_profile="crawler-only",
        ),
        "gen1db": _readiness_target(
            "gen1db",
            role="crawler-control",
            deploy_profile="control-only",
        ),
    }


def test_deployment_readiness_accepts_reviewed_service_profile_contracts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readiness = _deployment_readiness_for_targets(
        tmp_path,
        monkeypatch,
        _reviewed_readiness_targets(),
    )

    assert readiness["available"] is True
    assert not any(
        str(reason["code"]).startswith("topology_role_mismatch:")
        for reason in readiness["reasons"]
    )


@pytest.mark.parametrize(
    "target_name, wrong_role, wrong_profile",
    [
        ("gen1crawler", "crawler-control", "control-only"),
        ("gen1db", "crawler", "crawler-only"),
    ],
)
def test_deployment_readiness_rejects_limited_target_topology_role_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_name: str,
    wrong_role: str,
    wrong_profile: str,
) -> None:
    targets = _reviewed_readiness_targets()
    targets[target_name] = _readiness_target(
        target_name,
        role=wrong_role,
        deploy_profile=wrong_profile,
    )

    readiness = _deployment_readiness_for_targets(tmp_path, monkeypatch, targets)

    assert readiness["available"] is False
    assert f"topology_role_mismatch:{target_name}" in {
        reason["code"] for reason in readiness["reasons"]
    }


def test_ops_api_rejects_non_full_stack_profile_before_enqueue() -> None:
    source = (ROOT / "backend" / "routers" / "ops_v2.py").read_text(encoding="utf-8")

    assert 'target_row.get("deploy_profile") != "full-stack"' in source
    assert 'target.deploy_profile != "full-stack"' in source
    assert source.count("deployment_target_profile_forbidden") == 2
