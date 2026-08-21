#!/usr/bin/env python3
"""Exact, new-only stdin ingress for reviewed container release files."""

from __future__ import annotations

import hashlib
import json
import os
import pwd
import re
import stat
import sys
from pathlib import Path
from typing import BinaryIO, Mapping, Sequence


DEPLOY_USER = "mooncen_container_deploy"
INGRESS_ROOT = Path("/var/lib/mooncen-container-ingress")
SOURCE_TREE = re.compile(r"\A[0-9a-f]{40}\Z")
DIGEST = re.compile(r"\A[0-9a-f]{64}\Z")
DECIMAL_SIZE = re.compile(r"\A(?:0|[1-9][0-9]{0,9})\Z")
FILE_LIMITS: Mapping[str, int] = {
    "compose.production.yaml": 1024 * 1024,
    "images.tar": 8 * 1024 * 1024 * 1024,
    "release.json": 256 * 1024,
    "validation.json": 256 * 1024,
}
CHUNK_SIZE = 1024 * 1024


class IngressError(RuntimeError):
    """Raised when ingress metadata, bytes, or filesystem state is unsafe."""


def _canonical(value: Mapping[str, object]) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"


def _validate_tree(value: str) -> str:
    if SOURCE_TREE.fullmatch(value) is None:
        raise IngressError("source tree is invalid")
    return value


def _validate_directory(
    descriptor: int,
    *,
    expected_uid: int,
    expected_gid: int,
    label: str,
) -> None:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != expected_uid
        or metadata.st_gid != expected_gid
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise IngressError(f"{label} directory metadata is unsafe")


def _open_root(root: Path, *, expected_uid: int, expected_gid: int) -> int:
    try:
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise IngressError("ingress root is unavailable") from exc
    try:
        _validate_directory(
            descriptor,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
            label="ingress root",
        )
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def prepare(
    tree: str,
    *,
    root: Path = INGRESS_ROOT,
    expected_uid: int,
    expected_gid: int | None = None,
) -> dict[str, object]:
    trusted_tree = _validate_tree(tree)
    trusted_gid = expected_uid if expected_gid is None else expected_gid
    root_fd = _open_root(root, expected_uid=expected_uid, expected_gid=trusted_gid)
    try:
        try:
            os.mkdir(trusted_tree, mode=0o700, dir_fd=root_fd)
        except FileExistsError as exc:
            raise IngressError("ingress release directory already exists") from exc
        tree_fd = os.open(
            trusted_tree,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=root_fd,
        )
        try:
            _validate_directory(
                tree_fd,
                expected_uid=expected_uid,
                expected_gid=trusted_gid,
                label="release",
            )
        finally:
            os.close(tree_fd)
        os.fsync(root_fd)
    finally:
        os.close(root_fd)
    return {"prepared": True, "schema_version": 1, "source_tree": trusted_tree}


def upload(
    tree: str,
    name: str,
    size_text: str,
    expected_sha256: str,
    stream: BinaryIO,
    *,
    root: Path = INGRESS_ROOT,
    expected_uid: int,
    expected_gid: int | None = None,
) -> dict[str, object]:
    trusted_tree = _validate_tree(tree)
    if name not in FILE_LIMITS:
        raise IngressError("ingress filename is not canonical")
    if DECIMAL_SIZE.fullmatch(size_text) is None:
        raise IngressError("ingress size is not canonical decimal")
    expected_size = int(size_text)
    if expected_size <= 0 or expected_size > FILE_LIMITS[name]:
        raise IngressError("ingress size is outside the file limit")
    if DIGEST.fullmatch(expected_sha256) is None:
        raise IngressError("ingress digest is invalid")

    trusted_gid = expected_uid if expected_gid is None else expected_gid
    root_fd = _open_root(root, expected_uid=expected_uid, expected_gid=trusted_gid)
    tree_fd = -1
    output_fd = -1
    created = False
    try:
        try:
            tree_fd = os.open(
                trusted_tree,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=root_fd,
            )
        except OSError as exc:
            raise IngressError("ingress release directory is unavailable") from exc
        _validate_directory(
            tree_fd,
            expected_uid=expected_uid,
            expected_gid=trusted_gid,
            label="release",
        )
        try:
            output_fd = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=tree_fd,
            )
            created = True
        except OSError as exc:
            raise IngressError("ingress destination is not new-only") from exc
        metadata = os.fstat(output_fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != expected_uid
            or metadata.st_gid != trusted_gid
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise IngressError("new ingress file metadata is unsafe")

        digest = hashlib.sha256()
        written = 0
        while written < expected_size:
            chunk = stream.read(min(CHUNK_SIZE, expected_size - written))
            if not chunk:
                raise IngressError("ingress stream ended before the declared size")
            if not isinstance(chunk, bytes):
                raise IngressError("ingress stream is not binary")
            view = memoryview(chunk)
            while view:
                count = os.write(output_fd, view)
                if count <= 0:
                    raise IngressError("ingress write did not progress")
                view = view[count:]
            digest.update(chunk)
            written += len(chunk)
        if stream.read(1):
            raise IngressError("ingress stream exceeds the declared size")
        observed_sha256 = digest.hexdigest()
        if observed_sha256 != expected_sha256:
            raise IngressError("ingress digest does not match")
        os.fsync(output_fd)
        os.close(output_fd)
        output_fd = -1
        os.fsync(tree_fd)
    except Exception:
        if output_fd >= 0:
            os.close(output_fd)
            output_fd = -1
        if created and tree_fd >= 0:
            try:
                os.unlink(name, dir_fd=tree_fd)
                os.fsync(tree_fd)
            except OSError as cleanup_error:
                raise IngressError("partial ingress cleanup failed") from cleanup_error
        raise
    finally:
        if output_fd >= 0:
            os.close(output_fd)
        if tree_fd >= 0:
            os.close(tree_fd)
        os.close(root_fd)
    return {
        "name": name,
        "schema_version": 1,
        "sha256": expected_sha256,
        "size": expected_size,
        "source_tree": trusted_tree,
        "uploaded": True,
    }


def abort(
    tree: str,
    *,
    root: Path = INGRESS_ROOT,
    expected_uid: int,
    expected_gid: int | None = None,
) -> dict[str, object]:
    trusted_tree = _validate_tree(tree)
    trusted_gid = expected_uid if expected_gid is None else expected_gid
    root_fd = _open_root(root, expected_uid=expected_uid, expected_gid=trusted_gid)
    tree_fd = -1
    try:
        try:
            tree_fd = os.open(
                trusted_tree,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=root_fd,
            )
        except FileNotFoundError:
            return {
                "aborted": True,
                "schema_version": 1,
                "source_tree": trusted_tree,
            }
        except OSError as exc:
            raise IngressError("ingress release directory is unsafe") from exc
        _validate_directory(
            tree_fd,
            expected_uid=expected_uid,
            expected_gid=trusted_gid,
            label="release",
        )
        entries = sorted(os.listdir(tree_fd))
        if any(name not in FILE_LIMITS for name in entries):
            raise IngressError("ingress release contains an unknown entry")
        for name in entries:
            metadata = os.stat(name, dir_fd=tree_fd, follow_symlinks=False)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != expected_uid
                or metadata.st_gid != trusted_gid
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise IngressError("ingress cleanup target is unsafe")
            os.unlink(name, dir_fd=tree_fd)
        os.fsync(tree_fd)
        os.close(tree_fd)
        tree_fd = -1
        os.rmdir(trusted_tree, dir_fd=root_fd)
        os.fsync(root_fd)
    finally:
        if tree_fd >= 0:
            os.close(tree_fd)
        os.close(root_fd)
    return {"aborted": True, "schema_version": 1, "source_tree": trusted_tree}


def main(argv: Sequence[str] | None = None) -> int:
    os.umask(0o077)
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    account = pwd.getpwuid(os.getuid())
    if account.pw_name != DEPLOY_USER or account.pw_uid == 0:
        raise IngressError("ingress helper requires the dedicated deploy account")
    if len(arguments) == 2 and arguments[0] == "prepare":
        result = prepare(
            arguments[1],
            expected_uid=account.pw_uid,
            expected_gid=account.pw_gid,
        )
    elif len(arguments) == 5 and arguments[0] == "upload":
        result = upload(
            arguments[1],
            arguments[2],
            arguments[3],
            arguments[4],
            sys.stdin.buffer,
            expected_uid=account.pw_uid,
            expected_gid=account.pw_gid,
        )
    elif len(arguments) == 2 and arguments[0] == "abort":
        result = abort(
            arguments[1],
            expected_uid=account.pw_uid,
            expected_gid=account.pw_gid,
        )
    else:
        raise IngressError("ingress helper argv is invalid")
    sys.stdout.write(_canonical(result))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (IngressError, OSError) as exc:
        print(f"container ingress rejected: {exc}", file=sys.stderr)
        raise SystemExit(65) from None
