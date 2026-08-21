"""Fail-closed Seoul Public Service reservation collectors.

The official service exposes two independent ledgers used by Mooncen:
``T000`` (education) and ``T200`` (culture/experience).  Each ledger is
reconciled against the service-wide result and all 25 Seoul district filters,
including filters that currently declare zero rows.  Only rows that belong to
exactly one audited Seoul district are emitted.

The collector only POSTs the public search endpoint and GETs the public
information detail endpoint.  It never calls reservation forms, login,
identity-verification, applicant, payment, cancellation, status-history or
personal-information endpoints.  Application controls are inspected in the
detail HTML but are not followed.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
import hashlib
import html
import math
import re
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urlparse

import requests
from bs4 import BeautifulSoup, Tag


SEOUL_PUBLIC_SERVICE_PROVIDER = "SEOUL_PUBLIC_SERVICE"
SEOUL_CITY_CODE = "1100000000"
SEOUL_CITY_NAME = "서울특별시"
SEOUL_PUBLIC_SERVICE_HOST = "yeyak.seoul.go.kr"
SEOUL_PUBLIC_SERVICE_LIST_ENDPOINT = (
    "https://yeyak.seoul.go.kr/web/search/selectPageListDetailSearchImg.do"
)
SEOUL_PUBLIC_SERVICE_DETAIL_ENDPOINT = (
    "https://yeyak.seoul.go.kr/web/reservation/selectReservView.do"
)
SEOUL_PUBLIC_SERVICE_APPLICATION_ENDPOINT = (
    "https://yeyak.seoul.go.kr/web/reservation/insertFormReserve.do"
)
SEOUL_EDUCATION_URL = SEOUL_PUBLIC_SERVICE_LIST_ENDPOINT + "?code=T000"
SEOUL_EXPERIENCE_URL = SEOUL_PUBLIC_SERVICE_LIST_ENDPOINT + "?code=T200"

SEOUL_PAGE_SIZE = 6
SEOUL_DEFAULT_MAX_PAGES = 1_000
SEOUL_DEFAULT_DETAIL_LIMIT = 3_000
SEOUL_MAX_PHYSICAL_REQUESTS = 3_000
SEOUL_MAX_WORKERS = 4
SEOUL_PARTITION_MAX_ATTEMPTS = 3
SEOUL_CENSUS_MAX_ATTEMPTS = 2
SEOUL_PARSER = (
    "seoul_public_service_T000_T200+R403_R402+global_25_districts+"
    "declared_totals_complete_pages+empty_sentinels+stable_first_pages+"
    "public_details+locked_classification+notice_exclusion+no_application_calls"
)
SEOUL_OWNERSHIP_SCOPE = "seoul_public_service_active_and_announced_T000_T200_rows_attributed_to_one_of_25_seoul_district_filters"


@dataclass(frozen=True)
class SeoulCategory:
    code: str
    source_label: str
    canonical_url: str
    program_type: str
    domain_category: str
    service_group: str


@dataclass(frozen=True)
class SeoulDistrict:
    source_code: str
    label: str
    municipality_code: str

    @property
    def municipality_full_name(self) -> str:
        return f"{SEOUL_CITY_NAME} {self.label}"


@dataclass(frozen=True)
class SeoulStatus:
    source_code: str
    source_label: str
    normalized: str


@dataclass(frozen=True)
class PartitionSnapshot:
    status: SeoulStatus
    district: Optional[SeoulDistrict]
    total: int
    pages: int
    rows: tuple[Mapping[str, Any], ...]
    page_counts: Mapping[int, int]

    @property
    def key(self) -> str:
        district = self.district.source_code if self.district else "ALL"
        return f"{self.status.source_code}:{district}"


SEOUL_CATEGORIES: Mapping[str, SeoulCategory] = {
    "T000": SeoulCategory(
        "T000", "교육강좌", SEOUL_EDUCATION_URL, "교육", "교육·강좌", "공공강좌"
    ),
    "T200": SeoulCategory(
        "T200", "문화체험", SEOUL_EXPERIENCE_URL, "체험", "체험·견학", "체험"
    ),
}
SEOUL_STATUSES: tuple[SeoulStatus, ...] = (
    SeoulStatus("R403", "접수중", "OPEN"),
    SeoulStatus("R402", "안내중", "SCHEDULED"),
)
SEOUL_STATUS_BADGES: Mapping[str, frozenset[str]] = {
    # R403 is the site's search-state code.  It includes public programmes
    # whose application method is phone/on-site and whose badge explicitly
    # says that online reservation is unavailable.
    "R403": frozenset({"접수중", "온라인 예약불가"}),
    "R402": frozenset({"안내중"}),
}
SEOUL_DISTRICTS: tuple[SeoulDistrict, ...] = (
    SeoulDistrict("SE01", "강남구", "1168000000"),
    SeoulDistrict("SE02", "강동구", "1174000000"),
    SeoulDistrict("SE03", "강북구", "1130500000"),
    SeoulDistrict("SE04", "강서구", "1150000000"),
    SeoulDistrict("SE05", "관악구", "1162000000"),
    SeoulDistrict("SE06", "광진구", "1121500000"),
    SeoulDistrict("SE07", "구로구", "1153000000"),
    SeoulDistrict("SE08", "금천구", "1154500000"),
    SeoulDistrict("SE09", "노원구", "1135000000"),
    SeoulDistrict("SE10", "도봉구", "1132000000"),
    SeoulDistrict("SE11", "동대문구", "1123000000"),
    SeoulDistrict("SE12", "동작구", "1159000000"),
    SeoulDistrict("SE13", "마포구", "1144000000"),
    SeoulDistrict("SE14", "서대문구", "1141000000"),
    SeoulDistrict("SE15", "서초구", "1165000000"),
    SeoulDistrict("SE16", "성동구", "1120000000"),
    SeoulDistrict("SE17", "성북구", "1129000000"),
    SeoulDistrict("SE18", "송파구", "1171000000"),
    SeoulDistrict("SE19", "양천구", "1147000000"),
    SeoulDistrict("SE20", "영등포구", "1156000000"),
    SeoulDistrict("SE21", "용산구", "1117000000"),
    SeoulDistrict("SE22", "은평구", "1138000000"),
    SeoulDistrict("SE23", "종로구", "1111000000"),
    SeoulDistrict("SE24", "중구", "1114000000"),
    SeoulDistrict("SE25", "중랑구", "1126000000"),
)

SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_TOTAL_RE = re.compile(r"[\d,]+")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)"
)
_SERVICE_ID_RE = re.compile(r"S\d{18}")
_DETAIL_CALL_RE = re.compile(r"fnDetailPage\(\s*['\"](S\d{18})['\"]")
_EXTERNAL_CALL_RE = re.compile(r"fnNewPop\(\s*['\"](https?://[^'\"]+)['\"]")
_NOTICE_PREFIXES = ("(공지", "[공지", "공지사항", "알림사항", "안내사항")
_TEST_MARKERS = ("테스트", "신청하지 마세요", "예약하지 마세요")
_MAP_BUTTON_SUFFIX_RE = re.compile(r"\s*지도보기\s*$")
_AREA_SUFFIX_RE = re.compile(r"\s*\(\s*면적\s*:\s*[^)]*\)\s*$")
_METHOD_SEPARATOR_RE = re.compile(r"[\s,/+·ㆍ]+")


class SeoulPublicServiceContractError(ValueError):
    """Raised when an audited public contract is no longer exact."""


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(
        " ", html.unescape(str(value or "")).replace("\xa0", " ")
    ).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[\W_]+", "", _clean(value).casefold(), flags=re.UNICODE)


def _target_value(target: Any, key: str) -> Any:
    return (
        target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)
    )


def _category_for_target(target: Any) -> Optional[SeoulCategory]:
    if _clean(_target_value(target, "provider")) != SEOUL_PUBLIC_SERVICE_PROVIDER:
        return None
    raw_url = _clean(_target_value(target, "url"))
    parsed = urlparse(raw_url)
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").rstrip(".").lower() == SEOUL_PUBLIC_SERVICE_HOST
        and parsed.port is None
        and parsed.path == urlparse(SEOUL_PUBLIC_SERVICE_LIST_ENDPOINT).path
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    ):
        return None
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if len(pairs) != 1 or pairs[0][0] != "code":
        return None
    category = SEOUL_CATEGORIES.get(pairs[0][1])
    return category if category and raw_url == category.canonical_url else None


def is_seoul_public_service_target(target: Any) -> bool:
    return _category_for_target(target) is not None


is_target = is_seoul_public_service_target


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/138.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
    )
    return current


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


class _SessionLease:
    def __init__(self, session: Any) -> None:
        self.session = session

    def __getattr__(self, name: str) -> Any:
        return getattr(self.session, name)

    def close(self) -> None:
        return None


class _ThreadSessionPool:
    """Keep one managed session per crawl worker and close all at the end."""

    def __init__(self, factory: SessionFactory) -> None:
        self._factory = factory
        self._local = threading.local()
        self._lock = threading.Lock()
        self._sessions: list[Any] = []
        self._closed = False

    def __call__(self) -> _SessionLease:
        lease = getattr(self._local, "lease", None)
        if lease is not None:
            return lease
        session = self._factory()
        headers = getattr(session, "headers", None)
        if headers is not None and hasattr(headers, "update"):
            headers.update(
                {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/138.0 Safari/537.36"
                    ),
                    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
                }
            )
        lease = _SessionLease(session)
        with self._lock:
            if self._closed:
                _close_quietly(session)
                raise SeoulPublicServiceContractError("session pool is closed")
            self._sessions.append(session)
        self._local.lease = lease
        return lease

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            sessions = list(self._sessions)
            self._sessions.clear()
        for session in sessions:
            _close_quietly(session)


class _RequestBudget:
    def __init__(self, max_list: int, max_detail: int) -> None:
        self.max_list = max_list
        self.max_detail = max_detail
        self._lock = threading.Lock()
        self.list_requests = 0
        self.detail_requests = 0
        self.physical_requests = 0

    def claim(self, kind: str) -> None:
        with self._lock:
            if self.physical_requests >= SEOUL_MAX_PHYSICAL_REQUESTS:
                raise SeoulPublicServiceContractError(
                    "physical request safety cap exceeded"
                )
            if kind == "list":
                if self.list_requests >= self.max_list:
                    raise SeoulPublicServiceContractError(
                        "max_pages cannot cover the required list census"
                    )
                self.list_requests += 1
            elif kind == "detail":
                if self.detail_requests >= self.max_detail:
                    raise SeoulPublicServiceContractError(
                        "detail_limit cannot cover every required public detail"
                    )
                self.detail_requests += 1
            else:
                raise SeoulPublicServiceContractError("unknown request budget kind")
            self.physical_requests += 1


def _response_soup(response: Any, *, expected_path: str) -> BeautifulSoup:
    try:
        status = int(getattr(response, "status_code", 0))
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise SeoulPublicServiceContractError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise SeoulPublicServiceContractError("redirects are not accepted")
    final = _clean(getattr(response, "url", ""))
    if final:
        parsed = urlparse(final)
        if not (
            parsed.scheme == "https"
            and (parsed.hostname or "").rstrip(".").lower() == SEOUL_PUBLIC_SERVICE_HOST
            and parsed.path == expected_path
        ):
            raise SeoulPublicServiceContractError(
                "response escaped the audited public endpoint"
            )
    headers = getattr(response, "headers", {})
    content_type = _clean(
        headers.get("Content-Type") if isinstance(headers, Mapping) else ""
    ).lower()
    if content_type and "text/html" not in content_type:
        raise SeoulPublicServiceContractError(
            f"unexpected public response content type {content_type!r}"
        )
    text = str(getattr(response, "text", "") or "")
    if not text and getattr(response, "content", None):
        text = bytes(response.content).decode("utf-8", errors="replace")
    if not text:
        raise SeoulPublicServiceContractError("empty public response")
    soup = BeautifulSoup(text, "lxml")
    page_title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if any(marker in page_title for marker in ("로그인", "오류", "안내메시지")):
        raise SeoulPublicServiceContractError(
            "public request resolved to a guarded page"
        )
    return soup


class _Runner:
    def __init__(
        self,
        factory: SessionFactory,
        budget: _RequestBudget,
        timeout: int,
    ) -> None:
        self.factory = factory
        self.budget = budget
        self.timeout = timeout

    def list_soup(
        self,
        category: SeoulCategory,
        status: SeoulStatus,
        district: Optional[SeoulDistrict],
        page: int,
    ) -> BeautifulSoup:
        if page < 1:
            raise SeoulPublicServiceContractError("invalid list page")
        payload = {
            "code": category.code,
            "sch_svc_sttus": status.source_code,
            "currentPage": str(page),
        }
        if district is not None:
            payload["sch_pl"] = district.source_code
        self.budget.claim("list")
        response = self.factory().post(
            SEOUL_PUBLIC_SERVICE_LIST_ENDPOINT,
            data=payload,
            timeout=self.timeout,
            verify=True,
            allow_redirects=False,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "Referer": category.canonical_url,
            },
        )
        return _response_soup(
            response,
            expected_path=urlparse(SEOUL_PUBLIC_SERVICE_LIST_ENDPOINT).path,
        )

    def detail_soup(self, service_id: str, referer: str) -> BeautifulSoup:
        if not _SERVICE_ID_RE.fullmatch(service_id):
            raise SeoulPublicServiceContractError("unsafe public service identity")
        url = (
            SEOUL_PUBLIC_SERVICE_DETAIL_ENDPOINT
            + "?"
            + urlencode({"rsv_svc_id": service_id})
        )
        parsed = urlparse(url)
        if parsed.path != "/web/reservation/selectReservView.do" or dict(
            parse_qsl(parsed.query)
        ) != {"rsv_svc_id": service_id}:
            raise SeoulPublicServiceContractError("unsafe detail URL")
        self.budget.claim("detail")
        response = self.factory().get(
            url,
            timeout=self.timeout,
            verify=True,
            allow_redirects=False,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "Referer": referer,
            },
        )
        return _response_soup(
            response,
            expected_path=urlparse(SEOUL_PUBLIC_SERVICE_DETAIL_ENDPOINT).path,
        )


def _date_range(value: Any, label: str) -> tuple[str, str]:
    values = [
        f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
        for year, month, day in _DATE_RE.findall(_clean(value))
    ]
    if len(values) < 2:
        raise SeoulPublicServiceContractError(f"{label} lacks a complete date range")
    for raw in (values[0], values[1]):
        date.fromisoformat(raw)
    # Seoul sometimes appends a parenthesized priority-booking sub-period.
    # The first two dates are the labelled field's primary range; later dates
    # belong to that explanatory sub-period and must not replace its end date.
    return values[0], values[1]


def _text_without_label(node: Tag, selector: str) -> str:
    clone = BeautifulSoup(str(node), "lxml")
    root = clone.find(node.name)
    if root is None:
        return ""
    label = root.select_one(selector)
    if label is not None:
        label.decompose()
    return _clean(root.get_text(" ", strip=True))


def _venue_text(value: Any) -> str:
    """Remove exact Seoul UI metadata suffixes without touching place names."""

    cleaned = _MAP_BUTTON_SUFFIX_RE.sub("", _clean(value))
    return _clean(_AREA_SUFFIX_RE.sub("", cleaned))


def _is_offline_reservation_method(value: Any) -> bool:
    compact = _METHOD_SEPARATOR_RE.sub("", _clean(value))
    return compact in {"전화", "현장방문", "전화현장방문"}


def _district_controls(soup: BeautifulSoup) -> None:
    controls: dict[str, str] = {}
    for node in soup.select('input[name="sch_pl"]'):
        code = _clean(node.get("value"))
        onclick = _clean(node.get("onclick"))
        label_match = re.search(r"fnChoose\(\s*this\s*,\s*['\"]([^'\"]+)", onclick)
        if code and label_match:
            controls[code] = _clean(label_match.group(1))
    missing = [
        district.source_code
        for district in SEOUL_DISTRICTS
        if controls.get(district.source_code) != district.label
    ]
    if missing:
        raise SeoulPublicServiceContractError(
            "official 25-district selector contract changed: " + ",".join(missing)
        )


def _source_total(soup: BeautifulSoup) -> int:
    markers = soup.select("div.title_dep1 > span.text_red")
    if len(markers) != 1:
        raise SeoulPublicServiceContractError("missing unique official source total")
    raw = _clean(markers[0].get_text(" ", strip=True))
    if not _TOTAL_RE.fullmatch(raw):
        raise SeoulPublicServiceContractError("invalid official source total")
    return int(raw.replace(",", ""))


def _non_program_reason(title: str) -> str:
    cleaned = _clean(title)
    if cleaned.startswith(_NOTICE_PREFIXES):
        return "notice"
    if any(marker in cleaned for marker in _TEST_MARKERS):
        return "test"
    return ""


def _list_row(
    item: Tag,
    *,
    category: SeoulCategory,
    status: SeoulStatus,
    district: Optional[SeoulDistrict],
    page: int,
) -> dict[str, Any]:
    anchors = item.select("a[onclick]")
    if len(anchors) != 1:
        raise SeoulPublicServiceContractError("list card lacks one official action")
    anchor = anchors[0]
    onclick = _clean(anchor.get("onclick"))
    detail_match = _DETAIL_CALL_RE.search(onclick)
    external_match = _EXTERNAL_CALL_RE.search(onclick)
    if bool(detail_match) == bool(external_match):
        raise SeoulPublicServiceContractError(
            "list card must be exactly internal-detail or external-only"
        )
    title_node = item.select_one("h4.tit1")
    title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
    if not title:
        raise SeoulPublicServiceContractError("list card lacks title")
    category_labels = tuple(
        _clean(node.get_text(" ", strip=True))
        for node in item.select("ul.ib_type > li")
        if _clean(node.get_text(" ", strip=True))
    )
    if not category_labels or category_labels[0] != category.source_label:
        raise SeoulPublicServiceContractError("list category escaped the locked ledger")
    badge = item.select_one("span.bd_label")
    status_label = _clean(badge.get_text(" ", strip=True) if badge else "")
    if status_label not in SEOUL_STATUS_BADGES[status.source_code]:
        raise SeoulPublicServiceContractError("list status/filter mismatch")
    apply_node = item.select_one("ul.ib_attr > li:has(b.date1)")
    use_node = item.select_one("ul.ib_attr > li:has(b.date2)")
    if apply_node is None or use_node is None:
        raise SeoulPublicServiceContractError("list card lacks official periods")
    apply_range = _date_range(_text_without_label(apply_node, "b.date1"), "application")
    use_range = _date_range(_text_without_label(use_node, "b.date2"), "use")
    place_node = item.select_one("ul.ib_attr > li:has(b.place)")
    place = (
        _venue_text(_text_without_label(place_node, "b.place")) if place_node else ""
    )
    method_nodes = item.select("span.bd_ico")
    methods = tuple(
        dict.fromkeys(
            _clean(node.get_text(" ", strip=True))
            for node in method_nodes
            if _clean(node.get_text(" ", strip=True))
        )
    )
    if not methods:
        raise SeoulPublicServiceContractError("list card lacks reservation method")

    external_url = _clean(external_match.group(1)) if external_match else ""
    service_id = _clean(detail_match.group(1)) if detail_match else ""
    if external_url:
        parsed_external = urlparse(external_url)
        if not (
            parsed_external.scheme in {"http", "https"}
            and parsed_external.hostname
            and not parsed_external.username
            and not parsed_external.password
        ):
            raise SeoulPublicServiceContractError("unsafe external information URL")
        digest = hashlib.sha256(f"{external_url}\n{title}".encode("utf-8")).hexdigest()[
            :20
        ]
        identity = f"external:{digest}"
    else:
        identity = service_id
    detail_url = (
        SEOUL_PUBLIC_SERVICE_DETAIL_ENDPOINT
        + "?"
        + urlencode({"rsv_svc_id": service_id})
        if service_id
        else ""
    )
    return {
        "identity": identity,
        "service_id": service_id,
        "external_url": external_url,
        "is_external_only": bool(external_url),
        "title": title,
        "place": place,
        "methods": methods,
        "source_subcategories": category_labels[1:],
        "status_code": status.source_code,
        "status_label": status_label,
        "status": status.normalized,
        "application_start": apply_range[0],
        "application_end": apply_range[1],
        "start_date": use_range[0],
        "end_date": use_range[1],
        "detail_url": detail_url,
        "partition_district_code": district.source_code if district else "ALL",
        "page": page,
        "non_program_reason": _non_program_reason(title),
    }


def _parse_page(
    soup: BeautifulSoup,
    *,
    category: SeoulCategory,
    status: SeoulStatus,
    district: Optional[SeoulDistrict],
    page: int,
    check_controls: bool,
) -> tuple[int, list[dict[str, Any]]]:
    if check_controls:
        _district_controls(soup)
    code_nodes = soup.select('input[name="code"]')
    if len(code_nodes) != 1 or _clean(code_nodes[0].get("value")) != category.code:
        raise SeoulPublicServiceContractError("list response category contract changed")
    total = _source_total(soup)
    current_nodes = soup.select("input#currentPage")
    if len(current_nodes) > 1 or (
        current_nodes and _clean(current_nodes[0].get("value")) != str(page)
    ):
        raise SeoulPublicServiceContractError("list response page contract changed")
    boards = soup.select("ul.img_board")
    if len(boards) > 1:
        raise SeoulPublicServiceContractError("list response board contract changed")
    if total > 0 and not current_nodes:
        raise SeoulPublicServiceContractError("list response page contract changed")
    rows = [
        _list_row(
            item,
            category=category,
            status=status,
            district=district,
            page=page,
        )
        for item in (boards[0].select(":scope > li") if boards else ())
    ]
    return total, rows


def _row_signature(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        _clean(row.get("identity")),
        _normalized(row.get("title")),
        _normalized(row.get("place")),
        "|".join(_clean(value) for value in row.get("methods", ())),
        "|".join(_clean(value) for value in row.get("source_subcategories", ())),
        str(bool(row.get("is_external_only"))),
        _clean(row.get("application_start")),
        _clean(row.get("application_end")),
        _clean(row.get("start_date")),
        _clean(row.get("end_date")),
        _clean(row.get("status_code")),
        _clean(row.get("status_label")),
    )


def _collect_partition_once(
    runner: _Runner,
    category: SeoulCategory,
    status: SeoulStatus,
    district: Optional[SeoulDistrict],
) -> PartitionSnapshot:
    first = runner.list_soup(category, status, district, 1)
    total, first_rows = _parse_page(
        first,
        category=category,
        status=status,
        district=district,
        page=1,
        check_controls=True,
    )
    pages = max(1, math.ceil(total / SEOUL_PAGE_SIZE))
    page_rows: dict[int, list[dict[str, Any]]] = {1: first_rows}
    for page in range(2, pages + 1):
        page_total, rows = _parse_page(
            runner.list_soup(category, status, district, page),
            category=category,
            status=status,
            district=district,
            page=page,
            check_controls=False,
        )
        if page_total != total:
            raise SeoulPublicServiceContractError(
                "partition total changed across pages"
            )
        page_rows[page] = rows
    for page, rows in page_rows.items():
        expected = min(SEOUL_PAGE_SIZE, max(0, total - (page - 1) * SEOUL_PAGE_SIZE))
        if len(rows) != expected:
            raise SeoulPublicServiceContractError(
                f"partition page {page} expected {expected} rows, got {len(rows)}"
            )
    flattened = [row for page in range(1, pages + 1) for row in page_rows[page]]
    if len(flattened) != total:
        raise SeoulPublicServiceContractError(
            "declared partition total did not reconcile"
        )
    identities = [_clean(row["identity"]) for row in flattened]
    if len(identities) != len(set(identities)):
        raise SeoulPublicServiceContractError(
            "duplicate identities within one partition"
        )

    sentinel_page = pages + 1
    sentinel_total, sentinel_rows = _parse_page(
        runner.list_soup(category, status, district, sentinel_page),
        category=category,
        status=status,
        district=district,
        page=sentinel_page,
        check_controls=False,
    )
    if sentinel_total != total or sentinel_rows:
        raise SeoulPublicServiceContractError("immediate list sentinel is not empty")
    verify_total, verify_rows = _parse_page(
        runner.list_soup(category, status, district, 1),
        category=category,
        status=status,
        district=district,
        page=1,
        check_controls=True,
    )
    if verify_total != total or tuple(map(_row_signature, verify_rows)) != tuple(
        map(_row_signature, first_rows)
    ):
        raise SeoulPublicServiceContractError(
            "partition first page changed during census"
        )
    return PartitionSnapshot(
        status=status,
        district=district,
        total=total,
        pages=pages,
        rows=tuple(flattened),
        page_counts={page: len(rows) for page, rows in page_rows.items()},
    )


_RETRYABLE_PARTITION_DRIFT_MARKERS = (
    "partition total changed across pages",
    "partition page ",
    "declared partition total did not reconcile",
    "duplicate identities within one partition",
    "immediate list sentinel is not empty",
    "partition first page changed during census",
)


def _collect_partition(
    runner: _Runner,
    category: SeoulCategory,
    status: SeoulStatus,
    district: Optional[SeoulDistrict],
) -> PartitionSnapshot:
    scope = district.source_code if district else "ALL"
    for attempt in range(1, SEOUL_PARTITION_MAX_ATTEMPTS + 1):
        try:
            return _collect_partition_once(runner, category, status, district)
        except SeoulPublicServiceContractError as exc:
            message = _clean(exc)
            retryable = any(
                marker in message for marker in _RETRYABLE_PARTITION_DRIFT_MARKERS
            )
            if not retryable or attempt == SEOUL_PARTITION_MAX_ATTEMPTS:
                raise SeoulPublicServiceContractError(
                    f"{category.code}/{status.source_code}/{scope}: {message}"
                ) from exc
            time.sleep(0.2 * attempt)
    raise AssertionError("unreachable partition retry state")


def _detail_fields(soup: BeautifulSoup) -> dict[str, str]:
    fields: dict[str, str] = {}
    for node in soup.select("ul.dt_top_list > li"):
        label = node.select_one("b.tit1")
        if label is None:
            continue
        key = _clean(label.get_text(" ", strip=True))
        value = _text_without_label(node, "b.tit1")
        fields[key] = _venue_text(value) if key == "장소" else value
    return fields


def _detail_row(
    listed: Mapping[str, Any],
    soup: BeautifulSoup,
    *,
    category: SeoulCategory,
    district: SeoulDistrict,
) -> dict[str, Any]:
    service_id = _clean(listed.get("service_id"))
    hidden_ids = soup.select('input[name="rsv_svc_id"]')
    hidden_codes = soup.select('input[name="code"]')
    if (
        len(hidden_ids) != 1
        or _clean(hidden_ids[0].get("value")) != service_id
        or len(hidden_codes) != 1
        or _clean(hidden_codes[0].get("value")) != category.code
    ):
        raise SeoulPublicServiceContractError(
            f"{service_id}: detail identity/category mismatch"
        )
    title_node = soup.select_one("div.dt_top_box h4.dt_tit1 span.tit")
    detail_title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
    if not detail_title or _normalized(detail_title) != _normalized(
        listed.get("title")
    ):
        raise SeoulPublicServiceContractError(
            f"{service_id}: detail/list title mismatch"
        )
    status_node = soup.select_one("div.dt_top_box span.bd_label") or soup.select_one(
        "span.bd_label"
    )
    detail_status = _clean(status_node.get_text(" ", strip=True) if status_node else "")
    if detail_status != _clean(listed.get("status_label")):
        raise SeoulPublicServiceContractError(
            f"{service_id}: detail/list status mismatch"
        )
    fields = _detail_fields(soup)
    required = {"장소", "이용기간", "접수기간", "예약방법"}
    if not required <= set(fields):
        raise SeoulPublicServiceContractError(
            f"{service_id}: detail safe-field contract changed"
        )
    detail_apply = _date_range(fields["접수기간"], "detail application")
    detail_use = _date_range(fields["이용기간"], "detail use")
    if detail_apply != (
        _clean(listed.get("application_start")),
        _clean(listed.get("application_end")),
    ):
        raise SeoulPublicServiceContractError(
            f"{service_id}: detail/list application period mismatch"
        )
    if detail_use != (
        _clean(listed.get("start_date")),
        _clean(listed.get("end_date")),
    ):
        raise SeoulPublicServiceContractError(
            f"{service_id}: detail/list use period mismatch"
        )
    method = _clean(fields["예약방법"])
    status_label = _clean(listed.get("status_label"))
    online_unavailable = status_label == "온라인 예약불가"
    source = str(soup)
    application_route_declared = (
        "fnRevervInsertForm" in source and "insertFormReserve.do" in source
    )
    application_controls = soup.select('a[href="javascript:fnRevervInsertForm();"]')
    if "인터넷" in method and not application_route_declared:
        raise SeoulPublicServiceContractError(
            f"{service_id}: public application control contract changed"
        )
    if online_unavailable and (
        not _is_offline_reservation_method(method) or application_controls
    ):
        raise SeoulPublicServiceContractError(
            f"{service_id}: online-unavailable row exposes an online application"
        )
    if (
        _clean(listed.get("status")) == "OPEN"
        and not online_unavailable
        and "인터넷" in method
        and len(application_controls) != 1
    ):
        raise SeoulPublicServiceContractError(
            f"{service_id}: open online row lacks one public application control"
        )
    place = _clean(fields["장소"])
    status = _clean(listed.get("status"))
    online = "인터넷" in method
    application_available = (
        status == "OPEN"
        and not online_unavailable
        and ((online and len(application_controls) == 1) or not online)
    )
    detail_url = _clean(listed.get("detail_url"))
    return {
        "provider": SEOUL_PUBLIC_SERVICE_PROVIDER,
        "provider_course_id": (
            f"{SEOUL_PUBLIC_SERVICE_PROVIDER}:{category.code}:{service_id}"
        ),
        "title": detail_title,
        "description": detail_title,
        "branch": place or _clean(listed.get("place")) or SEOUL_CITY_NAME,
        "preserve_branch": True,
        "raw_url": detail_url,
        "source_url": category.canonical_url,
        # The public detail is a safe information URL.  The actual reservation
        # form endpoint is deliberately neither requested nor persisted.
        "application_url": detail_url if application_available else "",
        "application_type": (
            "ONLINE_RESERVATION"
            if application_available and online
            else ("PHONE_OR_VISIT" if application_available else "INFO_ONLY")
        ),
        "reservation_available": application_available,
        "status": status,
        "course_status": status,
        "registration_start_date": _clean(listed.get("application_start")),
        "registration_end_date": _clean(listed.get("application_end")),
        "start_date": _clean(listed.get("start_date")),
        "end_date": _clean(listed.get("end_date")),
        "venue_name": place or _clean(listed.get("place")),
        "region": district.municipality_full_name,
        "municipality_code": district.municipality_code,
        "municipality_full_name": district.municipality_full_name,
        "municipality_region_verified": True,
        "collection_category": "공공예약",
        "domain_category": category.domain_category,
        "source_group": "municipal_reservation",
        "service_group": category.service_group,
        "service_group_policy": "locked",
        "category": category.program_type,
        "program_type": category.program_type,
        "program_type_source": f"official_menu_{category.code}",
        "classification_locked": True,
        "operator_type": "지자체/공공기관",
        "facility_type": "공공기관",
        "collection_type": SEOUL_PARSER,
        "raw_fields": {
            "identity": service_id,
            "official_category_code": category.code,
            "official_category_label": category.source_label,
            "official_subcategory_labels": list(
                listed.get("source_subcategories") or ()
            ),
            "official_status_code": _clean(listed.get("status_code")),
            "official_status_label": detail_status,
            "official_reservation_method": method,
            "official_district_filter_code": district.source_code,
            "official_district_filter_name": district.label,
            "municipality_evidence": {
                "source": "official_sch_pl_partition",
                "code": district.municipality_code,
                "full_name": district.municipality_full_name,
            },
            "application_control_present": bool(application_controls),
            "application_route_declared_but_not_called": application_route_declared,
            "source_contact_omitted": True,
            "source_target_audience_omitted": True,
            "source_free_text_omitted": True,
        },
    }


def _fetch_detail_row(
    runner: _Runner,
    listed: Mapping[str, Any],
    *,
    category: SeoulCategory,
    district: SeoulDistrict,
) -> dict[str, Any]:
    """Fetch and parse one information detail inside its worker thread."""

    soup = runner.detail_soup(_clean(listed.get("service_id")), category.canonical_url)
    return _detail_row(listed, soup, category=category, district=district)


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _district_zero_map() -> dict[str, int]:
    return {district.municipality_code: 0 for district in SEOUL_DISTRICTS}


def _base_meta(
    error: str = "", category: Optional[SeoulCategory] = None
) -> dict[str, Any]:
    return {
        "source_total": 0,
        "source_rows": 0,
        "global_totals": {status.source_code: 0 for status in SEOUL_STATUSES},
        "district_totals": {
            district.municipality_code: {
                "source_code": district.source_code,
                "name": district.municipality_full_name,
                **{status.source_code: 0 for status in SEOUL_STATUSES},
                "total": 0,
            }
            for district in SEOUL_DISTRICTS
        },
        "district_returned_counts": _district_zero_map(),
        "district_provider_counts": {
            district.municipality_code: 0 for district in SEOUL_DISTRICTS
        },
        "partition_pages": {},
        "page_counts": {},
        "pages": 0,
        "required_list_requests": 0,
        "list_requests": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "physical_requests": 0,
        "external_no_internal_detail_count": 0,
        "unattributed_or_outside_seoul_count": 0,
        "explicit_non_program_count": 0,
        "notice_count": 0,
        "test_count": 0,
        "returned_count": 0,
        "district_count": len(SEOUL_DISTRICTS),
        "district_zero_source_count": len(SEOUL_DISTRICTS),
        "district_zero_returned_count": len(SEOUL_DISTRICTS),
        "sentinel_requests": 0,
        "stability_rechecks": 0,
        "pagination_complete": False,
        "district_reconciliation_complete": False,
        "stable_recheck_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "application_endpoints_called": 0,
        "pii_fields_stored": 0,
        "configured_collection_error": error,
        "errors": [error] if error else [],
        "parser": SEOUL_PARSER,
        "ownership_scope": SEOUL_OWNERSHIP_SCOPE,
        "canonical_provider": SEOUL_PUBLIC_SERVICE_PROVIDER,
        "canonical_url": category.canonical_url if category else "",
        "category_code": category.code if category else "",
        "category_label": category.source_label if category else "",
        "municipality_code": SEOUL_CITY_CODE,
        "covered_municipality_codes": [
            district.municipality_code for district in SEOUL_DISTRICTS
        ],
        "excluded_scope": (
            "external_only_rows_without_internal_detail; rows_not_attributed_to_"
            "exactly_one_SE01_SE25_partition; notices; tests; application_forms; "
            "login; applicants; contacts; attachments; free_text"
        ),
    }


def _collect_seoul_public_service_courses_once(
    target: Any,
    timeout: int = 20,
    max_pages: int = SEOUL_DEFAULT_MAX_PAGES,
    detail_limit: int = SEOUL_DEFAULT_DETAIL_LIMIT,
    *,
    max_workers: int = SEOUL_MAX_WORKERS,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete T000 or T200 Seoul snapshot.

    ``max_pages`` is the total list-request cap across both statuses, the
    global partition and all 25 district partitions.  It includes every
    immediate empty sentinel and every stable-first-page recheck.
    ``detail_limit`` is a fail-closed cap, never a sampling limit.
    """

    category = _category_for_target(target)
    if category is None:
        return (
            [],
            SEOUL_PARSER,
            _base_meta("target does not match an audited Seoul public-service ledger"),
        )
    if (
        not isinstance(timeout, int)
        or isinstance(timeout, bool)
        or not 1 <= timeout <= 120
        or not isinstance(max_pages, int)
        or isinstance(max_pages, bool)
        or max_pages < 1
        or not isinstance(detail_limit, int)
        or isinstance(detail_limit, bool)
        or detail_limit < 0
        or not isinstance(max_workers, int)
        or isinstance(max_workers, bool)
        or not 1 <= max_workers <= 8
    ):
        return (
            [],
            SEOUL_PARSER,
            _base_meta(
                "invalid timeout, max_pages, detail_limit, or max_workers", category
            ),
        )
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            return (
                [],
                SEOUL_PARSER,
                _base_meta("managed session_factory injection is required", category),
            )
        session_factory = _default_session_factory

    budget = _RequestBudget(max_pages, detail_limit)
    session_pool = _ThreadSessionPool(session_factory)
    runner = _Runner(session_pool, budget, timeout)
    meta = _base_meta(category=category)
    partition_snapshots: dict[str, PartitionSnapshot] = {}
    source_cap_reached = False
    try:
        partition_specs = [
            (status, district)
            for status in SEOUL_STATUSES
            for district in (None, *SEOUL_DISTRICTS)
        ]
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    _collect_partition, runner, category, status, district
                ): (status, district)
                for status, district in partition_specs
            }
            for future in as_completed(futures):
                snapshot = future.result()
                partition_snapshots[snapshot.key] = snapshot
        if len(partition_snapshots) != len(partition_specs):
            raise SeoulPublicServiceContractError("not every partition completed")

        global_rows_by_status: dict[str, tuple[Mapping[str, Any], ...]] = {}
        district_memberships: dict[tuple[str, str], list[SeoulDistrict]] = defaultdict(
            list
        )
        for status in SEOUL_STATUSES:
            global_snapshot = partition_snapshots[f"{status.source_code}:ALL"]
            global_rows_by_status[status.source_code] = global_snapshot.rows
            meta["global_totals"][status.source_code] = global_snapshot.total
            global_identities = {
                _clean(row["identity"]): row for row in global_snapshot.rows
            }
            if len(global_identities) != len(global_snapshot.rows):
                raise SeoulPublicServiceContractError(
                    "duplicate global identities within status"
                )
            for district in SEOUL_DISTRICTS:
                snapshot = partition_snapshots[
                    f"{status.source_code}:{district.source_code}"
                ]
                district_meta = meta["district_totals"][district.municipality_code]
                district_meta[status.source_code] = snapshot.total
                district_meta["total"] += snapshot.total
                for row in snapshot.rows:
                    identity = _clean(row["identity"])
                    if identity not in global_identities:
                        raise SeoulPublicServiceContractError(
                            "district identity is absent from its global partition"
                        )
                    if _row_signature(row) != _row_signature(
                        global_identities[identity]
                    ):
                        raise SeoulPublicServiceContractError(
                            "district/global row contract did not reconcile"
                        )
                    if not row["is_external_only"]:
                        district_memberships[(status.source_code, identity)].append(
                            district
                        )

        all_global_rows = [
            row
            for status in SEOUL_STATUSES
            for row in global_rows_by_status[status.source_code]
        ]
        all_global_identities = [_clean(row["identity"]) for row in all_global_rows]
        if len(all_global_identities) != len(set(all_global_identities)):
            raise SeoulPublicServiceContractError(
                "identity crossed active status partitions during census"
            )
        meta["source_total"] = sum(meta["global_totals"].values())
        meta["source_rows"] = len(all_global_rows)

        candidates: list[tuple[Mapping[str, Any], SeoulDistrict]] = []
        external_count = 0
        outside_count = 0
        explicit_non_program: list[Mapping[str, Any]] = []
        for listed in all_global_rows:
            if listed["is_external_only"]:
                external_count += 1
                continue
            reason = _clean(listed.get("non_program_reason"))
            if reason:
                explicit_non_program.append(listed)
                continue
            memberships = district_memberships[
                (_clean(listed["status_code"]), _clean(listed["identity"]))
            ]
            unique_memberships = {
                district.source_code: district for district in memberships
            }
            if len(unique_memberships) > 1:
                raise SeoulPublicServiceContractError(
                    "one internal service belongs to multiple Seoul district filters"
                )
            if not unique_memberships:
                outside_count += 1
                continue
            candidates.append((listed, next(iter(unique_memberships.values()))))
        if len(candidates) > detail_limit:
            source_cap_reached = True
            raise SeoulPublicServiceContractError(
                f"detail_limit allows {detail_limit} of {len(candidates)} required details"
            )

        detailed: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            detail_futures = {
                executor.submit(
                    _fetch_detail_row,
                    runner,
                    listed,
                    category=category,
                    district=district,
                ): (_clean(listed["service_id"]), district)
                for listed, district in candidates
            }
            for future in as_completed(detail_futures):
                detailed.append(future.result())
        detailed.sort(key=lambda row: _clean(row.get("provider_course_id")))
        result = list((dedupe_rows or _dedupe_default)(detailed))
        if len(result) != len(detailed):
            raise SeoulPublicServiceContractError(
                "dedupe changed a complete official snapshot"
            )

        partition_pages = {
            key: snapshot.pages for key, snapshot in sorted(partition_snapshots.items())
        }
        page_counts = {
            f"{key}:{page}": count
            for key, snapshot in sorted(partition_snapshots.items())
            for page, count in sorted(snapshot.page_counts.items())
        }
        returned_counts = Counter(
            _clean(row.get("municipality_code")) for row in result
        )
        for district in SEOUL_DISTRICTS:
            code = district.municipality_code
            count = returned_counts.get(code, 0)
            meta["district_returned_counts"][code] = count
            # Provider coverage is retained even when this complete snapshot
            # currently contains zero rows for the district.
            meta["district_provider_counts"][code] = 1
        meta.update(
            {
                "partition_pages": partition_pages,
                "page_counts": page_counts,
                "pages": sum(
                    snapshot.pages + 1 for snapshot in partition_snapshots.values()
                ),
                "required_list_requests": budget.list_requests,
                "list_requests": budget.list_requests,
                "detail_attempts": len(candidates),
                "detail_pages": len(detailed),
                "physical_requests": budget.physical_requests,
                "external_no_internal_detail_count": external_count,
                "unattributed_or_outside_seoul_count": outside_count,
                "explicit_non_program_count": len(explicit_non_program),
                "notice_count": sum(
                    row.get("non_program_reason") == "notice"
                    for row in explicit_non_program
                ),
                "test_count": sum(
                    row.get("non_program_reason") == "test"
                    for row in explicit_non_program
                ),
                "returned_count": len(result),
                "district_zero_source_count": sum(
                    int(value["total"]) == 0
                    for value in meta["district_totals"].values()
                ),
                "district_zero_returned_count": sum(
                    value == 0 for value in meta["district_returned_counts"].values()
                ),
                "sentinel_requests": len(partition_snapshots),
                "stability_rechecks": len(partition_snapshots),
                "status_counts": dict(Counter(row["status"] for row in result)),
                "pagination_complete": True,
                "district_reconciliation_complete": True,
                "stable_recheck_complete": True,
                "details_complete": len(detailed) == len(candidates),
                "snapshot_complete": True,
                "full_snapshot_validated": True,
                "no_current_data": not result,
                "configured_collection_error": "",
                "errors": [],
            }
        )
        return result, SEOUL_PARSER, meta
    except Exception as exc:
        message = f"{type(exc).__name__}: {_clean(exc)}"
        if "cap" in message or "max_pages" in message or "detail_limit" in message:
            source_cap_reached = True
        meta.update(
            {
                "partition_pages": {
                    key: snapshot.pages
                    for key, snapshot in sorted(partition_snapshots.items())
                },
                "pages": sum(
                    snapshot.pages + 1 for snapshot in partition_snapshots.values()
                ),
                "required_list_requests": budget.list_requests,
                "list_requests": budget.list_requests,
                "detail_attempts": budget.detail_requests,
                "detail_pages": 0,
                "physical_requests": budget.physical_requests,
                "source_cap_reached": source_cap_reached,
                "configured_collection_error": message,
                "errors": [message],
            }
        )
        return [], SEOUL_PARSER, meta
    finally:
        session_pool.close()


_RETRYABLE_CENSUS_DRIFT_MARKERS = (
    "district identity is absent from its global partition",
    "district/global row contract did not reconcile",
    "identity crossed active status partitions during census",
    "one internal service belongs to multiple Seoul district filters",
)


def collect_seoul_public_service_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = SEOUL_DEFAULT_MAX_PAGES,
    detail_limit: int = SEOUL_DEFAULT_DETAIL_LIMIT,
    *,
    max_workers: int = SEOUL_MAX_WORKERS,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect a stable snapshot, retrying only audited cross-partition drift."""

    result: tuple[list[dict[str, Any]], str, dict[str, Any]]
    for attempt in range(1, SEOUL_CENSUS_MAX_ATTEMPTS + 1):
        result = _collect_seoul_public_service_courses_once(
            target,
            timeout=timeout,
            max_pages=max_pages,
            detail_limit=detail_limit,
            max_workers=max_workers,
            session_factory=session_factory,
            dedupe_rows=dedupe_rows,
            allow_raw_requests_for_tests=allow_raw_requests_for_tests,
        )
        rows, parser, meta = result
        meta["snapshot_attempts"] = attempt
        error = _clean(meta.get("configured_collection_error"))
        retryable = any(marker in error for marker in _RETRYABLE_CENSUS_DRIFT_MARKERS)
        if rows or not retryable or attempt == SEOUL_CENSUS_MAX_ATTEMPTS:
            return rows, parser, meta
        time.sleep(0.5 * attempt)
    raise AssertionError("unreachable census retry state")


collect = collect_seoul_public_service_courses
