from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from deploy.docker import mooncen_container_release as runtime
from deploy.docker.native_baseline import inventory_sha256
from deploy.docker import production_runtime_integrity as integrity
from deploy.docker import bootstrap_production_runtime as bootstrap
from deploy.docker.build_release_bundle import BUILD_POLICY_PATHS
from deploy.docker.release_manifest import (
    VALIDATION_CHECKS,
    create_release_manifest,
    create_validation_receipt,
    write_json_evidence,
)


API_ID_A = "sha256:" + "a" * 64
FRONTEND_ID_A = "sha256:" + "b" * 64
API_ID_B = "sha256:" + "c" * 64
FRONTEND_ID_B = "sha256:" + "d" * 64
TREE_A = "1" * 40
TREE_B = "2" * 40
DIGEST_PATTERN_VALUE = "8" * 64


def _reference(tree: str, api_id: str, frontend_id: str, digit: str) -> dict[str, object]:
    return {
        "release_digest": digit * 64,
        "source_tree": tree,
        "image_ids": {"api": api_id, "frontend": frontend_id},
    }


def _loaded(
    tmp_path: Path,
    *,
    tree: str,
    api_id: str,
    frontend_id: str,
    digit: str,
) -> runtime.LoadedRelease:
    directory = tmp_path / f"loaded-{tree}"
    directory.mkdir()
    reference = _reference(tree, api_id, frontend_id, digit)
    return runtime.LoadedRelease(
        directory=directory,
        manifest={
            "migration_ledger_sha256": DIGEST_PATTERN_VALUE,
            "images": {
                "api": {"tag": f"mooncen/api:release-{tree}", "image_id": api_id},
                "frontend": {
                    "tag": f"mooncen/frontend:release-{tree}",
                    "image_id": frontend_id,
                },
            },
        },
        reference=reference,
    )


def _native_snapshot() -> dict[str, dict[str, bool]]:
    return {unit: {"active": True, "enabled": True} for unit in runtime.NATIVE_UNITS}


def _native_fallback() -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": 1,
        "identity": "0" * 64,
        "deploy_commit": "7" * 40,
        "deploy_archive_sha256": "6" * 64,
        "deploy_info_sha256": "5" * 64,
        "prebuild_sha256": "4" * 64,
        "runtime_tree_sha256": "3" * 64,
        "control_sha256": {
            **{unit: "2" * 64 for unit in runtime.NATIVE_UNITS},
            "mooncen-native-runtime-condition": "1" * 64,
        },
        "units": _native_snapshot(),
    }
    value["identity"] = runtime._native_fallback_identity(value)
    return value


def _controller(tmp_path: Path) -> runtime.ContainerReleaseController:
    return runtime.ContainerReleaseController(
        paths=runtime.RuntimePaths(
            release_root=tmp_path / "releases",
            state_root=tmp_path / "state",
            runtime_root=tmp_path / "run",
            native_root=tmp_path / "native",
        ),
        trusted_uid=os.getuid(),
    )


def test_native_inventory_binds_dependency_bytecode_and_directory_metadata(
    tmp_path: Path,
) -> None:
    root = tmp_path / "native"
    (root / ".venv/lib/python/site-packages/pkg/__pycache__").mkdir(parents=True)
    (root / "frontend2/node_modules/tool").mkdir(parents=True)
    (root / "nested/logs").mkdir(parents=True)
    (root / "logs").mkdir()
    bytecode = root / ".venv/lib/python/site-packages/pkg/__pycache__/module.cpython-312.pyc"
    bytecode.write_bytes(b"valid-header-and-bytecode-a")
    dependency = root / "frontend2/node_modules/tool/index.js"
    dependency.write_text("module.exports = 'a';\n", encoding="utf-8")
    nested_log = root / "nested/logs/reviewed.txt"
    nested_log.write_text("included\n", encoding="utf-8")
    runtime_log = root / "logs/runtime.log"
    runtime_log.write_text("mutable-a\n", encoding="utf-8")

    original = inventory_sha256(root)
    bytecode.write_bytes(b"valid-header-and-bytecode-b")
    assert inventory_sha256(root) != original
    bytecode.write_bytes(b"valid-header-and-bytecode-a")
    assert inventory_sha256(root) == original

    dependency_directory_mode = dependency.parent.stat().st_mode & 0o777
    dependency.parent.chmod(0o750)
    assert inventory_sha256(root) != original
    dependency.parent.chmod(dependency_directory_mode)
    assert inventory_sha256(root) == original

    nested_log.write_text("changed\n", encoding="utf-8")
    assert inventory_sha256(root) != original
    nested_log.write_text("included\n", encoding="utf-8")
    runtime_log.write_text("mutable-b\n", encoding="utf-8")
    assert inventory_sha256(root) == original


def _mock_common(
    controller: runtime.ContainerReleaseController,
    monkeypatch: pytest.MonkeyPatch,
    releases: dict[str, runtime.LoadedRelease],
    events: list[str],
) -> None:
    monkeypatch.setattr(controller, "_require_root", lambda: None)
    monkeypatch.setattr(controller, "_require_local_docker", lambda: None)
    monkeypatch.setattr(controller, "_validate_host_inputs", lambda: None)
    monkeypatch.setattr(controller, "_capture_native_snapshot", _native_snapshot)
    monkeypatch.setattr(controller, "_capture_native_fallback", _native_fallback)
    monkeypatch.setattr(controller, "_verify_native_fallback", lambda value: value)
    monkeypatch.setattr(
        controller,
        "_load_release",
        lambda source_tree, **_kwargs: releases[source_tree],
    )
    monkeypatch.setattr(
        controller,
        "_start_guard",
        lambda token: events.append(f"guard-start:{token}"),
    )
    monkeypatch.setattr(
        controller,
        "_finish_guard",
        lambda token, **_kwargs: events.append(f"guard-finish:{token}"),
    )
    monkeypatch.setattr(
        controller,
        "_verify_migration_ledger",
        lambda release: events.append(f"ledger:{release.reference['source_tree']}"),
    )
    monkeypatch.setattr(
        controller,
        "_start_candidate",
        lambda release: events.append(f"candidate:{release.reference['source_tree']}"),
    )
    monkeypatch.setattr(
        controller,
        "_cleanup_candidate",
        lambda release: events.append(f"candidate-stop:{release.reference['source_tree']}"),
    )
    monkeypatch.setattr(controller, "_stop_native", lambda: events.append("native-stop"))
    monkeypatch.setattr(
        controller,
        "_arm_stack_supervisor",
        lambda: events.append("stack-arm"),
    )
    monkeypatch.setattr(controller, "_enable_stack", lambda: events.append("stack-enable"))
    monkeypatch.setattr(controller, "_disable_stack", lambda: events.append("stack-disable"))
    monkeypatch.setattr(
        controller,
        "_restore_native",
        lambda _snapshot: events.append("native-restore"),
    )
    monkeypatch.setattr(
        controller,
        "_stop_active",
        lambda release: events.append(f"active-stop:{release.reference['source_tree']}"),
    )
    monkeypatch.setattr(
        controller,
        "_start_active",
        lambda release: events.append(f"active-start:{release.reference['source_tree']}"),
    )
    monkeypatch.setattr(
        controller,
        "_verify_host_origin",
        lambda: events.append("host-origin-verified"),
    )


def test_first_promotion_cuts_over_from_native_and_commits_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller(tmp_path)
    release = _loaded(
        tmp_path,
        tree=TREE_A,
        api_id=API_ID_A,
        frontend_id=FRONTEND_ID_A,
        digit="3",
    )
    events: list[str] = []
    _mock_common(controller, monkeypatch, {TREE_A: release}, events)

    state = controller.promote(TREE_A)

    assert state["active"] == release.reference
    assert state["previous"] is None
    assert controller._read_state() == state
    assert controller._read_transaction() is None
    assert events[3:9] == [
        f"candidate:{TREE_A}",
        "native-stop",
        f"active-start:{TREE_A}",
        "host-origin-verified",
        "stack-enable",
        f"candidate-stop:{TREE_A}",
    ]
    assert events[0] == f"ledger:{TREE_A}"
    assert events[1] == "stack-arm"
    assert events[2].startswith("guard-start:")
    assert events[-1].startswith("guard-finish:")


def test_pending_migration_gate_runs_before_transaction_and_guard_arm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller(tmp_path)
    release = _loaded(
        tmp_path,
        tree=TREE_A,
        api_id=API_ID_A,
        frontend_id=FRONTEND_ID_A,
        digit="3",
    )
    events: list[str] = []
    _mock_common(controller, monkeypatch, {TREE_A: release}, events)
    monkeypatch.setattr(
        controller,
        "_verify_migration_ledger",
        lambda _release: (_ for _ in ()).throw(runtime.ContainerReleaseError("pending migration")),
    )

    with pytest.raises(runtime.ContainerReleaseError, match="pending migration"):
        controller.promote(TREE_A)

    assert events == []
    assert controller._read_transaction() is None
    assert controller._read_state() is None


def test_guard_is_running_before_transaction_journal_is_published(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller(tmp_path)
    release = _loaded(
        tmp_path,
        tree=TREE_A,
        api_id=API_ID_A,
        frontend_id=FRONTEND_ID_A,
        digit="3",
    )
    events: list[str] = []
    _mock_common(controller, monkeypatch, {TREE_A: release}, events)
    create_transaction = controller._create_transaction

    def record_transaction(**kwargs):
        events.append("transaction-published")
        return create_transaction(**kwargs)

    monkeypatch.setattr(controller, "_create_transaction", record_transaction)

    controller.promote(TREE_A)

    guard_index = next(index for index, event in enumerate(events) if event.startswith("guard-start:"))
    assert guard_index < events.index("transaction-published") < events.index(f"candidate:{TREE_A}")


def test_prearmed_guard_without_a_journal_disables_its_stale_instance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller(tmp_path)
    events: list[tuple[str, bool]] = []
    moments = iter((100.0, 131.0))
    monkeypatch.setattr(controller, "_require_root", lambda: None)
    monkeypatch.setattr(runtime.time, "monotonic", lambda: next(moments))
    monkeypatch.setattr(
        controller,
        "_finish_guard",
        lambda token, *, stop=True: events.append((token, stop)),
    )
    monkeypatch.setattr(
        controller,
        "sleeper",
        lambda _seconds: pytest.fail("expired pre-arm must not sleep"),
    )

    controller.guard("f" * 32)

    assert events == [("f" * 32, False)]


def test_failed_first_cutover_restores_native_and_leaves_no_active_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller(tmp_path)
    release = _loaded(
        tmp_path,
        tree=TREE_A,
        api_id=API_ID_A,
        frontend_id=FRONTEND_ID_A,
        digit="3",
    )
    events: list[str] = []
    _mock_common(controller, monkeypatch, {TREE_A: release}, events)

    def fail_active(candidate: runtime.LoadedRelease) -> None:
        events.append(f"active-start:{candidate.reference['source_tree']}")
        raise runtime.ContainerReleaseError("injected active health failure")

    monkeypatch.setattr(controller, "_start_active", fail_active)

    with pytest.raises(runtime.ContainerReleaseError, match="was rolled back"):
        controller.promote(TREE_A)

    assert controller._read_state() is None
    assert controller._read_transaction() is None
    assert f"active-stop:{TREE_A}" in events
    assert "native-restore" in events


def test_guard_unit_cleanup_failure_cannot_reverse_a_durable_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller(tmp_path)
    release = _loaded(
        tmp_path,
        tree=TREE_A,
        api_id=API_ID_A,
        frontend_id=FRONTEND_ID_A,
        digit="3",
    )
    events: list[str] = []
    _mock_common(controller, monkeypatch, {TREE_A: release}, events)
    monkeypatch.setattr(
        controller,
        "_finish_guard",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            runtime.ContainerReleaseError("injected systemd cleanup failure")
        ),
    )

    state = controller.promote(TREE_A)

    assert controller._read_state() == state
    assert controller._read_transaction() is None


def test_operator_native_maintenance_of_first_container_release_returns_to_native(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller(tmp_path)
    release = _loaded(
        tmp_path,
        tree=TREE_A,
        api_id=API_ID_A,
        frontend_id=FRONTEND_ID_A,
        digit="3",
    )
    events: list[str] = []
    _mock_common(controller, monkeypatch, {TREE_A: release}, events)
    controller.promote(TREE_A)

    result = controller.rollback_native()

    assert result is None
    assert controller._read_state() is None
    assert controller._read_transaction() is None
    assert f"active-stop:{TREE_A}" in events
    assert "native-restore" in events
    assert "stack-disable" in events


def test_native_maintenance_ignores_previous_docker_pointer_and_returns_to_native(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller(tmp_path)
    first = _loaded(
        tmp_path,
        tree=TREE_A,
        api_id=API_ID_A,
        frontend_id=FRONTEND_ID_A,
        digit="3",
    )
    second = _loaded(
        tmp_path,
        tree=TREE_B,
        api_id=API_ID_B,
        frontend_id=FRONTEND_ID_B,
        digit="4",
    )
    events: list[str] = []
    _mock_common(controller, monkeypatch, {TREE_A: first, TREE_B: second}, events)
    controller.promote(TREE_A)
    controller.promote(TREE_B)

    assert controller._read_state()["previous"] == first.reference
    assert controller.rollback_native() is None
    assert controller._read_state() is None
    assert events[-1].startswith("guard-finish:")


def test_native_health_failure_stops_native_before_restoring_previous_docker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller(tmp_path)
    first = _loaded(
        tmp_path,
        tree=TREE_A,
        api_id=API_ID_A,
        frontend_id=FRONTEND_ID_A,
        digit="3",
    )
    second = _loaded(
        tmp_path,
        tree=TREE_B,
        api_id=API_ID_B,
        frontend_id=FRONTEND_ID_B,
        digit="4",
    )
    events: list[str] = []
    _mock_common(controller, monkeypatch, {TREE_A: first, TREE_B: second}, events)
    controller.promote(TREE_A)
    previous_state = controller.promote(TREE_B)
    failures = iter((True, False))

    def verify_origin() -> None:
        events.append("host-origin-verified")
        if next(failures):
            raise runtime.ContainerReleaseError("injected native health failure")

    monkeypatch.setattr(controller, "_verify_host_origin", verify_origin)
    monkeypatch.setattr(
        controller,
        "_assert_native_units_disabled",
        lambda: events.append("native-disabled-asserted"),
    )

    with pytest.raises(runtime.ContainerReleaseError, match="active Docker release was restored"):
        controller.rollback_native()

    assert controller._read_state() == previous_state
    assert controller._read_transaction() is None
    recovery_native_stop = len(events) - 1 - events[::-1].index("native-stop")
    recovery_assert = len(events) - 1 - events[::-1].index("native-disabled-asserted")
    recovery_docker_start = len(events) - 1 - events[::-1].index(f"active-start:{TREE_B}")
    assert recovery_native_stop < recovery_assert < recovery_docker_start


def test_failed_second_release_restores_exact_previous_docker_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller(tmp_path)
    first = _loaded(
        tmp_path,
        tree=TREE_A,
        api_id=API_ID_A,
        frontend_id=FRONTEND_ID_A,
        digit="3",
    )
    second = _loaded(
        tmp_path,
        tree=TREE_B,
        api_id=API_ID_B,
        frontend_id=FRONTEND_ID_B,
        digit="4",
    )
    events: list[str] = []
    releases = {TREE_A: first, TREE_B: second}
    _mock_common(controller, monkeypatch, releases, events)
    first_state = controller.promote(TREE_A)

    def start_active(candidate: runtime.LoadedRelease) -> None:
        events.append(f"active-start:{candidate.reference['source_tree']}")
        if candidate.reference["source_tree"] == TREE_B:
            raise runtime.ContainerReleaseError("injected second release failure")

    monkeypatch.setattr(controller, "_start_active", start_active)

    with pytest.raises(runtime.ContainerReleaseError, match="was rolled back"):
        controller.promote(TREE_B)

    assert controller._read_state() == first_state
    assert f"active-stop:{TREE_A}" in events
    assert f"active-stop:{TREE_B}" in events
    assert events.count(f"active-start:{TREE_A}") >= 2
    assert "native-restore" not in events


def test_orphan_guard_recovers_cutover_after_controller_death(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller(tmp_path)
    release = _loaded(
        tmp_path,
        tree=TREE_A,
        api_id=API_ID_A,
        frontend_id=FRONTEND_ID_A,
        digit="3",
    )
    events: list[str] = []
    _mock_common(controller, monkeypatch, {TREE_A: release}, events)
    transaction = controller._create_transaction(
        operation="promote",
        target=release.reference,
        previous_state=None,
        native_snapshot=_native_fallback(),
    )
    controller._update_transaction(
        transaction["token"],
        phase="active_verifying",
        candidate_started=True,
        cutover_started=True,
    )
    monkeypatch.setattr(controller, "_owner_alive", lambda _transaction: False)

    controller.guard(transaction["token"], once=True)

    assert controller._read_transaction() is None
    assert controller._read_state() is None
    assert f"candidate-stop:{TREE_A}" in events
    assert f"active-stop:{TREE_A}" in events
    assert "native-restore" in events
    assert "host-origin-verified" in events


def test_expired_live_owner_is_fenced_then_guard_converges_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [100_000.0]
    controller = runtime.ContainerReleaseController(
        paths=runtime.RuntimePaths(
            release_root=tmp_path / "releases",
            state_root=tmp_path / "state",
            runtime_root=tmp_path / "run",
        ),
        trusted_uid=os.getuid(),
        clock=lambda: now[0],
    )
    release = _loaded(
        tmp_path,
        tree=TREE_A,
        api_id=API_ID_A,
        frontend_id=FRONTEND_ID_A,
        digit="3",
    )
    events: list[str] = []
    _mock_common(controller, monkeypatch, {TREE_A: release}, events)
    transaction = controller._create_transaction(
        operation="promote",
        target=release.reference,
        previous_state=None,
        native_snapshot=_native_fallback(),
    )
    now[0] += runtime.TRANSACTION_DEADLINE_SECONDS + 1
    monkeypatch.setattr(controller, "_owner_alive", lambda _transaction: True)
    monkeypatch.setattr(
        controller,
        "_fence_expired_owner",
        lambda _transaction: events.append("owner-fenced"),
    )

    controller.guard(transaction["token"], once=True)

    assert "owner-fenced" in events
    assert controller._read_transaction() is None
    assert controller._read_state() is None


def test_container_inspection_rejects_manifest_image_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller(tmp_path)
    release = _loaded(
        tmp_path,
        tree=TREE_A,
        api_id=API_ID_A,
        frontend_id=FRONTEND_ID_A,
        digit="3",
    )
    monkeypatch.setattr(controller, "_container_id", lambda *_args, **_kwargs: "f" * 64)
    monkeypatch.setattr(
        controller,
        "_execute",
        lambda *_args, **_kwargs: runtime.CommandResult(0, f"{'sha256:' + 'e' * 64} true 0"),
    )

    with pytest.raises(runtime.ContainerReleaseError, match="manifest image"):
        controller._verify_project_images(
            release,
            project=runtime.ACTIVE_PROJECT,
            api_port=runtime.ACTIVE_API_PORT,
            frontend_port=runtime.ACTIVE_FRONTEND_PORT,
            services=("api",),
        )


def test_command_runner_strips_remote_docker_and_compose_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller(tmp_path)
    captured: dict[str, str] = {}

    class Result:
        returncode = 0
        stdout = "default\n"

    def fake_run(*_args, **kwargs):
        captured.update(kwargs["env"])
        return Result()

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    monkeypatch.setenv("DOCKER_HOST", "ssh://production.invalid")
    monkeypatch.setenv("DOCKER_CONTEXT", "remote")
    monkeypatch.setenv("COMPOSE_FILE", "/tmp/attacker.yaml")
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "attacker")

    controller._execute(("docker", "context", "show"))

    assert "DOCKER_HOST" not in captured
    assert "DOCKER_CONTEXT" not in captured
    assert "COMPOSE_FILE" not in captured
    assert "COMPOSE_PROJECT_NAME" not in captured


def test_controller_rejects_remote_docker_context_and_unlisted_systemd_unit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller(tmp_path)
    monkeypatch.setattr(
        controller,
        "_execute",
        lambda *_args, **_kwargs: runtime.CommandResult(0, "production-over-ssh"),
    )
    with pytest.raises(runtime.ContainerReleaseError, match="default Docker context"):
        controller._require_local_docker()
    with pytest.raises(runtime.ContainerReleaseError, match="outside the allowlist"):
        controller._systemctl_result("stop", "docker.service")


def test_compose_api_rejects_destructive_action_even_for_allowlisted_project(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path)
    release = _loaded(
        tmp_path,
        tree=TREE_A,
        api_id=API_ID_A,
        frontend_id=FRONTEND_ID_A,
        digit="3",
    )
    with pytest.raises(runtime.ContainerReleaseError, match="forbidden"):
        controller._compose(
            release,
            project=runtime.ACTIVE_PROJECT,
            api_port=runtime.ACTIVE_API_PORT,
            frontend_port=runtime.ACTIVE_FRONTEND_PORT,
            arguments=("down", "--volumes"),
        )


def test_production_migration_gate_is_read_only_and_digest_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller(tmp_path)
    release = _loaded(
        tmp_path,
        tree=TREE_A,
        api_id=API_ID_A,
        frontend_id=FRONTEND_ID_A,
        digit="3",
    )
    captured: list[str] = []
    plan = {
        "schema_version": 1,
        "current": True,
        "pending": [],
        "expected_count": 42,
        "applied_count": 42,
        "expected_ledger_sha256": DIGEST_PATTERN_VALUE,
        "applied_ledger_sha256": DIGEST_PATTERN_VALUE,
    }

    def compose(*_args, **kwargs):
        captured.extend(kwargs["arguments"])
        return runtime.CommandResult(0, json.dumps(plan, sort_keys=True))

    monkeypatch.setattr(controller, "_compose", compose)
    controller._verify_migration_ledger(release)

    assert captured[-5:] == [
        "DB/setup_db.py",
        "--mode",
        "plan",
        "--json",
        "--require-current",
    ]
    assert "migrate" in captured  # the fixed service name, never a migration mode
    assert captured[captured.index("--mode") + 1] == "plan"

    plan["pending"] = ["20990101_001_pending"]
    plan["current"] = False
    with pytest.raises(runtime.ContainerReleaseError, match="ledger does not match"):
        controller._verify_migration_ledger(release)


def test_public_contract_checks_auth_and_all_protected_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller(tmp_path)
    requested_statuses: list[str] = []

    def http_json(url: str, **_kwargs):
        if url.endswith(f":{runtime.CANDIDATE_API_PORT}/"):
            return {"message": "Welcome to MoonCen API", "profile": "public"}
        if "/api/courses/" in url:
            return {"items": [], "total": 0}
        if url.endswith("/api/auth/oauth/config"):
            return {}
        raise AssertionError(url)

    def http_status(url: str) -> int:
        requested_statuses.append(url)
        return 401 if url.endswith("/api/auth/me") else 404

    monkeypatch.setattr(controller, "_http_json", http_json)
    monkeypatch.setattr(controller, "_http_status", http_status)
    controller._verify_application_contract(
        runtime.CANDIDATE_API_PORT,
        runtime.CANDIDATE_FRONTEND_PORT,
    )

    assert sum(url.endswith("/api/auth/me") for url in requested_statuses) == 2
    for path in runtime.PROTECTED_PATHS:
        assert sum(url.endswith(path) for url in requested_statuses) == 2


def test_pidfd_fencing_terminates_only_the_recorded_process_identity(
    tmp_path: Path,
) -> None:
    if not hasattr(os, "pidfd_open") or not hasattr(runtime.signal, "pidfd_send_signal"):
        pytest.skip("Linux pidfd fencing is unavailable")
    controller = _controller(tmp_path)
    process = runtime.subprocess.Popen(["/bin/sleep", "30"])
    try:
        transaction = {
            "owner_pid": process.pid,
            "owner_start_ticks": controller._process_start_ticks(process.pid),
            "owner_boot_id": controller._boot_id(),
        }
        controller._fence_expired_owner(transaction)
        assert process.wait(timeout=2) != 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)


def test_pidfd_fencing_never_signals_reused_or_mismatched_pid_identity(
    tmp_path: Path,
) -> None:
    if not hasattr(os, "pidfd_open") or not hasattr(runtime.signal, "pidfd_send_signal"):
        pytest.skip("Linux pidfd fencing is unavailable")
    controller = _controller(tmp_path)
    process = runtime.subprocess.Popen(["/bin/sleep", "30"])
    try:
        transaction = {
            "owner_pid": process.pid,
            "owner_start_ticks": controller._process_start_ticks(process.pid) + 1,
            "owner_boot_id": controller._boot_id(),
        }
        controller._fence_expired_owner(transaction)
        assert process.poll() is None
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=2)


def test_native_unit_drift_is_rejected_before_boot_convergence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller(tmp_path)
    drifted = _native_snapshot()
    monkeypatch.setattr(controller, "_capture_native_snapshot", lambda: drifted)
    with pytest.raises(runtime.ContainerReleaseError, match="native application unit drift"):
        controller._assert_native_units_disabled()


@pytest.mark.parametrize("stop_returncode", [1, 5])
def test_stop_native_accepts_idempotent_codes_only_after_exact_postcondition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stop_returncode: int,
) -> None:
    controller = _controller(tmp_path)
    actions: list[tuple[str, str]] = []

    def systemctl(action: str, unit: str, **_kwargs: object) -> runtime.CommandResult:
        actions.append((action, unit))
        return runtime.CommandResult(stop_returncode if action == "stop" else 1, "")

    def execute(command: tuple[str, ...], **_kwargs: object) -> runtime.CommandResult:
        assert command[:2] == ("systemctl", "show")
        assert command[2] in runtime.NATIVE_UNITS
        assert command[3:] == (
            "--property=ActiveState",
            "--property=UnitFileState",
            "--property=MainPID",
            "--no-pager",
        )
        return runtime.CommandResult(
            0,
            "ActiveState=inactive\nUnitFileState=disabled\nMainPID=0",
        )

    monkeypatch.setattr(controller, "_systemctl_result", systemctl)
    monkeypatch.setattr(controller, "_execute", execute)

    controller._stop_native()

    assert actions == [
        action
        for unit in runtime.NATIVE_UNITS
        for action in (("stop", unit), ("disable", unit))
    ]


@pytest.mark.parametrize(
    "postcondition",
    [
        "ActiveState=active\nUnitFileState=disabled\nMainPID=0",
        "ActiveState=inactive\nUnitFileState=enabled\nMainPID=0",
        "ActiveState=inactive\nUnitFileState=disabled\nMainPID=4821",
    ],
)
def test_stop_native_rejects_active_enabled_or_live_pid_after_accepted_codes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    postcondition: str,
) -> None:
    controller = _controller(tmp_path)
    monkeypatch.setattr(
        controller,
        "_systemctl_result",
        lambda *_args, **_kwargs: runtime.CommandResult(1, ""),
    )
    monkeypatch.setattr(
        controller,
        "_execute",
        lambda *_args, **_kwargs: runtime.CommandResult(0, postcondition),
    )

    with pytest.raises(
        runtime.ContainerReleaseError,
        match="did not reach its exact saved state",
    ):
        controller._stop_native()


@pytest.mark.parametrize(
    ("expected_active", "expected_enabled", "postcondition"),
    [
        (True, True, "ActiveState=active\nUnitFileState=enabled\nMainPID=4821"),
        (True, False, "ActiveState=active\nUnitFileState=disabled\nMainPID=4821"),
        (False, True, "ActiveState=inactive\nUnitFileState=enabled\nMainPID=0"),
        (False, False, "ActiveState=inactive\nUnitFileState=disabled\nMainPID=0"),
    ],
)
def test_restore_native_accepts_command_errors_only_after_exact_saved_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    expected_active: bool,
    expected_enabled: bool,
    postcondition: str,
) -> None:
    controller = _controller(tmp_path)
    trusted = {
        "units": {
            unit: {"active": expected_active, "enabled": expected_enabled}
            for unit in runtime.NATIVE_UNITS
        }
    }
    monkeypatch.setattr(controller, "_verify_native_fallback", lambda _value: trusted)
    monkeypatch.setattr(
        controller,
        "_systemctl_result",
        lambda action, *_args, **_kwargs: runtime.CommandResult(
            5 if action in {"start", "stop"} else 1,
            "",
        ),
    )
    monkeypatch.setattr(
        controller,
        "_execute",
        lambda *_args, **_kwargs: runtime.CommandResult(0, postcondition),
    )

    controller._restore_native({})


@pytest.mark.parametrize(
    ("expected_active", "expected_enabled", "postcondition"),
    [
        (True, True, "ActiveState=inactive\nUnitFileState=enabled\nMainPID=0"),
        (True, True, "ActiveState=active\nUnitFileState=enabled\nMainPID=0"),
        (True, True, "ActiveState=active\nUnitFileState=disabled\nMainPID=4821"),
        (False, False, "ActiveState=active\nUnitFileState=disabled\nMainPID=4821"),
        (False, False, "ActiveState=inactive\nUnitFileState=disabled\nMainPID=4821"),
        (False, True, "ActiveState=inactive\nUnitFileState=disabled\nMainPID=0"),
    ],
)
def test_restore_native_rejects_drift_from_saved_active_enabled_and_pid_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    expected_active: bool,
    expected_enabled: bool,
    postcondition: str,
) -> None:
    controller = _controller(tmp_path)
    trusted = {
        "units": {
            unit: {"active": expected_active, "enabled": expected_enabled}
            for unit in runtime.NATIVE_UNITS
        }
    }
    monkeypatch.setattr(controller, "_verify_native_fallback", lambda _value: trusted)
    monkeypatch.setattr(
        controller,
        "_systemctl_result",
        lambda *_args, **_kwargs: runtime.CommandResult(1, ""),
    )
    monkeypatch.setattr(
        controller,
        "_execute",
        lambda *_args, **_kwargs: runtime.CommandResult(0, postcondition),
    )

    with pytest.raises(
        runtime.ContainerReleaseError,
        match="did not reach its exact saved state",
    ):
        controller._restore_native({})


def test_ensure_active_fails_before_docker_when_native_unit_drift_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller(tmp_path)
    release = _loaded(
        tmp_path,
        tree=TREE_A,
        api_id=API_ID_A,
        frontend_id=FRONTEND_ID_A,
        digit="3",
    )
    monkeypatch.setattr(controller, "_require_root", lambda: None)
    controller._ensure_layout()
    controller._write_state(
        controller._new_active_state(
            target=release.reference,
            previous_state=None,
            native_snapshot=_native_fallback(),
            rollback=False,
        )
    )
    monkeypatch.setattr(controller, "_capture_native_snapshot", _native_snapshot)
    monkeypatch.setattr(
        controller,
        "_require_local_docker",
        lambda: pytest.fail("Docker must not run after native-unit drift"),
    )

    with pytest.raises(runtime.ContainerReleaseError, match="native application unit drift"):
        controller.ensure_active()


def test_receipt_target_identity_must_match_root_owned_host_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_root = tmp_path / "releases"
    release_root.mkdir(mode=0o700)
    release_dir = release_root / TREE_A
    release_dir.mkdir(mode=0o700)
    (release_dir / "images.tar").write_bytes(b"bundle")
    (release_dir / "images.tar").chmod(0o600)
    (release_dir / "compose.production.yaml").write_text("services: {}\n", encoding="utf-8")
    (release_dir / "compose.production.yaml").chmod(0o644)
    manifest = create_release_manifest(
        base_commit="5" * 40,
        source_tree=TREE_A,
        snapshot_commit="6" * 40,
        platform="linux/amd64",
        bundle_sha256="7" * 64,
        compose_sha256="8" * 64,
        build_policy_sha256="9" * 64,
        migration_ledger_sha256="a" * 64,
        images={
            "api": {"tag": f"mooncen/api:release-{TREE_A}", "image_id": API_ID_A},
            "frontend": {
                "tag": f"mooncen/frontend:release-{TREE_A}",
                "image_id": FRONTEND_ID_A,
            },
        },
        created_at="2026-08-19T12:00:00Z",
    )
    write_json_evidence(release_dir / "release.json", manifest)
    receipt = create_validation_receipt(
        release=manifest,
        target="an2p-dev",
        target_identity="b" * 64,
        checks={name: True for name in VALIDATION_CHECKS},
        validated_at="2026-08-19T12:01:00Z",
        expires_at="2099-08-19T13:01:00Z",
    )
    write_json_evidence(release_dir / "validation.json", receipt, receipt=True)
    identity = tmp_path / "an2p-dev-target-identity"
    identity.write_text("c" * 64 + "\n", encoding="ascii")
    identity.chmod(0o600)
    monkeypatch.setattr(runtime, "DEVELOPMENT_IDENTITY_FILE", identity)
    monkeypatch.setattr(
        runtime,
        "verify_release_directory",
        lambda *_args, **_kwargs: {"image_ids": {"api": API_ID_A, "frontend": FRONTEND_ID_A}},
    )
    controller = runtime.ContainerReleaseController(
        paths=runtime.RuntimePaths(release_root=release_root, state_root=tmp_path / "state"),
        trusted_uid=os.getuid(),
        clock=lambda: 1_787_142_000.0,
    )

    with pytest.raises(runtime.ContainerReleaseError, match="identity is not trusted"):
        controller._load_release(TREE_A, require_current_receipt=True)


def test_systemd_contract_is_durable_and_never_controls_host_infrastructure() -> None:
    root = Path(__file__).resolve().parents[1]
    stack = (root / "deploy/ubuntu/systemd/mooncen-container-stack.service").read_text(encoding="utf-8")
    guard = (root / "deploy/ubuntu/systemd/mooncen-container-release-guard@.service").read_text(encoding="utf-8")
    native_guard = (root / "deploy/ubuntu/mooncen_release_guard.sh").read_text(encoding="utf-8")
    assert "mooncen-container-release ensure-active" in stack
    assert "mooncen-container-release stop-active" in stack
    assert "mooncen-container-release guard %i" in guard
    assert "TimeoutStopSec=150s" in stack
    assert "RuntimeDirectory=mooncen-container-release" in stack
    assert "RuntimeDirectoryMode=0700" in stack
    assert "RuntimeDirectoryPreserve=yes" in stack
    assert "ReadWritePaths=-/run/mooncen-container-release" in stack
    assert "Restart=on-failure" in guard
    assert "RuntimeDirectory=mooncen-container-release" in guard
    assert "RuntimeDirectoryMode=0700" in guard
    assert "RuntimeDirectoryPreserve=yes" in guard
    assert "ReadWritePaths=-/run/mooncen-container-release" in guard
    assert "WantedBy=multi-user.target" in guard
    for forbidden in (
        "ExecStart=systemctl",
        "ExecStop=systemctl",
        "docker compose down",
        "docker system prune",
        "mooncen-an2p-deploy-sshd",
        "containerd.service",
    ):
        assert forbidden not in stack
        assert forbidden not in guard
    assert "$1 !~ /^mooncen-container-release-guard@/" in native_guard


def test_controller_has_only_fixed_projects_ports_and_native_units() -> None:
    assert runtime.ALLOWED_PROJECTS == {
        "mooncen-production",
        "mooncen-production-candidate",
    }
    assert (runtime.ACTIVE_API_PORT, runtime.ACTIVE_FRONTEND_PORT) == (8001, 5173)
    assert (runtime.CANDIDATE_API_PORT, runtime.CANDIDATE_FRONTEND_PORT) == (
        18001,
        15173,
    )
    assert runtime.NATIVE_UNITS == (
        "mooncen-api.service",
        "mooncen-frontend.service",
        "mooncen-ai-worker.service",
    )


def test_release_policy_digest_covers_every_production_controller_entrypoint() -> None:
    assert {
        "DB/provision_deployment_worker_login.sql",
        "DB/roles.sql",
        "DB/roles_body.sql",
        "deploy/docker/compose.production.yaml",
        "deploy/docker/bootstrap_production_runtime.py",
        "deploy/docker/install_production_runtime.sh",
        "deploy/docker/mooncen-container-release",
        "deploy/docker/mooncen_container_release.py",
        "deploy/docker/production_runtime_integrity.py",
        "deploy/ubuntu/install_sudoers.sh",
        "deploy/ubuntu/nginx/mooncen.conf",
        "deploy/ubuntu/mooncen_release_guard.sh",
        "deploy/ubuntu/setup_project.sh",
        "deploy/ubuntu/systemd/mooncen-container-release-guard@.service",
        "deploy/ubuntu/systemd/mooncen-container-stack.service",
        "ops_agent/deployment_registry.py",
        "ops_agent/production_topology.py",
        "tests/test_ai_check_db_roles.py",
        "tests/test_deployment_db_roles.py",
        "tests/test_staging_safety_contract.py",
        "tests/test_deployment_registry_profiles.py",
        "tests/test_production_topology_contract.py",
        "tests/test_remaining_security_contracts.py",
    }.issubset(BUILD_POLICY_PATHS)
    assert len(BUILD_POLICY_PATHS) == len(set(BUILD_POLICY_PATHS))


def test_installed_controller_wrapper_uses_isolated_python() -> None:
    wrapper = (Path(__file__).resolve().parents[1] / "deploy/docker/mooncen-container-release").read_text(
        encoding="utf-8"
    )
    assert "exec /usr/bin/python3 -I \\\n" in wrapper


def _write_ingress_release(root: Path, *, expires_at: str = "2099-08-19T13:01:00Z") -> None:
    release_dir = root / TREE_A
    release_dir.mkdir(mode=0o700)
    bundle = b"reviewed image bundle"
    compose_bytes = b"services: {}\n"
    (release_dir / "images.tar").write_bytes(bundle)
    (release_dir / "compose.production.yaml").write_bytes(compose_bytes)
    manifest = create_release_manifest(
        base_commit="5" * 40,
        source_tree=TREE_A,
        snapshot_commit="6" * 40,
        platform="linux/amd64",
        bundle_sha256=hashlib.sha256(bundle).hexdigest(),
        compose_sha256=hashlib.sha256(compose_bytes).hexdigest(),
        build_policy_sha256="9" * 64,
        migration_ledger_sha256="a" * 64,
        images={
            "api": {"tag": f"mooncen/api:release-{TREE_A}", "image_id": API_ID_A},
            "frontend": {
                "tag": f"mooncen/frontend:release-{TREE_A}",
                "image_id": FRONTEND_ID_A,
            },
        },
        created_at="2026-08-19T12:00:00Z",
    )
    write_json_evidence(release_dir / "release.json", manifest)
    receipt = create_validation_receipt(
        release=manifest,
        target="an2p-dev",
        target_identity="b" * 64,
        checks={name: True for name in VALIDATION_CHECKS},
        validated_at="2026-08-19T12:01:00Z",
        expires_at=expires_at,
    )
    write_json_evidence(release_dir / "validation.json", receipt, receipt=True)
    for path in release_dir.iterdir():
        path.chmod(0o600)


def test_stage_uses_fixed_ingress_and_installs_new_only_canonical_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ingress = tmp_path / "ingress"
    ingress.mkdir(mode=0o700)
    _write_ingress_release(ingress)
    identity = tmp_path / "identity"
    identity.write_text("b" * 64 + "\n", encoding="ascii")
    identity.chmod(0o600)
    monkeypatch.setattr(runtime, "INGRESS_ROOT", ingress)
    monkeypatch.setattr(runtime, "DEVELOPMENT_IDENTITY_FILE", identity)
    monkeypatch.setattr(
        runtime,
        "load_bootstrap_config",
        lambda **_kwargs: {
            "deploy_uid": os.getuid(),
            "deploy_gid": os.getgid(),
        },
    )
    policy_checks: list[str] = []
    monkeypatch.setattr(
        runtime,
        "validate_installed_runtime",
        lambda digest, **_kwargs: policy_checks.append(digest),
    )
    controller = _controller(tmp_path)
    monkeypatch.setattr(controller, "_require_root", lambda: None)
    monkeypatch.setattr(controller, "_verify_native_fallback", lambda value: value)

    result = controller.stage(TREE_A)

    destination = controller.paths.release_root / TREE_A
    assert result["staged"] is True
    assert result["source_tree"] == TREE_A
    assert set(path.name for path in destination.iterdir()) == {
        "release.json",
        "validation.json",
        "images.tar",
        "compose.production.yaml",
    }
    assert stat_mode(destination) == 0o700
    assert stat_mode(destination / "images.tar") == 0o600
    assert stat_mode(destination / "release.json") == 0o644
    assert policy_checks == ["9" * 64]
    assert controller.stage(TREE_A) == result


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_expired_stage_receipt_never_creates_root_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ingress = tmp_path / "ingress"
    ingress.mkdir(mode=0o700)
    _write_ingress_release(ingress, expires_at="2026-08-19T12:02:00Z")
    identity = tmp_path / "identity"
    identity.write_text("b" * 64 + "\n", encoding="ascii")
    identity.chmod(0o600)
    monkeypatch.setattr(runtime, "INGRESS_ROOT", ingress)
    monkeypatch.setattr(runtime, "DEVELOPMENT_IDENTITY_FILE", identity)
    monkeypatch.setattr(
        runtime,
        "load_bootstrap_config",
        lambda **_kwargs: {"deploy_uid": os.getuid(), "deploy_gid": os.getgid()},
    )
    controller = runtime.ContainerReleaseController(
        paths=runtime.RuntimePaths(
            release_root=tmp_path / "releases",
            state_root=tmp_path / "state",
        ),
        trusted_uid=os.getuid(),
        clock=lambda: 1_787_142_000.0,
        enforce_installation_receipt=False,
    )
    monkeypatch.setattr(controller, "_require_root", lambda: None)

    with pytest.raises(runtime.ContainerReleaseError, match="evidence is invalid"):
        controller.stage(TREE_A)
    assert not (controller.paths.release_root / TREE_A).exists()
    assert not list(controller.paths.release_root.glob(".incoming-*"))


def test_preflight_is_no_load_and_has_no_transaction_or_guard_side_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller(tmp_path)
    release = _loaded(
        tmp_path,
        tree=TREE_A,
        api_id=API_ID_A,
        frontend_id=FRONTEND_ID_A,
        digit="3",
    )
    calls: list[object] = []
    monkeypatch.setattr(controller, "_require_root", lambda: None)
    monkeypatch.setattr(controller, "_require_local_docker", lambda: calls.append("docker"))
    monkeypatch.setattr(controller, "_validate_host_inputs", lambda: calls.append("host"))
    monkeypatch.setattr(
        controller,
        "_load_release",
        lambda tree, **kwargs: calls.append((tree, kwargs)) or release,
    )
    monkeypatch.setattr(controller, "_verify_migration_ledger", lambda _r: calls.append("ledger"))
    monkeypatch.setattr(controller, "_verify_host_origin", lambda: calls.append("origin"))
    monkeypatch.setattr(
        controller,
        "_arm_stack_supervisor",
        lambda: pytest.fail("preflight must not arm the supervisor"),
    )

    result = controller.preflight(TREE_A)

    assert result["preflight"] == "passed"
    assert calls == [
        "docker",
        "host",
        (
            TREE_A,
            {
                "require_current_receipt": True,
                "load_images": False,
                "require_installed_policy": True,
            },
        ),
        "ledger",
        "origin",
    ]
    assert controller._read_transaction() is None


@pytest.mark.skipif(os.name != "posix", reason="flock is a POSIX runtime contract")
def test_shared_controller_lock_fences_exclusive_installer_lock(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    controller._ensure_layout()
    probe = """
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDWR)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit(9)
raise SystemExit(0)
"""
    with controller._control_lock():
        blocked = subprocess.run(
            [sys.executable, "-c", probe, str(controller.paths.control_lock_file)],
            check=False,
        )
    acquired = subprocess.run(
        [sys.executable, "-c", probe, str(controller.paths.control_lock_file)],
        check=False,
    )
    assert blocked.returncode == 9
    assert acquired.returncode == 0
    assert stat_mode(controller.paths.control_lock_file) == 0o600


def test_installation_receipt_detects_root_runtime_byte_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed = tmp_path / "controller"
    installed.write_text("reviewed\n", encoding="ascii")
    installed.chmod(0o600)
    receipt = tmp_path / "receipt.json"
    monkeypatch.setattr(
        integrity,
        "INSTALLED_RUNTIME_FILES",
        (("controller", installed, 0o600),),
    )
    value = {
        "schema_version": 1,
        "build_policy_sha256": "d" * 64,
        "installed_files": {"controller": hashlib.sha256(b"reviewed\n").hexdigest()},
        "installed_at": "2026-08-19T12:00:00Z",
    }
    integrity._atomic_write(receipt, value, mode=0o600)
    integrity.validate_installed_runtime(
        "d" * 64,
        receipt_path=receipt,
        trusted_uid=os.getuid(),
    )

    installed.write_text("drifted\n", encoding="ascii")
    with pytest.raises(integrity.RuntimeIntegrityError, match="bytes have drifted"):
        integrity.validate_installed_runtime(
            "d" * 64,
            receipt_path=receipt,
            trusted_uid=os.getuid(),
        )


def test_installation_receipt_covers_native_container_exclusion_bytes() -> None:
    installed = {
        (label, str(path), mode)
        for label, path, mode in integrity.INSTALLED_RUNTIME_FILES
    }
    assert {
        (
            "native_runtime_condition",
            "/usr/local/libexec/mooncen-native-runtime-condition",
            0o755,
        ),
        (
            "container_pg_hba_helper",
            "/usr/local/libexec/mooncen-configure-container-pg-hba",
            0o755,
        ),
        (
            "an2p_control_secret_exporter",
            "/usr/local/libexec/mooncen-export-an2p-control-secrets",
            0o755,
        ),
        (
            "native_api_unit",
            "/etc/systemd/system/mooncen-api.service",
            0o644,
        ),
        (
            "native_frontend_unit",
            "/etc/systemd/system/mooncen-frontend.service",
            0o644,
        ),
        (
            "native_ai_worker_unit",
            "/etc/systemd/system/mooncen-ai-worker.service",
            0o644,
        ),
        (
            "native_deploy_guard_unit",
            "/etc/systemd/system/mooncen-deploy-guard@.service",
            0o644,
        ),
    }.issubset(installed)


def test_bootstrap_accepts_only_one_exact_identity_line() -> None:
    identity = b"e" * 64
    assert bootstrap._target_identity(io.BytesIO(identity + b"\n")) == identity.decode()
    for invalid in (identity, identity + b"\nextra", b"E" * 64 + b"\n"):
        with pytest.raises(bootstrap.BootstrapError, match="one lowercase"):
            bootstrap._target_identity(io.BytesIO(invalid))


def test_target_identity_command_reads_only_the_root_owned_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller(tmp_path)
    monkeypatch.setattr(controller, "_require_root", lambda: None)
    monkeypatch.setattr(
        controller,
        "_development_target_identity",
        lambda: "e" * 64,
    )

    assert controller.target_identity() == {
        "schema_version": 1,
        "target": "an2p-dev",
        "target_identity": "e" * 64,
    }


def test_target_identity_cli_emits_compact_canonical_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeController:
        def _require_root(self) -> None:
            return None

        def _ensure_layout(self) -> None:
            return None

        def _control_lock(self):
            return contextlib.nullcontext()

        def target_identity(self) -> dict[str, object]:
            return {
                "target_identity": "e" * 64,
                "target": "an2p-dev",
                "schema_version": 1,
            }

    monkeypatch.setattr(runtime, "ContainerReleaseController", FakeController)

    assert runtime.main(["target-identity"]) == 0
    assert capsys.readouterr().out == (
        '{"schema_version":1,"target":"an2p-dev","target_identity":"' + "e" * 64 + '"}\n'
    )


def test_native_setup_stages_only_container_inputs_and_manual_bootstrap() -> None:
    root = Path(__file__).resolve().parents[1]
    setup = (root / "deploy/ubuntu/setup_project.sh").read_text(encoding="utf-8")
    sudoers = (root / "deploy/ubuntu/install_sudoers.sh").read_text(encoding="utf-8")
    guard = (root / "deploy/ubuntu/mooncen_release_guard.sh").read_text(encoding="utf-8")
    installer = (root / "deploy/docker/install_production_runtime.sh").read_text(encoding="utf-8")
    assert 'install_service_env api.env "$API_OS_USER"' in setup
    assert "install_container_env container-api.env <<ENV" in setup
    assert 'install_service_env ai.env "$AI_OS_USER"' in setup
    assert "install_container_env container-ai.env <<ENV" in setup
    assert "install_container_env_copy" not in setup
    assert "container-migrator.env" in setup
    assert "container-frontend-runtime-config.js" in setup
    assert "install_production_runtime.sh" not in setup
    for path in (
        "/etc/mooncen/container-api.env",
        "/etc/mooncen/container-ai.env",
        "/etc/mooncen/container-migrator.env",
        "/etc/mooncen/container-frontend-runtime-config.js",
        "/etc/mooncen/container-bootstrap.json",
        "/etc/mooncen/an2p-dev-target-identity",
        "/etc/mooncen/container-runtime-installation.json",
    ):
        assert path in guard
    assert "${CONTAINER_BOOTSTRAP}" in sudoers
    assert "${CONTAINER_CONTROLLER} status" in sudoers
    assert "${CONTAINER_CONTROLLER} target-identity" in sudoers
    assert "${CONTAINER_CONTROLLER} native-begin ${CONTAINER_TOKEN_ARG}" in sudoers
    assert "${CONTAINER_CONTROLLER} native-end ${CONTAINER_TOKEN_ARG}" in sudoers
    assert "${CONTAINER_CONTROLLER} *" not in sudoers
    for action in (
        "lease-bind",
        "lease-release",
        "stage",
        "load-images",
        "preflight",
        "promote",
        "rollback",
        "rollback-native",
    ):
        assert f"${{CONTAINER_CONTROLLER}} {action}" not in sudoers
    assert "--expected-build-policy-sha256" in installer
    assert "/usr/bin/flock -x 9" in installer
    assert "write-installation-receipt" in installer
    assert "docs/docker-production.md" in installer


def test_public_container_env_is_an_exact_secret_allowlist() -> None:
    setup = (Path(__file__).resolve().parents[1] / "deploy/ubuntu/setup_project.sh").read_text(encoding="utf-8")
    match = re.search(
        r"install_container_env container-api\.env <<ENV\n(?P<body>.*?)\nENV\n",
        setup,
        flags=re.DOTALL,
    )
    assert match is not None
    body = match.group("body")
    keys = {line.split("=", 1)[0] for line in body.splitlines()}
    assert keys == {
        "ENVIRONMENT",
        "DB_NAME",
        "DB_API_USER",
        "DB_API_PASSWORD",
        "DB_POOL_MIN",
        "DB_POOL_MAX",
        "MOONCEN_CORS_ORIGINS",
        "MOONCEN_TRUSTED_HOSTS",
        "AUTH_SECRET",
        "NAVER_OAUTH_CLIENT_ID",
        "NAVER_OAUTH_CLIENT_SECRET",
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "OAUTH_REDIRECT_URIS",
        "MOONCEN_ADMIN_EMAILS",
        "MOONCEN_ADMIN_PROVIDER_IDS",
        "MOONCEN_BUG_REPORT_TO",
        "MOONCEN_BUG_REPORT_FROM",
        "MOONCEN_SMTP_HOST",
        "MOONCEN_SMTP_PORT",
        "MOONCEN_SMTP_USERNAME",
        "MOONCEN_SMTP_PASSWORD",
        "MOONCEN_SMTP_SECURITY",
        "SITE_URL",
    }
    forbidden = {
        "DB_HOST",
        "DB_PORT",
        "DB_SSLMODE",
        "DB_OWNER_USER",
        "DB_SSLROOTCERT",
        "DB_SSLCERT",
        "DB_SSLKEY",
        "MOONCEN_OPS_SINGLE_ACCOUNT_ONLY",
        "MOONCEN_OPS_LOGIN_ID",
        "MOONCEN_OPS_PASSWORD_HASH",
        "OPS_CLOUDFLARE_ANALYTICS_ZONE_ID",
        "OPS_CLOUDFLARE_ANALYTICS_TOKEN",
        "MOONCEN_SERVER_MONITOR_TOKEN",
    }
    assert keys.isdisjoint(forbidden)

    sentinel_values = {name: f"FORBIDDEN_{name}_SENTINEL" for name in forbidden}
    referenced = set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)", body))
    environment = {
        "PATH": "/usr/bin:/bin",
        **{name: "allowed-value" for name in referenced},
        **sentinel_values,
    }
    rendered = subprocess.run(
        ["/bin/bash", "-eu", "-c", f"cat <<ENV\n{body}\nENV\n"],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert all(sentinel not in rendered for sentinel in sentinel_values.values())


def test_ai_container_env_omits_native_host_and_tls_paths() -> None:
    setup = (Path(__file__).resolve().parents[1] / "deploy/ubuntu/setup_project.sh").read_text(encoding="utf-8")
    match = re.search(
        r"install_container_env container-ai\.env <<ENV\n(?P<body>.*?)\nENV\n",
        setup,
        flags=re.DOTALL,
    )
    assert match is not None
    keys = {line.split("=", 1)[0] for line in match.group("body").splitlines()}
    assert keys == {
        "ENVIRONMENT",
        "DB_NAME",
        "DB_RUNTIME_USER",
        "DB_RUNTIME_PASSWORD",
        "DB_APPLICATION_NAME",
        "DB_POOL_MIN",
        "DB_POOL_MAX",
        "OLLAMA_HOST",
        "OLLAMA_HOSTS",
        "OLLAMA_MODEL",
        "AI_PROVIDER",
        "AI_WORKERS",
        "AI_BATCH_SIZE",
        "AI_DELAY",
        "AI_POLL_INTERVAL",
        "AI_ACTIVE_START",
        "AI_ACTIVE_END",
        "AI_WEEKEND_24H",
    }
    assert keys.isdisjoint(
        {
            "DB_HOST",
            "DB_PORT",
            "DB_SSLMODE",
            "DB_SSLROOTCERT",
            "DB_SSLCERT",
            "DB_SSLKEY",
        }
    )


def test_container_transaction_cas_and_native_intent_are_mutually_exclusive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller(tmp_path)
    monkeypatch.setattr(controller, "_require_root", lambda: None)
    monkeypatch.setattr(controller, "_verify_native_fallback", lambda value: value)
    token = "a" * 32
    wrong_token = "b" * 32

    assert controller.native_begin(token) == {"schema_version": 1, "token": token}
    assert controller.status()["native_intent"] == {
        "schema_version": 1,
        "token": token,
    }
    with pytest.raises(runtime.ContainerReleaseError, match="native deployment intent"):
        controller._create_transaction(
            operation="promote",
            target=None,
            previous_state=None,
            native_snapshot=_native_fallback(),
        )
    with pytest.raises(runtime.ContainerReleaseError, match="token changed"):
        controller.native_end(wrong_token)
    assert controller.native_end(token) == {
        "ended": True,
        "schema_version": 1,
        "token": token,
    }

    with pytest.raises(runtime.ContainerReleaseError, match="compare-and-swap"):
        controller._create_transaction(
            operation="promote",
            target=None,
            previous_state=None,
            native_snapshot=_native_fallback(),
            expected_generation=0,
            expected_active_release_digest="0" * 64,
            expected_previous_release_digest="0" * 64,
            expected_state_sha256="f" * 64,
        )
    assert controller._read_transaction() is None


def test_status_cli_bypasses_long_running_control_lock(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeController:
        def _require_root(self) -> None:
            return None

        def _ensure_layout(self) -> None:
            return None

        def _control_lock(self):
            raise AssertionError("status must not acquire the outer control lock")

        def status(self) -> dict[str, object]:
            return {
                "schema_version": 1,
                "native_intent": None,
                "state": None,
                "transaction": None,
                "worker_lease": None,
            }

    monkeypatch.setattr(runtime, "ContainerReleaseController", FakeController)
    assert runtime.main(["status"]) == 0
    assert capsys.readouterr().out == (
        '{"native_intent":null,"schema_version":1,"state":null,'
        '"transaction":null,"worker_lease":null}\n'
    )


def test_worker_claim_epoch_fences_old_mutations_and_cannot_be_reused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [1_800_000_000.0]
    controller = runtime.ContainerReleaseController(
        paths=runtime.RuntimePaths(
            release_root=tmp_path / "releases",
            state_root=tmp_path / "state",
            runtime_root=tmp_path / "run",
            native_root=tmp_path / "native",
        ),
        trusted_uid=os.getuid(),
        clock=lambda: now[0],
    )
    monkeypatch.setattr(controller, "_require_root", lambda: None)
    first_job = "1" * 32
    first_token = "2" * 32
    second_job = "3" * 32
    second_token = "4" * 32

    with controller._control_lock(exclusive=True):
        first = controller.bind_worker_lease(first_job, 10, first_token)
    assert first["active"] is True
    assert first["claim_token_sha256"] == hashlib.sha256(first_token.encode("ascii")).hexdigest()
    controller._require_worker_lease(first_job, 10, first_token)

    with controller._control_lock(exclusive=True):
        second = controller.bind_worker_lease(second_job, 11, second_token)
    assert second["claim_epoch"] == 11
    with pytest.raises(runtime.ContainerReleaseError, match="fenced"):
        controller._require_worker_lease(first_job, 10, first_token)

    with controller._control_lock(exclusive=True):
        released = controller.release_worker_lease(second_job, 11, second_token)
    assert released["active"] is False
    with pytest.raises(runtime.ContainerReleaseError, match="cannot be reused"):
        with controller._control_lock(exclusive=True):
            controller.bind_worker_lease(second_job, 11, second_token)
    with pytest.raises(runtime.ContainerReleaseError, match="fenced"):
        with controller._control_lock(exclusive=True):
            controller.bind_worker_lease(first_job, 10, first_token)


def test_exclusive_worker_fence_waits_for_inflight_shared_controller_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller(tmp_path)
    monkeypatch.setattr(controller, "_require_root", lambda: None)
    shared_entered = threading.Event()
    release_shared = threading.Event()
    fence_finished = threading.Event()
    failures: list[BaseException] = []

    def old_command() -> None:
        try:
            with controller._control_lock():
                shared_entered.set()
                assert release_shared.wait(timeout=2)
        except BaseException as exc:  # pragma: no cover - thread assertion relay
            failures.append(exc)

    def new_fence() -> None:
        try:
            with controller._control_lock(exclusive=True):
                controller.bind_worker_lease("5" * 32, 20, "6" * 32)
            fence_finished.set()
        except BaseException as exc:  # pragma: no cover - thread assertion relay
            failures.append(exc)

    old_thread = threading.Thread(target=old_command)
    fence_thread = threading.Thread(target=new_fence)
    old_thread.start()
    assert shared_entered.wait(timeout=1)
    fence_thread.start()
    time.sleep(0.05)
    assert not fence_finished.is_set()
    release_shared.set()
    old_thread.join(timeout=2)
    fence_thread.join(timeout=2)
    assert failures == []
    assert fence_finished.is_set()
    assert controller.status()["worker_lease"]["claim_epoch"] == 20
