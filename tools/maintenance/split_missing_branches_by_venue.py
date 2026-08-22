from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from psycopg2.extras import Json


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from DB.db_utils import get_db_cursor
from tools.maintenance.backfill_missing_branch_addresses import (
    AddressCandidate,
    NaverResolver,
    administrative_center_search_name,
    address_matches_locality,
    branch_locality,
    build_source_indexes,
    choose_unique_source,
    clean_text,
    compact,
    external_resolution,
    embedded_course_address,
    embedded_road_address,
    facility_stem,
    fetch_address_sources,
    fetch_missing_branches,
    has_multiple_venues,
    is_ambiguous_facility_name,
    is_non_physical_name,
    load_naver_api_credentials,
    load_provider_localities,
    names_overlap,
    place_candidate_score,
    normalize_stored_address,
    road_address_key,
    source_candidate,
    unique_address,
)


@dataclass
class VenueGroup:
    parent: dict[str, Any]
    facility_name: str
    courses: list[dict[str, Any]]

    @property
    def active_courses(self) -> int:
        return sum(bool(row.get("is_active")) for row in self.courses)

    @property
    def venue_names(self) -> list[str]:
        return sorted(
            {
                clean_text(row.get("venue_name"))
                for row in self.courses
                if clean_text(row.get("venue_name"))
            }
        )


@dataclass
class VenueResolution:
    group: VenueGroup
    candidate: AddressCandidate
    method: str


VENUE_PARENT_OVERRIDES: dict[str, dict[str, str]] = {
    "MUNI_WWW_HAEUNDAE_GO_KR_E2AD27FA": {
        "고운홀": "해운대문화회관",
        "제2연습실": "해운대문화회관",
        "제3연습실": "해운대문화회관",
        "회의실": "해운대문화회관",
    },
}
VENUE_TEXT_NAME_OVERRIDES: dict[str, tuple[tuple[str, str], ...]] = {
    "MUNI_WWW_GOYANG_GO_KR_AFE8FBDD": (
        ("고양특례시문예회관", "고양시문예회관"),
    ),
    "MUNI_RESVE_YONGIN_GO_KR_221336AC": (
        ("농촌파크로80-1", "용인농촌테마파크 종합체험관"),
        ("처인성로673", "처인성역사교육관"),
        ("강남서로38", "기흥구 꿈이룸 안전체험교실"),
        ("풍덕천로86", "수지구 꿈이룸 안전체험교실"),
    ),
}
FACILITY_NAME_SUFFIXES = (
    "체험관",
    "교육관",
    "문예회관",
    "문화회관",
    "평생학습관",
    "행정복지센터",
    "주민센터",
    "도서관",
    "복지관",
    "문화센터",
    "가족센터",
    "청소년문화의집",
    "센터",
    "공방",
    "스튜디오",
    "서점",
    "학습공간",
    "교육장",
    "이용시설",
)


def venue_group_report_key(
    provider: Any,
    parent_name: Any,
    facility_name: Any,
    venue_names: Any,
) -> tuple[str, str, str, str]:
    if isinstance(venue_names, list):
        venue_text = " | ".join(venue_names)
    else:
        venue_text = clean_text(venue_names)
    return (
        clean_text(provider).upper(),
        clean_text(parent_name),
        clean_text(facility_name),
        venue_text,
    )


def facility_name_without_address(provider: Any, value: Any) -> str:
    raw = clean_text(value)
    address = embedded_road_address(raw)
    if not address:
        return ""
    text = clean_text(raw.replace(address, " "))
    text = clean_text(re.sub(r"지도\s*보기", " ", text))
    text = clean_text(
        re.sub(
            (
                r"(?<![가-힣])(?:서울|부산|대구|인천|광주|대전|울산|세종|"
                r"경기|강원|충북|충남|전북|전남|경북|경남|제주)(?![가-힣])"
            ),
            " ",
            text,
        )
    )
    text = clean_text(
        re.sub(
            (
                r"(?<![가-힣0-9])[가-힣]+"
                r"(?:특별시|광역시|특별자치시|특별자치도|도|시|군|구|읍|면|동)"
                r"(?![가-힣0-9])"
            ),
            " ",
            text,
        )
    )
    text = clean_text(
        re.sub(
            (
                r"\b(?:지하\s*)?\d+\s*층\b.*$"
                r"|\b\d+\s*호\b.*$"
                r"|\b\d+\s*동(?:\s*\d+(?:[.-]\d+)*\s*라인)?\b.*$"
            ),
            " ",
            text,
            flags=re.IGNORECASE,
        )
    ).strip(" ()[],.-")
    if not text:
        return ""
    candidate = facility_stem(provider, text)
    if not candidate or embedded_road_address(candidate):
        return ""
    if any(candidate.endswith(suffix) for suffix in FACILITY_NAME_SUFFIXES):
        return candidate
    return ""


def top_level_comma_parts(value: Any) -> list[str]:
    text = clean_text(value)
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for character in text:
        if character in "([":
            depth += 1
        elif character in ")]" and depth:
            depth -= 1
        if character == "," and depth == 0:
            parts.append(clean_text("".join(current)))
            current = []
            continue
        current.append(character)
    parts.append(clean_text("".join(current)))
    return [part for part in parts if part]


def stable_branch_code(provider: str, name: str) -> str:
    source = "|".join((provider, clean_text(name), ""))
    digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:12].upper()
    slug = re.sub(r"[^A-Za-z0-9가-힣]+", "_", clean_text(name)).strip("_")[:28]
    if slug:
        return f"{slug}_{digest}"[:50]
    return f"{provider[:32]}_{digest}"[:50]


def venue_facility_name(provider: Any, venue_name: Any) -> tuple[str, str]:
    raw = clean_text(venue_name)
    provider_key = clean_text(provider).upper()
    if not raw:
        return "", "empty_venue"
    if is_non_physical_name(raw):
        return "", "non_physical_venue"
    if has_multiple_venues(raw):
        return "", "multiple_venues"
    override = VENUE_PARENT_OVERRIDES.get(provider_key, {}).get(raw)
    if override:
        return override, ""
    raw_key = compact(raw)
    for token, target_name in VENUE_TEXT_NAME_OVERRIDES.get(
        provider_key,
        (),
    ):
        if compact(token) in raw_key:
            return target_name, ""
    comma_parts = top_level_comma_parts(raw)
    if len(comma_parts) > 1:
        part_names = [
            facility_stem(provider, part)
            for part in comma_parts
            if facility_stem(provider, part)
        ]
        part_keys = {compact(name) for name in part_names}
        if len(part_names) >= 2 and len(part_keys) == 1:
            return min(part_names, key=len), ""
    name = facility_stem(provider, raw)
    if embedded_road_address(name):
        name = facility_name_without_address(provider, raw)
        if not name:
            return "", "address_without_facility_name"
    if name == "전남광주통합특별시서구가족센터":
        name = "서구가족센터"
    if not name:
        return "", "room_only_or_unusable"
    if is_non_physical_name(name):
        return "", "non_physical_venue"
    if is_ambiguous_facility_name(name):
        return "", "ambiguous_facility"
    return name, ""


def load_groups(
    provider: str | None,
) -> tuple[list[VenueGroup], list[dict[str, Any]], Counter[str]]:
    parents = fetch_missing_branches(provider, True, None)
    if not parents:
        return [], [], Counter()
    parents_by_id = {str(row["id"]): row for row in parents}
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT
                c.id::text,
                c.branch_id::text,
                c.provider,
                c.provider_course_id,
                c.title,
                c.venue_name,
                c.venue_address,
                c.raw_url,
                c.is_active
            FROM courses c
            WHERE c.branch_id = ANY(%(branch_ids)s::uuid[])
            ORDER BY c.branch_id, c.provider_course_id
            """,
            {"branch_ids": list(parents_by_id)},
        )
        courses = [dict(row) for row in cursor.fetchall()]

    grouped: dict[tuple[str, str], VenueGroup] = {}
    rejected: list[dict[str, Any]] = []
    reasons: Counter[str] = Counter()
    for course in courses:
        parent = parents_by_id[course["branch_id"]]
        facility_name, reason = venue_facility_name(
            parent.get("provider"),
            course.get("venue_name"),
        )
        if reason:
            if course.get("is_active"):
                rejected.append({**course, "parent": parent, "reason": reason})
                reasons[reason] += 1
            continue
        key = (course["branch_id"], compact(facility_name))
        if key not in grouped:
            grouped[key] = VenueGroup(parent, facility_name, [])
        grouped[key].courses.append(course)

    active_groups = [group for group in grouped.values() if group.active_courses]
    active_groups.sort(
        key=lambda item: (
            -item.active_courses,
            clean_text(item.parent.get("provider")),
            item.facility_name,
        )
    )
    return active_groups, rejected, reasons


def resolve_group(
    group: VenueGroup,
    locality: str,
    by_name: dict[str, list[dict[str, Any]]],
    by_stem: dict[str, list[dict[str, Any]]],
    naver: NaverResolver | None,
) -> tuple[VenueResolution | None, str]:
    active_courses = [
        row for row in group.courses if bool(row.get("is_active"))
    ]
    addresses = [
        row.get("venue_address")
        for row in group.courses
        if clean_text(row.get("venue_address"))
    ]
    active_addresses = [
        row.get("venue_address")
        for row in active_courses
        if clean_text(row.get("venue_address"))
    ]
    course_address = (
        unique_address(active_addresses, locality)
        if active_courses and len(active_addresses) == len(active_courses)
        else ""
    )
    if course_address:
        return (
            VenueResolution(
                group,
                AddressCandidate(
                    address=course_address,
                    lat=None,
                    lon=None,
                    address_source="COURSE_VENUE_ADDRESS",
                    coordinate_source=None,
                    confidence=90,
                    verified=True,
                    query="unique course.venue_address",
                    matched_name=group.facility_name,
                ),
                "course_venue_address",
            ),
            "",
        )

    key = compact(group.facility_name)
    source = choose_unique_source(by_name.get(key, []), locality)
    if not source:
        source = choose_unique_source(by_stem.get(key, []), locality)
    if source:
        return (
            VenueResolution(
                group,
                source_candidate(source, "TRUSTED_VENUE_STEM"),
                "trusted_venue_stem",
            ),
            "",
        )

    pseudo_branch = {
        "id": group.parent["id"],
        "provider": group.parent["provider"],
        "name": group.facility_name,
        "course_addresses": addresses,
        "course_venue_names": group.venue_names,
        "course_raw_address_texts": group.venue_names,
    }
    embedded_address = embedded_course_address(pseudo_branch, locality)
    if embedded_address:
        return (
            VenueResolution(
                group,
                AddressCandidate(
                    address=embedded_address,
                    lat=None,
                    lon=None,
                    address_source="OFFICIAL_COURSE_EMBEDDED_ADDRESS",
                    coordinate_source=None,
                    confidence=90,
                    verified=True,
                    query="official course venue_name",
                    matched_name=group.facility_name,
                ),
                "course_embedded_address",
            ),
            "",
        )

    if not naver:
        return None, "no_trusted_database_match"
    resolution, reason = external_resolution(
        pseudo_branch,
        locality,
        None,
        naver,
    )
    if not resolution:
        return None, reason
    return (
        VenueResolution(group, resolution.candidate, resolution.method),
        "",
    )


def load_audited_report_resolutions(
    report_path: Path,
    groups: list[VenueGroup],
    provider_localities: dict[str, str],
    min_score: int,
) -> tuple[list[VenueResolution], list[str]]:
    groups_by_key = {
        venue_group_report_key(
            group.parent["provider"],
            group.parent["name"],
            group.facility_name,
            group.venue_names,
        ): group
        for group in groups
    }
    errors: list[str] = []
    resolutions: list[VenueResolution] = []
    with report_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    seen: set[tuple[str, str, str, str]] = set()
    for index, row in enumerate(rows, start=2):
        key = venue_group_report_key(
            row.get("provider"),
            row.get("parent_name"),
            row.get("facility_name"),
            row.get("venue_names"),
        )
        group = groups_by_key.get(key)
        if not group:
            errors.append(f"row {index}: current venue group not found")
            continue
        if key in seen:
            errors.append(f"row {index}: duplicate venue group")
            continue
        seen.add(key)
        method = clean_text(row.get("method"))
        if method not in {
            "course_venue_address",
            "course_embedded_address",
            "course_embedded_address_naver",
            "naver_local",
        }:
            errors.append(f"row {index}: unsupported report method {method!r}")
            continue
        lat_text = clean_text(row.get("lat"))
        lon_text = clean_text(row.get("lon"))
        if bool(lat_text) != bool(lon_text):
            errors.append(f"row {index}: incomplete coordinates")
            continue
        try:
            lat = float(lat_text) if lat_text else None
            lon = float(lon_text) if lon_text else None
            confidence = int(row["confidence"])
        except (TypeError, ValueError):
            errors.append(f"row {index}: invalid coordinates/confidence")
            continue
        if (
            lat is not None
            and lon is not None
            and not (33.0 <= lat <= 39.5 and 124.0 <= lon <= 132.0)
        ):
            errors.append(f"row {index}: coordinates outside South Korea")
            continue
        locality = branch_locality(group.parent, provider_localities)
        address = normalize_stored_address(row.get("address"))
        if not address_matches_locality(address, locality):
            errors.append(f"row {index}: address/locality mismatch")
            continue

        if method == "course_venue_address":
            active_courses = [
                course
                for course in group.courses
                if bool(course.get("is_active"))
            ]
            active_addresses = [
                course.get("venue_address")
                for course in active_courses
                if clean_text(course.get("venue_address"))
            ]
            source_address = (
                unique_address(active_addresses, locality)
                if active_courses
                and len(active_addresses) == len(active_courses)
                else ""
            )
            if (
                not source_address
                or road_address_key(source_address)
                != road_address_key(address)
            ):
                errors.append(
                    f"row {index}: course venue address no longer matches"
                )
                continue
            if lat is not None or confidence < 90:
                errors.append(
                    f"row {index}: course venue evidence changed"
                )
                continue
            address = source_address
            address_source = "COURSE_VENUE_ADDRESS_AUDITED_REPORT"
            coordinate_source = None
        elif method == "naver_local":
            if lat is None or lon is None:
                errors.append(f"row {index}: map result has no coordinates")
                continue
            target_name = (
                administrative_center_search_name(group.facility_name)
                or group.facility_name
            )
            score = place_candidate_score(
                target_name,
                locality,
                row.get("matched_name"),
                address,
                {"establishment"},
                group.parent["provider"],
            )
            if score < min_score or confidence < min_score:
                errors.append(
                    f"row {index}: external name score {score} below {min_score}"
                )
                continue
            address_source = "NAVER_LOCAL_SEARCH_AUDITED_REPORT"
            coordinate_source = "NAVER_LOCAL_SEARCH_AUDITED_REPORT"
        else:
            pseudo_branch = {
                "provider": group.parent["provider"],
                "name": group.facility_name,
                "course_venue_names": group.venue_names,
                "course_raw_address_texts": group.venue_names,
            }
            source_address = embedded_course_address(
                pseudo_branch,
                locality,
            )
            if (
                not source_address
                or road_address_key(source_address) != road_address_key(address)
            ):
                errors.append(
                    f"row {index}: embedded source address no longer matches"
                )
                continue
            address = source_address
            address_source = "COURSE_EMBEDDED_ADDRESS_AUDITED_REPORT"
            coordinate_source = (
                "NAVER_LOCAL_SEARCH_AUDITED_REPORT"
                if lat is not None
                else None
            )
            if (
                method == "course_embedded_address_naver"
                and lat is None
            ):
                errors.append(f"row {index}: map result has no coordinates")
                continue
            if method == "course_embedded_address" and lat is not None:
                errors.append(
                    f"row {index}: embedded address evidence changed"
                )
                continue

        resolutions.append(
            VenueResolution(
                group,
                AddressCandidate(
                    address=address,
                    lat=lat,
                    lon=lon,
                    address_source=address_source,
                    coordinate_source=coordinate_source,
                    confidence=confidence,
                    verified=True,
                    query=f"audited report {report_path.name}",
                    matched_name=clean_text(row.get("matched_name")),
                ),
                method,
            )
        )
    return resolutions, errors


def load_existing_branches() -> dict[str, list[dict[str, Any]]]:
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT id::text, provider, branch_code, name, address
            FROM branches
            WHERE NULLIF(btrim(address), '') IS NOT NULL
            ORDER BY provider, name
            """
        )
        result: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in cursor.fetchall():
            result[clean_text(row["provider"]).upper()].append(dict(row))
        return result


def matching_existing_branch(
    resolution: VenueResolution,
    existing: dict[str, list[dict[str, Any]]],
) -> str:
    provider = clean_text(resolution.group.parent.get("provider")).upper()
    address_key = road_address_key(resolution.candidate.address)
    matches = [
        row
        for row in existing.get(provider, [])
        if names_overlap(resolution.group.facility_name, row.get("name"))
        and road_address_key(row.get("address")) == address_key
    ]
    return matches[0]["id"] if len(matches) == 1 else ""


def apply_resolutions(
    resolutions: list[VenueResolution],
) -> tuple[int, int, int]:
    existing = load_existing_branches()
    created_or_updated: set[str] = set()
    updated_courses = 0
    old_branch_ids = sorted(
        {str(item.group.parent["id"]) for item in resolutions}
    )
    with get_db_cursor() as cursor:
        for item in resolutions:
            group = item.group
            candidate = item.candidate
            provider = clean_text(group.parent["provider"]).upper()
            target_branch_id = matching_existing_branch(item, existing)
            if not target_branch_id:
                branch_code = stable_branch_code(provider, group.facility_name)
                locality = clean_text(group.parent.get("region_sido"))
                sigungu = clean_text(group.parent.get("region_sigungu"))
                cursor.execute(
                    """
                    INSERT INTO branches (
                        provider,
                        branch_code,
                        name,
                        address,
                        website_url,
                        address_source,
                        lat,
                        lon,
                        coordinate_source,
                        location_confidence,
                        location_verified,
                        location_checked_at,
                        location_query,
                        region_sido,
                        region_sigungu,
                        basic_info
                    )
                    VALUES (
                        %(provider)s,
                        %(branch_code)s,
                        %(name)s,
                        %(address)s,
                        %(website_url)s,
                        %(address_source)s,
                        %(lat)s,
                        %(lon)s,
                        %(coordinate_source)s,
                        %(confidence)s,
                        %(verified)s,
                        CURRENT_TIMESTAMP,
                        %(query)s,
                        %(region_sido)s,
                        %(region_sigungu)s,
                        %(basic_info)s
                    )
                    ON CONFLICT (provider, branch_code)
                    DO UPDATE SET
                        name = EXCLUDED.name,
                        address = EXCLUDED.address,
                        website_url = COALESCE(
                            NULLIF(EXCLUDED.website_url, ''),
                            branches.website_url
                        ),
                        address_source = EXCLUDED.address_source,
                        lat = COALESCE(EXCLUDED.lat, branches.lat),
                        lon = COALESCE(EXCLUDED.lon, branches.lon),
                        coordinate_source = COALESCE(
                            EXCLUDED.coordinate_source,
                            branches.coordinate_source
                        ),
                        location_confidence = GREATEST(
                            COALESCE(branches.location_confidence, 0),
                            COALESCE(EXCLUDED.location_confidence, 0)
                        ),
                        location_verified = COALESCE(
                            branches.location_verified,
                            FALSE
                        ) OR COALESCE(EXCLUDED.location_verified, FALSE),
                        location_checked_at = CURRENT_TIMESTAMP,
                        location_query = EXCLUDED.location_query,
                        region_sido = COALESCE(
                            NULLIF(EXCLUDED.region_sido, ''),
                            branches.region_sido
                        ),
                        region_sigungu = COALESCE(
                            NULLIF(EXCLUDED.region_sigungu, ''),
                            branches.region_sigungu
                        ),
                        basic_info = COALESCE(branches.basic_info, '{}'::jsonb)
                            || EXCLUDED.basic_info,
                        updated_at = CURRENT_TIMESTAMP
                    RETURNING id::text
                    """,
                    {
                        "provider": provider,
                        "branch_code": branch_code,
                        "name": group.facility_name,
                        "address": normalize_stored_address(candidate.address),
                        "website_url": clean_text(
                            group.parent.get("website_url")
                        ),
                        "address_source": candidate.address_source,
                        "lat": candidate.lat,
                        "lon": candidate.lon,
                        "coordinate_source": candidate.coordinate_source,
                        "confidence": candidate.confidence,
                        "verified": candidate.verified,
                        "query": candidate.query,
                        "region_sido": locality,
                        "region_sigungu": sigungu,
                        "basic_info": Json(
                            {
                                "venue_split": {
                                    "method": item.method,
                                    "parent_branch_id": str(
                                        group.parent["id"]
                                    ),
                                }
                            }
                        ),
                    },
                )
                target_branch_id = str(cursor.fetchone()["id"])
            created_or_updated.add(target_branch_id)

            course_ids = [row["id"] for row in group.courses]
            cursor.execute(
                """
                UPDATE courses
                SET branch_id = %(branch_id)s,
                    venue_address = %(address)s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ANY(%(course_ids)s::uuid[])
                """,
                {
                    "branch_id": target_branch_id,
                    "address": normalize_stored_address(candidate.address),
                    "course_ids": course_ids,
                },
            )
            updated_courses += cursor.rowcount

        cursor.execute(
            """
            DELETE FROM branches b
            WHERE b.id = ANY(%(old_branch_ids)s::uuid[])
              AND NOT EXISTS (
                    SELECT 1 FROM courses c WHERE c.branch_id = b.id
                  )
            RETURNING b.id
            """,
            {"old_branch_ids": old_branch_ids},
        )
        deleted_branches = cursor.rowcount
    return len(created_or_updated), updated_courses, deleted_branches


def write_reports(
    output_dir: Path,
    resolutions: list[VenueResolution],
    unresolved: list[tuple[VenueGroup, str]],
    rejected: list[dict[str, Any]],
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    resolved_path = output_dir / f"venue_split_resolved_{stamp}.csv"
    unresolved_path = output_dir / f"venue_split_unresolved_{stamp}.csv"
    with resolved_path.open("w", encoding="utf-8-sig", newline="") as handle:
        fieldnames = [
            "provider",
            "parent_name",
            "facility_name",
            "active_courses",
            "total_courses",
            "method",
            "matched_name",
            "address",
            "lat",
            "lon",
            "confidence",
            "venue_names",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in resolutions:
            writer.writerow(
                {
                    "provider": item.group.parent["provider"],
                    "parent_name": item.group.parent["name"],
                    "facility_name": item.group.facility_name,
                    "active_courses": item.group.active_courses,
                    "total_courses": len(item.group.courses),
                    "method": item.method,
                    "matched_name": item.candidate.matched_name,
                    "address": item.candidate.address,
                    "lat": item.candidate.lat,
                    "lon": item.candidate.lon,
                    "confidence": item.candidate.confidence,
                    "venue_names": " | ".join(item.group.venue_names),
                }
            )

    with unresolved_path.open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        fieldnames = [
            "provider",
            "parent_name",
            "facility_name",
            "active_courses",
            "reason",
            "venue_names",
            "provider_course_id",
            "title",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for group, reason in unresolved:
            writer.writerow(
                {
                    "provider": group.parent["provider"],
                    "parent_name": group.parent["name"],
                    "facility_name": group.facility_name,
                    "active_courses": group.active_courses,
                    "reason": reason,
                    "venue_names": " | ".join(group.venue_names),
                    "provider_course_id": "",
                    "title": "",
                }
            )
        for row in rejected:
            parent = row["parent"]
            writer.writerow(
                {
                    "provider": parent["provider"],
                    "parent_name": parent["name"],
                    "facility_name": "",
                    "active_courses": 1,
                    "reason": row["reason"],
                    "venue_names": clean_text(row.get("venue_name")),
                    "provider_course_id": row["provider_course_id"],
                    "title": row["title"],
                }
            )
    return resolved_path, unresolved_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Split missing aggregate branches by physical venue and copy only "
            "trusted or verified addresses."
        )
    )
    parser.add_argument("--provider")
    parser.add_argument("--naver", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--apply-report",
        type=Path,
        help=(
            "Revalidate and apply a prior resolved CSV without repeating "
            "external requests. Requires --apply."
        ),
    )
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--delay", type=float, default=0.05)
    parser.add_argument("--min-score", type=int, default=82)
    parser.add_argument("--max-naver-requests", type=int, default=3000)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "logs" / "branch_address_backfill",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    groups, rejected, rejected_reasons = load_groups(args.provider)
    provider_localities = load_provider_localities()
    if args.apply_report:
        resolutions, errors = load_audited_report_resolutions(
            args.apply_report,
            groups,
            provider_localities,
            args.min_score,
        )
        print(
            f"apply_report={args.apply_report} "
            f"report_resolutions={len(resolutions)} "
            f"audit_errors={len(errors)} apply={args.apply}"
        )
        for error in errors[:50]:
            print(f"audit_error={error}")
        if errors:
            return 1
        if not args.apply:
            print("report was audited only; pass --apply to persist it")
            return 0
        branches, courses, deleted = apply_resolutions(resolutions)
        print(
            f"target_branches={branches} updated_courses={courses} "
            f"deleted_empty_parents={deleted}"
        )
        return 0

    sources = fetch_address_sources()
    by_name, by_stem, _ = build_source_indexes(sources)
    naver = None
    if args.naver:
        client_id, client_secret = load_naver_api_credentials()
        naver = NaverResolver(
            client_id,
            client_secret,
            timeout=args.timeout,
            delay=args.delay,
            min_score=args.min_score,
            max_requests=args.max_naver_requests,
        )

    resolutions: list[VenueResolution] = []
    unresolved: list[tuple[VenueGroup, str]] = []
    for group in groups:
        locality = branch_locality(group.parent, provider_localities)
        resolution, reason = resolve_group(
            group,
            locality,
            by_name,
            by_stem,
            naver,
        )
        if resolution:
            if (
                not address_matches_locality(
                    resolution.candidate.address,
                    locality,
                )
                and clean_text(locality)
            ):
                unresolved.append((group, "resolved_address_locality_mismatch"))
                continue
            resolutions.append(resolution)
        else:
            unresolved.append((group, reason))

    resolved_path, unresolved_path = write_reports(
        args.output_dir,
        resolutions,
        unresolved,
        rejected,
    )
    methods = Counter(item.method for item in resolutions)
    reasons = Counter(reason for _, reason in unresolved)
    reasons.update(rejected_reasons)
    print(
        f"groups={len(groups)} resolved_groups={len(resolutions)} "
        f"unresolved_groups={len(unresolved)} "
        f"rejected_active_courses={len(rejected)} apply={args.apply}"
    )
    print(
        f"resolved_active_courses="
        f"{sum(item.group.active_courses for item in resolutions)} "
        f"resolved_total_courses="
        f"{sum(len(item.group.courses) for item in resolutions)} "
        f"naver_requests={naver.requests if naver else 0}"
    )
    print(f"methods={dict(methods)}")
    print(f"reasons={dict(reasons)}")
    print(f"resolved_report={resolved_path}")
    print(f"unresolved_report={unresolved_path}")
    if args.apply and resolutions:
        branches, courses, deleted = apply_resolutions(resolutions)
        print(
            f"target_branches={branches} updated_courses={courses} "
            f"deleted_empty_parents={deleted}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
