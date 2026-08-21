from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from deploy.docker import build_release_bundle as builder


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str, str, str]:
    root = tmp_path / "source"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.name", "MoonCen Test")
    _git(root, "config", "user.email", "test@example.invalid")
    (root / "base.txt").write_text("base\n", encoding="utf-8")
    _git(root, "add", "base.txt")
    _git(root, "commit", "-m", "base")
    base = _git(root, "rev-parse", "HEAD")
    (root / "source.txt").write_text("release\n", encoding="utf-8")
    _git(root, "add", "source.txt")
    _git(root, "commit", "-m", "snapshot")
    snapshot = _git(root, "rev-parse", "HEAD")
    tree = _git(root, "rev-parse", "HEAD^{tree}")
    return root, base, tree, snapshot


def test_attest_checkout_binds_exact_parent_tree_and_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, base, tree, snapshot = _repository(tmp_path)
    monkeypatch.setattr(builder, "require_clean_source", lambda _root: None)

    builder.attest_checkout(
        root,
        base_commit=base,
        source_tree=tree,
        snapshot_commit=snapshot,
    )

    with pytest.raises(builder.BuildError, match="tree"):
        builder.attest_checkout(
            root,
            base_commit=base,
            source_tree="f" * 40,
            snapshot_commit=snapshot,
        )


def test_attest_checkout_rejects_dirty_docker_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, base, tree, snapshot = _repository(tmp_path)

    def reject(_root: Path) -> None:
        raise builder.SourceVerificationError("dirty")

    monkeypatch.setattr(builder, "require_clean_source", reject)
    with pytest.raises(builder.BuildError, match="do not match"):
        builder.attest_checkout(
            root,
            base_commit=base,
            source_tree=tree,
            snapshot_commit=snapshot,
        )


def test_builder_supplies_its_own_exact_safe_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(
        arguments: tuple[str, ...], *, root: Path, timeout: int
    ) -> str:
        captured.update(arguments=arguments, root=root, timeout=timeout)
        return "ok"

    monkeypatch.setattr(builder, "_run", fake_run)
    assert builder._git(tmp_path, "status", "--porcelain") == "ok"
    assert captured["root"] == tmp_path.resolve()
    assert f"safe.directory={tmp_path.resolve()}" in captured["arguments"]


def test_migration_ledger_digest_is_sorted_and_content_bound(tmp_path: Path) -> None:
    root = tmp_path / "source"
    migrations = root / "DB" / "migrations"
    migrations.mkdir(parents=True)
    (migrations / "002_second.sql").write_text("SELECT 2;\n", encoding="utf-8")
    (migrations / "001_first.sql").write_text("SELECT 1;\n", encoding="utf-8")
    expected = hashlib.sha256(
        json.dumps(
            [
                {
                    "version": "001_first",
                    "checksum": hashlib.sha256(b"SELECT 1;\n").hexdigest(),
                },
                {
                    "version": "002_second",
                    "checksum": hashlib.sha256(b"SELECT 2;\n").hexdigest(),
                },
            ],
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    ).hexdigest()

    assert builder.migration_ledger_digest(root) == expected

    (migrations / "002_second.sql").write_text("SELECT 3;\n", encoding="utf-8")
    assert builder.migration_ledger_digest(root) != expected


def test_private_release_directory_requires_private_owned_root(tmp_path: Path) -> None:
    root = tmp_path / "releases"
    root.mkdir(mode=0o700)
    release = builder._private_release_directory(root, "a" * 40)
    assert release.stat().st_mode & 0o777 == 0o700

    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    with pytest.raises(builder.BuildError, match="must not be accessible"):
        builder._private_release_directory(public, "b" * 40)


def test_local_docker_requires_default_unix_socket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(["remote", "unix:///var/run/docker.sock", "linux/x86_64"])
    monkeypatch.setattr(builder, "_run", lambda *_args, **_kwargs: next(responses))
    with pytest.raises(builder.BuildError, match="default local"):
        builder.require_local_docker(tmp_path)


def test_builder_strips_remote_docker_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    class Result:
        returncode = 0
        stdout = "ok\n"

    def fake_run(*_args, **kwargs):
        captured.update(kwargs["env"])
        return Result()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("DOCKER_HOST", "tcp://attacker.invalid:2375")
    monkeypatch.setenv("DOCKER_CONTEXT", "remote")

    assert builder._run(("docker", "info"), root=tmp_path) == "ok"
    assert "DOCKER_HOST" not in captured
    assert "DOCKER_CONTEXT" not in captured


def test_existing_release_directory_is_never_overwritten(tmp_path: Path) -> None:
    root = tmp_path / "releases"
    root.mkdir(mode=0o700)
    (root / ("c" * 40)).mkdir()

    with pytest.raises(builder.BuildError, match="already exists"):
        builder._private_release_directory(root, "c" * 40)


def test_bundle_size_limit_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "bundle.tar"
    path.write_bytes(b"x" * 11)
    with pytest.raises(builder.BuildError, match="size limit"):
        builder._sha256_file(path, maximum=10)
