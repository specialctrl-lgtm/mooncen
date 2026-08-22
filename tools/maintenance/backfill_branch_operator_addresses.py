from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
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
    KakaoResolver,
    address_matches_locality,
    administrative_center_search_name,
    branch_locality,
    clean_text,
    fetch_missing_branches,
    is_usable_address,
    load_kakao_api_key,
    load_provider_localities,
    place_candidate_score,
)


GWANGJU_DISTRICTS = {"광산구", "남구", "동구", "북구", "서구"}
OFFICIAL_OPERATOR_ADDRESS_OVERRIDES: dict[str, tuple[str, str]] = {
    "경기도 여주시": (
        "경기도 여주시 세종로 1",
        "https://contract.yeoju.go.kr/notice/naviMapInfo.do",
    ),
    "경상남도 하동군": (
        "경상남도 하동군 하동읍 군청로 23",
        "https://www.hadong.go.kr/",
    ),
    "경기도 남양주시": (
        "경기도 남양주시 경춘로 1037",
        "https://www.nyj.go.kr/www/index.do",
    ),
    "대구광역시 중구": (
        "대구광역시 중구 국채보상로139길 1",
        "https://www.daegu.go.kr/public/index.do?menu_id=00002005",
    ),
    "서울특별시 광진구": (
        "서울특별시 광진구 아차산로 400",
        "https://www.gwangjin.go.kr/",
    ),
    "광주광역시 동구": (
        "광주광역시 동구 서남로 1",
        "https://www.donggu.kr/menu.es?mid=a10504010000",
    ),
    "인천광역시 검단구": (
        "인천광역시 검단구 독정로125번길 12",
        "https://www.geomdan.go.kr/main/main.jsp",
    ),
    "인천광역시 동구": (
        "인천광역시 동구 금곡로 67",
        "https://www.icdonggu.go.kr/main/",
    ),
    "인천광역시 서해구": (
        "인천광역시 서해구 서곶로 307",
        (
            "https://www.seohae.go.kr/open_content/main/seogu/"
            "office/location.jsp"
        ),
    ),
    "전북특별자치도 완주군": (
        "전북특별자치도 완주군 용진읍 지암로 61",
        "https://www.wanju.go.kr/",
    ),
    "제주특별자치도 제주시": (
        "제주특별자치도 제주시 광양9길 10",
        "https://www.jejusi.go.kr/",
    ),
    "충청북도 청주시": (
        "충청북도 청주시 상당구 상당로69번길 38",
        "https://www.cheongju.go.kr/www/index.do",
    ),
}
PROVIDER_LOCALITY_OVERRIDES = {
    "DAEGU_BUKGU_RESERVATION": "대구광역시 북구",
    "MUNI_WWW_GJCF_OR_KR_F9585EF3": "광주광역시 남구",
    "MUNI_WWW_GWANGJU_GO_KR_82EF77CD": "경기도 광주시",
    "NATIONAL_MUSEUM_OF_MODERN_ART": "서울특별시 종로구",
    "SIMIN_WELFARE_FACILITY_SEED": "광주광역시 북구",
}
PROVIDER_OPERATOR_NAMES = {
    "MUNI_WWW_GJCF_OR_KR_F9585EF3": "광주문화재단",
    "NATIONAL_MUSEUM_OF_MODERN_ART": "국립현대미술관 서울",
    "SIMIN_WELFARE_FACILITY_SEED": "시민종합사회복지관",
}
OFFICIAL_ORGANIZATION_ADDRESSES = {
    "MUNI_WWW_GJCF_OR_KR_F9585EF3": (
        "광주광역시 남구 천변좌로338번길 7",
        "https://www.gjcf.or.kr/cf/intro/directions.do",
    ),
    "NATIONAL_MUSEUM_OF_MODERN_ART": (
        "서울특별시 종로구 삼청로 30",
        "https://www.mmca.go.kr/civil/policy231204.do",
    ),
    "SIMIN_WELFARE_FACILITY_SEED": (
        "광주광역시 북구 양일로 76-1",
        "https://www.si-min.or.kr/",
    ),
}
OFFICIAL_BRANCH_OPERATOR_ADDRESSES: dict[
    tuple[str, str],
    dict[str, str],
] = {
    ("AK_PLAZA", "AK PLAZA 문화아카데미"): {
        "locality": "경기도 평택시",
        "target_name": "AK PLAZA",
        "address": "경기도 평택시 평택로 51",
        "source_url": "https://www.akplaza.com/etc/mobile",
    },
    ("DAEGU_RESERVATION", "대구광역시 통합예약"): {
        "locality": "대구광역시 중구",
        "target_name": "대구광역시청",
        "address": "대구광역시 중구 공평로 88",
        "source_url": "https://www.daegu.go.kr/index.do?menu_id=00000188",
    },
    ("ELAND_RETAIL", "이랜드 리테일 문화센터"): {
        "locality": "서울특별시 서초구",
        "target_name": "이랜드리테일",
        "address": "서울특별시 서초구 잠원로 51",
        "source_url": (
            "https://www.elandretail.com/members/membership_01.do"
        ),
    },
    ("GALLERIA", "갤러리아 문화센터"): {
        "locality": "서울특별시 마포구",
        "target_name": "한화갤러리아",
        "address": "서울특별시 마포구 양화로 81",
        "source_url": "https://www.hanwhagalleria.co.kr/",
    },
    ("GWANGJU_RESERVATION", "관리운영과"): {
        "locality": "광주광역시 서구",
        "target_name": "광주광역시청",
        "address": "광주광역시 서구 내방로 111",
        "source_url": (
            "https://www.gwangju.go.kr/public/contentsView.do"
            "?pageId=public170"
        ),
    },
    ("GWANGJU_RESERVATION", "안전정책관"): {
        "locality": "광주광역시 서구",
        "target_name": "광주광역시청",
        "address": "광주광역시 서구 내방로 111",
        "source_url": (
            "https://www.gwangju.go.kr/public/contentsView.do"
            "?pageId=public170"
        ),
    },
    ("HOMEPLUS", "홈플러스 문화센터"): {
        "locality": "서울특별시 강서구",
        "target_name": "홈플러스 본사",
        "address": "서울특별시 강서구 화곡로 398",
        "source_url": (
            "https://corporate.homeplus.co.kr/ABOUT/Homeplus.aspx"
        ),
    },
    (
        "MUNI_WWW_GWANGSAN_GO_KR_D16CCB12",
        "광주광역시 광산구",
    ): {
        "locality": "광주광역시 광산구",
        "target_name": "광산구청",
        "address": "광주광역시 광산구 광산로29번길 15",
        "source_url": "https://www.gwangsan.go.kr/",
    },
    ("NATIONAL_PARK_RESERVATION", "국립공원공단 예약시스템"): {
        "locality": "강원특별자치도 원주시",
        "target_name": "국립공원공단",
        "address": "강원특별자치도 원주시 혁신로 22",
        "source_url": (
            "https://res.knps.or.kr/contents/H/serviceGuide.do"
            "?parkId=B013"
        ),
    },
    ("SPORTS_VOUCHER", "스포츠강좌이용권 시설/강좌 후보"): {
        "locality": "서울특별시 송파구",
        "target_name": "국민체육진흥공단",
        "address": "서울특별시 송파구 올림픽로 424",
        "source_url": (
            "https://spobiz.kspo.or.kr/front/html/html.do"
            "?sitePage=centerLocation&topMenuSeq=5"
        ),
    },
    (
        "SUNCHEON_SENIOR_WELFARE_NOTICE",
        "순천시 노인복지관 평생교육 사업",
    ): {
        "locality": "전라남도 순천시",
        "target_name": "순천시청",
        "address": "전라남도 순천시 장명로 30",
        "source_url": "https://www.sc.go.kr/kr/info/0001/0004/0001/",
    },
    (
        "ULSAN_EDU_BOOKING",
        "디지털 리터러시 울디릿 Uldilit",
    ): {
        "locality": "울산광역시 중구",
        "target_name": "울산광역시교육청",
        "address": "울산광역시 중구 북부순환도로 375",
        "source_url": "https://use.go.kr/booking/",
    },
    ("ULSAN_EDU_BOOKING", "울산광역시교육청 통합예약"): {
        "locality": "울산광역시 중구",
        "target_name": "울산광역시교육청",
        "address": "울산광역시 중구 북부순환도로 375",
        "source_url": "https://use.go.kr/booking/",
    },
}


@dataclass(frozen=True)
class OperatorQuery:
    locality: str
    target_name: str
    scope: str


@dataclass(frozen=True)
class OperatorResolution:
    branch: dict[str, Any]
    query: OperatorQuery
    candidate: AddressCandidate


def physical_locality(value: Any) -> str:
    locality = clean_text(value)
    if not locality:
        return ""
    locality = locality.replace("강원도 ", "강원특별자치도 ", 1)
    locality = locality.replace("전라북도 ", "전북특별자치도 ", 1)
    if locality == "인천광역시 서구":
        return "인천광역시 서해구"
    tokens = locality.split()
    if tokens[0] != "전남광주통합특별시":
        return locality
    district = tokens[1] if len(tokens) > 1 else ""
    sido = "광주광역시" if district in GWANGJU_DISTRICTS else "전라남도"
    return clean_text(" ".join((sido, *tokens[1:])))


def government_office_name(locality: Any) -> str:
    text = physical_locality(locality)
    tokens = text.split()
    if not tokens:
        return ""
    unit = tokens[-1]
    if unit.endswith(("시", "군", "구")):
        return f"{unit}청"
    if unit.endswith(("특별시", "광역시", "특별자치시", "도", "특별자치도")):
        return f"{unit}청"
    return ""


def inferred_locality(branch: dict[str, Any], locality: Any) -> str:
    supplied = physical_locality(locality)
    if supplied:
        return supplied
    provider = clean_text(branch.get("provider")).upper()
    provider_override = PROVIDER_LOCALITY_OVERRIDES.get(provider)
    if provider_override:
        return provider_override
    name = clean_text(branch.get("name"))
    province_aliases = {
        "전라북도": "전북특별자치도",
        "강원도": "강원특별자치도",
    }
    parts = name.split()
    if (
        len(parts) >= 2
        and parts[0].endswith(
            ("특별시", "광역시", "특별자치시", "도", "특별자치도")
        )
        and parts[1].endswith(("시", "군", "구"))
    ):
        sido = province_aliases.get(parts[0], parts[0])
        return physical_locality(f"{sido} {parts[1]}")
    metropolitan_aliases = {
        "대구": "대구광역시",
        "대전": "대전광역시",
        "광주": "광주광역시",
        "부산": "부산광역시",
        "서울": "서울특별시",
        "울산": "울산광역시",
        "인천": "인천광역시",
    }
    if (
        len(parts) >= 2
        and parts[0] in metropolitan_aliases
        and parts[1].endswith("구")
    ):
        return f"{metropolitan_aliases[parts[0]]} {parts[1]}"
    return ""


def branch_administrative_center_name(value: Any) -> str:
    text = clean_text(value).replace(".", "·")
    exact = text if text.endswith(("읍", "면", "동")) else ""
    if not exact:
        match = re.search(
            r"([가-힣0-9·]+(?:읍|면|동))\s*(?:주민자치센터|자치회관)$",
            text,
        )
        exact = clean_text(match.group(1)) if match else ""
    if exact.endswith(("읍", "면")):
        return f"{exact}사무소"
    if exact.endswith("동"):
        return f"{exact} 행정복지센터"
    return administrative_center_search_name(text)


def branch_operator_override(branch: dict[str, Any]) -> dict[str, str] | None:
    key = (
        clean_text(branch.get("provider")).upper(),
        clean_text(branch.get("name")),
    )
    return OFFICIAL_BRANCH_OPERATOR_ADDRESSES.get(key)


def operator_query(branch: dict[str, Any], locality: Any) -> OperatorQuery | None:
    branch_override = branch_operator_override(branch)
    if branch_override:
        return OperatorQuery(
            locality=branch_override["locality"],
            target_name=branch_override["target_name"],
            scope="organization_office",
        )
    actual_locality = inferred_locality(branch, locality)
    if not actual_locality:
        return None
    provider = clean_text(branch.get("provider")).upper()
    organization_name = PROVIDER_OPERATOR_NAMES.get(provider)
    if organization_name:
        return OperatorQuery(
            locality=actual_locality,
            target_name=organization_name,
            scope="organization_office",
        )
    admin_name = branch_administrative_center_name(branch.get("name"))
    if admin_name:
        return OperatorQuery(
            locality=actual_locality,
            target_name=admin_name,
            scope="administrative_center",
        )
    office_name = government_office_name(actual_locality)
    if not office_name:
        return None
    return OperatorQuery(
        locality=actual_locality,
        target_name=office_name,
        scope="operator_office",
    )


def operator_office_fallback(query: OperatorQuery) -> OperatorQuery | None:
    if query.scope != "administrative_center":
        return None
    office_name = government_office_name(query.locality)
    if not office_name:
        return None
    return OperatorQuery(
        locality=query.locality,
        target_name=office_name,
        scope="operator_office",
    )


def official_address_override(
    query: OperatorQuery,
    provider: Any,
    branch_name: Any = "",
) -> tuple[str, str] | None:
    branch_override = OFFICIAL_BRANCH_OPERATOR_ADDRESSES.get(
        (
            clean_text(provider).upper(),
            clean_text(branch_name),
        )
    )
    if (
        branch_override
        and query.scope == "organization_office"
        and query.locality == branch_override["locality"]
        and query.target_name == branch_override["target_name"]
    ):
        return (
            branch_override["address"],
            branch_override["source_url"],
        )
    if query.scope == "operator_office":
        return OFFICIAL_OPERATOR_ADDRESS_OVERRIDES.get(query.locality)
    if query.scope == "organization_office":
        return OFFICIAL_ORGANIZATION_ADDRESSES.get(
            clean_text(provider).upper()
        )
    return None


def resolve_queries(
    grouped: dict[OperatorQuery, list[dict[str, Any]]],
    *,
    timeout: int,
    delay: float,
    min_score: int,
    max_requests: int,
    workers: int,
) -> tuple[
    list[OperatorResolution],
    list[tuple[dict[str, Any], str]],
    int,
    int | None,
]:
    resolver = KakaoResolver(
        load_kakao_api_key(),
        timeout=timeout,
        delay=delay,
        min_score=min_score,
        max_requests=max_requests,
    )
    resolved: list[OperatorResolution] = []
    unresolved: list[tuple[dict[str, Any], str]] = []

    def resolve_official_address(
        query: OperatorQuery,
        override: tuple[str, str],
    ) -> AddressCandidate:
        address, source_url = override
        # The curated address is authoritative. Geocode that address directly
        # so a cheaper address lookup supplies coordinates for the same place;
        # never combine it with coordinates from an earlier keyword candidate.
        candidate = resolver.geocode_address(address, query.locality)
        if candidate:
            return replace(
                candidate,
                address=address,
                address_source="CURATED_OFFICIAL_OPERATOR_OFFICE",
                confidence=max(95, candidate.confidence),
                verified=True,
                query=source_url,
                matched_name=(candidate.matched_name or query.target_name),
            )
        return AddressCandidate(
            address=address,
            lat=None,
            lon=None,
            address_source="CURATED_OFFICIAL_OPERATOR_OFFICE",
            coordinate_source=None,
            confidence=100,
            verified=True,
            query=source_url,
            matched_name=query.target_name,
        )

    def search(
        query: OperatorQuery,
    ) -> tuple[OperatorQuery, AddressCandidate | None]:
        branch = grouped.get(query, [{}])[0]
        provider = clean_text(branch.get("provider")).upper()
        branch_name = clean_text(branch.get("name"))
        override = official_address_override(query, provider, branch_name)
        if override:
            return query, resolve_official_address(query, override)

        candidate = resolver.place(
            "MUNICIPAL_OPERATOR",
            query.target_name,
            query.locality,
        )
        used_query = query
        fallback = operator_office_fallback(query)
        if not candidate and fallback:
            used_query = fallback
            override = official_address_override(
                used_query,
                provider,
                branch_name,
            )
            if override:
                candidate = resolve_official_address(used_query, override)
            else:
                candidate = resolver.place(
                    "MUNICIPAL_OPERATOR",
                    used_query.target_name,
                    used_query.locality,
                )
        return used_query, candidate

    pool_size = max(1, min(16, int(workers)))
    with ThreadPoolExecutor(max_workers=pool_size) as executor:
        futures = {
            executor.submit(search, query): (query, branches)
            for query, branches in grouped.items()
        }
        for future in as_completed(futures):
            query, branches = futures[future]
            try:
                used_query, candidate = future.result()
            except Exception as exc:
                candidate = None
                reason = f"resolver_error:{type(exc).__name__}"
            else:
                reason = "operator_office_not_found"
            if candidate:
                for branch in branches:
                    resolved.append(
                        OperatorResolution(branch, used_query, candidate)
                    )
            else:
                unresolved.extend((branch, reason) for branch in branches)
    return resolved, unresolved, resolver.requests, resolver.blocked_status


def write_reports(
    output_dir: Path,
    resolutions: list[OperatorResolution],
    unresolved: list[tuple[dict[str, Any], str]],
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    resolved_path = output_dir / f"branch_operator_resolved_{stamp}.csv"
    unresolved_path = output_dir / f"branch_operator_unresolved_{stamp}.csv"
    with resolved_path.open("w", encoding="utf-8-sig", newline="") as handle:
        fields = (
            "branch_id",
            "provider",
            "branch_code",
            "name",
            "active_courses",
            "scope",
            "locality",
            "target_name",
            "matched_name",
            "address",
            "lat",
            "lon",
            "confidence",
            "address_source",
            "coordinate_source",
            "query",
        )
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in sorted(
            resolutions,
            key=lambda value: (
                -int(value.branch.get("active_courses") or 0),
                clean_text(value.branch.get("provider")),
                clean_text(value.branch.get("name")),
            ),
        ):
            writer.writerow(
                {
                    "branch_id": item.branch["id"],
                    "provider": item.branch["provider"],
                    "branch_code": item.branch["branch_code"],
                    "name": item.branch["name"],
                    "active_courses": item.branch.get("active_courses") or 0,
                    "scope": item.query.scope,
                    "locality": item.query.locality,
                    "target_name": item.query.target_name,
                    "matched_name": item.candidate.matched_name,
                    "address": item.candidate.address,
                    "lat": item.candidate.lat,
                    "lon": item.candidate.lon,
                    "confidence": item.candidate.confidence,
                    "address_source": item.candidate.address_source,
                    "coordinate_source": item.candidate.coordinate_source or "",
                    "query": item.candidate.query,
                }
            )
    with unresolved_path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        fields = (
            "branch_id",
            "provider",
            "branch_code",
            "name",
            "active_courses",
            "reason",
        )
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for branch, reason in unresolved:
            writer.writerow(
                {
                    "branch_id": branch["id"],
                    "provider": branch["provider"],
                    "branch_code": branch["branch_code"],
                    "name": branch["name"],
                    "active_courses": branch.get("active_courses") or 0,
                    "reason": reason,
                }
            )
    return resolved_path, unresolved_path


def load_report_resolutions(
    report_path: Path,
    branches: list[dict[str, Any]],
    provider_localities: dict[str, str],
    min_score: int,
) -> tuple[list[OperatorResolution], list[str]]:
    current = {clean_text(branch["id"]): branch for branch in branches}
    resolutions: list[OperatorResolution] = []
    errors: list[str] = []
    seen: set[str] = set()
    with report_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for index, row in enumerate(rows, start=2):
        branch_id = clean_text(row.get("branch_id"))
        branch = current.get(branch_id)
        if not branch:
            errors.append(f"row {index}: current missing branch not found")
            continue
        if branch_id in seen:
            errors.append(f"row {index}: duplicate branch")
            continue
        seen.add(branch_id)
        locality = branch_locality(branch, provider_localities)
        expected = operator_query(branch, locality)
        if not expected:
            errors.append(f"row {index}: operator query is no longer available")
            continue
        reported_query = OperatorQuery(
            scope=clean_text(row.get("scope")),
            locality=clean_text(row.get("locality")),
            target_name=clean_text(row.get("target_name")),
        )
        allowed_queries = [expected]
        fallback = operator_office_fallback(expected)
        if fallback:
            allowed_queries.append(fallback)
        if reported_query not in allowed_queries:
            errors.append(f"row {index}: operator query changed")
            continue
        expected = reported_query
        address = clean_text(row.get("address"))
        if not is_usable_address(address) or not address_matches_locality(
            address,
            expected.locality,
        ):
            errors.append(f"row {index}: invalid address/locality")
            continue
        try:
            confidence = int(row["confidence"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"row {index}: invalid confidence")
            continue
        lat_text = clean_text(row.get("lat"))
        lon_text = clean_text(row.get("lon"))
        if bool(lat_text) != bool(lon_text):
            errors.append(f"row {index}: incomplete coordinates")
            continue
        try:
            lat = float(lat_text) if lat_text else None
            lon = float(lon_text) if lon_text else None
        except ValueError:
            errors.append(f"row {index}: invalid coordinates")
            continue
        if (
            lat is not None
            and lon is not None
            and not (33.0 <= lat <= 39.5 and 124.0 <= lon <= 132.0)
        ):
            errors.append(f"row {index}: coordinates outside South Korea")
            continue
        provider = clean_text(branch.get("provider")).upper()
        override = official_address_override(
            expected,
            provider,
            branch.get("name"),
        )
        curated = bool(
            override
            and address == override[0]
            and clean_text(row.get("query")) == override[1]
        )
        if not curated:
            score = place_candidate_score(
                expected.target_name,
                expected.locality,
                row.get("matched_name"),
                address,
                {"establishment"},
                "MUNICIPAL_OPERATOR",
            )
            if score < min_score:
                errors.append(
                    f"row {index}: operator match score {score} below {min_score}"
                )
                continue
        if confidence < min_score:
            errors.append(
                f"row {index}: confidence {confidence} below {min_score}"
            )
            continue
        expected_address_source = (
            "CURATED_OFFICIAL_OPERATOR_OFFICE"
            if curated
            else "KAKAO_LOCAL_KEYWORD"
        )
        reported_address_source = clean_text(row.get("address_source"))
        allowed_address_sources = {expected_address_source}
        if not curated:
            # Reports created before the Kakao migration remain auditable and
            # applyable without relabeling their historical provenance.
            allowed_address_sources.add("GOOGLE_PLACES_TEXT_SEARCH")
        if reported_address_source and reported_address_source not in allowed_address_sources:
            errors.append(f"row {index}: address source changed")
            continue
        address_source = reported_address_source or expected_address_source
        coordinate_source = clean_text(row.get("coordinate_source")) or None
        if lat is None:
            coordinate_source = None
        elif not coordinate_source:
            if address_source == "GOOGLE_PLACES_TEXT_SEARCH":
                coordinate_source = "GOOGLE_PLACES_TEXT_SEARCH"
            elif curated:
                coordinate_source = "KAKAO_LOCAL_ADDRESS"
            else:
                coordinate_source = "KAKAO_LOCAL_KEYWORD"
        if coordinate_source is not None:
            allowed_coordinate_sources = {
                "KAKAO_LOCAL_KEYWORD": {"KAKAO_LOCAL_KEYWORD"},
                "GOOGLE_PLACES_TEXT_SEARCH": {
                    "GOOGLE_PLACES_TEXT_SEARCH",
                    "GOOGLE_GEOCODING_EMBEDDED_ADDRESS",
                },
                "CURATED_OFFICIAL_OPERATOR_OFFICE": {
                    "KAKAO_LOCAL_ADDRESS",
                    # Keep reports produced before the Kakao migration auditable
                    # without permitting mixed Kakao/Google provenance elsewhere.
                    "GOOGLE_PLACES_TEXT_SEARCH",
                    "GOOGLE_GEOCODING_EMBEDDED_ADDRESS",
                },
            }.get(address_source, set())
            if coordinate_source not in allowed_coordinate_sources:
                errors.append(f"row {index}: coordinate source changed")
                continue
        candidate = AddressCandidate(
            address=address,
            lat=lat,
            lon=lon,
            address_source=address_source,
            coordinate_source=coordinate_source,
            confidence=confidence,
            verified=True,
            query=clean_text(row.get("query")),
            matched_name=clean_text(row.get("matched_name")),
        )
        resolutions.append(OperatorResolution(branch, expected, candidate))
    return resolutions, errors


def apply_resolutions(resolutions: list[OperatorResolution]) -> int:
    updated = 0
    checked_at = datetime.now().astimezone().isoformat()
    with get_db_cursor() as cursor:
        for item in resolutions:
            role = (
                "administrative_center"
                if item.query.scope == "administrative_center"
                else "operating_organization"
            )
            cursor.execute(
                """
                UPDATE branches
                SET address = %(address)s,
                    address_source = %(address_source)s,
                    lat = %(lat)s,
                    lon = %(lon)s,
                    coordinate_source = %(coordinate_source)s,
                    location_confidence = %(confidence)s,
                    location_verified = TRUE,
                    location_checked_at = CURRENT_TIMESTAMP,
                    location_query = %(query)s,
                    basic_info = COALESCE(basic_info, '{}'::jsonb)
                        || %(basic_info)s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %(id)s::uuid
                  AND NULLIF(btrim(address), '') IS NULL
                """,
                {
                    "id": item.branch["id"],
                    "address": item.candidate.address,
                    "address_source": item.candidate.address_source,
                    "lat": item.candidate.lat,
                    "lon": item.candidate.lon,
                    "coordinate_source": item.candidate.coordinate_source,
                    "confidence": item.candidate.confidence,
                    "query": item.candidate.query,
                    "basic_info": Json(
                        {
                            "location_role": role,
                            "operator_address_backfill": {
                                "scope": item.query.scope,
                                "target_name": item.query.target_name,
                                "matched_name": item.candidate.matched_name,
                                "checked_at": checked_at,
                            },
                        }
                    ),
                },
            )
            updated += cursor.rowcount
    return updated


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fill branch-only locations for aggregate, virtual, or otherwise "
            "unresolved branches using their official operating office."
        )
    )
    parser.add_argument("--provider")
    parser.add_argument("--active-only", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--apply-report", type=Path)
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--delay", type=float, default=0.02)
    parser.add_argument("--min-score", type=int, default=82)
    parser.add_argument("--max-requests", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "logs" / "branch_address_backfill",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    provider = clean_text(args.provider).upper() or None
    branches = fetch_missing_branches(
        provider,
        active_only=args.active_only,
        limit=None,
    )
    localities = load_provider_localities()
    if args.apply_report:
        resolutions, errors = load_report_resolutions(
            args.apply_report,
            branches,
            localities,
            args.min_score,
        )
        print(
            f"apply_report={args.apply_report} "
            f"report_resolutions={len(resolutions)} "
            f"audit_errors={len(errors)} apply={args.apply}"
        )
        for error in errors[:100]:
            print(f"audit_error={error}")
        if errors:
            return 1
        if not args.apply:
            print("report was audited only; pass --apply to persist it")
            return 0
        print(f"updated_branches={apply_resolutions(resolutions)}")
        return 0

    grouped: dict[OperatorQuery, list[dict[str, Any]]] = defaultdict(list)
    unresolved: list[tuple[dict[str, Any], str]] = []
    for branch in branches:
        locality = branch_locality(branch, localities)
        query = operator_query(branch, locality)
        if query:
            grouped[query].append(branch)
        else:
            unresolved.append((branch, "operator_locality_unavailable"))
    resolutions, search_unresolved, request_count, blocked_status = resolve_queries(
        grouped,
        timeout=args.timeout,
        delay=args.delay,
        min_score=args.min_score,
        max_requests=max(0, args.max_requests),
        workers=args.workers,
    )
    if blocked_status is not None:
        print(
            "fatal_kakao_request_blocked "
            f"status={blocked_status} apply_aborted={args.apply}"
        )
        return 2
    unresolved.extend(search_unresolved)
    resolved_path, unresolved_path = write_reports(
        args.output_dir,
        resolutions,
        unresolved,
    )
    scopes = Counter(item.query.scope for item in resolutions)
    print(
        f"targets={len(branches)} query_groups={len(grouped)} "
        f"resolved={len(resolutions)} unresolved={len(unresolved)} "
        f"kakao_requests={request_count} apply={args.apply}"
    )
    print(f"scopes={dict(scopes)}")
    print(f"resolved_report={resolved_path}")
    print(f"unresolved_report={unresolved_path}")
    if args.apply:
        print(f"updated_branches={apply_resolutions(resolutions)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
