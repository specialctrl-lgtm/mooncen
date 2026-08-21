from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_promoter() -> ModuleType:
    path = ROOT / "deploy" / "docker" / "promote_review_snapshot.py"
    spec = importlib.util.spec_from_file_location("mooncen_docker_review_promoter", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(root: Path, *arguments: str, input_text: str | None = None) -> str:
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_AUTHOR_NAME": "MoonCen Test",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "MoonCen Test",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
        }
    )
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        env=environment,
        input=input_text,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    ).stdout.strip()


def _review_repository(tmp_path: Path) -> tuple[Path, str, str, str]:
    root = tmp_path / "source"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.name", "MoonCen Test")
    _git(root, "config", "user.email", "test@example.invalid")
    (root / "app.txt").write_text("base\n", encoding="utf-8")
    _git(root, "add", "app.txt")
    _git(root, "commit", "-m", "base")
    base = _git(root, "rev-parse", "HEAD")
    (root / "app.txt").write_text("reviewed\n", encoding="utf-8")
    _git(root, "add", "app.txt")
    tree = _git(root, "write-tree")
    review = _git(
        root,
        "commit-tree",
        tree,
        "-p",
        base,
        "-m",
        "WIP: local Docker development review snapshot",
        "-m",
        "Local review only. Do not push, merge, deploy, or release.",
    )
    _git(root, "reset", "--mixed", base)
    return root, base, tree, review


def test_promotes_exact_reviewed_tree_to_dedicated_release_reference(
    tmp_path: Path,
) -> None:
    promoter = _load_promoter()
    root, base, tree, review = _review_repository(tmp_path)
    reference = f"refs/mooncen/docker-release-snapshots/{'1' * 32}"

    result = promoter.promote_review_snapshot(
        root=root,
        review_commit=review,
        expected_base_commit=base,
        expected_source_tree=tree,
        reference=reference,
        confirmation=f"PROMOTE DOCKER {tree[:12]}",
    )

    assert result["review_commit"] == review
    assert result["base_commit"] == base
    assert result["source_tree"] == tree
    assert result["reference"] == reference
    snapshot = result["snapshot_commit"]
    assert _git(root, "rev-parse", reference) == snapshot
    assert _git(root, "rev-parse", f"{snapshot}^1") == base
    assert _git(root, "rev-parse", f"{snapshot}^{{tree}}") == tree
    assert _git(root, "show", "-s", "--format=%s", snapshot) == (
        "MoonCen reviewed Docker release snapshot"
    )


def test_rejects_stale_confirmation_without_creating_reference(tmp_path: Path) -> None:
    promoter = _load_promoter()
    root, base, tree, review = _review_repository(tmp_path)
    reference = f"refs/mooncen/docker-release-snapshots/{'2' * 32}"

    with pytest.raises(promoter.PromotionError, match="confirmation"):
        promoter.promote_review_snapshot(
            root=root,
            review_commit=review,
            expected_base_commit=base,
            expected_source_tree=tree,
            reference=reference,
            confirmation="PROMOTE DOCKER 000000000000",
        )

    result = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", reference],
        cwd=root,
        check=False,
        timeout=15,
    )
    assert result.returncode == 1


def test_rejects_noncanonical_review_commit(tmp_path: Path) -> None:
    promoter = _load_promoter()
    root, base, tree, _review = _review_repository(tmp_path)
    unreviewed = _git(root, "commit-tree", tree, "-p", base, "-m", "not reviewed")

    with pytest.raises(promoter.PromotionError, match="canonical"):
        promoter.promote_review_snapshot(
            root=root,
            review_commit=unreviewed,
            expected_base_commit=base,
            expected_source_tree=tree,
            reference=f"refs/mooncen/docker-release-snapshots/{'3' * 32}",
            confirmation=f"PROMOTE DOCKER {tree[:12]}",
        )


def test_removes_only_its_exact_reference_when_post_create_validation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    promoter = _load_promoter()
    root, base, tree, review = _review_repository(tmp_path)
    reference = f"refs/mooncen/docker-release-snapshots/{'4' * 32}"
    original_git = promoter._git
    failed = False

    def fail_first_reference_read(
        repository: Path,
        *arguments: str,
        **kwargs: object,
    ) -> str:
        nonlocal failed
        if arguments == ("rev-parse", "--verify", reference) and not failed:
            failed = True
            raise promoter.PromotionError("simulated post-create verification failure")
        return original_git(repository, *arguments, **kwargs)

    monkeypatch.setattr(promoter, "_git", fail_first_reference_read)

    with pytest.raises(promoter.PromotionError, match="simulated"):
        promoter.promote_review_snapshot(
            root=root,
            review_commit=review,
            expected_base_commit=base,
            expected_source_tree=tree,
            reference=reference,
            confirmation=f"PROMOTE DOCKER {tree[:12]}",
        )

    assert failed is True
    result = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", reference],
        cwd=root,
        check=False,
        timeout=15,
    )
    assert result.returncode == 1
