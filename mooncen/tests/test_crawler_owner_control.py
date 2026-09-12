from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.routers import crawler_owner
from ops_agent.deployment_registry import DeployTarget


def _target(identity_file: str = "ssh-agent") -> DeployTarget:
    return DeployTarget(
        name="gen1crawler",
        server="gen1crawler",
        user="sgm",
        domain="gen1crawler",
        remote_dir="/opt/mooncen",
        identity_file=identity_file,
        role="crawler",
        active=False,
        deploy_profile="crawler-only",
        environment="production",
    )


def _control() -> crawler_owner._OwnerControl:
    return crawler_owner._OwnerControl(
        ssh="ssh",
        target=_target(),
        identity=None,
    )


def test_owner_ssh_commands_are_fixed_noninteractive_and_unforwarded() -> None:
    status_command = crawler_owner._ssh_command(_control(), crawler_owner._STATUS_ARGUMENTS)
    run_command = crawler_owner._ssh_command(_control(), crawler_owner._RUN_ARGUMENTS)

    assert status_command[:2] == ["ssh", "-o"]
    assert "BatchMode=yes" in status_command
    assert "ProxyCommand=none" in status_command
    assert "ProxyJump=none" in status_command
    assert "StrictHostKeyChecking=yes" in status_command
    assert "ClearAllForwardings=yes" in status_command
    assert "sgm@gen1crawler" in status_command
    assert status_command[-len(crawler_owner._STATUS_ARGUMENTS) :] == list(
        crawler_owner._STATUS_ARGUMENTS
    )
    assert run_command[-len(crawler_owner._RUN_ARGUMENTS) :] == [
        "/usr/bin/sudo",
        "-n",
        "--",
        "/usr/local/libexec/mooncen-ops-service",
        "crawler-once-start",
    ]
    assert "shell" not in run_command


def test_status_parser_keeps_only_reviewed_systemd_fields() -> None:
    parsed = crawler_owner._parse_status(
        "\n".join(
            (
                "Id=mooncen-crawler.timer",
                "ActiveState=inactive",
                "UnitFileState=disabled",
                "Unexpected=secret",
                "",
                "Id=mooncen-crawler-once.service",
                "ActiveState=inactive",
                "Result=success",
                "ExecMainStatus=0",
                "ExecMainStartTimestamp=Fri 2026-09-11 10:00:00 KST",
                "ExecMainExitTimestamp=Fri 2026-09-11 13:00:00 KST",
                "",
            )
        )
    )

    assert parsed["mooncen-crawler.timer"] == {
        "Id": "mooncen-crawler.timer",
        "ActiveState": "inactive",
        "UnitFileState": "disabled",
    }
    assert parsed["mooncen-crawler-once.service"]["Result"] == "success"
    assert parsed["mooncen-crawler-once.service"]["ExecMainStartTimestamp"].startswith("Fri 2026")
    assert "Unexpected" not in parsed["mooncen-crawler.timer"]


def test_owner_control_is_pinned_to_reviewed_legacy_crawler(monkeypatch: pytest.MonkeyPatch) -> None:
    topology = SimpleNamespace(
        crawler_mode="legacy",
        primary_for=lambda service: SimpleNamespace(node="gen1crawler", service_host="gen1crawler"),
    )
    monkeypatch.setattr(crawler_owner, "current_environment", lambda: "production")
    monkeypatch.setattr(crawler_owner, "load_production_topology", lambda: topology)
    monkeypatch.setattr(crawler_owner, "load_deploy_targets", lambda: ("cloud", {"gen1crawler": _target()}))
    monkeypatch.setattr(crawler_owner.shutil, "which", lambda executable: "C:/Windows/System32/OpenSSH/ssh.exe")

    control = crawler_owner._owner_control()

    assert control.target.name == "gen1crawler"
    assert control.target.user == "sgm"
    assert control.identity is None


class _DB:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/api/ops/crawlers/owner/run-all", "headers": []})


def test_run_all_audits_before_launching_fixed_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, object]] = []
    db = _DB()
    monkeypatch.setattr(
        crawler_owner,
        "_remote_status",
        lambda _control: {"timer": {}, "run": {"ActiveState": "inactive"}},
    )
    monkeypatch.setattr(crawler_owner, "_owner_control", _control)
    monkeypatch.setattr(crawler_owner, "_dispatch_snapshot", lambda: {"running": False})
    monkeypatch.setattr(
        crawler_owner,
        "append_audit",
        lambda *_args, **kwargs: events.append(("audit", kwargs)),
    )
    monkeypatch.setattr(
        crawler_owner,
        "_launch_dispatch",
        lambda control: events.append(("launch", control)) or {"pid": 42, "started_at": "now"},
    )

    result = crawler_owner.run_all_crawlers(
        crawler_owner.CrawlerOwnerRunRequest(confirmation="MOONCEN-CRAWLER-ALL"),
        _request(),
        SimpleNamespace(id=uuid4()),
        db,  # type: ignore[arg-type]
    )

    assert result["accepted"] is True
    assert db.commits == 1
    assert [event[0] for event in events] == ["audit", "launch"]
    audit = events[0][1]
    assert isinstance(audit, dict)
    assert audit["action"] == "crawler.run_all.request"
    assert audit["result"] == "success"
    assert audit["after_data"] == {"command": "crawler-once-start", "host": "gen1crawler"}


def test_dispatch_waits_for_the_remote_acceptance_result(monkeypatch: pytest.MonkeyPatch) -> None:
    completed = SimpleNamespace(returncode=0, stdout="ActiveState=activating\n", stderr="")
    monkeypatch.setattr(crawler_owner.subprocess, "run", lambda *_args, **_kwargs: completed)

    result = crawler_owner._launch_dispatch(_control())

    assert result["accepted"] is True
    assert crawler_owner._dispatch_snapshot()["running"] is False
    assert crawler_owner._dispatch_snapshot()["exit_code"] == 0


def test_dispatch_surfaces_remote_start_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    completed = SimpleNamespace(returncode=1, stdout="", stderr="sudo: a password is required\n")
    monkeypatch.setattr(crawler_owner.subprocess, "run", lambda *_args, **_kwargs: completed)

    with pytest.raises(RuntimeError, match="password is required"):
        crawler_owner._launch_dispatch(_control())

    snapshot = crawler_owner._dispatch_snapshot()
    assert snapshot["running"] is False
    assert snapshot["exit_code"] == 1
    assert snapshot["error"] == "sudo: a password is required"


def test_run_all_rejects_an_active_remote_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crawler_owner, "_owner_control", _control)
    monkeypatch.setattr(
        crawler_owner,
        "_remote_status",
        lambda _control: {"timer": {}, "run": {"ActiveState": "activating"}},
    )

    with pytest.raises(HTTPException) as raised:
        crawler_owner.run_all_crawlers(
            crawler_owner.CrawlerOwnerRunRequest(confirmation="MOONCEN-CRAWLER-ALL"),
            _request(),
            SimpleNamespace(id=uuid4()),
            _DB(),  # type: ignore[arg-type]
        )

    assert raised.value.status_code == 409


def test_run_all_requires_the_exact_server_side_confirmation() -> None:
    with pytest.raises(HTTPException) as raised:
        crawler_owner.run_all_crawlers(
            crawler_owner.CrawlerOwnerRunRequest(confirmation="yes"),
            _request(),
            SimpleNamespace(id=uuid4()),
            _DB(),  # type: ignore[arg-type]
        )

    assert raised.value.status_code == 422
