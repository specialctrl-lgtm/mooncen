from __future__ import annotations

import contextlib
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from deploy.an2p import host_layer_transition as transition


PREVIOUS = f"runtime-pair.{'1' * 40}.{'2' * 40}.{'3' * 64}"
TARGET = f"runtime-pair.{'4' * 40}.{'5' * 40}.{'6' * 64}"
PREVIOUS_HOST = "7" * 64
TARGET_HOST = "8" * 64


def _entry(
    *,
    label: str = "runtime_manager",
    relative: str = "deploy/an2p/runtime_pair_manager.py",
    installed: Path = transition.INSTALLED_MANAGER,
    mode: int = 0o755,
) -> transition.ManifestEntry:
    return transition.ManifestEntry(label, relative, installed, mode)


def _context(
    root: Path,
    name: str,
    *,
    host_sha: str,
    receipt_sha: str,
    manifest: tuple[transition.ManifestEntry, ...],
) -> transition.PairContext:
    pair = root / name
    control = pair / "control"
    docker = pair / "docker"
    return transition.PairContext(
        name=name,
        pair=pair,
        control=control,
        docker=docker,
        receipt={"source_tree": transition.PAIR_PATTERN.fullmatch(name).group(2)},  # type: ignore[union-attr]
        receipt_sha256=receipt_sha,
        manifest=manifest,
        manifest_sha256=hashlib.sha256(transition._manifest_payload(manifest)).hexdigest(),
        host_layer_sha256=host_sha,
    )


def _journal(*, phase: str = "prepared", publish: Path | None = None) -> dict[str, object]:
    publish_path = publish or transition.STATE_ROOT / "install-transaction.json"
    return {
        "manifest_sha256": "9" * 64,
        "phase": phase,
        "previous_host_layer_sha256": PREVIOUS_HOST,
        "previous_pair": PREVIOUS,
        "previous_receipt_sha256": "a" * 64,
        "publish_journal": str(publish_path),
        "publish_journal_sha256": "b" * 64,
        "schema_version": 1,
        "target_host_layer_sha256": TARGET_HOST,
        "target_pair": TARGET,
        "target_receipt_sha256": "c" * 64,
    }


@pytest.fixture
def root_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transition, "ROOT_UID", os.getuid())
    monkeypatch.setattr(transition, "ROOT_GID", os.getgid())


def test_cli_exposes_only_the_reviewed_prepare_recover_and_json_status_contract() -> None:
    parser = transition._parser()
    parsed = parser.parse_args(
        [
            "prepare",
            "--previous-pair",
            PREVIOUS,
            "--target-pair",
            TARGET,
            "--previous-host-layer",
            PREVIOUS_HOST,
            "--target-host-layer",
            TARGET_HOST,
            "--publish-journal",
            "/var/lib/mooncen-an2p-runtime/install-transaction.json",
        ]
    )

    assert parsed.command == "prepare"
    assert parsed.previous_pair == PREVIOUS
    assert parsed.target_pair == TARGET
    assert parsed.publish_journal == Path("/var/lib/mooncen-an2p-runtime/install-transaction.json")
    assert parser.parse_args(["recover"]).command == "recover"
    assert parser.parse_args(["recover", "--boot-fence"]).boot_fence is True
    assert parser.parse_args(["status", "--json"]).json is True
    with pytest.raises(SystemExit):
        parser.parse_args(["status"])


def test_transition_rejects_even_one_manifest_field_change(tmp_path: Path) -> None:
    previous_manifest = (_entry(),)
    target_manifest = (_entry(relative="deploy/an2p/replacement_runtime_pair_manager.py"),)
    previous = _context(
        tmp_path,
        PREVIOUS,
        host_sha=PREVIOUS_HOST,
        receipt_sha="a" * 64,
        manifest=previous_manifest,
    )
    target = _context(
        tmp_path,
        TARGET,
        host_sha=TARGET_HOST,
        receipt_sha="b" * 64,
        manifest=target_manifest,
    )

    with pytest.raises(transition.TransitionError, match="manifest changed"):
        transition._require_matching_manifests(previous, target)


def test_loading_reviewed_manifest_never_mutates_the_pair_tree(
    tmp_path: Path,
    root_identity: None,
) -> None:
    del root_identity
    control = tmp_path / "control"
    source = control / "deploy/an2p/runtime_pair_manager.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "from pathlib import Path\n"
        "HOST_LAYER_FILES = ((\n"
        "    'runtime_manager',\n"
        "    'deploy/an2p/runtime_pair_manager.py',\n"
        "    Path('/usr/local/libexec/mooncen-an2p-runtime-manager'),\n"
        "    0o755,\n"
        "),)\n",
        encoding="ascii",
    )
    source.chmod(0o644)
    before = {path.relative_to(control) for path in control.rglob("*")}

    first = transition._load_manifest(control)
    second = transition._load_manifest(control)

    after = {path.relative_to(control) for path in control.rglob("*")}
    assert first == second == (_entry(),)
    assert after == before
    assert not tuple(control.rglob("*.pyc"))


def test_transition_journal_is_canonical_exact_and_root_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    root_identity: None,
) -> None:
    del root_identity
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    journal_path = state / "host-layer-transition.json"
    monkeypatch.setattr(transition, "STATE_ROOT", state)
    monkeypatch.setattr(transition, "TRANSITION_JOURNAL", journal_path)
    value = _journal(publish=state / "install-transaction.json")

    transition._write_transition(value, create=True)

    assert journal_path.stat().st_mode & 0o777 == 0o600
    assert journal_path.read_bytes() == transition._canonical_json(value)
    assert transition._load_transition() == value

    unsafe = {**value, "unexpected": True}
    journal_path.write_bytes(transition._canonical_json(unsafe))
    journal_path.chmod(0o600)
    with pytest.raises(transition.TransitionError, match="schema"):
        transition._load_transition()


def test_transition_journal_rejects_duplicate_keys_and_symlinks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    root_identity: None,
) -> None:
    del root_identity
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    journal = state / "host-layer-transition.json"
    monkeypatch.setattr(transition, "STATE_ROOT", state)
    monkeypatch.setattr(transition, "TRANSITION_JOURNAL", journal)
    journal.write_text('{"phase":"prepared","phase":"activated"}\n', encoding="ascii")
    journal.chmod(0o600)

    with pytest.raises(transition.TransitionError, match="duplicate"):
        transition._load_transition()

    journal.unlink()
    target = state / "target"
    target.write_text("payload\n", encoding="ascii")
    journal.symlink_to(target)
    with pytest.raises(transition.TransitionError, match="unsafe"):
        transition._load_transition()


def test_new_prepare_journals_before_enabling_the_boot_fence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = (_entry(),)
    previous = _context(
        tmp_path,
        PREVIOUS,
        host_sha=PREVIOUS_HOST,
        receipt_sha="a" * 64,
        manifest=manifest,
    )
    target = _context(
        tmp_path,
        TARGET,
        host_sha=TARGET_HOST,
        receipt_sha="c" * 64,
        manifest=manifest,
    )
    publish = tmp_path / "install-transaction.json"
    events: list[str] = []
    monkeypatch.setattr(transition, "_load_transition", lambda: None)
    monkeypatch.setattr(transition, "_load_committed_receipt", lambda _pair: None)
    monkeypatch.setattr(transition, "PAIR_TRANSACTION", tmp_path / "pair.json")
    monkeypatch.setattr(
        transition,
        "CONTROL_FINALIZATION_TRANSACTION",
        tmp_path / "control.json",
    )
    monkeypatch.setattr(transition, "OPS_ROTATION_TRANSACTION", tmp_path / "ops.json")
    monkeypatch.setattr(
        transition,
        "_load_pair",
        lambda name: previous if name == PREVIOUS else target,
    )
    monkeypatch.setattr(transition, "_current_pair", lambda: PREVIOUS)
    monkeypatch.setattr(transition, "_installed_host_state", lambda *_args: "old")
    monkeypatch.setattr(
        transition,
        "_load_publish_journal",
        lambda *_args: ({}, b"publication\n"),
    )
    monkeypatch.setattr(
        transition,
        "_install_recovery_unit_files",
        lambda: events.append("unit-files"),
    )
    monkeypatch.setattr(
        transition,
        "_write_transition",
        lambda _value, **_kwargs: events.append("journal"),
    )
    monkeypatch.setattr(
        transition,
        "_enable_recovery_unit",
        lambda: events.append("enable"),
    )
    monkeypatch.setattr(
        transition,
        "_converge",
        lambda _value: (
            events.append("converge")
            or {
                "active_pair": TARGET,
                "host_transition": "committed",
                "schema_version": 1,
            }
        ),
    )

    result = transition.prepare(
        previous_pair=PREVIOUS,
        target_pair=TARGET,
        previous_host_layer=PREVIOUS_HOST,
        target_host_layer=TARGET_HOST,
        publish_journal=publish,
    )

    assert events == ["unit-files", "journal", "enable"]
    assert result == {
        "host_transition": "armed",
        "schema_version": 1,
        "target_pair": TARGET,
    }


def test_enable_failure_leaves_the_durable_prepared_journal_for_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = (_entry(),)
    previous = _context(
        tmp_path,
        PREVIOUS,
        host_sha=PREVIOUS_HOST,
        receipt_sha="a" * 64,
        manifest=manifest,
    )
    target = _context(
        tmp_path,
        TARGET,
        host_sha=TARGET_HOST,
        receipt_sha="c" * 64,
        manifest=manifest,
    )
    events: list[str] = []
    monkeypatch.setattr(transition, "_load_transition", lambda: None)
    monkeypatch.setattr(transition, "_load_committed_receipt", lambda _pair: None)
    monkeypatch.setattr(transition, "PAIR_TRANSACTION", tmp_path / "pair.json")
    monkeypatch.setattr(
        transition,
        "CONTROL_FINALIZATION_TRANSACTION",
        tmp_path / "control.json",
    )
    monkeypatch.setattr(transition, "OPS_ROTATION_TRANSACTION", tmp_path / "ops.json")
    monkeypatch.setattr(
        transition,
        "_load_pair",
        lambda name: previous if name == PREVIOUS else target,
    )
    monkeypatch.setattr(transition, "_current_pair", lambda: PREVIOUS)
    monkeypatch.setattr(transition, "_installed_host_state", lambda *_args: "old")
    monkeypatch.setattr(
        transition,
        "_load_publish_journal",
        lambda *_args: ({}, b"publication\n"),
    )
    monkeypatch.setattr(
        transition,
        "_install_recovery_unit_files",
        lambda: events.append("unit-files"),
    )
    monkeypatch.setattr(
        transition,
        "_write_transition",
        lambda _value, **_kwargs: events.append("journal"),
    )
    monkeypatch.setattr(
        transition,
        "_enable_recovery_unit",
        lambda: (_ for _ in ()).throw(transition.TransitionError("enable failed")),
    )
    monkeypatch.setattr(
        transition,
        "_remove_recovery_unit",
        lambda: pytest.fail("durable prepared state must not be erased"),
    )
    monkeypatch.setattr(
        transition,
        "_converge",
        lambda _value: pytest.fail("host mutation cannot precede recovery arming"),
    )

    with pytest.raises(transition.TransitionError, match="enable failed"):
        transition.prepare(
            previous_pair=PREVIOUS,
            target_pair=TARGET,
            previous_host_layer=PREVIOUS_HOST,
            target_host_layer=TARGET_HOST,
            publish_journal=tmp_path / "install-transaction.json",
        )

    assert events == ["unit-files", "journal"]


def test_retry_of_a_prepared_journal_rearms_for_the_recovery_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publish = tmp_path / "install-transaction.json"
    existing = _journal(publish=publish)
    events: list[str] = []
    monkeypatch.setattr(transition, "_load_transition", lambda: existing)
    monkeypatch.setattr(
        transition,
        "_install_recovery_unit",
        lambda: events.append("arm"),
    )
    result = transition.prepare(
        previous_pair=PREVIOUS,
        target_pair=TARGET,
        previous_host_layer=PREVIOUS_HOST,
        target_host_layer=TARGET_HOST,
        publish_journal=publish,
    )

    assert events == ["arm"]
    assert result == {
        "host_transition": "armed",
        "schema_version": 1,
        "target_pair": TARGET,
    }


def test_publish_journal_must_be_canonical_and_bind_the_exact_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    root_identity: None,
) -> None:
    del root_identity
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    publish = state / "install-transaction.json"
    monkeypatch.setattr(transition, "STATE_ROOT", state)
    expected = transition._publish_journal_expected(TARGET, PREVIOUS, PREVIOUS_HOST)
    publish.write_bytes(transition._canonical_json(expected))
    publish.chmod(0o600)

    value, payload = transition._load_publish_journal(
        publish,
        TARGET,
        PREVIOUS,
        PREVIOUS_HOST,
    )

    assert value == expected
    assert hashlib.sha256(payload).hexdigest() == hashlib.sha256(transition._canonical_json(expected)).hexdigest()

    wrong = {**expected, "pair_name": PREVIOUS}
    publish.write_bytes(transition._canonical_json(wrong))
    publish.chmod(0o600)
    with pytest.raises(transition.TransitionError, match="does not bind"):
        transition._load_publish_journal(
            publish,
            TARGET,
            PREVIOUS,
            PREVIOUS_HOST,
        )

    wrong = {**expected, "transition_from_pair": TARGET}
    publish.write_bytes(transition._canonical_json(wrong))
    publish.chmod(0o600)
    with pytest.raises(transition.TransitionError, match="does not bind"):
        transition._load_publish_journal(
            publish,
            TARGET,
            PREVIOUS,
            PREVIOUS_HOST,
        )


def test_recovery_unit_is_restart_free_and_enabled_without_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    root_identity: None,
) -> None:
    del root_identity
    system_units = tmp_path / "systemd"
    system_units.mkdir(mode=0o755)
    unit = system_units / transition.RECOVERY_UNIT_NAME
    continuation = system_units / transition.CONTINUATION_UNIT_NAME
    calls: list[tuple[str, ...]] = []
    enablement_checks: list[bool] = []
    monkeypatch.setattr(transition, "SYSTEM_UNIT_ROOT", system_units)
    monkeypatch.setattr(transition, "RECOVERY_UNIT", unit)
    monkeypatch.setattr(transition, "CONTINUATION_UNIT", continuation)
    monkeypatch.setattr(
        transition,
        "_systemctl",
        lambda *arguments, **_kwargs: calls.append(arguments),
    )
    monkeypatch.setattr(transition, "_unit_enabled", lambda _unit: True)
    monkeypatch.setattr(
        transition,
        "_verify_recovery_enablement",
        lambda *, enabled: enablement_checks.append(enabled),
    )

    transition._install_recovery_unit()

    payload = unit.read_text(encoding="ascii")
    assert "Restart=no" in payload
    assert "Restart=on-" not in payload
    assert "loginctl" not in payload
    assert "OnSuccess=" not in payload
    assert "recover --boot-fence" in payload
    assert "RemainAfterExit=yes" in payload
    assert "TimeoutStartSec=infinity" in payload
    assert (
        f"ExecStartPost={transition.SYSTEMCTL} --no-block start "
        f"{transition.CONTINUATION_UNIT_NAME}"
    ) in payload
    assert "RequiredBy=" + " ".join(transition.HOST_CONSUMER_UNITS) in payload
    assert all(unit_name in payload for unit_name in transition.HOST_CONSUMER_UNITS)
    assert unit.stat().st_mode & 0o777 == 0o644
    continuation_payload = continuation.read_text(encoding="ascii")
    assert "Before=" not in continuation_payload
    assert "ProtectHome=true" not in continuation_payload
    assert "RequiresMountsFor=/home/sgm " in continuation_payload
    assert " recover\n" in continuation_payload
    assert continuation.stat().st_mode & 0o777 == 0o644
    assert calls == [("daemon-reload",)]
    assert enablement_checks == [True]
    assert all("--now" not in call for call in calls)


def test_recovery_enablement_requires_every_consumer_and_is_fsynced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    root_identity: None,
) -> None:
    del root_identity
    system_units = tmp_path / "systemd"
    system_units.mkdir(mode=0o755)
    recovery = system_units / transition.RECOVERY_UNIT_NAME
    recovery.write_bytes(transition._recovery_unit_payload())
    recovery.chmod(0o644)
    monkeypatch.setattr(transition, "SYSTEM_UNIT_ROOT", system_units)
    monkeypatch.setattr(transition, "RECOVERY_UNIT", recovery)
    links = transition._recovery_enablement_links()
    for link in links:
        link.parent.mkdir(mode=0o755, exist_ok=True)
        link.symlink_to(recovery)

    transition._verify_recovery_enablement(enabled=True)

    missing = links[-1]
    missing.unlink()
    with pytest.raises(transition.TransitionError, match="dependency is absent"):
        transition._verify_recovery_enablement(enabled=True)
    for link in links[:-1]:
        link.unlink()
    transition._verify_recovery_enablement(enabled=False)


def test_recovery_cleanup_validates_and_removes_both_exact_unit_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    root_identity: None,
) -> None:
    del root_identity
    system_units = tmp_path / "systemd"
    system_units.mkdir(mode=0o755)
    recovery = system_units / transition.RECOVERY_UNIT_NAME
    continuation = system_units / transition.CONTINUATION_UNIT_NAME
    recovery.write_bytes(transition._recovery_unit_payload())
    continuation.write_bytes(transition._continuation_unit_payload())
    recovery.chmod(0o644)
    continuation.chmod(0o644)
    calls: list[tuple[str, ...]] = []
    enablement_checks: list[bool] = []
    monkeypatch.setattr(transition, "SYSTEM_UNIT_ROOT", system_units)
    monkeypatch.setattr(transition, "RECOVERY_UNIT", recovery)
    monkeypatch.setattr(transition, "CONTINUATION_UNIT", continuation)
    monkeypatch.setattr(
        transition,
        "_systemctl",
        lambda *arguments, **_kwargs: calls.append(arguments),
    )
    monkeypatch.setattr(
        transition,
        "_verify_recovery_enablement",
        lambda *, enabled: enablement_checks.append(enabled),
    )

    transition._remove_recovery_unit()

    assert not recovery.exists()
    assert not continuation.exists()
    assert calls == [
        ("daemon-reload",),
        ("stop", transition.RECOVERY_UNIT_NAME),
        ("reset-failed", transition.RECOVERY_UNIT_NAME),
        ("daemon-reload",),
    ]
    assert enablement_checks == [False]


def test_recovery_cleanup_removes_exact_dangling_links_after_unit_unlink_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    root_identity: None,
) -> None:
    del root_identity
    system_units = tmp_path / "systemd"
    system_units.mkdir(mode=0o755)
    recovery = system_units / transition.RECOVERY_UNIT_NAME
    continuation = system_units / transition.CONTINUATION_UNIT_NAME
    monkeypatch.setattr(transition, "SYSTEM_UNIT_ROOT", system_units)
    monkeypatch.setattr(transition, "RECOVERY_UNIT", recovery)
    monkeypatch.setattr(transition, "CONTINUATION_UNIT", continuation)
    links = transition._recovery_enablement_links()
    for link in links:
        link.parent.mkdir(mode=0o755, exist_ok=True)
        link.symlink_to(recovery)
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        transition,
        "_systemctl",
        lambda *arguments, **_kwargs: calls.append(arguments),
    )

    transition._remove_recovery_unit()

    assert all(not link.exists() and not link.is_symlink() for link in links)
    assert calls == [
        ("daemon-reload",),
        ("stop", transition.RECOVERY_UNIT_NAME),
        ("reset-failed", transition.RECOVERY_UNIT_NAME),
        ("daemon-reload",),
    ]


def test_atomic_host_install_recovers_from_an_old_target_mix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    root_identity: None,
) -> None:
    del root_identity
    old_control = tmp_path / "old" / "control"
    target_control = tmp_path / "target" / "control"
    installed_root = tmp_path / "installed"
    for directory in (old_control, target_control, installed_root):
        directory.mkdir(parents=True, mode=0o755)
    entries = (
        _entry(
            label="first",
            relative="first.py",
            installed=installed_root / "first",
        ),
        _entry(
            label="second",
            relative="second.py",
            installed=installed_root / "second",
        ),
    )
    for entry in entries:
        (old_control / entry.relative).write_bytes(f"old-{entry.label}\n".encode("ascii"))
        (target_control / entry.relative).write_bytes(f"target-{entry.label}\n".encode("ascii"))
        (old_control / entry.relative).chmod(0o644)
        (target_control / entry.relative).chmod(0o644)
        entry.installed.write_bytes((old_control / entry.relative).read_bytes())
        entry.installed.chmod(0o755)
    previous = _context(
        tmp_path / "contexts",
        PREVIOUS,
        host_sha=PREVIOUS_HOST,
        receipt_sha="a" * 64,
        manifest=entries,
    )
    target = _context(
        tmp_path / "contexts",
        TARGET,
        host_sha=TARGET_HOST,
        receipt_sha="b" * 64,
        manifest=entries,
    )
    object.__setattr__(previous, "control", old_control)
    object.__setattr__(target, "control", target_control)
    original = transition._atomic_install_entry
    installs = 0

    def interrupt_after_first(payload: bytes, entry: transition.ManifestEntry) -> None:
        nonlocal installs
        installs += 1
        if installs == 2:
            raise RuntimeError("simulated power loss")
        original(payload, entry)

    monkeypatch.setattr(transition, "_atomic_install_entry", interrupt_after_first)
    with pytest.raises(RuntimeError, match="power loss"):
        transition._install_target_host(previous, target)
    assert transition._installed_host_state(previous, target) == "mixed"

    monkeypatch.setattr(transition, "_atomic_install_entry", original)
    transition._install_target_host(previous, target)

    assert transition._installed_host_state(previous, target) == "target"
    assert not list(installed_root.glob(".*host-transition*"))


@pytest.mark.parametrize(
    ("pointer", "host_state", "phase", "expected_prefix"),
    (
        (
            PREVIOUS,
            "old",
            "prepared",
            ("deactivate", "native", "install", "activate", "commit"),
        ),
        (None, "old", "deactivated", ("native", "install", "activate", "commit")),
        (None, "mixed", "deactivated", ("install", "activate", "commit")),
        (None, "target", "host-installed", ("activate", "commit")),
        (None, "target", "activated", ("activate", "commit")),
        (TARGET, "target", "activated", ("commit",)),
    ),
)
def test_recovery_converges_every_reviewed_pointer_and_host_state_forward(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pointer: str | None,
    host_state: str,
    phase: str,
    expected_prefix: tuple[str, ...],
) -> None:
    manifest = (_entry(),)
    previous = _context(
        tmp_path,
        PREVIOUS,
        host_sha=PREVIOUS_HOST,
        receipt_sha="a" * 64,
        manifest=manifest,
    )
    target = _context(
        tmp_path,
        TARGET,
        host_sha=TARGET_HOST,
        receipt_sha="b" * 64,
        manifest=manifest,
    )
    value = _journal(phase=phase)
    value["manifest_sha256"] = previous.manifest_sha256
    state: dict[str, object] = {"pointer": pointer, "host": host_state}
    events: list[str] = []
    monkeypatch.setattr(transition, "_contexts", lambda _value: (previous, target))
    monkeypatch.setattr(transition, "_verify_publish_binding", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(transition, "_current_pair", lambda: state["pointer"])
    monkeypatch.setattr(
        transition,
        "_installed_host_state",
        lambda _previous, _target: str(state["host"]),
    )
    monkeypatch.setattr(transition, "_pair_transaction_exists", lambda: False)
    monkeypatch.setattr(transition, "_verify_consumers_quiescent", lambda: None)
    monkeypatch.setattr(transition, "_verify_native_runtime", lambda **_kwargs: None)
    monkeypatch.setattr(transition, "_systemctl", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(transition, "_quiesce_target_residue", lambda _target: None)
    monkeypatch.setattr(transition, "_selector_status", lambda: transition.DOCKER_STATUS)
    monkeypatch.setattr(transition, "_unit_active", lambda _unit: True)
    monkeypatch.setattr(transition, "_unit_enabled", lambda _unit: True)
    monkeypatch.setattr(transition, "_verify_target_runtime", lambda *_args: None)
    monkeypatch.setattr(
        transition,
        "_converge_previous_no_pair",
        lambda _previous: events.append("native"),
    )

    def deactivate(_previous: transition.PairContext) -> None:
        events.append("deactivate")
        state["pointer"] = None

    def install(_previous: transition.PairContext, _target: transition.PairContext) -> None:
        events.append("install")
        state["host"] = "target"

    def activate(_target: transition.PairContext) -> None:
        events.append("activate")
        state["pointer"] = TARGET

    def advance(current: dict[str, object], next_phase: str) -> dict[str, object]:
        updated = dict(current)
        updated["phase"] = next_phase
        return updated

    monkeypatch.setattr(transition, "_deactivate_previous", deactivate)
    monkeypatch.setattr(transition, "_install_target_host", install)
    monkeypatch.setattr(transition, "_activate_target", activate)
    monkeypatch.setattr(transition, "_advance", advance)
    monkeypatch.setattr(transition, "_commit", lambda _value: events.append("commit"))

    result = transition._converge(value)

    assert tuple(events) == expected_prefix
    assert result == {
        "active_pair": TARGET,
        "host_transition": "committed",
        "schema_version": 1,
    }


def test_recovery_rejects_mixed_host_bytes_while_the_old_pointer_is_live(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = (_entry(),)
    previous = _context(
        tmp_path,
        PREVIOUS,
        host_sha=PREVIOUS_HOST,
        receipt_sha="a" * 64,
        manifest=manifest,
    )
    target = _context(
        tmp_path,
        TARGET,
        host_sha=TARGET_HOST,
        receipt_sha="b" * 64,
        manifest=manifest,
    )
    value = _journal()
    value["manifest_sha256"] = previous.manifest_sha256
    monkeypatch.setattr(transition, "_contexts", lambda _value: (previous, target))
    monkeypatch.setattr(transition, "_verify_publish_binding", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(transition, "_current_pair", lambda: PREVIOUS)
    monkeypatch.setattr(transition, "_installed_host_state", lambda *_args: "mixed")
    monkeypatch.setattr(transition, "_pair_transaction_exists", lambda: False)

    with pytest.raises(transition.TransitionError, match="old pair pointer"):
        transition._converge(value)


def test_post_deactivation_install_failure_never_calls_an_old_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = (_entry(),)
    previous = _context(
        tmp_path,
        PREVIOUS,
        host_sha=PREVIOUS_HOST,
        receipt_sha="a" * 64,
        manifest=manifest,
    )
    target = _context(
        tmp_path,
        TARGET,
        host_sha=TARGET_HOST,
        receipt_sha="b" * 64,
        manifest=manifest,
    )
    value = _journal(phase="deactivated")
    value["manifest_sha256"] = previous.manifest_sha256
    events: list[str] = []
    monkeypatch.setattr(transition, "_contexts", lambda _value: (previous, target))
    monkeypatch.setattr(transition, "_verify_publish_binding", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(transition, "_current_pair", lambda: None)
    monkeypatch.setattr(transition, "_installed_host_state", lambda *_args: "mixed")
    monkeypatch.setattr(transition, "_pair_transaction_exists", lambda: False)
    monkeypatch.setattr(transition, "_verify_consumers_quiescent", lambda: events.append("quiescent"))
    monkeypatch.setattr(transition, "_verify_native_runtime", lambda **_kwargs: events.append("native"))
    monkeypatch.setattr(transition, "_advance", lambda current, _phase: current)
    monkeypatch.setattr(
        transition,
        "_install_target_host",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("install interrupted")),
    )

    with pytest.raises(RuntimeError, match="interrupted"):
        transition._converge(value)

    assert events == ["quiescent", "native"]


@pytest.mark.parametrize("pointer", [PREVIOUS, None])
def test_old_host_transaction_is_recovered_before_forward_transition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pointer: str | None,
) -> None:
    previous = _context(
        tmp_path,
        PREVIOUS,
        host_sha=PREVIOUS_HOST,
        receipt_sha="a" * 64,
        manifest=(_entry(),),
    )
    target = _context(
        tmp_path,
        TARGET,
        host_sha=TARGET_HOST,
        receipt_sha="b" * 64,
        manifest=(_entry(),),
    )
    value = _journal(phase="prepared" if pointer == PREVIOUS else "deactivated")
    value["manifest_sha256"] = previous.manifest_sha256
    state: dict[str, object] = {"pointer": pointer, "txn": True, "host": "old"}
    events: list[str] = []
    monkeypatch.setattr(transition, "_contexts", lambda _value: (previous, target))
    monkeypatch.setattr(transition, "_verify_publish_binding", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(transition, "_current_pair", lambda: state["pointer"])
    monkeypatch.setattr(transition, "_installed_host_state", lambda *_args: str(state["host"]))
    monkeypatch.setattr(transition, "_pair_transaction_exists", lambda: bool(state["txn"]))

    def manager(_context: transition.PairContext, command: str, *_args: str) -> dict[str, object]:
        assert command == "recover"
        events.append("recover-old")
        state["txn"] = False
        state["pointer"] = PREVIOUS if pointer == PREVIOUS else None
        return {}

    monkeypatch.setattr(transition, "_run_manager", manager)

    def deactivate(_context: transition.PairContext) -> None:
        events.append("deactivate")
        state["pointer"] = None

    monkeypatch.setattr(transition, "_deactivate_previous", deactivate)
    monkeypatch.setattr(transition, "_converge_previous_no_pair", lambda _context: events.append("native"))

    def install(_previous: transition.PairContext, _target: transition.PairContext) -> None:
        events.append("install")
        state["host"] = "target"

    monkeypatch.setattr(transition, "_install_target_host", install)
    monkeypatch.setattr(transition, "_systemctl", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(transition, "_quiesce_target_residue", lambda _target: None)
    monkeypatch.setattr(transition, "_selector_status", lambda: transition.DOCKER_STATUS)
    monkeypatch.setattr(transition, "_unit_active", lambda _unit: True)
    monkeypatch.setattr(transition, "_unit_enabled", lambda _unit: True)

    def activate(_target: transition.PairContext) -> None:
        events.append("activate")
        state["pointer"] = TARGET

    monkeypatch.setattr(transition, "_activate_target", activate)
    monkeypatch.setattr(transition, "_verify_target_runtime", lambda *_args: None)
    monkeypatch.setattr(
        transition,
        "_advance",
        lambda current, phase: {**current, "phase": phase},
    )
    monkeypatch.setattr(transition, "_commit", lambda _value: events.append("commit"))

    transition._converge(value)

    assert events[0] == "recover-old"
    assert events[-1] == "commit"


def test_boot_fence_never_runs_a_pair_manager_even_with_a_pending_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous = _context(
        tmp_path,
        PREVIOUS,
        host_sha=PREVIOUS_HOST,
        receipt_sha="a" * 64,
        manifest=(_entry(),),
    )
    target = _context(
        tmp_path,
        TARGET,
        host_sha=TARGET_HOST,
        receipt_sha="b" * 64,
        manifest=(_entry(),),
    )
    value = _journal()
    value["manifest_sha256"] = previous.manifest_sha256
    monkeypatch.setattr(transition, "_contexts", lambda _value: (previous, target))
    monkeypatch.setattr(transition, "_verify_publish_binding", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(transition, "_boot_fence_consumers", lambda: None)
    monkeypatch.setattr(transition, "_current_pair", lambda: PREVIOUS)
    monkeypatch.setattr(transition, "_installed_host_state", lambda *_args: "old")
    monkeypatch.setattr(transition, "_verify_consumers_quiescent", lambda: None)
    monkeypatch.setattr(
        transition,
        "_run_manager",
        lambda *_args: pytest.fail("the activating fence must never run a pair manager"),
    )

    assert transition._converge_boot_fence(value)["host_transition"] == "fenced"


def test_handoff_queues_the_fence_then_verifies_the_committed_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    @contextlib.contextmanager
    def lock(name: str):
        events.append(f"enter:{name}")
        try:
            yield
        finally:
            events.append(f"exit:{name}")

    monkeypatch.setattr(transition, "_installer_lock", lambda: lock("install"))
    monkeypatch.setattr(transition, "_operation_lock", lambda: lock("transition"))

    def systemctl(*arguments: str, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        events.append("systemctl:" + " ".join(arguments))
        return subprocess.CompletedProcess(arguments, 0, b"inactive\n", b"")

    monkeypatch.setattr(transition, "_systemctl", systemctl)
    monkeypatch.setattr(transition, "_unit_active", lambda _unit: False)
    monkeypatch.setattr(transition, "_load_committed_receipt", lambda _pair: {"state": "committed"})
    monkeypatch.setattr(transition, "_load_transition", lambda: None)
    expected = {
        "active_pair": TARGET,
        "host_transition": "committed",
        "schema_version": 1,
    }
    monkeypatch.setattr(transition, "_verify_committed_prepare", lambda *_args, **_kwargs: expected)

    result = transition._handoff_recovery(
        previous_pair=PREVIOUS,
        target_pair=TARGET,
        previous_host_layer=PREVIOUS_HOST,
        target_host_layer=TARGET_HOST,
    )

    assert result == expected
    assert events == [
        f"systemctl:reset-failed {transition.RECOVERY_UNIT_NAME}",
        f"systemctl:reset-failed {transition.CONTINUATION_UNIT_NAME}",
        f"systemctl:--no-block start {transition.RECOVERY_UNIT_NAME}",
        "enter:install",
        "enter:transition",
        "exit:transition",
        "exit:install",
    ]


def test_exact_retry_restarts_failed_continuation_behind_active_fence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    @contextlib.contextmanager
    def lock(name: str):
        events.append(f"enter:{name}")
        try:
            yield
        finally:
            events.append(f"exit:{name}")

    monkeypatch.setattr(transition, "_installer_lock", lambda: lock("install"))
    monkeypatch.setattr(transition, "_operation_lock", lambda: lock("transition"))
    monkeypatch.setattr(transition, "_unit_active", lambda unit: unit == transition.RECOVERY_UNIT_NAME)
    transition_reads = iter((_journal(), None))
    monkeypatch.setattr(transition, "_load_transition", lambda: next(transition_reads))
    monkeypatch.setattr(
        transition,
        "_verify_consumers_quiescent",
        lambda: events.append("quiescent"),
    )

    def systemctl(*arguments: str, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        events.append("systemctl:" + " ".join(arguments))
        return subprocess.CompletedProcess(arguments, 0, b"inactive\n", b"")

    monkeypatch.setattr(transition, "_systemctl", systemctl)
    monkeypatch.setattr(transition, "_load_committed_receipt", lambda _pair: {"state": "committed"})
    expected = {
        "active_pair": TARGET,
        "host_transition": "committed",
        "schema_version": 1,
    }
    monkeypatch.setattr(transition, "_verify_committed_prepare", lambda *_args, **_kwargs: expected)

    assert transition._handoff_recovery(
        previous_pair=PREVIOUS,
        target_pair=TARGET,
        previous_host_layer=PREVIOUS_HOST,
        target_host_layer=TARGET_HOST,
    ) == expected
    assert events == [
        "enter:install",
        "enter:transition",
        "quiescent",
        "exit:transition",
        "exit:install",
        f"systemctl:reset-failed {transition.CONTINUATION_UNIT_NAME}",
        f"systemctl:--no-block start {transition.CONTINUATION_UNIT_NAME}",
        "enter:install",
        "enter:transition",
        "exit:transition",
        "exit:install",
    ]


def test_handoff_accepts_commit_completed_before_active_fence_inspection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @contextlib.contextmanager
    def lock():
        yield

    monkeypatch.setattr(transition, "_installer_lock", lock)
    monkeypatch.setattr(transition, "_operation_lock", lock)
    monkeypatch.setattr(transition, "_unit_active", lambda _unit: True)
    monkeypatch.setattr(transition, "_load_transition", lambda: None)
    receipt = {"state": "committed"}
    monkeypatch.setattr(transition, "_load_committed_receipt", lambda _pair: receipt)
    expected = {
        "active_pair": TARGET,
        "host_transition": "committed",
        "schema_version": 1,
    }
    monkeypatch.setattr(transition, "_verify_committed_prepare", lambda *_args, **_kwargs: expected)
    monkeypatch.setattr(
        transition,
        "_systemctl",
        lambda *_args, **_kwargs: pytest.fail("a completed handoff must not restart systemd units"),
    )

    assert transition._handoff_recovery(
        previous_pair=PREVIOUS,
        target_pair=TARGET,
        previous_host_layer=PREVIOUS_HOST,
        target_host_layer=TARGET_HOST,
    ) == expected


def test_pointer_none_with_old_host_replays_exact_old_native_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous = _context(
        tmp_path,
        PREVIOUS,
        host_sha=PREVIOUS_HOST,
        receipt_sha="a" * 64,
        manifest=(_entry(),),
    )
    events: list[str] = []
    monkeypatch.setattr(transition, "_pair_transaction_exists", lambda: False)
    monkeypatch.setattr(
        transition,
        "_disable_unit",
        lambda unit: events.append(f"disable:{unit}"),
    )
    monkeypatch.setattr(transition, "_systemctl", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        transition,
        "_run_selector",
        lambda context, action: events.append(f"selector:{context.name}:{action}") or transition.NATIVE_STATUS,
    )
    monkeypatch.setattr(transition, "_current_pair", lambda: None)
    monkeypatch.setattr(
        transition,
        "_verify_consumers_quiescent",
        lambda: events.append("quiescent"),
    )
    monkeypatch.setattr(
        transition,
        "_verify_native_runtime",
        lambda **_kwargs: events.append("healthy"),
    )

    transition._converge_previous_no_pair(previous)

    selector_event = f"selector:{PREVIOUS}:native-select"
    assert selector_event in events
    assert all(event.startswith("disable:") for event in events[: events.index(selector_event)])
    assert events[-2:] == ["quiescent", "healthy"]


def test_pointer_none_old_host_rejects_a_preexisting_pair_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous = _context(
        tmp_path,
        PREVIOUS,
        host_sha=PREVIOUS_HOST,
        receipt_sha="a" * 64,
        manifest=(_entry(),),
    )
    monkeypatch.setattr(transition, "_pair_transaction_exists", lambda: True)
    monkeypatch.setattr(
        transition,
        "_disable_unit",
        lambda _unit: pytest.fail("host consumers changed before transaction rejection"),
    )

    with pytest.raises(transition.TransitionError, match="transaction blocks"):
        transition._converge_previous_no_pair(previous)


def test_mixed_host_native_health_uses_the_sgm_user_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def systemctl(
        *arguments: str,
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        calls.append(arguments)
        state = b"active\n" if "is-active" in arguments else b"enabled\n"
        return subprocess.CompletedProcess(arguments, 0, state, b"")

    monkeypatch.setattr(transition, "_systemctl", systemctl)
    monkeypatch.setattr(
        transition,
        "_selector_status",
        lambda: pytest.fail("a mixed host must not execute either selector"),
    )
    monkeypatch.setattr(transition, "_wait_http", lambda _url: None)

    transition._verify_native_runtime(use_selector=False)

    assert len(calls) == 4
    assert all(call[:2] == ("--user", "--machine=sgm@") for call in calls)
    assert {call[-1] for call in calls} == set(transition.NATIVE_UNITS)


def test_target_bytes_with_a_deactivated_phase_reload_before_activation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = (_entry(),)
    previous = _context(
        tmp_path,
        PREVIOUS,
        host_sha=PREVIOUS_HOST,
        receipt_sha="a" * 64,
        manifest=manifest,
    )
    target = _context(
        tmp_path,
        TARGET,
        host_sha=TARGET_HOST,
        receipt_sha="b" * 64,
        manifest=manifest,
    )
    value = _journal(phase="deactivated")
    value["manifest_sha256"] = previous.manifest_sha256
    state: dict[str, str | None] = {"pointer": None}
    events: list[str] = []
    monkeypatch.setattr(transition, "_contexts", lambda _value: (previous, target))
    monkeypatch.setattr(
        transition,
        "_verify_publish_binding",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(transition, "_current_pair", lambda: state["pointer"])
    monkeypatch.setattr(transition, "_installed_host_state", lambda *_args: "target")
    monkeypatch.setattr(transition, "_pair_transaction_exists", lambda: False)
    monkeypatch.setattr(
        transition,
        "_systemctl",
        lambda *arguments, **_kwargs: events.append("systemctl:" + " ".join(arguments)),
    )
    monkeypatch.setattr(
        transition,
        "_quiesce_target_residue",
        lambda _target: events.append("quiesce"),
    )
    monkeypatch.setattr(transition, "_selector_status", lambda: transition.DOCKER_STATUS)
    monkeypatch.setattr(transition, "_unit_active", lambda _unit: True)
    monkeypatch.setattr(transition, "_unit_enabled", lambda _unit: True)

    def activate(_target: transition.PairContext) -> None:
        events.append("activate")
        state["pointer"] = TARGET

    monkeypatch.setattr(transition, "_activate_target", activate)
    monkeypatch.setattr(
        transition,
        "_verify_target_runtime",
        lambda *_args: events.append("verify"),
    )

    def advance(current: dict[str, object], phase: str) -> dict[str, object]:
        updated = dict(current)
        updated["phase"] = phase
        return updated

    monkeypatch.setattr(transition, "_advance", advance)
    monkeypatch.setattr(transition, "_commit", lambda _value: events.append("commit"))

    transition._converge(value)

    assert events[:3] == ["systemctl:daemon-reload", "quiesce", "activate"]


def test_boot_fence_only_quiesces_and_defers_uncommitted_target_to_continuation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = (_entry(),)
    previous = _context(
        tmp_path,
        PREVIOUS,
        host_sha=PREVIOUS_HOST,
        receipt_sha="a" * 64,
        manifest=manifest,
    )
    target = _context(
        tmp_path,
        TARGET,
        host_sha=TARGET_HOST,
        receipt_sha="b" * 64,
        manifest=manifest,
    )
    value = _journal(phase="activated")
    value["manifest_sha256"] = previous.manifest_sha256
    state: dict[str, str | None] = {"pointer": TARGET}
    events: list[str] = []
    monkeypatch.setattr(transition, "_contexts", lambda _value: (previous, target))
    monkeypatch.setattr(
        transition,
        "_verify_publish_binding",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        transition,
        "_boot_fence_consumers",
        lambda: events.append("fence"),
    )
    monkeypatch.setattr(transition, "_current_pair", lambda: state["pointer"])
    monkeypatch.setattr(transition, "_installed_host_state", lambda *_args: "target")
    monkeypatch.setattr(transition, "_pair_transaction_exists", lambda: False)

    def deactivate(context: transition.PairContext) -> None:
        events.append(f"deactivate:{context.name}")
        state["pointer"] = None

    monkeypatch.setattr(transition, "_deactivate_previous", deactivate)
    monkeypatch.setattr(
        transition,
        "_quiesce_target_residue",
        lambda _target: events.append("quiesce"),
    )
    monkeypatch.setattr(transition, "_verify_consumers_quiescent", lambda: None)
    monkeypatch.setattr(transition, "_verify_native_runtime", lambda **_kwargs: None)
    monkeypatch.setattr(
        transition,
        "_activate_target",
        lambda _target: pytest.fail("the ordered boot fence must not activate Docker"),
    )

    result = transition._converge_boot_fence(value)

    assert result == {
        "host_transition": "fenced",
        "schema_version": 1,
        "target_pair": TARGET,
    }
    assert events == ["fence"]


@pytest.mark.parametrize("phase", ["host-installed", "activated"])
def test_continuation_reactivates_target_quiesced_by_boot_fence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    manifest = (_entry(),)
    previous = _context(
        tmp_path,
        PREVIOUS,
        host_sha=PREVIOUS_HOST,
        receipt_sha="a" * 64,
        manifest=manifest,
    )
    target = _context(
        tmp_path,
        TARGET,
        host_sha=TARGET_HOST,
        receipt_sha="b" * 64,
        manifest=manifest,
    )
    value = _journal(phase=phase)
    value["manifest_sha256"] = previous.manifest_sha256
    state = {"docker": False}
    events: list[str] = []
    monkeypatch.setattr(transition, "_contexts", lambda _value: (previous, target))
    monkeypatch.setattr(transition, "_verify_publish_binding", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(transition, "_current_pair", lambda: TARGET)
    monkeypatch.setattr(transition, "_installed_host_state", lambda *_args: "target")
    monkeypatch.setattr(transition, "_pair_transaction_exists", lambda: False)
    monkeypatch.setattr(
        transition,
        "_selector_status",
        lambda: transition.DOCKER_STATUS if state["docker"] else transition.NATIVE_STATUS,
    )
    monkeypatch.setattr(transition, "_unit_active", lambda _unit: state["docker"])
    monkeypatch.setattr(transition, "_unit_enabled", lambda _unit: state["docker"])
    monkeypatch.setattr(
        transition,
        "_quiesce_target_residue",
        lambda _target: events.append("quiesce"),
    )

    def activate(_target: transition.PairContext) -> None:
        events.append("activate")
        state["docker"] = True

    monkeypatch.setattr(transition, "_activate_target", activate)
    monkeypatch.setattr(
        transition,
        "_verify_target_runtime",
        lambda *_args: events.append("verify"),
    )

    def advance(current: dict[str, object], target_phase: str) -> dict[str, object]:
        updated = dict(current)
        updated["phase"] = target_phase
        events.append(f"phase:{target_phase}")
        return updated

    monkeypatch.setattr(transition, "_advance", advance)
    monkeypatch.setattr(transition, "_commit", lambda _value: events.append("commit"))

    assert transition._converge(value) == {
        "active_pair": TARGET,
        "host_transition": "committed",
        "schema_version": 1,
    }
    assert events == ["quiesce", "activate", "verify", "phase:activated", "commit"]


def test_boot_fence_synchronously_disables_every_ordered_consumer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        transition,
        "_systemctl",
        lambda *arguments, **_kwargs: calls.append(arguments),
    )

    monkeypatch.setattr(transition, "_unit_active", lambda _unit: False)
    monkeypatch.setattr(transition, "_unit_enabled", lambda _unit: False)

    transition._boot_fence_consumers()

    expected: list[tuple[str, ...]] = []
    for unit in transition.HOST_CONSUMER_UNITS:
        expected.extend(
            [
                ("disable", "--now", unit),
                ("stop", unit),
                ("reset-failed", unit),
            ]
        )
    assert calls == expected
    assert all("cancel" not in call and "start" not in call for call in calls)


def test_boot_fence_quiesces_consumers_before_loading_a_corrupt_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, ...] | str] = []
    journal = tmp_path / "host-layer-transition.json"
    journal.write_text("corrupt\n", encoding="ascii")
    monkeypatch.setattr(transition, "TRANSITION_JOURNAL", journal)

    def systemctl(
        *arguments: str,
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        events.append(arguments)
        if arguments[0] == "is-active":
            return subprocess.CompletedProcess(arguments, 3, b"inactive\n", b"")
        if arguments[0] == "is-enabled":
            return subprocess.CompletedProcess(arguments, 1, b"disabled\n", b"")
        return subprocess.CompletedProcess(arguments, 0, b"", b"")

    monkeypatch.setattr(transition, "_systemctl", systemctl)

    def corrupt() -> None:
        events.append("load")
        raise transition.TransitionError("corrupt")

    monkeypatch.setattr(
        transition,
        "_load_transition",
        corrupt,
    )

    with pytest.raises(transition.TransitionError, match="corrupt"):
        transition.recover(boot_fence=True)

    load_index = events.index("load")
    before_load = events[:load_index]
    for unit in transition.HOST_CONSUMER_UNITS:
        assert ("disable", "--now", unit) in before_load
        assert ("stop", unit) in before_load
        assert ("reset-failed", unit) in before_load
    assert not any(isinstance(event, tuple) and event and event[0] == "cancel" for event in before_load)


def test_boot_fence_with_no_journal_does_not_disturb_the_live_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        transition,
        "TRANSITION_JOURNAL",
        tmp_path / "absent-host-layer-transition.json",
    )
    monkeypatch.setattr(
        transition,
        "_boot_fence_consumers",
        lambda: pytest.fail("absence is not transition authority"),
    )
    monkeypatch.setattr(
        transition,
        "_load_transition",
        lambda: pytest.fail("an absent journal must not be parsed"),
    )

    assert transition.recover(boot_fence=True) == {
        "fenced": False,
        "schema_version": 1,
    }


def test_deactivate_failure_after_pointer_removal_converges_native_in_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous = _context(
        tmp_path,
        PREVIOUS,
        host_sha=PREVIOUS_HOST,
        receipt_sha="a" * 64,
        manifest=(_entry(),),
    )
    events: list[str] = []
    monkeypatch.setattr(transition, "_disable_unit", lambda _unit: None)
    monkeypatch.setattr(transition, "_systemctl", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        transition,
        "_run_manager",
        lambda *_args: (_ for _ in ()).throw(transition.TransitionError("crash")),
    )
    monkeypatch.setattr(transition, "_pair_transaction_exists", lambda: False)
    monkeypatch.setattr(transition, "_current_pair", lambda: None)
    monkeypatch.setattr(
        transition,
        "_converge_previous_no_pair",
        lambda _previous: events.append("native"),
    )

    transition._deactivate_previous(previous)

    assert events == ["native"]


def test_deactivate_failure_before_pointer_removal_restores_previous_development(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous = _context(
        tmp_path,
        PREVIOUS,
        host_sha=PREVIOUS_HOST,
        receipt_sha="a" * 64,
        manifest=(_entry(),),
    )
    calls: list[tuple[str, ...]] = []

    def manager(
        _context: transition.PairContext,
        *arguments: str,
    ) -> dict[str, object]:
        calls.append(arguments)
        if arguments[0] == "deactivate-initial":
            raise transition.TransitionError("crash")
        return {"active_pair": PREVIOUS}

    monkeypatch.setattr(transition, "_disable_unit", lambda _unit: None)
    monkeypatch.setattr(transition, "_systemctl", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(transition, "_run_manager", manager)
    monkeypatch.setattr(transition, "_pair_transaction_exists", lambda: False)
    monkeypatch.setattr(transition, "_current_pair", lambda: PREVIOUS)
    monkeypatch.setattr(
        transition,
        "_run_selector",
        lambda *_args: transition.DOCKER_STATUS,
    )
    monkeypatch.setattr(transition, "_unit_active", lambda _unit: True)
    monkeypatch.setattr(transition, "_unit_enabled", lambda _unit: True)
    monkeypatch.setattr(transition, "_wait_http", lambda _url: None)

    with pytest.raises(transition.TransitionError, match="development was restored"):
        transition._deactivate_previous(previous)

    assert calls == [
        ("deactivate-initial", PREVIOUS),
        ("activate-development", PREVIOUS),
    ]


def test_commit_publishes_receipt_then_removes_journals_and_recovery_units(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    publish = state / "install-transaction.json"
    transition_journal = state / "host-layer-transition.json"
    publish.write_text("publish\n", encoding="ascii")
    transition_journal.write_text("transition\n", encoding="ascii")
    value = _journal(phase="activated", publish=publish)
    events: list[str] = []
    monkeypatch.setattr(transition, "TRANSITION_JOURNAL", transition_journal)
    monkeypatch.setattr(
        transition,
        "_write_committed_receipt",
        lambda _value: events.append("receipt") or {},
    )

    def remove(path: Path, *, expected_sha256: str) -> None:
        del expected_sha256
        events.append("publish" if path == publish else "transition")

    monkeypatch.setattr(transition, "_remove_file_exact", remove)
    monkeypatch.setattr(
        transition,
        "_remove_recovery_unit",
        lambda: events.append("recovery-unit"),
    )

    transition._commit(value)

    assert events == ["receipt", "publish", "transition", "recovery-unit"]


def test_committed_receipt_is_immutable_canonical_and_root_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    root_identity: None,
) -> None:
    del root_identity
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    commits = state / "host-layer-transition-commits"
    value = _journal(phase="activated", publish=state / "install-transaction.json")
    monkeypatch.setattr(transition, "STATE_ROOT", state)
    monkeypatch.setattr(transition, "COMMITTED_ROOT", commits)
    monkeypatch.setattr(transition, "_installed_helper_sha256", lambda: "d" * 64)

    receipt = transition._write_committed_receipt(value)
    path = commits / f"{TARGET}.json"

    assert commits.stat().st_mode & 0o777 == 0o700
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.read_bytes() == transition._canonical_json(receipt)
    assert transition._load_committed_receipt(TARGET) == receipt
    assert transition._write_committed_receipt(value) == receipt

    drifted = {**receipt, "publish_journal_sha256": "e" * 64}
    path.write_bytes(transition._canonical_json(drifted))
    path.chmod(0o600)
    with pytest.raises(transition.TransitionError, match="drifted"):
        transition._write_committed_receipt(value)


def test_committed_prepare_reverifies_exact_target_without_publication_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = (_entry(),)
    previous = _context(
        tmp_path,
        PREVIOUS,
        host_sha=PREVIOUS_HOST,
        receipt_sha="a" * 64,
        manifest=manifest,
    )
    target = _context(
        tmp_path,
        TARGET,
        host_sha=TARGET_HOST,
        receipt_sha="c" * 64,
        manifest=manifest,
    )
    helper_sha = "d" * 64
    publish_sha = hashlib.sha256(
        transition._canonical_json(transition._publish_journal_expected(TARGET, PREVIOUS, PREVIOUS_HOST))
    ).hexdigest()
    receipt = {
        "helper_sha256": helper_sha,
        "manifest_sha256": previous.manifest_sha256,
        "previous_host_layer_sha256": PREVIOUS_HOST,
        "previous_pair": PREVIOUS,
        "previous_receipt_sha256": previous.receipt_sha256,
        "publish_journal_sha256": publish_sha,
        "schema_version": 1,
        "state": "committed",
        "target_host_layer_sha256": TARGET_HOST,
        "target_pair": TARGET,
        "target_receipt_sha256": target.receipt_sha256,
    }
    events: list[str] = []
    monkeypatch.setattr(
        transition,
        "_load_pair",
        lambda name: previous if name == PREVIOUS else target,
    )
    monkeypatch.setattr(transition, "_installed_helper_sha256", lambda: "e" * 64)
    monkeypatch.setattr(transition, "_current_pair", lambda: TARGET)
    monkeypatch.setattr(transition, "_installed_host_state", lambda *_args: "target")
    monkeypatch.setattr(transition, "_pair_transaction_exists", lambda: False)
    monkeypatch.setattr(
        transition,
        "_verify_target_runtime",
        lambda *_args: events.append("verified"),
    )

    result = transition._verify_committed_prepare(
        receipt,
        previous_pair=PREVIOUS,
        target_pair=TARGET,
        previous_host_layer=PREVIOUS_HOST,
        target_host_layer=TARGET_HOST,
    )

    assert result == {
        "active_pair": TARGET,
        "host_transition": "committed",
        "schema_version": 1,
    }
    assert events == ["verified"]


def test_prepare_returns_from_exact_committed_receipt_without_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = {"state": "committed"}
    result = {
        "active_pair": TARGET,
        "host_transition": "committed",
        "schema_version": 1,
    }
    events: list[str] = []
    monkeypatch.setattr(transition, "_load_transition", lambda: None)
    monkeypatch.setattr(
        transition,
        "_load_committed_receipt",
        lambda pair: receipt if pair == TARGET else None,
    )
    monkeypatch.setattr(
        transition,
        "_verify_committed_prepare",
        lambda *_args, **_kwargs: events.append("verified") or result,
    )
    monkeypatch.setattr(
        transition,
        "_load_publish_journal",
        lambda *_args: pytest.fail("committed retry must not require publication"),
    )

    actual = transition.prepare(
        previous_pair=PREVIOUS,
        target_pair=TARGET,
        previous_host_layer=PREVIOUS_HOST,
        target_host_layer=TARGET_HOST,
        publish_journal=tmp_path / "absent-install-transaction.json",
    )

    assert actual == result
    assert events == ["verified"]


def test_phase_updates_are_monotonic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    value = _journal(phase="host-installed")
    monkeypatch.setattr(transition, "_write_transition", lambda _value: None)

    assert transition._advance(value, "activated")["phase"] == "activated"
    with pytest.raises(transition.TransitionError, match="backwards"):
        transition._advance(value, "deactivated")


def test_status_requires_no_secret_or_noncanonical_output_when_idle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(transition, "_load_transition", lambda: None)

    value = transition.status()

    assert value == {"active": False, "schema_version": 1}
    assert json.loads(transition._canonical_json(value)) == value
