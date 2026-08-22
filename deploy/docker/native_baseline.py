#!/usr/bin/env python3
"""Hash the immutable native application inventory with bounded traversal."""

from __future__ import annotations

import argparse
import hashlib
import os
import stat
from pathlib import Path
from typing import Sequence


MAX_FILES = 200_000
MAX_FILE_BYTES = 2 * 1024**3
MAX_TOTAL_BYTES = 16 * 1024**3
EXCLUDED_DIRECTORY_NAMES = frozenset({".git", ".mypy_cache", ".pytest_cache"})
EXCLUDED_ROOT_DIRECTORIES = frozenset({"logs"})
EXCLUDED_PATHS = frozenset(
    {
        ".deploy-info",
        ".an2p-runtime-receipt.json",
        ".pair-receipt.json",
        ".mooncen-prebuilt-release",
        "failover/cloudflare_gate.log",
        "failover/cloudflared_role_guard.log",
        "frontend2/dist/sitemap.xml",
        "frontend2/public/sitemap.xml",
    }
)


class NativeBaselineError(RuntimeError):
    """Raised when the native inventory cannot be hashed safely."""


def _excluded(relative: str, *, directory: bool) -> bool:
    parts = Path(relative).parts
    if not parts:
        return False
    if parts[0] in EXCLUDED_ROOT_DIRECTORIES:
        return True
    if any(part in EXCLUDED_DIRECTORY_NAMES for part in parts[:-1] if directory is False):
        return True
    if directory and any(part in EXCLUDED_DIRECTORY_NAMES for part in parts):
        return True
    return relative in EXCLUDED_PATHS


def inventory_sha256(root: Path) -> str:
    """Return a path/mode/content digest for immutable native runtime inputs."""

    try:
        root_metadata = root.lstat()
    except OSError as exc:
        raise NativeBaselineError("native inventory root is unavailable") from exc
    if root.is_symlink() or not stat.S_ISDIR(root_metadata.st_mode):
        raise NativeBaselineError("native inventory root is unsafe")
    digest = hashlib.sha256()
    count = 0
    total_bytes = 0
    for directory, directory_names, file_names in os.walk(root, topdown=True, followlinks=False):
        directory_path = Path(directory)
        relative_directory = directory_path.relative_to(root).as_posix()
        if relative_directory == ".":
            relative_directory = ""
        try:
            directory_metadata = directory_path.lstat()
        except OSError as exc:
            raise NativeBaselineError("native inventory directory changed while walking") from exc
        if directory_path.is_symlink() or not stat.S_ISDIR(directory_metadata.st_mode):
            raise NativeBaselineError("native inventory directory is unsafe")
        count += 1
        if count > MAX_FILES:
            raise NativeBaselineError("native inventory exceeds the entry limit")
        digest.update(
            (
                f"D\0{relative_directory or '.'}\0{directory_metadata.st_uid}\0"
                f"{directory_metadata.st_gid}\0{stat.S_IMODE(directory_metadata.st_mode):04o}\n"
            ).encode("utf-8")
        )
        directory_names[:] = sorted(
            name
            for name in directory_names
            if not _excluded(
                f"{relative_directory}/{name}".lstrip("/"),
                directory=True,
            )
        )
        file_names.sort()
        for name in tuple(directory_names):
            candidate = directory_path / name
            if candidate.is_symlink():
                directory_names.remove(name)
                file_names.append(name)
        file_names.sort()
        for name in file_names:
            candidate = directory_path / name
            relative = candidate.relative_to(root).as_posix()
            if _excluded(relative, directory=False):
                continue
            try:
                metadata = candidate.lstat()
            except OSError as exc:
                raise NativeBaselineError("native inventory changed while walking") from exc
            count += 1
            if count > MAX_FILES:
                raise NativeBaselineError("native inventory exceeds the entry limit")
            if stat.S_ISLNK(metadata.st_mode):
                try:
                    target = os.readlink(candidate)
                except OSError as exc:
                    raise NativeBaselineError("native inventory symlink cannot be read") from exc
                digest.update(
                    (
                        f"L\0{relative}\0{metadata.st_uid}\0{metadata.st_gid}\0"
                        f"{stat.S_IMODE(metadata.st_mode):04o}\0{target}\n"
                    ).encode("utf-8")
                )
                continue
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_FILE_BYTES:
                raise NativeBaselineError("native inventory contains an unsafe entry")
            file_digest = hashlib.sha256()
            try:
                descriptor = os.open(candidate, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                try:
                    opened = os.fstat(descriptor)
                    while True:
                        chunk = os.read(descriptor, 1024 * 1024)
                        if not chunk:
                            break
                        total_bytes += len(chunk)
                        if total_bytes > MAX_TOTAL_BYTES:
                            raise NativeBaselineError("native inventory exceeds the byte limit")
                        file_digest.update(chunk)
                    closed = os.fstat(descriptor)
                finally:
                    os.close(descriptor)
            except OSError as exc:
                raise NativeBaselineError("native inventory file cannot be hashed") from exc
            if (
                opened.st_ino != metadata.st_ino
                or opened.st_dev != metadata.st_dev
                or opened.st_size != metadata.st_size
                or opened.st_uid != metadata.st_uid
                or opened.st_gid != metadata.st_gid
                or stat.S_IMODE(opened.st_mode) != stat.S_IMODE(metadata.st_mode)
                or opened.st_mtime_ns != metadata.st_mtime_ns
                or opened.st_ctime_ns != metadata.st_ctime_ns
                or closed.st_size != metadata.st_size
                or closed.st_uid != metadata.st_uid
                or closed.st_gid != metadata.st_gid
                or stat.S_IMODE(closed.st_mode) != stat.S_IMODE(metadata.st_mode)
                or closed.st_mtime_ns != metadata.st_mtime_ns
                or closed.st_ctime_ns != metadata.st_ctime_ns
            ):
                raise NativeBaselineError("native inventory file changed while hashing")
            digest.update(
                (
                    f"F\0{relative}\0{metadata.st_uid}\0{metadata.st_gid}\0"
                    f"{stat.S_IMODE(metadata.st_mode):04o}\0"
                    f"{metadata.st_size}\0{file_digest.hexdigest()}\n"
                ).encode("utf-8")
            )
    return digest.hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    arguments = parser.parse_args(argv)
    root = Path(arguments.root)
    if not root.is_absolute():
        parser.error("--root must be absolute")
    try:
        result = inventory_sha256(root)
    except (NativeBaselineError, OSError) as exc:
        parser.exit(1, f"mooncen native baseline: {exc}\n")
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
