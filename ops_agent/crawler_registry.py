from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MUNICIPAL_AGGREGATE_OWNER = "MUNICIPAL_RESERVATION_TARGETS"
EXPERIENCE_AGGREGATE_OWNER = "EXPERIENCE_TARGETS"
MAX_CRAWLER_PROVIDER_ENV_BYTES = 48 * 1024
PROVIDER_NAME_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{0,99}")


class CrawlerProviderRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class CrawlerProviderExecution:
    requested_provider: str
    scheduled_provider: str
    environment: dict[str, str]


def _ensure_project_importable(project_root: Path) -> None:
    project_path = str(project_root.resolve())
    if project_path not in sys.path:
        sys.path.insert(0, project_path)


@lru_cache(maxsize=4)
def _provider_sets(
    project_root_value: str,
) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    project_root = Path(project_root_value).resolve()
    _ensure_project_importable(project_root)
    try:
        from Crawler.Crawler_EducationExperience import experience_provider_names
        from Crawler.Crawler_MunicipalIntegratedReservation import load_operational_entries
        from run_crawlers import PROVIDER_ADAPTERS

        registered = frozenset(str(provider).strip().upper() for provider in PROVIDER_ADAPTERS)
        operational = frozenset(
            str(row.get("provider") or "").strip().upper()
            for row in load_operational_entries()
            if str(row.get("provider") or "").strip()
        )
        experience = frozenset(experience_provider_names())
    except Exception as exc:
        raise CrawlerProviderRegistryError(
            "crawler provider registry could not be validated"
        ) from exc
    if any(not PROVIDER_NAME_PATTERN.fullmatch(provider) for provider in operational | experience):
        raise CrawlerProviderRegistryError("aggregate concrete provider registry is invalid")
    if operational & experience:
        raise CrawlerProviderRegistryError("aggregate concrete provider ownership overlaps")
    return registered, operational, experience


def reviewed_crawler_providers(project_root: Path = PROJECT_ROOT) -> frozenset[str]:
    registered, operational, experience = _provider_sets(str(project_root.resolve()))
    if MUNICIPAL_AGGREGATE_OWNER not in registered:
        operational = frozenset()
    if EXPERIENCE_AGGREGATE_OWNER not in registered:
        experience = frozenset()
    return registered | operational | experience


def resolve_crawler_provider_execution(
    provider: str,
    project_root: Path = PROJECT_ROOT,
    *,
    scheduled_provider: str | None = None,
) -> CrawlerProviderExecution:
    normalized = str(provider or "").strip().upper()
    if not PROVIDER_NAME_PATTERN.fullmatch(normalized):
        raise CrawlerProviderRegistryError("crawler provider is not registered")

    project_root = project_root.resolve()
    registered, operational, experience = _provider_sets(str(project_root))
    requested_owner = str(scheduled_provider or "").strip().upper()
    if requested_owner and not PROVIDER_NAME_PATTERN.fullmatch(requested_owner):
        raise CrawlerProviderRegistryError("scheduled crawler provider is not registered")
    if normalized in registered and requested_owner in {"", normalized}:
        return CrawlerProviderExecution(normalized, normalized, {})

    aggregate_owner = requested_owner or (
        MUNICIPAL_AGGREGATE_OWNER
        if normalized in operational
        else EXPERIENCE_AGGREGATE_OWNER
        if normalized in experience
        else ""
    )
    if aggregate_owner not in {MUNICIPAL_AGGREGATE_OWNER, EXPERIENCE_AGGREGATE_OWNER}:
        raise CrawlerProviderRegistryError("crawler provider is not registered")
    concrete_scope = operational if aggregate_owner == MUNICIPAL_AGGREGATE_OWNER else experience
    if aggregate_owner not in registered:
        raise CrawlerProviderRegistryError("aggregate crawler is unavailable")
    if normalized not in concrete_scope:
        raise CrawlerProviderRegistryError("crawler provider is not owned by the scheduled aggregate")

    _ensure_project_importable(project_root)
    try:
        excluded = concrete_scope - {normalized}
        if aggregate_owner == MUNICIPAL_AGGREGATE_OWNER:
            from Crawler.Crawler_MunicipalIntegratedReservation import load_municipal_targets

            selected_targets = load_municipal_targets(scheduled_providers=set(excluded))
            selected = {
                str(row.get("provider") or "").strip().upper()
                for row in selected_targets
                if str(row.get("provider") or "").strip()
            }
        else:
            from Crawler.Crawler_EducationExperience import experience_provider_names

            selected = set(experience_provider_names(scheduled_providers=set(excluded)))
    except Exception as exc:
        raise CrawlerProviderRegistryError(
            "crawler provider registry could not be validated"
        ) from exc
    if selected != {normalized}:
        raise CrawlerProviderRegistryError("aggregate crawler provider selection is not exact")

    exclusion_value = " ".join(sorted(excluded))
    if (
        not exclusion_value
        or len(exclusion_value.encode("utf-8")) > MAX_CRAWLER_PROVIDER_ENV_BYTES
    ):
        raise CrawlerProviderRegistryError("aggregate crawler provider exclusion scope is invalid")
    return CrawlerProviderExecution(
        requested_provider=normalized,
        scheduled_provider=aggregate_owner,
        environment={"CRAWLER_PROVIDERS": exclusion_value},
    )
