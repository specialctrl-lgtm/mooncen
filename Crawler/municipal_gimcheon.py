"""Fail-closed collector for Gimcheon Lifelong Learning Center education.

The public education owner consists of the five sibling programme sections
under ``/welfare``.  The former discovery URL is the authenticated personal
application-history page, not a public course ledger.  This collector keeps
the incumbent provider but requires its target to be retargeted to the
regular-course section, which is the canonical entry point for the complete
five-section ledger.

Every section is walked through its advertised last page.  A request for the
exact next page must be structurally empty and the first, last, and sentinel
pages must remain stable on recheck.  Current/future rows are then bound to
their detail pages by ``operNo``.  Registration controls are inspected, but
application, authentication, and applicant-list endpoints are never fetched.
Instructor fields, free-text bodies, attachments, contact details, and images
are intentionally excluded from returned rows.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


GIMCHEON_HOST = "www.gc.go.kr"
GIMCHEON_PROVIDER = "MUNI_WWW_GC_GO_KR_91618000"
GIMCHEON_MUNICIPALITY_CODE = "4715000000"
GIMCHEON_MUNICIPALITY_NAME = "경상북도 김천시"
GIMCHEON_BRANCH = "김천시평생교육원"
GIMCHEON_ADDRESS = "경상북도 김천시 공단2길 30-22"
GIMCHEON_CANONICAL_CANDIDATE_ID = "MUNI_IR_25705DD6ADAA"
GIMCHEON_OLD_CANDIDATE_ID = "MUNI_IR_A70EA9B39330"
GIMCHEON_CANONICAL_URL = "https://www.gc.go.kr/welfare/page/10023/10016.tc"
GIMCHEON_OLD_URL = "https://www.gc.go.kr/welfare/page/10049/1008.tc"
GIMCHEON_PAGE_SIZE = 5
GIMCHEON_MAX_PAGES = 10
GIMCHEON_DETAIL_LIMIT = 100
GIMCHEON_FETCH_ATTEMPTS = 2
GIMCHEON_OWNERSHIP_SCOPE = (
    "official_gimcheon_lifelong_learning_center_five_education_sections"
)
GIMCHEON_PARSER = (
    "gimcheon_complete_five_section_education+exact_owner_menu_vocabulary+"
    "advertised_last_page+exact_empty_post_last_sentinel+stable_boundaries+"
    "current_detail_oper_no_binding+registration_state_control_validation+"
    "no_application_or_applicant_endpoint_fetch+pii_allowlist"
)

GIMCHEON_CANDIDATE_DECISIONS: Mapping[str, str] = {
    GIMCHEON_CANONICAL_CANDIDATE_ID: (
        "promote_and_retarget_disabled_existing_provider_to_complete_education_owner"
    ),
    GIMCHEON_OLD_CANDIDATE_ID: (
        "exclude_old_url_but_reuse_incumbent_provider_for_canonical_owner"
    ),
}
GIMCHEON_EXCLUDED_OWNER_BOUNDARIES: Mapping[str, str] = {
    GIMCHEON_OLD_URL: (
        "authenticated_personal_education_application_history_not_public_course_ledger"
    )
}


@dataclass(frozen=True)
class GimcheonSection:
    key: str
    label: str
    menu_no: str
    page_no: str
    target_code: str
    detail_order: str

    @property
    def url(self) -> str:
        return (
            f"https://{GIMCHEON_HOST}/welfare/page/"
            f"{self.menu_no}/{self.page_no}.tc"
        )

    @property
    def menu_href(self) -> str:
        return (
            f"/welfare/page/link.tc?mn={self.menu_no}&pageNo={self.page_no}"
        )


GIMCHEON_SECTIONS = (
    GimcheonSection("regular", "평생교육 정기강좌", "10023", "10016", "RMS003001", "2"),
    GimcheonSection("rolling", "수시강좌", "10024", "10017", "RMS003003", "2"),
    GimcheonSection("social", "사회적배려계층", "10071", "1007", "RMS003010", "1"),
    GimcheonSection("women", "김천시여성대학", "10025", "10018", "RMS003002", "2"),
    GimcheonSection("humanities", "핵심 인문학 특강", "10026", "10019", "RMS003011", "2"),
)
GIMCHEON_SECTION_BY_KEY = {section.key: section for section in GIMCHEON_SECTIONS}
GIMCHEON_ACTIVE_MENU_LABELS = tuple(section.label for section in GIMCHEON_SECTIONS)


class GimcheonContractError(RuntimeError):
    """Raised when the audited Gimcheon public contract changes."""


@dataclass(frozen=True)
class _Registration:
    rcpt_no: str
    source_state: str
    source_label: str
    kind: str
    apply_period: str
    apply_start: date
    apply_end: date
    capacity: str
    waiting_capacity: str
    fee: str


@dataclass(frozen=True)
class _ListedCourse:
    section_key: str
    identity: str
    title: str
    detail_url: str
    subcategory: str
    application_method: str
    venue: str
    period: str
    event_start: date
    event_end: date
    schedule_time: str
    schedule_days: str
    registrations: tuple[_Registration, ...]
    page: int


@dataclass(frozen=True)
class _Page:
    requested_page: int
    advertised_last_page: int
    active_page: Optional[int]
    rows: tuple[_ListedCourse, ...]


Fetcher = Callable[..., Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})(?:\.)?(?!\d)")
_DETAIL_ONCLICK_RE = re.compile(r"^eduList\.detail\(\s*'(\d+)'\s*\)$")
_LIST_APPLY_RE = re.compile(r"^eduList\.apply\(\s*'(\d+)'\s*,\s*'([A-Z])'\s*\)$")
_DETAIL_APPLY_RE = re.compile(r"^eduDetail\.apply\(\s*'(\d+)'\s*,\s*'([A-Z])'\s*\)$")
_PAGE_MOVE_RE = re.compile(r"eduList\.pageMove\(\s*(\d+)\s*\)")
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,3}[-\s)]?\d{3,4}[-\s]?\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_LIST_FIELD_LABELS = (
    "상세분류",
    "접수방법",
    "강의실",
    "교육기간",
    "교육시간",
    "교육요일",
)
_REGISTRATION_FIELD_LABELS = ("기 간", "정 원", "후 보", "교 육 비")
_STATE_STATUS = {"I": "OPEN", "W": "SCHEDULED", "E": "CLOSED"}
_STATE_LABELS = {
    "I": frozenset({"접수", "접수중", "신청"}),
    "W": frozenset({"대기", "접수대기", "접수예정"}),
    "E": frozenset({"마감", "접수마감", "종료"}),
}


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider"))


def _target_url(target: Any) -> str:
    return _clean(_target_value(target, "url"))


def _canonical_url_key(url: str) -> tuple[str, str, str, tuple[tuple[str, tuple[str, ...]], ...]]:
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    return (
        parsed.scheme.lower(),
        (parsed.hostname or "").rstrip(".").lower(),
        parsed.path,
        tuple(sorted((key, tuple(values)) for key, values in query.items())),
    )


def is_gimcheon_education_target(target: Any) -> bool:
    parsed = urlparse(_target_url(target))
    return bool(
        _provider(target) == GIMCHEON_PROVIDER
        and parsed.scheme.lower() == "https"
        and parsed.port is None
        and not parsed.username
        and not parsed.password
        and not parsed.params
        and not parsed.fragment
        and _canonical_url_key(_target_url(target))
        == _canonical_url_key(GIMCHEON_CANONICAL_URL)
    )


is_target = is_gimcheon_education_target


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is not None:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
    )
    return current


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=True)


def _coerce_html(result: Any, requested_url: str) -> BeautifulSoup:
    final_url = requested_url
    if isinstance(result, BeautifulSoup):
        soup = result
    elif isinstance(result, (str, bytes)):
        soup = BeautifulSoup(result, "html.parser")
    else:
        status = int(getattr(result, "status_code", 200))
        if status != 200:
            raise GimcheonContractError(f"HTTP {status} for {requested_url}")
        final_url = _clean(getattr(result, "url", requested_url)) or requested_url
        soup = BeautifulSoup(getattr(result, "text", ""), "html.parser")
    parsed = urlparse(final_url)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != GIMCHEON_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
    ):
        raise GimcheonContractError(f"unexpected redirect target: {final_url}")
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if "김천시평생교육원" not in title or "오류" in title:
        raise GimcheonContractError(f"unexpected or missing page title: {title}")
    return soup


def _fetch_soup(
    session: Any,
    url: str,
    timeout: int,
    fetcher: Fetcher,
    meta: dict[str, Any],
) -> BeautifulSoup:
    meta["logical_requests"] = int(meta.get("logical_requests") or 0) + 1
    error: Optional[Exception] = None
    for _ in range(GIMCHEON_FETCH_ATTEMPTS):
        meta["physical_requests"] = int(meta.get("physical_requests") or 0) + 1
        try:
            return _coerce_html(fetcher(session, url, timeout), url)
        except Exception as exc:  # retried, then the complete snapshot fails closed
            error = exc
    meta["request_retry_count"] = int(meta["physical_requests"]) - int(meta["logical_requests"])
    if isinstance(error, GimcheonContractError):
        raise error
    raise GimcheonContractError(
        f"request failed after retries for {url}: {_clean(error)}"
    ) from error


def _section_page_url(section: GimcheonSection, page: int) -> str:
    if page < 1:
        raise GimcheonContractError("page must be positive")
    if page == 1:
        return section.url
    query = {
        "importUrl": "/edu/list.tc",
        "pageDtlOrdrNo": section.detail_order,
        "pageIndex": str(page),
        "operNo": "0",
        "rcptNo": "0",
        "pageNo": section.page_no,
        "searchTrgtSeCd": section.target_code,
    }
    return f"{section.url}?{urlencode(query)}"


def _detail_url(section: GimcheonSection, identity: str) -> str:
    if not identity.isdigit() or int(identity) < 1:
        raise GimcheonContractError(f"{section.key}: invalid operNo {identity!r}")
    query = {
        "importUrl": "/edu/detail.tc",
        "pageDtlOrdrNo": section.detail_order,
        "operNo": identity,
        "rcptNo": "0",
        "pageNo": section.page_no,
        "searchTrgtSeCd": section.target_code,
    }
    return f"{section.url}?{urlencode(query)}"


def _input_value(form: BeautifulSoup, name: str) -> str:
    fields = form.select(f"input[name='{name}']")
    if len(fields) != 1:
        raise GimcheonContractError(f"form field {name!r} count changed: {len(fields)}")
    return _clean(fields[0].get("value"))


def _validate_list_form(
    soup: BeautifulSoup,
    section: GimcheonSection,
    requested_page: int,
) -> None:
    forms = soup.select("form#eduListForm")
    if len(forms) != 1:
        raise GimcheonContractError(
            f"{section.key} page {requested_page}: eduListForm count changed"
        )
    form = forms[0]
    expected = {
        "pageDtlOrdrNo": section.detail_order,
        "pageNo": section.page_no,
        "searchTrgtSeCd": section.target_code,
        "operNo": "0",
        "rcptNo": "0",
    }
    for name, value in expected.items():
        if _input_value(form, name) != value:
            raise GimcheonContractError(
                f"{section.key} page {requested_page}: {name} contract changed"
            )
    page_value = _input_value(form, "pageIndex")
    if requested_page == 1:
        if page_value not in {"", "1"}:
            raise GimcheonContractError(f"{section.key}: unexpected first-page value")
    elif page_value != str(requested_page):
        raise GimcheonContractError(
            f"{section.key} page {requested_page}: pageIndex binding mismatch"
        )


def _date_range(value: str, label: str) -> tuple[date, date]:
    matches = _DATE_RE.findall(_clean(value))
    if len(matches) != 2:
        raise GimcheonContractError(f"{label}: expected exactly two dates: {_clean(value)}")
    try:
        start, end = (date(int(year), int(month), int(day)) for year, month, day in matches)
    except ValueError as exc:
        raise GimcheonContractError(f"{label}: invalid date: {_clean(value)}") from exc
    if end < start:
        raise GimcheonContractError(f"{label}: end precedes start")
    return start, end


def _course_fields(card: BeautifulSoup, label: str) -> dict[str, str]:
    output: dict[str, str] = {}
    for item in card.select(".desc ul.list > li"):
        key_node = item.select_one(".tit")
        value_node = item.select_one(".txt")
        if key_node is None or value_node is None:
            raise GimcheonContractError(f"{label}: malformed course field")
        key = _clean(key_node.get_text(" ", strip=True))
        value = _clean(value_node.get_text(" ", strip=True))
        if not key or key in output:
            raise GimcheonContractError(f"{label}: duplicate or empty course field")
        output[key] = value
    if set(output) != set(_LIST_FIELD_LABELS) or any(not output[key] for key in output):
        raise GimcheonContractError(
            f"{label}: course field vocabulary changed: {tuple(output)}"
        )
    return output


def _registration_fields(item: BeautifulSoup, label: str) -> dict[str, str]:
    output: dict[str, str] = {}
    for row in item.select("ol > li"):
        key_node = row.find("em", recursive=False)
        value_node = row.find("span", recursive=False)
        if key_node is None or value_node is None:
            raise GimcheonContractError(f"{label}: malformed registration field")
        key = _clean(key_node.get_text(" ", strip=True))
        value = _clean(value_node.get_text(" ", strip=True))
        if not key or key in output:
            raise GimcheonContractError(f"{label}: duplicate registration field")
        output[key] = value
    if tuple(output) != _REGISTRATION_FIELD_LABELS or any(not output[key] for key in output):
        raise GimcheonContractError(
            f"{label}: registration vocabulary changed: {tuple(output)}"
        )
    return output


def _parse_registrations(
    card: BeautifulSoup,
    *,
    label: str,
    detail: bool,
) -> tuple[_Registration, ...]:
    prefix = _DETAIL_APPLY_RE if detail else _LIST_APPLY_RE
    output: list[_Registration] = []
    for item in card.select("ul.accept > li"):
        anchors = item.find_all("a", recursive=False)
        if len(anchors) != 1:
            raise GimcheonContractError(f"{label}: registration anchor count changed")
        anchor = anchors[0]
        match = prefix.fullmatch(_clean(anchor.get("onclick")))
        if match is None:
            raise GimcheonContractError(f"{label}: registration onclick contract changed")
        rcpt_no, source_state = match.groups()
        if source_state not in _STATE_STATUS:
            raise GimcheonContractError(f"{label}: unknown registration state {source_state}")
        kind_node = anchor.find("p", recursive=False)
        badges = anchor.find_all("span", recursive=False)
        if kind_node is None or len(badges) != 1:
            raise GimcheonContractError(f"{label}: registration label structure changed")
        kind = _clean(kind_node.get_text(" ", strip=True))
        source_label = _clean(badges[0].get_text(" ", strip=True))
        if not kind or source_label not in _STATE_LABELS[source_state]:
            raise GimcheonContractError(
                f"{label}: state/label mismatch {source_state}/{source_label}"
            )
        fields = _registration_fields(item, label)
        apply_start, apply_end = _date_range(fields["기 간"], f"{label} 접수기간")
        output.append(
            _Registration(
                rcpt_no=rcpt_no,
                source_state=source_state,
                source_label=source_label,
                kind=kind,
                apply_period=fields["기 간"],
                apply_start=apply_start,
                apply_end=apply_end,
                capacity=fields["정 원"],
                waiting_capacity=fields["후 보"],
                fee=fields["교 육 비"],
            )
        )
    if not output:
        raise GimcheonContractError(f"{label}: course lacks a registration ledger")
    numbers = [item.rcpt_no for item in output]
    if len(numbers) != len(set(numbers)):
        raise GimcheonContractError(f"{label}: duplicate registration identities")
    return tuple(output)


def _parse_card(
    card: BeautifulSoup,
    section: GimcheonSection,
    page: int,
) -> _ListedCourse:
    detail_links = card.select("a[onclick^='eduList.detail']")
    if len(detail_links) != 1:
        raise GimcheonContractError(
            f"{section.key} page {page}: detail control count changed"
        )
    match = _DETAIL_ONCLICK_RE.fullmatch(_clean(detail_links[0].get("onclick")))
    if match is None:
        raise GimcheonContractError(f"{section.key} page {page}: malformed detail control")
    identity = match.group(1)
    title_node = card.select_one(".desc .top .left > .tit")
    title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
    if not title:
        raise GimcheonContractError(f"{section.key}:{identity}: empty course title")
    fields = _course_fields(card, f"{section.key}:{identity}")
    event_start, event_end = _date_range(
        fields["교육기간"], f"{section.key}:{identity} 교육기간"
    )
    registrations = _parse_registrations(
        card, label=f"{section.key}:{identity}", detail=False
    )
    return _ListedCourse(
        section_key=section.key,
        identity=identity,
        title=title,
        detail_url=_detail_url(section, identity),
        subcategory=fields["상세분류"],
        application_method=fields["접수방법"],
        venue=fields["강의실"],
        period=fields["교육기간"],
        event_start=event_start,
        event_end=event_end,
        schedule_time=fields["교육시간"],
        schedule_days=fields["교육요일"],
        registrations=registrations,
        page=page,
    )


def _advertised_last_page(soup: BeautifulSoup) -> int:
    values = {1}
    for link in soup.select(".pager a[onclick]"):
        match = _PAGE_MOVE_RE.search(_clean(link.get("onclick")))
        if match:
            values.add(int(match.group(1)))
    return max(values)


def _active_page(soup: BeautifulSoup) -> Optional[int]:
    nodes = soup.select(".pager a.active")
    if not nodes:
        return None
    if len(nodes) != 1:
        raise GimcheonContractError("multiple active pagination controls")
    value = _clean(nodes[0].get_text(" ", strip=True))
    if not value.isdigit():
        raise GimcheonContractError("non-numeric active pagination control")
    return int(value)


def _parse_page(
    soup: BeautifulSoup,
    section: GimcheonSection,
    requested_page: int,
) -> _Page:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if "교육프로그램" not in title or section.label not in title:
        raise GimcheonContractError(
            f"{section.key} page {requested_page}: unexpected title {title}"
        )
    _validate_list_form(soup, section, requested_page)
    empty_marker = "모집중인 강좌가 없습니다."
    wrappers = [
        wrapper
        for wrapper in soup.select(".calss_wrap")
        if wrapper.select(":scope > .class_item")
        or empty_marker in _clean(wrapper.get_text(" ", strip=True))
    ]
    if len(wrappers) != 1:
        raise GimcheonContractError(
            f"{section.key} page {requested_page}: course-ledger wrapper count changed"
        )
    wrapper = wrappers[0]
    cards = wrapper.select(":scope > .class_item")
    if cards:
        if empty_marker in _clean(wrapper.get_text(" ", strip=True)):
            raise GimcheonContractError(f"{section.key}: rows and empty marker coexist")
        rows = tuple(_parse_card(card, section, requested_page) for card in cards)
    else:
        if empty_marker not in _clean(wrapper.get_text(" ", strip=True)):
            raise GimcheonContractError(
                f"{section.key} page {requested_page}: missing structural empty marker"
            )
        rows = ()
    if len(rows) > GIMCHEON_PAGE_SIZE:
        raise GimcheonContractError(
            f"{section.key} page {requested_page}: page-size overflow"
        )
    identities = [row.identity for row in rows]
    if len(identities) != len(set(identities)):
        raise GimcheonContractError(
            f"{section.key} page {requested_page}: duplicate operNo"
        )
    return _Page(
        requested_page=requested_page,
        advertised_last_page=_advertised_last_page(soup),
        active_page=_active_page(soup),
        rows=rows,
    )


def _registration_signature(item: _Registration) -> tuple[Any, ...]:
    return (
        item.rcpt_no,
        item.source_state,
        item.source_label,
        item.kind,
        item.apply_period,
        item.capacity,
        item.waiting_capacity,
        item.fee,
    )


def _course_signature(item: _ListedCourse) -> tuple[Any, ...]:
    return (
        item.identity,
        item.title,
        item.subcategory,
        item.application_method,
        item.venue,
        item.period,
        item.schedule_time,
        item.schedule_days,
        tuple(_registration_signature(value) for value in item.registrations),
    )


def _page_signature(page: _Page) -> tuple[Any, ...]:
    return (
        page.advertised_last_page,
        page.active_page,
        tuple(_course_signature(row) for row in page.rows),
    )


def _collect_section(
    session: Any,
    section: GimcheonSection,
    timeout: int,
    max_pages: int,
    fetcher: Fetcher,
    meta: dict[str, Any],
) -> tuple[list[_ListedCourse], int]:
    first_url = _section_page_url(section, 1)
    first_soup = _fetch_soup(session, first_url, timeout, fetcher, meta)
    if section.key == "regular":
        _validate_owner_menu(first_soup)
    first = _parse_page(first_soup, section, 1)
    last_page = first.advertised_last_page
    if last_page < 1 or last_page > max_pages:
        meta["source_cap_reached"] = True
        raise GimcheonContractError(
            f"{section.key}: advertised last page {last_page} exceeds max_pages={max_pages}"
        )
    pages: dict[int, _Page] = {1: first}
    for page_number in range(2, last_page + 1):
        url = _section_page_url(section, page_number)
        parsed = _parse_page(
            _fetch_soup(session, url, timeout, fetcher, meta), section, page_number
        )
        if parsed.advertised_last_page != last_page:
            raise GimcheonContractError(f"{section.key}: advertised last page changed")
        pages[page_number] = parsed

    for page_number, parsed in pages.items():
        if parsed.active_page != page_number:
            raise GimcheonContractError(
                f"{section.key} page {page_number}: active-page binding mismatch"
            )
        if page_number < last_page and len(parsed.rows) != GIMCHEON_PAGE_SIZE:
            raise GimcheonContractError(
                f"{section.key} page {page_number}: premature short page"
            )
    if last_page > 1 and not pages[last_page].rows:
        raise GimcheonContractError(f"{section.key}: advertised last page is empty")
    if not first.rows and last_page != 1:
        raise GimcheonContractError(f"{section.key}: empty first page advertises later data")

    sentinel_number = last_page + 1
    sentinel_url = _section_page_url(section, sentinel_number)
    sentinel = _parse_page(
        _fetch_soup(session, sentinel_url, timeout, fetcher, meta),
        section,
        sentinel_number,
    )
    if (
        sentinel.rows
        or sentinel.active_page is not None
        or sentinel.advertised_last_page != last_page
    ):
        raise GimcheonContractError(
            f"{section.key}: post-last page is not the exact structural empty sentinel"
        )

    for page_number in sorted({1, last_page}):
        url = _section_page_url(section, page_number)
        checked = _parse_page(
            _fetch_soup(session, url, timeout, fetcher, meta), section, page_number
        )
        if _page_signature(checked) != _page_signature(pages[page_number]):
            raise GimcheonContractError(
                f"{section.key} page {page_number}: stability recheck changed"
            )
    sentinel_checked = _parse_page(
        _fetch_soup(session, sentinel_url, timeout, fetcher, meta),
        section,
        sentinel_number,
    )
    if _page_signature(sentinel_checked) != _page_signature(sentinel):
        raise GimcheonContractError(f"{section.key}: sentinel stability recheck changed")

    meta.setdefault("boundary_modes", {})[section.key] = "exact_structural_empty"
    meta.setdefault("sentinel_pages", {})[section.key] = sentinel_number
    rows = [row for number in sorted(pages) for row in pages[number].rows]
    identities = [row.identity for row in rows]
    if len(identities) != len(set(identities)):
        raise GimcheonContractError(f"{section.key}: duplicate operNo across pages")
    return rows, last_page


def _validate_owner_menu(soup: BeautifulSoup) -> None:
    owner_nodes = []
    for node in soup.select("#gnb > li.depth1"):
        links = node.select("ul > li.depth2 > a[href]")
        if any("mn=10023" in _clean(link.get("href")) for link in links):
            owner_nodes.append(node)
    if len(owner_nodes) != 1:
        raise GimcheonContractError("education-program owner menu could not be isolated")
    links = owner_nodes[0].select("ul > li.depth2 > a[href]")
    actual = tuple(
        (
            _clean(link.get_text(" ", strip=True)),
            urlparse(urljoin(GIMCHEON_CANONICAL_URL, _clean(link.get("href")))).path,
            tuple(
                sorted(
                    (
                        key,
                        tuple(values),
                    )
                    for key, values in parse_qs(
                        urlparse(
                            urljoin(GIMCHEON_CANONICAL_URL, _clean(link.get("href")))
                        ).query
                    ).items()
                )
            ),
        )
        for link in links
    )
    expected = tuple(
        (
            section.label,
            "/welfare/page/link.tc",
            (("mn", (section.menu_no,)), ("pageNo", (section.page_no,))),
        )
        for section in GIMCHEON_SECTIONS
    )
    if actual != expected:
        raise GimcheonContractError(f"education owner menu vocabulary changed: {actual}")


def _validate_detail_form(
    soup: BeautifulSoup,
    listed: _ListedCourse,
    section: GimcheonSection,
) -> BeautifulSoup:
    forms = soup.select("form#eduDetailForm")
    if len(forms) != 1:
        raise GimcheonContractError(
            f"{section.key}:{listed.identity}: eduDetailForm count changed"
        )
    form = forms[0]
    expected = {
        "pageDtlOrdrNo": section.detail_order,
        "searchTrgtSeCd": section.target_code,
        "operNo": listed.identity,
        "rcptNo": "0",
    }
    for name, value in expected.items():
        if _input_value(form, name) != value:
            raise GimcheonContractError(
                f"{section.key}:{listed.identity}: detail {name} binding mismatch"
            )
    if _input_value(form, "pageIndex") not in {"", str(listed.page)}:
        raise GimcheonContractError(
            f"{section.key}:{listed.identity}: unexpected detail pageIndex"
        )
    return form


def _parse_detail(
    soup: BeautifulSoup,
    listed: _ListedCourse,
) -> dict[str, Any]:
    section = GIMCHEON_SECTION_BY_KEY[listed.section_key]
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if "교육프로그램" not in title or section.label not in title:
        raise GimcheonContractError(
            f"{section.key}:{listed.identity}: detail page owner/title mismatch"
        )
    form = _validate_detail_form(soup, listed, section)
    cards = form.select(".calss_wrap > .class_item")
    if len(cards) != 1:
        raise GimcheonContractError(
            f"{section.key}:{listed.identity}: detail course card count changed"
        )
    card = cards[0]
    heading = card.select_one(".desc .top .left > .tit")
    detail_title = _clean(heading.get_text(" ", strip=True) if heading else "")
    if detail_title != listed.title:
        raise GimcheonContractError(
            f"{section.key}:{listed.identity}: list/detail title mismatch"
        )
    fields = _course_fields(card, f"{section.key}:{listed.identity} detail")
    expected_fields = {
        "상세분류": listed.subcategory,
        "접수방법": listed.application_method,
        "강의실": listed.venue,
        "교육기간": listed.period,
        "교육시간": listed.schedule_time,
        "교육요일": listed.schedule_days,
    }
    if fields != expected_fields:
        raise GimcheonContractError(
            f"{section.key}:{listed.identity}: list/detail course fields disagree"
        )
    registrations = _parse_registrations(
        form, label=f"{section.key}:{listed.identity} detail", detail=True
    )
    if tuple(_registration_signature(item) for item in registrations) != tuple(
        _registration_signature(item) for item in listed.registrations
    ):
        raise GimcheonContractError(
            f"{section.key}:{listed.identity}: list/detail registration binding changed"
        )

    # An enabled source-state I entry is the HTML application control.  The
    # collector records the already-fetched detail page as its safe entry URL;
    # it never follows eduDetail.apply, authentication, rcpt, or applicant URLs.
    application_controls = sum(item.source_state == "I" for item in registrations)
    for node in form.select("form[action], a[href]"):
        candidate = _clean(node.get("action") or node.get("href"))
        lowered = candidate.lower()
        if candidate and not lowered.startswith("javascript:") and any(
            token in lowered
            for token in ("/edu/apply", "/edu/rcpt", "applicant", "applylist", "rcptlist")
        ):
            raise GimcheonContractError(
                f"{section.key}:{listed.identity}: unexpected direct application endpoint"
            )
    return {
        "title": detail_title,
        "application_controls": application_controls,
        "registrations": registrations,
    }


def _course_status(listed: _ListedCourse) -> str:
    states = {item.source_state for item in listed.registrations}
    if "I" in states:
        return "OPEN"
    if "W" in states and states <= {"W", "E"}:
        return "SCHEDULED"
    if states == {"E"}:
        return "CLOSED"
    raise GimcheonContractError(
        f"{listed.section_key}:{listed.identity}: unsupported status combination {states}"
    )


def _joined_unique(values: Iterable[str], separator: str = " / ") -> str:
    output: list[str] = []
    for value in values:
        cleaned = _clean(value)
        if cleaned and cleaned not in output:
            output.append(cleaned)
    return separator.join(output)


def _target_extra(target: Any) -> Mapping[str, Any]:
    value = _target_value(target, "extra")
    return value if isinstance(value, Mapping) else {}


def _row(
    target: Any,
    listed: _ListedCourse,
    detail: Mapping[str, Any],
) -> dict[str, Any]:
    section = GIMCHEON_SECTION_BY_KEY[listed.section_key]
    status = _course_status(listed)
    control_count = int(detail.get("application_controls") or 0)
    has_control = control_count > 0
    apply_start = min(item.apply_start for item in listed.registrations)
    apply_end = max(item.apply_end for item in listed.registrations)
    apply_period = _joined_unique(item.apply_period for item in listed.registrations)
    capacity = _joined_unique(item.capacity for item in listed.registrations)
    fee = _joined_unique(item.fee for item in listed.registrations)
    schedule = _joined_unique((listed.schedule_days, listed.schedule_time), " · ")
    extra = _target_extra(target)
    output: dict[str, Any] = {
        "provider": GIMCHEON_PROVIDER,
        "provider_course_id": (
            f"{GIMCHEON_PROVIDER}:education:{listed.section_key}:{listed.identity}"
        ),
        "title": listed.title,
        "branch": GIMCHEON_BRANCH,
        "branch_code": f"{GIMCHEON_PROVIDER}:lifelong_learning_center",
        "preserve_branch": True,
        "branch_url": section.url,
        "raw_url": listed.detail_url,
        "application_url": listed.detail_url if has_control else "",
        "application_type": (
            "ONLINE_LOGIN_REQUIRED" if has_control else "INFO_ONLY_DISABLED_SOURCE_STATE"
        ),
        "application_method_raw": listed.application_method,
        "reservation_available": bool(has_control and status == "OPEN"),
        "status": status,
        "period": listed.period,
        "apply_period": apply_period,
        "schedule_raw": schedule,
        "start_date": listed.event_start.isoformat(),
        "end_date": listed.event_end.isoformat(),
        "apply_start_date": apply_start.isoformat(),
        "apply_end_date": apply_end.isoformat(),
        "target": "",
        "capacity": capacity,
        "fee": fee,
        "venue_name": listed.venue,
        "room": listed.venue,
        "address": GIMCHEON_ADDRESS,
        "venue_address": GIMCHEON_ADDRESS,
        "category": listed.subcategory or section.label,
        "collection_category": _clean(extra.get("collection_category") or "공공예약"),
        "domain_category": _clean(extra.get("domain_category") or "교육·강좌"),
        "operator_type": _clean(extra.get("operator_type") or "지자체/공공기관"),
        "source_group": _clean(extra.get("source_group") or "municipal_reservation"),
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": "static_html+detail_html",
        "program_type": "교육",
        "municipality_code": GIMCHEON_MUNICIPALITY_CODE,
        "municipality_name": GIMCHEON_MUNICIPALITY_NAME,
        "municipality_full_name": GIMCHEON_MUNICIPALITY_NAME,
        "raw_fields": {
            "parser": GIMCHEON_PARSER,
            "identity": listed.identity,
            "section_key": listed.section_key,
            "section_label": section.label,
            "source_page": listed.page,
            "source_status": _joined_unique(
                (item.source_state for item in listed.registrations), ","
            ),
            "source_status_label": _joined_unique(
                (item.source_label for item in listed.registrations), ","
            ),
            "registration_numbers": [item.rcpt_no for item in listed.registrations],
            "detail_verified": True,
            "list_detail_binding": "operNo+title+fields+registration_ledger",
            "application_control_verified": True,
            "application_endpoint_requested": False,
            "applicant_list_requested": False,
            "instructor_body_attachment_contact_image_excluded": True,
        },
    }
    public_text = " ".join(
        _clean(output.get(key))
        for key in (
            "title",
            "branch",
            "venue_name",
            "schedule_raw",
            "target",
            "category",
        )
    )
    if _PHONE_RE.search(public_text) or _EMAIL_RE.search(public_text):
        raise GimcheonContractError(
            f"{listed.section_key}:{listed.identity}: public row leaked contact data"
        )
    return output


def collect_gimcheon_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = GIMCHEON_MAX_PAGES,
    detail_limit: int = GIMCHEON_DETAIL_LIMIT,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete current/future five-section education snapshot."""

    audit_date = _today(today)
    factory = session_factory or _default_session_factory
    html_fetcher = fetcher or _default_fetcher
    meta: dict[str, Any] = {
        "municipality_code": GIMCHEON_MUNICIPALITY_CODE,
        "municipality_name": GIMCHEON_MUNICIPALITY_NAME,
        "owner_provider": GIMCHEON_PROVIDER,
        "canonical_url": GIMCHEON_CANONICAL_URL,
        "candidate_id": GIMCHEON_CANONICAL_CANDIDATE_ID,
        "retargeted_candidate_id": GIMCHEON_OLD_CANDIDATE_ID,
        "parser": GIMCHEON_PARSER,
        "ownership_scope": GIMCHEON_OWNERSHIP_SCOPE,
        "cutoff": audit_date.isoformat(),
        "pages": 0,
        "data_pages": 0,
        "list_requests": 0,
        "detail_pages": 0,
        "logical_requests": 0,
        "physical_requests": 0,
        "request_retry_count": 0,
        "application_endpoint_requests": 0,
        "applicant_list_requests": 0,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "configured_collection_error": "",
    }
    session: Any = None
    try:
        if not is_gimcheon_education_target(target):
            raise GimcheonContractError(
                "target is not the canonical Gimcheon education owner"
            )
        if timeout < 1 or max_pages < 1 or detail_limit < 0:
            raise GimcheonContractError("invalid collector limits")
        session = factory()

        section_rows: dict[str, list[_ListedCourse]] = {}
        section_pages: dict[str, int] = {}
        for section in GIMCHEON_SECTIONS:
            rows, last_page = _collect_section(
                session,
                section,
                timeout,
                max_pages,
                html_fetcher,
                meta,
            )
            section_rows[section.key] = rows
            section_pages[section.key] = last_page

        listed = [
            row
            for section in GIMCHEON_SECTIONS
            for row in section_rows[section.key]
        ]
        identities = [row.identity for row in listed]
        if len(identities) != len(set(identities)):
            raise GimcheonContractError("duplicate operNo across education sections")

        current = [row for row in listed if row.event_end >= audit_date]
        if len(current) > detail_limit:
            meta["source_cap_reached"] = True
            raise GimcheonContractError(
                "detail_limit would create a partial current/future snapshot"
            )

        details: dict[tuple[str, str], dict[str, Any]] = {}
        for row in current:
            soup = _fetch_soup(
                session, row.detail_url, timeout, html_fetcher, meta
            )
            details[(row.section_key, row.identity)] = _parse_detail(soup, row)
            meta["detail_pages"] = int(meta["detail_pages"]) + 1

        output = [
            _row(target, row, details[(row.section_key, row.identity)])
            for row in current
        ]
        if dedupe_rows is not None:
            output = list(dedupe_rows(output))
        if len(output) != len(current):
            raise GimcheonContractError(
                "dedupe changed an already-unique current snapshot"
            )

        section_counts = {
            GIMCHEON_SECTION_BY_KEY[key].label: len(rows)
            for key, rows in section_rows.items()
        }
        section_current_counts = dict(
            Counter(
                GIMCHEON_SECTION_BY_KEY[row.section_key].label for row in current
            )
        )
        source_states = Counter(
            item.source_state for row in listed for item in row.registrations
        )
        output_statuses = Counter(_clean(row.get("status")) for row in output)
        controls = sum(bool(row.get("reservation_available")) for row in output)
        data_pages = sum(section_pages.values())
        meta.update(
            {
                "pages": data_pages,
                "data_pages": data_pages,
                "list_requests": int(meta["logical_requests"])
                - int(meta["detail_pages"]),
                "section_pages": {
                    GIMCHEON_SECTION_BY_KEY[key].label: value
                    for key, value in section_pages.items()
                },
                "section_counts": section_counts,
                "section_current_counts": section_current_counts,
                "active_menu_labels": list(GIMCHEON_ACTIVE_MENU_LABELS),
                "source_rows": len(listed),
                "source_total": len(listed),
                "current_source_count": len(current),
                "expired_source_count": len(listed) - len(current),
                "source_status_counts": dict(source_states),
                "current_status_counts": dict(output_statuses),
                "branch_counts": dict(
                    Counter(_clean(row.get("branch")) for row in output)
                ),
                "official_branch_addresses": {GIMCHEON_BRANCH: GIMCHEON_ADDRESS},
                "detail_attempts": len(current),
                "detail_verified": len(details),
                "application_control_count": controls,
                "info_only_count": len(output) - controls,
                "duplicate_identity_count": 0,
                "returned_count": len(output),
                "output_rows": len(output),
                "request_retry_count": int(meta["physical_requests"])
                - int(meta["logical_requests"]),
                "pagination_complete": True,
                "details_complete": True,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
                "no_current_data": not output,
                "no_current_reason": (
                    "공식 교육프로그램 5개 섹션에 현재·향후 강좌가 없음"
                    if not output
                    else ""
                ),
                "candidate_decisions": dict(GIMCHEON_CANDIDATE_DECISIONS),
                "excluded_owner_boundaries": dict(
                    GIMCHEON_EXCLUDED_OWNER_BOUNDARIES
                ),
                "old_url_relationship": (
                    "authenticated_personal_application_history_not_catalog"
                ),
            }
        )
        return output, GIMCHEON_PARSER, meta
    except Exception as exc:  # every network/parser/contract issue fails closed
        meta.update(
            {
                "configured_collection_error": f"{type(exc).__name__}: {_clean(exc)}",
                "pagination_complete": False,
                "details_complete": False,
                "snapshot_complete": False,
                "full_snapshot_validated": False,
                "returned_count": 0,
                "output_rows": 0,
                "request_retry_count": int(meta.get("physical_requests") or 0)
                - int(meta.get("logical_requests") or 0),
            }
        )
        return [], GIMCHEON_PARSER, meta
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


collect = collect_gimcheon_education


__all__ = [
    "GIMCHEON_ACTIVE_MENU_LABELS",
    "GIMCHEON_ADDRESS",
    "GIMCHEON_BRANCH",
    "GIMCHEON_CANONICAL_CANDIDATE_ID",
    "GIMCHEON_CANONICAL_URL",
    "GIMCHEON_CANDIDATE_DECISIONS",
    "GIMCHEON_DETAIL_LIMIT",
    "GIMCHEON_EXCLUDED_OWNER_BOUNDARIES",
    "GIMCHEON_FETCH_ATTEMPTS",
    "GIMCHEON_HOST",
    "GIMCHEON_MAX_PAGES",
    "GIMCHEON_MUNICIPALITY_CODE",
    "GIMCHEON_MUNICIPALITY_NAME",
    "GIMCHEON_OLD_CANDIDATE_ID",
    "GIMCHEON_OLD_URL",
    "GIMCHEON_OWNERSHIP_SCOPE",
    "GIMCHEON_PAGE_SIZE",
    "GIMCHEON_PARSER",
    "GIMCHEON_PROVIDER",
    "GIMCHEON_SECTIONS",
    "GimcheonContractError",
    "GimcheonSection",
    "collect",
    "collect_gimcheon_education",
    "is_gimcheon_education_target",
    "is_target",
]
