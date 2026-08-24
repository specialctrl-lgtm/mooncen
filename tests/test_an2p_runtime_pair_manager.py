from __future__ import annotations

import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import subprocess
import threading

import pytest

from deploy.an2p import runtime_pair_manager as manager


PAIR_A = f"runtime-pair.{'1' * 40}.{'2' * 40}.{'3' * 64}"
PAIR_B = f"runtime-pair.{'4' * 40}.{'5' * 40}.{'6' * 64}"


def test_manager_health_recovery_ignores_inherited_proxy_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"target": 0, "proxy": 0}

    class TargetHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler ABI.
            calls["target"] += 1
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ready"}')

        def log_message(self, *_args: object) -> None:
            return

    class ProxyCanaryHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler ABI.
            calls["proxy"] += 1
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            return

    target = ThreadingHTTPServer(("127.0.0.1", 0), TargetHandler)
    proxy = ThreadingHTTPServer(("127.0.0.1", 0), ProxyCanaryHandler)
    target_thread = threading.Thread(target=target.serve_forever, daemon=True)
    proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    target_thread.start()
    proxy_thread.start()
    try:
        proxy_url = f"http://127.0.0.1:{proxy.server_port}"
        for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
            monkeypatch.setenv(name, proxy_url)
        for name in ("NO_PROXY", "no_proxy"):
            monkeypatch.setenv(name, "")
        manager._wait_http(
            f"http://127.0.0.1:{target.server_port}/health",
            timeout=2,
        )
    finally:
        target.shutdown()
        proxy.shutdown()
        target.server_close()
        proxy.server_close()
        target_thread.join(timeout=2)
        proxy_thread.join(timeout=2)

    assert calls == {"target": 1, "proxy": 0}


def test_manager_health_recovery_rejects_redirect_without_contacting_sink() -> None:
    calls = {"redirect": 0, "sink": 0}

    class SinkHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler ABI.
            calls["sink"] += 1
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ready"}')

        def log_message(self, *_args: object) -> None:
            return

    sink = ThreadingHTTPServer(("127.0.0.1", 0), SinkHandler)

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler ABI.
            calls["redirect"] += 1
            self.send_response(302)
            self.send_header(
                "Location",
                f"http://127.0.0.1:{sink.server_port}/health",
            )
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            return

    redirect = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    threads = [
        threading.Thread(target=server.serve_forever, daemon=True)
        for server in (redirect, sink)
    ]
    for thread in threads:
        thread.start()
    try:
        with pytest.raises(manager.PairManagerError, match="did not recover"):
            manager._wait_http(
                f"http://127.0.0.1:{redirect.server_port}/health",
                timeout=1,
            )
    finally:
        for server in (redirect, sink):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=2)

    assert calls["redirect"] >= 1
    assert calls["sink"] == 0


def _snapshot(*, target: str, previous: str | None, phase: str) -> dict[str, object]:
    return {
        "development_only": False,
        "docker_selected": True,
        "ops_api_enabled": True,
        "phase": phase,
        "previous": previous,
        "previous_docker_selected": True,
        "schema_version": 1,
        "status_enabled": True,
        "target": target,
        "tunnel_enabled": True,
        "worker_enabled": True,
    }


def test_first_pair_activation_rejects_legacy_docker_before_journal_or_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    snapshot = _snapshot(target=PAIR_A, previous=None, phase="prepared")
    snapshot.update(
        {
            "development_only": True,
            "ops_api_enabled": False,
            "status_enabled": False,
            "tunnel_enabled": False,
            "worker_enabled": False,
        }
    )
    monkeypatch.setattr(manager, "validate_pair", lambda name: events.append(("validate", name)))
    monkeypatch.setattr(manager, "recover", lambda: events.append("recover"))
    monkeypatch.setattr(manager, "current_pair", lambda: None)
    monkeypatch.setattr(manager, "_snapshot", lambda *_args, **_kwargs: snapshot)
    monkeypatch.setattr(manager, "_write_journal", lambda _value: events.append("journal"))
    monkeypatch.setattr(manager, "_stop_units", lambda: events.append("stop"))

    with pytest.raises(manager.PairManagerError, match="legacy Docker"):
        manager.activate_development(PAIR_A)

    assert events == [("validate", PAIR_A), "recover"]


def test_failed_pair_activation_restores_one_previous_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = {"pair": PAIR_A}
    events: list[object] = []
    start_attempts = 0

    monkeypatch.setattr(manager, "validate_pair", lambda name: events.append(("validate", name)))
    monkeypatch.setattr(manager, "recover", lambda: events.append("recover"))
    monkeypatch.setattr(manager, "current_pair", lambda: current["pair"])
    monkeypatch.setattr(
        manager,
        "_snapshot",
        lambda target, previous: _snapshot(
            target=target,
            previous=previous,
            phase="prepared",
        ),
    )
    monkeypatch.setattr(
        manager,
        "_write_journal",
        lambda value: events.append(("journal", value["phase"])),
    )
    monkeypatch.setattr(manager, "_stop_units", lambda: events.append("stop"))
    monkeypatch.setattr(manager, "_apply_unit_enablement", lambda _value: None)

    def switch(name: str | None) -> None:
        events.append(("switch", name))
        current["pair"] = name

    def start(_value: dict[str, object]) -> None:
        nonlocal start_attempts
        start_attempts += 1
        events.append(("start", current["pair"]))
        if start_attempts == 1:
            raise RuntimeError("new runtime health failed")

    monkeypatch.setattr(manager, "_switch_pointer", switch)
    monkeypatch.setattr(manager, "_start_units", start)
    monkeypatch.setattr(manager, "_clear_journal", lambda: events.append("clear"))

    with pytest.raises(RuntimeError, match="health failed"):
        manager.activate(PAIR_B)

    assert current["pair"] == PAIR_A
    assert events == [
        ("validate", PAIR_B),
        "recover",
        ("journal", "prepared"),
        "stop",
        ("switch", PAIR_B),
        ("journal", "switched"),
        ("start", PAIR_B),
        "stop",
        ("switch", PAIR_A),
        ("start", PAIR_A),
        "clear",
    ]


@pytest.mark.parametrize(
    ("phase", "previous", "expected"),
    (
        ("prepared", PAIR_A, PAIR_A),
        ("switched", None, PAIR_B),
    ),
)
def test_boot_recovery_converges_pointer_without_starting_dependents(
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    previous: str | None,
    expected: str,
) -> None:
    events: list[object] = []
    monkeypatch.setattr(
        manager,
        "_load_journal",
        lambda: _snapshot(target=PAIR_B, previous=previous, phase=phase),
    )
    monkeypatch.setattr(manager, "_stop_units", lambda: events.append("stop"))
    monkeypatch.setattr(
        manager,
        "_switch_pointer",
        lambda name: events.append(("switch", name)),
    )
    monkeypatch.setattr(manager, "_clear_journal", lambda: events.append("clear"))
    monkeypatch.setattr(manager, "_apply_unit_enablement", lambda _value: None)
    monkeypatch.setattr(
        manager,
        "_start_units",
        lambda _value: pytest.fail("boot recovery must leave ordered dependents to systemd"),
    )

    manager.recover_boot()

    assert events == ["stop", ("switch", expected), "clear"]


def test_clean_boot_still_performs_full_active_pair_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(manager, "_load_journal", lambda: None)
    monkeypatch.setattr(
        manager,
        "current_pair",
        lambda *, full_validation=True: calls.append(full_validation) or PAIR_A,
    )
    monkeypatch.setattr(
        manager,
        "_validate_pair_structure",
        lambda _name: (None, None, None, {"receipt_digest": "7" * 64}),
    )
    monkeypatch.setattr(manager, "_write_boot_validation", lambda *_args: None)

    manager.recover_boot()

    assert calls == [True]


def test_service_start_gate_requires_live_exact_transaction_authorization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    journal = tmp_path / "transaction.json"
    authorization = tmp_path / "start-authorization.json"
    lock = tmp_path / "operation.lock"
    monkeypatch.setattr(manager, "JOURNAL", journal)
    monkeypatch.setattr(manager, "LOCK", lock)
    monkeypatch.setattr(manager, "START_AUTHORIZATION", authorization)
    monkeypatch.setattr(
        manager,
        "current_pair",
        lambda *, full_validation=True: PAIR_B,
    )
    monkeypatch.setattr(manager, "_require_boot_validation", lambda _pair: None)
    monkeypatch.setattr(manager, "control_finalized", lambda _pair: True)

    manager.gate_service_start(manager.SYSTEM_UNITS["ops_api"])

    journal.write_text("journal\n", encoding="ascii")
    owner = os.open(lock, os.O_RDWR)
    fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
    monkeypatch.setattr(
        manager,
        "_load_journal",
        lambda **_kwargs: _snapshot(
            target=PAIR_B,
            previous=PAIR_A,
            phase="switched",
        ),
    )
    with pytest.raises(manager.PairManagerError, match="blocks service start"):
        manager.gate_service_start(manager.SYSTEM_UNITS["ops_api"])

    authorization.write_text("authorization\n", encoding="ascii")
    now = 1_800_000_000
    value = {
        "boot_id": "11111111-2222-3333-4444-555555555555",
        "deadline_epoch": now + 60,
        "journal_sha256": "7" * 64,
        "pair": PAIR_B,
        "pid": 1234,
        "pid_start_ticks": 5678,
        "schema_version": 1,
        "unit": manager.SYSTEM_UNITS["ops_api"],
    }
    monkeypatch.setattr(manager, "_load_canonical", lambda *_args, **_kwargs: value)
    monkeypatch.setattr(manager, "_journal_sha256", lambda: "7" * 64)
    monkeypatch.setattr(
        manager,
        "_boot_id",
        lambda: "11111111-2222-3333-4444-555555555555",
    )
    monkeypatch.setattr(manager, "_pid_start_ticks", lambda _pid: 5678)
    monkeypatch.setattr(manager.time, "time", lambda: now)

    manager.gate_service_start(manager.SYSTEM_UNITS["ops_api"])

    value["deadline_epoch"] = now
    with pytest.raises(manager.PairManagerError, match="authorization is invalid"):
        manager.gate_service_start(manager.SYSTEM_UNITS["ops_api"])
    os.close(owner)


def test_service_start_gate_rejects_lock_held_before_journal_publish(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lock = tmp_path / "operation.lock"
    descriptor = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    monkeypatch.setattr(manager, "LOCK", lock)
    monkeypatch.setattr(manager, "JOURNAL", tmp_path / "transaction.json")
    monkeypatch.setattr(
        manager,
        "START_AUTHORIZATION",
        tmp_path / "start-authorization.json",
    )
    monkeypatch.setattr(
        manager,
        "current_pair",
        lambda *, full_validation=True: PAIR_A,
    )
    monkeypatch.setattr(manager, "_require_boot_validation", lambda _pair: None)
    monkeypatch.setattr(manager, "control_finalized", lambda _pair: True)
    try:
        with pytest.raises(manager.PairManagerError, match="blocks service start"):
            manager.gate_service_start(manager.SYSTEM_UNITS["ops_api"])
    finally:
        os.close(descriptor)


def test_failed_systemd_start_always_revokes_short_lived_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        manager,
        "_write_start_authorization",
        lambda _unit: events.append("authorize"),
    )
    monkeypatch.setattr(
        manager,
        "_systemctl",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            manager.PairManagerError("start failed")
        ),
    )
    monkeypatch.setattr(
        manager,
        "_clear_start_authorization",
        lambda: events.append("revoke"),
    )

    with pytest.raises(manager.PairManagerError, match="start failed"):
        manager._authorized_systemctl_start(manager.SYSTEM_UNITS["worker"])

    assert events == ["authorize", "revoke"]


def test_unfinalized_pair_gate_allows_docker_but_rejects_api_and_worker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(manager, "LOCK", tmp_path / "operation.lock")
    monkeypatch.setattr(manager, "JOURNAL", tmp_path / "transaction.json")
    monkeypatch.setattr(
        manager,
        "START_AUTHORIZATION",
        tmp_path / "start-authorization.json",
    )
    monkeypatch.setattr(
        manager,
        "current_pair",
        lambda *, full_validation=True: PAIR_B,
    )
    monkeypatch.setattr(manager, "_require_boot_validation", lambda _pair: None)
    monkeypatch.setattr(manager, "control_finalized", lambda _pair: False)

    manager.gate_service_start(manager.SYSTEM_UNITS["docker"])
    for unit in (manager.SYSTEM_UNITS["ops_api"], manager.SYSTEM_UNITS["worker"]):
        with pytest.raises(manager.PairManagerError, match="finalized registration"):
            manager.gate_service_start(unit)


def test_development_activation_commits_pending_only_after_docker_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    current = {"pair": PAIR_A}
    snapshot = {
        **_snapshot(target=PAIR_B, previous=PAIR_A, phase="prepared"),
        "development_only": True,
        "ops_api_enabled": False,
        "status_enabled": False,
        "tunnel_enabled": False,
        "worker_enabled": False,
    }
    monkeypatch.setattr(manager, "validate_pair", lambda pair: events.append(("validate", pair)))
    monkeypatch.setattr(manager, "recover", lambda: events.append("recover"))
    monkeypatch.setattr(manager, "current_pair", lambda: current["pair"])
    monkeypatch.setattr(
        manager,
        "_snapshot",
        lambda target, previous, *, development_only=False: (
            events.append(("snapshot", development_only)) or dict(snapshot)
        ),
    )
    monkeypatch.setattr(
        manager,
        "_write_journal",
        lambda value: events.append(("journal", value["phase"])),
    )
    monkeypatch.setattr(manager, "_stop_units", lambda: events.append("stop"))

    def switch(pair: str | None) -> None:
        current["pair"] = pair
        events.append(("switch", pair))

    monkeypatch.setattr(manager, "_switch_pointer", switch)
    monkeypatch.setattr(manager, "_apply_unit_enablement", lambda _value: events.append("enable"))
    monkeypatch.setattr(manager, "_start_units", lambda _value: events.append("healthy"))
    monkeypatch.setattr(
        manager,
        "_write_pending_control_finalization",
        lambda pair, *, replace_pair=None: events.append(
            ("pending", pair, replace_pair)
        ),
    )
    monkeypatch.setattr(
        manager,
        "_selector_status",
        lambda: events.append("exclusive-docker") or {"docker_selected": True},
    )
    monkeypatch.setattr(manager, "_clear_journal", lambda: events.append("clear"))

    result = manager.activate_development(PAIR_B)

    assert result["active_pair"] == PAIR_B
    assert (
        events.index("healthy")
        < events.index("exclusive-docker")
        < events.index(("pending", PAIR_B, PAIR_A))
        < events.index("clear")
    )
    assert ("snapshot", True) in events


def test_manager_selector_child_inherits_the_held_operation_fence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    operation_lock = tmp_path / "operation.lock"
    descriptor = os.open(operation_lock, os.O_RDWR | os.O_CREAT, 0o600)
    observed: dict[str, object] = {}

    def run(*_args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed.update(kwargs)
        return subprocess.CompletedProcess(("selector",), 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(manager.subprocess, "run", run)
    manager._OPERATION_LOCK_DESCRIPTOR = descriptor
    try:
        completed = manager._run_selector("docker-select")
    finally:
        manager._OPERATION_LOCK_DESCRIPTOR = None
        os.close(descriptor)

    assert completed.returncode == 0
    assert observed["pass_fds"] == (descriptor,)
    assert observed["env"] == {
        "HOME": "/root",
        "LANG": "C",
        "LC_ALL": "C",
        "MOONCEN_AN2P_MANAGER_LOCK_FD": str(descriptor),
        "PATH": "/usr/bin:/bin",
    }


def test_development_rollback_restores_finalized_previous_and_clears_target_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    applied: list[dict[str, object]] = []
    snapshot = {
        **_snapshot(target=PAIR_B, previous=PAIR_A, phase="switched"),
        "development_only": True,
        "ops_api_enabled": False,
        "status_enabled": False,
        "tunnel_enabled": False,
        "worker_enabled": False,
    }
    monkeypatch.setattr(manager, "_stop_units", lambda: events.append("stop"))
    monkeypatch.setattr(
        manager,
        "_switch_pointer",
        lambda pair: events.append(("switch", pair)),
    )
    monkeypatch.setattr(manager, "control_finalized", lambda pair: pair == PAIR_A)
    monkeypatch.setattr(manager, "_apply_unit_enablement", lambda value: applied.append(value))
    monkeypatch.setattr(manager, "_start_units", lambda _value: events.append("start"))
    monkeypatch.setattr(
        manager,
        "_clear_pending_control_finalization",
        lambda pair: events.append(("clear-pending", pair)),
    )
    monkeypatch.setattr(
        manager,
        "_clear_journal",
        lambda: events.append("clear-journal"),
    )

    manager._rollback_transaction(snapshot)

    assert applied[0]["docker_selected"] is True
    assert applied[0]["ops_api_enabled"] is True
    assert applied[0]["status_enabled"] is True
    assert applied[0]["tunnel_enabled"] is True
    assert applied[0]["worker_enabled"] is True
    assert events == [
        "stop",
        ("switch", PAIR_A),
        "start",
        ("clear-pending", PAIR_B),
        "clear-journal",
    ]


def test_development_rollback_restores_unfinalized_previous_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    snapshot = {
        **_snapshot(target=PAIR_B, previous=PAIR_A, phase="switched"),
        "development_only": True,
        "ops_api_enabled": False,
        "status_enabled": False,
        "tunnel_enabled": False,
        "worker_enabled": False,
    }
    monkeypatch.setattr(manager, "_stop_units", lambda: events.append("stop"))
    monkeypatch.setattr(manager, "_switch_pointer", lambda pair: events.append(("switch", pair)))
    monkeypatch.setattr(manager, "control_finalized", lambda _pair: False)
    monkeypatch.setattr(manager, "_apply_unit_enablement", lambda _value: None)
    monkeypatch.setattr(manager, "_start_units", lambda _value: events.append("start"))
    monkeypatch.setattr(
        manager,
        "_clear_pending_control_finalization",
        lambda pair: events.append(("clear-pending", pair)),
    )
    monkeypatch.setattr(
        manager,
        "_write_pending_control_finalization",
        lambda pair, *, replace_pair=None: events.append(
            ("write-pending", pair, replace_pair)
        ),
    )
    monkeypatch.setattr(manager, "_clear_journal", lambda: events.append("clear-journal"))

    manager._rollback_transaction(snapshot)

    assert events == [
        "stop",
        ("switch", PAIR_A),
        "start",
        ("clear-pending", PAIR_B),
        ("write-pending", PAIR_A, None),
        "clear-journal",
    ]


@pytest.mark.parametrize("phase", ["prepared", "switched"])
def test_boot_recovery_rolls_back_interrupted_initial_development_commit(
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    events: list[object] = []
    applied: list[dict[str, object]] = []
    snapshot = {
        **_snapshot(target=PAIR_B, previous=None, phase=phase),
        "development_only": True,
        "ops_api_enabled": False,
        "previous_docker_selected": False,
        "status_enabled": False,
        "tunnel_enabled": False,
        "worker_enabled": False,
    }
    monkeypatch.setattr(manager, "_load_journal", lambda: snapshot)
    monkeypatch.setattr(manager, "_stop_units", lambda: events.append("stop"))
    monkeypatch.setattr(manager, "_switch_pointer", lambda pair: events.append(("switch", pair)))
    monkeypatch.setattr(manager, "_apply_unit_enablement", lambda value: applied.append(value))
    monkeypatch.setattr(
        manager,
        "_clear_pending_control_finalization",
        lambda pair: events.append(("clear-pending", pair)),
    )
    monkeypatch.setattr(manager, "_clear_journal", lambda: events.append("clear-journal"))
    monkeypatch.setattr(
        manager,
        "_start_units",
        lambda value: events.append(("start", value["docker_selected"])),
    )
    monkeypatch.setattr(manager, "_select_native", lambda: events.append("native"))

    manager.recover_boot()

    assert applied[0]["docker_selected"] is False
    assert events == [
        "stop",
        ("switch", None),
        ("start", False),
        "native",
        ("clear-pending", PAIR_B),
        "clear-journal",
    ]


def test_initial_development_health_failure_restores_native_without_pointer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    current: dict[str, str | None] = {"pair": None}
    snapshot = {
        **_snapshot(target=PAIR_B, previous=None, phase="prepared"),
        "development_only": True,
        "ops_api_enabled": False,
        "previous_docker_selected": False,
        "status_enabled": False,
        "tunnel_enabled": False,
        "worker_enabled": False,
    }
    starts = 0

    monkeypatch.setattr(manager, "validate_pair", lambda _pair: None)
    monkeypatch.setattr(manager, "recover", lambda: None)
    monkeypatch.setattr(manager, "current_pair", lambda: current["pair"])
    monkeypatch.setattr(
        manager,
        "_snapshot",
        lambda *_args, **_kwargs: dict(snapshot),
    )
    monkeypatch.setattr(manager, "_write_journal", lambda value: events.append(value["phase"]))
    monkeypatch.setattr(manager, "_stop_units", lambda: events.append("stop"))

    def switch(pair: str | None) -> None:
        current["pair"] = pair
        events.append(("switch", pair))

    def start(value: dict[str, object]) -> None:
        nonlocal starts
        starts += 1
        events.append(("start", value["docker_selected"]))
        if starts == 1:
            raise manager.PairManagerError("Docker health failed")

    monkeypatch.setattr(manager, "_switch_pointer", switch)
    monkeypatch.setattr(manager, "_apply_unit_enablement", lambda _value: None)
    monkeypatch.setattr(manager, "_start_units", start)
    monkeypatch.setattr(manager, "_select_native", lambda: events.append("native"))
    monkeypatch.setattr(
        manager,
        "_clear_pending_control_finalization",
        lambda pair: events.append(("clear-pending", pair)),
    )
    monkeypatch.setattr(manager, "_clear_journal", lambda: events.append("clear"))

    with pytest.raises(manager.PairManagerError, match="Docker health failed"):
        manager.activate_development(PAIR_B)

    assert current["pair"] is None
    assert events[-5:] == [
        ("switch", None),
        ("start", False),
        "native",
        ("clear-pending", PAIR_B),
        "clear",
    ]
