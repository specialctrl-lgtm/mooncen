from __future__ import annotations

import argparse
import math
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg2
import requests
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from DB.connection_settings import database_connect_options
from tools.maintenance.kakao_geocode_branches import (
    RequestBudget,
    RequestBudgetExceeded,
)

KAKAO_KEYWORD_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
KAKAO_ADDRESS_URL = "https://dapi.kakao.com/v2/local/search/address.json"


@dataclass
class Candidate:
    source: str
    query: str
    name: str
    address: str
    lat: float
    lon: float
    score: int


def load_api_key() -> str:
    load_dotenv(ROOT / ".env")
    key = (
        os.getenv("KAKAO_MAPS_REST_API_KEY")
        or os.getenv("MoonCenKakaoMapsRestApiKey")
    )
    if not key:
        raise RuntimeError("Kakao Maps REST API key is missing.")
    return key


def db_connect():
    load_dotenv(ROOT / ".env")
    host = os.getenv("DB_HOST", "localhost")
    return psycopg2.connect(
        host=host,
        port=int(os.getenv("DB_PORT", "5432")),
        dbname=os.getenv("DB_NAME", "mooncen"),
        user=os.getenv("DB_USER", "mooncen_admin"),
        password=os.getenv("DB_PASSWORD", ""),
        **database_connect_options(host, "mooncen-emart-location-audit"),
    )


def compact(value: str | None) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", value or "").lower()


def clean_branch_name(name: str) -> str:
    value = re.sub(r"\([^)]*\)", "", name or "")
    value = value.replace("수원TR점", "수원 트레이더스점")
    value = value.replace("(TR)", " 트레이더스")
    value = re.sub(r"\s+", " ", value).strip()
    if value and not value.endswith("점"):
        value = f"{value}점"
    return value


def build_queries(name: str) -> list[str]:
    cleaned = clean_branch_name(name)
    base = cleaned.removesuffix("점")
    queries = [
        f"이마트 {cleaned}",
        f"이마트 {base}",
        f"이마트 문화센터 {cleaned}",
        f"emart {cleaned}",
    ]
    if "스타필드시티" in cleaned:
        queries.insert(0, f"이마트 {cleaned.replace('스타필드시티', '스타필드 시티 ')}")
    if "트레이더스" in cleaned:
        queries.insert(0, cleaned)
        queries.insert(1, f"트레이더스 홀세일 클럽 {base.replace('트레이더스', '').strip()}")

    result: list[str] = []
    seen = set()
    for query in queries:
        query = re.sub(r"\s+", " ", query).strip()
        if query and query not in seen:
            result.append(query)
            seen.add(query)
    return result


def distance_km(lat1: Any, lon1: Any, lat2: float, lon2: float) -> float | None:
    if lat1 is None or lon1 is None:
        return None
    lat1 = float(lat1)
    lon1 = float(lon1)
    r = 6371.0088
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def score_candidate(branch_name: str, candidate_name: str, address: str, source: str) -> int:
    branch = compact(clean_branch_name(branch_name).replace("점", ""))
    cand = compact(f"{candidate_name} {address}")
    candidate_name_compact = compact(candidate_name)
    if any(
        excluded in candidate_name_compact
        for excluded in ("이마트24", "이마트에브리데이", "노브랜드")
    ):
        return 0
    if "스타필드" in branch and "스타필드" not in cand:
        return 0
    if "트레이더스" in branch and "트레이더스" not in cand:
        return 0

    score = 30
    if source == "places":
        score += 20
    if "이마트" in candidate_name or "이마트" in address or "emart" in candidate_name.lower():
        score += 20
    if branch and branch in cand:
        score += 35
    elif branch and any(part for part in [branch.replace("스타필드시티", ""), branch.replace("트레이더스", "")] if part and part in cand):
        score += 18
    if "대한민국" in address or re.search(r"(서울|경기|인천|부산|대구|광주|대전|울산|세종|강원|충청|전라|경상|제주)", address):
        score += 5
    return min(score, 100)


def _kakao_documents(
    api_key: str,
    url: str,
    params: dict[str, Any],
    timeout: int,
    request_cache: dict[tuple[str, tuple[tuple[str, str], ...]], list[dict[str, Any]]] | None = None,
    request_budget: RequestBudget | None = None,
) -> list[dict[str, Any]]:
    cache_key = (
        url,
        tuple(sorted((str(key), str(value)) for key, value in params.items())),
    )
    if request_cache is not None and cache_key in request_cache:
        return request_cache[cache_key]
    if request_budget is not None:
        request_budget.consume()
    try:
        response = requests.get(
            url,
            headers={"Authorization": f"KakaoAK {api_key}"},
            params=params,
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        status_text = f" status={status_code}" if status_code is not None else ""
        raise RuntimeError(f"Kakao Local request failed type={type(exc).__name__}{status_text}") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("documents"), list):
        raise RuntimeError("Kakao Local response must contain a documents list")
    documents = [item for item in payload["documents"] if isinstance(item, dict)]
    if request_cache is not None:
        request_cache[cache_key] = documents
    return documents


def _coordinates(item: dict[str, Any]) -> tuple[float, float] | None:
    try:
        lat = float(item["y"])
        lon = float(item["x"])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(lat) or not math.isfinite(lon):
        return None
    if not 32.5 <= lat <= 39.5 or not 124.0 <= lon <= 132.5:
        return None
    return lat, lon


def _address(item: dict[str, Any]) -> str:
    road_address = item.get("road_address")
    values = (
        item.get("road_address_name"),
        road_address.get("address_name") if isinstance(road_address, dict) else None,
        item.get("address_name"),
    )
    return next((re.sub(r"\s+", " ", str(value)).strip() for value in values if value), "")


def fetch_candidates(
    api_key: str,
    branch_name: str,
    timeout: int,
    min_score: int,
    *,
    request_cache: dict[tuple[str, tuple[tuple[str, str], ...]], list[dict[str, Any]]] | None = None,
    request_budget: RequestBudget | None = None,
) -> list[Candidate]:
    candidates: list[Candidate] = []
    for query in build_queries(branch_name):
        documents = _kakao_documents(
            api_key,
            KAKAO_KEYWORD_URL,
            {"query": query, "size": 5, "sort": "accuracy"},
            timeout,
            request_cache,
            request_budget,
        )
        for item in documents:
            coordinates = _coordinates(item)
            if coordinates is None:
                continue
            name = str(item.get("place_name") or "")
            address = _address(item)
            candidates.append(
                Candidate(
                    source="KAKAO_LOCAL_KEYWORD",
                    query=query,
                    name=name,
                    address=address,
                    lat=coordinates[0],
                    lon=coordinates[1],
                    score=score_candidate(branch_name, name, address, "places"),
                )
            )
        if candidates and max(item.score for item in candidates) >= min_score:
            break

        documents = _kakao_documents(
            api_key,
            KAKAO_ADDRESS_URL,
            {"query": query, "analyze_type": "similar", "size": 3},
            timeout,
            request_cache,
            request_budget,
        )
        for item in documents:
            coordinates = _coordinates(item)
            if coordinates is None:
                continue
            address = _address(item)
            candidates.append(
                Candidate(
                    source="KAKAO_LOCAL_ADDRESS",
                    query=query,
                    name="",
                    address=address,
                    lat=coordinates[0],
                    lon=coordinates[1],
                    score=score_candidate(branch_name, "", address, "geocode"),
                )
            )
        time.sleep(0.05)
    return sorted(candidates, key=lambda item: item.score, reverse=True)


def fetch_branches(
    limit: int | None = None,
    *,
    missing_only: bool = False,
) -> list[dict[str, Any]]:
    sql = """
        SELECT id, name, address, lat, lon, location_confidence, location_verified, location_query
        FROM branches
        WHERE provider = 'EMART'
    """
    if missing_only:
        sql += " AND (lat IS NULL OR lon IS NULL)"
    sql += " ORDER BY name"
    if limit:
        sql += " LIMIT %s"
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (limit,) if limit else None)
        columns = [desc[0] for desc in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def update_branch(branch_id: str, candidate: Candidate, verified: bool) -> None:
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE branches
            SET address = %s,
                lat = %s,
                lon = %s,
                address_source = %s,
                coordinate_source = %s,
                location_confidence = %s,
                location_verified = %s,
                location_checked_at = now(),
                location_query = %s
            WHERE id = %s
            """,
            (
                candidate.address,
                candidate.lat,
                candidate.lon,
                candidate.source,
                candidate.source,
                candidate.score,
                verified,
                candidate.query,
                branch_id,
            ),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit and refresh EMART branch coordinates with Kakao Local API")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=10)
    parser.add_argument("--min-score", type=int, default=82)
    parser.add_argument("--mismatch-km", type=float, default=1.0)
    parser.add_argument("--max-requests", type=int, default=1000)
    parser.add_argument("--missing-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.max_requests < 1:
        parser.error("--max-requests must be at least 1")

    api_key = load_api_key()
    branches = fetch_branches(args.limit or None, missing_only=args.missing_only)
    print(
        f"targets={len(branches)} missing_only={args.missing_only} "
        f"dry_run={args.dry_run}"
    )

    updated = 0
    unchanged = 0
    review = 0
    failed = 0
    request_budget = RequestBudget(args.max_requests)
    request_cache: dict[
        tuple[str, tuple[tuple[str, str], ...]],
        list[dict[str, Any]],
    ] = {}
    budget_exhausted = False
    for branch in branches:
        try:
            candidates = fetch_candidates(
                api_key,
                branch["name"],
                args.timeout,
                args.min_score,
                request_cache=request_cache,
                request_budget=request_budget,
            )
        except RequestBudgetExceeded:
            budget_exhausted = True
            print(
                "STOPPED\tKakao request budget exhausted\t"
                f"used={request_budget.used}\tlimit={request_budget.limit}"
            )
            break
        best = candidates[0] if candidates else None
        if not best:
            failed += 1
            print(f"FAILED\t{branch['name']}\tno_candidate")
            continue

        diff = distance_km(branch["lat"], branch["lon"], best.lat, best.lon)
        needs_update = diff is None or diff >= args.mismatch_km
        verified = best.score >= args.min_score
        status = "UPDATE" if needs_update and verified else "OK" if verified else "REVIEW"
        if status == "UPDATE":
            updated += 1
            if not args.dry_run:
                update_branch(str(branch["id"]), best, verified)
        elif status == "OK":
            unchanged += 1
        else:
            review += 1

        print(
            f"{status}\t{branch['name']}\tscore={best.score}\tdiff_km={diff if diff is not None else 'NA'}"
            f"\tquery={best.query}\tname={best.name}\taddress={best.address}"
        )

    print(
        f"summary updated={updated} unchanged={unchanged} review={review} failed={failed} "
        f"requests_used={request_budget.used} request_limit={request_budget.limit} "
        f"budget_exhausted={budget_exhausted}"
    )


if __name__ == "__main__":
    main()
