#!/usr/bin/env python3
"""Fail closed unless the an2p Docker EnvironmentFile is private and local."""

from __future__ import annotations

import argparse
import grp
import os
import re
import stat
import sys
from pathlib import Path


RUNTIME_TARGET_PATTERN = re.compile(
    r"\Adocker-release-runtime\.[0-9a-f]{40}\.[0-9a-f]{64}\."
    r"[0-9a-f]{64}\.[A-Za-z0-9]{8}\Z"
)
SYSTEM_PAIR_PATTERN = re.compile(
    r"\Aruntime-pair\.[0-9a-f]{40}\.[0-9a-f]{40}\.[0-9a-f]{64}\Z"
)
SYSTEM_DOCKER_ALIAS = Path("/opt/mooncen-an2p-docker/current")
SYSTEM_PAIR_CURRENT = Path("/opt/mooncen-an2p-runtime/current")


def _trusted_environment_directory(directory: Path) -> os.stat_result:
    uid = os.getuid()
    try:
        metadata = directory.lstat()
    except OSError as exc:
        raise ValueError("Docker environment directory is missing or unreadable.") from exc
    if stat.S_ISLNK(metadata.st_mode):
        try:
            target = os.readlink(directory)
            parent_metadata = directory.parent.lstat()
            resolved_parent = directory.parent.resolve(strict=True)
            resolved = directory.resolve(strict=True)
            resolved_metadata = resolved.lstat()
        except OSError as exc:
            raise ValueError("Docker runtime pointer is unreadable.") from exc
        if (
            directory.name != "docker-release-runtime"
            or "/" in target
            or RUNTIME_TARGET_PATTERN.fullmatch(target) is None
            or directory.parent.is_symlink()
            or not stat.S_ISDIR(parent_metadata.st_mode)
            or parent_metadata.st_uid != uid
            or stat.S_IMODE(parent_metadata.st_mode) != 0o700
            or resolved.parent != resolved_parent
            or resolved.is_symlink()
            or not stat.S_ISDIR(resolved_metadata.st_mode)
            or resolved_metadata.st_uid != uid
            or stat.S_IMODE(resolved_metadata.st_mode) != 0o700
        ):
            raise ValueError("Docker runtime pointer is unsafe.")
        return resolved_metadata
    return metadata


def validate_environment_file(path: Path) -> None:
    try:
        directory_status = _trusted_environment_directory(path.parent)
        file_status = path.lstat()
    except OSError as exc:
        raise ValueError("Docker environment path is missing or unreadable.") from exc
    uid = os.getuid()
    if (
        not stat.S_ISDIR(directory_status.st_mode)
        or directory_status.st_uid != uid
        or stat.S_IMODE(directory_status.st_mode) != 0o700
    ):
        raise ValueError("Docker environment directory must be user-owned mode 0700.")
    if not stat.S_ISREG(file_status.st_mode) or file_status.st_uid != uid or stat.S_IMODE(file_status.st_mode) != 0o600:
        raise ValueError("Docker environment file must be user-owned mode 0600.")


def validate_system_environment_file(path: Path, *, reader_group: str) -> None:
    """Validate the immutable root-installed system-service environment."""

    try:
        group_id = grp.getgrnam(reader_group).gr_gid
        directory_status = path.parent.lstat()
        resolved_directory = path.parent.resolve(strict=True)
        file_status = path.lstat()
    except (KeyError, OSError) as exc:
        raise ValueError("system Docker environment path is unavailable.") from exc
    direct_directory = bool(
        not path.parent.is_symlink()
        and stat.S_ISDIR(directory_status.st_mode)
        and directory_status.st_uid == 0
        and directory_status.st_gid == group_id
        and stat.S_IMODE(directory_status.st_mode) == 0o750
    )
    alias_directory = False
    if path.parent == SYSTEM_DOCKER_ALIAS and path.parent.is_symlink():
        try:
            alias_status = SYSTEM_DOCKER_ALIAS.lstat()
            pair_pointer_status = SYSTEM_PAIR_CURRENT.lstat()
            pair_target = os.readlink(SYSTEM_PAIR_CURRENT)
            pair = SYSTEM_PAIR_CURRENT.parent / pair_target
            pair_status = pair.lstat()
            resolved_pair = pair.resolve(strict=True)
        except OSError as exc:
            raise ValueError("system Docker runtime pointer is unavailable.") from exc
        alias_directory = bool(
            alias_status.st_uid == 0
            and alias_status.st_gid == 0
            and os.readlink(SYSTEM_DOCKER_ALIAS)
            == "../mooncen-an2p-runtime/current/docker"
            and stat.S_ISLNK(pair_pointer_status.st_mode)
            and pair_pointer_status.st_uid == 0
            and pair_pointer_status.st_gid == 0
            and pair_target.startswith("releases/")
            and SYSTEM_PAIR_PATTERN.fullmatch(pair_target.removeprefix("releases/"))
            is not None
            and not pair.is_symlink()
            and stat.S_ISDIR(pair_status.st_mode)
            and pair_status.st_uid == 0
            and pair_status.st_gid == 0
            and stat.S_IMODE(pair_status.st_mode) == 0o755
            and resolved_directory == resolved_pair / "docker"
        )
    if not (direct_directory or alias_directory):
        raise ValueError(
            "system Docker runtime directory must be root-owned, reader-group mode 0750."
        )
    resolved_status = resolved_directory.lstat()
    if (
        not stat.S_ISDIR(resolved_status.st_mode)
        or resolved_status.st_uid != 0
        or resolved_status.st_gid != group_id
        or stat.S_IMODE(resolved_status.st_mode) != 0o750
    ):
        raise ValueError("resolved system Docker runtime directory is unsafe.")
    if (
        path.is_symlink()
        or not stat.S_ISREG(file_status.st_mode)
        or file_status.st_uid != 0
        or file_status.st_gid != group_id
        or stat.S_IMODE(file_status.st_mode) != 0o640
    ):
        raise ValueError(
            "system Docker environment must be root-owned, reader-group mode 0640."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--system-runtime", action="store_true")
    parser.add_argument("--reader-group", default="mooncen_docker_operator")
    args = parser.parse_args()
    try:
        if args.system_runtime:
            validate_system_environment_file(
                args.path,
                reader_group=args.reader_group,
            )
        else:
            validate_environment_file(args.path)
    except ValueError as exc:
        print(f"Docker environment validation failed: {exc}", file=sys.stderr)
        return 78
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
