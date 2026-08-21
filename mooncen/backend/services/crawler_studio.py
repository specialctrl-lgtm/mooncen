"""Pure validation helpers for the central Crawler Studio source repository."""

from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROVIDERS_MANIFEST = PROJECT_ROOT / "config" / "production_crawler_providers.yaml"
PROVIDER_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{0,99}")
SOURCE_PATH_PATTERN = re.compile(r"Crawler/[A-Za-z0-9_./-]{1,220}\.py")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
MAX_SOURCE_BYTES = 512 * 1024


class CrawlerStudioValidationError(ValueError):
    pass


@lru_cache(maxsize=1)
def reviewed_provider_paths() -> dict[str, frozenset[str]]:
    """Load a secret-free, committed provider/path allowlist.

    The production manifest is the provider authority. Entry points are
    derived from the already reviewed runner registry and are never supplied
    by a browser. Shared runner files legitimately map to multiple providers.
    """

    import yaml

    try:
        manifest = yaml.safe_load(PROVIDERS_MANIFEST.read_text(encoding="utf-8")) or {}
        raw_providers = manifest.get("providers")
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise CrawlerStudioValidationError("reviewed provider manifest is unavailable") from exc
    if manifest.get("version") != 1 or not isinstance(raw_providers, list) or not raw_providers:
        raise CrawlerStudioValidationError("reviewed provider manifest is invalid")
    providers = [str(item).strip().upper() for item in raw_providers]
    if len(providers) != len(set(providers)) or any(
        not PROVIDER_PATTERN.fullmatch(provider) for provider in providers
    ):
        raise CrawlerStudioValidationError("reviewed provider identities are invalid")

    try:
        from run_crawlers import PROVIDER_COMMANDS
    except (ImportError, RuntimeError, ValueError) as exc:
        raise CrawlerStudioValidationError("reviewed crawler registry is unavailable") from exc

    mapping: dict[str, frozenset[str]] = {}
    for provider in providers:
        command = PROVIDER_COMMANDS.get(provider)
        if not isinstance(command, list):
            raise CrawlerStudioValidationError(f"reviewed provider has no crawler: {provider}")
        script_path: str | None = None
        for script_index, argument in enumerate(command):
            if not isinstance(argument, str):
                raise CrawlerStudioValidationError("reviewed crawler command is invalid")
            if argument.endswith(".py"):
                script_path = "/".join(command[: script_index + 1]).replace("\\", "/")
                break
        if script_path is None:
            raise CrawlerStudioValidationError(f"reviewed provider has no source path: {provider}")
        canonical_path = validate_source_path(script_path)
        if not (PROJECT_ROOT / canonical_path).is_file():
            raise CrawlerStudioValidationError(
                f"reviewed provider source file is unavailable: {provider}"
            )
        mapping[provider] = frozenset({canonical_path})
    return mapping


def validate_provider_path(provider: str, source_path: str) -> tuple[str, str]:
    raw_provider = str(provider)
    normalized_provider = raw_provider.strip().upper()
    if raw_provider != normalized_provider:
        raise CrawlerStudioValidationError("provider must use its canonical reviewed identity")
    normalized_path = validate_source_path(source_path)
    try:
        allowed = reviewed_provider_paths()[normalized_provider]
    except KeyError as exc:
        raise CrawlerStudioValidationError("provider is outside the reviewed allowlist") from exc
    if normalized_path not in allowed:
        raise CrawlerStudioValidationError("source path is not reviewed for this provider")
    return normalized_provider, normalized_path


def validate_source_path(source_path: str) -> str:
    raw_path = str(source_path)
    if "\\" in raw_path or raw_path != raw_path.strip():
        raise CrawlerStudioValidationError("crawler source path is invalid")
    normalized = raw_path.strip()
    if not SOURCE_PATH_PATTERN.fullmatch(normalized):
        raise CrawlerStudioValidationError("crawler source path is invalid")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise CrawlerStudioValidationError("crawler source path escapes the reviewed root")
    return path.as_posix()


def validate_source_text(source: str, expected_sha256: str) -> tuple[str, bytes, str]:
    if not isinstance(source, str) or "\x00" in source:
        raise CrawlerStudioValidationError("crawler source must be UTF-8 text without NUL")
    try:
        encoded = source.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise CrawlerStudioValidationError("crawler source must be valid UTF-8") from exc
    if not 1 <= len(encoded) <= MAX_SOURCE_BYTES:
        raise CrawlerStudioValidationError("crawler source size is outside the allowed limit")
    digest = hashlib.sha256(encoded).hexdigest()
    expected = str(expected_sha256)
    if (
        expected != expected.strip().lower()
        or not SHA256_PATTERN.fullmatch(expected)
        or digest != expected
    ):
        raise CrawlerStudioValidationError("crawler source SHA-256 does not match exact UTF-8 bytes")
    return source, encoded, digest


def source_capabilities() -> dict[str, dict[str, Any]]:
    return {
        "draft_storage": {"available": True, "reason": None},
        "revision_history": {"available": True, "reason": None},
        "review_decision": {"available": True, "reason": None},
        "source_approval": {
            "available": False,
            "reason": "independent_source_approval_evidence_not_implemented",
        },
        "independent_release_approval": {
            "available": False,
            "reason": "independent_operator_approval_evidence_not_implemented",
        },
        "fixture_validation": {
            "available": False,
            "reason": "immutable_fixture_validation_runner_not_implemented",
        },
        "source_execution": {
            "available": False,
            "reason": "central_sandboxed_source_runner_not_implemented",
        },
        "build": {
            "available": False,
            "reason": "immutable_builder_evidence_handoff_not_implemented",
        },
        "sign": {"available": False, "reason": "signer_is_outside_ops_api"},
    }


def source_preview(source: str, *, maximum: int = 4_000) -> str:
    return source if len(source) <= maximum else source[:maximum] + "\n…"


def canonical_metadata(value: dict[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True).encode("utf-8")
    if len(encoded) > 8_192:
        raise CrawlerStudioValidationError("crawler draft metadata is too large")
    return value
