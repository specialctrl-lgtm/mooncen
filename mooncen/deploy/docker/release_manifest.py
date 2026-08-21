#!/usr/bin/env python3
"""Canonical Docker release and development-validation evidence.

MoonCen does not currently depend on a container registry.  A promoted release
is therefore identified by the SHA-256 of a ``docker image save`` bundle, the
exact local Docker image IDs restored from that bundle, and the reviewed
Compose/policy digests.  Mutable tags are retained only as local lookup names;
they are never sufficient evidence on their own.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = 1
MAX_MANIFEST_BYTES = 256 * 1024
GIT_OBJECT_PATTERN = re.compile(r"\A[0-9a-f]{40}\Z")
SHA256_PATTERN = re.compile(r"\A[0-9a-f]{64}\Z")
IMAGE_ID_PATTERN = re.compile(r"\Asha256:[0-9a-f]{64}\Z")
IMAGE_TAG_PATTERN = re.compile(r"\Amooncen/(?P<service>api|frontend):release-(?P<tree>[0-9a-f]{40})\Z")
PLATFORM_PATTERN = re.compile(r"\Alinux/(amd64|arm64)\Z")
TARGET_PATTERN = re.compile(r"\A[a-z][a-z0-9_-]{0,31}\Z")
UTC_TIMESTAMP_PATTERN = re.compile(r"\A20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")

RELEASE_KEYS = frozenset(
    {
        "schema_version",
        "release_digest",
        "base_commit",
        "source_tree",
        "snapshot_commit",
        "platform",
        "bundle_sha256",
        "compose_sha256",
        "build_policy_sha256",
        "migration_ledger_sha256",
        "images",
        "created_at",
    }
)
IMAGE_KEYS = frozenset({"tag", "image_id"})
IMAGE_SERVICES = frozenset({"api", "frontend"})
VALIDATION_KEYS = frozenset(
    {
        "schema_version",
        "receipt_digest",
        "release_digest",
        "source_tree",
        "target",
        "target_identity",
        "platform",
        "bundle_sha256",
        "compose_sha256",
        "image_ids",
        "checks",
        "status",
        "validated_at",
        "expires_at",
    }
)
VALIDATION_CHECKS = frozenset(
    {
        "migration_ledger",
        "api_health",
        "frontend_health",
        "protected_routes",
        "database_least_privilege",
        "runtime_hardening",
    }
)


class ManifestError(ValueError):
    """Raised when release evidence is not canonical or trustworthy."""


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise ManifestError("release evidence is not canonical JSON") from exc


def _digest_payload(value: Mapping[str, Any], *, digest_key: str) -> str:
    unsigned = dict(value)
    unsigned.pop(digest_key, None)
    return hashlib.sha256(_canonical_json(unsigned)).hexdigest()


def _exact_keys(value: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    keys = frozenset(value)
    if keys != expected:
        missing = sorted(expected - keys)
        unknown = sorted(keys - expected)
        raise ManifestError(f"{label} fields are invalid (missing={missing}, unknown={unknown})")


def _required_string(
    value: Any,
    *,
    field: str,
    pattern: re.Pattern[str],
) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise ManifestError(f"{field} is invalid")
    return value


def _timestamp(value: Any, *, field: str) -> str:
    text = _required_string(value, field=field, pattern=UTC_TIMESTAMP_PATTERN)
    try:
        parsed = datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ManifestError(f"{field} is invalid") from exc
    if parsed.year < 2020:
        raise ManifestError(f"{field} is invalid")
    return text


def _validate_image_records(
    value: Any,
    *,
    source_tree: str,
) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict) or frozenset(value) != IMAGE_SERVICES:
        raise ManifestError("images must contain exactly api and frontend")
    normalized: dict[str, dict[str, str]] = {}
    seen_ids: set[str] = set()
    for service in sorted(IMAGE_SERVICES):
        record = value.get(service)
        if not isinstance(record, dict):
            raise ManifestError(f"images.{service} must be an object")
        _exact_keys(record, IMAGE_KEYS, f"images.{service}")
        tag = _required_string(record.get("tag"), field=f"images.{service}.tag", pattern=IMAGE_TAG_PATTERN)
        match = IMAGE_TAG_PATTERN.fullmatch(tag)
        assert match is not None
        if match.group("service") != service or match.group("tree") != source_tree:
            raise ManifestError(f"images.{service}.tag is not bound to the source tree")
        image_id = _required_string(
            record.get("image_id"),
            field=f"images.{service}.image_id",
            pattern=IMAGE_ID_PATTERN,
        )
        if image_id in seen_ids:
            raise ManifestError("api and frontend image IDs must be distinct")
        seen_ids.add(image_id)
        normalized[service] = {"tag": tag, "image_id": image_id}
    return normalized


def validate_release_manifest(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError("release manifest must be an object")
    _exact_keys(value, RELEASE_KEYS, "release manifest")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ManifestError("release manifest schema_version is unsupported")
    source_tree = _required_string(value.get("source_tree"), field="source_tree", pattern=GIT_OBJECT_PATTERN)
    normalized: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "release_digest": _required_string(
            value.get("release_digest"),
            field="release_digest",
            pattern=SHA256_PATTERN,
        ),
        "base_commit": _required_string(value.get("base_commit"), field="base_commit", pattern=GIT_OBJECT_PATTERN),
        "source_tree": source_tree,
        "snapshot_commit": _required_string(
            value.get("snapshot_commit"),
            field="snapshot_commit",
            pattern=GIT_OBJECT_PATTERN,
        ),
        "platform": _required_string(value.get("platform"), field="platform", pattern=PLATFORM_PATTERN),
        "bundle_sha256": _required_string(value.get("bundle_sha256"), field="bundle_sha256", pattern=SHA256_PATTERN),
        "compose_sha256": _required_string(value.get("compose_sha256"), field="compose_sha256", pattern=SHA256_PATTERN),
        "build_policy_sha256": _required_string(
            value.get("build_policy_sha256"),
            field="build_policy_sha256",
            pattern=SHA256_PATTERN,
        ),
        "migration_ledger_sha256": _required_string(
            value.get("migration_ledger_sha256"),
            field="migration_ledger_sha256",
            pattern=SHA256_PATTERN,
        ),
        "images": _validate_image_records(value.get("images"), source_tree=source_tree),
        "created_at": _timestamp(value.get("created_at"), field="created_at"),
    }
    expected = _digest_payload(normalized, digest_key="release_digest")
    if normalized["release_digest"] != expected:
        raise ManifestError("release_digest does not match the canonical manifest")
    return normalized


def create_release_manifest(
    *,
    base_commit: str,
    source_tree: str,
    snapshot_commit: str,
    platform: str,
    bundle_sha256: str,
    compose_sha256: str,
    build_policy_sha256: str,
    migration_ledger_sha256: str,
    images: Mapping[str, Mapping[str, str]],
    created_at: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "release_digest": "0" * 64,
        "base_commit": base_commit,
        "source_tree": source_tree,
        "snapshot_commit": snapshot_commit,
        "platform": platform,
        "bundle_sha256": bundle_sha256,
        "compose_sha256": compose_sha256,
        "build_policy_sha256": build_policy_sha256,
        "migration_ledger_sha256": migration_ledger_sha256,
        "images": {key: dict(record) for key, record in images.items()},
        "created_at": created_at,
    }
    payload["release_digest"] = _digest_payload(payload, digest_key="release_digest")
    return validate_release_manifest(payload)


def validate_validation_receipt(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError("validation receipt must be an object")
    _exact_keys(value, VALIDATION_KEYS, "validation receipt")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ManifestError("validation receipt schema_version is unsupported")
    source_tree = _required_string(value.get("source_tree"), field="source_tree", pattern=GIT_OBJECT_PATTERN)
    image_ids = value.get("image_ids")
    if not isinstance(image_ids, dict) or frozenset(image_ids) != IMAGE_SERVICES:
        raise ManifestError("image_ids must contain exactly api and frontend")
    normalized_image_ids = {
        service: _required_string(image_ids[service], field=f"image_ids.{service}", pattern=IMAGE_ID_PATTERN)
        for service in sorted(IMAGE_SERVICES)
    }
    if len(set(normalized_image_ids.values())) != len(normalized_image_ids):
        raise ManifestError("api and frontend image IDs must be distinct")
    checks = value.get("checks")
    if not isinstance(checks, dict) or frozenset(checks) != VALIDATION_CHECKS:
        raise ManifestError("validation checks are incomplete")
    if any(type(checks[name]) is not bool for name in VALIDATION_CHECKS):
        raise ManifestError("validation check values must be boolean")
    status = value.get("status")
    if status not in {"passed", "failed"}:
        raise ManifestError("validation status is invalid")
    if (status == "passed") != all(checks.values()):
        raise ManifestError("validation status does not match the check results")
    validated_at = _timestamp(value.get("validated_at"), field="validated_at")
    expires_at = _timestamp(value.get("expires_at"), field="expires_at")
    if expires_at <= validated_at:
        raise ManifestError("validation receipt expiry must follow validation time")
    normalized: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "receipt_digest": _required_string(
            value.get("receipt_digest"),
            field="receipt_digest",
            pattern=SHA256_PATTERN,
        ),
        "release_digest": _required_string(
            value.get("release_digest"),
            field="release_digest",
            pattern=SHA256_PATTERN,
        ),
        "source_tree": source_tree,
        "target": _required_string(value.get("target"), field="target", pattern=TARGET_PATTERN),
        "target_identity": _required_string(
            value.get("target_identity"),
            field="target_identity",
            pattern=SHA256_PATTERN,
        ),
        "platform": _required_string(value.get("platform"), field="platform", pattern=PLATFORM_PATTERN),
        "bundle_sha256": _required_string(value.get("bundle_sha256"), field="bundle_sha256", pattern=SHA256_PATTERN),
        "compose_sha256": _required_string(value.get("compose_sha256"), field="compose_sha256", pattern=SHA256_PATTERN),
        "image_ids": normalized_image_ids,
        "checks": {name: checks[name] for name in sorted(VALIDATION_CHECKS)},
        "status": status,
        "validated_at": validated_at,
        "expires_at": expires_at,
    }
    expected = _digest_payload(normalized, digest_key="receipt_digest")
    if normalized["receipt_digest"] != expected:
        raise ManifestError("receipt_digest does not match the canonical receipt")
    return normalized


def create_validation_receipt(
    *,
    release: Mapping[str, Any],
    target: str,
    target_identity: str,
    checks: Mapping[str, bool],
    validated_at: str,
    expires_at: str,
) -> dict[str, Any]:
    trusted_release = validate_release_manifest(dict(release))
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "receipt_digest": "0" * 64,
        "release_digest": trusted_release["release_digest"],
        "source_tree": trusted_release["source_tree"],
        "target": target,
        "target_identity": target_identity,
        "platform": trusted_release["platform"],
        "bundle_sha256": trusted_release["bundle_sha256"],
        "compose_sha256": trusted_release["compose_sha256"],
        "image_ids": {service: trusted_release["images"][service]["image_id"] for service in sorted(IMAGE_SERVICES)},
        "checks": dict(checks),
        "status": "passed" if all(checks.values()) else "failed",
        "validated_at": validated_at,
        "expires_at": expires_at,
    }
    payload["receipt_digest"] = _digest_payload(payload, digest_key="receipt_digest")
    return validate_validation_receipt(payload)


def load_json_evidence(path: Path, *, receipt: bool = False) -> dict[str, Any]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ManifestError("release evidence cannot be read") from exc
    if path.is_symlink() or not path.is_file() or metadata.st_size > MAX_MANIFEST_BYTES:
        raise ManifestError("release evidence path is unsafe")
    try:
        value = json.loads(path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError("release evidence is not valid JSON") from exc
    return validate_validation_receipt(value) if receipt else validate_release_manifest(value)


def write_json_evidence(path: Path, value: Mapping[str, Any], *, receipt: bool = False) -> None:
    normalized = validate_validation_receipt(dict(value)) if receipt else validate_release_manifest(dict(value))
    parent = path.parent.resolve(strict=True)
    if path.parent.is_symlink() or path.exists() or path.is_symlink():
        raise ManifestError("release evidence output path already exists or is unsafe")
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_canonical_json(normalized) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o644)
        os.replace(temporary, path)
        temporary = None
    except OSError as exc:
        raise ManifestError("release evidence could not be written atomically") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


@dataclass(frozen=True)
class PromotionEvidence:
    release: dict[str, Any]
    receipt: dict[str, Any]


def bind_promotion_evidence(
    release: Mapping[str, Any],
    receipt: Mapping[str, Any],
    *,
    now: str,
) -> PromotionEvidence:
    evidence = bind_validation_evidence(release, receipt)
    _timestamp(now, field="now")
    if evidence.receipt["expires_at"] <= now:
        raise ManifestError("development validation receipt has expired")
    return evidence


def bind_validation_evidence(
    release: Mapping[str, Any],
    receipt: Mapping[str, Any],
) -> PromotionEvidence:
    """Bind a canonical PASS receipt without applying a promotion-time TTL.

    Receipt expiry gates a *new* production promotion.  A previously activated
    development runtime may still converge on boot as long as the immutable
    release, target, policy, and image identities remain exact.
    """

    trusted_release = validate_release_manifest(dict(release))
    trusted_receipt = validate_validation_receipt(dict(receipt))
    comparisons = {
        "release_digest": trusted_release["release_digest"],
        "source_tree": trusted_release["source_tree"],
        "platform": trusted_release["platform"],
        "bundle_sha256": trusted_release["bundle_sha256"],
        "compose_sha256": trusted_release["compose_sha256"],
    }
    for field, expected in comparisons.items():
        if trusted_receipt[field] != expected:
            raise ManifestError(f"validation receipt {field} does not match the release")
    expected_image_ids = {service: trusted_release["images"][service]["image_id"] for service in sorted(IMAGE_SERVICES)}
    if trusted_receipt["image_ids"] != expected_image_ids:
        raise ManifestError("validation receipt image IDs do not match the release")
    if trusted_receipt["status"] != "passed":
        raise ManifestError("only a passed development receipt can be promoted")
    return PromotionEvidence(release=trusted_release, receipt=trusted_receipt)
