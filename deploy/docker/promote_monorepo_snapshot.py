#!/usr/bin/env python3
"""Promote the reviewed mooncen/ subtree from a monorepo commit to a release ref.

The repository worktree root is the parent directory that contains ``mooncen/``.
The created release commit deliberately uses the ``mooncen`` subtree as its root
Git tree, so the immutable release checkout preserves the historical MoonCen
layout (``deploy/``, ``DB/``, ``backend/`` ... at checkout root) without an
embedded Git repository.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Sequence


APP_PREFIX = "mooncen"
REFERENCE_PREFIX = "refs/mooncen/docker-release-snapshots/"
OBJECT_PATTERN = re.compile(r"\A[0-9a-f]{40}\Z")
REFERENCE_PATTERN = re.compile(
    r"\Arefs/mooncen/docker-release-snapshots/[0-9a-f]{32}\Z"
)
RELEASE_SUBJECT = "MoonCen reviewed Docker release snapshot"
COMMAND_TIMEOUT_SECONDS = 60


class PromotionError(RuntimeError):
    """Raised when the monorepo snapshot cannot be promoted safely."""


def _environment() -> dict[str, str]:
    environment = os.environ.copy()
    for name in tuple(environment):
        if name.startswith("GIT_CONFIG_"):
            environment.pop(name, None)
    for name in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_NAMESPACE",
    ):
        environment.pop(name, None)
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    return environment


def _git(root: Path, *arguments: str, input_data: bytes | None = None) -> str:
    try:
        completed = subprocess.run(
            [
                "git",
                "-c",
                f"core.hooksPath={os.devnull}",
                "-c",
                "core.fsmonitor=false",
                *arguments,
            ],
            cwd=root,
            env=_environment(),
            input=input_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PromotionError("Git could not prepare the monorepo release snapshot") from exc
    if completed.returncode != 0:
        operation = arguments[0] if arguments else "command"
        raise PromotionError(f"Git failed during {operation}")
    try:
        return completed.stdout.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError as exc:
        raise PromotionError("Git returned invalid UTF-8 metadata") from exc


def _assert_root(root: Path) -> Path:
    try:
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise PromotionError("Repository root does not exist") from exc
    if not resolved.is_dir() or resolved.is_symlink():
        raise PromotionError("Repository root is unsafe")
    if _git(resolved, "rev-parse", "--show-toplevel") != str(resolved):
        raise PromotionError("Run this tool at the monorepo Git worktree root")
    if not (resolved / APP_PREFIX).is_dir() or (resolved / APP_PREFIX).is_symlink():
        raise PromotionError("The reviewed mooncen/ application directory is unavailable")
    return resolved


def _require_clean(root: Path) -> None:
    status = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise PromotionError(
            "The monorepo worktree is not clean; commit or discard changes before release"
        )


def _reference_exists(root: Path, reference: str) -> bool:
    completed = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", reference],
        cwd=root,
        env=_environment(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=COMMAND_TIMEOUT_SECONDS,
    )
    if completed.returncode not in {0, 1}:
        raise PromotionError("Release reference existence check failed")
    return completed.returncode == 0


def promote(root: Path, *, reference: str | None = None) -> dict[str, str]:
    repository = _assert_root(root)
    _require_clean(repository)

    branch = _git(repository, "symbolic-ref", "--quiet", "--short", "HEAD")
    if branch != "main":
        raise PromotionError("Monorepo release snapshots must be promoted from main")

    base_commit = _git(repository, "rev-parse", "--verify", "HEAD^{commit}").lower()
    source_tree = _git(
        repository, "rev-parse", "--verify", f"HEAD:{APP_PREFIX}"
    ).lower()
    if OBJECT_PATTERN.fullmatch(base_commit) is None or OBJECT_PATTERN.fullmatch(source_tree) is None:
        raise PromotionError("Reviewed Git identities are invalid")

    selected_reference = reference or f"{REFERENCE_PREFIX}{secrets.token_hex(16)}"
    if REFERENCE_PATTERN.fullmatch(selected_reference) is None:
        raise PromotionError("Release reference must end with exactly 32 lowercase hex characters")
    if _reference_exists(repository, selected_reference):
        raise PromotionError("Release reference already exists")

    identity = _environment()
    identity.update(
        {
            "GIT_AUTHOR_NAME": "MoonCen Ops",
            "GIT_AUTHOR_EMAIL": "ops@localhost",
            "GIT_COMMITTER_NAME": "MoonCen Ops",
            "GIT_COMMITTER_EMAIL": "ops@localhost",
        }
    )
    completed = subprocess.run(
        [
            "git",
            "-c",
            f"core.hooksPath={os.devnull}",
            "commit-tree",
            source_tree,
            "-p",
            base_commit,
        ],
        cwd=repository,
        env=identity,
        input=(RELEASE_SUBJECT + "\n").encode("ascii"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=COMMAND_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        raise PromotionError("Git could not create the immutable release commit")
    snapshot_commit = completed.stdout.decode("ascii", errors="strict").strip().lower()
    if OBJECT_PATTERN.fullmatch(snapshot_commit) is None:
        raise PromotionError("Git returned an invalid release commit identity")

    zero = "0" * 40
    try:
        _git(
            repository,
            "update-ref",
            "--no-deref",
            selected_reference,
            snapshot_commit,
            zero,
        )
        if _git(repository, "rev-parse", "--verify", f"{selected_reference}^{{commit}}").lower() != snapshot_commit:
            raise PromotionError("Release reference did not converge")
        if _git(repository, "rev-parse", "--verify", f"{snapshot_commit}^{{tree}}").lower() != source_tree:
            raise PromotionError("Release commit tree changed")
        if _git(repository, "rev-parse", "--verify", f"{snapshot_commit}^1").lower() != base_commit:
            raise PromotionError("Release commit parent changed")
    except PromotionError:
        subprocess.run(
            ["git", "update-ref", "--no-deref", "-d", selected_reference, snapshot_commit],
            cwd=repository,
            env=_environment(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
        raise

    return {
        "reference": selected_reference,
        "snapshot_commit": snapshot_commit,
        "base_commit": base_commit,
        "source_tree": source_tree,
        "source_prefix": APP_PREFIX,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument(
        "--reference",
        help="optional exact refs/mooncen/docker-release-snapshots/<32hex> reference",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(argv)
    try:
        result = promote(Path.cwd(), reference=arguments.reference)
    except (OSError, PromotionError, UnicodeError) as exc:
        print(f"Monorepo Docker release promotion refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
