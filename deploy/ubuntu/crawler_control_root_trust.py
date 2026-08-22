#!/usr/bin/python3
"""Fixed, root-owned trust boundary for the first crawler-control install.

This program is intentionally *not* executed from ``/opt/mooncen``.  A reviewed
copy is installed out of band at
``/usr/local/libexec/mooncen-crawler-control-root-trust``.  It authenticates a
candidate release, asks a separately installed fixed evidence engine to make
and verify a real isolated-restore attestation, and signs a release-bound
receipt with a host-local OpenSSH key.

The ``consume-receipt`` interface deliberately remains NOT READY.  Enabling it
requires the receipt consumption row and the first crawler-control database
write to commit under one PostgreSQL advisory/audit gate.  No command-line
override bypasses that gate.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - production helper is Linux-only
    fcntl = None
try:
    import pwd
except ImportError:  # pragma: no cover - production helper is Linux-only
    pwd = None


HELPER_FORMAT = "mooncen-crawler-control-root-trust-v1"
RELEASE_FORMAT = "mooncen-crawler-control-release-v1"
RECEIPT_FORMAT = "mooncen-crawler-control-backup-receipt-v1"
EVIDENCE_FORMAT = "mooncen-crawler-control-backup-attestation-v1"
RELEASE_NAMESPACE = "mooncen-crawler-control-release"
RELEASE_PRINCIPAL = "mooncen-crawler-control-release"
RECEIPT_NAMESPACE = "mooncen-crawler-control-backup-receipt-v1"
RECEIPT_PRINCIPAL = "mooncen-gen1db-backup-receipt"
EXPECTED_HOST = "gen1db"
EXPECTED_ROLE = "crawler-control"
EXPECTED_DATABASE = "mooncen_staging"
EXPECTED_DATABASE_HOST = "gen1db"
EXPECTED_DATABASE_PORT = 5432
EXPECTED_DATABASE_SSLMODE = "verify-full"
MAX_RECEIPT_AGE_SECONDS = 86_400
MAX_CLOCK_SKEW_SECONDS = 300
MAX_JSON_BYTES = 256 * 1024
MAX_SIGNATURE_BYTES = 16 * 1024
MAX_METADATA_BYTES = 4096
MAX_TREE_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_RETAINED_RECEIPTS = 2

TRUST_ROOT = Path("/var/lib/mooncen-crawler-control-root-trust")
RECEIPT_ROOT = TRUST_ROOT / "receipts"
LOCK_PATH = TRUST_ROOT / "issue.lock"
POLICY_PATH = Path("/etc/mooncen/crawler-control-root-trust.policy")
RELEASE_ALLOWED_SIGNERS = Path(
    "/etc/mooncen/crawler-control-release-allowed-signers"
)
RECEIPT_ALLOWED_SIGNERS = Path(
    "/etc/mooncen/crawler-control-backup-receipt-allowed-signers"
)
RECEIPT_SIGNING_KEY = Path(
    "/etc/mooncen/crawler-control-backup-receipt-signing-key"
)
EVIDENCE_HMAC_KEY = Path(
    "/etc/mooncen/crawler-control-backup-attestation.key"
)
EVIDENCE_ENGINE = Path(
    "/usr/local/libexec/mooncen-crawler-control-backup-attestation"
)
HELPER_INSTALL_PATH = Path("/usr/local/libexec/mooncen-crawler-control-root-trust")
RECEIPT_SCHEMA_INSTALL_PATH = Path(
    "/usr/local/share/mooncen/crawler-control-backup-receipt.schema.json"
)
DATABASE_ENV = Path("/etc/mooncen/crawler-control-bootstrap-schema.env")
PYTHON = Path("/usr/bin/python3.11")
SSH_KEYGEN = Path("/usr/bin/ssh-keygen")

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
RELEASE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
NONCE_RE = re.compile(r"^[0-9a-f]{64}$")
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
KEY_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
EVIDENCE_ENGINE_POLICY_RE = re.compile(
    r"\AFORMAT=mooncen-crawler-control-root-trust-policy-v1\n"
    r"BOOTSTRAP_COMMIT=((?:[0-9a-f]{40}|[0-9a-f]{64}))\n"
    r"ROOT_TRUST_HELPER_SHA256=([0-9a-f]{64})\n"
    r"EVIDENCE_ENGINE_SHA256=([0-9a-f]{64})\n"
    r"RECEIPT_SCHEMA_SHA256=([0-9a-f]{64})\n\Z"
)


class TrustError(RuntimeError):
    """Raised when fixed bootstrap trust evidence is absent or invalid."""


@dataclass(frozen=True)
class CandidateIdentity:
    release_id: str
    deploy_commit: str
    archive_sha256: str
    tree_sha256: str
    node_role: str
    target_host: str
    signer_principal: str
    metadata_sha256: str
    signature_sha256: str
    archive_size_bytes: int
    tree_size_bytes: int


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
            raise TrustError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _parse_json(data: bytes, label: str) -> dict[str, Any]:
    if not data or len(data) > MAX_JSON_BYTES:
        raise TrustError(f"{label} size is invalid")
    try:
        parsed = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                TrustError(f"{label} contains a non-finite number: {value}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TrustError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(parsed, dict):
        raise TrustError(f"{label} must be a JSON object")
    if _canonical_json(parsed) != data:
        raise TrustError(f"{label} is not canonical JSON")
    return parsed


def _exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise TrustError(f"{label} has unknown or missing fields")


def _string(value: Any, label: str, maximum: int = 255) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise TrustError(f"{label} is invalid")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise TrustError(f"{label} contains control characters")
    return value


def _uint(value: Any, label: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise TrustError(f"{label} is invalid")
    return value


def _timestamp(value: Any, label: str) -> dt.datetime:
    text = _string(value, label, 32)
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", text):
        raise TrustError(f"{label} must be canonical UTC seconds")
    try:
        parsed = dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise TrustError(f"{label} is invalid") from exc
    return parsed.replace(tzinfo=dt.timezone.utc)


def _format_timestamp(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_all(descriptor: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        written = os.write(descriptor, data[offset:])
        if written <= 0:
            raise TrustError("short write while persisting root trust evidence")
        offset += written


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_regular(
    path: Path,
    *,
    label: str,
    maximum: int,
    owner_uid: int | None = None,
    modes: set[int] | None = None,
) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise TrustError(f"{label} is unavailable") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or (owner_uid is not None and metadata.st_uid != owner_uid)
        or (modes is not None and stat.S_IMODE(metadata.st_mode) not in modes)
        or metadata.st_size < 1
        or metadata.st_size > maximum
    ):
        raise TrustError(f"{label} ownership, mode, link count, or size is unsafe")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if (
            (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
        ):
            raise TrustError(f"{label} changed while being opened")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) != opened.st_size or len(data) > maximum:
            raise TrustError(f"{label} changed while being read")
        return data
    finally:
        os.close(descriptor)


def _sha256_file(path: Path, *, maximum: int) -> tuple[str, int]:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise TrustError(f"unsafe file: {path.name}")
    if not 0 < metadata.st_size <= maximum:
        raise TrustError(f"file size is outside the reviewed bound: {path.name}")
    digest = hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise TrustError(f"file changed while being opened: {path.name}")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        if os.fstat(descriptor).st_size != metadata.st_size:
            raise TrustError(f"file changed while being hashed: {path.name}")
    finally:
        os.close(descriptor)
    return digest.hexdigest(), metadata.st_size


def _parse_release_metadata(data: bytes) -> dict[str, str]:
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError as exc:
        raise TrustError("release metadata must be ASCII") from exc
    expected_order = (
        "FORMAT",
        "DEPLOY_COMMIT",
        "DEPLOY_ARCHIVE_SHA256",
        "DEPLOY_TREE_SHA256",
        "NODE_ROLE",
        "TARGET_HOST",
    )
    lines = text.splitlines(keepends=True)
    if len(lines) != len(expected_order) or any(not line.endswith("\n") for line in lines):
        raise TrustError("release metadata line contract is invalid")
    result: dict[str, str] = {}
    for expected, line in zip(expected_order, lines, strict=True):
        key, separator, value = line[:-1].partition("=")
        if separator != "=" or key != expected or key in result:
            raise TrustError("release metadata field order is invalid")
        result[key] = value
    if result["FORMAT"] != RELEASE_FORMAT:
        raise TrustError("release metadata format is unsupported")
    if not COMMIT_RE.fullmatch(result["DEPLOY_COMMIT"]):
        raise TrustError("release commit is invalid")
    for key in ("DEPLOY_ARCHIVE_SHA256", "DEPLOY_TREE_SHA256"):
        if not SHA256_RE.fullmatch(result[key]):
            raise TrustError(f"release metadata {key} is invalid")
    if result["NODE_ROLE"] != EXPECTED_ROLE or result["TARGET_HOST"] != EXPECTED_HOST:
        raise TrustError("release target is not the pinned crawler-control host")
    return result


def _run(command: list[str], *, stdin: bytes | None = None, timeout: int = 60) -> subprocess.CompletedProcess[bytes]:
    environment = {
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "LC_ALL": "C",
    }
    try:
        return subprocess.run(
            command,
            input=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TrustError(f"fixed command failed to execute: {Path(command[0]).name}") from exc


def _verify_openssh_signature(
    content: bytes,
    signature: Path,
    allowed_signers: Path,
    *,
    principal: str,
    namespace: str,
) -> None:
    result = _run(
        [
            str(SSH_KEYGEN),
            "-Y",
            "verify",
            "-f",
            str(allowed_signers),
            "-I",
            principal,
            "-n",
            namespace,
            "-s",
            str(signature),
        ],
        stdin=content,
    )
    if result.returncode != 0:
        raise TrustError("OpenSSH signature verification failed")


def _verify_candidate(
    candidate_dir: Path,
    *,
    release_id: str,
    deploy_user: str,
    expected_commit: str,
    expected_archive_sha256: str,
    expected_tree_sha256: str,
) -> CandidateIdentity:
    if not RELEASE_ID_RE.fullmatch(release_id) or not USER_RE.fullmatch(deploy_user):
        raise TrustError("release id or deploy user is invalid")
    if not COMMIT_RE.fullmatch(expected_commit):
        raise TrustError("expected commit is invalid")
    if not SHA256_RE.fullmatch(expected_archive_sha256) or not SHA256_RE.fullmatch(expected_tree_sha256):
        raise TrustError("expected release digest is invalid")
    expected_prefix = f"/tmp/mooncen-control-upload-{release_id}."
    if not str(candidate_dir).startswith(expected_prefix) or "/" in str(candidate_dir)[len(expected_prefix) :]:
        raise TrustError("candidate directory is outside the fixed upload namespace")
    try:
        if pwd is None:
            raise TrustError("candidate verification is Linux-only")
        deploy_uid = pwd.getpwnam(deploy_user).pw_uid
        directory_metadata = candidate_dir.lstat()
    except (KeyError, OSError) as exc:
        raise TrustError("candidate directory or deploy account is unavailable") from exc
    if (
        not stat.S_ISDIR(directory_metadata.st_mode)
        or directory_metadata.st_uid != deploy_uid
        or stat.S_IMODE(directory_metadata.st_mode) != 0o700
    ):
        raise TrustError("candidate directory ownership or mode is unsafe")
    names = sorted(item.name for item in candidate_dir.iterdir())
    if names != [
        "crawler-control-release.env",
        "crawler-control-release.sig",
        "crawler-control-release.tar.gz",
        "crawler-control-release.tree",
    ]:
        raise TrustError("candidate directory contains unreviewed files")
    metadata_path = candidate_dir / "crawler-control-release.env"
    signature_path = candidate_dir / "crawler-control-release.sig"
    archive_path = candidate_dir / "crawler-control-release.tar.gz"
    tree_path = candidate_dir / "crawler-control-release.tree"
    metadata_bytes = _read_regular(
        metadata_path,
        label="candidate metadata",
        maximum=MAX_METADATA_BYTES,
        owner_uid=deploy_uid,
        modes={0o600},
    )
    signature_bytes = _read_regular(
        signature_path,
        label="candidate signature",
        maximum=MAX_SIGNATURE_BYTES,
        owner_uid=deploy_uid,
        modes={0o600},
    )
    metadata = _parse_release_metadata(metadata_bytes)
    if (
        metadata["DEPLOY_COMMIT"] != expected_commit
        or metadata["DEPLOY_ARCHIVE_SHA256"] != expected_archive_sha256
        or metadata["DEPLOY_TREE_SHA256"] != expected_tree_sha256
    ):
        raise TrustError("candidate metadata differs from the reviewed identity")
    archive_sha256, archive_size = _sha256_file(archive_path, maximum=MAX_ARCHIVE_BYTES)
    tree_sha256, tree_size = _sha256_file(tree_path, maximum=MAX_TREE_BYTES)
    for path in (archive_path, tree_path):
        item = path.lstat()
        if item.st_uid != deploy_uid or stat.S_IMODE(item.st_mode) != 0o600:
            raise TrustError("candidate artifact ownership or mode is unsafe")
    if archive_sha256 != expected_archive_sha256 or tree_sha256 != expected_tree_sha256:
        raise TrustError("candidate artifact digest differs from signed metadata")
    _verify_openssh_signature(
        metadata_bytes,
        signature_path,
        RELEASE_ALLOWED_SIGNERS,
        principal=RELEASE_PRINCIPAL,
        namespace=RELEASE_NAMESPACE,
    )
    return CandidateIdentity(
        release_id=release_id,
        deploy_commit=expected_commit,
        archive_sha256=archive_sha256,
        tree_sha256=tree_sha256,
        node_role=EXPECTED_ROLE,
        target_host=EXPECTED_HOST,
        signer_principal=RELEASE_PRINCIPAL,
        metadata_sha256=_sha256_bytes(metadata_bytes),
        signature_sha256=_sha256_bytes(signature_bytes),
        archive_size_bytes=archive_size,
        tree_size_bytes=tree_size,
    )


def _validate_evidence_document(document: dict[str, Any]) -> tuple[str, str, str]:
    _exact_keys(document, {"authentication", "payload"}, "backup attestation")
    authentication = document["authentication"]
    payload = document["payload"]
    if not isinstance(authentication, dict) or not isinstance(payload, dict):
        raise TrustError("backup attestation structure is invalid")
    if payload.get("format") != EVIDENCE_FORMAT:
        raise TrustError("backup attestation format is unsupported")
    database = payload.get("database")
    if not isinstance(database, dict) or (
        database.get("dns_host") != EXPECTED_DATABASE_HOST
        or database.get("port") != EXPECTED_DATABASE_PORT
        or database.get("database_name") != EXPECTED_DATABASE
        or database.get("tls_mode") != EXPECTED_DATABASE_SSLMODE
    ):
        raise TrustError("backup attestation is not pinned to gen1db staging")
    issued_at = _string(payload.get("issued_at"), "backup issued_at", 32)
    valid_until = _string(payload.get("valid_until"), "backup valid_until", 32)
    _timestamp(issued_at, "backup issued_at")
    _timestamp(valid_until, "backup valid_until")
    key_id = _string(authentication.get("key_id"), "backup key id", 71)
    if not KEY_ID_RE.fullmatch(key_id):
        raise TrustError("backup attestation key id is invalid")
    return issued_at, valid_until, key_id


def _build_receipt(
    candidate: CandidateIdentity,
    *,
    nonce: str,
    evidence_sha256: str,
    evidence_key_id: str,
    evidence_issued_at: str,
    evidence_valid_until: str,
    now: dt.datetime,
) -> dict[str, Any]:
    if not NONCE_RE.fullmatch(nonce) or not SHA256_RE.fullmatch(evidence_sha256):
        raise TrustError("receipt nonce or evidence digest is invalid")
    evidence_expiry = _timestamp(evidence_valid_until, "backup valid_until")
    issued_at = now.astimezone(dt.timezone.utc).replace(microsecond=0)
    valid_until = min(
        issued_at + dt.timedelta(seconds=MAX_RECEIPT_AGE_SECONDS),
        evidence_expiry,
    )
    if valid_until <= issued_at:
        raise TrustError("backup evidence is already expired")
    return {
        "candidate": {
            "archive_size_bytes": candidate.archive_size_bytes,
            "metadata_sha256": candidate.metadata_sha256,
            "signature_sha256": candidate.signature_sha256,
            "tree_size_bytes": candidate.tree_size_bytes,
        },
        "format": RECEIPT_FORMAT,
        "issued_at": _format_timestamp(issued_at),
        "issuer": {
            "helper_format": HELPER_FORMAT,
            "hostname": EXPECTED_HOST,
            "signature_namespace": RECEIPT_NAMESPACE,
            "signature_principal": RECEIPT_PRINCIPAL,
        },
        "nonce": nonce,
        "recovery_evidence": {
            "attestation_format": EVIDENCE_FORMAT,
            "attestation_key_id": evidence_key_id,
            "attestation_path_basename": "backup-attestation.json",
            "attestation_sha256": evidence_sha256,
            "database_host": EXPECTED_DATABASE_HOST,
            "database_name": EXPECTED_DATABASE,
            "database_port": EXPECTED_DATABASE_PORT,
            "database_sslmode": EXPECTED_DATABASE_SSLMODE,
            "issued_at": evidence_issued_at,
            "valid_until": evidence_valid_until,
        },
        "release": {
            "archive_sha256": candidate.archive_sha256,
            "deploy_commit": candidate.deploy_commit,
            "node_role": candidate.node_role,
            "release_id": candidate.release_id,
            "signer_principal": candidate.signer_principal,
            "target_host": candidate.target_host,
            "tree_sha256": candidate.tree_sha256,
        },
        "valid_until": _format_timestamp(valid_until),
    }


def _validate_receipt(
    receipt: dict[str, Any],
    *,
    now: dt.datetime,
    expected_nonce: str,
    expected_release_id: str,
    expected_commit: str,
    expected_archive_sha256: str,
    expected_tree_sha256: str,
) -> None:
    _exact_keys(
        receipt,
        {"candidate", "format", "issued_at", "issuer", "nonce", "recovery_evidence", "release", "valid_until"},
        "backup receipt",
    )
    if receipt["format"] != RECEIPT_FORMAT or receipt["nonce"] != expected_nonce:
        raise TrustError("backup receipt format or nonce differs")
    if not NONCE_RE.fullmatch(expected_nonce):
        raise TrustError("expected receipt nonce is invalid")
    issuer = receipt["issuer"]
    release = receipt["release"]
    recovery = receipt["recovery_evidence"]
    candidate = receipt["candidate"]
    if not all(isinstance(value, dict) for value in (issuer, release, recovery, candidate)):
        raise TrustError("backup receipt object structure is invalid")
    _exact_keys(issuer, {"helper_format", "hostname", "signature_namespace", "signature_principal"}, "receipt issuer")
    _exact_keys(release, {"archive_sha256", "deploy_commit", "node_role", "release_id", "signer_principal", "target_host", "tree_sha256"}, "receipt release")
    _exact_keys(candidate, {"archive_size_bytes", "metadata_sha256", "signature_sha256", "tree_size_bytes"}, "receipt candidate")
    _exact_keys(recovery, {"attestation_format", "attestation_key_id", "attestation_path_basename", "attestation_sha256", "database_host", "database_name", "database_port", "database_sslmode", "issued_at", "valid_until"}, "receipt recovery evidence")
    if issuer != {
        "helper_format": HELPER_FORMAT,
        "hostname": EXPECTED_HOST,
        "signature_namespace": RECEIPT_NAMESPACE,
        "signature_principal": RECEIPT_PRINCIPAL,
    }:
        raise TrustError("backup receipt issuer identity is invalid")
    expected_release = {
        "archive_sha256": expected_archive_sha256,
        "deploy_commit": expected_commit,
        "node_role": EXPECTED_ROLE,
        "release_id": expected_release_id,
        "signer_principal": RELEASE_PRINCIPAL,
        "target_host": EXPECTED_HOST,
        "tree_sha256": expected_tree_sha256,
    }
    if release != expected_release:
        raise TrustError("backup receipt is bound to another release")
    if (
        recovery.get("attestation_format") != EVIDENCE_FORMAT
        or recovery.get("attestation_path_basename") != "backup-attestation.json"
        or recovery.get("database_host") != EXPECTED_DATABASE_HOST
        or recovery.get("database_port") != EXPECTED_DATABASE_PORT
        or recovery.get("database_name") != EXPECTED_DATABASE
        or recovery.get("database_sslmode") != EXPECTED_DATABASE_SSLMODE
        or not SHA256_RE.fullmatch(str(recovery.get("attestation_sha256", "")))
        or not KEY_ID_RE.fullmatch(str(recovery.get("attestation_key_id", "")))
    ):
        raise TrustError("backup receipt recovery binding is invalid")
    for key in ("metadata_sha256", "signature_sha256"):
        if not SHA256_RE.fullmatch(str(candidate.get(key, ""))):
            raise TrustError("backup receipt candidate digest is invalid")
    _uint(candidate.get("archive_size_bytes"), "archive size", MAX_ARCHIVE_BYTES)
    _uint(candidate.get("tree_size_bytes"), "tree size", MAX_TREE_BYTES)
    issued_at = _timestamp(receipt["issued_at"], "receipt issued_at")
    valid_until = _timestamp(receipt["valid_until"], "receipt valid_until")
    evidence_issued = _timestamp(recovery.get("issued_at"), "evidence issued_at")
    evidence_valid = _timestamp(recovery.get("valid_until"), "evidence valid_until")
    current = now.astimezone(dt.timezone.utc)
    if issued_at < evidence_issued - dt.timedelta(seconds=MAX_CLOCK_SKEW_SECONDS):
        raise TrustError("backup receipt predates its recovery evidence")
    if not issued_at < valid_until <= evidence_valid:
        raise TrustError("backup receipt expiry is outside its evidence lifetime")
    if valid_until - issued_at > dt.timedelta(seconds=MAX_RECEIPT_AGE_SECONDS):
        raise TrustError("backup receipt lifetime exceeds the reviewed maximum")
    if current < issued_at - dt.timedelta(seconds=MAX_CLOCK_SKEW_SECONDS) or current >= valid_until:
        raise TrustError("backup receipt is not currently valid")


def _require_root_host() -> None:
    if os.name != "posix" or os.geteuid() != 0:
        raise TrustError("the fixed trust helper must run as root on Linux")
    if socket.gethostname().split(".", 1)[0] != EXPECTED_HOST:
        raise TrustError("the fixed trust helper is pinned to hostname gen1db")


def _root_file(path: Path, *, mode: int, maximum: int, label: str) -> bytes:
    return _read_regular(path, label=label, maximum=maximum, owner_uid=0, modes={mode})


def _verify_bootstrap(*, require_signing_key: bool) -> str:
    policy = _root_file(POLICY_PATH, mode=0o400, maximum=4096, label="root trust policy")
    try:
        policy_text = policy.decode("ascii")
    except UnicodeDecodeError as exc:
        raise TrustError("root trust policy is not ASCII") from exc
    match = EVIDENCE_ENGINE_POLICY_RE.fullmatch(policy_text)
    if match is None:
        raise TrustError("root trust policy format is invalid")
    expected_helper_sha256 = match.group(2)
    expected_engine_sha256 = match.group(3)
    expected_schema_sha256 = match.group(4)
    helper = _root_file(
        HELPER_INSTALL_PATH,
        mode=0o755,
        maximum=2 * 1024 * 1024,
        label="fixed root trust helper",
    )
    if _sha256_bytes(helper) != expected_helper_sha256:
        raise TrustError("fixed root trust helper differs from root trust policy")
    engine = _root_file(EVIDENCE_ENGINE, mode=0o755, maximum=2 * 1024 * 1024, label="fixed evidence engine")
    if _sha256_bytes(engine) != expected_engine_sha256:
        raise TrustError("fixed evidence engine differs from root trust policy")
    receipt_schema = _root_file(
        RECEIPT_SCHEMA_INSTALL_PATH,
        mode=0o444,
        maximum=MAX_JSON_BYTES,
        label="fixed backup receipt schema",
    )
    if _sha256_bytes(receipt_schema) != expected_schema_sha256:
        raise TrustError("fixed backup receipt schema differs from root trust policy")
    _root_file(RELEASE_ALLOWED_SIGNERS, mode=0o644, maximum=64 * 1024, label="release allowed-signers")
    _root_file(RECEIPT_ALLOWED_SIGNERS, mode=0o644, maximum=64 * 1024, label="receipt allowed-signers")
    _root_file(EVIDENCE_HMAC_KEY, mode=0o600, maximum=4096, label="evidence authentication key")
    _root_file(DATABASE_ENV, mode=0o600, maximum=64 * 1024, label="bootstrap database environment")
    if require_signing_key:
        _root_file(RECEIPT_SIGNING_KEY, mode=0o600, maximum=64 * 1024, label="receipt signing key")
    for executable in (PYTHON, SSH_KEYGEN):
        metadata = executable.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_mode & 0o022:
            raise TrustError(f"fixed executable is unavailable or writable: {executable}")
    return expected_engine_sha256


def _invoke_evidence_engine(command: str, output: Path | None = None) -> None:
    arguments = [
        str(PYTHON),
        "-I",
        str(EVIDENCE_ENGINE),
        command,
        "--database-env",
        str(DATABASE_ENV),
        "--key",
        str(EVIDENCE_HMAC_KEY),
    ]
    if command == "issue":
        if output is None:
            raise TrustError("evidence output path is required")
        arguments.extend(["--output", str(output)])
        timeout = 4 * 60 * 60
    else:
        if output is None:
            raise TrustError("evidence attestation path is required")
        arguments.extend(["--attestation", str(output), "--max-age-seconds", str(MAX_RECEIPT_AGE_SECONDS)])
        timeout = 20 * 60
    result = _run(arguments, timeout=timeout)
    if result.returncode != 0:
        raise TrustError(f"fixed backup evidence engine {command} failed")


def _ensure_trust_directories() -> None:
    for path in (TRUST_ROOT, RECEIPT_ROOT):
        if not path.exists():
            path.mkdir(mode=0o700)
        metadata = path.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) != 0o700:
            raise TrustError(f"root trust directory is unsafe: {path}")


def _issue_lock() -> int:
    if fcntl is None:
        raise TrustError("root trust issue locking is Linux-only")
    _ensure_trust_directories()
    descriptor = os.open(LOCK_PATH, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    metadata = os.fstat(descriptor)
    if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_nlink != 1:
        os.close(descriptor)
        raise TrustError("root trust issue lock is unsafe")
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(descriptor)
        raise TrustError("another root trust receipt issue is running") from exc
    return descriptor


def _sign_receipt(receipt_path: Path) -> Path:
    signature_path = Path(str(receipt_path) + ".sig")
    if signature_path.exists() or signature_path.is_symlink():
        raise TrustError("receipt signature path already exists")
    result = _run(
        [
            str(SSH_KEYGEN),
            "-Y",
            "sign",
            "-f",
            str(RECEIPT_SIGNING_KEY),
            "-n",
            RECEIPT_NAMESPACE,
            str(receipt_path),
        ]
    )
    if result.returncode != 0 or not signature_path.exists():
        raise TrustError("OpenSSH receipt signing failed")
    os.chmod(signature_path, 0o400)
    _fsync_file(signature_path)
    receipt_bytes = _read_regular(receipt_path, label="backup receipt", maximum=MAX_JSON_BYTES, owner_uid=0, modes={0o400})
    _verify_openssh_signature(
        receipt_bytes,
        signature_path,
        RECEIPT_ALLOWED_SIGNERS,
        principal=RECEIPT_PRINCIPAL,
        namespace=RECEIPT_NAMESPACE,
    )
    return signature_path


def _receipt_paths(nonce: str) -> tuple[Path, Path, Path]:
    if not NONCE_RE.fullmatch(nonce):
        raise TrustError("receipt nonce is invalid")
    directory = RECEIPT_ROOT / nonce
    return directory / "receipt.json", directory / "receipt.json.sig", directory / "backup-attestation.json"


def _issue_command(args: argparse.Namespace) -> None:
    _require_root_host()
    _verify_bootstrap(require_signing_key=True)
    lock = _issue_lock()
    temporary: Path | None = None
    try:
        existing = [item for item in RECEIPT_ROOT.iterdir() if item.is_dir()]
        if len(existing) >= MAX_RETAINED_RECEIPTS:
            raise TrustError("receipt retention cap reached; reviewed retirement is required")
        candidate = _verify_candidate(
            args.candidate_dir,
            release_id=args.release_id,
            deploy_user=args.deploy_user,
            expected_commit=args.expected_commit,
            expected_archive_sha256=args.expected_archive_sha256,
            expected_tree_sha256=args.expected_tree_sha256,
        )
        nonce = secrets.token_hex(32)
        final = RECEIPT_ROOT / nonce
        temporary = RECEIPT_ROOT / f".{nonce}.issuing"
        temporary.mkdir(mode=0o700)
        evidence_path = temporary / "backup-attestation.json"
        _invoke_evidence_engine("issue", evidence_path)
        os.chmod(evidence_path, 0o400)
        _fsync_file(evidence_path)
        _invoke_evidence_engine("verify", evidence_path)
        evidence_bytes = _read_regular(
            evidence_path,
            label="backup attestation",
            maximum=MAX_JSON_BYTES,
            owner_uid=0,
            modes={0o400},
        )
        evidence = _parse_json(evidence_bytes, "backup attestation")
        evidence_issued, evidence_valid, evidence_key_id = _validate_evidence_document(evidence)
        receipt = _build_receipt(
            candidate,
            nonce=nonce,
            evidence_sha256=_sha256_bytes(evidence_bytes),
            evidence_key_id=evidence_key_id,
            evidence_issued_at=evidence_issued,
            evidence_valid_until=evidence_valid,
            now=dt.datetime.now(dt.timezone.utc),
        )
        receipt_path = temporary / "receipt.json"
        descriptor = os.open(receipt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
        try:
            _write_all(descriptor, _canonical_json(receipt))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        _sign_receipt(receipt_path)
        directory_descriptor = os.open(temporary, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
        temporary.rename(final)
        temporary = None
        root_descriptor = os.open(RECEIPT_ROOT, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(root_descriptor)
        finally:
            os.close(root_descriptor)
        print(f"receipt_nonce={nonce}")
        print(f"receipt_path={final / 'receipt.json'}")
        print(f"receipt_signature_path={final / 'receipt.json.sig'}")
    finally:
        if temporary is not None and temporary.exists():
            shutil.rmtree(temporary)
        os.close(lock)


def _verify_receipt_command(args: argparse.Namespace, *, print_proof: bool = True) -> dict[str, Any]:
    _require_root_host()
    _verify_bootstrap(require_signing_key=False)
    receipt_path, signature_path, evidence_path = _receipt_paths(args.nonce)
    if args.receipt != receipt_path or args.receipt_signature != signature_path:
        raise TrustError("receipt paths must use the fixed root-owned nonce directory")
    receipt_bytes = _read_regular(receipt_path, label="backup receipt", maximum=MAX_JSON_BYTES, owner_uid=0, modes={0o400})
    _read_regular(signature_path, label="backup receipt signature", maximum=MAX_SIGNATURE_BYTES, owner_uid=0, modes={0o400})
    _verify_openssh_signature(
        receipt_bytes,
        signature_path,
        RECEIPT_ALLOWED_SIGNERS,
        principal=RECEIPT_PRINCIPAL,
        namespace=RECEIPT_NAMESPACE,
    )
    receipt = _parse_json(receipt_bytes, "backup receipt")
    _validate_receipt(
        receipt,
        now=dt.datetime.now(dt.timezone.utc),
        expected_nonce=args.nonce,
        expected_release_id=args.release_id,
        expected_commit=args.expected_commit,
        expected_archive_sha256=args.expected_archive_sha256,
        expected_tree_sha256=args.expected_tree_sha256,
    )
    evidence_bytes = _read_regular(evidence_path, label="backup attestation", maximum=MAX_JSON_BYTES, owner_uid=0, modes={0o400})
    if _sha256_bytes(evidence_bytes) != receipt["recovery_evidence"]["attestation_sha256"]:
        raise TrustError("retained backup evidence differs from the signed receipt")
    evidence = _parse_json(evidence_bytes, "backup attestation")
    _validate_evidence_document(evidence)
    _invoke_evidence_engine("verify", evidence_path)
    if print_proof:
        print(
            "MOONCEN_CONTROL_BACKUP_RECEIPT_VERIFIED="
            f"{args.nonce}:{args.expected_commit}:{args.expected_tree_sha256}"
        )
    return receipt


def _verify_candidate_command(args: argparse.Namespace) -> None:
    _require_root_host()
    _verify_bootstrap(require_signing_key=False)
    identity = _verify_candidate(
        args.candidate_dir,
        release_id=args.release_id,
        deploy_user=args.deploy_user,
        expected_commit=args.expected_commit,
        expected_archive_sha256=args.expected_archive_sha256,
        expected_tree_sha256=args.expected_tree_sha256,
    )
    print(
        "MOONCEN_CONTROL_CANDIDATE_VERIFIED="
        f"{identity.release_id}:{identity.deploy_commit}:{identity.archive_sha256}:{identity.tree_sha256}"
    )


def _consume_command(args: argparse.Namespace) -> None:
    _verify_receipt_command(args, print_proof=False)
    raise TrustError(
        "NOT READY: receipt consumption and the first crawler-control database write "
        "are not yet one PostgreSQL advisory/audit transaction; no receipt was consumed "
        "and no database mutation was attempted"
    )


def _identity_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--expected-archive-sha256", required=True)
    parser.add_argument("--expected-tree-sha256", required=True)


def _receipt_arguments(parser: argparse.ArgumentParser) -> None:
    _identity_arguments(parser)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--receipt-signature", required=True, type=Path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    bootstrap = subparsers.add_parser("verify-bootstrap")
    bootstrap.set_defaults(handler=lambda _args: print(f"root_trust_bootstrap={_verify_bootstrap(require_signing_key=False)}"))
    candidate = subparsers.add_parser("verify-candidate")
    _identity_arguments(candidate)
    candidate.add_argument("--deploy-user", required=True)
    candidate.add_argument("--candidate-dir", required=True, type=Path)
    candidate.set_defaults(handler=_verify_candidate_command)
    issue = subparsers.add_parser("issue-receipt")
    _identity_arguments(issue)
    issue.add_argument("--deploy-user", required=True)
    issue.add_argument("--candidate-dir", required=True, type=Path)
    issue.set_defaults(handler=_issue_command)
    verify = subparsers.add_parser("verify-receipt")
    _receipt_arguments(verify)
    verify.set_defaults(handler=_verify_receipt_command)
    consume = subparsers.add_parser("consume-receipt")
    _receipt_arguments(consume)
    consume.set_defaults(handler=_consume_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if getattr(args, "command", None) != "verify-bootstrap":
            # Every operational command performs its own stricter root/host check.
            pass
        else:
            _require_root_host()
        args.handler(args)
    except TrustError as exc:
        print(f"crawler-control root trust failed: {exc}", file=sys.stderr)
        return 70 if "NOT READY:" in str(exc) else 78
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
