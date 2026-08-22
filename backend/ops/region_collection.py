from __future__ import annotations

import copy
import hashlib
import os
import re
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend import models
from backend.routers.courses import (
    EDUCATION_CATEGORY_NAMES,
    EXPERIENCE_CATEGORY_NAMES,
    course_scope_filter,
)
from service_group import (
    CULTURE_CENTER_PROVIDERS,
    EXPERIENCE_CONTENT_KEYWORDS,
    EXPERIENCE_EXCLUDED_PROGRAM_TYPES,
    EXPERIENCE_KEYWORDS,
    EXPERIENCE_PROGRAM_TYPES,
    EXPERIENCE_SOURCE_GROUPS,
    LOCAL_GOVERNMENT_EDUCATION_BRANCH_TOKENS,
    LOCAL_GOVERNMENT_EDUCATION_EXCLUDED_FACILITY_TOKENS,
    LOCAL_GOVERNMENT_EDUCATION_OFFICE_TOKEN_RULES,
    PUBLIC_COURSE_SOURCE_GROUPS,
    PUBLIC_NON_ADMIN_EXPERIENCE_SOURCE_GROUPS,
    SERVICE_GROUP_EXPERIENCE,
    SERVICE_GROUP_PUBLIC_COURSE,
    infer_service_group,
    normalize_service_group,
)
from tools.report_scope_region_coverage import (
    CourseLocation,
    MUNICIPALITY_CODE_LIST_KEYS,
    MUNICIPALITY_LIST_KEYS,
    Municipality,
    MunicipalityIndex,
    compact_text,
    load_location_overrides,
    load_municipality_index,
    load_provider_municipalities,
    resolve_target_municipalities,
    resolve_course_municipality,
)


SCOPES = ("experience", "education")
DEFAULT_CACHE_SECONDS = 300
MIN_CACHE_SECONDS = 10
MAX_CACHE_SECONDS = 1_800
PROJECT_ROOT = Path(__file__).resolve().parents[2]
OPERATIONAL_PROVIDER_FILE = (
    PROJECT_ROOT / "config" / "municipal_integrated_reservation_operational.yaml"
)
PRODUCTION_PROVIDER_FILE = PROJECT_ROOT / "config" / "production_crawler_providers.yaml"
TARGET_CONFIG_DIR = PROJECT_ROOT / "config" / "crawl_targets"
MUNICIPALITY_SOURCE_FILE = PROJECT_ROOT / "config" / "municipal_course_search_targets.yaml"
COVERAGE_SOURCE_FILE = (
    PROJECT_ROOT / "config" / "municipal_integrated_reservation_coverage.yaml"
)
LOCATION_OVERRIDE_FILE = PROJECT_ROOT / "config" / "scope_region_location_overrides.yaml"
PROVIDER_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{0,99}\Z")
MUNICIPALITY_CODE_PATTERN = re.compile(r"\d{10}\Z")
PRODUCTION_MUNICIPALITY_COUNT = 269
MUNICIPALITY_TYPES = frozenset({"city", "county", "district"})
PUBLIC_TARGET_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
WORKING_TARGET_STATUSES = frozenset({"ready", "partial", "candidate", "generated"})
EXPERIENCE_AGGREGATE_PROVIDER = "EXPERIENCE_TARGETS"
MUNICIPAL_AGGREGATE_PROVIDER = "MUNICIPAL_RESERVATION_TARGETS"
TARGET_DEFAULT_FIELDS = (
    "collection_category",
    "domain_category",
    "source_group",
    "operator_type",
    "service_group",
)
TARGET_URL_FIELDS = (
    "url",
    "list_url",
    "base_url",
    "source_url",
    "homepage",
)
MIXED_TARGET_EXPERIENCE_PROVIDERS = frozenset({"ULSAN_EDU_BOOKING"})
EXPERIENCE_TARGET_TOKENS = (*EXPERIENCE_KEYWORDS, "문화유산")
EXPERIENCE_TARGET_URL_TOKENS = ("/expr",)
REGION_WIDE_CONFIGURED_PROVIDERS = frozenset(
    {
        # These collectors enumerate rows for the official metropolitan
        # service area.  A province-level hint is therefore an ownership
        # boundary, unlike a national museum's display-only region hint.
        "BUSAN_RESERVATION",
        "GWANGJU_RESERVATION",
        "INCHEON_RESERVATION",
        "MUNI_JHED_JNE_GO_KR_16474ED5",
        "MUNI_WWW_GWANGJU_GO_KR_82EF77CD",
        "SEOUL_PUBLIC_SERVICE",
        "ULSAN_EDU_BOOKING",
    }
)
# Runtime row resolution may use a broader, provider-level municipality
# allowlist than a single configured target owns.  Daegu intentionally shares
# one provider identity across the metropolitan /expr experience catalogue and
# the /lect education catalogue.  Keeping it out of the generic configured set
# prevents the five-municipality education target from being advertised as all
# nine municipalities, while the runtime resolver can still match an /expr row
# to any exact Daegu district found in branch/address evidence.
REGION_WIDE_LOCATION_FALLBACK_PROVIDERS = frozenset(
    {*REGION_WIDE_CONFIGURED_PROVIDERS, "DAEGU_RESERVATION"}
)
# These legacy/broad providers have a configured municipality that describes
# the old target owner, not every row that remains in the database.  Applying
# the normal single-provider fallback would overwrite stronger per-row venue
# evidence and manufacture a municipality when no such evidence exists.
ROW_LOCATION_REQUIRED_PROVIDERS = frozenset(
    {
        "MUNI_CNC_CACF_OR_KR_7A12B48E",
        "MUNI_RESERVE_ANSAN_GO_KR_8236CAF0",
        "MUNI_RESVE_YONGIN_GO_KR_221336AC",
    }
)
ADDRESS_LOCATION_REQUIRED_PROVIDERS = frozenset(
    {"MUNI_RESVE_YONGIN_GO_KR_221336AC"}
)
DAEGU_REGION_WIDE_EXPERIENCE_URL = "https://yeyak.daegu.go.kr/expr/list"
FALLBACK_TARGET_STATUSES = frozenset(
    {"no_current_data", "needs_discovery", "needs_parser", "blocked"}
)


def _cache_seconds() -> int:
    raw = str(os.getenv("OPS_REGION_COLLECTION_CACHE_SECONDS", DEFAULT_CACHE_SECONDS)).strip()
    try:
        value = int(raw)
    except ValueError:
        value = DEFAULT_CACHE_SECONDS
    return max(MIN_CACHE_SECONDS, min(MAX_CACHE_SECONDS, value))


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _latest(first: datetime | None, second: datetime | None) -> datetime | None:
    if first is None:
        return second
    if second is None:
        return first
    return max(first, second)


@dataclass(frozen=True)
class ScopeAggregateRow:
    provider: str
    branch_id: str
    branch_name: str
    branch_address: str
    facility_type: str
    facility_category: str
    venue_name: str
    venue_address: str
    total_data_count: int
    active_data_count: int
    latest_collected_at: datetime | None
    latest_historical_at: datetime | None
    region_sido: str = ""
    region_sigungu: str = ""

    @classmethod
    def from_db_row(cls, row: Any) -> "ScopeAggregateRow":
        return cls(
            provider=str(row.provider or "").strip().upper(),
            branch_id=str(row.branch_id or ""),
            branch_name=str(row.branch_name or "").strip(),
            branch_address=str(row.branch_address or "").strip(),
            facility_type=str(row.facility_type or "").strip(),
            facility_category=str(row.facility_category or "").strip(),
            venue_name=str(row.venue_name or "").strip(),
            venue_address=str(row.venue_address or "").strip(),
            total_data_count=int(row.total_data_count or 0),
            active_data_count=int(row.active_data_count or 0),
            latest_collected_at=row.latest_collected_at,
            latest_historical_at=row.latest_historical_at,
            region_sido=str(row.region_sido or "").strip(),
            region_sigungu=str(row.region_sigungu or "").strip(),
        )

    def as_course_location(self) -> CourseLocation:
        return CourseLocation(
            course_id=f"aggregate:{self.provider}:{self.branch_id}",
            provider=self.provider,
            branch_id=self.branch_id,
            branch_name=self.branch_name,
            branch_address=self.branch_address,
            facility_type=self.facility_type,
            facility_category=self.facility_category,
            venue_name=self.venue_name,
            venue_address=self.venue_address,
            is_active=self.active_data_count > 0,
        )


@dataclass
class ProviderAccumulator:
    active_data_count: int = 0
    total_data_count: int = 0
    active_branches: set[str] = field(default_factory=set)
    all_branches: set[str] = field(default_factory=set)
    latest_collected_at: datetime | None = None
    latest_historical_at: datetime | None = None

    def add(self, row: ScopeAggregateRow) -> None:
        self.active_data_count += row.active_data_count
        self.total_data_count += row.total_data_count
        if row.branch_id:
            self.all_branches.add(row.branch_id)
            if row.active_data_count:
                self.active_branches.add(row.branch_id)
        self.latest_collected_at = _latest(
            self.latest_collected_at,
            row.latest_collected_at,
        )
        self.latest_historical_at = _latest(
            self.latest_historical_at,
            row.latest_historical_at,
        )

    def as_payload(self, provider: str) -> dict[str, Any]:
        return {
            "provider": provider,
            "active_data_count": self.active_data_count,
            "total_data_count": self.total_data_count,
            "active_branch_count": len(self.active_branches),
            "total_branch_count": len(self.all_branches),
            "latest_collected_at": _iso(self.latest_collected_at),
            "latest_historical_at": _iso(self.latest_historical_at),
            "_active_branch_ids": sorted(self.active_branches),
            "_total_branch_ids": sorted(self.all_branches),
        }


@dataclass
class ScopeAccumulator:
    providers: dict[str, ProviderAccumulator] = field(
        default_factory=lambda: defaultdict(ProviderAccumulator)
    )
    active_branches: set[str] = field(default_factory=set)
    all_branches: set[str] = field(default_factory=set)

    def add(self, row: ScopeAggregateRow) -> None:
        self.providers[row.provider].add(row)
        if row.branch_id:
            self.all_branches.add(row.branch_id)
            if row.active_data_count:
                self.active_branches.add(row.branch_id)

    def as_payload(self) -> dict[str, Any]:
        provider_rows = [
            stats.as_payload(provider)
            for provider, stats in sorted(self.providers.items())
        ]
        active_rows = [row for row in provider_rows if row["active_data_count"] > 0]
        active_count = sum(int(row["active_data_count"]) for row in provider_rows)
        total_count = sum(int(row["total_data_count"]) for row in provider_rows)
        latest_active = max(
            (str(row["latest_collected_at"]) for row in active_rows if row["latest_collected_at"]),
            default=None,
        )
        latest_historical = max(
            (
                str(row["latest_historical_at"])
                for row in provider_rows
                if row["latest_historical_at"]
            ),
            default=None,
        )
        return {
            "status": (
                "collected"
                if active_count
                else "historical"
                if total_count
                else "empty"
            ),
            "active_provider_count": len(active_rows),
            "total_provider_count": len(provider_rows),
            "active_data_count": active_count,
            "total_data_count": total_count,
            "active_branch_count": len(self.active_branches),
            "total_branch_count": len(self.all_branches),
            "latest_collected_at": latest_active,
            "latest_historical_at": latest_historical,
            "providers": provider_rows,
            "_active_branch_ids": sorted(self.active_branches),
            "_total_branch_ids": sorted(self.all_branches),
        }


@dataclass(frozen=True)
class RegionReference:
    index: MunicipalityIndex
    provider_municipalities: Mapping[str, set[str]]
    location_overrides: Mapping[tuple[str, str], Municipality]
    configured_by_municipality: Mapping[str, tuple[str, ...]]
    configured_by_scope: Mapping[str, Mapping[str, tuple[str, ...]]] = field(
        default_factory=dict
    )
    unmapped_configured_providers: tuple[str, ...] = ()
    unmapped_configured_by_scope: Mapping[str, tuple[str, ...]] = field(
        default_factory=dict
    )
    unmapped_configured_targets_by_scope: Mapping[
        str,
        tuple[Mapping[str, str | None], ...],
    ] = field(default_factory=dict)


def _load_provider_list(path: Path, list_key: str) -> set[str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError(f"invalid Ops provider registry: {path.name}")
    values = data.get(list_key)
    if not isinstance(values, list) or len(values) > 5_000:
        raise ValueError(f"invalid Ops provider list: {path.name}")

    providers: set[str] = set()
    for value in values:
        raw_provider = value.get("provider") if isinstance(value, dict) else value
        provider = _clean(raw_provider).upper()
        if not PROVIDER_PATTERN.fullmatch(provider):
            raise ValueError(f"invalid Ops provider in {path.name}")
        providers.add(provider)
    return providers


def _scope_target_rows() -> list[dict[str, Any]]:
    """Load target rows with the same file-level metadata inheritance as crawlers."""

    rows: list[dict[str, Any]] = []
    for path in sorted(TARGET_CONFIG_DIR.glob("*.yaml")):
        if path.name == "index.yaml":
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError(f"invalid Ops crawler target document: {path.name}")
        targets = data.get("targets") or []
        if not isinstance(targets, list) or len(targets) > 20_000:
            raise ValueError(f"invalid Ops crawler targets: {path.name}")
        defaults = {
            key: data.get(key)
            for key in TARGET_DEFAULT_FIELDS
            if _clean(data.get(key))
        }
        for value in targets:
            if not isinstance(value, Mapping):
                continue
            rows.append({**defaults, **dict(value)})
    return rows


def _target_url(target: Mapping[str, Any]) -> str:
    return next(
        (
            _clean(target.get(field_name))
            for field_name in TARGET_URL_FIELDS
            if _clean(target.get(field_name))
        ),
        "",
    )


def _target_scope(target: Mapping[str, Any]) -> str | None:
    provider = _clean(target.get("provider")).upper()
    explicit_group = normalize_service_group(target.get("service_group"))
    policy = _clean(target.get("service_group_policy")).lower()
    group = (
        explicit_group
        if policy == "locked" and explicit_group
        else infer_service_group(
            provider=provider,
            collection_category=target.get("collection_category"),
            domain_category=target.get("domain_category"),
            source_group=target.get("source_group"),
            operator_type=target.get("operator_type"),
            branch_name=target.get("branch"),
            raw_url=_target_url(target),
            title=target.get("name"),
            category_raw=target.get("category_raw"),
            program_type=target.get("program_type"),
            service_group=target.get("service_group"),
        )
    )
    if group == SERVICE_GROUP_EXPERIENCE:
        return "experience"
    if policy == "locked" and explicit_group == SERVICE_GROUP_PUBLIC_COURSE:
        return "education"
    if any(token in _target_url(target).lower() for token in EXPERIENCE_TARGET_URL_TOKENS):
        return "experience"
    target_metadata = " ".join(
        _clean(target.get(field_name)) for field_name in ("name", "branch")
    )
    if provider in MIXED_TARGET_EXPERIENCE_PROVIDERS or any(
        token in target_metadata for token in EXPERIENCE_TARGET_TOKENS
    ):
        return "experience"
    if group == SERVICE_GROUP_PUBLIC_COURSE:
        return "education"
    return None


def _target_scopes(target: Mapping[str, Any]) -> frozenset[str]:
    """Return every Ops scope owned by one configured crawler target.

    Most targets are single-scope and continue to use taxonomy inference.  A
    canonical ledger that emits course-level education and experience rows can
    declare both via ``ops_scopes`` so its provider remains visible in either
    tab even while one side currently has zero data.
    """

    declared = target.get("ops_scopes")
    if declared is not None:
        if (
            not isinstance(declared, list)
            or not declared
            or len(declared) > len(SCOPES)
            or any(
                not isinstance(value, str) or value not in SCOPES
                for value in declared
            )
            or len(set(declared)) != len(declared)
        ):
            raise ValueError("invalid crawler target ops_scopes")
        return frozenset(declared)

    inferred = _target_scope(target)
    scopes = {inferred} if inferred else set()
    if _clean(target.get("provider")).upper() in MIXED_TARGET_EXPERIENCE_PROVIDERS:
        scopes.update(SCOPES)
    return frozenset(scopes)


def _configured_region_fallback_providers(
    target: Mapping[str, Any],
) -> frozenset[str]:
    """Return only region-wide owners justified for this exact target scope."""

    providers = set(REGION_WIDE_CONFIGURED_PROVIDERS)
    provider = _clean(target.get("provider")).upper()
    normalized_url = (
        _target_url(target).split("#", 1)[0].split("?", 1)[0].rstrip("/")
    )
    if (
        provider == "DAEGU_RESERVATION"
        and "experience" in _target_scopes(target)
        and normalized_url == DAEGU_REGION_WIDE_EXPERIENCE_URL.rstrip("/")
    ):
        providers.add(provider)
    return frozenset(providers)


def _target_status_rank(target: Mapping[str, Any]) -> int:
    status = _clean(target.get("crawler_status") or target.get("status")).lower()
    if status in WORKING_TARGET_STATUSES and _target_url(target):
        return 0
    if status in FALLBACK_TARGET_STATUSES:
        return 1
    return 2


def _configured_target_identifier(target: Mapping[str, Any]) -> str:
    explicit = _clean(target.get("target_id"))
    if PUBLIC_TARGET_ID_PATTERN.fullmatch(explicit):
        return explicit
    fingerprint = "\x1f".join(
        (
            _clean(target.get("provider")).upper(),
            _target_url(target),
            _clean(target.get("name")),
            _clean(target.get("branch")),
        )
    )
    return f"derived:{hashlib.sha256(fingerprint.encode('utf-8')).hexdigest()[:16]}"


def _unmapped_configured_target(
    provider: str,
    target: Mapping[str, Any],
) -> dict[str, str | None]:
    has_explicit_municipality = any(
        target.get(key)
        for key in (
            *MUNICIPALITY_LIST_KEYS,
            *MUNICIPALITY_CODE_LIST_KEYS,
            "municipality_code",
            "municipality_full_name",
        )
    )
    region_hint = _clean(
        target.get("municipality_full_name") or target.get("region")
    )
    if has_explicit_municipality:
        reason = "explicit_municipality_unresolved"
    elif region_hint:
        reason = "region_hint_requires_explicit_municipality"
    elif _clean(target.get("name") or target.get("branch")):
        reason = "municipality_not_inferred"
    else:
        reason = "municipality_evidence_missing"
    display_name = _clean(target.get("name") or target.get("branch")) or provider
    return {
        "provider": provider,
        "target_id": _configured_target_identifier(target),
        "display_name": display_name[:200],
        "region_hint": region_hint[:100] or None,
        "reason": reason,
    }


def _configured_provider_registry(
    target_rows: Iterable[Mapping[str, Any]] | None = None,
) -> frozenset[str]:
    """Resolve concrete scheduled providers without importing crawler modules.

    Production schedules two aggregate owners.  The UI must show their concrete
    providers, not the aggregate process names, otherwise most experience
    collectors disappear from regional coverage.
    """

    production = _load_provider_list(PRODUCTION_PROVIDER_FILE, "providers")
    operational = _load_provider_list(OPERATIONAL_PROVIDER_FILE, "entries")
    rows = list(target_rows) if target_rows is not None else _scope_target_rows()
    providers = production - {
        EXPERIENCE_AGGREGATE_PROVIDER,
        MUNICIPAL_AGGREGATE_PROVIDER,
    }
    if MUNICIPAL_AGGREGATE_PROVIDER in production:
        providers.update(operational)
    if EXPERIENCE_AGGREGATE_PROVIDER in production:
        providers.update(
            _clean(target.get("provider")).upper()
            for target in rows
            if _clean(target.get("crawler_status") or target.get("status")).lower()
            in WORKING_TARGET_STATUSES
            and _target_url(target)
            and "experience" in _target_scopes(target)
        )
    if any(not PROVIDER_PATTERN.fullmatch(provider) for provider in providers):
        raise ValueError("invalid concrete Ops provider registry")
    return frozenset(providers)


def _configured_provider_scopes(
    providers: frozenset[str],
    target_rows: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, frozenset[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    rows = target_rows if target_rows is not None else _scope_target_rows()
    rows_by_provider = _selected_provider_targets(providers, rows)
    for provider, provider_rows in rows_by_provider.items():
        for target in provider_rows:
            for scope in _target_scopes(target):
                result[provider].add(scope)
    return {
        provider: frozenset(scopes)
        for provider, scopes in result.items()
        if scopes
    }


def _selected_provider_targets(
    providers: frozenset[str],
    target_rows: Iterable[Mapping[str, Any]],
) -> dict[str, list[Mapping[str, Any]]]:
    rows_by_provider: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for target in target_rows:
        provider = _clean(target.get("provider")).upper()
        if provider in providers:
            rows_by_provider[provider].append(target)
    result: dict[str, list[Mapping[str, Any]]] = {}
    for provider, provider_rows in rows_by_provider.items():
        selected_rank = min(_target_status_rank(target) for target in provider_rows)
        result[provider] = [
            target
            for target in provider_rows
            if _target_status_rank(target) == selected_rank
        ]
    return result


_REFERENCE_LOCK = threading.Lock()
_REFERENCE_SIGNATURE: tuple[tuple[str, int, int], ...] | None = None
_REFERENCE_VALUE: RegionReference | None = None


def _reference_config_signature() -> tuple[tuple[str, int, int], ...]:
    paths = [
        MUNICIPALITY_SOURCE_FILE,
        COVERAGE_SOURCE_FILE,
        OPERATIONAL_PROVIDER_FILE,
        PRODUCTION_PROVIDER_FILE,
        LOCATION_OVERRIDE_FILE,
        *sorted(TARGET_CONFIG_DIR.glob("*.yaml")),
    ]
    signature: list[tuple[str, int, int]] = []
    for path in paths:
        try:
            stat = path.stat()
        except FileNotFoundError:
            signature.append((str(path), -1, -1))
        else:
            signature.append((str(path), stat.st_mtime_ns, stat.st_size))
    return tuple(signature)


def _validate_production_municipality_index(index: MunicipalityIndex) -> None:
    """Fail closed when the production municipality catalogue is malformed."""

    municipalities = index.municipalities
    if len(municipalities) != PRODUCTION_MUNICIPALITY_COUNT:
        raise ValueError(
            "invalid production municipality index: "
            f"expected {PRODUCTION_MUNICIPALITY_COUNT} entries"
        )

    codes: set[str] = set()
    full_names: set[str] = set()
    for municipality in municipalities:
        if not MUNICIPALITY_CODE_PATTERN.fullmatch(municipality.code):
            raise ValueError("invalid production municipality index: invalid code")
        if municipality.code in codes:
            raise ValueError("invalid production municipality index: duplicate code")
        codes.add(municipality.code)

        if not municipality.sido or not municipality.sigungu or not municipality.full_name:
            raise ValueError("invalid production municipality index: missing field")
        expected_full_name = (
            municipality.sido
            if municipality.sido == municipality.sigungu
            else f"{municipality.sido} {municipality.sigungu}"
        )
        if municipality.full_name != expected_full_name:
            raise ValueError("invalid production municipality index: invalid full name")
        if municipality.full_name in full_names:
            raise ValueError(
                "invalid production municipality index: duplicate full name"
            )
        full_names.add(municipality.full_name)

        if municipality.municipality_type not in MUNICIPALITY_TYPES:
            raise ValueError(
                "invalid production municipality index: invalid municipality type"
            )

    if set(index.by_full_name) != full_names or any(
        index.by_full_name[name] != municipality
        for name, municipality in (
            (municipality.full_name, municipality)
            for municipality in municipalities
        )
    ):
        raise ValueError("invalid production municipality index: inconsistent lookup")


def _region_reference() -> RegionReference:
    global _REFERENCE_SIGNATURE, _REFERENCE_VALUE
    signature = _reference_config_signature()
    with _REFERENCE_LOCK:
        if _REFERENCE_VALUE is not None and signature == _REFERENCE_SIGNATURE:
            return _REFERENCE_VALUE

    index = load_municipality_index(MUNICIPALITY_SOURCE_FILE)
    _validate_production_municipality_index(index)
    target_rows = _scope_target_rows()
    reviewed_providers = _configured_provider_registry(target_rows)
    provider_scopes = _configured_provider_scopes(reviewed_providers, target_rows)
    selected_targets = _selected_provider_targets(reviewed_providers, target_rows)
    baseline_provider_municipalities = load_provider_municipalities(
        index,
        target_dir=TARGET_CONFIG_DIR,
        coverage_path=COVERAGE_SOURCE_FILE,
        operational_path=OPERATIONAL_PROVIDER_FILE,
        include_region_fallback=False,
        include_targets=False,
    )
    provider_municipalities = load_provider_municipalities(
        index,
        target_dir=TARGET_CONFIG_DIR,
        coverage_path=COVERAGE_SOURCE_FILE,
        operational_path=OPERATIONAL_PROVIDER_FILE,
        include_region_fallback=False,
        region_fallback_providers=REGION_WIDE_LOCATION_FALLBACK_PROVIDERS,
    )
    location_overrides = load_location_overrides(index, LOCATION_OVERRIDE_FILE)
    configured_by_scope: dict[str, dict[str, set[str]]] = {
        scope: defaultdict(set) for scope in SCOPES
    }
    unmapped_targets_by_scope: dict[
        str,
        dict[tuple[str, str], dict[str, str | None]],
    ] = {scope: {} for scope in SCOPES}
    for provider, targets in selected_targets.items():
        # A provider-level baseline is only safe for a single configured target.
        # With multiple targets it is the union of their known municipalities;
        # applying that union to one unresolved target would hide the gap and
        # falsely claim that target for every municipality owned by its siblings.
        baseline_municipality_names = (
            baseline_provider_municipalities.get(provider, set())
            if len(targets) == 1
            else set()
        )
        for target in targets:
            scopes = _target_scopes(target)
            if not scopes:
                continue
            municipality_names = resolve_target_municipalities(
                target,
                index,
                include_region_fallback=False,
                region_fallback_providers=_configured_region_fallback_providers(
                    target
                ),
            ) or baseline_municipality_names
            if not municipality_names:
                item = _unmapped_configured_target(provider, target)
                for scope in scopes:
                    unmapped_targets_by_scope[scope].setdefault(
                        (provider, str(item["target_id"])),
                        item,
                    )
                continue
            for municipality_name in municipality_names:
                if municipality_name in index.by_full_name:
                    for scope in scopes:
                        configured_by_scope[scope][municipality_name].add(provider)
    configured: dict[str, set[str]] = defaultdict(set)
    for municipality_providers in configured_by_scope.values():
        for municipality_name, providers in municipality_providers.items():
            if municipality_name in index.by_full_name:
                configured[municipality_name].update(providers)
    mapped_providers = {
        provider for providers in configured.values() for provider in providers
    }
    scoped_providers = set(provider_scopes)
    unmapped_configured_providers = tuple(
        sorted(scoped_providers - mapped_providers)
    )
    mapped_providers_by_scope = {
        scope: {
            provider
            for providers in configured_by_scope[scope].values()
            for provider in providers
        }
        for scope in SCOPES
    }
    unmapped_configured_by_scope = {
        scope: tuple(
            sorted(
                provider
                for provider, scopes in provider_scopes.items()
                if scope in scopes
                and provider not in mapped_providers_by_scope[scope]
            )
        )
        for scope in SCOPES
    }
    reference = RegionReference(
        index=index,
        provider_municipalities=provider_municipalities,
        location_overrides=location_overrides,
        configured_by_municipality={
            name: tuple(sorted(providers)) for name, providers in configured.items()
        },
        configured_by_scope={
            scope: {
                name: tuple(sorted(providers))
                for name, providers in municipality_providers.items()
            }
            for scope, municipality_providers in configured_by_scope.items()
        },
        unmapped_configured_providers=unmapped_configured_providers,
        unmapped_configured_by_scope=unmapped_configured_by_scope,
        unmapped_configured_targets_by_scope={
            scope: tuple(
                sorted(
                    targets.values(),
                    key=lambda item: (
                        str(item["provider"]),
                        str(item["target_id"]),
                    ),
                )
            )
            for scope, targets in unmapped_targets_by_scope.items()
        },
    )
    with _REFERENCE_LOCK:
        _REFERENCE_SIGNATURE = signature
        _REFERENCE_VALUE = reference
    return reference


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _sql_text(value: Any) -> str:
    return "" if value is None else str(value)


def _has_compact_scope_token(values: Iterable[Any], tokens: Iterable[str]) -> bool:
    compact_values = tuple(
        _sql_text(value).lower().replace(" ", "") for value in values
    )
    return any(
        re.sub(r"\s+", "", token).lower() in value
        for value in compact_values
        for token in tokens
        if re.sub(r"\s+", "", token)
    )


def _has_ilike_token(values: Iterable[Any], tokens: Iterable[str]) -> bool:
    lowered = tuple(_sql_text(value).lower() for value in values)
    return any(token.lower() in value for value in lowered for token in tokens if token)


def _administrative_branch(row: Any) -> bool:
    values = (
        row.branch_name,
        row.facility_type,
        row.facility_category,
        row.education_institution,
        row.target_name,
        row.matched_name,
    )
    if _has_ilike_token(
        values,
        LOCAL_GOVERNMENT_EDUCATION_EXCLUDED_FACILITY_TOKENS,
    ):
        return False
    if _has_ilike_token(values, LOCAL_GOVERNMENT_EDUCATION_BRANCH_TOKENS):
        return True
    for value in values:
        lowered = _sql_text(value).lower()
        for token, false_fragments in LOCAL_GOVERNMENT_EDUCATION_OFFICE_TOKEN_RULES:
            if token.lower() in lowered and not any(
                fragment.lower() in lowered for fragment in false_fragments
            ):
                return True
    return bool(
        row.education_institution is not None
        and re.fullmatch(
            r"[가-힣0-9 ]{1,40}(시|군|구|읍|면|동)",
            _sql_text(row.education_institution).strip(),
        )
    )


def _scope_for_aggregate_row(row: Any) -> str | None:
    """Mirror the production SQL taxonomy after a cheap metadata aggregate."""

    provider = _sql_text(row.provider)
    branch_provider = _sql_text(row.branch_provider)
    service_group = _sql_text(row.service_group)
    program_type = _sql_text(row.program_type)
    collection_category = _sql_text(row.collection_category)
    domain_category = _sql_text(row.domain_category)
    ai_category = _sql_text(row.ai_category)
    source_group = _sql_text(row.source_group)
    category_raw = _sql_text(row.category_raw)
    locked_policy = _clean(row.service_group_policy).lower()
    locked_group_source = (
        row.locked_service_group
        if row.locked_service_group is not None
        else row.service_group
    )
    locked_group = _sql_text(locked_group_source).strip(" ")
    locked_public_course = (
        locked_policy == "locked"
        and locked_group.replace(" ", "")
        == SERVICE_GROUP_PUBLIC_COURSE.replace(" ", "")
    )

    if provider in CULTURE_CENTER_PROVIDERS:
        return None

    administrative_branch = _administrative_branch(row)

    non_experience_program = (
        not program_type or program_type not in EXPERIENCE_EXCLUDED_PROGRAM_TYPES
    )
    course_category_experience = non_experience_program and (
        service_group == SERVICE_GROUP_EXPERIENCE
        or program_type in EXPERIENCE_PROGRAM_TYPES
        or collection_category in EXPERIENCE_CATEGORY_NAMES
        or domain_category in EXPERIENCE_CATEGORY_NAMES
        or ai_category in EXPERIENCE_CATEGORY_NAMES
        or source_group in EXPERIENCE_SOURCE_GROUPS
        or _has_compact_scope_token(
            (category_raw, program_type),
            EXPERIENCE_CONTENT_KEYWORDS,
        )
    )
    institution_branch = (
        _sql_text(row.branch_provider) == "CULTURE_FACILITY"
        or row.facility_source is not None
        or _sql_text(row.facility_service_group) == SERVICE_GROUP_EXPERIENCE
        or _sql_text(row.facility_collection_category) == SERVICE_GROUP_EXPERIENCE
        or _sql_text(row.facility_category) in EXPERIENCE_CATEGORY_NAMES
        or _sql_text(row.facility_type) in EXPERIENCE_CATEGORY_NAMES
        or _has_ilike_token(
            (row.branch_name, row.facility_type, row.facility_category),
            LOCAL_GOVERNMENT_EDUCATION_EXCLUDED_FACILITY_TOKENS,
        )
    )
    institution_experience = provider == "CULTURE_FACILITY" or (
        not administrative_branch
        and (
            source_group in PUBLIC_NON_ADMIN_EXPERIENCE_SOURCE_GROUPS
            or institution_branch
        )
    )
    experience = (
        non_experience_program
        and not locked_public_course
        and (institution_experience or course_category_experience)
    )
    if experience:
        return "experience"

    public_course = (
        provider[:5].upper() == "MUNI_"
        or provider == "PUBLIC"
        or source_group in PUBLIC_COURSE_SOURCE_GROUPS
        or service_group == SERVICE_GROUP_PUBLIC_COURSE
        or branch_provider[:5].upper() == "MUNI_"
        or branch_provider == "PUBLIC"
    )
    explicit_education_categories = {
        domain_category,
        collection_category,
        _sql_text(row.raw_domain_category),
        _sql_text(row.raw_collection_category),
    }
    locked_education = locked_public_course and bool(
        explicit_education_categories.intersection(EDUCATION_CATEGORY_NAMES)
    )
    if public_course and (locked_education or administrative_branch):
        return "education"
    return None


def _all_scope_aggregate_rows(
    session: Session,
) -> dict[str, list[ScopeAggregateRow]]:
    """Apply the public scope contract in SQL, then aggregate location fields.

    Grouping every taxonomy/JSON field before classifying creates thousands of
    unnecessary groups and repeatedly loads unrelated culture-centre rows.
    The public API predicate is authoritative, so filtering each requested
    scope first is both faster and prevents Ops taxonomy drift.
    """

    observed_at = func.coalesce(
        models.Course.last_seen_at,
        models.Course.first_seen_at,
        models.Course.created_at,
    )
    result: dict[str, list[ScopeAggregateRow]] = {scope: [] for scope in SCOPES}
    for scope in SCOPES:
        query = (
            session.query(
                models.Course.provider,
                models.Course.branch_id,
                models.Branch.name.label("branch_name"),
                models.Branch.address.label("branch_address"),
                models.Branch.region_sido,
                models.Branch.region_sigungu,
                models.Branch.facility_type,
                models.Branch.facility_category,
                models.Course.venue_name,
                models.Course.venue_address,
                func.count(models.Course.id).label("total_data_count"),
                func.count(models.Course.id)
                .filter(models.Course.is_active.is_(True))
                .label("active_data_count"),
                func.max(observed_at)
                .filter(models.Course.is_active.is_(True))
                .label("latest_collected_at"),
                func.max(observed_at).label("latest_historical_at"),
            )
            .outerjoin(models.Branch, models.Branch.id == models.Course.branch_id)
            .filter(course_scope_filter(scope))
            .group_by(
                models.Course.provider,
                models.Course.branch_id,
                models.Branch.name,
                models.Branch.address,
                models.Branch.region_sido,
                models.Branch.region_sigungu,
                models.Branch.facility_type,
                models.Branch.facility_category,
                models.Course.venue_name,
                models.Course.venue_address,
            )
            .yield_per(2_000)
        )
        result[scope].extend(ScopeAggregateRow.from_db_row(row) for row in query)
    return result


def _scope_aggregate_rows(session: Session, scope: str) -> Iterable[ScopeAggregateRow]:
    if scope not in SCOPES:
        raise ValueError(f"unsupported collection scope: {scope}")
    yield from _all_scope_aggregate_rows(session)[scope]


def _empty_scope_payload() -> dict[str, Any]:
    return ScopeAccumulator().as_payload()


def _configured_status(scope_payload: Mapping[str, Any], configured_count: int) -> str:
    if int(scope_payload.get("active_data_count") or 0):
        return "collected"
    if int(scope_payload.get("total_data_count") or 0):
        return "historical"
    if configured_count:
        return "connected_empty"
    return "unconfigured"


def _resolve_aggregate_municipality(
    row: ScopeAggregateRow,
    reference: RegionReference,
) -> Municipality | None:
    override = reference.location_overrides.get(
        (row.provider, compact_text(row.branch_name))
    )
    if override is not None:
        return override
    if row.region_sido and row.region_sigungu:
        explicit = reference.index.exact_match(
            f"{row.region_sido} {row.region_sigungu}"
        )
        if explicit is not None:
            return explicit
    if row.provider in ROW_LOCATION_REQUIRED_PROVIDERS:
        # Prefer address fields because facility names can contain unrelated
        # municipality-like tokens.  Providers whose audited contract exposes
        # an exact venue address must never fall back to names; the remaining
        # legacy providers may use one unique name/venue match.
        evidence_groups = [(row.branch_address, row.venue_address)]
        if row.provider not in ADDRESS_LOCATION_REQUIRED_PROVIDERS:
            evidence_groups.append((row.branch_name, row.venue_name))
        for values in evidence_groups:
            matches = reference.index.match_all(*values)
            if len(matches) == 1:
                return matches[0]
        return None
    return resolve_course_municipality(
        row.as_course_location(),
        reference.index,
        reference.provider_municipalities,
        None,
    )


def _provider_rows_by_name(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        provider = str(row.get("provider") or "")
        if not provider:
            continue
        current = result.setdefault(
            provider,
            {
                "provider": provider,
                "active_data_count": 0,
                "total_data_count": 0,
                "active_branch_count": 0,
                "total_branch_count": 0,
                "latest_collected_at": None,
                "latest_historical_at": None,
                "_active_branch_ids": set(),
                "_total_branch_ids": set(),
            },
        )
        for field_name in ("active_data_count", "total_data_count"):
            current[field_name] += int(row.get(field_name) or 0)
        current["_active_branch_ids"].update(
            str(value) for value in row.get("_active_branch_ids", ()) if value
        )
        current["_total_branch_ids"].update(
            str(value) for value in row.get("_total_branch_ids", ()) if value
        )
        for field_name in ("latest_collected_at", "latest_historical_at"):
            value = row.get(field_name)
            if value and (current[field_name] is None or str(value) > str(current[field_name])):
                current[field_name] = value
    for current in result.values():
        active_branch_ids = sorted(current["_active_branch_ids"])
        total_branch_ids = sorted(current["_total_branch_ids"])
        current["active_branch_count"] = len(active_branch_ids)
        current["total_branch_count"] = len(total_branch_ids)
        current["_active_branch_ids"] = active_branch_ids
        current["_total_branch_ids"] = total_branch_ids
    return result


def _summarize_scope(
    municipality_rows: Iterable[Mapping[str, Any]],
    scope: str,
    *,
    include_rollup_status: bool = False,
) -> dict[str, Any]:
    rows = list(municipality_rows)
    provider_rows = _provider_rows_by_name(
        provider
        for row in rows
        for provider in row[scope]["providers"]
    )
    configured_providers = sorted(
        {
            provider
            for row in rows
            for provider in row[scope].get("configured_providers", [])
        }
    )
    providers = [provider_rows[name] for name in sorted(provider_rows)]
    active_providers = [row for row in providers if row["active_data_count"] > 0]
    active_branch_ids = sorted(
        {
            str(branch_id)
            for row in rows
            for branch_id in row[scope].get("_active_branch_ids", ())
            if branch_id
        }
    )
    total_branch_ids = sorted(
        {
            str(branch_id)
            for row in rows
            for branch_id in row[scope].get("_total_branch_ids", ())
            if branch_id
        }
    )
    latest_collected = max(
        (
            str(row[scope]["latest_collected_at"])
            for row in rows
            if row[scope]["latest_collected_at"]
        ),
        default=None,
    )
    latest_historical = max(
        (
            str(row[scope]["latest_historical_at"])
            for row in rows
            if row[scope]["latest_historical_at"]
        ),
        default=None,
    )
    status_values = [
        (
            row["rollup"][scope]["status"]
            if include_rollup_status and row.get("rollup")
            else row[scope]["status"]
        )
        for row in rows
    ]
    status_counts = {
        status_name: sum(value == status_name for value in status_values)
        for status_name in ("collected", "historical", "connected_empty", "unconfigured")
    }
    status = (
        "collected"
        if any(int(row[scope]["active_data_count"]) for row in rows)
        else "historical"
        if any(int(row[scope]["total_data_count"]) for row in rows)
        else "connected_empty"
        if configured_providers
        else "unconfigured"
    )
    return {
        "status": status,
        "municipality_count": len(rows),
        "collected_municipality_count": status_counts["collected"],
        "historical_municipality_count": status_counts["historical"],
        "connected_empty_municipality_count": status_counts["connected_empty"],
        "unconfigured_municipality_count": status_counts["unconfigured"],
        "configured_provider_count": len(configured_providers),
        "active_provider_count": len(active_providers),
        "total_provider_count": len(providers),
        "active_data_count": sum(int(row[scope]["active_data_count"]) for row in rows),
        "total_data_count": sum(int(row[scope]["total_data_count"]) for row in rows),
        "active_branch_count": len(active_branch_ids),
        "total_branch_count": len(total_branch_ids),
        "latest_collected_at": latest_collected,
        "latest_historical_at": latest_historical,
        "configured_providers": configured_providers,
        "active_providers": [row["provider"] for row in active_providers],
        "providers": providers,
        "_active_branch_ids": active_branch_ids,
        "_total_branch_ids": total_branch_ids,
    }


def _without_internal_collection_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_internal_collection_fields(item)
            for key, item in value.items()
            if not str(key).startswith("_")
        }
    if isinstance(value, list):
        return [_without_internal_collection_fields(item) for item in value]
    return value


def build_region_collection_snapshot(
    session: Session,
    *,
    reference: RegionReference | None = None,
    aggregate_rows: Mapping[str, Iterable[ScopeAggregateRow]] | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    selected_reference = reference or _region_reference()
    accumulators: dict[str, dict[str, ScopeAccumulator]] = {
        scope: defaultdict(ScopeAccumulator) for scope in SCOPES
    }
    unmapped: dict[str, dict[str, Any]] = {
        scope: {
            "active_data_count": 0,
            "total_data_count": 0,
            "active_providers": set(),
            "providers": set(),
        }
        for scope in SCOPES
    }

    selected_rows = (
        aggregate_rows
        if aggregate_rows is not None
        else _all_scope_aggregate_rows(session)
    )

    for scope in SCOPES:
        rows = selected_rows[scope]
        for row in rows:
            municipality = _resolve_aggregate_municipality(
                row,
                selected_reference,
            )
            if municipality is None:
                unmapped[scope]["active_data_count"] += row.active_data_count
                unmapped[scope]["total_data_count"] += row.total_data_count
                if row.active_data_count > 0:
                    unmapped[scope]["active_providers"].add(row.provider)
                unmapped[scope]["providers"].add(row.provider)
                continue
            accumulators[scope][municipality.full_name].add(row)

    municipalities: list[dict[str, Any]] = []
    for municipality in selected_reference.index.municipalities:
        configured_providers = list(
            selected_reference.configured_by_municipality.get(
                municipality.full_name,
                (),
            )
        )
        row: dict[str, Any] = {
            "code": municipality.code,
            "sido": municipality.sido,
            "sigungu": municipality.sigungu,
            "full_name": municipality.full_name,
            "municipality_type": municipality.municipality_type,
            "configured_provider_count": len(configured_providers),
            "configured_providers": configured_providers,
        }
        for scope in SCOPES:
            scope_configured_providers = list(
                selected_reference.configured_by_scope.get(scope, {}).get(
                    municipality.full_name,
                    (),
                )
                if selected_reference.configured_by_scope
                else configured_providers
            )
            payload = (
                accumulators[scope][municipality.full_name].as_payload()
                if municipality.full_name in accumulators[scope]
                else _empty_scope_payload()
            )
            payload["configured_provider_count"] = len(
                scope_configured_providers
            )
            payload["configured_providers"] = scope_configured_providers
            payload["status"] = _configured_status(
                payload,
                len(scope_configured_providers),
            )
            row[scope] = payload
        municipalities.append(row)

    for row in municipalities:
        descendants = [
            candidate
            for candidate in municipalities
            if candidate is not row
            and candidate["sido"] == row["sido"]
            and str(candidate["sigungu"]).startswith(f"{row['sigungu']} ")
        ]
        row["child_municipality_count"] = len(descendants)
        if descendants:
            rollup_rows = [row, *descendants]
            configured_providers = sorted(
                {
                    provider
                    for candidate in rollup_rows
                    for provider in candidate["configured_providers"]
                }
            )
            row["rollup"] = {
                "configured_provider_count": len(configured_providers),
                "configured_providers": configured_providers,
                "experience": _summarize_scope(rollup_rows, "experience"),
                "education": _summarize_scope(rollup_rows, "education"),
            }
        else:
            row["rollup"] = None

    by_sido: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in municipalities:
        by_sido[str(row["sido"])].append(row)
    sidos: list[dict[str, Any]] = []
    for sido, rows in by_sido.items():
        sidos.append(
            {
                "sido": sido,
                "municipality_count": len(rows),
                "configured_provider_count": len(
                    {
                        provider
                        for row in rows
                        for provider in row["configured_providers"]
                    }
                ),
                "experience": _summarize_scope(
                    rows,
                    "experience",
                    include_rollup_status=True,
                ),
                "education": _summarize_scope(
                    rows,
                    "education",
                    include_rollup_status=True,
                ),
            }
        )

    totals = {
        "sido_count": len(sidos),
        "municipality_count": len(municipalities),
        "configured_provider_count": len(
            {
                provider
                for row in municipalities
                for provider in row["configured_providers"]
            }
        ),
        "unmapped_configured_provider_count": len(
            selected_reference.unmapped_configured_providers
        ),
        "unmapped_configured_providers": list(
            selected_reference.unmapped_configured_providers
        ),
    }
    for scope in SCOPES:
        scope_summary = _summarize_scope(
            municipalities,
            scope,
            include_rollup_status=True,
        )
        scope_summary["unmapped_active_data_count"] = int(
            unmapped[scope]["active_data_count"]
        )
        scope_summary["unmapped_total_data_count"] = int(
            unmapped[scope]["total_data_count"]
        )
        scope_summary["unmapped_provider_count"] = len(unmapped[scope]["providers"])
        scope_summary["unmapped_active_provider_count"] = len(
            unmapped[scope]["active_providers"]
        )
        scope_summary["unmapped_active_provider_names"] = sorted(
            unmapped[scope]["active_providers"]
        )
        scope_summary["unmapped_provider_names"] = sorted(
            unmapped[scope]["providers"]
        )
        scope_summary["unmapped_configured_provider_count"] = len(
            selected_reference.unmapped_configured_by_scope.get(scope, ())
        )
        scope_summary["unmapped_configured_providers"] = list(
            selected_reference.unmapped_configured_by_scope.get(scope, ())
        )
        unmapped_configured_targets = (
            selected_reference.unmapped_configured_targets_by_scope.get(scope, ())
        )
        scope_summary["unmapped_configured_target_count"] = len(
            unmapped_configured_targets
        )
        scope_summary["unmapped_configured_targets"] = [
            dict(target) for target in unmapped_configured_targets
        ]
        totals[scope] = scope_summary

    return _without_internal_collection_fields({
        "available": True,
        "generated_at": _iso(generated_at or datetime.now(timezone.utc)),
        "cache_seconds": _cache_seconds(),
        "cache_policy": "source_revision_and_config_mtime",
        "municipality_source": "config/municipal_course_search_targets.yaml",
        "mapping_policy": {
            "method": "configured_provider_and_branch_location_derived",
            "category_policy": "authoritative_course_scope_filter",
            "configured_provider_policy": "production_schedule_and_aggregate_targets_with_scope_specific_region_mapping",
            "course_count_policy": "each_course_maps_to_one_direct_municipality",
            "rollup_policy": "parent_city_includes_direct_children_without_provider_duplication",
            "branch_count_policy": "unique_branch_ids_per_region_and_rollup",
            "latest_collection_policy": "coalesce(last_seen_at, first_seen_at, created_at); never updated_at",
        },
        "totals": totals,
        "sidos": sidos,
        "municipalities": municipalities,
    })


_CACHE_LOCK = threading.Lock()
_CACHE_DEADLINE = 0.0
_CACHE_SNAPSHOT: dict[str, Any] | None = None
_CACHE_SOURCE_KEY: tuple[Any, ...] | None = None


def _database_revision(session: Session) -> tuple[Any, ...]:
    course_row = session.query(
        func.count(models.Course.id),
        func.count(models.Course.id).filter(models.Course.is_active.is_(True)),
        func.max(models.Course.updated_at),
        func.max(models.Course.last_seen_at),
        func.max(models.Course.removed_at),
    ).one()
    branch_row = session.query(
        func.count(models.Branch.id),
        func.max(models.Branch.updated_at),
    ).one()
    return (
        int(course_row[0] or 0),
        int(course_row[1] or 0),
        course_row[2],
        course_row[3],
        course_row[4],
        int(branch_row[0] or 0),
        branch_row[1],
    )


def clear_region_collection_cache() -> None:
    global _CACHE_DEADLINE, _CACHE_SNAPSHOT, _CACHE_SOURCE_KEY
    with _CACHE_LOCK:
        _CACHE_DEADLINE = 0.0
        _CACHE_SNAPSHOT = None
        _CACHE_SOURCE_KEY = None


def get_region_collection_snapshot(
    session: Session,
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    global _CACHE_DEADLINE, _CACHE_SNAPSHOT, _CACHE_SOURCE_KEY
    now = time.monotonic()
    with _CACHE_LOCK:
        if (
            not force_refresh
            and _CACHE_SNAPSHOT is not None
            and now < _CACHE_DEADLINE
        ):
            return copy.deepcopy(_CACHE_SNAPSHOT)
        source_key = (
            _database_revision(session),
            _reference_config_signature(),
        )
        if (
            not force_refresh
            and _CACHE_SNAPSHOT is not None
            and source_key == _CACHE_SOURCE_KEY
        ):
            _CACHE_DEADLINE = time.monotonic() + _cache_seconds()
            return copy.deepcopy(_CACHE_SNAPSHOT)
        snapshot = build_region_collection_snapshot(session)
        _CACHE_SNAPSHOT = snapshot
        _CACHE_SOURCE_KEY = source_key
        _CACHE_DEADLINE = time.monotonic() + _cache_seconds()
        return copy.deepcopy(snapshot)
