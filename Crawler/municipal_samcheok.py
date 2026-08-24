"""Fail-closed collectors for Samcheok's public education catalogues.

The audited education boundary consists of four disjoint owners: the
lifelong-learning course ledger, Samcheok Youth Training Center, Dogye Youth
Encouragement Center, and the Wondeok/Geundeok Youth Culture Houses.  The
province-operated Samcheok Education and Culture Center is already collected
by its own GWE library provider.  Municipal library, museum, arts-center and
tourism pages do not expose another structured public education ledger.

Only exact public list/detail pages are reachable.  Application forms,
attachments, login/member routes, applicant data, telephone links and payment
routes are outside the allowlist.  Any access restriction or source-contract
drift invalidates the complete owner snapshot.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
import re
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence
from urllib.parse import parse_qsl, urlencode, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

SAMCHEOK_MUNICIPALITY_CODE = "5123000000"
SAMCHEOK_MUNICIPALITY_NAME = "강원특별자치도 삼척시"

# Retain the incumbent provider while retargeting its partial 2023 notice URL
# to the complete course ledger.
SAMCHEOK_LIFELONG_PROVIDER = "MUNI_WWW_SAMCHEOK_GO_KR_AEA01740"
SAMCHEOK_YOUTH_PROVIDER = "MUNI_YOUTH_SAMCHEOK_GO_KR_96E8E691"
SAMCHEOK_DGYOUTH_PROVIDER = "MUNI_DGYOUTH_SAMCHEOK_GO_KR_C683FA1B"
SAMCHEOK_WDYOUTH_PROVIDER = "MUNI_WDYOUTH_SAMCHEOK_GO_KR_AE04F451"

SAMCHEOK_LIFELONG_URL = "https://www.samcheok.go.kr/specialty/00465/01127.web"
SAMCHEOK_YOUTH_URL = "https://youth.samcheok.go.kr/sub/Program1.php"
SAMCHEOK_DGYOUTH_URL = "https://dgyouth.samcheok.go.kr/sub/Program1.php"
SAMCHEOK_WDYOUTH_URL = "https://wdyouth.samcheok.go.kr/sub/Program1.php"

SAMCHEOK_LIFELONG_CANDIDATE_ID = "MUNI_IR_90DD3B4771BF"
SAMCHEOK_YOUTH_CANDIDATE_ID = "MUNI_IR_565500DF239C"
SAMCHEOK_DGYOUTH_CANDIDATE_ID = "MUNI_IR_72CCF0EE42A4"
SAMCHEOK_WDYOUTH_CANDIDATE_ID = "MUNI_IR_405013E57576"

SAMCHEOK_LEGACY_LIFELONG_URL = (
    "https://www.samcheok.go.kr/specialty/00465/01164.web?syear=2023"
)
SAMCHEOK_LEGACY_LIFELONG_CANDIDATE_ID = "MUNI_IR_0B7A7B0B1CB2"

SAMCHEOK_OWNERS: Mapping[str, Mapping[str, str]] = {
    "lifelong": {
        "provider": SAMCHEOK_LIFELONG_PROVIDER,
        "url": SAMCHEOK_LIFELONG_URL,
        "candidate_id": SAMCHEOK_LIFELONG_CANDIDATE_ID,
    },
    "youth": {
        "provider": SAMCHEOK_YOUTH_PROVIDER,
        "url": SAMCHEOK_YOUTH_URL,
        "candidate_id": SAMCHEOK_YOUTH_CANDIDATE_ID,
    },
    "dgyouth": {
        "provider": SAMCHEOK_DGYOUTH_PROVIDER,
        "url": SAMCHEOK_DGYOUTH_URL,
        "candidate_id": SAMCHEOK_DGYOUTH_CANDIDATE_ID,
    },
    "wdyouth": {
        "provider": SAMCHEOK_WDYOUTH_PROVIDER,
        "url": SAMCHEOK_WDYOUTH_URL,
        "candidate_id": SAMCHEOK_WDYOUTH_CANDIDATE_ID,
    },
}

SAMCHEOK_LIFELONG_BRANCHES = (
    "삼척평생학습관",
    "도계평생학습센터",
    "원덕평생학습센터",
)
_LIFELONG_INSTITUTION_ALIASES = {
    "삼척시평생학습관": "삼척평생학습관",
}
SAMCHEOK_YOUTH_BRANCHES: Mapping[str, tuple[str, ...]] = {
    "youth": ("삼척시청소년수련관",),
    "dgyouth": ("도계청소년장학센터",),
    "wdyouth": ("원덕청소년문화의집", "근덕청소년문화의집"),
}
SAMCHEOK_LIBRARY_BRANCHES = (
    "삼척교육문화관",
    "도계읍도서관",
    "원덕읍도서관",
    "남양작은도서관",
    "평생학습관 내 작은도서관",
)

SAMCHEOK_EXCLUDED_BOUNDARIES: Mapping[str, str] = {
    SAMCHEOK_LEGACY_LIFELONG_URL: (
        "2023 notice board is a partial shell; retarget the incumbent provider "
        "to the complete course ledger"
    ),
    "https://lib.gwe.go.kr/samecc/menu/3560/lecture-event/list/all": (
        "already collected by provider MUNI_LIB_GWE_GO_KR_303FFE72"
    ),
    "municipal_library_pages": (
        "official facility/catalogue information only; no structured programme ledger"
    ),
    "museum_and_arts_pages": (
        "news/facility surfaces without a stable public course ledger"
    ),
    "https://samcheoktour.kr": "tourism/experience reservations, not education courses",
    "https://scsci.or.kr/": (
        "admission/experience owner; its official education catalogues currently declare no rows"
    ),
    "school_partner_programmes": (
        "closed school-linked delivery without public individual recruitment"
    ),
}

SAMCHEOK_AUDIT_BASELINE: Mapping[str, Mapping[str, int]] = {
    "lifelong": {"source": 112, "current": 112, "returned": 112},
    "youth": {"source": 15, "current": 7, "returned": 7},
    "dgyouth": {"source": 10, "current": 10, "returned": 10},
    "wdyouth": {"source": 30, "current": 13, "returned": 13},
}

SAMCHEOK_PARSER = (
    "samcheok_four_disjoint_official_education_owners+complete_pagination_or_"
    "fixed_page_registries+empty_course_sentinels+stable_boundary_rechecks+"
    "current_public_details+official_branch_normalization+"
    "school_partner_and_existing_owner_exclusions+no_application_attachment_pii_routes"
)
SAMCHEOK_MAX_PAGES = 100
SAMCHEOK_MAX_DETAILS = 300
SAMCHEOK_MAX_WORKERS = 4
SAMCHEOK_MAX_BYTES = 5_000_000
SAMCHEOK_LIFELONG_PAGE_SIZE = 12
SAMCHEOK_UNDATED_ACTIVE_GRACE_DAYS = 45
SAMCHEOK_EMPTY_SENTINEL = "__MOONCEN_EMPTY_SENTINEL_5123000000__"


class SamcheokContractError(RuntimeError):
    """Raised when an audited Samcheok public-source contract changes."""


Fetcher = Callable[..., Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE = re.compile(r"\s+")
_DATE_TOKEN = re.compile(
    r"(?:(20\d{2})\s*(?:년|[./-])\s*)?"
    r"(\d{1,2})\s*(?:월|[./-])\s*(\d{1,2})(?:일)?"
)
_PHONE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+[.][A-Za-z]{2,}")
_FORBIDDEN_KEYS = frozenset(
    {
        "phone",
        "email",
        "contact",
        "manager",
        "instructor",
        "teacher",
        "description",
        "content",
        "attachments",
        "attachment_url",
        "image_url",
        "request_form",
        "applicant",
        "user_list",
    }
)


@dataclass(frozen=True)
class _StaticSpec:
    key: str
    owner: str
    url: str
    branch: str
    heading_token: str
    header_tokens: tuple[str, ...]
    title_index: int
    period_index: Optional[int]
    schedule_index: Optional[int]
    capacity_index: Optional[int]
    target_index: Optional[int]
    venue_index: Optional[int]
    operation_key: str
    application_keys: tuple[str, ...]
    fee_keys: tuple[str, ...]
    undated_active: bool = False


def youth_program_url(number: int) -> str:
    return f"https://youth.samcheok.go.kr/sub/Program{int(number)}.php"


def dgyouth_program_url(number: int) -> str:
    return f"https://dgyouth.samcheok.go.kr/sub/Program{int(number)}.php"


def wdyouth_program_url(number: int, *, geundeok: bool = False) -> str:
    suffix = "-gd" if geundeok else ""
    return f"https://wdyouth.samcheok.go.kr/sub/Program{int(number)}{suffix}.php"


_STATIC_SOURCE_SPECS: Mapping[str, tuple[_StaticSpec, ...]] = {
    "youth": (
        _StaticSpec(
            "regular", "youth", youth_program_url(1), "삼척시청소년수련관",
            "2026 삼척시청소년수련관", ("프로그램", "참가대상", "운영기간"),
            0, 2, 3, 4, 1, 5, "운영기간", ("접수기간",), ("수강료",),
        ),
        _StaticSpec(
            "summer", "youth", youth_program_url(2), "삼척시청소년수련관",
            "2026 삼척시청소년수련관", ("프로그램", "참가 대상", "운영 기간"),
            0, 2, 3, 4, 1, None, "운영기간", ("신청기간",), (),
        ),
    ),
    "dgyouth": (
        _StaticSpec(
            "regular", "dgyouth", dgyouth_program_url(1), "도계청소년장학센터",
            "교육문화 프로그램", ("프로그램", "운영일시", "회기"),
            0, None, 1, 3, 4, 5, "운영기간", ("접수기간",), (),
        ),
        _StaticSpec(
            "summer", "dgyouth", dgyouth_program_url(2), "도계청소년장학센터",
            "2026년 여름방학", ("구분", "프로그램", "운영일시"),
            1, None, 2, 4, 5, 6, "", ("접수기간",), (), True,
        ),
    ),
    "wdyouth": (
        _StaticSpec(
            "wondeok_regular", "wdyouth", wdyouth_program_url(1),
            "원덕청소년문화의집", "2026원덕청소년문화의집",
            ("프로그램", "운영기간", "운영일시"),
            0, 2, 3, 4, 1, 5, "운영기간", ("접수기간",), ("참가비",),
        ),
        _StaticSpec(
            "geundeok_regular", "wdyouth", wdyouth_program_url(1, geundeok=True),
            "근덕청소년문화의집", "2026년 근덕청소년문화의집",
            ("프로그램명", "운영기간", "운영일시"),
            0, 1, 2, 3, 4, 5, "운영기간", ("접수기간",), ("참가비",),
        ),
        _StaticSpec(
            "wondeok_summer", "wdyouth", wdyouth_program_url(2),
            "원덕청소년문화의집", "2026 여름방학프로그램",
            ("프로그램명", "운영기간", "운영일시"),
            0, 1, 2, 3, None, 4, "운영기간", (), ("수강료",),
        ),
        _StaticSpec(
            "geundeok_summer", "wdyouth", wdyouth_program_url(2, geundeok=True),
            "근덕청소년문화의집", "2026년 근덕청소년문화의집",
            ("프로그램명", "운영기간", "운영일시"),
            0, 1, 2, 3, 4, 5, "운영기간", ("접수기간",), ("참 가 비",),
        ),
        _StaticSpec(
            "geundeok_special", "wdyouth", wdyouth_program_url(6),
            "근덕청소년문화의집", "특성화 프로그램",
            ("프로그램명", "운영기간", "운영일시"),
            0, 1, 2, 3, 4, 5, "운영기간", ("접수기간",), (),
        ),
    ),
}

_STATIC_AUDIT_URLS: Mapping[str, tuple[str, ...]] = {
    "youth": (youth_program_url(3), youth_program_url(4), youth_program_url(5)),
    "dgyouth": (dgyouth_program_url(3), dgyouth_program_url(4)),
    "wdyouth": (wdyouth_program_url(3), wdyouth_program_url(4)),
}
_STATIC_SENTINELS: Mapping[str, str] = {
    "youth": youth_program_url(4),
    "dgyouth": dgyouth_program_url(4),
    "wdyouth": wdyouth_program_url(3),
}
_STATIC_NAV_EXPECTED: Mapping[str, frozenset[str]] = {
    "youth": frozenset(f"Program{n}.php" for n in range(1, 6)),
    "dgyouth": frozenset(f"Program{n}.php" for n in range(1, 5)),
    "wdyouth": frozenset(
        {
            "Program1.php", "Program1-gd.php", "Program2.php", "Program2-gd.php",
            "Program3.php", "Program4.php", "Program6.php",
        }
    ),
}


def lifelong_list_url(page: int) -> str:
    return f"{SAMCHEOK_LIFELONG_URL}?{urlencode({'cpage': int(page)})}"


def lifelong_detail_url(identity: Any) -> str:
    return f"{SAMCHEOK_LIFELONG_URL}?{urlencode({'amode': 'view', 'idx': str(identity)})}"


def lifelong_empty_sentinel_url() -> str:
    return f"{SAMCHEOK_LIFELONG_URL}?{urlencode({'cpage': 1, 'stype': 'title', 'sstring': SAMCHEOK_EMPTY_SENTINEL})}"


def _clean(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def owner_for_target(target: Any) -> str:
    provider = _clean(_target_value(target, "provider"))
    url = _clean(_target_value(target, "url"))
    for owner, config in SAMCHEOK_OWNERS.items():
        if provider == config["provider"] and url == config["url"]:
            return owner
    return ""


def is_samcheok_target(target: Any) -> bool:
    return bool(owner_for_target(target))


is_target = is_samcheok_target


def _query_once(url: str) -> tuple[Any, dict[str, str]]:
    parsed = urlparse(url)
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    values = dict(pairs)
    if len(values) != len(pairs):
        raise SamcheokContractError("duplicate query key")
    return parsed, values


def _positive_int(value: Any) -> bool:
    return bool(re.fullmatch(r"[1-9]\d*", _clean(value)))


def _classify_url(owner: str, method: str, url: str) -> str:
    parsed, query = _query_once(url)
    if method != "GET" or parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment:
        raise SamcheokContractError("request is outside the public GET boundary")
    if owner == "lifelong":
        if parsed.netloc != "www.samcheok.go.kr" or parsed.path != "/specialty/00465/01127.web":
            raise SamcheokContractError("lifelong route is not allowlisted")
        if set(query) == {"cpage"} and _positive_int(query["cpage"]):
            return "list"
        if query == {"cpage": "1", "stype": "title", "sstring": SAMCHEOK_EMPTY_SENTINEL}:
            return "list"
        if set(query) == {"amode", "idx"} and query.get("amode") == "view" and _positive_int(query.get("idx")):
            return "detail"
        raise SamcheokContractError("lifelong query is not allowlisted")
    if owner not in _STATIC_SOURCE_SPECS:
        raise SamcheokContractError("unknown Samcheok owner")
    allowed = {spec.url for spec in _STATIC_SOURCE_SPECS[owner]} | set(_STATIC_AUDIT_URLS[owner])
    if query or url not in allowed:
        raise SamcheokContractError("youth route is not allowlisted")
    return "list"


def _raw_session() -> requests.Session:
    current = requests.Session()
    current.trust_env = False
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/140 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    return current


def _default_fetcher(session: Any, method: str, url: str, *, timeout: int) -> Any:
    if method != "GET":
        raise SamcheokContractError("unsupported HTTP method")
    return session.get(url, timeout=timeout, allow_redirects=False)


def _response_bytes(response: Any, requested_url: str) -> bytes:
    status = int(getattr(response, "status_code", 200))
    if status != 200:
        raise SamcheokContractError(f"HTTP {status}")
    if getattr(response, "history", None):
        raise SamcheokContractError("redirect history is forbidden")
    headers = getattr(response, "headers", {}) or {}
    if headers.get("Location") or headers.get("location"):
        raise SamcheokContractError("redirect location is forbidden")
    response_url = _clean(getattr(response, "url", ""))
    if response_url and response_url != requested_url:
        raise SamcheokContractError("response URL changed")
    content = getattr(response, "content", None)
    if content is None:
        content = str(getattr(response, "text", response)).encode("utf-8")
    if not isinstance(content, (bytes, bytearray)):
        content = bytes(content)
    content = bytes(content)
    if not content or len(content) > SAMCHEOK_MAX_BYTES:
        raise SamcheokContractError("empty or oversized response")
    return content


class _Requester:
    def __init__(
        self,
        owner: str,
        session_factory: SessionFactory,
        fetcher: Fetcher,
        timeout: int,
        meta: dict[str, Any],
    ) -> None:
        self.owner = owner
        self.session_factory = session_factory
        self.fetcher = fetcher
        self.timeout = timeout
        self.meta = meta
        self.local = threading.local()
        self.lock = threading.Lock()
        self.sessions: list[Any] = []

    def _session(self) -> Any:
        current = getattr(self.local, "session", None)
        if current is None:
            current = self.session_factory()
            self.local.session = current
            with self.lock:
                self.sessions.append(current)
        return current

    def _retry(self, attempt: int) -> None:
        with self.lock:
            self.meta["request_retry_count"] += 1
        current = getattr(self.local, "session", None)
        if current is not None:
            close = getattr(current, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        self.local.session = None
        time.sleep(min(2.0, 0.25 * (2**attempt)))

    def soup(self, method: str, url: str) -> BeautifulSoup:
        kind = _classify_url(self.owner, method, url)
        with self.lock:
            self.meta["logical_requests"] += 1
            self.meta["list_requests" if kind == "list" else "detail_requests"] += 1
        attempts = 4
        last_error: Optional[Exception] = None
        for attempt in range(attempts):
            with self.lock:
                self.meta["physical_requests"] += 1
            try:
                response = self.fetcher(self._session(), method, url, timeout=self.timeout)
                status = int(getattr(response, "status_code", 200))
                if status in {408, 429, 500, 502, 503, 504} and attempt + 1 < attempts:
                    self._retry(attempt)
                    continue
                return BeautifulSoup(_response_bytes(response, url), "html.parser")
            except requests.RequestException as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    self._retry(attempt)
                    continue
                raise
        if last_error is not None:
            raise last_error
        raise SamcheokContractError("request retries exhausted")

    def close(self) -> None:
        seen: set[int] = set()
        for current in self.sessions:
            if id(current) in seen:
                continue
            seen.add(id(current))
            close = getattr(current, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass


def _parallel_map(values: Sequence[Any], function: Callable[[Any], Any], workers: int) -> list[Any]:
    if not values:
        return []
    if workers <= 1 or len(values) == 1:
        return [function(value) for value in values]
    with ThreadPoolExecutor(max_workers=min(workers, len(values))) as pool:
        return list(pool.map(function, values))


def _date_pair(
    value: Any,
    field: str,
    *,
    default_year: Optional[int] = None,
    required: bool = True,
) -> Optional[tuple[date, date]]:
    matches = _DATE_TOKEN.findall(_clean(value))
    if not matches:
        if required:
            raise SamcheokContractError(f"missing {field} date range")
        return None
    year = default_year
    parsed: list[date] = []
    for year_text, month_text, day_text in matches[:2]:
        if year_text:
            year = int(year_text)
        if year is None:
            raise SamcheokContractError(f"missing {field} year")
        try:
            parsed.append(date(year, int(month_text), int(day_text)))
        except ValueError as exc:
            raise SamcheokContractError(f"invalid {field} date") from exc
    if len(parsed) == 1:
        parsed.append(parsed[0])
    if parsed[1] < parsed[0]:
        raise SamcheokContractError(f"reversed {field} date range")
    return parsed[0], parsed[1]


def _definition_pairs(container: Any) -> dict[str, str]:
    pairs: dict[str, str] = {}
    if container is None:
        return pairs
    for node in container.select("dt"):
        sibling = node.find_next_sibling("dd")
        key = _clean(node.get_text(" ", strip=True))
        value = _clean(sibling.get_text(" ", strip=True)) if sibling else ""
        if key and key not in pairs:
            pairs[key] = value
    return pairs


def _table_pairs(table: Any) -> dict[str, str]:
    pairs: dict[str, str] = {}
    if table is None:
        return pairs
    for row in table.select("tr"):
        cells = row.select(":scope > th, :scope > td")
        for index, node in enumerate(cells[:-1]):
            if node.name == "th" and cells[index + 1].name == "td":
                key = _clean(node.get_text(" ", strip=True)).rstrip(":")
                value = _clean(cells[index + 1].get_text(" ", strip=True))
                if key and key not in pairs:
                    pairs[key] = value
    return pairs


def _table_grid(table: Any) -> list[list[str]]:
    """Expand tbody rowspans while omitting markup-only empty rows."""

    rows: list[list[str]] = []
    carry: dict[int, tuple[int, str]] = {}
    for tr in table.select("tbody > tr") if table is not None else ():
        direct = tr.select(":scope > th, :scope > td")
        occupied: dict[int, str] = {}
        for column, (remaining, value) in list(carry.items()):
            occupied[column] = value
            if remaining <= 1:
                del carry[column]
            else:
                carry[column] = (remaining - 1, value)
        column = 0
        for cell in direct:
            while column in occupied:
                column += 1
            value = _clean(cell.get_text(" ", strip=True))
            try:
                colspan = max(1, int(cell.get("colspan", 1)))
                rowspan = max(1, int(cell.get("rowspan", 1)))
            except (TypeError, ValueError):
                raise SamcheokContractError("invalid table span")
            for offset in range(colspan):
                occupied[column + offset] = value
                if rowspan > 1:
                    carry[column + offset] = (rowspan - 1, value)
            column += colspan
        if not direct:
            continue
        maximum = max(occupied, default=-1)
        rows.append([occupied.get(index, "") for index in range(maximum + 1)])
    return rows


def _source_hash(rows: Iterable[Mapping[str, Any]]) -> str:
    identities = sorted(_clean(row.get("source_identity")) for row in rows)
    return hashlib.sha256("\n".join(identities).encode("utf-8")).hexdigest()


def _status(value: Any) -> str:
    text = _clean(value)
    if any(token in text for token in ("접수중", "모집중", "신청하기", "진행")):
        return "OPEN"
    if any(token in text for token in ("대기", "예정")):
        return "SCHEDULED"
    if any(token in text for token in ("마감", "종료", "폐강", "취소")):
        return "CLOSED"
    return text or "PUBLISHED"


def _base_row(provider: str, identity: str, title: str) -> dict[str, Any]:
    return {
        "provider": provider,
        "municipality_code": SAMCHEOK_MUNICIPALITY_CODE,
        "municipality_name": SAMCHEOK_MUNICIPALITY_NAME,
        "provider_course_id": f"{provider}:{identity}",
        "source_course_id": identity,
        "title": title,
        "application_url": "",
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "classification_locked": True,
    }


@dataclass(frozen=True)
class _LifePage:
    requested: int
    reported: int
    last: int
    rows: tuple[dict[str, Any], ...]


def _lifelong_branch(title: str, listed: str) -> tuple[str, bool]:
    match = re.match(r"^\[([^]]+)]", title)
    if not match:
        raise SamcheokContractError("lifelong title lost its institution prefix")
    prefix = match.group(1)
    if prefix.startswith("삼척평생학습관"):
        branch = "삼척평생학습관"
    elif prefix == "도계평생학습센터":
        branch = "도계평생학습센터"
    elif prefix == "원덕평생학습센터":
        branch = "원덕평생학습센터"
    else:
        raise SamcheokContractError(f"unknown lifelong institution prefix: {prefix}")
    if listed == branch:
        return branch, False
    if _LIFELONG_INSTITUTION_ALIASES.get(listed) == branch:
        return branch, True
    if branch == "원덕평생학습센터" and listed == "원":
        return branch, True
    raise SamcheokContractError(f"lifelong institution drift: {listed}")


def _lifelong_page(
    soup: BeautifulSoup,
    requested: int,
    *,
    expected_last: Optional[int] = None,
    allow_clamp: bool = False,
    empty_search: bool = False,
) -> _LifePage:
    table = soup.select_one("table.t1")
    if table is None:
        raise SamcheokContractError("lifelong course table disappeared")
    header = tuple(_clean(node.get_text(" ", strip=True)) for node in table.select("thead th"))
    expected_header = (
        "강좌명", "교육기간", "수강신청기간", "교육기관", "수강료", "접수방법", "상태"
    )
    if header != expected_header:
        raise SamcheokContractError("lifelong list header changed")
    current_node = soup.select_one(".pagination .on a")
    current_match = re.search(r"\d+", _clean(current_node.get_text(" ", strip=True)) if current_node else "")
    if not current_match:
        raise SamcheokContractError("lifelong current-page marker changed")
    reported = int(current_match.group())
    page_numbers = [reported]
    for anchor in soup.select(".pagination a[href]"):
        query = dict(parse_qsl(urlparse(anchor.get("href", "")).query, keep_blank_values=True))
        if _positive_int(query.get("cpage")):
            page_numbers.append(int(query["cpage"]))
    last = max(page_numbers)
    if expected_last is not None and last != expected_last:
        raise SamcheokContractError("lifelong pagination boundary drift")
    if allow_clamp:
        if reported != last or requested != last + 1:
            raise SamcheokContractError("lifelong post-last page no longer clamps to the last page")
    elif reported != requested:
        raise SamcheokContractError("lifelong reported page changed")
    rows: list[dict[str, Any]] = []
    empty_message = False
    for tr in table.select("tbody > tr"):
        cells = tr.select(":scope > th, :scope > td")
        if len(cells) == 1:
            message = _clean(cells[0].get_text(" ", strip=True))
            if "등록된 내용이 없습니다" in message:
                empty_message = True
                continue
            if empty_search:
                continue
        if len(cells) != 7:
            raise SamcheokContractError("lifelong list row shape changed")
        anchor = cells[0].select_one("a[href]")
        if anchor is None:
            raise SamcheokContractError("lifelong detail link disappeared")
        parsed, query = _query_once(anchor.get("href", ""))
        if query.get("amode") != "view" or not _positive_int(query.get("idx")):
            raise SamcheokContractError("lifelong detail identity changed")
        identity = query["idx"]
        title = _clean(cells[0].get_text(" ", strip=True))
        start, end = _date_pair(cells[1].get_text(" ", strip=True), "lifelong education") or (None, None)
        apply_start, apply_end = _date_pair(
            cells[2].get_text(" ", strip=True), "lifelong application"
        ) or (None, None)
        branch, repaired = _lifelong_branch(title, _clean(cells[3].get_text(" ", strip=True)))
        rows.append(
            {
                "source_identity": identity,
                "title": title,
                "start": start,
                "end": end,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "branch": branch,
                "branch_repaired": repaired,
                "fee": _clean(cells[4].get_text(" ", strip=True)),
                "application_method": _clean(cells[5].get_text(" ", strip=True)),
                "source_status": _clean(cells[6].get_text(" ", strip=True)),
            }
        )
    if empty_search:
        if rows or not empty_message or last != 1 or reported != 1:
            raise SamcheokContractError("lifelong empty-search sentinel changed")
    elif empty_message:
        raise SamcheokContractError("lifelong source page unexpectedly became empty")
    return _LifePage(requested, reported, last, tuple(rows))


def _life_signature(page: _LifePage) -> tuple[Any, ...]:
    return (
        page.reported,
        page.last,
        tuple(
            (row["source_identity"], row["title"], row["end"], row["source_status"])
            for row in page.rows
        ),
    )


def _lifelong_detail(source: Mapping[str, Any], soup: BeautifulSoup) -> tuple[dict[str, Any], Counter[str]]:
    title_node = soup.select_one("h2.h1")
    title = _clean(title_node.get_text(" ", strip=True)) if title_node else ""
    if title != source["title"]:
        raise SamcheokContractError(f"lifelong {source['source_identity']}: detail title drift")
    pairs = _table_pairs(soup.select_one("table.t3"))
    required = {
        "교육 구분", "교육 대상", "교육 기관", "교육 장소", "강의 시간",
        "접수인원/정원", "수강료", "접수 방법", "접수 상태",
    }
    if not required.issubset(pairs):
        raise SamcheokContractError(f"lifelong {source['source_identity']}: detail fields changed")
    detail_branch = pairs["교육 기관"]
    normalized_detail_branch = _LIFELONG_INSTITUTION_ALIASES.get(
        detail_branch, detail_branch
    )
    repaired_detail_branch = (
        str(source["source_identity"]) == "199"
        and bool(source.get("branch_repaired"))
        and source["branch"] == "원덕평생학습센터"
        and detail_branch == "원"
    )
    if (
        normalized_detail_branch != source["branch"]
        and not repaired_detail_branch
    ):
        raise SamcheokContractError(f"lifelong {source['source_identity']}: detail institution drift")
    if pairs["접수 상태"] != source["source_status"]:
        raise SamcheokContractError(f"lifelong {source['source_identity']}: detail status drift")
    start_date = source["start"].isoformat()
    end_date = source["end"].isoformat()
    apply_start = source["apply_start"].isoformat()
    apply_end = source["apply_end"].isoformat()
    venue = pairs["교육 장소"] or source["branch"]
    schedule = pairs["강의 시간"] or "시간 별도 안내"
    detail_url = lifelong_detail_url(source["source_identity"])
    row = _base_row(SAMCHEOK_LIFELONG_PROVIDER, str(source["source_identity"]), str(source["title"]))
    row.update(
        {
            "status": _status(source["source_status"]),
            "source_status": source["source_status"],
            "start_date": start_date,
            "end_date": end_date,
            "period": f"{start_date} ~ {end_date}",
            "apply_start": apply_start,
            "apply_end": apply_end,
            "apply_start_date": apply_start,
            "apply_end_date": apply_end,
            "apply_period": f"{apply_start} ~ {apply_end}",
            "branch": source["branch"],
            "branch_code": {
                "삼척평생학습관": "SAMCHEOK_LIFELONG",
                "도계평생학습센터": "DOGYE_LIFELONG",
                "원덕평생학습센터": "WONDEOK_LIFELONG",
            }[source["branch"]],
            "preserve_branch": True,
            "venue": venue,
            "venue_name": venue,
            "category": pairs["교육 구분"],
            "target": pairs["교육 대상"],
            "schedule": schedule,
            "schedule_raw": schedule,
            "fee": pairs["수강료"],
            "capacity_text": pairs["접수인원/정원"],
            "capacity": pairs["접수인원/정원"],
            "application_method": pairs["접수 방법"],
            "source_url": detail_url,
            "raw_url": detail_url,
            "raw_fields": {
                "parser": "samcheok_lifelong_complete",
                "idx": source["source_identity"],
            },
        }
    )
    sensitive = sum(
        bool(pairs.get(key))
        for key in ("문의 전화", "강사명", "강사경력사항", "강의 내용", "학습 목표")
    )
    main = soup.select_one("#contents") or soup
    controls = sum(
        1
        for anchor in main.select("a[href]")
        if any(
            token in (_clean(anchor.get_text(" ", strip=True)) + anchor.get("href", "")).lower()
            for token in ("신청", "apply", "insert", "write")
        )
    )
    return row, Counter(sensitive_fields=sensitive, application_controls=controls)


def _collect_lifelong(
    requester: _Requester,
    cutoff: date,
    max_pages: int,
    detail_limit: int,
    workers: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    first = _lifelong_page(requester.soup("GET", lifelong_list_url(1)), 1)
    required = first.last + 6
    if required > max_pages:
        raise SamcheokContractError(f"max_pages cap allows {max_pages} of {required} lifelong requests")
    pages: dict[int, _LifePage] = {1: first}
    pages.update(
        _parallel_map(
            list(range(2, first.last + 1)),
            lambda page: (
                page,
                _lifelong_page(
                    requester.soup("GET", lifelong_list_url(page)),
                    page,
                    expected_last=first.last,
                ),
            ),
            workers,
        )
    )
    last_page = pages[first.last]
    clamped = _lifelong_page(
        requester.soup("GET", lifelong_list_url(first.last + 1)),
        first.last + 1,
        expected_last=first.last,
        allow_clamp=True,
    )
    if _life_signature(clamped)[1:] != _life_signature(last_page)[1:]:
        raise SamcheokContractError("lifelong clamped post-last sentinel differs from last page")
    empty = _lifelong_page(
        requester.soup("GET", lifelong_empty_sentinel_url()), 1, empty_search=True
    )
    listed = [dict(row) for page in range(1, first.last + 1) for row in pages[page].rows]
    if not listed or len({row["source_identity"] for row in listed}) != len(listed):
        raise SamcheokContractError("lifelong full source union changed")
    for page in range(1, first.last):
        if len(pages[page].rows) != SAMCHEOK_LIFELONG_PAGE_SIZE:
            raise SamcheokContractError(f"lifelong page {page}: page-size contract changed")
    if not 1 <= len(last_page.rows) <= SAMCHEOK_LIFELONG_PAGE_SIZE:
        raise SamcheokContractError("lifelong final page size changed")
    current = [row for row in listed if row["end"] >= cutoff]
    if len(current) > detail_limit:
        raise SamcheokContractError(
            f"detail_limit allows {detail_limit} of {len(current)} lifelong details"
        )
    parsed = _parallel_map(
        current,
        lambda source: _lifelong_detail(
            source, requester.soup("GET", lifelong_detail_url(source["source_identity"]))
        ),
        workers,
    )
    rechecks: dict[str, bool] = {}
    for page, original in ((1, first), (first.last, last_page)):
        observed = _lifelong_page(
            requester.soup("GET", lifelong_list_url(page)),
            page,
            expected_last=first.last,
        )
        rechecks[str(page)] = _life_signature(observed) == _life_signature(original)
    observed_clamp = _lifelong_page(
        requester.soup("GET", lifelong_list_url(first.last + 1)),
        first.last + 1,
        expected_last=first.last,
        allow_clamp=True,
    )
    rechecks[f"clamped:{first.last + 1}"] = _life_signature(observed_clamp) == _life_signature(clamped)
    observed_empty = _lifelong_page(
        requester.soup("GET", lifelong_empty_sentinel_url()), 1, empty_search=True
    )
    rechecks["empty-search"] = _life_signature(observed_empty) == _life_signature(empty)
    if not all(rechecks.values()):
        raise SamcheokContractError("lifelong boundary drift")
    discarded = Counter()
    for _row, audit in parsed:
        discarded.update(audit)
    rows = [row for row, _audit in parsed]
    audit = {
        "source_total": len(listed),
        "source_rows": len(listed),
        "pages": first.last,
        "page_size": SAMCHEOK_LIFELONG_PAGE_SIZE,
        "clamped_post_last_page": first.last + 1,
        "empty_sentinel_url": lifelong_empty_sentinel_url(),
        "empty_sentinel_rows": 0,
        "boundary_rechecks": rechecks,
        "current_source_count": len(current),
        "detail_verified": len(parsed),
        "detail_transport": "public_detail_pages",
        "branch_repair_count": sum(bool(row["branch_repaired"]) for row in listed),
        "sensitive_detail_fields_discarded": discarded["sensitive_fields"],
        "application_controls_discarded": discarded["application_controls"],
        "branch_counts": dict(Counter(row["branch"] for row in rows)),
        "source_identity_count": len(listed),
        "source_identity_sha256": _source_hash(listed),
        "required_list_requests": required,
    }
    return rows, audit


def _cohort_year(heading: str) -> int:
    match = re.search(r"20\d{2}", heading)
    if not match:
        raise SamcheokContractError("static programme heading lost its year")
    return int(match.group())


def _page_controls(soup: BeautifulSoup) -> Counter[str]:
    main = soup.select_one("#SubContWrap") or soup.select_one("#contents") or soup
    counts: Counter[str] = Counter()
    for anchor in main.select("a[href]"):
        href = _clean(anchor.get("href", ""))
        lowered = href.lower()
        label = _clean(anchor.get_text(" ", strip=True)).lower()
        if lowered.startswith("tel:") or "form.naver.com" in lowered or "application.php" in lowered:
            counts["application_controls"] += 1
        elif lowered.endswith((".hwp", ".hwpx", ".pdf")) or "/data/" in lowered:
            counts["attachment_controls"] += 1
        elif any(token in label + lowered for token in ("신청", "apply", "register")):
            counts["application_controls"] += 1
    counts["sensitive_fields"] += len(_PHONE.findall(_clean(main.get_text(" ", strip=True))))
    counts["sensitive_fields"] += len(_EMAIL.findall(_clean(main.get_text(" ", strip=True))))
    return counts


def _safe_index(values: Sequence[str], index: Optional[int]) -> str:
    return values[index] if index is not None and 0 <= index < len(values) else ""


def _static_page(spec: _StaticSpec, soup: BeautifulSoup) -> tuple[list[dict[str, Any]], Counter[str]]:
    box = soup.select_one(".noticeBox")
    heading_node = box.select_one("h4") if box else None
    subtitle_node = box.select_one(".sub-title-M") if box else None
    heading = _clean(
        " ".join(
            node.get_text(" ", strip=True)
            for node in (heading_node, subtitle_node)
            if node is not None
        )
    )
    if spec.heading_token not in heading:
        raise SamcheokContractError(f"{spec.key}: cohort heading changed")
    year = _cohort_year(heading)
    definitions = _definition_pairs(box)
    operation: Optional[tuple[date, date]] = None
    if spec.operation_key:
        if spec.operation_key not in definitions:
            raise SamcheokContractError(f"{spec.key}: operation period disappeared")
        operation = _date_pair(definitions[spec.operation_key], f"{spec.key} operation")
    application: Optional[tuple[date, date]] = None
    for key in spec.application_keys:
        if definitions.get(key):
            application = _date_pair(definitions[key], f"{spec.key} application")
            break
    if spec.application_keys and application is None:
        raise SamcheokContractError(f"{spec.key}: application period disappeared")
    table = soup.select_one("table.t3")
    if table is None:
        raise SamcheokContractError(f"{spec.key}: programme table disappeared")
    header = _clean(" ".join(node.get_text(" ", strip=True) for node in table.select("thead th")))
    compact_header = re.sub(r"\s+", "", header)
    if not all(re.sub(r"\s+", "", token) in compact_header for token in spec.header_tokens):
        raise SamcheokContractError(f"{spec.key}: programme table header changed")
    fallback_target = next(
        (definitions[key] for key in ("모집대상", "운영대상", "참가대상") if definitions.get(key)),
        "",
    )
    fee = next((definitions[key] for key in spec.fee_keys if definitions.get(key)), "")
    rows: list[dict[str, Any]] = []
    for values in _table_grid(table):
        title = _safe_index(values, spec.title_index)
        if not title or title == "계" or title.startswith("대기자"):
            continue
        if len(title) > 200:
            raise SamcheokContractError(f"{spec.key}: implausible programme title")
        start, end = operation or (None, None)
        period_text = _safe_index(values, spec.period_index)
        if period_text:
            try:
                row_period = _date_pair(
                    period_text,
                    f"{spec.key} row period",
                    default_year=year,
                    required=False,
                )
            except SamcheokContractError as exc:
                if "reversed" not in str(exc) or operation is None:
                    raise
                row_period = None
            if row_period is not None:
                start, end = row_period
        if start is None and not spec.undated_active:
            raise SamcheokContractError(f"{spec.key}: programme date disappeared")
        identity = f"{spec.key}:{hashlib.sha256(title.encode('utf-8')).hexdigest()[:12]}"
        rows.append(
            {
                "source_identity": identity,
                "page_key": spec.key,
                "title": title,
                "branch": spec.branch,
                "cohort": heading,
                "cohort_year": year,
                "start": start,
                "end": end,
                "apply_start": application[0] if application else None,
                "apply_end": application[1] if application else None,
                "schedule": _safe_index(values, spec.schedule_index),
                "capacity": _safe_index(values, spec.capacity_index),
                "target": _safe_index(values, spec.target_index) or fallback_target,
                "venue": _safe_index(values, spec.venue_index),
                "fee": fee,
                "source_url": spec.url,
                "undated_active": spec.undated_active,
            }
        )
    if not rows:
        raise SamcheokContractError(f"{spec.key}: programme table became empty")
    if len({row["source_identity"] for row in rows}) != len(rows):
        raise SamcheokContractError(f"{spec.key}: duplicate programme identity")
    return rows, _page_controls(soup)


def _static_signature(rows: Sequence[Mapping[str, Any]]) -> tuple[Any, ...]:
    return tuple(
        (
            row["source_identity"], row["title"], row.get("end"), row.get("apply_end"),
            row["branch"],
        )
        for row in rows
    )


def _eligible_table_count(soup: BeautifulSoup) -> int:
    return sum(len(_table_grid(table)) for table in soup.select(".noticeBox ~ .scrollTB table.t3"))


def _institutional_count(owner: str, url: str, soup: BeautifulSoup) -> int:
    if url not in _STATIC_AUDIT_URLS[owner]:
        return 0
    if url.endswith("Program3.php") and owner == "youth":
        table = soup.select_one("table.t3")
        return len(_table_grid(table)) if table else 0
    if (owner == "dgyouth" and url.endswith("Program3.php")) or (
        owner == "wdyouth" and url.endswith("Program4.php")
    ):
        count = 0
        for table in soup.select("table.t3"):
            count += sum(_positive_int(row[0]) for row in _table_grid(table) if row)
        return count
    return 0


def _nav_names(soups: Iterable[BeautifulSoup]) -> frozenset[str]:
    names: set[str] = set()
    for soup in soups:
        for anchor in soup.select("a[href]"):
            name = urlparse(anchor.get("href", "")).path.rsplit("/", 1)[-1]
            if re.fullmatch(r"Program\d+(?:-gd)?[.]php", name):
                names.add(name)
    return frozenset(names)


def _inline_current(source: Mapping[str, Any], cutoff: date) -> bool:
    end = source.get("end")
    if isinstance(end, date):
        return end >= cutoff
    if not source.get("undated_active"):
        return False
    apply_start, apply_end = source.get("apply_start"), source.get("apply_end")
    return (
        isinstance(apply_start, date)
        and isinstance(apply_end, date)
        and int(source.get("cohort_year") or 0) == cutoff.year
        and apply_start - timedelta(days=90) <= cutoff
        and cutoff <= apply_end + timedelta(days=SAMCHEOK_UNDATED_ACTIVE_GRACE_DAYS)
    )


def _inline_status(source: Mapping[str, Any], cutoff: date) -> tuple[str, str]:
    start, end = source.get("apply_start"), source.get("apply_end")
    if isinstance(start, date) and cutoff < start:
        return "SCHEDULED", "접수예정"
    if isinstance(start, date) and isinstance(end, date) and start <= cutoff <= end:
        return "OPEN", "접수중"
    if isinstance(end, date) and cutoff > end:
        return "CLOSED", "접수마감"
    return "PUBLISHED", "공개"


def _inline_row(owner: str, source: Mapping[str, Any], cutoff: date) -> dict[str, Any]:
    provider = SAMCHEOK_OWNERS[owner]["provider"]
    identity = str(source["source_identity"])
    status, source_status = _inline_status(source, cutoff)
    start_date = source["start"].isoformat() if isinstance(source.get("start"), date) else ""
    end_date = source["end"].isoformat() if isinstance(source.get("end"), date) else ""
    apply_start = (
        source["apply_start"].isoformat()
        if isinstance(source.get("apply_start"), date)
        else ""
    )
    apply_end = (
        source["apply_end"].isoformat()
        if isinstance(source.get("apply_end"), date)
        else ""
    )
    period = " ~ ".join(part for part in (start_date, end_date) if part) or "일정 별도 안내"
    apply_period = (
        " ~ ".join(part for part in (apply_start, apply_end) if part)
        or "접수일 별도 안내"
    )
    venue = _clean(source.get("venue")) or str(source["branch"])
    schedule = _clean(source.get("schedule")) or "시간 별도 안내"
    target = _clean(source.get("target")) or "대상 별도 안내"
    fee = _clean(source.get("fee")) or "요금 별도 안내"
    source_url = str(source["source_url"])
    raw_url = f"{source_url}#course-{identity}"
    row = _base_row(provider, identity, str(source["title"]))
    row.update(
        {
            "status": status,
            "source_status": source_status,
            "start_date": start_date,
            "end_date": end_date,
            "period": period,
            "apply_start": apply_start,
            "apply_end": apply_end,
            "apply_start_date": apply_start,
            "apply_end_date": apply_end,
            "apply_period": apply_period,
            "branch": source["branch"],
            "branch_code": {
                "삼척시청소년수련관": "SAMCHEOK_YOUTH",
                "도계청소년장학센터": "DOGYE_YOUTH",
                "원덕청소년문화의집": "WONDEOK_YOUTH",
                "근덕청소년문화의집": "GEUNDEOK_YOUTH",
            }[source["branch"]],
            "preserve_branch": True,
            "venue": venue,
            "venue_name": venue,
            "category": "청소년 교육문화프로그램",
            "target": target,
            "schedule": schedule,
            "schedule_raw": schedule,
            "fee": fee,
            "capacity_text": source["capacity"],
            "capacity": source["capacity"],
            "application_method": "",
            "source_url": source_url,
            "raw_url": raw_url,
            "raw_fields": {
                "parser": f"samcheok_{owner}_fixed_public_pages",
                "page_key": source["page_key"],
                "cohort": source["cohort"],
                "date_basis": (
                    "application_window_only" if source.get("undated_active") else "published_operation_period"
                ),
            },
        }
    )
    return row


def _collect_static(
    owner: str,
    requester: _Requester,
    cutoff: date,
    max_pages: int,
    detail_limit: int,
    workers: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    specs = _STATIC_SOURCE_SPECS[owner]
    audit_urls = _STATIC_AUDIT_URLS[owner]
    urls = tuple(dict.fromkeys([*(spec.url for spec in specs), *audit_urls]))
    required = len(urls) + 3
    if required > max_pages:
        raise SamcheokContractError(f"max_pages cap allows {max_pages} of {required} {owner} requests")
    fetched_pairs = _parallel_map(
        urls,
        lambda url: (url, requester.soup("GET", url)),
        workers,
    )
    soups = dict(fetched_pairs)
    observed_nav = _nav_names(soups.values())
    missing_nav = _STATIC_NAV_EXPECTED[owner] - observed_nav
    if missing_nav:
        raise SamcheokContractError(f"{owner} navigation registry changed: {sorted(missing_nav)}")
    parsed_pages: dict[str, list[dict[str, Any]]] = {}
    discarded = Counter()
    for spec in specs:
        parsed, audit = _static_page(spec, soups[spec.url])
        parsed_pages[spec.url] = parsed
        discarded.update(audit)
    sentinel_url = _STATIC_SENTINELS[owner]
    sentinel_rows = _eligible_table_count(soups[sentinel_url])
    if sentinel_rows:
        raise SamcheokContractError(f"{owner} empty-course sentinel returned rows")
    institutional = sum(_institutional_count(owner, url, soups[url]) for url in audit_urls)
    listed = [dict(row) for spec in specs for row in parsed_pages[spec.url]]
    if len({row["source_identity"] for row in listed}) != len(listed):
        raise SamcheokContractError(f"{owner} full source union changed")
    current = [row for row in listed if _inline_current(row, cutoff)]
    if len(current) > detail_limit:
        raise SamcheokContractError(
            f"detail_limit allows {detail_limit} of {len(current)} {owner} inline details"
        )
    rows = [_inline_row(owner, source, cutoff) for source in current]
    first_spec, last_spec = specs[0], specs[-1]
    rechecks: dict[str, bool] = {}
    for label, spec in (("first", first_spec), ("last", last_spec)):
        observed, _audit = _static_page(spec, requester.soup("GET", spec.url))
        rechecks[label] = _static_signature(observed) == _static_signature(parsed_pages[spec.url])
    observed_sentinel = requester.soup("GET", sentinel_url)
    rechecks["empty-sentinel"] = _eligible_table_count(observed_sentinel) == 0
    if not all(rechecks.values()):
        raise SamcheokContractError(f"{owner} boundary drift")
    audit = {
        "source_total": len(listed),
        "source_rows": len(listed),
        "pages": len(specs),
        "audited_navigation_pages": len(urls),
        "empty_sentinel_url": sentinel_url,
        "empty_sentinel_rows": 0,
        "boundary_rechecks": rechecks,
        "current_source_count": len(current),
        "detail_verified": len(current),
        "detail_transport": "inline_public_table",
        "undated_current_count": sum(not isinstance(row.get("end"), date) for row in current),
        "application_controls_discarded": discarded["application_controls"],
        "attachment_controls_discarded": discarded["attachment_controls"],
        "sensitive_detail_fields_discarded": discarded["sensitive_fields"],
        "excluded_surface_counts": {"closed_school_partner_programmes": institutional},
        "branch_counts": dict(Counter(row["branch"] for row in rows)),
        "source_identity_count": len(listed),
        "source_identity_sha256": _source_hash(listed),
        "fixed_page_registry_complete": True,
        "required_list_requests": required,
    }
    return rows, audit


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _initial_meta(owner: str, cutoff: date) -> dict[str, Any]:
    config = SAMCHEOK_OWNERS.get(owner, {})
    return {
        "owner": owner,
        "provider": config.get("provider", ""),
        "canonical_url": config.get("url", ""),
        "candidate_id": config.get("candidate_id", ""),
        "municipality_code": SAMCHEOK_MUNICIPALITY_CODE,
        "audit_date": cutoff.isoformat(),
        "logical_requests": 0,
        "physical_requests": 0,
        "list_requests": 0,
        "detail_requests": 0,
        "request_retry_count": 0,
        "application_endpoint_requests": 0,
        "login_endpoint_requests": 0,
        "attachment_endpoint_requests": 0,
        "payment_endpoint_requests": 0,
        "pii_endpoint_requests": 0,
        "pii_values_persisted": 0,
        "source_cap_reached": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "returned_count": 0,
        "configured_collection_error": "",
    }


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                key_text = str(key).lower()
                child_path = f"{path}.{key}" if path else str(key)
                if key_text in _FORBIDDEN_KEYS:
                    errors.append(f"forbidden key {child_path}")
                walk(child, child_path)
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")
        elif isinstance(value, str) and (_PHONE.search(value) or _EMAIL.search(value)):
            errors.append(f"PII value in {path}")

    walk(row, "")
    return errors


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            output.append(row)
    return output


def collect_samcheok_education(
    target: Any,
    timeout: int = 35,
    max_pages: int = SAMCHEOK_MAX_PAGES,
    detail_limit: int = SAMCHEOK_MAX_DETAILS,
    *,
    today: Optional[date | datetime | str] = None,
    max_workers: int = SAMCHEOK_MAX_WORKERS,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one complete current/future Samcheok education-owner snapshot."""

    try:
        cutoff = _today(today)
    except (TypeError, ValueError):
        cutoff = datetime.now(ZoneInfo("Asia/Seoul")).date()
        meta = _initial_meta("", cutoff)
        meta["configured_collection_error"] = "today is invalid"
        return [], SAMCHEOK_PARSER, meta
    owner = owner_for_target(target)
    meta = _initial_meta(owner, cutoff)
    if not owner:
        meta.update(
            {
                "provider": _clean(_target_value(target, "provider")),
                "canonical_url": _clean(_target_value(target, "url")),
                "configured_collection_error": "non-canonical Samcheok education target",
            }
        )
        return [], SAMCHEOK_PARSER, meta
    try:
        timeout, max_pages, detail_limit, max_workers = map(
            int, (timeout, max_pages, detail_limit, max_workers)
        )
        if timeout < 1 or max_pages < 1 or detail_limit < 0 or not 1 <= max_workers <= 16:
            raise ValueError
    except (TypeError, ValueError):
        meta["configured_collection_error"] = "invalid collection limits"
        return [], SAMCHEOK_PARSER, meta
    if fetcher is None and session_factory is None and not allow_raw_requests_for_tests:
        meta["configured_collection_error"] = (
            "raw requests disabled; inject the managed session/fetcher or explicitly opt in"
        )
        return [], SAMCHEOK_PARSER, meta
    factory = session_factory or _raw_session
    requester = _Requester(owner, factory, fetcher or _default_fetcher, timeout, meta)
    try:
        if owner == "lifelong":
            rows, audit = _collect_lifelong(
                requester, cutoff, max_pages, detail_limit, max_workers
            )
        else:
            rows, audit = _collect_static(
                owner, requester, cutoff, max_pages, detail_limit, max_workers
            )
        original_ids = [row["provider_course_id"] for row in rows]
        deduped = list((dedupe_rows or _dedupe_default)(rows))
        if any(not isinstance(row, Mapping) for row in deduped):
            raise SamcheokContractError("dedupe returned a non-object row")
        if [row.get("provider_course_id") for row in deduped] != original_ids:
            raise SamcheokContractError("dedupe changed complete owner identity/cardinality")
        privacy = [error for row in deduped for error in _privacy_errors(row)]
        if privacy:
            raise SamcheokContractError("; ".join(dict.fromkeys(privacy)))
        if any(row.get("application_url") for row in deduped):
            raise SamcheokContractError("application endpoint escaped output boundary")
        deduped = sorted(
            (dict(row) for row in deduped),
            key=lambda row: (
                _clean(row.get("start_date")),
                _clean(row.get("title")),
                _clean(row.get("provider_course_id")),
            ),
        )
        meta.update(audit)
        details_complete = audit["detail_verified"] == audit["current_source_count"]
        if audit["detail_transport"] == "public_detail_pages":
            details_complete = details_complete and meta["detail_requests"] == audit["detail_verified"]
        meta.update(
            {
                "returned_count": len(deduped),
                "status_counts": dict(Counter(row.get("status", "") for row in deduped)),
                "output_identity_sha256": hashlib.sha256(
                    "\n".join(sorted(row["provider_course_id"] for row in deduped)).encode("utf-8")
                ).hexdigest(),
                "owner_identity_disjoint": True,
                "pagination_complete": True,
                "details_complete": details_complete,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
            }
        )
        return deduped, SAMCHEOK_PARSER, meta
    except Exception as exc:
        if "max_pages cap" in _clean(exc) or "detail_limit" in _clean(exc):
            meta["source_cap_reached"] = True
        meta.update(
            {
                "returned_count": 0,
                "pagination_complete": False,
                "details_complete": False,
                "snapshot_complete": False,
                "full_snapshot_validated": False,
                "configured_collection_error": f"{type(exc).__name__}: {_clean(exc)}",
            }
        )
        return [], SAMCHEOK_PARSER, meta
    finally:
        requester.close()


collect_samcheok_education_courses = collect_samcheok_education
collect = collect_samcheok_education

__all__ = [name for name in globals() if name.startswith("SAMCHEOK_")] + [
    "SamcheokContractError",
    "collect",
    "collect_samcheok_education",
    "collect_samcheok_education_courses",
    "dgyouth_program_url",
    "is_samcheok_target",
    "is_target",
    "lifelong_detail_url",
    "lifelong_empty_sentinel_url",
    "lifelong_list_url",
    "owner_for_target",
    "wdyouth_program_url",
    "youth_program_url",
]
