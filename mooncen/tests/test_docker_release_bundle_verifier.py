from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from deploy.docker import verify_release_bundle as verifier
from deploy.docker.release_manifest import create_release_manifest, write_json_evidence


TREE = "2" * 40
API_ID = "sha256:" + "a" * 64
FRONTEND_ID = "sha256:" + "b" * 64


def _release_directory(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    release = tmp_path / "release"
    release.mkdir(mode=0o700)
    bundle = release / "images.tar"
    bundle.write_bytes(b"reviewed docker bundle")
    compose = release / "compose.production.yaml"
    compose.write_text("name: mooncen-production\n", encoding="utf-8")
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
            "api": {"tag": f"mooncen/api:release-{TREE}", "image_id": API_ID},
            "frontend": {
                "tag": f"mooncen/frontend:release-{TREE}",
                "image_id": FRONTEND_ID,
            },
        },
        created_at="2026-08-19T12:00:00Z",
    )
    write_json_evidence(release / "release.json", manifest)
    return release, manifest


def test_verifies_bundle_compose_and_loaded_image_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release, manifest = _release_directory(tmp_path)
    calls: list[tuple[str, ...]] = []

    def docker(arguments, *, root):
        del root
        calls.append(tuple(arguments))
        if arguments == ("context", "show"):
            return "default"
        if arguments[0:2] == ("context", "inspect"):
            return "unix:///var/run/docker.sock"
        if arguments[0:3] == ("image", "load", "--input"):
            return "Loaded"
        tag = arguments[-1]
        if tag == manifest["images"]["api"]["tag"]:
            return API_ID
        if tag == manifest["images"]["frontend"]["tag"]:
            return FRONTEND_ID
        raise AssertionError(arguments)

    monkeypatch.setattr(verifier, "_docker", docker)
    result = verifier.verify_release_directory(release, load_images=True)

    assert result["release_digest"] == manifest["release_digest"]
    assert result["image_ids"] == {"api": API_ID, "frontend": FRONTEND_ID}
    assert any(call[:2] == ("image", "load") for call in calls)


def test_tampered_bundle_is_rejected_before_docker_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release, _manifest = _release_directory(tmp_path)
    (release / "images.tar").write_bytes(b"tampered")
    monkeypatch.setattr(
        verifier,
        "_docker",
        lambda *_args, **_kwargs: pytest.fail("Docker must not run for a bad bundle"),
    )
    with pytest.raises(verifier.VerificationError, match="bundle SHA"):
        verifier.verify_release_directory(release, load_images=True)


def test_artifact_only_stage_verification_never_touches_docker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release, manifest = _release_directory(tmp_path)
    monkeypatch.setattr(
        verifier,
        "_docker",
        lambda *_args, **_kwargs: pytest.fail("stage verification must not touch Docker"),
    )

    result = verifier.verify_release_artifacts(release)

    assert result == {
        "release_digest": manifest["release_digest"],
        "source_tree": TREE,
        "bundle_sha256": manifest["bundle_sha256"],
        "compose_sha256": manifest["compose_sha256"],
    }


def test_tampered_compose_is_rejected_before_docker_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release, _manifest = _release_directory(tmp_path)
    (release / "compose.production.yaml").write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setattr(
        verifier,
        "_docker",
        lambda *_args, **_kwargs: pytest.fail("Docker must not run for bad Compose"),
    )
    with pytest.raises(verifier.VerificationError, match="Compose SHA"):
        verifier.verify_release_directory(release, load_images=True)


def test_loaded_tag_drift_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release, _manifest = _release_directory(tmp_path)

    def docker(arguments, *, root):
        del root
        if arguments == ("context", "show"):
            return "default"
        if arguments[0:2] == ("context", "inspect"):
            return "unix:///var/run/docker.sock"
        if arguments[0:2] == ("image", "load"):
            return "Loaded"
        return "sha256:" + "f" * 64

    monkeypatch.setattr(verifier, "_docker", docker)
    with pytest.raises(verifier.VerificationError, match="image ID"):
        verifier.verify_release_directory(release, load_images=True)


def test_symlink_bundle_is_rejected(tmp_path: Path) -> None:
    release, _manifest = _release_directory(tmp_path)
    bundle = release / "images.tar"
    target = release / "other.tar"
    target.write_bytes(bundle.read_bytes())
    bundle.unlink()
    bundle.symlink_to(target.name)
    with pytest.raises(verifier.VerificationError, match="unsafe"):
        verifier.verify_release_directory(release, load_images=False)


def test_group_writable_release_directory_is_rejected(tmp_path: Path) -> None:
    release, _manifest = _release_directory(tmp_path)
    release.chmod(0o770)
    with pytest.raises(verifier.VerificationError, match="directory is unsafe"):
        verifier.verify_release_directory(release, load_images=False)
