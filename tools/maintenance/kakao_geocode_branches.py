from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from DB.db_utils import get_db_cursor

KAKAO_ADDRESS_SEARCH_URL = "https://dapi.kakao.com/v2/local/search/address.json"
KAKAO_KEYWORD_SEARCH_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
KAKAO_QUERY_DELAY_SECONDS = 0.1
KAKAO_RETRYABLE_STATUS_CODES = frozenset({500, 502, 503, 504})
KAKAO_MAX_REQUEST_ATTEMPTS = 2
KAKAO_MAX_RETRY_DELAY_SECONDS = 5.0
KAKAO_DEFAULT_MAX_REQUESTS_PER_RUN = 1_000
# Kakao Local rejects keyword queries longer than 100 characters with HTTP 400.
# Reject them locally so one malformed aggregate branch name cannot abort the
# coordinate backfill or consume a paid/budgeted request.
KAKAO_MAX_QUERY_CHARACTERS = 100
GEOCODE_OUTCOME_STATUSES = frozenset(
    {
        "pending",
        "resolved",
        "no_result",
        "low_confidence",
        "invalid_address",
        "region_mismatch",
        "quota_exhausted",
        "request_error",
        "manual_review",
    }
)
GEOCODE_MAX_CANDIDATE_EVIDENCE = 5
GEOCODE_MAX_EVIDENCE_TEXT = 500
GEOCODE_MAX_ERROR_TEXT = 2_000
REGION_SIDO_ALIASES = {
    "강원도": ("강원도", "강원"),
    "강원특별자치도": ("강원특별자치도", "강원"),
    "경기도": ("경기도", "경기"),
    "경상남도": ("경상남도", "경남"),
    "경상북도": ("경상북도", "경북"),
    "광주광역시": ("광주광역시", "광주"),
    "대구광역시": ("대구광역시", "대구"),
    "대전광역시": ("대전광역시", "대전"),
    "부산광역시": ("부산광역시", "부산"),
    "서울특별시": ("서울특별시", "서울"),
    "세종특별자치시": ("세종특별자치시", "세종"),
    "울산광역시": ("울산광역시", "울산"),
    "인천광역시": ("인천광역시", "인천"),
    "전라남도": ("전라남도", "전남"),
    "전라북도": ("전라북도", "전북"),
    "전북특별자치도": ("전북특별자치도", "전북"),
    "전남광주통합특별시": (
        "전남광주통합특별시",
        "전라남도",
        "광주광역시",
        "전남",
        "광주",
    ),
    "제주도": ("제주도", "제주"),
    "제주특별자치도": ("제주특별자치도", "제주"),
    "충청남도": ("충청남도", "충남"),
    "충청북도": ("충청북도", "충북"),
}
REGION_SIGUNGU_ALIASES = {
    "검단구": ("검단구", "서구"),
    "서해구": ("서해구", "서구"),
    "영종구": ("영종구", "중구"),
    "제물포구": ("제물포구", "동구", "중구"),
    "동탄구": ("동탄구", "화성시"),
    "만세구": ("만세구", "화성시"),
    "병점구": ("병점구", "화성시"),
    "효행구": ("효행구", "화성시"),
}
KAKAO_LOCATION_SOURCES = frozenset(
    {
        "KAKAO_LOCAL_ADDRESS",
        "KAKAO_LOCAL_KEYWORD",
    }
)

RequestCacheKey = tuple[str, tuple[tuple[str, str], ...]]
RequestCache = dict[RequestCacheKey, tuple[dict[str, Any], ...]]

PROVIDER_KEYWORDS = {
    "EMART": ["이마트", "emart"],
    "HOMEPLUS": ["홈플러스", "homeplus"],
    "LOTTE": ["롯데마트", "롯데", "lotte"],
    "HYUNDAI_DEPT": ["현대백화점", "현대문화센터", "hyundai"],
    "SHINSEGAE_ACADEMY": ["신세계", "신세계아카데미", "shinsegae"],
    "ELAND_RETAIL": ["이랜드", "뉴코아", "NC", "eland"],
    "AK_PLAZA": ["AK플라자", "AK PLAZA", "akplaza"],
    "GALLERIA": ["갤러리아", "galleria"],
    "LOTTE_MART": ["롯데마트", "롯데", "lottemart"],
    "CULTURE_FACILITY": ["도서관", "박물관", "미술관", "문화원", "문화의집", "문학관", "문예회관"],
    "MUNI_RESERVE_ANSAN_GO_KR_02253999": ["안산", "안산시", "경기도"],
    "MUNI_RESERVE_ANSAN_GO_KR_8236CAF0": ["안산", "안산시", "경기도"],
}

SEJONG_EMD_EDUCATION_PROVIDER = "MUNI_WWW_SEJONG_GO_KR_53F478AF"
_SEJONG_EMD_BRANCH_RE = re.compile(
    r"^(?P<suborg>[0-9A-Za-z가-힣]+(?:읍|면|동))\s+(?P<venue>.+)$"
)
_SEJONG_EMD_ROOM_DETAIL_RE = re.compile(
    r"(?i)(?:(?:지하\s*)?\d+\s*층(?:\s*"
    r"(?:제?\s*\d+\s*호|[0-9A-Za-z가-힣.]+(?:실|룸|방)(?:\s*\d+)?|(?:대|소)?강당))?|"
    r"(?:제?\s*\d+\s*호|[0-9A-Za-z가-힣.]+(?:실|룸|방)(?:\s*\d+)?|(?:대|소)?강당))"
)
_SEJONG_EMD_FACILITY_MARKERS = (
    "복합커뮤니티센터",
    "복컴",
    "행복누림터",
    "행정복지센터",
    "정음관",
    "훈민관",
    "문화복지회관",
    "복지회관",
)
_SEJONG_EMD_MISSING_VENUES = frozenset(
    {
        "주민자치프로그램",
    }
)
_SEJONG_EMD_MULTI_LOCATION_HINTS = (
    "파크골프장/복지회관",
    "문화사랑방1,한솔파크골프장",
    "아름1실/남세종청소년센터",
)
_SEJONG_EMD_HAPPY_CENTER_NAMES = {
    "나성동": "나성동 행복누림터",
    "대평동": "대평동 행복누림터",
    "도담동": "도담동 행복누림터",
    "보람동": "보람동 행복누림터",
    "소담동": "소담동 행복누림터",
    "아름동": "아름동 행복누림터",
    "어진동": "어진동 행복누림터",
    "종촌동": "종촌동 행복누림터",
}
_SEJONG_EMD_REVIEWED_KAKAO_PLACE_NAMES = frozenset(
    {
        *_SEJONG_EMD_HAPPY_CENTER_NAMES.values(),
        "고운동 남측 행복누림터",
        "고운동 북측 행복누림터",
        "연동면 행복누림터",
        "연서면행정복지센터",
        "연서면행정복지센터 봉암출장소",
        "부강면문화복지회관",
        "세종부강신협 본점",
        "장군면복지회관",
        "전동면 복합커뮤니티센터",
        "전의면행복누림터",
        "조치원읍 행복누림터",
        "한솔동 행복누림터 정음관",
        "한솔동 행복누림터 훈민관",
        "세종필드골프클럽 골프연습장",
        "세종시 생활폐기물 종합처리시설",
        "전동면게이트볼장",
        "전의면행정복지센터",
        "전의생활체육공원",
    }
)
_SEJONG_EMD_ROOM_ONLY_RE = re.compile(
    r"(?i)(?:실|룸|홀|강당|사랑방|체육관|탁구장)\d*"
    r"(?:\s*\([^)]*\))?$"
)


@dataclass
class GeocodeCandidate:
    query: str
    formatted_address: str
    lat: float
    lon: float
    place_id: str | None
    location_type: str | None
    partial_match: bool
    confidence: int
    raw_status: str
    source: str = "KAKAO_LOCAL_KEYWORD"
    matched_name: str | None = None


class RequestBudgetExceeded(RuntimeError):
    """Raised before a request that would exceed the configured run budget."""


@dataclass
class RequestBudget:
    limit: int
    used: int = 0
    _lock: threading.Lock = field(
        default_factory=threading.Lock,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if isinstance(self.limit, bool) or self.limit < 1:
            raise ValueError("Kakao request budget must be a positive integer")

    def consume(self) -> None:
        with self._lock:
            if self.used >= self.limit:
                raise RequestBudgetExceeded(f"Kakao request budget exhausted used={self.used} limit={self.limit}")
            self.used += 1


def load_api_key() -> str:
    load_dotenv(PROJECT_ROOT / ".env")

    key = os.getenv("KAKAO_MAPS_REST_API_KEY") or os.getenv("MoonCenKakaoMapsRestApiKey")
    if not key:
        raise RuntimeError("Kakao Maps REST API key is missing. Set KAKAO_MAPS_REST_API_KEY in the server environment.")
    return key


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", value).lower()


def address_matches_region(
    address: str | None,
    region_sido: str,
    region_sigungu: str,
) -> bool:
    normalized_address = normalize_text(address)
    if not normalized_address:
        return False
    sido = re.sub(r"\s+", " ", region_sido or "").strip()
    sigungu_tokens = [
        token.strip("(),")
        for token in re.sub(r"\s+", " ", region_sigungu or "").split()
        if token.strip("(),").endswith(("시", "군", "구"))
    ]
    if not sido or not sigungu_tokens:
        return False
    sido_aliases = REGION_SIDO_ALIASES.get(sido, (sido,))
    if not any(normalize_text(alias) in normalized_address for alias in sido_aliases):
        return False
    return all(
        any(normalize_text(alias) in normalized_address for alias in REGION_SIGUNGU_ALIASES.get(token, (token,)))
        for token in sigungu_tokens
    )


def configured_locality_parts(value: str | None) -> tuple[str, str] | None:
    """Return one fail-closed province/city pair from reviewed target metadata.

    Provider-wide target metadata is useful only when it resolves to one unique
    locality.  A province-only value is deliberately rejected: sending a bare
    facility name across an entire province can select a plausible but wrong
    Kakao place.
    """

    tokens = re.sub(r"\s+", " ", value or "").strip().split()
    if len(tokens) < 2:
        return None
    first = normalize_text(tokens[0])
    if not any(
        first in {normalize_text(canonical), *(normalize_text(alias) for alias in aliases)}
        for canonical, aliases in REGION_SIDO_ALIASES.items()
    ):
        return None
    sigungu = " ".join(tokens[1:]).strip()
    if not any(token.strip("(),").endswith(("시", "군", "구")) for token in tokens[1:]):
        return None
    return tokens[0], sigungu


def load_configured_provider_localities() -> dict[str, str]:
    """Load only unique provider localities from the reviewed crawl registry."""

    # Keep the relatively expensive YAML/reference scan lazy.  Normal address
    # and stored-region runs do not need it.
    from tools.maintenance.backfill_missing_branch_addresses import (
        load_provider_localities,
    )

    return {
        str(provider).strip().upper(): locality
        for provider, locality in load_provider_localities().items()
        if configured_locality_parts(locality) is not None
    }


def is_usable_address(value: str | None) -> bool:
    text = re.sub(r"\s+", " ", value or "").strip()
    if not text or text in {"대한민국", "South Korea", "Korea"}:
        return False
    if len(text) < 8:
        return False
    return bool(re.search(r"(특별시|광역시|특별자치시|특별자치도|도|시|군|구|읍|면|동|로|길|대로)", text))


_ROAD_ADDRESS_IDENTITY_RE = re.compile(
    r"(?P<route>[0-9A-Za-z가-힣·.-]+(?:대로|로|길))\s*"
    r"(?P<number>\d+(?:-\d+)?)"
)


def _canonical_address_text(value: str) -> str:
    normalized = normalize_text(value)
    for canonical, aliases in REGION_SIDO_ALIASES.items():
        canonical_normalized = normalize_text(canonical)
        for alias in sorted({canonical, *aliases}, key=len, reverse=True):
            alias_normalized = normalize_text(alias)
            if normalized.startswith(alias_normalized):
                return canonical_normalized + normalized[len(alias_normalized) :]
    return normalized


def _address_sido(value: str) -> str:
    normalized = normalize_text(value)
    for canonical, aliases in REGION_SIDO_ALIASES.items():
        for alias in sorted({canonical, *aliases}, key=len, reverse=True):
            if normalized.startswith(normalize_text(alias)):
                return canonical
    return ""


def _road_address_identity(value: str) -> tuple[str, str] | None:
    matches = list(_ROAD_ADDRESS_IDENTITY_RE.finditer(value))
    if not matches:
        return None
    match = matches[-1]
    return normalize_text(match.group("route")), match.group("number")


def addresses_refer_to_same_location(requested: str, returned: str) -> bool:
    """Fail closed unless a Kakao similar-address result identifies the request."""

    if _canonical_address_text(requested) == _canonical_address_text(returned):
        return True
    requested_identity = _road_address_identity(requested)
    returned_identity = _road_address_identity(returned)
    if requested_identity is None or requested_identity != returned_identity:
        return False
    requested_sido = _address_sido(requested)
    returned_sido = _address_sido(returned)
    return bool(requested_sido and requested_sido == returned_sido)


def clean_branch_name(provider: str, name: str) -> str:
    value = re.sub(r"\s+", " ", name or "").strip()
    replacements = [
        "문화센터",
        "문센",
        "롯데문화센터",
        "롯데마트",
        "롯데",
        "홈플러스",
        "이마트",
        "현대백화점",
        "신세계아카데미",
        "신세계",
        "이랜드리테일",
        "AK플라자",
        "갤러리아",
    ]
    for token in replacements:
        value = value.replace(token, "").strip()
    value = re.sub(r"\([^)]*\)", "", value).strip()
    if provider in {"EMART", "HOMEPLUS"} and value and not value.endswith("점"):
        value = f"{value}점"
    return value


def _strip_sejong_emd_room_detail(value: str) -> str:
    trimmed = re.sub(r"\s*\([^)]*\)\s*$", "", value).strip()
    match = _SEJONG_EMD_ROOM_DETAIL_RE.search(trimmed)
    if match and not trimmed[match.end() :].strip() and not trimmed[: match.start()].rstrip().endswith("골프연습장"):
        trimmed = trimmed[: match.start()].rstrip(" ,-/")
    return re.sub(r"\s+", " ", trimmed).strip()


def _sejong_emd_known_facility(suborg: str, venue: str) -> str | None:
    """Map room-level Sejong EMD venue text to Kakao's exact place name."""

    normalized_venue = normalize_text(venue)
    if suborg == "고운동":
        if "남측" in venue:
            return "고운동 남측 행복누림터"
        if "북측" in venue:
            return "고운동 북측 행복누림터"
    if suborg == "연서면":
        if "봉암출장소" in venue:
            return "연서면행정복지센터 봉암출장소"
        if "연서면사무소" in venue:
            return "연서면행정복지센터"
    if suborg == "부강면":
        if "복지회관" in venue:
            return "부강면문화복지회관"
        if "신협" in venue:
            return "세종부강신협 본점"
    if suborg == "장군면" and "복지회관" in venue:
        return "장군면복지회관"
    if suborg == "연동면" and "행복누림터" in venue:
        return "연동면 행복누림터"
    if suborg == "연기면" and "세종필드 골프연습장" in venue:
        return "세종필드골프클럽 골프연습장"
    if suborg == "전동면" and "세종생활폐기물종합처리시설" in venue:
        return "세종시 생활폐기물 종합처리시설"
    if suborg == "전동면" and normalize_text(venue) == "전동게이트볼장":
        return "전동면게이트볼장"
    if suborg == "전의면" and ("복컴" in venue or "복합커뮤니티센터" in venue):
        return "전의면행복누림터"
    if suborg == "전의면" and venue.startswith("면사무소"):
        return "전의면행정복지센터"
    if suborg == "전의면" and normalize_text(venue) == "전의체육공원":
        return "전의생활체육공원"
    if suborg == "전동면" and ("복컴" in venue or "복합커뮤니티센터" in venue):
        return "전동면 복합커뮤니티센터"
    if suborg == "조치원읍" and ("복컴" in venue or "복합커뮤니티센터" in venue):
        return "조치원읍 행복누림터"
    if suborg == "한솔동":
        if "정음관" in venue:
            return "한솔동 행복누림터 정음관"
        if "훈민관" in venue:
            return "한솔동 행복누림터 훈민관"

    happy_center = _SEJONG_EMD_HAPPY_CENTER_NAMES.get(suborg)
    if happy_center and (
        any(marker in venue for marker in ("복컴", "복합커뮤니티센터", "행복누림터"))
        or _SEJONG_EMD_ROOM_ONLY_RE.search(venue)
    ):
        return happy_center

    # Avoid treating a generic room word as an external Kakao place.  A bare
    # room outside the reviewed mappings remains unresolved for manual review.
    if (
        normalized_venue in {"다목적", "다목적홀", "다목적강당"}
        or (
            _SEJONG_EMD_ROOM_ONLY_RE.search(venue)
            and not any(marker in venue for marker in _SEJONG_EMD_FACILITY_MARKERS)
        )
    ):
        return ""
    return None


def sejong_emd_facility_name(name: str) -> str:
    """Return one safe Kakao place name for a Sejong EMD course branch.

    The crawler's branch identity combines the 읍면동 owner with the detail
    page's room-level venue.  Only this provider may infer a 복합커뮤니티센터
    from a bare 동 room; 읍/면 room-only values remain unresolved.
    """

    value = re.sub(r"\s+", " ", (name or "").replace("_", " ")).strip()
    for canonical in _SEJONG_EMD_REVIEWED_KAKAO_PLACE_NAMES:
        if normalize_text(value) == normalize_text(canonical):
            return canonical
    match = _SEJONG_EMD_BRANCH_RE.fullmatch(value)
    suborg = match.group("suborg") if match else ""
    venue = match.group("venue") if match else value
    venue = re.sub(rf"^{re.escape(suborg)}\s+", "", venue).strip() if suborg else venue
    if normalize_text(venue) in {normalize_text(item) for item in _SEJONG_EMD_MISSING_VENUES}:
        return ""
    if any(hint in venue for hint in _SEJONG_EMD_MULTI_LOCATION_HINTS):
        return ""
    if re.search(r"\s+(?:또는|혹은)\s+", venue):
        return ""

    known_facility = _sejong_emd_known_facility(suborg, venue)
    if known_facility is not None:
        return known_facility

    candidates: list[tuple[int, str]] = []
    for alternative in re.split(r"\s+(?:또는|혹은)\s+", venue):
        alternative = re.sub(r"\s+", " ", alternative).strip(" ,-/")
        if not alternative:
            continue

        markers = [
            marker
            for marker in _SEJONG_EMD_FACILITY_MARKERS
            if marker in alternative
        ]
        facility = _strip_sejong_emd_room_detail(alternative)
        if markers:
            last_marker = max(markers, key=lambda marker: alternative.rfind(marker) + len(marker))
            marker_end = alternative.rfind(last_marker) + len(last_marker)
            # Once a reviewed facility marker is present, everything after the
            # marker is a room/floor label and must not enter the Kakao query.
            facility = alternative[:marker_end].strip()
            facility = re.sub(rf"^{re.escape(suborg)}\s*", "", facility).strip() if suborg else facility
            facility = re.sub(r"복컴", "복합커뮤니티센터", facility)
            facility = re.sub(r"\s+", " ", facility).strip()
            if facility and suborg:
                facility = f"{suborg} {facility}"
            if facility:
                candidates.append((3, facility))
            continue

        if facility:
            # A named school, sports centre, gallery, or other external venue
            # must remain exactly as published.  The owner 읍면동 is already in
            # the region-constrained query and is not part of the place name.
            candidates.append((2, facility))
        elif suborg.endswith("동"):
            candidates.append((1, f"{suborg} 복합커뮤니티센터"))

    if not candidates:
        return ""
    return max(enumerate(candidates), key=lambda item: (item[1][0], item[0]))[1][1]


def region_facility_name(provider: str, name: str) -> str:
    if provider == SEJONG_EMD_EDUCATION_PROVIDER:
        return sejong_emd_facility_name(name)
    value = clean_branch_name(provider, name).replace("_", " ")
    value = re.sub(r"\s+", " ", value).strip()
    resident_center = re.fullmatch(r"주민센터\s+([0-9A-Za-z가-힣]+동)", value)
    if resident_center:
        value = f"{resident_center.group(1)} 주민센터"
    trimmed = re.sub(
        r"\s+(?:(?:지하\s*)?\d+\s*층.*|"
        r"(?:제?\d+\s*)?(?:강의실|회의실|교육실|교실|다목적실|프로그램실|"
        r"강당|체험실|세미나실|실습실|대강당|소강당|공연장))$",
        "",
        value,
    ).strip()
    return trimmed if len(normalize_text(trimmed)) >= 4 else value


def build_queries(provider: str, name: str, address: str | None = None) -> list[str]:
    if provider == SEJONG_EMD_EDUCATION_PROVIDER:
        facility = sejong_emd_facility_name(name)
        return [facility] if facility and len(facility) <= KAKAO_MAX_QUERY_CHARACTERS else []

    clean_name = clean_branch_name(provider, name)
    if not clean_name:
        clean_name = name

    queries: list[str] = []
    if is_usable_address(address):
        clean_address = re.sub(r"\s+", " ", address).strip()
        # The cheaper address endpoint has already tried the bare address before
        # this keyword fallback runs. Do not pay for the same bare-address query
        # again; combine it with the place name so Kakao can resolve the venue.
        queries.extend([f"{clean_address} {name}", f"{clean_address} {clean_name}"])

    if provider == "EMART":
        queries.extend([f"이마트 {clean_name}", f"이마트 {clean_name} 문화센터"])
    elif provider == "HOMEPLUS":
        queries.extend([f"홈플러스 {clean_name}", f"홈플러스 {clean_name} 문화센터"])
    elif provider == "LOTTE":
        queries.extend([f"롯데마트 {clean_name}", f"롯데문화센터 {clean_name}", f"롯데 {clean_name}"])
    elif provider == "HYUNDAI_DEPT":
        queries.extend([f"현대백화점 {clean_name}", f"현대문화센터 {clean_name}"])
    elif provider == "SHINSEGAE_ACADEMY":
        queries.extend([f"신세계백화점 {clean_name}", f"신세계아카데미 {clean_name}"])
    elif provider == "ELAND_RETAIL":
        queries.extend([f"이랜드리테일 {clean_name}", f"뉴코아 {clean_name}", f"NC백화점 {clean_name}"])
    elif provider == "AK_PLAZA":
        queries.extend([f"AK플라자 {clean_name}", f"AK PLAZA {clean_name}"])
    elif provider == "GALLERIA":
        queries.extend([f"갤러리아 {clean_name}", f"갤러리아백화점 {clean_name}"])
    elif provider == "LOTTE_MART":
        queries.extend([f"롯데마트 {clean_name}", f"롯데마트 문화센터 {clean_name}"])
    elif provider == "CULTURE_FACILITY":
        queries.extend([name, clean_name])
    elif provider == "CNALL_LECTURE":
        queries.extend([f"충청남도 {clean_name}", f"충남 {clean_name}", clean_name])
    elif provider in {"MUNI_RESERVE_ANSAN_GO_KR_02253999", "MUNI_RESERVE_ANSAN_GO_KR_8236CAF0"}:
        queries.extend([f"안산시 {clean_name}", f"경기도 안산시 {clean_name}", f"안산 {clean_name}"])
    else:
        queries.extend([name, f"{name} 문화센터"])

    deduped = []
    seen = set()
    for query in queries:
        key = query.strip()
        if key and len(key) <= KAKAO_MAX_QUERY_CHARACTERS and key not in seen:
            deduped.append(key)
            seen.add(key)
    return deduped


def build_region_queries(
    provider: str,
    name: str,
    region_sido: str,
    region_sigungu: str,
) -> list[str]:
    locality_parts = [region_sido]
    if not (
        provider == SEJONG_EMD_EDUCATION_PROVIDER
        and normalize_text(region_sido) == normalize_text(region_sigungu)
    ):
        locality_parts.append(region_sigungu)
    locality = re.sub(r"\s+", " ", " ".join(locality_parts)).strip()
    queries: list[str] = []
    seen: set[str] = set()
    cleaned_name = region_facility_name(provider, name)
    if provider == SEJONG_EMD_EDUCATION_PROVIDER and not cleaned_name:
        return []
    base_queries = [cleaned_name, *build_queries(provider, name)]
    for base_query in base_queries:
        query = f"{locality} {base_query}".strip()
        if query and len(query) <= KAKAO_MAX_QUERY_CHARACTERS and query not in seen:
            queries.append(query)
            seen.add(query)
    return queries


def score_result(provider: str, branch_name: str, address: str | None, query: str, result: dict[str, Any]) -> int:
    formatted = result.get("road_address_name") or result.get("address_name") or ""
    matched_name = result.get("place_name") or ""
    normalized_address = normalize_text(formatted)
    normalized_name = normalize_text(matched_name)
    normalized_branch_name = normalize_text(branch_name)
    normalized_query = normalize_text(query)
    normalized_source_address = normalize_text(address) if is_usable_address(address) else ""
    clean_name = normalize_text(clean_branch_name(provider, branch_name).replace("점", ""))

    score = 30
    if normalized_branch_name and normalized_branch_name == normalized_name:
        score += 45
    elif clean_name and clean_name in normalized_name:
        score += 35
    elif normalized_name and normalized_name in normalized_branch_name:
        score += 25

    if normalized_source_address and (
        normalized_source_address in normalized_address or normalized_address in normalized_source_address
    ):
        score += 30
    elif normalized_source_address and normalized_source_address in normalized_query:
        score += 10

    provider_hits = sum(
        1 for keyword in PROVIDER_KEYWORDS.get(provider, []) if normalize_text(keyword) in normalized_name
    )
    if provider_hits:
        score += 20

    category_group_code = str(result.get("category_group_code") or "")
    if (
        provider
        in {
            "AK_PLAZA",
            "ELAND_RETAIL",
            "EMART",
            "GALLERIA",
            "HOMEPLUS",
            "HYUNDAI_DEPT",
            "LOTTE",
            "LOTTE_MART",
            "SHINSEGAE_ACADEMY",
        }
        and category_group_code == "MT1"
    ):
        score += 10
    elif provider == "CULTURE_FACILITY" and category_group_code == "CT1":
        score += 10

    return max(0, min(score, 100))


def _request_documents(
    api_key: str,
    url: str,
    params: dict[str, Any],
    timeout: int,
    request_cache: RequestCache | None = None,
    request_budget: RequestBudget | None = None,
) -> list[dict[str, Any]]:
    query = params.get("query")
    if isinstance(query, str) and len(query.strip()) > KAKAO_MAX_QUERY_CHARACTERS:
        return []

    cache_key = (
        url,
        tuple(sorted((str(key), str(value)) for key, value in params.items())),
    )
    if request_cache is not None and cache_key in request_cache:
        return [dict(item) for item in request_cache[cache_key]]

    payload: Any = None
    for attempt in range(KAKAO_MAX_REQUEST_ATTEMPTS):
        if request_budget is not None:
            # Count real HTTP attempts, including retries. Cache hits return above
            # and therefore do not consume the user's cost ceiling.
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
            break
        except (requests.RequestException, ValueError) as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            should_retry = (
                isinstance(exc, requests.HTTPError)
                and status_code in KAKAO_RETRYABLE_STATUS_CODES
                and attempt + 1 < KAKAO_MAX_REQUEST_ATTEMPTS
            )
            if should_retry:
                error_response = getattr(exc, "response", None)
                retry_after = getattr(error_response, "headers", {}).get("Retry-After")
                try:
                    retry_delay = float(retry_after)
                except (TypeError, ValueError):
                    retry_delay = float(2**attempt)
                time.sleep(max(0.0, min(retry_delay, KAKAO_MAX_RETRY_DELAY_SECONDS)))
                continue

            status_text = f" status={status_code}" if status_code is not None else ""
            raise RuntimeError(f"Kakao Local request failed type={type(exc).__name__}{status_text}") from None

    if not isinstance(payload, dict) or not isinstance(payload.get("documents"), list):
        raise RuntimeError("Kakao Local response must contain a documents list")
    documents = [item for item in payload["documents"] if isinstance(item, dict)]
    if request_cache is not None:
        # Deliberately omit the API key from the cache key and value. Kakao Local
        # search results are public data; credentials must remain request-only.
        request_cache[cache_key] = tuple(dict(item) for item in documents)
    return documents


def _coordinates(result: dict[str, Any]) -> tuple[float, float] | None:
    try:
        lat = float(result["y"])
        lon = float(result["x"])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(lat) or not math.isfinite(lon):
        return None
    # Kakao Local is a Korea-focused service. Reject malformed or surprising
    # coordinates before they can move a branch marker outside the service area.
    if not 32.5 <= lat <= 39.5 or not 124.0 <= lon <= 132.5:
        return None
    return lat, lon


def _formatted_address(result: dict[str, Any]) -> str:
    road_address = result.get("road_address")
    address = result.get("address")
    values = (
        result.get("road_address_name"),
        road_address.get("address_name") if isinstance(road_address, dict) else None,
        result.get("address_name"),
        address.get("address_name") if isinstance(address, dict) else None,
    )
    return next((re.sub(r"\s+", " ", str(value)).strip() for value in values if value), "")


def _bounded_evidence_text(value: Any, limit: int = GEOCODE_MAX_EVIDENCE_TEXT) -> str | None:
    text_value = re.sub(r"\s+", " ", str(value or "")).strip()
    return text_value[:limit] or None


def _raw_candidate_evidence(
    query: str,
    result: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    coordinates = _coordinates(result)
    return {
        "query": _bounded_evidence_text(query),
        "address": _bounded_evidence_text(_formatted_address(result)),
        "matched_name": _bounded_evidence_text(result.get("place_name")),
        "place_id": _bounded_evidence_text(result.get("id"), 100),
        "lat": coordinates[0] if coordinates else None,
        "lon": coordinates[1] if coordinates else None,
        "rejection_reason": reason,
    }


def _append_rejected_candidate(
    diagnostics: dict[str, Any],
    query: str,
    result: dict[str, Any],
    reason: str,
) -> None:
    evidence = diagnostics.setdefault("rejected_candidates", [])
    if isinstance(evidence, list) and len(evidence) < GEOCODE_MAX_CANDIDATE_EVIDENCE:
        evidence.append(_raw_candidate_evidence(query, result, reason))


def _address_candidate(
    api_key: str,
    address: str,
    timeout: int,
    request_cache: RequestCache | None = None,
    request_budget: RequestBudget | None = None,
) -> GeocodeCandidate | None:
    query = re.sub(r"\s+", " ", address).strip()
    documents = _request_documents(
        api_key,
        KAKAO_ADDRESS_SEARCH_URL,
        {"query": query, "analyze_type": "similar", "size": 5},
        timeout,
        request_cache,
        request_budget,
    )
    normalized_query = normalize_text(query)
    best: GeocodeCandidate | None = None
    for result in documents:
        coordinates = _coordinates(result)
        formatted = _formatted_address(result)
        if (
            coordinates is None
            or not is_usable_address(formatted)
            or not addresses_refer_to_same_location(query, formatted)
        ):
            continue
        normalized_result = normalize_text(formatted)
        confidence = 80
        if normalized_query == normalized_result:
            confidence += 15
        elif normalized_query in normalized_result or normalized_result in normalized_query:
            confidence += 10
        if result.get("address_type") in {"ROAD", "ROAD_ADDR", "REGION_ADDR"}:
            confidence += 5
        candidate = GeocodeCandidate(
            query=query,
            formatted_address=formatted,
            lat=coordinates[0],
            lon=coordinates[1],
            place_id=None,
            location_type="ADDRESS",
            partial_match=False,
            confidence=min(confidence, 100),
            raw_status="OK",
            source="KAKAO_LOCAL_ADDRESS",
        )
        if best is None or candidate.confidence > best.confidence:
            best = candidate
    return best


def geocode_branch(
    api_key: str,
    provider: str,
    branch_name: str,
    address: str | None,
    timeout: int,
    request_cache: RequestCache | None = None,
    request_budget: RequestBudget | None = None,
    address_search_only: bool = False,
    expected_locality: str | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> GeocodeCandidate | None:
    if diagnostics is not None:
        diagnostics.clear()
        diagnostics.update(
            {
                "documents_seen": 0,
                "invalid_candidate_count": 0,
                "place_name_mismatch_count": 0,
                "region_mismatch_count": 0,
                "rejected_candidates": [],
            }
        )
    sejong_expected_place_name = (
        sejong_emd_facility_name(branch_name)
        if provider == SEJONG_EMD_EDUCATION_PROVIDER
        else ""
    )
    if provider == SEJONG_EMD_EDUCATION_PROVIDER and not sejong_expected_place_name:
        if diagnostics is not None:
            diagnostics["blocked_reason"] = "ambiguous_or_missing_venue"
        return None

    if is_usable_address(address):
        candidate = _address_candidate(
            api_key,
            str(address),
            timeout,
            request_cache,
            request_budget,
        )
        if candidate is not None:
            return candidate
        time.sleep(KAKAO_QUERY_DELAY_SECONDS)

    if address_search_only:
        return None

    locality_parts = expected_locality.split(maxsplit=1) if expected_locality else []
    queries = (
        build_region_queries(provider, branch_name, *locality_parts)
        if len(locality_parts) == 2
        else build_queries(provider, branch_name, address)
    )
    if not queries and diagnostics is not None:
        diagnostics["blocked_reason"] = "no_safe_query"
    best: GeocodeCandidate | None = None
    for query in queries:
        documents = _request_documents(
            api_key,
            KAKAO_KEYWORD_SEARCH_URL,
            {"query": query, "size": 5, "sort": "accuracy"},
            timeout,
            request_cache,
            request_budget,
        )
        if diagnostics is not None:
            diagnostics["documents_seen"] += len(documents)
        for result in documents:
            if (
                sejong_expected_place_name
                and normalize_text(result.get("place_name"))
                != normalize_text(sejong_expected_place_name)
            ):
                # Sejong's generic "복컴" searches often rank a different
                # neighbourhood first. Only an exact Kakao place-name match is
                # safe enough to persist across every room in the facility.
                if diagnostics is not None:
                    diagnostics["place_name_mismatch_count"] += 1
                    _append_rejected_candidate(diagnostics, query, result, "place_name_mismatch")
                continue
            coordinates = _coordinates(result)
            formatted = _formatted_address(result)
            if coordinates is None or not is_usable_address(formatted):
                if diagnostics is not None:
                    diagnostics["invalid_candidate_count"] += 1
                    _append_rejected_candidate(diagnostics, query, result, "invalid_candidate")
                continue
            if expected_locality and (
                len(locality_parts) != 2 or not address_matches_region(formatted, *locality_parts)
            ):
                if diagnostics is not None:
                    diagnostics["region_mismatch_count"] += 1
                    _append_rejected_candidate(diagnostics, query, result, "region_mismatch")
                continue
            confidence = score_result(provider, branch_name, address, query, result)
            if expected_locality:
                confidence = min(100, confidence + 10)
                if normalize_text(result.get("place_name")) == normalize_text(
                    sejong_expected_place_name or region_facility_name(provider, branch_name)
                ):
                    confidence = max(confidence, 85)
            candidate = GeocodeCandidate(
                query=query,
                formatted_address=formatted,
                lat=coordinates[0],
                lon=coordinates[1],
                place_id=str(result.get("id") or "") or None,
                location_type="KEYWORD",
                partial_match=False,
                confidence=confidence,
                raw_status="OK",
                source="KAKAO_LOCAL_KEYWORD",
                matched_name=str(result.get("place_name") or "") or None,
            )
            if best is None or candidate.confidence > best.confidence:
                best = candidate

            # No later candidate can beat a perfect score. Stopping here avoids
            # additional paid keyword-search calls for alternate query spellings.
            if candidate.confidence == 100 or (expected_locality and candidate.confidence >= 85):
                return candidate

        time.sleep(KAKAO_QUERY_DELAY_SECONDS)

    return best


def distance_km(lat1: Any, lon1: Any, lat2: float, lon2: float) -> float | None:
    if lat1 is None or lon1 is None:
        return None
    try:
        lat1_value = float(lat1)
        lon1_value = float(lon1)
    except (TypeError, ValueError):
        return None
    radius_km = 6371.0088
    phi1 = math.radians(lat1_value)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1_value)
    d_lambda = math.radians(lon2 - lon1_value)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return radius_km * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def fetch_targets(
    provider: str | None,
    update_all: bool,
    verify_existing: bool,
    limit: int | None,
    *,
    with_active_courses: bool = False,
    address_only: bool = False,
    region_keyword_only: bool = False,
    course_address_only: bool = False,
    configured_locality_only: bool = False,
    retry_after_days: int = 0,
    coordinate_source_prefix: str | None = None,
    configured_provider_localities: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    provider_filter = "AND provider = %(provider)s" if provider else ""
    coordinate_source_filter = (
        "AND coordinate_source ILIKE %(coordinate_source_prefix)s"
        if coordinate_source_prefix
        else ""
    )
    course_filter = (
        """
              AND EXISTS (
                    SELECT 1
                    FROM courses c
                    WHERE c.branch_id = branches.id
                      AND COALESCE(c.is_active, true) = true
              )
        """
        if with_active_courses
        else ""
    )
    if address_only:
        address_filter = "AND address IS NOT NULL AND btrim(address) <> ''"
    elif course_address_only:
        address_filter = """
              AND (address IS NULL OR btrim(address) = '')
              AND 1 = (
                    SELECT COUNT(*)
                    FROM (
                        SELECT DISTINCT btrim(ca.venue_address) AS address
                        FROM courses ca
                        WHERE ca.branch_id = branches.id
                          AND COALESCE(ca.is_active, true) = true
                          AND ca.venue_address IS NOT NULL
                          AND btrim(ca.venue_address) <> ''
                    ) AS unique_course_addresses
              )
        """
    elif region_keyword_only:
        address_filter = """
              AND (address IS NULL OR btrim(address) = '')
              AND region_sido IS NOT NULL
              AND btrim(region_sido) <> ''
              AND region_sigungu IS NOT NULL
              AND btrim(region_sigungu) <> ''
        """
    elif configured_locality_only:
        address_filter = """
              AND (address IS NULL OR btrim(address) = '')
              AND (
                    region_sido IS NULL
                 OR btrim(region_sido) = ''
                 OR region_sigungu IS NULL
                 OR btrim(region_sigungu) = ''
              )
        """
    else:
        address_filter = ""
    checked_filter = (
        """
              AND (
                    location_checked_at IS NULL
                 OR location_checked_at < now() - make_interval(days => %(retry_after_days)s)
              )
        """
        if retry_after_days > 0
        else ""
    )
    retry_queue_filter = (
        ""
        if update_all
        else "AND (geocode_next_retry_at IS NULL OR geocode_next_retry_at <= now())"
    )
    if verify_existing:
        target_filter = """
              AND address IS NOT NULL
              AND btrim(address) <> ''
              AND lat IS NOT NULL
              AND lon IS NOT NULL
        """
    elif update_all:
        target_filter = ""
    else:
        target_filter = """
              AND (
                    address IS NULL
                 OR btrim(address) = ''
                 OR lat IS NULL
                 OR lon IS NULL
              )
        """
    # Configured-locality eligibility is evaluated against the reviewed YAML
    # registry after the DB read.  Applying SQL LIMIT first could starve valid
    # rows behind ineligible providers, so bound the filtered result instead.
    limit_sql = "LIMIT %(limit)s" if limit and not configured_locality_only else ""
    params = {
        "provider": provider,
        "limit": limit,
        "retry_after_days": retry_after_days,
        "coordinate_source_prefix": (
            f"{coordinate_source_prefix}%" if coordinate_source_prefix else None
        ),
    }

    with get_db_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT id, provider, branch_code, name, address, lat, lon,
                   location_verified, region_sido, region_sigungu,
                   coordinate_source, geocode_status, geocode_reason_code,
                   geocode_attempt_count, geocode_next_retry_at,
                   (
                       SELECT MIN(course_address)
                       FROM (
                           SELECT DISTINCT btrim(ca.venue_address) AS course_address
                           FROM courses ca
                           WHERE ca.branch_id = branches.id
                             AND COALESCE(ca.is_active, true) = true
                             AND ca.venue_address IS NOT NULL
                             AND btrim(ca.venue_address) <> ''
                       ) AS unique_course_addresses
                       HAVING COUNT(*) = 1
                   ) AS course_address
            FROM branches
            WHERE 1 = 1
              {provider_filter}
              {coordinate_source_filter}
              {target_filter}
              {course_filter}
              {address_filter}
              {checked_filter}
              {retry_queue_filter}
            ORDER BY CASE
                         WHEN address IS NOT NULL AND btrim(address) <> '' THEN 0
                         ELSE 1
                     END,
                     provider,
                     name
            {limit_sql}
            """,
            params,
        )
        rows = [dict(row) for row in cursor.fetchall()]

    if course_address_only:
        rows = [row for row in rows if is_usable_address(row.get("course_address"))]

    if configured_locality_only:
        configured = (
            load_configured_provider_localities()
            if configured_provider_localities is None
            else configured_provider_localities
        )
        eligible: list[dict[str, Any]] = []
        for row in rows:
            locality = configured.get(str(row.get("provider") or "").strip().upper())
            parts = configured_locality_parts(locality)
            if parts is None:
                continue
            inferred = dict(row)
            inferred["inferred_region_sido"] = parts[0]
            inferred["inferred_region_sigungu"] = parts[1]
            inferred["location_locality_source"] = "configured_unique_provider_locality"
            eligible.append(inferred)
        rows = eligible[:limit] if limit else eligible

    return rows


def clear_existing_locations(provider: str | None) -> int:
    provider_filter = "WHERE provider = %(provider)s" if provider else ""
    with get_db_cursor() as cursor:
        cursor.execute(
            f"""
            UPDATE branches
            SET address = NULL,
                lat = NULL,
                lon = NULL,
                location = NULL,
                address_source = NULL,
                coordinate_source = NULL,
                location_confidence = 0,
                location_verified = FALSE,
                location_checked_at = now(),
                location_query = NULL,
                geocode_status = 'pending',
                geocode_reason_code = 'cleared_for_reprocessing',
                geocode_attempt_count = 0,
                geocode_candidates = '[]'::jsonb,
                geocode_next_retry_at = NULL,
                geocode_last_error = NULL,
                geocode_last_attempt_at = NULL
            {provider_filter}
            """,
            {"provider": provider},
        )
        return cursor.rowcount


def candidate_evidence(candidate: GeocodeCandidate) -> dict[str, Any]:
    return {
        "query": _bounded_evidence_text(candidate.query),
        "address": _bounded_evidence_text(candidate.formatted_address),
        "matched_name": _bounded_evidence_text(candidate.matched_name),
        "place_id": _bounded_evidence_text(candidate.place_id, 100),
        "lat": candidate.lat,
        "lon": candidate.lon,
        "confidence": candidate.confidence,
        "source": candidate.source,
        "location_type": _bounded_evidence_text(candidate.location_type, 50),
    }


def _bounded_candidates(candidates: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not candidates:
        return []
    bounded: list[dict[str, Any]] = []
    for candidate in candidates[:GEOCODE_MAX_CANDIDATE_EVIDENCE]:
        if isinstance(candidate, dict):
            bounded.append(candidate)
    return bounded


def record_geocode_outcome(
    branch_id: str,
    status: str,
    reason_code: str,
    *,
    candidates: list[dict[str, Any]] | None = None,
    retry_after_days: int | None = None,
    error: str | None = None,
) -> None:
    if status not in GEOCODE_OUTCOME_STATUSES:
        raise ValueError(f"unsupported geocode outcome status: {status}")
    if not re.fullmatch(r"[a-z0-9_]{1,100}", reason_code):
        raise ValueError("geocode reason code must be a bounded machine-readable token")
    if retry_after_days is not None and not 0 <= retry_after_days <= 3650:
        raise ValueError("retry_after_days must be between 0 and 3650")
    candidates_json = json.dumps(
        _bounded_candidates(candidates),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    error_text = _bounded_evidence_text(error, GEOCODE_MAX_ERROR_TEXT)
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            UPDATE branches
            SET location_checked_at = now(),
                geocode_status = %(status)s,
                geocode_reason_code = %(reason_code)s,
                geocode_attempt_count = geocode_attempt_count + 1,
                geocode_candidates = CAST(%(candidates_json)s AS jsonb),
                geocode_next_retry_at = CASE
                    WHEN %(retry_after_days)s IS NULL THEN NULL
                    ELSE now() + make_interval(days => %(retry_after_days)s)
                END,
                geocode_last_error = %(error)s,
                geocode_last_attempt_at = now()
            WHERE id = %(id)s
            """,
            {
                "id": branch_id,
                "status": status,
                "reason_code": reason_code,
                "candidates_json": candidates_json,
                "retry_after_days": retry_after_days,
                "error": error_text,
            },
        )


def update_branch(branch_id: str, candidate: GeocodeCandidate, verified: bool) -> None:
    source = candidate.source if candidate.source in KAKAO_LOCATION_SOURCES else "KAKAO_LOCAL_KEYWORD"
    status = "resolved" if verified else "low_confidence"
    reason_code = "kakao_verified" if verified else "low_confidence_persisted"
    retry_after_days = None if verified else 30
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            UPDATE branches
            SET address = %(address)s,
                lat = %(lat)s,
                lon = %(lon)s,
                address_source = %(source)s,
                coordinate_source = %(source)s,
                location_confidence = %(confidence)s,
                location_verified = %(verified)s,
                location_checked_at = now(),
                location_query = %(query)s,
                geocode_status = %(status)s,
                geocode_reason_code = %(reason_code)s,
                geocode_attempt_count = geocode_attempt_count + 1,
                geocode_candidates = CAST(%(candidates_json)s AS jsonb),
                geocode_next_retry_at = CASE
                    WHEN %(retry_after_days)s IS NULL THEN NULL
                    ELSE now() + make_interval(days => %(retry_after_days)s)
                END,
                geocode_last_error = NULL,
                geocode_last_attempt_at = now()
            WHERE id = %(id)s
            """,
            {
                "id": branch_id,
                "address": candidate.formatted_address,
                "lat": candidate.lat,
                "lon": candidate.lon,
                "confidence": candidate.confidence,
                "verified": verified,
                "query": candidate.query,
                "source": source,
                "status": status,
                "reason_code": reason_code,
                "candidates_json": json.dumps(
                    [candidate_evidence(candidate)],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "retry_after_days": retry_after_days,
            },
        )


def mark_branch_checked(branch_id: str) -> None:
    """Backward-compatible helper for older maintenance callers/tests."""

    record_geocode_outcome(
        branch_id,
        "no_result",
        "legacy_no_result",
        retry_after_days=7,
    )


def print_summary() -> None:
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT provider,
                   COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE address IS NOT NULL AND btrim(address) <> '') AS with_address,
                   COUNT(*) FILTER (WHERE lat IS NOT NULL AND lon IS NOT NULL) AS with_coordinates,
                   COUNT(*) FILTER (WHERE location_verified IS TRUE) AS verified
            FROM branches
            GROUP BY provider
            ORDER BY provider
            """
        )
        rows = cursor.fetchall()

    print("\nBranch location summary:")
    for row in rows:
        print(
            f"  {row['provider']}: address={row['with_address']}/{row['total']} "
            f"coord={row['with_coordinates']}/{row['total']} verified={row['verified']}/{row['total']}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update branch addresses and coordinates with Kakao Local API")
    parser.add_argument("--provider", default=None, help="Provider code to process. Omit to process every provider.")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Maximum branches to process. Default 0 means no limit.",
    )
    parser.add_argument("--timeout", type=int, default=10)
    parser.add_argument("--delay", type=float, default=0.15)
    parser.add_argument(
        "--max-requests",
        type=int,
        default=os.getenv(
            "KAKAO_GEOCODE_MAX_REQUESTS_PER_RUN",
            str(KAKAO_DEFAULT_MAX_REQUESTS_PER_RUN),
        ),
        help=(
            "Maximum Kakao HTTP requests in this process, including retries. "
            "Defaults to KAKAO_GEOCODE_MAX_REQUESTS_PER_RUN or 1000."
        ),
    )
    parser.add_argument("--min-confidence", type=int, default=75)
    parser.add_argument(
        "--retry-after-days",
        type=int,
        default=0,
        help=("Skip unresolved branches checked within this many days. Default 0 retries immediately."),
    )
    parser.add_argument(
        "--allow-low-confidence",
        action="store_true",
        help=(
            "Persist candidates below --min-confidence as unverified. "
            "By default low-confidence candidates are reported but not written."
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--with-active-courses",
        action="store_true",
        help="Process only branches that currently own at least one active course.",
    )
    safe_mode = parser.add_mutually_exclusive_group()
    safe_mode.add_argument(
        "--address-only",
        action="store_true",
        help=(
            "Process only incomplete branches with a stored address and use only "
            "Kakao address search, without a keyword fallback."
        ),
    )
    safe_mode.add_argument(
        "--region-keyword-only",
        action="store_true",
        help=(
            "Process only address-missing branches with a stored city/district, "
            "prefix keyword queries with that locality, and reject results outside it."
        ),
    )
    safe_mode.add_argument(
        "--course-address-only",
        action="store_true",
        help=(
            "Process only address-missing branches whose active courses expose one "
            "unique venue address, using Kakao address search without keyword fallback."
        ),
    )
    safe_mode.add_argument(
        "--configured-locality-only",
        action="store_true",
        help=(
            "Process only address/region-missing branches whose provider has one "
            "reviewed target locality, and reject Kakao results outside it."
        ),
    )
    parser.add_argument(
        "--update-all",
        action="store_true",
        help="Update every branch, including branches that already have address and coordinates.",
    )
    parser.add_argument(
        "--verify-existing",
        action="store_true",
        help=(
            "Re-geocode branches with existing address and coordinates. Matching verified Kakao results are "
            "also persisted so legacy provenance converges."
        ),
    )
    parser.add_argument(
        "--coordinate-source-prefix",
        default=None,
        help=(
            "With --verify-existing, restrict targets to legacy provenance such as GOOGLE. "
            "The prefix is parameterized and never sent to Kakao."
        ),
    )
    parser.add_argument(
        "--mismatch-threshold-km",
        type=float,
        default=1.0,
        help="When --verify-existing is used, update coordinates only if current and geocoded positions differ by this many km.",
    )
    parser.add_argument(
        "--clear-existing",
        action="store_true",
        help="Clear current address/lat/lon/source fields before geocoding targets.",
    )
    args = parser.parse_args()
    if args.max_requests < 1:
        parser.error("--max-requests must be at least 1")
    if not 0 <= args.retry_after_days <= 3650:
        parser.error("--retry-after-days must be between 0 and 3650")
    safe_incomplete_mode = any(
        (
            args.address_only,
            args.region_keyword_only,
            args.course_address_only,
            args.configured_locality_only,
        )
    )
    if safe_incomplete_mode and (
        args.update_all or args.verify_existing or args.clear_existing
    ):
        parser.error("safe incomplete-location modes cannot be combined with destructive or existing-location modes")
    if args.coordinate_source_prefix:
        args.coordinate_source_prefix = args.coordinate_source_prefix.strip()
        if not args.verify_existing:
            parser.error("--coordinate-source-prefix requires --verify-existing")
        if not re.fullmatch(r"[0-9A-Za-z_:-]{1,100}", args.coordinate_source_prefix):
            parser.error("--coordinate-source-prefix must be a safe 1-100 character provenance prefix")
    return args


def main() -> int:
    args = parse_args()
    api_key = load_api_key()

    if args.clear_existing:
        if args.dry_run:
            print("dry-run: existing branch locations would be cleared before geocoding")
        else:
            cleared = clear_existing_locations(args.provider)
            print(f"cleared existing locations: {cleared}")

    targets = fetch_targets(
        args.provider,
        update_all=args.update_all,
        verify_existing=args.verify_existing,
        limit=args.limit,
        with_active_courses=args.with_active_courses,
        address_only=args.address_only,
        region_keyword_only=args.region_keyword_only,
        course_address_only=args.course_address_only,
        configured_locality_only=args.configured_locality_only,
        retry_after_days=args.retry_after_days,
        coordinate_source_prefix=args.coordinate_source_prefix,
    )
    print(
        f"targets: {len(targets)} provider={args.provider or 'ALL'} "
        f"with_active_courses={args.with_active_courses} "
        f"address_only={args.address_only} "
        f"region_keyword_only={args.region_keyword_only} "
        f"course_address_only={args.course_address_only} "
        f"configured_locality_only={args.configured_locality_only} "
        f"coordinate_source_prefix={args.coordinate_source_prefix or 'ALL'} "
        f"retry_after_days={args.retry_after_days} dry_run={args.dry_run}"
    )

    updated = 0
    low_confidence = 0
    failed = 0
    unchanged = 0
    request_cache: RequestCache = {}
    request_budget = RequestBudget(args.max_requests)
    budget_exhausted = False
    request_errors = 0

    def retry_days(default: int) -> int:
        return max(default, args.retry_after_days)

    for row in targets:
        print(f"\n[{row['provider']}] {row['name']} ({row['branch_code']})")
        diagnostics: dict[str, Any] = {}
        geocode_address = (
            row.get("course_address")
            if args.course_address_only
            else row.get("address")
        )
        expected_locality = None
        if args.region_keyword_only:
            expected_locality = (
                f"{row.get('region_sido') or ''} {row.get('region_sigungu') or ''}"
            ).strip()
        elif args.configured_locality_only:
            expected_locality = (
                f"{row.get('inferred_region_sido') or ''} "
                f"{row.get('inferred_region_sigungu') or ''}"
            ).strip()
        try:
            candidate = geocode_branch(
                api_key,
                row["provider"],
                row["name"],
                geocode_address,
                args.timeout,
                request_cache,
                request_budget,
                args.address_only or args.course_address_only,
                expected_locality,
                diagnostics,
            )
        except RequestBudgetExceeded as exc:
            budget_exhausted = True
            print(f"  stopped: Kakao request budget exhausted used={request_budget.used} limit={request_budget.limit}")
            if not args.dry_run:
                record_geocode_outcome(
                    str(row["id"]),
                    "quota_exhausted",
                    "local_request_budget_exhausted",
                    retry_after_days=retry_days(1),
                    error=str(exc),
                )
            break
        except RuntimeError as exc:
            message = str(exc)
            is_http_quota = "status=429" in message
            is_auth_error = "status=401" in message or "status=403" in message
            status = "quota_exhausted" if is_http_quota else "request_error"
            reason = (
                "kakao_http_quota_exhausted"
                if is_http_quota
                else "kakao_authentication_failed"
                if is_auth_error
                else "kakao_request_failed"
            )
            print(f"  request failed: {reason}")
            request_errors += 1
            if not args.dry_run:
                record_geocode_outcome(
                    str(row["id"]),
                    status,
                    reason,
                    retry_after_days=retry_days(1),
                    error=message,
                )
            if is_http_quota:
                budget_exhausted = True
            if is_http_quota or is_auth_error:
                break
            time.sleep(args.delay)
            continue
        if not candidate:
            if (args.address_only or args.course_address_only) and not is_usable_address(
                geocode_address
            ):
                outcome_status = "invalid_address"
                reason_code = (
                    "course_venue_address_not_geocodable"
                    if args.course_address_only
                    else "stored_address_not_geocodable"
                )
                retry_after = retry_days(30)
            elif diagnostics.get("blocked_reason"):
                outcome_status = "manual_review"
                reason_code = str(diagnostics["blocked_reason"])
                retry_after = retry_days(90)
            elif diagnostics.get("region_mismatch_count", 0) > 0:
                outcome_status = "region_mismatch"
                reason_code = "candidate_outside_expected_region"
                retry_after = retry_days(30)
            elif diagnostics.get("invalid_candidate_count", 0) > 0:
                outcome_status = "no_result"
                reason_code = "kakao_candidates_invalid"
                retry_after = retry_days(14)
            elif args.address_only or args.course_address_only:
                outcome_status = "no_result"
                reason_code = (
                    "kakao_course_address_no_result"
                    if args.course_address_only
                    else "kakao_address_no_result"
                )
                retry_after = retry_days(30)
            elif args.region_keyword_only or args.configured_locality_only:
                outcome_status = "no_result"
                reason_code = (
                    "kakao_configured_locality_no_result"
                    if args.configured_locality_only
                    else "kakao_region_keyword_no_result"
                )
                retry_after = retry_days(14)
            else:
                outcome_status = "no_result"
                reason_code = "kakao_no_result"
                retry_after = retry_days(7)
            print(f"  no result: {reason_code}")
            failed += 1
            if not args.dry_run:
                record_geocode_outcome(
                    str(row["id"]),
                    outcome_status,
                    reason_code,
                    candidates=diagnostics.get("rejected_candidates"),
                    retry_after_days=retry_after,
                )
            time.sleep(args.delay)
            continue

        current_distance = distance_km(row.get("lat"), row.get("lon"), candidate.lat, candidate.lon)
        if args.verify_existing and current_distance is not None and current_distance < args.mismatch_threshold_km:
            if candidate.confidence >= args.min_confidence:
                unchanged += 1
                print(
                    f"  verified-existing distance={current_distance:.2f}km "
                    f"confidence={candidate.confidence} query={candidate.query}"
                )
                if not args.dry_run:
                    # Re-persist the matching Kakao candidate so legacy Google
                    # provenance converges instead of remaining permanently
                    # ambiguous after a successful comparison.
                    update_branch(str(row["id"]), candidate, True)
            else:
                low_confidence += 1
                print(
                    f"  review distance={current_distance:.2f}km confidence={candidate.confidence} "
                    f"query={candidate.query}"
                )
                if not args.dry_run:
                    record_geocode_outcome(
                        str(row["id"]),
                        "low_confidence",
                        "existing_coordinate_low_confidence",
                        candidates=[candidate_evidence(candidate)],
                        retry_after_days=retry_days(30),
                    )
            time.sleep(args.delay)
            continue

        verified = candidate.confidence >= args.min_confidence
        print(
            f"  confidence={candidate.confidence} verified={verified} lat={candidate.lat:.7f} lon={candidate.lon:.7f}"
        )
        if current_distance is not None:
            print(f"  distance_from_current: {current_distance:.2f}km")
        print(f"  query: {candidate.query}")
        print(f"  address: {candidate.formatted_address}")

        if verified:
            updated += 1
        else:
            low_confidence += 1
            if not args.allow_low_confidence:
                print("  skipped: confidence is below the persistence threshold")
                if not args.dry_run:
                    record_geocode_outcome(
                        str(row["id"]),
                        "low_confidence",
                        "candidate_below_persistence_threshold",
                        candidates=[candidate_evidence(candidate)],
                        retry_after_days=retry_days(30),
                    )

        if not args.dry_run and (verified or args.allow_low_confidence):
            update_branch(str(row["id"]), candidate, verified)

        time.sleep(args.delay)

    print(
        f"\nupdated_verified={updated} unchanged={unchanged} "
        f"low_confidence={low_confidence} failed={failed} "
        f"request_errors={request_errors} "
        f"requests_used={request_budget.used} request_limit={request_budget.limit} "
        f"budget_exhausted={budget_exhausted}"
    )
    print_summary()
    if budget_exhausted:
        return 3
    if request_errors:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
