"""Canonical, path-free crawler payload builder evidence contracts.

The Ops API is not a producer of these documents.  An isolated builder may
consume a database-issued ticket and emit builder evidence; an isolated signer
may later append a detached public signature.  None of the contracts contain a
repository path, artifact path, private-key path, command, URL, or secret.

This module intentionally validates structure only.  Authoritative issuance of
``BuilderTicket`` remains database-gated until independent Studio source
approval exists.  A structurally valid JSON document is therefore not, by
itself, authorization to build, sign, register, or roll out anything.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Final, Mapping, Sequence
from uuid import UUID


TICKET_FORMAT: Final = "mooncen-crawler-payload-builder-ticket-v1"
BUILDER_EVIDENCE_FORMAT: Final = "mooncen-crawler-payload-builder-evidence-v1"
SIGNER_EVIDENCE_FORMAT: Final = "mooncen-crawler-payload-signer-evidence-v1"
SIGNATURE_NAMESPACE: Final = "mooncen-crawler-release-v1"

ENVIRONMENTS: Final = frozenset({"production", "staging", "development"})
TEST_PROFILES: Final = frozenset({"crawler", "crawler_full"})
BLOCKED_REGISTRATION_REASONS: Final = (
    "independent_source_approval_verification_not_implemented",
    "isolated_test_evidence_not_implemented",
    "release_agent_exact_payload_manifest_verification_not_implemented",
    "isolated_signer_handoff_not_implemented",
)

_OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_CONFIG_REVISION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_LOGIN = re.compile(r"[a-z_][a-z0-9_]{0,62}")
_KEY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@-]{0,127}")
_SOURCE_PATH = re.compile(r"Crawler/[A-Za-z0-9_./-]+[.]py")
MAX_SOURCES: Final = 42
MAX_SIGNATURE_BYTES: Final = 64 * 1024


class BuilderEvidenceError(ValueError):
    """A builder handoff document is not canonical or safely bounded."""


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    """Return the one evidence encoding used for every persisted digest."""

    try:
        encoded = json.dumps(
            dict(value),
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise BuilderEvidenceError("evidence is not canonical JSON") from exc
    return encoded + b"\n"


def document_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _exact_fields(value: Mapping[str, Any], expected: set[str], *, label: str) -> None:
    if type(value) is not dict or set(value) != expected:
        raise BuilderEvidenceError(f"{label} fields are invalid")


def _canonical_uuid(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise BuilderEvidenceError(f"{label} is not a canonical UUID")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise BuilderEvidenceError(f"{label} is not a canonical UUID") from exc
    if parsed.int == 0 or str(parsed) != value:
        raise BuilderEvidenceError(f"{label} is not a canonical non-nil UUID")
    return value


def _identity(value: Any, pattern: re.Pattern[str], *, label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise BuilderEvidenceError(f"{label} is invalid")
    return value


def _positive_int(value: Any, *, label: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise BuilderEvidenceError(f"{label} is invalid")
    return value


def _timestamp(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z") or len(value) > 40:
        raise BuilderEvidenceError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise BuilderEvidenceError(f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise BuilderEvidenceError(f"{label} must be UTC")
    canonical = parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")
    if value != canonical:
        raise BuilderEvidenceError(f"{label} is not canonical")
    return value


def _source_path(value: Any) -> str:
    path = _identity(value, _SOURCE_PATH, label="source path")
    parts = path.split("/")
    if (
        any(part in {"", ".", ".."} for part in parts)
        or "\\" in path
        or path != path.encode("ascii", "strict").decode("ascii")
        or path.casefold() != path.lower()
    ):
        raise BuilderEvidenceError("source path is not canonical ASCII")
    return path


@dataclass(frozen=True)
class SourceRevision:
    draft_id: str
    revision: int
    source_path: str
    source_sha256: str

    @classmethod
    def parse(cls, raw: Mapping[str, Any]) -> SourceRevision:
        _exact_fields(
            raw,
            {"draft_id", "revision", "source_path", "source_sha256"},
            label="source revision",
        )
        return cls(
            draft_id=_canonical_uuid(raw["draft_id"], label="draft id"),
            revision=_positive_int(raw["revision"], label="revision", maximum=2_147_483_647),
            source_path=_source_path(raw["source_path"]),
            source_sha256=_identity(raw["source_sha256"], _SHA256, label="source SHA-256"),
        )


@dataclass(frozen=True)
class BuilderTicket:
    format: str
    ticket_id: str
    build_request_id: str
    environment: str
    request_digest: str
    source_commit: str
    source_tree: str
    code_version: str
    config_revision: str
    test_profile: str
    source_approval_receipt_id: str
    source_approval_digest: str
    source_approver_login: str
    source_approved_at: str
    sources: tuple[SourceRevision, ...]

    @classmethod
    def parse(cls, raw: Mapping[str, Any]) -> BuilderTicket:
        _exact_fields(
            raw,
            {
                "format",
                "ticket_id",
                "build_request_id",
                "environment",
                "request_digest",
                "source_commit",
                "source_tree",
                "code_version",
                "config_revision",
                "test_profile",
                "source_approval_receipt_id",
                "source_approval_digest",
                "source_approver_login",
                "source_approved_at",
                "sources",
            },
            label="builder ticket",
        )
        if raw["format"] != TICKET_FORMAT:
            raise BuilderEvidenceError("builder ticket format is invalid")
        environment = raw["environment"]
        profile = raw["test_profile"]
        if environment not in ENVIRONMENTS or profile not in TEST_PROFILES:
            raise BuilderEvidenceError("builder ticket environment or test profile is invalid")
        source_commit = _identity(raw["source_commit"], _OID, label="source commit")
        source_tree = _identity(raw["source_tree"], _OID, label="source tree")
        if len(source_commit) != len(source_tree):
            raise BuilderEvidenceError("source commit and tree object formats differ")
        raw_sources = raw["sources"]
        if type(raw_sources) is not list or not 1 <= len(raw_sources) <= MAX_SOURCES:
            raise BuilderEvidenceError("builder ticket sources are invalid")
        sources = tuple(SourceRevision.parse(item) for item in raw_sources)
        paths = [item.source_path for item in sources]
        draft_revisions = [(item.draft_id, item.revision) for item in sources]
        if (
            paths != sorted(paths, key=lambda item: item.encode("ascii"))
            or len(paths) != len(set(paths))
            or len(draft_revisions) != len(set(draft_revisions))
        ):
            raise BuilderEvidenceError("builder ticket sources are not unique canonical order")
        return cls(
            format=TICKET_FORMAT,
            ticket_id=_canonical_uuid(raw["ticket_id"], label="ticket id"),
            build_request_id=_canonical_uuid(raw["build_request_id"], label="build request id"),
            environment=environment,
            request_digest=_identity(raw["request_digest"], _SHA256, label="request digest"),
            source_commit=source_commit,
            source_tree=source_tree,
            code_version=_identity(raw["code_version"], _VERSION, label="code version"),
            config_revision=_identity(
                raw["config_revision"], _CONFIG_REVISION, label="config revision"
            ),
            test_profile=profile,
            source_approval_receipt_id=_canonical_uuid(
                raw["source_approval_receipt_id"], label="source approval receipt id"
            ),
            source_approval_digest=_identity(
                raw["source_approval_digest"], _SHA256, label="source approval digest"
            ),
            source_approver_login=_identity(
                raw["source_approver_login"], _LOGIN, label="source approver login"
            ),
            source_approved_at=_timestamp(raw["source_approved_at"], label="source approved at"),
            sources=sources,
        )

    def document(self) -> dict[str, Any]:
        result = asdict(self)
        result["sources"] = [asdict(item) for item in self.sources]
        return result

    @property
    def digest(self) -> str:
        return document_sha256(self.document())


@dataclass(frozen=True)
class BuilderEvidence:
    document: dict[str, Any]

    @classmethod
    def parse(cls, raw: Mapping[str, Any]) -> BuilderEvidence:
        fields = {
            "format",
            "ticket_id",
            "ticket_digest",
            "build_request_id",
            "environment",
            "request_digest",
            "source_commit",
            "source_commit_object_sha256",
            "source_tree",
            "source_tree_object_sha256",
            "runtime_allowlist_sha256",
            "code_version",
            "config_revision",
            "test_profile",
            "source_approval_receipt_id",
            "source_approval_digest",
            "archive_sha256",
            "archive_size_bytes",
            "content_manifest_sha256",
            "content_manifest_size_bytes",
            "file_count",
            "input_size_bytes",
            "object_count",
            "registration_ready",
            "blocked_reasons",
        }
        _exact_fields(raw, fields, label="builder evidence")
        if raw["format"] != BUILDER_EVIDENCE_FORMAT:
            raise BuilderEvidenceError("builder evidence format is invalid")
        for field in (
            "ticket_digest",
            "request_digest",
            "source_commit_object_sha256",
            "source_tree_object_sha256",
            "runtime_allowlist_sha256",
            "source_approval_digest",
            "archive_sha256",
            "content_manifest_sha256",
        ):
            _identity(raw[field], _SHA256, label=field.replace("_", " "))
        commit = _identity(raw["source_commit"], _OID, label="source commit")
        tree = _identity(raw["source_tree"], _OID, label="source tree")
        if len(commit) != len(tree):
            raise BuilderEvidenceError("builder evidence object formats differ")
        if raw["environment"] not in ENVIRONMENTS or raw["test_profile"] not in TEST_PROFILES:
            raise BuilderEvidenceError("builder evidence environment or test profile is invalid")
        _identity(raw["code_version"], _VERSION, label="code version")
        _identity(raw["config_revision"], _CONFIG_REVISION, label="config revision")
        _canonical_uuid(raw["ticket_id"], label="ticket id")
        _canonical_uuid(raw["build_request_id"], label="build request id")
        _canonical_uuid(raw["source_approval_receipt_id"], label="source approval receipt id")
        _positive_int(raw["archive_size_bytes"], label="archive size", maximum=512 * 1024 * 1024)
        _positive_int(
            raw["content_manifest_size_bytes"],
            label="content manifest size",
            maximum=8 * 1024 * 1024,
        )
        _positive_int(raw["file_count"], label="file count", maximum=30_000)
        _positive_int(raw["input_size_bytes"], label="input size", maximum=2 * 1024 * 1024 * 1024)
        _positive_int(raw["object_count"], label="object count", maximum=100_000)
        if raw["registration_ready"] is not False:
            raise BuilderEvidenceError("builder evidence must remain non-registrable")
        if raw["blocked_reasons"] != list(BLOCKED_REGISTRATION_REASONS):
            raise BuilderEvidenceError("builder evidence blocked reasons are invalid")
        document = dict(raw)
        canonical_json_bytes(document)
        return cls(document=document)

    @property
    def digest(self) -> str:
        return document_sha256(self.document)


@dataclass(frozen=True)
class SignerEvidence:
    document: dict[str, Any]
    signature_bytes: bytes

    @classmethod
    def parse(cls, raw: Mapping[str, Any]) -> SignerEvidence:
        _exact_fields(
            raw,
            {
                "format",
                "builder_evidence_digest",
                "artifact_digest",
                "content_manifest_digest",
                "signature_namespace",
                "key_id",
                "signature",
                "signature_sha256",
                "signed_at",
            },
            label="signer evidence",
        )
        if raw["format"] != SIGNER_EVIDENCE_FORMAT:
            raise BuilderEvidenceError("signer evidence format is invalid")
        for field in (
            "builder_evidence_digest",
            "artifact_digest",
            "content_manifest_digest",
            "signature_sha256",
        ):
            _identity(raw[field], _SHA256, label=field.replace("_", " "))
        if raw["signature_namespace"] != SIGNATURE_NAMESPACE:
            raise BuilderEvidenceError("signer evidence namespace is invalid")
        _identity(raw["key_id"], _KEY_ID, label="signer key id")
        _timestamp(raw["signed_at"], label="signed at")
        signature = raw["signature"]
        if not isinstance(signature, str) or len(signature) > MAX_SIGNATURE_BYTES * 2:
            raise BuilderEvidenceError("detached signature is invalid")
        try:
            decoded = base64.b64decode(signature, validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise BuilderEvidenceError("detached signature is invalid") from exc
        if not 1 <= len(decoded) <= MAX_SIGNATURE_BYTES:
            raise BuilderEvidenceError("detached signature size is invalid")
        if hashlib.sha256(decoded).hexdigest() != raw["signature_sha256"]:
            raise BuilderEvidenceError("detached signature digest differs")
        document = dict(raw)
        canonical_json_bytes(document)
        return cls(document=document, signature_bytes=decoded)

    @property
    def digest(self) -> str:
        return document_sha256(self.document)


def load_ticket_json(data: bytes, *, maximum: int = 256 * 1024) -> BuilderTicket:
    if not 1 <= len(data) <= maximum:
        raise BuilderEvidenceError("builder ticket size is invalid")
    try:
        raw = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_constant,
        )
    except BuilderEvidenceError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BuilderEvidenceError("builder ticket is invalid JSON") from exc
    if type(raw) is not dict:
        raise BuilderEvidenceError("builder ticket must be an object")
    ticket = BuilderTicket.parse(raw)
    if data != canonical_json_bytes(ticket.document()):
        raise BuilderEvidenceError("builder ticket bytes are not canonical")
    return ticket


def _reject_duplicate_pairs(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BuilderEvidenceError("evidence contains a duplicate field")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise BuilderEvidenceError("evidence contains a non-finite number")


__all__ = [
    "BLOCKED_REGISTRATION_REASONS",
    "BUILDER_EVIDENCE_FORMAT",
    "BuilderEvidence",
    "BuilderEvidenceError",
    "BuilderTicket",
    "SIGNATURE_NAMESPACE",
    "SIGNER_EVIDENCE_FORMAT",
    "SignerEvidence",
    "SourceRevision",
    "TICKET_FORMAT",
    "canonical_json_bytes",
    "document_sha256",
    "load_ticket_json",
]
