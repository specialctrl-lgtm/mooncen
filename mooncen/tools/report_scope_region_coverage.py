from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

import yaml
from sqlalchemy import and_


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import models
from backend.database import SessionLocal
from backend.routers.courses import (
    _course_public_education_scope_filter,
    course_scope_filter,
)
from service_group import LOCAL_GOVERNMENT_EDUCATION_EXCLUDED_FACILITY_TOKENS


KST = ZoneInfo("Asia/Seoul")
DEFAULT_MUNICIPALITY_SOURCE = ROOT / "config" / "municipal_course_search_targets.yaml"
DEFAULT_COVERAGE_SOURCE = ROOT / "config" / "municipal_integrated_reservation_coverage.yaml"
DEFAULT_OPERATIONAL_SOURCE = (
    ROOT / "config" / "municipal_integrated_reservation_operational.yaml"
)
DEFAULT_TARGET_DIR = ROOT / "config" / "crawl_targets"
DEFAULT_LOCATION_OVERRIDE_SOURCE = (
    ROOT / "config" / "scope_region_location_overrides.yaml"
)
DEFAULT_OUTPUT_DIR = ROOT / "logs" / "scope_region_coverage"

STATUS_COLLECTED = "수집됨"
STATUS_CLASSIFICATION_REVIEW = "분류확인필요"
STATUS_HISTORICAL = "과거자료만"
STATUS_NOT_COLLECTED = "미수집"

SCOPE_LABELS = {
    "experience": "체험",
    "education": "교육",
}

SIDO_ALIASES = {
    "서울특별시": ("서울특별시", "서울시", "서울"),
    "부산광역시": ("부산광역시", "부산시", "부산"),
    "대구광역시": ("대구광역시", "대구시", "대구"),
    "인천광역시": ("인천광역시", "인천시", "인천"),
    "대전광역시": ("대전광역시", "대전시", "대전"),
    "울산광역시": ("울산광역시", "울산시", "울산"),
    "세종특별자치시": ("세종특별자치시", "세종시", "세종"),
    "경기도": ("경기도", "경기"),
    "강원특별자치도": ("강원특별자치도", "강원도", "강원"),
    "충청북도": ("충청북도", "충북"),
    "충청남도": ("충청남도", "충남"),
    "전북특별자치도": ("전북특별자치도", "전라북도", "전북"),
    "경상북도": ("경상북도", "경북"),
    "경상남도": ("경상남도", "경남"),
    "제주특별자치도": ("제주특별자치도", "제주도", "제주"),
    "전남광주통합특별시": (
        "전남광주통합특별시",
        "광주광역시",
        "광주시",
        "광주",
        "전라남도",
        "전남",
    ),
}

MUNICIPALITY_LIST_KEYS = (
    "covered_municipalities",
    "municipalities",
)
MUNICIPALITY_CODE_LIST_KEYS = (
    "row_municipality_codes",
)
MUNICIPALITY_VALUE_KEYS = (
    "municipality_full_name",
    "region",
    "branch",
    "address",
)


def compact_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[^0-9a-z가-힣]+", "", text)


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


@dataclass(frozen=True)
class Municipality:
    code: str
    sido: str
    sigungu: str
    full_name: str
    municipality_type: str


@dataclass
class MunicipalityIndex:
    municipalities: list[Municipality]
    by_full_name: dict[str, Municipality]
    aliases: dict[str, set[str]]
    local_aliases: dict[str, set[str]]

    @classmethod
    def build(cls, rows: Iterable[Mapping[str, Any]]) -> "MunicipalityIndex":
        municipalities = [
            Municipality(
                code=clean_text(row.get("code")),
                sido=clean_text(row.get("sido")),
                sigungu=clean_text(row.get("sigungu")),
                full_name=clean_text(row.get("full_name")),
                municipality_type=clean_text(row.get("municipality_type")),
            )
            for row in rows
            if clean_text(row.get("full_name"))
        ]
        by_full_name = {row.full_name: row for row in municipalities}
        aliases: dict[str, set[str]] = {}
        local_candidates: dict[str, set[str]] = defaultdict(set)
        prohibited_stems = {
            compact_text(alias)
            for values in SIDO_ALIASES.values()
            for alias in values
            if compact_text(alias)
        }

        for row in municipalities:
            row_aliases = _municipality_full_aliases(row)
            aliases[row.full_name] = {
                compact_text(alias) for alias in row_aliases if compact_text(alias)
            }

            sigungu_parts = row.sigungu.split()
            local_values = {row.sigungu}
            if sigungu_parts:
                local_values.add(sigungu_parts[-1])
            for value in tuple(local_values):
                value_compact = compact_text(value)
                if value_compact:
                    local_candidates[value_compact].add(row.full_name)
                stem = re.sub(r"(특별자치시|특별시|광역시|시|군|구)$", "", value)
                stem_compact = compact_text(stem)
                if (
                    len(stem_compact) >= 2
                    and stem_compact not in prohibited_stems
                ):
                    local_candidates[stem_compact].add(row.full_name)

        local_aliases: dict[str, set[str]] = defaultdict(set)
        for alias, names in local_candidates.items():
            if len(names) == 1:
                local_aliases[next(iter(names))].add(alias)

        return cls(
            municipalities=municipalities,
            by_full_name=by_full_name,
            aliases=aliases,
            local_aliases=dict(local_aliases),
        )

    def match_all(self, *values: Any) -> list[Municipality]:
        blob = compact_text(" ".join(clean_text(value) for value in values if clean_text(value)))
        if not blob:
            return []

        scores: dict[str, tuple[int, int]] = {}
        for municipality in self.municipalities:
            best_full = max(
                (
                    len(alias)
                    for alias in self.aliases.get(municipality.full_name, ())
                    if alias and alias in blob
                ),
                default=0,
            )
            best_local = max(
                (
                    len(alias)
                    for alias in self.local_aliases.get(municipality.full_name, ())
                    if alias and alias in blob
                ),
                default=0,
            )
            if best_full:
                scores[municipality.full_name] = (2, best_full)
            elif best_local:
                scores[municipality.full_name] = (1, best_local)

        if not scores:
            return []
        max_score = max(scores.values())
        strongest = {
            full_name
            for full_name, score in scores.items()
            if score == max_score
        }
        return sorted(
            (self.by_full_name[name] for name in strongest),
            key=lambda row: (len(compact_text(row.sigungu)), row.full_name),
            reverse=True,
        )

    def match_one(self, *values: Any) -> Municipality | None:
        matches = self.match_all(*values)
        return matches[0] if matches else None

    def match_within(
        self,
        full_names: Iterable[str],
        *values: Any,
    ) -> Municipality | None:
        blob = compact_text(
            " ".join(clean_text(value) for value in values if clean_text(value))
        )
        if not blob:
            return None

        scores: dict[str, tuple[int, int]] = {}
        for full_name in full_names:
            municipality = self.by_full_name.get(full_name)
            if municipality is None:
                continue
            full_score = max(
                (
                    len(alias)
                    for alias in self.aliases.get(full_name, ())
                    if alias and alias in blob
                ),
                default=0,
            )
            local_values = {
                compact_text(municipality.sigungu),
                compact_text(municipality.sigungu.split()[-1]),
            }
            local_score = max(
                (
                    len(alias)
                    for alias in local_values
                    if alias and alias in blob
                ),
                default=0,
            )
            if full_score:
                scores[full_name] = (2, full_score)
            elif local_score:
                scores[full_name] = (1, local_score)

        if not scores:
            return None
        max_score = max(scores.values())
        matches = [
            self.by_full_name[full_name]
            for full_name, score in scores.items()
            if score == max_score
        ]
        return matches[0] if len(matches) == 1 else None

    def exact_match(self, value: Any) -> Municipality | None:
        needle = compact_text(value)
        if not needle:
            return None
        exact = [
            self.by_full_name[full_name]
            for full_name, aliases in self.aliases.items()
            if needle in aliases
        ]
        if len(exact) == 1:
            return exact[0]
        return None


def _municipality_full_aliases(row: Municipality) -> set[str]:
    aliases = {row.full_name, f"{row.sido} {row.sigungu}"}
    for sido_alias in SIDO_ALIASES.get(row.sido, (row.sido,)):
        aliases.add(f"{sido_alias} {row.sigungu}")

    if row.sido == "전남광주통합특별시":
        code_prefix = row.code[:3]
        legacy_sido_aliases = (
            ("광주광역시", "광주")
            if code_prefix in {"122", "123"}
            else ("전라남도", "전남")
        )
        for sido_alias in legacy_sido_aliases:
            aliases.add(f"{sido_alias} {row.sigungu}")
    return aliases


def load_municipality_index(path: Path = DEFAULT_MUNICIPALITY_SOURCE) -> MunicipalityIndex:
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows = document.get("municipalities") or []
    if not rows:
        raise RuntimeError(f"No municipalities found in {path}")
    return MunicipalityIndex.build(rows)


def load_location_overrides(
    index: MunicipalityIndex,
    path: Path = DEFAULT_LOCATION_OVERRIDE_SOURCE,
) -> dict[tuple[str, str], Municipality]:
    if not path.exists():
        return {}

    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    result: dict[tuple[str, str], Municipality] = {}
    for row_number, row in enumerate(document.get("overrides") or [], start=1):
        if not isinstance(row, Mapping):
            raise RuntimeError(f"Invalid location override #{row_number} in {path}")

        provider = clean_text(row.get("provider")).upper()
        branch = compact_text(row.get("branch"))
        full_name = clean_text(row.get("municipality_full_name"))
        municipality = index.exact_match(full_name)
        if not provider or not branch or municipality is None:
            raise RuntimeError(
                f"Invalid location override #{row_number} in {path}: "
                f"provider={provider!r}, branch={row.get('branch')!r}, "
                f"municipality={full_name!r}"
            )

        key = (provider, branch)
        previous = result.get(key)
        if previous is not None and previous != municipality:
            raise RuntimeError(
                f"Conflicting location override for {provider}/{row.get('branch')} "
                f"in {path}"
            )
        result[key] = municipality
    return result


def _municipalities_from_mapping(
    value: Any,
    index: MunicipalityIndex,
) -> set[str]:
    if isinstance(value, Mapping):
        full_name = clean_text(value.get("full_name"))
        if full_name:
            match = index.exact_match(full_name) or index.match_one(full_name)
            return {match.full_name} if match else set()
        sido = clean_text(value.get("sido"))
        sigungu = clean_text(value.get("sigungu"))
        if sido or sigungu:
            match = index.exact_match(f"{sido} {sigungu}") or index.match_one(
                sido, sigungu
            )
            return {match.full_name} if match else set()
    if isinstance(value, str):
        match = index.exact_match(value)
        return {match.full_name} if match else set()
    return set()


def resolve_target_municipalities(
    target: Mapping[str, Any],
    index: MunicipalityIndex,
    *,
    include_region_fallback: bool = True,
    region_fallback_providers: Iterable[str] = (),
) -> set[str]:
    provider = clean_text(target.get("provider")).upper()
    by_code = {row.code: row for row in index.municipalities if row.code}
    explicit: set[str] = set()
    for key in MUNICIPALITY_LIST_KEYS:
        for value in target.get(key) or []:
            explicit.update(_municipalities_from_mapping(value, index))
    for key in MUNICIPALITY_CODE_LIST_KEYS:
        for value in target.get(key) or []:
            municipality = by_code.get(clean_text(value))
            if municipality is not None:
                explicit.add(municipality.full_name)
    municipality = by_code.get(clean_text(target.get("municipality_code")))
    if municipality is not None:
        explicit.add(municipality.full_name)
    explicit.update(
        _municipalities_from_mapping(
            target.get("municipality_full_name"),
            index,
        )
    )
    if explicit:
        return explicit

    exact: set[str] = set()
    for key in MUNICIPALITY_VALUE_KEYS[1:]:
        match = index.exact_match(target.get(key))
        if match is not None:
            exact.add(match.full_name)
    if exact:
        return exact

    inferred = index.match_one(
        target.get("municipality_full_name"),
        target.get("branch"),
        target.get("name"),
        target.get("address"),
    )
    if inferred is not None:
        return {inferred.full_name}

    reviewed_region_fallbacks = {
        clean_text(value).upper()
        for value in region_fallback_providers
        if clean_text(value)
    }
    region_hint = target.get("region")
    sido = _match_sido(region_hint)
    if not sido and provider in reviewed_region_fallbacks:
        region_hint = " ".join(
            clean_text(target.get(key))
            for key in ("region", "name", "branch")
            if clean_text(target.get(key))
        )
        sido = _match_sido_within(region_hint)
    if not sido or not (
        include_region_fallback or provider in reviewed_region_fallbacks
    ):
        return set()
    return {
        municipality.full_name
        for municipality in _municipalities_for_sido_hint(
            index,
            sido,
            region_hint,
        )
    }


def load_provider_municipalities(
    index: MunicipalityIndex,
    *,
    target_dir: Path = DEFAULT_TARGET_DIR,
    coverage_path: Path = DEFAULT_COVERAGE_SOURCE,
    operational_path: Path = DEFAULT_OPERATIONAL_SOURCE,
    include_region_fallback: bool = True,
    region_fallback_providers: Iterable[str] = (),
    include_targets: bool = True,
) -> dict[str, set[str]]:
    result: dict[str, set[str]] = defaultdict(set)

    if coverage_path.exists():
        document = yaml.safe_load(coverage_path.read_text(encoding="utf-8")) or {}
        for row in document.get("municipalities") or []:
            municipality = index.exact_match(row.get("full_name"))
            if municipality is None:
                continue
            providers: set[str] = set()
            for key in (
                "owner_providers",
                "promoted_providers",
                "yaml_owner_providers",
            ):
                providers.update(
                    clean_text(provider).upper()
                    for provider in row.get(key) or []
                    if clean_text(provider)
                )
            for provider in providers:
                result[provider].add(municipality.full_name)

    if operational_path.exists():
        document = yaml.safe_load(operational_path.read_text(encoding="utf-8")) or {}
        for entry in document.get("entries") or []:
            provider = clean_text(entry.get("provider")).upper()
            if not provider:
                continue
            for municipality in entry.get("municipalities") or []:
                result[provider].update(
                    _municipalities_from_mapping(municipality, index)
                )

    if include_targets:
        for path in sorted(target_dir.glob("*.yaml")):
            document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for target in document.get("targets") or []:
                if not isinstance(target, Mapping):
                    continue
                provider = clean_text(target.get("provider")).upper()
                if not provider:
                    continue
                result[provider].update(
                    resolve_target_municipalities(
                        target,
                        index,
                        include_region_fallback=include_region_fallback,
                        region_fallback_providers=region_fallback_providers,
                    )
                )

    return dict(result)


def _match_sido(value: Any) -> str:
    needle = compact_text(value)
    if not needle:
        return ""
    matches = [
        sido
        for sido, aliases in SIDO_ALIASES.items()
        if needle in {compact_text(alias) for alias in aliases}
    ]
    return matches[0] if len(matches) == 1 else ""


def _match_sido_within(value: Any) -> str:
    needle = compact_text(value)
    if not needle:
        return ""
    matches = {
        sido
        for sido, aliases in SIDO_ALIASES.items()
        if any(compact_text(alias) in needle for alias in aliases)
    }
    return next(iter(matches)) if len(matches) == 1 else ""


def _municipalities_for_sido_hint(
    index: MunicipalityIndex,
    sido: str,
    raw_hint: Any,
) -> list[Municipality]:
    rows = [row for row in index.municipalities if row.sido == sido]
    if sido != "전남광주통합특별시":
        return rows

    hint = compact_text(raw_hint)
    if "전남광주통합특별시" in hint:
        return rows
    if "광주" in hint:
        return [row for row in rows if row.code[:3] in {"122", "123"}]
    if "전남" in hint or "전라남" in hint:
        return [row for row in rows if row.code[:3] not in {"122", "123"}]
    return rows


@dataclass(frozen=True)
class CourseLocation:
    course_id: str
    provider: str
    branch_id: str
    branch_name: str
    branch_address: str
    facility_type: str
    facility_category: str
    venue_name: str
    venue_address: str
    is_active: bool

    @classmethod
    def from_row(cls, row: Any) -> "CourseLocation":
        return cls(
            course_id=str(row.id),
            provider=clean_text(row.provider).upper(),
            branch_id=str(row.branch_id or ""),
            branch_name=clean_text(row.branch_name),
            branch_address=clean_text(row.branch_address),
            facility_type=clean_text(row.facility_type),
            facility_category=clean_text(row.facility_category),
            venue_name=clean_text(row.venue_name),
            venue_address=clean_text(row.venue_address),
            is_active=bool(row.is_active),
        )

    @property
    def location_values(self) -> tuple[str, ...]:
        return (
            self.branch_name,
            self.branch_address,
            self.venue_name,
            self.venue_address,
        )


def resolve_course_municipality(
    row: CourseLocation,
    index: MunicipalityIndex,
    provider_municipalities: Mapping[str, set[str]],
    location_overrides: Mapping[tuple[str, str], Municipality] | None = None,
) -> Municipality | None:
    if location_overrides:
        override = location_overrides.get(
            (row.provider, compact_text(row.branch_name))
        )
        if override is not None:
            return override

    provider_names = provider_municipalities.get(row.provider, set())
    text_matches = index.match_all(*row.location_values)
    if provider_names:
        provider_matches = [
            match for match in text_matches if match.full_name in provider_names
        ]
        if provider_matches:
            return provider_matches[0]
        restricted_match = index.match_within(
            provider_names,
            *row.location_values,
        )
        if restricted_match is not None:
            return restricted_match
        if len(provider_names) == 1:
            return index.by_full_name[next(iter(provider_names))]

        provider_rows = [
            index.by_full_name[name]
            for name in provider_names
            if name in index.by_full_name
        ]
        provider_sidos = {municipality.sido for municipality in provider_rows}
        if text_matches and len(provider_sidos) > 1:
            return text_matches[0]

        parent_candidates = [
            municipality
            for municipality in provider_rows
            if any(
                other.sigungu.startswith(f"{municipality.sigungu} ")
                for other in provider_rows
                if other != municipality
            )
        ]
        if len(parent_candidates) == 1:
            parent = parent_candidates[0]
            descendants = [
                municipality
                for municipality in provider_rows
                if municipality.sigungu.startswith(f"{parent.sigungu} ")
            ]
            return descendants[0] if len(descendants) == 1 else parent
        return None
    return text_matches[0] if text_matches else None


@dataclass
class ScopeStats:
    all_courses: set[str] = field(default_factory=set)
    active_courses: set[str] = field(default_factory=set)
    all_branches: set[str] = field(default_factory=set)
    active_branches: set[str] = field(default_factory=set)
    all_providers: set[str] = field(default_factory=set)
    active_providers: set[str] = field(default_factory=set)

    def add(self, row: CourseLocation) -> None:
        self.all_courses.add(row.course_id)
        self.all_branches.add(row.branch_id)
        self.all_providers.add(row.provider)
        if row.is_active:
            self.active_courses.add(row.course_id)
            self.active_branches.add(row.branch_id)
            self.active_providers.add(row.provider)


def scope_status(
    stats: ScopeStats,
    *,
    classification_candidate_active: int = 0,
) -> str:
    if stats.active_courses:
        return STATUS_COLLECTED
    if classification_candidate_active:
        return STATUS_CLASSIFICATION_REVIEW
    if stats.all_courses:
        return STATUS_HISTORICAL
    return STATUS_NOT_COLLECTED


def _course_query(session, predicate):
    return (
        session.query(
            models.Course.id,
            models.Course.provider,
            models.Course.branch_id,
            models.Course.venue_name,
            models.Course.venue_address,
            models.Course.is_active,
            models.Branch.name.label("branch_name"),
            models.Branch.address.label("branch_address"),
            models.Branch.facility_type,
            models.Branch.facility_category,
        )
        .join(models.Branch, models.Branch.id == models.Course.branch_id)
        .filter(predicate)
        .yield_per(2_000)
    )


def looks_like_generic_municipal_branch(
    row: CourseLocation,
    municipality: Municipality,
    index: MunicipalityIndex,
) -> bool:
    metadata = compact_text(
        " ".join(
            (
                row.branch_name,
                row.facility_type,
                row.facility_category,
            )
        )
    )
    if any(
        compact_text(token) in metadata
        for token in LOCAL_GOVERNMENT_EDUCATION_EXCLUDED_FACILITY_TOKENS
    ):
        return False

    branch_name = compact_text(row.branch_name)
    if branch_name.startswith(compact_text("대한민국")):
        branch_name = branch_name[len(compact_text("대한민국")) :]
    exact_aliases = set(index.aliases.get(municipality.full_name, set()))
    exact_aliases.add(compact_text(municipality.sigungu))
    return bool(branch_name and branch_name in exact_aliases)


def collect_scope_rows(
    index: MunicipalityIndex,
    provider_municipalities: Mapping[str, set[str]],
    location_overrides: Mapping[tuple[str, str], Municipality] | None = None,
) -> tuple[
    dict[str, dict[str, ScopeStats]],
    dict[str, dict[str, int]],
    list[dict[str, Any]],
    dict[str, dict[str, int]],
]:
    by_scope: dict[str, dict[str, ScopeStats]] = {
        scope: defaultdict(ScopeStats) for scope in SCOPE_LABELS
    }
    unmapped: dict[
        tuple[str, str, str, str], dict[str, Any]
    ] = {}
    scope_totals: dict[str, dict[str, int]] = {
        scope: {
            "all": 0,
            "active": 0,
            "mapped_all": 0,
            "mapped_active": 0,
        }
        for scope in SCOPE_LABELS
    }
    classification_candidates: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "courses": set(),
            "branches": set(),
            "providers": set(),
        }
    )

    session = SessionLocal()
    try:
        for scope in SCOPE_LABELS:
            for db_row in _course_query(session, course_scope_filter(scope)):
                row = CourseLocation.from_row(db_row)
                totals = scope_totals[scope]
                totals["all"] += 1
                if row.is_active:
                    totals["active"] += 1

                municipality = resolve_course_municipality(
                    row,
                    index,
                    provider_municipalities,
                    location_overrides,
                )
                if municipality is None:
                    key = (
                        scope,
                        row.provider,
                        row.branch_name,
                        row.branch_address,
                    )
                    unknown = unmapped.setdefault(
                        key,
                        {
                            "scope": SCOPE_LABELS[scope],
                            "provider": row.provider,
                            "branch_name": row.branch_name,
                            "branch_address": row.branch_address,
                            "all_courses": 0,
                            "active_courses": 0,
                        },
                    )
                    unknown["all_courses"] += 1
                    if row.is_active:
                        unknown["active_courses"] += 1
                    continue

                totals["mapped_all"] += 1
                if row.is_active:
                    totals["mapped_active"] += 1
                by_scope[scope][municipality.full_name].add(row)

        unmanaged_public = and_(
            course_scope_filter("unmanaged"),
            _course_public_education_scope_filter(),
            models.Course.is_active.is_(True),
        )
        for db_row in _course_query(session, unmanaged_public):
            row = CourseLocation.from_row(db_row)
            municipality = resolve_course_municipality(
                row,
                index,
                provider_municipalities,
                location_overrides,
            )
            if municipality is None or not looks_like_generic_municipal_branch(
                row,
                municipality,
                index,
            ):
                continue
            candidate = classification_candidates[municipality.full_name]
            candidate["courses"].add(row.course_id)
            candidate["branches"].add(row.branch_id)
            candidate["providers"].add(row.provider)
    finally:
        session.close()

    candidate_counts = {
        full_name: {
            "active_courses": len(values["courses"]),
            "branches": len(values["branches"]),
            "providers": len(values["providers"]),
        }
        for full_name, values in classification_candidates.items()
    }
    return (
        by_scope,
        candidate_counts,
        sorted(
            unmapped.values(),
            key=lambda row: (
                -int(row["active_courses"]),
                row["scope"],
                row["provider"],
                row["branch_name"],
            ),
        ),
        scope_totals,
    )


def build_municipality_rows(
    index: MunicipalityIndex,
    by_scope: Mapping[str, Mapping[str, ScopeStats]],
    classification_candidates: Mapping[str, Mapping[str, int]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for municipality in index.municipalities:
        experience = by_scope["experience"].get(
            municipality.full_name,
            ScopeStats(),
        )
        education = by_scope["education"].get(
            municipality.full_name,
            ScopeStats(),
        )
        candidate = classification_candidates.get(municipality.full_name, {})
        candidate_active = int(candidate.get("active_courses", 0))
        rows.append(
            {
                "행정구역코드": municipality.code,
                "시도": municipality.sido,
                "시군구": municipality.sigungu,
                "전체명": municipality.full_name,
                "유형": municipality.municipality_type,
                "체험_상태": scope_status(experience),
                "체험_활성강좌": len(experience.active_courses),
                "체험_전체강좌": len(experience.all_courses),
                "체험_활성기관지점": len(experience.active_branches),
                "체험_활성프로바이더": len(experience.active_providers),
                "교육_상태": scope_status(
                    education,
                    classification_candidate_active=candidate_active,
                ),
                "교육_활성강좌": len(education.active_courses),
                "교육_전체강좌": len(education.all_courses),
                "교육_활성기관지점": len(education.active_branches),
                "교육_활성프로바이더": len(education.active_providers),
                "교육_분류누락의심_활성강좌": candidate_active,
                "교육_분류누락의심_기관지점": int(candidate.get("branches", 0)),
            }
        )
    return rows


def build_sido_summary(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["시도"])].append(row)

    result: list[dict[str, Any]] = []
    for sido, sido_rows in grouped.items():
        count = len(sido_rows)
        experience_collected = sum(
            row["체험_상태"] == STATUS_COLLECTED for row in sido_rows
        )
        education_collected = sum(
            row["교육_상태"] == STATUS_COLLECTED for row in sido_rows
        )
        education_review = sum(
            row["교육_상태"] == STATUS_CLASSIFICATION_REVIEW for row in sido_rows
        )
        both_collected = sum(
            row["체험_상태"] == STATUS_COLLECTED
            and row["교육_상태"] == STATUS_COLLECTED
            for row in sido_rows
        )
        result.append(
            {
                "시도": sido,
                "시군구수": count,
                "체험_수집지역": experience_collected,
                "체험_수집률": (
                    round(experience_collected / count * 100, 1) if count else 0.0
                ),
                "체험_활성강좌": sum(
                    int(row["체험_활성강좌"]) for row in sido_rows
                ),
                "교육_수집지역": education_collected,
                "교육_수집률": (
                    round(education_collected / count * 100, 1) if count else 0.0
                ),
                "교육_분류확인필요지역": education_review,
                "교육_활성강좌": sum(
                    int(row["교육_활성강좌"]) for row in sido_rows
                ),
                "둘다_수집지역": both_collected,
            }
        )
    return result


def _write_csv(path: Path, rows: list[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _mapping_percentage(mapped: int, total: int) -> float:
    return round(mapped / total * 100, 1) if total else 100.0


def _markdown_table(headers: list[str], rows: Iterable[Iterable[Any]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(str(value).replace("|", "\\|") for value in row)
            + " |"
        )
    return lines


def write_markdown_report(
    path: Path,
    *,
    generated_at: datetime,
    municipality_rows: list[Mapping[str, Any]],
    sido_rows: list[Mapping[str, Any]],
    unmapped_rows: list[Mapping[str, Any]],
    scope_totals: Mapping[str, Mapping[str, int]],
    municipality_source: Path,
    location_override_source: Path,
    location_override_count: int,
) -> None:
    lines = [
        "# 체험·교육 시도/시군구 수집 현황",
        "",
        f"- 생성 시각: {generated_at.isoformat(timespec='seconds')}",
        f"- 행정구역 기준: `{municipality_source.relative_to(ROOT)}` "
        f"({len(municipality_rows)}개 시·군·구)",
        f"- 주소 보정: `{location_override_source.relative_to(ROOT)}` "
        f"({location_override_count}개 지점)",
        "- 수집됨: 현재 DB에서 `is_active=true`인 강좌가 1건 이상",
        "- 과거자료만: 비활성 이력은 있으나 활성 강좌가 없음",
        "- 분류확인필요: 공공교육 강좌는 활성 상태이나 행정시설 명칭 규칙에서 빠진 것으로 의심됨",
        "- 교육 범위: 시청·군청·구청, 주민센터·주민자치·행정복지센터·자치회관만 포함",
        "- 체험 범위: 현재 운영 API의 `scope=experience` 분류와 동일",
        "",
        "## 지역 매핑 완성도",
        "",
    ]
    lines.extend(
        _markdown_table(
            ["대카테고리", "활성 전체", "활성 지역확정", "지역확정률", "전체 이력"],
            (
                (
                    SCOPE_LABELS[scope],
                    totals["active"],
                    totals["mapped_active"],
                    f"{_mapping_percentage(totals['mapped_active'], totals['active']):.1f}%",
                    totals["all"],
                )
                for scope, totals in scope_totals.items()
            ),
        )
    )
    lines.extend(["", "## 시도 요약", ""])
    lines.extend(
        _markdown_table(
            [
                "시도",
                "시군구",
                "체험 수집",
                "체험률",
                "교육 수집",
                "교육률",
                "교육 분류확인",
                "둘 다",
            ],
            (
                (
                    row["시도"],
                    row["시군구수"],
                    row["체험_수집지역"],
                    f"{row['체험_수집률']:.1f}%",
                    row["교육_수집지역"],
                    f"{row['교육_수집률']:.1f}%",
                    row["교육_분류확인필요지역"],
                    row["둘다_수집지역"],
                )
                for row in sido_rows
            ),
        )
    )

    lines.extend(["", "## 시군구 전체", ""])
    lines.extend(
        _markdown_table(
            [
                "시도",
                "시군구",
                "유형",
                "체험",
                "체험 활성",
                "체험 지점",
                "교육",
                "교육 활성",
                "교육 지점",
                "교육 누락의심",
            ],
            (
                (
                    row["시도"],
                    row["시군구"],
                    row["유형"],
                    row["체험_상태"],
                    row["체험_활성강좌"],
                    row["체험_활성기관지점"],
                    row["교육_상태"],
                    row["교육_활성강좌"],
                    row["교육_활성기관지점"],
                    row["교육_분류누락의심_활성강좌"],
                )
                for row in municipality_rows
            ),
        )
    )

    lines.extend(["", "## 지역 미확정", ""])
    active_unmapped = [
        row for row in unmapped_rows if int(row.get("active_courses", 0)) > 0
    ]
    if active_unmapped:
        lines.append(
            f"활성 강좌가 있으나 지역을 확정하지 못한 지점 {len(active_unmapped)}개입니다. "
            "CSV에는 전체 목록이 포함됩니다."
        )
        lines.append("")
        lines.extend(
            _markdown_table(
                ["범주", "프로바이더", "지점", "활성", "주소"],
                (
                    (
                        row["scope"],
                        row["provider"],
                        row["branch_name"],
                        row["active_courses"],
                        row["branch_address"],
                    )
                    for row in active_unmapped[:50]
                ),
            )
        )
    else:
        lines.append("활성 강좌의 지역 미확정 건이 없습니다.")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def generate_report(
    *,
    municipality_source: Path = DEFAULT_MUNICIPALITY_SOURCE,
    location_override_source: Path = DEFAULT_LOCATION_OVERRIDE_SOURCE,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> dict[str, Any]:
    generated_at = datetime.now(KST)
    index = load_municipality_index(municipality_source)
    provider_municipalities = load_provider_municipalities(index)
    location_overrides = load_location_overrides(
        index,
        location_override_source,
    )
    by_scope, classification_candidates, unmapped, scope_totals = collect_scope_rows(
        index,
        provider_municipalities,
        location_overrides,
    )
    municipality_rows = build_municipality_rows(
        index,
        by_scope,
        classification_candidates,
    )
    sido_rows = build_sido_summary(municipality_rows)

    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = generated_at.strftime("%Y%m%d_%H%M%S")
    municipality_csv = output_dir / f"scope_region_coverage_{suffix}.csv"
    sido_csv = output_dir / f"scope_region_coverage_sido_{suffix}.csv"
    unmapped_csv = output_dir / f"scope_region_coverage_unmapped_{suffix}.csv"
    markdown = output_dir / f"scope_region_coverage_{suffix}.md"

    _write_csv(municipality_csv, municipality_rows)
    _write_csv(sido_csv, sido_rows)
    _write_csv(unmapped_csv, unmapped)
    write_markdown_report(
        markdown,
        generated_at=generated_at,
        municipality_rows=municipality_rows,
        sido_rows=sido_rows,
        unmapped_rows=unmapped,
        scope_totals=scope_totals,
        municipality_source=municipality_source,
        location_override_source=location_override_source,
        location_override_count=len(location_overrides),
    )

    return {
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "municipalities": len(municipality_rows),
        "provider_municipality_mappings": len(provider_municipalities),
        "location_overrides": len(location_overrides),
        "scope_totals": scope_totals,
        "status_totals": {
            scope: {
                status: sum(
                    row[f"{label}_상태"] == status for row in municipality_rows
                )
                for status in (
                    STATUS_COLLECTED,
                    STATUS_CLASSIFICATION_REVIEW,
                    STATUS_HISTORICAL,
                    STATUS_NOT_COLLECTED,
                )
            }
            for scope, label in SCOPE_LABELS.items()
        },
        "paths": {
            "municipality_csv": str(municipality_csv),
            "sido_csv": str(sido_csv),
            "unmapped_csv": str(unmapped_csv),
            "markdown": str(markdown),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report experience/education collection coverage by municipality."
    )
    parser.add_argument(
        "--municipality-source",
        type=Path,
        default=DEFAULT_MUNICIPALITY_SOURCE,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
    )
    parser.add_argument(
        "--location-overrides",
        type=Path,
        default=DEFAULT_LOCATION_OVERRIDE_SOURCE,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = generate_report(
        municipality_source=args.municipality_source.resolve(),
        location_override_source=args.location_overrides.resolve(),
        output_dir=args.output_dir.resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
