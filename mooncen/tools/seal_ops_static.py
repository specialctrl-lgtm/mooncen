#!/usr/bin/env python3
"""Seal the fixed Ops SPA inside one pending immutable an2p runtime pair."""

from __future__ import annotations

import json
import os
import re
import socket
import stat
import sys
from pathlib import Path
from typing import Sequence


CONTROL_ROOT = Path(__file__).resolve().parents[1]
if str(CONTROL_ROOT) not in sys.path:
    sys.path.insert(0, str(CONTROL_ROOT))

from backend.ops_static import OpsStaticError, create_ops_static_manifest  # noqa: E402


RUNTIME_RELEASES = Path("/opt/mooncen-an2p-runtime/releases")
PAIR_PATTERN = re.compile(
    r"\Aruntime-pair\.[0-9a-f]{40}\.[0-9a-f]{40}\.[0-9a-f]{64}\Z"
)
STAGING_TOKEN_PATTERN = re.compile(r"\A[0-9a-f]{32}\Z")


def _root_directory(path: Path, *, uid: int = 0, gid: int = 0) -> Path:
    metadata = path.lstat()
    resolved = path.resolve(strict=True)
    if (
        path.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != uid
        or metadata.st_gid != gid
        or stat.S_IMODE(metadata.st_mode) != 0o755
    ):
        raise OpsStaticError(f"immutable runtime directory is unsafe: {path}")
    return resolved


def seal(
    pair_name: str,
    *,
    staging_token: str | None = None,
    releases: Path = RUNTIME_RELEASES,
    trusted_uid: int = 0,
    trusted_gid: int = 0,
) -> dict[str, object]:
    if PAIR_PATTERN.fullmatch(pair_name) is None:
        raise OpsStaticError("runtime pair name is invalid")
    if staging_token is not None and STAGING_TOKEN_PATTERN.fullmatch(staging_token) is None:
        raise OpsStaticError("runtime pair staging token is invalid")
    trusted_releases = _root_directory(
        releases,
        uid=trusted_uid,
        gid=trusted_gid,
    )
    selected_name = pair_name if staging_token is None else f".stage.{staging_token}"
    pair = _root_directory(
        trusted_releases / selected_name,
        uid=trusted_uid,
        gid=trusted_gid,
    )
    if pair.parent != trusted_releases:
        raise OpsStaticError("runtime pair escaped its fixed release root")
    control = _root_directory(
        pair / "control",
        uid=trusted_uid,
        gid=trusted_gid,
    )
    if CONTROL_ROOT != control:
        raise OpsStaticError("static sealer is not executing from the selected control runtime")
    return create_ops_static_manifest(
        control / "ops-console-dist",
        trusted_uid=trusted_uid,
        trusted_gid=trusted_gid,
    )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if os.geteuid() != 0 or socket.gethostname().split(".", 1)[0].lower() != "an2p":
        raise OpsStaticError("run as root on an2p")
    if len(arguments) == 1:
        pair_name, staging_token = arguments[0], None
    elif len(arguments) == 3 and arguments[1] == "--staging-token":
        pair_name, staging_token = arguments[0], arguments[2]
    else:
        raise OpsStaticError(
            "usage: seal_ops_static.py <runtime-pair-name> "
            "[--staging-token <32-lowerhex>]"
        )
    result = seal(pair_name, staging_token=staging_token)
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
    except (OSError, OpsStaticError, ValueError) as exc:
        print(f"Ops static sealing failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
