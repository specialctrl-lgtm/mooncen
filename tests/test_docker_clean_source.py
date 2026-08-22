from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_verifier() -> ModuleType:
    path = ROOT / "deploy" / "docker" / "verify_clean_source.py"
    spec = importlib.util.spec_from_file_location("mooncen_docker_source_verifier", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(repository: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )


def _write(repository: Path, relative_path: str, content: str = "content\n") -> Path:
    path = repository / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _commit(repository: Path, message: str) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=MoonCen Test",
            "-c",
            "user.email=mooncen-test@example.invalid",
            "-c",
            "commit.gpgSign=false",
            "commit",
            "-m",
            message,
        ],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )


@pytest.fixture
def committed_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "source"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "core.autocrlf", "false")
    _write(repository, "control.txt", "control\n")
    _write(repository, "src/app.py", "print('tracked')\n")
    _git(repository, "add", "control.txt", "src/app.py")
    _commit(repository, "fixture")
    return repository


def _inspect(verifier: ModuleType, repository: Path):
    return verifier.inspect_source(
        repository,
        required_paths=("control.txt",),
        control_input_paths=(),
        build_input_paths=("src",),
    )


def test_clean_committed_source_passes(committed_repository: Path) -> None:
    verifier = _load_verifier()

    report = _inspect(verifier, committed_repository)

    assert report.ok
    assert report.issue_count == 0


@pytest.mark.parametrize("state", ["untracked", "modified", "deleted", "staged"])
def test_dirty_build_inputs_fail_closed(
    committed_repository: Path,
    state: str,
) -> None:
    verifier = _load_verifier()
    tracked = committed_repository / "src" / "app.py"
    if state == "untracked":
        _write(committed_repository, "src/untracked.py")
    elif state == "modified":
        tracked.write_text("print('modified')\n", encoding="utf-8")
    elif state == "deleted":
        tracked.unlink()
    else:
        tracked.write_text("print('staged')\n", encoding="utf-8")
        _git(committed_repository, "add", "src/app.py")

    report = _inspect(verifier, committed_repository)

    assert not report.ok
    assert report.changes
    assert report.changes[0].path.startswith("src/")


def test_dirty_transitive_control_input_fails_closed(
    committed_repository: Path,
) -> None:
    verifier = _load_verifier()
    control_dependency = _write(
        committed_repository,
        "control-plane/ui/src/api.ts",
        "export const endpoint = '/api';\n",
    )
    _git(committed_repository, "add", "control-plane/ui/src/api.ts")
    _commit(committed_repository, "add transitive control input")
    control_dependency.write_text("export const endpoint = '/changed';\n", encoding="utf-8")

    report = verifier.inspect_source(
        committed_repository,
        required_paths=("control.txt",),
        control_input_paths=("control-plane",),
        build_input_paths=("src",),
    )

    assert [(change.status, change.path) for change in report.changes] == [
        (" M", "control-plane/ui/src/api.ts")
    ]


def test_required_control_file_must_exist_in_head(committed_repository: Path) -> None:
    verifier = _load_verifier()
    _write(committed_repository, "new-control.txt")

    report = verifier.inspect_source(
        committed_repository,
        required_paths=("control.txt", "new-control.txt"),
        control_input_paths=(),
        build_input_paths=("src",),
    )

    assert report.missing_from_head == ("new-control.txt",)
    assert any(change.path == "new-control.txt" for change in report.changes)


def test_required_control_file_must_remain_in_worktree(committed_repository: Path) -> None:
    verifier = _load_verifier()
    (committed_repository / "control.txt").unlink()

    report = _inspect(verifier, committed_repository)

    assert report.missing_from_worktree == ("control.txt",)
    assert any(change.path == "control.txt" for change in report.changes)


def test_required_copy_root_must_remain_in_worktree(committed_repository: Path) -> None:
    verifier = _load_verifier()
    (committed_repository / "src" / "app.py").unlink()
    (committed_repository / "src").rmdir()

    report = _inspect(verifier, committed_repository)

    assert report.missing_from_worktree == ("src",)
    assert not report.missing_from_head


def test_required_copy_root_must_exist_in_head(committed_repository: Path) -> None:
    verifier = _load_verifier()
    _write(committed_repository, "future/app.py")

    report = verifier.inspect_source(
        committed_repository,
        required_paths=("control.txt",),
        control_input_paths=(),
        build_input_paths=("src", "future"),
    )

    assert report.missing_from_head == ("future",)
    assert any(change.path == "future/app.py" for change in report.changes)


def test_gitignored_file_fails_unless_dockerignore_excludes_it(
    committed_repository: Path,
) -> None:
    verifier = _load_verifier()
    _write(
        committed_repository,
        ".gitignore",
        "src/cache/\nsrc/local-only.py\n",
    )
    _write(committed_repository, ".dockerignore", "**/cache\n")
    _git(committed_repository, "add", ".gitignore", ".dockerignore")
    _commit(committed_repository, "ignore contracts")
    _write(committed_repository, "src/cache/ignored.py", "CACHE SECRET\n")
    _write(committed_repository, "src/local-only.py", "LOCAL SECRET\n")

    report = verifier.inspect_source(
        committed_repository,
        required_paths=("control.txt", ".dockerignore"),
        control_input_paths=(),
        build_input_paths=("src",),
    )

    assert [(change.status, change.path) for change in report.changes] == [
        ("!!", "src/local-only.py")
    ]
    assert "SECRET" not in verifier.format_failure(report)


def test_failure_output_is_filename_only_and_bounded(committed_repository: Path) -> None:
    verifier = _load_verifier()
    for index in range(8):
        _write(
            committed_repository,
            f"src/untracked-{index}.py",
            f"SECRET-CONTENT-{index}\n",
        )

    report = _inspect(verifier, committed_repository)
    message = verifier.format_failure(report, limit=3)

    assert "SECRET-CONTENT" not in message
    assert message.count("git-??") == 3
    assert "5 additional issue(s) omitted" in message
    assert len(message) < 2_000


def test_manifest_rejects_paths_outside_repository(committed_repository: Path) -> None:
    verifier = _load_verifier()

    with pytest.raises(verifier.SourceVerificationError, match="unsafe path"):
        verifier.inspect_source(
            committed_repository,
            required_paths=("../outside",),
            control_input_paths=(),
            build_input_paths=("src",),
        )


@pytest.mark.parametrize("unsafe_path", [".", "./src", "src//app.py", "src/"])
def test_manifest_rejects_noncanonical_paths(
    committed_repository: Path,
    unsafe_path: str,
) -> None:
    verifier = _load_verifier()

    with pytest.raises(verifier.SourceVerificationError, match="unsafe path"):
        verifier.inspect_source(
            committed_repository,
            required_paths=("control.txt",),
            control_input_paths=(),
            build_input_paths=(unsafe_path,),
        )


def test_replace_ref_cannot_change_verified_head_tree(
    committed_repository: Path,
) -> None:
    verifier = _load_verifier()
    base = _git(committed_repository, "rev-parse", "HEAD").stdout.strip()
    original_branch = _git(
        committed_repository, "branch", "--show-current"
    ).stdout.strip()
    _git(committed_repository, "switch", "-c", "replacement-fixture")
    (committed_repository / "control.txt").unlink()
    _git(committed_repository, "rm", "control.txt")
    _commit(committed_repository, "replacement without control")
    replacement = _git(committed_repository, "rev-parse", "HEAD").stdout.strip()
    _git(committed_repository, "switch", original_branch)
    _git(committed_repository, "replace", base, replacement)

    try:
        report = _inspect(verifier, committed_repository)
    finally:
        _git(committed_repository, "replace", "-d", base)

    assert report.ok


@pytest.mark.parametrize("mode", ["120000", "160000"])
def test_head_link_modes_are_refused(
    committed_repository: Path,
    mode: str,
) -> None:
    verifier = _load_verifier()
    if mode == "120000":
        object_id = _git(
            committed_repository, "hash-object", "-w", "src/app.py"
        ).stdout.strip()
    else:
        object_id = _git(committed_repository, "rev-parse", "HEAD").stdout.strip()
    _git(
        committed_repository,
        "update-index",
        "--add",
        "--cacheinfo",
        f"{mode},{object_id},src/app.py",
    )
    _commit(committed_repository, "unsafe Git mode fixture")

    with pytest.raises(verifier.SourceVerificationError, match="regular tracked files"):
        _inspect(verifier, committed_repository)


def test_worktree_symbolic_link_is_refused(
    committed_repository: Path,
    tmp_path: Path,
) -> None:
    verifier = _load_verifier()
    tracked = committed_repository / "src" / "app.py"
    target = tmp_path / "outside.py"
    target.write_text("print('outside')\n", encoding="utf-8")
    tracked.unlink()
    try:
        tracked.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symbolic links are unavailable: {exc}")

    with pytest.raises(verifier.SourceVerificationError, match="links or special files"):
        _inspect(verifier, committed_repository)
