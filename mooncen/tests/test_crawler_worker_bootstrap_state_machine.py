from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import activate_crawler_worker_bootstrap_state as state


pytestmark = pytest.mark.skipif(os.name != "posix", reason="atomic symlink semantics require POSIX")

RELEASE_A = "1" * 32
RELEASE_B = "2" * 32
COMMIT = "a" * 40
ARCHIVE = "b" * 64
TREE = "c" * 64


def _layout(tmp_path: Path) -> Path:
    base = tmp_path / "mooncen-worker"
    for relative in ("releases", ".staging", ".transactions"):
        (base / relative).mkdir(parents=True, mode=0o700)
    return base


def _transaction(release_id: str, phase: str, previous: str = "") -> dict[str, str]:
    return {
        "format": "mooncen-worker-bootstrap-transaction-v1",
        "release_id": release_id,
        "phase": phase,
        "previous_target": previous,
        "new_target": f"releases/{release_id}",
        "commit": COMMIT,
        "archive_sha256": ARCHIVE,
        "tree_sha256": TREE,
    }


def _provenance(release: Path, release_id: str) -> None:
    release.mkdir(parents=True, exist_ok=False)
    info = release / ".deploy-info"
    info.write_text(
        "\n".join(
            (
                f"RELEASE_ID={release_id}",
                f"DEPLOY_COMMIT={COMMIT}",
                f"DEPLOY_ARCHIVE_SHA256={ARCHIVE}",
                f"DEPLOY_TREE_SHA256={TREE}",
            )
        )
        + "\n",
        encoding="ascii",
    )
    info.chmod(0o400)


@pytest.fixture
def root_tree_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    def safe_tree(path: Path, expected_parent: Path) -> None:
        if path.parent != expected_parent or not state.RELEASE_ID.fullmatch(path.name):
            raise state.ActivationError("unsafe test release tree")
        if path.is_symlink() or not path.is_dir():
            raise state.ActivationError("unsafe test release tree")

    monkeypatch.setattr(state, "_safe_tree", safe_tree)


def test_prepared_candidate_is_rolled_back(
    tmp_path: Path, root_tree_checks: None
) -> None:
    base = _layout(tmp_path)
    candidate = base / ".staging" / RELEASE_A
    candidate.mkdir()
    journal = base / ".transactions" / f"{RELEASE_A}.json"
    state._write_json(journal, _transaction(RELEASE_A, "prepared"))

    state.recover_transactions(base)

    assert not candidate.exists()
    assert not journal.exists()


def test_prepared_post_rename_orphan_is_validated_and_rolled_back(
    tmp_path: Path, root_tree_checks: None
) -> None:
    base = _layout(tmp_path)
    published = base / "releases" / RELEASE_A
    _provenance(published, RELEASE_A)
    journal = base / ".transactions" / f"{RELEASE_A}.json"
    state._write_json(journal, _transaction(RELEASE_A, "prepared"))

    state.recover_transactions(base)

    assert not published.exists()
    assert not journal.exists()


@pytest.mark.parametrize("candidate,published", [(True, True), (False, False)])
def test_prepared_impossible_tree_combinations_fail_closed(
    tmp_path: Path,
    root_tree_checks: None,
    candidate: bool,
    published: bool,
) -> None:
    base = _layout(tmp_path)
    if candidate:
        (base / ".staging" / RELEASE_A).mkdir()
    if published:
        _provenance(base / "releases" / RELEASE_A, RELEASE_A)
    state._write_json(
        base / ".transactions" / f"{RELEASE_A}.json",
        _transaction(RELEASE_A, "prepared"),
    )

    with pytest.raises(state.ActivationError, match="both candidate|lost both"):
        state.recover_transactions(base)


def test_published_transaction_rolls_forward_atomically(
    tmp_path: Path, root_tree_checks: None
) -> None:
    base = _layout(tmp_path)
    _provenance(base / "releases" / RELEASE_A, RELEASE_A)
    journal = base / ".transactions" / f"{RELEASE_A}.json"
    state._write_json(journal, _transaction(RELEASE_A, "published"))

    state.recover_transactions(base)

    assert os.readlink(base / "current") == f"releases/{RELEASE_A}"
    assert not journal.exists()


def test_post_switch_failure_restores_previous_pointer(
    tmp_path: Path,
    root_tree_checks: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _layout(tmp_path)
    _provenance(base / "releases" / RELEASE_A, RELEASE_A)
    os.symlink(f"releases/{RELEASE_A}", base / "current")
    candidate = base / ".staging" / RELEASE_B
    candidate.mkdir()

    monkeypatch.setattr(state, "_create_runtime", lambda *_args: ("d" * 64, "e" * 64))

    def seal(path: Path, deploy_info: dict[str, str]) -> None:
        info = path / ".deploy-info"
        info.write_text(
            "\n".join(f"{key}={value}" for key, value in sorted(deploy_info.items())) + "\n",
            encoding="ascii",
        )
        info.chmod(0o400)

    monkeypatch.setattr(state, "_seal_candidate", seal)
    prune_calls = 0

    def fail_after_switch(_base: Path) -> None:
        nonlocal prune_calls
        prune_calls += 1
        if prune_calls == 2:
            raise state.ActivationError("injected post-switch failure")

    monkeypatch.setattr(state, "_prune_releases", fail_after_switch)
    arguments = SimpleNamespace(
        base=base,
        candidate=candidate,
        wheelhouse=tmp_path / "wheelhouse",
        python=Path("/usr/bin/python3.12"),
        release_id=RELEASE_B,
        commit=COMMIT,
        archive_sha256=ARCHIVE,
        tree_sha256=TREE,
        worker_key="wtr-linux",
        kernel_hostname="worker.example",
        topology_sha256="f" * 64,
        resource_dropin_sha256="0" * 64,
    )

    with pytest.raises(state.ActivationError, match="injected post-switch"):
        state.activate(arguments)

    assert os.readlink(base / "current") == f"releases/{RELEASE_A}"
    assert not (base / "releases" / RELEASE_B).exists()
    assert not any((base / ".transactions").iterdir())


def test_retention_converges_to_current_plus_two_previous(
    tmp_path: Path, root_tree_checks: None
) -> None:
    base = _layout(tmp_path)
    identifiers = [f"{value:x}" * 32 for value in range(1, 6)]
    for index, release_id in enumerate(identifiers):
        release = base / "releases" / release_id
        release.mkdir()
        os.utime(release, ns=(index + 1, index + 1))
    os.symlink(f"releases/{identifiers[-1]}", base / "current")

    state._prune_releases(base)

    remaining = {path.name for path in (base / "releases").iterdir()}
    assert identifiers[-1] in remaining
    assert len(remaining) == state.MAX_RELEASES


def test_current_pointer_rejects_unsafe_symlink(
    tmp_path: Path, root_tree_checks: None
) -> None:
    base = _layout(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(str(outside), base / "current")

    with pytest.raises(state.ActivationError, match="target is invalid"):
        state._current_target(base, required=True)
