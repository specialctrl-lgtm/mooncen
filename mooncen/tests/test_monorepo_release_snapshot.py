from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "deploy" / "docker" / "promote_monorepo_snapshot.py"
spec = importlib.util.spec_from_file_location("promote_monorepo_snapshot", MODULE_PATH)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def test_promote_uses_mooncen_subtree_as_release_root(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "MoonCen Test")
    git(root, "config", "user.email", "test@localhost")

    app = root / "mooncen"
    app.mkdir()
    (app / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    (app / "deploy").mkdir()
    (app / "deploy" / "marker.txt").write_text("reviewed\n", encoding="utf-8")
    (root / "outer-only.txt").write_text("not in release checkout\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-m", "reviewed monorepo state")

    base_commit = git(root, "rev-parse", "HEAD")
    expected_tree = git(root, "rev-parse", "HEAD:mooncen^{tree}")
    reference = "refs/mooncen/docker-release-snapshots/" + "a" * 32

    result = module.promote(root, reference=reference)

    assert result["base_commit"] == base_commit
    assert result["source_tree"] == expected_tree
    assert result["reference"] == reference
    snapshot = result["snapshot_commit"]
    assert git(root, "rev-parse", f"{snapshot}^1") == base_commit
    assert git(root, "rev-parse", f"{snapshot}^{{tree}}") == expected_tree
    assert git(root, "show", f"{snapshot}:compose.yaml") == "services: {}"

    missing_outer = subprocess.run(
        ["git", "cat-file", "-e", f"{snapshot}:outer-only.txt"],
        cwd=root,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert missing_outer.returncode != 0


def test_promote_refuses_dirty_monorepo(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "MoonCen Test")
    git(root, "config", "user.email", "test@localhost")
    (root / "mooncen").mkdir()
    (root / "mooncen" / "tracked.txt").write_text("clean\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-m", "initial")
    (root / "mooncen" / "tracked.txt").write_text("dirty\n", encoding="utf-8")

    try:
        module.promote(root)
    except module.PromotionError as exc:
        assert "not clean" in str(exc)
    else:
        raise AssertionError("dirty monorepo promotion unexpectedly succeeded")
