"""Issue and verify the gen1db crawler-control backup attestation.

The attestation is deliberately local to the control host.  It is authenticated
with a root-only HMAC key and binds a recent, isolated restore test to the live
PostgreSQL cluster and TLS peer seen at issue time.  Verification repeats the
read-only live identity, TLS and schema probes before an installer may write to
the database.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import stat
import struct
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - the production command is Linux-only
    fcntl = None
try:
    import resource
except ImportError:  # pragma: no cover - the production command is Linux-only
    resource = None


FORMAT = "mooncen-crawler-control-backup-attestation-v1"
TOOL_NAME = "mooncen-crawler-control-backup-attestation"
TOOL_VERSION = "1.0.0"
AUTHENTICATION_ALGORITHM = "hmac-sha256"
SIGNING_DOMAIN = b"mooncen:crawler-control-backup-attestation:v1\0"
EXPECTED_HOST = "gen1db"
EXPECTED_PORT = 5432
EXPECTED_DATABASE = "mooncen_staging"
EXPECTED_SSLMODE = "verify-full"
DEFAULT_ATTESTATION_PATH = Path(
    "/etc/mooncen/crawler-control-backup-attestation.json"
)
DEFAULT_KEY_PATH = Path("/etc/mooncen/crawler-control-backup-attestation.key")
MAX_ATTESTATION_AGE_SECONDS = 86_400
MAX_ISSUANCE_DELAY_SECONDS = 900
MAX_RESTORE_DURATION_SECONDS = 14_400
MAX_CLOCK_SKEW_SECONDS = 300
MAX_JSON_BYTES = 256 * 1024
MAX_KEY_BYTES = 4096
MAX_SCHEMA_BYTES = 64 * 1024 * 1024
MAX_BACKUP_BYTES = 16 * 1024 * 1024 * 1024
MAX_SOURCE_DATABASE_BYTES = 16 * 1024 * 1024 * 1024
MAX_RETAINED_EVIDENCE_DIRECTORIES = 2
MAX_RETAINED_EVIDENCE_BYTES = 2 * MAX_BACKUP_BYTES
MIN_EVIDENCE_FREE_RESERVE_BYTES = 8 * 1024 * 1024 * 1024
MIN_POSTGRES_FREE_RESERVE_BYTES = 8 * 1024 * 1024 * 1024
MIN_HMAC_KEY_BYTES = 32
MAX_HMAC_KEY_BYTES = 64
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER_PATTERN = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
EXTENSION_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
SYSTEM_IDENTIFIER_PATTERN = re.compile(r"^[0-9]{10,24}$")
VERSION_NUM_PATTERN = re.compile(r"^[0-9]{5,8}$")
RESTORE_DATABASE_PATTERN = re.compile(r"^mooncen_restore_[a-z0-9_]{1,43}$")
EVIDENCE_DIRECTORY_PATTERN = re.compile(
    r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{16}$"
)
REQUIRED_OBJECTS = (
    "public.branches",
    "public.courses",
    "public.crawl_batches",
    "public.mooncen_schema_migrations",
    "public.ops_agents",
    "public.ops_jobs",
)
ISSUE_LOCK_DIRECTORY = Path("/run/mooncen-crawler-control-backup-attestation")
ISSUE_LOCK_PATH = ISSUE_LOCK_DIRECTORY / "issue.lock"
EVIDENCE_ROOT_TEXT = "/var/lib/mooncen-crawler-control-backup-attestation/evidence"
EVIDENCE_ROOT = Path(EVIDENCE_ROOT_TEXT)


class AttestationError(RuntimeError):
    """Raised when attestation evidence or trust material is invalid."""


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AttestationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _parse_json(data: bytes, label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                AttestationError(f"invalid JSON constant: {value}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AttestationError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(parsed, dict):
        raise AttestationError(f"{label} must be a JSON object")
    return parsed


def _require_exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        raise AttestationError(
            f"{label} keys differ from the contract "
            f"(missing={missing}, unexpected={unexpected})"
        )


def _require_string(value: Any, label: str, *, maximum: int = 255) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or any(ord(character) < 0x20 for character in value)
    ):
        raise AttestationError(f"{label} is not a bounded printable string")
    return value


def _require_uint(value: Any, label: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise AttestationError(f"{label} is outside its unsigned integer bound")
    return value


def _parse_timestamp(value: Any, label: str) -> dt.datetime:
    text = _require_string(value, label, maximum=20)
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", text):
        raise AttestationError(f"{label} must be a second-precision UTC timestamp")
    try:
        parsed = dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError as exc:
        raise AttestationError(f"{label} is not a valid UTC timestamp") from exc
    return parsed


def _format_timestamp(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _validate_parent_chain(path: Path) -> None:
    if not path.is_absolute():
        raise AttestationError("protected paths must be absolute")
    current = path.parent
    while current != current.parent:
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise AttestationError(f"protected parent is unavailable: {current}") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise AttestationError(f"protected parent is not a real directory: {current}")
        if os.name == "posix" and metadata.st_uid != 0:
            raise AttestationError(f"protected parent is not root-owned: {current}")
        if os.name == "posix" and metadata.st_mode & 0o022:
            raise AttestationError(f"protected parent is group/world writable: {current}")
        current = current.parent


def _read_regular_file(
    path: Path,
    *,
    label: str,
    maximum: int,
    owner_only: bool,
    require_root: bool = True,
) -> bytes:
    if os.name == "posix" and require_root:
        _validate_parent_chain(path)
    try:
        before = path.lstat()
    except OSError as exc:
        raise AttestationError(f"{label} is unavailable") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise AttestationError(f"{label} must be a regular non-symlink file")
    if before.st_size <= 0 or before.st_size > maximum:
        raise AttestationError(f"{label} size is outside its bound")
    if os.name == "posix":
        if require_root and before.st_uid != 0:
            raise AttestationError(f"{label} must be root-owned")
        unsafe_mask = 0o077 if owner_only else 0o022
        if stat.S_IMODE(before.st_mode) & unsafe_mask:
            protection = "owner-only" if owner_only else "not group/world writable"
            raise AttestationError(f"{label} must be {protection}")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise AttestationError(f"{label} could not be opened safely") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
            or opened.st_size != before.st_size
        ):
            raise AttestationError(f"{label} changed while it was opened")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise AttestationError(f"{label} exceeds its size bound")
        after = os.fstat(descriptor)
        if (
            after.st_size != total
            or after.st_mtime_ns != opened.st_mtime_ns
            or after.st_ctime_ns != opened.st_ctime_ns
        ):
            raise AttestationError(f"{label} changed while it was read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _sha256_file(
    path: Path,
    *,
    label: str,
    maximum: int,
    require_root: bool = True,
    owner_only: bool = False,
) -> tuple[str, int]:
    if os.name == "posix" and require_root:
        _validate_parent_chain(path)
    try:
        before = path.lstat()
    except OSError as exc:
        raise AttestationError(f"{label} is unavailable") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise AttestationError(f"{label} must be a regular non-symlink file")
    if before.st_size <= 0 or before.st_size > maximum:
        raise AttestationError(f"{label} size is outside its bound")
    unsafe_mode = 0o077 if owner_only else 0o022
    if os.name == "posix" and (
        (require_root and before.st_uid != 0) or before.st_mode & unsafe_mode
    ):
        raise AttestationError(f"{label} is not a protected root-owned file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise AttestationError(f"{label} could not be opened safely") from exc
    digest = hashlib.sha256()
    total = 0
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
            or opened.st_size != before.st_size
        ):
            raise AttestationError(f"{label} changed while it was opened")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                raise AttestationError(f"{label} exceeds its size bound")
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (
            after.st_size != total
            or after.st_mtime_ns != opened.st_mtime_ns
            or after.st_ctime_ns != opened.st_ctime_ns
        ):
            raise AttestationError(f"{label} changed while it was hashed")
    finally:
        os.close(descriptor)
    return digest.hexdigest(), total


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short write while persisting protected evidence")
        view = view[written:]


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_regular_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise AttestationError("protected evidence is not a regular file")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _load_key(path: Path, *, require_root: bool = True) -> bytes:
    key = _read_regular_file(
        path,
        label="attestation HMAC key",
        maximum=MAX_KEY_BYTES,
        owner_only=True,
        require_root=require_root,
    )
    if not MIN_HMAC_KEY_BYTES <= len(key) <= MAX_HMAC_KEY_BYTES:
        raise AttestationError("attestation HMAC key must contain 32 to 64 raw bytes")
    return key


def _key_id(key: bytes) -> str:
    return f"sha256:{hashlib.sha256(key).hexdigest()}"


def _signing_bytes(payload: dict[str, Any], key_id: str) -> bytes:
    return SIGNING_DOMAIN + _canonical_json(
        {
            "algorithm": AUTHENTICATION_ALGORITHM,
            "key_id": key_id,
            "payload": payload,
        }
    )


def _schema_digest(data: bytes) -> str:
    # PostgreSQL 17 adds a random, paired psql transport guard around an
    # otherwise deterministic schema dump.  Remove only an exact first/last
    # pair with the same token.  Internal lines and trailing whitespace may be
    # executable dollar-quoted function content and must remain byte-exact.
    lines = data.splitlines(keepends=True)
    if not lines:
        return hashlib.sha256(data).hexdigest()
    first = re.fullmatch(rb"\\restrict ([A-Za-z0-9]{16,128})(?:\r?\n)?", lines[0])
    last = re.fullmatch(rb"\\unrestrict ([A-Za-z0-9]{16,128})(?:\r?\n)?", lines[-1])
    if first is None and last is None:
        normalized = data
    elif first is None or last is None or first.group(1) != last.group(1):
        raise AttestationError("schema dump contains an invalid restrict guard pair")
    else:
        normalized = b"".join(lines[1:-1])
    return hashlib.sha256(normalized).hexdigest()


def _parse_environment(path: Path, *, require_root: bool = True) -> dict[str, str]:
    raw = _read_regular_file(
        path,
        label="schema database environment",
        maximum=128 * 1024,
        owner_only=True,
        require_root=require_root,
    )
    values: dict[str, str] = {}
    for number, raw_line in enumerate(raw.decode("utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Z][A-Z0-9_]*)=(\S.*)", line)
        if not match:
            raise AttestationError(f"invalid protected environment line {number}")
        name, value = match.groups()
        if name in values:
            raise AttestationError(f"duplicate protected environment key: {name}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[name] = value
    required = {
        "OPS_CRAWLER_SCHEMA_DB_HOST",
        "OPS_CRAWLER_SCHEMA_DB_PORT",
        "OPS_CRAWLER_SCHEMA_DB_NAME",
        "OPS_CRAWLER_SCHEMA_DB_USER",
        "OPS_CRAWLER_SCHEMA_DB_PASSWORD",
        "DB_SSLMODE",
        "DB_SSLROOTCERT",
    }
    if not required.issubset(values):
        raise AttestationError("schema database environment is incomplete")
    if (
        values["OPS_CRAWLER_SCHEMA_DB_HOST"] != EXPECTED_HOST
        or values["OPS_CRAWLER_SCHEMA_DB_PORT"] != str(EXPECTED_PORT)
        or values["OPS_CRAWLER_SCHEMA_DB_NAME"] != EXPECTED_DATABASE
        or values["DB_SSLMODE"] != EXPECTED_SSLMODE
    ):
        raise AttestationError(
            "attestation database environment must select "
            "gen1db:5432/mooncen_staging with verify-full TLS"
        )
    if not IDENTIFIER_PATTERN.fullmatch(values["OPS_CRAWLER_SCHEMA_DB_USER"]):
        raise AttestationError("schema database user is invalid")
    if not values["OPS_CRAWLER_SCHEMA_DB_PASSWORD"]:
        raise AttestationError("schema database password is empty")
    ca_path = Path(values["DB_SSLROOTCERT"])
    _read_regular_file(
        ca_path,
        label="database TLS root CA",
        maximum=1024 * 1024,
        owner_only=False,
        require_root=require_root,
    )
    return values


def _postgres_tls_fingerprint(host: str, port: int, ca_path: Path) -> str:
    context = ssl.create_default_context(cafile=str(ca_path))
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    try:
        with socket.create_connection((host, port), timeout=10) as raw_socket:
            raw_socket.sendall(struct.pack("!II", 8, 80877103))
            if raw_socket.recv(1) != b"S":
                raise AttestationError("PostgreSQL endpoint refused TLS")
            with context.wrap_socket(raw_socket, server_hostname=host) as tls_socket:
                certificate = tls_socket.getpeercert(binary_form=True)
    except (OSError, ssl.SSLError) as exc:
        raise AttestationError("verified PostgreSQL TLS identity probe failed") from exc
    if not certificate:
        raise AttestationError("PostgreSQL TLS peer did not provide a certificate")
    return hashlib.sha256(certificate).hexdigest()


def _pg_dump_path() -> Path:
    return _trusted_executable("pg_dump")


def _trusted_executable(name: str) -> Path:
    candidate = shutil.which(name, path="/usr/sbin:/usr/bin:/sbin:/bin")
    if not candidate:
        raise AttestationError(f"{name} is required")
    resolved = Path(candidate).resolve(strict=True)
    metadata = resolved.stat()
    if not stat.S_ISREG(metadata.st_mode):
        raise AttestationError(f"{name} does not resolve to a regular executable")
    if metadata.st_uid != 0 or metadata.st_mode & 0o022:
        raise AttestationError(f"{name} must resolve to a protected root-owned executable")
    return resolved


def _run_as_postgres(
    executable: Path,
    arguments: list[str],
    *,
    stdin: Any = subprocess.DEVNULL,
    stdout: Any = subprocess.PIPE,
    timeout: int = 300,
    file_size_limit: int | None = None,
) -> subprocess.CompletedProcess[bytes]:
    runuser = _trusted_executable("runuser")
    preexec_fn = None
    if file_size_limit is not None:
        if resource is None:
            raise AttestationError("RLIMIT_FSIZE is required for backup creation")

        def set_file_size_limit() -> None:
            resource.setrlimit(resource.RLIMIT_FSIZE, (file_size_limit, file_size_limit))

        preexec_fn = set_file_size_limit
    try:
        completed = subprocess.run(
            [str(runuser), "-u", "postgres", "--", str(executable), *arguments],
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
            stdin=stdin,
            stdout=stdout,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            preexec_fn=preexec_fn,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AttestationError(f"{executable.name} did not complete") from exc
    if completed.returncode != 0:
        raise AttestationError(f"{executable.name} failed")
    return completed


def _postgres_sql(database: str, query: str, *, timeout: int = 60) -> str:
    if not IDENTIFIER_PATTERN.fullmatch(database) and not RESTORE_DATABASE_PATTERN.fullmatch(database):
        raise AttestationError("unsafe maintenance database identifier")
    psql = _trusted_executable("psql")
    completed = _run_as_postgres(
        psql,
        [
            "-X",
            "--no-psqlrc",
            "--set",
            "ON_ERROR_STOP=1",
            "--port",
            str(EXPECTED_PORT),
            "--dbname",
            database,
            "--tuples-only",
            "--no-align",
            "--command",
            query,
        ],
        timeout=timeout,
    )
    output = completed.stdout
    if not isinstance(output, bytes) or len(output) > 1024 * 1024:
        raise AttestationError("PostgreSQL maintenance query output is outside its bound")
    try:
        return output.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise AttestationError("PostgreSQL maintenance query returned invalid UTF-8") from exc


def _quoted_identifier(identifier: str) -> str:
    if not RESTORE_DATABASE_PATTERN.fullmatch(identifier):
        raise AttestationError("unsafe isolated restore database identifier")
    return f'"{identifier}"'


def _assert_no_orphan_restore_databases() -> None:
    output = _postgres_sql(
        "postgres",
        "SELECT datname FROM pg_database "
        "WHERE left(datname, 23) = 'mooncen_restore_attest_' "
        "ORDER BY datname",
    )
    names = tuple(line for line in output.splitlines() if line)
    if any(not RESTORE_DATABASE_PATTERN.fullmatch(name) for name in names):
        raise AttestationError("isolated restore inventory contains an unsafe name")
    if names:
        raise AttestationError(
            "orphan isolated restore database requires reviewed manual cleanup"
        )


def _ensure_root_directory(path: Path, mode: int) -> None:
    if not path.exists() and not path.is_symlink():
        path.mkdir(mode=mode, parents=False)
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != mode
    ):
        raise AttestationError(f"protected directory contract failed: {path}")


def _prepare_issue_lock_directory(path: Path = ISSUE_LOCK_DIRECTORY) -> None:
    if not path.exists() and not path.is_symlink():
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
    _ensure_root_directory(path, 0o700)


def _acquire_issue_lock() -> int:
    if fcntl is None:
        raise AttestationError("fcntl is required for backup attestation serialization")
    _prepare_issue_lock_directory()
    _validate_parent_chain(ISSUE_LOCK_PATH)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(ISSUE_LOCK_PATH, flags, 0o600)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise AttestationError("backup attestation lock file is unsafe")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(descriptor)
        raise AttestationError("another backup attestation issue is running") from exc
    except OSError as exc:
        try:
            os.close(descriptor)
        except UnboundLocalError:
            pass
        raise AttestationError("backup attestation issue lock is unavailable") from exc
    return descriptor


def _create_evidence_directory(now: dt.datetime) -> Path:
    control_root = EVIDENCE_ROOT.parent
    created_control_root = False
    if not control_root.exists() and not control_root.is_symlink():
        control_root.mkdir(mode=0o700)
        created_control_root = True
    _ensure_root_directory(control_root, 0o700)
    if created_control_root:
        _fsync_directory(control_root.parent)
    created_evidence_root = False
    if not EVIDENCE_ROOT.exists() and not EVIDENCE_ROOT.is_symlink():
        EVIDENCE_ROOT.mkdir(mode=0o700)
        created_evidence_root = True
    _ensure_root_directory(EVIDENCE_ROOT, 0o700)
    if created_evidence_root:
        _fsync_directory(control_root)
    retained_count, retained_bytes = _inspect_retained_evidence()
    if retained_count >= MAX_RETAINED_EVIDENCE_DIRECTORIES:
        raise AttestationError(
            "backup evidence retention cap reached; review and retire an exact old evidence directory"
        )
    if retained_bytes > MAX_RETAINED_EVIDENCE_BYTES:
        raise AttestationError("backup evidence byte cap exceeded")
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    evidence_directory = EVIDENCE_ROOT / f"{stamp}-{secrets.token_hex(8)}"
    evidence_directory.mkdir(mode=0o700)
    metadata = evidence_directory.lstat()
    if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise AttestationError("new backup evidence directory is unsafe")
    _fsync_directory(EVIDENCE_ROOT)
    return evidence_directory


def _inspect_retained_evidence() -> tuple[int, int]:
    """Return a bounded inventory without deleting recovery evidence.

    Automatic pruning could remove the only reviewed recovery artifact.  A
    failed or superseded generation therefore consumes one of two slots until
    an operator retires its exact root-owned directory out of band.
    """

    if not EVIDENCE_ROOT.exists() and not EVIDENCE_ROOT.is_symlink():
        return 0, 0
    _ensure_root_directory(EVIDENCE_ROOT, 0o700)
    total = 0
    directories = 0
    try:
        entries = sorted(EVIDENCE_ROOT.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise AttestationError("backup evidence inventory is unavailable") from exc
    for directory in entries:
        try:
            metadata = directory.lstat()
        except OSError as exc:
            raise AttestationError("backup evidence entry is unavailable") from exc
        if (
            not EVIDENCE_DIRECTORY_PATTERN.fullmatch(directory.name)
            or stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or (os.name == "posix" and metadata.st_uid != 0)
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise AttestationError("backup evidence root contains an unreviewed entry")
        directories += 1
        try:
            children = list(directory.iterdir())
        except OSError as exc:
            raise AttestationError("backup evidence directory is unreadable") from exc
        if len(children) > 1:
            raise AttestationError("backup evidence directory contains unexpected files")
        for child in children:
            child_metadata = child.lstat()
            if (
                child.name != f"{EXPECTED_DATABASE}.dump"
                or stat.S_ISLNK(child_metadata.st_mode)
                or not stat.S_ISREG(child_metadata.st_mode)
                or child_metadata.st_nlink != 1
                or (os.name == "posix" and child_metadata.st_uid != 0)
                or stat.S_IMODE(child_metadata.st_mode) != 0o600
                or child_metadata.st_size > MAX_BACKUP_BYTES
            ):
                raise AttestationError("backup evidence file contract failed")
            total += child_metadata.st_size
            if total > MAX_RETAINED_EVIDENCE_BYTES:
                raise AttestationError("backup evidence byte cap exceeded")
    return directories, total


def _validate_private_evidence_path(path: Path) -> None:
    if path.parent.parent != EVIDENCE_ROOT:
        raise AttestationError("retained backup evidence is outside its protected root")
    for directory in (EVIDENCE_ROOT.parent, EVIDENCE_ROOT, path.parent):
        try:
            metadata = directory.lstat()
        except OSError as exc:
            raise AttestationError("retained backup evidence directory is unavailable") from exc
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or (os.name == "posix" and metadata.st_uid != 0)
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise AttestationError(
                "retained backup evidence directories must be root-owned mode 0700"
            )


def _available_bytes(path: Path) -> int:
    try:
        filesystem = os.statvfs(path)
    except OSError as exc:
        raise AttestationError(f"free-space probe failed: {path}") from exc
    return filesystem.f_bavail * filesystem.f_frsize


def _preflight_backup_and_restore_space(evidence_directory: Path) -> int:
    database_size_text = _postgres_sql(
        EXPECTED_DATABASE,
        "SELECT pg_database_size(current_database())::text",
    )
    if not database_size_text.isdigit():
        raise AttestationError("source database size probe is invalid")
    database_size = int(database_size_text)
    if not 1 <= database_size <= MAX_SOURCE_DATABASE_BYTES:
        raise AttestationError("source database exceeds the reviewed attestation size cap")

    data_directory_text = _postgres_sql("postgres", "SHOW data_directory")
    data_directory = Path(data_directory_text)
    if not data_directory.is_absolute():
        raise AttestationError("PostgreSQL data_directory is not absolute")
    try:
        data_metadata = data_directory.lstat()
    except OSError as exc:
        raise AttestationError("PostgreSQL data_directory is unavailable") from exc
    if stat.S_ISLNK(data_metadata.st_mode) or not stat.S_ISDIR(data_metadata.st_mode):
        raise AttestationError("PostgreSQL data_directory must be a real directory")
    custom_tablespaces = _postgres_sql(
        "postgres",
        "SELECT count(*)::text FROM pg_tablespace "
        "WHERE spcname NOT IN ('pg_default', 'pg_global')",
    )
    if custom_tablespaces != "0":
        raise AttestationError(
            "custom PostgreSQL tablespaces are unsupported by the isolated restore gate"
        )
    if _postgres_sql("postgres", "SHOW temp_tablespaces"):
        raise AttestationError("temp_tablespaces must be empty for isolated restore evidence")
    pg_wal = data_directory / "pg_wal"
    try:
        wal_metadata = pg_wal.lstat()
    except OSError as exc:
        raise AttestationError("PostgreSQL pg_wal path is unavailable") from exc
    if stat.S_ISLNK(wal_metadata.st_mode) or not stat.S_ISDIR(wal_metadata.st_mode):
        raise AttestationError("PostgreSQL pg_wal must remain on the reviewed data filesystem")
    evidence_required = MAX_BACKUP_BYTES + MIN_EVIDENCE_FREE_RESERVE_BYTES
    restore_required = database_size * 2 + MIN_POSTGRES_FREE_RESERVE_BYTES
    evidence_available = _available_bytes(evidence_directory)
    postgres_available = _available_bytes(data_directory)
    if evidence_directory.stat().st_dev == data_metadata.st_dev:
        shared_required = MAX_BACKUP_BYTES + database_size * 2 + max(
            MIN_EVIDENCE_FREE_RESERVE_BYTES,
            MIN_POSTGRES_FREE_RESERVE_BYTES,
        )
        if evidence_available < shared_required:
            raise AttestationError(
                "shared backup/PostgreSQL filesystem lacks dump, restore and reserve capacity"
            )
    elif evidence_available < evidence_required:
        raise AttestationError(
            "backup evidence filesystem lacks the hard dump cap plus free-space reserve"
        )
    if postgres_available < restore_required:
        raise AttestationError(
            "PostgreSQL filesystem lacks twice the source database size plus reserve"
        )
    return database_size


def _verify_local_cluster_matches(live: dict[str, Any]) -> None:
    output = _postgres_sql(
        "postgres",
        """
        SELECT (pg_control_system()).system_identifier::text || E'\\t' ||
               database.oid::text || E'\\t' ||
               pg_get_userbyid(database.datdba) || E'\\t' ||
               current_setting('server_version_num')
        FROM pg_database AS database
        WHERE database.datname = 'mooncen_staging'
        """,
    )
    fields = output.split("\t")
    expected = [
        live["database"]["postgres_system_identifier"],
        str(live["database"]["database_oid"]),
        live["database"]["database_owner"],
        live["database"]["postgres_server_version_num"],
    ]
    if fields != expected:
        raise AttestationError("local PostgreSQL cluster differs from verified gen1db TLS endpoint")


def _create_backup_and_verify_restore(
    *, evidence_directory: Path, live_before: dict[str, Any]
) -> dict[str, Any]:
    _verify_local_cluster_matches(live_before)
    _assert_no_orphan_restore_databases()
    _preflight_backup_and_restore_space(evidence_directory)
    pg_dump = _pg_dump_path()
    pg_restore = _trusted_executable("pg_restore")
    backup_path = evidence_directory / f"{EXPECTED_DATABASE}.dump"
    backup_started = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        backup_descriptor = os.open(backup_path, flags, 0o600)
    except OSError as exc:
        raise AttestationError("protected backup output could not be created") from exc
    try:
        _run_as_postgres(
            pg_dump,
            [
                "--format=custom",
                "--compress=6",
                "--no-owner",
                "--no-privileges",
                "--no-tablespaces",
                "--port",
                str(EXPECTED_PORT),
                "--dbname",
                EXPECTED_DATABASE,
            ],
            stdout=backup_descriptor,
            timeout=3600,
            file_size_limit=MAX_BACKUP_BYTES,
        )
        os.fsync(backup_descriptor)
    finally:
        os.close(backup_descriptor)
    os.chown(backup_path, 0, 0)
    os.chmod(backup_path, 0o600)
    _fsync_regular_file(backup_path)
    _fsync_directory(evidence_directory)
    backup_completed = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    backup_sha256, backup_size = _sha256_file(
        backup_path,
        label="fresh PostgreSQL custom backup",
        maximum=MAX_BACKUP_BYTES,
        owner_only=True,
    )
    backup_read_descriptor = os.open(
        backup_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        _run_as_postgres(
            pg_restore,
            ["--list"],
            stdin=backup_read_descriptor,
            timeout=120,
        )
    finally:
        os.close(backup_read_descriptor)

    restore_database = f"mooncen_restore_attest_{secrets.token_hex(12)}"
    if not RESTORE_DATABASE_PATTERN.fullmatch(restore_database):
        raise AttestationError("generated restore database name is invalid")
    quoted = _quoted_identifier(restore_database)
    if _postgres_sql(
        "postgres",
        f"SELECT count(*) FROM pg_database WHERE datname = '{restore_database}'",
    ) != "0":
        raise AttestationError("generated isolated restore database already exists")

    create_attempted = False
    restore_error: Exception | None = None
    cleanup_error: Exception | None = None
    result: dict[str, Any] | None = None
    restore_started = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    try:
        # Mark the name before sending CREATE.  A network/client failure can
        # occur after PostgreSQL commits the command, so a local success flag
        # is not sufficient evidence that no database exists.
        create_attempted = True
        _postgres_sql(
            "postgres",
            f"CREATE DATABASE {quoted} WITH TEMPLATE template0 OWNER postgres ALLOW_CONNECTIONS false",
        )
        _postgres_sql(
            "postgres",
            f"REVOKE CONNECT ON DATABASE {quoted} FROM PUBLIC; "
            f"ALTER DATABASE {quoted} ALLOW_CONNECTIONS true",
        )
        backup_read_descriptor = os.open(
            backup_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            _run_as_postgres(
                pg_restore,
                [
                    "--exit-on-error",
                    "--single-transaction",
                    "--no-owner",
                    "--no-privileges",
                    "--no-tablespaces",
                    "--port",
                    str(EXPECTED_PORT),
                    "--dbname",
                    restore_database,
                ],
                stdin=backup_read_descriptor,
                timeout=3600,
            )
        finally:
            os.close(backup_read_descriptor)
        object_literals = ",".join(f"'{item}'" for item in REQUIRED_OBJECTS)
        restored_objects = tuple(
            _postgres_sql(
                restore_database,
                "SELECT n.nspname || '.' || c.relname "
                "FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace "
                "WHERE (n.nspname || '.' || c.relname) = ANY(ARRAY["
                f"{object_literals}"
                "]::text[]) AND c.relkind IN ('r', 'p') ORDER BY 1",
            ).splitlines()
        )
        if restored_objects != REQUIRED_OBJECTS:
            raise AttestationError("isolated restore is missing required staging objects")
        counts = _postgres_sql(
            restore_database,
            "SELECT (SELECT count(*) FROM public.courses)::text || E'\\t' || "
            "(SELECT count(*) FROM public.branches)::text",
        ).split("\t")
        if len(counts) != 2 or not all(value.isdigit() for value in counts):
            raise AttestationError("isolated restore smoke counts are invalid")
        courses_count, branches_count = (int(value) for value in counts)
        if courses_count < 1 or branches_count < 1:
            raise AttestationError("isolated restore smoke counts are empty")

        restored_schema = _run_as_postgres(
            pg_dump,
            [
                "--schema-only",
                "--no-owner",
                "--no-privileges",
                "--no-tablespaces",
                "--port",
                str(EXPECTED_PORT),
                "--dbname",
                restore_database,
            ],
            timeout=300,
        ).stdout
        if not isinstance(restored_schema, bytes) or not restored_schema:
            raise AttestationError("isolated restored schema dump is empty")
        if len(restored_schema) > MAX_SCHEMA_BYTES:
            raise AttestationError("isolated restored schema dump exceeds its bound")
        restored_schema_sha256 = _schema_digest(restored_schema)
        restore_completed = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        result = {
            "backup_completed_at": _format_timestamp(backup_completed),
            "backup_name": backup_path.name,
            "backup_path": str(backup_path),
            "backup_sha256": backup_sha256,
            "backup_size": backup_size,
            "branches_count": branches_count,
            "courses_count": courses_count,
            "restore_completed_at": _format_timestamp(restore_completed),
            "restore_database": restore_database,
            "restore_started_at": _format_timestamp(restore_started),
            "restored_schema_sha256": restored_schema_sha256,
        }
    except Exception as exc:
        restore_error = exc
    finally:
        if create_attempted:
            cleanup_failures: list[Exception] = []
            try:
                _postgres_sql(
                    "postgres",
                    f"ALTER DATABASE {quoted} ALLOW_CONNECTIONS false; "
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    f"WHERE datname = '{restore_database}' AND pid <> pg_backend_pid()",
                )
            except Exception as exc:
                cleanup_failures.append(exc)
            # DROP is independent of the connection-disable step.  Always
            # attempt it, including when CREATE returned an uncertain client
            # error or ALTER/terminate failed.
            try:
                _postgres_sql(
                    "postgres", f"DROP DATABASE IF EXISTS {quoted} WITH (FORCE)"
                )
            except Exception as exc:
                cleanup_failures.append(exc)
            try:
                if _postgres_sql(
                    "postgres",
                    f"SELECT count(*) FROM pg_database WHERE datname = '{restore_database}'",
                ) != "0":
                    raise AttestationError("isolated restore database still exists after DROP")
            except Exception as exc:
                cleanup_failures.append(exc)
            if cleanup_failures:
                cleanup_error = cleanup_failures[-1]
    if cleanup_error is not None:
        raise AttestationError(
            "isolated restore database cleanup failed; attestation was not issued"
        ) from cleanup_error
    if restore_error is not None:
        if isinstance(restore_error, AttestationError):
            raise restore_error
        raise AttestationError("isolated restore verification failed") from restore_error
    if result is None:
        raise AttestationError("isolated restore produced no verified result")
    if backup_started > backup_completed:
        raise AttestationError("backup completion clock moved backwards")
    return result


def _dump_live_schema(environment: dict[str, str]) -> tuple[str, str]:
    executable = _pg_dump_path()
    child_environment = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PGPASSWORD": environment["OPS_CRAWLER_SCHEMA_DB_PASSWORD"],
        "PGSSLMODE": EXPECTED_SSLMODE,
        "PGSSLROOTCERT": environment["DB_SSLROOTCERT"],
    }
    command = [
        str(executable),
        "--schema-only",
        "--no-owner",
        "--no-privileges",
        "--no-tablespaces",
        "--host",
        EXPECTED_HOST,
        "--port",
        str(EXPECTED_PORT),
        "--username",
        environment["OPS_CRAWLER_SCHEMA_DB_USER"],
        "--dbname",
        EXPECTED_DATABASE,
    ]
    try:
        completed = subprocess.run(
            command,
            env=child_environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AttestationError("live schema-only pg_dump did not complete") from exc
    if completed.returncode != 0:
        raise AttestationError("live schema-only pg_dump failed")
    if not completed.stdout or len(completed.stdout) > MAX_SCHEMA_BYTES:
        raise AttestationError("live schema-only pg_dump size is outside its bound")
    try:
        version = subprocess.run(
            [str(executable), "--version"],
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise AttestationError("pg_dump version probe failed") from exc
    return _schema_digest(completed.stdout), _require_string(
        version, "pg_dump version", maximum=120
    )


def _read_live_database_identity(environment: dict[str, str]) -> dict[str, Any]:
    try:
        import psycopg2
    except ImportError as exc:  # pragma: no cover - production dependency guard
        raise AttestationError("psycopg2 is required for database identity verification") from exc

    try:
        connection = psycopg2.connect(
            host=EXPECTED_HOST,
            port=EXPECTED_PORT,
            dbname=EXPECTED_DATABASE,
            user=environment["OPS_CRAWLER_SCHEMA_DB_USER"],
            password=environment["OPS_CRAWLER_SCHEMA_DB_PASSWORD"],
            sslmode=EXPECTED_SSLMODE,
            sslrootcert=environment["DB_SSLROOTCERT"],
            connect_timeout=10,
            application_name="mooncen_backup_attestation_verify",
            options="-c default_transaction_read_only=on -c statement_timeout=10000",
        )
    except Exception as exc:
        raise AttestationError("read-only database identity connection failed") from exc
    try:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT current_database(), inet_server_addr()::text,
                           inet_server_port(), database.oid::bigint,
                           pg_get_userbyid(database.datdba),
                           current_setting('server_version_num'),
                           EXISTS (
                             SELECT 1 FROM pg_stat_ssl
                             WHERE pid = pg_backend_pid() AND ssl
                           )
                    FROM pg_database AS database
                    WHERE database.datname = current_database()
                    """
                )
                row = cursor.fetchone()
                cursor.execute(
                    "SELECT extname, extversion FROM pg_extension ORDER BY extname"
                )
                extensions = [
                    {"name": str(name), "version": str(version)}
                    for name, version in cursor.fetchall()
                ]
                cursor.execute(
                    """
                    SELECT n.nspname || '.' || c.relname
                    FROM pg_class AS c
                    JOIN pg_namespace AS n ON n.oid = c.relnamespace
                    WHERE (n.nspname || '.' || c.relname) = ANY(%s)
                      AND c.relkind IN ('r', 'p')
                    ORDER BY 1
                    """,
                    (list(REQUIRED_OBJECTS),),
                )
                objects = tuple(str(item[0]) for item in cursor.fetchall())
    except Exception as exc:
        raise AttestationError("read-only database identity query failed") from exc
    finally:
        connection.close()
    if row is None or row[0] != EXPECTED_DATABASE or row[2] != EXPECTED_PORT or row[6] is not True:
        raise AttestationError("live database endpoint or TLS session identity is invalid")
    if objects != REQUIRED_OBJECTS:
        raise AttestationError("live database is missing required staging objects")
    server_address = _require_string(row[1], "database server address", maximum=64)
    try:
        ipaddress.ip_address(server_address)
    except ValueError as exc:
        raise AttestationError("database server address is not an IP address") from exc
    owner = _require_string(row[4], "database owner", maximum=63)
    if not IDENTIFIER_PATTERN.fullmatch(owner):
        raise AttestationError("database owner is not a PostgreSQL identifier")
    version_num = _require_string(row[5], "PostgreSQL version number", maximum=8)
    if not VERSION_NUM_PATTERN.fullmatch(version_num):
        raise AttestationError("PostgreSQL version number is invalid")
    for extension in extensions:
        if not EXTENSION_NAME_PATTERN.fullmatch(extension["name"]):
            raise AttestationError("extension name is invalid")
        _require_string(extension["version"], "extension version", maximum=80)
    return {
        "database_oid": _require_uint(row[3], "database OID", 4_294_967_295),
        "database_owner": owner,
        "extensions": extensions,
        "postgres_server_version_num": version_num,
        "server_address": server_address,
    }


def _collect_live_contract(
    environment: dict[str, str], *, require_root: bool = True
) -> dict[str, Any]:
    hostname = socket.gethostname().split(".", 1)[0].lower()
    if hostname != EXPECTED_HOST:
        raise AttestationError("backup attestation operations are pinned to hostname gen1db")
    database = _read_live_database_identity(environment)
    local_identity = _postgres_sql(
        "postgres",
        """
        SELECT (pg_control_system()).system_identifier::text || E'\\t' ||
               database.oid::text || E'\\t' ||
               pg_get_userbyid(database.datdba) || E'\\t' ||
               current_setting('server_version_num')
        FROM pg_database AS database
        WHERE database.datname = 'mooncen_staging'
        """,
    ).split("\t")
    if len(local_identity) != 4 or local_identity[1:] != [
        str(database["database_oid"]),
        database["database_owner"],
        database["postgres_server_version_num"],
    ]:
        raise AttestationError("local PostgreSQL cluster differs from verified TLS endpoint")
    if not SYSTEM_IDENTIFIER_PATTERN.fullmatch(local_identity[0]):
        raise AttestationError("local PostgreSQL system identifier is invalid")
    database["postgres_system_identifier"] = local_identity[0]
    schema_sha256, pg_dump_version = _dump_live_schema(environment)
    ca_path = Path(environment["DB_SSLROOTCERT"])
    ca_sha256, _ = _sha256_file(
        ca_path,
        label="database TLS root CA",
        maximum=1024 * 1024,
        require_root=require_root,
    )
    return {
        "database": database,
        "hostname": hostname,
        "pg_dump_version": pg_dump_version,
        "schema_sha256": schema_sha256,
        "tls_ca_sha256": ca_sha256,
        "tls_certificate_sha256": _postgres_tls_fingerprint(
            EXPECTED_HOST, EXPECTED_PORT, ca_path
        ),
    }


def _validate_payload(payload: dict[str, Any], *, now: dt.datetime, max_age: int) -> None:
    _require_exact_keys(
        payload,
        {
            "backup",
            "database",
            "format",
            "issued_at",
            "restore",
            "schema",
            "tool",
            "valid_until",
        },
        "attestation payload",
    )
    if payload["format"] != FORMAT:
        raise AttestationError("attestation format is unsupported")
    tool = payload["tool"]
    if not isinstance(tool, dict):
        raise AttestationError("attestation tool record is invalid")
    _require_exact_keys(tool, {"name", "version"}, "attestation tool")
    if tool != {"name": TOOL_NAME, "version": TOOL_VERSION}:
        raise AttestationError("attestation tool identity is unsupported")

    database = payload["database"]
    if not isinstance(database, dict):
        raise AttestationError("database identity is invalid")
    _require_exact_keys(
        database,
        {
            "database_name",
            "database_oid",
            "database_owner",
            "dns_host",
            "extensions",
            "os_hostname",
            "port",
            "postgres_server_version_num",
            "postgres_system_identifier",
            "server_address",
            "tls_ca_sha256",
            "tls_certificate_sha256",
            "tls_mode",
            "tls_server_name",
        },
        "database identity",
    )
    if (
        database["dns_host"] != EXPECTED_HOST
        or database["os_hostname"] != EXPECTED_HOST
        or database["tls_server_name"] != EXPECTED_HOST
        or database["port"] != EXPECTED_PORT
        or database["database_name"] != EXPECTED_DATABASE
        or database["tls_mode"] != EXPECTED_SSLMODE
    ):
        raise AttestationError("attestation does not identify exact gen1db staging endpoint")
    _require_uint(database["database_oid"], "database OID", 4_294_967_295)
    if not IDENTIFIER_PATTERN.fullmatch(
        _require_string(database["database_owner"], "database owner", maximum=63)
    ):
        raise AttestationError("database owner is invalid")
    if not SYSTEM_IDENTIFIER_PATTERN.fullmatch(
        _require_string(
            database["postgres_system_identifier"],
            "PostgreSQL system identifier",
            maximum=24,
        )
    ):
        raise AttestationError("PostgreSQL system identifier is invalid")
    if not VERSION_NUM_PATTERN.fullmatch(
        _require_string(
            database["postgres_server_version_num"],
            "PostgreSQL version number",
            maximum=8,
        )
    ):
        raise AttestationError("PostgreSQL version number is invalid")
    try:
        ipaddress.ip_address(
            _require_string(database["server_address"], "database server address", maximum=64)
        )
    except ValueError as exc:
        raise AttestationError("database server address is invalid") from exc
    for field in ("tls_ca_sha256", "tls_certificate_sha256"):
        if not SHA256_PATTERN.fullmatch(_require_string(database[field], field, maximum=64)):
            raise AttestationError(f"{field} is invalid")
    extensions = database["extensions"]
    if not isinstance(extensions, list) or len(extensions) > 128:
        raise AttestationError("database extensions are invalid")
    extension_names = []
    for extension in extensions:
        if not isinstance(extension, dict):
            raise AttestationError("database extension record is invalid")
        _require_exact_keys(extension, {"name", "version"}, "database extension")
        name = _require_string(extension["name"], "extension name", maximum=63)
        if not EXTENSION_NAME_PATTERN.fullmatch(name):
            raise AttestationError("extension name is invalid")
        _require_string(extension["version"], "extension version", maximum=80)
        extension_names.append(name)
    if extension_names != sorted(set(extension_names)):
        raise AttestationError("database extensions must be unique and sorted")

    backup = payload["backup"]
    if not isinstance(backup, dict):
        raise AttestationError("backup evidence is invalid")
    _require_exact_keys(
        backup,
        {
            "completed_at",
            "kind",
            "object_name",
            "object_path",
            "pg_dump_version",
            "reviewed_manifest_sha256",
            "sha256",
            "size_bytes",
        },
        "backup evidence",
    )
    if backup["kind"] not in {"pg_custom_dump", "reviewed_pg_custom_dump"}:
        raise AttestationError("backup kind is unsupported")
    object_name = _require_string(backup["object_name"], "backup object name", maximum=180)
    if Path(object_name).name != object_name or object_name in {".", ".."}:
        raise AttestationError("backup object name must be a basename")
    object_path_text = _require_string(
        backup["object_path"], "backup evidence path", maximum=500
    )
    object_path = PurePosixPath(object_path_text)
    evidence_root = PurePosixPath(EVIDENCE_ROOT_TEXT)
    if (
        not object_path.is_absolute()
        or object_name != f"{EXPECTED_DATABASE}.dump"
        or object_path.name != object_name
        or object_path.parent.parent != evidence_root
        or not EVIDENCE_DIRECTORY_PATTERN.fullmatch(object_path.parent.name)
        or ".." in object_path.parts
    ):
        raise AttestationError("backup evidence path is outside its canonical protected root")
    if not SHA256_PATTERN.fullmatch(_require_string(backup["sha256"], "backup SHA-256", maximum=64)):
        raise AttestationError("backup SHA-256 is invalid")
    _require_uint(backup["size_bytes"], "backup size", MAX_BACKUP_BYTES)
    _require_string(backup["pg_dump_version"], "backup pg_dump version", maximum=120)
    manifest_digest = backup["reviewed_manifest_sha256"]
    if backup["kind"] == "reviewed_pg_custom_dump":
        if not isinstance(manifest_digest, str) or not SHA256_PATTERN.fullmatch(manifest_digest):
            raise AttestationError("reviewed backup requires a manifest SHA-256")
    elif manifest_digest is not None:
        raise AttestationError("local pg_dump must not claim a reviewed manifest")

    restore = payload["restore"]
    if not isinstance(restore, dict):
        raise AttestationError("restore evidence is invalid")
    _require_exact_keys(
        restore,
        {
            "branches_count",
            "completed_at",
            "courses_count",
            "database_name",
            "isolated",
            "pg_restore_list_passed",
            "required_objects",
            "result",
            "started_at",
        },
        "restore evidence",
    )
    if (
        restore["result"] != "passed"
        or restore["isolated"] is not True
        or restore["pg_restore_list_passed"] is not True
    ):
        raise AttestationError("isolated restore result is not passed")
    restore_database = _require_string(
        restore["database_name"], "restore database name", maximum=63
    )
    if (
        not RESTORE_DATABASE_PATTERN.fullmatch(restore_database)
        or restore_database == EXPECTED_DATABASE
    ):
        raise AttestationError("restore database is not an isolated test database")
    if _require_uint(restore["courses_count"], "restored course count", 1_000_000_000) < 1:
        raise AttestationError("restored course smoke count is empty")
    if _require_uint(restore["branches_count"], "restored branch count", 10_000_000) < 1:
        raise AttestationError("restored branch smoke count is empty")
    if restore["required_objects"] != list(REQUIRED_OBJECTS):
        raise AttestationError("restored required-object contract is incomplete")

    schema = payload["schema"]
    if not isinstance(schema, dict):
        raise AttestationError("schema evidence is invalid")
    _require_exact_keys(
        schema,
        {"algorithm", "match", "restored_sha256", "source_sha256"},
        "schema evidence",
    )
    if schema["algorithm"] != "canonical-pg-dump-schema-only-sha256-v1":
        raise AttestationError("schema digest algorithm is unsupported")
    source_schema = _require_string(schema["source_sha256"], "source schema digest", maximum=64)
    restored_schema = _require_string(
        schema["restored_sha256"], "restored schema digest", maximum=64
    )
    if (
        schema["match"] is not True
        or not SHA256_PATTERN.fullmatch(source_schema)
        or not hmac.compare_digest(source_schema, restored_schema)
    ):
        raise AttestationError("source and restored schema digests do not match")

    issued_at = _parse_timestamp(payload["issued_at"], "issued_at")
    valid_until = _parse_timestamp(payload["valid_until"], "valid_until")
    backup_at = _parse_timestamp(backup["completed_at"], "backup completed_at")
    restore_started = _parse_timestamp(restore["started_at"], "restore started_at")
    restore_completed = _parse_timestamp(restore["completed_at"], "restore completed_at")
    if not backup_at <= restore_started <= restore_completed <= issued_at:
        raise AttestationError("backup, restore and issuance timestamps are out of order")
    if (restore_completed - restore_started).total_seconds() > MAX_RESTORE_DURATION_SECONDS:
        raise AttestationError("isolated restore duration exceeds its bound")
    if (issued_at - restore_completed).total_seconds() > MAX_ISSUANCE_DELAY_SECONDS:
        raise AttestationError("attestation was not issued immediately after restore verification")
    if not issued_at < valid_until <= issued_at + dt.timedelta(
        seconds=MAX_ATTESTATION_AGE_SECONDS
    ):
        raise AttestationError("attestation validity interval exceeds its hard bound")
    if now < issued_at - dt.timedelta(seconds=MAX_CLOCK_SKEW_SECONDS):
        raise AttestationError("attestation issuance time is in the future")
    if now > valid_until:
        raise AttestationError("backup/restore attestation has expired")
    if (now - restore_completed).total_seconds() > max_age:
        raise AttestationError("backup/restore attestation is stale")


def _issue_document(
    *,
    key: bytes,
    live: dict[str, Any],
    backup_sha256: str,
    backup_size: int,
    backup_name: str,
    backup_path: str,
    backup_kind: str,
    backup_completed_at: str,
    reviewed_manifest_sha256: str | None,
    restored_schema_sha256: str,
    restore_database: str,
    restore_started_at: str,
    restore_completed_at: str,
    courses_count: int,
    branches_count: int,
    now: dt.datetime,
) -> dict[str, Any]:
    payload = {
        "backup": {
            "completed_at": backup_completed_at,
            "kind": backup_kind,
            "object_name": backup_name,
            "object_path": backup_path,
            "pg_dump_version": live["pg_dump_version"],
            "reviewed_manifest_sha256": reviewed_manifest_sha256,
            "sha256": backup_sha256,
            "size_bytes": backup_size,
        },
        "database": {
            "database_name": EXPECTED_DATABASE,
            "database_oid": live["database"]["database_oid"],
            "database_owner": live["database"]["database_owner"],
            "dns_host": EXPECTED_HOST,
            "extensions": live["database"]["extensions"],
            "os_hostname": live["hostname"],
            "port": EXPECTED_PORT,
            "postgres_server_version_num": live["database"][
                "postgres_server_version_num"
            ],
            "postgres_system_identifier": live["database"][
                "postgres_system_identifier"
            ],
            "server_address": live["database"]["server_address"],
            "tls_ca_sha256": live["tls_ca_sha256"],
            "tls_certificate_sha256": live["tls_certificate_sha256"],
            "tls_mode": EXPECTED_SSLMODE,
            "tls_server_name": EXPECTED_HOST,
        },
        "format": FORMAT,
        "issued_at": _format_timestamp(now),
        "restore": {
            "branches_count": branches_count,
            "completed_at": restore_completed_at,
            "courses_count": courses_count,
            "database_name": restore_database,
            "isolated": True,
            "pg_restore_list_passed": True,
            "required_objects": list(REQUIRED_OBJECTS),
            "result": "passed",
            "started_at": restore_started_at,
        },
        "schema": {
            "algorithm": "canonical-pg-dump-schema-only-sha256-v1",
            "match": True,
            "restored_sha256": restored_schema_sha256,
            "source_sha256": live["schema_sha256"],
        },
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "valid_until": _format_timestamp(
            now + dt.timedelta(seconds=MAX_ATTESTATION_AGE_SECONDS)
        ),
    }
    _validate_payload(
        payload,
        now=now,
        max_age=MAX_ATTESTATION_AGE_SECONDS,
    )
    key_identifier = _key_id(key)
    tag = hmac.new(
        key, _signing_bytes(payload, key_identifier), hashlib.sha256
    ).hexdigest()
    return {
        "authentication": {
            "algorithm": AUTHENTICATION_ALGORITHM,
            "key_id": key_identifier,
            "tag": tag,
        },
        "payload": payload,
    }


def _verify_document(
    document: dict[str, Any],
    *,
    key: bytes,
    now: dt.datetime,
    max_age: int,
    live: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not 1 <= max_age <= MAX_ATTESTATION_AGE_SECONDS:
        raise AttestationError("maximum attestation age must be between 1 and 86400 seconds")
    _require_exact_keys(document, {"authentication", "payload"}, "attestation")
    authentication = document["authentication"]
    payload = document["payload"]
    if not isinstance(authentication, dict) or not isinstance(payload, dict):
        raise AttestationError("attestation structure is invalid")
    _require_exact_keys(
        authentication, {"algorithm", "key_id", "tag"}, "attestation authentication"
    )
    if authentication["algorithm"] != AUTHENTICATION_ALGORITHM:
        raise AttestationError("attestation authentication algorithm is unsupported")
    expected_key_id = _key_id(key)
    supplied_key_id = _require_string(authentication["key_id"], "key identifier", maximum=71)
    if not hmac.compare_digest(supplied_key_id, expected_key_id):
        raise AttestationError("attestation was signed by a different key")
    supplied_tag = _require_string(authentication["tag"], "authentication tag", maximum=64)
    if not SHA256_PATTERN.fullmatch(supplied_tag):
        raise AttestationError("attestation authentication tag is invalid")
    expected_tag = hmac.new(
        key, _signing_bytes(payload, expected_key_id), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(supplied_tag, expected_tag):
        raise AttestationError("attestation authentication failed")
    _validate_payload(payload, now=now, max_age=max_age)

    if live is not None:
        database = payload["database"]
        comparisons = {
            "database_oid": live["database"]["database_oid"],
            "database_owner": live["database"]["database_owner"],
            "extensions": live["database"]["extensions"],
            "os_hostname": live["hostname"],
            "postgres_server_version_num": live["database"][
                "postgres_server_version_num"
            ],
            "postgres_system_identifier": live["database"][
                "postgres_system_identifier"
            ],
            "server_address": live["database"]["server_address"],
            "tls_ca_sha256": live["tls_ca_sha256"],
            "tls_certificate_sha256": live["tls_certificate_sha256"],
        }
        for field, observed in comparisons.items():
            if database[field] != observed:
                raise AttestationError(f"live database identity changed after attestation: {field}")
        if not hmac.compare_digest(
            payload["schema"]["source_sha256"], live["schema_sha256"]
        ):
            raise AttestationError("live database schema changed after backup attestation")
    return payload


def _atomic_write_attestation(path: Path, data: bytes) -> None:
    _validate_parent_chain(path)
    parent = path.parent
    parent_metadata = parent.stat()
    if parent_metadata.st_uid != 0 or parent_metadata.st_mode & 0o022:
        raise AttestationError("attestation directory must be protected and root-owned")
    if path.exists() or path.is_symlink():
        metadata = path.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise AttestationError("existing attestation path is unsafe")
    temporary = parent / f".{path.name}.tmp-{os.getpid()}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    replaced = False
    try:
        descriptor = os.open(temporary, flags, 0o600)
        try:
            _write_all(descriptor, data)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.chown(temporary, 0, 0)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        replaced = True
        _fsync_directory(parent)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        if replaced:
            raise AttestationError(
                "attestation was replaced but directory durability could not be confirmed"
            ) from exc
        raise AttestationError("attestation could not be written atomically") from exc


def _require_root_linux() -> None:
    if os.name != "posix" or not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise AttestationError("backup attestation commands must run as root on Linux")


def _generate_key(args: argparse.Namespace) -> None:
    _require_root_linux()
    path = args.key
    _validate_parent_chain(path)
    parent = path.parent
    parent_metadata = parent.stat()
    if parent_metadata.st_uid != 0 or parent_metadata.st_mode & 0o022:
        raise AttestationError("attestation key directory must be protected and root-owned")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise AttestationError("attestation HMAC key already exists; refusing rotation") from exc
    except OSError as exc:
        raise AttestationError("attestation HMAC key could not be created safely") from exc
    try:
        key = secrets.token_bytes(32)
        _write_all(descriptor, key)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chown(path, 0, 0)
    os.chmod(path, 0o600)
    _fsync_directory(parent)
    print(f"backup_attestation_key_created={path}")


def _issue(args: argparse.Namespace) -> None:
    _require_root_linux()
    lock_descriptor = _acquire_issue_lock()
    try:
        environment = _parse_environment(args.database_env)
        key = _load_key(args.key)
        started_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        live_before = _collect_live_contract(environment)
        evidence_directory = _create_evidence_directory(started_at)
        evidence = _create_backup_and_verify_restore(
            evidence_directory=evidence_directory,
            live_before=live_before,
        )
        # A second read-only probe makes concurrent endpoint, extension or
        # schema changes fail closed.  It is deliberately after FORCE DROP.
        live_after = _collect_live_contract(environment)
        if live_before != live_after:
            raise AttestationError("live database contract changed during backup verification")
        if not hmac.compare_digest(
            evidence["restored_schema_sha256"], live_after["schema_sha256"]
        ):
            raise AttestationError("source and isolated-restored schema digests differ")
        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        document = _issue_document(
            key=key,
            live=live_after,
            backup_sha256=evidence["backup_sha256"],
            backup_size=evidence["backup_size"],
            backup_name=evidence["backup_name"],
            backup_path=evidence["backup_path"],
            backup_kind="pg_custom_dump",
            backup_completed_at=evidence["backup_completed_at"],
            reviewed_manifest_sha256=None,
            restored_schema_sha256=evidence["restored_schema_sha256"],
            restore_database=evidence["restore_database"],
            restore_started_at=evidence["restore_started_at"],
            restore_completed_at=evidence["restore_completed_at"],
            courses_count=evidence["courses_count"],
            branches_count=evidence["branches_count"],
            now=now,
        )
        _atomic_write_attestation(args.output, _canonical_json(document))
        print(f"backup_attestation_issued={args.output}")
        print(f"backup_evidence_directory={evidence_directory}")
    finally:
        os.close(lock_descriptor)


def _verify(args: argparse.Namespace) -> None:
    _require_root_linux()
    raw = _read_regular_file(
        args.attestation,
        label="backup attestation",
        maximum=MAX_JSON_BYTES,
        owner_only=True,
    )
    document = _parse_json(raw, "backup attestation")
    if raw != _canonical_json(document):
        raise AttestationError("backup attestation JSON is not canonical")
    key = _load_key(args.key)
    environment = _parse_environment(args.database_env)
    live = _collect_live_contract(environment)
    payload = _verify_document(
        document,
        key=key,
        now=dt.datetime.now(dt.timezone.utc).replace(microsecond=0),
        max_age=args.max_age_seconds,
        live=live,
    )
    backup = payload["backup"]
    retained_backup = Path(backup["object_path"])
    _validate_private_evidence_path(retained_backup)
    observed_sha256, observed_size = _sha256_file(
        retained_backup,
        label="retained backup evidence",
        maximum=MAX_BACKUP_BYTES,
        owner_only=True,
    )
    if observed_size != backup["size_bytes"] or not hmac.compare_digest(
        observed_sha256, backup["sha256"]
    ):
        raise AttestationError("retained backup evidence digest or size changed")
    print("backup_attestation_verified")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Issue or verify the gen1db crawler-control backup attestation."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate_key = subparsers.add_parser("generate-key")
    generate_key.add_argument("--key", type=Path, default=DEFAULT_KEY_PATH)
    generate_key.set_defaults(handler=_generate_key)

    issue = subparsers.add_parser("issue")
    issue.add_argument("--database-env", type=Path, required=True)
    issue.add_argument("--key", type=Path, default=DEFAULT_KEY_PATH)
    issue.add_argument("--output", type=Path, default=DEFAULT_ATTESTATION_PATH)
    issue.set_defaults(handler=_issue)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--database-env", type=Path, required=True)
    verify.add_argument("--attestation", type=Path, default=DEFAULT_ATTESTATION_PATH)
    verify.add_argument("--key", type=Path, default=DEFAULT_KEY_PATH)
    verify.add_argument(
        "--max-age-seconds",
        type=int,
        default=MAX_ATTESTATION_AGE_SECONDS,
    )
    verify.set_defaults(handler=_verify)
    return parser


def main() -> int:
    try:
        arguments = _build_parser().parse_args()
        arguments.handler(arguments)
    except AttestationError as exc:
        print(f"backup attestation rejected: {exc}", file=sys.stderr)
        return 65
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
