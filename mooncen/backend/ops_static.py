"""Serve a reviewed, root-owned Ops SPA from the Ops API origin."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi.responses import Response


CONTROL_CURRENT = Path("/opt/mooncen-an2p-control/current")
RUNTIME_CURRENT = Path("/opt/mooncen-an2p-runtime/current")
TRUSTED_UID = 0
TRUSTED_GID = 0
STATIC_RELATIVE_ROOT = Path("ops-console-dist")
MANIFEST_NAME = ".mooncen-ops-static.json"
OPS_STATIC_NODE_IMAGE = (
    "node:24.18.0-bookworm-slim@"
    "sha256:6f7b03f7c2c8e2e784dcf9295400527b9b1270fd37b7e9a7285cf83b6951452d"
)
OPS_STATIC_BUILD_CONTRACT = {
    "api_base": "same-origin",
    "base_path": "/",
    "csrf_cookie_name": "mooncen_ops_csrf",
    "node_image": OPS_STATIC_NODE_IMAGE,
    "npm_install": "npm ci --ignore-scripts --no-audit --fund=false",
}
CONTROL_ALIAS_TARGET = "../mooncen-an2p-runtime/current/control"
RUNTIME_TARGET_PATTERN = re.compile(
    r"\Areleases/runtime-pair\.[0-9a-f]{40}\.[0-9a-f]{40}\.[0-9a-f]{64}\Z"
)
SHA256_PATTERN = re.compile(r"\A[0-9a-f]{64}\Z")
HASHED_ASSET_PATTERN = re.compile(r"-[A-Za-z0-9_-]{8,}\.[A-Za-z0-9]+\Z")
MAX_STATIC_FILE_BYTES = 16 * 1024 * 1024
MAX_INDEX_BYTES = 2 * 1024 * 1024
MAX_STATIC_BUNDLE_BYTES = 64 * 1024 * 1024
MAX_STATIC_FILES = 2_000
CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".ico": "image/x-icon",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; "
    "form-action 'self'; script-src 'self'; style-src 'self'; style-src-elem 'self'; "
    "style-src-attr 'unsafe-inline'; img-src 'self' data:; font-src 'self'; "
    "connect-src 'self'"
)
REQUIRED_COMPILED_CANARIES = (
    b"mooncen_ops_csrf",
    b"/api/auth/ops/login",
    b"/api/ops",
)
FORBIDDEN_COMPILED_CANARIES = (
    b"mooncen_csrf",
    b"127.0.0.1:8001",
    b"127.0.0.1:8002",
    b"localhost:8001",
    b"localhost:8002",
)


class OpsStaticError(RuntimeError):
    """Raised when the immutable UI bundle or a request is unsafe."""


@dataclass(frozen=True)
class StaticFileRecord:
    path: str
    sha256: str
    size: int
    device: int
    inode: int
    uid: int
    gid: int
    mode: int
    mtime_ns: int
    ctime_ns: int
    content_type: str


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise OpsStaticError("Ops static manifest contains duplicate keys")
        value[key] = item
    return value


def _safe_relative_path(value: object) -> str:
    path = str(value or "")
    candidate = PurePosixPath(path)
    if (
        not path
        or len(path) > 240
        or path.startswith("/")
        or "\\" in path
        or any(ord(character) < 32 for character in path)
        or candidate.is_absolute()
        or any(part in {"", ".", ".."} for part in candidate.parts)
        or candidate.as_posix() != path
    ):
        raise OpsStaticError("Ops static manifest path is unsafe")
    suffix = candidate.suffix.lower()
    if suffix not in CONTENT_TYPES:
        raise OpsStaticError("Ops static manifest contains an unsupported file type")
    return path


def _directory_metadata(path: Path, *, uid: int, gid: int) -> Path:
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise OpsStaticError(f"Ops static directory is unavailable: {path}") from exc
    if (
        path.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != uid
        or metadata.st_gid != gid
        or stat.S_IMODE(metadata.st_mode) != 0o755
    ):
        raise OpsStaticError(f"Ops static directory metadata is unsafe: {path}")
    return resolved


def _read_pinned_file(
    path: Path,
    *,
    uid: int,
    gid: int,
    maximum: int,
) -> tuple[bytes, os.stat_result]:
    descriptor = -1
    try:
        metadata = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != uid
            or metadata.st_gid != gid
            or stat.S_IMODE(metadata.st_mode) != 0o644
            or metadata.st_size <= 0
            or metadata.st_size > maximum
        ):
            raise OpsStaticError(f"Ops static file metadata is unsafe: {path.name}")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        before = os.fstat(descriptor)
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum - total + 1))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise OpsStaticError("Ops static file exceeds its size bound")
        after = os.fstat(descriptor)
    except OSError as exc:
        raise OpsStaticError(f"Ops static file cannot be read: {path.name}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_uid != after.st_uid
        or before.st_gid != after.st_gid
        or stat.S_IMODE(before.st_mode) != stat.S_IMODE(after.st_mode)
        or before.st_mtime_ns != after.st_mtime_ns
        or before.st_ctime_ns != after.st_ctime_ns
        or total != before.st_size
    ):
        raise OpsStaticError("Ops static file changed while it was being read")
    return b"".join(chunks), before


def _manifest(root: Path, *, uid: int, gid: int) -> dict[str, Any]:
    raw, _metadata = _read_pinned_file(
        root / MANIFEST_NAME,
        uid=uid,
        gid=gid,
        maximum=2 * 1024 * 1024,
    )
    try:
        text = raw.decode("ascii")
        value = json.loads(text, object_pairs_hook=_unique_object)
        canonical = (
            json.dumps(
                value,
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
    except (UnicodeError, ValueError) as exc:
        raise OpsStaticError("Ops static manifest is not canonical JSON") from exc
    if (
        text != canonical
        or not isinstance(value, dict)
        or frozenset(value) != {"build_contract", "files", "schema_version"}
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["build_contract"] != OPS_STATIC_BUILD_CONTRACT
        or not isinstance(value["files"], dict)
        or not 1 <= len(value["files"]) <= MAX_STATIC_FILES
    ):
        raise OpsStaticError("Ops static manifest schema is invalid")
    return value


def _verify_compiled_contract(contents: dict[str, bytes]) -> None:
    javascript = b"\n".join(
        value for name, value in sorted(contents.items()) if name.endswith(".js")
    )
    if not javascript or any(canary not in javascript for canary in REQUIRED_COMPILED_CANARIES):
        raise OpsStaticError("Ops static JavaScript is not bound to the reviewed API contract")
    combined = b"\n".join(value for _name, value in sorted(contents.items()))
    if any(canary in combined for canary in FORBIDDEN_COMPILED_CANARIES):
        raise OpsStaticError("Ops static bundle contains a legacy API or CSRF binding")


class OpsStaticBundle:
    def __init__(self, root: Path, files: dict[str, StaticFileRecord]) -> None:
        self.root = root
        self.files = files

    def _file_response(self, relative: str, *, head: bool) -> Response:
        record = self.files.get(relative)
        if record is None:
            raise OpsStaticError("Ops static file is not in the reviewed manifest")
        data, metadata = _read_pinned_file(
            self.root / relative,
            uid=record.uid,
            gid=record.gid,
            maximum=MAX_INDEX_BYTES if relative == "index.html" else MAX_STATIC_FILE_BYTES,
        )
        if (
            metadata.st_dev != record.device
            or metadata.st_ino != record.inode
            or metadata.st_size != record.size
            or stat.S_IMODE(metadata.st_mode) != record.mode
            or metadata.st_mtime_ns != record.mtime_ns
            or metadata.st_ctime_ns != record.ctime_ns
            or hashlib.sha256(data).hexdigest() != record.sha256
        ):
            raise OpsStaticError("Ops static file differs from its reviewed receipt")
        headers = {
            "Content-Security-Policy": CONTENT_SECURITY_POLICY,
            "Cross-Origin-Opener-Policy": "same-origin",
            "Cross-Origin-Resource-Policy": "same-origin",
            "Permissions-Policy": "camera=(), geolocation=(), microphone=()",
            "X-Frame-Options": "DENY",
        }
        if relative == "index.html":
            headers["Cache-Control"] = "no-store"
        elif HASHED_ASSET_PATTERN.search(PurePosixPath(relative).name):
            headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            headers["Cache-Control"] = "no-cache, no-transform"
        return Response(
            content=b"" if head else data,
            media_type=None,
            headers={**headers, "Content-Type": record.content_type},
        )

    def response(self, request_path: str, *, head: bool = False) -> Response:
        path = request_path.strip("/")
        if not path:
            return self._file_response("index.html", head=head)
        if path == "api" or path.startswith("api/"):
            return Response(
                content=b"" if head else b'{"detail":"Not Found"}',
                status_code=404,
                media_type="application/json",
                headers={"Cache-Control": "no-store"},
            )
        candidate = PurePosixPath(path)
        if (
            len(path) > 240
            or "\\" in path
            or any(ord(character) < 32 for character in path)
            or candidate.is_absolute()
            or any(part in {"", ".", ".."} for part in candidate.parts)
            or candidate.as_posix() != path
        ):
            return Response(status_code=404, headers={"Cache-Control": "no-store"})
        try:
            normalized = _safe_relative_path(path) if "." in candidate.name else path
        except OpsStaticError:
            return Response(status_code=404, headers={"Cache-Control": "no-store"})
        if normalized in self.files:
            return self._file_response(normalized, head=head)
        if "." in candidate.name:
            return Response(status_code=404, headers={"Cache-Control": "no-store"})
        return self._file_response("index.html", head=head)


def load_ops_static_bundle(
    root: Path,
    *,
    trusted_uid: int = 0,
    trusted_gid: int = 0,
) -> OpsStaticBundle:
    trusted_root = _directory_metadata(root, uid=trusted_uid, gid=trusted_gid)
    manifest = _manifest(trusted_root, uid=trusted_uid, gid=trusted_gid)
    declared = manifest["files"]
    expected_paths: set[str] = set()
    records: dict[str, StaticFileRecord] = {}
    contents: dict[str, bytes] = {}
    total_bytes = 0
    for raw_path, raw_record in declared.items():
        relative = _safe_relative_path(raw_path)
        if relative in expected_paths or not isinstance(raw_record, dict) or frozenset(raw_record) != {
            "sha256",
            "size",
        }:
            raise OpsStaticError("Ops static manifest file record is invalid")
        digest = str(raw_record.get("sha256") or "")
        size = raw_record.get("size")
        if (
            SHA256_PATTERN.fullmatch(digest) is None
            or type(size) is not int
            or size <= 0
            or size > (MAX_INDEX_BYTES if relative == "index.html" else MAX_STATIC_FILE_BYTES)
        ):
            raise OpsStaticError("Ops static manifest file bounds are invalid")
        candidate = trusted_root / relative
        resolved_parent = candidate.parent.resolve(strict=True)
        if resolved_parent == trusted_root or trusted_root in resolved_parent.parents:
            pass
        else:  # pragma: no cover - guarded by the normalized PurePosix path.
            raise OpsStaticError("Ops static file escapes the reviewed root")
        data, metadata = _read_pinned_file(
            candidate,
            uid=trusted_uid,
            gid=trusted_gid,
            maximum=MAX_INDEX_BYTES if relative == "index.html" else MAX_STATIC_FILE_BYTES,
        )
        if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
            raise OpsStaticError("Ops static file does not match its manifest digest")
        expected_paths.add(relative)
        contents[relative] = data
        total_bytes += size
        records[relative] = StaticFileRecord(
            path=relative,
            sha256=digest,
            size=size,
            device=metadata.st_dev,
            inode=metadata.st_ino,
            uid=metadata.st_uid,
            gid=metadata.st_gid,
            mode=stat.S_IMODE(metadata.st_mode),
            mtime_ns=metadata.st_mtime_ns,
            ctime_ns=metadata.st_ctime_ns,
            content_type=CONTENT_TYPES[PurePosixPath(relative).suffix.lower()],
        )
    if "index.html" not in records or total_bytes > MAX_STATIC_BUNDLE_BYTES:
        raise OpsStaticError("Ops static bundle is incomplete or oversized")
    _verify_compiled_contract(contents)

    actual_files: set[str] = set()
    for candidate in trusted_root.rglob("*"):
        relative = candidate.relative_to(trusted_root).as_posix()
        metadata = candidate.lstat()
        if candidate.is_symlink():
            raise OpsStaticError("Ops static bundle contains a symbolic link")
        if stat.S_ISDIR(metadata.st_mode):
            _directory_metadata(candidate, uid=trusted_uid, gid=trusted_gid)
        elif stat.S_ISREG(metadata.st_mode):
            actual_files.add(relative)
        else:
            raise OpsStaticError("Ops static bundle contains a special file")
    if actual_files != expected_paths | {MANIFEST_NAME}:
        raise OpsStaticError("Ops static bundle file set differs from its manifest")
    return OpsStaticBundle(trusted_root, records)


def create_ops_static_manifest(
    root: Path,
    *,
    trusted_uid: int = 0,
    trusted_gid: int = 0,
) -> dict[str, Any]:
    """Seal one root-owned Docker build output with an exact canonical receipt."""

    trusted_root = _directory_metadata(root, uid=trusted_uid, gid=trusted_gid)
    manifest_path = trusted_root / MANIFEST_NAME
    if manifest_path.exists() or manifest_path.is_symlink():
        raise OpsStaticError("Ops static manifest already exists")
    contents: dict[str, bytes] = {}
    files: dict[str, dict[str, Any]] = {}
    total_bytes = 0
    for candidate in trusted_root.rglob("*"):
        relative = candidate.relative_to(trusted_root).as_posix()
        metadata = candidate.lstat()
        if candidate.is_symlink():
            raise OpsStaticError("Ops static build output contains a symbolic link")
        if stat.S_ISDIR(metadata.st_mode):
            _directory_metadata(candidate, uid=trusted_uid, gid=trusted_gid)
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise OpsStaticError("Ops static build output contains a special file")
        normalized = _safe_relative_path(relative)
        if normalized in files or len(files) >= MAX_STATIC_FILES:
            raise OpsStaticError("Ops static build output file set is invalid")
        data, _pinned = _read_pinned_file(
            candidate,
            uid=trusted_uid,
            gid=trusted_gid,
            maximum=MAX_INDEX_BYTES if normalized == "index.html" else MAX_STATIC_FILE_BYTES,
        )
        total_bytes += len(data)
        if total_bytes > MAX_STATIC_BUNDLE_BYTES:
            raise OpsStaticError("Ops static build output is oversized")
        contents[normalized] = data
        files[normalized] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
        }
    if "index.html" not in files:
        raise OpsStaticError("Ops static build output has no index.html")
    _verify_compiled_contract(contents)
    manifest = {
        "build_contract": OPS_STATIC_BUILD_CONTRACT,
        "files": files,
        "schema_version": 1,
    }
    encoded = (
        json.dumps(
            manifest,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("ascii")
    descriptor = -1
    try:
        descriptor = os.open(
            manifest_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        metadata = os.fstat(descriptor)
        if metadata.st_uid != trusted_uid or metadata.st_gid != trusted_gid:
            if os.geteuid() != 0:
                raise OpsStaticError("Ops static manifest owner is not trusted")
            os.fchown(descriptor, trusted_uid, trusted_gid)
        os.fchmod(descriptor, 0o644)
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short Ops static manifest write")
            view = view[written:]
        os.fsync(descriptor)
    except (OSError, OpsStaticError) as exc:
        try:
            manifest_path.unlink()
        except OSError:
            pass
        if isinstance(exc, OpsStaticError):
            raise
        raise OpsStaticError("Ops static manifest could not be sealed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    directory_descriptor = os.open(trusted_root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)
    return {
        "file_count": len(files),
        "manifest_sha256": hashlib.sha256(encoded).hexdigest(),
        "schema_version": 1,
        "total_bytes": total_bytes,
    }


def load_fixed_ops_static_bundle() -> OpsStaticBundle:
    try:
        control_parent = _directory_metadata(
            CONTROL_CURRENT.parent,
            uid=TRUSTED_UID,
            gid=TRUSTED_GID,
        )
        runtime_parent = _directory_metadata(
            RUNTIME_CURRENT.parent,
            uid=TRUSTED_UID,
            gid=TRUSTED_GID,
        )
        control_link_metadata = CONTROL_CURRENT.lstat()
        control_relative = os.readlink(CONTROL_CURRENT)
        runtime_link_metadata = RUNTIME_CURRENT.lstat()
        runtime_relative = os.readlink(RUNTIME_CURRENT)
    except OSError as exc:
        raise OpsStaticError("Ops control runtime pointer is unavailable") from exc
    if (
        not stat.S_ISLNK(control_link_metadata.st_mode)
        or control_link_metadata.st_uid != TRUSTED_UID
        or control_link_metadata.st_gid != TRUSTED_GID
        or control_relative != CONTROL_ALIAS_TARGET
        or not stat.S_ISLNK(runtime_link_metadata.st_mode)
        or runtime_link_metadata.st_uid != TRUSTED_UID
        or runtime_link_metadata.st_gid != TRUSTED_GID
        or RUNTIME_TARGET_PATTERN.fullmatch(runtime_relative) is None
    ):
        raise OpsStaticError("Ops control runtime pointer is unsafe")
    releases = _directory_metadata(
        runtime_parent / "releases",
        uid=TRUSTED_UID,
        gid=TRUSTED_GID,
    )
    runtime_pair = (runtime_parent / runtime_relative).resolve(strict=True)
    if runtime_pair.parent != releases:
        raise OpsStaticError("Ops runtime pair pointer escapes its release root")
    _directory_metadata(runtime_pair, uid=TRUSTED_UID, gid=TRUSTED_GID)
    runtime = _directory_metadata(
        runtime_pair / "control",
        uid=TRUSTED_UID,
        gid=TRUSTED_GID,
    )
    if (
        control_parent / CONTROL_CURRENT.name != CONTROL_CURRENT
        or CONTROL_CURRENT.resolve(strict=True) != runtime
    ):
        raise OpsStaticError("Ops control alias does not resolve to the active runtime pair")
    return load_ops_static_bundle(
        runtime / STATIC_RELATIVE_ROOT,
        trusted_uid=TRUSTED_UID,
        trusted_gid=TRUSTED_GID,
    )
