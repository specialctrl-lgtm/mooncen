"""Fail-closed collector for Sejong City Library reading-culture courses.

The reviewed source is the structured ``edusat`` catalogue selected by
``sh_ct_idx2=54``.  It is not a notice board.  Every list request reapplies the
complete filter, every current/future course is checked against its public
detail page, and overrun pagination is proved to repeat the final page.

Application, registration lookup, login, attachment, image, and applicant/PII
routes are deliberately never requested.  Public application availability is
represented by the already-verified course detail URL.
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
from bs4 import BeautifulSoup, Tag


SEJONG_LIBRARY_PROVIDER = "MUNI_LIB_SEJONG_GO_KR_026D075C"
SEJONG_LIBRARY_HOST = "lib.sejong.go.kr"
SEJONG_LIBRARY_ROOT = f"https://{SEJONG_LIBRARY_HOST}"
SEJONG_LIBRARY_LIST_PATH = "/main/edusat/list.do"
SEJONG_LIBRARY_DETAIL_PATH = "/main/edusat/view.do"
SEJONG_LIBRARY_APPLICATION_PATH = "/main/edusat/regist.do"
SEJONG_LIBRARY_REGISTRATION_LOOKUP_PATH = "/main/edusat/user.do"
SEJONG_LIBRARY_CANONICAL_URL = (
    f"{SEJONG_LIBRARY_ROOT}{SEJONG_LIBRARY_LIST_PATH}?sh_ct_idx2=54"
)
SEJONG_LIBRARY_MUNICIPALITY_CODE = "3611000000"
SEJONG_LIBRARY_MUNICIPALITY_NAME = "세종특별자치시"
SEJONG_LIBRARY_BRANCH = "세종시립도서관"
SEJONG_LIBRARY_ADDRESS = "세종특별자치시 세종로 1207"
SEJONG_LIBRARY_PAGE_SIZE = 20
SEJONG_LIBRARY_MAX_HTML_BYTES = 4_000_000
SEJONG_LIBRARY_PARSER = (
    "sejong_library_edusat_54+declared_pages+exact_last_page_clamp+"
    "stable_first_last+all_current_details+privacy_safe_controls"
)
SEJONG_LIBRARY_OWNERSHIP_SCOPE = (
    "sejong_city_library_edusat_54_current_and_future_reading_culture_education"
)

_SPACE_RE = re.compile(r"\s+")
_DATE_RANGE_PREFIX_RE = re.compile(
    r"\A(20\d{2})[-./](\d{1,2})[-./](\d{1,2})\s*~\s*"
    r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})(?:\s+(.*))?\Z"
)
_DATETIME_RANGE_RE = re.compile(
    r"\A(20\d{2})[-./](\d{1,2})[-./](\d{1,2})\s+([0-2]\d):([0-5]\d)\s*~\s*"
    r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})\s+([0-2]\d):([0-5]\d)\Z"
)
_CAPACITY_RE = re.compile(r"(\d{1,6})\s*/\s*(\d{1,6})\s*명")
_WAITLIST_RE = re.compile(r"대기\s*[:：]?\s*(\d{1,6})\s*명?")
_EDU_ID_RE = re.compile(r"\d{1,10}\Z")

_LIST_REQUIRED_FIELDS = frozenset({"신청기간", "운영기간", "모집인원"})
_LIST_ALLOWED_FIELDS = _LIST_REQUIRED_FIELDS | {"수강대상"}
_DETAIL_REQUIRED_FIELDS = frozenset(
    {"강좌기간", "강좌시간", "신청기간", "신청방법", "수강대상", "모집인원", "강의실"}
)
_DETAIL_OPTIONAL_SAFE_FIELDS = frozenset({"참가비"})
_DETAIL_EXCLUDED_FIELDS = frozenset({"강사", "첨부파일"})


class SejongLibraryContractError(ValueError):
    """Raised when the reviewed official catalogue contract changes."""


@dataclass(frozen=True)
class ListedCourse:
    edu_idx: str
    page: int
    position: int
    title: str
    source_category: str
    source_status: str
    status: str
    operation_raw: str
    operation_period: str
    start_date: date
    end_date: date
    schedule_raw: str
    application_raw: str
    target_raw: str
    capacity_raw: str
    raw_url: str
    application_control: bool

    def fingerprint(self) -> tuple[Any, ...]:
        return (
            self.edu_idx,
            self.title,
            self.source_category,
            self.source_status,
            self.operation_raw,
            self.application_raw,
            self.target_raw,
            self.capacity_raw,
            self.raw_url,
            self.application_control,
        )


@dataclass(frozen=True)
class ParsedPage:
    requested_page: int
    total: int
    last_page: int
    rows: tuple[ListedCourse, ...]

    def fingerprint(self) -> tuple[tuple[Any, ...], ...]:
        return tuple(row.fingerprint() for row in self.rows)


SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", _clean(value).casefold())


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
        raise SejongLibraryContractError("today must be an ISO date") from exc


def _safe_origin(parsed: Any) -> bool:
    try:
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == SEJONG_LIBRARY_HOST
        and port is None
        and not parsed.username
        and not parsed.password
        and not parsed.params
        and not parsed.fragment
    )


def _canonical_target_url(value: Any) -> bool:
    parsed = urlparse(_clean(value))
    query = parse_qs(parsed.query, keep_blank_values=True)
    return bool(
        _safe_origin(parsed)
        and parsed.path == SEJONG_LIBRARY_LIST_PATH
        and query == {"sh_ct_idx2": ["54"]}
    )


def is_sejong_library_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider")).upper() == SEJONG_LIBRARY_PROVIDER
        and _canonical_target_url(_target_value(target, "url"))
    )


is_target = is_sejong_library_target


def sejong_library_list_params(page: Any) -> dict[str, str]:
    value = _clean(page)
    if not value.isdigit() or int(value) < 1:
        raise SejongLibraryContractError("list page must be a positive integer")
    return {
        "v_page": str(int(value)),
        "sh_ct_idx": "",
        "sh_ct_idx2": "54",
        "v_status": "",
        "v_search": "",
        "v_keyword": "",
    }


def _single_query(query: Mapping[str, list[str]], key: str) -> str:
    values = query.get(key) or []
    return _clean(values[0]) if len(values) == 1 else ""


def _safe_list_response_url(value: Any, params: Mapping[str, str]) -> bool:
    parsed = urlparse(_clean(value))
    query = parse_qs(parsed.query, keep_blank_values=True)
    return bool(
        _safe_origin(parsed)
        and parsed.path == SEJONG_LIBRARY_LIST_PATH
        and set(query) == set(params)
        and all(query.get(key) == [expected] for key, expected in params.items())
    )


def _detail_url(edu_idx: str) -> str:
    if not _EDU_ID_RE.fullmatch(_clean(edu_idx)):
        raise SejongLibraryContractError("invalid course identity")
    return f"{SEJONG_LIBRARY_ROOT}{SEJONG_LIBRARY_DETAIL_PATH}?{urlencode({'edu_idx': edu_idx})}"


def _safe_detail_url(value: Any, expected_edu_idx: str = "") -> str:
    parsed = urlparse(urljoin(SEJONG_LIBRARY_ROOT, _clean(value)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    edu_idx = _single_query(query, "edu_idx")
    if not (
        _safe_origin(parsed)
        and parsed.path == SEJONG_LIBRARY_DETAIL_PATH
        and set(query).issubset({"edu_idx", "prepage"})
        and edu_idx
        and _EDU_ID_RE.fullmatch(edu_idx)
        and (not expected_edu_idx or edu_idx == expected_edu_idx)
    ):
        raise SejongLibraryContractError("detail URL left the reviewed public route")
    return _detail_url(edu_idx)


def _validate_bound_control(
    href: Any,
    *,
    expected_path: str,
    expected_edu_idx: str,
) -> None:
    parsed = urlparse(
        urljoin(
            f"{SEJONG_LIBRARY_ROOT}{SEJONG_LIBRARY_LIST_PATH}",
            _clean(href),
        )
    )
    query = parse_qs(parsed.query, keep_blank_values=True)
    if not (
        _safe_origin(parsed)
        and parsed.path == expected_path
        and set(query).issubset({"edu_idx", "prepage"})
        and _single_query(query, "edu_idx") == expected_edu_idx
    ):
        raise SejongLibraryContractError("course-bound control identity changed")


def _response_soup(response: Any, *, requested_url: str, params: Optional[Mapping[str, str]]) -> BeautifulSoup:
    if int(getattr(response, "status_code", 200)) != 200:
        raise SejongLibraryContractError(
            f"unexpected HTTP status {getattr(response, 'status_code', '')}"
        )
    final_url = _clean(getattr(response, "url", requested_url)) or requested_url
    if params is not None:
        if not _safe_list_response_url(final_url, params):
            raise SejongLibraryContractError("list response left the exact filtered URL scope")
    else:
        expected_id = _single_query(
            parse_qs(urlparse(requested_url).query, keep_blank_values=True), "edu_idx"
        )
        _safe_detail_url(final_url, expected_id)
    headers = getattr(response, "headers", {}) or {}
    content_type = _clean(headers.get("Content-Type"))
    if content_type and "html" not in content_type.lower():
        raise SejongLibraryContractError("response is not HTML")
    content = getattr(response, "content", None)
    if content is None:
        content = str(getattr(response, "text", "")).encode("utf-8")
    if not content or len(content) > SEJONG_LIBRARY_MAX_HTML_BYTES:
        raise SejongLibraryContractError("HTML response is empty or over the size cap")
    return BeautifulSoup(content, "lxml")


def _request_list(session: Any, page: int, timeout: int) -> BeautifulSoup:
    params = sejong_library_list_params(page)
    response = session.get(
        f"{SEJONG_LIBRARY_ROOT}{SEJONG_LIBRARY_LIST_PATH}",
        params=params,
        timeout=timeout,
        allow_redirects=False,
    )
    return _response_soup(
        response,
        requested_url=f"{SEJONG_LIBRARY_ROOT}{SEJONG_LIBRARY_LIST_PATH}",
        params=params,
    )


def _request_detail(session: Any, edu_idx: str, timeout: int) -> BeautifulSoup:
    url = _detail_url(edu_idx)
    response = session.get(url, timeout=timeout, allow_redirects=False)
    return _response_soup(response, requested_url=url, params=None)


def _validate_list_shell(soup: BeautifulSoup) -> None:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if "독서문화 프로그램 신청" not in title or "세종시립도서관" not in title:
        raise SejongLibraryContractError("unexpected catalogue title")
    forms = soup.select("form#frm_edu")
    if len(forms) != 1:
        raise SejongLibraryContractError("course search form changed")
    form = forms[0]
    action = urlparse(
        urljoin(
            f"{SEJONG_LIBRARY_ROOT}{SEJONG_LIBRARY_LIST_PATH}",
            _clean(form.get("action")),
        )
    )
    if action.path != SEJONG_LIBRARY_LIST_PATH or _clean(form.get("method")).lower() != "get":
        raise SejongLibraryContractError("course search form action/method changed")
    names = {_clean(node.get("name")) for node in form.select("[name]") if _clean(node.get("name"))}
    required_names = set(sejong_library_list_params(1)) - {"v_page"}
    if not required_names.issubset(names):
        raise SejongLibraryContractError("course search form fields changed")


def _declared_total(soup: BeautifulSoup) -> int:
    markers = soup.select(".board_total_left strong")
    if len(markers) != 1:
        raise SejongLibraryContractError("course total marker changed")
    value = _clean(markers[0].get_text(" ", strip=True)).replace(",", "")
    if not value.isdigit():
        raise SejongLibraryContractError("course total is not an integer")
    return int(value)


def _date_range_prefix(
    value: Any, *, require_order: bool = True
) -> tuple[str, date, date, str]:
    text = _clean(value).replace(" 요일", "요일")
    match = _DATE_RANGE_PREFIX_RE.fullmatch(text)
    if not match:
        raise SejongLibraryContractError(f"invalid operation period: {text or '<empty>'}")
    groups = match.groups()
    try:
        start = date(int(groups[0]), int(groups[1]), int(groups[2]))
        end = date(int(groups[3]), int(groups[4]), int(groups[5]))
    except ValueError as exc:
        raise SejongLibraryContractError("operation period contains an invalid date") from exc
    if require_order and end < start:
        raise SejongLibraryContractError("operation period ends before it starts")
    schedule = _clean(groups[6]).replace(" 요일", "요일")
    return f"{start.isoformat()} ~ {end.isoformat()}", start, end, schedule


def _datetime_range(value: Any) -> tuple[str, datetime, datetime, date, date]:
    text = _clean(value)
    match = _DATETIME_RANGE_RE.fullmatch(text)
    if not match:
        raise SejongLibraryContractError(f"invalid application period: {text or '<empty>'}")
    values = [int(item) for item in match.groups()]
    tz = ZoneInfo("Asia/Seoul")
    try:
        start = datetime(*values[:5], tzinfo=tz)
        end = datetime(*values[5:], tzinfo=tz)
    except ValueError as exc:
        raise SejongLibraryContractError("application period contains an invalid date") from exc
    if end < start:
        raise SejongLibraryContractError("application period ends before it starts")
    return (
        f"{start.strftime('%Y-%m-%d %H:%M')} ~ {end.strftime('%Y-%m-%d %H:%M')}",
        start,
        end,
        start.date(),
        end.date(),
    )


def _capacity(value: Any) -> tuple[int, int, Optional[int]]:
    text = _clean(value).replace(",", "")
    match = _CAPACITY_RE.search(text)
    if not match:
        raise SejongLibraryContractError("invalid course capacity")
    current, total = int(match.group(1)), int(match.group(2))
    if current < 0 or total < 1:
        raise SejongLibraryContractError("invalid course capacity values")
    wait_match = _WAITLIST_RE.search(text)
    return current, total, int(wait_match.group(1)) if wait_match else None


def _display_title(value: Any, *, require_list_prefix: bool) -> str:
    text = _clean(value)
    if require_list_prefix:
        match = re.fullmatch(r"\[문화행사\]\s*(.+)", text)
        if not match:
            raise SejongLibraryContractError("course row left the reviewed 문화행사 category")
        text = _clean(match.group(1))
    elif text.startswith("[문화행사]"):
        text = _clean(text[len("[문화행사]") :])
    if not text:
        raise SejongLibraryContractError("course title is empty")
    return text


def _field_pairs(container: Tag, selector: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for dl in container.select(selector):
        dt = dl.select_one("dt")
        dd = dl.select_one("dd")
        key = _clean(dt.get_text(" ", strip=True) if dt else "")
        if not key or dd is None or key in pairs:
            raise SejongLibraryContractError("duplicate/empty structured course field")
        pairs[key] = _clean(dd.get_text(" ", strip=True))
    return pairs


def _source_state(card: Tag) -> tuple[str, str, Tag]:
    controls = card.select(".btn_box a.btn_sm:not(.btn_check)")
    if len(controls) != 1:
        raise SejongLibraryContractError("course state control changed")
    control = controls[0]
    classes = set(control.get("class") or [])
    label = _clean(control.get_text(" ", strip=True))
    known = {
        ("btn_ing", "수강신청"): "OPEN",
        ("btn_prepare", "신청준비"): "SCHEDULED",
        ("btn_close", "기간종료"): "CLOSED",
        ("btn_end", "신청마감"): "CLOSED",
    }
    matches = [status for (css, text), status in known.items() if css in classes and text == label]
    if len(matches) != 1:
        raise SejongLibraryContractError(f"unknown course source status: {label}")
    return label, matches[0], control


def _parse_card(card: Tag, page: int, position: int) -> ListedCourse:
    links = card.select('p.tit a[href*="view.do"]')
    if len(links) != 1:
        raise SejongLibraryContractError("course card has no unique detail identity")
    parsed_href = urlparse(
        urljoin(
            f"{SEJONG_LIBRARY_ROOT}{SEJONG_LIBRARY_LIST_PATH}",
            _clean(links[0].get("href")),
        )
    )
    query = parse_qs(parsed_href.query, keep_blank_values=True)
    edu_idx = _single_query(query, "edu_idx")
    if not (
        _safe_origin(parsed_href)
        and parsed_href.path == SEJONG_LIBRARY_DETAIL_PATH
        and set(query).issubset({"edu_idx", "prepage"})
        and _EDU_ID_RE.fullmatch(edu_idx)
    ):
        raise SejongLibraryContractError("course detail identity changed")
    title = _display_title(links[0].get_text(" ", strip=True), require_list_prefix=True)
    categories = card.select("p.cate")
    if len(categories) != 1 or _clean(categories[0].get_text(" ", strip=True)) != "시립도서관":
        raise SejongLibraryContractError("course card branch/category changed")
    fields = _field_pairs(card, ".sm_box dl")
    if not _LIST_REQUIRED_FIELDS.issubset(fields) or not set(fields).issubset(_LIST_ALLOWED_FIELDS):
        raise SejongLibraryContractError("course card structured fields changed")
    # Historical rows include an official 2025-to-2024 typo.  The parsed end
    # date still proves that row is expired; current rows are ordered strictly
    # again when their details are checked.
    period, start, end, schedule = _date_range_prefix(
        fields["운영기간"], require_order=False
    )
    if not fields["신청기간"] or not fields["모집인원"]:
        raise SejongLibraryContractError("course card lacks application/capacity evidence")
    source_status, status, state_control = _source_state(card)
    checks = card.select(".btn_box a.btn_check[href]")
    if len(checks) != 1:
        raise SejongLibraryContractError("registration lookup identity control changed")
    _validate_bound_control(
        checks[0].get("href"),
        expected_path=SEJONG_LIBRARY_REGISTRATION_LOOKUP_PATH,
        expected_edu_idx=edu_idx,
    )
    application_control = status == "OPEN"
    if application_control:
        _validate_bound_control(
            state_control.get("href"),
            expected_path=SEJONG_LIBRARY_APPLICATION_PATH,
            expected_edu_idx=edu_idx,
        )
    elif _clean(state_control.get("href")) != "#javascript:;":
        raise SejongLibraryContractError("closed/scheduled course exposes an unexpected control")
    return ListedCourse(
        edu_idx=edu_idx,
        page=page,
        position=position,
        title=title,
        source_category="시립도서관",
        source_status=source_status,
        status=status,
        operation_raw=fields["운영기간"],
        operation_period=period,
        start_date=start,
        end_date=end,
        schedule_raw=schedule,
        application_raw=fields["신청기간"],
        target_raw=fields.get("수강대상", ""),
        capacity_raw=fields["모집인원"],
        raw_url=_detail_url(edu_idx),
        application_control=application_control,
    )


def _parse_page(soup: BeautifulSoup, requested_page: int, *, clamp: bool = False) -> ParsedPage:
    _validate_list_shell(soup)
    total = _declared_total(soup)
    last_page = max(1, math.ceil(total / SEJONG_LIBRARY_PAGE_SIZE))
    actual_page = last_page if clamp else requested_page
    if not clamp and requested_page > last_page:
        raise SejongLibraryContractError("data page exceeds the declared final page")
    containers = soup.select("#board .lesson > ul")
    if len(containers) != 1:
        raise SejongLibraryContractError("course result container changed")
    all_cards = containers[0].select(":scope > li")
    cards = [card for card in all_cards if card.select_one('p.tit a[href*="view.do"]')]
    expected = min(
        SEJONG_LIBRARY_PAGE_SIZE,
        max(0, total - (actual_page - 1) * SEJONG_LIBRARY_PAGE_SIZE),
    )
    if len(cards) != expected:
        raise SejongLibraryContractError(
            f"page {actual_page} has {len(cards)} course rows, expected {expected}"
        )
    if len(all_cards) != len(cards):
        marker = _clean(" ".join(card.get_text(" ", strip=True) for card in all_cards if card not in cards))
        if total != 0 or marker not in {"강좌가 없습니다.", "강좌가 없습니다"}:
            raise SejongLibraryContractError("non-course row appeared in the course ledger")
    rows = tuple(_parse_card(card, actual_page, position) for position, card in enumerate(cards, 1))
    return ParsedPage(requested_page=requested_page, total=total, last_page=last_page, rows=rows)


def _detail_state(header: Tag) -> tuple[str, str]:
    controls = header.select("a.btn_sm")
    if len(controls) != 1:
        raise SejongLibraryContractError("detail state control changed")
    control = controls[0]
    classes = set(control.get("class") or [])
    label = _clean(control.get_text(" ", strip=True))
    known = {
        ("btn_receipt", "신청중"): "OPEN",
        ("btn_prepare", "신청준비"): "SCHEDULED",
        ("btn_close", "기간종료"): "CLOSED",
        ("btn_end", "신청마감"): "CLOSED",
    }
    matches = [status for (css, text), status in known.items() if css in classes and text == label]
    if len(matches) != 1:
        raise SejongLibraryContractError(f"unknown course detail status: {label}")
    return label, matches[0]


def _detail_pairs(table: Tag) -> tuple[dict[str, str], set[str]]:
    pairs: dict[str, str] = {}
    excluded_seen: set[str] = set()
    for dl in table.select("tbody dl.info"):
        dt = dl.select_one("dt")
        dd = dl.select_one("dd")
        key = _clean(dt.get_text(" ", strip=True) if dt else "")
        if not key or dd is None or key in pairs or key in excluded_seen:
            raise SejongLibraryContractError("duplicate/empty detail field")
        if key in _DETAIL_EXCLUDED_FIELDS:
            excluded_seen.add(key)
            continue
        if key not in _DETAIL_REQUIRED_FIELDS | _DETAIL_OPTIONAL_SAFE_FIELDS:
            raise SejongLibraryContractError(f"unknown structured detail field: {key}")
        pairs[key] = _clean(dd.get_text(" ", strip=True))
    if not _DETAIL_REQUIRED_FIELDS.issubset(pairs):
        raise SejongLibraryContractError("required structured detail fields changed")
    return pairs, excluded_seen


def _enrich_current(row: ListedCourse, soup: BeautifulSoup) -> dict[str, Any]:
    if row.end_date < row.start_date:
        raise SejongLibraryContractError("current list operation period is reversed")
    tables = soup.select("#board .table_bview > table")
    if len(tables) != 1:
        raise SejongLibraryContractError("expected one structured course detail table")
    table = tables[0]
    headers = table.select(":scope > thead th")
    if len(headers) != 1:
        raise SejongLibraryContractError("course detail title header changed")
    detail_label, detail_status = _detail_state(headers[0])
    header_text = _clean(headers[0].get_text(" ", strip=True))
    if not header_text.endswith(detail_label):
        raise SejongLibraryContractError("detail title/status structure changed")
    detail_title = _display_title(header_text[: -len(detail_label)], require_list_prefix=False)
    if _normalized(detail_title) != _normalized(row.title) or detail_status != row.status:
        raise SejongLibraryContractError("list/detail title or status mismatch")
    fields, excluded_fields = _detail_pairs(table)
    detail_period, detail_start, detail_end, unused_schedule = _date_range_prefix(fields["강좌기간"])
    del unused_schedule
    if (
        detail_period != row.operation_period
        or detail_start != row.start_date
        or detail_end != row.end_date
    ):
        raise SejongLibraryContractError("list/detail operation period mismatch")
    schedule = _clean(fields["강좌시간"]).replace(" 요일", "요일")
    if row.schedule_raw and _normalized(schedule) != _normalized(row.schedule_raw):
        raise SejongLibraryContractError("list/detail course schedule mismatch")
    apply_period, apply_start, apply_end, apply_start_date, apply_end_date = _datetime_range(
        fields["신청기간"]
    )
    list_apply = _datetime_range(row.application_raw)
    if (apply_start, apply_end) != (list_apply[1], list_apply[2]):
        raise SejongLibraryContractError("list/detail application period mismatch")
    if not row.target_raw or _normalized(fields["수강대상"]) != _normalized(row.target_raw):
        raise SejongLibraryContractError("list/detail course target mismatch")
    current, total, wait = _capacity(fields["모집인원"])
    list_current, list_total, list_wait = _capacity(row.capacity_raw)
    if (current, total) != (list_current, list_total):
        raise SejongLibraryContractError("list/detail course capacity mismatch")
    application_links = soup.select("#board .btn_w a.con_btn.btn_receipt[href]")
    if detail_status == "OPEN":
        if len(application_links) != 1 or not row.application_control:
            raise SejongLibraryContractError("open detail lacks one application identity control")
        _validate_bound_control(
            application_links[0].get("href"),
            expected_path=SEJONG_LIBRARY_APPLICATION_PATH,
            expected_edu_idx=row.edu_idx,
        )
    elif application_links or row.application_control:
        raise SejongLibraryContractError("closed detail unexpectedly exposes application control")
    room = _clean(fields["강의실"])
    if not room or not _clean(fields["신청방법"]):
        raise SejongLibraryContractError("course venue/application method is missing")
    safe_application_url = row.raw_url if detail_status == "OPEN" else ""
    return {
        "provider": SEJONG_LIBRARY_PROVIDER,
        "provider_course_id": f"{SEJONG_LIBRARY_PROVIDER}:edusat54:{row.edu_idx}",
        "title": row.title,
        "branch": SEJONG_LIBRARY_BRANCH,
        "branch_code": f"{SEJONG_LIBRARY_PROVIDER}:main",
        "preserve_branch": True,
        "address": SEJONG_LIBRARY_ADDRESS,
        "venue_name": SEJONG_LIBRARY_BRANCH,
        "room": room,
        "raw_url": row.raw_url,
        "application_url": safe_application_url,
        "application_type": "ONLINE_RESERVATION" if safe_application_url else "",
        "reservation_available": bool(safe_application_url),
        "status": detail_status,
        "period": row.operation_period,
        "start_date": row.start_date,
        "end_date": row.end_date,
        "schedule_raw": schedule,
        "apply_period": apply_period,
        "apply_start_at": apply_start,
        "apply_end_at": apply_end,
        "apply_start_date": apply_start_date,
        "apply_end_date": apply_end_date,
        "target": _clean(fields["수강대상"]),
        "capacity": total,
        "capacity_current": current,
        "capacity_total": total,
        "capacity_remaining": max(0, total - current),
        "waitlist_total": wait if wait is not None else list_wait,
        "fee": _clean(fields.get("참가비")),
        "application_method_raw": _clean(fields["신청방법"]),
        "category": "독서문화",
        "program_type": "교육",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "source_group": "library",
        "operator_type": "지자체/공공도서관",
        "municipality_code": SEJONG_LIBRARY_MUNICIPALITY_CODE,
        "municipality_name": SEJONG_LIBRARY_MUNICIPALITY_NAME,
        "raw_fields": {
            "source_kind": "sejong_library_edusat_54",
            "source_category": row.source_category,
            "source_status": row.source_status,
            "detail_status": detail_label,
            "edu_idx": row.edu_idx,
            "source_page": row.page,
            "detail_verified": True,
            "excluded_structured_fields": sorted(excluded_fields),
        },
    }


def _failure_meta(error: str = "", **extra: Any) -> dict[str, Any]:
    return {
        "parser": SEJONG_LIBRARY_PARSER,
        "pages": 0,
        "data_pages": 0,
        "list_requests": 0,
        "required_list_requests": 0,
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
        "clamp_verified": False,
        "stable_boundaries_verified": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "configured_collection_error": error,
        **extra,
    }


def collect_sejong_library_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 20,
    detail_limit: int = 50,
    *,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect a complete, privacy-safe current snapshot from catalogue 54."""

    parser = SEJONG_LIBRARY_PARSER
    if not is_sejong_library_target(target):
        return [], parser, _failure_meta("target provider/url is not canonical Sejong library")
    try:
        allowed_pages = max(0, int(max_pages))
        allowed_details = max(0, int(detail_limit))
        cutoff = _today(today)
    except Exception as exc:
        return [], parser, _failure_meta(f"invalid collection limits/date: {type(exc).__name__}: {_clean(exc)}")
    if allowed_pages < 4:
        return [], parser, _failure_meta(
            "max_pages cap cannot cover root, clamp, and stable boundary checks",
            source_cap_reached=True,
            required_list_requests=4,
        )
    factory = session_factory or requests.Session
    session_obj = factory()
    list_requests = 0
    data_pages = 0
    detail_attempts = 0
    detail_pages = 0
    errors: list[str] = []
    source_cap_reached = False
    candidates: list[ListedCourse] = []
    first_page: Optional[ParsedPage] = None
    final_page: Optional[ParsedPage] = None
    clamp_verified = False
    stable_verified = False
    required_list_requests = 0
    duplicate_count = 0
    current_rows: list[ListedCourse] = []
    result: list[dict[str, Any]] = []
    try:
        try:
            first_page = _parse_page(_request_list(session_obj, 1, timeout), 1)
            list_requests += 1
            data_pages += 1
            required_list_requests = first_page.last_page + 3
            if required_list_requests > allowed_pages:
                source_cap_reached = True
                raise SejongLibraryContractError(
                    f"max_pages cap allows {allowed_pages} of {required_list_requests} required list requests"
                )
            candidates.extend(first_page.rows)
            final_page = first_page
            for page in range(2, first_page.last_page + 1):
                parsed = _parse_page(_request_list(session_obj, page, timeout), page)
                list_requests += 1
                data_pages += 1
                if parsed.total != first_page.total or parsed.last_page != first_page.last_page:
                    raise SejongLibraryContractError("declared total/page count changed during crawl")
                candidates.extend(parsed.rows)
                final_page = parsed
            if final_page is None:
                raise SejongLibraryContractError("final page was not collected")
            clamp_page = first_page.last_page + 1
            clamp = _parse_page(
                _request_list(session_obj, clamp_page, timeout), clamp_page, clamp=True
            )
            list_requests += 1
            if (
                clamp.total != first_page.total
                or clamp.last_page != first_page.last_page
                or clamp.fingerprint() != final_page.fingerprint()
            ):
                raise SejongLibraryContractError("overrun page did not repeat the exact final page")
            clamp_verified = True
            if len(candidates) != first_page.total:
                raise SejongLibraryContractError(
                    f"complete count mismatch: parsed={len(candidates)} declared={first_page.total}"
                )
            identities = [row.edu_idx for row in candidates]
            duplicate_count = len(identities) - len(set(identities))
            if duplicate_count:
                raise SejongLibraryContractError(f"{duplicate_count} duplicate course identities")
            current_rows = [row for row in candidates if row.end_date >= cutoff]
            if len(current_rows) > allowed_details:
                source_cap_reached = True
                raise SejongLibraryContractError(
                    f"detail_limit cap allows {allowed_details} of {len(current_rows)} required details"
                )
            # Only current rows parse application dates.  The official archive
            # contains a known impossible 2024-06-31 application end date, which
            # is irrelevant to currentness and must not poison every snapshot.
            for row in current_rows:
                _datetime_range(row.application_raw)
                _capacity(row.capacity_raw)
                detail_attempts += 1
                enriched = _enrich_current(row, _request_detail(session_obj, row.edu_idx, timeout))
                detail_pages += 1
                result.append(enriched)
            first_recheck = _parse_page(_request_list(session_obj, 1, timeout), 1)
            list_requests += 1
            final_recheck = _parse_page(
                _request_list(session_obj, first_page.last_page, timeout),
                first_page.last_page,
            )
            list_requests += 1
            if (
                first_recheck.total != first_page.total
                or first_recheck.fingerprint() != first_page.fingerprint()
                or final_recheck.total != final_page.total
                or final_recheck.fingerprint() != final_page.fingerprint()
            ):
                raise SejongLibraryContractError("first/final catalogue boundary changed during crawl")
            stable_verified = True
            if dedupe_rows is not None:
                deduped = list(dedupe_rows(result))
                if len(deduped) != len(result):
                    raise SejongLibraryContractError("downstream dedupe changed complete snapshot count")
                result = deduped
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {_clean(exc)}")

        snapshot_complete = not errors
        if not snapshot_complete:
            result = []
        expired_count = len(candidates) - len(current_rows)
        status_counts = Counter(_clean(row.get("status")) for row in result)
        meta = {
            "parser": parser,
            "pages": list_requests,
            "data_pages": data_pages,
            "clamp_pages": 1 if clamp_verified else 0,
            "list_requests": list_requests,
            "required_list_requests": required_list_requests,
            "request_count": list_requests + detail_attempts,
            "source_rows": len(candidates),
            "declared_source_rows": first_page.total if first_page else 0,
            "valid_count": len(candidates),
            "invalid_count": 0 if snapshot_complete else 1,
            "duplicate_count": duplicate_count,
            "expired_count": expired_count,
            "current_count": len(current_rows),
            "returned_count": len(result),
            "detail_candidates": len(current_rows),
            "detail_attempts": detail_attempts,
            "detail_pages": detail_pages,
            "detail_errors": max(0, detail_attempts - detail_pages),
            "clamp_verified": clamp_verified and snapshot_complete,
            "stable_boundaries_verified": stable_verified and snapshot_complete,
            "pagination_detected": bool(first_page and first_page.last_page > 1),
            "pagination_complete": bool(
                snapshot_complete
                and first_page
                and data_pages == first_page.last_page
                and clamp_verified
                and stable_verified
            ),
            "details_complete": bool(snapshot_complete and detail_pages == len(current_rows)),
            "snapshot_complete": snapshot_complete,
            "source_cap_reached": source_cap_reached,
            "reservation_discovery_links": sum(bool(row.get("application_url")) for row in result),
            "application_control_count": sum(row.application_control for row in current_rows),
            "branch_count": 1 if result else 0,
            "branch_counts": {SEJONG_LIBRARY_BRANCH: len(result)} if result else {},
            "status_counts": dict(status_counts),
            "ownership_scope": SEJONG_LIBRARY_OWNERSHIP_SCOPE,
            "no_current_data": snapshot_complete and not result,
            "no_current_reason": (
                "all complete library course rows are expired"
                if snapshot_complete and not result and candidates
                else "the complete library catalogue contains zero rows"
                if snapshot_complete and not result
                else ""
            ),
            "configured_collection_error": "; ".join(errors),
        }
        return result, parser, meta
    finally:
        close = getattr(session_obj, "close", None)
        if callable(close):
            close()


collect_sejong_library_education = collect_sejong_library_courses
collect = collect_sejong_library_courses
