#!/usr/bin/env python3
"""Copy one reviewed Docker bundle into the immutable worker release root."""

from __future__ import annotations

import fcntl
import grp
import json
import os
import re
import shutil
import socket
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploy.docker.release_manifest import (  # noqa: E402
    ManifestError,
    bind_promotion_evidence,
    load_json_evidence,
)
from deploy.docker.verify_release_bundle import (  # noqa: E402
    VerificationError,
    verify_release_artifacts,
)


SOURCE_ROOT = Path("/opt/mooncen-an2p-docker/evidence")
DESTINATION_ROOT = Path("/var/lib/mooncen-deployment-worker/releases")
SOURCE_GROUP = "mooncen_docker_operator"
DESTINATION_GROUP = "mooncen_deployment_worker"
EXPECTED_FILES = frozenset(
    {"compose.production.yaml", "images.tar", "release.json", "validation.json"}
)
SOURCE_TREE_PATTERN = re.compile(r"\A[0-9a-f]{40}\Z")
SHA256_PATTERN = re.compile(r"\A[0-9a-f]{64}\Z")
MAXIMUM_FILE_SIZES = {
    "compose.production.yaml": 1024 * 1024,
    "images.tar": 16 * 1024 * 1024 * 1024,
    "release.json": 256 * 1024,
    "validation.json": 256 * 1024,
}


class EvidenceHandoffError(RuntimeError):
    """Raised when immutable release handoff cannot be proven safe."""


def _directory(
    path: Path,
    *,
    uid: int,
    gid: int,
    mode: int,
) -> Path:
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise EvidenceHandoffError(f"required evidence directory is unavailable: {path}") from exc
    if (
        path.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != uid
        or metadata.st_gid != gid
        or stat.S_IMODE(metadata.st_mode) != mode
    ):
        raise EvidenceHandoffError(f"evidence directory metadata is unsafe: {path}")
    return resolved


def _file_metadata(path: Path, *, uid: int, gid: int, mode: int) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise EvidenceHandoffError(f"evidence file is unavailable: {path.name}") from exc
    maximum = MAXIMUM_FILE_SIZES.get(path.name, 0)
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != uid
        or metadata.st_gid != gid
        or stat.S_IMODE(metadata.st_mode) != mode
        or metadata.st_size <= 0
        or metadata.st_size > maximum
    ):
        raise EvidenceHandoffError(f"evidence file metadata is unsafe: {path.name}")
    return metadata


def _validate_release(
    directory: Path,
    source_tree: str,
    *,
    uid: int,
    gid: int,
    directory_mode: int,
    file_mode: int,
) -> dict[str, Any]:
    trusted = _directory(directory, uid=uid, gid=gid, mode=directory_mode)
    try:
        names = {entry.name for entry in trusted.iterdir()}
    except OSError as exc:
        raise EvidenceHandoffError("evidence directory cannot be listed") from exc
    if names != EXPECTED_FILES:
        raise EvidenceHandoffError("evidence directory file set is not exact")
    for name in EXPECTED_FILES:
        _file_metadata(trusted / name, uid=uid, gid=gid, mode=file_mode)
    try:
        verified = verify_release_artifacts(trusted)
        release = load_json_evidence(trusted / "release.json")
        receipt = load_json_evidence(trusted / "validation.json", receipt=True)
        bound = bind_promotion_evidence(
            release,
            receipt,
            now=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
    except (ManifestError, VerificationError, OSError) as exc:
        raise EvidenceHandoffError("release bundle or PASS receipt is invalid") from exc
    if (
        verified.get("source_tree") != source_tree
        or bound.release.get("source_tree") != source_tree
        or bound.receipt.get("source_tree") != source_tree
        or bound.receipt.get("target") != "an2p-dev"
        or bound.receipt.get("status") != "passed"
        or SHA256_PATTERN.fullmatch(str(bound.release.get("release_digest"))) is None
        or SHA256_PATTERN.fullmatch(str(bound.receipt.get("receipt_digest"))) is None
    ):
        raise EvidenceHandoffError("release evidence does not match the requested source tree")
    return {
        "release": bound.release,
        "receipt": bound.receipt,
    }


def _copy_pinned_file(
    source: Path,
    destination: Path,
    *,
    source_uid: int,
    source_gid: int,
    destination_uid: int,
    destination_gid: int,
) -> None:
    source_metadata = _file_metadata(source, uid=source_uid, gid=source_gid, mode=0o640)
    source_descriptor = -1
    destination_descriptor = -1
    try:
        source_descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        before = os.fstat(source_descriptor)
        destination_descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        written = 0
        while True:
            chunk = os.read(source_descriptor, 1024 * 1024)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                count = os.write(destination_descriptor, view)
                if count <= 0:
                    raise OSError("short evidence write")
                written += count
                view = view[count:]
        after = os.fstat(source_descriptor)
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_uid != after.st_uid
            or before.st_gid != after.st_gid
            or stat.S_IMODE(before.st_mode) != stat.S_IMODE(after.st_mode)
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
            or before.st_dev != source_metadata.st_dev
            or before.st_ino != source_metadata.st_ino
            or written != before.st_size
        ):
            raise EvidenceHandoffError("source evidence changed during immutable handoff")
        destination_metadata = os.fstat(destination_descriptor)
        if (
            destination_metadata.st_uid != destination_uid
            or destination_metadata.st_gid != destination_gid
        ):
            os.fchown(destination_descriptor, destination_uid, destination_gid)
        os.fchmod(destination_descriptor, 0o640)
        os.fsync(destination_descriptor)
    except OSError as exc:
        raise EvidenceHandoffError(f"evidence file copy failed: {source.name}") from exc
    finally:
        if destination_descriptor >= 0:
            os.close(destination_descriptor)
        if source_descriptor >= 0:
            os.close(source_descriptor)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _same_evidence(source: dict[str, Any], destination: dict[str, Any]) -> bool:
    return bool(
        source["release"] == destination["release"]
        and source["receipt"] == destination["receipt"]
    )


def handoff(
    source_tree: str,
    *,
    source_root: Path = SOURCE_ROOT,
    destination_root: Path = DESTINATION_ROOT,
    source_uid: int = 0,
    source_gid: int | None = None,
    destination_uid: int = 0,
    destination_gid: int | None = None,
) -> dict[str, Any]:
    if SOURCE_TREE_PATTERN.fullmatch(source_tree) is None:
        raise EvidenceHandoffError("source tree must be exactly 40 lowercase hexadecimal characters")
    try:
        resolved_source_gid = (
            grp.getgrnam(SOURCE_GROUP).gr_gid if source_gid is None else source_gid
        )
        resolved_destination_gid = (
            grp.getgrnam(DESTINATION_GROUP).gr_gid
            if destination_gid is None
            else destination_gid
        )
    except KeyError as exc:
        raise EvidenceHandoffError("required isolated service group is unavailable") from exc
    trusted_source_root = _directory(
        source_root,
        uid=source_uid,
        gid=resolved_source_gid,
        mode=0o750,
    )
    trusted_destination_root = _directory(
        destination_root,
        uid=destination_uid,
        gid=resolved_destination_gid,
        mode=0o750,
    )
    source = trusted_source_root / source_tree
    destination = trusted_destination_root / source_tree
    source_evidence = _validate_release(
        source,
        source_tree,
        uid=source_uid,
        gid=resolved_source_gid,
        directory_mode=0o750,
        file_mode=0o640,
    )

    lock_path = trusted_destination_root / ".handoff.lock"
    lock_descriptor = os.open(
        lock_path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    stage: Path | None = None
    try:
        lock_metadata = os.fstat(lock_descriptor)
        if (
            not stat.S_ISREG(lock_metadata.st_mode)
            or lock_metadata.st_nlink != 1
            or lock_metadata.st_uid != destination_uid
            or stat.S_IMODE(lock_metadata.st_mode) != 0o600
        ):
            raise EvidenceHandoffError("evidence handoff lock is unsafe")
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
        if destination.exists() or destination.is_symlink():
            destination_evidence = _validate_release(
                destination,
                source_tree,
                uid=destination_uid,
                gid=resolved_destination_gid,
                directory_mode=0o750,
                file_mode=0o640,
            )
            if not _same_evidence(source_evidence, destination_evidence):
                raise EvidenceHandoffError("existing worker evidence differs from reviewed source")
            return _result(source_tree, source_evidence, installed=False, idempotent=True)

        stale_prefix = f".handoff-{source_tree}-"
        for candidate in trusted_destination_root.iterdir():
            if not candidate.name.startswith(stale_prefix):
                continue
            metadata = candidate.lstat()
            if (
                candidate.is_symlink()
                or not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != destination_uid
                or candidate.resolve(strict=True).parent != trusted_destination_root
            ):
                raise EvidenceHandoffError("stale evidence handoff path is unsafe")
            shutil.rmtree(candidate)

        stage = Path(
            tempfile.mkdtemp(prefix=stale_prefix, dir=trusted_destination_root)
        )
        for name in sorted(EXPECTED_FILES):
            _copy_pinned_file(
                source / name,
                stage / name,
                source_uid=source_uid,
                source_gid=resolved_source_gid,
                destination_uid=destination_uid,
                destination_gid=resolved_destination_gid,
            )
        if (stage.stat().st_uid, stage.stat().st_gid) != (
            destination_uid,
            resolved_destination_gid,
        ):
            os.chown(stage, destination_uid, resolved_destination_gid)
        os.chmod(stage, 0o750)
        _validate_release(
            stage,
            source_tree,
            uid=destination_uid,
            gid=resolved_destination_gid,
            directory_mode=0o750,
            file_mode=0o640,
        )
        _fsync_directory(stage)
        if destination.exists() or destination.is_symlink():
            raise EvidenceHandoffError("worker evidence destination appeared during handoff")
        os.rename(stage, destination)
        stage = None
        _fsync_directory(trusted_destination_root)
        installed_evidence = _validate_release(
            destination,
            source_tree,
            uid=destination_uid,
            gid=resolved_destination_gid,
            directory_mode=0o750,
            file_mode=0o640,
        )
        if not _same_evidence(source_evidence, installed_evidence):
            raise EvidenceHandoffError("installed worker evidence differs from reviewed source")
        return _result(source_tree, source_evidence, installed=True, idempotent=False)
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
        os.close(lock_descriptor)


def _result(
    source_tree: str,
    evidence: dict[str, Any],
    *,
    installed: bool,
    idempotent: bool,
) -> dict[str, Any]:
    return {
        "idempotent": idempotent,
        "installed": installed,
        "receipt_digest": evidence["receipt"]["receipt_digest"],
        "release_digest": evidence["release"]["release_digest"],
        "schema_version": 1,
        "source_tree": source_tree,
    }


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if os.geteuid() != 0 or socket.gethostname().split(".", 1)[0].lower() != "an2p":
        raise EvidenceHandoffError("run as root on an2p")
    if len(arguments) != 1:
        raise EvidenceHandoffError("usage: container_evidence_handoff.py <40hex-source-tree>")
    result = handoff(arguments[0])
    print(
        json.dumps(
            result,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (EvidenceHandoffError, OSError, ValueError) as exc:
        print(f"container evidence handoff failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
