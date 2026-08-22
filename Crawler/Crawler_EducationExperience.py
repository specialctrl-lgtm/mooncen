from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse, urlunparse


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler import Crawler_GeneratedYamlTargets as generated_targets
from service_group import (
    EXCLUDED_DOMAIN_CATEGORIES,
    EXCLUDED_SOURCE_GROUPS,
    EXPERIENCE_KEYWORDS,
    SERVICE_GROUP_EXPERIENCE,
    SERVICE_GROUP_PUBLIC_COURSE,
    infer_service_group,
)


# These tokens intentionally inspect target metadata, not collected course text.
# They cover mixed public-reservation registries whose file-level service group is
# public education even though an individual target is explicitly an experience,
# exhibition, performance, or event source.
EXPERIENCE_TARGET_TOKENS = (*EXPERIENCE_KEYWORDS, "문화유산")
DEDICATED_EXPERIENCE_PROVIDERS = {
    "MUNI_LIB_ULJIN_GO_KR_84BA0199",
    "NATIONAL_MAP_MUSEUM",
    "NATIONAL_MUSEUM_OF_KOREA",
}
DEDICATED_EXPERIENCE_PROVIDER_PREFIXES = ("SUWON_LIBRARY_",)
MIXED_INSTITUTION_EXPERIENCE_PROVIDERS = {
    # The official catalogue contains several institutions. Its collector
    # classifies rows by the official branch name instead of promoting every
    # education-office program into the experience service group.
    "ULSAN_EDU_BOOKING",
}
load_yaml_targets = generated_targets.load_yaml_targets
main = generated_targets.main
target_url = generated_targets.target_url


def is_experience_target(target: dict[str, Any]) -> bool:
    provider = str(target.get("provider") or "").strip().upper()
    source_group = str(target.get("source_group") or "").strip().lower()
    domain_category = str(target.get("domain_category") or "").strip()
    if source_group in EXCLUDED_SOURCE_GROUPS or domain_category in EXCLUDED_DOMAIN_CATEGORIES:
        return False
    if (
        str(target.get("service_group_policy") or "").strip().lower() == "locked"
        and str(target.get("service_group") or "").strip() == SERVICE_GROUP_PUBLIC_COURSE
    ):
        return False
    if provider in MIXED_INSTITUTION_EXPERIENCE_PROVIDERS:
        return True

    inferred_group = infer_service_group(
        provider=target.get("provider"),
        collection_category=target.get("collection_category"),
        domain_category=target.get("domain_category"),
        source_group=target.get("source_group"),
        operator_type=target.get("operator_type"),
        branch_name=target.get("branch"),
        raw_url=target_url(target),
        service_group=target.get("service_group"),
    )
    if inferred_group == SERVICE_GROUP_EXPERIENCE:
        return True

    # Target names are not course titles: institution/activity words here are
    # source-level evidence and safely cover mixed reservation registries.
    metadata = " ".join(
        str(target.get(field) or "").strip()
        for field in (
            "name",
            "branch",
        )
    )
    return any(token in metadata for token in EXPERIENCE_TARGET_TOKENS)


def configured_provider_names(value: str | None = None) -> set[str]:
    raw = os.getenv("CRAWLER_PROVIDERS", "") if value is None else value
    providers = {
        token.strip().upper()
        for token in re.split(r"[\s,]+", str(raw or ""))
        if token.strip()
    }
    providers.discard("EXPERIENCE_TARGETS")
    return providers


def aggregate_owned_provider_names() -> set[str]:
    return {
        str(provider).strip().upper()
        for provider in generated_targets.MUNICIPAL_OPERATIONAL_PROVIDER_NAMES
        if str(provider).strip()
    }


def experience_provider_names(
    targets: Iterable[dict[str, Any]] | None = None,
    *,
    scheduled_providers: set[str] | None = None,
) -> list[str]:
    target_rows = list(targets) if targets is not None else load_yaml_targets()
    excluded = {
        str(provider).strip().upper() for provider in (scheduled_providers or set())
    }
    excluded.update(aggregate_owned_provider_names())
    return sorted(
        {
            str(target.get("provider") or "").strip().upper()
            for target in target_rows
            if is_experience_target(target)
            and str(target.get("provider") or "").strip()
            and str(target.get("provider") or "").strip().upper() not in excluded
        }
    )


# Compatibility for callers that inspect the old module constant. ``run`` still
# resolves the registry afresh so a newly deployed target is picked up without
# maintaining a second hard-coded provider list.
PROVIDERS = tuple(experience_provider_names())


def experience_main_url(target: dict[str, Any]) -> str:
    """Return the safe, stable site entry point used for menu discovery."""

    explicit = str(target.get("main_url") or target.get("base_url") or "").strip()
    if explicit:
        return explicit
    configured_url = target_url(target)
    parsed = urlparse(configured_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return urlunparse((parsed.scheme, parsed.netloc, "/", "", "", ""))


def target_has_verified_parser(target: dict[str, Any]) -> bool:
    provider = str(target.get("provider") or "").strip().upper()
    if provider in DEDICATED_EXPERIENCE_PROVIDERS or provider.startswith(
        DEDICATED_EXPERIENCE_PROVIDER_PREFIXES
    ):
        return True
    quality = target.get("last_quality")
    parser = (
        str(quality.get("parser") or "").strip().lower()
        if isinstance(quality, dict)
        else ""
    )
    if not parser or parser == "none":
        return False
    return not (
        parser.startswith("generic_")
        or "no_structured" in parser
        or "needs_parser" in parser
    )


def prepare_experience_target(target: dict[str, Any]) -> dict[str, Any]:
    """Enable bounded main-site discovery without mutating the registry row."""

    prepared = dict(target)
    if "discover_from_main_url" not in prepared and target_has_verified_parser(prepared):
        # A verified source-specific route is the ownership boundary. Crawling
        # arbitrary top-navigation links here can mix every branch into each
        # provider (notably the 19 Suwon library providers).
        prepared["discover_from_main_url"] = False
        return prepared

    main_url = experience_main_url(prepared)
    if main_url and prepared.get("discover_from_main_url") is not False:
        prepared["main_url"] = main_url
        prepared["discover_from_main_url"] = True
        prepared.setdefault("main_discovery_max_pages", 4)
        prepared.setdefault("main_discovery_max_candidates", 12)
    return prepared


def run() -> int:
    args = sys.argv[1:]
    scheduled_providers = configured_provider_names()
    has_provider = any(arg == "--provider" or arg.startswith("--provider=") for arg in args)
    aggregate_providers = set() if has_provider else aggregate_owned_provider_names()
    if not has_provider and "--all" not in args:
        # Keep the no-argument registry resolver as the public seam used by
        # wrappers/tests, then apply the runtime schedule exclusion locally.
        providers = [
            provider
            for provider in experience_provider_names()
            if provider not in scheduled_providers
        ]
        if not providers:
            print("No enabled experience targets were found.", file=sys.stderr)
            return 2
        provider_args: list[str] = []
        for provider in providers:
            provider_args.extend(["--provider", provider])
        args = [*provider_args, *args]

    original_argv = sys.argv[:]
    original_target_loader = generated_targets.load_yaml_targets

    def load_experience_targets(*loader_args, **loader_kwargs) -> list[dict[str, Any]]:
        return [
            prepare_experience_target(target)
            for target in original_target_loader(*loader_args, **loader_kwargs)
            if is_experience_target(target)
            and str(target.get("provider") or "").strip().upper()
            not in scheduled_providers | aggregate_providers
        ]

    try:
        # The generated CLI filters by provider. Restrict its source rows too so
        # mixed providers cannot pull unrelated lecture targets into this macro.
        generated_targets.load_yaml_targets = load_experience_targets
        sys.argv = [sys.argv[0], *args]
        return main()
    finally:
        generated_targets.load_yaml_targets = original_target_loader
        sys.argv = original_argv


if __name__ == "__main__":
    raise SystemExit(run())
