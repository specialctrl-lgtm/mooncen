"""Fail-closed collector for Sejong 읍면동 resident education courses.

The official page is a server-rendered course ledger, but its pagination links
drop the active state and page-size filters.  This collector therefore sends
the complete reviewed query on every list request and never follows the page
links rendered by the site.

Only three current course states are owned: 모집중, 대기중, and 교육중.
교육종료 is an archive and is intentionally excluded.  Every current identity
must resolve to an official course detail with exact education/application
periods and a venue.  Notice boards, navigation links, application forms,
attachments, login routes, and applicant/PII endpoints are never requested.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import math
import re
from threading import Lock, local
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, NavigableString, Tag


SEJONG_EMD_PROVIDER = "MUNI_WWW_SEJONG_GO_KR_53F478AF"
SEJONG_EMD_HOST = "www.sejong.go.kr"
SEJONG_EMD_ROOT = f"https://{SEJONG_EMD_HOST}"
SEJONG_EMD_LIST_PATH = "/prog/lecCourse/EMD/dong/sub03_03/intro.do"
SEJONG_EMD_CANONICAL_URL = f"{SEJONG_EMD_ROOT}{SEJONG_EMD_LIST_PATH}"
SEJONG_EMD_MUNICIPALITY_CODE = "3611000000"
SEJONG_EMD_MUNICIPALITY_NAME = "세종특별자치시"
SEJONG_EMD_PAGE_SIZE = 100
SEJONG_EMD_MAX_HTML_BYTES = 4_000_000
SEJONG_EMD_MAX_WORKERS = 8
SEJONG_EMD_PARSER = (
    "sejong_emd_resident_education+open_scheduled_in_progress_state_partitions+"
    "complete_filtered_pages+empty_sentinels+stable_boundaries+all_current_public_details+"
    "identity_bound_application_controls+course_table_only_notice_exclusion+"
    "no_application_login_attachment_or_pii_calls"
)
SEJONG_EMD_OWNERSHIP_SCOPE = (
    "sejong_emd_current_open_scheduled_and_in_progress_resident_education_courses"
)

SEJONG_EMD_STATE_PARTITIONS: tuple[str, ...] = ("1", "2", "3")
SEJONG_EMD_SUBORGS = frozenset(
    {
        "조치원읍",
        "연기면",
        "연동면",
        "부강면",
        "금남면",
        "장군면",
        "연서면",
        "전의면",
        "전동면",
        "소정면",
        "한솔동",
        "도담동",
        "아름동",
        "종촌동",
        "고운동",
        "보람동",
        "새롬동",
        "대평동",
        "소담동",
        "가람동",
        "어진동",
        "나성동",
        "다정동",
        "해밀동",
        "반곡동",
        "집현동",
        "산울동",
        "누리동",
        "한별동",
    }
)
SEJONG_EMD_STATE_NAMES = {
    "1": "모집중",
    "2": "대기중",
    "3": "교육중",
}
SEJONG_EMD_STATE_STATUSES = {
    "1": "OPEN",
    "2": "SCHEDULED",
    "3": "CLOSED",
}
SEJONG_EMD_LIST_STATUS_LABELS = {
    "1": frozenset({"모집중"}),
    "2": frozenset({"대기중", "모집대기", "모집예정", "접수대기", "접수예정"}),
    "3": frozenset({"모집마감"}),
}
SEJONG_EMD_DETAIL_CONTROL_LABELS = {
    "1": frozenset({"신청하기"}),
    "2": frozenset({"대기중", "모집대기", "모집예정", "접수대기", "접수예정"}),
    "3": frozenset({"접수마감", "모집마감"}),
}

_SPACE_RE = re.compile(r"\s+")
_INTEGER_RE = re.compile(r"\d+")
_CAPACITY_RE = re.compile(
    r"(\d[\d,]*)\s*/\s*(\d[\d,]*)"
    r"(?:\s+대기\(\d[\d,]*/\d[\d,]*/?\))?\Z"
)
_DATE_RANGE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})\s*~\s*(20\d{2}-\d{2}-\d{2})\Z")
_DATETIME_RANGE_RE = re.compile(
    r"(20\d{2}-\d{2}-\d{2})(?:\s+([0-2]\d:[0-5]\d))?\s*~\s*"
    r"(20\d{2}-\d{2}-\d{2})(?:\s+([0-2]\d:[0-5]\d))?\Z"
)
_DETAIL_PATH_RE = re.compile(r"/prog/lecCourse/EMD/([a-z0-9_-]+)/sub03_03/view\.do\Z")
_APPLICATION_PATH_RE = re.compile(r"/prog/lecReserve/EMD/([a-z0-9_-]+)/sub03_03/write\.do\Z")
_LIST_HEADERS = ("순번", "읍면동", "교육과정", "신청인원/모집인원", "프로그램정보", "상태")
_LIST_INFO_LABELS = ("운영일자", "운영시간", "수강인원", "수강료", "수강접수일정", "교육장소")
_CAPTION_REQUIRED_FIELDS = frozenset({"교육시간", "교육기간", "접수기간", "수업료", "재료비", "실습비"})
_CAPTION_ALLOWED_FIELDS = _CAPTION_REQUIRED_FIELDS | {"담당자", "접수 대상"}
_SUMMARY_FIELDS = frozenset({"교육정원", "교육대상", "교육장소", "문의전화"})
_NO_DATA_MARKERS = ("검색된 내용이 없습니다.", "등록된 강좌가 없습니다.", "등록된 프로그램이 없습니다.")
_ROAD_ADDRESS_SUFFIX_RE = re.compile(
    r"\(\s*(?P<address>"
    r"(?:세종특별자치시\s+)?(?:[0-9A-Za-z가-힣·.-]+(?:읍|면|동)\s+)?"
    r"[0-9A-Za-z가-힣·.-]+(?:대로|로|길)\s*\d+(?:-\d+)?)\s*\)\s*\Z"
)
_MULTI_VENUE_RE = re.compile(r"(?:\s+(?:또는|혹은)\s+|[,/])")
_ROOM_ONLY_VENUE_RE = re.compile(
    r"(?i)^(?:(?:지하\s*)?\d+\s*층\s+)?"
    r"[0-9A-Za-z가-힣. ]*(?:실|룸|홀|강당|방|교실)\s*\d*"
    r"(?:\s*\([^)]*\))?$"
)
_BARE_ROOM_HAPPINESS_CENTERS = frozenset(
    {
        "대평동",
        "도담동",
        "보람동",
        "소담동",
        "아름동",
        "종촌동",
    }
)
_CANONICAL_FACILITY_PREFIXES: dict[str, tuple[tuple[str, str], ...]] = {
    "고운동": (
        ("남측 복컴", "고운동 남측 행복누림터"),
        ("남측 복합커뮤니티센터", "고운동 남측 행복누림터"),
        ("남측 행복누림터", "고운동 남측 행복누림터"),
        ("북측 복컴", "고운동 북측 행복누림터"),
        ("북측 복합커뮤니티센터", "고운동 북측 행복누림터"),
        ("북측 행복누림터", "고운동 북측 행복누림터"),
    ),
    "나성동": (
        ("복컴", "나성동 행복누림터"),
        ("복합커뮤니티센터", "나성동 행복누림터"),
        ("행복누림터", "나성동 행복누림터"),
    ),
    "어진동": (
        ("복컴", "어진동 행복누림터"),
        ("복합커뮤니티센터", "어진동 행복누림터"),
        ("행복누림터", "어진동 행복누림터"),
    ),
    "연동면": (("행복누림터", "연동면 행복누림터"),),
    "부강면": (
        ("복지회관", "부강면문화복지회관"),
        ("신협", "세종부강신협 본점"),
    ),
    "연서면": (
        ("봉암출장소", "연서면행정복지센터 봉암출장소"),
        ("연서면사무소", "연서면행정복지센터"),
    ),
    "장군면": (("복지회관", "장군면복지회관"),),
    "전동면": (
        ("복컴", "전동면 복합커뮤니티센터"),
        ("복합커뮤니티센터", "전동면 복합커뮤니티센터"),
    ),
    "전의면": (
        ("복컴", "전의면행복누림터"),
        ("복합커뮤니티센터", "전의면행복누림터"),
        ("행복누림터", "전의면행복누림터"),
    ),
    "조치원읍": (
        ("복컴", "조치원읍 행복누림터"),
        ("복합커뮤니티센터", "조치원읍 행복누림터"),
        ("행복누림터", "조치원읍 행복누림터"),
    ),
    "한솔동": (
        ("정음관", "한솔동 행복누림터 정음관"),
        ("훈민관", "한솔동 행복누림터 훈민관"),
    ),
}


class SejongEmdContractError(ValueError):
    """Raised when the reviewed official course contract changes."""


@dataclass(frozen=True)
class ListedCourse:
    edu_no: str
    state: str
    page: int
    position: int
    suborg: str
    title: str
    capacity_current: int
    capacity_total: int
    info_text: str
    source_status: str
    raw_url: str
    site_slug: str

    @property
    def identity(self) -> str:
        return self.edu_no

    def stable_signature(self) -> tuple[Any, ...]:
        # Applicant counts are intentionally excluded: they can legitimately
        # change while public details are being read.
        return (
            self.edu_no,
            self.state,
            self.page,
            self.position,
            self.suborg,
            self.title,
            self.capacity_total,
            self.info_text,
            self.source_status,
            self.raw_url,
            self.site_slug,
        )


@dataclass(frozen=True)
class ListPage:
    state: str
    page: int
    total: int
    last_page: int
    rows: tuple[ListedCourse, ...]

    def stable_signature(self) -> tuple[Any, ...]:
        return (
            self.state,
            self.page,
            self.total,
            self.last_page,
            tuple(row.stable_signature() for row in self.rows),
        )


SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


@dataclass(frozen=True)
class SejongEmdVenueLocation:
    branch: str
    branch_identity: str
    address: str


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", _clean(value).casefold())


def _official_parenthetical_road_address(suborg: str, venue: str) -> tuple[str, str]:
    """Split one exact official ``(...로/길 N)`` suffix from a venue.

    Floor, room, prose, and multi-place parentheses deliberately do not match.
    읍/면 is restored in the full address because the source only publishes the
    road-name suffix for these rows.
    """

    match = _ROAD_ADDRESS_SUFFIX_RE.search(venue)
    if not match:
        return venue, ""
    facility = _clean(venue[: match.start()])
    address = _clean(match.group("address"))
    address = re.sub(r"\s*(\d+(?:-\d+)?)\Z", r" \1", address)
    if not address.startswith(SEJONG_EMD_MUNICIPALITY_NAME):
        locality = suborg if suborg.endswith(("읍", "면")) and not address.startswith(suborg) else ""
        address = _clean(f"{SEJONG_EMD_MUNICIPALITY_NAME} {locality} {address}")
    return facility, address


def sejong_emd_venue_location(suborg: Any, venue: Any) -> SejongEmdVenueLocation:
    """Return a stable physical branch and an official inline road address.

    Only reviewed, single-place Sejong facility patterns are collapsed.  This
    prevents room names from creating dozens of duplicate branches while also
    keeping ambiguous multi-place courses out of automatic location fixes.
    """

    owner = _clean(suborg)
    source_venue = _clean(venue)
    facility, address = _official_parenthetical_road_address(owner, source_venue)
    if not source_venue:
        branch = _clean(f"{owner} 주민자치프로그램")
        return SejongEmdVenueLocation(branch=branch, branch_identity="", address="")

    # Never assign one canonical facility to a course that names multiple
    # physical venues.  Its original branch identity remains available for
    # manual review and it receives no inferred address.
    if _MULTI_VENUE_RE.search(facility):
        branch = _clean(f"{owner} {source_venue}")
        return SejongEmdVenueLocation(
            branch=branch,
            branch_identity=source_venue,
            address="",
        )

    owned_facility = re.sub(rf"^{re.escape(owner)}\s+", "", facility).strip()
    for prefix, canonical in _CANONICAL_FACILITY_PREFIXES.get(owner, ()):
        if owned_facility.startswith(prefix):
            return SejongEmdVenueLocation(
                branch=canonical,
                branch_identity=canonical,
                address=address,
            )

    if owner in _BARE_ROOM_HAPPINESS_CENTERS and (
        _ROOM_ONLY_VENUE_RE.fullmatch(facility)
        or owned_facility.startswith(("복컴", "복합커뮤니티센터", "행복누림터"))
    ):
        canonical = f"{owner} 행복누림터"
        return SejongEmdVenueLocation(
            branch=canonical,
            branch_identity=canonical,
            address=address,
        )

    # An exact inline road address is safe even for an external named venue.
    # Drop only that suffix from the branch identity; all other unreviewed
    # venue text preserves the previous crawler identity byte-for-byte.
    branch_venue = facility if address else source_venue
    return SejongEmdVenueLocation(
        branch=_clean(f"{owner} {branch_venue}"),
        branch_identity=branch_venue,
        address=address,
    )


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    try:
        return date.fromisoformat(_clean(value))
    except ValueError as exc:
        raise SejongEmdContractError("today must be an ISO date") from exc


def _exact_target_url(value: Any) -> bool:
    parsed = urlparse(_clean(value))
    try:
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == SEJONG_EMD_HOST
        and port is None
        and parsed.path == SEJONG_EMD_LIST_PATH
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


def is_sejong_emd_target(target: Any) -> bool:
    return (
        _clean(_target_value(target, "provider")).upper() == SEJONG_EMD_PROVIDER
        and _exact_target_url(_target_value(target, "url"))
    )


is_target = is_sejong_emd_target


def sejong_emd_list_payload(state: Any, page: Any) -> dict[str, str]:
    state_text = _clean(state)
    page_text = _clean(page)
    if state_text not in SEJONG_EMD_STATE_PARTITIONS:
        raise SejongEmdContractError("list state is outside the current-course partitions")
    if not page_text.isdigit() or int(page_text) < 1:
        raise SejongEmdContractError("list page must be a positive integer")
    return {
        "pageUnit": str(SEJONG_EMD_PAGE_SIZE),
        "pageIndex": str(int(page_text)),
        "pageSize": str(SEJONG_EMD_PAGE_SIZE),
        "suborgCode": "",
        "groupYn": "",
        "stDt": "",
        "edDt": "",
        "state": state_text,
        "searchCondition": "subject",
        "searchKeyword": "",
    }


def _remove_session_id(value: str) -> str:
    parsed = urlparse(value)
    path = re.sub(r";jsessionid=[^/?#;]+", "", parsed.path, flags=re.IGNORECASE)
    params = re.sub(r"^jsessionid=[^/?#;]+;?", "", parsed.params, flags=re.IGNORECASE)
    return urlunparse((parsed.scheme, parsed.netloc, path, params, parsed.query, parsed.fragment))


def _safe_origin(parsed: Any) -> bool:
    try:
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == SEJONG_EMD_HOST
        and port is None
        and not parsed.username
        and not parsed.password
        and not parsed.fragment
    )


def _safe_list_response_url(value: Any, payload: Mapping[str, str]) -> bool:
    parsed = urlparse(_remove_session_id(_clean(value)))
    if not _safe_origin(parsed) or parsed.path != SEJONG_EMD_LIST_PATH or parsed.params:
        return False
    if not parsed.query:
        return True
    query = parse_qs(parsed.query, keep_blank_values=True)
    return bool(
        set(query) == set(payload)
        and all(len(query[key]) == 1 and query[key][0] == payload[key] for key in payload)
    )


def _safe_detail_url(value: Any, *, expected_edu_no: str = "") -> tuple[str, str] | None:
    absolute = _remove_session_id(urljoin(SEJONG_EMD_ROOT, _clean(value)))
    parsed = urlparse(absolute)
    match = _DETAIL_PATH_RE.fullmatch(parsed.path)
    if not _safe_origin(parsed) or not match or parsed.params:
        return None
    query = parse_qs(parsed.query, keep_blank_values=True)
    if not set(query).issubset({"pageIndex", "eduNo", "oneInwon"}) or set(query) < {"eduNo"}:
        return None
    if any(len(values) != 1 for values in query.values()):
        return None
    edu_no = query["eduNo"][0]
    if not edu_no.isdigit() or (expected_edu_no and edu_no != expected_edu_no):
        return None
    if "pageIndex" in query and (not query["pageIndex"][0].isdigit() or int(query["pageIndex"][0]) < 1):
        return None
    return absolute, match.group(1)


def _safe_application_url(value: Any, *, expected_edu_no: str, expected_slug: str) -> str:
    absolute = _remove_session_id(urljoin(SEJONG_EMD_ROOT, _clean(value)))
    parsed = urlparse(absolute)
    match = _APPLICATION_PATH_RE.fullmatch(parsed.path)
    if not _safe_origin(parsed) or not match or match.group(1) != expected_slug or parsed.params:
        return ""
    query = parse_qs(parsed.query, keep_blank_values=True)
    if not set(query).issubset({"pageIndex", "eduNo", "oneInwon", "resvChk"}) or set(query) < {"eduNo"}:
        return ""
    if any(len(values) != 1 for values in query.values()) or query["eduNo"][0] != expected_edu_no:
        return ""
    return absolute


def _default_session_factory() -> requests.Session:
    value = requests.Session()
    value.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (compatible; MooncenMunicipalAudit/1.0; "
                "+https://www.sejong.go.kr/)"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    return value


class _Requester:
    def __init__(self, session_factory: SessionFactory, timeout: int, request_cap: int) -> None:
        self.session_factory = session_factory
        self.timeout = timeout
        self.request_cap = request_cap
        self.list_session = session_factory()
        self.thread_state = local()
        self.sessions: list[Any] = [self.list_session]
        self.lock = Lock()
        self.requests = 0
        self.list_requests = 0
        self.detail_requests = 0

    def _consume(self, kind: str) -> None:
        with self.lock:
            if self.requests >= self.request_cap:
                raise SejongEmdContractError("request budget exhausted")
            self.requests += 1
            if kind == "list":
                self.list_requests += 1
            else:
                self.detail_requests += 1

    def _detail_session(self) -> Any:
        value = getattr(self.thread_state, "session", None)
        if value is None:
            value = self.session_factory()
            self.thread_state.session = value
            with self.lock:
                self.sessions.append(value)
        return value

    def _soup(self, response: Any, requested_url: str, *, kind: str, payload: Mapping[str, str] | None) -> BeautifulSoup:
        status = int(getattr(response, "status_code", 200))
        if status != 200:
            raise SejongEmdContractError(f"unexpected HTTP status {status}")
        final_url = _clean(getattr(response, "url", requested_url)) or requested_url
        if kind == "list":
            if payload is None or not _safe_list_response_url(final_url, payload):
                raise SejongEmdContractError("list response left the reviewed HTTPS query scope")
        elif _safe_detail_url(final_url) is None:
            raise SejongEmdContractError("detail response left the reviewed HTTPS scope")
        headers = getattr(response, "headers", {}) or {}
        content_type = _clean(headers.get("Content-Type"))
        if content_type and "html" not in content_type.lower():
            raise SejongEmdContractError("response is not HTML")
        content = getattr(response, "content", None)
        if content is None:
            content = str(getattr(response, "text", "")).encode("utf-8")
        if not content or len(content) > SEJONG_EMD_MAX_HTML_BYTES:
            raise SejongEmdContractError("HTML response is empty or over the size cap")
        return BeautifulSoup(content, "lxml")

    def list_soup(self, state: str, page: int) -> BeautifulSoup:
        payload = sejong_emd_list_payload(state, page)
        self._consume("list")
        response = self.list_session.get(
            SEJONG_EMD_CANONICAL_URL,
            params=payload,
            timeout=self.timeout,
            allow_redirects=False,
        )
        return self._soup(response, SEJONG_EMD_CANONICAL_URL, kind="list", payload=payload)

    def detail_soup(self, url: str) -> BeautifulSoup:
        if _safe_detail_url(url) is None:
            raise SejongEmdContractError("detail request left the reviewed public URL allowlist")
        self._consume("detail")
        response = self._detail_session().get(url, timeout=self.timeout, allow_redirects=False)
        return self._soup(response, url, kind="detail", payload=None)

    def close(self) -> None:
        for value in reversed(self.sessions):
            close = getattr(value, "close", None)
            if callable(close):
                close()


def _direct_label(node: Tag) -> str:
    return _clean(" ".join(str(child) for child in node.children if isinstance(child, NavigableString)))


def _value_after_label(node: Tag, label_node: Tag) -> str:
    values: list[str] = []
    seen = False
    for child in node.children:
        if child is label_node:
            seen = True
            continue
        if not seen:
            continue
        if isinstance(child, NavigableString):
            values.append(str(child))
        elif isinstance(child, Tag):
            values.append(child.get_text(" ", strip=True))
    return _clean(" ".join(values))


def _validate_form_contract(soup: BeautifulSoup) -> None:
    forms = soup.select('form[name="eduSearchForm"]')
    if len(forms) != 1:
        raise SejongEmdContractError("course search form contract changed")
    form = forms[0]
    action = urlparse(_remove_session_id(urljoin(SEJONG_EMD_ROOT, _clean(form.get("action")))))
    if action.path != SEJONG_EMD_LIST_PATH or _clean(form.get("method")).lower() != "post":
        raise SejongEmdContractError("course search form action/method changed")
    names = {_clean(node.get("name")) for node in form.select("[name]") if _clean(node.get("name"))}
    required_names = set(sejong_emd_list_payload("1", 1))
    if not required_names.issubset(names):
        raise SejongEmdContractError("course search form fields changed")
    state_options = {
        _clean(option.get("value")): _clean(option.get_text(" ", strip=True))
        for option in form.select('select[name="state"] option')
        if _clean(option.get("value"))
    }
    if state_options != {"1": "모집중", "2": "대기중", "3": "교육중", "4": "교육종료"}:
        raise SejongEmdContractError("course state directory changed")


def _declared_total(soup: BeautifulSoup) -> int:
    markers = soup.select(".program--count strong")
    if len(markers) != 1:
        raise SejongEmdContractError("course total marker contract changed")
    value = _clean(markers[0].get_text(" ", strip=True)).replace(",", "")
    if not value.isdigit():
        raise SejongEmdContractError("course total is not an integer")
    return int(value)


def _parse_capacity(value: Any) -> tuple[int, int]:
    match = _CAPACITY_RE.fullmatch(_clean(value))
    if not match:
        raise SejongEmdContractError("list capacity is not current/total")
    current, total = (int(group.replace(",", "")) for group in match.groups())
    if total < 1 or current < 0:
        raise SejongEmdContractError("list capacity is invalid")
    return current, total


def _parse_list_page(soup: BeautifulSoup, state: str, page: int) -> ListPage:
    total = _declared_total(soup)
    last_page = max(1, math.ceil(total / SEJONG_EMD_PAGE_SIZE))
    tables = []
    for table in soup.select("table.table-default"):
        caption = table.find("caption")
        if caption is not None and "강좌명/강사명" in _clean(caption.get_text(" ", strip=True)):
            tables.append(table)
    if len(tables) != 1:
        raise SejongEmdContractError("official course result table contract changed")
    table = tables[0]
    headers = tuple(_clean(node.get_text(" ", strip=True)) for node in table.select("thead th"))
    if headers != _LIST_HEADERS:
        raise SejongEmdContractError("course result headers changed")
    rows: list[ListedCourse] = []
    for table_row in table.select("tbody > tr"):
        cells = table_row.select(":scope > td")
        links = table_row.select('a[href*="view.do"]')
        if not links:
            marker = _clean(table_row.get_text(" ", strip=True))
            if marker and not any(value in marker for value in _NO_DATA_MARKERS):
                raise SejongEmdContractError("non-course row appeared in the official course result table")
            continue
        if len(cells) != len(_LIST_HEADERS) or len(links) != 1:
            raise SejongEmdContractError("course row columns/detail control changed")
        values = [_clean(cell.get_text(" ", strip=True)) for cell in cells]
        suborg, title, info_text, source_status = values[1], values[2], values[4], values[5]
        if suborg not in SEJONG_EMD_SUBORGS or not title or source_status not in SEJONG_EMD_LIST_STATUS_LABELS[state]:
            raise SejongEmdContractError("course row identity/status changed")
        if not all(label in info_text for label in _LIST_INFO_LABELS):
            raise SejongEmdContractError("course row lacks the structured programme fields")
        current, capacity_total = _parse_capacity(values[3])
        safe_detail = _safe_detail_url(links[0].get("href"))
        if safe_detail is None:
            raise SejongEmdContractError("course row detail URL left the reviewed course route")
        raw_url, site_slug = safe_detail
        query = parse_qs(urlparse(raw_url).query, keep_blank_values=True)
        edu_no = query["eduNo"][0]
        rows.append(
            ListedCourse(
                edu_no=edu_no,
                state=state,
                page=page,
                position=len(rows) + 1,
                suborg=suborg,
                title=title,
                capacity_current=current,
                capacity_total=capacity_total,
                info_text=info_text,
                source_status=source_status,
                raw_url=raw_url,
                site_slug=site_slug,
            )
        )
    return ListPage(state=state, page=page, total=total, last_page=last_page, rows=tuple(rows))


def _parse_date_range(value: Any, field: str) -> tuple[date, date]:
    match = _DATE_RANGE_RE.fullmatch(_clean(value))
    if not match:
        raise SejongEmdContractError(f"{field} is not an exact date range")
    try:
        start, end = (date.fromisoformat(group) for group in match.groups())
    except ValueError as exc:
        raise SejongEmdContractError(f"{field} contains an invalid date") from exc
    if end < start:
        raise SejongEmdContractError(f"{field} range is reversed")
    return start, end


def _parse_datetime_range(value: Any, field: str) -> tuple[date, date]:
    match = _DATETIME_RANGE_RE.fullmatch(_clean(value))
    if not match:
        raise SejongEmdContractError(f"{field} is not an exact date/time range")
    try:
        start = date.fromisoformat(match.group(1))
        end = date.fromisoformat(match.group(3))
    except ValueError as exc:
        raise SejongEmdContractError(f"{field} contains an invalid date") from exc
    if end < start:
        raise SejongEmdContractError(f"{field} range is reversed")
    return start, end


def _integer(value: Any, field: str) -> int:
    match = _INTEGER_RE.search(_clean(value).replace(",", ""))
    if not match:
        raise SejongEmdContractError(f"{field} has no integer")
    return int(match.group(0))


def _fee(value: Any) -> int | None:
    text = _clean(value)
    if not text:
        return None
    if "무료" in text:
        return 0
    match = re.search(r"([\d,]+)\s*원", text)
    return int(match.group(1).replace(",", "")) if match else None


def _labelled_caption_fields(root: Tag) -> dict[str, str]:
    fields: dict[str, str] = {}
    for item in root.select(".caption-info > .li"):
        labels = item.select(":scope > b")
        if len(labels) != 1:
            raise SejongEmdContractError("detail caption label contract changed")
        label = _direct_label(labels[0])
        if not label or label in fields:
            raise SejongEmdContractError("detail caption labels are empty or duplicated")
        fields[label] = _value_after_label(item, labels[0])
    if not _CAPTION_REQUIRED_FIELDS.issubset(fields) or not set(fields).issubset(_CAPTION_ALLOWED_FIELDS):
        raise SejongEmdContractError("detail caption field directory changed")
    return fields


def _summary_fields(root: Tag) -> dict[str, str]:
    fields: dict[str, str] = {}
    for item in root.select(".apply-article .self-accrdt > .item"):
        labels = item.select(":scope > strong")
        values = item.select(":scope > em")
        if len(labels) != 1 or len(values) != 1:
            raise SejongEmdContractError("detail summary field contract changed")
        label = _clean(labels[0].get_text(" ", strip=True))
        if not label or label in fields:
            raise SejongEmdContractError("detail summary labels are empty or duplicated")
        fields[label] = _clean(values[0].get_text(" ", strip=True))
    if set(fields) != _SUMMARY_FIELDS:
        raise SejongEmdContractError("detail summary field directory changed")
    return fields


def _branch_code(suborg: str, venue: str) -> str:
    identity = f"{SEJONG_EMD_PROVIDER}|{_normalized(suborg)}|{_normalized(venue)}"
    return f"SEJONG_EMD_{hashlib.sha1(identity.encode('utf-8')).hexdigest()[:16].upper()}"


def _row_from_detail(
    target: Any,
    listed: ListedCourse,
    soup: BeautifulSoup,
    cutoff: date,
) -> dict[str, Any]:
    roots = soup.select("#txt .program--contents")
    if len(roots) != 1:
        raise SejongEmdContractError("official course detail root changed")
    root = roots[0]
    titles = root.select(".caption-title")
    if len(titles) != 1 or _normalized(titles[0].get_text(" ", strip=True)) != _normalized(listed.title):
        raise SejongEmdContractError("list/detail title identity mismatch")
    caption = _labelled_caption_fields(root)
    summary = _summary_fields(root)
    start_date, end_date = _parse_date_range(caption["교육기간"], "교육기간")
    apply_start, apply_end = _parse_datetime_range(caption["접수기간"], "접수기간")
    if end_date < cutoff:
        raise SejongEmdContractError("current-state partition returned an expired course")
    venue = _clean(summary["교육장소"])
    location = sejong_emd_venue_location(listed.suborg, venue)
    detail_capacity = _integer(summary["교육정원"], "교육정원")
    if detail_capacity < 1:
        raise SejongEmdContractError("course detail capacity is invalid")

    controls = root.select(".figure .btn_wrap > a")
    if len(controls) != 1:
        raise SejongEmdContractError("detail application control changed")
    control = controls[0]
    control_text = _clean(control.get_text(" ", strip=True))
    if control_text not in SEJONG_EMD_DETAIL_CONTROL_LABELS[listed.state]:
        raise SejongEmdContractError("detail application state does not match the list partition")
    application_url = ""
    application_control_present = False
    if listed.state == "1":
        discovered_application_url = _safe_application_url(
            control.get("href"),
            expected_edu_no=listed.edu_no,
            expected_slug=listed.site_slug,
        )
        if not discovered_application_url:
            raise SejongEmdContractError("open course application control is not identity-bound")
        # Keep the public course detail as the user-facing URL.  The write
        # route is identity-checked above but is never fetched or persisted.
        application_url = listed.raw_url
        application_control_present = True
    elif _clean(control.get("href")) not in {"", "#", "javascript:void(0);", "javascript:void(0)"}:
        raise SejongEmdContractError("non-open course unexpectedly exposes an application route")

    provider = _clean(_target_value(target, "provider")).upper()
    branch = location.branch
    target_text = _clean(summary["교육대상"] or caption.get("접수 대상"))
    capacity_mismatch = listed.capacity_total != detail_capacity
    normalized_status = SEJONG_EMD_STATE_STATUSES[listed.state]
    return {
        "provider": provider,
        "provider_course_id": f"{provider}:emd:{listed.edu_no}"[:100],
        "prefer_incoming_provider_course_id": True,
        "title": listed.title,
        "branch": branch,
        "branch_code": _branch_code(listed.suborg, location.branch_identity),
        "preserve_branch": True,
        "branch_url": SEJONG_EMD_CANONICAL_URL,
        "branch_address": location.address,
        "branch_address_source": (
            "OFFICIAL_SEJONG_EMD_DETAIL_VENUE" if location.address else ""
        ),
        "category": "교육·강좌",
        "program_type": "교육",
        "raw_url": listed.raw_url,
        "source_url": SEJONG_EMD_CANONICAL_URL,
        "application_url": application_url,
        "application_type": "ONLINE_RESERVATION" if application_url else "INFO_ONLY",
        "application_method_raw": "온라인" if application_url else "",
        "application_methods": ["온라인"] if application_url else [],
        "reservation_available": bool(application_url),
        "status": normalized_status,
        "fee": _fee(caption["수업료"]),
        "price_text": caption["수업료"],
        "period": f"{start_date.isoformat()} ~ {end_date.isoformat()}",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "apply_period": f"{apply_start.isoformat()} ~ {apply_end.isoformat()}",
        "apply_start": apply_start.isoformat(),
        "apply_end": apply_end.isoformat(),
        "schedule_raw": caption["교육시간"],
        "target": target_text,
        "capacity": f"{listed.capacity_current}/{detail_capacity}",
        "capacity_current": listed.capacity_current,
        "capacity_total": detail_capacity,
        "venue_name": venue,
        "venue_address": location.address,
        "address": location.address,
        "contact": summary["문의전화"],
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": SEJONG_EMD_PARSER,
        "municipality_code": SEJONG_EMD_MUNICIPALITY_CODE,
        "municipality_full_name": SEJONG_EMD_MUNICIPALITY_NAME,
        "municipality_region_verified": True,
        "region_sido": SEJONG_EMD_MUNICIPALITY_NAME,
        "region_sigungu": SEJONG_EMD_MUNICIPALITY_NAME,
        "raw_fields": {
            "parser": SEJONG_EMD_PARSER,
            "ownership_scope": SEJONG_EMD_OWNERSHIP_SCOPE,
            "source_edu_no": listed.edu_no,
            "source_state": listed.state,
            "source_state_name": SEJONG_EMD_STATE_NAMES[listed.state],
            "source_status": listed.source_status,
            "source_page": listed.page,
            "source_position": listed.position,
            "source_suborg": listed.suborg,
            "source_site_slug": listed.site_slug,
            "source_list_info": listed.info_text,
            "source_capacity_total": listed.capacity_total,
            "detail_capacity_mismatch": capacity_mismatch,
            "material_fee": caption["재료비"],
            "practice_fee": caption["실습비"],
            "manager": caption.get("담당자", ""),
            "location_query_hint": location.address
            or _clean(f"{SEJONG_EMD_MUNICIPALITY_NAME} {branch}"),
            "source_venue_address": location.address,
            "source_venue_address_verified": bool(location.address),
            "source_venue_missing": not venue,
            "detail_verified": True,
            "structured_course_row_verified": True,
            "application_control_present": application_control_present,
            "application_route_identity_verified_not_stored": application_control_present,
            "application_endpoint_fetched": False,
            "notice_board_endpoint_fetched": False,
            "authentication_endpoint_fetched": False,
            "attachment_endpoint_fetched": False,
            "pii_endpoint_fetched": False,
        },
    }


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "request_count": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "source_total": 0,
        "source_totals": {},
        "source_pages": {},
        "source_counts": {},
        "source_status_counts": {},
        "returned_count": 0,
        "unique_source_count": 0,
        "source_duplicate_count": 0,
        "semantic_duplicate_count": 0,
        "branch_counts": {},
        "application_control_count": 0,
        "list_detail_capacity_mismatch_count": 0,
        "pagination_complete": False,
        "stable_boundaries": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "no_current_reason": "",
        "canonical_provider": SEJONG_EMD_PROVIDER,
        "canonical_url": SEJONG_EMD_CANONICAL_URL,
        "ownership_scope": SEJONG_EMD_OWNERSHIP_SCOPE,
        "covered_municipalities": [
            {
                "code": SEJONG_EMD_MUNICIPALITY_CODE,
                "sido": SEJONG_EMD_MUNICIPALITY_NAME,
                "sigungu": SEJONG_EMD_MUNICIPALITY_NAME,
                "full_name": SEJONG_EMD_MUNICIPALITY_NAME,
            }
        ],
        "excluded_states": {"4": "교육종료 archive"},
        "notice_board_requests": 0,
        "application_endpoint_requests": 0,
        "authentication_endpoint_requests": 0,
        "attachment_endpoint_requests": 0,
        "pii_endpoint_requests": 0,
        "http_methods": ["GET"],
        "configured_collection_error": "",
    }


def collect_sejong_emd_education(
    target: Any,
    timeout: int = 30,
    max_pages: int = 100,
    detail_limit: int = 800,
    *,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = SEJONG_EMD_MAX_WORKERS,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one atomic current Sejong 읍면동 education snapshot."""

    meta = _base_meta()
    if not is_sejong_emd_target(target):
        meta["configured_collection_error"] = "target does not match the exact Sejong 읍면동 course ledger"
        return [], SEJONG_EMD_PARSER, meta
    try:
        timeout_value = int(timeout)
        page_cap = int(max_pages)
        detail_cap = int(detail_limit)
        workers = int(max_workers)
        cutoff = _today(today)
    except (TypeError, ValueError, SejongEmdContractError) as exc:
        meta["configured_collection_error"] = f"invalid arguments: {_clean(exc)}"
        return [], SEJONG_EMD_PARSER, meta
    if timeout_value < 1 or page_cap < 1 or detail_cap < 0 or workers < 1:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = "invalid timeout/max_pages/detail_limit/max_workers cap"
        return [], SEJONG_EMD_PARSER, meta

    requester: Optional[_Requester] = None
    try:
        requester = _Requester(
            session_factory or _default_session_factory,
            timeout_value,
            request_cap=page_cap + detail_cap + 8,
        )
        first_pages: dict[str, ListPage] = {}
        final_pages: dict[str, ListPage] = {}
        all_pages: dict[str, list[ListPage]] = {}
        listed_rows: list[ListedCourse] = []

        for state in SEJONG_EMD_STATE_PARTITIONS:
            soup = requester.list_soup(state, 1)
            if state == "1":
                _validate_form_contract(soup)
            first_pages[state] = _parse_list_page(soup, state, 1)

        required_list_requests = 0
        for state, first in first_pages.items():
            boundary_rechecks = 1 if first.last_page == 1 else 2
            required_list_requests += first.last_page + 1 + boundary_rechecks
            meta["source_totals"][state] = first.total
            meta["source_pages"][state] = first.last_page
        meta["required_list_requests"] = required_list_requests
        if required_list_requests > page_cap:
            meta["source_cap_reached"] = True
            raise SejongEmdContractError(
                f"max_pages={page_cap} is below required list requests={required_list_requests}"
            )

        for state, first in first_pages.items():
            pages = [first]
            for page_number in range(2, first.last_page + 1):
                page = _parse_list_page(requester.list_soup(state, page_number), state, page_number)
                if page.total != first.total or page.last_page != first.last_page:
                    raise SejongEmdContractError("declared state total/pages changed during pagination")
                pages.append(page)
            for page in pages:
                expected = max(
                    0,
                    min(SEJONG_EMD_PAGE_SIZE, first.total - SEJONG_EMD_PAGE_SIZE * (page.page - 1)),
                )
                if len(page.rows) != expected:
                    raise SejongEmdContractError(
                        f"state={state} page={page.page} has {len(page.rows)} rows, expected {expected}"
                    )
            sentinel_number = first.last_page + 1
            sentinel = _parse_list_page(
                requester.list_soup(state, sentinel_number),
                state,
                sentinel_number,
            )
            if sentinel.total != first.total or sentinel.last_page != first.last_page or sentinel.rows:
                raise SejongEmdContractError("post-last state page is not an exact empty sentinel")
            final_pages[state] = pages[-1]
            all_pages[state] = pages
            state_rows = [row for page in pages for row in page.rows]
            if len(state_rows) != first.total:
                raise SejongEmdContractError("state pages do not reconcile to the declared total")
            meta["source_counts"][state] = len(state_rows)
            listed_rows.extend(state_rows)

        identities = [row.identity for row in listed_rows]
        meta["source_total"] = len(listed_rows)
        meta["unique_source_count"] = len(set(identities))
        meta["source_duplicate_count"] = len(identities) - len(set(identities))
        if meta["source_duplicate_count"]:
            raise SejongEmdContractError("state partitions contain duplicate course identities")
        if len(listed_rows) > detail_cap:
            meta["source_cap_reached"] = True
            raise SejongEmdContractError(
                f"detail_limit={detail_cap} is below unique current rows={len(listed_rows)}"
            )
        meta["pagination_complete"] = True
        meta["detail_attempts"] = len(listed_rows)

        detail_rows: list[dict[str, Any]] = []

        def fetch_detail(listed: ListedCourse) -> dict[str, Any]:
            safe = _safe_detail_url(listed.raw_url, expected_edu_no=listed.edu_no)
            if safe is None:
                raise SejongEmdContractError("listed detail identity left the reviewed route")
            return _row_from_detail(target, listed, requester.detail_soup(listed.raw_url), cutoff)

        if listed_rows:
            with ThreadPoolExecutor(max_workers=min(workers, len(listed_rows))) as executor:
                futures = {executor.submit(fetch_detail, listed): listed for listed in listed_rows}
                for future in as_completed(futures):
                    listed = futures[future]
                    try:
                        detail_rows.append(future.result())
                    except Exception as exc:
                        raise SejongEmdContractError(
                            f"detail {listed.identity}: {type(exc).__name__}: {_clean(exc)}"
                        ) from exc

        for state, first in first_pages.items():
            repeated_first = _parse_list_page(requester.list_soup(state, 1), state, 1)
            if repeated_first.stable_signature() != first.stable_signature():
                raise SejongEmdContractError("first state boundary changed during detail crawl")
            final = final_pages[state]
            if final.page != 1:
                repeated_final = _parse_list_page(requester.list_soup(state, final.page), state, final.page)
                if repeated_final.stable_signature() != final.stable_signature():
                    raise SejongEmdContractError("final state boundary changed during detail crawl")

        detail_rows.sort(key=lambda row: _clean(row.get("provider_course_id")))
        selected_dedupe = dedupe_rows or _dedupe_default
        deduped = list(selected_dedupe(detail_rows))
        if len(deduped) != len(detail_rows):
            raise SejongEmdContractError(
                f"dedupe changed complete returned count {len(detail_rows)} to {len(deduped)}"
            )
        detail_rows = deduped
        semantic_counts = Counter(
            (
                _normalized(row.get("title")),
                _normalized(row.get("period")),
                _normalized(row.get("schedule_raw")),
                _normalized(row.get("branch")),
            )
            for row in detail_rows
        )
        meta.update(
            {
                "pages": requester.list_requests,
                "request_count": requester.requests,
                "list_requests": requester.list_requests,
                "detail_pages": requester.detail_requests,
                "returned_count": len(detail_rows),
                "semantic_duplicate_count": sum(count - 1 for count in semantic_counts.values() if count > 1),
                "source_status_counts": dict(sorted(Counter(row["status"] for row in detail_rows).items())),
                "branch_counts": dict(sorted(Counter(row["branch"] for row in detail_rows).items())),
                "application_control_count": sum(bool(row.get("application_url")) for row in detail_rows),
                "list_detail_capacity_mismatch_count": sum(
                    bool((row.get("raw_fields") or {}).get("detail_capacity_mismatch")) for row in detail_rows
                ),
                "stable_boundaries": True,
                "details_complete": requester.detail_requests == len(listed_rows),
                "snapshot_complete": True,
                "no_current_data": not detail_rows,
                "no_current_reason": (
                    "official current state partitions contain no open, scheduled, or in-progress courses"
                    if not detail_rows
                    else ""
                ),
            }
        )
        return detail_rows, SEJONG_EMD_PARSER, meta
    except Exception as exc:
        if requester is not None:
            meta.update(
                {
                    "pages": requester.list_requests,
                    "request_count": requester.requests,
                    "list_requests": requester.list_requests,
                    "detail_pages": requester.detail_requests,
                }
            )
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        meta["snapshot_complete"] = False
        return [], SEJONG_EMD_PARSER, meta
    finally:
        if requester is not None:
            requester.close()


collect = collect_sejong_emd_education


__all__ = [
    "SEJONG_EMD_CANONICAL_URL",
    "SEJONG_EMD_MUNICIPALITY_CODE",
    "SEJONG_EMD_MUNICIPALITY_NAME",
    "SEJONG_EMD_OWNERSHIP_SCOPE",
    "SEJONG_EMD_PAGE_SIZE",
    "SEJONG_EMD_PARSER",
    "SEJONG_EMD_PROVIDER",
    "SEJONG_EMD_STATE_PARTITIONS",
    "SejongEmdContractError",
    "collect",
    "collect_sejong_emd_education",
    "is_sejong_emd_target",
    "is_target",
    "sejong_emd_list_payload",
]
