from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from deploy.an2p import container_evidence_handoff as handoff_module
from deploy.an2p import mooncen_register_container_evidence as registration_entrypoint
from deploy.docker.release_manifest import (
    VALIDATION_CHECKS,
    create_release_manifest,
    create_validation_receipt,
    write_json_evidence,
)


TREE = "2" * 40
TARGET_IDENTITY = "9" * 64


def _release_source(
    root: Path,
    *,
    validated_at: str = "2026-08-19T12:10:00Z",
) -> tuple[Path, dict[str, object], dict[str, object]]:
    root.mkdir(mode=0o750)
    root.chmod(0o750)
    release = root / TREE
    release.mkdir(mode=0o750)
    release.chmod(0o750)
    bundle = release / "images.tar"
    bundle.write_bytes(b"reviewed Docker images")
    compose = release / "compose.production.yaml"
    compose.write_text("name: mooncen-production\n", encoding="ascii")
    manifest = create_release_manifest(
        base_commit="1" * 40,
        source_tree=TREE,
        snapshot_commit="3" * 40,
        platform="linux/amd64",
        bundle_sha256=hashlib.sha256(bundle.read_bytes()).hexdigest(),
        compose_sha256=hashlib.sha256(compose.read_bytes()).hexdigest(),
        build_policy_sha256="6" * 64,
        migration_ledger_sha256="7" * 64,
        images={
            "api": {
                "tag": f"mooncen/api:release-{TREE}",
                "image_id": "sha256:" + "a" * 64,
            },
            "frontend": {
                "tag": f"mooncen/frontend:release-{TREE}",
                "image_id": "sha256:" + "b" * 64,
            },
        },
        created_at="2026-08-19T12:00:00Z",
    )
    # Keep the fixture valid across calendar days; production expiry checking
    # remains unchanged and the receipt digest is generated from this value.
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=1)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    receipt = create_validation_receipt(
        release=manifest,
        target="an2p-dev",
        target_identity=TARGET_IDENTITY,
        checks={name: True for name in VALIDATION_CHECKS},
        validated_at=validated_at,
        expires_at=expires_at,
    )
    write_json_evidence(release / "release.json", manifest)
    write_json_evidence(release / "validation.json", receipt, receipt=True)
    for path in release.iterdir():
        path.chmod(0o640)
    return release, manifest, receipt


def _destination_root(path: Path) -> Path:
    path.mkdir(mode=0o750)
    path.chmod(0o750)
    return path


def _handoff(source_root: Path, destination_root: Path):
    return handoff_module.handoff(
        TREE,
        source_root=source_root,
        destination_root=destination_root,
        source_uid=os.geteuid(),
        source_gid=os.getegid(),
        destination_uid=os.geteuid(),
        destination_gid=os.getegid(),
    )


def test_root_handoff_installs_immutable_worker_bundle_and_is_idempotent(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    _release_source(source_root)
    destination_root = _destination_root(tmp_path / "releases")

    installed = _handoff(source_root, destination_root)
    repeated = _handoff(source_root, destination_root)

    assert installed["installed"] is True
    assert installed["idempotent"] is False
    assert repeated == {**installed, "installed": False, "idempotent": True}
    destination = destination_root / TREE
    assert destination.stat().st_mode & 0o777 == 0o750
    assert {item.name for item in destination.iterdir()} == handoff_module.EXPECTED_FILES
    for item in destination.iterdir():
        metadata = item.lstat()
        assert metadata.st_mode & 0o777 == 0o640
        assert metadata.st_nlink == 1


def test_root_handoff_rejects_replay_with_different_receipt(tmp_path: Path) -> None:
    first_source = tmp_path / "first-source"
    _release_source(first_source)
    destination_root = _destination_root(tmp_path / "releases")
    _handoff(first_source, destination_root)

    second_source = tmp_path / "second-source"
    _release_source(second_source, validated_at="2026-08-19T12:11:00Z")

    with pytest.raises(handoff_module.EvidenceHandoffError, match="differs"):
        _handoff(second_source, destination_root)


def test_root_handoff_cleans_only_exact_owned_partial_stage(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    _release_source(source_root)
    destination_root = _destination_root(tmp_path / "releases")
    stale = destination_root / f".handoff-{TREE}-AbCd1234"
    stale.mkdir(mode=0o700)
    (stale / "partial").write_bytes(b"partial")

    result = _handoff(source_root, destination_root)

    assert result["installed"] is True
    assert not stale.exists()


def test_root_handoff_rejects_paths_symlinks_and_non_exact_file_sets(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    release, _manifest, _receipt = _release_source(source_root)
    destination_root = _destination_root(tmp_path / "releases")

    with pytest.raises(handoff_module.EvidenceHandoffError, match="40 lowercase"):
        handoff_module.handoff("/tmp/operator-selected")

    extra = release / "operator.txt"
    extra.write_bytes(b"unreviewed")
    extra.chmod(0o640)
    with pytest.raises(handoff_module.EvidenceHandoffError, match="file set"):
        _handoff(source_root, destination_root)
    extra.unlink()

    compose = release / "compose.production.yaml"
    target = release / "compose-target"
    target.write_bytes(compose.read_bytes())
    target.chmod(0o640)
    compose.unlink()
    compose.symlink_to(target.name)
    with pytest.raises(handoff_module.EvidenceHandoffError, match="file set|metadata"):
        _handoff(source_root, destination_root)


def test_registration_entrypoint_derives_one_pending_runtime_pair_by_tree(
    tmp_path: Path,
) -> None:
    releases = tmp_path / "releases"
    releases.mkdir(mode=0o755)
    releases.chmod(0o755)
    pair_name = f"runtime-pair.{'1' * 40}.{TREE}.{'3' * 64}"
    control = releases / pair_name / "control"
    python = control / ".venv/bin/python"
    registrar = control / "tools/register_container_deployment_evidence.py"
    python.parent.mkdir(parents=True)
    registrar.parent.mkdir(parents=True)
    for directory in (releases / pair_name, control):
        directory.chmod(0o755)
    python.write_text("#!/bin/sh\nexit 1\n", encoding="ascii")
    python.chmod(0o755)
    registrar.write_text("# reviewed registrar\n", encoding="ascii")
    registrar.chmod(0o644)

    runtime, executable = registration_entrypoint._immutable_control_runtime(
        TREE,
        releases=releases,
        trusted_uid=os.geteuid(),
        trusted_gid=os.getegid(),
    )

    assert runtime == control
    assert executable == python

    second = releases / f"runtime-pair.{'4' * 40}.{TREE}.{'5' * 64}"
    (second / "control").mkdir(parents=True)
    second.chmod(0o755)
    (second / "control").chmod(0o755)
    with pytest.raises(
        registration_entrypoint.EvidenceRegistrationError,
        match="exactly one",
    ):
        registration_entrypoint._immutable_control_runtime(
            TREE,
            releases=releases,
            trusted_uid=os.geteuid(),
            trusted_gid=os.getegid(),
        )


def test_registration_entrypoint_requires_pair_manager_inventory_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pair_name = f"runtime-pair.{'1' * 40}.{TREE}.{'3' * 64}"
    value = {
        "pair": pair_name,
        "schema_version": 1,
        "source_tree": TREE,
        "valid": True,
    }
    stdout = (
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("ascii")
    observed: dict[str, object] = {}
    monkeypatch.setattr(
        registration_entrypoint,
        "_root_owned_executable",
        lambda path: path,
    )

    def run(command: tuple[str, ...], **kwargs: object) -> SimpleNamespace:
        observed["command"] = command
        observed.update(kwargs)
        return SimpleNamespace(returncode=0, stdout=stdout, stderr=b"")

    monkeypatch.setattr(registration_entrypoint.subprocess, "run", run)

    registration_entrypoint._validate_runtime_pair(pair_name, TREE)

    assert observed["command"] == (
        "/usr/local/libexec/mooncen-an2p-runtime-manager",
        "validate",
        pair_name,
    )
    assert observed["stdin"] is registration_entrypoint.subprocess.DEVNULL
    assert observed["shell"] is False

    mismatched = dict(value, source_tree="4" * 40)
    bad_stdout = (
        json.dumps(mismatched, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("ascii")
    monkeypatch.setattr(
        registration_entrypoint.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=bad_stdout,
            stderr=b"",
        ),
    )
    with pytest.raises(registration_entrypoint.EvidenceRegistrationError, match="source tree"):
        registration_entrypoint._validate_runtime_pair(pair_name, TREE)
