from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from ops_agent import deployment_registry


def _write_hold(tmp_path, payload: object) -> None:
    path = tmp_path / deployment_registry.DEPLOYMENT_HOLD_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_load_deployment_hold_accepts_only_bounded_active_payload(tmp_path) -> None:
    _write_hold(
        tmp_path,
        {
            "active": True,
            "code": "credential_rotation_required",
            "message": "Rotate the reviewed provider credential before deployment.",
        },
    )

    assert deployment_registry.load_deployment_hold(tmp_path) == {
        "code": "credential_rotation_required",
        "message": "Rotate the reviewed provider credential before deployment.",
    }


@pytest.mark.parametrize(
    "payload",
    (
        {"active": False, "code": "hold", "message": "disabled"},
        {"active": True, "code": "INVALID CODE", "message": "invalid"},
        {"active": True, "code": "hold", "message": "line one\nline two"},
    ),
)
def test_load_deployment_hold_fails_closed_for_invalid_payload(tmp_path, payload) -> None:
    _write_hold(tmp_path, payload)

    with pytest.raises(ValueError, match="deployment hold"):
        deployment_registry.load_deployment_hold(tmp_path)


def test_deployment_readiness_blocks_an_active_local_hold(tmp_path, monkeypatch) -> None:
    target = deployment_registry.DeployTarget(
        name="cloud",
        server="cloud",
        user="ubuntu",
        domain="example.test",
        remote_dir="/opt/mooncen",
        identity_file="ssh-agent",
        role="primary",
        active=True,
    )
    placement = SimpleNamespace(node="cloud", role="primary", replicates_from=None)
    crawler_placement = SimpleNamespace(
        node="gen1crawler",
        role="primary",
        replicates_from=None,
    )
    control_placement = SimpleNamespace(
        node="gen1db",
        role="primary",
        replicates_from=None,
    )
    topology = SimpleNamespace(
        nodes={
            "cloud": SimpleNamespace(dns_host="cloud"),
            "gen1crawler": SimpleNamespace(dns_host="gen1crawler"),
            "gen1db": SimpleNamespace(dns_host="gen1db"),
        },
        services={
            "frontend": [placement],
            "backend": [placement],
            "database": [placement],
            "staging_database": [control_placement],
            "crawler": [crawler_placement],
            "crawler_control": [control_placement],
        },
        active_node="cloud",
        public_payload=lambda: {"activeNode": "cloud"},
    )
    monkeypatch.setattr(
        deployment_registry,
        "load_deploy_targets",
        lambda _root: ("cloud", {"cloud": target}),
    )
    monkeypatch.setattr(deployment_registry, "load_production_topology", lambda _root: topology)
    monkeypatch.setattr(deployment_registry, "git_snapshot", lambda _root: {"commit": "a" * 40})
    monkeypatch.setattr(deployment_registry, "identity_file_ready", lambda _target: True)
    monkeypatch.setattr(deployment_registry, "powershell_executable", lambda: "powershell.exe")
    (tmp_path / deployment_registry.DEPLOY_SCRIPT_PATH).write_text("# fixture\n", encoding="utf-8")
    (tmp_path / deployment_registry.DEPLOY_LOCAL_PATH).write_text("# fixture\n", encoding="utf-8")
    _write_hold(
        tmp_path,
        {
            "active": True,
            "code": "kakao_rest_key_rotation_required",
            "message": "Kakao REST credential rotation must be confirmed before deployment.",
        },
    )

    readiness = deployment_registry.deployment_readiness(tmp_path)

    assert readiness["available"] is True
    assert readiness["can_deploy"] is False
    assert readiness["deployment_hold"] == {
        "code": "kakao_rest_key_rotation_required",
        "message": "Kakao REST credential rotation must be confirmed before deployment.",
    }
    assert readiness["deployment_hold"] in readiness["reasons"]
