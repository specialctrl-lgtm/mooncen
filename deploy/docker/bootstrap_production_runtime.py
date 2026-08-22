#!/usr/bin/python3
"""One-shot root bootstrap for the production container control plane.

The native deployment installs this helper and a root-owned digest of its
reviewed build-policy files.  It does not install or start the container
runtime.  A later fixed sudo command supplies only the trusted an2p target
identity on standard input; this helper snapshots the pinned sources into a
private root directory before invoking the installer.
"""

from __future__ import annotations

import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence


SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

from production_runtime_integrity import (  # noqa: E402
    BUILD_POLICY_PATHS,
    RuntimeIntegrityError,
    build_policy_digest,
    load_bootstrap_config,
)


TARGET_IDENTITY_PATTERN = re.compile(rb"\A[0-9a-f]{64}\n\Z")
BOOTSTRAP_STAGE_ROOT = Path("/var/lib/mooncen-container-bootstrap")
INGRESS_ROOT = Path("/var/lib/mooncen-container-ingress")
NODE_ROLE_FILE = Path("/etc/mooncen-node-role")
MAX_POLICY_FILE_BYTES = 64 * 1024 * 1024


class BootstrapError(RuntimeError):
    """Raised when the fixed production bootstrap contract is not satisfied."""


def _require_root() -> None:
    if os.geteuid() != 0:
        raise BootstrapError("bootstrap must run as root")


def _target_identity(stream: object) -> str:
    reader = getattr(stream, "read", None)
    if reader is None:
        raise BootstrapError("target identity input is unavailable")
    raw = reader(66)
    if not isinstance(raw, bytes) or TARGET_IDENTITY_PATTERN.fullmatch(raw) is None:
        raise BootstrapError("stdin must contain one lowercase 64-hex target identity line")
    return raw[:-1].decode("ascii")


def _require_private_root(path: Path, *, create: bool) -> Path:
    if create and not path.exists():
        path.mkdir(mode=0o700, parents=False)
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise BootstrapError(f"private bootstrap directory is unavailable: {path}") from exc
    if (
        path.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or resolved != path.absolute()
    ):
        raise BootstrapError(f"private bootstrap directory is unsafe: {path}")
    return resolved


def _require_primary_node() -> None:
    try:
        metadata = NODE_ROLE_FILE.lstat()
        role = NODE_ROLE_FILE.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise BootstrapError("production node role is unavailable") from exc
    if (
        NODE_ROLE_FILE.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or role != "primary"
    ):
        raise BootstrapError("container production bootstrap requires the primary node")


def _copy_regular_file(source: Path, destination: Path) -> None:
    read_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    write_flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    source_fd = destination_fd = -1
    total = 0
    try:
        source_fd = os.open(source, read_flags)
        metadata = os.fstat(source_fd)
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o022:
            raise BootstrapError(f"reviewed source file is unsafe: {source.name}")
        destination_fd = os.open(destination, write_flags, 0o600)
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_POLICY_FILE_BYTES:
                raise BootstrapError(f"reviewed source file is too large: {source.name}")
            view = memoryview(chunk)
            while view:
                written = os.write(destination_fd, view)
                view = view[written:]
        os.fsync(destination_fd)
    except OSError as exc:
        raise BootstrapError(f"reviewed source snapshot failed: {source.name}") from exc
    finally:
        if source_fd >= 0:
            os.close(source_fd)
        if destination_fd >= 0:
            os.close(destination_fd)


def _snapshot_policy(source_root: Path, expected_digest: str) -> Path:
    stage_parent = _require_private_root(BOOTSTRAP_STAGE_ROOT, create=True)
    stage = Path(tempfile.mkdtemp(prefix="runtime.", dir=stage_parent))
    os.chmod(stage, 0o700)
    try:
        for relative in BUILD_POLICY_PATHS:
            source = source_root / relative
            destination = stage / relative
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            _copy_regular_file(source, destination)
        if build_policy_digest(stage) != expected_digest:
            raise BootstrapError("root snapshot does not match the pinned build policy")
    except BaseException:
        shutil.rmtree(stage)
        raise
    return stage


def _ensure_ingress(config: dict[str, object]) -> None:
    deploy_uid = config["deploy_uid"]
    deploy_gid = config["deploy_gid"]
    assert isinstance(deploy_uid, int)
    assert isinstance(deploy_gid, int)
    if INGRESS_ROOT.exists() or INGRESS_ROOT.is_symlink():
        try:
            metadata = INGRESS_ROOT.lstat()
        except OSError as exc:
            raise BootstrapError("container ingress root cannot be inspected") from exc
        if (
            INGRESS_ROOT.is_symlink()
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != deploy_uid
            or metadata.st_gid != deploy_gid
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise BootstrapError("container ingress root is unsafe")
        return
    stage: Path | None = Path(
        tempfile.mkdtemp(prefix=".mooncen-container-ingress.", dir="/var/lib")
    )
    try:
        os.chown(stage, deploy_uid, deploy_gid)
        os.chmod(stage, 0o700)
        os.rename(stage, INGRESS_ROOT)
        stage = None
        parent_fd = os.open("/var/lib", os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        if stage is not None:
            stage.rmdir()


def bootstrap(target_identity: str) -> dict[str, object]:
    config = load_bootstrap_config()
    _require_primary_node()
    try:
        account = pwd.getpwnam(str(config["deploy_user"]))
    except KeyError as exc:
        raise BootstrapError("configured deploy account does not exist") from exc
    if account.pw_uid != config["deploy_uid"] or account.pw_gid != config["deploy_gid"]:
        raise BootstrapError("configured deploy account identity has changed")
    source_root = Path(str(config["source_root"]))
    expected_digest = str(config["build_policy_sha256"])
    stage = _snapshot_policy(source_root, expected_digest)
    try:
        result = subprocess.run(
            (
                "/usr/bin/bash",
                str(stage / "deploy/docker/install_production_runtime.sh"),
                "--an2p-target-identity",
                target_identity,
                "--expected-build-policy-sha256",
                expected_digest,
            ),
            cwd=stage,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            check=False,
        )
        if result.returncode != 0:
            raise BootstrapError("production runtime installer failed")
        _ensure_ingress(config)
    except (OSError, subprocess.SubprocessError) as exc:
        raise BootstrapError("production runtime installer could not run") from exc
    finally:
        shutil.rmtree(stage)
    return {"schema_version": 1, "installed": True, "build_policy_sha256": expected_digest}


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        if arguments:
            raise BootstrapError("bootstrap accepts no command-line arguments")
        _require_root()
        identity = _target_identity(sys.stdin.buffer)
        result = bootstrap(identity)
    except (BootstrapError, OSError, RuntimeIntegrityError) as exc:
        print(f"mooncen container bootstrap: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
