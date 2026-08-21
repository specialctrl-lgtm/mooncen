from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path
from typing import Optional

from geopy.exc import GeocoderTimedOut, GeocoderUnavailable
from geopy.geocoders import ArcGIS

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from DB.db_utils import get_db_cursor


PROVIDER_QUERY_PREFIX = {
    "EMART": "이마트",
    "HOMEPLUS": "홈플러스",
    "LOTTE": "롯데마트",
}


def clean_branch_name(provider: str, name: str) -> str:
    value = re.sub(r"\s+", " ", name or "").strip()
    value = value.replace("문화센터", "").strip()

    for prefix in ("이마트", "홈플러스", "롯데마트", "롯데"):
        value = value.replace(prefix, "").strip()

    match = re.search(r"([가-힣A-Za-z0-9]+점)", value)
    if match:
        return match.group(1)

    if provider in {"EMART", "HOMEPLUS"} and value and not value.endswith("점"):
        return f"{value}점"

    return value


def build_queries(provider: str, name: str, address: Optional[str]) -> list[str]:
    queries = []
    if address and address.strip():
        queries.append(address.strip())

    clean_name = clean_branch_name(provider, name)
    prefix = PROVIDER_QUERY_PREFIX.get(provider, "")
    if prefix and clean_name:
        queries.append(f"{prefix} {clean_name}")
        queries.append(f"{prefix} {clean_name} 문화센터")

    if name:
        queries.append(name)

    deduped = []
    seen = set()
    for query in queries:
        key = query.strip()
        if key and key not in seen:
            deduped.append(key)
            seen.add(key)
    return deduped


def geocode(geocoder: ArcGIS, query: str, timeout: int) -> tuple[Optional[float], Optional[float]]:
    try:
        result = geocoder.geocode(query, timeout=timeout)
    except (GeocoderTimedOut, GeocoderUnavailable):
        return None, None
    except Exception as exc:
        print(f"  geocode error: {exc}")
        return None, None

    if not result:
        return None, None
    return float(result.latitude), float(result.longitude)


def fetch_targets(provider: Optional[str], only_with_courses: bool, limit: Optional[int]):
    provider_filter = "AND b.provider = %(provider)s" if provider else ""
    course_join = "JOIN courses c ON c.branch_id = b.id" if only_with_courses else ""
    sql = f"""
        SELECT b.id, b.provider, b.branch_code, b.name, b.address
        FROM branches b
        {course_join}
        WHERE (b.lat IS NULL OR b.lon IS NULL)
          {provider_filter}
        GROUP BY b.id, b.provider, b.branch_code, b.name, b.address
        ORDER BY b.provider, b.name
    """
    params = {"provider": provider}
    if limit:
        sql += " LIMIT %(limit)s"
        params["limit"] = limit

    with get_db_cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall()


def update_branch(branch_id, lat: float, lon: float, query: str) -> None:
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            UPDATE branches
            SET lat = %(lat)s,
                lon = %(lon)s,
                coordinate_source = 'ARCGIS_GEOCODING',
                location_confidence = 60,
                location_verified = FALSE,
                location_checked_at = now(),
                location_query = %(query)s,
                updated_at = now()
            WHERE id = %(id)s
            """,
            {"id": branch_id, "lat": lat, "lon": lon, "query": query},
        )


def print_summary(provider: Optional[str] = None) -> None:
    provider_filter = "WHERE provider = %(provider)s" if provider else ""
    with get_db_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT provider,
                   COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE lat IS NOT NULL AND lon IS NOT NULL) AS with_coordinates
            FROM branches
            {provider_filter}
            GROUP BY provider
            ORDER BY provider;
            """,
            {"provider": provider},
        )
        rows = cursor.fetchall()

    print("\nCoordinate coverage:")
    for row in rows:
        total = int(row["total"])
        filled = int(row["with_coordinates"])
        pct = (filled / total * 100) if total else 0
        print(f"  {row['provider']}: {filled}/{total} ({pct:.1f}%)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill missing branch coordinates")
    parser.add_argument("--provider", default=None, help="Provider code to process. Omit to process every provider.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--timeout", type=int, default=10)
    parser.add_argument("--all-branches", action="store_true", help="Include branches without courses")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    geocoder = ArcGIS(user_agent="mooncen_branch_coordinates")
    targets = fetch_targets(
        provider=args.provider,
        only_with_courses=not args.all_branches,
        limit=args.limit,
    )

    print(f"Branches missing coordinates: {len(targets)}")
    updated = 0
    failed = 0

    for row in targets:
        print(f"\n[{row['provider']}] {row['name']} ({row['branch_code']})")
        for query in build_queries(row["provider"], row["name"], row.get("address")):
            print(f"  query: {query}")
            lat, lon = geocode(geocoder, query, args.timeout)
            if lat is None or lon is None:
                time.sleep(args.delay)
                continue

            print(f"  found: {lat}, {lon}")
            if not args.dry_run:
                update_branch(row["id"], lat, lon, query)
            updated += 1
            break
        else:
            print("  failed")
            failed += 1

        time.sleep(args.delay)

    print(f"\nUpdated: {updated}, failed: {failed}")
    print_summary(args.provider)


if __name__ == "__main__":
    main()
