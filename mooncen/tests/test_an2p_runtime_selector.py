from __future__ import annotations

import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import threading

import pytest

from deploy.an2p import mooncen_an2p_service_control as selector


def _completed(stdout: bytes = b"") -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(("systemctl",), 0, stdout=stdout, stderr=b"")


def test_selector_uses_fixed_root_owned_lxc_and_rejects_extra_arguments() -> None:
    assert selector.command_for(("lxd-db-start",)) == (
        "/usr/sbin/lxc",
        "start",
        "mooncen-dev-db",
    )
    assert selector.command_for(("lxd-db-stop",)) == (
        "/usr/sbin/lxc",
        "stop",
        "mooncen-dev-db",
    )
    assert selector.command_for(("lxd-db-status",)) == (
        "/usr/sbin/lxc",
        "list",
        "mooncen-dev-db",
        "--format=json",
    )
    with pytest.raises(selector.ControlError):
        selector.command_for(("lxd-db-start", "unreviewed-instance"))
    with pytest.raises(selector.ControlError):
        selector.command_for(("docker-start",))


def _reviewed_lxd_configuration() -> dict[str, object]:
    idmap = json.dumps(
        [
            {
                "Hostid": 1_000_000,
                "Isgid": False,
                "Isuid": True,
                "Maprange": 1_000_000_000,
                "Nsid": 0,
            },
            {
                "Hostid": 1_000_000,
                "Isgid": True,
                "Isuid": False,
                "Maprange": 1_000_000_000,
                "Nsid": 0,
            },
        ],
        separators=(",", ":"),
    )
    return {
        "architecture": "x86_64",
        "expanded_config": {
            "boot.autostart": "true",
            "boot.autostart.priority": "20",
            "image.os": "Ubuntu",
            "volatile.base_image": "1" * 64,
            "volatile.idmap.base": "0",
            "volatile.idmap.current": idmap,
            "volatile.idmap.next": idmap,
            "volatile.last_state.idmap": "[]",
        },
        "expanded_devices": {
            name: dict(device)
            for name, device in selector.LXD_DEVICE_CONTRACT.items()
        },
        "ephemeral": False,
        "name": "mooncen-dev-db",
        "profiles": ["default"],
        "stateful": False,
        "status": "Stopped",
        "type": "container",
    }


def test_lxd_database_start_preflight_rejects_preplanted_host_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = _reviewed_lxd_configuration()
    monkeypatch.setattr(
        selector,
        "_run",
        lambda _argv: _completed(
            json.dumps(value, separators=(",", ":")).encode("utf-8")
        ),
    )
    selector._validate_lxd_database()

    value["expanded_config"]["raw.lxc"] = "lxc.mount.entry=/ host none bind 0 0"  # type: ignore[index]
    with pytest.raises(selector.ControlError, match="unsafe key"):
        selector._validate_lxd_database()
    value["expanded_config"].pop("raw.lxc")  # type: ignore[union-attr]
    value["expanded_devices"]["host"] = {  # type: ignore[index]
        "path": "/host",
        "source": "/",
        "type": "disk",
    }
    with pytest.raises(selector.ControlError, match="boundary drifted"):
        selector._validate_lxd_database()


def test_unit_state_parser_accepts_only_exact_loaded_terminal_states(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "LoadState": b"loaded\n",
        "ActiveState": b"inactive\n",
        "UnitFileState": b"disabled\n",
    }

    def fake_systemctl(*arguments: str, **_kwargs: object):
        property_name = next(
            item.partition("=")[2]
            for item in arguments
            if item.startswith("--property=")
        )
        return _completed(values[property_name])

    monkeypatch.setattr(selector, "_systemctl", fake_systemctl)

    assert selector._unit_flag("mooncen-api.service", "active") is False
    assert selector._unit_flag("mooncen-api.service", "enabled") is False
    values["ActiveState"] = b"active\n"
    values["UnitFileState"] = b"enabled\n"
    assert selector._unit_flag("mooncen-api.service", "active") is True
    assert selector._unit_flag("mooncen-api.service", "enabled") is True

    values["ActiveState"] = b"failed\n"
    with pytest.raises(selector.ControlError, match="indeterminate"):
        selector._unit_flag("mooncen-api.service", "active")
    values["ActiveState"] = b"inactive\n"
    values["LoadState"] = b"not-found\n"
    with pytest.raises(selector.ControlError, match="not loaded"):
        selector._unit_flag("mooncen-api.service", "active")


def test_health_gate_ignores_inherited_proxy_environment(
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
        target_url = f"http://127.0.0.1:{target.server_port}/health"
        proxy_url = f"http://127.0.0.1:{proxy.server_port}"
        monkeypatch.setattr(selector, "HEALTH_URLS", (target_url,))
        for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
            monkeypatch.setenv(name, proxy_url)
        for name in ("NO_PROXY", "no_proxy"):
            monkeypatch.setenv(name, "")

        selector._wait_for_health(timeout_seconds=2)
    finally:
        target.shutdown()
        proxy.shutdown()
        target.server_close()
        proxy.server_close()
        target_thread.join(timeout=2)
        proxy_thread.join(timeout=2)

    assert calls == {"target": 1, "proxy": 0}


def test_health_gate_rejects_redirect_without_contacting_sink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        monkeypatch.setattr(
            selector,
            "HEALTH_URLS",
            (f"http://127.0.0.1:{redirect.server_port}/health",),
        )
        with pytest.raises(selector.ControlError, match="did not become healthy"):
            selector._wait_for_health(timeout_seconds=1)
    finally:
        for server in (redirect, sink):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=2)

    assert calls["redirect"] >= 1
    assert calls["sink"] == 0


def test_docker_selection_publishes_fence_before_stopping_native(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {
        "marker": False,
        "docker_active": False,
        "docker_enabled": False,
        "native_active": {unit: True for unit in selector.NATIVE_UNITS},
        "native_enabled": {unit: True for unit in selector.NATIVE_UNITS},
    }
    events: list[str] = []

    def set_marker(present: bool) -> None:
        events.append(f"marker:{present}")
        state["marker"] = present

    def systemctl(*arguments: str, user: bool = False, **_kwargs: object):
        if arguments[:2] == ("disable", "--now") and user:
            events.append("native:disable")
            for unit in selector.NATIVE_UNITS:
                state["native_active"][unit] = False  # type: ignore[index]
                state["native_enabled"][unit] = False  # type: ignore[index]
        elif arguments[:1] == ("reset-failed",) and user:
            events.append("native:reset-failed")
        elif arguments == ("enable", "--now", selector.DOCKER_UNIT) and not user:
            events.append("docker:enable")
            state["docker_active"] = True
            state["docker_enabled"] = True
        else:  # pragma: no cover - an unexpected mutation is the failure.
            raise AssertionError((arguments, user))
        return _completed()

    def unit_flag(unit: str, property_name: str, *, user: bool = False) -> bool:
        if user:
            return bool(state[f"native_{property_name}"][unit])  # type: ignore[index]
        return bool(state[f"docker_{property_name}"])

    monkeypatch.setattr(selector, "_set_marker", set_marker)
    monkeypatch.setattr(selector, "_systemctl", systemctl)
    monkeypatch.setattr(selector, "_unit_flag", unit_flag)
    monkeypatch.setattr(selector, "_wait_for_health", lambda: events.append("health"))

    selector._select_docker()

    assert events == [
        "marker:True",
        "native:disable",
        "native:reset-failed",
        "docker:enable",
        "health",
    ]
    assert state["marker"] is True
    assert state["docker_active"] is True
    assert not any(state["native_active"].values())  # type: ignore[union-attr]


def test_docker_selection_failure_leaves_a_safe_downtime_fence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = {"present": False}
    events: list[str] = []

    def set_marker(present: bool) -> None:
        marker["present"] = present
        events.append(f"marker:{present}")

    def fail_native_stop(*_arguments: str, **_kwargs: object):
        events.append("native-stop-failed")
        raise selector.ControlError("fixed runtime control command failed")

    monkeypatch.setattr(selector, "_set_marker", set_marker)
    monkeypatch.setattr(selector, "_systemctl", fail_native_stop)

    with pytest.raises(selector.ControlError):
        selector._select_docker()

    assert marker["present"] is True
    assert events == ["marker:True", "native-stop-failed"]


def test_native_selection_stops_docker_before_removing_fence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {
        "marker": True,
        "docker_active": True,
        "docker_enabled": True,
        "native_active": {unit: False for unit in selector.NATIVE_UNITS},
        "native_enabled": {unit: False for unit in selector.NATIVE_UNITS},
    }
    events: list[str] = []

    def set_marker(present: bool) -> None:
        events.append(f"marker:{present}")
        state["marker"] = present

    def systemctl(*arguments: str, user: bool = False, **_kwargs: object):
        if arguments == ("disable", "--now", selector.DOCKER_UNIT) and not user:
            events.append("docker:disable")
            state["docker_active"] = False
            state["docker_enabled"] = False
        elif arguments == ("reset-failed", selector.DOCKER_UNIT) and not user:
            events.append("docker:reset-failed")
        elif arguments[:2] == ("enable", "--now") and user:
            events.append("native:enable")
            for unit in selector.NATIVE_UNITS:
                state["native_active"][unit] = True  # type: ignore[index]
                state["native_enabled"][unit] = True  # type: ignore[index]
        else:  # pragma: no cover - an unexpected mutation is the failure.
            raise AssertionError((arguments, user))
        return _completed()

    def unit_flag(unit: str, property_name: str, *, user: bool = False) -> bool:
        if user:
            return bool(state[f"native_{property_name}"][unit])  # type: ignore[index]
        return bool(state[f"docker_{property_name}"])

    monkeypatch.setattr(selector, "_set_marker", set_marker)
    monkeypatch.setattr(selector, "_systemctl", systemctl)
    monkeypatch.setattr(selector, "_unit_flag", unit_flag)
    monkeypatch.setattr(selector, "_wait_for_health", lambda: events.append("health"))

    selector._select_native()

    assert events == [
        "docker:disable",
        "docker:reset-failed",
        "marker:False",
        "native:enable",
        "health",
    ]
    assert state["marker"] is False
    assert state["docker_active"] is False
    assert all(state["native_active"].values())  # type: ignore[union-attr]


def test_native_selection_never_removes_fence_when_docker_stop_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    def fail_docker_stop(*_arguments: str, **_kwargs: object):
        events.append("docker-stop-failed")
        raise selector.ControlError("fixed runtime control command failed")

    monkeypatch.setattr(selector, "_systemctl", fail_docker_stop)
    monkeypatch.setattr(
        selector,
        "_set_marker",
        lambda _present: events.append("marker-mutated"),
    )

    with pytest.raises(selector.ControlError):
        selector._select_native()

    assert events == ["docker-stop-failed"]


def test_manual_selection_waits_for_manager_commit_and_rejects_crash_journal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path
    operation_lock = root / "operation.lock"
    selection_lock = root / "selection.lock"
    journal = root / "transaction.json"
    monkeypatch.setattr(selector, "MANAGER_OPERATION_LOCK", operation_lock)
    monkeypatch.setattr(selector, "MANAGER_JOURNAL", journal)
    monkeypatch.setattr(selector, "SELECTION_LOCK", selection_lock)
    monkeypatch.setattr(selector.os, "fchown", lambda *_args: None)
    monkeypatch.delenv(selector.MANAGER_LOCK_FD_ENV, raising=False)
    events: list[str] = []
    started = threading.Event()
    failure: list[BaseException] = []
    monkeypatch.setattr(selector, "_select_native", lambda: events.append("native"))

    owner = os.open(operation_lock, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def select() -> None:
        started.set()
        try:
            selector._locked_selection("native-select")
        except BaseException as exc:  # pragma: no cover - asserted below.
            failure.append(exc)

    thread = threading.Thread(target=select, daemon=True)
    thread.start()
    assert started.wait(timeout=1)
    thread.join(timeout=0.1)
    assert thread.is_alive()
    assert events == []
    os.close(owner)
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert failure == []
    assert events == ["native"]

    journal.write_text("interrupted manager transaction\n", encoding="ascii")
    with pytest.raises(selector.ControlError, match="blocks manual selection"):
        selector._locked_selection("native-select")
    assert events == ["native"]


def test_manager_inherited_fence_allows_child_selector_without_deadlock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path
    operation_lock = root / "operation.lock"
    monkeypatch.setattr(selector, "MANAGER_OPERATION_LOCK", operation_lock)
    monkeypatch.setattr(selector, "MANAGER_JOURNAL", root / "transaction.json")
    monkeypatch.setattr(selector, "SELECTION_LOCK", root / "selection.lock")
    monkeypatch.setattr(selector.os, "fchown", lambda *_args: None)
    events: list[str] = []
    monkeypatch.setattr(selector, "_select_docker", lambda: events.append("docker"))

    owner = os.open(operation_lock, os.O_RDWR | os.O_CREAT, 0o600)
    inherited = os.dup(owner)
    fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
    monkeypatch.setenv(selector.MANAGER_LOCK_FD_ENV, str(inherited))
    selector._locked_selection("docker-select")
    os.close(owner)

    assert events == ["docker"]
