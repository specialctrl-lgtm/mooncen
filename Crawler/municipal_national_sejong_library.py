"""Fail-closed collector for National Library of Korea, Sejong programmes.

Only the reviewed ``PRO043`` all-audiences special-programme catalogue is in
scope.  The catalogue declares its total, uses ten rows per page, and returns
an empty immediate post-last page.  A successful snapshot therefore requires
all declared pages, that empty sentinel, stable first/final boundary rechecks,
and every public detail page whose education end date has not passed.

The public detail page contains scripts and forms leading to applicant,
authentication, capacity-check, and attachment endpoints.  This collector
never requests those routes and never copies instructor names, arbitrary body
content, contact details, or attachment metadata.  An open row points its
``application_url`` at the already verified public detail page.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


NATIONAL_SEJONG_LIBRARY_PROVIDER = "MUNI_SEJONG_NL_GO_KR_7F55E25D"
NATIONAL_SEJONG_LIBRARY_CANDIDATE_ID = "MUNI_IR_97ABDAB9D017"
NATIONAL_SEJONG_LIBRARY_CANONICAL_URL = (
    "https://sejong.nl.go.kr/html/c3/c320.jsp?"
    "codeId=PRO043&menuId=O365&upperMenuId=O300&sel=O360"
)
NATIONAL_SEJONG_LIBRARY_URL = NATIONAL_SEJONG_LIBRARY_CANONICAL_URL
NATIONAL_SEJONG_LIBRARY_HOST = "sejong.nl.go.kr"
NATIONAL_SEJONG_LIBRARY_LIST_PATH = "/html/c3/c320.jsp"
NATIONAL_SEJONG_LIBRARY_DETAIL_PATH = "/html/c3/c320_1.jsp"
NATIONAL_SEJONG_LIBRARY_PAGE_SIZE = 10
NATIONAL_SEJONG_LIBRARY_PARSER = (
    "national_sejong_library_pro043+complete_declared_pages+"
    "empty_post_last_sentinel+stable_first_last+all_current_public_details+"
    "privacy_safe_controls"
)
NATIONAL_SEJONG_LIBRARY_OWNERSHIP_SCOPE = (
    "national_sejong_library_pro043_current_and_future_special_program_education"
)
NATIONAL_SEJONG_LIBRARY_MUNICIPALITY_CODE = "3611000000"
NATIONAL_SEJONG_LIBRARY_MUNICIPALITY_NAME = "세종특별자치시"
NATIONAL_SEJONG_LIBRARY_BRANCH = "국립세종도서관"
NATIONAL_SEJONG_LIBRARY_ADDRESS = "세종특별자치시 다솜3로 48"
NATIONAL_SEJONG_LIBRARY_LATITUDE = 36.4988247
NATIONAL_SEJONG_LIBRARY_LONGITUDE = 127.2683884
NATIONAL_SEJONG_LIBRARY_COORDINATE_SOURCE = "KAKAO_LOCAL_ADDRESS"

_CANONICAL_QUERY: Mapping[str, list[str]] = {
    "codeId": ["PRO043"],
    "menuId": ["O365"],
    "upperMenuId": ["O300"],
    "sel": ["O360"],
}
_LIST_HEADERS = (
    "No.",
    "신청현황",
    "교육현황",
    "과정명",
    "강사명",
    "교육기간",
    "신청기간",
    "모집현황 (대기자)",
)
_LIST_FORM_FIELDS = {
    "menuId": "O365",
    "upperMenuId": "O300",
    "codeId": "PRO043",
    "etc1": "",
    "progrmId": "",
    "searchKeyword": "",
}
_APPLICATION_STATUS = {
    "대기자 접수중": "OPEN",
    "신청중": "OPEN",
    "신청 접수중": "OPEN",
    "접수중": "OPEN",
    "신청가능": "OPEN",
    "신청 예정": "SCHEDULED",
    "접수예정": "SCHEDULED",
    "신청전": "SCHEDULED",
    "신청 마감": "CLOSED",
}
_EDUCATION_STATUS = {
    "교육전": "SCHEDULED",
    "교육중": "OPEN",
    "교육종료": "CLOSED",
}
_FORBIDDEN_PATH_MARKERS = (
    "/reqst/",
    "/progrm/progrmapptimecon.do",
    "/login.do",
    "/download",
    "/down.do",
)

Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DATE_RANGE_RE = re.compile(
    r"^(20\d{2})-(\d{1,2})-(\d{1,2})\s*~\s*"
    r"(20\d{2})-(\d{1,2})-(\d{1,2})$"
)
_DATETIME_RANGE_RE = re.compile(
    r"^(20\d{2})-(\d{1,2})-(\d{1,2})\s+"
    r"(\d{1,2}):(\d{2})\s*~\s*"
    r"(20\d{2})-(\d{1,2})-(\d{1,2})\s+"
    r"(\d{1,2}):(\d{2})$"
)
_CAPACITY_RE = re.compile(
    r"^(\d{1,6})\s*/\s*(\d{1,6})\s*"
    r"\(\s*(\d{1,6})\s*/\s*(\d{1,6})\s*\)$"
)
_PAIR_CAPACITY_RE = re.compile(r"^(\d{1,6})\s*/\s*(\d{1,6})$")
_DETAIL_LINK_RE = re.compile(r"^javascript:fn_egov_view\('(\d{1,12})'\)$")
_PAGE_LINK_RE = re.compile(
    r"^fn_egov_link_page\((\d+)\);\s*return\s+false;$"
)


class NationalSejongLibraryContractError(ValueError):
    """Raised when a response leaves the reviewed public catalogue contract."""


@dataclass(frozen=True)
class ListedProgramme:
    identity: str
    ordinal: int
    title: str
    application_status_raw: str
    education_status_raw: str
    status: str
    period: str
    start: date
    end: date
    apply_period: str
    apply_start: datetime
    apply_end: datetime
    capacity_current: int
    capacity_total: int
    waitlist_current: int
    waitlist_total: int
    page: int

    def stable_signature(self) -> tuple[Any, ...]:
        return (
            self.identity,
            self.ordinal,
            self.title,
            self.application_status_raw,
            self.education_status_raw,
            self.period,
            self.apply_period,
            self.capacity_current,
            self.capacity_total,
            self.waitlist_current,
            self.waitlist_total,
        )


@dataclass(frozen=True)
class ListPage:
    requested_page: int
    total: int
    last_page: int
    rows: tuple[ListedProgramme, ...]

    def stable_signature(self) -> tuple[Any, ...]:
        return (
            self.total,
            self.last_page,
            tuple(row.stable_signature() for row in self.rows),
        )


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", _clean(value).lower())


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def is_national_sejong_library_target(target: Any) -> bool:
    """Return true only for the exact registered PRO043 catalogue root."""

    if _clean(_target_value(target, "provider")) != NATIONAL_SEJONG_LIBRARY_PROVIDER:
        return False
    try:
        parsed = urlparse(_clean(_target_value(target, "url")))
        query = parse_qs(parsed.query, keep_blank_values=True)
        return bool(
            parsed.scheme == "https"
            and (parsed.hostname or "").lower() == NATIONAL_SEJONG_LIBRARY_HOST
            and parsed.port is None
            and parsed.path == NATIONAL_SEJONG_LIBRARY_LIST_PATH
            and not parsed.params
            and not parsed.fragment
            and not parsed.username
            and not parsed.password
            and query == _CANONICAL_QUERY
        )
    except ValueError:
        return False


is_target = is_national_sejong_library_target


def national_sejong_library_list_url(page: int) -> str:
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise NationalSejongLibraryContractError("list page must be a positive integer")
    query = (
        ("codeId", "PRO043"),
        ("menuId", "O365"),
        ("upperMenuId", "O300"),
        ("sel", "O360"),
        ("pageIndex", str(page)),
        ("searchCondition", "2"),
        ("searchKeyword", ""),
        ("etc1", ""),
        ("progrmId", ""),
    )
    return f"https://{NATIONAL_SEJONG_LIBRARY_HOST}{NATIONAL_SEJONG_LIBRARY_LIST_PATH}?{urlencode(query)}"


def national_sejong_library_detail_url(identity: str) -> str:
    identity = _clean(identity)
    if not identity.isdigit() or len(identity) > 12:
        raise NationalSejongLibraryContractError("invalid programme identity")
    query = (
        ("progrmId", identity),
        ("menuId", "O365"),
        ("upperMenuId", "O300"),
        ("codeId", "PRO043"),
        ("etc1", ""),
        ("sel", "O360"),
    )
    return f"https://{NATIONAL_SEJONG_LIBRARY_HOST}{NATIONAL_SEJONG_LIBRARY_DETAIL_PATH}?{urlencode(query)}"


def _validate_request_url(url: str, *, detail_identity: str = "") -> None:
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    lowered_path = parsed.path.lower()
    if any(marker in lowered_path for marker in _FORBIDDEN_PATH_MARKERS):
        raise NationalSejongLibraryContractError("forbidden private endpoint request")
    common = bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == NATIONAL_SEJONG_LIBRARY_HOST
        and parsed.port is None
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )
    if not common:
        raise NationalSejongLibraryContractError("request left the reviewed HTTPS origin")
    if detail_identity:
        expected = {
            "progrmId": [detail_identity],
            "menuId": ["O365"],
            "upperMenuId": ["O300"],
            "codeId": ["PRO043"],
            "etc1": [""],
            "sel": ["O360"],
        }
        if parsed.path != NATIONAL_SEJONG_LIBRARY_DETAIL_PATH or query != expected:
            raise NationalSejongLibraryContractError("detail request identity/query changed")
        return
    expected_keys = {
        "codeId",
        "menuId",
        "upperMenuId",
        "sel",
        "pageIndex",
        "searchCondition",
        "searchKeyword",
        "etc1",
        "progrmId",
    }
    if parsed.path != NATIONAL_SEJONG_LIBRARY_LIST_PATH or set(query) != expected_keys:
        raise NationalSejongLibraryContractError("list request query changed")
    if (
        query["codeId"] != ["PRO043"]
        or query["menuId"] != ["O365"]
        or query["upperMenuId"] != ["O300"]
        or query["sel"] != ["O360"]
        or query["searchCondition"] != ["2"]
        or query["searchKeyword"] != [""]
        or query["etc1"] != [""]
        or query["progrmId"] != [""]
        or len(query["pageIndex"]) != 1
        or not query["pageIndex"][0].isdigit()
    ):
        raise NationalSejongLibraryContractError("list request parameters changed")


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
        }
    )
    return current


def _default_fetcher(session_obj: Any, url: str, timeout: int) -> Any:
    return session_obj.get(
        url,
        timeout=timeout,
        allow_redirects=False,
        verify=True,
    )


def _response_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, (str, bytes, bytearray)):
        if not value:
            raise NationalSejongLibraryContractError("empty HTML response")
        return BeautifulSoup(value, "lxml")
    status = int(getattr(value, "status_code", 200))
    if status != 200:
        raise NationalSejongLibraryContractError(f"unexpected HTTP status {status}")
    history = getattr(value, "history", ()) or ()
    if history:
        raise NationalSejongLibraryContractError("redirected response is not accepted")
    payload = getattr(value, "content", None)
    if not payload:
        payload = getattr(value, "text", "")
    if not payload:
        raise NationalSejongLibraryContractError("empty HTML response")
    return BeautifulSoup(payload, "lxml")


class _Requester:
    def __init__(
        self,
        session_factory: SessionFactory,
        fetcher: Fetcher,
        timeout: int,
    ) -> None:
        self.session = session_factory()
        self.fetcher = fetcher
        self.timeout = timeout
        self.list_requests = 0
        self.detail_requests = 0
        self.urls: list[str] = []

    def _get(self, url: str, *, detail_identity: str = "") -> BeautifulSoup:
        _validate_request_url(url, detail_identity=detail_identity)
        self.urls.append(url)
        return _response_soup(self.fetcher(self.session, url, self.timeout))

    def list_soup(self, page: int) -> BeautifulSoup:
        self.list_requests += 1
        return self._get(national_sejong_library_list_url(page))

    def detail_soup(self, identity: str) -> BeautifulSoup:
        self.detail_requests += 1
        return self._get(
            national_sejong_library_detail_url(identity),
            detail_identity=identity,
        )

    @property
    def requests(self) -> int:
        return self.list_requests + self.detail_requests

    def close(self) -> None:
        close = getattr(self.session, "close", None)
        if callable(close):
            close()


def _date_range(value: Any) -> tuple[str, date, date]:
    text = _clean(value)
    match = _DATE_RANGE_RE.fullmatch(text)
    if not match:
        raise NationalSejongLibraryContractError(f"invalid education period: {text}")
    values = [int(item) for item in match.groups()]
    start = date(values[0], values[1], values[2])
    end = date(values[3], values[4], values[5])
    if end < start:
        raise NationalSejongLibraryContractError("education period ends before it starts")
    return f"{start.isoformat()} ~ {end.isoformat()}", start, end


def _datetime_range(value: Any) -> tuple[str, datetime, datetime]:
    text = _clean(value)
    match = _DATETIME_RANGE_RE.fullmatch(text)
    if not match:
        raise NationalSejongLibraryContractError(f"invalid application period: {text}")
    values = [int(item) for item in match.groups()]
    timezone = ZoneInfo("Asia/Seoul")
    start = datetime(*values[:5], tzinfo=timezone)
    end = datetime(*values[5:], tzinfo=timezone)
    if end < start:
        raise NationalSejongLibraryContractError("application period ends before it starts")
    return (
        f"{start.strftime('%Y-%m-%d %H:%M')} ~ {end.strftime('%Y-%m-%d %H:%M')}",
        start,
        end,
    )


def _capacity(value: Any) -> tuple[int, int, int, int]:
    text = _clean(value)
    match = _CAPACITY_RE.fullmatch(text)
    if not match:
        raise NationalSejongLibraryContractError(f"invalid list capacity: {text}")
    current, total, wait_current, wait_total = (int(item) for item in match.groups())
    if total <= 0 or current < 0 or wait_current < 0 or wait_total < 0:
        raise NationalSejongLibraryContractError("invalid programme capacity values")
    return current, total, wait_current, wait_total


def _pair_capacity(value: Any, label: str) -> tuple[int, int]:
    text = _clean(value)
    match = _PAIR_CAPACITY_RE.fullmatch(text)
    if not match:
        raise NationalSejongLibraryContractError(f"invalid detail {label}: {text}")
    current, total = (int(item) for item in match.groups())
    if total < 0 or current < 0:
        raise NationalSejongLibraryContractError(f"invalid detail {label} values")
    return current, total


def _validate_list_form(soup: BeautifulSoup, requested_page: int) -> None:
    forms = [
        form
        for form in soup.select("form")
        if _clean(form.get("name")) == "listForm"
    ]
    if len(forms) != 1 or _clean(forms[0].get("method")).lower() != "post":
        raise NationalSejongLibraryContractError("programme search form changed")
    form = forms[0]
    action = urlparse(urljoin(NATIONAL_SEJONG_LIBRARY_CANONICAL_URL, _clean(form.get("action"))))
    try:
        _validate_request_url(action.geturl())
    except NationalSejongLibraryContractError:
        raise NationalSejongLibraryContractError("programme search action changed")
    action_query = parse_qs(action.query, keep_blank_values=True)
    if action_query.get("pageIndex") != [str(requested_page)]:
        raise NationalSejongLibraryContractError("programme search action page changed")
    for name, expected in _LIST_FORM_FIELDS.items():
        nodes = form.select(f'[name="{name}"]')
        if len(nodes) != 1 or _clean(nodes[0].get("value")) != expected:
            raise NationalSejongLibraryContractError(f"programme search field {name} changed")
    page_nodes = form.select('[name="pageIndex"]')
    if len(page_nodes) != 1 or _clean(page_nodes[0].get("value")) != str(requested_page):
        raise NationalSejongLibraryContractError("programme search page identity changed")
    search = form.select('[name="searchCondition"]')
    if len(search) != 1 or not search[0].select('option[value="2"]'):
        raise NationalSejongLibraryContractError("programme search condition changed")


def _declared_total(soup: BeautifulSoup, requested_page: int) -> tuple[int, int]:
    markers = soup.select(".board_tit .curpage")
    if len(markers) != 1:
        raise NationalSejongLibraryContractError("missing declared programme total")
    marker = _clean(markers[0].get_text(" ", strip=True))
    match = re.fullmatch(
        r"총\s*게시물\s*수\s*:\s*(\d+)\s*"
        r"현재\s*페이지\s*:\s*(\d+)\s*/\s*(\d+)",
        marker,
    )
    if not match:
        raise NationalSejongLibraryContractError("declared total marker changed")
    total, current, displayed_end = (int(item) for item in match.groups())
    pages = max(1, math.ceil(total / NATIONAL_SEJONG_LIBRARY_PAGE_SIZE))
    if current != requested_page:
        raise NationalSejongLibraryContractError(
            f"requested page {requested_page} returned page {current}"
        )
    expected_end = (
        pages
        if requested_page > pages
        else min(math.ceil(requested_page / 10) * 10, pages)
    )
    if displayed_end != expected_end:
        raise NationalSejongLibraryContractError("pagination block boundary changed")
    return total, pages


def _validate_paginator(soup: BeautifulSoup, requested_page: int, pages: int) -> None:
    containers = soup.select("div#paging.paginate")
    if len(containers) != 1:
        raise NationalSejongLibraryContractError("programme paginator changed")
    paginator = containers[0]
    current = paginator.select("a.on")
    if len(current) != 1 or _clean(current[0].get_text(" ", strip=True)) != str(requested_page):
        raise NationalSejongLibraryContractError("programme current-page marker changed")
    hidden = paginator.select('input#pageIndex[name="pageIndex"]')
    if len(hidden) != 1 or _clean(hidden[0].get("value")) != str(requested_page):
        raise NationalSejongLibraryContractError("programme paginator hidden page changed")
    numeric_links = 0
    for link in paginator.select("a[onclick]"):
        onclick = _clean(link.get("onclick"))
        match = _PAGE_LINK_RE.fullmatch(onclick)
        if not match:
            raise NationalSejongLibraryContractError("programme paginator link changed")
        linked_page = int(match.group(1))
        if linked_page < 1 or linked_page > pages:
            raise NationalSejongLibraryContractError("programme paginator leaves declared range")
        if _clean(link.get_text(" ", strip=True)).isdigit():
            numeric_links += 1
    if pages > 1 and numeric_links == 0:
        raise NationalSejongLibraryContractError("programme paginator has no numeric links")


def _parse_list_row(
    node: Any,
    *,
    requested_page: int,
    row_index: int,
    total: int,
) -> ListedProgramme:
    cells = node.find_all("td", recursive=False)
    if len(cells) != 8:
        raise NationalSejongLibraryContractError("programme table row shape changed")
    ordinal_text = _clean(cells[0].get_text(" ", strip=True))
    expected_ordinal = total - ((requested_page - 1) * NATIONAL_SEJONG_LIBRARY_PAGE_SIZE + row_index)
    if not ordinal_text.isdigit() or int(ordinal_text) != expected_ordinal:
        raise NationalSejongLibraryContractError("programme row ordinal changed")
    application_status = _clean(cells[1].get_text(" ", strip=True))
    education_status = _clean(cells[2].get_text(" ", strip=True))
    if application_status not in _APPLICATION_STATUS:
        raise NationalSejongLibraryContractError(
            f"unknown application status: {application_status}"
        )
    if education_status not in _EDUCATION_STATUS:
        raise NationalSejongLibraryContractError(
            f"unknown education status: {education_status}"
        )
    title_links = cells[3].select("a[href]")
    if len(title_links) != 1:
        raise NationalSejongLibraryContractError("programme detail identity is ambiguous")
    identity_match = _DETAIL_LINK_RE.fullmatch(_clean(title_links[0].get("href")))
    if not identity_match:
        raise NationalSejongLibraryContractError("programme detail control changed")
    identity = identity_match.group(1)
    title = _clean(title_links[0].get_text(" ", strip=True))
    if not title:
        raise NationalSejongLibraryContractError("programme title is empty")
    period, start, end = _date_range(cells[5].get_text(" ", strip=True))
    apply_period, apply_start, apply_end = _datetime_range(
        cells[6].get_text(" ", strip=True)
    )
    capacity_values = _capacity(cells[7].get_text(" ", strip=True))
    return ListedProgramme(
        identity=identity,
        ordinal=int(ordinal_text),
        title=title,
        application_status_raw=application_status,
        education_status_raw=education_status,
        status=_APPLICATION_STATUS[application_status],
        period=period,
        start=start,
        end=end,
        apply_period=apply_period,
        apply_start=apply_start,
        apply_end=apply_end,
        capacity_current=capacity_values[0],
        capacity_total=capacity_values[1],
        waitlist_current=capacity_values[2],
        waitlist_total=capacity_values[3],
        page=requested_page,
    )


def _parse_list_page(
    soup: BeautifulSoup,
    requested_page: int,
    *,
    sentinel: bool = False,
) -> ListPage:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if "모든 대상" not in title or NATIONAL_SEJONG_LIBRARY_BRANCH not in title:
        raise NationalSejongLibraryContractError("unexpected programme catalogue title")
    _validate_list_form(soup, requested_page)
    total, pages = _declared_total(soup, requested_page)
    tables = soup.select("table.board_table.applyT")
    if len(tables) != 1:
        raise NationalSejongLibraryContractError("programme list table changed")
    table = tables[0]
    captions = table.find_all("caption", recursive=False)
    if len(captions) != 1 or _clean(captions[0].get_text(" ", strip=True)) != "교육신청목록":
        raise NationalSejongLibraryContractError("programme list caption changed")
    headers = tuple(_clean(node.get_text(" ", strip=True)) for node in table.select("thead th"))
    if headers != _LIST_HEADERS:
        raise NationalSejongLibraryContractError("programme list headers changed")
    body = table.find("tbody", recursive=False)
    if body is None:
        raise NationalSejongLibraryContractError("programme list body is missing")
    nodes = body.find_all("tr", recursive=False)
    if sentinel:
        if requested_page != pages + 1 or nodes:
            raise NationalSejongLibraryContractError("post-last page is not the exact empty sentinel")
        containers = soup.select("div#paging.paginate")
        if len(containers) != 1 or containers[0].select("a.on"):
            raise NationalSejongLibraryContractError("empty sentinel current-page marker changed")
        hidden = containers[0].select('input#pageIndex[name="pageIndex"]')
        if len(hidden) != 1 or _clean(hidden[0].get("value")) != str(requested_page):
            raise NationalSejongLibraryContractError("empty sentinel page identity changed")
        for link in containers[0].select("a[onclick]"):
            match = _PAGE_LINK_RE.fullmatch(_clean(link.get("onclick")))
            if not match or not 1 <= int(match.group(1)) <= pages:
                raise NationalSejongLibraryContractError("empty sentinel paginator changed")
        return ListPage(requested_page, total, pages, ())
    if requested_page < 1 or requested_page > pages:
        raise NationalSejongLibraryContractError("requested data page is outside declared range")
    _validate_paginator(soup, requested_page, pages)
    expected_rows = min(
        NATIONAL_SEJONG_LIBRARY_PAGE_SIZE,
        max(0, total - (requested_page - 1) * NATIONAL_SEJONG_LIBRARY_PAGE_SIZE),
    )
    if len(nodes) != expected_rows:
        raise NationalSejongLibraryContractError(
            f"page {requested_page} has {len(nodes)} rows, expected {expected_rows}"
        )
    rows = tuple(
        _parse_list_row(
            node,
            requested_page=requested_page,
            row_index=index,
            total=total,
        )
        for index, node in enumerate(nodes)
    )
    return ListPage(requested_page, total, pages, rows)


def _direct_detail_pairs(table: Any) -> tuple[dict[str, str], list[Any]]:
    body = table.find("tbody", recursive=False)
    if body is None:
        raise NationalSejongLibraryContractError("public detail body is missing")
    rows = body.find_all("tr", recursive=False)
    if len(rows) < 5:
        raise NationalSejongLibraryContractError("public detail row structure changed")
    expected_keys = (
        ("제목",),
        ("강사", "장소"),
        ("정원", "대기 정원"),
        ("기간", "시간"),
    )
    pairs: dict[str, str] = {}
    for row, keys in zip(rows[:4], expected_keys):
        cells = row.find_all(["th", "td"], recursive=False)
        found_keys = tuple(
            _clean(cell.get_text(" ", strip=True))
            for cell in cells
            if cell.name == "th"
        )
        if found_keys != keys:
            raise NationalSejongLibraryContractError("public detail fields changed")
        for index, cell in enumerate(cells):
            if cell.name != "th":
                continue
            if index + 1 >= len(cells) or cells[index + 1].name != "td":
                raise NationalSejongLibraryContractError("public detail field pairing changed")
            pairs[_clean(cell.get_text(" ", strip=True))] = _clean(
                cells[index + 1].get_text(" ", strip=True)
            )
    content_cells = rows[4].find_all("td", recursive=False)
    if len(content_cells) != 1 or content_cells[0].select_one("div.viewbox") is None:
        raise NationalSejongLibraryContractError("public detail content boundary changed")
    return pairs, rows[4:]


def _detail_form_contract(
    soup: BeautifulSoup,
    listed: ListedProgramme,
) -> None:
    forms = [
        form
        for form in soup.select("form")
        if _clean(form.get("name")) == "progrm"
    ]
    if len(forms) != 1 or _clean(forms[0].get("method")).lower() != "post":
        raise NationalSejongLibraryContractError("course-bound application form changed")
    form = forms[0]
    if _clean(form.get("action")):
        raise NationalSejongLibraryContractError("application form unexpectedly exposes an endpoint")
    expected = {
        "progrmId": listed.identity,
        "partcptPsncpa": str(listed.capacity_total),
        "waitPsncpa": str(listed.waitlist_total),
        "codeId": "PRO043",
        "menuId": "O365",
        "upperMenuId": "O300",
        "startDt": listed.apply_start.date().isoformat(),
        "endDt": listed.apply_end.date().isoformat(),
    }
    for name, value in expected.items():
        nodes = form.select(f'input[name="{name}"]')
        if len(nodes) != 1 or _clean(nodes[0].get("value")) != value:
            raise NationalSejongLibraryContractError(
                f"course-bound application field {name} changed"
            )


def _row_from_detail(
    target: Any,
    listed: ListedProgramme,
    soup: BeautifulSoup,
) -> dict[str, Any]:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if "모든 대상" not in title or NATIONAL_SEJONG_LIBRARY_BRANCH not in title:
        raise NationalSejongLibraryContractError("unexpected public detail title")
    tables = soup.select("table.boardView")
    if len(tables) != 1:
        raise NationalSejongLibraryContractError("expected one public detail table")
    table = tables[0]
    captions = table.find_all("caption", recursive=False)
    if (
        len(captions) != 1
        or _clean(captions[0].get_text(" ", strip=True))
        != "교육 내용을 상세하게 작성한 표"
    ):
        raise NationalSejongLibraryContractError("public detail caption changed")
    pairs, ignored_rows = _direct_detail_pairs(table)
    if set(pairs) != {"제목", "강사", "장소", "정원", "대기 정원", "기간", "시간"}:
        raise NationalSejongLibraryContractError("public structured detail fields changed")
    if _normalized(pairs["제목"]) != _normalized(listed.title):
        raise NationalSejongLibraryContractError("list/detail programme title mismatch")
    period, start, end = _date_range(pairs["기간"])
    if period != listed.period or start != listed.start or end != listed.end:
        raise NationalSejongLibraryContractError("list/detail education period mismatch")
    capacity_current, capacity_total = _pair_capacity(pairs["정원"], "capacity")
    wait_current, wait_total = _pair_capacity(pairs["대기 정원"], "wait capacity")
    if (
        capacity_current != listed.capacity_current
        or capacity_total != listed.capacity_total
        or wait_current != listed.waitlist_current
        or wait_total != listed.waitlist_total
    ):
        raise NationalSejongLibraryContractError("list/detail capacity mismatch")
    schedule = _clean(pairs["시간"])
    if not re.fullmatch(r"\d{1,2}:\d{2}\s*~\s*\d{1,2}:\d{2}", schedule):
        raise NationalSejongLibraryContractError("public detail time changed")
    room = _clean(pairs["장소"])
    if not room:
        raise NationalSejongLibraryContractError("public detail venue is empty")
    _detail_form_contract(soup, listed)
    raw_url = national_sejong_library_detail_url(listed.identity)
    application_url = raw_url if listed.status == "OPEN" else ""
    del ignored_rows  # Body and attachment rows are intentionally never read.
    return {
        "provider": NATIONAL_SEJONG_LIBRARY_PROVIDER,
        "provider_course_id": (
            f"{NATIONAL_SEJONG_LIBRARY_PROVIDER}:program:{listed.identity}"
        ),
        "title": listed.title,
        "branch": NATIONAL_SEJONG_LIBRARY_BRANCH,
        "branch_code": (
            f"{NATIONAL_SEJONG_LIBRARY_PROVIDER}:national-sejong-library"
        ),
        "preserve_branch": True,
        "address": NATIONAL_SEJONG_LIBRARY_ADDRESS,
        "branch_lat": NATIONAL_SEJONG_LIBRARY_LATITUDE,
        "branch_lon": NATIONAL_SEJONG_LIBRARY_LONGITUDE,
        "branch_coordinate_source": NATIONAL_SEJONG_LIBRARY_COORDINATE_SOURCE,
        "branch_location_confidence": 100,
        "branch_location_verified": True,
        "branch_location_query": NATIONAL_SEJONG_LIBRARY_ADDRESS,
        "venue_name": NATIONAL_SEJONG_LIBRARY_BRANCH,
        "room": room,
        "raw_url": raw_url,
        "application_url": application_url,
        "application_type": "ONLINE_RESERVATION" if application_url else "INFO_ONLY",
        "reservation_available": bool(application_url),
        "status": listed.status,
        "is_active": True,
        "period": listed.period,
        "start_date": listed.start,
        "end_date": listed.end,
        "schedule_raw": schedule,
        "apply_period": listed.apply_period,
        "apply_start_at": listed.apply_start,
        "apply_end_at": listed.apply_end,
        "apply_start_date": listed.apply_start.date(),
        "apply_end_date": listed.apply_end.date(),
        "capacity": listed.capacity_total,
        "capacity_current": listed.capacity_current,
        "capacity_total": listed.capacity_total,
        "capacity_remaining": max(0, listed.capacity_total - listed.capacity_current),
        "waitlist_current": listed.waitlist_current,
        "waitlist_total": listed.waitlist_total,
        "target": "모든 대상",
        "category": "특별프로그램",
        "program_type": "교육",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "source_group": "library",
        "operator_type": "국립/공공기관",
        "municipality_code": NATIONAL_SEJONG_LIBRARY_MUNICIPALITY_CODE,
        "municipality_name": NATIONAL_SEJONG_LIBRARY_MUNICIPALITY_NAME,
        "municipality_full_name": NATIONAL_SEJONG_LIBRARY_MUNICIPALITY_NAME,
        "municipality_region_verified": True,
        "region_sido": NATIONAL_SEJONG_LIBRARY_MUNICIPALITY_NAME,
        "region_sigungu": NATIONAL_SEJONG_LIBRARY_MUNICIPALITY_NAME,
        "raw_fields": {
            "source_kind": "national_sejong_library_pro043",
            "progrm_id": listed.identity,
            "source_page": listed.page,
            "source_application_status": listed.application_status_raw,
            "source_education_status": listed.education_status_raw,
            "detail_verified": True,
            "structured_course_row_verified": True,
            "application_control_present": bool(application_url),
            "application_route_identity_verified_not_stored": True,
            "application_endpoint_fetched": False,
            "capacity_check_endpoint_fetched": False,
            "authentication_endpoint_fetched": False,
            "attachment_endpoint_fetched": False,
            "pii_endpoint_fetched": False,
            "freeform_body_copied": False,
            "instructor_copied": False,
        },
    }


def _base_meta() -> dict[str, Any]:
    return {
        "parser": NATIONAL_SEJONG_LIBRARY_PARSER,
        "pages": 0,
        "data_pages": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "request_count": 0,
        "source_rows": 0,
        "declared_source_rows": 0,
        "valid_count": 0,
        "invalid_count": 0,
        "duplicate_count": 0,
        "expired_count": 0,
        "current_count": 0,
        "returned_count": 0,
        "detail_candidates": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "empty_sentinel_verified": False,
        "stable_boundaries": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "no_current_reason": "",
        "branch_counts": {},
        "status_counts": {},
        "application_control_count": 0,
        "application_endpoint_requests": 0,
        "capacity_check_endpoint_requests": 0,
        "authentication_endpoint_requests": 0,
        "attachment_endpoint_requests": 0,
        "pii_endpoint_requests": 0,
        "http_methods": ["GET"],
        "canonical_provider": NATIONAL_SEJONG_LIBRARY_PROVIDER,
        "canonical_url": NATIONAL_SEJONG_LIBRARY_CANONICAL_URL,
        "ownership_scope": NATIONAL_SEJONG_LIBRARY_OWNERSHIP_SCOPE,
        "covered_municipalities": [
            {
                "code": NATIONAL_SEJONG_LIBRARY_MUNICIPALITY_CODE,
                "sido": NATIONAL_SEJONG_LIBRARY_MUNICIPALITY_NAME,
                "sigungu": NATIONAL_SEJONG_LIBRARY_MUNICIPALITY_NAME,
                "full_name": NATIONAL_SEJONG_LIBRARY_MUNICIPALITY_NAME,
            }
        ],
        "configured_collection_error": "",
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


def collect_national_sejong_library_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 30,
    detail_limit: int = 100,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one atomic current/future PRO043 education snapshot."""

    meta = _base_meta()
    if not is_national_sejong_library_target(target):
        meta["configured_collection_error"] = (
            "target does not match the exact National Sejong Library PRO043 catalogue"
        )
        return [], NATIONAL_SEJONG_LIBRARY_PARSER, meta
    try:
        timeout_value = int(timeout)
        page_cap = int(max_pages)
        detail_cap = int(detail_limit)
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["configured_collection_error"] = f"invalid arguments: {_clean(exc)}"
        return [], NATIONAL_SEJONG_LIBRARY_PARSER, meta
    if timeout_value < 1 or page_cap < 1 or detail_cap < 0:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = "invalid timeout/max_pages/detail_limit cap"
        return [], NATIONAL_SEJONG_LIBRARY_PARSER, meta
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            meta["configured_collection_error"] = "managed session_factory injection is required"
            return [], NATIONAL_SEJONG_LIBRARY_PARSER, meta
        session_factory = _default_session_factory
    selected_fetcher = fetcher or _default_fetcher
    requester: Optional[_Requester] = None
    try:
        requester = _Requester(session_factory, selected_fetcher, timeout_value)
        first = _parse_list_page(requester.list_soup(1), 1)
        required_list_requests = first.last_page + 3
        meta.update(
            {
                "declared_source_rows": first.total,
                "source_pages": first.last_page,
                "required_list_requests": required_list_requests,
            }
        )
        if required_list_requests > page_cap:
            meta["source_cap_reached"] = True
            raise NationalSejongLibraryContractError(
                f"max_pages={page_cap} is below required list requests={required_list_requests}"
            )
        pages = [first]
        for page_number in range(2, first.last_page + 1):
            page = _parse_list_page(requester.list_soup(page_number), page_number)
            if page.total != first.total or page.last_page != first.last_page:
                raise NationalSejongLibraryContractError(
                    "declared total/page count changed during pagination"
                )
            pages.append(page)
        sentinel_number = first.last_page + 1
        sentinel = _parse_list_page(
            requester.list_soup(sentinel_number),
            sentinel_number,
            sentinel=True,
        )
        if sentinel.total != first.total or sentinel.last_page != first.last_page:
            raise NationalSejongLibraryContractError("empty sentinel total/pages changed")
        meta["empty_sentinel_verified"] = True
        listed = [row for page in pages for row in page.rows]
        if len(listed) != first.total:
            raise NationalSejongLibraryContractError(
                f"complete source count mismatch: parsed={len(listed)} declared={first.total}"
            )
        identities = [row.identity for row in listed]
        duplicate_count = len(identities) - len(set(identities))
        meta.update(
            {
                "source_rows": len(listed),
                "valid_count": len(listed),
                "duplicate_count": duplicate_count,
            }
        )
        if duplicate_count:
            raise NationalSejongLibraryContractError(
                f"{duplicate_count} duplicate programme identities"
            )
        current = [row for row in listed if row.end >= cutoff]
        meta.update(
            {
                "expired_count": len(listed) - len(current),
                "current_count": len(current),
                "detail_candidates": len(current),
                "detail_attempts": len(current),
            }
        )
        if len(current) > detail_cap:
            meta["source_cap_reached"] = True
            raise NationalSejongLibraryContractError(
                f"detail_limit={detail_cap} is below current rows={len(current)}"
            )
        rows: list[dict[str, Any]] = []
        for listed_row in current:
            try:
                rows.append(
                    _row_from_detail(
                        target,
                        listed_row,
                        requester.detail_soup(listed_row.identity),
                    )
                )
            except Exception as exc:
                meta["detail_errors"] += 1
                raise NationalSejongLibraryContractError(
                    f"detail {listed_row.identity}: {type(exc).__name__}: {_clean(exc)}"
                ) from exc
        repeated_first = _parse_list_page(requester.list_soup(1), 1)
        if repeated_first.stable_signature() != first.stable_signature():
            raise NationalSejongLibraryContractError(
                "first boundary changed during detail crawl"
            )
        final = pages[-1]
        repeated_final = _parse_list_page(
            requester.list_soup(final.requested_page),
            final.requested_page,
        )
        if repeated_final.stable_signature() != final.stable_signature():
            raise NationalSejongLibraryContractError(
                "final boundary changed during detail crawl"
            )
        meta["stable_boundaries"] = True
        rows.sort(key=lambda row: _clean(row.get("provider_course_id")))
        selected_dedupe = dedupe_rows or _dedupe_default
        deduped = list(selected_dedupe(rows))
        if len(deduped) != len(rows):
            raise NationalSejongLibraryContractError(
                "downstream dedupe changed the complete current snapshot"
            )
        rows = deduped
        branch_counts = Counter(_clean(row.get("branch")) for row in rows)
        status_counts = Counter(_clean(row.get("status")) for row in rows)
        meta.update(
            {
                "pages": requester.list_requests,
                "data_pages": first.last_page,
                "list_requests": requester.list_requests,
                "request_count": requester.requests,
                "returned_count": len(rows),
                "detail_pages": requester.detail_requests,
                "pagination_complete": True,
                "details_complete": requester.detail_requests == len(current),
                "snapshot_complete": True,
                "no_current_data": not rows,
                "no_current_reason": (
                    "all complete PRO043 programme rows are expired" if not rows else ""
                ),
                "branch_counts": dict(branch_counts),
                "status_counts": dict(status_counts),
                "application_control_count": sum(
                    bool(row.get("application_url")) for row in rows
                ),
            }
        )
        return rows, NATIONAL_SEJONG_LIBRARY_PARSER, meta
    except Exception as exc:
        if requester is not None:
            meta.update(
                {
                    "pages": requester.list_requests,
                    "list_requests": requester.list_requests,
                    "request_count": requester.requests,
                    "detail_pages": requester.detail_requests,
                }
            )
        meta["snapshot_complete"] = False
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        return [], NATIONAL_SEJONG_LIBRARY_PARSER, meta
    finally:
        if requester is not None:
            requester.close()


collect = collect_national_sejong_library_courses


__all__ = [
    "NATIONAL_SEJONG_LIBRARY_ADDRESS",
    "NATIONAL_SEJONG_LIBRARY_BRANCH",
    "NATIONAL_SEJONG_LIBRARY_CANONICAL_URL",
    "NATIONAL_SEJONG_LIBRARY_CANDIDATE_ID",
    "NATIONAL_SEJONG_LIBRARY_DETAIL_PATH",
    "NATIONAL_SEJONG_LIBRARY_HOST",
    "NATIONAL_SEJONG_LIBRARY_LIST_PATH",
    "NATIONAL_SEJONG_LIBRARY_MUNICIPALITY_CODE",
    "NATIONAL_SEJONG_LIBRARY_MUNICIPALITY_NAME",
    "NATIONAL_SEJONG_LIBRARY_OWNERSHIP_SCOPE",
    "NATIONAL_SEJONG_LIBRARY_PAGE_SIZE",
    "NATIONAL_SEJONG_LIBRARY_PARSER",
    "NATIONAL_SEJONG_LIBRARY_PROVIDER",
    "NATIONAL_SEJONG_LIBRARY_URL",
    "NationalSejongLibraryContractError",
    "collect",
    "collect_national_sejong_library_courses",
    "is_national_sejong_library_target",
    "is_target",
    "national_sejong_library_detail_url",
    "national_sejong_library_list_url",
]
