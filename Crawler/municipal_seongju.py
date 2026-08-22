"""Fail-closed collector for Seongju Welfare Platform education ledgers.

The review candidate on ``www.sj.go.kr`` is no longer an education page: its
menu id now resolves to the county job board.  The actual county-owned public
application ledger lives on ``www.sj-welfare.or.kr`` and consists of exactly
two sibling catalogues: County Resident Happiness Education and Seongju Youth
Culture House.

Each catalogue is traversed through its server-declared last page.  The exact
next page must be structurally empty and the first, last, and sentinel pages
must remain stable.  Current/future rows are bound to detail pages by
``class_seq``.  An enabled application form is recorded from its HTML control,
but application, identity-check, and applicant endpoints are never fetched.
Free-text descriptions are used only to identify audited venues and targets;
descriptions, contacts, attachments, images, and applicant data are not
returned.
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


SEONGJU_HOST = "www.sj-welfare.or.kr"
SEONGJU_PROVIDER = "MUNI_WWW_SJ_WELFARE_OR_KR_335868A2"
SEONGJU_MUNICIPALITY_CODE = "4784000000"
SEONGJU_MUNICIPALITY_NAME = "경상북도 성주군"
SEONGJU_CANONICAL_CANDIDATE_ID = "MUNI_IR_6F852ACC73A5"
SEONGJU_REVIEW_CANDIDATE_ID = "MUNI_IR_252DDD7B7164"
SEONGJU_REVIEW_PROVIDER = "MUNI_WWW_SJ_GO_KR_1AF02AE6"
SEONGJU_DEPRECATED_PROVIDER = "MUNI_WWW_SJ_GO_KR_5AF393CB"
SEONGJU_SOCIAL_WELFARE_PROVIDER = "MUNI_WWW_SJWELFARE_OR_KR_9BB62674"
SEONGJU_SOCIAL_WELFARE_CANDIDATE_ID = "MUNI_IR_7A482D01A0AA"
SEONGJU_CANONICAL_URL = (
    "https://www.sj-welfare.or.kr/cnts/community/educationApplication.html"
)
SEONGJU_REVIEW_URL = "https://www.sj.go.kr/page.do?mnu_uid=1154"
SEONGJU_PAGE_SIZE = 10
SEONGJU_MAX_PAGES = 10
SEONGJU_DETAIL_LIMIT = 100
SEONGJU_FETCH_ATTEMPTS = 2
SEONGJU_OWNERSHIP_SCOPE = (
    "official_seongju_welfare_platform_happiness_and_youth_education_ledgers"
)
SEONGJU_PARSER = (
    "seongju_complete_two_ledger_education+exact_community_menu_vocabulary+"
    "server_total_last_page+exact_empty_post_last_sentinel+stable_boundaries+"
    "current_detail_class_seq_binding+audited_official_venue_mapping+"
    "application_control_without_form_fetch+pii_allowlist"
)

SEONGJU_CANDIDATE_DECISIONS: Mapping[str, str] = {
    SEONGJU_CANONICAL_CANDIDATE_ID: "promote_new_complete_county_education_owner",
    SEONGJU_REVIEW_CANDIDATE_ID: "exclude_recycled_county_job_board_url",
}
SEONGJU_EXCLUDED_OWNER_BOUNDARIES: Mapping[str, str] = {
    SEONGJU_REVIEW_URL: "recycled_menu_id_now_job_board_not_education_or_reservation",
    (
        "https://www.sj-welfare.or.kr/cnts/community/"
        "educationApplicationCheck.html"
    ): "identity_and_application_check_form_not_public_course_source",
    (
        "https://www.sj-welfare.or.kr/cnts/community/"
        "youthCulturalCenterCheck.html"
    ): "identity_and_application_check_form_not_public_course_source",
    "https://www.sj-welfare.or.kr/cnts/community/notice.html": (
        "supplemental_announcement_board_not_complete_reservation_ledger"
    ),
}
SEONGJU_SEPARATE_OWNERS: Mapping[str, str] = {
    (
        "https://www.sjwelfare.or.kr/front/index.php?"
        "g_page=lecture&m_page=lecture04"
    ): "separate_seongju_social_welfare_center_education_owner_not_duplicate",
}


@dataclass(frozen=True)
class SeongjuLedger:
    key: str
    label: str
    path: str
    branch: str
    branch_address: str
    period_label: str

    @property
    def url(self) -> str:
        return f"https://{SEONGJU_HOST}{self.path}"


SEONGJU_LEDGERS = (
    SeongjuLedger(
        "happiness",
        "군민행복교육",
        "/cnts/community/educationApplication.html",
        "군민행복교육",
        "경상북도 성주군 성주읍 성주로 3200, 성주군청 별관 1층",
        "수강기간",
    ),
    SeongjuLedger(
        "youth",
        "청소년문화의집",
        "/cnts/community/youthCulturalCenter.html",
        "성주군청소년문화의집",
        "경상북도 성주군 성주읍 성주순환로 271-9",
        "기간",
    ),
)
SEONGJU_LEDGER_BY_KEY = {ledger.key: ledger for ledger in SEONGJU_LEDGERS}
SEONGJU_ACTIVE_LEDGER_LABELS = tuple(ledger.label for ledger in SEONGJU_LEDGERS)
SEONGJU_BRANCH_ADDRESSES: Mapping[str, str] = {
    ledger.branch: ledger.branch_address for ledger in SEONGJU_LEDGERS
}
SEONGJU_VENUE_ADDRESSES: Mapping[str, str] = {
    "성주창의문화센터": "경상북도 성주군 성주읍 경산길 17",
    "성주문화예술회관": "경상북도 성주군 성주읍 성주로 3204",
    "성주군청소년문화의집": "경상북도 성주군 성주읍 성주순환로 271-9",
}

_COMMUNITY_MENU = (
    ("공지사항", "/cnts/community/notice.html"),
    ("군민행복교육", "/cnts/community/educationApplication.html"),
    ("청소년문화의집", "/cnts/community/youthCulturalCenter.html"),
    ("신청서식", "/cnts/community/dataroom.html"),
    ("채용정보", "/cnts/community/job.html"),
    ("자주하는 질문", "/cnts/community/faq.html"),
    ("질문과 답변", "/cnts/community/qna.html"),
)
_LIST_HEADERS = {
    "happiness": (
        "번호",
        "강좌명",
        "요일",
        "교육시간",
        "수강기간",
        "모집인원",
        "상태",
        "수강신청",
    ),
    "youth": (
        "번호",
        "강좌명",
        "요일",
        "교육시간",
        "기간",
        "모집인원",
        "상태",
        "수강신청",
    ),
}
_STATUS_MAP = {
    "접수대기": "SCHEDULED",
    "접수중": "OPEN",
    "접수마감": "CLOSED",
}


class SeongjuContractError(RuntimeError):
    """Raised when the audited Seongju public contract changes."""


@dataclass(frozen=True)
class _ListedCourse:
    ledger_key: str
    identity: str
    title: str
    detail_url: str
    day: str
    time: str
    period: str
    event_start: date
    event_end: date
    capacity: str
    source_status: str
    page: int


@dataclass(frozen=True)
class _Page:
    requested_page: int
    total: int
    advertised_last_page: int
    active_page: int
    rows: tuple[_ListedCourse, ...]


Fetcher = Callable[..., Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(
    r"(?<!\d)(?:(20\d{2})|[`']?(\d{2}))[.\-/](\d{1,2})[.\-/](\d{1,2})(?!\d)"
)
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,3}[-\s)]?\d{3,4}[-\s]?\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_IDENTITY_RE = re.compile(r"^[1-9]\d*$")
_DISABLED_APPLICATION = {"No_Signup()", "No_Signup_1()"}


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


def _url_key(url: str) -> tuple[str, str, str, tuple[tuple[str, tuple[str, ...]], ...]]:
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    return (
        parsed.scheme.lower(),
        (parsed.hostname or "").rstrip(".").lower(),
        parsed.path,
        tuple(sorted((key, tuple(values)) for key, values in query.items())),
    )


def is_seongju_education_target(target: Any) -> bool:
    parsed = urlparse(_target_url(target))
    return bool(
        _provider(target) == SEONGJU_PROVIDER
        and parsed.scheme.lower() == "https"
        and parsed.port is None
        and not parsed.username
        and not parsed.password
        and not parsed.params
        and not parsed.fragment
        and _url_key(_target_url(target)) == _url_key(SEONGJU_CANONICAL_URL)
    )


is_target = is_seongju_education_target


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
            raise SeongjuContractError(f"HTTP {status} for {requested_url}")
        final_url = _clean(getattr(result, "url", requested_url)) or requested_url
        soup = BeautifulSoup(getattr(result, "text", ""), "html.parser")
    parsed = urlparse(final_url)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != SEONGJU_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
    ):
        raise SeongjuContractError(f"unexpected redirect target: {final_url}")
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != "성주복지플랫폼":
        raise SeongjuContractError(f"unexpected or missing site title: {title}")
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
    for _ in range(SEONGJU_FETCH_ATTEMPTS):
        meta["physical_requests"] = int(meta.get("physical_requests") or 0) + 1
        try:
            return _coerce_html(fetcher(session, url, timeout), url)
        except Exception as exc:
            error = exc
    meta["request_retry_count"] = int(meta["physical_requests"]) - int(meta["logical_requests"])
    if isinstance(error, SeongjuContractError):
        raise error
    raise SeongjuContractError(
        f"request failed after retries for {url}: {_clean(error)}"
    ) from error


def _page_url(ledger: SeongjuLedger, page: int) -> str:
    if page < 1:
        raise SeongjuContractError("page must be positive")
    return f"{ledger.url}?{urlencode({'page': str(page)})}"


def _date_range(value: str, label: str) -> tuple[date, date]:
    matches = _DATE_RE.findall(_clean(value).replace("∼", "~").replace("～", "~"))
    if len(matches) != 2:
        raise SeongjuContractError(f"{label}: expected exactly two dates: {_clean(value)}")
    output: list[date] = []
    try:
        for full_year, short_year, month, day in matches:
            year = int(full_year) if full_year else 2000 + int(short_year)
            output.append(date(year, int(month), int(day)))
    except ValueError as exc:
        raise SeongjuContractError(f"{label}: invalid date: {_clean(value)}") from exc
    if output[1] < output[0]:
        raise SeongjuContractError(f"{label}: end precedes start")
    return output[0], output[1]


def _normalize_period(value: str, label: str) -> tuple[str, date, date]:
    start, end = _date_range(value, label)
    return f"{start.isoformat()} ~ {end.isoformat()}", start, end


def _safe_detail_url(
    page_url: str,
    href: str,
    ledger: SeongjuLedger,
) -> tuple[str, str]:
    url = urljoin(page_url, _clean(href))
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    identity = _clean((query.get("class_seq") or [""])[0])
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != SEONGJU_HOST
        or parsed.path != ledger.path
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.fragment
        or _clean((query.get("pg") or [""])[0]) != "vv"
        or not _IDENTITY_RE.fullmatch(identity)
        or set(query) - {"pg", "class_seq", "page"}
    ):
        raise SeongjuContractError(f"{ledger.key}: unsafe detail URL {url}")
    return url, identity


def _validate_community_menu(soup: BeautifulSoup) -> None:
    menus = soup.select("ul.ul7")
    if len(menus) != 1:
        raise SeongjuContractError("community owner menu count changed")
    actual = tuple(
        (
            _clean(link.get_text(" ", strip=True)),
            urlparse(urljoin(SEONGJU_CANONICAL_URL, _clean(link.get("href")))).path,
        )
        for link in menus[0].select(":scope > li > a[href]")
    )
    if actual != _COMMUNITY_MENU:
        raise SeongjuContractError(f"community owner menu vocabulary changed: {actual}")


def _parse_list_row(
    row: BeautifulSoup,
    ledger: SeongjuLedger,
    page_url: str,
    page: int,
) -> _ListedCourse:
    cells = row.find_all("td", recursive=False)
    if len(cells) != 8:
        raise SeongjuContractError(
            f"{ledger.key} page {page}: list row cell count changed"
        )
    number = _clean(cells[0].get_text(" ", strip=True))
    title = _clean(cells[1].get_text(" ", strip=True))
    day = _clean(cells[2].get_text(" ", strip=True))
    time = _clean(cells[3].get_text(" ", strip=True))
    raw_period = _clean(cells[4].get_text(" ", strip=True))
    capacity = _clean(cells[5].get_text(" ", strip=True))
    source_status = _clean(cells[6].get_text(" ", strip=True))
    links = cells[7].select("a[href]")
    if (
        not number.isdigit()
        or not title
        or not day
        or not time
        or not capacity
        or source_status not in _STATUS_MAP
        or len(links) != 1
        or _clean(links[0].get_text(" ", strip=True)) != "수강신청"
    ):
        raise SeongjuContractError(f"{ledger.key} page {page}: malformed list row")
    detail_url, identity = _safe_detail_url(page_url, links[0].get("href"), ledger)
    period, event_start, event_end = _normalize_period(
        raw_period, f"{ledger.key}:{identity} period"
    )
    return _ListedCourse(
        ledger_key=ledger.key,
        identity=identity,
        title=title,
        detail_url=detail_url,
        day=day,
        time=time,
        period=period,
        event_start=event_start,
        event_end=event_end,
        capacity=capacity,
        source_status=source_status,
        page=page,
    )


def _counter_value(scope: BeautifulSoup, selector: str, label: str) -> int:
    nodes = scope.select(selector)
    if len(nodes) != 1:
        raise SeongjuContractError(f"{label} counter count changed")
    value = _clean(nodes[0].get_text(" ", strip=True))
    if not value.isdigit():
        raise SeongjuContractError(f"{label} counter is not numeric")
    return int(value)


def _parse_page(
    soup: BeautifulSoup,
    ledger: SeongjuLedger,
    page_url: str,
    requested_page: int,
) -> _Page:
    heading = soup.select_one("section.pagetitle h1")
    path = soup.select_one("section.pagetitle .path strong")
    if (
        _clean(heading.get_text(" ", strip=True) if heading else "") != ledger.label
        or _clean(path.get_text(" ", strip=True) if path else "") != ledger.label
    ):
        raise SeongjuContractError(
            f"{ledger.key} page {requested_page}: owner heading changed"
        )
    sections = soup.select("section.educationApplication")
    if len(sections) != 1:
        raise SeongjuContractError(
            f"{ledger.key} page {requested_page}: ledger section count changed"
        )
    section = sections[0]
    total = _counter_value(section, ".board_page .num1", f"{ledger.key}.total")
    active_page = _counter_value(section, ".board_page .num2", f"{ledger.key}.page")
    last_page = _counter_value(section, ".board_page .num3", f"{ledger.key}.last")
    if active_page != requested_page or last_page < 0:
        raise SeongjuContractError(
            f"{ledger.key} page {requested_page}: page counter binding changed"
        )
    tables = section.select(".board_body > table")
    if len(tables) != 1:
        raise SeongjuContractError(
            f"{ledger.key} page {requested_page}: list table count changed"
        )
    table = tables[0]
    headers = tuple(
        _clean(node.get_text(" ", strip=True)) for node in table.select("thead th")
    )
    if headers != _LIST_HEADERS[ledger.key]:
        raise SeongjuContractError(
            f"{ledger.key}: list header vocabulary changed: {headers}"
        )
    raw_rows = table.select("tbody > tr")
    exact_empty_notice = False
    if len(raw_rows) == 1:
        cells = raw_rows[0].find_all("td", recursive=False)
        exact_empty_notice = bool(
            len(cells) == 1
            and _clean(cells[0].get("colspan")) == "8"
            and _clean(cells[0].get_text(" ", strip=True))
            == "등록된 강좌가 없습니다."
        )
    rows = (
        ()
        if exact_empty_notice
        else tuple(
            _parse_list_row(row, ledger, page_url, requested_page)
            for row in raw_rows
        )
    )
    if total == 0 and raw_rows and not exact_empty_notice:
        raise SeongjuContractError(
            f"{ledger.key} page {requested_page}: zero-total empty marker changed"
        )
    if len(rows) > SEONGJU_PAGE_SIZE:
        raise SeongjuContractError(
            f"{ledger.key} page {requested_page}: page-size overflow"
        )
    identities = [row.identity for row in rows]
    if len(identities) != len(set(identities)):
        raise SeongjuContractError(
            f"{ledger.key} page {requested_page}: duplicate class_seq"
        )
    return _Page(
        requested_page=requested_page,
        total=total,
        advertised_last_page=last_page,
        active_page=active_page,
        rows=rows,
    )


def _course_signature(row: _ListedCourse) -> tuple[Any, ...]:
    return (
        row.identity,
        row.title,
        row.day,
        row.time,
        row.period,
        row.capacity,
        row.source_status,
    )


def _page_signature(page: _Page) -> tuple[Any, ...]:
    return (
        page.total,
        page.advertised_last_page,
        page.active_page,
        tuple(_course_signature(row) for row in page.rows),
    )


def _collect_ledger(
    session: Any,
    ledger: SeongjuLedger,
    timeout: int,
    max_pages: int,
    fetcher: Fetcher,
    meta: dict[str, Any],
) -> tuple[list[_ListedCourse], int]:
    first_url = _page_url(ledger, 1)
    first_soup = _fetch_soup(session, first_url, timeout, fetcher, meta)
    if ledger.key == "happiness":
        _validate_community_menu(first_soup)
    first = _parse_page(first_soup, ledger, first_url, 1)
    last_page = first.advertised_last_page
    if last_page > max_pages:
        meta["source_cap_reached"] = True
        raise SeongjuContractError(
            f"{ledger.key}: advertised last page {last_page} exceeds max_pages={max_pages}"
        )
    expected_last = max(1, (first.total + SEONGJU_PAGE_SIZE - 1) // SEONGJU_PAGE_SIZE)
    if first.total == 0:
        expected_last = 0
    if last_page != expected_last:
        raise SeongjuContractError(
            f"{ledger.key}: total/last-page counters disagree"
        )
    if first.total == 0:
        if first.rows:
            raise SeongjuContractError(f"{ledger.key}: zero-total first page is not empty")
        sentinel_number = 2
        sentinel_url = _page_url(ledger, sentinel_number)
        sentinel = _parse_page(
            _fetch_soup(session, sentinel_url, timeout, fetcher, meta),
            ledger,
            sentinel_url,
            sentinel_number,
        )
        if sentinel.rows or sentinel.total != 0 or sentinel.advertised_last_page != 0:
            raise SeongjuContractError(
                f"{ledger.key}: zero-total overflow page is not structurally empty"
            )
        for page_number, original in ((1, first), (sentinel_number, sentinel)):
            url = _page_url(ledger, page_number)
            checked = _parse_page(
                _fetch_soup(session, url, timeout, fetcher, meta),
                ledger,
                url,
                page_number,
            )
            if _page_signature(checked) != _page_signature(original):
                raise SeongjuContractError(
                    f"{ledger.key} page {page_number}: zero-total stability recheck changed"
                )
        meta.setdefault("boundary_modes", {})[
            ledger.key
        ] = "exact_zero_total_first_and_overflow"
        meta.setdefault("sentinel_pages", {})[ledger.key] = sentinel_number
        return [], 1
    pages: dict[int, _Page] = {1: first}
    for page_number in range(2, last_page + 1):
        url = _page_url(ledger, page_number)
        parsed = _parse_page(
            _fetch_soup(session, url, timeout, fetcher, meta),
            ledger,
            url,
            page_number,
        )
        if parsed.total != first.total or parsed.advertised_last_page != last_page:
            raise SeongjuContractError(f"{ledger.key}: pagination counters changed")
        pages[page_number] = parsed
    for page_number, parsed in pages.items():
        if page_number < last_page and len(parsed.rows) != SEONGJU_PAGE_SIZE:
            raise SeongjuContractError(
                f"{ledger.key} page {page_number}: premature short page"
            )
        if page_number == last_page and first.total and not parsed.rows:
            raise SeongjuContractError(f"{ledger.key}: advertised last page is empty")
    if sum(len(page.rows) for page in pages.values()) != first.total:
        raise SeongjuContractError(f"{ledger.key}: traversed rows do not equal total")

    sentinel_number = last_page + 1
    sentinel_url = _page_url(ledger, sentinel_number)
    sentinel = _parse_page(
        _fetch_soup(session, sentinel_url, timeout, fetcher, meta),
        ledger,
        sentinel_url,
        sentinel_number,
    )
    if (
        sentinel.rows
        or sentinel.total != first.total
        or sentinel.advertised_last_page != last_page
    ):
        raise SeongjuContractError(
            f"{ledger.key}: post-last page is not the exact structural empty sentinel"
        )

    for page_number in sorted({1, last_page}):
        url = _page_url(ledger, page_number)
        checked = _parse_page(
            _fetch_soup(session, url, timeout, fetcher, meta),
            ledger,
            url,
            page_number,
        )
        if _page_signature(checked) != _page_signature(pages[page_number]):
            raise SeongjuContractError(
                f"{ledger.key} page {page_number}: stability recheck changed"
            )
    sentinel_checked = _parse_page(
        _fetch_soup(session, sentinel_url, timeout, fetcher, meta),
        ledger,
        sentinel_url,
        sentinel_number,
    )
    if _page_signature(sentinel_checked) != _page_signature(sentinel):
        raise SeongjuContractError(f"{ledger.key}: sentinel stability recheck changed")

    meta.setdefault("boundary_modes", {})[ledger.key] = "exact_structural_empty"
    meta.setdefault("sentinel_pages", {})[ledger.key] = sentinel_number
    rows = [row for number in sorted(pages) for row in pages[number].rows]
    return rows, last_page


def _detail_pairs(table: BeautifulSoup, label: str) -> dict[str, str]:
    output: dict[str, str] = {}
    for row in table.select("tbody > tr"):
        header = row.find("th", recursive=False)
        value = row.find("td", recursive=False)
        if header is None or value is None:
            continue
        key = _clean(header.get_text(" ", strip=True))
        if not key or key in output:
            raise SeongjuContractError(f"{label}: duplicate detail field")
        output[key] = _clean(value.get_text(" ", strip=True))
    return output


def _detail_description(table: BeautifulSoup) -> str:
    for row in table.select("tbody > tr"):
        header = row.find("th", recursive=False)
        value = row.find("td", recursive=False)
        if (
            header is not None
            and value is not None
            and _clean(header.get_text(" ", strip=True)) == "교육내용"
        ):
            return "\n".join(
                _clean(part)
                for part in value.get_text("\n", strip=True).splitlines()
                if _clean(part)
            )
    return ""


def _target_from_description(description: str, ledger: SeongjuLedger) -> str:
    if ledger.key == "youth":
        return "성주군 아동·청소년"
    match = re.search(r"(?:^|\n)-?\s*모집대상\s*[:：]\s*([^\n]+)", description)
    if match is None:
        raise SeongjuContractError("happiness detail lacks audited 모집대상")
    target = _clean(match.group(1))
    if _PHONE_RE.search(target) or _EMAIL_RE.search(target):
        raise SeongjuContractError("target contains contact data")
    return target


def _venue_from_description(
    description: str,
    ledger: SeongjuLedger,
) -> tuple[str, str]:
    if ledger.key == "youth":
        return ledger.branch, ledger.branch_address
    match = re.search(r"(?:^|\n)-?\s*교육장소\s*[:：]\s*([^\n]+)", description)
    if match is None:
        raise SeongjuContractError("happiness detail lacks audited 교육장소")
    room = _clean(match.group(1))
    if "창의문화센터" in room:
        return room.replace("창의문화센터", "성주창의문화센터", 1), SEONGJU_VENUE_ADDRESSES[
            "성주창의문화센터"
        ]
    if "문화예술회관" in room:
        return room.replace("문화예술회관", "성주문화예술회관", 1), SEONGJU_VENUE_ADDRESSES[
            "성주문화예술회관"
        ]
    raise SeongjuContractError(f"unknown official happiness venue: {room}")


def _safe_application_control(
    table: BeautifulSoup,
    listed: _ListedCourse,
) -> tuple[bool, str, str]:
    ledger = SEONGJU_LEDGER_BY_KEY[listed.ledger_key]
    rows = [
        row
        for row in table.select("tbody > tr")
        if _clean(row.find("th").get_text(" ", strip=True) if row.find("th") else "")
        == "수강신청"
    ]
    if len(rows) != 1:
        raise SeongjuContractError(
            f"{ledger.key}:{listed.identity}: application row count changed"
        )
    links = rows[0].select("a")
    if len(links) != 1 or _clean(links[0].get_text(" ", strip=True)) != "수강신청":
        raise SeongjuContractError(
            f"{ledger.key}:{listed.identity}: application control count changed"
        )
    link = links[0]
    href = _clean(link.get("href"))
    onclick = _clean(link.get("onclick")).rstrip(";")
    if listed.source_status != "접수중":
        if href != "#;" or onclick not in _DISABLED_APPLICATION:
            raise SeongjuContractError(
                f"{ledger.key}:{listed.identity}: disabled application contract changed"
            )
        return False, "", onclick
    if onclick:
        raise SeongjuContractError(
            f"{ledger.key}:{listed.identity}: open control unexpectedly uses onclick"
        )
    application_url = urljoin(listed.detail_url, href)
    parsed = urlparse(application_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != SEONGJU_HOST
        or parsed.path != ledger.path
        or _clean((query.get("pg") or [""])[0]) != "sign"
        or _clean((query.get("class_seq") or [""])[0]) != listed.identity
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.fragment
        or set(query) - {"pg", "class_seq", "page"}
    ):
        raise SeongjuContractError(
            f"{ledger.key}:{listed.identity}: unsafe application form control"
        )
    return True, application_url, "direct_sign_control"


def _parse_detail(soup: BeautifulSoup, listed: _ListedCourse) -> dict[str, Any]:
    ledger = SEONGJU_LEDGER_BY_KEY[listed.ledger_key]
    heading = soup.select_one("section.pagetitle h1")
    if _clean(heading.get_text(" ", strip=True) if heading else "") != ledger.label:
        raise SeongjuContractError(
            f"{ledger.key}:{listed.identity}: detail owner heading changed"
        )
    tables = soup.select("section.educationApplication .board_view > table")
    if len(tables) != 1:
        raise SeongjuContractError(
            f"{ledger.key}:{listed.identity}: detail table count changed"
        )
    table = tables[0]
    title_nodes = table.select(".itemtd .subject")
    if len(title_nodes) != 1:
        raise SeongjuContractError(
            f"{ledger.key}:{listed.identity}: detail title count changed"
        )
    title = _clean(title_nodes[0].get_text(" ", strip=True))
    if title != listed.title:
        raise SeongjuContractError(
            f"{ledger.key}:{listed.identity}: list/detail title mismatch"
        )
    pairs = _detail_pairs(table, f"{ledger.key}:{listed.identity}")
    required = {
        "요일 및 교육시간",
        ledger.period_label,
        "모집인원",
        "교육내용",
        "수강료",
        "상태",
        "수강신청",
    }
    if not required.issubset(pairs) or set(pairs) - (required | {"재료비"}):
        raise SeongjuContractError(
            f"{ledger.key}:{listed.identity}: detail field vocabulary changed"
        )
    period, start, end = _normalize_period(
        pairs[ledger.period_label], f"{ledger.key}:{listed.identity} detail period"
    )
    if (
        period != listed.period
        or start != listed.event_start
        or end != listed.event_end
        or _clean(pairs["요일 및 교육시간"]) != _clean(f"{listed.day} {listed.time}")
        or re.sub(r"\s+", "", pairs["모집인원"])
        != re.sub(r"\s+", "", listed.capacity)
        or pairs["상태"] != listed.source_status
    ):
        raise SeongjuContractError(
            f"{ledger.key}:{listed.identity}: list/detail fields disagree"
        )
    description = _detail_description(table)
    target = _target_from_description(description, ledger)
    venue_name, venue_address = _venue_from_description(description, ledger)
    control, application_url, control_mode = _safe_application_control(table, listed)
    return {
        "title": title,
        "target": target,
        "venue_name": venue_name,
        "venue_address": venue_address,
        "fee": pairs["수강료"],
        "material_fee": pairs.get("재료비", ""),
        "application_control": control,
        "application_url": application_url,
        "application_control_mode": control_mode,
    }


def _target_extra(target: Any) -> Mapping[str, Any]:
    value = _target_value(target, "extra")
    return value if isinstance(value, Mapping) else {}


def _row(
    target: Any,
    listed: _ListedCourse,
    detail: Mapping[str, Any],
) -> dict[str, Any]:
    ledger = SEONGJU_LEDGER_BY_KEY[listed.ledger_key]
    status = _STATUS_MAP[listed.source_status]
    control = bool(detail.get("application_control"))
    application_url = _clean(detail.get("application_url"))
    if control != bool(application_url) or control != (status == "OPEN"):
        raise SeongjuContractError(
            f"{ledger.key}:{listed.identity}: status/application control disagree"
        )
    extra = _target_extra(target)
    venue_name = _clean(detail.get("venue_name"))
    venue_address = _clean(detail.get("venue_address"))
    output: dict[str, Any] = {
        "provider": SEONGJU_PROVIDER,
        "provider_course_id": (
            f"{SEONGJU_PROVIDER}:education:{listed.ledger_key}:{listed.identity}"
        ),
        "title": listed.title,
        "branch": ledger.branch,
        "branch_code": f"{SEONGJU_PROVIDER}:{listed.ledger_key}",
        "preserve_branch": True,
        "branch_url": ledger.url,
        "raw_url": listed.detail_url,
        "application_url": application_url,
        "application_type": "ONLINE_FORM" if control else "INFO_ONLY_DISABLED_CONTROL",
        "application_method_raw": "온라인 수강신청" if control else "접수 비활성",
        "reservation_available": control,
        "status": status,
        "period": listed.period,
        "apply_period": "",
        "schedule_raw": _clean(f"{listed.day} · {listed.time}"),
        "start_date": listed.event_start.isoformat(),
        "end_date": listed.event_end.isoformat(),
        "apply_start_date": "",
        "apply_end_date": "",
        "target": _clean(detail.get("target")),
        "capacity": listed.capacity,
        "fee": _clean(detail.get("fee")),
        "material_fee": _clean(detail.get("material_fee")),
        "venue_name": venue_name,
        "room": venue_name,
        "address": venue_address,
        "venue_address": venue_address,
        "category": ledger.label,
        "collection_category": _clean(extra.get("collection_category") or "공공예약"),
        "domain_category": _clean(extra.get("domain_category") or "교육·강좌"),
        "operator_type": _clean(extra.get("operator_type") or "지자체/공공기관"),
        "source_group": _clean(extra.get("source_group") or "municipal_reservation"),
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": "static_html+detail_html",
        "program_type": "교육",
        "municipality_code": SEONGJU_MUNICIPALITY_CODE,
        "municipality_name": SEONGJU_MUNICIPALITY_NAME,
        "municipality_full_name": SEONGJU_MUNICIPALITY_NAME,
        "raw_fields": {
            "parser": SEONGJU_PARSER,
            "identity": listed.identity,
            "ledger_key": listed.ledger_key,
            "source_page": listed.page,
            "source_status": listed.source_status,
            "detail_verified": True,
            "list_detail_binding": "class_seq+title+schedule+period+capacity+status",
            "application_control_verified": True,
            "application_control_mode": _clean(detail.get("application_control_mode")),
            "application_endpoint_requested": False,
            "identity_check_requested": False,
            "applicant_list_requested": False,
            "description_contact_attachment_image_applicant_data_excluded": True,
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
        raise SeongjuContractError(
            f"{ledger.key}:{listed.identity}: public row leaked contact data"
        )
    return output


def collect_seongju_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = SEONGJU_MAX_PAGES,
    detail_limit: int = SEONGJU_DETAIL_LIMIT,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete current/future Seongju education snapshot."""

    audit_date = _today(today)
    factory = session_factory or _default_session_factory
    html_fetcher = fetcher or _default_fetcher
    meta: dict[str, Any] = {
        "municipality_code": SEONGJU_MUNICIPALITY_CODE,
        "municipality_name": SEONGJU_MUNICIPALITY_NAME,
        "owner_provider": SEONGJU_PROVIDER,
        "canonical_url": SEONGJU_CANONICAL_URL,
        "candidate_id": SEONGJU_CANONICAL_CANDIDATE_ID,
        "rejected_review_candidate_id": SEONGJU_REVIEW_CANDIDATE_ID,
        "parser": SEONGJU_PARSER,
        "ownership_scope": SEONGJU_OWNERSHIP_SCOPE,
        "cutoff": audit_date.isoformat(),
        "pages": 0,
        "data_pages": 0,
        "list_requests": 0,
        "detail_pages": 0,
        "logical_requests": 0,
        "physical_requests": 0,
        "request_retry_count": 0,
        "application_endpoint_requests": 0,
        "identity_check_requests": 0,
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
        if not is_seongju_education_target(target):
            raise SeongjuContractError(
                "target is not the canonical Seongju education owner"
            )
        if timeout < 1 or max_pages < 1 or detail_limit < 0:
            raise SeongjuContractError("invalid collector limits")
        session = factory()

        ledger_rows: dict[str, list[_ListedCourse]] = {}
        ledger_pages: dict[str, int] = {}
        for ledger in SEONGJU_LEDGERS:
            rows, last_page = _collect_ledger(
                session,
                ledger,
                timeout,
                max_pages,
                html_fetcher,
                meta,
            )
            ledger_rows[ledger.key] = rows
            ledger_pages[ledger.key] = last_page

        listed = [
            row for ledger in SEONGJU_LEDGERS for row in ledger_rows[ledger.key]
        ]
        source_keys = [(row.ledger_key, row.identity) for row in listed]
        if len(source_keys) != len(set(source_keys)):
            raise SeongjuContractError("duplicate identities across education ledgers")
        current = [row for row in listed if row.event_end >= audit_date]
        if len(current) > detail_limit:
            meta["source_cap_reached"] = True
            raise SeongjuContractError(
                "detail_limit would create a partial current/future snapshot"
            )

        details: dict[tuple[str, str], dict[str, Any]] = {}
        for row in current:
            soup = _fetch_soup(session, row.detail_url, timeout, html_fetcher, meta)
            details[(row.ledger_key, row.identity)] = _parse_detail(soup, row)
            meta["detail_pages"] = int(meta["detail_pages"]) + 1

        output = [
            _row(target, row, details[(row.ledger_key, row.identity)])
            for row in current
        ]
        if dedupe_rows is not None:
            output = list(dedupe_rows(output))
        if len(output) != len(current):
            raise SeongjuContractError(
                "dedupe changed an already-unique current snapshot"
            )

        ledger_counts = {
            SEONGJU_LEDGER_BY_KEY[key].label: len(rows)
            for key, rows in ledger_rows.items()
        }
        ledger_current_counts = {
            ledger.label: sum(row.ledger_key == ledger.key for row in current)
            for ledger in SEONGJU_LEDGERS
        }
        source_statuses = Counter(row.source_status for row in listed)
        output_statuses = Counter(_clean(row.get("status")) for row in output)
        controls = sum(bool(row.get("reservation_available")) for row in output)
        data_pages = sum(ledger_pages.values())
        meta.update(
            {
                "pages": data_pages,
                "data_pages": data_pages,
                "list_requests": int(meta["logical_requests"])
                - int(meta["detail_pages"]),
                "ledger_pages": {
                    SEONGJU_LEDGER_BY_KEY[key].label: value
                    for key, value in ledger_pages.items()
                },
                "ledger_counts": ledger_counts,
                "ledger_current_counts": ledger_current_counts,
                "active_ledger_labels": list(SEONGJU_ACTIVE_LEDGER_LABELS),
                "source_rows": len(listed),
                "source_total": len(listed),
                "current_source_count": len(current),
                "expired_source_count": len(listed) - len(current),
                "source_status_counts": dict(source_statuses),
                "current_status_counts": dict(output_statuses),
                "source_branch_counts": {
                    SEONGJU_LEDGER_BY_KEY[key].branch: len(rows)
                    for key, rows in ledger_rows.items()
                },
                "branch_counts": dict(
                    Counter(_clean(row.get("branch")) for row in output)
                ),
                "official_branch_addresses": dict(SEONGJU_BRANCH_ADDRESSES),
                "official_venue_addresses": dict(SEONGJU_VENUE_ADDRESSES),
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
                    "공식 두 교육 원장의 공개 과정이 모두 기준일 이전 종료"
                    if not output and listed
                    else "공식 두 교육 원장에 공개 과정이 없음"
                    if not output
                    else ""
                ),
                "candidate_decisions": dict(SEONGJU_CANDIDATE_DECISIONS),
                "excluded_owner_boundaries": dict(
                    SEONGJU_EXCLUDED_OWNER_BOUNDARIES
                ),
                "separate_owners": dict(SEONGJU_SEPARATE_OWNERS),
                "separate_social_welfare_provider": (
                    SEONGJU_SOCIAL_WELFARE_PROVIDER
                ),
                "separate_social_welfare_candidate_id": (
                    SEONGJU_SOCIAL_WELFARE_CANDIDATE_ID
                ),
                "existing_generic_collector_relationship": (
                    "generic_parser_code_exists_but_no_active_crawl_target_found"
                ),
            }
        )
        return output, SEONGJU_PARSER, meta
    except Exception as exc:
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
        return [], SEONGJU_PARSER, meta
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


collect = collect_seongju_education


__all__ = [
    "SEONGJU_ACTIVE_LEDGER_LABELS",
    "SEONGJU_BRANCH_ADDRESSES",
    "SEONGJU_CANONICAL_CANDIDATE_ID",
    "SEONGJU_CANONICAL_URL",
    "SEONGJU_CANDIDATE_DECISIONS",
    "SEONGJU_DEPRECATED_PROVIDER",
    "SEONGJU_DETAIL_LIMIT",
    "SEONGJU_EXCLUDED_OWNER_BOUNDARIES",
    "SEONGJU_FETCH_ATTEMPTS",
    "SEONGJU_HOST",
    "SEONGJU_LEDGERS",
    "SEONGJU_MAX_PAGES",
    "SEONGJU_MUNICIPALITY_CODE",
    "SEONGJU_MUNICIPALITY_NAME",
    "SEONGJU_OWNERSHIP_SCOPE",
    "SEONGJU_PAGE_SIZE",
    "SEONGJU_PARSER",
    "SEONGJU_PROVIDER",
    "SEONGJU_REVIEW_CANDIDATE_ID",
    "SEONGJU_REVIEW_PROVIDER",
    "SEONGJU_REVIEW_URL",
    "SEONGJU_SEPARATE_OWNERS",
    "SEONGJU_SOCIAL_WELFARE_CANDIDATE_ID",
    "SEONGJU_SOCIAL_WELFARE_PROVIDER",
    "SEONGJU_VENUE_ADDRESSES",
    "SeongjuContractError",
    "SeongjuLedger",
    "collect",
    "collect_seongju_education",
    "is_seongju_education_target",
    "is_target",
]
