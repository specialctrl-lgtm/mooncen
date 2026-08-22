from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROVIDER_MANIFEST = PROJECT_ROOT / "config" / "production_crawler_providers.yaml"
DEFAULT_OWNERSHIP_MANIFEST = PROJECT_ROOT / "config" / "production_crawler_provider_ownership.json"
EXPERIENCE_AGGREGATE_OWNER = "EXPERIENCE_TARGETS"
MUNICIPAL_AGGREGATE_OWNER = "MUNICIPAL_RESERVATION_TARGETS"
AGGREGATE_PROVIDER_OWNERS = frozenset(
    {EXPERIENCE_AGGREGATE_OWNER, MUNICIPAL_AGGREGATE_OWNER}
)
PROVIDER_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{0,99}")


def _load_document(path: Path) -> tuple[dict[str, Any], bytes]:
    resolved = path.resolve()
    if not resolved.is_file() or resolved.is_symlink():
        raise RuntimeError("Crawler provider ownership manifest must be a regular file")
    raw = resolved.read_bytes()
    if not raw or len(raw) > 1_048_576:
        raise RuntimeError("Crawler provider ownership manifest size is invalid")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Crawler provider ownership manifest is invalid") from exc
    if not isinstance(document, dict):
        raise RuntimeError("Crawler provider ownership manifest must be an object")
    return document, raw


def reviewed_provider_output_scopes(
    provider_manifest: Path = DEFAULT_PROVIDER_MANIFEST,
    ownership_manifest: Path = DEFAULT_OWNERSHIP_MANIFEST,
) -> dict[str, tuple[str, ...]]:
    document, _raw = _load_document(ownership_manifest)
    if document.get("format") != "mooncen-crawler-provider-ownership-v1":
        raise RuntimeError("Crawler provider ownership manifest format is invalid")
    expected_provider_digest = document.get("providers_manifest_sha256")
    if not isinstance(expected_provider_digest, str) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_provider_digest
    ):
        raise RuntimeError("Crawler provider ownership manifest digest is invalid")
    resolved_provider_manifest = provider_manifest.resolve()
    if resolved_provider_manifest.is_dir():
        resolved_provider_manifest = (
            resolved_provider_manifest / "config" / "production_crawler_providers.yaml"
        )
    if not resolved_provider_manifest.is_file() or resolved_provider_manifest.is_symlink():
        raise RuntimeError("Crawler provider manifest must be a regular file")
    provider_bytes = resolved_provider_manifest.read_bytes()
    if hashlib.sha256(provider_bytes).hexdigest() != expected_provider_digest:
        raise RuntimeError("Crawler provider and ownership manifests are not the same reviewed revision")

    raw_scopes = document.get("scheduled_providers")
    if not isinstance(raw_scopes, dict) or not raw_scopes or len(raw_scopes) > 512:
        raise RuntimeError("Crawler provider ownership scopes are invalid")
    scopes: dict[str, tuple[str, ...]] = {}
    concrete_owners: dict[str, str] = {}
    for raw_owner, raw_providers in raw_scopes.items():
        owner = str(raw_owner or "").strip().upper()
        if not PROVIDER_PATTERN.fullmatch(owner) or not isinstance(raw_providers, list):
            raise RuntimeError("Crawler provider ownership entry is invalid")
        providers = tuple(str(value or "").strip().upper() for value in raw_providers)
        if (
            not providers
            or len(providers) > 512
            or len(providers) != len(set(providers))
            or any(not PROVIDER_PATTERN.fullmatch(value) for value in providers)
        ):
            raise RuntimeError(f"Crawler provider output scope is invalid: {owner}")
        if owner not in AGGREGATE_PROVIDER_OWNERS and providers != (owner,):
            raise RuntimeError(f"Non-aggregate crawler output scope is not self-owned: {owner}")
        for concrete in providers:
            previous = concrete_owners.setdefault(concrete, owner)
            if previous != owner:
                raise RuntimeError("Concrete crawler provider has multiple scheduled owners")
        scopes[owner] = tuple(sorted(providers))
    return dict(sorted(scopes.items()))


def reviewed_scheduled_crawler_providers(
    provider_manifest: Path = DEFAULT_PROVIDER_MANIFEST,
) -> frozenset[str]:
    return frozenset(reviewed_provider_output_scopes(provider_manifest))


def reviewed_crawler_providers(
    provider_manifest: Path = DEFAULT_PROVIDER_MANIFEST,
) -> frozenset[str]:
    scopes = reviewed_provider_output_scopes(provider_manifest)
    return frozenset(scopes) | frozenset(
        concrete for providers in scopes.values() for concrete in providers
    )


def build_course_provider_owners(
    providers: list[str],
    provider_manifest: Path = DEFAULT_PROVIDER_MANIFEST,
) -> dict[str, str]:
    normalized = tuple(str(provider or "").strip().upper() for provider in providers)
    if not normalized or len(normalized) != len(set(normalized)):
        raise ValueError("scheduled providers must be non-empty and unique")
    scopes = reviewed_provider_output_scopes(provider_manifest)
    all_concrete = {concrete for values in scopes.values() for concrete in values}
    unknown = sorted(set(normalized) - set(scopes) - all_concrete)
    if unknown:
        raise ValueError("scheduled providers are absent from the reviewed ownership manifest")
    owners: dict[str, str] = {}
    for owner in normalized:
        concrete_scope = scopes.get(owner, (owner,))
        for concrete in concrete_scope:
            previous = owners.setdefault(concrete, owner)
            if previous != owner:
                raise ValueError("course provider has conflicting scheduled owners")
    return dict(sorted(owners.items()))
