#!/usr/bin/env python3
"""Promote one audited Docker review snapshot into a release snapshot commit."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[2]
OBJECT_PATTERN = re.compile(r"\A[0-9a-f]{40}\Z")
REFERENCE_PATTERN = re.compile(
    r"\Arefs/mooncen/docker-release-snapshots/[0-9a-f]{32}\Z"
)
REVIEW_SUBJECT = "WIP: local Docker development review snapshot"
REVIEW_BODY = "Local review only. Do not push, merge, deploy, or release."
RELEASE_SUBJECT = "MoonCen reviewed Docker release snapshot"
COMMAND_TIMEOUT_SECONDS = 60


class PromotionError(RuntimeError):
    """Raised when review evidence cannot be promoted exactly."""


def _git_environment() -> dict[str, str]:
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


def _git(
    root: Path,
    *arguments: str,
    input_data: bytes | None = None,
    environment: dict[str, str] | None = None,
) -> str:
    command_environment = _git_environment()
    if environment:
        command_environment.update(environment)
    try:
        result = subprocess.run(
            [
                "git",
                "-c",
                f"core.hooksPath={os.devnull}",
                "-c",
                "core.fsmonitor=false",
                *arguments,
            ],
            cwd=root,
            env=command_environment,
            check=False,
            capture_output=True,
            input=input_data,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PromotionError("Git could not promote the Docker review snapshot") from exc
    if result.returncode != 0:
        raise PromotionError(f"Git promotion failed during {arguments[0]}")
    try:
        return result.stdout.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError as exc:
        raise PromotionError("Git returned invalid promotion metadata") from exc


def _reference_exists(root: Path, reference: str) -> bool:
    try:
        result = subprocess.run(
            [
                "git",
                "-c",
                f"core.hooksPath={os.devnull}",
                "show-ref",
                "--verify",
                "--quiet",
                reference,
            ],
            cwd=root,
            env=_git_environment(),
            check=False,
            capture_output=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PromotionError("Docker release reference could not be checked") from exc
    if result.returncode not in {0, 1}:
        raise PromotionError("Docker release snapshot reference could not be checked")
    return result.returncode == 0


def promote_review_snapshot(
    *,
    root: Path,
    review_commit: str,
    expected_base_commit: str,
    expected_source_tree: str,
    reference: str,
    confirmation: str,
) -> dict[str, str]:
    try:
        repository = root.resolve(strict=True)
    except OSError as exc:
        raise PromotionError("Repository root does not exist") from exc
    if root.is_symlink() or not repository.is_dir():
        raise PromotionError("Repository root is unsafe")
    if _git(repository, "rev-parse", "--show-toplevel") != str(repository):
        raise PromotionError("Promotion must run at the repository root")
    for label, value in (
        ("review commit", review_commit),
        ("base commit", expected_base_commit),
        ("source tree", expected_source_tree),
    ):
        if OBJECT_PATTERN.fullmatch(value) is None:
            raise PromotionError(f"Expected {label} is invalid")
    if REFERENCE_PATTERN.fullmatch(reference) is None:
        raise PromotionError("Docker release snapshot reference is invalid")
    expected_confirmation = f"PROMOTE DOCKER {expected_source_tree[:12]}"
    if confirmation != expected_confirmation:
        raise PromotionError("Typed Docker snapshot confirmation does not match")

    resolved_review = _git(
        repository,
        "rev-parse",
        "--verify",
        f"{review_commit}^{{commit}}",
    ).lower()
    resolved_tree = _git(
        repository,
        "rev-parse",
        "--verify",
        f"{review_commit}^{{tree}}",
    ).lower()
    parent_line = _git(repository, "rev-list", "--parents", "-n", "1", review_commit)
    parents = parent_line.split()
    if resolved_review != review_commit or len(parents) != 2:
        raise PromotionError("Docker review snapshot commit identity is invalid")
    if parents[1].lower() != expected_base_commit:
        raise PromotionError("Docker review snapshot base commit changed")
    if resolved_tree != expected_source_tree:
        raise PromotionError("Docker review snapshot source tree changed")
    subject = _git(repository, "show", "-s", "--format=%s", review_commit)
    body = _git(repository, "show", "-s", "--format=%b", review_commit)
    if subject != REVIEW_SUBJECT or body != REVIEW_BODY:
        raise PromotionError("Commit is not a canonical Docker review snapshot")

    zero = "0" * 40
    if _reference_exists(repository, reference):
        raise PromotionError("Docker release snapshot reference already exists")

    identity = {
        "GIT_AUTHOR_NAME": "MoonCen Ops",
        "GIT_AUTHOR_EMAIL": "ops@localhost",
        "GIT_COMMITTER_NAME": "MoonCen Ops",
        "GIT_COMMITTER_EMAIL": "ops@localhost",
    }
    release_commit = _git(
        repository,
        "commit-tree",
        expected_source_tree,
        "-p",
        expected_base_commit,
        input_data=(RELEASE_SUBJECT + "\n").encode("ascii"),
        environment=identity,
    ).lower()
    if OBJECT_PATTERN.fullmatch(release_commit) is None:
        raise PromotionError("Git returned an invalid Docker release commit")
    reference_update_attempted = False
    try:
        # The zero old object makes creation compare-and-swap: a concurrently
        # created reference is never overwritten.  If any post-create
        # verification fails, the matching delete below removes only the exact
        # object created by this invocation.
        reference_update_attempted = True
        _git(repository, "update-ref", "--no-deref", reference, release_commit, zero)
        if _git(repository, "rev-parse", "--verify", reference).lower() != release_commit:
            raise PromotionError("Docker release snapshot reference did not converge")
        if (
            _git(
                repository,
                "rev-parse",
                "--verify",
                f"{release_commit}^{{tree}}",
            ).lower()
            != expected_source_tree
            or _git(repository, "rev-parse", "--verify", f"{release_commit}^1").lower()
            != expected_base_commit
        ):
            raise PromotionError("Docker release snapshot commit changed after promotion")
    except PromotionError as exc:
        if reference_update_attempted:
            try:
                _git(
                    repository,
                    "update-ref",
                    "--no-deref",
                    "-d",
                    reference,
                    release_commit,
                )
            except PromotionError as cleanup_error:
                raise PromotionError(
                    "Docker release promotion failed and its exact reference "
                    "could not be removed safely"
                ) from cleanup_error
        raise exc
    return {
        "base_commit": expected_base_commit,
        "source_tree": expected_source_tree,
        "review_commit": review_commit,
        "snapshot_commit": release_commit,
        "reference": reference,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-commit", required=True)
    parser.add_argument("--base-commit", required=True)
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--confirmation", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = promote_review_snapshot(
            root=Path.cwd(),
            review_commit=args.review_commit,
            expected_base_commit=args.base_commit,
            expected_source_tree=args.source_tree,
            reference=args.reference,
            confirmation=args.confirmation,
        )
    except (OSError, PromotionError) as exc:
        print(f"Docker review promotion refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
