from __future__ import annotations

import hashlib
import importlib
import subprocess
from pathlib import Path

import pytest


def _git(repository: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )


def _write(repository: Path, relative_path: str, content: str) -> Path:
    path = repository / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
    return path


def _commit(repository: Path, message: str) -> None:
    _git(
        repository,
        "-c",
        "user.name=MoonCen Test",
        "-c",
        "user.email=mooncen-test@example.invalid",
        "-c",
        "commit.gpgSign=false",
        "commit",
        "-m",
        message,
    )


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "core.autocrlf", "false")
    _git(root, "config", "user.name", "MoonCen Test")
    _git(root, "config", "user.email", "mooncen-test@example.invalid")
    _write(root, ".gitignore", "src/*.env\n")
    _write(root, "control.txt", "control v1\n")
    _write(root, "src/app.py", "print('v1')\n")
    _write(root, "outside.txt", "outside v1\n")
    _git(root, "add", ".gitignore", "control.txt", "src/app.py", "outside.txt")
    _commit(root, "fixture")
    return root


def _create(repository: Path, branch: str = "docker-dev-snapshot-20260816"):
    snapshot = importlib.import_module("deploy.docker.create_review_snapshot")
    return snapshot.create_review_snapshot(
        repository,
        branch,
        required_paths=(".gitignore", "control.txt"),
        control_input_paths=(),
        build_input_paths=("src",),
    )


def _index_digest(repository: Path) -> str:
    raw_path = _git(repository, "rev-parse", "--git-path", "index").stdout.strip()
    path = Path(raw_path)
    if not path.is_absolute():
        path = repository / path
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _install_compose_fixture(repository: Path) -> str:
    compose = """services:
  postgres:
    build:
      context: .
      dockerfile: deploy/docker/postgres.Dockerfile
  migrate:
    build:
      context: .
      dockerfile: deploy/docker/api.Dockerfile
  api:
    build:
      context: .
      dockerfile: deploy/docker/api.Dockerfile
  frontend:
    build:
      context: .
      dockerfile: deploy/docker/frontend.Dockerfile
"""
    _write(repository, "compose.yaml", compose)
    _write(
        repository,
        "deploy/docker/api.Dockerfile",
        "FROM scratch\nCOPY src /app/src\n",
    )
    _write(
        repository,
        "deploy/docker/frontend.Dockerfile",
        "FROM scratch\nCOPY src /app/src\n",
    )
    _write(repository, "deploy/docker/postgres.Dockerfile", "FROM scratch\n")
    _git(repository, "add", "compose.yaml", "deploy/docker")
    _commit(repository, "compose fixture")
    return compose


def _create_compose_snapshot(repository: Path):
    snapshot = importlib.import_module("deploy.docker.create_review_snapshot")
    return snapshot.create_review_snapshot(
        repository,
        "docker-dev-snapshot-20260816",
        required_paths=(
            ".gitignore",
            "control.txt",
            "compose.yaml",
            "deploy/docker/api.Dockerfile",
            "deploy/docker/frontend.Dockerfile",
            "deploy/docker/postgres.Dockerfile",
        ),
        control_input_paths=(),
        build_input_paths=("src",),
    )


def test_creates_fixed_local_wip_commit_without_switching_head(
    repository: Path,
) -> None:
    base = _git(repository, "rev-parse", "HEAD").stdout.strip()
    _write(repository, "control.txt", "control v2\n")

    result = _create(repository)

    assert result.base_commit == base
    assert _git(repository, "rev-parse", "HEAD").stdout.strip() == base
    assert _git(repository, "branch", "--show-current").stdout.strip() in {
        "master",
        "main",
    }
    assert (
        _git(repository, "show", "-s", "--format=%s", result.commit).stdout.strip()
        == "WIP: local Docker development review snapshot"
    )
    assert "Do not push, merge, deploy, or release" in _git(
        repository, "show", "-s", "--format=%b", result.commit
    ).stdout
    assert _git(repository, "show", f"{result.branch}:control.txt").stdout == (
        "control v2\n"
    )


def test_existing_snapshot_ref_is_refused(repository: Path) -> None:
    snapshot = importlib.import_module("deploy.docker.create_review_snapshot")
    branch = "docker-dev-snapshot-20260816"
    _git(repository, "branch", branch)

    with pytest.raises(snapshot.SnapshotError, match="already exists"):
        _create(repository, branch)


def test_untracked_transitive_control_input_is_captured(repository: Path) -> None:
    snapshot = importlib.import_module("deploy.docker.create_review_snapshot")
    control = _write(repository, "ops/new_control.py", "POLICY = 'reviewed'\n")

    result = snapshot.create_review_snapshot(
        repository,
        "docker-dev-snapshot-20260816",
        required_paths=(".gitignore", "control.txt"),
        control_input_paths=("ops",),
        build_input_paths=("src",),
    )

    assert "ops/new_control.py" in result.changed_paths
    assert _git(
        repository,
        "show",
        f"{result.branch}:ops/new_control.py",
    ).stdout == "POLICY = 'reviewed'\n"
    assert control.read_text(encoding="utf-8") == "POLICY = 'reviewed'\n"


def test_snapshot_boundary_covers_every_production_build_policy_path() -> None:
    policy = importlib.import_module("deploy.docker.production_runtime_integrity")
    verifier = importlib.import_module("deploy.docker.verify_clean_source")
    roots = tuple(
        dict.fromkeys(
            (
                *verifier.REQUIRED_CONTROL_PATHS,
                *verifier.CONTROL_INPUT_PATHS,
                *verifier.BUILD_INPUT_PATHS,
            )
        )
    )

    uncovered = {
        path
        for path in policy.BUILD_POLICY_PATHS
        if not any(path == root or path.startswith(f"{root}/") for root in roots)
    }
    assert uncovered == set()


def test_ignored_secret_is_excluded_from_snapshot(repository: Path) -> None:
    ignored = _write(
        repository,
        "src/local-secret.env",
        "TOKEN=github_pat_" + "A" * 60 + "\n",
    )
    _write(repository, "src/app.py", "print('review')\n")

    result = _create(repository)
    tree_paths = _git(
        repository, "ls-tree", "-r", "--name-only", result.branch
    ).stdout.splitlines()

    assert "src/local-secret.env" not in tree_paths
    assert ignored.is_file()


def test_current_index_bytes_and_staged_state_are_unchanged(repository: Path) -> None:
    _write(repository, "outside.txt", "outside staged\n")
    _git(repository, "add", "outside.txt")
    _write(repository, "src/app.py", "print('snapshot')\n")
    before = _index_digest(repository)

    result = _create(repository)

    assert _index_digest(repository) == before
    assert _git(
        repository, "diff", "--cached", "--name-only"
    ).stdout.splitlines() == ["outside.txt"]
    assert _git(repository, "show", f"{result.branch}:outside.txt").stdout == (
        "outside v1\n"
    )


def test_dirty_selected_files_are_captured_without_cleaning_worktree(
    repository: Path,
) -> None:
    tracked = _write(repository, "src/app.py", "print('dirty tracked')\n")
    untracked = _write(repository, "src/new.py", "print('dirty untracked')\n")

    result = _create(repository)

    assert set(result.changed_paths) == {"src/app.py", "src/new.py"}
    assert _git(repository, "show", f"{result.branch}:src/app.py").stdout == (
        "print('dirty tracked')\n"
    )
    assert _git(repository, "show", f"{result.branch}:src/new.py").stdout == (
        "print('dirty untracked')\n"
    )
    assert tracked.read_text(encoding="utf-8") == "print('dirty tracked')\n"
    assert untracked.read_text(encoding="utf-8") == "print('dirty untracked')\n"
    status = _git(repository, "status", "--short").stdout
    assert " M src/app.py" in status
    assert "?? src/new.py" in status


def test_credential_failure_is_filename_only_and_bounded(repository: Path) -> None:
    snapshot = importlib.import_module("deploy.docker.create_review_snapshot")
    fake_secret = "github_pat_" + "B" * 60
    for index in range(snapshot.MAX_REPORTED_PATHS + 3):
        _write(repository, f"src/credential-{index:02d}.txt", fake_secret + "\n")

    with pytest.raises(snapshot.SnapshotError) as raised:
        _create(repository)

    message = str(raised.value)
    assert fake_secret not in message
    assert "additional file(s) omitted" in message
    assert message.count("credential-") == snapshot.MAX_REPORTED_PATHS
    ref_check = subprocess.run(
        [
            "git",
            "show-ref",
            "--verify",
            "--quiet",
            "refs/heads/docker-dev-snapshot-20260816",
        ],
        cwd=repository,
        check=False,
        capture_output=True,
        timeout=15,
    )
    assert ref_check.returncode == 1


def test_non_repository_and_nested_directory_are_refused(
    repository: Path,
    tmp_path: Path,
) -> None:
    snapshot = importlib.import_module("deploy.docker.create_review_snapshot")
    not_repository = tmp_path / "not-repository"
    not_repository.mkdir()
    nested = repository / "nested"
    nested.mkdir()

    for root in (not_repository, nested):
        with pytest.raises(snapshot.SnapshotError):
            snapshot.create_review_snapshot(
                root,
                "docker-dev-snapshot-20260816",
                required_paths=("control.txt",),
                control_input_paths=(),
                build_input_paths=("src",),
            )


@pytest.mark.parametrize(
    "branch",
    [
        "docker-dev-snapshot-20260230",
        "docker-dev-snapshot-260816",
        "review/docker-dev-snapshot-20260816",
        "docker-dev-snapshot-20260816-extra",
        "docker-dev-snapshot-20٢٦٠٨١٦",
    ],
)
def test_branch_name_is_strict_and_calendar_valid(branch: str) -> None:
    snapshot = importlib.import_module("deploy.docker.create_review_snapshot")

    with pytest.raises(snapshot.SnapshotError):
        snapshot._validated_branch(branch)


def test_cli_has_no_custom_commit_message_option() -> None:
    snapshot = importlib.import_module("deploy.docker.create_review_snapshot")

    with pytest.raises(SystemExit) as raised:
        snapshot.main(
            [
                "--branch",
                "docker-dev-snapshot-20260816",
                "--message",
                "not allowed",
            ]
        )

    assert raised.value.code == 2


def test_binary_classified_file_is_still_scanned(repository: Path) -> None:
    snapshot = importlib.import_module("deploy.docker.create_review_snapshot")
    fake_secret = "github_pat_" + "C" * 60
    binary = repository / "src" / "binary.dat"
    binary.write_bytes(b"\x00prefix\x00" + fake_secret.encode("ascii") + b"\x00")

    with pytest.raises(snapshot.SnapshotError) as raised:
        _create(repository)

    message = str(raised.value)
    assert "src/binary.dat" in message
    assert fake_secret not in message


def test_deleted_and_ignored_required_controls_are_refused(
    repository: Path,
) -> None:
    snapshot = importlib.import_module("deploy.docker.create_review_snapshot")
    (repository / "control.txt").unlink()
    with pytest.raises(snapshot.SnapshotError, match="missing from the worktree"):
        _create(repository)

    _write(repository, "control.txt", "restored\n")
    ignored = _write(repository, "src/required.env", "LOCAL_ONLY=true\n")
    with pytest.raises(snapshot.SnapshotError):
        snapshot.create_review_snapshot(
            repository,
            "docker-dev-snapshot-20260817",
            required_paths=(".gitignore", "control.txt", "src/required.env"),
            control_input_paths=(),
            build_input_paths=("src",),
        )
    assert ignored.is_file()


def test_dot_manifest_path_is_refused(repository: Path) -> None:
    snapshot = importlib.import_module("deploy.docker.create_review_snapshot")

    with pytest.raises(snapshot.SnapshotError, match="manifest is invalid"):
        snapshot.create_review_snapshot(
            repository,
            "docker-dev-snapshot-20260816",
            required_paths=(".",),
            control_input_paths=(),
            build_input_paths=("src",),
        )


def test_embedded_repository_gitlink_is_refused(repository: Path) -> None:
    snapshot = importlib.import_module("deploy.docker.create_review_snapshot")
    embedded = repository / "src" / "embedded"
    embedded.mkdir()
    _git(embedded, "init")
    _git(embedded, "config", "core.autocrlf", "false")
    _write(embedded, "payload.py", "print('embedded')\n")
    _git(embedded, "add", "payload.py")
    _commit(embedded, "embedded fixture")
    _write(repository, "src/app.py", "print('outer change')\n")

    with pytest.raises(snapshot.SnapshotError, match="embedded repositories"):
        _create(repository)


def test_replace_ref_cannot_change_snapshot_base_tree(repository: Path) -> None:
    base = _git(repository, "rev-parse", "HEAD").stdout.strip()
    original_branch = _git(repository, "branch", "--show-current").stdout.strip()
    _git(repository, "switch", "-c", "replacement-fixture")
    _write(repository, "outside.txt", "replacement content\n")
    _git(repository, "add", "outside.txt")
    _commit(repository, "replacement fixture")
    replacement = _git(repository, "rev-parse", "HEAD").stdout.strip()
    _git(repository, "switch", original_branch)
    _git(repository, "replace", base, replacement)
    _write(repository, "src/app.py", "print('snapshot')\n")

    result = _create(repository)
    _git(repository, "replace", "-d", base)

    assert _git(repository, "show", f"{result.branch}:outside.txt").stdout == (
        "outside v1\n"
    )


def test_repository_hooks_are_disabled(repository: Path) -> None:
    hooks = repository / "fixture-hooks"
    hooks.mkdir()
    post_marker = repository / "post-index-hook-ran"
    ref_marker = repository / "reference-hook-ran"
    post_hook = _write(
        hooks,
        "post-index-change",
        f"#!/bin/sh\nprintf ran > '{post_marker.as_posix()}'\n",
    )
    ref_hook = _write(
        hooks,
        "reference-transaction",
        f"#!/bin/sh\nprintf ran > '{ref_marker.as_posix()}'\n",
    )
    post_hook.chmod(0o755)
    ref_hook.chmod(0o755)
    _git(repository, "config", "core.hooksPath", hooks.as_posix())
    _write(repository, "src/app.py", "print('hook safe')\n")

    _create(repository)

    assert not post_marker.exists()
    assert not ref_marker.exists()


def test_external_clean_filter_is_refused_before_git_add(repository: Path) -> None:
    snapshot = importlib.import_module("deploy.docker.create_review_snapshot")
    marker = repository / "clean-filter-ran"
    _write(repository, ".gitattributes", "src/*.py filter=evil\n")
    _git(repository, "add", ".gitattributes")
    _commit(repository, "filter fixture")
    _git(
        repository,
        "config",
        "filter.evil.clean",
        f"printf ran > '{marker.as_posix()}'; cat",
    )
    _git(repository, "config", "filter.evil.required", "true")
    _write(repository, "src/app.py", "print('must not filter')\n")

    with pytest.raises(snapshot.SnapshotError, match="External Git clean filters"):
        _create(repository)

    assert not marker.exists()


def test_late_untracked_file_is_detected(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = importlib.import_module("deploy.docker.create_review_snapshot")
    _write(repository, "src/app.py", "print('initial')\n")
    real_scan = snapshot._credential_scan

    def scan_then_create(root: Path, tree: str, paths: tuple[str, ...]) -> None:
        real_scan(root, tree, paths)
        _write(repository, "src/late.py", "print('late')\n")

    monkeypatch.setattr(snapshot, "_credential_scan", scan_then_create)

    with pytest.raises(snapshot.SnapshotError, match="were not staged"):
        _create(repository)


def test_staged_dockerfile_cannot_add_unmonitored_copy_source(
    repository: Path,
) -> None:
    snapshot = importlib.import_module("deploy.docker.create_review_snapshot")
    dockerfile = _write(
        repository,
        "deploy/docker/api.Dockerfile",
        "FROM scratch\nCOPY src /app/src\n",
    )
    _git(repository, "add", "deploy/docker/api.Dockerfile")
    _commit(repository, "dockerfile fixture")
    _write(
        repository,
        "deploy/docker/api.Dockerfile",
        dockerfile.read_text(encoding="utf-8") + "COPY extra /app/extra\n",
    )
    _write(repository, "extra/payload.txt", "not monitored\n")

    with pytest.raises(snapshot.SnapshotError, match="COPY sources are outside"):
        snapshot.create_review_snapshot(
            repository,
            "docker-dev-snapshot-20260816",
            required_paths=(
                ".gitignore",
                "control.txt",
                "deploy/docker/api.Dockerfile",
            ),
            control_input_paths=(),
            build_input_paths=("src",),
        )


def test_post_ref_index_race_rolls_back_exact_snapshot_ref(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = importlib.import_module("deploy.docker.create_review_snapshot")
    _write(repository, "src/app.py", "print('snapshot')\n")
    real_git = snapshot._git

    def racing_git(root: Path, arguments, **kwargs):
        result = real_git(root, arguments, **kwargs)
        if arguments[:2] == ("update-ref", "--no-deref") and "-d" not in arguments:
            _write(repository, "outside.txt", "concurrent staged change\n")
            _git(repository, "add", "outside.txt")
        return result

    monkeypatch.setattr(snapshot, "_git", racing_git)

    with pytest.raises(snapshot.SnapshotError, match="rolled back"):
        _create(repository)

    ref_check = subprocess.run(
        [
            "git",
            "show-ref",
            "--verify",
            "--quiet",
            "refs/heads/docker-dev-snapshot-20260816",
        ],
        cwd=repository,
        check=False,
        capture_output=True,
        timeout=15,
    )
    assert ref_check.returncode == 1


def test_post_ref_integrity_exception_rolls_back_exact_snapshot_ref(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = importlib.import_module("deploy.docker.create_review_snapshot")
    _write(repository, "src/app.py", "print('snapshot')\n")
    real_unchanged = snapshot._unchanged
    calls = 0

    def fail_post_update(*arguments, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 4:
            raise snapshot.SnapshotError("simulated integrity read failure")
        return real_unchanged(*arguments, **kwargs)

    monkeypatch.setattr(snapshot, "_unchanged", fail_post_update)

    with pytest.raises(snapshot.SnapshotError, match="rolled back"):
        _create(repository)

    ref_check = subprocess.run(
        [
            "git",
            "show-ref",
            "--verify",
            "--quiet",
            "refs/heads/docker-dev-snapshot-20260816",
        ],
        cwd=repository,
        check=False,
        capture_output=True,
        timeout=15,
    )
    assert calls == 4
    assert ref_check.returncode == 1


def test_head_symbolic_identity_change_is_detected(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = importlib.import_module("deploy.docker.create_review_snapshot")
    _git(repository, "branch", "concurrent-head")
    _write(repository, "src/app.py", "print('snapshot')\n")
    real_create_commit = snapshot._create_commit

    def create_then_move_head(root: Path, tree: str, base_commit: str) -> str:
        commit = real_create_commit(root, tree, base_commit)
        _git(repository, "symbolic-ref", "HEAD", "refs/heads/concurrent-head")
        return commit

    monkeypatch.setattr(snapshot, "_create_commit", create_then_move_head)

    with pytest.raises(snapshot.SnapshotError, match="before ref creation"):
        _create(repository)


def test_cleanup_failure_occurs_before_ref_creation(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = importlib.import_module("deploy.docker.create_review_snapshot")
    _write(repository, "src/app.py", "print('snapshot')\n")
    real_cleanup = snapshot._safe_delete_temporary_index

    def cleanup_then_fail(temporary) -> None:
        real_cleanup(temporary)
        raise snapshot.SnapshotError("simulated cleanup failure")

    monkeypatch.setattr(snapshot, "_safe_delete_temporary_index", cleanup_then_fail)

    with pytest.raises(snapshot.SnapshotError, match="simulated cleanup failure"):
        _create(repository)

    assert not (repository / ".git" / "refs" / "heads" / "docker-dev-snapshot-20260816").exists()


def test_inherited_git_config_cannot_enable_hooks(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hooks = repository / "injected-hooks"
    hooks.mkdir()
    marker = repository / "injected-hook-ran"
    hook = _write(
        hooks,
        "post-index-change",
        f"#!/bin/sh\nprintf ran > '{marker.as_posix()}'\n",
    )
    hook.chmod(0o755)
    _write(repository, "src/app.py", "print('snapshot')\n")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", hooks.as_posix())

    _create(repository)

    assert not marker.exists()


def test_credential_shaped_filename_is_redacted(repository: Path) -> None:
    snapshot = importlib.import_module("deploy.docker.create_review_snapshot")
    token = "github_pat_" + "D" * 50
    _write(repository, f"src/{token}.txt", token + "\n")

    with pytest.raises(snapshot.SnapshotError) as raised:
        _create(repository)

    message = str(raised.value)
    assert token not in message
    assert "<redacted-credential-like>" in message


def test_nested_symbolic_link_mode_is_refused(repository: Path) -> None:
    snapshot = importlib.import_module("deploy.docker.create_review_snapshot")
    link = repository / "src" / "link.txt"
    try:
        link.symlink_to("target.txt")
    except OSError:
        pytest.skip("symbolic link creation is unavailable")
    _git(repository, "add", "src/link.txt")
    _commit(repository, "symlink-mode fixture")
    assert link.is_symlink()
    _write(repository, "src/app.py", "print('snapshot')\n")

    with pytest.raises(snapshot.SnapshotError, match="links"):
        _create(repository)


@pytest.mark.parametrize(
    "context_directive",
    [
        "ADD extra /app/extra",
        "RUN --mount=type=bind,source=extra,target=/src true",
        "RUN --mount=source=extra,target=/src true",
        "ONBUILD COPY extra /app/extra",
        "COPY src/../extra /app/extra",
        "COPY --from=alpine /payload /app/payload",
        "FROM alpine:latest",
        "COPY --from=0 /payload /app/payload",
    ],
)
def test_staged_dockerfile_rejects_other_context_read_directives(
    repository: Path,
    context_directive: str,
) -> None:
    snapshot = importlib.import_module("deploy.docker.create_review_snapshot")
    _write(
        repository,
        "deploy/docker/api.Dockerfile",
        "FROM scratch\nCOPY src /app/src\n",
    )
    _git(repository, "add", "deploy/docker/api.Dockerfile")
    _commit(repository, "dockerfile fixture")
    _write(
        repository,
        "deploy/docker/api.Dockerfile",
        f"FROM scratch\nCOPY src /app/src\n{context_directive}\n",
    )
    _write(repository, "extra/payload.txt", "not monitored\n")

    with pytest.raises(snapshot.SnapshotError, match="could not be parsed safely"):
        snapshot.create_review_snapshot(
            repository,
            "docker-dev-snapshot-20260816",
            required_paths=(
                ".gitignore",
                "control.txt",
                "deploy/docker/api.Dockerfile",
            ),
            control_input_paths=(),
            build_input_paths=("src",),
        )


def test_staged_dockerfile_rejects_alternate_escape_parser_directive(
    repository: Path,
) -> None:
    snapshot = importlib.import_module("deploy.docker.create_review_snapshot")
    _write(
        repository,
        "deploy/docker/api.Dockerfile",
        "FROM scratch\nCOPY src /app/src\n",
    )
    _git(repository, "add", "deploy/docker/api.Dockerfile")
    _commit(repository, "dockerfile fixture")
    _write(
        repository,
        "deploy/docker/api.Dockerfile",
        "# escape=`\nFROM scratch\nCOPY src `\n  extra /app/source\n",
    )
    _write(repository, "extra/payload.txt", "not monitored\n")

    with pytest.raises(snapshot.SnapshotError, match="could not be parsed safely"):
        snapshot.create_review_snapshot(
            repository,
            "docker-dev-snapshot-20260816",
            required_paths=(
                ".gitignore",
                "control.txt",
                "deploy/docker/api.Dockerfile",
            ),
            control_input_paths=(),
            build_input_paths=("src",),
        )


def test_staged_compose_exact_build_graph_is_accepted(repository: Path) -> None:
    _install_compose_fixture(repository)
    _write(repository, "src/app.py", "print('compose snapshot')\n")

    result = _create_compose_snapshot(repository)

    assert "src/app.py" in result.changed_paths


def test_staged_compose_accepts_only_the_reviewed_runtime_config_bind(
    repository: Path,
) -> None:
    compose = _install_compose_fixture(repository)
    runtime_mount = """    volumes:
      - type: bind
        source: ${MOONCEN_RUNTIME_CONFIG_FILE:-./frontend2/public/runtime-config.js}
        target: /usr/share/nginx/html/runtime-config.js
        read_only: true
        bind:
          create_host_path: false
"""
    compose = compose.replace(
        "  frontend:\n    build:\n      context: .\n"
        "      dockerfile: deploy/docker/frontend.Dockerfile\n",
        "  frontend:\n    build:\n      context: .\n"
        "      dockerfile: deploy/docker/frontend.Dockerfile\n"
        + runtime_mount,
    )
    _write(repository, "compose.yaml", compose)
    _write(repository, "src/app.py", "print('runtime bind')\n")

    result = _create_compose_snapshot(repository)

    assert "src/app.py" in result.changed_paths

    unsafe = compose.replace("read_only: true", "read_only: false")
    other = repository.parent / "unsafe-source"
    other.mkdir()
    _git(other, "init")
    _git(other, "config", "user.name", "MoonCen Test")
    _git(other, "config", "user.email", "mooncen-test@example.invalid")
    _write(other, ".gitignore", "")
    _write(other, "control.txt", "control\n")
    _write(other, "src/app.py", "print('base')\n")
    _write(other, "compose.yaml", unsafe)
    for dockerfile in (
        "api.Dockerfile",
        "frontend.Dockerfile",
        "postgres.Dockerfile",
    ):
        source = repository / "deploy" / "docker" / dockerfile
        _write(other, f"deploy/docker/{dockerfile}", source.read_text(encoding="utf-8"))
    _git(other, "add", ".")
    _commit(other, "unsafe fixture")
    _write(other, "src/app.py", "print('changed')\n")
    with pytest.raises(
        importlib.import_module("deploy.docker.create_review_snapshot").SnapshotError,
        match="host bind mounts",
    ):
        _create_compose_snapshot(other)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (
            "      context: .\n      dockerfile: deploy/docker/postgres.Dockerfile",
            "      context: ../outside\n"
            "      dockerfile: deploy/docker/postgres.Dockerfile",
        ),
        (
            "      dockerfile: deploy/docker/postgres.Dockerfile",
            "      dockerfile: deploy/docker/postgres.Dockerfile\n"
            "      additional_contexts:\n"
            "        outside: ../outside",
        ),
        (
            "  postgres:\n    build:",
            "  postgres:\n    env_file: ../outside.env\n    build:",
        ),
        (
            "  postgres:\n    build:",
            "  postgres:\n"
            "    extends:\n"
            "      file: ../outside.yaml\n"
            "      service: base\n"
            "    build:",
        ),
        (
            "      dockerfile: deploy/docker/postgres.Dockerfile",
            "      dockerfile: deploy/docker/postgres.Dockerfile\n"
            "    volumes:\n"
            "      - ../outside:/mnt/outside",
        ),
        (
            "services:\n",
            "configs:\n  outside:\n    file: ../outside.conf\nservices:\n",
        ),
        (
            "services:\n",
            "include:\n  - ../outside.yaml\nservices:\n",
        ),
    ],
)
def test_staged_compose_rejects_external_build_or_file_inputs(
    repository: Path,
    old: str,
    new: str,
) -> None:
    snapshot = importlib.import_module("deploy.docker.create_review_snapshot")
    compose = _install_compose_fixture(repository)
    assert old in compose
    _write(repository, "compose.yaml", compose.replace(old, new, 1))

    with pytest.raises(snapshot.SnapshotError, match="Compose"):
        _create_compose_snapshot(repository)
