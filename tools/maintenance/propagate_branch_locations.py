from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from DB.db_utils import get_db_cursor
from tools.maintenance.kakao_geocode_branches import (
    address_matches_region,
    addresses_refer_to_same_location,
    configured_locality_parts,
    normalize_text,
)


MIN_VERIFIED_SOURCE_CONFIDENCE = 75
SOUTH_KOREA_LAT_RANGE = (32.5, 39.5)
SOUTH_KOREA_LON_RANGE = (124.0, 132.5)


@dataclass(frozen=True)
class LocationMatch:
    target: dict[str, Any]
    source: dict[str, Any]


def normalize_branch_name(value: str | None) -> str:
    return normalize_text(value)


def location_match_key(row: dict[str, Any]) -> tuple[str, str]:
    return (
        str(row.get("provider") or "").strip().upper(),
        normalize_branch_name(row.get("name")),
    )


def coordinate_key(row: dict[str, Any]) -> tuple[float, float]:
    return round(float(row["lat"]), 6), round(float(row["lon"]), 6)


def is_eligible_verified_source(row: dict[str, Any]) -> bool:
    try:
        lat = float(row["lat"])
        lon = float(row["lon"])
        confidence = int(row.get("location_confidence") or 0)
    except (KeyError, TypeError, ValueError):
        return False
    provider, name = location_match_key(row)
    coordinate_source = str(row.get("coordinate_source") or "").strip().upper()
    return bool(
        row.get("location_verified")
        and provider
        and name
        # Do not launder legacy Google coordinates into a provenance value
        # that would escape the Kakao re-verification pass.  Automatic copies
        # are derived only from an already verified Kakao coordinate chain.
        and coordinate_source.startswith("KAKAO_")
        and confidence >= MIN_VERIFIED_SOURCE_CONFIDENCE
        and SOUTH_KOREA_LAT_RANGE[0] <= lat <= SOUTH_KOREA_LAT_RANGE[1]
        and SOUTH_KOREA_LON_RANGE[0] <= lon <= SOUTH_KOREA_LON_RANGE[1]
    )


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _regions_compatible(left: dict[str, Any], right: dict[str, Any]) -> bool:
    for field in ("region_sido", "region_sigungu"):
        left_value = normalize_text(_text(left.get(field)))
        right_value = normalize_text(_text(right.get(field)))
        if left_value and right_value and left_value != right_value:
            return False
    return True


def location_evidence_compatible(target: dict[str, Any], source: dict[str, Any]) -> bool:
    target_address = _text(target.get("address"))
    source_address = _text(source.get("address"))
    if (
        target_address
        and source_address
        and not addresses_refer_to_same_location(target_address, source_address)
    ):
        return False
    if not _regions_compatible(target, source):
        return False

    target_locality = " ".join(
        part
        for part in (
            _text(target.get("region_sido")),
            _text(target.get("region_sigungu")),
        )
        if part
    )
    target_parts = configured_locality_parts(target_locality)
    if target_parts and source_address and not address_matches_region(
        source_address,
        target_parts[0],
        target_parts[1],
    ):
        return False

    source_locality = " ".join(
        part
        for part in (
            _text(source.get("region_sido")),
            _text(source.get("region_sigungu")),
        )
        if part
    )
    source_parts = configured_locality_parts(source_locality)
    if source_parts and target_address and not address_matches_region(
        target_address,
        source_parts[0],
        source_parts[1],
    ):
        return False
    return True


def index_verified_sources(
    sources: list[dict[str, Any]],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    indexed: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for source in sources:
        if is_eligible_verified_source(source):
            indexed.setdefault(location_match_key(source), []).append(source)
    return indexed


def choose_unique_verified_source(
    target: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str]:
    """Choose one source only when every same-name signal is fail-closed."""

    if target.get("lat") is not None or target.get("lon") is not None:
        return None, "partial_or_existing_target_coordinates"
    provider, name = location_match_key(target)
    if not provider or not name:
        return None, "missing_target_identity"

    eligible = [
        source
        for source in candidates
        if is_eligible_verified_source(source)
        and location_match_key(source) == (provider, name)
    ]
    if not eligible:
        return None, "no_verified_same_name_source"

    coordinate_groups: dict[tuple[float, float], list[dict[str, Any]]] = {}
    for source in eligible:
        coordinate_groups.setdefault(coordinate_key(source), []).append(source)
    if len(coordinate_groups) != 1:
        return None, "ambiguous_verified_same_name_coordinates"

    source_group = next(iter(coordinate_groups.values()))
    for index, source in enumerate(source_group):
        if any(
            not location_evidence_compatible(source, other)
            for other in source_group[index + 1 :]
        ):
            return None, "conflicting_verified_source_evidence"
        if not location_evidence_compatible(target, source):
            return None, "conflicting_target_source_evidence"

    return max(source_group, key=source_priority), "verified_same_name_copy"


def source_priority(row: dict[str, Any]) -> tuple[int, int, int, str]:
    return (
        int(bool(row.get("location_verified"))),
        int(row.get("location_confidence") or 0),
        int(bool((row.get("address") or "").strip())),
        str(row.get("id") or ""),
    )


def build_location_matches(
    targets: list[dict[str, Any]],
    sources: list[dict[str, Any]],
) -> tuple[list[LocationMatch], list[dict[str, Any]], list[dict[str, Any]]]:
    sources_by_name = index_verified_sources(sources)

    matches: list[LocationMatch] = []
    ambiguous: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []

    for target in targets:
        source, reason = choose_unique_verified_source(
            target,
            sources_by_name.get(location_match_key(target), []),
        )
        if source is not None:
            matches.append(LocationMatch(target=target, source=source))
        elif reason in {
            "ambiguous_verified_same_name_coordinates",
            "conflicting_verified_source_evidence",
            "conflicting_target_source_evidence",
            "partial_or_existing_target_coordinates",
        }:
            ambiguous.append(target)
        else:
            unmatched.append(target)

    return matches, ambiguous, unmatched


def fetch_targets(provider: str | None, with_active_courses: bool) -> list[dict[str, Any]]:
    active_course_filter = """
          AND EXISTS (
                SELECT 1
                FROM courses c
                WHERE c.branch_id = b.id
                  AND COALESCE(c.is_active, TRUE) = TRUE
          )
    """ if with_active_courses else ""

    provider_filter = "AND b.provider = %(provider)s" if provider else ""
    with get_db_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT b.id, b.provider, b.branch_code, b.name, b.address,
                   b.lat, b.lon, b.address_source, b.coordinate_source,
                   b.location_confidence, b.location_verified,
                   b.region_sido, b.region_sigungu
            FROM branches b
            WHERE b.lat IS NULL
              AND b.lon IS NULL
              {provider_filter}
              {active_course_filter}
            ORDER BY b.provider, b.name, b.branch_code
            """,
            {"provider": provider},
        )
        return [dict(row) for row in cursor.fetchall()]


def fetch_sources(provider: str | None) -> list[dict[str, Any]]:
    provider_filter = "AND provider = %(provider)s" if provider else ""
    with get_db_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT id, provider, branch_code, name, address,
                   lat, lon, address_source, coordinate_source,
                   location_confidence, location_verified,
                   region_sido, region_sigungu
            FROM branches
            WHERE lat IS NOT NULL
              AND lon IS NOT NULL
              AND location_verified IS TRUE
              AND coordinate_source IS NOT NULL
              AND btrim(coordinate_source) <> ''
              AND location_confidence >= %(minimum_confidence)s
              {provider_filter}
            ORDER BY provider, name, branch_code
            """,
            {
                "provider": provider,
                "minimum_confidence": MIN_VERIFIED_SOURCE_CONFIDENCE,
            },
        )
        return [dict(row) for row in cursor.fetchall()]


def persist_matches(matches: list[LocationMatch]) -> int:
    updated = 0
    with get_db_cursor() as cursor:
        for match in matches:
            cursor.execute(
                """
                SELECT id, provider, branch_code, name, address,
                       lat, lon, address_source, coordinate_source,
                       location_confidence, location_verified,
                       region_sido, region_sigungu
                FROM branches
                WHERE id = %(target_id)s
                FOR UPDATE
                """,
                {"target_id": match.target["id"]},
            )
            target_row = cursor.fetchone()
            if not target_row:
                continue
            target = dict(target_row)
            provider, normalized_name = location_match_key(target)
            cursor.execute(
                """
                SELECT id, provider, branch_code, name, address,
                       lat, lon, address_source, coordinate_source,
                       location_confidence, location_verified,
                       region_sido, region_sigungu
                FROM branches
                WHERE provider = %(provider)s
                  AND lat IS NOT NULL
                  AND lon IS NOT NULL
                  AND location_verified IS TRUE
                  AND regexp_replace(
                        lower(btrim(name)),
                        '[^0-9A-Za-z가-힣]+',
                        '',
                        'g'
                  ) = %(normalized_name)s
                FOR SHARE
                """,
                {
                    "provider": provider,
                    "normalized_name": normalized_name,
                },
            )
            fresh_sources = [dict(row) for row in cursor.fetchall()]
            source, _reason = choose_unique_verified_source(target, fresh_sources)
            if source is None:
                continue
            cursor.execute(
                """
                UPDATE branches
                SET address = CASE
                        WHEN address IS NULL OR btrim(address) = '' THEN %(source_address)s
                        ELSE address
                    END,
                    address_source = CASE
                        WHEN (address IS NULL OR btrim(address) = '')
                             AND %(source_address)s IS NOT NULL
                             AND btrim(%(source_address)s) <> ''
                            THEN %(copy_source)s
                        ELSE address_source
                    END,
                    lat = %(lat)s,
                    lon = %(lon)s,
                    coordinate_source = %(copy_source)s,
                    location_confidence = %(location_confidence)s,
                    location_verified = TRUE,
                    location_checked_at = now(),
                    location_query = %(location_query)s,
                    geocode_status = 'resolved',
                    geocode_reason_code = 'verified_kakao_same_name_copy',
                    geocode_next_retry_at = NULL,
                    geocode_last_error = NULL,
                    geocode_last_attempt_at = now()
                WHERE id = %(target_id)s
                  AND provider = %(target_provider)s
                  AND lat IS NULL
                  AND lon IS NULL
                  AND EXISTS (
                        SELECT 1
                        FROM branches source
                        WHERE source.id = %(source_id)s
                          AND source.provider = %(target_provider)s
                          AND source.location_verified IS TRUE
                          AND source.lat = %(lat)s
                          AND source.lon = %(lon)s
                          AND source.coordinate_source IS NOT NULL
                          AND btrim(source.coordinate_source) <> ''
                          AND upper(left(btrim(source.coordinate_source), 6)) = 'KAKAO_'
                          AND source.location_confidence >= %(minimum_confidence)s
                  )
                """,
                {
                    "target_id": target["id"],
                    "target_provider": target["provider"],
                    "source_id": source["id"],
                    "source_address": source.get("address"),
                    "lat": source["lat"],
                    "lon": source["lon"],
                    "copy_source": (
                        "KAKAO_VERIFIED_SAME_NAME_COPY:"
                        f"{source.get('branch_code') or source['id']}"
                    ),
                    "location_confidence": int(source.get("location_confidence") or 0),
                    "minimum_confidence": MIN_VERIFIED_SOURCE_CONFIDENCE,
                    "location_query": (
                        f"same provider/name: {source.get('provider')} "
                        f"{source.get('name')}"
                    ),
                },
            )
            updated += cursor.rowcount
    return updated


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Copy verified coordinates between same-provider branches with the same "
            "normalized name."
        )
    )
    parser.add_argument(
        "--provider",
        default=None,
        help="Provider code to process. Omit to process every provider safely.",
    )
    parser.add_argument(
        "--with-active-courses",
        action="store_true",
        help="Target only branches that own at least one active course.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Maximum verified matches to update. Default 0 means no limit.",
    )
    parser.add_argument(
        "--details",
        action="store_true",
        help="Print individual matches and rejected targets after the summary.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.limit < 0:
        raise SystemExit("--limit must be zero or greater")
    provider = args.provider.strip().upper() if args.provider else None
    targets = fetch_targets(provider, args.with_active_courses)
    sources = fetch_sources(provider)
    matches, ambiguous, unmatched = build_location_matches(targets, sources)
    if args.limit:
        matches = matches[: args.limit]

    print(
        f"provider={provider or 'ALL'} targets={len(targets)} matches={len(matches)} "
        f"ambiguous={len(ambiguous)} unmatched={len(unmatched)} "
        f"dry_run={args.dry_run}"
    )
    if args.details:
        for match in matches:
            target = match.target
            source = match.source
            print(
                f"  match {target['name']} target={target['branch_code']} "
                f"source={source['branch_code']} lat={source['lat']} lon={source['lon']}"
            )
        for target in ambiguous:
            print(f"  ambiguous {target['name']} ({target['branch_code']})")
        for target in unmatched:
            print(f"  unmatched {target['name']} ({target['branch_code']})")

    if args.dry_run:
        return 0

    updated = persist_matches(matches)
    print(f"updated={updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
